"""Public replay V001's pack rule and exporter (``tools/market/vehicle_pack.py``, ``nhtsa_export.py``), offline."""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "market"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "market" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


V = _load("vehicle_pack")
E = _load("nhtsa_export")
COUNTS = [["ENGINE", 5000], ["AIR BAGS", 4000], ["ENGINE AND ENGINE COOLING", 1500], ["VISIBILITY/WIPER", 1800],
          ["VISIBILITY", 1200], ["UNKNOWN OR OTHER", 9000], ["SERVICE BRAKES, HYDRAULIC", 1100],
          ["SERVICE BRAKES", 3000], ["TRAILER HITCHES", 200]]


def complaint(odino: str, make: str, model: str, year: str, comp: str, state: str, ldate: str,
              text: str = "The engine stalled.", prod: str = "V") -> list[str]:
    row = [""] * 51
    row[E.C_ODINO], row[E.C_MAKE], row[E.C_MODEL], row[E.C_YEAR] = odino, make, model, year
    row[E.C_COMP], row[E.C_STATE], row[E.C_LDATE], row[E.C_DESCR], row[E.C_PROD] = comp, state, ldate, text, prod
    row[50] = "Operator Name Never Exported"
    return row


def recall(campno: str, make: str, model: str, year: str, comp: str, rcdate: str, kind: str = "V") -> list[str]:
    row = [""] * 29
    row[E.R_CAMPNO], row[E.R_MAKE], row[E.R_MODEL], row[E.R_YEAR] = campno, make, model, year
    row[E.R_COMP], row[E.R_TYPE], row[E.R_RCDATE] = comp, kind, rcdate
    return row


