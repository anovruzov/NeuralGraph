"""Figures, generated from the raw rows.

Deliberately plain: no styling that could make a difference look bigger than
it is, error bars everywhere there are seeds to compute them from, and log
axes labelled as such.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .analysis import agg, boot_ci, dedupe, load  # noqa: E402
from .runner import ART  # noqa: E402

FIG = os.path.join(ART, "figures")
os.makedirs(FIG, exist_ok=True)

ORDER = ["A_flat_rag", "A2_chunked_ctx", "B_long_context", "B2_map_reduce",
         "B4_central_triage", "C_recursive_sum", "D_hier_nolineage",
         "E_hier_lineage", "F_hier_retrieval", "G_hier_questions",
         "H_mycelic_full", "I_mycelic_completion", "J_mycelic_verified",
         "Y_oracle_retrieval", "Z_random_rank", "Z2_naive_enumerate"]
COLORS = plt.get_cmap("tab20")


def _color(a: str):
    return COLORS(ORDER.index(a) % 20) if a in ORDER else "gray"


def _style(ax, title, xlabel, ylabel):
    ax.set_title(title, fontsize=11)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(alpha=0.25, linewidth=0.5)
    ax.tick_params(labelsize=8)


def fig_scale() -> Optional[str]:
    rows = dedupe(load("e1_baselines.jsonl") + load("e1b_extra.jsonl")
                  + load("e10_scale_trend.jsonl"))
    if not rows:
        return None
    scales = sorted({r["scale"] for r in rows})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for metric, ax, lab in ((
            "found_anywhere_in_register", axes[0], "discovery (found)"),
            ("evidence_coverage_2links", axes[1], "evidence coverage")):
        for a in ORDER:
            xs, ys, los, his = [], [], [], []
            for s in scales:
                v = [r[metric] for r in rows
                     if r["arch"] == a and r["scale"] == s]
                if not v:
                    continue
                m, lo, hi = boot_ci(v)
                xs.append(s); ys.append(m); los.append(m - lo); his.append(hi - m)
            if not xs:
                continue
            ax.errorbar(xs, ys, yerr=[los, his], marker="o", ms=3.5,
                        lw=1.2, capsize=2, label=a, color=_color(a))
        ax.set_xscale("log")
        _style(ax, lab + " vs enterprise size", "users (log)", lab)
    axes[0].legend(fontsize=6.0, ncol=2, loc="lower left")
    fig.tight_layout()
    p = os.path.join(FIG, "scale.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_frontier() -> Optional[str]:
    rows = dedupe(load("e1_baselines.jsonl") + load("e1b_extra.jsonl"))
    fr = load("e6_frontier.jsonl")
    if not rows:
        return None
    scales = sorted({r["scale"] for r in rows})
    fig, axes = plt.subplots(1, len(scales), figsize=(4.2 * len(scales), 4.2),
                             squeeze=False)
    for i, s in enumerate(scales):
        ax = axes[0][i]
        for a in ORDER:
            v = [(r["compute_units"], r["found_anywhere_in_register"])
                 for r in rows if r["arch"] == a and r["scale"] == s]
            if not v:
                continue
            x = float(np.mean([q[0] for q in v]))
            y = float(np.mean([q[1] for q in v]))
            ax.scatter([x], [y], s=26, color=_color(a), zorder=3)
            ax.annotate(a.split("_")[0], (x, y), fontsize=6,
                        xytext=(3, 3), textcoords="offset points")
        sub = [r for r in fr if r["scale"] == s and r["knob"] == "question_frac"]
        if sub:
            a2 = agg(sub, ["compute_units", "found_anywhere_in_register"],
                     by=("question_frac",))
            a2.sort(key=lambda r: r["compute_units"])
            ax.plot([r["compute_units"] for r in a2],
                    [r["found_anywhere_in_register"] for r in a2],
                    "-", lw=1.0, color="black", alpha=0.6,
                    label="hierarchy question budget")
            ax.legend(fontsize=6)
        ax.set_xscale("log")
        _style(ax, f"{s:,} users", "compute units (log)", "discovery (found)")
    fig.tight_layout()
    p = os.path.join(FIG, "cost_quality.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_level_marginal() -> Optional[str]:
    rows = load("e3b_level_marginal.jsonl")
    if not rows:
        return None
    levels = ["user", "team", "dept", "site", "region", "enterprise"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    w = 0.38
    archs = sorted({r["arch"] for r in rows})
    for j, a in enumerate(archs):
        sub = [r for r in rows if r["arch"] == a]
        base = [r["found_anywhere_in_register"] for r in sub
                if r["upgraded_level"] == "none"]
        b = float(np.mean(base)) if base else 0.0
        ys, los, his = [], [], []
        for lv in levels:
            v = [r["found_anywhere_in_register"] - b for r in sub
                 if r["upgraded_level"] == lv]
            if v:
                m, lo, hi = boot_ci(v)
                ys.append(m); los.append(m - lo); his.append(hi - m)
            else:
                ys.append(0.0); los.append(0.0); his.append(0.0)
        ax.bar(np.arange(len(levels)) + j * w, ys, width=w, label=a,
               yerr=[los, his], capsize=2, color=COLORS(j * 4))
    ax.set_xticks(np.arange(len(levels)) + w / 2)
    ax.set_xticklabels(levels, fontsize=8)
    ax.axhline(0, color="black", lw=0.8)
    _style(ax, "Marginal value of upgrading ONE level to a frontier model\n"
               "(baseline: every level small-7b)", "level upgraded",
           "Δ discovery vs baseline")
    ax.legend(fontsize=7)
    fig.tight_layout()
    p = os.path.join(FIG, "level_marginal.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_qsweep() -> Optional[str]:
    rows = load("e3c_q_sweep.jsonl")
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for a in sorted({r["arch"] for r in rows}):
        sub = [r for r in rows if r["arch"] == a]
        qs = sorted({r["q"] for r in sub})
        ys, los, his = [], [], []
        for q in qs:
            v = [r["found_anywhere_in_register"] for r in sub if r["q"] == q]
            m, lo, hi = boot_ci(v)
            ys.append(m); los.append(m - lo); his.append(hi - m)
        ax.errorbar(qs, ys, yerr=[los, his], marker="o", ms=3.5, lw=1.2,
                    capsize=2, label=a, color=_color(a))
    _style(ax, "Operator quality → architecture quality\n"
               "(every level at the same capability q)",
           "capability q (all levels)", "discovery (found)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    p = os.path.join(FIG, "q_sweep.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_discrimination() -> Optional[str]:
    a = os.path.join(ART, "live_rank_results.json")
    b = os.path.join(ART, "live_rank_results_rich.json")
    if not (os.path.exists(a) and os.path.exists(b)):
        return None
    d, dr = json.load(open(a)), json.load(open(b))

    def _cell(dd, m):
        """(mean, lower err, upper err) across repeats, NaN if absent."""
        ag = dd.get("models_aggregated", {}).get(m)
        if ag:
            return (ag["ap_mean"], ag["ap_mean"] - ag["ap_min"],
                    ag["ap_max"] - ag["ap_mean"])
        v = dd.get("models", {}).get(m)
        return (v["ap"], 0.0, 0.0) if v else (np.nan, 0.0, 0.0)

    keys = (set(d.get("models_aggregated", d.get("models", {})))
            | set(dr.get("models_aggregated", dr.get("models", {}))))
    names = sorted(keys)
    stats = [_cell(d, m) for m in names]
    rich = [_cell(dr, m) for m in names]
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    x = np.arange(len(names))
    # The error bar is the FULL RANGE over independent repeats of the same
    # task with the same model, not a CI: with 2-3 runs a CI would be
    # meaningless, and the range is the honest statement of the spread.
    ax.bar(x - 0.2, [c[0] for c in stats], 0.4,
           yerr=[[c[1] for c in stats], [c[2] for c in stats]], capsize=3,
           label="evidence statistics only", color=COLORS(0))
    ax.bar(x + 0.2, [c[0] for c in rich], 0.4,
           yerr=[[c[1] for c in rich], [c[2] for c in rich]], capsize=3,
           label="statistics + raw work notes", color=COLORS(4))
    ax.axhline(d["random_ap"], color="gray", ls=":", lw=1.2,
               label=f"random ({d['random_ap']:.3f})")
    ax.axhline(d["simulator_logistic_ap"], color="black", ls="--", lw=1.2,
               label=f"simulator logistic ({d['simulator_logistic_ap']:.3f})")
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=8)
    _style(ax, f"Directly measured: candidate discrimination\n"
               f"({d['n_items']} candidates from a real run, {d['n_real']} "
               f"genuine; bars = mean, whiskers = full range over repeats)",
           "model", "average precision")
    ax.legend(fontsize=7)
    fig.tight_layout()
    p = os.path.join(FIG, "discrimination.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_ablation() -> Optional[str]:
    rows = load("e2_ablations.jsonl")
    if not rows:
        return None
    a = agg(rows, ["found_anywhere_in_register", "average_precision"],
            by=("ablation",))
    base = [r for r in a if r["ablation"] == "full"]
    if not base:
        return None
    b = base[0]["found_anywhere_in_register"]
    a = [r for r in a if r["ablation"] != "full"]
    a.sort(key=lambda r: r["found_anywhere_in_register"] - b)
    fig, ax = plt.subplots(figsize=(7.6, max(3.4, 0.30 * len(a) + 1.4)))
    ys = [r["found_anywhere_in_register"] - b for r in a]
    cols = ["#b2182b" if y < 0 else "#2166ac" for y in ys]
    ax.barh(np.arange(len(a)), ys, color=cols)
    ax.set_yticks(np.arange(len(a)))
    ax.set_yticklabels([r["ablation"] for r in a], fontsize=8)
    ax.axvline(0, color="black", lw=0.8)
    _style(ax, "Ablations: change in discovery vs the full system",
           "Δ discovery (found)", "")
    fig.tight_layout()
    p = os.path.join(FIG, "ablations.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_privacy() -> Optional[str]:
    rows = load("e9_privacy.jsonl")
    if not rows:
        return None
    big = max({r["scale"] for r in rows})
    a = agg([r for r in rows if r["scale"] == big],
            ["raw_text_exposure_fraction", "claim_exposure_fraction",
             "found_anywhere_in_register"], by=("arch",))
    a.sort(key=lambda r: -r["found_anywhere_in_register"])
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for r in a:
        ax.scatter([max(1e-4, r["raw_text_exposure_fraction"])],
                   [r["found_anywhere_in_register"]], s=40,
                   color=_color(r["arch"]), zorder=3)
        ax.annotate(r["arch"].split("_")[0],
                    (max(1e-4, r["raw_text_exposure_fraction"]),
                     r["found_anywhere_in_register"]),
                    fontsize=6.5, xytext=(4, 3), textcoords="offset points")
    ax.set_xscale("log")
    _style(ax, f"Discovery vs confidentiality cost at {big:,} users\n"
               "(x: fraction of records whose ORIGINAL TEXT left its owner; "
               "1e-4 = none)",
           "raw-text exposure (log)", "discovery (found)")
    fig.tight_layout()
    p = os.path.join(FIG, "privacy.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_adversarial() -> Optional[str]:
    rows = load("e5_adversarial.jsonl")
    if not rows:
        return None
    archs = sorted({r["arch"] for r in rows})
    a = agg(rows, ["found_anywhere_in_register", "false_discovery_rate",
                   "decoy_acceptance_all"], by=("condition", "arch"))
    base = {r["arch"]: r for r in a if r["condition"] == "clean"}
    conds = [c for c in sorted({r["condition"] for r in a}) if c != "clean"]
    if not conds:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(13.0, max(3.6, 0.32 * len(conds) + 1.8)))
    y = np.arange(len(conds))
    h = 0.8 / max(1, len(archs))
    for j, arch in enumerate(archs):
        d_found, d_fdr = [], []
        for c in conds:
            row = [r for r in a if r["condition"] == c and r["arch"] == arch]
            b = base.get(arch)
            if row and b:
                d_found.append(row[0]["found_anywhere_in_register"]
                               - b["found_anywhere_in_register"])
                d_fdr.append(row[0]["false_discovery_rate"]
                             - b["false_discovery_rate"])
            else:
                d_found.append(0.0); d_fdr.append(0.0)
        axes[0].barh(y + j * h, d_found, height=h, label=arch,
                     color=_color(arch))
        axes[1].barh(y + j * h, d_fdr, height=h, label=arch, color=_color(arch))
    for ax, lab in ((axes[0], "Δ discovery (found) vs that architecture's "
                              "own clean baseline"),
                    (axes[1], "Δ false-discovery rate vs own clean baseline")):
        ax.set_yticks(y + 0.4 - h / 2)
        ax.set_yticklabels(conds, fontsize=7.5)
        ax.axvline(0, color="black", lw=0.8)
        _style(ax, lab, "Δ", "")
    axes[0].legend(fontsize=6.5, loc="lower left")
    fig.tight_layout()
    p = os.path.join(FIG, "adversarial.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_fanin() -> Optional[str]:
    rows = load("e4_fanin.jsonl")
    rows_d = load("e4b_dept_fanin.jsonl")
    if not (rows or rows_d):
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.2))
    for ax, rs, xkey, xlab in ((axes[0], rows, "team_size", "users per team"),
                               (axes[1], rows_d, "teams_per_dept",
                                "teams per department")):
        if not rs:
            ax.text(0.5, 0.5, "(not run)", ha="center", transform=ax.transAxes)
            _style(ax, "", xlab, "discovery (found)")
            continue
        for arch in sorted({r["arch"] for r in rs}):
            sub = [r for r in rs if r["arch"] == arch]
            xs = sorted({r[xkey] for r in sub})
            ys, los, his = [], [], []
            for x in xs:
                v = [r["found_anywhere_in_register"] for r in sub
                     if r[xkey] == x]
                m, lo, hi = boot_ci(v)
                ys.append(m); los.append(m - lo); his.append(hi - m)
            ax.errorbar(xs, ys, yerr=[los, his], marker="o", ms=4, lw=1.3,
                        capsize=3, label=arch, color=_color(arch))
        ax.set_ylim(0, None)
        _style(ax, "Fan-in vs strategic discovery", xlab, "discovery (found)")
        ax.legend(fontsize=7)
    fig.tight_layout()
    p = os.path.join(FIG, "fanin.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


ALL = [fig_scale, fig_frontier, fig_level_marginal, fig_qsweep,
       fig_discrimination, fig_ablation, fig_privacy, fig_adversarial,
       fig_fanin]


def build() -> List[str]:
    out = []
    for f in ALL:
        try:
            p = f()
        except Exception as e:  # pragma: no cover
            print(f"  {f.__name__}: FAILED {e}")
            continue
        if p:
            out.append(p)
            print(f"  {f.__name__}: {os.path.relpath(p)}")
        else:
            print(f"  {f.__name__}: (no data)")
    return out


if __name__ == "__main__":
    build()
