#!/usr/bin/env python
"""Build vehicle pack v2 from the frozen vehicle pack (plan item 1.5 of the 2026-10-09 handoff).

    python tools/market/vehicle_pack_v2.py --pack docs/collective/replay/vehicles/pack
        --probe docs/collective/replay/vehicles/nhtsa-probe.json --out docs/collective/replay/vehicles/pack-v2
    python tools/market/vehicle_pack_v2.py --check PACK --probe docs/collective/replay/vehicles/nhtsa-probe.json

``pack/`` stays as it is: V001 to V003 and R002 use it. v2 is a copy with three changes, each a pure function here:

* **One name per component.** NHTSA's flat files carry two naming schemes for some components (the complaint file's
  notes say that since its May 2021 update it shows component names as they changed). Many recalls use the old names
  and complaints the new ones, so such an outcome could never match an alert (handoff mistake 7). :data:`MERGED` maps
  each of the three old names that ``pack/`` kept as its own category to its new name's code; their predicates and
  codes go, and their phrases join the new name's lexicon. The mapping still lists the old names, so the exporter
  writes the same ``components[]`` column as before and an outcome under an old name gets the new name's predicate.
  :func:`name_check` is the check: every component name in the probe resolves to a predicate that a name of the
  complaints' scheme also resolves to.
* **A missing reporter is not one reporter.** ``pack/``'s mapping has no reporter field, and a site counts every
  unknown reporter as one shared reporter (``edge/site.py`` ``build_cells``), so every burst got the few-reporters
  penalty (handoff mistake 9). v2's mapping reads a ``reporter`` column, which the exporter fills with each
  complaint's own ``ODINO`` when the pack names it (``nhtsa_export.pack_reporter``): NHTSA publishes no complainant id,
  so each complaint counts as its own reporter, as model-free R already counts it. ``pack/`` names none, so its
  export is unchanged.
* **Detector thresholds for 60 sites.** ``pack/``'s ``detectors.json`` is the device pack's, set for 6 plants
  (handoff mistake 4). :data:`DETECTOR_CHANGES` is the first rung of a ladder whose null candidate rate passes
  ``vehicle_null_rate.py``'s bar on the generator's synthetic null world at 60 sites (``PACK-V2.md`` beside the pack
  gives the runs).

The generator is ``pack/``'s with 60 fictional sites instead of 6 (the null world the thresholds are measured on), 12
more filler sentences so that two years of 60 sites have enough distinct narratives, and the merged predicates
removed. The fixtures are rebuilt for the merged predicates. Everything else is copied.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vehicle_pack import ENTITY, FICTIONAL_VEHICLES, GENERIC_CATEGORY, category_of  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.2.0"
# old name -> new name, from the handoff (mistake 7), CHOICE-R001.md and the categories pack/ kept: both names of
# each pair are among the probe's complaint categories, and pack/ gave each its own predicate
MERGED = {"ENGINE AND ENGINE COOLING": "ENGINE",
          "FUEL SYSTEM, GASOLINE": "FUEL/PROPULSION SYSTEM",
          "SERVICE BRAKES, HYDRAULIC": "SERVICE BRAKES"}
REPORTER_PATH = "reporter"
SITES = [f"state-{i:02d}" for i in range(1, 61)]
# the device pack's 12 filler sentences give too few filler-only narratives for 60 sites over two years
FILLER_ADDED = ["The owner contacted the dealer.", "The vehicle was towed to the dealer.",
                "The dealer could reproduce the problem.", "The incident happened on a highway.",
                "The incident happened in a parking lot.", "The owner has contacted the manufacturer.",
                "A repair is scheduled for next week.", "The vehicle was purchased new.",
                "The vehicle was purchased used.", "The dealer kept the vehicle for two days.",
                "The owner is waiting for parts.", "The owner asked for a refund."]
# dotted path in detectors.json -> value; set from vehicle_null_rate.py runs (PACK-V2.md)
DETECTOR_CHANGES: dict[str, Any] = {"burst.alpha_site": 0.001, "burst.min_sites": 3, "cooccurrence.min_sites": 3}
COPIED = ("aliases.json", "egress.json", "followups.json", "questions.json", "rules.json")


class BuildError(ValueError):
    pass


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------------------- names

def merge_names(mapping: Mapping[str, Any], codes: Mapping[str, Any], vocabulary: Mapping[str, Any],
                merged: Mapping[str, str] = MERGED) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The mapping, codes and vocabulary with each old name mapped to its new name's code, the old predicates and
    codes removed, and their phrases added to the new predicate's lexicon."""
    mapping, codes, vocabulary = (copy.deepcopy(dict(x)) for x in (mapping, codes, vocabulary))
    value_map = mapping["codes"][0]["value_map"]
    for old, new in sorted(merged.items()):
        if old not in value_map or new not in value_map:
            raise BuildError(f"{old!r} and {new!r} must both be categories of the pack")
        old_code, new_code = value_map[old], value_map[new]
        old_pred, new_pred = codes[old_code]["predicate"], codes[new_code]["predicate"]
        if sum(1 for c in codes.values() if c["predicate"] == old_pred) != 1:
            raise BuildError(f"predicate {old_pred} has more than one code")
        value_map[old] = new_code
        del codes[old_code]
        phrases = vocabulary["predicates"].pop(old_pred)["lexicon"]["en"]
        lexicon = vocabulary["predicates"][new_pred]["lexicon"]["en"]
        lexicon.extend(p for p in phrases if p not in lexicon)
    return mapping, codes, vocabulary


