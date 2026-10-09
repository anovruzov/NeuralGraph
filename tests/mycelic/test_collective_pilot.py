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

from mycelic.collective.packs.connector import map_rows
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import load_pack
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


class GuardTests(unittest.TestCase):
    def test_the_audit_imports_no_inference_module_or_model_client(self) -> None:
        for path in sorted((ROOT / "mycelic" / "collective" / "pilot").glob("*.py")):
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            with self.subTest(module=module):
                self.assertEqual(forbidden_imports(path.read_text(encoding="utf-8"), module, EVALUATE_FORBIDDEN), [])


if __name__ == "__main__":
    unittest.main()
