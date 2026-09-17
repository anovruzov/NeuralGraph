"""Seed-paired statistics for system comparisons (DESIGN.md §11).

Every comparison between two systems is *paired by seed*: system A and system B
were run on the same generated world, so the per-seed difference removes the
world-to-world variance.  The reporting contract is

* paired bootstrap (10,000 resamples of the per-seed differences) 95% CI,
* paired t-test p-value,
* Wilcoxon signed-rank p-value,
* Cohen's d_z (mean difference / SD of the differences),
* Holm step-down correction within a family of comparisons,

and **never a p-value without an effect size** — every function that returns a
p-value returns the paired difference, its CI and d_z next to it.

`summarize` gives the unpaired per-system summary (mean, SD, t-based 95% CI, n)
that figures use for error bars.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sps

__all__ = [
    "summarize",
    "summarize_groups",
    "paired_bootstrap_ci",
    "paired_summary",
    "holm",
    "compare_systems",
]

_NAN = float("nan")


def _as_float_array(values: Iterable[Any]) -> np.ndarray:
    arr = np.asarray(list(values) if not isinstance(values, (np.ndarray, pd.Series)) else values, dtype=float)
    return arr.ravel()


# ---------------------------------------------------------------------------
# Unpaired summary (error bars)
# ---------------------------------------------------------------------------

def summarize(values: Iterable[Any], alpha: float = 0.05) -> dict[str, float]:
    """Mean, sample SD and t-based 95% CI of a sample (NaNs dropped).

    Returns ``dict(mean, sd, ci_low, ci_high, n)``.  With ``n == 1`` the SD and
    CI are NaN; with ``n == 0`` everything is NaN and ``n == 0``.
    """
    x = _as_float_array(values)
    x = x[~np.isnan(x)]
    n = int(x.size)
    if n == 0:
        return {"mean": _NAN, "sd": _NAN, "ci_low": _NAN, "ci_high": _NAN, "n": 0}
    mean = float(x.mean())
    if n == 1:
        return {"mean": mean, "sd": _NAN, "ci_low": _NAN, "ci_high": _NAN, "n": 1}
    sd = float(x.std(ddof=1))
    half = float(sps.t.ppf(1 - alpha / 2, n - 1) * sd / math.sqrt(n))
    return {"mean": mean, "sd": sd, "ci_low": mean - half, "ci_high": mean + half, "n": n}


def summarize_groups(df: pd.DataFrame, metric: str, by: Sequence[str] | str, alpha: float = 0.05) -> pd.DataFrame:
    """`summarize` applied per group: one row per unique combination of ``by``.

    The returned frame has the ``by`` columns followed by ``mean, sd, ci_low,
    ci_high, n``.  Groups whose metric is entirely NaN are kept with ``n == 0``.
    """
    by = [by] if isinstance(by, str) else list(by)
    missing = [c for c in by + [metric] if c not in df.columns]
    if missing:
        raise KeyError(f"summarize_groups: missing columns {missing}")
    rows = []
    for key, sub in df.groupby(by, sort=True, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        s = summarize(pd.to_numeric(sub[metric], errors="coerce").to_numpy(), alpha)
        rows.append({**dict(zip(by, key)), **s})
    cols = by + ["mean", "sd", "ci_low", "ci_high", "n"]
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Paired comparison
# ---------------------------------------------------------------------------

def paired_bootstrap_ci(d: np.ndarray, n_boot: int = 10000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean of paired differences ``d``.

    Resamples the differences with replacement ``n_boot`` times (vectorised,
    chunked so memory stays bounded for large ``n``).
    """
    d = _as_float_array(d)
    n = d.size
    if n == 0:
        return _NAN, _NAN
    if n == 1:
        return float(d[0]), float(d[0])
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    chunk = max(1, int(2_000_000 // n))
    for start in range(0, n_boot, chunk):
        stop = min(n_boot, start + chunk)
        idx = rng.integers(0, n, size=(stop - start, n))
        means[start:stop] = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def paired_summary(a: Iterable[Any], b: Iterable[Any], n_boot: int = 10000, seed: int = 0, alpha: float = 0.05) -> dict[str, float]:
    """Paired comparison of ``a`` against ``b`` (same length, matched by position).

    ``diff = mean(a - b)`` — positive means ``a`` is larger.  Pairs with a NaN on
    either side are dropped.  Returns::

        dict(mean_a, mean_b, diff, ci_low, ci_high, t_p, wilcoxon_p, cohen_dz, n)

    * ``ci_low/ci_high``: paired percentile bootstrap of ``diff`` (``n_boot`` resamples, ``seed``).
    * ``t_p``: two-sided paired t-test.  Zero-variance differences give 1.0 when the
      difference is exactly zero and 0.0 otherwise (a degenerate but honest answer).
    * ``wilcoxon_p``: two-sided Wilcoxon signed-rank test (zero differences dropped,
      1.0 if every pair is tied).
    * ``cohen_dz``: ``diff / sd(a - b)`` (ddof=1); 0.0 for identical samples, ±inf
      for a constant non-zero shift, NaN when ``n < 2``.
    """
    a = _as_float_array(a)
    b = _as_float_array(b)
    if a.size != b.size:
        raise ValueError(f"paired_summary: length mismatch {a.size} vs {b.size}")
    keep = ~(np.isnan(a) | np.isnan(b))
    a, b = a[keep], b[keep]
    n = int(a.size)
    out: dict[str, float] = {"mean_a": _NAN, "mean_b": _NAN, "diff": _NAN, "ci_low": _NAN, "ci_high": _NAN,
                             "t_p": _NAN, "wilcoxon_p": _NAN, "cohen_dz": _NAN, "n": n}
    if n == 0:
        return out
    d = a - b
    diff = float(d.mean())
    out.update({"mean_a": float(a.mean()), "mean_b": float(b.mean()), "diff": diff})
    out["ci_low"], out["ci_high"] = paired_bootstrap_ci(d, n_boot=n_boot, seed=seed, alpha=alpha)
    if n < 2:
        return out
    sd_d = float(d.std(ddof=1))
    # A constant shift (all differences equal up to floating-point cancellation) has no
    # sampling variance: report the degenerate but honest answer instead of a 1e15 d_z.
    if sd_d <= 1e-9 * max(1.0, abs(diff)):
        if abs(diff) <= 1e-12:
            out.update({"t_p": 1.0, "wilcoxon_p": 1.0, "cohen_dz": 0.0})
        else:
            with np.errstate(all="ignore"):
                out["wilcoxon_p"] = float(sps.wilcoxon(np.full(n, diff)).pvalue)
            out.update({"t_p": 0.0, "cohen_dz": math.copysign(math.inf, diff)})
        return out
    out["t_p"] = float(sps.ttest_rel(a, b).pvalue)
    if np.all(d == 0):
        out["wilcoxon_p"] = 1.0
    else:
        try:
            out["wilcoxon_p"] = float(sps.wilcoxon(d, zero_method="wilcox").pvalue)
        except ValueError:
            out["wilcoxon_p"] = _NAN
    out["cohen_dz"] = diff / sd_d
    return out


def holm(pvals: Iterable[Any]) -> np.ndarray:
    """Holm step-down adjusted p-values (family-wise error control).

    Returns an array aligned with the input.  NaN entries are ignored (they do not
    count towards the family size and stay NaN).  Adjusted values are monotone in
    the raw ordering and never smaller than the raw p-value.
    """
    p = _as_float_array(pvals)
    out = np.full(p.shape, _NAN, dtype=float)
    valid = ~np.isnan(p)
    m = int(valid.sum())
    if m == 0:
        return out
    pv = p[valid]
    order = np.argsort(pv, kind="stable")
    ranked = pv[order] * (m - np.arange(m))
    adj = np.minimum(np.maximum.accumulate(ranked), 1.0)
    adj_unsorted = np.empty(m, dtype=float)
    adj_unsorted[order] = adj
    out[valid] = np.maximum(adj_unsorted, pv)
    return out


def compare_systems(df: pd.DataFrame, metric: str, baseline_system: str, systems: Sequence[str] | None = None,
                    by: Sequence[str] | str = ("seed",), n_boot: int = 10000, seed: int = 0,
                    system_col: str = "system") -> pd.DataFrame:
    """One row per system: its summary and its seed-paired comparison against ``baseline_system``.

    Pairing is an inner join on the ``by`` key(s) (default: ``seed``).  If a
    system has several rows for one key (e.g. several conditions pooled), they
    are averaged first — callers should filter to a single condition before
    comparing.  ``mean/sd/ci_low/ci_high/n`` describe every available row of the
    system; ``n_pairs`` and everything after it describe the paired subset.

    Columns: ``system, n, mean, sd, ci_low, ci_high, n_pairs, diff, diff_ci_low,
    diff_ci_high, t_p, holm_p, wilcoxon_p, wilcoxon_holm_p, cohen_dz``.

    ``holm_p`` corrects ``t_p`` within this family (all non-baseline systems of the
    call); ``wilcoxon_holm_p`` does the same for the Wilcoxon p-values.  The
    baseline row has ``diff = 0`` and NaN p-values, and is not part of the family.
    """
    by = [by] if isinstance(by, str) else list(by)
    for c in by + [metric, system_col]:
        if c not in df.columns:
            raise KeyError(f"compare_systems: column {c!r} not in frame")
    work = df[[system_col, *by, metric]].copy()
    work[metric] = pd.to_numeric(work[metric], errors="coerce")
    present = list(dict.fromkeys(work[system_col].tolist()))
    if systems is None:
        systems = [baseline_system] + [s for s in present if s != baseline_system] if baseline_system in present else present
    systems = list(systems)
    per_key = {s: work[work[system_col] == s].groupby(by, dropna=False)[metric].mean() for s in systems}
    if baseline_system in work[system_col].values and baseline_system not in per_key:
        per_key[baseline_system] = work[work[system_col] == baseline_system].groupby(by, dropna=False)[metric].mean()
    base = per_key.get(baseline_system)
    rows: list[dict[str, Any]] = []
    for s in systems:
        vals = per_key[s]
        summ = summarize(vals.to_numpy())
        row: dict[str, Any] = {"system": s, "n": summ["n"], "mean": summ["mean"], "sd": summ["sd"],
                               "ci_low": summ["ci_low"], "ci_high": summ["ci_high"],
                               "n_pairs": 0, "diff": _NAN, "diff_ci_low": _NAN, "diff_ci_high": _NAN,
                               "t_p": _NAN, "holm_p": _NAN, "wilcoxon_p": _NAN, "wilcoxon_holm_p": _NAN, "cohen_dz": _NAN}
        if s == baseline_system:
            row.update({"n_pairs": summ["n"], "diff": 0.0, "diff_ci_low": 0.0, "diff_ci_high": 0.0})
        elif base is not None:
            joined = pd.concat([vals.rename("a"), base.rename("b")], axis=1, join="inner").dropna()
            ps = paired_summary(joined["a"].to_numpy(), joined["b"].to_numpy(), n_boot=n_boot, seed=seed)
            row.update({"n_pairs": ps["n"], "diff": ps["diff"], "diff_ci_low": ps["ci_low"], "diff_ci_high": ps["ci_high"],
                        "t_p": ps["t_p"], "wilcoxon_p": ps["wilcoxon_p"], "cohen_dz": ps["cohen_dz"]})
        rows.append(row)
    out = pd.DataFrame(rows)
    fam = out["system"] != baseline_system
    if fam.any():
        out.loc[fam, "holm_p"] = holm(out.loc[fam, "t_p"].to_numpy())
        out.loc[fam, "wilcoxon_holm_p"] = holm(out.loc[fam, "wilcoxon_p"].to_numpy())
    return out
