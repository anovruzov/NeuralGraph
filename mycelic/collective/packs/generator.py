"""A seeded synthetic world built from a pack's ``generator.json``: records, gold claims and master data.

``generate(pack, seed, sites, weeks)`` uses ``random.Random(seed)`` only (instance methods), iterates every mapping in
sorted order and never iterates a set, so the same arguments give the same world, byte for byte, on every machine
and under every ``PYTHONHASHSEED`` (:func:`world_digest` is the check).

Records are produced week by week and site by site (the first ``sites`` site specs), a seeded number per site and
week. A record is either:

* a **forward** (with probability ``forwarding_rate``, when an independent record from another site exists in the
  previous 4 weeks): a copy of a uniformly drawn one (narrative, codes, entities, language, persons, reporter and
  gold) under a new ``record_ref`` and this site, naming its origin; or
* **independent**: a main predicate drawn by weight among those with templates in the site's language; entities
  drawn from the universe, following links (a child drawn from its parent's list); sentences (the main affirmed
  template, then by rate a second predicate, a negated sentence and an entity-only sentence, then 1 to 3 filler
  sentences; filler only at ``zero_claim_rate``; with ``mixed_language_rate`` the second predicate comes from another
  pack language and the record's language is None); slot surfaces drawn by weight (exact, lower, underscore, en dash,
  full-width, a space form only for ids that are alias targets, an alias); codes by rate; structured entities by
  fill rate; persons from their generators and the reporter from the site's pool.

Person, reporter and slot values never hold an id of an egress entity type: a value whose scan finds one is
re-drawn, and after 50 attempts the generator fails. Narratives are unique among independent records: the sentence
choice is re-drawn, and after 20 attempts the generator fails rather than duplicate one.

Gold is what a perfect extractor outputs under ``edge/extract.py``'s semantics: per sentence, each slot entity with
the sentence's predicate and negation; a predicate sentence without a slot attached to the record's primary
structured entity (or omitted without one); entity-only sentences as ``(entity, None, False)``; duplicates removed.
Every record is synthetic and passes ``connector.check_record``.
"""
from __future__ import annotations

import copy
import random
import string
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ..jsonio import canonical_bytes, sha256_hex
from .canonical import Canonicaliser, ascii_case
from .connector import record_problems, valid_date
from .loader import SURFACES, thaw

if TYPE_CHECKING:
    from .loader import FrozenPack

PERSON_ATTEMPTS = 50
NARRATIVE_ATTEMPTS = 20
FORWARD_WEEKS = 4
LOWER_STRUCTURED_RATE = 0.1


class GeneratorError(ValueError):
    pass


@dataclass(frozen=True)
class World:
    params: Mapping[str, Any]
    records: tuple[dict[str, Any], ...]
    gold: Mapping[str, tuple[dict[str, Any], ...]]
    master_data: Mapping[str, Mapping[str, tuple[str, ...]]]


def world_digest(world: World) -> str:
    return sha256_hex(canonical_bytes({"params": thaw(world.params), "records": list(world.records),
                                       "gold": thaw(world.gold), "master_data": thaw(world.master_data)}))


def _weighted(rng: random.Random, items: Sequence[tuple[Any, float]]) -> Any:
    total = sum(w for _, w in items)
    x = rng.random() * total
    for value, w in items:
        x -= w
        if x < 0:
            return value
    return items[-1][0]


