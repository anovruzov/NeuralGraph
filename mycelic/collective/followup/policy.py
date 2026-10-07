"""Follow-up policy (G7): the labels, ``as_of``, principals, the approvers file and the kill switch.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

**as_of.** Every follow-up call carries a logical time, normalised by :func:`normalise_as_of` to
``YYYY-MM-DDTHH:MM:SSZ``: a calendar date means 00:00:00Z, and a timestamp needs seconds and a ``Z`` or ``+00:00``
offset (a fraction is truncated). Any other offset, an impossible date or time, or anything else is a
:class:`FollowupError`. The daily cap counts by :func:`utc_date`, the first ten characters.

**Principals.** :data:`SYSTEM` (the only principal that may propose; never a decider) and :func:`human` (a person
label ``[a-z][a-z0-9_.-]{1,63}``, never ``system``).

**Approvers** live in their own strict-JSON file, read at every use and validated against the ``OrgConfig`` and the
pack (D1; they are not part of the org file, whose hash every detection run stores)::

    {"schema_version": 1, "enterprise": "<segment>",
     "approvers": [{"person_label": "<label>", "role": "<pack role>", "unit_path": "<path>"}, ...]}

Every object is closed and every key required; 0 to 1000 entries. Validation stops at the first problem, in this
order: the top-level shape, ``schema_version``, ``enterprise`` (the org's), each entry in list order (keys,
``person_label``, ``role``, ``unit_path``: it parses, has 1 to 5 segments, starts with the enterprise and is an
ancestor-or-self of at least one org site's unit path), then duplicate ``(person_label, role, unit_path)`` triples.
:class:`ApproversError` names a JSON path and a fixed problem, never a value. ``approvers_hash`` is the sha256 of the
canonical sorted entries. :meth:`Approvers.find` walks from a unit up to the enterprise and takes the smallest label
holding the role at the first unit that has one; :meth:`Approvers.authority` answers whether a person may decide for
a set of target units (``not_an_approver``, ``role_not_allowed``, ``out_of_scope`` or None).

**The kill switch** (:class:`KillSwitch`) is read on every call, never cached: a strict-JSON file ``{"schema_version":
1, "global": "on"|"off", "types": {"<type id>": "on"|"off"}}`` (closed; an unknown type id or a bad value makes the
file unreadable) and the environment variable :data:`KILL_ENV` (comma-separated ``all`` or pack follow-up type ids;
any other token is invalid). It fails closed: the first ON source wins, in the order of :data:`KILL_SOURCES`
(``file_missing``, ``file_unreadable``, ``file_global``, ``env_invalid``, ``env_global``, ``file_type``,
``env_type``).

Pure apart from reading the two files and the environment it is given; no clock.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from ...hierarchy import HierarchyError, ancestors, is_ancestor_or_self, split_path
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from ..packs.connector import valid_date

if TYPE_CHECKING:
    from ..detect.org import OrgConfig
    from ..packs.loader import FrozenPack

BUILT_AHEAD_LABEL = ("Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is "
                     "unvalidated; nothing here measures it.")
OUTCOME_LABEL = "measurement only; not causal, no counterfactual"
PERSON_LABEL_RE = re.compile(r"[a-z][a-z0-9_.-]{1,63}", re.ASCII)
PRINCIPAL_KINDS = ("system", "human")
_AS_OF_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})"
                       r"(?:T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,9})?(?:Z|\+00:00))?", re.ASCII)
APPROVERS_KEYS = ("schema_version", "enterprise", "approvers")
APPROVER_KEYS = ("person_label", "role", "unit_path")
MAX_APPROVERS = 1000
MAX_UNIT_SEGMENTS = 5
AUTHORITY_CODES = ("not_an_approver", "role_not_allowed", "out_of_scope")
KILL_ENV = "MYCELIC_FOLLOWUP_KILL"
KILL_KEYS = ("schema_version", "global", "types")
SWITCH_VALUES = ("on", "off")
KILL_SOURCES = ("file_missing", "file_unreadable", "file_global", "env_invalid", "env_global", "file_type",
                "env_type")


class FollowupError(ValueError):
    """A malformed follow-up call; the text is fixed and never holds a value. Nothing is written."""


# --------------------------------------------------------------------------------------------------- as_of

def normalise_as_of(value: Any) -> str:
    m = _AS_OF_RE.fullmatch(value) if isinstance(value, str) else None
    if m is None or not valid_date(m.group(1)):
        raise FollowupError("as_of must be a UTC date YYYY-MM-DD or a timestamp with Z or +00:00") from None
    if m.group(2) is None:
        return f"{m.group(1)}T00:00:00Z"
    hour, minute, second = int(m.group(2)), int(m.group(3)), int(m.group(4))
    if hour > 23 or minute > 59 or second > 59:
        raise FollowupError("as_of must be a UTC date YYYY-MM-DD or a timestamp with Z or +00:00") from None
    return f"{m.group(1)}T{hour:02d}:{minute:02d}:{second:02d}Z"


def utc_date(as_of: str) -> str:
    return as_of[:10]


def plus_days(as_of: str, n: int) -> str:
    """A normalised ``as_of`` moved by ``n`` whole days; a day past 9999-12-31 is :class:`FollowupError`."""
    day: date | None = None
    try:
        day = date.fromisoformat(as_of[:10]) + timedelta(days=n)
    except OverflowError:
        day = None
    if day is None:
        raise FollowupError("as_of moved by the acknowledgement days passes the last calendar date") from None
    return day.isoformat() + as_of[10:]


# --------------------------------------------------------------------------------------------------- principals

@dataclass(frozen=True)
class Principal:
    kind: str
    label: str

    def __post_init__(self) -> None:
        if self.kind == "system":
            ok = self.label == "system"
        else:
            ok = (self.kind == "human" and isinstance(self.label, str)
                  and PERSON_LABEL_RE.fullmatch(self.label) is not None and self.label != "system")
        if not ok:
            raise FollowupError("a principal is the system or a human with a person label") from None


SYSTEM = Principal("system", "system")


def human(label: str) -> Principal:
    return Principal("human", label)


# --------------------------------------------------------------------------------------------------- approvers

class ApproversError(ValueError):
    """``str`` is ``approvers: <path>: <problem>``; the path is built from the format's key names and indices."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"approvers: {path}: {problem}")
        self.path = path
        self.problem = problem


