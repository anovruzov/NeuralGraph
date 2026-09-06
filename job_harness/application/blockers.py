"""Detection of things the harness must never attempt to defeat.

Encountering any of these ends the attempt for this job: the blocker is logged,
state is saved, and the campaign moves on. The harness does not solve CAPTCHAs,
bypass identity checks, or complete assessments meant for the applicant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..browser.dom import FormSnapshot

CAPTCHA = "captcha"
LOGIN_REQUIRED = "login_required"
IDENTITY_VERIFICATION = "identity_verification"
ASSESSMENT = "assessment"
VIDEO_INTERVIEW = "video_interview"
SECURITY_CHALLENGE = "security_challenge"
BOT_DETECTION = "bot_detection"
UNSUPPORTED_FORM = "unsupported_form"
MISSING_ANSWER = "missing_answer"
VALIDATION_FAILED = "validation_failed"
NAVIGATION_FAILED = "navigation_failed"

# Checked against page text and DOM markup. Ordered most specific first.
DOM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (CAPTCHA, re.compile(
        r"\bre[- ]?captcha\b|\bh[- ]?captcha\b|\bcaptcha\b|g-recaptcha|"
        r"cf-turnstile|\bturnstile\b|funcaptcha|arkoselabs|geetest", re.I)),
    (BOT_DETECTION, re.compile(
        r"are you a human|verify you are (a )?human|unusual traffic|"
        r"automated (access|requests) (detected|blocked)|access denied.*bot|"
        r"perimeterx|datadome|incapsula|imperva", re.I)),
    (IDENTITY_VERIFICATION, re.compile(
        r"verify your identity|identity verification|upload (a )?(government|photo) id|"
        r"driver'?s licen[cs]e photo|passport photo|selfie", re.I)),
    (SECURITY_CHALLENGE, re.compile(
        r"(verification|security) code (was |has been )?sent|enter the (6|six)[- ]digit|"
        r"two[- ]factor|2fa|one[- ]time (pass)?code|check your (phone|email) for a code", re.I)),
    (VIDEO_INTERVIEW, re.compile(
        r"video (interview|assessment|response)|record (a |your )?video|"
        r"hirevue|spark ?hire|willo\b|one-way interview", re.I)),
    (ASSESSMENT, re.compile(
        r"coding (challenge|assessment|test)|technical assessment|online assessment|"
        r"timed (test|assessment)|hackerrank|codility|codesignal|karat|woven|"
        r"take[- ]home (test|assignment|exercise)|skills? (test|assessment)", re.I)),
    (LOGIN_REQUIRED, re.compile(
        r"sign in to (continue|apply)|log ?in to (continue|apply)|create an account to apply|"
        r"you must be (signed|logged) in|please (sign|log) in", re.I)),
]


@dataclass
class Blocker:
    kind: str
    detail: str = ""
    evidence: str = ""

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


def detect_blocker(snapshot: FormSnapshot, page_html: str = "") -> Optional[Blocker]:
    """Scan a page snapshot for a hard blocker. Returns None if the page is workable."""
    haystacks = [snapshot.body_text or "", page_html or "",
                 " ".join(b.get("text", "") for b in snapshot.buttons)]
    combined = "\n".join(haystacks)

    for kind, pattern in DOM_PATTERNS:
        match = pattern.search(combined)
        if match:
            start = max(0, match.start() - 60)
            return Blocker(kind=kind, detail=match.group(0)[:120],
                           evidence=combined[start:match.end() + 60].replace("\n", " ")[:240])

    # A password field on an application page means an account wall.
    for field in snapshot.fields:
        if field.type == "password":
            return Blocker(kind=LOGIN_REQUIRED, detail="password field present",
                           evidence=field.label[:120])

    return None


def is_blocked_page(page_text: str) -> Optional[str]:
    for kind, pattern in DOM_PATTERNS:
        if pattern.search(page_text or ""):
            return kind
    return None
