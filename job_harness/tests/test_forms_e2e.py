"""End-to-end tests that drive a real browser against the bundled ATS fixtures.

These prove the pipeline as a whole: form extraction, grounded filling, the
dry-run stop before Submit, real submission, verification evidence, blocker
handling and the refusal to submit twice.
"""
from __future__ import annotations

import pytest

from job_harness.application.field_resolver import FieldResolver
from job_harness.application.filler import ApplicationFiller
from job_harness.application.pipeline import ApplicationPipeline
from job_harness.browser.dom import extract_form
from job_harness.browser.engine import BrowserEngine
from job_harness.database.models import Job, JobStatus

pytestmark = [pytest.mark.browser, pytest.mark.slow]

ATS_FIXTURES = ["greenhouse_like.html", "lever_like.html",
                "ashby_like.html", "workday_like.html"]
BLOCKED_FIXTURES = [("captcha_form.html", "captcha"),
                    ("assessment_form.html", "assessment")]


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


# ------------------------------------------------------------------ extraction

def test_form_extraction_finds_labelled_fields(page, fixture_server):
    page.goto(fixture_server.url_for("greenhouse_like.html"))
    snapshot = extract_form(page)
    labels = [f.label for f in snapshot.fields]
    assert "First Name *" in labels
    assert any("authorized to work" in l for l in labels)
    assert snapshot.find_submit()["text"] == "Submit Application"


def test_radio_groups_collapse_to_one_question(page, fixture_server):
    page.goto(fixture_server.url_for("lever_like.html"))
    snapshot = extract_form(page)
    radios = [f for f in snapshot.fields if f.type == "radio"]
    assert radios, "expected radio groups"
    for group in radios:
        assert len(group.options) >= 2
        assert group.label.lower() not in ("yes", "no")     # not an option's own label


def test_hidden_steps_are_not_extracted(page, fixture_server):
    page.goto(fixture_server.url_for("workday_like.html"))
    labels = [f.label for f in extract_form(page).fields]
    assert any("First Name" in l for l in labels)
    assert not any("Resume" in l for l in labels)   # lives in a later, hidden step


def test_optional_fields_are_not_marked_required(page, fixture_server):
    page.goto(fixture_server.url_for("greenhouse_like.html"))
    by_label = {f.label: f for f in extract_form(page).fields}
    assert by_label["First Name *"].required
    assert not by_label["Phone"].required
    assert not by_label["Cover Letter"].required


def test_prompt_view_is_compact(page, fixture_server):
    page.goto(fixture_server.url_for("greenhouse_like.html"))
    snapshot = extract_form(page)
    payload = snapshot.to_prompt_json()
    # A structured form is far smaller than the raw page it came from.
    assert len(payload) < len(page.content())
    assert "<div" not in payload


# --------------------------------------------------------------------- dry run

@pytest.mark.parametrize("fixture", ATS_FIXTURES)
def test_dry_run_reaches_ready_to_submit(pipeline, db, fixture_server, fixture):
    job = job_for(fixture_server, fixture, fixture.split("_")[0].title())
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=True)
    assert result.status == JobStatus.READY_TO_SUBMIT, result.detail
    assert result.filled >= 5


def test_dry_run_does_not_submit(pipeline, db, browser, fixture_server):
    job = job_for(fixture_server, "greenhouse_like.html", "Acme AI")
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=True)
    application = db.get_application(result.application_id)
    assert application["status"] == JobStatus.READY_TO_SUBMIT
    assert application["submitted_at"] is None
    assert application["ats_application_id"] is None
    # The form was never advanced to a confirmation page. Check the rendered
    # text, not the source: the fixture's success handler contains that string.
    check = browser.new_page()
    browser.goto(check, fixture_server.url_for("greenhouse_like.html"))
    assert "Thank you" not in check.inner_text("body")
    assert check.locator("#application_form").count() == 1
    browser.close_page(check)


