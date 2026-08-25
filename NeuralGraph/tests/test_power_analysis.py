"""Deterministic tests for the paired-outcome power analysis.

The power analysis decides whether the 60-question set can adjudicate anything
at all, so a bug in it would invalidate every accept/reject decision made with
it. These tests pin the properties a correct implementation must have, and each
one fails if the obvious mistake is made.
"""

from __future__ import annotations

import unittest

from evaluation.improvement_loop.power import (
    JUDGE_FLIP,
    mcnemar_exact,
    minimum_detectable,
    power,
    required_n,
)


class McNemarTests(unittest.TestCase):
    def test_no_discordant_pairs_is_never_significant(self):
        self.assertEqual(mcnemar_exact(0, 0), 1.0)

    def test_symmetric_discordance_is_never_significant(self):
        for k in (1, 5, 20):
            self.assertEqual(mcnemar_exact(k, k), 1.0)

    def test_is_symmetric_in_its_arguments(self):
        for a, b in ((3, 0), (7, 2), (10, 4)):
            self.assertAlmostEqual(mcnemar_exact(a, b), mcnemar_exact(b, a))

    def test_p_falls_as_the_split_becomes_more_lopsided(self):
        self.assertGreater(mcnemar_exact(5, 4), mcnemar_exact(8, 1))

    def test_six_one_way_pairs_is_the_exact_threshold(self):
        """With a perfect judge a pure-gain candidate produces zero regressions,
        so significance depends only on the count. Below six, an exact two-sided
        test cannot reach 0.05 no matter how large n is -- which is why the
        perfect-judge power curve is flat at zero for small gains."""
        self.assertGreater(mcnemar_exact(5, 0), 0.05)
        self.assertLess(mcnemar_exact(6, 0), 0.05)


class PowerTests(unittest.TestCase):
    def test_is_deterministic_under_a_fixed_seed(self):
        a = power(5, iterations=400, seed=7)
        b = power(5, iterations=400, seed=7)
        self.assertEqual(a["power"], b["power"])
        self.assertEqual(a["observed_delta_ci95"], b["observed_delta_ci95"])

    def test_power_increases_with_true_gain(self):
        curve = [power(g, iterations=600)["power"] for g in (2, 5, 9)]
        self.assertLess(curve[0], curve[1])
        self.assertLess(curve[1], curve[2])

    def test_zero_true_gain_holds_near_the_false_positive_rate(self):
        """A null candidate must not look significant more often than alpha."""
        self.assertLessEqual(power(0, iterations=1500)["power"], 0.05)

    def test_observed_delta_tracks_the_true_gain(self):
        small = power(2, iterations=600)["mean_observed_delta"]
        large = power(9, iterations=600)["mean_observed_delta"]
        self.assertLess(small, large)

    def test_judge_noise_widens_the_observed_interval(self):
        clean = power(5, flip=0.0, iterations=600)
        noisy = power(5, flip=0.20, iterations=600)
        width = lambda r: r["observed_delta_ci95"][1] - r["observed_delta_ci95"][0]  # noqa: E731
        self.assertGreater(width(noisy), width(clean))

    def test_larger_n_gives_more_power_at_a_fixed_effect_size(self):
        """Effect size is held constant in points, so the gain scales with n."""
        small = power(5, n=60, iterations=600)["power"]
        large = power(25, n=300, iterations=600)["power"]
        self.assertGreater(large, small)


class RequirementTests(unittest.TestCase):
    def test_minimum_detectable_gain_is_reported_for_n60(self):
        mde = minimum_detectable(iterations=600)
        self.assertIsNotNone(mde)
        self.assertGreater(mde, 5, "an MDE this small would contradict the power curve")

    def test_judge_noise_raises_the_required_sample_size(self):
        clean = required_n(8.0, flip=0.0, iterations=400)
        noisy = required_n(8.0, flip=JUDGE_FLIP, iterations=400)
        self.assertIsNotNone(clean)
        self.assertIsNotNone(noisy)
        self.assertGreater(noisy, clean)

    def test_smaller_effects_need_larger_samples(self):
        big = required_n(10.0, iterations=400)
        small = required_n(5.0, iterations=400)
        self.assertGreater(small, big)


if __name__ == "__main__":
    unittest.main()
