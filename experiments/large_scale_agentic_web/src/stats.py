"""Statistics: paired tests, effect sizes, bootstrap intervals, Holm correction.

All headline comparisons are *paired by seed*: the same world, the same workload
and the same failure draw are handed to every system, so seed-to-seed variation
in world difficulty cancels out.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
from scipy import stats as sps


@dataclass
class Comparison:
    metric: str
    system_a: str
    system_b: str
    n_pairs: int
    mean_a: float
    mean_b: float
    mean_diff: float
    ci_low: float
    ci_high: float
    cohens_dz: float
    t_stat: float
    p_paired_t: float
    p_wilcoxon: float
    p_holm: float = float("nan")
    significant_holm_05: bool = False


def bootstrap_ci(x: np.ndarray, n_boot: int = 10_000, alpha: float = 0.05,
                 seed: int = 12345) -> tuple[float, float]:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if x.size < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, x.size, (n_boot, x.size))
    means = x[idx].mean(axis=1)
    return (float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


def mean_ci(x: np.ndarray, alpha: float = 0.05) -> tuple[float, float, float, float]:
    """mean, sd, and a 95% t-interval for the mean."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = x.size
    if n == 0:
        return (float("nan"),) * 4
    m = float(x.mean())
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    if n < 2 or sd == 0:
        return m, sd, m, m
    h = sps.t.ppf(1 - alpha / 2, n - 1) * sd / np.sqrt(n)
    return m, sd, m - h, m + h


def paired_compare(a: np.ndarray, b: np.ndarray, metric: str,
                   name_a: str, name_b: str) -> Comparison:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    ok = ~(np.isnan(a) | np.isnan(b))
    a, b = a[ok], b[ok]
    d = a - b
    n = d.size
    if n < 2:
        return Comparison(metric, name_a, name_b, n, float(np.mean(a)) if n else float("nan"),
                          float(np.mean(b)) if n else float("nan"), float(np.mean(d)) if n else float("nan"),
                          float("nan"), float("nan"), float("nan"), float("nan"),
                          float("nan"), float("nan"))
    sd = d.std(ddof=1)
    dz = float(d.mean() / sd) if sd > 0 else float("inf") * np.sign(d.mean()) if d.mean() != 0 else 0.0
    if sd > 0:
        t, p_t = sps.ttest_rel(a, b)
    else:
        t, p_t = (float("inf") if d.mean() != 0 else 0.0), (0.0 if d.mean() != 0 else 1.0)
    try:
        p_w = float(sps.wilcoxon(a, b, zero_method="zsplit").pvalue)
    except ValueError:
        p_w = 1.0
    lo, hi = bootstrap_ci(d)
    return Comparison(metric, name_a, name_b, n, float(a.mean()), float(b.mean()),
                      float(d.mean()), lo, hi, dz, float(t), float(p_t), p_w)


def holm(comparisons: list[Comparison], alpha: float = 0.05,
         field: str = "p_paired_t") -> list[Comparison]:
    """Holm-Bonferroni step-down correction over a family of comparisons."""
    idx = sorted(range(len(comparisons)), key=lambda i: getattr(comparisons[i], field))
    m = len(idx)
    prev = 0.0
    for rank, i in enumerate(idx):
        p = getattr(comparisons[i], field)
        adj = min(1.0, max(prev, (m - rank) * p))
        prev = adj
        comparisons[i].p_holm = adj
        comparisons[i].significant_holm_05 = adj < alpha
    return comparisons


def loglog_fit(n: np.ndarray, y: np.ndarray) -> dict:
    """Empirical scaling trend: slope of log y vs log N (never an asymptotic claim)."""
    n = np.asarray(n, float)
    y = np.asarray(y, float)
    ok = (n > 0) & (y > 0) & ~np.isnan(y)
    n, y = n[ok], y[ok]
    if n.size < 2:
        return {"slope": float("nan"), "intercept": float("nan"), "r2": float("nan"),
                "n_points": int(n.size), "classification": "insufficient data"}
    ln, ly = np.log(n), np.log(y)
    slope, intercept, r, p, se = sps.linregress(ln, ly)
    # a logarithmic law fits log y ~ log(log N) poorly under a power fit; test it
    lg = np.log(np.log(n))
    r2_log = sps.linregress(lg, y).rvalue ** 2 if n.size >= 3 else float("nan")
    if slope < 0.15:
        cls = "approximately constant"
    elif slope < 0.45:
        cls = "sub-linear"
    elif slope < 0.85:
        cls = "sub-linear (near sqrt)"
    elif slope < 1.15:
        cls = "approximately linear"
    else:
        cls = "super-linear"
    return {"slope": float(slope), "intercept": float(intercept), "r2": float(r ** 2),
            "stderr": float(se), "p_value": float(p), "n_points": int(n.size),
            "r2_vs_log_n": float(r2_log), "classification": cls}
