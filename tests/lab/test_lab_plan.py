"""Planning and push discovery: byte-stable plans, first-fit-decreasing shards, GitHub outputs, and the request a push
added, found in throwaway git repositories (a bare ``origin``, a work clone that pushes, a fresh clone as checkout).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from lab.discover import discover
from lab.manifest import CTX_DEFAULTS, load_manifest
from lab.notes import (BAD_REQUEST_NAME, BRANCH_DELETED, CHECKOUT_MISMATCH, DEFAULT_BRANCH, DELETE_ONLY,
                       MERGE_SEVERAL, NO_BASE, NO_REQUEST_CHANGE, NOT_A_BRANCH, SEVERAL_REQUESTS)
from lab import plan as lab_plan
from lab.plan import (MAX_SHARDS, OUTPUT_KEYS, PlanError, check_matrix_size, check_outputs_size, output_size,
                      pack_shards, plan_outputs, run_id, unit_id)
from lab.request import load_request
from mycelic.collective.experiments.common import RUN_ID_RE
from tests.lab.helpers import (LAB_MANIFEST, MANIFEST_TEST, PLUMBING_001, PLUMBING_MIN, ROOT, GitWorld, git, git_run,
                               lab_env, make_plan, plumbing_min, run_plan, write_json)

TIME_KEY_RE = re.compile(r"(^|_)(at|time|date|epoch|ts)(_|$)")
REQUEST_TEXT = PLUMBING_MIN.read_text(encoding="utf-8")


def _keys(obj: Any) -> list[str]:
    if isinstance(obj, dict):
        return [k for key, value in obj.items() for k in (key, *_keys(value))]
    if isinstance(obj, list):
        return [k for value in obj for k in _keys(value)]
    return []


def _unit(uid: str, model: str | None, minutes: int, experiment: str = "g0", needs_secret: bool = False) -> dict:
    return {"unit": uid, "model": model, "kind": "fake" if model else "none", "minutes": minutes,
            "experiment": experiment, "needs_secret": needs_secret}


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-plan-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class DeterminismTests(TempDirTest):
    def test_two_cli_plans_are_byte_identical(self) -> None:
        plans = []
        for name in ("A", "B"):
            r = subprocess.run([sys.executable, "-m", "lab.plan",
                                "--request", "tests/lab/data/requests/plumbing-min.json",
                                "--manifest", "tests/lab/data/manifest-test.json", "--out", str(self.tmp / name)],
                               cwd=ROOT, env=lab_env(), capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "plan: 3 units in 2 shards (plumbing)")
            plans.append((self.tmp / name / "plan.json").read_bytes())
        self.assertEqual(plans[0], plans[1])
        self.assertEqual(subprocess.run(["cmp", str(self.tmp / "A" / "plan.json"), str(self.tmp / "B" / "plan.json")])
                         .returncode, 0)
        plan = json.loads(plans[0])
        self.assertEqual([k for k in _keys(plan) if TIME_KEY_RE.search(k)], [])
        self.assertLessEqual(len(plan["matrix"]["include"]), 256)
        self.assertLess(len(json.dumps(plan["matrix"], separators=(",", ":"), sort_keys=True).encode()), 1000000)
        self.assertEqual([(s["shard"], s["units"], s["timeout_minutes"]) for s in plan["shards"]],
                         [("s001-fake-a", ["e3-fake-a"], 30), ("s002-fake-b", ["g0-fake-b", "e3-fake-b"], 40)])
        self.assertEqual([u["unit"] for u in plan["units"]], ["e3-fake-a", "e3-fake-b", "g0-fake-b"])
        self.assertEqual({u["unit"]: u["shard"] for u in plan["units"]},
                         {"e3-fake-a": "s001-fake-a", "e3-fake-b": "s002-fake-b", "g0-fake-b": "s002-fake-b"})
        self.assertEqual(plan["result_class"], "plumbing")
        self.assertEqual(plan["skipped"], [])
        self.assertEqual(sorted(plan["models"]), ["fake-a", "fake-b"])
        self.assertIsNone(plan["models"]["fake-a"]["lock"])
        self.assertEqual(plan["request"]["sha256"], hashlib.sha256(PLUMBING_MIN.read_bytes()).hexdigest())
        g0 = next(u for u in plan["units"] if u["unit"] == "g0-fake-b")
        self.assertEqual(g0["params"], {"pack": "device_quality", "records": 50, "seed": 7})
        self.assertEqual((g0["seeds"], g0["needs_secret"], g0["env"]), ([7], False, []))

    def test_existing_plan_in_out_is_refused(self) -> None:
        (self.tmp / "plan.json").write_text("{}")
        code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(MANIFEST_TEST),
                                   "--out", str(self.tmp)])
        self.assertEqual(code, 2)
        self.assertEqual((self.tmp / "plan.json").read_text(), "{}")


class ShardPackingTests(unittest.TestCase):
    def test_first_fit_decreasing_by_serving_class_then_minutes_and_id_ties(self) -> None:
        units = [_unit("g0-m", "m", 5), _unit("e1-m", "m", 10, "e1"), _unit("e3-m", "m", 10, "e3"),
                 _unit("e2-m", "m", 10, "e2"), _unit("x1-m", "m", 4, "x1")]
        shards = pack_shards(units, 20)
        self.assertEqual([(s["shard"], s["units"], s["planned_minutes"]) for s in shards],
                         [("s001-m", ["e1-m", "g0-m", "x1-m"], 19), ("s002-m", ["e2-m", "e3-m"], 20)])
        self.assertEqual([s["timeout_minutes"] for s in shards], [44, 45])
        # g0, sim and e3 alone keep G4's placement: e3 last, then -minutes, then the unit id
        units = [_unit("g0-m", "m", 5), _unit("sim-m-s1", "m", 10, "sim"), _unit("e3-m", "m", 10, "e3"),
                 _unit("g0-n", "m", 5), _unit("sim-m-s2", "m", 10, "sim")]
        self.assertEqual([s["units"] for s in pack_shards(units, 20)],
                         [["sim-m-s1", "sim-m-s2"], ["g0-m", "g0-n", "e3-m"]])

    def test_groups_none_label_and_global_numbering(self) -> None:
        units = [_unit("g0-b", "b", 10), _unit("g0-a", "a", 10), _unit("sim", None, 5), _unit("g0-a2", "a", 15)]
        shards = pack_shards(units, 20)
        self.assertEqual([(s["shard"], s["units"]) for s in shards],
                         [("s001-a", ["g0-a2"]), ("s002-a", ["g0-a"]), ("s003-b", ["g0-b"]), ("s004-none", ["sim"])])
        self.assertEqual([s["kind"] for s in shards], ["fake", "fake", "fake", "none"])
        self.assertIsNone(shards[3]["model"])

    def test_needs_secret_splits_groups(self) -> None:
        shards = pack_shards([_unit("a1", "a", 1), _unit("a2", "a", 1, needs_secret=True)], 20)
        self.assertEqual([(s["shard"], s["needs_secret"]) for s in shards], [("s001-a", False), ("s002-a", True)])

    def test_more_than_256_shards(self) -> None:
        units = [_unit(f"g0-m{i}", "m", 20) for i in range(MAX_SHARDS)]
        self.assertEqual(len(pack_shards(units, 20)), MAX_SHARDS)
        with self.assertRaises(PlanError) as caught:
            pack_shards(units + [_unit("g0-z", "m", 20)], 20)
        self.assertEqual((caught.exception.path, caught.exception.problem),
                         ("$.experiments", "the plan needs more than 256 shards"))

    def test_matrix_size_limit(self) -> None:
        check_matrix_size({"include": []})
        with self.assertRaises(PlanError):
            check_matrix_size({"include": ["x" * 1000000]})
        with self.assertRaises(PlanError):
            check_matrix_size({"x": "y"}, limit=len('{"x":"y"}'))

    def test_id_bounds(self) -> None:
        key = "fake-longest-key-0123456"
        self.assertEqual(len(key), 24)
        uid = unit_id("openfda", key, "s2147483647")
        self.assertLessEqual(len(uid), 55)
        rid = run_id(uid, "f" * 64)
        self.assertLessEqual(len(rid), 64)
        self.assertIsNotNone(RUN_ID_RE.fullmatch(rid))
        self.assertEqual(unit_id("e3", "fake-a"), "e3-fake-a")
        self.assertEqual(unit_id("sim", None, "r2"), "sim-r2")
        with self.assertRaises(PlanError):
            unit_id("openfda", key + "x" * 30, "s2147483647")

    def test_run_ids_unique_and_capacity_error(self) -> None:
        manifest = load_manifest(MANIFEST_TEST)
        tmp = Path(tempfile.mkdtemp(prefix="lab-ids-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        obj = plumbing_min()
        obj["models"] = ["fake-a", "fake-b", "fake-fail", "fake-slow", "fake-longest-key-0123456"]
        obj["experiments"]["e3"]["seed"] = 2147483647
        path = write_json(tmp / "ids.json", obj)
        from lab.plan import build_plan
        plan = build_plan(load_request(path, manifest), manifest, "unknown")
        run_ids = [u["run_id"] for u in plan["units"]]
        self.assertEqual(len(run_ids), len(set(run_ids)))
        self.assertTrue(all(RUN_ID_RE.fullmatch(r) and len(r) <= 64 for r in run_ids))
        obj["experiments"]["e3"]["minutes"] = 21
        write_json(path, obj)
        code, out, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "o")])
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("error: $.experiments.e3.minutes: exceeds the shard capacity"), err)


class OutputTests(TempDirTest):
    def _outputs(self, path: Path) -> dict[str, str]:
        lines = path.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("existing"))

    def test_github_output_appends_single_lines(self) -> None:
        gho = self.tmp / "gho"
        gho.write_text("existing=1\n", encoding="utf-8")
        code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(MANIFEST_TEST),
                                   "--out", str(self.tmp / "o"), "--github-output", str(gho)])
        self.assertEqual(code, 0, err)
        lines = gho.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "existing=1")
        self.assertEqual([line.split("=", 1)[0] for line in lines[1:]], list(OUTPUT_KEYS))
        values = dict(line.split("=", 1) for line in lines[1:])
        plan_bytes = (self.tmp / "o" / "plan.json").read_bytes()
        self.assertEqual(values["plan_sha256"], hashlib.sha256(plan_bytes).hexdigest())
        self.assertEqual(values["has_units"], "true")
        self.assertEqual(values["max_parallel"], "2")
        self.assertEqual(values["retention_days"], "1")
        self.assertEqual(values["result_class"], "plumbing")
        self.assertEqual(json.loads(values["matrix"]), json.loads(plan_bytes)["matrix"])

    def test_max_parallel_is_capped_by_shards(self) -> None:
        obj = plumbing_min()
        obj.update(models=["fake-a"], max_parallel=16)
        obj["experiments"]["g0"]["models"] = ["fake-a"]
        path = write_json(self.tmp / "one.json", obj)
        gho = self.tmp / "gho"
        code, out, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST),
                                   "--out", str(self.tmp / "o"), "--github-output", str(gho)])
        self.assertEqual(code, 0, err)
        self.assertEqual(self._outputs(gho)["max_parallel"], "1")

    def test_server_request_matrix(self) -> None:
        obj = plumbing_min()
        obj.update(provider="llama-server", models=["tiny-gguf"])
        obj["experiments"]["g0"]["models"] = ["tiny-gguf"]
        path = write_json(self.tmp / "real.json", obj)
        gho = self.tmp / "gho"
        code, out, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST),
                                   "--out", str(self.tmp / "o"), "--github-output", str(gho)])
        self.assertEqual(code, 0, err)
        outputs = self._outputs(gho)
        self.assertEqual(outputs["result_class"], "real")
        entry = json.loads(outputs["matrix"])["include"][0]
        self.assertEqual((entry["kind"], entry["gguf_key"], entry["model"]), ("gguf", "tiny-gguf", "tiny-gguf"))
        self.assertEqual((entry["hosted"], entry["openfda"]), (False, False))

    def test_provision_entries_and_outputs(self) -> None:
        gho = self.tmp / "gho-fake"
        code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(MANIFEST_TEST),
                                   "--out", str(self.tmp / "fake"), "--github-output", str(gho)])
        self.assertEqual(code, 0, err)
        plan = json.loads((self.tmp / "fake" / "plan.json").read_text())
        self.assertEqual(plan["provision"], [])
        outputs = self._outputs(gho)
        self.assertEqual((outputs["has_provision"], outputs["provision_matrix"]), ("false", '{"include":[]}'))

        manifest = json.loads(MANIFEST_TEST.read_text())
        manifest["models"]["second-gguf"] = {**manifest["models"]["tiny-gguf"], "alias": "second-test"}
        write_json(self.tmp / "m.json", manifest)
        locked = {"repo": "example-org/tiny-test-GGUF", "file": "tiny-test-q4.gguf",
                  "revision": manifest["models"]["tiny-gguf"]["gguf"]["revision"],
                  "commit": manifest["models"]["tiny-gguf"]["gguf"]["revision"], "sha256": "ab" * 32, "size": 10}
        server_lock = {"tag": manifest["server"]["tag"], "asset": manifest["server"]["asset"], "sha256": "cd" * 32}
        write_json(self.tmp / "m.lock.json", {"schema_version": 1, "server": server_lock,
                                              "models": {"second-gguf": locked}})
        obj = plumbing_min()
        obj.update(provider="llama-server", models=["tiny-gguf", "second-gguf"])
        obj["experiments"]["g0"]["models"] = ["second-gguf", "tiny-gguf"]
        path = write_json(self.tmp / "real.json", obj)
        plans = []
        for name in ("A", "B"):
            gho = self.tmp / f"gho-{name}"
            code, out, err = run_plan(["--request", str(path), "--manifest", str(self.tmp / "m.json"),
                                       "--out", str(self.tmp / name), "--github-output", str(gho)])
            self.assertEqual(code, 0, err)
            plans.append((self.tmp / name / "plan.json").read_bytes())
        self.assertEqual(plans[0], plans[1])
        plan = json.loads(plans[0])
        tag = manifest["server"]["tag"]
        self.assertEqual(plan["provision"], [
            {"target": "server", "key": "", "entry": "server", "cache_path": f"server/{tag}",
             "cache_key": f"lab-server-{tag}-" + "cd" * 8,
             "restore_key": f"lab-server-{tag}-" + "cd" * 8, "restore_prefix": f"lab-server-{tag}-"},
            {"target": "gguf", "key": "second-gguf", "entry": "gguf-second-gguf", "cache_path": "gguf/second-gguf",
             "cache_key": "lab-gguf-second-gguf-" + "ab" * 8,
             "restore_key": "lab-gguf-second-gguf-" + "ab" * 8, "restore_prefix": "lab-gguf-second-gguf-"},
            {"target": "gguf", "key": "tiny-gguf", "entry": "gguf-tiny-gguf", "cache_path": "gguf/tiny-gguf",
             "cache_key": "lab-gguf-tiny-gguf-unlocked", "restore_key": "",
             "restore_prefix": "lab-gguf-tiny-gguf-"}])
        outputs = self._outputs(self.tmp / "gho-A")
        self.assertEqual(outputs["has_provision"], "true")
        self.assertEqual(json.loads(outputs["provision_matrix"]), {"include": plan["provision"]})
        self.assertNotIn("\n", outputs["provision_matrix"])

        manifest["models"] = {k: v for k, v in manifest["models"].items() if v["kind"] == "fake"}
        write_json(self.tmp / "m.json", manifest)
        write_json(self.tmp / "m.lock.json", {"schema_version": 1, "server": server_lock, "models": {}})
        code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(self.tmp / "m.json"),
                                   "--out", str(self.tmp / "C")])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads((self.tmp / "C" / "plan.json").read_text())["provision"], [])

    def test_errors_for_request_manifest_and_event(self) -> None:
        bad = write_json(self.tmp / "bad.json", {**plumbing_min(), "job_minutes": 1})
        code, out, err = run_plan(["--request", str(bad), "--manifest", str(MANIFEST_TEST),
                                   "--out", str(self.tmp / "r")])
        error = json.loads((self.tmp / "r" / "plan-error.json").read_text())
        self.assertEqual((code, error["source"], error["path"]), (2, "request", "$.job_minutes"))
        self.assertTrue(error["request"].endswith("bad.json"))
        self.assertIn(f"::error file={error['request']}::$.job_minutes: must be an int", out)

        manifest = json.loads(MANIFEST_TEST.read_text())
        manifest["server"] = None
        write_json(self.tmp / "m.json", manifest)
        shutil.copy(MANIFEST_TEST.with_name("manifest-test.lock.json"), self.tmp / "m.lock.json")
        code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(self.tmp / "m.json"),
                                   "--out", str(self.tmp / "m")])
        error = json.loads((self.tmp / "m" / "plan-error.json").read_text())
        self.assertEqual((code, error["source"], error["path"], error["request"]), (2, "manifest", "$.server", None))
        self.assertEqual(err.splitlines()[0], "error: manifest $.server: required when a gguf model is listed")
        self.assertIn("::error::manifest $.server: required", out)

        (self.tmp / "m.lock.json").write_text('{"schema_version": 2, "server": null, "models": {}}')
        manifest["server"] = json.loads(MANIFEST_TEST.read_text())["server"]
        write_json(self.tmp / "m.json", manifest)
        code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(self.tmp / "m.json"),
                                   "--out", str(self.tmp / "l")])
        error = json.loads((self.tmp / "l" / "plan-error.json").read_text())
        self.assertEqual((code, error["source"], error["path"]), (2, "lock", "$.schema_version"))
        self.assertEqual(err.splitlines()[0], "error: lock $.schema_version: must be 1")

        event = write_json(self.tmp / "event.json", {"inputs": {"request": "lab/requests/../x.json"}})
        code, out, err = run_plan(["--event-path", str(event), "--event-name", "workflow_dispatch",
                                   "--manifest", str(MANIFEST_TEST), "--out", str(self.tmp / "e")])
        error = json.loads((self.tmp / "e" / "plan-error.json").read_text())
        self.assertEqual((code, error["source"], error["path"], error["request"]),
                         (2, "event", "$.inputs.request", None))
        self.assertIn("::error::$.inputs.request: must be lab/requests/<name>.json", out)
        self.assertFalse((self.tmp / "e" / "plan.json").exists())


class OutputBudgetTests(TempDirTest):
    def test_outputs_size_utf16(self) -> None:
        plan, _ = make_plan(self.tmp, plumbing_min())
        entry = plan["matrix"]["include"][0]
        label = "fake-longest-key-0123456"
        plan["shards"] = [{**plan["shards"][0], "shard": f"s{i + 1:03d}-{label}"} for i in range(256)]
        plan["matrix"] = {"include": [{**entry, "shard": f"s{i + 1:03d}-{label}", "model": label, "gguf_key": label}
                                      for i in range(256)]}
        key = f"lab-gguf-{label}-" + "a" * 16
        plan["provision"] = [{"target": "gguf", "key": label, "entry": f"gguf-{label}", "cache_path": f"gguf/{label}",
                              "cache_key": key, "restore_key": key, "restore_prefix": f"lab-gguf-{label}-"}] * 9
        outputs = plan_outputs(plan, "0" * 64) | {"plan_artifact": "x" * 64}
        check_outputs_size(outputs)
        self.assertLess(output_size(outputs), 900_000)
        self.assertEqual(output_size({"ab": "cd"}), len("ab=cd") * 2)

        check_outputs_size({"matrix": "x" * 449_990})
        for outputs in ({"matrix": "x" * 449_997}, {"matrix": "x" * 300_000, "provision_matrix": "y" * 149_995}):
            with self.subTest(sizes=[len(v) for v in outputs.values()]):
                self.assertLess(sum(len(f"{k}={v}".encode("utf-8")) for k, v in outputs.items()), 900_000)
                with self.assertRaises(PlanError) as caught:
                    check_outputs_size(outputs)
                self.assertEqual((caught.exception.path, caught.exception.problem),
                                 ("$.experiments", "the job matrix would be too large"))
        check_outputs_size({"matrix": "x" * 300_000})
        check_outputs_size({"provision_matrix": "y" * 149_995})

        check_matrix_size({"include": ["x" * 499_970]})
        with self.assertRaises(PlanError):
            check_matrix_size({"include": ["x" * 499_990]})

    def test_build_plan_checks_the_outputs(self) -> None:
        with mock.patch.object(lab_plan, "MAX_OUTPUT_BYTES", 1000):
            code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(MANIFEST_TEST),
                                       "--out", str(self.tmp / "o")])
        self.assertEqual(code, 2)
        self.assertEqual(err.splitlines()[0], "error: $.experiments: the job matrix would be too large")
        code, out, err = run_plan(["--request", str(PLUMBING_MIN), "--manifest", str(MANIFEST_TEST),
                                   "--out", str(self.tmp / "p")])
        self.assertEqual(code, 0, err)

    def test_shipped_requests_plan(self) -> None:
        manifest = load_manifest(LAB_MANIFEST)
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            request = load_request(PLUMBING_001.relative_to(ROOT), manifest, strict_location=True)
        finally:
            os.chdir(cwd)
        plan = lab_plan.build_plan(request, manifest, "unknown")
        self.assertEqual(request.path, "lab/requests/plumbing-001.json")
        self.assertEqual([(s["shard"], s["kind"], s["units"], s["planned_minutes"], s["timeout_minutes"])
                          for s in plan["shards"]],
                         [("s001-fake-a", "fake", ["sim-fake-a-s1", "e1-fake-a-r1"], 20, 45),
                          ("s002-fake-a", "fake", ["e1-fake-a-r2", "e1-fake-a-r3", "e2-fake-a", "e3-fake-a"], 20, 45),
                          ("s003-fake-b", "fake", ["g0-fake-b", "e1-fake-b-r1", "e1-fake-b-r2"], 20, 45),
                          ("s004-fake-b", "fake", ["e1-fake-b-r3", "e3-fake-b"], 10, 35),
                          ("s005-none", "none", ["x1"], 5, 30)])
        self.assertEqual([e["openfda"] for e in plan["matrix"]["include"]], [False] * 5)
        self.assertEqual((plan["result_class"], plan["provision"], plan["retention_days"]), ("plumbing", [], 7))
        self.assertEqual(sorted(p.name for p in (ROOT / "lab" / "requests").iterdir()),
                         ["README.md", "plumbing-001.json"])

        requests = self.tmp / "lab" / "requests"
        requests.mkdir(parents=True)
        shutil.copy(ROOT / "lab" / "templates" / "check.json", requests / "check-001.json")
        request = load_request(requests / "check-001.json", manifest, strict_location=True, root=self.tmp)
        plan = lab_plan.build_plan(request, manifest, "unknown")
        self.assertEqual([(s["shard"], s["kind"], s["model"], s["units"]) for s in plan["shards"]],
                         [("s001-a-0p5b", "gguf", "a-0p5b", ["e3-a-0p5b"])])
        self.assertEqual(plan["result_class"], "real")
        self.assertEqual([(p["target"], p["entry"]) for p in plan["provision"]],
                         [("server", "server"), ("gguf", "gguf-a-0p5b")])
        for entry in plan["provision"]:
            self.assertEqual(entry["cache_key"], entry["restore_key"] or entry["restore_prefix"] + "unlocked")

        shutil.copy(ROOT / "lab" / "templates" / "smoke.json", requests / "smoke-001.json")
        request = load_request(requests / "smoke-001.json", manifest, strict_location=True, root=self.tmp)
        plan = lab_plan.build_plan(request, manifest, "unknown")
        (model,) = request.data["models"]
        experiments = {u["unit"]: u["experiment"] for u in plan["units"]}
        self.assertEqual([(s["kind"], s["model"], [experiments[u] for u in s["units"]], s["planned_minutes"],
                           s["timeout_minutes"]) for s in plan["shards"]],
                         [("gguf", model, ["sim", "g0", "e3"], 305, 330)])
        self.assertEqual(sorted(p.name for p in (ROOT / "lab" / "templates").iterdir()),
                         ["check.json", "hosted-comparison.json", "main.json", "openfda-replay.json", "smoke.json"])


class CentralContextTests(unittest.TestCase):
    """An E2 unit's ``central_context_tokens``: the context one central request gets."""

    def test_by_central_and_model_kind(self) -> None:
        manifest = load_manifest(MANIFEST_TEST)
        models = {**manifest.models, "tiny-gguf": {**manifest.models["tiny-gguf"], "e2_ctx_per_slot": 8192},
                  "h-a": {**manifest.models["h-a"], "context_tokens": 65536}}
        changed = dataclasses.replace(manifest, models=models)
        self.assertEqual(lab_plan.central_context_tokens("self", "tiny-gguf", changed), 8192)
        self.assertNotIn("e2_ctx_per_slot", changed.models["fake-a"])
        self.assertEqual(lab_plan.central_context_tokens("self", "fake-a", changed), CTX_DEFAULTS["e2_ctx_per_slot"])
        self.assertEqual(lab_plan.central_context_tokens("h-a", "fake-a", changed), 65536)
        self.assertEqual(lab_plan.central_context_tokens("h-a", "tiny-gguf", changed), 65536)

    def test_in_the_unit_params(self) -> None:
        obj = plumbing_min()
        obj["experiments"] = {"e2": {"minutes": 5, "pack": "device_quality", "plant": "plant_e2_smoke", "seeds": [1],
                                     "weeks": 52, "eval_from": 20, "eval_to": 51, "top_n": 5}}
        with tempfile.TemporaryDirectory(prefix="lab-plan-") as tmp:
            path = write_json(Path(tmp) / "e2-ctx.json", obj)
            request = load_request(path, load_manifest(MANIFEST_TEST))
        plan = lab_plan.build_plan(request, load_manifest(MANIFEST_TEST), "unknown")
        self.assertEqual({u["unit"]: u["params"]["central_context_tokens"] for u in plan["units"]},
                         {"e2-fake-a": 32768, "e2-fake-b": 32768})


