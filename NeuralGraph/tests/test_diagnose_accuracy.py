"""Tests for the retrieval-vs-generation failure diagnostic.

The diagnostic's conclusion — that single-hop failure is a generation problem
rather than a retrieval one — is only as trustworthy as the evidence proxy
underneath it. These tests pin the proxy's properties, not just its outputs.

The most important one is `test_lenient_is_a_superset_of_strict`. That
implication was violated in the first version: "lenient" used exact token
matching while "strict" used substring, so "sunset" failed to match "sunsets"
and the lenient measure was actually *stricter*. It inverted the two measures
and pushed the headline toward "retrieval failure", which was the wrong
conclusion. It is asserted over the whole real corpus, not a toy example.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from evaluation.diagnose_accuracy import (
    PARTIAL_THRESHOLD,
    _found,
    answer_items,
    calibrate,
    classify,
    content_tokens,
    diagnose,
    evidence_present,
    failure_modes,
    normalise,
    token_recall,
)

ARTIFACT = Path(__file__).resolve().parents[2] / "demo" / "maximal.json"


def _records():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))["results"]


class NormalisationTests(unittest.TestCase):
    def test_month_abbreviations_and_ordinals_are_normalised(self):
        """Date format varies between gold answers and stored memories."""
        self.assertEqual(normalise("7th May 2023"), "7 may 2023")
        self.assertEqual(normalise("May 7, 2023"), "may 7 2023")
        self.assertEqual(normalise("Sept 1st"), "september 1")

    def test_stopwords_are_dropped_from_content_tokens(self):
        self.assertEqual(content_tokens("the gym and the park"), ["gym", "park"])

    def test_answer_items_splits_lists_but_not_single_answers(self):
        self.assertEqual(len(answer_items("bowls, cup")), 2)
        self.assertEqual(len(answer_items("Pride parade, school speech, support group")), 3)
        self.assertEqual(len(answer_items("sunset")), 1)


class ProxyPropertyTests(unittest.TestCase):
    """Properties the measures must satisfy, checked over the real corpus."""

    @classmethod
    def setUpClass(cls):
        cls.records = _records()

    def test_lenient_is_a_superset_of_strict(self):
        """If the whole gold string is present, every token in it is present.

        Violated by the original implementation; the regression it caused
        inverted the diagnosis, so this is checked on every record.
        """
        for record in self.records:
            strict, lenient = evidence_present(record)
            if strict:
                with self.subTest(question=str(record.get("question"))[:60]):
                    self.assertTrue(lenient, "strict matched but lenient did not")

    def test_partial_is_a_superset_of_lenient(self):
        """Requiring half the tokens cannot be stricter than requiring all of them."""
        for record in self.records:
            if _found(record, "lenient"):
                with self.subTest(question=str(record.get("question"))[:60]):
                    self.assertTrue(_found(record, "partial"))

    def test_token_recall_is_a_fraction(self):
        for record in self.records:
            value = token_recall(record)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_full_token_recall_implies_lenient_match(self):
        for record in self.records:
            if token_recall(record) == 1.0 and content_tokens(str(record.get("gold_answer") or "")):
                with self.subTest(question=str(record.get("question"))[:60]):
                    self.assertTrue(_found(record, "lenient"))

    def test_missing_or_empty_fields_do_not_raise(self):
        for record in ({}, {"gold_answer": ""}, {"gold_answer": "x"},
                       {"gold_answer": "x", "retrieved_memories": []},
                       {"gold_answer": "x", "retrieved_memories": ["plain string"]}):
            with self.subTest(record=record):
                self.assertIn(classify(record, "partial"),
                              ("healthy", "generation_failure",
                               "retrieval_failure", "answered_without_visible_evidence"))


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = _records()
        cls.calibration = calibrate(cls.records, "partial")

    def test_the_proxy_is_reliable_on_conversational_categories(self):
        """Detection rate on correct answers is the proxy's true-positive rate.

        If this drops, the correction stops being an adjustment and becomes an
        extrapolation, and the conclusion should no longer be quoted.
        """
        for category in ("single_hop", "multi_hop", "temporal"):
            with self.subTest(category=category):
                self.assertGreater(
                    self.calibration[category]["detection_rate_on_correct"], 75.0
                )

    def test_open_domain_calibration_is_explicitly_unreliable(self):
        """Its answers are not in the conversation, so the proxy cannot see them.

        Pinned so nobody quotes the open-domain row as if it were sound.
        """
        self.assertLess(
            self.calibration["open_domain"]["detection_rate_on_correct"], 60.0
        )

    def test_estimated_evidence_never_exceeds_the_number_of_failures(self):
        for category, row in self.calibration.items():
            with self.subTest(category=category):
                self.assertLessEqual(row["estimated_evidence_on_wrong"], row["wrong"])

    def test_shares_sum_to_one_hundred(self):
        for category, row in self.calibration.items():
            with self.subTest(category=category):
                total = (row["estimated_generation_share_of_failures"]
                         + row["estimated_retrieval_share_of_failures"])
                self.assertAlmostEqual(total, 100.0, places=1)


class HeadlineDiagnosisTests(unittest.TestCase):
    """The conclusions this work exists to support."""

    @classmethod
    def setUpClass(cls):
        cls.records = _records()
        cls.calibration = calibrate(cls.records, "partial")
        cls.single_hop = failure_modes(cls.records, "single_hop")

    def test_single_hop_failure_is_predominantly_a_generation_failure(self):
        """The central finding: the evidence was there and the answer was wrong."""
        share = self.calibration["single_hop"]["estimated_generation_share_of_failures"]
        self.assertGreater(share, 60.0, "single-hop is no longer generation-dominated")

    def test_single_hop_accuracy_is_the_documented_fifty_percent(self):
        report = diagnose(self.records, "partial")
        self.assertAlmostEqual(report["by_category"]["single_hop"]["accuracy"], 52.1, delta=1.0)

    def test_single_hop_is_mostly_list_questions(self):
        self.assertGreater(self.single_hop["list_valued_share"], 60.0)

    def test_generation_failures_omit_gold_items_rather_than_over_including(self):
        """The shape of the failure, which is what makes it fixable.

        Answers are wrong *sets*, not wrong facts: nearly every failure drops at
        least one gold item, and essentially none is complete-but-over-inclusive.
        """
        modes = self.single_hop
        self.assertGreater(modes["omits_a_gold_item"], 0.9 * modes["generation_failures"])
        self.assertLess(modes["complete_but_judged_wrong"], 0.05 * modes["generation_failures"])

    def test_a_large_minority_of_failures_answer_unrelated_content(self):
        """A selection failure, distinct from incomplete coverage, needing a different fix."""
        modes = self.single_hop
        self.assertGreater(modes["no_overlap_with_gold"], 0.3 * modes["generation_failures"])

    def test_single_hop_is_worse_than_multi_hop(self):
        """Counterintuitive and load-bearing: the easy category is the weak one."""
        report = diagnose(self.records, "partial")
        self.assertLess(
            report["by_category"]["single_hop"]["accuracy"],
            report["by_category"]["multi_hop"]["accuracy"],
        )


if __name__ == "__main__":
    unittest.main()
