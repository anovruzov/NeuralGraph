"""The few statistics the experiment harnesses report, written out so a reviewer can check each formula.

Pure functions; randomness only through ``random.Random(seed)`` with an explicit seed, so every figure can be
recomputed bit for bit from the run file.

* :func:`percentile` uses linear interpolation between closest ranks, ``rank = (n - 1) * p / 100`` (numpy's
  default ``'linear'`` method), so p50 of ``[1, 2, 3, 4]`` is 2.5.
* :func:`wilson` is the Wilson score interval in closed form (95% by default), clamped to ``[0, 1]``.
* :func:`stratified_share` ``(strata)``: each stratum is ``(weight, k, n, N)``, its population weight (the weights
  sum to 1) and ``k`` hits among ``n`` units drawn by simple random sampling without replacement from its ``N``.
  ``share = sum w_h p_h`` with ``p_h = k_h / n_h``; ``variance = sum w_h^2 (1 - n_h / N_h) s_h^2 / n_h`` with
  ``s_h^2 = p_h (1 - p_h) n_h / (n_h - 1)`` (0 when ``n_h = 1``). The 95% interval is Wilson's at the effective
  sample size ``n_eff = share (1 - share) / variance`` (Kish's design effect), capped at the total sample size ``n``
  and equal to it when the variance or ``share (1 - share)`` is 0, so it is never narrower than a simple random
  sample of ``n`` with the same share.
* :func:`sign_test` is the exact two-sided sign test on paired differences with zeros dropped:
  ``p = min(1, 2 * sum_{i <= min(n+, n-)} C(n, i) / 2^n)``, summed in integers before the one final division.
* :func:`paired_bootstrap` is a percentile bootstrap of the mean paired difference.
* :func:`f1_from_counts` is ``2TP / (2TP + FP + FN)``, None when the denominator is 0 (nothing predicted, nothing
  to find), never 1.0 by convention.
* :func:`bootstrap_f1` resamples records (each a ``(tp, fp, fn)`` triple) with replacement and recomputes the
  pooled F1; resamples whose F1 is undefined are counted in ``undefined`` and left out of the percentiles.
* :func:`paired_bootstrap_f1` ``(a, b)``: two sides' ``(tp, fp, fn)`` triples for the same records (paired by
  index). ``diff`` is ``F1(sum a) - F1(sum b)``, the difference of the pooled (micro) F1s; each of ``B`` replicates
  draws ``n`` record indices with replacement and scores both sides on that one draw. A replicate where either F1 is
  undefined is counted in ``undefined`` and left out of the percentiles. A record with no counts on either side
  (nothing labelled, nothing predicted) adds nothing to either F1, so it cannot pull the difference toward 0.

The detector tails (G4). "sf" means the inclusive upper tail ``P(X >= m)``; every ``*_logsf`` is its natural log,
computed in log space so that counts up to 8*10^9 stay finite, and is never above 0.0:

* :func:`poisson_logsf` ``(c, lam)``: ``log P(X >= c)`` for ``X ~ Poisson(lam)``, ``c`` an int >= 0, ``lam`` finite
  and > 0. ``c = 0`` gives 0.0. Otherwise ``P(X >= c)`` is the regularised lower incomplete gamma ``P(c, lam)``
  with log prefactor ``-lam + c*log(lam) - lgamma(c)``. When ``lam < c + 1`` it is the prefactor times the series
  ``sum_{n>=0} lam^n / (c (c+1) ... (c+n))``, stopped when a term falls below ``sum * 1e-17``; otherwise
  ``log1p(-Q)``, with ``Q(c, lam)`` the prefactor times the modified-Lentz continued fraction, stopped when
  ``|delta - 1| < 1e-16``. Either loop raises ``ArithmeticError`` after 10^7 iterations.
* :func:`binom_logsf` ``(m, n, p)``: ``log P(X >= m)`` for ``X ~ Bin(n, p)``; ``m <= 0`` gives 0.0, ``m > n``
  gives -inf, ``p = 0`` gives -inf and ``p = 1`` gives 0.0; otherwise the log-sum-exp of
  ``lgamma(n+1) - lgamma(j+1) - lgamma(n-j+1) + j*log(p) + (n-j)*log1p(-p)`` over ``j = m..n``.
* :func:`poisson_binomial_logsf` ``(ps, m)``: ``log P(sum_i Bernoulli(p_i) >= m)`` by the exact O(n^2) dynamic
  programme in log space: ``dp[0] = 0``, the rest -inf; per ``p``,
  ``new[j] = logaddexp(dp[j] + log1p(-p), dp[j-1] + log(p))``, where a term with ``p = 0`` or ``p = 1`` is skipped
  rather than taking ``log(0)``; the tail is ``logsumexp(dp[m..n])``. ``m <= 0`` gives 0.0, ``m > n`` -inf, and an
  exactly-zero probability -inf.
* :func:`smoothed_pmi` ``(n_xy, n_x, n_y, n, a) = log(((n_xy + a)(n + a)) / ((n_x + a)(n_y + a)))``, counts and
  ``a`` non-negative; a zero factor raises ``ValueError('undefined')``.
* :func:`logistic` ``(x) = 1 / (1 + exp(-x))`` for ``x >= 0`` and ``exp(x) / (1 + exp(x))`` otherwise, so it never
  overflows: ``logistic(-800) == 0.0`` and ``logistic(800) == 1.0``.

Each validates its input (no bool, no NaN or infinity, ints where an int is meant) and raises ``ValueError`` with a
message that names the argument, never its value.

The ranking metrics and the cluster bootstrap (G5). Items are ``(score, relevant)`` pairs; tied scores are never
broken by input order or by a stable sort, they are averaged exactly over every ordering of the tie:

* :func:`tie_averaged_ap` ``(scores, relevant, n_relevant=None)``: tie groups are formed by equal score, in
  descending score order. For a group of ``n`` items with ``r`` relevant, ``s`` items before it and ``R_b`` relevant
  items before it, the contribution is ``r * (R_b + 1) / (s + 1)`` when ``n = 1``, else
  ``sum_{j=1..n} (r / n) * (R_b + 1 + (j - 1)(r - 1) / (n - 1)) / (s + j)``, the exact expectation over uniformly
  random orderings of the ties (position ``s + j`` holds a relevant item with probability ``r / n``, and then the
  expected number of relevant items among the ``j - 1`` tied items above it is ``(j - 1)(r - 1) / (n - 1)``).
  ``AP = sum of contributions / n_relevant``. ``n_relevant`` defaults to the number of relevant items and must be an
  int at least that number, so relevant items that were never ranked lower AP. None when ``n_relevant`` is 0.
* :func:`tie_averaged_precision_at_k` ``(scores, relevant, k)``: the expected number of relevant items in the top
  ``k`` is the sum of ``r`` over the groups lying wholly inside the top ``k``, plus ``r * (k - s) / n`` for the group
  that straddles position ``k``; divided by ``k`` always, even when fewer than ``k`` items are ranked. An empty
  ranking gives 0.0.
* :func:`cluster_bootstrap_mean` ``(clusters, *, B, seed, alpha=0.05)``: ``mean`` is the pooled mean over every unit
  of every cluster. Each of ``B`` replicates draws ``n_clusters`` whole clusters with replacement
  (``random.Random(seed)``) and takes the pooled mean of the drawn units; the interval is the replicates'
  :func:`percentile` at ``100 * alpha / 2`` and ``100 * (1 - alpha / 2)``. Units of one cluster are never split.

The paired ranking bootstrap (G6, E2):

* :func:`paired_ranking_bootstrap` ``(scores, relevant, *, k, B, seed, alpha=0.05, ratio=None, epsilon=0.01,
  clusters=None)``: ``scores`` maps each condition name to one score per item, ``relevant`` marks each item. Point
  estimates per condition: AP is :func:`tie_averaged_ap` with ``n_relevant`` = all relevant items (None when there is
  none), and precision@k is :func:`tie_averaged_precision_at_k`. Each of ``B`` replicates draws ``n`` item indices
  with replacement from one ``random.Random(seed)`` stream, and every condition is scored on the same draw (paired).
  With ``clusters`` (one label per item) a replicate instead draws as many labels as there are distinct labels, with
  replacement, from the labels in sorted order, and takes every item of each drawn label (in item order), so items
  that share a label are never split; ``method`` is then ``paired cluster percentile`` and ``n_clusters`` the number
  of labels. In a replicate AP uses ``n_relevant`` = the relevant items drawn; a replicate with none drawn is counted
  in ``ap_undefined`` and left out of that condition's AP interval. ``chance`` is the AP of a ranking that ties every
  item (the expected AP of a random order, near the prevalence ``n_relevant / n``), on the same draws.
  With ``ratio = (numerator, denominator)`` the estimate is ``AP_num / AP_den``, None when ``AP_den`` is None or
  below ``epsilon``; a replicate's ratio is undefined (counted, left out) on the same rule. ``lift`` is the
  chance-corrected ratio ``(AP_num - chance) / (AP_den - chance)``, the share of the denominator's lift over a random
  order that the numerator keeps, undefined when ``AP_den - chance`` is below ``epsilon``; ``denominator_lift`` is
  ``AP_den - chance``. Every interval is the :func:`percentile` of the defined replicates at ``100 * alpha / 2`` and
  ``100 * (1 - alpha / 2)``, None when there is none. ``method`` is ``paired percentile`` without clusters.
"""
from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence

Z95 = 1.959963984540054


def _check_numbers(values: Sequence[Any], what: str) -> list[float]:
    out = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
            raise ValueError(f"{what} must be numbers (no bool, no NaN)") from None
        out.append(v)
    return out


def percentile(values: Sequence[float], p: float) -> float | None:
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 100:
        raise ValueError("p must be a number in [0, 100]") from None
    xs = sorted(_check_numbers(values, "values"))
    if not xs:
        return None
    rank = (len(xs) - 1) * p / 100
    lo = math.floor(rank)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def mean(values: Sequence[float]) -> float | None:
    xs = _check_numbers(values, "values")
    return math.fsum(xs) / len(xs) if xs else None


def sd(values: Sequence[float]) -> float | None:
    """Sample standard deviation (n - 1 in the denominator); None below two values."""
    xs = _check_numbers(values, "values")
    if len(xs) < 2:
        return None
    m = math.fsum(xs) / len(xs)
    return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    for name, v in (("k", k), ("n", n)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} must be an int") from None
    if not 0 <= k <= n:
        raise ValueError("need 0 <= k <= n") from None
    if n == 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    # at k = 0 the lower bound is exactly 0 and at k = n the upper bound is exactly 1; floating point misses by 1 ulp
    low = 0.0 if k == 0 else max(0.0, center - half)
    high = 1.0 if k == n else min(1.0, center + half)
    return low, high


