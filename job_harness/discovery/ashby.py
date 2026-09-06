"""Ashby job board adapter (public posting API)."""
from __future__ import annotations

from typing import Any, Iterator, Optional

from ..config.logging_setup import get_logger
from ..database.models import Job
from .base import DiscoveryAdapter, guess_remote_status, html_to_text, parse_salary

log = get_logger("discovery.ashby")

POSTING_API = ("https://api.ashbyhq.com/posting-api/job-board/{token}"
               "?includeCompensation=true")


class AshbyAdapter(DiscoveryAdapter):
    name = "ashby"
    ats_type = "ashby"

    def discover(self, target: str) -> Iterator[Job]:
        token = self._board_token(target)
        try:
            payload = self.get_json(POSTING_API.format(token=token))
        except Exception as exc:
            log.warning("ashby board fetch failed",
                        extra={"board": token, "error": str(exc)[:200]})
            return
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else (payload or [])
        for entry in jobs[: self.config.max_jobs_per_board]:
            job = self.normalize(entry, token)
            if job is not None:
                yield job

    @staticmethod
    def _board_token(target: str) -> str:
        target = target.strip().rstrip("/")
        if "://" in target:
            for marker in ("jobs.ashbyhq.com/", "job-board/"):
                if marker in target:
                    return target.split(marker, 1)[1].split("/")[0].split("?")[0]
        return target

    def normalize(self, entry: dict[str, Any], token: str) -> Optional[Job]:
        title = (entry.get("title") or "").strip()
        if not title:
            return None
        description = (entry.get("descriptionPlain")
                       or html_to_text(entry.get("descriptionHtml") or ""))
        location = entry.get("location") or ""
        if not location and entry.get("address"):
            addr = (entry.get("address") or {}).get("postalAddress") or {}
            location = ", ".join(
                str(v) for v in (addr.get("addressLocality"), addr.get("addressRegion"),
                                 addr.get("addressCountry")) if v
            )
        salary_min, salary_max = parse_salary(description)
        comp = entry.get("compensation") or {}
        for tier in (comp.get("compensationTiers") or []):
            lo, hi = tier.get("minValue"), tier.get("maxValue")
            if isinstance(lo, (int, float)) and lo > 20000:
                salary_min = salary_min or int(lo)
            if isinstance(hi, (int, float)) and hi > 20000:
                salary_max = salary_max or int(hi)
        apply_url = entry.get("applyUrl") or entry.get("jobUrl") or ""
        return Job(
            company=entry.get("organizationName") or token.replace("-", " ").title(),
            title=title,
            canonical_apply_url=apply_url,
            location=location,
            remote_status=("remote" if entry.get("isRemote")
                           else guess_remote_status(location, description)),
            employment_type=entry.get("employmentType"),
            posted_date=(entry.get("publishedAt") or entry.get("updatedAt") or "")[:19] or None,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            source_url=entry.get("jobUrl") or apply_url,
            ats_type=self.ats_type,
            ats_job_key=f"ashby:{token}:{entry.get('id')}",
            raw={"team": entry.get("team"), "board": token},
        )
