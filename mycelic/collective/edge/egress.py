"""The Boundary: the only path out of a site.

Exactly two artifact types may leave (:data:`ARTIFACT_TYPES`), in one direction, ``out``:

* ``cells_bundle``: weekly count cells ``(entity_type, entity_id, predicate, iso_week, channel)`` with ``n``,
  ``n_roots`` and ``n_reporters``, each an int >= k or the literal ``'<k'``; ``res_conf_min`` only when ``n`` is an
  int. Weeks lie in ``(after, closed_through]``, cells are strictly sorted, ids have their type's canonical form;
* ``usage_summary``: per (task, endpoint) counts over the ledger rows of closed weeks, suppressed the same way, with
  token sums and latency percentiles only when ``calls`` is an int.

Both are structurally closed (an unknown key anywhere is refused), never revised (``after`` must equal the last
``closed_through`` sent for that type; an identical re-send is a no-op), and validated here by a closed spec
language of our own (``schemacheck`` cannot say "an int >= k or the string '<k'", nor optional keys, and is tied to
model replies). Problems are checked in a fixed order and the first is raised as :class:`EgressError`, which holds
the artifact type, a path built only from schema property names and integer indices, and a keyword. It never holds
a value: an unknown key is reported at its parent object as ``additionalProperties``, unnamed. This module logs
nothing.

:meth:`Boundary.send` appends one canonical JSON line (``artifact_type, body, bytes, direction, sha256, site, ts``)
to the HQ receive log first and then to the site's egress log; after a crash between the two writes a re-send makes
them agree (HQ may then hold a duplicate with the same sha256, which HQ drops). A refused send writes nothing.

The Boundary and HQ share one validator. :func:`check_artifact` runs the structural check and then the cross-field
check on an already-parsed body and returns the first problem ``(path, keyword)`` or None; :func:`log_row_problem`
is the check of one receive- or egress-log row. HQ (``detect/store.py``) calls both on what it reads, so a bundle HQ
accepts is exactly one this Boundary would have let out.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from ..inference.errors import KINDS
from ..jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from ..packs.connector import ISO_DATE_RE, SITE_ID_RE, valid_date
from .weeks import TS_RE, local_date, valid_week

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack

ARTIFACT_TYPES = ("cells_bundle", "usage_summary")
DIRECTIONS = ("out",)
SUPPRESSED = "<k"
CHANNELS = ("codes", "text_only")
LOG_KEYS = ("artifact_type", "body", "bytes", "direction", "sha256", "site", "ts")
KEYWORDS = ("type", "required", "additionalProperties", "const", "enum", "pattern", "maxLength", "minimum",
            "maximum", "count", "week", "date", "id_format", "order", "range", "consistency", "sequence", "json",
            "direction", "artifact_type")
SCHEMA_VERSION = 1
MAX_COUNT = 10 ** 9
MAX_TOKENS = 10 ** 12
MAX_LATENCY_MS = 10 ** 9
ENTITY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,39}", re.ASCII)

BODY_KEYS = ("after", "as_of", "closed_through", "config_hash", "k", "pack", "schema_version", "site")
CELL_KEYS = ("channel", "entity_id", "entity_type", "iso_week", "n", "n_reporters", "n_roots", "predicate")
CELL_OPTIONAL = ("res_conf_min",)
COUNT_FIELDS = ("n", "n_roots", "n_reporters")
GROUP_KEYS = ("calls", "endpoint", "errors", "fake", "ok", "task", "tokens_in_missing", "tokens_out_missing")
GROUP_OPTIONAL = ("latency_ms_p50", "latency_ms_p95", "tokens_in", "tokens_out")


class EgressError(Exception):
    """An artifact the Boundary refused. ``args`` is empty; str and repr name only the type, path and keyword."""

    def __init__(self, *, artifact_type: str, path: str, keyword: str) -> None:
        if artifact_type not in ARTIFACT_TYPES + ("unknown",) or keyword not in KEYWORDS:
            raise ValueError("unknown EgressError artifact type or keyword") from None
        super().__init__()
        self.artifact_type = artifact_type
        self.path = path
        self.keyword = keyword

    def __str__(self) -> str:
        return f"egress refused: artifact_type={self.artifact_type} path={self.path} keyword={self.keyword}"

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(artifact_type={self.artifact_type!r}, path={self.path!r}, "
                f"keyword={self.keyword!r})")


# --------------------------------------------------------------------------------------------------- spec language

@dataclass(frozen=True)
class _Obj:
    required: Mapping[str, Any]
    optional: Mapping[str, Any]


@dataclass(frozen=True)
class _Arr:
    item: Any


@dataclass(frozen=True)
class _Const:
    value: Any


@dataclass(frozen=True)
class _Enum:
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _Str:
    regex: re.Pattern[str]
    max_len: int


@dataclass(frozen=True)
class _Int:
    lo: int
    hi: int


@dataclass(frozen=True)
class _Num:
    lo: float
    hi: float


@dataclass(frozen=True)
class _Bool:
    pass


@dataclass(frozen=True)
class _Count:
    """An int (not bool) in [k, MAX_COUNT], or exactly ``'<k'``; with ``zero``, also the int 0."""

    k: int
    zero: bool = False


@dataclass(frozen=True)
class _Week:
    nullable: bool


@dataclass(frozen=True)
class _Date:
    pass


def _obj(required: dict[str, Any], optional: dict[str, Any] | None = None) -> _Obj:
    return _Obj(required=MappingProxyType(dict(required)), optional=MappingProxyType(dict(optional or {})))


def _same(a: Any, b: Any) -> bool:
    """Type-strict equality, as schemacheck compares enums: True != 1, 1 == 1.0."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check(node: Any, value: Any, path: str) -> tuple[str, str] | None:
    """The first problem ``(path, keyword)``: object keys unknown first, then required sorted, then optional present
    sorted; arrays by index."""
    if isinstance(node, _Obj):
        if not isinstance(value, dict):
            return path, "type"
        if any(key not in node.required and key not in node.optional for key in value):
            return path, "additionalProperties"
        for key in sorted(node.required):
            if key not in value:
                return f"{path}.{key}", "required"
            found = _check(node.required[key], value[key], f"{path}.{key}")
            if found is not None:
                return found
        for key in sorted(k for k in node.optional if k in value):
            found = _check(node.optional[key], value[key], f"{path}.{key}")
            if found is not None:
                return found
        return None
    if isinstance(node, _Arr):
        if not isinstance(value, list):
            return path, "type"
        for i, item in enumerate(value):
            found = _check(node.item, item, f"{path}[{i}]")
            if found is not None:
                return found
        return None
    if isinstance(node, _Const):
        return None if _same(value, node.value) else (path, "const")
    if isinstance(node, _Enum):
        return None if any(_same(value, v) for v in node.values) else (path, "enum")
    if isinstance(node, _Str):
        if not isinstance(value, str):
            return path, "type"
        if len(value) > node.max_len:
            return path, "maxLength"
        return None if node.regex.fullmatch(value) is not None else (path, "pattern")
    if isinstance(node, (_Int, _Num)):
        number = _is_int(value) or (isinstance(node, _Num) and isinstance(value, float) and math.isfinite(value))
        if not number:
            return path, "type"
        if value < node.lo:
            return path, "minimum"
        return None if value <= node.hi else (path, "maximum")
    if isinstance(node, _Bool):
        return None if isinstance(value, bool) else (path, "type")
    if isinstance(node, _Count):
        if (isinstance(value, str) and value == SUPPRESSED) or (node.zero and _is_int(value) and value == 0):
            return None
        return None if _is_int(value) and node.k <= value <= MAX_COUNT else (path, "count")
    if isinstance(node, _Week):
        if value is None and node.nullable:
            return None
        return None if valid_week(value) else (path, "week")
    if isinstance(node, _Date):
        ok = isinstance(value, str) and ISO_DATE_RE.fullmatch(value) is not None and valid_date(value)
        return None if ok else (path, "date")
    raise TypeError("unknown spec node") from None


