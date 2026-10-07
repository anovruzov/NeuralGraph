"""Shards end to end against the collective's fake OpenAI-compatible server: dry runs, participation, timeouts,
signals, sealing, the environment allowlist and the responder. Nothing here measures a model: every server is a
fake, and every record says ``plumbing``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from lab import shard as lab_shard
from lab import units
from lab.notes import (BUDGET_EXHAUSTED, E3_FAILURES, HARNESS_INTERRUPTED, HARNESS_USAGE, KILLED_BY_SIGNAL,
                       LOW_PARTICIPATION, NO_MODEL_CALLS, RESULT_CONTRADICTS_EXIT, RESULT_MISSING, SHARD_INTERRUPTED,
                       TIMED_OUT, UNEXPECTED_EXIT)
from lab.responder import Responder, ResponderError
from mycelic.collective import schemacheck
from mycelic.collective.experiments.e3_latency import WORKLOADS
from mycelic.collective.inference.tasks import data_block
from mycelic.collective.packs.loader import load_pack
from tests.lab.helpers import (MANIFEST_TEST, ROOT, git, kill_mentioning, lab_cli, make_plan, pids_mentioning,
                               plumbing_min, wait_until)

ARG_RE = re.compile(r"--[a-z][a-z0-9-]*=.*")
STATUS_LINE_RE = re.compile(r"lab: unit [a-z0-9-]+ status [a-z_]+ class [a-z-]+ exit (-?[0-9]+|none) "
                            r"wall_s [0-9]+\.[0-9]")


def _request(models: list[str], *, e3: bool = True, g0: bool = True) -> dict[str, Any]:
    obj = plumbing_min()
    obj["models"] = models
    blocks = {}
    if g0:
        blocks["g0"] = {**obj["experiments"]["g0"], "models": models}
    if e3:
        blocks["e3"] = obj["experiments"]["e3"]
    obj["experiments"] = blocks
    return obj


def _argv_ok(argv: list[str]) -> bool:
    return (len(argv) > 3 and argv[1] == "-m" and argv[2].startswith("mycelic.collective.experiments.")
            and all(ARG_RE.fullmatch(a) for a in argv[3:]) and not any("allow-dirty" in a for a in argv))


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-shard-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))


# --------------------------------------------------------------------------------------------------- dry runs

class PlumbingDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-dryrun-"))
        cls.out = cls.tmp / "D"
        cls.done = lab_cli("lab.dryrun", "--request", "tests/lab/data/requests/plumbing-min.json",
                           "--out", str(cls.out))

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def _units(self) -> list[dict[str, Any]]:
        return [_json(p) for p in sorted(self.out.glob("shards/*/units/*/unit.json"))]

    def test_exit_and_one_status_line_per_unit(self) -> None:
        self.assertEqual(self.done.returncode, 0, self.done.stderr)
        lines = [line for line in self.done.stdout.splitlines() if line.startswith("lab: ")]
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(STATUS_LINE_RE.fullmatch(line) for line in lines), lines)
        self.assertEqual(self.done.stdout.splitlines()[0], "plan: 3 units in 2 shards (plumbing)")
        self.assertEqual(len(self.done.stdout.splitlines()), 4, self.done.stdout)

    def test_units_are_ok_plumbing(self) -> None:
        records = self._units()
        self.assertEqual(sorted(r["unit"] for r in records), ["e3-fake-a", "e3-fake-b", "g0-fake-b"])
        for r in records:
            with self.subTest(unit=r["unit"]):
                self.assertEqual((r["status"], r["measurement_class"], r["class_reason"]), ("ok", "plumbing",
                                                                                          "fake_kind"))
                self.assertIn("plumbing", r["notes"])
                self.assertIn("synthetic", r["notes"])
                self.assertEqual(r["exit_code"], 0)
                self.assertTrue(_argv_ok(r["argv"]), r["argv"])
                self.assertGreater(r["fake_rows"], 0)
                self.assertEqual(r["fake_rows"], r["ledger_rows"])
                self.assertIsNotNone(r["participation"])
        e3 = [r for r in records if r["experiment"] == "e3"]
        self.assertTrue(all("runner_hardware" in r["notes"] and r["harness_measurement"] is False for r in e3))

    def test_shards_have_status_and_provenance(self) -> None:
        shards = sorted(self.out.glob("shards/*"))
        self.assertEqual([s.name for s in shards], ["s001-fake-a", "s002-fake-b"])
        for shard in shards:
            with self.subTest(shard=shard.name):
                provenance = _json(shard / "provenance.json")
                self.assertEqual(tuple(sorted(provenance)), tuple(sorted(lab_shard.PROVENANCE_KEYS)))
                self.assertEqual(provenance["result_class"], "plumbing")
                self.assertTrue(provenance["banner"].startswith("Plumbing check"))
                self.assertTrue(provenance["complete"])
                self.assertFalse(provenance["interrupted"])
                self.assertEqual(provenance["exit_code"], 0)
                self.assertEqual(provenance["server"]["implementation"], "FakeOpenAIServer")
                self.assertIs(provenance["code"]["collective"]["code_dirty"], False)
                status = _json(shard / "status.json")
                self.assertEqual(tuple(sorted(status)), tuple(sorted(lab_shard.STATUS_KEYS)))
                self.assertEqual((status["steps"], status["failed_step"], status["missing_units"]),
                                 ({"run": "success"}, None, []))
                self.assertEqual(set(status["units"].values()), {"ok"})

    def test_g0_says_models_fake_false_but_the_lab_says_plumbing(self) -> None:
        record = _json(next(self.out.glob("shards/*/units/g0-fake-b/unit.json")))
        leakage = _json(next(self.out.glob(f"shards/*/runs/g0/{record['run_id']}/leakage.json")))
        self.assertFalse(leakage["models_fake"])
        self.assertTrue(leakage["passed"])
        self.assertGreater(record["fake_rows"], 0)
        self.assertEqual((record["measurement_class"], record["class_reason"]), ("plumbing", "fake_kind"))
        self.assertEqual(record["participation"]["required"], ["extract_claims"])

    def test_recorded_hashes_match_the_files(self) -> None:
        import hashlib
        checked = 0
        for shard in self.out.glob("shards/*"):
            maps = [_json(shard / "status.json")["files"]]
            maps += [_json(p)["files"] for p in shard.glob("units/*/unit.json")]
            for files in maps:
                for rel, meta in files.items():
                    data = (shard / rel).read_bytes()
                    self.assertEqual((hashlib.sha256(data).hexdigest(), len(data)), (meta["sha256"], meta["bytes"]))
                    checked += 1
        self.assertGreater(checked, 10)

    def test_nothing_private_or_scratch_is_kept(self) -> None:
        for path in self.out.rglob("*"):
            rel = path.relative_to(self.out).as_posix()
            self.assertNotRegex(rel, r"(^|/)(private|work|followup)(/|$)")
            self.assertNotIn(".sqlite3", rel)
        routing = [p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if "routing" in p.parts]
        self.assertTrue(routing)
        self.assertTrue(all(re.fullmatch(r"shards/[^/]+/routing(/[a-z0-9-]+\.json)?", r) for r in routing), routing)
        dirty = git("status", "--porcelain", "--", "mycelic", cwd=ROOT)
        self.assertEqual(dirty, "")


class ParticipationDryRunTests(unittest.TestCase):
    """fake-fail answers every call with HTTP 401: E3 still exits 0 (it counts failures) and G0 still passes (it
    falls back to the lexical extractor), so only the participation check stands between them and ``ok``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-fail-"))
        request = cls.tmp / "fail-run.json"
        request.write_text(json.dumps(_request(["fake-fail"])), encoding="utf-8")
        cls.done = lab_cli("lab.dryrun", "--request", str(request), "--out", str(cls.tmp / "D"),
                          "--manifest", str(MANIFEST_TEST))
        cls.records = {p.parent.name: _json(p) for p in (cls.tmp / "D").glob("shards/*/units/*/unit.json")}

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def test_dry_run_exits_one(self) -> None:
        self.assertEqual(self.done.returncode, 1, self.done.stdout + self.done.stderr)

    def test_e3_invalid(self) -> None:
        record = self.records["e3-fake-fail"]
        self.assertEqual((record["exit_code"], record["status"]), (0, "invalid"))
        reason = record["status_reason"]
        self.assertTrue(reason.startswith(LOW_PARTICIPATION) or reason.startswith(E3_FAILURES), reason)
        self.assertRegex(reason, r"ok share 0\.0 below 0\.95|[0-9]+ of [0-9]+ measured requests failed")

    def test_g0_invalid_naming_extract_claims(self) -> None:
        record = self.records["g0-fake-fail"]
        self.assertEqual((record["exit_code"], record["status"]), (0, "invalid"))
        self.assertIn("extract_claims", record["status_reason"])
        self.assertEqual(record["participation"]["tasks"]["extract_claims"]["ok"], 0)


