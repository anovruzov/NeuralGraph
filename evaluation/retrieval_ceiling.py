"""Split retrieval failures into recoverable misses and unanswerable questions.

`docs/ACCURACY.md` sized the retrieval bucket at 186 questions whose evidence
was not in the retrieved context, and treated all of them as recoverable. That
assumption was never checked, and it is wrong for a quarter of them.

A question is only recoverable by better retrieval if its answer is *in the
corpus to begin with*. `evaluation/locomo/locomo10.json` holds the full source
conversations, so this checks each gold answer against the entire conversation
it came from — every session, every turn — rather than against the ~21 memories
retrieval happened to return.

    python3 -m evaluation.retrieval_ceiling

Three outcomes per question:

    retrieved       the evidence reached the answerer
    missed          the answer is in the conversation, retrieval did not find it
    unanswerable    the answer is nowhere in the conversation

Only ``missed`` is a retrieval problem. ``unanswerable`` is a hard ceiling: no
amount of recall recovers it, because the corpus does not contain the answer.
Counting those as recoverable inflates every projection built on top.

WHY THIS IS A LOWER BOUND ON ANSWERABILITY

The same token-overlap proxy as `diagnose_accuracy` is used, so it inherits the
same one-sided bias: an answer that must be *derived* (a duration, a count) can
be unfindable as text while still being perfectly answerable. So "unanswerable"
is an over-count and "missed" an under-count — the retrieval prize is at least
as large as reported here, never smaller. That direction is the safe one: it
cannot make retrieval look more promising than it is.

`open_domain` is expected to dominate the unanswerable group by construction —
those questions are not about the conversation — which is a useful sanity check
that the measure is finding what it claims.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evaluation.diagnose_accuracy import _found, content_tokens, normalise

LOCOMO = Path(__file__).resolve().parent / "locomo" / "locomo10.json"
ARTIFACT = Path(__file__).resolve().parents[1] / "demo" / "maximal.json"

#: Fraction of a gold answer's content tokens that must appear in the source
#: conversation for it to count as answerable. Matches the ``partial`` measure
#: in ``diagnose_accuracy`` so the two analyses agree on what "present" means.
PRESENCE_THRESHOLD = 0.5


def conversation_corpus(path: Path = LOCOMO) -> dict[int, str]:
    """Normalised full text of each source conversation, keyed 1..N.

    ``demo/maximal.json`` numbers conversations from 1; ``locomo10.json`` is a
    list in the same order, verified by matching speaker names. Every session
    list and every free-text field is included, because a gold answer may come
    from a summary rather than a turn.
    """
    samples = json.loads(path.read_text(encoding="utf-8"))
    corpus: dict[int, str] = {}
    for index, sample in enumerate(samples, start=1):
        parts: list[str] = []
        for value in (sample.get("conversation") or {}).values():
            if isinstance(value, list):
                for turn in value:
                    if isinstance(turn, dict):
                        parts.append(str(turn.get("text", "")))
                        if turn.get("speaker"):
                            parts.append(str(turn["speaker"]))
            elif isinstance(value, str):
                parts.append(value)
        corpus[index] = normalise(" ".join(parts))
    return corpus


def answerable(record: dict[str, Any], corpus: dict[int, str],
               threshold: float = PRESENCE_THRESHOLD) -> bool:
    """Is the gold answer present in the conversation this question came from?"""
    tokens = content_tokens(str(record.get("gold_answer", "")))
    if not tokens:
        # No content tokens to test ("yes"/"no"). Assume answerable rather than
        # inflating the unanswerable count on a measurement artefact.
        return True
    haystack = corpus.get(record.get("conversation"))
    if not haystack:
        return True
    return sum(1 for t in tokens if t in haystack) / len(tokens) >= threshold


def classify(record: dict[str, Any], corpus: dict[int, str]) -> str:
    if _found(record, "partial"):
        return "retrieved"
    return "missed" if answerable(record, corpus) else "unanswerable"


def analyse(records: list[dict[str, Any]], corpus: dict[int, str]) -> dict[str, Any]:
    overall: Counter = Counter()
    by_category: dict[str, Counter] = defaultdict(Counter)
    failures: Counter = Counter()
    failures_by_category: dict[str, Counter] = defaultdict(Counter)

    for record in records:
        label = classify(record, corpus)
        overall[label] += 1
        by_category[str(record.get("category", "unknown"))][label] += 1
        if not record.get("correct"):
            failures[label] += 1
            failures_by_category[str(record.get("category", "unknown"))][label] += 1

    total = len(records)
    wrong = sum(1 for r in records if not r.get("correct"))
    correct = total - wrong
    unanswerable = failures["unanswerable"]

    return {
        "total_questions": total,
        "correct": correct,
        "accuracy": round(100 * correct / total, 1),
        "all_questions": dict(overall),
        "failures": dict(failures),
        "failures_by_category": {c: dict(v) for c, v in sorted(failures_by_category.items())},
        "by_category": {c: dict(v) for c, v in sorted(by_category.items())},
        # The number this module exists to produce.
        "recoverable_by_retrieval": failures["missed"],
        "unanswerable_from_corpus": unanswerable,
        "hard_ceiling_accuracy": round(100 * (total - unanswerable) / total, 1),
        "answered_correctly_without_corpus_support": sum(
            1 for r in records if r.get("correct") and not answerable(r, corpus)
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"questions {report['total_questions']}  "
                 f"accuracy {report['accuracy']}%")
    lines.append("")
    lines.append("Wrong answers, by why the evidence was not used:")
    failures = report["failures"]
    for label in ("retrieved", "missed", "unanswerable"):
        lines.append(f"  {label:14s} {failures.get(label, 0):4d}")
    lines.append("")
    header = f"{'category':14s} {'retrieved':>10s} {'missed':>8s} {'unanswerable':>13s}"
    lines.append(header)
    lines.append("-" * len(header))
    for category, counts in report["failures_by_category"].items():
        lines.append(f"{category:14s} {counts.get('retrieved', 0):10d} "
                     f"{counts.get('missed', 0):8d} {counts.get('unanswerable', 0):13d}")
    lines.append("")
    lines.append(f"recoverable by retrieval : {report['recoverable_by_retrieval']}")
    lines.append(f"unanswerable from corpus : {report['unanswerable_from_corpus']}")
    lines.append(f"hard ceiling             : {report['hard_ceiling_accuracy']}% "
                 "(no retrieval or generation fix can exceed this)")
    lines.append(f"correct without corpus support: "
                 f"{report['answered_correctly_without_corpus_support']} "
                 "(parametric knowledge or judge leniency)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("artifact", nargs="?", type=Path, default=ARTIFACT)
    parser.add_argument("--locomo", type=Path, default=LOCOMO)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    records = payload["results"] if isinstance(payload, dict) else payload
    report = analyse(records, conversation_corpus(args.locomo))

    text = (json.dumps(report, indent=2, sort_keys=True) + "\n"
            if args.format == "json" else render(report) + "\n")
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
