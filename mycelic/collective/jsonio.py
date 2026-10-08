"""Strict JSON in, canonical JSON out.

Every file the collective layer reads (routing files, caches, run files, ledgers) and every model reply goes
through :func:`strict_load`, which refuses what ``json.loads`` silently accepts: a byte-order mark, invalid UTF-8,
``NaN``/``Infinity`` (and ``1e999``, which ``json.loads`` turns into ``inf``), duplicate keys (``json.loads`` keeps
the last one, so two readers of the same file could disagree) and nesting deep enough to raise ``RecursionError``.

Errors are :class:`StrictJsonError` with a fixed ``reason`` and a ``path``. The path is a JSONPath for duplicate
keys (``$.endpoints.local``), ``line L column C`` for syntax errors and ``$`` otherwise; no other document text is
ever put in an error. Key names do appear in a duplicate-key path, so code that parses untrusted text (model
replies, see ``inference/jsonparse.py``) maps the error to its reason only.

:func:`canonical_dumps` is the one serialisation used for hashing, ledgers and run files: sorted keys, no spaces,
literal non-ASCII, and no ``NaN``. It is byte-stable across key orders.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

REASONS = ("bom", "encoding", "syntax", "duplicate_key", "non_finite", "too_deep", "not_canonicalisable")

_SIMPLE_KEY = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)


class StrictJsonError(ValueError):
    """``str`` is ``"<reason> at <path>"``; see the module docstring for what ``path`` may contain."""

    def __init__(self, reason: str, path: str = "$") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown StrictJsonError reason {reason!r}") from None
        super().__init__(f"{reason} at {path}")
        self.reason = reason
        self.path = path


class _NonFinite(Exception):
    pass


class _DupDict(dict):
    """A JSON object that had a repeated key; keeps every (key, value) pair so the post-walk can find the first."""

    def __init__(self, pairs: list[tuple[str, Any]]) -> None:
        super().__init__(pairs)
        self.pairs = pairs


def _reject_constant(name: str) -> Any:
    raise _NonFinite() from None


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _NonFinite() from None
    return value


def _child_path(path: str, key: str) -> str:
    if _SIMPLE_KEY.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=True)}]"


def _first_duplicate(root: Any) -> str | None:
    """JSONPath of the first repeated key in document order (iterative, so depth cannot recurse)."""
    stack: list[tuple[bool, str, Any]] = [(False, "$", root)]
    while stack:
        is_dup, path, node = stack.pop()
        if is_dup:
            return path
        if isinstance(node, dict):
            pairs = node.pairs if isinstance(node, _DupDict) else list(node.items())
            children: list[tuple[bool, str, Any]] = []
            seen: set[str] = set()
            for key, value in pairs:
                child = _child_path(path, key)
                if key in seen:
                    children.append((True, child, None))
                    break
                seen.add(key)
                children.append((False, child, value))
            stack.extend(reversed(children))
        elif isinstance(node, list):
            stack.extend(reversed([(False, f"{path}[{i}]", v) for i, v in enumerate(node)]))
    return None


def strict_load(data: bytes | bytearray | str) -> Any:
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
        if raw.startswith(b"\xef\xbb\xbf"):
            raise StrictJsonError("bom") from None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            position = exc.start
            raise StrictJsonError("encoding", f"byte {position}") from None
    elif isinstance(data, str):
        if data.startswith("﻿"):
            raise StrictJsonError("bom") from None
        text = data
    else:
        raise TypeError("strict_load takes bytes or str") from None

    seen_duplicate = False

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal seen_duplicate
        obj = dict(pairs)
        if len(obj) != len(pairs):
            seen_duplicate = True
            return _DupDict(pairs)
        return obj

    failure: StrictJsonError | None = None
    try:
        value = json.loads(text, object_pairs_hook=hook, parse_constant=_reject_constant, parse_float=_parse_float)
    except _NonFinite:
        failure = StrictJsonError("non_finite")
    except RecursionError:
        failure = StrictJsonError("too_deep")
    except json.JSONDecodeError as exc:
        failure = StrictJsonError("syntax", f"line {exc.lineno} column {exc.colno}")
    except ValueError:  # e.g. an integer longer than the interpreter's digit limit
        failure = StrictJsonError("syntax")
    if failure is not None:
        raise failure from None
    if seen_duplicate:
        raise StrictJsonError("duplicate_key", _first_duplicate(value) or "$") from None
    return value


def canonical_dumps(obj: Any) -> str:
    """Sorted keys, ``(',', ':')`` separators, literal non-ASCII, no NaN; the result always encodes as UTF-8."""
    failed = False
    try:
        text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        text.encode("utf-8")
    except (ValueError, TypeError, RecursionError):  # NaN, circular, unsortable or non-JSON types, lone surrogates
        failed = True
    if failed:
        raise StrictJsonError("not_canonicalisable") from None
    return text


def canonical_bytes(obj: Any) -> bytes:
    return canonical_dumps(obj).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def short_digest(hex_digest: str, n: int = 12) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("n must be a positive int") from None
    if not isinstance(hex_digest, str) or not re.fullmatch(f"[0-9a-f]{{{n},}}", hex_digest, re.ASCII):
        raise ValueError(f"not a lowercase hex digest of at least {n} characters") from None
    return hex_digest[:n]


def load_json_file(path: str | Path) -> Any:
    return strict_load(Path(path).read_bytes())
