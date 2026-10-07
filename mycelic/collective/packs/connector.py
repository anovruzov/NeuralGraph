"""Vendor rows in, internal records out, driven entirely by a pack's mapping file.

A record has exactly :data:`RECORD_KEYS`. Field types:

* ``record_ref``: 1..128 printable characters; ``site``: a site id; ``received_date``: ``YYYY-MM-DD``;
* ``language``: str or None; ``codes``: unique str; ``entities``: ``{type: [unique str]}`` with exactly the
  mapping's entity types; ``narrative``: str (``""`` when there is none);
* ``persons``: ``{field: str | None}`` with exactly the mapping's person fields; ``reporter``: str or None;
* ``origin_ref`` and ``origin_site``: both None or both str (a forwarded record names its origin);
* ``synthetic``: bool.

Mapping paths are dot-separated vendor field names; a ``[]`` suffix fans out over an array, and a narrative
``where`` keeps only the elements of the last fanned-out array whose sibling field is in a list. Only these fields
are produced: a vendor field the mapping does not name never reaches a record. Rejected rows are counted per
reason (:data:`REJECT_REASONS`, plus ``missing_<target>`` for each required target); a code value that a
``value_map`` does not list is counted in ``unmapped_code_values`` (over the records produced), not rejected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..jsonio import StrictJsonError, strict_load

RECORD_KEYS = ("record_ref", "site", "received_date", "language", "codes", "entities", "narrative", "persons",
               "reporter", "origin_ref", "origin_site", "synthetic")
REJECT_REASONS = ("not_object", "invalid_json", "missing_record_ref", "missing_received_date", "bad_date", "bad_type",
                  "bad_site", "duplicate_record_ref")
SITE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}", re.ASCII)
ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
COMPACT_DATE_RE = re.compile(r"[0-9]{8}", re.ASCII)
MAX_REF = 128


class ConnectorError(ValueError):
    pass


class _Reject(Exception):
    """Internal: a row is rejected for ``reason``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_INVALID_LINE = object()


@dataclass(frozen=True)
class Mapped:
    records: tuple[dict[str, Any], ...]
    rejected: dict[str, int]
    rows: int
    unmapped_code_values: int


def valid_date(text: Any) -> bool:
    if not isinstance(text, str) or ISO_DATE_RE.fullmatch(text) is None:
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _str_list(value: Any) -> bool:
    return (isinstance(value, list) and all(isinstance(v, str) for v in value)
            and len(set(value)) == len(value))


def _mapping(pack: Any, name: str) -> Any:
    found = pack.mappings.get(name) if isinstance(name, str) else None
    if found is None:
        raise ConnectorError(f"pack {pack.id} has no mapping {name!r}") from None
    return found


def record_problems(record: Any, pack: Any, mapping: str = "mapping") -> list[str]:
    m = _mapping(pack, mapping)
    if not isinstance(record, dict):
        return ["record is not an object"]
    missing = [k for k in RECORD_KEYS if k not in record]
    extra = sorted(k for k in record if k not in RECORD_KEYS)
    if missing or extra:
        return [f"record keys differ: missing {missing}, unexpected {extra}"]
    out = []
    ref = record["record_ref"]
    if not isinstance(ref, str) or not 1 <= len(ref) <= MAX_REF or not ref.isprintable():
        out.append(f"record_ref: must be 1..{MAX_REF} printable characters")
    if not isinstance(record["site"], str) or SITE_ID_RE.fullmatch(record["site"]) is None:
        out.append("site: must match [a-z0-9][a-z0-9_.-]{0,63}")
    if not valid_date(record["received_date"]):
        out.append("received_date: must be a calendar date YYYY-MM-DD")
    if record["language"] is not None and not isinstance(record["language"], str):
        out.append("language: must be a string or null")
    if not _str_list(record["codes"]):
        out.append("codes: must be a list of unique strings")
    entities = record["entities"]
    if not isinstance(entities, dict) or sorted(entities) != sorted(m["entities"]):
        out.append(f"entities: must have exactly the keys {sorted(m['entities'])}")
    elif not all(_str_list(entities[t]) for t in sorted(entities)):
        out.append("entities: every value must be a list of unique strings")
    if not isinstance(record["narrative"], str):
        out.append("narrative: must be a string")
    persons = record["persons"]
    if not isinstance(persons, dict) or sorted(persons) != sorted(m["persons"]):
        out.append(f"persons: must have exactly the keys {sorted(m['persons'])}")
    elif not all(v is None or isinstance(v, str) for v in persons.values()):
        out.append("persons: every value must be a string or null")
    if record["reporter"] is not None and not isinstance(record["reporter"], str):
        out.append("reporter: must be a string or null")
    origin = (record["origin_ref"], record["origin_site"])
    if not (origin == (None, None) or all(isinstance(v, str) for v in origin)):
        out.append("origin_ref and origin_site: must be both null or both strings")
    if not isinstance(record["synthetic"], bool):
        out.append("synthetic: must be a boolean")
    return out


