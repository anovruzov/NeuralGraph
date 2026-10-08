"""The demo scenario (G8): a fictional multi-site company, one hero case, a sibling and decoys, planted into a synthetic
world built from a domain pack. Generic: every domain value comes from ``scenario.json`` or the pack.

**Fictional and synthetic.** The company, its sites, records, people and ids are invented; the world is the pack's
seeded generator plus the records written here. Nothing built here is a measurement.

The file is strict JSON, every object closed and every key required::

    {"kind": "collective_demo_scenario", "schema_version": 1, "pack": "<pack id>", "seed": <int>, "weeks": <int>,
     "company": "<name containing (fictional)>", "illustration": "<text without digits>", "tie_salt": "<salt>",
     "org": <an org config, detect/org.py>, "master_data_additions": [{"site", "entity_type", "ids"}],
     "approvers": <an approvers file, followup/policy.py>, "followups": [{"type", "args"}], "items": [<item>]}

An item is ``{id, role (hero | sibling | decoy), label, key {entity_type, entity_id, predicate}, visibility,
start_week, weeks, sites [{site, language, rate_per_week}], codes, structured [{entity_type, ids, fill_rate}],
narratives {language: [template]}, slots {name: text | {language: text}}, filler [lo, hi], reporter (pool | single),
copies [{to_site, marked}]}``. ``start_week`` is a 0-based index into the world's weeks.

Rules:

* **Validation** (:func:`parse_scenario`) stops at the first problem, in a fixed order: the closed top level, ``kind``
  and ``schema_version``, the pack, ``seed``, ``weeks``, ``company``, ``illustration``, ``org`` (at least 5 sites in at
  least 2 countries, whose ids are the first site ids of the pack's world generator, in order),
  ``master_data_additions``, ``approvers``, ``followups``, then each item and the roles across items.
  :class:`ScenarioError` names a JSON path and a fixed problem, never a value.
* **Construction** (:func:`build_world`): the pack's generated world, then each item's records (weeks, then sites in
  the given order, then ``rate_per_week`` records), each from ``random.Random("scenario:<seed>:<item id>")`` only:
  a received date in the week, persons copied from one of the site's background records, the reporter (a seeded pick
  from the site's reporters, or the site's first reporter for ``single``), the codes as given, each structured field
  by a seeded Bernoulli draw (ids cycled over the item's records), and a narrative: one template filled from the
  slots, then seeded filler sentences of the pack, unique in the world. Copies keep the narrative, persons,
  reporter, codes and entities in the same ISO week; marked copies name their origin. Then the master-data
  additions and the canaries (``leakage.plant_canaries``), then the checks that each item is what it says it is.
* **Keys.** An item's keys are its key and, for a structured field of the key's entity type, each listed id with the
  key's predicate (a decoy spread over several ids). The hero's **case keys** are its case entities (the key's
  entity, every structured entity of a hero record and every entity the lexical extractor finds in a hero narrative)
  times its case predicates (the key's and those of every code on a hero record), egress types only; they are
  computed before the canaries are planted, so no canary id can become a case key.

**Codes-miss scenarios** (B1b, pre-registered in ``docs/collective/b1/PREREG.md``) add an optional top-level
``codes_miss`` block ``{case_kind, statement, author_note, robustness_seeds, shifts [{id, label, sites, start_week,
weeks, to_code}]}``. A shift is a background change: every non-copy record at a listed site in a covered week,
background and scenario items alike, gets exactly ``[to_code]``, and every copy then takes its origin's codes. Parsing
checks the realism constraints R1, R2, R4, R5, R6 and R8; building checks R3 and R7 (the PREREG's section 4).

Deterministic: ``build_world`` gives the same world under any ``PYTHONHASHSEED`` (every iteration is ordered).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from mycelic.collective import schemacheck
from mycelic.collective.detect.org import OrgConfig, OrgError, parse_org
from mycelic.collective.detect.rules import series_key
from mycelic.collective.edge.extract import LexicalExtractor, codes_channel, pair
from mycelic.collective.edge.weeks import iso_week
from mycelic.collective.followup.policy import ApproversError, parse_approvers
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.leakage import Manifest, plant_canaries
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.connector import record_problems
from mycelic.collective.packs.generator import GeneratorError, generate
from mycelic.collective.packs.loader import FrozenPack, PackError, load_pack

SCHEMA_VERSION = 1
KIND = "collective_demo_scenario"
DEFAULT = Path(__file__).resolve().parent / "scenario.json"
TOP_KEYS = ("kind", "schema_version", "pack", "seed", "weeks", "company", "illustration", "tie_salt", "org",
            "master_data_additions", "approvers", "followups", "items")
OPTIONAL_TOP_KEYS = ("codes_miss",)
CODES_MISS_KEYS = ("case_kind", "statement", "author_note", "robustness_seeds", "shifts")
SHIFT_KEYS = ("id", "label", "sites", "start_week", "weeks", "to_code")
CASE_KIND = "codes_miss"
# the PREREG's section 1, verbatim: every screen, run file and document that shows the scenario carries both
STATEMENT = ("Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 "
             "and the Phase-1 signal audit measure.")
AUTHOR_NOTE = ("Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism "
               "is possible, not that it is common.")
ROBUSTNESS_SEEDS = 8
MAX_HERO_RATE = 2
MIN_HERO_TEMPLATES = 2
ITEM_KEYS = ("id", "role", "label", "key", "visibility", "start_week", "weeks", "sites", "codes", "structured",
             "narratives", "slots", "filler", "reporter", "copies")
ROLES = ("hero", "sibling", "decoy")
VISIBILITIES = ("narrative_only", "codes_only", "both")
HERO_VISIBILITIES = ("narrative_only", "both")
SIBLING_VISIBILITY = "none"
REPORTERS = ("pool", "single")
TEXT_VISIBILITIES = ("narrative_only", "both")
CODE_VISIBILITIES = ("codes_only", "both")
FICTIONAL_MARK = "(fictional)"
MAX_SEED = 10 ** 9
MAX_WEEKS = 104
MAX_COMPANY = 80
MAX_TEXT = 400
MAX_RATE = 10
MAX_FILLER = 3
MIN_SITES = 5
MIN_COUNTRIES = 2
MIN_HERO_SITES = 3
MIN_HERO_LANGUAGES = 2
MIN_DECOYS = 3
NARRATIVE_ATTEMPTS = 20
PLACEHOLDER_CONCLUSION = "c-" + "0" * 32
ITEM_ID_RE = re.compile(r"[a-z][a-z0-9-]{0,39}", re.ASCII)
SALT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)
_SLOT = re.compile(r"\{([^{}]*)\}")
_DIGIT = re.compile(r"[0-9]")


class ScenarioError(ValueError):
    """``str`` is ``scenario: <path>: <problem>``; the path is built from key names and list indices only."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"scenario: {path}: {problem}")
        self.path = path
        self.problem = problem