# --------------------------------------------------------------------------------------------------- run_unit

class RunUnitTests(TempDirTest):
    def _unit(self, plan: dict[str, Any], uid: str) -> dict[str, Any]:
        return next(u for u in plan["units"] if u["unit"] == uid)

    def test_timeout_kills_the_group(self) -> None:
        plan, _ = make_plan(self.tmp, _request(["fake-slow"], e3=False))
        pids: list[int] = []
        started = time.monotonic()
        record = units.run_unit(self._unit(plan, "g0-fake-slow"), plan, self.tmp / "OUT", timeout_s=2.0,
                                provider_override=None, on_start=pids.append)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual((record["status"], record["status_reason"]), ("timed_out", TIMED_OUT))

        def gone() -> bool:
            try:
                os.killpg(pids[0], 0)
            except ProcessLookupError:
                return True
            return False

        self.assertTrue(wait_until(gone, 5))
        self.assertFalse((self.tmp / "OUT" / "work").exists() and any((self.tmp / "OUT" / "work").iterdir()))

    def test_environment_allowlist(self) -> None:
        plan, _ = make_plan(self.tmp, _request(["fake-a"], g0=False))
        sentinels = {"GH_TOKEN": "Qz01SecretGh", "GITHUB_TOKEN": "Qz02SecretGithub",
                     "MYCELIC_LAB_HOSTED_API_KEY": "Qz03SecretHosted", "MYCELIC_LAB_OPENFDA_API_KEY": "Qz04SecretFda"}
        environs: list[bytes] = []

        def capture(pid: int) -> None:
            # exec has passed its point of no return when Popen returns, so the old environment is gone; until the
            # kernel has laid out the new stack the file reads empty, so wait for the first non-empty read
            path = Path(f"/proc/{pid}/environ")
            wait_until(lambda: path.read_bytes() != b"", 5, interval=0.001)
            environs.append(path.read_bytes())

        with mock.patch.dict(os.environ, sentinels):
            record = units.run_unit(self._unit(plan, "e3-fake-a"), plan, self.tmp / "OUT", timeout_s=120,
                                    provider_override=None, on_start=capture)
        self.assertEqual(record["status"], "ok", record["status_reason"])
        names = [entry.split(b"=", 1)[0].decode() for entry in environs[0].split(b"\0") if entry]
        self.assertIn("PATH", names)
        self.assertIn(b"PYTHONUNBUFFERED=1", environs[0].split(b"\0"))
        self.assertTrue(set(names) <= set(units.ENV_ALLOWLIST) | {"PYTHONUNBUFFERED"}, names)
        for value in sentinels.values():
            self.assertNotIn(value.encode(), environs[0])

    def test_run_process_kills_a_sleeping_grandchild(self) -> None:
        script = ("import subprocess, sys, time; "
                  "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                  "print(c.pid, flush=True); time.sleep(60)")
        out, err = self.tmp / "out.log", self.tmp / "err.log"
        result = units.run_process([sys.executable, "-c", script], {"PATH": os.environ["PATH"]}, cwd=self.tmp,
                                   timeout_s=1.0, stdout_path=out, stderr_path=err)
        self.assertTrue(result.timed_out)
        self.assertTrue(out.read_text().split()[0].isdigit(), "the grandchild was started")

        def gone() -> bool:
            try:
                os.killpg(result.pgid, 0)
            except ProcessLookupError:
                return True
            return False

        self.assertTrue(wait_until(gone, 5))

    def test_run_process_kills_stragglers_after_a_normal_exit(self) -> None:
        script = ("import subprocess, sys; "
                  "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); print(c.pid)")
        result = units.run_process([sys.executable, "-c", script], {"PATH": os.environ["PATH"]}, cwd=self.tmp,
                                   timeout_s=30, stdout_path=self.tmp / "o.log", stderr_path=self.tmp / "e.log")
        self.assertEqual((result.exit_code, result.timed_out), (0, False))

        def gone() -> bool:
            try:
                os.killpg(result.pgid, 0)
            except ProcessLookupError:
                return True
            return False

        self.assertTrue(wait_until(gone, 5))

    def test_cap_log(self) -> None:
        log = self.tmp / "big.log"
        log.write_bytes(b"a" * 10 + b"b" * units.LOG_CAP_BYTES)
        self.assertEqual(units.cap_log(log), {"bytes": units.LOG_CAP_BYTES, "truncated": True})
        self.assertEqual(log.read_bytes(), b"b" * units.LOG_CAP_BYTES)
        self.assertEqual(units.cap_log(log), {"bytes": units.LOG_CAP_BYTES, "truncated": False})
        self.assertIsNone(units.cap_log(self.tmp / "absent.log"))

    def test_build_argv_shape(self) -> None:
        plan, _ = make_plan(self.tmp, _request(["fake-a"]))
        for unit in plan["units"]:
            argv = units.build_argv(unit, self.tmp / "OUT", self.tmp / "OUT" / "routing" / "x.json")
            self.assertTrue(_argv_ok(argv), argv)


# --------------------------------------------------------------------------------------------------- pure parts

class StatusMapTests(unittest.TestCase):
    def test_exit_code_table(self) -> None:
        e3_ok = {"kind": "e3", "cells": []}
        g0_pass, g0_fail = {"kind": "g0_leakage", "passed": True}, {"kind": "g0_leakage", "passed": False}
        rows = [
            ("e3", 0, False, e3_ok, ("ok", None)),
            ("e3", 0, False, None, ("failed", RESULT_MISSING)),
            ("e3", 0, False, {"kind": "g0_leakage"}, ("failed", RESULT_MISSING)),
            ("e3", 1, False, e3_ok, ("failed", UNEXPECTED_EXIT)),
            ("e3", 2, False, None, ("failed", HARNESS_USAGE)),
            ("e3", 0, True, e3_ok, ("timed_out", TIMED_OUT)),
            ("g0", 0, False, g0_pass, ("ok", None)),
            ("g0", 1, False, g0_fail, ("result_fail", None)),
            ("g0", 1, False, None, ("failed", RESULT_MISSING)),
            ("g0", 0, False, None, ("failed", RESULT_MISSING)),
            ("g0", 0, False, {"kind": "g0_leakage", "passed": "yes"}, ("failed", RESULT_MISSING)),
            ("g0", 0, False, g0_fail, ("failed", RESULT_CONTRADICTS_EXIT)),
            ("g0", 1, False, g0_pass, ("failed", RESULT_CONTRADICTS_EXIT)),
            ("g0", 2, False, g0_fail, ("failed", HARNESS_USAGE)),
            ("g0", 3, False, g0_pass, ("failed", UNEXPECTED_EXIT)),
            ("g0", 130, False, None, ("interrupted", HARNESS_INTERRUPTED)),
            ("e3", -2, False, None, ("interrupted", HARNESS_INTERRUPTED)),
            ("g0", -9, False, g0_pass, ("failed", KILLED_BY_SIGNAL)),
            ("g0", -15, True, None, ("timed_out", TIMED_OUT)),
            ("g0", 1, True, g0_fail, ("timed_out", TIMED_OUT)),
        ]
        for experiment, code, timed_out, result, expected in rows:
            with self.subTest(experiment=experiment, code=code, timed_out=timed_out):
                self.assertEqual(units.harness_status(experiment, code, timed_out, result), expected)


def _row(task: str, attempt: int, ok: bool, fake: bool = True) -> dict[str, Any]:
    return {"task": task, "attempt": attempt, "ok": ok, "fake_marker": fake}


class ParticipationTests(unittest.TestCase):
    def test_repair_rows_count_once_per_call(self) -> None:
        rows = [_row("extract_claims", 1, False), _row("extract_claims", 2, True)] * 20
        record, problem = units.participation("g0", rows, ["extract_claims"], None)
        self.assertIsNone(problem)
        self.assertEqual(record["tasks"]["extract_claims"], {"attempted": 20, "ok": 20, "share": 1.0})

    def test_boundary_refusals_count_as_failed_calls(self) -> None:
        rows = [_row("extract_claims", 1, True)] * 19 + [_row("extract_claims", 0, False)] * 2
        record, problem = units.participation("g0", rows, ["extract_claims"], None)
        self.assertEqual(record["tasks"]["extract_claims"]["attempted"], 21)
        self.assertTrue(problem.startswith(LOW_PARTICIPATION), problem)
        self.assertIn("extract_claims ok share 0.904762 below 0.95", problem)

    def test_threshold_is_inclusive(self) -> None:
        rows = [_row("extract_claims", 1, True)] * 19 + [_row("extract_claims", 1, False)]
        self.assertIsNone(units.participation("g0", rows, ["extract_claims"], None)[1])

    def test_optional_task_without_calls_is_fine_required_is_not(self) -> None:
        rows = [_row("extract_claims", 1, True)] * 5
        record, problem = units.participation("g0", rows, ["extract_claims"], None)
        self.assertIsNone(problem)
        self.assertNotIn("judge_record", record["tasks"])
        record, problem = units.participation("g0", [_row("judge_record", 1, True)], ["extract_claims"], None)
        self.assertEqual(problem, f"{NO_MODEL_CALLS}: extract_claims")
        _, problem = units.participation("g0", rows + [_row("judge_record", 1, False)], ["extract_claims"], None)
        self.assertTrue(problem.startswith(f"{LOW_PARTICIPATION}: judge_record"), problem)

    def test_e3_failure_share(self) -> None:
        tasks = ["e3_short_answer"]
        rows = [_row("e3_short_answer", 1, True)] * 100
        cells = {"cells": [{"measured": 50, "failures": {}}, {"measured": 50, "failures": {"http_5xx": 5}}]}
        self.assertIsNone(units.participation("e3", rows, tasks, cells)[1])
        cells["cells"][1]["failures"]["timeout"] = 1
        record, problem = units.participation("e3", rows, tasks, cells)
        self.assertEqual(problem, f"{E3_FAILURES}: 6 of 100 measured requests failed")
        self.assertEqual(record["e3"], {"measured": 100, "failed": 6, "share": 0.06, "max_share": 0.05})


class MeasurementClassTests(unittest.TestCase):
    def test_classes(self) -> None:
        real = [_row("t", 1, True, fake=False)]
        self.assertEqual(units.measurement_class("none", None, []), ("no-model", "no_model"))
        self.assertEqual(units.measurement_class("fake", None, real), ("plumbing", "fake_kind"))
        self.assertEqual(units.measurement_class("gguf", "fake", real), ("plumbing", "provider_override"))
        self.assertEqual(units.measurement_class("gguf", None, real + [_row("t", 1, True)]),
                         ("plumbing", "fake_marker"))
        self.assertEqual(units.measurement_class("gguf", None, real), ("unverified", "model_rule_pending"))


class ResponderTests(unittest.TestCase):
    def _request(self, name: str | None, payload: Any = None) -> dict[str, Any]:
        obj: dict[str, Any] = {"messages": [{"role": "system", "content": "x"},
                                            {"role": "user", "content": data_block(payload or {"text": "t"})}]}
        if name is not None:
            obj["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": {}}}
        return obj

    def test_refusals(self) -> None:
        pack = load_pack("device_quality")
        with self.assertRaises(ResponderError):
            Responder(pack)(self._request("no_such_task"))
        with self.assertRaises(ResponderError):
            Responder(pack)(self._request(None))
        with self.assertRaises(ResponderError):
            Responder(None)(self._request("extract_claims", {"text": "t", "language": "en"}))
        with self.assertRaises(ResponderError):
            Responder(pack)({"response_format": {"type": "json_schema", "json_schema": {"name": "e3_short_answer"}},
                             "messages": []})
        with self.assertRaises(ResponderError) as caught:
            Responder(pack)(self._request("Qz55SnTl"))
        self.assertNotIn("Qz55SnTl", str(caught.exception))

    def test_e3_replies_match_the_workload_schemas(self) -> None:
        for workload, (task, schema, _) in WORKLOADS.items():
            with self.subTest(workload=workload):
                reply = Responder(None)(self._request(task.name))
                self.assertEqual(schemacheck.compile(schema).validate(reply), [])

    def test_pack_tasks_answer(self) -> None:
        reply = Responder(load_pack("device_quality"))(self._request("extract_claims",
                                                                     {"text": "The pump failed.", "language": "en"}))
        self.assertIn("claims", reply)


