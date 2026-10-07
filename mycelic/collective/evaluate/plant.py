"""Plant specs: true patterns and decoys planted into a generated world, and the labels the scorecard is read against.

A plant spec is strict JSON, every object closed::

    {"kind": "plant_spec", "schema_version": 1, "pack": "<pack id>", "prereg_sha256": null | "<64 hex>",
     "planted_by": "<1..80 printable>", "planter_saw_detector_code": <bool>, "notes": "<at most 2000>",
     "patterns": [<1..500 patterns>], "decoys": [<0..500 decoys>]}

A pattern is ``{id, entity_type, entity_id, predicate, sites (>= 2), start_week, weeks, rate_per_week, visibility,
language}``. A decoy has ``{id, class, entity_type, predicate, sites, start_week, weeks, rate_per_week, language}``
plus, by class: ``entity_id`` and ``copy_sites`` (``echo_marked``, ``cross_site_unmarked_copies``; ``sites`` is the
one origin); ``entity_id`` (``same_site_duplicates`` and ``single_site_burst`` with one site, ``single_reporter`` and
``stale_chain`` with at least two); ``entity_ids`` (``high_base_rate_everywhere``, at least two of one type);
``entity_id``, ``near_miss_id`` and ``near_miss_sites`` (``near_miss_entity``: A at its one site, B at another).
``start_week`` is a 0-based index into the world's weeks; ``rate_per_week`` is the exact number of records per counted
site per week.

Rules:

* **Errors.** :class:`PlantError` holds a JSON path and a fixed problem and never a value from the spec.
  :func:`parse_plant` raises the first problem in a fixed order: the top level (shape, unknown and missing keys,
  types), ``kind`` and ``schema_version``, ``pack``, ``prereg_sha256``, ``planted_by``, ``notes``, the list sizes,
  then each item in list order (a decoy's ``class`` is read first, since its keys depend on it; then keys, id, entity
  type, ids, predicate, site lists, week and rate ranges, visibility and language, what the construction needs, the
  class's shape), then across items (duplicate ids, then a key planted twice). :func:`check_plant` adds the world:
  sites of the org, master data of every counted site, the world and evaluation weeks, staleness before the
  evaluation weeks, a high base rate's site share and a single reporter's rate against k.
* **Planted records carry only the planted mention.** ``narrative_only`` (every decoy is): no codes and no structured
  entities, a narrative of one single-slot template plus filler; ``codes_only``: one specific code of the predicate,
  the structured id and filler only; ``both``: all of these. So S and the model-free R can never see a
  ``narrative_only`` pattern or a decoy through planted records.
* **Construction** (:func:`plant`) uses only ``random.Random(f"plant:{spec sha256}:{world seed}")``. Persons and
  reporters are drawn from each site's background records; narratives are unique against the world (re-drawn at
  most :data:`NARRATIVE_ATTEMPTS` times), except same-site duplicates (one narrative reused) and copies (the
  origin's). Marked copies name their origin; unmarked copies do not, so each is its own root at its copy site.
* **Labels** (:func:`labels_doc`) are seed-independent: each pattern's found window ``[start, min(end + grace,
  eval_to)]``, each decoy's watch span and the quiet precondition the structural classes are checked under.

Pure; nothing here reads a clock or writes a file.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ..detect.rules import series_key
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from ..packs.canonical import Canonicaliser
from ..packs.connector import SITE_ID_RE, record_problems
from ..packs.generator import World

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack

KIND = "plant_spec"
LABELS_KIND = "plant_labels"
SCHEMA_VERSION = 1
TOP_KEYS = ("kind", "schema_version", "pack", "prereg_sha256", "planted_by", "planter_saw_detector_code", "notes",
            "patterns", "decoys")
PATTERN_KEYS = ("id", "entity_type", "entity_id", "predicate", "sites", "start_week", "weeks", "rate_per_week",
                "visibility", "language")
DECOY_CLASSES = ("echo_marked", "same_site_duplicates", "cross_site_unmarked_copies", "single_reporter",
                 "stale_chain", "high_base_rate_everywhere", "single_site_burst", "near_miss_entity")
STRUCTURAL_CLASSES = ("echo_marked", "same_site_duplicates", "single_site_burst", "near_miss_entity", "stale_chain")
PENALTY_CLASSES = ("single_reporter", "high_base_rate_everywhere")
HARD_CASE_CLASSES = ("cross_site_unmarked_copies",)
_DECOY_COMMON = ("id", "class", "entity_type", "predicate", "sites", "start_week", "weeks", "rate_per_week",
                 "language")
DECOY_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "echo_marked": (*_DECOY_COMMON, "entity_id", "copy_sites"),
    "cross_site_unmarked_copies": (*_DECOY_COMMON, "entity_id", "copy_sites"),
    "same_site_duplicates": (*_DECOY_COMMON, "entity_id"),
    "single_site_burst": (*_DECOY_COMMON, "entity_id"),
    "single_reporter": (*_DECOY_COMMON, "entity_id"),
    "stale_chain": (*_DECOY_COMMON, "entity_id"),
    "high_base_rate_everywhere": (*_DECOY_COMMON, "entity_ids"),
    "near_miss_entity": (*_DECOY_COMMON, "entity_id", "near_miss_id", "near_miss_sites"),
})
# (minimum, maximum) number of origin sites per class; None is unbounded
_SITE_COUNTS: Mapping[str, tuple[int, int | None]] = MappingProxyType({
    "echo_marked": (1, 1), "cross_site_unmarked_copies": (1, 1), "same_site_duplicates": (1, 1),
    "single_site_burst": (1, 1), "single_reporter": (2, None), "stale_chain": (2, None),
    "high_base_rate_everywhere": (1, None), "near_miss_entity": (1, 1)})
VISIBILITIES = ("narrative_only", "codes_only", "both")
TEXT_VISIBILITIES = ("narrative_only", "both")
CODE_VISIBILITIES = ("codes_only", "both")
DECOY_VISIBILITY = "narrative_only"
QUIET_RULES = ("other_sites_at_most_one", "all_sites_zero")
MIN_PATTERN_SITES = 2                  # STRATEGY 6.1: a hidden pattern spans at least two sites; not a detector setting
MAX_ITEMS = 500
MAX_WEEKS = 520
MAX_RATE = 50
MAX_PLANTED_BY = 80
MAX_NOTES = 2000
NARRATIVE_ATTEMPTS = 20
ITEM_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}", re.ASCII)
SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SLOT = re.compile(r"\{([^{}]*)\}")
_PERSON = "person:"


class PlantError(ValueError):
    """``str`` is ``plant: <path>: <problem>``; the path is built from the format's key names and list indices."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"plant: {path}: {problem}")
        self.path = path
        self.problem = problem


