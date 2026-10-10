"""The model server's lifecycle on a runner: start it on loopback, prove it is what was asked for, watch it, stop it.

:class:`ModelServer` runs the pinned server binary (extracted fresh by ``lab.shard prepare``) as::

    <binary> -m <gguf> --alias <alias> --host 127.0.0.1 --port <p> -t <T> -tb <TB> -np <slots> -c <slots x ctx>
             --seed <s> --cache-ram <MiB> --fit off --offline --no-webui --metrics <allowlisted manifest args>

and nothing else (:func:`server_argv`). The process gets exactly four environment variables (:func:`server_env`:
``PATH``, ``HOME``, ``LANG``, ``TMPDIR``; no token, key or server-argument variable reaches it), runs in its own
session with ``OUT/server/tmp`` as its working directory, and logs to ``OUT/server/server-<key>-<index>.log``.

``start`` returns the start record once the server is ready:

1. it polls ``GET /health`` every :data:`HEALTH_POLL_S` (connection refused and 503 mean loading) until 200 or the
   health deadline; a process that exits first fails the start (:data:`~lab.notes.SERVER_START_FAILED`), unless it
   exited within :data:`BIND_WINDOW_S` with a bind error in its log, which retries on a new port (at most
   :data:`PORT_ATTEMPTS` ports);
2. ``GET /props`` must report the requested per-slot context (``default_generation_settings.n_ctx``), slot count
   (``total_slots``) and model path (``model_path``, exactly the ``-m`` string), in that order;
3. ``GET /v1/models`` must list the alias.

``build_info`` (and whether it starts with ``<tag>-``), the backend lines and the first ``system_info`` line of the
log are recorded, never asserted. Any failure, an interrupt included, stops the server before it propagates; a
:class:`ServerError` carries a fixed problem (it becomes a unit's ``status_reason``) and the log tail as detail (it
goes only into the start record). ``stop`` is idempotent: SIGTERM to the process group, :data:`STOP_GRACE_S` for the
leader, SIGKILL to the group, then the log is cut to its last :data:`SERVER_LOG_CAP` bytes. A killed process closes its
sockets a moment before it can be reaped, so a caller whose requests just went unanswered polls with
:data:`EXIT_GRACE_S` before it concludes the server still runs (:func:`exit_reason` then names the exit).

:class:`FakeServer` has the same surface over the collective's in-process fake (plumbing runs). HTTP to either goes
through :func:`loopback_json` or the collective client: plain HTTP to ``127.0.0.1``, never through a proxy and never
with a credential.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from mycelic.collective.experiments.common import utc_clock
from mycelic.collective.inference.fakeserver import FakeOpenAIServer
from mycelic.collective.packs.loader import FrozenPack

from . import display_path, hostinfo
from .notes import (ALIAS_MISMATCH, OOM_HINT, SERVER_EXITED, SERVER_NOT_HEALTHY, SERVER_SETTINGS,
                    SERVER_START_FAILED)
from .responder import Responder

LOOPBACK = "127.0.0.1"
HEALTH_POLL_S = 0.5
HEALTH_DEADLINE_S = 300
BIND_WINDOW_S = 5
PORT_ATTEMPTS = 3
WATCH_INTERVAL_S = 2
STOP_GRACE_S = 10
LOG_TAIL_BYTES = 4096
SERVER_LOG_CAP = 1 << 20
MIN_MEM_AFTER_LOAD = 1536 << 20
AFTER_UNIT_HEALTH_S = 30
EXIT_GRACE_S = 2
VERSION_TIMEOUT_S = 30
VERSION_LINE_CAP = 200
LOG_LINE_CAP = 400
MAX_BACKEND_LINES = 10
MAX_LISTED = 20
BIND_RE = re.compile(r"\bbind\b", re.I)
FAKE_FIRST_TOKEN_S = 0.05
FAKE_TOKEN_S = 0.005


class ServerError(Exception):
    """``problem`` is a short notes sentence (a unit's ``status_reason``); ``detail`` holds the log tail."""

    def __init__(self, problem: str, detail: str = "") -> None:
        super().__init__(problem)
        self.problem = problem
        self.detail = detail

    def __str__(self) -> str:
        return self.problem + "\n" + self.detail


@dataclass(frozen=True)
class ServerSpec:
    binary: Path
    model_path: Path
    alias: str
    tag: str
    slots: int
    ctx_per_slot: int
    seed: int
    threads: int
    threads_batch: int
    cache_ram_mib: int
    extra_args: tuple[str, ...]


# --------------------------------------------------------------------------------------------------- pure parts

def server_env(environ: Mapping[str, str], home: str, tmpdir: str) -> dict[str, str]:
    """Exactly the four variables the server binary (and its ``--version``) may see."""
    return {"PATH": environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "LANG": "C.UTF-8", "TMPDIR": tmpdir}


def thread_counts(host: Mapping[str, Any], mode: str) -> tuple[int, int]:
    """(``-t``, ``-tb``) from a host record (``nproc_available``, ``cpu.physical_cores``): batch threads are the
    CPUs this process may use; generation threads the physical cores among them (``physical``) or all (``logical``)."""
    tb = host.get("nproc_available") or os.cpu_count() or 1
    cpu = host.get("cpu")
    physical = cpu.get("physical_cores") if isinstance(cpu, Mapping) else None
    if mode == "physical" and isinstance(physical, int) and physical >= 1:
        return min(physical, tb), tb
    return tb, tb


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]


