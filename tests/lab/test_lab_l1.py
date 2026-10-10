"""Latency test L001 (``lab.l1``, the lab's ``l1`` experiment; ``docs/collective/L001/CHOICE-L001.md`` with K1 to
K13): the data and the questions, every judge's path on fresh stores, the timing, the scoring and the headline, the
preregistration and its stops, a unit through the lab's fake model server with its pins, budget and failures, the
units' wiring and warm-up, the aggregate with its re-run rule, the summaries, the guard, the workflow, the request and
the plan. Nothing here measures a model or reads MSHA: every file is ``tests/lab/l1_data.py``'s synthetic one (every
value invented), every server is the lab's fake, and no test reaches a network host (a fetch is always stubbed).

One dry run of the latency template with one model (five units in five shards, ``bootstrap_b`` 1000), through
``lab.dryrun --l1-raw``, is shared by the classes that read it; :class:`~tests.lab.helpers.DryTree` copies of it are
changed and re-sealed for the aggregate's cases.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import math
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import yaml

from lab import aggregate as lab_aggregate
from lab import dryrun as lab_dryrun
from lab import l1 as lab_l1
from lab import l1guard as G
from lab import l1path as P
from lab import l1score as SC
from lab import msha as lab_msha
from lab import notes
from lab import plan as lab_plan
from lab import prereg as lab_prereg
from lab import summary as lab_summary
from lab import units
from lab import warmup as lab_warmup
from lab.manifest import load_manifest
from lab.notes import (L1_CHECK_FAILED, L1_FETCH_FAILED, L1_INCOMPLETE, L1_INFRA, L1_LEFT_OUT, L1_NO_REAGGREGATION,
                       L1_NOT_FINISHED, L1_NOT_MEASURED, L1_RERUN_AFTER_CALLS, L1_STOPPED, PREREG_MISSING)
from lab.request import (L1_QUESTIONS_PROBLEM, MSHA_HOSTED, RequestError, load_request, validate)
from lab.responder import Responder, pack_free_judge
from lab.server import FakeServer
from mycelic.collective.edge.verify import JUDGE_TASK
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps
from tests.lab import l1_data
from tests.lab.helpers import LAB_MANIFEST, ROOT, DryTree, call_main, check_sources, write_json

TEMPLATE = ROOT / "lab" / "templates" / "latency.json"
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "mycelic-lab.yml").read_text(encoding="utf-8"))
MODEL = "a-0p5b"


def l1_request(**block: Any) -> dict[str, Any]:
    doc = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    doc["models"] = [MODEL]
    doc["experiments"]["l1"].update(bootstrap_b=1000, **block)
    return doc


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows_of(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def numbers(obj: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every number in a JSON value, with its path."""
    if isinstance(obj, dict):
        return [x for k, v in obj.items() for x in numbers(v, f"{path}/{k}")]
    if isinstance(obj, list):
        return [x for i, v in enumerate(obj) for x in numbers(v, f"{path}/{i}")]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [(path, obj)]
    return []


def k12(test: unittest.TestCase, obj: Any, skip: tuple[str, ...] = ()) -> None:
    """K12: floats with at most three decimals, integer counts below 100,000."""
    for where, value in numbers(obj):
        if any(where.startswith(s) for s in skip):
            continue
        if isinstance(value, float):
            test.assertEqual(round(value, 3), value, where)
            test.assertLess(abs(value), 100000, where)
        else:
            test.assertLess(abs(value), 100000, where)


# --------------------------------------------------------------------------------------------------- shared fixtures

class _Raw:
    """The synthetic accident file (seed 1) in a raw directory, written once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Raw]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-l1-raw-"))
            cls.writer = l1_data.accident_file(1)
            cls.raw = cls.tmp / "raw"
            l1_data.write_raw(cls.raw, cls.writer)
            cls.secrets = l1_data.secrets(cls.writer)
        return cls


class _Data:
    """``lab.l1path.prepare`` on the synthetic file, made once."""

    data: P.Data | None = None

    @classmethod
    def get(cls) -> P.Data:
        if cls.data is None:
            raw = _Raw.get()
            cls.data = P.prepare(raw.raw, raw.tmp / "work")
        return cls.data


class _Dry:
    """``lab.dryrun --l1-raw`` of the latency template with one model, made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Dry]":
        if cls.tmp is None:
            raw = _Raw.get()
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-l1-dry-"))
            request = write_json(cls.tmp / "latency-001.json", l1_request())
            cls.out = cls.tmp / "D"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cls.code = lab_dryrun.main(["--request", str(request), "--out", str(cls.out),
                                            "--l1-raw", str(raw.raw)])
            cls.stdout = stdout.getvalue()
            cls.plan = read_json(cls.out / "plan" / "plan.json")
            cls.report = read_json(cls.out / "report" / "report.json")
            cls.prereg = read_json(cls.out / "plan" / "prereg" / "l1" / "prereg.json")
            cls.scores = read_json(cls.out / "plan" / "prereg" / "l1" / "scores.json")
            cls.manifest = read_json(cls.out / "plan" / "prereg" / "prereg.json")
            cls.raw = lab_l1.raw_dir(cls.out / "plan")
        return cls

    @classmethod
    def unit(cls, slot: int) -> dict[str, Any]:
        return next(u for u in cls.plan["units"] if u["params"]["slot"] == slot)

    @classmethod
    def run_dir(cls, slot: int) -> Path:
        u = cls.unit(slot)
        return cls.out / "shards" / u["shard"] / "runs" / "l1" / u["run_id"]


# --------------------------------------------------------------------------------------------------- rule 1 to 3

class DataTests(unittest.TestCase):
    """Rule 1 (the demo's pipeline on the file), rule 2 (exactly one alert, K11) and rule 3 (the five questions)."""

    def setUp(self) -> None:
        self.data = _Data.get()

    def test_the_demo_s_steps_on_the_file(self) -> None:
        counts = P.audit_counts(self.data.audit)
        self.assertEqual(counts["mines"], l1_data.MINES)
        self.assertEqual(counts["records"], len(self.data.records))
        self.assertEqual(counts["alerts"], {"X": 1, "S": 0})
        self.assertEqual(self.data.site_ids, [f"m{i:02d}" for i in range(1, l1_data.MINES + 1)])
        self.assertEqual({r["site"] for r in self.data.records}, set(self.data.site_ids))
        # the pack is c1's drafted pack: five specific predicates and the other bucket
        self.assertEqual(len(P.specific_predicates(self.data)), 5)
        self.assertNotIn(self.data.other_predicate, P.specific_predicates(self.data))
        self.assertTrue(self.data.draft["check"]["passed"])

    def test_one_alert_and_five_slots(self) -> None:
        (alert,) = P.alerts(self.data.audit)
        self.assertEqual((alert["channel"], alert["predicate"]), ("X", "slip_or_fall_of_person"))
        specs = P.question_specs(self.data, alert)
        self.assertEqual([s["slot"] for s in specs], [1, 2, 3, 4, 5])
        self.assertEqual([s["kind"] for s in specs], ["alert"] + ["control"] * 4)
        self.assertEqual(specs[0]["predicate"], alert["predicate"])
        self.assertEqual([s["predicate"] for s in specs[1:]],
                         sorted(p for p in P.specific_predicates(self.data) if p != alert["predicate"]))
        for s in specs:     # the same entity, channel and as_of; the window start comes from the alert question
            self.assertEqual((s["entity_type"], s["entity_id"], s["channel"], s["as_of"], s["window_start"]),
                             (specs[0]["entity_type"], specs[0]["entity_id"], "X", alert["available_date"], None))
        windowed = P.with_window(specs, {"start_week": "2023-W24", "end_week": "2023-W31"})
        self.assertEqual({s["window_start"] for s in windowed}, {"2023-W24"})

    def test_not_exactly_four_other_predicates_stops(self) -> None:
        (alert,) = P.alerts(self.data.audit)
        specific = P.specific_predicates(self.data)
        for got in ([p for p in specific if p != "machinery"], specific + ["extra"]):
            with self.subTest(n=len(got)), mock.patch.object(P, "specific_predicates", return_value=got):
                with self.assertRaises(P.L1Error) as caught:
                    P.question_specs(self.data, alert)
                self.assertEqual(caught.exception.code, "questions")


# --------------------------------------------------------------------------------------------------- rules 4 and 5

class _Capture:
    """A judge answering as the pack-free plumbing judge does, keeping every payload it was sent."""

    def __init__(self, sleep_s: float = 0.0) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.sleep_s = sleep_s

    def __call__(self, payload: dict[str, Any]) -> dict[str, str]:
        self.payloads.append(payload)
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return pack_free_judge(payload)


