"""Root-cause analysis of the v2 (abstention-gate) round. Read-only.

Does not modify prompts, graders, splits, caches or predictions. It reads the
two caches, pairs them by question id, and separates what actually changed from
what only appeared to change.

The central discipline: a byte-identical or semantically equivalent answer that
receives a different verdict is judge noise. Counting it as an improvement or a
regression attributes model behaviour to a coin flip.
"""

from __future__ import annotations

import json
import math
import random
import re
import statistics
from pathlib import Path
from typing import Any

from evaluation.diagnose_accuracy import answer_items, evidence_present, retrieved_text
from evaluation.improvement_loop.batch import completed_ids, rebuild_rows
from evaluation.improvement_loop.corpus import load_corpus
from evaluation.improvement_loop.rejected_v2.prompts_v2 import order_evidence
from evaluation.improvement_loop.rounds import load_eval_sets
from evaluation.improvement_loop.runner import ResponseCache, RunConfig

OUT = Path("evaluation/artifacts/locomo_loop")

CLASSES = (
    "SAME_OUTPUT_SAME_VERDICT",
    "SAME_OUTPUT_JUDGE_FLIP",
    "SEMANTICALLY_EQUIVALENT_OUTPUT_JUDGE_FLIP",
    "REAL_GENERATION_CHANGE",
)


def norm(text: str) -> str:
    """Normalise for semantic-equivalence comparison: case, punctuation, articles."""
    t = (text or "").lower()
    t = re.sub(r"\b(a|an|the)\b", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(t.split())


def equivalent(a_items: list[str], b_items: list[str]) -> bool:
    return sorted(norm(i) for i in a_items) == sorted(norm(i) for i in b_items)


def classify_pair(before: dict[str, Any], after: dict[str, Any]) -> str:
    same_bytes = before["items"] == after["items"]
    same_meaning = equivalent(before["items"], after["items"])
    same_verdict = before["judged_correct"] == after["judged_correct"]
    if same_bytes and same_verdict:
        return "SAME_OUTPUT_SAME_VERDICT"
    if same_bytes and not same_verdict:
        return "SAME_OUTPUT_JUDGE_FLIP"
    if same_meaning and not same_verdict:
        return "SEMANTICALLY_EQUIVALENT_OUTPUT_JUDGE_FLIP"
    if same_meaning and same_verdict:
        return "SAME_OUTPUT_SAME_VERDICT"
    return "REAL_GENERATION_CHANGE"


def mcnemar_exact(improved: int, regressed: int) -> float:
    """Two-sided exact McNemar (binomial on discordant pairs)."""
    n = improved + regressed
    if n == 0:
        return 1.0
    k = min(improved, regressed)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def paired_bootstrap(deltas: list[int], iterations: int = 10000,
                     seed: int = 20260825) -> tuple[float, float]:
    if not deltas:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(deltas)
    samples = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations)
    )
    return (round(samples[int(0.025 * iterations)], 4),
            round(samples[int(0.975 * iterations) - 1], 4))


def item_support(item: str, record: dict[str, Any]) -> str:
    """Where, if anywhere, is this item supported?"""
    hay = retrieved_text(record).lower()
    n = norm(item)
    if not n:
        return "empty"
    if n in norm(hay):
        return "supported_by_retrieved_evidence"
    tokens = [t for t in n.split() if len(t) > 3]
    if tokens and all(t in hay for t in tokens):
        return "supported_by_retrieved_evidence_tokenwise"
    return "unsupported_by_retrieved_evidence"


def root_cause(before: dict[str, Any], after: dict[str, Any],
               record: dict[str, Any], klass: str) -> str:
    if klass in ("SAME_OUTPUT_JUDGE_FLIP",
                 "SEMANTICALLY_EQUIVALENT_OUTPUT_JUDGE_FLIP"):
        return "judge_nondeterminism"
    if klass == "SAME_OUTPUT_SAME_VERDICT":
        return "no_change"
    gold_n = len(answer_items(str(record.get("gold_answer", ""))))
    if after.get("not_in_evidence") and not before.get("not_in_evidence"):
        return "gate_introduced_abstention"
    if before.get("not_in_evidence") and not after.get("not_in_evidence"):
        return ("gate_recovered_abstention_correctly" if after["judged_correct"]
                else "gate_converted_abstention_to_wrong_answer")
    if len(after["items"]) < len(before["items"]) and gold_n > 1:
        return "under_listing_lost_a_gold_item"
    unsupported = [i for i in after["items"]
                   if item_support(i, record).startswith("unsupported")]
    if unsupported and not after["judged_correct"]:
        return "unsupported_item_introduced"
    if after["judged_correct"] and not before["judged_correct"]:
        return "genuine_improvement"
    if before["judged_correct"] and not after["judged_correct"]:
        return "genuine_regression_answer_changed"
    return "changed_but_verdict_unmoved"


