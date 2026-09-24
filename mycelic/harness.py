"""Deployment drivers shared by the production smoke test and the demo.

Both drive a *real* deployment (no mocks): ``ProcessDriver`` runs ``nats-server`` and ``python -m mycelic serve``
as subprocesses in a temporary directory (what CI uses); ``ComposeDriver`` drives ``docker compose`` against
``deploy/mycelic/docker-compose.yml`` (what an operator runs).  Each exposes the same operations, so one scenario
can be executed against either::

    d = ProcessDriver() | ComposeDriver()
    d.up()                        # start everything, wait for /ready
    d.kill("mycelic")             # SIGKILL / docker compose kill
    d.start("mycelic")            # restart the service, wait for /ready
    d.kill("nats"); d.start("nats")
    d.wipe_database()             # lose the Mycelic database (service down), keep the NATS volume
    d.base_url, d.admin_token, d.logs("mycelic")
    d.down()
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "deploy" / "mycelic" / "docker-compose.yml"
RULES_FILE = REPO_ROOT / "deploy" / "mycelic" / "rules.json"


def secret_hex(nbytes: int) -> str:
    """A hex secret that always contains a letter: nats-server's config parser reads an all-digit value as a number."""
    value = secrets.token_hex(nbytes)
    return value if any(c in string.ascii_letters for c in value) else "a" + value[1:]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, *, timeout: float = 60.0, status: int = 200, headers: dict[str, str] | None = None) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == status:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
                last = f"status {resp.status}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code == status:
                return {}
        except Exception as exc:  # connection refused while starting
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    raise TimeoutError(f"{url} did not answer {status} within {timeout}s (last: {last})")


class Driver:
    name = "abstract"
    base_url: str
    admin_token: str

    def up(self) -> None: ...
    def down(self) -> None: ...
    def kill(self, service: str) -> None: ...
    def start(self, service: str) -> None: ...
    def wipe_database(self) -> None: ...
    def logs(self, service: str, tail: int = 50) -> str: ...

    def wait_ready(self, timeout: float = 60.0) -> None:
        wait_http(f"{self.base_url}/ready", timeout=timeout)

    def wait_health(self, timeout: float = 60.0) -> dict[str, Any]:
        return wait_http(f"{self.base_url}/health", timeout=timeout)


class ProcessDriver(Driver):
    """nats-server + mycelic as local subprocesses (needs the nats-server binary)."""

    name = "process"

    def __init__(self, *, workdir: str | Path | None = None, nats_bin: str | None = None, python: str = sys.executable,
                 min_support: int = 2, log_level: str = "INFO") -> None:
        self.nats_bin = nats_bin or os.environ.get("MYCELIC_NATS_SERVER_BIN") or shutil.which("nats-server")
        if not self.nats_bin:
            raise RuntimeError("nats-server not found: set MYCELIC_NATS_SERVER_BIN or put nats-server on PATH")
        self._tmp = None
        if workdir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="mycelic-smoke-")
            workdir = self._tmp.name
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.python = python
        self.nats_port = free_port()
        self.http_port = free_port()
        self.base_url = f"http://127.0.0.1:{self.http_port}"
        self.admin_token = secrets.token_hex(32)
        self.signing_key = secrets.token_hex(32)
        self.nats_user, self.nats_password = "mycelic", secret_hex(16)
        self.min_support = min_support
        self.log_level = log_level
        self.procs: dict[str, subprocess.Popen] = {}
        (self.workdir / "js").mkdir(exist_ok=True)
        (self.workdir / "nats.conf").write_text(
            f'port: {self.nats_port}\nhttp_port: -1\njetstream {{ store_dir: "{self.workdir / "js"}" }}\n'
            f'authorization {{ user: "{self.nats_user}", password: "{self.nats_password}" }}\n')

    # --- lifecycle
    def up(self) -> None:
        self.start("nats")
        self.start("mycelic")

    def down(self) -> None:
        for name in list(self.procs):
            self.kill(name)
        if self._tmp is not None:
            self._tmp.cleanup()

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("MYCELIC_")}
        env.update({
            "PYTHONPATH": str(REPO_ROOT), "MYCELIC_HOST": "127.0.0.1", "MYCELIC_PORT": str(self.http_port),
            "MYCELIC_DB_PATH": str(self.workdir / "mycelic.db"), "MYCELIC_NATS_URL": f"nats://127.0.0.1:{self.nats_port}",
            "MYCELIC_NATS_USER": self.nats_user, "MYCELIC_NATS_PASSWORD": self.nats_password,
            "MYCELIC_ADMIN_TOKEN": self.admin_token, "MYCELIC_EVENT_SIGNING_KEY": self.signing_key,
            "MYCELIC_RULES_FILE": str(RULES_FILE), "MYCELIC_MIN_SUPPORT": str(self.min_support),
            "MYCELIC_LOG_LEVEL": self.log_level, "MYCELIC_RATE_LIMIT_RPS": "1000", "MYCELIC_RATE_LIMIT_BURST": "2000",
        })
        return env

    def start(self, service: str) -> None:
        if service in self.procs:
            return
        log = open(self.workdir / f"{service}.log", "ab")
        if service == "nats":
            self.procs[service] = subprocess.Popen([self.nats_bin, "-c", str(self.workdir / "nats.conf")], stdout=log, stderr=subprocess.STDOUT)
            deadline = time.time() + 15
            while time.time() < deadline:
                if self.procs[service].poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", self.nats_port), timeout=0.2):
                        return
                except OSError:
                    time.sleep(0.05)
            raise RuntimeError(f"nats-server did not start (exit={self.procs[service].poll()}):\n{self.logs('nats', 15)}")
        if service == "mycelic":
            self.procs[service] = subprocess.Popen([self.python, "-m", "mycelic", "serve"], env=self.env(), cwd=str(REPO_ROOT),
                                                   stdout=log, stderr=subprocess.STDOUT)
            self.wait_ready(60)
            return
        raise ValueError(service)

    def kill(self, service: str) -> None:
        proc = self.procs.pop(service, None)
        if proc is None:
            return
        proc.kill()                       # SIGKILL: no graceful shutdown, exactly like a crashed pod
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass

    def stop(self, service: str) -> None:
        proc = self.procs.pop(service, None)
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    def wipe_database(self) -> None:
        if "mycelic" in self.procs:
            raise RuntimeError("stop the service before wiping its database")
        for f in self.workdir.glob("mycelic.db*"):
            f.unlink()

    def logs(self, service: str, tail: int = 50) -> str:
        path = self.workdir / f"{service}.log"
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-tail:])