class PathTests(unittest.TestCase):
    """One judge on one question through the product's path (rules 4 to 6, K1, K2, K4, K6)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Data.get()
        (alert,) = P.alerts(cls.data.audit)
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-l1-path-"))
        specs = P.question_specs(cls.data, alert)
        probe, _ = P.run_path(cls.data, specs[0], "probe", cls.tmp / "probe")
        cls.specs = P.with_window(specs, probe.window)
        cls.probe = probe

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, True)

    def runtimes(self, judge: Callable[[dict[str, Any]], dict[str, str]]) -> dict[str, P.FakeRuntime]:
        return {sid: P.FakeRuntime(sid, judge) for sid in self.data.site_ids}

    def test_the_codes_are_hidden_from_every_judge_but_the_key(self) -> None:
        seen = _Capture()
        result, _ = P.run_path(self.data, self.specs[0], "model", self.tmp / "m", runtimes=self.runtimes(seen))
        self.assertTrue(seen.payloads)
        self.assertEqual({tuple(p["record"]["codes"]) for p in seen.payloads}, {()})
        keyed: list[dict[str, Any]] = []
        original = P.key_answer

        def spy(pack: Any) -> Callable[[dict[str, Any]], dict[str, str]]:
            inner = original(pack)
            return lambda payload: (keyed.append(payload), inner(payload))[1]

        with mock.patch.object(P, "key_answer", spy):
            P.run_path(self.data, self.specs[0], "key", self.tmp / "k")
        self.assertTrue(any(p["record"]["codes"] for p in keyed))
        # the hidden store's records are the unchanged store's, in the same order (rule 4)
        result, world = P.run_path(self.data, self.specs[0], "probe", self.tmp / "p", keep_world=True)
        try:
            check = P.retrieval_check(self.data, world, self.specs[0], result.window, result.routes)
        finally:
            world.close()
        self.assertTrue(check["same"])
        self.assertEqual(sum(check["records"].values()), len(seen.payloads))

    def test_each_question_goes_to_its_own_key_s_mines(self) -> None:
        """K1: the routes are the question's own; every judge's path gives the probe's question id and routes."""
        result, _ = P.run_path(self.data, self.specs[1], "lexical", self.tmp / "l1")
        probe1, _ = P.run_path(self.data, self.specs[1], "probe", self.tmp / "p1")
        self.assertEqual((result.question_id, result.routes), (probe1.question_id, probe1.routes))
        self.assertNotEqual(probe1.routes, self.probe.routes)
        self.assertLessEqual(sum(1 for r in probe1.routes.values() if r == "sibling"), 2)

    def test_fresh_stores_for_every_path(self) -> None:
        """K4: a path that follows another on the same question still judges every record (no stored verdict)."""
        P.run_path(self.data, self.specs[0], "lexical", self.tmp / "same")
        first = _Capture()
        a, _ = P.run_path(self.data, self.specs[0], "model", self.tmp / "same", runtimes=self.runtimes(first))
        second = _Capture()
        b, _ = P.run_path(self.data, self.specs[0], "model", self.tmp / "same", runtimes=self.runtimes(second))
        self.assertEqual(len(first.payloads), len(second.payloads))
        self.assertEqual(a.calls, b.calls)
        self.assertGreater(sum(a.calls.values()), 0)

    def test_the_judges_answer_by_their_rules(self) -> None:
        """Rule 5 and K2: the key reads the filed code; the control and the baseline never read the record."""
        pack = self.data.pack
        code, info = next((c, i) for c, i in pack.codes.items())
        q = {"predicate": info.predicate}
        key = P.key_answer(pack)
        self.assertEqual(key({"question": q, "record": {"codes": [code]}}),
                         {"mentions_entity": "yes", "describes_predicate": "yes"})
        self.assertEqual(key({"question": {"predicate": "other"}, "record": {"codes": [code]}})["describes_predicate"],
                         "no")
        self.assertEqual(key({"question": q, "record": {"codes": []}})["describes_predicate"], "no")
        self.assertEqual(P.record_blind_answer(True)({}), {"mentions_entity": "yes", "describes_predicate": "yes"})
        self.assertEqual(P.record_blind_answer(False)({}), {"mentions_entity": "yes", "describes_predicate": "no"})
        self.assertEqual(P.route_role_answer("contributing")({})["describes_predicate"], "yes")
        self.assertEqual(P.route_role_answer("sibling")({})["describes_predicate"], "no")

    def test_one_mine_after_another_and_the_stages_sum(self) -> None:
        """Rule 6: sites in sorted order, one at a time; build + mines + gaps + gate is the time to answer."""
        result, _ = P.run_path(self.data, self.specs[0], "model", self.tmp / "t", runtimes=self.runtimes(_Capture()))
        t = result.timing
        self.assertEqual([m["mine"] for m in t["mines"]], sorted(result.routes))
        for before, after in zip(t["mines"], t["mines"][1:]):
            self.assertLessEqual(before["end"], after["start"])
        self.assertFalse(any(m["contended"] or m["timed_out"] for m in t["mines"]))
        total = t["question_build_s"] + sum(m["seconds"] for m in t["mines"]) + sum(t["between_s"]) + t["gate_s"]
        self.assertAlmostEqual(total, t["time_to_answer_s"], places=9)
        self.assertEqual(len(t["between_s"]), len(t["mines"]) - 1)
        self.assertLessEqual(P.at_once(result), t["time_to_answer_s"])
        derived = P.default_deadline(self.data.pack, result, self.specs[0]["as_of"])
        self.assertEqual((derived["status"], derived["timeouts"]), (result.status_first, 0))

    def test_a_control_question_is_timed_from_its_call(self) -> None:
        """Rule 6: the time to answer runs from the call to ``verify_candidate``; the constructed candidate (an HQ
        cell query) is built before the clock starts."""
        built: list[float] = []
        original = P.constructed_candidate

        def slow(*args: Any, **kwargs: Any) -> Any:
            out = original(*args, **kwargs)
            time.sleep(0.3)
            built.append(time.perf_counter())
            return out

        with mock.patch.object(P, "constructed_candidate", slow):
            result, _ = P.run_path(self.data, self.specs[1], "lexical", self.tmp / "control")
        t = result.timing
        (done,) = built
        started = t["mines"][0]["start"] - t["question_build_s"]           # the clock's start, t0
        self.assertGreaterEqual(started, done - 1e-6)
        self.assertAlmostEqual(t["question_build_s"] + sum(m["seconds"] for m in t["mines"]) + sum(t["between_s"])
                               + t["gate_s"], t["time_to_answer_s"], places=9)

    def test_a_mine_that_times_out(self) -> None:
        """K6: the mine is unknown (HQ's timeout) at the first decision, ends at its deadline, the next mines are
        contended, and its late verdict is taken in after the call; the derived default deadline counts it."""
        slow = _Capture(sleep_s=0.06)
        result, _ = P.run_path(self.data, self.specs[0], "model", self.tmp / "slow", runtimes=self.runtimes(slow),
                               deadline_s=0.25, late_wait_s=30)
        t = result.timing
        late = [m for m in t["mines"] if m["timed_out"]]
        self.assertTrue(late)
        first = late[0]
        self.assertAlmostEqual(first["seconds"], 0.25, places=6)
        self.assertEqual((result.first[first["mine"]]["verdict"], result.first[first["mine"]]["reason"]),
                         ("unknown", "timeout"))
        after = [m for m in t["mines"] if m["start"] > first["start"]]
        self.assertTrue(after and after[0]["contended"])           # its late thread still shares the server
        self.assertNotEqual(result.final[first["mine"]]["verdict"], "unknown")
        self.assertIsNotNone(result.time_to_final_s)
        self.assertGreaterEqual(result.time_to_final_s, t["time_to_answer_s"])
        derived = P.default_deadline(self.data.pack, result, self.specs[0]["as_of"], deadline_s=0.05)
        self.assertGreaterEqual(derived["timeouts"], 1)

    def test_the_stages_by_hand(self) -> None:
        calls = [{"site": "m01", "start": 1.0, "end": 2.0}, {"site": "m02", "start": 2.5, "end": 9.0},
                 {"site": "m03", "start": 5.5, "end": 6.0}]
        s = P.stages(0.5, 7.0, calls, deadline_s=3.0)
        self.assertEqual([m["timed_out"] for m in s["mines"]], [False, True, False])
        self.assertEqual([m["contended"] for m in s["mines"]], [False, False, True])
        self.assertEqual([m["seconds"] for m in s["mines"]], [1.0, 3.0, 0.5])
        self.assertEqual((s["question_build_s"], s["between_s"], s["gate_s"]), (0.5, [0.5, 0.0], 1.0))
        self.assertEqual(s["time_to_answer_s"], 6.5)


# --------------------------------------------------------------------------------------------------- rules 7 to 10

def _rows(spec: list[tuple[str, int, str, str, str]]) -> list[dict[str, Any]]:
    """(mine, slot, stratum, key, model) rows."""
    return [{"mine": m, "slot": s, "stratum": st, "key": k, "model": v} for m, s, st, k, v in spec]


