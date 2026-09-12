"""Split QA failures into retrieval failures and answer-generation failures.

"Single-hop accuracy is 50%" is not a diagnosis. It is consistent with two
opposite problems that need opposite fixes:

    retrieval failure       the supporting fact never reached the answerer
    generation failure      the fact was right there and the answer was wrong

This reads a recorded benchmark artifact -- which stores, per question, the
retrieved memories alongside the gold answer and the judge's verdict -- and
separates the two. It needs no Ollama, no API key and no network: everything it
reports is recomputed from what the run already wrote down.

    python3 -m evaluation.diagnose_accuracy demo/maximal.json

HOW EVIDENCE PRESENCE IS DECIDED, and why the raw numbers cannot be trusted.

There is no ground-truth label for "the supporting fact was retrieved", so this
uses the gold answer's presence in the retrieved text as a proxy, under three
measures:

    strict    the normalised gold answer appears as a substring
    lenient   every content token of the gold answer appears somewhere
    partial   at least half of the gold answer's content tokens appear

The proxy under-detects: it cannot see evidence that was paraphrased, computed,
or -- the dominant case in this corpus -- partially listed, where gold names
four things and three were retrieved. Under-detection inflates the apparent
"retrieval failure" count, which is precisely the conclusion the uncorrected
numbers suggest. So the raw split must not be read directly.

``calibrate()`` corrects for it. Correct answers are the calibration set: when
the system answered correctly it demonstrably had what it needed, so the
proxy's detection rate on correct answers estimates its true-positive rate.
Under ``partial`` that rate is 84-97% for the conversational categories, so the
correction is a modest adjustment rather than a load-bearing extrapolation.

The one category where this does not hold is ``open_domain`` (38% detection),
whose answers are not in the conversation at all. Its calibrated figures are
reported but should not be relied on.

WHAT THIS FOUND, on ``demo/maximal.json`` (1540 questions, GPT-4o judge):

Single-hop is a GENERATION problem, not a retrieval problem. About 76% of
single-hop failures had their evidence retrieved. That independently
corroborates the "77% recall@50" figure recorded in
``QUERY_ROUTING_IMPROVEMENTS.md`` by a different route.

The failure has a specific shape: 66% of single-hop questions have list-valued
gold answers, and 85 of 86 measured generation failures omit at least one gold
item. None were complete-but-over-inclusive. Within those, 38 share no content
token with the gold answer at all -- the answerer had the evidence and wrote
about something else, which is a selection failure rather than a coverage one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

# Words carrying no evidence value; dropped before token-overlap scoring so that
# a gold answer of "the gym" is not judged retrieved because "the" appears.
STOPWORDS = frozenset("""
a an the of to in on at by for with from and or but is are was were be been being
it its this that these those he she they them his her their i you we my your our
as if then than so such not no nor do does did done have has had will would can
could should may might must about into over under after before during
""".split())

MONTHS = {
    "jan": "january", "feb": "february", "mar": "march", "apr": "april",
    "jun": "june", "jul": "july", "aug": "august", "sep": "september",
    "sept": "september", "oct": "october", "nov": "november", "dec": "december",
}


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, expand month abbreviations.

    Date formatting varies between the gold answer and the stored memory text
    ("7 May 2023" vs "May 7, 2023"), so month names are normalised and ordinal
    suffixes dropped. Without this, correct temporal retrievals read as misses.
    """
    text = text.lower()
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [MONTHS.get(token, token) for token in text.split()]
    return " ".join(tokens)


def content_tokens(text: str) -> list[str]:
    return [token for token in normalise(text).split() if token not in STOPWORDS]


def retrieved_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for memory in record.get("retrieved_memories") or ():
        if isinstance(memory, dict):
            for key in ("text", "speaker", "datetime"):
                value = memory.get(key)
                if isinstance(value, str):
                    parts.append(value)
        elif isinstance(memory, str):
            parts.append(memory)
    return " ".join(parts)