def _wilson_real(p: float, n: float, z: float) -> tuple[float, float]:
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (0.0 if p == 0 else max(0.0, center - half)), (1.0 if p == 1 else min(1.0, center + half))


def stratified_share(strata: Sequence[tuple[float, int, int, float]], z: float = Z95) -> dict[str, Any]:
    if not isinstance(strata, (list, tuple)) or not strata:
        raise ValueError("strata must be a non-empty list") from None
    rows = []
    for item in strata:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            raise ValueError("each stratum must be (weight, k, n, N)") from None
        w, k, n, big_n = _real_arg(item[0], "weight"), _int_arg(item[1], "k", 0), _int_arg(item[2], "n", 1), \
            _real_arg(item[3], "N")
        if w < 0 or k > n or big_n < n:
            raise ValueError("need weight >= 0, k <= n and N >= n") from None
        rows.append((w, k, n, big_n))
    if not math.isclose(math.fsum(r[0] for r in rows), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("the weights must sum to 1") from None
    share = min(1.0, max(0.0, math.fsum(w * k / n for w, k, n, _ in rows)))
    variance = math.fsum(w * w * (1 - n / big_n) * (k / n) * (1 - k / n) / (n - 1)
                         for w, k, n, big_n in rows if n > 1)
    total = sum(n for _, _, n, _ in rows)
    spread = share * (1 - share)
    n_eff = float(total) if variance <= 0 or spread <= 0 else min(float(total), spread / variance)
    low, high = _wilson_real(share, n_eff, z)
    return {"share": share, "ci_low": low, "ci_high": high, "variance": max(0.0, variance), "n_eff": n_eff,
            "n": total, "method": "stratified wilson (effective n)"}


def sign_test(diffs: Sequence[float]) -> dict[str, Any]:
    xs = _check_numbers(diffs, "diffs")
    n_pos = sum(1 for d in xs if d > 0)
    n_neg = sum(1 for d in xs if d < 0)
    n_eff = n_pos + n_neg
    if n_eff == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(n_eff, i) for i in range(min(n_pos, n_neg) + 1))
        p_value = min(1.0, (2 * tail) / (2 ** n_eff))
    return {"n_pos": n_pos, "n_neg": n_neg, "n_ties": len(xs) - n_eff, "n_eff": n_eff, "p_value": p_value}


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, B: int = 10000, seed: int | str,
                     alpha: float = 0.05) -> dict[str, Any]:
    xa, xb = _check_numbers(a, "a"), _check_numbers(b, "b")
    if len(xa) != len(xb):
        raise ValueError("a and b must have the same length") from None
    if not xa:
        raise ValueError("need at least one pair") from None
    if isinstance(B, bool) or not isinstance(B, int) or B < 1:
        raise ValueError("B must be a positive int") from None
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)") from None
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise ValueError("seed must be an int or str") from None
    diffs = [x - y for x, y in zip(xa, xb)]
    n = len(diffs)
    rng = random.Random(seed)
    reps = []
    for _ in range(B):
        reps.append(math.fsum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    return {"n": n, "mean_diff": math.fsum(diffs) / n, "ci_low": percentile(reps, 100 * alpha / 2),
            "ci_high": percentile(reps, 100 * (1 - alpha / 2)), "B": B, "seed": seed, "method": "percentile"}


def _check_count(v: Any, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"{what} must be an int >= 0") from None
    return v


def f1_from_counts(tp: int, fp: int, fn: int) -> float | None:
    tp, fp, fn = _check_count(tp, "tp"), _check_count(fp, "fp"), _check_count(fn, "fn")
    denominator = 2 * tp + fp + fn
    return 2 * tp / denominator if denominator else None


def bootstrap_f1(counts: Sequence[Sequence[int]], *, B: int, seed: int | str, alpha: float = 0.05) -> dict[str, Any]:
    rows = []
    for row in counts:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise ValueError("counts must be (tp, fp, fn) triples") from None
        rows.append(tuple(_check_count(v, "a count") for v in row))
    if not rows:
        raise ValueError("need at least one record") from None
    if isinstance(B, bool) or not isinstance(B, int) or B < 1:
        raise ValueError("B must be a positive int") from None
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)") from None
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise ValueError("seed must be an int or str") from None
    n = len(rows)
    rng = random.Random(seed)
    reps, undefined = [], 0
    for _ in range(B):
        tp = fp = fn = 0
        for _ in range(n):
            a, b, c = rows[rng.randrange(n)]
            tp, fp, fn = tp + a, fp + b, fn + c
        value = f1_from_counts(tp, fp, fn)
        if value is None:
            undefined += 1
        else:
            reps.append(value)
    total = tuple(sum(r[i] for r in rows) for i in range(3))
    return {"f1": f1_from_counts(*total), "ci_low": percentile(reps, 100 * alpha / 2) if reps else None,
            "ci_high": percentile(reps, 100 * (1 - alpha / 2)) if reps else None, "B": B, "seed": seed,
            "method": "percentile", "undefined": undefined}