@dataclass(frozen=True)
class Approver:
    person_label: str
    role: str
    unit_path: str


@dataclass(frozen=True)
class Approvers:
    enterprise: str
    entries: tuple[Approver, ...]
    approvers_hash: str

    def holders(self, role: str, unit: str) -> list[str]:
        """The labels holding exactly ``role`` at exactly ``unit``, sorted."""
        return sorted(e.person_label for e in self.entries if e.role == role and e.unit_path == unit)

    def find(self, role: str, start_unit: str) -> tuple[str | None, str | None]:
        """From ``start_unit`` up to the enterprise, the first unit with a holder of ``role`` and its smallest
        label; ``(None, None)`` when there is none."""
        for unit in reversed(ancestors(start_unit)):
            labels = self.holders(role, unit)
            if labels:
                return labels[0], unit
        return None, None

    def grant(self, label: str, allowed_roles: Iterable[str], target_units: Iterable[str]) -> Approver | None:
        """The first entry (sorted) of ``label`` with an allowed role whose unit covers every target unit."""
        roles, targets = frozenset(allowed_roles), tuple(target_units)
        for e in self.entries:
            if e.person_label == label and e.role in roles and all(is_ancestor_or_self(e.unit_path, t)
                                                                   for t in targets):
                return e
        return None

    def authority(self, label: str, allowed_roles: Iterable[str], target_units: Iterable[str]) -> str | None:
        """None when ``label`` may decide for every target unit, else the first failing code of
        :data:`AUTHORITY_CODES`."""
        roles = frozenset(allowed_roles)
        mine = [e for e in self.entries if e.person_label == label]
        if not mine:
            return "not_an_approver"
        if not any(e.role in roles for e in mine):
            return "role_not_allowed"
        return None if self.grant(label, roles, target_units) is not None else "out_of_scope"