def check_record(record: Any, pack: Any, mapping: str = "mapping") -> None:
    problems = record_problems(record, pack, mapping)
    if problems:
        raise ConnectorError(problems[0]) from None


def record_mapping(record: Any, pack: Any) -> str | None:
    """The first of the pack's mappings (in name order) under which ``record`` has no problems, else None."""
    for name in sorted(pack.mappings):
        if not record_problems(record, pack, name):
            return name
    return None


# --------------------------------------------------------------------------------------------------- paths

def _segments(path: str) -> list[tuple[str, bool]]:
    return [(seg[:-2], True) if seg.endswith("[]") else (seg, False) for seg in path.split(".")]


def evaluate(row: Any, path: str, where: Mapping[str, Any] | None = None) -> list[Any]:
    """Every value at ``path`` in document order; [] when absent. Raises _Reject('bad_type') when the row's shape
    contradicts the path (a non-object where an object is needed, a non-array where ``[]`` fans out)."""
    segs = _segments(path)
    last_fan = max((i for i, (_, fan) in enumerate(segs) if fan), default=None)
    nodes = [row]
    for i, (name, fan) in enumerate(segs):
        nxt = []
        for node in nodes:
            if node is None:
                continue
            if not isinstance(node, dict):
                raise _Reject("bad_type") from None
            value = node.get(name)
            if value is None:
                continue
            if fan:
                if not isinstance(value, list):
                    raise _Reject("bad_type") from None
                items = value
                if where is not None and i == last_fan:
                    items = [v for v in items if isinstance(v, dict) and v.get(where["field"]) in where["in"]]
                nxt.extend(items)
            else:
                nxt.append(value)
        nodes = nxt
    return [n for n in nodes if n is not None]


def _scalar(value: Any) -> str | None:
    """A stripped string, None for empty; ints become str; anything else is bad_type."""
    if isinstance(value, bool):
        raise _Reject("bad_type") from None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.strip() or None
    raise _Reject("bad_type") from None


def _scalars(row: Any, path: str | None, where: Mapping[str, Any] | None = None) -> list[str]:
    if path is None:
        return []
    out = []
    for value in evaluate(row, path, where):
        s = _scalar(value)
        if s is not None:
            out.append(s)
    return out


def _one(row: Any, path: str | None) -> str | None:
    values = _scalars(row, path)
    return values[0] if values else None


def _unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _date(value: str, fmt: str) -> str:
    if fmt == "yyyymmdd":
        if COMPACT_DATE_RE.fullmatch(value) is None:
            raise _Reject("bad_date") from None
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    if not valid_date(value):
        raise _Reject("bad_date") from None
    return value


