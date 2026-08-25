"""Provider-neutral evaluation runner: local answerer, independent judge.

Two models, deliberately from different vendors and families. The answerer runs
on Ollama; the judge is gpt-4o, which is also the judge that produced the
recorded baseline in ``demo/maximal.json``. Keeping the judge fixed means a
change in score is a change in answers, not a change in who is grading.

Everything that could silently move a score is hashed into a config digest and
recorded on every result: both model ids, temperature, the prompt text itself,
the evidence, and the top-k. A cached response is only reused when that digest
matches, so a prompt edit invalidates its own cache rather than quietly serving
yesterday's answers.

The error gate is not advisory. If more than 1% of questions fail to produce a
scored result, the run is marked invalid and its accuracy is not reported --
dropping errors from the denominator is exactly how a broken run comes to look
like an improvement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from evaluation.diagnose_accuracy import answer_items, evidence_present, retrieved_text
from evaluation.replay_generation import (
    JSON_INSTRUCTION,
    SYSTEM_PROMPT,
    build_prompt,
    grade,
    parse_items,
)

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Judge pricing, USD per 1M tokens. Only the judge bills; the answerer is local.
JUDGE_RATES = {"gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}

MAX_ERROR_RATE = 0.01

JUDGE_SYSTEM = (
    "You grade whether a candidate answer to a question about a conversation "
    "matches the reference answer.\n"
    "Reply with exactly one word: CORRECT or INCORRECT.\n"
    "Rules:\n"
    "1. Judge meaning, not wording. Different phrasing of the same fact is CORRECT.\n"
    "2. For a question admitting several items, the candidate must cover every "
    "item in the reference. Missing an item is INCORRECT.\n"
    "3. Extra items the reference does not contain make it INCORRECT.\n"
    "4. A date or time must denote the same point in time to be CORRECT.\n"
    "5. An abstention ('not in evidence') is INCORRECT unless the reference is "
    "itself an abstention."
)


@dataclass(frozen=True)
class RunConfig:
    """Frozen configuration. Anything that can move a score lives here."""
    answerer_model: str = "qwen2.5:7b-instruct"
    judge_model: str = "gpt-4o"
    temperature: float = 0.0
    top_k: str = "recorded"          # replay holds retrieval fixed
    concurrency: int = 4
    prompt_version: str = "v1-baseline"
    system_prompt: str = SYSTEM_PROMPT
    json_instruction: str = JSON_INSTRUCTION
    max_usd: float = 5.0

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(
            (self.system_prompt + "\x00" + self.json_instruction).encode("utf-8")
        ).hexdigest()[:16]

    @property
    def config_hash(self) -> str:
        payload = {
            "answerer_model": self.answerer_model,
            "judge_model": self.judge_model,
            "temperature": self.temperature,
            "top_k": self.top_k,
            "prompt_hash": self.prompt_hash,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # The prompt bodies are large; record their hash, not the text.
        d.pop("system_prompt")
        d.pop("json_instruction")
        d["prompt_hash"] = self.prompt_hash
        d["config_hash"] = self.config_hash
        return d


def evidence_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(retrieved_text(record).encode("utf-8")).hexdigest()[:16]


class ResponseCache:
    """Disk cache keyed by question, models, prompt, evidence and config.

    Makes the loop resumable without re-spending accepted API calls, and makes
    a prompt change invalidate its own entries automatically.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"CORRUPTED CACHE at {self.path}: {exc}. Stop and inspect; "
                    "do not delete it blindly, it may hold paid results."
                ) from exc
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(kind: str, qid: int, cfg: RunConfig, ev_hash: str) -> str:
        return f"{kind}:{qid}:{cfg.config_hash}:{ev_hash}"

    def get(self, key: str) -> Any | None:
        value = self._data.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class ProgressReporter:
    """Per-category running accuracy on stderr. Never touches stdout."""

    def __init__(self, total: int, every: int = 5) -> None:
        self.total = total
        self.every = every
        self.done = 0
        self.errors = 0
        self.n: dict[str, int] = {}
        self.correct: dict[str, int] = {}
        self.started = time.time()

    def tick(self, category: str | None = None, correct: bool | None = None) -> None:
        self.done += 1
        if category is None or correct is None:
            self.errors += 1
        else:
            self.n[category] = self.n.get(category, 0) + 1
            self.correct[category] = self.correct.get(category, 0) + (1 if correct else 0)
        if self.done % self.every == 0 or self.done == self.total:
            parts = [
                f"{c}={self.correct[c]}/{k} ({self.correct[c] / k:.0%})"
                for c, k in sorted(self.n.items())
            ]
            rate = (time.time() - self.started) / max(self.done, 1)
            eta = rate * (self.total - self.done)
            errs = f", errors={self.errors}" if self.errors else ""
            print(
                f"  [{self.done}/{self.total}] " + ", ".join(parts) + errs
                + f"  ~{eta/60:.0f}m left",
                file=sys.stderr, flush=True,
            )


def _post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


