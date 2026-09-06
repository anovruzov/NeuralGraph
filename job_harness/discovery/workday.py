"""Workday adapter.

Workday tenants expose a public CxS JSON search endpoint per career site:
    https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
A target is given as "tenant/site" plus an optional host, or as a career-site URL.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from ..config.logging_setup import get_logger
from ..database.models import Job
from .base import DiscoveryAdapter, guess_remote_status, html_to_text, parse_salary

log = get_logger("discovery.workday")

_POSTED_RELATIVE = re.compile(r"(\d+)\+?\s*(day|hour|week|month)s?\s*ago", re.I)


class WorkdayAdapter(DiscoveryAdapter):
    name = "workday"
    ats_type = "workday"

    def discover(self, target: str) -> Iterator[Job]:
        parsed = self.parse_target(target)
        if parsed is None:
            log.warning("unrecognized workday target", extra={"target": target})
            return
        host, tenant, site = parsed
        endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        offset, limit = 0, 20
        seen = 0
        while seen < self.config.max_jobs_per_board:
            body = {"appliedFacets": {}, "limit": limit, "offset": offset,
                    "searchText": ""}
            try:
                resp = self.client.post(endpoint, json=body,
                                        headers={"Content-Type": "application/json",
                                                 "Accept": "application/json"})
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                log.warning("workday fetch failed",
                            extra={"tenant": tenant, "site": site, "error": str(exc)[:200]})
                return
            postings = payload.get("jobPostings") or []
            if not postings:
                return
            for entry in postings:
                job = self.normalize(entry, host, tenant, site)
                if job is not None:
                    yield job
                seen += 1
                if seen >= self.config.max_jobs_per_board:
                    return
            offset += limit
            if offset >= int(payload.get("total") or 0):
                return

    @staticmethod
    def parse_target(target: str) -> Optional[tuple[str, str, str]]:
        """Return (host, tenant, site) from 'tenant/site', 'host|tenant/site', or a URL."""
        target = target.strip()
        if "://" in target:
            parts = urlsplit(target)
            host = parts.netloc
            segments = [s for s in parts.path.split("/") if s]
            if "cxs" in segments:
                i = segments.index("cxs")
                if len(segments) > i + 2:
                    return host, segments[i + 1], segments[i + 2]
            tenant = host.split(".")[0]
            # /en-US/SiteName/... or /SiteName/...
            for seg in segments:
                if re.match(r"^[a-z]{2}-[A-Z]{2}$", seg):
                    continue
                if seg.lower() in ("job", "jobs", "details"):
                    break
                return host, tenant, seg
            return None
        if "|" in target:
            host, _, rest = target.partition("|")
            if "/" in rest:
                tenant, _, site = rest.partition("/")
                return host, tenant, site
            return None
        if "/" in target:
            tenant, _, site = target.partition("/")
            return f"{tenant}.wd1.myworkdayjobs.com", tenant, site
        return None

    def normalize(self, entry: dict[str, Any], host: str, tenant: str,
                  site: str) -> Optional[Job]:
        title = (entry.get("title") or "").strip()
        if not title:
            return None
        path = entry.get("externalPath") or ""
        apply_url = f"https://{host}/{site}{path}" if path.startswith("/") else \
            f"https://{host}/{site}/{path}"
        location = entry.get("locationsText") or ""
        posted_date = self._posted(entry.get("postedOn") or entry.get("startDate"))
        description = html_to_text(entry.get("jobDescription") or "")
        salary_min, salary_max = parse_salary(description)
        return Job(
            company=tenant.replace("-", " ").title(),
            title=title,
            canonical_apply_url=apply_url,
            location=location,
            remote_status=guess_remote_status(location, description),
            employment_type=entry.get("timeType"),
            posted_date=posted_date,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            source_url=apply_url,
            ats_type=self.ats_type,
            ats_job_key=f"workday:{tenant}:{entry.get('bulletFields', [path])[0]}",
            raw={"tenant": tenant, "site": site, "host": host, "path": path},
        )

    @staticmethod
    def _posted(value: Any) -> Optional[str]:
        if not value:
            return None
        text = str(value)
        match = _POSTED_RELATIVE.search(text)
        if match:
            n, unit = int(match.group(1)), match.group(2).lower()
            delta = {"hour": timedelta(hours=n), "day": timedelta(days=n),
                     "week": timedelta(weeks=n), "month": timedelta(days=30 * n)}[unit]
            return (datetime.now(timezone.utc) - delta).date().isoformat()
        if re.search(r"today|just posted", text, re.I):
            return datetime.now(timezone.utc).date().isoformat()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return None
