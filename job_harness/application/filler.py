"""Fill, validate, and (in apply mode) submit one application."""
from __future__ import annotations

import time
from dataclasses import dataclass, field as dc_field
from typing import Any, Optional

from playwright.sync_api import Page

from ..browser import actions
from ..browser.dom import FormField, FormSnapshot, extract_form
from ..browser.engine import BrowserEngine
from ..config.logging_setup import get_logger
from ..config.settings import Config
from ..database.db import Database
from ..database.models import Job, JobStatus
from ..verification.verifier import ResponseWatcher, SubmissionEvidence, verify_submission
from . import blockers
from .field_resolver import FieldResolver, ResolvedAnswer

log = get_logger("application.filler")

MAX_FORM_STEPS = 6


@dataclass
class FillOutcome:
    status: str                                  # READY_TO_SUBMIT | SUBMITTED | VERIFIED | BLOCKED | FAILED
    answers: list[ResolvedAnswer] = dc_field(default_factory=list)
    filled: int = 0
    skipped: int = 0
    blocked_fields: list[ResolvedAnswer] = dc_field(default_factory=list)
    blocker: Optional[blockers.Blocker] = None
    evidence: Optional[SubmissionEvidence] = None
    steps: int = 0
    screenshot: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (JobStatus.READY_TO_SUBMIT, JobStatus.SUBMITTED,
                               JobStatus.VERIFIED)


