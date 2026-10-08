"""The multi-site simulation (``lab.sim``) and what the lab makes of it: the plants and settings, the pipeline copy
against the collective's, end-to-end runs against the fake OpenAI-compatible server (complete, empty claims, a planted
leak, a projection skip, a timeout and low participation), the dry run and usage refusals, the request block, the
plan, the aggregate rows and the summary. Every server here is a fake: nothing measures a model.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import lab.sim as lab_sim
from lab import aggregate as lab_aggregate
from lab import request as lab_request
from lab import shard as lab_shard
from lab import summary, units
from lab.manifest import load_manifest
from lab.notes import (HARNESS_INTERRUPTED, HARNESS_USAGE, HEADINGS, LOW_PARTICIPATION, NO_MODEL_CALLS,
                       RESULT_CONTRADICTS_EXIT, RESULT_MISSING, SIM_LOW_PARTICIPATION, SIM_NOTES, SIM_PROJECTED,
                       SIM_WORLD_DIFFERS, SIM_WORLD_SAME, SIZING_NOTE, TIMED_OUT, UNEXPECTED_EXIT)
from lab.plan import build_plan
from lab.request import RequestError, load_request, validate
from lab.responder import Responder
from lab.warmup import warm_tasks
from mycelic.collective.edge.extract import TASK_NAME
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.verify import JUDGE_TASK
from mycelic.collective.evaluate import baselines, harness
from mycelic.collective.evaluate.plant import check_plant, labels_doc, load_plant, plant
from mycelic.collective.experiments.e2_pushdown import _label as e2_label
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.jsonio import canonical_bytes
from mycelic.collective.packs.generator import generate, world_digest
from mycelic.collective.packs.loader import load_pack
from mycelic.collective.stats import percentile
from tests.lab.helpers import (LAB_MANIFEST, MANIFEST_TEST, PLUMBING_001, ROOT, call_main, check_sources,
                               kill_mentioning, lab_env, make_plan, plumbing_min, sim_block, write_json)

PACK = load_pack("device_quality")
SITE_IDS = lab_sim.site_ids_of(PACK)
SETTINGS = {"plant": "sim_small", "seed": 1, "weeks": 34, "eval_from": 19, "eval_to": 33, "grace_weeks": 4,
            "tie_salt": "lab-sim", "top_n": 10, "bootstrap_b": 10000, "bootstrap_seed": 1}
ENTRY = {"alias": "lab-fake-a", "response_format": "json_schema", "transport_schema": "full"}


def _json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sim_argv(routing: Path, runs_dir: Path, run_id: str, *, budget: float = 600, **changes: Any) -> list[str]:
    values = {**SETTINGS, **changes}
    return ([f"--{key.replace('_', '-')}={value}" for key, value in values.items()]
            + [f"--routing={routing}", f"--run-id={run_id}", f"--runs-dir={runs_dir}", f"--budget-seconds={budget}"])


def write_routing(path: Path, base_url: str, **endpoint: Any) -> Path:
    """The lab's own sim routing (``units.routing_doc``), the endpoint changed by ``endpoint``."""
    doc = units.routing_doc({"experiment": "sim"}, ENTRY, base_url)
    doc["endpoints"]["lab"].update(endpoint)
    return write_json(path, doc)


def _schema_name(request_json: Any) -> Any:
    return request_json["response_format"]["json_schema"]["name"]


def sim_world(seed: int = 1, weeks: int = 34, name: str = "sim_small") -> tuple[Any, Any, list[dict[str, Any]]]:
    """(world, planted, records) as ``lab.sim`` builds them."""
    world = generate(PACK, seed, lab_sim.SITES, weeks)
    planted = plant(world, load_plant(lab_sim.PLANTS[name].path, PACK), PACK)
    return world, planted, list(world.records) + list(planted.records)


def run_with_server(tmp: Path, responder: Any, run_id: str, **server: Any) -> tuple[int, str, str, Path]:
    """``lab.sim.main`` in process against a fresh fake server; returns (exit, stdout, stderr, run directory)."""
    fake = FakeOpenAIServer(server.pop("persona", "valid"), responder=responder, **server).start()
    try:
        routing = write_routing(tmp / f"routing-{run_id}.json", fake.base_url)
        code, out, err = call_main(lab_sim, sim_argv(routing, tmp / "runs", run_id))
    finally:
        fake.stop()
    return code, out, err, tmp / "runs" / "sim" / run_id


def _card(status: str = "complete", *, scan: Any = True, extraction: Any = True,
          projection: dict[str, Any] | None = None) -> dict[str, Any]:
    if status == "skipped_projection":
        return {"kind": "lab_sim_scorecard", "status": status, "scan": None, "extraction": None,
                "projection": {"projected_s": 120.0, "threshold": 0.9, "remaining_budget_s": 60.0,
                               **(projection or {})}}
    return {"kind": "lab_sim_scorecard", "status": status, "scan": {"passed": scan},
            "extraction": {"passed": extraction, "fallback_share": 0.0 if extraction is True else 0.1}}


def _row(task: str, attempt: int, ok: bool) -> dict[str, Any]:
    return {"task": task, "attempt": attempt, "ok": ok, "fake_marker": True}


def sim_request(models: list[str], **block: Any) -> dict[str, Any]:
    obj = plumbing_min()
    obj["models"] = models
    obj["experiments"] = {"sim": {**sim_block(), **block}}
    return obj


# --------------------------------------------------------------------------------------------------- settings

