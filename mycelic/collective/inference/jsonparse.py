"""Pull the one JSON object out of a model reply.

Our own module rather than NeuralGraph's ``chat_memory.jsonutil``: importing that loads numpy and the NeuralGraph
LLM client, and it uses ``json.loads`` directly, which accepts duplicate keys and ``NaN`` and can raise
``RecursionError`` on deep nesting.

Steps, in order:

1. Reasoning blocks. Every ``<think>...</think>`` is removed (ASCII case-insensitive). An unterminated ``<think>``
   drops itself and everything after it. A ``</think>`` left without an opening tag drops everything up to and
   including the last one (some servers strip the opening tag).
2. Code fences: any line that, stripped, is ```` ``` ```` plus an optional language tag is dropped.
3. Candidates: each ``{`` found by plain search, left to right, starts one (at most 16). A string-aware scanner
   follows ``"..."`` with backslash escapes and tracks ``{``/``[`` depth, so braces inside strings never close a
   candidate. Depth above 64 ends the search with ``too_deep`` without parsing anything, so nothing can recurse
   deeply, and no object nested inside the too-deep one can be taken for the answer. A balanced candidate goes to
   ``jsonio.strict_load`` (no duplicate keys, no NaN, no ``1e999``); the first one that parses wins, so of two
   objects the first is taken. When a balanced candidate fails to parse, the search resumes after its end: an
   object nested inside an invalid one is part of it, not an answer of its own.

Failures are :class:`ReplyParseError` whose ``str`` is only its reason (``empty``, ``no_object``, ``too_deep`` or
``invalid``); the reply text never enters an error.
"""
from __future__ import annotations

import re
from typing import Any

from ..jsonio import StrictJsonError, strict_load

MAX_DEPTH = 64
MAX_CANDIDATES = 16

_OPEN = re.compile(r"<think>", re.IGNORECASE | re.ASCII)
_CLOSE = re.compile(r"</think>", re.IGNORECASE | re.ASCII)
_FENCE = re.compile(r"```[A-Za-z0-9_-]*", re.ASCII)
_OUTSIDE = re.compile(r'[{}\[\]"]')
_INSIDE = re.compile(r'["\\]')


class ReplyParseError(ValueError):
    REASONS = ("empty", "no_object", "too_deep", "invalid")

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def strip_reasoning(text: str) -> str:
    out = []
    pos = 0
    while True:
        opening = _OPEN.search(text, pos)
        if opening is None:
            out.append(text[pos:])
            break
        closing = _CLOSE.search(text, opening.end())
        out.append(text[pos:opening.start()])
        if closing is None:          # unterminated: drop the tag and everything after it
            break
        pos = closing.end()
    cleaned = "".join(out)
    last_close = None
    for last_close in _CLOSE.finditer(cleaned):
        pass
    if last_close is not None:
        cleaned = cleaned[last_close.end():]
    return cleaned


def strip_fences(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if _FENCE.fullmatch(line.strip()) is None)


def _scan(text: str, start: int) -> tuple[str, int]:
    """From the ``{`` at ``start``: ('ok', end), ('too_deep', 0) or ('unbalanced', 0)."""
    depth = 0
    pos = start
    while True:
        m = _OUTSIDE.search(text, pos)
        if m is None:
            return "unbalanced", 0
        ch = m.group()
        pos = m.end()
        if ch == '"':
            while True:
                s = _INSIDE.search(text, pos)
                if s is None:
                    return "unbalanced", 0
                if s.group() == "\\":
                    pos = s.end() + 1
                    continue
                pos = s.end()
                break
        elif ch in "{[":
            depth += 1
            if depth > MAX_DEPTH:
                return "too_deep", 0
        else:
            depth -= 1
            if depth == 0:
                return "ok", pos


def extract_object(text: str | None) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ReplyParseError("empty") from None
    cleaned = strip_fences(strip_reasoning(text))
    if not cleaned.strip():
        raise ReplyParseError("empty") from None
    reason = "no_object"
    pos = 0
    for _ in range(MAX_CANDIDATES):
        start = cleaned.find("{", pos)
        if start < 0:
            break
        pos = start + 1
        status, end = _scan(cleaned, start)
        if status == "too_deep":     # its end is unknown, so whatever follows may be nested inside it: stop
            raise ReplyParseError("too_deep") from None
        if status != "ok":
            continue
        try:
            value = strict_load(cleaned[start:end])
        except StrictJsonError:
            if reason == "no_object":
                reason = "invalid"
            pos = end                # objects nested in an invalid one are part of it, not candidates of their own
            continue
        if isinstance(value, dict):
            return value
    raise ReplyParseError(reason) from None
