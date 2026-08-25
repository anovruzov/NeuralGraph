"""Seeded power analysis for paired binary outcomes with an unreliable judge.

The question this answers is not "did the round improve" but "could this
evaluation have detected an improvement at all". A frozen 60-question set with a
judge that flips ~4.5% of identical answers may be unable to distinguish a real
three-question gain from noise, in which case scored rounds on it are diagnostic
rather than confirmatory and should be described that way.

Model. Each question is a paired binary trial. The baseline is correct with
probability p0 = 24/60. A candidate with a true net gain of d questions flips
some baseline-wrong answers to right. On top of that, the *judge* independently
flips any verdict with probability `flip` -- measured here at 4.5% from 22
observations of one byte-identical candidate. The judge flip is applied
independently to the baseline and candidate verdicts, which is what makes it so
corrosive: it adds variance to both arms of a paired comparison that was
supposed to cancel shared variance.

Everything is seeded. `simulate` with the same seed returns the same numbers.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

BASELINE_CORRECT = 24
N_DEFAULT = 60
JUDGE_FLIP = 0.045          # measured: 1 CORRECT in 22 identical calls
ALPHA = 0.05
SEED = 20260825


def mcnemar_exact(improved: int, regressed: int) -> float:
    """Two-sided exact McNemar: binomial test on the discordant pairs."""
    n = improved + regressed
    if n == 0:
        return 1.0
    k = min(improved, regressed)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _trial(rng: random.Random, n: int, baseline_correct: int, true_gain: int,
           flip: float) -> tuple[int, int]:
    """One simulated experiment. Returns (improved, regressed) as *observed*."""
    truth_base = [True] * baseline_correct + [False] * (n - baseline_correct)
    rng.shuffle(truth_base)

    truth_cand = list(truth_base)
    wrong_idx = [i for i, v in enumerate(truth_cand) if not v]
    rng.shuffle(wrong_idx)
    for i in wrong_idx[:max(0, true_gain)]:
        truth_cand[i] = True
    if true_gain < 0:
        right_idx = [i for i, v in enumerate(truth_cand) if v]
        rng.shuffle(right_idx)
        for i in right_idx[: -true_gain]:
            truth_cand[i] = False

    # The judge observes each verdict independently, and flips some of them.
    obs_base = [v ^ (rng.random() < flip) for v in truth_base]
    obs_cand = [v ^ (rng.random() < flip) for v in truth_cand]

    improved = sum(1 for a, b in zip(obs_base, obs_cand) if not a and b)
    regressed = sum(1 for a, b in zip(obs_base, obs_cand) if a and not b)
    return improved, regressed


def power(true_gain: int, n: int = N_DEFAULT, baseline_correct: int = BASELINE_CORRECT,
          flip: float = JUDGE_FLIP, iterations: int = 4000,
          alpha: float = ALPHA, seed: int = SEED) -> dict[str, Any]:
    """Probability that an experiment with this true gain reaches significance."""
    rng = random.Random(seed)
    scaled_base = round(baseline_correct * n / N_DEFAULT)
    hits = 0
    deltas: list[float] = []
    for _ in range(iterations):
        improved, regressed = _trial(rng, n, scaled_base, true_gain, flip)
        if mcnemar_exact(improved, regressed) < alpha:
            hits += 1
        deltas.append((improved - regressed) / n)
    deltas.sort()
    return {
        "true_gain_questions": true_gain,
        "true_gain_points": round(100 * true_gain / n, 2),
        "n": n,
        "judge_flip": flip,
        "power": round(hits / iterations, 4),
        "mean_observed_delta": round(sum(deltas) / len(deltas), 4),
        "observed_delta_ci95": [round(deltas[int(0.025 * iterations)], 4),
                                round(deltas[int(0.975 * iterations) - 1], 4)],
        "iterations": iterations,
    }


def minimum_detectable(n: int = N_DEFAULT, flip: float = JUDGE_FLIP,
                       target_power: float = 0.80, max_gain: int = 40,
                       iterations: int = 4000, seed: int = SEED) -> int | None:
    """Smallest true gain (in questions) reaching `target_power` at this n."""
    for gain in range(1, max_gain + 1):
        if power(gain, n=n, flip=flip, iterations=iterations, seed=seed)["power"] >= target_power:
            return gain
    return None


def required_n(true_gain_points: float, flip: float = JUDGE_FLIP,
               target_power: float = 0.80, iterations: int = 2000,
               seed: int = SEED, cap: int = 4000) -> int | None:
    """Sample size needed for `target_power` at a fixed *effect size in points*.

    The gain is expressed in percentage points rather than questions so it stays
    constant as n grows -- otherwise a larger n would silently test a larger
    effect.
    """
    n = 20
    while n <= cap:
        gain = max(1, round(true_gain_points * n / 100))
        if power(gain, n=n, flip=flip, iterations=iterations, seed=seed)["power"] >= target_power:
            return n
        n = int(n * 1.5) + 1
    return None


def analyse(iterations: int = 4000, seed: int = SEED) -> dict[str, Any]:
    gains = list(range(1, 11))
    curve = [power(g, iterations=iterations, seed=seed) for g in gains]

    # How much does judge instability alone cost?
    perfect = {g: power(g, flip=0.0, iterations=iterations, seed=seed)["power"] for g in gains}
    noisy = {g: c["power"] for g, c in zip(gains, curve)}

    effect_points = [3.0, 5.0, 8.0, 10.0]
    required = {
        f"{p}pt": {
            "with_judge_noise": required_n(p, flip=JUDGE_FLIP, iterations=iterations, seed=seed),
            "perfect_judge": required_n(p, flip=0.0, iterations=iterations, seed=seed),
        } for p in effect_points
    }

    mde = minimum_detectable(iterations=iterations, seed=seed)
    return {
        "seed": seed,
        "iterations": iterations,
        "baseline": f"{BASELINE_CORRECT}/{N_DEFAULT}",
        "judge_flip_measured": JUDGE_FLIP,
        "alpha": ALPHA,
        "power_curve": curve,
        "power_at_effect_points": {
            f"{round(100*g/N_DEFAULT,1)}pt ({g}q)": noisy[g] for g in gains
        },
        "judge_noise_cost": {
            f"{g}q": {"perfect_judge": perfect[g], "measured_judge": noisy[g],
                      "power_lost": round(perfect[g] - noisy[g], 4)} for g in gains
        },
        "minimum_detectable_gain_questions": mde,
        "minimum_detectable_gain_points": (round(100 * mde / N_DEFAULT, 2)
                                           if mde else None),
        "required_n_for_80pct_power": required,
    }