def evidence_present(record: dict[str, Any]) -> tuple[bool, bool]:
    """Return (strict, lenient) evidence presence for one question."""
    gold = str(record.get("gold_answer") or "").strip()
    if not gold:
        return False, False
    haystack = normalise(retrieved_text(record))
    if not haystack:
        return False, False

    strict = normalise(gold) in haystack
    tokens = content_tokens(gold)
    if tokens:
        # Substring, not set membership. Exact token matching looks more
        # rigorous and is simply wrong here: it misses ordinary morphology
        # ("sunset" vs "sunsets"), which made the lenient measure *stricter*
        # than the strict one and inverted the two. Lenient must be a superset
        # of strict by construction -- if the whole gold string is present then
        # every token in it is -- and ``test_diagnose_accuracy.py`` asserts that
        # implication over the real corpus so it cannot silently break again.
        lenient = all(token in haystack for token in tokens)
    else:
        # A gold answer that is entirely stopwords ("yes", "no") carries no
        # tokens to match on. Fall back to strict rather than scoring it
        # trivially lenient, which would inflate apparent retrieval success.
        lenient = strict
    return strict, lenient


def token_recall(record: dict[str, Any]) -> float:
    """Fraction of the gold answer's content tokens present in retrieved text.

    All-or-nothing matching is wrong for list answers -- gold "pottery, camping,
    painting, swimming" scores zero if three of the four were retrieved, which
    is not a retrieval failure in any useful sense. This is the continuous
    version the ``partial`` measure thresholds.
    """
    gold = str(record.get("gold_answer") or "").strip()
    tokens = content_tokens(gold)
    if not tokens:
        return 1.0 if evidence_present(record)[0] else 0.0
    haystack = normalise(retrieved_text(record))
    return sum(1 for token in tokens if token in haystack) / len(tokens)


PARTIAL_THRESHOLD = 0.5


def calibrate(records: list[dict[str, Any]], measure: str) -> dict[str, Any]:
    """Correct the evidence proxy for its own false-negative rate.

    The proxy cannot see evidence that was paraphrased, computed, or partially
    listed, so it under-detects. Left uncorrected, that bias inflates
    "retrieval failure" -- which happens to be the conclusion the raw numbers
    suggest, so the conclusion cannot be taken at face value.

    Correct answers calibrate it. When the system answered correctly it
    demonstrably had what it needed, so the detection rate on correct answers
    estimates P(proxy fires | evidence really was there). Dividing the observed
    detection rate on *wrong* answers by that gives a bias-corrected estimate of
    how many failures actually had their evidence retrieved.

    The assumption -- that a correct answer implies evidence was present -- is
    strong for extractive categories like ``single_hop`` and weaker where
    answers are computed or inferable without a retrieved span. It is reported
    per category rather than pooled so the reader can discount it accordingly,
    and ``detection_rate`` is reported alongside every estimate so a category
    whose calibration rests on few correct answers is visible as such.
    """
    out: dict[str, Any] = {}
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_category[str(record.get("category", "unknown"))].append(record)
    by_category["TOTAL"] = list(records)

    for category, rows in sorted(by_category.items()):
        correct = [r for r in rows if r.get("correct")]
        wrong = [r for r in rows if not r.get("correct")]
        if not correct or not wrong:
            continue
        detected = sum(1 for r in correct if _found(r, measure))
        detection_rate = detected / len(correct)
        observed_wrong = sum(1 for r in wrong if _found(r, measure))
        if detection_rate > 0:
            estimated = min(observed_wrong / detection_rate, float(len(wrong)))
        else:
            estimated = float("nan")
        out[category] = {
            "correct": len(correct),
            "wrong": len(wrong),
            "detection_rate_on_correct": round(100 * detection_rate, 1),
            "observed_evidence_on_wrong": observed_wrong,
            "estimated_evidence_on_wrong": round(estimated, 1),
            "estimated_generation_share_of_failures": round(100 * estimated / len(wrong), 1),
            "estimated_retrieval_share_of_failures": round(
                100 * (len(wrong) - estimated) / len(wrong), 1
            ),
        }
    return out


def _found(record: dict[str, Any], measure: str) -> bool:
    if measure == "strict":
        return evidence_present(record)[0]
    if measure == "lenient":
        return evidence_present(record)[1]
    if measure == "partial":
        return token_recall(record) >= PARTIAL_THRESHOLD
    raise ValueError(f"unknown measure: {measure}")


