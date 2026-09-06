"""The harness must never invent a personal fact."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_harness.application import semantics
from job_harness.application.field_resolver import (
    FieldResolver, match_attestation_option,
)
from job_harness.browser.dom import FormField
from job_harness.profile.applicant import Applicant, ProfileError

PROFILE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "applicant.test.json"
RESUME_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "resume.test.txt"

JOB_CONTEXT = {"company": "Acme AI", "title": "AI Engineer"}


def field(**kw) -> FormField:
    base = dict(selector="#x", tag="input", type="text", name="", label="", options=[])
    base.update(kw)
    return FormField(**base)


@pytest.fixture
def resolver(applicant, qwen, config):
    return FieldResolver(applicant, qwen, config.run.min_field_confidence,
                         config.run.resume_path)


# ---------------------------------------------------------------- classification

@pytest.mark.parametrize("label,expected", [
    ("First Name *", "first_name"),
    ("Last Name", "last_name"),
    ("Email Address*", "email"),
    ("Phone Number", "phone"),
    ("Resume/CV", "resume_upload"),
    ("LinkedIn Profile", "linkedin"),
    ("GitHub URL", "github"),
    ("School", "university"),
    ("Highest level of education completed", "degree"),
    ("Discipline", "major"),
    ("Expected graduation date", "graduation_date"),
    ("GPA", "gpa"),
    ("Are you legally authorized to work in the United States?", "work_authorization"),
    ("Will you now or in the future require sponsorship?", "sponsorship_required"),
    ("Are you legally authorized to work in the country of employment?", "work_authorization"),
    ("Do you have an active security clearance?", "security_clearance"),
    ("Have you ever been convicted of a felony?", "criminal_history"),
    ("What is the total number of years of professional experience you have?", "years_experience"),
    ("Desired Salary", "salary_expectation"),
    ("How did you hear about us?", "how_did_you_hear"),
    ("Gender", "gender"),
    ("Veteran Status", "veteran_status"),
    ("Disability Status", "disability_status"),
    ("Why do you want to work here?", "why_this_company"),
    ("I certify the information is accurate and complete", "acknowledgement"),
])
def test_deterministic_field_classification(label, expected):
    assert semantics.classify_label(label) == expected


def test_unknown_labels_fall_through_to_the_model():
    assert semantics.classify_label("What is your favourite kind of widget?") is None


def test_sponsorship_is_not_confused_with_authorization():
    # These two questions mention the same words and take opposite answers.
    assert semantics.classify_label(
        "Will you now or in the future require sponsorship for employment visa status?"
    ) == "sponsorship_required"
    assert semantics.classify_label(
        "Are you legally authorized to work in the US?") == "work_authorization"


# -------------------------------------------------------------------- grounding

def test_known_facts_come_from_the_profile(resolver, applicant):
    answer = resolver.resolve(field(label="Email Address *", type="email", required=True),
                              JOB_CONTEXT)
    assert answer.answerable
    assert answer.value == applicant.get("email")
    assert answer.source == "profile.email"
    assert answer.resolver == "deterministic"


def test_missing_clearance_is_never_invented(resolver):
    answer = resolver.resolve(
        field(label="What is your current security clearance level?", required=True),
        JOB_CONTEXT)
    assert not answer.answerable
    assert not answer.value
    assert "attestation" in answer.blocked_reason


def test_unknown_personal_question_is_refused(resolver):
    answer = resolver.resolve(
        field(label="What is your mother's maiden name?", required=True), JOB_CONTEXT)
    assert not answer.answerable
    assert answer.value in ("", None)


def test_profile_value_absent_from_options_blocks_rather_than_guesses(resolver):
    answer = resolver.resolve(field(
        label="Degree", type="select", required=True,
        options=[{"label": "Certificate", "value": "1"},
                 {"label": "Trade School", "value": "2"}]), JOB_CONTEXT)
    assert not answer.answerable
    assert "does not match any allowed option" in answer.blocked_reason


def test_work_authorization_and_sponsorship_get_opposite_answers(resolver):
    auth = resolver.resolve(field(
        label="Are you legally authorized to work in the United States?", type="select",
        required=True, options=[{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}]),
        JOB_CONTEXT)
    sponsor = resolver.resolve(field(
        label="Will you now or in the future require sponsorship?", type="select",
        required=True, options=[{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}]),
        JOB_CONTEXT)
    assert auth.value == "Yes"
    assert sponsor.value == "No"


def test_verbose_attestation_options_are_matched_by_meaning():
    options = [{"label": "Please select", "value": ""},
               {"label": "I am authorized to work in the US without sponsorship", "value": "a"},
               {"label": "I require sponsorship now or in the future", "value": "b"}]
    assert match_attestation_option("yes", options, "work_authorization")["value"] == "a"
    assert match_attestation_option("no", options, "sponsorship_required")["value"] == "a"


def test_degree_synonyms_map_to_the_offered_option(resolver):
    answer = resolver.resolve(field(
        label="Degree", type="select", required=True,
        options=[{"label": "Select...", "value": ""},
                 {"label": "Bachelor's Degree", "value": "2"},
                 {"label": "Master's Degree", "value": "3"}]), JOB_CONTEXT)
    assert answer.value == "Bachelor's Degree"      # profile says "Bachelor of Science"


def test_demographics_follow_the_decline_policy(resolver):
    answer = resolver.resolve(field(
        label="Gender", type="select",
        options=[{"label": "Male", "value": "m"}, {"label": "Female", "value": "f"},
                 {"label": "Decline To Self Identify", "value": "d"}]), JOB_CONTEXT)
    assert answer.value == "Decline To Self Identify"
    assert answer.resolver == "policy"


def test_required_demographic_without_a_decline_option_blocks(resolver):
    answer = resolver.resolve(field(
        label="Gender", type="select", required=True,
        options=[{"label": "Male", "value": "m"}, {"label": "Female", "value": "f"}]),
        JOB_CONTEXT)
    assert not answer.answerable
    assert "decline" in answer.blocked_reason


def test_self_disclosed_demographic_is_used_when_provided(applicant, qwen, config):
    applicant.data["demographic_response_policy"]["veteran_status"] = "I am not a protected veteran"
    resolver = FieldResolver(applicant, qwen, config.run.min_field_confidence)
    answer = resolver.resolve(field(
        label="Veteran Status", type="select",
        options=[{"label": "I am not a protected veteran", "value": "2"},
                 {"label": "I don't wish to answer", "value": "3"}]), JOB_CONTEXT)
    assert answer.value == "I am not a protected veteran"


def test_optional_unanswerable_field_is_skipped_not_blocked(resolver):
    answer = resolver.resolve(field(label="Fax number", required=False), JOB_CONTEXT)
    assert not answer.answerable
    assert answer.skip or not answer.safe_to_submit


def test_low_confidence_model_answers_are_rejected(resolver, monkeypatch):
    from job_harness.qwen.schemas import FieldAnswer
    monkeypatch.setattr(resolver.qwen, "answer_form_question",
                        lambda *a, **k: FieldAnswer(
                            field_type="text", answer="Stanford University", confidence=0.4,
                            source="profile.university", safe_to_submit=True))
    answer = resolver.resolve(field(label="Tell us something", required=True), JOB_CONTEXT)
    assert not answer.answerable
    assert "confidence" in answer.blocked_reason


def test_model_answers_without_a_source_are_rejected(resolver, monkeypatch):
    from job_harness.qwen.schemas import FieldAnswer
    monkeypatch.setattr(resolver.qwen, "answer_form_question",
                        lambda *a, **k: FieldAnswer(
                            field_type="text", answer="Harvard", confidence=0.99,
                            source="unknown", safe_to_submit=True))
    answer = resolver.resolve(field(label="Tell us something", required=True), JOB_CONTEXT)
    assert not answer.answerable
    assert "no source" in answer.blocked_reason


def test_ungrounded_short_answers_are_rejected(resolver, monkeypatch):
    from job_harness.qwen.schemas import FieldAnswer
    monkeypatch.setattr(resolver.qwen, "answer_form_question",
                        lambda *a, **k: FieldAnswer(
                            field_type="text", answer="Massachusetts Institute of Technology",
                            confidence=0.95, source="profile.university", safe_to_submit=True))
    answer = resolver.resolve(field(label="Where did you study?", required=True), JOB_CONTEXT)
    assert not answer.answerable          # not present in the profile or resume
    assert "traceable" in answer.blocked_reason


def test_grounded_short_answers_are_accepted(resolver, monkeypatch):
    from job_harness.qwen.schemas import FieldAnswer
    monkeypatch.setattr(resolver.qwen, "answer_form_question",
                        lambda *a, **k: FieldAnswer(
                            field_type="text", answer="University of Texas at Austin",
                            confidence=0.95, source="profile.university", safe_to_submit=True))
    answer = resolver.resolve(field(label="Where did you study?", required=True), JOB_CONTEXT)
    assert answer.answerable


# --------------------------------------------------------------------- profile

def test_profile_requires_core_fields(tmp_path):
    path = tmp_path / "applicant.json"
    path.write_text(json.dumps({"legal_name": "", "email": "", "phone": ""}))
    with pytest.raises(ProfileError):
        Applicant.load(path, strict=True)


def test_missing_profile_raises_a_clear_error(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        Applicant.load(tmp_path / "nope.json")


def test_dotted_path_lookup(applicant):
    assert applicant.get("employment_history[0].title") == "Machine Learning Engineer"
    assert applicant.get("nothing.here", "fallback") == "fallback"


def test_redacted_summary_excludes_contact_details(applicant):
    summary = applicant.redacted_summary()
    assert applicant.get("email") not in summary
    assert applicant.get("phone") not in summary
    assert applicant.get("university") in summary


def test_fact_corpus_includes_resume_text(applicant):
    corpus = applicant.fact_corpus()
    assert "pytorch" in corpus
    assert "university of texas at austin" in corpus


def test_a_broken_pdf_extractor_does_not_stop_the_run(tmp_path, monkeypatch):
    """A missing or broken optional extractor must degrade, never crash.

    pypdf's native backend can fail outside the Exception hierarchy, which would
    otherwise take the whole campaign down at startup.
    """
    import builtins

    from job_harness.profile import applicant as applicant_module

    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n")

    real_import = builtins.__import__

    def exploding_import(name, *args, **kwargs):
        if name == "pypdf":
            raise BaseException("native extension exploded")   # noqa: TRY002
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", exploding_import)
    monkeypatch.setattr(applicant_module.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))

    loaded = Applicant.load(PROFILE_FIXTURE, pdf, strict=False)
    assert loaded.resume_text == ""
    assert loaded.full_name                                    # profile still usable


# ------------------------------------------------------- cover letter policy

@pytest.fixture
def cover_letter_resolver(qwen, config):
    def build(mode: str, path: str = "") -> FieldResolver:
        applicant = Applicant.load(PROFILE_FIXTURE, RESUME_FIXTURE)
        applicant.data["cover_letter_policy"] = {"mode": mode, "cover_letter_path": path}
        return FieldResolver(applicant, qwen, config.run.min_field_confidence,
                             config.run.resume_path)
    return build


def cover_field(field_type: str, required: bool) -> FormField:
    return FormField(selector="#c", tag=field_type, type=field_type,
                     label="Cover Letter", required=required)


def test_skip_policy_leaves_an_optional_cover_letter_blank(cover_letter_resolver, qwen):
    before = qwen.stats["requests"]
    answer = cover_letter_resolver("skip").resolve(cover_field("textarea", False), JOB_CONTEXT)
    assert answer.skip and not answer.answerable
    assert qwen.stats["requests"] == before        # no tokens spent


def test_skip_policy_blocks_a_required_cover_letter(cover_letter_resolver):
    answer = cover_letter_resolver("skip").resolve(cover_field("textarea", True), JOB_CONTEXT)
    assert not answer.answerable
    assert "skip" in answer.blocked_reason


def test_file_policy_uploads_the_configured_file(cover_letter_resolver, tmp_path):
    letter = tmp_path / "cover.txt"
    letter.write_text("Dear team,\nI build AI systems.\n")
    answer = cover_letter_resolver("file", str(letter)).resolve(
        cover_field("file", True), JOB_CONTEXT)
    assert answer.answerable and answer.value == str(letter)


def test_file_policy_pastes_the_text_into_a_textarea(cover_letter_resolver, tmp_path):
    letter = tmp_path / "cover.txt"
    letter.write_text("Dear team,\nI build AI systems.\n")
    answer = cover_letter_resolver("file", str(letter)).resolve(
        cover_field("textarea", True), JOB_CONTEXT)
    assert answer.answerable and "I build AI systems." in answer.value


def test_file_policy_without_a_file_never_invents_one(cover_letter_resolver):
    answer = cover_letter_resolver("file").resolve(cover_field("file", True), JOB_CONTEXT)
    assert not answer.answerable
    assert not answer.value


def test_generate_policy_composes_from_the_profile(cover_letter_resolver):
    answer = cover_letter_resolver("generate").resolve(
        cover_field("textarea", True), JOB_CONTEXT)
    assert answer.answerable
    assert answer.source.startswith("profile.")


def test_a_file_upload_is_never_composed(cover_letter_resolver):
    """There is nothing to upload, and prose cannot become a file."""
    answer = cover_letter_resolver("generate").resolve(
        cover_field("file", False), JOB_CONTEXT)
    assert not answer.answerable
    assert answer.skip
