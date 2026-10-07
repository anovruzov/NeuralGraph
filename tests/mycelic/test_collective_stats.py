"""The statistics behind every harness figure, checked against independent computations."""
from __future__ import annotations

import itertools
import math
import unittest

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


if __name__ == "__main__":
    unittest.main()
