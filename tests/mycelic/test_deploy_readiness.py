"""Deploy safety: probes that never wait on the broker or the database, Kubernetes/Compose manifests that match
the service (probe timeouts, grace periods, the shipped rules), the single-writer lock on the SQLite file, a
SIGTERM that finishes inside the grace period, and SECURITY.md statements that are true of the code."""
from __future__ import annotations

import asyncio
import json
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
from pathlib import Path
from unittest import mock

import yaml
from aiohttp.test_utils import TestClient, TestServer

from mycelic.api import create_app, run_server
from mycelic.config import ConfigError, Settings
from mycelic.metrics import Metrics
from mycelic.service import MycelicService
from mycelic.store import DatabaseLocked, MycelicStore, acquire_db_lock, release_db_lock
from mycelic.transport import InProcessTransport, JetStreamTransport

from .helpers import ADMIN_TOKEN, ServiceHarness, settings

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


if __name__ == "__main__":
    unittest.main()
