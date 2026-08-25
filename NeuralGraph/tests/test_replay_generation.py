"""Tests for the generation-replay harness and its item-level grader.

No API key and no network. Everything here exercises the offline half: prompt
construction, cost estimation, and the grader — which is the part that decides
whether a future replay run means anything.

The grader is validated against the recorded GPT-4o judge rather than against
hand-picked examples: if item-F1 did not separate judge-correct answers from
judge-wrong ones, it would not be measuring correctness and no improvement it
reported could be believed.
"""

from __future__ import annotations

import contextlib
import io
import json
import statistics
import unittest
from pathlib import Path

from evaluation.diagnose_accuracy import evidence_present, retrieved_text
from evaluation.replay_generation import (
    RESPONSE_FORMAT,
    SYSTEM_PROMPT,
    ProgressReporter,
    build_prompt,
    build_row,
    estimate_cost,
    grade,
    is_daily_quota_error,
    parse_items,
    stratified_sample,
    summarise,
)

ARTIFACT = Path(__file__).resolve().parents[2] / "demo" / "maximal.json"


def _records():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))["results"]


class GraderUnitTests(unittest.TestCase):
    def test_perfect_answer_scores_one(self):
        result = grade("bowls, cup", ["bowls", "cup"])
        self.assertEqual(result.f1, 1.0)
        self.assertTrue(result.exact_set_match)
        self.assertEqual(result.missing, [])

    def test_missing_an_item_costs_recall_not_precision(self):
        """The dominant failure: naming one of two gold items."""
        result = grade('"Nothing is Impossible", "Charlotte\'s Web"', ["Charlotte's Web"])
        self.assertEqual(result.precision, 1.0)
        self.assertEqual(result.recall, 0.5)
        self.assertFalse(result.exact_set_match)
        self.assertEqual(len(result.missing), 1)

    def test_inventing_an_item_costs_precision_not_recall(self):
        result = grade("sunset", ["sunset", "still life"])
        self.assertEqual(result.recall, 1.0)
        self.assertEqual(result.precision, 0.5)
        self.assertEqual(result.spurious, ["still life"])

    def test_items_match_on_content_tokens_not_exact_strings(self):
        """Paraphrase must not be scored as a miss, or this measures phrasing."""
        self.assertEqual(grade("Pride parade", ["LGBTQ+ pride parade"]).recall, 1.0)
        self.assertEqual(grade("Horse", ["horses"]).recall, 1.0)

    def test_unrelated_answer_scores_zero(self):
        result = grade("dinosaurs, nature", ["camping", "beach"])
        self.assertEqual(result.f1, 0.0)
        self.assertEqual(len(result.missing), 2)

    def test_empty_prediction_scores_zero_without_raising(self):
        result = grade("bowls, cup", [])
        self.assertEqual(result.f1, 0.0)
        self.assertFalse(result.exact_set_match)

    def test_empty_gold_does_not_claim_an_exact_match(self):
        self.assertFalse(grade("", ["anything"]).exact_set_match)

    def test_each_predicted_item_is_consumed_once(self):
        """One prediction must not satisfy two distinct gold items."""
        result = grade("painting, pottery", ["painting pottery"])
        self.assertLess(result.recall, 1.0)

    def test_a_string_prediction_is_split_like_a_list(self):
        from_string = grade("bowls, cup", "bowls, cup")
        from_list = grade("bowls, cup", ["bowls", "cup"])
        self.assertEqual(from_string.f1, from_list.f1)


