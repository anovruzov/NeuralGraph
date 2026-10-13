"""The ``emergence`` pack, written from the benchmark's own constants (``docs/collective/E2E-INGEST.md`` section 3.4).

    python -m research.e2e_ingest.emergence_pack write [--out DIR]
    python -m research.e2e_ingest.emergence_pack check [DIR]

The pipeline, the detectors, the question templates and the gate need a pack. This module builds one from
``research/mycelic/corpus.py`` (``CAUSAL_CHAINS``, ``PRED_SURFACE``, the entity names of ``_mk_entities``) and from
the 200-user world's site ids and first records, writes it as canonical JSON, and loads it back with the pinned
loader (``load_pack_dir``) as its self-check. The pack is committed at ``research/e2e_ingest/pack/emergence/``; a test
regenerates it and requires byte identity. Nothing under ``mycelic/collective/packs/`` changes.

The predicate is the causal chain (section 3.4): ``chain_supply`` .. ``chain_network`` carry the surface phrases of
every predicate of their chain except ``revenue_miss``, which ends two chains and is a predicate of its own, so a
phrase maps to one predicate. Background predicates get no pack predicate.

Three points where the loader's rules, not the brief's wording, fix the data (``BUILD-E2E.md``):

* the canonical ids are ``E-`` plus the upper-cased entity name (``emergence_world.canonical_entity_id``): the loader
  refuses an alias whose folded form equals its target's ("alias chain"), and the alias table is exactly every entity
  name, as the brief says;
* ``codes.json`` holds one placeholder code per predicate, ``EM-01`` .. ``EM-08``; the loader needs at least one
  non-specific code and the generator a generic one, so ``EM-08`` (``revenue_miss``) is the non-specific one. No
  record carries a code;
* ``fixtures/records.jsonl`` holds the first 40 records of the world, the loader's minimum (``MIN_FIXTURES``), where
  the brief said 5.

Harness side; synthetic; ``measurement: false``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.jsonio import canonical_dumps, sha256_hex
from mycelic.collective.packs.loader import FIXTURES_FILE, MIN_FIXTURES, FrozenPack, PackError, load_pack_dir
from research.mycelic.corpus import CAUSAL_CHAINS, PRED_SURFACE, PREDICATES, Corpus

from .emergence_world import (CHAIN_PREDICATES, ENTITY_TYPE, LANGUAGE, PACK_DIR, PACK_ID, REVENUE_MISS, START,
                              build_records, canonical_entity_id, entity_ids, generate, pack_predicate, site_ids)

PACK_VERSION = "0.1.0"
WORLD_TARGET_USERS = 200
WORLD_SEED = 0
FIXTURE_RECORDS = MIN_FIXTURES
PACK_PREDICATES = (*CHAIN_PREDICATES, REVENUE_MISS)
CODES = {p: f"EM-{i:02d}" for i, p in enumerate(PACK_PREDICATES, start=1)}
GENERIC_CODE = CODES[REVENUE_MISS]
FILE_NAMES = ("pack.json", "vocabulary.json", "codes.json", "aliases.json", "mapping.json", "egress.json",
              "detectors.json", "rules.json", "questions.json", "followups.json", "generator.json", FIXTURES_FILE)
TEMPLATE_ID = "count_predicate_on_entity"
TEMPLATE_TEXT = ("In the last {window}, how many of your records describe {predicate_label} for {entity_type_label} "
                 "{entity_id}?")
DISCLAIMER = ("Every term of this pack comes from research/mycelic/corpus.py, the EMERGENCE benchmark's synthetic "
              "generator: the entity names are its invented stems and suffixes, the predicates are its causal chains "
              "and their surface phrases. Nothing here names a real organisation, product, vendor or person; the pack "
              "exists so the collective's pipeline can ingest the benchmark's synthetic records.")
# the extraction block, the detector blocks and the surface weights copied from device_quality
EXTRACTION = {"max_input_chars": 6000, "max_output_tokens": 1024, "max_claims": 20,
              "confidence": {"exact": 1.0, "alias": 0.95, "variant": 0.9}}
DETECTOR_BLOCKS = {
    "burst": {"alpha_site": 0.01, "lambda_floor": 0.01, "p_min": 0.01, "p_max": 0.25, "min_sites": 2},
    "cooccurrence": {"pmi_smoothing": 0.5, "pmi_delta": 1.0, "min_sites": 2},
    "resolution": {"res_conf_min": 0.95},
    "independence": {"echo_min_ratio": 0.5},
    "decoy": {"stale_days": 42, "base_rate_site_fraction": 0.5},
    "ranker": {"bias": -4.0, "weights": {"burst_surprise": 0.35, "pmi_rise": 0.5, "log_independent_roots": 0.4,
                                         "supporting_sites": 0.3, "low_res_conf": -1.0, "echo": -1.5,
                                         "few_reporters_share": -1.0, "high_base_rate": -1.5}},
}
DETECTOR_SETTINGS = {"alert_budget_per_week": 20, "cooldown_weeks": 2, "baseline_weeks": 8, "window_weeks": 6,
                     "min_history_weeks": 6}
EGRESS = {"k": 3, "suppress_below_days": 31, "count_granularity": "week", "close_lag_days": 14, "min_window_weeks": 6,
          "verdict_count_buckets": [3, 10, 50], "egress_entity_types": [ENTITY_TYPE], "require_master_data": True,
          "never_fields": ["narrative", "reporter"],
          "central_allowed_fields": ["codes", f"entities.{ENTITY_TYPE}", "received_date", "site"],
          "verify_max_records": 500, "question_budget_per_entity_per_day": 5,
          "question_entities_per_site_per_day": 200}
PUSHDOWN = {"min_confirming_sites": 2, "min_independent_roots": 3, "min_independent_reporters": 3,
            "freshness_days": 42, "max_sibling_sites": 2}
SURFACE = {"exact": 6, "lower": 1, "underscore": 1, "en_dash": 1, "fullwidth": 0.5, "space": 0.5, "alias": 2}
FOLLOWUPS = {   # mycelic/collective/onboard/data/template/followups.json, copied; the follow-up layer is not run
    "roles": {"pattern_reviewer": {"label": "Reviewer of drafted patterns"}},
    "types": {"evidence_packet": {"ack_days": 2, "args_schema": {"conclusion": {"kind": "conclusion_id"}},
                                  "daily_cap": 50, "draft_schema": None, "enabled": True, "escalate_to_role": None,
                                  "executor": "packet", "label": "Evidence packet assembled at each site",
                                  "owner_role": "pattern_reviewer", "template": None, "tier": "T0"}},
}
FILLER = ["The record was filed by its author.", "The thread holds the context.", "No further action is recorded.",
          "The owner is assigned.", "The review is pending.", "The ticket reference is attached."]


class EmergencePackError(ValueError):
    pass


def _label(text: str) -> str:
    return text.replace("_", " ")


def chain_lexicon(index: int) -> list[str]:
    """The surface phrases of every predicate of chain ``index`` except ``revenue_miss``, in chain order."""
    return [phrase for name in CAUSAL_CHAINS[index] if name != REVENUE_MISS for phrase in PRED_SURFACE[name]]


def predicate_lexicon(predicate: str) -> list[str]:
    if predicate == REVENUE_MISS:
        return list(PRED_SURFACE[REVENUE_MISS])
    return chain_lexicon(CHAIN_PREDICATES.index(predicate))


def predicate_label(predicate: str) -> str:
    if predicate == REVENUE_MISS:
        return "Revenue miss (ends two chains)"
    chain = CAUSAL_CHAINS[CHAIN_PREDICATES.index(predicate)]
    return f"Causal chain: {_label(chain[0])} to {_label(chain[-1])}"


def _vocabulary(ids: Sequence[str]) -> dict[str, Any]:
    return {
        "entity_types": {ENTITY_TYPE: {"label": "Entity", "id_format": None, "separator": None, "case": "upper",
                                       "strip_leading_zeros": False, "exact_match_metric": False, "egress": True,
                                       "ids": sorted(ids)}},
        "predicates": {p: {"label": predicate_label(p), "lexicon": {LANGUAGE: predicate_lexicon(p)}}
                       for p in PACK_PREDICATES},
        "negation": {LANGUAGE: {"pre": [], "post": ["not"], "terminators": []}},
        "negation_window": 8,
        "extraction": EXTRACTION,
    }


def _codes() -> dict[str, Any]:
    return {CODES[p]: {"label": f"Placeholder code for {_label(p)}; attached to no record",
                       "predicate": p, "specific": CODES[p] != GENERIC_CODE} for p in PACK_PREDICATES}


def _mapping() -> dict[str, Any]:
    return {"record_ref": "record_ref", "site": "site", "received_date": {"path": "received_date", "format": "iso"},
            "language": "language", "codes": [{"path": "codes[]", "value_map": None}],
            "entities": {ENTITY_TYPE: [f"entities.{ENTITY_TYPE}[]"]}, "primary_entity_type": ENTITY_TYPE,
            "narrative": [{"path": "narrative", "where": None}], "persons": {}, "reporter": "reporter",
            "origin_ref": "origin_ref", "origin_site": "origin_site", "required": ["narrative"]}


def _rules() -> dict[str, Any]:
    rules = {f"{p}_multi_site": {"label": f"{predicate_label(p)} at several sites", "entity_type": ENTITY_TYPE,
                                 "predicate": p, "min_sites": 2, "min_count_per_site": 1, "window_weeks": 8}
             for p in CHAIN_PREDICATES}
    return {"rules": rules}


def _questions() -> dict[str, Any]:
    return {"templates": {TEMPLATE_ID: {"text": TEMPLATE_TEXT, "entity_types": [ENTITY_TYPE], "predicates": None}},
            "pushdown": dict(PUSHDOWN)}


def _narratives() -> dict[str, Any]:
    out = {}
    for p in PACK_PREDICATES:
        phrases = predicate_lexicon(p)
        affirmed = [f"{phrase[0].upper()}{phrase[1:]} for {{{ENTITY_TYPE}}}." for phrase in phrases[:2]]
        negated = [f"{phrases[0][0].upper()}{phrases[0][1:]} for {{{ENTITY_TYPE}}} not."]
        out[p] = {"affirmed": affirmed, "negated": negated}
    return out


def _generator(sites: Sequence[str], ids: Sequence[str]) -> dict[str, Any]:
    return {
        "start": START,
        "sites": [{"id": sid, "label": f"EMERGENCE site {sid} (synthetic)", "language": LANGUAGE,
                   "weekly_volume": [1, 1], "reporters": 1} for sid in sites],
        "forwarding_rate": 0.0, "mixed_language_rate": 0.0, "negated_sentence_rate": 0.0,
        "second_predicate_rate": 0.0, "entity_sentence_rate": 0.0, "zero_claim_rate": 0.0,
        "predicate_weights": {p: 1 for p in PACK_PREDICATES},
        "codes": {"specific_rate": 0.0, "generic_rate": 0.0, "generic_code": GENERIC_CODE},
        "fill_rates": {ENTITY_TYPE: 1.0},
        "universe": {ENTITY_TYPE: sorted(ids)},
        "links": {},
        "surface": dict(SURFACE),
        "narratives": {LANGUAGE: _narratives()},
        "entity_sentences": {LANGUAGE: [f"The record concerns {{{ENTITY_TYPE}}}."]},
        "filler": {LANGUAGE: list(FILLER)},
        "persons": {},
        "reporter": {"kind": "pattern", "segments": [{"literal": "U"}, {"digit": [1, 6]}], "separator": None,
                     "case": "upper"},
        "master_data": {sid: {ENTITY_TYPE: "all"} for sid in sites},
    }


def _fixture(corpus: Corpus, rid: int, record: Mapping[str, Any]) -> dict[str, Any]:
    r = corpus.recs[rid]
    anchor = canonical_entity_id(corpus.entities[int(r["anchor"])])
    predicate = pack_predicate(PREDICATES[int(r["pred"])])
    gold = [{"entity_type": ENTITY_TYPE, "entity_id": anchor, "predicate": predicate,
             "negated": bool(predicate is not None and int(r["polarity"]) < 0)}]
    aux = int(r["aux"])
    if aux >= 0 and aux != int(r["anchor"]):
        gold.append({"entity_type": ENTITY_TYPE, "entity_id": canonical_entity_id(corpus.entities[aux]),
                     "predicate": None, "negated": False})
    return {"gold": gold, "record": dict(record)}


def pack_files(corpus: Corpus, *, fixtures: int = FIXTURE_RECORDS) -> dict[str, Any]:
    """Every pack file as a parsed object (the fixtures as a list of lines), from the corpus's entities, its org's
    site ids and its first ``fixtures`` records."""
    ids = entity_ids(corpus)
    sites = site_ids(corpus.org)
    records = build_records(corpus)[:fixtures]
    return {
        "pack.json": {"id": PACK_ID, "version": PACK_VERSION,
                      "title": "EMERGENCE benchmark world (synthetic adapter vocabulary)", "languages": [LANGUAGE],
                      "illustrative": True, "disclaimer": DISCLAIMER, "same_author_as_code": True},
        "vocabulary.json": _vocabulary(ids),
        "codes.json": _codes(),
        "aliases.json": {ENTITY_TYPE: {name: canonical_entity_id(name) for name in corpus.entities}},
        "mapping.json": _mapping(),
        "egress.json": dict(EGRESS),
        "detectors.json": {**DETECTOR_SETTINGS, **DETECTOR_BLOCKS},
        "rules.json": _rules(),
        "questions.json": _questions(),
        "followups.json": FOLLOWUPS,
        "generator.json": _generator(sites, ids),
        FIXTURES_FILE: [_fixture(corpus, rid, records[rid]) for rid in range(len(records))],
    }


def pack_bytes(files: Mapping[str, Any]) -> dict[str, bytes]:
    """Each file's bytes: canonical JSON plus a newline; the fixtures one canonical line per record."""
    out = {}
    for name in FILE_NAMES:
        obj = files[name]
        if name == FIXTURES_FILE:
            out[name] = "".join(canonical_dumps(line) + "\n" for line in obj).encode("utf-8")
        else:
            out[name] = (canonical_dumps(obj) + "\n").encode("utf-8")
    return out