# --------------------------------------------------------------------------------------------------- discovery

class DiscoveryTests(TempDirTest):
    def setUp(self) -> None:
        super().setUp()
        self.world = GitWorld(self.tmp)

    def _discover(self, event: Path, sha: str, name: str = "push") -> Any:
        return discover(event, name, self.world.clone(sha))

    def _assert(self, found: Any, outcome: str, request: str | None, code: int) -> None:
        self.assertEqual((found.outcome, found.request, found.exit_code), (outcome, request, code), found.errors)

    def test_new_branch_request_in_non_tip_commit(self) -> None:
        w = self.world
        w.git("checkout", "-q", "-b", "feat")
        w.commit({"lab/requests/r1.json": REQUEST_TEXT})
        after = w.commit({"notes.txt": "x\n"})
        w.push("feat")
        found = self._discover(w.push_event(before="0" * 40, after=after, branch="feat"), after)
        self._assert(found, "run", "lab/requests/r1.json", 0)

    def test_new_branch_with_merge_tip(self) -> None:
        w = self.world
        w.git("checkout", "-q", "-b", "feat")
        w.commit({"lab/requests/r1.json": REQUEST_TEXT})
        w.git("checkout", "-q", "main")
        w.commit({"main.txt": "advance\n"})
        w.push("main")
        w.git("checkout", "-q", "feat")
        w.git("merge", "-q", "--no-ff", "-m", "merge main", "main")
        after = w.push("feat")
        clone = w.clone(after)
        listed = git("rev-list", "--boundary", after, "--not", "--exclude=origin/feat", "--remotes=origin", cwd=clone)
        self.assertEqual(sum(1 for line in listed.splitlines() if line.startswith("-")), 2)
        found = discover(w.push_event(before="0" * 40, after=after, branch="feat"), "push", clone)
        self._assert(found, "run", "lab/requests/r1.json", 0)

    def test_force_push_with_unreachable_before(self) -> None:
        w = self.world
        w.git("checkout", "-q", "-b", "feat")
        before = w.commit({"lab/requests/old.json": REQUEST_TEXT})
        w.push("feat")
        w.git("reset", "-q", "--hard", w.base)
        after = w.commit({"lab/requests/new.json": REQUEST_TEXT})
        w.push("feat", force=True)
        clone = w.clone(after)
        self.assertNotEqual(git_run("cat-file", "-e", f"{before}^{{commit}}", cwd=clone).returncode, 0)
        found = discover(w.push_event(before=before, after=after, branch="feat"), "push", clone)
        self._assert(found, "run", "lab/requests/new.json", 0)

    def test_before_reachable_not_ancestor(self) -> None:
        w = self.world
        w.git("checkout", "-q", "-b", "feat")
        before = w.commit({"lab/requests/a.json": REQUEST_TEXT})
        w.push("feat")
        w.git("checkout", "-q", "-b", "keep")
        w.push("keep")
        w.git("checkout", "-q", "feat")
        w.git("reset", "-q", "--hard", w.base)
        after = w.commit({"lab/requests/b.json": REQUEST_TEXT})
        w.push("feat", force=True)
        found = self._discover(w.push_event(before=before, after=after, branch="feat"), after)
        # two-dot diff before..after: a.json deleted, b.json added
        self._assert(found, "run", "lab/requests/b.json", 0)

    def _existing_branch(self, files: dict[str, str]) -> str:
        w = self.world
        w.git("checkout", "-q", "-b", "feat")
        before = w.commit(files)
        w.push("feat")
        return before

    def test_rename(self) -> None:
        w = self.world
        before = self._existing_branch({"lab/requests/a.json": REQUEST_TEXT})
        after = w.commit(rename=("lab/requests/a.json", "lab/requests/b.json"))
        w.push("feat")
        self._assert(self._discover(w.push_event(before=before, after=after, branch="feat"), after),
                     "run", "lab/requests/b.json", 0)

    def test_subdirectory_is_outside_the_glob(self) -> None:
        w = self.world
        before = self._existing_branch({"notes.txt": "x\n"})
        after = w.commit({"lab/requests/sub/x.json": REQUEST_TEXT, "lab/requests/valid.json": REQUEST_TEXT})
        w.push("feat")
        self._assert(self._discover(w.push_event(before=before, after=after, branch="feat"), after),
                     "run", "lab/requests/valid.json", 0)

    def test_bad_name_is_refused_and_not_echoed(self) -> None:
        w = self.world
        before = self._existing_branch({"notes.txt": "x\n"})
        after = w.commit({"lab/requests/Qz77SnTl.json": REQUEST_TEXT})
        w.push("feat")
        found = self._discover(w.push_event(before=before, after=after, branch="feat"), after)
        self._assert(found, "error", None, 2)
        self.assertEqual(found.errors, [f"event: {BAD_REQUEST_NAME}"])

    def test_delete_only(self) -> None:
        w = self.world
        before = self._existing_branch({"lab/requests/a.json": REQUEST_TEXT})
        after = w.commit(delete=("lab/requests/a.json",))
        w.push("feat")
        found = self._discover(w.push_event(before=before, after=after, branch="feat"), after)
        self._assert(found, "nothing", None, 0)
        self.assertEqual(found.notices, [DELETE_ONLY])

    def test_two_requests_in_a_normal_push(self) -> None:
        w = self.world
        before = self._existing_branch({"notes.txt": "x\n"})
        after = w.commit({"lab/requests/one.json": REQUEST_TEXT, "lab/requests/two.json": REQUEST_TEXT})
        w.push("feat")
        found = self._discover(w.push_event(before=before, after=after, branch="feat"), after)
        self._assert(found, "error", None, 2)
        self.assertEqual(found.errors[0], f"event: {SEVERAL_REQUESTS}")
        for name in ("one", "two"):
            self.assertIn(f"lab/requests/{name}.json", found.errors)
            self.assertIn(f"gh workflow run mycelic-lab.yml --ref feat -f request=lab/requests/{name}.json",
                          found.errors)

    def test_merge_tip_with_three_requests(self) -> None:
        w = self.world
        before = self._existing_branch({"notes.txt": "x\n"})
        w.git("checkout", "-q", "-b", "other", "main")
        w.commit({f"lab/requests/m{i}.json": REQUEST_TEXT for i in range(3)})
        w.push("other")
        w.git("checkout", "-q", "feat")
        w.git("merge", "-q", "--no-ff", "-m", "merge other", "other")
        after = w.push("feat")
        found = self._discover(w.push_event(before=before, after=after, branch="feat"), after)
        self._assert(found, "nothing", None, 0)
        self.assertEqual(found.notices[0], MERGE_SEVERAL)
        self.assertEqual(len(found.notices), 4)

    def test_deleted_default_and_tag_pushes_run_nothing(self) -> None:
        w = self.world
        head = w.head()
        cases = [(w.push_event(before=head, after="0" * 40, branch="feat", deleted=True), BRANCH_DELETED),
                 (w.push_event(before=head, after=head, branch="main"), DEFAULT_BRANCH),
                 (w.push_event(before="0" * 40, after=head, branch="", ref="refs/tags/v1"), NOT_A_BRANCH)]
        for event, notice in cases:
            with self.subTest(notice=notice):
                found = discover(event, "push", w.work)
                self._assert(found, "nothing", None, 0)
                self.assertEqual(found.notices, [notice])

    def test_head_must_be_after(self) -> None:
        w = self.world
        before = self._existing_branch({"notes.txt": "x\n"})
        after = w.commit({"lab/requests/r.json": REQUEST_TEXT})
        w.push("feat")
        found = discover(w.push_event(before=before, after=after, branch="feat"), "push", w.clone(before))
        self._assert(found, "error", None, 2)
        self.assertEqual(found.errors, [f"event: {CHECKOUT_MISMATCH}"])

    def test_unrelated_history_has_no_base(self) -> None:
        w = self.world
        w.git("checkout", "-q", "--orphan", "lonely")
        w.git("rm", "-q", "-rf", "--cached", ".")
        (w.work / "README.md").unlink()
        after = w.commit({"lab/requests/r.json": REQUEST_TEXT})
        w.push("lonely")
        found = self._discover(w.push_event(before="0" * 40, after=after, branch="lonely"), after)
        self._assert(found, "error", None, 2)
        self.assertEqual(found.errors[0], f"event: {NO_BASE}")
        self.assertIn("gh workflow run mycelic-lab.yml --ref lonely -f request=lab/requests/<name>.json",
                      found.errors)

    def test_no_request_change(self) -> None:
        w = self.world
        before = self._existing_branch({"notes.txt": "x\n"})
        after = w.commit({"notes.txt": "y\n"})
        w.push("feat")
        found = self._discover(w.push_event(before=before, after=after, branch="feat"), after)
        self._assert(found, "error", None, 2)
        self.assertEqual(found.errors[0], f"event: {NO_REQUEST_CHANGE}")

    def test_literal_pathspecs_in_the_environment_are_ignored(self) -> None:
        w = self.world
        before = self._existing_branch({"notes.txt": "x\n"})
        after = w.commit({"lab/requests/r.json": REQUEST_TEXT})
        w.push("feat")
        clone = w.clone(after)
        with mock.patch.dict(os.environ, {"GIT_LITERAL_PATHSPECS": "1", "GIT_DIR": str(self.tmp / "nowhere")}):
            found = discover(w.push_event(before=before, after=after, branch="feat"), "push", clone)
        self._assert(found, "run", "lab/requests/r.json", 0)

    def test_malformed_events(self) -> None:
        w = self.world
        head = w.head()
        good = {"before": head, "after": head, "ref": "refs/heads/feat", "deleted": False,
                "repository": {"default_branch": "main"}}
        cases = [({**good, "after": "HEAD"}, "$.after"), ({**good, "before": 7}, "$.before"),
                 ({**good, "ref": None}, "$.ref"), ({**good, "deleted": "false"}, "$.deleted"),
                 ({k: v for k, v in good.items() if k != "repository"}, "$.repository.default_branch"),
                 ({**good, "after": head.upper()}, "$.after")]
        for obj, path in cases:
            with self.subTest(path=path):
                found = discover(w.event(**obj), "push", w.work)
                self._assert(found, "error", None, 2)
                self.assertEqual(found.error.path, path)
        bad_json = self.tmp / "bad-event.json"
        bad_json.write_bytes(b'{"after": NaN}')
        self._assert(discover(bad_json, "push", w.work), "error", None, 2)
        self._assert(discover(w.event(**good), "pull_request", w.work), "error", None, 2)

    def test_dispatch(self) -> None:
        w = self.world
        w.commit({"lab/requests/ok.json": REQUEST_TEXT})
        self._assert(discover(w.event(inputs={"request": "lab/requests/ok.json"}), "workflow_dispatch", w.work),
                     "run", "lab/requests/ok.json", 0)
        for value in ("lab/requests/../ok.json", str(w.work / "lab/requests/ok.json"), "lab/requests/sub/ok.json",
                      "lab/requests/missing.json", 7, None):
            with self.subTest(value=value):
                found = discover(w.event(inputs={"request": value}), "workflow_dispatch", w.work)
                self._assert(found, "error", None, 2)
                self.assertEqual(found.error.path, "$.inputs.request")


