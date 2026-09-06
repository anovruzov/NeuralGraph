"""Typed records shared across the harness."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStatus:
    DISCOVERED = "DISCOVERED"
    SCORED = "SCORED"
    SKIPPED = "SKIPPED"
    QUEUED = "QUEUED"
    OPENED = "OPENED"
    FILLING = "FILLING"
    BLOCKED = "BLOCKED"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED = "SUBMITTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"

    TERMINAL = {SKIPPED, VERIFIED, DUPLICATE}
    SUCCESS = {SUBMITTED, VERIFIED}
    ALL = {
        DISCOVERED, SCORED, SKIPPED, QUEUED, OPENED, FILLING, BLOCKED,
        READY_TO_SUBMIT, SUBMITTED, VERIFIED, FAILED, DUPLICATE,
    }


_WS = re.compile(r"\s+")
_TITLE_NOISE = re.compile(
    r"\b(?:job\s*id|req(?:uisition)?\s*(?:id|#)?|#)\s*[:\-]?\s*[a-z0-9\-_]+\b", re.I
)
_PAREN = re.compile(r"\((?:[^()]*)\)")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_COMPANY_SUFFIX = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c|ltd|limited|corp|corporation|co|gmbh|ag|sa|plc|labs?|ai|technologies|technology)\b",
    re.I,
)


def normalize_title(title: str) -> str:
    """Collapse a job title to a comparable key (for dedupe, not display)."""
    t = (title or "").lower()
    t = _TITLE_NOISE.sub(" ", t)
    t = _PAREN.sub(" ", t)
    t = t.replace("&", " and ")
    t = _NON_ALNUM.sub(" ", t)
    # Common ATS decorations that do not change the role identity.
    for noise in (
        "full time", "fulltime", "part time", "contract", "remote", "hybrid",
        "onsite", "on site", "us", "usa", "united states", "new grad",
        "new graduate", "university grad", "early career", "opening", "hiring",
    ):
        t = re.sub(rf"\b{re.escape(noise)}\b", " ", t)
    t = _WS.sub(" ", t).strip()
    return t


def normalize_company(company: str) -> str:
    c = (company or "").lower()
    c = _NON_ALNUM.sub(" ", c)
    c = _COMPANY_SUFFIX.sub(" ", c)
    return _WS.sub(" ", c).strip()


def canonicalize_url(url: str) -> str:
    """Strip tracking parameters and trailing slashes so the same posting maps to one key."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    if not url:
        return ""
    parts = urlsplit(url.strip())
    if not parts.netloc:
        # A relative or malformed URL must stay recognizable rather than be
        # turned into a plausible-looking but wrong absolute URL.
        return url.strip()
    drop_prefixes = ("utm_", "gh_src", "src", "ref", "source", "trk", "lever-",
                     "gclid", "fbclid", "mc_")
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not any(k.lower().startswith(p) for p in drop_prefixes)
    ]
    query.sort()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, urlencode(query), ""))


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class Job:
    """Normalized job posting. `job_id` is derived, never supplied by a source."""

    company: str
    title: str
    canonical_apply_url: str
    job_id: str = ""
    location: Optional[str] = None
    remote_status: Optional[str] = None       # remote | hybrid | onsite | unknown
    employment_type: Optional[str] = None
    posted_date: Optional[str] = None         # ISO date
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    description: str = ""
    source_url: Optional[str] = None
    ats_type: Optional[str] = None
    ats_job_key: Optional[str] = None
    discovered_at: str = field(default_factory=utcnow)
    status: str = JobStatus.DISCOVERED
    score: Optional[int] = None
    score_breakdown: Optional[dict] = None
    score_reason: Optional[str] = None
    scored_at: Optional[str] = None
    raw: Optional[dict] = None

    def __post_init__(self) -> None:
        self.canonical_apply_url = canonicalize_url(self.canonical_apply_url)
        if not self.job_id:
            self.job_id = self.derive_id()

    @property
    def normalized_title(self) -> str:
        return normalize_title(self.title)

    @property
    def normalized_company(self) -> str:
        return normalize_company(self.company)

    @property
    def description_hash(self) -> str:
        return sha256(_WS.sub(" ", (self.description or "").lower()).strip())

    def derive_id(self) -> str:
        basis = "|".join([
            self.normalized_company,
            self.normalized_title,
            self.ats_job_key or self.canonical_apply_url,
        ])
        return sha256(basis)[:20]

    def identity_key(self) -> str:
        return f"{self.normalized_company}|{self.normalized_title}|{self.ats_job_key or self.canonical_apply_url}"

    def to_row(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["normalized_title"] = self.normalized_title
        d["normalized_company"] = self.normalized_company
        d["description_hash"] = self.description_hash
        d["score_breakdown"] = json.dumps(self.score_breakdown) if self.score_breakdown else None
        d["raw"] = json.dumps(self.raw) if self.raw else None
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Job":
        allowed = {f.name for f in dataclasses.fields(cls)}
        data = {k: v for k, v in dict(row).items() if k in allowed}
        for key in ("score_breakdown", "raw"):
            if isinstance(data.get(key), str):
                try:
                    data[key] = json.loads(data[key])
                except (ValueError, TypeError):
                    data[key] = None
        return cls(**data)
