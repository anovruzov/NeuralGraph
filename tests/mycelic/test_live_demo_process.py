"""The live demo engine: a headless recording against the real stack, the replay pages built from a trace, and
the console server (SSE, control, preflight, cleanup).

The recording and interrupt tests run ``demo/live/mycelic_live.py`` with the process driver (skipped without
nats-server, like the other process tests); the page and server tests need no stack.
"""
from __future__ import annotations

import http.client
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

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "demo" / "live" / "mycelic_live.py"
COMMITTED_TRACE = ROOT / "demo" / "live" / "trace.json"
NATS_BIN = os.environ.get("MYCELIC_NATS_SERVER_BIN") or shutil.which("nats-server")
SKELETON_TAGS = re.compile(r"<!doctype|<html[\s>]|</html>|<head[\s>]|</head>|<body[\s>]|</body>", re.IGNORECASE)
EVENT_TYPES = {"service", "agent_registered", "note_shared", "note_private", "derived", "status_change", "retracted", "query",
               "lineage", "rebuild_progress", "metric", "check", "act_start", "act_end", "done"}


def embedded_trace(page: str) -> dict:
    m = re.search(r'<script id="mycelic-trace" type="application/json">(.*?)</script>', page, re.DOTALL)
    assert m is not None, "no embedded trace"
    return json.loads(m.group(1))


def secret_findings(text: str) -> list[str]:
    """Anything in a trace that looks like a credential: an agent key, a 64-hex token, or a credential-named field."""
    found = []
    if "mk_" in text:
        found.append("agent api key prefix mk_")
    if re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text):
        found.append("64-hex token (admin token / signing key)")
    for key in ("api_key", "admin_token", "password", "signing_key", "Authorization"):
        if f'"{key}"' in text:
            found.append(f"field {key}")
    return found


