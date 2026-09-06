"""Campaign orchestrator: DISCOVER -> SCORE -> QUEUE -> APPLY -> VERIFY -> LOG -> NEXT.

Designed to run unattended for hours. Every job is processed inside an error
boundary, state lives in SQLite, and a restart resumes from the queue rather
than starting over.
"""
from __future__ import annotations

import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .application.field_resolver import FieldResolver
from .application.pipeline import ApplicationPipeline, JobResult
from .browser.engine import BrowserEngine
from .config.logging_setup import get_logger
from .config.settings import Config
from .database.db import Database
from .database.models import Job, JobStatus, utcnow
from .discovery.engine import DiscoveryEngine
from .profile.applicant import Applicant
from .qwen.qwen_client import QwenClient
from .scoring.scorer import ScoreDecision, Scorer

log = get_logger("orchestrator")

STATE_RUNNING = "RUNNING"
STATE_PAUSED = "PAUSED"
STATE_STOPPED = "STOPPED"
STATE_FINISHED = "FINISHED"

CONTROL_KEY = "harness_state"


@dataclass
class CampaignSummary:
    run_id: str
    discovered: int = 0
    scored: int = 0
    queued: int = 0
    skipped: int = 0
    ready_to_submit: int = 0
    submitted: int = 0
    verified: int = 0
    blocked: int = 0
    failed: int = 0
    duplicates: int = 0
    elapsed_s: float = 0.0
    stop_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class RateLimiter:
    """Caps applications per hour using a sliding window of submission times."""

    def __init__(self, max_per_hour: int) -> None:
        self.max_per_hour = max_per_hour
        self._events: list[float] = []

    def seed(self, timestamps: list[float]) -> None:
        self._events = [t for t in timestamps if t > time.time() - 3600]

    def wait_time(self) -> float:
        if self.max_per_hour <= 0:
            return 0.0
        cutoff = time.time() - 3600
        self._events = [t for t in self._events if t > cutoff]
        if len(self._events) < self.max_per_hour:
            return 0.0
        return max(0.0, (self._events[0] + 3600) - time.time())

    def record(self) -> None:
        self._events.append(time.time())


