"""G5: evaluation. Plant specs and their refusals, the planted construction through the real EdgeSite, Boundary, HQ
store and G4 path, the baselines' inputs (S, R model-free, U, single_site, rules), the harness's pins and scorecard,
and determinism across processes.

Every world here is synthetic and same-author; every test works in a temporary directory and connects nowhere. Any
figure a test prints is from a synthetic, same-author world and is not a measurement.
"""
from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from unittest import mock

from mycelic.collective import stats
from mycelic.collective.detect.detectors import DetectorConfig, detect, result_bytes, run_detection
from mycelic.collective.detect.rules import series_key
from mycelic.collective.detect.store import CollectiveStore
from mycelic.collective.edge.extract import LexicalExtractor, codes_channel, sense
from mycelic.collective.edge.records import InputRow
from mycelic.collective.edge.site import build_cells
from mycelic.collective.evaluate import baselines as B
from mycelic.collective.evaluate import harness as H
from mycelic.collective.evaluate import plant as P
from mycelic.collective.jsonio import canonical_bytes, sha256_hex
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.connector import record_problems
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import BUILTIN_ROOT, PackError, load_pack
from tests.mycelic.test_collective_edge import _SQL_STATEMENT, Clock, db_rows, pack_copy, string_constants
from tests.mycelic.test_collective_guards import BUILTIN_PACKS, EVALUATE_FILES, path_snapshot
from tests.mycelic.test_collective_pushdown import B1_CONFIG_HASHES, G7_CONFIG_HASHES, R2_CONFIG_HASHES

ROOT = Path(__file__).resolve().parents[2]
DQ = load_pack("device_quality")
CI = load_pack("claims_integrity")
DQ_SMOKE = BUILTIN_ROOT / "device_quality" / "fixtures" / "plant_smoke.json"
CI_SMOKE = BUILTIN_ROOT / "claims_integrity" / "fixtures" / "plant_smoke.json"
DQ_SITES = [s["id"] for s in DQ.generator["sites"]]
DQ_SEED, CI_SEED = 11, 5
# per built-in pack: its smoke plant spec, the world seed its check uses and the end weeks a stale chain may have with
# eval_from 26 (device and it_incidents: stale_days 42, lag 14, so end <= 21; claims: 56 and 21, so end <= 20)
SMOKE_FIXTURES = {"device_quality": (DQ_SMOKE, DQ_SEED, (19, 20, 21)),
                  "claims_integrity": (CI_SMOKE, CI_SEED, (19, 20)),
                  "it_incidents": (BUILTIN_ROOT / "it_incidents" / "fixtures" / "plant_smoke.json", 7, (19, 20, 21))}
EVAL_FROM, EVAL_TO, WEEKS = 26, 51, 52
W = B.world_weeks(DQ.generator["start"], WEEKS)
R_MF_LABEL = ("R (model-free): the same detectors over record-level, unsuppressed counts built only from the pack's "
              "central_allowed_fields (structured codes and structured ids, never narrative). Not STRATEGY section "
              "6.1's R (the best central system, including a frontier model, reading the allowed fields); E2 (G6) "
              "approximates that with its central_allowed condition.")
SYNTHETIC_NOTE = "synthetic, same-author world; not a measurement"