class ComposeDriver(Driver):
    """docker compose against deploy/mycelic/docker-compose.yml, with a generated .env unless one is given."""

    name = "compose"

    def __init__(self, *, compose_file: Path = COMPOSE_FILE, env_file: Path | None = None, project: str | None = None,
                 port: int | None = None, build: bool = True, extra_env: dict[str, str] | None = None) -> None:
        self.compose_file = compose_file
        self.project = project or "mycelic-smoke"
        self.build = build
        self._generated_env = env_file is None
        self.env_file = env_file or compose_file.parent / f".env.{self.project}"
        self.port = port or free_port()
        if self._generated_env:
            self.admin_token = secrets.token_hex(32)
            lines = [f"MYCELIC_ADMIN_TOKEN={self.admin_token}", f"MYCELIC_EVENT_SIGNING_KEY={secrets.token_hex(32)}",
                     "NATS_USER=mycelic", f"NATS_PASSWORD={secret_hex(16)}", f"MYCELIC_PORT={self.port}", "MYCELIC_LOG_LEVEL=INFO"]
            lines += [f"{k}={v}" for k, v in (extra_env or {}).items()]
            self.env_file.write_text("\n".join(lines) + "\n")
        else:
            values = dict(line.split("=", 1) for line in self.env_file.read_text().splitlines() if "=" in line and not line.startswith("#"))
            self.admin_token = values["MYCELIC_ADMIN_TOKEN"].strip()
            self.port = int(values.get("MYCELIC_PORT", "8080"))
        self.base_url = f"http://127.0.0.1:{self.port}"

    def _compose(self, *args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
        cmd = ["docker", "compose", "-p", self.project, "--env-file", str(self.env_file), "-f", str(self.compose_file), *args]
        return subprocess.run(cmd, check=check, capture_output=capture, text=True)

    def up(self) -> None:
        args = ["up", "-d", "--wait", "--wait-timeout", "180"]
        if self.build:
            args.insert(1, "--build")
        self._compose(*args)
        self.wait_ready(120)

    def down(self) -> None:
        self._compose("down", "-v", "--remove-orphans", check=False)
        if self._generated_env and self.env_file.exists():
            self.env_file.unlink()

    def kill(self, service: str) -> None:
        self._compose("kill", service)

    def stop(self, service: str) -> None:
        self._compose("stop", service)

    def start(self, service: str) -> None:
        self._compose("start", service)
        if service == "mycelic":
            self.wait_ready(120)
        else:
            time.sleep(1.0)

    def wipe_database(self) -> None:
        # the service must be stopped; delete the SQLite files inside the named volume, keep the NATS volume
        volume = f"{self.project}_mycelic-data"
        subprocess.run(["docker", "run", "--rm", "--user", "root", "--entrypoint", "sh", "-v", f"{volume}:/data", "mycelic:local",
                        "-c", "rm -f /data/mycelic.db*"], check=True)

    def logs(self, service: str, tail: int = 50) -> str:
        return self._compose("logs", "--no-color", "--tail", str(tail), service, check=False, capture=True).stdout
