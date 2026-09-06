"""Deduplication: deterministic keys first, Qwen only for genuine ambiguity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..config.logging_setup import get_logger
from ..database.db import Database
from ..database.models import Job, normalize_title

log = get_logger("dedupe")


@dataclass
class DedupeVerdict:
    is_duplicate: bool
    existing_job_id: Optional[str] = None
    rule: str = ""
    confidence: float = 1.0


def _token_overlap(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class Deduplicator:
    """Order of checks, cheapest first:
       1. canonical URL   2. ATS job key   3. company+normalized title
       4. company + description hash       5. Qwen, only for near-miss titles
    """

    def __init__(self, db: Database, qwen: Any = None,
                 llm_threshold: float = 0.55) -> None:
        self.db = db
        self.qwen = qwen
        self.llm_threshold = llm_threshold

    def check(self, job: Job) -> DedupeVerdict:
        row = self.db.query_one(
            "SELECT job_id FROM jobs WHERE canonical_apply_url=?", (job.canonical_apply_url,)
        )
        if row:
            return DedupeVerdict(True, row["job_id"], "canonical_url")

        if job.ats_job_key:
            row = self.db.query_one(
                "SELECT job_id FROM jobs WHERE ats_job_key=?", (job.ats_job_key,)
            )
            if row:
                return DedupeVerdict(True, row["job_id"], "ats_job_key")

        row = self.db.query_one(
            "SELECT job_id FROM jobs WHERE normalized_company=? AND normalized_title=?",
            (job.normalized_company, job.normalized_title),
        )
        if row:
            return DedupeVerdict(True, row["job_id"], "company_title")

        if job.description:
            row = self.db.query_one(
                "SELECT job_id FROM jobs WHERE normalized_company=? AND description_hash=?",
                (job.normalized_company, job.description_hash),
            )
            if row:
                return DedupeVerdict(True, row["job_id"], "description_hash")

        # Near-miss titles at the same company: ask the model, but only then.
        siblings = self.db.query(
            "SELECT job_id, company, title, location FROM jobs WHERE normalized_company=? LIMIT 20",
            (job.normalized_company,),
        )
        near = [
            dict(r) for r in siblings
            if _token_overlap(job.normalized_title,
                              normalize_title(r["title"])) >= self.llm_threshold
        ]
        if near and self.qwen is not None:
            verdict = self.qwen.detect_duplicate(
                {"company": job.company, "title": job.title, "location": job.location},
                [{"job_id": n["job_id"], "company": n["company"], "title": n["title"],
                  "location": n["location"]} for n in near[:5]],
            )
            if verdict.is_duplicate and verdict.confidence >= 0.7:
                return DedupeVerdict(True, near[0]["job_id"], "qwen", verdict.confidence)

        return DedupeVerdict(False)
