"""The run-file contract (G8): which files a run directory holds, how digests and the usage and follow-up ledgers are
projected into them, where every on-screen number points, and how they are written and read.

A demo run directory holds :data:`RUN_FILES`; the first five are the **primary** files (:data:`PRIMARY_FILES`), and
``screen.json`` is derived from them: every number it shows names its source as ``<file>#<RFC 6901 pointer>``
(:func:`parse_src`, :func:`resolve`), and the source must be a primary file. G0's ``run_files`` stage writes four of
them (no ``leakage.json`` and no ``screen.json``) with the same helpers.

Rules:

* **Digests.** Every sha256 in a run file is written as its first :data:`DIGEST_CHARS` hex characters
  (:func:`digest`, :func:`shorten`): a 64-hex string looks like a credential to a secret scan, and the full digests
  stay in the stores of the work directory. A string that holds a 64-hex run inside a longer text is refused, never
  kept silently.
* **Usage ledger** (:func:`project_ledger`): exactly :data:`LEDGER_ROW_KEYS` per row (never ``ts``, ``run_id``,
  ``ref``, ``host`` or ``model_served``), at most ``per_group`` rows per ``(site, task)`` in input order, and a summary
  that counts every row. ``site`` is the id of a ``site:<id>`` boundary, or ``hq`` for ``central``.
* **Follow-up ledger** (:func:`project_entries`): one line per entry with shortened digests, then one
  ``ledger_head`` line with the head hash and the entry count.
* **Portability** (:func:`portability_problems`): rule names, never values. A run file holds no absolute path, none of
  the caller's forbidden strings (the work and run directories, the repository root, the home directory, the host
  name, the user name), no 64-hex token, no credential-named key and no agent-key prefix.
* **Writing** (:func:`write_run_files`): every file is serialised and checked before anything is written; any problem
  writes nothing. Each file is written atomically (a temporary file and ``os.replace``), ``scorecard.json`` last: a
  directory without ``scorecard.json`` is not a run.

:class:`RunFileError` names a file, a pointer or a step and a fixed problem; it never holds a value. Pure apart from
:func:`write_run_files` and :func:`read_run_files`; no clock and no randomness.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load

RUN_FILES = ("scorecard.json", "trace.json", "ledger.jsonl", "leakage.json", "approvals.jsonl", "screen.json")
PRIMARY_FILES = RUN_FILES[:5]
SCORECARD = RUN_FILES[0]
DIGEST_CHARS = 32
LEDGER_ROW_KEYS = ("site", "task", "attempt", "endpoint", "provider", "model_requested", "boundary_mode",
                   "data_label", "ok", "error_kind", "tokens_in", "tokens_out", "latency_ms", "cost_basis",
                   "fake_marker")
PORTABILITY_RULES = ("absolute_path", "forbidden_string", "hex64", "credential_key", "key_prefix")
CREDENTIAL_KEYS = ("api_key", "admin_token", "password", "signing_key", "Authorization")
AGENT_KEY_PREFIX = "mk_"
MIN_FORBIDDEN = 3
SRC_PROBLEMS = ("src_not_primary", "src_unresolved", "src_not_scalar")
CENTRAL_SITE = "hq"
HEAD_KIND = "ledger_head"

_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_HEX64_TOKEN = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.ASCII)
_ABSOLUTE = re.compile(r"(?<![\w.-])/(home|Users|root|tmp|var|private|opt|mnt|srv|etc)/|(?<![A-Za-z])[A-Za-z]:\\")
_CREDENTIAL = re.compile("|".join(rf'"{re.escape(k)}"\s*:' for k in CREDENTIAL_KEYS))
_INDEX = re.compile(r"0|[1-9][0-9]*", re.ASCII)
_ESCAPE = re.compile(r"~[^01]|~$")


class RunFileError(ValueError):
    """``str`` is ``run files: <where>: <problem>``; ``where`` is a file name, a pointer or a step, never a value."""

    def __init__(self, where: str, problem: str) -> None:
        super().__init__(f"run files: {where}: {problem}")
        self.where = where
        self.problem = problem


# --------------------------------------------------------------------------------------------------- digests

def digest(h: Any) -> str:
    """The first :data:`DIGEST_CHARS` characters of a 64-lowercase-hex sha256."""
    if not isinstance(h, str) or _HEX64.fullmatch(h) is None:
        raise RunFileError("digest", "not a sha256 hex digest") from None
    return h[:DIGEST_CHARS]


def _short(text: str) -> str:
    if _HEX64.fullmatch(text) is not None:
        return text[:DIGEST_CHARS]
    if _HEX64.search(text) is not None:
        raise RunFileError("shorten", "a string holds a sha256 digest inside a longer text") from None
    return text


def shorten(obj: Any) -> Any:
    """A deep copy in which every 64-hex string is its 32-hex prefix; any other string (or key) that contains a
    64-hex run is refused."""
    if isinstance(obj, Mapping):
        return {_short(k) if isinstance(k, str) else k: shorten(obj[k]) for k in obj}
    if isinstance(obj, (list, tuple)):
        return [shorten(v) for v in obj]
    if isinstance(obj, str):
        return _short(obj)
    return obj


# --------------------------------------------------------------------------------------------------- ledgers

def _site_of(boundary: Any) -> str:
    if isinstance(boundary, str) and boundary.startswith("site:"):
        return boundary[len("site:"):]
    return CENTRAL_SITE


def project_ledger(rows: Sequence[Mapping[str, Any]], *,
                   per_group: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The first ``per_group`` rows of each ``(site, task)`` in input order, projected to :data:`LEDGER_ROW_KEYS`, and
    a summary over every row: ``{rows_total, rows_written, per_group, capped, by_task}`` with ``by_task`` sorted by
    task, each ``{task, calls, ok, errors, tokens_in, tokens_out, tokens_missing, fake}`` (``calls`` counts logical
    attempts; ``tokens_missing`` counts rows without one of the two token counts; ``fake`` is true when any row of the
    task carried the fake marker)."""
    if isinstance(per_group, bool) or not isinstance(per_group, int) or per_group < 1:
        raise RunFileError("ledger", "per_group must be an int >= 1") from None
    out: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    by_task: dict[str, dict[str, Any]] = {}
    for row in rows:
        site, task = _site_of(row["boundary"]), row["task"]
        group = (site, task)
        seen[group] = seen.get(group, 0) + 1
        if seen[group] <= per_group:
            out.append({"site": site, **{k: row[k] for k in LEDGER_ROW_KEYS[1:]}})
        acc = by_task.setdefault(task, {"task": task, "calls": 0, "ok": 0, "errors": 0, "tokens_in": 0,
                                        "tokens_out": 0, "tokens_missing": 0, "fake": False})
        acc["calls"] += 1
        acc["ok" if row["ok"] else "errors"] += 1
        tin, tout = row["tokens_in"], row["tokens_out"]
        acc["tokens_in"] += tin if isinstance(tin, int) and not isinstance(tin, bool) else 0
        acc["tokens_out"] += tout if isinstance(tout, int) and not isinstance(tout, bool) else 0
        acc["tokens_missing"] += 1 if tin is None or tout is None else 0
        acc["fake"] = acc["fake"] or bool(row["fake_marker"])
    summary = {"rows_total": len(rows), "rows_written": len(out), "per_group": per_group,
               "capped": len(out) < len(rows), "by_task": [by_task[t] for t in sorted(by_task)]}
    return out, summary


