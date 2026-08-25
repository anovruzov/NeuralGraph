"""Paired round-over-round comparison, failure taxonomy, and insights.

Comparison is paired by question id, never by aggregate. Two runs over the same
60 questions share most of their variance, so a paired bootstrap over per-
question deltas is far tighter than comparing two independent means -- and it
is the only form that can say *which* questions moved.

The taxonomy exists so a round's diagnosis is derived from residual failures
rather than chosen by taste. Each failing question is assigned exactly one
label, in a fixed priority order, so the counts partition the failures and the
largest bucket is unambiguous.
"""

from __future__ import annotations

import random
from typing import Any

# Priority order matters: a question can exhibit several symptoms, and the
# label must name the one a fix should target first. Evidence problems come
# before generation problems because no prompt change recovers absent evidence.
FAILURE_LABELS = (
    "retrieval_miss",         # the gold answer is not in the retrieved evidence
    "false_abstention",       # evidence was present and the model declined
    "answer_type_mismatch",   # answered a different type than the question asks
    "incomplete_list",        # missed at least one gold item it had evidence for
    "unsupported_items",      # asserted items the evidence does not contain
    "wrong_selection",        # had evidence, answered unrelated content
)


def label_failure(row: dict[str, Any]) -> str | None:
    """Assign one label to a failing question, or None if it passed."""
    if row.get("error"):
        return "error"
    if row.get("judged_correct"):
        return None
    if not row.get("evidence_lenient"):
        return "retrieval_miss"
    if row.get("not_in_evidence"):
        return "false_abstention"
    if row.get("answer_type_mismatch"):
        return "answer_type_mismatch"
    if row.get("item_recall", 0) < 1.0 and row.get("item_recall", 0) > 0:
        return "incomplete_list"
    if row.get("unsupported_items", 0) > 0:
        return "unsupported_items"
    return "wrong_selection"


