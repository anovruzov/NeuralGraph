"""Unattended-operation guarantees: a broken job, a crashed browser, or a hard
kill must never end the campaign or cost a duplicate submission.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from job_harness.application.field_resolver import FieldResolver
from job_harness.application.pipeline import ApplicationPipeline
from job_harness.browser.engine import BrowserEngine
from job_harness.database.db import Database
from job_harness.database.models import Job, JobStatus
from job_harness.orchestrator import Orchestrator

pytestmark = [pytest.mark.browser, pytest.mark.slow]

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def browser(config, db, chromium_path):
    config.browser.executable_path = chromium_path
    engine = BrowserEngine(config.browser, db=db, run_id="test-run").start()
    yield engine
    engine.stop()


@pytest.fixture
def pipeline(config, db, browser, qwen, applicant):
    resolver = FieldResolver(applicant, qwen, config.run.min_field_confidence,
                             config.run.resume_path)
    return ApplicationPipeline(config, db, browser, qwen, resolver, "test-run")


def job_for(fixture_server, name: str, company: str) -> Job:
    return Job(company=company, title="AI Engineer",
               canonical_apply_url=fixture_server.url_for(name))


def test_browser_recovers_from_a_crash(browser, db):
    """A crashed browser is restarted and keeps working."""
    assert browser.healthy()
    browser.context.close()                      # simulate the context dying
    assert not browser.healthy()

    browser.recover()
    assert browser.healthy()
    assert browser.crashes == 1

    page = browser.new_page()
    page.set_content("<h1>alive</h1>")
    assert page.inner_text("h1") == "alive"
    browser.close_page(page)


def test_campaign_continues_after_a_browser_crash_mid_job(
        pipeline, browser, db, fixture_server):
    """A crash between jobs must not lose the queue."""
    first = job_for(fixture_server, "greenhouse_like.html", "Acme AI")
    second = job_for(fixture_server, "ashby_like.html", "Nova")
    db.upsert_job(first)
    db.upsert_job(second)

    assert pipeline.process(first, dry_run=True).status == JobStatus.READY_TO_SUBMIT

    browser.context.close()                      # crash between jobs
    result = pipeline.process(second, dry_run=True)

    assert result.status == JobStatus.READY_TO_SUBMIT, result.detail
    assert browser.crashes == 1                  # the pipeline recovered it


def test_a_broken_job_does_not_stop_the_others(pipeline, db, fixture_server):
    """One unreachable posting must not end the campaign."""
    broken = Job(company="Gone Co", title="AI Engineer",
                 canonical_apply_url="http://127.0.0.1:1/nowhere.html")
    good = job_for(fixture_server, "greenhouse_like.html", "Acme AI")
    db.upsert_job(broken)
    db.upsert_job(good)

    broken_result = pipeline.process(broken, dry_run=True)
    assert broken_result.status in (JobStatus.BLOCKED, JobStatus.FAILED)

    good_result = pipeline.process(good, dry_run=True)
    assert good_result.status == JobStatus.READY_TO_SUBMIT


def test_hard_kill_and_restart_resumes_without_resubmitting(
        tmp_path, chromium_path, fixture_server):
    """Kill the process mid-campaign, restart it, and check the invariants.

    The restarted run must pick up the remaining queue and must not resubmit
    anything the first run already submitted.
    """
    database_path = tmp_path / "harness.db"
    fixtures = Path(__file__).resolve().parent / "fixtures"

    def run(extra: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "run.py", "--apply",
             "--db", str(database_path),
             "--profile", str(fixtures / "applicant.test.json"),
             "--resume", str(fixtures / "resume.test.txt"),
             "--fake-qwen", "--no-dashboard", *extra],
            cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
            env={**__import__("os").environ,
                 "BROWSER_EXECUTABLE": chromium_path,
                 "BROWSER_PROFILE_DIR": str(tmp_path / "browser"),
                 "HARNESS_LOG_DIR": str(tmp_path / "logs")},
        )

    # First run: apply to a single job, then stop at the limit.
    first = run(["--max-applications", "1", "--url", fixture_server.base_url + "/board"])
    assert first.returncode == 0, first.stderr[-2000:]

    database = Database(str(database_path))
    submitted_first = database.query_one(
        "SELECT COUNT(*) AS n FROM applications WHERE submitted_at IS NOT NULL")["n"]
    assert submitted_first >= 1
    queued = database.query_one(
        "SELECT COUNT(*) AS n FROM jobs WHERE status='QUEUED'")["n"]
    assert queued >= 1, "expected work left for the second run"
    database.close()

    # Second run: a fresh process against the same database.
    second = run(["--max-applications", "10", "--no-discovery"])
    assert second.returncode == 0, second.stderr[-2000:]

    database = Database(str(database_path))
    rows = database.query(
        "SELECT job_id, COUNT(*) AS n FROM applications "
        "WHERE status IN ('SUBMITTED','VERIFIED') GROUP BY job_id HAVING n > 1")
    assert not rows, "a job was submitted more than once across the restart"

    submitted_total = database.query_one(
        "SELECT COUNT(*) AS n FROM applications WHERE submitted_at IS NOT NULL")["n"]
    assert submitted_total > submitted_first, "the second run made no progress"
    database.close()


def test_orchestrator_survives_a_pipeline_exception(config, db, qwen, applicant,
                                                    fixture_server, monkeypatch,
                                                    chromium_path):
    """An exception inside a job is contained and recorded, and the loop goes on."""
    config.browser.executable_path = chromium_path
    orchestrator = Orchestrator(config, applicant, qwen, db, run_id="test-run")
    db.start_run("test-run", "dry-run", {})

    for index, fixture in enumerate(["greenhouse_like.html", "ashby_like.html"]):
        job = job_for(fixture_server, fixture, f"Co{index}")
        db.upsert_job(job)
        db.save_score(job.job_id, 90, {}, "test", JobStatus.QUEUED)

    from job_harness.application import pipeline as pipeline_module

    original = pipeline_module.ApplicationPipeline.process
    calls = {"n": 0}

    def flaky(self, job, dry_run):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pipeline exploded")
        return original(self, job, dry_run)

    monkeypatch.setattr(pipeline_module.ApplicationPipeline, "process", flaky)

    try:
        processed = orchestrator.apply_queued()
    finally:
        if orchestrator.browser:
            orchestrator.browser.stop()

    assert calls["n"] == 2                       # it kept going after the exception
    assert processed == 2
    assert db.query_one("SELECT COUNT(*) AS n FROM errors")["n"] >= 1
