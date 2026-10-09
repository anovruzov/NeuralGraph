"""The power check (``mycelic.collective.pilot.power``): the background, the plants, scoring, the gate and the CLI."""
from __future__ import annotations

import contextlib
import io
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Any

from mycelic.collective.edge.weeks import week_monday
from mycelic.collective.pilot import audit as A
from mycelic.collective.pilot import power as P

ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack"
VEHICLE_BACKGROUND = ROOT / "docs" / "collective" / "power" / "vehicle-background.json"
PACK = A._load(str(PACK_DIR))


def small_background(**changes: Any) -> dict[str, Any]:
    """The vehicle background shrunk to a few sites and vehicles, so a world audits in seconds."""
    bg = json.loads(VEHICLE_BACKGROUND.read_text(encoding="utf-8"))
    bg.update({"weekly_volume": 30.0, "plant_entity_rate": [0.2, 2.0], "sextuple_key_rate": [0.02, 0.5]})
    bg["sites"]["count"] = 6
    bg["entities"]["count"] = 80
    bg.update(changes)
    return bg


def cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = P.main(argv)
    return code, out.getvalue(), err.getvalue()


WORLD = {"years": 1, "seed": 1, "rates": [0.5, 2.0], "plants": 2, "sextuplings": 2, "ramp_weeks": 104,
         "sextuple_weeks": 13, "stagger_weeks": 26}


class BackgroundTests(unittest.TestCase):
    def test_the_vehicle_background_is_accepted(self) -> None:
        bg = P.check_background(json.loads(VEHICLE_BACKGROUND.read_text(encoding="utf-8")), PACK)
        self.assertEqual((bg["sites"]["count"], bg["entities"]["type"]), (60, "vehicle"))

    def test_every_problem_is_named(self) -> None:
        cases = [({"kind": "other"}, "kind must be"), ({"start": "2021-01-05"}, "Monday"),
                 ({"weekly_volume": 0}, "weekly_volume"), ({"predicates": {"exploded": 1}}, "pack predicates"),
                 ({"plant_entity_rate": [1.0, 0.5]}, "plant_entity_rate"),
                 ({"entities": {"type": "vehicle", "count": 9, "gamma_shape": 0.5, "id_format": "syn {n}"}},
                  "not a canonical vehicle"),
                 ({"entities": {"type": "lot", "count": 9, "gamma_shape": 0.5, "id_format": "X{n}"}}, "entity type")]
        for change, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(P.PowerError, message):
                    P.check_background(small_background(**change), PACK)
        bg = small_background()
        del bg["sources"]
        with self.assertRaisesRegex(P.PowerError, "exactly the keys"):
            P.check_background(bg, PACK)

    def test_poisson_draws(self) -> None:
        rng = random.Random("poisson")
        for lam in (0.3, 2.5, 75.0):
            draws = [P.poisson(rng, lam) for _ in range(4000)]
            mean = sum(draws) / len(draws)
            self.assertLess(abs(mean - lam), 4 * (lam / len(draws)) ** 0.5, lam)
        self.assertEqual(P.poisson(rng, 0.0), 0)


class WorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bg = P.check_background(small_background(), PACK)
        cls.rows, cls.planted, cls.weeks = P.build_world(PACK, cls.bg, **WORLD)

    def test_a_world_is_a_function_of_its_seed(self) -> None:
        again = P.build_world(PACK, self.bg, **WORLD)
        self.assertEqual((again[0], again[1]), (self.rows, self.planted))
        other = P.build_world(PACK, self.bg, **{**WORLD, "seed": 2})
        self.assertNotEqual(other[0], self.rows)

    def test_rows_map_through_the_pack(self) -> None:
        from mycelic.collective.packs.connector import map_rows
        mapped = map_rows(self.rows, PACK, synthetic=True)
        self.assertEqual((mapped.rejected, len(mapped.records)), ({}, len(self.rows)))
        self.assertEqual(len({r["site"] for r in mapped.records}), 6)
        self.assertEqual(len(self.weeks), 52)

    def test_plants_and_controls(self) -> None:
        plants = [p for p in self.planted if p.role == P.PLANT]
        controls = [p for p in self.planted if p.role == P.CONTROL]
        self.assertEqual(len(plants), len(controls))
        self.assertEqual(Counter((p.kind, p.rate) for p in plants),
                         Counter({(P.RAMP, 0.5): 2, (P.RAMP, 2.0): 2, (P.SEXTUPLE, None): 2}))
        self.assertEqual(len({p.entity_id for p in self.planted}), len(self.planted))     # every key on its own entity
        for plant, control in zip(plants, controls):
            self.assertEqual((control.outcome_id, control.open_week, control.records),
                             (f"control-{plant.outcome_id}", plant.open_week, 0))
            self.assertGreaterEqual(plant.open_week, 52 - WORLD["stagger_weeks"])
            self.assertLess(plant.open_week, 52)
            if plant.kind == P.RAMP:
                self.assertEqual(control.predicate, plant.predicate)
            lo, hi = self.bg["plant_entity_rate"] if plant.kind == P.RAMP else self.bg["sextuple_key_rate"]
            if plant.kind == P.SEXTUPLE:
                self.assertTrue(lo <= plant.background_rate <= hi and lo <= control.background_rate <= hi)

    def test_planted_records_lie_on_the_key_before_the_outcome(self) -> None:
        mapping = PACK.mapping()
        value_map = mapping["codes"][0]["value_map"]
        pred_of = {term: PACK.codes[code].predicate for term, code in value_map.items()}
        for p in (p for p in self.planted if p.role == P.PLANT):
            opened = week_monday(self.weeks[p.open_week]).strftime("%Y%m%d")
            before = sum(1 for r in self.rows if r["vehicle"] == p.entity_id
                         and pred_of[r["components"][0]] == p.predicate and r["received"] < opened)
            self.assertGreaterEqual(before, p.records)
        self.assertGreater(sum(p.records for p in self.planted if p.kind == P.RAMP and p.rate == 2.0),
                           sum(p.records for p in self.planted if p.kind == P.RAMP and p.rate == 0.5))

    def test_found_in_lookback_is_the_audits_found(self) -> None:
        outcomes = [A.Outcome(p.outcome_id, week_monday(self.weeks[p.open_week]).isoformat(), "vehicle", p.entity_id,
                              p.predicate) for p in self.planted]
        doc = A.audit(PACK, self.rows, outcomes, lookback=26, synthetic=True)
        compared = 0
        for name in A.channel_names(doc):
            timeline = doc["channels"][name]["alert_timeline"]
            for r, o in zip(doc["channels"][name]["by_outcome"], outcomes):
                self.assertEqual(r["outcome_id"], o.outcome_id)
                self.assertEqual(P.found_in_lookback(timeline, o.key, o.opened, 26), r["found"], (name, r))
                compared += 1
        self.assertEqual(compared, len(outcomes) * 5)

    def test_too_few_entities_in_the_band_is_an_error(self) -> None:
        bg = small_background(plant_entity_rate=[5.0, 6.0])
        with self.assertRaisesRegex(P.PowerError, "plant_entity_rate"):
            P.build_world(PACK, bg, **WORLD)


def world(years: int, rows: list[tuple[str, str, float | None, dict[str, bool | None]]]) -> dict[str, Any]:
    """A scored world as run_world returns it: (role, kind, rate, found at look-back 26 per channel)."""
    plants = [{"role": role, "kind": kind, "rate": rate, "records": 5,
               "found": {n: (None if f is None else {"26": f}) for n, f in found.items()}}
              for role, kind, rate, found in rows]
    return {"years": years, "channels": ["X", "P"], "plants": plants}


