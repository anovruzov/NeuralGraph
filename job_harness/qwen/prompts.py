"""Prompt construction. Kept terse on purpose: tokens are the dominant cost."""
from __future__ import annotations

import json
from typing import Any

from .schemas import (
    AnswerValidation, BatchJobClassification, DuplicateVerdict, FieldAnswer,
    FieldClassification, JobClassification, JobScore, Requirements,
    SubmissionEvaluation, TitleExpansion, json_skeleton,
)

GROUNDING_RULES = (
    "GROUNDING RULES (absolute):\n"
    "- The applicant profile JSON and resume text are the ONLY sources of personal fact.\n"
    "- NEVER invent or infer: degrees, employers, job titles, dates, GPA, skills, "
    "publications, citizenship, visa or work-authorization status, security clearances, "
    "salary history, addresses, or identifiers.\n"
    "- If a fact is not present in the provided sources, set source=\"unknown\", "
    "safe_to_submit=false, and leave answer empty. Refusing to answer is CORRECT behavior.\n"
    "- Never guess a value that would be a legal attestation.\n"
)

JSON_RULES = (
    "Return ONE JSON object and nothing else. No markdown fences, no prose, no "
    "explanation outside the JSON. Use the exact keys shown."
)


def _fmt(skeleton: str) -> str:
    return f"{JSON_RULES}\nShape:\n{skeleton}"


def system_prompt(function: str) -> str:
    base = "You are a precise data-extraction and decision engine for an automated job-application harness. "
    if function in ("answer_form_question", "validate_answer", "classify_form_field"):
        return base + "You never fabricate personal facts.\n" + GROUNDING_RULES
    return base + "You output strict JSON."


def classify_job(job: dict[str, Any], target_roles: list[str]) -> str:
    return (
        f"Classify this job posting against the candidate's target roles.\n"
        f"Target roles: {', '.join(target_roles)}\n"
        f"Semantically equivalent titles also count as relevant.\n\n"
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company')}\n"
        f"Location: {job.get('location')}\n"
        f"Excerpt: {(job.get('description') or '')[:1200]}\n\n"
        f"{_fmt(json_skeleton(JobClassification))}"
    )


def classify_jobs_batch(jobs: list[dict[str, Any]], target_roles: list[str]) -> str:
    listing = "\n".join(
        f"{i}. {j.get('title')} | {j.get('company')} | {j.get('location')} | "
        f"{(j.get('description') or '')[:280]}"
        for i, j in enumerate(jobs)
    )
    return (
        f"Classify each job posting against the candidate's target roles.\n"
        f"Target roles: {', '.join(target_roles)}\n"
        f"Semantically equivalent titles also count as relevant.\n\n"
        f"POSTINGS ({len(jobs)}), one per line, numbered:\n{listing}\n\n"
        f"Return one result per posting, in the same order, {len(jobs)} in total.\n"
        f"{JSON_RULES}\nShape:\n"
        '{\n  "results": [\n' + json_skeleton(JobClassification) + "\n  ]\n}"
    )


def expand_titles(target_roles: list[str], seen_titles: list[str]) -> str:
    return (
        "List job titles that are semantically equivalent to these target roles, "
        "as used by real employers. Include common abbreviations and variants "
        "(for example 'MLE' for Machine Learning Engineer). Do not include "
        "management, staff or principal variants, and do not include titles from "
        "other professions.\n\n"
        f"Target roles: {', '.join(target_roles)}\n"
        + (f"Titles already seen on job boards, for style: "
           f"{', '.join(seen_titles[:40])}\n" if seen_titles else "")
        + f"\n{_fmt(json_skeleton(TitleExpansion))}"
    )


def score_job(job: dict[str, Any], profile_summary: str, resume_text: str,
              weights: dict[str, int], requirements: dict[str, Any] | None) -> str:
    rubric = "\n".join(f"- {k}: max {v}" for k, v in weights.items())
    req_block = (
        f"\nParsed requirements: {json.dumps(requirements)[:1200]}\n" if requirements else ""
    )
    return (
        "Score how well this candidate fits this job, 0-100, using the rubric.\n"
        f"Rubric (points allocate to these components, total 100):\n{rubric}\n\n"
        "Rules:\n"
        "- Separate REQUIRED from PREFERRED qualifications. Missing PREFERRED items must "
        "not by itself cause a low score or a SKIP.\n"
        "- Judge whether the stated years-of-experience requirement is still plausible for "
        "this candidate (a '3+ years' ask is often plausible for a strong early-career candidate; "
        "'8+ years' is not).\n"
        "- application_friction is HIGH score for a short standard ATS form, LOW for "
        "assessments/portfolios/long essays.\n"
        "- decision: APPLY if score >= 65, BORDERLINE if 50-64, SKIP if < 50.\n\n"
        f"JOB\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')} | Remote: {job.get('remote_status')}\n"
        f"Posted: {job.get('posted_date')} | Salary: {job.get('salary_min')}-{job.get('salary_max')}\n"
        f"Description:\n{(job.get('description') or '')[:6000]}\n"
        f"{req_block}\n"
        f"CANDIDATE PROFILE\n{profile_summary[:2500]}\n\n"
        f"RESUME\n{resume_text[:4000]}\n\n"
        f"{_fmt(json_skeleton(JobScore))}"
    )