def project_entries(entries: Sequence[Any], *, label: str) -> list[dict[str, Any]]:
    """One line per follow-up ledger entry ``{seq, at, kind, key, actor, payload, prev_hash, hash}`` (payload and
    hashes shortened), then exactly one ``{kind: ledger_head, head_hash, entries, label}`` line; ``head_hash`` is the
    last entry's hash, or null for an empty ledger."""
    lines = [{"seq": e.seq, "at": e.at, "kind": e.kind, "key": e.key, "actor": e.actor,
              "payload": shorten(e.payload), "prev_hash": digest(e.prev_hash), "hash": digest(e.hash)}
             for e in entries]
    head = digest(entries[-1].hash) if entries else None
    lines.append({"kind": HEAD_KIND, "head_hash": head, "entries": len(entries), "label": label})
    return lines


# --------------------------------------------------------------------------------------------------- portability

def _token_pattern(text: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(text) + r"(?![A-Za-z0-9_])")


def portability_problems(data: bytes, *, forbidden: Iterable[str]) -> list[str]:
    """The names of the :data:`PORTABILITY_RULES` that ``data`` breaks, in rule order; never a value. Forbidden strings
    shorter than :data:`MIN_FORBIDDEN` characters are ignored; the others match whole tokens, case-sensitively."""
    text = bytes(data).decode("utf-8", "replace")
    found: list[str] = []
    if _ABSOLUTE.search(text) is not None:
        found.append("absolute_path")
    if any(_token_pattern(s).search(text) is not None
           for s in sorted({str(s) for s in forbidden}) if len(s) >= MIN_FORBIDDEN):
        found.append("forbidden_string")
    if _HEX64_TOKEN.search(text) is not None:
        found.append("hex64")
    if _CREDENTIAL.search(text) is not None:
        found.append("credential_key")
    if AGENT_KEY_PREFIX in text:
        found.append("key_prefix")
    return found


# --------------------------------------------------------------------------------------------------- hashes and sources

