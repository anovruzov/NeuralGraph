#!/usr/bin/env python3
"""Publication figures for the Mycelic benchmark — produced ONLY from ``results/processed/*.csv``.

Usage::

    python experiments/figures.py --results results            # writes results/figures/*.png + *.pdf
    python experiments/figures.py --results results --out /tmp/figs --only F3,F8 --strict

Every figure reads one processed CSV (flat rows written by ``experiments/common.py``:
``family, system, seed, tier, n_workers`` + condition columns + dotted metric columns).
A missing CSV or a missing required column skips the figure with a printed warning;
optional columns add panels when present.  Rows whose ``status`` column is not ``ok``
(failed runs) are dropped before plotting.  Error bars are 95% t-based confidence
intervals across seeds (``mycelic_bench.stats.summarize``); a point with one seed has
no error bar.

Expected CSV columns per figure
-------------------------------
F1  discovery vs org size            scaling.csv       system, seed, n_workers, metrics.recall_global
                                                       optional: metrics.recall_cross_team, metrics.runtime_s, metrics.peak_rss_mb
F2  useful information retained      compression.csv   system, seed, compression, sketch_order, metrics.compression_ratio,
    vs compression ratio                               metrics.recall_global (fallback: metrics.fidelity.executive.useful_fact_recall)
                                                       optional: cadence (facets), metrics.recall_cross_team (second row)
F3  poison propagation across layers poisoning.csv     system, seed, malicious_fraction, metrics.poison.poison_promotion_rate
                                                       optional: metrics.poison.poison_by_layer.{team,department,region,executive}
                                                       (stacked-by-layer panels), detector (modal value kept)
F4  privacy leakage vs centralized   privacy.csv       system, seed, metrics.raw_sensitive_leakage and/or metrics.fraction_raw_exposed
                                                       optional: quote_policy, k_anonymity (row labels), metrics.reconstructability
F5  cross-team emergent discovery    aggregation.csv   system, seed, metrics.recall_cross_team
    rate                             (fallback headline.csv)   optional: metrics.recall_local, metrics.recall_global, metrics.precision_strict
F6  knowledge survival under failures failures.csv     system, seed, failure_kind, failure_rate, knowledge_survival
                                                       optional: useful_discovery_survival, lineage_survival
F7  accuracy vs communication cost   aggregation.csv   system, seed, metrics.bytes_transmitted, metrics.recall_global
                                     (fallback headline.csv)   optional: metrics.cost_usd_est, metrics.tokens_to_cloud
F8  edge-model Pareto frontier       models.csv        system, seed, profile, metrics.recall_global, and one of
    (SYNTHETIC capability sweep)                       metrics.latency_ms_est / metrics.energy_j_est / metrics.cost_usd_est
F9  hierarchy-depth ablation         hierarchy.csv     system, seed, layers, metrics.recall_global
                                                       optional: metrics.recall_cross_team, metrics.precision_strict, metrics.bytes_transmitted
F10 time-to-discovery distribution   aggregation.csv   system, seed, metrics.ttd_global_median
                                     (fallback headline.csv, temporal.csv)   optional: metrics.ttd_cross_team_median
investor_comparison                  headline.csv      system, seed + any of the executive-table metrics:
                                     (fallback aggregation.csv)  metrics.recall_global, metrics.recall_cross_team,
                                                       metrics.poison.poison_promotion_rate, metrics.fraction_raw_exposed,
                                                       metrics.compression_ratio, metrics.cost_usd_est, metrics.bytes_transmitted
fig_headline_table                   same as investor_comparison (rendered as a table image; mean ± SD, n seeds)

When a CSV carries several values of a condition column that a figure does not sweep
(e.g. ``detector`` in poisoning.csv), the modal value is kept and printed.

Styling: matplotlib Agg backend, 300 dpi PNG + vector PDF, colour-blind-safe palette
validated with the dataviz ``validate_palette.js`` checks (adjacent CVD ΔE ≥ 8, normal-vision
ΔE ≥ 15 in the fixed system order), fixed system→colour/marker mapping shared by every
figure, thin marks, hairline solid grid, text in ink tokens (never in a series colour).
"""

from __future__ import annotations

import argparse
import math
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

HERE = Path(__file__).resolve().parent
_SRC = HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from mycelic_bench.stats import summarize_groups  # noqa: E402

# ---------------------------------------------------------------------------
# Palette & chrome (dataviz reference palette, light surface; validated)
# ---------------------------------------------------------------------------
SURFACE = "#ffffff"
INK = "#0b0b0b"          # primary text
INK2 = "#52514e"         # secondary text / error bars
MUTED = "#898781"        # axis labels, footnotes, "no aggregation" reference
GRID = "#e1e0d9"         # hairline gridlines
AXIS = "#c3c2b7"         # axis rules

# Fixed display order of systems; colours are assigned to the ENTITY and never re-cycled.
SYSTEM_ORDER = [
    "B0_isolated", "B1_central_keyword", "B2_central_rag", "B3_central_llm_summary", "B4_majority_vote",
    "B5_flat_agents", "B6_hier_no_lineage", "B7_mycelic", "B8_mycelic_security", "B9_mycelic_questioning",
    "ORACLE_central_stats",
]
SYSTEM_LABEL = {
    "B0_isolated": "B0 isolated workers", "B1_central_keyword": "B1 central keyword", "B2_central_rag": "B2 central RAG",
    "B3_central_llm_summary": "B3 central LLM summary", "B4_majority_vote": "B4 majority vote",
    "B5_flat_agents": "B5 flat agents", "B6_hier_no_lineage": "B6 hierarchy, no lineage", "B7_mycelic": "B7 Mycelic",
    "B8_mycelic_security": "B8 Mycelic + security", "B9_mycelic_questioning": "B9 Mycelic + questioning",
    "ORACLE_central_stats": "Oracle (upper bound)",
}
SYSTEM_SHORT = {
    "B0_isolated": "B0", "B1_central_keyword": "B1", "B2_central_rag": "B2", "B3_central_llm_summary": "B3",
    "B4_majority_vote": "B4", "B5_flat_agents": "B5", "B6_hier_no_lineage": "B6", "B7_mycelic": "B7",
    "B8_mycelic_security": "B8", "B9_mycelic_questioning": "B9", "ORACLE_central_stats": "Oracle",
}
# Adjacent pairs in SYSTEM_ORDER pass: worst CVD ΔE 15.3, worst normal-vision ΔE 20.8 (validate_palette.js, #ffffff).
# References (B0 floor, Oracle ceiling) wear neutral ink tokens and dotted/dashed lines.
SYSTEM_STYLE: dict[str, dict[str, Any]] = {
    "B0_isolated":            dict(color=MUTED,     marker="v", ls=":"),
    "B1_central_keyword":     dict(color="#eb6834", marker="P", ls="-"),
    "B2_central_rag":         dict(color="#4a3aa7", marker="X", ls="-"),
    "B3_central_llm_summary": dict(color="#e87ba4", marker="p", ls="-"),
    "B4_majority_vote":       dict(color="#008300", marker="h", ls="-"),
    "B5_flat_agents":         dict(color="#eda100", marker="^", ls="-"),
    "B6_hier_no_lineage":     dict(color="#e34948", marker="s", ls="--"),
    "B7_mycelic":             dict(color="#2a78d6", marker="o", ls="-"),
    "B8_mycelic_security":    dict(color="#1baf7a", marker="D", ls="-"),
    "B9_mycelic_questioning": dict(color="#184f95", marker="*", ls="-"),
    "ORACLE_central_stats":   dict(color=INK,       marker="_", ls="--"),
}
_FALLBACK_MARKERS = ["o", "s", "D", "^", "v", "P", "X", "p", "h"]