@dataclass(frozen=True)
class Pattern:
    path: str
    id: str
    entity_type: str
    entity_id: str
    predicate: str
    sites: tuple[str, ...]
    start_week: int
    weeks: int
    rate_per_week: int
    visibility: str
    language: str

    @property
    def end_week(self) -> int:
        return self.start_week + self.weeks - 1

    @property
    def key(self) -> str:
        return series_key(self.entity_type, self.entity_id, self.predicate)


@dataclass(frozen=True)
class Decoy:
    path: str
    id: str
    decoy_class: str
    entity_type: str
    entity_ids: tuple[str, ...]          # one id; several for high_base_rate_everywhere
    predicate: str
    sites: tuple[str, ...]
    copy_sites: tuple[str, ...]
    near_miss_id: str | None
    near_miss_sites: tuple[str, ...]
    start_week: int
    weeks: int
    rate_per_week: int
    language: str
    visibility: str = DECOY_VISIBILITY

    @property
    def end_week(self) -> int:
        return self.start_week + self.weeks - 1

    def counted(self) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        """``(key, entity id, sites)`` per key: the sites whose planted records count in that key's cells (a marked
        copy is forwarded-in at its copy site and counts nowhere; an unmarked copy counts at its copy site)."""
        if self.decoy_class == "near_miss_entity":
            return ((series_key(self.entity_type, self.entity_ids[0], self.predicate), self.entity_ids[0],
                     self.sites),
                    (series_key(self.entity_type, self.near_miss_id, self.predicate), self.near_miss_id,
                     self.near_miss_sites))
        sites = self.sites + self.copy_sites if self.decoy_class == "cross_site_unmarked_copies" else self.sites
        return tuple((series_key(self.entity_type, eid, self.predicate), eid, sites) for eid in self.entity_ids)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _, _ in self.counted())

    @property
    def planted_sites(self) -> tuple[str, ...]:
        return self.sites + self.copy_sites + self.near_miss_sites