class Orchestrator:
    def __init__(self, config: Config, applicant: Applicant, qwen: QwenClient,
                 db: Database, run_id: Optional[str] = None) -> None:
        self.config = config
        self.applicant = applicant
        self.qwen = qwen
        self.db = db
        self.run_id = run_id or f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        self.qwen.run_id = self.run_id
        self.dry_run = config.run.mode != "apply"
        self.limiter = RateLimiter(config.run.max_applications_per_hour)
        self.discovery = DiscoveryEngine(config, db, qwen, self.run_id)
        self.scorer = Scorer(config, qwen, applicant)
        self.resolver = FieldResolver(applicant, qwen, config.run.min_field_confidence,
                                      config.run.resume_path)
        self.browser: Optional[BrowserEngine] = None
        self.summary = CampaignSummary(run_id=self.run_id)
        self._stop = threading.Event()
        self._started = time.time()

    # ---------------------------------------------------------------- state

    @property
    def state(self) -> str:
        try:
            return self.db.get_control(CONTROL_KEY, STATE_RUNNING) or STATE_RUNNING
        except Exception:
            return STATE_STOPPED       # database gone: treat as a stop, never spin

    def set_state(self, state: str) -> None:
        # A stop can arrive from a signal handler while shutdown is already in
        # progress, so persisting the new state must never raise.
        try:
            self.db.set_control(CONTROL_KEY, state)
            self.db.set_run_context(self.run_id, state=state)
        except Exception as exc:
            log.warning("could not persist state change",
                        extra={"state": state, "error": str(exc)[:200]})
        log.info("harness state changed", extra={"state": state})

    def request_stop(self, reason: str = "stop requested") -> None:
        self.summary.stop_reason = reason
        self._stop.set()
        self.set_state(STATE_STOPPED)

    def install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: Any) -> None:
            log.warning("signal received; finishing current job then stopping",
                        extra={"signal": signum})
            self.request_stop(f"signal {signum}")
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass       # not the main thread

    # ------------------------------------------------------------ lifecycle

    def run(self, discover: bool = True) -> CampaignSummary:
        self.db.start_run(self.run_id, self.config.run.mode, self.config.to_dict())
        self.set_state(STATE_RUNNING)
        self.limiter.seed(self._recent_submission_times())
        log.info("campaign starting",
                 extra={"run_id": self.run_id, "mode": self.config.run.mode,
                        "max_applications": self.config.run.max_applications,
                        "min_score": self.config.run.min_score})
        try:
            self._loop(discover)
        except KeyboardInterrupt:
            self.summary.stop_reason = "interrupted"
            log.warning("campaign interrupted")
        except Exception as exc:
            self.summary.stop_reason = f"fatal: {exc}"
            log.error("campaign failed", extra={"error": str(exc)[:300]}, exc_info=True)
            self.db.record_error(self.run_id, None, "orchestrator", exc)
        finally:
            self._shutdown()
        self.summary.elapsed_s = time.time() - self._started
        return self.summary

    def _shutdown(self) -> None:
        if self.browser is not None:
            try:
                self.browser.stop()
            except Exception:
                pass
            self.browser = None
        self.discovery.close()
        final = STATE_STOPPED if self._stop.is_set() else STATE_FINISHED
        self.db.set_control(CONTROL_KEY, final)
        self.db.set_run_context(self.run_id, state=final, ended_at=utcnow(),
                                current_state="STOPPED")
        stats = self.qwen.stats
        log.info("campaign finished",
                 extra={"run_id": self.run_id, "verified": self.summary.verified,
                        "ready_to_submit": self.summary.ready_to_submit,
                        "blocked": self.summary.blocked, "failed": self.summary.failed,
                        "qwen_requests": stats["requests"],
                        "qwen_cost_usd": round(stats["cost_usd"], 4)})

    # --------------------------------------------------------------- phases

    def _loop(self, discover: bool) -> None:
        last_discovery = 0.0
        while not self._stop.is_set():
            if self._paused_wait():
                continue

            if discover and (time.time() - last_discovery) >= self.config.run.discovery_interval_seconds:
                self.discover()
                last_discovery = time.time()

            self.score_pending()

            processed = self.apply_queued()

            if self._stop.is_set():
                break
            if self._limit_reached():
                self.summary.stop_reason = self.summary.stop_reason or "application limit reached"
                break

            if processed == 0:
                if not self.config.run.loop_forever:
                    self.summary.stop_reason = self.summary.stop_reason or "queue empty"
                    break
                log.info("queue empty; waiting for the next discovery cycle",
                         extra={"sleep_s": self.config.run.poll_interval_seconds})
                if self._sleep(self.config.run.poll_interval_seconds):
                    break

    def discover(self) -> int:
        self.db.set_run_context(self.run_id, current_state="DISCOVERING")
        targets = self.discovery.targets()
        if not targets:
            log.warning("no discovery targets configured; "
                        "set discovery.boards or discovery.seed_urls")
            return 0
        report = self.discovery.run()
        self.summary.discovered += report.discovered
        self.summary.duplicates += report.duplicates
        return report.discovered

    def score_pending(self) -> int:
        self.db.set_run_context(self.run_id, current_state="SCORING")
        pending = self.db.unscored_jobs(limit=200)
        if not pending:
            return 0

        # One batched relevance pass settles the obvious rejects cheaply; only
        # the survivors cost a full rubric call.
        prescreened: dict[str, Any] = {}
        try:
            prescreened = self.scorer.prescreen(pending)
        except Exception as exc:
            log.warning("batch prescreen failed; falling back to per-job scoring",
                        extra={"error": str(exc)[:200]})

        count = 0
        for job in pending:
            if self._stop.is_set() or self._paused_wait():
                break
            settled = prescreened.get(job.job_id)
            try:
                if isinstance(settled, ScoreDecision):
                    decision = settled
                else:
                    decision = self.scorer.score(job, classification=settled)
            except Exception as exc:
                log.error("scoring failed", extra={"job_id": job.job_id,
                                                   "error": str(exc)[:200]})
                self.db.record_error(self.run_id, job.job_id, "scoring", exc)
                self.db.set_job_status(job.job_id, JobStatus.FAILED)
                self.summary.failed += 1
                continue

            status = decision.status
            if status == JobStatus.QUEUED and decision.score < self.config.run.min_score:
                status = JobStatus.SKIPPED
                decision.reason += f" | below run min_score {self.config.run.min_score}"

            self.db.save_score(job.job_id, decision.score, decision.breakdown,
                               decision.reason, status)
            self.db.bump_run(self.run_id, "scored")
            self.summary.scored += 1
            count += 1
            if status == JobStatus.QUEUED:
                self.db.bump_run(self.run_id, "queued")
                self.summary.queued += 1
            else:
                self.db.bump_run(self.run_id, "skipped")
                self.summary.skipped += 1
        if count:
            log.info("scoring pass complete",
                     extra={"scored": count, "queued": self.summary.queued,
                            "skipped": self.summary.skipped})
        return count

    def apply_queued(self) -> int:
        queue = self.db.jobs_by_status(JobStatus.QUEUED, limit=200)
        if not queue:
            return 0
        self._ensure_browser()
        pipeline = ApplicationPipeline(self.config, self.db, self.browser, self.qwen,
                                       self.resolver, self.run_id)
        processed = 0

        for job in queue:
            if self._stop.is_set():
                break
            if self._paused_wait():
                continue
            if self._limit_reached():
                break
            if not self._company_capacity(job):
                continue

            wait = self.limiter.wait_time()
            if wait > 0:
                log.info("rate limit reached; waiting",
                         extra={"wait_s": round(wait), "max_per_hour":
                                self.config.run.max_applications_per_hour})
                if self._sleep(min(wait, 60)):
                    break
                continue

            result = self._process_with_timeout(pipeline, job)
            processed += 1
            self._tally(result)
            if result.status in (JobStatus.SUBMITTED, JobStatus.VERIFIED,
                                 JobStatus.READY_TO_SUBMIT):
                self.limiter.record()

        return processed

    # --------------------------------------------------------------- helpers

    def _process_with_timeout(self, pipeline: ApplicationPipeline, job: Job) -> JobResult:
        """Run one job. The pipeline has its own error boundary; this adds a wall clock."""
        started = time.time()
        result = pipeline.process(job, self.dry_run)
        elapsed = time.time() - started
        if elapsed > self.config.run.job_timeout_seconds:
            log.warning("job exceeded its time budget",
                        extra={"job_id": job.job_id, "elapsed_s": round(elapsed)})
            # A slow page can leave the browser wedged; recycle it before the next job.
            try:
                if self.browser and not self.browser.healthy():
                    self.browser.recover()
            except Exception:
                pass
        return result

    def _tally(self, result: JobResult) -> None:
        mapping = {
            JobStatus.VERIFIED: "verified", JobStatus.SUBMITTED: "submitted",
            JobStatus.READY_TO_SUBMIT: "ready_to_submit", JobStatus.BLOCKED: "blocked",
            JobStatus.FAILED: "failed", JobStatus.DUPLICATE: "duplicates",
        }
        attr = mapping.get(result.status)
        if attr:
            setattr(self.summary, attr, getattr(self.summary, attr) + 1)
        if result.status == JobStatus.VERIFIED:
            self.summary.submitted += 1

    def _limit_reached(self) -> bool:
        done = (self.summary.verified + self.summary.submitted
                if not self.dry_run else self.summary.ready_to_submit)
        return done >= self.config.run.max_applications > 0

    def _company_capacity(self, job: Job) -> bool:
        cap = self.config.run.max_applications_per_company
        if cap <= 0:
            return True
        used = self.db.company_application_count(job.normalized_company)
        if used >= cap:
            log.info("company cap reached; skipping",
                     extra={"company": job.company, "cap": cap})
            self.db.set_job_status(job.job_id, JobStatus.SKIPPED)
            self.db.bump_run(self.run_id, "skipped")
            self.summary.skipped += 1
            return False
        return True

    def _ensure_browser(self) -> None:
        if self.browser is None:
            self.browser = BrowserEngine(self.config.browser, self.db, self.run_id).start()
        elif not self.browser.healthy():
            self.browser.recover()

    def _paused_wait(self) -> bool:
        """Block while the dashboard has the run paused. True if state changed."""
        if self.state != STATE_PAUSED:
            if self.state == STATE_STOPPED:
                self._stop.set()
                return True
            return False
        log.info("paused; waiting for RESUME")
        self.db.set_run_context(self.run_id, current_state="PAUSED")
        while self.state == STATE_PAUSED and not self._stop.is_set():
            if self._sleep(2):
                return True
        if self.state == STATE_STOPPED:
            self._stop.set()
        log.info("resumed")
        return True

    def _sleep(self, seconds: float) -> bool:
        """Interruptible sleep. Returns True if a stop was requested."""
        return self._stop.wait(timeout=seconds)

    def _recent_submission_times(self) -> list[float]:
        rows = self.db.query(
            "SELECT submitted_at FROM applications WHERE submitted_at IS NOT NULL")
        out: list[float] = []
        for row in rows:
            try:
                out.append(datetime.fromisoformat(row["submitted_at"]).timestamp())
            except (TypeError, ValueError):
                continue
        return out