def paired_bootstrap_f1(a: Sequence[Sequence[int]], b: Sequence[Sequence[int]], *, B: int, seed: int | str,
                        alpha: float = 0.05) -> dict[str, Any]:
    sides = []
    for counts in (a, b):
        rows = []
        for row in counts:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                raise ValueError("counts must be (tp, fp, fn) triples") from None
            rows.append(tuple(_check_count(v, "a count") for v in row))
        sides.append(rows)
    ra, rb = sides
    if len(ra) != len(rb):
        raise ValueError("a and b must have the same length") from None
    if not ra:
        raise ValueError("need at least one record") from None
    if isinstance(B, bool) or not isinstance(B, int) or B < 1:
        raise ValueError("B must be a positive int") from None
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)") from None
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise ValueError("seed must be an int or str") from None
    rows = [x + y for x, y in zip(ra, rb)]            # (tp, fp, fn) of a, then of b, per record

    def pooled(picks: Sequence[int]) -> tuple[float | None, float | None]:
        t = [0] * 6
        for j in picks:
            for i, v in enumerate(rows[j]):
                t[i] += v
        return f1_from_counts(*t[:3]), f1_from_counts(*t[3:])

    n = len(rows)
    f1_a, f1_b = pooled(range(n))
    rng = random.Random(seed)
    reps, undefined = [], 0
    for _ in range(B):
        x, y = pooled([rng.randrange(n) for _ in range(n)])
        if x is None or y is None:
            undefined += 1
        else:
            reps.append(x - y)
    return {"n": n, "f1_a": f1_a, "f1_b": f1_b, "diff": None if f1_a is None or f1_b is None else f1_a - f1_b,
            "ci_low": percentile(reps, 100 * alpha / 2) if reps else None,
            "ci_high": percentile(reps, 100 * (1 - alpha / 2)) if reps else None, "B": B, "seed": seed,
            "method": "paired percentile", "undefined": undefined}


