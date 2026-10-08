"""B1b: the constructed codes-miss illustration (``demo/collective/scenario_codes_miss.json``, ``codes_miss.py``).

Pre-registered in ``docs/collective/b1/PREREG.md``. Its section 6 counts as an attempt any computation of alerts on a
distinct codes-miss digest, so nothing here runs the detectors on the committed scenario: the parse and build tests
build worlds only, the rule tests run :func:`codes_miss.outcome` and :func:`codes_miss.evaluate` on synthetic
detection results, and the attempt tests read the committed attempt and recorded run files.
"""
from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Any

from demo.collective import codes_miss as cm
from demo.collective import collective_demo as demo
from demo.collective import scenario as scn
from demo.collective import screen as scr
from mycelic.collective import runfiles, schemacheck
from mycelic.collective.jsonio import sha256_hex

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "demo" / "collective"
SCENARIO = DEMO_DIR / "scenario_codes_miss.json"
PREREG = ROOT / "docs" / "collective" / "b1" / "PREREG.md"
ATTEMPTS = ROOT / "docs" / "collective" / "b1" / "attempts"
HALVERN = DEMO_DIR / "recorded" / "collective-halvern-b1a"
HERO = "hero-overheat"
# the hero lot is one of the PREREG's adjustable fields (section 6); its product follows by the generator's links (R4)
_HERO_RAW = next(i for i in json.loads(SCENARIO.read_text(encoding="utf-8"))["items"] if i["id"] == HERO)
HERO_LOT = _HERO_RAW["key"]["entity_id"]
HERO_PRODUCT = next(s["ids"][0] for s in _HERO_RAW["structured"] if s["entity_type"] == "product")
HERO_KEY = f"lot:{HERO_LOT}:overheat"
CASE = (HERO_KEY, f"lot:{HERO_LOT}:malfunction_unspecified", f"product:{HERO_PRODUCT}:malfunction_unspecified")
_SCHEMA = schemacheck.compile(cm.SCHEMA)


def raw() -> dict[str, Any]:
    return json.loads(SCENARIO.read_text(encoding="utf-8"))


def parse(doc: dict[str, Any]) -> scn.Scenario:
    return scn.parse_scenario(doc, digest="0" * 32)