def _envelope(pack: "FrozenPack", site_id: str) -> dict[str, Any]:
    return {"schema_version": _Const(SCHEMA_VERSION), "pack": _Const(pack.id), "config_hash": _Const(pack.config_hash),
            "site": _Const(site_id), "as_of": _Date(), "after": _Week(nullable=True),
            "closed_through": _Week(nullable=False), "k": _Const(pack.egress.k)}


def _spec(pack: "FrozenPack", site_id: str, artifact_type: str, tasks: Sequence[str] = (),
          endpoints: Sequence[str] = ()) -> _Obj:
    k = pack.egress.k
    if artifact_type == "cells_bundle":
        conf = pack.extraction
        cell = _obj({"entity_type": _Enum(tuple(pack.egress.egress_entity_types)),
                     "entity_id": _Str(ENTITY_ID_RE, 40),
                     "predicate": _Enum(tuple(sorted(pack.predicates))),
                     "iso_week": _Week(nullable=False), "channel": _Enum(CHANNELS),
                     **{f: _Count(k) for f in COUNT_FIELDS}},
                    {"res_conf_min": _Enum((conf.confidence_exact, conf.confidence_alias, conf.confidence_variant))})
        return _obj({**_envelope(pack, site_id), "cells": _Arr(cell)})
    group = _obj({"task": _Enum(tuple(tasks)), "endpoint": _Enum(tuple(endpoints)), "calls": _Count(k),
                  "ok": _Count(k, zero=True), "tokens_in_missing": _Count(k, zero=True),
                  "tokens_out_missing": _Count(k, zero=True), "errors": _obj({}, {kind: _Count(k) for kind in KINDS}),
                  "fake": _Bool()},
                 {"tokens_in": _Int(0, MAX_TOKENS), "tokens_out": _Int(0, MAX_TOKENS),
                  "latency_ms_p50": _Num(0, MAX_LATENCY_MS), "latency_ms_p95": _Num(0, MAX_LATENCY_MS)})
    return _obj({**_envelope(pack, site_id), "groups": _Arr(group)})