class GraderValidityTests(unittest.TestCase):
    """Does the grader track the independent judge? If not, nothing downstream holds."""

    @classmethod
    def setUpClass(cls):
        cls.rows = [
            (record, grade(str(record.get("gold_answer", "")),
                           str(record.get("generated_answer", ""))))
            for record in _records()
        ]

    def test_item_f1_separates_judge_correct_from_judge_wrong(self):
        for category in ("single_hop", "multi_hop", "temporal"):
            subset = [(r, g) for r, g in self.rows if r["category"] == category]
            right = statistics.mean(g.f1 for r, g in subset if r["correct"])
            wrong = statistics.mean(g.f1 for r, g in subset if not r["correct"])
            with self.subTest(category=category):
                self.assertGreater(
                    right, wrong + 0.2,
                    f"{category}: F1 does not separate correct from wrong answers",
                )

    def test_exact_set_match_is_stricter_than_the_judge(self):
        """Pinned so nobody reads exact_set_match as the judge's metric.

        The judge accepts partial and paraphrased answers; exact set match does
        not. It is a strict lower bound, and an F1 gain need not translate one
        for one into judged accuracy.
        """
        strict = sum(1 for _r, g in self.rows if g.exact_set_match)
        judged = sum(1 for r, _g in self.rows if r["correct"])
        self.assertLess(strict, judged)

    def test_single_hop_failures_are_weak_on_recall_and_precision(self):
        """Failures both omit gold items and invent absent ones."""
        failures = [g for r, g in self.rows
                    if r["category"] == "single_hop" and not r["correct"]]
        self.assertLess(statistics.mean(g.recall for g in failures), 0.4)
        self.assertLess(statistics.mean(g.precision for g in failures), 0.4)


class ReplyParsingTests(unittest.TestCase):
    """A parse failure would be scored as a wrong answer, not as a parse failure.

    That is the dangerous case: it blames the prompt for a formatting problem
    and makes a good model look bad. Reasoning models make it likely — qwen3 and
    deepseek-r1 emit <think> blocks ahead of the answer even under JSON mode.
    """

    def test_plain_json(self):
        self.assertEqual(
            parse_items('{"items": ["a", "b"], "not_in_evidence": false}'),
            (["a", "b"], False),
        )

    def test_reasoning_model_think_block_is_stripped(self):
        raw = '<think>Let me scan the excerpts.</think>{"items": ["a"], "not_in_evidence": false}'
        self.assertEqual(parse_items(raw), (["a"], False))

    def test_fenced_code_block(self):
        raw = '```json\n{"items": ["a"], "not_in_evidence": false}\n```'
        self.assertEqual(parse_items(raw), (["a"], False))

    def test_json_with_a_preamble_sentence(self):
        raw = 'Here is the answer: {"items": ["a", "b"], "not_in_evidence": true}'
        self.assertEqual(parse_items(raw), (["a", "b"], True))

    def test_prose_falls_back_to_item_splitting(self):
        self.assertEqual(parse_items("bowls, cup"), (["bowls", "cup"], False))

    def test_think_block_then_prose(self):
        self.assertEqual(parse_items("<think>hm</think>bowls, cup"), (["bowls", "cup"], False))

    def test_empty_and_malformed_do_not_raise(self):
        for raw in ("", "   ", "{not json", '{"wrong_key": 1}', "null"):
            with self.subTest(raw=raw):
                items, flag = parse_items(raw)
                self.assertIsInstance(items, list)
                self.assertIsInstance(flag, bool)

    def test_items_returned_as_a_string_is_split_not_shredded(self):
        """A model that ignores the array type returns "bowls, cup" as one string.

        It must be split into items. The failure mode guarded against is falling
        through to prose parsing on the *raw JSON*, which shreds
        `{"items": ...}` into nonsense fragments.

        Test values must carry content tokens — "a, b" would not work here,
        since "a" is a stopword and is legitimately dropped.
        """
        items, _flag = parse_items('{"items": "bowls, cup", "not_in_evidence": false}')
        self.assertEqual(items, ["bowls", "cup"])
        self.assertNotIn("not_in_evidence", " ".join(items))


class QuotaDetectionTests(unittest.TestCase):
    """A per-day quota must be told apart from ordinary throttling.

    Both arrive as HTTP 429 with a body saying "check your plan and billing
    details". Retrying a per-day exhaustion burns the remaining allowance and
    converts every later question into a scored wrong answer, so a sweep would
    report a quota wall as model accuracy.
    """

    def test_daily_quota_markers_are_recognised(self):
        for body in (
            "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            "quotaId: GenerateRequestsPerDayPerProjectPerModel",
            "You exceeded 20 requests per day",
        ):
            with self.subTest(body=body[:40]):
                self.assertTrue(is_daily_quota_error(body))

    def test_rate_limits_are_not_mistaken_for_daily_quota(self):
        """Per-minute throttling IS retryable; treating it as fatal aborts good runs."""
        for body in (
            "GenerateRequestsPerMinutePerProject",
            "This model is currently experiencing high demand",
            "429 Too Many Requests",
            "",
        ):
            with self.subTest(body=body[:40]):
                self.assertFalse(is_daily_quota_error(body))


