"""Greenhouse job board adapter (public boards API)."""
from __future__ import annotations

from typing import Any, Iterator, Optional

from ..config.logging_setup import get_logger
from ..database.models import Job
from .base import DiscoveryAdapter, guess_remote_status, html_to_text, parse_salary

log = get_logger("discovery.greenhouse")

BOARD_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseAdapter(DiscoveryAdapter):
    name = "greenhouse"
    ats_type = "greenhouse"

    def discover(self, target: str) -> Iterator[Job]:
        token = self._board_token(target)
        url = f"{BOARD_API.format(token=token)}?content=true"
        try:
            payload = self.get_json(url)
        except Exception as exc:
            log.warning("greenhouse board fetch failed",
                        extra={"board": token, "error": str(exc)[:200]})
            return
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        for entry in (jobs or [])[: self.config.max_jobs_per_board]:
            job = self.normalize(entry, token)
            if job is not None:
                yield job

    @staticmethod
    def _board_token(target: str) -> str:
        target = target.strip().rstrip("/")
        if "://" in target:
            for marker in ("/boards/", "boards.greenhouse.io/", "job-boards.greenhouse.io/"):
                if marker in target:
                    tail = target.split(marker, 1)[1]
                    return tail.split("/")[0]
        return target

    def normalize(self, entry: dict[str, Any], token: str) -> Optional[Job]:
        title = (entry.get("title") or "").strip()
        if not title:
            return None
        content = html_to_text(entry.get("content") or "")
        location = (entry.get("location") or {}).get("name") or ""
        apply_url = entry.get("absolute_url") or \
            f"https://boards.greenhouse.io/{token}/jobs/{entry.get('id')}"
        salary_min, salary_max = parse_salary(content)
        company = (entry.get("company_name")
                   or (entry.get("offices") or [{}])[0].get("name")
                   or token.replace("-", " ").title())
        departments = ", ".join(d.get("name", "") for d in (entry.get("departments") or []))
        return Job(
            company=company,
            title=title,
            canonical_apply_url=apply_url,
            location=location,
            remote_status=guess_remote_status(location, content),
            employment_type=None,
            posted_date=(entry.get("updated_at") or entry.get("first_published") or "")[:19] or None,
            salary_min=salary_min,
            salary_max=salary_max,
            description=content,
            source_url=apply_url,
            ats_type=self.ats_type,
            ats_job_key=f"greenhouse:{token}:{entry.get('id')}",
            raw={"departments": departments, "board": token, "gh_id": entry.get("id")},
        )
