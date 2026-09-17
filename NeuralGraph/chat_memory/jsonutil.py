"""Robust JSON extraction from small-model completions.

Qwen-7B-class models mostly return valid JSON when asked, but they also wrap it in ```json fences,
prefix it with "Here is the JSON:", emit trailing commas, or truncate at max_tokens. Every parser
in the memory pipeline goes through :func:`parse_json_object` / :func:`parse_json_array` so those
failure modes are handled in one place and never raise.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _candidates(text: str) -> list[str]:
    """Yield substrings likely to hold the JSON payload, most specific first."""
    text = _THINK_RE.sub("", text or "").strip()
    out: list[str] = []
    for m in _FENCE_RE.finditer(text):
        out.append(m.group(1).strip())
    out.append(text)
    return out


def _balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    """Return the first balanced ``open_ch``...``close_ch`` span, honouring JSON strings."""
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
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
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _loads_lenient(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    fixed = _TRAILING_COMMA_RE.sub(r"\1", s)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def _repair_truncated(s: str) -> str:
    """Close open strings/brackets of a completion cut off by max_tokens (best effort)."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
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
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    out = s
    if in_str:
        out += '"'
    # drop a dangling ``"key":`` or trailing comma before closing
    out = re.sub(r',\s*$', "", out)
    out = re.sub(r'"[^"]*"\s*:\s*$', "", out)
    out = re.sub(r',\s*$', "", out)
    while stack:
        out += stack.pop()
    return out


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort: return the first JSON object found in ``text`` or ``None``."""
    for cand in _candidates(text):
        span = _balanced(cand, "{", "}")
        for attempt in (span, cand):
            if not attempt:
                continue
            obj = _loads_lenient(attempt)
            if isinstance(obj, dict):
                return obj
        # truncated completion: try to close it
        start = cand.find("{")
        if start >= 0:
            obj = _loads_lenient(_repair_truncated(cand[start:]))
            if isinstance(obj, dict):
                return obj
    return None


def parse_json_array(text: str) -> list[Any] | None:
    """Best-effort: return the first JSON array found in ``text`` or ``None``."""
    for cand in _candidates(text):
        span = _balanced(cand, "[", "]")
        for attempt in (span, cand):
            if not attempt:
                continue
            obj = _loads_lenient(attempt)
            if isinstance(obj, list):
                return obj
        start = cand.find("[")
        if start >= 0:
            obj = _loads_lenient(_repair_truncated(cand[start:]))
            if isinstance(obj, list):
                return obj
    return None


def as_str(v: Any, limit: int | None = None) -> str:
    """Coerce a JSON value to a clean string (``None`` -> '')."""
    if v is None:
        return ""
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    s = " ".join(s.split())
    return s[:limit] if limit else s


def as_float(v: Any, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return max(lo, min(hi, f))


def as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, (str, int, float, dict)):
        return [v]
    return list(v)