@dataclass(frozen=True)
class Key:
    entity_type: str
    entity_id: str
    predicate: str | None


@dataclass(frozen=True)
class Item:
    path: str
    id: str
    role: str
    label: str
    key: Key
    visibility: str
    start_week: int
    weeks: int
    sites: tuple[tuple[str, str, int], ...]
    codes: tuple[str, ...]
    structured: tuple[tuple[str, tuple[str, ...], float], ...]
    narratives: Mapping[str, tuple[str, ...]]
    slots: Mapping[str, Any]
    filler: tuple[int, int]
    reporter: str
    copies: tuple[tuple[str, bool], ...]

    @property
    def keys(self) -> tuple[str, ...]:
        """The key and, for a structured field of the key's type, each of its ids with the key's predicate."""
        k = self.key
        if k.predicate is None:
            return ()
        ids = {k.entity_id}
        for t, values, _ in self.structured:
            if t == k.entity_type:
                ids.update(values)
        return tuple(series_key(k.entity_type, i, k.predicate) for i in sorted(ids))

    @property
    def site_ids(self) -> tuple[str, ...]:
        return tuple(s for s, _, _ in self.sites)


@dataclass(frozen=True)
class Shift:
    id: str
    label: str
    sites: tuple[str, ...]
    start_week: int
    weeks: int
    to_code: str

    def covers(self, site: str, week: int) -> bool:
        return site in self.sites and self.start_week <= week < self.start_week + self.weeks


@dataclass(frozen=True)
class CodesMiss:
    statement: str
    author_note: str
    robustness_seeds: tuple[int, ...]
    shifts: tuple[Shift, ...]


@dataclass(frozen=True)
class Scenario:
    raw: Mapping[str, Any]
    digest: str
    pack: FrozenPack
    org: OrgConfig
    seed: int
    weeks: int
    company: str
    illustration: str
    tie_salt: str
    approvers: Mapping[str, Any]
    followups: tuple[Mapping[str, Any], ...]
    master_data_additions: tuple[tuple[str, str, tuple[str, ...]], ...]
    items: tuple[Item, ...]
    hero: Item
    codes_miss: CodesMiss | None = None


@dataclass(frozen=True)
class DemoWorld:
    records: tuple[dict[str, Any], ...]
    master_data: Mapping[str, Mapping[str, tuple[str, ...]]]
    weeks: tuple[str, ...]
    item_records: Mapping[str, tuple[str, ...]]
    narratives: tuple[str, ...]
    manifest: Manifest
    case_keys: tuple[str, ...]
    by_construction: Mapping[str, Any]
    structured_fill: tuple[Mapping[str, Any], ...]


# --------------------------------------------------------------------------------------------------- parsing

