"""Per-domain root-cause analysis of LoCoMo failures.

Runs over the recorded full-pipeline results in ``demo/maximal.json`` -- 1540
questions with GPT-4o judge verdicts -- because that is the real NeuralGraph
system. The local 7B replay measures a much weaker answerer and would diagnose
the model, not the system.

Every failing question gets exactly one label, assigned in a fixed priority
order so the counts partition the failures and the largest bucket per domain is
unambiguous. Evidence-side causes are ranked ahead of generation-side ones
because no prompt change recovers evidence that was never retrieved.

The point of the per-domain split is that the domains fail for different
reasons, and a single overall ranking hides that. A fix aimed at the corpus-wide
dominant cause can be worthless for the weakest domain.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluation.diagnose_accuracy import _found, answer_items, evidence_present, retrieved_text

CORPUS = Path("demo/maximal.json")


def _answer_type(text: str) -> str:
    import re
    t = (text or "").lower().strip()
    if re.search(r"\b(19|20)\d{2}\b", t) or re.search(
        r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t
    ):
        return "date"
    if re.fullmatch(r"[\d,.]+", t):
        return "number"
    if t in {"yes", "no"}:
        return "yesno"
    return "other"


def classify(record: dict[str, Any], measure: str = "lenient") -> dict[str, Any]:
    """Label one record under a given evidence measure.

    ``measure`` decides what counts as "the evidence was retrieved":
    ``lenient`` needs every content token of the gold answer present,
    ``partial`` needs at least half. The choice is not cosmetic -- it moves
    failures between the retrieval and generation buckets and can invert which
    root cause dominates a domain. Both are therefore reported.
    """
    gold = str(record.get("gold_answer") or "")
    predicted = str(record.get("generated_answer") or "")
    correct = bool(record.get("correct"))
    strict, lenient = evidence_present(record)
    found = _found(record, measure)
    haystack = retrieved_text(record).lower()

    gold_items = answer_items(gold)
    pred_items = answer_items(predicted)

    # Which gold items are present in the retrieved evidence at all?
    recoverable = [g for g in gold_items if g and g.lower() in haystack]
    # Which gold items did the answer actually cover?
    covered = [g for g in gold_items if g and g.lower() in predicted.lower()]
    unsupported = [p for p in pred_items if p and p.lower() not in haystack]

    abstained = any(
        marker in predicted.lower()
        for marker in ("not in evidence", "no mention", "does not mention",
                       "not mentioned", "cannot determine", "no information")
    ) or not predicted.strip()

    signals = {
        "gold_items": len(gold_items),
        "recoverable_items": len(recoverable),
        "covered_items": len(covered),
        "unsupported_items": len(unsupported),
        "evidence_strict": strict,
        "evidence_lenient": lenient,
        "evidence_found": found,
        "measure": measure,
        "abstained": abstained,
        "gold_type": _answer_type(gold),
        "pred_type": _answer_type(predicted),
        "is_list": len(gold_items) > 1,
    }

    if correct:
        return {"label": None, **signals}

    # Priority order. Evidence first: nothing downstream recovers absent evidence.
    if not found:
        label = "retrieval_miss"
    elif abstained:
        label = "false_abstention"
    elif signals["gold_type"] != signals["pred_type"] and signals["gold_type"] != "other":
        label = "answer_type_mismatch"
    elif gold_items and len(covered) < len(gold_items) and covered:
        label = "incomplete_list"
    elif unsupported and not covered:
        label = "wrong_selection"
    elif unsupported:
        label = "unsupported_items"
    else:
        label = "wrong_selection"
    return {"label": label, **signals}


RCA_NOTES = {
    "retrieval_miss": (
        "The gold answer is absent from the retrieved excerpts. No prompt or "
        "generation change can recover it; this is retrieval or an unanswerable "
        "question."
    ),
    "false_abstention": (
        "The evidence was retrieved and the model declined to answer. Pure "
        "generation defect, and the cheapest class to fix."
    ),
    "answer_type_mismatch": (
        "Answered a different type than the question asks -- typically a date "
        "where a thing was wanted, or vice versa. Routing/prompt defect."
    ),
    "incomplete_list": (
        "Covered some but not all gold items it had evidence for. The "
        "single largest generation defect on list-valued questions."
    ),
    "unsupported_items": (
        "Asserted items the retrieved evidence does not contain -- invention "
        "rather than omission. Precision defect."
    ),
    "wrong_selection": (
        "Had the evidence and answered unrelated content. Evidence-selection or "
        "attention defect inside generation."
    ),
}


def analyse(records: list[dict[str, Any]], measure: str = "lenient") -> dict[str, Any]:
    per_domain: dict[str, dict[str, Any]] = {}
    domains = sorted({str(r.get("category")) for r in records})

    overall_counts: dict[str, int] = defaultdict(int)
    rows: list[dict[str, Any]] = []

    for record in records:
        c = classify(record, measure)
        rows.append({"id": record.get("id"), "category": str(record.get("category")), **c})
        if c["label"]:
            overall_counts[c["label"]] += 1

    for domain in domains:
        sub = [r for r in rows if r["category"] == domain]
        failures = [r for r in sub if r["label"]]
        counts: dict[str, int] = defaultdict(int)
        for r in failures:
            counts[r["label"]] += 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

        n = len(sub)
        n_fail = len(failures)
        lists = [r for r in sub if r["is_list"]]
        list_fail = [r for r in lists if r["label"]]

        # Recoverable = failures where the evidence was actually present.
        recoverable = [r for r in failures if r["evidence_found"]]

        per_domain[domain] = {
            "n": n,
            "accuracy": round((n - n_fail) / n, 4) if n else 0.0,
            "failures": n_fail,
            "evidence_present_rate": round(
                sum(1 for r in sub if r["evidence_found"]) / n, 4) if n else 0.0,
            "recoverable_failures": len(recoverable),
            "recoverable_share_of_failures": round(
                len(recoverable) / n_fail, 4) if n_fail else 0.0,
            "list_question_share": round(len(lists) / n, 4) if n else 0.0,
            "list_failure_share_of_failures": round(
                len(list_fail) / n_fail, 4) if n_fail else 0.0,
            "counts": dict(ranked),
            "shares": {k: round(v / n_fail, 4) for k, v in ranked} if n_fail else {},
            "dominant": ranked[0][0] if ranked else None,
            "dominant_n": ranked[0][1] if ranked else 0,
            "dominant_share": round(ranked[0][1] / n_fail, 4) if ranked else 0.0,
            "rca": RCA_NOTES.get(ranked[0][0], "") if ranked else "",
        }

    total_fail = sum(overall_counts.values())
    return {
        "corpus": str(CORPUS),
        "measure": measure,
        "n": len(records),
        "accuracy": round((len(records) - total_fail) / len(records), 4),
        "total_failures": total_fail,
        "overall_counts": dict(sorted(overall_counts.items(), key=lambda kv: -kv[1])),
        "overall_shares": {
            k: round(v / total_fail, 4)
            for k, v in sorted(overall_counts.items(), key=lambda kv: -kv[1])
        },
        "by_domain": per_domain,
        "label_definitions": RCA_NOTES,
    }


def main() -> int:
    records = json.loads(CORPUS.read_text(encoding="utf-8"))["results"]
    reports = {m: analyse(records, m) for m in ("lenient", "partial")}

    # The headline finding is where the two measures disagree about the
    # dominant cause. That disagreement is the result, not a nuisance.
    flips = {}
    for domain in reports["lenient"]["by_domain"]:
        a = reports["lenient"]["by_domain"][domain]["dominant"]
        b = reports["partial"]["by_domain"][domain]["dominant"]
        if a != b:
            flips[domain] = {"lenient": a, "partial": b}

    out = Path("evaluation/artifacts/locomo_loop/failure_rca.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"measures": reports,
         "dominant_cause_flips_by_measure": flips,
         "note": (
             "Two evidence measures are reported because the dominant root "
             "cause is sensitive to the definition of 'the evidence was "
             "retrieved'. lenient requires every gold content token in the "
             "retrieved text; partial requires at least half. docs/ACCURACY.md "
             "headlines the partial measure. Any fix plan must state which "
             "measure it is aimed at."
         )}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
