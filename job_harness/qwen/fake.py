"""A deterministic in-process stand-in for a Qwen endpoint.

Used by tests and by `--fake-qwen` so the entire pipeline can be exercised
end-to-end without a model server. It reads the same prompts a real model would
and answers with rule-based logic, so it exercises the real parsing, validation,
caching and cost paths. It is NOT a model and is never used in `--apply` mode
unless explicitly requested.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx


def _body(prompt: str) -> str:
    """Prompt text minus the trailing JSON-shape instructions.

    The shape block names schema fields (e.g. security_clearance_required) that
    would otherwise trip this backend's keyword heuristics.
    """
    cut = prompt.find("Return ONE JSON object")
    return prompt[:cut] if cut > 0 else prompt


def _extract_block(prompt: str, header: str) -> str:
    """Pull the text following a section header up to the next blank-line header."""
    idx = prompt.find(header)
    if idx < 0:
        return ""
    rest = prompt[idx + len(header):]
    stop = len(rest)
    for marker in ("\nJOB\n", "\nRESUME\n", "\nPOLICY\n", "\nFIELD\n",
                   "\nAPPLICANT PROFILE\n", "\nCANDIDATE PROFILE\n",
                   "\nEXISTING\n", "\nPAGE STATE\n", "\nPROPOSED ANSWER\n",
                   "Return ONE JSON object"):
        pos = rest.find(marker)
        if 0 <= pos < stop:
            stop = pos
    return rest[:stop].strip()


def _json_after(prompt: str, header: str) -> dict[str, Any]:
    block = _extract_block(prompt, header)
    start = block.find("{")
    if start < 0:
        return {}
    depth, in_str, esc = 0, False, False
    for i in range(start, len(block)):
        ch = block[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(block[start:i + 1])
                except ValueError:
                    return {}
    return {}


AI_TERMS = ("machine learning", "ml ", "llm", "ai ", "artificial intelligence",
            "deep learning", "nlp", "rag", "retrieval", "agent", "model",
            "pytorch", "transformer", "inference", "evaluation", "fine-tun")
SENIOR = ("staff", "principal", "director", "vp ", "vice president",
          "engineering manager", "head of", "distinguished")


class FakeQwenTransport(httpx.BaseTransport):
    """httpx transport that answers /chat/completions locally."""

    def __init__(self, failure_mode: Optional[str] = None) -> None:
        # failure_mode: None | "malformed_once" | "always_malformed" | "http_500"
        self.failure_mode = failure_mode
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.failure_mode == "http_500":
            return httpx.Response(500, json={"error": "boom"}, request=request)

        body = json.loads(request.content or b"{}")
        messages = body.get("messages", [])
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        is_repair_turn = "corrected JSON object" in user
        if is_repair_turn:
            user = next((m["content"] for m in messages if m["role"] == "user"), "")

        content = json.dumps(self.answer(user))
        if self.failure_mode == "always_malformed" or (
            self.failure_mode == "malformed_once" and self.calls == 1 and not is_repair_turn
        ):
            content = "Sure! Here you go:\n```json\n{oops: not json,,"

        return httpx.Response(
            200,
            json={
                "id": f"fake-{self.calls}",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": max(1, len(user) // 4),
                          "completion_tokens": max(1, len(content) // 4),
                          "total_tokens": max(2, (len(user) + len(content)) // 4)},
            },
            request=request,
        )

    # ------------------------------------------------------------ dispatch

    def answer(self, prompt: str) -> dict[str, Any]:
        if prompt.startswith("Classify this job posting"):
            return self._classify_job(prompt)
        if prompt.startswith("Score how well"):
            return self._score_job(prompt)
        if prompt.startswith("Extract structured requirements"):
            return self._requirements(prompt)
        if prompt.startswith("Classify what this web form field"):
            return self._classify_field(prompt)
        if prompt.startswith("Answer one job-application form field"):
            return self._answer_field(prompt)
        if prompt.startswith("Decide whether the candidate posting"):
            return self._duplicate(prompt)
        if prompt.startswith("Determine whether a job application"):
            return self._evaluate(prompt)
        if prompt.startswith("Validate a proposed form answer"):
            return self._validate(prompt)
        return {"ok": True}

    # ------------------------------------------------------------ handlers

    def _classify_job(self, prompt: str) -> dict[str, Any]:
        title = re.search(r"^Title: (.*)$", prompt, re.M)
        title_text = (title.group(1) if title else "").lower()
        low = _body(prompt).lower()
        relevant = any(t in title_text for t in
                       ("engineer", "scientist", "researcher", "research"))
        seniority = "unknown"
        if any(s in title_text for s in SENIOR):
            seniority = "staff_plus"
        elif "senior" in title_text or "sr." in title_text:
            seniority = "senior"
        elif any(s in title_text for s in ("intern",)):
            seniority = "intern"
        elif any(s in title_text for s in ("new grad", "university grad", "graduate")):
            seniority = "new_grad"
        elif any(s in title_text for s in ("junior", "jr", "associate", "i ", " i")):
            seniority = "junior"
        remote = "remote" if "remote" in low else ("hybrid" if "hybrid" in low else "unknown")
        return {
            "is_relevant": relevant and any(t in low for t in AI_TERMS),
            "role_family": "ai_engineering" if any(t in low for t in AI_TERMS) else "software",
            "seniority": seniority,
            "remote_status": remote,
            "equivalent_title": title.group(1) if title else "",
            "reason": "keyword heuristics (fake backend)",
        }

    def _score_job(self, prompt: str) -> dict[str, Any]:
        # Only look at the job block: the instruction text itself mentions
        # words like "assessments" that would otherwise skew the heuristics.
        low = _extract_block(prompt, "\nJOB\n").lower() or _body(prompt).lower()
        title = re.search(r"^Title: (.*)$", prompt, re.M)
        title_text = (title.group(1) if title else "").lower()
        ai_hits = sum(1 for t in AI_TERMS if t in low)
        tech = min(30, 10 + ai_hits * 3)
        evidence = 18 if "python" in low else 12
        ai_rel = min(20, 6 + ai_hits * 2)
        upside = 8
        comp = 8 if "salary" in low or "$" in prompt else 6
        friction = 4 if "assessment" not in low else 1
        senior = any(s in title_text for s in SENIOR)
        if senior:
            tech = max(0, tech - 20)
        years = re.search(r"(\d+)\+?\s*years", low)
        yrs = int(years.group(1)) if years else 0
        plausible = yrs <= 5
        if not plausible:
            tech = max(0, tech - 10)
        total = tech + evidence + ai_rel + upside + comp + friction
        decision = "APPLY" if total >= 65 else ("BORDERLINE" if total >= 50 else "SKIP")
        return {
            "score": total,
            "breakdown": {"technical_fit": tech, "resume_evidence": evidence,
                          "ai_relevance": ai_rel, "career_upside": upside,
                          "company_comp": comp, "application_friction": friction},
            "decision": decision,
            "required_qualifications_met": not senior and plausible,
            "experience_plausible": plausible,
            "missing_required": [] if plausible else [f"{yrs}+ years experience"],
            "reason": "heuristic score (fake backend)",
        }

    def _requirements(self, prompt: str) -> dict[str, Any]:
        body = _body(prompt)
        low = body.lower()
        req, pref = [], []
        for line in body.splitlines():
            l = line.strip("-• \t")
            if not l or len(l) > 200:
                continue
            ll = l.lower()
            if any(k in ll for k in ("required", "must have", "minimum")):
                req.append(l[:120])
            elif any(k in ll for k in ("preferred", "nice to have", "bonus", "plus")):
                pref.append(l[:120])
        years = re.search(r"(\d+)\+?\s*(?:\-\s*\d+\s*)?years", low)
        return {
            "required": req[:10],
            "preferred": pref[:10],
            "min_years_experience": int(years.group(1)) if years else None,
            "max_years_experience": None,
            "degree_required": "bachelors" if "bachelor" in low else None,
            "work_authorization_required": "us" if "authorized to work" in low else None,
            "sponsorship_available": None if "sponsor" not in low else ("not" not in low),
            "locations": [],
            "security_clearance_required": "clearance" in low,
        }

    def _classify_field(self, prompt: str) -> dict[str, Any]:
        field = _json_after(prompt, "FIELD\n")
        label = " ".join(str(x) for x in (
            field.get("label"), field.get("name"), field.get("placeholder"),
            field.get("aria_label"))).lower()
        ftype = field.get("type") or "text"
        table = [
            ("first name", "first_name"), ("last name", "last_name"),
            ("full name", "full_name"), ("preferred name", "preferred_name"),
            ("email", "email"), ("phone", "phone"), ("linkedin", "linkedin"),
            ("github", "github"), ("portfolio", "portfolio"), ("website", "website"),
            ("resume", "resume_upload"), ("cv", "resume_upload"),
            ("cover letter", "cover_letter"), ("school", "university"),
            ("university", "university"), ("degree", "degree"),
            ("discipline", "major"), ("major", "major"),
            ("graduation", "graduation_date"), ("gpa", "gpa"),
            ("years of experience", "years_experience"),
            ("authorized to work", "work_authorization"),
            ("work authorization", "work_authorization"),
            ("sponsorship", "sponsorship_required"), ("visa", "visa_status"),
            ("clearance", "security_clearance"), ("felony", "criminal_history"),
            ("start date", "start_date"), ("salary", "salary_expectation"),
            ("compensation", "salary_expectation"), ("notice", "notice_period"),
            ("relocat", "relocation"), ("remote", "remote_preference"),
            ("how did you hear", "how_did_you_hear"), ("referral", "referral"),
            ("gender", "gender"), ("race", "race_ethnicity"),
            ("ethnicity", "race_ethnicity"), ("veteran", "veteran_status"),
            ("disability", "disability_status"), ("hispanic", "hispanic_latino"),
            ("pronoun", "pronouns"), ("city", "city"), ("state", "state"),
            ("country", "country"), ("location", "city"),
            ("why", "why_this_company"),
        ]
        key = "other"
        for needle, semantic in table:
            if needle in label:
                key = semantic
                break
        demographic = key in ("gender", "race_ethnicity", "veteran_status",
                              "disability_status", "hispanic_latino", "pronouns")
        sensitive = key in ("security_clearance", "criminal_history", "visa_status")
        return {
            "semantic_key": key,
            "field_type": ftype,
            "is_required": bool(field.get("required")),
            "is_demographic": demographic,
            "is_sensitive": sensitive,
            "expects_free_text": ftype in ("textarea",) or key in ("why_this_company", "open_question"),
            "confidence": 0.9 if key != "other" else 0.3,
            "reason": "label lookup (fake backend)",
        }

    def _answer_field(self, prompt: str) -> dict[str, Any]:
        field = _json_after(prompt, "FIELD\n")
        profile = _json_after(prompt, "APPLICANT PROFILE\n")
        label = str(field.get("label") or field.get("name") or "").lower()
        options = field.get("options") or []
        option_texts = [o.get("label") if isinstance(o, dict) else str(o) for o in options]

        if "why" in label and field.get("type") == "textarea":
            skills = ", ".join((profile.get("skills") or [])[:5])
            return {"field_type": "textarea",
                    "answer": (f"I build AI systems and my background centers on {skills}. "
                               "I am interested in this role because it focuses on applied "
                               "AI engineering, which is where my project work is concentrated."),
                    "confidence": 0.8, "source": "profile.skills",
                    "safe_to_submit": True, "reason": "composed from profile skills"}

        if option_texts:
            for opt in option_texts:
                if opt and opt.lower().startswith(("prefer not", "decline", "i don't wish")):
                    return {"field_type": field.get("type", "select"), "answer": opt,
                            "confidence": 0.9, "source": "policy.demographic",
                            "safe_to_submit": True, "reason": "declined per policy"}
            return {"field_type": field.get("type", "select"), "answer": "",
                    "confidence": 0.2, "source": "unknown", "safe_to_submit": False,
                    "reason": "no grounded option match (fake backend)"}

        return {"field_type": field.get("type", "text"), "answer": "",
                "confidence": 0.1, "source": "unknown", "safe_to_submit": False,
                "reason": "not present in profile (fake backend)"}

    def _duplicate(self, prompt: str) -> dict[str, Any]:
        cand = _json_after(prompt, "CANDIDATE\n")
        block = _extract_block(prompt, "EXISTING\n")
        try:
            existing = json.loads(block[block.find("["):block.rfind("]") + 1])
        except ValueError:
            existing = []
        ct, cc = str(cand.get("title", "")).lower(), str(cand.get("company", "")).lower()
        for e in existing:
            if str(e.get("company", "")).lower() == cc and str(e.get("title", "")).lower() == ct:
                return {"is_duplicate": True, "confidence": 0.95,
                        "reason": "identical company and title"}
        return {"is_duplicate": False, "confidence": 0.8, "reason": "no match"}

    def _evaluate(self, prompt: str) -> dict[str, Any]:
        state = _json_after(prompt, "PAGE STATE\n")
        text = json.dumps(state).lower()
        app_id = None
        m = re.search(r"(?:confirmation|application)\s*(?:id|number|#)[:\s]*([a-z0-9\-]{4,})", text)
        if m:
            app_id = m.group(1)
        if any(k in text for k in ("captcha", "recaptcha", "hcaptcha")):
            return {"submitted": False, "confidence": 0.9, "signal": "none",
                    "application_id": None, "blocker": "captcha", "reason": "captcha present"}
        if any(k in text for k in ("thank you for applying", "application received",
                                   "application submitted", "we have received your application",
                                   "thanks for applying")):
            return {"submitted": True, "confidence": 0.92,
                    "signal": "application_id" if app_id else "confirmation_message",
                    "application_id": app_id, "blocker": None,
                    "reason": "confirmation text present"}
        if "error" in text or "required" in text:
            return {"submitted": False, "confidence": 0.8, "signal": "none",
                    "application_id": None, "blocker": "validation_error",
                    "reason": "validation errors present"}
        return {"submitted": False, "confidence": 0.5, "signal": "none",
                "application_id": None, "blocker": None, "reason": "no confirmation signal"}

    def _validate(self, prompt: str) -> dict[str, Any]:
        field = _json_after(prompt, "FIELD\n")
        block = _extract_block(prompt, "PROPOSED ANSWER\n")
        try:
            answer = json.loads(block)
        except ValueError:
            answer = block
        profile_text = _extract_block(prompt, "APPLICANT PROFILE\n").lower()
        options = [o.get("label") if isinstance(o, dict) else str(o)
                   for o in (field.get("options") or [])]
        if options and isinstance(answer, str) and answer not in options:
            return {"valid": False, "grounded": False, "normalized_answer": None,
                    "problems": ["answer not among allowed values"],
                    "reason": "option mismatch"}
        grounded = True
        if isinstance(answer, str) and len(answer) < 120 and answer:
            token = answer.strip().lower()
            if token not in profile_text and not options and "@" not in token:
                grounded = token in ("yes", "no", "true", "false") or any(
                    ch.isdigit() for ch in token)
        return {"valid": bool(answer != ""), "grounded": grounded,
                "normalized_answer": answer, "problems": [],
                "reason": "fake validator"}


def fake_client(config: Any, db: Any = None, run_id: Optional[str] = None,
                failure_mode: Optional[str] = None):
    from .qwen_client import QwenClient
    return QwenClient(config, db=db, run_id=run_id,
                      transport=FakeQwenTransport(failure_mode=failure_mode))
