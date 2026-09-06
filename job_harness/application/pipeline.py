"""Per-job application pipeline with a hard error boundary.

One broken application must never end the campaign, so every stage here is
wrapped: any exception becomes a FAILED job and the loop continues.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from ..ats.detect import apply_url_for, detect
from ..browser.engine import BrowserEngine
from ..config.logging_setup import get_logger
from ..config.settings import Config
from ..database.db import Database, DuplicateSubmission
from ..database.models import Job, JobStatus
from ..qwen.qwen_client import QwenClient
from . import blockers
from .field_resolver import FieldResolver
from .filler import ApplicationFiller, FillOutcome

log = get_logger("application.pipeline")


@dataclass
class JobResult:
    job_id: str
    status: str
    detail: str = ""
    application_id: Optional[int] = None
    filled: int = 0
    duration_s: float = 0.0
    blocker: Optional[str] = None


class ApplicationPipeline:
    def __init__(self, config: Config, db: Database, browser: BrowserEngine,
                 qwen: QwenClient, resolver: FieldResolver, run_id: str) -> None:
        self.config = config
        self.db = db
        self.browser = browser
        self.qwen = qwen
        self.run_id = run_id
        self.filler = ApplicationFiller(config, db, browser, resolver, qwen, run_id)

    def process(self, job: Job, dry_run: bool) -> JobResult:
        started = time.time()
        page = None
        application_id: Optional[int] = None

        if self.db.has_successful_application(job.job_id):
            self.db.set_job_status(job.job_id, JobStatus.DUPLICATE)
            return JobResult(job.job_id, JobStatus.DUPLICATE,
                             "already submitted in an earlier run")

        try:
            application_id = self.db.create_application(job.job_id, self.run_id, dry_run)
        except DuplicateSubmission as exc:
            self.db.set_job_status(job.job_id, JobStatus.DUPLICATE)
            return JobResult(job.job_id, JobStatus.DUPLICATE, str(exc))

        self.db.set_job_status(job.job_id, JobStatus.OPENED)
        self.db.set_run_context(self.run_id, current_company=job.company,
                                current_role=job.title, current_state="OPENING")

        try:
            if not self.browser.healthy():
                self.browser.recover()

            page = self.browser.new_page()
            target = apply_url_for(job.canonical_apply_url,
                                   job.ats_type or detect(job.canonical_apply_url).ats_type)
            if not self.browser.goto(page, target):
                return self._fail(job, application_id, JobStatus.BLOCKED,
                                  "navigation failed", blockers.NAVIGATION_FAILED,
                                  started)

            self.db.set_job_status(job.job_id, JobStatus.FILLING)
            self.db.set_run_context(self.run_id, current_state="FILLING")
            self.db.update_application(application_id, status=JobStatus.FILLING)

            outcome: FillOutcome = self.filler.apply_to(page, job, application_id, dry_run)
            return self._record_outcome(job, application_id, outcome, started)

        except Exception as exc:
            log.error("application pipeline error",
                      extra={"job_id": job.job_id, "error": str(exc)[:300]}, exc_info=True)
            self.db.record_error(self.run_id, job.job_id, "pipeline", exc)
            return self._fail(job, application_id, JobStatus.FAILED, str(exc)[:300],
                              None, started)
        finally:
            self.browser.close_page(page)
            self.db.set_run_context(self.run_id, current_state="IDLE")

    # ---------------------------------------------------------------- state

    def _record_outcome(self, job: Job, application_id: int, outcome: FillOutcome,
                        started: float) -> JobResult:
        duration = time.time() - started
        checkpoint = {
            "steps": outcome.steps, "filled": outcome.filled, "skipped": outcome.skipped,
            "screenshot": outcome.screenshot,
            "blocked_fields": [
                {"field": a.field_key, "semantic_key": a.semantic_key,
                 "reason": a.blocked_reason[:200]} for a in outcome.blocked_fields[:10]
            ],
        }

        if outcome.status == JobStatus.READY_TO_SUBMIT:
            self.db.update_application(application_id, status=JobStatus.READY_TO_SUBMIT,
                                       checkpoint=checkpoint)
            self.db.set_job_status(job.job_id, JobStatus.READY_TO_SUBMIT)
            self.db.bump_run(self.run_id, "ready_to_submit")
            self.db.bump_run(self.run_id, "opened")

        elif outcome.status == JobStatus.VERIFIED:
            evidence = outcome.evidence.to_dict() if outcome.evidence else {}
            self.db.mark_submitted(application_id,
                                   outcome.evidence.application_id if outcome.evidence else None)
            self.db.mark_verified(
                application_id, {**evidence, **checkpoint},
                ats_application_id=outcome.evidence.application_id if outcome.evidence else None,
                confirmation_text=(outcome.evidence.confirmation_text if outcome.evidence else ""),
                confirmation_url=(outcome.evidence.confirmation_url if outcome.evidence else ""),
            )
            self.db.set_job_status(job.job_id, JobStatus.VERIFIED)
            self.db.bump_run(self.run_id, "submitted")
            self.db.bump_run(self.run_id, "verified")
            self.db.bump_run(self.run_id, "opened")

        elif outcome.status == JobStatus.SUBMITTED:
            evidence = outcome.evidence.to_dict() if outcome.evidence else {}
            self.db.mark_submitted(application_id,
                                   outcome.evidence.application_id if outcome.evidence else None)
            self.db.update_application(application_id, evidence={**evidence, **checkpoint},
                                       error=outcome.detail[:500])
            self.db.set_job_status(job.job_id, JobStatus.SUBMITTED)
            self.db.bump_run(self.run_id, "submitted")
            self.db.bump_run(self.run_id, "opened")

        elif outcome.status == JobStatus.BLOCKED:
            blocker = outcome.blocker
            self.db.update_application(
                application_id, status=JobStatus.BLOCKED,
                blocker_type=blocker.kind if blocker else "unknown",
                blocker_detail=(blocker.detail if blocker else "")[:500],
                checkpoint=checkpoint,
            )
            self.db.set_job_status(job.job_id, JobStatus.BLOCKED)
            self.db.bump_run(self.run_id, "blocked")
            self.db.bump_run(self.run_id, "opened")

        else:
            self.db.update_application(application_id, status=JobStatus.FAILED,
                                       error=outcome.detail[:500], checkpoint=checkpoint)
            self.db.set_job_status(job.job_id, JobStatus.FAILED)
            self.db.bump_run(self.run_id, "failed")

        log.info("job finished",
                 extra={"job_id": job.job_id, "company": job.company,
                        "status": outcome.status, "filled": outcome.filled,
                        "duration_s": round(duration, 1)})
        return JobResult(
            job_id=job.job_id, status=outcome.status,
            detail=outcome.detail or (str(outcome.blocker) if outcome.blocker else ""),
            application_id=application_id, filled=outcome.filled, duration_s=duration,
            blocker=outcome.blocker.kind if outcome.blocker else None,
        )

    def _fail(self, job: Job, application_id: Optional[int], status: str, detail: str,
              blocker_kind: Optional[str], started: float) -> JobResult:
        if application_id is not None:
            self.db.update_application(application_id, status=status, error=detail[:500],
                                       blocker_type=blocker_kind)
        self.db.set_job_status(job.job_id, status)
        self.db.bump_run(self.run_id, "blocked" if status == JobStatus.BLOCKED else "failed")
        return JobResult(job.job_id, status, detail, application_id,
                         duration_s=time.time() - started, blocker=blocker_kind)