def cli(main: Callable[[list[str]], int], argv: Sequence[Any]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def smoke(path: Path = DQ_SMOKE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def prereg_argv(pack: Any, runs: Path, run_id: str = "pre", *extra: Any, seeds: str = str(DQ_SEED)) -> list[Any]:
    ref = pack if isinstance(pack, str) else pack.directory
    return ["prereg", "--pack", ref, "--seeds", seeds, "--eval-from", EVAL_FROM, "--eval-to", EVAL_TO,
            "--tie-salt", "g5-smoke", "--detector-author", "Mycelic  Engineering", "--run-id", run_id,
            "--runs-dir", runs, "--allow-dirty", *extra]


def run_argv(prereg: Path, plant_file: Path, runs: Path, run_id: str, *extra: Any,
             seeds: str = str(DQ_SEED)) -> list[Any]:
    return ["run", "--prereg", prereg, "--plant", plant_file, "--seeds", seeds, "--run-id", run_id, "--runs-dir",
            runs, *extra]


def item_records(planted: P.Planted, spec: P.PlantSpec, item_id: str) -> list[dict[str, Any]]:
    """The planted records of one item (records are planted item by item, in spec order)."""
    start = 0
    for item in spec.items:
        n = planted.counts[item.id]
        if item.id == item_id:
            return list(planted.records[start:start + n])
        start += n
    raise KeyError(item_id)


def lb(n: int | None) -> int:
    return 1 if n is None else n


def assert_interval_blocks(test: unittest.TestCase, card: Mapping[str, Any], *, B: int = 10000, seed: int = 1) -> None:
    """B2: every channel block (the ablation's too) carries its four interval blocks with the specified shape: net
    recall over patterns; precision@40, AP and false alarms per week over the seeds with a value (null where none)."""
    blocks = dict(card["channels"])
    if card["ablation"] is not None:
        blocks.update(card["ablation"]["channels"])
    for name, block in blocks.items():
        with test.subTest(channel=name):
            for metric in H.INTERVAL_METRICS:
                ci = block[f"{metric}_ci"]
                values = ([r[metric] for r in block["per_seed"] if r[metric] is not None]
                          if metric != "recall_net" else card["patterns"])
                if metric in ("precision_at_40", "average_precision") and not block["ranked"]:
                    test.assertIsNone(ci)
                    continue
                test.assertIsNotNone(ci, metric)
                test.assertEqual((ci["B"], ci["seed"], ci["method"]),
                                 (B, f"x1:{seed}:{name}:{metric}", "cluster percentile"))
                test.assertEqual((ci["clusters"], ci["n_clusters"]),
                                 ("patterns" if metric == "recall_net" else "seeds", len(values)))
                if metric == "false_alarms_per_week":
                    test.assertLessEqual(abs(ci["estimate"] - block[metric]), 1e-12)
                else:
                    test.assertEqual(ci["estimate"], block[metric])
                test.assertLessEqual(ci["ci_low"], ci["estimate"] + 1e-12)
                test.assertLessEqual(ci["estimate"], ci["ci_high"] + 1e-12)


# =================================================================================================== plant specs

def base_spec() -> dict[str, Any]:
    return {"kind": "plant_spec", "schema_version": 1, "pack": "device_quality", "prereg_sha256": None,
            "planted_by": "planter", "planter_saw_detector_code": False, "notes": "",
            "patterns": [{"id": "p1", "entity_type": "component", "entity_id": "PRESSURE-SENSOR",
                          "predicate": "overheat", "sites": DQ_SITES[:2], "start_week": 30, "weeks": 4,
                          "rate_per_week": 2, "visibility": "narrative_only", "language": "en"}],
            "decoys": []}


def decoy(cls: str) -> dict[str, Any]:
    return copy.deepcopy(next(d for d in smoke()["decoys"] if d["class"] == cls))


def _set(path: Sequence[Any], value: Any) -> Callable[[dict[str, Any]], None]:
    def apply(spec: dict[str, Any]) -> None:
        node = spec
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return apply


def _with_decoy(cls: str, **changes: Any) -> Callable[[dict[str, Any]], None]:
    def apply(spec: dict[str, Any]) -> None:
        d = decoy(cls)
        d.update(changes)
        spec["decoys"].append(d)
    return apply


def _second_pattern(**changes: Any) -> Callable[[dict[str, Any]], None]:
    def apply(spec: dict[str, Any]) -> None:
        p = copy.deepcopy(spec["patterns"][0])
        p.update({"id": "p2", **changes})
        spec["patterns"].append(p)
    return apply


def _del(path: Sequence[Any]) -> Callable[[dict[str, Any]], None]:
    def apply(spec: dict[str, Any]) -> None:
        node = spec
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return apply


# (name, mutation, stage, path, problem); stage "parse" fails in parse_plant, "check" in check_plant
REFUSALS: tuple[tuple[str, Callable[[dict[str, Any]], None], str, str, str], ...] = (
    ("only unknown keys", lambda s: s.clear() or s.update({"x": 1}), "parse", "$", "unknown key"),
    ("unknown top key", _set(["extra"], 1), "parse", "$", "unknown key"),
    ("missing top key", _del(["notes"]), "parse", "$.notes", "missing key"),
    ("bool schema version", _set(["schema_version"], True), "parse", "$.schema_version", "wrong type"),
    ("kind", _set(["kind"], "spec"), "parse", "$.kind", "must be plant_spec"),
    ("schema version", _set(["schema_version"], 2), "parse", "$.schema_version", "must be 1"),
    ("wrong pack id", _set(["pack"], "claims_integrity"), "parse", "$.pack", "pack mismatch"),
    ("malformed prereg sha", _set(["prereg_sha256"], "ABC"), "parse", "$.prereg_sha256",
     "must be null or 64 lowercase hex"),
    ("planted_by empty", _set(["planted_by"], ""), "parse", "$.planted_by", "must be 1 to 80 printable characters"),
    ("planted_by control", _set(["planted_by"], "a\nb"), "parse", "$.planted_by",
     "must be 1 to 80 printable characters"),
    ("saw not bool", _set(["planter_saw_detector_code"], 0), "parse", "$.planter_saw_detector_code", "wrong type"),
    ("notes too long", _set(["notes"], "x" * 2001), "parse", "$.notes", "must be at most 2000 characters"),
    ("empty patterns", _set(["patterns"], []), "parse", "$.patterns", "must hold 1 to 500 items"),
    ("item unknown key", _set(["patterns", 0, "colour_zq"], "zq-marker"), "parse", "$.patterns[0]", "unknown key"),
    ("item missing key", _del(["patterns", 0, "language"]), "parse", "$.patterns[0].language", "missing key"),
    ("bad item id", _set(["patterns", 0, "id"], "P 1"), "parse", "$.patterns[0].id", "invalid id"),
    ("unknown entity type", _set(["patterns", 0, "entity_type"], "widget_zq"), "parse", "$.patterns[0].entity_type",
     "unknown entity type"),
    ("alias-only id outside the vocabulary", _set(["patterns", 0, "entity_id"], "WHEEL-HUB"), "parse",
     "$.patterns[0].entity_id", "unknown id"),
    ("id not canonical", _set(["patterns", 0], {**base_spec()["patterns"][0], "entity_type": "product",
                                                "entity_id": "sd-9", "predicate": "power_loss"}), "parse",
     "$.patterns[0].entity_id", "id not canonical"),
    ("unknown predicate", _set(["patterns", 0, "predicate"], "melt_zq"), "parse", "$.patterns[0].predicate",
     "unknown predicate"),
    ("sites not a list", _set(["patterns", 0, "sites"], "plant-ashvale"), "parse", "$.patterns[0].sites",
     "must be a list of site ids"),
    ("duplicate site", _set(["patterns", 0, "sites"], [DQ_SITES[0], DQ_SITES[0]]), "parse",
     "$.patterns[0].sites[1]", "duplicate site"),
    ("bool start week", _set(["patterns", 0, "start_week"], True), "parse", "$.patterns[0].start_week",
     "must be an int >= 0"),
    ("weeks 0", _set(["patterns", 0, "weeks"], 0), "parse", "$.patterns[0].weeks", "must be an int 1..520"),
    ("rate 0", _set(["patterns", 0, "rate_per_week"], 0), "parse", "$.patterns[0].rate_per_week",
     "must be an int 1..50"),
    ("rate 51", _set(["patterns", 0, "rate_per_week"], 51), "parse", "$.patterns[0].rate_per_week",
     "must be an int 1..50"),
    ("unknown visibility", _set(["patterns", 0, "visibility"], "text"), "parse", "$.patterns[0].visibility",
     "unknown visibility"),
    ("unknown language", _set(["patterns", 0, "language"], "xq"), "parse", "$.patterns[0].language",
     "unknown language"),
    ("no single-slot template", _set(["patterns", 0, "predicate"], "display_fault"), "parse", "$.patterns[0]",
     "no single-slot template"),
    ("no specific code", _set(["patterns", 0], {**base_spec()["patterns"][0], "entity_type": "product",
                                                "entity_id": "SD-9", "predicate": "malfunction_unspecified",
                                                "visibility": "codes_only"}), "parse", "$.patterns[0].predicate",
     "no specific code"),
    ("one-site pattern", _set(["patterns", 0, "sites"], DQ_SITES[:1]), "parse", "$.patterns[0].sites",
     "too few sites"),
    ("unknown decoy class", lambda s: s["decoys"].append({**decoy("echo_marked"), "class": "ghost_zq"}), "parse",
     "$.decoys[0].class", "unknown decoy class"),
    ("decoy without class", lambda s: s["decoys"].append({k: v for k, v in decoy("echo_marked").items()
                                                          if k != "class"}), "parse", "$.decoys[0].class",
     "missing key"),
    ("echo copies overlap the origin", _with_decoy("echo_marked", copy_sites=[DQ_SITES[0], DQ_SITES[1]]), "parse",
     "$.decoys[0].copy_sites", "sites overlap"),
    ("burst with two sites", _with_decoy("single_site_burst", sites=DQ_SITES[3:5]), "parse", "$.decoys[0].sites",
     "too many sites"),
    ("reporter with one site", _with_decoy("single_reporter", sites=DQ_SITES[1:2]), "parse", "$.decoys[0].sites",
     "too few sites"),
    ("one high-base-rate id", _with_decoy("high_base_rate_everywhere", entity_ids=["INFUSION-TUBING"]), "parse",
     "$.decoys[0].entity_ids", "too few entity ids"),
    ("near-miss ids equal", _with_decoy("near_miss_entity", near_miss_id="L20045"), "parse",
     "$.decoys[0].near_miss_id", "near-miss ids are equal"),
    ("near-miss more than 2 edits", _with_decoy("near_miss_entity", near_miss_id="L31008A"), "parse",
     "$.decoys[0].near_miss_id", "not a near miss"),
    ("near-miss on the same site", _with_decoy("near_miss_entity", near_miss_sites=[DQ_SITES[0]]), "parse",
     "$.decoys[0].near_miss_sites", "sites overlap"),
    ("two patterns on one key", _second_pattern(), "parse", "$.patterns[1]", "duplicate key"),
    ("pattern and decoy on one key", _with_decoy("echo_marked", entity_id="PRESSURE-SENSOR", predicate="overheat"),
     "parse", "$.decoys[0]", "duplicate key"),
    ("duplicate id", _with_decoy("echo_marked", id="p1"), "parse", "$.decoys[0].id", "duplicate id"),
    ("site outside the org", _set(["patterns", 0, "sites"], [DQ_SITES[0], "plant-zed"]), "check",
     "$.patterns[0].sites[1]", "unknown site"),
    ("id missing from a counted site's master data", _set(["patterns", 0], {
        **base_spec()["patterns"][0], "entity_type": "lot", "entity_id": "L10017", "predicate": "crack",
        "sites": [DQ_SITES[0], DQ_SITES[1]]}), "check", "$.patterns[0].sites[1]",
     "id not in master data of a counted site"),
    ("outside the world weeks", _set(["patterns", 0, "start_week"], 50), "check", "$.patterns[0].weeks",
     "outside the world weeks"),
    ("outside the evaluation weeks", _set(["patterns", 0, "start_week"], 20), "check", "$.patterns[0].start_week",
     "outside the evaluation weeks"),
    # device: stale at eval_from 26 needs 7 * (26 - end) + close_lag 14 > stale_days 42, so end <= 21, and the
    # whole chain in week 26's window (window_weeks 8) needs start >= 19
    ("stale chain not stale", _with_decoy("stale_chain", start_week=19, weeks=4), "check",
     "$.decoys[0].start_week", "not stale at the first evaluation week"),
    ("stale chain before the first window", _with_decoy("stale_chain", start_week=18, weeks=4), "check",
     "$.decoys[0].start_week", "not inside the first evaluation week's window"),
    ("stale chain wholly before the first window", _with_decoy("stale_chain", start_week=14, weeks=6), "check",
     "$.decoys[0].start_week", "not inside the first evaluation week's window"),
    ("high base rate at too few sites", _with_decoy("high_base_rate_everywhere", sites=DQ_SITES[:3]), "check",
     "$.decoys[0].sites", "too few sites for a high base rate"),
    ("single reporter below k", _with_decoy("single_reporter", rate_per_week=2), "check",
     "$.decoys[0].rate_per_week", "rate below k"),
)


class PlantCase(unittest.TestCase):
    world_dq: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.world_dq = generate(DQ, DQ_SEED, 6, WEEKS)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, spec: P.PlantSpec, pack: Any = DQ, world: Any = None) -> None:
        world = world or self.world_dq
        P.check_plant(spec, pack, site_ids=list(world.params["site_ids"]), master_data=world.master_data,
                      n_weeks=WEEKS, eval_from=EVAL_FROM, eval_to=EVAL_TO)


class PlantSpecTests(PlantCase):
    def test_every_builtin_smoke_fixture_parses_and_checks(self) -> None:
        for pid, (path, seed, _) in SMOKE_FIXTURES.items():
            pack = load_pack(pid)
            with self.subTest(pack=pack.id):
                spec = P.load_plant(path, pack)
                self.check(spec, pack, generate(pack, seed, 6, WEEKS))
                self.assertEqual((len(spec.patterns), len(spec.decoys)), (3, 8))
                self.assertEqual(sorted(d.decoy_class for d in spec.decoys), sorted(P.DECOY_CLASSES))
                self.assertEqual(spec.sha256, sha256_hex(canonical_bytes(smoke(path))))
                self.assertTrue(spec.planter_saw_detector_code)
                self.assertFalse(P.is_blind(spec, "someone else"))
                self.assertEqual({p.visibility for p in spec.patterns}, {"narrative_only"})

    def test_the_loader_accepts_plant_files_and_hashes_none_of_them(self) -> None:
        for pid in BUILTIN_PACKS:
            with self.subTest(pack=pid):
                base = load_pack(pid)
                copy_pack = pack_copy(self.tmp, pid)
                fixtures = Path(copy_pack.directory) / "fixtures"
                self.assertEqual(copy_pack.hashes(), base.hashes())
                (fixtures / "plant_smoke.json").write_text('{"changed": true}', encoding="utf-8")
                (fixtures / "plant_x1_round_2.json").write_text("not even json", encoding="utf-8")
                self.assertEqual(load_pack(copy_pack.directory).hashes(), base.hashes())
                for name in ("more.jsonl", "plant.json", "plant_UPPER.json", "plant_x.jsonl"):
                    (fixtures / name).write_text("{}", encoding="utf-8")
                    with self.assertRaises(PackError) as cm:
                        load_pack(copy_pack.directory)
                    self.assertEqual((cm.exception.file, cm.exception.problem), (f"fixtures/{name}", "unexpected file"))
                    (fixtures / name).unlink()
        for pack in (DQ, CI):       # B1's followups.json template key changed only config_hash
            self.assertNotEqual(pack.config_hash, R2_CONFIG_HASHES[pack.id])
            self.assertNotEqual(pack.config_hash, G7_CONFIG_HASHES[pack.id])
        self.assertEqual(DQ.hashes(), {
            "config_hash": B1_CONFIG_HASHES["device_quality"],
            "vocabulary_hash": "e46f521154ce94f42136319ade62cebb5c65515ab90a057470495a606f225901",
            "detector_hash": "c9462f62aa90245f2c7cee50078d337554bded58c4630cda7becbf7a8048c7ec",
            "fixtures_hash": "dc4b70b7044b1094baaa669fb5a3582eeae91af290213e13b94180791db5656d"})
        self.assertEqual(CI.hashes(), {
            "config_hash": B1_CONFIG_HASHES["claims_integrity"],
            "vocabulary_hash": "028b7603f2b6ef3bba203dee299ea8b001880ef89cd4b276de8513d2a1a301f3",
            "detector_hash": "2041fe9b3e141a5603836d893c97d5671eb21cbb3da611969efbd0c2c4514b84",
            "fixtures_hash": "a2e8936b4be26683f0860c8c8799e701f9aedfb78ea167d5420ac891111b8d80"})

    def test_every_refusal_names_its_path_and_a_fixed_problem(self) -> None:
        self.check(P.parse_plant(base_spec(), DQ))
        for name, mutate, stage, path, problem in REFUSALS:
            with self.subTest(name=name):
                spec = base_spec()
                mutate(spec)
                with self.assertRaises(P.PlantError) as cm:
                    parsed = P.parse_plant(spec, DQ)
                    self.assertEqual(stage, "check", "parse_plant accepted a spec it should refuse")
                    self.check(parsed)
                self.assertEqual((cm.exception.path, cm.exception.problem), (path, problem))
                self.assertEqual(str(cm.exception), f"plant: {path}: {problem}")

    def test_refusals_that_need_a_pack_copy(self) -> None:
        cases = (
            # an alias-only id without any alias: no surface to write it in a narrative
            ({("vocabulary.json", "entity_types", "component", "ids"):
              [*DQ.entity_types["component"].ids, "WHEEL-HUB"]},
             {"entity_id": "WHEEL-HUB"}, "$.patterns[0].entity_id", "no alias surface"),
            # a type the record mapping does not carry cannot be planted as a structured code
            ({("vocabulary.json", "entity_types", "fastener"): {
                "label": "Fastener", "id_format": None, "separator": None, "case": "upper",
                "strip_leading_zeros": False, "exact_match_metric": False, "egress": True, "ids": ["HINGE-PIN"]},
              ("egress.json", "egress_entity_types"): [*DQ.egress.egress_entity_types, "fastener"],
              # G6: every egress entity type needs a question template with predicates null
              ("questions.json", "templates", "count_predicate_on_entity", "entity_types"):
                  [*DQ.questions["count_predicate_on_entity"].entity_types, "fastener"],
              ("aliases.json", "fastener"): {"hinge pin": "HINGE-PIN"},
              ("generator.json", "universe", "fastener"): ["HINGE-PIN"]},
             {"entity_type": "fastener", "entity_id": "HINGE-PIN", "predicate": "crack", "visibility": "codes_only"},
             "$.patterns[0].entity_type", "not a structured entity type"),
            # a type that may not leave the sites
            ({("vocabulary.json", "entity_types", "supplier", "egress"): False,
              ("egress.json", "egress_entity_types"): ["component", "lot", "product"]},
             {"entity_type": "supplier", "entity_id": "V1001"}, "$.patterns[0].entity_type",
             "entity type does not leave the sites"),
        )
        for edits, changes, path, problem in cases:
            with self.subTest(problem=problem):
                pack = pack_copy(self.tmp, "device_quality", edits)
                spec = base_spec()
                spec["patterns"][0].update(changes)
                with self.assertRaises(P.PlantError) as cm:
                    P.parse_plant(spec, pack)
                self.assertEqual((cm.exception.path, cm.exception.problem), (path, problem))

    def test_errors_hold_no_value(self) -> None:
        for name, mutate, _, _, _ in REFUSALS:
            spec = base_spec()
            mutate(spec)
            with self.subTest(name=name):
                try:
                    self.check(P.parse_plant(spec, DQ))
                except P.PlantError as err:
                    for value in ("WHEEL-HUB", "sd-9", "zq", "xq", "plant-zed", "L10017", "L31008A", "P 1", "ABC"):
                        self.assertNotIn(value, str(err))
        for not_an_object in ([], "spec", None):
            with self.assertRaises(P.PlantError) as cm:
                P.parse_plant(not_an_object, DQ)
            self.assertEqual((cm.exception.path, cm.exception.problem), ("$", "must be an object"))
        with self.assertRaises(P.PlantError) as cm:
            P.load_plant(self.tmp / "absent-secret-name.json", DQ)
        self.assertEqual((cm.exception.path, cm.exception.problem), ("$", "cannot read (FileNotFoundError)"))
        self.assertNotIn("absent-secret-name", str(cm.exception))
        bad = self.tmp / "dup.json"
        bad.write_text('{"kind": "plant_spec", "kind": "x"}', encoding="utf-8")
        with self.assertRaises(P.PlantError) as cm:
            P.load_plant(bad, DQ)
        self.assertEqual((cm.exception.path, cm.exception.problem), ("$.kind", "duplicate_key"))

    def test_planting_is_deterministic_per_spec_and_seed(self) -> None:
        spec = P.load_plant(DQ_SMOKE, DQ)
        a = P.plant(self.world_dq, spec, DQ)
        b = P.plant(generate(DQ, DQ_SEED, 6, WEEKS), spec, DQ)
        self.assertEqual(canonical_bytes(list(a.records)), canonical_bytes(list(b.records)))
        other = P.plant(generate(DQ, DQ_SEED + 1, 6, WEEKS), spec, DQ)
        self.assertNotEqual(canonical_bytes(list(a.records)), canonical_bytes(list(other.records)))
        self.assertEqual(dict(a.counts), dict(other.counts))
        changed = P.parse_plant({**smoke(), "notes": "another spec"}, DQ)
        self.assertNotEqual(canonical_bytes(list(P.plant(self.world_dq, changed, DQ).records)),
                            canonical_bytes(list(a.records)))
        self.assertEqual(sum(a.counts.values()), len(a.records))
        self.assertEqual(a.counts["p1"], 3 * 8 * 2)
        self.assertEqual(a.counts["d-echo"], 8 * 3 * 3)                  # origins plus two copies each
        self.assertEqual(a.counts["d-base-rate"], 3 * 5 * 6 * 3)
        refs = [r["record_ref"] for r in a.records]
        self.assertEqual(len(set(refs)), len(refs))
        self.assertTrue(all("-plant-" in ref for ref in refs))

    def test_planted_records_pass_the_record_check_and_carry_only_the_planted_claim(self) -> None:
        canon = Canonicaliser(DQ, known={t: tuple(DQ.generator["universe"][t]) for t in ("lot", "product",
                                                                                         "supplier")})
        lexical = LexicalExtractor(DQ, canon)
        for visibility in P.VISIBILITIES:
            with self.subTest(visibility=visibility):
                raw = base_spec()
                raw["patterns"][0].update({"entity_type": "product", "entity_id": "SD-12", "predicate": "overheat",
                                           "visibility": visibility})
                spec = P.parse_plant(raw, DQ)
                planted = P.plant(self.world_dq, spec, DQ)
                self.assertEqual(len(planted.records), 2 * 4 * 2)
                for r in planted.records:
                    self.assertEqual(record_problems(r, DQ), [])
                    self.assertTrue(r["synthetic"])
                    self.assertEqual((r["origin_ref"], r["origin_site"]), (None, None))
                    _, _, claims = sense(r, DQ, canon, lexical)
                    pairs = {(c.entity_type, c.entity_id, c.predicate, c.channel) for c in claims}
                    if visibility == "narrative_only":
                        self.assertEqual(r["codes"], [])
                        self.assertTrue(all(v == [] for v in r["entities"].values()))
                        self.assertEqual(pairs, {("product", "SD-12", "overheat", "text_only")})
                    elif visibility == "codes_only":
                        self.assertEqual(len(r["codes"]), 1)
                        self.assertTrue(DQ.codes[r["codes"][0]].specific)
                        self.assertEqual(r["entities"]["product"], ["SD-12"])
                        self.assertEqual(pairs, {("product", "SD-12", "overheat", "codes")})
                    else:
                        self.assertEqual(pairs, {("product", "SD-12", "overheat", "codes")})
                        self.assertIn("SD-12", r["narrative"])

    def test_narratives_are_unique_except_duplicates_and_copies(self) -> None:
        spec = P.load_plant(DQ_SMOKE, DQ)
        planted = P.plant(self.world_dq, spec, DQ)
        world_narratives = {r["narrative"] for r in self.world_dq.records}
        independent = [r for r in planted.records if r["origin_ref"] is None]
        dup = item_records(planted, spec, "d-dup")
        self.assertEqual(len({r["narrative"] for r in dup}), 1)
        unmarked = item_records(planted, spec, "d-copies")
        origins = [r for r in unmarked if r["site"] in spec.decoys[2].sites]
        copies = [r for r in unmarked if r["site"] not in spec.decoys[2].sites]
        self.assertEqual(len(copies), 2 * len(origins))
        rest = [r for r in independent if r not in dup and r not in copies]
        self.assertEqual(len({r["narrative"] for r in rest}), len(rest))
        self.assertFalse({r["narrative"] for r in planted.records} & world_narratives)

    def test_copies_reporters_and_near_misses(self) -> None:
        spec = P.load_plant(DQ_SMOKE, DQ)
        planted = P.plant(self.world_dq, spec, DQ)
        echo = spec.decoys[0]
        records = item_records(planted, spec, "d-echo")
        origins = {r["record_ref"]: r for r in records if r["site"] in echo.sites}
        for r in records:
            if r["site"] in echo.copy_sites:
                origin = origins[r["origin_ref"]]
                self.assertEqual(r["origin_site"], origin["site"])
                for key in ("narrative", "codes", "entities", "persons", "reporter", "language"):
                    self.assertEqual(r[key], origin[key])
                self.assertEqual(B.iso_week(r["received_date"]), B.iso_week(origin["received_date"]))
        for r in item_records(planted, spec, "d-copies"):
            self.assertEqual((r["origin_ref"], r["origin_site"]), (None, None))
        reporter = spec.decoys[3]
        records = item_records(planted, spec, "d-reporter")
        for site in reporter.sites:
            self.assertEqual(len({r["reporter"] for r in records if r["site"] == site}), 1)
        near = spec.decoys[7]
        canon = Canonicaliser(DQ)
        for value in (near.entity_ids[0], near.near_miss_id):
            self.assertEqual(canon.resolve_exact("lot", value).entity_id, value)
        for r in item_records(planted, spec, "d-near-miss"):
            expected = near.entity_ids[0] if r["site"] in near.sites else near.near_miss_id
            self.assertIn(f"lot {expected} ", r["narrative"])
        self.assertEqual(P.edit_distance(near.entity_ids[0], near.near_miss_id), 1)
        self.assertEqual((P.edit_distance("", "ab"), P.edit_distance("kitten", "sitting")), (2, 3))

    def test_a_site_without_background_records_is_refused(self) -> None:
        world = generate(DQ, DQ_SEED, 2, WEEKS)
        raw = base_spec()
        raw["patterns"][0]["sites"] = [DQ_SITES[0], DQ_SITES[4]]
        with self.assertRaises(P.PlantError) as cm:
            P.plant(world, P.parse_plant(raw, DQ), DQ)
        self.assertEqual((cm.exception.path, cm.exception.problem), ("$", "site has no background records"))

    def test_labels_windows_watch_spans_and_quiet_rules(self) -> None:
        spec = P.load_plant(DQ_SMOKE, DQ)
        labels = P.labels_doc(spec, DQ, weeks=W, eval_from=EVAL_FROM, eval_to=EVAL_TO, grace_weeks=4)
        self.assertEqual((labels["kind"], labels["plant_sha256"], labels["seeds"]), ("plant_labels", spec.sha256, []))
        p1 = labels["patterns"][0]
        self.assertEqual((p1["start_index"], p1["end_index"], p1["found_from"], p1["found_to"]),
                         (30, 37, W[30], W[41]))
        self.assertEqual(p1["key"], "component:PRESSURE-SENSOR:overheat")
        clipped = P.labels_doc(spec, DQ, weeks=W, eval_from=EVAL_FROM, eval_to=40, grace_weeks=4)["patterns"][0]
        self.assertEqual(clipped["found_to"], W[40])
        by_id = {d["id"]: d for d in labels["decoys"]}
        self.assertEqual((by_id["d-echo"]["watch_from"], by_id["d-echo"]["watch_to"]), (W[30], W[41]))
        self.assertEqual((by_id["d-echo"]["quiet_from"], by_id["d-echo"]["quiet_to"]), (W[23], W[41]))
        self.assertEqual(by_id["d-echo"]["quiet_rule"], "other_sites_at_most_one")
        self.assertEqual(by_id["d-echo"]["sites"], [DQ_SITES[0]])
        self.assertEqual(by_id["d-echo"]["planted_sites"], DQ_SITES[:3])
        self.assertEqual(by_id["d-copies"]["sites"], [DQ_SITES[1], DQ_SITES[2], DQ_SITES[4]])
        stale = by_id["d-stale"]
        # weeks 19 to 21, wholly inside week 26's window; watch from eval_from to 21 + 8 - 1 + 4 = 32
        self.assertEqual((stale["start_index"], stale["end_index"]), (19, 21))
        self.assertEqual((stale["watch_from"], stale["watch_to"]), (W[26], W[32]))
        self.assertEqual((stale["quiet_from"], stale["quiet_to"], stale["quiet_rule"]), (W[22], W[32], "all_sites_zero"))
        for name in ("d-reporter", "d-base-rate", "d-copies"):
            self.assertEqual((by_id[name]["quiet_from"], by_id[name]["quiet_rule"]), (None, None))
        self.assertEqual(len(by_id["d-base-rate"]["keys"]), 3)
        self.assertEqual(by_id["d-near-miss"]["keys"], ["lot:L20045:contamination", "lot:L20046:contamination"])
        self.assertEqual(by_id["d-near-miss"]["sites"], [DQ_SITES[0], DQ_SITES[3]])

    def test_a_stale_chain_is_stale_at_the_first_evaluation_week_and_inside_its_window(self) -> None:
        # audit r3: a chain stale only by calendar days (7 * (eval_from - end) > stale_days) sat outside every window
        # of its watch span, so window arithmetic, not G4's stale filter, kept it quiet. Now its last week must be
        # stale at eval_from's own as_of (at least Sunday + close_lag_days) and its first week inside eval_from's
        # window. Device (stale_days 42, lag 14): end <= 21; claims (stale_days 56, lag 21): end <= 20; both start >= 19
        for pid, (path, seed, legal_ends) in SMOKE_FIXTURES.items():
            pack = load_pack(pid)
            world = generate(pack, seed, 6, WEEKS)
            raw = json.loads(path.read_text(encoding="utf-8"))
            index = next(i for i, d in enumerate(raw["decoys"]) if d["class"] == "stale_chain")
            for start in range(14, EVAL_FROM):
                for weeks in range(1, EVAL_FROM - start + 1):
                    end = start + weeks - 1
                    with self.subTest(pack=pack.id, start=start, end=end):
                        raw["decoys"][index].update(start_week=start, weeks=weeks)
                        spec = P.parse_plant(raw, pack)
                        if start >= EVAL_FROM - 7 and end in legal_ends:
                            self.check(spec, pack, world)
                            continue
                        with self.assertRaises(P.PlantError) as cm:
                            self.check(spec, pack, world)
                        self.assertEqual(cm.exception.path, f"$.decoys[{index}].start_week")
                        self.assertEqual(cm.exception.problem,
                                         "not stale at the first evaluation week" if end > legal_ends[-1]
                                         else "not inside the first evaluation week's window")

    def test_is_blind_normalises_names(self) -> None:
        spec = P.parse_plant({**base_spec(), "planted_by": "  Ada   LOVELACE "}, DQ)
        self.assertFalse(P.is_blind(spec, "ada lovelace"))
        self.assertFalse(P.is_blind(spec, "Ada\tLovelace"))
        self.assertTrue(P.is_blind(spec, "Mycelic Engineering"))
        saw = P.parse_plant({**base_spec(), "planter_saw_detector_code": True}, DQ)
        self.assertFalse(P.is_blind(saw, "Mycelic Engineering"))


# =================================================================================================== construction

class ConstructionCase(unittest.TestCase):
    """Runs a smoke spec once through ``harness main`` (prereg, then run) and keeps the run files and work dir."""

    pack: Any
    card: dict[str, Any]

    @classmethod
    def run_smoke(cls, pack: Any, plant_file: Path, seed: int, *extra: str) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.runs = cls.tmp / "runs"
        cls.seed = seed
        cls.plant_file = plant_file
        code, out, err = cli(H.main, prereg_argv(pack, cls.runs, seeds=str(seed)))
        assert code == 0, err
        cls.prereg_path = cls.runs / "x1" / "pre" / "prereg.json"
        code, out, err = cli(H.main, run_argv(cls.prereg_path, plant_file, cls.runs, "smoke", "--allow-dirty", *extra,
                                              seeds=str(seed)))
        assert code == 0, err
        cls.out = out
        cls.run_dir = cls.runs / "x1" / "smoke"
        cls.card = json.loads((cls.run_dir / "scorecard.json").read_text(encoding="utf-8"))
        cls.labels = json.loads((cls.run_dir / "labels.json").read_text(encoding="utf-8"))
        cls.work = cls.run_dir / "work" / f"seed-{seed}" / "planted"
        loaded = pack if not isinstance(pack, str) else load_pack(pack)
        cls.pack = loaded
        cls.spec = P.load_plant(plant_file, loaded)
        cls.world = generate(loaded, seed, 6, WEEKS)
        cls.planted = P.plant(cls.world, cls.spec, loaded)
        cls.site_ids = list(cls.world.params["site_ids"])
        cls.org = B.org_for_sites(cls.site_ids, B.EVAL_ENTERPRISE)
        cls.as_of = B.closing_date(loaded, W[-1])
        store = CollectiveStore(cls.work / "hq" / "collective.sqlite3", loaded, cls.org, clock=Clock())
        try:
            cls.x = detect(store, as_of=cls.as_of, run_channel="X", tie_salt="g5-smoke")
            cls.bundles, cls.cells = store.detection_inputs(cls.as_of, "X")
        finally:
            store.close()
        print(f"\n[{SYNTHETIC_NOTE}] {out.strip()}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def candidate(self, key: str) -> dict[str, Any] | None:
        return next((c for c in self.x["candidates"] if c["key"] == key), None)

    def assert_only_the_stale_filter_keeps_it_quiet(self) -> None:
        """Audit r3: the stale chain was a candidate while fresh (burn-in), has none in its watch span, and with G4's
        stale filter disabled it is a candidate there, so the filter, not window arithmetic, keeps it from being one.
        X1 scores it on candidacy as well as alerts: with the filter disabled it raises no alert in its watch span (the
        cooldown after its fresh alert is never released), so only ``candidate`` and ``failed`` show the broken
        filter."""
        stale = next(d for d in self.spec.decoys if d.decoy_class == "stale_chain")
        label = self.label(stale.id)

        def watched(weeks: Sequence[str]) -> list[str]:
            return [w for w in weeks if label["watch_from"] <= w <= label["watch_to"]]

        def outcome(card: Mapping[str, Any], channel: str) -> dict[str, Any]:
            decoy = next(d for d in card["decoys"] if d["id"] == stale.id)
            return next(o for o in decoy["outcomes"] if o["channel"] == channel)

        self.assertEqual(label["watch_from"], self.card["world"]["eval_from_week"])
        self.assertEqual(self.decoy_card(stale.id)["quiet_elsewhere"], [{"seed": self.seed, "quiet": True}])
        c = self.candidate(stale.keys[0])
        self.assertTrue([w for w in c["candidate_weeks"] if w < label["watch_from"]])
        self.assertTrue([w for w in c["alert_weeks"] if w < label["watch_from"]])
        self.assertFalse(watched(c["candidate_weeks"]))
        self.assertEqual(outcome(self.card, "X"), {"seed": self.seed, "channel": "X", "alerted": False,
                                                   "first_alert_week": None, "candidate": False,
                                                   "first_candidate_week": None, "failed": False})
        for name in ("rules", "single_site"):
            self.assertIsNone(outcome(self.card, name)["candidate"])
        self.assertEqual(self.card["channels"]["X"]["decoys_failed"]["stale_chain"], 0)
        with mock.patch("mycelic.collective.detect.detectors._stale", return_value=False):
            unfiltered = run_detection(self.pack, self.org, bundles=self.bundles, cells=self.cells,
                                       as_of=self.as_of, run_channel="X", tie_salt="g5-smoke")
            code, _, err = cli(H.main, run_argv(self.prereg_path, self.plant_file, self.runs, "stale-filter-off",
                                                "--allow-dirty", seeds=str(self.seed)))
        self.assertEqual(code, 0, err)
        c = next(c for c in unfiltered["candidates"] if c["key"] == stale.keys[0])
        self.assertTrue(watched(c["candidate_weeks"]))
        self.assertFalse(watched(c["alert_weeks"]))
        broken = json.loads((self.runs / "x1" / "stale-filter-off" / "scorecard.json").read_text(encoding="utf-8"))
        self.assertEqual(outcome(broken, "X"), {"seed": self.seed, "channel": "X", "alerted": False,
                                                "first_alert_week": None, "candidate": True,
                                                "first_candidate_week": watched(c["candidate_weeks"])[0],
                                                "failed": True})
        self.assertEqual((broken["channels"]["X"]["decoys_alerted"]["stale_chain"],
                          broken["channels"]["X"]["decoys_failed"]["stale_chain"]), (0, 1))

    def decoy_card(self, decoy_id: str) -> dict[str, Any]:
        return next(d for d in self.card["decoys"] if d["id"] == decoy_id)

    def label(self, decoy_id: str) -> dict[str, Any]:
        return next(d for d in self.labels["decoys"] if d["id"] == decoy_id)

    def site_db(self, site: str) -> Path:
        return self.work / "edge" / f"site-{site}.sqlite3"


class PlantedConstructionTests(ConstructionCase):
    @classmethod
    def setUpClass(cls) -> None:
        tmp = Path(tempfile.mkdtemp())
        cls._pack_tmp = tmp
        pack = pack_copy(tmp, "device_quality", {("rules.json", "rules"): {}})
        cls.run_smoke(pack, DQ_SMOKE, DQ_SEED, "--ablation-k1")

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        shutil.rmtree(cls._pack_tmp)

    def test_x_finds_the_three_patterns_within_the_budget(self) -> None:
        x = self.card["channels"]["X"]
        self.assertEqual((x["units"], x["found"], x["recall"]), (3, 3, 1.0))
        budget = self.pack.detectors["alert_budget_per_week"]
        self.assertTrue(all(len(w["alerts"]) <= budget for w in self.x["weeks"]))
        for p in self.labels["patterns"]:
            c = self.candidate(p["key"])
            self.assertTrue(any(p["found_from"] <= w <= p["found_to"] for w in c["alert_weeks"]), p["id"])
        self.assertIn("(synthetic, internal only, not a measurement)", self.out)

    def test_structural_decoys_are_quiet_elsewhere_and_raise_no_x_alert(self) -> None:
        for d in self.spec.decoys:
            if d.decoy_class not in P.STRUCTURAL_CLASSES:
                continue
            with self.subTest(decoy=d.id):
                card, label = self.decoy_card(d.id), self.label(d.id)
                self.assertEqual(card["quiet_elsewhere"], [{"seed": self.seed, "quiet": True}])
                self.assertEqual(H.quiet_elsewhere(d, label, self.cells), True)
                x = next(o for o in card["outcomes"] if o["channel"] == "X")
                self.assertFalse(x["alerted"])
                self.assertFalse(x["failed"])
                for key in d.keys:
                    c = self.candidate(key)
                    weeks = c["alert_weeks"] if c else []
                    self.assertFalse([w for w in weeks if label["watch_from"] <= w <= label["watch_to"]])

    def test_marked_copies_are_forwarded_in_and_add_nothing_to_their_cells(self) -> None:
        echo = self.spec.decoys[0]
        copies = [r for r in item_records(self.planted, self.spec, echo.id) if r["site"] in echo.copy_sites]
        self.assertEqual(len(copies), 2 * 8 * 3)
        for site in echo.copy_sites:
            refs = [r["record_ref"] for r in copies if r["site"] == site]
            marks = "(" + ",".join("?" * len(refs)) + ")"
            rows = db_rows(self.site_db(site), f"SELECT forwarded_in FROM records WHERE record_ref IN {marks} "
                                               "ORDER BY record_ref", tuple(refs))
            self.assertEqual(rows, [(1,)] * len(refs))
            counted = db_rows(self.site_db(site), "SELECT COUNT(*) FROM claims c JOIN records r ON r.record_ref = "
                                                  f"c.record_ref WHERE r.forwarded_in = 0 AND r.record_ref IN {marks}",
                              tuple(refs))
            self.assertEqual(counted, [(0,)])

    def test_same_site_duplicates_share_one_root_and_their_cells_have_suppressed_roots(self) -> None:
        dup = self.spec.decoys[1]
        site = dup.sites[0]
        refs = [r["record_ref"] for r in item_records(self.planted, self.spec, dup.id)]
        marks = "(" + ",".join("?" * len(refs)) + ")"
        roots = db_rows(self.site_db(site), f"SELECT DISTINCT root_ref FROM records WHERE record_ref IN {marks} "
                                            "ORDER BY root_ref", tuple(refs))
        self.assertEqual(len(roots), 1)
        self.assertIn(roots[0][0], refs)
        key = (dup.entity_type, dup.entity_ids[0], dup.predicate)
        planted_weeks = W[dup.start_week:dup.end_week + 1]
        cells = [c for c in self.cells if (c.entity_type, c.entity_id, c.predicate) == key and c.site == site
                 and c.channel == "text_only" and c.iso_week in planted_weeks]
        self.assertEqual(len(cells), dup.weeks)
        for c in cells:
            self.assertIsNotNone(c.n)
            self.assertGreaterEqual(c.n, dup.rate_per_week)
            self.assertIsNone(c.n_roots)

    def test_near_miss_ids_stay_two_keys_each_at_its_one_site(self) -> None:
        near = self.spec.decoys[7]
        records = item_records(self.planted, self.spec, near.id)
        for value, site in ((near.entity_ids[0], near.sites[0]), (near.near_miss_id, near.near_miss_sites[0])):
            refs = [r["record_ref"] for r in records if r["site"] == site]
            self.assertEqual(len(refs), near.weeks * near.rate_per_week)
            marks = "(" + ",".join("?" * len(refs)) + ")"
            claims = db_rows(self.site_db(site), "SELECT DISTINCT entity_type, entity_id, predicate FROM claims "
                                                 f"WHERE record_ref IN {marks} ORDER BY entity_id", tuple(refs))
            self.assertEqual(claims, [(near.entity_type, value, near.predicate)])
        self.assertNotEqual(*near.keys)

    def test_only_the_stale_filter_keeps_the_stale_chain_out_of_its_watch_span(self) -> None:
        self.assert_only_the_stale_filter_keeps_it_quiet()

    def test_penalty_decoys_carry_their_flags_and_their_alerts_are_reported(self) -> None:
        reporter = self.spec.decoys[3]
        label = self.label(reporter.id)
        c = self.candidate(reporter.keys[0])
        snapshot = c["snapshot"]
        self.assertTrue(label["watch_from"] <= snapshot["week"] <= label["watch_to"])
        self.assertTrue(set(reporter.sites) <= set(snapshot["flags"]["few_reporters_sites"]))
        base_rate = self.spec.decoys[5]
        label = self.label(base_rate.id)
        for key in base_rate.keys:
            with self.subTest(key=key):
                snapshot = self.candidate(key)["snapshot"]
                self.assertTrue(label["watch_from"] <= snapshot["week"] <= label["watch_to"])
                self.assertTrue(snapshot["flags"]["high_base_rate"])
        flags = {f["key"]: f["flags"] for item in self.decoy_card(base_rate.id)["flags_at_detection"]
                 for f in item["flags"]}
        self.assertTrue(all(f is None or f["high_base_rate"] for f in flags.values()))
        for cls in P.PENALTY_CLASSES:
            self.assertIsInstance(self.card["channels"]["X"]["decoys_alerted"][cls], int)

    def test_one_site_of_the_two_site_pattern_alone_gives_no_candidate(self) -> None:
        p2 = next(p for p in self.labels["patterns"] if p["id"] == "p2")
        self.assertEqual(len(p2["sites"]), 2)
        c = self.candidate(p2["key"])
        self.assertTrue([w for w in c["candidate_weeks"] if p2["found_from"] <= w <= p2["found_to"]])
        dropped = [cell for cell in self.cells
                   if not (cell.site == p2["sites"][1] and series_key(cell.entity_type, cell.entity_id,
                                                                     cell.predicate) == p2["key"])]
        self.assertLess(len(dropped), len(self.cells))
        result = run_detection(self.pack, self.org, bundles=self.bundles, cells=dropped, as_of=self.as_of,
                               run_channel="X", tie_salt="g5-smoke")
        c = next((c for c in result["candidates"] if c["key"] == p2["key"]), None)
        weeks = c["candidate_weeks"] if c else []
        self.assertFalse([w for w in weeks if p2["found_from"] <= w <= p2["found_to"]])

    def test_a_single_site_burst_is_found_by_single_site_and_never_by_x(self) -> None:
        burst = self.spec.decoys[6]
        label = self.label(burst.id)
        c = self.candidate(burst.keys[0])
        self.assertFalse([w for w in (c["candidate_weeks"] if c else [])
                          if label["watch_from"] <= w <= label["watch_to"]])
        items = next(a["items"] for a in self.card["alerts"] if a["channel"] == "single_site")
        self.assertTrue([e for e in items if e["site"] == burst.sites[0] and e["key"] == burst.keys[0]
                         and label["watch_from"] <= e["week"] <= label["watch_to"]])
        card = self.decoy_card(burst.id)
        self.assertTrue(next(o for o in card["outcomes"] if o["channel"] == "single_site")["alerted"])

    def test_s_and_r_mf_cannot_see_narrative_only_plants_by_construction(self) -> None:
        entries = {e["channel"]: e for e in self.card["by_construction"]}
        self.assertEqual(sorted(entries), ["R_mf", "S"])
        for name, e in entries.items():
            self.assertEqual((e["label"], e["visibility"]), ("by construction, not a result", "narrative_only"))
            self.assertEqual(e["recall"], self.card["channels"][name]["by_visibility"]["narrative_only"]["recall"])
        canon = Canonicaliser(self.pack)
        for r in self.planted.records:
            codes = codes_channel(r, self.pack, canon)
            self.assertEqual((codes.predicates, codes.entities), ((), ()))
            self.assertEqual(sense(r, self.pack, canon, None)[2], ())
        self.assertEqual(B.r_mf_cells(self.pack, list(self.planted.records), master_data=self.world.master_data,
                                      last_week=W[-1]), {})

    def test_every_baseline_is_reported_and_the_hard_case_is_listed(self) -> None:
        for name in B.CHANNELS:
            block = self.card["channels"][name]
            self.assertEqual(block["label"], B.CHANNEL_LABELS[name])
            self.assertEqual(block["units"], 3)
            self.assertEqual(len(block["per_seed"]), 1)
            self.assertEqual(sorted(block["decoys_alerted"]), sorted(P.DECOY_CLASSES))
        self.assertFalse(self.card["channels"]["rules"]["ranked"])
        self.assertIsNone(self.card["channels"]["rules"]["average_precision"])
        hard = self.card["known_hard_cases"]
        self.assertEqual([(h["decoy_id"], h["class"], h["seed"]) for h in hard],
                         [("d-copies", "cross_site_unmarked_copies", self.seed)])
        self.assertEqual(hard[0]["note"], "copies without origin markers count as independent at each site "
                                          "(G3 limitation)")
        self.assertEqual(sorted(hard[0]["alerted"]), sorted(B.CHANNELS))
        copies = self.spec.decoys[2]
        for r in item_records(self.planted, self.spec, copies.id):
            if r["site"] in copies.copy_sites:
                root = db_rows(self.site_db(r["site"]), "SELECT root_ref FROM records WHERE record_ref = ? "
                                                        "ORDER BY record_ref", (r["record_ref"],))
                self.assertEqual(root, [(r["record_ref"],)])

    def test_the_no_plant_control_runs_per_seed_and_only_its_earlier_finds_are_not_net(self) -> None:
        # regression: seed 11's world alone (no plant) alerts single_site on two pattern keys at a planted site
        # inside their windows. Audit r2: both control alerts come strictly after single_site's planted-world find
        # (the plant's alert came first and its cooldown hid the later background alert), so neither is a chance
        # find; single_site's three finds are all net and the collective lift on this seed is 0, not 2 / 3
        control_dir = self.run_dir / "work" / f"seed-{self.seed}" / "control"
        self.assertTrue((control_dir / "hq" / "collective.sqlite3").is_file())
        index = {w: i for i, w in enumerate(W)}
        control = {(a["seed"], a["channel"]): a["items"] for a in self.card["control_alerts"]}
        self.assertEqual(sorted(control), sorted((self.seed, name) for name in (*B.CHANNELS, *B.ABLATION_CHANNELS)))
        for name in B.CHANNELS:
            with self.subTest(channel=name):
                block, items = self.card["channels"][name], control[(self.seed, name)]
                in_control = {p["id"]: H.pattern_outcome(items, p, index) for p in self.labels["patterns"]}
                found_there = {i for i, o in in_control.items() if o["found"]}
                self.assertEqual((block["control_alerts"], block["control_found"]), (len(items), len(found_there)))
                outcomes = {p["id"]: next(o for o in p["outcomes"] if o["channel"] == name)
                            for p in self.card["patterns"]}
                self.assertEqual({i for i, o in outcomes.items() if o["found_in_control"]}, found_there)
                chance = {i for i, o in outcomes.items() if o["found"] and i in found_there
                          and in_control[i]["first_alert_week"] <= o["first_alert_week"]}
                self.assertEqual({i for i, o in outcomes.items() if o["chance_find"]}, chance)
                self.assertEqual(block["chance_found"], len(chance))
                self.assertEqual(block["found_net"],
                                 sum(1 for i, o in outcomes.items() if o["found"] and i not in chance))
        single = self.card["channels"]["single_site"]
        self.assertEqual((single["found"], single["control_found"], single["chance_found"], single["found_net"]),
                         (3, 2, 0, 3))
        items = control[(self.seed, "single_site")]
        for p in self.card["patterns"]:
            o = next(o for o in p["outcomes"] if o["channel"] == "single_site")
            label = next(lb for lb in self.labels["patterns"] if lb["id"] == p["id"])
            if o["found_in_control"]:
                self.assertLess(index[o["first_alert_week"]],
                                index[H.pattern_outcome(items, label, index)["first_alert_week"]])
        self.assertEqual(self.card["channels"]["X"]["control_found"], 0)
        lift = self.card["lifts"]["X_minus_single_site"]
        self.assertEqual((lift["estimate"], lift["basis"]), (0.0, H.NET_BASIS))
        for e in self.card["by_construction"]:
            self.assertEqual(e["recall_net"], 0.0)
        for item in next(a["items"] for a in self.card["control_alerts"] if a["channel"] == "single_site"):
            self.assertIn(item["site"], self.site_ids)

    def test_the_x1_block_lifts_and_ablation(self) -> None:
        x1 = self.card["x1"]
        self.assertFalse(x1["eligible"])
        self.assertIsNone(x1["verdict"])
        self.assertEqual(len(x1["reasons"]), 4)
        self.assertIn("fewer than 10 patterns: the lift intervals are unstable", self.card["warnings"])
        for name in ("X_minus_single_site", "X_minus_S", "X_minus_R_mf"):
            lift = self.card["lifts"][name]
            self.assertEqual((lift["n_patterns"], lift["n_seeds"], lift["n_units"], lift["B"]), (3, 1, 3, 10000))
            self.assertLessEqual(lift["ci_low"], lift["estimate"])
            self.assertLessEqual(lift["estimate"], lift["ci_high"])
        self.assertTrue(self.card["stamps"]["ablation_k1"])
        self.assertEqual(self.card["ablation"]["label"], B.ABLATION_LABEL)
        self.assertEqual(sorted(self.card["ablation"]["channels"]), ["S_k1", "X_k1"])
        assert_interval_blocks(self, self.card)
        self.assertEqual(self.card["min_detectable_rate"]["k"], 3)
        self.assertEqual([(r["rate_at_k"], r["rate_unsuppressed"]) for r in self.card["min_detectable_rate"]["rows"]],
                         [(1, 1), (3, 1), (2, 2), (2, 2), (2, 2), (2, 2), (3, 3)])
        self.assertEqual(H.scorecard_problems(self.card), [])


class ClaimsIntegrityConstructionTests(ConstructionCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.run_smoke("claims_integrity", CI_SMOKE, CI_SEED)

    def test_x_finds_a_planted_pattern_and_the_scorecard_validates(self) -> None:
        self.assertEqual(self.card["pack"]["id"], "claims_integrity")
        self.assertGreaterEqual(self.card["channels"]["X"]["found"], 1)
        self.assertEqual(H.scorecard_problems(self.card), [])
        self.assertEqual(self.card["content_hash"], H.content_hash(self.card))
        self.assertIsNone(self.card["ablation"])
        self.assertFalse(self.card["stamps"]["ablation_k1"])

    def test_only_the_stale_filter_keeps_the_stale_chain_out_of_its_watch_span(self) -> None:
        self.assert_only_the_stale_filter_keeps_it_quiet()

    def test_min_detectable_rate_at_k5_equals_the_hand_rows(self) -> None:
        mdr = self.card["min_detectable_rate"]
        self.assertEqual(mdr["k"], 5)
        self.assertEqual(mdr["label"], "analytic: constant weekly counts, G4's per-site D2 tests only "
                                       "(rate_at_k_text_background: X with the background in text-only cells and the "
                                       "planted rate in codes cells, two tests at alpha_site / 2); not a measurement")
        rows = mdr["rows"]
        self.assertEqual([r["background"] for r in rows], list(range(11)))
        self.assertEqual([r["rate_at_k"] for r in rows[:5]], [1, 5, 4, 3, 2])
        self.assertEqual([r["rate_unsuppressed"] for r in rows[:5]], [1, 1, 2, 2, 2])

    def test_a_pattern_below_k_is_reported_without_assertion(self) -> None:
        p2 = next(p for p in self.card["patterns"] if p["id"] == "p2")
        self.assertEqual(sorted({o["channel"] for o in p2["outcomes"]}), sorted(B.CHANNELS))
        found = next(o for o in p2["outcomes"] if o["channel"] == "X")["found"]
        print(f"\n[{SYNTHETIC_NOTE}] claims_integrity p2 (rate 2 at k=5) found by X: {found}")

    def test_a_decoy_noisy_elsewhere_is_warned_and_still_counted(self) -> None:
        noisy = [d for d in self.card["decoys"] if any(q["quiet"] is False for q in d["quiet_elsewhere"])]
        self.assertTrue(noisy)
        for d in noisy:
            self.assertIn(f"decoy {d['id']} seed {self.seed}: not quiet elsewhere; an alert on its key may come from "
                          "background", self.card["warnings"])
        for name in B.CHANNELS:
            alerted = sum(1 for d in self.card["decoys"] for o in d["outcomes"]
                          if o["channel"] == name and o["alerted"])
            self.assertEqual(alerted, sum(self.card["channels"][name]["decoys_alerted"].values()))


# =================================================================================================== baselines

class RecordingRecord(dict):
    """A record that records every key read and fails on iteration or on a read R-mf may never make."""

    FORBIDDEN = ("narrative", "persons", "reporter", "origin_ref", "origin_site", "language", "record_ref",
                 "synthetic")

    def __init__(self, data: Mapping[str, Any], reads: list[str], prefix: str = "") -> None:
        super().__init__(data)
        self.reads = reads
        self.prefix = prefix

    def __getitem__(self, key: str) -> Any:
        self.reads.append(self.prefix + key)
        if not self.prefix and key in self.FORBIDDEN:
            raise AssertionError(f"R-mf read {key}")
        return super().__getitem__(key)

    def _iteration(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("R-mf iterated a record")

    __iter__ = keys = items = values = get = __contains__ = __len__ = copy = _iteration


class BaselineInputTests(unittest.TestCase):
    pack = DQ

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.world = generate(DQ, 7, 6, 38)
        cls.weeks = B.world_weeks(cls.world.params["start"], 38)
        cls.site_ids = list(cls.world.params["site_ids"])
        cls.records = list(cls.world.records)
        cls.pipeline = B.run_pipeline(DQ, cls.records, site_ids=cls.site_ids, master_data=cls.world.master_data,
                                      weeks=cls.weeks, workdir=cls.tmp / "plain")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pipeline.close()
        cls._tmp.cleanup()

    def test_r_mf_reads_only_the_allowed_fields(self) -> None:
        source = next(r for r in self.records if r["codes"] and r["entities"]["product"])
        reads: list[str] = []
        record = RecordingRecord({**source, "entities": RecordingRecord(source["entities"], reads, "entities.")},
                                 reads)
        cells = B.r_mf_cells(DQ, [record], master_data=self.world.master_data, last_week=self.weeks[-1])
        self.assertTrue(cells)
        allowed = set(DQ.egress.central_allowed_fields) | {"entities"}     # the parent of every entities.<type>
        self.assertTrue(reads)
        self.assertEqual(sorted(set(reads) - allowed), [])
        self.assertIn("entities.product", reads)
        for forbidden in ("narrative", "persons", "reporter"):
            probe = RecordingRecord({**source, "entities": RecordingRecord(source["entities"], [], "entities.")}, [])
            with self.subTest(forbidden=forbidden), self.assertRaises(AssertionError):
                probe[forbidden]
        with self.assertRaises(AssertionError):
            list(RecordingRecord(source, []))

    def test_r_mf_is_unchanged_by_poisoned_narratives_while_x_and_u_change(self) -> None:
        poison = " Units from lot L20046 arrived cracked. The SD-9 overheated while charging. QZXV-MARKER"
        poisoned = [{**r, "narrative": r["narrative"] + poison} for r in self.records]
        last = self.weeks[-1]
        clean_rmf = B.r_mf_cells(DQ, self.records, master_data=self.world.master_data, last_week=last)
        self.assertEqual(canonical_bytes(clean_rmf),
                         canonical_bytes(B.r_mf_cells(DQ, poisoned, master_data=self.world.master_data,
                                                      last_week=last)))
        other = B.run_pipeline(DQ, poisoned, site_ids=self.site_ids, master_data=self.world.master_data,
                               weeks=self.weeks, workdir=self.tmp / "poisoned")
        try:
            self.assertNotEqual(canonical_bytes(B.u_cells(other)), canonical_bytes(B.u_cells(self.pipeline)))
            x_clean = self.pipeline.store.detection_inputs(self.pipeline.as_of, "X")[1]
            x_poisoned = other.store.detection_inputs(other.as_of, "X")[1]
            self.assertNotEqual([c[:10] for c in x_clean], [c[:10] for c in x_poisoned])
            s_clean = self.pipeline.store.detection_inputs(self.pipeline.as_of, "S")[1]
            s_poisoned = other.store.detection_inputs(other.as_of, "S")[1]
            self.assertEqual([c[:10] for c in s_clean], [c[:10] for c in s_poisoned])
        finally:
            other.close()

    def test_r_mf_counts_forwarded_copies_because_origin_fields_are_not_allowed(self) -> None:
        self.assertNotIn("origin_site", DQ.egress.central_allowed_fields)
        source = next(r for r in self.records if r["codes"] and r["entities"]["product"]
                      and r["site"] != "plant-brindlemoor" and r["origin_ref"] is None)
        copy_record = {**source, "record_ref": "copy-1", "site": "plant-brindlemoor",
                       "origin_ref": source["record_ref"], "origin_site": source["site"]}
        cells = B.r_mf_cells(DQ, [source, copy_record], master_data=self.world.master_data, last_week=self.weeks[-1])
        self.assertEqual(sorted(cells), sorted({source["site"], "plant-brindlemoor"}))
        for site in cells:
            for c in cells[site]:
                self.assertEqual((c["n"], c["n_roots"], c["n_reporters"]), (1, 1, 1))

    def test_u_counts_every_claim_row_of_non_forwarded_records(self) -> None:
        u = B.u_cells(self.pipeline)
        rows = 0
        for sid in self.site_ids:
            rows += db_rows(self.tmp / "plain" / "edge" / f"site-{sid}.sqlite3",
                            "SELECT COUNT(*) FROM claims c JOIN records r ON r.record_ref = c.record_ref "
                            "WHERE r.forwarded_in = 0 ORDER BY 1")[0][0]
        self.assertEqual(sum(c["n"] for cells in u.values() for c in cells), rows)
        self.assertTrue(all(isinstance(c["n_roots"], int) and isinstance(c["n_reporters"], int)
                            for cells in u.values() for c in cells))
        self.assertEqual({c["channel"] for cells in u.values() for c in cells}, {"codes", "text_only"})

    def test_x_and_s_are_byte_identical_from_the_hq_store_alone(self) -> None:
        workdir = self.tmp / "hq-alone"
        pipeline = B.run_pipeline(DQ, self.records, site_ids=self.site_ids, master_data=self.world.master_data,
                                  weeks=self.weeks, workdir=workdir)
        try:
            first = B.hq_results(pipeline.store, as_of=pipeline.as_of, tie_salt="hq-alone")
            org, as_of = pipeline.org, pipeline.as_of
        finally:
            pipeline.close()
        shutil.rmtree(workdir / "edge")
        store = CollectiveStore(workdir / "hq" / "collective.sqlite3", DQ, org, clock=Clock())
        try:
            again = B.hq_results(store, as_of=as_of, tie_salt="hq-alone")
        finally:
            store.close()
        for name in ("X", "S"):
            self.assertEqual(result_bytes(again[name]), result_bytes(first[name]))
        self.assertGreater(first["X"]["cells"], first["S"]["cells"])

    def test_the_k1_ablation_needs_its_flag(self) -> None:
        for flag in (False, None, 1, "yes"):
            with self.subTest(flag=flag), self.assertRaises(B.EvaluationError) as cm:
                B.k1_cells(self.pipeline, ablation_k1=flag)
            self.assertEqual(str(cm.exception), "the k=1 ablation needs ablation_k1=True")
        cells = B.k1_cells(self.pipeline, ablation_k1=True)
        site = self.pipeline.sites[self.site_ids[1]]
        self.assertTrue(all(c["entity_id"] in site.master.get(c["entity_type"], ())
                            or DQ.entity_types[c["entity_type"]].id_format is None
                            for c in cells[self.site_ids[1]]))
        self.assertTrue(all(isinstance(c["n"], int) for c in cells[self.site_ids[1]]))

    def test_build_cells_k_defaults_to_the_pack_and_refuses_a_bad_value(self) -> None:
        site = self.pipeline.sites[self.site_ids[0]]
        rows = site.store.emission_inputs(None, self.weeks[-1])
        default = build_cells(rows, DQ, master=site.master)
        self.assertEqual(canonical_bytes(default), canonical_bytes(build_cells(rows, DQ, master=site.master,
                                                                               k=DQ.egress.k)))
        self.assertTrue(any(c["n"] == "<k" for c in default[0]))
        unsuppressed, _ = build_cells(rows, DQ, master=site.master, k=1)
        self.assertTrue(all(isinstance(c[f], int) for c in unsuppressed for f in ("n", "n_roots", "n_reporters")))
        self.assertTrue(all("res_conf_min" in c for c in unsuppressed))
        self.assertEqual([c["entity_id"] for c in unsuppressed], [c["entity_id"] for c in default[0]])
        for k in (0, -1, True, False, 1.0, "3"):
            with self.subTest(k=k), self.assertRaises(ValueError) as cm:
                build_cells(rows, DQ, master=site.master, k=k)
            self.assertEqual(str(cm.exception), "k must be an int >= 1")

    def test_the_r_mf_field_rule(self) -> None:
        pack = pack_copy(self.tmp, "device_quality", {("egress.json", "central_allowed_fields"):
                                                      ["codes", "entities.product", "site"]})
        with self.assertRaises(B.EvaluationError):
            B.r_mf_cells(pack, self.records[:3], master_data=self.world.master_data, last_week=self.weeks[-1])


class _StubStore:
    def __init__(self, rows: list[InputRow]) -> None:
        self.rows = rows

    def emission_inputs(self, after: Any, through: str) -> list[InputRow]:
        return [r for r in self.rows if r.count_week <= through]


class _StubSite:
    def __init__(self, rows: list[InputRow]) -> None:
        self.store = _StubStore(rows)


class SingleSiteAndRulesTests(unittest.TestCase):
    def stub(self, rows: Mapping[str, list[InputRow]], n_weeks: int = 40) -> Any:
        weeks = tuple(B.world_weeks("2024-01-01", n_weeks))
        return B.Pipeline(pack=DQ, org=B.org_for_sites(sorted(rows), "eval"), weeks=weeks,
                          as_of=B.closing_date(DQ, weeks[-1]), sites={s: _StubSite(rows[s]) for s in rows},
                          store=None)

    @staticmethod
    def row(week: str, ref: str, entity_id: str, predicate: str = "crack") -> InputRow:
        return InputRow(count_week=week, record_ref=ref, root_ref=ref, reporter_id=None, entity_type="product",
                        entity_id=entity_id, predicate=predicate, channel="codes", res_conf=1.0)

    def test_a_hand_d2_computation_shared_budget_cooldown_and_salted_ties(self) -> None:
        weeks = B.world_weeks("2024-01-01", 40)
        sites = [f"s{i}" for i in range(7)]
        rows = {s: [self.row(weeks[0], f"{s}-bg", "CM-5", "leak"),
                    self.row(weeks[29], f"{s}-a", "SD-9"), self.row(weeks[30], f"{s}-b", "SD-9")] for s in sites}
        events = B.single_site_alerts(self.stub(rows), tie_salt="ties")
        cfg = DetectorConfig.from_pack(DQ)
        score = 0.0 - stats.poisson_logsf(2, cfg.lambda_floor * cfg.window_weeks)
        order = sorted(sites, key=lambda s: sha256_hex(f"ties|{s}|product:SD-9:crack"))
        self.assertEqual([(e["week"], e["rank"], e["site"]) for e in events],
                         [(weeks[30], i + 1, s) for i, s in enumerate(order[:5])]
                         + [(weeks[31], i + 1, s) for i, s in enumerate(order[5:])])
        self.assertTrue(all(e["score"] == score and e["key"] == "product:SD-9:crack" for e in events))
        flipped = B.single_site_alerts(self.stub(rows), tie_salt="other-salt")
        self.assertNotEqual([e["site"] for e in flipped[:5]], [e["site"] for e in events[:5]])

    def test_a_site_without_enough_history_never_exceeds(self) -> None:
        weeks = B.world_weeks("2024-01-01", 40)
        rows = {"s1": [self.row(weeks[20], "a", "SD-9"), self.row(weeks[21], "b", "SD-9")]}
        self.assertEqual(B.single_site_alerts(self.stub(rows), tie_salt="t"), [])
        rows = {"s1": [self.row(weeks[0], "bg", "CM-5", "leak"), self.row(weeks[20], "a", "SD-9"),
                       self.row(weeks[21], "b", "SD-9")]}
        self.assertEqual([e["week"] for e in B.single_site_alerts(self.stub(rows), tie_salt="t")], [weeks[21]])

    def test_rule_episodes_start_where_the_previous_week_has_no_hit(self) -> None:
        result = {"rule_hits": [
            {"rule_id": "r1", "key": "a:A1:p", "weeks": ["2024-W10", "2024-W11", "2024-W13"]},
            {"rule_id": "r2", "key": "a:A1:p", "weeks": ["2024-W12", "2024-W20"]},
            {"rule_id": "r1", "key": "b:B1:p", "weeks": ["2020-W53", "2021-W01", "2021-W03"]}]}
        self.assertEqual([(e["week"], e["key"]) for e in B.rule_alerts(result)],
                         [("2020-W53", "b:B1:p"), ("2021-W03", "b:B1:p"), ("2024-W10", "a:A1:p"),
                          ("2024-W20", "a:A1:p")])
        self.assertTrue(all(e["rank"] is None and e["score"] is None and e["site"] is None
                            for e in B.rule_alerts(result)))

    def test_no_sql_and_no_sqlite3_in_the_evaluation_code(self) -> None:
        for path in EVALUATE_FILES:
            with self.subTest(module=path.name):
                self.assertEqual([s for s in string_constants(path) if _SQL_STATEMENT.match(s)], [])
                tree = ast.parse(path.read_text(encoding="utf-8"))
                names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
                self.assertFalse({n for n in names if n.split(".")[0] == "sqlite3"})


# =================================================================================================== harness

def _label(**fields: Any) -> dict[str, Any]:
    base = {"id": "p", "key": "t:K1:p", "visibility": "narrative_only", "start_index": 30, "end_index": 37,
            "found_from": W[30], "found_to": W[41], "sites": ["plant-x", "plant-z"]}
    return {**base, **fields}


def _event(week: str, key: str = "t:K1:p", score: float | None = 0.5, site: str | None = None) -> dict[str, Any]:
    return {"week": week, "rank": 1, "key": key, "score": score, "site": site}


def _candidate(week: str, key: str) -> dict[str, Any]:
    return {"week": week, "key": key}


class MetricTests(unittest.TestCase):
    index = {w: i for i, w in enumerate(W)}

    def test_repeats_count_once_and_out_of_window_alerts_are_false_alarms(self) -> None:
        label = _label()
        events = [_event(W[32]), _event(W[33]), _event(W[40]), _event(W[42]), _event(W[29]), _event(W[35], "t:K2:p")]
        self.assertEqual(H.pattern_outcome(events, label, self.index),
                         {"found": True, "first_alert_week": W[32], "delay_weeks": 2, "lead_weeks": 5})
        self.assertEqual(H.false_alarms(events, [label]), 3)
        late = H.pattern_outcome([_event(W[40])], label, self.index)
        self.assertEqual((late["delay_weeks"], late["lead_weeks"]), (10, -3))
        self.assertFalse(H.pattern_outcome([_event(W[42]), _event(W[29])], label, self.index)["found"])

    def test_burn_in_alerts_are_dropped(self) -> None:
        events = [_event(W[10]), _event(W[25]), _event(W[26]), _event(W[51])]
        self.assertEqual([e["week"] for e in H.eval_events(events, W[26], W[51])], [W[26], W[51]])

    def test_a_decoy_counts_only_inside_its_watch_span(self) -> None:
        label = {"class": "echo_marked", "keys": ["t:D1:p", "t:D2:p"], "watch_from": W[30], "watch_to": W[35]}
        self.assertEqual(H.decoy_outcome([_event(W[29], "t:D1:p"), _event(W[36], "t:D2:p")], label),
                         {"alerted": False, "first_alert_week": None, "candidate": None, "first_candidate_week": None,
                          "failed": False})
        self.assertEqual(H.decoy_outcome([_event(W[33], "t:D2:p"), _event(W[31], "t:D1:p")], label),
                         {"alerted": True, "first_alert_week": W[31], "candidate": None, "first_candidate_week": None,
                          "failed": True})
        # candidacy is scored for stale chains only
        self.assertEqual(H.decoy_outcome([], label, [_candidate(W[31], "t:D1:p")])["failed"], False)

    def test_a_stale_chain_fails_a_channel_that_keeps_it_a_candidate_in_its_watch_span(self) -> None:
        # audit r3 review: a stale chain alerts while fresh (burn-in) and, without G4's stale filter, stays a
        # candidate at every step after, so its cooldown is never released and it never alerts again; scored on alerts
        # alone, a broken filter could not fail it
        label = {"class": "stale_chain", "keys": ["t:D1:p"], "watch_from": W[30], "watch_to": W[35]}
        quiet = H.decoy_outcome([], label, [_candidate(W[29], "t:D1:p"), _candidate(W[31], "t:D2:p")])
        self.assertEqual(quiet, {"alerted": False, "first_alert_week": None, "candidate": False,
                                 "first_candidate_week": None, "failed": False})
        kept = H.decoy_outcome([], label, [_candidate(W[33], "t:D1:p"), _candidate(W[32], "t:D1:p")])
        self.assertEqual(kept, {"alerted": False, "first_alert_week": None, "candidate": True,
                                "first_candidate_week": W[32], "failed": True})
        # rules and single_site have no candidates: their outcome is the alert alone
        self.assertEqual(H.decoy_outcome([_event(W[34], "t:D1:p")], label),
                         {"alerted": True, "first_alert_week": W[34], "candidate": None, "first_candidate_week": None,
                          "failed": True})
        labels = {"patterns": [_label()], "decoys": [{**label, "id": "d"}]}
        block = H.channel_block("X", "label", {1: [], 2: []}, labels, self.index, 26, {1: [], 2: []},
                                {1: [_candidate(W[31], "t:D1:p")], 2: []})
        self.assertEqual((block["decoys_alerted"]["stale_chain"], block["decoys_failed"]["stale_chain"]), (0, 1))
        self.assertEqual(block["alerts"], 0)
        without = H.channel_block("rules", "label", {1: []}, labels, self.index, 26, {1: []})
        self.assertEqual(without["decoys_failed"]["stale_chain"], 0)

    def labels(self) -> dict[str, Any]:
        return {"patterns": [_label(), _label(id="q", key="t:K3:p", visibility="codes_only")],
                "decoys": [{"id": "d", "class": "echo_marked", "keys": ["t:D1:p"], "watch_from": W[30],
                            "watch_to": W[35]}]}

    def test_a_channel_block_on_hand_built_alerts(self) -> None:
        per_seed = {1: [_event(W[32], score=0.9), _event(W[33], score=0.9), _event(W[31], "t:D1:p", score=0.95),
                        _event(W[45], "t:X:p", score=0.2)],
                    2: [_event(W[45], score=0.4)]}
        block = H.channel_block("X", "label", per_seed, self.labels(), self.index, 26, {1: [], 2: []})
        self.assertEqual((block["units"], block["found"], block["recall"]), (4, 1, 0.25))
        self.assertEqual((block["control_found"], block["chance_found"], block["found_net"], block["recall_net"]),
                         (0, 0, 1, 0.25))
        self.assertEqual(block["by_visibility"]["narrative_only"],
                         {"units": 2, "found": 1, "recall": 0.5, "control_found": 0, "chance_found": 0, "found_net": 1,
                          "recall_net": 0.5})
        self.assertEqual(block["by_visibility"]["both"],
                         {"units": 0, "found": 0, "recall": None, "control_found": 0, "chance_found": 0,
                          "found_net": 0, "recall_net": None})
        self.assertEqual((block["alerts"], block["false_alarms"]), (5, 3))
        self.assertEqual(block["false_alarms_per_week"], 3 / (26 * 2))
        self.assertEqual(block["decoys_alerted"]["echo_marked"], 1)
        self.assertEqual(block["decoys_failed"], block["decoys_alerted"])
        self.assertEqual((block["median_delay_weeks"], block["median_lead_weeks"]), (2, 5))
        seed1 = block["per_seed"][0]
        # seed 1 ranks D1 (0.95), K1 (0.9, relevant), X (0.2): p@40 = 1/40, AP = (1/2) / 2 patterns
        self.assertEqual((seed1["precision_at_40"], seed1["average_precision"]), (1 / 40, 0.25))
        self.assertEqual(block["per_seed"][1]["average_precision"], 0.0)
        self.assertEqual(block["average_precision"], 0.125)
        rules = H.channel_block("rules", "label", {1: [_event(W[32], score=None)]}, self.labels(), self.index, 26,
                                {1: []})
        self.assertEqual((rules["ranked"], rules["precision_at_40"], rules["average_precision"], rules["found"]),
                         (False, None, None, 1))

    def test_single_site_matches_the_key_only_at_a_planted_site(self) -> None:
        # regression: an alert on the key at a site the pattern was never planted at is not a find
        label = _label()
        self.assertEqual(label["sites"], ["plant-x", "plant-z"])
        per_seed = {1: [_event(W[34], site="plant-x"), _event(W[34], site="plant-y")]}
        block = H.channel_block("single_site", "label", per_seed, self.labels(), self.index, 26, {1: []})
        self.assertEqual((block["found"], block["false_alarms"], block["alerts"]), (1, 1, 2))
        elsewhere = {1: [_event(W[34], site="plant-y")]}
        block = H.channel_block("single_site", "label", elsewhere, self.labels(), self.index, 26, {1: []})
        self.assertEqual((block["found"], block["false_alarms"]), (0, 1))
        self.assertFalse(H.pattern_outcome(elsewhere[1], label, self.index)["found"])

    def test_a_find_the_control_makes_as_early_or_earlier_is_a_chance_find_and_not_net(self) -> None:
        # regression: the same key alerting in the world without the plant, in the same week or earlier, makes the
        # planted run's find a chance find
        planted = {1: [_event(W[32])], 2: [_event(W[33])], 3: [_event(W[34])]}
        control = {1: [_event(W[32])], 2: [], 3: [_event(W[31]), _event(W[36])]}
        block = H.channel_block("X", "label", planted, self.labels(), self.index, 26, control)
        self.assertEqual((block["found"], block["control_found"], block["chance_found"], block["found_net"]),
                         (3, 2, 2, 1))
        self.assertEqual((block["control_recall"], block["recall_net"], block["control_alerts"]), (2 / 6, 1 / 6, 3))
        self.assertEqual([(r["seed"], r["found"], r["control_found"], r["chance_found"], r["found_net"],
                           r["control_alerts"]) for r in block["per_seed"]],
                         [(1, 1, 1, 1, 0, 1), (2, 1, 0, 0, 1, 0), (3, 1, 1, 1, 0, 2)])
        label = self.labels()["patterns"][0]
        self.assertFalse(H.net_found(planted[1], control[1], label, self.index))
        self.assertTrue(H.net_found(planted[2], control[2], label, self.index))
        self.assertTrue(H.chance_find(planted[3], control[3], label, self.index))

    def test_a_control_alert_only_after_the_planted_find_does_not_void_it(self) -> None:
        # regression (audit r2): a channel that spends its whole budget (single_site) alerts on the plant at W31;
        # without the plant the same key alerts only at W40, from background the planted world's cooldown
        # suppressed. That is not a chance find, and removing it gave X a positive "collective lift" over a
        # single_site that found more patterns, earlier.
        label = _label()
        planted = {1: [_event(W[31], site="plant-x"), _event(W[31], site="plant-z")]}
        control = {1: [_event(W[40], site="plant-z")]}
        self.assertFalse(H.chance_find(planted[1], control[1], label, self.index))
        self.assertTrue(H.net_found(planted[1], control[1], label, self.index))
        block = H.channel_block("single_site", "label", planted, self.labels(), self.index, 26, control)
        self.assertEqual((block["found"], block["control_found"], block["chance_found"], block["found_net"]),
                         (1, 1, 0, 1))
        # the same control alert at or before the planted find is still a chance find
        for week in (W[31], W[30]):
            self.assertTrue(H.chance_find(planted[1], [_event(week, site="plant-x")], label, self.index))
        # a control alert at a site the pattern was never planted at does not count, whatever its week
        self.assertFalse(H.chance_find(planted[1], [_event(W[30], site="plant-y")], label, self.index))
        # X found 1 of 1, single_site found 1 of 1: the lift on net found is 0, not 1
        x = {"p": [H.net_found([_event(W[33])], [], label, self.index)]}
        single = {"p": [H.net_found(planted[1], control[1], label, self.index)]}
        self.assertEqual(H.lift("X_minus_single_site", x, single, B=1000, seed=1)["estimate"], 0.0)

    def test_lifts_are_cluster_bootstraps_and_equal_recalls_give_zero(self) -> None:
        same = {"p1": [True, False], "p2": [True, True]}
        lift = H.lift("X_minus_S", same, same, B=1000, seed=1)
        self.assertEqual((lift["estimate"], lift["ci_low"], lift["ci_high"]), (0.0, 0.0, 0.0))
        self.assertEqual(lift["basis"], "found in the planted world and not found as early or earlier in the same "
                                        "seed's no-plant control world")
        self.assertTrue(lift["ci_low"] <= 0 <= lift["ci_high"])
        self.assertEqual((lift["n_patterns"], lift["n_seeds"], lift["n_units"], lift["seed"], lift["method"]),
                         (2, 2, 4, "x1:1:X_minus_S", "cluster percentile"))
        better = H.lift("X_minus_S", {"p1": [True, True], "p2": [True, True]},
                        {"p1": [False, False], "p2": [True, True]}, B=1000, seed=1)
        self.assertEqual(better["estimate"], 0.5)

    def test_x1_eligibility_and_verdict(self) -> None:
        lift = {"ci_low": 0.1}
        block = H.x1_block(blind=True, bound=True, n_patterns=20, n_decoys=20, lift_x=lift, precision_x=0.25)
        # B2: the defaults state no planter relation, so even a passing verdict does not count as STRATEGY's X1
        self.assertEqual(block, {"eligible": True, "reasons": [], "verdict": {
            "lift_ci_low_above_0": True, "precision_at_40_at_least_0_25": True, "pass": True},
            "family_size": 1, "planter_relation": "unstated", "independent": False, "counts_as_strategy_x1": False,
            "caveats": [H.UNSTATED_CAVEAT]})
        block = H.x1_block(blind=True, bound=True, n_patterns=20, n_decoys=20, lift_x={"ci_low": 0.0},
                           precision_x=0.3)
        self.assertFalse(block["verdict"]["pass"])
        block = H.x1_block(blind=False, bound=False, n_patterns=19, n_decoys=3, lift_x=lift, precision_x=1.0)
        self.assertEqual((block["eligible"], len(block["reasons"]), block["verdict"]), (False, 4, None))

    def test_x1_relation_family_size_and_caveats(self) -> None:
        # B2: only an independent planter's eligible, passing verdict counts as STRATEGY's X1; eligible and the
        # verdict are computed exactly as before
        passing, failing = {"ci_low": 0.1}, {"ci_low": 0.0}
        cases = (("independent", True, passing, True), ("same_system_procedural", True, passing, False),
                 ("unstated", True, passing, False), ("independent", False, passing, False),
                 ("independent", True, failing, False), ("same_system_procedural", True, failing, False))
        for relation, blind, lift, counts in cases:
            with self.subTest(relation=relation, blind=blind, lift=lift):
                block = H.x1_block(blind=blind, bound=True, n_patterns=20, n_decoys=20, lift_x=lift,
                                   precision_x=0.3, planter_relation=relation, family_size=2)
                before = H.x1_block(blind=blind, bound=True, n_patterns=20, n_decoys=20, lift_x=lift,
                                    precision_x=0.3)
                self.assertEqual({k: block[k] for k in ("eligible", "reasons", "verdict")},
                                 {k: before[k] for k in ("eligible", "reasons", "verdict")})
                self.assertEqual((block["planter_relation"], block["family_size"]), (relation, 2))
                self.assertEqual(block["independent"], relation == "independent")
                self.assertEqual(block["counts_as_strategy_x1"], counts)
                self.assertEqual(block["verdict"] is None, not blind)
        self.assertEqual({r: H.x1_block(blind=True, bound=True, n_patterns=20, n_decoys=20, lift_x=passing,
                                        precision_x=0.3, planter_relation=r)["caveats"] for r in H.PLANTER_RELATIONS},
                         {"independent": [],
                          "same_system_procedural": ["Procedural blinding only: the planter and the detector author "
                                                     "are the same AI system."],
                          "unstated": ["The prereg does not state the planter's relation to the detector author, so "
                                       "this run cannot count as STRATEGY's X1."]})

    def test_interval_blocks_on_hand_built_inputs(self) -> None:
        block = {"per_seed": [{"precision_at_40": 0.5, "average_precision": None, "false_alarms_per_week": 0.25},
                              {"precision_at_40": None, "average_precision": None, "false_alarms_per_week": 0.25},
                              {"precision_at_40": 0.25, "average_precision": None, "false_alarms_per_week": 0.25}]}
        net = {"p2": [True, False, False], "p1": [True, True, True]}
        out = H.interval_blocks("X", block, net, B=1000, seed=7)
        self.assertEqual(out, H.interval_blocks("X", block, net, B=1000, seed=7))
        self.assertEqual(sorted(out), ["average_precision_ci", "false_alarms_per_week_ci", "precision_at_40_ci",
                                       "recall_net_ci"])
        recall = out["recall_net_ci"]
        boot = stats.cluster_bootstrap_mean([[1, 1, 1], [1, 0, 0]], B=1000, seed="x1:7:X:recall_net")
        self.assertEqual(recall, {"estimate": 4 / 6, "ci_low": boot["ci_low"], "ci_high": boot["ci_high"], "B": 1000,
                                  "seed": "x1:7:X:recall_net", "method": "cluster percentile",
                                  "clusters": "patterns", "n_clusters": 2})
        precision = out["precision_at_40_ci"]           # the seed without a value is dropped
        self.assertEqual((precision["estimate"], precision["clusters"], precision["n_clusters"], precision["seed"]),
                         (0.375, "seeds", 2, "x1:7:X:precision_at_40"))
        self.assertLessEqual(precision["ci_low"], precision["estimate"])
        self.assertLessEqual(precision["estimate"], precision["ci_high"])
        self.assertIsNone(out["average_precision_ci"])  # no seed has a value
        same = out["false_alarms_per_week_ci"]          # all-equal clusters give a degenerate interval
        self.assertEqual((same["estimate"], same["ci_low"], same["ci_high"], same["n_clusters"]),
                         (0.25, 0.25, 0.25, 3))
        self.assertIsNone(H.interval_blocks("X", block, {}, B=1000, seed=7)["recall_net_ci"])
        self.assertNotEqual(H.interval_blocks("S", block, net, B=1000, seed=7)["recall_net_ci"]["seed"],
                            recall["seed"])

    def test_lifts_carry_an_adjusted_interval_from_the_same_replicates(self) -> None:
        a = {"p1": [True, True, False], "p2": [False, True, True], "p3": [True, False, False], "p4": [True, True, True]}
        b = {"p1": [False, False, False], "p2": [False, True, False], "p3": [True, True, False],
             "p4": [False, True, False]}
        one = H.lift("X_minus_S", a, b, B=2000, seed=3)
        self.assertEqual((one["alpha_adjusted"], one["ci_low_adjusted"], one["ci_high_adjusted"]),
                         (0.05, one["ci_low"], one["ci_high"]))
        two = H.lift("X_minus_S", a, b, B=2000, seed=3, family_size=2)
        unadjusted = ("estimate", "ci_low", "ci_high", "B", "seed", "method", "basis", "n_patterns", "n_seeds",
                      "n_units")
        self.assertEqual({k: two[k] for k in unadjusted}, {k: one[k] for k in unadjusted})
        self.assertEqual(two["alpha_adjusted"], 0.025)
        self.assertLessEqual(two["ci_low_adjusted"], two["ci_low"])
        self.assertGreaterEqual(two["ci_high_adjusted"], two["ci_high"])
        clusters = [[int(x) - int(y) for x, y in zip(a[p], b[p])] for p in sorted(a)]
        boot = stats.cluster_bootstrap_mean(clusters, B=2000, seed="x1:3:X_minus_S", alpha=0.025)
        self.assertEqual((two["ci_low_adjusted"], two["ci_high_adjusted"]), (boot["ci_low"], boot["ci_high"]))

    def test_rate_strata_split_net_recall_by_planted_rate(self) -> None:
        patterns = [{"id": "a", "rate_per_week": 1}, {"id": "b", "rate_per_week": 3}, {"id": "c", "rate_per_week": 1}]
        found = {name: {"a": [True, False], "b": [False, False], "c": [True, True]} for name in B.CHANNELS}
        found["S"] = {"a": [False, False], "b": [True, False], "c": [False, False]}
        rows = H.rate_strata(patterns, found, 2)
        self.assertEqual([(r["rate_per_week"], r["patterns"], r["units"]) for r in rows], [(1, 2, 4), (3, 1, 2)])
        self.assertEqual(sorted(rows[0]["channels"]), sorted(B.CHANNELS))
        self.assertEqual(rows[0]["channels"]["X"], {"found_net": 3, "recall_net": 0.75})
        self.assertEqual(rows[0]["channels"]["S"], {"found_net": 0, "recall_net": 0.0})
        self.assertEqual(rows[1]["channels"]["X"], {"found_net": 0, "recall_net": 0.0})
        self.assertEqual(rows[1]["channels"]["S"], {"found_net": 1, "recall_net": 0.5})

    def test_min_detectable_rate_of_the_device_pack(self) -> None:
        rows = H.min_detectable_rate(DQ)["rows"]
        # regression: with the background in text-only cells and the plant in codes cells X needs rate 3 at b = 2..5,
        # not the single-cell row's 2 (b = 2: the codes test's 8 x lb(2) = 8 against the certain 1 a week, and the
        # combined 8 x (1 + 1) = 16 against ub 2 a week, both fail at alpha / 2)
        self.assertEqual([r["rate_at_k_text_background"] for r in rows], [1, 3, 3, 3, 3, 3, 3])
        log_half = math.log(0.01 / 2)
        self.assertGreater(stats.poisson_logsf(8, 8.0), log_half)
        self.assertGreater(stats.poisson_logsf(16, 16.0), log_half)
        self.assertLess(stats.poisson_logsf(24, 8.0), log_half)
        self.assertEqual([r["rate_at_k"] for r in rows], [1, 3, 2, 2, 2, 2, 3])
        self.assertEqual([r["rate_unsuppressed"] for r in rows], [1, 1, 2, 2, 2, 2, 3])


class HarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.runs = self.tmp / "runs"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def prereg(self, pack: Any = "device_quality", run_id: str = "pre", *extra: Any,
               seeds: str = str(DQ_SEED)) -> Path:
        code, _, err = cli(H.main, prereg_argv(pack, self.runs, run_id, *extra, seeds=seeds))
        self.assertEqual(code, 0, err)
        return self.runs / "x1" / run_id / "prereg.json"

    def test_prereg_pins_every_value(self) -> None:
        doc = json.loads(self.prereg().read_text(encoding="utf-8"))
        self.assertEqual(doc["kind"], "x1_prereg")
        self.assertEqual({k: doc["pack"][k] for k in DQ.hashes()}, DQ.hashes())
        self.assertEqual(doc["pack"]["ref"], "device_quality")
        self.assertEqual((doc["code_hash"], doc["code_files"]), (H.eval_code_hash(), H.eval_code_files()))
        self.assertIn("mycelic/collective/detect/detectors.py", doc["code_files"])
        self.assertIn("mycelic/collective/evaluate/harness.py", doc["code_files"])
        self.assertIn("mycelic/collective/experiments/common.py", doc["code_files"])
        self.assertNotIn("mycelic/collective/experiments/e1_extract.py", doc["code_files"])
        self.assertEqual(doc["world"], {"seeds": [DQ_SEED], "sites": 6, "site_ids": DQ_SITES, "weeks": 52,
                                        "start": "2024-01-01"})
        self.assertEqual(doc["evaluation"], {"eval_from": 26, "eval_to": 51, "eval_from_week": W[26],
                                             "eval_to_week": W[51], "grace_weeks": 4, "top_k": 40})
        self.assertEqual((doc["tie_salt"], doc["detector_author"], doc["bootstrap"]),
                         ("g5-smoke", "Mycelic  Engineering", {"B": 10000, "seed": 1}))
        self.assertTrue(doc["allow_dirty"])
        H.read_prereg(self.runs / "x1" / "pre" / "prereg.json")
        path = self.prereg(pack_copy(self.tmp, "device_quality"), "pre-path")
        self.assertTrue(Path(json.loads(path.read_text(encoding="utf-8"))["pack"]["ref"]).is_absolute())

    def test_prereg_refuses_bad_settings(self) -> None:
        cases = (("--eval-from", 18), ("--eval-to", 52), ("--weeks", 33), ("--seeds", "1,1"), ("--seeds", ""),
                 ("--seeds", "a"), ("--seeds", "-1"), ("--tie-salt", "tab\tsalt"), ("--detector-author", ""),
                 ("--bootstrap-b", 999), ("--grace-weeks", 53), ("--sites", 7), ("--run-id", "bad id"))
        before = path_snapshot(self.tmp)
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                argv = prereg_argv("device_quality", self.runs)
                if flag in argv:
                    argv[argv.index(flag) + 1] = value
                else:
                    argv += [flag, value]
                code, _, err = cli(H.main, argv)
                self.assertEqual(code, 2, (flag, value))
                self.assertTrue(err.startswith("error: "))
        self.assertEqual(path_snapshot(self.tmp), before)

    def test_prereg_pins_the_family_size_and_the_planter_relation(self) -> None:
        # B2: optional flags, so earlier argv keeps working; the defaults state no relation and one primary test
        doc = json.loads(self.prereg().read_text(encoding="utf-8"))
        self.assertEqual((doc["family_size"], doc["planter_relation"]), (1, "unstated"))
        for size, relation in ((2, "same_system_procedural"), (100, "independent"), (1, "unstated")):
            with self.subTest(size=size, relation=relation):
                path = self.prereg("device_quality", f"flags-{size}-{relation}", "--family-size", size,
                                   "--planter-relation", relation)
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual((doc["family_size"], doc["planter_relation"]), (size, relation))
                H.read_prereg(path)
        self.assertEqual(H.PLANTER_RELATIONS, ("independent", "same_system_procedural", "unstated"))
        before = path_snapshot(self.tmp)
        for flag, value in (("--family-size", 0), ("--family-size", 101), ("--family-size", "x"),
                            ("--family-size", "-1"), ("--family-size", "2.0"), ("--family-size", "\u0662"),
                            ("--planter-relation", "other"), ("--planter-relation", "Independent"),
                            ("--planter-relation", "")):
            with self.subTest(flag=flag, value=value):
                code, out, err = cli(H.main, prereg_argv("device_quality", self.runs, "refused", flag, value))
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith(f"error: {flag} must be "), err)
        self.assertEqual(path_snapshot(self.tmp), before)

    def test_read_prereg_refuses_a_prereg_without_or_with_a_bad_family_size_or_relation(self) -> None:
        path = self.prereg()
        doc = json.loads(path.read_text(encoding="utf-8"))
        cases = [({k: v for k, v in doc.items() if k != "family_size"}, "$.family_size required"),
                 ({k: v for k, v in doc.items() if k != "planter_relation"}, "$.planter_relation required"),
                 ({**doc, "family_size": 0}, "$.family_size minimum"),
                 ({**doc, "family_size": 101}, "$.family_size maximum"),
                 ({**doc, "planter_relation": "other"}, "$.planter_relation enum")]
        for i, (bad, problem) in enumerate(cases):
            with self.subTest(problem=problem):
                bad_path = self.tmp / f"prereg-{i}.json"
                bad_path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(H.UsageError) as cm:
                    H.read_prereg(bad_path)
                self.assertEqual(str(cm.exception), f"prereg {bad_path} is not an x1 prereg.json ({problem})")
                code, _, err = cli(H.main, run_argv(bad_path, DQ_SMOKE, self.runs, f"bad-{i}", "--allow-dirty"))
                self.assertEqual(code, 2)
                self.assertIn("is not an x1 prereg.json", err)
                self.assertFalse((self.runs / "x1" / f"bad-{i}").exists())

    def test_check_plant_refuses_a_foreign_binding_and_accepts_a_matching_or_null_one(self) -> None:
        # B2: check-plant refused only a malformed binding before; a wrong one first failed in run
        prereg = self.prereg()
        sha = sha256_hex(prereg.read_bytes())
        for value, ok in ((None, True), (sha, True), ("f" * 64, False)):
            with self.subTest(value=value):
                path = self.tmp / f"plant-{value}.json"
                path.write_text(json.dumps({**smoke(), "prereg_sha256": value}), encoding="utf-8")
                for extra in ((), ("--construct",), ("--dry-run",)):
                    code, out, err = cli(H.main, ["check-plant", "--prereg", prereg, "--plant", path, *extra])
                    if ok:
                        self.assertEqual(code, 0, err)
                    else:
                        self.assertEqual((code, out), (2, ""))
                        self.assertEqual(err.strip(), "error: the plant spec's prereg_sha256 is not the sha256 of "
                                                      "this prereg file")

    def test_check_plant_construct_builds_every_prereg_seed_and_writes_nothing(self) -> None:
        prereg = self.prereg("device_quality", "two-seeds", seeds="11,12")
        before = path_snapshot(self.tmp)
        code, out, err = cli(H.main, ["check-plant", "--construct", "--prereg", prereg, "--plant", DQ_SMOKE])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), [f"prereg_sha256: {sha256_hex(prereg.read_bytes())}",
                                            "plant: ok (patterns=3 decoys=8)", "construction: ok (seeds=2)"])
        code, out, err = cli(H.main, ["check-plant", "--construct", "--prereg", prereg, "--plant", DQ_SMOKE,
                                      "--dry-run"])
        self.assertEqual((code, out), (0, "dry-run: evaluate.harness check-plant\n"), err)
        self.assertEqual(path_snapshot(self.tmp), before)

    def test_check_plant_construct_names_the_seed_of_a_construction_failure(self) -> None:
        # a codes_only plant has filler-only text; with the loader's minimum of five 'de' filler sentences there are
        # 5 + 20 + 60 = 85 distinct texts, fewer than the plant's 100 records, so construction fails at the first
        # seed. plant() raises it; parse and check cannot see it
        filler = DQ.generator["filler"]["de"][:5]
        pack = pack_copy(self.tmp, "device_quality", {("generator.json", "filler", "de"): filler})
        prereg = self.prereg(pack, "few-filler", seeds="12,11")
        raw = base_spec()
        raw["patterns"][0].update({"visibility": "codes_only", "language": "de", "rate_per_week": 10, "weeks": 5})
        path = self.tmp / "plant_de.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        code, out, err = cli(H.main, ["check-plant", "--prereg", prereg, "--plant", path])
        self.assertEqual((code, out.splitlines()[1:]), (0, ["plant: ok (patterns=1 decoys=0)"]), err)
        code, out, err = cli(H.main, ["check-plant", "--construct", "--prereg", prereg, "--plant", path])
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err.strip(), "error: plant: $.patterns[0]: narrative uniqueness exhausted (seed 11)")
        code, out, err = cli(H.main, ["check-plant", "--construct", "--prereg", prereg, "--plant", path, "--dry-run"])
        self.assertEqual((code, out), (0, "dry-run: evaluate.harness check-plant\n"), err)

    def test_dirty_code_needs_allow_dirty_which_is_stamped(self) -> None:
        for state in (True, "unknown"):
            with self.subTest(state=state), mock.patch.object(H, "code_dirty", return_value=state):
                argv = [a for a in prereg_argv("device_quality", self.runs, f"dirty-{state}") if a != "--allow-dirty"]
                code, _, err = cli(H.main, argv)
                self.assertEqual(code, 2)
                self.assertIn("--allow-dirty", err)
                path = self.prereg("device_quality", f"allowed-{state}")
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual((doc["code_dirty"], doc["allow_dirty"]), (state, True))
                H.read_prereg(path)
        with mock.patch.object(H, "code_dirty", return_value=False):
            argv = [a for a in prereg_argv("device_quality", self.runs, "clean") if a != "--allow-dirty"]
            self.assertEqual(cli(H.main, argv)[0], 0)
        prereg = self.runs / "x1" / "clean" / "prereg.json"
        with mock.patch.object(H, "code_dirty", return_value=True):
            code, _, err = cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, "dirty-run"))
        self.assertEqual(code, 2)
        self.assertIn("--allow-dirty", err)
        self.assertFalse((self.runs / "x1" / "dirty-run").exists())

    def test_run_refuses_each_changed_pin_and_names_every_one(self) -> None:
        cases = (
            (("questions.json", "templates", "count_predicate_on_entity", "text"),
             "In the last {window}, how many records describe {predicate_label} for {entity_type_label} {entity_id}?",
             ["config_hash"]),
            (("detectors.json", "alert_budget_per_week"), 6, ["config_hash", "detector_hash"]),
            (("generator.json", "forwarding_rate"), 0.09, ["fixtures_hash"]),
        )
        for edit, value, names in cases:
            with self.subTest(edit=edit[0]):
                pack = pack_copy(self.tmp, "device_quality")
                prereg = self.prereg(pack, f"pre-{edit[0]}")
                path = Path(pack.directory) / edit[0]
                doc = json.loads(path.read_text(encoding="utf-8"))
                node = doc
                for key in edit[1:-1]:
                    node = node[key]
                node[edit[-1]] = value
                path.write_text(json.dumps(doc), encoding="utf-8")
                code, _, err = cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, f"run-{edit[0]}", "--allow-dirty"))
                self.assertEqual(code, 2)
                self.assertEqual(err.strip(), f"error: pinned values differ from the prereg: {', '.join(names)}")
                self.assertFalse((self.runs / "x1" / f"run-{edit[0]}").exists())
        prereg = self.prereg("device_quality", "pre-code")
        with mock.patch.object(H, "eval_code_hash", return_value="0" * 64):
            code, _, err = cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, "run-code", "--allow-dirty"))
        self.assertEqual((code, err.strip()), (2, "error: pinned values differ from the prereg: code_hash"))

    def test_run_refuses_other_seeds_a_foreign_binding_and_an_existing_run_dir(self) -> None:
        prereg = self.prereg()
        code, _, err = cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, "seeds", "--allow-dirty", seeds="11,12"))
        self.assertEqual(code, 2)
        self.assertIn("--seeds", err)
        foreign = self.tmp / "plant_bound.json"
        foreign.write_text(json.dumps({**smoke(), "prereg_sha256": "f" * 64}), encoding="utf-8")
        code, _, err = cli(H.main, run_argv(prereg, foreign, self.runs, "bound", "--allow-dirty"))
        self.assertEqual(code, 2)
        self.assertIn("prereg_sha256", err)
        (self.runs / "x1" / "exists").mkdir(parents=True)
        code, _, err = cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, "exists", "--allow-dirty"))
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        bad = self.tmp / "prereg.json"
        bad.write_text(json.dumps({**json.loads(prereg.read_text(encoding="utf-8")), "extra": 1}), encoding="utf-8")
        code, _, err = cli(H.main, run_argv(bad, DQ_SMOKE, self.runs, "malformed", "--allow-dirty"))
        self.assertEqual(code, 2)
        self.assertIn("not an x1 prereg.json", err)
        for name in ("seeds", "bound", "malformed"):
            self.assertFalse((self.runs / "x1" / name).exists())

    def test_an_interrupted_run_exits_130_and_its_id_cannot_be_reused(self) -> None:
        prereg = self.prereg()
        with mock.patch.object(H, "_seed_run", side_effect=KeyboardInterrupt):
            code, _, err = cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, "cut", "--allow-dirty"))
        self.assertEqual(code, 130)
        self.assertTrue((self.runs / "x1" / "cut").is_dir())
        self.assertFalse((self.runs / "x1" / "cut" / "scorecard.json").exists())
        self.assertEqual(cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, "cut", "--allow-dirty"))[0], 2)

    def test_check_plant_prints_the_prereg_sha_and_writes_nothing(self) -> None:
        prereg = self.prereg()
        before = path_snapshot(self.tmp)
        code, out, _ = cli(H.main, ["check-plant", "--prereg", prereg, "--plant", DQ_SMOKE])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [f"prereg_sha256: {sha256_hex(prereg.read_bytes())}",
                                            "plant: ok (patterns=3 decoys=8)"])
        bad = self.tmp / "plant_bad.json"
        raw = smoke()
        raw["patterns"][0]["start_week"] = 50
        bad.write_text(json.dumps(raw), encoding="utf-8")
        before = path_snapshot(self.tmp)
        code, out, err = cli(H.main, ["check-plant", "--prereg", prereg, "--plant", bad])
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err.strip(), "error: plant: $.patterns[0].weeks: outside the world weeks")
        self.assertEqual(path_snapshot(self.tmp), before)

    def test_the_dry_run_contract(self) -> None:
        prereg = self.prereg()
        before = path_snapshot(self.tmp)
        argvs = (prereg_argv("device_quality", self.runs, "dry") + ["--dry-run"],
                 prereg_argv(str(self.tmp / "absent-pack"), self.runs, "dry") + ["--dry-run"],
                 ["check-plant", "--prereg", prereg, "--plant", DQ_SMOKE, "--dry-run"],
                 ["check-plant", "--prereg", self.tmp / "absent.json", "--plant", DQ_SMOKE, "--dry-run"],
                 run_argv(prereg, DQ_SMOKE, self.runs, "dry", "--dry-run", "--allow-dirty"),
                 run_argv(self.tmp / "absent.json", self.tmp / "absent-plant.json", self.runs, "dry", "--dry-run"))
        for argv in argvs:
            with self.subTest(argv=argv[:2]):
                code, out, err = cli(H.main, argv)
                self.assertEqual(code, 0, err)
                self.assertTrue(out.startswith(f"dry-run: evaluate.harness {argv[0]}"))
        self.assertEqual(path_snapshot(self.tmp), before)
        code, _, _ = cli(H.main, run_argv(prereg, DQ_SMOKE, self.runs, "pre", "--dry-run"))
        self.assertEqual(code, 2)                      # the run directory exists


