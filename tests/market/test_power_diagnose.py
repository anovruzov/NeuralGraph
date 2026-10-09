"""The comparators' diagnostic for the power check (``tools/market/power_diagnose.py``), offline."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack"
VEHICLE_BACKGROUND = ROOT / "docs" / "collective" / "power" / "vehicle-background.json"

_spec = importlib.util.spec_from_file_location("power_diagnose", ROOT / "tools" / "market" / "power_diagnose.py")
PD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PD)  # type: ignore[union-attr]

# ramps of 20 weeks start inside a one-year export, so PRR finds some and misses some
WORLD = {"rates": [0.5, 2.0], "plants": 2, "sextuplings": 2, "ramp_weeks": 20}


def small_background() -> dict[str, Any]:
    """The vehicle background shrunk to six sites and 80 vehicles, as in the power check's own tests."""
    bg = json.loads(VEHICLE_BACKGROUND.read_text(encoding="utf-8"))
    bg.update({"weekly_volume": 30.0, "plant_entity_rate": [0.2, 2.0], "sextuple_key_rate": [0.02, 0.5]})
    bg["sites"]["count"] = 6
    bg["entities"]["count"] = 80
    return bg


class DiagnoseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = PD.A._load(str(PACK_DIR))
        cls.bg = PD.P.check_background(small_background(), cls.pack)
        cls.doc = PD.diagnose(cls.pack, cls.bg, years=1, seed=1, lookbacks=[26, 52], **WORLD)

    def test_it_finds_what_the_power_check_finds(self) -> None:
        # the same world through the whole audit: P's and PRR's found per plant must be the diagnostic's
        world = PD.P.run_world(str(PACK_DIR), self.bg, years=1, seed=1, sextuple_weeks=13, stagger_weeks=26,
                               lookbacks=[26, 52], tie_salt="pilot", **WORLD)
        plants = {p["outcome_id"]: p for p in world["plants"] if p["role"] == "plant"}
        sextuplings, ramps = self.doc["P"]["sextuplings"], self.doc["PRR"]["ramps"]
        self.assertEqual(len(sextuplings), 2)
        self.assertEqual(len(ramps), 4)
        for s in sextuplings:
            self.assertEqual(s["found"], plants[s["outcome_id"]]["found"]["P"], s["outcome_id"])
        for r in ramps:
            self.assertEqual(r["found"], plants[r["outcome_id"]]["found"]["PRR"], r["outcome_id"])
        self.assertEqual(sorted({r["found"]["26"] for r in ramps}), [False, True])
        self.assertEqual(self.doc["P"]["alerts_kept"], world["alerts"]["P"])
        self.assertEqual(self.doc["PRR"]["alerts_kept"], world["alerts"]["PRR"])

    def test_counts_add_up(self) -> None:
        p = self.doc["P"]
        self.assertEqual(sum(p["snapshot_flags"].values()), p["candidate_keys"])
        self.assertEqual(self.doc["world"]["first_evaluated_index"], 19)
        for r in self.doc["PRR"]["ramps"]:
            self.assertEqual(r["found"]["26"], r["first_lead_days"]["26"] is not None)
            if r["first_lead_days"]["26"] is not None:
                self.assertLessEqual(r["first_lead_days"]["26"], 7 * 26)
                self.assertTrue(r["found"]["52"])

    def test_the_cooldown_replay(self) -> None:
        weeks = [f"2024-W{w:02d}" for w in range(1, 21)]
        # alerted at W02; a candidate at W03 and W05 (cooling), absent W06 to W09, released, a candidate at W12
        candidate = {"candidate_weeks": ["2024-W02", "2024-W03", "2024-W05", "2024-W12"], "alert_weeks": ["2024-W02"]}
        self.assertEqual(PD.cooling_candidate_weeks(candidate, weeks, 4), ["2024-W03", "2024-W05"])
        self.assertEqual(PD.cooling_candidate_weeks(candidate, weeks, 0), [])

    def test_the_command_writes_json_and_a_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bg_path, out = Path(tmp) / "bg.json", Path(tmp) / "d" / "diagnosis.json"
            bg_path.write_text(json.dumps(small_background()), encoding="utf-8")
            text = io.StringIO()
            with redirect_stdout(text):
                code = PD.main(["--pack", str(PACK_DIR), "--background", str(bg_path), "--years", "1", "--seed", "1",
                                "--lookback-weeks", "26,52", "--rates", "0.5,2", "--plants", "2", "--sextuplings", "2",
                                "--ramp-weeks", "20", "--out", str(out)])
            self.assertEqual(code, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((doc["kind"], doc["label"]), (PD.KIND, PD.LABEL))
        self.assertEqual(doc["P"], json.loads(json.dumps(self.doc["P"])))
        self.assertEqual((doc["settings"]["sextuple_weeks"], doc["settings"]["stagger_weeks"]), (13, 26))  # defaults
        self.assertIn("P sextuplings, look-back 26: found", text.getvalue())
        self.assertIn("PRR ramps at 2 a week, look-back 52: found", text.getvalue())


if __name__ == "__main__":
    unittest.main()
