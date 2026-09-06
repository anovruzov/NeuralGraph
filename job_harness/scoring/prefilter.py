"""Deterministic filters that run BEFORE any model call.

Every job rejected here is a model call saved, so the checks are cheap,
explainable, and conservative: they only reject on unambiguous signals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..config.settings import DiscoveryConfig, ScoringConfig
from ..database.models import Job

# Roles that are not software/AI engineering at all.
OFF_DOMAIN_MARKERS = (
    "recruiter", "sales", "account executive", "customer success", "marketing",
    "designer", "ux ", "accountant", "paralegal", "nurse", "driver", "barista",
    "teacher", "technician", "warehouse", "security guard", "clinical",
    "physician", "attorney", "hr business partner", "talent partner",
)

INTERN_MARKERS = ("intern", "internship", "co-op", "coop ", "apprentice")

YEARS_PATTERNS = [
    re.compile(r"(\d{1,2})\s*\+\s*years", re.I),
    re.compile(r"(?:minimum|at least|min\.?)\s*(?:of\s*)?(\d{1,2})\s*years", re.I),
    re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s*years", re.I),
    re.compile(r"(\d{1,2})\s*years?\s+of\s+(?:relevant\s+|professional\s+|industry\s+)?experience", re.I),
]

CLEARANCE_PATTERN = re.compile(
    r"\b(active\s+)?(ts/sci|top secret|secret clearance|security clearance|polygraph)\b", re.I
)

US_LOCATION_MARKERS = (
    "united states", "usa", "u.s.", "us-", "remote - us", "remote (us",
    "remote, us", "anywhere in the us", "us remote",
)

NON_US_MARKERS = (
    "london", "berlin", "paris", "toronto", "vancouver", "bangalore",
    "bengaluru", "hyderabad", "singapore", "sydney", "tokyo", "dublin",
    "amsterdam", "zurich", "tel aviv", "sao paulo", "mexico city", "warsaw",
    "united kingdom", "canada", "india", "germany", "france", "australia",
    "japan", "ireland", "netherlands", "switzerland", "israel", "brazil",
    "poland", "spain", "portugal", "sweden", "norway", "denmark", "emea",
    "apac", "latam",
)


@dataclass
class PrefilterResult:
    passed: bool
    reason: str = ""
    rule: str = ""
    min_years_required: Optional[int] = None
    detected_seniority: Optional[str] = None


def parse_min_years(text: str) -> Optional[int]:
    """Smallest explicitly-required years-of-experience figure in the text."""
    found: list[int] = []
    for pattern in YEARS_PATTERNS:
        for match in pattern.finditer(text or ""):
            try:
                found.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    return min(found) if found else None


def parse_posted_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text[:len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    # Epoch milliseconds (Ashby, some Workday feeds).
    if text.isdigit():
        num = int(text)
        try:
            if num > 1e11:
                num //= 1000
            return datetime.fromtimestamp(num, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    # ATS listings usually prefix these ("Posted 3 Days Ago"), so search, not match.
    relative = re.search(r"(\d+)\+?\s*(day|hour|week|month)s?\s+ago", text, re.I)
    if relative:
        n, unit = int(relative.group(1)), relative.group(2).lower()
        delta = {"hour": timedelta(hours=n), "day": timedelta(days=n),
                 "week": timedelta(weeks=n), "month": timedelta(days=30 * n)}[unit]
        return (datetime.now(timezone.utc) - delta).date()
    if re.search(r"\b(today|just posted|posted today|yesterday)\b", text, re.I):
        if re.search(r"\byesterday\b", text, re.I):
            return (datetime.now(timezone.utc) - timedelta(days=1)).date()
        return datetime.now(timezone.utc).date()
    return None


def detect_seniority(title: str) -> str:
    t = f" {(title or '').lower()} "
    if any(m in t for m in INTERN_MARKERS):
        return "intern"
    if any(m in t for m in ("new grad", "new graduate", "university grad", "grad program")):
        return "new_grad"
    if any(m in t for m in ("junior", " jr ", " jr.", "entry level", "entry-level",
                            "associate", " i ", " l1 ", " level 1")):
        return "junior"
    if any(m in t for m in ("senior", " sr ", " sr.", " iii ", " l5 ", " l6 ")):
        return "senior"
    if any(m in t for m in ("staff", "principal", "distinguished", "fellow", "architect")):
        return "staff_plus"
    if any(m in t for m in ("director", "vp ", "vice president", "head of", "manager",
                            "chief", "cto")):
        return "management"
    return "mid"


def is_remote(job: Job) -> bool:
    text = f"{job.location or ''} {job.remote_status or ''}".lower()
    if "remote" in text:
        return "hybrid" not in text
    return (job.remote_status or "").lower() == "remote"


def prefilter(job: Job, scoring: ScoringConfig, discovery: DiscoveryConfig,
              today: Optional[date] = None) -> PrefilterResult:
    today = today or datetime.now(timezone.utc).date()
    title = (job.title or "").lower()
    padded_title = f" {title} "
    description = (job.description or "")
    blob = f"{title} {description}".lower()

    seniority = detect_seniority(job.title or "")

    if any(m in padded_title for m in OFF_DOMAIN_MARKERS):
        return PrefilterResult(False, f"off-domain title: {job.title}", "off_domain",
                               detected_seniority=seniority)

    if not scoring.allow_senior_roles:
        for marker in scoring.senior_title_markers:
            if marker.strip() and marker.lower() in padded_title:
                return PrefilterResult(
                    False, f"senior/management title blocked by config: '{marker.strip()}'",
                    "senior_title", detected_seniority=seniority)
        if seniority in ("staff_plus", "management"):
            return PrefilterResult(False, f"seniority '{seniority}' blocked by config",
                                   "senior_title", detected_seniority=seniority)

    if any(m in padded_title for m in INTERN_MARKERS):
        return PrefilterResult(False, "internship posting", "internship",
                               detected_seniority=seniority)

    posted = parse_posted_date(job.posted_date)
    if posted is not None and discovery.freshness_days > 0:
        age = (today - posted).days
        if age > discovery.freshness_days:
            return PrefilterResult(False, f"posted {age}d ago (limit {discovery.freshness_days}d)",
                                   "stale", detected_seniority=seniority)

    if discovery.remote_only and not is_remote(job):
        return PrefilterResult(False, f"not remote: {job.location}", "not_remote",
                               detected_seniority=seniority)

    if scoring.require_us_work_location and not is_remote(job):
        loc = (job.location or "").lower()
        if loc and not any(m in loc for m in US_LOCATION_MARKERS):
            if any(m in loc for m in NON_US_MARKERS):
                return PrefilterResult(False, f"non-US location: {job.location}",
                                       "non_us", detected_seniority=seniority)

    if CLEARANCE_PATTERN.search(blob):
        return PrefilterResult(False, "requires a security clearance", "clearance",
                               detected_seniority=seniority)

    min_years = parse_min_years(description)
    if min_years is not None and min_years > scoring.max_years_experience_required:
        return PrefilterResult(
            False, f"requires {min_years}+ years (limit {scoring.max_years_experience_required})",
            "years_experience", min_years_required=min_years, detected_seniority=seniority)

    return PrefilterResult(True, "passed deterministic prefilter", "",
                           min_years_required=min_years, detected_seniority=seniority)
