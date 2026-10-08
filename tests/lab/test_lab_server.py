"""The model server's lifecycle against the stub binary: argv and environment, start, health, settings, stop, the
warm-up, the watchdog and restart budget, whole gguf shards through the stub hub (provision, prepare, run, seal in
process), and the classifier that alone may call a number a model measurement.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from lab import hostinfo, server, units
from lab import shard as lab_shard
from lab.notes import (ALIAS_MISMATCH, BUDGET_EXHAUSTED, CONTEXT_TOO_SMALL, NOT_PREPARED, OOM_HINT, REMEDY_CONTEXT,
                       REMEDY_HTTP, REMEDY_REASONING, SERVER_EXITED, SERVER_NOT_HEALTHY, SERVER_SETTINGS,
                       SERVER_START_FAILED, SERVER_UNAVAILABLE, SERVER_UNHEALTHY_AFTER, SHARD_INTERRUPTED, WARMUP_HTTP,
                       WARMUP_LENGTH, WARMUP_REASONING)
from lab.server import ModelServer, ServerError, ServerSpec, server_argv, server_env, thread_counts
from lab.warmup import response_format, warm_tasks, warm_up
from mycelic.collective.edge.extract import TASK_NAME
from mycelic.collective.experiments.e3_latency import WORKLOADS, filler
from mycelic.collective.inference.routing import parse_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.packs.loader import load_pack
from mycelic.collective.schemacheck import compile as compile_schema
from lab.plan import run_id
from tests.lab.helpers import (MANIFEST_TEST, MODEL_KEY, ROOT, StubWorld, call_main, gguf_request, kill_mentioning,
                               lab_env, make_plan, pids_mentioning, wait_until, write_json)
from tests.lab.stubs.fake_server_stub import write_launcher

TAG = "b7"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _units(out: Path) -> dict[str, dict[str, Any]]:
    return {p.parent.name: _json(p) for p in sorted(out.glob("units/*/unit.json"))}


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-server-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self._no_process_left)
        self.addCleanup(kill_mentioning, str(self.tmp))

    def _no_process_left(self) -> None:
        self.assertTrue(wait_until(lambda: not pids_mentioning(str(self.tmp)), 10), pids_mentioning(str(self.tmp)))

    def stub_server(self, config: dict[str, Any] | None = None, **spec: Any) -> ModelServer:
        launcher = write_launcher(self.tmp / "bin" / "server-stub",
                                  {"record_dir": str(self.tmp / "records"), "tag": TAG, **(config or {})})
        model = self.tmp / "models" / "model.gguf"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"GGUF")
        fields = {"binary": launcher, "model_path": model, "alias": "stub-alias", "tag": TAG, "slots": 1,
                  "ctx_per_slot": 16384, "seed": 7, "threads": 2, "threads_batch": 2, "cache_ram_mib": 256,
                  "extra_args": (), **spec}
        health = fields.pop("health_deadline_s", 30)
        instance = ModelServer(ServerSpec(**fields), out=self.tmp / "OUT", key="k", index=0,
                               health_deadline_s=health)
        self.addCleanup(instance.stop)
        return instance

    def records(self, kind: str) -> list[Any]:
        return [_json(p) for p in sorted((self.tmp / "records").glob(f"{kind}-*.json"),
                                         key=lambda p: int(p.stem.split("-")[1]))]


class WorldTest(TempDirTest):
    def world(self, **kwargs: Any) -> StubWorld:
        world = StubWorld(self.tmp / f"w{len(getattr(self, '_worlds', []))}", **kwargs)
        self._worlds = [*getattr(self, "_worlds", []), world]
        self.addCleanup(world.close)
        return world

    def prepared(self, world: StubWorld, out: Path | None = None) -> Path:
        world.provision_all()
        out = out or world.tmp / "OUT"
        code, stdout, stderr = world.prepare(out)
        self.assertEqual(code, 0, stderr)
        return out

    def server_argvs(self, world: StubWorld) -> list[list[str]]:
        return [argv for argv in world.stub_files("argv") if "-m" in argv]


# --------------------------------------------------------------------------------------------------- argv and env

class ArgvEnvTests(TempDirTest):
    def test_argv_exact_shape(self) -> None:
        spec = ServerSpec(binary=Path("/x/bin/server"), model_path=Path("/c/m.gguf"), alias="a", tag="t", slots=3,
                          ctx_per_slot=4096, seed=11, threads=2, threads_batch=4, cache_ram_mib=1024,
                          extra_args=("--jinja", "--reasoning", "off"))
        self.assertEqual(server_argv(spec, 8123), [
            "/x/bin/server", "-m", "/c/m.gguf", "--alias", "a", "--host", "127.0.0.1", "--port", "8123", "-t", "2",
            "-tb", "4", "-np", "3", "-c", "12288", "--seed", "11", "--cache-ram", "1024", "--fit", "off",
            "--offline", "--no-webui", "--metrics", "--jinja", "--reasoning", "off"])

    def test_thread_counts_physical_logical_unknown(self) -> None:
        host = {"nproc_available": 4, "cpu": {"physical_cores": 2}}
        self.assertEqual(thread_counts(host, "physical"), (2, 4))
        self.assertEqual(thread_counts(host, "logical"), (4, 4))
        self.assertEqual(thread_counts({"nproc_available": 4, "cpu": {"physical_cores": 8}}, "physical"), (4, 4))
        self.assertEqual(thread_counts({"nproc_available": 4, "cpu": None}, "physical"), (4, 4))
        with mock.patch.object(os, "cpu_count", return_value=6):
            self.assertEqual(thread_counts({"nproc_available": None, "cpu": None}, "physical"), (6, 6))
        with mock.patch.object(os, "cpu_count", return_value=None):
            self.assertEqual(thread_counts({}, "logical"), (1, 1))

    def test_server_env_exactly_four(self) -> None:
        env = server_env({"PATH": "/p", "GH_TOKEN": "t", "HOME": "/root", "LD_PRELOAD": "x"}, "/o/home", "/o/tmp")
        self.assertEqual(env, {"PATH": "/p", "HOME": "/o/home", "LANG": "C.UTF-8", "TMPDIR": "/o/tmp"})
        self.assertEqual(server_env({}, "/h", "/t")["PATH"], "/usr/bin:/bin")

    def test_environment_allowlist_with_sentinels(self) -> None:
        program = _json(MANIFEST_TEST)["server"]["program"]
        sentinels = {"GH_TOKEN": "Qz51Gh", "GITHUB_TOKEN": "Qz52Github", "MYCELIC_LAB_HOSTED_API_KEY": "Qz53Hosted",
                     f"{program.split('-')[0].upper()}_ARG_CTX_SIZE": "Qz54Ctx", "LD_PRELOAD": "Qz55Preload"}
        world = StubWorld(self.tmp / "w", request=gguf_request(g0=False))
        self.addCleanup(world.close)
        with mock.patch.dict(os.environ, sentinels):
            world.provision_all()
            out = self.tmp / "OUT"
            self.assertEqual(world.prepare(out)[0], 0)
            code, _, stderr = world.run(out)
        self.assertEqual(code, 0, stderr)
        argvs, environs = world.stub_files("argv"), world.stub_files("environ")
        self.assertEqual([a[0] if a else None for a in argvs].count("--version"), 2)
        self.assertEqual(len(environs), 3)
        for argv, env in zip(argvs, environs):
            with self.subTest(argv=argv[:1]):
                self.assertEqual(sorted(env), ["HOME", "LANG", "PATH", "TMPDIR"])
                for name, value in sentinels.items():
                    self.assertNotIn(name, env)
                    self.assertNotIn(value, json.dumps(env))
        for env in environs[1:]:
            self.assertTrue(env["HOME"].startswith(str(out)) and env["TMPDIR"].startswith(str(out)))


# --------------------------------------------------------------------------------------------------- start

class StartTests(TempDirTest):
    def test_health_sequence_503_then_200(self) -> None:
        instance = self.stub_server({"health_503": 2})
        record = instance.start()
        self.assertEqual(record["health_503"], 2)
        self.assertEqual(instance.health(5), 200)
        self.assertEqual((record["port"], record["port_attempts"], record["pid"]), (instance.port, 1, instance.pid))
        self.assertEqual(record["props"], {"n_ctx": 16384, "total_slots": 1, "model_path": str(self.tmp / "models" /
                                                                                              "model.gguf"),
                                           "build_info": f"{TAG}-stub", "model_alias": "stub-alias"})
        self.assertEqual(record["models_listed"], ["stub-alias"])
        self.assertEqual(record["argv"][0], str(self.tmp / "bin" / "server-stub"))
        self.assertEqual(instance.base_url, f"http://127.0.0.1:{instance.port}/v1")
        self.assertEqual(instance.host_label, f"127.0.0.1:{instance.port}")
        self.assertIsNone(instance.poll())

    def _fails(self, problem: str, config: dict[str, Any], within: float = 10, **spec: Any) -> ServerError:
        instance = self.stub_server(config, **spec)
        started = time.monotonic()
        with self.assertRaises(ServerError) as caught:
            instance.start()
        self.assertLess(time.monotonic() - started, within)
        self.assertEqual(caught.exception.problem, problem)
        self.assertEqual(instance.record["problem"], problem)
        self.assertIsNotNone(instance.record["exit"])
        self.assertTrue(wait_until(lambda: not pids_mentioning(str(self.tmp / "bin")), 5))
        return caught.exception

    def test_never_healthy_fails_fast(self) -> None:
        err = self._fails(SERVER_NOT_HEALTHY, {"never_healthy": True}, within=5, health_deadline_s=3)
        self.assertIn("listening", str(err))
        self.assertGreater(self.records("argv") and len(self.records("argv")), 0)

    def test_crash_on_start_log_tail(self) -> None:
        err = self._fails(f"{SERVER_START_FAILED}: exit code 3", {"crash_on_start": True}, within=5)
        self.assertIn("failed to load the model file", str(err))
        self.assertEqual(str(err), f"{err.problem}\n{err.detail}")
        self.assertNotIn("failed to load", err.problem)

    def test_unsupported_flag(self) -> None:
        err = self._fails(f"{SERVER_START_FAILED}: exit code 1", {"reject_flag": "--metrics"}, within=5)
        self.assertIn("error: invalid argument: --metrics", str(err))
        self.assertIn("invalid argument", _tail_of(self.tmp))

    def test_wrong_alias(self) -> None:
        self._fails(ALIAS_MISMATCH, {"wrong_alias": True})

    def test_wrong_n_ctx(self) -> None:
        err = self._fails(f"{SERVER_SETTINGS}: n_ctx", {"wrong_n_ctx": 4096})
        self.assertNotIn("4096", err.problem)

    def test_wrong_slots(self) -> None:
        self._fails(f"{SERVER_SETTINGS}: total_slots", {"wrong_slots": 4})

    def test_wrong_model_path(self) -> None:
        self._fails(f"{SERVER_SETTINGS}: model_path", {"wrong_model_path": "/elsewhere/model.gguf"})

    def test_bind_fail_once_retries(self) -> None:
        instance = self.stub_server({"bind_fail": 1})
        record = instance.start()
        self.assertEqual(record["port_attempts"], 2)
        argvs = self.records("argv")
        self.assertEqual(len(argvs), 2)
        self.assertNotEqual(argvs[0][argvs[0].index("--port") + 1], argvs[1][argvs[1].index("--port") + 1])

    def test_bind_fail_always_gives_up_after_three(self) -> None:
        err = self._fails(f"{SERVER_START_FAILED}: exit code 1", {"bind_fail": 9})
        self.assertIn("couldn't bind HTTP server socket", str(err))
        self.assertEqual(len(self.records("argv")), server.PORT_ATTEMPTS)

    def test_poll_waits_only_when_asked(self) -> None:
        instance = self.stub_server()
        instance.start()
        started = time.monotonic()
        self.assertIsNone(instance.poll(0.3))
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        os.kill(instance.pid, signal.SIGKILL)
        self.assertEqual(instance.poll(5), {"code": None, "signal": "SIGKILL"})
        self.assertEqual(instance.stop(), {"code": None, "signal": "SIGKILL", "planned": False})

    def test_stop_leaves_no_process(self) -> None:
        instance = self.stub_server()
        instance.start()
        pid = instance.pid
        self.assertEqual(instance.stop(), {"code": None, "signal": "SIGTERM", "planned": True})
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pid, 0)
        self.assertEqual(instance.stop(), {"code": None, "signal": "SIGTERM", "planned": True})
        self.assertIsNotNone(instance.record["stopped_at"])
        self.assertEqual(pids_mentioning(str(self.tmp)), [])
        log = self.tmp / "OUT" / "server" / "server-k-0.log"
        log.write_bytes(b"x" * (server.SERVER_LOG_CAP + 10))
        instance._exit = None
        instance.stop()
        self.assertEqual(log.stat().st_size, server.SERVER_LOG_CAP)

    def test_build_info_and_backend_lines_recorded(self) -> None:
        record = self.stub_server().start()
        self.assertTrue(record["build_info_matches_tag"])
        self.assertEqual(record["backend_lines"], ["load_backend: loaded CPU backend from libstub-base.so"])
        self.assertTrue(record["system_info"].startswith("system_info: n_threads = 2"))
        other = self.stub_server(tag="b77")
        self.assertFalse(other.start()["build_info_matches_tag"])

    def test_missing_binary(self) -> None:
        instance = self.stub_server()
        instance.spec = dataclasses.replace(instance.spec, binary=self.tmp / "absent")
        with self.assertRaises(ServerError) as caught:
            instance.start()
        self.assertEqual(caught.exception.problem, f"{SERVER_START_FAILED}: FileNotFoundError")


def _tail_of(tmp: Path) -> str:
    return (tmp / "OUT" / "server" / "server-k-0.log").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------------------- warm-up

class WarmupTests(TempDirTest):
    def setUp(self) -> None:
        super().setUp()
        self.plan, _ = make_plan(self.tmp / "p", gguf_request())
        self.entry = self.plan["models"][MODEL_KEY]
        self.units = {u["experiment"]: u for u in self.plan["units"]}

    def _warm(self, config: dict[str, Any] | None = None, *, experiments: tuple[str, ...] = ("g0", "e3"),
              ctx: int = 16384, meminfo: Any = lambda: 8 << 30, entry: dict[str, Any] | None = None,
              budget_s: float | None = None) -> tuple[ModelServer, dict[str, Any], dict[str, str], str | None]:
        instance = self.stub_server({"pack": "device_quality", **(config or {})}, slots=2, ctx_per_slot=ctx)
        instance.start()
        tasks = warm_tasks([self.units[e] for e in experiments], self.plan)
        record, skips, stop_all = warm_up(instance, entry or self.entry, tasks, ctx_per_slot=ctx,
                                          out=self.tmp / "OUT", start_index=0, meminfo=meminfo, budget_s=budget_s)
        return instance, record, skips, stop_all

    def test_ledger_outside_run_dirs(self) -> None:
        _, record, skips, stop_all = self._warm()
        self.assertEqual((skips, stop_all), ({}, None))
        ledger = self.tmp / "OUT" / "server" / "warmup.ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual(sorted(r["ref"] for r in rows), ["warmup:e3_extraction_like", "warmup:e3_short_answer",
                                                         "warmup:extract_claims", "warmup:judge_record"])
        self.assertTrue(all(r["run_id"] == "warmup-0" for r in rows))
        self.assertFalse(record["unanswered"])
        self.assertEqual(record["ledger"], "server/warmup.ledger.jsonl")
        self.assertFalse((self.tmp / "OUT" / "runs").exists())
        streamed = {row["task"]: row["stream_usage"] for row in record["tasks"]}
        self.assertEqual(streamed, {"e3_extraction_like": True, "e3_short_answer": True, "extract_claims": None,
                                    "judge_record": None})
        for row in record["tasks"]:
            self.assertEqual((row["http_status"], row["ok"], row["finish_reason"], row["fits"]), (200, True, "stop",
                                                                                                 True))
            self.assertGreater(row["worst_prompt_tokens"], 0)
            self.assertGreaterEqual(row["worst_prompt_tokens"], row["prompt_tokens_counted"])

    def test_finish_length_skips_dependents_only(self) -> None:
        _, record, skips, stop_all = self._warm({"finish_length": [TASK_NAME]})
        g0 = self.units["g0"]["unit"]
        self.assertEqual(skips, {g0: f"{WARMUP_LENGTH.format(task=TASK_NAME)}; {REMEDY_REASONING}"})
        self.assertIsNone(stop_all)

    def test_reasoning_content_skips_start(self) -> None:
        instance, record, skips, stop_all = self._warm({"reasoning_content": True})
        self.assertEqual(stop_all, f"{WARMUP_REASONING}; {REMEDY_REASONING}")
        self.assertTrue(record["probe"]["reasoning_content"])

    def test_http_error_skips_dependents(self) -> None:
        _, record, skips, stop_all = self._warm({"http_error": {"judge_record": 500}})
        g0 = self.units["g0"]["unit"]
        self.assertEqual(skips, {g0: f"{WARMUP_HTTP.format(task='judge_record', detail='HTTP 500')}; {REMEDY_HTTP}"})
        self.assertIsNone(stop_all)

    def test_context_too_small_worst_case(self) -> None:
        _, record, skips, stop_all = self._warm(experiments=("g0",), ctx=2048)
        g0 = self.units["g0"]["unit"]
        row = next(r for r in record["tasks"] if r["task"] == TASK_NAME)
        self.assertFalse(row["fits"])
        reason = CONTEXT_TOO_SMALL.format(task=TASK_NAME, needed=row["worst_prompt_tokens"] + row["max_tokens"],
                                          prompt=row["worst_prompt_tokens"], max_tokens=row["max_tokens"], slot=2048)
        self.assertEqual(skips[g0], f"{reason}; {REMEDY_CONTEXT.format(field='ctx_per_slot')}")
        self.assertTrue(skips[g0].startswith("context too small"))
        self.assertIsNone(row["http_status"])

    def test_context_too_small_spares_the_e3_class(self) -> None:
        world = StubWorld(self.tmp / "w", server_config={"pack": "device_quality"}, model={"ctx_per_slot": 2048})
        self.addCleanup(world.close)
        world.provision_all()
        out = self.tmp / "OUT2"
        self.assertEqual(world.prepare(out)[0], 0)
        code, stdout, stderr = world.run(out)
        self.assertEqual(code, 1, stdout + stderr)
        records = _units(out)
        g0, e3 = records["g0-tiny-gguf"], records["e3-tiny-gguf"]
        self.assertEqual(g0["status"], "skipped")
        self.assertTrue(g0["status_reason"].startswith("context too small"))
        self.assertTrue(g0["status_reason"].endswith(REMEDY_CONTEXT.format(field="ctx_per_slot")))
        self.assertEqual(e3["status"], "ok", e3["status_reason"])

    def test_memory_after_load_stops_the_server_in_a_shard(self) -> None:
        world = StubWorld(self.tmp / "w", server_config={"pack": "device_quality"}, request=gguf_request(e3=False))
        self.addCleanup(world.close)
        world.provision_all()
        out = self.tmp / "OUT3"
        self.assertEqual(world.prepare(out)[0], 0)
        with mock.patch.object(hostinfo, "mem_available", return_value=1 << 30):
            code, stdout, stderr = world.run(out)
        self.assertEqual(code, 1, stdout + stderr)
        record = _units(out)["g0-tiny-gguf"]
        self.assertEqual(record["status"], "skipped")
        self.assertTrue(record["status_reason"].startswith("memory: 1024 MiB available"), record["status_reason"])
        start = _json(out / "provenance.json")["server"]["starts"][0]
        self.assertEqual((start["problem"], start["exit"]["planned"]), (record["status_reason"], True))
        self.assertEqual(start["mem_available_after_load"], 1 << 30)

    def test_budget_bounds_the_warmup(self) -> None:
        # nothing left: no request at all
        _, record, skips, stop_all = self._warm(budget_s=0)
        self.assertEqual((stop_all, skips, record["tasks"], record["probe"]), (BUDGET_EXHAUSTED, {}, [], None))
        self.assertEqual(self.records("chat"), [])
        # a server slower than the budget: the call's deadline is what is left, and its timeout is the budget's,
        # not a context or schema finding; without the budget this warm-up takes three ten-second chats
        started = time.monotonic()
        _, record, skips, stop_all = self._warm({"chat_delay_s": 10}, experiments=("e3",), budget_s=6)
        self.assertLess(time.monotonic() - started, 9.5)
        self.assertEqual((stop_all, skips, record["probe"]), (BUDGET_EXHAUSTED, {}, None))
        called = [row for row in record["tasks"] if row["error_kind"] is not None]
        self.assertTrue(all(row["error_kind"] == "timeout" for row in called), called)
        self.assertLessEqual(len(called), 1)

    def test_repeat_identical_true_and_false(self) -> None:
        self.assertTrue(self._warm()[1]["repeat_identical"])
        self.assertFalse(self._warm({"nondeterministic": True})[1]["repeat_identical"])

    def test_probe_body_matches_runtime_request(self) -> None:
        _, record, _, _ = self._warm(experiments=("g0",))
        chats = self.records("chat")
        runtime_first, probes = chats[0], chats[-2:]
        self.assertEqual(record["probe"]["task"], TASK_NAME)
        self.assertEqual(runtime_first["response_format"]["json_schema"]["name"], TASK_NAME)
        for probe in probes:
            for key in ("messages", "response_format", "max_tokens", "temperature", "model"):
                self.assertEqual(probe[key], runtime_first[key], key)
            self.assertNotIn("stream", probe)
        self.assertEqual(record["probe"]["http_status"], [200, 200])

    def test_response_format_mirrors_runtime(self) -> None:
        pack = load_pack("device_quality")
        tasks = warm_tasks([self.units["g0"], self.units["e3"]], self.plan)
        for fmt in ("json_schema", "json_object", "none"):
            for transport in ("full", "reduced"):
                entry = {"response_format": fmt, "transport_schema": transport}
                config = parse_routing({"schema_version": 1, "endpoints": {"lab": {
                    "provider": "openai_compat", "boundary": "central", "base_url": "http://127.0.0.1:1/v1",
                    "model": "m", "response_format": fmt, "transport_schema": transport}}, "routes": {}},
                    check_env=False)
                for t in tasks:
                    compiled = compile_schema(t.schema)
                    with self.subTest(fmt=fmt, transport=transport, task=t.task.name):
                        plan = types.SimpleNamespace(schema=compiled, task=t.task)
                        self.assertEqual(response_format(entry, compiled, t.task),
                                         Runtime._response_format(None, plan, config.endpoints["lab"]))
        self.assertTrue(pack.id)

    def test_memory_after_load_stops_and_skips(self) -> None:
        _, record, skips, stop_all = self._warm(meminfo=lambda: 1 << 30)
        self.assertEqual(stop_all, "memory: 1024 MiB available after the model loaded, 1536 MiB needed")
        self.assertEqual(record["mem_available_after_load"], 1 << 30)
        self.assertIsNone(self._warm(meminfo=lambda: None)[3])

    def test_warm_tasks_with_sim(self) -> None:
        plan, _ = make_plan(self.tmp / "s", gguf_request(sim=True))
        by_experiment = {u["experiment"]: u for u in plan["units"]}
        sim = by_experiment["sim"]
        self.assertEqual(sim["unit"], f"sim-{MODEL_KEY}-s1")
        tasks = warm_tasks([by_experiment["g0"], sim, by_experiment["e3"]], plan)
        self.assertEqual([(t.task.name, t.pack) for t in tasks],
                         [("e3_extraction_like", ""), ("e3_short_answer", ""), ("extract_claims", "device_quality"),
                          ("judge_record", "device_quality")])
        for t in tasks[2:]:
            self.assertEqual(t.units, (by_experiment["g0"]["unit"], sim["unit"]))
        self.assertEqual(lab_shard.serving_class(sim), lab_shard.serving_class(by_experiment["g0"]))
        alone = warm_tasks([sim], plan)
        self.assertEqual([(t.task.name, t.pack, t.units) for t in alone],
                         [("extract_claims", "device_quality", (sim["unit"],)),
                          ("judge_record", "device_quality", (sim["unit"],))])

    def test_warm_tasks_payloads_deterministic(self) -> None:
        first = warm_tasks([self.units["g0"], self.units["e3"]], self.plan)
        second = warm_tasks([self.units["e3"], self.units["g0"]], self.plan)
        self.assertEqual(first, second)
        self.assertEqual([(t.task.name, t.pack) for t in first],
                         [("e3_extraction_like", ""), ("e3_short_answer", ""), ("extract_claims", "device_quality"),
                          ("judge_record", "device_quality")])
        by_name = {t.task.name: t for t in first}
        pack = load_pack("device_quality")
        extract, judge = by_name["extract_claims"], by_name["judge_record"]
        self.assertEqual(extract.units, (self.units["g0"]["unit"],))
        self.assertFalse(extract.stream or judge.stream)
        self.assertTrue(by_name["e3_short_answer"].stream)
        self.assertEqual(extract.payload["language"], extract.worst["language"])
        self.assertLessEqual(len(extract.worst["text"]), pack.extraction.max_input_chars)
        self.assertGreater(len(extract.worst["text"]), pack.extraction.max_input_chars - 200)
        self.assertLess(len(extract.payload["text"]), len(extract.worst["text"]))
        self.assertEqual(judge.payload["question"]["predicate"], sorted(pack.predicates)[0])
        self.assertEqual(judge.payload["record"]["text"], extract.payload["text"])
        self.assertEqual(judge.worst["record"]["text"], extract.worst["text"])
        seed = self.units["e3"]["params"]["seed"]
        self.assertEqual(by_name["e3_short_answer"].payload,
                         {"text": filler(WORKLOADS["short"][2], f"warmup:{seed}:short")})
        self.assertEqual(by_name["e3_short_answer"].payload, by_name["e3_short_answer"].worst)


# --------------------------------------------------------------------------------------------------- watchdog

def _three_e3_units(world: StubWorld) -> None:
    """Rewrite the world's plan to one shard of three E3 units on the gguf model (before provisioning)."""
    plan = world.plan
    base = next(u for u in plan["units"] if u["experiment"] == "e3")
    units_ = [base] + [{**base, "unit": f"{base['unit']}-r{i}",
                        "run_id": run_id(f"{base['unit']}-r{i}", plan["request"]["sha256"])} for i in (2, 3)]
    for unit in units_:
        unit["shard"] = plan["shards"][0]["shard"]
    shard = plan["shards"][0]
    shard.update(units=[u["unit"] for u in units_], planned_minutes=3 * base["minutes"],
                 timeout_minutes=3 * base["minutes"] + plan["shard_overhead_minutes"])
    plan["units"] = units_
    world.plan_path = write_json(world.tmp / "p" / "plan-three.json", plan)


