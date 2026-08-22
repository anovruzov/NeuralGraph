"""Replay the answer-generation step with Claude, holding retrieval fixed.

`docs/ACCURACY.md` established that single-hop failure is a *generation*
problem: ~76% of failures had their evidence retrieved. That diagnosis implies
a specific experiment, and `demo/maximal.json` already contains everything it
needs -- the retrieved memories are recorded per question.

So the generation step can be replayed on its own. Same questions, same
retrieved context, different answerer. Retrieval is held fixed by construction,
which is what makes any measured difference attributable to generation rather
than to a better search.

    export ANTHROPIC_API_KEY=sk-ant-...
    python3 -m evaluation.replay_generation demo/maximal.json --limit 40 --category single_hop

WHY THE GRADER IS NOT A JUDGE MODEL

The recorded run used a GPT-4o judge emitting a binary verdict. That hides
exactly the thing the diagnosis found: answers fail as *sets* -- naming two of
three gold items scores the same zero as naming none. So the primary grader
here is deterministic item-level precision/recall/F1 against the gold list.

That choice has three benefits: it costs nothing, it is perfectly reproducible,
and it shows partial credit, so "the fix recovered a third item" is visible
rather than being rounded away. `exact_set_match` is reported alongside as the
strict binary comparable to the original judge.

An LLM judge is deliberately not the default. Using Claude to grade Claude
would score the fix with the model being fixed.

COST. A full 1540-question replay is roughly 3M input tokens; at Opus 5 rates
(~$5/1M in, $25/1M out) expect on the order of $20-40. Use `--limit` for a
pilot first, and `--dry-run` to see the estimate and a sample prompt without
spending anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluation.diagnose_accuracy import answer_items, content_tokens, retrieved_text

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "medium"

# The prompt is the intervention under test. Every instruction in it targets a
# failure mode measured in docs/ACCURACY.md, and nothing else is in here:
#   - 85/86 failures omitted a gold item      -> enumerate exhaustively
#   - a third invented plausible items        -> only what the evidence states
#   - 38/86 answered unrelated content        -> answer THIS question
SYSTEM_PROMPT = """\
You answer questions about a conversation, using only the supplied excerpts.