class EventCliTests(TempDirTest):
    def _plan(self, cwd: Path, event: Path, name: str = "push") -> tuple[subprocess.CompletedProcess[str], Path]:
        gho = self.tmp / "github-output"
        gho.write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "lab.plan", "--event-path", str(event), "--event-name", name,
                            "--manifest", str(MANIFEST_TEST), "--out", str(self.tmp / "out"),
                            "--github-output", str(gho)], cwd=cwd, env=lab_env(), capture_output=True, text=True,
                           timeout=120)
        return r, gho

    def test_push_runs_the_added_request(self) -> None:
        w = GitWorld(self.tmp)
        w.git("checkout", "-q", "-b", "feat")
        after = w.commit({"lab/requests/try-it.json": REQUEST_TEXT})
        w.push("feat")
        clone = w.clone(after)
        r, gho = self._plan(clone, w.push_event(before="0" * 40, after=after, branch="feat"))
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads((self.tmp / "out" / "plan.json").read_text())
        self.assertEqual((plan["request"]["path"], plan["request"]["name"], plan["git_sha"]),
                         ("lab/requests/try-it.json", "try-it", after))
        lines = gho.read_text().splitlines()
        self.assertEqual([line.split("=", 1)[0] for line in lines], list(OUTPUT_KEYS))
        self.assertIn("has_units=true", lines)

    def test_branch_name_is_escaped_in_workflow_commands(self) -> None:
        w = GitWorld(self.tmp)
        branch = "feat%2C,x"
        w.git("checkout", "-q", "-b", branch)
        before = w.commit({"notes.txt": "x\n"})
        w.push(branch)
        after = w.commit({"notes.txt": "y\n"})
        w.push(branch)
        r, gho = self._plan(w.clone(after), w.push_event(before=before, after=after, branch=branch))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.splitlines()[0], f"error: event: {NO_REQUEST_CHANGE}")
        error_lines = [line for line in r.stdout.splitlines() if line.startswith("::error")]
        self.assertEqual(len(error_lines), 1)
        self.assertIn("--ref feat%252C,x -f request=lab/requests/<name>.json", error_lines[0])
        self.assertNotIn("%2C,x", error_lines[0].replace("%252C,x", ""))
        self.assertIn("%0A", error_lines[0])
        self.assertIn("--ref feat%252C,x -f request=lab/requests/<name>.json", r.stderr)
        self.assertEqual(gho.read_text(), "")

    def test_malformed_request_push_is_refused_and_summarised(self) -> None:
        from lab import summary

        w = GitWorld(self.tmp)
        w.git("checkout", "-q", "-b", "feat")
        bad = json.loads(REQUEST_TEXT)
        bad["job_minutes"] = 7
        after = w.commit({"lab/requests/bad-one.json": json.dumps(bad)})
        w.push("feat")
        r, gho = self._plan(w.clone(after), w.push_event(before="0" * 40, after=after, branch="feat"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("::error file=lab/requests/bad-one.json::$.job_minutes: must be an int in [45, 330]",
                      r.stdout.splitlines())
        self.assertEqual(gho.read_text(), "")
        md, entries = summary.render_plan(self.tmp / "out")
        self.assertEqual(md.splitlines()[0], "## Lab plan refused")
        self.assertIn("`$.job_minutes`", md)
        self.assertIn("`must be an int in [45, 330]`", md)
        self.assertIn("`lab/requests/bad-one.json`", md)
        self.assertEqual(entries, [])

    def test_delete_only_push_writes_nothing_to_run(self) -> None:
        w = GitWorld(self.tmp)
        w.git("checkout", "-q", "-b", "feat")
        before = w.commit({"lab/requests/gone.json": REQUEST_TEXT})
        w.push("feat")
        after = w.commit(delete=("lab/requests/gone.json",))
        w.push("feat")
        r, gho = self._plan(w.clone(after), w.push_event(before=before, after=after, branch="feat"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"::notice::{DELETE_ONLY}")
        self.assertFalse((self.tmp / "out" / "plan.json").exists())
        self.assertEqual(gho.read_text().splitlines(),
                         ["has_provision=false", "has_units=false", 'matrix={"include":[]}', "max_parallel=1",
                          "plan_sha256=", 'provision_matrix={"include":[]}', "retention_days=1", "result_class=none"])

    def test_nothing_outcomes_write_plan_nothing(self) -> None:
        branch = "feat-Qz9branch"
        w = GitWorld(self.tmp)
        w.git("checkout", "-q", "-b", branch)
        before = w.commit({"lab/requests/gone.json": REQUEST_TEXT})
        w.push(branch)
        after = w.commit(delete=("lab/requests/gone.json",))
        w.push(branch)
        r, gho = self._plan(w.clone(after), w.push_event(before=before, after=after, branch=branch))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"::notice::{DELETE_ONLY}"), r.stderr)
        nothing = self.tmp / "out" / "plan-nothing.json"
        self.assertEqual(json.loads(nothing.read_text()), {"schema_version": 1, "kind": "lab_plan_nothing",
                                                            "notice": DELETE_ONLY, "requests": []})
        self.assertEqual(gho.read_text().splitlines(),
                         ["has_provision=false", "has_units=false", 'matrix={"include":[]}', "max_parallel=1",
                          "plan_sha256=", 'provision_matrix={"include":[]}', "retention_days=1", "result_class=none"])

        shutil.rmtree(self.tmp / "out")
        w.git("checkout", "-q", "-b", "other", "main")
        w.commit({f"lab/requests/m{i}.json": REQUEST_TEXT for i in range(3)})
        w.push("other")
        w.git("checkout", "-q", branch)
        tip = w.head()
        w.git("merge", "-q", "--no-ff", "-m", "merge other", "other")
        merged = w.push(branch)
        r, gho = self._plan(w.clone(merged), w.push_event(before=tip, after=merged, branch=branch))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], f"::notice::{MERGE_SEVERAL}")
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(branch in line for line in lines[1:]))
        data = nothing.read_bytes()
        self.assertEqual(json.loads(data), {"schema_version": 1, "kind": "lab_plan_nothing", "notice": MERGE_SEVERAL,
                                            "requests": [f"lab/requests/m{i}.json" for i in range(3)]})
        self.assertNotIn(branch.encode(), data)
        self.assertNotIn(b"gh workflow run", data)
        self.assertFalse((self.tmp / "out" / "plan.json").exists())
        self.assertIn("has_units=false", gho.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