async def answer_one(record: dict[str, Any], cfg: RunConfig, cache: ResponseCache,
                     sem: asyncio.Semaphore) -> dict[str, Any]:
    qid = int(record["id"])
    ev = evidence_hash(record)
    key = ResponseCache.key("answer", qid, cfg, ev)
    cached = cache.get(key)
    if cached is not None:
        return cached

    body = {
        "model": cfg.answerer_model,
        "stream": False,
        "temperature": cfg.temperature,
        "messages": [
            {"role": "system", "content": cfg.system_prompt + "\n" + cfg.json_instruction},
            {"role": "user", "content": build_prompt(record)},
        ],
    }
    async with sem:
        start = time.time()
        try:
            payload = await asyncio.to_thread(_post, OLLAMA_URL, body, {}, 600)
            text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            out = {
                "id": qid, "text": text, "latency_ms": round((time.time() - start) * 1000, 1),
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            }
        except Exception as exc:  # noqa: BLE001 - recorded, never silently dropped
            out = {"id": qid, "error": f"{type(exc).__name__}: {exc}",
                   "latency_ms": round((time.time() - start) * 1000, 1)}
    cache.put(key, out)
    return out


async def judge_one(record: dict[str, Any], candidate: str, cfg: RunConfig,
                    cache: ResponseCache, sem: asyncio.Semaphore) -> dict[str, Any]:
    qid = int(record["id"])
    # The candidate is part of the key: a different answer must be re-judged.
    ev = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
    key = ResponseCache.key("judge", qid, cfg, ev)
    cached = cache.get(key)
    if cached is not None:
        return cached

    prompt = (
        f"Question: {record.get('question')}\n"
        f"Reference answer: {record.get('gold_answer')}\n"
        f"Candidate answer: {candidate or '(no answer)'}\n\n"
        "CORRECT or INCORRECT?"
    )
    body = {
        "model": cfg.judge_model, "temperature": 0, "max_tokens": 4,
        "messages": [{"role": "system", "content": JUDGE_SYSTEM},
                     {"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}
    async with sem:
        try:
            payload = await asyncio.to_thread(_post, OPENAI_URL, body, headers, 120)
            verdict = payload["choices"][0]["message"]["content"].strip().upper()
            usage = payload.get("usage") or {}
            out = {
                "id": qid,
                "correct": verdict.startswith("CORRECT"),
                "verdict": verdict,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            }
        except Exception as exc:  # noqa: BLE001
            out = {"id": qid, "error": f"{type(exc).__name__}: {exc}"}
    cache.put(key, out)
    return out


ANSWER_TYPES = ("date", "person", "number", "other")


def _answer_type(text: str) -> str:
    import re
    t = (text or "").lower()
    if re.search(r"\b(19|20)\d{2}\b|\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t):
        return "date"
    if re.search(r"^\d+(\.\d+)?$", t.strip()):
        return "number"
    return "other"


def score_row(record: dict[str, Any], answer: dict[str, Any],
              verdict: dict[str, Any]) -> dict[str, Any]:
    """Score one question. Errors are marked, never dropped."""
    qid = int(record["id"])
    base = {"id": qid, "category": str(record.get("category")),
            "conversation": str(record.get("conversation"))}

    if "error" in answer:
        return {**base, "error": answer["error"], "stage": "answer"}
    if "error" in verdict:
        return {**base, "error": verdict["error"], "stage": "judge"}

    items, not_in_evidence = parse_items(answer["text"])
    gold = str(record.get("gold_answer", ""))
    g = grade(gold, items).__dict__
    strict, lenient = evidence_present(record)
    gold_items = answer_items(gold)
    haystack = retrieved_text(record).lower()

    unsupported = [i for i in items if i and i.lower() not in haystack]

    return {
        **base,
        "judged_correct": bool(verdict["correct"]),
        "items": items,
        "not_in_evidence": not_in_evidence,
        "item_f1": g["f1"],
        "item_precision": g["precision"],
        "item_recall": g["recall"],
        "exact_set_match": g["exact_set_match"],
        "evidence_strict": strict,
        "evidence_lenient": lenient,
        "false_abstention": bool(not_in_evidence and lenient),
        "unsupported_items": len(unsupported),
        "unsupported_rate": round(len(unsupported) / len(items), 4) if items else 0.0,
        "answer_type_mismatch": (
            _answer_type(" ".join(items)) != _answer_type(gold)
            if items and gold else False
        ),
        "gold_item_count": len(gold_items),
        "latency_ms": answer["latency_ms"],
        "answer_tokens": answer.get("output_tokens", 0),
        "judge_tokens": verdict.get("input_tokens", 0) + verdict.get("output_tokens", 0),
        "judge_input_tokens": verdict.get("input_tokens", 0),
        "judge_output_tokens": verdict.get("output_tokens", 0),
    }


def summarise(rows: list[dict[str, Any]], cfg: RunConfig,
              weights: dict[str, float] | None = None) -> dict[str, Any]:
    """All metrics, overall and per category, with the error-rate validity gate."""
    scored = [r for r in rows if "error" not in r]
    errors = [r for r in rows if "error" in r]
    total = len(rows)
    error_rate = len(errors) / total if total else 1.0

    def block(subset: list[dict[str, Any]]) -> dict[str, Any]:
        if not subset:
            return {"n": 0}
        n = len(subset)
        mean = lambda k: round(sum(r[k] for r in subset) / n, 4)  # noqa: E731
        lat = sorted(r["latency_ms"] for r in subset)
        return {
            "n": n,
            "judged_accuracy": round(sum(1 for r in subset if r["judged_correct"]) / n, 4),
            "item_f1": mean("item_f1"),
            "item_precision": mean("item_precision"),
            "item_recall": mean("item_recall"),
            "exact_set_match": round(
                sum(1 for r in subset if r["exact_set_match"]) / n, 4),
            "evidence_recall": round(
                sum(1 for r in subset if r["evidence_lenient"]) / n, 4),
            "false_abstention_rate": round(
                sum(1 for r in subset if r["false_abstention"]) / n, 4),
            "unsupported_item_rate": mean("unsupported_rate"),
            "answer_type_mismatch_rate": round(
                sum(1 for r in subset if r["answer_type_mismatch"]) / n, 4),
            "latency_p50_ms": round(statistics.median(lat), 1),
            "latency_p95_ms": round(lat[max(0, int(0.95 * len(lat)) - 1)], 1),
        }

    by_category = {
        c: block([r for r in scored if r["category"] == c])
        for c in sorted({r["category"] for r in scored})
    }

    overall = block(scored)

    # Reweight the balanced score back to corpus proportions. Reported beside
    # the balanced number so the two can never be confused.
    corpus_weighted = None
    if weights and by_category:
        acc = sum(
            by_category[c]["judged_accuracy"] * w
            for c, w in weights.items() if c in by_category
        )
        covered = sum(w for c, w in weights.items() if c in by_category)
        corpus_weighted = round(acc / covered, 4) if covered else None

    judge_in = sum(r.get("judge_input_tokens", 0) for r in scored)
    judge_out = sum(r.get("judge_output_tokens", 0) for r in scored)
    in_rate, out_rate = JUDGE_RATES.get(cfg.judge_model, (0.0, 0.0))
    usd = judge_in / 1e6 * in_rate + judge_out / 1e6 * out_rate

    return {
        "valid": error_rate <= MAX_ERROR_RATE,
        "validity_note": (
            "OK" if error_rate <= MAX_ERROR_RATE else
            f"INVALID: error rate {error_rate:.2%} exceeds {MAX_ERROR_RATE:.0%}. "
            "Accuracy is not reported; errors were not dropped from the denominator."
        ),
        "n_total": total,
        "n_scored": len(scored),
        "n_errors": len(errors),
        "error_rate": round(error_rate, 4),
        "error_sample": [r.get("error") for r in errors[:3]],
        "overall": overall,
        "corpus_weighted_accuracy": corpus_weighted,
        "by_category": by_category,
        "cost": {
            "judge_input_tokens": judge_in,
            "judge_output_tokens": judge_out,
            "judge_usd": round(usd, 4),
            "usd_per_question": round(usd / len(scored), 6) if scored else 0.0,
            "answerer_usd": 0.0,
            "answerer_note": "local Ollama, no billing",
        },
        "config": cfg.as_dict(),
    }


async def run_eval(records_by_id: dict[int, dict[str, Any]], question_ids: tuple[int, ...],
                   cfg: RunConfig, cache: ResponseCache,
                   label: str = "eval") -> list[dict[str, Any]]:
    """Answer then judge every question. Progress to stderr."""
    print(f"\n{label}: {len(question_ids)} questions | answerer={cfg.answerer_model} "
          f"| judge={cfg.judge_model} | config={cfg.config_hash}", file=sys.stderr)

    answer_sem = asyncio.Semaphore(cfg.concurrency)
    judge_sem = asyncio.Semaphore(8)
    progress = ProgressReporter(len(question_ids))
    rows: list[dict[str, Any]] = []

    async def one(qid: int) -> dict[str, Any]:
        record = records_by_id[qid]
        answer = await answer_one(record, cfg, cache, answer_sem)
        if "error" in answer:
            row = score_row(record, answer, {})
            progress.tick()
            return row
        items, _ = parse_items(answer["text"])
        candidate = ", ".join(items) if items else answer["text"][:300]
        verdict = await judge_one(record, candidate, cfg, cache, judge_sem)
        row = score_row(record, answer, verdict)
        progress.tick(row.get("category"), row.get("judged_correct")) if "error" not in row \
            else progress.tick()
        return row

    tasks = [asyncio.ensure_future(one(q)) for q in question_ids]
    for coro in asyncio.as_completed(tasks):
        rows.append(await coro)
        if len(rows) % 10 == 0:
            cache.flush()
    cache.flush()
    rows.sort(key=lambda r: r["id"])
    return rows