# --------------------------------------------------------------------------------------------------- detector tails

NEG_INF = -math.inf
MAX_ITERATIONS = 10 ** 7
_TINY = 1e-300


def _int_arg(v: Any, what: str, lo: int | None = None) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or (lo is not None and v < lo):
        raise ValueError(f"{what} must be an int" + ("" if lo is None else f" >= {lo}")) from None
    return v


def _real_arg(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not math.isfinite(v)):
        raise ValueError(f"{what} must be a finite number (no bool)") from None
    return v


def _log_add(a: float, b: float) -> float:
    if a == NEG_INF:
        return b
    if b == NEG_INF:
        return a
    hi, lo = (a, b) if a >= b else (b, a)
    return hi + math.log1p(math.exp(lo - hi))


def _log_sum(xs: Sequence[float]) -> float:
    hi = max(xs, default=NEG_INF)
    if hi == NEG_INF:
        return NEG_INF
    return hi + math.log(math.fsum(math.exp(x - hi) for x in xs))


def poisson_logsf(c: int, lam: float) -> float:
    c = _int_arg(c, "c", 0)
    lam = _real_arg(lam, "lam")
    if lam <= 0:
        raise ValueError("lam must be > 0") from None
    if c == 0:
        return 0.0
    prefix = -lam + c * math.log(lam) - math.lgamma(c)
    if lam < c + 1:
        term = total = 1.0 / c
        n = 0
        while term >= total * 1e-17:
            n += 1
            if n > MAX_ITERATIONS:
                raise ArithmeticError("poisson_logsf series did not converge") from None
            term *= lam / (c + n)
            total += term
        return min(0.0, prefix + math.log(total))
    b = lam + 1.0 - c
    lentz_c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    i = 0
    while True:
        i += 1
        if i > MAX_ITERATIONS:
            raise ArithmeticError("poisson_logsf continued fraction did not converge") from None
        an = -i * (i - c)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        lentz_c = b + an / lentz_c
        if abs(lentz_c) < _TINY:
            lentz_c = _TINY
        d = 1.0 / d
        delta = d * lentz_c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return min(0.0, math.log1p(-math.exp(prefix) * h))


def poisson_sf(c: int, lam: float) -> float:
    return math.exp(poisson_logsf(c, lam))


