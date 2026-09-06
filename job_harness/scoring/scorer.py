"""Job scoring: deterministic prefilter, then a Qwen rubric score."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..config.logging_setup import get_logger
from ..config.settings import Config
from ..database.models import Job, JobStatus
from ..profile.applicant import Applicant
from ..qwen.qwen_client import QwenClient
from ..qwen.schemas import JobScore
from .prefilter import PrefilterResult, prefilter

log = get_logger("scoring")


@dataclass
class ScoreDecision:
    job_id: str
    score: int
    decision: str                    # APPLY | SKIP
    status: str                      # QUEUED | SKIPPED
    reason: str
    breakdown: dict[str, Any] = field(default_factory=dict)
    prefiltered: bool = False
    requirements: Optional[dict[str, Any]] = None


class Scorer:
    def __init__(self, config: Config, qwen: QwenClient, applicant: Applicant) -> None:
        self.config = config
        self.qwen = qwen
        self.applicant = applicant

    def score(self, job: Job, today: Optional[date] = None) -> ScoreDecision:
        pre: PrefilterResult = prefilter(job, self.config.scoring, self.config.discovery, today)
        if not pre.passed:
            return ScoreDecision(
                job_id=job.job_id, score=0, decision="SKIP", status=JobStatus.SKIPPED,
                reason=f"prefilter[{pre.rule}]: {pre.reason}", prefiltered=True,
                breakdown={"prefilter_rule": pre.rule},
            )

        # A relevance classification is far cheaper than a full rubric score, so it
        # gates the expensive call.
        classification = self.qwen.classify_job(
            {"title": job.title, "company": job.company, "location": job.location,
             "description": (job.description or "")[:1500]},
            self.config.discovery.target_roles,
        )
        if not classification.is_relevant:
            return ScoreDecision(
                job_id=job.job_id, score=0, decision="SKIP", status=JobStatus.SKIPPED,
                reason=f"not relevant to target roles: {classification.reason}"[:400],
                breakdown={"role_family": classification.role_family,
                           "seniority": classification.seniority},
            )
        if (not self.config.scoring.allow_senior_roles
                and classification.seniority in ("staff_plus", "management")):
            return ScoreDecision(
                job_id=job.job_id, score=0, decision="SKIP", status=JobStatus.SKIPPED,
                reason=f"classified seniority '{classification.seniority}' is blocked",
                breakdown={"seniority": classification.seniority},
            )

        requirements = None
        if job.description and len(job.description) > 400:
            requirements = self.qwen.extract_requirements(job.description).model_dump(mode="json")
            if requirements.get("security_clearance_required"):
                return ScoreDecision(
                    job_id=job.job_id, score=0, decision="SKIP", status=JobStatus.SKIPPED,
                    reason="requires a security clearance", requirements=requirements,
                )

        result: JobScore = self.qwen.score_job(
            {"title": job.title, "company": job.company, "location": job.location,
             "remote_status": job.remote_status or classification.remote_status,
             "posted_date": job.posted_date, "salary_min": job.salary_min,
             "salary_max": job.salary_max, "description": job.description},
            self.applicant.redacted_summary(),
            self.applicant.resume_text,
            self.config.scoring.weights,
            requirements,
        )

        decision, status, reason = self._decide(result)
        breakdown = result.breakdown.model_dump(mode="json")
        breakdown["seniority"] = classification.seniority
        breakdown["experience_plausible"] = result.experience_plausible
        breakdown["required_qualifications_met"] = result.required_qualifications_met
        if result.missing_required:
            breakdown["missing_required"] = result.missing_required[:10]

        log.info("scored job", extra={"job_id": job.job_id, "company": job.company,
                                      "title": job.title, "score": result.score,
                                      "decision": decision})
        return ScoreDecision(
            job_id=job.job_id, score=result.score, decision=decision, status=status,
            reason=reason, breakdown=breakdown, requirements=requirements,
        )

    def _decide(self, result: JobScore) -> tuple[str, str, str]:
        cfg = self.config.scoring
        score, reason = result.score, (result.reason or "")[:400]

        if score >= cfg.apply_threshold:
            if not result.required_qualifications_met and result.missing_required:
                # A score above threshold with unmet hard requirements is contradictory;
                # trust the explicit requirement signal.
                return ("SKIP", JobStatus.SKIPPED,
                        f"score {score} but required qualifications unmet: "
                        f"{', '.join(result.missing_required[:3])}")
            return "APPLY", JobStatus.QUEUED, f"score {score} >= {cfg.apply_threshold}. {reason}"

        if score >= cfg.borderline_threshold:
            if result.experience_plausible:
                return ("APPLY", JobStatus.QUEUED,
                        f"borderline score {score} accepted: experience requirements "
                        f"remain plausible. {reason}")
            return ("SKIP", JobStatus.SKIPPED,
                    f"borderline score {score} rejected: experience requirements "
                    f"not plausible. {reason}")

        return "SKIP", JobStatus.SKIPPED, f"score {score} < {cfg.borderline_threshold}. {reason}"
