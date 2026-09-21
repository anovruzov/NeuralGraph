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

from .analysis import agg, boot_ci, load, md_table, paired
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
    rows = load("e1_baselines.jsonl") + load("e1b_extra.jsonl")
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
    rows = load("e1_baselines.jsonl") + load("e1b_extra.jsonl")
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
    return md_table(a, [("arch", "architecture", 0), ("q", "q", 2),
                        ("average_precision", "AP", 4),
                        ("found_anywhere_in_register", "found", 3),
                        ("rare_signal_recall", "rare", 3),
                        ("false_discovery_rate", "FDR", 3),
                        ("compute_units", "compute", 0)],
                    sort_by=None)


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
    for scale in sorted({r["scale"] for r in rows}):
        sub = [r for r in rows if r["scale"] == scale]
        p = paired(sub, True, False, "found_anywhere_in_register",
                   group="cross_links", key=("scale", "seed"))
        if p.get("n"):
            lines.append(f"| {scale:,} | {p['mean_diff']:+.4f} | "
                         f"[{p['ci_lo']:+.4f}, {p['ci_hi']:+.4f}] | "
                         f"{p['wins']}/{p['n_nonzero']} | {p['sign_p']:.3f} |")
    return "\n".join(lines)


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
    rows = load("e1_baselines.jsonl") + load("e1b_extra.jsonl") + \
        load("e10_scale_trend.jsonl")
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
    return "\n".join(out) if out else "_(live measurements not run)_"


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


def build() -> str:
    from .report_text import compose
    return compose({
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
    })


if __name__ == "__main__":
    txt = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write(txt)
    print("wrote", OUT, len(txt), "chars")