class SummaryTests(unittest.TestCase):
    def test_power_control_and_the_gate(self) -> None:
        rows = []
        for i in range(10):
            rows.append(("plant", P.RAMP, 0.5, {"X": i < 3, "P": i < 9}))
            rows.append(("control", P.RAMP, 0.5, {"X": False, "P": i < 1}))
        s = P.summarise([world(2, rows)], lookbacks=[26], threshold=0.8, max_control_share=0.2)
        cell = s["cells"][0]["by_lookback"]["26"]
        self.assertEqual((cell["channels"]["X"]["power"]["found"], cell["channels"]["P"]["power"]["found"]), (3, 9))
        self.assertEqual(cell["channels"]["P"]["control"]["found"], 1)
        self.assertEqual((cell["best"], cell["best_power"], cell["passes"]), ("P", 0.9, True))
        self.assertTrue(s["gate"]["passes"])
        # a channel that also finds most controls is not counted
        noisy = [(r, k, rate, {**f, "P": True}) for r, k, rate, f in rows]
        s = P.summarise([world(2, noisy)], lookbacks=[26], threshold=0.8, max_control_share=0.2)
        self.assertEqual((s["cells"][0]["by_lookback"]["26"]["best"], s["gate"]["passes"]), ("X", False))
        self.assertEqual(s["gate"]["failing"], [{"rate": 0.5, "years": 2, "best": "X", "best_power": 0.3}])

    def test_sextuplings_are_reported_not_gated(self) -> None:
        rows = [("plant", P.RAMP, 2.0, {"X": True, "P": True}), ("control", P.RAMP, 2.0, {"X": False, "P": False}),
                ("plant", P.SEXTUPLE, None, {"X": False, "P": False}),
                ("control", P.SEXTUPLE, None, {"X": False, "P": None})]
        s = P.summarise([world(4, rows)], lookbacks=[26], threshold=0.8, max_control_share=0.2)
        self.assertEqual([(c["kind"], c["rate"]) for c in s["cells"]], [(P.RAMP, 2.0), (P.SEXTUPLE, None)])
        self.assertFalse(s["cells"][1]["by_lookback"]["26"]["passes"])
        self.assertTrue(s["gate"]["passes"])


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.bg_path = cls.tmp / "bg.json"
        cls.bg_path.write_text(json.dumps(small_background()), encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def run_check(self, out: str, *extra: str) -> tuple[int, str, str, dict[str, Any]]:
        code, stdout, err = cli(["run", "--pack", str(PACK_DIR), "--background", str(self.bg_path), "--out",
                                 str(self.tmp / out), "--years", "1", "--seeds", "1", "--plants", "2",
                                 "--sextuplings", "1", *extra])
        doc = json.loads((self.tmp / out / "power.json").read_text(encoding="utf-8")) if code in (0, 1) else {}
        return code, stdout, err, doc

    def test_a_weak_ramp_fails_the_gate_and_exits_1(self) -> None:
        code, out, err, doc = self.run_check("weak", "--rates", "0.01", "--lookback-weeks", "26,104")
        self.assertEqual(code, 1, err)
        self.assertIn("power check: fails at look-back 26 weeks", out)
        self.assertEqual((doc["kind"], doc["label"], doc["settings"]["lookback_weeks"]), (P.KIND, P.LABEL, [26, 104]))
        self.assertEqual(doc["summary"]["channels"], list(A.CHANNELS + A.COMPARATORS))
        self.assertFalse(doc["summary"]["gate"]["passes"])
        self.assertEqual(set(doc["summary"]["cells"][0]["by_lookback"]), {"26", "104"})
        md = (self.tmp / "weak" / "power.md").read_text(encoding="utf-8")
        self.assertIn(P.LABEL, md)
        self.assertIn("## Sextuplings, look-back 104 weeks", md)

    def test_a_strong_ramp_passes_and_exits_0(self) -> None:
        code, out, err, doc = self.run_check("strong", "--rates", "20")
        self.assertEqual(code, 0, err)
        self.assertIn("power check: passes", out)
        self.assertTrue(doc["summary"]["gate"]["passes"])

    def test_the_same_inputs_give_the_same_file_with_workers(self) -> None:
        one = self.run_check("one", "--rates", "2", "--seeds", "1,2")[3]
        two = self.run_check("two", "--rates", "2", "--seeds", "1,2", "--workers", "2")[3]
        self.assertEqual(one, two)

    def test_usage_errors_exit_2_and_dry_run_touches_nothing(self) -> None:
        for extra, message in ((["--rates", "0.1,x"], "--rates"), (["--rates", "0.1,0.1"], "distinct"),
                               (["--plants", "0"], "--plants"), (["--threshold", "1.5"], "--threshold")):
            with self.subTest(message=message):
                code, _, err, _ = self.run_check("bad", *extra)
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        code, out, _ = cli(["run", "--pack", str(self.tmp / "nope"), "--background", str(self.tmp / "nope.json"),
                            "--out", str(self.tmp / "dry"), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("would need: file", out)
        self.assertIn("would write:", out)
        self.assertFalse((self.tmp / "dry").exists())


if __name__ == "__main__":
    unittest.main()