def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _closed(raw: Any, path: str, keys: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ScenarioError(path, "must be an object") from None
    if any(key not in keys and key not in optional for key in raw):
        raise ScenarioError(path, "unknown key") from None
    for key in keys:
        if key not in raw:
            raise ScenarioError(f"{path}.{key}", "missing key") from None
    return raw


def _text(value: Any, path: str, limit: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or not value.isprintable():
        raise ScenarioError(path, f"must be 1 to {limit} printable characters") from None
    return value


def _canonical(pack: FrozenPack, entity_type: str, value: Any) -> bool:
    et = pack.entity_types[entity_type]
    if et.id_format is None:
        return isinstance(value, str) and et.ids is not None and value in et.ids
    return et.id_format.is_canonical(value)


def _id_list(pack: FrozenPack, entity_type: str, value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ScenarioError(path, "must be a non-empty list of ids") from None
    for j, v in enumerate(value):
        if not _canonical(pack, entity_type, v):
            raise ScenarioError(f"{path}[{j}]", "id not canonical") from None
        if v in value[:j]:
            raise ScenarioError(f"{path}[{j}]", "duplicate id") from None
    return tuple(value)


def _org(raw: Any, pack: FrozenPack) -> OrgConfig:
    org = None
    try:
        org = parse_org(raw)
    except OrgError as err:
        raise ScenarioError("$.org" + err.path[1:], err.problem) from None
    sites = [s["site_id"] for s in raw["sites"]]
    if len(sites) < MIN_SITES:
        raise ScenarioError("$.org.sites", f"needs at least {MIN_SITES} sites") from None
    if len({s["country"] for s in raw["sites"]}) < MIN_COUNTRIES:
        raise ScenarioError("$.org.sites", f"needs sites in at least {MIN_COUNTRIES} countries") from None
    generated = [s["id"] for s in pack.generator["sites"]]
    if sites != generated[:len(sites)]:
        raise ScenarioError("$.org.sites", "site ids must be the pack generator's first site ids, in order") from None
    return org


def _additions(raw: Any, pack: FrozenPack, org: OrgConfig) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if not isinstance(raw, list):
        raise ScenarioError("$.master_data_additions", "must be a list") from None
    out = []
    for i, entry in enumerate(raw):
        path = f"$.master_data_additions[{i}]"
        entry = _closed(entry, path, ("site", "entity_type", "ids"))
        if entry["site"] not in org.sites:
            raise ScenarioError(f"{path}.site", "unknown site") from None
        t = entry["entity_type"]
        if not isinstance(t, str) or t not in pack.egress.egress_entity_types \
                or pack.entity_types[t].id_format is None:
            raise ScenarioError(f"{path}.entity_type", "not an egress entity type with an id format") from None
        out.append((entry["site"], t, _id_list(pack, t, entry["ids"], f"{path}.ids")))
    return tuple(out)


def _followups(raw: Any, pack: FrozenPack) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list) or not raw:
        raise ScenarioError("$.followups", "must be a non-empty list") from None
    out = []
    for i, entry in enumerate(raw):
        path = f"$.followups[{i}]"
        entry = _closed(entry, path, ("type", "args"))
        ft = pack.followups.get(entry["type"]) if isinstance(entry["type"], str) else None
        if ft is None or not ft.enabled or ft.tier not in ("T0", "T1"):
            raise ScenarioError(f"{path}.type", "not an enabled T0 or T1 follow-up type of the pack") from None
        args = entry["args"]
        if not isinstance(args, dict):
            raise ScenarioError(f"{path}.args", "must be an object") from None
        filled = dict(args)
        for name in sorted(ft.args):
            if ft.args[name]["kind"] == "conclusion_id":
                if name in args:
                    raise ScenarioError(f"{path}.args", "names a conclusion arg; the engine fills it") from None
                filled[name] = PLACEHOLDER_CONCLUSION
        if schemacheck.compile(ft.args_json_schema()).validate(filled):
            raise ScenarioError(f"{path}.args", "does not pass the type's args schema") from None
        out.append(MappingProxyType({"type": entry["type"], "args": MappingProxyType(dict(args))}))
    return tuple(out)


def _key(raw: Any, path: str, pack: FrozenPack, role: str) -> Key:
    key = _closed(raw, path, ("entity_type", "entity_id", "predicate"))
    t = key["entity_type"]
    if not isinstance(t, str) or t not in pack.egress.egress_entity_types:
        raise ScenarioError(f"{path}.entity_type", "not an egress entity type") from None
    if not _canonical(pack, t, key["entity_id"]):
        raise ScenarioError(f"{path}.entity_id", "id not canonical") from None
    predicate = key["predicate"]
    if role == "sibling":
        if predicate is not None:
            raise ScenarioError(f"{path}.predicate", "a sibling's key has no predicate") from None
    elif not isinstance(predicate, str) or predicate not in pack.predicates:
        raise ScenarioError(f"{path}.predicate", "unknown predicate") from None
    return Key(t, key["entity_id"], predicate)


def _slots_of(template: str) -> list[str] | None:
    """The placeholder names of a template, or None when a brace is unbalanced."""
    names = _SLOT.findall(template)
    rest = _SLOT.sub("", template)
    return None if "{" in rest or "}" in rest else names


def _item(raw: Any, path: str, pack: FrozenPack, org: OrgConfig, world_weeks: int) -> Item:
    item = _closed(raw, path, ITEM_KEYS)
    if not isinstance(item["id"], str) or ITEM_ID_RE.fullmatch(item["id"]) is None:
        raise ScenarioError(f"{path}.id", "must be a slug [a-z][a-z0-9-]{0,39}") from None
    role = item["role"]
    if role not in ROLES:
        raise ScenarioError(f"{path}.role", "must be hero, sibling or decoy") from None
    label = _text(item["label"], f"{path}.label", MAX_TEXT)
    key = _key(item["key"], f"{path}.key", pack, role)
    visibility = item["visibility"]
    allowed = (SIBLING_VISIBILITY,) if role == "sibling" else HERO_VISIBILITIES if role == "hero" else VISIBILITIES
    if visibility not in allowed:
        raise ScenarioError(f"{path}.visibility", "not allowed for the role") from None
    start, weeks = item["start_week"], item["weeks"]
    if not _int(start) or start < 0 or not _int(weeks) or weeks < 1 or start + weeks > world_weeks:
        raise ScenarioError(f"{path}.weeks", "start_week + weeks must lie inside the world's weeks") from None
    sites_raw = item["sites"]
    if not isinstance(sites_raw, list) or not sites_raw:
        raise ScenarioError(f"{path}.sites", "must be a non-empty list") from None
    sites = []
    for j, s in enumerate(sites_raw):
        spath = f"{path}.sites[{j}]"
        s = _closed(s, spath, ("site", "language", "rate_per_week"))
        if s["site"] not in org.sites:
            raise ScenarioError(f"{spath}.site", "unknown site") from None
        if s["site"] in [x[0] for x in sites]:
            raise ScenarioError(f"{spath}.site", "duplicate site") from None
        if s["language"] not in pack.languages:
            raise ScenarioError(f"{spath}.language", "not a pack language") from None
        if not _int(s["rate_per_week"]) or not 1 <= s["rate_per_week"] <= MAX_RATE:
            raise ScenarioError(f"{spath}.rate_per_week", f"must be an int 1..{MAX_RATE}") from None
        sites.append((s["site"], s["language"], s["rate_per_week"]))
    codes = item["codes"]
    if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes) or len(set(codes)) != len(codes):
        raise ScenarioError(f"{path}.codes", "must be a list of unique codes") from None
    for j, c in enumerate(codes):
        if c not in pack.codes:
            raise ScenarioError(f"{path}.codes[{j}]", "not a pack code") from None
        if role != "sibling" and visibility == "narrative_only" and pack.codes[c].predicate == key.predicate:
            raise ScenarioError(f"{path}.codes[{j}]", "codes the key's predicate, so the key is not narrative-only") \
                from None
    structured_raw = item["structured"]
    if not isinstance(structured_raw, list):
        raise ScenarioError(f"{path}.structured", "must be a list") from None
    mapping_types = pack.mapping()["entities"]
    structured = []
    for j, s in enumerate(structured_raw):
        spath = f"{path}.structured[{j}]"
        s = _closed(s, spath, ("entity_type", "ids", "fill_rate"))
        if s["entity_type"] not in mapping_types or s["entity_type"] in [x[0] for x in structured]:
            raise ScenarioError(f"{spath}.entity_type", "not a structured entity type of the pack, or repeated") \
                from None
        ids = _id_list(pack, s["entity_type"], s["ids"], f"{spath}.ids")
        rate = s["fill_rate"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            raise ScenarioError(f"{spath}.fill_rate", "must be a number in [0, 1]") from None
        structured.append((s["entity_type"], ids, float(rate)))
    slots = item["slots"]
    if not isinstance(slots, dict):
        raise ScenarioError(f"{path}.slots", "must be an object") from None
    languages = sorted({lang for _, lang, _ in sites})
    for name in sorted(slots):
        value = slots[name]
        ok = isinstance(value, str) or (isinstance(value, dict) and all(isinstance(v, str) for v in value.values())
                                        and all(lang in value for lang in languages))
        if not ok:
            raise ScenarioError(f"{path}.slots", "a slot is text, or text for every language the item uses") \
                from None
    narratives = item["narratives"]
    if not isinstance(narratives, dict):
        raise ScenarioError(f"{path}.narratives", "must be an object") from None
    for lang in languages:
        templates = narratives.get(lang)
        if not isinstance(templates, list) or not templates or not all(isinstance(t, str) and t for t in templates):
            raise ScenarioError(f"{path}.narratives", "needs a list of templates for every language used") from None
        for j, template in enumerate(templates):
            names = _slots_of(template)
            if names is None or any(n not in slots for n in names):
                raise ScenarioError(f"{path}.narratives.{lang}[{j}]", "names a slot the item does not define") \
                    from None
    filler = item["filler"]
    if not (isinstance(filler, list) and len(filler) == 2 and all(_int(x) for x in filler)
            and 0 <= filler[0] <= filler[1] <= MAX_FILLER):
        raise ScenarioError(f"{path}.filler", f"must be [lo, hi] with 0 <= lo <= hi <= {MAX_FILLER}") from None
    for lang in languages:
        if len(pack.generator["filler"].get(lang, ())) < filler[1]:
            raise ScenarioError(f"{path}.filler", "the pack has too few filler sentences for a language") from None
    if item["reporter"] not in REPORTERS:
        raise ScenarioError(f"{path}.reporter", "must be pool or single") from None
    copies_raw = item["copies"]
    if not isinstance(copies_raw, list):
        raise ScenarioError(f"{path}.copies", "must be a list") from None
    copies = []
    for j, c in enumerate(copies_raw):
        cpath = f"{path}.copies[{j}]"
        c = _closed(c, cpath, ("to_site", "marked"))
        if c["to_site"] not in org.sites or c["to_site"] in [s for s, _, _ in sites] \
                or c["to_site"] in [x[0] for x in copies]:
            raise ScenarioError(f"{cpath}.to_site", "must be a known site outside the item's sites") from None
        if not isinstance(c["marked"], bool):
            raise ScenarioError(f"{cpath}.marked", "must be a boolean") from None
        copies.append((c["to_site"], c["marked"]))
    if role == "hero":
        if len(sites) < MIN_HERO_SITES or len(languages) < MIN_HERO_LANGUAGES:
            raise ScenarioError(f"{path}.sites", f"the hero needs at least {MIN_HERO_SITES} sites and "
                                                 f"{MIN_HERO_LANGUAGES} languages") from None
    return Item(path=path, id=item["id"], role=role, label=label, key=key, visibility=visibility, start_week=start,
                weeks=weeks, sites=tuple(sites), codes=tuple(codes), structured=tuple(structured),
                narratives=MappingProxyType({lang: tuple(narratives[lang]) for lang in languages}),
                slots=MappingProxyType(dict(slots)), filler=(filler[0], filler[1]), reporter=item["reporter"],
                copies=tuple(copies))


def _shift(raw: Any, path: str, pack: FrozenPack, org: OrgConfig, world_weeks: int) -> Shift:
    s = _closed(raw, path, SHIFT_KEYS)
    if not isinstance(s["id"], str) or ITEM_ID_RE.fullmatch(s["id"]) is None:
        raise ScenarioError(f"{path}.id", "must be a slug [a-z][a-z0-9-]{0,39}") from None
    label = _text(s["label"], f"{path}.label", MAX_TEXT)
    sites = s["sites"]
    if not isinstance(sites, list) or not sites or len(set(sites)) != len(sites) \
            or not all(isinstance(x, str) and x in org.sites for x in sites):
        raise ScenarioError(f"{path}.sites", "must be a non-empty list of distinct known sites") from None
    start, weeks = s["start_week"], s["weeks"]
    if not _int(start) or start < 0 or not _int(weeks) or weeks < 1 or start + weeks > world_weeks:
        raise ScenarioError(f"{path}.weeks", "start_week + weeks must lie inside the world's weeks") from None
    if start + weeks != world_weeks:
        raise ScenarioError(f"{path}.weeks", "R1: a shift runs to the world's last week") from None
    if s["to_code"] not in pack.codes:
        raise ScenarioError(f"{path}.to_code", "not a pack code") from None
    return Shift(id=s["id"], label=label, sites=tuple(sites), start_week=start, weeks=weeks, to_code=s["to_code"])


def _hero_realism(hero: Item, pack: FrozenPack, path: str) -> None:
    """R4: exactly one structured entry for each type the generator fills and the central fields allow, at the
    generator's fill rate, each one universe id that follows the generator's links."""
    gen = pack.generator
    allowed = set(pack.egress.central_allowed_fields)
    want = sorted(t for t in gen["fill_rates"] if f"entities.{t}" in allowed)
    got = {t: (ids, rate) for t, ids, rate in hero.structured}
    if sorted(got) != want:
        raise ScenarioError(f"{path}.structured", "R4: one entry for each type the generator fills and the central "
                                                  "fields allow") from None
    for t in want:
        ids, rate = got[t]
        if len(ids) != 1 or ids[0] not in gen["universe"][t] or rate != float(gen["fill_rates"][t]):
            raise ScenarioError(f"{path}.structured", "R4: one universe id at the generator's fill rate") from None
    for t, link in sorted(gen["links"].items()):
        parent = link["parent"]
        if t in got and parent in got and got[t][0][0] not in link["map"].get(got[parent][0][0], ()):
            raise ScenarioError(f"{path}.structured", "R4: the ids follow the generator's links") from None


def _codes_miss(raw: Any, pack: FrozenPack, org: OrgConfig, hero: Item, seed: int, world_weeks: int) -> CodesMiss:
    path = "$.codes_miss"
    block = _closed(raw, path, CODES_MISS_KEYS)
    if block["case_kind"] != CASE_KIND:
        raise ScenarioError(f"{path}.case_kind", f"must be {CASE_KIND}") from None
    if block["statement"] != STATEMENT:
        raise ScenarioError(f"{path}.statement", "must be the pre-registered statement, verbatim") from None
    if block["author_note"] != AUTHOR_NOTE:
        raise ScenarioError(f"{path}.author_note", "must be the pre-registered author note, verbatim") from None
    seeds = block["robustness_seeds"]
    if not (isinstance(seeds, list) and len(seeds) == ROBUSTNESS_SEEDS and all(_int(x) for x in seeds)
            and len(set(seeds)) == ROBUSTNESS_SEEDS and seed not in seeds
            and all(0 <= x <= MAX_SEED for x in seeds)):
        raise ScenarioError(f"{path}.robustness_seeds",
                            f"R6: exactly {ROBUSTNESS_SEEDS} distinct seeds other than the main seed") from None
    if not isinstance(block["shifts"], list) or not block["shifts"]:
        raise ScenarioError(f"{path}.shifts", "R1: at least one shift") from None
    shifts = tuple(_shift(x, f"{path}.shifts[{i}]", pack, org, world_weeks) for i, x in enumerate(block["shifts"]))
    if len({x.id for x in shifts}) != len(shifts):
        raise ScenarioError(f"{path}.shifts", "duplicate shift id") from None
    window = pack.detectors["window_weeks"]
    for i, x in enumerate(shifts):
        if x.start_week != hero.start_week - window:
            raise ScenarioError(f"{path}.shifts[{i}].start_week", "R8: a shift starts at the hero's start week minus "
                                                                  "the pack's window_weeks") from None
    hpath = hero.path
    if hero.visibility != "narrative_only":
        raise ScenarioError(f"{hpath}.visibility", "R2: the hero is narrative-only") from None
    for site, _, rate in hero.sites:
        for week in range(hero.start_week, hero.start_week + hero.weeks):
            covering = [x for x in shifts if x.covers(site, week)]
            if not covering:
                raise ScenarioError(f"{path}.shifts", "R1: a shift covers every hero site-week") from None
            if any(hero.codes != (x.to_code,) for x in covering):
                raise ScenarioError(f"{hpath}.codes", "R2: the hero carries exactly the code the shift writes") \
                    from None
        if rate > MAX_HERO_RATE:
            raise ScenarioError(f"{hpath}.sites", f"R5: at most {MAX_HERO_RATE} hero records per site and week") \
                from None
    for lang, templates in sorted(hero.narratives.items()):
        if len(templates) < MIN_HERO_TEMPLATES:
            raise ScenarioError(f"{hpath}.narratives.{lang}", f"R5: at least {MIN_HERO_TEMPLATES} templates per "
                                                              "hero language") from None
    _hero_realism(hero, pack, hpath)
    return CodesMiss(statement=STATEMENT, author_note=AUTHOR_NOTE, robustness_seeds=tuple(seeds), shifts=shifts)


def parse_scenario(raw: Any, *, digest: str) -> Scenario:
    top = _closed(raw, "$", TOP_KEYS, OPTIONAL_TOP_KEYS)
    if top["kind"] != KIND:
        raise ScenarioError("$.kind", f"must be {KIND}") from None
    if not _int(top["schema_version"]) or top["schema_version"] != SCHEMA_VERSION:
        raise ScenarioError("$.schema_version", f"must be {SCHEMA_VERSION}") from None
    pack = None
    try:
        pack = load_pack(top["pack"]) if isinstance(top["pack"], str) else None
    except PackError:
        pack = None
    if pack is None:
        raise ScenarioError("$.pack", "the pack does not load") from None
    if not _int(top["seed"]) or not 0 <= top["seed"] <= MAX_SEED:
        raise ScenarioError("$.seed", f"must be an int in [0, {MAX_SEED}]") from None
    need = pack.detectors["baseline_weeks"] + pack.detectors["window_weeks"]
    if not _int(top["weeks"]) or not need <= top["weeks"] <= MAX_WEEKS:
        raise ScenarioError("$.weeks", f"must be an int in [{need}, {MAX_WEEKS}]") from None
    company = _text(top["company"], "$.company", MAX_COMPANY)
    if FICTIONAL_MARK not in company:
        raise ScenarioError("$.company", f"must say {FICTIONAL_MARK}") from None
    illustration = _text(top["illustration"], "$.illustration", MAX_TEXT)
    if _DIGIT.search(illustration) is not None:
        raise ScenarioError("$.illustration", "must hold no digit") from None
    if not isinstance(top["tie_salt"], str) or SALT_RE.fullmatch(top["tie_salt"]) is None:
        raise ScenarioError("$.tie_salt", "must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}") from None
    org = _org(top["org"], pack)
    additions = _additions(top["master_data_additions"], pack, org)
    try:
        parse_approvers(top["approvers"], org, pack)
    except ApproversError as err:
        raise ScenarioError("$.approvers" + err.path[1:], err.problem) from None
    followups = _followups(top["followups"], pack)
    if not isinstance(top["items"], list):
        raise ScenarioError("$.items", "must be a list") from None
    items = tuple(_item(raw_item, f"$.items[{i}]", pack, org, top["weeks"]) for i, raw_item in enumerate(top["items"]))
    seen: list[str] = []
    for item in items:
        if item.id in seen:
            raise ScenarioError(f"{item.path}.id", "duplicate id") from None
        seen.append(item.id)
    heroes = [i for i in items if i.role == "hero"]
    siblings = [i for i in items if i.role == "sibling"]
    if len(heroes) != 1:
        raise ScenarioError("$.items", "needs exactly one hero") from None
    if not siblings:
        raise ScenarioError("$.items", "needs at least one sibling") from None
    if sum(1 for i in items if i.role == "decoy") < MIN_DECOYS:
        raise ScenarioError("$.items", f"needs at least {MIN_DECOYS} decoys") from None
    hero = heroes[0]
    codes_miss = _codes_miss(top["codes_miss"], pack, org, hero, top["seed"], top["weeks"]) \
        if "codes_miss" in top else None
    for s in siblings:
        if (s.key.entity_type, s.key.entity_id) != (hero.key.entity_type, hero.key.entity_id):
            raise ScenarioError(f"{s.path}.key", "a sibling holds the hero's entity") from None
        if set(s.site_ids) & set(hero.site_ids):
            raise ScenarioError(f"{s.path}.sites", "a sibling's sites are not the hero's") from None
    return Scenario(raw=MappingProxyType(dict(top)), digest=digest, pack=pack, org=org, seed=top["seed"],
                    weeks=top["weeks"], company=company, illustration=illustration, tie_salt=top["tie_salt"],
                    approvers=MappingProxyType(dict(top["approvers"])), followups=followups,
                    master_data_additions=additions, items=items, hero=hero, codes_miss=codes_miss)


def load_scenario(path: str | Path = DEFAULT) -> Scenario:
    data = None
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ScenarioError("$", f"cannot read ({exc.__class__.__name__})") from None
    raw = failure = None
    try:
        raw = strict_load(data)
    except StrictJsonError as err:
        failure = err.reason
    if failure is not None:
        raise ScenarioError("$", f"not strict JSON ({failure})") from None
    return parse_scenario(raw, digest=sha256_hex(data)[:32])


# --------------------------------------------------------------------------------------------------- construction

def _fill(template: str, slots: Mapping[str, Any], language: str) -> str:
    def one(m: re.Match[str]) -> str:
        value = slots[m.group(1)]
        return value if isinstance(value, str) else value[language]
    return _SLOT.sub(one, template)


class _Builder:
    def __init__(self, scenario: Scenario, background: tuple[dict[str, Any], ...]) -> None:
        self.scenario = scenario
        self.pack = scenario.pack
        self.start = date.fromisoformat(scenario.pack.generator["start"])
        persons: dict[str, list[Mapping[str, Any]]] = {}
        reporters: dict[str, set[str]] = {}
        for r in background:
            if r["origin_ref"] is None:
                persons.setdefault(r["site"], []).append(r["persons"])
                if r["reporter"] is not None:
                    reporters.setdefault(r["site"], set()).add(r["reporter"])
        self.persons = persons
        self.reporters = {s: sorted(reporters[s]) for s in sorted(reporters)}
        self.seen = {r["narrative"] for r in background}
        self.types = tuple(sorted(self.pack.mapping()["entities"]))
        self.seq = 0
        self.records: list[dict[str, Any]] = []
        self.copy_of: dict[str, str] = {}             # every scenario copy, marked or not, to its origin's ref

    def _ref(self, site: str) -> str:
        self.seq += 1
        return f"{site}-scn-{self.seq:06d}"

    def _add(self, record: dict[str, Any], path: str) -> dict[str, Any]:
        record = {k: record[k] for k in sorted(record)}
        if record_problems(record, self.pack):
            raise ScenarioError(path, "a scenario record fails the record check") from None
        self.records.append(record)
        return record

    def _narrative(self, item: Item, language: str, rng: random.Random) -> str:
        filler = list(self.pack.generator["filler"][language])
        for _ in range(NARRATIVE_ATTEMPTS):
            text = _fill(rng.choice(item.narratives[language]), item.slots, language)
            sentences = [text] + rng.sample(filler, rng.randint(*item.filler))
            narrative = " ".join(sentences)
            if narrative not in self.seen:
                self.seen.add(narrative)
                return narrative
        raise ScenarioError(item.path, "narrative uniqueness exhausted") from None

    def item(self, item: Item) -> list[str]:
        rng = random.Random(f"scenario:{self.scenario.seed}:{item.id}")
        refs: list[str] = []
        index = 0
        for week in range(item.start_week, item.start_week + item.weeks):
            monday = self.start + timedelta(days=7 * week)
            for site, language, rate in item.sites:
                if site not in self.persons or not self.reporters.get(site):
                    raise ScenarioError(item.path, "a site has no background records") from None
                for _ in range(rate):
                    entities: dict[str, list[str]] = {t: [] for t in self.types}
                    for t, ids, fill in item.structured:
                        if rng.random() < fill:
                            entities[t] = [ids[index % len(ids)]]
                    reporter = (rng.choice(self.reporters[site]) if item.reporter == "pool"
                                else self.reporters[site][0])
                    origin = self._add({
                        "record_ref": self._ref(site), "site": site,
                        "received_date": (monday + timedelta(days=rng.randrange(7))).isoformat(),
                        "language": language, "codes": list(item.codes), "entities": entities,
                        "narrative": self._narrative(item, language, rng),
                        "persons": dict(rng.choice(self.persons[site])), "reporter": reporter,
                        "origin_ref": None, "origin_site": None, "synthetic": True}, item.path)
                    refs.append(origin["record_ref"])
                    index += 1
                    for to_site, marked in item.copies:
                        copy = self._add({
                            "record_ref": self._ref(to_site), "site": to_site,
                            "received_date": (monday + timedelta(days=rng.randrange(7))).isoformat(),
                            "language": origin["language"], "codes": list(origin["codes"]),
                            "entities": {t: list(origin["entities"][t]) for t in self.types},
                            "narrative": origin["narrative"], "persons": dict(origin["persons"]),
                            "reporter": origin["reporter"],
                            "origin_ref": origin["record_ref"] if marked else None,
                            "origin_site": origin["site"] if marked else None, "synthetic": True}, item.path)
                        self.copy_of[copy["record_ref"]] = origin["record_ref"]
                        refs.append(copy["record_ref"])
        return refs


def _claims(record: Mapping[str, Any], pack: FrozenPack, canonicaliser: Canonicaliser,
            text: bool) -> set[tuple[str, str, str]]:
    codes = codes_channel(record, pack, canonicaliser)
    lexical = LexicalExtractor(pack, canonicaliser).extract(record, codes) if text else None
    return {(c.entity_type, c.entity_id, c.predicate) for c in pair(codes, lexical)}


def _check(scenario: Scenario, item: Item, records: list[dict[str, Any]],
           canonicalisers: Mapping[str, Canonicaliser]) -> None:
    pack = scenario.pack
    keys = {tuple(k.split(":", 2)) for k in item.keys}
    for record in records:
        canon = canonicalisers[record["site"]]
        if item.role == "sibling":
            hero = scenario.hero.key
            target = (item.key.entity_type, item.key.entity_id, hero.predicate)
            if target in _claims(record, pack, canon, True):
                raise ScenarioError(item.path, "a sibling record states the hero's predicate") from None
            continue
        codes = _claims(record, pack, canon, False)
        if item.visibility == "narrative_only" and codes & keys:
            raise ScenarioError(item.path, "the codes channel holds a narrative-only key") from None
        if item.visibility in CODE_VISIBILITIES and not codes & keys:
            raise ScenarioError(item.path, "the codes channel misses the key") from None
        if item.visibility in TEXT_VISIBILITIES and not _claims(record, pack, canon, True) & keys:
            raise ScenarioError(item.path, "the lexical extractor misses the key") from None


def _case_keys(scenario: Scenario, hero_records: list[dict[str, Any]],
               canonicalisers: Mapping[str, Canonicaliser]) -> tuple[str, ...]:
    pack = scenario.pack
    key = scenario.hero.key
    entities = {(key.entity_type, key.entity_id)}
    predicates = {key.predicate}
    for record in hero_records:
        canon = canonicalisers[record["site"]]
        codes = codes_channel(record, pack, canon)
        entities.update((e.entity_type, e.entity_id) for e in codes.entities)
        claims = LexicalExtractor(pack, canon).extract(record, codes).claims
        entities.update((c.entity_type, c.entity_id) for c in claims)
        predicates.update(pack.codes[c].predicate for c in record["codes"] if c in pack.codes)
    egress = set(pack.egress.egress_entity_types)
    return tuple(sorted(series_key(t, i, p) for t, i in entities if t in egress for p in predicates))


def _by_construction(scenario: Scenario, hero_records: list[dict[str, Any]]) -> dict[str, Any]:
    pack = scenario.pack
    hero = scenario.hero
    hidden = hero.visibility == "narrative_only"
    reason = None
    if hidden:
        coded = any(pack.codes[c].predicate == hero.key.predicate for r in hero_records for c in r["codes"])
        allowed = set(pack.egress.central_allowed_fields)
        field = f"entities.{hero.key.entity_type}"
        canon = Canonicaliser(pack)
        structured = field in allowed and any(
            (m := canon.resolve_exact(hero.key.entity_type, v)) is not None and m.entity_id == hero.key.entity_id
            for r in hero_records for v in r["entities"].get(hero.key.entity_type, ()))
        if not coded:
            reason = "predicate_only_in_narrative" if structured else "entity_and_predicate_only_in_narrative"
    return {"S": hidden, "R_mf": hidden, "reason": reason}


def _apply_shifts(scenario: Scenario, records: list[dict[str, Any]], copy_of: Mapping[str, str]) -> None:
    """Every non-copy record at a shifted site in a covered week gets exactly the shift's code; every copy then takes
    its origin's codes (a background copy names its origin in ``origin_ref``; a scenario copy is in ``copy_of``)."""
    cm = scenario.codes_miss
    if cm is None:
        return
    start = date.fromisoformat(scenario.pack.generator["start"])
    by_ref = {r["record_ref"]: r for r in records}
    copies = {r["record_ref"]: r["origin_ref"] for r in records if r["origin_ref"] is not None}
    copies.update(copy_of)
    for r in records:
        if r["record_ref"] in copies:
            continue
        week = (date.fromisoformat(r["received_date"]) - start).days // 7
        for shift in cm.shifts:
            if shift.covers(r["site"], week):
                r["codes"] = [shift.to_code]
    for ref in sorted(copies):
        origin = by_ref.get(copies[ref])
        if origin is not None:
            by_ref[ref]["codes"] = list(origin["codes"])


def build_world(scenario: Scenario, *, without: str | None = None) -> DemoWorld:
    """The scenario's world; ``without`` names an item left out (the codes-miss rule's counterfactual world, whose
    ``case_keys`` are then empty: the rule reads the case keys of the world with every item)."""
    pack = scenario.pack
    world = None
    try:
        world = generate(pack, scenario.seed, len(scenario.org.sites), scenario.weeks)
    except GeneratorError:
        world = None
    if world is None:
        raise ScenarioError("$.seed", "the pack's world generator refuses these settings") from None
    builder = _Builder(scenario, world.records)
    item_records: dict[str, tuple[str, ...]] = {}
    for item in scenario.items:
        item_records[item.id] = tuple(builder.item(item)) if item.id != without else ()
    hero = scenario.hero
    if scenario.codes_miss is not None and without != hero.id:
        for site in hero.site_ids:                     # R3: the generator's own master data, not the additions
            if hero.key.entity_id not in world.master_data.get(site, {}).get(hero.key.entity_type, ()):
                raise ScenarioError(f"{hero.path}.key", "R3: the hero's entity is a universe entity in every hero "
                                                        "site's master data") from None
    background = [dict(r) for r in world.records]
    _apply_shifts(scenario, background + builder.records, builder.copy_of)
    master: dict[str, dict[str, set[str]]] = {s: {t: set(ids) for t, ids in world.master_data[s].items()}
                                              for s in world.master_data}
    for site, t, ids in scenario.master_data_additions:
        master.setdefault(site, {}).setdefault(t, set()).update(ids)
    master_data = MappingProxyType({s: MappingProxyType({t: tuple(sorted(master[s][t])) for t in sorted(master[s])})
                                    for s in sorted(master)})
    canonicalisers = {s: Canonicaliser(pack, known=master_data[s]) for s in sorted(master_data)}
    by_ref = {r["record_ref"]: r for r in builder.records}
    hero_records = [by_ref[ref] for ref in item_records[scenario.hero.id]]
    case_keys = _case_keys(scenario, hero_records, canonicalisers) if hero_records else ()   # before the canaries
    if scenario.codes_miss is not None and hero_records:
        for item in scenario.items:                    # R7: no decoy key is a hero case key
            if item.role == "decoy" and set(item.keys) & set(case_keys):
                raise ScenarioError(f"{item.path}.key", "R7: a decoy key is a hero case key") from None
    by_construction = _by_construction(scenario, hero_records)
    fill = []
    for t, _, _ in scenario.hero.structured:
        fill.append(MappingProxyType({"entity_type": t, "entity_type_label": pack.entity_types[t].label,
                                      "filled": sum(1 for r in hero_records if r["entities"][t]),
                                      "records": len(hero_records)}))
    records, manifest = plant_canaries(tuple(background) + tuple(builder.records),
                                       random.Random(f"collective-demo-canaries:{scenario.seed}"), pack)
    planted = {r["record_ref"]: r for r in records}
    for item in scenario.items:
        _check(scenario, item, [planted[ref] for ref in item_records[item.id]], canonicalisers)
    first = date.fromisoformat(pack.generator["start"])
    weeks = tuple(iso_week(first + timedelta(days=7 * i)) for i in range(scenario.weeks))
    return DemoWorld(records=records, master_data=master_data, weeks=weeks,
                     item_records=MappingProxyType(item_records), narratives=tuple(r["narrative"] for r in records),
                     manifest=manifest, case_keys=case_keys, by_construction=MappingProxyType(by_construction),
                     structured_fill=tuple(fill))
