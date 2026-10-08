"""The optional hosted provider: one OpenAI-compatible host named by two secrets, in E1 and as E2's central comparator.

Nothing here calls a real host: every hosted call goes to the collective's ``FakeOpenAIServer``, usually behind a
loopback :class:`~tests.lab.stubs.hosted_stub.PrefixFront` that serves it under a path holding a sentinel, with a
sentinel key. The checks: the manifest's ``hosted`` kind, the request's placement rules, hosted block and call bound,
the plan without the secrets (skips, never a substitute) and with them (one hosted shard, the shares), the secrets'
states, the preflight's outcomes, the per-shard call cap, the classes a hosted result can get, the E2 hosted central
on a stub model server, the report's hosted calls and cost, and that neither the key nor the base URL's path appears
in any file, log, summary or subprocess environment where it must not.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from lab import hosted, notes
from lab import provision as lab_provision
from lab import shard as lab_shard
from lab import summary
from lab import units
from lab.aggregate import E1_JSON, _class_of
from lab.goldlabels import build_labels
from lab.hosted import BASE_URL_VAR, HAS_VAR, KEY_VAR, PREFLIGHT_LEDGER, PREFLIGHT_MAX_CALLS, REDACTED_BASE_URL
from lab.manifest import REPIN, ManifestError, load_manifest, parse_lock, parse_manifest
from lab.prereg import PLACEHOLDER_BASE_URL, load_prereg
from lab.request import HOSTED_PLACE, RequestError, validate
from lab.responder import Responder
from mycelic.collective.inference.fakeserver import FakeOpenAIServer
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.packs.loader import load_pack
from tests.lab.helpers import (DATA, LAB_MANIFEST, MANIFEST_TEST, MODEL_KEY, DryTree, StubWorld, call_main,
                               check_sources, dry_run, kill_mentioning, make_plan, plumbing_min, run_plan, run_prereg,
                               sim_block, write_json)
from tests.lab.stubs.hosted_stub import PrefixFront
from tests.lab.test_lab_server import _evidence

HOSTED_E1 = DATA / "requests" / "hosted-e1.json"
KEY = "sk-lab-SENTINEL-7f3a"
PATH_TOKEN = "lab-path-SENTINEL-q9"
PREFIX = f"/{PATH_TOKEN}/v1"
DQ = load_pack("device_quality")
MANIFEST = load_manifest(MANIFEST_TEST)
SERVER_PROGRAM = MANIFEST.server["program"]
E2_BLOCK = {"minutes": 5, "pack": "device_quality", "plant": "plant_e2_smoke", "seeds": [1], "weeks": 52,
            "eval_from": 20, "eval_to": 51, "top_n": 5, "min_candidates": 5, "bootstrap_b": 1000}
HOSTED_UNITS = ("e1-h-a-r1", "e1-h-a-r2", "e1-h-a-r3")
FAKE_UNITS = ("e1-fake-a-r1", "e1-fake-a-r2", "e1-fake-a-r3")
SHARD_RE = re.compile(r"s[0-9]{3}-hosted")
H_A = {"kind": "hosted", "model": "lab-hosted-a", "response_format": "json_schema", "transport_schema": "full",
       "price": {"per_mtok_in": 1.0, "per_mtok_out": 2.0}, "deadline_s": 60, "max_retries": 1}


def _json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def hosted_e1(**changes: Any) -> dict[str, Any]:
    obj = _json(HOSTED_E1)
    obj.update(changes)
    return obj


def e1_block(**changes: Any) -> dict[str, Any]:
    return {**hosted_e1()["experiments"]["e1"], **changes}


def _env(**values: str | None) -> dict[str, str]:
    """``os.environ`` without the hosted variables, plus ``values`` (None leaves a name out)."""
    env = {k: v for k, v in os.environ.items() if k not in (KEY_VAR, BASE_URL_VAR, HAS_VAR)}
    env.update({k: v for k, v in values.items() if v is not None})
    return env


def plan_with(directory: Path, request: dict[str, Any], has: str | None = "true",
              manifest: Path = MANIFEST_TEST) -> tuple[dict[str, Any], Path]:
    """``make_plan`` (plan and preregistration) with ``LAB_HAS_HOSTED`` set to ``has`` (None: absent)."""
    with mock.patch.dict(os.environ, _env(**{HAS_VAR: has}), clear=True):
        return make_plan(directory, request, manifest)


def hosted_shard(plan: dict[str, Any]) -> str:
    return next(s["shard"] for s in plan["shards"] if s["kind"] == "hosted")


def run_shard(plan_path: Path, shard: str, out: Path, env: dict[str, str]
              ) -> tuple[int, str, str, list[tuple[list[str], dict[str, str]]]]:
    """``lab.shard run`` in-process with ``env`` as the whole environment; every harness argv and environment."""
    seen: list[tuple[list[str], dict[str, str]]] = []
    real = units.run_process

    def spy(argv: list[str], environ: dict[str, str], *args: Any, **kwargs: Any) -> Any:
        seen.append((list(argv), dict(environ)))
        return real(argv, environ, *args, **kwargs)

    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(units, "run_process", spy):
        code, stdout, stderr = call_main(lab_shard, ["run", "--plan", str(plan_path), "--shard", shard, "--out",
                                                     str(out)])
    return code, stdout, stderr, seen


def unit_json(out: Path, unit: str) -> dict[str, Any]:
    return _json(Path(out) / "units" / unit / "unit.json")


def files_holding(root: Path, *needles: str) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file()
                  and any(n.encode() in p.read_bytes() for n in needles))


class TempTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-hosted-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))

    def fake(self, persona: str = "valid") -> FakeOpenAIServer:
        server = FakeOpenAIServer(persona, responder=Responder(DQ)).start()
        self.addCleanup(server.stop)
        return server

    def front(self, target: FakeOpenAIServer, **kwargs: Any) -> PrefixFront:
        front = PrefixFront(target.base_url, PREFIX, **kwargs).start()
        self.addCleanup(front.stop)
        return front


# --------------------------------------------------------------------------------------------------- the manifest

def _manifest_with(entry: Any, key: str = "h") -> dict[str, Any]:
    obj = _json(MANIFEST_TEST)
    obj["models"] = {key: entry}
    return obj


class ManifestTests(unittest.TestCase):
    def test_hosted_defaults(self) -> None:
        _, _, models = parse_manifest(_manifest_with({"kind": "hosted", "model": "lab-x"}))
        self.assertEqual(models["h"], {"kind": "hosted", "model": "lab-x", "response_format": "json_schema",
                                       "transport_schema": "full", "price": None, "deadline_s": 300,
                                       "max_retries": 2})
        self.assertEqual(MANIFEST.models["h-a"], H_A)
        self.assertEqual(MANIFEST.models["h-b"], {"kind": "hosted", "model": "lab-hosted-b",
                                                  "response_format": "json_schema", "transport_schema": "reduced",
                                                  "price": None, "deadline_s": 60, "max_retries": 0})

    def test_refusals(self) -> None:
        base = {"kind": "hosted", "model": "lab-x"}
        cases = [
            ({**base, "alias": "x"}, "$.models.h.alias", "unknown key"),
            ({**base, "gguf": {}}, "$.models.h.gguf", "unknown key"),
            ({**base, "persona": "valid"}, "$.models.h.persona", "unknown key"),
            ({"kind": "hosted"}, "$.models.h.model", "required"),
            ({**base, "model": ""}, "$.models.h.model", "must match"),
            ({**base, "model": "-x"}, "$.models.h.model", "must match"),
            ({**base, "model": "a b"}, "$.models.h.model", "must match"),
            ({**base, "model": 5}, "$.models.h.model", "must match"),
            ({**base, "response_format": "xml"}, "$.models.h.response_format", "must be one of"),
            ({**base, "transport_schema": "tiny"}, "$.models.h.transport_schema", "must be one of"),
            ({**base, "price": {"per_mtok_in": 1}}, "$.models.h.price.per_mtok_out", "required"),
            ({**base, "price": {"per_mtok_out": 1}}, "$.models.h.price.per_mtok_in", "required"),
            ({**base, "price": {"per_mtok_in": -1, "per_mtok_out": 1}}, "$.models.h.price.per_mtok_in",
             "must be a number in [0, 1000]"),
            ({**base, "price": {"per_mtok_in": True, "per_mtok_out": 1}}, "$.models.h.price.per_mtok_in",
             "must be a number in [0, 1000]"),
            ({**base, "price": {"per_mtok_in": 1, "per_mtok_out": 1000.5}}, "$.models.h.price.per_mtok_out",
             "must be a number in [0, 1000]"),
            ({**base, "price": {"per_mtok_in": "1", "per_mtok_out": 1}}, "$.models.h.price.per_mtok_in",
             "must be a number in [0, 1000]"),
            ({**base, "price": {"per_mtok_in": 1, "per_mtok_out": 1, "usd_per_hour": 1}},
             "$.models.h.price.usd_per_hour", "unknown key"),
            ({**base, "price": None}, "$.models.h.price", "must be an object"),
            ({**base, "deadline_s": 9}, "$.models.h.deadline_s", "must be an int in [10, 3600]"),
            ({**base, "deadline_s": 3601}, "$.models.h.deadline_s", "must be an int in [10, 3600]"),
            ({**base, "deadline_s": 30.0}, "$.models.h.deadline_s", "must be an int in [10, 3600]"),
            ({**base, "max_retries": -1}, "$.models.h.max_retries", "must be an int in [0, 5]"),
            ({**base, "max_retries": 6}, "$.models.h.max_retries", "must be an int in [0, 5]"),
            ({**base, "max_retries": True}, "$.models.h.max_retries", "must be an int in [0, 5]"),
            ({"kind": "remote", "model": "lab-x"}, "$.models.h.kind", "must be one of gguf, fake, hosted"),
        ]
        for entry, path, problem in cases:
            with self.subTest(entry=entry), self.assertRaises(ManifestError) as caught:
                parse_manifest(_manifest_with(entry))
            self.assertEqual(caught.exception.path, path)
            self.assertTrue(caught.exception.problem.startswith(problem), caught.exception.problem)

    def test_reserved_keys(self) -> None:
        for key, problem in (("hosted", "reserved for the hosted shard group"),
                             ("none", "reserved for model-free shard groups")):
            with self.subTest(key=key), self.assertRaises(ManifestError) as caught:
                parse_manifest(_manifest_with({"kind": "hosted", "model": "lab-x"}, key=key))
            self.assertEqual((caught.exception.path, caught.exception.problem), (f"$.models.{key}", problem))

    def test_a_lock_entry_for_a_hosted_key_is_repin(self) -> None:
        locked = {"repo": "a/b", "file": "x.gguf", "revision": "main", "commit": "0" * 40, "sha256": "0" * 64,
                  "size": 1}
        with self.assertRaises(ManifestError) as caught:
            parse_lock({"schema_version": 1, "server": None, "models": {"h-a": locked}}, MANIFEST.server,
                       MANIFEST.models)
        self.assertEqual(caught.exception.path, "$.models")
        self.assertTrue(caught.exception.problem.startswith(REPIN))

    def test_lab_manifest_ships_no_hosted_entry(self) -> None:
        manifest = load_manifest(LAB_MANIFEST)
        self.assertTrue(manifest.models)
        self.assertNotIn("hosted", {m["kind"] for m in manifest.models.values()})


# --------------------------------------------------------------------------------------------------- the request

def _e2(**changes: Any) -> dict[str, Any]:
    return {**E2_BLOCK, **changes}


class RequestTests(unittest.TestCase):
    def refused(self, obj: dict[str, Any], path: str, problem: str) -> RequestError:
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        self.assertEqual(caught.exception.path, path)
        self.assertTrue(caught.exception.problem.startswith(problem), caught.exception.problem)
        return caught.exception

    def test_hosted_endpoint_and_reference(self) -> None:
        data = validate(hosted_e1(), MANIFEST)
        self.assertEqual((data["experiments"]["e1"]["models"], data["hosted"]), (["fake-a", "h-a"],
                                                                                {"h-a": {"max_calls": 300}}))
        data = validate(hosted_e1(experiments={"e1": e1_block(reference="h-a")}), MANIFEST)
        self.assertEqual(data["experiments"]["e1"]["reference"], "h-a")
        self.assertEqual(validate(plumbing_min(), MANIFEST)["hosted"], {})
        self.assertEqual(validate({**plumbing_min(), "hosted": {}}, MANIFEST)["hosted"], {})

    def test_both_providers_accept_hosted(self) -> None:
        self.assertEqual(validate(hosted_e1(), MANIFEST)["provider"], "fake")
        obj = hosted_e1(provider=SERVER_PROGRAM, models=[MODEL_KEY, "h-a"],
                        experiments={"e1": e1_block(reference=MODEL_KEY)})
        self.assertEqual(validate(obj, MANIFEST)["provider"], SERVER_PROGRAM)

    def test_e2_central(self) -> None:
        obj = hosted_e1(models=["fake-a", "fake-b", "h-a"], experiments={"e2": _e2(models=["fake-a"], central="h-a")},
                        hosted={"h-a": {"max_calls": 32}})
        self.assertEqual(validate(obj, MANIFEST)["experiments"]["e2"]["central"], "h-a")
        for central in ("fake-b", "h-b", "tiny-gguf"):
            with self.subTest(central=central):
                bad = copy.deepcopy(obj)
                bad["experiments"]["e2"]["central"] = central
                self.refused(bad, "$.experiments.e2.central", "must be self or a hosted model of $.models")

    def test_hosted_placement(self) -> None:
        blocks = {"e3": plumbing_min()["experiments"]["e3"],
                  "g0": {k: v for k, v in plumbing_min()["experiments"]["g0"].items() if k != "models"},
                  "sim": sim_block(), "e2": _e2()}
        for name, block in blocks.items():
            with self.subTest(block=name, at="block"):
                obj = hosted_e1(experiments={name: {**block, "models": ["fake-a", "h-a"]}}, hosted=None)
                obj.pop("hosted")
                self.refused(obj, f"$.experiments.{name}.models[1]", HOSTED_PLACE)
            with self.subTest(block=name, at="models"):
                obj = hosted_e1(experiments={name: dict(block)})
                obj.pop("hosted")
                self.refused(obj, "$.models[1]", HOSTED_PLACE)
        self.assertEqual(HOSTED_PLACE, ("a hosted model runs only in e1 or as e2.central: the simulation and the "
                                        "canary scan run each site's model inside the runner, and E3 measures this "
                                        "runner"))

    def test_hosted_block_rules(self) -> None:
        unused = {**plumbing_min(), "hosted": {"h-a": {"max_calls": 5}}}
        self.refused(unused, "$.hosted", "only for the hosted models e1 or e2.central uses")
        missing = hosted_e1()
        missing.pop("hosted")
        self.refused(missing, "$.hosted", "required: the hosted models e1 or e2.central uses need max_calls")
        cases = [
            ("x", "$.hosted", "must be an object"),
            ({}, "$.hosted.h-a", "required"),
            ({"h-a": {"max_calls": 300}, "h-b": {"max_calls": 300}}, "$.hosted.h-b",
             "not a hosted model e1 or e2.central uses"),
            ({"h-a": {"max_calls": 300}, "H B": {"max_calls": 300}}, "$.hosted",
             "not a hosted model e1 or e2.central uses (name not shown)"),
            ({"h-a": 5}, "$.hosted.h-a", "must be an object"),
            ({"h-a": {"max_calls": 300, "cap": 1}}, "$.hosted.h-a.cap", "unknown key"),
            ({"h-a": {}}, "$.hosted.h-a.max_calls", "required"),
        ]
        for value in (0, 1000001, True, 300.0, "300", None):
            cases.append(({"h-a": {"max_calls": value}}, "$.hosted.h-a.max_calls",
                          "must be an int in [1, 1000000]"))
        for block, path, problem in cases:
            with self.subTest(block=block):
                self.refused(hosted_e1(hosted=block), path, problem)

    def test_bound_generator_labels(self) -> None:
        bound = PREFLIGHT_MAX_CALLS + 3 * 40 * 2
        self.assertEqual(bound, 246)
        self.assertEqual(validate(hosted_e1(hosted={"h-a": {"max_calls": bound}}), MANIFEST)["hosted"],
                         {"h-a": {"max_calls": bound}})
        err = self.refused(hosted_e1(hosted={"h-a": {"max_calls": bound - 1}}), "$.hosted.h-a.max_calls",
                           f"must be at least {bound}, the most model calls")
        self.assertIn("lower e1.runs, e1.labels.n, e2.top_n or e2.seeds, or raise max_calls", err.problem)

    def test_bound_fixture_labels(self) -> None:
        records = build_labels("fixtures", "device_quality")[1]["records"]
        bound = PREFLIGHT_MAX_CALLS + 3 * records * 2
        labels = {"source": "fixtures", "pack": "device_quality"}
        ok = hosted_e1(experiments={"e1": e1_block(labels=labels)}, hosted={"h-a": {"max_calls": bound}})
        self.assertEqual(validate(ok, MANIFEST)["hosted"]["h-a"]["max_calls"], bound)
        bad = hosted_e1(experiments={"e1": e1_block(labels=labels)}, hosted={"h-a": {"max_calls": bound - 1}})
        self.refused(bad, "$.hosted.h-a.max_calls", f"must be at least {bound},")

    def test_bound_e1_and_e2_central_on_one_key(self) -> None:
        bound = (PREFLIGHT_MAX_CALLS + 3 * 40 * 2) + 2 * (2 * PREFLIGHT_MAX_CALLS + 2 * 5 * 1 * 2)
        self.assertEqual(bound, 310)

        def obj(max_calls: int) -> dict[str, Any]:
            return hosted_e1(models=["fake-a", "fake-b", "h-a"], hosted={"h-a": {"max_calls": max_calls}},
                             experiments={"e1": e1_block(models=["fake-a", "h-a"]),
                                          "e2": _e2(models=["fake-a", "fake-b"], central="h-a")})

        self.assertEqual(validate(obj(bound), MANIFEST)["hosted"], {"h-a": {"max_calls": bound}})
        self.refused(obj(bound - 1), "$.hosted.h-a.max_calls", f"must be at least {bound},")

    def test_bound_does_not_depend_on_the_secrets(self) -> None:
        results = []
        for env in (_env(), _env(**{HAS_VAR: "true", KEY_VAR: KEY, BASE_URL_VAR: "https://api.example/v1"})):
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RequestError) as caught:
                    validate(hosted_e1(hosted={"h-a": {"max_calls": 245}}), MANIFEST)
                results.append((caught.exception.path, caught.exception.problem))
        self.assertEqual(results[0], results[1])

    def test_plan_exits_two_at_the_bound(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lab-hosted-req-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = write_json(tmp / "bound.json", hosted_e1(hosted={"h-a": {"max_calls": 245}}))
        code, _, stderr = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "o")])
        self.assertEqual(code, 2)
        self.assertTrue(stderr.splitlines()[0].startswith("error: $.hosted.h-a.max_calls: must be at least 246,"))


# --------------------------------------------------------------------------------------------------- the plan

class PlanTests(TempTest):
    def plan(self, request: dict[str, Any], has: str | None, name: str = "p",
             manifest: Path = MANIFEST_TEST) -> tuple[int, str, str, dict[str, Any] | None, dict[str, str]]:
        path = write_json(self.tmp / f"{name}.json", request)
        out, gho = self.tmp / f"{name}-out", self.tmp / f"{name}.gho"
        with mock.patch.dict(os.environ, _env(**{HAS_VAR: has}), clear=True):
            code, stdout, stderr = run_plan(["--request", str(path), "--manifest", str(manifest), "--out", str(out),
                                             "--github-output", str(gho)])
        plan = _json(out / "plan.json") if (out / "plan.json").exists() else None
        outputs = dict(line.split("=", 1) for line in gho.read_text().splitlines()) if gho.exists() else {}
        return code, stdout, stderr, plan, outputs

    def test_without_the_secrets_hosted_units_are_skipped(self) -> None:
        for i, has in enumerate((None, "false", "True", "1", "")):
            with self.subTest(has=has):
                code, stdout, stderr, plan, outputs = self.plan(hosted_e1(), has, name=f"n{i}")
                self.assertEqual(code, 0, stderr)
                self.assertEqual(plan["skipped"], [
                    *({"unit": u, "experiment": "e1", "model": "fake-a", "reason": notes.E1_WITHOUT_HOSTED}
                      for u in FAKE_UNITS),
                    *({"unit": u, "experiment": "e1", "model": "h-a", "reason": notes.HOSTED_SECRETS_MISSING}
                      for u in HOSTED_UNITS)])
                self.assertEqual((plan["units"], plan["shards"], plan["hosted"], plan["matrix"]),
                                 ([], [], {}, {"include": []}))
                self.assertEqual((outputs["has_units"], outputs["max_parallel"]), ("false", "1"))
                notices = [line for line in stdout.splitlines() if line.startswith("::notice title=lab hosted "
                                                                                   "skipped::")]
                self.assertEqual(len(notices), 1)
                self.assertIn(KEY_VAR, notices[0])
                self.assertIn(BASE_URL_VAR, notices[0])
                for unit in (*FAKE_UNITS, *HOSTED_UNITS):
                    self.assertIn(unit, notices[0])
        md, sources = summary.render_plan(self.tmp / "n0-out")
        skipped = md.split(f"### {notes.HEADINGS['plan-skipped']}", 1)[1]
        for unit in (*FAKE_UNITS, *HOSTED_UNITS):
            self.assertIn(f"| `{unit}` | `e1` |", skipped)
        self.assertIn(f"| {notes.HOSTED_SECRETS_MISSING} |", skipped)
        check_sources(self, md, sources, self.tmp / "n0-out")

    def test_two_local_endpoints_keep_e1(self) -> None:
        request = hosted_e1(models=["fake-a", "fake-b", "h-a"], experiments={"e1": e1_block()})
        code, _, stderr, plan, _ = self.plan(request, None)
        self.assertEqual(code, 0, stderr)
        self.assertEqual([s["unit"] for s in plan["skipped"]], list(HOSTED_UNITS))
        self.assertEqual({s["reason"] for s in plan["skipped"]}, {notes.HOSTED_SECRETS_MISSING})
        self.assertEqual(sorted({u["model"] for u in plan["units"]}), ["fake-a", "fake-b"])
        self.assertEqual(sorted(plan["models"]), ["fake-a", "fake-b"])
        code, _, stderr = run_prereg(self.tmp / "p-out" / "plan.json")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(load_prereg(self.tmp / "p-out" / "plan.json").manifest["e1"]["endpoints"],
                         ["fake-a", "fake-b"])

    def test_a_hosted_reference_skips_all_of_e1(self) -> None:
        request = hosted_e1(models=["fake-a", "fake-b", "h-a"], experiments={"e1": e1_block(reference="h-a")})
        code, _, stderr, plan, _ = self.plan(request, None)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(plan["units"], [])
        reasons = {s["unit"]: s["reason"] for s in plan["skipped"]}
        self.assertEqual(len(reasons), 9)
        self.assertEqual({reasons[u] for u in HOSTED_UNITS}, {notes.HOSTED_SECRETS_MISSING})
        self.assertEqual({r for u, r in reasons.items() if u not in HOSTED_UNITS}, {notes.E1_WITHOUT_HOSTED})

    def test_an_e2_hosted_central_is_skipped_never_self(self) -> None:
        request = hosted_e1(models=["fake-a", "fake-b", "h-a"], hosted={"h-a": {"max_calls": 64}},
                            experiments={"e2": _e2(models=["fake-a", "fake-b"], central="h-a"),
                                         "g0": {**plumbing_min()["experiments"]["g0"], "models": ["fake-a"]}})
        code, _, stderr, plan, _ = self.plan(request, None)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(plan["skipped"], [{"unit": f"e2-{m}", "experiment": "e2", "model": m,
                                            "reason": notes.E2_CENTRAL_HOSTED_SKIPPED} for m in ("fake-a", "fake-b")])
        self.assertEqual([u["unit"] for u in plan["units"]], ["g0-fake-a"])
        self.assertFalse([u for u in plan["units"] if u["experiment"] == "e2"])
        code, _, stderr, plan, _ = self.plan(request, "true", name="t")
        self.assertEqual(code, 0, stderr)
        self.assertEqual({u["params"]["central"] for u in plan["units"] if u["experiment"] == "e2"}, {"h-a"})

    def test_with_the_secrets_one_hosted_shard(self) -> None:
        code, stdout, stderr, plan, outputs = self.plan(hosted_e1(), "true")
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("::notice", stdout)
        self.assertEqual(plan["skipped"], [])
        shards = [s for s in plan["shards"] if s["kind"] == "hosted"]
        self.assertEqual(len(shards), 1)
        shard = shards[0]
        self.assertRegex(shard["shard"], SHARD_RE)
        self.assertEqual((shard["model"], shard["needs_secret"], shard["units"]), (None, True, list(HOSTED_UNITS)))
        entry = next(m for m in plan["matrix"]["include"] if m["shard"] == shard["shard"])
        self.assertEqual((entry["hosted"], entry["model"], entry["gguf_key"], entry["kind"]), (True, "", "", "hosted"))
        other = next(m for m in plan["matrix"]["include"] if m["shard"] != shard["shard"])
        self.assertEqual(other["hosted"], False)
        for unit in plan["units"]:
            hosted_unit = unit["model"] == "h-a"
            self.assertEqual((unit["kind"] == "hosted", unit["needs_secret"], unit["env"]),
                             (hosted_unit, hosted_unit, [KEY_VAR] if hosted_unit else []), unit["unit"])
        self.assertEqual(plan["hosted"], {"h-a": {"model": "lab-hosted-a", "max_calls": 300, "bound": 246,
                                                  "shares": {shard["shard"]: 246}}})
        self.assertEqual(sum(plan["hosted"]["h-a"]["shares"].values()), plan["hosted"]["h-a"]["bound"])
        self.assertLessEqual(plan["hosted"]["h-a"]["bound"], plan["hosted"]["h-a"]["max_calls"])
        self.assertEqual(sorted(plan["models"]), ["fake-a", "h-a"])
        self.assertEqual(outputs["has_units"], "true")

    def test_two_hosted_keys_share_one_shard(self) -> None:
        request = hosted_e1(models=["fake-a", "h-a", "h-b"], experiments={"e1": e1_block()},
                            hosted={"h-a": {"max_calls": 246}, "h-b": {"max_calls": 246}})
        code, _, stderr, plan, _ = self.plan(request, "true")
        self.assertEqual(code, 0, stderr)
        shard = hosted_shard(plan)
        self.assertEqual(len([s for s in plan["shards"] if s["kind"] == "hosted"]), 1)
        self.assertEqual({k: v["shares"] for k, v in plan["hosted"].items()},
                         {"h-a": {shard: 246}, "h-b": {shard: 246}})

    def test_an_e2_hosted_central_gets_its_own_shard(self) -> None:
        request = hosted_e1(provider=SERVER_PROGRAM, models=[MODEL_KEY, "h-a"], hosted={"h-a": {"max_calls": 32}},
                            experiments={"e2": _e2(models=[MODEL_KEY], central="h-a"),
                                         "g0": {**plumbing_min()["experiments"]["g0"], "models": [MODEL_KEY]}})
        code, _, stderr, plan, _ = self.plan(request, "true")
        self.assertEqual(code, 0, stderr)
        by_unit = {u["unit"]: u for u in plan["units"]}
        e2, g0 = by_unit[f"e2-{MODEL_KEY}"], by_unit[f"g0-{MODEL_KEY}"]
        self.assertNotEqual(e2["shard"], g0["shard"])
        self.assertEqual((e2["needs_secret"], e2["env"], e2["kind"]), (True, [KEY_VAR], "gguf"))
        self.assertEqual((g0["needs_secret"], g0["env"]), (False, []))
        matrix = {m["shard"]: m for m in plan["matrix"]["include"]}
        self.assertEqual((matrix[e2["shard"]]["hosted"], matrix[e2["shard"]]["gguf_key"]), (True, MODEL_KEY))
        self.assertEqual((matrix[g0["shard"]]["hosted"], matrix[g0["shard"]]["gguf_key"]), (False, MODEL_KEY))
        shard = next(s for s in plan["shards"] if s["shard"] == e2["shard"])
        self.assertEqual(shard["units"], [e2["unit"]])
        self.assertEqual(plan["hosted"]["h-a"]["shares"], {e2["shard"]: 2 * PREFLIGHT_MAX_CALLS + 2 * 5 * 1 * 2})
        self.assertEqual(sorted(plan["models"]), ["h-a", MODEL_KEY])

    def test_hosted_units_beyond_one_shard_are_refused(self) -> None:
        request = hosted_e1(job_minutes=45, experiments={"e1": e1_block(minutes=10)})
        code, _, stderr, plan, _ = self.plan(request, "true")
        self.assertEqual(code, 2)
        self.assertTrue(stderr.splitlines()[0].startswith(
            "error: $.experiments.e1.minutes: the hosted E1 units need more minutes than one shard holds"), stderr)
        code, _, stderr, plan, _ = self.plan(request, None, name="absent")
        self.assertEqual(code, 0, stderr)

    def test_plans_are_byte_identical(self) -> None:
        for has in ("true", None):
            path = write_json(self.tmp / "same.json", hosted_e1())
            blobs = []
            for name in ("A", "B"):
                out = self.tmp / f"{has}-{name}"
                with mock.patch.dict(os.environ, _env(**{HAS_VAR: has}), clear=True):
                    code, _, stderr = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out",
                                                str(out)])
                self.assertEqual(code, 0, stderr)
                blobs.append((out / "plan.json").read_bytes())
            self.assertEqual(blobs[0], blobs[1], has)

    def test_prereg_pins_the_external_endpoint(self) -> None:
        plan, plan_path = plan_with(self.tmp / "pp", hosted_e1())
        e1 = plan_path.parent / "prereg" / "e1"
        routing = _json(e1 / "routing.json")["endpoints"]
        self.assertEqual(routing["h-a"], hosted.endpoint(MANIFEST.models["h-a"], PLACEHOLDER_BASE_URL))
        self.assertEqual((routing["h-a"]["boundary"], routing["h-a"]["api_key_env"]), ("external", KEY_VAR))
        self.assertEqual(routing["fake-a"]["boundary"], "site:lab")
        prereg = _json(e1 / "prereg" / "prereg.json")
        self.assertEqual(prereg["allow_external_raw"], "synthetic")
        pinned = {e["name"]: e for e in prereg["endpoints"]}
        self.assertEqual((pinned["h-a"]["boundary"], pinned["h-a"]["model"]), ("external", "lab-hosted-a"))


# --------------------------------------------------------------------------------------------------- the secrets

class ConfigTests(unittest.TestCase):
    URL = "https://api.example.com/v1/"

    def test_states_and_problem_order(self) -> None:
        not_set = notes.HOSTED_SECRET_NOT_SET.format
        blank = notes.HOSTED_SECRET_BLANK.format
        cases = [
            ({}, ("not_set", "not_set"), not_set(name=KEY_VAR)),
            ({KEY_VAR: "", BASE_URL_VAR: ""}, ("not_set", "not_set"), not_set(name=KEY_VAR)),
            ({KEY_VAR: " \t\n", BASE_URL_VAR: self.URL}, ("blank", "present"), blank(name=KEY_VAR)),
            ({KEY_VAR: "sk lab", BASE_URL_VAR: "http://api.example.com/v1"}, ("not_printable", "invalid"),
             notes.HOSTED_KEY_NOT_TOKEN),
            ({KEY_VAR: "sk-léb", BASE_URL_VAR: self.URL}, ("not_printable", "present"),
             notes.HOSTED_KEY_NOT_TOKEN),
            ({KEY_VAR: KEY}, ("present", "not_set"), not_set(name=BASE_URL_VAR)),
            ({KEY_VAR: KEY, BASE_URL_VAR: "  "}, ("present", "blank"), blank(name=BASE_URL_VAR)),
            ({KEY_VAR: KEY, BASE_URL_VAR: "http://api.example.com/v1"}, ("present", "invalid"),
             notes.HOSTED_BASE_URL_INVALID),
            ({KEY_VAR: KEY, BASE_URL_VAR: self.URL}, ("present", "present"), None),
        ]
        for environ, states, problem in cases:
            with self.subTest(environ=environ):
                config = hosted.load_config(environ)
                self.assertEqual((config.secrets[KEY_VAR], config.secrets[BASE_URL_VAR]), states)
                self.assertEqual(config.problem, problem)
                if problem is not None:
                    self.assertEqual((config.key, config.base_url), (None, None))

    def test_values_are_stripped(self) -> None:
        config = hosted.load_config({KEY_VAR: f"  {KEY}\n", BASE_URL_VAR: f"\t{self.URL} \n"})
        self.assertEqual((config.key, config.base_url, config.problem), (KEY, self.URL, None))
        self.assertEqual((config.scheme, config.host), ("https", "api.example.com:443"))
        self.assertEqual(hosted.unit_environ({"PATH": "/bin"}, config), {"PATH": "/bin", KEY_VAR: KEY})

    def test_base_urls(self) -> None:
        accepted = {"http://127.0.0.1:8080/v1": ("http", "127.0.0.1:8080"),
                    "http://localhost:8080/v1": ("http", "localhost:8080"),
                    "http://[::1]:8080/v1": ("http", "[::1]:8080"),
                    "https://127.0.0.1/v1": ("https", "127.0.0.1:443"),
                    "https://api.example.com:8443/openai/v1": ("https", "api.example.com:8443"),
                    "https://8.8.8.8/v1": ("https", "8.8.8.8:443")}
        refused = ("http://api.example.com/v1", "http://8.8.8.8/v1", "https://api.example.com/v1?x=1",
                   "https://api.example.com/v1#frag", "https://user:pw@api.example.com/v1",
                   "https://user@api.example.com/v1", "https://10.0.0.1/v1", "https://192.168.1.2/v1",
                   "https://172.16.0.3/v1", "https://169.254.169.254/v1", "https://[fe80::1]/v1",
                   "https://0.0.0.0/v1", "https:///v1", "https://api.example.com:99999/v1", "ftp://api.example.com/v1",
                   "api.example.com/v1", "https://api example.com/v1")
        for url, (scheme, host) in accepted.items():
            with self.subTest(url=url):
                config = hosted.load_config({KEY_VAR: KEY, BASE_URL_VAR: url})
                self.assertEqual((config.problem, config.scheme, config.host), (None, scheme, host))
        for url in refused:
            with self.subTest(url=url):
                config = hosted.load_config({KEY_VAR: KEY, BASE_URL_VAR: url})
                self.assertEqual((config.secrets[BASE_URL_VAR], config.problem),
                                 ("invalid", notes.HOSTED_BASE_URL_INVALID))
                self.assertEqual((config.scheme, config.host, config.base_url), (None, None, None))

    def test_no_message_holds_a_value(self) -> None:
        keys = ("", " ", KEY, f" {KEY} ", "sk Qz55 key", "sk-éQz55")
        urls = ("", " ", f"https://api.example.com{PREFIX}", f"http://api.example.com{PREFIX}",
                f"https://u:Qz55@api.example.com{PREFIX}", f"https://10.0.0.1{PREFIX}")
        for key in keys:
            for url in urls:
                config = hosted.load_config({KEY_VAR: key, BASE_URL_VAR: url})
                shown = f"{config.problem} {config!r} {config.secrets} {config.host} {config.scheme}"
                for value in (key.strip(), url.strip(), PATH_TOKEN, "Qz55"):
                    if value:
                        self.assertNotIn(value, shown, (key, url))


# --------------------------------------------------------------------------------------------------- the sentinel run

class _Sentinel:
    """One dry run of hosted-e1.json with the hosted variables set, its hosted calls through a front whose path holds a
    sentinel to a fake behind it; shared by the classes that read it."""

    tmp: Path | None = None
    out: Path | None = None
    done: subprocess.CompletedProcess[str] | None = None
    base_url = ""
    front_requests: list[dict[str, Any]] = []
    fake_requests: list[dict[str, Any]] = []

    @classmethod
    def get(cls) -> "type[_Sentinel]":
        if cls.done is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-hosted-sentinel-"))
            cls.out = cls.tmp / "D"
            fake = FakeOpenAIServer("valid", responder=Responder(DQ)).start()
            front = PrefixFront(fake.base_url, PREFIX).start()
            try:
                cls.base_url = front.base_url
                cls.done = dry_run(cls.out, HOSTED_E1, MANIFEST_TEST,
                                   **{HAS_VAR: "true", KEY_VAR: KEY, BASE_URL_VAR: front.base_url})
                cls.front_requests, cls.fake_requests = list(front.requests), list(fake.requests)
            finally:
                front.stop()
                fake.stop()
        return cls


def tearDownModule() -> None:
    if _Sentinel.tmp is not None:
        kill_mentioning(str(_Sentinel.tmp))
        shutil.rmtree(_Sentinel.tmp, True)


class DryRunSentinelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shared = _Sentinel.get()
        cls.out, cls.done, cls.base_url = shared.out, shared.done, shared.base_url
        cls.front_requests, cls.fake_requests = shared.front_requests, shared.fake_requests

    def setUp(self) -> None:
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)

    def hosted_dir(self) -> Path:
        return self.out / "shards" / hosted_shard(_json(self.out / "plan" / "plan.json"))

    def test_no_sentinel_anywhere(self) -> None:
        needles = (KEY, PATH_TOKEN, self.base_url)
        self.assertEqual(files_holding(self.out, *needles), [])
        for text in (self.done.stdout, self.done.stderr):
            for needle in needles:
                self.assertNotIn(needle, text)
        summaries = sorted(self.out.rglob("*.md"))
        self.assertGreaterEqual(len(summaries), 4)

    def test_every_hosted_request_carried_the_key(self) -> None:
        self.assertTrue(self.front_requests)
        self.assertEqual({r["headers"].get("authorization") for r in self.front_requests}, {f"Bearer {KEY}"})
        self.assertEqual({r["path"] for r in self.front_requests}, {f"{PREFIX}/chat/completions", f"{PREFIX}/models"})
        self.assertEqual({r["path"] for r in self.fake_requests}, {"/v1/chat/completions", "/v1/models"})

    def test_routing_is_redacted_and_scratch_is_gone(self) -> None:
        root = self.hosted_dir()
        files = sorted((root / "routing").glob("e1-h-a-r*.json"))
        self.assertEqual([p.name for p in files], [f"{u}.json" for u in HOSTED_UNITS])
        for path in files:
            endpoint = _json(path)["endpoints"]["h-a"]
            self.assertEqual((endpoint["base_url"], endpoint["api_key_env"]), (REDACTED_BASE_URL, KEY_VAR))
        self.assertEqual(sorted(self.out.rglob("work")), [])
        self.assertEqual(sorted(self.out.rglob("hosted-routing")), [])

    def test_hosted_units_are_ok_and_plumbing(self) -> None:
        root = self.hosted_dir()
        for unit in HOSTED_UNITS:
            record = unit_json(root, unit)
            self.assertEqual((record["status"], record["measurement_class"], record["class_reason"]),
                             ("ok", "plumbing", "provider_override"))
            self.assertEqual(record["hosted"]["role"], "endpoint")
            self.assertIn("hosted_raw", record["notes"])
            self.assertNotIn("hosted_api", record["notes"])
            self.assertNotIn("runner_hardware", record["notes"])
        report = _json(self.out / "report" / "report.json")
        self.assertEqual({u["display_class"] for u in report["units"]}, {"plumbing"})
        self.assertEqual(report["contains_hosted"], False)

    def test_calls_and_cost_equal_the_wire_and_the_ledgers(self) -> None:
        report = _json(self.out / "report" / "report.json")
        chat = [r for r in self.front_requests if r["path"] == f"{PREFIX}/chat/completions"]
        block = report["hosted"]["h-a"]
        self.assertEqual(block["calls"], len(chat))
        root = self.hosted_dir()
        rows = [r for r in read_ledger(root / PREFLIGHT_LEDGER) if r["attempt"] >= 1]
        self.assertEqual(block["preflight_calls"], len(rows))
        for path in sorted((root / "runs" / "e1").glob("*/ledger.jsonl")):
            rows += [r for r in read_ledger(path) if r["attempt"] >= 1]
        self.assertEqual(len(rows), len(chat))
        self.assertEqual(block["estimated_usd"], round(sum(r["cost_usd"] for r in rows), 6))
        self.assertGreater(block["estimated_usd"], 0)
        self.assertEqual((block["tokens_in"], block["tokens_out"], block["tokens_missing"]),
                         (sum(r["tokens_in"] for r in rows), sum(r["tokens_out"] for r in rows), 0))
        self.assertEqual((block["model"], block["max_calls"], block["bound"], block["priced"],
                          block["unreadable_ledgers"]), ("lab-hosted-a", 300, 246, True, 0))


# --------------------------------------------------------------------------------------------------- shards

class ShardTests(TempTest):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(tempfile.mkdtemp(prefix="lab-hosted-shards-"))
        cls.plan_a, cls.plan_a_path = plan_with(cls.base / "a", hosted_e1())
        cls.plan_b, cls.plan_b_path = plan_with(
            cls.base / "b", hosted_e1(models=["fake-a", "h-b"], hosted={"h-b": {"max_calls": 300}}))

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.base))
        shutil.rmtree(cls.base, True)

    def run_hosted(self, env: dict[str, str]) -> tuple[int, Path, list[tuple[list[str], dict[str, str]]]]:
        out = self.tmp / f"S{len(list(self.tmp.iterdir()))}"
        code, _, stderr, seen = run_shard(self.plan_a_path, hosted_shard(self.plan_a), out, env)
        return code, out, seen

    def session(self, plan: dict[str, Any], plan_path: Path, env: dict[str, str]) -> hosted.Session:
        shard = next(s for s in plan["shards"] if s["kind"] == "hosted")
        return hosted.Session(plan, shard, self.tmp / "session", env, load_prereg(plan_path), sleep=lambda s: None)

    def first_unit(self, plan: dict[str, Any]) -> dict[str, Any]:
        return next(u for u in plan["units"] if u["kind"] == "hosted")

    def test_secret_problems_fail_every_hosted_unit_without_a_request(self) -> None:
        fake = self.fake()
        front = self.front(fake)
        cases = [
            ({KEY_VAR: "   ", BASE_URL_VAR: front.base_url}, notes.HOSTED_SECRET_BLANK.format(name=KEY_VAR)),
            ({KEY_VAR: KEY, BASE_URL_VAR: " \t "}, notes.HOSTED_SECRET_BLANK.format(name=BASE_URL_VAR)),
            ({BASE_URL_VAR: front.base_url}, notes.HOSTED_SECRET_NOT_SET.format(name=KEY_VAR)),
            ({KEY_VAR: KEY, BASE_URL_VAR: ""}, notes.HOSTED_SECRET_NOT_SET.format(name=BASE_URL_VAR)),
            ({KEY_VAR: "sk lab", BASE_URL_VAR: front.base_url}, notes.HOSTED_KEY_NOT_TOKEN),
            ({KEY_VAR: KEY, BASE_URL_VAR: f"http://api.example.com{PREFIX}"}, notes.HOSTED_BASE_URL_INVALID),
            ({KEY_VAR: KEY, BASE_URL_VAR: f"https://10.1.2.3{PREFIX}"}, notes.HOSTED_BASE_URL_INVALID),
        ]
        for values, reason in cases:
            with self.subTest(reason=reason):
                code, out, seen = self.run_hosted(_env(**values))
                self.assertEqual(code, 1)
                for unit in HOSTED_UNITS:
                    record = unit_json(out, unit)
                    self.assertEqual((record["status"], record["status_reason"], record["argv"]),
                                     ("failed", reason, []))
                self.assertEqual(seen, [])
                block = _json(out / "provenance.json")["hosted"]
                self.assertEqual(block["problem"], reason)
                self.assertEqual(block["keys"]["h-a"]["preflight"], None)
                self.assertEqual(files_holding(out, KEY, PATH_TOKEN), [])
        self.assertEqual((front.requests, fake.requests), ([], []))
        # the local units of the same run are unaffected
        out = self.tmp / "local"
        shard = next(s["shard"] for s in self.plan_a["shards"] if s["kind"] == "fake")
        code, _, stderr, _ = run_shard(self.plan_a_path, shard, out, _env(**{KEY_VAR: " ", BASE_URL_VAR: " "}))
        self.assertEqual(code, 0, stderr)
        self.assertEqual({unit_json(out, u)["status"] for u in FAKE_UNITS}, {"ok"})

    def test_preflight_refusals(self) -> None:
        cases = [("unauthorized", "hosted preflight failed: HTTP 401"),
                 ("rejects-json_schema", "hosted preflight failed: HTTP 400"),
                 ("truncated", notes.HOSTED_LENGTH.format(task="extract_claims"))]
        for persona, reason in cases:
            with self.subTest(persona=persona):
                fake = self.fake(persona)
                front = self.front(fake)
                code, out, seen = self.run_hosted(_env(**{KEY_VAR: KEY, BASE_URL_VAR: front.base_url}))
                self.assertEqual(code, 1)
                self.assertEqual(len(fake.chat_requests), 1)
                self.assertEqual(seen, [])
                for unit in HOSTED_UNITS:
                    record = unit_json(out, unit)
                    self.assertEqual((record["status"], record["argv"]), ("failed", []))
                    self.assertTrue(record["status_reason"].startswith(reason), record["status_reason"])
                    if persona != "truncated":
                        self.assertIn("switch response_format from json_schema to json_object to none",
                                      record["status_reason"])
                preflight = _json(out / "provenance.json")["hosted"]["keys"]["h-a"]["preflight"]
                self.assertEqual((preflight["status"], preflight["calls"]), ("failed", 1))
                self.assertTrue(preflight["reason"].startswith(reason))
                self.assertEqual(files_holding(out, KEY, PATH_TOKEN), [])

    def test_a_lab_retry_passes_the_first_500(self) -> None:
        fake = self.fake("http500-then-ok")
        session = self.session(self.plan_b, self.plan_b_path, _env(**{KEY_VAR: KEY, BASE_URL_VAR: fake.base_url}))
        with mock.patch.object(hosted, "PREFLIGHT_WAIT_S", 0):
            handle = session.gate(self.first_unit(self.plan_b), budget_s=600)
        self.assertIsInstance(handle, hosted.Handle)
        preflight = session.block["keys"]["h-b"]["preflight"]
        self.assertEqual((preflight["status"], preflight["calls"], preflight["tasks"][0]["calls"]), ("passed", 2, 2))
        self.assertEqual(len(fake.chat_requests), 2)
        self.assertEqual((handle.calls_before, session.calls_used("h-b")), (2, 2))

    def test_a_persistent_503_is_unavailable(self) -> None:
        fake = self.fake()
        front = self.front(fake, fail_status=503)
        session = self.session(self.plan_b, self.plan_b_path, _env(**{KEY_VAR: KEY, BASE_URL_VAR: front.base_url}))
        with mock.patch.object(hosted, "PREFLIGHT_WAIT_S", 0):
            got = session.gate(self.first_unit(self.plan_b), budget_s=600)
        self.assertEqual(got, ("failed", notes.HOSTED_UNAVAILABLE.format(detail="HTTP 503",
                                                                         calls=PREFLIGHT_MAX_CALLS)))
        self.assertEqual(len(front.chat_requests), PREFLIGHT_MAX_CALLS)
        self.assertEqual(fake.chat_requests, [])
        # a failed preflight is cached: the next unit of the key sends nothing
        unit = [u for u in self.plan_b["units"] if u["kind"] == "hosted"][1]
        self.assertEqual(session.gate(unit, budget_s=600)[0], "failed")
        self.assertEqual(len(front.chat_requests), PREFLIGHT_MAX_CALLS)

    def test_a_redirect_is_never_followed(self) -> None:
        target = self.fake()
        decoy = self.fake()
        front = self.front(decoy, redirect_to=target.base_url + "/chat/completions")
        session = self.session(self.plan_a, self.plan_a_path, _env(**{KEY_VAR: KEY, BASE_URL_VAR: front.base_url}))
        got = session.gate(self.first_unit(self.plan_a), budget_s=600)
        self.assertEqual(got[0], "failed")
        self.assertTrue(got[1].startswith("hosted preflight failed: HTTP 307"), got[1])
        self.assertEqual((target.requests, decoy.requests), ([], []))
        self.assertEqual(len(front.chat_requests), 1)

    def test_too_little_time_skips_without_a_request(self) -> None:
        fake = self.fake()
        session = self.session(self.plan_a, self.plan_a_path, _env(**{KEY_VAR: KEY, BASE_URL_VAR: fake.base_url}))
        self.assertEqual(session.gate(self.first_unit(self.plan_a), budget_s=0.5),
                         ("skipped", notes.BUDGET_EXHAUSTED))
        self.assertEqual(fake.requests, [])

    def test_a_stripped_key_and_the_provenance_block(self) -> None:
        fake = self.fake()
        front = self.front(fake)
        session = self.session(self.plan_a, self.plan_a_path,
                               _env(**{KEY_VAR: f"  {KEY}\n", BASE_URL_VAR: f" {front.base_url}\n"}))
        handle = session.gate(self.first_unit(self.plan_a), budget_s=600)
        self.assertIsInstance(handle, hosted.Handle)
        self.assertEqual({r["headers"]["authorization"] for r in front.requests}, {f"Bearer {KEY}"})
        self.assertEqual({r["headers"]["authorization"] for r in fake.requests}, {f"Bearer {KEY}"})
        self.assertEqual(handle.environ[KEY_VAR], KEY)
        self.assertNotIn(BASE_URL_VAR, units.subprocess_env(handle.environ, [KEY_VAR]))
        self.assertNotIn(KEY, repr(handle))
        self.assertNotIn(PATH_TOKEN, repr(handle))
        block = session.block
        port = front.base_url.split(":")[2].split("/")[0]
        self.assertEqual(set(block), {"secrets", "scheme", "host", "problem", "keys"})
        self.assertEqual((block["scheme"], block["host"], block["problem"]), ("http", f"127.0.0.1:{port}", None))
        self.assertEqual(block["secrets"], {KEY_VAR: "present", BASE_URL_VAR: "present"})
        key = block["keys"]["h-a"]
        self.assertEqual(set(key), {"model", "response_format", "transport_schema", "deadline_s", "max_retries",
                                    "priced", "share", "calls_used", "models_list", "preflight"})
        self.assertEqual(key["models_list"], {"http_status": 200, "ok": True, "listed": 1, "model_listed": False})
        self.assertEqual((key["share"], key["calls_used"], key["priced"]), (246, 1, True))
        self.assertEqual(set(key["preflight"]["tasks"][0]), {"task", "calls", "http_status", "error_kind",
                                                             "finish_reason", "latency_ms", "tokens_in", "tokens_out"})
        self.assertNotIn(PATH_TOKEN, json.dumps(block))
        record = units.hosted_record(handle)
        self.assertEqual(record, {"key": "h-a", "role": "endpoint", "model": "lab-hosted-a", "scheme": "http",
                                  "host": f"127.0.0.1:{port}", "response_format": "json_schema",
                                  "transport_schema": "full", "share": 246, "calls_before": 1, "calls_after": None})


class CapTests(TempTest):
    def test_the_share_caps_the_units_a_shard_starts(self) -> None:
        plan, plan_path = plan_with(self.tmp / "p", hosted_e1())
        shard = hosted_shard(plan)
        plan["hosted"]["h-a"]["shares"][shard] = 2
        plan_path.write_text(json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        shutil.rmtree(plan_path.parent / "prereg")
        code, _, stderr = run_prereg(plan_path)
        self.assertEqual(code, 0, stderr)
        fake = self.fake()
        out = self.tmp / "S"
        code, _, stderr, seen = run_shard(plan_path, shard, out, _env(**{KEY_VAR: KEY, BASE_URL_VAR: fake.base_url}))
        self.assertEqual(code, 1, stderr)
        self.assertEqual(unit_json(out, "e1-h-a-r1")["status"], "ok")
        for unit in ("e1-h-a-r2", "e1-h-a-r3"):
            record = unit_json(out, unit)
            self.assertEqual((record["status"], record["status_reason"], record["argv"]),
                             ("skipped", notes.HOSTED_MAX_CALLS, []))
        self.assertEqual(len(fake.chat_requests), 1 + 40)
        self.assertEqual(len(seen), 1)
        record = unit_json(out, "e1-h-a-r1")
        self.assertEqual((record["hosted"]["share"], record["hosted"]["calls_before"],
                          record["hosted"]["calls_after"]), (2, 1, 41))
        self.assertEqual(_json(out / "provenance.json")["hosted"]["keys"]["h-a"]["calls_used"], 41)

    def test_calls_used_counts_preflight_rows_and_an_unreadable_ledger_is_the_share(self) -> None:
        plan, plan_path = plan_with(self.tmp / "p", hosted_e1())
        fake = self.fake()
        shard = next(s for s in plan["shards"] if s["kind"] == "hosted")
        session = hosted.Session(plan, shard, self.tmp / "o", _env(**{KEY_VAR: KEY, BASE_URL_VAR: fake.base_url}),
                                 load_prereg(plan_path))
        self.assertEqual(session.calls_used("h-a"), 0)
        unit = next(u for u in plan["units"] if u["kind"] == "hosted")
        handle = session.gate(unit, budget_s=600)
        self.assertEqual((handle.calls_before, session.calls_used("h-a")), (1, 1))
        ledger = self.tmp / "o" / "runs" / "e1" / unit["run_id"] / "ledger.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"partial": ', encoding="utf-8")
        self.assertEqual(session.calls_used("h-a"), 246)
        ledger.unlink()
        self.assertEqual(session.calls_used("h-a"), 1)
        with open(self.tmp / "o" / PREFLIGHT_LEDGER, "a", encoding="utf-8") as fh:
            fh.write('{"partial": ')
        self.assertEqual(session.calls_used("h-a"), 246)
        self.assertEqual(session.gate(unit, budget_s=600), ("skipped", notes.HOSTED_MAX_CALLS))


# --------------------------------------------------------------------------------------------------- classes

def _record(**changes: Any) -> dict[str, Any]:
    return {"status": "ok", "measurement_class": "hosted-api", "kind": "hosted", "provider": "llama-server",
            "fake_rows": 0, "class_checks": {k: True for k in units.HOSTED_CHECK_KEYS}, **changes}


REAL = {"result_class": "real", "provider": "llama-server"}


class ClassTests(TempTest):
    def test_a_real_hosted_run_is_hosted_api(self) -> None:
        request = hosted_e1(provider=SERVER_PROGRAM, models=[MODEL_KEY, "h-a"],
                            experiments={"e1": e1_block(reference=MODEL_KEY)})
        plan, plan_path = plan_with(self.tmp / "p", request)
        fake = self.fake()
        front = self.front(fake, strip_fake=True)
        out = self.tmp / "S"
        code, _, stderr, seen = run_shard(plan_path, hosted_shard(plan), out,
                                          _env(**{KEY_VAR: f" {KEY}\n", BASE_URL_VAR: front.base_url}))
        self.assertEqual(code, 0, stderr)
        provenance = _json(out / "provenance.json")
        self.assertEqual((provenance["result_class"], provenance["server"]), ("real", {"kind": "hosted"}))
        for unit in HOSTED_UNITS:
            record = unit_json(out, unit)
            self.assertEqual((record["status"], record["measurement_class"], record["class_reason"]),
                             ("ok", "hosted-api", "hosted_verified"), record["status_reason"])
            self.assertEqual(record["class_checks"], {k: True for k in units.HOSTED_CHECK_KEYS})
            self.assertEqual(units.display_class(record, provenance), "hosted-api")
            self.assertEqual(record["notes"], ["synthetic", "hosted_api", "hosted_raw"])
            self.assertEqual(record["fake_rows"], 0)
        self.assertEqual(len(seen), 3)
        for argv, env in seen:
            self.assertEqual(env[KEY_VAR], KEY)
            self.assertNotIn(BASE_URL_VAR, env)
            self.assertTrue(set(env) <= {*units.ENV_ALLOWLIST, "PYTHONUNBUFFERED", KEY_VAR}, sorted(env))
            self.assertNotIn(KEY, " ".join(argv))
            self.assertNotIn(PATH_TOKEN, " ".join(argv))
        self.assertEqual({r["headers"]["authorization"] for r in fake.requests}, {f"Bearer {KEY}"})
        self.assertEqual(files_holding(out, KEY, PATH_TOKEN), [])
        self.assertEqual(lab_shard.seal(out, hosted_shard(plan), {"run": "success"}, str(plan_path)), 0)
        self.assertFalse((out / "work").exists())
        md, sources = summary.render_shard(out)
        check_sources(self, md, sources, out)
        self.assertEqual(md.splitlines()[0], f"## {notes.HEADINGS['shard']} `{hosted_shard(plan)}`")
        self.assertIn(f"### {notes.HEADINGS['shard-hosted']}", md)

    def test_hosted_checks_and_measurement_class(self) -> None:
        rows = [{"host": "h:443", "fake_marker": False}, {"host": "h:443", "fake_marker": False}]
        checks = units.hosted_checks(rows, "ok", True, "h:443")
        self.assertEqual(checks, {k: True for k in units.HOSTED_CHECK_KEYS})
        self.assertEqual(list(checks), list(units.HOSTED_CHECK_KEYS))
        self.assertEqual(units.hosted_checks(rows, "ok", None, "h:443")["harness_measurement"], True)
        self.assertEqual(units.hosted_checks(rows, "ok", False, "h:443")["harness_measurement"], False)
        self.assertEqual(units.hosted_checks(rows, "invalid", True, "h:443")["participation"], False)
        self.assertEqual(units.hosted_checks([*rows, {"host": "x:443"}], "ok", True, "h:443")["hosted_host"], False)
        self.assertEqual(units.measurement_class("hosted", None, rows, checks), ("hosted-api", "hosted_verified"))
        self.assertEqual(units.measurement_class("hosted", None, rows, None), ("unverified", "no_evidence"))
        for key in units.HOSTED_CHECK_KEYS:
            with self.subTest(key=key):
                self.assertEqual(units.measurement_class("hosted", None, rows, {**checks, key: False}),
                                 ("unverified", key))
                missing = {k: v for k, v in checks.items() if k != key}
                self.assertEqual(units.measurement_class("hosted", None, rows, missing), ("unverified", key))
        self.assertEqual(units.measurement_class("hosted", "fake", rows, checks), ("plumbing", "provider_override"))
        self.assertEqual(units.measurement_class("hosted", None, [{"fake_marker": True}], checks),
                         ("plumbing", "fake_marker"))
        self.assertEqual(units.measurement_class("gguf", None, rows, checks)[0], "unverified")

    def test_display_class_matrix(self) -> None:
        self.assertEqual(units.display_class(_record(), REAL), "hosted-api")
        cases = [
            (_record(fake_rows=1), REAL, "plumbing"),
            (_record(), {"result_class": "plumbing", "provider": "llama-server"}, "plumbing"),
            (_record(), {"result_class": "real", "provider": "fake"}, "plumbing"),
            (_record(provider="fake"), REAL, "plumbing"),
            (_record(measurement_class="plumbing"), REAL, "plumbing"),
            (_record(class_checks={"hosted_host": True, "participation": True}), REAL, "unverified"),
            (_record(class_checks={**{k: True for k in units.HOSTED_CHECK_KEYS}, "hosted_host": False}), REAL,
             "unverified"),
            (_record(class_checks=None), REAL, "unverified"),
            (_record(fake_rows=None), REAL, "unverified"),
            (_record(kind="gguf"), REAL, "unverified"),
            (_record(measurement_class="model"), REAL, "unverified"),
            (_record(status="failed"), REAL, "no-result"),
            (_record(), None, "unverified"),
        ]
        for record, provenance, expected in cases:
            with self.subTest(record=record, provenance=provenance):
                self.assertEqual(units.display_class(record, provenance), expected)
        self.assertEqual(units.DISPLAY_CLASSES, ("model", "hosted-api", "unverified", "plumbing", "no-model",
                                                 "no-result"))

    def test_unit_notes_per_role(self) -> None:
        self.assertEqual(units.unit_notes("e1", "hosted-api", "endpoint"), ["synthetic", "hosted_api", "hosted_raw"])
        self.assertEqual(units.unit_notes("e1", "plumbing", "endpoint"), ["plumbing", "synthetic", "hosted_raw"])
        self.assertEqual(units.unit_notes("e1", "unverified", "endpoint"), ["synthetic", "hosted_api", "hosted_raw"])
        self.assertEqual(units.unit_notes("e2", "model", "central"),
                         ["model_measurement", "synthetic", "runner_hardware", "text_only_scan", "central_hosted",
                          "hosted_raw"])
        self.assertEqual(units.unit_notes("e1", "model"), ["model_measurement", "synthetic", "runner_hardware"])
        for key in ("hosted_api", "hosted_raw", "central_hosted"):
            self.assertIn(key, notes.NOTES)

    def test_model_checks_with_hosted_central_rows(self) -> None:
        evidence, rows = _evidence()
        central = [{"host": "api.example:443", "model_served": "lab-hosted-a", "http_status": 200,
                    "fake_marker": False}]
        checks, _ = units.model_checks(evidence, rows, "ok", True, "alias", "127.0.0.1:9", hosted_rows=central,
                                       hosted_host="api.example:443")
        self.assertEqual(checks, {k: True for k in units.CHECK_KEYS})
        moved = [*central, {**central[0], "host": "other.example:443"}]
        checks, _ = units.model_checks(evidence, rows, "ok", True, "alias", "127.0.0.1:9", hosted_rows=moved,
                                       hosted_host="api.example:443")
        self.assertEqual((checks["ledger_host"], checks["model_served"]), (False, True))
        checks, _ = units.model_checks(evidence, [*rows, *central], "ok", True, "alias", "127.0.0.1:9")
        self.assertEqual((checks["ledger_host"], checks["model_served"]), (False, False))


# --------------------------------------------------------------------------------------------------- E2 central

class E2CentralTests(TempTest):
    def test_a_hosted_central_on_a_stub_model_server(self) -> None:
        request = hosted_e1(provider=SERVER_PROGRAM, models=[MODEL_KEY, "h-a"], hosted={"h-a": {"max_calls": 32}},
                            experiments={"e2": _e2(models=[MODEL_KEY], central="h-a"),
                                         "g0": {**plumbing_min()["experiments"]["g0"], "models": [MODEL_KEY]}})
        with mock.patch.dict(os.environ, _env(**{HAS_VAR: "true"}), clear=True):
            world = StubWorld(self.tmp, request=request, model={"e2_ctx_per_slot": 32768},
                              server_config={"pack": "device_quality"}, extra_models={"h-a": H_A})
        self.addCleanup(world.close)
        e2 = next(u for u in world.plan["units"] if u["experiment"] == "e2")
        shard = next(s for s in world.plan["shards"] if s["shard"] == e2["shard"])
        self.assertEqual((shard["units"], shard["needs_secret"]), ([e2["unit"]], True))
        out = self.tmp / "S"
        # the stub's files are a few KiB; the provision preflight's disk check (a GiB beyond the file) is not under
        # test here and would refuse on a nearly full scratch disk
        with mock.patch.object(lab_provision, "disk_free", return_value=1 << 40):
            world.provision_all()
            code, _, stderr = world.prepare(out, shard["shard"])
        self.assertEqual(code, 0, stderr)
        fake = self.fake()
        front = self.front(fake)
        env = _env(**{KEY_VAR: KEY, BASE_URL_VAR: front.base_url})
        seen: list[dict[str, str]] = []
        real = units.run_process

        def spy(argv: list[str], environ: dict[str, str], *args: Any, **kwargs: Any) -> Any:
            seen.append(dict(environ))
            return real(argv, environ, *args, **kwargs)

        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(units, "run_process", spy):
            code, _, stderr = world.run(out, shard["shard"])
        record = unit_json(out, e2["unit"])
        self.assertEqual(record["status"], "ok", record["status_reason"])
        self.assertEqual(code, 0, stderr)
        environs = world.stub_files("environ")
        self.assertTrue(environs)
        for environ in environs:
            self.assertEqual(sorted(environ), ["HOME", "LANG", "PATH", "TMPDIR"])
            self.assertNotIn(KEY, json.dumps(environ))
        self.assertEqual(files_holding(out, KEY, PATH_TOKEN, front.base_url), [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][KEY_VAR], KEY)
        self.assertNotIn(BASE_URL_VAR, seen[0])
        routing = out / "routing" / e2["unit"]
        sites = sorted((routing / "sites").glob("*.json"))
        self.assertTrue(sites)
        for path in sites:
            self.assertNotIn("api_key_env", path.read_text(encoding="utf-8"))
        central = _json(routing / "central.json")["endpoints"]["lab-central"]
        self.assertEqual((central["base_url"], central["api_key_env"], central["boundary"], central["model"]),
                         (REDACTED_BASE_URL, KEY_VAR, "external", "lab-hosted-a"))
        chat = front.chat_requests
        self.assertGreater(len(chat), 2)
        self.assertEqual({r["headers"]["authorization"] for r in chat}, {f"Bearer {KEY}"})
        warm = {r["task"] for r in read_ledger(out / "server" / "warmup.ledger.jsonl")}
        self.assertIn("judge_record", warm)
        self.assertFalse(warm & {"judge_candidate_raw", "judge_candidate_allowed"}, warm)
        projection = record["projection"]
        self.assertIsNotNone(projection)
        pre = [r for r in read_ledger(out / PREFLIGHT_LEDGER) if r["ok"] is True]
        for task, key in (("judge_candidate_raw", "raw_s"), ("judge_candidate_allowed", "allowed_s")):
            self.assertEqual(projection["inputs"][key], max(r["latency_ms"] for r in pre if r["task"] == task) / 1000)
        self.assertIn("central_hosted", record["notes"])
        self.assertIn("hosted_raw", record["notes"])
        self.assertEqual((record["hosted"]["role"], record["hosted"]["key"]), ("central", "h-a"))
        preflight = _json(out / "provenance.json")["hosted"]["keys"]["h-a"]["preflight"]
        self.assertEqual(([t["task"] for t in preflight["tasks"]], preflight["status"]),
                         (["judge_candidate_raw", "judge_candidate_allowed"], "passed"))
        self.assertEqual(sorted(record["class_checks"]), sorted(units.CHECK_KEYS))


# --------------------------------------------------------------------------------------------------- the report

def _section(md: str, heading: str) -> str:
    parts = md.split(heading, 1)
    if len(parts) < 2:
        return ""
    return re.split(r"\n#{2,3} ", parts[1], 1)[0]


class AggregateSummaryTests(TempTest):
    @classmethod
    def setUpClass(cls) -> None:
        shared = _Sentinel.get()
        cls.out, cls.done = shared.out, shared.done

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        self.tree = DryTree(self.out, self.tmp / "T")
        plan = self.tree.plan
        self.hosted = hosted_shard(plan)
        self.local = next(s["shard"] for s in plan["shards"] if s["kind"] == "fake")

    def test_class_of(self) -> None:
        self.assertEqual(_class_of([], "plumbing"), "plumbing")
        self.assertEqual(_class_of(["model", "model"], "x"), "model")
        self.assertEqual(_class_of(["model", "hosted-api"], "x"), "hosted-api")
        self.assertEqual(_class_of(["hosted-api"], "x"), "hosted-api")
        self.assertEqual(_class_of(["hosted-api", "unverified"], "x"), "unverified")
        self.assertEqual(_class_of(["hosted-api", "plumbing"], "x"), "plumbing")

    def test_tokens_missing_and_an_unpriced_model(self) -> None:
        unit = "e1-h-a-r1"
        record = self.tree.read(self.hosted, f"units/{unit}/unit.json")
        rel = next(r for r in record["files"] if r.endswith("/ledger.jsonl"))
        lines = self.tree.path(self.hosted, rel).read_bytes().splitlines()
        first = json.loads(lines[0])
        first["tokens_out"] = None
        data = b"\n".join([json.dumps(first, sort_keys=True, separators=(",", ":")).encode(), *lines[1:]]) + b"\n"
        self.tree.replace_unit_file(self.hosted, unit, rel, data)
        self.tree.reseal(self.hosted)
        code, _, stderr, report = self.tree.aggregate(self.tmp / "R1")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((report["hosted"]["h-a"]["tokens_missing"], report["hosted"]["h-a"]["priced"]), (1, True))
        self.tree.set_plan(lambda plan: plan["models"]["h-a"].update(price=None))
        code, _, stderr, report = self.tree.aggregate(self.tmp / "R2")
        self.assertEqual(code, 0, stderr)
        block = report["hosted"]["h-a"]
        self.assertEqual((block["priced"], block["estimated_usd"], block["tokens_missing"]), (False, None, 1))
        md, sources = summary.render_report(self.tmp / "R2")
        check_sources(self, md, sources, self.tmp / "R2")
        self.assertIn(notes.HOSTED_COST_NOTE, md.splitlines())

    def test_hosted_api_units_and_the_e1_block(self) -> None:
        self.tree.make_hosted(self.hosted, HOSTED_UNITS)
        code, _, stderr, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, stderr)
        rows = {u["unit"]: u for u in report["units"]}
        self.assertEqual({rows[u]["display_class"] for u in HOSTED_UNITS}, {"hosted-api"})
        self.assertEqual({rows[u]["cpu_model"] for u in HOSTED_UNITS}, {None})
        self.assertEqual((report["contains_hosted"], report["contains_measurements"]), (True, False))
        latency = [r for r in report["latency"] if r["model"] == "h-a"]
        self.assertTrue(latency)
        self.assertEqual({(r["display_class"], r["cpu_model"]) for r in latency}, {("hosted-api", None)})
        self.assertEqual((report["e1"]["hosted_endpoints"], report["e1"]["display_class"]), (["h-a"], "plumbing"))
        md, sources = summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn(f"### {notes.HEADINGS['hosted-api']}", md.splitlines())
        section = _section(md, f"### {notes.HEADINGS['hosted-api']}")
        for unit in HOSTED_UNITS:
            self.assertIn(f"`{unit}`", section)
        self.assertIn(f"### {notes.HEADINGS['hosted']}", md.splitlines())
        self.assertIn(notes.HOSTED_COST_NOTE, md.splitlines())
        self.assertIn(summary.ids(notes.E1_HOSTED_LABEL), md.splitlines())
        for key in ("hosted_api", "hosted_raw"):
            self.assertIn(notes.NOTES[key], md.splitlines())
        self.assertNotIn(PATH_TOKEN, md)
        text, entries = summary.render_shard(self.tree.path(self.hosted))
        check_sources(self, text, entries, self.tree.path(self.hosted))
        self.assertEqual(text.splitlines()[0], f"## {notes.HEADINGS['shard']} `{self.hosted}`")
        self.assertIn(f"### {notes.HEADINGS['shard-hosted']}", text.splitlines())

    def test_e1_verdicts_shown_for_a_hosted_api_measurement(self) -> None:
        self.tree.make_hosted(self.hosted, HOSTED_UNITS)
        self.tree.make_real(self.local, model_units=FAKE_UNITS)
        code, _, stderr, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, stderr)
        block = report["e1"]
        self.assertEqual((block["compared"], block["display_class"], block["measurement"], block["verdicts_shown"]),
                         (True, "hosted-api", False, False))
        real = units.run_process

        def measured(argv: list[str], *args: Any, **kwargs: Any) -> Any:
            result = real(argv, *args, **kwargs)
            runs_dir = next(a.split("=", 1)[1] for a in argv if a.startswith("--runs-dir="))
            path = Path(runs_dir).parent / E1_JSON
            doc = _json(path)
            doc["measurement"] = True
            path.write_text(json.dumps(doc), encoding="utf-8")
            return result

        with mock.patch.object(units, "run_process", measured):
            code, _, stderr, report = self.tree.aggregate(self.tmp / "R2")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((report["e1"]["display_class"], report["e1"]["verdicts_shown"]), ("hosted-api", True))
        self.assertEqual((report["contains_hosted"], report["contains_measurements"]), (True, True))
        md, sources = summary.render_report(self.tmp / "R2")
        check_sources(self, md, sources, self.tmp / "R2")
        section = _section(md, f"### {notes.HEADINGS['hosted-api']}")
        self.assertIn(notes.COLUMNS["non_inferior"], section)

    def test_first_line_rule(self) -> None:
        self.tree.make_hosted(self.hosted, HOSTED_UNITS)
        code, _, stderr, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, stderr)
        report_path = self.tmp / "R" / "report.json"
        for contains_hosted, first in ((True, f"## {notes.HEADINGS['report']}"), (False, notes.NO_MEASUREMENT_LINE)):
            with self.subTest(contains_hosted=contains_hosted):
                report.update(result_class="real", contains_measurements=False, contains_hosted=contains_hosted)
                report_path.write_text(json.dumps(report), encoding="utf-8")
                md, _ = summary.render_report(self.tmp / "R")
                self.assertEqual(md.splitlines()[0], first)

    def test_plan_summary_tables(self) -> None:
        md, sources = summary.render_plan(self.out / "plan")
        check_sources(self, md, sources, self.out / "plan")
        hosted_table = _section(md, f"### {notes.HEADINGS['plan-hosted']}")
        self.assertIn("| `h-a` | `lab-hosted-a` | 300 | 246 |", hosted_table)
        models = _section(md, f"### {notes.HEADINGS['models']}")
        self.assertIn("| `h-a` | `hosted` | `lab-hosted-a` |", models)
        self.assertNotIn(notes.HEADINGS["plan-skipped"], md)
        self.assertNotIn(PATH_TOKEN, md)


if __name__ == "__main__":
    unittest.main()
