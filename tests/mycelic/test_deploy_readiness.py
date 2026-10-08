"""Deploy safety: probes that never wait on the broker or the database, Kubernetes/Compose manifests that match
the service (probe timeouts, grace periods, the shipped rules, the optional settings and the release version), the
single-writer lock on the SQLite file, a SIGTERM that finishes inside the grace period, readiness that a replay over the
wrong signing key blocks until a rebuild, the per-organization volume cap, a log from another release that still
applies (rollback), and documentation that is true of the code."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from fractions import Fraction
from pathlib import Path
from unittest import mock

import yaml
from aiohttp.test_utils import TestClient, TestServer

from mycelic.api import create_app, run_server
from mycelic.config import ConfigError, Settings
from mycelic.integrity import Keyring
from mycelic.metrics import Metrics
from mycelic.service import READY_BLOCK_REASON, MycelicService, _recorded_ratio
from mycelic.store import DatabaseLocked, MycelicStore, acquire_db_lock, release_db_lock
from mycelic.transport import InProcessTransport, JetStreamTransport, subject_for
from mycelic.version import VERSION

from .helpers import (ADMIN_TOKEN, DEMO_RULE, HeldTransport, ServiceHarness, integrity_outcomes, memory_history, rebuild,
                      rebuild_differences, settings)
from .test_aggregation_core import ORG, TEAM, wire

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "mycelic"
PUBLIC_HEALTH_KEYS = {"status", "version", "transport_connected", "consumer_running"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_env(**extra: str) -> dict[str, str]:
    """The test process's environment without any MYCELIC_* variable, so a developer's shell cannot leak in."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MYCELIC_")}
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return env


class StallingTransport(InProcessTransport):
    """``info()`` hangs once ``stall`` is set, like a SIGSTOPped broker: the client still reports connected."""

    stall = False

    async def info(self):  # type: ignore[override]
        if self.stall:
            await asyncio.sleep(30)
        return await super().info()


class HangingCloseTransport(InProcessTransport):
    """``close()`` never returns, like a drain against a broker that stopped answering."""

    aborted = False

    async def close(self) -> None:
        await asyncio.Event().wait()

    def abort(self) -> None:
        self.aborted = True
        self._connected = False