class PackRuleTests(unittest.TestCase):
    def test_vehicle_ids(self) -> None:
        self.assertEqual(V.vehicle_id("Ford", "F-150", "2021"), "FORD-F150-2021")
        self.assertEqual(V.vehicle_id("MERCEDES-BENZ", "GLE 350 4MATIC SUV LONG NAME", "2022"),
                         "MERCEDESBENZ-GLE3504MATICSUVLONGN-2022")
        self.assertIsNone(V.vehicle_id("FORD", "F-150", "9999"))
        self.assertIsNone(V.vehicle_id("FORD", "", "2021"))

    def test_categories_phrases_and_owners(self) -> None:
        got = V.choose(COUNTS)
        by = {p["category"]: p for p in got["predicates"]}
        self.assertEqual([p["category"] for p in got["predicates"]],
                         ["ENGINE", "AIR BAGS", "SERVICE BRAKES", "VISIBILITY/WIPER", "ENGINE AND ENGINE COOLING",
                          "SERVICE BRAKES, HYDRAULIC"])
        self.assertEqual(by["ENGINE AND ENGINE COOLING"]["lexicon"], ["engine cooling"])
        self.assertEqual(by["SERVICE BRAKES, HYDRAULIC"]["lexicon"], ["hydraulic"])
        self.assertEqual(by["VISIBILITY/WIPER"]["lexicon"], ["visibility", "wiper"])
        self.assertEqual(got["dropped"], ["VISIBILITY"])        # its only phrase belongs to a larger category
        self.assertEqual(by["SERVICE BRAKES, HYDRAULIC"]["predicate"], "service_brakes_hydraulic")
        self.assertEqual(V.category_of("AIR BAGS:FRONTAL:DRIVER SIDE"), "AIR BAGS")

    def test_the_built_pack_loads_and_runs_its_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pack"
            result = V.build(V.choose(COUNTS), out)
            self.assertEqual(result["pack"]["id"], "vehicle_complaints")
            self.assertEqual(result["predicates"], 7)
            mapping = json.loads((out / "mapping.json").read_text())
            self.assertEqual(mapping["codes"][0]["value_map"]["UNKNOWN OR OTHER"], V.GENERIC_CODE)
            self.assertEqual(mapping["persons"], {})
            egress = json.loads((out / "egress.json").read_text())
            self.assertIn("narrative", egress["never_fields"])
            from mycelic.collective.pilot import audit as A
            self.assertEqual(A.main(["demo", "--pack", str(out), "--out", str(Path(tmp) / "demo"), "--weeks", "48"]),
                             0)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cats = {"ENGINE", "AIR BAGS", "UNKNOWN OR OTHER"}

    def test_complaints_one_row_per_odino_and_nothing_personal(self) -> None:
        rows = [complaint("1", "FORD", "F-150", "2021", "ENGINE:IGNITION", "TX", "20230105"),
                complaint("1", "FORD", "F-150", "2021", "AIR BAGS", "TX", "20230105"),
                complaint("1", "FORD", "F-150", "2021", "TRAILER HITCHES", "TX", "20230105"),
                complaint("2", "FORD", "ESCAPE", "9999", "ENGINE", "CA", "20230106"),
                complaint("3", "FORD", "ESCAPE", "2020", "ENGINE", "", "20230106"),
                complaint("4", "FORD", "ESCAPE", "2020", "ENGINE", "CA", "20220106"),
                complaint("5", "CHEVROLET", "BOLT", "2020", "ENGINE", "CA", "20230106"),
                complaint("6", "FORD", "TIRE", "2020", "TIRES", "CA", "20230106", prod="T")]
        out, counts = E.complaints(rows, "FORD", "20230101", "20241231", self.cats)
        self.assertEqual(out, [{"odino": "1", "state": "tx", "received": "20230105",
                                "components[]": ["AIR BAGS", "ENGINE", "UNKNOWN OR OTHER"],
                                "vehicle": "FORD-F150-2021", "summary": "The engine stalled."}])
        self.assertEqual((counts["no_model_year"], counts["no_state"], counts["outside_window"],
                          counts["not_vehicle"], counts["complaints"]), (1, 1, 1, 1, 1))
        self.assertNotIn("Operator", json.dumps(out))

    def test_recalls_one_outcome_per_campaign_and_vehicle(self) -> None:
        preds = {"ENGINE": "engine", "AIR BAGS": "air_bags"}
        rows = [recall("23V001000", "FORD", "F-150", "2021", "ENGINE:COOLING", "20230301"),
                recall("23V001000", "FORD", "F-150", "2022", "ENGINE", "20230301"),
                recall("23V002000", "FORD", "ESCAPE", "2020", "ENGINE", "20230401"),
                recall("23V002000", "FORD", "ESCAPE", "2020", "AIR BAGS", "20230401"),
                recall("23V003000", "FORD", "ESCAPE", "2020", "TRAILER HITCHES", "20230501"),
                recall("22V009000", "FORD", "ESCAPE", "2020", "ENGINE", "20221201"),
                recall("23E001000", "FORD", "ESCAPE", "2020", "ENGINE", "20230601", kind="E")]
        out, counts = E.recalls(rows, "FORD", "20230101", "20241231", preds)
        self.assertEqual([(o["outcome_id"], o["opened"], o["predicate"]) for o in out],
                         [("23V001000/FORD-F150-2021", "2023-03-01", "engine"),
                          ("23V001000/FORD-F150-2022", "2023-03-01", "engine"),
                          ("23V002000/FORD-ESCAPE-2020", "2023-04-01", ""),     # two categories: any predicate
                          ("23V003000/FORD-ESCAPE-2020", "2023-05-01", "")])    # not a pack category
        self.assertEqual((counts["campaigns"], counts["outside_window"], counts["not_vehicle"]), (3, 1, 1))

    def test_export_feeds_the_pilot_audit(self) -> None:
        from mycelic.collective.pilot import audit as A
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack"
            V.build(V.choose(COUNTS), pack)
            cats, preds = E.pack_categories(pack)
            start = date(2023, 1, 2)
            rows = []
            n = 0
            for week in range(60):
                for state in ("TX", "CA", "NY", "FL", "OH", "WA"):
                    for model in ("F-150", "ESCAPE", "EXPLORER"):
                        n += 1
                        comp = "AIR BAGS" if (model == "ESCAPE" and 40 <= week < 48 and state in ("TX", "CA", "NY")) \
                            else ("ENGINE" if n % 3 else "SERVICE BRAKES")
                        day = (start + timedelta(days=7 * week + n % 5)).strftime("%Y%m%d")
                        rows.append(complaint(str(n), "FORD", model, "2021", comp, state, day,
                                              text=f"Problem with the {comp.lower()}."))
            export, _ = E.complaints(rows, "FORD", "20230101", "20241231", cats)
            A.write_csv(export, Path(tmp) / "export.csv")
            outcomes, _ = E.recalls([recall("24V100000", "FORD", "ESCAPE", "2021", "AIR BAGS:INFLATOR", "20240115")],
                                    "FORD", "20230101", "20241231", preds)
            E.write_outcomes(outcomes, Path(tmp) / "outcomes.csv")
            code = A.main(["run", "--pack", str(pack), "--records", str(Path(tmp) / "export.csv"), "--outcomes",
                           str(Path(tmp) / "outcomes.csv"), "--out", str(Path(tmp) / "audit")])
            self.assertEqual(code, 0)
            doc = json.loads((Path(tmp) / "audit" / "audit.json").read_text())
            self.assertEqual(doc["export"]["rejected"], {})
            self.assertEqual(len(doc["export"]["sites"]), 6)
            self.assertEqual(doc["export"]["coverage"]["resolved_entity"]["share"], 1.0)
            self.assertEqual(doc["outcomes"]["in_scope"], 1)
            # the planted air bag burst: one complaint per state and week sits under k in the codes-only cells,
            # but the codes and text cells together carry it
            self.assertTrue(doc["channels"]["X"]["by_outcome"][0]["found"])


if __name__ == "__main__":
    unittest.main()
