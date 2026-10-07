"""What every experiment harness shares: run directories, atomic run files, code stamps and the dry-run contract.

Run files live in ``runs/<kind>/<run_id>/`` (``runs/`` is git-ignored except its ``.gitignore``). A run directory is
never reused: an existing one is refused and the harness exits 2, so a result cannot be silently overwritten.

Every result carries the code stamps (``code_commit``, ``code_dirty``, ``code_hash`` over every ``*.py`` under
``mycelic/collective``) and a ``measurement`` flag. :func:`measurement_flag` is false whenever a fake was involved:
a fake provider in the routing, a ledger row whose response carried ``X-Mycelic-Fake: 1``, or a model listing
that said ``fake``. It is never inferred from the host name.

Dry-run contract, the same for every CLI: ``--dry-run`` parses the arguments and validates every input that
exists (routing is loaded with ``check_env=False``; caches, samples and sheets are checked for structure), then
prints ``dry-run: <cli>``, one ``would need: ...`` line per missing input, environment variable or network host,
and one ``would write: ...`` line per output. It exits 0 when inputs are only missing and 2 when an existing input
is invalid or the run directory exists. It opens no socket, runs no subprocess and creates no file or directory.

:func:`utc_clock` is the wall clock the harnesses stamp results with; the runtime itself only ever receives it as
an injected function.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..inference.routing import Endpoint
from ..jsonio import canonical_dumps, sha256_hex

ROOT = Path(__file__).resolve().parents[3]
COLLECTIVE_DIR = ROOT / "mycelic" / "collective"
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)


class UsageError(ValueError):
    """A configuration or usage problem: the CLI prints it and exits 2."""


def check_run_id(run_id: str) -> str:
    if RUN_ID_RE.fullmatch(run_id or "") is None:
        raise UsageError("--run-id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}") from None
    return run_id


def run_dir(runs_dir: str | Path, kind: str, run_id: str) -> Path:
    """``<runs_dir>/<kind>/<run_id>``; refuses an existing directory. Does not create it."""
    path = Path(runs_dir) / kind / check_run_id(run_id)
    if path.exists():
        raise UsageError(f"run directory already exists: {path} (pick a new --run-id)") from None
    return path


def write_json_atomic(path: str | Path, obj: Any) -> str:
    """Canonical JSON plus a newline, through a temporary file and ``os.replace``. Returns the file's sha256."""
    path = Path(path)
    data = (canonical_dumps(obj) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return sha256_hex(data)


def utc_clock() -> str:
    """ISO 8601 UTC with milliseconds and ``Z``."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def code_paths(root: Path = ROOT) -> list[Path]:
    return sorted((root / "mycelic" / "collective").rglob("*.py"))


def code_hash(paths: Iterable[Path] | None = None, root: Path = ROOT) -> str:
    """sha256 over the sorted relative POSIX paths, each as ``path + NUL + 8-byte big-endian length + bytes``."""
    entries = []
    for p in (code_paths(root) if paths is None else paths):
        p = Path(p)
        entries.append((p.resolve().relative_to(root.resolve()).as_posix(), p.read_bytes()))
    h = hashlib.sha256()
    for rel, data in sorted(entries):
        h.update(rel.encode("utf-8") + b"\0" + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def code_commit() -> str:
    r = _git("-C", str(ROOT), "rev-parse", "HEAD")
    sha = r.stdout.strip() if r is not None and r.returncode == 0 else ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha, re.ASCII) else "unknown"


def code_dirty() -> bool | str:
    r = _git("--no-optional-locks", "-C", str(ROOT), "status", "--porcelain", "--", "mycelic/collective")
    if r is None or r.returncode != 0:
        return "unknown"
    return bool(r.stdout.strip())


def code_stamps() -> dict[str, Any]:
    return {"code_commit": code_commit(), "code_dirty": code_dirty(), "code_hash": code_hash()}


def measurement_flag(endpoints: Iterable[Endpoint], rows: Iterable[dict[str, Any]], models_fake: bool) -> bool:
    if models_fake:
        return False
    if any(e.provider == "fake" for e in endpoints):
        return False
    return not any(r.get("fake_marker") for r in rows)


class DryRun:
    """Collects ``would need`` and ``would write`` lines and prints them in the contract's format."""

    def __init__(self, cli: str) -> None:
        self.cli = cli
        self.needs: list[str] = []
        self.writes: list[str] = []

    def need(self, what: str) -> None:
        if what not in self.needs:
            self.needs.append(what)

    def write(self, what: str) -> None:
        self.writes.append(what)

    def emit(self) -> int:
        print(f"dry-run: {self.cli}")
        for line in self.needs:
            print(f"would need: {line}")
        for line in self.writes:
            print(f"would write: {line}")
        return 0


def fail(message: str, code: int = 2) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]
