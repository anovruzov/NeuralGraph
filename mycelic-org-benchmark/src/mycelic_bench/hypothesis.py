"""Shared hypothesis search (honesty rule 1.2: every system uses this).

For each cell C (order k) and label l the *interaction test* compares the
label rate inside C with the rate in the most elevated order-(k-1) sub-cell
outside C.  Order-1 cells are compared with their complement.  The exact
one-sided hypergeometric test is used (equivalent to Fisher's exact test on
the 2x2 table), vectorised with a cheap z-score screen first.  Benjamini-
Hochberg controls the false discovery rate within one search call.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import hypergeom

from .sketch import COL_DD, COL_DR, COL_DT, COL_DW, COL_K0, COL_N, Sketch
from .vocab import N_LABELS, cell_index


@dataclass
class Candidates:
    cell: np.ndarray        # int64 [m]
    label: np.ndarray       # int64 [m]
    sign: np.ndarray        # +1 / -1
    n: np.ndarray
    k: np.ndarray
    rate: np.ndarray
    baseline: np.ndarray    # outside rate of the best sub-marginal
    effect: np.ndarray      # rate - baseline (signed)
    p: np.ndarray
    q: np.ndarray
    n_out: np.ndarray
    dw: np.ndarray
    dt: np.ndarray
    dd: np.ndarray
    dr: np.ndarray
    n_tested: int = 0

    def __len__(self) -> int:
        return len(self.cell)

    def subset(self, mask: np.ndarray) -> "Candidates":
        return Candidates(*(getattr(self, f)[mask] for f in (
            "cell", "label", "sign", "n", "k", "rate", "baseline", "effect", "p", "q", "n_out", "dw", "dt", "dd", "dr")),
            n_tested=self.n_tested)


def _empty() -> Candidates:
    z = np.zeros(0, dtype=np.int64)
    f = np.zeros(0, dtype=float)
    return Candidates(z, z, z, z, z, f, f, f, f, f, z, z, z, z, z, 0)


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    m = len(p)
    if m == 0:
        return p
    order = np.argsort(p)
    ranked = p[order] * m / (np.arange(m) + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return out


def outside_counts(sketch: Sketch, ids: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every cell id return per-label outside counts (n_out [m, L],
    k_out [m, L]) of the most-elevated order-(k-1) sub-marginal outside the
    cell, and ok [m] (False when no valid comparison exists).  Order-1 cells
    are compared with their complement."""
    ci = cell_index()
    m = len(ids)
    n_out = np.zeros((m, N_LABELS), dtype=np.int64)
    k_out = np.zeros((m, N_LABELS), dtype=np.int64)
    ok = np.zeros(m, dtype=bool)
    if m == 0:
        return n_out, k_out, ok
    cnt = sketch.lookup(ids)
    n_c = cnt[:, COL_N]
    k_c = cnt[:, COL_K0:COL_K0 + N_LABELS]
    orders = ci.order_of(ids)
    first_attr_ids = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
    tot = sketch.lookup(first_attr_ids).sum(axis=0)
    tot_n, tot_k = int(tot[COL_N]), tot[COL_K0:COL_K0 + N_LABELS]
    # Outside evidence is only usable when it is a consistent 2x2 table: 0 <= k_out <= n_out.  Pooled sketches
    # can be transiently inconsistent (a question answer carries the exact current count of an order-4 cell
    # while its order-3 sub-cells at the same node lag behind through delta promotion / min_n suppression);
    # such a sub-cell is skipped for that label rather than yielding a rate > 1 and a p-value of 1e-300.
    for order in np.unique(orders):
        sel = np.flatnonzero(orders == order)
        if order == 1:
            no = (tot_n - n_c[sel])[:, None]
            ko = tot_k[None, :] - k_c[sel]
            good = (no > 0) & (ko >= 0) & (ko <= no)
            n_out[sel] = np.where(good, no, 0)
            k_out[sel] = np.where(good, ko, 0)
            ok[sel] = good.any(axis=1)
            continue
        subs = ci.sub_ids(ids[sel], int(order))            # [s, order]
        sub_cnt = sketch.lookup(subs.ravel()).reshape(len(sel), int(order), -1)
        so_n = sub_cnt[:, :, COL_N] - n_c[sel][:, None]  # outside n per sub-cell  [s, order]
        so_k = sub_cnt[:, :, COL_K0:COL_K0 + N_LABELS] - k_c[sel][:, None, :]   # [s, order, L]
        valid = (so_n > 0)[:, :, None] & (so_k >= 0) & (so_k <= so_n[:, :, None])   # [s, order, L]
        rate = np.where(valid, so_k / np.maximum(so_n, 1)[:, :, None], -1.0)
        best = rate.argmax(axis=1)                            # [s, L]
        s_idx = np.arange(len(sel))[:, None]
        lab = np.arange(N_LABELS)[None, :]
        good = valid[s_idx, best, lab]                        # [s, L]: the chosen sub-cell is consistent
        n_out[sel] = np.where(good, so_n[s_idx, best], 0)
        k_out[sel] = np.where(good, so_k[s_idx, best, lab], 0)
        ok[sel] = good.any(axis=1)
    n_out = np.maximum(n_out, 0)
    k_out = np.maximum(k_out, 0)
    return n_out, k_out, ok