class StratifiedSampleTests(unittest.TestCase):
    """A sample must represent the corpus, or the number measured on it is local.

    Taking the first N records is the trap: demo/maximal.json is ordered by
    conversation, so the first 300 cover only conversations 1-3 of 10.
    """

    @classmethod
    def setUpClass(cls):
        cls.records = _records()

    def test_returns_exactly_the_requested_size(self):
        for size in (10, 50, 300, 999):
            with self.subTest(size=size):
                self.assertEqual(len(stratified_sample(self.records, size)), size)

    def test_category_mix_matches_the_corpus(self):
        import collections
        sample = stratified_sample(self.records, 300)
        got = collections.Counter(r["category"] for r in sample)
        full = collections.Counter(r["category"] for r in self.records)
        for category, total in full.items():
            with self.subTest(category=category):
                expected = total * 300 / len(self.records)
                self.assertLessEqual(abs(got[category] - expected), 1.0)

    def test_sample_spans_every_conversation(self):
        """The specific failure of --limit: three conversations out of ten."""
        sample = stratified_sample(self.records, 300)
        self.assertEqual(
            {r["conversation"] for r in sample},
            {r["conversation"] for r in self.records},
        )
        first_n = {r["conversation"] for r in self.records[:300]}
        self.assertLess(len(first_n), 5, "if --limit stopped being biased, revisit this")

    def test_is_deterministic_so_two_models_see_identical_questions(self):
        a = [r["id"] for r in stratified_sample(self.records, 300)]
        b = [r["id"] for r in stratified_sample(self.records, 300)]
        self.assertEqual(a, b)

    def test_a_different_seed_gives_a_different_sample(self):
        a = [r["id"] for r in stratified_sample(self.records, 300, seed=1)]
        b = [r["id"] for r in stratified_sample(self.records, 300, seed=2)]
        self.assertNotEqual(a, b)

    def test_no_duplicates(self):
        sample = stratified_sample(self.records, 300)
        self.assertEqual(len({r["id"] for r in sample}), len(sample))

    def test_oversized_request_is_clamped_to_the_corpus(self):
        sample = stratified_sample(self.records, 99999)
        self.assertEqual(len(sample), len(self.records))


class PromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = _records()

    def test_prompt_carries_the_question_and_the_evidence(self):
        record = self.records[0]
        prompt = build_prompt(record)
        self.assertIn(record["question"], prompt)
        first_memory = record["retrieved_memories"][0]["text"]
        self.assertIn(first_memory[:40], prompt)

    def test_prompt_is_built_only_from_the_question_and_the_evidence(self):
        """The gold_answer *field* must never reach the prompt.

        Note what this does NOT assert. The gold answer's *text* legitimately
        appears in the excerpts — that is precisely what "the evidence was
        retrieved" means, and the whole diagnosis in docs/ACCURACY.md is about
        how often it does. An earlier version of this test asserted the
        opposite and failed on 23 records for being wrong about the task.

        The real leak to guard against is reading the label field itself, so
        this uses a sentinel that appears nowhere in the evidence.
        """
        record = dict(self.records[0])
        record["gold_answer"] = "ZZQX_SENTINEL_NOT_IN_ANY_MEMORY"
        prompt = build_prompt(record)
        self.assertNotIn("ZZQX_SENTINEL", prompt)
        self.assertNotIn("gold", prompt.lower())

    def test_prompt_omits_the_recorded_baseline_answer(self):
        """Showing Claude the previous answer would contaminate the comparison."""
        record = dict(self.records[0])
        record["generated_answer"] = "ZZQX_BASELINE_SENTINEL"
        self.assertNotIn("ZZQX_BASELINE_SENTINEL", build_prompt(record))

    def test_system_prompt_targets_each_measured_failure_mode(self):
        lowered = SYSTEM_PROMPT.lower()
        self.assertIn("every distinct item", lowered)   # omission, 85/86 failures
        self.assertIn("do not infer", lowered)          # invention
        self.assertIn("question actually asked", lowered)  # wrong selection, 38/86

    def test_schema_requires_an_item_list(self):
        schema = RESPONSE_FORMAT["schema"]
        self.assertEqual(schema["properties"]["items"]["type"], "array")
        self.assertIn("items", schema["required"])
        self.assertFalse(schema["additionalProperties"])

    def test_cost_estimate_scales_with_question_count(self):
        small = estimate_cost(self.records[:10], "claude-opus-5")
        large = estimate_cost(self.records[:100], "claude-opus-5")
        self.assertGreater(large["estimated_usd"], small["estimated_usd"])
        self.assertEqual(small["questions"], 10)