def binom_logsf(m: int, n: int, p: float) -> float:
    m = _int_arg(m, "m")
    n = _int_arg(n, "n", 0)
    p = _real_arg(p, "p")
    if not 0 <= p <= 1:
        raise ValueError("p must be in [0, 1]") from None
    if m <= 0:
        return 0.0
    if m > n or p == 0:
        return NEG_INF
    if p == 1:
        return 0.0
    lp, lq, top = math.log(p), math.log1p(-p), math.lgamma(n + 1)
    terms = [top - math.lgamma(j + 1) - math.lgamma(n - j + 1) + j * lp + (n - j) * lq for j in range(m, n + 1)]
    return min(0.0, _log_sum(terms))


def binom_sf(m: int, n: int, p: float) -> float:
    return math.exp(binom_logsf(m, n, p))


def poisson_binomial_logsf(ps: Sequence[float], m: int) -> float:
    if not isinstance(ps, (list, tuple)):
        raise ValueError("ps must be a list or tuple of probabilities") from None
    probs = []
    for p in ps:
        p = _real_arg(p, "each p")
        if not 0 <= p <= 1:
            raise ValueError("each p must be in [0, 1]") from None
        probs.append(p)
    m = _int_arg(m, "m")
    n = len(probs)
    if m <= 0:
        return 0.0
    if m > n:
        return NEG_INF
    dp = [0.0] + [NEG_INF] * n
    for p in probs:
        if p == 0:
            continue
        if p == 1:
            dp = [NEG_INF] + dp[:-1]
            continue
        lp, lq = math.log(p), math.log1p(-p)
        dp = [_log_add(dp[j] + lq, dp[j - 1] + lp if j else NEG_INF) for j in range(n + 1)]
    return min(0.0, _log_sum(dp[m:]))


def smoothed_pmi(n_xy: float, n_x: float, n_y: float, n: float, smoothing: float) -> float:
    for name, v in (("n_xy", n_xy), ("n_x", n_x), ("n_y", n_y), ("n", n), ("smoothing", smoothing)):
        if _real_arg(v, name) < 0:
            raise ValueError(f"{name} must be >= 0") from None
    a = smoothing
    num = (n_xy + a) * (n + a)
    den = (n_x + a) * (n_y + a)
    if num == 0 or den == 0:
        raise ValueError("undefined") from None
    return math.log(num / den)


def logistic(x: float) -> float:
    x = _real_arg(x, "x")
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


# --------------------------------------------------------------------------------------------------- ranking (G5)

def _ranking(scores: Sequence[Any], relevant: Sequence[Any]) -> list[tuple[int, int]]:
    """``(n, r)`` per tie group, in descending score order."""
    if not isinstance(scores, (list, tuple)) or not isinstance(relevant, (list, tuple)):
        raise ValueError("scores and relevant must be lists or tuples") from None
    if len(scores) != len(relevant):
        raise ValueError("scores and relevant must have the same length") from None
    for s in scores:
        _real_arg(s, "each score")
    if not all(isinstance(r, bool) for r in relevant):
        raise ValueError("relevant must hold bools") from None
    groups: dict[float, list[int]] = {}
    for s, r in zip(scores, relevant):
        group = groups.setdefault(s, [0, 0])
        group[0] += 1
        group[1] += int(r)
    return [(groups[s][0], groups[s][1]) for s in sorted(groups, reverse=True)]


def tie_averaged_ap(scores: Sequence[float], relevant: Sequence[bool], n_relevant: int | None = None) -> float | None:
    groups = _ranking(scores, relevant)
    found = sum(r for _, r in groups)
    if n_relevant is None:
        n_relevant = found
    if isinstance(n_relevant, bool) or not isinstance(n_relevant, int) or n_relevant < found:
        raise ValueError("n_relevant must be an int >= the number of relevant items") from None
    if n_relevant == 0:
        return None
    terms: list[float] = []
    before = relevant_before = 0
    for n, r in groups:
        if n == 1:
            terms.append(r * (relevant_before + 1) / (before + 1))
        else:
            terms.extend((r / n) * (relevant_before + 1 + (j - 1) * (r - 1) / (n - 1)) / (before + j)
                         for j in range(1, n + 1))
        before += n
        relevant_before += r
    return math.fsum(terms) / n_relevant


