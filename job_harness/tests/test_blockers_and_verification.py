"""Blocker detection and evidence-based submission verification."""
from __future__ import annotations

import pytest

from job_harness.application import blockers
from job_harness.browser.dom import FormField, FormSnapshot
from job_harness.verification.verifier import (
    APPLICATION_ID, CONFIRMATION_TEXT, FAILURE_TEXT, verify_submission,
)


def snapshot(text: str = "", **kw) -> FormSnapshot:
    return FormSnapshot(body_text=text, **kw)


@pytest.mark.parametrize("text,kind", [
    ("Please complete the reCAPTCHA to continue", blockers.CAPTCHA),
    ('<div class="h-captcha" data-sitekey="x"></div>', blockers.CAPTCHA),
    ("cf-turnstile widget", blockers.CAPTCHA),
    ("Verify you are human before continuing", blockers.BOT_DETECTION),
    ("Please upload a government ID to verify your identity", blockers.IDENTITY_VERIFICATION),
    ("A verification code was sent to your phone", blockers.SECURITY_CHALLENGE),
    ("Record a video response using HireVue", blockers.VIDEO_INTERVIEW),
    ("Complete a timed coding challenge on HackerRank", blockers.ASSESSMENT),
    ("Please sign in to continue with your application", blockers.LOGIN_REQUIRED),
])
def test_blockers_are_detected(text, kind):
    blocker = blockers.detect_blocker(snapshot(text))
    assert blocker is not None and blocker.kind == kind


def test_a_normal_application_page_is_not_blocked():
    assert blockers.detect_blocker(
        snapshot("Apply for AI Engineer. Upload your resume and submit.")) is None


def test_password_field_is_treated_as_a_login_wall():
    blocker = blockers.detect_blocker(snapshot(
        "Create an account", fields=[FormField(selector="#p", type="password", label="Password")]))
    assert blocker is not None and blocker.kind == blockers.LOGIN_REQUIRED


@pytest.mark.parametrize("text", [
    "Thank you for applying to Acme AI",
    "Your application has been received",
    "We have received your application",
    "Thanks for applying!",
    "Application submitted successfully",
])
def test_confirmation_phrases_are_recognized(text):
    assert CONFIRMATION_TEXT.search(text)


@pytest.mark.parametrize("text", [
    "This field is required",
    "Please correct the errors below",
    "An error occurred submitting your application",
])
def test_failure_phrases_are_recognized(text):
    assert FAILURE_TEXT.search(text)
    assert not CONFIRMATION_TEXT.search(text)


def test_application_id_extraction():
    assert APPLICATION_ID.search(
        "Your application has been received. Confirmation ID: GH-123456").group(1) == "GH-123456"
    assert APPLICATION_ID.search("Reference Number: R-889231").group(1) == "R-889231"


class FakePage:
    """Minimal stand-in so verification can be tested without a browser."""

    def __init__(self, url: str) -> None:
        self.url = url


def test_verification_requires_positive_evidence(qwen):
    result = verify_submission(
        FakePage("https://acme.com/apply"), "https://acme.com/apply", None, qwen,
        snapshot("Please fill in the application form below.", url="https://acme.com/apply"))
    assert not result.verified


def test_confirmation_text_with_an_id_verifies(qwen):
    result = verify_submission(
        FakePage("https://acme.com/thank-you"), "https://acme.com/apply", None, qwen,
        snapshot("Thank you for applying. Confirmation ID: GH-123456",
                 url="https://acme.com/thank-you"))
    assert result.verified
    assert result.application_id == "GH-123456"
    assert result.signal == "application_id"


def test_validation_errors_are_never_treated_as_success(qwen):
    result = verify_submission(
        FakePage("https://acme.com/apply"), "https://acme.com/apply", None, qwen,
        snapshot("Thank you for applying. This field is required: email",
                 url="https://acme.com/apply", errors=["This field is required"]))
    assert not result.verified
    assert result.signal == "failed"


def test_url_change_alone_is_not_enough(qwen):
    result = verify_submission(
        FakePage("https://acme.com/step2"), "https://acme.com/apply", None, qwen,
        snapshot("Continue your application", url="https://acme.com/step2"))
    assert not result.verified


def test_confirmation_url_plus_empty_form_verifies(qwen):
    result = verify_submission(
        FakePage("https://acme.com/confirmation"), "https://acme.com/apply", None, qwen,
        snapshot("All done.", url="https://acme.com/confirmation"))
    assert result.verified          # confirmation URL + the form being gone


class FailingWatcher:
    def successful(self):
        return None

    def failed(self):
        return ("https://acme.com/applications", 422, "POST")


def test_failed_application_post_blocks_verification(qwen):
    result = verify_submission(
        FakePage("https://acme.com/thanks"), "https://acme.com/apply", FailingWatcher(), qwen,
        snapshot("Thank you for applying", url="https://acme.com/thanks"))
    assert not result.verified
    assert result.network_status == 422
