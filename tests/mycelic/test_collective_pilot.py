"""The pilot signal audit (``mycelic.collective.pilot.audit``): export reading, outcome checks, scoring, end to end."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

from mycelic.collective.evaluate.baselines import closing_date, r_mf_cells
from mycelic.collective.experiments.openfda_replay import replay_weeks
from mycelic.collective.packs.connector import map_rows
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import load_pack, thaw
from mycelic.collective.pilot import audit as A
from tests.mycelic.test_collective_guards import EVALUATE_FORBIDDEN, forbidden_imports

PACKS = ("device_quality", "claims_integrity", "it_incidents")
ROOT = Path(__file__).resolve().parents[2]


def cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = A.main(argv)
    return code, out.getvalue(), err.getvalue()


def write_outcomes(path: Path, rows: list[dict[str, str]], columns: tuple[str, ...] = (
        "outcome_id", "opened", "entity_type", "entity_id", "predicate")) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)
    return path


class ExportTests(unittest.TestCase):
    def test_export_rows_map_back_to_the_same_records_in_every_pack(self) -> None:
        for pack_id in PACKS:
            with self.subTest(pack=pack_id):
                pack = load_pack(pack_id)
                world = generate(pack, 1, len(pack.generator["sites"]), 40)
                records = world.records[:60]
                rows = [A.export_row(r, pack.mapping()) for r in records]
                mapped = map_rows(rows, pack, synthetic=True)
                self.assertEqual(mapped.rejected, {})
                for got, want in zip(mapped.records, records):
                    for key in ("record_ref", "site", "received_date", "narrative", "entities", "reporter"):
                        self.assertEqual(got[key], want[key], key)

    def test_csv_round_trip_nests_and_splits_lists(self) -> None:
        rows = [{"complaint_no": "c-1", "plant": "plant-a", "lots": ["L10001", "L10002"],
                 "patient": {"name": "Ann", "ref": "PT1"}, "description": "It cracked; badly, \"really\"."}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.csv"
            A.write_csv(rows, path)
            self.assertEqual(A.read_csv_rows(path), rows)

    def test_a_path_through_a_list_is_not_written(self) -> None:
        row: dict[str, Any] = {}
        self.assertFalse(A.set_path(row, "notes[].body", "x"))
        self.assertEqual(row, {})

    def test_jsonl_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "x.jsonl"
            good.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")
            self.assertEqual(A.read_rows(good), [{"a": 1}, {"b": 2}])
            bad = Path(tmp) / "y.jsonl"
            bad.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
            with self.assertRaisesRegex(A.AuditError, "line 2 is not JSON"):
                A.read_rows(bad)
            with self.assertRaisesRegex(A.AuditError, "cannot read"):
                A.read_rows(Path(tmp) / "absent.csv")


class OutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pack = load_pack("device_quality")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_valid_file(self) -> None:
        path = write_outcomes(self.tmp / "o.csv", [
            {"outcome_id": "CAPA-1", "opened": "2024-09-30", "entity_type": "product", "entity_id": "sd-9",
             "predicate": "leak"},
            {"outcome_id": "CAPA-2", "opened": "2024-10-01", "entity_type": "lot", "entity_id": "L10001",
             "predicate": ""}])
        got = A.read_outcomes(path, self.pack)
        self.assertEqual([(o.outcome_id, o.entity_id, o.predicate, o.key) for o in got],
                         [("CAPA-1", "SD-9", "leak", "product:SD-9:leak"), ("CAPA-2", "L10001", None, "lot:L10001")])

    def test_every_problem_is_named(self) -> None:
        base = {"outcome_id": "C", "opened": "2024-09-30", "entity_type": "product", "entity_id": "SD-9",
                "predicate": ""}
        cases = [({"opened": "30/09/2024"}, "opened must be YYYY-MM-DD"),
                 ({"entity_type": "plant"}, "is not a type of pack"),
                 ({"entity_id": "nothing like an id"}, "does not resolve"),
                 ({"predicate": "exploded"}, "is not a predicate"),
                 ({"outcome_id": ""}, "empty or repeated")]
        for change, message in cases:
            with self.subTest(message=message):
                path = write_outcomes(self.tmp / "o.csv", [{**base, **change}])
                with self.assertRaisesRegex(A.AuditError, message):
                    A.read_outcomes(path, self.pack)
        path = write_outcomes(self.tmp / "o.csv", [base, base])
        with self.assertRaisesRegex(A.AuditError, "empty or repeated"):
            A.read_outcomes(path, self.pack)
        path = write_outcomes(self.tmp / "o.csv", [{"outcome_id": "C"}], columns=("outcome_id",))
        with self.assertRaisesRegex(A.AuditError, "missing columns opened, entity_type, entity_id"):
            A.read_outcomes(path, self.pack)


def alert(week: str, available: str, key: str, rank: int = 1) -> dict[str, Any]:
    t, eid, pred = key.split(":")
    return {"week": week, "available_date": available, "rank": rank, "score": 1.0, "key": key, "entity_type": t,
            "entity_id": eid, "predicate": pred, "sites": ["a", "b"], "record_refs": ["r1", "r2"]}


class ScoreTests(unittest.TestCase):
    def test_found_lead_post_and_unexplained(self) -> None:
        outcomes = [A.Outcome("O1", "2024-06-03", "product", "SD-9", "leak"),
                    A.Outcome("O2", "2024-06-03", "product", "IP-7", None),
                    A.Outcome("O3", "2024-06-03", "lot", "L10001", None)]
        alerts = [alert("2024-W18", "2024-05-06", "product:SD-9:leak"),        # 28 days before O1
                  alert("2024-W20", "2024-05-20", "product:SD-9:crack"),       # other predicate: not O1's
                  alert("2024-W19", "2024-05-13", "product:IP-7:overheat"),    # O2 takes any predicate
                  alert("2024-W24", "2024-06-17", "lot:L10001:crack"),         # after O3 opened: post, not found
                  alert("2024-W30", "2024-07-29", "product:CM-5:leak")]        # no outcome at all
        s = A.score_channel(outcomes, alerts, lookback=8, post=8, available_first=date(2024, 3, 3),
                            evaluated_weeks=20)
        by = {r["outcome_id"]: r for r in s["by_outcome"]}
        self.assertEqual((by["O1"]["found"], by["O1"]["lead_days"]), (True, 28))
        self.assertEqual((by["O2"]["found"], by["O2"]["lead_days"]), (True, 21))
        self.assertEqual((by["O3"]["found"], by["O3"]["post_alerts"]), (False, 1))
        self.assertEqual(s["summary"]["found"], 2)
        self.assertEqual(s["unexplained"], [1, 4])
        self.assertEqual(s["summary"]["unexplained_alerts"], 2)
        self.assertGreaterEqual(s["summary"]["p_value"], 1 / 20)

    def test_reactive_alerts_inflate_only_the_audited_null(self) -> None:
        # every alert is a reaction after the outcome opened: nothing to find, yet the audited null rotates the
        # reactions into the look-back and expects finds; the corrected null leaves them out and expects none
        outcomes = [A.Outcome("O1", "2024-06-03", "product", "SD-9", None)]
        alerts = [alert("2024-W23", "2024-06-10", "product:SD-9:leak"),
                  alert("2024-W25", "2024-06-24", "product:SD-9:leak")]
        s = A.score_channel(outcomes, alerts, lookback=8, post=8, available_first=date(2024, 3, 3),
                            evaluated_weeks=20)["summary"]
        self.assertEqual(s["found"], 0)
        self.assertGreater(s["expected_found"], 0)
        self.assertEqual(s["expected_found_excluding_own_post"], 0)
        self.assertEqual(s["p_value_excluding_own_post"], 1.0)
        # an alert before the window that is not a reaction stays in both nulls
        early = alerts + [alert("2024-W12", "2024-03-24", "product:SD-9:leak")]
        s = A.score_channel(outcomes, early, lookback=8, post=8, available_first=date(2024, 3, 3),
                            evaluated_weeks=20)
        self.assertGreater(s["summary"]["expected_found_excluding_own_post"], 0)
        self.assertLess(s["summary"]["expected_found_excluding_own_post"], s["summary"]["expected_found"])
        self.assertEqual([t["available_date"] for t in s["alert_timeline"]],
                         ["2024-06-10", "2024-06-24", "2024-03-24"])

    def test_the_lookback_bounds_a_find(self) -> None:
        outcomes = [A.Outcome("O1", "2024-06-03", "product", "SD-9", None)]
        s = A.score_channel(outcomes, [alert("2024-W10", "2024-03-10", "product:SD-9:leak")], lookback=8, post=8,
                            available_first=date(2024, 3, 3), evaluated_weeks=20)
        self.assertEqual(s["summary"]["found"], 0)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.docs = {}
        for pack_id in PACKS:
            code, out, err = cli(["demo", "--pack", pack_id, "--out", str(cls.tmp / pack_id), "--weeks", "48"])
            assert code == 0, err
            cls.docs[pack_id] = json.loads((cls.tmp / pack_id / "audit.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_demo_runs_in_every_field_with_its_label(self) -> None:
        for pack_id, doc in self.docs.items():
            with self.subTest(pack=pack_id):
                self.assertEqual((doc["kind"], doc["pack"]["id"], doc["label"]), (A.KIND, pack_id, A.DEMO_LABEL))
                self.assertEqual(doc["export"]["rejected"], {})
                self.assertEqual(doc["outcomes"]["given"], 3)
                for name in A.CHANNELS:
                    s = doc["channels"][name]["summary"]
                    self.assertEqual(s["outcomes"], doc["outcomes"]["in_scope"])
                    self.assertIsNotNone(s["expected_found"])
                md = (self.tmp / pack_id / "audit.md").read_text(encoding="utf-8")
                self.assertIn(A.DEMO_LABEL, md)
                self.assertIn("Expected by chance", md)

    def test_codes_only_channels_cannot_see_narrative_only_plants(self) -> None:
        for pack_id, doc in self.docs.items():
            with self.subTest(pack=pack_id):
                self.assertEqual(doc["channels"]["S"]["summary"]["found"], 0)

    def test_the_review_list_names_sites_and_records(self) -> None:
        for pack_id, doc in self.docs.items():
            with self.subTest(pack=pack_id):
                self.assertTrue(doc["review"])
                for entry in doc["review"]:
                    self.assertGreaterEqual(len(entry["sites"]), 1)
                    self.assertEqual(entry["records"] >= len(entry["record_refs"]) > 0, True)
                    self.assertLessEqual(len(entry["record_refs"]), A.MAX_REVIEW_REFS)

    def test_run_on_the_demo_files_reproduces_the_numbers_with_the_real_label(self) -> None:
        d = self.tmp / "device_quality"
        code, _, err = cli(["run", "--pack", "device_quality", "--records", str(d / "export.csv"), "--outcomes",
                            str(d / "outcomes.csv"), "--out", str(self.tmp / "rerun")])
        self.assertEqual(code, 0, err)
        doc = json.loads((self.tmp / "rerun" / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["label"], A.LABEL)
        demo = self.docs["device_quality"]
        self.assertEqual(doc["channels"], demo["channels"])
        self.assertEqual(doc["review"], demo["review"])

    def test_cli_errors_exit_2(self) -> None:
        code, _, err = cli(["run", "--pack", "no_such_pack", "--records", "x.csv", "--outcomes", "y.csv",
                            "--out", str(self.tmp / "e")])
        self.assertEqual(code, 2)
        self.assertIn("pack", err)
        too_short = self.tmp / "short.csv"
        with open(self.tmp / "device_quality" / "export.csv", encoding="utf-8") as fh:
            too_short.write_text("".join(fh.readlines()[:40]), encoding="utf-8")
        code, _, err = cli(["run", "--pack", "device_quality", "--records", str(too_short), "--outcomes",
                            str(self.tmp / "device_quality" / "outcomes.csv"), "--out", str(self.tmp / "e2")])
        self.assertEqual(code, 2)
        self.assertIn("weeks", err)


def strip_comparators(doc: dict[str, Any]) -> dict[str, Any]:
    """An audit document as it reads without the comparator channels."""
    out = json.loads(json.dumps(doc))
    out.pop("comparators", None)
    for name in A.COMPARATORS:
        out["channels"].pop(name, None)
    return out


class ComparatorUnitTests(unittest.TestCase):
    def test_prr_and_yates_chi_squared(self) -> None:
        prr, chi2 = A.prr_stats(6, 4, 20, 170)
        self.assertAlmostEqual(prr, (6 / 10) / (20 / 190))
        self.assertAlmostEqual(chi2, 200 * (abs(6 * 170 - 4 * 20) - 100) ** 2 / (10 * 190 * 26 * 174))
        self.assertEqual(A.prr_stats(3, 0, 0, 50)[0], float("inf"))       # no other entity has the failure
        self.assertIsNone(A.prr_stats(3, 2, 0, 0))                        # no other entity at all
        self.assertEqual(A.prr_stats(1, 9, 10, 90), (1.0, 0.0))           # |ad - bc| below n / 2

    def test_pooled_cells_sum_every_site(self) -> None:
        def cell(week: str, n: int, conf: float) -> dict[str, Any]:
            return {"entity_type": "product", "entity_id": "SD-9", "predicate": "leak", "iso_week": week,
                    "channel": "codes", "n": n, "n_roots": n, "n_reporters": n, "res_conf_min": conf}
        got = A.pooled_cells({"b": [cell("2024-W02", 2, 0.9), cell("2024-W03", 1, 1.0)],
                              "a": [cell("2024-W02", 3, 0.97)]})
        self.assertEqual([(c["iso_week"], c["n"], c["n_roots"], c["n_reporters"], c["res_conf_min"]) for c in got],
                         [("2024-W02", 5, 5, 5, 0.9), ("2024-W03", 1, 1, 1, 1.0)])
        with self.assertRaisesRegex(A.AuditError, "exact counts"):
            A.pooled_cells({"a": [{**cell("2024-W02", 3, 0.9), "n": None}]})

    def test_the_pooled_pack_changes_only_the_cross_site_minimums(self) -> None:
        pack = load_pack("device_quality")
        pooled = A.pooled_pack(pack)
        want = thaw(pack.detectors)
        want["burst"]["min_sites"] = want["cooccurrence"]["min_sites"] = 1
        self.assertEqual(thaw(pooled.detectors), want)
        self.assertNotEqual(pooled.detector_hash, pack.detector_hash)
        self.assertEqual((pooled.config_hash, pooled.vocabulary_hash, pooled.rules), (pack.config_hash,
                                                                                      pack.vocabulary_hash, pack.rules))
        self.assertEqual(pack.detectors["burst"]["min_sites"], 2)           # the loaded pack is untouched

    def test_prr_alerts_once_per_episode_under_budget_and_cooldown(self) -> None:
        pack = load_pack("device_quality")
        d = pack.detectors
        self.assertEqual((d["baseline_weeks"], d["cooldown_weeks"], d["alert_budget_per_week"]), (26, 4, 5))
        weeks = [f"2024-W{w:02d}" for w in range(1, 53)] + [f"2025-W{w:02d}" for w in range(1, 9)]

        def cells(eid: str, pred: str, counts: dict[int, int]) -> list[dict[str, Any]]:
            return [{"entity_type": "product", "entity_id": eid, "predicate": pred, "iso_week": weeks[i],
                     "channel": "codes", "n": n} for i, n in counts.items()]
        rows = []
        for i in range(len(weeks)):                          # background: forty products, two failures, every week
            for j in range(40):
                rows += cells(f"P-{j}", "leak", {i: 2}) + cells(f"P-{j}", "crack", {i: 2})
        rows += cells("P-0", "overheat", {10: 2, 11: 2, 12: 1})               # a burst no other product shares
        result = A.prr_result(pack, rows, weeks, tie_salt="t")
        # the count reaches 3 at index 11 (2024-W12); the key keeps signalling while the burst is in the 26-week
        # window, so it cools and does not alert again
        self.assertEqual([(a["week"], a["key"]) for a in result["alerts"]], [("2024-W12", "product:P-0:overheat")])
        # it stops signalling at index 37 and is released four quiet steps later; a new burst then alerts again
        late = A.prr_result(pack, rows + cells("P-0", "overheat", {50: 4}), weeks, tie_salt="t")
        self.assertEqual([a["week"] for a in late["alerts"]], ["2024-W12", "2024-W51"])
        soon = A.prr_result(pack, rows + cells("P-0", "overheat", {38: 4}), weeks, tie_salt="t")
        self.assertEqual([a["week"] for a in soon["alerts"]], ["2024-W12"])     # still cooling at index 38
        # a budget of 5 a week: six keys signal at once (P-0 is cooling), five alert, ranked by chi-squared
        burst = []
        for j in range(7):
            burst += cells(f"P-{j}", "overheat", {20: 3 + j})
        top = A.prr_result(pack, rows + burst, weeks, tie_salt="t")["alerts"]
        at = [a for a in top if a["week"] == "2024-W21"]
        self.assertEqual([a["key"] for a in at], [f"product:P-{j}:overheat" for j in (6, 5, 4, 3, 2)])
        self.assertEqual([a["rank"] for a in at], [1, 2, 3, 4, 5])
        self.assertEqual(sorted(at, key=lambda a: -a["score"]), at)


class ComparatorEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.docs = {}
        vehicle_pack = str(ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack")
        for name, ref in (("device_quality", "device_quality"), ("vehicles", vehicle_pack)):
            pack = A._load(ref)
            records, outcomes = A.demo_inputs(pack, cls.tmp / name, seed=1, weeks=48)
            rows, given = A.read_rows(records), A.read_outcomes(outcomes, pack)
            cls.docs[name] = {flag: A.audit(pack, rows, given, synthetic=True, comparators=flag)
                              for flag in (False, True)}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_x_s_and_r_mf_are_identical_with_and_without_the_comparators(self) -> None:
        for name, docs in self.docs.items():
            with self.subTest(pack=name):
                off, on = docs[False], docs[True]
                for channel in A.CHANNELS:
                    self.assertEqual(on["channels"][channel], off["channels"][channel], channel)
                self.assertEqual(on["review"], off["review"])
                self.assertEqual(strip_comparators(on), json.loads(json.dumps(off)))
                self.assertNotIn("comparators", off)
                self.assertEqual(sorted(off["channels"]), sorted(A.CHANNELS))

    def test_the_comparators_are_scored_like_the_other_channels(self) -> None:
        for name, docs in self.docs.items():
            with self.subTest(pack=name):
                on = docs[True]
                self.assertEqual(on["comparators"]["channels"], list(A.COMPARATORS))
                for channel in A.COMPARATORS:
                    s = on["channels"][channel]["summary"]
                    self.assertEqual(s["outcomes"], on["outcomes"]["in_scope"])
                    self.assertIsNotNone(s["expected_found"])
                    self.assertEqual(len(on["channels"][channel]["alert_timeline"]), s["alerts"])
                md = A.render(on)
                self.assertIn("| P | ", md)
                self.assertIn("| PRR | ", md)
                self.assertNotIn("| P | ", A.render(docs[False]))

    def test_the_cli_leaves_them_out_on_request(self) -> None:
        d = self.tmp / "device_quality"
        code, out, err = cli(["run", "--pack", "device_quality", "--records", str(d / "export.csv"), "--outcomes",
                              str(d / "outcomes.csv"), "--out", str(self.tmp / "off"), "--no-comparators"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("PRR", out)
        doc = json.loads((self.tmp / "off" / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["channels"], json.loads(json.dumps(self.docs["device_quality"][False]["channels"])))

    def test_without_r_mf_the_comparators_say_why(self) -> None:
        pack = load_pack("device_quality")
        egress = pack.egress.__class__(**{**pack.egress.__dict__, "central_allowed_fields": ("codes",)})
        from dataclasses import replace
        narrow = replace(pack, egress=egress)
        d = self.tmp / "device_quality"
        doc = A.audit(narrow, A.read_rows(d / "export.csv"), A.read_outcomes(d / "outcomes.csv", pack),
                      synthetic=True)
        self.assertIsNone(doc["channels"]["R_mf"]["summary"])
        for channel in A.COMPARATORS:
            self.assertIsNone(doc["channels"][channel]["summary"])
            self.assertIn("needs model-free R's cells", doc["channels"][channel]["reason"])


class PooledChannelTests(unittest.TestCase):
    def test_a_burst_spread_thin_over_sites_is_seen_only_pooled(self) -> None:
        # twelve sites, three vehicles; a background of codes every week, then eight extra engine complaints on one
        # vehicle in eight weeks, never two at one site: no site sees a burst, the pooled count does
        pack = A._load(str(ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack"))
        sites = [f"s{i:02d}" for i in range(1, 13)]
        vehicles = ["SYN-V0001-2020", "SYN-V0002-2020", "SYN-V0003-2020"]
        comps = ["AIR BAGS", "STEERING", "SUSPENSION"]
        rows: list[dict[str, Any]] = []
        start = date(2024, 1, 1)

        def add(week: int, site: str, vehicle: str, comp: str) -> None:
            day = start.toordinal() + 7 * week + len(rows) % 5
            rows.append({"odino": str(len(rows) + 1), "state": site, "received": date.fromordinal(day).strftime(
                "%Y%m%d"), "components": [comp], "vehicle": vehicle, "summary": f"Problem with the {comp.lower()}."})
        for week in range(52):
            for i, site in enumerate(sites):
                add(week, site, vehicles[(week + i) % 3], comps[(week + 2 * i) % 3])
        for k in range(8):
            add(36 + k, sites[k], vehicles[0], "ENGINE")
        outcome = A.Outcome("O1", "2024-10-28", "vehicle", vehicles[0], "engine")
        doc = A.audit(pack, rows, [outcome], synthetic=True)
        found = {n: doc["channels"][n]["by_outcome"][0]["found"] for n in A.channel_names(doc)}
        self.assertEqual((found["X"], found["S"], found["R_mf"]), (False, False, False))
        self.assertTrue(found["P"])


class ComparatorWalkTests(unittest.TestCase):
    def test_prr_walks_from_the_first_week_and_drops_early_alerts_as_the_detectors_do(self) -> None:
        # twelve sites, three vehicles, every failure spread evenly; then engine complaints on the first vehicle from
        # the first week, and fuel complaints on the second from week 30, both on no other vehicle
        pack = A._load(str(ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack"))
        sites = [f"s{i:02d}" for i in range(1, 13)]
        vehicles = ["SYN-V0001-2020", "SYN-V0002-2020", "SYN-V0003-2020"]
        comps = ["AIR BAGS", "STEERING", "SUSPENSION"]
        rows: list[dict[str, Any]] = []
        start = date(2024, 1, 1)

        def add(week: int, site: str, vehicle: str, comp: str) -> None:
            day = start.toordinal() + 7 * week + len(rows) % 5
            rows.append({"odino": str(len(rows) + 1), "state": site, "received": date.fromordinal(day).strftime(
                "%Y%m%d"), "components": [comp], "vehicle": vehicle, "summary": f"Problem with the {comp.lower()}."})
        for week in range(52):
            for i, site in enumerate(sites):
                add(week, site, vehicles[(week + i) % 3], comps[(week + 2 * i) % 3])
            add(week, sites[week % 12], vehicles[0], "ENGINE")
        for week in range(30, 34):
            add(week, sites[week % 12], vehicles[1], "FUEL SYSTEM, GASOLINE")
            add(week, sites[(week + 6) % 12], vehicles[1], "FUEL SYSTEM, GASOLINE")
        opened = date.fromordinal(start.toordinal() + 7 * 45).isoformat()
        outcomes = [A.Outcome("O1", opened, "vehicle", vehicles[0], "engine"),
                    A.Outcome("O2", opened, "vehicle", vehicles[1], "fuel_system_gasoline")]
        doc = A.audit(pack, rows, outcomes, synthetic=True)
        found = {r["outcome_id"]: r["found"] for r in doc["channels"]["PRR"]["by_outcome"]}
        self.assertEqual(found, {"O1": False, "O2": True})

        # the same walks outside the audit: both channels walk from the export's first week
        records = list(map_rows(rows, pack, synthetic=True).records)
        days = sorted(r["received_date"][:10] for r in records)
        weeks = replay_weeks(days[0], days[-1])
        master = A._master_data(pack, records, sorted({r["site"] for r in records}))
        cells = r_mf_cells(pack, records, master_data=master, last_week=weeks[-1])
        raw = A.comparator_results(pack, cells, weeks, as_of=closing_date(pack, weeks[-1]), tie_salt="pilot")
        evaluated_from = doc["weeks"]["evaluated_from"]
        self.assertEqual(evaluated_from, weeks[19])
        engine = f"vehicle:{vehicles[0]}:engine"
        # PRR: the standing engine signal reaches chi-squared 4 in week index 3, alerts there, before the evaluated
        # weeks, and keeps signalling, so it cools from then on
        self.assertEqual([a["week"] for a in raw["PRR"]["alerts"] if a["key"] == engine], [weeks[3]])
        # the detectors walk the same weeks and cannot alert before the evaluated weeks: no site has the history
        self.assertEqual([w["week"] for w in raw["P"]["weeks"]], weeks)
        self.assertEqual([w["candidates"] for w in raw["P"]["weeks"][:19]], [0] * 19)
        # the audit keeps exactly each walk's alerts from the first evaluated week on
        for name in A.COMPARATORS:
            kept = [(a["week"], a["key"]) for a in raw[name]["alerts"] if a["week"] >= evaluated_from]
            timeline = [(t["week"], max(t["keys"], key=len)) for t in doc["channels"][name]["alert_timeline"]]
            self.assertEqual(timeline, kept, name)


class GuardTests(unittest.TestCase):
    def test_the_audit_imports_no_inference_module_or_model_client(self) -> None:
        for path in sorted((ROOT / "mycelic" / "collective" / "pilot").glob("*.py")):
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            with self.subTest(module=module):
                self.assertEqual(forbidden_imports(path.read_text(encoding="utf-8"), module, EVALUATE_FORBIDDEN), [])


if __name__ == "__main__":
    unittest.main()