def _unreaped(real: Any) -> Any:
    """``ModelServer.poll`` as it looks in the moment after a kill: the sockets are closed but the process cannot be
    reaped yet, so only a call that waits sees the exit."""
    def poll(self: ModelServer, wait_s: float = 0.0) -> dict[str, Any] | None:
        return real(self, wait_s) if wait_s > 0 else None
    return poll


class WatchdogTests(WorldTest):
    def _run(self, config: dict[str, Any], patch_health: bool = False,
             unreaped: bool = False) -> tuple[int, dict[str, Any], dict[str, Any]]:
        world = self.world(server_config={"pack": "device_quality", **config}, request=gguf_request(g0=False))
        _three_e3_units(world)
        out = self.prepared(world)
        with mock.patch.object(server, "AFTER_UNIT_HEALTH_S", 1 if patch_health else server.AFTER_UNIT_HEALTH_S), \
                mock.patch.object(ModelServer, "poll", _unreaped(ModelServer.poll) if unreaped else ModelServer.poll):
            code, stdout, stderr = world.run(out)
        self.out, self.stdout = out, stdout
        return code, _units(out), _json(out / "provenance.json")

    def test_crash_mid_unit_invalid_then_restart_ok(self) -> None:
        code, records, provenance = self._run({"exit_after_chats": 6, "exit_times": 1})
        self.assertEqual(code, 1, self.stdout)
        first, second, third = (records[u] for u in provenance["planned_units"])
        self.assertEqual(first["status"], "invalid")
        self.assertEqual(first["status_reason"], f"{SERVER_EXITED} SIGKILL; {OOM_HINT}")
        self.assertEqual(first["server_exit"], {"code": None, "signal": "SIGKILL"})
        self.assertEqual((second["status"], third["status"]), ("ok", "ok"), second["status_reason"])
        starts = provenance["server"]["starts"]
        self.assertEqual([(s["restart"], s["class"]) for s in starts], [(False, "e3"), (True, "e3")])
        self.assertEqual(starts[0]["exit"]["planned"], False)
        self.assertEqual(provenance["server"]["restarts"], 1)
        self.assertEqual((second["serving"]["start"], third["serving"]["start"]), (1, 1))

    def test_crash_mid_unit_seen_before_reaping(self) -> None:
        # the watch never sees the exit; the failing unit still names it, after a short wait for the process
        code, records, provenance = self._run({"exit_after_chats": 6, "exit_times": 1}, patch_health=True,
                                              unreaped=True)
        self.assertEqual(code, 1, self.stdout)
        first, second, third = (records[u] for u in provenance["planned_units"])
        self.assertEqual((first["status"], first["status_reason"]), ("invalid", f"{SERVER_EXITED} SIGKILL; {OOM_HINT}"))
        self.assertEqual(first["server_exit"], {"code": None, "signal": "SIGKILL"})
        self.assertEqual((second["status"], third["status"]), ("ok", "ok"), second["status_reason"])
        self.assertEqual(provenance["server"]["restarts"], 1)

    def test_second_crash_skips_rest(self) -> None:
        code, records, provenance = self._run({"exit_after_chats": 6, "exit_times": 2})
        self.assertEqual(code, 1)
        first, second, third = (records[u] for u in provenance["planned_units"])
        self.assertEqual((first["status"], second["status"]), ("invalid", "invalid"))
        self.assertIn("SIGKILL", second["status_reason"])
        self.assertEqual((third["status"], third["status_reason"]), ("skipped", SERVER_UNAVAILABLE))
        self.assertEqual(provenance["server"]["restarts"], 1)
        self.assertEqual(len(provenance["server"]["starts"]), 2)

    def test_unhealthy_after_unit_counts_as_exit(self) -> None:
        code, records, provenance = self._run({"unhealthy_after_chats": 6}, patch_health=True)
        self.assertEqual(code, 1)
        first, second, third = (records[u] for u in provenance["planned_units"])
        self.assertEqual((first["status"], second["status"]), ("ok", "ok"))
        self.assertEqual(first["server_after"], {"health": 503, "note": SERVER_UNHEALTHY_AFTER})
        self.assertEqual((third["status"], third["status_reason"]), ("skipped", SERVER_UNAVAILABLE))
        starts = provenance["server"]["starts"]
        self.assertEqual([s["restart"] for s in starts], [False, True])
        self.assertEqual(starts[0]["exit"]["planned"], True)

    def test_crash_during_warmup_fails_the_class(self) -> None:
        # the stub answers the first chat (extract_claims) and SIGKILLs itself at the second (judge_record)
        for exit_times in (1, 2):
            with self.subTest(exit_times=exit_times):
                world = self.world(server_config={"pack": "device_quality", "exit_after_chats": 1,
                                                  "exit_times": exit_times})
                out = self.prepared(world)
                code, stdout, stderr = world.run(out)
                self.assertEqual(code, 1, stdout + stderr)
                records, provenance = _units(out), _json(out / "provenance.json")
                g0, e3 = records["g0-tiny-gguf"], records["e3-tiny-gguf"]
                reason = f"{SERVER_EXITED} SIGKILL; {OOM_HINT}"
                self.assertEqual((g0["status"], g0["status_reason"]), ("skipped", reason))
                starts = provenance["server"]["starts"]
                self.assertEqual((starts[0]["class"], starts[0]["problem"], starts[0]["exit"]),
                                 ("quality", reason, {"code": None, "signal": "SIGKILL", "planned": False}))
                self.assertEqual(starts[0]["warmup"]["tasks"][1]["error_kind"], "network")
                self.assertTrue(starts[0]["warmup"]["unanswered"])
                self.assertEqual([(s["class"], s["restart"]) for s in starts], [("quality", False), ("e3", True)])
                self.assertEqual(provenance["server"]["restarts"], 1)
                if exit_times == 1:
                    self.assertEqual(e3["status"], "ok", e3["status_reason"])
                    self.assertIsNone(starts[1]["problem"])
                else:                        # a second exit, in the e3 class's warm-up, ends the shard's serving
                    self.assertEqual((e3["status"], e3["status_reason"]), ("skipped", reason))
                    self.assertEqual(starts[1]["problem"], reason)

    def test_crash_during_warmup_seen_before_reaping(self) -> None:
        world = self.world(server_config={"pack": "device_quality", "exit_after_chats": 1, "exit_times": 1})
        out = self.prepared(world)
        with mock.patch.object(ModelServer, "poll", _unreaped(ModelServer.poll)):
            code, stdout, stderr = world.run(out)
        self.assertEqual(code, 1, stdout + stderr)
        g0 = _units(out)["g0-tiny-gguf"]
        self.assertEqual((g0["status"], g0["status_reason"]), ("skipped", f"{SERVER_EXITED} SIGKILL; {OOM_HINT}"))
        self.assertEqual(_json(out / "provenance.json")["server"]["restarts"], 1)

    def test_run_process_watch_stops_group(self) -> None:
        script = ("import subprocess, sys, time; "
                  "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")
        flag = time.monotonic() + 1
        started = time.monotonic()
        result = units.run_process([sys.executable, "-c", script], {"PATH": os.environ["PATH"]}, cwd=self.tmp,
                                   timeout_s=60, stdout_path=self.tmp / "o.log", stderr_path=self.tmp / "e.log",
                                   watch=lambda: time.monotonic() > flag)
        self.assertTrue(result.stopped_by_watch)
        self.assertFalse(result.timed_out)
        self.assertLess(time.monotonic() - started, 15)

        def gone() -> bool:
            try:
                os.killpg(result.pgid, 0)
            except ProcessLookupError:
                return True
            return False

        self.assertTrue(wait_until(gone, 5))
        quick = units.run_process([sys.executable, "-c", "pass"], {"PATH": os.environ["PATH"]}, cwd=self.tmp,
                                  timeout_s=30, stdout_path=self.tmp / "o2.log", stderr_path=self.tmp / "e2.log",
                                  watch=lambda: True)
        self.assertEqual((quick.exit_code, quick.stopped_by_watch), (0, False))