# --------------------------------------------------------------------------------------------------- shard CLI

class ShardCliTests(TempDirTest):
    def _plan(self, models: list[str], **kwargs: Any) -> Path:
        _, path = make_plan(self.tmp, _request(models, **kwargs))
        return path

    def test_refusals(self) -> None:
        plan = self._plan(["fake-a"], g0=False)
        for name in ("units", "runs"):
            out = self.tmp / f"out-{name}"
            (out / name).mkdir(parents=True)
            r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s001-fake-a", "--out", str(out))
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertEqual(sorted(p.name for p in out.iterdir()), [name])
        inside = ROOT / "mycelic" / "lab-test-out-never-created"
        r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s001-fake-a", "--out", str(inside))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(inside.exists())
        r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s009-fake-a", "--out", str(self.tmp / "u"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.tmp / "u").exists())
        r = lab_cli("lab.shard", "run", "--plan", str(self.tmp / "missing.json"), "--shard", "s001-fake-a",
                    "--out", str(self.tmp / "m"))
        self.assertEqual(r.returncode, 2)
        doctored = _json(plan)
        doctored["units"][0]["unit"] = "../../escape"
        (self.tmp / "doctored.json").write_text(json.dumps(doctored), encoding="utf-8")
        r = lab_cli("lab.shard", "run", "--plan", str(self.tmp / "doctored.json"), "--shard", "s001-fake-a",
                    "--out", str(self.tmp / "d"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.tmp / "d").exists())
        r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s001-fake-a", "--out", str(self.tmp / "n"),
                    "--deadline-epoch", "nan")
        self.assertEqual(r.returncode, 2)
        r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s001-fake-a", "--out", str(self.tmp / "p"),
                    "--provider", "hosted")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.tmp / "n").exists() or (self.tmp / "p").exists())

    def test_server_shard_needs_the_fake_provider(self) -> None:
        obj = _request(["tiny-gguf"], g0=False)
        obj["provider"] = "llama-server"
        _, plan = make_plan(self.tmp, obj)
        r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s001-tiny-gguf", "--out", str(self.tmp / "g"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no model server", r.stderr)
        self.assertFalse((self.tmp / "g").exists())
        r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s001-tiny-gguf", "--out", str(self.tmp / "o"),
                    "--provider", "fake")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        record = _json(self.tmp / "o" / "units" / "e3-tiny-gguf" / "unit.json")
        self.assertEqual((record["status"], record["measurement_class"], record["class_reason"]),
                         ("ok", "plumbing", "provider_override"))
        self.assertEqual(_json(self.tmp / "o" / "provenance.json")["result_class"], "plumbing")

    def test_prepared_out_runs_and_budget_skips(self) -> None:
        plan = self._plan(["fake-a"])
        out = self.tmp / "prepared"
        (out / "provision").mkdir(parents=True)
        (out / "server").mkdir()
        r = lab_cli("lab.shard", "run", "--plan", str(plan), "--shard", "s001-fake-a", "--out", str(out),
                    "--deadline-epoch", str(time.time() + 60))
        self.assertEqual(r.returncode, 1, r.stderr)
        records = [_json(p) for p in sorted(out.glob("units/*/unit.json"))]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual((record["status"], record["status_reason"]), ("skipped", BUDGET_EXHAUSTED))
            self.assertEqual((record["argv"], record["exit_code"], record["files"], record["participation"]),
                             ([], None, {}, None))
        self.assertEqual(len([line for line in r.stdout.splitlines() if line.startswith("lab: unit ")]), 2)

    def _slow_run(self) -> tuple[subprocess.Popen[str], Path]:
        plan = self._plan(["fake-slow"])
        out = self.tmp / "slow"
        proc = subprocess.Popen([sys.executable, "-m", "lab.shard", "run", "--plan", str(plan),
                                 "--shard", "s001-fake-slow", "--out", str(out)], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        started = wait_until(lambda: (out / "units" / "g0-fake-slow" / "stdout.log").exists()
                             and pids_mentioning(str(out / "work")), 60)
        self.assertTrue(started)
        time.sleep(0.5)
        return proc, out

    def test_sigterm_interrupts(self) -> None:
        proc, out = self._slow_run()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 1)
        g0 = _json(out / "units" / "g0-fake-slow" / "unit.json")
        e3 = _json(out / "units" / "e3-fake-slow" / "unit.json")
        self.assertEqual((g0["status"], g0["status_reason"]), ("interrupted", SHARD_INTERRUPTED))
        self.assertEqual((e3["status"], e3["status_reason"]), ("skipped", SHARD_INTERRUPTED))
        provenance = _json(out / "provenance.json")
        self.assertEqual((provenance["interrupted"], provenance["complete"], provenance["exit_code"]), (True, True, 1))
        self.assertTrue(wait_until(lambda: not pids_mentioning(str(out)), 5), pids_mentioning(str(out)))
        self.assertFalse((out / "work" / "g0-fake-slow").exists())

    def test_seal_after_sigkill(self) -> None:
        proc, out = self._slow_run()
        proc.kill()
        proc.communicate(timeout=20)
        kill_mentioning(str(out))
        self.assertTrue((out / "work").exists())
        r = lab_cli("lab.shard", "seal", "--out", str(out), "--shard", "s001-fake-slow",
                    "--step-outcome", "prepare=success", "--step-outcome", "run=cancelled",
                    "--step-outcome", "upload=skipped")
        self.assertEqual(r.returncode, 0, r.stderr)
        status = _json(out / "status.json")
        self.assertEqual(status["steps"], {"prepare": "success", "run": "cancelled", "upload": "skipped"})
        self.assertEqual(status["failed_step"], "run")
        self.assertIn("g0-fake-slow", status["missing_units"])
        self.assertEqual(status["provenance"], {"present": True, "complete": False, "interrupted": False})
        self.assertFalse((out / "work").exists())
        self.assertFalse(any(rel.startswith("work/") for rel in status["files"]))

    def test_seal_without_a_run(self) -> None:
        absent = self.tmp / "absent"
        r = lab_cli("lab.shard", "seal", "--out", str(absent), "--step-outcome", "prepare=failure")
        self.assertEqual(r.returncode, 0, r.stderr)
        status = _json(absent / "status.json")
        self.assertEqual((status["out_existed"], status["steps"], status["failed_step"], status["shard"]),
                         (False, {"prepare": "failure"}, "prepare", None))
        self.assertEqual((status["units"], status["missing_units"], status["files"]), ({}, None, {}))
        prepared = self.tmp / "prepared"
        (prepared / "provision").mkdir(parents=True)
        (prepared / "provision" / "notes.txt").write_text("x")
        r = lab_cli("lab.shard", "seal", "--out", str(prepared), "--shard", "s001-fake-a",
                    "--step-outcome", "prepare=success", "--step-outcome", "run=skipped")
        status = _json(prepared / "status.json")
        self.assertEqual((r.returncode, status["out_existed"], status["shard"], status["failed_step"]),
                         (0, True, "s001-fake-a", None))
        self.assertEqual(list(status["files"]), ["provision/notes.txt"])
        self.assertEqual(status["provenance"], {"present": False, "complete": False, "interrupted": False})

    def test_malformed_seal_arguments_write_nothing(self) -> None:
        for args in (["--step-outcome", "run"], ["--step-outcome", "run=ok"], ["--step-outcome", "Run=success"],
                     ["--step-outcome", "run=success", "--step-outcome", "run=failure"], ["--shard", "../x"]):
            out = self.tmp / "bad"
            with self.subTest(args=args):
                r = lab_cli("lab.shard", "seal", "--out", str(out), *args)
                self.assertEqual(r.returncode, 2)
                self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
