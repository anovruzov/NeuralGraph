"""Deterministic label -> semantic key classification.

Runs before any model call. Patterns are ordered: the first match wins, so more
specific patterns are listed first.
"""
from __future__ import annotations

import re
from typing import Optional

# (compiled pattern, semantic_key). Matched against label + name + placeholder.
LABEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [(re.compile(p, re.I), k) for p, k in [
    # Identity
    (r"\b(first|given)[\s_-]*name\b|\bfname\b", "first_name"),
    (r"\b(last|family|sur)[\s_-]*name\b|\blname\b", "last_name"),
    (r"\bpreferred[\s_-]*(first[\s_-]*)?name\b|\bnickname\b|what.*(go|called) by", "preferred_name"),
    (r"\b(full|legal)[\s_-]*name\b|^name$|\byour name\b", "full_name"),
    (r"\be[-\s]?mail\b", "email"),
    (r"\b(phone|mobile|cell|telephone)\b", "phone"),
    (r"\bpronouns?\b", "pronouns"),

    # Work status. Sponsorship is checked before generic authorization because
    # both mention "visa" and the answers are opposite.
    (r"sponsor(ship)?", "sponsorship_required"),
    (r"(legally )?authoriz(ed|ation) to work|work authorization|right to work|eligible to work",
     "work_authorization"),
    (r"\bvisa\b|\bimmigration status\b|\bh-?1b\b|\bopt\b|\bcpt\b", "visa_status"),
    (r"security clearance|\bclearance\b|ts/sci|top secret", "security_clearance"),
    (r"felony|criminal|convicted|background check consent", "criminal_history"),
    (r"\b(are you|have you).*(18|eighteen)\b|age.*(18|eighteen)", "age_verification"),

    # Location
    (r"\b(street|address\s*line|address1|mailing address)\b", "address"),
    (r"\b(zip|postal)\s*code\b", "postal_code"),
    (r"\bcity\b|\btown\b|\blocality\b", "city"),
    (r"\b(state|province|region)\b", "state"),
    (r"\bcountry\b", "country"),
    (r"\b(current\s+)?location\b|where are you (currently )?(based|located)", "city"),

    # Links
    (r"linked\s*-?\s*in", "linkedin"),
    (r"\bgithub\b|\bgit hub\b", "github"),
    (r"\b(portfolio|personal (site|website)|website|web site|url)\b", "portfolio"),
    (r"\b(twitter|x\.com)\b", "twitter"),

    # Documents
    (r"\b(resume|cv|curriculum vitae)\b", "resume_upload"),
    (r"cover\s*letter", "cover_letter"),

    # Education
    (r"\b(school|university|college|institution)\b", "university"),
    (r"\bdegree\b|\bqualification\b|highest level of education|level of education|"
     r"education level|highest (education|degree)", "degree"),
    (r"\b(discipline|major|field of study|concentration)\b", "major"),
    (r"\b(graduation|grad)\s*(date|year|month)?\b|expected graduation", "graduation_date"),
    (r"\bgpa\b|grade point", "gpa"),

    # Experience / logistics
    (r"years?\s+of\s+[\w\s/&,-]{0,60}?experience|(number|how many)\s+(of\s+)?years|"
     r"experience.*\byears\b", "years_experience"),
    (r"\b(desired|expected|target)?\s*(salary|compensation|pay|rate)\b|salary expectation",
     "salary_expectation"),
    (r"\b(start date|available to start|availability|earliest.*(start|join))\b", "start_date"),
    (r"notice period|how much notice", "notice_period"),
    (r"\brelocat(e|ion)\b", "relocation"),
    (r"\b(remote|hybrid|on-?site|in office|work (location )?preference)\b", "remote_preference"),
    (r"how did you (hear|find out|learn)|where did you (hear|find)|referral source",
     "how_did_you_hear"),
    (r"\breferr?(al|ed)\b|who referred you|employee referral", "referral"),
    (r"previously (worked|been employed|applied)|former employee", "prior_employment"),
    (r"current (company|employer)", "current_employer"),
    (r"current (title|role|position)", "current_title"),

    # EEO / demographics
    (r"\bgender\b|\bsex\b", "gender"),
    (r"hispanic|latin(o|a|x)", "hispanic_latino"),
    (r"\brace\b|\bethnicity\b|racial", "race_ethnicity"),
    (r"veteran|military service|protected veteran", "veteran_status"),
    (r"disab(ility|led)|\bada\b", "disability_status"),

    # Free text
    (r"why (do you want|are you interested)|why (this|our) (company|role|team)|"
     r"what (interests|excites) you", "why_this_company"),
    (r"tell us about|describe (a|your)|what (project|experience)", "open_question"),

    # Consent
    (r"\b(i (agree|consent|certify|acknowledge)|terms|privacy policy|accurate and complete)\b",
     "acknowledgement"),
]]

DEMOGRAPHIC_KEYS = {"gender", "race_ethnicity", "hispanic_latino", "veteran_status",
                    "disability_status", "pronouns"}

# Keys the harness must never answer from inference: a wrong value is a false
# legal attestation. Answered only from an explicit profile value.
SENSITIVE_KEYS = {"security_clearance", "criminal_history", "visa_status",
                  "work_authorization", "sponsorship_required", "age_verification",
                  "prior_employment"}

FREE_TEXT_KEYS = {"why_this_company", "open_question", "cover_letter"}


REQUIRED_MARKERS = re.compile(r"[*✱†‡]+|\(\s*required\s*\)|\brequired\b\s*$", re.I)


def clean_label(text: str) -> str:
    """Strip required markers and punctuation noise so patterns can anchor."""
    cleaned = REQUIRED_MARKERS.sub(" ", str(text or ""))
    cleaned = re.sub(r"[_\[\]]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def classify_label(field_label: str, name: str = "", placeholder: str = "",
                   help_text: str = "") -> Optional[str]:
    """Return a semantic key from deterministic patterns, or None."""
    blob = " ".join(clean_label(x) for x in (field_label, name, placeholder, help_text) if x)
    if not blob.strip():
        return None
    for pattern, key in LABEL_PATTERNS:
        if pattern.search(blob):
            return key
    return None


def is_demographic(key: str) -> bool:
    return key in DEMOGRAPHIC_KEYS


def is_sensitive(key: str) -> bool:
    return key in SENSITIVE_KEYS
