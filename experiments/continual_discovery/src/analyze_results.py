#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
import math
import statistics

import matplotlib.pyplot as plt

METRICS = [
    "accuracy",
    "false_confident_consensus_rate",
    "questions_per_claim",
    "independent_roots_per_question",
    "conflict_detection_rate",
    "resolved_gap_rate",
    "final_independent_roots",
    "runtime_sec",
]

ORDER = ["none", "random", "uncertainty", "lineage", "continual"]


def mean_sd(vals):
    if not vals:
        return 0.0, 0.0
    return statistics.mean(vals), statistics.stdev(vals) if len(vals) > 1 else 0.0


def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(int(r["agent_scale"]), r["strategy"])].append(r)
    out = []
    for (n, s), rs in sorted(groups.items(), key=lambda x: (x[0][0], ORDER.index(x[0][1]))):
        row = {"agent_scale": n, "strategy": s, "runs": len(rs)}
        for m in METRICS:
            vals = [float(r[m]) for r in rs]
            mu, sd = mean_sd(vals)
            row[m+"_mean"] = mu
            row[m+"_sd"] = sd
        out.append(row)
    return out


def write_csv(rows, path):
    fields = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)


def write_md(rows, path):
    lines = [
        "# Synthetic Continual Discovery Results",
        "",
        "> **Evidence class: synthetic simulation / mechanism probe. These are not production deployment results.**",
        "",
        "| Agents | Strategy | Accuracy | False confident consensus | Q / claim | New roots / Q | Conflict detection | Resolved gaps |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['agent_scale']} | {r['strategy']} | {r['accuracy_mean']:.3f} ± {r['accuracy_sd']:.3f} | "
            f"{r['false_confident_consensus_rate_mean']:.3f} ± {r['false_confident_consensus_rate_sd']:.3f} | "
            f"{r['questions_per_claim_mean']:.2f} | {r['independent_roots_per_question_mean']:.3f} | "
            f"{r['conflict_detection_rate_mean']:.3f} | {r['resolved_gap_rate_mean']:.3f} |"
        )
    lines += ["", "Do not remove the synthetic-evidence label when copying this table."]
    path.write_text("\n".join(lines)+"\n")


def write_tex(rows, path):
    lines = [
        r"% SYNTHETIC SIMULATION / MECHANISM PROBE — NOT A REAL DEPLOYMENT BENCHMARK",
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\caption{Synthetic mechanism probe of continual questioning under correlated evidence lineages.}",
        r"\label{tab:synthetic-questioning}",
        r"\begin{tabular}{r l r r r}",
        r"\toprule",
        r"Agents & Policy & Accuracy & False-conf. & New roots / Q \\",
        r"\midrule",
    ]
    for r in rows:
        lines.append(
            f"{r['agent_scale']} & {r['strategy']} & {100*r['accuracy_mean']:.1f}\\% & "
            f"{100*r['false_confident_consensus_rate_mean']:.1f}\\% & {r['independent_roots_per_question_mean']:.2f} \\\\" 
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    path.write_text("\n".join(lines)+"\n")


def plot_metric(rows, metric, ylabel, outpath):
    by_strategy = defaultdict(list)
    for r in rows:
        by_strategy[r["strategy"]].append((int(r["agent_scale"]), float(r[metric+"_mean"])))
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for s in ORDER:
        pts = sorted(by_strategy.get(s, []))
        if not pts: continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        ax.plot(xs, ys, marker="o", label=s)
    ax.set_xscale("log")
    ax.set_xlabel("Synthetic agent population")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(outpath, dpi=180)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out", default="results")
    p.add_argument("--figures", default="figures")
    a = p.parse_args()
    rows = load(a.input)
    summary = summarize(rows)
    out = Path(a.out); figs = Path(a.figures)
    out.mkdir(parents=True, exist_ok=True); figs.mkdir(parents=True, exist_ok=True)
    write_csv(summary, out/"summary.csv")
    write_md(summary, out/"summary.md")
    write_tex(summary, out/"paper_table.tex")
    plot_metric(summary, "accuracy", "Accuracy", figs/"accuracy_vs_agents.png")
    plot_metric(summary, "false_confident_consensus_rate", "False confident consensus rate", figs/"false_consensus_vs_agents.png")
    plot_metric(summary, "independent_roots_per_question", "Independent roots acquired per question", figs/"independent_roots_per_question.png")
    print(f"Wrote {out/'summary.md'} and {figs}")

if __name__ == "__main__":
    main()