def resolve(name: str, value_map: Mapping[str, str], codes: Mapping[str, Any]) -> dict[str, Any]:
    """Where a component name goes: its code and predicate as a complaint (a category outside the mapping is
    exported as unknown or other), and as an outcome (a non-specific code matches any failure, shown as None)."""
    cat = category_of(name)
    code = value_map.get(cat, value_map[GENERIC_CATEGORY])
    return {"category": cat, "code": code, "predicate": codes[code]["predicate"],
            "outcome_predicate": codes[code]["predicate"] if codes[code]["specific"] else None}


def name_check(counts: Sequence[Sequence[Any]], mapping: Mapping[str, Any], codes: Mapping[str, Any],
               old_names: Sequence[str] = tuple(MERGED)) -> dict[str, Any]:
    """Every component name in ``counts`` (the probe's ``[name, complaints]`` list), resolved as an outcome would be.
    A name passes when its outcome matches any failure, or when a name of the complaints' scheme (every name but
    ``old_names``) resolves to the same predicate."""
    value_map = mapping["codes"][0]["value_map"]
    rows = [{"name": str(n), "complaints": int(c), **resolve(str(n), value_map, codes)} for n, c in counts]
    current = {r["predicate"] for r in rows if r["category"] not in old_names}
    for r in rows:
        r["ok"] = r["outcome_predicate"] is None or r["outcome_predicate"] in current
    return {"names": len(rows), "old_names": sorted(old_names), "failing": [r["name"] for r in rows if not r["ok"]],
            "rows": rows}


# --------------------------------------------------------------------------------------------------- the rest