def analyse() -> dict[str, Any]:
    records = load_corpus()
    by_id = {int(r["id"]): r for r in records}
    es = load_eval_sets(OUT / "eval_sets.json")["validation"]
    c1 = RunConfig()
    c2 = RunConfig(prompt_version="v2-abstention-gate")
    cb = ResponseCache(OUT / "cache" / "validation.json")
    ca = ResponseCache(OUT / "cache" / "validation_round1.json")

    db, _ = completed_ids(cb, es.question_ids, by_id, c1)
    da, _ = completed_ids(ca, es.question_ids, by_id, c2)
    rb = {r["id"]: r for r in rebuild_rows(cb, db, by_id, c1)}
    ra = {r["id"]: r for r in rebuild_rows(ca, da, by_id, c2)}
    shared = sorted(set(rb) & set(ra))

    rows: list[dict[str, Any]] = []
    counts = {k: 0 for k in CLASSES}
    for qid in shared:
        b, a = rb[qid], ra[qid]
        rec = by_id[qid]
        klass = classify_pair(b, a)
        counts[klass] += 1
        strict, lenient = evidence_present(rec)
        rows.append({
            "id": qid,
            "category": b["category"],
            "class": klass,
            "root_cause": root_cause(b, a, rec, klass),
            "question": rec.get("question"),
            "gold_answer": rec.get("gold_answer"),
            "gold_item_count": len(answer_items(str(rec.get("gold_answer", "")))),
            "v1_items": b["items"],
            "v2_items": a["items"],
            "v1_correct": b["judged_correct"],
            "v2_correct": a["judged_correct"],
            "v1_abstained": bool(b.get("not_in_evidence")),
            "v2_abstained": bool(a.get("not_in_evidence")),
            "v1_item_f1": b["item_f1"],
            "v2_item_f1": a["item_f1"],
            "v1_unsupported": b.get("unsupported_items", 0),
            "v2_unsupported": a.get("unsupported_items", 0),
            "v2_cited_evidence": a.get("cited_evidence", []),
            "evidence_lenient": lenient,
            "evidence_strict": strict,
            "v2_item_support": [
                {"item": i, "support": item_support(i, rec)} for i in a["items"]
            ],
        })

    # Raw delta
    n = len(shared)
    bc = sum(1 for q in shared if rb[q]["judged_correct"])
    ac = sum(1 for q in shared if ra[q]["judged_correct"])
    improved = [q for q in shared
                if not rb[q]["judged_correct"] and ra[q]["judged_correct"]]
    regressed = [q for q in shared
                 if rb[q]["judged_correct"] and not ra[q]["judged_correct"]]

    # Noise-corrected: consistently adjudicate identical/equivalent outputs by
    # holding the verdict fixed at the v1 value, so only real output changes move
    # the score.
    noise_ids = {r["id"] for r in rows
                 if r["class"] in ("SAME_OUTPUT_JUDGE_FLIP",
                                   "SEMANTICALLY_EQUIVALENT_OUTPUT_JUDGE_FLIP")}
    corrected_after = sum(
        1 for q in shared
        if (rb[q]["judged_correct"] if q in noise_ids else ra[q]["judged_correct"])
    )
    real_improved = [q for q in improved if q not in noise_ids]
    real_regressed = [q for q in regressed if q not in noise_ids]

    deltas = [int(ra[q]["judged_correct"]) - int(rb[q]["judged_correct"]) for q in shared]
    ci_low, ci_high = paired_bootstrap(deltas)

    by_cat: dict[str, Any] = {}
    for cat in sorted({rb[q]["category"] for q in shared}):
        ids = [q for q in shared if rb[q]["category"] == cat]
        x = sum(1 for q in ids if rb[q]["judged_correct"])
        y = sum(1 for q in ids if ra[q]["judged_correct"])
        by_cat[cat] = {
            "n": len(ids), "v1": x, "v2": y,
            "delta": round((y - x) / len(ids), 4),
            "improved": [q for q in ids if q in improved],
            "regressed": [q for q in ids if q in regressed],
        }

    # Temporal mechanism (section 7)
    temporal = [r for r in rows if r["category"] == "temporal"]
    tem = {
        "n": len(temporal),
        "v1_abstentions": sum(1 for r in temporal if r["v1_abstained"]),
        "v2_abstentions": sum(1 for r in temporal if r["v2_abstained"]),
        "false_abstentions_recovered": sum(
            1 for r in temporal
            if r["v1_abstained"] and not r["v2_abstained"] and r["v2_correct"]),
        "abstention_to_wrong_answer": sum(
            1 for r in temporal
            if r["v1_abstained"] and not r["v2_abstained"] and not r["v2_correct"]),
        "correct_abstentions_broken": sum(
            1 for r in temporal
            if r["v1_abstained"] and r["v1_correct"] and not r["v2_abstained"]),
        "new_abstentions_introduced": sum(
            1 for r in temporal if not r["v1_abstained"] and r["v2_abstained"]),
        "unsupported_delta": (sum(r["v2_unsupported"] for r in temporal)
                              - sum(r["v1_unsupported"] for r in temporal)),
    }

    # Evidence reordering exposure (section 5)
    reorder = {"changed_order": 0, "changed_set": 0, "rank1_displacement": []}
    for qid in shared:
        rec = by_id[qid]
        orig = [m.get("text", "") for m in (rec.get("retrieved_memories") or ())
                if isinstance(m, dict)]
        if not orig:
            continue
        new = [m["text"] for m in order_evidence(rec)]
        if orig != new:
            reorder["changed_order"] += 1
        if sorted(orig) != sorted(new):
            reorder["changed_set"] += 1
        reorder["rank1_displacement"].append(new.index(orig[0]) + 1)

    return {
        "paired_rows": n,
        "raw": {
            "v1_correct": bc, "v2_correct": ac,
            "v1_accuracy": round(bc / n, 4) if n else 0,
            "v2_accuracy": round(ac / n, 4) if n else 0,
            "delta": round((ac - bc) / n, 4) if n else 0,
            "improved": improved, "regressed": regressed,
        },
        "noise_corrected": {
            "v2_correct": corrected_after,
            "v2_accuracy": round(corrected_after / n, 4) if n else 0,
            "delta": round((corrected_after - bc) / n, 4) if n else 0,
            "real_improved": real_improved,
            "real_regressed": real_regressed,
            "judge_flip_rows": sorted(noise_ids),
        },
        "class_counts": counts,
        "statistics": {
            "mcnemar_exact_p_raw": round(mcnemar_exact(len(improved), len(regressed)), 4),
            "mcnemar_exact_p_corrected": round(
                mcnemar_exact(len(real_improved), len(real_regressed)), 4),
            "bootstrap_ci_95": [ci_low, ci_high],
            "significant": ci_low > 0 or ci_high < 0,
        },
        "by_category": by_cat,
        "item_metrics": {
            m: {
                "v1": round(statistics.mean(rb[q][m] for q in shared), 4),
                "v2": round(statistics.mean(ra[q][m] for q in shared), 4),
                "delta": round(statistics.mean(ra[q][m] for q in shared)
                               - statistics.mean(rb[q][m] for q in shared), 4),
            } for m in ("item_f1", "item_recall", "item_precision")
        },
        "abstentions": {
            "v1": sum(1 for q in shared if rb[q].get("not_in_evidence")),
            "v2": sum(1 for q in shared if ra[q].get("not_in_evidence")),
        },
        "unsupported_items": {
            "v1": sum(rb[q].get("unsupported_items", 0) for q in shared),
            "v2": sum(ra[q].get("unsupported_items", 0) for q in shared),
        },
        "temporal_mechanism": tem,
        "evidence_reordering": {
            "changed_order": reorder["changed_order"],
            "changed_set": reorder["changed_set"],
            "rank1_median_new_position": (
                statistics.median(reorder["rank1_displacement"])
                if reorder["rank1_displacement"] else None),
        },
        "rows": rows,
    }


def main() -> int:
    report = analyse()
    path = OUT / "v2_root_cause.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