# Ordinal ramp for hierarchy layers (one hue, validated --ordinal): team -> executive.
LAYER_ORDER = ["team", "department", "region", "executive"]
LAYER_COLORS = {"team": "#86b6ef", "department": "#5598e7", "region": "#256abf", "executive": "#104281"}
# Categorical palette for non-system entities (compression schemes), fixed order, validated.
COND_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
COMPRESSION_ORDER = ["full_sketch", "order_capped", "order2_plus_significant", "significant_cells_only", "claims_only",
                     "top_k_claims", "byte_budget"]
PROFILE_ORDER = ["sim-1b", "sim-3b", "sim-7b", "sim-8b", "sim-14b", "sim-frontier", "sim-perfect"]
FAILURE_ORDER = ["random_user", "random_team", "department", "region", "targeted_high_support", "targeted_lineage",
                 "stale_replica", "partition", "delayed_sync"]
CONDITION_COLS = ["malicious_fraction", "attack_type", "detector", "layers", "compression", "sketch_order", "cadence",
                  "profile", "failure_kind", "failure_rate", "questioning", "router", "quote_policy", "k_anonymity",
                  "policy", "scenario"]

# Executive table: (column, label, better, format kind)
EXEC_METRICS = [
    ("metrics.recall_global", "Global discovery", "higher", "pct"),
    ("metrics.recall_cross_team", "Cross-team synthesis", "higher", "pct"),
    ("metrics.poison.poison_promotion_rate", "Poison propagation", "lower", "pct"),
    ("metrics.fraction_raw_exposed", "Private data exposed", "lower", "pct"),
    ("metrics.compression_ratio", "Compression", "higher", "ratio"),
    ("metrics.cost_usd_est", "Cost", "lower", "usd"),
    ("metrics.bytes_transmitted", "Bytes", "lower", "bytes"),
]

F8_TITLE = "F8 — Edge-model Pareto frontier (SYNTHETIC capability sweep: simulated profiles, not measured models)"
F8_NOTE = ("Profiles are configured assumptions (configs/models.yaml, measured: false) swept synthetically; "
           "no real model was run (DESIGN.md §9). ")


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def warn(msg: str) -> None:
    print(f"[figures] WARNING: {msg}", flush=True)


def info(msg: str) -> None:
    print(f"[figures] {msg}", flush=True)


def apply_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8.5, "axes.titlesize": 9.0, "axes.titleweight": "semibold",
        "axes.titlelocation": "left", "axes.labelsize": 8.5, "legend.fontsize": 7.5, "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.6,
        "grid.linestyle": "-", "axes.axisbelow": True, "lines.linewidth": 1.6, "lines.markersize": 4.5,
        "legend.frameon": False, "figure.dpi": 100, "savefig.dpi": 300, "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "pdf.fonttype": 42, "ps.fonttype": 42,
        "xtick.major.size": 2.5, "ytick.major.size": 2.5, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    })


def load_csv(results: Path, *families: str) -> tuple[pd.DataFrame | None, str | None]:
    """First existing ``results/processed/<family>.csv`` among ``families`` (failed rows dropped)."""
    for fam in families:
        p = Path(results) / "processed" / f"{fam}.csv"
        if p.exists():
            try:
                df = pd.read_csv(p)
            except Exception as e:  # pragma: no cover - corrupt file
                warn(f"could not read {p}: {e!r}")
                continue
            if df.empty:
                warn(f"{p} is empty")
                continue
            if "status" in df.columns:
                ok = df["status"].isna() | df["status"].astype(str).str.lower().isin(["ok", "success", "done"])
                if (~ok).any():
                    info(f"{fam}.csv: dropping {int((~ok).sum())} row(s) with status != ok")
                df = df[ok]
            if "system" in df.columns:
                df = df[df["system"].notna()]
            return df.reset_index(drop=True), fam
    warn(f"missing CSV: {', '.join(f'processed/{f}.csv' for f in families)}")
    return None, None


def has_cols(df: pd.DataFrame, cols: list[str], fig: str) -> bool:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        warn(f"{fig}: required column(s) missing: {missing} — skipped")
        return False
    return True


def first_col(df: pd.DataFrame | None, *candidates: str) -> str | None:
    if df is None:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


def numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def order_systems(systems) -> list[str]:
    known = [s for s in SYSTEM_ORDER if s in set(systems)]
    unknown = sorted(s for s in set(systems) if s not in SYSTEM_ORDER)
    return known + unknown


def style(system: str) -> dict[str, Any]:
    if system in SYSTEM_STYLE:
        return SYSTEM_STYLE[system]
    # Unknown system: never generate a new hue — neutral ink with a distinct marker.
    idx = sum(1 for s in sorted(SYSTEM_STYLE) if s not in SYSTEM_ORDER)
    m = _FALLBACK_MARKERS[hash(system) % len(_FALLBACK_MARKERS)] if idx == 0 else "o"
    return dict(color=INK2, marker=m, ls="-.")


def label_of(system: str) -> str:
    return SYSTEM_LABEL.get(system, system)


def default_condition(df: pd.DataFrame, fig: str, exclude: tuple[str, ...] = ()) -> pd.DataFrame:
    """Keep the modal value of each condition column the figure does not sweep.

    A condition column that differs only *between* systems is part of each system's definition (B5 has one
    layer, B8 uses the lineage-aware detector, B9 asks questions, baselines have `layers == 0`): such columns
    are left alone, otherwise the executive comparison would silently drop whole systems.  A column that
    varies *within* a system is a swept condition: each system is reduced to its own modal value.
    """
    for c in CONDITION_COLS:
        if c in exclude or c not in df.columns:
            continue
        vals = df[c].dropna()
        if vals.nunique() <= 1:
            continue
        if "system" in df.columns:
            per_system = df.groupby("system")[c].nunique(dropna=True)
            if (per_system <= 1).all():
                continue      # differs between systems only: a system property, not a sweep
            keep = pd.Series(False, index=df.index)
            modes = {}
            for s, sub in df.groupby("system"):
                v = sub[c].dropna()
                if v.nunique() <= 1:
                    keep |= df["system"] == s
                    continue
                mode = v.mode().iloc[0]
                modes[s] = mode
                keep |= (df["system"] == s) & ((df[c] == mode) | df[c].isna())
            info(f"{fig}: column {c!r} varies within {sorted(modes)}; keeping each system's modal value {modes}")
            df = df[keep]
        else:
            mode = vals.mode().iloc[0]
            info(f"{fig}: column {c!r} has {vals.nunique()} values; keeping modal value {mode!r}")
            df = df[(df[c] == mode) | df[c].isna()]
    return df