def item(doc: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(i for i in doc["items"] if i["id"] == item_id)


# --------------------------------------------------------------------------------------------------- parse and build

class ParseTests(unittest.TestCase):
    """Parsing and building only: no alerts are computed (PREREG section 6)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sc = scn.load_scenario(SCENARIO)

    def test_the_committed_scenario_is_the_pre_registered_cast(self) -> None:
        sc = self.sc
        self.assertEqual((sc.pack.id, sc.seed, sc.weeks, sc.tie_salt, sc.company),
                         ("device_quality", 29, 40, "collective-demo-codes-miss", "Tarnwick Devices (fictional)"))
        self.assertEqual(sc.org.enterprise, "tarnwick")
        cmb = sc.codes_miss
        self.assertEqual((cmb.statement, cmb.author_note), (scn.STATEMENT, scn.AUTHOR_NOTE))
        self.assertEqual(cmb.robustness_seeds, (31, 37, 41, 43, 47, 53, 59, 61))
        (shift,) = cmb.shifts
        self.assertEqual((shift.id, shift.start_week, shift.weeks, shift.to_code), ("intake-form", 22, 18, "ILL-9001"))
        self.assertEqual(shift.sites, ("plant-ashvale", "plant-brindlemoor", "plant-corrowfield", "werk-dornhagen",
                                       "werk-erlenbruch"))
        self.assertEqual(shift.label, "A new complaint-intake form sets the generic problem code on every complaint")
        hero = sc.hero
        self.assertEqual((hero.id, hero.keys, hero.visibility, hero.codes, hero.start_week, hero.weeks),
                         (HERO, (HERO_KEY,), "narrative_only", ("ILL-9001",), 30, 6))
        self.assertEqual(hero.sites, (("plant-brindlemoor", "en", 1), ("plant-corrowfield", "en", 1),
                                      ("werk-dornhagen", "de", 1)))
        self.assertEqual(hero.structured, (("lot", (HERO_LOT,), 0.6), ("product", (HERO_PRODUCT,), 0.95),
                                           ("component", ("PUMP-HOUSING",), 0.3), ("supplier", ("V1001",), 0.2)))
        self.assertEqual([i.id for i in sc.items], [HERO, "sibling-quarantine", "decoy-echo-flood",
                                                    "decoy-single-reporter", "decoy-generic-rise"])
        self.assertEqual(sc.pack.detectors["window_weeks"], 8)

    def test_only_adjustable_fields_differ_from_the_preregistered_cast(self) -> None:
        """PREREG section 6: between attempts only the hero lot, sites and languages, rate, start week and templates
        may change. The committed scenario is attempt 1's (the cast as PREREG section 3 fixes it) with the hero lot
        replaced, its product following the generator's link and the sibling holding the same lot."""
        first = json.loads((ATTEMPTS / "attempt-1" / "scenario_codes_miss.json").read_text(encoding="utf-8"))
        hero1 = next(i for i in first["items"] if i["id"] == HERO)
        lot1 = hero1["key"]["entity_id"]
        product1 = next(s["ids"][0] for s in hero1["structured"] if s["entity_type"] == "product")
        self.assertEqual((lot1, product1), ("L10002", "SD-9"))
        links = self.sc.pack.generator["links"]["lot"]
        self.assertIn(HERO_LOT, links["map"][HERO_PRODUCT])
        current = json.loads(SCENARIO.read_text(encoding="utf-8"))
        for item in current["items"]:
            if item["id"] in (HERO, "sibling-quarantine"):
                if item["key"]["entity_id"] == HERO_LOT:
                    item["key"]["entity_id"] = lot1
                item["slots"] = {k: (lot1 if v == HERO_LOT else v) for k, v in item["slots"].items()}
                for entry in item["structured"]:
                    entry["ids"] = [{HERO_LOT: lot1, HERO_PRODUCT: product1}.get(x, x) for x in entry["ids"]]
        self.assertEqual(current, first)

    def test_the_statement_and_the_author_note_are_the_preregistered_sentences(self) -> None:
        text = PREREG.read_text(encoding="utf-8")
        self.assertIn(f"STATEMENT: `{scn.STATEMENT}`", text)
        self.assertIn(f"AUTHOR_NOTE: `{scn.AUTHOR_NOTE}`", text)
        self.assertIn("> " + cm.RULE, text)

    def test_the_shift_rewrites_codes_at_shifted_sites_in_covered_weeks_and_copies_follow_their_origin(self) -> None:
        sc = self.sc
        world = scn.build_world(sc)
        from datetime import date
        start = date.fromisoformat(sc.pack.generator["start"])
        (shift,) = sc.codes_miss.shifts
        copies = [r for r in world.records if r["origin_ref"] is not None]
        by_ref = {r["record_ref"]: r for r in world.records}
        covered = outside = 0
        for r in world.records:
            if r["origin_ref"] is not None:
                continue
            week = (date.fromisoformat(r["received_date"]) - start).days // 7
            if shift.covers(r["site"], week):
                covered += 1
                self.assertEqual(r["codes"], ["ILL-9001"], r["record_ref"])
            else:
                outside += 1
        self.assertGreater(covered, 300)
        self.assertGreater(outside, 300)
        unshifted = [r for r in world.records if r["origin_ref"] is None and r["site"] == "plant-fennick"]
        self.assertTrue(any(r["codes"] != ["ILL-9001"] for r in unshifted))
        self.assertTrue(copies)
        for c in copies:
            if c["origin_ref"] in by_ref:
                self.assertEqual(c["codes"], by_ref[c["origin_ref"]]["codes"], c["record_ref"])
        hero = [by_ref[ref] for ref in world.item_records[HERO]]
        self.assertEqual(len(hero), 18)
        self.assertTrue(all(r["codes"] == ["ILL-9001"] for r in hero))
        self.assertEqual(world.case_keys, tuple(sorted(
            f"{t}:{i}:{p}" for t, i in (("component", "PUMP-HOUSING"), ("lot", HERO_LOT), ("product", HERO_PRODUCT),
                                        ("supplier", "V1001"))
            for p in ("malfunction_unspecified", "overheat"))))

    def test_the_world_without_the_hero_has_no_hero_records_and_no_case_keys(self) -> None:
        world = scn.build_world(self.sc, without=HERO)
        self.assertEqual(world.item_records[HERO], ())
        self.assertEqual(world.case_keys, ())
        self.assertEqual(len(world.records), len(scn.build_world(self.sc).records) - 18)

    def test_building_is_deterministic(self) -> None:
        a, b = scn.build_world(self.sc), scn.build_world(self.sc)
        self.assertEqual(a.records, b.records)
        self.assertEqual(a.case_keys, b.case_keys)

    def test_the_first_scenario_has_no_codes_miss_block_and_builds_as_before(self) -> None:
        sc = scn.load_scenario()
        self.assertIsNone(sc.codes_miss)
        self.assertNotIn("codes_miss", sc.raw)


class RealismTests(unittest.TestCase):
    """Committed in-test copies that each break one constraint (not attempts: PREREG section 6)."""

    def assert_refused(self, change, message: str, *, build: bool = False) -> None:
        doc = raw()
        change(doc)
        with self.assertRaises(scn.ScenarioError) as ctx:
            sc = parse(doc)
            if build:
                scn.build_world(sc)
        self.assertIn(message, str(ctx.exception))

    def test_the_committed_scenario_passes(self) -> None:
        sc = parse(raw())
        scn.build_world(sc)

    def test_block_keys_and_verbatim_texts(self) -> None:
        self.assert_refused(lambda d: d["codes_miss"].update(extra=1), "unknown key")
        self.assert_refused(lambda d: d["codes_miss"].pop("shifts"), "missing")
        self.assert_refused(lambda d: d["codes_miss"].update(case_kind="other"), "must be codes_miss")
        self.assert_refused(lambda d: d["codes_miss"].update(statement=scn.STATEMENT[:-1]), "statement, verbatim")
        self.assert_refused(lambda d: d["codes_miss"].update(author_note=scn.AUTHOR_NOTE + " "), "note, verbatim")
        self.assert_refused(lambda d: d.update(extra=1), "unknown key")

    def test_r1_shifts(self) -> None:
        self.assert_refused(lambda d: d["codes_miss"].update(shifts=[]), "R1: at least one shift")
        self.assert_refused(lambda d: d["codes_miss"]["shifts"][0].update(weeks=17), "R1: a shift runs to the")

        def uncover(d: dict[str, Any]) -> None:
            d["codes_miss"]["shifts"][0]["sites"].remove("werk-dornhagen")
        self.assert_refused(uncover, "R1: a shift covers every hero site-week")
        self.assert_refused(lambda d: d["codes_miss"]["shifts"][0].update(to_code="NOPE"), "not a pack code")
        self.assert_refused(lambda d: d["codes_miss"]["shifts"].append(dict(d["codes_miss"]["shifts"][0])),
                            "duplicate shift id")

    def test_r2_narrative_only_hero_with_the_shift_code(self) -> None:
        self.assert_refused(lambda d: item(d, HERO).update(codes=["ILL-9002"]), "R2")

        def two_codes(d: dict[str, Any]) -> None:
            item(d, HERO)["codes"] = ["ILL-9001", "ILL-9002"]
        self.assert_refused(two_codes, "R2")

    def test_r3_the_hero_entity_is_in_the_generators_master_data_at_every_hero_site(self) -> None:
        def move(d: dict[str, Any]) -> None:
            item(d, HERO)["sites"][2]["site"] = "werk-erlenbruch"
        self.assert_refused(move, "R3", build=True)

        def move_and_add(d: dict[str, Any]) -> None:
            move(d)
            d["master_data_additions"].append({"site": "werk-erlenbruch", "entity_type": "lot", "ids": [HERO_LOT]})
        self.assert_refused(move_and_add, "R3", build=True)

    def test_r4_structured_entries(self) -> None:
        def drop_supplier(d: dict[str, Any]) -> None:
            item(d, HERO)["structured"] = [s for s in item(d, HERO)["structured"] if s["entity_type"] != "supplier"]
        self.assert_refused(drop_supplier, "R4")

        def entry(t: str, **fields: Any):
            def change(d: dict[str, Any]) -> None:
                next(s for s in item(d, HERO)["structured"] if s["entity_type"] == t).update(fields)
            return change
        self.assert_refused(entry("lot", fill_rate=0.5), "R4")
        self.assert_refused(entry("product", ids=[HERO_PRODUCT, "CM-5"]), "R4")
        self.assert_refused(entry("supplier", ids=["V9999"]), "R4")
        self.assert_refused(entry("product", ids=["CM-5"]), "R4: the ids follow the generator's links")

    def test_r5_rate_and_templates(self) -> None:
        self.assert_refused(lambda d: item(d, HERO)["sites"][0].update(rate_per_week=3), "R5")
        self.assert_refused(lambda d: item(d, HERO)["narratives"]["de"].pop(), "R5")

    def test_r6_robustness_seeds(self) -> None:
        for seeds in ([31, 37, 41, 43, 47, 53, 59], [31, 37, 41, 43, 47, 53, 59, 61, 67], [29, 37, 41, 43, 47, 53, 59, 61],
                      [31, 31, 41, 43, 47, 53, 59, 61], [31, 37, 41, 43, 47, 53, 59, True], "31"):
            with self.subTest(seeds=seeds):
                self.assert_refused(lambda d, s=seeds: d["codes_miss"].update(robustness_seeds=s), "R6")

    def test_r7_no_decoy_key_is_a_case_key(self) -> None:
        def clash(d: dict[str, Any]) -> None:
            item(d, "decoy-generic-rise")["structured"][0]["ids"] = ["HV-81", "HV-82", HERO_PRODUCT]
        self.assert_refused(clash, "R7", build=True)

    def test_r8_the_shift_starts_a_window_before_the_hero(self) -> None:
        def later(d: dict[str, Any]) -> None:
            d["codes_miss"]["shifts"][0].update(start_week=23, weeks=17)
        self.assert_refused(later, "R8")


# --------------------------------------------------------------------------------------------------- the rule

def W(i: int) -> str:
    return f"2030-W{i + 1:02d}"


WORLD_WEEKS = tuple(W(i) for i in range(40))
DET_WEEKS = tuple(W(i) for i in range(42))
LAST = W(41)


def channel(alerts=(), candidates=()) -> dict[str, Any]:
    return {"alerts": [{"week": W(w), "rank": rank, "key": k, "score": 1.0} for k, w, rank in alerts],
            "candidates": [{"key": k, "candidate_weeks": [W(w) for w in cw], "alert_weeks": [W(w) for w in aw]}
                           for k, cw, aw in candidates],
            "weeks": [{"week": w} for w in DET_WEEKS]}


def detection(x=(), s=(), r=(), *, s_candidates=(), r_candidates=(), case=CASE) -> dict[str, Any]:
    return {"X": channel(x), "S": channel(s, s_candidates), "R_mf": channel(r, r_candidates), "weeks": DET_WEEKS,
            "last_week": LAST, "cooldown_weeks": 4, "case_keys": tuple(case), "weeks_world": WORLD_WEEKS}


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sc = scn.load_scenario(SCENARIO)

    def out(self, with_hero, without_hero, *, strict: bool = True) -> dict[str, Any]:
        return cm.outcome(with_hero, without_hero, self.sc, strict=strict)

    def test_x_alone_holds(self) -> None:
        o = self.out(detection(x=[(HERO_KEY, 32, 1)]), detection(case=()))
        self.assertEqual((o["built"], o["x_caught"], o["x_chance_find"], o["x_week"], o["holds"]),
                         (True, True, False, W(32), True))
        for c in cm.RULE_CHANNELS:
            self.assertEqual(o["channels"][c], {"alerts": [], "attributed": [], "attributed_no_later": False,
                                                "strict_no_later": False, "cooling_at_x_week": []})

    def test_an_attributed_baseline_alert_no_later_than_x_breaks_the_rule(self) -> None:
        key = CASE[2]
        for week in (31, 32):
            with self.subTest(week=week):
                o = self.out(detection(x=[(HERO_KEY, 32, 1)], r=[(key, week, 2)]), detection(case=()))
                self.assertEqual(o["channels"]["R_mf"]["attributed"], [{"key": key, "rank": 2, "week": W(week)}])
                self.assertTrue(o["channels"]["R_mf"]["attributed_no_later"])
                self.assertFalse(o["holds"])
        o = self.out(detection(x=[(HERO_KEY, 32, 1)], s=[(key, 33, 1)]), detection(case=()))
        self.assertEqual(len(o["channels"]["S"]["attributed"]), 1)
        self.assertFalse(o["channels"]["S"]["attributed_no_later"])
        self.assertTrue(o["holds"])

    def test_an_alert_the_world_without_the_hero_also_raises_is_not_attributed(self) -> None:
        key = CASE[2]
        o = self.out(detection(x=[(HERO_KEY, 32, 1)], r=[(key, 31, 1)]), detection(r=[(key, 30, 1)], case=()))
        self.assertEqual(o["channels"]["R_mf"]["attributed"], [])
        self.assertFalse(o["channels"]["R_mf"]["attributed_no_later"])
        self.assertTrue(o["holds"])
        self.assertTrue(o["channels"]["R_mf"]["strict_no_later"])
        # the counterfactual alert must fall from the hero's first week to the alert's week
        for week in (29, 32):
            with self.subTest(without_week=week):
                o = self.out(detection(x=[(HERO_KEY, 32, 1)], r=[(key, 31, 1)]),
                             detection(r=[(key, week, 1)], case=()))
                self.assertTrue(o["channels"]["R_mf"]["attributed_no_later"])

    def test_alerts_before_the_hero_count_only_for_the_strict_reading_from_the_shift_on(self) -> None:
        key = CASE[1]
        for week, strict in ((21, False), (22, True), (29, True)):
            with self.subTest(week=week):
                o = self.out(detection(x=[(HERO_KEY, 32, 1)], s=[(key, week, 1)]), detection(case=()))
                self.assertEqual(o["channels"]["S"]["alerts"], [])
                self.assertTrue(o["holds"])
                self.assertEqual(o["channels"]["S"]["strict_no_later"], strict)

    def test_a_key_outside_the_case_never_counts(self) -> None:
        o = self.out(detection(x=[(HERO_KEY, 32, 1)], r=[("product:HV-81:malfunction_unspecified", 31, 1)]),
                     detection(case=()))
        self.assertEqual(o["channels"]["R_mf"]["alerts"], [])
        self.assertTrue(o["holds"])

    def test_a_chance_find_is_not_caught(self) -> None:
        o = self.out(detection(x=[(HERO_KEY, 32, 1)]), detection(x=[(HERO_KEY, 31, 3)], case=()))
        self.assertEqual((o["x_caught"], o["x_chance_find"], o["holds"]), (False, True, False))
        o = self.out(detection(x=[(HERO_KEY, 32, 1)]), detection(x=[(HERO_KEY, 33, 3)], case=()))
        self.assertEqual((o["x_caught"], o["x_chance_find"], o["holds"]), (True, False, True))
        o = self.out(detection(x=[(HERO_KEY, 32, 1)]), detection(x=[(HERO_KEY, 28, 3)], case=()))
        self.assertTrue(o["x_caught"])

    def test_x_never_alerting_uses_the_last_week_and_does_not_hold(self) -> None:
        o = self.out(detection(x=[(HERO_KEY, 28, 1)], r=[(CASE[2], 40, 1)]), detection(case=()))
        self.assertEqual((o["x_caught"], o["x_chance_find"], o["x_week"], o["holds"]), (False, False, None, False))
        self.assertTrue(o["channels"]["R_mf"]["attributed_no_later"])

    def test_cooling_candidates(self) -> None:
        key = CASE[2]
        held = detection(x=[(HERO_KEY, 32, 1)], r=[(key, 20, 1)], r_candidates=[(key, range(20, 33), [20])])
        o = self.out(held, detection(case=()))
        self.assertEqual(o["channels"]["R_mf"]["cooling_at_x_week"], [key])
        self.assertTrue(o["channels"]["R_mf"]["strict_no_later"])
        self.assertTrue(o["holds"])
        # absent for cooldown_weeks steps after the alert: released, so no longer cooling at X's week
        released = detection(x=[(HERO_KEY, 32, 1)], r=[(key, 20, 1)], r_candidates=[(key, [20, 25, 32], [20])])
        self.assertEqual(self.out(released, detection(case=()))["channels"]["R_mf"]["cooling_at_x_week"], [])
        r = released["R_mf"]
        self.assertFalse(cm.cooling_at(r, key, W(23), 4))     # absent that week
        self.assertFalse(cm.cooling_at(r, key, W(25), 4))     # four quiet steps (21 to 24) released it
        self.assertFalse(cm.cooling_at(r, key, W(20), 4))     # the alert week itself is not cooling
        held = channel([(key, 20, 1)], [(key, [20, 24, 32], [20])])
        self.assertTrue(cm.cooling_at(held, key, W(24), 4))   # three quiet steps (21 to 23), then present again
        self.assertFalse(cm.cooling_at(held, key, W(32), 4))  # quiet from 25 to 28: released
        self.assertFalse(cm.cooling_at(held, key, W(24), 0))  # no cooldown configured
        self.assertFalse(cm.cooling_at(held, "lot:L1:none", W(24), 4))

    def test_the_lenient_reading_leaves_the_strict_fields_null(self) -> None:
        o = self.out(detection(x=[(HERO_KEY, 32, 1)]), detection(case=()), strict=False)
        self.assertIsNone(o["channels"]["S"]["strict_no_later"])
        self.assertIsNone(o["channels"]["S"]["cooling_at_x_week"])

    def test_a_world_that_does_not_build_does_not_hold(self) -> None:
        for a, b in ((None, detection(case=())), (detection(x=[(HERO_KEY, 32, 1)]), None)):
            o = self.out(a, b)
            self.assertEqual(o, {"built": False, "x_caught": False, "x_chance_find": False, "x_week": None,
                                 "channels": None, "holds": False})


def fake_detect(holding_seeds: set[int], *, broken: set[int] = frozenset(), main_seed: int = 29):
    """A stand-in for ``detect_only``: X alerts the hero key in the main seed and in ``holding_seeds``; a seed in
    ``broken`` does not build; every world without the hero is quiet. Records each call."""
    calls: list[tuple[int, str | None, int, int]] = []

    def detect(sc: scn.Scenario, without: str | None) -> dict[str, Any] | None:
        calls.append((sc.seed, without, sc.codes_miss.shifts[0].start_week, sc.hero.sites[0][2]))
        if sc.seed in broken:
            return None
        if without is not None:
            return detection(case=())
        return detection(x=[(HERO_KEY, 32, 1)] if sc.seed == main_seed or sc.seed in holding_seeds else [])
    return detect, calls


class EvaluateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sc = scn.load_scenario(SCENARIO)

    def fake(self, holding_seeds: set[int], *, broken: set[int] = frozenset()):
        return fake_detect(holding_seeds, broken=broken, main_seed=self.sc.seed)

    def test_order_counts_and_schema(self) -> None:
        detect, calls = self.fake({31, 37, 41, 43})
        main = detection(x=[(HERO_KEY, 32, 1)])
        block = cm.evaluate(self.sc, main, "supported", detect)
        self.assertEqual(_SCHEMA.validate(block), [])
        self.assertEqual([r["seed"] for r in block["robustness"]], [31, 37, 41, 43, 47, 53, 59, 61])
        self.assertEqual((block["robust_seeds"], block["robust_holding"], block["holds_robust"]), (8, 4, True))
        self.assertEqual([(g["offset"], g["rate"]) for g in block["grid"]],
                         [(-8, 1), (-8, 2), (-4, 1), (-4, 2), (0, 1), (0, 2)])
        self.assertEqual((block["grid_cells"], block["grid_holding"]), (6, 6))
        self.assertEqual((block["holds_main"], block["holds"], block["holds_strict"]), (True, True, True))
        self.assertEqual((block["hero_key"], block["hero_first_week"], block["first_shift_week"]),
                         (HERO_KEY, W(30), W(22)))
        self.assertEqual(block["shifts"], [{"id": "intake-form", "label": self.sc.codes_miss.shifts[0].label,
                                            "first_week": W(22), "sites": 5, "to_code": "ILL-9001"}])
        self.assertEqual((block["statement"], block["author_note"], block["rule"]),
                         (scn.STATEMENT, scn.AUTHOR_NOTE, cm.RULE))
        # main's counterfactual, each seed with and without the hero, each offset's counterfactual then its rates
        expected = [(29, HERO, 22, 1)]
        for seed in (31, 37, 41, 43, 47, 53, 59, 61):
            expected += [(seed, None, 22, 1), (seed, HERO, 22, 1)]
        for offset in (-8, -4, 0):
            expected += [(29, HERO, 30 + offset, 1), (29, None, 30 + offset, 1), (29, None, 30 + offset, 2)]
        self.assertEqual(calls, expected)

    def test_holds_robust_needs_at_least_half(self) -> None:
        detect, _ = self.fake({31, 37, 41})
        block = cm.evaluate(self.sc, detection(x=[(HERO_KEY, 32, 1)]), "supported", detect)
        self.assertEqual((block["robust_holding"], block["holds_robust"], block["holds_main"], block["holds"]),
                         (3, False, True, False))
        self.assertFalse(block["holds_strict"])

    def test_a_seed_whose_world_does_not_build_does_not_hold(self) -> None:
        detect, _ = self.fake({31, 37, 41, 43}, broken={31})
        block = cm.evaluate(self.sc, detection(x=[(HERO_KEY, 32, 1)]), "supported", detect)
        self.assertFalse(block["robustness"][0]["outcome"]["built"])
        self.assertEqual(block["robust_holding"], 3)
        self.assertEqual(_SCHEMA.validate(block), [])

    def test_the_gate(self) -> None:
        detect, _ = self.fake({31, 37, 41, 43})
        base = cm.evaluate_detection(self.sc, detection(x=[(HERO_KEY, 32, 1)]), detect)
        self.assertEqual((base["gate_status"], base["holds_main"], base["holds"], base["holds_strict"]),
                         (None, None, None, None))
        self.assertEqual(_SCHEMA.validate(base), [])
        for gate, want in (("supported", True), ("hypothesis", False), ("contested", False), (None, None)):
            with self.subTest(gate=gate):
                block = cm.with_gate(base, gate)
                self.assertEqual((block["gate_status"], block["holds_main"], block["holds"]), (gate, want, want))
                self.assertEqual(_SCHEMA.validate(block), [])
        failing = cm.evaluate_detection(self.sc, detection(x=[(HERO_KEY, 32, 1)], r=[(CASE[2], 31, 1)]), detect)
        self.assertEqual(cm.with_gate(failing, None)["holds_main"], False)
        self.assertEqual(cm.with_gate(failing, "supported")["holds"], False)

    def test_strict_needs_both_channels_quiet(self) -> None:
        detect, _ = self.fake({31, 37, 41, 43})
        main = detection(x=[(HERO_KEY, 32, 1)], s=[(CASE[1], 24, 1)])
        block = cm.evaluate(self.sc, main, "supported", detect)
        self.assertEqual((block["holds"], block["holds_strict"]), (True, False))

    def test_summary_lines(self) -> None:
        detect, _ = self.fake({31, 37, 41, 43})
        base = cm.evaluate_detection(self.sc, detection(x=[(HERO_KEY, 32, 1)], r=[(CASE[2], 31, 1)]), detect)
        lines = cm.summary_lines(cm.with_gate(base, "supported"))
        self.assertEqual(lines[:3], [scn.STATEMENT, scn.AUTHOR_NOTE, cm.VERDICT_TEXTS[False]])
        self.assertIn("R_mf resolved the case no later than X.", lines)
        good = cm.evaluate_detection(self.sc, detection(x=[(HERO_KEY, 32, 1)]), detect)
        self.assertEqual(cm.summary_lines(cm.with_gate(good, None))[2], cm.VERDICT_TEXTS[None])
        self.assertEqual(cm.summary_lines(cm.with_gate(good, "supported"))[2], cm.VERDICT_TEXTS[True])

    def test_variants(self) -> None:
        sc = self.sc
        self.assertEqual(cm.grid_offsets(8), (-8, -4, 0))
        self.assertEqual(cm.grid_offsets(5), (-5, -2, 0))
        v = cm.variant(sc, seed=31)
        self.assertEqual((v.seed, v.items, v.codes_miss.shifts), (31, sc.items, sc.codes_miss.shifts))
        v = cm.variant(sc, shift_offset=0)
        self.assertEqual((v.codes_miss.shifts[0].start_week, v.codes_miss.shifts[0].weeks), (30, 10))
        v = cm.variant(sc, hero_rate=2)
        self.assertEqual({rate for _, _, rate in v.hero.sites}, {2})
        self.assertIs(next(i for i in v.items if i.id == HERO), v.hero)
        self.assertEqual(sc.hero.sites[0][2], 1)
        with self.assertRaises(ValueError):
            cm.variant(scn.load_scenario(), seed=1)


# --------------------------------------------------------------------------------------------------- the screen

def halvern_docs() -> dict[str, Any]:
    return {name: (json.loads((HALVERN / name).read_text(encoding="utf-8")) if name.endswith(".json") else None)
            for name in ("scorecard.json", "trace.json", "leakage.json")}


class ScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sc = scn.load_scenario(SCENARIO)
        detect, _ = fake_detect({31, 37, 41, 43}, main_seed=cls.sc.seed)
        cls.good = cm.evaluate_detection(cls.sc, detection(x=[(HERO_KEY, 32, 1)]), detect)
        cls.bad = cm.evaluate_detection(cls.sc, detection(x=[(HERO_KEY, 32, 1)], r=[(CASE[2], 31, 1)]), detect)

    def screen(self, block: dict[str, Any] | None) -> dict[str, Any]:
        docs = halvern_docs()
        if block is not None:
            docs["scorecard.json"]["codes_miss"] = copy.deepcopy(block)
        return scr.build_screen(docs, mode="record", phase="complete")

    def texts(self, screen: dict[str, Any], block_id: str) -> str:
        block = next(b for b in screen["blocks"] if b["id"] == block_id)
        items = {i["id"]: i["display"] for i in screen["items"]}
        return "".join(p["text"] if p["text"] is not None else items[p["item"]] for p in block["parts"])

    def test_the_statement_the_author_note_and_the_shift_open_the_screen(self) -> None:
        s = self.screen(cm.with_gate(self.good, "supported"))
        self.assertEqual(scr.screen_problems(s), [])
        self.assertEqual(self.texts(s, "codes-miss-statement"), scn.STATEMENT)
        self.assertEqual(self.texts(s, "codes-miss-author-note"), scn.AUTHOR_NOTE)
        self.assertEqual(self.texts(s, "codes-miss-shift-0"),
                         f"Background change: {self.sc.codes_miss.shifts[0].label} · at 5 plants from week {W(22)}")
        beats = {b["id"]: b["beat"] for b in s["blocks"]}
        self.assertEqual((beats["codes-miss-statement"], beats["codes-miss-verdict"]), ("problem", "check"))
        self.assertIn("problem", s["cut_60s"])
        self.assertIn("check", s["cut_60s"])
        for i in s["items"]:
            self.assertEqual(i["display"], scr.format_value(runfiles.resolve(
                {"scorecard.json": {**halvern_docs()["scorecard.json"],
                                    "codes_miss": cm.with_gate(self.good, "supported")},
                 "trace.json": halvern_docs()["trace.json"], "leakage.json": halvern_docs()["leakage.json"]},
                i["src"]), i["fmt"]))

    def test_the_verdict_reads_each_way(self) -> None:
        for block, gate, verdict in ((self.good, "supported", True), (self.good, None, None),
                                     (self.good, "hypothesis", False), (self.bad, "supported", False)):
            with self.subTest(gate=gate, verdict=verdict):
                s = self.screen(cm.with_gate(block, gate))
                self.assertEqual(self.texts(s, "codes-miss-verdict"), scr.CODES_MISS_RULE + scr.CODES_MISS_VERDICTS[
                    verdict])
        s = self.screen(cm.with_gate(self.bad, "supported"))
        r = next(b for b in s["blocks"] if b["id"] == "codes-miss-r")
        self.assertEqual(r["kind"], "warning")
        self.assertIn(f"resolved the case no later than X: yes · its first alert of a case key that the world "
                      f"without the case did not raise: {CASE[2]} in week {W(31)}", self.texts(s, "codes-miss-r"))
        self.assertEqual(next(b for b in s["blocks"] if b["id"] == "codes-miss-s")["kind"], "note")
        self.assertEqual(self.texts(s, "codes-miss-robust"),
                         "Robustness seeds where the rule holds: 4 of 8 (at least half must hold)")
        self.assertNotIn("codes-miss-strict", [b["id"] for b in self.screen(cm.with_gate(self.good, None))["blocks"]])

    def test_a_run_without_the_block_has_no_codes_miss_blocks(self) -> None:
        s = self.screen(None)
        self.assertFalse([b for b in s["blocks"] if b["id"].startswith("codes-miss")])
        committed = json.loads((HALVERN / "screen.json").read_text(encoding="utf-8"))
        self.assertEqual(s["blocks"], committed["blocks"])

    def test_validate_run_checks_the_block_on_its_own_schema(self) -> None:
        docs = runfiles.read_run_files(HALVERN, runfiles.RUN_FILES)
        self.assertEqual(demo.validate_run(docs), [])
        docs["scorecard.json"] = {**docs["scorecard.json"], "codes_miss": cm.with_gate(self.good, "supported")}
        self.assertEqual(demo.validate_run(docs), [])
        broken = copy.deepcopy(docs)
        broken["scorecard.json"]["codes_miss"]["rule"] = "another rule"
        problems = demo.validate_run(broken)
        self.assertTrue(problems)
        self.assertTrue(all(p[0] == "scorecard.json" and p[1].startswith("$.codes_miss") for p in problems))


# --------------------------------------------------------------------------------------------------- attempts

def attempt_dirs() -> list[Path]:
    if not ATTEMPTS.is_dir():
        return []
    return sorted((p for p in ATTEMPTS.iterdir() if re.fullmatch(r"attempt-[1-3]", p.name)),
                  key=lambda p: int(p.name.split("-")[1]))


@unittest.skipUnless(attempt_dirs(), "no codes-miss attempt is committed yet")
class AttemptTests(unittest.TestCase):
    """PREREG section 6: at most three attempts, each committed with its scenario bytes and first scorecard; the
    committed scenario is the last attempt's, byte-identical, and the recorded run is that digest's."""

    def test_at_most_three_attempts_numbered_from_one(self) -> None:
        names = [p.name for p in ATTEMPTS.iterdir()]
        self.assertEqual(sorted(n for n in names if n.startswith("attempt-")),
                         [f"attempt-{i}" for i in range(1, len(attempt_dirs()) + 1)])
        self.assertLessEqual(len(attempt_dirs()), 3)

    def test_each_attempt_has_its_scenario_and_first_scorecard(self) -> None:
        for n, d in enumerate(attempt_dirs(), start=1):
            with self.subTest(attempt=d.name):
                data = (d / "scenario_codes_miss.json").read_bytes()
                if n > 1:          # a later attempt states what it changes and why before it is recorded
                    plan = (d / "PLAN.md").read_text(encoding="utf-8")
                    self.assertIn(sha256_hex(data)[:32], plan)
                    self.assertIn(f"attempt-{n - 1}/scorecard.json", plan)
                if not (d / "scorecard.json").exists():
                    self.assertEqual(d, attempt_dirs()[-1], "only the last attempt may be planned, not recorded")
                    continue
                card = json.loads((d / "scorecard.json").read_text(encoding="utf-8"))
                self.assertEqual(card["scenario"]["digest"], sha256_hex(data)[:32])
                self.assertEqual(_SCHEMA.validate(card["codes_miss"]), [])
                self.assertEqual((card["codes_miss"]["statement"], card["codes_miss"]["author_note"]),
                                 (scn.STATEMENT, scn.AUTHOR_NOTE))

    def test_the_committed_scenario_is_the_last_attempt(self) -> None:
        last = attempt_dirs()[-1]
        self.assertEqual((last / "scenario_codes_miss.json").read_bytes(), SCENARIO.read_bytes())

    def test_distinct_digests(self) -> None:
        digests = [sha256_hex((d / "scenario_codes_miss.json").read_bytes())[:32] for d in attempt_dirs()]
        self.assertEqual(len(set(digests)), len(digests))

    def test_a_later_attempt_follows_a_failure(self) -> None:
        for d in attempt_dirs()[:-1]:
            block = json.loads((d / "scorecard.json").read_text())["codes_miss"]
            fatal = not block["main"]["x_caught"] or block["gate_status"] != cm.SUPPORTED
            self.assertTrue(fatal or block["holds"] is False, d.name)
