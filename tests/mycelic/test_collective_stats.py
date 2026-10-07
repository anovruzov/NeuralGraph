"""The statistics behind every harness figure, checked against independent computations."""
from __future__ import annotations

import itertools
import math
import random
import time
import unittest
from fractions import Fraction

from mycelic.collective import stats


class PercentileTests(unittest.TestCase):
    def test_linear_interpolation_matches_the_documented_values(self) -> None:
        self.assertEqual(stats.percentile([1, 2, 3, 4], 50), 2.5)
        self.assertAlmostEqual(stats.percentile([1, 2, 3, 4], 95), 3.85, places=12)
        self.assertEqual(stats.percentile([4, 1, 3, 2], 0), 1)
        self.assertEqual(stats.percentile([4, 1, 3, 2], 100), 4)
        self.assertEqual(stats.percentile([7], 95), 7)

    def test_empty_input_gives_none_and_p_must_be_in_range(self) -> None:
        self.assertIsNone(stats.percentile([], 50))
        for p in (-0.1, 100.1, True, float("nan"), "50"):
            with self.subTest(p=p), self.assertRaises(ValueError):
                stats.percentile([1, 2], p)

    def test_bool_and_nan_values_are_rejected(self) -> None:
        for values in ([1, True], [1.0, float("nan")]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                stats.percentile(values, 50)


class MeanSdTests(unittest.TestCase):
    def test_mean_and_sample_sd(self) -> None:
        self.assertIsNone(stats.mean([]))
        self.assertEqual(stats.mean([1, 2, 3, 4]), 2.5)
        self.assertIsNone(stats.sd([]))
        self.assertIsNone(stats.sd([3]))
        self.assertAlmostEqual(stats.sd([2, 4, 4, 4, 5, 5, 7, 9]), math.sqrt(32 / 7), places=12)


class WilsonTests(unittest.TestCase):
    def closed_form(self, k: int, n: int, z: float = stats.Z95) -> tuple[float, float]:
        p = k / n
        center = (p + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return center - half, center + half

    def test_matches_the_closed_form(self) -> None:
        for k, n in ((60, 300), (29, 300), (45, 300), (1, 7), (13, 20)):
            lo, hi = stats.wilson(k, n)
            elo, ehi = self.closed_form(k, n)
            self.assertAlmostEqual(lo, elo, delta=1e-12)
            self.assertAlmostEqual(hi, ehi, delta=1e-12)

    def test_extremes_stay_in_the_unit_interval(self) -> None:
        for k, n in ((0, 10), (10, 10), (0, 1), (1, 1)):
            lo, hi = stats.wilson(k, n)
            self.assertGreaterEqual(lo, 0.0)
            self.assertLessEqual(hi, 1.0)
            self.assertLessEqual(lo, k / n)
            self.assertGreaterEqual(hi, k / n)

    def test_n_zero_and_invalid_input(self) -> None:
        self.assertIsNone(stats.wilson(0, 0))
        for k, n in ((True, 10), (1, True), (-1, 10), (11, 10), (1.0, 10), (1, 0)):
            with self.subTest(k=k, n=n), self.assertRaises(ValueError):
                stats.wilson(k, n)


def _minlike(k: int, n: int) -> float:
    """Two-sided binomial p-value, 'minlike' method, p = 0.5: sum of pmf over outcomes no likelier than k."""
    pmf = [math.comb(n, i) / 2 ** n for i in range(n + 1)]
    return min(1.0, sum(q for q in pmf if q <= pmf[k] * (1 + 1e-7)))


class SignTestTests(unittest.TestCase):
    def test_equals_the_binomial_minlike_sum_for_every_n_and_k_up_to_20(self) -> None:
        for n in range(1, 21):
            for k in range(n + 1):
                diffs = [1.0] * k + [-1.0] * (n - k)
                with self.subTest(n=n, k=k):
                    self.assertAlmostEqual(stats.sign_test(diffs)["p_value"], _minlike(k, n), delta=1e-12)

    def test_equals_exhaustive_sign_enumeration_up_to_12(self) -> None:
        for n in range(1, 13):
            stat = [min(sum(s), n - sum(s)) for s in itertools.product((0, 1), repeat=n)]
            for k in range(n + 1):
                observed = min(k, n - k)
                exact = sum(1 for s in stat if s <= observed) / 2 ** n
                with self.subTest(n=n, k=k):
                    self.assertAlmostEqual(stats.sign_test([1] * k + [-1] * (n - k))["p_value"], exact, delta=1e-12)

    def test_zeros_are_dropped_and_all_ties_give_one(self) -> None:
        r = stats.sign_test([0, 0.0, 0])
        self.assertEqual(r, {"n_pos": 0, "n_neg": 0, "n_ties": 3, "n_eff": 0, "p_value": 1.0})
        r = stats.sign_test([0.5, 0, -2, 3, 0])
        self.assertEqual((r["n_pos"], r["n_neg"], r["n_ties"], r["n_eff"]), (2, 1, 2, 3))
        with self.assertRaises(ValueError):
            stats.sign_test([1, float("nan")])


class BootstrapTests(unittest.TestCase):
    def test_reproducible_for_a_seed_and_different_for_another(self) -> None:
        a = [0.9, 0.8, 0.75, 0.95, 0.6, 0.7, 0.85, 0.9]
        b = [0.85, 0.82, 0.7, 0.9, 0.65, 0.6, 0.8, 0.88]
        r1 = stats.paired_bootstrap(a, b, B=500, seed=11)
        r2 = stats.paired_bootstrap(a, b, B=500, seed=11)
        r3 = stats.paired_bootstrap(a, b, B=500, seed=12)
        self.assertEqual(r1, r2)
        self.assertNotEqual((r1["ci_low"], r1["ci_high"]), (r3["ci_low"], r3["ci_high"]))
        self.assertAlmostEqual(r1["mean_diff"], sum(x - y for x, y in zip(a, b)) / len(a), places=12)
        self.assertLessEqual(r1["ci_low"], r1["mean_diff"])
        self.assertGreaterEqual(r1["ci_high"], r1["mean_diff"])
        self.assertEqual((r1["n"], r1["B"], r1["seed"], r1["method"]), (8, 500, 11, "percentile"))

    def test_input_errors(self) -> None:
        with self.assertRaises(ValueError):
            stats.paired_bootstrap([1, 2], [1], B=10, seed=1)
        with self.assertRaises(ValueError):
            stats.paired_bootstrap([], [], B=10, seed=1)
        with self.assertRaises(TypeError):
            stats.paired_bootstrap([1], [1], B=10)  # seed is required


class F1Tests(unittest.TestCase):
    def test_f1_from_counts_values(self) -> None:
        self.assertEqual(stats.f1_from_counts(3, 1, 1), 0.75)
        self.assertEqual(stats.f1_from_counts(2, 0, 0), 1.0)
        self.assertEqual(stats.f1_from_counts(0, 2, 5), 0.0)
        self.assertIsNone(stats.f1_from_counts(0, 0, 0))

    def test_f1_from_counts_rejects_bad_input(self) -> None:
        for args in ((-1, 0, 0), (1.0, 0, 0), (True, 0, 0), ("1", 0, 0), (0, None, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                stats.f1_from_counts(*args)

    def test_bootstrap_f1_is_deterministic_and_matches_a_direct_recomputation(self) -> None:
        counts = [(1, 0, 0), (0, 1, 1), (2, 0, 1), (3, 1, 0), (1, 1, 1), (0, 0, 2)]
        a = stats.bootstrap_f1(counts, B=400, seed="e1:7:x")
        self.assertEqual(a, stats.bootstrap_f1(counts, B=400, seed="e1:7:x"))
        other = stats.bootstrap_f1(counts, B=400, seed="e1:8:x")
        self.assertNotEqual((a["ci_low"], a["ci_high"]), (other["ci_low"], other["ci_high"]))
        self.assertEqual(a["f1"], stats.f1_from_counts(7, 3, 5))
        self.assertLessEqual(a["ci_low"], a["f1"])
        self.assertGreaterEqual(a["ci_high"], a["f1"])
        self.assertEqual((a["B"], a["seed"], a["method"], a["undefined"]), (400, "e1:7:x", "percentile", 0))
        rng = random.Random("e1:7:x")
        reps = []
        for _ in range(400):
            picks = [counts[rng.randrange(len(counts))] for _ in counts]
            reps.append(stats.f1_from_counts(*(sum(p[i] for p in picks) for i in range(3))))
        self.assertEqual((a["ci_low"], a["ci_high"]), (stats.percentile(reps, 2.5), stats.percentile(reps, 97.5)))

    def test_undefined_resamples_are_counted_and_excluded(self) -> None:
        r = stats.bootstrap_f1([(0, 0, 0)] * 9 + [(1, 0, 0)], B=300, seed=3)
        self.assertGreater(r["undefined"], 0)
        self.assertEqual((r["f1"], r["ci_low"], r["ci_high"]), (1.0, 1.0, 1.0))
        empty = stats.bootstrap_f1([(0, 0, 0), (0, 0, 0)], B=50, seed=3)
        self.assertEqual((empty["f1"], empty["ci_low"], empty["ci_high"], empty["undefined"]), (None, None, None, 50))

    def test_bootstrap_f1_input_validation(self) -> None:
        for counts, kwargs in (([], {}), ([(1, 0)], {}), ([(1, 0, -1)], {}), ([(1, 0, 0.5)], {}), (["abc"], {}),
                               ([(1, 0, 0)], {"B": 0}), ([(1, 0, 0)], {"B": True}), ([(1, 0, 0)], {"alpha": 1}),
                               ([(1, 0, 0)], {"seed": 1.5})):
            args = {"B": 10, "seed": 1, **kwargs}
            with self.subTest(counts=counts, kwargs=kwargs), self.assertRaises(ValueError):
                stats.bootstrap_f1(counts, **args)



# --------------------------------------------------------------------------------------------------- G4: detector tails

BAD_NUMBERS = (True, False, float("nan"), float("inf"), float("-inf"), "1", None, [1])


def _brute_poisson_binomial(ps: list[float], m: int) -> float:
    total = 0.0
    for bits in itertools.product((0, 1), repeat=len(ps)):
        if sum(bits) >= m:
            pr = 1.0
            for b, p in zip(bits, ps):
                pr *= p if b else 1 - p
            total += pr
    return total


def _exact_poisson_tail(c: int, lam: float) -> float:
    """The sum of pmf terms from c upward (exp of the lgamma-based log pmf), until they vanish."""
    terms, j = [], c
    while True:
        t = math.exp(-lam + j * math.log(lam) - math.lgamma(j + 1))
        terms.append(t)
        if (j > lam and t < 1e-320) or j > c + 5000:
            return math.fsum(terms)
        j += 1


class PoissonBinomialTests(unittest.TestCase):
    def test_equals_brute_force_enumeration_on_50_seeded_vectors(self) -> None:
        rng = random.Random("g4-poisson-binomial")
        with_edges = 0
        for case in range(50):
            n = rng.randrange(0, 11)
            ps = [rng.choice((0.0, 1.0, rng.random(), rng.random() ** 6, 1 - rng.random() ** 6)) for _ in range(n)]
            if case % 5 == 0 and n >= 2:
                ps[0], ps[1] = 0.0, 1.0
            with_edges += any(p in (0.0, 1.0) for p in ps)
            for m in range(0, n + 2):
                with self.subTest(case=case, m=m):
                    got = math.exp(stats.poisson_binomial_logsf(ps, m))
                    self.assertAlmostEqual(got, _brute_poisson_binomial(ps, m), delta=1e-12)
        self.assertGreaterEqual(with_edges, 10)

    def test_equal_probabilities_give_the_binomial_tail(self) -> None:
        for n, p in ((1, 0.3), (6, 0.01), (6, 0.25), (10, 0.5), (25, 0.07), (40, 0.9)):
            for m in range(-1, n + 2):
                with self.subTest(n=n, p=p, m=m):
                    a, b = stats.poisson_binomial_logsf([p] * n, m), stats.binom_logsf(m, n, p)
                    if b == -math.inf:
                        self.assertEqual(a, -math.inf)
                    else:
                        self.assertAlmostEqual(a, b, delta=1e-10 * max(1.0, abs(b)))

    def test_edges(self) -> None:
        self.assertEqual(stats.poisson_binomial_logsf([0.2, 0.3], 0), 0.0)
        self.assertEqual(stats.poisson_binomial_logsf([0.2, 0.3], -4), 0.0)
        self.assertEqual(stats.poisson_binomial_logsf([0.2, 0.3], 3), -math.inf)
        self.assertEqual(stats.poisson_binomial_logsf([], 1), -math.inf)
        self.assertEqual(stats.poisson_binomial_logsf([], 0), 0.0)
        self.assertEqual(stats.poisson_binomial_logsf([0.0, 0.0, 0.0], 1), -math.inf)
        self.assertEqual(stats.poisson_binomial_logsf([1.0, 1.0], 2), 0.0)
        self.assertEqual(stats.poisson_binomial_logsf([1.0, 0.0], 2), -math.inf)
        ps = [0.25] * 6
        self.assertAlmostEqual(stats.poisson_binomial_logsf(ps, 6), 6 * math.log(0.25), delta=1e-12)
        self.assertTrue(math.isfinite(stats.poisson_binomial_logsf([0.01] * 1000, 1000)))
        self.assertEqual(str(stats.poisson_binomial_logsf([0.5], 0)), "0.0")

    def test_invalid_input(self) -> None:
        for ps in ([0.5, -0.1], [1.5], [True], [float("nan")], [float("inf")], ["0.5"], "abc", None, {0.5}):
            with self.subTest(ps=ps), self.assertRaises(ValueError):
                stats.poisson_binomial_logsf(ps, 1)
        for m in (1.0, True, "1", None):
            with self.subTest(m=m), self.assertRaises(ValueError):
                stats.poisson_binomial_logsf([0.5], m)


class TailTests(unittest.TestCase):
    def test_poisson_matches_the_exact_pmf_sum(self) -> None:
        for c in range(0, 41):
            for lam in (0.01, 0.08, 0.5, 1, 2.46, 5, 10, 20, 35):
                with self.subTest(c=c, lam=lam):
                    exact, got = _exact_poisson_tail(c, lam), stats.poisson_sf(c, lam)
                    if exact > 1e-300:
                        self.assertLessEqual(abs(got - exact) / exact, 1e-10)
                    else:
                        self.assertLessEqual(abs(got - exact), 1e-12)

    def test_poisson_c_zero_and_huge_counts_are_finite_and_fast(self) -> None:
        self.assertEqual(stats.poisson_logsf(0, 0.08), 0.0)
        self.assertEqual(stats.poisson_logsf(0, 10 ** 9), 0.0)
        for c, lam, check in ((10 ** 9, 0.08, lambda sf: sf == 0.0), (10 ** 6, 10 ** 6, lambda sf: 0.49 < sf < 0.51),
                              (8 * 10 ** 9, 8 * 10 ** 9, lambda sf: 0.49 < sf < 0.51),
                              (8 * 10 ** 9 - 1, 8 * 10 ** 9, lambda sf: 0.49 < sf < 0.51),
                              (1, 10 ** 9, lambda sf: sf == 1.0)):
            with self.subTest(c=c, lam=lam):
                start = time.perf_counter()
                value = stats.poisson_logsf(c, lam)
                self.assertLess(time.perf_counter() - start, 1.0)
                self.assertTrue(math.isfinite(value))
                self.assertLessEqual(value, 0.0)
                self.assertTrue(check(math.exp(value)), math.exp(value))

    def test_poisson_documented_edge_value(self) -> None:
        # a fresh series: two '<k' window weeks (c = 2) against lambda_floor 0.01 over an 8-week window
        self.assertAlmostEqual(stats.poisson_sf(2, 0.08), 1 - math.exp(-0.08) * 1.08, delta=1e-15)
        self.assertLess(stats.poisson_sf(2, 0.08), 0.01)

    def test_poisson_invalid_input(self) -> None:
        for c in (-1, 1.0, True, "1", None, float("nan")):
            with self.subTest(c=c), self.assertRaises(ValueError):
                stats.poisson_logsf(c, 1.0)
        for lam in (0, 0.0, -1.0, *BAD_NUMBERS):
            with self.subTest(lam=lam), self.assertRaises(ValueError):
                stats.poisson_logsf(1, lam)

    def test_binomial_matches_exact_fraction_sums(self) -> None:
        rng = random.Random("g4-binomial")
        for case in range(120):
            n = rng.randrange(0, 31)
            p = rng.choice((rng.random(), rng.random() ** 8, 1 - rng.random() ** 8, 0.5, 1e-5))
            exact_p = Fraction(p)
            for m in range(-1, n + 2):
                with self.subTest(case=case, m=m):
                    exact = float(sum(Fraction(math.comb(n, j)) * exact_p ** j * (1 - exact_p) ** (n - j)
                                      for j in range(max(m, 0), n + 1)))
                    got = stats.binom_sf(m, n, p)
                    if exact == 0.0:
                        self.assertEqual(got, 0.0)
                    else:
                        self.assertLessEqual(abs(got - exact) / exact, 1e-12)

    def test_binomial_edges_and_invalid_input(self) -> None:
        self.assertEqual(stats.binom_logsf(0, 5, 0.3), 0.0)
        self.assertEqual(stats.binom_logsf(-2, 5, 0.3), 0.0)
        self.assertEqual(stats.binom_logsf(6, 5, 0.3), -math.inf)
        self.assertEqual(stats.binom_logsf(1, 5, 0.0), -math.inf)
        self.assertEqual(stats.binom_logsf(5, 5, 1.0), 0.0)
        self.assertEqual(stats.binom_logsf(0, 0, 0.5), 0.0)
        for args in ((1.0, 5, 0.3), (True, 5, 0.3), (1, -1, 0.3), (1, 5.0, 0.3), (1, False, 0.3), (1, 5, -0.1),
                     (1, 5, 1.1), *((1, 5, bad) for bad in BAD_NUMBERS)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                stats.binom_logsf(*args)


class PMITests(unittest.TestCase):
    def test_hand_values(self) -> None:
        self.assertEqual(stats.smoothed_pmi(10, 20, 25, 100, 0), math.log(2))
        self.assertEqual(stats.smoothed_pmi(10, 20, 25, 100, 0), 0.6931471805599453)
        self.assertEqual(stats.smoothed_pmi(10, 20, 25, 100, 0.5), 0.7024296463538652)
        self.assertEqual(stats.smoothed_pmi(0, 4, 6, 50, 1.0), 0.37647757123491205)

    def test_undefined_and_invalid_input(self) -> None:
        for args in ((0, 4, 6, 50, 0), (3, 0, 6, 50, 0), (3, 4, 0, 50, 0), (3, 4, 6, 0, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError) as cm:
                stats.smoothed_pmi(*args)
            self.assertEqual(str(cm.exception), "undefined")
        for i in range(5):
            for bad in (-1, -0.5, *BAD_NUMBERS):
                args = [10, 20, 25, 100, 0.5]
                args[i] = bad
                with self.subTest(position=i, bad=bad), self.assertRaises(ValueError):
                    stats.smoothed_pmi(*args)


class LogisticTests(unittest.TestCase):
    def test_values_and_symmetry(self) -> None:
        self.assertEqual(stats.logistic(0), 0.5)
        self.assertAlmostEqual(stats.logistic(2), 0.8807970779778823, delta=1e-15)
        self.assertEqual(stats.logistic(-800), 0.0)
        self.assertEqual(stats.logistic(800), 1.0)
        self.assertEqual(stats.logistic(-800.0), 0.0)
        for x in (0.1, 1, 2.5, 7, 13.25, 36):
            with self.subTest(x=x):
                self.assertAlmostEqual(stats.logistic(x) + stats.logistic(-x), 1.0, delta=1e-15)

    def test_invalid_input(self) -> None:
        for bad in BAD_NUMBERS:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                stats.logistic(bad)


# --------------------------------------------------------------------------------------------------- G5 ranking

T, F = True, False
HAND_RANKINGS = (
    # scores, relevant, n_relevant, AP, {k: p@k}
    ([.9, .8, .7, .6], [T, F, T, F], 2, Fraction(5, 6), {1: 1.0, 2: 0.5, 40: 0.05}),
    ([.5, .5, .5, .5], [T, F, T, F], 2, Fraction(49, 72), {1: 0.5, 3: 0.5, 40: 0.05}),
    ([.9, .5, .5, .5, .1], [F, T, F, T, T], 4, Fraction(2, 5), {2: 1 / 3, 4: 0.5, 40: 0.075}),
    ([.7, .7, .2], [F, F, F], 0, None, {1: 0.0, 40: 0.0}),
)


def _orderings(scores: list[float], relevant: list[bool]) -> list[list[bool]]:
    """Every ordering of the items by descending score, ties in every order (with duplicates for equal items, so each
    permutation of a tie group is equally likely)."""
    out = []
    for perm in itertools.permutations(range(len(scores))):
        if all(scores[perm[i]] >= scores[perm[i + 1]] for i in range(len(perm) - 1)):
            out.append([relevant[i] for i in perm])
    return out


def brute_ap(scores: list[float], relevant: list[bool], n_relevant: int) -> float:
    values = []
    for order in _orderings(scores, relevant):
        hits, total = 0, 0.0
        for i, rel in enumerate(order, start=1):
            if rel:
                hits += 1
                total += hits / i
        values.append(total / n_relevant)
    return sum(values) / len(values)


def brute_precision(scores: list[float], relevant: list[bool], k: int) -> float:
    orders = _orderings(scores, relevant)
    return sum(sum(order[:k]) / k for order in orders) / len(orders)


class TieAveragedRankingTests(unittest.TestCase):
    def test_the_four_hand_rankings(self) -> None:
        for scores, relevant, n_relevant, ap, at_k in HAND_RANKINGS:
            with self.subTest(scores=scores, relevant=relevant):
                got = stats.tie_averaged_ap(scores, relevant, n_relevant)
                if ap is None:
                    self.assertIsNone(got)
                else:
                    self.assertAlmostEqual(got, float(ap), delta=1e-12)
                for k, value in at_k.items():
                    self.assertAlmostEqual(stats.tie_averaged_precision_at_k(scores, relevant, k), value, delta=1e-12)

    def test_default_n_relevant_and_an_empty_ranking(self) -> None:
        self.assertAlmostEqual(stats.tie_averaged_ap([.9, .8, .7, .6], [T, F, T, F]), 5 / 6, delta=1e-12)
        self.assertIsNone(stats.tie_averaged_ap([], []))
        self.assertEqual(stats.tie_averaged_precision_at_k([], [], 40), 0.0)

    def test_equal_brute_force_over_every_ordering_of_the_ties(self) -> None:
        rng = random.Random("g5-ranking-brute-force")
        for case in range(50):
            n = rng.randint(1, 7)
            scores = [rng.choice((0.1, 0.4, 0.4, 0.7, 0.9)) for _ in range(n)]
            relevant = [rng.random() < 0.4 for _ in range(n)]
            n_relevant = sum(relevant) + rng.randint(0, 2)
            with self.subTest(case=case):
                got = stats.tie_averaged_ap(scores, relevant, n_relevant)
                if n_relevant == 0:
                    self.assertIsNone(got)
                else:
                    self.assertAlmostEqual(got, brute_ap(scores, relevant, n_relevant), delta=1e-12)
                for k in (1, 2, 3, n, 40):
                    self.assertAlmostEqual(stats.tie_averaged_precision_at_k(scores, relevant, k),
                                           brute_precision(scores, relevant, k), delta=1e-12)

    def test_relevant_items_never_ranked_lower_ap(self) -> None:
        scores, relevant = [.9, .5, .1], [T, F, T]
        full = stats.tie_averaged_ap(scores, relevant)
        for extra in (1, 2, 5):
            with self.subTest(extra=extra):
                self.assertAlmostEqual(stats.tie_averaged_ap(scores, relevant, 2 + extra), full * 2 / (2 + extra),
                                       delta=1e-12)

    def test_input_validation(self) -> None:
        for scores, relevant in (([.5], [T, F]), ([True], [T]), ([float("nan")], [T]), ([float("inf")], [T]),
                                 (["0.5"], [T]), ([.5], [1]), ([.5], ["yes"]), ((x for x in [.5]), [T])):
            with self.subTest(scores=scores, relevant=relevant):
                with self.assertRaises(ValueError):
                    stats.tie_averaged_ap(scores, relevant)
                with self.assertRaises(ValueError):
                    stats.tie_averaged_precision_at_k(scores, relevant, 1)
        for n_relevant in (0, 1, True, 2.0, "2"):
            with self.subTest(n_relevant=n_relevant), self.assertRaises(ValueError):
                stats.tie_averaged_ap([.9, .1], [T, T], n_relevant)
        for k in (0, -1, True, 1.0, "1"):
            with self.subTest(k=k), self.assertRaises(ValueError):
                stats.tie_averaged_precision_at_k([.9], [T], k)

    def test_errors_never_hold_a_value(self) -> None:
        with self.assertRaises(ValueError) as cm:
            stats.tie_averaged_ap([0.123456], ["marker-xyz"])
        self.assertNotIn("marker-xyz", str(cm.exception))
        self.assertNotIn("0.123456", str(cm.exception))


class ClusterBootstrapTests(unittest.TestCase):
    def test_reproducible_for_a_seed_and_different_for_another(self) -> None:
        clusters = [[1, 0, 1], [0], [1, 1], [0, 0, 1, 1], [1]]
        a = stats.cluster_bootstrap_mean(clusters, B=500, seed="x1:1:lift")
        self.assertEqual(a, stats.cluster_bootstrap_mean(clusters, B=500, seed="x1:1:lift"))
        b = stats.cluster_bootstrap_mean(clusters, B=500, seed="x1:2:lift")
        self.assertNotEqual((a["ci_low"], a["ci_high"]), (b["ci_low"], b["ci_high"]))
        self.assertEqual((a["B"], a["seed"], a["method"], a["n_clusters"], a["n_units"]),
                         (500, "x1:1:lift", "cluster percentile", 5, 11))

    def test_all_zero_clusters_give_zero(self) -> None:
        out = stats.cluster_bootstrap_mean([[0, 0], [0], [0, 0, 0]], B=1000, seed=3)
        self.assertEqual((out["mean"], out["ci_low"], out["ci_high"]), (0.0, 0.0, 0.0))

    def test_the_mean_is_the_pooled_mean(self) -> None:
        out = stats.cluster_bootstrap_mean([[1, 1, 1], [0]], B=10, seed=1)
        self.assertEqual(out["mean"], 0.75)
        self.assertNotEqual(out["mean"], (1.0 + 0.0) / 2)

    def test_clusters_are_resampled_not_units(self) -> None:
        out = stats.cluster_bootstrap_mean([[1] * 9, [0]], B=2000, seed=11)
        self.assertEqual(out["ci_low"], 0.0)          # a quarter of the draws are the lone zero cluster twice
        self.assertEqual(out["ci_high"], 1.0)
        units = stats.paired_bootstrap([1] * 9 + [0], [0] * 10, B=2000, seed=11)
        self.assertGreater(units["ci_low"], 0.0)       # resampling the ten units almost never gives all zeros

    def test_a_replicate_draws_whole_clusters(self) -> None:
        clusters = [[0.0], [1.0, 1.0, 1.0]]
        out = stats.cluster_bootstrap_mean(clusters, B=200, seed="whole")
        rng = random.Random("whole")
        reps = []
        for _ in range(200):
            drawn = [rng.randrange(2) for _ in range(2)]
            units = [v for i in drawn for v in clusters[i]]
            reps.append(sum(units) / len(units))
        self.assertEqual(out["ci_low"], stats.percentile(reps, 2.5))
        self.assertEqual(out["ci_high"], stats.percentile(reps, 97.5))

    def test_input_validation(self) -> None:
        for clusters in ([], [[]], [[1], []], [[True]], [[float("nan")]], [["1"]], [1, 2], "ab", None):
            with self.subTest(clusters=clusters), self.assertRaises(ValueError):
                stats.cluster_bootstrap_mean(clusters, B=10, seed=1)
        for kwargs in ({"B": 0, "seed": 1}, {"B": True, "seed": 1}, {"B": 1.0, "seed": 1}, {"B": 10, "seed": 1.5},
                       {"B": 10, "seed": True}, {"B": 10, "seed": 1, "alpha": 0}, {"B": 10, "seed": 1, "alpha": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                stats.cluster_bootstrap_mean([[1]], **kwargs)



class PairedRankingBootstrapTests(unittest.TestCase):
    """G6 (E2): AP and precision@k per condition, one shared resampling stream, and an AP ratio."""

    def test_hand_computed_point_estimates(self) -> None:
        out = stats.paired_ranking_bootstrap({"a": [3, 2, 1], "b": [1, 2, 3]}, [True, False, True], k=2, B=10, seed=1)
        self.assertEqual((out["n"], out["n_relevant"], out["B"], out["seed"], out["k"], out["method"]),
                         (3, 2, 10, 1, 2, "paired percentile"))
        a, b = out["conditions"]["a"], out["conditions"]["b"]
        self.assertAlmostEqual(a["ap"], (1 + 2 / 3) / 2)               # hits at ranks 1 and 3
        self.assertAlmostEqual(b["ap"], (1 + 2 / 3) / 2)
        self.assertEqual((a["precision_at_k"], b["precision_at_k"]), (0.5, 0.5))
        out = stats.paired_ranking_bootstrap({"a": [3, 2, 1]}, [False, True, True], k=1, B=10, seed=1)
        self.assertAlmostEqual(out["conditions"]["a"]["ap"], (1 / 2 + 2 / 3) / 2)
        self.assertEqual(out["conditions"]["a"]["precision_at_k"], 0.0)
        tied = stats.paired_ranking_bootstrap({"a": [1, 1, 0]}, [True, False, False], k=1, B=10, seed=1)
        self.assertEqual(tied["conditions"]["a"]["ap"], stats.tie_averaged_ap([1, 1, 0], [True, False, False]))
        self.assertEqual(tied["conditions"]["a"]["precision_at_k"], 0.5)          # tie-averaged over the tie
        self.assertIsNone(out["ratio"])

    def test_identical_conditions_give_a_ratio_of_one_with_a_degenerate_interval(self) -> None:
        rng = random.Random(4)
        scores = [rng.random() for _ in range(30)]
        relevant = [rng.random() < 0.3 for _ in range(30)]
        out = stats.paired_ranking_bootstrap({"x": scores, "y": list(scores)}, relevant, k=5, B=300, seed=2,
                                             ratio=("x", "y"))
        self.assertEqual({k: out["ratio"][k] for k in ("numerator", "denominator", "estimate", "ci_low", "ci_high")},
                         {"numerator": "x", "denominator": "y", "estimate": 1.0, "ci_low": 1.0, "ci_high": 1.0})
        self.assertEqual(out["conditions"]["x"], out["conditions"]["y"])
        self.assertEqual(out["ratio"]["undefined"], out["conditions"]["x"]["ap_undefined"])

    def test_the_replicates_are_paired_draws_of_items(self) -> None:
        scores = {"a": [0.9, 0.8, 0.3, 0.2, 0.1], "b": [0.1, 0.7, 0.8, 0.9, 0.2]}
        relevant = [True, False, True, False, False]
        out = stats.paired_ranking_bootstrap(scores, relevant, k=2, B=200, seed="e2", ratio=("a", "b"))
        rng = random.Random("e2")
        reps: dict[str, list[float]] = {"a": [], "b": []}
        p_reps: dict[str, list[float]] = {"a": [], "b": []}
        ratios: list[float] = []
        undefined = 0
        for _ in range(200):
            drawn = [rng.randrange(5) for _ in range(5)]
            marks = [relevant[i] for i in drawn]
            for name in ("a", "b"):
                p_reps[name].append(stats.tie_averaged_precision_at_k([scores[name][i] for i in drawn], marks, 2))
            if not any(marks):
                undefined += 1
                continue
            aps = {name: stats.tie_averaged_ap([scores[name][i] for i in drawn], marks) for name in ("a", "b")}
            for name in ("a", "b"):
                reps[name].append(aps[name])
            if aps["b"] >= 0.01:
                ratios.append(aps["a"] / aps["b"])
        self.assertGreater(undefined, 0)
        for name in ("a", "b"):
            c = out["conditions"][name]
            self.assertEqual((c["ap_ci_low"], c["ap_ci_high"], c["ap_undefined"]),
                             (stats.percentile(reps[name], 2.5), stats.percentile(reps[name], 97.5), undefined))
            self.assertEqual((c["p_ci_low"], c["p_ci_high"]),
                             (stats.percentile(p_reps[name], 2.5), stats.percentile(p_reps[name], 97.5)))
        self.assertEqual((out["ratio"]["ci_low"], out["ratio"]["ci_high"], out["ratio"]["undefined"]),
                         (stats.percentile(ratios, 2.5), stats.percentile(ratios, 97.5), 200 - len(ratios)))
        self.assertEqual(out["ratio"]["estimate"], out["conditions"]["a"]["ap"] / out["conditions"]["b"]["ap"])

    def test_a_denominator_below_epsilon_gives_no_ratio(self) -> None:
        n = 200
        relevant = [False] * (n - 1) + [True]
        good, bad = [float(i == n - 1) for i in range(n)], [float(n - i) for i in range(n)]
        out = stats.paired_ranking_bootstrap({"good": good, "bad": bad}, relevant, k=10, B=50, seed=3,
                                             ratio=("good", "bad"))
        self.assertEqual(out["conditions"]["good"]["ap"], 1.0)
        self.assertAlmostEqual(out["conditions"]["bad"]["ap"], 1 / n)
        self.assertIsNone(out["ratio"]["estimate"])
        self.assertEqual(out["ratio"]["epsilon"], 0.01)
        # a replicate's ratio is undefined when it drew no relevant item or its denominator AP is below epsilon
        rng, undefined = random.Random(3), 0
        for _ in range(50):
            drawn = [rng.randrange(n) for _ in range(n)]
            marks = [relevant[i] for i in drawn]
            den = stats.tie_averaged_ap([bad[i] for i in drawn], marks) if any(marks) else None
            undefined += int(den is None or den < 0.01)
        self.assertGreater(undefined, 0)
        self.assertEqual(out["ratio"]["undefined"], undefined)
        looser = stats.paired_ranking_bootstrap({"good": good, "bad": bad}, relevant, k=10, B=50, seed=3,
                                                ratio=("good", "bad"), epsilon=0.001)
        self.assertAlmostEqual(looser["ratio"]["estimate"], float(n))

    def test_zero_positives_leave_every_ap_and_the_ratio_undefined(self) -> None:
        out = stats.paired_ranking_bootstrap({"a": [1, 2, 3], "b": [3, 2, 1]}, [False] * 3, k=2, B=40, seed=1,
                                             ratio=("a", "b"))
        self.assertEqual(out["n_relevant"], 0)
        for name in ("a", "b"):
            self.assertEqual(out["conditions"][name], {"ap": None, "ap_ci_low": None, "ap_ci_high": None,
                                                       "ap_undefined": 40, "precision_at_k": 0.0, "p_ci_low": 0.0,
                                                       "p_ci_high": 0.0})
        self.assertEqual((out["ratio"]["estimate"], out["ratio"]["ci_low"], out["ratio"]["undefined"]),
                         (None, None, 40))

    def test_reproducible_for_a_seed_and_different_for_another(self) -> None:
        rng = random.Random(9)
        scores = {"a": [rng.random() for _ in range(40)], "b": [rng.random() for _ in range(40)]}
        relevant = [rng.random() < 0.4 for _ in range(40)]
        one = stats.paired_ranking_bootstrap(scores, relevant, k=10, B=200, seed=1, ratio=("a", "b"))
        self.assertEqual(one, stats.paired_ranking_bootstrap(scores, relevant, k=10, B=200, seed=1, ratio=("a", "b")))
        two = stats.paired_ranking_bootstrap(scores, relevant, k=10, B=200, seed=2, ratio=("a", "b"))
        self.assertNotEqual((one["ratio"]["ci_low"], one["ratio"]["ci_high"]),
                            (two["ratio"]["ci_low"], two["ratio"]["ci_high"]))
        self.assertEqual(one["conditions"]["a"]["ap"], two["conditions"]["a"]["ap"])

    def test_a_constant_numerator_keeps_none_of_the_lift_over_chance(self) -> None:
        # regression (E2): on a pool that is mostly relevant a constant ranking's AP is near the prevalence, so the
        # plain AP ratio can clear a bar; the chance-corrected lift ratio of a constant ranking is 0
        rng = random.Random(5)
        relevant = [rng.random() < 0.8 for _ in range(200)]
        central = [(2.0 if r else 1.0) + rng.random() * 1.5 for r in relevant]
        out = stats.paired_ranking_bootstrap({"constant": [0.5] * 200, "central": central}, relevant, k=40, B=300,
                                             seed=1, ratio=("constant", "central"))
        chance = stats.tie_averaged_ap([0.0] * 200, relevant)
        self.assertEqual(out["chance"]["ap"], chance)
        self.assertEqual(out["chance"]["prevalence"], sum(relevant) / 200)
        self.assertEqual(out["conditions"]["constant"]["ap"], chance)
        self.assertGreater(out["ratio"]["estimate"], 0.8)                    # the plain ratio looks good
        self.assertEqual(out["ratio"]["lift_estimate"], 0.0)
        self.assertAlmostEqual(out["ratio"]["denominator_lift"], out["conditions"]["central"]["ap"] - chance)
        self.assertLess(out["ratio"]["lift_ci_high"], 0.05)
        self.assertGreater(out["ratio"]["denominator_lift_ci_low"], 0.0)
        self.assertEqual(out["ratio"]["lift_undefined"], 0)

    def test_the_lift_ratio_by_hand_and_its_epsilon(self) -> None:
        scores = {"a": [0.9, 0.8, 0.3, 0.2, 0.1], "b": [0.8, 0.1, 0.9, 0.2, 0.3]}
        relevant = [True, False, True, False, False]
        out = stats.paired_ranking_bootstrap(scores, relevant, k=2, B=50, seed=1, ratio=("a", "b"))
        chance = stats.tie_averaged_ap([0.0] * 5, relevant)
        ap_a, ap_b = out["conditions"]["a"]["ap"], out["conditions"]["b"]["ap"]
        self.assertEqual((ap_a, ap_b), ((1 + 2 / 3) / 2, 1.0))
        self.assertEqual(out["ratio"]["lift_estimate"], (ap_a - chance) / (ap_b - chance))
        # a denominator below chance leaves the lift undefined too
        below = stats.paired_ranking_bootstrap({"a": scores["a"], "b": [0.1, 0.7, 0.0, 0.9, 0.2]}, relevant, k=2,
                                               B=50, seed=1, ratio=("a", "b"))
        self.assertLess(below["conditions"]["b"]["ap"], chance)
        self.assertIsNone(below["ratio"]["lift_estimate"])
        # a denominator no better than chance (within epsilon) leaves the lift undefined
        flat = stats.paired_ranking_bootstrap({"a": scores["a"], "b": [0.0] * 5}, relevant, k=2, B=50, seed=1,
                                              ratio=("a", "b"))
        self.assertIsNone(flat["ratio"]["lift_estimate"])
        self.assertEqual(flat["ratio"]["denominator_lift"], 0.0)
        self.assertEqual(flat["ratio"]["lift_undefined"], 50)

    def test_cluster_replicates_draw_whole_labels(self) -> None:
        scores = {"a": [0.9, 0.8, 0.3, 0.2, 0.1, 0.4], "b": [0.1, 0.7, 0.8, 0.9, 0.2, 0.3]}
        relevant = [True, False, True, False, False, True]
        clusters = ["k2", "k1", "k2", "k3", "k1", "k3"]
        out = stats.paired_ranking_bootstrap(scores, relevant, k=2, B=100, seed="c", ratio=("a", "b"),
                                             clusters=clusters)
        self.assertEqual((out["method"], out["n_clusters"]), ("paired cluster percentile", 3))
        members = {"k1": [1, 4], "k2": [0, 2], "k3": [3, 5]}
        labels = sorted(members)
        rng = random.Random("c")
        reps = []
        for _ in range(100):
            drawn = [i for _ in range(3) for i in members[labels[rng.randrange(3)]]]
            marks = [relevant[i] for i in drawn]
            if any(marks):
                reps.append(stats.tie_averaged_ap([scores["a"][i] for i in drawn], marks))
        self.assertEqual((out["conditions"]["a"]["ap_ci_low"], out["conditions"]["a"]["ap_ci_high"]),
                         (stats.percentile(reps, 2.5), stats.percentile(reps, 97.5)))
        plain = stats.paired_ranking_bootstrap(scores, relevant, k=2, B=100, seed="c", ratio=("a", "b"))
        self.assertEqual((plain["method"], plain["n_clusters"]), ("paired percentile", None))
        self.assertEqual(plain["conditions"]["a"]["ap"], out["conditions"]["a"]["ap"])

    def test_input_validation(self) -> None:
        good = {"a": [1.0, 2.0]}
        cases = [
            ({}, [True, False], {}), ({1: [1.0, 2.0]}, [True, False], {}), ([[1.0, 2.0]], [True, False], {}),
            (good, [1, 0], {}), (good, [], {}), (good, (True,), {}), ({"a": [1.0]}, [True, False], {}),
            ({"a": [1.0, float("nan")]}, [True, False], {}), ({"a": [1.0, float("inf")]}, [True, False], {}),
            ({"a": [1.0, True]}, [True, False], {}), ({"a": [1.0, "2"]}, [True, False], {}),
            ({"a": (1.0, 2.0), "b": [1.0]}, [True, False], {}),
            (good, [True, False], {"k": 0}), (good, [True, False], {"k": True}), (good, [True, False], {"k": 1.5}),
            (good, [True, False], {"B": 0}), (good, [True, False], {"B": True}),
            (good, [True, False], {"alpha": 0}), (good, [True, False], {"alpha": 1}),
            (good, [True, False], {"seed": 1.5}), (good, [True, False], {"seed": True}),
            (good, [True, False], {"ratio": ("a", "z")}), (good, [True, False], {"ratio": ("a",)}),
            (good, [True, False], {"epsilon": 0}), (good, [True, False], {"epsilon": -1}),
            (good, [True, False], {"epsilon": float("nan")}), (good, [True, False], {"epsilon": True}),
            (good, [True, False], {"clusters": ["x"]}), (good, [True, False], {"clusters": ["x", 1]}),
            (good, [True, False], {"clusters": "xy"}),
        ]
        for scores, relevant, kwargs in cases:
            args = {"k": 1, "B": 5, "seed": 1, **kwargs}
            with self.subTest(scores=scores, relevant=relevant, kwargs=kwargs), self.assertRaises(ValueError):
                stats.paired_ranking_bootstrap(scores, relevant, **args)
        self.assertEqual(stats.paired_ranking_bootstrap(good, (True, False), k=1, B=5, seed=1)["n"], 2)


if __name__ == "__main__":
    unittest.main()