def err_pair(mean, lo, hi) -> np.ndarray:
    """(2, n) asymmetric error array for matplotlib from mean / ci bounds; NaN -> 0 (no bar)."""
    mean = np.asarray(mean, dtype=float); lo = np.asarray(lo, dtype=float); hi = np.asarray(hi, dtype=float)
    low = np.nan_to_num(mean - lo, nan=0.0); high = np.nan_to_num(hi - mean, nan=0.0)
    return np.vstack([np.clip(low, 0, None), np.clip(high, 0, None)])


def yerr(tbl: pd.DataFrame) -> np.ndarray:
    return err_pair(tbl["mean"], tbl["ci_low"], tbl["ci_high"])


def seeds_note(df: pd.DataFrame, fam: str | None, extra: str = "") -> str:
    if "seed" in df.columns and "system" in df.columns and len(df):
        n = df.groupby("system")["seed"].nunique()
        n_txt = f"n = {int(n.min())}" if n.min() == n.max() else f"n = {int(n.min())}–{int(n.max())}"
    else:
        n_txt = "n = ?"
    tier = ""
    if "tier" in df.columns and df["tier"].notna().any():
        tiers = sorted(df["tier"].dropna().astype(str).unique())
        tier = f" Tier: {', '.join(tiers)}."
    return (f"Error bars: 95% t-CI across seeds ({n_txt} seeds per point).{tier} {extra}"
            f"Source: results/processed/{fam}.csv. Model backend: simulated profiles unless the run manifest says otherwise (DESIGN.md §9).")


def system_handles(systems: list[str]) -> tuple[list, list]:
    hs, ls = [], []
    for s in systems:
        st = style(s)
        hs.append(Line2D([0], [0], color=st["color"], marker=st["marker"], ls=st["ls"], lw=1.6, markersize=5,
                         markeredgecolor=SURFACE, markeredgewidth=0.5))
        ls.append(label_of(s))
    return hs, ls


def save(fig, out: Path, name: str) -> None:
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.png", dpi=300, bbox_inches="tight", pad_inches=0.06)
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    info(f"wrote {out / name}.png and .pdf")


def finish(fig, out: Path, name: str, note: str, systems: list[str] | None = None, legend_ncol: int | None = None) -> None:
    """Attach the shared system legend (≥2 series) and the provenance footnote, then save."""
    fig_h = fig.get_size_inches()[1]
    y = -0.03
    if systems and len(systems) >= 2:
        hs, ls = system_handles(systems)
        ncol = legend_ncol or min(len(ls), 4)
        rows = math.ceil(len(ls) / ncol)
        fig.legend(hs, ls, loc="upper center", bbox_to_anchor=(0.5, y), ncol=ncol, frameon=False, fontsize=7.5,
                   handlelength=2.4, columnspacing=1.4, handletextpad=0.6)
        y -= (0.17 * rows + 0.08) / fig_h
    fig.text(0.0, y, _wrap(note, 150), fontsize=6.6, color=MUTED, ha="left", va="top", transform=fig.transFigure)
    save(fig, out, name)


def _wrap(text: str, width: int) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(text, width=width))


def lines_by_system(ax, tbl: pd.DataFrame, xcol: str, systems: list[str]) -> None:
    for s in systems:
        sub = tbl[(tbl["system"] == s) & (tbl["n"] > 0)].sort_values(xcol)
        if sub.empty:
            continue
        st = style(s)
        ax.errorbar(sub[xcol].to_numpy(dtype=float), sub["mean"].to_numpy(), yerr=yerr(sub), color=st["color"],
                    marker=st["marker"], ls=st["ls"], lw=1.6, markersize=4.5, markeredgecolor=SURFACE,
                    markeredgewidth=0.5, capsize=2, elinewidth=0.8, ecolor=st["color"], label=label_of(s), zorder=3)


def barh_by_row(ax, labels: list[str], means, lows, highs, colors: list[str], xlabel: str = "", unit_axis: bool = False) -> None:
    """Horizontal bars, one per row entity; identity is carried by the tick label (no legend needed)."""
    y = np.arange(len(labels))
    means = np.asarray(means, dtype=float)
    ax.barh(y, np.nan_to_num(means, nan=0.0), xerr=err_pair(means, lows, highs), color=colors, height=0.62,
            edgecolor=SURFACE, linewidth=0.8, error_kw=dict(ecolor=INK2, elinewidth=0.8, capsize=2), zorder=3)
    ax.set_yticks(y); ax.set_yticklabels(labels); ax.invert_yaxis()
    ax.grid(False, axis="y"); ax.grid(True, axis="x")
    ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    if xlabel:
        ax.set_xlabel(xlabel)
    if unit_axis:
        ax.set_xlim(0, 1.0)
    for yi, m in zip(y, means):
        if np.isnan(m):
            ax.text(0.01, yi, "n/a", va="center", ha="left", fontsize=6.5, color=MUTED, transform=ax.get_yaxis_transform())
        elif m == 0:
            ax.text(0.01, yi, "0", va="center", ha="left", fontsize=6.5, color=INK2, transform=ax.get_yaxis_transform())


def pareto_front(x: np.ndarray, y: np.ndarray, minimize_x: bool) -> list[int]:
    """Indices of the non-dominated points (maximise y; minimise or maximise x)."""
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    ok = ~(np.isnan(x) | np.isnan(y))
    idx = np.flatnonzero(ok)
    order = idx[np.argsort(x[idx] if minimize_x else -x[idx], kind="stable")]
    best = -np.inf; front = []
    for i in order:
        if y[i] > best:
            front.append(int(i)); best = y[i]
    return sorted(front, key=lambda i: x[i])


def human_bytes(v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(v) < 1000:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.2g} {unit}"
        v /= 1000
    return f"{v:.2g} PB"


def fmt_value(mean: float, sd: float | None, kind: str) -> str:
    """Executive-table cell: mean ± sd in the metric's natural unit."""
    if mean is None or (isinstance(mean, float) and math.isnan(mean)):
        return "n/a"
    has_sd = sd is not None and not (isinstance(sd, float) and math.isnan(sd))
    if kind == "pct":
        return f"{100 * mean:.1f}%" + (f" ± {100 * sd:.1f}" if has_sd else "")
    if kind == "ratio":
        return f"{mean:.3g}×" + (f" ± {sd:.2g}" if has_sd else "")
    if kind == "usd":
        return f"${mean:.3g}" + (f" ± {sd:.2g}" if has_sd else "")
    if kind == "bytes":
        return human_bytes(mean) + (f" ± {human_bytes(sd)}" if has_sd else "")
    return f"{mean:.3g}" + (f" ± {sd:.2g}" if has_sd else "")


def _panel_title(ax, i: int, text: str) -> None:
    ax.set_title(f"({'abcdefghijkl'[i]}) {text}")


def _suptitle(fig, text: str) -> None:
    fig.suptitle(text, x=0.0, ha="left", fontsize=10, fontweight="semibold", color=INK)


# ---------------------------------------------------------------------------
# F1 — discovery vs organisation size
# ---------------------------------------------------------------------------