@dataclass(frozen=True)
class PlantSpec:
    raw: Mapping[str, Any]
    sha256: str
    pack: str
    prereg_sha256: str | None
    planted_by: str
    planter_saw_detector_code: bool
    notes: str
    patterns: tuple[Pattern, ...]
    decoys: tuple[Decoy, ...]

    @property
    def items(self) -> tuple[Pattern | Decoy, ...]:
        return self.patterns + self.decoys


@dataclass(frozen=True)
class Planted:
    records: tuple[dict[str, Any], ...]
    counts: Mapping[str, int]            # item id -> records planted for it (copies included)


# --------------------------------------------------------------------------------------------------- pack helpers

def single_slot_templates(pack: "FrozenPack", language: str, predicate: str, entity_type: str) -> list[str]:
    """The affirmed templates of (language, predicate) whose non-person slots are exactly ``[entity_type]``, in the
    order the world spec lists them."""
    by_predicate = pack.generator["narratives"].get(language, {}).get(predicate)
    if by_predicate is None:
        return []
    return [t for t in by_predicate["affirmed"]
            if [s for s in _SLOT.findall(t) if not s.startswith(_PERSON)] == [entity_type]]


def aliases_of(pack: "FrozenPack", entity_type: str, entity_id: str) -> list[str]:
    table = pack.aliases.get(entity_type, {})
    return sorted(alias for alias in table if table[alias] == entity_id)


def specific_codes(pack: "FrozenPack", predicate: str) -> list[str]:
    return sorted(c for c in pack.codes if pack.codes[c].predicate == predicate and pack.codes[c].specific)


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insertions, deletions and substitutions, each costing 1)."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, start=1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def is_blind(spec: PlantSpec, detector_author: str) -> bool:
    """Self-declared blindness: the planter did not see the detector code and is not the detector's author (names
    compared case-folded with whitespace collapsed)."""
    return (not spec.planter_saw_detector_code) and _norm(spec.planted_by) != _norm(detector_author)


# --------------------------------------------------------------------------------------------------- parsing

def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


_TOP_TYPES = {"kind": (str,), "schema_version": (int,), "pack": (str,), "prereg_sha256": (str, type(None)),
              "planted_by": (str,), "planter_saw_detector_code": (bool,), "notes": (str,), "patterns": (list,),
              "decoys": (list,)}


