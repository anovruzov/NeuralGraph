"""Submission verification.

Clicking Submit proves nothing. A submission counts as VERIFIED only when the
page afterwards carries positive evidence: a confirmation URL, confirmation
text, an application ID, or a successful application POST observed on the wire.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from playwright.sync_api import Page, Response

from ..browser.dom import FormSnapshot, extract_form
from ..config.logging_setup import get_logger

log = get_logger("verification")

CONFIRMATION_TEXT = re.compile(
    r"thank you for (applying|your (application|interest|submission))|"
    r"(your )?application (has been )?(was )?(successfully )?(received|submitted|sent|complete)|"
    r"we('ve| have) received your application|thanks for applying|"
    r"application complete|successfully applied|"
    r"we('ll| will) (be in touch|review your application|get back to you)", re.I)

CONFIRMATION_URL = re.compile(
    r"/(thank[-_]?you|confirmation|confirmed|success|applied|complete[d]?)\b|"
    r"[?&](confirmation|success|applied|submitted)=", re.I)

APPLICATION_ID = re.compile(
    r"(?:application|confirmation|reference|candidate|req(?:uisition)?)\s*"
    r"(?:id|number|no\.?|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_\-]{3,40})", re.I)

FAILURE_TEXT = re.compile(
    r"(this field is required|please (correct|complete|fill|enter)|"
    r"required field|invalid (email|phone|input)|"
    r"(an |there was an )?error (occurred|submitting)|something went wrong|"
    r"could not (be )?(submit|process))", re.I)

# Endpoints an ATS uses for the actual application POST.
APPLY_ENDPOINT = re.compile(
    r"/(applications?|apply|submit|candidates?|job_?application)s?(/|\?|$)", re.I)


@dataclass
class SubmissionEvidence:
    verified: bool = False
    signal: str = "none"
    confidence: float = 0.0
    application_id: Optional[str] = None
    confirmation_text: str = ""
    confirmation_url: str = ""
    network_status: Optional[int] = None
    network_url: str = ""
    screenshot: str = ""
    failure_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified, "signal": self.signal,
            "confidence": self.confidence, "application_id": self.application_id,
            "confirmation_text": self.confirmation_text[:500],
            "confirmation_url": self.confirmation_url,
            "network_status": self.network_status, "network_url": self.network_url[:300],
            "screenshot": self.screenshot, "failure_text": self.failure_text[:300],
            **self.details,
        }


class ResponseWatcher:
    """Records application-looking POST responses while a submit is in flight."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.responses: list[tuple[str, int, str]] = []
        self._handler = self._on_response
        page.on("response", self._handler)

    def _on_response(self, response: Response) -> None:
        try:
            request = response.request
            if request.method not in ("POST", "PUT"):
                return
            if not APPLY_ENDPOINT.search(response.url):
                return
            self.responses.append((response.url, response.status, request.method))
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.page.remove_listener("response", self._handler)
        except Exception:
            pass

    def successful(self) -> Optional[tuple[str, int, str]]:
        for url, status, method in self.responses:
            if 200 <= status < 400:
                return url, status, method
        return None

    def failed(self) -> Optional[tuple[str, int, str]]:
        for url, status, method in self.responses:
            if status >= 400:
                return url, status, method
        return None


def verify_submission(page: Page, before_url: str, watcher: Optional[ResponseWatcher] = None,
                      qwen: Any = None, snapshot: Optional[FormSnapshot] = None
                      ) -> SubmissionEvidence:
    """Collect evidence that the application was actually submitted."""
    evidence = SubmissionEvidence()
    snapshot = snapshot if snapshot is not None else extract_form(page, include_frames=True)
    text = snapshot.body_text or ""
    url = snapshot.url or page.url

    failure = FAILURE_TEXT.search(text)
    if failure:
        evidence.failure_text = text[max(0, failure.start() - 80): failure.end() + 120]
    if snapshot.errors:
        evidence.details["dom_errors"] = snapshot.errors[:8]
    if snapshot.invalid:
        # Native constraint validation rejected the form: the submit never left
        # the browser, whatever the page looks like.
        evidence.details["invalid_fields"] = [
            {"label": f.get("label", "")[:80], "message": f.get("message", "")[:120],
             "hidden": f.get("hidden", False)}
            for f in snapshot.invalid[:8]
        ]
        if not evidence.failure_text:
            first = snapshot.invalid[0]
            evidence.failure_text = (
                f"browser validation rejected '{first.get('label', '')[:60]}': "
                f"{first.get('message', '')[:100]}")

    if watcher is not None:
        ok = watcher.successful()
        bad = watcher.failed()
        if ok:
            evidence.network_url, evidence.network_status = ok[0], ok[1]
        elif bad:
            evidence.network_url, evidence.network_status = bad[0], bad[1]

    signals: list[tuple[str, float]] = []

    confirmation = CONFIRMATION_TEXT.search(text)
    if confirmation:
        evidence.confirmation_text = text[max(0, confirmation.start() - 40):
                                          confirmation.end() + 200].strip()
        signals.append(("confirmation_message", 0.85))

    if CONFIRMATION_URL.search(url) and url != before_url:
        evidence.confirmation_url = url
        signals.append(("confirmation_page", 0.8))

    app_id = APPLICATION_ID.search(text)
    if app_id and confirmation:
        evidence.application_id = app_id.group(1)
        signals.append(("application_id", 0.95))

    if evidence.network_status is not None and 200 <= evidence.network_status < 400:
        signals.append(("network_response", 0.75))

    # The form disappearing while the URL changed is weak corroboration only.
    if not snapshot.fields and url != before_url and not evidence.failure_text:
        signals.append(("form_gone", 0.45))

    if evidence.failure_text or snapshot.invalid or (evidence.network_status or 0) >= 400:
        evidence.verified = False
        evidence.signal = "failed"
        evidence.confidence = 0.9
        log.warning("submission verification failed",
                    extra={"url": url, "failure": evidence.failure_text[:150],
                           "status": evidence.network_status})
        return evidence

    if signals:
        signals.sort(key=lambda s: s[1], reverse=True)
        evidence.signal, evidence.confidence = signals[0]
        # Two independent signals is strong; one weak signal alone is not.
        evidence.verified = evidence.confidence >= 0.75 or len(signals) >= 2
        evidence.details["all_signals"] = [s[0] for s in signals]

    if not evidence.verified and qwen is not None:
        judgment = qwen.evaluate_application({
            "url": url, "title": snapshot.title,
            "text": text[:3000], "errors": snapshot.errors[:8],
            "buttons": [b.get("text") for b in snapshot.buttons[:12]],
            "network_status": evidence.network_status,
            "url_changed": url != before_url,
        })
        evidence.details["qwen"] = {"submitted": judgment.submitted,
                                    "confidence": judgment.confidence,
                                    "signal": judgment.signal,
                                    "reason": judgment.reason[:200]}
        if judgment.submitted and judgment.confidence >= 0.8:
            evidence.verified = True
            evidence.signal = judgment.signal or "model_judgment"
            evidence.confidence = judgment.confidence
            evidence.application_id = evidence.application_id or judgment.application_id
        elif judgment.blocker:
            evidence.details["blocker"] = judgment.blocker

    log.info("verification result",
             extra={"verified": evidence.verified, "signal": evidence.signal,
                    "confidence": evidence.confidence,
                    "application_id": evidence.application_id})
    return evidence
