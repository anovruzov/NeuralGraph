"""Adapter for arbitrary seed URLs: a single job page, or a board index page."""
from __future__ import annotations

import json
import re
from typing import Any, Iterator, Optional
from urllib.parse import urljoin

from ..ats import detect as ats_detect
from ..config.logging_setup import get_logger
from ..database.models import Job
from .base import DiscoveryAdapter, guess_remote_status, html_to_text, parse_salary

log = get_logger("discovery.generic")

_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)


class GenericUrlAdapter(DiscoveryAdapter):
    """Parses schema.org JobPosting markup, which most ATS job pages emit."""

    name = "generic_url"
    ats_type = "unknown"

    def discover(self, target: str) -> Iterator[Job]:
        try:
            html = self.get_text(target)
        except Exception as exc:
            log.warning("seed url fetch failed", extra={"url": target, "error": str(exc)[:200]})
            return

        found = False
        for job in self._from_jsonld(html, target):
            found = True
            yield job
        if found:
            return

        # Board index page: follow links that look like job postings on known ATS hosts.
        links = {urljoin(target, href) for href in _HREF.findall(html)}
        candidates = [
            u for u in links
            if ats_detect.detect_from_url(u).known
            and re.search(r"/jobs?/|/postings?/|/job/", u)
        ][: self.config.max_jobs_per_board]
        for url in candidates:
            try:
                page = self.get_text(url)
            except Exception:
                continue
            for job in self._from_jsonld(page, url):
                yield job

    def _from_jsonld(self, html: str, url: str) -> Iterator[Job]:
        for block in _JSONLD.findall(html):
            try:
                data = json.loads(block.strip())
            except ValueError:
                continue
            for node in self._iter_postings(data):
                job = self.normalize(node, url, html)
                if job is not None:
                    yield job

    @staticmethod
    def _iter_postings(data: Any) -> Iterator[dict[str, Any]]:
        if isinstance(data, list):
            for item in data:
                yield from GenericUrlAdapter._iter_postings(item)
        elif isinstance(data, dict):
            if data.get("@type") == "JobPosting":
                yield data
            for key in ("@graph", "itemListElement"):
                if key in data:
                    yield from GenericUrlAdapter._iter_postings(data[key])

    def normalize(self, node: dict[str, Any], url: str, html: str = "") -> Optional[Job]:
        title = (node.get("title") or "").strip()
        if not title:
            match = _TITLE.search(html or "")
            title = html_to_text(match.group(1)) if match else ""
        if not title:
            return None
        org = node.get("hiringOrganization") or {}
        company = (org.get("name") if isinstance(org, dict) else str(org)) or ""
        description = html_to_text(node.get("description") or "")
        location = self._location(node)
        salary_min, salary_max = self._salary(node)
        if not salary_min:
            salary_min, salary_max = parse_salary(description)
        detection = ats_detect.detect(url, html)
        apply_url = ats_detect.apply_url_for(
            node.get("url") or node.get("sameAs") or url, detection.ats_type
        )
        return Job(
            company=company or "Unknown",
            title=title,
            canonical_apply_url=apply_url,
            location=location,
            remote_status=("remote" if node.get("jobLocationType") == "TELECOMMUTE"
                           else guess_remote_status(location, description)),
            employment_type=(node.get("employmentType") if isinstance(node.get("employmentType"), str)
                             else None),
            posted_date=(node.get("datePosted") or "")[:19] or None,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            source_url=url,
            ats_type=detection.ats_type if detection.known else None,
            ats_job_key=node.get("identifier", {}).get("value")
            if isinstance(node.get("identifier"), dict) else None,
            raw={"source": "jsonld"},
        )

    @staticmethod
    def _location(node: dict[str, Any]) -> str:
        loc = node.get("jobLocation")
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        if isinstance(loc, dict):
            addr = loc.get("address") or {}
            if isinstance(addr, dict):
                return ", ".join(str(v) for v in (
                    addr.get("addressLocality"), addr.get("addressRegion"),
                    addr.get("addressCountry")) if v)
            return str(addr)
        if node.get("jobLocationType") == "TELECOMMUTE":
            return "Remote"
        return ""

    @staticmethod
    def _salary(node: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        base = node.get("baseSalary")
        if not isinstance(base, dict):
            return None, None
        value = base.get("value")
        if not isinstance(value, dict):
            return None, None
        unit = str(value.get("unitText") or "").upper()
        lo, hi = value.get("minValue"), value.get("maxValue")
        try:
            lo = int(float(lo)) if lo is not None else None
            hi = int(float(hi)) if hi is not None else None
        except (TypeError, ValueError):
            return None, None
        if unit in ("HOUR", "DAY", "WEEK"):
            return None, None
        if lo and lo < 10000:
            return None, None
        return lo, hi