class ApplicationFiller:
    def __init__(self, config: Config, db: Database, browser: BrowserEngine,
                 resolver: FieldResolver, qwen: Any, run_id: str) -> None:
        self.config = config
        self.db = db
        self.browser = browser
        self.resolver = resolver
        self.qwen = qwen
        self.run_id = run_id

    # ------------------------------------------------------------ the loop

    def apply_to(self, page: Page, job: Job, application_id: int,
                 dry_run: bool) -> FillOutcome:
        outcome = FillOutcome(status=JobStatus.FAILED)
        job_context = {"company": job.company, "title": job.title,
                       "location": job.location,
                       "description_excerpt": (job.description or "")[:600]}

        for step in range(1, MAX_FORM_STEPS + 1):
            outcome.steps = step
            snapshot = extract_form(page, include_frames=True)

            blocker = blockers.detect_blocker(snapshot, self._page_html(page))
            if blocker is not None:
                outcome.status = JobStatus.BLOCKED
                outcome.blocker = blocker
                outcome.screenshot = self.browser.screenshot(
                    page, f"{job.company}_{blocker.kind}") or ""
                log.warning("blocker encountered", extra={"job_id": job.job_id,
                                                          "kind": blocker.kind,
                                                          "detail": blocker.detail[:120]})
                return outcome

            if not snapshot.fields:
                # No form here: follow an obvious apply link once, else give up.
                if step == 1 and self._follow_apply_link(page, snapshot):
                    continue
                outcome.status = JobStatus.BLOCKED
                outcome.blocker = blockers.Blocker(
                    blockers.UNSUPPORTED_FORM, "no fillable form fields found",
                    snapshot.body_text[:200])
                outcome.screenshot = self.browser.screenshot(page, f"{job.company}_noform") or ""
                return outcome

            step_result = self._fill_step(page, snapshot, job, job_context, application_id)
            outcome.answers.extend(step_result["answers"])
            outcome.filled += step_result["filled"]
            outcome.skipped += step_result["skipped"]

            if step_result["blocked"]:
                outcome.status = JobStatus.BLOCKED
                outcome.blocked_fields = step_result["blocked"]
                first = step_result["blocked"][0]
                outcome.blocker = blockers.Blocker(
                    blockers.MISSING_ANSWER,
                    f"required field '{first.field_key}' has no grounded answer",
                    first.blocked_reason)
                outcome.screenshot = self.browser.screenshot(
                    page, f"{job.company}_missing_answer") or ""
                log.warning("application blocked on required field",
                            extra={"job_id": job.job_id, "field": first.field_key,
                                   "reason": first.blocked_reason[:150],
                                   "count": len(step_result["blocked"])})
                return outcome

            after = extract_form(page, include_frames=True)

            next_button = after.find_next()
            submit_button = after.find_submit()

            if next_button and not submit_button:
                log.info("advancing multi-step form",
                         extra={"job_id": job.job_id, "step": step,
                                "button": next_button.get("text")})
                if not actions.click_button(page, next_button["selector"],
                                            next_button.get("frame_url", "")):
                    outcome.status = JobStatus.FAILED
                    outcome.detail = "could not advance to the next form step"
                    return outcome
                page.wait_for_timeout(1200)
                if after.errors:
                    log.warning("validation errors after step",
                                extra={"errors": after.errors[:5]})
                continue

            if submit_button is None:
                outcome.status = JobStatus.BLOCKED
                outcome.blocker = blockers.Blocker(
                    blockers.UNSUPPORTED_FORM, "no submit button found",
                    ", ".join(b.get("text", "") for b in after.buttons[:8]))
                return outcome

            return self._finish(page, job, outcome, after, submit_button, dry_run)

        outcome.status = JobStatus.FAILED
        outcome.detail = f"form did not reach a submit step within {MAX_FORM_STEPS} steps"
        return outcome

    # --------------------------------------------------------- one step

    def _fill_step(self, page: Page, snapshot: FormSnapshot, job: Job,
                   job_context: dict[str, Any], application_id: int) -> dict[str, Any]:
        answers: list[ResolvedAnswer] = []
        blocked: list[ResolvedAnswer] = []
        filled = skipped = 0

        for form_field in snapshot.fields:
            if form_field.disabled or form_field.readonly:
                continue
            try:
                answer = self.resolver.resolve(form_field, job_context)
            except Exception as exc:
                log.error("field resolution failed",
                          extra={"field": form_field.key, "error": str(exc)[:200]})
                self.db.record_error(self.run_id, job.job_id, "resolve_field", exc)
                answer = ResolvedAnswer(field_key=form_field.key, semantic_key="other",
                                        blocked_reason=f"resolver error: {exc}"[:200])

            answers.append(answer)

            if answer.skip or not answer.answerable:
                if form_field.required and not answer.answerable:
                    blocked.append(answer)
                else:
                    skipped += 1
                self._record(application_id, job, form_field, answer, filled=False)
                continue

            ok = actions.apply_value(
                page, form_field, answer.value,
                resume_path=self.config.run.resume_path,
                timeout=self.config.browser.action_timeout_ms,
            )
            if ok:
                filled += 1
            else:
                answer.reason = (answer.reason + " | fill failed in DOM").strip(" |")
                if form_field.required:
                    answer.blocked_reason = "value could not be entered into the field"
                    answer.safe_to_submit = False
                    blocked.append(answer)
                else:
                    skipped += 1
            self._record(application_id, job, form_field, answer, filled=ok)

        return {"answers": answers, "blocked": blocked, "filled": filled, "skipped": skipped}

    def _record(self, application_id: int, job: Job, form_field: FormField,
                answer: ResolvedAnswer, filled: bool) -> None:
        try:
            self.db.record_answer(
                application_id=application_id, job_id=job.job_id,
                field_key=answer.field_key, label=form_field.label[:300],
                field_type=form_field.type, answer=answer.value,
                confidence=answer.confidence, source=answer.source,
                resolver=answer.resolver, safe_to_submit=answer.safe_to_submit,
                filled=filled,
            )
        except Exception as exc:
            log.debug("could not record answer", extra={"error": str(exc)[:150]})

    # ----------------------------------------------------------- finishing

    def _finish(self, page: Page, job: Job, outcome: FillOutcome,
                snapshot: FormSnapshot, submit_button: dict[str, Any],
                dry_run: bool) -> FillOutcome:
        unmet = [f for f in snapshot.visible_required() if not self._has_value(page, f)]
        if unmet:
            outcome.status = JobStatus.BLOCKED
            outcome.blocker = blockers.Blocker(
                blockers.VALIDATION_FAILED,
                f"{len(unmet)} required field(s) still empty",
                ", ".join(f.label[:40] for f in unmet[:5]))
            outcome.screenshot = self.browser.screenshot(page, f"{job.company}_unmet") or ""
            log.warning("required fields still empty at submit time",
                        extra={"job_id": job.job_id,
                               "fields": [f.label[:60] for f in unmet[:5]]})
            return outcome

        if dry_run:
            outcome.status = JobStatus.READY_TO_SUBMIT
            outcome.detail = (f"validated {outcome.filled} filled field(s); "
                              f"submit button '{submit_button.get('text')}' not clicked "
                              f"(dry run)")
            outcome.screenshot = self.browser.screenshot(page, f"{job.company}_ready") or ""
            log.info("dry run: application ready to submit",
                     extra={"job_id": job.job_id, "company": job.company,
                            "filled": outcome.filled, "skipped": outcome.skipped})
            return outcome

        before_url = page.url
        watcher = ResponseWatcher(page)
        try:
            log.info("submitting application",
                     extra={"job_id": job.job_id, "company": job.company,
                            "title": job.title, "button": submit_button.get("text")})
            clicked = actions.click_button(page, submit_button["selector"],
                                           submit_button.get("frame_url", ""),
                                           timeout=self.config.browser.action_timeout_ms)
            if not clicked:
                outcome.status = JobStatus.FAILED
                outcome.detail = "submit button could not be clicked"
                return outcome
            self._settle(page)
            evidence = verify_submission(page, before_url, watcher, self.qwen)
        finally:
            watcher.stop()

        evidence.screenshot = self.browser.screenshot(page, f"{job.company}_after_submit") or ""
        outcome.evidence = evidence
        if evidence.verified:
            outcome.status = JobStatus.VERIFIED
        else:
            # The click happened; without evidence it stays SUBMITTED, never VERIFIED.
            outcome.status = JobStatus.SUBMITTED
            outcome.detail = (f"submitted but unverified: {evidence.signal}; "
                              f"{evidence.failure_text[:150]}")
        return outcome

    # ------------------------------------------------------------- helpers

    def _settle(self, page: Page) -> None:
        """Wait for the page to react to the submit click."""
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            page.wait_for_timeout(3000)
        page.wait_for_timeout(1200)

    def _has_value(self, page: Page, form_field: FormField) -> bool:
        from ..browser.dom import frame_for
        ctx = frame_for(page, form_field.frame_url)
        try:
            if form_field.type == "file":
                count = ctx.evaluate(
                    "(sel) => { const el = document.querySelector(sel);"
                    " return el && el.files ? el.files.length : 0; }", form_field.selector)
                return bool(count)
            if form_field.type == "radio":
                return bool(ctx.evaluate(
                    "(sel) => { const el = document.querySelector(sel);"
                    " if (!el || !el.name) return false;"
                    " return !!document.querySelector(`input[name=\"${el.name}\"]:checked`); }",
                    form_field.selector))
            if form_field.type == "checkbox":
                return bool(ctx.locator(form_field.selector).first.is_checked(timeout=3000))
            value = ctx.locator(form_field.selector).first.input_value(timeout=3000)
            return bool(value and value.strip())
        except Exception:
            return False

    def _page_html(self, page: Page) -> str:
        try:
            return page.content()[:120000]
        except Exception:
            return ""

    def _follow_apply_link(self, page: Page, snapshot: FormSnapshot) -> bool:
        """Job description pages often link to the real application form."""
        for button in snapshot.buttons:
            text = (button.get("text") or "").strip().lower()
            if text in ("apply", "apply now", "apply for this job",
                        "apply for this position", "submit application", "start application"):
                log.info("following apply link", extra={"text": text})
                if actions.click_button(page, button["selector"],
                                        button.get("frame_url", "")):
                    page.wait_for_timeout(2000)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    return True
        return False