def tie_averaged_precision_at_k(scores: Sequence[float], relevant: Sequence[bool], k: int) -> float:
    groups = _ranking(scores, relevant)
    k = _int_arg(k, "k", 1)
    expected: list[float] = []
    before = 0
    for n, r in groups:
        if before >= k:
            break
        expected.append(float(r) if before + n <= k else r * (k - before) / n)
        before += n
    return math.fsum(expected) / k


def cluster_bootstrap_mean(clusters: Sequence[Sequence[float]], *, B: int, seed: int | str,
                           alpha: float = 0.05) -> dict[str, Any]:
    if not isinstance(clusters, (list, tuple)) or not clusters:
        raise ValueError("clusters must be a non-empty list of clusters") from None
    units: list[list[float]] = []
    for c in clusters:
        if not isinstance(c, (list, tuple)) or not c:
            raise ValueError("each cluster must be a non-empty list of numbers") from None
        units.append([_real_arg(v, "each value") for v in c])
    if isinstance(B, bool) or not isinstance(B, int) or B < 1:
        raise ValueError("B must be a positive int") from None
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)") from None
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise ValueError("seed must be an int or str") from None
    n = len(units)
    sums = [math.fsum(c) for c in units]
    sizes = [len(c) for c in units]
    rng = random.Random(seed)
    reps = []
    for _ in range(B):
        drawn = [rng.randrange(n) for _ in range(n)]
        reps.append(math.fsum(sums[i] for i in drawn) / sum(sizes[i] for i in drawn))
    return {"mean": math.fsum(sums) / sum(sizes), "ci_low": percentile(reps, 100 * alpha / 2),
            "ci_high": percentile(reps, 100 * (1 - alpha / 2)), "B": B, "seed": seed, "method": "cluster percentile",
            "n_clusters": n, "n_units": sum(sizes)}


# --------------------------------------------------------------------------------------------------- paired ranking

