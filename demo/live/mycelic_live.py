#!/usr/bin/env python
"""Mycelic live demo: a visual console that drives the real stack, and a replay of a recorded run.

    python demo/live/mycelic_live.py                         # start the stack, serve the console, open the browser
    python demo/live/mycelic_live.py --auto --dwell 8        # advance the acts by themselves, 8 s apart
    python demo/live/mycelic_live.py --record demo/live/trace.json      # headless: every act, write the trace
    python demo/live/mycelic_live.py --replay [demo/live/trace.json]    # serve a recorded run, no stack needed
    python demo/live/mycelic_live.py --export page.html [--trace demo/live/trace.json]      # standalone replay page
    python demo/live/mycelic_live.py --export-fragment page.fragment.html                    # same, no skeleton

Orrery Robotics, a fictional company (synthetic data), runs 40 AI agents in EMEA, APAC and head office
(``demo/live/scenario.py``).  The default mode (``--present``) checks the prerequisites, starts nats-server and
the Mycelic service (``--driver process``, or ``--driver compose``), serves the console at
``http://127.0.0.1:8765/`` and runs act 0; the presenter advances the next act from the console (``POST /control
{"action": "next"}``) or ``--auto`` does it every ``--dwell`` seconds.  Presenter notes open in a second window at
``http://127.0.0.1:8765/?presenter`` (it follows the same run), never on the projected page unless asked for (N).  Every act does real work against the
stack: agents are processes with their own keys and local memory files; the conclusions are Mycelic's.
Nothing shown is hard-coded: the engine polls the admin read API (memories by status, lineage, status) and
records what changed, with the time it saw it, plus the actions it takes itself.  If the stack cannot run,
``--replay`` (default: ``demo/live/trace.json``) serves a recorded run instead.  If a live run fails, the console
stays up with the error and the replay command, until Ctrl-C.

Acts: 0 cold open (service comes up) · 1 the organisation (agents register) · 2 agents observe (34 agents
share, everyone keeps private notes) · 3 each region concludes (procurement shares; ``regional_supply_risk``
is evaluated in EMEA and APAC) · 4 the enterprise puts together what no single team held (HQ shares;
``strategic_second_source`` is evaluated) · 5 every conclusion has a receipt (lineage, admin and redacted views) ·
6 strategy follows evidence (retraction, recount) · 7 pull the plug (SIGKILL, delete the database, rebuild the
memories, lineage and agents from JetStream; the audit log is not in the event log and is not rebuilt) · 8 close.

The demo's operator runs two rules, copied verbatim from ``deploy/mycelic/rules.json``: ``regional_supply_risk``
and ``strategic_second_source`` (with the compose driver the deployment's own rules file applies).

HTTP endpoints (live and replay)
--------------------------------
GET  /         the console (``demo/live/console.html`` wrapped in a ``<!doctype html>`` skeleton; in replay mode
               with the trace embedded)
GET  /trace    the trace so far (JSON, format below)
GET  /events   Server-Sent Events.  On connect: one ``event: snapshot`` message (the trace without ``events``),
               then every event so far, then new ones as they happen; each default message is one trace event
               as JSON with ``id: <index>`` (a reconnect with ``Last-Event-ID`` resumes after it).  Another
               ``snapshot`` is sent whenever org, rules, checks or summary change.  ``: ping`` every 15 s.
               A client whose ``Last-Event-ID`` is past the end of this run's events (a tab left open across a
               restart) gets every event from the start; the console also compares ``meta.run_id`` and reloads.
POST /control  ``{"action": "next"}`` runs the next act (live mode; ``busy`` while an act runs; ``409`` in replay,
               with ``--auto``, and once the run is complete; ``400`` for a body that is not a JSON object)

Console contract
----------------
``console.html`` is a *fragment*: ``<title>`` and ``<style>`` first, then markup and scripts, and no
doctype/html/head/body tags.  The server and ``--export`` wrap it in a skeleton with charset and viewport meta;
``--export-fragment`` does not (for publishers that add their own); the skeleton puts the fragment's leading
``<title>`` and ``<style>`` in ``<head>``.  The trace is injected as
``<script id="mycelic-trace" type="application/json">...</script>`` in place of the marker comment
``<!-- mycelic:trace -->`` (or before the first ``<script`` if the marker is missing), with every ``<`` in the
JSON escaped as ``\\u003c``.  With the tag present the console replays it; without it the console is live and
reads ``/trace`` and ``/events``.

Trace format (``schema: "mycelic-demo-trace/1"``)
--------------------------------------------------
::

    {"schema": "mycelic-demo-trace/1",
     "meta": {"recorded_at": ISO-8601 UTC, "mycelic_version", "driver", "nats_server_version", "python", "platform",
              "duration_s", "fictional": true, "company", "mode": "record"|"present", "poll_interval_s",
              "fast_poll_interval_s", "rules_source", "run_id": random per run,
              "story": {"entity", "label", "question"} (the scenario's subject, for the console's labels)},
     "org": {"enterprise": id,
             "units": [{"path", "layer", "label", "parent"}],          # enterprise, region, subsidiary, department, team
             "agents": [{"agent_id", "path", "team_path", "region", "label", "role": "core"|"background",
                         "stage": "observe"|"conclude"|"synthesise"}]},
     "rules": [{"rule_id", "target_layer", "required_slots", "emits_slot", "min_units", "min_agents", "min_teams",
                "conclusion", "topic_prefix", "sources", "corroborate", "kind", "owner"}],
     "acts": [{"id", "index", "title", "caption", "t0", "t1"}],
     "events": [{"t": seconds since run start, "act": act id, "type", ...payload}],   # ordered, t non-decreasing
     "checks": [{"id", "act", "text", "ok"}],
     "summary": {...}}

Events are in the order they were recorded and ``t`` never decreases.  Events the watcher produces carry the
time of the API read that first returned the change (or the previous event's time if that is later), so ``t``
of a ``note_shared`` or ``derived`` event is when it became readable, not when the engine got round to writing it.

Event types and payloads:

* ``service`` {state: starting|healthy|killed|db_wiped|rebuilding|ready, detail} (``ready`` in act 7 also carries
  rebuild_s and rebuild_events; it is stamped when the restarted service answered ``/ready``, i.e. replay done)
* ``agent_registered`` {agent_id, path, team_path, region, role, label}
* ``note_shared`` {agent_id, memory_id, team_path, region, role, topic, slot, entity, text, confidence, status}
  -- seen when the service accepted it (before it is applied)
* ``note_private`` {agent_id, text, local_only: true} -- read from the agent's own local state after it ran
* ``derived`` {memory_id, layer, scope, operator, rule_id, topic, slot, entity, kind, text, confidence, support,
  independent_teams, version_of, status, parents: [memory ids], roots: [agent note ids], corroborated_units}
* ``status_change`` {memory_id, layer, from, to, reason} -- ``from`` is null when the watcher first saw the memory
  already superseded or retracted (it never observed it active)
* ``retracted`` {agent_id, memory_id, reason} -- the agent withdrew its own note (the engine did it with its key)
* ``query`` {viewer, question, scope, min_layer, results: [{memory_id, layer, scope, text, score, rule_id, operator}]}
* ``lineage`` {viewer, memory_id, graph} -- ``graph`` exactly as ``GET /lineage/{id}`` returns it for that viewer
  (``viewer`` is ``"admin"`` or an agent id)
* ``rebuild_progress`` {applied, total}
* ``metric`` {memories_by_layer, memories_by_status, lineage_edges, registered_agents, stream_messages}
* ``check`` {id, text, ok}
* ``act_start`` / ``act_end`` (``act`` names it), ``done`` {} at the end, and ``error`` {message, hint} if a live run
  failed (one line; ``hint`` is the replay command)

``summary`` holds real counts from the run: agents, agents_core, agents_background, teams, notes_shared,
notes_private, memories_by_layer, memories_by_status, lineage_edges, stream_messages (at the end), rebuild_s
(restart to ready, including process start), rebuild_events (the log replayed), events_appended_on_restart (the
restarted service logs its rules file again, one event per rule, because a fresh database does not know them),
time_to_region_s {region: s}, time_to_enterprise_s, time_to_restore_s (each an upper bound on "the last note a
conclusion rests on accepted -> the conclusion readable": from the moment the last watcher read that did not return
that note was sent to the arrival of the first read that returned the conclusion, so the poller's own cadence, ``fast_poll_interval_s`` plus
the time a poll takes, is included), background_notes, background_consolidations, background_rule_conclusions,
strategy {memory_id, support, independent_teams, confidence, regions}, checks_passed, checks_total.  API keys,
the admin token, the NATS password and the signing key never enter the trace; a guard refuses to write one that
contains any of them.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import platform
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from demo.live.scenario import (COMPANY, ENTERPRISE, ENTITY, RECOUNT, STRATEGIC_QUERY, SUBJECT, LiveAgent,  # noqa: E402
                                LiveScenario, run_agents)
from mycelic.harness import RULES_FILE, ComposeDriver, Driver, ProcessDriver  # noqa: E402
from mycelic.hierarchy import LAYERS  # noqa: E402
from mycelic.sdk import MycelicClient, MycelicError  # noqa: E402
from tests.smoke.mycelic_smoke import wait_idle  # noqa: E402

HERE = Path(__file__).resolve().parent
CONSOLE = HERE / "console.html"
DEFAULT_TRACE = HERE / "trace.json"
SCHEMA = "mycelic-demo-trace/1"
TRACE_MARKER = "<!-- mycelic:trace -->"
DEMO_RULES = ("regional_supply_risk", "strategic_second_source")
STATUSES = ("active", "superseded", "retracted")
REPLAY_COMMAND = f"python {Path(__file__).resolve()} --replay --port 8766"
REPLAY_HINT = f"or, if you cannot run the stack, serve the recorded run: {REPLAY_COMMAND}"


def acts_for(sc: LiveScenario) -> list[dict[str, Any]]:
    """Act titles and captions; every number in a caption is counted from the scenario."""
    n, core, bg = len(sc.agents), len(sc.by_role("core")), len(sc.by_role("background"))
    observe = len(sc.stage("observe"))
    return [
        {"id": "cold-open", "title": "Every agent sees a sliver",
         "caption": "A company runs dozens of AI agents. Each sees a sliver. Who knows what they know?"},
        {"id": "organisation", "title": "The organisation",
         "caption": f"{n} agents across EMEA, APAC and head office. Each has its own key and its own local memory."},
        {"id": "observe", "title": "Agents observe",
         "caption": f"{observe} agents share notes; every agent keeps private notes on its own disk. "
                    "Mycelic rolls the shared ones up the org chart."},
        {"id": "regions", "title": "Each region concludes",
         "caption": "Procurement in each region shares its stock level. The operator-written rule is evaluated in EMEA "
                    "and in APAC, separately."},
        {"id": "enterprise", "title": "The enterprise puts together what no single team held",
         "caption": "Head office shares demand growth and supplier concentration. The strategy rule is evaluated at "
                    "the enterprise."},
        {"id": "receipts", "title": "Every conclusion has a receipt",
         "caption": "Ask for the strategy's lineage: who said what, when, how sure. Then the same lineage as another "
                    "agent sees it."},
        {"id": "evidence", "title": "Strategy follows evidence",
         "caption": "APAC finds its stock was counted twice and retracts. Watch what rested on those notes, then a "
                    "recount arrives."},
        {"id": "plug", "title": "Pull the plug",
         "caption": "Kill the service. Delete its database. Rebuild its memories, lineage and agents from the event log."},
        {"id": "close", "title": "Shared memory for organisations of AI agents",
         "caption": f"Verified checks and real totals from this run: {core} story agents, {bg} background agents, "
                    "no LLM in the data path."},
    ]


# ---------------------------------------------------------------------------------------------------- trace
class TraceRecorder:
    """The trace being recorded, plus a condition variable the SSE handlers wait on."""

    def __init__(self, *, mode: str, driver: str, poll: float, fast_poll: float, rules_source: str) -> None:
        self._t0 = time.monotonic()
        self._cv = threading.Condition()
        self.act: str | None = None
        self.done = False
        self.revision = 0
        self.trace: dict[str, Any] = {
            "schema": SCHEMA,
            "meta": {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mycelic_version": None,
                     "driver": driver, "nats_server_version": None, "python": platform.python_version(),
                     "platform": platform.platform(), "duration_s": None, "fictional": True, "company": COMPANY,
                     "mode": mode, "poll_interval_s": poll, "fast_poll_interval_s": fast_poll, "rules_source": rules_source,
                     "run_id": secrets.token_hex(6),
                     "story": {"entity": ENTITY, "label": SUBJECT, "question": STRATEGIC_QUERY}},
            "org": {"enterprise": ENTERPRISE, "units": [], "agents": []},
            "rules": [], "acts": [], "events": [], "checks": [], "summary": {},
        }

    def now(self) -> float:
        return round(time.monotonic() - self._t0, 3)

    def emit(self, type_: str, *, at: float | None = None, **payload: Any) -> dict[str, Any]:
        """Append one event; ``at`` backdates it to when it was observed (the watcher's read), else it is now."""
        with self._cv:
            t = self.now() if at is None else at
            if self.trace["events"]:
                t = max(t, self.trace["events"][-1]["t"])      # keep the event list ordered by time
            ev = {"t": t, "act": self.act, "type": type_, **payload}
            self.trace["events"].append(ev)
            self._cv.notify_all()
        return ev

    def update(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Change a non-event part of the trace (org, rules, meta, summary); SSE clients get a new snapshot."""
        with self._cv:
            fn(self.trace)
            self.revision += 1
            self._cv.notify_all()

    def act_start(self, index: int) -> None:
        act = self.trace["acts"][index]
        with self._cv:
            self.act = act["id"]
            act["t0"] = self.now()
            self.revision += 1
        self.emit("act_start")

    def act_end(self, index: int) -> None:
        self.emit("act_end")
        self.update(lambda tr: tr["acts"][index].__setitem__("t1", self.now()))

    def check(self, check_id: str, text: str, ok: bool) -> bool:
        ok = bool(ok)
        self.update(lambda tr: tr["checks"].append({"id": check_id, "act": self.act, "text": text, "ok": ok}))
        self.emit("check", id=check_id, text=text, ok=ok)
        return ok

    def finish(self) -> None:
        self.update(lambda tr: tr["meta"].__setitem__("duration_s", self.now()))
        self.emit("done")
        with self._cv:
            self.done = True
            self._cv.notify_all()

    def snapshot(self, *, events: bool = True) -> dict[str, Any]:
        with self._cv:
            tr = {k: v for k, v in self.trace.items() if events or k != "events"}
            return json.loads(json.dumps(tr, default=str))

    def wait_events(self, after: int, revision: int, timeout: float) -> tuple[list[dict[str, Any]], int]:
        """Events with index > ``after`` and the current revision, waiting up to ``timeout`` for either to move."""
        with self._cv:
            self._cv.wait_for(lambda: len(self.trace["events"]) > after + 1 or self.revision != revision, timeout)
            return [dict(e) for e in self.trace["events"][after + 1:]], self.revision


def html_json(data: Any) -> str:
    """JSON that is safe inside a <script> element: no '<' survives, so neither '</script>' nor '<!--' can."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def assert_no_secrets(text: str, secrets: list[str]) -> None:
    leaked = [s[:6] + "…" for s in secrets if s and s in text]
    if leaked or "mk_" in text:
        raise RuntimeError(f"refusing to write a trace that contains a secret ({len(leaked)} found, api-key prefix: {'mk_' in text})")


# ---------------------------------------------------------------------------------------------------- page
def inject_trace(fragment: str, trace: dict[str, Any]) -> str:
    tag = f'<script id="mycelic-trace" type="application/json">{html_json(trace)}</script>'
    if TRACE_MARKER in fragment:
        return fragment.replace(TRACE_MARKER, tag, 1)
    i = fragment.find("<script")
    return fragment[:i] + tag + "\n" + fragment[i:] if i >= 0 else fragment + "\n" + tag


def build_page(trace: dict[str, Any] | None, *, fragment: bool = False, console: Path = CONSOLE) -> str:
    """The console page: the fragment, with the trace embedded when given, wrapped in a skeleton unless ``fragment``."""
    body = console.read_text(encoding="utf-8")
    if trace is not None:
        body = inject_trace(body, trace)
    if fragment:
        return body
    head = ""
    m = re.match(r"\s*<title>.*?</title>\s*<style>.*?</style>\s*", body, re.DOTALL)   # the fragment's leading title + style
    if m:
        head, body = m.group(0).strip() + "\n", body[m.end():]
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + head + '</head>\n<body>\n'
            + body + "\n</body>\n</html>\n")


class _QuietHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        if not isinstance(sys.exc_info()[1], ConnectionError):   # a browser tab closing mid-stream is not an error
            super().handle_error(request, client_address)


class ConsoleServer:
    """GET / (console), GET /trace, GET /events (SSE), POST /control, on a ThreadingHTTPServer."""

    def __init__(self, host: str, port: int, *, recorder: TraceRecorder | None = None, trace: dict[str, Any] | None = None,
                 control: "queue.Queue[str] | None" = None, busy: Callable[[], bool] = lambda: False,
                 refuse: Callable[[], str | None] = lambda: None) -> None:
        """``refuse`` returns why ``POST /control`` cannot queue an act right now (``--auto``, run complete), or None."""
        self.recorder, self.static_trace, self.control, self.busy, self.refuse = recorder, trace, control, busy, refuse
        self.stopping = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # quiet: the terminal belongs to the presenter
                pass

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, data: Any, status: int = 200) -> None:
                self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    page = build_page(server.static_trace)
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/trace":
                    self._json(server.static_trace if server.static_trace is not None else server.recorder.snapshot())
                elif path == "/events":
                    self._events()
                elif path == "/favicon.ico":
                    self._send(204, b"", "image/x-icon")
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0] != "/control":
                    return self._json({"error": "not found"}, 404)
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    return self._json({"ok": False, "error": "body must be JSON"}, 400)
                if not isinstance(body, dict):
                    return self._json({"ok": False, "error": "body must be a JSON object"}, 400)
                if server.control is None:
                    return self._json({"ok": False, "error": "replay mode: the console advances by itself"}, 409)
                reason = server.refuse()
                if reason:
                    return self._json({"ok": False, "error": reason}, 409)
                if body.get("action") != "next":
                    return self._json({"ok": False, "error": "unknown action; expected {\"action\": \"next\"}"}, 400)
                if server.busy() or not server.control.empty():
                    return self._json({"ok": False, "busy": True, "act": server.recorder.act})
                server.control.put("next")
                return self._json({"ok": True, "queued": "next", "act": server.recorder.act})

            def _events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    last = int(self.headers.get("Last-Event-ID", "-1"))
                except ValueError:
                    last = -1
                n_events = len(server.static_trace["events"] if server.static_trace is not None else server.recorder.trace["events"])
                if last >= n_events:
                    last = -1                                  # an id from another run (a tab left open across a restart)
                try:
                    if server.static_trace is not None:
                        self._snapshot({k: v for k, v in server.static_trace.items() if k != "events"})
                        for i, ev in enumerate(server.static_trace["events"]):
                            if i > last:
                                self._message(i, ev)
                        while not server.stopping.wait(15):
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                        return
                    rec = server.recorder
                    revision = -1
                    while not server.stopping.is_set():
                        events, rev = rec.wait_events(last, revision, 15.0)
                        if rev != revision:
                            revision = rev
                            self._snapshot(rec.snapshot(events=False))
                        for ev in events:
                            last += 1
                            self._message(last, ev)
                        if not events:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def _snapshot(self, snap: dict[str, Any]) -> None:
                self.wfile.write(b"event: snapshot\ndata: " + json.dumps(snap, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()

            def _message(self, index: int, ev: dict[str, Any]) -> None:
                self.wfile.write(f"id: {index}\ndata: ".encode() + json.dumps(ev, ensure_ascii=False).encode("utf-8") + b"\n\n")

        self.httpd = _QuietHTTPServer((host, port), Handler)
        self.url = f"http://{host}:{self.httpd.server_address[1]}/"
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="mycelic-live-http", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------------------------------- watching
def list_memories(client: MycelicClient, status: str) -> list[dict[str, Any]]:
    # the SDK's list_memories has no status filter; GET /memories accepts ?status=
    return client._request("GET", "/memories", params={"scope": ENTERPRISE, "status": status, "limit": 500})["memories"]


def all_memories(client: MycelicClient) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for status in STATUSES:
        rows = list_memories(client, status)
        if len(rows) >= 500:
            print(f"warning: {status} memories hit the 500-row page limit; the watcher may miss some", file=sys.stderr)
        out.update({m["memory_id"]: m for m in rows})
    return out


def metric_payload(st: dict[str, Any]) -> dict[str, Any]:
    s = st.get("stats") or {}
    return {"memories_by_layer": s.get("memories_by_layer"), "memories_by_status": s.get("memories_by_status"),
            "lineage_edges": s.get("lineage_edges"), "registered_agents": s.get("registered_agents"),
            "stream_messages": st["checks"]["transport"].get("stream_messages")}


class MemoryWatcher:
    """Polls the admin read API and turns every change into a trace event, stamped with the read that saw it.

    Each poll reads the active memories; every ``FULL_EVERY``-th poll while agents share (every poll otherwise)
    also reads the superseded and retracted ones, so a version that lived between two polls is still recorded.
    For each memory the watcher keeps when it was first seen and when it was last known to be absent, which is
    what :meth:`LiveDemo.latency` turns into an upper bound on "note accepted -> conclusion readable".
    """

    FULL_EVERY = 10

    def __init__(self, base_url: str, admin_token: str, rec: TraceRecorder, sc: LiveScenario, *, interval: float,
                 fast_interval: float) -> None:
        self.client = MycelicClient(base_url, admin_token, retries=0, timeout=10)
        self.rec, self.sc = rec, sc
        self.interval, self.fast_interval = interval, fast_interval
        self.fast = False
        self._wake = threading.Event()
        self.known: dict[str, dict[str, Any]] = {}      # memory_id -> {"status", "layer"} as last seen
        self.first_seen: dict[str, float] = {}          # memory_id -> time of the first read that returned it
        self.absent_at: dict[str, float | None] = {}    # memory_id -> when the last read without it was sent
        self._last_sent: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._last_metric: dict[str, Any] | None = None
        self._last_metric_at = 0.0
        self._thread = threading.Thread(target=self._run, name="mycelic-live-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def pause(self) -> None:
        self._paused.set()
        with self._lock:                                 # wait for a poll in flight
            pass

    def resume(self) -> None:
        self._paused.clear()

    def set_fast(self, fast: bool) -> None:
        """Poll every ``fast_interval`` (agents are sharing) or every ``interval``; switching wakes the poller."""
        self.fast = fast
        self._wake.set()

    def _run(self) -> None:
        n = 0
        while not self._stop.is_set():
            if not self._paused.is_set():
                try:
                    self.poll_once(full=not self.fast or n % self.FULL_EVERY == 0)
                    n += 1
                    if not self.fast and time.monotonic() - self._last_metric_at > 1.0:
                        self.metric()
                except (MycelicError, OSError, KeyError):
                    pass                                 # a restart in progress; the next poll catches up
            self._wake.wait(self.fast_interval if self.fast else self.interval)
            self._wake.clear()

    def metric(self, *, force: bool = False) -> None:
        payload = metric_payload(self.client.status())
        self._last_metric_at = time.monotonic()
        if force or payload != self._last_metric:
            self._last_metric = payload
            self.rec.emit("metric", **payload)

    def poll_once(self, *, full: bool = True) -> None:
        with self._lock:
            if self._paused.is_set():
                return
            sent_at = self.rec.now()                     # before the request: the snapshot is at or after this
            rows = {m["memory_id"]: m for m in list_memories(self.client, "active")}
            read_at = self.rec.now()                     # after the response: the snapshot is at or before this
            if full:
                for status in STATUSES[1:]:
                    rows.update({m["memory_id"]: m for m in list_memories(self.client, status)})
            before, self._last_sent = self._last_sent, sent_at
            new = sorted((m for mid, m in rows.items() if mid not in self.known),
                         key=lambda m: (LAYERS.index(m["layer"]), m.get("applied_at") or m.get("created_at") or "", m["memory_id"]))
            for m in new:
                self.first_seen[m["memory_id"]] = read_at
                self.absent_at[m["memory_id"]] = before
            for m in new:
                if m["layer"] == "agent":
                    self._note(m, read_at)
                else:
                    self._derived(m, read_at)
                self.known[m["memory_id"]] = {"status": "active", "layer": m["layer"]}
                if m["status"] != "active":             # first seen already superseded/retracted: never observed active
                    self._status(m, None, read_at)
            for mid, m in rows.items():
                if self.known[mid]["status"] != m["status"]:
                    self._status(m, self.known[mid]["status"], read_at)

    def _status(self, m: dict[str, Any], before: str | None, at: float) -> None:
        self.rec.emit("status_change", at=at, memory_id=m["memory_id"], layer=m["layer"], **{"from": before, "to": m["status"]},
                      reason=(m.get("metadata") or {}).get("status_reason"))
        self.known[m["memory_id"]]["status"] = m["status"]

    def _note(self, m: dict[str, Any], at: float) -> None:
        a = self.sc.agent(m["producer_id"])
        self.rec.emit("note_shared", at=at, agent_id=a.agent_id, memory_id=m["memory_id"], team_path=a.team_path, region=a.region,
                      role=a.role, topic=m["topic"], slot=m["slot"], entity=m["entity"], text=m["text"],
                      confidence=m["confidence"], status=m["status"])

    def _derived(self, m: dict[str, Any], at: float) -> None:
        meta = m.get("metadata") or {}
        g = self.client.lineage(m["memory_id"])
        parents = sorted(e["parent"] for e in g["edges"] if e["child"] == m["memory_id"])
        self.rec.emit("derived", at=at, memory_id=m["memory_id"], layer=m["layer"], scope=m["scope"], operator=m["operator"],
                      rule_id=m["rule_id"], topic=m["topic"], slot=m["slot"], entity=m["entity"], kind=m["kind"],
                      text=m["text"], confidence=m["confidence"], support=m["support"],
                      independent_teams=m["independent_teams"], version_of=meta.get("version_of"), status=m["status"],
                      parents=parents, roots=g["roots"], corroborated_units=meta.get("corroborated_units") or None)


# ---------------------------------------------------------------------------------------------------- the story
class DemoProcessDriver(ProcessDriver):
    """The process driver with the demo's rules file: the two rules of the story, verbatim from rules.json."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        rules = [r for r in json.loads(RULES_FILE.read_text(encoding="utf-8"))["rules"] if r["rule_id"] in DEMO_RULES]
        self.rules_file = self.workdir / "demo-rules.json"
        self.rules_file.write_text(json.dumps({"rules": rules}, indent=1), encoding="utf-8")

    def env(self) -> dict[str, str]:
        env = super().env()
        env["MYCELIC_RULES_FILE"] = str(self.rules_file)
        return env


def leaf(scope: str) -> str:
    return scope.rsplit("/", 1)[-1]


def region_hits(scopes: set[str]) -> str:
    """The regions a search returned conclusions from, as a check sentence reads them: "EMEA only", "none"."""
    names = sorted(leaf(s).upper() for s in scopes)
    if not names:
        return "none"
    return f"{names[0]} only" if len(names) == 1 else " and ".join(names)


def query_payload(res: dict[str, Any], viewer: str, question: str, scope: str, min_layer: str) -> dict[str, Any]:
    return {"viewer": viewer, "question": question, "scope": scope, "min_layer": min_layer,
            "results": [{"memory_id": h["memory"]["memory_id"], "layer": h["memory"]["layer"], "scope": h["memory"]["scope"],
                         "text": h["memory"]["text"], "score": h["score"], "rule_id": h["memory"]["rule_id"],
                         "operator": h["memory"]["operator"]} for h in res["results"]]}


class LiveDemo:
    """Runs the acts against a real deployment and records what happened."""

    def __init__(self, driver: Driver, rec: TraceRecorder, workdir: Path, *, spread: float, poll: float,
                 fast_poll: float, log: Callable[[str], None] = print) -> None:
        self.driver, self.rec, self.workdir, self.log = driver, rec, workdir, log
        self.poll, self.fast_poll = poll, fast_poll
        self.sc = LiveScenario(workdir / "agents", spread=spread).build()
        self.acts: list[Callable[[], None]] = [self.cold_open, self.organisation, self.observe, self.regions, self.enterprise,
                                               self.receipts, self.evidence, self.plug, self.close]
        self.admin: MycelicClient | None = None
        self.watcher: MemoryWatcher | None = None
        self.states: dict[str, dict[str, Any]] = {}
        self.regional: dict[str, dict[str, Any]] = {}
        self.strategy: dict[str, Any] | None = None
        self.numbers: dict[str, Any] = {"time_to_region_s": {}}
        acts = acts_for(self.sc)
        rec.update(lambda tr: tr.update(acts=[{**a, "index": i, "t0": None, "t1": None} for i, a in enumerate(acts)],
                                        org={"enterprise": ENTERPRISE, "units": self.sc.units(), "agents": [
                                            {"agent_id": a.agent_id, "path": a.path, "team_path": a.team_path,
                                             "region": a.region, "label": a.label, "role": a.role, "stage": a.stage}
                                            for a in self.sc.agents]}))

    # ------------------------------------------------------------------ plumbing
    def secrets(self) -> list[str]:
        d = self.driver
        return [d.admin_token, getattr(d, "signing_key", ""), getattr(d, "nats_password", "")] + [a.api_key for a in self.sc.agents]

    def run_act(self, index: int) -> None:
        act = self.rec.trace["acts"][index]
        self.log(f"\n=== act {index}: {act['title']}")
        self.rec.act_start(index)
        self.acts[index]()
        self.rec.act_end(index)

    def check(self, check_id: str, text: str, ok: bool) -> bool:
        self.log(f"  {'ok ' if ok else 'FAIL'} {text}")
        return self.rec.check(check_id, text, ok)

    def client(self, agent_id: str) -> MycelicClient:
        return MycelicClient(self.driver.base_url, self.sc.agent(agent_id).api_key)

    def settle(self) -> dict[str, Any]:
        """Wait until the service has applied everything, then record the final state of this step."""
        st = wait_idle(self.admin)
        if self.watcher:
            self.watcher.poll_once(full=True)
            self.watcher.metric(force=True)
        return st

    def run_stage(self, agents: list[LiveAgent]) -> dict[str, dict[str, Any]]:
        self.watcher.set_fast(True)
        try:
            states = run_agents(self.sc.workdir, self.driver.base_url, agents)
            self.settle()
        finally:
            self.watcher.set_fast(False)
        for a in agents:
            known = set(self.states.get(a.agent_id, {}).get("local_only", []))
            for text in states[a.agent_id]["local_only"]:
                if text not in known:
                    self.rec.emit("note_private", agent_id=a.agent_id, text=text, local_only=True)
        self.states.update(states)
        return states

    def latency(self, memory: dict[str, Any]) -> float | None:
        """Upper bound, in seconds, from the last note a conclusion rests on being accepted to the conclusion being
        readable: from sending the last watcher read that did not yet return that note to receiving the first read
        that returned the conclusion (both reads by the same poller, so no clock skew)."""
        seen, absent = self.watcher.first_seen, self.watcher.absent_at
        roots = (memory.get("metadata") or {}).get("roots") or []
        if memory["memory_id"] not in seen or not roots or any(r not in seen for r in roots):
            return None
        last = max(roots, key=lambda r: seen[r])
        start = absent.get(last)
        return round(seen[memory["memory_id"]] - (start if start is not None else seen[last]), 3)

    def regional_conclusions(self) -> dict[str, dict[str, Any]]:
        return {m["scope"]: m for m in self.admin.list_memories(scope=ENTERPRISE, layer="region", limit=200)
                if m.get("rule_id") == "regional_supply_risk"}

    def ask_strategy(self, viewer: str = "hq-sourcing-1") -> dict[str, Any] | None:
        res = self.client(viewer).query(STRATEGIC_QUERY, scope=ENTERPRISE, min_layer="enterprise", k=5, include_lineage=False)
        self.rec.emit("query", **query_payload(res, viewer, STRATEGIC_QUERY, ENTERPRISE, "enterprise"))
        hits = [h["memory"] for h in res["results"] if h["memory"].get("rule_id") == "strategic_second_source"]
        return self.admin.get_memory(hits[0]["memory_id"]) if hits else None

    def private_leaks(self, agent_ids: list[str]) -> tuple[int, list[str]]:
        """Private notes read from the agents' own local state, and those whose text the service holds anywhere."""
        texts = [t for aid in agent_ids for t in self.states[aid]["local_only"]]
        held = [m["text"] for m in all_memories(self.admin).values()]
        leaks = [t for t in texts if any(t in h for h in held)]
        for t in texts:                                   # and the keyword index, as an administrator sees it
            res = self.admin.query(t, scope=ENTERPRISE, k=5, include_lineage=False)
            if any(t in h["memory"]["text"] for h in res["results"]):
                leaks.append(t)
        return len(texts), sorted(set(leaks))

    # ------------------------------------------------------------------ acts
    def cold_open(self) -> None:
        self.rec.emit("service", state="starting", detail=f"nats-server + Mycelic service ({self.driver.name} driver)")
        self.driver.up()
        self.admin = MycelicClient(self.driver.base_url, self.driver.admin_token)
        h = self.admin.health()
        self.rec.update(lambda tr: tr["meta"].__setitem__("mycelic_version", h["version"]))
        self.rec.emit("service", state="healthy", detail=f"Mycelic {h['version']}: {h['status']}, event log on NATS JetStream")
        rules = [r for r in self.admin.list_rules() if r.get("enabled", True)]
        self.rec.update(lambda tr: tr.__setitem__("rules", [
            {k: r.get(k) for k in ("rule_id", "target_layer", "required_slots", "emits_slot", "min_units", "min_agents",
                                   "min_teams", "conclusion", "topic_prefix", "sources", "corroborate", "kind")}
            | {"owner": (r.get("metadata") or {}).get("owner")} for r in rules]))
        have = {r["rule_id"] for r in rules}
        self.check("service_ready", f"the service is {h['status']} and runs the operator-written rules {', '.join(sorted(have))}",
                   h["status"] == "ok" and set(DEMO_RULES) <= have)
        self.watcher = MemoryWatcher(self.driver.base_url, self.driver.admin_token, self.rec, self.sc,
                                     interval=self.poll, fast_interval=self.fast_poll)
        self.watcher.metric(force=True)
        self.watcher.start()

    def organisation(self) -> None:
        for a in self.sc.agents:
            agent = self.sc.register(self.admin, a)
            self.rec.emit("agent_registered", agent_id=a.agent_id, path=agent["path"], team_path=a.team_path, region=a.region,
                          role=a.role, label=a.label)
            self.sc.write_observations(a)
        registered = [x for x in self.admin.list_agents(ENTERPRISE) if x["status"] == "active"]
        keys = {a.api_key for a in self.sc.agents}
        teams = {a.team_path for a in self.sc.agents}
        self.check("agents_registered", f"{len(registered)} agents in {len(teams)} teams registered, each with its own key "
                   f"({len(self.sc.by_role('core'))} in the SD-9 story, {len(self.sc.by_role('background'))} on other work)",
                   len(registered) == len(self.sc.agents) == len(keys) and all(k.startswith("mk_") for k in keys))
        self.watcher.metric(force=True)

    def observe(self) -> None:
        agents = self.sc.stage("observe")
        states = self.run_stage(agents)
        shared = sum(s["counts"]["shared"] for s in states.values())
        n, leaks = self.private_leaks([a.agent_id for a in agents])
        self.log(f"  {len(agents)} agents shared {shared} notes and kept {n} private")
        self.check("private_stayed_local", f"{n} private notes stayed on {len(agents)} agents' own disks: every memory and the "
                   f"keyword index were searched and none of their text is in the service", n >= len(agents) and not leaks)

    def regions(self) -> None:
        self.run_stage(self.sc.stage("conclude"))
        self.regional = self.regional_conclusions()
        for scope, m in sorted(self.regional.items()):
            self.numbers["time_to_region_s"][leaf(scope)] = self.latency(m)
            self.log(f"  [{scope}] {m['support']} agents / {m['independent_teams']} teams: {m['text'][:100]}")
        self.check("regions_concluded", "EMEA and APAC each reached a regional supply-risk conclusion, independently",
                   set(self.regional) == {f"{ENTERPRISE}/emea", f"{ENTERPRISE}/apac"})
        viewer, question = "emea-field-sales-1", "supply risk sd-9"
        res = self.client(viewer).query(question, scope=ENTERPRISE, min_layer="region", k=10, include_lineage=False)
        self.rec.emit("query", **query_payload(res, viewer, question, ENTERPRISE, "region"))
        seen = {h["memory"]["scope"] for h in res["results"] if h["memory"]["layer"] == "region"}
        others = {leaf(u["path"]) for u in self.sc.units() if u["layer"] == "region"} - {"emea"}
        quoting = [h for h in res["results"] if h["memory"]["layer"] == "enterprise"
                   and any(f"[{o}]" in h["memory"]["text"] for o in others)]
        self.check("region_visibility",
                   f"access follows the org chart: {viewer}'s search returns EMEA's regional conclusion, not APAC's "
                   f"(region-layer hits: {region_hits(seen)})"
                   + ("; the enterprise summary it can read quotes both regions' conclusions" if quoting else ""),
                   seen == {f"{ENTERPRISE}/emea"} and any(h["memory"].get("rule_id") == "regional_supply_risk" for h in res["results"]))

    def enterprise(self) -> None:
        self.run_stage(self.sc.stage("synthesise"))
        strat = self.ask_strategy()
        self.check("strategy_exists", "the enterprise holds a strategic recommendation: qualify a second source", strat is not None)
        if strat is None:
            return
        self.strategy = strat
        regions = strat["metadata"].get("corroborated_units", {}).get("supply_risk", {}).get("region", [])
        self.check("corroborated_regions", f"supply risk corroborated in {len(regions)} regions ({' and '.join(sorted(leaf(r).upper() for r in regions))}); "
                   f"support {strat['support']} agents in {strat['independent_teams']} teams", len(regions) >= 2)
        g = self.admin.lineage(strat["memory_id"])
        self.check("lineage_layers", f"its lineage crosses {' -> '.join(g['layers'])}: two derivation steps from notes to strategy",
                   g["layers"] == ["agent", "region", "enterprise"])
        tte = self.latency(strat)
        self.numbers["time_to_enterprise_s"] = tte
        self.check("time_to_enterprise", f"head office's last note accepted -> enterprise conclusion readable within "
                   f"{tte * 1000:.0f} ms (upper bound, measured by polling the API)" if tte is not None
                   else "time to the enterprise conclusion could not be measured", tte is not None and tte >= 0)
        # background traffic: consolidated, but no rule conclusion rests on it
        bg = {a.agent_id for a in self.sc.by_role("background")}
        mems = all_memories(self.admin)
        bg_notes = {mid for mid, m in mems.items() if m["layer"] == "agent" and m["producer_id"] in bg}
        rule_hits = [m for m in mems.values() if m["operator"] == "slot_composition"
                     and set((m.get("metadata") or {}).get("roots") or []) & bg_notes]
        bg_cons = [m for m in mems.values() if m["operator"] == "topic_consolidation"
                   and set((m.get("metadata") or {}).get("roots") or []) <= bg_notes and (m.get("metadata") or {}).get("roots")]
        self.numbers.update(background_notes=len(bg_notes), background_consolidations=len(bg_cons),
                            background_rule_conclusions=len(rule_hits))
        self.check("background_no_rule", f"{len(bg)} background agents' {len(bg_notes)} notes consolidated into {len(bg_cons)} "
                   f"memories and completed no rule", bool(bg_notes and bg_cons and not rule_hits))

    def receipts(self) -> None:
        strat = self.strategy
        if strat is None:
            self.check("redacted_view", "no strategy to trace", False)
            return
        g = self.admin.lineage(strat["memory_id"])
        self.rec.emit("lineage", viewer="admin", memory_id=strat["memory_id"], graph=g)
        viewer = "emea-field-sales-1"
        view = self.client(viewer).lineage(strat["memory_id"])
        self.rec.emit("lineage", viewer=viewer, memory_id=strat["memory_id"], graph=view)
        redacted_by_region: dict[str, int] = {}
        for n in view["nodes"].values():
            if n["redacted"]:
                r = n["scope"].split("/")[1] if "/" in n["scope"] else n["scope"]
                redacted_by_region[r] = redacted_by_region.get(r, 0) + 1
        hq_notes = [mid for mid, n in g["nodes"].items() if n["layer"] == "agent" and n["scope"].startswith(f"{ENTERPRISE}/hq/")]
        apac = self.regional.get(f"{ENTERPRISE}/apac", {}).get("memory_id")
        emea = self.regional.get(f"{ENTERPRISE}/emea", {}).get("memory_id")
        edges = lambda x: {(e["child"], e["parent"]) for e in x["edges"]}  # noqa: E731
        self.check("redacted_view",
                   f"the same lineage for {viewer}: {view['redacted_contributions']} of {len(view['nodes'])} contributions redacted "
                   f"({', '.join(f'{v} {k}' for k, v in sorted(redacted_by_region.items()))}), "
                   "its own region and note readable, shape intact",
                   view["redacted_contributions"] == sum(redacted_by_region.values()) > 0
                   and apac in view["nodes"] and view["nodes"][apac]["text"] is None
                   and emea in view["nodes"] and not view["nodes"][emea]["redacted"]
                   and all(view["nodes"][mid]["redacted"] for mid in hq_notes)
                   and set(view["nodes"]) == set(g["nodes"]) and edges(view) == edges(g))

    def evidence(self) -> None:
        strat = self.strategy
        if strat is None:
            self.check("strategy_withdrawn", "no strategy to withdraw", False)
            return
        reason = "inventory was counted twice"
        supplier_notes = [m for m in self.admin.list_memories(scope=f"{ENTERPRISE}/apac", layer="agent", limit=200)
                          if m["slot"] == "supplier_buffer_low"]
        for m in sorted(supplier_notes, key=lambda m: m["producer_id"]):
            self.client(m["producer_id"]).retract(m["memory_id"], reason)
            self.rec.emit("retracted", agent_id=m["producer_id"], memory_id=m["memory_id"], reason=reason)
        self.settle()
        regional_now = set(self.regional_conclusions())
        gone = self.ask_strategy() is None
        self.check("strategy_withdrawn", f"APAC's regional conclusion and the strategy are withdrawn "
                   f"(regions still concluding: {region_hits(regional_now)})",
                   regional_now == {f"{ENTERPRISE}/emea"} and gone)
        old = self.admin.get_memory(strat["memory_id"])
        self.check("history_kept", f"the withdrawn strategy is kept as history (status: {old['status']})", old["status"] == "retracted")
        a = self.sc.agent("apac-procurement-2")
        retracted_by = {m["producer_id"] for m in supplier_notes}
        self.sc.write_observations(a, [RECOUNT], tag="recount", include_private=False)
        self.run_stage([a])
        back = self.ask_strategy()
        recount = [m["memory_id"] for m in self.admin.list_memories(scope=a.path, layer="agent", limit=20)
                   if m.get("local_ref") == f"{a.agent_id}-recount-0"]
        self.check("strategy_restored", f"a physical recount posted by {a.agent_id} (APAC procurement"
                   + (", one of the agents that retracted" if a.agent_id in retracted_by else "")
                   + ") restores the regional conclusion and the strategy as new conclusions resting on the new note; "
                   "the withdrawn ones stay in history",
                   back is not None and back["memory_id"] != strat["memory_id"] and bool(recount)
                   and recount[0] in (back["metadata"].get("roots") or [])
                   and self.admin.get_memory(strat["memory_id"])["status"] == "retracted"
                   and set(self.regional_conclusions()) == {f"{ENTERPRISE}/emea", f"{ENTERPRISE}/apac"})
        if back is not None:
            self.numbers["time_to_restore_s"] = self.latency(back)
            self.strategy = back
            self.regional = self.regional_conclusions()
            self.rec.emit("lineage", viewer="admin", memory_id=back["memory_id"], graph=self.admin.lineage(back["memory_id"]))

    def plug(self) -> None:
        strat = self.strategy
        st = self.settle()                               # nothing in flight: the log is complete before the kill
        self.watcher.pause()
        try:
            before = set(all_memories(self.admin))
            active_before = {m["memory_id"] for m in self.admin.list_memories(scope=ENTERPRISE, limit=500)}
            g_before = self.admin.lineage(strat["memory_id"]) if strat else None
            total = st["checks"]["transport"].get("last_seq") or st["checks"]["transport"].get("stream_messages")
            self.driver.kill("mycelic")
            self.rec.emit("service", state="killed", detail="SIGKILL: no graceful shutdown")
            self.driver.wipe_database()
            self.rec.emit("service", state="db_wiped", detail="the service's SQLite database files were deleted")
            self.rec.emit("service", state="rebuilding", detail=f"replaying {total} events from the JetStream log")
            stop = threading.Event()
            probe = MycelicClient(self.driver.base_url, self.driver.admin_token, retries=0, timeout=2)

            def progress() -> None:
                last = None
                while not stop.is_set():
                    try:
                        applied = probe.status()["checks"]["consumer"].get("last_applied_seq")
                        if applied is not None and applied != last and applied <= total:
                            last = applied
                            self.rec.emit("rebuild_progress", applied=applied, total=total)
                    except (MycelicError, OSError, KeyError):
                        pass
                    stop.wait(0.1)

            t = threading.Thread(target=progress, name="mycelic-live-rebuild", daemon=True)
            t0 = time.monotonic()
            t.start()
            try:
                self.driver.start("mycelic")             # returns once /ready answers 200: the replay is done
            finally:
                rebuild_s = round(time.monotonic() - t0, 2)
                stop.set()
                t.join(timeout=5)
            self.rec.emit("rebuild_progress", applied=total, total=total)
            self.rec.emit("service", state="ready", detail=f"ready {rebuild_s} s after the restart: {total} logged events "
                          "replayed into an empty database", rebuild_s=rebuild_s, rebuild_events=total)
            st = wait_idle(self.admin, timeout=180)
            applied = st["checks"]["consumer"].get("last_applied_seq") or 0
            # a fresh database does not know the rules yet, so the restarted service appends its rules file to the
            # log again (one rule.upserted event per rule) right after the replay: the log grows, the replay does not
            appended = max(0, applied - total)
            self.numbers.update(rebuild_s=rebuild_s, rebuild_events=total, events_appended_on_restart=appended)
            after = set(all_memories(self.admin))
            active_after = {m["memory_id"] for m in self.admin.list_memories(scope=ENTERPRISE, limit=500)}
            g_after = self.admin.lineage(strat["memory_id"]) if strat else None
            if g_after:
                self.rec.emit("lineage", viewer="admin", memory_id=strat["memory_id"], graph=g_after)
            edges = lambda x: {(e["child"], e["parent"]) for e in x["edges"]}  # noqa: E731
            same = (g_before is not None and g_after is not None and g_after["roots"] == g_before["roots"]
                    and edges(g_after) == edges(g_before) and g_after["layers"] == g_before["layers"])
            self.check("rebuild_identical", f"rebuilt {len(active_after)} active memories from {total} events in {rebuild_s} s; "
                       f"the strategy's lineage is identical (same roots, same {len(edges(g_after)) if g_after else 0} edges)",
                       same and active_after == active_before and after == before)
            back = self.ask_strategy()
            self.check("keys_survive_rebuild", "head office's agent asks again with the same key and gets the same strategy",
                       back is not None and strat is not None and back["memory_id"] == strat["memory_id"])
        finally:
            self.watcher.resume()
        self.watcher.poll_once()
        self.watcher.metric(force=True)

    def close(self) -> None:
        n, leaks = self.private_leaks(list(self.states))
        self.check("private_never_reached", f"after everything, none of the {n} private notes of {len(self.states)} agents was "
                   f"ever sent to the service (the demo read them from each agent's own disk to show them)",
                   n >= len(self.sc.agents) and not leaks)
        st = self.admin.status()
        self.rec.emit("metric", **metric_payload(st))
        checks = self.rec.trace["checks"]
        strat = self.strategy or {}
        summary = {
            "agents": len(self.sc.agents), "agents_core": len(self.sc.by_role("core")),
            "agents_background": len(self.sc.by_role("background")), "teams": len({a.team_path for a in self.sc.agents}),
            "notes_shared": sum(s["counts"]["shared"] for s in self.states.values()),
            "notes_private": sum(s["counts"]["local_only"] for s in self.states.values()),
            "memories_by_layer": st["stats"]["memories_by_layer"],
            "memories_by_status": st["stats"]["memories_by_status"],
            "lineage_edges": st["stats"]["lineage_edges"],
            "stream_messages": st["checks"]["transport"].get("stream_messages"),
            "rebuild_s": self.numbers.get("rebuild_s"), "rebuild_events": self.numbers.get("rebuild_events"),
            "events_appended_on_restart": self.numbers.get("events_appended_on_restart"),
            "time_to_region_s": self.numbers["time_to_region_s"],
            "time_to_enterprise_s": self.numbers.get("time_to_enterprise_s"),
            "time_to_restore_s": self.numbers.get("time_to_restore_s"),
            "background_notes": self.numbers.get("background_notes"),
            "background_consolidations": self.numbers.get("background_consolidations"),
            "background_rule_conclusions": self.numbers.get("background_rule_conclusions"),
            "strategy": {"memory_id": strat.get("memory_id"), "support": strat.get("support"),
                         "independent_teams": strat.get("independent_teams"), "confidence": strat.get("confidence"),
                         "regions": [leaf(r) for r in (strat.get("metadata") or {}).get("corroborated_units", {})
                                     .get("supply_risk", {}).get("region", [])]} if strat else None,
            "checks_passed": sum(1 for c in checks if c["ok"]), "checks_total": len(checks),
        }
        self.rec.update(lambda tr: tr.__setitem__("summary", summary))

    def shutdown(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()


# ---------------------------------------------------------------------------------------------------- entry points
def nats_version(nats_bin: str | None) -> str | None:
    if not nats_bin:
        return None
    try:
        out = subprocess.run([nats_bin, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out.split()[-1] if out else None


def port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def preflight(args: argparse.Namespace, *, serve: bool) -> list[str]:
    """Everything that would make the live run fail, each with what to do about it."""
    problems = []
    if args.driver == "process":
        nats_bin = os.environ.get("MYCELIC_NATS_SERVER_BIN") or shutil.which("nats-server")
        if not nats_bin:
            problems.append("nats-server not found: install it (https://github.com/nats-io/nats-server/releases) and put it on "
                            "PATH, or set MYCELIC_NATS_SERVER_BIN=/path/to/nats-server")
        elif not os.path.isfile(nats_bin):
            problems.append(f"nats-server not found at {nats_bin} (MYCELIC_NATS_SERVER_BIN): point it at the binary itself")
        elif not os.access(nats_bin, os.X_OK):
            problems.append(f"nats-server at {nats_bin} is not executable: chmod +x it")
        missing = [m for m in ("aiohttp", "nats", "prometheus_client") if importlib.util.find_spec(m) is None]
        if missing:
            problems.append(f"the service needs {', '.join(missing)} in this Python ({sys.executable}): "
                            f"pip install -r requirements-runtime.txt")
    else:
        if not shutil.which("docker"):
            problems.append("docker not found: install Docker, or use --driver process")
        elif subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            problems.append("the Docker daemon is not reachable: start it, or use --driver process")
    if serve and not port_free(args.host, args.port):
        problems.append(f"port {args.port} on {args.host} is in use: pass --port <another>, or stop what is using it")
    if serve and not CONSOLE.exists():
        problems.append(f"the console page {CONSOLE} is missing")
    return problems


def bypass_proxies_for_localhost() -> None:
    """Every request the demo makes goes to 127.0.0.1; an http_proxy without a no_proxy entry (or a macOS system
    proxy, which urllib also reads) would route it to the proxy.  The agent processes inherit this."""
    for var in ("NO_PROXY", "no_proxy"):
        have = [h.strip() for h in os.environ.get(var, "").split(",") if h.strip()]
        os.environ[var] = ",".join(have + [h for h in ("127.0.0.1", "localhost") if h not in have])


def make_driver(args: argparse.Namespace) -> Driver:
    if args.driver == "process":
        return DemoProcessDriver()
    extra = {"CA_BUNDLE_FILE": os.environ["MYCELIC_BUILD_CA_BUNDLE"]} if os.environ.get("MYCELIC_BUILD_CA_BUNDLE") else None
    return ComposeDriver(project="mycelic-live", build=not args.no_build, extra_env=extra)


def load_trace(path: Path) -> dict[str, Any]:
    """Read a trace, or exit with one line saying what is wrong with it."""
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"error: cannot read the trace {path}: {exc.strerror or exc}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {path} is not JSON ({exc.msg}, line {exc.lineno})") from None
    if not isinstance(trace, dict) or trace.get("schema") != SCHEMA:
        found = trace.get("schema") if isinstance(trace, dict) else type(trace).__name__
        raise SystemExit(f"error: {path}: not a {SCHEMA} trace (schema: {found!r})")
    return trace


def export(args: argparse.Namespace) -> int:
    trace = load_trace(args.trace)
    for path, fragment in ((args.export, False), (args.export_fragment, True)):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(build_page(trace, fragment=fragment), encoding="utf-8")
            print(f"wrote {path} ({path.stat().st_size // 1024} KiB, {'fragment' if fragment else 'standalone page'}, "
                  f"{len(trace['events'])} events)")
    return 0


def serve_replay(args: argparse.Namespace) -> int:
    trace = load_trace(args.replay)
    if not port_free(args.host, args.port):
        print(f"error: port {args.port} on {args.host} is in use: pass --port <another>", file=sys.stderr)
        return 2
    server = ConsoleServer(args.host, args.port, trace=trace)
    server.start()
    print(f"replaying {args.replay} ({len(trace['events'])} events, recorded {trace['meta'].get('recorded_at')}) at {server.url}")
    print(f"  presenter notes: {server.url}?presenter   ·   manual advance: {server.url}#manual")
    if not args.no_browser:
        webbrowser.open(server.url)
    with interrupts_raise():
        try:
            wait_or_interrupt(args.exit_after)
        except KeyboardInterrupt:
            pass
        finally:
            with interrupts_ignored():
                server.stop()
    return 0


def wait_or_interrupt(seconds: float | None) -> None:
    deadline = None if seconds is None else time.monotonic() + seconds
    while deadline is None or time.monotonic() < deadline:
        time.sleep(0.25)


def _interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


# SIGTERM, and SIGHUP (the terminal window closed, an ssh session dropped), stop the demo like Ctrl-C does
STOP_SIGNALS = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s is not None]


@contextlib.contextmanager
def interrupts_raise() -> Iterator[None]:
    previous = {s: signal.signal(s, _interrupt) for s in STOP_SIGNALS}
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


@contextlib.contextmanager
def interrupts_ignored() -> Iterator[None]:
    """Cleanup that a second Ctrl-C (or a SIGTERM/SIGHUP) must not cut short."""
    sigs = [signal.SIGINT] + STOP_SIGNALS
    previous = {s: signal.signal(s, signal.SIG_IGN) for s in sigs}
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def one_line(exc: BaseException) -> str:
    first = (str(exc).strip().splitlines() or [""])[0]
    text = f"{type(exc).__name__}: {first}" if first else type(exc).__name__
    return text if len(text) <= 200 else text[:199] + "…"


def run_live(args: argparse.Namespace) -> int:
    recording = args.record is not None
    driver = make_driver(args)
    rec = TraceRecorder(mode="record" if recording else "present", driver=driver.name, poll=args.poll, fast_poll=args.fast_poll,
                        rules_source="deploy/mycelic/rules.json: " + (", ".join(DEMO_RULES) if args.driver == "process" else "all rules"))
    rec.update(lambda tr: tr["meta"].__setitem__("nats_server_version", nats_version(getattr(driver, "nats_bin", None))))
    workdir = Path(tempfile.mkdtemp(prefix="mycelic-live-"))
    demo = LiveDemo(driver, rec, workdir, spread=args.spread, poll=args.poll, fast_poll=args.fast_poll)
    control: queue.Queue[str] = queue.Queue()
    busy = threading.Event()
    failed = threading.Event()

    def refuse() -> str | None:
        if args.auto:
            return "auto mode: the acts advance by themselves"
        if rec.done:
            return "the run is complete"
        if failed.is_set():
            return "the run stopped"
        return None

    server: ConsoleServer | None = None
    code = 2
    with interrupts_raise():
        try:
            if not recording:
                server = ConsoleServer(args.host, args.port, recorder=rec, control=control, busy=busy.is_set, refuse=refuse)
                server.start()
                how = f"auto, {args.dwell} s per act" if args.auto else "advance from the console"
                print(f"Mycelic live console: {server.url}  ({how})")
                print(f"  presenter notes: {server.url}?presenter")
                if not args.no_browser:
                    webbrowser.open(server.url)
            try:
                for index in range(len(demo.acts)):
                    if index and not recording:
                        if args.auto:
                            time.sleep(args.dwell)
                        else:
                            while True:
                                try:
                                    control.get(timeout=0.5)
                                    break
                                except queue.Empty:
                                    continue
                    busy.set()
                    try:
                        demo.run_act(index)
                    finally:
                        busy.clear()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                failed.set()
                message = one_line(exc)
                rec.emit("error", message=message, hint=REPLAY_COMMAND)
                print(f"\nERROR: {message}", file=sys.stderr)
                print("--- mycelic log tail ---\n" + (driver.logs("mycelic", 40) or ""), file=sys.stderr)
                print(f"\nthe live run stopped; {REPLAY_HINT}", file=sys.stderr)
                if server is not None:                   # keep the console up: it shows the error and the replay command
                    with interrupts_ignored():
                        demo.shutdown()
                        driver.down()
                    print("the console stays up with the error: Ctrl-C to stop", file=sys.stderr)
                    wait_or_interrupt(args.exit_after)
                return 2
            rec.finish()
            checks = rec.trace["checks"]
            ok = bool(checks) and all(c["ok"] for c in checks)
            print(f"\n{sum(c['ok'] for c in checks)}/{len(checks)} checks passed")
            s = rec.trace["summary"]
            keys = ("agents", "notes_shared", "notes_private", "time_to_enterprise_s", "rebuild_s", "stream_messages")
            print(f"summary: {json.dumps({k: s[k] for k in keys})}")
            code = 0 if ok else 1
            if recording:
                snap = rec.snapshot()
                text = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
                assert_no_secrets(text, demo.secrets())
                args.record.parent.mkdir(parents=True, exist_ok=True)
                args.record.write_text(text + "\n", encoding="utf-8")
                print(f"trace written to {args.record} ({len(text) // 1024} KiB, {len(snap['events'])} events)")
            else:
                with interrupts_ignored():               # the stack is not needed any more; the console stays up
                    demo.shutdown()
                    driver.down()
                until = f" for {args.exit_after} s" if args.exit_after is not None else ": Ctrl-C to stop"
                print("the run is complete and the stack is stopped; the console stays up" + until)
                wait_or_interrupt(args.exit_after)
            return code
        except KeyboardInterrupt:
            if rec.done:                                 # a finished run stopped with Ctrl-C keeps its result
                return code
            print("\ninterrupted: stopping the stack")
            return 130
        finally:
            with interrupts_ignored():
                demo.shutdown()
                driver.down()
                shutil.rmtree(workdir, ignore_errors=True)   # the agents' local files hold the private notes
                if server is not None:
                    server.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--present", action="store_true", help="start the stack and serve the live console (the default)")
    mode.add_argument("--record", type=Path, metavar="PATH", help="headless: run every act back to back and write the trace")
    mode.add_argument("--replay", type=Path, metavar="PATH", nargs="?", const=DEFAULT_TRACE,
                      help=f"serve the console over a recorded trace (default: {DEFAULT_TRACE}); no stack needed")
    mode.add_argument("--export", type=Path, metavar="PATH", help="write a standalone replay page with the trace embedded")
    ap.add_argument("--export-fragment", type=Path, metavar="PATH", help="the replay page without doctype/html/head/body")
    ap.add_argument("--trace", type=Path, default=DEFAULT_TRACE, help="the trace to export (default: %(default)s)")
    ap.add_argument("--driver", choices=["process", "compose"], default="process")
    ap.add_argument("--no-build", action="store_true", help="compose: do not rebuild the image")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--auto", action="store_true", help="advance the acts by themselves")
    ap.add_argument("--dwell", type=float, default=8.0, help="--auto: seconds between acts (default: %(default)s)")
    ap.add_argument("--exit-after", type=float, default=None, metavar="SECONDS",
                    help="live/replay: stop this many seconds after the last act (default: wait for Ctrl-C)")
    ap.add_argument("--spread", type=float, default=6.0, help="seconds over which act 2's agents start sharing (default: %(default)s)")
    ap.add_argument("--poll", type=float, default=0.25, help="watcher interval between agent waves (default: %(default)s)")
    ap.add_argument("--fast-poll", type=float, default=0.02, help="watcher interval while agents share (default: %(default)s)")
    args = ap.parse_args(argv)
    if args.export or args.export_fragment:
        if args.record or args.replay or args.present:
            ap.error("--export/--export-fragment cannot be combined with --present, --record or --replay")
        return export(args)
    bypass_proxies_for_localhost()
    if args.replay:
        return serve_replay(args)
    problems = preflight(args, serve=args.record is None)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        print(f"fix the above, {REPLAY_HINT}", file=sys.stderr)
        return 2
    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