class SimSettingsTests(unittest.TestCase):
    def test_plants(self) -> None:
        small, smoke = lab_sim.PLANTS["sim_small"], lab_sim.PLANTS["plant_smoke"]
        self.assertEqual(sorted(lab_sim.PLANTS), ["plant_smoke", "sim_small"])
        self.assertEqual((small.name, small.pack, small.eval_from, small.judge_calls_per_candidate),
                         ("sim_small", "device_quality", 19, 32))
        self.assertEqual((smoke.name, smoke.pack, smoke.eval_from, smoke.judge_calls_per_candidate),
                         ("plant_smoke", "device_quality", 26, 36))
        self.assertEqual(small.path, ROOT / "lab" / "plants" / "device_quality" / "sim_small.json")
        self.assertEqual(smoke.path.relative_to(ROOT).as_posix(),
                         "mycelic/collective/packs/data/device_quality/fixtures/plant_smoke.json")
        self.assertEqual(lab_sim.world_settings(small, 34), {"eval_from": 19, "eval_to": 33, "grace_weeks": 4})
        self.assertEqual(lab_sim.min_weeks(PACK), 34)
        spec = load_plant(small.path, PACK)
        self.assertEqual((spec.planted_by, spec.planter_saw_detector_code, spec.prereg_sha256),
                         ("mycelic lab (same author as the detector code)", True, None))
        self.assertEqual((len(spec.patterns), len(spec.decoys)), (4, 2))

    def test_sim_small_fits_seeds_one_to_three(self) -> None:
        harness.check_settings(PACK, sites=6, weeks=34, eval_from=19, eval_to=33, grace_weeks=4)
        spec = load_plant(lab_sim.PLANTS["sim_small"].path, PACK)
        counts = {}
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                world, planted, records = sim_world(seed)
                check_plant(spec, PACK, site_ids=SITE_IDS, master_data=world.master_data, n_weeks=34, eval_from=19,
                            eval_to=33)
                self.assertIsNone(lab_sim.plant_problem("sim_small", seed, 34))
                counts[seed] = (len(world.records), len(planted.records), len(records))
                print(f"sim_small seed {seed}: {counts[seed][0]} generated + {counts[seed][1]} planted = "
                      f"{counts[seed][2]} records")
                self.assertLessEqual(len(records), 1100, counts[seed])
        self.assertEqual(counts[1], (845, 186, 1031))

    def test_plant_smoke_needs_more_weeks(self) -> None:
        self.assertIsNotNone(lab_sim.plant_problem("plant_smoke", 1, 34))
        self.assertIsNone(lab_sim.plant_problem("plant_smoke", 1, 52))
        problem = lab_sim.plant_problem("plant_smoke", 1, 34)
        self.assertIsInstance(problem, str)
        self.assertEqual(problem, lab_sim.plant_problem("plant_smoke", 1, 34))

    def test_projection(self) -> None:
        p = lab_sim.projection(extract_p50_s=2.0, remaining_records=100, judge_p50_s=1.0, top_n=10, judge_calls=32,
                               budget_s=1000.0, elapsed_s=100.0)
        self.assertEqual(p, {"after_records": 20, "extract_record_s_p50": 2.0, "judge_s_p50": 1.0,
                             "judge_calls_assumed": 32, "top_n": 10, "remaining_records": 100, "projected_s": 520.0,
                             "budget_s": 1000.0, "remaining_budget_s": 900.0, "threshold": 0.9, "exceeds": False})
        # exactly 0.9 of the budget left fits; anything above it does not
        self.assertEqual(0.9 * 50.0, 45.0)
        at = lab_sim.projection(extract_p50_s=0.5, remaining_records=90, judge_p50_s=0.0, top_n=10, judge_calls=32,
                                budget_s=60.0, elapsed_s=10.0)
        self.assertEqual((at["projected_s"], at["remaining_budget_s"], at["exceeds"]), (45.0, 50.0, False))
        above = lab_sim.projection(extract_p50_s=0.5, remaining_records=91, judge_p50_s=0.0, top_n=10,
                                   judge_calls=32, budget_s=60.0, elapsed_s=10.0)
        self.assertTrue(above["exceeds"])
        for elapsed in (60.0, 75.0):
            gone = lab_sim.projection(extract_p50_s=0.001, remaining_records=1, judge_p50_s=0.0, top_n=1,
                                      judge_calls=1, budget_s=60.0, elapsed_s=elapsed)
            self.assertLessEqual(gone["remaining_budget_s"], 0)
            self.assertTrue(gone["exceeds"])
        self.assertEqual(lab_sim.suggested_minutes(480.0), 10)
        self.assertEqual(lab_sim.suggested_minutes(481.0), 11)
        self.assertIsNone(lab_sim.suggested_minutes(None))

    def test_pushdown_label_matches_e2(self) -> None:
        world, _, records = sim_world()
        weeks = baselines.world_weeks(PACK.generator["start"], 34)
        labels = labels_doc(load_plant(lab_sim.PLANTS["sim_small"].path, PACK), PACK, weeks=weeks, eval_from=19,
                            eval_to=33, grace_weeks=4)
        with tempfile.TemporaryDirectory(prefix="lab-sim-label-") as tmp:
            pipeline = baselines.run_pipeline(PACK, records, site_ids=SITE_IDS, master_data=world.master_data,
                                              weeks=weeks, workdir=tmp)
            try:
                x = baselines.hq_results(pipeline.store, as_of=pipeline.as_of, tie_salt="lab-sim")["X"]
            finally:
                pipeline.close()
        seen = set()
        snapshots = [c for c in x["candidates"] if c["snapshot"] is not None]
        self.assertGreater(len(snapshots), 10)
        for c in snapshots:
            week = c["snapshot"]["week"]
            got = lab_sim.pushdown_label(c["key"], week, labels)
            self.assertEqual(got, e2_label(c["key"], week, labels), c["key"])
            seen.add(got)
        self.assertLessEqual(seen, {"true", "background", "decoy"})
        self.assertIn("true", seen)
        self.assertIn("background", seen)
        decoy_key = labels["decoys"][0]["keys"][0]
        self.assertEqual(lab_sim.pushdown_label(decoy_key, weeks[20], labels), e2_label(decoy_key, weeks[20], labels))
        pattern = labels["patterns"][0]
        for week in (pattern["found_from"], pattern["found_to"], weeks[0], weeks[-1]):
            self.assertEqual(lab_sim.pushdown_label(pattern["key"], week, labels),
                             e2_label(pattern["key"], week, labels))

    def test_harness_status_table(self) -> None:
        skipped = SIM_PROJECTED.format(projected="2.0", budget="0.9")
        self.assertEqual(skipped, "projected 2.0 min > budget 0.9 min")
        rows = [
            (0, False, _card(), ("ok", None)),
            (1, False, _card(scan=False), ("result_fail", None)),
            (1, False, _card(scan=False, extraction=False), ("result_fail", None)),
            (1, False, _card(extraction=False), ("ok", None)),
            (1, False, _card("skipped_projection"), ("skipped", skipped)),
            (0, False, _card("skipped_projection"), ("failed", RESULT_CONTRADICTS_EXIT)),
            (1, False, _card(), ("failed", RESULT_CONTRADICTS_EXIT)),
            (0, False, _card(scan=False), ("failed", RESULT_CONTRADICTS_EXIT)),
            (0, False, _card(extraction=False), ("failed", RESULT_CONTRADICTS_EXIT)),
            (0, False, None, ("failed", RESULT_MISSING)),
            (1, False, None, ("failed", RESULT_MISSING)),
            (0, False, {"kind": "g0_leakage", "passed": True}, ("failed", RESULT_MISSING)),
            (0, False, {**_card(), "status": "other"}, ("failed", RESULT_MISSING)),
            (0, False, _card(scan="yes"), ("failed", RESULT_MISSING)),
            (0, False, {**_card(), "extraction": None}, ("failed", RESULT_MISSING)),
            (1, False, _card("skipped_projection", projection={"projected_s": "120"}), ("failed", RESULT_MISSING)),
            (1, False, _card("skipped_projection", projection={"threshold": True}), ("failed", RESULT_MISSING)),
            (1, False, {**_card("skipped_projection"), "projection": None}, ("failed", RESULT_MISSING)),
            (2, False, _card(), ("failed", HARNESS_USAGE)),
            (3, False, _card(), ("failed", UNEXPECTED_EXIT)),
            (None, False, None, ("failed", UNEXPECTED_EXIT)),
            (130, False, None, ("interrupted", HARNESS_INTERRUPTED)),
            (0, True, _card(), ("timed_out", TIMED_OUT)),
            (-15, True, None, ("timed_out", TIMED_OUT)),
        ]
        for code, timed_out, result, expected in rows:
            with self.subTest(code=code, timed_out=timed_out, result=result):
                self.assertEqual(units.harness_status("sim", code, timed_out, result), expected)

    def test_participation_rule(self) -> None:
        rows = [_row(TASK_NAME, 1, True)] * 20 + [_row(JUDGE_TASK, 1, True)] * 20
        record, problem = units.participation("sim", rows, [TASK_NAME], _card())
        self.assertIsNone(problem)
        self.assertEqual(record["sim"], {"fallback_share": 0.0, "max_share": 0.05})
        record, problem = units.participation("sim", rows, [TASK_NAME], _card(extraction=False))
        self.assertEqual(problem, f"{SIM_LOW_PARTICIPATION}: lexical fallback share 0.1 above 0.05")
        self.assertEqual(record["sim"], {"fallback_share": 0.1, "max_share": 0.05})
        _, problem = units.participation("sim", rows, [TASK_NAME], None)
        self.assertEqual(problem, f"{SIM_LOW_PARTICIPATION}: lexical fallback share None above 0.05")
        # the required-task check comes first, the ledgers' ok share after the sim's own rule
        _, problem = units.participation("sim", rows[20:], [TASK_NAME], _card(extraction=False))
        self.assertEqual(problem, f"{NO_MODEL_CALLS}: {TASK_NAME}")
        failing = rows[:20] + [_row(JUDGE_TASK, 1, False)] * 2 + [_row(JUDGE_TASK, 1, True)] * 18
        _, problem = units.participation("sim", failing, [TASK_NAME], _card(extraction=False))
        self.assertTrue(problem.startswith(SIM_LOW_PARTICIPATION), problem)
        _, problem = units.participation("sim", failing, [TASK_NAME], _card())
        self.assertTrue(problem.startswith(f"{LOW_PARTICIPATION}: {JUDGE_TASK}"), problem)
        self.assertIsNone(units.participation("g0", rows, [TASK_NAME], None)[0]["sim"])
        self.assertEqual(units.required_tasks({"experiment": "sim", "params": {}}), [TASK_NAME])

    def test_build_argv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab-sim-argv-") as tmp:
            plan, _ = make_plan(Path(tmp), sim_request(["fake-a"]))
        (unit,) = plan["units"]
        out = Path("/out")
        argv = units.build_argv(unit, out, out / "routing" / "sim-fake-a-s1.json", budget_s=870)
        self.assertEqual(argv[:3], [sys.executable, "-m", "lab.sim"])
        self.assertEqual(argv[3:], [
            "--plant=sim_small", "--seed=1", "--weeks=34", "--eval-from=19", "--eval-to=33", "--grace-weeks=4",
            "--tie-salt=lab-sim", "--top-n=10", "--bootstrap-b=10000", "--bootstrap-seed=1",
            "--routing=/out/routing/sim-fake-a-s1.json", f"--run-id={unit['run_id']}",
            "--runs-dir=/out/work/sim-fake-a-s1", "--budget-seconds=870"])
        with self.assertRaises(ValueError):
            units.build_argv(unit, out, out / "routing" / "x.json")
        self.assertEqual(units.harness_dir(unit, out), out / "work" / "sim-fake-a-s1" / "sim" / unit["run_id"])
        self.assertEqual(units.SIM_BUDGET_MARGIN_S, 30)

    def test_warm_tasks_sim_like_g0(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab-sim-warm-") as tmp:
            request = plumbing_min()
            request["experiments"]["g0"]["seed"] = 1
            request["experiments"]["sim"] = sim_block(models=["fake-b"])
            plan, _ = make_plan(Path(tmp), request)
        by_unit = {u["unit"]: u for u in plan["units"]}
        g0, sim = by_unit["g0-fake-b"], by_unit["sim-fake-b-s1"]
        only_g0, only_sim = warm_tasks([g0], plan), warm_tasks([sim], plan)
        self.assertEqual([(t.task.name, t.pack) for t in only_sim], [(TASK_NAME, "device_quality"),
                                                                    (JUDGE_TASK, "device_quality")])
        for a, b in zip(only_g0, only_sim):
            self.assertEqual((a.task, a.pack, a.schema, a.payload, a.worst, a.stream),
                             (b.task, b.pack, b.schema, b.payload, b.worst, b.stream))
        both = warm_tasks([g0, sim], plan)
        self.assertEqual([(t.task.name, t.pack, t.units) for t in both],
                         [(TASK_NAME, "device_quality", ("g0-fake-b", "sim-fake-b-s1")),
                          (JUDGE_TASK, "device_quality", ("g0-fake-b", "sim-fake-b-s1"))])


# --------------------------------------------------------------------------------------------------- the pipeline

class PipelineEquivalenceTests(unittest.TestCase):
    """``lab.sim.run_pipeline`` without runtimes is ``baselines.run_pipeline``: a drift upstream fails here."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-equiv-"))
        cls.world, _, cls.records = sim_world(1, 52, "plant_smoke")
        cls.weeks = baselines.world_weeks(PACK.generator["start"], 52)
        cls.ours, cls.summaries, cls.ingested = lab_sim.run_pipeline(
            PACK, cls.records, site_ids=SITE_IDS, master_data=cls.world.master_data, weeks=cls.weeks,
            workdir=cls.tmp / "a")
        cls.theirs = baselines.run_pipeline(PACK, cls.records, site_ids=SITE_IDS, master_data=cls.world.master_data,
                                            weeks=cls.weeks, workdir=cls.tmp / "b")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.ours.close()
        cls.theirs.close()
        shutil.rmtree(cls.tmp, True)

    def test_hq_results_equal(self) -> None:
        a = baselines.hq_results(self.ours.store, as_of=self.ours.as_of, tie_salt="lab-sim")
        b = baselines.hq_results(self.theirs.store, as_of=self.theirs.as_of, tie_salt="lab-sim")
        for channel in ("X", "S"):
            with self.subTest(channel=channel):
                self.assertEqual(canonical_bytes(a[channel]), canonical_bytes(b[channel]))
                self.assertIn("run_id", a[channel])
        self.assertTrue(a["X"]["candidates"])

    def test_u_cells_and_single_site_equal(self) -> None:
        self.assertEqual(canonical_bytes(baselines.u_cells(self.ours)), canonical_bytes(baselines.u_cells(self.theirs)))
        self.assertEqual(canonical_bytes(baselines.single_site_alerts(self.ours, tie_salt="lab-sim")),
                         canonical_bytes(baselines.single_site_alerts(self.theirs, tie_salt="lab-sim")))

    def test_summaries_are_lexical(self) -> None:
        self.assertEqual(sorted(self.summaries), sorted(SITE_IDS))
        for sid in SITE_IDS:
            n = sum(1 for r in self.records if r["site"] == sid)
            with self.subTest(site=sid):
                self.assertEqual(dict(self.summaries[sid].extractors), {"lexical": n})
                self.assertEqual((self.summaries[sid].records, self.ingested[sid]), (n, n))

    def test_runtimes_must_match_the_sites(self) -> None:
        for runtimes in ({}, {sid: None for sid in SITE_IDS[:-1]}, {**{sid: None for sid in SITE_IDS}, "x": None}):
            with self.subTest(keys=sorted(runtimes)), self.assertRaises(ValueError):
                lab_sim.run_pipeline(PACK, self.records, site_ids=SITE_IDS, master_data=self.world.master_data,
                                     weeks=self.weeks, workdir=self.tmp / "never", runtimes=runtimes)
        self.assertFalse((self.tmp / "never").exists())


# --------------------------------------------------------------------------------------------------- end to end

class FakeServerSimTests(unittest.TestCase):
    """Run A in process, run B as a subprocess with another hash seed, against one fake server."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-run-"))
        t0 = time.monotonic()
        server = FakeOpenAIServer("valid", responder=Responder(PACK)).start()
        try:
            routing = write_routing(cls.tmp / "routing.json", server.base_url)
            cls.runs = cls.tmp / "runs"
            cls.a = call_main(lab_sim, sim_argv(routing, cls.runs, "A"))
            cls.b = subprocess.run([sys.executable, "-m", "lab.sim", *sim_argv(routing, cls.runs, "B")], cwd=ROOT,
                                   env=lab_env(PYTHONHASHSEED="7"), capture_output=True, text=True, timeout=300,
                                   stdin=subprocess.DEVNULL)
        finally:
            server.stop()
        cls.wall = time.monotonic() - t0
        print(f"FakeServerSimTests: two sim_small runs in {cls.wall:.1f} s")
        cls.run_a, cls.run_b = cls.runs / "sim" / "A", cls.runs / "sim" / "B"
        cls.doc_a = _json(cls.run_a / "scorecard.json") if (cls.run_a / "scorecard.json").exists() else None
        cls.doc_b = _json(cls.run_b / "scorecard.json") if (cls.run_b / "scorecard.json").exists() else None
        cls.world, cls.planted, cls.records = sim_world()

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def setUp(self) -> None:
        self.assertEqual(self.a[0], 0, self.a[2])
        self.assertEqual(self.b.returncode, 0, self.b.stderr)

    def test_wall_time(self) -> None:
        self.assertLess(self.wall, 60)

    def test_status_line_and_status(self) -> None:
        for (_, out, err), doc, run in ((self.a, self.doc_a, self.run_a),
                                        ((self.b.returncode, self.b.stdout, self.b.stderr), self.doc_b, self.run_b)):
            self.assertEqual(out, f"sim: plant=sim_small seed=1 status=complete measurement=false raw_text_crossed=0 "
                                  f"-> {run / 'scorecard.json'} (synthetic, internal only)\n")
            self.assertEqual(err, "")
            self.assertEqual(doc["status"], "complete")

    def test_schema_and_content_hash(self) -> None:
        self.assertEqual(lab_sim.scorecard_problems(self.doc_a), [])
        self.assertEqual(lab_sim.scorecard_problems(self.doc_b), [])
        self.assertEqual(self.doc_a["content_hash"], self.doc_b["content_hash"])
        self.assertEqual(self.doc_a["content_hash"], lab_sim.content_hash(self.doc_a))
        self.assertNotEqual(self.doc_a["run_id"], self.doc_b["run_id"])
        self.assertNotEqual(self.doc_a["created_at"], self.doc_b["created_at"])
        self.assertEqual(self.doc_a["content_hash_excludes"], list(lab_sim.CONTENT_HASH_EXCLUDES))
        broken = copy.deepcopy(self.doc_a)
        broken["channels"] = None
        self.assertIn(("$.channels", "status"), lab_sim.scorecard_problems(broken))
        skipped = {**copy.deepcopy(self.doc_a), "status": "skipped_projection"}
        self.assertEqual({p for p, why in lab_sim.scorecard_problems(skipped) if why == "status"},
                         {f"$.{block}" for block in lab_sim.RESULT_BLOCKS})

    def test_stamps(self) -> None:
        self.assertEqual(self.doc_a["stamps"], {
            "synthetic": True, "internal_only": True, "blind": False, "measurement": False,
            "measurement_reasons": ["fake_model"], "extractor": "model:lab", "data_label": "synthetic",
            "same_author_pack": PACK.same_author_as_code})
        self.assertEqual(self.doc_a["endpoint"]["listing"]["fake"], True)

    def test_every_block(self) -> None:
        for block in lab_sim.RESULT_BLOCKS:
            self.assertIsNotNone(self.doc_a[block], block)
        self.assertEqual(sorted(self.doc_a["channels"]), sorted(lab_sim.CHANNELS))
        self.assertEqual(sorted(self.doc_a["lifts"]), sorted(name for name, _, _ in lab_sim.LIFTS))
        self.assertEqual([p["id"] for p in self.doc_a["patterns"]], ["p1", "p2", "p3", "p4"])
        for p in self.doc_a["patterns"]:
            self.assertEqual(sorted(p["outcomes"]), sorted(lab_sim.CHANNELS))
        self.assertEqual(self.doc_a["notes"], ["synthetic_internal", "lexical_exact", "few_patterns",
                                               "r_model_free"])
        self.assertEqual(list(SIM_NOTES), self.doc_a["notes"])

    def test_extraction_per_site(self) -> None:
        extraction = self.doc_a["extraction"]
        for sid in SITE_IDS:
            n = sum(1 for r in self.records if r["site"] == sid)
            site = extraction["sites"][sid]
            with self.subTest(site=sid):
                self.assertEqual((site["records"], site["ingested"], site["model"], site["fallback"],
                                  site["fallback_share"]), (n, n, n, 0, 0.0))
                self.assertEqual(set(site["errors"].values()), {0})
                self.assertGreater(site["claims"], 0)
        self.assertEqual((extraction["records"], extraction["fallback"], extraction["fallback_share"],
                          extraction["max_fallback_share"], extraction["passed"]), (1031, 0, 0.0, 0.05, True))

    def test_scan(self) -> None:
        scan = self.doc_a["scan"]
        self.assertEqual(self.doc_a["raw_text_crossed"], 0)
        self.assertEqual(scan["shingle_overlap_bytes"], 0)
        self.assertGreater(scan["positive_control_bytes"], 0)
        self.assertTrue(scan["passed"])
        self.assertGreater(scan["artifacts"], len(SITE_IDS) + 1)
        self.assertEqual(scan["method"], lab_sim.SCAN_METHOD)

    def test_latency_from_ledgers(self) -> None:
        rows = [r for p in sorted((self.run_a / "edge").glob("site-*.ledger.jsonl")) for r in read_ledger(p)]
        self.assertEqual(len(list((self.run_a / "edge").glob("site-*.ledger.jsonl"))), len(SITE_IDS))
        for task in (TASK_NAME, JUDGE_TASK):
            values = [r["latency_ms"] for r in rows if r["task"] == task and r["ok"] is True
                      and isinstance(r["latency_ms"], (int, float))]
            with self.subTest(task=task):
                self.assertEqual(self.doc_a["latency"][task], {"n": len(values),
                                                               "p50_ms": round(percentile(values, 50), 3),
                                                               "p95_ms": round(percentile(values, 95), 3)})
        self.assertEqual(self.doc_a["latency"][TASK_NAME]["n"], 1031)
        judged = [r for r in rows if r["task"] == JUDGE_TASK and not r["ref"].startswith("jw:")]
        warmup = [r for r in rows if r["task"] == JUDGE_TASK and r["ref"].startswith("jw:")]
        self.assertEqual(len(warmup), lab_sim.JUDGE_WARMUP_CALLS)
        self.assertEqual(self.doc_a["pushdown"]["judge_calls"], len(judged))

    def test_pushdown(self) -> None:
        pushdown = self.doc_a["pushdown"]
        self.assertEqual(pushdown["n"], min(10, pushdown["detected"]))
        self.assertEqual(pushdown["n"], len(pushdown["items"]))
        self.assertEqual(sum(pushdown["labels"].values()), pushdown["n"])
        self.assertEqual(sum(pushdown["statuses"].values()), pushdown["n"])
        self.assertEqual(pushdown["n_true"], pushdown["labels"]["true"])
        self.assertGreater(pushdown["n_true"], 0)
        self.assertEqual(pushdown["timeouts"], pushdown["verdicts"]["unknown"]["timeout"])
        keys = [(i["week"], i["key"]) for i in pushdown["items"]]
        self.assertEqual(len(set(keys)), len(keys))
        for item in pushdown["items"]:
            self.assertEqual(item["score_pushdown"] // 1_000_000, lab_sim.STATUS_RANK[item["status"]])

    def test_projection_block(self) -> None:
        p = self.doc_a["projection"]
        self.assertTrue(p["checked"])
        self.assertFalse(p["exceeds"])
        self.assertEqual((p["after_records"], p["judge_source"], p["judge_calls_assumed"], p["top_n"],
                          p["remaining_records"], p["threshold"], p["budget_s"]),
                         (20, "warmup", 32, 10, 1011, 0.9, 600.0))
        self.assertEqual((p["estimate_s"], p["suggested_basis"]), (self.doc_a["timings"]["total_s"], "measured"))
        self.assertEqual(p["suggested_minutes"], math.ceil(1.25 * p["estimate_s"] / 60))
        measured = p["measured"]
        self.assertEqual(measured["judge_calls_per_candidate"],
                         round(self.doc_a["pushdown"]["judge_calls"] / self.doc_a["pushdown"]["n"], 6))
        self.assertGreater(measured["extract_record_s_p50"], 0)

    def test_run_files(self) -> None:
        labels = _json(self.run_a / "labels.json")
        self.assertEqual(labels["seeds"], [{"seed": 1, "planted_records": 186}])
        self.assertEqual([p["id"] for p in labels["patterns"]], ["p1", "p2", "p3", "p4"])
        progress = _json(self.run_a / "progress.json")
        self.assertEqual((progress["kind"], progress["phase"], progress["run_id"]), ("lab_sim_progress", "done", "A"))
        self.assertEqual((progress["records"]["done"], progress["records"]["total"]), (1031, 1031))
        self.assertEqual(progress["candidates"]["done"], progress["candidates"]["total"])
        self.assertEqual(sorted(progress["records"]["sites"]), sorted(SITE_IDS))
        self.assertIsNotNone(progress["estimate_s"])
        self.assertEqual(progress["suggested_minutes"], math.ceil(1.25 * progress["estimate_s"] / 60))
        self.assertEqual(progress["latency_s"]["judge_warmup"]["n"], lab_sim.JUDGE_WARMUP_CALLS)
        self.assertEqual(progress["projection"]["exceeds"], False)
        world = self.doc_a["world"]
        self.assertEqual(world["world_digest"], world_digest(generate(PACK, 1, 6, 34)))
        self.assertEqual(world["records"], {"generated": 845, "planted": 186, "total": 1031})
        self.assertEqual((world["plant"], world["plant_sha256"]),
                         ("sim_small", load_plant(lab_sim.PLANTS["sim_small"].path, PACK).sha256))
        self.assertEqual((world["eval_from_week"], world["eval_to_week"], world["evaluation_weeks"]),
                         ("2024-W20", "2024-W34", 15))

    def test_no_database_outside_work(self) -> None:
        for run in (self.run_a, self.run_b):
            for path in run.rglob("*"):
                rel = path.relative_to(run)
                if ".sqlite3" in path.name:
                    self.assertEqual(rel.parts[0], "work", rel)
            self.assertEqual(sorted(p.name for p in run.iterdir()),
                             ["edge", "labels.json", "progress.json", "scorecard.json", "work"])
            self.assertEqual(sorted(p.name for p in (run / "edge").iterdir()),
                             sorted(f"site-{sid}.ledger.jsonl" for sid in SITE_IDS))


class _EmptyClaims(Responder):
    """Every extraction reply holds no claim; the judge answers lexically."""

    def __call__(self, request_json: Any) -> dict[str, Any]:
        if _schema_name(request_json) == TASK_NAME:
            return {"claims": []}
        return super().__call__(request_json)


class EmptyClaimsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-empty-"))
        cls.code, cls.out, cls.err, cls.run_dir = run_with_server(cls.tmp, _EmptyClaims(PACK), "E")
        cls.doc = _json(cls.run_dir / "scorecard.json")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, True)

    def test_the_model_channel_reads_nothing_from_narratives(self) -> None:
        self.assertEqual(self.code, 0, self.err)
        channels = self.doc["channels"]
        self.assertEqual(channels["X_lexical"]["found"], 4)
        self.assertEqual(channels["X_lexical"]["by_visibility"]["narrative_only"]["found"], 3)
        self.assertEqual(channels["X_model"]["by_visibility"]["narrative_only"]["found"], 0)
        self.assertLess(self.doc["lifts"]["X_model_minus_X_lexical"]["estimate"], 0)
        self.assertTrue(self.doc["extraction"]["passed"])
        self.assertEqual(self.doc["extraction"]["fallback"], 0)
        self.assertTrue(self.doc["scan"]["passed"])