def paired_ranking_bootstrap(scores: Mapping[str, Sequence[float]], relevant: Sequence[bool], *, k: int, B: int,
                             seed: int | str, alpha: float = 0.05, ratio: tuple[str, str] | None = None,
                             epsilon: float = 0.01, clusters: Sequence[str] | None = None) -> dict[str, Any]:
    if not isinstance(scores, Mapping) or not scores or not all(isinstance(name, str) for name in scores):
        raise ValueError("scores must map condition names to score lists") from None
    if not isinstance(relevant, (list, tuple)) or not all(isinstance(r, bool) for r in relevant):
        raise ValueError("relevant must be a list of bools") from None
    n = len(relevant)
    if n == 0:
        raise ValueError("need at least one item") from None
    for name in scores:
        values = scores[name]
        if not isinstance(values, (list, tuple)) or len(values) != n:
            raise ValueError("every condition needs one score per item") from None
        for v in values:
            _real_arg(v, "each score")
    _int_arg(k, "k", 1)
    _int_arg(B, "B", 1)
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)") from None
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise ValueError("seed must be an int or str") from None
    if ratio is not None and (not isinstance(ratio, (list, tuple)) or len(ratio) != 2
                              or not all(name in scores for name in ratio)):
        raise ValueError("ratio must name two conditions") from None
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)) or not math.isfinite(epsilon) \
            or epsilon <= 0:
        raise ValueError("epsilon must be a finite number > 0") from None
    if clusters is not None and (not isinstance(clusters, (list, tuple)) or len(clusters) != n
                                 or not all(isinstance(c, str) for c in clusters)):
        raise ValueError("clusters must hold one str label per item") from None
    names = sorted(scores)
    rel = list(relevant)
    n_relevant = sum(rel)
    groups: list[list[int]] = []
    if clusters is not None:
        members: dict[str, list[int]] = {}
        for i, label in enumerate(clusters):
            members.setdefault(label, []).append(i)
        groups = [members[label] for label in sorted(members)]

    def ap(values: Sequence[float], marks: Sequence[bool]) -> float | None:
        return tie_averaged_ap(list(values), list(marks)) if any(marks) else None

    def quotient(num: float | None, den: float | None) -> float | None:
        return None if num is None or den is None or den < epsilon else num / den

    def lift(num: float | None, den: float | None, base: float | None) -> float | None:
        if num is None or den is None or base is None:
            return None
        return quotient(num - base, den - base)

    point_ap = {name: ap(scores[name], rel) for name in names}
    point_chance = ap([0.0] * n, rel)
    ap_reps: dict[str, list[float]] = {name: [] for name in names}
    p_reps: dict[str, list[float]] = {name: [] for name in names}
    chance_reps: list[float] = []
    ratio_reps: list[float] = []
    lift_reps: list[float] = []
    den_lift_reps: list[float] = []
    ap_undefined = ratio_undefined = lift_undefined = 0
    rng = random.Random(seed)
    for _ in range(B):
        if clusters is None:
            drawn = [rng.randrange(n) for _ in range(n)]
        else:
            drawn = [i for _ in range(len(groups)) for i in groups[rng.randrange(len(groups))]]
        marks = [rel[i] for i in drawn]
        defined = any(marks)
        ap_undefined += int(not defined)
        chance = tie_averaged_ap([0.0] * len(drawn), marks) if defined else None
        if chance is not None:
            chance_reps.append(chance)
        replicate: dict[str, float | None] = {}
        for name in names:
            values = [scores[name][i] for i in drawn]
            replicate[name] = tie_averaged_ap(values, marks) if defined else None
            if replicate[name] is not None:
                ap_reps[name].append(replicate[name])
            p_reps[name].append(tie_averaged_precision_at_k(values, marks, k))
        if ratio is not None:
            value = quotient(replicate[ratio[0]], replicate[ratio[1]])
            if value is None:
                ratio_undefined += 1
            else:
                ratio_reps.append(value)
            value = lift(replicate[ratio[0]], replicate[ratio[1]], chance)
            if value is None:
                lift_undefined += 1
            else:
                lift_reps.append(value)
            if replicate[ratio[1]] is not None and chance is not None:
                den_lift_reps.append(replicate[ratio[1]] - chance)

    def ci(reps: Sequence[float]) -> tuple[float | None, float | None]:
        if not reps:
            return None, None
        return percentile(reps, 100 * alpha / 2), percentile(reps, 100 * (1 - alpha / 2))

    conditions = {}
    for name in names:
        ap_low, ap_high = ci(ap_reps[name])
        p_low, p_high = ci(p_reps[name])
        conditions[name] = {"ap": point_ap[name], "ap_ci_low": ap_low, "ap_ci_high": ap_high,
                            "ap_undefined": ap_undefined,
                            "precision_at_k": tie_averaged_precision_at_k(list(scores[name]), rel, k),
                            "p_ci_low": p_low, "p_ci_high": p_high}
    ratio_doc = None
    if ratio is not None:
        low, high = ci(ratio_reps)
        lift_low, lift_high = ci(lift_reps)
        den_low, den_high = ci(den_lift_reps)
        den_ap = point_ap[ratio[1]]
        ratio_doc = {"numerator": ratio[0], "denominator": ratio[1],
                     "estimate": quotient(point_ap[ratio[0]], den_ap), "ci_low": low, "ci_high": high,
                     "undefined": ratio_undefined, "epsilon": epsilon,
                     "lift_estimate": lift(point_ap[ratio[0]], den_ap, point_chance), "lift_ci_low": lift_low,
                     "lift_ci_high": lift_high, "lift_undefined": lift_undefined,
                     "denominator_lift": None if den_ap is None or point_chance is None else den_ap - point_chance,
                     "denominator_lift_ci_low": den_low, "denominator_lift_ci_high": den_high}
    chance_low, chance_high = ci(chance_reps)
    return {"n": n, "n_relevant": n_relevant, "B": B, "seed": seed, "k": k,
            "method": "paired percentile" if clusters is None else "paired cluster percentile",
            "n_clusters": None if clusters is None else len(groups),
            "chance": {"prevalence": n_relevant / n, "ap": point_chance, "ap_ci_low": chance_low,
                       "ap_ci_high": chance_high},
            "conditions": conditions, "ratio": ratio_doc}