def world_corpus(target_users: int = WORLD_TARGET_USERS, seed: int = WORLD_SEED) -> Corpus:
    return generate(target_users, seed)[1]


def write_pack(directory: str | Path = PACK_DIR, *, corpus: Corpus | None = None) -> dict[str, str]:
    """Write the pack into ``directory`` (created; existing pack files replaced) and return each file's sha256."""
    directory = Path(directory)
    corpus = corpus if corpus is not None else world_corpus()
    data = pack_bytes(pack_files(corpus))
    (directory / "fixtures").mkdir(parents=True, exist_ok=True)
    digests = {}
    for name in FILE_NAMES:
        path = directory / name
        path.write_bytes(data[name])
        digests[name] = sha256_hex(data[name])
    return digests


def read_pack_bytes(directory: str | Path = PACK_DIR) -> dict[str, bytes]:
    directory = Path(directory)
    return {name: (directory / name).read_bytes() for name in FILE_NAMES if (directory / name).exists()}


def load_emergence_pack(directory: str | Path = PACK_DIR) -> FrozenPack:
    """The pinned loader's full check of the pack directory (schemas, cross-references, hashes)."""
    return load_pack_dir(Path(directory), expected_id=PACK_ID)


def check_pack(directory: str | Path = PACK_DIR, *, corpus: Corpus | None = None) -> dict[str, Any]:
    """The pack at ``directory`` loads, and its bytes equal a fresh regeneration's, file by file."""
    corpus = corpus if corpus is not None else world_corpus()
    expected = pack_bytes(pack_files(corpus))
    actual = read_pack_bytes(directory)
    differing = sorted(name for name in FILE_NAMES if actual.get(name) != expected[name])
    problem = None
    pack = None
    try:
        pack = load_emergence_pack(directory)
    except PackError as err:
        problem = str(err)
    return {"ok": not differing and problem is None, "differing": differing, "loader_problem": problem,
            "hashes": pack.hashes() if pack is not None else None,
            "sha256": {name: sha256_hex(expected[name]) for name in FILE_NAMES}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.e2e_ingest.emergence_pack",
                                     description="Write or check the emergence pack (synthetic).")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write")
    w.add_argument("--out", default=str(PACK_DIR))
    c = sub.add_parser("check")
    c.add_argument("directory", nargs="?", default=str(PACK_DIR))
    args = parser.parse_args(argv)
    if args.command == "write":
        digests = write_pack(args.out)
        pack = load_emergence_pack(args.out)
        for name in FILE_NAMES:
            print(f"{name}: {digests[name]}")
        for name, value in pack.hashes().items():
            print(f"{name}: {value}")
        return 0
    result = check_pack(args.directory)
    print(canonical_dumps({k: v for k, v in result.items() if k != "sha256"}))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
