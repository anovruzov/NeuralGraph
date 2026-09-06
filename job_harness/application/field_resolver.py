"""Resolve one form field to a truthful, grounded answer.

Order of resolution, cheapest and most trustworthy first:
    1. deterministic label classification  -> semantic key
    2. deterministic profile lookup        -> value straight from applicant.json
    3. Qwen classification                 -> semantic key for unknown labels
    4. Qwen answer                         -> only for questions the profile
                                              cannot answer directly
    5. validation                          -> option constraints + grounding

Anything that survives none of these is UNANSWERABLE. An unanswerable REQUIRED
field blocks the application; the harness never invents a value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Optional

from ..browser.actions import best_option
from ..browser.dom import FormField
from ..config.logging_setup import get_logger
from ..profile.applicant import Applicant
from ..qwen.qwen_client import QwenClient
from . import semantics

log = get_logger("application.resolver")

DECLINE_MARKERS = ("prefer not", "decline", "don't wish", "do not wish", "not wish to answer",
                   "i don't want to answer", "choose not", "opt out", "no response")

AFFIRMATIVE = re.compile(
    r"^\s*(yes\b|y\b|true\b|authorized\b|i am authorized|us citizen|u\.s\. citizen|"
    r"citizen\b|permanent resident|green ?card|eligible\b)", re.I)
NEGATIVE = re.compile(r"^\s*(no\b|n\b|false\b|not\b|none\b|never\b|i do not|i don't)", re.I)

DEGREE_SYNONYMS = {
    "bachelor": ["bachelor", "bachelors", "bachelor's", "bs", "b.s.", "ba", "b.a.",
                 "undergraduate", "bachelor's degree", "bachelor degree"],
    "master": ["master", "masters", "master's", "ms", "m.s.", "ma", "m.a.", "meng",
               "master's degree", "graduate degree"],
    "doctorate": ["doctorate", "phd", "ph.d.", "doctoral", "doctor of philosophy"],
    "associate": ["associate", "associates", "associate's", "aa", "as"],
    "high_school": ["high school", "secondary", "ged", "diploma"],
}

REMOTE_SYNONYMS = {
    "remote": ["remote", "fully remote", "work from home", "wfh", "distributed"],
    "hybrid": ["hybrid", "flexible", "partially remote"],
    "onsite": ["onsite", "on-site", "in office", "in-office", "in person"],
}


@dataclass
class ResolvedAnswer:
    field_key: str
    semantic_key: str
    value: Any = ""
    confidence: float = 0.0
    source: str = "unknown"
    safe_to_submit: bool = False
    resolver: str = "none"          # deterministic | qwen | policy | none
    reason: str = ""
    field_type: str = ""
    blocked_reason: str = ""
    skip: bool = False              # optional field intentionally left blank

    @property
    def answerable(self) -> bool:
        return self.safe_to_submit and self.value not in (None, "", [])


def _interpret_yes_no(text: str) -> Optional[str]:
    """Read an explicit yes/no out of a profile statement. Never guesses."""
    value = str(text or "").strip()
    if not value:
        return None
    if AFFIRMATIVE.match(value):
        return "yes"
    if NEGATIVE.match(value):
        return "no"
    lowered = value.lower()
    if lowered in ("yes", "true", "1"):
        return "yes"
    if lowered in ("no", "false", "0"):
        return "no"
    return None


def _degree_family(text: str) -> Optional[str]:
    lowered = str(text or "").lower()
    for family, words in DEGREE_SYNONYMS.items():
        if any(w in lowered for w in words):
            return family
    return None


def _match_degree_option(profile_degree: str, options: list[dict[str, Any]]
                         ) -> Optional[dict[str, Any]]:
    family = _degree_family(profile_degree)
    if not family:
        return None
    for option in options:
        if _degree_family(option.get("label") or "") == family:
            return option
    return None


def _decline_option(options: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for option in options:
        label = str(option.get("label") or "").lower()
        if any(m in label for m in DECLINE_MARKERS):
            return option
    return None


class FieldResolver:
    def __init__(self, applicant: Applicant, qwen: QwenClient,
                 min_confidence: float = 0.75, resume_path: str = "") -> None:
        self.applicant = applicant
        self.qwen = qwen
        self.min_confidence = min_confidence
        self.resume_path = resume_path
        self._corpus = applicant.fact_corpus()

    # ------------------------------------------------------------- resolve

    def resolve(self, field: FormField, job_context: dict[str, Any]) -> ResolvedAnswer:
        semantic_key = semantics.classify_label(
            field.label, field.name, field.placeholder, field.help
        )
        resolver_used = "deterministic"

        if semantic_key is None:
            classification = self.qwen.classify_form_field(field.prompt_view())
            semantic_key = classification.semantic_key or "other"
            resolver_used = "qwen"
            log.debug("qwen classified field",
                      extra={"label": field.label[:80], "semantic_key": semantic_key,
                             "confidence": classification.confidence})

        answer = ResolvedAnswer(field_key=field.key, semantic_key=semantic_key,
                                field_type=field.type)

        # Demographics follow the stated policy and are never inferred.
        if semantics.is_demographic(semantic_key):
            return self._resolve_demographic(field, answer)

        deterministic = self._deterministic_value(field, semantic_key)
        if deterministic is not None:
            value, source = deterministic
            matched = self._coerce_to_options(field, value, semantic_key)
            if matched is None and field.options:
                # We know the truth but cannot express it in this field's options.
                answer.blocked_reason = (
                    f"profile value for '{semantic_key}' does not match any allowed option"
                )
                answer.reason = f"value {str(value)[:60]!r} not in options"
                answer.source = source
                return answer
            answer.value = matched if matched is not None else value
            answer.confidence = 0.98
            answer.source = source
            answer.resolver = "deterministic"
            answer.safe_to_submit = True
            answer.reason = f"read directly from {source}"
            return answer

        # Sensitive questions are answered only from an explicit profile value.
        if semantics.is_sensitive(semantic_key):
            answer.blocked_reason = (
                f"'{semantic_key}' is a legal attestation and the profile does not "
                f"state an answer"
            )
            answer.reason = "refusing to infer a sensitive answer"
            return answer

        # Optional field with no grounded answer: leave it blank rather than guess.
        if not field.required and semantic_key not in semantics.FREE_TEXT_KEYS:
            if not self._worth_asking(field, semantic_key):
                answer.skip = True
                answer.reason = "optional field with no grounded value; left blank"
                answer.resolver = "deterministic"
                return answer

        return self._resolve_with_qwen(field, answer, job_context)

    # ------------------------------------------------------- deterministic

    def _deterministic_value(self, field: FormField, key: str
                             ) -> Optional[tuple[Any, str]]:
        """Return (value, source_path) when the profile answers this directly."""
        a = self.applicant
        simple = {
            "first_name": ("first_name", a.first_name),
            "last_name": ("last_name", a.last_name),
            "full_name": ("legal_name", a.full_name),
            "preferred_name": ("preferred_name", a.get("preferred_name") or a.first_name),
            "email": ("email", a.get("email")),
            "phone": ("phone", a.get("phone")),
            "city": ("city", a.get("city")),
            "state": ("state", a.get("state")),
            "country": ("country", a.get("country")),
            "address": ("address_line1", a.get("address_line1")),
            "postal_code": ("postal_code", a.get("postal_code")),
            "linkedin": ("linkedin", a.get("linkedin")),
            "github": ("github", a.get("github")),
            "portfolio": ("portfolio", a.get("portfolio")),
            "university": ("university", a.get("university")),
            "major": ("major", a.get("major")),
            "gpa": ("gpa", a.get("gpa")),
            "graduation_date": ("graduation_date", a.get("graduation_date")),
            "years_experience": ("years_experience", a.get("years_experience")),
            "salary_expectation": ("salary_preferences", a.get("salary_preferences")),
            "start_date": ("earliest_start_date", a.get("earliest_start_date")),
            "how_did_you_hear": ("how_did_you_hear", a.get("how_did_you_hear")),
            "current_employer": ("employment_history[0].company",
                                 a.get("employment_history[0].company")),
            "current_title": ("employment_history[0].title",
                              a.get("employment_history[0].title")),
            "security_clearance": ("security_clearance", a.get("security_clearance")),
            "criminal_history": ("criminal_history", a.get("criminal_history")),
            "visa_status": ("visa_status", a.get("visa_status")),
        }
        if key in simple:
            path, value = simple[key]
            if value not in (None, "", [], {}):
                return value, f"profile.{path}"
            return None

        if key == "resume_upload":
            return (self.resume_path, "resume_file") if self.resume_path else None

        if key == "degree":
            value = a.get("degree")
            return (value, "profile.degree") if value else None

        if key == "work_authorization":
            verdict = _interpret_yes_no(a.get("work_authorization"))
            if verdict:
                return verdict, "profile.work_authorization"
            return None

        if key == "sponsorship_required":
            verdict = _interpret_yes_no(a.get("sponsorship_requirement"))
            if verdict:
                return verdict, "profile.sponsorship_requirement"
            return None

        if key == "relocation":
            verdict = _interpret_yes_no(a.get("relocation_preferences"))
            if verdict:
                return verdict, "profile.relocation_preferences"
            if a.get("relocation_preferences") and field.type in ("text", "textarea"):
                return a.get("relocation_preferences"), "profile.relocation_preferences"
            return None

        if key == "remote_preference":
            preference = str(a.get("remote_preferences") or "")
            if not preference:
                return None
            if field.options:
                lowered = preference.lower()
                for family, words in REMOTE_SYNONYMS.items():
                    if any(w in lowered for w in words):
                        for option in field.options:
                            label = str(option.get("label") or "").lower()
                            if any(w in label for w in REMOTE_SYNONYMS[family]):
                                return option.get("label"), "profile.remote_preferences"
                return None
            return preference, "profile.remote_preferences"

        if key == "acknowledgement" and field.type in ("checkbox", "radio"):
            # Affirming that the submitted information is accurate is true by
            # construction: everything submitted came from the profile.
            return "yes", "policy.acknowledgement"

        if key == "cover_letter":
            policy = a.get("cover_letter_policy") or {}
            path = policy.get("cover_letter_path")
            if field.type == "file" and path:
                return path, "profile.cover_letter_policy.cover_letter_path"
            return None

        return None

    def _coerce_to_options(self, field: FormField, value: Any,
                           semantic_key: str) -> Optional[Any]:
        """Map a profile value onto the field's allowed values, or None if it cannot be."""
        if not field.options:
            return value
        if semantic_key == "degree":
            option = _match_degree_option(str(value), field.options)
            if option:
                return option.get("label")
        option = best_option(value, field.options)
        return option.get("label") if option else None

    def _worth_asking(self, field: FormField, semantic_key: str) -> bool:
        """Is it worth spending a model call on this optional field?"""
        if field.options and _decline_option(field.options):
            return True
        return semantic_key in ("open_question", "why_this_company", "other")

    # ------------------------------------------------------- demographics

    def _resolve_demographic(self, field: FormField, answer: ResolvedAnswer
                             ) -> ResolvedAnswer:
        key = answer.semantic_key
        disclosed = self.applicant.demographic_value(key)
        if disclosed:
            matched = best_option(disclosed, field.options) if field.options else None
            answer.value = matched.get("label") if matched else disclosed
            answer.confidence = 0.95
            answer.source = f"profile.demographic_response_policy.{key}"
            answer.resolver = "policy"
            answer.safe_to_submit = bool(answer.value)
            answer.reason = "self-disclosed value from profile policy"
            return answer

        if self.applicant.declines_demographics(key):
            option = _decline_option(field.options) if field.options else None
            if option:
                answer.value = option.get("label")
                answer.confidence = 0.95
                answer.source = "policy.demographic_decline"
                answer.resolver = "policy"
                answer.safe_to_submit = True
                answer.reason = "selected the decline-to-answer option per policy"
                return answer
            if not field.required:
                answer.skip = True
                answer.resolver = "policy"
                answer.reason = "declined; field is optional and has no decline option"
                return answer
            answer.blocked_reason = (
                f"demographic field '{key}' is required but offers no "
                f"decline-to-answer option"
            )
            answer.reason = "policy declines and no decline option exists"
            return answer

        answer.blocked_reason = f"no demographic policy for '{key}'"
        return answer

    # --------------------------------------------------------------- qwen

    def _resolve_with_qwen(self, field: FormField, answer: ResolvedAnswer,
                           job_context: dict[str, Any]) -> ResolvedAnswer:
        proposal = self.qwen.answer_form_question(
            field.prompt_view(),
            self.applicant.answer_json(),
            self.applicant.resume_text,
            job_context,
            {"demographic_response_policy": self.applicant.demographic_policy,
             "cover_letter_policy": self.applicant.get("cover_letter_policy") or {}},
        )
        answer.resolver = "qwen"
        answer.confidence = proposal.confidence
        answer.source = proposal.source or "unknown"
        answer.reason = (proposal.reason or "")[:300]
        answer.value = proposal.answer

        if not proposal.safe_to_submit:
            answer.safe_to_submit = False
            answer.blocked_reason = f"model declined to answer: {answer.reason}"[:300]
            return answer
        if proposal.source in ("", "unknown"):
            answer.safe_to_submit = False
            answer.blocked_reason = "model answer has no source in the profile or resume"
            return answer
        if proposal.confidence < self.min_confidence:
            answer.safe_to_submit = False
            answer.blocked_reason = (
                f"confidence {proposal.confidence:.2f} below threshold "
                f"{self.min_confidence:.2f}"
            )
            return answer

        validation = self.validate(field, answer)
        if not validation[0]:
            answer.safe_to_submit = False
            answer.blocked_reason = validation[1]
            return answer

        answer.value = validation[2]
        answer.safe_to_submit = True
        return answer

    # --------------------------------------------------------- validation

    def validate(self, field: FormField, answer: ResolvedAnswer) -> tuple[bool, str, Any]:
        """Check an answer against the field's constraints and against grounding.

        Returns (ok, problem, normalized_value).
        """
        value = answer.value
        if value in (None, "", []):
            return False, "empty answer", value

        if field.options:
            option = best_option(value, field.options)
            if option is None:
                return False, "answer is not one of the allowed values", value
            value = option.get("label")

        if field.maxlength and isinstance(value, str) and len(value) > field.maxlength:
            value = value[: field.maxlength]

        if field.type == "email" and isinstance(value, str):
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
                return False, "not a valid email address", value

        if field.pattern and isinstance(value, str):
            try:
                if not re.match(field.pattern, value):
                    return False, f"does not match required pattern {field.pattern}", value
            except re.error:
                pass

        if answer.resolver == "qwen" and not field.options:
            grounded, problem = self._grounding_check(field, answer, value)
            if not grounded:
                return False, problem, value

        return True, "", value

    def _grounding_check(self, field: FormField, answer: ResolvedAnswer,
                         value: Any) -> tuple[bool, str]:
        """Reject free-text answers that assert facts absent from the sources of truth.

        Short factual answers must appear verbatim in the profile/resume corpus.
        Long prose is checked by the model, which sees the same sources.
        """
        if not isinstance(value, str):
            return True, ""
        text = value.strip()
        if len(text) <= 80:
            normalized = re.sub(r"\s+", " ", text.lower())
            if normalized in self._corpus:
                return True, ""
            if re.fullmatch(r"[\d\s\-+().,/$]*", normalized):
                return True, ""            # pure numbers/dates carry no claim
            if normalized in ("yes", "no", "n/a", "none", "not applicable"):
                return True, ""
            return False, "short answer is not traceable to the profile or resume"

        verdict = self.qwen.validate_answer(
            field.prompt_view(), text, self.applicant.answer_json(),
            self.applicant.resume_text,
        )
        if not verdict.grounded:
            return False, f"validator found ungrounded content: {verdict.reason[:150]}"
        if not verdict.valid:
            return False, f"validator rejected the answer: {'; '.join(verdict.problems)[:150]}"
        return True, ""
