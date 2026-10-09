#!/usr/bin/env python
"""Build the vehicle-complaints pack, by rule, from NHTSA's own component categories (public replay V001).

    python tools/market/vehicle_pack.py --probe docs/collective/replay/vehicles/nhtsa-probe.json
        --out docs/collective/replay/vehicles/pack

A fourth field, built mechanically from a public schema rather than written by hand: the rule is
`docs/collective/replay/vehicles/CHOICE-V001.md`, and every step is a pure function here.

* **The entity** is the vehicle: make, model and model year, each upper-cased with every character other than A-Z and
  0-9 removed, the make cut to 12 characters and the model to 20, joined by dashes (``FORD-F150-2021``).
* **The predicates** are NHTSA's top-level component categories (the part of ``COMPDESC`` before the first colon)
  with at least :data:`MIN_COMPLAINTS` complaints in the probe's 2015-2024 counts, ``UNKNOWN OR OTHER`` aside. Each
  category's phrases are its lower-cased name split at ``/``, ``,``, brackets and `` and ``; a phrase that several
  categories share belongs to the one with most complaints, and a category left with no phrase of its own is dropped
  (its complaints count as unknown or other). The generic predicate ``unknown_or_other`` takes everything else.
* **The codes** are ``NHT-001`` onwards, one specific code per category in descending complaint order, and the
  non-specific ``NHT-999`` for unknown or other; the export's mapping maps each category name to its code.
* **The rest** is structure, not data: the device pack's detectors, egress limits, negation cues and extraction
  settings, one cross-state rule on the largest category, the generic question templates, an evidence-packet
  follow-up, and a small fictional generator and fixtures so that the pack loads (the replay reads neither).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "mycelic" / "collective" / "packs" / "data" / "device_quality"
MIN_COMPLAINTS = 1000
GENERIC_CATEGORY = "UNKNOWN OR OTHER"
GENERIC_PREDICATE = "unknown_or_other"
GENERIC_CODE = "NHT-999"
ENTITY = "vehicle"
VEHICLE_FORMAT = [{"alnum": [1, 12]}, {"sep": "required"}, {"alnum": [1, 20]}, {"sep": "required"}, {"digit": [4, 4]}]
MAKE_MAX, MODEL_MAX = 12, 20
SITES = [f"state-{c}" for c in "abcdef"]
FICTIONAL_VEHICLES = ["ACME-ROADSTER-2021", "ACME-ROADSTER-2022", "ACME-HAULER-2020", "BOLT-COMPACT-2023",
                      "BOLT-COMPACT-2024", "ZEPHYR-VAN-2022"]
MAPPING_PATHS = {"record_ref": "odino", "site": "state", "received": "received", "codes": "components[]",
                 "vehicle": "vehicle", "narrative": "summary"}
_SPLIT = re.compile(r"/|,|\(|\)|\band\b")


class BuildError(Exception):
    pass


def vehicle_id(make: str, model: str, year: str) -> str | None:
    """The canonical vehicle id, or None for a missing part or an unknown model year (9999)."""
    m = re.sub(r"[^A-Z0-9]", "", make.upper())[:MAKE_MAX]
    d = re.sub(r"[^A-Z0-9]", "", model.upper())[:MODEL_MAX]
    y = year.strip()
    if not m or not d or not re.fullmatch(r"(19|20)\d\d", y):
        return None
    return f"{m}-{d}-{y}"


def category_of(compdesc: str) -> str:
    """A component description's top-level category (before the first colon), upper-cased and trimmed."""
    return compdesc.split(":")[0].strip().upper()