class ScorecardSchemaTests(unittest.TestCase):
    card: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            runs = tmp / "runs"
            assert cli(H.main, prereg_argv("device_quality", runs))[0] == 0
            prereg = runs / "x1" / "pre" / "prereg.json"
            assert cli(H.main, run_argv(prereg, DQ_SMOKE, runs, "card", "--allow-dirty"))[0] == 0
            cls.card = json.loads((runs / "x1" / "card" / "scorecard.json").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp)

    def test_stamps_label_and_content_hash(self) -> None:
        card = self.card
        self.assertEqual(H.scorecard_problems(card), [])
        self.assertEqual({k: card["stamps"][k] for k in ("synthetic", "internal_only", "measurement", "blind",
                                                        "extractor", "plant_bound_to_prereg")},
                         {"synthetic": True, "internal_only": True, "measurement": False, "blind": False,
                          "extractor": "lexical", "plant_bound_to_prereg": False})
        self.assertEqual(card["stamps"]["blind_basis"], "self-declared: planted_by differs from the detector author "
                                                        "and planter_saw_detector_code is false")
        self.assertEqual(card["channel_labels"]["R_mf"], R_MF_LABEL)
        self.assertEqual(sorted(card["hashes"]), sorted(("config_hash", "vocabulary_hash", "detector_hash",
                                                         "fixtures_hash", "code_hash", "org_hash", "prereg_sha256",
                                                         "plant_sha256", "labels_sha256")))
        self.assertEqual(card["content_hash_excludes"],
                         ["$.content_hash", "$.created_at", "$.run_id", "$.paths", "$.timings"])
        self.assertEqual(H.content_hash(card), card["content_hash"])
        for key in ("created_at", "run_id", "paths", "timings"):
            self.assertEqual(H.content_hash({**card, key: "changed"}), card["content_hash"])
        self.assertNotEqual(H.content_hash({**card, "warnings": []}), card["content_hash"])
        self.assertNotEqual(H.content_hash({**card, "hashes": {**card["hashes"], "plant_sha256": "0" * 64}}),
                            card["content_hash"])
        self.assertTrue(all(Path(p).is_absolute() for p in card["paths"].values()))
        self.assertEqual(card["stamps"]["same_author_pack"], True)

    def test_every_channel_carries_its_intervals_and_the_rate_strata(self) -> None:
        card = self.card
        assert_interval_blocks(self, card)
        for name in B.CHANNELS:
            ranked = card["channels"][name]["ranked"]
            for metric in ("recall_net", "false_alarms_per_week"):
                self.assertIsNotNone(card["channels"][name][f"{metric}_ci"])
            for metric in ("precision_at_40", "average_precision"):
                self.assertEqual(card["channels"][name][f"{metric}_ci"] is None, not ranked)
        self.assertFalse(card["channels"]["rules"]["ranked"])
        self.assertEqual([p["rate_per_week"] for p in card["patterns"]], [2, 2, 2])
        self.assertEqual([(r["rate_per_week"], r["patterns"], r["units"]) for r in card["by_rate_per_week"]],
                         [(2, 3, 3)])
        for name in B.CHANNELS:
            self.assertEqual(card["by_rate_per_week"][0]["channels"][name],
                             {"found_net": card["channels"][name]["found_net"],
                              "recall_net": card["channels"][name]["recall_net"]})
        for lift in card["lifts"].values():
            self.assertEqual((lift["alpha_adjusted"], lift["ci_low_adjusted"], lift["ci_high_adjusted"]),
                             (0.05, lift["ci_low"], lift["ci_high"]))
        self.assertEqual({k: card["x1"][k] for k in ("family_size", "planter_relation", "independent",
                                                      "counts_as_strategy_x1", "caveats")},
                         {"family_size": 1, "planter_relation": "unstated", "independent": False,
                          "counts_as_strategy_x1": False, "caveats": [H.UNSTATED_CAVEAT]})
        self.assertIn("recall_net_ci resamples whole patterns; precision_at_40_ci, average_precision_ci and "
                      "false_alarms_per_week_ci resample seeds (n_clusters), so with few seeds they are coarse.",
                      card["notes"])
        self.assertIn("Each lift also carries an interval at alpha_adjusted = 0.05 / family_size from the same "
                      "bootstrap replicates; x1.verdict reads the unadjusted 95% interval (STRATEGY section 11.2).",
                      card["notes"])

    def test_the_schema_rejects_tampering(self) -> None:
        tampered = (
            ({**self.card, "extra": 1}, ("$", "additionalProperties")),
            ({k: v for k, v in self.card.items() if k != "lifts"}, ("$.lifts", "required")),
            ({**self.card, "stamps": {**self.card["stamps"], "measurement": True}}, ("$.stamps.measurement", "const")),
            ({**self.card, "stamps": {**self.card["stamps"], "synthetic": "yes"}}, ("$.stamps.synthetic", "type")),
            ({**self.card, "channels": {**self.card["channels"], "X": {**self.card["channels"]["X"], "found": "3"}}},
             ("$.channels.X.found", "type")),
            ({**self.card, "channel_labels": {**self.card["channel_labels"], "R_mf": "R"}},
             ("$.channel_labels.R_mf", "const")),
            ({**self.card, "code": {**self.card["code"], "code_dirty": None}}, ("$.code.code_dirty", "type")),
            ({**self.card, "code": {**self.card["code"], "code_dirty": "maybe"}}, ("$.code.code_dirty", "type")),
            ({**self.card, "channels": {**self.card["channels"], "X": {
                k: v for k, v in self.card["channels"]["X"].items() if k != "recall_net_ci"}}},
             ("$.channels.X.recall_net_ci", "required")),
            ({**self.card, "channels": {**self.card["channels"], "S": {
                **self.card["channels"]["S"], "precision_at_40_ci": {"estimate": 0.5}}}},
             ("$.channels.S.precision_at_40_ci.ci_low", "required")),
            ({**self.card, "lifts": {**self.card["lifts"], "X_minus_S": {
                k: v for k, v in self.card["lifts"]["X_minus_S"].items() if k != "ci_low_adjusted"}}},
             ("$.lifts.X_minus_S.ci_low_adjusted", "required")),
            ({**self.card, "x1": {**self.card["x1"], "planter_relation": "other"}}, ("$.x1.planter_relation", "enum")),
            ({**self.card, "x1": {**self.card["x1"], "caveats": ["looks fine"]}}, ("$.x1.caveats[0]", "enum")),
            ({k: v for k, v in self.card.items() if k != "by_rate_per_week"}, ("$.by_rate_per_week", "required")),
            ({**self.card, "patterns": [{k: v for k, v in self.card["patterns"][0].items() if k != "rate_per_week"}]},
             ("$.patterns[0].rate_per_week", "required")),
        )
        for doc, problem in tampered:
            with self.subTest(problem=problem):
                self.assertIn(problem, H.scorecard_problems(doc))
        for state in (True, False, "unknown"):
            self.assertEqual(H.scorecard_problems({**self.card, "code": {**self.card["code"], "code_dirty": state}}),
                             [])


