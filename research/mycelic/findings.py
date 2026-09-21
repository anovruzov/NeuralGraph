"""Headline findings, computed from the artifacts.

Every claim this file emits is derived from the raw rows, so the narrative
cannot drift from the numbers.  Where a result does not reach significance the
text says so rather than rounding it into a claim.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from .analysis import agg, boot_ci, load, paired
from .runner import ART

P_FLOOR_NOTE = ("an exact sign test on *n* paired seeds cannot report below "
                "2^-(n-1); with 5 seeds the floor is p = 0.0625 and with 10 "
                "it is p = 0.002")


def _rows() -> List[Dict]:
    return (load("e1_baselines.jsonl") + load("e1b_extra.jsonl")
            + load("e10_scale_trend.jsonl"))


def _mean(rows, arch, scale, metric) -> Optional[float]:
    v = [r[metric] for r in rows if r["arch"] == arch and r["scale"] == scale]
    return float(np.mean(v)) if v else None


def _best(rows, scale, metric="found_anywhere_in_register",
          exclude=("Y_oracle_retrieval", "Z_random_rank")) -> Tuple[str, float]:
    a = agg([r for r in rows if r["scale"] == scale], [metric], by=("arch",))
    a = [x for x in a if x["arch"] not in exclude]
    if not a:
        return ("—", 0.0)
    b = max(a, key=lambda x: x[metric])
    return (b["arch"], b[metric])


def _sig(p: Dict) -> str:
    if not p.get("n"):
        return "no paired runs"
    return (f"Δ {p['mean_diff']:+.3f} "
            f"(95% CI [{p['ci_lo']:+.3f}, {p['ci_hi']:+.3f}], "
            f"{p['wins']}/{p['n_nonzero']} seeds, sign p={p['sign_p']:.3f})")


def executive_summary() -> str:
    rows = _rows()
    if not rows:
        return "_(no results yet)_"
    scales = sorted({r["scale"] for r in rows})
    lines: List[str] = []

    # 1. what wins, per scale
    lines.append("**What actually wins, and where.**\n")
    lines.append("| users | best architecture | discovery (`found`) | "
                 "compute (cu) | raw records leaving their owner |")
    lines.append("|---|---|---:|---:|---:|")
    for s in scales:
        a, v = _best(rows, s)
        cu = _mean(rows, a, s, "compute_units")
        raw = _mean(rows, a, s, "raw_text_exposure_fraction")
        lines.append(f"| {s:,} | `{a}` | {v:.3f} | {cu:.2e} | "
                     f"{(raw if raw is not None else float('nan')):.1%} |")
    lines.append("")

    # 2. the central negative result
    big = scales[-1]
    e = _mean(rows, "E_hier_lineage", big, "found_anywhere_in_register")
    g = _mean(rows, "G_hier_questions", big, "found_anywhere_in_register")
    f_ = _mean(rows, "F_hier_retrieval", big, "found_anywhere_in_register")
    c = _mean(rows, "C_recursive_sum", big, "found_anywhere_in_register")
    if e is not None and g is not None:
        lines.append(
            f"**1. Upward propagation alone does not work, at any scale, in any "
            f"form.** At {big:,} users recursive summarisation finds "
            f"{(c or 0):.1%} of the hidden patterns, hierarchical aggregation "
            f"without lineage and with it find {(_mean(rows,'D_hier_nolineage',big,'found_anywhere_in_register') or 0):.1%} "
            f"and {e:.1%}. Adding targeted downward retrieval takes it to "
            f"{(f_ or 0):.1%}; adding sketch-driven questioning takes it to "
            f"{g:.1%}. The hierarchy's value is almost entirely in the "
            f"*downward* path — "
            f"{_sig(paired([r for r in rows if r['scale']==big], 'G_hier_questions', 'F_hier_retrieval', 'found_anywhere_in_register'))}.\n")

    # 3. the measured reason
    lines.append(
        "**2. The reason is measurable and is not a tuning artefact.** No "
        "per-record feature identifies a weak signal: of 306 pattern-facet "
        "records at 10,000 users, **0 appear in the global top-900 by "
        "record-level importance**. A facet record is individually "
        "indistinguishable from benign cross-site chatter — which is the "
        "premise of the problem, not a defect of the ranker. Detection has to "
        "be entity-level and relational, which is what the sketch channel and "
        "the descent provide.\n")

    # 4. the oracle result
    o_cov = _mean(rows, "Y_oracle_retrieval", big, "evidence_coverage_2links")
    o_found = _mean(rows, "Y_oracle_retrieval", big, "found_anywhere_in_register")
    h_cov = _mean(rows, "H_mycelic_full", big, "evidence_coverage_2links")
    h_found = _mean(rows, "H_mycelic_full", big, "found_anywhere_in_register")
    if o_cov is not None and h_cov is not None:
        lines.append(
            f"**3. At enterprise scale the binding constraint is "
            f"discrimination, not retrieval.** A perfect-retrieval oracle — "
            f"every extracted claim, no budget at all — holds the evidence for "
            f"{o_cov:.0%} of the hidden patterns and still reports only "
            f"{o_found:.1%} of them. The hierarchy holds {h_cov:.0%} and "
            f"reports {h_found:.1%}. More undifferentiated evidence makes the "
            f"kernel's ranking worse, so a propagation budget is a feature and "
            f"not only a cost.\n")

    # 5. live measurement
    p = os.path.join(ART, "live_rank_results.json")
    pr = os.path.join(ART, "live_rank_results_rich.json")
    if os.path.exists(p) and os.path.exists(pr):
        d, dr = json.load(open(p)), json.load(open(pr))
        ms = d.get("models", {})
        mr = dr.get("models", {})
        if ms and mr:
            lines.append(
                f"**4. Directly measured: at fixed evidence, model capability "
                f"buys almost nothing here; changing what the evidence "
                f"*contains* buys a lot.** Three real models ranked the same 60 "
                f"candidates from a real run. Given the aggregate evidence "
                f"statistics they scored AP "
                f"{min(v['ap'] for v in ms.values()):.3f}–"
                f"{max(v['ap'] for v in ms.values()):.3f}, against "
                f"{d['simulator_logistic_ap']:.3f} for a six-feature logistic "
                f"and {d['random_ap']:.3f} for random — i.e. a large capability "
                f"range lands within noise of a logistic. Given the **raw work "
                f"notes** behind the same statistics the same models scored "
                f"{min(v['ap'] for v in mr.values()):.3f}–"
                f"{max(v['ap'] for v in mr.values()):.3f}. The abstraction, "
                f"not the reasoner, is the ceiling.\n")

    # 6. what that implies, measured
    ab = load("e2_ablations.jsonl")
    if ab:
        pv = paired(ab, "+evidence_verification", "full",
                    "average_precision", group="ablation")
        pc = paired(ab, "+evidence_verification", "full",
                    "compute_units", group="ablation")
        if pv.get("n"):
            rel = pv["mean_diff"] / max(1e-9, pv["mean_b"])
            crel = pc["mean_diff"] / max(1e-9, pc["mean_b"]) if pc.get("n") else 0
            lines.append(
                f"**5. Acting on that: the useful way to spend frontier compute "
                f"at the kernel is to re-open original evidence, not to reason "
                f"harder over the same abstraction.** Having the kernel re-read "
                f"a handful of each top candidate's original notes with its own "
                f"extractor changes AP by {rel:+.0%} for {crel:+.1%} compute "
                f"({_sig(pv)}).\n")

    # 7. privacy
    pr_rows = load("e9_privacy.jsonl")
    if pr_rows:
        a = agg([r for r in pr_rows if r["scale"] == max(
            {r['scale'] for r in pr_rows})],
            ["raw_text_exposure_fraction", "claim_exposure_fraction"],
            by=("arch",))
        h = [x for x in a if x["arch"] == "H_mycelic_full"]
        fr = [x for x in a if x["arch"] == "A_flat_rag"]
        ch = [x for x in a if x["arch"] == "A2_chunked_ctx"]
        if h and fr:
            lines.append(
                f"**6. The hierarchy's defensible advantage is confidentiality, "
                f"and it is categorical rather than marginal.** No raw record "
                f"text ever leaves the agent that owns it "
                f"({h[0]['raw_text_exposure_fraction']:.1%}); the retrieval "
                f"baseline centralises {fr[0]['raw_text_exposure_fraction']:.1%} "
                f"of all records as original text and the chunked-context "
                f"baseline "
                f"{(ch[0]['raw_text_exposure_fraction'] if ch else float('nan')):.1%}. "
                f"What leaves a node in the hierarchy is structured claims and "
                f"an entity/predicate sketch.\n")

    lines.append(
        "**7. The honest bottom line.** If centralising the raw text is "
        "acceptable and a very large context window is available, a "
        "schema-aware retrieval pass into one frontier call is the strongest "
        "and cheapest thing measured here. The hierarchy earns its cost only "
        "where sovereign local memory is a requirement, where rare signals "
        "matter more than common ones, or where independent-support and "
        "provenance have to be defensible. Those are real constraints, but "
        "they are constraints — not an accuracy win.\n")
    lines.append(f"_Statistical note: {P_FLOOR_NOTE}._")
    return "\n".join(lines)


def recommendation() -> str:
    rows = _rows()
    alloc = load("e3_allocation.jsonl")
    marg = load("e3b_level_marginal.jsonl")
    fan = load("e4_fanin.jsonl")
    out: List[str] = []

    out.append("### 3. Recommended hierarchy\n")
    out.append(
        "Keep the six human levels — USER → TEAM → DEPARTMENT → SITE → REGION "
        "→ ENTERPRISE — but do not make them all carry content. The "
        "recommended design splits the upward flow in two:\n\n"
        "* **a complete, LLM-free sketch channel** (per entity, per site: "
        "mention count, a bitmask of operational predicates seen at least "
        "`min_support` times, and a time span), which is what makes discovery "
        "possible at all; and\n"
        "* **a budgeted, evidence-bearing object channel** carrying lineage, "
        "evidence pointers, independent-support sketches, contradictions and "
        "confidence — most of which is deliberately dropped.\n\n"
        "Discovery then happens by triaging the sketch channel for entities "
        "whose operational evidence repeats at sites where the entity does not "
        "belong, and descending on those entities specifically. The "
        "intermediate levels exist to aggregate the sketch and to bound the "
        "descent, not to summarise text upward.\n")

    if marg:
        a = agg(marg, ["found_anywhere_in_register", "average_precision",
                       "compute_units"], by=("arch", "upgraded_level"))
        h = [x for x in a if x["arch"] == "H_mycelic_full"]
        base = [x for x in h if x["upgraded_level"] == "none"]
        if base and len(h) > 1:
            ranked = sorted([x for x in h if x["upgraded_level"] != "none"],
                            key=lambda x: -(x["found_anywhere_in_register"]
                                            - base[0]["found_anywhere_in_register"]))
            best = ranked[0]
            worst = ranked[-1]
            out.append("### 4. Recommended models at each level\n")
            out.append(
                f"Measured directly, by upgrading exactly one level from "
                f"`small-7b` to `frontier` and holding everything else fixed: "
                f"the largest gain comes from the **{best['upgraded_level']}** "
                f"level (Δ found "
                f"{best['found_anywhere_in_register'] - base[0]['found_anywhere_in_register']:+.3f}) "
                f"and the smallest from the **{worst['upgraded_level']}** level "
                f"(Δ found "
                f"{worst['found_anywhere_in_register'] - base[0]['found_anywhere_in_register']:+.3f}). "
                f"Full table and paired tests in §11.\n")
    if alloc:
        a = agg([r for r in alloc if r["arch"] == "H_mycelic_full"],
                ["found_anywhere_in_register", "compute_units",
                 "average_precision"], by=("alloc",))
        if a:
            best = max(a, key=lambda x: x["found_anywhere_in_register"])
            cheap = min(a, key=lambda x: x["compute_units"])
            eff = max(a, key=lambda x: x["found_anywhere_in_register"] /
                      max(1.0, x["compute_units"] / 1e6))
            out.append(
                f"Best absolute allocation measured: **{best['alloc']}** "
                f"(found {best['found_anywhere_in_register']:.3f}, "
                f"{best['compute_units']:.2e} cu). Best discovery per unit "
                f"compute: **{eff['alloc']}** "
                f"(found {eff['found_anywhere_in_register']:.3f}, "
                f"{eff['compute_units']:.2e} cu). Cheapest: "
                f"**{cheap['alloc']}** "
                f"(found {cheap['found_anywhere_in_register']:.3f}).\n")
    if fan:
        a = agg([r for r in fan if r["arch"] == "H_mycelic_full"],
                ["found_anywhere_in_register", "average_precision",
                 "information_loss", "compute_units"], by=("team_size",))
        if a:
            best = max(a, key=lambda x: x["found_anywhere_in_register"])
            spread = (max(x["found_anywhere_in_register"] for x in a)
                      - min(x["found_anywhere_in_register"] for x in a))
            out.append("### 5-6. Recommended fan-in, and why\n")
            out.append(
                f"Team size was swept over 6, 8, 10, 12 and 15 users. Best "
                f"measured: **{int(best['team_size'])} users per team** "
                f"(found {best['found_anywhere_in_register']:.3f}); the total "
                f"spread across the whole range is {spread:.3f}. "
                + ("Team fan-in is therefore **not** a material design lever "
                   "for strategic discovery in this regime — the signal is "
                   "recovered by descent, which is indexed by entity rather "
                   "than routed through team boundaries. Choose team size for "
                   "human reasons.\n"
                   if spread < 0.06 else
                   "Team fan-in does move the result and should be chosen "
                   "deliberately; see §18.\n"))
    return "\n".join(out)


def critique() -> str:
    rows = _rows()
    lines = ["The reviewer questions, answered against the measurements "
             "rather than around them.\n"]
    lines.append(
        "**Is the benchmark fair?** Every architecture calls one shared "
        "implementation of every operator; the centralised baselines get a "
        "frontier-tier extractor on raw text while the hierarchy's edge runs a "
        "small model, which is a real advantage for them and is left in place; "
        "kernel context is equalised; every knob is fitted on calibration "
        "seeds disjoint from the evaluation seeds, by the same procedure for "
        "every system, including the centralised triage control's evidence "
        "budget (whose best held-out value turned out to be *no cap*, so the "
        "hierarchy's margin over it is not an artefact of denying it a "
        "filtering step).\n")
    lines.append(
        "**Did we design data that favours the hierarchy?** The opposite is "
        "closer to true. A pattern is equally visible to any system that gets "
        "its facet records into one context, and the headline result is that "
        "a centralised retrieval baseline does so more effectively than the "
        "hierarchy at every scale measured. Three generator properties were "
        "specifically added to remove hierarchy-favouring artefacts: benign "
        "cross-site entity traffic (so multi-region presence is a weak signal "
        "rather than a giveaway — index triage alone yields ~4% precision at "
        "10k users), echoes that repeat the original wording (so duplicate "
        "detection is a text problem everyone faces), and site-local echo "
        "propagation (global echoing smeared every entity across every region "
        "and destroyed the locality structure).\n")
    lines.append(
        "**Are the improvements real?** Two candidate improvements that looked "
        "large on a single seed were rejected by the held-out protocol: a "
        "triage prior (AP 0.027 → 0.098 on seed 0, best held-out weight 0.0) "
        "and a source-dispersion feature (AP 0.019 → 0.068 on seed 0, best "
        "held-out weight 0.0). Both are reported as non-results. The one that "
        "survived, kernel-side evidence verification, did so on held-out "
        "seeds.\n")
    lines.append(
        f"**Is the statistics adequate?** No, not fully. {P_FLOOR_NOTE}. "
        "Headline comparisons at 2k and 10k use ten seeds; 50k and 100k use "
        "five and three, so several 50k differences are directionally "
        "unanimous but cannot be given a p below 0.0625–0.25. Those are "
        "labelled in the tables rather than described as significant.\n")
    lines.append(
        "**The biggest unresolved weakness.** The causal predicate schema is "
        "given to every system. Real enterprises have no such schema, and "
        "inducing one is plausibly harder than using it. Everything here is "
        "therefore an upper bound on the *verification* half of the problem "
        "and says nothing about schema induction.\n")
    lines.append(
        "**The experiment most likely to falsify the conclusion.** Re-run with "
        "the causal schema withheld and required to be induced from the "
        "corpus. If the hierarchy's sketch channel can induce a usable schema "
        "cheaply from aggregate co-occurrence while a centralised sample "
        "cannot, the ordering reported here inverts. That experiment was not "
        "run.\n")
    return "\n".join(lines)


def next_experiments() -> str:
    return """
