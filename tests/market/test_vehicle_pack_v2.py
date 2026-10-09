"""Vehicle pack v2 (plan item 1.5 of the 2026-10-09 handoff): the builder, the name check, the reporter column and the
null-rate tool (``tools/market/vehicle_pack_v2.py``, ``nhtsa_export.py``, ``vehicle_null_rate.py``), offline."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "market"))
VEHICLES = ROOT / "docs" / "collective" / "replay" / "vehicles"
PACK, PACK_V2, PROBE = VEHICLES / "pack", VEHICLES / "pack-v2", VEHICLES / "nhtsa-probe.json"
NULL_RATE = VEHICLES / "null-rate"
LADDER = ("pack", "L1", "L2", "L3")
RUNS = ("sites60-seeds1-3", "sites60-seeds4-6", "sites6-seeds1-3", "sites60-vehicles60-seeds1-3")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "market" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


V2 = _load("vehicle_pack_v2")
E = _load("nhtsa_export")
N = _load("vehicle_null_rate")
OLD = ("ENGINE AND ENGINE COOLING", "FUEL SYSTEM, GASOLINE", "SERVICE BRAKES, HYDRAULIC")
GENERIC_NAMES = ("UNKNOWN OR OTHER", "VISIBILITY", "FUEL SYSTEM, DIESEL", "TRACTION CONTROL SYSTEM",
                 "SERVICE BRAKES, AIR", "PARKING BRAKE", "EQUIPMENT ADAPTIVE/MOBILITY", "HYBRID PROPULSION SYSTEM",
                 "Chest Clip, Buckle, Harness", "TRAILER HITCHES", "Other/I am not sure", "INTERIOR LIGHTING",
                 "Carry Handle, Shell, Base", "FUEL SYSTEM, OTHER")
# sha256 of pack/'s export.csv of _rows(), written by the exporter at 7a94535, before pack v2 existed
PACK_EXPORT_SHA256 = "46a2c74f5d40560d8e025c2d71525ac410329262eaefacc1b0003759831329b5"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def complaint(odino: str, make: str, model: str, year: str, comp: str, state: str, ldate: str,
              text: str = "The engine stalled.", prod: str = "V") -> list[str]:
    row = [""] * 51
    row[E.C_ODINO], row[E.C_MAKE], row[E.C_MODEL], row[E.C_YEAR] = odino, make, model, year
    row[E.C_COMP], row[E.C_STATE], row[E.C_LDATE], row[E.C_DESCR], row[E.C_PROD] = comp, state, ldate, text, prod
    row[50] = "Operator Name Never Exported"
    return row


def recall(campno: str, model: str, year: str, comp: str, rcdate: str) -> list[str]:
    row = [""] * 29
    row[E.R_CAMPNO], row[E.R_MAKE], row[E.R_MODEL], row[E.R_YEAR] = campno, "FORD", model, year
    row[E.R_COMP], row[E.R_TYPE], row[E.R_RCDATE] = comp, "V", rcdate
    return row


def investigation(action: str, model: str, year: str, comp: str, odate: str) -> list[str]:
    row = [""] * 11
    row[E.I_ACTION], row[E.I_MAKE], row[E.I_MODEL], row[E.I_YEAR], row[E.I_COMP], row[E.I_ODATE] = \
        action, "FORD", model, year, comp, odate
    return row


COMPONENTS = ["ENGINE", "ENGINE AND ENGINE COOLING:COOLING SYSTEM", "SERVICE BRAKES", "SERVICE BRAKES, HYDRAULIC:ABS",
              "FUEL/PROPULSION SYSTEM", "FUEL SYSTEM, GASOLINE:DELIVERY", "AIR BAGS", "VISIBILITY:WINDSHIELD",
              "TRAILER HITCHES", "UNKNOWN OR OTHER"]
STATES = ["TX", "CA", "NY", "FL", "OH", "WA", "PR", "DC"]
MODELS = [("F-150", "2021"), ("ESCAPE", "2020"), ("EXPLORER", "2022"), ("BRONCO", "9999")]


def _rows() -> list[list[str]]:
    """80 complaints of two component rows each: old and new names, a category outside the pack, an unknown model
    year and complaints received before the window."""
    out = []
    for n in range(160):
        k = n // 2
        model, year = MODELS[k % len(MODELS)]
        day = f"2023{1 + k % 12:02d}{1 + k % 28:02d}" if k % 9 else f"2022{1 + k % 12:02d}15"
        out.append(complaint(str(11000000 + k), "FORD", model, year, COMPONENTS[(n * 7) % len(COMPONENTS)],
                             STATES[k % len(STATES)], day, text=f"Complaint {k} about the vehicle."))
    return out


def _export(pack: Path, out: Path) -> bytes:
    from mycelic.collective.pilot.audit import write_csv
    cats, _ = E.pack_categories(pack)
    rows, _ = E.complaints(_rows(), "FORD", "20230101", "20241231", cats, E.pack_reporter(pack))
    write_csv(rows, out)
    return out.read_bytes()


def _csv(blob: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(blob.decode("utf-8").splitlines()))


class BuildTests(unittest.TestCase):
    def test_the_committed_pack_v2_is_the_builders_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pack-v2"
            result = V2.build(PACK, _json(PROBE), out)
            built = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
            committed = sorted(p.relative_to(PACK_V2) for p in PACK_V2.rglob("*") if p.is_file())
            self.assertEqual(built, committed)
            for rel in built:
                self.assertEqual((out / rel).read_bytes(), (PACK_V2 / rel).read_bytes(), str(rel))
        self.assertEqual(result["pack"]["version"], "0.2.0")
        self.assertEqual(result["predicates"], 24)

    def test_pack_v2_loads_and_is_pinned(self) -> None:
        # a change to pack-v2 after a run uses it is a new choice file
        from mycelic.collective.packs.loader import load_pack_dir
        pack = load_pack_dir(PACK_V2)
        self.assertEqual((pack.id, pack.version, len(pack.generator["sites"])), ("vehicle_complaints", "0.2.0", 60))
        hashes = (pack.config_hash, pack.vocabulary_hash, pack.detector_hash, pack.fixtures_hash)
        self.assertEqual(tuple(h[:8] for h in hashes), PINNED)

    def test_only_detectors_mapping_names_and_generator_differ_from_pack(self) -> None:
        for name in V2.COPIED:
            self.assertEqual((PACK / name).read_bytes(), (PACK_V2 / name).read_bytes(), name)
        mapping, mapping_v1 = _json(PACK_V2 / "mapping.json"), _json(PACK / "mapping.json")
        self.assertEqual((mapping_v1["reporter"], mapping["reporter"]), (None, "reporter"))
        self.assertEqual((mapping_v1["required"], mapping["required"]), (["narrative"], ["narrative", "reporter"]))
        vm, vm1 = mapping["codes"][0]["value_map"], mapping_v1["codes"][0]["value_map"]
        self.assertEqual(sorted(vm), sorted(vm1))               # the same names: the export writes the same column
        self.assertEqual({k: v for k, v in vm.items() if k not in OLD}, {k: v for k, v in vm1.items() if k not in OLD})
        for old, new in V2.MERGED.items():
            self.assertEqual(vm[old], vm[new])
        self.assertEqual(set(_json(PACK / "codes.json")) - set(_json(PACK_V2 / "codes.json")),
                         {"NHT-019", "NHT-020", "NHT-021"})
        lex = {p: d["lexicon"]["en"] for p, d in _json(PACK_V2 / "vocabulary.json")["predicates"].items()}
        self.assertEqual(lex["engine"], ["engine", "engine cooling"])
        self.assertEqual(lex["service_brakes"], ["service brakes", "hydraulic"])
        self.assertEqual(lex["fuel_propulsion_system"], ["fuel", "propulsion system", "fuel system", "gasoline"])
        for gone in ("engine_and_engine_cooling", "fuel_system_gasoline", "service_brakes_hydraulic"):
            self.assertNotIn(gone, lex)

    def test_the_generator_is_packs_with_60_sites(self) -> None:
        gen, gen1 = _json(PACK_V2 / "generator.json"), _json(PACK / "generator.json")
        self.assertEqual(sorted(k for k in gen if gen[k] != gen1[k]),
                         ["filler", "master_data", "narratives", "predicate_weights", "sites"])
        self.assertEqual(len(gen["sites"]), 60)
        self.assertTrue(all((s["weekly_volume"], s["reporters"]) == ([3, 7], 5) for s in gen["sites"]))
        self.assertEqual(set(gen1["predicate_weights"]) - set(gen["predicate_weights"]),
                         {"engine_and_engine_cooling", "fuel_system_gasoline", "service_brakes_hydraulic"})
        self.assertEqual(gen["filler"]["en"], gen1["filler"]["en"] + V2.FILLER_ADDED)

    def test_a_bad_merge_or_setting_is_refused(self) -> None:
        mapping, codes, vocab = (_json(PACK / n) for n in ("mapping.json", "codes.json", "vocabulary.json"))
        with self.assertRaises(V2.BuildError):
            V2.merge_names(mapping, codes, vocab, {"VISIBILITY": "VISIBILITY/WIPER"})   # not a pack category
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(V2.BuildError):
                V2.build(PACK, _json(PROBE), Path(tmp))                                  # exists
            with self.assertRaises(V2.BuildError):
                V2.build(PACK, _json(PROBE), Path(tmp) / "v2", {"burst.min_site": 3})   # no such setting
            self.assertFalse((Path(tmp) / "v2").exists())


class NameCheckTests(unittest.TestCase):
    def test_pack_fails_on_the_three_old_names_and_v2_on_none(self) -> None:
        counts = _json(PROBE)["complaints"]["top_components"]
        before = V2.name_check(counts, _json(PACK / "mapping.json"), _json(PACK / "codes.json"))
        after = V2.name_check(counts, _json(PACK_V2 / "mapping.json"), _json(PACK_V2 / "codes.json"))
        self.assertEqual((before["names"], after["names"]), (40, 40))
        self.assertEqual(sorted(before["failing"]), sorted(OLD))
        self.assertEqual(after["failing"], [])
        rows = {r["name"]: r for r in after["rows"]}
        self.assertEqual(rows["ENGINE AND ENGINE COOLING"]["outcome_predicate"], "engine")
        self.assertIsNone(rows["VISIBILITY"]["outcome_predicate"])      # outside the pack: matches any failure

    def test_the_check_can_fail_only_on_the_listed_old_names(self) -> None:
        # with no old names listed, pack/ passes too: the check shows the merge is applied, not that the list is whole
        counts = _json(PROBE)["complaints"]["top_components"]
        self.assertEqual(V2.name_check(counts, _json(PACK / "mapping.json"), _json(PACK / "codes.json"),
                                       old_names=())["failing"], [])
        # the probe's names that pass only because unknown or other matches any failure (PACK-V2.md lists them)
        after = V2.name_check(counts, _json(PACK_V2 / "mapping.json"), _json(PACK_V2 / "codes.json"))
        generic = sorted(r["name"] for r in after["rows"] if r["outcome_predicate"] is None)
        self.assertEqual(generic, sorted(GENERIC_NAMES))

    def test_cli_exit_codes(self) -> None:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(V2.main(["--check", str(PACK), "--probe", str(PROBE)]), 1)
            self.assertEqual(V2.main(["--check", str(PACK_V2), "--probe", str(PROBE)]), 0)

    def test_outcomes_under_old_names_get_the_complaints_predicate(self) -> None:
        rows = [recall("23V001000", "F-150", "2021", "ENGINE AND ENGINE COOLING:COOLING SYSTEM", "20230301"),
                recall("23V002000", "ESCAPE", "2020", "ENGINE AND ENGINE COOLING", "20230401"),
                recall("23V002000", "ESCAPE", "2020", "ENGINE:IGNITION", "20230401"),
                recall("23V003000", "ESCAPE", "2020", "SERVICE BRAKES, HYDRAULIC:ABS", "20230501")]
        got = {}
        for name, pack in (("v1", PACK), ("v2", PACK_V2)):
            _, preds = E.pack_categories(pack)
            out, _ = E.recalls(rows, "FORD", "20230101", "20241231", preds)
            got[name] = [o["predicate"] for o in out]
        self.assertEqual(got["v1"], ["engine_and_engine_cooling", "", "service_brakes_hydraulic"])
        self.assertEqual(got["v2"], ["engine", "engine", "service_brakes"])
        _, preds = E.pack_categories(PACK_V2)
        out, _ = E.investigations([investigation("PE23001", "F-150", "2021", "FUEL SYSTEM, GASOLINE:DELIVERY",
                                                 "20230301")], "FORD", "20230101", "20241231", preds)
        self.assertEqual(out[0]["predicate"], "fuel_propulsion_system")


class ReporterTests(unittest.TestCase):
    def test_pack_export_is_byte_identical_to_before(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blob = _export(PACK, Path(tmp) / "export.csv")
        self.assertIsNone(E.pack_reporter(PACK))
        self.assertEqual(hashlib.sha256(blob).hexdigest(), PACK_EXPORT_SHA256)
        self.assertEqual(blob.splitlines()[0], b"components[],odino,received,state,summary,vehicle")

    def test_pack_v2_export_adds_only_the_reporter_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            v1 = _csv(_export(PACK, Path(tmp) / "v1.csv"))
            v2 = _csv(_export(PACK_V2, Path(tmp) / "v2.csv"))
        self.assertEqual(E.pack_reporter(PACK_V2), "reporter")
        self.assertEqual(len(v1), 53)
        self.assertEqual([{k: v for k, v in r.items() if k != "reporter"} for r in v2], v1)
        self.assertTrue(all(r["reporter"] == r["odino"] for r in v2))

    def test_an_export_without_the_reporter_column_is_refused_by_v2(self) -> None:
        # the exporter at 7a94535 and any caller that passes no reporter write no column: pack-v2 refuses every row
        # (missing_reporter) instead of reading them as one shared unknown reporter, and the audit stops
        from mycelic.collective.packs.connector import map_rows
        from mycelic.collective.packs.loader import load_pack_dir
        from mycelic.collective.pilot import audit as A
        cats, _ = E.pack_categories(PACK_V2)
        export, _ = E.complaints(_rows(), "FORD", "20230101", "20241231", cats)
        with tempfile.TemporaryDirectory() as tmp:
            A.write_csv(export, Path(tmp) / "export.csv")
            rows = A.read_csv_rows(Path(tmp) / "export.csv")
        self.assertNotIn("reporter", rows[0])
        v1, v2 = (map_rows(rows, load_pack_dir(p)) for p in (PACK, PACK_V2))
        self.assertEqual((len(v1.records), len(v2.records)), (53, 0))
        self.assertEqual(dict(v2.rejected), {"missing_reporter": 53})
        with self.assertRaises(A.AuditError):
            A.audit(load_pack_dir(PACK_V2), rows, [])

    def test_a_reporter_column_must_be_new_and_plain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("odino", "a.b", "persons.reporter", "rep[]"):
                d = Path(tmp) / "p"
                d.mkdir(exist_ok=True)
                (d / "mapping.json").write_text(json.dumps({"reporter": bad}))
                with self.assertRaises(ValueError, msg=bad):
                    E.pack_reporter(d)

    def test_a_missing_reporter_was_one_reporter_and_now_each_complaint_is_one(self) -> None:
        # three complaints on one vehicle, component and week at one site: pack/'s cell has one shared unknown reporter
        # (below k, so suppressed and flagged as few reporters); pack-v2's has three
        from mycelic.collective.edge.egress import SUPPRESSED
        from mycelic.collective.edge.records import InputRow
        from mycelic.collective.edge.site import build_cells
        from mycelic.collective.edge.weeks import iso_week
        from mycelic.collective.packs.connector import map_rows
        from mycelic.collective.packs.loader import load_pack_dir
        from mycelic.collective.pilot.audit import read_csv_rows, write_csv
        rows = [complaint(str(500 + i), "FORD", "F-150", "2021", "AIR BAGS", "TX", "20230105") for i in range(3)]
        got = {}
        with tempfile.TemporaryDirectory() as tmp:
            for name, path in (("v1", PACK), ("v2", PACK_V2)):
                pack = load_pack_dir(path)
                cats, _ = E.pack_categories(path)
                export, _ = E.complaints(rows, "FORD", "20230101", "20241231", cats, E.pack_reporter(path))
                write_csv(export, Path(tmp) / f"{name}.csv")
                records = map_rows(read_csv_rows(Path(tmp) / f"{name}.csv"), pack).records
                inputs = [InputRow(count_week=iso_week(r["received_date"]), record_ref=r["record_ref"],
                                   root_ref=r["record_ref"], reporter_id=r["reporter"], entity_type="vehicle",
                                   entity_id="FORD-F150-2021", predicate="air_bags", channel="codes", res_conf=1.0)
                          for r in records]
                cells, _ = build_cells(inputs, pack, master={"vehicle": ["FORD-F150-2021"]})
                got[name] = (cells[0]["n"], cells[0]["n_reporters"])
        self.assertEqual(got["v1"], (3, SUPPRESSED))
        self.assertEqual(got["v2"], (3, 3))


class NullRateTests(unittest.TestCase):
    def test_settings_and_rate(self) -> None:
        d = _json(PACK / "detectors.json")
        self.assertEqual(N.parse_set(["burst.min_sites=3", "burst.alpha_site=0.001"]),
                         {"burst.min_sites": 3, "burst.alpha_site": 0.001})
        self.assertEqual(N.with_settings(d, {"burst.min_sites": 3})["burst"]["min_sites"], 3)
        self.assertEqual(d["burst"]["min_sites"], 2)                       # the input is not changed
        for bad in ({"burst.min_site": 3}, {"nope.x": 1}):
            with self.assertRaises(ValueError):
                N.with_settings(d, bad)
        with self.assertRaises(ValueError):
            N.parse_set(["burst.min_sites"])
        result = {"candidates": [{"key": "b", "candidate_weeks": ["2024-W20", "2024-W21", "2024-W03"]},
                                 {"key": "c", "candidate_weeks": ["2024-W02"]}]}       # W02, W03: not evaluated
        got = N.rate(result, {"a": 9, "b": 5, "c": 5, "d": 1}, ["2024-W20", "2024-W21", "2024-W22"])
        self.assertEqual((got["tests"], got["candidate_steps"], got["series_ever"]), (12, 2, 1))
        self.assertAlmostEqual(got["rate"], 2 / 12)
        self.assertAlmostEqual(got["series_ever_share"], 1 / 4)
        busy = got["busiest_quarter"]                                       # one of four: the busiest, "a"
        self.assertEqual((busy["series"], busy["tests"], busy["candidate_steps"]), (1, 3, 0))
        per_seed = [{ch: dict(got) for ch in N.CHANNELS}, {ch: dict(got) for ch in N.CHANNELS}]
        pooled = N.pooled(per_seed)
        self.assertEqual(pooled["X"]["all"]["tests"], 24)
        self.assertAlmostEqual(pooled["max_series_ever_share"], 1 / 4)

    def test_settings_are_measured_on_the_same_worlds(self) -> None:
        d = _json(PACK_V2 / "detectors.json")
        loose = N.with_settings(d, {"burst.min_sites": 2, "cooccurrence.min_sites": 2, "burst.alpha_site": 0.05})
        doc = N.run(PACK_V2, {"loose": loose, "pack": d}, sites=6, weeks=40, seeds=[1])
        a, b = (doc["settings"][n]["per_seed"][0] for n in ("loose", "pack"))
        for ch in N.CHANNELS:
            self.assertEqual(a[ch]["tests"], b[ch]["tests"])
            self.assertGreater(a[ch]["tests"], 0)
            self.assertGreaterEqual(a[ch]["candidate_steps"], b[ch]["candidate_steps"])
        self.assertEqual(doc["worlds"][0]["sites"], 6)
        self.assertNotEqual(doc["settings"]["loose"]["detector_hash"], doc["settings"]["pack"]["detector_hash"])
        self.assertNotIn("vehicles", doc)

    def test_a_sparser_world_draws_from_more_vehicles(self) -> None:
        from mycelic.collective.packs.canonical import Canonicaliser
        from mycelic.collective.packs.loader import load_pack_dir
        ids = N.fictional_vehicles(60)
        self.assertEqual(len(set(ids)), 60)
        self.assertLessEqual(set(_json(PACK_V2 / "generator.json")["universe"]["vehicle"]), set(ids))
        canon = Canonicaliser(load_pack_dir(PACK_V2))
        self.assertTrue(all(canon.is_canonical("vehicle", v) for v in ids))
        for bad in (0, N.MAX_VEHICLES + 1):
            with self.assertRaises(ValueError):
                N.fictional_vehicles(bad)
        d = _json(PACK_V2 / "detectors.json")
        dense = N.run(PACK_V2, {"v2": d}, sites=6, weeks=40, seeds=[1])
        sparse = N.run(PACK_V2, {"v2": d}, sites=6, weeks=40, seeds=[1], vehicles=N.fictional_vehicles(24))
        self.assertEqual(sparse["vehicles"], N.fictional_vehicles(24))
        self.assertEqual(sparse["pack"], dense["pack"])                    # the pack given, not the world's copy
        for ch in N.CHANNELS:
            self.assertGreater(sparse["settings"]["v2"]["per_seed"][0][ch]["series"],
                               dense["settings"]["v2"]["per_seed"][0][ch]["series"])


class ThresholdTests(unittest.TestCase):
    """The committed null-rate runs (PACK-V2.md, section 3) and the thresholds pack-v2 carries."""

    def test_pack_v2_carries_the_setting_the_ladder_chose(self) -> None:
        detectors = _json(PACK_V2 / "detectors.json")
        self.assertEqual(N.with_settings(_json(PACK / "detectors.json"), V2.DETECTOR_CHANGES), detectors)
        self.assertEqual(_json(NULL_RATE / "grid.json")["L2"], V2.DETECTOR_CHANGES)
        from mycelic.collective.packs.loader import load_pack_dir
        pack = load_pack_dir(PACK_V2)
        for name in RUNS:
            doc = _json(NULL_RATE / f"{name}.json")
            self.assertEqual(doc["settings"]["L2"]["detectors"], detectors, name)
            self.assertEqual(doc["settings"]["L2"]["detector_hash"], pack.detector_hash, name)
            self.assertEqual(doc["settings"]["pack"]["detectors"], _json(PACK / "detectors.json"), name)
            # run on the committed pack-v2: all four hashes, not only the world's
            self.assertEqual(doc["pack"], {"id": pack.id, "version": pack.version, **pack.hashes()}, name)

    def test_the_ladder_picks_l2_on_the_calibration_seeds_and_it_holds_on_the_others(self) -> None:
        calibration, held_out = _json(NULL_RATE / "sites60-seeds1-3.json"), _json(NULL_RATE / "sites60-seeds4-6.json")
        self.assertEqual((calibration["sites"], calibration["seeds"], held_out["seeds"]), (60, [1, 2, 3], [4, 5, 6]))
        self.assertEqual(N.first_passing(calibration, LADDER), "L2")
        self.assertTrue(N.passes(held_out["settings"]["L2"]["pooled"]))
        for doc in (calibration, held_out):
            self.assertFalse(N.passes(doc["settings"]["pack"]["pooled"]))
            self.assertGreater(doc["settings"]["pack"]["pooled"]["X"]["all"]["rate"], 0.25)
            for seed in doc["settings"]["L2"]["per_seed"]:
                for ch in N.CHANNELS:
                    self.assertLessEqual(seed[ch]["rate"], N.LIMIT)
                    self.assertLessEqual(seed[ch]["busiest_quarter"]["rate"], N.LIMIT)
        # at 6 sites, the settings made for 6 plants already pass
        self.assertEqual(N.first_passing(_json(NULL_RATE / "sites6-seeds1-3.json"), LADDER), "pack")

    def test_in_the_sparser_world_packs_settings_already_pass(self) -> None:
        # whether pack/'s thresholds pass depends on the synthetic world (PACK-V2.md, "What this does not show")
        doc = _json(NULL_RATE / "sites60-vehicles60-seeds1-3.json")
        self.assertEqual((doc["sites"], doc["seeds"], doc["vehicles"]), (60, [1, 2, 3], N.fictional_vehicles(60)))
        self.assertEqual(N.first_passing(doc, LADDER), "pack")
        self.assertEqual(doc["settings"]["pack"]["pooled"]["X"]["all"]["series"], 3 * 60 * 24)
        self.assertEqual(sum(doc["settings"]["L2"]["pooled"][ch]["all"]["candidate_steps"] for ch in N.CHANNELS), 0)

    def test_the_pilot_demo_still_finds_both_smoke_patterns(self) -> None:
        # p1 is written in the narratives only, so only X can see it; p2 is in the codes too
        from mycelic.collective.pilot import audit as A
        with tempfile.TemporaryDirectory() as tmp:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(A.main(["demo", "--pack", str(PACK_V2), "--out", tmp]), 0)
            doc = _json(Path(tmp) / "audit.json")
        found = {ch: {r["outcome_id"]: r["found"] for r in doc["channels"][ch]["by_outcome"]} for ch in N.CHANNELS}
        self.assertEqual(len(doc["export"]["sites"]), 60)
        self.assertEqual(found["X"], {"ISSUE-p1": True, "ISSUE-p2": True})
        self.assertTrue(found["S"]["ISSUE-p2"] and found["R_mf"]["ISSUE-p2"])


PINNED = ("30e7df2c", "771149ad", "6aa5128a", "d631dc98")


if __name__ == "__main__":
    unittest.main()
