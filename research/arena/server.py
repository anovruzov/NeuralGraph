"""The console server: SSE events, state, artifacts, graph, and controls that do something.

Follows the convention of ``demo/live/mycelic_live.py``: stdlib ``ThreadingHTTPServer``, one thread per
connection, ``GET /events`` as Server-Sent Events.  Binds loopback only.

    GET  /                  the console page
    GET  /events?after=N    SSE: ``event: snapshot`` (state), then every stored event with ``id > N``
                            (``Last-Event-ID`` is honoured, so a reconnecting browser resumes), then new ones as
                            they are appended; ``event: state`` every few seconds; ``: keepalive`` otherwise
    GET  /state             the same snapshot as JSON
    GET  /graph?site=S      node/edge counts and a sample subgraph from the site's NeuralGraph sqlite, plus the
                            fabric's lineage edges; ``{"status": "NO DATA"}`` with a reason when absent
    GET  /artifact?path=P   the text of a referenced artifact, allow-listed to the configured roots
    GET  /export            state + every event as JSON;  GET /export.jsonl  the raw event log
    POST /control           {"action": "start"|"pause"|"resume"|"stop", "scenario": ..., "confirm": bool}

A control is a real process operation (``Popen`` of the harness, ``SIGSTOP``/``SIGCONT``/``SIGTERM`` of its
process group); its outcome is an event like any other.  ``stop`` refuses without ``confirm``.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import concurrency
from .events import EventStore
from .sources import GitSource, HarnessSource, SwarmSource

HERE = Path(__file__).resolve().parent
ARTIFACT_MAX = 256 * 1024


@dataclass
class Scenario:
    name: str
    description: str
    argv: Callable[[Path, str], list[str]]      # (run_dir, run_id) -> command


@dataclass
class Config:
    state_dir: Path
    swarm_dir: Path | None = None
    run_dir: Path | None = None
    run_log: Path | None = None
    repo: Path | None = None
    branch: str = "HEAD"
    runs_root: Path | None = None
    scenarios: dict[str, Scenario] = field(default_factory=dict)
    harness_cwd: Path | None = None
    harness_env: dict[str, str] = field(default_factory=dict)
    poll_seconds: float = 0.5
    artifact_roots: list[Path] = field(default_factory=list)


class HarnessController:
    """One harness run at a time, as a real child process group."""

    def __init__(self, app: "App") -> None:
        self.app = app
        self.proc: subprocess.Popen[bytes] | None = None
        self.run_dir: Path | None = None
        self.scenario: str | None = None
        self.paused = False
        self._log: Any = None
        self._reported_exit = False

    def status(self) -> dict[str, Any]:
        rc = self.proc.poll() if self.proc else None
        return {"pid": self.proc.pid if self.proc else None, "running": bool(self.proc and rc is None),
                "paused": self.paused, "returncode": rc, "run_dir": str(self.run_dir) if self.run_dir else None,
                "scenario": self.scenario, "scenarios": {k: v.description for k, v in self.app.config.scenarios.items()}}

    def start(self, scenario: str) -> dict[str, Any]:
        cfg = self.app.config
        if scenario not in cfg.scenarios:
            raise ValueError(f"unknown scenario {scenario!r}")
        if self.proc and self.proc.poll() is None:
            raise RuntimeError("a run is already in progress")
        if cfg.runs_root is None:
            raise RuntimeError("no --runs-root configured")
        run_id = f"arena-{scenario}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        cfg.runs_root.mkdir(parents=True, exist_ok=True)
        run_dir = cfg.runs_root / run_id                 # the harness creates it, and refuses one that exists
        argv = cfg.scenarios[scenario].argv(run_dir, run_id)
        log_path = cfg.runs_root / f"{run_id}.log"       # so the log lives beside the run, not inside it
        self._log = open(log_path, "wb")
        env = dict(os.environ)
        env.update(cfg.harness_env)
        self.proc = subprocess.Popen(argv, cwd=str(cfg.harness_cwd or Path.cwd()), env=env, stdout=self._log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.run_dir, self.scenario, self.paused, self._reported_exit = run_dir, scenario, False, False
        self.app.add_source(HarnessSource(run_dir, log_path, run_id))
        self.app.store.append(tag="CONTROL", source="control", agent="operator",
                              msg=f"start {scenario}: pid {self.proc.pid} -> {run_dir}",
                              ref={"path": str(log_path)}, data={"argv": argv, "pid": self.proc.pid, "run_id": run_id})
        return self.status()

    @staticmethod
    def _session_pids(sid: int) -> list[int]:
        try:
            txt = subprocess.run(["ps", "-eo", "pid=,sid="], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        out = []
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) == sid and int(parts[0]) != sid:
                out.append(int(parts[0]))
        return out

    def _sweep(self, sid: int) -> int:
        """``start_new_session`` made the harness its session's leader, so a site process or broker that outlives
        it is still in that session: kill them all and report how many there were, rather than leak them."""
        pids = self._session_pids(sid)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return len(pids)

    def _signal(self, sig: int) -> None:
        if not (self.proc and self.proc.poll() is None):
            raise RuntimeError("no run in progress")
        os.killpg(os.getpgid(self.proc.pid), sig)

    def pause(self) -> dict[str, Any]:
        self._signal(signal.SIGSTOP)
        self.paused = True
        self.app.store.append(tag="CONTROL", source="control", agent="operator", msg=f"pause: SIGSTOP pgid {self.proc.pid}",
                              data={"pid": self.proc.pid})
        return self.status()

    def resume(self) -> dict[str, Any]:
        self._signal(signal.SIGCONT)
        self.paused = False
        self.app.store.append(tag="CONTROL", source="control", agent="operator", msg=f"resume: SIGCONT pgid {self.proc.pid}",
                              data={"pid": self.proc.pid})
        return self.status()

    def stop(self, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise PermissionError("stop is destructive: confirm required")
        if self.paused:
            self._signal(signal.SIGCONT)
        self._signal(signal.SIGTERM)
        pid = self.proc.pid
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            self.proc.wait(5)
        self.paused = False
        swept = self._sweep(pid)
        self.app.store.append(tag="CONTROL", source="control", agent="operator",
                              msg=f"stop: pid {pid} exited {self.proc.returncode}; swept {swept} from its session",
                              data={"pid": pid, "returncode": self.proc.returncode, "swept": swept})
        self._reported_exit = True
        return self.status()

    def check(self) -> None:
        """Called by the poller: report a run that ended on its own, once."""
        if self.proc and self.proc.poll() is not None and not self._reported_exit:
            self._reported_exit = True
            rc = self.proc.returncode
            self.app.store.append(tag="COMPLETE" if rc == 0 else "FAIL", source="control", agent="hq",
                                  msg=f"run {self.scenario} exited {rc}", data={"pid": self.proc.pid, "returncode": rc})
            swept = self._sweep(self.proc.pid)
            if swept:
                self.app.store.append(tag="RECOVER", source="control", agent="hq",
                                      msg=f"swept {swept} process(es) the run left in session {self.proc.pid}",
                                      data={"pid": self.proc.pid, "swept": swept})
            if self._log:
                self._log.close()


class App:
    def __init__(self, config: Config) -> None:
        self.config = config
        config.state_dir.mkdir(parents=True, exist_ok=True)
        self.store = EventStore(config.state_dir / "events.jsonl")
        self.sources: list[Any] = []
        self.swarm: SwarmSource | None = None
        self.harness: HarnessSource | None = None
        self.controller = HarnessController(self)
        self.started = time.time()
        self.stopping = False
        self._lock = threading.Lock()
        self._errors: set[str] = set()
        if config.swarm_dir:
            self.swarm = SwarmSource(config.swarm_dir)
            self.add_source(self.swarm, announce=False)
        if config.run_dir:
            self.add_source(HarnessSource(config.run_dir, config.run_log), announce=False)
        if config.repo:
            self.add_source(GitSource(config.repo, config.branch), announce=False)
        self._restore()
        for s in self.sources:
            self.store.append(tag="SOURCE", source="console", msg=f"source attached: {s.name} {getattr(s, 'root', getattr(s, 'run_dir', getattr(s, 'repo', '')))}")

    # ---- sources and their persisted read positions
    def add_source(self, src: Any, announce: bool = True) -> None:
        with self._lock:
            self.sources.append(src)
            if isinstance(src, HarnessSource):
                self.harness = src
        if announce:
            self.store.append(tag="SOURCE", source="console", msg=f"source attached: {src.name} {getattr(src, 'run_dir', '')}")

    def _state_path(self) -> Path:
        return self.config.state_dir / "sources.json"

    def _restore(self) -> None:
        p = self._state_path()
        if not p.exists():
            return
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for s in self.sources:
            key = f"{s.name}:{getattr(s, 'root', getattr(s, 'run_dir', getattr(s, 'repo', '')))}"
            if key in saved:
                try:
                    s.restore(saved[key])
                except Exception:   # a corrupt snapshot must not stop the console; it re-reads from zero
                    pass

    def _save(self) -> None:
        with self._lock:
            d = {f"{s.name}:{getattr(s, 'root', getattr(s, 'run_dir', getattr(s, 'repo', '')))}": s.snapshot() for s in self.sources}
        tmp = self._state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(self._state_path())

    def poll_once(self) -> None:
        with self._lock:
            sources = list(self.sources)
        for s in sources:
            try:
                s.poll(self.store)
            except Exception as e:   # one bad line must not kill the poller; say so once per message
                key = f"{s.name}:{e}"
                if key not in self._errors:
                    self._errors.add(key)
                    self.store.append(tag="FAIL", source="console", msg=f"source {s.name} raised {type(e).__name__}: {e}")
        self.controller.check()
        self._save()

    def poll_forever(self) -> None:
        while not self.stopping:
            self.poll_once()
            time.sleep(self.config.poll_seconds)

    # ---- state
    def state(self) -> dict[str, Any]:
        now = time.time()
        registry = self.swarm.registry() if self.swarm else []
        return {
            "console": {"started": self.started, "now": now, "latest_event_id": self.store.latest_id,
                        "sources": [s.name for s in self.sources], "counts": self.store.counts()},
            "agents": registry,
            "concurrency": concurrency.stats(registry, now),
            "run": self.harness.summary() if self.harness else None,
            "harness": self.controller.status(),
            "policy": {"model_requests_in_benchmark": 0, "note": "the benchmark makes no model calls by policy; "
                       "builder-layer model request boundaries are not instrumented (stamps bound the agent)"},
        }

    # ---- graph
    def graph(self, site: str | None) -> dict[str, Any]:
        if not self.harness:
            return {"status": "NO DATA", "reason": "no run attached"}
        run = self.harness.run_dir
        site = site or (sorted(self.harness.sites)[0] if self.harness.sites else None)
        if not site:
            return {"status": "NO DATA", "reason": "run has produced no site graphs yet"}
        p = run / "main" / "edge" / f"site-{site}.graph.sqlite3"
        out: dict[str, Any] = {"status": "OK", "site": site, "path": str(p)}
        if not p.exists():
            return {"status": "NO DATA", "reason": f"{p.name} not written yet", "site": site}
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=0.5)
            try:
                out["nodes"] = con.execute("select count(*) from nodes").fetchone()[0]
                out["edges"] = con.execute("select count(*) from edges").fetchone()[0]
                ncols = [r[1] for r in con.execute("pragma table_info(nodes)")]
                ecols = [r[1] for r in con.execute("pragma table_info(edges)")]
                pick = [c for c in ("node_id", "layer", "created_at", "session_key") if c in ncols][:4]
                rows = con.execute(f"select {', '.join(pick)} from nodes order by rowid limit 80").fetchall()
                out["sample_nodes"] = [dict(zip(pick, r)) for r in rows]
                src = next((c for c in ecols if c in ("source_id", "src", "from_id")), None)
                dst = next((c for c in ecols if c in ("target_id", "dst", "to_id")), None)
                typ = next((c for c in ecols if c in ("edge_type", "type", "kind")), None)
                if src and dst and out["edges"]:
                    ids = tuple(r[0] for r in rows)
                    q = f"select {src}, {dst}{', ' + typ if typ else ''} from edges where {src} in ({','.join('?' * len(ids))}) limit 400"
                    out["sample_edges"] = [{"source": e[0], "target": e[1], "type": e[2] if typ else None}
                                           for e in con.execute(q, ids).fetchall()]
                else:
                    out["sample_edges"] = []
            finally:
                con.close()
        except sqlite3.Error as e:
            return {"status": "NO DATA", "reason": f"sqlite: {e}", "site": site}
        db = run / "fabric" / "mycelic.db"
        out["lineage"] = {"count": 0, "sample": []}
        if db.exists():
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
                try:
                    out["lineage"]["count"] = con.execute("select count(*) from lineage_edges").fetchone()[0]
                    cur = con.execute("select * from lineage_edges limit 120")
                    cols = [c[0] for c in cur.description]
                    out["lineage"]["columns"] = cols
                    out["lineage"]["sample"] = [dict(zip(cols, r)) for r in cur.fetchall()]
                finally:
                    con.close()
            except sqlite3.Error as e:
                out["lineage"]["error"] = str(e)
        return out

    # ---- artifacts
    def artifact(self, path: str) -> tuple[int, str, bytes]:
        try:
            p = Path(path).resolve()
        except OSError:
            return 400, "text/plain", b"bad path"
        roots = [r.resolve() for r in self.config.artifact_roots]
        if not any(p == r or r in p.parents for r in roots):
            return 403, "text/plain", b"outside the allowed roots"
        if not p.is_file():
            return 404, "text/plain", b"not a file"
        if p.suffix in (".sqlite3", ".db"):
            return 415, "text/plain", b"sqlite artifact: use /graph for its contents"
        data = p.read_bytes()[:ARTIFACT_MAX]
        return 200, "text/plain; charset=utf-8", data


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    page = (HERE / "console.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "arena/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:   # quiet; the event log is the log
            pass

        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, "application/json", json.dumps(obj, sort_keys=True).encode())

        def do_GET(self) -> None:
            u = urlsplit(self.path)
            qs = parse_qs(u.query)
            if u.path == "/":
                self._send(200, "text/html; charset=utf-8", page)
            elif u.path == "/events":
                self._events(qs)
            elif u.path == "/state":
                self._json(200, app.state())
            elif u.path == "/graph":
                self._json(200, app.graph(qs.get("site", [None])[0]))
            elif u.path == "/artifact":
                code, ctype, body = app.artifact(qs.get("path", [""])[0])
                self._send(code, ctype, body)
            elif u.path == "/export":
                self._json(200, {"state": app.state(), "events": [json.loads(e.to_json()) for e in app.store.since(0)]})
            elif u.path == "/export.jsonl":
                self._send(200, "application/x-ndjson", app.store.path.read_bytes())
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self) -> None:
            u = urlsplit(self.path)
            if u.path != "/control":
                self._send(404, "text/plain", b"not found")
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                self._json(400, {"error": "bad json"})
                return
            action = body.get("action")
            try:
                if action == "start":
                    self._json(200, app.controller.start(str(body.get("scenario") or "short")))
                elif action == "pause":
                    self._json(200, app.controller.pause())
                elif action == "resume":
                    self._json(200, app.controller.resume())
                elif action == "stop":
                    self._json(200, app.controller.stop(bool(body.get("confirm"))))
                else:
                    self._json(400, {"error": f"unknown action {action!r}"})
            except PermissionError as e:
                self._json(409, {"error": str(e), "confirm_required": True})
            except (RuntimeError, ValueError) as e:
                self._json(409, {"error": str(e)})

        def _events(self, qs: dict[str, list[str]]) -> None:
            try:
                after = int(self.headers.get("Last-Event-ID") or qs.get("after", ["0"])[0] or 0)
            except ValueError:
                after = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            w = self.wfile

            def emit(kind: str, data: str, eid: int | None = None) -> None:
                head = f"id: {eid}\n" if eid is not None else ""
                w.write(f"{head}event: {kind}\ndata: {data}\n\n".encode("utf-8"))
                w.flush()

            try:
                emit("snapshot", json.dumps(app.state()))
                for ev in app.store.since(after):
                    emit("event", ev.to_json(), ev.id)
                    after = ev.id
                last_state = time.time()
                while not app.stopping:
                    new = app.store.wait(after, 3.0)
                    for ev in new:
                        emit("event", ev.to_json(), ev.id)
                        after = ev.id
                    if time.time() - last_state >= 2.0:
                        emit("state", json.dumps(app.state()))
                        last_state = time.time()
                    if not new:
                        w.write(b": keepalive\n\n")
                        w.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


class ConsoleServer:
    def __init__(self, app: App, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.app = app
        self.httpd = ThreadingHTTPServer((host, port), make_handler(app))
        self.httpd.daemon_threads = True
        self.poller = threading.Thread(target=app.poll_forever, name="arena-poller", daemon=True)

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> None:
        self.poller.start()
        threading.Thread(target=self.httpd.serve_forever, name="arena-http", daemon=True).start()

    def stop(self) -> None:
        self.app.stopping = True
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.store.close()