class _Builder:
    def __init__(self, pack: "FrozenPack", seed: int) -> None:
        self.pack = pack
        self.spec = pack.generator
        self.rng = random.Random(seed)
        self.canon = Canonicaliser(pack)
        universe = self.spec["universe"]
        # the re-draw check also knows the whole universe, so a space form of any id counts as id-shaped
        self.check = Canonicaliser(pack, known={t: universe[t] for t in universe})
        self.egress = frozenset(pack.egress.egress_entity_types)
        self.aliases_of: dict[str, dict[str, list[str]]] = {}
        for t in sorted(pack.aliases):
            table = pack.aliases[t]
            inverse: dict[str, list[str]] = {}
            for alias in sorted(table):
                inverse.setdefault(table[alias], []).append(alias)
            self.aliases_of[t] = inverse
        self.targets = pack.alias_targets()
        self.mapping = pack.mapping()
        self.primary_type = self.mapping["primary_entity_type"]
        self.weights = self.spec["predicate_weights"]
        self.narratives = self.spec["narratives"]
        self.links = self.spec["links"]

    # ------------------------------------------------------------------ values
    def _pattern(self, gen: Mapping[str, Any]) -> str:
        letters = string.ascii_uppercase if gen["case"] == "upper" else string.ascii_lowercase
        pools = {"alpha": letters, "digit": string.digits, "alnum": letters + string.digits}
        out = []
        for seg in gen["segments"]:
            kind = next(iter(seg))
            value = seg[kind]
            if kind == "literal":
                out.append(value)
            elif kind == "sep":
                if value == "required" or self.rng.random() < 0.5:
                    out.append(gen["separator"])
            else:
                n = self.rng.randint(value[0], value[1])
                out.append("".join(self.rng.choice(pools[kind]) for _ in range(n)))
        return "".join(out)

    def _raw_value(self, gen: Mapping[str, Any]) -> str:
        if gen["kind"] == "name":
            return f"{self.rng.choice(gen['first'])} {self.rng.choice(gen['last'])}"
        if gen["kind"] == "choice":
            return self.rng.choice(gen["values"])
        return self._pattern(gen)

    def value(self, gen: Mapping[str, Any]) -> str:
        for _ in range(PERSON_ATTEMPTS):
            v = self._raw_value(gen)
            if not any(m.entity_type in self.egress for m in self.check.scan(v).mentions):
                return v
        raise GeneratorError("person generator yields id-shaped values") from None

    def reporter_pool(self, size: int) -> list[str]:
        pool: list[str] = []
        for _ in range(PERSON_ATTEMPTS * size):
            if len(pool) == size:
                break
            v = self.value(self.spec["reporter"])
            if v not in pool:
                pool.append(v)
        if len(pool) < size:
            raise GeneratorError("the reporter generator cannot fill a site's pool with unique values") from None
        return pool

    # ------------------------------------------------------------------ entities and surfaces
    def entities(self) -> dict[str, str]:
        universe = self.spec["universe"]
        drawn = {t: self.rng.choice(universe[t]) for t in sorted(universe) if t not in self.links}
        for child in sorted(self.links):
            link = self.links[child]
            drawn[child] = self.rng.choice(link["map"][drawn[link["parent"]]])
        return drawn

    def surface(self, t: str, eid: str) -> str:
        et = self.pack.entity_types[t]
        aliases = self.aliases_of.get(t, {}).get(eid, [])
        if et.id_format is None:
            return self.rng.choice(aliases)
        sep = et.separator
        options = []
        for name in SURFACES:
            weight = self.spec["surface"][name]
            form = None
            if weight <= 0:
                continue
            if name == "exact":
                form = eid
            elif name == "lower":
                form = ascii_case(eid, "lower")
            elif name in ("underscore", "en_dash") and sep and sep in eid:
                form = eid.replace(sep, "_" if name == "underscore" else "\u2013")
            elif name == "space" and sep and sep in eid and eid in self.targets[t]:
                form = eid.replace(sep, " ")
            elif name == "fullwidth":
                form = "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in eid)
            elif name == "alias" and aliases:
                form = self.rng.choice(aliases)
            if form is not None:
                options.append((form, weight))
        return _weighted(self.rng, options) if options else eid

    # ------------------------------------------------------------------ sentences
    def eligible(self, lang: str, exclude: str | None = None) -> list[tuple[str, float]]:
        preds = self.narratives.get(lang, {})
        return [(p, self.weights[p]) for p in sorted(preds) if p in self.weights and p != exclude]

    def render(self, template: str, entities: Mapping[str, str], persons: Mapping[str, Any]) -> tuple[str, list]:
        """The sentence text and its slot entities as (type, id, surface)."""
        out, slots, pos = [], [], 0
        while True:
            i = template.find("{", pos)
            if i < 0:
                out.append(template[pos:])
                break
            j = template.index("}", i)
            out.append(template[pos:i])
            name = template[i + 1:j]
            if name.startswith("person:"):
                out.append(persons[name[len("person:"):]] or "")
            else:
                text = self.surface(name, entities[name])
                out.append(text)
                slots.append((name, entities[name], text))
            pos = j + 1
        return "".join(out), slots

    def sentences(self, lang: str, main: str, mixed_lang: str | None, zero: bool, entities: Mapping[str, str],
                  persons: Mapping[str, Any], primary: tuple[str, str] | None) -> tuple[str, list[dict[str, Any]]]:
        rng = self.rng
        plan: list[tuple[str, str | None, bool, str]] = []   # (template, predicate, negated, language)
        if not zero:
            plan.append((rng.choice(self.narratives[lang][main]["affirmed"]), main, False, lang))
            if mixed_lang is not None:
                options = self.eligible(mixed_lang, main) or self.eligible(mixed_lang)
                second = _weighted(rng, options)
                plan.append((rng.choice(self.narratives[mixed_lang][second]["affirmed"]), second, False, mixed_lang))
            elif rng.random() < self.spec["second_predicate_rate"]:
                options = self.eligible(lang, main)
                if options:
                    second = _weighted(rng, options)
                    plan.append((rng.choice(self.narratives[lang][second]["affirmed"]), second, False, lang))
            if rng.random() < self.spec["negated_sentence_rate"]:
                options = self.eligible(lang, main)
                if options:
                    neg = _weighted(rng, options)
                    plan.append((rng.choice(self.narratives[lang][neg]["negated"]), neg, True, lang))
            if rng.random() < self.spec["entity_sentence_rate"]:
                plan.append((rng.choice(self.spec["entity_sentences"][lang]), None, False, lang))
        filler = self.spec["filler"][lang]
        for text in rng.sample(list(filler), min(len(filler), rng.randint(1, 3))):
            plan.append((text, None, False, lang))
        rng.shuffle(plan)

        texts, gold = [], []
        for template, pred, negated, _ in plan:
            text, slots = self.render(template, entities, persons)
            texts.append(text)
            if pred is not None and not slots:
                if primary is not None:
                    gold.append({"entity_type": primary[0], "entity_id": primary[1], "entity_text": None,
                                 "predicate": pred, "negated": negated})
                continue
            for t, eid, surface in slots:
                gold.append({"entity_type": t, "entity_id": eid, "entity_text": surface, "predicate": pred,
                             "negated": negated})
        unique, seen = [], set()
        for g in gold:
            key = (g["entity_type"], g["entity_id"], g["predicate"] or "", g["negated"])
            if key not in seen:
                seen.add(key)
                unique.append(g)
        return " ".join(texts), unique

    # ------------------------------------------------------------------ one independent record
    def codes(self, main: str) -> list[str]:
        spec = self.spec["codes"]
        r = self.rng.random()
        if r < spec["specific_rate"]:
            specific = sorted(c for c, code in self.pack.codes.items() if code.predicate == main and code.specific)
            return [self.rng.choice(specific)] if specific else [spec["generic_code"]]
        if r < spec["specific_rate"] + spec["generic_rate"]:
            return [spec["generic_code"]]
        return []

    def structured(self, entities: Mapping[str, str]) -> dict[str, list[str]]:
        out = {}
        for t in sorted(self.mapping["entities"]):
            values = []
            if self.rng.random() < self.spec["fill_rates"].get(t, 0):
                eid = entities[t]
                lower = (self.pack.entity_types[t].id_format is not None
                         and self.rng.random() < LOWER_STRUCTURED_RATE)
                values.append(ascii_case(eid, "lower") if lower else eid)
            out[t] = values
        return out

    def independent(self, site: Mapping[str, Any], narratives_seen: set[str]) -> tuple[dict[str, Any], list]:
        rng = self.rng
        lang = site["language"]
        others = [g for g in sorted(self.narratives) if g != lang and self.eligible(g)]
        mixed_lang = rng.choice(others) if others and rng.random() < self.spec["mixed_language_rate"] else None
        main = _weighted(rng, self.eligible(lang))
        zero = rng.random() < self.spec["zero_claim_rate"]
        entities = self.entities()
        codes = self.codes(main)
        structured = self.structured(entities)
        persons = {f: self.value(self.spec["persons"][f]) for f in sorted(self.spec["persons"])}
        primary = None
        for value in structured.get(self.primary_type, []):
            m = self.canon.resolve_exact(self.primary_type, value)
            if m is not None:
                primary = (self.primary_type, m.entity_id)
                break
        for _ in range(NARRATIVE_ATTEMPTS):
            narrative, gold = self.sentences(lang, main, mixed_lang, zero, entities, persons, primary)
            if narrative not in narratives_seen:
                narratives_seen.add(narrative)
                record = {"language": None if mixed_lang else lang, "codes": codes, "entities": structured,
                          "narrative": narrative, "persons": persons}
                return record, gold
        raise GeneratorError(f"narrative uniqueness exhausted after {NARRATIVE_ATTEMPTS} re-draws") from None


