"""One batch per invocation. Deterministic, resumable, sequential.

Design constraints this enforces, each because a looser version has already
cost time on this machine:

* **One batch per process.** A long-lived loop holding the Ollama slot competes
  with other work and cannot be interrupted without losing the batch.
* **Skip-by-cache, not by index.** The next batch is the next *unfinished* ids,
  recomputed from the cache each invocation. Re-running a failed batch
  therefore calls only the incomplete ids and costs nothing for the rest.
* **Per-question checkpoint.** `run_eval` flushes after every question via
  temp-file-and-rename, so a kill leaves the previous cache intact.
* **Errors are counted, never excluded.** API, parse, timeout and ingestion
  failures all land in the denominator. Cumulative error rate above 1% aborts
  the run rather than quietly shrinking the scored set.
* **Frozen config.** Model, prompt, top-k, judge and split ids come from the
  saved artifacts; this module never chooses them.

Usage (one batch):

    python3.11 -m evaluation.improvement_loop.batch validation --size 5 --concurrency 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from evaluation.improvement_loop.corpus import load_corpus
from evaluation.improvement_loop.rounds import corpus_weights, load_eval_sets
from evaluation.improvement_loop.runner import (
    ResponseCache,
    RunConfig,
    evidence_hash,
    run_eval,
    score_row,
    summarise,
)

OUT = Path("evaluation/artifacts/locomo_loop")
MAX_ERROR_RATE = 0.01


def _answer_key(qid: int, cfg: RunConfig, record: dict[str, Any]) -> str:
    return ResponseCache.key("answer", qid, cfg, evidence_hash(record))


def completed_ids(cache: ResponseCache, question_ids: tuple[int, ...],
                  by_id: dict[int, dict[str, Any]], cfg: RunConfig) -> tuple[list[int], list[int]]:
    """Split the eval set into (completed successfully, still to do).

    An errored cache entry counts as *not* completed, so re-running a failed
    batch retries exactly those ids and reuses everything else.
    """
    done, todo = [], []
    for qid in question_ids:
        entry = cache._data.get(_answer_key(qid, cfg, by_id[qid]))
        if entry is not None and "error" not in entry:
            done.append(qid)
        else:
            todo.append(qid)
    return done, todo


def rebuild_rows(cache: ResponseCache, ids: list[int], by_id: dict[int, dict[str, Any]],
                 cfg: RunConfig) -> list[dict[str, Any]]:
    """Reconstruct scored rows for already-cached questions, no API calls."""
    import hashlib

    from evaluation.replay_generation import parse_items

    rows = []
    for qid in ids:
        record = by_id[qid]
        answer = cache._data.get(_answer_key(qid, cfg, record))
        if answer is None:
            continue
        if "error" in answer:
            rows.append(score_row(record, answer, {}))
            continue
        items, _ = parse_items(answer["text"])
        candidate = ", ".join(items) if items else answer["text"][:300]
        jkey = ResponseCache.key(
            "judge", qid, cfg,
            hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16],
        )
        verdict = cache._data.get(jkey)
        if verdict is None:
            continue
        rows.append(score_row(record, answer, verdict))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("split", choices=("validation", "locked_test"))
    ap.add_argument("--size", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--log-dir", type=Path, default=OUT / "logs")
    args = ap.parse_args(argv)

    records = load_corpus()
    by_id = {int(r["id"]): r for r in records}
    sets = load_eval_sets(OUT / "eval_sets.json")
    es = sets[args.split]
    cfg = RunConfig(concurrency=args.concurrency)
    cache = ResponseCache(OUT / "cache" / f"{args.split}.json")

    done, todo = completed_ids(cache, es.question_ids, by_id, cfg)
    total = len(es.question_ids)
    batch_index = len(done) // args.size

    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / f"{args.split}_batch{batch_index:03d}.log"

    if not todo:
        print(f"{args.split}: ALL {total} COMPLETE", file=sys.stderr)
        _write_summary(args.split, cache, es, by_id, cfg, records)
        return 0

    ids = tuple(todo[: args.size])
    started = time.time()

    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        print(msg, file=sys.stderr, flush=True)

    log(f"split={args.split} batch={batch_index} attempting={list(ids)} "
        f"cached={len(done)}/{total} config={cfg.config_hash}")

    rows = asyncio.run(run_eval(by_id, ids, cfg, cache, label=f"{args.split} b{batch_index}"))
    elapsed = time.time() - started

    batch_errors = sum(1 for r in rows if "error" in r)
    batch_completed = len(rows) - batch_errors

    # Cumulative state, rebuilt from the cache so it is authoritative.
    done_after, todo_after = completed_ids(cache, es.question_ids, by_id, cfg)
    all_rows = rebuild_rows(cache, done_after, by_id, cfg)
    cum_errors = sum(1 for r in all_rows if "error" in r)
    scored = [r for r in all_rows if "error" not in r]
    cum_acc = (sum(1 for r in scored if r["judged_correct"]) / len(scored)) if scored else 0.0
    attempted_total = len(done_after) + cum_errors
    cum_error_rate = cum_errors / attempted_total if attempted_total else 0.0

    per_q = elapsed / len(ids)
    remaining = len(todo_after)
    eta_min = remaining * per_q / 60

    log(f"  ids attempted : {list(ids)}")
    log(f"  completed     : {batch_completed}")
    log(f"  cached (skip) : {len(done)}")
    log(f"  errors        : {batch_errors}")
    log(f"  cumulative    : {len(done_after)}/{total}")
    log(f"  accuracy      : {cum_acc:.3f} ({sum(1 for r in scored if r['judged_correct'])}"
        f"/{len(scored)} scored)")
    log(f"  elapsed       : {elapsed:.0f}s ({per_q:.1f}s/q)")
    log(f"  est remaining : {remaining} q, ~{eta_min:.0f} min")
    log(f"  error rate    : {cum_error_rate:.2%} (limit {MAX_ERROR_RATE:.0%})")

    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    if cum_error_rate > MAX_ERROR_RATE:
        print(
            f"\nABORT: cumulative error rate {cum_error_rate:.2%} exceeds "
            f"{MAX_ERROR_RATE:.0%}. Errors were NOT dropped from the denominator. "
            "Investigate before continuing.",
            file=sys.stderr,
        )
        return 2

    if not todo_after:
        _write_summary(args.split, cache, es, by_id, cfg, records)
        print(f"\n{args.split}: COMPLETE — summary written", file=sys.stderr)
    return 0


def _write_summary(split: str, cache: ResponseCache, es, by_id, cfg, records) -> None:
    """Write the split's baseline artifact once every question is done."""
    done, _ = completed_ids(cache, es.question_ids, by_id, cfg)
    rows = rebuild_rows(cache, list(es.question_ids), by_id, cfg)
    s = summarise(rows, cfg, corpus_weights(records))
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    s.update({
        "round": 0, "label": f"baseline_{split}", "eval_set": split,
        "eval_set_digest": es.digest, "corpus_digest": es.corpus_digest,
        "commit": sha,
    })
    path = OUT / f"baseline_{split}.json"
    path.write_text(json.dumps({"summary": s, "rows": rows}, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