def server_argv(spec: ServerSpec, port: int) -> list[str]:
    return [str(spec.binary), "-m", str(spec.model_path), "--alias", spec.alias, "--host", LOOPBACK,
            "--port", str(port), "-t", str(spec.threads), "-tb", str(spec.threads_batch), "-np", str(spec.slots),
            "-c", str(spec.slots * spec.ctx_per_slot), "--seed", str(spec.seed), "--cache-ram", str(spec.cache_ram_mib),
            "--fit", "off", "--offline", "--no-webui", "--metrics", *spec.extra_args]


def printable(text: str, cap: int) -> str:
    """Printable ASCII only, cut to ``cap`` characters: what the lab keeps of a line the server or binary wrote."""
    return "".join(c for c in text if 0x20 <= ord(c) <= 0x7e)[:cap]


def loopback_json(port: int, method: str, path: str, body: Any = None,
                  timeout_s: float = 10) -> tuple[int | None, Any]:
    """One request to ``127.0.0.1:<port>`` (no proxy, no credential); (status or None, parsed JSON or None)."""
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout_s)
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(16 << 20)
    except (OSError, http.client.HTTPException, ValueError):
        return None, None
    finally:
        conn.close()
    try:
        obj = json.loads(raw) if raw else None
    except (ValueError, RecursionError):
        obj = None
    return resp.status, obj


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _exit_of(code: int | None) -> dict[str, Any]:
    if code is not None and code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        return {"code": None, "signal": name}
    return {"code": code, "signal": None}


def describe_exit(exit_: Mapping[str, Any]) -> str:
    return exit_["signal"] if exit_.get("signal") else f"exit code {exit_.get('code')}"


def exit_reason(exit_: Mapping[str, Any]) -> str:
    """The reason given for what a server that exited unplanned served: its exit, and for SIGKILL the out-of-memory
    hint."""
    reason = f"{SERVER_EXITED} {describe_exit(exit_)}"
    return f"{reason}; {OOM_HINT}" if exit_.get("signal") == "SIGKILL" else reason