# --------------------------------------------------------------------------------------------------- whole shards

class ShardModelTests(WorldTest):
    def test_two_classes_recompute_np_and_ctx_plumbing_with_fake_header(self) -> None:
        world = self.world(server_config={"pack": "device_quality"}, server={"args": ["--no-jinja"]},
                           model={"server_args": ["--reasoning", "off"]})
        manifest = _json(world.manifest_path)
        out = self.prepared(world)
        code, stdout, stderr = world.run(out)
        self.assertEqual(code, 0, stdout + stderr)
        records = _units(out)
        for record in records.values():
            with self.subTest(unit=record["unit"]):
                self.assertEqual((record["status"], record["measurement_class"], record["class_reason"]),
                                 ("ok", "plumbing", "fake_marker"))
                self.assertIn("plumbing", record["notes"])
                self.assertGreater(record["fake_rows"], 0)
        argvs = self.server_argvs(world)
        self.assertEqual(len(argvs), 2)
        entry = world.plan["models"][MODEL_KEY]
        self.assertEqual(manifest["models"][MODEL_KEY]["server_args"], ["--reasoning", "off"])
        e3 = next(u for u in world.plan["units"] if u["experiment"] == "e3")
        concurrency = max(e3["params"]["concurrency"])
        for argv, (np_, ctx) in zip(argvs, ((1, entry["ctx_per_slot"]),
                                            (concurrency, concurrency * entry["e3_ctx_per_slot"]))):
            with self.subTest(np=np_):
                self.assertEqual((argv[argv.index("-np") + 1], argv[argv.index("-c") + 1]), (str(np_), str(ctx)))
                for flag, value in (("--cache-ram", "1024"), ("--fit", "off"), ("--host", "127.0.0.1"),
                                    ("-tb", str(hostinfo.nproc_available()))):
                    self.assertEqual(argv[argv.index(flag) + 1], value)
                for flag in ("--offline", "--no-webui", "--metrics"):
                    self.assertIn(flag, argv)
                self.assertEqual(argv[-3:], ["--no-jinja", "--reasoning", "off"])
                self.assertEqual(argv.index("--metrics"), len(argv) - 4)
                self.assertEqual(argv[argv.index("-m") + 1], _json(out / "provision" / "prepare.json")["model"]["path"])

    def test_no_fake_header_is_unverified_download_hosts(self) -> None:
        world = self.world(server_config={"pack": "device_quality", "no_fake_header": True})
        out = self.prepared(world)
        code, stdout, stderr = world.run(out)
        self.assertEqual(code, 0, stdout + stderr)
        for record in _units(out).values():
            with self.subTest(unit=record["unit"]):
                self.assertEqual((record["status"], record["measurement_class"], record["class_reason"]),
                                 ("ok", "unverified", "download_hosts"))
                checks = record["class_checks"]
                self.assertEqual([k for k, v in checks.items() if not v], ["download_hosts"])
                self.assertEqual(record["fake_rows"], 0)
                self.assertNotIn("model_measurement", record["notes"])
                self.assertEqual(record["serving"]["alias"], "tiny-test")
        self.assertIn("unverified", stdout)
        for path in out.glob("runs/**/*ledger.jsonl"):
            self.assertNotIn("warmup:", path.read_text())

    def test_run_without_prepare_exits_2(self) -> None:
        world = self.world()
        code, stdout, stderr = world.run(self.tmp / "unprepared")
        self.assertEqual(code, 2)
        self.assertEqual(stderr.strip(), f"error: {NOT_PREPARED}")
        self.assertFalse((self.tmp / "unprepared").exists())

    def test_sigterm_during_health_wait(self) -> None:
        world = self.world(server_config={"health_delay_s": 120})
        out = self.prepared(world)
        proc = subprocess.Popen([sys.executable, "-m", "lab.shard", "run", "--plan", str(world.plan_path), "--shard",
                                 "s001-tiny-gguf", "--out", str(out)], cwd=ROOT, env=lab_env(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertTrue(wait_until(lambda: len(pids_mentioning(str(out / "server" / "bin"))) > 0, 60))
        self.assertTrue(wait_until(lambda: (world.record_dir / "argv-1.json").exists(), 30))
        time.sleep(1.0)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 1, stdout + stderr)
        records = _units(out)
        first, second = records["g0-tiny-gguf"], records["e3-tiny-gguf"]
        self.assertEqual((first["status"], first["status_reason"]), ("interrupted", SHARD_INTERRUPTED))
        self.assertEqual((second["status"], second["status_reason"]), ("skipped", SHARD_INTERRUPTED))
        self.assertTrue(wait_until(lambda: not pids_mentioning(str(out)), 10), pids_mentioning(str(out)))
        provenance = _json(out / "provenance.json")
        self.assertEqual((provenance["interrupted"], provenance["exit_code"]), (True, 1))
        self.assertEqual(provenance["server"]["starts"][0]["exit"]["planned"], True)

    def test_sigterm_during_warmup(self) -> None:
        world = self.world(server_config={"pack": "device_quality", "chat_delay_s": 120})
        out = self.prepared(world)
        proc = subprocess.Popen([sys.executable, "-m", "lab.shard", "run", "--plan", str(world.plan_path), "--shard",
                                 "s001-tiny-gguf", "--out", str(out)], cwd=ROOT, env=lab_env(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertTrue(wait_until(lambda: (world.record_dir / "chat-0.json").exists(), 60))
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 1, stdout + stderr)
        records = _units(out)
        self.assertEqual((records["g0-tiny-gguf"]["status"], records["e3-tiny-gguf"]["status"]),
                         ("interrupted", "skipped"))
        self.assertTrue(wait_until(lambda: not pids_mentioning(str(out)), 10), pids_mentioning(str(out)))
        start = _json(out / "provenance.json")["server"]["starts"][0]
        self.assertEqual((start["warmup"], start["exit"]["planned"]), (None, True))

    def test_warmup_within_the_shard_budget(self) -> None:
        # the start gets its health check (at least a minute) but the warm-up only what is left after the margin and
        # one unit's minimum; a server that takes half a minute per chat stops it with the budget as the reason
        world = self.world(server_config={"pack": "device_quality", "chat_delay_s": 30}, request=gguf_request(e3=False))
        out = self.prepared(world)
        started = time.monotonic()
        deadline = time.time() + lab_shard.DEADLINE_MARGIN_S + lab_shard.MIN_UNIT_SECONDS + 15
        code, stdout, stderr = world.run(out, None, "--deadline-epoch", str(deadline))
        self.assertLess(time.monotonic() - started, 28, stdout + stderr)
        self.assertEqual(code, 1, stdout + stderr)
        record = _units(out)["g0-tiny-gguf"]
        self.assertEqual((record["status"], record["status_reason"]), ("skipped", BUDGET_EXHAUSTED))
        start = _json(out / "provenance.json")["server"]["starts"][0]
        self.assertEqual((start["problem"], start["warmup"]["stop_all"]), (BUDGET_EXHAUSTED, BUDGET_EXHAUSTED))

    def test_seal_removes_server_bin_home_tmp(self) -> None:
        world = self.world(server_config={"pack": "device_quality"}, request=gguf_request(g0=False))
        out = self.prepared(world)
        self.assertEqual(world.run(out)[0], 0)
        for name in ("bin", "home", "tmp"):
            self.assertTrue((out / "server" / name).is_dir(), name)
        code, _, _ = lab_shard_call(["seal", "--out", str(out), "--shard", "s001-tiny-gguf", "--step-outcome",
                                     "prepare=success", "--step-outcome", "run=success"])
        self.assertEqual(code, 0)
        for name in ("bin", "home", "tmp"):
            self.assertFalse((out / "server" / name).exists(), name)
        status = _json(out / "status.json")
        self.assertIn("server/server-tiny-gguf-0.log", status["files"])
        self.assertIn("server/warmup.ledger.jsonl", status["files"])
        self.assertIn("provision/prepare.json", status["files"])
        self.assertFalse(any(rel.startswith(("server/bin", "server/home", "server/tmp")) for rel in status["files"]))

    def test_provenance_keys_and_server_block(self) -> None:
        world = self.world(server_config={"pack": "device_quality", "no_fake_header": True})
        out = self.prepared(world)
        self.assertEqual(world.run(out)[0], 0)
        provenance = _json(out / "provenance.json")
        self.assertEqual(tuple(sorted(provenance)), tuple(sorted(lab_shard.PROVENANCE_KEYS)))
        self.assertEqual((provenance["result_class"], provenance["banner"], provenance["provider"]),
                         ("real", None, world.manifest["server"]["program"]))
        block = provenance["server"]
        sha = hashlib.sha256(world.tarball).hexdigest()
        self.assertEqual((block["kind"], block["tag"], block["asset"], block["sha256"], block["verified_by"],
                          block["first_use"], block["version"], block["restarts"]),
                         ("model", world.tag, world.asset, sha, "github-api", False, "version: 0 (stub)", 0))
        self.assertEqual([s["class"] for s in block["starts"]], ["quality", "e3"])
        binary = _json(out / "provision" / "prepare.json")["server"]["binary"]
        for start in block["starts"]:
            self.assertEqual(start["argv"][0], binary)
            self.assertEqual(start["exit"]["planned"], True)
            self.assertIsNone(start["problem"])
            self.assertIsNone(start["log_tail"])
            self.assertTrue(start["warmup"]["tasks"])
        model = provenance["model"]
        self.assertEqual((model["key"], model["commit"], model["sha256"], model["size"], model["verified_by"],
                          model["license"]),
                         (MODEL_KEY, world.commit, hashlib.sha256(world.blob).hexdigest(), len(world.blob), "hf-api",
                          "apache-2.0"))
        files = {name: hashlib.sha256((out / "provision" / f"{name}.json").read_bytes()).hexdigest()
                 for name in ("server", "model", "prepare")}
        self.assertEqual(provenance["provision"], {"server_record_sha256": files["server"],
                                                   "model_record_sha256": files["model"],
                                                   "prepare_sha256": files["prepare"]})


def lab_shard_call(argv: list[str]) -> tuple[int, str, str]:
    return call_main(lab_shard, argv)


# --------------------------------------------------------------------------------------------------- classifier

def _evidence() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    connection = {"purpose": "download", "logical_host": "github.com", "host": "github.com", "status": 302}
    evidence = {
        "server_record": {"verified": True, "verified_by": "github-api", "connections": [connection]},
        "model_record": {"verified": True, "verified_by": "hf-api",
                         "connections": [{**connection, "logical_host": "hub.example", "host": "hub.example"}]},
        "server_record_sha256": "s" * 64, "model_record_sha256": "m" * 64,
        "prepare": {"server": {"record_sha256": "s" * 64}, "model": {"record_sha256": "m" * 64, "path": "/c/x.gguf"},
                    "connections": []},
        "start": {"props": {"model_path": "/c/x.gguf"}},
    }
    rows = [{"host": "127.0.0.1:9", "model_served": "alias", "http_status": 200, "fake_marker": False},
            {"host": "127.0.0.1:9", "model_served": None, "http_status": 503, "fake_marker": False}]
    return evidence, rows


def _classify(evidence: dict[str, Any], rows: list[dict[str, Any]], status: str = "ok",
              harness: bool | None = True, kind: str = "gguf", override: str | None = None) -> tuple[str, str]:
    checks, _ = units.model_checks(evidence, rows, status, harness, "alias", "127.0.0.1:9")
    return units.measurement_class(kind, override, rows, checks)


class ClassifierTests(unittest.TestCase):
    def test_base_evidence_is_model(self) -> None:
        evidence, rows = _evidence()
        self.assertEqual(_classify(evidence, rows), ("model", "verified"))
        self.assertEqual(_classify(evidence, rows, harness=None), ("model", "verified"))
        self.assertEqual(_classify(evidence, rows, status="result_fail"), ("model", "verified"))
        checks, flags = units.model_checks(evidence, rows, "ok", True, "alias", "127.0.0.1:9")
        self.assertEqual(list(checks), list(units.CHECK_KEYS))
        self.assertEqual(flags, [])
        self.assertEqual(units.unit_notes("e3", "model"), ["model_measurement", "synthetic", "runner_hardware"])

    def test_each_condition_alone_is_unverified(self) -> None:
        def mutate(key: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
            evidence, rows = _evidence()
            kwargs: dict[str, Any] = {}
            if key == "model_verified":
                evidence["model_record"]["verified_by"] = "first-use"
            elif key == "server_verified":
                evidence["server_record_sha256"] = "t" * 64
            elif key == "download_hosts":
                evidence["prepare"]["connections"] = [{"purpose": "redirect", "logical_host": "cdn.example",
                                                       "host": "elsewhere.example", "status": 200}]
            elif key == "model_path":
                evidence["start"]["props"]["model_path"] = "/c/other.gguf"
            elif key == "ledger_host":
                rows[1]["host"] = "127.0.0.1:10"
            elif key == "model_served":
                rows[0]["model_served"] = "other"
            elif key == "harness_measurement":
                kwargs["harness"] = False
            elif key == "participation":
                kwargs["status"] = "invalid"
            return evidence, rows, kwargs

        for key in units.CHECK_KEYS:
            with self.subTest(key=key):
                evidence, rows, kwargs = mutate(key)
                self.assertEqual(_classify(evidence, rows, **kwargs), ("unverified", key))
        evidence, rows = _evidence()
        evidence["model_record"]["verified"] = "yes"
        self.assertEqual(_classify(evidence, rows), ("unverified", "model_verified"))
        evidence, rows = _evidence()
        del evidence["prepare"]
        self.assertEqual(_classify(evidence, rows), ("unverified", "model_verified"))
        self.assertEqual(units.measurement_class("gguf", None, rows, None), ("unverified", "no_evidence"))

    def test_first_use_flagged(self) -> None:
        evidence, rows = _evidence()
        evidence["server_record"]["verified_by"] = "first-use"
        checks, flags = units.model_checks(evidence, rows, "ok", True, "alias", "127.0.0.1:9")
        self.assertEqual(flags, ["server_first_use"])
        self.assertEqual(units.measurement_class("gguf", None, rows, checks), ("model", "verified"))
        evidence["server_record"]["verified_by"] = "hf-api"
        self.assertEqual(_classify(evidence, rows), ("unverified", "server_verified"))

    def test_loopback_hosts_never_model(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1", "10.0.0.5", "192.168.1.2", ""):
            evidence, rows = _evidence()
            evidence["model_record"]["connections"] = [{"purpose": "download", "logical_host": host, "host": host,
                                                        "status": 200}]
            with self.subTest(host=host):
                self.assertEqual(_classify(evidence, rows), ("unverified", "download_hosts"))

    def test_harness_verdict_only_true_counts(self) -> None:
        for result, verdict in (({"measurement": True}, True), ({"measurement": False}, False), ({}, False),
                                ({"measurement": "true"}, False), ({"measurement": 1}, False), (None, False),
                                ([], False)):
            with self.subTest(result=result):
                self.assertIs(units.harness_verdict("e3", result), verdict)
        self.assertIsNone(units.harness_verdict("g0", {}))
        evidence, rows = _evidence()
        self.assertEqual(_classify(evidence, rows, harness=units.harness_verdict("e3", {})),
                         ("unverified", "harness_measurement"))

    def test_precedence_fake_override_none(self) -> None:
        evidence, rows = _evidence()
        self.assertEqual(_classify(evidence, rows, kind="none"), ("no-model", "no_model"))
        self.assertEqual(_classify(evidence, rows, kind="fake"), ("plumbing", "fake_kind"))
        self.assertEqual(_classify(evidence, rows, override="fake"), ("plumbing", "provider_override"))
        rows[1]["fake_marker"] = True
        self.assertEqual(_classify(evidence, rows), ("plumbing", "fake_marker"))


if __name__ == "__main__":
    unittest.main()
