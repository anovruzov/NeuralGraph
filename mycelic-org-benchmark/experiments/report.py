#!/usr/bin/env python3
"""Assemble the results document and the machine-readable summary from ``results/processed/*.csv``.

    python experiments/report.py --results results --out docs/RESULTS.md --summary results/summary.json
                                 [--baseline B7_mycelic] [--n-boot 10000] [--seed 0]
                                 [--figures results/figures] [--tables results/tables]

Contract (DESIGN.md §1 rule 6: no hand-entered number anywhere in the report):

* Inputs are ONLY the processed CSVs (one flat row per run as written by ``experiments/common.py``:
  ``family, run_id, status, system, seed, tier, <condition columns>, runtime_s, metrics.*`` plus the
  family-specific derived columns), the figure PNGs and the table Markdown files.  Ground truth, raw
  manifests and claims are never read; nothing is typed by hand.
* Every family CSV present gets a section (known families first, unknown ones after them, same layout):
  source file and sha256, run counts, seeds, failed runs (``status != ok``: counted, named, excluded from
  every statistic), the condition grid, key metrics per condition and system as mean ± SD over seeds
  (n = seeds; NaNs dropped and counted), seed-paired comparisons of every other system against the
  baseline under the same condition (mean difference, paired-bootstrap 95% CI, paired-t and Wilcoxon
  p-values Holm-corrected within the family × metric group, Cohen's d_z, n pairs; fewer than 3 pairs ->
  listed as skipped) and, for the baseline system (every system when the baseline was not run), the same
  statistics across the swept conditions against a reference condition (``mycelic_bench.stats`` only).
* Absent families get an explicit "not run / not available" line, absent columns are named, and the
  script never crashes on partial results (the campaign may still be running).  Re-runs append rows: of
  several ``ok`` rows for one (condition, system, seed[, cluster_id]) the most recent (``timestamp``,
  else file order) supersedes the others and the count is reported.
* ``summary.json``: family -> condition -> system: n, seeds, mean/sd/min/max/NaN count of every numeric
  metric column and the paired statistics vs the baseline for the key metrics; plus ``generated_at``,
  the git commit and the source CSVs with row counts and sha256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from mycelic_bench.stats import holm, paired_summary  # noqa: E402

FAMILY_ORDER = ["headline", "aggregation", "compression", "poisoning", "edge_security", "privacy", "contradictions", "temporal",
                "hierarchy", "scaling", "models", "questioning", "independent_support", "independent_support_clusters", "routing",
                "failures"]
FAMILY_NOTE = {"models": "SYNTHETIC capability sweep over assumed model profiles (configs/models.yaml), not measured models",
               "independent_support_clusters": "one row per planted evidence cluster (several rows per run)",
               "routing": "one row per router x seed on a finished B7 hierarchy; `router` is the condition"}
ID_COLS = {"family", "run_id", "batch_id", "status", "system", "kind", "seed", "tier", "tag", "condition", "dataset_sha256",
           "timestamp", "error", "per_run_error", "cluster_id", "cell", "label", "estimate_source", "profile_kind", "params_b",
           "n_interactions", "n_rounds", "n_teams", "n_departments", "n_regions"}
CONDITION_CANDIDATES = ["tier", "n_workers", "malicious_fraction", "attack_type", "detector", "layers", "byte_budget", "compression",
                        "sketch_order", "cadence", "profile", "quote_policy", "k_anonymity", "questioning", "failure_kind",
                        "failure_rate", "n_contradictions", "resolve_ratio", "n_temporal_revisions", "recent_window", "router",
                        "policy", "scenario", "kind"]
ALWAYS_SPLIT = ("tier", "n_workers")   # different worlds: seeds are never paired across these
REFERENCE_VALUES: dict[str, Any] = {   # the runners' defaults -> reference condition of the condition-paired comparisons
    "tier": "tier1", "n_workers": 1000, "malicious_fraction": 0.0, "detector": "none", "layers": 5, "byte_budget": 0,
    "compression": "full_sketch", "sketch_order": 3, "cadence": 1, "profile": "sim-3b", "quote_policy": "none", "k_anonymity": 3,
    "questioning": "none", "failure_kind": "none", "failure_rate": 0.0, "resolve_ratio": 2.0, "recent_window": 6,
    "router": "hierarchy_aware"}
_M = "metrics."


def _keys(metrics: str, literal: str = "") -> list[str]:
    """Preferred key-metric columns: ``metrics.``-prefixed names plus literal (derived) column names."""
    return [_M + m for m in metrics.split()] + literal.split()


KEY_METRICS: dict[str, list[str]] = {
    "aggregation": _keys("recall_global recall_cross_team recall_local precision_strict false_association_rate ttd_global_median "
                         "bytes_transmitted compression_ratio"),
    "headline": _keys("recall_global recall_cross_team precision_strict poison.poison_promotion_rate raw_sensitive_leakage "
                      "compression_ratio cost_usd_est bytes_transmitted"),
    "compression": _keys("compression_ratio bytes_transmitted recall_global recall_cross_team precision_strict "
                         "false_association_rate fidelity.executive.useful_fact_recall"),
    "poisoning": _keys("poison.poison_promotion_rate poison.poison_claims_at_root precision_strict false_association_rate "
                       "recall_global recall_cross_team", "security.record_detection_f1"),
    "edge_security": _keys("poison.poison_promotion_rate precision_strict", "security.record_detection_recall "
                           "security.record_detection_precision security.record_detection_f1 security.record_false_suppression "
                           "security.total_tokens_to_cloud security.canary_leakage"),
    "privacy": _keys("raw_sensitive_leakage n_canaries_exposed fraction_raw_exposed reconstructability recall_global "
                     "recall_cross_team", "privacy.small_cell_fraction_k3 privacy.small_cell_fraction_k10"),
    "contradictions": _keys("contradiction_f1 contradiction_precision contradiction_recall correct_resolution_rate "
                            "incorrect_resolution_rate unresolved_when_appropriate time_to_detect_mean recall_contradiction"),
    "temporal": _keys("recall_temporal revision_accuracy stale_persistence revision_latency old_evidence_retention "
                      "catastrophic_overwrite"),
    "hierarchy": _keys("recall_global recall_cross_team precision_strict bytes_transmitted compression_ratio ttd_global_median"),
    "scaling": _keys("runtime_s tokens bytes_transmitted bytes_off_device model_calls recall_global", "runtime_s peak_rss_mb"),
    "models": _keys("recall_global recall_cross_team precision_strict poison.poison_promotion_rate latency_ms_est energy_j_est "
                    "cost_usd_est"),
    "questioning": _keys("recall_global recall_cross_team bytes_transmitted", "questioning.questions_asked "
                         "questioning.question_bytes questioning.marginal_value_per_question questioning.delta_value"),
    "independent_support": _keys("precision_strict recall_cross_team", "support.is_mae support.is_within_1 "
                                 "support.correlated_discount_ok support.fake_consensus_resisted support.true_clusters_accepted"),
    "independent_support_clusters": _keys("", "abs_error abs_error_vs_cluster estimated_is true_is_cell accepted_at_root"),
    "routing": _keys("recall_at_budget recall_full precision unnecessary_escalation_rate correct_destination cross_team_discovery "
                     "messages bytes"),
    "failures": _keys("", "knowledge_survival useful_discovery_survival lineage_survival contradiction_detection_survival "
                      "recovery_latency repair_quality false_reconstruction_rate"),
}
FALLBACK_SKIP = tuple(_M + p for p in ("per_layer.", "fidelity.", "stats.", "categories.", "per_shape.", "poison.poison_by_layer.",
                                       "failure_reasons.", "n_", "workload."))
FIGURE_FAMILY = {"F1": "scaling", "F2": "compression", "F3": "poisoning", "F4": "privacy", "F5": "aggregation", "F6": "failures",
                 "F7": "aggregation", "F8": "models", "F9": "hierarchy", "F10": "aggregation", "investor": "headline",
                 "fig_headline": "headline"}
_BOOL_TEXT = {"True": 1.0, "False": 0.0, "true": 1.0, "false": 0.0, True: 1.0, False: 0.0}
MIN_PAIRS = 3
MAX_FAILED_LISTED = 25


def info(msg: str) -> None:
    print(f"[report] {msg}", flush=True)


# ----- loading ---------------------------------------------------------------------------------------------
@dataclass
class Family:
    name: str
    path: Path
    sha256: str
    n_rows: int
    ok: pd.DataFrame                                 # status == ok, numeric-coerced, duplicates superseded
    failed: pd.DataFrame                             # status != ok
    n_superseded: int
    cond_cols: list[str]
    metric_cols: list[str]
    key_metrics: list[str]
    groups: list[tuple[dict[str, Any], pd.DataFrame]]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(root: Path) -> tuple[str | None, bool | None]:
    """(commit, dirty) of the repository containing ``root``; (None, None) when git is unavailable."""
    try:
        def run(*a: str) -> Any:
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, timeout=10)
        sha, st = run("rev-parse", "HEAD"), run("status", "--porcelain", "--untracked-files=no")
        return (sha.stdout.strip() if sha.returncode == 0 else None), (bool(st.stdout.strip()) if st.returncode == 0 else None)
    except Exception:
        return None, None


def is_metric_column(c: str) -> bool:
    return c not in ID_COLS and c not in CONDITION_CANDIDATES and not c.startswith("world_")


def coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Metric columns to float where every non-null value parses (booleans -> 0/1); others stay text."""
    df = df.copy()
    for c in df.columns:
        s = df[c]
        if not is_metric_column(c):
            continue
        if s.dtype == bool:
            df[c] = s.astype(float)
        elif s.dtype == object or pd.api.types.is_string_dtype(s):
            s2 = s.map(lambda v: _BOOL_TEXT.get(v, v) if isinstance(v, (str, bool)) else v)
            num = pd.to_numeric(s2, errors="coerce")
            if num.notna().sum() == s2.notna().sum():
                df[c] = num
    return df


