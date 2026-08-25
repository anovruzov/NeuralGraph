"""Regression tests for fix 1: the temporal abstention gate.

Built from the *mechanism* found on the development split, never from a
development or locked-test answer. Every fixture below is synthetic: invented
speakers, invented events, invented dates. Nothing here would pass by
memorising a benchmark question, and none of these strings appears in the
corpus.

The four behaviours the round must preserve or establish:

1. answer present  -> no abstention
2. answer absent   -> abstention preserved
3. relative date   -> requires resolving against the excerpt timestamp
4. nearby but unrelated event -> does not prevent abstention

A prompt cannot be unit-tested without a model, so these test the deterministic
machinery the prompt depends on: chronological ordering, stable excerpt ids,
timestamp visibility, citation parsing, and the abstention-support check. If
any of that is wrong, the instruction is unfollowable regardless of the model.
"""

from __future__ import annotations

import unittest

from evaluation.improvement_loop.rejected_v2.prompts_v2 import (
    JSON_INSTRUCTION_V2,
    SYSTEM_PROMPT_V2,
    abstention_is_supported,
    build_prompt_v2,
    order_evidence,
    parse_response_v2,
    parse_timestamp,
)


def memory(speaker: str, when: str, text: str) -> dict[str, str]:
    return {"speaker": speaker, "datetime": when, "text": text}


def record(question: str, memories: list[dict[str, str]]) -> dict[str, object]:
    return {"id": 999999, "category": "temporal", "question": question,
            "retrieved_memories": memories}


class TimestampParsingTests(unittest.TestCase):
    def test_parses_the_corpus_timestamp_format(self):
        stamp = parse_timestamp("4:04 pm on 20 January, 2023")
        self.assertIsNotNone(stamp)
        self.assertEqual((stamp.year, stamp.month, stamp.day), (2023, 1, 20))

    def test_parses_a_bare_date(self):
        stamp = parse_timestamp("9 April, 2023")
        self.assertEqual((stamp.year, stamp.month, stamp.day), (2023, 4, 9))

    def test_unparseable_timestamp_returns_none_rather_than_guessing(self):
        self.assertIsNone(parse_timestamp("sometime last spring"))
        self.assertIsNone(parse_timestamp(""))


class ChronologicalOrderingTests(unittest.TestCase):
    def test_excerpts_are_ordered_oldest_first(self):
        rec = record("when?", [
            memory("A", "1:00 pm on 5 March, 2023", "third"),
            memory("A", "1:00 pm on 1 January, 2023", "first"),
            memory("A", "1:00 pm on 2 February, 2023", "second"),
        ])
        texts = [item["text"] for item in order_evidence(rec)]
        self.assertEqual(texts, ["first", "second", "third"])

    def test_ids_are_assigned_after_sorting_and_are_stable(self):
        rec = record("when?", [
            memory("A", "1:00 pm on 5 March, 2023", "later"),
            memory("A", "1:00 pm on 1 January, 2023", "earlier"),
        ])
        first_pass = {i["id"]: i["text"] for i in order_evidence(rec)}
        second_pass = {i["id"]: i["text"] for i in order_evidence(rec)}
        self.assertEqual(first_pass, second_pass)
        self.assertEqual(first_pass["E1"], "earlier")

    def test_undated_excerpts_sort_last_and_never_become_earliest(self):
        """An excerpt with no timestamp must not be presented as the earliest
        event; a 'when did X first happen' answer would then be invented."""
        rec = record("when?", [
            memory("A", "", "undated"),
            memory("A", "1:00 pm on 1 January, 2023", "dated"),
        ])
        ordered = order_evidence(rec)
        self.assertEqual(ordered[0]["text"], "dated")
        self.assertEqual(ordered[-1]["text"], "undated")

    def test_ordering_is_stable_for_equal_timestamps(self):
        rec = record("when?", [
            memory("A", "1:00 pm on 1 January, 2023", "alpha"),
            memory("B", "1:00 pm on 1 January, 2023", "beta"),
        ])
        self.assertEqual([i["text"] for i in order_evidence(rec)], ["alpha", "beta"])


class PromptContentTests(unittest.TestCase):
    def test_every_excerpt_carries_a_citable_id_and_its_timestamp(self):
        rec = record("When did Rho start fencing?", [
            memory("Rho", "2:00 pm on 4 May, 2021", "I took up fencing today."),
            memory("Rho", "2:00 pm on 9 September, 2021", "Fencing is going well."),
        ])
        prompt = build_prompt_v2(rec)
        self.assertIn("[E1]", prompt)
        self.assertIn("[E2]", prompt)
        self.assertIn("4 May, 2021", prompt)
        self.assertIn("9 September, 2021", prompt)
        self.assertIn("chronological", prompt.lower())

    def test_prompt_never_contains_the_gold_answer_field(self):
        rec = record("When?", [memory("A", "1:00 pm on 1 January, 2023", "x")])
        rec["gold_answer"] = "ZZQX_SENTINEL_NOT_IN_ANY_EXCERPT"
        rec["generated_answer"] = "ZZQX_BASELINE_SENTINEL"
        prompt = build_prompt_v2(rec)
        self.assertNotIn("ZZQX_SENTINEL", prompt)
        self.assertNotIn("ZZQX_BASELINE_SENTINEL", prompt)

    def test_instruction_gates_abstention_behind_a_coverage_check(self):
        lowered = SYSTEM_PROMPT_V2.lower()
        self.assertIn("timestamps are evidence", lowered)
        self.assertIn("before you may abstain", lowered)
        self.assertIn("re-read every excerpt", lowered)
        # The guard against trading abstention for hallucination must be present.
        self.assertIn("wrong answer is worse", lowered)

    def test_instruction_requires_citations(self):
        self.assertIn("cited_evidence", JSON_INSTRUCTION_V2)