def extract_requirements(description: str) -> str:
    return (
        "Extract structured requirements from this job description. Distinguish REQUIRED "
        "(must-have, 'required', 'minimum qualifications') from PREFERRED (nice-to-have, "
        "'preferred', 'bonus'). Do not invent items that are not stated.\n\n"
        f"{description[:8000]}\n\n"
        f"{_fmt(json_skeleton(Requirements))}"
    )


def classify_form_field(field: dict[str, Any]) -> str:
    return (
        "Classify what this web form field is asking for.\n"
        "semantic_key must be a lowercase snake_case canonical name such as: "
        "first_name, last_name, full_name, preferred_name, email, phone, city, state, "
        "country, address, linkedin, github, portfolio, website, resume_upload, "
        "cover_letter, cover_letter_upload, university, degree, major, graduation_date, "
        "gpa, years_experience, work_authorization, sponsorship_required, "
        "visa_status, security_clearance, criminal_history, start_date, "
        "salary_expectation, notice_period, relocation, remote_preference, "
        "how_did_you_hear, referral, gender, race_ethnicity, veteran_status, "
        "disability_status, hispanic_latino, pronouns, acknowledgement, "
        "why_this_company, open_question, other.\n\n"
        f"FIELD\n{json.dumps(field, ensure_ascii=False)[:2500]}\n\n"
        f"{_fmt(json_skeleton(FieldClassification))}"
    )


def answer_form_question(field: dict[str, Any], profile_json: str, resume_text: str,
                         job_context: dict[str, Any], policy: dict[str, Any]) -> str:
    return (
        "Answer one job-application form field using ONLY the applicant profile and resume.\n"
        f"{GROUNDING_RULES}\n"
        "If the field has an allowed-values list, `answer` MUST be exactly one of those "
        "strings (or a list of them for multi-select). For a yes/no question, use the "
        "option text as written on the page.\n"
        "For demographic/EEO fields, follow demographic_response_policy in the profile; "
        "if it says decline, choose the decline option if one exists, else set "
        "safe_to_submit=false.\n"
        "Set source to the JSON path you used (e.g. \"profile.email\", "
        "\"profile.employment_history[0].title\", \"resume\", \"policy.demographic\") or "
        "\"unknown\".\n"
        "For open-ended questions (e.g. 'why do you want to work here'), you MAY compose "
        "prose, but every factual claim in it must come from the profile or resume.\n\n"
        f"FIELD\n{json.dumps(field, ensure_ascii=False)[:2500]}\n\n"
        f"JOB\n{json.dumps(job_context, ensure_ascii=False)[:900]}\n\n"
        f"APPLICANT PROFILE\n{profile_json[:5000]}\n\n"
        f"RESUME\n{resume_text[:3500]}\n\n"
        f"POLICY\n{json.dumps(policy, ensure_ascii=False)[:600]}\n\n"
        f"{_fmt(json_skeleton(FieldAnswer))}"
    )


def detect_duplicate(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> str:
    return (
        "Decide whether the candidate posting is the SAME opening as any of the existing "
        "postings (same company, same role, possibly different board/URL/wording). "
        "Different levels or different locations of the same role are NOT duplicates "
        "unless the location is the only difference and the role is remote.\n\n"
        f"CANDIDATE\n{json.dumps(candidate, ensure_ascii=False)[:900]}\n\n"
        f"EXISTING\n{json.dumps(existing, ensure_ascii=False)[:2500]}\n\n"
        f"{_fmt(json_skeleton(DuplicateVerdict))}"
    )


def evaluate_application(page_state: dict[str, Any]) -> str:
    return (
        "Determine whether a job application was successfully submitted, based on the "
        "page state after clicking Submit. Do NOT assume success. Look for a confirmation "
        "page/message, an application or confirmation ID, a redirect to a thank-you URL, "
        "or a successful response. Validation errors, a still-present submit button with "
        "field errors, a CAPTCHA, or a login wall mean NOT submitted.\n\n"
        f"PAGE STATE\n{json.dumps(page_state, ensure_ascii=False)[:5000]}\n\n"
        f"{_fmt(json_skeleton(SubmissionEvaluation))}"
    )


def validate_answer(field: dict[str, Any], answer: Any, profile_json: str,
                    resume_text: str) -> str:
    return (
        "Validate a proposed form answer before submission.\n"
        "valid = the answer satisfies the field's format/constraints/allowed values.\n"
        "grounded = every factual claim traces to the profile or resume. An answer that "
        "asserts an unsupported degree, employer, date, authorization status, or clearance "
        "is NOT grounded.\n"
        "normalized_answer = the answer coerced to the exact expected format, or null.\n\n"
        f"FIELD\n{json.dumps(field, ensure_ascii=False)[:2000]}\n\n"
        f"PROPOSED ANSWER\n{json.dumps(answer, ensure_ascii=False)[:1500]}\n\n"
        f"APPLICANT PROFILE\n{profile_json[:4000]}\n\n"
        f"RESUME\n{resume_text[:2500]}\n\n"
        f"{_fmt(json_skeleton(AnswerValidation))}"
    )