def fig_f1(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "scaling")
    if df is None or not has_cols(df, ["system", "seed", "n_workers", "metrics.recall_global"], "F1"):
        return False
    df = default_condition(df, "F1")
    panels = [(m, l, log) for m, l, log in [
        ("metrics.recall_global", "Global discovery recall", False), ("metrics.recall_cross_team", "Cross-team recall", False),
        ("metrics.runtime_s", "Runtime (s)", True), ("metrics.peak_rss_mb", "Peak RSS (MB)", True)] if m in df.columns]
    df = numeric(df, ["n_workers"] + [m for m, _, _ in panels])
    systems = order_systems(df["system"].unique())
    fig, axes = plt.subplots(1, len(panels), figsize=(3.1 * len(panels), 2.8), squeeze=False)
    for i, (ax, (m, label, log)) in enumerate(zip(axes[0], panels)):
        tbl = summarize_groups(df, m, ["system", "n_workers"])
        lines_by_system(ax, tbl, "n_workers", systems)
        ax.set_xscale("log"); ax.set_xlabel("Organisation size (workers)"); ax.set_ylabel(label)
        _panel_title(ax, i, label)
        if log:
            ax.set_yscale("log")
        else:
            ax.set_ylim(-0.02, 1.02)
    _suptitle(fig, "F1 — Discovery vs organisation size")
    fig.tight_layout()
    finish(fig, out, "F1_discovery_vs_org_size", seeds_note(df, fam), systems)
    return True


# ---------------------------------------------------------------------------
# F2 — useful information retained vs compression ratio
# ---------------------------------------------------------------------------

def fig_f2(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "compression")
    if df is None or not has_cols(df, ["system", "seed", "metrics.compression_ratio"], "F2"):
        return False
    ycol = first_col(df, "metrics.recall_global", "metrics.fidelity.executive.useful_fact_recall")
    if ycol is None:
        warn("F2: need metrics.recall_global or metrics.fidelity.executive.useful_fact_recall — skipped"); return False
    counts = df["system"].value_counts()
    focus = "B7_mycelic" if "B7_mycelic" in counts.index else counts.index[0]
    if len(counts) > 1:
        info(f"F2: {len(counts)} systems in compression.csv; plotting the sweep for {focus!r}")
    df = df[df["system"] == focus]
    sweep = [c for c in ("compression", "sketch_order", "cadence") if c in df.columns and df[c].nunique() > 1]
    df = default_condition(df, "F2", exclude=tuple(sweep))
    ycols = [ycol] + [c for c in ("metrics.recall_cross_team",) if c in df.columns and c != ycol]
    df = numeric(df, ["metrics.compression_ratio", "sketch_order", "cadence"] + ycols)
    facets = sorted(df["cadence"].dropna().unique()) if "cadence" in sweep else [None]
    group = [c for c in ("compression", "sketch_order") if c in df.columns]
    if not group:
        group = ["system"]
    schemes = [s for s in COMPRESSION_ORDER if s in set(df.get("compression", pd.Series(dtype=str)).dropna())]
    schemes += sorted(set(df["compression"].dropna()) - set(schemes)) if "compression" in df.columns else []
    scheme_color = {s: COND_PALETTE[i % len(COND_PALETTE)] for i, s in enumerate(schemes)}
    if len(schemes) > len(COND_PALETTE):
        warn(f"F2: {len(schemes)} compression schemes exceed the 8-hue palette; colours repeat — read labels")
    order_marker = {1: "^", 2: "s", 3: "o", 4: "D"}
    fig, axes = plt.subplots(len(ycols), len(facets), figsize=(3.6 * len(facets), 2.9 * len(ycols)), squeeze=False)
    k = 0
    for r, yc in enumerate(ycols):
        for c, cad in enumerate(facets):
            ax = axes[r][c]
            sub = df if cad is None else df[df["cadence"] == cad]
            tx = summarize_groups(sub, "metrics.compression_ratio", group)
            ty = summarize_groups(sub, yc, group)
            m = tx.merge(ty, on=group, suffixes=("_x", "_y"))
            m = m[(m["n_x"] > 0) & (m["n_y"] > 0)]
            for _, row in m.iterrows():
                color = scheme_color.get(row["compression"], INK2) if "compression" in group else COND_PALETTE[0]
                marker = order_marker.get(int(row["sketch_order"]), "o") if "sketch_order" in group else "o"
                x = max(float(row["mean_x"]), 1e-9)
                xerr = err_pair([x], [max(row["ci_low_x"], 1e-9) if not np.isnan(row["ci_low_x"]) else np.nan], [row["ci_high_x"]])
                yr = err_pair([row["mean_y"]], [row["ci_low_y"]], [row["ci_high_y"]])
                ax.errorbar(x, row["mean_y"], xerr=xerr, yerr=yr, fmt=marker, color=color, markersize=5.5,
                            markeredgecolor=SURFACE, markeredgewidth=0.5, capsize=2, elinewidth=0.8, zorder=3)
            if len(m) >= 2:
                idx = pareto_front(m["mean_x"].to_numpy(), m["mean_y"].to_numpy(), minimize_x=False)
                ax.plot(m["mean_x"].to_numpy()[idx], m["mean_y"].to_numpy()[idx], color=AXIS, lw=1.0, zorder=2)
            ax.set_xscale("log"); ax.set_ylim(-0.02, 1.02)
            ax.set_xlabel("Compression ratio (raw bytes / bytes promoted above team, log)")
            ax.set_ylabel("Useful information retained" if yc == ycol else "Cross-team recall")
            _panel_title(ax, k, ("useful information retained" if yc == ycol else "cross-team recall") +
                         (f" — cadence {cad:g}" if cad is not None else "")); k += 1
    _suptitle(fig, f"F2 — Useful information retained vs compression ratio ({label_of(focus)}; grey line = Pareto frontier)")
    fig.tight_layout()
    hs = [Patch(facecolor=scheme_color[s], edgecolor=SURFACE, label=s) for s in schemes]
    if hs:
        fig.legend(hs, [s for s in schemes], loc="upper center", bbox_to_anchor=(0.3, -0.02), ncol=min(len(hs), 3),
                   title="compression scheme", title_fontsize=7.5, frameon=False)
    if "sketch_order" in group:
        orders = sorted(int(o) for o in df["sketch_order"].dropna().unique())
        ho = [Line2D([0], [0], marker=order_marker.get(o, "o"), color=INK2, ls="", markersize=5.5) for o in orders]
        fig.legend(ho, [f"order ≤ {o}" for o in orders], loc="upper center", bbox_to_anchor=(0.78, -0.02),
                   ncol=len(ho), title="sketch order", title_fontsize=7.5, frameon=False)
    note = seeds_note(df, fam, "Each point = one (scheme, sketch-order) condition; error bars in both axes. ")
    y = -0.02 - (0.17 * max(1, math.ceil(len(hs) / 3)) + 0.25) / fig.get_size_inches()[1]
    fig.text(0.0, y, _wrap(note, 150), fontsize=6.6, color=MUTED, ha="left", va="top", transform=fig.transFigure)
    save(fig, out, "F2_information_vs_compression")
    return True