class EvidenceAttributionTests(unittest.TestCase):
    """The per-row evidence fields must mean exactly what they mean in
    ``diagnose_accuracy.py``.

    The whole point of carrying ``evidence_lenient`` on a replay row is to
    split a wrong answer into "this model dropped evidence it was handed"
    (generation, this replay's problem) versus "the excerpts never contained
    the answer" (retrieval, the NeuralGraph track's problem). That split is
    only meaningful if it is the *same* measurement docs/ACCURACY.md made. A
    reimplementation that drifted -- a different normaliser, a token-set check
    instead of substring -- would silently reassign failures between two teams'
    backlogs, so these tests pin the row fields to the source functions rather
    than to hand-written expectations.
    """

    @classmethod
    def setUpClass(cls):
        cls.records = _records()

    def _rows(self, records):
        # Items are irrelevant to the evidence fields; grading is covered
        # elsewhere. Pass the gold answer so rows are well-formed.
        return [
            build_row(r, [str(r.get("gold_answer", ""))], False, 0, 0)
            for r in records
        ]

    def test_row_evidence_matches_the_diagnosis_source_of_truth(self):
        for record in self.records[:400]:
            row = build_row(record, [], False, 0, 0)
            strict, lenient = evidence_present(record)
            with self.subTest(id=record.get("id")):
                self.assertEqual(row["evidence_strict"], strict)
                self.assertEqual(row["evidence_lenient"], lenient)
                self.assertEqual(row["retrieved_text"], retrieved_text(record))

    def test_lenient_is_a_superset_of_strict_on_the_real_corpus(self):
        """Same implication test_diagnose_accuracy.py asserts, re-asserted on
        the replay row so the two cannot drift apart."""
        for row in self._rows(self.records[:400]):
            if row["evidence_strict"]:
                with self.subTest(id=row["id"]):
                    self.assertTrue(row["evidence_lenient"])

    def test_gold_absent_from_excerpts_is_not_scored_as_retrieved(self):
        record = dict(self.records[0])
        record["gold_answer"] = "ZZQX_SENTINEL_NOT_IN_ANY_MEMORY"
        row = build_row(record, [], False, 0, 0)
        self.assertFalse(row["evidence_strict"])
        self.assertFalse(row["evidence_lenient"])

    def test_no_retrieved_memories_is_never_evidence(self):
        record = dict(self.records[0])
        record["retrieved_memories"] = []
        row = build_row(record, [], False, 0, 0)
        self.assertEqual(row["retrieved_text"], "")
        self.assertFalse(row["evidence_strict"])
        self.assertFalse(row["evidence_lenient"])