def _positions(node: Any, path: str, out: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> None:
    if isinstance(node, _Obj):
        out[path] = (tuple(sorted(node.required)), tuple(sorted(node.optional)))
        for key in sorted({**node.required, **node.optional}):
            _positions({**node.required, **node.optional}[key], f"{path}.{key}", out)
    elif isinstance(node, _Arr):
        _positions(node.item, f"{path}[]", out)


def artifact_keys(pack: "FrozenPack", artifact_type: str) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Each object position of an artifact type (``$``, ``$.cells[]``; ``$``, ``$.groups[]``,
    ``$.groups[].errors``) mapped to its (required keys, optional keys)."""
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError("unknown artifact type") from None
    out: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    _positions(_spec(pack, "x", artifact_type), "$", out)
    return out


def schema_words() -> tuple[str, ...]:
    """Every key name and fixed enum string of the two artifact schemas and the log rows."""
    words = {*BODY_KEYS, "cells", "groups", *CELL_KEYS, *CELL_OPTIONAL, *GROUP_KEYS, *GROUP_OPTIONAL, *KINDS,
             *LOG_KEYS, *ARTIFACT_TYPES, *DIRECTIONS, *CHANNELS, SUPPRESSED}
    return tuple(sorted(words))


# --------------------------------------------------------------------------------------------------- cross-field

def _range(body: dict[str, Any], weeks: Sequence[tuple[str, str]]) -> tuple[str, str] | None:
    after, through = body["after"], body["closed_through"]
    if after is not None and not after < through:
        return "$.after", "range"
    for path, week in weeks:
        if not (week <= through and (after is None or week > after)):
            return path, "range"
    return None


def _cells_problem(pack: "FrozenPack", body: dict[str, Any]) -> tuple[str, str] | None:
    cells = body["cells"]
    found = _range(body, [(f"$.cells[{i}].iso_week", c["iso_week"]) for i, c in enumerate(cells)])
    if found is not None:
        return found
    for i, c in enumerate(cells):
        et = pack.entity_types[c["entity_type"]]
        ok = c["entity_id"] in et.ids if et.id_format is None else et.id_format.canonical.fullmatch(c["entity_id"])
        if not ok:
            return f"$.cells[{i}].entity_id", "id_format"
    for i, c in enumerate(cells):
        exact = _is_int(c["n"])
        if ("res_conf_min" in c) != exact:
            return f"$.cells[{i}].res_conf_min", "consistency"
        for f in ("n_roots", "n_reporters"):
            if not exact and c[f] != SUPPRESSED:
                return f"$.cells[{i}].{f}", "consistency"
            if exact and _is_int(c[f]) and c[f] > c["n"]:
                return f"$.cells[{i}].{f}", "consistency"
    keys = [(c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"], c["channel"]) for c in cells]
    for i in range(1, len(keys)):
        if not keys[i - 1] < keys[i]:
            return f"$.cells[{i}]", "order"
    return None


def _usage_problem(body: dict[str, Any]) -> tuple[str, str] | None:
    groups = body["groups"]
    for i, g in enumerate(groups):
        if g["calls"] == SUPPRESSED:
            for key in GROUP_OPTIONAL:
                if key in g:
                    return f"$.groups[{i}].{key}", "consistency"
    for i in range(1, len(groups)):
        if not (groups[i - 1]["task"], groups[i - 1]["endpoint"]) < (groups[i]["task"], groups[i]["endpoint"]):
            return f"$.groups[{i}]", "order"
    return _range(body, [])


def check_artifact(pack: "FrozenPack", site_id: str, artifact_type: str, body: Any, *, tasks: Sequence[str] = (),
                   endpoints: Sequence[str] = ()) -> tuple[str, str] | None:
    """The first problem ``(path, keyword)`` of an already-parsed ``body`` of ``artifact_type`` from ``site_id``:
    the closed structure first, then the cross-field checks. None when the artifact is valid (the ``after`` sequence
    is the caller's)."""
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError("unknown artifact type") from None
    found = _check(_spec(pack, site_id, artifact_type, tasks, endpoints), body, "$")
    if found is None:
        found = _cells_problem(pack, body) if artifact_type == "cells_bundle" else _usage_problem(body)
    return found


# --------------------------------------------------------------------------------------------------- logs

def log_row_problem(row: Any) -> str | None:
    """None for a well-formed log row, else a fixed problem text (never a value)."""
    if not isinstance(row, dict) or sorted(row) != list(LOG_KEYS):
        return "not a log row with exactly the log keys"
    if row["artifact_type"] not in ARTIFACT_TYPES or row["direction"] not in DIRECTIONS:
        return "unknown artifact type or direction"
    if not isinstance(row["site"], str) or SITE_ID_RE.fullmatch(row["site"]) is None:
        return "bad site"
    if not isinstance(row["ts"], str) or TS_RE.fullmatch(row["ts"]) is None or local_date(row["ts"]) is None:
        return "bad ts"
    if not isinstance(row["body"], dict) or not valid_week(row["body"].get("closed_through")):
        return "body without a closed week"
    data = canonical_bytes(row["body"])
    if row["sha256"] != sha256_hex(data) or not _is_int(row["bytes"]) or row["bytes"] != len(data):
        return "sha256 or bytes do not match the body"
    return None


def read_log(path: str | Path) -> list[dict[str, Any]]:
    """Every row of an egress or receive log; [] for an absent file. A bad line raises ValueError naming the line
    and a fixed problem, never a value."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_bytes().split(b"\n"), start=1):
        if not line:
            continue
        row = problem = None
        try:
            row = strict_load(line)
        except StrictJsonError:
            problem = "not strict JSON"
        if problem is None:
            problem = log_row_problem(row)
        if problem is not None:
            raise ValueError(f"{path}: line {number}: {problem}") from None
        rows.append(row)
    return rows


# --------------------------------------------------------------------------------------------------- the Boundary

class Boundary:
    """One site's way out. ``tasks`` and ``endpoints`` are the values a usage summary may name."""

    def __init__(self, pack: "FrozenPack", site_id: str, *, egress_log: str | Path, receive_log: str | Path,
                 clock: Callable[[], str], tasks: Sequence[str] = (), endpoints: Sequence[str] = ()) -> None:
        if not isinstance(site_id, str) or SITE_ID_RE.fullmatch(site_id) is None:
            raise ValueError("site_id must match [a-z0-9][a-z0-9_.-]{0,63}") from None
        self.pack = pack
        self.site_id = site_id
        self.egress_log = Path(egress_log)
        self.receive_log = Path(receive_log)
        self._clock = clock
        self._tasks = tuple(tasks)
        self._endpoints = tuple(endpoints)
        self.egress_log.parent.mkdir(parents=True, exist_ok=True)
        self.receive_log.parent.mkdir(parents=True, exist_ok=True)
        self._last: dict[str, tuple[str, str]] = {}
        for number, row in enumerate(read_log(self.egress_log), start=1):
            if row["site"] != site_id:
                raise ValueError(f"{self.egress_log}: line {number}: a row of another site") from None
            self._last[row["artifact_type"]] = (row["body"]["closed_through"], row["sha256"])

    def _checked(self, direction: Any, artifact_type: Any, body: Any) -> tuple[bytes, dict[str, Any], str]:
        t = artifact_type if artifact_type in ARTIFACT_TYPES else "unknown"
        if direction not in DIRECTIONS:
            raise EgressError(artifact_type=t, path="$", keyword="direction") from None
        if t == "unknown":
            raise EgressError(artifact_type=t, path="$", keyword="artifact_type") from None
        data = parsed = None
        try:
            data = canonical_bytes(body)
            parsed = strict_load(data)        # a copy that shares nothing with the caller's object
        except StrictJsonError:
            data = None
        if data is None:
            raise EgressError(artifact_type=t, path="$", keyword="json") from None
        found = check_artifact(self.pack, self.site_id, t, parsed, tasks=self._tasks, endpoints=self._endpoints)
        if found is not None:
            raise EgressError(artifact_type=t, path=found[0], keyword=found[1]) from None
        sha = sha256_hex(data)
        last = self._last.get(t)
        resend = last is not None and last[1] == sha
        if not resend and not _same(parsed["after"], last[0] if last is not None else None):
            raise EgressError(artifact_type=t, path="$.after", keyword="sequence") from None
        return data, parsed, sha

    def validate(self, direction: str, artifact_type: str, body: Any) -> dict[str, Any]:
        """A parsed copy of ``body`` if it may leave now; else :class:`EgressError`. Writes nothing."""
        return self._checked(direction, artifact_type, body)[1]

    def append(self, path: Path, line: str) -> None:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
            fh.flush()

    def send(self, direction: str, artifact_type: str, body: Any) -> dict[str, Any]:
        data, parsed, sha = self._checked(direction, artifact_type, body)
        last = self._last.get(artifact_type)
        if last is not None and last[1] == sha:
            return strict_load(data)
        ts = self._clock()
        if not isinstance(ts, str) or TS_RE.fullmatch(ts) is None or local_date(ts) is None:
            raise ValueError("the clock must return an ISO 8601 timestamp") from None
        line = canonical_dumps({"artifact_type": artifact_type, "body": parsed, "bytes": len(data),
                                "direction": direction, "sha256": sha, "site": self.site_id, "ts": ts}) + "\n"
        self.append(self.receive_log, line)
        self.append(self.egress_log, line)
        self._last[artifact_type] = (parsed["closed_through"], sha)
        return strict_load(data)
