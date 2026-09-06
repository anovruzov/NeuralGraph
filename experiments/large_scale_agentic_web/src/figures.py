"""Publication figures.

Design decisions worth stating, because they are the ones a reader could
otherwise mistake for arbitrary taste:

* **Nine systems is more colour than any palette can carry.** Validated against
  the six-check colour formula (``--pairs all``), seven-plus chromatic slots fail
  CVD separation outright. So the figures facet by *role* instead: the four
  equal-budget policies that the paper's claim is actually about get four
  validated hues, and the systems that serve as cost or performance envelopes
  (isolated memory, the centralized store, broad replication, gossip) are drawn
  as neutral reference lines with distinct dash patterns and direct labels.
  Identity is therefore never carried by colour alone.
* **Focus palette** ``#0059A0 #C24400 #3D8FC8 #0F7A5A`` passes lightness band,
  chroma floor, all-pairs CVD separation, normal-vision floor and 3:1 contrast
  against the chart surface.
* Marks are thin and small, the grid is recessive, and axis text wears ink
  tokens rather than series colour.
* Survival and accuracy axes always span the full [0, 1]; nothing is truncated.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

# ---------------------------------------------------------------- ink tokens
INK = "#1a1c1e"
INK_MUTED = "#5c6165"
GRID = "#dcdfe3"
SURFACE = "#ffffff"

# focus hues: the equal-budget comparison the paper is about
FOCUS = {
    "B6": "#0059A0",   # lineage-aware fabric (the proposed system)
    "B7": "#C24400",   # lineage + continual questioning
    "B3": "#3D8FC8",   # random replication, same budget
    "B5": "#0F7A5A",   # diversity-blind, same budget
    "B3Q": "#C24400",  # questioning control: same role as B7, dashed
}
# envelopes: not competitors at matched cost, so neutral
CONTEXT = {
    "B0": "#B4B8BC",
    "B1": "#15181b",
    "B2": "#6f7377",
    "B4": "#93989d",
}

STYLE = {
    "B0": (CONTEXT["B0"], "o", (0, (1, 2))),
    "B1": (CONTEXT["B1"], "s", (0, (5, 2))),
    "B2": (CONTEXT["B2"], "^", "-"),
    "B4": (CONTEXT["B4"], "D", (0, (4, 1, 1, 1))),
    "B3": (FOCUS["B3"], "v", "-"),
    "B5": (FOCUS["B5"], "P", "-"),
    "B6": (FOCUS["B6"], "o", "-"),
    "B7": (FOCUS["B7"], "*", "-"),
    "B3Q": (FOCUS["B3Q"], "X", (0, (3, 1.5))),
    "B3L": (FOCUS["B3"], "v", (0, (3, 1.5))),
    "B6C": (FOCUS["B6"], "o", (0, (1, 2))),
    "B2L": (CONTEXT["B2"], "^", (0, (3, 1.5))),
}
IS_FOCUS = {"B3", "B5", "B6", "B7", "B3Q"}

SHORT = {
    "B0": "B0 isolated", "B1": "B1 centralized", "B2": "B2 full replication",
    "B3": "B3 random repl.", "B4": "B4 gossip", "B5": "B5 diversity-blind",
    "B6": "B6 lineage", "B7": "B7 lineage+Q", "B3Q": "B3+Q (control)",
    "B3L": "random+lineage agg.", "B6C": "lineage+count agg.", "B2L": "broad+lineage agg.",
}
ORDER = ["B6", "B7", "B3", "B5", "B3Q", "B2", "B4", "B1", "B0"]

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 400, "savefig.facecolor": SURFACE,
    "font.size": 8.5, "font.family": "DejaVu Sans",
    "axes.edgecolor": "#c3c7cb", "axes.linewidth": 0.7,
    "axes.labelcolor": INK, "axes.labelsize": 8.5, "axes.titlesize": 9,
    "axes.titlecolor": INK, "axes.titleweight": "medium", "axes.titlepad": 5,
    "axes.grid": True, "axes.axisbelow": True,
    "grid.color": GRID, "grid.linewidth": 0.55,
    "xtick.color": INK_MUTED, "ytick.color": INK_MUTED,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "legend.frameon": False, "legend.fontsize": 7.5, "legend.handlelength": 2.4,
    "axes.spines.top": False, "axes.spines.right": False,
})

MS_FOCUS, MS_CONTEXT = 3.4, 2.6
LW_FOCUS, LW_CONTEXT = 1.7, 1.0


def save(fig, path: Path, also_pdf: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    if also_pdf:
        fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def subtitle(fig, text, y=1.0):
    fig.text(0.5, y, text, ha="center", va="bottom", fontsize=7.5, color=INK_MUTED)


def legend_below(fig, ax, ncol=5, y=-0.02):
    handles, labels = ax.get_legend_handles_labels()
    if not labels:
        return
    idx = sorted(range(len(labels)), key=lambda i: ORDER.index(labels[i].split()[0])
                 if labels[i].split()[0] in ORDER else 99)
    fig.legend([handles[i] for i in idx], [labels[i] for i in idx],
               loc="lower center", ncol=ncol, bbox_to_anchor=(0.5, y),
               labelcolor=INK, columnspacing=1.6, handletextpad=0.6)


def curve(ax, x, mean, lo, hi, system, band=True):
    c, mk, ls = STYLE.get(system, (INK_MUTED, "o", "-"))
    focus = system in IS_FOCUS
    ax.plot(x, mean, color=c, marker=mk, linestyle=ls,
            markersize=MS_FOCUS if focus else MS_CONTEXT,
            linewidth=LW_FOCUS if focus else LW_CONTEXT,
            markeredgewidth=0, zorder=4 if focus else 2,
            label=SHORT.get(system, system))
    if band and focus:
        ax.fill_between(x, lo, hi, color=c, alpha=0.16, linewidth=0, zorder=1)


# ---------------------------------------------------------------------------

def money_figure(data, systems, regimes, regime_titles, metrics, metric_titles,
                 n_agents, out: Path, n_seeds=None):
    """Failure severity against survival and accuracy, one column per regime."""
    nrow, ncol = len(metrics), len(regimes)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.15 * ncol, 2.45 * nrow),
                             sharex=True, sharey="row", squeeze=False)
    for i, metric in enumerate(metrics):
        for j, reg in enumerate(regimes):
            ax = axes[i][j]
            for s in systems:
                pts = data.get((reg, s))
                if not pts:
                    continue
                x = np.array([p["severity"] for p in pts])
                o = np.argsort(x)
                curve(ax, x[o],
                      np.array([p[metric]["mean"] for p in pts])[o],
                      np.array([p[metric]["ci_low"] for p in pts])[o],
                      np.array([p[metric]["ci_high"] for p in pts])[o], s)
            ax.set_ylim(0, 1.0)
            ax.set_xlim(-0.03, 0.93)
            ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
            if i == 0:
                ax.set_title(regime_titles[j])
            if i == nrow - 1:
                ax.set_xlabel("agents removed")
            if j == 0:
                ax.set_ylabel(metric_titles[i])
    legend_below(fig, axes[0][0], ncol=5, y=-0.10)
    subtitle(fig, f"N = {n_agents:,} agents"
             + (f" · {n_seeds} seeds · bands are 95% CI" if n_seeds else ""), y=1.005)
    fig.tight_layout()
    save(fig, out)


def frontier_figure(points, sweep, out: Path, n_agents, severity_label,
                    n_seeds=None, per_seed=None):
    """Left: resilience against cost. Right: every seed, at matched budget.

    The seed-to-seed spread is far narrower than the [0, 1] survival axis, so it
    is invisible on the frontier panel; the right panel plots each run as its own
    point on a zoomed axis instead of hiding the distribution behind one marker.
    """
    per_seed = per_seed or {}
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.4),
                             gridspec_kw={"width_ratios": [1.3, 1.0]})

    # ---- frontier ---------------------------------------------------------
    ax = axes[0]
    xkey = "total_cost_per_claim"
    for name, pts in sweep.items():
        xs = np.array([p[xkey] for p in pts], float)
        ys = np.array([p["knowledge_survival"]["mean"] for p in pts], float)
        o = np.argsort(xs)
        ax.plot(xs[o], ys[o], color=STYLE.get(name, (INK_MUTED,))[0],
                linestyle=(0, (4, 2)), linewidth=0.9, alpha=0.5, zorder=1)
    groups: dict[float, list] = {}
    for p in points:
        if p[xkey] <= 0:          # B0 publishes nothing: no place on a log axis
            continue
        groups.setdefault(round(np.log10(p[xkey]), 2), []).append(p)
    for _, members in groups.items():
        n = len(members)
        for rank, p in enumerate(members):
            sid = p["system"]
            c, mk, _ = STYLE.get(sid, (INK_MUTED, "o", "-"))
            xc = p[xkey] * (1.0 + 0.045 * (rank - (n - 1) / 2.0))
            ax.plot([xc], [p["knowledge_survival"]["mean"]], marker=mk,
                    markersize=6.0 if mk == "*" else 4.6, color=c,
                    markeredgecolor=SURFACE, markeredgewidth=0.7,
                    linestyle="none", zorder=5, label=SHORT.get(sid, sid))
        lead = members[0]
        ax.annotate("=".join(m["system"] for m in members),
                    (lead[xkey], lead["knowledge_survival"]["mean"]),
                    textcoords="offset points", xytext=(0, 11), ha="center",
                    fontsize=7, color=INK)
    ax.set_xscale("log")
    ax.set_xlabel("storage + communication per claim")
    ax.set_ylabel("knowledge survival")
    ax.set_ylim(0, 1.0)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_title("Resilience against cost")

    # ---- per-seed strip ---------------------------------------------------
    ax2 = axes[1]
    strip = [s_ for s_ in ["B3", "B5", "B6", "B7", "B3Q", "B4", "B2"] if s_ in per_seed]
    rng = np.random.default_rng(7)
    lo_all, hi_all, n_dots = 1.0, 0.0, 0
    for i, sid in enumerate(strip):
        vals = np.asarray(per_seed[sid], float)
        if not vals.size:
            continue
        n_dots += vals.size
        c = STYLE.get(sid, (INK_MUTED,))[0]
        jitter = (rng.random(vals.size) - 0.5) * 0.42
        ax2.plot(i + jitter, vals, linestyle="none", marker="o", markersize=2.1,
                 color=c, alpha=0.55, markeredgewidth=0, zorder=3)
        ax2.plot([i - 0.32, i + 0.32], [vals.mean()] * 2, color=c,
                 linewidth=1.8, solid_capstyle="round", zorder=4)
        lo_all, hi_all = min(lo_all, vals.min()), max(hi_all, vals.max())
    pad = 0.05 * max(hi_all - lo_all, 1e-3)
    ax2.set_ylim(lo_all - pad, hi_all + pad)
    ax2.set_xticks(range(len(strip)))
    ax2.set_xticklabels(strip)
    ax2.set_xlim(-0.6, len(strip) - 0.4)
    ax2.grid(axis="x", visible=False)
    ax2.set_ylabel("knowledge survival  (zoomed)")
    ax2.set_title(f"Every seed, one dot ({n_dots} runs)")

    legend_below(fig, axes[0], ncol=5, y=-0.09)
    subtitle(fig, f"N = {n_agents:,} · {severity_label}"
             + (f" · {n_seeds} seeds" if n_seeds else "")
             + " · left: dashed lines sweep the storage budget k; markers dodged "
               "horizontally where systems coincide", y=1.02)
    fig.tight_layout()
    save(fig, out)


def scaling_figure(scaling, out: Path):
    keys = [("storage_per_claim", "storage per claim"),
            ("messages_per_claim", "messages per claim"),
            ("mean_probe_rounds", "retrieval rounds"),
            ("knowledge_survival", "knowledge survival")]
    fig, axes = plt.subplots(1, 4, figsize=(12.2, 2.8))
    for ax, (k, title) in zip(axes, keys):
        for s, pts in scaling.items():
            n = np.array([p["n_agents"] for p in pts], float)
            o = np.argsort(n)
            curve(ax, n[o],
                  np.array([p[k]["mean"] for p in pts], float)[o],
                  np.array([p[k]["ci_low"] for p in pts], float)[o],
                  np.array([p[k]["ci_high"] for p in pts], float)[o], s, band=False)
        ax.set_xscale("log")
        if k == "knowledge_survival":
            ax.set_ylim(0, 1)
        else:
            ax.set_yscale("log")
        ax.set_xlabel("agents N")
        ax.set_title(title)
    legend_below(fig, axes[0], ncol=7, y=-0.12)
    subtitle(fig, "measured at 50% random churn · empirical trend over four scales, "
                  "not an asymptotic claim", y=1.02)
    fig.tight_layout()
    save(fig, out)


def corruption_figure(data, systems, out: Path, n_agents, n_seeds=None):
    kinds = [("node_coordinated", "Coordinated node corruption"),
             ("root", "Origin-source corruption"),
             ("node_uncoordinated", "Uncoordinated node corruption")]
    fig, axes = plt.subplots(1, len(kinds), figsize=(3.5 * len(kinds), 2.9), sharey=True)
    for ax, (kind, title) in zip(axes, kinds):
        for s in systems:
            pts = data.get((kind, s))
            if not pts:
                continue
            x = np.array([p["corruption_level"] for p in pts])
            o = np.argsort(x)
            curve(ax, x[o],
                  np.array([p["knowledge_survival"]["mean"] for p in pts])[o],
                  np.array([p["knowledge_survival"]["ci_low"] for p in pts])[o],
                  np.array([p["knowledge_survival"]["ci_high"] for p in pts])[o], s)
        ax.set_xlabel("fraction corrupted")
        ax.set_title(title)
        ax.set_ylim(0, 1)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    axes[0].set_ylabel("knowledge survival")
    legend_below(fig, axes[0], ncol=5, y=-0.10)
    subtitle(fig, f"N = {n_agents:,} · on top of 30% random churn"
             + (f" · {n_seeds} seeds" if n_seeds else ""), y=1.02)
    fig.tight_layout()
    save(fig, out)


def class_figure(data, systems, out: Path, n_agents, n_seeds=None):
    """One origin with 100 copies against three independent origins."""
    fig, axes = plt.subplots(1, 3, figsize=(11.0, 2.9))
    panels = [("acc_class_correlated", "acc_class_independent", "Task accuracy"),
              ("iss_class_correlated", "iss_class_independent", "Independent-support survival"),
              ("ece_class_correlated", "ece_class_independent", "Calibration error (lower better)")]
    width = 0.34
    xs = np.arange(len(systems))
    c_corr, c_indep = "#93989d", "#0059A0"
    for ax, (kc, ki, title) in zip(axes, panels):
        mc = [data[s][kc]["mean"] for s in systems]
        mi = [data[s][ki]["mean"] for s in systems]
        ec = [[data[s][kc]["mean"] - data[s][kc]["ci_low"] for s in systems],
              [data[s][kc]["ci_high"] - data[s][kc]["mean"] for s in systems]]
        ei = [[data[s][ki]["mean"] - data[s][ki]["ci_low"] for s in systems],
              [data[s][ki]["ci_high"] - data[s][ki]["mean"] for s in systems]]
        ax.bar(xs - width / 2 - 0.01, mc, width, yerr=ec, capsize=1.8, color=c_corr,
               linewidth=0, error_kw={"elinewidth": 0.8, "ecolor": INK_MUTED},
               label="1 origin, 100 copies")
        ax.bar(xs + width / 2 + 0.01, mi, width, yerr=ei, capsize=1.8, color=c_indep,
               linewidth=0, error_kw={"elinewidth": 0.8, "ecolor": INK_MUTED},
               label="3 origins, 1 copy each")
        ax.set_xticks(xs)
        ax.set_xticklabels(systems)
        ax.set_title(title)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylim(0, 1)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.08),
               labelcolor=INK)
    subtitle(fig, f"N = {n_agents:,} · 50% targeted white-box attack"
             + (f" · {n_seeds} seeds" if n_seeds else ""), y=1.02)
    fig.tight_layout()
    save(fig, out)


def questioning_figure(budget_curves, out: Path, n_agents, n_seeds=None):
    fig, axes = plt.subplots(1, 3, figsize=(11.0, 2.9))
    panels = [("accuracy_macro", "Task accuracy"),
              ("acc_revision_sensitive", "Stale-fact accuracy"),
              ("messages_per_claim", "Messages per claim")]
    palette = {"B6 lineage": FOCUS["B6"], "B7 lineage+Q": FOCUS["B7"],
               "B3 random repl.": FOCUS["B3"], "B3+Q (control)": FOCUS["B5"]}
    for ax, (metric, title) in zip(axes, panels):
        for name, pts in budget_curves.items():
            x = np.array([p["question_budget"] for p in pts], float)
            o = np.argsort(x)
            c = palette.get(name, INK_MUTED)
            ls = "-" if "+Q" in name else (0, (4, 2))
            ax.plot(x[o], np.array([p[metric]["mean"] for p in pts], float)[o],
                    color=c, linestyle=ls, marker="o", markersize=3.0,
                    markeredgewidth=0, linewidth=1.6, label=name)
            ax.fill_between(x[o],
                            np.array([p[metric]["ci_low"] for p in pts], float)[o],
                            np.array([p[metric]["ci_high"] for p in pts], float)[o],
                            color=c, alpha=0.14, linewidth=0)
        ax.set_xlabel("questions asked per claim")
        ax.set_title(title)
        if metric != "messages_per_claim":
            ax.set_ylim(0, 1)
        else:
            ax.set_yscale("log")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.10),
               labelcolor=INK)
    subtitle(fig, f"N = {n_agents:,} · 50% correlated failure"
             + (f" · {n_seeds} seeds, bands 95% CI" if n_seeds else ""), y=1.02)
    fig.tight_layout()
    save(fig, out)


def insert_figure(sev_data, points, sweep, per_seed, out: Path,
                  n_agents, n_seeds, regime_key="targeted_whitebox"):
    """Compact three-panel figure for the one-page camera-ready insert."""
    per_seed = per_seed or {}
    fig, axes = plt.subplots(1, 3, figsize=(6.9, 2.05),
                             gridspec_kw={"width_ratios": [1.0, 1.05, 1.0]})

    ax = axes[0]
    for s in ["B0", "B1", "B2", "B4", "B3", "B5", "B6", "B7"]:
        pts = sev_data.get((regime_key, s))
        if not pts:
            continue
        x = np.array([p["severity"] for p in pts])
        o = np.argsort(x)
        curve(ax, x[o],
              np.array([p["knowledge_survival"]["mean"] for p in pts])[o],
              np.array([p["knowledge_survival"]["ci_low"] for p in pts])[o],
              np.array([p["knowledge_survival"]["ci_high"] for p in pts])[o], s)
    ax.set_ylim(0, 1.0); ax.set_xlim(0.05, 0.93)
    ax.set_yticks([0, 0.5, 1.0])
    ax.set_xlabel("agents removed")
    ax.set_ylabel("knowledge survival")
    ax.set_title("(a) Targeted attack")

    ax = axes[1]
    xkey = "total_cost_per_claim"
    for name, pts in sweep.items():
        xs = np.array([p[xkey] for p in pts], float)
        ys = np.array([p["knowledge_survival"]["mean"] for p in pts], float)
        o = np.argsort(xs)
        ax.plot(xs[o], ys[o], color=STYLE.get(name, (INK_MUTED,))[0],
                linestyle=(0, (4, 2)), linewidth=0.8, alpha=0.5, zorder=1)
    groups: dict[float, list] = {}
    for p in points:
        if p[xkey] > 0:
            groups.setdefault(round(np.log10(p[xkey]), 2), []).append(p)
    for _, members in groups.items():
        n = len(members)
        for rank, p in enumerate(members):
            sid = p["system"]
            c, mk, _ = STYLE.get(sid, (INK_MUTED, "o", "-"))
            xc = p[xkey] * (1.0 + 0.05 * (rank - (n - 1) / 2.0))
            ax.plot([xc], [p["knowledge_survival"]["mean"]], marker=mk,
                    markersize=5.4 if mk == "*" else 4.0, color=c,
                    markeredgecolor=SURFACE, markeredgewidth=0.6,
                    linestyle="none", zorder=5)
        lead = members[0]
        ax.annotate("=".join(m["system"] for m in members),
                    (lead[xkey], lead["knowledge_survival"]["mean"]),
                    textcoords="offset points", xytext=(0, 9), ha="center",
                    fontsize=6, color=INK)
    ax.set_xscale("log"); ax.set_ylim(0, 1.0); ax.set_yticks([0, 0.5, 1.0])
    ax.set_xlabel("storage + comms per claim")
    ax.set_title("(b) Resilience vs cost")

    ax = axes[2]
    strip = [s_ for s_ in ["B3", "B5", "B6", "B7", "B4", "B2"] if s_ in per_seed]
    rng = np.random.default_rng(7)
    lo_all, hi_all, n_dots = 1.0, 0.0, 0
    for i, sid in enumerate(strip):
        vals = np.asarray(per_seed[sid], float)
        if not vals.size:
            continue
        n_dots += vals.size
        c = STYLE.get(sid, (INK_MUTED,))[0]
        ax.plot(i + (rng.random(vals.size) - 0.5) * 0.42, vals, linestyle="none",
                marker="o", markersize=1.5, color=c, alpha=0.55,
                markeredgewidth=0, zorder=3)
        ax.plot([i - 0.3, i + 0.3], [vals.mean()] * 2, color=c, linewidth=1.5, zorder=4)
        lo_all, hi_all = min(lo_all, vals.min()), max(hi_all, vals.max())
    pad = 0.05 * max(hi_all - lo_all, 1e-3)
    ax.set_ylim(lo_all - pad, hi_all + pad)
    ax.set_xticks(range(len(strip))); ax.set_xticklabels(strip, fontsize=7)
    ax.set_xlim(-0.6, len(strip) - 0.4)
    ax.grid(axis="x", visible=False)
    ax.set_title(f"(c) Every seed ({n_dots} runs)")

    handles = [Line2D([], [], color=STYLE[s][0], marker=STYLE[s][1],
                      linestyle=STYLE[s][2], markersize=3.2,
                      linewidth=1.4 if s in IS_FOCUS else 0.9, label=SHORT[s])
               for s in ["B6", "B7", "B3", "B5", "B2", "B4", "B1", "B0"]]
    fig.legend(handles=handles, loc="lower center", ncol=8, bbox_to_anchor=(0.5, -0.17),
               labelcolor=INK, fontsize=6.4, handlelength=2.0, columnspacing=1.0,
               handletextpad=0.4)
    fig.tight_layout()
    save(fig, out, also_pdf=True)
