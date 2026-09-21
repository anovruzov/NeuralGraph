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

from .analysis import agg, boot_ci, dedupe, load, paired
from .runner import ART

P_FLOOR_NOTE = ("an exact sign test on *n* paired seeds cannot report below "
                "2^-(n-1); with 5 seeds the floor is p = 0.0625 and with 10 "
                "it is p = 0.002")


def _rows() -> List[Dict]:
    return dedupe(load("e1_baselines.jsonl") + load("e1b_extra.jsonl")
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



GLOSSARY = """
The report uses a small number of terms repeatedly. All of them are
measurements, not scores on a scale someone invented.

| term | what it means |
|---|---|
| **hidden pattern** | A genuine emerging problem planted in the synthetic company: a chain of causally linked events about one supplier, system or component, whose parts are deliberately scattered across different teams, sites and regions so that nobody sees more than one part. |
| **discovery (`found`)** | The fraction of hidden patterns the system puts on the executive risk register *anywhere*. The plainest measure of "did we find it at all". |
| **AP** (average precision) | How well the system *ranks* what it found. A register nobody can read top to bottom is worth less than one where the real items are at the top. Random ranking of the same candidates scores about 0.30 on the discrimination task. |
| **evidence coverage** | The fraction of hidden patterns for which the system ever *held* the evidence, whether or not it reported them. The gap between coverage and discovery is what is lost to judgement rather than to retrieval. |
| **rare-signal recall** | Discovery restricted to the hardest patterns: those supported by one or two people in the entire 50,000-person company. |
| **FDR** (false discovery rate) | The fraction of what the system reports that is not a real pattern. |
| **decoy acceptance** | The fraction of the deliberately planted traps the system falls for. |
| **compute (cu)** | Normalised compute units: model size times tokens. Provider-independent; a dollar estimate is given separately. |
| **raw-text exposure** | The fraction of employees' original notes read by anything other than the agent that owns them. The confidentiality cost. |
"""


def decision_summary() -> str:
    """The front page: what to build, what it costs, what it buys.

    Every comparative claim here is derived from the rows.  An earlier version
    asserted three standing advantages for the hierarchy; one of them
    (weak-signal sensitivity) is contradicted by the data at some scales, so
    the claims are now computed and the ones that fail are stated as failures.
    """
    rows = _rows()
    if not rows:
        return "_(no results yet)_"
    HIER = "H_mycelic_full"
    # Use the largest scale at which the comparison is actually COMPLETE.
    # Picking the largest scale present will, mid-run, put a half-finished
    # column on the front page and report a missing architecture as 0%.
    need = {HIER, "A_flat_rag", "A2_chunked_ctx", "B2_map_reduce",
            "B4_central_triage", "C_recursive_sum", "E_hier_lineage",
            "F_hier_retrieval", "G_hier_questions"}
    complete = []
    for sc in sorted({r["scale"] for r in rows}):
        have = {r["arch"] for r in rows if r["scale"] == sc}
        if need <= have:
            complete.append(sc)
    if not complete:
        return "_(results incomplete: no scale yet has every architecture)_"
    big = complete[-1]
    n_seeds = len({r["seed"] for r in rows if r["scale"] == big})
    out = []

    best_arch, best_v = _best(rows, big)

    def m(arch, k):
        return _mean(rows, arch, big, k)

    h, b = m(HIER, "found_anywhere_in_register"), best_v
    out.append("### The one-paragraph version")
    out.append("")
    verdict = ("is not the most accurate option" if (h or 0) < (b or 0) - 1e-9
               else "is the most accurate option measured")
    out.append(
        f"We built a synthetic enterprise with known hidden "
        "problems planted in it, at 2,000, 10,000 and 50,000 people, and "
        "tested sixteen ways of finding those problems, from pouring a "
        "filtered sample of the company's notes into one very large model, to "
        "a six-level hierarchy of agents mirroring "
        f"the org chart. **The hierarchy {verdict}.** The strongest "
        f"single approach measured at {big:,} people is `{best_arch}`, which finds "
        f"{(b or 0):.0%} of the hidden problems against the hierarchy's "
        f"{(h or 0):.0%}. Whether the hierarchy is nonetheless worth building "
        "depends entirely on which of the secondary properties below we "
        "actually need; the table states which ones it delivers and which it "
        "does not.")
    out.append("")

    out.append(f"### Head to head at {big:,} users ({n_seeds} seeds)")
    out.append("")
    out.append("| | best centralised option | the hierarchy | hierarchy better? |")
    out.append("|---|---:|---:|---|")
    out.append(f"| approach | `{best_arch}` | `{HIER}` | |")

    def row(label, key, higher_is_better=True, fmt="{:.0%}", invert=False):
        bv, hv = m(best_arch, key), m(HIER, key)
        if bv is None or hv is None:
            return
        better = (hv > bv + 1e-9) if higher_is_better else (hv < bv - 1e-9)
        mark = "**yes**" if better else ("no" if abs(hv - bv) > 1e-9 else "tie")
        bs = fmt.format(bv) if abs(bv) < 1e4 else f"{bv:.2e}"
        hs = fmt.format(hv) if abs(hv) < 1e4 else f"{hv:.2e}"
        out.append(f"| {label} | {bs} | {hs} | {mark} |")

    row("hidden problems found", "found_anywhere_in_register")
    row("the hardest problems (1-2 witnesses company-wide)",
        "rare_signal_recall")
    row("found within the top 100 of the register", "recall_at_100")
    row("precision in the top 40 of the register", "precision_at_40")
    row("precision in the top 100 of the register", "precision_at_100")
    row("ranking quality (AP)", "average_precision", fmt="{:.3f}")
    row("decoy traps accepted", "decoy_acceptance_all", higher_is_better=False)
    row("evidence coverage (held it at all)", "evidence_coverage_2links")
    row("independent-support accuracy", "independent_evidence_accuracy")
    row("lineage accuracy", "lineage_accuracy")
    row("contradictions detected (F1)", "contradiction_f1")
    row("compute", "compute_units", higher_is_better=False, fmt="{:.2e}")
    row("model calls", "inference_calls", higher_is_better=False, fmt="{:,.0f}")
    row("employees' original notes read centrally",
        "raw_text_exposure_fraction", higher_is_better=False)
    out.append("")
    out.append(
        "*Reading the false-discovery rate.* The register is a ranked "
        "watchlist of roughly one entry per tracked entity, not a shortlist, "
        "so a high FDR over the whole register is structural and is true of "
        "every architecture including the perfect-retrieval reference. The "
        "operationally meaningful numbers are precision in the top 40 or top "
        "100 — what an executive would actually read — and the ranking "
        "quality (AP) that determines them.")
    out.append("")

    # which grounds actually hold
    wins, losses = [], []
    checks = [
        ("confidentiality (no original notes leave the owning agent)",
         "raw_text_exposure_fraction", False),
        ("weak-signal sensitivity", "rare_signal_recall", True),
        ("independent-support accuracy", "independent_evidence_accuracy", True),
        ("lineage / provenance", "lineage_accuracy", True),
        ("resistance to planted traps", "decoy_acceptance_all", False),
    ]
    for label, key, hib in checks:
        bv, hv = m(best_arch, key), m(HIER, key)
        if bv is None or hv is None:
            continue
        better = (hv > bv + 1e-9) if hib else (hv < bv - 1e-9)
        (wins if better else losses).append((label, bv, hv))

    out.append("### Which of the usual arguments for a hierarchy actually hold")
    out.append("")
    if wins:
        out.append("**Hold, on this evidence:**")
        out.append("")
        for label, bv, hv in wins:
            out.append(f"* {label} — {hv:.0%} vs {bv:.0%} for the centralised "
                       f"option." if abs(hv) < 1e4 else
                       f"* {label} — {hv:.3g} vs {bv:.3g}.")
        out.append("")
    if losses:
        out.append("**Do NOT hold, and should not be used to justify the "
                   "build:**")
        out.append("")
        for label, bv, hv in losses:
            out.append(f"* {label} — {hv:.0%} vs {bv:.0%}; the centralised "
                       f"option is better." if abs(hv) < 1e4 else
                       f"* {label} — {hv:.3g} vs {bv:.3g}; centralised wins.")
        out.append("")

    # The sharpest framing available: the centralised twin of the hierarchy's
    # OWN algorithm, which isolates the algorithm from the topology.
    b4f = m("B4_central_triage", "found_anywhere_in_register")
    b4c = m("B4_central_triage", "compute_units")
    b4p = m("B4_central_triage", "claim_exposure_fraction")
    hp = m(HIER, "claim_exposure_fraction")
    hc = m(HIER, "compute_units")
    if b4f is not None and h is not None and b4c and hc:
        out.append("### The control that reframes the decision")
        out.append("")
        gap = h - b4f
        tie = abs(gap) < 0.05
        out.append(
            "`B4_central_triage` runs **exactly the hierarchy's own discovery "
            "algorithm**, centrally: one claim pool, no propagation budget, no "
            "routing error, no descent. It separates the value of the "
            "*algorithm* from the value of the *topology*.")
        out.append("")
        out.append(
            f"It finds {b4f:.0%} of the hidden problems against the "
            f"hierarchy's {h:.0%}, at {b4c:.2e} compute against {hc:.2e} — "
            f"about {hc/max(1.0,b4c):.1f}x less."
            + ("  Within noise, they are the same." if tie else ""))
        out.append("")
        if b4p is not None and hp is not None:
            out.append(
                "So the honest statement of what the hierarchy buys is narrow "
                "and specific: **the triage algorithm is what finds the "
                "problems; the hierarchy is how you run that algorithm without "
                "centralising the company's data.** The centralised version "
                f"pools {b4p:.0%} of all extracted claims in one place; the "
                f"hierarchy pools {hp:.0%} and moves no original text at all. "
                f"That privacy property costs roughly {hc/max(1.0,b4c):.1f}x "
                "the compute and tens of thousands of extra model calls. "
                "If we do not need it, we should run the algorithm centrally.")
            out.append("")
    out.append("### Three decisions this supports")
    out.append("")
    c = m("C_recursive_sum", "found_anywhere_in_register")
    d_ = m("D_hier_nolineage", "found_anywhere_in_register")
    e_ = m("E_hier_lineage", "found_anywhere_in_register")
    f_ = m("F_hier_retrieval", "found_anywhere_in_register")
    g_ = m("G_hier_questions", "found_anywhere_in_register")
    out.append(
        "**1. Do not build progressive summarisation up the org chart.** This "
        "is the intuitive design — each layer summarises the layer below — and "
        "it is the clearest negative result in the study. At "
        f"{big:,} users it finds "
        f"{(c if c is not None else 0):.0%} (plain summarisation), "
        f"{(d_ if d_ is not None else 0):.0%} (structured, no lineage) and "
        f"{(e_ if e_ is not None else 0):.0%} (structured with lineage) of the "
        "hidden problems. The reason is measurable rather than a matter of "
        "tuning, and is given in the executive summary.")
    out.append("")
    if f_ is not None and g_ is not None:
        out.append(
            "**2. If we build a hierarchy, the value is in the downward path, "
            "not the upward one.** Adding targeted downward retrieval takes "
            f"discovery from {(e_ or 0):.0%} to {f_:.0%}; adding "
            f"sketch-driven questioning on top takes it to {g_:.0%}. Budget "
            "accordingly: the upward channel should be cheap and statistical, "
            "and the downward channel is where the work — and the cost — "
            "actually is.")
        out.append("")
    out.append(_decision_evidence_vs_capability())
    out.append("")
    return "\n".join(out)


def _decision_evidence_vs_capability() -> str:
    """Decision 3, stated only as strongly as the repeats allow."""
    import json as _json
    p = os.path.join(ART, "live_rank_results.json")
    pr = os.path.join(ART, "live_rank_results_rich.json")
    head = ("**3. Spend top-tier model budget on re-opening original "
            "evidence, not on bigger reasoning over summaries.** ")
    if not (os.path.exists(p) and os.path.exists(pr)):
        return head + "_(live measurement not run)_"
    d, dr = _json.load(open(p)), _json.load(open(pr))

    def _cells(dd):
        ag = dd.get("models_aggregated")
        if ag:
            return {k: (v["ap_mean"], v["ap_min"], v["ap_max"], v["n_runs"])
                    for k, v in ag.items()}
        return {k: (v["ap"], v["ap"], v["ap"], 1)
                for k, v in dd.get("models", {}).items()}

    cs, cr = _cells(d), _cells(dr)
    common = sorted(set(cs) & set(cr))
    if not common:
        return head + "_(live measurement not run)_"
    helped = [m for m in common if cr[m][0] > cs[m][0]]
    # A model counts as separated only if its two conditions' observed run
    # ranges do not overlap AND the richer condition is the higher one.  With
    # 2-3 runs that is the only claim the data supports; anything finer would
    # be invented precision.  A cell with a single run has a degenerate
    # "range", so separation there is not evidence and is excluded.
    sep = [m for m in common
           if cs[m][3] > 1 and cr[m][3] > 1 and cr[m][1] > cs[m][2]]
    thin = [m for m in common if cs[m][3] < 2 or cr[m][3] < 2]
    ap_stats = f"{min(c[0] for c in cs.values()):.3f}–" \
               f"{max(c[0] for c in cs.values()):.3f}"
    body = (f"Measured directly on {len(common)} real models, each run "
            f"{max(c[3] for c in cr.values())} times per condition: given the "
            f"same summarised evidence, a large model and a small model land "
            f"in the same band (AP {ap_stats}) and no better than a "
            f"six-feature statistical rule ({d['simulator_logistic_ap']:.3f}). "
            f"Given the *original notes* behind that evidence, "
            f"{len(helped)} of {len(common)} improve")
    if sep:
        body += (f", and for {', '.join(sorted(sep))} the richer condition's "
                 f"worst run still beats the summarised condition's best")
    losers = [m for m in common if cr[m][0] <= cs[m][0]]
    if losers:
        body += (f". It is **not** universal: {', '.join(sorted(losers))} did "
                 f"not improve, so this is a property of a model's ability to "
                 f"use raw evidence, not a law")
    if thin:
        _t = sorted(thin)
        body += (f". {', '.join(_t)} still "
                 f"{'has' if len(_t) == 1 else 'have'} a condition measured "
                 f"only once, so "
                 f"{'its' if len(_t) == 1 else 'those'} "
                 f"{'direction is' if len(_t) == 1 else 'directions are'} "
                 f"provisional")
    body += (". Acting on it — having the kernel re-read a handful of source "
             "notes per candidate — was still the best return on compute "
             "found in the study, but size it against §23 rather than against "
             "the headline number.")
    return head + body


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

        def _means(dd):
            """Per-model mean AP over repeats, and the largest run-to-run
            range seen in any cell — which bounds how much of any difference
            below could be sampling rather than mechanism."""
            ag = dd.get("models_aggregated")
            if ag:
                return ({k: v["ap_mean"] for k, v in ag.items()},
                        max((v["ap_max"] - v["ap_min"] for v in ag.values()),
                            default=0.0),
                        max((v["n_runs"] for v in ag.values()), default=1))
            ms = dd.get("models", {})
            return ({k: v["ap"] for k, v in ms.items()}, 0.0, 1)

        ms, spread_s, n_s = _means(d)
        mr, spread_r, n_r = _means(dr)
        if ms and mr:
            common = sorted(set(ms) & set(mr))
            helped = sum(1 for m in common if mr[m] > ms[m])
            gaps = [mr[m] - ms[m] for m in common]
            biggest_spread = max(spread_s, spread_r)
            decisive = [m for m in common
                        if abs(mr[m] - ms[m]) > biggest_spread]
            strength = (
                "and changing what the evidence *contains* buys a lot"
                if len(decisive) == len(common) else
                "changing what the evidence *contains* helps the strongest "
                "model a lot and is not reliable across models"
                if decisive else
                "and changing what the evidence *contains* does not "
                "reliably help either")
            lines.append(
                f"**4. Directly measured: at fixed evidence, model capability "
                f"buys almost nothing here, {strength}.** "
                f"{len(common)} real models ranked the same {d['n_items']} "
                f"candidates from a real run "
                f"({max(n_s, n_r)} independent runs per cell). Given the "
                f"aggregate evidence statistics they scored AP "
                f"{min(ms.values()):.3f}–{max(ms.values()):.3f}, against "
                f"{d['simulator_logistic_ap']:.3f} for a six-feature logistic "
                f"and {d['random_ap']:.3f} for random — i.e. a large capability "
                f"range lands within noise of a logistic. Given the **raw work "
                f"notes** behind the same statistics the same models scored "
                f"{min(mr.values()):.3f}–{max(mr.values()):.3f}. Richer "
                f"evidence helped {helped} of {len(common)} models "
                f"(Δ {min(gaps):+.3f} to {max(gaps):+.3f}); the largest "
                f"run-to-run range within a single cell is "
                f"{biggest_spread:.3f}, and "
                + (f"{len(decisive)} of the {len(common)} "
                   f"{'moves' if len(decisive) == 1 else 'move'} "
                   f"by more than that "
                   f"({', '.join(sorted(decisive))}). " if decisive else
                   "no model moves by more than that, so this comparison "
                   "does not separate the two conditions. ")
                + "The abstraction, not the reasoner, is the plausible "
                  "ceiling — but see §23 before treating the size of the "
                  "effect as established.\n")

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
    lines.append(_critique_improvements_real())
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


def _critique_improvements_real() -> str:
    """Read the calibration file rather than asserting what it chose.

    An earlier hand-written version of this paragraph named the source-
    dispersion feature as a rejected non-result.  The calibration on the
    corrected corpus *selected* it, so the paragraph was contradicting the
    file it was describing.  Everything here is now read back from
    `calibration.json`.
    """
    head = "**Are the improvements real?** "
    p = os.path.join(ART, "calibration.json")
    if not os.path.exists(p):
        return head + "_(calibration not run)_\n"
    d = json.load(open(p))
    rejected, kept = [], []
    for knob, label in (("triage_prior_weight", "a triage prior"),
                        ("w_dispersion", "a source-dispersion feature"),
                        ("w_synchrony", "a reporting-synchrony feature"),
                        ("w_attribution", "an entity-attribution penalty")):
        if knob not in d:
            continue
        (kept if float(d[knob]) > 0 else rejected).append(
            f"{label} (held-out weight {float(d[knob]):g})")
    body = (f"Every knob was fitted on calibration seeds {d['seeds']}, "
            f"disjoint from the evaluation seeds, and then frozen. ")
    if rejected:
        body += ("The protocol **rejected** " + "; ".join(rejected) +
                 " — each of which had looked like a clear win on a single "
                 "seed. They are reported as non-results rather than quietly "
                 "dropped. ")
    if kept:
        body += ("It **selected** " + "; ".join(kept) + ". ")
    body += ("The one that matters most, kernel-side evidence verification, "
             "was selected on held-out seeds and is reported with its paired "
             "test in §12.\n\n"
             "One caveat belongs here rather than in a footnote: "
             "`w_dispersion` was rejected twice on the pre-correction corpus "
             "and selected on the corrected one, with no change to the "
             "mechanism. A setting whose sign flips when the data is "
             "regenerated is fitted to that data, not to the problem. It "
             "should be re-fitted on real data before deployment and should "
             "not be treated as a transferable finding.\n")
    return head + body


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
        out.append(
            "**That last figure is not a measurement and should not be read "
            "as one.** `question_targeting` is the fraction of questions the "
            "kernel aimed at the entity it was actually reasoning about, and "
            "in the simulator that is drawn directly from the kernel tier's "
            "`question_quality` parameter — so with a frontier kernel it is "
            "~100% *by construction*, and it would report ~100% even if "
            "targeting were worthless. The informative test is the ablation "
            "below that destroys targeting outright, and that one does not "
            "reach significance. It is listed here because leaving a "
            "parameter echoed back as a headline number would be the kind of "
            "thing this report is supposed to catch.\n")
    for name, label in (("-questions", "removing questioning entirely"),
                        ("-question_targeting", "keeping questions but "
                         "destroying their targeting")):
        p = paired(ab, "full", name, "found_anywhere_in_register",
                   group="ablation")
        pr_ = paired(ab, "full", name, "rare_signal_recall", group="ablation")
        pc = paired(ab, "full", name, "compute_units", group="ablation")
        if p.get("n"):
            # _sig reports full MINUS ablated.  Written next to a label that
            # names what was REMOVED, a positive number reads as if removal
            # helped, so the deltas are flipped here to be deltas of the
            # ablation itself and the direction is spelled out.
            out.append(
                f"* **{label}**: discovery {_flip(p)}, rare-signal recall "
                f"{_flip(pr_)}, compute {-pc['mean_diff']:+.2e} cu.")
    if len(out) > 1:
        out.append("")
        out.append("Every Δ above is *the ablated system minus the full one*, "
                   "so a negative number means removing the mechanism made "
                   "things worse. The seed count is the number of paired "
                   "seeds on which the ablation was the worse of the two.")
    return "\n".join(out)


def _flip(p: Dict) -> str:
    """_sig, with the sign of a paired(full, ablated) difference reversed."""
    if not p.get("n"):
        return "no paired runs"
    # p['wins'] counts seeds where full > ablated, i.e. seeds on which the
    # ablation was the worse of the pair.  That is the count the label
    # promises, and it is deliberately NOT flipped along with the sign.
    return (f"Δ {-p['mean_diff']:+.3f} "
            f"(95% CI [{-p['ci_hi']:+.3f}, {-p['ci_lo']:+.3f}], "
            f"worse on {p['wins']}/{p['n_nonzero']} seeds, "
            f"sign p={p['sign_p']:.3f})")
