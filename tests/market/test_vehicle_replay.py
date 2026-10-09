"""Public replays V001 and V002: pack rule, exporter and summary (``tools/market/vehicle_*.py``, ``nhtsa_export.py``),
offline."""
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
S = _load("vehicle_summary")
I = _load("nhtsa_inv_probe")
ES = _load("nhtsa_event_study")
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



def investigation(action: str, make: str, model: str, year: str, comp: str, odate: str) -> list[str]:
    row = [""] * 11
    row[E.I_ACTION], row[E.I_MAKE], row[E.I_MODEL], row[E.I_YEAR], row[E.I_COMP], row[E.I_ODATE] = \
        action, make, model, year, comp, odate
    row[10] = "Summary text never exported"
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
                                "components": ["AIR BAGS", "ENGINE", "UNKNOWN OR OTHER"],
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

    def test_investigations_preliminary_evaluations_and_petitions_only(self) -> None:
        preds = {"ENGINE": "engine", "AIR BAGS": "air_bags"}
        rows = [investigation("PE23001", "FORD", "F-150", "2021", "ENGINE AND ENGINE COOLING:ENGINE", "20230301"),
                investigation("PE23001", "FORD", "F-150", "2021", "ENGINE", "20230301"),
                investigation("DP23002", "FORD", "ESCAPE", "2020", "AIR BAGS", "20230401"),
                investigation("DP23002", "FORD", "ESCAPE", "2020", "ENGINE", "20230401"),
                investigation("EA23003", "FORD", "ESCAPE", "2020", "ENGINE", "20230501"),
                investigation("RQ23004", "FORD", "ESCAPE", "2020", "ENGINE", "20230501"),
                investigation("PE22009", "FORD", "ESCAPE", "2020", "ENGINE", "20221201"),
                investigation("PE23005", "FORD", "ESCAPE", "9999", "ENGINE", "20230601"),
                investigation("PE23006", "JEEP", "WRANGLER", "2020", "ENGINE", "20230601")]
        out, counts = E.investigations(rows, "FORD", "20230101", "20241231", preds)
        self.assertEqual([(o["outcome_id"], o["opened"], o["predicate"]) for o in out],
                         [("DP23002/FORD-ESCAPE-2020", "2023-04-01", ""),          # two categories: any predicate
                          ("PE23001/FORD-F150-2021", "2023-03-01", "engine")])     # ENGINE AND ... is not in preds
        self.assertEqual((counts["kind_PE"], counts["kind_DP"], counts["kind_EA"], counts["kind_RQ"]), (3, 2, 1, 1))
        self.assertEqual((counts["other_kind"], counts["outside_window"], counts["no_model_year"],
                          counts["investigations"], counts["make_rows"]), (2, 1, 1, 2, 8))
        self.assertNotIn("Summary", json.dumps(out))

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
            with open(Path(tmp) / "export.csv", newline="", encoding="utf-8") as fh:
                # the mapping's codes path; run 001 exported "components[][]", which read back as no codes
                self.assertIn("components[]", next(csv.reader(fh)))
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
            self.assertEqual(doc["export"]["coverage"]["codes"]["share"], 1.0)
            self.assertEqual(doc["outcomes"]["in_scope"], 1)
            # the planted air bag burst reaches every channel, the codes-only ones included
            for channel in ("X", "S", "R_mf"):
                self.assertTrue(doc["channels"][channel]["by_outcome"][0]["found"], channel)


