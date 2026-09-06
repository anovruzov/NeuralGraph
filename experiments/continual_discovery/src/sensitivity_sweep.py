#!/usr/bin/env python3
"""One-factor-at-a-time sensitivity sweep for the Continual Discovery probe.

The experiment protocol requires that the mechanism not depend on a single
hand-picked configuration. This script varies each of the five parameters
named in `docs/EXPERIMENT_PROTOCOL.md` around the base config and reports,
per axis value, a seed-paired comparison of `continual` and `lineage`
against the baseline policies.

Losses are reported, not hidden: every cell where a lineage-aware policy is
beaten by a baseline is listed verbatim in the generated summary.

Evidence class: synthetic simulation / mechanism probe.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from continual_discovery_sim import generate_claim, run_strategy

# The five axes the protocol names, swept around the base configuration.
AXES: Dict[str, List] = {
    "corrupt_root_prob": [0.05, 0.10, 0.14, 0.20, 0.30],
    "root_zipf_exponent": [0.0, 0.6, 1.0, 1.35, 1.8],
    "question_budget_per_claim": [1, 2, 3, 5, 8],
    "min_independent_roots": [2, 3, 4, 5],
    "candidate_pool_size": [16, 32, 64, 128],
}

BASELINES = ["none", "random", "uncertainty"]
FOCUS = ["lineage", "continual"]
ORDER = ["none", "random", "uncertainty", "lineage", "continual"]

# metric -> +1 if larger is better, -1 if smaller is better
DIRECTION = {
    "accuracy": 1,
    "false_confident_consensus_rate": -1,
    "independent_roots_per_question": 1,
    "resolved_gap_rate": 1,
}


def run_cell(cfg: dict, n_agents: int, seed: int) -> Dict[str, dict]:
    """Run every strategy on one shared set of worlds.

    Seeding mirrors `continual_discovery_sim.run` exactly, so a cell at the
    base parameter values reproduces the corresponding main-sweep rows.
    """
    world_rng = np.random.default_rng(int(seed) + int(n_agents) * 1_000_003)
    worlds = [generate_claim(world_rng, int(n_agents), cfg) for _ in range(int(cfg["n_claims"]))]
    out = {}
    for s_idx, strategy in enumerate(cfg["strategies"]):
        rng = np.random.default_rng(int(seed) * 1009 + int(n_agents) * 9176 + s_idx * 7919)
        out[strategy] = run_strategy(worlds, strategy, rng, cfg)
    return out


def paired_verdict(deltas: List[float], direction: int) -> tuple[float, float, str]:
    """Seed-paired mean delta, its standard error, and a win/tie/loss call.

    `deltas` are per-seed differences (focus minus baseline) of a raw metric;
    `direction` orients them so that positive always means "focus is better".
    """
    oriented = [d * direction for d in deltas]
    mu = statistics.mean(oriented)
    sd = statistics.stdev(oriented) if len(oriented) > 1 else 0.0
    sem = sd / math.sqrt(len(oriented)) if oriented else 0.0
    if sem == 0.0:
        verdict = "win" if mu > 0 else ("loss" if mu < 0 else "tie")
    elif mu > 2 * sem:
        verdict = "win"
    elif mu < -2 * sem:
        verdict = "loss"
    else:
        verdict = "tie"
    return mu, sem, verdict


def sweep(base_cfg: dict, n_agents: int, seeds: List[int], n_claims: int) -> List[dict]:
    rows = []
    for axis, values in AXES.items():
        for value in values:
            cfg = dict(base_cfg)
            cfg[axis] = value
            cfg["n_claims"] = n_claims
            cfg["strategies"] = ORDER
            for seed in seeds:
                cell = run_cell(cfg, n_agents, seed)
                for strategy, metrics in cell.items():
                    rows.append({
                        "evidence_class": "synthetic_simulation",
                        "axis": axis,
                        "axis_value": value,
                        "is_base_value": int(value == base_cfg[axis]),
                        "agent_scale": n_agents,
                        "seed": seed,
                        "strategy": strategy,
                        **metrics,
                    })
            done = f"{axis}={value}"
            print(f"swept {done:<38} seeds={len(seeds)} claims={n_claims}")
    return rows


def summarize(rows: List[dict]) -> List[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["axis"], r["axis_value"], r["strategy"])].append(r)
    out = []
    for (axis, value, strategy), rs in groups.items():
        row = {"axis": axis, "axis_value": value, "strategy": strategy,
               "runs": len(rs), "is_base_value": rs[0]["is_base_value"]}
        for m in list(DIRECTION) + ["questions_per_claim", "final_independent_roots"]:
            vals = [float(r[m]) for r in rs]
            row[m + "_mean"] = statistics.mean(vals)
            row[m + "_sd"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
        out.append(row)
    out.sort(key=lambda r: (list(AXES).index(r["axis"]),
                            AXES[r["axis"]].index(r["axis_value"]),
                            ORDER.index(r["strategy"])))
    return out


def comparisons(rows: List[dict]) -> List[dict]:
    by_cell = defaultdict(dict)
    for r in rows:
        by_cell[(r["axis"], r["axis_value"], r["seed"])][r["strategy"]] = r

    out = []
    cells = defaultdict(list)
    for (axis, value, seed), per_strategy in by_cell.items():
        cells[(axis, value)].append(per_strategy)

    for (axis, value), seed_cells in sorted(cells.items(),
                                            key=lambda kv: (list(AXES).index(kv[0][0]),
                                                            AXES[kv[0][0]].index(kv[0][1]))):
        for focus in FOCUS:
            for baseline in BASELINES + [b for b in FOCUS if b != focus]:
                for metric, direction in DIRECTION.items():
                    deltas = [float(c[focus][metric]) - float(c[baseline][metric]) for c in seed_cells]
                    mu, sem, verdict = paired_verdict(deltas, direction)
                    q_focus = statistics.mean([float(c[focus]["questions_per_claim"]) for c in seed_cells])
                    q_base = statistics.mean([float(c[baseline]["questions_per_claim"]) for c in seed_cells])
                    out.append({
                        "axis": axis,
                        "axis_value": value,
                        "focus": focus,
                        "baseline": baseline,
                        "metric": metric,
                        "oriented_mean_delta": mu,
                        "sem": sem,
                        "verdict": verdict,
                        "seeds_favoring_focus": sum(1 for d in deltas if d * direction > 0),
                        "n_seeds": len(deltas),
                        "focus_questions_per_claim": q_focus,
                        "baseline_questions_per_claim": q_base,
                    })
    return out


def load_raw(path: Path) -> List[dict]:
    """Re-read a previously written sensitivity_raw.csv.

    Lets the tables and figures be regenerated without re-running the sweep,
    so a reporting change never silently reruns the science.
    """
    def coerce(key: str, value: str):
        if key in ("evidence_class", "axis", "strategy"):
            return value
        if key in ("is_base_value", "agent_scale", "seed", "n_claims",
                   "total_questions", "total_new_roots"):
            return int(value)
        if key == "axis_value":
            f = float(value)
            return int(f) if f.is_integer() and "." not in value else f
        return float(value)

    with path.open(newline="") as f:
        return [{k: coerce(k, v) for k, v in r.items()} for r in csv.DictReader(f)]


def write_csv(rows: List[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _loss_rows(comps: List[dict], focus_set, baseline_set) -> List[dict]:
    return [c for c in comps
            if c["verdict"] == "loss" and c["focus"] in focus_set and c["baseline"] in baseline_set]


def write_md(summary: List[dict], comps: List[dict], meta: dict, path: Path) -> None:
    lines = [
        "# Continual Discovery — sensitivity sweep",
        "",
        "> **Evidence class: synthetic simulation / mechanism probe. These are not",
        "> production deployment results.**",
        "",
        f"One-factor-at-a-time sweep around the base configuration, "
        f"{meta['n_claims']} claims x {meta['n_seeds']} seeds per cell at a synthetic "
        f"population of {meta['agent_scale']} agents with bounded per-claim routing. "
        "Every strategy in a cell sees the same generated worlds, so all comparisons "
        "below are seed-paired. A cell at the base parameter values reproduces the "
        "corresponding rows of the headline sweep exactly (asserted in "
        "`tests/test_sensitivity.py`).",
        "",
        "Verdicts use the paired mean difference against its standard error over seeds: "
        "**win** if the mean is more than 2 SEM better, **loss** if more than 2 SEM worse, "
        "**tie** otherwise. Metrics are oriented so positive always means the focus "
        "policy is better. No significance correction is applied across the "
        f"{len(comps)} comparisons; read individual cells as descriptive.",
        "",
    ]

    lines += _headline_section(comps)

    for axis in AXES:
        lines += [f"## {axis}", "",
                  "| value | strategy | accuracy | false confident consensus | Q / claim | new roots / Q | resolved gaps |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for r in [r for r in summary if r["axis"] == axis]:
            base_mark = " (base)" if r["is_base_value"] else ""
            lines.append(
                f"| {r['axis_value']}{base_mark} | {r['strategy']} | "
                f"{r['accuracy_mean']:.3f} ± {r['accuracy_sd']:.3f} | "
                f"{r['false_confident_consensus_rate_mean']:.3f} ± {r['false_confident_consensus_rate_sd']:.3f} | "
                f"{r['questions_per_claim_mean']:.2f} | "
                f"{r['independent_roots_per_question_mean']:.3f} | "
                f"{r['resolved_gap_rate_mean']:.3f} |"
            )
        lines.append("")

    lines += ["## Accuracy — `continual` vs each baseline, every cell", "",
              "| axis | value | vs | Δ accuracy | SEM | seeds favoring | verdict | Q/claim (continual vs baseline) |",
              "|---|---|---|---:|---:|---:|---|---|"]
    for c in comps:
        if c["focus"] != "continual" or c["metric"] != "accuracy" or c["baseline"] == "lineage":
            continue
        lines.append(
            f"| {c['axis']} | {c['axis_value']} | {c['baseline']} | "
            f"{c['oriented_mean_delta']:+.4f} | {c['sem']:.4f} | "
            f"{c['seeds_favoring_focus']}/{c['n_seeds']} | {c['verdict']} | "
            f"{c['focus_questions_per_claim']:.2f} vs {c['baseline_questions_per_claim']:.2f} |"
        )
    lines.append("")

    lines += _losses_section(comps)
    lines += _totals_section(comps)
    path.write_text("\n".join(lines))


def _headline_section(comps: List[dict]) -> List[str]:
    """Machine-generated verdict counts, so the prose cannot drift from the data."""
    lines = ["## What the sweep says", ""]
    n_cells = sum(len(v) for v in AXES.values())
    for focus in FOCUS:
        for metric in ("accuracy", "false_confident_consensus_rate"):
            for baseline in BASELINES:
                sel = [c for c in comps if c["focus"] == focus
                       and c["baseline"] == baseline and c["metric"] == metric]
                w = sum(1 for c in sel if c["verdict"] == "win")
                l = sum(1 for c in sel if c["verdict"] == "loss")
                t = len(sel) - w - l
                lines.append(f"- `{focus}` vs `{baseline}` on **{metric}**: "
                             f"{w} wins / {t} ties / {l} losses across {n_cells} cells")
        lines.append("")
    lines += [
        "Read the per-axis tables before quoting any of this. The two lineage-aware "
        "policies buy different things: `lineage` spends almost no questions and is "
        "the cheapest way to suppress confidently-wrong consensus, while `continual` "
        "spends more and is the only policy that resolves initial epistemic gaps at a "
        "meaningful rate. Neither dominates the other on every metric, and both are "
        "compared against `random`, which always spends the full budget.",
        "",
    ]
    return lines


def _losses_section(comps: List[dict]) -> List[str]:
    """Negative results, kept per the pack's guardrail."""
    lines = ["## Regimes where a lineage-aware policy loses to a baseline", ""]
    baseline_losses = _loss_rows(comps, set(FOCUS), set(BASELINES))
    if not baseline_losses:
        lines += ["No cell in this sweep shows a separated loss for `lineage` or "
                  "`continual` against `none`, `random`, or `uncertainty` on any "
                  "tracked metric.", ""]
    else:
        lines += ["| axis | value | focus | vs | metric | Δ (oriented) | SEM | seeds favoring focus | Q/claim (focus vs baseline) |",
                  "|---|---|---|---|---|---:|---:|---:|---|"]
        for c in baseline_losses:
            lines.append(
                f"| {c['axis']} | {c['axis_value']} | {c['focus']} | {c['baseline']} | "
                f"{c['metric']} | {c['oriented_mean_delta']:+.4f} | {c['sem']:.4f} | "
                f"{c['seeds_favoring_focus']}/{c['n_seeds']} | "
                f"{c['focus_questions_per_claim']:.2f} vs {c['baseline_questions_per_claim']:.2f} |"
            )
        lines.append("")

    peer_losses = _loss_rows(comps, set(FOCUS), set(FOCUS))
    lines += ["## Trade-offs between the two lineage-aware policies", "",
              "These are not baseline comparisons. `lineage` asks far fewer questions, "
              "so it wins on roots acquired per question and on false confident "
              "consensus while never resolving gaps; `continual` spends more and wins "
              "on accuracy and resolved gaps. Both directions are listed.", ""]
    if peer_losses:
        lines += ["| axis | value | focus | vs | metric | Δ (oriented) | SEM | Q/claim (focus vs peer) |",
                  "|---|---|---|---|---|---:|---:|---|"]
        for c in peer_losses:
            lines.append(
                f"| {c['axis']} | {c['axis_value']} | {c['focus']} | {c['baseline']} | "
                f"{c['metric']} | {c['oriented_mean_delta']:+.4f} | {c['sem']:.4f} | "
                f"{c['focus_questions_per_claim']:.2f} vs {c['baseline_questions_per_claim']:.2f} |"
            )
        lines.append("")
    else:
        lines += ["No separated differences between `lineage` and `continual` in this sweep.", ""]
    return lines


