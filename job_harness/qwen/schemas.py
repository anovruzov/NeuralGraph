"""Pydantic response schemas. Every Qwen call is validated against one of these."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class JobClassification(StrictModel):
    is_relevant: bool
    role_family: str = Field(default="other")
    seniority: Literal["intern", "new_grad", "junior", "mid", "senior", "staff_plus", "management", "unknown"] = "unknown"
    remote_status: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    equivalent_title: str = ""
    reason: str = ""


class ScoreBreakdown(StrictModel):
    technical_fit: int = 0
    resume_evidence: int = 0
    ai_relevance: int = 0
    career_upside: int = 0
    company_comp: int = 0
    application_friction: int = 0


class JobScore(StrictModel):
    score: int
    breakdown: ScoreBreakdown
    decision: Literal["APPLY", "BORDERLINE", "SKIP"]
    required_qualifications_met: bool
    experience_plausible: bool
    missing_required: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("score")
    @classmethod
    def clamp(cls, v: int) -> int:
        return max(0, min(100, int(v)))


class Requirements(StrictModel):
    required: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    min_years_experience: Optional[int] = None
    max_years_experience: Optional[int] = None
    degree_required: Optional[str] = None
    work_authorization_required: Optional[str] = None
    sponsorship_available: Optional[bool] = None
    locations: list[str] = Field(default_factory=list)
    security_clearance_required: bool = False


class FieldClassification(StrictModel):
    """What a form field is asking for, in harness vocabulary."""
    semantic_key: str                 # e.g. "email", "years_experience", "work_authorization"
    field_type: str                   # text | email | tel | select | radio | checkbox | textarea | file | date | url | number
    is_required: bool = False
    is_demographic: bool = False
    is_sensitive: bool = False        # clearance/citizenship/criminal/medical
    expects_free_text: bool = False
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class FieldAnswer(StrictModel):
    """The contract from section 8 of the spec."""
    field_type: str = ""
    answer: Any = ""
    confidence: float = 0.0
    source: str = ""                  # profile path, resume, decline_policy, or "unknown"
    safe_to_submit: bool = False
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class DuplicateVerdict(StrictModel):
    is_duplicate: bool
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class SubmissionEvaluation(StrictModel):
    """Did this page indicate a successful submission?"""
    submitted: bool
    confidence: float = 0.0
    signal: str = ""                  # confirmation_page | confirmation_message | application_id | redirect | none
    application_id: Optional[str] = None
    blocker: Optional[str] = None     # captcha | login | assessment | verification | validation_error | none
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class AnswerValidation(StrictModel):
    valid: bool
    grounded: bool                    # traceable to profile/resume, not invented
    normalized_answer: Any = None
    problems: list[str] = Field(default_factory=list)
    reason: str = ""


SCHEMA_BY_FUNCTION: dict[str, type[StrictModel]] = {
    "classify_job": JobClassification,
    "score_job": JobScore,
    "extract_requirements": Requirements,
    "classify_form_field": FieldClassification,
    "answer_form_question": FieldAnswer,
    "detect_duplicate": DuplicateVerdict,
    "evaluate_application": SubmissionEvaluation,
    "validate_answer": AnswerValidation,
}


def json_skeleton(model: type[BaseModel]) -> str:
    """Compact field list to embed in a prompt (cheaper than a full JSON Schema)."""
    lines = []
    for name, f in model.model_fields.items():
        ann = f.annotation
        text = getattr(ann, "__name__", str(ann)).replace("typing.", "")
        lines.append(f'  "{name}": <{text}>')
    return "{\n" + ",\n".join(lines) + "\n}"
