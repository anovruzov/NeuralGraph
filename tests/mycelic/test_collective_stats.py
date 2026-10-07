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


if __name__ == "__main__":
    unittest.main()
