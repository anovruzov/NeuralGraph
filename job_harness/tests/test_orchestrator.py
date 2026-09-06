"""Campaign-level behaviour: limits, pausing, resilience and resume."""
from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from job_harness.dashboard.server import DashboardServer
from job_harness.dashboard.stats import collect
from job_harness.database.models import Job, JobStatus
from job_harness.orchestrator import (
    Orchestrator, RateLimiter, STATE_PAUSED, STATE_RUNNING, STATE_STOPPED,
)


def make_job(index: int, **kw) -> Job:
    base = dict(company=f"Company {index}", title="AI Engineer",
                canonical_apply_url=f"https://boards.greenhouse.io/c{index}/jobs/1",
                ats_type="greenhouse", ats_job_key=f"greenhouse:c{index}:1",
                posted_date="2026-09-04", location="Remote - US",
                description="Build LLM agents in Python. 2+ years of experience. " * 20)
    base.update(kw)
    return Job(**base)


@pytest.fixture
def orchestrator(config, db, qwen, applicant) -> Orchestrator:
    return Orchestrator(config, applicant, qwen, db, run_id="test-run")


# ----------------------------------------------------------------- rate limits

def test_rate_limiter_allows_up_to_the_cap():
    limiter = RateLimiter(3)
    for _ in range(3):
        assert limiter.wait_time() == 0
        limiter.record()
    assert limiter.wait_time() > 0


def test_rate_limiter_is_disabled_at_zero():
    limiter = RateLimiter(0)
    for _ in range(50):
        limiter.record()
    assert limiter.wait_time() == 0


def test_rate_limiter_forgets_old_events():
    limiter = RateLimiter(1)
    limiter.seed([time.time() - 4000])
    assert limiter.wait_time() == 0


# --------------------------------------------------------------------- scoring

def test_score_pending_queues_and_skips(orchestrator, db):
    good = make_job(1)
    senior = make_job(2, title="Director of Engineering")
    db.upsert_job(good)
    db.upsert_job(senior)
    orchestrator.score_pending()
    assert db.get_job(good.job_id).status == JobStatus.QUEUED
    assert db.get_job(senior.job_id).status == JobStatus.SKIPPED


def test_run_min_score_overrides_a_queued_decision(config, db, qwen, applicant):
    config.run.min_score = 99
    orchestrator = Orchestrator(config, applicant, qwen, db, run_id="test-run")
    db.upsert_job(make_job(1))
    orchestrator.score_pending()
    job = db.get_job(make_job(1).job_id)
    assert job.status == JobStatus.SKIPPED
    assert "min_score" in job.score_reason