async def open_mcp_stream(port: int, key: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """GET /mcp as an SSE stream, read until the first keep-alive so the handler sits in its endless loop."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((f"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {key}\r\n"
                  "Accept: text/event-stream\r\n\r\n").encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readline(), 5)
    assert b" 200 " in head, head
    seen = b""
    while b": keep-alive" not in seen:
        seen += await asyncio.wait_for(reader.read(1024), 5)
    return reader, writer


def open_mcp_stream_sync(port: int, key: str) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall((f"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {key}\r\n"
                  "Accept: text/event-stream\r\n\r\n").encode())
    seen = b""
    while b": keep-alive" not in seen:
        chunk = sock.recv(1024)
        if not chunk:
            raise AssertionError(f"stream closed: {seen!r}")
        seen += chunk
    assert b" 200 " in seen.split(b"\r\n", 1)[0], seen
    return sock


def http(url: str, *, method: str = "GET", body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def wait_for_http(url: str, *, status: int | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    deadline = time.monotonic() + timeout
    while True:
        try:
            got = http(url)
            if status is None or got[0] == status:
                return got
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise AssertionError(f"{url} did not answer{'' if status is None else f' {status}'} within {timeout}s")
        time.sleep(0.1)


# ---------------------------------------------------------------------------------------------------------- health


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.transport = StallingTransport()
        self.svc = MycelicService(settings(self.tmp.name), transport=self.transport, metrics=Metrics())
        self.svc.status_interval = 0.1
        self.svc.status_timeout = 0.3
        self.client = TestClient(TestServer(create_app(self.svc)))
        await self.client.start_server()
        self.admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.svc.close()
        self.tmp.cleanup()

    async def get(self, path: str, *, headers: dict | None = None, limit: float = 1.0) -> tuple[int, str, float]:
        """GET within ``limit`` seconds (``asyncio.wait_for``), returning status, body and the elapsed time."""
        async def call() -> tuple[int, str]:
            r = await self.client.get(path, headers=headers)
            return r.status, await r.text()

        t0 = time.monotonic()
        status, text = await asyncio.wait_for(call(), limit)
        return status, text, time.monotonic() - t0

    async def get_json(self, path: str, **kw) -> tuple[int, dict]:
        status, text, _ = await self.get(path, **kw)
        return status, json.loads(text)

    async def test_health_and_ready_never_block_on_the_broker(self) -> None:
        await self.svc.start()
        self.assertEqual((await self.get_json("/health"))[1]["status"], "ok")
        self.transport.stall = True
        deadline = time.monotonic() + self.svc.status_timeout + 2.0
        # stale shows first by age (3 status intervals), then the timed-out refresh records its error
        while "error" not in (await self.get_json("/health", headers=self.admin))[1]["checks"]["transport"]:
            self.assertLess(time.monotonic(), deadline, "the refresh under a stalled broker never timed out")
            await asyncio.sleep(0.05)
        for path, headers in (("/health", None), ("/health", self.admin), ("/ready", None)):
            with self.subTest(path=path, admin=headers is not None):
                status, _, elapsed = await self.get(path, headers=headers, limit=1.0)
                self.assertEqual(status, 200)
                self.assertLess(elapsed, 1.0)
        _, public = await self.get_json("/health")
        self.assertEqual(set(public), PUBLIC_HEALTH_KEYS)
        self.assertEqual(public["transport_connected"], self.transport.connected)
        self.assertTrue(public["transport_connected"], "a stalled broker still looks connected")
        self.assertEqual(public["status"], "degraded", "stale counts as degraded")
        _, full = await self.get_json("/health", headers=self.admin)
        self.assertTrue(full["checks"]["transport"]["stale"])
        self.assertIn("timed out after 0.3s", full["checks"]["transport"]["error"])
        status, ready = await self.get_json("/ready")
        self.assertEqual(status, 200, "readiness does not depend on the broker by default")
        self.assertEqual(set(ready), {"ready", "status", "replaying_to_seq", "transport_connected"})
        self.assertEqual(ready["status"], "degraded")

    async def test_probes_never_call_stats_or_transport_info(self) -> None:
        self.svc.status_interval = 3600.0
        await self.svc.start()
        await asyncio.sleep(0.2)                      # the status task's first refresh has run; the next is an hour away
        calls: list[str] = []

        def stats() -> dict:
            calls.append("store.stats")
            raise AssertionError("a probe queried the database")

        async def info() -> dict:
            calls.append("transport.info")
            raise AssertionError("a probe called the broker")

        self.svc.store.stats = stats                  # type: ignore[method-assign]
        self.transport.info = info                    # type: ignore[method-assign]
        for path, headers in (("/health", None), ("/health", self.admin), ("/ready", None), ("/ready", self.admin)):
            with self.subTest(path=path, admin=headers is not None):
                status, _, _ = await self.get(path, headers=headers)
                self.assertEqual(status, 200)
        self.assertEqual(calls, [])

    async def test_unstarted_service_reports_unknown_transport_without_blocking(self) -> None:
        status, text, elapsed = await self.get("/health")
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 1.0)
        public = json.loads(text)
        self.assertEqual(set(public), PUBLIC_HEALTH_KEYS)
        self.assertFalse(public["transport_connected"])
        self.assertEqual(public["status"], "degraded")
        _, full = await self.get_json("/health", headers=self.admin)
        self.assertTrue(full["checks"]["transport"]["stale"])
        self.assertIsNone(full["checks"]["transport"]["age_seconds"])
        self.assertTrue(full["checks"]["db"]["ok"])
        status, ready = await self.get_json("/ready")
        self.assertEqual(status, 503)
        self.assertFalse(ready["ready"])

    async def test_admin_status_and_metrics_are_bounded_under_a_stalled_broker(self) -> None:
        await self.svc.start()
        self.transport.stall = True
        limit = self.svc.status_timeout + 1.0
        status, text, elapsed = await self.get("/admin/status", headers=self.admin, limit=limit)
        self.assertEqual(status, 200)
        self.assertLess(elapsed, limit)
        body = json.loads(text)
        self.assertTrue(body["checks"]["transport"]["stale"])
        self.assertIn("timed out", body["checks"]["transport"]["error"])
        self.assertEqual(body["status"], "degraded")
        self.assertIn("settings", body)
        status, text, elapsed = await self.get("/metrics", headers=self.admin, limit=limit)
        self.assertEqual(status, 200)
        self.assertLess(elapsed, limit)
        self.assertIn("mycelic_consumer_pending", text)

    async def test_connected_is_live_not_cached(self) -> None:
        self.svc.status_interval = 3600.0
        await self.svc.start()
        await asyncio.sleep(0.2)

        async def no_connect() -> None:               # the transport keeper must not reconnect behind the test's back
            pass

        self.transport.connect = no_connect           # type: ignore[method-assign]
        refreshed_at = self.svc._status_at
        self.assertTrue(self.svc._transport_info["connected"])
        self.transport._connected = False             # the broker went away (a killed broker closes the socket)
        _, public = await self.get_json("/health")
        self.assertFalse(public["transport_connected"])
        self.assertEqual(public["status"], "degraded")
        _, ready = await self.get_json("/ready")
        self.assertFalse(ready["transport_connected"])
        self.assertEqual(self.svc._status_at, refreshed_at, "no refresh in between: the probe read it live")
        self.assertTrue(self.svc._transport_info["connected"], "the snapshot itself still says connected")

    async def test_failing_database_reports_failing(self) -> None:
        await self.svc.start()

        def broken() -> dict:
            raise sqlite3.OperationalError("disk I/O error")

        self.svc.store.stats = broken                 # type: ignore[method-assign]
        await self.svc.refresh_status()
        status, public = await self.get_json("/health")
        self.assertEqual((status, public["status"]), (503, "failing"))
        _, full = await self.get_json("/health", headers=self.admin)
        self.assertFalse(full["checks"]["db"]["ok"])
        self.assertIn("disk I/O error", full["checks"]["db"]["error"])
        self.assertEqual((await self.get_json("/ready"))[0], 503)
        del self.svc.store.stats                      # the disk recovers: the next refresh clears the error
        await self.svc.refresh_status()
        status, public = await self.get_json("/health")
        self.assertEqual((status, public["status"]), (200, "ok"))

    async def test_closed_database_reports_failing(self) -> None:
        await self.svc.store.close()
        await self.svc.refresh_status()               # stats on a closed store raise; the refresh does not
        status, public = await self.get_json("/health")
        self.assertEqual((status, public["status"]), (503, "failing"))
        _, full = await self.get_json("/health", headers=self.admin)
        self.assertEqual(full["checks"]["db"]["error"], "the database is closed")
        self.assertEqual((await self.get_json("/ready"))[0], 503)


class LoopResilienceTests(unittest.IsolatedAsyncioTestCase):
    """A database error never ends the publisher or the consumer: each logs it, counts it, backs off and goes on; a
    publish that keeps failing reads degraded; a loop that ended anyway fails /health, so the liveness probe restarts
    the process."""

    async def asyncSetUp(self) -> None:
        self.h = await ServiceHarness(nats_max_deliver=2).start()
        self.addAsyncCleanup(self.h.close)
        await self.h.register("a-1", team="team-a")
        await self.h.settle()

    def errors(self, loop: str) -> float:
        return self.h.service.metrics.loop_errors.labels(loop)._value.get()

    async def test_a_database_error_never_stops_the_publisher(self) -> None:
        from mycelic.store import Tx
        s, real = self.h.service, Tx.mark_published
        calls: list[str] = []

        def once(tx: Tx, event_id: str, js_seq: int | None) -> None:
            calls.append(event_id)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database or disk is full")
            real(tx, event_id, js_seq)

        with self.assertLogs("mycelic.service", "ERROR") as logs, mock.patch.object(Tx, "mark_published", once):
            mid = await self.h.observe("a-1", "Dock 4 is closed.", topic="ops:docks")
            await self.h.settle()
        self.assertTrue(any("publisher loop failed" in line and "database or disk is full" in line for line in logs.output))
        self.assertEqual(calls[0], s.store.get_memory(mid).event_id)
        self.assertIsNotNone(s.store.get_memory(mid).applied_at)
        # and the loop still publishes what comes next
        later = await self.h.observe("a-1", "Dock 5 is closed.", topic="ops:docks")
        await self.h.settle()
        self.assertIsNotNone(s.store.get_memory(later).applied_at)
        self.assertEqual(s.store.stats()["outbox_pending"], 0)
        self.assertTrue(s._publisher_running)
        self.assertEqual(self.errors("publisher"), 1)
        self.assertEqual((await s.health())["status"], "ok")
        self.assertTrue((await s.ready())[0])

    async def test_a_database_error_never_stops_the_consumer(self) -> None:
        from mycelic.store import Tx
        s, real_apply, real_audit = self.h.service, self.h.service.apply_event, Tx.audit
        fault = {"on": True, "hits": 0}

        async def apply_event(event: dict, *, seq: int | None = None) -> str:
            if fault["on"]:
                raise sqlite3.OperationalError("database or disk is full")
            return await real_apply(event, seq=seq)

        def audit(tx: Tx, principal: str, action: str, *args, **kwargs) -> None:
            if fault["on"] and action == "event.failed":
                fault["hits"] += 1
                raise sqlite3.OperationalError("database or disk is full")
            real_audit(tx, principal, action, *args, **kwargs)

        with self.assertLogs("mycelic.service", "ERROR") as logs, mock.patch.object(s, "apply_event", apply_event), \
                mock.patch.object(Tx, "audit", audit):
            await self.h.observe("a-1", "Dock 4 is closed.", topic="ops:docks")
            deadline = time.monotonic() + 10
            while not fault["hits"]:
                self.assertLess(time.monotonic(), deadline, "the terminate path never failed")
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)
            fault["on"] = False
            mid = await self.h.observe("a-1", "Dock 5 is closed.", topic="ops:docks")
            await self.h.settle()
        self.assertTrue(any("consumer loop failed" in line and "database or disk is full" in line for line in logs.output))
        self.assertTrue(s._consumer_running)
        self.assertGreaterEqual(self.errors("consumer"), 1)
        self.assertIsNotNone(s.store.get_memory(mid).applied_at, "the consumer kept applying the log")
        self.assertNotEqual((await s.health())["status"], "failing")
        self.assertTrue((await s.ready())[0])

    async def test_a_publish_that_keeps_failing_is_degraded(self) -> None:
        s = self.h.service
        self.assertEqual((await s.health())["status"], "ok")

        async def refuse(*args, **kwargs) -> int:
            raise ConnectionError("no stream")

        with mock.patch.object(s.transport, "publish", refuse):
            await self.h.observe("a-1", "Dock 4 is closed.", topic="ops:docks")
            deadline = time.monotonic() + 5
            while s._publish_error is None:
                self.assertLess(time.monotonic(), deadline, "the publish never failed")
                await asyncio.sleep(0.02)
            h = await s.health()
            self.assertEqual(h["status"], "degraded")
            self.assertEqual(h["checks"]["publisher"]["last_error"], "ConnectionError: no stream")
            self.assertGreaterEqual(h["checks"]["publisher"]["failing_seconds"], 0)
        await self.h.settle(20)
        h = await s.health()
        self.assertEqual(h["status"], "ok")
        self.assertNotIn("last_error", h["checks"]["publisher"])

    @staticmethod
    def failing_info(transport: InProcessTransport, state: dict) -> object:
        """``info()`` as JetStreamTransport answers it while ``state['fails']``: stream_info timed out (JetStream not ready
        after a broker restart, say) while the client reads connected, so there is an ``error`` and no ``last_seq``."""
        real = transport.info

        async def info() -> dict:
            out = await real()
            if not state["fails"]:
                return out
            state["calls"] = state.get("calls", 0) + 1
            return {"transport": out["transport"], "connected": True, "error": "TimeoutError: nats: timeout"}
        return info

    def judgements(self) -> list[str]:
        return [r["action"] for r in self.h.service.store.recent_audit(100) if r["action"].startswith("recovery.")]

    async def test_a_resync_while_the_broker_cannot_report_its_stream_judges_nothing(self) -> None:
        s = self.h.service
        for i in range(3):
            await self.h.observe("a-1", f"Dock {i} is closed.", topic="ops:docks")
        await self.h.settle()
        applied = s.store.get_meta("last_applied_seq")
        state, real_handle = {"fails": True, "handle_fails": True}, s._handle_delivery

        async def handle(d) -> None:
            if state["handle_fails"]:
                state["handle_fails"] = False
                raise ConnectionError("ack failed: connection lost")
            await real_handle(d)

        with mock.patch.object(s.transport, "info", self.failing_info(s.transport, state)), \
                mock.patch.object(s, "_handle_delivery", handle), self.assertLogs("mycelic.service", "WARNING") as logs:
            await self.h.observe("a-1", "Dock 3 is closed.", topic="ops:docks")
            deadline = time.monotonic() + 10
            # the handling error, then resyncs the broker cannot answer (or, judged on no answer, a 'purged' stream)
            while self.errors("consumer") < 3 and not self.judgements():
                self.assertLess(time.monotonic(), deadline, "the consumer never retried its resync")
                await asyncio.sleep(0.05)
            self.assertEqual(self.judgements(), [])
            self.assertEqual(s.store.get_meta("last_applied_seq"), applied, "a stream of unknown length is not 'purged'")
            self.assertEqual(s.metrics.recoveries.labels("stream_behind_database")._value.get(), 0)
            self.assertFalse(any("purged or recreated" in line for line in logs.output))
            state["fails"] = False
            later = await self.h.observe("a-1", "Dock 4 is closed.", topic="ops:docks")
            await self.h.settle()
        self.assertTrue(any("did not report the stream" in line for line in logs.output))
        self.assertIsNotNone(s.store.get_memory(later).applied_at, "the consumer went on once the broker answered")
        self.assertGreater(int(s.store.get_meta("last_applied_seq")), int(applied))
        self.assertEqual(self.judgements(), [])
        self.assertFalse(s._resync_pending)

    async def test_a_start_while_the_broker_cannot_report_its_stream_judges_once_it_can(self) -> None:
        """A fresh database over a log that holds events must replay it; a start that cannot read the stream's length
        leaves that judgement to the consumer, which makes it before its first fetch once the broker answers."""
        transport = self.h.service.transport
        mid = await self.h.observe("a-1", "Dock 4 is closed.", topic="ops:docks")
        await self.h.settle()
        await self.h.service.stop()                       # its log stays in the shared in-process transport
        state = {"fails": True}
        h2 = ServiceHarness(transport=transport)
        self.addAsyncCleanup(h2.close)
        with mock.patch.object(transport, "info", self.failing_info(transport, state)), \
                self.assertLogs("mycelic.service", "WARNING") as logs:
            await h2.start()
            deadline = time.monotonic() + 10
            while state.get("calls", 0) < 4:
                self.assertLess(time.monotonic(), deadline, "the consumer never retried")
                await asyncio.sleep(0.05)
            self.assertIsNone(h2.service.store.get_memory(mid))
            state["fails"] = False
            deadline = time.monotonic() + 10
            while h2.service.store.get_memory(mid) is None:
                self.assertLess(time.monotonic(), deadline, "the fresh database never replayed the log")
                await asyncio.sleep(0.05)
            await h2.settle()
        self.assertTrue(any("did not report the stream" in line for line in logs.output))
        self.assertEqual(h2.service.store.get_memory(mid).status, "active")
        self.assertTrue((await h2.service.ready())[0])

    async def test_a_loop_that_ended_fails_health(self) -> None:
        s = self.h.service
        client = TestClient(TestServer(create_app(s)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        publisher = next(t for t in s._tasks if t.get_name() == "mycelic-publisher")
        publisher.cancel()
        await asyncio.gather(publisher, return_exceptions=True)
        r = await client.get("/health")
        self.assertEqual((r.status, (await r.json())["status"]), (503, "failing"))
        self.assertEqual((await s.health())["checks"]["dead_loops"], ["mycelic-publisher"])
        self.assertEqual((await client.get("/ready")).status, 503)


# ---------------------------------------------------------------------------------------------------------- manifests


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _statefulset(path: Path) -> dict:
    return next(d for d in _docs(path) if d["kind"] == "StatefulSet")


def _shutdown_timeout_from_configmap() -> float:
    cm = next(d for d in _docs(DEPLOY / "k8s" / "mycelic-configmap.yaml") if d["kind"] == "ConfigMap")
    return float(cm["data"].get("MYCELIC_SHUTDOWN_TIMEOUT_SECONDS", Settings.shutdown_timeout_seconds))


def _compose_seconds(value: str) -> float:
    """A compose duration (``30s``, ``1m30s``, ``1.5m``) in seconds."""
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001, "us": 0.000001}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|us|h|m|s)", str(value))
    if not parts or "".join(n + u for n, u in parts) != str(value):
        raise AssertionError(f"not a compose duration: {value!r}")
    return sum(float(n) * units[u] for n, u in parts)


class ManifestTests(unittest.TestCase):
    def test_k8s_manifests_parse_probe_timeouts_startup_on_health_rules_in_sync(self) -> None:
        files = sorted((DEPLOY / "k8s").glob("*.yaml"))
        self.assertGreater(len(files), 5)
        for f in files:
            with self.subTest(file=f.name):
                self.assertTrue(_docs(f), "every manifest parses and is not empty")
        for name in ("mycelic-statefulset.yaml", "nats-statefulset.yaml"):
            for c in _statefulset(DEPLOY / "k8s" / name)["spec"]["template"]["spec"]["containers"]:
                for kind in ("startupProbe", "readinessProbe", "livenessProbe"):
                    probe = c.get(kind)
                    if probe is None:
                        continue
                    with self.subTest(file=name, container=c["name"], probe=kind):
                        self.assertGreaterEqual(probe.get("timeoutSeconds", 1), 5)
                        if kind != "startupProbe":
                            self.assertEqual(probe.get("failureThreshold"), 3)
        mycelic = _statefulset(DEPLOY / "k8s" / "mycelic-statefulset.yaml")["spec"]["template"]["spec"]["containers"][0]
        startup = mycelic["startupProbe"]
        self.assertEqual(startup["httpGet"]["path"], "/health", "a replay gates readiness, never startup")
        self.assertGreaterEqual(startup["periodSeconds"] * startup["failureThreshold"], 600)
        self.assertEqual(mycelic["readinessProbe"]["httpGet"]["path"], "/ready")
        self.assertEqual(mycelic["livenessProbe"]["httpGet"]["path"], "/health")
        cm = next(d for d in _docs(DEPLOY / "k8s" / "mycelic-configmap.yaml") if d["kind"] == "ConfigMap")
        shipped = json.loads((DEPLOY / "rules.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(cm["data"]["rules.json"]), shipped)
        self.assertEqual(len(shipped["rules"]), 3)

    def test_grace_periods_cover_bounded_shutdown(self) -> None:
        t = _shutdown_timeout_from_configmap()
        pod = _statefulset(DEPLOY / "k8s" / "mycelic-statefulset.yaml")["spec"]["template"]["spec"]
        self.assertGreaterEqual(pod["terminationGracePeriodSeconds"], 2 * t + 5)
        compose = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
        svc = compose["services"]["mycelic"]
        env = svc["environment"]
        self.assertIn("MYCELIC_SHUTDOWN_TIMEOUT_SECONDS", env)
        m = re.fullmatch(r"\$\{MYCELIC_SHUTDOWN_TIMEOUT_SECONDS:-([0-9.]+)\}", str(env["MYCELIC_SHUTDOWN_TIMEOUT_SECONDS"]))
        self.assertIsNotNone(m, env["MYCELIC_SHUTDOWN_TIMEOUT_SECONDS"])
        example = re.search(r"^MYCELIC_SHUTDOWN_TIMEOUT_SECONDS=([0-9.]+)$",
                            (DEPLOY / ".env.example").read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(example)
        grace = _compose_seconds(svc["stop_grace_period"])
        for compose_t in (float(m.group(1)), float(example.group(1))):
            self.assertGreaterEqual(grace, 2 * compose_t + 5)


    def test_optional_settings_pass_through(self) -> None:
        defaults = {"MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG": shipped_quota()["DEPLOYMENT.md"],
                    "MYCELIC_REPLAY_MAX_REJECT_RATIO": Settings.replay_max_reject_ratio,
                    "MYCELIC_VERIFY_MAX_NODES": Settings.verify_max_nodes,
                    "MYCELIC_EXPIRY_SWEEP_SECONDS": Settings.expiry_sweep_seconds}
        example = dict(re.findall(r"^(MYCELIC_[A-Z_]+)=(.*)$", (DEPLOY / ".env.example").read_text(encoding="utf-8"), re.M))
        env = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["mycelic"]["environment"]
        cm = next(d for d in _docs(DEPLOY / "k8s" / "mycelic-configmap.yaml") if d["kind"] == "ConfigMap")["data"]
        for name, default in defaults.items():
            with self.subTest(name=name):
                self.assertIsNotNone(default)
                self.assertEqual(float(example[name]), float(default), ".env.example")
                m = re.fullmatch(rf"\$\{{{name}:-([0-9.]+)\}}", str(env.get(name)))
                self.assertIsNotNone(m, f"compose passes {name} with a default")
                self.assertEqual(float(m.group(1)), float(default), "compose default")
                self.assertIsInstance(cm.get(name), str, "ConfigMap values are strings")
                self.assertEqual(float(cm[name]), float(default), "ConfigMap")
        with mock.patch.dict(os.environ, serve_env(MYCELIC_HOST="127.0.0.1", **{k: v for k, v in example.items() if v}),
                             clear=True):
            s = Settings.from_env()
        self.assertEqual((s.max_active_memories_per_org, s.replay_max_reject_ratio, s.verify_max_nodes, s.expiry_sweep_seconds),
                         tuple(type(getattr(Settings, k))(v) for k, v in zip(
                             ("max_active_memories_per_org", "replay_max_reject_ratio", "verify_max_nodes",
                              "expiry_sweep_seconds"), defaults.values())))
        kustomization = _docs(DEPLOY / "k8s" / "kustomization.yaml")[0]
        self.assertEqual(kustomization["images"][0]["newTag"], VERSION)
        container = _statefulset(DEPLOY / "k8s" / "mycelic-statefulset.yaml")["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], f"mycelic:{VERSION}")


# ---------------------------------------------------------------------------------------------------------- lock


class LockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "mycelic.db"
        self.services: list[MycelicService] = []

    async def asyncTearDown(self) -> None:
        for s in self.services:
            await s.close()
        self.tmp.cleanup()

    def service(self, **overrides) -> MycelicService:
        s = MycelicService(settings(self.tmp.name, **overrides), transport=InProcessTransport(), metrics=Metrics())
        self.services.append(s)
        return s

    async def test_second_service_on_the_same_database_is_refused(self) -> None:
        first = self.service()
        self.assertFalse(os.get_inheritable(first.store._lock_fd), "a child process must not inherit the lock")
        with self.assertRaises(DatabaseLocked) as cm:
            self.service()
        self.assertIsInstance(cm.exception, RuntimeError)
        self.assertIn(str(self.db), str(cm.exception))
        self.assertIn(f"pid {os.getpid()}", str(cm.exception))
        await first.close()
        self.assertTrue(Path(f"{self.db}.lock").exists(), "the lock file is never unlinked")
        third = self.service()
        self.assertTrue(third.store.is_open)

    async def test_a_refused_writer_never_touches_the_database(self) -> None:
        fd = acquire_db_lock(self.db)
        try:
            with self.assertRaises(DatabaseLocked):
                self.service()
            self.assertFalse(self.db.exists(), "refused before sqlite created (or migrated) the file")
        finally:
            release_db_lock(fd)

    async def test_lock_released_when_only_the_store_closes(self) -> None:
        crashed = self.service()
        await crashed.start()
        await crashed.transport.close()               # the crash simulation test_jetstream uses
        await crashed.store.close()
        self.assertTrue(self.service().store.is_open)

    async def test_lock_held_by_a_killed_process_is_released(self) -> None:
        code = ("import sys, time\nfrom mycelic.store import acquire_db_lock\nacquire_db_lock(sys.argv[1])\n"
                "print('locked', flush=True)\ntime.sleep(120)\n")
        proc = subprocess.Popen([sys.executable, "-c", code, str(self.db)], cwd=ROOT, env=serve_env(),
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self.addCleanup(proc.stdout.close)            # type: ignore[union-attr]
        self.assertEqual(proc.stdout.readline().strip(), "locked")  # type: ignore[union-attr]
        with self.assertRaises(DatabaseLocked) as cm:
            self.service()
        self.assertIn(f"pid {proc.pid}", str(cm.exception))
        proc.kill()
        proc.wait()
        self.assertTrue(self.service().store.is_open, "the kernel released the lock with its holder")

    async def test_stale_lock_file_does_not_block(self) -> None:
        lock = Path(f"{self.db.resolve()}.lock")
        lock.write_bytes(b"\x00garbage 99999\n")
        self.assertTrue(self.service().store.is_open)
        self.assertEqual(lock.read_text(), f"{os.getpid()}\n")

    async def test_lock_released_when_store_construction_fails(self) -> None:
        c = sqlite3.connect(self.db)
        c.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL); INSERT INTO meta VALUES ('schema_version', '99');")
        c.close()
        opened: list[sqlite3.Connection] = []
        real_connect = sqlite3.connect

        def connect(*a, **kw):
            conn = real_connect(*a, **kw)
            opened.append(conn)
            return conn

        with mock.patch("mycelic.store.sqlite3.connect", connect):
            with self.assertRaises(RuntimeError) as cm:
                self.service()
        self.assertNotIsInstance(cm.exception, DatabaseLocked)
        self.assertIn("newer", str(cm.exception))
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")             # the half-built store closed its connection
        fd = acquire_db_lock(self.db)
        self.assertIsNotNone(fd)
        release_db_lock(fd)

    async def test_unlockable_path_names_the_path(self) -> None:
        blocker = Path(self.tmp.name) / "not-a-directory"
        blocker.write_text("x")
        db = blocker / "mycelic.db"
        with self.assertRaises(RuntimeError) as cm:
            self.service(db_path=str(db))
        self.assertNotIsInstance(cm.exception, DatabaseLocked)
        self.assertIn(f"{db}.lock", str(cm.exception))

    async def test_injected_store_and_memory_db_take_no_lock(self) -> None:
        st = settings(self.tmp.name)
        for _ in range(2):
            s = MycelicService(st, store=MycelicStore(":memory:"), transport=InProcessTransport(), metrics=Metrics())
            self.services.append(s)
        self.assertFalse(Path(f"{st.db_path}.lock").exists())
        self.assertFalse(Path(st.db_path).exists())
        for _ in range(2):
            self.assertIsNone(self.service(db_path=":memory:").store._lock_fd)
        self.assertIsNone(acquire_db_lock(":memory:"))
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), [])

    async def test_symlinked_path_contends_with_the_target(self) -> None:
        link = Path(self.tmp.name) / "link.db"
        link.symlink_to(self.db)
        self.service()
        for spelling in (link, Path(self.tmp.name) / "sub" / ".." / "mycelic.db", Path(os.path.relpath(self.db))):
            with self.subTest(path=str(spelling)):
                with self.assertRaises(DatabaseLocked):
                    self.service(db_path=str(spelling))

    async def test_close_twice_is_harmless(self) -> None:
        s = self.service()
        await s.start()
        await s.close()
        await s.close()
        await s.store.close()
        release_db_lock(None)
        fd = acquire_db_lock(self.db)
        self.assertIsNotNone(fd)
        release_db_lock(fd)

    def test_serve_on_a_locked_database_exits_2(self) -> None:
        def serve(db: Path) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-m", "mycelic", "serve", "--db", str(db), "--nats-url", "",
                                   "--port", str(free_port())],
                                  cwd=ROOT, env=serve_env(MYCELIC_HOST="127.0.0.1"), capture_output=True, text=True, timeout=60)

        fd = acquire_db_lock(self.db)
        try:
            r = serve(self.db)
        finally:
            release_db_lock(fd)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("configuration error", r.stderr)
        self.assertIn(str(self.db), r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        blocker = Path(self.tmp.name) / "not-a-directory"
        blocker.write_text("x")
        r = serve(blocker / "mycelic.db")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("configuration error", r.stderr)
        self.assertIn(f"{blocker / 'mycelic.db'}.lock", r.stderr)

    async def test_online_backup_works_while_locked(self) -> None:
        h = await ServiceHarness().start()
        try:
            await h.register("log-1", team="logistics")
            mid = await h.observe("log-1", "Rotterdam terminal 3 strike announced for weeks 41-43.",
                                  topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.8)
            await h.settle()
            self.assertEqual(h.service.store._conn.execute("PRAGMA locking_mode").fetchone()[0], "normal")
            backup = Path(self.tmp.name) / "backup.db"
            src, dst = sqlite3.connect(h.settings.db_path), sqlite3.connect(backup)
            try:
                src.backup(dst)
            finally:
                src.close()
            try:
                self.assertIn((mid,), dst.execute("SELECT memory_id FROM memories").fetchall())
                self.assertIn(("log-1",), dst.execute("SELECT agent_id FROM agents").fetchall())
            finally:
                dst.close()
        finally:
            await h.close()


# ---------------------------------------------------------------------------------------------------------- shutdown


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_shutdown_is_bounded(self) -> None:
        svc = MycelicService(settings(self.tmp.name, shutdown_timeout_seconds=1.0), transport=HangingCloseTransport(),
                             metrics=Metrics())
        await svc.start()
        with self.assertLogs("mycelic.service", "WARNING") as logs:
            t0 = time.monotonic()
            await asyncio.wait_for(svc.close(), 5.0)
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0)
        self.assertTrue(any("transport did not close within" in line for line in logs.output), logs.output)
        self.assertTrue(svc.transport.aborted)
        self.assertFalse(svc.store.is_open)
        fd = acquire_db_lock(svc.settings.db_path)
        self.assertIsNotNone(fd, "the lock was released")
        release_db_lock(fd)

    async def test_stuck_task_still_leaves_the_transport_half_a_second(self) -> None:
        svc = MycelicService(settings(self.tmp.name, shutdown_timeout_seconds=1.0), transport=HangingCloseTransport(),
                             metrics=Metrics())
        await svc.start()
        release = asyncio.Event()

        async def stubborn() -> None:                 # ignores cancellation, like a task stuck in uncancellable work
            while not release.is_set():
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    continue

        stuck = asyncio.create_task(stubborn(), name="stubborn")
        svc._tasks.append(stuck)
        try:
            with self.assertLogs("mycelic.service", "WARNING") as logs:
                t0 = time.monotonic()
                await asyncio.wait_for(svc.close(), 5.0)
                elapsed = time.monotonic() - t0
        finally:
            release.set()
            await stuck
        self.assertGreaterEqual(elapsed, 1.4)
        self.assertLess(elapsed, 2.0)
        self.assertTrue(any("stubborn did not stop" in line for line in logs.output), logs.output)
        self.assertTrue(any("transport did not close within 0.5s" in line for line in logs.output), logs.output)
        self.assertFalse(svc.store.is_open)

    async def test_drain_timeout_fits_the_budget(self) -> None:
        import nats

        for budget, drain in ((10.0, 5), (1.0, 1), (2.5, 1), (30.0, 15)):
            with self.subTest(budget=budget):
                seen: dict = {}

                async def connect(**options):
                    seen.update(options)
                    raise ConnectionError("not reaching a broker in this test")

                t = JetStreamTransport(Settings(nats_url="nats://127.0.0.1:1", shutdown_timeout_seconds=budget))
                with mock.patch.object(nats, "connect", connect):
                    with self.assertRaises(ConnectionError):
                        await t.connect()
                self.assertEqual(seen["drain_timeout"], drain)
        t.abort()                                     # never raises, connected or not
        self.assertFalse(t.connected)

    async def test_runner_cleanup_is_bounded_with_an_open_mcp_stream(self) -> None:
        svc = MycelicService(settings(self.tmp.name, shutdown_timeout_seconds=1.0), transport=InProcessTransport(),
                             metrics=Metrics())
        await svc.start()
        runner = await run_server(svc)
        writer = None
        try:
            port = runner.addresses[0][1]
            _, key = await svc.register_agent({"enterprise": "northwind", "team": "logistics", "agent_id": "mcp-1"})
            _, writer = await open_mcp_stream(port, key)
            t0 = time.monotonic()
            await asyncio.wait_for(runner.cleanup(), 10.0)
            elapsed = time.monotonic() - t0
        finally:
            if writer is not None:
                writer.close()
            await svc.close()
        self.assertLess(elapsed, svc.settings.shutdown_timeout_seconds + 1.0)

    def _serve(self, *args: str, timeout_seconds: str = "2") -> tuple[subprocess.Popen, int, Path]:
        port = free_port()
        log = Path(self.tmp.name) / "serve.log"
        env = serve_env(MYCELIC_HOST="127.0.0.1", MYCELIC_PORT=str(port), MYCELIC_DB_PATH=str(Path(self.tmp.name) / "mycelic.db"),
                        MYCELIC_ADMIN_TOKEN=ADMIN_TOKEN, MYCELIC_SHUTDOWN_TIMEOUT_SECONDS=timeout_seconds,
                        MYCELIC_LOG_LEVEL="INFO")
        with log.open("w") as out:
            proc = subprocess.Popen([sys.executable, "-m", "mycelic", "serve", *args], cwd=ROOT, env=env,
                                    stdout=out, stderr=subprocess.STDOUT)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc, port, log

    def _sigterm(self, proc: subprocess.Popen, limit: float) -> tuple[int, float]:
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise AssertionError(f"still running {limit}s after SIGTERM")
        return rc, time.monotonic() - t0

    def test_sigterm_with_open_mcp_stream_exits_promptly(self) -> None:
        proc, port, log = self._serve("--nats-url", "")
        base = f"http://127.0.0.1:{port}"
        wait_for_http(base + "/ready", status=200)
        status, res = http(base + "/admin/agents", method="POST", token=ADMIN_TOKEN,
                           body={"enterprise": "northwind", "team": "logistics", "agent_id": "mcp-1"})
        self.assertEqual(status, 201, res)
        sock = open_mcp_stream_sync(port, res["api_key"])
        try:
            rc, elapsed = self._sigterm(proc, 7.0)
        finally:
            sock.close()
        text = log.read_text()
        self.assertEqual(rc, 0, text)
        self.assertLess(elapsed, 7.0)
        self.assertIn("shutting down", text)

    def test_sigterm_during_startup_is_honoured(self) -> None:
        # nothing listens on this port: the first connect to the broker is retried for ever, so start never returns
        proc, port, log = self._serve("--nats-url", f"nats://127.0.0.1:{free_port()}")
        base = f"http://127.0.0.1:{port}"
        status, health = wait_for_http(base + "/health")
        self.assertEqual((status, health["transport_connected"]), (200, False))
        self.assertEqual(http(base + "/ready")[0], 503)
        rc, elapsed = self._sigterm(proc, 2 * 2 + 5)
        text = log.read_text()
        self.assertEqual(rc, 0, text)
        self.assertIn("shutting down", text)
        self.assertNotIn('"mycelic": "started"', text)


# ---------------------------------------------------------------------------------------------------------- signing keys


KEY_A = "readiness-signing-key-a-0123456789abcdef"
KEY_B = "readiness-signing-key-b-fedcba9876543210"
KEY_C = "unrelated-signing-key-c-0011223344556677"      # configured nowhere: what a forger might hold


def audit_details(store: MycelicStore, action: str) -> list[dict]:
    """The details of every audit row with ``action``, oldest first."""
    return [a["detail"] for a in reversed(store.recent_audit(100_000)) if a["action"] == action]


def forged(event_id: str, *, signed_by: str | None) -> tuple[str, bytes, str, dict[str, str]]:
    """A stream entry claiming a note of log-1, unsigned or signed by a key the service does not hold."""
    data = json.dumps(wire("memory.observed", {"memory_id": f"mem_{event_id}", "org_id": ORG, "layer": "agent",
                                               "scope": f"{TEAM}/log-1", "text": "forged", "producer_id": "log-1"},
                           event_id=event_id)).encode()
    headers = {"Mycelic-Signature": Keyring(signed_by).event_signature(data)} if signed_by else {}
    return subject_for(ORG, "memory.observed"), data, event_id, headers


def signed_note(event_id: str, key: str) -> tuple[str, bytes, str, dict[str, str]]:
    """A stream entry signed with ``key``: an agent event that applies without touching any memory."""
    data = json.dumps(wire("agent.event", {"type": "note"}, event_id=event_id)).encode()
    return subject_for(ORG, "agent.event"), data, event_id, {"Mycelic-Signature": Keyring(key).event_signature(data)}


def recorded_ratio(rejected: int, seen: int) -> float:
    """The ``ratio`` a judged replay records: rejected/seen rounded up to 6 places, computed exactly here."""
    return math.ceil(Fraction(rejected, seen) * 10**6) / 10**6


def agent_state(service: MycelicService) -> list[tuple]:
    return sorted((a["agent_id"], a["status"], a["log_status"])
                  for a in service.store._conn.execute("SELECT agent_id, status, log_status FROM agents"))


class SignatureReadinessTests(unittest.IsolatedAsyncioTestCase):
    """A replay that rejects the signature of more than MYCELIC_REPLAY_MAX_REJECT_RATIO of what it consumes leaves readiness
    blocked until a rebuild into a fresh database, or a ratio raised to the recorded one and a restart; fewer rejections
    only warn.  In process: the stream is an in-process log holding what a seeded node signed."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.services: list[MycelicService] = []
        self.clients: list[TestClient] = []

    async def asyncTearDown(self) -> None:
        for c in self.clients:
            await c.close()
        for s in self.services:
            await s.close()
        self.tmp.cleanup()

    async def node(self, name: str, log: list | None = None, *, transport: InProcessTransport | None = None,
                   start: bool = True, **overrides) -> MycelicService:
        """A service on the database in ``root/name``, over ``transport`` or a new in-process stream holding ``log`` (its
        consumer at the start, as a durable consumer created for a fresh database is)."""
        where = self.root / name
        where.mkdir(exist_ok=True)
        if transport is None:
            transport = InProcessTransport()
            for entry in log or []:
                transport._log.append(entry)
                transport._seen_ids[entry[2]] = len(transport._log)
        s = MycelicService(settings(where, **overrides), transport=transport, metrics=Metrics())
        self.services.append(s)
        if start:
            await s.start()
        return s

    async def replayed(self, s: MycelicService, timeout: float = 20.0) -> None:
        """Wait until the replay has completed (in memory and in the database) and the consumer has nothing left."""
        deadline = time.monotonic() + timeout
        while s._replay_target is not None or s.store.get_meta("replay_target_seq") is not None:
            self.assertLess(time.monotonic(), deadline, "the replay did not complete")
            await asyncio.sleep(0.02)
        self.assertTrue(await s.wait_idle(timeout))

    async def http(self, s: MycelicService) -> TestClient:
        client = TestClient(TestServer(create_app(s)))
        await client.start_server()
        self.clients.append(client)
        return client

    async def signed_log(self) -> tuple[list, dict[str, str], dict, list]:
        """What a seeded node signed with KEY_A (the rule, five agents of which one is revoked, four notes and a
        retraction): its stream, the agent keys, and the memory history and agents a rebuild must reproduce."""
        s = await self.node("origin", event_signing_key=KEY_A)
        await s.upsert_rule(DEMO_RULE)
        keys = {}
        for agent, team, dept in (("log-1", "logistics", "ops"), ("log-2", "logistics", "ops"), ("proc-1", "procurement", "ops"),
                                  ("sales-1", "field-sales", "commercial"), ("temp-1", "logistics", "ops")):
            _, keys[agent] = await s.register_agent({"enterprise": ORG, "region": "emea", "subsidiary": "nw-gmbh",
                                                     "department": dept, "team": team, "agent_id": agent})
        ids = {}
        for agent, topic, slot in (("log-1", "supply:sd-9/transport", "transport_disruption"),
                                   ("log-2", "supply:sd-9/transport", "transport_disruption"),
                                   ("proc-1", "supply:sd-9/supplier", "supplier_buffer_low"),
                                   ("sales-1", "supply:sd-9/demand", "demand_commitment"),
                                   ("temp-1", "supply:sd-9/transport", "transport_disruption")):
            m, _ = await s.ingest_memory(s.authenticate(f"Bearer {keys[agent]}"),
                                         {"text": f"{slot} for sd-9 by {agent}", "topic": topic, "slot": slot, "entity": "sd-9"})
            ids[agent] = m.memory_id
        await s.retract(s.authenticate(f"Bearer {keys['temp-1']}"), ids["temp-1"])
        await s.revoke_agent("temp-1")
        self.assertTrue(await s.wait_idle(20))
        self.assertEqual(s.store.stats()["memories_by_layer"]["enterprise"], 1)
        log = list(s.transport._log)
        before, agents = memory_history(s.store), agent_state(s)
        await s.close()
        return log, keys, before, agents

    async def test_replay_under_the_ratio_does_not_block(self) -> None:
        log, _, before, agents = await self.signed_log()
        mixed = log[:3] + [forged("evt_forged_unsigned", signed_by=None)] + log[3:9] + [forged("evt_forged_c", signed_by=KEY_C)] + log[9:]
        n, k = len(mixed), 2
        for ratio, blocked in ((k / n, False), ((k - 1) / n, True), (0.0, True)):
            with self.subTest(ratio=ratio):
                s = await self.node(f"ratio-{ratio}", mixed, event_signing_key=KEY_A, replay_max_reject_ratio=ratio)
                await self.replayed(s)
                ok, detail = await s.ready()
                self.assertEqual(ok, not blocked)
                self.assertEqual(detail.get("reason"), READY_BLOCK_REASON if blocked else None)
                self.assertEqual(audit_details(s.store, "recovery.signature_rejections"),
                                 [{"seen": n, "rejected": k, "ratio": recorded_ratio(k, n), "max_ratio": ratio, "blocked": blocked}])
                rejected = [d for d in audit_details(s.store, "event.rejected") if d.get("reason") == "invalid signature"]
                self.assertEqual(sorted(d["seq"] for d in rejected), [4, 11], "one event.rejected per forgery, at its seq")
                block = s.store.get_meta("ready_block")
                if blocked:
                    self.assertEqual({k_: v for k_, v in json.loads(block).items() if k_ != "at"},
                                     {"reason": "signature_rejections", "seen": n, "rejected": k})
                else:
                    self.assertIsNone(block)
                self.assertEqual(s.metrics.recoveries.labels("replay_signature_rejections")._value.get(), 1 if blocked else 0)
                self.assertIsNone(s.store.get_meta("replay_seen"), "the counts end with the replay")
                self.assertIsNone(s.store.get_meta("replay_sig_rejected"))
                self.assertEqual(memory_history(s.store), before, "the forgeries changed nothing")
                self.assertEqual(agent_state(s), agents)
                await s.close()

    async def test_replay_with_nothing_seen_does_not_block(self) -> None:
        s = await self.node("empty", event_signing_key=KEY_A, replay_max_reject_ratio=0.0)
        client = await self.http(s)
        r = await client.post("/admin/replay", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        self.assertEqual(r.status, 202)
        self.assertEqual(await r.json(), {"replaying": True, "target_seq": None})
        self.assertTrue((await s.ready())[0])
        self.assertEqual(audit_details(s.store, "recovery.signature_rejections"), [])
        for key in ("ready_block", "replay_seen", "replay_sig_rejected", "replay_target_seq"):
            self.assertIsNone(s.store.get_meta(key), key)

    async def test_block_persists_warns_on_replay_and_lifts_only_by_fresh_rebuild_or_ratio(self) -> None:
        log, keys, before, agents = await self.signed_log()
        n = len(log)
        admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        # the database is lost and the signing key was regenerated in place: every event is rejected
        s = await self.node("lost", log, event_signing_key=KEY_B)
        transport = s.transport
        await self.replayed(s)
        self.assertEqual(memory_history(s.store), {})
        self.assertEqual(agent_state(s), [])
        ok, detail = await s.ready()
        self.assertFalse(ok)
        self.assertEqual(detail["reason"], READY_BLOCK_REASON)
        self.assertEqual(audit_details(s.store, "recovery.signature_rejections"),
                         [{"seen": n, "rejected": n, "ratio": 1.0, "max_ratio": 0.01, "blocked": True}])
        client = await self.http(s)
        with mock.patch.object(s.store, "get_meta", side_effect=AssertionError("a probe read the database")), \
                mock.patch.object(s.store, "stats", side_effect=AssertionError("a probe read the database")):
            r = await client.get("/ready")
            self.assertEqual(r.status, 503)
            self.assertTrue((await r.json())["reason"].startswith("signature_rejections"))
        r = await client.get("/health")
        self.assertEqual((r.status, (await r.json())["status"]), (200, "degraded"), "liveness holds: the pod is never restarted")
        r = await client.get("/admin/status", headers=admin)
        block = (await r.json())["checks"]["consumer"]["ready_block"]
        self.assertEqual({k: block[k] for k in ("reason", "seen", "rejected", "ratio", "max_ratio")},
                         {"reason": "signature_rejections", "seen": n, "rejected": n, "ratio": 1.0, "max_ratio": 0.01})
        r = await client.get("/metrics", headers=admin)
        self.assertIn('mycelic_recovery_total{kind="replay_signature_rejections"} 1.0', await r.text())
        # a second replay over the ratio rewrites the block with its own counts (one more event, signed by B)
        extra = json.dumps(wire("agent.event", {"type": "note"}, event_id="evt_signed_by_b")).encode()
        await transport.publish(subject_for(ORG, "agent.event"), extra, "evt_signed_by_b", headers=s._sign(extra))
        r = await client.post("/admin/replay", headers=admin)
        self.assertEqual(r.status, 202)
        self.assertIn("warning", await r.json())
        await self.replayed(s)
        recorded = json.loads(s.store.get_meta("ready_block"))
        self.assertEqual((recorded["seen"], recorded["rejected"]), (n + 1, n))
        self.assertEqual(s.metrics.recoveries.labels("replay_signature_rejections")._value.get(), 2)
        await s.close()

        # reopened with the corrected keyring: blocked at construction and after start, and a replay rejecting nothing
        # neither lifts nor rewrites the block
        fixed = dict(event_signing_key=KEY_B, event_signing_keys_previous=[KEY_A])
        s2 = await self.node("lost", transport=transport, start=False, **fixed)
        self.assertFalse((await s2.ready())[0])
        self.assertEqual((await s2.ready())[1]["reason"], READY_BLOCK_REASON)
        await s2.start()
        self.assertTrue(await s2.wait_idle(10))
        self.assertEqual(await s2.ready(), (False, {"ready": False, "status": "degraded", "replaying_to_seq": None,
                                                     "transport_connected": True, "reason": READY_BLOCK_REASON}))
        client2 = await self.http(s2)
        r = await client2.post("/admin/replay", headers=admin)
        self.assertEqual(r.status, 202)
        body = await r.json()
        self.assertIn("move the database aside", body["warning"])
        self.assertEqual(audit_details(s2.store, "replay")[-1]["warning"], body["warning"])
        await self.replayed(s2)
        ok, detail = await s2.ready()
        self.assertFalse(ok, "a replay into the blocked database never lifts the block")
        self.assertEqual(detail["reason"], READY_BLOCK_REASON)
        self.assertEqual(json.loads(s2.store.get_meta("ready_block")), recorded)
        self.assertEqual(len(audit_details(s2.store, "recovery.signature_rejections")), 2, "nothing rejected, nothing judged")
        self.assertEqual(s2.metrics.recoveries.labels("replay_signature_rejections")._value.get(), 0)
        await s2.close()

        # the escape hatch for a stream that really holds forgeries: a ratio at the recorded one lifts it in place
        self.assertEqual(recorded["seen"], n + 1)
        for ratio, ready in (((n - 1) / (n + 1), False), (n / (n + 1), True), (recorded_ratio(n, n + 1), True), (1.0, True)):
            with self.subTest(ratio=ratio):
                s3 = await self.node("lost", transport=transport, replay_max_reject_ratio=ratio, **fixed)
                self.assertTrue(await s3.wait_idle(10))
                self.assertEqual((await s3.ready())[0], ready)
                self.assertIsNotNone(s3.store.get_meta("ready_block"), "the block itself is never deleted")
                await s3.close()

        # the remedy: the database moved aside, a rebuild with the corrected keyring is ready and complete
        s4 = await self.node("rebuilt", list(transport._log), **fixed)
        await self.replayed(s4)
        self.assertTrue((await s4.ready())[0])
        self.assertEqual(memory_history(s4.store), before)
        self.assertEqual(agent_state(s4), agents)
        self.assertEqual(audit_details(s4.store, "recovery.signature_rejections"), [])
        self.assertEqual(s4.authenticate(f"Bearer {keys['sales-1']}").id, "sales-1")

    async def test_unparsable_block_holds(self) -> None:
        s = await self.node("garbled", event_signing_key=KEY_A, replay_max_reject_ratio=1.0)
        async with s.store.transaction() as tx:
            tx.set_meta("ready_block", "{not json")
        await s.close()
        s2 = await self.node("garbled", event_signing_key=KEY_A, replay_max_reject_ratio=1.0)
        self.assertTrue(await s2.wait_idle(10))
        ok, detail = await s2.ready()
        self.assertEqual((ok, detail["reason"]), (False, READY_BLOCK_REASON), "a block that does not parse fails safe")
        self.assertEqual((await s2.health(live=True))["checks"]["consumer"]["ready_block"], {"reason": "signature_rejections"})

    async def test_replay_counts_survive_a_crash(self) -> None:
        log, _, before, _ = await self.signed_log()
        mixed = log[:1] + [forged("evt_forged_early", signed_by=None)] + log[1:]
        n = len(mixed)
        # a rebuild that dies after three deliveries (the second a forgery), applied by hand with no background loops
        s = await self.node("crash", mixed, start=False, event_signing_key=KEY_A, replay_max_reject_ratio=0.5)
        self.services.remove(s)                     # it dies below; nothing closes it again
        transport = s.transport
        await s.start(background=False)
        self.assertEqual(s._replay_target, n)
        for _ in range(3):
            for d in await transport.fetch(1, 1.0):
                await s._handle_delivery(d)
        self.assertEqual((s.store.get_meta("replay_seen"), s.store.get_meta("replay_sig_rejected")), ("3", "1"))
        await transport.close()
        await s.store.close()
        # the restart resumes the replay where the consumer stood, and keeps counting
        s2 = await self.node("crash", transport=transport, event_signing_key=KEY_A, replay_max_reject_ratio=0.5)
        self.assertGreater(s2.metrics.recoveries.labels("replay_resumed")._value.get(), 0)
        await self.replayed(s2)
        self.assertEqual(audit_details(s2.store, "recovery.signature_rejections"),
                         [{"seen": n, "rejected": 1, "ratio": recorded_ratio(1, n), "max_ratio": 0.5, "blocked": False}])
        self.assertTrue((await s2.ready())[0])
        self.assertEqual(memory_history(s2.store), before)

    async def test_a_replay_that_can_never_complete_is_not_judged(self) -> None:
        """A replay cut short by a crash, and a stream that now ends before what the database applied (purged or
        recreated): that replay can never complete, so its partial counts are discarded without a judgement."""
        log = [forged(f"evt_purged_forged_{i}", signed_by=None) for i in range(3)] + [signed_note(f"evt_purged_{i}", KEY_A)
                                                                                     for i in range(3)]
        s = await self.node("purged", log, start=False, event_signing_key=KEY_A)
        self.services.remove(s)                     # it dies below; nothing closes it again
        await s.start(background=False)
        self.assertEqual(s._replay_target, len(log))
        for _ in range(3):
            for d in await s.transport.fetch(1, 1.0):
                await s._handle_delivery(d)
        self.assertEqual((s.store.get_meta("replay_seen"), s.store.get_meta("replay_sig_rejected")), ("3", "3"))
        await s.transport.close()
        await s.store.close()
        s2 = await self.node("purged", event_signing_key=KEY_A)          # over a new, empty stream
        self.assertTrue(await s2.wait_idle(10))
        self.assertEqual(s2.metrics.recoveries.labels("stream_behind_database")._value.get(), 1)
        self.assertEqual(len(audit_details(s2.store, "recovery.stream_behind_database")), 1)
        self.assertTrue((await s2.ready())[0], "3 of 3 rejected so far, but never judged")
        self.assertEqual(audit_details(s2.store, "recovery.signature_rejections"), [])
        for key in ("ready_block", "replay_seen", "replay_sig_rejected", "replay_target_seq"):
            self.assertIsNone(s2.store.get_meta(key), key)
        self.assertEqual(s2.metrics.recoveries.labels("replay_signature_rejections")._value.get(), 0)

    async def test_a_new_replay_target_starts_the_counts_again(self) -> None:
        """POST /admin/replay in the middle of a replay starts it again from the first event, and its counts with it,
        so what the abandoned pass consumed is not counted twice."""
        log = [forged("evt_again_forged", signed_by=None)] + [signed_note(f"evt_again_{i}", KEY_A) for i in range(5)]
        n = len(log)
        s = await self.node("again", log, start=False, event_signing_key=KEY_A, replay_max_reject_ratio=0.5)
        await s.start(background=False)
        for _ in range(3):
            for d in await s.transport.fetch(1, 1.0):
                await s._handle_delivery(d)
        self.assertEqual((s.store.get_meta("replay_seen"), s.store.get_meta("replay_sig_rejected")), ("3", "1"))
        self.assertEqual((await s.replay())["target_seq"], n)
        self.assertEqual((s.store.get_meta("replay_seen"), s.store.get_meta("replay_sig_rejected")), ("0", "0"))
        for _ in range(n):
            for d in await s.transport.fetch(1, 1.0):
                await s._handle_delivery(d)
        self.assertIsNone(s._replay_target, "the replay completed")
        self.assertEqual(audit_details(s.store, "recovery.signature_rejections"),
                         [{"seen": n, "rejected": 1, "ratio": recorded_ratio(1, n), "max_ratio": 0.5, "blocked": False}])

    def test_recorded_ratio_is_the_least_six_place_value_that_lifts_the_block(self) -> None:
        """For every rejected/seen up to 400 events: the recorded ratio is rejected/seen rounded up to 6 places (never
        float noise above it: 83/160 stays 0.51875), the service's strict ``rejected / seen > ratio`` does not block at
        it, it does block one step lower, and the value survives JSON and the environment as written."""
        for seen in range(1, 401):
            for rejected in range(1, seen + 1):
                value = _recorded_ratio(rejected, seen)
                self.assertEqual(value, recorded_ratio(rejected, seen), (rejected, seen))
                self.assertFalse(rejected / seen > value, (rejected, seen))
                self.assertTrue(rejected / seen > (round(value * 10**6) - 1) / 10**6, (rejected, seen))
                self.assertEqual(float(json.dumps(value)), value)
                self.assertLessEqual(len(json.dumps(value).partition(".")[2]), 6, (rejected, seen))

    async def test_the_recorded_ratio_as_shown_lifts_the_block(self) -> None:
        """The escape hatch exactly as documented: MYCELIC_REPLAY_MAX_REJECT_RATIO set to the ``ratio`` /admin/status
        shows, copied as text, lifts the block at the restart, and a later replay judged at that setting does not block,
        also where rejected/seen has no 6-place form (2 of 7 is recorded as 0.285715, where 0.285714 would leave it
        blocked); one step lower still blocks."""
        admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        for rejected, seen, shown in ((2, 7, "0.285715"), (1, 12, "0.083334")):
            with self.subTest(rejected=rejected, seen=seen):
                name = f"shown-{rejected}-of-{seen}"
                log = [forged(f"evt_{name}_forged_{i}", signed_by=None) for i in range(rejected)] + \
                    [signed_note(f"evt_{name}_{i}", KEY_A) for i in range(seen - rejected)]
                s = await self.node(name, log, event_signing_key=KEY_A)
                await self.replayed(s)
                self.assertFalse((await s.ready())[0])
                r = await (await self.http(s)).get("/admin/status", headers=admin)
                text = json.dumps((await r.json())["checks"]["consumer"]["ready_block"]["ratio"])
                self.assertEqual(text, shown)
                self.assertEqual(audit_details(s.store, "recovery.signature_rejections")[0]["ratio"], float(shown))
                transport = s.transport
                await s.close()
                with mock.patch.dict(os.environ, {**serve_env(MYCELIC_HOST="127.0.0.1"),
                                                  "MYCELIC_REPLAY_MAX_REJECT_RATIO": text}, clear=True):
                    configured = Settings.from_env().replay_max_reject_ratio
                s2 = await self.node(name, transport=transport, event_signing_key=KEY_A, replay_max_reject_ratio=configured)
                self.assertTrue(await s2.wait_idle(10))
                self.assertTrue((await s2.ready())[0], f"{shown} as shown lifts the block")
                await s2.replay()
                await self.replayed(s2)
                self.assertTrue((await s2.ready())[0], "a replay judged at that ratio does not block again")
                self.assertEqual(audit_details(s2.store, "recovery.signature_rejections")[-1],
                                 {"seen": seen, "rejected": rejected, "ratio": float(shown), "max_ratio": configured,
                                  "blocked": False})
                await s2.close()
                lower = (round(float(shown) * 10**6) - 1) / 10**6
                s3 = await self.node(name, transport=transport, event_signing_key=KEY_A, replay_max_reject_ratio=lower)
                self.assertTrue(await s3.wait_idle(10))
                self.assertFalse((await s3.ready())[0], f"{lower} is below {rejected}/{seen}")
                await s3.close()


# ---------------------------------------------------------------------------------------------------------- volume cap


def shipped_quota() -> dict[str, int | None]:
    """MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG as .env.example, the compose default, the ConfigMap and the DEPLOYMENT.md marker
    give it (None where it is missing)."""
    name = "MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG"
    example = re.search(rf"^{name}=([0-9]+)$", (DEPLOY / ".env.example").read_text(encoding="utf-8"), re.M)
    env = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["mycelic"]["environment"]
    compose = re.fullmatch(rf"\$\{{{name}:-([0-9]+)\}}", str(env.get(name)))
    cm = next(d for d in _docs(DEPLOY / "k8s" / "mycelic-configmap.yaml") if d["kind"] == "ConfigMap")["data"].get(name)
    marker = re.search(rf"Shipped value: `{name}=([0-9]+)`", (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8"))
    return {"env.example": int(example.group(1)) if example else None, "compose": int(compose.group(1)) if compose else None,
            "configmap": int(cm) if isinstance(cm, str) and cm.isdigit() else None,
            "DEPLOYMENT.md": int(marker.group(1)) if marker else None}


class QuotaTests(unittest.IsolatedAsyncioTestCase):
    """MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG: a new note past the organization's limit is refused with 507, inside the write's
    transaction; resends and updates never are, and nothing the consumer applies is ever capped."""

    async def asyncSetUp(self) -> None:
        self.transport = HeldTransport()                      # the consumer receives nothing until released
        self.h = await ServiceHarness(transport=self.transport, max_active_memories_per_org=3).start()
        self.client = TestClient(TestServer(create_app(self.h.service)))
        await self.client.start_server()
        for agent in ("log-1", "log-2"):
            await self.h.register(agent, team="logistics")
        await self.h.register("acme-1", team="ops", enterprise="acme")

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.h.close()

    async def post(self, agent: str, key: str, **extra) -> tuple[int, dict]:
        body = {"text": f"note {key} by {agent}", "topic": "supply:sd-9/transport", "idempotency_key": key, **extra}
        r = await self.client.post("/memory", json=body, headers={"Authorization": f"Bearer {self.h.keys[agent]}"})
        return r.status, await r.json()

    async def events(self, agent: str, events: list[dict]) -> tuple[int, dict]:
        r = await self.client.post("/events", json={"events": events}, headers={"Authorization": f"Bearer {self.h.keys[agent]}"})
        return r.status, await r.json()

    def rows(self) -> tuple[int, int, int]:
        c = self.h.service.store._conn
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "memories", "audit_log"))

    async def test_org_quota_returns_507_and_is_per_org(self) -> None:
        s, refused = self.h.service, 0
        ids = []
        for i in range(3):
            status, body = await self.post("log-1", f"k{i}")
            self.assertEqual(status, 202, body)
            ids.append(body["memory_id"])
        status, body = await self.post("log-1", "k3")
        refused += 1
        self.assertEqual(status, 507)
        self.assertEqual(body["error"], "organization 'northwind' is at its limit of 3 active notes "
                                        "(MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG; 3 active now): retract notes it no longer "
                                        "needs (a retraction counts once it has been applied) or ask the operator to raise "
                                        "the limit")
        status, body = await self.post("log-1", "k0")
        self.assertEqual((status, body["created"], body["memory_id"]), (200, False, ids[0]), "a resend is never refused")
        self.assertEqual((await self.post("acme-1", "a0"))[0], 202, "another organization has its own count")
        # a retraction counts once it has been applied, not while it is on its way through the log
        r = await self.client.post(f"/memory/{ids[0]}/retract", json={}, headers={"Authorization": f"Bearer {self.h.keys['log-1']}"})
        self.assertEqual(r.status, 202)
        self.assertEqual((await self.post("log-2", "k4"))[0], 507)
        refused += 1
        self.transport.hold = False
        await self.h.settle()
        self.assertEqual((await self.post("log-2", "k4"))[0], 202)
        # five concurrent posts at one below the limit: exactly one is accepted
        r = await self.client.post(f"/memory/{ids[1]}/retract", json={}, headers={"Authorization": f"Bearer {self.h.keys['log-1']}"})
        self.assertEqual(r.status, 202)
        await self.h.settle()
        self.assertEqual(s.store.active_note_count("northwind"), 2)
        results = await asyncio.gather(*(self.post("log-2", f"c{i}") for i in range(5)))
        self.assertEqual(sorted(status for status, _ in results), [202, 507, 507, 507, 507])
        refused += 4
        await self.h.settle()
        # a batch that would pass the limit is refused whole: no event, memory or audit row
        r = await self.client.post(f"/memory/{ids[2]}/retract", json={}, headers={"Authorization": f"Bearer {self.h.keys['log-1']}"})
        self.assertEqual(r.status, 202)
        await self.h.settle()
        before = self.rows()
        status, body = await self.events("log-2", [{"type": "seen", "memory": {"text": "batched 1", "topic": "supply:x/y"}},
                                                   {"type": "seen"},
                                                   {"type": "seen", "memory": {"text": "batched 2", "topic": "supply:x/y"}}])
        refused += 1
        self.assertEqual(status, 507)
        self.assertIn("2 active now, this batch adds 2", body["error"])
        self.assertEqual(self.rows(), before, "nothing of the refused batch was written")
        # embedded resends and plain events are never counted
        status, body = await self.events("log-2", [{"type": "seen", "idempotency_key": "e1", "memory": {"text": "embedded"}}])
        self.assertEqual(status, 202, body)
        self.assertEqual(s.store.active_note_count("northwind"), 3)
        status, body = await self.events("log-2", [{"type": "seen", "idempotency_key": "e1", "memory": {"text": "embedded"}},
                                                   {"type": "plain"}])
        self.assertEqual(status, 202, body)
        self.assertEqual(body["results"][0]["memory_created"], False)
        # an update replaces an active note, so it is accepted at the limit (and the count is back once it applies)
        status, body = await self.post("log-2", "k4-v2", supersedes=(await self.post("log-2", "k4"))[1]["memory_id"])
        self.assertEqual(status, 202, body)
        await self.h.settle()
        self.assertEqual(s.store.active_note_count("northwind"), 3)
        self.assertEqual(s.metrics.quota_rejections._value.get(), refused)
        r = await self.client.get("/metrics", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        self.assertIn(f"mycelic_quota_rejections_total {float(refused)}", await r.text())
        plan = s.store._conn.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) AS n FROM memories WHERE org_id=? AND "
                                     "operator='agent_observation' AND status='active'", ("northwind",)).fetchall()
        self.assertIn("USING COVERING INDEX idx_memories_org_op_status", " ".join(r["detail"] for r in plan))
        # lowering the limit deletes nothing; a rebuild from the log is never capped
        s.settings.max_active_memories_per_org = 1
        self.assertEqual((await self.post("log-1", "late"))[0], 507)
        self.assertEqual(s.store.active_note_count("northwind"), 3)
        log = [json.loads(payload) for _, payload, _, _ in self.transport._log]
        rebuilt = await rebuild(log, self.h.tmp.name, max_active_memories_per_org=1)
        try:
            self.assertEqual(rebuilt.store.active_note_count("northwind"), 3)
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()

    def test_quota_shipped_default_matches_docs(self) -> None:
        values = shipped_quota()
        self.assertNotIn(None, values.values(), values)
        self.assertEqual(len(set(values.values())), 1, values)
        self.assertEqual(Settings.max_active_memories_per_org, 0, "the code default is off; the shipped files set the cap")


# ---------------------------------------------------------------------------------------------------------- rollback


class RollbackTests(unittest.IsolatedAsyncioTestCase):
    """What rolling back to this release from a later one relies on: a stream that later release wrote applies here.
    Unknown kinds are ignored (marked applied, counted), unknown fields are ignored, and fields a payload leaves out take
    defaults a rebuild reproduces; 0.1.0's payloads (no ``expires_at`` or ``attested_at``) apply too."""

    async def test_unknown_kinds_and_missing_optional_fields_apply_for_rollback(self) -> None:
        h = await ServiceHarness(event_signing_key=KEY_A).start()
        self.addAsyncCleanup(h.close)
        s = h.service

        async def send(event: dict) -> int:
            """Publish ``event`` signed with the node's key and let the consumer apply it; its stream sequence."""
            data = json.dumps(event).encode()
            seq = await s.transport.publish(subject_for(event["org_id"], event["kind"]), data, event["event_id"],
                                            headers=s._sign(data))
            await h.settle()
            self.assertEqual(s.store.get_event(event["event_id"]).status, "applied")
            return seq

        # registrations and a rule from a later release: extra fields, and schema 2 in the envelope
        for agent in ("log-1", "log-2"):
            await send({**wire("agent.registered", {"agent_id": agent, "org_id": ORG, "path": f"{TEAM}/{agent}", "key_hash": "x",
                                                    "status": "active", "pronouns": "it/its", "quota_class": "gold"}),
                        "schema": 2, "trace_id": "abc"})
        await send({**wire("rule.upserted", {**DEMO_RULE, "priority": 7, "explain": {"style": "short"}}), "schema": 2})
        self.assertEqual(s.store.get_agent("log-1").path, f"{TEAM}/log-1")
        self.assertEqual(s.store.get_rule(DEMO_RULE["rule_id"]).min_agents, DEMO_RULE["min_agents"])
        # an unknown kind: applied without effect, counted, and the position moves past it
        state = (memory_history(s.store), agent_state(s), s.store.list_rules(enabled_only=False))
        seq = await send({**wire("memory.reclassified", {"memory_id": "mem_x", "class": "b"}), "schema": 2})
        self.assertEqual(s.store.get_meta("last_applied_seq"), str(seq))
        self.assertEqual(s.metrics.events_ignored.labels("unknown_kind")._value.get(), 1)
        self.assertEqual((memory_history(s.store), agent_state(s), s.store.list_rules(enabled_only=False)), state)
        # a note with only the fields the apply requires, and one in the shape 0.1.0 published (no expires_at, attested_at)
        topic = "supply:sd-9/transport"
        await send(wire("memory.observed", {"memory_id": "mem_minimal", "org_id": ORG, "layer": "agent",
                                            "scope": f"{TEAM}/log-1", "text": "Terminal 3 strike announced.",
                                            "producer_id": "log-1", "topic": topic}))
        v010 = wire("memory.observed", {"memory_id": "mem_v010", "org_id": ORG, "layer": "agent", "scope": f"{TEAM}/log-2",
                                        "text": "Carrier ETA slipped by 12 days.", "topic": topic, "slot": None, "entity": None,
                                        "kind": "observation", "confidence": 0.7, "support": 1, "independent_teams": 1,
                                        "producer_id": "log-2", "operator": "agent_observation", "rule_id": None,
                                        "event_id": None, "visibility": "team", "status": "active", "superseded_by": None,
                                        "created_at": "2026-10-01T08:00:00+00:00", "applied_at": None, "source_event_ids": [],
                                        "local_ref": None, "metadata": {}, "embedding_hint": [0.1, 0.2]})
        v010["payload"]["event_id"] = v010["event_id"]
        await send(v010)
        minimal, old = s.store.get_memory("mem_minimal"), s.store.get_memory("mem_v010")
        self.assertEqual((minimal.kind, minimal.confidence, minimal.visibility, minimal.status, minimal.expires_at,
                          minimal.attested_at), ("observation", 0.5, "team", "active", None, None))
        carrier = s.store.get_event(minimal.event_id)
        self.assertEqual(carrier.payload["memory_id"], "mem_minimal", "the note's event defaults to the one that carried it")
        self.assertEqual(minimal.created_at, carrier.created_at, "and its time to that event's, which a rebuild reproduces")
        self.assertEqual((old.expires_at, old.attested_at, old.created_at), (None, None, "2026-10-01T08:00:00+00:00"))
        self.assertEqual(set(integrity_outcomes(s.store).values()), {"ok"})
        team = s.store.current_derived(ORG, "topic_consolidation", TEAM, topic)
        self.assertIsNotNone(team)
        self.assertEqual(sorted(team.metadata["roots"]), ["mem_minimal", "mem_v010"])
        report = await s.verify(h.admin, team.memory_id)
        self.assertEqual((report["verdict"], report["reasons"]), ("verified", []))
        # a rebuild of that stream reproduces every row, digest, edge and applied rule
        log = [json.loads(payload) for _, payload, _, _ in s.transport._log]
        rebuilt = await rebuild(log, h.tmp.name, event_signing_key=KEY_A)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()


# ---------------------------------------------------------------------------------------------------------- config


class ConfigTests(unittest.TestCase):
    def test_shutdown_timeout_setting(self) -> None:
        loopback = serve_env(MYCELIC_HOST="127.0.0.1")   # from_env validates: off loopback it would want an admin token
        with mock.patch.dict(os.environ, loopback, clear=True):
            self.assertEqual(Settings.from_env().shutdown_timeout_seconds, 10.0)
        self.assertEqual(Settings().shutdown_timeout_seconds, 10.0)
        with mock.patch.dict(os.environ, {**loopback, "MYCELIC_SHUTDOWN_TIMEOUT_SECONDS": "2.5"}, clear=True):
            self.assertEqual(Settings.from_env().shutdown_timeout_seconds, 2.5)
        for bad in ("0", "abc", "0.5", "-3", "nan", "inf"):
            with self.subTest(value=bad), mock.patch.dict(os.environ, {**loopback, "MYCELIC_SHUTDOWN_TIMEOUT_SECONDS": bad}, clear=True):
                with self.assertRaises(ConfigError) as cm:
                    Settings.from_env()
                self.assertIn("MYCELIC_SHUTDOWN_TIMEOUT_SECONDS", str(cm.exception))
        self.assertNotEqual(Settings().redacted()["shutdown_timeout_seconds"], "set", "not a secret")

    def test_new_settings_validate(self) -> None:
        loopback = serve_env(MYCELIC_HOST="127.0.0.1")
        with mock.patch.dict(os.environ, loopback, clear=True):
            s = Settings.from_env()
        self.assertEqual((s.replay_max_reject_ratio, s.max_active_memories_per_org), (0.01, 0))
        self.assertEqual((Settings().replay_max_reject_ratio, Settings().max_active_memories_per_org), (0.01, 0))
        for name, raw, attr, value in (("MYCELIC_REPLAY_MAX_REJECT_RATIO", "0", "replay_max_reject_ratio", 0.0),
                                       ("MYCELIC_REPLAY_MAX_REJECT_RATIO", "0.25", "replay_max_reject_ratio", 0.25),
                                       ("MYCELIC_REPLAY_MAX_REJECT_RATIO", "1", "replay_max_reject_ratio", 1.0),
                                       ("MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG", "0", "max_active_memories_per_org", 0),
                                       ("MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG", "5000", "max_active_memories_per_org", 5000)):
            with self.subTest(name=name, value=raw), mock.patch.dict(os.environ, {**loopback, name: raw}, clear=True):
                self.assertEqual(getattr(Settings.from_env(), attr), value)
        for name, raw in (("MYCELIC_REPLAY_MAX_REJECT_RATIO", "1.5"), ("MYCELIC_REPLAY_MAX_REJECT_RATIO", "-0.1"),
                          ("MYCELIC_REPLAY_MAX_REJECT_RATIO", "abc"), ("MYCELIC_REPLAY_MAX_REJECT_RATIO", "nan"),
                          ("MYCELIC_REPLAY_MAX_REJECT_RATIO", "inf"), ("MYCELIC_REPLAY_MAX_REJECT_RATIO", "-inf"),
                          ("MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG", "-1"), ("MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG", "2.5"),
                          ("MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG", "lots")):
            with self.subTest(name=name, value=raw), mock.patch.dict(os.environ, {**loopback, name: raw}, clear=True):
                with self.assertRaises(ConfigError) as cm:
                    Settings.from_env()
                self.assertIn(name, str(cm.exception))
        with self.assertRaises(ConfigError) as cm:
            Settings(host="127.0.0.1", replay_max_reject_ratio=1.01).validate()
        self.assertEqual(str(cm.exception), "MYCELIC_REPLAY_MAX_REJECT_RATIO must be between 0 and 1")
        redacted = Settings().redacted()
        self.assertEqual((redacted["replay_max_reject_ratio"], redacted["max_active_memories_per_org"]), (0.01, 0), "not secrets")


# ---------------------------------------------------------------------------------------------------------- docs


class DocsTests(unittest.TestCase):
    def test_security_md_has_no_known_false_statements(self) -> None:
        text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertNotIn("rotate both secrets together", flat)
        self.assertNotIn("an unknown agent id costs the same as a wrong secret", flat)
        reporting = [" ".join(p.split()) for p in text.split("\n\n") if "report" in p.lower() and "vulnerab" in p.lower()]
        self.assertEqual(len(reporting), 1, reporting)
        self.assertIn("private vulnerability reporting", reporting[0])
        self.assertIn("not enabled", reporting[0])
        self.assertIn("must enable it", reporting[0])
        self.assertIn("backup-critical", flat, "the signing key is state, not a rotatable secret")
        self.assertNotIn("verifies with a single key", flat)
        self.assertNotIn("until key rotation with a keyring is supported", flat)
        self.assertIn("MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS", flat, "rotation through previous keys is documented")
        # the backfill bound is read from the database, so it can be moved; the alarm must not depend on a second audit row
        self.assertNotIn("never re-signs a row inserted after the first row signed at insert", flat)
        self.assertNotIn("more than one `integrity.backfill` audit row", flat)
        self.assertIn("removing the digests of every row signed at insert up to and including a row", flat)
        self.assertIn("So in a database created at schema 4", flat)
        self.assertIn("alert on the log and the metric", flat)

    def test_deployment_md_documents_the_shutdown_budget_and_the_lock(self) -> None:
        text = " ".join((ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8").split())
        self.assertIn("| `SHUTDOWN_TIMEOUT_SECONDS` | `10` |", text)
        self.assertIn("configuration error: another Mycelic process is using", text)
        self.assertIn("Probes: startup `/health`, readiness `/ready`, liveness `/health`", text)

    def test_every_setting_has_a_configuration_row(self) -> None:
        names = sorted(set(re.findall(r'"MYCELIC_([A-Z_]+)"', (ROOT / "mycelic" / "config.py").read_text(encoding="utf-8"))))
        self.assertIn("MAX_ACTIVE_MEMORIES_PER_ORG", names)
        section = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8").split("## 3. Configuration reference", 1)[1].split("### 3a.", 1)[0]
        first_cells = " ".join(line.split("|")[1] for line in section.splitlines() if line.startswith("| `"))
        for name in names:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", first_cells)

    def test_g9_docs(self) -> None:
        deployment = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        flat = " ".join(deployment.split())
        self.assertIn("\n### 4a. Rollback\n", deployment)
        rollback = deployment.split("### 4a. Rollback", 1)[1].split("\n## 5.", 1)[0]
        flat_rollback = " ".join(rollback.split())
        self.assertIn("database schema 6 is newer than this code", flat_rollback)
        for phrase in ("mycelic_mycelic-data", "mycelic_nats-data", "alpine tar czf", "tar xzf /b/mycelic-data.tgz", "0.1.0",
                       "IntegrityError", "unknown_kind"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, flat_rollback)
        # G8's text (restore the database and let 0.1.0 replay the stream) is gone
        self.assertNotIn("**Rolling back and forward again.**", flat)
        self.assertNotIn("**What a rollback loses.**", flat)
        self.assertNotIn("start the earlier image on it. It re-delivers from the stream", flat)
        troubleshooting = deployment.split("## 7. Troubleshooting", 1)[1]
        for phrase in ('"reason": "signature_rejections', "507", "is at its limit of", "database schema 6 is newer than this code",
                       "another Mycelic process"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, troubleshooting)
        self.assertIn("**Signing-key mistakes.**", flat)
        self.assertIn("**Volume cap.**", flat)
        security = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
        for phrase in ("signature_rejections", "MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG", "MYCELIC_REPLAY_MAX_REJECT_RATIO",
                       "recovery.signature_rejections", "507"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, security)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for line in readme.splitlines():
            if "NATS_PASSWORD" in line and "openssl rand -hex" in line:
                with self.subTest(line=line):
                    self.assertRegex(line, r"n\$\(openssl rand -hex 32\)", "the NATS password must start with a letter")
        architecture = (ROOT / "docs" / "MYCELIC_ARCHITECTURE.md").read_text(encoding="utf-8")
        for name in ("mycelic_quota_rejections_total", 'mycelic_recovery_total{kind="replay_signature_rejections"}'):
            with self.subTest(metric=name):
                self.assertIn(name, architecture)


if __name__ == "__main__":
    unittest.main()