def _totals_section(comps: List[dict]) -> List[str]:
    vs_baseline = [c for c in comps if c["baseline"] in BASELINES]
    vs_peer = [c for c in comps if c["baseline"] in FOCUS]
    def tally(rows):
        w = sum(1 for c in rows if c["verdict"] == "win")
        l = sum(1 for c in rows if c["verdict"] == "loss")
        return w, len(rows) - w - l, l
    bw, bt, bl = tally(vs_baseline)
    pw, pt, pl = tally(vs_peer)
    return [
        "## Sweep totals", "",
        f"- cells: {sum(len(v) for v in AXES.values())} "
        f"({len(AXES)} axes, one factor varied at a time)",
        f"- comparisons: {len(comps)} ({len(FOCUS)} focus policies x "
        f"{len(BASELINES) + len(FOCUS) - 1} baselines-or-peers x "
        f"{len(DIRECTION)} metrics x {sum(len(v) for v in AXES.values())} cells)",
        f"- vs baselines: {bw} wins / {bt} ties / {bl} losses",
        f"- vs the other lineage-aware policy: {pw} wins / {pt} ties / {pl} losses",
        "",
        "Do not remove the synthetic-evidence label when copying these tables.",
        "",
    ]


def plot_axes(summary: List[dict], figs: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panels = [("accuracy_mean", "Accuracy"),
              ("false_confident_consensus_rate_mean", "False confident consensus"),
              ("independent_roots_per_question_mean", "New roots / question")]
    for axis in AXES:
        rows = [r for r in summary if r["axis"] == axis]
        fig, axarr = plt.subplots(1, 3, figsize=(13.5, 3.9))
        for ax, (key, label) in zip(axarr, panels):
            for s in ORDER:
                pts = [(r["axis_value"], r[key]) for r in rows if r["strategy"] == s]
                pts.sort()
                ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", label=s)
            ax.set_xlabel(axis)
            ax.set_ylabel(label)
            ax.grid(True, alpha=0.25)
        axarr[0].legend(frameon=False, ncol=2, fontsize=8)
        fig.suptitle(f"Sensitivity: {axis} (synthetic simulation)", fontsize=10)
        fig.tight_layout()
        fig.savefig(figs / f"sensitivity_{axis}.png", dpi=160)
        plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/fast.json", help="base config supplying the un-swept parameters")
    p.add_argument("--out", default="results")
    p.add_argument("--figures", default="figures")
    p.add_argument("--agent-scale", type=int, default=1000)
    p.add_argument("--seeds", type=int, default=8)
    p.add_argument("--claims", type=int, default=1500)
    p.add_argument("--no-figures", action="store_true")
    p.add_argument("--from-raw", default=None,
                   help="regenerate tables and figures from an existing sensitivity_raw.csv "
                        "instead of re-running the sweep")
    a = p.parse_args()

    with open(a.config) as f:
        base_cfg = json.load(f)

    if a.from_raw:
        rows = load_raw(Path(a.from_raw))
        seeds = sorted({r["seed"] for r in rows})
        a.agent_scale = rows[0]["agent_scale"]
        a.claims = int(rows[0]["n_claims"])
        print(f"re-analyzing {len(rows)} rows from {a.from_raw}")
    else:
        seeds = list(range(a.seeds))
        rows = sweep(base_cfg, a.agent_scale, seeds, a.claims)
    summary = summarize(rows)
    comps = comparisons(rows)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if not a.from_raw:
        write_csv(rows, out / "sensitivity_raw.csv")
    write_csv(summary, out / "sensitivity_summary.csv")
    write_csv(comps, out / "sensitivity_comparisons.csv")

    meta = {"base_config": a.config, "agent_scale": a.agent_scale,
            "n_seeds": len(seeds), "n_claims": a.claims, "axes": AXES,
            "base_values": {k: base_cfg[k] for k in AXES}}
    with (out / "sensitivity_config.json").open("w") as f:
        json.dump(meta, f, indent=2)

    write_md(summary, comps, meta, out / "sensitivity_summary.md")

    if not a.no_figures:
        figs = Path(a.figures)
        figs.mkdir(parents=True, exist_ok=True)
        plot_axes(summary, figs)

    print(f"Wrote {out/'sensitivity_summary.md'}")


if __name__ == "__main__":
    main()