def test_scoring_failure_does_not_stop_the_campaign(orchestrator, db, monkeypatch):
    db.upsert_job(make_job(1))
    db.upsert_job(make_job(2))
    calls = {"n": 0}
    original = orchestrator.scorer.score

    def flaky(job, today=None, classification=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scorer exploded")
        return original(job, today, classification)

    monkeypatch.setattr(orchestrator.scorer, "score", flaky)
    orchestrator.score_pending()
    statuses = {row["status"] for row in db.query("SELECT status FROM jobs")}
    assert JobStatus.FAILED in statuses          # the broken one
    assert JobStatus.QUEUED in statuses          # the campaign continued
    assert db.query_one("SELECT COUNT(*) AS n FROM errors")["n"] == 1


# ---------------------------------------------------------------------- limits

def test_company_cap_skips_further_jobs(config, db, qwen, applicant):
    config.run.max_applications_per_company = 1
    orchestrator = Orchestrator(config, applicant, qwen, db, run_id="test-run")
    job = make_job(1)
    db.upsert_job(job)
    application_id = db.create_application(job.job_id, "test-run", dry_run=False)
    db.mark_submitted(application_id, "ATS-1")

    other = make_job(1, canonical_apply_url="https://boards.greenhouse.io/c1/jobs/2",
                     ats_job_key="greenhouse:c1:2", title="LLM Engineer")
    db.upsert_job(other)
    assert not orchestrator._company_capacity(other)
    assert db.get_job(other.job_id).status == JobStatus.SKIPPED


def test_application_limit_is_respected(config, db, qwen, applicant):
    config.run.max_applications = 2
    orchestrator = Orchestrator(config, applicant, qwen, db, run_id="test-run")
    orchestrator.summary.ready_to_submit = 2
    assert orchestrator._limit_reached()


# ----------------------------------------------------------------------- state

def test_stop_is_honoured(orchestrator, db):
    orchestrator.request_stop("test")
    assert db.get_control("harness_state") == STATE_STOPPED
    assert orchestrator._stop.is_set()


def test_pause_blocks_until_resumed(orchestrator, db):
    db.set_control("harness_state", STATE_PAUSED)
    done = threading.Event()

    def resume_soon():
        time.sleep(0.4)
        db.set_control("harness_state", STATE_RUNNING)

    threading.Thread(target=resume_soon, daemon=True).start()
    started = time.time()
    orchestrator._paused_wait()
    done.set()
    assert time.time() - started >= 0.3
    assert db.get_control("harness_state") == STATE_RUNNING


def test_interruptible_sleep_returns_on_stop(orchestrator):
    timer = threading.Timer(0.2, orchestrator.request_stop)
    timer.start()
    try:
        started = time.time()
        assert orchestrator._sleep(10) is True
        assert time.time() - started < 5
    finally:
        timer.cancel()
        timer.join(timeout=2)


def test_stop_during_shutdown_does_not_raise(config, db, qwen, applicant):
    orchestrator = Orchestrator(config, applicant, qwen, db, run_id="test-run")
    db.close()
    orchestrator.request_stop("after shutdown")       # must not raise
    assert orchestrator._stop.is_set()


# ------------------------------------------------------------------- dashboard

@pytest.fixture
def dashboard(config, db):
    config.dashboard.auth_token = "test-token"
    config.dashboard.port = 0
    server = DashboardServer(config, db)
    server.config.dashboard.port = 8799
    server.start()
    yield server
    server.stop()


def request(path: str, token: str | None = None, method: str = "GET", body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:8799{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Auth-Token": token, "Content-Type": "application/json"} if token
        else {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except ValueError:
        return 200, {}


def test_dashboard_requires_a_token(dashboard):
    assert request("/api/status")[0] == 401
    assert request("/api/status", token="wrong")[0] == 401
    assert request("/api/status", token="test-token")[0] == 200


def test_health_endpoint_is_unauthenticated(dashboard):
    assert request("/healthz")[0] == 200


def test_dashboard_control_actions(dashboard, db):
    for action, expected in [("pause", "PAUSED"), ("resume", "RUNNING"),
                             ("stop", "STOPPED"), ("start", "RUNNING")]:
        status, payload = request("/api/control", "test-token", "POST", {"action": action})
        assert status == 200 and payload["state"] == expected
        assert db.get_control("harness_state") == expected


def test_unknown_control_action_is_rejected(dashboard):
    status, payload = request("/api/control", "test-token", "POST", {"action": "explode"})
    assert status == 400 and not payload["ok"]


def test_dashboard_config_updates_apply(dashboard, config):
    status, payload = request("/api/config", "test-token", "POST",
                              {"min_score": 72, "remote_only": True,
                               "max_applications_per_hour": 5,
                               "target_roles": "AI Engineer, LLM Engineer"})
    assert status == 200
    assert config.run.min_score == 72
    assert config.scoring.apply_threshold == 72
    assert config.discovery.remote_only is True
    assert config.run.max_applications_per_hour == 5
    assert config.discovery.target_roles == ["AI Engineer", "LLM Engineer"]


def test_dashboard_config_values_are_clamped(dashboard, config):
    request("/api/config", "test-token", "POST", {"min_score": 5000})
    assert config.run.min_score == 100


def test_non_loopback_bind_without_a_token_is_refused(config):
    config.dashboard.enabled = True
    config.dashboard.host = "0.0.0.0"
    config.dashboard.auth_token = ""
    with pytest.raises(ValueError, match="auth_token"):
        config.validate()


def test_stats_snapshot_shape(db):
    data = collect(db)
    for key in ("run", "pipeline", "rates", "qwen", "recent", "blockers", "errors"):
        assert key in data
    for key in ("discovered", "scored", "queued", "submitted", "verified",
                "blocked", "failed", "duplicates"):
        assert key in data["pipeline"]


# ------------------------------------------------------------ retry blocked

def _blocked_job(db, index: int, blocker: str) -> Job:
    job = make_job(index)
    db.upsert_job(job)
    db.save_score(job.job_id, 80, {}, "test", JobStatus.BLOCKED)
    application_id = db.create_application(job.job_id, "test-run", dry_run=False)
    db.update_application(application_id, status=JobStatus.BLOCKED, blocker_type=blocker)
    return job


def test_recoverable_blocks_are_requeued(orchestrator, db):
    fixable = _blocked_job(db, 1, "missing_answer")
    assert orchestrator.retry_blocked() == 1
    assert db.get_job(fixable.job_id).status == JobStatus.QUEUED


@pytest.mark.parametrize("blocker", ["captcha", "assessment", "login_required",
                                     "video_interview", "identity_verification",
                                     "security_challenge", "bot_detection"])
def test_blocks_needing_a_person_are_never_requeued(orchestrator, db, blocker):
    job = _blocked_job(db, 1, blocker)
    assert orchestrator.retry_blocked() == 0
    assert db.get_job(job.job_id).status == JobStatus.BLOCKED


def test_a_submitted_job_is_never_requeued(orchestrator, db):
    job = _blocked_job(db, 1, "missing_answer")
    application_id = db.create_application(job.job_id, "test-run", dry_run=False)
    db.mark_submitted(application_id, "ATS-1")
    assert orchestrator.retry_blocked() == 0
    assert db.get_job(job.job_id).status == JobStatus.BLOCKED


def test_recoverable_and_human_blockers_are_disjoint():
    from job_harness.application import blockers as blocker_module
    assert not (blocker_module.RECOVERABLE & blocker_module.NEEDS_HUMAN)


# --------------------------------------------------- unverified submissions

def test_unverified_submissions_are_surfaced_for_review(db):
    job = make_job(1)
    db.upsert_job(job)
    application_id = db.create_application(job.job_id, "test-run", dry_run=False)
    db.mark_submitted(application_id, None)

    pending = db.unverified_submissions()
    assert len(pending) == 1
    assert pending[0]["company"] == job.company

    data = collect(db)
    assert data["pipeline"]["needs_review"] == 1
    assert data["needs_review"][0]["canonical_apply_url"] == job.canonical_apply_url


def test_verified_submissions_do_not_need_review(db):
    job = make_job(1)
    db.upsert_job(job)
    application_id = db.create_application(job.job_id, "test-run", dry_run=False)
    db.mark_submitted(application_id, "ATS-1")
    db.mark_verified(application_id, {"signal": "confirmation_message"}, "ATS-1")
    assert db.unverified_submissions() == []
    assert collect(db)["pipeline"]["needs_review"] == 0


# ------------------------------------------------------- score floor config

def test_min_score_floor_defaults_below_the_apply_threshold(config):
    """Otherwise the documented 50-64 borderline band could never apply."""
    assert config.run.min_score <= config.scoring.apply_threshold
    assert config.run.min_score <= config.scoring.borderline_threshold


def test_a_floor_above_the_threshold_is_refused(config):
    config.run.min_score = config.scoring.apply_threshold + 10
    with pytest.raises(ValueError, match="min_score"):
        config.validate()


# --------------------------------------------------------- secret handling

def test_credentials_are_redacted_in_the_config_snapshot(config):
    """The snapshot is written to run_statistics and served by the dashboard."""
    config.qwen.api_key = "sk-super-secret"
    config.dashboard.auth_token = "token-super-secret"
    blob = json.dumps(config.to_dict())
    assert "sk-super-secret" not in blob
    assert "token-super-secret" not in blob
    assert config.to_dict()["qwen"]["api_key"] == "***"


def test_the_dashboard_never_serves_credentials(dashboard, config):
    config.qwen.api_key = "sk-super-secret"
    _, payload = request("/api/status", "test-token")
    assert "sk-super-secret" not in json.dumps(payload)
    assert "test-token" not in json.dumps(payload)


def test_the_response_cache_stores_no_prompt_text(db, qwen):
    """Prompts carry the applicant's personal data; only responses are cached."""
    qwen.classify_job({"title": "AI Engineer", "company": "Acme",
                       "location": "Remote", "description": "LLM agents"},
                      ["AI Engineer"])
    rows = db.query("SELECT cache_key, response FROM qwen_cache")
    assert rows
    for row in rows:
        assert "Acme" not in row["cache_key"]
        assert "applicant" not in (row["response"] or "").lower()
