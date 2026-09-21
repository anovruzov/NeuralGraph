"""Aggregation, uncertainty and significance for the raw JSONL rows.

Rules followed here:
  * raw per-run metrics are never overwritten; everything is recomputed from
    the JSONL,
  * every aggregate carries a bootstrap CI,
  * every architecture comparison is PAIRED on (scale, seed) - the seed
    controls both the org and the corpus, so unpaired tests would be dominated
    by between-world variance,
  * with 5 seeds an exact sign test is reported rather than a t-test, because
    n=5 does not support a normality assumption.
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .runner import ART


def load(fname: str) -> List[Dict]:
    p = os.path.join(ART, fname)
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def boot_ci(x: Sequence[float], n: int = 4000, alpha: float = 0.05,
            seed: int = 0) -> Tuple[float, float, float]:
    a = np.asarray(list(x), dtype=float)
    if len(a) == 0:
        return (0.0, 0.0, 0.0)
    if len(a) == 1:
        return (float(a[0]), float(a[0]), float(a[0]))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    means = a[idx].mean(axis=1)
    return (float(a.mean()), float(np.percentile(means, 100 * alpha / 2)),
            float(np.percentile(means, 100 * (1 - alpha / 2))))


def sign_test(diffs: Sequence[float]) -> Tuple[int, int, float]:
    """Exact two-sided sign test. Returns (n_pos, n_total_nonzero, p)."""
    d = [x for x in diffs if abs(x) > 1e-12]
    n = len(d)
    if n == 0:
        return (0, 0, 1.0)
    k = sum(1 for x in d if x > 0)
    # two-sided exact binomial
    from math import comb
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1))
    p = min(1.0, 2.0 * tail / (2 ** n))
    return (k, n, p)


def paired(rows: List[Dict], a: str, b: str, metric: str,
           group: str = "arch", key=("scale", "seed")) -> Dict[str, object]:
    ka = {tuple(r[k] for k in key): r[metric] for r in rows if r[group] == a}
    kb = {tuple(r[k] for k in key): r[metric] for r in rows if r[group] == b}
    common = sorted(set(ka) & set(kb))
    if not common:
        return {"n": 0}
    da = np.array([ka[c] for c in common], dtype=float)
    db = np.array([kb[c] for c in common], dtype=float)
    diff = da - db
    m, lo, hi = boot_ci(diff)
    k, n, p = sign_test(diff.tolist())
    return {"n": len(common), "mean_a": float(da.mean()),
            "mean_b": float(db.mean()), "mean_diff": m,
            "ci_lo": lo, "ci_hi": hi, "wins": k, "n_nonzero": n,
            "sign_p": p}


def agg(rows: List[Dict], metrics: Sequence[str], by: Sequence[str] = ("arch",),
        seed_key: str = "seed") -> List[Dict]:
    keys = sorted({tuple(r[b] for b in by) for r in rows})
    out = []
    for k in keys:
        sub = [r for r in rows if tuple(r[b] for b in by) == k]
        d: Dict[str, object] = {b: v for b, v in zip(by, k)}
        d["n_runs"] = len(sub)
        for m in metrics:
            vals = [r[m] for r in sub if m in r]
            mean, lo, hi = boot_ci(vals)
            d[m] = mean
            d[m + "_lo"] = lo
            d[m + "_hi"] = hi
        out.append(d)
    return out


def md_table(rows: List[Dict], cols: Sequence[Tuple[str, str, int]],
             sort_by: Optional[str] = None, reverse: bool = True,
             ci_for: Sequence[str] = ()) -> str:
    """cols = [(key, header, decimals)]"""
    rs = list(rows)
    if sort_by:
        rs.sort(key=lambda r: r.get(sort_by, 0), reverse=reverse)
    head = "| " + " | ".join(h for _, h, _ in cols) + " |"
    sep = "|" + "|".join("---" if i == 0 else "---:"
                         for i in range(len(cols))) + "|"
    lines = [head, sep]
    for r in rs:
        cells = []
        for k, _, dec in cols:
            v = r.get(k, "")
            if isinstance(v, float):
                s = f"{v:.{dec}f}" if abs(v) < 1e5 else f"{v:.2e}"
                if k in ci_for and (k + "_lo") in r:
                    s += f" <sub>[{r[k+'_lo']:.{dec}f}, {r[k+'_hi']:.{dec}f}]</sub>"
            else:
                s = str(v)
            cells.append(s)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


HEADLINE_COLS = [
    ("arch", "architecture", 0),
    ("average_precision", "AP", 4),
    ("found_anywhere_in_register", "found", 3),
    ("recall_at_100", "R@100", 3),
    ("rare_signal_recall", "rare R", 3),
    ("false_discovery_rate", "FDR", 3),
    ("decoy_acceptance_all", "decoy acc", 3),
    ("independent_evidence_accuracy", "indep acc", 3),
    ("lineage_accuracy", "lineage", 3),
    ("contradiction_f1", "contra F1", 3),
    ("information_loss", "info loss", 3),
    ("compute_units", "compute", 2),
    ("tokens_total", "tokens", 2),
    ("wall_seconds", "wall s", 1),
    ("privacy_exposure_fraction", "privacy", 4),
]


def summary(fname: str, by=("arch",), metrics=None) -> str:
    rows = load(fname)
    if not rows:
        return f"(no rows in {fname})"
    metrics = metrics or [c[0] for c in HEADLINE_COLS[1:]]
    a = agg(rows, metrics, by=by)
    cols = [(by[0], by[0], 0)] + [(m, m, 4) for m in metrics]
    return md_table(a, cols, sort_by="average_precision")


if __name__ == "__main__":
    import sys
    for f in sys.argv[1:]:
        print(f"\n### {f}\n")
        print(summary(f))
