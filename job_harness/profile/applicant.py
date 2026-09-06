"""Applicant source of truth.

`applicant.json` and the resume file are the ONLY sources of personal fact.
Nothing in this module invents a value: every lookup either resolves to
something present in the profile or returns None.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config.logging_setup import get_logger

log = get_logger("profile")

REQUIRED_FIELDS = ["legal_name", "email", "phone"]

PROFILE_TEMPLATE: dict[str, Any] = {
    "legal_name": "",
    "preferred_name": "",
    "email": "",
    "phone": "",
    "city": "",
    "state": "",
    "country": "",
    "linkedin": "",
    "github": "",
    "portfolio": "",
    "university": "",
    "degree": "",
    "major": "",
    "graduation_date": "",
    "gpa": "",
    "work_authorization": "",
    "sponsorship_requirement": "",
    "relocation_preferences": "",
    "remote_preferences": "",
    "salary_preferences": "",
    "employment_history": [],
    "skills": [],
    "demographic_response_policy": {
        "gender": "decline",
        "race_ethnicity": "decline",
        "hispanic_latino": "decline",
        "veteran_status": "decline",
        "disability_status": "decline",
        "pronouns": "decline",
    },
}


class ProfileError(Exception):
    pass


@dataclass
class Applicant:
    data: dict[str, Any]
    path: Path
    resume_path: Optional[Path] = None
    resume_text: str = ""

    # ------------------------------------------------------------- loading

    @classmethod
    def load(cls, profile_path: str | Path, resume_path: str | Path | None = None,
             strict: bool = True) -> "Applicant":
        path = Path(profile_path)
        if not path.exists():
            raise ProfileError(
                f"applicant profile not found at {path}. Copy "
                f"profile/applicant.example.json to {path.name} and fill it in."
            )
        try:
            data = json.loads(path.read_text())
        except ValueError as exc:
            raise ProfileError(f"applicant profile is not valid JSON: {exc}") from exc

        applicant = cls(data=data, path=path)
        if resume_path:
            applicant.attach_resume(resume_path, strict=strict)
        if strict:
            missing = applicant.missing_required()
            if missing:
                raise ProfileError(
                    f"applicant profile is missing required fields: {', '.join(missing)}"
                )
        return applicant

    def missing_required(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS if not str(self.data.get(f) or "").strip()]

    def attach_resume(self, resume_path: str | Path, strict: bool = True) -> None:
        path = Path(resume_path)
        if not path.exists():
            if strict:
                raise ProfileError(f"resume not found at {path}")
            log.warning("resume file missing", extra={"path": str(path)})
            return
        self.resume_path = path
        self.resume_text = extract_resume_text(path)
        if not self.resume_text.strip():
            log.warning("resume text could not be extracted; "
                        "Qwen will rely on applicant.json alone",
                        extra={"path": str(path)})

    # -------------------------------------------------------------- access

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted/indexed lookup: 'employment_history[0].title'."""
        cur: Any = self.data
        for part in re.split(r"\.(?![^\[]*\])", path):
            m = re.match(r"^([^\[]+)((?:\[\d+\])*)$", part)
            if not m:
                return default
            key, idx = m.group(1), m.group(2)
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                return default
            for i in re.findall(r"\[(\d+)\]", idx):
                if isinstance(cur, list) and int(i) < len(cur):
                    cur = cur[int(i)]
                else:
                    return default
            if cur is None:
                return default
        return cur

    def has(self, path: str) -> bool:
        value = self.get(path)
        return value not in (None, "", [], {})

    @property
    def full_name(self) -> str:
        return str(self.data.get("legal_name") or "").strip()

    @property
    def first_name(self) -> str:
        explicit = str(self.data.get("first_name") or "").strip()
        if explicit:
            return explicit
        parts = self.full_name.split()
        return parts[0] if parts else ""

    @property
    def last_name(self) -> str:
        explicit = str(self.data.get("last_name") or "").strip()
        if explicit:
            return explicit
        parts = self.full_name.split()
        return parts[-1] if len(parts) > 1 else ""

    @property
    def demographic_policy(self) -> dict[str, Any]:
        policy = self.data.get("demographic_response_policy")
        return policy if isinstance(policy, dict) else {}

    def declines_demographics(self, semantic_key: str) -> bool:
        value = str(self.demographic_policy.get(semantic_key, "decline")).lower()
        return value in ("decline", "prefer_not_to_say", "prefer not to say", "no", "skip")

    def demographic_value(self, semantic_key: str) -> Optional[str]:
        """A self-disclosed demographic value, only if explicitly provided."""
        value = self.demographic_policy.get(semantic_key)
        if isinstance(value, str) and value.lower() not in (
            "decline", "prefer_not_to_say", "prefer not to say", "skip", "no", ""
        ):
            return value
        return None

    # ------------------------------------------------------------- exports

    def redacted_summary(self, max_chars: int = 2500) -> str:
        """Compact profile view for scoring prompts (no contact details: saves tokens
        and keeps PII out of scoring caches)."""
        d = self.data
        lines = [
            f"Location: {d.get('city','')}, {d.get('state','')} {d.get('country','')}".strip(),
            f"Education: {d.get('degree','')} {d.get('major','')} at {d.get('university','')}"
            f" (grad {d.get('graduation_date','')})".strip(),
            f"GPA: {d.get('gpa','')}" if d.get("gpa") else "",
            f"Work authorization: {d.get('work_authorization','')}",
            f"Sponsorship requirement: {d.get('sponsorship_requirement','')}",
            f"Remote preference: {d.get('remote_preferences','')}",
            f"Relocation: {d.get('relocation_preferences','')}",
            f"Salary preference: {d.get('salary_preferences','')}",
            f"Skills: {', '.join(str(s) for s in (d.get('skills') or [])[:40])}",
        ]
        for job in (d.get("employment_history") or [])[:6]:
            if not isinstance(job, dict):
                continue
            lines.append(
                f"- {job.get('title','')} @ {job.get('company','')} "
                f"({job.get('start_date','')}–{job.get('end_date','')}): "
                f"{str(job.get('summary') or job.get('description') or '')[:300]}"
            )
        return "\n".join(l for l in lines if l.strip())[:max_chars]

    def answer_json(self, max_chars: int = 6000) -> str:
        """Full profile for form answering; contact details included by necessity."""
        return json.dumps(self.data, ensure_ascii=False)[:max_chars]

    def fact_corpus(self) -> str:
        """Lowercase blob of every profile value + resume text, used to test whether a
        proposed answer is traceable to a source of truth."""
        chunks: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
            elif node is not None:
                chunks.append(str(node))

        walk(self.data)
        chunks.append(self.resume_text)
        return " \n ".join(chunks).lower()


# --------------------------------------------------------------- resume text

def extract_resume_text(path: Path) -> str:
    """Extract text from a resume. Falls back through pdftotext, pypdf, plain read."""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return path.read_text(errors="ignore")
    if suffix == ".pdf":
        try:
            out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                 capture_output=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.decode("utf-8", errors="ignore")
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        try:
            from pypdf import PdfReader  # optional dependency
            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            log.warning("pdf text extraction unavailable",
                        extra={"error": str(exc)[:200],
                               "hint": "install poppler-utils or pypdf"})
            return ""
    if suffix == ".docx":
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
            root = ET.fromstring(xml)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            return " ".join(t.text or "" for t in root.iter(f"{ns}t"))
        except Exception as exc:
            log.warning("docx extraction failed", extra={"error": str(exc)[:200]})
            return ""
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def write_example_profile(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(PROFILE_TEMPLATE, indent=2))
    return p