class _LeakySite(EdgeSite):
    """The first site writes one line holding a slice of one of its own narratives to its egress log."""

    leak = ""
    leaked = False

    def emit_cells(self, as_of: str) -> Any:
        emission = super().emit_cells(as_of)
        if self.site_id == SITE_IDS[0] and not _LeakySite.leaked:
            with open(self.boundary.egress_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"note": _LeakySite.leak}) + "\n")
            _LeakySite.leaked = True
        return emission


class LeakageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-leak-"))
        _, _, records = sim_world()
        narrative = next(r["narrative"] for r in records if r["site"] == SITE_IDS[0] and len(r["narrative"]) >= 200)
        _LeakySite.leak, _LeakySite.leaked = narrative[:200], False
        with mock.patch.object(lab_sim, "EdgeSite", _LeakySite):
            cls.code, cls.out, cls.err, cls.run_dir = run_with_server(cls.tmp, Responder(PACK), "L")
        cls.doc = _json(cls.run_dir / "scorecard.json")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, True)

    def test_a_leak_fails_the_scan(self) -> None:
        self.assertTrue(_LeakySite.leaked)
        self.assertEqual(self.code, 1, self.err)
        self.assertGreater(self.doc["raw_text_crossed"], 0)
        self.assertEqual(self.doc["raw_text_crossed"], self.doc["scan"]["shingle_overlap_bytes"])
        self.assertFalse(self.doc["scan"]["passed"])
        self.assertTrue(self.doc["extraction"]["passed"])
        self.assertEqual(lab_sim.scorecard_problems(self.doc), [])
        self.assertEqual(units.harness_status("sim", 1, False, self.doc), ("result_fail", None))
        self.assertIn("raw_text_crossed=", self.out)

    def test_only_the_model_pipeline_is_scanned(self) -> None:
        narratives = [r["narrative"] for r in sim_world()[2]]
        model = lab_sim.scan_pipeline(PACK, self.run_dir / "work" / "model", SITE_IDS, narratives)
        # the run scanned HQ's database with its WAL still open; closing it since has checkpointed the WAL
        for key in ("artifacts", "shingle_overlap_bytes", "positive_control_bytes", "passed"):
            self.assertEqual(model[key], self.doc["scan"][key], key)
        lexical = lab_sim.scan_pipeline(PACK, self.run_dir / "work" / "lexical", SITE_IDS, narratives)
        self.assertTrue(lexical["passed"])
        self.assertEqual(lexical["shingle_overlap_bytes"], 0)
        egress = (self.run_dir / "work" / "lexical" / "edge" / f"site-{SITE_IDS[0]}.egress.jsonl").read_text("utf-8")
        self.assertNotIn(_LeakySite.leak, egress)