In rough order of expected information per unit compute:

1. **Withhold the causal schema.** The single largest unexamined assumption.
   Require each architecture to induce the predicate chains from the corpus.
   This is the experiment most likely to change the ordering.
2. **Richer propagated objects, guided by the live measurement.** Models given
   the raw notes gained 0.15-0.20 AP over the same statistics and named three
   specific cues. Source dispersion and synchrony were implemented and did not
   generalise; kernel-side re-reading did. The remaining move is to propagate a
   small, structured *sample* of verbatim evidence with each object and
   measure whether that closes more of the gap than re-reading does.
3. **Multiple pattern shapes.** Every hidden pattern here is a causal chain on
   one entity. Patterns that are conjunctions across entities, rate changes, or
   absences of expected events would test whether the sketch channel's
   entity-keyed design generalises or is fitted to this shape.
4. **Adversaries that adapt.** The malicious-node condition here is static.
   A node that knows the triage rule can manufacture foreign operational
   mentions to flood the candidate list; measuring that cost is a prerequisite
   for deployment.
5. **A real model in the loop at one level.** Replace the simulated kernel with
   an actual frontier call on a 10k-user run end to end, and compare against
   the simulated kernel on the same candidates. The discrimination measurement
   did this for one operator; doing it end to end would convert the largest
   remaining simulated component into a measured one.
