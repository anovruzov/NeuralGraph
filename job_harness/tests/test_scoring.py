"""Prefilter rules and rubric-based scoring decisions."""
from __future__ import annotations

from datetime import date

import pytest

from job_harness.database.models import Job, JobStatus
from job_harness.qwen.schemas import JobScore, ScoreBreakdown
from job_harness.scoring.prefilter import (
    detect_seniority, parse_min_years, parse_posted_date, prefilter,
)
from job_harness.scoring.scorer import Scorer

TODAY = date(2026, 9, 6)


def job(**kw) -> Job:
    base = dict(company="Acme AI", title="AI Engineer",
                canonical_apply_url="https://boards.greenhouse.io/acme/jobs/1",
                posted_date="2026-09-04", location="Remote - US",
                description="Build LLM agents in Python. 2+ years of experience.")
    base.update(kw)
    return Job(**base)


@pytest.mark.parametrize("title,rule", [
    ("Staff Machine Learning Engineer", "senior_title"),
    ("Principal AI Engineer", "senior_title"),
    ("Director of Machine Learning", "senior_title"),
    ("VP of Engineering", "senior_title"),
    ("Engineering Manager, AI", "senior_title"),
    ("Machine Learning Intern", "internship"),
    ("Technical Recruiter", "off_domain"),
])
def test_prefilter_rejects_out_of_scope_titles(config, title, rule):
    result = prefilter(job(title=title), config.scoring, config.discovery, TODAY)
    assert not result.passed
    assert result.rule == rule


def test_prefilter_accepts_target_roles(config):
    for title in ["AI Engineer", "Research Engineer", "LLM Engineer",
                  "Junior Research Engineer", "ML Systems Engineer",
                  "AI Engineer, New Grad"]:
        result = prefilter(job(title=title), config.scoring, config.discovery, TODAY)
        assert result.passed, f"{title}: {result.reason}"


def test_senior_titles_pass_when_explicitly_allowed(config):
    config.scoring.allow_senior_roles = True
    assert prefilter(job(title="Staff AI Engineer"), config.scoring,
                     config.discovery, TODAY).passed


def test_stale_postings_are_rejected(config):
    result = prefilter(job(posted_date="2026-08-01"), config.scoring, config.discovery, TODAY)
    assert not result.passed and result.rule == "stale"


def test_freshness_window_is_configurable(config):
    config.discovery.freshness_days = 60
    assert prefilter(job(posted_date="2026-08-01"), config.scoring,
                     config.discovery, TODAY).passed


def test_excessive_experience_requirement_is_rejected(config):
    result = prefilter(job(description="We need 9+ years of experience."),
                       config.scoring, config.discovery, TODAY)
    assert not result.passed and result.rule == "years_experience"
    assert result.min_years_required == 9


def test_clearance_requirement_is_rejected(config):
    result = prefilter(job(description="Active TS/SCI clearance required."),
                       config.scoring, config.discovery, TODAY)
    assert not result.passed and result.rule == "clearance"


def test_remote_only_filters_onsite(config):
    config.discovery.remote_only = True
    result = prefilter(job(location="New York, NY"), config.scoring, config.discovery, TODAY)
    assert not result.passed and result.rule == "not_remote"


def test_non_us_locations_are_rejected(config):
    result = prefilter(job(location="London, United Kingdom"),
                       config.scoring, config.discovery, TODAY)
    assert not result.passed and result.rule == "non_us"


@pytest.mark.parametrize("text,expected", [
    ("3+ years of experience", 3),
    ("minimum of 5 years", 5),
    ("2-4 years experience", 2),
    ("no requirement stated", None),
])
def test_min_years_parsing(text, expected):
    assert parse_min_years(text) == expected


@pytest.mark.parametrize("value,expected", [
    ("2026-09-04", date(2026, 9, 4)),
    ("2026-09-04T10:00:00Z", date(2026, 9, 4)),
    ("garbage", None),
])
def test_posted_date_parsing(value, expected):
    assert parse_posted_date(value) == expected


def test_relative_posted_dates_are_understood():
    assert parse_posted_date("3 days ago") is not None
    assert parse_posted_date("Posted Today") is not None


@pytest.mark.parametrize("title,expected", [
    ("Senior AI Engineer", "senior"),
    ("Staff AI Engineer", "staff_plus"),
    ("Junior Research Engineer", "junior"),
    ("AI Engineer, New Grad", "new_grad"),
    ("ML Engineering Intern", "intern"),
    ("Director of AI", "management"),
])
def test_seniority_detection(title, expected):
    assert detect_seniority(title) == expected


def _decide(scorer: Scorer, **kw):
    defaults = dict(score=70, breakdown=ScoreBreakdown(), decision="APPLY",
                    required_qualifications_met=True, experience_plausible=True,
                    missing_required=[], reason="")
    defaults.update(kw)
    return scorer._decide(JobScore(**defaults))