def _closed(value: Any, path: str, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApproversError(path, "must be an object") from None
    if any(key not in keys for key in value):
        raise ApproversError(path, "unknown key") from None
    for key in keys:
        if key not in value:
            raise ApproversError(f"{path}.{key}", "missing key") from None
    return value


def _entry(raw: Any, path: str, org: "OrgConfig", pack: "FrozenPack") -> Approver:
    entry = _closed(raw, path, APPROVER_KEYS)
    label = entry["person_label"]
    if not isinstance(label, str) or PERSON_LABEL_RE.fullmatch(label) is None or label == "system":
        raise ApproversError(f"{path}.person_label", "invalid person label") from None
    if not isinstance(entry["role"], str) or entry["role"] not in pack.roles:
        raise ApproversError(f"{path}.role", "unknown role") from None
    parts = None
    try:
        parts = split_path(entry["unit_path"])
    except HierarchyError:
        parts = None
    if parts is None:
        raise ApproversError(f"{path}.unit_path", "unparseable unit_path") from None
    if not 1 <= len(parts) <= MAX_UNIT_SEGMENTS:
        raise ApproversError(f"{path}.unit_path", f"must have 1 to {MAX_UNIT_SEGMENTS} segments") from None
    if parts[0] != org.enterprise:
        raise ApproversError(f"{path}.unit_path", "must start with the enterprise") from None
    unit = entry["unit_path"]
    if not any(is_ancestor_or_self(unit, site.unit_path) for site in org.sites.values()):
        raise ApproversError(f"{path}.unit_path", "covers no site") from None
    return Approver(person_label=label, role=entry["role"], unit_path=unit)


def parse_approvers(obj: Any, org: "OrgConfig", pack: "FrozenPack") -> Approvers:
    raw = _closed(obj, "$", APPROVERS_KEYS)
    if not isinstance(raw["approvers"], list) or len(raw["approvers"]) > MAX_APPROVERS:
        raise ApproversError("$.approvers", f"must be a list of 0 to {MAX_APPROVERS} entries") from None
    version = raw["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ApproversError("$.schema_version", "must be 1") from None
    if raw["enterprise"] != org.enterprise:
        raise ApproversError("$.enterprise", "must be the org's enterprise") from None
    entries = [_entry(e, f"$.approvers[{i}]", org, pack) for i, e in enumerate(raw["approvers"])]
    seen: set[Approver] = set()
    for i, e in enumerate(entries):
        if e in seen:
            raise ApproversError(f"$.approvers[{i}]", "duplicate entry") from None
        seen.add(e)
    ordered = tuple(sorted(entries, key=lambda e: (e.person_label, e.role, e.unit_path)))
    digest = sha256_hex(canonical_bytes([{"person_label": e.person_label, "role": e.role, "unit_path": e.unit_path}
                                         for e in ordered]))
    return Approvers(enterprise=org.enterprise, entries=ordered, approvers_hash=digest)


def load_approvers(path: str | Path, org: "OrgConfig", pack: "FrozenPack") -> Approvers:
    data = None
    try:
        data = Path(path).read_bytes()
    except OSError:
        data = None
    if data is None:
        raise ApproversError("$", "cannot read") from None
    value = reason = None
    try:
        value = strict_load(data)
    except StrictJsonError as err:
        reason = err.reason
    if reason is not None:
        raise ApproversError("$", f"not strict JSON ({reason})") from None
    return parse_approvers(value, org, pack)


# --------------------------------------------------------------------------------------------------- kill switch

@dataclass(frozen=True)
class KillState:
    on: bool
    source: str | None


class KillSwitch:
    """The kill switch of one pack's follow-up types; ``environ`` defaults to ``os.environ``, read at call time."""

    def __init__(self, path: str | Path, pack: "FrozenPack", *, environ: Mapping[str, str] | None = None,
                 env_var: str = KILL_ENV) -> None:
        self.path = Path(path)
        self.pack = pack
        self.env_var = env_var
        self._environ = environ

    def _file(self) -> tuple[str | None, dict[str, Any] | None]:
        """``(None, document)`` or ``('file_missing' | 'file_unreadable', None)``."""
        data = problem = None
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            problem = "file_missing"
        except OSError:
            problem = "file_unreadable"
        if problem is not None:
            return problem, None
        doc = None
        try:
            doc = strict_load(data)
        except StrictJsonError:
            doc = None
        ok = (isinstance(doc, dict) and sorted(doc) == sorted(KILL_KEYS)
              and not isinstance(doc["schema_version"], bool) and doc["schema_version"] == 1
              and isinstance(doc["schema_version"], int) and doc["global"] in SWITCH_VALUES
              and isinstance(doc["types"], dict)
              and all(t in self.pack.followups and doc["types"][t] in SWITCH_VALUES for t in doc["types"]))
        return (None, doc) if ok else ("file_unreadable", None)

    def _env(self) -> tuple[bool, frozenset[str]]:
        """``(valid, tokens)``: unset or empty is valid and empty; any token that is neither ``all`` nor a pack
        follow-up type id makes it invalid."""
        environ = os.environ if self._environ is None else self._environ
        value = environ.get(self.env_var)
        if value is None or value == "":
            return True, frozenset()
        tokens = frozenset(t.strip() for t in value.split(","))
        return all(t == "all" or t in self.pack.followups for t in tokens), tokens

    def state(self, type_id: str) -> KillState:
        """Read now, never cached; the first ON source of :data:`KILL_SOURCES` wins."""
        problem, doc = self._file()
        if problem is not None:
            return KillState(True, problem)
        if doc["global"] == "on":
            return KillState(True, "file_global")
        valid, tokens = self._env()
        if not valid:
            return KillState(True, "env_invalid")
        if "all" in tokens:
            return KillState(True, "env_global")
        if doc["types"].get(type_id) == "on":
            return KillState(True, "file_type")
        if type_id in tokens:
            return KillState(True, "env_type")
        return KillState(False, None)