# ---------------------------------------------------------------------------
# F3 — poison propagation across layers
# ---------------------------------------------------------------------------

def fig_f3(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "poisoning")
    if df is None or not has_cols(df, ["system", "seed", "malicious_fraction", "metrics.poison.poison_promotion_rate"], "F3"):
        return False
    df = default_condition(df, "F3", exclude=("malicious_fraction",))
    layer_cols = [(l, f"metrics.poison.poison_by_layer.{l}") for l in LAYER_ORDER if f"metrics.poison.poison_by_layer.{l}" in df.columns]
    df = numeric(df, ["malicious_fraction", "metrics.poison.poison_promotion_rate"] + [c for _, c in layer_cols])
    systems = order_systems(df["system"].unique())
    hier = [s for s in systems if layer_cols and df.loc[df["system"] == s, [c for _, c in layer_cols]].notna().any().any()][:6]
    fracs = sorted(df["malicious_fraction"].dropna().unique())
    ncols = max(len(hier), 1)
    fig = plt.figure(figsize=(max(6.6, 2.3 * ncols), 5.4 if hier else 3.0))
    gs = fig.add_gridspec(2 if hier else 1, ncols, height_ratios=[1.05, 1] if hier else [1])
    k = 0
    if hier:
        xs = np.arange(len(fracs))
        ymax = 0.0
        top_axes = []
        for i, s in enumerate(hier):
            ax = fig.add_subplot(gs[0, i]); top_axes.append(ax)
            sub = df[df["system"] == s]
            bottom = np.zeros(len(fracs))
            for layer, col in layer_cols:
                t = summarize_groups(sub, col, ["malicious_fraction"]).set_index("malicious_fraction").reindex(fracs)
                vals = np.nan_to_num(t["mean"].to_numpy(dtype=float), nan=0.0)
                ax.bar(xs, vals, bottom=bottom, width=0.64, color=LAYER_COLORS[layer], edgecolor=SURFACE, linewidth=0.8, zorder=3)
                bottom = bottom + vals
            tot = sub.groupby(["malicious_fraction", "seed"])[[c for _, c in layer_cols]].sum(min_count=1).sum(axis=1, min_count=1).reset_index(name="total")
            tt = summarize_groups(tot, "total", ["malicious_fraction"]).set_index("malicious_fraction").reindex(fracs)
            ax.errorbar(xs, tt["mean"].to_numpy(dtype=float), yerr=yerr(tt), fmt="none", ecolor=INK2, elinewidth=0.8, capsize=2, zorder=4)
            ymax = max(ymax, float(np.nanmax(tt["ci_high"].fillna(tt["mean"]).to_numpy(dtype=float), initial=0.0)), float(bottom.max()))
            ax.set_xticks(xs); ax.set_xticklabels([f"{100 * f:g}%" for f in fracs])
            ax.set_xlabel("Malicious workers"); _panel_title(ax, k, SYSTEM_SHORT.get(s, s) + " — poison claims per layer"); k += 1
            if i == 0:
                ax.set_ylabel("Accepted poison claims (count, stacked by layer)")
        for ax in top_axes:
            ax.set_ylim(0, ymax * 1.12 if ymax > 0 else 1)
        lh = [Patch(facecolor=LAYER_COLORS[l], edgecolor=SURFACE, label=l) for l, _ in layer_cols]
        top_axes[0].legend(lh, [l for l, _ in layer_cols], loc="upper left", fontsize=7, title="layer", title_fontsize=7)
        ax = fig.add_subplot(gs[1, :])
    else:
        warn("F3: no metrics.poison.poison_by_layer.* columns — drawing the promotion-rate panel only")
        ax = fig.add_subplot(gs[0, :])
    tbl = summarize_groups(df, "metrics.poison.poison_promotion_rate", ["system", "malicious_fraction"])
    lines_by_system(ax, tbl, "malicious_fraction", systems)
    ax.set_xlabel("Malicious workers (fraction of workforce)"); ax.set_ylabel("Poison promotion rate at root")
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(xmax=1.0, decimals=0))
    ax.set_ylim(-0.02, max(1.02, float(np.nanmax(tbl["ci_high"].fillna(tbl["mean"]).to_numpy(dtype=float), initial=0.0)) * 1.05))
    _panel_title(ax, k, "share of root-accepted claims that are poison")
    _suptitle(fig, "F3 — Poison propagation across layers")
    fig.tight_layout()
    finish(fig, out, "F3_poison_propagation", seeds_note(df, fam), systems)
    return True


# ---------------------------------------------------------------------------
# F4 — privacy leakage vs centralized
# ---------------------------------------------------------------------------

def fig_f4(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "privacy")
    if df is None or not has_cols(df, ["system", "seed"], "F4"):
        return False
    panels = [(m, l) for m, l in [("metrics.raw_sensitive_leakage", "Canary tokens leaked (fraction)"),
                                  ("metrics.fraction_raw_exposed", "Raw bytes off-device / raw bytes"),
                                  ("metrics.reconstructability", "Re-identifiable promoted cells (n < k)")] if m in df.columns]
    if not panels:
        warn("F4: none of metrics.raw_sensitive_leakage / metrics.fraction_raw_exposed present — skipped"); return False
    conds = [c for c in ("quote_policy", "k_anonymity") if c in df.columns]
    df = default_condition(df, "F4", exclude=tuple(conds))
    df = numeric(df, [m for m, _ in panels])
    group = ["system"] + conds
    keys = df[group].drop_duplicates()
    keys["_o"] = keys["system"].map({s: i for i, s in enumerate(order_systems(keys["system"].unique()))})
    keys = keys.sort_values(["_o"] + conds, na_position="first").drop(columns="_o")
    labels = []
    for _, r in keys.iterrows():
        extra = [f"{c.replace('_', ' ')}={r[c]}" for c in conds if pd.notna(r[c])]
        labels.append(label_of(r["system"]) + (f" ({', '.join(extra)})" if extra else ""))
    colors = [style(s)["color"] for s in keys["system"]]
    fig, axes = plt.subplots(1, len(panels), figsize=(3.2 * len(panels) + 1.2, 0.3 * len(keys) + 1.4), sharey=True, squeeze=False)
    for i, (ax, (m, label)) in enumerate(zip(axes[0], panels)):
        t = summarize_groups(df, m, group).merge(keys, on=group, how="right")
        barh_by_row(ax, labels, t["mean"], t["ci_low"], t["ci_high"], colors, xlabel=label, unit_axis=True)
        _panel_title(ax, i, label)
    _suptitle(fig, "F4 — Privacy leakage: hierarchical DLP vs centralized raw shipping")
    fig.tight_layout()
    finish(fig, out, "F4_privacy_leakage", seeds_note(df, fam, "Lower is better. Centralized systems receive every raw record by design (DESIGN.md §1.3). "))
    return True


# ---------------------------------------------------------------------------
# F5 — cross-team emergent discovery rate
# ---------------------------------------------------------------------------