Rules:
1. Answer only from the excerpts. If the excerpts do not contain the answer, \
say exactly: NOT IN EVIDENCE.
2. Never add an item the excerpts do not state. Do not infer plausible extras.
3. If the question asks what things, which things, or otherwise admits more \
than one answer, list EVERY distinct item the excerpts support - scan all of \
them before answering, not just the first relevant one.
4. Answer the question actually asked. Related information that does not \
answer it must be left out.
5. Give the answer only. No preamble, no explanation, no restating the question.
"""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Every distinct item answering the question. One element for a "
                    "single-valued answer. Empty if the evidence does not answer it."
                ),
            },
            "not_in_evidence": {
                "type": "boolean",
                "description": "True when the excerpts do not answer the question.",
            },
        },
        "required": ["items", "not_in_evidence"],
        "additionalProperties": False,
    },
}


@dataclass
class Grade:
    """Item-level scoring of one answer against its gold list."""

    precision: float
    recall: float
    f1: float
    exact_set_match: bool
    gold_items: int
    predicted_items: int
    missing: list[str] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)


def _stem(token: str) -> str:
    """Strip a plural 's' so 'horses' and 'horse' compare equal.

    Deliberately minimal. Real stemming would conflate distinct gold items
    ("painting" and "paint") and silently inflate recall, which is the metric
    under test. This is the same morphology trap that inverted the two evidence
    measures in ``diagnose_accuracy`` — worth handling, not worth over-handling.
    """
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _item_key(item: str) -> frozenset[str]:
    """Compare items by stemmed content tokens, so 'horses' matches 'a horse'."""
    return frozenset(_stem(token) for token in content_tokens(item))


def grade(gold: str, predicted: str | list[str]) -> Grade:
    """Score predicted items against gold items by token-set overlap.

    Two items match when their content-token sets intersect. Exact string
    equality would fail on ordinary paraphrase ("Pride parade" vs "LGBTQ+ pride
    parade") and would measure phrasing rather than content.
    """
    gold_list = [g for g in answer_items(gold) if _item_key(g)]
    if isinstance(predicted, str):
        predicted_list = [p for p in answer_items(predicted) if _item_key(p)]
    else:
        predicted_list = [p for p in predicted if _item_key(p)]

    gold_keys = [_item_key(g) for g in gold_list]
    pred_keys = [_item_key(p) for p in predicted_list]

    matched_gold: set[int] = set()
    matched_pred: set[int] = set()
    for gi, gk in enumerate(gold_keys):
        for pi, pk in enumerate(pred_keys):
            if pi in matched_pred:
                continue
            if gk & pk:
                matched_gold.add(gi)
                matched_pred.add(pi)
                break

    hits = len(matched_gold)
    precision = hits / len(pred_keys) if pred_keys else 0.0
    recall = hits / len(gold_keys) if gold_keys else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Grade(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        exact_set_match=bool(gold_keys) and hits == len(gold_keys) == len(pred_keys),
        gold_items=len(gold_keys),
        predicted_items=len(pred_keys),
        missing=[g for i, g in enumerate(gold_list) if i not in matched_gold],
        spurious=[p for i, p in enumerate(predicted_list) if i not in matched_pred],
    )


def build_prompt(record: dict[str, Any]) -> str:
    excerpts = []
    for memory in record.get("retrieved_memories") or ():
        if isinstance(memory, dict):
            speaker = memory.get("speaker") or "unknown"
            when = memory.get("datetime") or ""
            text = memory.get("text") or ""
            excerpts.append(f"[{speaker}{(', ' + when) if when else ''}] {text}")
        else:
            excerpts.append(str(memory))
    joined = "\n".join(f"- {line}" for line in excerpts)
    return f"Excerpts:\n{joined}\n\nQuestion: {record.get('question', '')}"


def estimate_cost(records: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Rough offline estimate, so a dry run costs nothing.

    Characters/4 is a crude token proxy; it is used only to warn about the order
    of magnitude before spending money, never reported as a measurement.
    """
    chars = sum(len(build_prompt(r)) + len(SYSTEM_PROMPT) for r in records)
    input_tokens = chars / 4
    output_tokens = 60 * len(records)
    rate_in, rate_out = (5.0, 25.0) if "opus" in model else (3.0, 15.0)
    return {
        "questions": len(records),
        "estimated_input_tokens": int(input_tokens),
        "estimated_output_tokens": int(output_tokens),
        "estimated_usd": round(
            input_tokens / 1e6 * rate_in + output_tokens / 1e6 * rate_out, 2
        ),
        "note": "chars/4 proxy; order-of-magnitude only",
    }


async def answer_one(client, record: dict[str, Any], model: str, effort: str,
                     semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                output_config={"effort": effort, "format": RESPONSE_FORMAT},
                messages=[{"role": "user", "content": build_prompt(record)}],
            )
        except Exception as exc:  # surfaced per-record; the sweep continues
            return {"id": record.get("id"), "error": f"{type(exc).__name__}: {exc}"}

    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        parsed = json.loads(text)
        items = [str(i) for i in parsed.get("items", [])]
        not_in_evidence = bool(parsed.get("not_in_evidence"))
    except (json.JSONDecodeError, AttributeError):
        items, not_in_evidence = answer_items(text), False

    scored = grade(str(record.get("gold_answer", "")), items)
    baseline = grade(str(record.get("gold_answer", "")),
                     str(record.get("generated_answer", "")))
    return {
        "id": record.get("id"),
        "category": record.get("category"),
        "question": record.get("question"),
        "gold_answer": record.get("gold_answer"),
        "baseline_answer": record.get("generated_answer"),
        "baseline_judged_correct": bool(record.get("correct")),
        "claude_items": items,
        "claude_not_in_evidence": not_in_evidence,
        "claude": scored.__dict__,
        "baseline": baseline.__dict__,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "error" not in r]
    errors = [r for r in rows if "error" in r]
    if not scored:
        return {"scored": 0, "errors": len(errors),
                "error_sample": [r["error"] for r in errors[:3]]}

    def mean(key: str, side: str) -> float:
        return round(sum(r[side][key] for r in scored) / len(scored), 4)

    return {
        "scored": len(scored),
        "errors": len(errors),
        "error_sample": [r["error"] for r in errors[:3]],
        "claude": {
            "item_f1": mean("f1", "claude"),
            "item_recall": mean("recall", "claude"),
            "item_precision": mean("precision", "claude"),
            "exact_set_match": round(
                sum(1 for r in scored if r["claude"]["exact_set_match"]) / len(scored), 4
            ),
        },
        "baseline": {
            "item_f1": mean("f1", "baseline"),
            "item_recall": mean("recall", "baseline"),
            "item_precision": mean("precision", "baseline"),
            "exact_set_match": round(
                sum(1 for r in scored if r["baseline"]["exact_set_match"]) / len(scored), 4
            ),
            "original_judge_accuracy": round(
                sum(1 for r in scored if r["baseline_judged_correct"]) / len(scored), 4
            ),
        },
        "tokens": {
            "input": sum(r["usage"]["input_tokens"] for r in scored),
            "output": sum(r["usage"]["output_tokens"] for r in scored),
        },
    }


async def run(records, model, effort, concurrency):
    import anthropic

    client = anthropic.AsyncAnthropic()
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [answer_one(client, r, model, effort, semaphore) for r in records]
    return await asyncio.gather(*tasks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--category", help="restrict to one category")
    parser.add_argument("--limit", type=int, help="first N questions (use for a pilot)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT,
                        choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="print a sample prompt and cost estimate; call nothing")
    args = parser.parse_args(argv)

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    records = payload["results"] if isinstance(payload, dict) else payload
    if args.category:
        records = [r for r in records if r.get("category") == args.category]
    if args.limit:
        records = records[:args.limit]

    if args.dry_run:
        print(json.dumps(estimate_cost(records, args.model), indent=2))
        if records:
            print("\n--- system prompt ---\n" + SYSTEM_PROMPT)
            print("--- sample user prompt ---\n" + build_prompt(records[0])[:1200])
        return 0

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(
            "No credentials found.\n"
            "  Create a key at https://console.anthropic.com -> Settings -> API keys\n"
            "  then:  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "Run with --dry-run to preview prompts and cost without a key.",
            file=sys.stderr,
        )
        return 2

    rows = asyncio.run(run(records, args.model, args.effort, args.concurrency))
    report = {
        "model": args.model,
        "effort": args.effort,
        "category": args.category or "all",
        "summary": summarise(rows),
        "rows": rows,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps(report["summary"], indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
