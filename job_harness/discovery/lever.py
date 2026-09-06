"""Lever job board adapter (public postings API)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from ..config.logging_setup import get_logger
from ..database.models import Job
from .base import DiscoveryAdapter, guess_remote_status, html_to_text, parse_salary

log = get_logger("discovery.lever")

POSTINGS_API = "https://api.lever.co/v0/postings/{token}?mode=json"


class LeverAdapter(DiscoveryAdapter):
    name = "lever"
    ats_type = "lever"

    def discover(self, target: str) -> Iterator[Job]:
        token = self._board_token(target)
        try:
            payload = self.get_json(POSTINGS_API.format(token=token))
        except Exception as exc:
            log.warning("lever board fetch failed",
                        extra={"board": token, "error": str(exc)[:200]})
            return
        for entry in (payload or [])[: self.config.max_jobs_per_board]:
            job = self.normalize(entry, token)
            if job is not None:
                yield job

    @staticmethod
    def _board_token(target: str) -> str:
        target = target.strip().rstrip("/")
        if "://" in target:
            for marker in ("jobs.lever.co/", "api.lever.co/v0/postings/"):
                if marker in target:
                    return target.split(marker, 1)[1].split("/")[0].split("?")[0]
        return target

    def normalize(self, entry: dict[str, Any], token: str) -> Optional[Job]:
        title = (entry.get("text") or "").strip()
        if not title:
            return None
        categories = entry.get("categories") or {}
        description = html_to_text(entry.get("description") or "")
        lists = entry.get("lists") or []
        for block in lists:
            description += "\n\n" + (block.get("text") or "") + "\n" + \
                html_to_text(block.get("content") or "")
        description += "\n" + html_to_text(entry.get("additional") or "")
        location = categories.get("location") or ""
        posted = entry.get("createdAt")
        posted_date = None
        if isinstance(posted, (int, float)):
            posted_date = datetime.fromtimestamp(posted / 1000, tz=timezone.utc).date().isoformat()
        salary_min, salary_max = parse_salary(description)
        apply_url = entry.get("hostedUrl") or entry.get("applyUrl") or ""
        return Job(
            company=token.replace("-", " ").title(),
            title=title,
            canonical_apply_url=apply_url,
            location=location,
            remote_status=(categories.get("workplaceType") or
                           guess_remote_status(location, description)),
            employment_type=categories.get("commitment"),
            posted_date=posted_date,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description.strip(),
            source_url=apply_url,
            ats_type=self.ats_type,
            ats_job_key=f"lever:{token}:{entry.get('id')}",
            raw={"team": categories.get("team"), "board": token},
        )
