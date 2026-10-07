"""The few statistics the experiment harnesses report, written out so a reviewer can check each formula.

Pure functions; randomness only through ``random.Random(seed)`` with an explicit seed, so every figure can be
recomputed bit for bit from the run file.

* :func:`percentile` uses linear interpolation between closest ranks, ``rank = (n - 1) * p / 100`` (numpy's
  default ``'linear'`` method), so p50 of ``[1, 2, 3, 4]`` is 2.5.
* :func:`wilson` is the Wilson score interval in closed form (95% by default), clamped to ``[0, 1]``.
* :func:`sign_test` is the exact two-sided sign test on paired differences with zeros dropped:
  ``p = min(1, 2 * sum_{i <= min(n+, n-)} C(n, i) / 2^n)``, summed in integers before the one final division.
* :func:`paired_bootstrap` is a percentile bootstrap of the mean paired difference.
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