def search(
    sketch: Sketch,
    n_min: int = 8,
    effect_min: float = 0.08,
    fdr_q: float = 0.05,
    max_order: int = 3,
    restrict_ids: np.ndarray | None = None,
    z_screen: float = 1.5,
    two_sided: bool = True,
    min_order: int = 1,
) -> Candidates:
    """Run the interaction test on every eligible cell of the sketch."""
    ci = cell_index()
    if len(sketch.ids) == 0:
        return _empty()
    orders = ci.order_of(sketch.ids)
    elig = (sketch.counts[:, COL_N] >= n_min) & (orders <= max_order) & (orders >= min_order)
    if restrict_ids is not None:
        elig &= np.isin(sketch.ids, restrict_ids)
    ids = sketch.ids[elig]
    if len(ids) == 0:
        return _empty()
    cnt = sketch.counts[elig]
    n_c = cnt[:, COL_N]
    k_c = cnt[:, COL_K0:COL_K0 + N_LABELS]
    # outside counts (label specific)
    n_out_lab, k_out, ok = outside_counts(sketch, ids)
    valid = ok & (n_out_lab.max(axis=1) > 0)
    if not valid.any():
        return _empty()
    ids, cnt, n_c, k_c, k_out, n_out_lab = ids[valid], cnt[valid], n_c[valid], k_c[valid], k_out[valid], n_out_lab[valid]
    m = len(ids)
    rate_c = k_c / n_c[:, None]
    rate_o = k_out / np.maximum(n_out_lab, 1)
    diff = rate_c - rate_o
    pbar = (k_c + k_out) / np.maximum(n_c[:, None] + n_out_lab, 1)
    se = np.sqrt(np.maximum(pbar * (1 - pbar), 1e-9) * (1.0 / n_c[:, None] + 1.0 / np.maximum(n_out_lab, 1)))
    z = diff / se
    # screen
    cand = np.abs(z) >= z_screen if two_sided else z >= z_screen
    cand &= n_out_lab > 0
    n_tested = int(m * N_LABELS)
    if not cand.any():
        return _empty()
    ri, li = np.nonzero(cand)
    M = n_c[ri] + n_out_lab[ri, li]          # population
    K = k_c[ri, li] + k_out[ri, li]         # successes in population
    N = n_c[ri]                              # draws
    kk = k_c[ri, li]
    sign = np.where(diff[ri, li] >= 0, 1, -1)
    p = exact_or_normal_p(kk, M, K, N, sign)
    p_up = p
    if not two_sided:
        p = p_up
        sign = np.ones_like(sign)
    p = np.clip(p, 1e-300, 1.0)
    # BH over the full tested family: screened-out hypotheses have p >= ~0.067 (z<1.5),
    # we account for them by scaling with n_tested (conservative: treat them as p=1 in the ranking).
    q = np.minimum(bh_qvalues(p) * (n_tested / max(len(p), 1)), 1.0)
    eff = diff[ri, li]
    keep = (q < fdr_q) & (np.abs(eff) >= effect_min)
    if not keep.any():
        return _empty()
    ri, li, sign, p, q, eff = ri[keep], li[keep], sign[keep], p[keep], q[keep], eff[keep]
    return Candidates(
        cell=ids[ri], label=li.astype(np.int64), sign=sign.astype(np.int64), n=n_c[ri], k=k_c[ri, li],
        rate=rate_c[ri, li], baseline=rate_o[ri, li], effect=eff, p=p, q=q, n_out=n_out_lab[ri, li],
        dw=cnt[ri, COL_DW], dt=cnt[ri, COL_DT], dd=cnt[ri, COL_DD], dr=cnt[ri, COL_DR], n_tested=n_tested,
    )