# =================================================================================================== determinism

class DeterminismTests(unittest.TestCase):
    def test_two_processes_with_other_hash_seeds_give_the_same_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assert cli(H.main, prereg_argv("device_quality", tmp_path / "pre-runs", "pre", "--family-size", 2,
                                           "--planter-relation", "same_system_procedural"))[0] == 0
            prereg = tmp_path / "pre-runs" / "x1" / "pre" / "prereg.json"
            bound = tmp_path / "plant_bound.json"
            bound.write_text(json.dumps({**smoke(), "prereg_sha256": sha256_hex(prereg.read_bytes())}),
                             encoding="utf-8")
            cards = []
            for seed, run_id in (("0", "first"), ("12345", "second")):
                runs = tmp_path / f"runs-{seed}"
                r = subprocess.run([sys.executable, "-m", "mycelic.collective.evaluate.harness",
                                    *map(str, run_argv(prereg, bound, runs, run_id, "--allow-dirty"))],
                                   cwd=ROOT, capture_output=True, text=True, timeout=600,
                                   env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)})
                self.assertEqual(r.returncode, 0, r.stderr)
                cards.append(json.loads((runs / "x1" / run_id / "scorecard.json").read_text(encoding="utf-8")))
            a, b = cards
            self.assertEqual(a["content_hash"], b["content_hash"])
            self.assertEqual(sorted(k for k in a if a[k] != b[k]), ["created_at", "paths", "run_id", "timings"])
            self.assertTrue(a["stamps"]["plant_bound_to_prereg"])
            self.assertNotIn("the plant spec is not bound to the prereg (prereg_sha256 is null)", a["x1"]["reasons"])
            self.assertFalse(a["x1"]["eligible"])
            # B2: the new blocks are part of the content hash and equal across processes
            self.assertEqual(a["x1"], b["x1"])
            self.assertEqual({k: a["x1"][k] for k in ("family_size", "planter_relation", "independent",
                                                       "counts_as_strategy_x1", "caveats")},
                             {"family_size": 2, "planter_relation": "same_system_procedural", "independent": False,
                              "counts_as_strategy_x1": False, "caveats": [H.SAME_SYSTEM_CAVEAT]})
            self.assertEqual((a["lifts"], a["by_rate_per_week"]), (b["lifts"], b["by_rate_per_week"]))
            for lift in a["lifts"].values():
                self.assertEqual(lift["alpha_adjusted"], 0.025)
                self.assertLessEqual(lift["ci_low_adjusted"], lift["ci_low"])
                self.assertGreaterEqual(lift["ci_high_adjusted"], lift["ci_high"])
            for name in B.CHANNELS:
                for metric in H.INTERVAL_METRICS:
                    self.assertEqual(a["channels"][name][f"{metric}_ci"], b["channels"][name][f"{metric}_ci"])
            assert_interval_blocks(self, a)


if __name__ == "__main__":
    unittest.main()