def _map_row(row: Any, m: Any, site: str | None, synthetic: bool, seen: set[str],
             unmapped: list[int]) -> dict[str, Any]:
    """One row as a record; ``unmapped[0]`` counts this row's code values missing from a value map. ``seen`` holds
    the refs of records already produced: a ref first seen on a rejected row does not make a later row a duplicate."""
    if row is _INVALID_LINE:
        raise _Reject("invalid_json") from None
    if not isinstance(row, dict):
        raise _Reject("not_object") from None
    required = set(m["required"])
    ref = _one(row, m["record_ref"])
    if ref is None:
        raise _Reject("missing_record_ref") from None
    if len(ref) > MAX_REF or not ref.isprintable():
        raise _Reject("bad_type") from None
    if ref in seen:
        raise _Reject("duplicate_record_ref") from None
    received = _one(row, m["received_date"]["path"])
    if received is None:
        raise _Reject("missing_received_date") from None
    received = _date(received, m["received_date"]["format"])

    if m["site"] is not None:
        site_value = _one(row, m["site"])
        if site_value is None:
            raise _Reject("missing_site") from None
    else:
        site_value = site
    if site_value is None or SITE_ID_RE.fullmatch(site_value) is None:
        raise _Reject("bad_site") from None

    language = _one(row, m["language"])
    codes: list[str] = []
    for spec in m["codes"]:
        for value in _scalars(row, spec["path"]):
            if spec["value_map"] is None:
                codes.append(value)
            elif value in spec["value_map"]:
                codes.append(spec["value_map"][value])
            else:
                unmapped[0] += 1
    entities = {t: _unique(v for path in m["entities"][t] for v in _scalars(row, path)) for t in sorted(m["entities"])}
    texts = [v for spec in m["narrative"] for v in _scalars(row, spec["path"], spec["where"])]
    narrative = "\n\n".join(texts)
    persons = {f: _one(row, m["persons"][f]) for f in sorted(m["persons"])}
    reporter = _one(row, m["reporter"])
    origin_ref, origin_site = _one(row, m["origin_ref"]), _one(row, m["origin_site"])
    if (origin_ref is None) != (origin_site is None):
        raise _Reject("bad_type") from None
    present = {"narrative": bool(narrative), "codes": bool(codes), "language": language is not None,
               "reporter": reporter is not None, "site": True}
    present.update({f"entities.{t}": bool(entities[t]) for t in entities})
    for target in sorted(required):
        if not present.get(target, False):
            raise _Reject("missing_" + target.replace(".", "_")) from None
    return {"record_ref": ref, "site": site_value, "received_date": received, "language": language,
            "codes": _unique(codes), "entities": entities, "narrative": narrative, "persons": persons,
            "reporter": reporter, "origin_ref": origin_ref, "origin_site": origin_site, "synthetic": bool(synthetic)}


def map_rows(rows: Iterable[Any], pack: Any, *, mapping: str = "mapping", site: str | None = None,
             synthetic: bool = False) -> Mapped:
    m = _mapping(pack, mapping)
    if m["site"] is None and site is None:
        raise ConnectorError(f"mapping {mapping} has no site path: pass a site") from None
    if site is not None and (not isinstance(site, str) or SITE_ID_RE.fullmatch(site) is None):
        raise ConnectorError("site must match [a-z0-9][a-z0-9_.-]{0,63}") from None
    records: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    seen: set[str] = set()
    unmapped_total = 0
    n = 0
    for row in rows:
        n += 1
        reason = None
        unmapped = [0]
        try:
            record = _map_row(row, m, site, synthetic, seen, unmapped)
        except _Reject as exc:
            reason = exc.reason
        if reason is None and record_problems(record, pack, mapping):
            reason = "bad_type"
        if reason is not None:
            rejected[reason] = rejected.get(reason, 0) + 1
        else:
            records.append(record)
            seen.add(record["record_ref"])
            unmapped_total += unmapped[0]
    return Mapped(records=tuple(records), rejected=dict(sorted(rejected.items())), rows=n,
                  unmapped_code_values=unmapped_total)


def read_jsonl(path: str | Path, pack: Any, *, mapping: str = "mapping", site: str | None = None,
               synthetic: bool = False) -> Mapped:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ConnectorError(f"cannot read {path} ({exc.__class__.__name__})") from None
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    rows: list[Any] = []
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            rows.append(strict_load(line.rstrip(b"\r")))
        except StrictJsonError:
            rows.append(_INVALID_LINE)
    return map_rows(rows, pack, mapping=mapping, site=site, synthetic=synthetic)
