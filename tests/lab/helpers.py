"""Shared fixtures for the lab tests: paths, request builders, an in-process plan runner and throwaway git worlds.

Every git call passes ``-c user.name=lab -c user.email=lab@invalid`` and every repository is created with ``-b
main``, so the tests run with an empty ``HOME``, ``GIT_CONFIG_NOSYSTEM=1`` and ``GIT_CONFIG_GLOBAL=/dev/null``.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "tests" / "lab" / "data"
MANIFEST_TEST = DATA / "manifest-test.json"
PLUMBING_MIN = DATA / "requests" / "plumbing-min.json"
GIT_IDENTITY = ("-c", "user.name=lab", "-c", "user.email=lab@invalid")


def plumbing_min() -> dict[str, Any]:
    return json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))


def request_obj(**changes: Any) -> dict[str, Any]:
    """plumbing-min with top-level keys replaced (a value of ``None`` deletes the key)."""
    obj = copy.deepcopy(plumbing_min())
    for key, value in changes.items():
        if value is None:
            obj.pop(key, None)
        else:
            obj[key] = value
    return obj


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    return path


def run_plan(argv: list[str]) -> tuple[int, str, str]:
    """``lab.plan.main`` in-process with stdout and stderr captured."""
    from lab import plan

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = plan.main(argv)
    return code, out.getvalue(), err.getvalue()


def make_plan(directory: Path, request: dict[str, Any], manifest: Path = MANIFEST_TEST,
              name: str = "lab-test") -> tuple[dict[str, Any], Path]:
    """Plan ``request`` (written to ``directory/<name>.json``) into ``directory/plan``; returns (plan, plan path)."""
    path = write_json(directory / f"{name}.json", request)
    code, out, err = run_plan(["--request", str(path), "--manifest", str(manifest), "--out", str(directory / "plan")])
    if code != 0:
        raise AssertionError(f"plan failed: {err}")
    plan_path = directory / "plan" / "plan.json"
    return json.loads(plan_path.read_text(encoding="utf-8")), plan_path


def lab_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return env


def lab_cli(*args: str, cwd: Path = ROOT, timeout: float = 300, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", *args], cwd=cwd, env=lab_env(), capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL, **kwargs)


def pids_mentioning(text: str) -> list[int]:
    """Live processes whose command line contains ``text`` (this process excluded)."""
    found = []
    needle = text.encode("utf-8")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if needle in cmdline and state != "Z":
            found.append(int(entry.name))
    return found


def kill_mentioning(text: str) -> None:
    for pid in pids_mentioning(text):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def wait_until(predicate: Any, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


# --------------------------------------------------------------------------------------------------- git worlds

def git_run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd, capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = git_run(*args, cwd=cwd)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


class GitWorld:
    """A bare ``origin`` with ``main``, a ``work`` clone to commit and push from, and fresh ``--no-local`` clones that
    play the workflow checkout (full history, every branch as ``origin/<branch>``, HEAD detached at the pushed tip;
    unreachable objects are not copied, as with a real fetch)."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        self._clones = 0
        self._events = 0
        git("init", "-q", "--bare", "-b", "main", str(self.origin))
        git("init", "-q", "-b", "main", str(self.work))
        self.git("remote", "add", "origin", str(self.origin))
        self.base = self.commit({"README.md": "base\n"}, message="base")
        self.push("main")

    def git(self, *args: str, check: bool = True) -> str:
        return git(*args, cwd=self.work, check=check)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def commit(self, files: dict[str, str] | None = None, delete: tuple[str, ...] = (), message: str = "change",
               rename: tuple[str, str] | None = None) -> str:
        for rel, content in (files or {}).items():
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.git("add", "--", rel)
        for rel in delete:
            self.git("rm", "-q", "--", rel)
        if rename is not None:
            self.git("mv", "--", *rename)
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def push(self, branch: str, force: bool = False) -> str:
        self.git("push", "-q", *(["--force"] if force else []), "origin", f"HEAD:refs/heads/{branch}")
        return self.head()

    def clone(self, sha: str) -> Path:
        self._clones += 1
        path = self.tmp / f"plan-{self._clones}"
        git("clone", "-q", "--no-local", str(self.origin), str(path))
        git("checkout", "-q", "--detach", sha, cwd=path)
        return path

    def event(self, **fields: Any) -> Path:
        self._events += 1
        path = self.tmp / f"event-{self._events}.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        return path

    def push_event(self, *, before: str, after: str, branch: str, deleted: bool = False,
                   default_branch: str = "main", ref: str | None = None) -> Path:
        return self.event(before=before, after=after, ref=ref or f"refs/heads/{branch}", deleted=deleted,
                          repository={"default_branch": default_branch})
