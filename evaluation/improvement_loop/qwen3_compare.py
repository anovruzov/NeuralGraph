"""Unpaid deterministic answerer comparison: qwen2.5:7b-instruct vs qwen3:8b.

Zero paid calls, no judge. Every metric here is computed against the frozen gold
answers already in the corpus, so this measures *answerer capability* alone and
is explicitly not a judged accuracy score.

Evidence, ordering, question membership and answer schema are held identical to
the frozen v1 configuration; only the answerer changes. That is the point: any
difference is attributable to the model, not to a new retrieval mechanism.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evaluation.improvement_loop.corpus import load_corpus, load_splits
from evaluation.improvement_loop.rounds import load_eval_sets
from evaluation.replay_generation import (JSON_INSTRUCTION, SYSTEM_PROMPT,
                                          build_prompt, grade, parse_items)
from evaluation.diagnose_accuracy import retrieved_text

OUT = Path("evaluation/artifacts/closeout_20260825")
LOOP = Path("evaluation/artifacts/locomo_loop")
URL = "http://localhost:11434/v1/chat/completions"


def prompt_hash() -> str:
    return hashlib.sha256((SYSTEM_PROMPT + "\x00" + JSON_INSTRUCTION).encode()).hexdigest()[:16]


def ask(model: str, record: dict, mode: str, timeout: int = 900) -> dict:
    system = SYSTEM_PROMPT + "\n" + JSON_INSTRUCTION
    user = build_prompt(record)
    if mode == "no_think":
        user += "\n/no_think"
    body = {"model": model, "stream": False, "temperature": 0,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    usage = payload.get("usage") or {}
    return {"text": payload["choices"][0]["message"]["content"],
            "ms": round((time.time() - start) * 1000),
            "in_tokens": usage.get("prompt_tokens", 0),
            "out_tokens": usage.get("completion_tokens", 0)}


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()


def score(record: dict, items: list[str]) -> dict:
    g = grade(str(record.get("gold_answer", "")), items).__dict__
    hay = retrieved_text(record).lower()
    return {"item_f1": g["f1"], "item_precision": g["precision"],
            "item_recall": g["recall"], "exact_set_match": g["exact_set_match"],
            "unsupported": len([i for i in items if i and i.lower() not in hay]),
            "n_items": len(items)}


def run(model: str, mode: str, ids: list[int], cache_path: Path, by_id: dict) -> dict:
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [q for q in ids if str(q) not in cache]
    print(f"{model}/{mode}: {len(ids)-len(todo)} cached, {len(todo)} to run",
          file=sys.stderr, flush=True)
    for n, qid in enumerate(todo, 1):
        try:
            out = ask(model, by_id[qid], mode)
            items, nie = parse_items(strip_think(out["text"]))
            cache[str(qid)] = {**out, "items": items, "not_in_evidence": nie,
                               "parsed": bool(items) or nie}
        except Exception as exc:                       # recorded, never dropped
            cache[str(qid)] = {"error": f"{type(exc).__name__}: {exc}"}
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, sort_keys=True))
        tmp.replace(cache_path)                        # atomic checkpoint
        if n % 5 == 0:
            print(f"  {n}/{len(todo)}", file=sys.stderr, flush=True)
    return cache


def summarise(cache: dict, ids: list[int], by_id: dict) -> dict:
    rows, errors = [], []
    for qid in ids:
        e = cache.get(str(qid))
        if e is None:
            continue
        if "error" in e:
            errors.append(qid); continue
        rows.append({"id": qid, "category": str(by_id[qid]["category"]),
                     "items": e["items"], "ms": e["ms"], "parsed": e["parsed"],
                     "out_tokens": e.get("out_tokens", 0), **score(by_id[qid], e["items"])})
    n = len(rows) or 1
    lat = sorted(r["ms"] for r in rows) or [0]
    return {"n_scored": len(rows), "n_errors": len(errors), "error_ids": errors,
            "operational_error_rate": round(len(errors) / max(len(ids), 1), 4),
            **{k: round(sum(r[k] for r in rows) / n, 4)
               for k in ("item_f1", "item_precision", "item_recall")},
            "exact_set_match": round(sum(1 for r in rows if r["exact_set_match"]) / n, 4),
            "unsupported_items": sum(r["unsupported"] for r in rows),
            "unsupported_item_rate": round(sum(r["unsupported"] for r in rows)
                                           / max(sum(r["n_items"] for r in rows), 1), 4),
            "parse_success": round(sum(1 for r in rows if r["parsed"]) / n, 4),
            "latency_p50_ms": lat[len(lat)//2],
            "latency_p95_ms": lat[max(0, int(0.95*len(lat))-1)],
            "mean_out_tokens": round(sum(r["out_tokens"] for r in rows) / n, 1),
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--mode", choices=("think", "no_think"), default="no_think")
    ap.add_argument("--set", choices=("smoke", "diagnostic60"), default="smoke")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    by_id = {int(r["id"]): r for r in load_corpus()}
    if args.set == "smoke":
        ids = list(load_splits(LOOP / "splits.json").development)[:5]   # dev only
    else:
        ids = list(load_eval_sets(LOOP / "eval_sets.json")["validation"].question_ids)
    if args.limit:
        ids = ids[: args.limit]

    tag = f"{args.model.replace(':', '_')}_{args.mode}_{args.set}"
    cache_path = OUT / "cache" / f"{tag}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarise(run(args.model, args.mode, ids, cache_path, by_id), ids, by_id)
    summary.update({"model": args.model, "mode": args.mode, "set": args.set,
                    "n_requested": len(ids), "prompt_hash": prompt_hash(),
                    "temperature": 0, "paid_calls": 0})
    (OUT / f"{tag}_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