def condition_columns(df: pd.DataFrame) -> list[str]:
    """Candidates that vary *within* a system (a column differing only between systems, e.g. ``layers == 0`` for
    baselines, is a system property, not a sweep); tier / n_workers split whenever they vary."""
    out = []
    for c in CONDITION_CANDIDATES:
        if c not in df.columns or df[c].nunique(dropna=True) <= 1:
            continue
        if c not in ALWAYS_SPLIT and "system" in df.columns and not bool((df.groupby("system")[c].nunique(dropna=True) > 1).any()):
            continue
        out.append(c)
    return out


def supersede_duplicates(df: pd.DataFrame, cond_cols: list[str]) -> tuple[pd.DataFrame, int]:
    key = [c for c in cond_cols + ["system", "seed", "cluster_id"] if c in df.columns]
    if not key or df.empty:
        return df, 0
    order = df["timestamp"].astype(str) if "timestamp" in df.columns else pd.Series("", index=df.index)
    work = df.assign(_ord=order.to_numpy()).sort_values("_ord", kind="stable")
    kept = work.drop_duplicates(subset=key, keep="last").drop(columns="_ord").sort_index()
    return kept, int(len(df) - len(kept))


def choose_key_metrics(name: str, df: pd.DataFrame, metric_cols: list[str]) -> list[str]:
    keys = [m for m in (KEY_METRICS.get(name) or KEY_METRICS["aggregation"]) if m in metric_cols and df[m].notna().any()]
    ordered = [c for c in metric_cols if c.startswith(_M)] + [c for c in metric_cols if not c.startswith(_M)]
    for c in ordered:                                   # fall back to the first informative numeric metric columns
        if len(keys) >= 4:
            break
        if c not in keys and not c.startswith(FALLBACK_SKIP) and df[c].notna().any():
            keys.append(c)
    return keys[:8]