def predicate_id(category: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")[:40].rstrip("_")
    return slug if slug[:1].isalpha() else f"c_{slug}"[:40]


def phrases(category: str) -> list[str]:
    out = []
    for part in _SPLIT.split(category.lower()):
        p = " ".join(part.split())
        if p and p not in out:
            out.append(p)
    return out


def choose(counts: Sequence[Sequence[Any]], min_complaints: int = MIN_COMPLAINTS) -> dict[str, Any]:
    """The rule's predicates: ``[(category, lexicon phrases)]`` in descending complaint order, and the dropped ones."""
    cats = [(category_of(str(c)), int(n)) for c, n in counts]
    merged: dict[str, int] = {}
    for c, n in cats:
        merged[c] = merged.get(c, 0) + n
    kept = sorted(((c, n) for c, n in merged.items() if n >= min_complaints and c != GENERIC_CATEGORY),
                  key=lambda cn: (-cn[1], cn[0]))
    owner: dict[str, str] = {}
    for c, _ in kept:                                 # descending: the larger category takes a shared phrase
        for p in phrases(c):
            owner.setdefault(p, c)
    chosen, dropped = [], []
    for c, n in kept:
        own = [p for p in phrases(c) if owner[p] == c]
        if own:
            chosen.append({"category": c, "complaints": n, "predicate": predicate_id(c), "lexicon": own})
        else:
            dropped.append(c)
    ids = [x["predicate"] for x in chosen]
    if len(set(ids)) != len(ids) or GENERIC_PREDICATE in ids:
        raise BuildError("two categories give the same predicate id")
    return {"predicates": chosen, "dropped": dropped}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build(chosen: Mapping[str, Any], out: Path) -> dict[str, Any]:
    preds = chosen["predicates"]
    if out.exists():
        raise BuildError(f"{out} exists; remove it first")
    out.mkdir(parents=True)
    (out / "fixtures").mkdir()
    base_vocab = _read(BASE / "vocabulary.json")
    codes = {f"NHT-{i:03d}": {"label": p["category"].title(), "predicate": p["predicate"], "specific": True}
             for i, p in enumerate(preds, start=1)}
    codes[GENERIC_CODE] = {"label": "Unknown or other component", "predicate": GENERIC_PREDICATE, "specific": False}
    code_of = {p["predicate"]: c for c, p in zip(codes, preds)}
    predicates = {p["predicate"]: {"label": p["category"].title(), "lexicon": {"en": p["lexicon"]}} for p in preds}
    predicates[GENERIC_PREDICATE] = {"label": "Unknown or other component", "lexicon": {"en": ["unknown or other"]}}
    _write(out / "pack.json", {
        "id": "vehicle_complaints", "version": "0.1.0",
        "title": "Vehicle safety complaints (NHTSA component categories)", "languages": ["en"], "illustrative": True,
        "disclaimer": ("Built by tools/market/vehicle_pack.py for public replay V001 from NHTSA's public complaint "
                       "categories. The categories are NHTSA's; the generator, its vehicles and sites and the "
                       "fixtures are fictional and exist only so that the pack loads."),
        "same_author_as_code": True})
    _write(out / "vocabulary.json", {
        "entity_types": {ENTITY: {"label": "Vehicle (make, model, model year)", "id_format": VEHICLE_FORMAT,
                                  "separator": "-", "case": "upper", "strip_leading_zeros": False,
                                  "exact_match_metric": True, "egress": True, "ids": None}},
        "predicates": predicates,
        "negation": {"en": base_vocab["negation"]["en"]},
        "negation_window": base_vocab["negation_window"],
        "extraction": base_vocab["extraction"]})
    _write(out / "codes.json", codes)
    _write(out / "aliases.json", {})
    value_map = {p["category"]: code_of[p["predicate"]] for p in preds}
    value_map[GENERIC_CATEGORY] = GENERIC_CODE
    _write(out / "mapping.json", {
        "record_ref": MAPPING_PATHS["record_ref"], "site": MAPPING_PATHS["site"],
        "received_date": {"path": MAPPING_PATHS["received"], "format": "yyyymmdd"}, "language": None,
        "codes": [{"path": MAPPING_PATHS["codes"], "value_map": value_map}],
        "entities": {ENTITY: [MAPPING_PATHS["vehicle"]]}, "primary_entity_type": ENTITY,
        "narrative": [{"path": MAPPING_PATHS["narrative"], "where": None}], "persons": {}, "reporter": None,
        "origin_ref": None, "origin_site": None, "required": ["narrative"]})
    egress = _read(BASE / "egress.json")
    egress.update({"egress_entity_types": [ENTITY], "never_fields": ["narrative", "reporter"],
                   "central_allowed_fields": ["codes", f"entities.{ENTITY}", "received_date", "site"]})
    _write(out / "egress.json", egress)
    shutil.copyfile(BASE / "detectors.json", out / "detectors.json")
    top = preds[0]["predicate"]
    _write(out / "rules.json", {"rules": {f"multi_state_{top}"[:40]: {
        "label": f"{preds[0]['category'].title()} complaints on one vehicle in several states",
        "entity_type": ENTITY, "predicate": top, "min_sites": 2, "min_count_per_site": 2, "window_weeks": 8}}})
    questions = _read(BASE / "questions.json")
    _write(out / "questions.json", {
        "templates": {"count_predicate_on_entity": {
            "text": "In the last {window}, how many of your complaints describe {predicate_label} for "
                    "{entity_type_label} {entity_id}?", "entity_types": [ENTITY], "predicates": None}},
        "pushdown": questions["pushdown"]})
    followups = _read(BASE / "followups.json")
    packet = dict(followups["types"]["evidence_packet"], owner_role="safety_engineer",
                  label="Evidence packet assembled in each state")
    _write(out / "followups.json", {"roles": {"safety_engineer": {"label": "Vehicle safety engineer"}},
                                    "types": {"evidence_packet": packet}})
    gen = _read(BASE / "generator.json")
    words = {p: predicates[p]["lexicon"]["en"][0] for p in predicates}
    _write(out / "generator.json", {
        "start": gen["start"],
        "sites": [{"id": s, "label": f"State {s[-1].upper()} (fictional)", "language": "en", "weekly_volume": [3, 7],
                   "reporters": 5} for s in SITES],
        **{k: gen[k] for k in ("forwarding_rate", "negated_sentence_rate", "second_predicate_rate",
                               "entity_sentence_rate", "zero_claim_rate")},
        "mixed_language_rate": 0,                     # one language
        "predicate_weights": {p: 1 for p in predicates},
        "codes": {"specific_rate": 0.5, "generic_rate": 0.2, "generic_code": GENERIC_CODE},
        "fill_rates": {ENTITY: 1.0}, "universe": {ENTITY: FICTIONAL_VEHICLES}, "links": {},
        "surface": {k: (0 if k == "alias" else v) for k, v in gen["surface"].items()},
        "narratives": {"en": {p: {"affirmed": [f"The owner reported a problem with the {w} of the {{{ENTITY}}}.",
                                               f"The {w} of the {{{ENTITY}}} failed while driving."],
                                  "negated": [f"No problem with the {w} was found on the {{{ENTITY}}}."]}
                              for p, w in words.items()}},
        "entity_sentences": {"en": [f"The complaint concerns the {{{ENTITY}}}."]},
        "filler": {"en": gen["filler"]["en"]}, "persons": {}, "reporter": gen["reporter"],
        "master_data": {s: {ENTITY: "all"} for s in SITES}})
    lines = []
    plist = sorted(predicates)
    for i in range(48):
        p = plist[i % len(plist)]
        v = FICTIONAL_VEHICLES[i % len(FICTIONAL_VEHICLES)]
        code = GENERIC_CODE if p == GENERIC_PREDICATE else code_of[p]
        record = {"codes": [code], "entities": {ENTITY: [v]}, "language": "en",
                  "narrative": f"The owner reported a problem with the {words[p]} of the {v}.", "origin_ref": None,
                  "origin_site": None, "persons": {}, "received_date": f"2024-02-{1 + i % 28:02d}",
                  "record_ref": f"vc-{i + 1:03d}", "reporter": None, "site": SITES[i % len(SITES)], "synthetic": True}
        gold = [{"entity_id": v, "entity_type": ENTITY, "negated": False, "predicate": p}]
        lines.append(json.dumps({"gold": gold, "record": record}, ensure_ascii=False) + "\n")
    (out / "fixtures" / "records.jsonl").write_text("".join(lines), encoding="utf-8")
    second = preds[1]["predicate"] if len(preds) > 1 else top
    _write(out / "fixtures" / "plant_smoke.json", {
        "kind": "plant_spec", "schema_version": 1, "pack": "vehicle_complaints", "prereg_sha256": None,
        "planted_by": "tools/market/vehicle_pack.py (same author as the detector code)",
        "planter_saw_detector_code": True, "notes": "construction smoke for the pilot demo, never a result",
        "patterns": [
            {"id": "p1", "entity_type": ENTITY, "entity_id": FICTIONAL_VEHICLES[0], "predicate": top,
             "sites": SITES[:3], "start_week": 30, "weeks": 8, "rate_per_week": 2, "visibility": "narrative_only",
             "language": "en"},
            {"id": "p2", "entity_type": ENTITY, "entity_id": FICTIONAL_VEHICLES[3], "predicate": second,
             "sites": SITES[2:5], "start_week": 34, "weeks": 8, "rate_per_week": 2, "visibility": "both",
             "language": "en"}],
        "decoys": []})
    sys.path.insert(0, str(ROOT))
    from mycelic.collective.packs.loader import PackError, load_pack_dir  # noqa: E402
    try:
        frozen = load_pack_dir(out)
    except PackError as err:
        raise BuildError(f"the built pack does not load: {err}") from None
    return {"pack": {"id": frozen.id, "version": frozen.version, **frozen.hashes()},
            "predicates": len(predicates), "dropped": list(chosen["dropped"])}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--probe", required=True, help="the NHTSA probe's output (its complaints.top_components)")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    probe = _read(Path(args.probe))
    try:
        chosen = choose(probe["complaints"]["top_components"])
        result = {"rule": chosen, **build(chosen, Path(args.out))}
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