def fig_f5(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "aggregation", "headline")
    if df is None or not has_cols(df, ["system", "seed", "metrics.recall_cross_team"], "F5"):
        return False
    df = default_condition(df, "F5")
    panels = [(m, l) for m, l in [("metrics.recall_local", "Local findings recall"),
                                  ("metrics.recall_cross_team", "Cross-team findings recall"),
                                  ("metrics.recall_global", "Hidden global findings recall"),
                                  ("metrics.precision_strict", "Precision (strict)")] if m in df.columns]
    df = numeric(df, [m for m, _ in panels])
    systems = order_systems(df["system"].unique())
    fig, axes = plt.subplots(1, len(panels), figsize=(2.9 * len(panels) + 1.4, 0.3 * len(systems) + 1.5), sharey=True, squeeze=False)
    for i, (ax, (m, label)) in enumerate(zip(axes[0], panels)):
        t = summarize_groups(df, m, ["system"]).set_index("system").reindex(systems)
        barh_by_row(ax, [label_of(s) for s in systems], t["mean"], t["ci_low"], t["ci_high"], [style(s)["color"] for s in systems],
                    xlabel=label, unit_axis=True)
        _panel_title(ax, i, label)
    _suptitle(fig, "F5 — Cross-team emergent discovery: recall by finding kind (exact matches)")
    fig.tight_layout()
    finish(fig, out, "F5_cross_team_discovery", seeds_note(df, fam, "Cross-team/global findings need evidence pooled across ≥2 teams/departments (DESIGN.md §3.5). "))
    return True


# ---------------------------------------------------------------------------
# F6 — knowledge survival under failures
# ---------------------------------------------------------------------------

def fig_f6(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "failures")
    if df is None or not has_cols(df, ["system", "seed", "failure_kind", "failure_rate", "knowledge_survival"], "F6"):
        return False
    df = default_condition(df, "F6", exclude=("failure_kind", "failure_rate"))
    metrics = [(m, l) for m, l in [("knowledge_survival", "Knowledge survival"),
                                   ("useful_discovery_survival", "Useful-discovery survival"),
                                   ("lineage_survival", "Lineage survival")] if m in df.columns]
    df = numeric(df, ["failure_rate"] + [m for m, _ in metrics])
    if df["failure_rate"].max() <= 1.0:
        df["failure_rate"] = df["failure_rate"] * 100.0
    kinds = [k for k in FAILURE_ORDER if k in set(df["failure_kind"].dropna())]
    kinds += sorted(set(df["failure_kind"].dropna()) - set(kinds))
    systems = order_systems(df["system"].unique())
    fig, axes = plt.subplots(len(metrics), len(kinds), figsize=(2.4 * len(kinds) + 0.6, 2.4 * len(metrics) + 0.4),
                             sharey="row", squeeze=False)
    k = 0
    for r, (m, label) in enumerate(metrics):
        for c, kind in enumerate(kinds):
            ax = axes[r][c]
            sub = df[df["failure_kind"] == kind]
            tbl = summarize_groups(sub, m, ["system", "failure_rate"])
            lines_by_system(ax, tbl, "failure_rate", systems)
            ax.set_ylim(-0.02, 1.05); ax.set_xlabel("Failure rate (%)")
            if c == 0:
                ax.set_ylabel(label)
            _panel_title(ax, k, f"{kind.replace('_', ' ')}"); k += 1
    _suptitle(fig, "F6 — Knowledge survival under failures (fraction of no-failure discoveries still accepted at the root)")
    fig.tight_layout()
    finish(fig, out, "F6_knowledge_survival", seeds_note(df, fam), systems)
    return True


# ---------------------------------------------------------------------------
# F7 — accuracy vs communication cost
# ---------------------------------------------------------------------------

def _scatter_systems(ax, df: pd.DataFrame, xcol: str, ycol: str, systems: list[str], xlabel: str, ylabel: str) -> None:
    tx = summarize_groups(df, xcol, ["system"]).set_index("system")
    ty = summarize_groups(df, ycol, ["system"]).set_index("system")
    pos = tx["mean"][tx["mean"] > 0]
    floor = float(pos.min()) / 3.0 if len(pos) else 1.0
    for s in systems:
        if s not in tx.index or s not in ty.index or tx.loc[s, "n"] == 0 or ty.loc[s, "n"] == 0:
            continue
        st = style(s)
        xm = float(tx.loc[s, "mean"]); zero = not (xm > 0)
        x = floor if zero else xm
        xerr = np.zeros((2, 1)) if zero else err_pair([x], [max(tx.loc[s, "ci_low"], floor / 3)], [tx.loc[s, "ci_high"]])
        ax.errorbar(x, ty.loc[s, "mean"], xerr=xerr, yerr=err_pair([ty.loc[s, "mean"]], [ty.loc[s, "ci_low"]], [ty.loc[s, "ci_high"]]),
                    fmt=st["marker"], color=st["color"], markersize=6, markeredgecolor=SURFACE, markeredgewidth=0.5,
                    capsize=2, elinewidth=0.8, zorder=3)
        ax.annotate(SYSTEM_SHORT.get(s, s) + (" (=0)" if zero else ""), (x, ty.loc[s, "mean"]), xytext=(5, 4),
                    textcoords="offset points", fontsize=6.8, color=INK2)
    ax.set_xscale("log"); ax.set_xlabel(xlabel); ax.set_ylabel(ylabel); ax.set_ylim(-0.02, 1.02)
    ax.grid(True, axis="x")


def fig_f7(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "aggregation", "headline")
    if df is None or not has_cols(df, ["system", "seed", "metrics.bytes_transmitted", "metrics.recall_global"], "F7"):
        return False
    df = default_condition(df, "F7")
    xpanels = [(c, l) for c, l in [("metrics.bytes_transmitted", "Bytes transmitted upward (log)"),
                                   ("metrics.cost_usd_est", "Estimated cost (USD, log)"),
                                   ("metrics.tokens_to_cloud", "Tokens sent to cloud (log)")] if c in df.columns][:3]
    df = numeric(df, ["metrics.recall_global"] + [c for c, _ in xpanels])
    systems = order_systems(df["system"].unique())
    fig, axes = plt.subplots(1, len(xpanels), figsize=(3.6 * len(xpanels), 3.0), squeeze=False)
    for i, (ax, (c, l)) in enumerate(zip(axes[0], xpanels)):
        _scatter_systems(ax, df, c, "metrics.recall_global", systems, l, "Global discovery recall")
        _panel_title(ax, i, l.replace(" (log)", ""))
    _suptitle(fig, "F7 — Accuracy vs communication cost (up-and-left is better)")
    fig.tight_layout()
    finish(fig, out, "F7_accuracy_vs_cost", seeds_note(df, fam, "Points at the left edge marked (=0) transmit nothing. "), systems)
    return True


# ---------------------------------------------------------------------------
# F8 — edge-model Pareto frontier (synthetic capability sweep)
# ---------------------------------------------------------------------------