def _closed(raw: Any, path: str, keys: Sequence[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlantError(path, "must be an object") from None
    if any(key not in keys for key in raw):
        raise PlantError(path, "unknown key") from None
    for key in keys:
        if key not in raw:
            raise PlantError(f"{path}.{key}", "missing key") from None
    return raw


def _check_id(pack: "FrozenPack", entity_type: str, value: Any, path: str) -> str:
    et = pack.entity_types[entity_type]
    if et.id_format is None:
        if not isinstance(value, str) or value not in et.ids:
            raise PlantError(path, "unknown id") from None
    elif not et.id_format.is_canonical(value):
        raise PlantError(path, "id not canonical") from None
    return value


def _sites(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(s, str) and SITE_ID_RE.fullmatch(s) for s in value):
        raise PlantError(path, "must be a list of site ids") from None
    for j, s in enumerate(value):
        if s in value[:j]:
            raise PlantError(f"{path}[{j}]", "duplicate site") from None
    return tuple(value)


def _ranges(raw: Mapping[str, Any], path: str) -> None:
    if not _int(raw["start_week"]) or raw["start_week"] < 0:
        raise PlantError(f"{path}.start_week", "must be an int >= 0") from None
    if not _int(raw["weeks"]) or not 1 <= raw["weeks"] <= MAX_WEEKS:
        raise PlantError(f"{path}.weeks", f"must be an int 1..{MAX_WEEKS}") from None
    if not _int(raw["rate_per_week"]) or not 1 <= raw["rate_per_week"] <= MAX_RATE:
        raise PlantError(f"{path}.rate_per_week", f"must be an int 1..{MAX_RATE}") from None


def _available(pack: "FrozenPack", path: str, entity_type: str, ids: Sequence[tuple[str, str]], predicate: str,
               language: str, visibility: str) -> None:
    """What the construction needs: a single-slot template and an alias surface for text, a specific code and a
    structured field for codes, filler for every record. ``ids`` are ``(path, id)`` pairs."""
    if language not in pack.generator["filler"]:
        raise PlantError(f"{path}.language", "no filler for the language") from None
    if visibility in TEXT_VISIBILITIES:
        if not single_slot_templates(pack, language, predicate, entity_type):
            raise PlantError(path, "no single-slot template") from None
        if pack.entity_types[entity_type].id_format is None:
            for id_path, eid in ids:
                if not aliases_of(pack, entity_type, eid):
                    raise PlantError(id_path, "no alias surface") from None
    if visibility in CODE_VISIBILITIES:
        if not specific_codes(pack, predicate):
            raise PlantError(f"{path}.predicate", "no specific code") from None
        if entity_type not in pack.mapping()["entities"]:
            raise PlantError(f"{path}.entity_type", "not a structured entity type") from None


def _site_count(sites: tuple[str, ...], path: str, lo: int, hi: int | None) -> None:
    if len(sites) < lo:
        raise PlantError(path, "too few sites") from None
    if hi is not None and len(sites) > hi:
        raise PlantError(path, "too many sites") from None


def _common(raw: dict[str, Any], path: str, pack: "FrozenPack") -> tuple[str, str]:
    """id and entity type checks shared by patterns and decoys; returns (id, entity type)."""
    if not isinstance(raw["id"], str) or ITEM_ID_RE.fullmatch(raw["id"]) is None:
        raise PlantError(f"{path}.id", "invalid id") from None
    t = raw["entity_type"]
    if not isinstance(t, str) or t not in pack.entity_types:
        raise PlantError(f"{path}.entity_type", "unknown entity type") from None
    if t not in pack.egress.egress_entity_types:
        raise PlantError(f"{path}.entity_type", "entity type does not leave the sites") from None
    return raw["id"], t


def _predicate_and_sites(raw: dict[str, Any], path: str, pack: "FrozenPack",
                         extra: Sequence[str] = ()) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw["predicate"], str) or raw["predicate"] not in pack.predicates:
        raise PlantError(f"{path}.predicate", "unknown predicate") from None
    lists = {name: _sites(raw[name], f"{path}.{name}") for name in ("sites", *extra)}
    _ranges(raw, path)
    return lists


def _language(raw: dict[str, Any], path: str, pack: "FrozenPack") -> str:
    if not isinstance(raw["language"], str) or raw["language"] not in pack.languages:
        raise PlantError(f"{path}.language", "unknown language") from None
    return raw["language"]


def _pattern(raw: Any, path: str, pack: "FrozenPack") -> Pattern:
    raw = _closed(raw, path, PATTERN_KEYS)
    item_id, t = _common(raw, path, pack)
    eid = _check_id(pack, t, raw["entity_id"], f"{path}.entity_id")
    lists = _predicate_and_sites(raw, path, pack)
    if not isinstance(raw["visibility"], str) or raw["visibility"] not in VISIBILITIES:
        raise PlantError(f"{path}.visibility", "unknown visibility") from None
    language = _language(raw, path, pack)
    _available(pack, path, t, [(f"{path}.entity_id", eid)], raw["predicate"], language, raw["visibility"])
    _site_count(lists["sites"], f"{path}.sites", MIN_PATTERN_SITES, None)
    return Pattern(path=path, id=item_id, entity_type=t, entity_id=eid, predicate=raw["predicate"],
                   sites=lists["sites"], start_week=raw["start_week"], weeks=raw["weeks"],
                   rate_per_week=raw["rate_per_week"], visibility=raw["visibility"], language=language)


def _decoy(raw: Any, path: str, pack: "FrozenPack") -> Decoy:
    if not isinstance(raw, dict):
        raise PlantError(path, "must be an object") from None
    if "class" not in raw:
        raise PlantError(f"{path}.class", "missing key") from None
    cls = raw["class"]
    if not isinstance(cls, str) or cls not in DECOY_CLASSES:
        raise PlantError(f"{path}.class", "unknown decoy class") from None
    raw = _closed(raw, path, DECOY_KEYS[cls])
    item_id, t = _common(raw, path, pack)
    if cls == "high_base_rate_everywhere":
        if not isinstance(raw["entity_ids"], list):
            raise PlantError(f"{path}.entity_ids", "must be a list of ids") from None
        ids = [(f"{path}.entity_ids[{j}]", _check_id(pack, t, v, f"{path}.entity_ids[{j}]"))
               for j, v in enumerate(raw["entity_ids"])]
    else:
        ids = [(f"{path}.entity_id", _check_id(pack, t, raw["entity_id"], f"{path}.entity_id"))]
    near_miss = None
    if cls == "near_miss_entity":
        near_miss = _check_id(pack, t, raw["near_miss_id"], f"{path}.near_miss_id")
    extra = {"echo_marked": ("copy_sites",), "cross_site_unmarked_copies": ("copy_sites",),
             "near_miss_entity": ("near_miss_sites",)}.get(cls, ())
    lists = _predicate_and_sites(raw, path, pack, extra)
    language = _language(raw, path, pack)
    _available(pack, path, t, ids + ([(f"{path}.near_miss_id", near_miss)] if near_miss is not None else []),
               raw["predicate"], language, DECOY_VISIBILITY)
    lo, hi = _SITE_COUNTS[cls]
    _site_count(lists["sites"], f"{path}.sites", lo, hi)
    for name in extra:
        if name == "copy_sites":
            _site_count(lists[name], f"{path}.{name}", 1, None)
        else:
            _site_count(lists[name], f"{path}.{name}", 1, 1)
        if set(lists[name]) & set(lists["sites"]):
            raise PlantError(f"{path}.{name}", "sites overlap") from None
    if cls == "high_base_rate_everywhere" and len(ids) < 2:
        raise PlantError(f"{path}.entity_ids", "too few entity ids") from None
    if near_miss is not None:
        a = ids[0][1]
        canon = Canonicaliser(pack)
        if a == near_miss:
            raise PlantError(f"{path}.near_miss_id", "near-miss ids are equal") from None
        if not 1 <= edit_distance(a, near_miss) <= 2:
            raise PlantError(f"{path}.near_miss_id", "not a near miss") from None
        for value in (a, near_miss):
            m = canon.resolve_exact(t, value)
            if m is None or m.entity_id != value:
                raise PlantError(f"{path}.near_miss_id", "near-miss ids resolve to one id") from None
    return Decoy(path=path, id=item_id, decoy_class=cls, entity_type=t, entity_ids=tuple(eid for _, eid in ids),
                 predicate=raw["predicate"], sites=lists["sites"], copy_sites=lists.get("copy_sites", ()),
                 near_miss_id=near_miss, near_miss_sites=lists.get("near_miss_sites", ()),
                 start_week=raw["start_week"], weeks=raw["weeks"], rate_per_week=raw["rate_per_week"],
                 language=language)


def parse_plant(raw: Any, pack: "FrozenPack") -> PlantSpec:
    top = _closed(raw, "$", TOP_KEYS)
    for key in TOP_KEYS:
        value = top[key]
        if not isinstance(value, _TOP_TYPES[key]) or (key == "schema_version" and isinstance(value, bool)):
            raise PlantError(f"$.{key}", "wrong type") from None
    if top["kind"] != KIND:
        raise PlantError("$.kind", f"must be {KIND}") from None
    if top["schema_version"] != SCHEMA_VERSION:
        raise PlantError("$.schema_version", f"must be {SCHEMA_VERSION}") from None
    if top["pack"] != pack.id:
        raise PlantError("$.pack", "pack mismatch") from None
    if top["prereg_sha256"] is not None and SHA256_RE.fullmatch(top["prereg_sha256"]) is None:
        raise PlantError("$.prereg_sha256", "must be null or 64 lowercase hex") from None
    planted_by = top["planted_by"]
    if not 1 <= len(planted_by) <= MAX_PLANTED_BY or not planted_by.isprintable():
        raise PlantError("$.planted_by", f"must be 1 to {MAX_PLANTED_BY} printable characters") from None
    if len(top["notes"]) > MAX_NOTES:
        raise PlantError("$.notes", f"must be at most {MAX_NOTES} characters") from None
    if not 1 <= len(top["patterns"]) <= MAX_ITEMS:
        raise PlantError("$.patterns", f"must hold 1 to {MAX_ITEMS} items") from None
    if len(top["decoys"]) > MAX_ITEMS:
        raise PlantError("$.decoys", f"must hold at most {MAX_ITEMS} items") from None
    patterns = tuple(_pattern(p, f"$.patterns[{i}]", pack) for i, p in enumerate(top["patterns"]))
    decoys = tuple(_decoy(d, f"$.decoys[{i}]", pack) for i, d in enumerate(top["decoys"]))
    ids: set[str] = set()
    keys: set[str] = set()
    for item in patterns + decoys:
        if item.id in ids:
            raise PlantError(f"{item.path}.id", "duplicate id") from None
        ids.add(item.id)
        for key in ((item.key,) if isinstance(item, Pattern) else item.keys):
            if key in keys:
                raise PlantError(item.path, "duplicate key") from None
            keys.add(key)
    return PlantSpec(raw=top, sha256=sha256_hex(canonical_bytes(top)), pack=top["pack"],
                     prereg_sha256=top["prereg_sha256"], planted_by=planted_by,
                     planter_saw_detector_code=top["planter_saw_detector_code"], notes=top["notes"],
                     patterns=patterns, decoys=decoys)


def load_plant(path: str | Path, pack: "FrozenPack") -> PlantSpec:
    data = None
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise PlantError("$", f"cannot read ({exc.__class__.__name__})") from None
    raw = failure = None
    try:
        raw = strict_load(data)
    except StrictJsonError as err:
        failure = (err.path, err.reason)
    if failure is not None:
        raise PlantError(*failure) from None
    return parse_plant(raw, pack)


def _counted_sites(item: Pattern | Decoy) -> list[tuple[str, str, str]]:
    """``(site list name, entity id, site)`` for every (id, site) whose planted records count in a cell."""
    if isinstance(item, Pattern):
        return [("sites", item.entity_id, s) for s in item.sites]
    out = []
    for _, eid, sites in item.counted():
        for s in sites:
            name = ("sites" if s in item.sites else "copy_sites" if s in item.copy_sites else "near_miss_sites")
            out.append((name, eid, s))
    return out


def check_plant(spec: PlantSpec, pack: "FrozenPack", *, site_ids: Sequence[str],
                master_data: Mapping[str, Mapping[str, Sequence[str]]], n_weeks: int, eval_from: int,
                eval_to: int) -> None:
    """The world checks of a parsed spec, item by item in list order; the first problem is raised."""
    detectors = pack.detectors
    for item in spec.items:
        path = item.path
        lists = [("sites", item.sites)]
        if isinstance(item, Decoy):
            lists += [("copy_sites", item.copy_sites), ("near_miss_sites", item.near_miss_sites)]
        for name, sites in lists:
            for j, s in enumerate(sites):
                if s not in site_ids:
                    raise PlantError(f"{path}.{name}[{j}]", "unknown site") from None
        if pack.egress.require_master_data and pack.entity_types[item.entity_type].id_format is not None:
            for name, eid, s in _counted_sites(item):
                if eid not in master_data.get(s, {}).get(item.entity_type, ()):
                    position = dict(lists)[name].index(s)
                    raise PlantError(f"{path}.{name}[{position}]", "id not in master data of a counted site") \
                        from None
        if item.start_week + item.weeks > n_weeks:
            raise PlantError(f"{path}.weeks", "outside the world weeks") from None
        cls = item.decoy_class if isinstance(item, Decoy) else None
        if cls == "stale_chain":
            if not 7 * (eval_from - item.end_week) > detectors["decoy"]["stale_days"]:
                raise PlantError(f"{path}.start_week", "not stale before the evaluation weeks") from None
        elif item.start_week < eval_from or item.end_week > eval_to:
            raise PlantError(f"{path}.start_week", "outside the evaluation weeks") from None
        if cls == "high_base_rate_everywhere" \
                and not len(item.sites) > detectors["decoy"]["base_rate_site_fraction"] * len(site_ids):
            raise PlantError(f"{path}.sites", "too few sites for a high base rate") from None
        if cls == "single_reporter" and item.rate_per_week < pack.egress.k:
            raise PlantError(f"{path}.rate_per_week", "rate below k") from None


# --------------------------------------------------------------------------------------------------- construction

class _Planter:
    def __init__(self, world: World, spec: PlantSpec, pack: "FrozenPack") -> None:
        self.pack = pack
        self.rng = random.Random(f"plant:{spec.sha256}:{world.params['seed']}")
        self.start = date.fromisoformat(world.params["start"])
        self.persons: dict[str, list[Mapping[str, Any]]] = {}
        reporters: dict[str, set[str]] = {}
        for r in world.records:
            if r["origin_ref"] is None:
                self.persons.setdefault(r["site"], []).append(r["persons"])
                if r["reporter"] is not None:
                    reporters.setdefault(r["site"], set()).add(r["reporter"])
        self.reporters = {s: sorted(reporters[s]) for s in sorted(reporters)}
        self.seen = {r["narrative"] for r in world.records}
        self.entity_types = tuple(sorted(pack.mapping()["entities"]))
        self.seq = 0
        self.records: list[dict[str, Any]] = []

    def _site(self, site: str) -> None:
        if site not in self.persons or not self.reporters.get(site):
            raise PlantError("$", "site has no background records") from None

    def _unique(self, path: str, draw: Any) -> str:
        for _ in range(NARRATIVE_ATTEMPTS):
            text = draw()
            if text not in self.seen:
                self.seen.add(text)
                return text
        raise PlantError(path, "narrative uniqueness exhausted") from None

    def narrative(self, item: Pattern | Decoy, entity_id: str, persons: Mapping[str, Any]) -> str:
        rng, pack = self.rng, self.pack
        filler = list(pack.generator["filler"][item.language])
        if item.visibility not in TEXT_VISIBILITIES:
            def filler_only() -> str:
                sentences = rng.sample(filler, rng.randint(1, 3))
                rng.shuffle(sentences)
                return " ".join(sentences)
            return self._unique(item.path, filler_only)
        templates = single_slot_templates(pack, item.language, item.predicate, item.entity_type)
        aliases = aliases_of(pack, item.entity_type, entity_id)
        alias_only = pack.entity_types[item.entity_type].id_format is None

        def one() -> str:
            template = rng.choice(templates)
            surface = rng.choice(aliases) if alias_only else entity_id

            def fill(m: re.Match[str]) -> str:
                name = m.group(1)
                return (persons.get(name[len(_PERSON):]) or "") if name.startswith(_PERSON) else surface

            sentences = [_SLOT.sub(fill, template)] + rng.sample(filler, rng.randint(1, 2))
            rng.shuffle(sentences)
            return " ".join(sentences)

        return self._unique(item.path, one)

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        record = {k: record[k] for k in sorted(record)}
        if record_problems(record, self.pack):
            raise PlantError("$", "planted record fails the record check") from None
        self.records.append(record)
        return record

    def ref(self, site: str) -> str:
        self.seq += 1
        return f"{site}-plant-{self.seq:06d}"

    def record(self, item: Pattern | Decoy, site: str, week: int, entity_id: str, *, reporter: str | None = None,
               narrative: str | None = None) -> dict[str, Any]:
        self._site(site)
        rng = self.rng
        received = (self.start + timedelta(days=7 * week + rng.randrange(7))).isoformat()
        persons = dict(rng.choice(self.persons[site]))
        if reporter is None:
            reporter = rng.choice(self.reporters[site])
        entities: dict[str, list[str]] = {t: [] for t in self.entity_types}
        codes: list[str] = []
        if item.visibility in CODE_VISIBILITIES:
            entities[item.entity_type] = [entity_id]
            codes = [rng.choice(specific_codes(self.pack, item.predicate))]
        if narrative is None:
            narrative = self.narrative(item, entity_id, persons)
        return self.add({"record_ref": self.ref(site), "site": site, "received_date": received,
                         "language": item.language, "codes": codes, "entities": entities, "narrative": narrative,
                         "persons": persons, "reporter": reporter, "origin_ref": None, "origin_site": None,
                         "synthetic": True})

    def copy(self, origin: Mapping[str, Any], site: str, *, marked: bool) -> dict[str, Any]:
        monday = date.fromisoformat(origin["received_date"])
        monday -= timedelta(days=monday.weekday())
        received = (monday + timedelta(days=self.rng.randrange(7))).isoformat()
        return self.add({"record_ref": self.ref(site), "site": site, "received_date": received,
                         "language": origin["language"], "codes": list(origin["codes"]),
                         "entities": {t: list(origin["entities"][t]) for t in self.entity_types},
                         "narrative": origin["narrative"], "persons": dict(origin["persons"]),
                         "reporter": origin["reporter"],
                         "origin_ref": origin["record_ref"] if marked else None,
                         "origin_site": origin["site"] if marked else None, "synthetic": True})


def plant(world: World, spec: PlantSpec, pack: "FrozenPack") -> Planted:
    """The planted records, in spec order (patterns, then decoys); within an item weeks ascending, then sites in the
    given order (entity ids first for a high base rate), then the week's records. The pipeline input is
    ``world.records + planted.records``."""
    p = _Planter(world, spec, pack)
    counts: dict[str, int] = {}
    for item in spec.items:
        before = len(p.records)
        cls = item.decoy_class if isinstance(item, Decoy) else None
        shared: str | None = None
        for week in range(item.start_week, item.end_week + 1):
            if isinstance(item, Pattern):
                for site in item.sites:
                    for _ in range(item.rate_per_week):
                        p.record(item, site, week, item.entity_id)
                continue
            if cls == "near_miss_entity":
                runs = [(item.entity_ids[0], item.sites[0]), (item.near_miss_id, item.near_miss_sites[0])]
            else:
                runs = [(eid, site) for eid in item.entity_ids for site in item.sites]
            for eid, site in runs:
                for _ in range(item.rate_per_week):
                    if cls == "single_reporter":
                        p._site(site)
                        origin = p.record(item, site, week, eid, reporter=p.reporters[site][0])
                    elif cls == "same_site_duplicates":
                        origin = p.record(item, site, week, eid, narrative=shared)
                        shared = origin["narrative"]
                    else:
                        origin = p.record(item, site, week, eid)
                    for copy_site in item.copy_sites:
                        p.copy(origin, copy_site, marked=cls == "echo_marked")
        counts[item.id] = len(p.records) - before
    return Planted(records=tuple(p.records), counts=MappingProxyType(counts))


# --------------------------------------------------------------------------------------------------- labels

def labels_doc(spec: PlantSpec, pack: "FrozenPack", *, weeks: Sequence[str], eval_from: int, eval_to: int,
               grace_weeks: int) -> dict[str, Any]:
    """The seed-independent labels; ``seeds`` is left empty for the harness to fill with the planted record counts."""
    window = pack.detectors["window_weeks"]
    patterns = []
    for p in spec.patterns:
        found_to = min(p.end_week + grace_weeks, eval_to)
        patterns.append({"id": p.id, "key": p.key, "entity_type": p.entity_type, "entity_id": p.entity_id,
                         "predicate": p.predicate, "sites": list(p.sites), "visibility": p.visibility,
                         "language": p.language, "rate_per_week": p.rate_per_week, "start_index": p.start_week,
                         "end_index": p.end_week, "start_week": weeks[p.start_week], "end_week": weeks[p.end_week],
                         "found_from": weeks[p.start_week], "found_to": weeks[found_to]})
    decoys = []
    for d in spec.decoys:
        cls = d.decoy_class
        if cls == "stale_chain":
            watch = (eval_from, min(max(eval_from, d.end_week + window - 1) + grace_weeks, eval_to))
            quiet: tuple[int, int] | None = (d.end_week + 1, watch[1])
            rule: str | None = "all_sites_zero"
        else:
            watch = (d.start_week, min(d.end_week + grace_weeks, eval_to))
            quiet, rule = None, None
            if cls in STRUCTURAL_CLASSES:
                quiet, rule = (max(0, d.start_week - window + 1), watch[1]), "other_sites_at_most_one"
        counted: list[str] = []
        for _, _, sites in d.counted():
            counted += [s for s in sites if s not in counted]
        decoys.append({"id": d.id, "class": cls, "keys": list(d.keys), "entity_type": d.entity_type,
                       "predicate": d.predicate, "sites": counted, "planted_sites": list(d.planted_sites),
                       "start_index": d.start_week, "end_index": d.end_week, "watch_from": weeks[watch[0]],
                       "watch_to": weeks[watch[1]], "quiet_from": weeks[quiet[0]] if quiet else None,
                       "quiet_to": weeks[quiet[1]] if quiet else None, "quiet_rule": rule})
    return {"kind": LABELS_KIND, "schema_version": SCHEMA_VERSION, "pack": spec.pack, "plant_sha256": spec.sha256,
            "patterns": patterns, "decoys": decoys, "seeds": []}