# --------------------------------------------------------------------------------------------------- budgets

class ProjectionUnitTests(unittest.TestCase):
    """A one-minute fake-slow unit: the projection after twenty extractions skips it inside its budget."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-proj-"))
        request = sim_request(["fake-slow"], minutes=1)
        cls.plan, _ = make_plan(cls.tmp, request)
        (cls.unit,) = cls.plan["units"]
        slow = functools.partial(FakeOpenAIServer, slow_s=0.05)
        with mock.patch("lab.server.FakeOpenAIServer", slow):
            cls.record = units.run_unit(cls.unit, cls.plan, cls.tmp / "OUT", timeout_s=60, provider_override=None)
        cls.run_dir = cls.tmp / "OUT" / "runs" / "sim" / cls.unit["run_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def test_skipped_inside_the_budget(self) -> None:
        r = self.record
        self.assertEqual((r["status"], r["exit_code"]), ("skipped", 1), r)
        self.assertIn("projected", r["status_reason"])
        self.assertIn(" min > budget ", r["status_reason"])
        self.assertLess(r["wall_s"], 60)
        self.assertIn("--budget-seconds=30", r["argv"])
        self.assertIsNone(r["participation"])
        self.assertEqual(r["measurement_class"], "plumbing")

    def test_collected_scorecard(self) -> None:
        doc = _json(self.run_dir / "scorecard.json")
        self.assertEqual(lab_sim.scorecard_problems(doc), [])
        self.assertEqual(doc["status"], "skipped_projection")
        p = doc["projection"]
        self.assertTrue(p["checked"])
        self.assertTrue(p["exceeds"])
        self.assertEqual((p["after_records"], p["remaining_records"], p["judge_source"], p["suggested_basis"]),
                         (20, 1011, "warmup", "projected"))
        self.assertGreater(p["projected_s"], 0.9 * p["remaining_budget_s"])
        self.assertAlmostEqual(p["estimate_s"], p["budget_s"] - p["remaining_budget_s"] + p["projected_s"], places=2)
        self.assertEqual(p["suggested_minutes"], math.ceil(1.25 * p["estimate_s"] / 60))
        self.assertEqual(self.record["status_reason"], SIM_PROJECTED.format(
            projected=f"{p['projected_s'] / 60:.1f}", budget=f"{p['threshold'] * p['remaining_budget_s'] / 60:.1f}"))
        self.assertGreaterEqual(doc["latency"][TASK_NAME]["n"], 20)
        self.assertIn("skipped_projection", doc["stamps"]["measurement_reasons"])
        self.assertIn("fake_model", doc["stamps"]["measurement_reasons"])
        self.assertFalse(doc["stamps"]["measurement"])
        for block in lab_sim.RESULT_BLOCKS:
            self.assertIsNone(doc[block], block)
        progress = _json(self.run_dir / "progress.json")
        self.assertEqual((progress["phase"], progress["records"]["done"]), ("skipped_projection", 20))

    def test_collected_files(self) -> None:
        files = sorted(self.record["files"])
        self.assertTrue(files)
        self.assertFalse([f for f in files if "sqlite" in f or "/work/" in f])
        self.assertIn(f"runs/sim/{self.unit['run_id']}/scorecard.json", files)
        self.assertIn(f"runs/sim/{self.unit['run_id']}/progress.json", files)
        self.assertFalse((self.tmp / "OUT" / "work" / self.unit["unit"]).exists())


class TimeoutTests(unittest.TestCase):
    """A run killed by the unit timeout still leaves a readable progress.json with an estimate."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-timeout-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))

    def test_timed_out_with_progress(self) -> None:
        plan, _ = make_plan(self.tmp, sim_request(["fake-slow"]))
        (unit,) = plan["units"]
        out = self.tmp / "OUT"
        server = FakeOpenAIServer("slow", responder=Responder(PACK), slow_s=0.05).start()
        self.addCleanup(server.stop)
        routing = write_json(out / "routing" / "sim.json",
                             units.routing_doc(unit, plan["models"]["fake-slow"], server.base_url))
        argv = units.build_argv(unit, out, routing, budget_s=600)
        (out / "units").mkdir(parents=True)
        proc = units.run_process(argv, units.subprocess_env(os.environ, []), cwd=ROOT, timeout_s=10,
                                 stdout_path=out / "units" / "stdout.log", stderr_path=out / "units" / "stderr.log")
        files = units.collect(unit, out)
        result = units.load_result(unit, out)
        self.assertIsNone(result)
        self.assertEqual(units.harness_status("sim", proc.exit_code, proc.timed_out, result), ("timed_out", TIMED_OUT))
        run = out / "runs" / "sim" / unit["run_id"]
        progress = _json(run / "progress.json")
        self.assertEqual(progress["phase"], "extract")
        self.assertLess(0, progress["records"]["done"])
        self.assertLess(progress["records"]["done"], progress["records"]["total"])
        self.assertIsNotNone(progress["estimate_s"])
        self.assertIsNotNone(progress["suggested_minutes"])
        self.assertFalse(progress["projection"]["exceeds"])
        self.assertIn(f"runs/sim/{unit['run_id']}/progress.json", files)
        self.assertFalse([f for f in files if "sqlite" in f])


