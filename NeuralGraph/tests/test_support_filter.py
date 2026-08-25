"""Adversarial tests for per-item evidence-support filtering.

The dangerous failure of this filter is not letting an unsupported item through
— it is silently dropping a *correct* one, which looks like a precision win
while costing recall. Several tests below exist only to catch that.

Every fixture is synthetic. No benchmark question, gold answer or entity appears
here, so nothing can pass by memorising the corpus.
"""

from __future__ import annotations

import unittest

from evaluation.improvement_loop.candidate_b.support_filter import (
    RULES,
    Decision,
    _stem,
    content_tokens,
    filter_items,
    normalise,
)

EVIDENCE = (
    "Rho said she bought a red painting at the market on Tuesday. "
    "Later Rho mentioned her fencing lessons and a trip to Lisbon. "
    "She also described the paintings hanging in her hallway."
)


class RejectsUnsupportedTests(unittest.TestCase):
    def test_invented_item_is_rejected(self):
        kept, _ = filter_items(["red bicycle"], EVIDENCE, "containment")
        self.assertEqual(kept, [])

    def test_the_red_painting_red_bicycle_confusion_is_caught(self):
        """The canonical adversarial case: swapping the head noun must not
        survive just because the modifier matches."""
        kept, _ = filter_items(["red painting", "red bicycle"], EVIDENCE, "containment")
        self.assertEqual(kept, ["red painting"])

    def test_plausible_but_absent_details_are_rejected(self):
        kept, _ = filter_items(["collars", "tags", "toys"], EVIDENCE, "containment")
        self.assertEqual(kept, [])

    def test_partially_supported_item_is_rejected_under_containment(self):
        kept, _ = filter_items(["fencing lessons in Madrid"], EVIDENCE, "containment")
        self.assertEqual(kept, [])


class PreservesSupportedTests(unittest.TestCase):
    """The dangerous direction: dropping correct items."""

    def test_verbatim_item_survives(self):
        kept, _ = filter_items(["red painting"], EVIDENCE, "containment")
        self.assertEqual(kept, ["red painting"])

    def test_morphological_variant_survives(self):
        """'paintings' in the evidence must support 'painting' and vice versa,
        or every plural gold item would be dropped."""
        kept, _ = filter_items(["paintings"], EVIDENCE, "containment")
        self.assertEqual(kept, ["paintings"])

    def test_reordered_paraphrase_survives_containment_but_not_lexical(self):
        item = "painting that is red"
        self.assertEqual(filter_items([item], EVIDENCE, "containment")[0], [item])
        self.assertEqual(filter_items([item], EVIDENCE, "lexical")[0], [])

    def test_multi_word_supported_item_survives(self):
        kept, _ = filter_items(["fencing lessons"], EVIDENCE, "containment")
        self.assertEqual(kept, ["fencing lessons"])

    def test_order_is_preserved(self):
        items = ["Lisbon", "red bicycle", "fencing lessons"]
        kept, _ = filter_items(items, EVIDENCE, "containment")
        self.assertEqual(kept, ["Lisbon", "fencing lessons"])


class RuleBehaviourTests(unittest.TestCase):
    def test_lexical_is_the_strictest_rule(self):
        items = ["painting that is red", "red painting", "red bicycle"]
        lexical, _ = filter_items(items, EVIDENCE, "lexical")
        containment, _ = filter_items(items, EVIDENCE, "containment")
        self.assertLessEqual(len(lexical), len(containment))

    def test_lower_coverage_threshold_keeps_more(self):
        items = ["fencing lessons in Madrid"]
        strict, _ = filter_items(items, EVIDENCE, "coverage", 0.9)
        loose, _ = filter_items(items, EVIDENCE, "coverage", 0.4)
        self.assertLessEqual(len(strict), len(loose))

    def test_unknown_rule_raises_rather_than_silently_passing_everything(self):
        with self.assertRaises(ValueError):
            filter_items(["x"], EVIDENCE, "no-such-rule")

    def test_every_named_rule_runs(self):
        for rule in RULES:
            kept, decisions = filter_items(["red painting"], EVIDENCE, rule)
            self.assertEqual(kept, ["red painting"], f"{rule} dropped a verbatim item")
            self.assertTrue(all(isinstance(d, Decision) for d in decisions))


class EdgeCaseTests(unittest.TestCase):
    def test_empty_and_blank_items_are_dropped_without_raising(self):
        kept, _ = filter_items(["", "   ", "red painting"], EVIDENCE, "containment")
        self.assertEqual(kept, ["red painting"])

    def test_function_word_only_item_falls_back_to_verbatim(self):
        """An item made only of stopwords has no content tokens to check.
        Passing it for free would let any degenerate answer through, so the
        code must fall back to a verbatim test. `of the` is genuinely
        token-free after stopword removal; `yes` is not, and testing with it
        would leave this branch unexercised."""
        self.assertEqual(content_tokens("of the"), [])
        self.assertEqual(filter_items(["of the"], "unrelated words here",
                                      "containment")[0], [])
        self.assertEqual(filter_items(["of the"], "a part of the market",
                                      "containment")[0], ["of the"])

    def test_stemming_is_shallow_and_does_not_truncate_to_a_prefix(self):
        """Aggressive stemming would collapse distinct items into one token and
        make unsupported items look supported. The strip must remove a suffix,
        never most of the word."""
        self.assertEqual(_stem("painting"), "paint")
        self.assertEqual(_stem("paintings"), "painting")
        self.assertEqual(_stem("fencing"), "fenc")
        # Short words are left alone rather than reduced to noise.
        self.assertEqual(_stem("has"), "has")
        self.assertEqual(_stem("red"), "red")
        for word in ("painting", "bicycle", "fencing", "lessons"):
            self.assertGreaterEqual(
                len(_stem(word)), len(word) - 3,
                f"{word!r} was truncated past a suffix strip")

    def test_empty_evidence_rejects_everything(self):
        kept, _ = filter_items(["red painting"], "", "containment")
        self.assertEqual(kept, [])

    def test_decisions_explain_every_rejection(self):
        _, decisions = filter_items(["red bicycle"], EVIDENCE, "containment")
        self.assertEqual(len(decisions), 1)
        self.assertFalse(decisions[0].kept)
        self.assertIn("content tokens", decisions[0].rationale)

    def test_filter_never_reads_a_gold_answer(self):
        """`filter_items` takes only items and evidence. If a gold answer could
        reach it, the recall it reports would be an oracle's."""
        import inspect
        params = set(inspect.signature(filter_items).parameters)
        self.assertEqual(params, {"items", "evidence", "rule", "threshold"})


class HelperTests(unittest.TestCase):
    def test_normalise_strips_punctuation_and_case(self):
        self.assertEqual(normalise("Red, Painting!"), "red painting")

    def test_content_tokens_drop_stopwords_and_short_tokens(self):
        self.assertEqual(content_tokens("the red painting of a"), ["red", "painting"])


if __name__ == "__main__":
    unittest.main()
