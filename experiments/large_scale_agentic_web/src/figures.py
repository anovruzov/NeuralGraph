"""Publication figures.  Axes are never truncated to exaggerate differences:
every knowledge-survival / accuracy axis runs the full [0, 1] range.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# colourblind-safe (Okabe-Ito) assignment, fixed across every figure
STYLE = {
    "B0": ("#999999", "o", ":"),
    "B1": ("#000000", "s", "--"),
    "B2": ("#E69F00", "^", "-"),
    "B3": ("#56B4E9", "v", "-"),
    "B4": ("#009E73", "D", "-"),
    "B5": ("#CC79A7", "P", "-"),
    "B6": ("#0072B2", "o", "-"),
    "B7": ("#D55E00", "*", "-"),
    "B3Q": ("#7570B3", "X", "--"),
    "B3L": ("#56B4E9", "v", "--"),
    "B6C": ("#0072B2", "o", ":"),
    "B2L": ("#E69F00", "^", "--"),
}
SHORT = {
    "B0": "B0 isolated", "B1": "B1 centralized", "B2": "B2 full replication",
    "B3": "B3 random repl.", "B4": "B4 gossip", "B5": "B5 diversity-blind",
    "B6": "B6 lineage", "B7": "B7 lineage+questions", "B3Q": "B3+questions (abl.)",
    "B3L": "random+lineage agg.", "B6C": "lineage+count agg.", "B2L": "full repl.+lineage agg.",
}

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 300, "font.size": 9,
    "axes.grid": True, "grid.alpha": 0.25, "axes.axisbelow": True,
    "legend.frameon": False, "axes.spines.top": False, "axes.spines.right": False,
})


def save(fig, path: Path, also_pdf: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    if also_pdf:
        fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def curve(ax, x, mean, lo, hi, system, label=None):
    c, mk, ls = STYLE.get(system, ("#444444", "o", "-"))
    ax.plot(x, mean, color=c, marker=mk, linestyle=ls, markersize=4,
            linewidth=1.5, label=label or SHORT.get(system, system))
    ax.fill_between(x, lo, hi, color=c, alpha=0.15, linewidth=0)


def money_figure(data, systems, regimes, regime_titles, metrics, metric_titles,
                 n_agents, out: Path, n_seeds=None):
    """Failure severity (x) vs survival / accuracy (y), one column per regime."""
    nrow, ncol = len(metrics), len(regimes)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.6 * ncol, 2.9 * nrow),
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
                m = np.array([p[metric]["mean"] for p in pts])[o]
                lo = np.array([p[metric]["ci_low"] for p in pts])[o]
                hi = np.array([p[metric]["ci_high"] for p in pts])[o]
                curve(ax, x[o], m, lo, hi, s)
            ax.set_ylim(0, 1.0)
            ax.set_xlim(-0.02, 0.92)
            if i == 0:
                ax.set_title(regime_titles[j], fontsize=9)
            if i == nrow - 1:
                ax.set_xlabel("fraction of agents removed")
            if j == 0:
                ax.set_ylabel(metric_titles[i])
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(5, len(labels)),
               bbox_to_anchor=(0.5, -0.06))
    sub = f"N = {n_agents:,} agents" + (f", {n_seeds} seeds, 95% CI" if n_seeds else "")
    fig.suptitle(f"Knowledge survival under escalating failure ({sub})", y=1.005, fontsize=10)
    fig.tight_layout()
    save(fig, out)


def frontier_figure(points, sweep, out: Path, n_agents, severity_label, n_seeds=None):
    """Resilience vs cost: is the proposed architecture on a better frontier?"""
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.9))
    for ax, (xkey, xlabel) in zip(axes, [
            ("storage_per_claim", "storage cost (published replicas per claim)"),
            ("total_cost_per_claim", "storage + communication cost per claim")]):
        for name, pts in sweep.items():
            xs = [p[xkey] for p in pts]
            ys = [p["knowledge_survival"]["mean"] for p in pts]
            o = np.argsort(xs)
            c, mk, ls = STYLE.get(name, ("#444444", "o", "-"))
            ax.plot(np.array(xs)[o], np.array(ys)[o], color=c, linestyle="--",
                    linewidth=1.0, alpha=0.7, zorder=1)
        # the equal-budget policies land on the same (cost, survival) point --
        # that coincidence is itself the result -- so co-located systems get one
        # shared label rather than three labels drawn on top of each other
        groups: dict[tuple, list] = {}
        for p in points:
            key = (round(np.log10(max(p[xkey], 1e-9)), 2),
                   round(p["knowledge_survival"]["mean"], 2))
            groups.setdefault(key, []).append(p)
        for key, members in groups.items():
            for rank, p in enumerate(members):
                sid = p["system"]
                c, mk, _ = STYLE.get(sid, ("#444444", "o", "-"))
                y = p["knowledge_survival"]["mean"]
                lo, hi = p["knowledge_survival"]["ci_low"], p["knowledge_survival"]["ci_high"]
                ax.errorbar(p[xkey], y, yerr=[[y - lo], [hi - y]], color=c, marker=mk,
                            markersize=max(4.5, 11.0 - 2.0 * rank), capsize=3,
                            markerfacecolor="none" if rank else c, markeredgewidth=1.5,
                            linestyle="none", zorder=3 + rank, label=SHORT.get(sid, sid))
            lead = members[0]
            label = " = ".join(m["system"] for m in members)
            colour = STYLE.get(lead["system"], ("#444444",))[0] if len(members) == 1 else "#333333"
            ax.annotate(label, (lead[xkey], lead["knowledge_survival"]["mean"]),
                        textcoords="offset points", xytext=(9, 7), fontsize=7.5, color=colour)
        ax.set_xscale("log")
        ax.set_xlabel(xlabel)
        ax.set_ylim(0, 1.0)
    axes[0].set_ylabel("knowledge survival")
    handles, labels = axes[0].get_legend_handles_labels()
    if labels:
        fig.legend(handles, labels, loc="lower center", ncol=min(5, len(labels)),
                   bbox_to_anchor=(0.5, -0.10))
    sub = f"N = {n_agents:,}, {severity_label}" + (f", {n_seeds} seeds, 95% CI" if n_seeds else "")
    fig.suptitle(f"Resilience-efficiency frontier ({sub}); dashed lines sweep the storage budget k",
                 y=1.02, fontsize=10)
    fig.tight_layout()
    save(fig, out)


def scaling_figure(scaling, out: Path):
    keys = [("storage_per_claim", "storage per claim"),
            ("messages_per_claim", "messages per claim"),
            ("mean_probe_rounds", "retrieval rounds per query"),
            ("knowledge_survival", "knowledge survival")]
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.3))
    for ax, (k, title) in zip(axes, keys):
        for s, pts in scaling.items():
            n = np.array([p["n_agents"] for p in pts], float)
            y = np.array([p[k]["mean"] for p in pts], float)
            lo = np.array([p[k]["ci_low"] for p in pts], float)
            hi = np.array([p[k]["ci_high"] for p in pts], float)
            o = np.argsort(n)
            c, mk, ls = STYLE.get(s, ("#444444", "o", "-"))
            ax.plot(n[o], y[o], color=c, marker=mk, linestyle=ls, markersize=4,
                    linewidth=1.4, label=SHORT.get(s, s))
            ax.fill_between(n[o], lo[o], hi[o], color=c, alpha=0.15, linewidth=0)
        ax.set_xscale("log")
        if k != "knowledge_survival":
            ax.set_yscale("log")
        else:
            ax.set_ylim(0, 1)
        ax.set_xlabel("agents N")
        ax.set_title(title, fontsize=9)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(6, len(labels)),
               bbox_to_anchor=(0.5, -0.12))
    fig.suptitle("Empirical scaling trend (4 measured scales; not an asymptotic claim)",
                 y=1.03, fontsize=10)
    fig.tight_layout()
    save(fig, out)


def corruption_figure(data, systems, out: Path, n_agents, n_seeds=None):
    kinds = [("node_coordinated", "coordinated node corruption"),
             ("root", "origin-source corruption (all derived copies inherit)"),
             ("node_uncoordinated", "uncoordinated node corruption")]
    fig, axes = plt.subplots(1, len(kinds), figsize=(4.1 * len(kinds), 3.4), sharey=True)
    for ax, (kind, title) in zip(axes, kinds):
        for s in systems:
            pts = data.get((kind, s))
            if not pts:
                continue
            x = np.array([p["corruption_level"] for p in pts])
            o = np.argsort(x)
            m = np.array([p["knowledge_survival"]["mean"] for p in pts])[o]
            lo = np.array([p["knowledge_survival"]["ci_low"] for p in pts])[o]
            hi = np.array([p["knowledge_survival"]["ci_high"] for p in pts])[o]
            curve(ax, x[o], m, lo, hi, s)
        ax.set_xlabel("fraction of nodes / origins corrupted")
        ax.set_title(title, fontsize=9)
        ax.set_ylim(0, 1)
    axes[0].set_ylabel("knowledge survival")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(5, len(labels)),
               bbox_to_anchor=(0.5, -0.12))
    fig.suptitle(f"Misinformation resistance (N = {n_agents:,}, 30% random churn"
                 + (f", {n_seeds} seeds, 95% CI)" if n_seeds else ")"), y=1.02, fontsize=10)
    fig.tight_layout()
    save(fig, out)


def class_figure(data, systems, out: Path, n_agents, n_seeds=None):
    """Section 7: 100 correlated copies of one origin vs 3 independent origins."""
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.4))
    panels = [("acc_class_correlated", "acc_class_independent", "task accuracy"),
              ("iss_class_correlated", "iss_class_independent", "independent-support survival"),
              ("ece_class_correlated", "ece_class_independent", "calibration error (lower better)")]
    width = 0.35
    xs = np.arange(len(systems))
    for ax, (kc, ki, title) in zip(axes, panels):
        mc = [data[s][kc]["mean"] for s in systems]
        mi = [data[s][ki]["mean"] for s in systems]
        ec = [[data[s][kc]["mean"] - data[s][kc]["ci_low"] for s in systems],
              [data[s][kc]["ci_high"] - data[s][kc]["mean"] for s in systems]]
        ei = [[data[s][ki]["mean"] - data[s][ki]["ci_low"] for s in systems],
              [data[s][ki]["ci_high"] - data[s][ki]["mean"] for s in systems]]
        ax.bar(xs - width / 2, mc, width, yerr=ec, capsize=2, color="#CC79A7",
               label="correlated: 1 origin, 100 copies")
        ax.bar(xs + width / 2, mi, width, yerr=ei, capsize=2, color="#009E73",
               label="independent: 3 origins, 1 copy each")
        ax.set_xticks(xs)
        ax.set_xticklabels(systems, fontsize=8)
        ax.set_title(title, fontsize=9)
    axes[0].set_ylim(0, 1)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.10))
    fig.suptitle(f"Correlated replication vs independent evidence "
                 f"(N = {n_agents:,}, 50% targeted white-box failure"
                 + (f", {n_seeds} seeds)" if n_seeds else ")"), y=1.02, fontsize=10)
    fig.tight_layout()
    save(fig, out)


def questioning_figure(budget_curves, out: Path, n_agents, n_seeds=None):
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.5))
    panels = [("accuracy_macro", "task accuracy (macro)"),
              ("acc_revision_sensitive", "stale-fact accuracy"),
              ("messages_per_claim", "messages per claim (cost)")]
    for ax, (metric, title) in zip(axes, panels):
        for name, pts in budget_curves.items():
            x = np.array([p["question_budget"] for p in pts], float)
            o = np.argsort(x)
            m = np.array([p[metric]["mean"] for p in pts], float)[o]
            lo = np.array([p[metric]["ci_low"] for p in pts], float)[o]
            hi = np.array([p[metric]["ci_high"] for p in pts], float)[o]
            ax.plot(x[o], m, marker="o", markersize=4, linewidth=1.4, label=name)
            ax.fill_between(x[o], lo, hi, alpha=0.15, linewidth=0)
        ax.set_xlabel("questions asked per claim")
        ax.set_title(title, fontsize=9)
        if metric != "messages_per_claim":
            ax.set_ylim(0, 1)
        else:
            ax.set_yscale("log")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(5, len(labels)),
               bbox_to_anchor=(0.5, -0.12))
    fig.suptitle(f"Continual-questioning ablation (N = {n_agents:,}, 50% correlated failure"
                 + (f", {n_seeds} seeds, 95% CI)" if n_seeds else ")"), y=1.02, fontsize=10)
    fig.tight_layout()
    save(fig, out)
