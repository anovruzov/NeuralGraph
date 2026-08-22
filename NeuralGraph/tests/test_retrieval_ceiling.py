"""Tests for the retrieval-ceiling split.

This module exists to correct an assumption: that all 186 questions whose
evidence was not retrieved are recoverable by better retrieval. A quarter are
not, because the answer is nowhere in the source conversation. Counting those
as recoverable inflates every projection built on top of them, so the split is
pinned here.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from evaluation.retrieval_ceiling import (
    analyse,
    answerable,
    classify,
    conversation_corpus,
)

ARTIFACT = Path(__file__).resolve().parents[2] / "demo" / "maximal.json"


def _records():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))["results"]


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = conversation_corpus()

    def test_every_conversation_is_loaded(self):
        records = _records()
        seen = {r["conversation"] for r in records}
        self.assertEqual(set(self.corpus), seen)

    def test_conversations_are_substantial(self):
        """A truncated corpus would make everything look unanswerable."""
        for cid, text in self.corpus.items():
            with self.subTest(conversation=cid):
                self.assertGreater(len(text.split()), 2000)

    def test_corpus_alignment_is_not_off_by_one(self):
        """Conversation N must map to the sample whose speakers appear in it.

        An off-by-one here would silently mark most answers unanswerable, which
        would look like a dramatic finding rather than an indexing bug.
        """
        records = _records()
        for cid in (1, 2, 3):
            speakers = {
                m.get("speaker")
                for r in records if r["conversation"] == cid
                for m in r["retrieved_memories"][:20]
            }
            speakers.discard(None)
            text = self.corpus[cid]
            for speaker in list(speakers)[:2]:
                with self.subTest(conversation=cid, speaker=speaker):
                    self.assertIn(str(speaker).lower(), text)


class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = _records()
        cls.corpus = conversation_corpus()
        cls.report = analyse(cls.records, cls.corpus)

    def test_labels_partition_the_failures(self):
        failures = self.report["failures"]
        wrong = sum(1 for r in self.records if not r.get("correct"))
        self.assertEqual(sum(failures.values()), wrong)

    def test_a_quarter_of_retrieval_failures_are_unanswerable(self):
        """The finding: not every retrieval miss is recoverable."""
        failures = self.report["failures"]
        no_evidence = failures["missed"] + failures["unanswerable"]
        share = failures["unanswerable"] / no_evidence
        self.assertGreater(share, 0.15)
        self.assertLess(share, 0.40)

    def test_recoverable_bucket_is_smaller_than_the_naive_count(self):
        """186 was the naive figure; the recoverable subset is materially less."""
        failures = self.report["failures"]
        self.assertLess(
            self.report["recoverable_by_retrieval"],
            failures["missed"] + failures["unanswerable"],
        )

    def test_open_domain_dominates_the_unanswerable_share(self):
        """Sanity check: those questions are not about the conversation.

        If some other category had the highest unanswerable rate, the measure
        would more likely be broken than revealing something.
        """
        by_category = self.report["failures_by_category"]
        rates = {}
        for category, counts in by_category.items():
            total = sum(counts.values())
            if total:
                rates[category] = counts.get("unanswerable", 0) / total
        self.assertEqual(max(rates, key=rates.get), "open_domain")

    def test_hard_ceiling_is_below_one_hundred_and_above_the_target(self):
        """85% must remain reachable, but perfection must not be claimed."""
        ceiling = self.report["hard_ceiling_accuracy"]
        self.assertLess(ceiling, 100.0)
        self.assertGreater(ceiling, 85.0)

    def test_answerable_is_a_lower_bound_not_an_exact_measure(self):
        """Some correct answers have no corpus support — derived, or judge leniency.

        Pinned because it is the direct evidence that `answerable` under-counts:
        a question answered correctly while scoring unanswerable proves the
        proxy misses derivable answers, so the retrieval prize is at least as
        large as reported, never smaller.
        """
        self.assertGreater(self.report["answered_correctly_without_corpus_support"], 0)


class LabelSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.corpus = {1: "melanie painted a sunset last weekend at the lake"}

    def test_retrieved_wins_over_corpus_presence(self):
        record = {
            "conversation": 1, "gold_answer": "sunset", "correct": False,
            "retrieved_memories": [{"text": "melanie painted a sunset"}],
        }
        self.assertEqual(classify(record, self.corpus), "retrieved")

    def test_in_corpus_but_not_retrieved_is_a_miss(self):
        record = {
            "conversation": 1, "gold_answer": "sunset", "correct": False,
            "retrieved_memories": [{"text": "something entirely unrelated"}],
        }
        self.assertEqual(classify(record, self.corpus), "missed")

    def test_absent_from_corpus_is_unanswerable(self):
        record = {
            "conversation": 1, "gold_answer": "quantum chromodynamics", "correct": False,
            "retrieved_memories": [{"text": "something unrelated"}],
        }
        self.assertEqual(classify(record, self.corpus), "unanswerable")

    def test_contentless_gold_is_treated_as_answerable(self):
        """A gold answer of pure stopwords has nothing to match on.

        It must default to answerable rather than inflating the unanswerable
        count on a measurement artefact. Note "yes"/"no" are NOT such cases —
        they carry content tokens under this tokenizer; see the yes/no test.
        """
        record = {"conversation": 1, "gold_answer": "the", "retrieved_memories": []}
        self.assertTrue(answerable(record, self.corpus))

    def test_yes_no_answers_are_not_swept_into_unanswerable(self):
        """A boolean gold whose text is absent would be a false unanswerable.

        Checked empirically rather than assumed: across the corpus all 24
        yes/no gold answers classify as answerable, because conversational
        dialogue contains "yes" and "no" in abundance. If this ever starts
        failing, boolean answers need special handling before the unanswerable
        count can be trusted.
        """
        records = _records()
        corpus = conversation_corpus()
        boolean = [
            r for r in records
            if str(r.get("gold_answer", "")).strip().lower().rstrip(".") in ("yes", "no")
        ]
        self.assertGreater(len(boolean), 10, "expected boolean questions in the corpus")
        misclassified = [r for r in boolean if classify(r, corpus) == "unanswerable"]
        self.assertEqual(misclassified, [])

    def test_unknown_conversation_does_not_claim_unanswerable(self):
        record = {"conversation": 99, "gold_answer": "sunset", "retrieved_memories": []}
        self.assertTrue(answerable(record, self.corpus))


if __name__ == "__main__":
    unittest.main()
