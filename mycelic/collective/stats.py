"""The few statistics the experiment harnesses report, written out so a reviewer can check each formula.

Pure functions; randomness only through ``random.Random(seed)`` with an explicit seed, so every figure can be
recomputed bit for bit from the run file.

* :func:`percentile` uses linear interpolation between closest ranks, ``rank = (n - 1) * p / 100`` (numpy's
  default ``'linear'`` method), so p50 of ``[1, 2, 3, 4]`` is 2.5.
* :func:`wilson` is the Wilson score interval in closed form (95% by default), clamped to ``[0, 1]``.
* :func:`sign_test` is the exact two-sided sign test on paired differences with zeros dropped:
  ``p = min(1, 2 * sum_{i <= min(n+, n-)} C(n, i) / 2^n)``, summed in integers before the one final division.
* :func:`paired_bootstrap` is a percentile bootstrap of the mean paired difference.
* :func:`f1_from_counts` is ``2TP / (2TP + FP + FN)``, None when the denominator is 0 (nothing predicted, nothing
  to find), never 1.0 by convention.
* :func:`bootstrap_f1` resamples records (each a ``(tp, fp, fn)`` triple) with replacement and recomputes the
  pooled F1; resamples whose F1 is undefined are counted in ``undefined`` and left out of the percentiles.

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
"""
from __future__ import annotations

import math
import random
from typing import Any, Sequence

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