class TracePageTests(unittest.TestCase):
    """No stack needed: pages built from the committed trace."""

    def setUp(self) -> None:
        if not COMMITTED_TRACE.exists():
            self.skipTest("demo/live/trace.json has not been recorded")
        self.tmp = Path(tempfile.mkdtemp(prefix="mycelic-live-page-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def export(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args, "--trace", str(COMMITTED_TRACE)], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_committed_trace_is_complete_and_clean(self) -> None:
        text = COMMITTED_TRACE.read_text(encoding="utf-8")
        self.assertLess(len(text), 2_000_000)
        self.assertEqual(secret_findings(text), [])
        tr = json.loads(text)
        self.assertEqual(tr["schema"], "mycelic-demo-trace/1")
        self.assertTrue(tr["meta"]["fictional"])
        self.assertTrue(tr["checks"] and all(c["ok"] for c in tr["checks"]), [c for c in tr["checks"] if not c["ok"]])
        self.assertEqual(tr["summary"]["checks_passed"], tr["summary"]["checks_total"])
        self.assertLessEqual({e["type"] for e in tr["events"]}, EVENT_TYPES)
        times = [e["t"] for e in tr["events"]]
        self.assertEqual(times, sorted(times))

    def test_fragment_has_no_skeleton_and_starts_with_the_title(self) -> None:
        out = self.tmp / "fragment.html"
        r = self.export("--export-fragment", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        page = out.read_text(encoding="utf-8")
        self.assertIsNone(SKELETON_TAGS.search(page), SKELETON_TAGS.search(page))
        self.assertTrue(page.lstrip().startswith("<title>"), page[:80])
        self.assertIn("</title>", page)
        self.assertEqual(embedded_trace(page)["summary"], json.loads(COMMITTED_TRACE.read_text())["summary"])

    def test_standalone_page_has_skeleton_and_trace(self) -> None:
        out = self.tmp / "page.html"
        r = self.export("--export", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        page = out.read_text(encoding="utf-8")
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn('<meta charset="utf-8">', page)
        self.assertIn('name="viewport"', page)
        self.assertLess(page.index("<title>"), page.index("</head>"), "the fragment's title belongs in <head>")
        self.assertLess(page.index("<style>"), page.index("</head>"))
        self.assertEqual(len(embedded_trace(page)["events"]), len(json.loads(COMMITTED_TRACE.read_text())["events"]))


class _SSE:
    """A minimal Server-Sent Events reader over http.client."""

    def __init__(self, port: int, last_event_id: int | None = None) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        self.conn.request("GET", "/events", headers=headers)
        self.resp = self.conn.getresponse()

    def messages(self, n: int) -> list[dict[str, Any]]:
        """The next ``n`` messages (comments skipped): {"event", "id", "data"}."""
        out: list[dict[str, Any]] = []
        msg: dict[str, Any] = {}
        while len(out) < n:
            line = self.resp.fp.readline().decode("utf-8").rstrip("\n")
            if line.startswith(":"):
                continue
            if not line:
                if msg:
                    out.append(msg)
                    msg = {}
                continue
            key, _, value = line.partition(": ")
            msg[key] = value if key != "data" else json.loads(value)
        return out

    def close(self) -> None:
        self.conn.close()


class ConsoleServerTests(unittest.TestCase):
    """No stack needed: the HTTP side of the console against a recorder with a few events."""

    def setUp(self) -> None:
        from demo.live import mycelic_live as live
        self.live = live
        self.rec = live.TraceRecorder(mode="present", driver="process", poll=0.25, fast_poll=0.02, rules_source="test")
        for i in range(3):
            self.rec.emit("metric", n=i)
        self.busy = False
        self.refusal: str | None = None
        self.control: Any = live.queue.Queue()
        self.server = live.ConsoleServer("127.0.0.1", 0, recorder=self.rec, control=self.control,
                                         busy=lambda: self.busy, refuse=lambda: self.refusal)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.port = self.server.httpd.server_address[1]

    def post(self, body: bytes) -> tuple[int, dict[str, Any]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", "/control", body=body, headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            return r.status, json.loads(r.read())
        finally:
            conn.close()

    def test_snapshot_first_then_events_with_their_index(self) -> None:
        sse = _SSE(self.port)
        self.addCleanup(sse.close)
        snap, *events = sse.messages(4)
        self.assertEqual(snap["event"], "snapshot")
        self.assertNotIn("events", snap["data"])
        self.assertEqual(snap["data"]["meta"]["run_id"], self.rec.trace["meta"]["run_id"])
        self.assertEqual([e["id"] for e in events], ["0", "1", "2"])
        self.assertEqual([e["data"]["n"] for e in events], [0, 1, 2])
        self.rec.emit("metric", n=3)                                     # new events follow as they happen
        (live,) = sse.messages(1)
        self.assertEqual((live["id"], live["data"]["n"]), ("3", 3))

    def test_last_event_id_resumes_and_an_id_past_the_end_starts_over(self) -> None:
        resumed = _SSE(self.port, last_event_id=1)
        self.addCleanup(resumed.close)
        _, first = resumed.messages(2)
        self.assertEqual(first["id"], "2")
        stale = _SSE(self.port, last_event_id=200)                        # a tab left open across a restart
        self.addCleanup(stale.close)
        _, first = stale.messages(2)
        self.assertEqual(first["id"], "0")

    def test_control_replies(self) -> None:
        self.assertEqual(self.post(b"not json")[0], 400)
        self.assertEqual(self.post(b"[1, 2]")[0], 400)
        self.assertEqual(self.post(b'{"action": "jump"}')[0], 400)
        self.busy = True
        status, body = self.post(b'{"action": "next"}')
        self.assertEqual((status, body.get("busy")), (200, True))
        self.busy = False
        self.refusal = "the run is complete"
        status, body = self.post(b'{"action": "next"}')
        self.assertEqual((status, body["error"]), (409, "the run is complete"))
        self.refusal = None
        status, body = self.post(b'{"action": "next"}')
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.control.get_nowait(), "next")

    def test_replay_server_refuses_control_and_serves_the_trace(self) -> None:
        if not COMMITTED_TRACE.exists():
            self.skipTest("demo/live/trace.json has not been recorded")
        trace = json.loads(COMMITTED_TRACE.read_text(encoding="utf-8"))
        server = self.live.ConsoleServer("127.0.0.1", 0, trace=trace)
        server.start()
        self.addCleanup(server.stop)
        port = server.httpd.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.addCleanup(conn.close)
        conn.request("POST", "/control", body=b'{"action": "next"}')
        r = conn.getresponse()
        r.read()
        self.assertEqual(r.status, 409)
        conn.request("GET", "/")
        page = conn.getresponse().read().decode("utf-8")
        self.assertEqual(len(embedded_trace(page)["events"]), len(trace["events"]))


class PreflightTests(unittest.TestCase):
    def run_demo(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        base = {k: v for k, v in os.environ.items() if k != "MYCELIC_NATS_SERVER_BIN"}
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True, timeout=60,
                              env={**base, **env})

    def test_missing_nats_server_is_reported_with_the_replay_command(self) -> None:
        r = self.run_demo("--record", os.devnull, PATH="")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("nats-server not found", r.stderr)
        self.assertIn("--replay", r.stderr)

    def test_a_directory_is_not_a_nats_server(self) -> None:
        r = self.run_demo("--record", os.devnull, MYCELIC_NATS_SERVER_BIN=tempfile.gettempdir())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("nats-server not found at", r.stderr)

    def test_a_bad_trace_is_one_line_not_a_traceback(self) -> None:
        r = self.run_demo("--replay", "/nonexistent/trace.json", "--no-browser")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("cannot read the trace", r.stderr)


def _children(pid: int) -> set[int]:
    """Every live descendant of ``pid`` (from /proc)."""
    out: set[int] = set()
    parents = {pid}
    while parents:
        found = set()
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    stat = (d / "stat").read_text()
                except OSError:
                    continue
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
                if ppid in parents and int(d.name) not in out:
                    found.add(int(d.name))
        out |= found
        parents = found
    return out


def _alive(pid: int) -> bool:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@unittest.skipUnless(NATS_BIN, "nats-server binary not available (set MYCELIC_NATS_SERVER_BIN)")
class LiveRecordProcessTests(unittest.TestCase):
    def test_record_passes_every_check_and_exports(self) -> None:
        from demo.live.mycelic_live import main

        tmp = Path(tempfile.mkdtemp(prefix="mycelic-live-record-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        trace_path = tmp / "trace.json"
        self.assertEqual(main(["--record", str(trace_path), "--spread", "1"]), 0)
        text = trace_path.read_text(encoding="utf-8")
        self.assertEqual(secret_findings(text), [])
        tr = json.loads(text)
        self.assertEqual(tr["schema"], "mycelic-demo-trace/1")
        for key in ("meta", "org", "rules", "acts", "events", "checks", "summary"):
            self.assertIn(key, tr)
        self.assertEqual([a["index"] for a in tr["acts"]], list(range(9)))
        self.assertTrue(all(a["t0"] is not None and a["t1"] is not None for a in tr["acts"]))
        self.assertEqual({r["rule_id"] for r in tr["rules"]}, {"regional_supply_risk", "strategic_second_source"})
        self.assertEqual(len(tr["org"]["agents"]), tr["summary"]["agents"])
        checks = {c["id"]: c for c in tr["checks"]}
        self.assertTrue(all(c["ok"] for c in checks.values()), [c for c in checks.values() if not c["ok"]])
        for cid in ("private_stayed_local", "time_to_enterprise", "background_no_rule", "redacted_view", "rebuild_identical"):
            self.assertIn(cid, checks)
        types = {e["type"] for e in tr["events"]}
        self.assertLessEqual(types, EVENT_TYPES)
        self.assertLessEqual({"note_shared", "note_private", "derived", "lineage", "retracted", "rebuild_progress", "done"}, types)
        s = tr["summary"]
        self.assertEqual(s["background_rule_conclusions"], 0)
        self.assertGreater(s["notes_private"], 0)
        self.assertIsNotNone(s["time_to_enterprise_s"])
        # the private notes the agents kept are nowhere in what the service handed out
        private = {e["text"] for e in tr["events"] if e["type"] == "note_private"}
        shared_texts = [e["text"] for e in tr["events"] if e["type"] in ("note_shared", "derived")]
        self.assertFalse([p for p in private if any(p in t for t in shared_texts)])

        page = tmp / "page.html"
        r = subprocess.run([sys.executable, str(SCRIPT), "--export", str(page), "--trace", str(trace_path)], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(embedded_trace(page.read_text(encoding="utf-8"))["summary"], s)


    @unittest.skipUnless(Path("/proc").is_dir(), "needs /proc to list child processes")
    def test_sigterm_mid_act_leaves_no_process_behind(self) -> None:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "--no-browser", "--port", "0", "--spread", "4"], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        port = None
        deadline = time.monotonic() + 60
        while port is None and time.monotonic() < deadline:
            m = re.search(r"http://127\.0\.0\.1:(\d+)/", proc.stdout.readline())
            port = int(m.group(1)) if m else None
        self.assertIsNotNone(port, "the console URL was not printed")

        def post_next() -> dict[str, Any]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("POST", "/control", body=b'{"action": "next"}')
                return json.loads(conn.getresponse().read())
            finally:
                conn.close()

        def act_running(act: str) -> bool:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", "/trace")
                events = json.loads(conn.getresponse().read())["events"]
            finally:
                conn.close()
            return any(e["type"] == "act_start" and e["act"] == act for e in events)

        for act in ("organisation", "observe"):             # act 0 runs by itself; queue acts 1 and 2
            deadline = time.monotonic() + 90
            while not post_next().get("ok"):
                self.assertLess(time.monotonic(), deadline, "the engine never accepted the next act")
                time.sleep(0.3)
            while not act_running(act):
                self.assertLess(time.monotonic(), deadline, f"act {act} never started")
                time.sleep(0.2)
        time.sleep(1.5)                                       # agents are sharing now
        kids = _children(proc.pid)
        self.assertTrue(kids, "no nats-server/service/agent processes found")
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=60), 130)
        time.sleep(0.5)
        self.assertEqual([k for k in kids if _alive(k)], [], "processes left behind after SIGTERM")


if __name__ == "__main__":
    unittest.main()
