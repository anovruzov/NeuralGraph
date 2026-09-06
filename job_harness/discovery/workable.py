"""Workable adapter (public account jobs API).

    https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
Falls back to the older /spi/v3/accounts/{account}/jobs shape when present.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from ..config.logging_setup import get_logger
from ..database.models import Job
from .base import DiscoveryAdapter, guess_remote_status, html_to_text, parse_salary

log = get_logger("discovery.workable")

WIDGET_API = "https://apply.workable.com/api/v1/widget/accounts/{account}?details=true"


class WorkableAdapter(DiscoveryAdapter):
    name = "workable"
    ats_type = "workable"

    def discover(self, target: str) -> Iterator[Job]:
        account = self._board_token(target)
        try:
            payload = self.get_json(WIDGET_API.format(account=account))
        except Exception as exc:
            log.warning("workable fetch failed",
                        extra={"account": account, "error": str(exc)[:200]})
            return
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        for entry in (jobs or [])[: self.config.max_jobs_per_board]:
            job = self.normalize(entry, account)
            if job is not None:
                yield job

    @staticmethod
    def _board_token(target: str) -> str:
        target = target.strip().rstrip("/")
        if "://" in target:
            for marker in ("apply.workable.com/", "/accounts/"):
                if marker in target:
                    return target.split(marker, 1)[1].split("/")[0].split("?")[0]
        return target

    def normalize(self, entry: dict[str, Any], account: str) -> Optional[Job]:
        title = (entry.get("title") or "").strip()
        if not title:
            return None
        description = html_to_text(
            (entry.get("description") or "") + "\n" + (entry.get("requirements") or "")
        )
        location = entry.get("location") or {}
        if isinstance(location, dict):
            location_text = ", ".join(str(v) for v in (
                location.get("city"), location.get("region"),
                location.get("country")) if v)
            remote_flag = bool(location.get("workplace") == "remote"
                               or location.get("telecommuting"))
        else:
            location_text, remote_flag = str(location), False
        salary_min, salary_max = parse_salary(description)
        shortcode = entry.get("shortcode") or entry.get("id")
        apply_url = (entry.get("application_url") or entry.get("url")
                     or f"https://apply.workable.com/{account}/j/{shortcode}/")
        return Job(
            company=entry.get("company_name") or account.replace("-", " ").title(),
            title=title,
            canonical_apply_url=apply_url,
            location=location_text,
            remote_status="remote" if remote_flag
            else guess_remote_status(location_text, description),
            employment_type=entry.get("employment_type"),
            posted_date=(entry.get("published_on") or entry.get("created_at") or "")[:19] or None,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            source_url=entry.get("url") or apply_url,
            ats_type=self.ats_type,
            ats_job_key=f"workable:{account}:{shortcode}",
            raw={"department": entry.get("department"), "board": account},
        )