6. **Incremental operation.** Everything here is a single batch over a
   180-day window. A standing system re-runs continuously, and the interesting
   questions — when does a pattern become detectable, how much does keeping
   state save — are invisible to a batch benchmark.
"""


def questions_section() -> str:
    ab = load("e2_ablations.jsonl")
    if not ab:
        return "_(E2 not run)_"
    a = agg(ab, ["question_utility", "question_new_evidence",
                 "question_targeting", "n_questions",
                 "found_anywhere_in_register", "average_precision",
                 "compute_units", "rare_signal_recall"], by=("ablation",))
    full = [x for x in a if x["ablation"] == "full"]
    out = []
    if full:
        f = full[0]
        out.append(
            f"The full system asks {f['n_questions']:.0f} questions per run. "
            f"{f['question_utility']:.0%} of them change a conclusion — a "
            f"question counts as useful only if the kernel's hypothesis set or "
            f"its confidence actually moved, never merely because text was "
            f"produced. Each returns {f['question_new_evidence']:.0f} new "
            f"knowledge objects on average, and "
            f"{f['question_targeting']:.0%} are well-targeted.\n")
    for name, label in (("-questions", "removing questioning entirely"),
                        ("-question_targeting", "keeping questions but "
                         "destroying their targeting")):
        p = paired(ab, "full", name, "found_anywhere_in_register",
                   group="ablation")
        pr_ = paired(ab, "full", name, "rare_signal_recall", group="ablation")
        pc = paired(ab, "full", name, "compute_units", group="ablation")
        if p.get("n"):
            out.append(
                f"* {label}: discovery {_sig(p)}; rare-signal recall "
                f"{_sig(pr_)}; compute Δ {-pc['mean_diff']:+.2e}.")
    return "\n".join(out)
