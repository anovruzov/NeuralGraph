"""Persistence, idempotency and crash recovery."""
from __future__ import annotations

import pytest

from job_harness.database.db import Database, DuplicateSubmission
from job_harness.database.models import (
    Job, JobStatus, canonicalize_url, normalize_company, normalize_title,
)


def make_job(**kw) -> Job:
    base = dict(company="Acme AI", title="AI Engineer",
                canonical_apply_url="https://boards.greenhouse.io/acme/jobs/1",
                ats_type="greenhouse", ats_job_key="greenhouse:acme:1")
    base.update(kw)
    return Job(**base)


def test_job_id_is_stable_and_derived():
    a = make_job()
    b = make_job(canonical_apply_url="https://boards.greenhouse.io/acme/jobs/1?utm_source=x")
    assert a.job_id == b.job_id


def test_url_canonicalization_strips_tracking():
    assert canonicalize_url("https://WWW.Example.com/jobs/1/?utm_source=li&gh_src=a") == \
        "https://example.com/jobs/1"


def test_relative_url_is_not_turned_into_an_absolute_one():
    assert canonicalize_url("jobs/1.html") == "jobs/1.html"


@pytest.mark.parametrize("raw,expected", [
    ("Senior AI Engineer (Remote, US) - Job ID 123", "senior ai engineer"),
    ("ML Engineer, New Grad", "ml engineer"),
    ("AI Engineer [Full-Time]", "ai engineer"),
])
def test_title_normalization(raw, expected):
    assert normalize_title(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Acme AI, Inc.", "acme"),
    ("Frontier Labs LLC", "frontier"),
    ("Nova Technologies Ltd", "nova"),
    ("OpenBrain", "openbrain"),
])
def test_company_normalization(raw, expected):
    assert normalize_company(raw) == expected


def test_duplicate_job_is_not_inserted_twice(db):
    first, is_new = db.upsert_job(make_job())
    assert is_new
    second, is_new_again = db.upsert_job(
        make_job(company="Acme AI Inc.", title="AI Engineer (Remote)",
                 canonical_apply_url="https://boards.greenhouse.io/acme/jobs/1?utm_source=x"))
    assert second == first and not is_new_again
    assert db.query_one("SELECT COUNT(*) AS n FROM jobs")["n"] == 1


def test_later_discovery_enriches_missing_fields(db):
    job_id, _ = db.upsert_job(make_job(description=""))
    db.upsert_job(make_job(description="Full description", salary_min=150000))
    row = db.query_one("SELECT description, salary_min FROM jobs WHERE job_id=?", (job_id,))
    assert row["description"] == "Full description"
    assert row["salary_min"] == 150000


def test_second_submission_for_a_job_is_refused(db):
    job = make_job()
    db.upsert_job(job)
    application_id = db.create_application(job.job_id, "run", dry_run=False)
    db.mark_submitted(application_id, "ATS-1")
    with pytest.raises(DuplicateSubmission):
        db.create_application(job.job_id, "run", dry_run=False)


def test_blocked_application_can_be_retried(db):
    job = make_job()
    db.upsert_job(job)
    first = db.create_application(job.job_id, "run", dry_run=True)
    db.update_application(first, status=JobStatus.BLOCKED, blocker_type="captcha")
    second = db.create_application(job.job_id, "run", dry_run=True)
    assert second != first
    assert db.get_application(second)["attempt"] == 2


def test_state_survives_reopening_the_database(config):
    job = make_job()
    first = Database(config.run.database_path)
    first.start_run("run-1", "apply", {})
    first.upsert_job(job)
    application_id = first.create_application(job.job_id, "run-1", dry_run=False)
    first.mark_verified(application_id, {"signal": "confirmation_message"}, "ATS-9")
    first.set_job_status(job.job_id, JobStatus.VERIFIED)
    first.close()

    # Simulate a crash and restart: a fresh process reads the same state.
    second = Database(config.run.database_path)
    assert second.has_successful_application(job.job_id)
    assert second.get_job(job.job_id).status == JobStatus.VERIFIED
    with pytest.raises(DuplicateSubmission):
        second.create_application(job.job_id, "run-2", dry_run=False)
    second.close()


def test_queue_survives_restart(config):
    first = Database(config.run.database_path)
    first.start_run("run-1", "dry-run", {})
    for i in range(3):
        job = make_job(canonical_apply_url=f"https://boards.greenhouse.io/acme/jobs/{i}",
                       ats_job_key=f"greenhouse:acme:{i}", title=f"AI Engineer {i}")
        first.upsert_job(job)
        first.save_score(job.job_id, 80, {}, "ok", JobStatus.QUEUED)
    first.close()

    second = Database(config.run.database_path)
    assert len(second.jobs_by_status(JobStatus.QUEUED)) == 3
    second.close()


def test_run_statistics_accumulate(db):
    db.bump_run("test-run", "verified", 2)
    db.add_run_cost("test-run", 0.25)
    row = db.get_run("test-run")
    assert row["verified"] == 2
    assert abs(row["qwen_cost_usd"] - 0.25) < 1e-9


def test_unknown_status_is_rejected(db):
    job = make_job()
    db.upsert_job(job)
    with pytest.raises(ValueError):
        db.set_job_status(job.job_id, "NOT_A_STATUS")


def test_errors_are_recorded_with_traceback(db):
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        db.record_error("test-run", "job1", "filling", exc)
    row = db.query_one("SELECT kind, message, traceback FROM errors")
    assert row["kind"] == "RuntimeError" and row["message"] == "boom"
    assert "RuntimeError" in row["traceback"]
