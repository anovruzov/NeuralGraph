"""The organisation config HQ detects for: which sites exist, where each sits in the hierarchy, and the decision unit
of a set of sites.

The file is strict JSON, every object closed and every key required::

    {"schema_version": 1, "enterprise": "<segment>",
     "sites": [{"site_id": "<site id>", "unit_path": "<path>", "country": "<CC>", "display_name": "<name>"}, ...]}

Rules:

* ``sites`` holds 1 to 1000 entries. A ``site_id`` matches the connector's site-id pattern and is unique. A
  ``unit_path`` parses with :func:`mycelic.hierarchy.split_path`, has 2 to 5 segments and starts with the enterprise,
  so a site is an org unit below the enterprise and above agents; unit paths are unique and none is an ancestor of
  another, so the decision unit of any set of sites is never ambiguous. ``country`` is two capital letters.
  ``display_name`` is 1 to 80 characters with no control, format, surrogate, private-use, unassigned or line and
  paragraph separator character; non-ASCII letters are allowed, ids stay slugs.
* Validation stops at the first problem, in this order: the top-level shape, ``schema_version``, ``enterprise``,
  each site in list order (keys, ``site_id``, ``unit_path``, ``country``, ``display_name``), then duplicate and
  nested unit paths. :class:`OrgError` names a JSON path and a fixed problem and never holds a value.
* :attr:`OrgConfig.org_hash` is the sha256 of the canonical JSON of the schema version, the enterprise and the sites
  sorted by ``site_id``, so it does not change with site order or key order.
* :meth:`OrgConfig.decision_unit` is the lowest common ancestor of a set of sites: their longest common unit-path
  prefix. Order and duplicates do not matter; one site gives its own unit path.

Pure; nothing here reads a clock or writes a file.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ...hierarchy import HierarchyError, is_ancestor_or_self, split_path, validate_segment
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from ..packs.connector import SITE_ID_RE

SCHEMA_VERSION = 1
ORG_KEYS = ("schema_version", "enterprise", "sites")
SITE_KEYS = ("site_id", "unit_path", "country", "display_name")
MAX_SITES = 1000
MIN_SEGMENTS = 2
MAX_SEGMENTS = 5
MAX_DISPLAY_NAME = 80
COUNTRY_RE = re.compile(r"[A-Z]{2}", re.ASCII)
FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


class OrgError(ValueError):
    """``str`` is ``org: <path>: <problem>``; the path is built from the format's key names and list indices."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"org: {path}: {problem}")
        self.path = path
        self.problem = problem


@dataclass(frozen=True)
class OrgSite:
    site_id: str
    unit_path: str
    country: str
    display_name: str


@dataclass(frozen=True)
class OrgConfig:
    enterprise: str
    sites: Mapping[str, OrgSite]
    org_hash: str

    def unit_path(self, site_id: str) -> str:
        site = self.sites.get(site_id) if isinstance(site_id, str) else None
        if site is None:
            raise OrgError("$.sites", "unknown site") from None
        return site.unit_path

    def decision_unit(self, site_ids: Iterable[str]) -> str:
        """The longest common unit-path prefix of the sites (their lowest common ancestor)."""
        paths = sorted({self.unit_path(s) for s in site_ids})
        if not paths:
            raise OrgError("$.sites", "no site given") from None
        common = paths[0].split("/")
        for path in paths[1:]:
            parts = path.split("/")
            n = 0
            while n < min(len(common), len(parts)) and common[n] == parts[n]:
                n += 1
            common = common[:n]
        return "/".join(common)


def _closed(value: Any, path: str, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OrgError(path, "must be an object") from None
    if any(key not in keys for key in value):
        raise OrgError(path, "unknown key") from None
    for key in keys:
        if key not in value:
            raise OrgError(f"{path}.{key}", "missing key") from None
    return value


def _display_name(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= MAX_DISPLAY_NAME
            and not any(unicodedata.category(ch) in FORBIDDEN_CATEGORIES for ch in value))


def _site(raw: Any, path: str, enterprise: str, seen: set[str]) -> OrgSite:
    site = _closed(raw, path, SITE_KEYS)
    site_id = site["site_id"]
    if not isinstance(site_id, str) or SITE_ID_RE.fullmatch(site_id) is None:
        raise OrgError(f"{path}.site_id", "invalid site id") from None
    if site_id in seen:
        raise OrgError(f"{path}.site_id", "duplicate site id") from None
    seen.add(site_id)
    parts = None
    try:
        parts = split_path(site["unit_path"])
    except HierarchyError:
        parts = None
    if parts is None:
        raise OrgError(f"{path}.unit_path", "unparseable unit_path") from None
    if not MIN_SEGMENTS <= len(parts) <= MAX_SEGMENTS:
        raise OrgError(f"{path}.unit_path", f"must have {MIN_SEGMENTS} to {MAX_SEGMENTS} segments") from None
    if parts[0] != enterprise:
        raise OrgError(f"{path}.unit_path", "must start with the enterprise") from None
    if not isinstance(site["country"], str) or COUNTRY_RE.fullmatch(site["country"]) is None:
        raise OrgError(f"{path}.country", "must be two capital letters") from None
    if not _display_name(site["display_name"]):
        raise OrgError(f"{path}.display_name", f"must be 1 to {MAX_DISPLAY_NAME} characters with no control, "
                                               "format or separator character") from None
    return OrgSite(site_id=site_id, unit_path=site["unit_path"], country=site["country"],
                   display_name=site["display_name"])


def parse_org(obj: Any) -> OrgConfig:
    raw = _closed(obj, "$", ORG_KEYS)
    if not isinstance(raw["sites"], list) or not 1 <= len(raw["sites"]) <= MAX_SITES:
        raise OrgError("$.sites", f"must be a list of 1 to {MAX_SITES} sites") from None
    version = raw["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise OrgError("$.schema_version", f"must be {SCHEMA_VERSION}") from None
    enterprise = None
    try:
        enterprise = validate_segment(raw["enterprise"], "enterprise")
    except HierarchyError:
        enterprise = None
    if enterprise is None:
        raise OrgError("$.enterprise", "invalid segment") from None
    seen: set[str] = set()
    sites = [_site(s, f"$.sites[{i}]", enterprise, seen) for i, s in enumerate(raw["sites"])]
    for j, later in enumerate(sites):
        for earlier in sites[:j]:
            if later.unit_path == earlier.unit_path:
                raise OrgError(f"$.sites[{j}].unit_path", "duplicate unit_path") from None
            if (is_ancestor_or_self(earlier.unit_path, later.unit_path)
                    or is_ancestor_or_self(later.unit_path, earlier.unit_path)):
                raise OrgError(f"$.sites[{j}].unit_path", "nested unit_path") from None
    ordered = sorted(sites, key=lambda s: s.site_id)
    canonical = {"schema_version": SCHEMA_VERSION, "enterprise": enterprise,
                 "sites": [{"site_id": s.site_id, "unit_path": s.unit_path, "country": s.country,
                            "display_name": s.display_name} for s in ordered]}
    return OrgConfig(enterprise=enterprise, sites=MappingProxyType({s.site_id: s for s in ordered}),
                     org_hash=sha256_hex(canonical_bytes(canonical)))


def load_org(path: str | Path) -> OrgConfig:
    data = None
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise OrgError("$", f"cannot read ({exc.__class__.__name__})") from None
    value = reason = None
    try:
        value = strict_load(data)
    except StrictJsonError as err:
        reason = err.reason
    if reason is not None:
        raise OrgError("$", f"not strict JSON ({reason})") from None
    return parse_org(value)