# --------------------------------------------------------------------------------------------------- participation

class _FailingResponder(Responder):
    """About one extraction payload in ten (by a hash of its text) gets a reply its schema refuses, on the attempt
    and on the repair alike, so the extractor falls back to the lexical one for those records."""

    def __call__(self, request_json: Any) -> dict[str, Any]:
        payload = request_payload(request_json)
        if _schema_name(request_json) == TASK_NAME and isinstance(payload, dict):
            digest = hashlib.sha256(payload["text"].encode("utf-8")).digest()
            if digest[0] % 10 == 0:
                return {"claims": "refused"}
        return super().__call__(request_json)


class ParticipationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-part-"))
        cls.plan, cls.plan_path = make_plan(cls.tmp, sim_request(["fake-a"], minutes=15))
        (cls.unit,) = cls.plan["units"]
        cls.out = cls.tmp / "shards" / "s001-fake-a"
        stdout = io.StringIO()
        with mock.patch("lab.server.Responder", _FailingResponder), contextlib.redirect_stdout(stdout):
            cls.code = lab_shard.run_shard(str(cls.plan_path), "s001-fake-a", cls.out, "fake", None)
        cls.stdout = stdout.getvalue()
        lab_shard.seal(cls.out, "s001-fake-a", {"run": "failure" if cls.code else "success"}, str(cls.plan_path))
        cls.report_dir = cls.tmp / "report"
        cls.aggregated = call_main(lab_aggregate, [
            "--plan", str(cls.plan_path), "--provision", str(cls.tmp / "provision"), "--shards",
            str(cls.tmp / "shards"), "--manifest", str(MANIFEST_TEST), "--out", str(cls.report_dir)])
        cls.report = _json(cls.report_dir / "report.json")
        cls.md, cls.entries = summary.render_report(cls.report_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def test_unit_invalid(self) -> None:
        self.assertEqual(self.code, 1, self.stdout)
        record = _json(self.out / "units" / self.unit["unit"] / "unit.json")
        self.assertEqual((record["status"], record["exit_code"]), ("invalid", 1))
        self.assertTrue(record["status_reason"].startswith(f"{SIM_LOW_PARTICIPATION}: lexical fallback share"),
                        record["status_reason"])
        self.assertGreater(record["participation"]["sim"]["fallback_share"], 0.05)
        doc = _json(self.out / "runs" / "sim" / self.unit["run_id"] / "scorecard.json")
        self.assertEqual(doc["status"], "complete")
        self.assertGreater(doc["extraction"]["fallback_share"], 0.05)
        self.assertLess(doc["extraction"]["fallback_share"], 0.2)
        self.assertFalse(doc["extraction"]["passed"])
        self.assertTrue(doc["scan"]["passed"])
        self.assertFalse(doc["stamps"]["measurement"])
        self.assertEqual(doc["stamps"]["measurement_reasons"], ["fake_model", "low_participation"])
        self.assertEqual(record["participation"]["sim"]["fallback_share"], doc["extraction"]["fallback_share"])
        fallback = sum(site["fallback"] for site in doc["extraction"]["sites"].values())
        self.assertEqual(fallback, doc["extraction"]["fallback"])
        self.assertGreater(sum(site["errors"]["schema_invalid"] for site in doc["extraction"]["sites"].values()), 0)

    def test_report_rows(self) -> None:
        self.assertEqual(self.aggregated[0], 0, self.aggregated[2])
        (row,) = self.report["units"]
        self.assertEqual((row["unit"], row["status"], row["display_class"]), ("sim-fake-a-s1", "invalid",
                                                                             "no-result"))
        self.assertEqual(self.report["sim"], [])
        self.assertEqual(self.report["notes"]["sim_world_digest"], [])
        (sizing,) = self.report["sim_sizing"]
        doc = _json(self.out / "runs" / "sim" / self.unit["run_id"] / "scorecard.json")
        self.assertEqual(sizing, {
            "unit": "sim-fake-a-s1", "model": "fake-a", "cpu_model": row["cpu_model"], "status": "invalid",
            "display_class": "no-result", "source": "scorecard", "records_done": 1031, "records_total": 1031,
            "extract_record_s_p50": doc["projection"]["measured"]["extract_record_s_p50"],
            "judge_s_p50": doc["projection"]["measured"]["judge_s_p50"],
            "estimate_s": doc["projection"]["estimate_s"],
            "suggested_minutes": doc["projection"]["suggested_minutes"]})

    def test_report_md(self) -> None:
        lines = self.md.splitlines()
        self.assertEqual(lines[0], "PLUMBING CHECK: no model was run")
        no_result = self.md.split(f"### {HEADINGS['no-result']}", 1)[1].split("\n### ", 1)[0]
        self.assertIn("`sim-fake-a-s1`", no_result)
        self.assertIn("`invalid`", no_result)
        sizing = self.md.split(f"### {HEADINGS['sizing']}", 1)[1].split("\n### ", 1)[0]
        self.assertIn("`sim-fake-a-s1`", sizing)
        self.assertIn(SIZING_NOTE, sizing)
        self.assertNotIn(HEADINGS["sim"], self.md)
        check_sources(self, self.md, self.entries, self.report_dir)
        pointers = {e["pointer"] for e in self.entries}
        for key in ("records_done", "records_total", "extract_record_s_p50", "judge_s_p50", "estimate_s",
                    "suggested_minutes"):
            self.assertIn(f"/sim_sizing/0/{key}", pointers)


# --------------------------------------------------------------------------------------------------- the CLI

class DryRunAndUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-sim-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.routing = write_routing(self.tmp / "routing.json", "http://127.0.0.1:9/v1")

    def test_dry_run_with_a_missing_routing_file(self) -> None:
        runs, missing = self.tmp / "runs", self.tmp / "absent.json"
        r = subprocess.run([sys.executable, "-m", "lab.sim", *sim_argv(missing, runs, "D"), "--dry-run"], cwd=ROOT,
                           env=lab_env(), capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        run = runs / "sim" / "D"
        self.assertEqual(r.stdout.splitlines(), [
            "dry-run: lab.sim", f"would need: routing file {missing}", f"would write: {run}/labels.json",
            f"would write: {run}/progress.json", f"would write: {run}/scorecard.json",
            f"would write: {run}/edge/site-<site>.ledger.jsonl", f"would write: {run}/work/"])
        self.assertEqual(r.stderr, "")
        self.assertFalse(runs.exists())

    def test_dry_run_with_inputs(self) -> None:
        runs = self.tmp / "runs"
        code, out, err = call_main(lab_sim, [*sim_argv(self.routing, runs, "D"), "--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[0], "dry-run: lab.sim")
        self.assertFalse(any(line.startswith("would need:") for line in out.splitlines()))
        self.assertEqual(len(out.splitlines()), 6)
        self.assertFalse(runs.exists())
        fake = write_routing(self.tmp / "fake.json", "http://127.0.0.1:9/v1", provider="fake")
        code, out, err = call_main(lab_sim, [*sim_argv(fake, runs, "D"), "--dry-run"])
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("error: "), err)
        self.assertFalse(runs.exists())

    def _refused(self, argv: list[str], fragment: str = "") -> str:
        code, out, err = call_main(lab_sim, argv)
        self.assertEqual((code, out), (2, ""), err)
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn(fragment, err)
        return err

    def test_refusals(self) -> None:
        runs = self.tmp / "runs"
        (runs / "sim" / "taken").mkdir(parents=True)
        central = write_routing(self.tmp / "central.json", "http://127.0.0.1:9/v1", boundary="central")
        fake = write_routing(self.tmp / "fake.json", "http://127.0.0.1:9/v1", provider="fake")
        escalating = units.routing_doc({"experiment": "sim"}, ENTRY, "http://127.0.0.1:9/v1")
        escalating["endpoints"]["other"] = dict(escalating["endpoints"]["lab"])
        escalating["routes"][TASK_NAME]["escalate_to"] = "other"
        escalating = write_json(self.tmp / "escalating.json", escalating)
        inside = ROOT / "mycelic" / "lab-sim-never-created"
        cases = [
            (sim_argv(self.routing, runs, "taken"), "run directory already exists"),
            (sim_argv(self.routing, runs, "bad/id"), "--run-id"),
            (sim_argv(self.routing, inside, "R"), "--runs-dir must not lie inside mycelic/"),
            (sim_argv(ROOT / "mycelic" / "routing.json", runs, "R"), "--routing must not lie inside mycelic/"),
            (sim_argv(self.routing, runs, "R", plant="nope"), "--plant must be one of plant_smoke, sim_small"),
            (sim_argv(self.routing, runs, "R", seed=-1), "--seed"),
            (sim_argv(self.routing, runs, "R", weeks=33, eval_to=32), "--weeks must be in [34"),
            (sim_argv(self.routing, runs, "R", weeks=53, eval_to=52), "--weeks must be in [34"),
            (sim_argv(self.routing, runs, "R", eval_from=17), "--eval-from"),
            (sim_argv(self.routing, runs, "R", eval_to=34), "--eval-to"),
            (sim_argv(self.routing, runs, "R", tie_salt="bad salt\t"), "--tie-salt"),
            (sim_argv(self.routing, runs, "R", top_n=0), "--top-n must be in [1, 60]"),
            (sim_argv(self.routing, runs, "R", top_n=61), "--top-n must be in [1, 60]"),
            (sim_argv(self.routing, runs, "R", bootstrap_b=999), "--bootstrap-b must be >= 1000"),
            (sim_argv(self.routing, runs, "R", budget=0), "--budget-seconds"),
            (sim_argv(self.routing, runs, "R", budget=float("nan")), "--budget-seconds"),
            (sim_argv(self.routing, runs, "R") + ["--deadline-seconds=3601"], "--deadline-seconds"),
            (sim_argv(self.routing, runs, "R") + ["--deadline-seconds=0"], "--deadline-seconds"),
            (sim_argv(self.tmp / "absent.json", runs, "R"), "routing"),
            (sim_argv(fake, runs, "R"), "routing"),
            (sim_argv(central, runs, "R"), lab_sim.ROUTING_PROBLEM),
            (sim_argv(escalating, runs, "R"), lab_sim.ROUTING_PROBLEM),
            (sim_argv(self.routing, runs, "R", plant="plant_smoke", eval_from=26), "error: plant: "),
        ]
        for argv, fragment in cases:
            with self.subTest(argv=argv[-6:], fragment=fragment):
                self._refused(argv, fragment)
        self.assertEqual(sorted(p.name for p in (runs / "sim").iterdir()), ["taken"])
        self.assertFalse(inside.exists())

    def test_interrupt_exits_130_and_leaves_a_partial_run(self) -> None:
        runs = self.tmp / "runs"
        with mock.patch.object(lab_sim, "list_models", side_effect=KeyboardInterrupt):
            code, out, err = call_main(lab_sim, sim_argv(self.routing, runs, "I"))
        self.assertEqual((code, out), (130, ""))
        self.assertIn("partial", err)
        self.assertTrue((runs / "sim" / "I" / "labels.json").exists())
        self._refused(sim_argv(self.routing, runs, "I"), "run directory already exists")


# --------------------------------------------------------------------------------------------------- request and plan

class RequestAndPlanTests(unittest.TestCase):
    MANIFEST = load_manifest(MANIFEST_TEST)

    def _error(self, obj: dict[str, Any]) -> tuple[str, str]:
        with self.assertRaises(RequestError) as caught:
            validate(obj, self.MANIFEST)
        return caught.exception.path, caught.exception.problem

    def _sim(self, **block: Any) -> dict[str, Any]:
        obj = plumbing_min()
        obj["experiments"]["sim"] = {**sim_block(), **block}
        return obj

    def test_valid_block_normalised(self) -> None:
        got = validate(self._sim(), self.MANIFEST)["experiments"]["sim"]
        self.assertEqual(got, {"models": ["fake-a", "fake-b"], "minutes": 5, "plant": "sim_small", "weeks": 34,
                               "top_n": 10, "seeds": [1]})
        got = validate(self._sim(models=["fake-b"], seeds=[3, 1], plant="plant_smoke", weeks=52), self.MANIFEST)
        self.assertEqual(got["experiments"]["sim"]["seeds"], [3, 1])
        self.assertEqual(got["experiments"]["sim"]["models"], ["fake-b"])

    def test_kinds(self) -> None:
        self.assertEqual(lab_request.SIM_KINDS, ("gguf", "fake"))
        with mock.patch.object(lab_request, "SIM_KINDS", ("gguf",)):
            self.assertEqual(self._error(self._sim(models=["fake-b"])),
                             ("$.experiments.sim.models[0]", "the simulation runs only gguf or fake models"))
            block = sim_block()
            obj = plumbing_min()
            obj["experiments"]["sim"] = block
            self.assertEqual(self._error(obj), ("$.models[0]", "the simulation runs only gguf or fake models"))
            obj["experiments"]["sim"] = {**block, "models": ["fake-a", "fake-b"]}
            self.assertEqual(self._error(obj)[0], "$.experiments.sim.models[0]")

    def test_refusals(self) -> None:
        cases = [
            (self._sim(plant="nope"), ("$.experiments.sim.plant", "must be one of plant_smoke, sim_small")),
            (self._sim(plant=7), ("$.experiments.sim.plant", "must be a string")),
            (self._sim(plant="<plant>"), ("$.experiments.sim.plant", lab_request.PLACEHOLDER)),
            (self._sim(weeks=33), ("$.experiments.sim.weeks", "must be an int in [34, 52]")),
            (self._sim(weeks=53), ("$.experiments.sim.weeks", "must be an int in [34, 52]")),
            (self._sim(top_n=0), ("$.experiments.sim.top_n", "must be an int in [1, 60]")),
            (self._sim(top_n=61), ("$.experiments.sim.top_n", "must be an int in [1, 60]")),
            (self._sim(seeds=[1, 1]), ("$.experiments.sim.seeds[1]", "duplicate")),
            (self._sim(seeds=[1, 2, 3, 4, 5, 6]), ("$.experiments.sim.seeds", "must be a list of 1 to 5 seeds")),
            (self._sim(seeds=[]), ("$.experiments.sim.seeds", "must be a list of 1 to 5 seeds")),
            (self._sim(seeds=[-1]), ("$.experiments.sim.seeds[0]", "must be an int in [0, 2147483647]")),
            (self._sim(minutes=21), ("$.experiments.sim.minutes",
                                     "exceeds the shard capacity (job_minutes minus the shard overhead)")),
            (self._sim(models=["fake-fail"]), ("$.experiments.sim.models[0]", "not one of $.models")),
        ]
        for obj, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self._error(obj), expected)
        path, problem = self._error(self._sim(plant="plant_smoke", weeks=34))
        self.assertEqual(path, "$.experiments.sim.weeks")
        self.assertEqual(problem, "the plant does not fit these weeks "
                                  f"({lab_sim.plant_problem('plant_smoke', 1, 34)})")

    def test_order(self) -> None:
        unknown = self._sim(speed=1)
        del unknown["experiments"]["sim"]["plant"]
        self.assertEqual(self._error(unknown), ("$.experiments.sim.speed", "unknown key"))
        block = sim_block()
        obj = plumbing_min()
        for key in ("minutes", "plant", "weeks", "top_n", "seeds"):
            obj["experiments"]["sim"] = {k: v for k, v in block.items() if k != key}
            with self.subTest(required=key):
                self.assertEqual(self._error(obj), (f"$.experiments.sim.{key}", "required"))
        obj["experiments"]["sim"] = {"models": ["fake-a"]}
        self.assertEqual(self._error(obj), ("$.experiments.sim.minutes", "required"))
        firsts = [
            (dict(models=["fake-fail"], minutes=0), "$.experiments.sim.models[0]"),
            (dict(minutes=0, plant="nope"), "$.experiments.sim.minutes"),
            (dict(plant="nope", weeks=1), "$.experiments.sim.plant"),
            (dict(weeks=1, top_n=0), "$.experiments.sim.weeks"),
            (dict(top_n=0, seeds=[1, 1]), "$.experiments.sim.top_n"),
            (dict(plant="plant_smoke", weeks=34, seeds=[1, 1]), "$.experiments.sim.seeds[1]"),
        ]
        for changes, path in firsts:
            with self.subTest(changes=changes):
                self.assertEqual(self._error(self._sim(**changes))[0], path)
        obj = plumbing_min()
        obj["experiments"] = {}
        self.assertEqual(self._error(obj), ("$.experiments", "needs at least one of e1, e2, e3, g0, sim, x1, openfda"))

    def test_unit_params(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lab-sim-plan-") as tmp:
            plan, _ = make_plan(Path(tmp), sim_request(["fake-a", "fake-b"], seeds=[2, 1]))
        self.assertEqual([u["unit"] for u in plan["units"]],
                         ["sim-fake-a-s1", "sim-fake-a-s2", "sim-fake-b-s1", "sim-fake-b-s2"])
        for unit in plan["units"]:
            seed = unit["seeds"][0]
            with self.subTest(unit=unit["unit"]):
                self.assertEqual(unit["unit"], f"sim-{unit['model']}-s{seed}")
                self.assertEqual(unit["params"], {
                    "plant": "sim_small", "pack": "device_quality", "weeks": 34,
                    **lab_sim.world_settings(lab_sim.PLANTS["sim_small"], 34), "tie_salt": lab_sim.TIE_SALT,
                    "top_n": 10, "bootstrap_b": lab_sim.BOOTSTRAP_B, "bootstrap_seed": lab_sim.BOOTSTRAP_SEED,
                    "seed": seed})
                self.assertEqual((unit["experiment"], unit["minutes"], unit["kind"]), ("sim", 5, "fake"))
        self.assertEqual(len({u["run_id"] for u in plan["units"]}), 4)

    def test_plumbing_001_plan(self) -> None:
        manifest = load_manifest(LAB_MANIFEST)
        request = load_request(PLUMBING_001, manifest, root=ROOT)
        plan = build_plan(request, manifest, "unknown")
        self.assertEqual([(s["shard"], s["units"], s["planned_minutes"], s["timeout_minutes"])
                          for s in plan["shards"]],
                         [("s001-fake-a", ["sim-fake-a-s1", "e1-fake-a-r1"], 20, 45),
                          ("s002-fake-a", ["e1-fake-a-r2", "e1-fake-a-r3", "e2-fake-a", "e3-fake-a"], 20, 45),
                          ("s003-fake-b", ["g0-fake-b", "e1-fake-b-r1", "e1-fake-b-r2"], 20, 45),
                          ("s004-fake-b", ["e1-fake-b-r3", "e3-fake-b"], 10, 35),
                          ("s005-none", ["x1"], 5, 30)])

    def test_smoke_template(self) -> None:
        manifest = load_manifest(LAB_MANIFEST)
        template = ROOT / "lab" / "templates" / "smoke.json"
        obj = _json(template)
        self.assertIn("lab/requests/smoke-001.json", obj["purpose"])
        with tempfile.TemporaryDirectory(prefix="lab-sim-smoke-") as tmp:
            requests = Path(tmp) / "lab" / "requests"
            requests.mkdir(parents=True)
            shutil.copy(template, requests / "smoke-001.json")
            request = load_request(requests / "smoke-001.json", manifest, strict_location=True, root=Path(tmp))
        plan = build_plan(request, manifest, "unknown")
        (model,) = obj["models"]
        (shard,) = plan["shards"]
        self.assertEqual((shard["kind"], shard["model"], shard["planned_minutes"], shard["timeout_minutes"]),
                         ("gguf", model, 305, 330))
        experiments = {u["unit"]: u["experiment"] for u in plan["units"]}
        self.assertEqual([experiments[u] for u in shard["units"]], ["sim", "g0", "e3"])
        self.assertEqual(plan["result_class"], "real")
        sim = next(u for u in plan["units"] if u["experiment"] == "sim")
        self.assertEqual((sim["minutes"], sim["params"]["plant"], sim["params"]["top_n"], sim["seeds"]),
                         (240, "sim_small", 10, [1]))


# --------------------------------------------------------------------------------------------------- aggregate

def _sim_row(unit: str, digest: str, seed: int = 1) -> dict[str, Any]:
    channel = {"found": 3, "units": 4, "recall": 0.75, "precision_at_40": 0.1, "average_precision": 0.6, "alerts": 12,
               "false_alarms": 8}
    return {"unit": unit, "model": "fake-a", "cpu_model": "cpu", "display_class": "plumbing", "measurement": False,
            "plant": "sim_small", "seed": seed, "weeks": 34, "records": 1031, "world_digest": digest,
            "channels": {name: dict(channel) for name in lab_sim.CHANNELS},
            "lifts": {name: {"estimate": 0.5, "ci_low": 0.25, "ci_high": 0.75} for name, _, _ in lab_sim.LIFTS},
            "pushdown": {"n": 10, "n_true": 4, "supported": 4, "ap_pushdown": 1.0, "ap_stats_only": 0.8},
            "raw_text_crossed": 0, "fallback_share": 0.0,
            "notes": ["synthetic_internal", "lexical_exact", "few_patterns", "r_model_free"]}


class AggregateSimTests(unittest.TestCase):
    def test_world_groups(self) -> None:
        rows = [_sim_row("sim-b-s1", "b" * 64), _sim_row("sim-a-s1", "a" * 64), _sim_row("sim-a-s2", "c" * 64, 2),
                _sim_row("sim-c-s1", "a" * 64)]
        groups = lab_aggregate.sim_world_groups(rows)
        self.assertEqual(groups, [
            {"plant": "sim_small", "seed": 1, "weeks": 34, "digests": ["a" * 64, "b" * 64],
             "units": ["sim-a-s1", "sim-b-s1", "sim-c-s1"], "consistent": False},
            {"plant": "sim_small", "seed": 2, "weeks": 34, "digests": ["c" * 64], "units": ["sim-a-s2"],
             "consistent": True}])
        self.assertEqual(lab_aggregate.sim_world_groups([]), [])

    def _render(self, rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], Path]:
        tmp = Path(tempfile.mkdtemp(prefix="lab-sim-report-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        report = {"kind": "lab_report", "result_class": "plumbing", "contains_measurements": False,
                  "unit_count": len(rows), "shard_count": 1,
                  "units": [{"unit": r["unit"], "experiment": "sim", "model": r["model"], "status": "ok",
                             "display_class": "plumbing", "shard": "s001-fake-a", "wall_s": 12.5} for r in rows],
                  "sim": rows,
                  "sim_sizing": [{"unit": r["unit"], "model": r["model"], "cpu_model": "cpu", "status": "ok",
                                  "display_class": "plumbing", "source": "scorecard", "records_done": 1031,
                                  "records_total": 1031, "extract_record_s_p50": 0.25, "judge_s_p50": 0.5,
                                  "estimate_s": 900.0, "suggested_minutes": 19} for r in rows],
                  "notes": {"sim_world_digest": lab_aggregate.sim_world_groups(rows)}}
        write_json(tmp / "report.json", report)
        md, entries = summary.render_report(tmp)
        return md, entries, tmp

    def test_summary_renders_the_sim_rows(self) -> None:
        rows = [_sim_row("sim-a-s1", "a" * 64), _sim_row("sim-b-s1", "b" * 64)]
        md, entries, root = self._render(rows)
        check_sources(self, md, entries, root)
        self.assertIn(SIM_WORLD_DIFFERS, md)
        self.assertNotIn(SIM_WORLD_SAME, md)
        for key in ("sim", "sim-lifts", "sim-pushdown", "sizing"):
            self.assertIn(HEADINGS[key], md)
        self.assertIn(SIZING_NOTE, md)
        for key, sentence in SIM_NOTES.items():
            self.assertIn(sentence, md, key)
        pointers = {e["pointer"] for e in entries}
        for i in range(len(rows)):
            for name in lab_sim.CHANNELS:
                for key in lab_aggregate.SIM_CHANNEL_FIELDS:
                    self.assertIn(f"/sim/{i}/channels/{name}/{key}", pointers)
            for name, _, _ in lab_sim.LIFTS:
                for key in lab_aggregate.SIM_LIFT_FIELDS:
                    self.assertIn(f"/sim/{i}/lifts/{name}/{key}", pointers)
            for key in ("n", "n_true", "supported", "ap_pushdown", "ap_stats_only"):
                self.assertIn(f"/sim/{i}/pushdown/{key}", pointers)
            for key in ("raw_text_crossed", "fallback_share"):
                self.assertIn(f"/sim/{i}/{key}", pointers)
            for key in ("records_done", "records_total", "extract_record_s_p50", "judge_s_p50", "estimate_s",
                        "suggested_minutes"):
                self.assertIn(f"/sim_sizing/{i}/{key}", pointers)
        styles = {e["pointer"]: (e["style"], e["text"]) for e in entries}
        self.assertEqual(styles["/sim_sizing/0/estimate_s"], ("min1", "15.0"))
        self.assertEqual(styles["/sim_sizing/0/judge_s_p50"], ("f1", "0.5"))
        self.assertEqual(styles["/sim/0/channels/X_model/recall"], ("f3", "0.750"))
        self.assertEqual(styles["/sim/0/fallback_share"], ("f3", "0.000"))
        self.assertEqual(styles["/sim/0/pushdown/n"], ("int", "10"))
        md, entries, root = self._render([_sim_row("sim-a-s1", "a" * 64), _sim_row("sim-b-s1", "a" * 64)])
        self.assertIn(SIM_WORLD_SAME, md)
        self.assertNotIn(SIM_WORLD_DIFFERS, md)
        check_sources(self, md, entries, root)


if __name__ == "__main__":
    unittest.main()