def test_decision_thresholds(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    assert _decide(scorer, score=80)[0] == "APPLY"
    assert _decide(scorer, score=65)[0] == "APPLY"
    assert _decide(scorer, score=40)[0] == "SKIP"


def test_borderline_scores_apply_when_experience_is_plausible(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    decision, status, _ = _decide(scorer, score=58, experience_plausible=True)
    assert decision == "APPLY" and status == JobStatus.QUEUED


def test_borderline_scores_skip_when_experience_is_implausible(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    decision, status, _ = _decide(scorer, score=58, experience_plausible=False)
    assert decision == "SKIP" and status == JobStatus.SKIPPED


def test_high_score_with_unmet_hard_requirements_is_skipped(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    decision, _, reason = _decide(scorer, score=90, required_qualifications_met=False,
                                  missing_required=["10 years of experience"])
    assert decision == "SKIP" and "required qualifications unmet" in reason


def test_missing_preferred_qualifications_do_not_cause_a_skip(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    decision, _, _ = _decide(scorer, score=72, required_qualifications_met=True,
                             missing_required=[])
    assert decision == "APPLY"


def test_scorer_skips_prefiltered_jobs_without_calling_the_model(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    before = qwen.stats["requests"]
    decision = scorer.score(job(title="Director of Engineering"), TODAY)
    assert decision.status == JobStatus.SKIPPED
    assert decision.prefiltered
    assert qwen.stats["requests"] == before        # no tokens spent on an obvious reject


def test_scorer_queues_a_good_job(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    decision = scorer.score(job(description=(
        "Build LLM agent infrastructure in Python and PyTorch. Required: 2+ years "
        "of experience and a Bachelor's degree. Preferred: publications. We work on "
        "retrieval, evaluation and post-training." * 3)), TODAY)
    assert decision.status == JobStatus.QUEUED
    assert decision.score >= config.scoring.apply_threshold


# ------------------------------------------------------- batched prescreening

def test_prescreen_settles_prefiltered_and_irrelevant_jobs(config, qwen, applicant):
    from job_harness.qwen.schemas import JobClassification
    from job_harness.scoring.scorer import ScoreDecision

    scorer = Scorer(config, qwen, applicant)
    jobs = [
        job(company="A", title="AI Engineer",
            canonical_apply_url="https://x/1", description="LLM agents in Python. " * 20),
        job(company="B", title="Director of Engineering", canonical_apply_url="https://x/2"),
        job(company="C", title="Office Manager", canonical_apply_url="https://x/3",
            description="Manage the office. No engineering."),
    ]
    outcome = scorer.prescreen(jobs, today=TODAY)

    assert isinstance(outcome[jobs[0].job_id], JobClassification)   # needs a full score
    assert isinstance(outcome[jobs[1].job_id], ScoreDecision)       # prefiltered
    assert outcome[jobs[1].job_id].prefiltered
    assert isinstance(outcome[jobs[2].job_id], ScoreDecision)       # classified irrelevant


def test_prescreen_uses_one_call_per_batch(config, qwen, applicant):
    scorer = Scorer(config, qwen, applicant)
    jobs = [job(company=f"C{i}", title="AI Engineer",
                canonical_apply_url=f"https://x/{i}",
                description="Build LLM agents in Python. " * 20) for i in range(8)]
    before = qwen.stats["requests"]
    scorer.prescreen(jobs, batch_size=8, today=TODAY)
    assert qwen.stats["requests"] - before == 1


def test_prescreen_falls_back_when_the_batch_is_short(config, qwen, applicant, monkeypatch):
    """A model that returns too few results must not misalign the postings."""
    from job_harness.qwen.schemas import JobClassification

    scorer = Scorer(config, qwen, applicant)
    jobs = [job(company=f"C{i}", title="AI Engineer",
                canonical_apply_url=f"https://x/{i}",
                description="Build LLM agents in Python. " * 20) for i in range(3)]

    real_batch = qwen.classify_jobs_batch
    monkeypatch.setattr(
        qwen, "_call",
        lambda function, prompt, schema, use_cache=True: _short_batch(function, prompt,
                                                                     schema, qwen))
    results = real_batch([{"title": j.title, "company": j.company,
                           "location": j.location, "description": j.description}
                          for j in jobs], config.discovery.target_roles)
    assert len(results) == len(jobs)
    assert all(isinstance(r, JobClassification) for r in results)


def _short_batch(function, prompt, schema, client):
    """Return one result for a three-posting batch, then behave normally."""
    from job_harness.qwen.qwen_client import CallResult
    from job_harness.qwen.schemas import BatchJobClassification, JobClassification
    if function == "classify_jobs_batch":
        return CallResult(data=BatchJobClassification(
            results=[JobClassification(is_relevant=True)]))
    return CallResult(data=JobClassification(is_relevant=True, reason="fallback"))


def test_scoring_a_job_with_a_precomputed_classification_skips_the_call(
        config, qwen, applicant):
    from job_harness.qwen.schemas import JobClassification

    scorer = Scorer(config, qwen, applicant)
    target = job(description="Build LLM agents in Python and PyTorch. "
                             "Required: 2+ years. Preferred: Kubernetes. " * 4)
    before = qwen.stats["requests"]
    scorer.score(target, TODAY,
                 classification=JobClassification(is_relevant=True, seniority="junior"))
    functions = [row["function"] for row in
                 qwen.db.query("SELECT function FROM qwen_calls ORDER BY id DESC LIMIT 4")]
    assert "classify_job" not in functions
    assert qwen.stats["requests"] > before          # the rubric score still ran