def generate(pack: "FrozenPack", seed: int, sites: int, weeks: int, start: str | None = None) -> World:
    spec = pack.generator
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GeneratorError("seed must be an int") from None
    if isinstance(sites, bool) or not isinstance(sites, int) or not 1 <= sites <= len(spec["sites"]):
        raise GeneratorError(f"sites must be an int in [1, {len(spec['sites'])}]") from None
    need = pack.detectors["baseline_weeks"] + pack.detectors["window_weeks"]
    if isinstance(weeks, bool) or not isinstance(weeks, int) or weeks < need:
        raise GeneratorError(f"weeks must be an int >= baseline_weeks + window_weeks = {need}") from None
    start = spec["start"] if start is None else start
    if not valid_date(start) or date.fromisoformat(start).weekday() != 0:
        raise GeneratorError("start must be a Monday, YYYY-MM-DD") from None

    b = _Builder(pack, seed)
    rng = b.rng
    chosen = list(spec["sites"][:sites])
    pools = {s["id"]: b.reporter_pool(s["reporters"]) for s in chosen}
    first_day = date.fromisoformat(start)
    records: list[dict[str, Any]] = []
    gold: dict[str, tuple[dict[str, Any], ...]] = {}
    by_week: list[list[tuple[str, dict[str, Any]]]] = []      # independent records per week, in generation order
    counters = {s["id"]: 0 for s in chosen}
    narratives_seen: set[str] = set()
    for week in range(weeks):
        by_week.append([])
        for site in chosen:
            sid = site["id"]
            for _ in range(rng.randint(site["weekly_volume"][0], site["weekly_volume"][1])):
                counters[sid] += 1
                ref = f"{sid}-{counters[sid]:06d}"
                received = (first_day + timedelta(days=7 * week + rng.randrange(7))).isoformat()
                forward = rng.random() < spec["forwarding_rate"]
                candidates = ([r for w in range(max(0, week - FORWARD_WEEKS), week) for s, r in by_week[w] if s != sid]
                              if forward else [])
                if candidates:
                    origin = rng.choice(candidates)
                    record = {**copy.deepcopy({k: origin[k] for k in ("language", "codes", "entities", "narrative",
                                                                     "persons", "reporter")}),
                              "record_ref": ref, "site": sid, "received_date": received,
                              "origin_ref": origin["record_ref"], "origin_site": origin["site"], "synthetic": True}
                    gold[ref] = gold[origin["record_ref"]]
                else:
                    body, claims = b.independent(site, narratives_seen)
                    record = {**body, "record_ref": ref, "site": sid, "received_date": received,
                              "reporter": rng.choice(pools[sid]), "origin_ref": None, "origin_site": None,
                              "synthetic": True}
                    gold[ref] = tuple(claims)
                    by_week[week].append((sid, record))
                record = {k: record[k] for k in sorted(record)}
                problems = record_problems(record, pack)
                if problems:
                    raise GeneratorError(f"generated record {ref} fails check_record: {problems[0]}") from None
                records.append(record)

    universe = spec["universe"]
    master = {}
    for site in chosen:
        per_type = spec["master_data"].get(site["id"], {})
        master[site["id"]] = MappingProxyType({t: tuple(universe[t] if per_type[t] == "all" else per_type[t])
                                               for t in sorted(per_type)})
    params = {"pack": pack.id, "version": pack.version, "fixtures_hash": pack.fixtures_hash, "seed": seed,
              "sites": sites, "site_ids": [s["id"] for s in chosen], "weeks": weeks, "start": start}
    return World(params=MappingProxyType(params), records=tuple(records), gold=MappingProxyType(gold),
                 master_data=MappingProxyType(master))
