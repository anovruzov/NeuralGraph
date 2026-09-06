"""Structured-output guarantees of the Qwen client."""
from __future__ import annotations

import pytest

from job_harness.config.settings import QwenConfig
from job_harness.qwen.fake import fake_client
from job_harness.qwen.json_repair import repair_json
from job_harness.qwen.schemas import JobScore, SCHEMA_BY_FUNCTION

JOB = {"title": "AI Engineer", "company": "Acme", "location": "Remote (US)",
       "description": "Build LLM agents in Python. 2+ years experience."}


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1, "b": true,}\n```', {"a": 1, "b": True}),
    ('Here you go:\n{"a": 1, "b": True}\nHope that helps.', {"a": 1, "b": True}),
    ('{"a": 1, // comment\n "b": [1,2,]}', {"a": 1, "b": [1, 2]}),
    ('{"a": “hello”}', {"a": "hello"}),
    ('[{"a": 5}]', {"a": 5}),
])
def test_json_repair_recovers(raw, expected):
    assert repair_json(raw) == expected


def test_json_repair_gives_up_cleanly():
    assert repair_json("no json at all") is None
    assert repair_json("") is None


def test_unterminated_json_is_closed():
    assert repair_json('{"answer": "unterminated') == {"answer": "unterminated"}


def test_every_function_returns_its_schema(db):
    client = fake_client(QwenConfig(), db=db, run_id="test-run")
    results = {
        "classify_job": client.classify_job(JOB, ["AI Engineer"]),
        "score_job": client.score_job(JOB, "profile", "resume",
                                      {"technical_fit": 30, "resume_evidence": 25,
                                       "ai_relevance": 20, "career_upside": 10,
                                       "company_comp": 10, "application_friction": 5}),
        "extract_requirements": client.extract_requirements("Required: 2 years. Preferred: k8s."),
        "classify_form_field": client.classify_form_field({"label": "Email", "type": "email"}),
        "answer_form_question": client.answer_form_question(
            {"label": "Email", "type": "email"}, "{}", "resume", {}, {}),
        "detect_duplicate": client.detect_duplicate({"company": "A", "title": "B"}, []),
        "evaluate_application": client.evaluate_application({"text": "Thank you for applying"}),
        "validate_answer": client.validate_answer({"label": "Email"}, "a@b.co", "{}", "r"),
    }
    for name, value in results.items():
        assert isinstance(value, SCHEMA_BY_FUNCTION[name]), name
    client.close()


def test_malformed_output_is_repaired_by_a_second_round_trip(db):
    client = fake_client(QwenConfig(), db=db, run_id="test-run", failure_mode="malformed_once")
    result = client.classify_job(JOB, ["AI Engineer"])
    assert result.is_relevant is True
    assert client.stats["degraded"] == 0
    client.close()


def test_persistently_malformed_output_degrades_safely(db):
    client = fake_client(QwenConfig(), db=db, run_id="test-run", failure_mode="always_malformed")
    score = client.score_job(JOB, "p", "r", {"technical_fit": 30, "resume_evidence": 25,
                                             "ai_relevance": 20, "career_upside": 10,
                                             "company_comp": 10, "application_friction": 5})
    assert isinstance(score, JobScore)
    assert score.decision == "SKIP"          # fail safe, never fail open
    assert score.score == 0
    assert client.stats["degraded"] == 1
    client.close()


def test_transport_failure_is_retried_then_degrades(db):
    config = QwenConfig(max_retries=1)
    client = fake_client(config, db=db, run_id="test-run", failure_mode="http_500")
    answer = client.answer_form_question({"label": "Email"}, "{}", "r", {}, {})
    assert answer.safe_to_submit is False    # never claims an answer it does not have
    assert client.stats["failures"] == 1
    row = db.query_one("SELECT ok, error FROM qwen_calls ORDER BY id DESC LIMIT 1")
    assert row["ok"] == 0 and "500" in row["error"]
    client.close()


def test_identical_requests_are_cached(db):
    client = fake_client(QwenConfig(), db=db, run_id="test-run")
    client.classify_job(JOB, ["AI Engineer"])
    assert client.stats["cache_hits"] == 0
    client.classify_job(JOB, ["AI Engineer"])
    assert client.stats["cache_hits"] == 1
    client.close()


def test_submission_evaluation_is_never_cached(db):
    client = fake_client(QwenConfig(), db=db, run_id="test-run")
    state = {"text": "Thank you for applying"}
    client.evaluate_application(state)
    client.evaluate_application(state)
    assert client.stats["cache_hits"] == 0
    client.close()


def test_scores_are_clamped_into_range():
    score = JobScore(score=250, breakdown={}, decision="APPLY",
                     required_qualifications_met=True, experience_plausible=True)
    assert score.score == 100