class RunFileTests(unittest.TestCase):
    def test_one_make_or_several(self) -> None:
        self.assertEqual(E.run_makes({"make": "ford"}), ["FORD"])
        self.assertEqual(E.run_makes({"makes": ["Chevrolet", "JEEP", "MERCEDES-BENZ"]}),
                         ["CHEVROLET", "JEEP", "MERCEDES-BENZ"])
        for bad in ({"makes": []}, {"makes": ["JEEP", "jeep"]}, {"makes": ["../FORD"]}, {"make": ""}):
            with self.assertRaises(ValueError):
                E.run_makes(bad)

    def test_every_committed_run_file_names_its_makes(self) -> None:
        for path in sorted((ROOT / "docs" / "collective" / "replay" / "vehicles").glob("run-*.json")):
            run = json.loads(path.read_text())
            self.assertTrue(E.run_makes(run), path.name)
            self.assertEqual((run["lookback_weeks"], run["post_weeks"], run["saw_recall_outcomes"]), (26, 26, "yes"))

    def test_the_committed_pack_is_frozen(self) -> None:
        # CHOICE-V002 pins V001's pack; a change to it is a new choice file
        from mycelic.collective.packs.loader import load_pack_dir
        pack = load_pack_dir(ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack")
        hashes = (pack.config_hash, pack.vocabulary_hash, pack.detector_hash, pack.fixtures_hash)
        self.assertEqual(tuple(h[:8] for h in hashes), ("a588e72d", "867df7fc", "325b125a", "9d9d8bd6"))


def _audit(found: dict[str, tuple[int, float, float]], in_scope: int = 10,
           own_post: dict[str, tuple[float | None, float | None]] | None = None) -> dict:
    channels = {c: {"summary": {"alerts": 4, "found": f, "expected_found": e, "p_value": p, "unexplained_alerts": 1}}
                for c, (f, e, p) in found.items()}
    for c, (e, p) in (own_post or {}).items():           # the null without own post-opening alerts, when kept
        channels[c]["summary"].update({"expected_found_excluding_own_post": e, "p_value_excluding_own_post": p})
    return {"export": {"records": 100, "sites": ["ca", "tx"], "coverage": {"codes": {"share": 1.0}}},
            "outcomes": {"in_scope": in_scope}, "weeks": {"evaluated_weeks": 87}, "channels": channels}


class SummaryTests(unittest.TestCase):
    def test_fisher(self) -> None:
        self.assertAlmostEqual(S.fisher([0.5]), 0.5)
        self.assertAlmostEqual(S.fisher([0.05, 0.05]), 0.0174787, places=6)   # chi-squared, 4 df, at 11.98
        self.assertEqual(S.fisher([1.0, 1.0, 1.0]), 1.0)
        with self.assertRaises(ValueError):
            S.fisher([])

    def test_sums_and_the_rule(self) -> None:
        strong = {"X": (6, 1.0, 0.0115), "S": (1, 1.0, 0.6), "R_mf": (0, 0.0, None)}
        weak = {"X": (3, 1.5, 0.05), "S": (0, 1.2, 1.0), "R_mf": (0, 0.0, None)}
        got = S.summarise({"JEEP": _audit(strong), "DODGE": _audit(weak, in_scope=5)})
        self.assertEqual(list(got["makes"]), ["DODGE", "JEEP"])
        self.assertEqual(got["in_scope"], 15)
        x, s, r = (got["channels"][c] for c in ("X", "S", "R_mf"))
        self.assertEqual((x["found"], x["expected_found"], x["makes_above_chance"], x["makes_scored"]), (9, 2.5, 2, 2))
        self.assertTrue(x["beyond_chance"])                       # Fisher p of 0.0115 and 0.05 is about 0.0045
        self.assertFalse(s["beyond_chance"])
        self.assertEqual((r["fisher_p"], r["beyond_chance"], r["makes_scored"]), (None, False, 0))
        self.assertAlmostEqual(got["alpha"], 0.05 / 3)

    def test_cli_reads_one_directory_per_make(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for make in ("HONDA", "NISSAN"):
                (Path(tmp) / make).mkdir()
                (Path(tmp) / make / "audit.json").write_text(json.dumps(_audit(
                    {"X": (1, 1.0, 0.5), "S": (1, 1.0, 0.5), "R_mf": (1, 1.0, 0.5)})))
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(S.main([tmp]), 0)
            self.assertEqual(sorted(json.loads(buf.getvalue())["makes"]), ["HONDA", "NISSAN"])
            self.assertEqual(S.main([str(Path(tmp) / "HONDA")]), 1)   # no make directories under it

    # the keys V002's summary had before the null without own post-opening alerts was added beside them
    V002_MAKE_KEYS = ("records", "sites", "codes_share", "in_scope", "evaluated_weeks")
    V002_MAKE_CHANNEL_KEYS = ("alerts", "found", "expected_found", "p_value", "unexplained_alerts")
    V002_CHANNEL_KEYS = ("found", "expected_found", "alerts", "makes_scored", "makes_above_chance", "fisher_p",
                         "beyond_chance")
    STRONG = {"X": (6, 1.0, 0.0115), "S": (1, 1.0, 0.6), "R_mf": (0, 0.0, None)}
    WEAK = {"X": (3, 1.5, 0.05), "S": (0, 1.2, 1.0), "R_mf": (0, 0.0, None)}

    def test_the_null_without_own_post_alerts_is_reported_beside_the_rule(self) -> None:
        plain = S.summarise({"JEEP": _audit(self.STRONG), "DODGE": _audit(self.WEAK, in_scope=5)})
        got = S.summarise({
            "JEEP": _audit(self.STRONG, own_post={"X": (0.5, 0.02), "S": (0.25, 0.7), "R_mf": (None, None)}),
            "DODGE": _audit(self.WEAK, in_scope=5, own_post={"X": (0.75, 0.04), "S": (1.0, 1.0),
                                                             "R_mf": (None, None)})})
        # every key and value V002's rule and table read is unchanged
        self.assertEqual({k: got[k] for k in ("kind", "alpha", "in_scope")},
                         {k: plain[k] for k in ("kind", "alpha", "in_scope")})
        for make in ("JEEP", "DODGE"):
            for k in self.V002_MAKE_KEYS:
                self.assertEqual(got["makes"][make][k], plain["makes"][make][k])
            for c in S.CHANNELS:
                for k in self.V002_MAKE_CHANNEL_KEYS:
                    self.assertEqual(got["makes"][make]["channels"][c][k], plain["makes"][make]["channels"][c][k])
        for c in S.CHANNELS:
            for k in self.V002_CHANNEL_KEYS:
                self.assertEqual(got["channels"][c][k], plain["channels"][c][k], (c, k))
        # and beside them, per make and channel and per channel
        self.assertEqual(got["makes"]["JEEP"]["channels"]["X"]["expected_found_excluding_own_post"], 0.5)
        self.assertEqual(got["makes"]["DODGE"]["channels"]["X"]["p_value_excluding_own_post"], 0.04)
        x, s, r = (got["channels"][c] for c in S.CHANNELS)
        self.assertEqual((x["expected_found_excluding_own_post"], x["makes_scored_excluding_own_post"]), (1.25, 2))
        self.assertAlmostEqual(x["fisher_p_excluding_own_post"], S.fisher([0.02, 0.04]))
        self.assertAlmostEqual(x["fisher_p_excluding_own_post"], 0.006505, places=6)   # chi-squared, 4 df, at 14.26
        self.assertEqual(s["expected_found_excluding_own_post"], 1.25)
        self.assertAlmostEqual(s["fisher_p_excluding_own_post"], S.fisher([0.7, 1.0]))
        # no outcome in scope: no p, as for the audited null; its expected count sums as 0
        self.assertEqual((r["expected_found_excluding_own_post"], r["makes_scored_excluding_own_post"],
                          r["fisher_p_excluding_own_post"]), (0.0, 0, None))
        self.assertNotIn("beyond_chance_excluding_own_post", x)   # no verdict: CHOICE-V002's rule is the audited p

    def test_audits_written_before_the_null_was_kept_give_none(self) -> None:
        old = S.summarise({"JEEP": _audit(self.STRONG), "DODGE": _audit(self.WEAK, in_scope=5)})
        for c in S.CHANNELS:
            self.assertEqual(old["makes"]["JEEP"]["channels"][c]["expected_found_excluding_own_post"], None)
            self.assertEqual(old["makes"]["JEEP"]["channels"][c]["p_value_excluding_own_post"], None)
            self.assertEqual((old["channels"][c]["expected_found_excluding_own_post"],
                              old["channels"][c]["makes_scored_excluding_own_post"],
                              old["channels"][c]["fisher_p_excluding_own_post"]), (None, 0, None))
        mixed = S.summarise({"JEEP": _audit(self.STRONG, own_post={c: (0.5, 0.02) for c in S.CHANNELS}),
                             "DODGE": _audit(self.WEAK, in_scope=5)})
        self.assertEqual((mixed["channels"]["X"]["expected_found_excluding_own_post"],
                          mixed["channels"]["X"]["fisher_p_excluding_own_post"]), (None, None))
        self.assertEqual(mixed["makes"]["JEEP"]["channels"]["X"]["p_value_excluding_own_post"], 0.02)


class InvestigationProbeTests(unittest.TestCase):
    def test_reads_only_the_shape(self) -> None:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("FLAT_INV.txt", "PE23001\tFORD\tF-150\t2021\tSECRET SUMMARY\nPE23002\tJEEP\tWRANGLER\t2020\tX\n")
        blobs = {I.INV_DOC: b"INVESTIGATIONS field list", I.INV_CANDIDATES[0]: buf.getvalue()}

        def get(url: str) -> bytes:
            if url not in blobs:
                raise OSError("404")
            return blobs[url]
        got = I.probe(get)
        self.assertEqual(got["files"][I.INV_CANDIDATES[0]]["rows"], 2)
        self.assertEqual(got["files"][I.INV_CANDIDATES[0]]["fields_per_row"], {5: 2})
        self.assertIn("error", got["files"][I.INV_CANDIDATES[1]])
        text = json.dumps(got)
        for content in ("SECRET", "FORD", "PE23001", "WRANGLER"):
            self.assertNotIn(content, text)


class EventStudyTests(unittest.TestCase):
    def test_quarters_and_placement(self) -> None:
        self.assertEqual([ES.quarter_of(d) for d in (-1, -91, -92, 0, 90)], [-1, -1, -2, 0, 0])
        rows = [complaint("1", "FORD", "F-150", "2021", "ENGINE", "TX", "20230301"),       # 61 days before: lookback
                complaint("1", "FORD", "F-150", "2021", "AIR BAGS", "TX", "20230301"),
                complaint("2", "FORD", "F-150", "2021", "ENGINE", "CA", "20220101"),       # 485 days before, pre-2023
                complaint("3", "FORD", "F-150", "2021", "TIRES", "CA", "20230601"),        # after, not matching
                complaint("4", "FORD", "ESCAPE", "2020", "ENGINE", "CA", "20230301"),      # another vehicle
                complaint("5", "TOYOTA", "CAMRY", "2020", "ENGINE", "CA", "20230301")]     # another make
        index = ES.complaint_index(rows, ES.MAKES)
        self.assertEqual(index["FORD-F150-2021"]["1"][1], {"ENGINE", "AIR BAGS"})
        self.assertNotIn("TOYOTA-CAMRY-2020", index)
        evs = ES.events([recall("23V001000", "FORD", "F-150", "2021", "ENGINE:FUEL", "20230501"),
                         recall("23V001000", "FORD", "F-150", "2021", "ENGINE", "20230502"),
                         recall("24V001000", "FORD", "F-150", "2021", "ENGINE", "20240501"),  # after the window
                         recall("23V002000", "FORD", "RANGER", "2019", "STEERING", "20230501")], "recall", ES.MAKES)
        self.assertEqual([(e["vehicle"], e["date"].isoformat()) for e in evs],
                         [("FORD-F150-2021", "2023-05-01"), ("FORD-RANGER-2019", "2023-05-01")])
        got = ES.study(evs, index)
        self.assertEqual((got["events"], got["events_with_any_complaint"],
                          got["events_with_matching_complaints_in_3y_before"]), (2, 1, 1))
        self.assertEqual(got["matching_complaints_3y_before"],
                         {"in_lookback": 1, "earlier_in_3y": 1, "before_export_start": 1, "matching_3y_total": 2})
        self.assertEqual(got["events_dated_from_2023"],
                         {"events": 2, "events_with_matching_before": 1, "in_lookback": 1, "earlier_in_3y": 1,
                          "before_export_start": 1, "events_mostly_before_export": 0})
        by_q = {row["quarter"]: row for row in got["by_quarter"]}
        self.assertEqual((by_q[-1]["mean_complaints"], by_q[-1]["mean_matching"]), (0.5, 0.5))
        self.assertEqual(by_q[0]["mean_complaints"], 0.5)                    # the tires complaint, 31 days after
        self.assertEqual(by_q[0]["mean_matching"], 0.0)
        investigations = ES.events([investigation("PE23001", "FORD", "F-150", "2021", "ENGINE", "20230501"),
                                    investigation("RQ23002", "FORD", "F-150", "2021", "ENGINE", "20230501")],
                                   "investigation", ES.MAKES)
        self.assertEqual([e["vehicle"] for e in investigations], ["FORD-F150-2021"])


if __name__ == "__main__":
    unittest.main()