def run_version(binary: Path, env: dict[str, str], cwd: Path,
                timeout_s: float = VERSION_TIMEOUT_S) -> tuple[int | None, str]:
    """``<binary> --version`` in its own session; (exit code, or None on a timeout, and its first non-empty output
    line, stdout before stderr, kept as :func:`printable`)."""
    proc = subprocess.Popen([str(binary), "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, cwd=cwd, start_new_session=True, close_fds=True)
    try:
        out, err = proc.communicate(timeout=timeout_s)
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        code = None
    except BaseException:
        _signal_group(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    _signal_group(proc.pid, signal.SIGKILL)
    for stream in (out, err):
        for line in stream.decode("utf-8", "replace").splitlines():
            if line.strip():
                return code, printable(line.strip(), VERSION_LINE_CAP)
    return code, ""


def _cap_file(path: Path, cap: int) -> None:
    try:
        size = path.stat().st_size
        if size > cap:
            with open(path, "rb") as fh:
                fh.seek(size - cap)
                tail = fh.read()
            path.write_bytes(tail)
    except OSError:
        pass


# --------------------------------------------------------------------------------------------------- the server

class ModelServer:
    def __init__(self, spec: ServerSpec, *, out: Path, key: str, index: int,
                 health_deadline_s: float = HEALTH_DEADLINE_S, environ: Mapping[str, str] = os.environ,
                 meminfo: Callable[[], int | None] = hostinfo.mem_available) -> None:
        self.spec = spec
        self.out = Path(out)
        self.key = key
        self.index = index
        self.health_deadline_s = health_deadline_s
        self.environ = environ
        self.meminfo = meminfo
        self.proc: subprocess.Popen[bytes] | None = None
        self.port: int | None = None
        self._exit: dict[str, Any] | None = None
        self.log_path = self.out / "server" / f"server-{key}-{index}.log"
        self.record: dict[str, Any] = {
            "index": index, "class": None, "units": [], "slots": spec.slots, "ctx_per_slot": spec.ctx_per_slot,
            "ctx_total": spec.slots * spec.ctx_per_slot, "threads": spec.threads,
            "threads_batch": spec.threads_batch, "seed": spec.seed, "argv": [], "port": None, "port_attempts": 0,
            "pid": None, "started_at": None, "ready_s": None, "health_503": 0, "props": None,
            "build_info_matches_tag": None, "models_listed": None, "backend_lines": [], "system_info": None,
            "warmup": None, "mem_available_after_load": None, "problem": None,
            "log": display_path(self.log_path, self.out), "log_tail": None, "exit": None, "restart": False,
            "stopped_at": None,
        }

    # ------------------------------------------------------------------ surface
    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}/v1"

    @property
    def host_label(self) -> str:
        return f"{LOOPBACK}:{self.port}"

    @property
    def alias(self) -> str:
        return self.spec.alias

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    def poll(self, wait_s: float = 0.0) -> dict[str, Any] | None:
        """None while the server runs, else ``{"code", "signal"}``. ``wait_s`` waits that long for an exit: a killed
        process closes its sockets a moment before it can be reaped, so a caller whose requests just went unanswered
        waits a little before concluding the server is still alive."""
        if self.proc is None:
            return {"code": None, "signal": None}
        try:
            code = self.proc.wait(timeout=wait_s) if wait_s > 0 else self.proc.poll()
        except subprocess.TimeoutExpired:
            code = None
        return None if code is None else _exit_of(code)

    def health(self, timeout_s: float) -> int | None:
        """Poll ``/health`` until it answers 200 or ``timeout_s`` passes; the last status (None: no answer)."""
        deadline = time.monotonic() + timeout_s
        status = None
        while True:
            if self.poll() is not None:
                return status
            status, _ = loopback_json(self.port or 0, "GET", "/health", timeout_s=max(0.5, min(5.0, timeout_s)))
            if status == 200 or time.monotonic() >= deadline:
                return status
            time.sleep(HEALTH_POLL_S)

    def tail(self, offset: int = 0) -> str:
        try:
            size = self.log_path.stat().st_size
            with open(self.log_path, "rb") as fh:
                fh.seek(max(offset, size - LOG_TAIL_BYTES))
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

    # ------------------------------------------------------------------ start
    def start(self) -> dict[str, Any]:
        try:
            return self._start()
        except ServerError as err:
            self.stop()
            self.record["problem"] = err.problem
            self.record["log_tail"] = err.detail
            raise
        except BaseException:
            self.stop()
            raise

    def _fail(self, problem: str, offset: int = 0) -> ServerError:
        self.stop()
        return ServerError(problem, self.tail(offset))

    def _start(self) -> dict[str, Any]:
        spec = self.spec
        server_dir = self.out / "server"
        home, tmp = server_dir / "home", server_dir / "tmp"
        for directory in (home, tmp):
            shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True)
        env = server_env(self.environ, str(home), str(tmp))
        t0 = time.monotonic()
        deadline = t0 + self.health_deadline_s
        self.record["started_at"] = utc_clock()
        for attempt in range(1, PORT_ATTEMPTS + 1):
            self.port = free_port()
            argv = server_argv(spec, self.port)
            offset = self.log_path.stat().st_size if self.log_path.exists() else 0
            try:
                with open(self.log_path, "ab") as log:
                    self.proc = subprocess.Popen(argv, env=env, cwd=tmp, stdin=subprocess.DEVNULL, stdout=log,
                                                 stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            except OSError as exc:
                raise ServerError(f"{SERVER_START_FAILED}: {exc.__class__.__name__}") from None
            launched = time.monotonic()
            self._exit = None
            self.record.update(port=self.port, port_attempts=attempt, pid=self.proc.pid,
                               argv=[display_path(spec.binary, self.out), *argv[1:]])
            if self._wait_healthy(deadline, offset):
                break
            exit_ = self.poll() or {"code": None, "signal": None}
            bind = time.monotonic() - launched <= BIND_WINDOW_S and BIND_RE.search(self.tail(offset)) is not None
            if bind and attempt < PORT_ATTEMPTS:
                self.stop()
                continue
            raise self._fail(f"{SERVER_START_FAILED}: {describe_exit(exit_)}", offset)
        self.record["ready_s"] = round(time.monotonic() - t0, 3)
        self._check_props()
        self._check_models()
        self._read_log()
        return self.record

    def _wait_healthy(self, deadline: float, offset: int) -> bool:
        """True once ``/health`` answers 200; False when the process exited first."""
        while True:
            if self.poll() is not None:
                return False
            status, _ = loopback_json(self.port or 0, "GET", "/health", timeout_s=2)
            if status == 200:
                return True
            if status == 503:
                self.record["health_503"] += 1
            if time.monotonic() >= deadline:
                raise self._fail(SERVER_NOT_HEALTHY, offset)
            time.sleep(HEALTH_POLL_S)

    def _check_props(self) -> None:
        spec = self.spec
        status, props = loopback_json(self.port or 0, "GET", "/props")
        props = props if status == 200 and isinstance(props, dict) else {}
        settings = props.get("default_generation_settings")
        n_ctx = settings.get("n_ctx") if isinstance(settings, dict) else None
        build_info = props.get("build_info")

        def text(value: Any) -> str | None:
            return printable(value, LOG_LINE_CAP) if isinstance(value, str) else None

        self.record["props"] = {"n_ctx": n_ctx if isinstance(n_ctx, int) else None,
                                "total_slots": props.get("total_slots") if isinstance(props.get("total_slots"), int)
                                else None,
                                "model_path": text(props.get("model_path")), "build_info": text(build_info),
                                "model_alias": text(props.get("model_alias"))}
        self.record["build_info_matches_tag"] = (isinstance(build_info, str)
                                                 and build_info.startswith(spec.tag + "-"))
        for field, got, want in (("n_ctx", n_ctx, spec.ctx_per_slot), ("total_slots", props.get("total_slots"),
                                                                         spec.slots),
                                 ("model_path", props.get("model_path"), str(spec.model_path))):
            if got != want or isinstance(got, bool):
                raise self._fail(f"{SERVER_SETTINGS}: {field}")

    def _check_models(self) -> None:
        status, listing = loopback_json(self.port or 0, "GET", "/v1/models")
        data = listing.get("data") if status == 200 and isinstance(listing, dict) else None
        ids = [entry["id"] for entry in data if isinstance(entry, dict) and isinstance(entry.get("id"), str)] \
            if isinstance(data, list) else []
        self.record["models_listed"] = [printable(i, LOG_LINE_CAP) for i in ids[:MAX_LISTED]]
        if self.spec.alias not in ids:
            raise self._fail(ALIAS_MISMATCH)

    def _read_log(self) -> None:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        lines = text.splitlines()
        self.record["backend_lines"] = [printable(line, LOG_LINE_CAP) for line in lines
                                        if "load_backend" in line][:MAX_BACKEND_LINES]
        self.record["system_info"] = next((printable(line, LOG_LINE_CAP) for line in lines if "system_info" in line),
                                          None)

    # ------------------------------------------------------------------ stop
    def stop(self) -> dict[str, Any] | None:
        """Idempotent; the exit record ``{"code", "signal", "planned"}`` (planned: the server was still running)."""
        if self.proc is None:
            return None
        if self._exit is None:
            planned = self.proc.poll() is None
            if planned:
                _signal_group(self.proc.pid, signal.SIGTERM)
                try:
                    self.proc.wait(timeout=STOP_GRACE_S)
                except subprocess.TimeoutExpired:
                    _signal_group(self.proc.pid, signal.SIGKILL)
                    self.proc.wait()
            _signal_group(self.proc.pid, signal.SIGKILL)
            self._exit = {**_exit_of(self.proc.returncode), "planned": planned}
            self.record["exit"] = self._exit
            self.record["stopped_at"] = utc_clock()
            _cap_file(self.log_path, SERVER_LOG_CAP)
        return self._exit


class FakeServer:
    """The collective's in-process fake behind the same surface as :class:`ModelServer` (plumbing runs)."""

    def __init__(self, persona: str, pack: FrozenPack | None, pack_free_judge: bool = False) -> None:
        self._server = FakeOpenAIServer(persona, responder=Responder(pack, pack_free_judge=pack_free_judge),
                                        first_token_s=FAKE_FIRST_TOKEN_S, token_s=FAKE_TOKEN_S)

    def start(self) -> dict[str, Any]:
        self._server.start()
        return {}

    def poll(self) -> dict[str, Any] | None:
        return None

    def health(self, timeout_s: float = 0) -> int:
        return 200

    def stop(self) -> None:
        self._server.stop()

    @property
    def base_url(self) -> str:
        return self._server.base_url

    @property
    def host_label(self) -> str:
        return f"{LOOPBACK}:{self._server.port}"

    @property
    def alias(self) -> str:
        return self._server.model_id