def condition_groups(df: pd.DataFrame, cond_cols: list[str]) -> list[tuple[dict[str, Any], pd.DataFrame]]:
    if not cond_cols:
        return [({}, df)]
    try:
        grouped = list(df.groupby(cond_cols, dropna=False, sort=True))
    except TypeError:                                   # mixed types in a key column cannot be sorted
        grouped = list(df.groupby(cond_cols, dropna=False, sort=False))
    return [({c: _py(v) for c, v in zip(cond_cols, key if isinstance(key, tuple) else (key,))}, sub) for key, sub in grouped]


def load_family(path: Path) -> Family:
    df = pd.read_csv(path, low_memory=False)
    if "status" in df.columns:
        ok_mask = df["status"].isna() | df["status"].astype(str).str.lower().isin(["ok", "success", "done"])
    else:
        ok_mask = pd.Series(True, index=df.index)
    ok = df[ok_mask]
    if "system" in ok.columns:
        ok = ok[ok["system"].notna()]
    ok = coerce_numeric(ok)
    cond_cols = condition_columns(ok)
    ok, n_dup = supersede_duplicates(ok, cond_cols)
    metric_cols = [c for c in ok.columns if is_metric_column(c) and pd.api.types.is_numeric_dtype(ok[c])]
    return Family(name=path.stem, path=path, sha256=sha256_of(path), n_rows=int(len(df)), ok=ok, failed=df[~ok_mask], n_superseded=n_dup,
                  cond_cols=cond_cols, metric_cols=metric_cols, key_metrics=choose_key_metrics(path.stem, ok, metric_cols),
                  groups=condition_groups(ok, cond_cols))