def set_dotted(obj: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``obj`` (a ``detectors.json``) with each dotted path set; every path must exist already."""
    out = copy.deepcopy(dict(obj))
    for path, value in sorted(changes.items()):
        node = out
        parts = path.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                raise BuildError(f"no such detector setting: {path}")
            node = node[part]
        if parts[-1] not in node:
            raise BuildError(f"no such detector setting: {path}")
        node[parts[-1]] = value
    return out


def generator(base: Mapping[str, Any], vocabulary: Mapping[str, Any]) -> dict[str, Any]:
    """``pack/``'s generator with 60 sites like its own (the null world of the detector thresholds), the merged
    predicates removed and more filler; its vehicles, volumes, weights and rates are unchanged."""
    gen = copy.deepcopy(dict(base))
    site = gen["sites"][0]
    gen["sites"] = [{"id": s, "label": f"State {s[-2:]} (fictional)", "language": site["language"],
                     "weekly_volume": site["weekly_volume"], "reporters": site["reporters"]} for s in SITES]
    keep = vocabulary["predicates"]
    gen["predicate_weights"] = {p: w for p, w in gen["predicate_weights"].items() if p in keep}
    gen["narratives"] = {"en": {p: t for p, t in gen["narratives"]["en"].items() if p in keep}}
    gen["master_data"] = {s: {ENTITY: "all"} for s in SITES}
    phrases = [p for d in vocabulary["predicates"].values() for p in d["lexicon"]["en"]]
    phrases += [c for cues in vocabulary["negation"]["en"].values() for c in cues if c.strip(",")]
    for text in FILLER_ADDED:
        if any(re.search(rf"\b{re.escape(p)}\b", text.lower()) for p in phrases):
            raise BuildError(f"filler {text!r} holds a lexicon phrase or a negation cue")
    gen["filler"] = {"en": gen["filler"]["en"] + [t for t in FILLER_ADDED if t not in gen["filler"]["en"]]}
    return gen


def fixtures(vocabulary: Mapping[str, Any], codes: Mapping[str, Any], plant: Mapping[str, Any]) -> tuple[str, dict]:
    """``pack/``'s 48 construction fixtures (vehicle_pack.build's rule) over the merged predicates, and its smoke
    plant on the new site ids."""
    code_of = {c["predicate"]: k for k, c in codes.items()}         # one code per predicate after the merge
    words = {p: vocabulary["predicates"][p]["lexicon"]["en"][0] for p in vocabulary["predicates"]}
    plist = sorted(vocabulary["predicates"])
    lines = []
    for i in range(48):
        p, v = plist[i % len(plist)], FICTIONAL_VEHICLES[i % len(FICTIONAL_VEHICLES)]
        record = {"codes": [code_of[p]], "entities": {ENTITY: [v]}, "language": "en",
                  "narrative": f"The owner reported a problem with the {words[p]} of the {v}.", "origin_ref": None,
                  "origin_site": None, "persons": {}, "received_date": f"2024-02-{1 + i % 28:02d}",
                  "record_ref": f"vc-{i + 1:03d}", "reporter": None, "site": SITES[i % 6], "synthetic": True}
        gold = [{"entity_id": v, "entity_type": ENTITY, "negated": False, "predicate": p}]
        lines.append(json.dumps({"gold": gold, "record": record}, ensure_ascii=False) + "\n")
    old_sites = [f"state-{c}" for c in "abcdef"]
    smoke = copy.deepcopy(dict(plant))
    smoke["planted_by"] = "tools/market/vehicle_pack_v2.py (same author as the detector code)"
    for pattern in smoke["patterns"]:
        pattern["sites"] = [SITES[old_sites.index(s)] for s in pattern["sites"]]
    return "".join(lines), smoke


def build(pack_dir: Path, probe: Mapping[str, Any], out: Path,
          detector_changes: Mapping[str, Any] = DETECTOR_CHANGES) -> dict[str, Any]:
    if out.exists():
        raise BuildError(f"{out} exists; remove it first")
    counts = probe["complaints"]["top_components"]
    mapping, codes, vocabulary = merge_names(_read(pack_dir / "mapping.json"), _read(pack_dir / "codes.json"),
                                             _read(pack_dir / "vocabulary.json"))
    mapping["reporter"] = REPORTER_PATH
    check = name_check(counts, mapping, codes)
    if check["failing"]:
        raise BuildError(f"names without a complaint predicate: {check['failing']}")
    detectors = set_dotted(_read(pack_dir / "detectors.json"), detector_changes)
    meta = _read(pack_dir / "pack.json")
    meta.update({"version": VERSION, "disclaimer": (
        "Built by tools/market/vehicle_pack_v2.py from the pack of public replay V001 (pack/), for plan item 1.5 of "
        "the 2026-10-09 handoff: old and new NHTSA component names merged, each complaint its own reporter, and "
        "detector thresholds for 60 sites. The categories are NHTSA's; the generator, its vehicles and sites and the "
        "fixtures are fictional.")})
    out.mkdir(parents=True)
    (out / "fixtures").mkdir()
    _write(out / "pack.json", meta)
    _write(out / "mapping.json", mapping)
    _write(out / "codes.json", codes)
    _write(out / "vocabulary.json", vocabulary)
    for name in COPIED:
        _write(out / name, _read(pack_dir / name))
    _write(out / "detectors.json", detectors)
    _write(out / "generator.json", generator(_read(pack_dir / "generator.json"), vocabulary))
    lines, smoke = fixtures(vocabulary, codes, _read(pack_dir / "fixtures" / "plant_smoke.json"))
    (out / "fixtures" / "records.jsonl").write_text(lines, encoding="utf-8")
    _write(out / "fixtures" / "plant_smoke.json", smoke)
    sys.path.insert(0, str(ROOT))
    from mycelic.collective.packs.loader import PackError, load_pack_dir  # noqa: E402
    try:
        frozen = load_pack_dir(out)
    except PackError as err:
        raise BuildError(f"the built pack does not load: {err}") from None
    return {"pack": {"id": frozen.id, "version": frozen.version, **frozen.hashes()},
            "predicates": len(frozen.predicates), "merged": dict(MERGED), "detector_changes": dict(detector_changes)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--probe", required=True, help="the NHTSA probe's output (its complaints.top_components)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pack", help="the pack to build v2 from (with --out)")
    g.add_argument("--check", help="print the name check of this pack directory")
    p.add_argument("--out")
    args = p.parse_args(argv)
    probe = _read(Path(args.probe))
    if args.check:
        d = Path(args.check)
        got = name_check(probe["complaints"]["top_components"], _read(d / "mapping.json"), _read(d / "codes.json"))
        print(json.dumps(got, indent=1, sort_keys=True))
        return 0 if not got["failing"] else 1
    if not args.out:
        p.error("--pack needs --out")
    try:
        result = build(Path(args.pack), probe, Path(args.out))
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
