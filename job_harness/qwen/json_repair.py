"""Recover JSON objects from imperfect LLM output.

Deterministic repairs only; a second model round-trip is the caller's last resort.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_PY_LITERALS = [("True", "true"), ("False", "false"), ("None", "null")]
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _balanced_slice(text: str) -> Optional[str]:
    """Return the first complete top-level {...} block, respecting strings/escapes."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
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
                return text[start:i + 1]
    # Unterminated: close what is open so the rest can still be parsed.
    tail = text[start:]
    if in_str:
        tail += '"'
    return tail + "}" * depth if depth > 0 else None


def _strip_comments(text: str) -> str:
    out, in_str, escape, i = [], False, False, 0
    while i < len(text):
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def repair_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort parse of a JSON object out of raw model output."""
    if not text:
        return None
    candidates: list[str] = []

    stripped = text.strip()
    candidates.append(stripped)

    fence = _FENCE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())

    block = _balanced_slice(text)
    if block:
        candidates.append(block)

    for candidate in list(candidates):
        cleaned = candidate.translate(_SMART_QUOTES)
        cleaned = _strip_comments(cleaned)
        cleaned = _TRAILING_COMMA.sub(r"\1", cleaned)
        for py, js in _PY_LITERALS:
            cleaned = re.sub(rf"(?<![\"\w]){py}(?![\"\w])", js, cleaned)
        candidates.append(cleaned)
        inner = _balanced_slice(cleaned)
        if inner:
            candidates.append(inner)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
    return None