# ----- statistics (per-seed means, unpaired summaries, paired comparisons) -----------------------------------
def per_seed_means(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """One row per seed (mean over that seed's rows, e.g. per-cluster rows); without a seed column rows are their own seed."""
    return df.groupby("seed", sort=True)[cols].mean() if "seed" in df.columns else df[cols].astype(float)


def column_stats(per_seed: pd.DataFrame, raw: pd.DataFrame, cols: list[str]) -> dict[str, dict[str, Any]]:
    if not cols:
        return {}
    v = per_seed[cols].to_numpy(dtype=float) if len(per_seed) else np.full((1, len(cols)), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        n = (~np.isnan(v)).sum(axis=0)
        mean, mn, mx = np.nanmean(v, axis=0), np.nanmin(v, axis=0), np.nanmax(v, axis=0)
        sd = np.where(n > 1, np.nanstd(v, axis=0, ddof=1), np.nan)
    n_nan = raw[cols].isna().sum(axis=0)
    return {c: {"n": int(n[i]), "n_nan": int(n_nan[c]), "mean": _f(mean[i]), "sd": _f(sd[i]), "min": _f(mn[i]), "max": _f(mx[i])}
            for i, c in enumerate(cols)}


def paired(a: pd.Series, b: pd.Series, n_boot: int, seed: int) -> dict[str, Any]:
    """Seed-paired comparison ``a - b`` (inner join on seed, NaN pairs dropped) through ``stats.paired_summary``."""
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    if len(j) < MIN_PAIRS:
        return {"n_pairs": int(len(j)), "skipped": f"only {len(j)} paired seed(s), fewer than {MIN_PAIRS}"}
    ps = paired_summary(j["a"].to_numpy(), j["b"].to_numpy(), n_boot=n_boot, seed=seed)
    return {"n_pairs": int(ps["n"]), "mean": ps["mean_a"], "mean_ref": ps["mean_b"], "diff": ps["diff"], "ci_low": ps["ci_low"],
            "ci_high": ps["ci_high"], "t_ci_low": ps["t_ci_low"], "t_ci_high": ps["t_ci_high"], "t_p": ps["t_p"],
            "wilcoxon_p": ps["wilcoxon_p"], "cohen_dz": ps["cohen_dz"], "holm_p": float("nan"), "wilcoxon_holm_p": float("nan"),
            "seeds": [_py(s) for s in j.index.tolist()]}


def add_holm(records: list[dict[str, Any]]) -> None:
    """Holm step-down over the raw p-values of every non-skipped record of the same metric (family x metric group)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if "skipped" not in r:
            groups.setdefault(r["metric"], []).append(r)
    for recs in groups.values():
        for raw, adj in (("t_p", "holm_p"), ("wilcoxon_p", "wilcoxon_holm_p")):
            for r, p in zip(recs, holm([r[raw] for r in recs])):
                r[adj] = float(p)


def system_comparisons(fam: Family, baseline: str, n_boot: int, seed: int) -> list[dict[str, Any]]:
    """Every non-baseline system vs the baseline, per condition and key metric; Holm within family x metric."""
    out: list[dict[str, Any]] = []
    if "seed" not in fam.ok.columns or "system" not in fam.ok.columns:
        return out
    for values, sub in fam.groups:
        if baseline not in set(sub["system"]):
            continue
        base = per_seed_means(sub[sub["system"] == baseline], fam.key_metrics)
        for system in sorted(set(sub["system"]) - {baseline}):
            ps = per_seed_means(sub[sub["system"] == system], fam.key_metrics)
            out += [{"condition": cond_label(values), "values": values, "system": system, "metric": m, **paired(ps[m], base[m], n_boot, seed)}
                    for m in fam.key_metrics]
    add_holm(out)
    return out


def reference_condition(conds: list[dict[str, Any]]) -> dict[str, Any]:
    """The condition matching most REFERENCE_VALUES (the runners' defaults); ties -> first in sorted grid order."""
    return max(conds, key=lambda c: sum(1 for k, v in c.items() if k in REFERENCE_VALUES and _same(v, REFERENCE_VALUES[k]))) if conds else {}


def condition_comparisons(fam: Family, baseline: str, n_boot: int, seed: int) -> dict[str, dict[str, Any]]:
    """Per system: every swept condition vs the reference condition (seed-paired); Holm within system x metric."""
    out: dict[str, dict[str, Any]] = {}
    if len(fam.groups) < 2 or "seed" not in fam.ok.columns or "system" not in fam.ok.columns:
        return out
    present = sorted(set(fam.ok["system"]))
    for system in ([baseline] if baseline in present else present):
        rows = [(v, s[s["system"] == system]) for v, s in fam.groups if (s["system"] == system).any()]
        if len(rows) < 2:
            continue
        ref = reference_condition([v for v, _ in rows])
        ref_ps = per_seed_means(next(s for v, s in rows if v == ref), fam.key_metrics)
        recs = []
        for values, s in rows:
            if values != ref:
                ps = per_seed_means(s, fam.key_metrics)
                recs += [{"condition": cond_label(values), "values": values, "system": system, "metric": m,
                          **paired(ps[m], ref_ps[m], n_boot, seed)} for m in fam.key_metrics]
        add_holm(recs)
        out[system] = {"reference": cond_label(ref), "reference_values": ref, "comparisons": recs}
    return out


# ----- small helpers ---------------------------------------------------------------------------------------
def _py(v: Any) -> Any:
    """Plain-Python scalar: numpy -> python, NaN / inf / pd.NA -> None (JSON-safe, comparable)."""
    if isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, (np.bool_, np.integer)):
        return v.item()
    if isinstance(v, (float, np.floating)):
        return _f(v)
    try:
        return None if v is None or pd.isna(v) else v
    except (TypeError, ValueError):
        return v


def _f(x: Any) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _same(a: Any, b: Any) -> bool:
    try:
        return math.isclose(float(a), float(b))
    except (TypeError, ValueError):
        return str(a) == str(b)


def fval(v: Any) -> str:
    v = _py(v)
    return "n/a" if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))


def cond_label(values: dict[str, Any]) -> str:
    return ", ".join(f"{k}={fval(v)}" for k, v in values.items()) if values else "single condition"


