"""Integrity tests for the LoCoMo improvement-loop splits.

These are the tests that make a reported gain falsifiable. If any of them can
be made to pass by adjusting the split logic, the loop's results mean nothing,
so each one asserts a property that a convenient bug would violate.

No API key, no network, no model.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from evaluation.improvement_loop.corpus import (
    CORPUS_PATH,
    SPLIT_SEED,
    Splits,
    corpus_digest,
    cut_splits,
    load_corpus,
    load_splits,
    save_splits,
    split_summary,
    verify_splits,
)


class SplitIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = load_corpus()
        cls.splits = cut_splits(cls.records)

    def test_splits_partition_the_corpus_exactly(self):
        dev = set(self.splits.development)
        val = set(self.splits.validation)
        test = set(self.splits.locked_test)
        self.assertEqual(dev | val | test, {int(r["id"]) for r in self.records})
        self.assertEqual(len(dev) + len(val) + len(test), len(self.records))

    def test_no_question_appears_in_two_splits(self):
        dev = set(self.splits.development)
        val = set(self.splits.validation)
        test = set(self.splits.locked_test)
        self.assertEqual(dev & val, set())
        self.assertEqual(dev & test, set())
        self.assertEqual(val & test, set())

    def test_split_sizes_are_close_to_forty_twenty_forty(self):
        n = len(self.records)
        for ids, want in (
            (self.splits.development, 0.40),
            (self.splits.validation, 0.20),
            (self.splits.locked_test, 0.40),
        ):
            self.assertAlmostEqual(len(ids) / n, want, delta=0.01)

    def test_cutting_twice_gives_identical_splits(self):
        again = cut_splits(self.records)
        self.assertEqual(self.splits.development, again.development)
        self.assertEqual(self.splits.validation, again.validation)
        self.assertEqual(self.splits.locked_test, again.locked_test)

    def test_every_category_is_represented_in_every_split(self):
        summary = split_summary(self.splits, self.records)
        for name in ("development", "validation", "locked_test"):
            for category in ("single_hop", "multi_hop", "temporal", "open_domain"):
                self.assertGreater(
                    summary[name]["by_category"].get(category, 0), 0,
                    f"{category} missing from {name}",
                )

    def test_every_conversation_is_represented_in_every_split(self):
        """A conversation confined to one split would make a per-conversation
        effect indistinguishable from a real fix."""
        summary = split_summary(self.splits, self.records)
        for name in ("development", "validation", "locked_test"):
            self.assertEqual(summary[name]["conversations"], 10)

    def test_category_proportions_are_preserved_within_tolerance(self):
        summary = split_summary(self.splits, self.records)
        overall = {}
        for record in self.records:
            overall[str(record["category"])] = overall.get(str(record["category"]), 0) + 1
        for name in ("development", "validation", "locked_test"):
            n = summary[name]["n"]
            for category, total in overall.items():
                got = summary[name]["by_category"].get(category, 0) / n
                want = total / len(self.records)
                self.assertAlmostEqual(got, want, delta=0.03, msg=f"{name}/{category}")


class DigestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = load_corpus()

    def test_digest_is_stable_across_record_order(self):
        shuffled = list(reversed(self.records))
        self.assertEqual(corpus_digest(self.records), corpus_digest(shuffled))

    def test_digest_ignores_answers_and_verdicts(self):
        """The digest proves *membership* stability. If it moved when a model
        produced different output, it could not be used to prove that the same
        questions were asked across rounds."""
        mutated = [dict(r) for r in self.records]
        mutated[0]["generated_answer"] = "ZZQX_DIFFERENT"
        mutated[0]["correct"] = not mutated[0].get("correct")
        mutated[1]["gold_answer"] = "ZZQX_ALSO_DIFFERENT"
        self.assertEqual(corpus_digest(self.records), corpus_digest(mutated))

    def test_digest_changes_when_a_question_changes(self):
        mutated = [dict(r) for r in self.records]
        mutated[0]["question"] = "ZZQX_A_DIFFERENT_QUESTION"
        self.assertNotEqual(corpus_digest(self.records), corpus_digest(mutated))

    def test_digest_changes_when_a_question_is_dropped(self):
        self.assertNotEqual(corpus_digest(self.records), corpus_digest(self.records[:-1]))


class VerificationRejectsTamperingTests(unittest.TestCase):
    """`verify_splits` is the gate. Each test corrupts one property and
    asserts the gate refuses, rather than silently repairing."""

    @classmethod
    def setUpClass(cls):
        cls.records = load_corpus()
        cls.splits = cut_splits(cls.records)

    def test_leaked_test_id_into_development_is_rejected(self):
        leaked = Splits(
            seed=self.splits.seed,
            corpus_digest=self.splits.corpus_digest,
            corpus_size=self.splits.corpus_size,
            development=self.splits.development + (self.splits.locked_test[0],),
            validation=self.splits.validation,
            locked_test=self.splits.locked_test,
        )
        with self.assertRaises(ValueError) as ctx:
            verify_splits(leaked, self.records)
        self.assertIn("LEAKAGE", str(ctx.exception))

    def test_missing_question_is_rejected(self):
        truncated = Splits(
            seed=self.splits.seed,
            corpus_digest=self.splits.corpus_digest,
            corpus_size=self.splits.corpus_size,
            development=self.splits.development[:-1],
            validation=self.splits.validation,
            locked_test=self.splits.locked_test,
        )
        with self.assertRaises(ValueError) as ctx:
            verify_splits(truncated, self.records)
        self.assertIn("partition", str(ctx.exception))

    def test_changed_benchmark_membership_is_rejected(self):
        mutated = [dict(r) for r in self.records]
        mutated[0]["question"] = "ZZQX_SWAPPED_QUESTION"
        with self.assertRaises(ValueError) as ctx:
            verify_splits(self.splits, mutated)
        self.assertIn("MEMBERSHIP CHANGED", str(ctx.exception))


class RoundTripTests(unittest.TestCase):
    def test_saved_splits_reload_identically(self):
        import tempfile

        records = load_corpus()
        splits = cut_splits(records)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "splits.json"
            save_splits(splits, path)
            back = load_splits(path)
        self.assertEqual(splits, back)
        verify_splits(back, records)

    def test_seed_is_recorded_in_the_artifact(self):
        self.assertEqual(cut_splits(load_corpus()).seed, SPLIT_SEED)


if __name__ == "__main__":
    unittest.main()