def classify(record: dict[str, Any], measure: str) -> str:
    found = _found(record, measure)
    correct = bool(record.get("correct"))
    if found and correct:
        return "healthy"
    if found and not correct:
        return "generation_failure"
    if not found and not correct:
        return "retrieval_failure"
    return "answered_without_visible_evidence"


LABELS = (
    "healthy",
    "generation_failure",
    "retrieval_failure",
    "answered_without_visible_evidence",
)


def diagnose(records: list[dict[str, Any]], measure: str = "lenient") -> dict[str, Any]:
    by_category: dict[str, Counter] = defaultdict(Counter)
    overall: Counter = Counter()
    for record in records:
        label = classify(record, measure)
        by_category[str(record.get("category", "unknown"))][label] += 1
        overall[label] += 1

    def summarise(counts: Counter) -> dict[str, Any]:
        total = sum(counts.values())
        wrong = counts["generation_failure"] + counts["retrieval_failure"]
        return {
            "n": total,
            "accuracy": round(
                100 * (counts["healthy"] + counts["answered_without_visible_evidence"]) / total, 1
            ) if total else 0.0,
            "counts": {label: counts[label] for label in LABELS},
            # Of everything that went wrong, what share was generation's fault?
            "generation_share_of_failures": round(
                100 * counts["generation_failure"] / wrong, 1
            ) if wrong else None,
            "evidence_present_rate": round(
                100 * (counts["healthy"] + counts["generation_failure"]) / total, 1
            ) if total else 0.0,
        }

    return {
        "measure": measure,
        "overall": summarise(overall),
        "by_category": {
            category: summarise(counts) for category, counts in sorted(by_category.items())
        },
    }


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"evidence measure: {report['measure']}")
    lines.append("")
    header = (
        f"{'category':14s} {'n':>5s} {'acc':>7s} {'evid':>7s} "
        f"{'healthy':>8s} {'gen fail':>9s} {'retr fail':>10s} {'no-eviok':>9s} {'gen%err':>8s}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    rows = list(report["by_category"].items()) + [("TOTAL", report["overall"])]
    for category, summary in rows:
        counts = summary["counts"]
        share = summary["generation_share_of_failures"]
        lines.append(
            f"{category:14s} {summary['n']:5d} {summary['accuracy']:6.1f}% "
            f"{summary['evidence_present_rate']:6.1f}% "
            f"{counts['healthy']:8d} {counts['generation_failure']:9d} "
            f"{counts['retrieval_failure']:10d} "
            f"{counts['answered_without_visible_evidence']:9d} "
            f"{(f'{share:.1f}%' if share is not None else '-'):>8s}"
        )
    lines.append("")
    lines.append("gen fail   = evidence was retrieved, answer still wrong")
    lines.append("retr fail  = evidence not found in retrieved memories")
    lines.append("no-eviok   = correct without visible evidence (derived, or judge leniency)")
    lines.append("gen%err    = generation's share of everything that went wrong")
    return "\n".join(lines)


def render_calibration(table: dict[str, Any], measure: str) -> str:
    lines = [f"bias-corrected split (measure: {measure})", ""]
    header = (f"{'category':14s} {'wrong':>6s} {'detect%':>8s} {'obs':>5s} "
              f"{'est':>7s} {'GEN%':>7s} {'RETR%':>7s}")
    lines.append(header)
    lines.append("-" * len(header))
    for category, row in table.items():
        lines.append(
            f"{category:14s} {row['wrong']:6d} {row['detection_rate_on_correct']:7.1f}% "
            f"{row['observed_evidence_on_wrong']:5d} {row['estimated_evidence_on_wrong']:7.1f} "
            f"{row['estimated_generation_share_of_failures']:6.1f}% "
            f"{row['estimated_retrieval_share_of_failures']:6.1f}%"
        )
    lines.append("")
    lines.append("detect% = proxy detection rate on CORRECT answers (its true-positive rate)")
    lines.append("obs/est = evidence on wrong answers, observed then bias-corrected")
    lines.append("GEN%    = share of failures where evidence WAS retrieved (generation's fault)")
    return "\n".join(lines)


def samples(records: list[dict[str, Any]], category: str, label: str,
            measure: str, limit: int) -> list[dict[str, str]]:
    out = []
    for record in records:
        if str(record.get("category")) != category:
            continue
        if classify(record, measure) != label:
            continue
        out.append({
            "question": str(record.get("question", ""))[:150],
            "gold": str(record.get("gold_answer", ""))[:100],
            "generated": str(record.get("generated_answer", ""))[:200],
        })
        if len(out) >= limit:
            break
    return out


def failure_modes(records: list[dict[str, Any]], category: str,
                  measure: str = "partial") -> dict[str, Any]:
    """Characterise generation failures: are lists incomplete, or simply wrong?

    A wrong answer to a list question can fail two very different ways, and they
    need different fixes: omitting items the evidence contained (a coverage
    problem in extraction), or answering about unrelated content despite having
    the evidence (a selection problem in prompting). Counting them separately is
    what turns "50% accuracy" into something actionable.
    """
    rows = [r for r in records if str(r.get("category")) == category]
    listy = [r for r in rows if len(answer_items(r.get("gold_answer"))) >= 2]
    failures = [r for r in rows if not r.get("correct") and _found(r, measure)]

    incomplete = disjoint = complete = 0
    for record in failures:
        gold = set()
        for item in answer_items(record.get("gold_answer")):
            gold |= set(content_tokens(item))
        produced = set(content_tokens(str(record.get("generated_answer") or "")))
        if not gold:
            continue
        overlap = gold & produced
        if not overlap:
            disjoint += 1
        elif gold <= produced:
            complete += 1
        else:
            incomplete += 1

    def pct(part: int, whole: int) -> float | None:
        return round(100 * part / whole, 1) if whole else None

    return {
        "category": category,
        "n": len(rows),
        "list_valued_gold": len(listy),
        "list_valued_share": pct(len(listy), len(rows)),
        "accuracy_on_list_questions": pct(
            sum(1 for r in listy if r.get("correct")), len(listy)
        ),
        "accuracy_on_single_item_questions": pct(
            sum(1 for r in rows if r not in listy and r.get("correct")),
            len(rows) - len(listy),
        ),
        "generation_failures": len(failures),
        "omits_a_gold_item": incomplete + disjoint,
        "no_overlap_with_gold": disjoint,
        "complete_but_judged_wrong": complete,
    }


def answer_items(value: Any) -> list[str]:
    """Split a gold answer into its list items, if it has any."""
    parts = [part.strip() for part in re.split(r",| and |;|/", str(value or "")) if part.strip()]
    return [part for part in parts if content_tokens(part)]


def load(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "results" in payload:
        return list(payload["results"])
    if isinstance(payload, list):
        return list(payload)
    raise ValueError("artifact has no 'results' array")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--measure",
                        choices=("strict", "lenient", "partial", "all"), default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--samples", metavar="CATEGORY",
                        help="print example failures for this category")
    parser.add_argument("--sample-label", default="generation_failure", choices=LABELS)
    parser.add_argument("--sample-limit", type=int, default=8)
    args = parser.parse_args(argv)

    records = load(args.artifact)
    measures = ("strict", "lenient", "partial") if args.measure == "all" else (args.measure,)
    reports = {measure: diagnose(records, measure) for measure in measures}
    calibration = {measure: calibrate(records, measure) for measure in measures}

    if args.format == "json":
        payload = json.dumps(
            {"reports": reports, "calibration": calibration}, sort_keys=True, indent=2
        ) + "\n"
    else:
        blocks = [render(reports[measure]) for measure in measures]
        blocks.append(render_calibration(calibration[measures[-1]], measures[-1]))
        payload = "\n\n".join(blocks) + "\n"
        if args.samples:
            picked = samples(records, args.samples, args.sample_label,
                             measures[-1], args.sample_limit)
            payload += f"\n{args.sample_label} examples in {args.samples}:\n"
            for item in picked:
                payload += (
                    f"\n  Q    {item['question']}\n"
                    f"  gold {item['gold']}\n"
                    f"  got  {item['generated']}\n"
                )
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