def fnum(x: Any, digits: int = 4) -> str:
    if _f(x) is None:
        return "n/a" if x is None or math.isnan(float(x)) else ("inf" if float(x) > 0 else "-inf")
    x = float(x)
    return "0" if x == 0 else (f"{x:.3e}" if (abs(x) >= 1e5 or abs(x) < 1e-3) else f"{x:.{digits}g}")


def fp(p: Any) -> str:
    return "n/a" if _f(p) is None else ("<0.001" if float(p) < 0.001 else f"{float(p):.3f}")


def fci(lo: Any, hi: Any) -> str:
    return "n/a" if _f(lo) is None or _f(hi) is None else f"[{fnum(lo)}, {fnum(hi)}]"


def fmt_seeds(seeds: list[Any]) -> str:
    """Compact seed list: consecutive integers become ranges."""
    try:
        s = sorted({int(x) for x in seeds})
    except (TypeError, ValueError):
        return ", ".join(sorted(str(x) for x in set(seeds)))
    runs: list[list[int]] = []
    for x in s:
        if runs and x == runs[-1][1] + 1:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    def esc(x: Any) -> str:
        return str(x).replace("|", "\\|").replace("\n", " ")
    return ("| " + " | ".join(esc(h) for h in headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n"
            + "".join("| " + " | ".join(esc(c) for c in r) + " |\n" for r in rows))


def short(m: str) -> str:
    return m[len(_M):] if m.startswith(_M) else m


def figure_refs(figdir: Path) -> list[tuple[str, Path, str]]:
    """(caption, path, family-or-'') for every PNG under ``figdir``; the caption is derived from the file name."""
    out = []
    for p in (sorted(figdir.glob("*.png")) if figdir.is_dir() else []):
        head, _, rest = p.stem.partition("_")
        caption = f"{head} — {rest.replace('_', ' ')}" if head[:1] == "F" and head[1:].isdigit() and rest else p.stem.replace("_", " ")
        out.append((caption, p, next((f for k, f in FIGURE_FAMILY.items() if p.stem.startswith(k + "_") or p.stem == k), "")))
    return out


def rel(path: Path, out_md: Path) -> str:
    """Path relative to the document (for links); absolute when the two trees are far apart."""
    r = os.path.relpath(path.resolve(), out_md.resolve().parent).replace(os.sep, "/")
    return r if r.count("../") <= 3 else str(path.resolve())


def condition_grid(df: pd.DataFrame) -> dict[str, list[Any]]:
    grid = {}
    for c in CONDITION_CANDIDATES:
        if c in df.columns and df[c].notna().any():
            vals = df[c].dropna().unique().tolist()
            try:
                vals = sorted(vals)
            except TypeError:
                vals = sorted(vals, key=str)
            grid[c] = [_py(v) for v in vals[:12]] + (["…"] if len(vals) > 12 else [])
    return grid


# ----- markdown --------------------------------------------------------------------------------------------
STATS_NOTE = ("Δ = mean of the per-seed differences (row − reference) over the seeds both ran on; 95% CI from the paired percentile "
              "bootstrap ({n_boot} resamples, seed {seed}; it under-covers below ~20 seeds, so the paired-t CI is given next to it); "
              "paired t-test and Wilcoxon signed-rank p-values, each Holm-corrected within this family × metric group; d_z = Cohen's "
              "d_z = Δ / SD of the differences. Comparisons with fewer than {min_pairs} paired seeds are skipped and say so.\n\n")
CMP_HEADERS = ["n pairs", "mean", "ref. mean", "Δ", "95% CI (bootstrap)", "95% CI (paired t)", "t p", "Holm p", "Wilcoxon p",
               "Holm p (W)", "d_z"]


def cmp_table(recs: list[dict[str, Any]], first: list[str], prefix: Any) -> str:
    """One comparison table (or a single line when every comparison of the metric was skipped)."""
    if not recs:
        return "_No comparison._\n\n"
    if all("skipped" in r for r in recs):
        return f"_All {len(recs)} comparisons skipped: {'; '.join(sorted({r['skipped'] for r in recs}))}._\n\n"
    rows = []
    for r in recs:
        cells = ([str(r["n_pairs"]), f"skipped: {r['skipped']}"] + ["—"] * (len(CMP_HEADERS) - 2)) if "skipped" in r else \
            [str(r["n_pairs"]), fnum(r["mean"]), fnum(r["mean_ref"]), fnum(r["diff"]), fci(r["ci_low"], r["ci_high"]),
             fci(r["t_ci_low"], r["t_ci_high"]), fp(r["t_p"]), fp(r["holm_p"]), fp(r["wilcoxon_p"]), fp(r["wilcoxon_holm_p"]),
             fnum(r["cohen_dz"], 3)]
        rows.append(prefix(r) + cells)
    return md_table(first + CMP_HEADERS, rows) + "\n"


def family_section(fam: Family, sys_recs: list[dict[str, Any]], cond_cmp: dict[str, dict[str, Any]], baseline: str,
                   figs: list[tuple[str, Path, str]], tables: list[Path], out_md: Path, n_boot: int, seed: int) -> str:
    ok, with_cond = fam.ok, bool(fam.cond_cols)
    L = [f"## {fam.name}\n\n"] + ([f"_{FAMILY_NOTE[fam.name]}_\n\n"] if fam.name in FAMILY_NOTE else [])
    dup = f" ({fam.n_superseded} earlier duplicate rows superseded by re-runs)" if fam.n_superseded else ""
    L.append(f"Source: `{rel(fam.path, out_md)}` — {fam.n_rows} rows: {len(ok)} ok used{dup}, {len(fam.failed)} not ok. "
             f"sha256 `{fam.sha256}`.\n\n")
    systems = sorted(set(ok["system"])) if "system" in ok.columns else []
    seeds = sorted(set(ok["seed"].dropna().tolist())) if "seed" in ok.columns else []
    L.append(f"Seeds ({len(seeds)}): {fmt_seeds(seeds) if seeds else 'no `seed` column'}. "
             f"Systems ({len(systems)}): {', '.join(f'`{s}`' for s in systems) or 'no `system` column'}.\n\n")
    if len(fam.failed):
        L.append(f"Failed / skipped runs ({len(fam.failed)}, excluded from every statistic):\n\n")
        for _, r in fam.failed.head(MAX_FAILED_LISTED).iterrows():
            L.append(f"- `{r.get('run_id', '?')}` — {r.get('system', '?')}, seed {fval(r.get('seed'))}, condition "
                     f"{r.get('condition', '?')}: {r.get('status', '?')}: {str(r.get('error', ''))[:160]}\n")
        L.append((f"- … and {len(fam.failed) - MAX_FAILED_LISTED} more (see the CSV `status` column)\n" if len(fam.failed) > MAX_FAILED_LISTED else "") + "\n")
    else:
        L.append("Failed runs: none.\n\n")
    grid = condition_grid(ok)
    if grid:
        L.append("Condition grid (distinct values over the ok rows; * = swept within a system and used to group runs):\n\n"
                 + md_table(["column", "distinct values"], [[f"`{c}`" + (" *" if c in fam.cond_cols else ""), ", ".join(fval(v) for v in vals)]
                                                             for c, vals in grid.items()]) + "\n")
    L.append(("Grouping columns: " + ", ".join(f"`{c}`" for c in fam.cond_cols) if with_cond else "No swept condition column")
             + f" → {len(fam.groups)} condition(s).\n\n")
    absent = [m for m in KEY_METRICS.get(fam.name, []) if m not in ok.columns]
    L.append("Key metrics: " + ", ".join(f"`{m}`" for m in fam.key_metrics) + "."
             + (" Preferred columns absent from this CSV (not available): " + ", ".join(f"`{m}`" for m in absent) + "." if absent else "") + "\n\n")
    # key metrics per condition x system
    L.append("### Key metrics per condition and system\n\nMean ± SD over seeds (n = seeds with an ok run; `NaN k` = runs whose value "
             "is missing, dropped before averaging).\n\n")
    rows = []
    for values, sub in fam.groups:
        for system, ss in (sub.groupby("system", sort=True) if "system" in sub.columns else [("all rows", sub)]):
            ps = per_seed_means(ss, fam.key_metrics)
            st = column_stats(ps, ss, fam.key_metrics)
            cells = ([cond_label(values)] if with_cond else []) + [f"`{system}`", str(len(ps))]
            for m in fam.key_metrics:
                s = st[m]
                txt = "n/a" if s["n"] == 0 else (fnum(s["mean"]) if s["sd"] is None else f"{fnum(s['mean'])} ± {fnum(s['sd'], 3)}")
                cells.append(txt + (f" (NaN {s['n_nan']})" if s["n_nan"] else ""))
            rows.append(cells)
    L.append(md_table((["condition"] if with_cond else []) + ["system", "n"] + [short(m) for m in fam.key_metrics], rows) + "\n")
    # systems vs baseline
    L.append(f"### Paired comparisons vs `{baseline}` (same seeds, same condition)\n\n")
    if "seed" not in ok.columns or "system" not in ok.columns:
        L.append("_Not available: the CSV has no `seed`/`system` column, so runs cannot be paired._\n\n")
    elif baseline not in systems:
        L.append(f"_Baseline `{baseline}` was not run in this family; no system comparison is possible "
                 "(see the condition-paired comparisons below)._\n\n")
    elif not sys_recs:
        L.append(f"_Only `{baseline}` was run in this family; no other system to compare._\n\n")
    else:
        L.append(STATS_NOTE.format(n_boot=n_boot, seed=seed, min_pairs=MIN_PAIRS))
        for m in fam.key_metrics:
            L.append(f"#### `{m}`\n\n" + cmp_table([r for r in sys_recs if r["metric"] == m], (["condition"] if with_cond else []) + ["system"],
                                                  lambda r: ([r["condition"]] if with_cond else []) + [f"`{r['system']}`"]))
    # conditions vs reference
    for system, block in cond_cmp.items():
        L.append(f"### Condition-paired comparisons for `{system}` (reference condition: {block['reference']})\n\n"
                 "Each swept condition against the reference condition of the same system, paired by seed; the reference is the "
                 "condition closest to the runner defaults. " + STATS_NOTE.format(n_boot=n_boot, seed=seed, min_pairs=MIN_PAIRS))
        for m in fam.key_metrics:
            L.append(f"#### `{m}`\n\n" + cmp_table([r for r in block["comparisons"] if r["metric"] == m], ["condition"], lambda r: [r["condition"]]))
    if not cond_cmp and len(fam.groups) < 2:
        L.append("_Condition-paired comparisons: not applicable (one condition)._\n\n")
    # figures / tables
    own = [(c, p) for c, p, f in figs if f == fam.name]
    if own:
        L.append("### Figures\n\n" + "".join(f"![{c}]({rel(p, out_md)})\n\n{c} (`{p.name}`)\n\n" for c, p in own))
    links = [p for p in tables if p.stem == f"stats_{fam.name}" or (fam.name == "headline" and p.stem == "executive_comparison")]
    if links:
        L.append("Tables: " + ", ".join(f"[{p.name}]({rel(p, out_md)})" for p in links) + "\n\n")
    return "".join(L)


def document(meta: dict[str, Any], families: dict[str, Family], sections: list[str], missing: list[str],
             figs: list[tuple[str, Path, str]], tables: list[Path], out_md: Path) -> str:
    L = ["# Benchmark results (generated)\n\n",
         f"Generated {meta['generated_at']} by `experiments/report.py` from `{meta['processed_dir']}` at git commit "
         f"`{meta['git_commit'] or 'unknown'}`{' (working tree dirty)' if meta.get('git_dirty') else ''}. Baseline system `{meta['baseline']}`; "
         f"paired bootstrap {meta['n_boot']} resamples (seed {meta['bootstrap_seed']}). Every number below is computed from the listed "
         "CSVs by this script; nothing is typed by hand. Model backend: simulated profiles unless a run manifest says otherwise "
         f"(DESIGN.md §9). Machine-readable copy: `{meta['summary_path']}`.\n\n## Source files\n\n"]
    rows = [[f"`{f.name}`", f"`{rel(f.path, out_md)}`", str(f.n_rows), str(len(f.ok)), str(len(f.failed)), str(f.n_superseded), f"`{f.sha256[:16]}…`"]
            for f in families.values()]
    L.append(md_table(["family", "file", "rows", "ok used", "not ok", "superseded", "sha256"], rows) if rows else "_No processed CSV found._\n")
    if missing:
        L.append("\nNot run / not available (no processed CSV): " + ", ".join(f"`{m}`" for m in missing) + ".\n")
    L.append("\n## Figures\n\n" + ("".join(f"- [{c}]({rel(p, out_md)})" + (f" — family `{f}`" if f else "") + "\n" for c, p, f in figs) + "\n"
                                  if figs else "_No figure PNG found (run `experiments/figures.py`)._\n\n"))
    L.append("## Tables\n\n" + ("".join(f"- [{p.name}]({rel(p, out_md)})\n" for p in tables) + "\n" if tables
                               else "_No table found (run `experiments/tables.py`)._\n\n"))
    L.extend(sections)
    other = [(c, p) for c, p, f in figs if f not in families]
    if other:
        L.append("## Other figures\n\n" + "".join(f"![{c}]({rel(p, out_md)})\n\n{c} (`{p.name}`)\n\n" for c, p in other))
    return "".join(L)


# ----- summary JSON ----------------------------------------------------------------------------------------
def family_summary(fam: Family, sys_recs: list[dict[str, Any]], cond_cmp: dict[str, dict[str, Any]], out_md: Path) -> dict[str, Any]:
    conds: dict[str, Any] = {}
    for values, sub in fam.groups:
        label, systems = cond_label(values), {}
        for system, ss in (sub.groupby("system", sort=True) if "system" in sub.columns else [("all rows", sub)]):
            ps = per_seed_means(ss, fam.metric_cols)
            systems[str(system)] = {
                "n": int(len(ps)), "n_rows": int(len(ss)), "seeds": [_py(s) for s in ps.index.tolist()],
                "metrics": column_stats(ps, ss, fam.metric_cols),
                "paired_vs_baseline": {r["metric"]: {k: v for k, v in r.items() if k not in ("condition", "values", "system", "metric")}
                                       for r in sys_recs if r["condition"] == label and r["system"] == system}}
        conds[label] = {"values": values, "systems": systems}
    failed = [{"run_id": _py(r.get("run_id")), "system": _py(r.get("system")), "seed": _py(r.get("seed")), "condition": _py(r.get("condition")),
               "status": _py(r.get("status")), "error": str(r.get("error", ""))[:300]} for _, r in fam.failed.iterrows()]
    return {"source": rel(fam.path, out_md), "sha256": fam.sha256, "n_rows": fam.n_rows, "n_ok": int(len(fam.ok)), "n_failed": int(len(fam.failed)),
            "n_superseded": fam.n_superseded, "seeds": [_py(s) for s in sorted(set(fam.ok["seed"].dropna().tolist()))] if "seed" in fam.ok.columns else [],
            "systems": sorted(set(fam.ok["system"])) if "system" in fam.ok.columns else [], "condition_columns": fam.cond_cols,
            "condition_grid": condition_grid(fam.ok), "key_metrics": fam.key_metrics, "metric_columns": fam.metric_cols,
            "failed_runs": failed, "conditions": conds, "condition_comparisons": cond_cmp}


def clean(obj: Any) -> Any:
    """JSON-safe copy: numpy scalars -> python, NaN/inf -> None, Paths -> str, keys -> str."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return str(obj) if isinstance(obj, Path) else _py(obj)


# ----- driver ----------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", default="results", help="results root containing processed/*.csv")
    ap.add_argument("--out", default="docs/RESULTS.md", help="Markdown results document to write")
    ap.add_argument("--summary", default=None, help="machine-readable summary (default: <results>/summary.json)")
    ap.add_argument("--baseline", default="B7_mycelic", help="system every other system is paired against")
    ap.add_argument("--n-boot", type=int, default=10000, help="paired bootstrap resamples")
    ap.add_argument("--seed", type=int, default=0, help="bootstrap RNG seed")
    ap.add_argument("--figures", default=None, help="figure directory (default: <results>/figures)")
    ap.add_argument("--tables", default=None, help="table directory (default: <results>/tables)")
    args = ap.parse_args(argv)
    results, out_md = Path(args.results), Path(args.out)
    processed = results / "processed"
    summary_path = Path(args.summary) if args.summary else results / "summary.json"
    figs = figure_refs(Path(args.figures) if args.figures else results / "figures")
    tabdir = Path(args.tables) if args.tables else results / "tables"
    tables = sorted(tabdir.glob("*.md")) if tabdir.is_dir() else []
    commit, dirty = git_commit(HERE.parent)
    meta = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git_commit": commit, "git_dirty": dirty,
            "baseline": args.baseline, "n_boot": int(args.n_boot), "bootstrap_seed": int(args.seed), "results_root": str(results),
            "processed_dir": str(processed), "summary_path": str(summary_path), "min_pairs": MIN_PAIRS}
    families: dict[str, Family] = {}
    errors: dict[str, str] = {}
    for p in (sorted(processed.glob("*.csv")) if processed.is_dir() else []):
        try:
            families[p.stem] = load_family(p)
            info(f"{p.name}: {families[p.stem].n_rows} rows, {len(families[p.stem].ok)} ok, {len(families[p.stem].groups)} condition(s)")
        except Exception as e:  # a corrupt or half-written CSV must not kill the report
            errors[p.stem] = f"{type(e).__name__}: {e}"
            info(f"WARNING: could not load {p}: {errors[p.stem]}")
    order = [f for f in FAMILY_ORDER if f in families] + sorted(f for f in families if f not in FAMILY_ORDER)
    missing = [f for f in FAMILY_ORDER if f not in families]
    sections, summary_fams = [], {}
    for name in order:
        fam = families[name]
        try:
            sys_recs = system_comparisons(fam, args.baseline, args.n_boot, args.seed)
            cond_cmp = condition_comparisons(fam, args.baseline, args.n_boot, args.seed)
            sections.append(family_section(fam, sys_recs, cond_cmp, args.baseline, figs, tables, out_md, args.n_boot, args.seed))
            summary_fams[name] = family_summary(fam, sys_recs, cond_cmp, out_md)
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"
            info(f"WARNING: section {name} failed: {errors[name]}")
            sections.append(f"## {name}\n\n_Report generation failed for `{fam.path}`: {errors[name]}_\n\n")
    sections += [f"## {n}\n\n_Could not read `{processed / (n + '.csv')}`: {e}_\n\n" for n, e in errors.items() if n not in families]
    sections += [f"## {m}\n\n_Not run / not available: `{processed / (m + '.csv')}` does not exist._\n\n" for m in missing]
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(document(meta, families, sections, missing, figs, tables, out_md), encoding="utf-8")
    summary = {**meta, "sources": [{"family": f.name, "path": str(f.path), "rows": f.n_rows, "rows_ok": int(len(f.ok)),
                                    "rows_failed": int(len(f.failed)), "sha256": f.sha256} for f in families.values()],
               "families_missing": missing, "errors": errors, "figures": [str(p) for _, p, _ in figs], "tables": [str(p) for p in tables],
               "families": summary_fams}
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(clean(summary), f, indent=1, allow_nan=False)
    info(f"wrote {out_md} ({len(order)} family section(s), {len(missing)} missing) and {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
