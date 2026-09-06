"""ATS platform detection from URL and page content."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

ATS_GREENHOUSE = "greenhouse"
ATS_LEVER = "lever"
ATS_ASHBY = "ashby"
ATS_WORKDAY = "workday"
ATS_SMARTRECRUITERS = "smartrecruiters"
ATS_WORKABLE = "workable"
ATS_JOBVITE = "jobvite"
ATS_ICIMS = "icims"
ATS_TALEO = "taleo"
ATS_BAMBOOHR = "bamboohr"
ATS_RIPPLING = "rippling"
ATS_UNKNOWN = "unknown"

URL_SIGNATURES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(boards|job-boards)\.greenhouse\.io|greenhouse\.io/embed|grnh\.se"), ATS_GREENHOUSE),
    (re.compile(r"jobs\.lever\.co|api\.lever\.co"), ATS_LEVER),
    (re.compile(r"jobs\.ashbyhq\.com|ashbyhq\.com/.*/jobs|api\.ashbyhq\.com"), ATS_ASHBY),
    (re.compile(r"myworkdayjobs\.com|myworkdaysite\.com|workday\.com"), ATS_WORKDAY),
    (re.compile(r"jobs\.smartrecruiters\.com|smartrecruiters\.com"), ATS_SMARTRECRUITERS),
    (re.compile(r"apply\.workable\.com|workable\.com"), ATS_WORKABLE),
    (re.compile(r"jobs\.jobvite\.com|jobvite\.com"), ATS_JOBVITE),
    (re.compile(r"\.icims\.com"), ATS_ICIMS),
    (re.compile(r"taleo\.net"), ATS_TALEO),
    (re.compile(r"\.bamboohr\.com"), ATS_BAMBOOHR),
    (re.compile(r"ats\.rippling\.com"), ATS_RIPPLING),
]

DOM_SIGNATURES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"greenhouse[-_]?(?:board|application|iframe)|id=[\"']grnhse", re.I), ATS_GREENHOUSE),
    (re.compile(r"lever-application|data-lever|postings-btn", re.I), ATS_LEVER),
    (re.compile(r"ashby-application-form|_ashby|ashbyhq", re.I), ATS_ASHBY),
    (re.compile(r"wd\d-cdn|workdayjobs|data-automation-id", re.I), ATS_WORKDAY),
    (re.compile(r"smartrecruiters", re.I), ATS_SMARTRECRUITERS),
    (re.compile(r"workable", re.I), ATS_WORKABLE),
    (re.compile(r"icims", re.I), ATS_ICIMS),
]

# Where the real application form lives relative to a job page, per ATS.
APPLY_HINTS: dict[str, list[str]] = {
    ATS_GREENHOUSE: ["#application", "#app_body", "form#application_form", "#grnhse_app"],
    ATS_LEVER: ["/apply"],
    ATS_ASHBY: ["/application"],
    ATS_WORKDAY: ["apply"],
}


@dataclass
class AtsDetection:
    ats_type: str
    confidence: float
    signal: str = ""

    @property
    def known(self) -> bool:
        return self.ats_type != ATS_UNKNOWN


def detect_from_url(url: str) -> AtsDetection:
    for pattern, ats in URL_SIGNATURES:
        if pattern.search(url or ""):
            return AtsDetection(ats, 0.95, f"url:{pattern.pattern[:30]}")
    return AtsDetection(ATS_UNKNOWN, 0.0)


def detect_from_html(html: str) -> AtsDetection:
    sample = (html or "")[:200000]
    for pattern, ats in DOM_SIGNATURES:
        if pattern.search(sample):
            return AtsDetection(ats, 0.8, f"dom:{pattern.pattern[:30]}")
    return AtsDetection(ATS_UNKNOWN, 0.0)


def detect(url: str, html: Optional[str] = None) -> AtsDetection:
    by_url = detect_from_url(url)
    if by_url.known:
        return by_url
    if html:
        return detect_from_html(html)
    return AtsDetection(ATS_UNKNOWN, 0.0)


def apply_url_for(url: str, ats_type: str) -> str:
    """Canonical application URL for a job page, when the ATS uses a distinct one."""
    url = (url or "").rstrip("/")
    if ats_type == ATS_LEVER and not url.endswith("/apply"):
        return f"{url}/apply"
    if ats_type == ATS_ASHBY and not url.endswith("/application"):
        return f"{url}/application"
    return url