def fig_f8(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "models")
    if df is None or not has_cols(df, ["system", "seed", "profile", "metrics.recall_global"], "F8"):
        return False
    xpanels = [(c, l) for c, l in [("metrics.latency_ms_est", "Estimated latency per node (ms, log)"),
                                   ("metrics.energy_j_est", "Estimated energy (J, log)"),
                                   ("metrics.cost_usd_est", "Estimated cost (USD, log)")] if c in df.columns][:2]
    if not xpanels:
        warn("F8: need one of metrics.latency_ms_est / metrics.energy_j_est / metrics.cost_usd_est — skipped"); return False
    df = default_condition(df, "F8", exclude=("profile",))
    df = numeric(df, ["metrics.recall_global"] + [c for c, _ in xpanels])
    systems = order_systems(df["system"].unique())
    profiles = [p for p in PROFILE_ORDER if p in set(df["profile"].dropna())] + sorted(set(df["profile"].dropna()) - set(PROFILE_ORDER))
    fig, axes = plt.subplots(1, len(xpanels), figsize=(3.8 * len(xpanels), 3.1), squeeze=False)
    for i, (ax, (c, l)) in enumerate(zip(axes[0], xpanels)):
        tx = summarize_groups(df, c, ["system", "profile"]); ty = summarize_groups(df, "metrics.recall_global", ["system", "profile"])
        m = tx.merge(ty, on=["system", "profile"], suffixes=("_x", "_y"))
        m = m[(m["n_x"] > 0) & (m["n_y"] > 0)]
        pos = m["mean_x"][m["mean_x"] > 0]
        floor = float(pos.min()) / 3.0 if len(pos) else 1.0
        for s in systems:
            ms = m[m["system"] == s].copy()
            if ms.empty:
                continue
            st = style(s)
            ms["x"] = ms["mean_x"].where(ms["mean_x"] > 0, floor)
            idx = pareto_front(ms["x"].to_numpy(), ms["mean_y"].to_numpy(), minimize_x=True)
            if len(idx) >= 2:
                ax.plot(ms["x"].to_numpy()[idx], ms["mean_y"].to_numpy()[idx], color=st["color"], lw=1.0, ls=st["ls"], alpha=0.6, zorder=2)
            for _, row in ms.iterrows():
                zero = not (row["mean_x"] > 0)
                xerr = np.zeros((2, 1)) if zero else err_pair([row["x"]], [max(row["ci_low_x"], floor / 3) if not np.isnan(row["ci_low_x"]) else np.nan], [row["ci_high_x"]])
                ax.errorbar(row["x"], row["mean_y"], xerr=xerr, yerr=err_pair([row["mean_y"]], [row["ci_low_y"]], [row["ci_high_y"]]),
                            fmt=st["marker"], color=st["color"], markersize=6, markeredgecolor=SURFACE, markeredgewidth=0.5,
                            capsize=2, elinewidth=0.8, zorder=3)
                ax.annotate(str(row["profile"]).replace("sim-", "") + (" (=0)" if zero else ""), (row["x"], row["mean_y"]),
                            xytext=(5, 3), textcoords="offset points", fontsize=6.5, color=INK2)
        ax.set_xscale("log"); ax.set_xlabel(l); ax.set_ylabel("Global discovery recall"); ax.set_ylim(-0.02, 1.02)
        ax.grid(True, axis="x")
        _panel_title(ax, i, l.replace(" (log)", "") + " — synthetic sweep")
    _suptitle(fig, F8_TITLE)
    fig.tight_layout()
    finish(fig, out, "F8_edge_model_pareto", seeds_note(df, fam, F8_NOTE + f"Profiles: {', '.join(profiles)}. Lines = per-system Pareto frontier. "), systems)
    return True


# ---------------------------------------------------------------------------
# F9 — hierarchy-depth ablation
# ---------------------------------------------------------------------------

def fig_f9(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "hierarchy")
    if df is None or not has_cols(df, ["system", "seed", "layers", "metrics.recall_global"], "F9"):
        return False
    df = default_condition(df, "F9", exclude=("layers",))
    panels = [(m, l, log) for m, l, log in [("metrics.recall_global", "Global discovery recall", False),
                                            ("metrics.recall_cross_team", "Cross-team recall", False),
                                            ("metrics.precision_strict", "Precision (strict)", False),
                                            ("metrics.bytes_transmitted", "Bytes transmitted (log)", True)] if m in df.columns]
    df = numeric(df, ["layers"] + [m for m, _, _ in panels])
    systems = order_systems(df["system"].unique())
    layers = sorted(df["layers"].dropna().unique())
    fig, axes = plt.subplots(1, len(panels), figsize=(3.0 * len(panels), 2.8), squeeze=False)
    for i, (ax, (m, label, log)) in enumerate(zip(axes[0], panels)):
        tbl = summarize_groups(df, m, ["system", "layers"])
        lines_by_system(ax, tbl, "layers", systems)
        ax.set_xticks(layers); ax.set_xticklabels([f"{int(l)}" for l in layers]); ax.set_xlabel("Hierarchy depth (layers)")
        ax.set_ylabel(label); _panel_title(ax, i, label.replace(" (log)", ""))
        if log:
            ax.set_yscale("log")
        else:
            ax.set_ylim(-0.02, 1.02)
    _suptitle(fig, "F9 — Hierarchy-depth ablation (equal per-node budgets)")
    fig.tight_layout()
    finish(fig, out, "F9_hierarchy_depth", seeds_note(df, fam), systems)
    return True


# ---------------------------------------------------------------------------
# F10 — time-to-discovery distribution
# ---------------------------------------------------------------------------

def fig_f10(results: Path, out: Path) -> bool:
    df, fam = load_csv(results, "aggregation", "headline", "temporal")
    if df is None or not has_cols(df, ["system", "seed", "metrics.ttd_global_median"], "F10"):
        return False
    df = default_condition(df, "F10")
    panels = [(m, l) for m, l in [("metrics.ttd_global_median", "Hidden global findings"),
                                  ("metrics.ttd_cross_team_median", "Cross-team findings")] if m in df.columns]
    df = numeric(df, [m for m, _ in panels])
    systems = order_systems(df["system"].unique())
    rng = np.random.default_rng(0)
    fig, axes = plt.subplots(1, len(panels), figsize=(3.6 * len(panels) + 1.2, 0.32 * len(systems) + 1.5), sharey=True, squeeze=False)
    for i, (ax, (m, label)) in enumerate(zip(axes[0], panels)):
        for j, s in enumerate(systems):
            vals = df.loc[df["system"] == s, m].dropna().to_numpy(dtype=float)
            if vals.size == 0:
                ax.text(0.01, j, "n/a (nothing discovered)", va="center", fontsize=6.5, color=MUTED, transform=ax.get_yaxis_transform())
                continue
            st = style(s)
            ax.scatter(vals, j + rng.uniform(-0.18, 0.18, vals.size), s=16, color=st["color"], marker=st["marker"],
                       edgecolor=SURFACE, linewidth=0.5, zorder=3)
            q1, med, q3 = np.percentile(vals, [25, 50, 75])
            ax.plot([q1, q3], [j, j], color=INK2, lw=3.5, alpha=0.3, solid_capstyle="butt", zorder=2)
            ax.plot([med, med], [j - 0.3, j + 0.3], color=INK, lw=1.2, zorder=4)
        ax.set_yticks(range(len(systems))); ax.set_yticklabels([label_of(s) for s in systems]); ax.invert_yaxis()
        ax.set_xlabel("Time-to-discovery (rounds; per-seed median)"); ax.grid(False, axis="y"); ax.grid(True, axis="x")
        ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
        ax.set_xlim(left=0)
        _panel_title(ax, i, label)
    _suptitle(fig, "F10 — Time-to-discovery distribution across seeds")
    fig.tight_layout()
    finish(fig, out, "F10_time_to_discovery", seeds_note(df, fam, "Each point = one seed's median first-acceptance round at the executive; grey bar = IQR across seeds, tick = median. "))
    return True