class DiagnosisSummaryTests(unittest.TestCase):
    """``summarise``'s diagnosis block must partition the wrong answers."""

    def _row(self, *, correct: bool, lenient: bool, ident: int = 0):
        gold = "alpha"
        items = ["alpha"] if correct else ["beta"]
        row = build_row({"id": ident, "category": "single_hop",
                         "gold_answer": gold, "generated_answer": gold},
                        items, False, 0, 0)
        row["evidence_lenient"] = lenient
        row["evidence_strict"] = lenient
        return row

    def test_wrong_splits_exhaustively_into_with_and_without_evidence(self):
        rows = [
            self._row(correct=True, lenient=True, ident=1),
            self._row(correct=False, lenient=True, ident=2),
            self._row(correct=False, lenient=True, ident=3),
            self._row(correct=False, lenient=False, ident=4),
        ]
        diagnosis = summarise(rows)["diagnosis"]
        self.assertEqual(diagnosis["wrong"], 3)
        self.assertEqual(diagnosis["wrong_with_evidence"], 2)
        self.assertEqual(diagnosis["wrong_without_evidence"], 1)
        self.assertEqual(
            diagnosis["wrong_with_evidence"] + diagnosis["wrong_without_evidence"],
            diagnosis["wrong"],
        )

    def test_wrong_count_agrees_with_the_reported_exact_set_match(self):
        rows = [self._row(correct=i % 3 == 0, lenient=True, ident=i)
                for i in range(9)]
        summary = summarise(rows)
        expected_wrong = len(rows) - round(
            summary["claude"]["exact_set_match"] * len(rows)
        )
        self.assertEqual(summary["diagnosis"]["wrong"], expected_wrong)

    def test_error_rows_are_excluded_from_the_diagnosis(self):
        """Error rows carry no evidence fields; counting them would crash or,
        worse, silently score a transport failure as a retrieval miss."""
        rows = [
            self._row(correct=False, lenient=False, ident=1),
            {"id": 2, "error": "boom"},
        ]
        summary = summarise(rows)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["diagnosis"]["wrong"], 1)

    def test_all_errors_reports_no_diagnosis_rather_than_a_false_zero(self):
        summary = summarise([{"id": 1, "error": "boom"}])
        self.assertEqual(summary["scored"], 0)
        self.assertNotIn("diagnosis", summary)


class ProgressReporterTests(unittest.TestCase):
    """Progress output is operator-facing only; it must never alter results,
    and must not crash a long run on an edge case."""

    def _capture(self, fn):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            fn()
        return buffer.getvalue()

    def test_counts_completions_and_errors_separately(self):
        reporter = ProgressReporter(total=4, every=100)
        self._capture(lambda: [
            reporter.tick("single_hop", True),
            reporter.tick("single_hop", False),
            reporter.tick(),
            reporter.tick("multi_hop", True),
        ])
        self.assertEqual(reporter.done, 4)
        self.assertEqual(reporter.errors, 1)
        self.assertEqual(reporter.category_n, {"single_hop": 2, "multi_hop": 1})
        self.assertEqual(reporter.category_correct,
                         {"single_hop": 1, "multi_hop": 1})

    def test_reports_running_accuracy_per_category(self):
        reporter = ProgressReporter(total=2, every=2)
        output = self._capture(lambda: [
            reporter.tick("single_hop", True),
            reporter.tick("single_hop", False),
        ])
        self.assertIn("2/2", output)
        self.assertIn("single_hop=1/2", output)
        self.assertIn("50.0%", output)

    def test_prints_on_the_interval_and_on_the_final_tick(self):
        reporter = ProgressReporter(total=3, every=2)
        lines = []
        for _ in range(3):
            lines.append(self._capture(lambda: reporter.tick("single_hop", True)))
        self.assertEqual(lines[0], "")            # 1 of 3: no interval, not final
        self.assertIn("2/3", lines[1])            # interval
        self.assertIn("3/3", lines[2])            # final tick, off-interval

    def test_an_all_error_run_still_reports_without_dividing_by_zero(self):
        reporter = ProgressReporter(total=1, every=1)
        output = self._capture(reporter.tick)
        self.assertIn("errors=1", output)

    def test_progress_goes_to_stderr_not_stdout(self):
        """Results are written to stdout; progress chatter must not corrupt
        a redirected JSON payload."""
        reporter = ProgressReporter(total=1, every=1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self._capture(lambda: reporter.tick("single_hop", True))
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