def exact_or_normal_p(kk: np.ndarray, M: np.ndarray, K: np.ndarray, N: np.ndarray, sign: np.ndarray) -> np.ndarray:
    """One-sided p-value of the 2x2 table: exact hypergeometric when any
    expected count is small (< 15), otherwise the normal approximation with
    continuity correction (indistinguishable at that size, 20x faster)."""
    from scipy.stats import norm
    M = np.maximum(M, 1)
    mean = N * K / M
    var = N * (K / M) * (1 - K / M) * (M - N) / np.maximum(M - 1, 1)
    small = (mean < 15) | ((N - mean) < 15) | (var <= 0)
    p = np.empty(len(kk), dtype=float)
    # cheap normal-approximation screen: the exact tail is only needed where it could reach significance
    with np.errstate(divide="ignore", invalid="ignore"):
        z0 = (kk - mean - 0.5 * sign) / np.sqrt(np.maximum(var, 1e-9))
        p0 = np.where(sign > 0, norm.sf(z0), norm.cdf(z0))
    p[:] = np.clip(p0, 1e-300, 1.0)
    small &= (p0 < 0.02) | (var <= 0)
    if small.any():
        idx = np.flatnonzero(small)
        p_up = hypergeom.sf(kk[idx] - 1, M[idx], K[idx], N[idx])
        p_dn = hypergeom.cdf(kk[idx], M[idx], K[idx], N[idx])
        p[idx] = np.where(sign[idx] > 0, p_up, p_dn)
    big = ~small
    if big.any():
        idx = np.flatnonzero(big)
        z = (kk[idx] - mean[idx] - 0.5 * sign[idx]) / np.sqrt(var[idx])
        p[idx] = np.where(sign[idx] > 0, norm.sf(z), norm.cdf(z))
    return np.clip(p, 1e-300, 1.0)


def rate_test(n_c: np.ndarray, k_c: np.ndarray, n_o: np.ndarray, k_o: np.ndarray, sign: np.ndarray) -> np.ndarray:
    """One-sided p-values for specific (cell vs outside) comparisons."""
    M = n_c + n_o
    K = k_c + k_o
    return exact_or_normal_p(np.asarray(k_c), np.asarray(M), np.asarray(K), np.asarray(n_c), np.asarray(sign))


def null_evidence(n: np.ndarray, k: np.ndarray, baseline: np.ndarray, effect_min: float, conf: float = 0.99) -> np.ndarray:
    """True where the data is powerful enough to say the rate is NOT elevated by
    effect_min over baseline: upper Clopper-Pearson-style bound (normal approx)
    of the rate below baseline + effect_min/2."""
    from scipy.stats import norm
    z = norm.ppf(conf)
    rate = k / np.maximum(n, 1)
    upper = rate + z * np.sqrt(np.maximum(rate * (1 - rate), 0.01) / np.maximum(n, 1))
    return (n > 0) & (upper < baseline + effect_min / 2)