def content_hash(doc: Mapping[str, Any], excludes: Iterable[str]) -> str:
    """32 hex of the sha256 of the canonical JSON of ``doc`` without the excluded top-level keys (``/<key>``)."""
    names = set()
    for pointer in excludes:
        if not isinstance(pointer, str) or not pointer.startswith("/") or "/" in pointer[1:]:
            raise RunFileError("content_hash", "an exclude must be a top-level pointer /<key>") from None
        names.add(pointer[1:])
    return sha256_hex(canonical_bytes({k: doc[k] for k in doc if k not in names}))[:DIGEST_CHARS]


def parse_src(src: Any) -> tuple[str, list[str]]:
    """``'<file>#<RFC 6901 pointer>'`` as ``(file, tokens)``; for a ``.jsonl`` file the first token is the 0-based line
    index. The file must be primary (``src_not_primary``); a malformed source is ``src_unresolved``."""
    if not isinstance(src, str) or "#" not in src:
        raise RunFileError("src", "src_unresolved") from None
    file, pointer = src.split("#", 1)
    if file not in PRIMARY_FILES:
        raise RunFileError(src, "src_not_primary") from None
    if pointer == "":
        return file, []
    if not pointer.startswith("/") or _ESCAPE.search(pointer) is not None:
        raise RunFileError(src, "src_unresolved") from None
    tokens = [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]
    if file.endswith(".jsonl") and _INDEX.fullmatch(tokens[0]) is None:
        raise RunFileError(src, "src_unresolved") from None
    return file, tokens


def resolve(docs: Mapping[str, Any], src: str) -> Any:
    """The scalar (str, int, float, bool or None) that ``src`` points at in ``docs`` (``{file name: document}``; a
    ``.jsonl`` document is its list of lines)."""
    file, tokens = parse_src(src)
    if file not in docs:
        raise RunFileError(src, "src_unresolved") from None
    node = docs[file]
    for token in tokens:
        if isinstance(node, Mapping) and token in node:
            node = node[token]
        elif isinstance(node, list) and _INDEX.fullmatch(token) is not None and int(token) < len(node):
            node = node[int(token)]
        else:
            raise RunFileError(src, "src_unresolved") from None
    if node is None or isinstance(node, (str, bool, int, float)):
        return node
    raise RunFileError(src, "src_not_scalar") from None


# --------------------------------------------------------------------------------------------------- files

def serialise(name: str, doc: Any) -> bytes:
    """Canonical JSON plus a newline for ``.json``; one canonical line per item for ``.jsonl``."""
    if name.endswith(".jsonl"):
        if not isinstance(doc, list):
            raise RunFileError(name, "a jsonl document is a list of lines") from None
        return "".join(canonical_dumps(line) + "\n" for line in doc).encode("utf-8")
    return (canonical_dumps(doc) + "\n").encode("utf-8")


def _atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_run_files(directory: str | Path, docs: Mapping[str, Any], *, forbidden: Iterable[str]) -> list[str]:
    """Serialise and check every document, then write each atomically, ``scorecard.json`` last. Any portability
    problem (or a name outside :data:`RUN_FILES`) raises :class:`RunFileError` and writes nothing. Returns the names in
    the order written."""
    if not docs:
        raise RunFileError("run", "no run file to write") from None
    forbidden = tuple(forbidden)
    data: dict[str, bytes] = {}
    for name in sorted(docs):
        if name not in RUN_FILES:
            raise RunFileError(name, "not a run file name") from None
        try:
            data[name] = serialise(name, docs[name])
        except StrictJsonError:
            raise RunFileError(name, "not canonical JSON") from None
        problems = portability_problems(data[name], forbidden=forbidden)
        if problems:
            raise RunFileError(name, "not portable (" + ", ".join(problems) + ")") from None
    order = [n for n in RUN_FILES if n in data and n != SCORECARD] + ([SCORECARD] if SCORECARD in data else [])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in order:
        _atomic(directory / name, data[name])
    return order


def read_run_files(directory: str | Path, names: Iterable[str]) -> dict[str, Any]:
    """``{name: document}`` read strictly; a missing, unreadable or invalid file raises :class:`RunFileError` naming
    it. A ``.jsonl`` document is its list of lines (one strict JSON value per line, no blank line)."""
    directory = Path(directory)
    out: dict[str, Any] = {}
    for name in names:
        raw = None
        try:
            raw = (directory / name).read_bytes()
        except FileNotFoundError:
            raise RunFileError(name, "missing") from None
        except OSError:
            raise RunFileError(name, "unreadable") from None
        try:
            if name.endswith(".jsonl"):
                parts = raw.split(b"\n")
                if parts and parts[-1] == b"":
                    parts.pop()
                if any(p == b"" for p in parts):
                    raise StrictJsonError("syntax")
                out[name] = [strict_load(p) for p in parts]
            else:
                out[name] = strict_load(raw)
        except StrictJsonError:
            raise RunFileError(name, "invalid JSON") from None
    return out