def taxonomy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Failure counts overall and by category. Labels partition the failures."""
    counts: dict[str, int] = {}
    by_cat: dict[str, dict[str, int]] = {}
    for row in rows:
        label = label_failure(row)
        if label is None:
            continue
        counts[label] = counts.get(label, 0) + 1
        cat = row.get("category", "?")
        by_cat.setdefault(cat, {})
        by_cat[cat][label] = by_cat[cat].get(label, 0) + 1

    total_failures = sum(counts.values())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "total_failures": total_failures,
        "counts": dict(ranked),
        "shares": {k: round(v / total_failures, 4) for k, v in ranked} if total_failures else {},
        "by_category": {c: dict(sorted(d.items(), key=lambda kv: -kv[1]))
                        for c, d in sorted(by_cat.items())},
        "dominant": ranked[0][0] if ranked else None,
        "dominant_n": ranked[0][1] if ranked else 0,
    }


def paired_bootstrap(before: dict[int, bool], after: dict[int, bool],
                     iterations: int = 10000, seed: int = 20260825) -> dict[str, Any]:
    """95% CI on the accuracy delta, resampling questions (not answers)."""
    ids = sorted(set(before) & set(after))
    if not ids:
        return {"n": 0, "delta": 0.0, "ci_low": 0.0, "ci_high": 0.0, "significant": False}
    deltas = [int(after[i]) - int(before[i]) for i in ids]
    observed = sum(deltas) / len(deltas)

    rng = random.Random(seed)
    samples = []
    n = len(deltas)
    for _ in range(iterations):
        samples.append(sum(deltas[rng.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    low = samples[int(0.025 * iterations)]
    high = samples[int(0.975 * iterations) - 1]
    return {
        "n": n,
        "delta": round(observed, 4),
        "ci_low": round(low, 4),
        "ci_high": round(high, 4),
        "significant": low > 0 or high < 0,
        "iterations": iterations,
    }


def compare(before_rows: list[dict[str, Any]], after_rows: list[dict[str, Any]]
            ) -> dict[str, Any]:
    """Paired comparison over identical question ids."""
    b = {r["id"]: r for r in before_rows if "error" not in r}
    a = {r["id"]: r for r in after_rows if "error" not in r}
    shared = sorted(set(b) & set(a))

    improved = [i for i in shared if not b[i]["judged_correct"] and a[i]["judged_correct"]]
    regressed = [i for i in shared if b[i]["judged_correct"] and not a[i]["judged_correct"]]

    before_acc = sum(1 for i in shared if b[i]["judged_correct"]) / len(shared) if shared else 0
    after_acc = sum(1 for i in shared if a[i]["judged_correct"]) / len(shared) if shared else 0

    cats = sorted({b[i]["category"] for i in shared})
    by_category = {}
    for c in cats:
        ids = [i for i in shared if b[i]["category"] == c]
        if not ids:
            continue
        bb = sum(1 for i in ids if b[i]["judged_correct"]) / len(ids)
        aa = sum(1 for i in ids if a[i]["judged_correct"]) / len(ids)
        by_category[c] = {
            "n": len(ids), "before": round(bb, 4), "after": round(aa, 4),
            "delta": round(aa - bb, 4),
            "improved": sum(1 for i in ids
                            if not b[i]["judged_correct"] and a[i]["judged_correct"]),
            "regressed": sum(1 for i in ids
                             if b[i]["judged_correct"] and not a[i]["judged_correct"]),
        }

    f1_delta = (
        sum(a[i]["item_f1"] for i in shared) - sum(b[i]["item_f1"] for i in shared)
    ) / len(shared) if shared else 0.0

    boot = paired_bootstrap(
        {i: b[i]["judged_correct"] for i in shared},
        {i: a[i]["judged_correct"] for i in shared},
    )

    # Failure migration: which labels were recovered, which newly appeared.
    before_labels = {i: label_failure(b[i]) for i in shared}
    after_labels = {i: label_failure(a[i]) for i in shared}
    recovered: dict[str, int] = {}
    introduced: dict[str, int] = {}
    for i in shared:
        bl, al = before_labels[i], after_labels[i]
        if bl and not al:
            recovered[bl] = recovered.get(bl, 0) + 1
        elif al and not bl:
            introduced[al] = introduced.get(al, 0) + 1

    largest = max(by_category.items(), key=lambda kv: kv[1]["delta"], default=(None, None))

    return {
        "n_paired": len(shared),
        "n_before_only": len(set(b) - set(a)),
        "n_after_only": len(set(a) - set(b)),
        "before_accuracy": round(before_acc, 4),
        "after_accuracy": round(after_acc, 4),
        "accuracy_delta": round(after_acc - before_acc, 4),
        "item_f1_delta": round(f1_delta, 4),
        "improved": improved,
        "regressed": regressed,
        "n_improved": len(improved),
        "n_regressed": len(regressed),
        "by_category": by_category,
        "largest_category_gain": (
            {"category": largest[0], **largest[1]} if largest[0] else None
        ),
        "bootstrap": boot,
        "failure_migration": {
            "recovered": dict(sorted(recovered.items(), key=lambda kv: -kv[1])),
            "introduced": dict(sorted(introduced.items(), key=lambda kv: -kv[1])),
        },
    }


def acceptance(summary_after: dict[str, Any], cmp: dict[str, Any],
               tests_passed: bool, integrity_ok: bool) -> dict[str, Any]:
    """The gate. All four conditions, and the reason if any fails."""
    reasons = []
    if not tests_passed:
        reasons.append("full test suite did not pass")
    if not integrity_ok:
        reasons.append("integrity violation")
    if not summary_after.get("valid"):
        reasons.append(f"run invalid: {summary_after.get('validity_note')}")
    if cmp["accuracy_delta"] <= 0:
        reasons.append(
            f"validation accuracy did not increase "
            f"({cmp['before_accuracy']:.3f} -> {cmp['after_accuracy']:.3f})"
        )
    # Guard the specific failure mode of "improved by scoring fewer questions".
    if cmp["n_after_only"] or cmp["n_before_only"]:
        reasons.append(
            f"question set changed: {cmp['n_before_only']} dropped, "
            f"{cmp['n_after_only']} added"
        )
    return {"accepted": not reasons, "reasons": reasons}


def insights(summary: dict[str, Any], tax: dict[str, Any],
             cmp: dict[str, Any] | None = None) -> list[str]:
    """Plain-language observations the next diagnosis should act on."""
    out: list[str] = []
    overall = summary.get("overall", {})

    if tax["total_failures"]:
        top = tax["dominant"]
        share = tax["shares"].get(top, 0)
        out.append(
            f"Dominant failure is **{top}** at {tax['dominant_n']}/"
            f"{tax['total_failures']} failures ({share:.0%})."
        )
        worst_cat = min(
            summary.get("by_category", {}).items(),
            key=lambda kv: kv[1].get("judged_accuracy", 1), default=(None, None),
        )
        if worst_cat[0]:
            out.append(
                f"Weakest category is **{worst_cat[0]}** at "
                f"{worst_cat[1]['judged_accuracy']:.1%}."
            )

    if overall.get("evidence_recall") is not None:
        er = overall["evidence_recall"]
        out.append(
            f"Evidence was present for {er:.0%} of questions, so at most "
            f"{1 - er:.0%} of the set is bounded by retrieval rather than generation."
        )
    if overall.get("false_abstention_rate"):
        out.append(
            f"False abstention on {overall['false_abstention_rate']:.0%} — the model "
            "declined despite having the evidence."
        )
    if overall.get("unsupported_item_rate"):
        out.append(
            f"Unsupported items at {overall['unsupported_item_rate']:.0%} of emitted "
            "items — invention, not omission."
        )
    if overall.get("item_recall") is not None and overall.get("item_precision") is not None:
        r, p = overall["item_recall"], overall["item_precision"]
        if r < p - 0.05:
            out.append(
                f"Item recall ({r:.2f}) trails precision ({p:.2f}): the model omits "
                "gold items more than it invents them — an incomplete-list problem."
            )
        elif p < r - 0.05:
            out.append(
                f"Item precision ({p:.2f}) trails recall ({r:.2f}): over-inclusion."
            )

    if cmp:
        b = cmp["bootstrap"]
        out.append(
            f"Paired delta {cmp['accuracy_delta']:+.3f} "
            f"(95% CI {b['ci_low']:+.3f}..{b['ci_high']:+.3f}, "
            f"{'significant' if b['significant'] else 'NOT significant'}); "
            f"{cmp['n_improved']} improved, {cmp['n_regressed']} regressed."
        )
        if cmp["failure_migration"]["introduced"]:
            out.append(
                "New failures introduced: "
                + ", ".join(f"{k}×{v}" for k, v in
                            cmp["failure_migration"]["introduced"].items())
            )
    return out