def test_dry_run_never_invents_an_unknown_answer(pipeline, db, fixture_server):
    job = job_for(fixture_server, "greenhouse_like.html", "Acme AI")
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=True)
    answers = {row["label"]: row for row in db.answers_for(result.application_id)}
    clearance = next(row for label, row in answers.items() if "clearance" in label.lower())
    assert not clearance["filled"]
    assert not clearance["safe_to_submit"]
    assert not clearance["answer"]


def test_grounded_answers_record_their_source(pipeline, db, fixture_server, applicant):
    job = job_for(fixture_server, "greenhouse_like.html", "Acme AI")
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=True)
    rows = {row["field_key"]: row for row in db.answers_for(result.application_id)}
    email = next(row for row in rows.values() if row["answer"] == applicant.get("email"))
    assert email["source"] == "profile.email"
    assert email["confidence"] >= 0.9


# --------------------------------------------------------------------- apply

@pytest.mark.parametrize("fixture", ATS_FIXTURES)
def test_apply_mode_submits_and_verifies(pipeline, db, fixture_server, fixture):
    job = job_for(fixture_server, fixture, fixture.split("_")[0].title())
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=False)
    assert result.status == JobStatus.VERIFIED, result.detail
    application = db.get_application(result.application_id)
    assert application["submitted_at"] is not None
    assert application["verified_at"] is not None
    assert application["confirmation_text"]


def test_confirmation_id_is_captured(pipeline, db, fixture_server):
    job = job_for(fixture_server, "ashby_like.html", "Nova")
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=False)
    assert db.get_application(result.application_id)["ats_application_id"] == "NOVA-778812"


def test_a_job_is_never_submitted_twice(pipeline, db, fixture_server):
    job = job_for(fixture_server, "greenhouse_like.html", "Acme AI")
    db.upsert_job(job)
    first = pipeline.process(job, dry_run=False)
    assert first.status == JobStatus.VERIFIED
    second = pipeline.process(job, dry_run=False)
    assert second.status == JobStatus.DUPLICATE
    assert db.query_one(
        "SELECT COUNT(*) AS n FROM applications WHERE job_id=? AND submitted_at IS NOT NULL",
        (job.job_id,))["n"] == 1


# ------------------------------------------------------------------- blockers

@pytest.mark.parametrize("fixture,kind", BLOCKED_FIXTURES)
def test_blockers_stop_the_application(pipeline, db, fixture_server, fixture, kind):
    job = job_for(fixture_server, fixture, "Blocked Co")
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=False)
    assert result.status == JobStatus.BLOCKED
    assert result.blocker == kind
    application = db.get_application(result.application_id)
    assert application["submitted_at"] is None


def test_blocked_state_is_recorded_for_resume(pipeline, db, fixture_server):
    job = job_for(fixture_server, "captcha_form.html", "SecureCo")
    db.upsert_job(job)
    pipeline.process(job, dry_run=False)
    assert db.get_job(job.job_id).status == JobStatus.BLOCKED
    row = db.query_one("SELECT blocker_type, checkpoint FROM applications WHERE job_id=?",
                       (job.job_id,))
    assert row["blocker_type"] == "captcha"


# --------------------------------------------------------------- form failures

def test_missing_required_answer_blocks_instead_of_guessing(
        config, db, browser, qwen, applicant, fixture_server):
    # An applicant with no work-authorization statement cannot answer that question.
    applicant.data["work_authorization"] = ""
    applicant.data["sponsorship_requirement"] = ""
    resolver = FieldResolver(applicant, qwen, config.run.min_field_confidence,
                             config.run.resume_path)
    pipeline = ApplicationPipeline(config, db, browser, qwen, resolver, "test-run")
    job = job_for(fixture_server, "greenhouse_like.html", "Acme AI")
    db.upsert_job(job)
    result = pipeline.process(job, dry_run=True)
    assert result.status == JobStatus.BLOCKED
    assert db.get_application(result.application_id)["blocker_type"] == "missing_answer"