# ---------------------------------------------------------------------------
# Investor comparison + headline table
# ---------------------------------------------------------------------------

def _exec_frame(results: Path, fig: str):
    df, fam = load_csv(results, "headline", "aggregation")
    if df is None or not has_cols(df, ["system", "seed"], fig):
        return None, None, []
    df = default_condition(df, fig)
    metrics = [m for m in EXEC_METRICS if m[0] in df.columns]
    if not metrics:
        warn(f"{fig}: none of the executive-table metrics present — skipped")
        return None, None, []
    return numeric(df, [m[0] for m in metrics]), fam, metrics


def fig_investor(results: Path, out: Path) -> bool:
    df, fam, metrics = _exec_frame(results, "investor_comparison")
    if df is None:
        return False
    systems = order_systems(df["system"].unique())
    ncols = min(4, len(metrics)); nrows = math.ceil(len(metrics) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(2.9 * ncols + 1.4, (0.27 * len(systems) + 1.3) * nrows), squeeze=False)
    for i, (col, label, better, kind) in enumerate(metrics):
        ax = axes[i // ncols][i % ncols]
        t = summarize_groups(df, col, ["system"]).set_index("system").reindex(systems)
        log = kind in ("ratio", "usd", "bytes")
        barh_by_row(ax, [label_of(s) if i % ncols == 0 else SYSTEM_SHORT.get(s, s) for s in systems], t["mean"], t["ci_low"], t["ci_high"],
                    [style(s)["color"] for s in systems], unit_axis=(kind == "pct"))
        if kind == "pct":
            ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(xmax=1.0, decimals=0))
        if log:
            pos = t["mean"][t["mean"] > 0]
            if len(pos):
                ax.set_xscale("log"); ax.set_xlim(left=float(pos.min()) / 10)
        arrow = "↑ higher is better" if better == "higher" else "↓ lower is better"
        _panel_title(ax, i, f"{label}  {arrow}")
    for j in range(len(metrics), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")
    _suptitle(fig, "Executive comparison — every system on the same worlds (mean, 95% CI across seeds)")
    fig.tight_layout()
    extra = ""
    if "malicious_fraction" in df.columns and df["malicious_fraction"].notna().any():
        extra = f"Malicious workers: {', '.join(f'{100 * v:g}%' for v in sorted(df['malicious_fraction'].dropna().unique()))}. "
    finish(fig, out, "investor_comparison", seeds_note(df, fam, extra))
    return True


def fig_headline_table(results: Path, out: Path) -> bool:
    df, fam, metrics = _exec_frame(results, "fig_headline_table")
    if df is None:
        return False
    systems = order_systems(df["system"].unique())
    cells, ncount = [], []
    for s in systems:
        sub = df[df["system"] == s]
        row = []
        for col, label, better, kind in metrics:
            t = summarize_groups(sub, col, ["system"])
            row.append(fmt_value(float(t["mean"].iloc[0]), float(t["sd"].iloc[0]), kind) if len(t) and t["n"].iloc[0] > 0 else "n/a")
        ncount.append(int(sub["seed"].nunique()))
        cells.append([label_of(s)] + row + [str(ncount[-1])])
    headers = ["System"] + [f"{label}\n({'↑' if better == 'higher' else '↓'} {'higher' if better == 'higher' else 'lower'} is better)" for _, label, better, _ in metrics] + ["Seeds\n(n)"]
    width = 1.35 * (len(headers)) + 1.0
    fig, ax = plt.subplots(figsize=(width, 0.36 * len(systems) + 1.6))
    ax.axis("off")
    tbl = ax.table(cellText=cells, colLabels=headers, loc="center", cellLoc="center", colLoc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(7.2); tbl.scale(1, 1.55)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor(GRID); cell.set_linewidth(0.6)
        if r == 0:
            cell.set_facecolor("#f0efec"); cell.set_text_props(weight="semibold", color=INK); cell.set_height(cell.get_height() * 1.6)
        else:
            sysname = systems[r - 1]
            cell.set_facecolor("#e9f1fb" if sysname == "B7_mycelic" else SURFACE)
            cell.set_text_props(color=INK, ha="left" if c == 0 else "center")
            if c == 0:
                cell.set_text_props(weight="semibold" if sysname.startswith("B7") else "normal")
                cell.PAD = 0.03
    ax.set_title("Headline executive table — mean ± SD across seeds (paired worlds; CIs and Holm-corrected tests in results/tables/)",
                 loc="left", fontsize=9, fontweight="semibold", color=INK)
    fig.tight_layout()
    finish(fig, out, "fig_headline_table", seeds_note(df, fam, "Poison propagation = share of root-accepted claims that are population-false and attack-borne. "))
    return True


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

FIGURES: list[tuple[str, Callable[[Path, Path], bool]]] = [
    ("F1_discovery_vs_org_size", fig_f1),
    ("F2_information_vs_compression", fig_f2),
    ("F3_poison_propagation", fig_f3),
    ("F4_privacy_leakage", fig_f4),
    ("F5_cross_team_discovery", fig_f5),
    ("F6_knowledge_survival", fig_f6),
    ("F7_accuracy_vs_cost", fig_f7),
    ("F8_edge_model_pareto", fig_f8),
    ("F9_hierarchy_depth", fig_f9),
    ("F10_time_to_discovery", fig_f10),
    ("investor_comparison", fig_investor),
    ("fig_headline_table", fig_headline_table),
]


def main(argv: list[str] | None = None) -> dict[str, bool]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", default="results", help="results root containing processed/*.csv")
    ap.add_argument("--out", default=None, help="output directory (default: <results>/figures)")
    ap.add_argument("--only", default=None, help="comma-separated figure name prefixes, e.g. F3,F8,investor")
    ap.add_argument("--strict", action="store_true", help="re-raise exceptions instead of skipping a figure")
    args = ap.parse_args(argv)
    results = Path(args.results)
    out = Path(args.out) if args.out else results / "figures"
    out.mkdir(parents=True, exist_ok=True)
    apply_style()
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    status: dict[str, bool] = {}
    for name, fn in FIGURES:
        if only and not any(name.lower().startswith(p.lower()) for p in only):
            continue
        try:
            ok = bool(fn(results, out))
        except Exception as e:  # graceful degradation: one bad CSV must not kill the rest
            warn(f"{name} failed: {e!r}")
            traceback.print_exc()
            if args.strict:
                raise
            ok = False
        status[name] = ok
        if not ok:
            warn(f"skipped {name}")
    n_ok = sum(status.values())
    info(f"{n_ok}/{len(status)} figures written to {out}")
    return status


if __name__ == "__main__":
    main()
