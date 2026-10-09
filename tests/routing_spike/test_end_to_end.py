"""A small end-to-end run (seed 1, three candidates per world), process mode against in-process mode, the per-arm
answer mode, and the frozen settings against the design's table."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mycelic.collective.edge.records import RecordStore
from mycelic.collective.jsonio import strict_load
from research.routing_spike import run as R
from research.routing_spike.world import ROOT, SETTINGS

from .helpers import small_world


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls.tmp.name) / "run"
        cls.paths = {kind: R.run_world_job(1, kind == "planted", str(cls.run_dir), "in_process", top_n=3)
                     for kind in ("planted", "noplant")}
        cls.docs = {kind: strict_load(Path(p).read_bytes()) for kind, p in cls.paths.items()}
        cls.result = R.score_run(cls.run_dir, settings={**SETTINGS, "seeds": [1]})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_every_world_check_holds(self) -> None:
        for kind, doc in self.docs.items():
            for name, check in doc["checks"].items():
                with self.subTest(world=kind, check=name):
                    self.assertTrue(check["ok"], check)
        self.assertTrue(self.docs["planted"]["checks"]["equivalence"]["ok"])
        self.assertNotIn("equivalence", self.docs["noplant"]["checks"])
        for name, check in self.result["checks"].items():
            with self.subTest(check=name):
                self.assertTrue(check["ok"], check)

    def test_the_path_runs_to_the_fabric_and_the_scorer_counts_through_it(self) -> None:
        planted = self.result["per_seed_label"]["1"]
        self.assertGreaterEqual(planted["A"]["found"], planted["R"]["found"])
        self.assertGreaterEqual(planted["R"]["found"], 1)
        self.assertEqual(self.result["verdict"]["verdict"] in ("pass", "fail"), True)
        self.assertTrue(self.result["synthetic"])
        self.assertFalse(self.result["measurement"])
        self.assertEqual(len(self.docs["planted"]["labels"]), 26)

    def test_the_export_splits_off_a_timing_free_routes_file(self) -> None:
        summary, routes = R.export_docs(self.result)
        self.assertNotIn("routes_and_answers", summary)
        self.assertIn("timings", json.dumps(summary))
        text = json.dumps(routes)
        self.assertNotIn("timings", text)
        self.assertNotIn("seconds", text)
        self.assertEqual(sorted(routes["worlds"]), sorted(self.result["routes_and_answers"]))
        for name, w in routes["worlds"].items():
            src = self.result["routes_and_answers"][name]
            self.assertEqual(len(w["candidates"]), len(src["candidates"]))
            self.assertEqual(sorted(w["routes"]), sorted(src["routes"]))
            for i, c in enumerate(src["candidates"]):
                self.assertEqual(len(w["answers"][i]), len(src["answers"][c["question_id"]]))

    def test_the_result_holds_no_record_text_or_record_ref(self) -> None:
        text = json.dumps(self.result) + json.dumps(self.docs)
        self.assertIsNone(re.search(r"(plant|werk)-[a-z]+-(plant-)?\d{6}", text))
        world = small_world(Path(self.tmp.name) / "probe", seed=1, planted=True, top_n=1)
        try:
            narratives = [r["narrative"] for r in world.records if len(r["narrative"]) >= 24]
            self.assertFalse(any(n[:24] in text for n in narratives))
        finally:
            world.pipeline.store.close()

    def test_process_mode_gives_byte_identical_verdicts(self) -> None:
        # the suite's small check; ``run.py process-check`` runs a whole planted world this way (section 10,
        # deviation 18)
        res = R.process_check(self.run_dir, 1, False, Path(self.tmp.name) / "process", top_n=2)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["mode"], res["reference_mode"]), ("process", "in_process"))
        self.assertEqual(res["candidates"], 2)
        self.assertGreater(res["pairs"], 0)
        self.assertEqual(res["differing_pairs"], 0)
        doc = strict_load((Path(self.tmp.name) / "process" / "worlds" / "seed-01-noplant" / "world.json")
                          .read_bytes())
        self.assertEqual(doc["mode"], "process")
        mine = self.docs["noplant"]["answers"]
        self.assertTrue(doc["answers"])
        for qid, sites in doc["answers"].items():
            self.assertEqual(sites, mine[qid])

    def test_the_process_check_sees_a_differing_verdict(self) -> None:
        # the comparison can fail: a reference world whose answer for one (question, site) differs
        import shutil
        ref = Path(self.tmp.name) / "tampered"
        shutil.copytree(self.run_dir / "worlds" / "seed-01-noplant", ref / "worlds" / "seed-01-noplant",
                        ignore=shutil.ignore_patterns("*.sqlite3*", "fabric", "pristine", "compare", "edge", "hq"))
        doc = strict_load((ref / "worlds" / "seed-01-noplant" / "world.json").read_bytes())
        qid = doc["candidates"][0]["question_id"]
        site = next(iter(doc["answers"][qid]))
        doc["answers"][qid][site]["sha256"] = "0" * 64
        (ref / "worlds" / "seed-01-noplant" / "world.json").write_text(json.dumps(doc), encoding="utf-8")
        res = R.process_check(ref, 1, False, Path(self.tmp.name) / "process-tampered", top_n=1)
        self.assertFalse(res["ok"])
        self.assertEqual(res["differing_pairs"], 1)
        self.assertFalse(res["same"]["answers"])


class PerArmModeTests(unittest.TestCase):
    def test_a_budget_unknown_in_the_a_pass_reruns_each_label_with_fresh_site_state(self) -> None:
        original = RecordStore.answered_count
        with tempfile.TemporaryDirectory() as tmp:
            world = R.build_world(1, False, Path(tmp) / "w", top_n=1)
            target = world.candidates[0]["entity_id"]

            def answered(self, entity_type, entity_id, day):
                return 99 if entity_id == target else original(self, entity_type, entity_id, day)

            try:
                with mock.patch.object(RecordStore, "answered_count", answered):
                    import asyncio
                    doc = asyncio.run(R.run_world_async(world))
            finally:
                world.pipeline.store.close()
        self.assertEqual(doc["answers_mode"], "per_arm")
        self.assertGreater(doc["budget_unknowns"], 0)
        self.assertTrue(doc["checks"]["answer_identity"]["ok"])
        self.assertTrue(doc["checks"]["equal_budget"]["ok"])
        for qid, sites in doc["answers"].items():
            self.assertTrue(all(v["reason"] == "budget" for v in sites.values()))


class SettingsTests(unittest.TestCase):
    """The frozen settings (section 8) against the design's table."""

    def test_settings_match_section_8(self) -> None:
        text = (ROOT / "docs" / "collective" / "ROUTING-SPIKE.md").read_text(encoding="utf-8")
        section = text.split("## 8. Frozen settings", 1)[1].split("## 9.", 1)[0]
        rows = dict(re.findall(r"^\| ([^|]+?) \| (.+?) \|$", section, re.M))
        self.assertIn(f"`{SETTINGS['pack']}`", rows["Pack"])
        self.assertIn(f"{SETTINGS['weeks']}, from {SETTINGS['start']}", rows["Weeks"])
        self.assertIn(f"indices {SETTINGS['eval_from']} to {SETTINGS['eval_to']}", rows["Evaluation weeks"])
        self.assertEqual(rows["Grace weeks"], str(SETTINGS["grace_weeks"]))
        self.assertEqual(rows["Seeds"], f"{SETTINGS['seeds'][0]} to {SETTINGS['seeds'][-1]}")
        self.assertEqual(len(SETTINGS["seeds"]), 20)
        self.assertIn(SETTINGS["plant"], rows["Plant spec"])
        self.assertIn(f"`{SETTINGS['tie_salt']}`", rows["Tie salt"])
        self.assertIn(f"top {SETTINGS['top_n']}", rows["Candidates"])
        self.assertIn(f"L = {SETTINGS['site_retrieval']['max_records']}", rows["Site retrieval"])
        self.assertIn(f"{SETTINGS['site_graph']['dim']} dimensions", rows["Site graph"])
        self.assertEqual(rows["Budget m"], str(SETTINGS["m"]))
        self.assertIn(f"constant {SETTINGS['rank_fusion']['k']}", rows["Rank fusion"])
        for name in SETTINGS["rank_fusion"]["rankings"]:
            self.assertIn(f"`{name}`", rows["Rank fusion"])
        self.assertIn("B = 10,000", rows["Primary"])
        self.assertIn(f"`{SETTINGS['bootstrap']['primary_seed']}`", rows["Primary"])
        self.assertIn(f"`{SETTINGS['bootstrap']['noplant_seed']}`", rows["No-plant"])
        self.assertIn(f"{SETTINGS['noplant_bar']:.2f}", rows["No-plant"])
        self.assertIn(SETTINGS["plant_sha256"], text)

    def test_run_2_changes_only_the_rankers_scope(self) -> None:
        # section 10, deviation 16: the one settings change between Run 1's prereg and Run 2's
        base = ROOT / "docs" / "collective" / "routing_spike"
        run1 = strict_load((base / "prereg.json").read_bytes())
        run2 = strict_load((base / "prereg-run-2.json").read_bytes())
        self.assertEqual(run2["settings"], SETTINGS)
        s1, s2 = json.loads(json.dumps(run1["settings"])), json.loads(json.dumps(run2["settings"]))
        self.assertEqual(s1["site_retrieval"]["ranker"], "Tesseract over the whole session")
        self.assertIn("on or before the question's as_of", s2["site_retrieval"]["ranker"])
        s1["site_retrieval"].pop("ranker")
        s2["site_retrieval"].pop("ranker")
        self.assertEqual(s1, s2)
        self.assertEqual(run1["pack_hashes"], run2["pack_hashes"])
        self.assertEqual(run1["plant_sha256"], run2["plant_sha256"])
        self.assertNotEqual(run1["code_hash"], run2["code_hash"])

    def test_prereg_holds_the_settings_and_the_code_hash(self) -> None:
        doc = R.prereg_doc()
        self.assertEqual(doc["settings"], SETTINGS)
        self.assertEqual(doc["code_hash"], R.spike_code_hash())
        self.assertIn("research/routing_spike/hq.py", doc["code_files"])
        self.assertTrue(doc["synthetic"])
        self.assertFalse(doc["measurement"])


if __name__ == "__main__":
    unittest.main()
