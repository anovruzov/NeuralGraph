"""The data files the lab ships and the founder then changes: ``lab/models.json``, ``lab/models.lock.json`` and
``lab/requests/``.

The founder's guide tells them to change these files: add requests and commit the lock (docs/lab/README.md, The first
runs, in order), add a hosted entry (Optional secrets) or a model (MODELS.md, Adding a model), and raise or lower a
model's context settings (Troubleshooting). So these tests pin only what those edits keep: the manifest and its lock
load, the shipped models and ``plumbing-001`` are still there and the shipped entries hold what MODELS.md says,
``plumbing-001`` and the check and smoke templates plan as shipped, and every request in ``lab/requests/`` loads.

The workflow's self-test (``test_lab_workflow.SELF_TEST``) does not run this module: it runs only modules that read
none of these files, and ``test_lab_workflow.SelfTestTests`` runs it, and this module, after those edits.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab import plan as lab_plan
from lab.manifest import load_manifest
from lab.request import RequestError, load_request, validate
from tests.lab.helpers import LAB_MANIFEST, PLUMBING_001, ROOT, TEMPLATES, plumbing_min

SHIPPED_GGUF = ("a-0p5b", "a-1p5b", "a-4b", "b-2b")
SHIPPED_MODELS = (*SHIPPED_GGUF, "fake-a", "fake-b")
REQUESTS = ROOT / "lab" / "requests"


class ShippedManifestTests(unittest.TestCase):
    def test_the_manifest_and_lock_load(self) -> None:
        manifest = load_manifest(LAB_MANIFEST)          # the lock too: every locked entry matches the manifest
        self.assertLessEqual(set(SHIPPED_MODELS), set(manifest.models))
        server = manifest.server
        self.assertIsNotNone(server)
        self.assertEqual((server["format"], server["threads"], server["cache_ram_mib"], server["args"]),
                         ("tar.gz", "physical", 1024, []))
        self.assertTrue(server["asset"].startswith(server["archive_root"] + "-bin-"))
        self.assertTrue(server["url"].endswith(f"/releases/download/{server['tag']}/{server['asset']}"))
        for key in SHIPPED_GGUF:
            entry = manifest.models[key]
            with self.subTest(key=key):
                self.assertEqual((entry["kind"], entry["alias"], entry["gguf"]["revision"], entry["gguf"]["license"]),
                                 ("gguf", f"lab-{key}", "main", "apache-2.0"))
                self.assertEqual((entry["response_format"], entry["transport_schema"]), ("json_schema", "full"))
                self.assertTrue(entry["gguf"]["file"].endswith(".gguf"))
        self.assertEqual(manifest.models["a-4b"]["server_args"], ["--reasoning", "off"])
        for key in ("fake-a", "fake-b"):
            self.assertEqual((manifest.models[key]["kind"], manifest.models[key]["persona"]), ("fake", "valid"))
        self.assertTrue(manifest.lock.path.endswith("lab/models.lock.json"))
        lock = json.loads((ROOT / "lab" / "models.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["schema_version"], 1)
        self.assertLessEqual(set(lock["models"]), {k for k, m in manifest.models.items() if m["kind"] == "gguf"})

    def test_providers_follow_the_shipped_server(self) -> None:
        manifest = load_manifest(LAB_MANIFEST)
        self.assertEqual(manifest.providers(), ("fake", manifest.server["program"]))
        obj = plumbing_min()
        obj.update(provider=manifest.server["program"], models=["tiny-gguf"])
        obj["experiments"] = {"e3": {**obj["experiments"]["e3"]}}
        with self.assertRaises(RequestError) as caught:
            validate(obj, manifest)
        self.assertEqual((caught.exception.path, caught.exception.problem),
                         ("$.models[0]", "not a model in the manifest"))
        obj["provider"] = "other-server"
        with self.assertRaises(RequestError) as caught:
            validate(obj, manifest)
        self.assertEqual(caught.exception.path, "$.provider")
        self.assertEqual(validate(plumbing_min(), manifest)["provider"], "fake")


class ShippedRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-shipped-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.manifest = load_manifest(LAB_MANIFEST)

    def test_every_request_loads(self) -> None:
        names = {p.name for p in REQUESTS.iterdir()}
        self.assertLessEqual({"README.md", "plumbing-001.json"}, names)
        for path in sorted(REQUESTS.glob("*.json")):
            with self.subTest(request=path.name):
                loaded = load_request(path, self.manifest, strict_location=True, root=ROOT)
                self.assertEqual(loaded.path, f"lab/requests/{path.name}")

    def test_plumbing_001_plans_as_shipped(self) -> None:
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            request = load_request(PLUMBING_001.relative_to(ROOT), self.manifest, strict_location=True)
        finally:
            os.chdir(cwd)
        plan = lab_plan.build_plan(request, self.manifest, "unknown")
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

    def test_check_and_smoke_templates_plan(self) -> None:
        manifest = self.manifest
        requests = self.tmp / "lab" / "requests"
        requests.mkdir(parents=True)
        shutil.copy(TEMPLATES / "check.json", requests / "check-001.json")
        request = load_request(requests / "check-001.json", manifest, strict_location=True, root=self.tmp)
        plan = lab_plan.build_plan(request, manifest, "unknown")
        self.assertEqual([(s["shard"], s["kind"], s["model"], s["units"]) for s in plan["shards"]],
                         [("s001-a-0p5b", "gguf", "a-0p5b", ["e3-a-0p5b"])])
        self.assertEqual(plan["result_class"], "real")
        self.assertEqual([(p["target"], p["entry"]) for p in plan["provision"]],
                         [("server", "server"), ("gguf", "gguf-a-0p5b")])
        for entry in plan["provision"]:
            self.assertEqual(entry["cache_key"], entry["restore_key"] or entry["restore_prefix"] + "unlocked")

        shutil.copy(TEMPLATES / "smoke.json", requests / "smoke-001.json")
        request = load_request(requests / "smoke-001.json", manifest, strict_location=True, root=self.tmp)
        plan = lab_plan.build_plan(request, manifest, "unknown")
        first, *others = request.data["models"]
        experiments = {u["unit"]: u["experiment"] for u in plan["units"]}
        self.assertEqual([(s["kind"], s["model"], [experiments[u] for u in s["units"]], s["planned_minutes"],
                           s["timeout_minutes"]) for s in plan["shards"]],
                         [("gguf", first, ["sim", "g0", "e3"], 305, 330)]
                         + [("gguf", model, ["sim", "e3"], 260, 285) for model in others])
        self.assertEqual(sorted(p.name for p in TEMPLATES.iterdir()),
                         ["check.json", "hosted-comparison.json", "main.json", "openfda-replay.json", "smoke.json"])


if __name__ == "__main__":
    unittest.main()