class MechanismTests(unittest.TestCase):
    """The four required behaviours, expressed over the deterministic parts."""

    def test_1_answer_present_means_evidence_bears_on_the_question(self):
        """A dated excerpt describing the event supplies the answer through its
        timestamp, so an abstention here would be a false abstention."""
        rec = record("When did Rho start fencing?", [
            memory("Rho", "2:00 pm on 4 May, 2021", "I took up fencing today."),
        ])
        ordered = order_evidence(rec)
        self.assertEqual(len(ordered), 1)
        self.assertTrue(ordered[0]["datetime"])
        # An abstention that cites nothing has not performed the check.
        self.assertFalse(abstention_is_supported([], True, [], rec))

    def test_2_answer_absent_keeps_abstention_available(self):
        """With excerpts supplied but none bearing on the question, abstention
        remains correct -- provided the model shows it examined them."""
        rec = record("When did Rho start fencing?", [
            memory("Rho", "2:00 pm on 4 May, 2021", "The weather is nice."),
        ])
        self.assertTrue(abstention_is_supported([], True, ["E1"], rec))

    def test_2b_abstention_with_no_evidence_at_all_is_trivially_supported(self):
        rec = record("When did Rho start fencing?", [])
        self.assertTrue(abstention_is_supported([], True, [], rec))

    def test_3_relative_date_requires_the_excerpt_timestamp_to_resolve(self):
        """The prose alone ('about four months now') names no date. Only the
        excerpt's timestamp makes it answerable, so the timestamp must reach
        the prompt."""
        rec = record("When did Rho start fencing?", [
            memory("Rho", "5:34 pm on 6 December, 2021",
                   "I've been fencing for about four months now."),
        ])
        prompt = build_prompt_v2(rec)
        self.assertNotIn("August", prompt)          # the answer is nowhere in text
        self.assertIn("6 December, 2021", prompt)   # but the basis for it is
        lowered = SYSTEM_PROMPT_V2.lower()
        self.assertIn("resolve relative expressions", lowered)
        self.assertIn("compute the resulting date", lowered)

    def test_4_nearby_but_unrelated_event_does_not_force_an_answer(self):
        """Excerpts close in time but about a different event must not be
        treated as evidence; the instruction must say so explicitly."""
        rec = record("When did Rho start fencing?", [
            memory("Rho", "2:00 pm on 3 May, 2021", "I started pottery today."),
            memory("Rho", "2:00 pm on 5 May, 2021", "I went swimming today."),
        ])
        self.assertTrue(abstention_is_supported([], True, ["E1", "E2"], rec))
        lowered = SYSTEM_PROMPT_V2.lower()
        self.assertIn("merely nearby in time", lowered)
        self.assertIn("is not evidence", lowered)


class ResponseParsingTests(unittest.TestCase):
    def test_parses_items_and_citations(self):
        items, abstained, cited = parse_response_v2(
            '{"items": ["August 2021"], "not_in_evidence": false, '
            '"cited_evidence": ["E1", "E3"]}'
        )
        self.assertEqual(items, ["August 2021"])
        self.assertFalse(abstained)
        self.assertEqual(cited, ["E1", "E3"])

    def test_parses_an_abstention(self):
        items, abstained, cited = parse_response_v2(
            '{"items": [], "not_in_evidence": true, "cited_evidence": ["E1"]}'
        )
        self.assertEqual(items, [])
        self.assertTrue(abstained)
        self.assertEqual(cited, ["E1"])

    def test_tolerates_fenced_json_and_reasoning_preamble(self):
        items, _, cited = parse_response_v2(
            '<think>weighing the excerpts</think>\n'
            '```json\n{"items": ["1 January, 2023"], "not_in_evidence": false, '
            '"cited_evidence": ["E2"]}\n```'
        )
        self.assertEqual(items, ["1 January, 2023"])
        self.assertEqual(cited, ["E2"])

    def test_malformed_citations_are_dropped_not_invented(self):
        _, _, cited = parse_response_v2(
            '{"items": ["x"], "not_in_evidence": false, '
            '"cited_evidence": ["E1", "not-an-id", 7, null]}'
        )
        self.assertEqual(cited, ["E1"])

    def test_unparseable_reply_does_not_raise(self):
        self.assertEqual(parse_response_v2("total garbage"), ([], False, []))
        self.assertEqual(parse_response_v2(""), ([], False, []))


if __name__ == "__main__":
    unittest.main()