class ScoringTests(unittest.TestCase):
    def test_site_answers_and_what_is_scored(self) -> None:
        questions = [{"slot": 1, "routes": {"m01": "contributing", "m02": "sibling"},
                      "strata": {"m01": "codes", "m02": "sibling"}}]
        rows = SC.site_answers(questions, {"model": {"1": {"m01": "confirm"}}}, {"1": {"m01": "confirm",
                                                                                      "m02": "unknown"}})
        self.assertEqual(rows, [{"slot": 1, "mine": "m01", "stratum": "codes", "key": "confirm", "model": "confirm"},
                                {"slot": 1, "mine": "m02", "stratum": "sibling", "key": "unknown", "model": None}])
        self.assertEqual(SC.scored(rows), rows[:1])          # the key's unknown (no records) is not scored

    def test_unknown_is_never_correct(self) -> None:
        rows = _rows([("m01", 1, "codes", "confirm", "unknown"), ("m02", 1, "sibling", "refute", "unknown")])
        d = SC.Drawn(rows, ["model", "key"], ["m01", "m02"], SC.draws(["m01", "m02"], 50, "l1:1"))
        s = d.score("model")
        self.assertEqual((s["sensitivity"]["value"], s["specificity"]["value"], s["balanced_accuracy"]["value"],
                          s["unknown_share"]["value"]), (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(d.score("key")["balanced_accuracy"]["value"], 1.0)

    def test_the_draws_are_rule_8_s(self) -> None:
        mines = ["m01", "m02", "m03"]
        rng = random.Random("l1:7")
        self.assertEqual(SC.draws(mines, 4, SC.seed_of(7)), [[rng.randrange(3) for _ in range(3)] for _ in range(4)])
        self.assertEqual(SC.seed_of(1), "l1:1")
        self.assertEqual(SC.record_seed_of(1), "l1:1:records")

    def test_a_mine_s_answers_are_drawn_together_and_empty_draws_withhold(self) -> None:
        rows = _rows([("m01", 1, "codes", "confirm", "confirm"), ("m01", 2, "codes", "confirm", "confirm"),
                      ("m02", 1, "codes", "confirm", "refute"), ("m03", 1, "sibling", "refute", "refute")])
        d = SC.Drawn(rows, ["model"], ["m01", "m02", "m03"], [[0, 0, 0], [0, 1, 2], [2, 2, 2]])
        self.assertEqual(d.sums["model"], [[6, 0, 6, 0, 0, 6], [3, 1, 2, 1, 0, 4], [0, 3, 0, 3, 0, 3]])
        s = d.score("model")
        self.assertEqual(s["balanced_accuracy"]["empty_draws"], 2)
        self.assertTrue(s["balanced_accuracy"]["withheld"])        # 2 of 3 draws empty, above 5%
        empty = SC.empty_draw_share(rows, 1000, "l1:1")
        self.assertEqual((empty["mines"], empty["draws"]), (3, 1000))
        self.assertTrue(empty["stops"])
        many = _rows([(f"m{i:02d}", 1, "codes" if i % 2 else "sibling", "confirm" if i % 2 else "refute", "confirm")
                      for i in range(1, 41)])
        self.assertFalse(SC.empty_draw_share(many, 1000, "l1:1")["stops"])

    def test_the_paired_differences_on_the_same_draws(self) -> None:
        rows = [{"mine": f"m{i:02d}", "slot": 1, "stratum": "codes" if i % 2 else "sibling",
                 "key": "confirm" if i % 2 else "refute", "model": "confirm" if i % 2 else "refute",
                 "lexical": "confirm", "route_role": "confirm" if i % 2 else "refute"} for i in range(1, 21)]
        scores, d = SC.score_judges(rows, ["model", "lexical", "route_role"], b=500, seed="l1:1")
        self.assertEqual(scores["model"]["balanced_accuracy"]["value"], 1.0)
        self.assertEqual(scores["lexical"]["balanced_accuracy"]["value"], 0.5)
        lex, route = d.paired("model", "lexical"), d.paired("model", "route_role")
        self.assertEqual((lex["value"], lex["ci_low"], lex["ci_high"]), (0.5, 0.5, 0.5))
        self.assertEqual((route["value"], route["ci_low"], route["ci_high"]), (0.0, 0.0, 0.0))

    def test_the_predicate_only_bound_by_hand(self) -> None:
        rows = _rows([("m01", 1, "codes", "confirm", "x"), ("m02", 1, "codes", "confirm", "x"),
                      ("m03", 1, "sibling", "refute", "x"), ("m01", 2, "codes", "confirm", "x"),
                      ("m02", 2, "sibling", "refute", "x"), ("m03", 2, "sibling", "refute", "x")])
        # P=3, N=3: question 1 max(2/3, 1/3), question 2 max(1/3, 2/3); half the sum is 2/3
        self.assertEqual(SC.predicate_bound(rows)["value"], 0.667)
        self.assertIsNone(SC.predicate_bound(rows[:2])["value"])

    def test_the_headline_at_its_boundaries(self) -> None:
        def iv(lo: float, hi: float, withheld: bool = False) -> dict[str, Any]:
            return {"ci_low": lo, "ci_high": hi, "withheld": withheld}

        cases = [((iv(0.01, 0.2), iv(0.01, 0.2)), "better"),
                 ((iv(0.01, 0.2), iv(0.0, 0.2)), "better_than_lexical_only"),
                 ((iv(-0.3, -0.01), iv(-0.3, 0.1)), "worse"), ((iv(0.0, 0.2), iv(0.1, 0.2)), "not_told_apart"),
                 ((iv(-0.2, 0.0), iv(-0.2, 0.0)), "not_told_apart")]
        for (lex, route), want in cases:
            with self.subTest(want=want):
                self.assertEqual(SC.headline(finished=True, measured=True, left_out_share=0.0, d_lex=lex,
                                             d_route=route), (want, None))
        # K3: a model one of whose units did not finish gets "no verdict", with its reason
        self.assertEqual(SC.headline(finished=False, measured=True, left_out_share=0.0, d_lex=iv(1, 1),
                                     d_route=iv(1, 1)), ("no_verdict", "incomplete"))
        self.assertEqual(SC.headline(finished=False, measured=False, left_out_share=None, d_lex=None, d_route=None),
                         ("no_verdict", "incomplete"))
        self.assertEqual(SC.headline(finished=True, measured=False, left_out_share=0.0, d_lex=iv(1, 1),
                                     d_route=iv(1, 1)), (None, "not_measured"))
        self.assertEqual(SC.headline(finished=True, measured=True, left_out_share=0.051, d_lex=iv(1, 1),
                                     d_route=iv(1, 1)), ("no_verdict", "left_out"))
        self.assertEqual(SC.headline(finished=True, measured=True, left_out_share=0.05, d_lex=iv(0, 0, True),
                                     d_route=iv(1, 1)), ("no_verdict", "withheld"))

    def test_the_gate_s_agreement_beside_a_constant_status(self) -> None:
        key = {"1": "contested", "2": "hypothesis", "3": "hypothesis", "4": "hypothesis", "5": "supported"}
        model = {"1": "hypothesis", "2": "hypothesis", "3": "supported", "4": "hypothesis", "5": "supported"}
        g = SC.gate_agreement({"model": model}, key)
        self.assertEqual((g["questions"], g["constant"]), (5, 3))
        self.assertEqual(g["key_statuses"], {"contested": 1, "hypothesis": 3, "supported": 1})
        self.assertEqual(g["judges"]["model"], {"equal": 3, "supported_agrees": 4})

    def test_the_strata_and_the_construction_counts(self) -> None:
        rows = [{"mine": "m01", "slot": 1, "stratum": "codes", "key": "confirm", "lexical": "refute"},
                {"mine": "m02", "slot": 1, "stratum": "text_only", "key": "refute", "lexical": "confirm"},
                {"mine": "m03", "slot": 1, "stratum": "sibling", "key": "refute", "lexical": "refute"},
                {"mine": "m04", "slot": 1, "stratum": "sibling", "key": "unknown", "lexical": "unknown"}]
        c = SC.construction(rows, {"1": {"m01": 3, "m02": 2, "m03": 1, "m04": 0}}, {"1": []})
        self.assertEqual(c, {"key_not_confirm_codes": 0, "key_not_refute_text_only": 0, "key_not_refute_sibling": 0,
                             "lexical_not_confirm_text_only": 0, "lexical_not_refute_sibling": 0,
                             "lexical_confirm_codes_only": 0, "lexical_refute_codes_only": 1})
        s = SC.by_stratum(rows, ["lexical"], b=200, seed="l1:1", mines=["m01", "m02", "m03"])
        self.assertEqual(s["sibling"]["lexical"]["verdicts"], {"confirm": 0, "refute": 1, "unknown": 1, "none": 0})
        self.assertEqual((s["sibling"]["lexical"]["scored"], s["sibling"]["lexical"]["equal_key"]), (1, 1))
        self.assertEqual(s["codes"]["lexical"]["sensitivity"]["value"], 0.0)
        self.assertEqual(s["text_only"]["lexical"]["specificity"]["value"], 0.0)

    def test_per_record_measures(self) -> None:
        """Rule 10: a transport failure is left out, a model failure is unknown, the verdict is decide's."""
        def j(m: str | None, d: str | None, err: str | None = None) -> dict[str, Any]:
            return {"mentions_entity": m, "describes_predicate": d, "error_kind": err}

        lines = [{"slot": 1, "mine": "m01", "index": 0, "positive": True, "model": j("yes", "yes")},
                 {"slot": 1, "mine": "m01", "index": 1, "positive": False, "model": j("yes", "no")},
                 {"slot": 1, "mine": "m02", "index": 0, "positive": False, "model": j(None, None, "timeout")},
                 {"slot": 1, "mine": "m02", "index": 1, "positive": True, "model": j(None, None, "schema_invalid")},
                 {"slot": 1, "mine": "m02", "index": 2, "positive": False, "model": j("unclear", "no")}]
        out = SC.per_record(lines, ["model"], b=200, seed="l1:1:records")
        m = out["model"]
        self.assertEqual((m["judged"], m["left_out"], m["positives"], m["negatives"]), (4, 1, 2, 2))
        self.assertEqual((m["sensitivity"]["value"], m["specificity"]["value"], m["unknown_share"]["value"]),
                         (0.5, 0.5, 0.5))
        self.assertEqual(m["pairs"], {"negative": {"unclear_no": 1, "yes_no": 1},
                                      "positive": {"failed": 1, "yes_yes": 1}})

    def test_the_latency_figures(self) -> None:
        q = [{"slot": 1, "finished": True, "calls": 4, "time_to_answer_s": 10.0, "question_build_s": 0.5,
              "gate_s": 0.25, "between_s": [0.1],
              "mines": [{"seconds": 4.0, "contended": False}, {"seconds": 5.0, "contended": True}]},
             {"slot": 2, "finished": True, "calls": 2, "time_to_answer_s": 20.0, "question_build_s": 0.5,
              "gate_s": 0.25, "between_s": [], "mines": [{"seconds": 19.0, "contended": False}]},
             {"slot": 3, "finished": False}]
        lat = SC.latency(q, {1: [1.0, 2.0, 3.0], 2: [4.0], 3: [99.0]})
        self.assertEqual((lat["questions"], lat["not_finished"], lat["contended_questions"]), (2, 1, 1))
        # K6: a contended question's time to answer is apart, never pooled with the others
        self.assertEqual(lat["time_to_answer_s"], {"n": 1, "median": 20.0, "p95": 20.0})
        self.assertEqual(lat["time_to_answer_s_contended"], {"n": 1, "median": 10.0, "p95": 10.0})
        self.assertEqual(lat["mine_s"]["n"], 2)                    # the contended mine is apart
        self.assertEqual(lat["mine_s_contended"], {"n": 1, "median": 5.0, "p95": 5.0})
        self.assertEqual(lat["judge_call_s"]["n"], 4)              # the unfinished question's calls are not pooled
        # the figures that pool a contended question's parts with the others say so
        self.assertEqual(lat["pooled_with_contended"], ["between_mines_s", "gate_s", "judge_call_s", "model_calls",
                                                        "question_build_s"])
        alert = SC.latency(q[:1], {1: [1.0]})
        self.assertEqual(alert["time_to_answer_s"], {"n": 0, "median": None, "p95": None})
        self.assertEqual(alert["time_to_answer_s_contended"], {"n": 1, "median": 10.0, "p95": 10.0})
        quiet = SC.latency(q[1:], {2: [4.0]})
        self.assertEqual((quiet["time_to_answer_s"], quiet["pooled_with_contended"]),
                         ({"n": 1, "median": 20.0, "p95": 20.0}, []))
        # rule 11: an unfinished question's elapsed time is kept as a lower bound, by slot
        bounded = SC.latency([q[1], {"slot": 3, "finished": False, "elapsed_lower_bound_s": 17.5}], {2: [4.0]})
        self.assertEqual(bounded["not_finished_lower_bounds"], [{"slot": 3, "elapsed_lower_bound_s": 17.5}])
        self.assertEqual(lat["not_finished_lower_bounds"], [{"slot": 3, "elapsed_lower_bound_s": None}])

    def test_an_interval_at_exactly_one_in_twenty_empty_draws(self) -> None:
        """Rule 8 and K7: the interval is withheld only when the empty draws are more than 5%."""
        values = [0.1 * (i % 10) for i in range(95)]
        self.assertFalse(SC._interval(values, 100, 5)["withheld"])
        self.assertTrue(SC._interval(values[:94], 100, 6)["withheld"])


# --------------------------------------------------------------------------------------------------- prereg

class PreregTests(unittest.TestCase):
    """The plan job's L1 step (K's order), on the dry run's plan, and its stops."""

    def setUp(self) -> None:
        self.dry = _Dry.get()

    def test_the_preregistration(self) -> None:
        doc, scores = self.dry.prereg, self.dry.scores
        self.assertEqual(sorted(doc), sorted(lab_l1.PREREG_KEYS))
        self.assertEqual((doc["kind"], doc["operator"], doc["slots"], doc["deadline_seconds"],
                          doc["endpoint_deadline_s"], doc["max_retries"], doc["demo_seed"], doc["data_label"]),
                         ("lab_l1_prereg", "c1", 5, 3600.0, 600, 1, 1, "public"))
        self.assertEqual((doc["bootstrap_b"], doc["bootstrap_seed"], doc["withhold_share"]), (1000, 1, 0.05))
        self.assertFalse(doc["input"]["is_demo"])                 # a synthetic file is not the demo's
        self.assertIsNone(doc["demo_figures"])
        self.assertEqual(doc["input"]["sha256"], hashlib.sha256((self.dry.raw / "Accidents.zip").read_bytes())
                         .hexdigest())
        self.assertEqual([q["slot"] for q in doc["questions"]], [1, 2, 3, 4, 5])
        for q in doc["questions"]:
            with self.subTest(slot=q["slot"]):
                self.assertEqual(set(q["routes"]), set(q["strata"]))
                self.assertEqual(set(q["routes"]), set(q["records"]))
                for mine, role in q["routes"].items():
                    self.assertEqual(role == "sibling", q["strata"][mine] == "sibling")
                self.assertLessEqual(sum(1 for r in q["routes"].values() if r == "sibling"), 2)
                for judge in P.JUDGES:
                    entry = doc["judges"][judge][str(q["slot"])]
                    self.assertEqual(set(entry["verdicts"]), set(q["routes"]))
                    self.assertIn(entry["status"], ("supported", "contested", "hypothesis", "refuted",
                                                    "insufficient"))
        self.assertEqual(doc["endpoints"][0]["name"], MODEL)
        self.assertEqual(doc["task"]["name"], JUDGE_TASK)
        self.assertEqual(doc["code_hash"], lab_l1.code_hash())
        self.assertEqual(sorted(scores["judges"]), sorted(P.JUDGES))
        self.assertEqual(scores["judges"]["key"]["balanced_accuracy"]["value"], 1.0)
        self.assertEqual(scores["draws"], doc["draws"])
        self.assertFalse(doc["draws"]["stops"])
        warm = read_json(self.dry.out / "plan" / "prereg" / "l1" / "warmup.json")
        self.assertEqual((warm["kind"], warm["predicate"]), ("lab_l1_warmup", _Data.get().other_predicate))
        self.assertNotIn(warm["predicate"], [q["predicate"] for q in doc["questions"]])

    def test_the_route_role_baseline_and_the_lexical_judge_by_construction(self) -> None:
        """K2: on these data the key confirms exactly at the codes contributors and refutes elsewhere; the baseline
        equals it except at the text-only contributors; the construction counts are zero for the key."""
        doc, scores = self.dry.prereg, self.dry.scores
        for q in doc["questions"]:
            key = doc["judges"]["key"][str(q["slot"])]["verdicts"]
            route = doc["judges"]["route_role"][str(q["slot"])]["verdicts"]
            for mine, stratum in q["strata"].items():
                if q["records"][mine] == 0:
                    continue
                self.assertEqual(key[mine], "confirm" if stratum == "codes" else "refute", (q["slot"], mine))
                self.assertEqual(route[mine] == key[mine], stratum != "text_only", (q["slot"], mine))
        c = scores["construction"]
        self.assertEqual((c["key_not_confirm_codes"], c["key_not_refute_text_only"], c["key_not_refute_sibling"]),
                         (0, 0, 0))
        self.assertTrue(any(v == "text_only" for q in doc["questions"] for v in q["strata"].values()))

    def test_the_manifest_block_and_the_console(self) -> None:
        block = self.dry.manifest["l1"]
        self.assertEqual(block["cache_key"], "lab-msha-" + self.dry.prereg["input"]["sha256"][:16])
        self.assertEqual(block["cache_key"], lab_prereg.l1_cache_key(self.dry.prereg["input"]["sha256"]))
        self.assertEqual([q["counts"] for q in block["questions"]],
                         [lab_l1.question_counts(q) for q in self.dry.prereg["questions"]])
        self.assertIn("prereg: e1 no x1 no e2 no j1 no l1 yes", self.dry.stdout.splitlines())
        logs = self.dry.out / "plan" / "prereg-logs"
        self.assertTrue((logs / "l1-prereg.stdout.log").is_file())
        self.assertEqual((logs / "l1-prereg.stdout.log").read_text(encoding="utf-8").splitlines(),
                         ["l1 prereg: questions 5 mines 8 file_is_demo false"])
        self.assertFalse((self.dry.out / "plan" / lab_l1.STOP_FILE).exists())

    def test_the_cache_key_for_the_workflow(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lab-l1-key-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "github-output"
        code, stdout, _ = call_main(lab_msha, ["cache-key", "--plan", str(self.dry.out / "plan" / "plan.json"),
                                               "--github-output", str(out)])
        self.assertEqual((code, stdout), (0, "msha cache-key: yes\n"))
        self.assertEqual(out.read_text(encoding="utf-8"), f"l1_cache_key={self.dry.manifest['l1']['cache_key']}\n")
        other = write_json(tmp / "plan.json", {"units": [{"experiment": "j1"}]})
        code, stdout, _ = call_main(lab_msha, ["cache-key", "--plan", str(other), "--github-output", str(out)])
        self.assertEqual((code, stdout), (0, "msha cache-key: none\n"))
        self.assertTrue(out.read_text(encoding="utf-8").endswith("l1_cache_key=\n"))

    def plan_dir(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="lab-l1-stop-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copy(self.dry.out / "plan" / "plan.json", tmp / "plan.json")
        return tmp

    def stop(self, plan: Path, **patches: Any) -> tuple[int, Any]:
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                owner, attr = name.split("__")
                stack.enter_context(mock.patch.object({"P": P, "SC": SC, "L": lab_l1}[owner], attr, value))
            with contextlib.redirect_stdout(io.StringIO()):
                code = lab_l1.preregister(plan / "plan.json", _Raw.get().raw, plan / "work")
        stop = plan / lab_l1.STOP_FILE
        return code, read_json(stop) if stop.exists() else None

    def test_the_stops_before_any_path(self) -> None:
        data = _Data.get()
        (alert,) = P.alerts(data.audit)
        demo = {"file": "Accidents.zip", "bytes": (_Raw.get().raw / "Accidents.zip").stat().st_size,
                "sha256": hashlib.sha256((_Raw.get().raw / "Accidents.zip").read_bytes()).hexdigest(),
                "figures": {**P.audit_counts(data.audit), "records": 1}}
        for patches, want in (({"P__alerts": mock.Mock(return_value=[])}, "no_alert"),
                              ({"P__alerts": mock.Mock(return_value=[alert, alert])}, "alerts"),
                              ({"P__specific_predicates": mock.Mock(return_value=["a", "b"])}, "questions"),
                              ({"L__demo_input": mock.Mock(return_value=demo)}, "demo_figures")):
            with self.subTest(stop=want):
                plan = self.plan_dir()
                code, stop = self.stop(plan, **patches)
                self.assertEqual((code, stop["stop"], stop["kind"]), (2, want, "lab_l1_stop"))
                self.assertFalse((plan / "prereg" / "l1").exists())
                self.assertIn(want, lab_l1.STOPS)

    def test_the_stops_after_the_paths(self) -> None:
        """A retrieval that differs stops at the first question; a draw share above 5% after every judge."""
        plan = self.plan_dir()
        code, stop = self.stop(plan, P__retrieval_check=mock.Mock(return_value={"same": False, "records": {},
                                                                                 "positives": {}}))
        self.assertEqual((code, stop["stop"]), (2, "retrieval"))
        plan = self.plan_dir()
        code, stop = self.stop(plan, SC__empty_draw_share=mock.Mock(return_value={
            "draws": 1000, "empty": 107, "share": 0.107, "stops": True, "mines": 8}))
        self.assertEqual((code, stop["stop"], stop["counts"]), (2, "draws", {"share": 0.107, "draws": 1000,
                                                                               "empty": 107}))
        self.assertFalse((plan / "prereg" / "l1").exists())

    def test_a_judge_s_other_routes_stop_the_plan(self) -> None:
        """K1 and K4: every judge's path must give the probe's question id and routes, else the plan job stops."""
        original = P.run_path
        for change in ("routes", "question_id"):
            def other(data: Any, spec: Any, judge: str, workdir: Path, **kwargs: Any) -> Any:
                result, world = original(data, spec, judge, workdir, **kwargs)
                if judge == "lexical" and change == "routes":
                    result.routes = {**result.routes, sorted(result.routes)[0]: "sibling"
                                     if result.routes[sorted(result.routes)[0]] != "sibling" else "contributing"}
                if judge == "record_blind" and change == "question_id":
                    result.question_id = "q-other"
                return result, world

            with self.subTest(change=change):
                plan = self.plan_dir()
                code, stop = self.stop(plan, P__run_path=other)
                self.assertEqual((code, stop["stop"]), (2, "routes"))
                self.assertFalse((plan / "prereg" / "l1").exists())

    def test_the_plan_job_s_reading_of_the_step(self) -> None:
        """``lab.prereg`` refuses the plan with the stop's code, or with the fetch sentence on exit 3."""
        plan = self.plan_dir()
        steps = lab_prereg._Steps(plan)
        units_ = [u for u in self.dry.plan["units"] if u["experiment"] == "l1"]
        write_json(plan / lab_l1.STOP_FILE, {"stop": "draws"})
        with mock.patch.object(lab_prereg._Steps, "run", return_value=2):
            with self.assertRaises(lab_prereg._StepFailed) as caught:
                lab_prereg._l1(steps, self.dry.plan, units_, plan, None)
        self.assertEqual((caught.exception.path, caught.exception.problem),
                         ("$.experiments.l1", L1_STOPPED.format(stop="draws")))
        with mock.patch.object(lab_prereg._Steps, "run", return_value=lab_l1.EXIT_INFRA):
            with self.assertRaises(lab_prereg._StepFailed) as caught:
                lab_prereg._l1(steps, self.dry.plan, units_, plan, None)
        self.assertEqual(caught.exception.problem, L1_FETCH_FAILED)
        (plan / lab_l1.STOP_FILE).write_text('{"stop": "a value"}', encoding="utf-8")
        with mock.patch.object(lab_prereg._Steps, "run", return_value=2):
            with self.assertRaises(lab_prereg._StepFailed) as caught:
                lab_prereg._l1(steps, self.dry.plan, units_, plan, None)
        self.assertEqual(caught.exception.problem, L1_STOPPED.format(stop="unknown"))


# --------------------------------------------------------------------------------------------------- a unit

class RunTests(unittest.TestCase):
    """``lab.l1 run`` in this process against the lab's fake server (a responder with no pack)."""

    def setUp(self) -> None:
        self.dry = _Dry.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-l1-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prereg_path = self.dry.out / "plan" / "prereg" / "l1" / "prereg.json"

    def server(self, responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> FakeOpenAIServer:
        server = FakeOpenAIServer("valid", responder=responder or Responder(None, pack_free_judge=True)).start()
        self.addCleanup(server.stop)
        return server

    def routing(self, base_url: str, change: Callable[[dict[str, Any]], None] | None = None) -> Path:
        sites = self.tmp / "sites"
        sites.mkdir(exist_ok=True)
        for mine in self.dry.prereg["mines"]:
            doc = lab_l1.routing_doc(self.dry.plan["models"], MODEL, base_url, mine)
            if change is not None:
                change(doc)
            write_json(sites / f"{mine}.json", doc)
        return sites

    def argv(self, sites: Path, *, slot: int = 2, run_id: str = "r1", budget: int = 900,
             prereg: Path | None = None, raw: Path | None = None) -> list[str]:
        return ["run", f"--prereg={prereg or self.prereg_path}", f"--raw={raw or self.dry.raw}",
                f"--routing-dir={sites}", f"--endpoint={MODEL}", f"--slot={slot}", f"--run-id={run_id}",
                f"--runs-dir={self.tmp / 'runs'}", f"--budget-seconds={budget}"]

    def run_l1(self, argv: list[str]) -> tuple[int, str, str, Path]:
        code, out, err = call_main(lab_l1, argv)
        return code, out, err, self.tmp / "runs" / "l1" / argv[6].split("=", 1)[1]

    def test_a_unit_through_the_fake_server(self) -> None:
        server = self.server()
        code, out, err, run = self.run_l1(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc = read_json(run / "run.json")
        q = self.dry.prereg["questions"][1]
        self.assertEqual(sorted(doc), sorted(lab_l1.RUN_KEYS))
        self.assertEqual((doc["complete"], doc["finished"], doc["problem"], doc["stopped"], doc["measurement"]),
                         (True, True, None, None, False))
        self.assertEqual((doc["question_id"], doc["slot"], doc["boundary_prefix"], doc["data_label"]),
                         (q["question_id"], 2, "site:", "public"))
        self.assertTrue(all(v is True or v == [] for v in doc["checks"].values()), doc["checks"])
        self.assertEqual(doc["checks"]["unit_check"], [])
        self.assertTrue(doc["lexical"]["reproduces"])
        self.assertEqual(doc["model_calls"], sum(q["records"].values()))
        for mine, entry in doc["mines"].items():
            with self.subTest(mine=mine):
                self.assertEqual(entry["calls"], q["records"][mine])        # K4: one call per retrieved record
                self.assertEqual(entry["attempts"], q["records"][mine])
                self.assertEqual((entry["role"], entry["stratum"]), (q["routes"][mine], q["strata"][mine]))
                rows = rows_of(run / f"ledger-{mine}.jsonl")
                self.assertEqual(len(rows), q["records"][mine])
                self.assertEqual({(r["boundary"], r["endpoint_boundary"], r["data_label"], r["task"]) for r in rows},
                                 {(f"site:{mine}",) * 2 + ("public", JUDGE_TASK)} if rows else set())
                if entry["calls"]:
                    self.assertIsNotNone(entry["first_call_s"])           # K5: the first call beside the median
                    self.assertIsNotNone(entry["median_call_s"])
        t = doc["timing"]
        total = t["question_build_s"] + sum(m["seconds"] for m in t["mines"]) + sum(t["between_s"]) + t["gate_s"]
        self.assertLessEqual(abs(total - t["time_to_answer_s"]), 0.002 * (2 + len(t["mines"]) * 2))
        self.assertEqual(sorted(doc["derived"]), ["at_once_s", "default_deadline", "no_model_s"])
        self.assertEqual(doc["crossing"]["overlap_bytes"], 0)
        lines = rows_of(run / "records.jsonl")
        self.assertEqual(len(lines), sum(q["records"].values()))
        self.assertEqual({tuple(sorted(line)) for line in lines}, {tuple(sorted(lab_l1.RECORD_KEYS))})
        self.assertEqual(sorted((line["mine"], line["index"]) for line in lines),
                         sorted((m, i) for m in q["records"] for i in range(q["records"][m])))
        k12(self, doc)
        k12(self, lines)
        ledgers = [f"ledger-{m}.jsonl" for m in self.dry.prereg["mines"]]
        self.assertEqual(sorted(p.name for p in run.iterdir()), sorted(["run.json", "records.jsonl", *ledgers]))
        self.assertFalse((self.tmp / "runs" / "l1-work-r1").exists())
        self.assertEqual(out, "")

    def test_every_pin_is_checked_before_any_call(self) -> None:
        server = self.server()
        bad = {
            "boundary": lambda d: next(iter(d["endpoints"].values())).update(boundary="site:lab"),
            "deadline": lambda d: next(iter(d["endpoints"].values())).update(deadline_s=3600),
            "retries": lambda d: next(iter(d["endpoints"].values())).update(max_retries=0),
            "escalation": lambda d: d["routes"][JUDGE_TASK].update(escalate_to=MODEL),
            "transport": lambda d: next(iter(d["endpoints"].values())).update(response_format="json_object"),
        }
        for name, change in bad.items():
            with self.subTest(pin=name):
                code, _, err, run = self.run_l1(self.argv(self.routing(server.base_url, change), run_id=f"b-{name}"))
                self.assertEqual(code, 2, err)
                self.assertFalse(run.exists())
        code, _, err, run = self.run_l1(self.argv(self.routing(server.base_url), slot=6, run_id="slot"))
        self.assertEqual(code, 2)
        with mock.patch.object(lab_l1, "code_hash", return_value="0" * 64):
            code, _, err, run = self.run_l1(self.argv(self.routing(server.base_url), run_id="hash"))
        self.assertEqual((code, run.exists()), (2, False))
        self.assertIn("code hash", err)
        other = dict(self.dry.prereg, kind="lab_j1_prereg")
        code, _, err, run = self.run_l1(self.argv(self.routing(server.base_url), run_id="kind",
                                                  prereg=write_json(self.tmp / "p.json", other)))
        self.assertEqual((code, run.exists()), (2, False))
        self.assertEqual(server.requests, [])

    def test_the_file_is_checked_before_any_call(self) -> None:
        """K10: no file and no fetch, or another file, is an infrastructure failure (exit 3) before any call."""
        server = self.server()
        sites = self.routing(server.base_url)
        with mock.patch.object(lab_l1, "ensure_file", side_effect=P.L1Error("fetch")):
            code, _, _, run = self.run_l1(self.argv(sites, run_id="nofile", raw=self.tmp / "empty"))
        doc = read_json(run / "run.json")
        self.assertEqual((code, doc["problem"], doc["model_calls"], doc["complete"]), (3, "fetch", 0, False))
        other = self.tmp / "other"
        l1_data.write_raw(other, l1_data.accident_file(2))
        code, _, _, run = self.run_l1(self.argv(sites, run_id="other", raw=other))
        doc = read_json(run / "run.json")
        self.assertEqual((code, doc["problem"], doc["model_calls"]), (3, "input_sha256", 0))
        self.assertEqual(sorted(p.name for p in run.iterdir()), ["run.json"])
        self.assertEqual(units.harness_status("l1", 3, False, doc), ("failed", L1_INFRA))
        self.assertEqual(server.requests, [])

    def test_a_missing_file_is_fetched_with_the_fetcher(self) -> None:
        raw = self.tmp / "fetched"
        data = (self.dry.raw / "Accidents.zip").read_bytes()
        got = lab_l1.ensure_file(raw, lambda url: data if url.endswith("Accidents.zip") else b"defs\n")
        self.assertEqual(got.read_bytes(), data)
        with self.assertRaises(P.L1Error) as caught:
            lab_l1.ensure_file(self.tmp / "never", lambda url: (_ for _ in ()).throw(OSError("offline")))
        self.assertEqual(caught.exception.code, "fetch")

    def test_it_stops_at_its_budget(self) -> None:
        """Rule 11: a path not finished at the budget is not finished, its elapsed time a lower bound. The budget's
        timer is armed when the timed path starts, for the time the budget's clock says is left then (the checks
        before the path count against the budget but never run under the timer), so how long the checks take on this
        machine does not decide whether the path makes a call. Slot 1 retrieves 45 records here: at half a second a
        call, its calls alone outlast the path's twenty seconds."""
        base = Responder(None, pack_free_judge=True)

        def slow(request: dict[str, Any]) -> dict[str, Any]:
            time.sleep(0.5)
            return base(request)

        server = self.server(slow)
        events: list[tuple[str, float]] = []
        prepare, setitimer = P.prepare, lab_l1.signal.setitimer

        def prepared(*args: Any, **kwargs: Any) -> Any:
            out = prepare(*args, **kwargs)
            events.append(("prepared", 0.0))
            return out

        def timer(which: int, seconds: float, *rest: float) -> Any:
            events.append(("timer", seconds))
            return setitimer(which, seconds, *rest)

        path_s = 20
        with mock.patch.object(lab_l1, "_clock", lambda: 0.0), mock.patch.object(P, "prepare", prepared), \
                mock.patch.object(lab_l1.signal, "setitimer", timer):
            started = time.monotonic()
            code, _, err, run = self.run_l1(self.argv(self.routing(server.base_url), slot=1, run_id="budget",
                                                      budget=lab_l1.AFTER_PATH_S + path_s))
        doc = read_json(run / "run.json")
        self.assertEqual((code, doc["complete"], doc["finished"], doc["stopped"]), (1, False, False, "budget"), err)
        armed = [i for i, (what, seconds) in enumerate(events) if what == "timer" and seconds > 0]
        self.assertEqual([events[i][1] for i in armed], [float(path_s)])    # the time left, by the budget's clock
        self.assertLess(events.index(("prepared", 0.0)), armed[0])           # armed after the checks, not before
        self.assertGreaterEqual(doc["timing"]["elapsed_lower_bound_s"], path_s - 0.01)
        self.assertIsNone(doc["timing"]["time_to_answer_s"])
        self.assertGreater(doc["model_calls"], 0)
        self.assertLess(doc["model_calls"], sum(self.dry.prereg["questions"][0]["records"].values()))
        self.assertLess(time.monotonic() - started, 180)
        self.assertEqual(units.harness_status("l1", 1, False, doc), ("failed", L1_NOT_FINISHED))
        for path in run.glob("ledger-*.jsonl"):
            self.assertTrue(path.read_bytes().endswith(b"\n") or not path.read_bytes())

    def test_transport_failures_are_counted_and_answered_by_index(self) -> None:
        base = Responder(None, pack_free_judge=True)

        def flaky(request: dict[str, Any]) -> dict[str, Any]:
            payload = request_payload(request)
            if int(hashlib.sha256(canonical_bytes(payload)).hexdigest(), 16) % 4 == 0:
                raise RuntimeError("dropped")
            return base(request)

        server = self.server(flaky)
        code, _, err, run = self.run_l1(self.argv(self.routing(server.base_url), slot=3, run_id="flaky"))
        doc = read_json(run / "run.json")
        failed = {m: e["failures"]["transport"] for m, e in doc["mines"].items()}
        self.assertGreater(sum(failed.values()), 0, err)
        lines = rows_of(run / "records.jsonl")
        errors = [line for line in lines if line["model"] is not None and line["model"]["error_kind"] is not None]
        self.assertEqual(len(errors), sum(failed.values()))
        self.assertIn(code, (0, 1))
        if code == 1:
            self.assertEqual(units.harness_status("l1", 1, False, doc), ("failed", L1_CHECK_FAILED))

    def test_a_lexical_rerun_that_differs_fails_the_unit(self) -> None:
        """Rule 5: the unit's lexical rerun must give the preregistered verdicts and status, else the question did
        not finish (a check failed)."""
        server = self.server()
        original = P.run_path

        def changed(data: Any, spec: Any, judge: str, workdir: Path, **kwargs: Any) -> Any:
            result, world = original(data, spec, judge, workdir, **kwargs)
            if judge == "lexical":
                result.status_first = "refuted" if result.status_first != "refuted" else "supported"
            return result, world

        with mock.patch.object(P, "run_path", changed):
            code, _, err, run = self.run_l1(self.argv(self.routing(server.base_url), slot=2, run_id="lexical"))
        doc = read_json(run / "run.json")
        self.assertEqual((code, doc["complete"], doc["finished"], doc["problem"]), (1, False, True, "lexical"), err)
        self.assertEqual((doc["checks"]["lexical_reproduced"], doc["lexical"]["reproduces"]), (False, False))
        self.assertEqual(doc["checks"]["unit_check"], [])
        self.assertEqual(units.harness_status("l1", code, False, doc), ("failed", L1_CHECK_FAILED))

    def test_the_unit_check(self) -> None:
        """K4: fewer calls than retrieved records fail the unit unless the mine degraded, the breaker stopped it or it
        timed out with its late thread still running when the unit ended; a call without a ledger row fails it, but
        for one still in flight when the unit stopped the path."""
        q = {"routes": {"m01": "contributing"}, "records": {"m01": 3}}
        c = lab_l1._Checked(question=q)
        rec = mock.Mock(calls=2, replies={0: {"ref": "a"}, 1: {"ref": "b"}})

        def result(reason: str | None = None, timed_out: bool = False, still: int = 0) -> Any:
            return mock.Mock(recorders={"m01": rec}, first={"m01": {"reason": reason}}, late_still_running=still,
                             timing={"mines": [{"mine": "m01", "timed_out": timed_out}]})

        rows = [{"task": JUDGE_TASK, "ref": r, "attempt": 1, "ok": True} for r in ("a", "b")]
        check = functools.partial(lab_l1.unit_check, endpoint=MODEL)
        self.assertEqual(check(c, result(), {"m01": rows}), ["m01: calls"])
        self.assertEqual(check(c, result("degraded"), {"m01": rows}), [])
        self.assertEqual(check(c, result(timed_out=True, still=1), {"m01": rows}), [])
        self.assertEqual(check(c, result(timed_out=True), {"m01": rows}), ["m01: calls"])
        self.assertEqual(check(c, result("degraded"), {"m01": rows[:1]}), ["m01: ledger"])
        rec.replies[1]["after_close"] = True
        self.assertEqual(check(c, result("degraded"), {"m01": rows[:1]}), [])
        rec.calls = 4
        self.assertEqual(check(c, result("degraded"), {"m01": rows}), ["m01: calls"])

    def test_the_unit_check_s_breaker_is_the_verifier_s(self) -> None:
        """K4: fewer calls are allowed for the breaker's stop only: the mine's last ``BREAKER_AFTER`` calls, in record
        order, each ended (its last attempt) in a failure that says the route's endpoint is down (a SERVER_DOWN kind
        on that endpoint, ``edge.extract.server_down``). One call that failed its attempt and its repair is not it,
        nor two such failures followed by a call, nor failures on another endpoint."""
        q = {"routes": {"m01": "contributing"}, "records": {"m01": 5}}
        c = lab_l1._Checked(question=q)

        def row(i: int, ok: bool = True, kind: str | None = None, attempt: int = 1,
                endpoint: str = MODEL) -> dict[str, Any]:
            return {"task": JUDGE_TASK, "ref": f"j:0123456789ab:{i}", "attempt": attempt, "ok": ok,
                    "error_kind": kind, "endpoint": endpoint}

        def check(rows: list[dict[str, Any]]) -> list[str]:
            calls = sorted({int(r["ref"].rsplit(":", 1)[1]) for r in rows})
            rec = mock.Mock(calls=len(calls), replies={i: {"ref": f"j:0123456789ab:{i}"} for i in calls})
            result = mock.Mock(recorders={"m01": rec}, first={"m01": {"reason": None}}, late_still_running=0,
                               timing={"mines": [{"mine": "m01", "timed_out": False}]})
            return lab_l1.unit_check(c, result, {"m01": rows}, endpoint=MODEL)

        self.assertEqual(lab_l1.BREAKER_AFTER, 2)
        ok = [row(0), row(1)]
        self.assertEqual(check(ok + [row(2, False, "timeout"), row(3, False, "http_5xx")]), [])
        self.assertEqual(check(ok + [row(2, False, "network"), row(3, False, "schema_invalid"),
                                     row(3, False, "timeout", attempt=2)]), [])      # the call's last attempt
        cases = {
            "one call, its attempt and its repair failed": ok + [row(2, False, "schema_invalid"),
                                                                 row(2, False, "schema_invalid", attempt=2)],
            "two down, then a call": [row(0, False, "timeout"), row(1, False, "timeout"), row(2)],
            "a call that failed otherwise between": ok + [row(2, False, "timeout"), row(3, False, "json_invalid")],
            "down on another endpoint": ok + [row(2, False, "timeout", endpoint="other"),
                                              row(3, False, "timeout", endpoint="other")],
            "one down call": ok + [row(2), row(3, False, "timeout")],
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                self.assertEqual(check(rows), ["m01: calls"])

    def test_the_recorder_after_the_unit_stopped_the_path(self) -> None:
        """A late thread starts no call once the unit stopped the path, and a call in flight then is marked."""
        gate = __import__("threading").Event()

        class Slow:
            def run(self, task: Any, payload: Any, schema: Any, *, ref: str, endpoint: str | None = None) -> Any:
                gate.wait(5)
                raise ValueError("I/O operation on closed file")

        runtime = Slow()
        rec = P.Recorder(runtime)
        worker = __import__("threading").Thread(target=lambda: self.assertRaises(ValueError, runtime.run, None, {},
                                                                                   None, ref="j:0123456789ab:4"))
        worker.start()
        time.sleep(0.1)
        rec.close()
        gate.set()
        worker.join(5)
        self.assertEqual((rec.calls, rec.replies[4]["after_close"]), (1, True))
        with self.assertRaises(RuntimeError):
            runtime.run(None, {}, None, ref="j:0123456789ab:5")
        self.assertEqual(rec.calls, 1)

    def test_errors_print_their_class_and_place_only(self) -> None:
        secret = _Raw.get().secrets[0]
        with mock.patch.object(lab_l1, "cmd_run", side_effect=KeyError(secret)):
            code, out, err = call_main(lab_l1, self.argv(self.tmp))
        self.assertEqual(code, 2)
        self.assertNotIn(secret, out + err)
        self.assertRegex(err.strip(), r"^l1 run: KeyError at [a-z0-9_/.]+:[0-9]+$")


# --------------------------------------------------------------------------------------------------- the lab's units

class UnitWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dry = _Dry.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-l1-units-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_argv_routing_and_status(self) -> None:
        unit = self.dry.unit(2)
        prereg = lab_prereg.load_prereg(self.dry.out / "plan" / "plan.json")
        argv = units.build_argv(unit, self.tmp, self.tmp / "unused", budget_s=17700, prereg=prereg.dir)
        self.assertEqual(argv[1:4], ["-m", "lab.l1", "run"])
        flags = dict(a[2:].split("=", 1) for a in argv[4:])
        self.assertEqual(flags["prereg"], str(prereg.dir / "prereg" / "l1" / "prereg.json"))
        self.assertEqual(flags["raw"], str(lab_l1.raw_dir(prereg.dir)))
        self.assertEqual((flags["endpoint"], flags["slot"], flags["budget-seconds"]), (MODEL, "2", "17700"))
        self.assertEqual(flags["routing-dir"], str(self.tmp / "routing" / unit["unit"] / "sites"))
        entry = self.dry.plan["models"][MODEL]
        sha, files, valid, _ = units.write_routing(unit, self.dry.plan, entry, "http://127.0.0.1:9/v1", self.tmp,
                                                   prereg)
        self.assertTrue(valid)
        self.assertEqual(sorted(files), [f"routing/{unit['unit']}/sites/{m}.json" for m in self.dry.prereg["mines"]])
        for mine in self.dry.prereg["mines"]:
            doc = read_json(self.tmp / "routing" / unit["unit"] / "sites" / f"{mine}.json")
            ep = doc["endpoints"][MODEL]
            self.assertEqual((ep["boundary"], ep["deadline_s"], ep["max_retries"], ep["model"]),
                             (f"site:{mine}", 600, 1, entry["alias"]))
            self.assertEqual(doc["routes"], {JUDGE_TASK: {"endpoint": MODEL}})
        self.assertEqual(units.required_tasks(unit), [JUDGE_TASK])
        ok = {"kind": "lab_l1_run", "complete": True, "finished": True}
        cases = [((0, ok), ("ok", None)), ((1, {**ok, "complete": False}), ("failed", L1_CHECK_FAILED)),
                 ((1, {**ok, "complete": False, "finished": False}), ("failed", L1_NOT_FINISHED)),
                 ((3, {**ok, "complete": False}), ("failed", L1_INFRA)),
                 ((0, {**ok, "complete": False}), ("failed", notes.RESULT_CONTRADICTS_EXIT)),
                 ((0, None), ("failed", notes.RESULT_MISSING))]
        for (code, result), want in cases:
            with self.subTest(code=code, result=result):
                self.assertEqual(units.harness_status("l1", code, False, result), want)

    def test_participation_counts_each_mine_s_calls(self) -> None:
        """Two mines number their calls alike: each call counts once, at its own boundary."""
        rows = [{"task": JUDGE_TASK, "ref": "j:0:0", "attempt": 1, "ok": True, "error_kind": None,
                 "boundary": f"site:m0{i}"} for i in (1, 2)]
        record, problem = units.participation("l1", rows, [JUDGE_TASK], None)
        self.assertEqual((record["tasks"][JUDGE_TASK]["ok"], record["tasks"][JUDGE_TASK]["attempted"], problem),
                         (2, 2, None))

    def test_the_warm_up_reads_no_record_and_asks_no_question(self) -> None:
        """K5: one judge_record task of pack ``l1`` from the preregistration's warm-up payloads."""
        prereg = lab_prereg.load_prereg(self.dry.out / "plan" / "plan.json")
        tasks = lab_warmup.warm_tasks([self.dry.unit(1), self.dry.unit(2)], self.dry.plan, prereg)
        (task,) = tasks
        warm = read_json(self.dry.out / "plan" / "prereg" / "l1" / "warmup.json")
        self.assertEqual((task.task.name, task.pack, task.units), (JUDGE_TASK, "l1", (self.dry.unit(1)["unit"],
                                                                                       self.dry.unit(2)["unit"])))
        self.assertEqual((task.payload, task.worst), (warm["typical"], warm["worst"]))
        self.assertEqual(task.payload["question"]["predicate"], _Data.get().other_predicate)
        text = task.worst["record"]["text"]
        self.assertLessEqual(len(text), _Data.get().pack.extraction.max_input_chars)
        grams = {g for n in _Raw.get().writer.narratives for g in G.ngrams(n, 4)}
        self.assertFalse(G.ngrams(text, 4) & grams)
        self.assertEqual(lab_warmup.warm_tasks([self.dry.unit(1)], self.dry.plan, None), [])


# --------------------------------------------------------------------------------------------------- the dry run

class DryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Dry.get()

    def test_the_plan_and_the_units(self) -> None:
        self.assertEqual(self.dry.code, 0, self.dry.stdout)
        plan = self.dry.plan
        self.assertEqual([(u["unit"], u["params"]["slot"], u["minutes"]) for u in plan["units"]],
                         [(f"l1-{MODEL}-q{k}", k, 300) for k in range(1, 6)])
        self.assertEqual({(s["planned_minutes"], s["timeout_minutes"], len(s["units"])) for s in plan["shards"]},
                         {(300, 325, 1)})
        self.assertEqual({(u["status"], u["display_class"]) for u in self.dry.report["units"]}, {("ok", "plumbing")})
        guard_lines = [line for line in self.dry.stdout.splitlines() if line.startswith("l1 guard:")]
        self.assertEqual(len(guard_lines), 2 + 3 * 5 + 2)         # the workflow's guard steps, in order
        self.assertTrue(all(" withheld 0 " in line for line in guard_lines))

    def test_the_report_block(self) -> None:
        block = self.dry.report["l1"]
        self.assertEqual((block["reason"], block["display_class"], block["operator"], sorted(block["models"])),
                         (None, "plumbing", "c1", [MODEL]))
        self.assertEqual(block["comparators"]["judges"], self.dry.scores["judges"])
        self.assertEqual(block["alert"], self.dry.prereg["alert"])
        entry = block["models"][MODEL]
        self.assertEqual((entry["complete"], entry["slots_finished"], entry["display_class"], entry["measurement"]),
                         (True, 5, "plumbing", False))
        self.assertEqual((entry["headline"], entry["headline_reason"]), (None, L1_NOT_MEASURED))
        scored = sum(1 for q in self.dry.prereg["questions"] for m in q["routes"]
                     if self.dry.prereg["judges"]["key"][str(q["slot"])]["verdicts"][m] in SC.SCORED)
        self.assertEqual((entry["answers"], entry["left_out"], entry["left_out_share"]), (scored, 0, 0.0))
        for judge in ("lexical", "route_role", "record_blind", "key"):
            self.assertEqual(entry["scores"][judge]["balanced_accuracy"]["value"],
                             self.dry.scores["judges"][judge]["balanced_accuracy"]["value"], judge)
        self.assertEqual(entry["d_lex"]["against"], "lexical")
        self.assertEqual(entry["d_route"]["against"], "route_role")
        self.assertEqual(entry["gate"]["questions"], 5)
        # a record is a mine and its index in the mine's retrieved list, the same list for every question
        widest: dict[str, int] = {}
        for q in self.dry.prereg["questions"]:
            for mine, n in q["records"].items():
                widest[mine] = max(widest.get(mine, 0), n)
        self.assertEqual((entry["per_record"]["records"], entry["per_record"]["judgements"]),
                         (sum(widest.values()), sum(sum(q["records"].values()) for q in self.dry.prereg["questions"])))
        self.assertEqual(entry["latency"]["alert"]["questions"], 1)
        self.assertEqual(entry["latency"]["all"]["questions"], 5)
        self.assertEqual(len(entry["latency"]["cpu_models"]), 1)
        self.assertEqual([q["slot"] for q in entry["questions"]], [1, 2, 3, 4, 5])
        for q in entry["questions"]:
            self.assertEqual(q["key"], self.dry.prereg["judges"]["key"][str(q["slot"])]["verdicts"])
        self.assertFalse([r for r in self.dry.report["latency"] if r.get("experiment") == "l1"])
        k12(self, block)

    def test_the_summaries(self) -> None:
        root = self.dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        for key in ("l1", "l1-time"):
            self.assertIn(f"#### {notes.HEADINGS[key]}", md)
        self.assertIn(f"| `{MODEL}` | `plumbing` | 5 of 5 |", md)
        self.assertIn(notes.L1_TIME_NOTE, md)
        self.assertIn(notes.L1_HEADLINE_NOTE, md)
        self.assertIn(notes.NOTES["public_msha"], md)
        sources = read_json(root / "report.sources.json")["sources"]
        check_sources(self, md, sources, root)
        plan_md = (self.dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        self.assertIn(f"#### {notes.HEADINGS['l1-prereg']}", plan_md)
        check_sources(self, plan_md, read_json(self.dry.out / "plan" / "summary.sources.json")["sources"],
                      self.dry.out / "plan")
        for shard in self.dry.plan["shards"]:
            shard_root = self.dry.out / "shards" / shard["shard"]
            check_sources(self, (shard_root / "summary" / "summary.md").read_text(encoding="utf-8"),
                          read_json(shard_root / "summary" / "summary.sources.json")["sources"], shard_root)

    def test_nothing_written_holds_a_value_of_the_file(self) -> None:
        """Rule 12: no record id, mine id, operator's or controller's name or id, or narrative run anywhere a run
        writes (the plan, every shard root, the report)."""
        secrets = _Raw.get().secrets
        grams = {g for n in _Raw.get().writer.narratives for g in G.ngrams(n, 8)}
        checked = 0
        for root in (self.dry.out / "plan", self.dry.out / "shards", self.dry.out / "report"):
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                checked += 1
                for value in secrets:
                    self.assertNotIn(value, text, path)
                self.assertFalse(G.ngrams(text, 8) & grams, path)
        self.assertGreater(checked, 50)


# --------------------------------------------------------------------------------------------------- the aggregate

class AggregateTests(unittest.TestCase):
    """DryTree copies of the dry run, changed and re-sealed as a real run would have left them."""

    def setUp(self) -> None:
        self.dry = _Dry.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-l1-agg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = DryTree(self.dry.out, self.tmp / "T", manifest=LAB_MANIFEST)

    def shard(self, slot: int) -> str:
        return self.dry.unit(slot)["shard"]

    def rel(self, slot: int, name: str) -> str:
        return f"runs/l1/{self.dry.unit(slot)['run_id']}/{name}"

    def edit_run(self, slot: int, change: Callable[[dict[str, Any]], None], reseal: bool = True) -> None:
        doc = self.tree.read(self.shard(slot), self.rel(slot, "run.json"))
        change(doc)
        self.tree.replace_unit_file(self.shard(slot), self.dry.unit(slot)["unit"], self.rel(slot, "run.json"),
                                    canonical_bytes(doc) + b"\n")
        if reseal:
            self.tree.reseal(self.shard(slot))

    def make_measured(self) -> None:
        for slot in range(1, 6):
            self.edit_run(slot, lambda d: d.update(measurement=True), reseal=False)
            self.tree.make_real(self.shard(slot), model_units=(self.dry.unit(slot)["unit"],))

    def aggregate(self, *extra: str) -> dict[str, Any]:
        code, _, err, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, err)
        return report

    def test_a_complete_model_measurement_gets_its_headline(self) -> None:
        self.make_measured()
        entry = self.aggregate()["l1"]["models"][MODEL]
        self.assertEqual((entry["display_class"], entry["measurement"], entry["complete"]), ("model", True, True))
        self.assertIn(entry["headline"], SC.HEADLINES)
        want = SC.headline(finished=True, measured=True, left_out_share=entry["left_out_share"], d_lex=entry["d_lex"],
                           d_route=entry["d_route"])
        self.assertEqual(entry["headline"], want[0])

    def test_a_missing_question_leaves_a_partial_reading(self) -> None:
        self.make_measured()
        shutil.rmtree(self.tree.path(self.shard(3)))
        entry = self.aggregate()["l1"]["models"][MODEL]
        self.assertEqual((entry["complete"], entry["partial"], entry["slots_ok"]), (False, True, [1, 2, 4, 5]))
        self.assertEqual((entry["headline"], entry["headline_reason"]), ("no_verdict", L1_INCOMPLETE))   # K3
        self.assertEqual(entry["latency"]["all"]["not_finished"], 1)

    def test_the_first_decision_is_scored(self) -> None:
        """K6: a mine that timed out is scored as the first decision saw it (HQ's timeout, unknown), and the gate's
        first status is the one compared with the key's; the late verdict and the final status decide nothing."""
        q = self.dry.prereg["questions"][0]
        key = self.dry.prereg["judges"]["key"]["1"]
        mine = next(m for m in sorted(q["routes"]) if key["verdicts"][m] in SC.SCORED)
        other = next(s for s in ("insufficient", "refuted", "supported") if s != key["status"])

        def late(doc: dict[str, Any]) -> None:
            v = doc["verdicts"]
            v["final"] = {m: dict(e) for m, e in v["first"].items()}
            v["first"][mine] = {"verdict": "unknown", "reason": "timeout", "support_bucket": None}
            v["final"][mine] = {"verdict": key["verdicts"][mine], "reason": None, "support_bucket": None}
            v["status_first"], v["status_final"] = other, key["status"]

        self.edit_run(1, late)
        runs = [self.tree.read(self.shard(s), self.rel(s, "run.json")) for s in range(1, 6)]
        entry = self.aggregate()["l1"]["models"][MODEL]
        question = entry["questions"][0]
        self.assertEqual((question["verdicts"][mine], question["final"][mine]), ("unknown", key["verdicts"][mine]))
        self.assertEqual((question["status_first"], question["status_final"]), (other, key["status"]))
        unknowns = sum(1 for doc in runs for m, e in doc["verdicts"]["first"].items()
                       if e["verdict"] == "unknown"
                       and self.dry.prereg["judges"]["key"][str(doc["slot"])]["verdicts"][m] in SC.SCORED)
        self.assertGreater(unknowns, 0)
        self.assertEqual(sum(entry["strata"][s]["model"]["verdicts"]["unknown"] for s in SC.STRATA),
                         sum(1 for doc in runs for e in doc["verdicts"]["first"].values() if e["verdict"] == "unknown"))
        scored = entry["scores"]["model"]["answers"]
        self.assertEqual(entry["scores"]["model"]["unknown_share"]["value"], round(unknowns / scored, 3))
        equal = sum(1 for doc in runs
                    if doc["verdicts"]["status_first"] == self.dry.prereg["judges"]["key"][str(doc["slot"])]["status"])
        self.assertEqual(entry["gate"]["judges"]["model"]["equal"], equal)
        self.assertEqual(sum(1 for doc in runs if doc["verdicts"]["status_final"]
                             == self.dry.prereg["judges"]["key"][str(doc["slot"])]["status"]) - equal, 1)

    def not_finished(self, slot: int, elapsed: float, calls: int) -> None:
        """The unit of ``slot`` as a budget stop would have left it (rule 11): a run that did not finish, its timing
        a lower bound, its record failed with :data:`L1_NOT_FINISHED`."""
        def stop(doc: dict[str, Any]) -> None:
            doc.update(complete=False, finished=False, stopped="budget", problem=None, model_calls=calls,
                       verdicts=None, derived=None, lexical=None, time_to_final_s=None, confirm_shares={},
                       crossing=None,
                       timing={"slot": slot, "finished": False, "elapsed_lower_bound_s": elapsed, "calls": calls,
                               "time_to_answer_s": None, "question_build_s": None, "gate_s": None,
                               "between_s": [], "mines": []})

        self.edit_run(slot, stop, reseal=False)
        self.tree.edit(self.shard(slot), f"units/{self.dry.unit(slot)['unit']}/unit.json",
                       lambda r: r.update(status="failed", status_reason=L1_NOT_FINISHED, exit_code=1))
        self.tree.reseal(self.shard(slot))

    def test_a_question_not_finished_keeps_its_lower_bound(self) -> None:
        """Rule 11: a path not finished by the budget is reported as not finished, with its elapsed time as a lower
        bound, in report.json and in report.md's time section."""
        self.make_measured()
        self.not_finished(1, 17890.125, 812)
        code, _, err, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, err)
        entry = report["l1"]["models"][MODEL]
        part = next(p for p in entry["units"] if p["slot"] == 1)
        self.assertEqual((part["ok"], part["status"], part["problem"]), (False, "failed", "not_finished"))
        self.assertEqual((part["elapsed_lower_bound_s"], part["model_calls"]), (17890.125, 812))
        for p in entry["units"]:
            if p["slot"] != 1:
                self.assertEqual((p["elapsed_lower_bound_s"], p["model_calls"]), (None, None), p["slot"])
        bound = [{"slot": 1, "elapsed_lower_bound_s": 17890.125}]
        self.assertEqual(entry["latency"]["alert"]["not_finished_lower_bounds"], bound)
        self.assertEqual(entry["latency"]["all"]["not_finished_lower_bounds"], bound)
        self.assertEqual((entry["headline"], entry["headline_reason"]), ("no_verdict", L1_INCOMPLETE))
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        columns = [notes.COLUMNS[c] for c in ("model", "l1_slot", "l1_elapsed_s", "l1_calls")]
        self.assertIn("| " + " | ".join(columns) + " |", md)
        self.assertIn(f"| `{MODEL}` | 1 | 17890.125 | 812 |", md)
        k12(self, report["l1"])

    def test_a_question_whose_run_is_not_this_one_shows_no_lower_bound(self) -> None:
        """Only a run of this preregistration, endpoint, slot and question gives its elapsed time."""
        self.not_finished(2, 99.5, 3)
        self.edit_run(2, lambda d: d.update(question_id="q-other"))
        part = next(p for p in self.aggregate()["l1"]["models"][MODEL]["units"] if p["slot"] == 2)
        self.assertEqual((part["problem"], part["elapsed_lower_bound_s"], part["model_calls"]),
                         ("run_differs", None, None))

    def test_a_changed_records_file_is_not_a_finished_question(self) -> None:
        lines = rows_of(self.tree.path(self.shard(2), self.rel(2, "records.jsonl")))
        lines[0]["index"] = 999
        data = "".join(canonical_dumps(line) + "\n" for line in lines).encode("utf-8")
        self.tree.replace_unit_file(self.shard(2), self.dry.unit(2)["unit"], self.rel(2, "records.jsonl"), data)
        self.tree.reseal(self.shard(2))
        entry = self.aggregate()["l1"]["models"][MODEL]
        part = next(p for p in entry["units"] if p["slot"] == 2)
        self.assertEqual((part["ok"], part["problem"], entry["complete"]), (False, "records_differ", False))

    def test_a_run_of_another_question_is_not_finished(self) -> None:
        self.edit_run(4, lambda d: d.update(question_id="q-other"))
        part = next(p for p in self.aggregate()["l1"]["models"][MODEL]["units"] if p["slot"] == 4)
        self.assertEqual((part["ok"], part["problem"]), (False, "run_differs"))

    def test_transport_failures_above_one_in_twenty_give_no_verdict(self) -> None:
        self.make_measured()
        q = self.dry.prereg["questions"][0]
        mine = next(m for m in sorted(q["routes"])
                    if self.dry.prereg["judges"]["key"]["1"]["verdicts"][m] in SC.SCORED)
        self.edit_run(1, lambda d: d["mines"][mine]["failures"].update(transport=1))
        self.edit_run(2, lambda d: [e["failures"].update(transport=1) for e in d["mines"].values()])
        entry = self.aggregate()["l1"]["models"][MODEL]
        self.assertGreater(entry["left_out"], 1)
        self.assertGreater(entry["left_out_share"], 0.05)
        self.assertEqual((entry["headline"], entry["headline_reason"]), ("no_verdict", L1_LEFT_OUT))
        self.assertEqual(entry["scores"]["model"]["answers"], entry["answers"] - entry["left_out"])

    def rerun(self, slot: int, first_calls: int, keep_ledgers: bool = False) -> dict[str, Any]:
        """The shard of ``slot`` as attempt 1 (its run.json saying ``first_calls`` model calls; with none, its unit
        record counts no ledger row and its ledgers are removed unless ``keep_ledgers``) and a re-run as attempt 2."""
        shard = self.shard(slot)
        root = self.tree.shards
        first = root / f"{shard}-attempt-1"
        shutil.copytree(root / shard, first)
        doc = read_json(first / self.rel(slot, "run.json"))
        doc.update(model_calls=first_calls)
        (first / self.rel(slot, "run.json")).write_bytes(canonical_bytes(doc) + b"\n")
        if first_calls == 0:
            if not keep_ledgers:
                for ledger in (first / self.rel(slot, "")).glob("ledger-*.jsonl"):
                    ledger.unlink()
            self.tree.edit(shard, f"units/{self.dry.unit(slot)['unit']}/unit.json", lambda r: None)
            record = read_json(first / "units" / self.dry.unit(slot)["unit"] / "unit.json")
            record["ledger_rows"] = 0
            (first / "units" / self.dry.unit(slot)["unit"] / "unit.json").write_bytes(canonical_bytes(record))
        self.tree.reseal(shard, attempt=1, path=first)
        self.tree.reseal(shard, attempt=2)
        return self.aggregate()

    def test_a_re_run_after_a_model_call_is_not_taken(self) -> None:
        report = self.rerun(2, first_calls=7)
        row = next(u for u in report["units"] if u["unit"] == self.dry.unit(2)["unit"])
        self.assertEqual((row["status"], row["status_reason"], row["display_class"]),
                         ("excluded", L1_RERUN_AFTER_CALLS, "no-result"))
        part = next(p for p in report["l1"]["models"][MODEL]["units"] if p["slot"] == 2)
        self.assertEqual((part["ok"], part["rerun_after_calls"]), (False, [1]))
        self.assertFalse(report["l1"]["models"][MODEL]["complete"])

    def test_a_re_run_before_any_model_call_is_taken(self) -> None:
        report = self.rerun(2, first_calls=0)
        row = next(u for u in report["units"] if u["unit"] == self.dry.unit(2)["unit"])
        self.assertEqual(row["status"], "ok")
        entry = report["l1"]["models"][MODEL]
        self.assertTrue(entry["complete"])
        part = next(p for p in entry["units"] if p["slot"] == 2)
        self.assertEqual((part["rerun_after_calls"], part["rerun_calls_unknown"]), (None, None))

    def test_a_re_run_after_ledger_rows_is_not_taken(self) -> None:
        """K10: an earlier attempt's ledger with a row shows a model call, whatever its run.json and unit record
        say."""
        rows = sum(len(rows_of(p)) for p in self.tree.path(self.shard(2), self.rel(2, "")).glob("ledger-*.jsonl"))
        self.assertGreater(rows, 0)
        report = self.rerun(2, first_calls=0, keep_ledgers=True)
        row = next(u for u in report["units"] if u["unit"] == self.dry.unit(2)["unit"])
        self.assertEqual((row["status"], row["status_reason"]), ("excluded", L1_RERUN_AFTER_CALLS))
        part = next(p for p in report["l1"]["models"][MODEL]["units"] if p["slot"] == 2)
        self.assertEqual((part["ok"], part["rerun_after_calls"]), (False, [1]))

    def test_a_re_run_after_an_attempt_without_an_artifact_is_not_taken(self) -> None:
        """K10: an earlier attempt of the shard that left no artifact (a cancelled or timed-out job uploads none)
        may have made model calls, so the re-run is not taken."""
        shard = self.shard(3)
        self.tree.reseal(shard, attempt=2)                    # attempt 1 left nothing
        report = self.aggregate()
        row = next(u for u in report["units"] if u["unit"] == self.dry.unit(3)["unit"])
        self.assertEqual((row["status"], row["status_reason"], row["display_class"]),
                         ("excluded", notes.L1_RERUN_CALLS_UNKNOWN, "no-result"))
        entry = report["l1"]["models"][MODEL]
        part = next(p for p in entry["units"] if p["slot"] == 3)
        self.assertEqual((part["ok"], part["rerun_after_calls"], part["rerun_calls_unknown"]), (False, None, [1]))
        self.assertFalse(entry["complete"])
        # the same shard with attempts 1 and 3 found, attempt 2 missing
        first = self.tree.shards / f"{shard}-attempt-1"
        shutil.copytree(self.tree.path(shard), first)
        for ledger in (first / self.rel(3, "")).glob("ledger-*.jsonl"):
            ledger.unlink()
        doc = read_json(first / self.rel(3, "run.json"))
        doc.update(model_calls=0)
        (first / self.rel(3, "run.json")).write_bytes(canonical_bytes(doc) + b"\n")
        record = read_json(first / "units" / self.dry.unit(3)["unit"] / "unit.json")
        record["ledger_rows"] = 0
        (first / "units" / self.dry.unit(3)["unit"] / "unit.json").write_bytes(canonical_bytes(record))
        self.tree.reseal(shard, attempt=1, path=first)
        self.tree.reseal(shard, attempt=3)
        code, _, err, report = self.tree.aggregate(self.tmp / "R3")
        self.assertEqual(code, 0, err)
        part = next(p for p in report["l1"]["models"][MODEL]["units"] if p["slot"] == 3)
        self.assertEqual((part["rerun_after_calls"], part["rerun_calls_unknown"]), (None, [2]))

    def test_without_the_preregistration_and_under_re_aggregation(self) -> None:
        (self.tree.root / "plan" / "prereg" / "l1" / "scores.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.aggregate()["l1"]["reason"], PREREG_MISSING)
        plan = self.tree.plan
        block = lab_aggregate.l1_block(plan, self.tree.plan_path, {
            u["unit"]: ({"display_class": "plumbing"}, None, None) for u in plan["units"]}, {}, reaggregation=True)
        self.assertEqual((block["reason"], block["models"]), (L1_NO_REAGGREGATION, {}))


# --------------------------------------------------------------------------------------------------- the guard

class GuardTests(unittest.TestCase):
    """Rule 12 and K9: every file a run uploads is scanned with D002's last guard and backstop for c1 to c5."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = _Raw.get()
        cls.guard = G.build(cls.raw.raw)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-l1-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_guard_s_values(self) -> None:
        self.assertIsNotNone(self.guard)
        w = self.raw.writer
        self.assertGreater(self.guard.counts["record_ids"], 0)
        self.assertGreater(self.guard.counts["mine_ids"], 0)
        self.assertEqual(self.guard.counts["operators"], 2)
        doc = sorted(w.ids["documents"])[0]
        mine = sorted(w.ids["mines"])[0]
        cases = {"record_id": f"see {doc} here", "mine_id": f"mine {mine}",
                 "refused_value": f"by {l1_data.OPERATOR['operator_name']} today",
                 "narrative_ngrams": " ".join(w.narratives[0].split()[:9])}
        for kind, text in cases.items():
            with self.subTest(kind=kind):
                self.assertGreater(self.guard.hits(text)[kind], 0)
        self.assertEqual(self.guard.hits('{"mine": "m01", "slot": 1, "seconds": 0.125}'),
                         dict.fromkeys(G.KINDS, 0))

    def test_a_hit_withholds_the_file_and_fails_the_job(self) -> None:
        d = self.tmp / "dir"
        (d / "units" / "u").mkdir(parents=True)
        (d / "work").mkdir()
        (d / "clean.json").write_text('{"mine": "m01"}', encoding="utf-8")
        doc = sorted(self.raw.writer.ids["documents"])[3]
        (d / "units" / "u" / "stderr.log").write_text(f"KeyError: '{doc}'\n", encoding="utf-8")
        (d / "work" / "skipped.txt").write_text(doc, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.run([d], self.raw.raw)
        self.assertEqual(code, 1)
        stub = json.loads((d / "units" / "u" / "stderr.log").read_text(encoding="utf-8"))
        self.assertEqual((stub["kind"], stub["hits"]["record_id"]), (G.STUB_KIND, 1))
        self.assertEqual((d / "clean.json").read_text(encoding="utf-8"), '{"mine": "m01"}')
        self.assertEqual((d / "work" / "skipped.txt").read_text(encoding="utf-8"), doc)     # never uploaded
        self.assertNotIn(doc, out.getvalue())
        self.assertEqual(out.getvalue().splitlines(),
                         ["l1 guard: dir files 2 withheld 1 record_id 1 mine_id 0 refused_value 0 "
                          "narrative_ngrams 0 unread 0"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(G.run([d], self.raw.raw), 0)                   # a stub is not scanned again

    def test_the_server_s_logs_are_scanned(self) -> None:
        """K9: the shard root's ``server/`` logs are scanned; only what the seal removes and never uploads
        (``server/bin``, ``server/home``, ``server/tmp``, ``work``) is not."""
        d = self.tmp / "shard"
        mine = sorted(self.raw.writer.ids["mines"])[1]
        for rel in ("server/server.log", "server/a-0p5b/stderr.log", "server/bin/x.txt", "server/home/x.txt",
                    "server/tmp/x.txt"):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(f"loaded at mine {mine}\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(G.run([d], self.raw.raw), 1)
        for rel in ("server/server.log", "server/a-0p5b/stderr.log"):
            self.assertEqual(json.loads((d / rel).read_text(encoding="utf-8"))["kind"], G.STUB_KIND, rel)
        for rel in ("server/bin/x.txt", "server/home/x.txt", "server/tmp/x.txt"):
            self.assertIn(mine, (d / rel).read_text(encoding="utf-8"), rel)
        self.assertIn("files 2 withheld 2 ", out.getvalue())

    def test_without_the_file_every_file_is_withheld(self) -> None:
        d = self.tmp / "dir"
        d.mkdir()
        (d / "a.json").write_text("{}", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.run([d], self.tmp / "no-raw", fetcher=lambda url: (_ for _ in ()).throw(OSError("offline")))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((d / "a.json").read_text(encoding="utf-8"))["hits"]["unread"], 1)
        self.assertNotIn("offline", out.getvalue())

    def test_the_command_leaves_other_plans_alone(self) -> None:
        plan = write_json(self.tmp / "plan.json", {"units": [{"experiment": "j1"}]})
        (self.tmp / "x.txt").write_text(sorted(self.raw.writer.ids["documents"])[0], encoding="utf-8")
        code, out, _ = call_main(lab_msha, ["guard", "--plan", str(plan), "--dir", str(self.tmp)])
        self.assertEqual((code, out), (0, "l1 guard: no l1 units\n"))

    def test_an_error_is_its_class_and_place(self) -> None:
        secret = sorted(self.raw.writer.ids["mines"])[0]
        try:
            raise ValueError(f"bad value {secret}")
        except ValueError as err:
            line = G.error_line("l1 guard", err)
        self.assertNotIn(secret, line)
        self.assertRegex(line, r"^l1 guard: ValueError at tests/lab/test_lab_l1\.py:[0-9]+$")


# --------------------------------------------------------------------------------------------------- the workflow

def _steps(job: str) -> list[dict[str, Any]]:
    return WORKFLOW["jobs"][job]["steps"]


def _index(steps: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> list[int]:
    return [i for i, s in enumerate(steps) if pred(s)]


def _guard(s: dict[str, Any]) -> bool:
    return s.get("run", "").startswith("python -m lab.msha guard ")


class WorkflowTests(unittest.TestCase):
    """K9 and K10 in ``.github/workflows/mycelic-lab.yml``."""

    MSHA = "${{ runner.temp }}/lab-msha"

    def test_the_plan_job_saves_the_file_and_guards_its_directory(self) -> None:
        steps = _steps("plan")
        ids = [s.get("id") for s in steps]
        prereg, key = ids.index("prereg"), ids.index("l1-key")
        (save,) = _index(steps, lambda s: s.get("uses", "").startswith("actions/cache/save@"))
        guard, again = _index(steps, _guard)
        (summary,) = _index(steps, lambda s: s.get("run", "").startswith("python -m lab.summary plan"))
        (upload,) = _index(steps, lambda s: s.get("uses", "").startswith("actions/upload-artifact@"))
        order = [prereg, key, save, guard, summary, again, upload]
        self.assertEqual(sorted(order), order)
        self.assertEqual(steps[again], steps[guard])
        self.assertEqual(steps[key]["if"], "steps.prereg.outcome == 'success'")
        self.assertIn('lab.msha cache-key --plan "$RUNNER_TEMP/lab-plan/plan.json" --github-output "$GITHUB_OUTPUT"',
                      steps[key]["run"])
        self.assertEqual(steps[save]["with"], {"path": self.MSHA, "key": "${{ steps.l1-key.outputs.l1_cache_key }}"})
        self.assertEqual(steps[save]["if"], "steps.l1-key.outputs.l1_cache_key != ''")
        self.assertEqual(steps[guard]["if"], "${{ !cancelled() }}")
        self.assertIn('--dir "$RUNNER_TEMP/lab-plan"', steps[guard]["run"])
        self.assertEqual(WORKFLOW["jobs"]["plan"]["outputs"]["l1_cache_key"],
                         "${{ steps.l1-key.outputs.l1_cache_key }}")
        # the path the workflow caches is the one the preregistration and the units read
        self.assertEqual(lab_l1.raw_dir(Path("/T/lab-plan")), Path("/T/lab-msha"))

    def test_the_run_job_restores_the_file_and_guards_before_and_after_the_seal(self) -> None:
        steps = _steps("run")
        ids = [s.get("id") for s in steps]
        (restore,) = _index(steps, lambda s: s.get("uses", "").startswith("actions/cache/restore@")
                            and s["with"]["path"] == self.MSHA)
        self.assertLess(restore, ids.index("prepare"))
        self.assertEqual(steps[restore]["if"], "needs.plan.outputs.l1_cache_key != ''")
        self.assertEqual(steps[restore]["with"]["key"], "${{ needs.plan.outputs.l1_cache_key }}")
        self.assertIs(steps[restore]["continue-on-error"], True)
        guards = _index(steps, _guard)
        seal = ids.index("seal")
        (summary,) = _index(steps, lambda s: s.get("run", "").startswith("python -m lab.summary shard"))
        (upload,) = _index(steps, lambda s: s.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(len(guards), 3)
        order = [ids.index("run"), guards[0], seal, guards[1], summary, guards[2], upload]
        self.assertEqual(sorted(order), order)
        for i in guards:
            self.assertEqual(steps[i]["if"], "${{ !cancelled() }}")
            self.assertIn('--dir "$RUNNER_TEMP/lab-shard"', steps[i]["run"])

    def test_the_aggregate_job_restores_the_file_and_guards_the_report(self) -> None:
        steps = _steps("aggregate")
        ids = [s.get("id") for s in steps]
        (restore,) = _index(steps, lambda s: s.get("uses", "").startswith("actions/cache/restore@"))
        guard, again = _index(steps, _guard)
        (summary,) = _index(steps, lambda s: s.get("run", "").startswith("python -m lab.summary report"))
        (upload,) = _index(steps, lambda s: s.get("uses", "").startswith("actions/upload-artifact@"))
        order = [restore, ids.index("aggregate"), guard, summary, again, upload]
        self.assertEqual(sorted(order), order)
        self.assertEqual(steps[restore]["with"]["path"], self.MSHA)
        self.assertIn('--dir "$RUNNER_TEMP/lab-report"', steps[guard]["run"])
        self.assertEqual(steps[again], steps[guard])


# --------------------------------------------------------------------------------------------------- the reference

class DocsTests(unittest.TestCase):
    """``docs/lab/REFERENCE.md`` quotes every L1 label as its constant, and names the block's keys and files."""

    TEXT = (ROOT / "docs" / "lab" / "REFERENCE.md").read_text(encoding="utf-8")

    def test_every_l1_label_is_quoted_as_its_constant(self) -> None:
        names = [n for n in dir(notes) if n.startswith("L1_") and isinstance(getattr(notes, n), str)]
        self.assertGreaterEqual(len(names), 15)
        for name in names:
            with self.subTest(label=name):
                self.assertIn(f"- `{name}`: {getattr(notes, name)}\n", self.TEXT)
        for key in ("public_msha", "model_measurement_msha"):
            self.assertIn(f"- `NOTES.{key}`: {notes.NOTES[key]}\n", self.TEXT)

    def test_the_block_s_keys_files_and_cache(self) -> None:
        from lab import request as lab_request

        for key in lab_request._L1_KEYS:
            self.assertIn(f"| `{key}` |", self.TEXT)
        for text in ("`l1-<model>-q<k>`", "`ledger-<mine>.jsonl`", "`records.jsonl`", "`kind: lab_l1_run`",
                     "`lab-msha-<sha16>`", "`lab.msha guard`", "`lab/templates/latency.json`",
                     "`lab/requests/latency-001.json`"):
            self.assertIn(text, self.TEXT)


# --------------------------------------------------------------------------------------------------- request and plan

class RequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(LAB_MANIFEST)

    def test_the_template_is_l001_s_settings(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lab-l1-req-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "lab" / "requests").mkdir(parents=True)
        shutil.copy(LAB_MANIFEST, tmp / "lab" / "models.json")
        shutil.copy(ROOT / "lab" / "models.lock.json", tmp / "lab" / "models.lock.json")
        target = tmp / "lab" / "requests" / "latency-001.json"
        shutil.copy(TEMPLATE, target)
        request = load_request(target, self.manifest, strict_location=True, root=tmp)
        self.assertEqual(request.name, "latency-001")
        self.assertIn("lab/requests/latency-001.json", request.data["purpose"])
        self.assertEqual(request.data["models"], ["a-0p5b", "a-1p5b", "a-4b"])
        self.assertEqual(request.data["experiments"]["l1"], {"models": ["a-0p5b", "a-1p5b", "a-4b"], "minutes": 300,
                                                             "questions": 5, "bootstrap_b": 10000,
                                                             "bootstrap_seed": 1})
        plan = lab_plan.build_plan(request, self.manifest, "unknown")
        self.assertEqual((len(plan["units"]), len(plan["shards"]), plan["job_minutes"], plan["max_parallel"]),
                         (15, 15, 330, 15))
        self.assertEqual({(s["planned_minutes"], s["timeout_minutes"]) for s in plan["shards"]}, {(300, 325)})
        self.assertEqual(sorted((u["model"], u["params"]["slot"]) for u in plan["units"]),
                         [(m, k) for m in ("a-0p5b", "a-1p5b", "a-4b") for k in range(1, 6)])
        self.assertEqual({u["params"]["slots"] for u in plan["units"]}, {5})
        self.assertFalse((ROOT / "lab" / "requests" / "latency-001.json").exists())

    def test_every_block_error(self) -> None:
        def check(block: dict[str, Any], path: str, problem: str | None = None) -> None:
            doc = l1_request()
            doc["experiments"]["l1"] = block
            with self.assertRaises(RequestError) as caught:
                validate(doc, self.manifest)
            self.assertEqual(caught.exception.path, path)
            if problem is not None:
                self.assertEqual(caught.exception.problem, problem)

        base = {"minutes": 300, "questions": 5}
        check({**base, "questions": 4}, "$.experiments.l1.questions", L1_QUESTIONS_PROBLEM)
        check({**base, "questions": 6}, "$.experiments.l1.questions", L1_QUESTIONS_PROBLEM)
        check({"minutes": 300}, "$.experiments.l1.questions")
        check({**base, "extra": 1}, "$.experiments.l1.extra")
        check({**base, "minutes": 400}, "$.experiments.l1.minutes")
        check({**base, "bootstrap_b": 10}, "$.experiments.l1.bootstrap_b")
        doc = {**l1_request(), "provider": "fake", "models": ["fake-a"]}
        self.assertEqual(validate(doc, self.manifest)["experiments"]["l1"]["models"], ["fake-a"])   # plumbing
        defaults = validate({**l1_request(), "experiments": {"l1": dict(base)}}, self.manifest)
        self.assertEqual((defaults["experiments"]["l1"]["bootstrap_b"], defaults["experiments"]["l1"]
                          ["bootstrap_seed"]), (10000, 1))

    def test_a_hosted_model_is_refused(self) -> None:
        manifest = json.loads(LAB_MANIFEST.read_text(encoding="utf-8"))
        manifest["models"]["big-hosted"] = {"kind": "hosted", "model": "lab-hosted-test", "context_tokens": 32768,
                                            "response_format": "json_schema",
                                            "price": {"per_mtok_in": 1.0, "per_mtok_out": 2.0}, "deadline_s": 300,
                                            "max_retries": 2}
        tmp = Path(tempfile.mkdtemp(prefix="lab-l1-hosted-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copy(ROOT / "lab" / "models.lock.json", tmp / "models.lock.json")
        loaded = load_manifest(write_json(tmp / "models.json", manifest))
        doc = l1_request()
        doc["models"] = ["a-0p5b", "big-hosted"]
        doc["experiments"]["l1"]["models"] = ["big-hosted"]
        with self.assertRaises(RequestError) as caught:
            validate(doc, loaded)
        self.assertEqual(caught.exception.problem, MSHA_HOSTED)


if __name__ == "__main__":
    unittest.main()
