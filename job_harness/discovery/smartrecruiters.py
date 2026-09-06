"""SmartRecruiters adapter (public postings API).

    https://api.smartrecruiters.com/v1/companies/{company}/postings
Listings are summaries; the detail endpoint carries the description, and is
fetched only for postings whose title already looks relevant.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from ..config.logging_setup import get_logger
from ..database.models import Job
from .base import DiscoveryAdapter, guess_remote_status, html_to_text, parse_salary

log = get_logger("discovery.smartrecruiters")

LIST_API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
DETAIL_API = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"


class SmartRecruitersAdapter(DiscoveryAdapter):
    name = "smartrecruiters"
    ats_type = "smartrecruiters"

    def discover(self, target: str) -> Iterator[Job]:
        company = self._board_token(target)
        offset, limit = 0, 100
        yielded = 0
        while yielded < self.config.max_jobs_per_board:
            try:
                payload = self.get_json(
                    f"{LIST_API.format(company=company)}?limit={limit}&offset={offset}")
            except Exception as exc:
                log.warning("smartrecruiters fetch failed",
                            extra={"company": company, "error": str(exc)[:200]})
                return
            postings = payload.get("content") or []
            if not postings:
                return
            for entry in postings:
                if yielded >= self.config.max_jobs_per_board:
                    return
                job = self.normalize(entry, company)
                if job is not None:
                    yield job
                    yielded += 1
            offset += limit
            if offset >= int(payload.get("totalFound") or 0):
                return

    @staticmethod
    def _board_token(target: str) -> str:
        target = target.strip().rstrip("/")
        if "://" in target:
            for marker in ("careers.smartrecruiters.com/", "jobs.smartrecruiters.com/",
                           "/v1/companies/"):
                if marker in target:
                    return target.split(marker, 1)[1].split("/")[0].split("?")[0]
        return target

    def _description(self, company: str, posting_id: str) -> str:
        try:
            detail = self.get_json(DETAIL_API.format(company=company, posting_id=posting_id))
        except Exception:
            return ""
        sections = ((detail.get("jobAd") or {}).get("sections") or {})
        parts = []
        for key in ("companyDescription", "jobDescription", "qualifications",
                    "additionalInformation"):
            text = (sections.get(key) or {}).get("text") or ""
            if text:
                parts.append(html_to_text(text))
        return "\n\n".join(parts)

    def normalize(self, entry: dict[str, Any], company: str) -> Optional[Job]:
        title = (entry.get("name") or "").strip()
        if not title:
            return None
        # The detail fetch costs a request, so skip it for clearly irrelevant titles.
        description = ""
        posting_id = entry.get("id")
        if posting_id and self.matches_targets(title, self.config.target_roles):
            description = self._description(company, str(posting_id))

        location = entry.get("location") or {}
        location_text = ", ".join(str(v) for v in (
            location.get("city"), location.get("region"), location.get("country")) if v)
        remote = "remote" if location.get("remote") else \
            guess_remote_status(location_text, description)
        salary_min, salary_max = parse_salary(description)
        company_name = ((entry.get("company") or {}).get("name")
                        or company.replace("-", " ").title())
        apply_url = (entry.get("applyUrl")
                     or f"https://jobs.smartrecruiters.com/{company}/{posting_id}")
        return Job(
            company=company_name,
            title=title,
            canonical_apply_url=apply_url,
            location=location_text,
            remote_status=remote,
            employment_type=(entry.get("typeOfEmployment") or {}).get("label"),
            posted_date=(entry.get("releasedDate") or entry.get("createdOn") or "")[:19] or None,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            source_url=apply_url,
            ats_type=self.ats_type,
            ats_job_key=f"smartrecruiters:{company}:{posting_id}",
            raw={"department": (entry.get("department") or {}).get("label"),
                 "board": company},
        )
