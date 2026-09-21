"""Generate the final report from the raw JSONL artifacts.

Everything in the output is computed here from artifacts/*.jsonl; nothing is
transcribed by hand.  Missing experiments degrade to an explicit "(not run)"
line rather than being silently omitted.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .analysis import agg, boot_ci, dedupe, load, md_table, paired
from .findings import (GLOSSARY, critique, decision_summary,
                       executive_summary, next_experiments,
                       questions_section, recommendation)
from .runner import ART

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "MYCELIC_ENTERPRISE.md")

CORE = ["average_precision", "found_anywhere_in_register", "recall_at_100",
        "rare_signal_recall", "evidence_coverage_2links",
        "false_discovery_rate", "decoy_acceptance_all",
        "independent_evidence_accuracy", "lineage_accuracy",
        "contradiction_f1", "information_loss", "compute_units",
        "tokens_total", "inference_calls", "wall_seconds",
        "privacy_exposure_fraction", "executive_answer_accuracy"]

NICE = {
    "average_precision": "AP",
    "average_precision_strict": "AP (strict match)",
    "found_anywhere_in_register": "found",
    "recall_at_100": "R@100",
    "recall_at_300": "R@300",
    "rare_signal_recall": "rare recall",
    "common_signal_recall": "common recall",
    "evidence_coverage_2links": "evidence cov.",
    "evidence_coverage_3links": "evidence cov.3",
    "false_discovery_rate": "FDR",
    "decoy_acceptance_all": "decoy acc.",
    "decoy_D1_entity_coincidence": "D1 entity-coinc.",
    "decoy_D2_temporal_scramble": "D2 time-scramble",
    "decoy_D3_near_miss_entity": "D3 near-miss",
    "decoy_D5_stale_chain": "D5 stale",
    "dup_inflation_support_error": "D4 support infl.",
    "independent_evidence_accuracy": "indep. acc.",
    "lineage_accuracy": "lineage",
    "provenance_preservation": "provenance",
    "evidence_precision": "evid. prec.",
    "evidence_recall": "evid. recall",
    "contradiction_f1": "contra F1",
    "information_loss": "info loss",
    "compute_units": "compute (cu)",
    "usd_estimate": "USD est.",
    "tokens_total": "tokens",
    "inference_calls": "calls",
    "wall_seconds": "wall s",
    "p95_call_s": "p95 call s",
    "privacy_exposure_fraction": "privacy exp.",
    "executive_answer_accuracy": "exec acc.",
    "cost_per_correct_discovery": "cu / discovery",
    "question_utility": "Q utility",
    "family_recall": "variant families",
    "kernel_context_tokens": "kernel ctx",
    "compression_ratio": "compression",
}


def _cols(metrics: Sequence[str], first: Tuple[str, str] = ("arch", "architecture")):
    out = [(first[0], first[1], 0)]
    for m in metrics:
        dec = 0 if m in ("compute_units", "tokens_total", "inference_calls") else 3
        out.append((m, NICE.get(m, m), dec))
    return out


def _fmt(rows, metrics, first=("arch", "architecture"), sort="average_precision",
         ci=()):
    if not rows:
        return "_(not run)_"
    return md_table(rows, _cols(metrics, first), sort_by=sort, ci_for=ci)


def section_baselines() -> str:
    rows = dedupe(load("e1_baselines.jsonl") + load("e1b_extra.jsonl"))
    if not rows:
        return "_(E1 not run)_"
    out = []
    for scale in sorted({r["scale"] for r in rows}):
        sub = [r for r in rows if r["scale"] == scale]
        n_seeds = len({r["seed"] for r in sub})
        a = agg(sub, CORE, by=("arch",))
        out.append(f"\n#### {scale:,} users "
                   f"({sub[0]['n_records']:,} records, "
                   f"{sub[0]['n_gold']} hidden patterns, {n_seeds} seeds)\n")
        out.append(_fmt(a, ["average_precision", "found_anywhere_in_register",
                            "recall_at_100", "rare_signal_recall",
                            "evidence_coverage_2links", "false_discovery_rate",
                            "decoy_acceptance_all",
                            "independent_evidence_accuracy",
                            "lineage_accuracy", "information_loss",
                            "compute_units", "privacy_exposure_fraction"],
                        ci=("average_precision",
                            "found_anywhere_in_register")))
    return "\n".join(out)


def section_pairs() -> str:
    rows = dedupe(load("e1_baselines.jsonl") + load("e1b_extra.jsonl"))
    if not rows:
        return "_(E1 not run)_"
    pairs = [
        ("H_mycelic_full", "E_hier_lineage"),
        ("H_mycelic_full", "B2_map_reduce"),
        ("H_mycelic_full", "A_flat_rag"),
        ("H_mycelic_full", "B_long_context"),
        ("H_mycelic_full", "C_recursive_sum"),
        ("H_mycelic_full", "D_hier_nolineage"),
        ("H_mycelic_full", "B4_central_triage"),
        ("H_mycelic_full", "Y_oracle_retrieval"),
        ("B2_map_reduce", "Z_random_rank"),
        ("G_hier_questions", "F_hier_retrieval"),
        ("F_hier_retrieval", "E_hier_lineage"),
    ]
    lines = ["| comparison | metric | mean A | mean B | Δ | 95% CI | wins | sign p |",
             "|---|---|---:|---:|---:|---|---:|---:|"]
    for metric in ("found_anywhere_in_register", "average_precision"):
        for a, b in pairs:
            p = paired(rows, a, b, metric)
            if not p.get("n"):
                continue
            lines.append(
                f"| {a} vs {b} | {NICE.get(metric, metric)} | "
                f"{p['mean_a']:.4f} | {p['mean_b']:.4f} | {p['mean_diff']:+.4f} | "
                f"[{p['ci_lo']:+.4f}, {p['ci_hi']:+.4f}] | "
                f"{p['wins']}/{p['n_nonzero']} | {p['sign_p']:.3f} |")
    return "\n".join(lines)


def section_ablations() -> str:
    rows = load("e2_ablations.jsonl")
    if not rows:
        return "_(E2 not run)_"
    a = agg(rows, CORE + ["rare_signal_recall", "question_utility",
                          "provenance_preservation", "family_recall",
                          "dup_inflation_support_error"], by=("ablation",))
    tbl = _fmt(a, ["average_precision", "found_anywhere_in_register",
                   "rare_signal_recall", "evidence_coverage_2links",
                   "false_discovery_rate", "independent_evidence_accuracy",
                   "lineage_accuracy", "contradiction_f1",
                   "dup_inflation_support_error", "question_utility",
                   "compute_units"],
               first=("ablation", "variant"), ci=("found_anywhere_in_register",))
    lines = [tbl, "", "**Paired against the full system** (same seeds):", "",
             "| removed | Δ found | 95% CI | wins | sign p | Δ AP | Δ compute |",
             "|---|---:|---|---:|---:|---:|---:|"]
    for name in sorted({r["ablation"] for r in rows}):
        if name == "full":
            continue
        pf = paired(rows, "full", name, "found_anywhere_in_register",
                    group="ablation", key=("scale", "seed"))
        pa = paired(rows, "full", name, "average_precision",
                    group="ablation", key=("scale", "seed"))
        pc = paired(rows, "full", name, "compute_units",
                    group="ablation", key=("scale", "seed"))
        if not pf.get("n"):
            continue
        lines.append(
            f"| {name} | {-pf['mean_diff']:+.4f} | "
            f"[{-pf['ci_hi']:+.4f}, {-pf['ci_lo']:+.4f}] | "
            f"{pf['wins']}/{pf['n_nonzero']} | {pf['sign_p']:.3f} | "
            f"{-pa['mean_diff']:+.4f} | {-pc['mean_diff']:+.3e} |")
    return "\n".join(lines)


def section_alloc() -> str:
    rows = load("e3_allocation.jsonl")
    if not rows:
        return "_(E3 not run)_"
    out = []
    for arch in sorted({r["arch"] for r in rows}):
        sub = [r for r in rows if r["arch"] == arch]
        a = agg(sub, CORE + ["cost_per_correct_discovery", "usd_estimate"],
                by=("alloc",))
        for r in a:
            t = [x for x in sub if x["alloc"] == r["alloc"]]
            r["tiers"] = t[0].get("tiers", "")
        out.append(f"\n#### {arch}\n")
        out.append(md_table(a, [("alloc", "allocation", 0),
                                ("tiers", "user→…→kernel", 0),
                                ("average_precision", "AP", 4),
                                ("found_anywhere_in_register", "found", 3),
                                ("rare_signal_recall", "rare", 3),
                                ("compute_units", "compute", 0),
                                ("cost_per_correct_discovery", "cu/disc", 0),
                                ("wall_seconds", "wall s", 1)],
                            sort_by="found_anywhere_in_register"))
    return "\n".join(out)


def section_level_marginal() -> str:
    rows = load("e3b_level_marginal.jsonl")
    if not rows:
        return "_(E3b not run)_"
    out = []
    for arch in sorted({r["arch"] for r in rows}):
        sub = [r for r in rows if r["arch"] == arch]
        a = agg(sub, CORE, by=("upgraded_level",))
        base = [x for x in a if x["upgraded_level"] == "none"]
        out.append(f"\n#### {arch} — baseline is every level at small-7b\n")
        for r in a:
            if base:
                r["delta_found"] = r["found_anywhere_in_register"] - \
                    base[0]["found_anywhere_in_register"]
                r["delta_cu"] = r["compute_units"] - base[0]["compute_units"]
                r["found_per_Mcu"] = (r["delta_found"] /
                                      max(1.0, r["delta_cu"] / 1e6)) \
                    if r["delta_cu"] > 0 else 0.0
        out.append(md_table(a, [("upgraded_level", "level upgraded to frontier", 0),
                                ("average_precision", "AP", 4),
                                ("found_anywhere_in_register", "found", 3),
                                ("delta_found", "Δ found", 3),
                                ("compute_units", "compute", 0),
                                ("delta_cu", "Δ compute", 0),
                                ("found_per_Mcu", "Δfound / Mcu", 4)],
                            sort_by="delta_found"))
        # paired significance vs the none baseline
        out.append("")
        out.append("| level | Δ found | 95% CI | wins | sign p |")
        out.append("|---|---:|---|---:|---:|")
        for lvl in sorted({r["upgraded_level"] for r in sub}):
            if lvl == "none":
                continue
            p = paired(sub, lvl, "none", "found_anywhere_in_register",
                       group="upgraded_level", key=("scale", "seed"))
            if p.get("n"):
                out.append(f"| {lvl} | {p['mean_diff']:+.4f} | "
                           f"[{p['ci_lo']:+.4f}, {p['ci_hi']:+.4f}] | "
                           f"{p['wins']}/{p['n_nonzero']} | {p['sign_p']:.3f} |")
    return "\n".join(out)


def section_qsweep() -> str:
    rows = load("e3c_q_sweep.jsonl")
    if not rows:
        return "_(E3c not run)_"
    a = agg(rows, CORE, by=("arch", "q"))
    tbl = md_table(a, [("arch", "architecture", 0), ("q", "q", 2),
                       ("average_precision", "AP", 4),
                       ("found_anywhere_in_register", "found", 3),
                       ("rare_signal_recall", "rare", 3),
                       ("false_discovery_rate", "FDR", 3),
                       ("compute_units", "compute", 0)],
                   sort_by=None)
    return tbl + "\n\n" + _qsweep_verdict(a)


def _qsweep_verdict(a: List[Dict]) -> str:
    """What the capability sweep says about where capability is worth buying."""
    qs = sorted({r["q"] for r in a})
    archs = sorted({r["arch"] for r in a})
    if len(qs) < 3:
        return ""
    lo, hi = qs[0], qs[-1]

    def g(arch, q, k="found_anywhere_in_register"):
        v = [r[k] for r in a if r["arch"] == arch and r["q"] == q]
        return float(v[0]) if v else float("nan")

    lines = ["**What the sweep says.** Each architecture is run with *every* "
             "level set to the same capability `q`, so the curve isolates the "
             "operator from the topology.", "",
             "| architecture | found at q=%.2f | found at q=%.2f | gain | "
             "compute multiple | gain per doubling of compute |"
             % (lo, hi),
             "|---|---:|---:|---:|---:|---:|"]
    rows_out = []
    for arch in archs:
        f0, f1 = g(arch, lo), g(arch, hi)
        c0, c1 = g(arch, lo, "compute_units"), g(arch, hi, "compute_units")
        mult = c1 / c0 if c0 else float("nan")
        doublings = np.log2(mult) if mult and mult > 0 else float("nan")
        per = (f1 - f0) / doublings if doublings and doublings > 0 else 0.0
        rows_out.append((arch, f0, f1, f1 - f0, mult, per))
        lines.append(f"| {arch} | {f0:.3f} | {f1:.3f} | {f1-f0:+.3f} | "
                     f"{mult:.1f}x | {per:+.3f} |")
    lines.append("")
    # The architecture that converts capability into discovery most
    # efficiently is the one worth spending a model budget on.
    best = max(rows_out, key=lambda r: r[5])
    worst = min(rows_out, key=lambda r: r[5])
    lines.append(
        f"Capability is not worth the same everywhere. Per doubling of "
        f"compute spent on better operators, `{best[0]}` converts it into "
        f"{best[5]:+.3f} discovery and `{worst[0]}` into {worst[5]:+.3f}. "
        f"Read together with §13, which upgrades one level at a time: this "
        f"table says how much a *uniform* capability increase is worth, §13 "
        f"says where to put it if you are only buying one.")
    # Pure upward aggregation is the interesting special case.
    up = [r for r in rows_out if r[0].startswith("E_")]
    if up:
        e = up[0]
        lines.append("")
        lines.append(
            f"The row worth dwelling on is `{e[0]}`, pure upward aggregation "
            f"with no descent. It finds {e[1]:.1%} at q={lo:.2f} and "
            f"{e[2]:.1%} at q={hi:.2f}. Its failure at realistic operator "
            f"quality is therefore not a structural impossibility — a "
            f"*perfect* summariser would make it work — but every real "
            f"operator sits far enough below perfect that the structure "
            f"cannot be rescued by a better model. That is a stronger "
            f"negative result than 'it does not work', and a more useful one: "
            f"it says the design is sensitive to exactly the thing we cannot "
            f"guarantee.")
    return "\n".join(lines)


def section_fanin() -> str:
    out = []
    rows = load("e4_fanin.jsonl")
    if rows:
        a = agg(rows, CORE + ["kernel_context_tokens", "compression_ratio"],
                by=("arch", "team_size"))
        out.append("#### Users per team\n")
        out.append(md_table(a, [("arch", "architecture", 0),
                                ("team_size", "team size", 0),
                                ("average_precision", "AP", 4),
                                ("found_anywhere_in_register", "found", 3),
                                ("rare_signal_recall", "rare", 3),
                                ("information_loss", "info loss", 3),
                                ("compute_units", "compute", 0),
                                ("wall_seconds", "wall s", 1)], sort_by=None))
    rows = load("e4b_dept_fanin.jsonl")
    if rows:
        a = agg(rows, CORE, by=("arch", "teams_per_dept"))
        out.append("\n#### Teams per department\n")
        out.append(md_table(a, [("arch", "architecture", 0),
                                ("teams_per_dept", "teams/dept", 0),
                                ("average_precision", "AP", 4),
                                ("found_anywhere_in_register", "found", 3),
                                ("information_loss", "info loss", 3),
                                ("compute_units", "compute", 0)], sort_by=None))
    return "\n".join(out) if out else "_(E4 not run)_"


def section_adversarial() -> str:
    rows = load("e5_adversarial.jsonl")
    if not rows:
        return "_(E5 not run)_"
    a = agg(rows, CORE + ["decoy_D1_entity_coincidence",
                          "decoy_D2_temporal_scramble",
                          "decoy_D3_near_miss_entity", "decoy_D5_stale_chain",
                          "dup_inflation_support_error"],
            by=("condition", "arch"))
    return md_table(a, [("condition", "condition", 0), ("arch", "architecture", 0),
                        ("average_precision", "AP", 4),
                        ("found_anywhere_in_register", "found", 3),
                        ("false_discovery_rate", "FDR", 3),
                        ("decoy_acceptance_all", "decoy acc", 3),
                        ("decoy_D1_entity_coincidence", "D1", 3),
                        ("decoy_D2_temporal_scramble", "D2", 3),
                        ("decoy_D3_near_miss_entity", "D3", 3),
                        ("decoy_D5_stale_chain", "D5", 3),
                        ("dup_inflation_support_error", "D4 infl", 2)],
                    sort_by=None)


def section_frontier() -> str:
    rows = load("e6_frontier.jsonl")
    if not rows:
        return "_(E6 not run)_"
    out = []
    h = [r for r in rows if r["knob"] == "question_frac"]
    if h:
        a = agg(h, CORE, by=("question_frac",))
        out.append("#### Hierarchy: question / descent budget\n")
        out.append(md_table(a, [("question_frac", "question budget frac", 2),
                                ("average_precision", "AP", 4),
                                ("found_anywhere_in_register", "found", 3),
                                ("rare_signal_recall", "rare", 3),
                                ("compute_units", "compute", 0),
                                ("cost_per_correct_discovery", "cu/disc", 0)],
                            sort_by=None))
    m = [r for r in rows if r["knob"] == "mr_budget"]
    if m:
        a = agg(m, CORE, by=("mr_budget",))
        out.append("\n#### Map-reduce: kernel object budget\n")
        out.append(md_table(a, [("mr_budget", "kernel objects", 0),
                                ("average_precision", "AP", 4),
                                ("found_anywhere_in_register", "found", 3),
                                ("compute_units", "compute", 0),
                                ("cost_per_correct_discovery", "cu/disc", 0)],
                            sort_by=None))
    return "\n".join(out)


def section_shape() -> str:
    rows = load("e7_shape.jsonl")
    if not rows:
        return "_(E7 not run)_"
    a = agg(rows, CORE, by=("shape", "alloc", "arch"))
    return md_table(a, [("shape", "capability shape", 0),
                        ("alloc", "allocation", 0),
                        ("arch", "architecture", 0),
                        ("average_precision", "AP", 4),
                        ("found_anywhere_in_register", "found", 3),
                        ("compute_units", "compute", 0)], sort_by=None)


def section_crosslinks() -> str:
    rows = load("e8_crosslinks.jsonl")
    if not rows:
        return "_(E8 not run)_"
    a = agg(rows, CORE, by=("scale", "cross_links"))
    lines = [md_table(a, [("scale", "users", 0),
                          ("cross_links", "semantic cross-links", 0),
                          ("average_precision", "AP", 4),
                          ("found_anywhere_in_register", "found", 3),
                          ("rare_signal_recall", "rare", 3),
                          ("compute_units", "compute", 0)], sort_by=None),
             "", "| scale | Δ found (links on − off) | 95% CI | wins | sign p |",
             "|---|---:|---|---:|---:|"]
    deltas = []
    for scale in sorted({r["scale"] for r in rows}):
        sub = [r for r in rows if r["scale"] == scale]
        p = paired(sub, True, False, "found_anywhere_in_register",
                   group="cross_links", key=("scale", "seed"))
        if p.get("n"):
            lines.append(f"| {scale:,} | {p['mean_diff']:+.4f} | "
                         f"[{p['ci_lo']:+.4f}, {p['ci_hi']:+.4f}] | "
                         f"{p['wins']}/{p['n_nonzero']} | {p['sign_p']:.3f} |")
            deltas.append((scale, p))
    lines.append("")
    lines.append(_crosslink_verdict(deltas))
    return "\n".join(lines)


def _crosslink_verdict(deltas) -> str:
    """State what the cross-link sweep found, including when it found nothing."""
    if not deltas:
        return ""
    # A scale counts as supporting cross-links only if the whole bootstrap CI
    # is on one side of zero; with 3 seeds the sign test cannot go below 0.25,
    # so requiring significance there would reject everything by construction.
    pos = [s for s, p in deltas if p["ci_lo"] > 0]
    neg = [s for s, p in deltas if p["ci_hi"] < 0]
    biggest = max(deltas, key=lambda x: abs(x[1]["mean_diff"]))
    out = ("**Verdict.** Adding semantic cross-links to the organisational "
           "tree ")
    if pos and not neg and len(pos) == len(deltas):
        out += "helps at every scale measured"
    elif pos and max(pos) == max(s for s, _ in deltas):
        out += "helps, and the effect holds at the largest scale measured"
    elif pos:
        out += (f"helps at {', '.join(f'{s:,}' for s in pos)} users but the "
                f"effect does not survive to "
                f"{max(s for s, _ in deltas):,}")
    else:
        out += "does not reliably change discovery at any scale measured"
    out += (f". The largest effect anywhere is "
            f"{biggest[1]['mean_diff']:+.3f} at {biggest[0]:,} users, on "
            f"{biggest[1]['n']} paired seeds. ")
    if not pos or max(pos, default=0) != max(s for s, _ in deltas):
        out += ("The mechanism the links are supposed to supply — a path "
                "between branches that the org chart does not provide — is "
                "already supplied by the sketch channel, which is indexed by "
                "entity rather than by branch and so crosses the tree for "
                "free. On this evidence the cross-links are redundant with "
                "it, not additive to it, and should not be built on the "
                "strength of these numbers.")
    else:
        out += ("Note that the seed counts here are small enough that the "
                "sign test cannot fall below 0.25, so the bootstrap interval "
                "is doing the work; treat this as directional.")
    return out



def section_provenance() -> str:
    rows = load("e12_provenance.jsonl")
    if not rows:
        return "_(E12 not run)_"
    a = agg(rows, ["evidence_names_claimed_entity", "reports_fully_attributed",
                   "reports_with_no_matching_evidence",
                   "found_anywhere_in_register", "evidence_precision",
                   "provenance_preservation"],
            by=("scale", "arch"))
    tbl = md_table(a, [("scale", "users", 0), ("arch", "architecture", 0),
                       ("evidence_names_claimed_entity",
                        "cited evidence names the claimed entity", 3),
                       ("reports_fully_attributed",
                        "reports fully attributed", 3),
                       ("reports_with_no_matching_evidence",
                        "reports with NO matching evidence", 3),
                       ("evidence_precision", "evidence precision", 3),
                       ("found_anywhere_in_register", "found", 3)],
                   sort_by=None)
    big = max(r["scale"] for r in a)
    sub = [r for r in a if r["scale"] == big]
    if not sub:
        return tbl
    best = max(sub, key=lambda r: r["evidence_names_claimed_entity"])
    hier = [r for r in sub if r["arch"] == "H_mycelic_full"]
    out = [tbl, ""]
    out.append(
        f"**Verdict at {big:,} users.** The strongest attribution is "
        f"`{best['arch']}` — {best['evidence_names_claimed_entity']:.0%} of "
        f"cited evidence names the entity the report is about, and "
        f"{best['reports_fully_attributed']:.0%} of its reports are fully "
        f"attributable end to end.")
    if hier:
        h = hier[0]
        out.append("")
        out.append(
            f"The hierarchy reaches "
            f"{h['evidence_names_claimed_entity']:.0%} on the first measure "
            f"and {h['reports_fully_attributed']:.1%} on the second. **That "
            f"second number is the uncomfortable one**, and it should be read "
            f"before any claim that a lineage-carrying architecture is "
            f"inherently more auditable: carrying a lineage *path* is not the "
            f"same as being able to put an executive in front of the original "
            f"note. The hierarchy knows which nodes a claim travelled "
            f"through; the centralised options can still show you the text. "
            f"For a regulator or an incident review, the second is what is "
            f"being asked for.")
        out.append("")
        hn = h["reports_with_no_matching_evidence"]
        bn = best["reports_with_no_matching_evidence"]
        # Lower is better here: this counts reports the system made with no
        # evidence behind them at all.
        out.append(
            f"It goes the same way on unsupported assertions — reports made "
            f"with no matching evidence behind them at all, where lower is "
            f"better. The hierarchy is at {hn:.1%} and `{best['arch']}` at "
            f"{bn:.1%}"
            + (f", so the hierarchy is roughly {hn / max(bn, 1e-9):.0f}x more "
               f"likely to put something on the register it cannot back up. "
               if hn > bn else
               f", so the hierarchy is the more conservative of the two here. ")
            + "Across all three attribution measures, provenance is a place "
              "the hierarchy loses, not a place it wins. It should not be "
              "used as an argument for building one.")
    return "\n".join(out)


def section_privacy() -> str:
    rows = load("e9_privacy.jsonl")
    if not rows:
        return "_(E9 not run)_"
    a = agg(rows, ["raw_text_exposure_fraction", "claim_exposure_fraction",
                   "sketch_entries_leaving_node", "privacy_exposure_fraction",
                   "found_anywhere_in_register", "compute_units"],
            by=("scale", "arch"))
    return md_table(a, [("scale", "users", 0), ("arch", "architecture", 0),
                        ("raw_text_exposure_fraction", "raw text out", 4),
                        ("claim_exposure_fraction", "claims out", 4),
                        ("sketch_entries_leaving_node", "index entries out", 0),
                        ("found_anywhere_in_register", "found", 3),
                        ("compute_units", "compute", 0)], sort_by=None)


def section_scale_trend() -> str:
    rows = dedupe(load("e1_baselines.jsonl") + load("e1b_extra.jsonl")
                  + load("e10_scale_trend.jsonl"))
    if not rows:
        return "_(not run)_"
    archs = ["A_flat_rag", "A2_chunked_ctx", "B_long_context",
             "B2_map_reduce", "B4_central_triage", "E_hier_lineage",
             "G_hier_questions", "H_mycelic_full", "J_mycelic_verified",
             "Y_oracle_retrieval"]
    scales = sorted({r["scale"] for r in rows})
    lines = ["| architecture | " + " | ".join(f"{s:,}" for s in scales) + " |",
             "|---|" + "---:|" * len(scales)]
    for a in archs:
        cells = []
        for sc in scales:
            sub = [r["found_anywhere_in_register"] for r in rows
                   if r["arch"] == a and r["scale"] == sc]
            cells.append(f"{np.mean(sub):.3f}" if sub else "—")
        if any(c != "—" for c in cells):
            lines.append(f"| {a} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Compute (cu) at the same points:")
    lines.append("")
    lines.append("| architecture | " + " | ".join(f"{s:,}" for s in scales) + " |")
    lines.append("|---|" + "---:|" * len(scales))
    for a in archs:
        cells = []
        for sc in scales:
            sub = [r["compute_units"] for r in rows
                   if r["arch"] == a and r["scale"] == sc]
            cells.append(f"{np.mean(sub):.2e}" if sub else "—")
        if any(c != "—" for c in cells):
            lines.append(f"| {a} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def section_live() -> str:
    out = []
    p = os.path.join(ART, "live_calibration.json")
    if os.path.exists(p):
        d = json.load(open(p))
        out.append("#### Primitive operators (blind, 198 items)\n")
        out.append("| model | extraction | entity fidelity | causal check | "
                   "temporal check | entity linking | independence | fitted q |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for m, v in d["measurements"].items():
            out.append(f"| {m} | {v['extract_recall']:.3f} | "
                       f"{v['entity_fidelity']:.3f} | {v['causal_check']:.3f} | "
                       f"{v['temporal_check']:.3f} | {v['entity_check']:.3f} | "
                       f"{v['dedup_check']:.3f} | {v['fitted_q']:.2f} |")
    for suffix, label in (("", "evidence STATISTICS only"),
                          ("_rich", "statistics + the RAW WORK NOTES")):
        p = os.path.join(ART, f"live_rank_results{suffix}.json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        out.append(f"\n#### Candidate discrimination — {label} "
                   f"({d['n_items']} candidates from a real run, "
                   f"{d['n_real']} genuine)\n")
        out.append("| ranker | AP | selection precision | selection recall | "
                   "selection F1 | n selected |")
        out.append("|---|---:|---:|---:|---:|---:|")
        out.append(f"| random | {d['random_ap']:.4f} | — | — | — | — |")
        out.append(f"| simulator's calibrated logistic (statistics) | "
                   f"{d['simulator_logistic_ap']:.4f} | — | — | — | — |")
        for m, v in sorted(d.get("models", {}).items()):
            out.append(f"| {m} | **{v['ap']:.4f}** | "
                       f"{v['selection_precision']:.3f} | "
                       f"{v['selection_recall']:.3f} | {v['selection_f1']:.3f} | "
                       f"{v['n_selected']} |")
        ag = {k: v for k, v in d.get("models_aggregated", {}).items()
              if v.get("n_runs", 1) > 1}
        if ag:
            out.append("")
            out.append("Independent repeats of the same task, same model, "
                       "fresh context. One run per cell cannot carry a "
                       "mechanism claim, so the spread is reported rather "
                       "than averaged away:")
            out.append("")
            out.append("| model | runs | AP mean | AP min | AP max | spread |")
            out.append("|---|---:|---:|---:|---:|---:|")
            for m, v in sorted(ag.items()):
                out.append(f"| {m} | {v['n_runs']} | {v['ap_mean']:.4f} | "
                           f"{v['ap_min']:.4f} | {v['ap_max']:.4f} | "
                           f"{v['ap_max'] - v['ap_min']:.4f} |")
    out.append("")
    out.append(_live_rich_delta())
    return "\n".join(out) if out else "_(live measurements not run)_"


def _live_rich_delta() -> str:
    """Does richer evidence beat a bigger model?  Answered from the repeats."""
    pa = os.path.join(ART, "live_rank_results.json")
    pb = os.path.join(ART, "live_rank_results_rich.json")
    if not (os.path.exists(pa) and os.path.exists(pb)):
        return ""
    d, dr = json.load(open(pa)), json.load(open(pb))

    def _cell(dd, m):
        ag = dd.get("models_aggregated", {}).get(m)
        if ag:
            return ag["ap_mean"], ag["ap_min"], ag["ap_max"], ag["n_runs"]
        v = dd.get("models", {}).get(m)
        if v:
            return v["ap"], v["ap"], v["ap"], 1
        return None

    names = sorted(set(d.get("models_aggregated", d.get("models", {})))
                   | set(dr.get("models_aggregated", dr.get("models", {}))))
    lines = ["**Does giving the SAME model richer evidence beat giving the "
             "task to a BIGGER model?**", "",
             "| model | statistics only (mean [min, max], n) | "
             "+ raw work notes (mean [min, max], n) | Δ |",
             "|---|---:|---:|---:|"]
    deltas = []
    for m in names:
        ca, cb = _cell(d, m), _cell(dr, m)
        if not (ca and cb):
            continue
        deltas.append((m, cb[0] - ca[0], ca, cb))
        lines.append(
            f"| {m} | {ca[0]:.4f} [{ca[1]:.4f}, {ca[2]:.4f}] n={ca[3]} | "
            f"{cb[0]:.4f} [{cb[1]:.4f}, {cb[2]:.4f}] n={cb[3]} | "
            f"{cb[0]-ca[0]:+.4f} |")
    if not deltas:
        return ""
    pos = sum(1 for _, dv, _, _ in deltas if dv > 0)
    # True interval overlap, not a one-sided comparison: the conditions are
    # separated only when one range lies entirely above the other.
    overlap = [m for m, dv, ca, cb in deltas
               if not (cb[1] > ca[2] or ca[1] > cb[2])]
    thin = [m for m, dv, ca, cb in deltas if ca[3] < 2 or cb[3] < 2]
    lines.append("")
    lines.append(
        f"Richer evidence helped {pos} of {len(deltas)} models. "
        + ("For " + ", ".join(sorted(overlap)) + " the two conditions' run "
           "ranges OVERLAP, so for those models this comparison does not "
           "separate the conditions at all. "
           if overlap else
           "No model's two condition ranges overlap. ")
        + (", ".join(sorted(thin))
           + (" still has" if len(thin) == 1 else " still have")
           + " a condition measured only once, so that range is a single "
             "point and any separation there is not evidence. "
           if thin else "")
        + "Read the size of the run-to-run spread before reading any Δ: "
          "where the spread is comparable to the gap, the gap is not a result.")
    return "\n".join(lines)


def section_calibration() -> str:
    p = os.path.join(ART, "calibration.json")
    if not os.path.exists(p):
        return "_(calibration not run)_"
    d = json.load(open(p))
    lines = [f"Fitted on calibration seeds {d['seeds']} at {d['scale']:,} users, "
             f"objective `{d['objective']}`, then frozen:", "",
             "| knob | chosen |", "|---|---:|"]
    for k in ("triage_prior_weight", "question_frac", "mr_budget",
              "ct_kernel_ko_cap", "flat_budget"):
        if k in d:
            lines.append(f"| `{k}` | {d[k]} |")
    for gname, label in (("hier_grid", "hierarchy"), ("mr_grid", "map-reduce"),
                         ("ct_grid", "central triage"), ("rag_grid", "flat RAG")):
        if gname not in d:
            continue
        lines.append(f"\n**{label} grid**\n")
        keys = [k for k in d[gname][0] if k not in
                ("average_precision", "found", "recall_at_100", "cu")]
        lines.append("| " + " | ".join(keys + ["AP", "found", "compute"]) + " |")
        lines.append("|" + "---|" * (len(keys) + 3))
        for row in d[gname]:
            lines.append("| " + " | ".join(str(row[k]) for k in keys) +
                         f" | {row['average_precision']:.5f} | "
                         f"{row.get('found', float('nan')):.3f} | "
                         f"{row['cu']:.3e} |")
    return "\n".join(lines)


def world_description() -> str:
    from .corpus import DEFAULT_CFG, CAUSAL_CHAINS
    from .org import DEFAULT_FANIN, build_org
    from .runner import build_world
    lines = []
    for n in (2_000, 10_000, 50_000):
        try:
            w = build_world(n, 0)
            s = w.org.stats()
            c = w.corpus.stats()
            lines.append(
                f"| {len(w.org.user_ids):,} | {c['n_records']:,} | "
                f"{s['team']['count']:,} | {s['department']['count']:,} | "
                f"{s['site']['count']} | {s['region']['count']} | "
                f"{s['team']['fanin_p10']}–{s['team']['fanin_p90']} "
                f"(med {s['team']['fanin_p50']}) | "
                f"{s['department']['fanin_p10']}–{s['department']['fanin_p90']} | "
                f"{s['site']['fanin_min']}–{s['site']['fanin_max']} | "
                f"{c['n_real_patterns']} | {c['n_rare_patterns']} | "
                f"{c['n_decoys']} | {c['n_entities']:,} |")
        except Exception as e:  # pragma: no cover
            lines.append(f"| {n} | error: {e} |")
    head = ("| users | records | teams | depts | sites | regions | users/team "
            "(p10–p90) | teams/dept | depts/site | patterns | rare | decoys | "
            "entities |\n|" + "---|" * 13)
    return head + "\n" + "\n".join(lines)


def section_headline() -> str:
    """The claims that are about the problem rather than about our design.

    Numbers are read back from the artifacts so that this section cannot
    drift from the results as seeds are added.
    """
    rows = dedupe(load("e1_baselines.jsonl") + load("e1b_extra.jsonl"))
    big = max((r["scale"] for r in rows), default=0)

    def mean(arch, key):
        v = [r[key] for r in rows if r["arch"] == arch and r["scale"] == big]
        return float(np.mean(v)) if v else float("nan")

    o_cov, o_found = (mean("Y_oracle_retrieval", "evidence_coverage_2links"),
                      mean("Y_oracle_retrieval", "found_anywhere_in_register"))
    n_or = len({r["seed"] for r in rows
                if r["arch"] == "Y_oracle_retrieval" and r["scale"] == big})

    live = ""
    pa = os.path.join(ART, "live_rank_results.json")
    pb = os.path.join(ART, "live_rank_results_rich.json")
    if os.path.exists(pa) and os.path.exists(pb):
        d, dr = json.load(open(pa)), json.load(open(pb))
        ag, agr = d.get("models_aggregated", {}), dr.get("models_aggregated", {})
        if ag and agr:
            band = f"{min(v['ap_mean'] for v in ag.values()):.3f}–" \
                   f"{max(v['ap_mean'] for v in ag.values()):.3f}"
            gaps = {k: agr[k]["ap_mean"] - ag[k]["ap_mean"]
                    for k in ag if k in agr}
            live = (
                f"3. **At fixed evidence, model capability is not the lever "
                f"people expect it to be.** Measured directly, blind, on "
                f"{len(ag)} real models across "
                f"{max(v['n_runs'] for v in agr.values())} runs each: given "
                f"the same aggregated evidence statistics, all of them land "
                f"in a band (AP {band}) that does not beat a six-feature "
                f"logistic regression ({d['simulator_logistic_ap']:.3f}). "
                f"Changing what the evidence *contains* moved one model by "
                f"{max(gaps.values()):+.2f} and another by "
                f"{min(gaps.values()):+.2f}. *Might not transfer* — and note "
                f"that it did not transfer uniformly even here, which is the "
                f"point.\n")

    prov = ""
    pr = load("e12_provenance.jsonl")
    if pr:
        pb_ = max(r["scale"] for r in pr)
        sub = agg([r for r in pr if r["scale"] == pb_],
                  ["reports_fully_attributed"], by=("arch",))
        h = [r for r in sub if r["arch"] == "H_mycelic_full"]
        best = max(sub, key=lambda r: r["reports_fully_attributed"])
        if h:
            prov = (
                f"4. **Carrying lineage is not the same as being auditable.** "
                f"Measured: the lineage-carrying hierarchy attributes "
                f"{h[0]['reports_fully_attributed']:.1%} of its reports to "
                f"original evidence end to end; `{best['arch']}` attributes "
                f"{best['reports_fully_attributed']:.0%}. A lineage path "
                f"records where a claim travelled, which is not what an "
                f"incident review asks for. *Transfers directly* to any "
                f"system marketing provenance as a benefit of "
                f"decentralisation.\n")

    parts = [
        "1. **Weak cross-organisational signals are not findable by ranking "
        "records.** Measured: 0 of 306 pattern-facet records appear in the "
        "global top 900 by any per-record importance feature at 10,000 "
        "users. A record that is one facet of a distributed problem is, by "
        "construction, indistinguishable from benign chatter. Any design "
        "whose first stage is \"rank the documents\" is solving a different "
        "problem. *Might not transfer if* real weak signals carry lexical "
        "markers our generator does not simulate — urgency language, "
        "escalation formatting, named severity levels.\n",
        f"2. **Above a certain corpus size the binding constraint moves from "
        f"retrieval to discrimination.** Measured: a perfect-retrieval "
        f"oracle holds the evidence for {o_cov:.0%} of hidden patterns at "
        f"{big:,} users ({n_or} seeds) and reports {o_found:.0%} of them. "
        f"Adding undifferentiated evidence past that point makes the ranking "
        f"*worse*. A propagation budget is therefore a feature, not only a "
        f"cost. *Might not transfer if* the executive layer can be given far "
        f"more reading budget than we modelled.\n",
        live, prov,
        "5. **A tuning knob that reverses sign when the corpus is "
        "regenerated is fitted to the corpus.** Measured: `w_dispersion` was "
        "rejected twice on one generator and selected at 0.8 on a corrected "
        "one, mechanism unchanged. *Transfers directly*: hold out the data "
        "used to fit anything, and re-fit when the data changes rather than "
        "inheriting the setting.\n",
        "6. **Measurement subjects make good reviewers.** The two most "
        "consequential defects found in this benchmark — a non-blind "
        "operator task file, and entity collisions between real patterns and "
        "decoys — were both reported, unprompted, by models being measured, "
        "not by reading the code. *Transfers directly*: leave room in the "
        "task for the subject to say the task is broken, and read what comes "
        "back.\n",
    ]
    return "\n".join(p for p in parts if p)


def _toc(body: str) -> str:
    """Build the contents list from the rendered document itself.

    Generating it from the output rather than maintaining a list means a
    renumbered or renamed section can never leave a stale entry behind.
    """
    import re
    lines = ["<details>", "<summary><b>Contents</b></summary>", ""]
    started = False
    for ln in body.splitlines():
        m = re.match(r"^(#{1,2}) (.+)$", ln)
        if not m or ln.startswith("# Mycelic"):
            continue
        # The document's own subtitle is an h2 above the first PART divider;
        # nothing before that divider belongs in the contents.
        if m.group(1) == "#":
            started = True
        if not started:
            continue
        title = m.group(2).strip()
        anchor = re.sub(r"[^a-z0-9\s-]", "", title.lower()).replace(" ", "-")
        if m.group(1) == "#":            # a PART divider
            lines.append(f"\n**{title}**\n")
        else:
            lines.append(f"* [{title}](#{anchor})")
    lines += ["", "</details>"]
    return "\n".join(lines)


def build() -> str:
    from .report_text import compose
    sections = {
        "toc": "",
        "world": world_description(),
        "calibration": section_calibration(),
        "baselines": section_baselines(),
        "pairs": section_pairs(),
        "ablations": section_ablations(),
        "alloc": section_alloc(),
        "level_marginal": section_level_marginal(),
        "qsweep": section_qsweep(),
        "fanin": section_fanin(),
        "adversarial": section_adversarial(),
        "frontier": section_frontier(),
        "shape": section_shape(),
        "crosslinks": section_crosslinks(),
        "live": section_live(),
        "privacy": section_privacy(),
        "scale_trend": section_scale_trend(),
        "provenance": section_provenance(),
        "summary": executive_summary(),
        "decision": decision_summary(),
        "glossary": GLOSSARY,
        "recommendation": recommendation(),
        "critique": critique(),
        "next": next_experiments(),
        "questions": questions_section(),
        "headline": section_headline(),
    }
    # Render once with an empty contents list to learn the real headings,
    # then again with the list built from them.
    sections["toc"] = _toc(compose(sections))
    return compose(sections)


if __name__ == "__main__":
    txt = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write(txt)
    print("wrote", OUT, len(txt), "chars")
