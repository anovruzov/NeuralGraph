#!/usr/bin/env python3
"""Markdown tables for the Mycelic benchmark — produced ONLY from ``results/processed/*.csv``.

Usage::

    python experiments/tables.py --results results                      # writes results/tables/*.md
    python experiments/tables.py --results results --out /tmp/t --baseline B7_mycelic --n-boot 2000

Outputs (each skipped with a printed warning when its CSV or columns are missing):

* ``executive_comparison.md`` — from ``headline.csv`` (fallback ``aggregation.csv``): System ×
  Global discovery / Cross-team synthesis / Poison propagation / Private data exposed /
  Compression / Cost / Bytes, as mean ± SD with the number of seeds.
* ``layer_fidelity.md`` — from ``aggregation.csv`` columns ``metrics.fidelity.<layer>.<state>``:
  per system, one row per layer (team → executive), mean ± SD per fidelity state.
* ``failure_reasons.md`` — from ``metrics.failure_reasons.<reason>`` columns (``aggregation.csv``,
  fallback ``headline.csv`` / ``poisoning.csv``): mean count of undiscovered hidden effects per reason
  and system (a missing reason in a run counts as 0).
* ``stats_<family>.md`` — one per processed CSV: for every condition group and headline metric,
  per-system mean, SD, 95% t-CI, and the seed-paired difference vs the baseline (default
  ``B7_mycelic``) with paired-bootstrap CI, Holm-corrected paired-t p (within the family of systems
  compared for that metric and condition) and Cohen's d_z.  Never a p-value without an effect size.

Rows with ``status != ok`` are dropped.  All numbers come from the CSVs; nothing is hand-entered.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from figures import (CONDITION_COLS, EXEC_METRICS, LAYER_ORDER, SYSTEM_LABEL, default_condition, fmt_value,  # noqa: E402
                     load_csv, order_systems)
from mycelic_bench.stats import compare_systems, summarize  # noqa: E402

FIDELITY_STATES = ["useful_fact_recall", "precision_strict", "false_promotion_rate", "survived", "dropped", "duplicated",
                   "distorted", "independent_support_preserved", "lineage_retention", "n_accepted"]
STATS_METRICS = [
    ("metrics.recall_global", "Global discovery recall"), ("metrics.recall_cross_team", "Cross-team recall"),
    ("metrics.recall_local", "Local recall"), ("metrics.precision_strict", "Precision (strict)"),
    ("metrics.false_association_rate", "False association rate"),
    ("metrics.poison.poison_promotion_rate", "Poison promotion rate"),
    ("metrics.raw_sensitive_leakage", "Canary leakage"), ("metrics.fraction_raw_exposed", "Raw bytes exposed"),
    ("metrics.compression_ratio", "Compression ratio"), ("metrics.bytes_transmitted", "Bytes transmitted"),
    ("metrics.cost_usd_est", "Cost (USD)"), ("metrics.ttd_global_median", "Time-to-discovery (global, rounds)"),
    ("metrics.contradiction_f1", "Contradiction F1"), ("metrics.revision_accuracy", "Revision accuracy"),
    ("knowledge_survival", "Knowledge survival"), ("useful_discovery_survival", "Useful-discovery survival"),
    ("retrieval_accuracy", "Retrieval accuracy"), ("unnecessary_escalation_rate", "Unnecessary escalation rate"),
    ("questions_asked", "Questions asked"), ("marginal_value_per_question", "Marginal value per question"),
]


def warn(msg: str) -> None:
    print(f"[tables] WARNING: {msg}", flush=True)


def info(msg: str) -> None:
    print(f"[tables] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _isnan(x: Any) -> bool:
    try:
        return x is None or (isinstance(x, float) and math.isnan(x)) or (isinstance(x, (np.floating,)) and np.isnan(x))
    except Exception:
        return False


def fmt_num(x: Any, digits: int = 3) -> str:
    if _isnan(x):
        return "n/a"
    x = float(x)
    if math.isinf(x):
        return "∞" if x > 0 else "−∞"
    if abs(x) >= 1e5 or (abs(x) < 1e-3 and x != 0):
        return f"{x:.{digits - 1}e}"
    return f"{x:.{digits}g}"


def fmt_p(p: Any) -> str:
    if _isnan(p):
        return "n/a"
    p = float(p)
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def fmt_pm(mean: Any, sd: Any, digits: int = 3) -> str:
    if _isnan(mean):
        return "n/a"
    return fmt_num(mean, digits) if _isnan(sd) else f"{fmt_num(mean, digits)} ± {fmt_num(sd, 2)}"


def fmt_ci(lo: Any, hi: Any) -> str:
    if _isnan(lo) or _isnan(hi):
        return "n/a"
    return f"[{fmt_num(lo)}, {fmt_num(hi)}]"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    def esc(s: Any) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(esc(h) for h in headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _write(out: Path, name: str, text: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    p.write_text(text, encoding="utf-8")
    info(f"wrote {p}")
    return p


def _source_line(fam: str, df: pd.DataFrame) -> str:
    n = df.groupby("system")["seed"].nunique() if {"system", "seed"} <= set(df.columns) else pd.Series(dtype=int)
    n_txt = "" if n.empty else (f"n = {int(n.min())} seeds per system" if n.min() == n.max() else f"n = {int(n.min())}–{int(n.max())} seeds per system")
    tier = ""
    if "tier" in df.columns and df["tier"].notna().any():
        tier = "; tier: " + ", ".join(sorted(df["tier"].dropna().astype(str).unique()))
    return f"Source: `results/processed/{fam}.csv` ({n_txt}{tier}). Model backend: simulated profiles unless the run manifest says otherwise (DESIGN.md §9).\n\n"


def _num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# executive_comparison.md
# ---------------------------------------------------------------------------

def write_executive_comparison(results: Path, out: Path) -> Path | None:
    df, fam = load_csv(results, "headline", "aggregation")
    if df is None or not {"system", "seed"} <= set(df.columns):
        warn("executive_comparison: headline.csv/aggregation.csv missing or without system/seed — skipped"); return None
    df = default_condition(df, "executive_comparison")
    metrics = [m for m in EXEC_METRICS if m[0] in df.columns]
    if not metrics:
        warn("executive_comparison: no executive-table metric columns present — skipped"); return None
    df = _num(df, [m[0] for m in metrics])
    systems = order_systems(df["system"].unique())
    headers = ["System"] + [f"{label} ({'↑' if better == 'higher' else '↓'})" for _, label, better, _ in metrics] + ["Seeds (n)"]
    rows = []
    for s in systems:
        sub = df[df["system"] == s]
        cells = [SYSTEM_LABEL.get(s, s)]
        for col, _, _, kind in metrics:
            st = summarize(sub[col].to_numpy(dtype=float))
            cells.append(fmt_value(st["mean"], st["sd"], kind) if st["n"] else "n/a")
        cells.append(str(int(sub["seed"].nunique())))
        rows.append(cells)
    text = "# Executive comparison\n\n"
    text += "Mean ± SD across seeds; every system ran on the same generated worlds (paired by seed). "
    text += "↑ = higher is better, ↓ = lower is better. Paired tests with effect sizes are in `stats_" + fam + ".md`.\n\n"
    if "malicious_fraction" in df.columns and df["malicious_fraction"].notna().any():
        fr = ", ".join(f"{100 * v:g}%" for v in sorted(df["malicious_fraction"].dropna().unique()))
        text += f"Malicious workers: {fr}.\n\n"
    text += _source_line(fam, df) + md_table(headers, rows)
    text += ("\nColumns: Global discovery = `metrics.recall_global`; Cross-team synthesis = `metrics.recall_cross_team`; "
             "Poison propagation = `metrics.poison.poison_promotion_rate`; Private data exposed = `metrics.fraction_raw_exposed`; "
             "Compression = `metrics.compression_ratio`; Cost = `metrics.cost_usd_est`; Bytes = `metrics.bytes_transmitted`.\n")
    return _write(out, "executive_comparison.md", text)


# ---------------------------------------------------------------------------
# layer_fidelity.md
# ---------------------------------------------------------------------------

def write_layer_fidelity(results: Path, out: Path) -> Path | None:
    df, fam = load_csv(results, "aggregation", "headline")
    if df is None:
        warn("layer_fidelity: aggregation.csv missing — skipped"); return None
    fid_cols = [c for c in df.columns if c.startswith("metrics.fidelity.")]
    if not fid_cols or not {"system", "seed"} <= set(df.columns):
        warn("layer_fidelity: no metrics.fidelity.<layer>.<state> columns — skipped"); return None
    df = default_condition(df, "layer_fidelity")
    df = _num(df, fid_cols)
    layers_present = [l for l in LAYER_ORDER if any(c.startswith(f"metrics.fidelity.{l}.") for c in fid_cols)]
    layers_present += sorted({c.split(".")[2] for c in fid_cols} - set(layers_present))
    states = [s for s in FIDELITY_STATES if any(c.endswith("." + s) for c in fid_cols)]
    states += sorted({c.split(".", 3)[3] for c in fid_cols if len(c.split(".", 3)) == 4} - set(states))
    text = "# Layer-transition fidelity\n\n"
    text += ("Each true effect is tracked through the layer transitions (DESIGN.md §10): `useful_fact_recall` = fraction of true "
             "local/cross-team/global effects held at the layer; `survived`/`dropped` are relative to the layer below; "
             "`distorted` = effect estimate off by > 50%; `duplicated` = extra exact claims per effect; "
             "`independent_support_preserved` = |IS_est − IS_true| within tolerance; `lineage_retention` = discoveries with lineage pointers. "
             "Mean ± SD across seeds.\n\n") + _source_line(fam, df)
    written_any = False
    for s in order_systems(df["system"].unique()):
        sub = df[df["system"] == s]
        rows = []
        for layer in layers_present:
            cells = [layer]
            any_val = False
            for st in states:
                col = f"metrics.fidelity.{layer}.{st}"
                if col in sub.columns and sub[col].notna().any():
                    sm = summarize(sub[col].to_numpy(dtype=float))
                    cells.append(fmt_pm(sm["mean"], sm["sd"])); any_val = True
                else:
                    cells.append("—")
            if any_val:
                rows.append(cells)
        if not rows:
            continue
        written_any = True
        text += f"## {SYSTEM_LABEL.get(s, s)} (`{s}`)\n\n" + md_table(["layer"] + states, rows) + "\n"
    if not written_any:
        text += "_No system in this CSV reports per-layer fidelity (only hierarchical systems have layers)._\n"
    return _write(out, "layer_fidelity.md", text)


# ---------------------------------------------------------------------------
# failure_reasons.md
# ---------------------------------------------------------------------------

def write_failure_reasons(results: Path, out: Path) -> Path | None:
    df, fam = load_csv(results, "aggregation", "headline", "poisoning")
    if df is None:
        warn("failure_reasons: no aggregation/headline/poisoning CSV — skipped"); return None
    cols = [c for c in df.columns if c.startswith("metrics.failure_reasons.")]
    if not cols or not {"system", "seed"} <= set(df.columns):
        warn("failure_reasons: no metrics.failure_reasons.<reason> columns — skipped"); return None
    df = default_condition(df, "failure_reasons")
    df = _num(df, cols)
    reasons = [c.split(".", 2)[2] for c in cols]
    order = np.argsort([-float(df[c].fillna(0).mean()) for c in cols], kind="stable")
    cols = [cols[i] for i in order]; reasons = [reasons[i] for i in order]
    rows = []
    for s in order_systems(df["system"].unique()):
        sub = df[df["system"] == s]
        cells = [SYSTEM_LABEL.get(s, s)]
        total = sub[cols].fillna(0).sum(axis=1)
        for c in cols:
            sm = summarize(sub[c].fillna(0).to_numpy(dtype=float))
            cells.append(fmt_pm(sm["mean"], sm["sd"], 3))
        smt = summarize(total.to_numpy(dtype=float))
        cells.append(fmt_pm(smt["mean"], smt["sd"], 3)); cells.append(str(int(sub["seed"].nunique())))
        rows.append(cells)
    text = "# Why hidden effects were not discovered\n\n"
    text += ("Mean number (± SD across seeds) of cross-team/global effects NOT accepted at the root, by the classifier in "
             "`runner.failure_reasons` (DESIGN.md §10). A reason absent from a run counts as 0. Columns sorted by overall frequency.\n\n")
    text += _source_line(fam, df) + md_table(["System"] + reasons + ["total undiscovered", "Seeds (n)"], rows)
    return _write(out, "failure_reasons.md", text)


# ---------------------------------------------------------------------------
# stats_<family>.md
# ---------------------------------------------------------------------------

def _condition_groups(df: pd.DataFrame) -> tuple[list[str], list[tuple[dict[str, Any], pd.DataFrame]]]:
    conds = [c for c in CONDITION_COLS if c in df.columns and df[c].nunique(dropna=True) > 1]
    if not conds:
        return [], [({}, df)]
    groups = []
    for key, sub in df.groupby(conds, dropna=False, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        groups.append((dict(zip(conds, key)), sub))
    return conds, groups


def write_stats_table(results: Path, out: Path, family: str, baseline: str, n_boot: int, seed: int = 0) -> Path | None:
    df, fam = load_csv(results, family)
    if df is None or not {"system", "seed"} <= set(df.columns):
        warn(f"stats_{family}: CSV missing or without system/seed — skipped"); return None
    metrics = [(c, l) for c, l in STATS_METRICS if c in df.columns]
    if not metrics:
        warn(f"stats_{family}: no headline metric columns — skipped"); return None
    df = _num(df, [c for c, _ in metrics])
    conds, groups = _condition_groups(df)
    headers = ["System", "n", "mean", "SD", "95% CI (t)", f"Δ vs {baseline}", "Δ 95% CI (paired bootstrap)", "Δ 95% CI (paired t)", "paired t p", "Holm p", "Wilcoxon p", "Cohen's d_z"]
    text = f"# Paired statistics — `{family}`\n\n"
    text += (f"Per metric and condition: unpaired mean/SD/95% t-CI per system, and the seed-paired difference of each system against "
             f"`{baseline}` (inner join on seed): paired-bootstrap 95% CI (percentile, 10,000 resamples unless stated; it under-covers "
             f"below ~20 seeds — simulated coverage 0.89 at n=10 and ~0.8 at n=5 — so the paired-t 95% CI is given next to it), paired t-test p, "
             f"Holm-adjusted p (family = all systems compared against the baseline for that metric and condition), Wilcoxon signed-rank p, "
             f"and Cohen's d_z. Bootstrap resamples used: {n_boot}.\n\n") + _source_line(fam, df)
    if conds:
        text += f"Condition columns swept in this family: {', '.join(f'`{c}`' for c in conds)}.\n\n"
    n_tables = 0
    for key, sub in groups:
        cond_txt = ", ".join(f"{k} = {v}" for k, v in key.items()) if key else "single condition"
        base_present = baseline in set(sub["system"])
        text += f"## Condition: {cond_txt}\n\n"
        if not base_present:
            text += f"_Baseline `{baseline}` not run under this condition; paired columns are n/a._\n\n"
        for col, label in metrics:
            if sub[col].notna().sum() == 0:
                continue
            base = baseline if base_present else str(order_systems(sub["system"].unique())[0])
            cmp = compare_systems(sub, col, base, systems=order_systems(sub["system"].unique()), n_boot=n_boot, seed=seed)
            rows = []
            for _, r in cmp.iterrows():
                is_base = r["system"] == baseline
                rows.append([SYSTEM_LABEL.get(r["system"], r["system"]) + (" (baseline)" if is_base else ""), str(int(r["n"])),
                             fmt_num(r["mean"]), fmt_num(r["sd"]), fmt_ci(r["ci_low"], r["ci_high"]),
                             "—" if is_base or not base_present else f"{fmt_num(r['diff'])} (n={int(r['n_pairs'])})",
                             "—" if is_base or not base_present else fmt_ci(r["diff_ci_low"], r["diff_ci_high"]),
                             "—" if is_base or not base_present else fmt_ci(r["diff_t_ci_low"], r["diff_t_ci_high"]),
                             "—" if is_base or not base_present else fmt_p(r["t_p"]),
                             "—" if is_base or not base_present else fmt_p(r["holm_p"]),
                             "—" if is_base or not base_present else fmt_p(r["wilcoxon_p"]),
                             "—" if is_base or not base_present else fmt_num(r["cohen_dz"], 2)])
            text += f"### {label} (`{col}`)\n\n" + md_table(headers, rows) + "\n"
            n_tables += 1
    if n_tables == 0:
        warn(f"stats_{family}: metric columns present but empty — skipped"); return None
    return _write(out, f"stats_{family}.md", text)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> dict[str, Path | None]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", default="results", help="results root containing processed/*.csv")
    ap.add_argument("--out", default=None, help="output directory (default: <results>/tables)")
    ap.add_argument("--baseline", default="B7_mycelic", help="system every other system is paired against")
    ap.add_argument("--n-boot", type=int, default=10000, help="paired bootstrap resamples")
    ap.add_argument("--families", default=None, help="comma-separated processed CSV families for stats tables (default: all present)")
    ap.add_argument("--seed", type=int, default=0, help="bootstrap RNG seed")
    args = ap.parse_args(argv)
    results = Path(args.results)
    out = Path(args.out) if args.out else results / "tables"
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path | None] = {}
    for name, fn in (("executive_comparison", write_executive_comparison), ("layer_fidelity", write_layer_fidelity),
                     ("failure_reasons", write_failure_reasons)):
        try:
            written[name] = fn(results, out)
        except Exception as e:
            warn(f"{name} failed: {e!r}"); written[name] = None
    processed = results / "processed"
    if args.families:
        families = [f.strip() for f in args.families.split(",") if f.strip()]
    else:
        families = sorted(p.stem for p in processed.glob("*.csv")) if processed.exists() else []
    if not families:
        warn(f"no processed CSVs found under {processed}")
    for fam in families:
        try:
            written[f"stats_{fam}"] = write_stats_table(results, out, fam, args.baseline, args.n_boot, args.seed)
        except Exception as e:
            warn(f"stats_{fam} failed: {e!r}"); written[f"stats_{fam}"] = None
    info(f"{sum(1 for v in written.values() if v)}/{len(written)} tables written to {out}")
    return written


if __name__ == "__main__":
    main()
