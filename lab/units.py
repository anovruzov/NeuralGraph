"""One unit: an experiment harness run as a subprocess against one model server, and the record of what happened.

Inside a shard's output directory ``OUT``, a unit writes::

    routing/<unit>.json                      the routing file the harness reads (one endpoint, ``lab``)
    units/<unit>/{stdout.log,stderr.log}     the harness output, each cut to its last 64 KiB; never printed
    runs/<e3|g0>/<run id>/...                the allowlisted harness outputs, copied from the scratch directory
    work/<unit>/                             the harness scratch directory, deleted when the unit ends

and returns the record the shard writes to ``units/<unit>/unit.json``. The model server is either the shard's
started model server (a :class:`Serving` handle: its loopback URL, alias and the provenance evidence) or, for fake
entries and the ``--provider fake`` override, the collective's ``FakeOpenAIServer`` inside this process, answered by
:class:`~lab.responder.Responder` (``server.FakeServer``). Either way the routing file names it as an
``openai_compat`` endpoint (E3 refuses fake routing by design), so the harness talks HTTP to it the same way. While
a model server serves the unit, the harness is watched: if the server process exits, the harness group is stopped,
and a unit whose server exited while it ran (seen by the watch or right after the harness ended) is ``invalid``
(``server exited with ...``; SIGKILL adds the out-of-memory hint).

Adapters (``ADAPTERS``): ``e3`` runs ``experiments.e3_latency`` (boundary ``central``) and keeps ``e3.json`` and
``ledger.jsonl``; ``g0`` runs ``experiments.g0_canary --mode routing`` (boundary ``any-simulated``, both routed
tasks on the endpoint) and keeps ``leakage.json`` and ``edge/site-*.ledger.jsonl``. Nothing else is collected: no
``private/``, no SQLite file, no ``hq*`` or ``followup/`` directory.

Status, from :func:`harness_status` and then the participation check:

* ``timed_out`` whenever the wait timed out; ``interrupted`` for exit 130 or SIGINT; ``failed`` for another signal,
  for exit 2 (the harness refused its configuration) and for any exit the adapter does not expect;
* E3: exit 0 with an ``e3.json`` of kind ``e3`` is ``ok``, anything else ``failed``;
* G0: exit 0 with ``passed: true`` is ``ok``, exit 1 with ``passed: false`` is ``result_fail`` (a valid FAIL
  verdict), any other pairing of exit 0/1 and ``leakage.json`` (missing, unreadable, contradicting) is ``failed``;
* participation: an ``ok`` or ``result_fail`` unit with a model is ``invalid`` unless the model answered. Per task
  in the ledgers, ``attempted`` counts calls (rows with attempt 0 or 1; a repair or escalation row is part of the
  same call) and ``ok`` the rows that succeeded. A required task (the E3 workload tasks; G0's ``extract_claims``)
  with no call, any task with an ok share below 0.95, or an E3 run with more than 5% failed measured requests
  makes the unit invalid. A harness that exits 0 although the model never answered (E3 counts its failures, G0
  falls back to the lexical extractor) can therefore never be ``ok``.

The measurement class (:func:`measurement_class`) says what the numbers are: ``plumbing`` when a fake answered (a
fake manifest entry, the ``--provider fake`` override, or any ledger row carrying the fake-server marker),
``no-model`` for a model-free unit; otherwise ``model`` only when every condition of :func:`model_checks` holds, in
this order: the model file was verified by the hub or the lock and its record is the one prepare used
(``model_verified``); the server archive likewise (``server_verified``; trusted on first use adds the flag
``server_first_use``); every download connection reached the host it named and none was private
(``download_hosts``); the server reported the verified file as its model (``model_path``); every ledger row went to
the started server (``ledger_host``) and every answer named the alias (``model_served``); the harness counted the
run as a measurement (``harness_measurement``, E3 only: its result says ``measurement: true``,
:func:`harness_verdict`); and the unit ran to a valid result (``participation``).
Otherwise ``unverified``, naming the first condition that failed (``no_evidence`` without a model server). G0's own
``models_fake`` is ignored: in routing mode it is false even against the fake HTTP server.

Summaries and the aggregate report never trust a record's ``measurement_class`` alone: :func:`display_class` re-reads
the record and its shard's provenance and puts each unit in one of :data:`DISPLAY_CLASSES`; ``model`` needs every
fact that admits a measurement to hold at once.

The harness runs with an allowlisted environment (:data:`ENV_ALLOWLIST` plus ``PYTHONUNBUFFERED=1``; no token or
key), in its own session; on a timeout, a watch that fired or an exception (a shard interrupt included) the whole
process group gets SIGTERM, then SIGKILL after 10 s, and after every exit the group is SIGKILLed again to remove
stragglers.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from mycelic.collective.edge.extract import TASK_NAME
from mycelic.collective.edge.verify import JUDGE_TASK
from mycelic.collective.experiments.common import utc_clock, write_json_atomic
from mycelic.collective.experiments.e3_latency import WORKLOADS
from mycelic.collective.inference.client import is_private_host
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import ConfigError, load_routing
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs.loader import load_pack

from . import ROOT
from .notes import (E3_FAILURES, FAKE_SERVER_NOTE, HARNESS_INTERRUPTED, HARNESS_USAGE, KILLED_BY_SIGNAL, LAB_ROUTING,
                    LOW_PARTICIPATION, NO_MODEL_CALLS, RESULT_CONTRADICTS_EXIT, RESULT_MISSING, SHARD_INTERRUPTED,
                    TIMED_OUT, UNEXPECTED_EXIT)
from .server import EXIT_GRACE_S, WATCH_INTERVAL_S, FakeServer, exit_reason

ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY",
                 "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
STATUSES = ("ok", "result_fail", "invalid", "failed", "timed_out", "interrupted", "skipped")
FAILING = ("invalid", "failed", "timed_out", "interrupted")
MIN_MODEL_OK_SHARE = 0.95
MAX_E3_FAILED_SHARE = 0.05
LOG_CAP_BYTES = 65536
TERM_GRACE_S = 10
ENDPOINT = "lab"
FLAG_NAME_RE = re.compile(r"[a-z][a-z0-9-]*", re.ASCII)
CHECK_KEYS = ("model_verified", "server_verified", "download_hosts", "model_path", "ledger_host", "model_served",
              "harness_measurement", "participation")
DISPLAY_CLASSES = ("model", "unverified", "plumbing", "no-model", "no-result")
MODEL_VERIFIED_BY = ("lock", "hf-api")
SERVER_VERIFIED_BY = ("lock", "github-api", "first-use")


class ShardInterrupted(BaseException):
    """Raised by the shard runner's SIGTERM/SIGINT handler. A BaseException, so no ``except Exception`` swallows it;
    :func:`run_process` attaches the :class:`ProcessResult` of the group it stopped as ``process``."""

    process: "ProcessResult | None" = None


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    signal_name: str | None
    timed_out: bool
    wall_s: float
    pgid: int
    stopped_by_watch: bool = False


@dataclass(frozen=True)
class Serving:
    """A started model server as a unit sees it: where to send calls, what to call it, how to note it in E3, how to
    tell whether it still runs, and the evidence the classifier reads (``server_record``, ``model_record`` and their
    sha256, ``prepare``, ``start``)."""

    base_url: str
    alias: str
    host_label: str
    server_note: str
    poll: Callable[..., dict[str, Any] | None]   # ModelServer.poll(wait_s=0)
    evidence: dict[str, Any]
    start: int
    class_name: str


@dataclass(frozen=True)
class Adapter:
    experiment: str
    module: str
    routed_tasks: tuple[str, ...]
    boundary: str
    deadline_s: int
    result_file: str
    collect: tuple[str, ...]        # glob patterns under the harness run directory
    ledgers: tuple[str, ...]        # the ledger files among them


ADAPTERS = {
    "e3": Adapter("e3", "mycelic.collective.experiments.e3_latency", (), "central", 3600, "e3.json",
                  ("e3.json", "ledger.jsonl"), ("ledger.jsonl",)),
    "g0": Adapter("g0", "mycelic.collective.experiments.g0_canary", (TASK_NAME, JUDGE_TASK), "any-simulated", 900,
                  "leakage.json", ("leakage.json", "edge/site-*.ledger.jsonl"), ("edge/site-*.ledger.jsonl",)),
}


# --------------------------------------------------------------------------------------------------- pure parts

def required_tasks(unit: Mapping[str, Any]) -> list[str]:
    if unit["experiment"] == "e3":
        return [WORKLOADS[w][0].name for w in unit["params"]["workloads"]]
    return [TASK_NAME]


def harness_dir(unit: Mapping[str, Any], out: Path) -> Path:
    """Where the harness writes: E3 under ``--runs-dir`` (``e3/<run id>``), G0 at ``--out``; the same shape."""
    return Path(out) / "work" / unit["unit"] / unit["experiment"] / unit["run_id"]


def flag(name: str, value: Any) -> str:
    if FLAG_NAME_RE.fullmatch(name) is None:
        raise ValueError("flag names match [a-z][a-z0-9-]*") from None
    return f"--{name}={value}"


def build_argv(unit: Mapping[str, Any], out: Path, routing_path: Path,
               server_note: str = FAKE_SERVER_NOTE) -> list[str]:
    """``[python, -m, <module>, --flag=value ...]``: every option in one element, never a bare value, and never
    the harness option that accepts a dirty tree."""
    p = unit["params"]
    if unit["experiment"] == "e3":
        flags = [("routing", routing_path), ("endpoint", ENDPOINT), ("run-id", unit["run_id"]),
                 ("runs-dir", Path(out) / "work" / unit["unit"]), ("boundary", "central"),
                 ("concurrency", ",".join(str(c) for c in p["concurrency"])), ("requests", p["requests"]),
                 ("warmup", p["warmup"]), ("workloads", ",".join(p["workloads"])), ("seed", p["seed"]),
                 ("server-note", server_note)]
    else:
        flags = [("pack", p["pack"]), ("records", p["records"]), ("seed", p["seed"]), ("out", harness_dir(unit, out)),
                 ("mode", "routing"), ("routing", routing_path)]
    return [sys.executable, "-m", ADAPTERS[unit["experiment"]].module, *(flag(n, v) for n, v in flags)]


def routing_doc(unit: Mapping[str, Any], entry: Mapping[str, Any], base_url: str) -> dict[str, Any]:
    adapter = ADAPTERS[unit["experiment"]]
    endpoint = {"provider": "openai_compat", "boundary": adapter.boundary, "base_url": base_url,
                "model": entry["alias"], "response_format": entry["response_format"],
                "transport_schema": entry["transport_schema"], "connect_timeout_s": 5,
                "deadline_s": adapter.deadline_s, "max_retries": 1}
    return {"schema_version": 1, "endpoints": {ENDPOINT: endpoint},
            "routes": {task: {"endpoint": ENDPOINT} for task in adapter.routed_tasks}}


def subprocess_env(environ: Mapping[str, str], extras: list[str]) -> dict[str, str]:
    env = {name: environ[name] for name in ENV_ALLOWLIST if name in environ}
    env["PYTHONUNBUFFERED"] = "1"
    for name in extras:
        if name in environ:
            env[name] = environ[name]
    return env


def harness_status(experiment: str, exit_code: int | None, timed_out: bool,
                   result: Any) -> tuple[str, str | None]:
    """The adapter's reading of an exit code and its result file (parsed JSON or None)."""
    if timed_out:
        return "timed_out", TIMED_OUT
    if exit_code is None:
        return "failed", UNEXPECTED_EXIT
    if exit_code in (130, -signal.SIGINT):
        return "interrupted", HARNESS_INTERRUPTED
    if exit_code < 0:
        return "failed", KILLED_BY_SIGNAL
    if exit_code == 2:
        return "failed", HARNESS_USAGE
    if experiment == "e3":
        if exit_code != 0:
            return "failed", UNEXPECTED_EXIT
        good = isinstance(result, dict) and result.get("kind") == "e3" and isinstance(result.get("cells"), list)
        return ("ok", None) if good else ("failed", RESULT_MISSING)
    if exit_code not in (0, 1):
        return "failed", UNEXPECTED_EXIT
    passed = result.get("passed") if isinstance(result, dict) and result.get("kind") == "g0_leakage" else None
    if not isinstance(passed, bool):
        return "failed", RESULT_MISSING
    if exit_code == 0 and passed:
        return "ok", None
    if exit_code == 1 and not passed:
        return "result_fail", None
    return "failed", RESULT_CONTRADICTS_EXIT


def participation(experiment: str, rows: list[dict[str, Any]], required: list[str],
                  result: Any) -> tuple[dict[str, Any], str | None]:
    """The participation record and the first problem (None when the model answered enough)."""
    counts: dict[str, dict[str, int]] = {task: {"attempted": 0, "ok": 0} for task in required}
    for row in rows:
        c = counts.setdefault(row["task"], {"attempted": 0, "ok": 0})
        if row["attempt"] in (0, 1):
            c["attempted"] += 1
        if row["ok"] is True:
            c["ok"] += 1
    tasks = {task: {**c, "share": round(c["ok"] / c["attempted"], 6) if c["attempted"] else None}
             for task, c in sorted(counts.items())}
    e3 = None
    if experiment == "e3":
        cells = result["cells"] if isinstance(result, dict) and isinstance(result.get("cells"), list) else []
        measured = sum(c.get("measured", 0) for c in cells)
        failed = sum(sum(c.get("failures", {}).values()) for c in cells)
        e3 = {"measured": measured, "failed": failed, "share": round(failed / measured, 6) if measured else None,
              "max_share": MAX_E3_FAILED_SHARE}
    record = {"threshold": MIN_MODEL_OK_SHARE, "required": list(required), "tasks": tasks, "e3": e3}
    for task in required:
        if counts[task]["attempted"] == 0:
            return record, f"{NO_MODEL_CALLS}: {task}"
    for task, c in sorted(counts.items()):
        if c["attempted"] and c["ok"] * 100 < round(MIN_MODEL_OK_SHARE * 100) * c["attempted"]:
            return record, f"{LOW_PARTICIPATION}: {task} ok share {tasks[task]['share']} below {MIN_MODEL_OK_SHARE}"
    if e3 is not None and e3["measured"] and e3["failed"] * 100 > round(MAX_E3_FAILED_SHARE * 100) * e3["measured"]:
        return record, f"{E3_FAILURES}: {e3['failed']} of {e3['measured']} measured requests failed"
    return record, None


def harness_verdict(experiment: str, result: Any) -> bool | None:
    """E3's own verdict that its run was a measurement, for :func:`model_checks`: only ``measurement: true`` in its
    result counts (a missing key or result does not); None for a harness that gives no such verdict (G0)."""
    if experiment != "e3":
        return None
    return isinstance(result, dict) and result.get("measurement") is True


def model_checks(evidence: Mapping[str, Any], rows: list[dict[str, Any]], status: str | None,
                 harness_measurement: bool | None, alias: str, host: str) -> tuple[dict[str, bool], list[str]]:
    """Every condition that admits a model measurement (:data:`CHECK_KEYS`, in order) and the flags; see the module
    docstring. ``harness_measurement`` is None when the harness has no such verdict (G0), which passes."""
    flags: list[str] = []

    def held(check: Callable[[], bool]) -> bool:
        try:
            return bool(check())
        except (KeyError, TypeError, AttributeError, IndexError):
            return False

    server_record, model_record = evidence.get("server_record"), evidence.get("model_record")
    prepare, start = evidence.get("prepare"), evidence.get("start")

    def connections() -> list[dict[str, Any]]:
        return [*server_record["connections"], *model_record["connections"], *prepare["connections"]]

    checks = {
        "model_verified": held(lambda: model_record["verified"] is True
                               and model_record["verified_by"] in MODEL_VERIFIED_BY
                               and evidence["model_record_sha256"] == prepare["model"]["record_sha256"]),
        "server_verified": held(lambda: server_record["verified"] is True
                                and server_record["verified_by"] in SERVER_VERIFIED_BY
                                and evidence["server_record_sha256"] == prepare["server"]["record_sha256"]),
        "download_hosts": held(lambda: all(isinstance(c["host"], str) and c["host"] != ""
                                           and c["host"] == c["logical_host"] and not is_private_host(c["host"])
                                           for c in connections())),
        "model_path": held(lambda: isinstance(start["props"]["model_path"], str)
                           and start["props"]["model_path"] == prepare["model"]["path"]),
        "ledger_host": held(lambda: all(row["host"] == host for row in rows)),
        "model_served": held(lambda: all(row["model_served"] == alias for row in rows if row["http_status"] == 200)),
        "harness_measurement": harness_measurement is None or harness_measurement is True,
        "participation": status in ("ok", "result_fail"),
    }
    if held(lambda: server_record["verified_by"] == "first-use"):
        flags.append("server_first_use")
    return checks, flags


def measurement_class(kind: str, provider_override: str | None, rows: list[dict[str, Any]],
                      checks: Mapping[str, bool] | None = None) -> tuple[str, str]:
    """(class, reason key); the reason keys are those of ``notes.CLASS_REASONS``."""
    if kind == "none":
        return "no-model", "no_model"
    if kind == "fake":
        return "plumbing", "fake_kind"
    if provider_override == "fake":
        return "plumbing", "provider_override"
    if any(row.get("fake_marker") for row in rows):
        return "plumbing", "fake_marker"
    if checks is None:
        return "unverified", "no_evidence"
    for key in CHECK_KEYS:
        if checks.get(key) is not True:
            return "unverified", key
    return "model", "verified"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def display_class(record: Mapping[str, Any], provenance: Mapping[str, Any] | None) -> str:
    """Where a summary shows a unit; the first rule that holds wins:

    1. ``no-result`` unless the status is ``ok`` or ``result_fail``;
    2. ``plumbing`` when the record says plumbing, its kind or provider is fake, it counted fake rows, or the shard's
       provenance is not of result class ``real`` or names the fake provider;
    3. ``no-model`` for a model-free unit (kind ``none``, class ``no-model``);
    4. ``model`` only when the record says model, its kind is gguf, the provenance is ``real``, it counted zero fake
       rows and every one of :data:`CHECK_KEYS` is exactly true;
    5. otherwise ``unverified``.
    """
    if record.get("status") not in ("ok", "result_fail"):
        return "no-result"
    fake_rows = record.get("fake_rows")
    prov = provenance if isinstance(provenance, Mapping) else None
    if (record.get("measurement_class") == "plumbing" or record.get("kind") == "fake"
            or record.get("provider") == "fake" or (_is_int(fake_rows) and fake_rows != 0)
            or (prov is not None and (prov.get("result_class") != "real" or prov.get("provider") == "fake"))):
        return "plumbing"
    if record.get("kind") == "none" and record.get("measurement_class") == "no-model":
        return "no-model"
    checks = record.get("class_checks")
    if (record.get("measurement_class") == "model" and record.get("kind") == "gguf" and prov is not None
            and prov.get("result_class") == "real" and _is_int(fake_rows) and fake_rows == 0
            and isinstance(checks, Mapping) and all(checks.get(key) is True for key in CHECK_KEYS)):
        return "model"
    return "unverified"


def unit_notes(experiment: str, measurement: str) -> list[str]:
    notes = ["plumbing"] if measurement == "plumbing" else ["model_measurement"] if measurement == "model" else []
    notes.append("synthetic")
    notes.append("runner_hardware" if experiment == "e3" else "text_only_scan")
    return notes


def unit_record(unit: Mapping[str, Any], shard: str, provider: str, provider_override: str | None,
                **fields: Any) -> dict[str, Any]:
    """A unit.json; fields left out take the values of a unit that never ran (a skipped unit)."""
    measurement, reason = measurement_class(unit["kind"], provider_override, [])
    record = {
        "schema_version": 1, "unit": unit["unit"], "run_id": unit["run_id"], "shard": shard,
        "experiment": unit["experiment"], "model": unit["model"], "kind": unit["kind"], "provider": provider,
        "argv": [], "seeds": list(unit["seeds"]), "timeout_s": None, "started_at": None, "finished_at": None,
        "wall_s": 0.0, "exit_code": None, "signal": None, "status": "skipped", "status_reason": None,
        "measurement_class": measurement, "class_reason": reason,
        "notes": unit_notes(unit["experiment"], measurement), "routing_sha256": None, "files": {},
        "fake_rows": None, "ledger_rows": None, "participation": None, "harness_measurement": None, "logs": None,
        "serving": None, "server_exit": None, "server_after": None, "class_checks": None, "class_flags": [],
    }
    unknown = set(fields) - set(record)
    if unknown:
        raise ValueError(f"unknown unit.json keys {sorted(unknown)}") from None
    record.update(fields)
    return record


# --------------------------------------------------------------------------------------------------- processes

def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM to the group, up to 10 s for the leader, then SIGKILL."""
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=TERM_GRACE_S)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL)
        proc.wait()


def _result(proc: subprocess.Popen[bytes], timed_out: bool, t0: float, watched: bool = False) -> ProcessResult:
    code = proc.returncode
    name = None
    if code is not None and code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
    return ProcessResult(exit_code=code, signal_name=name, timed_out=timed_out,
                         wall_s=round(time.monotonic() - t0, 3), pgid=proc.pid, stopped_by_watch=watched)


def run_process(argv: list[str], env: dict[str, str], *, cwd: Path, timeout_s: float, stdout_path: Path,
                stderr_path: Path, on_start: Callable[[int], None] | None = None,
                watch: Callable[[], bool] | None = None) -> ProcessResult:
    """Run ``argv`` in a new session with output to the two files; see the module docstring for the kill rules.
    With ``watch``, the wait goes in :data:`~lab.server.WATCH_INTERVAL_S` slices and a true ``watch()`` stops the
    group as a timeout would (``stopped_by_watch``)."""
    t0 = time.monotonic()
    proc: subprocess.Popen[bytes] | None = None
    timed_out = watched = False
    try:
        with open(stdout_path, "wb") as out_fh, open(stderr_path, "wb") as err_fh:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out_fh, stderr=err_fh, env=env, cwd=cwd,
                                    start_new_session=True, close_fds=True)
        if on_start is not None:
            on_start(proc.pid)
        deadline = t0 + timeout_s
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                _stop(proc)
                break
            try:
                proc.wait(timeout=min(WATCH_INTERVAL_S, left) if watch is not None else left)
                break
            except subprocess.TimeoutExpired:
                if watch is not None and watch():
                    watched = True
                    _stop(proc)
                    break
    except BaseException as exc:
        if proc is not None:
            _stop(proc)
            _signal_group(proc.pid, signal.SIGKILL)
            if isinstance(exc, ShardInterrupted):
                exc.process = _result(proc, timed_out, t0)
        raise
    _signal_group(proc.pid, signal.SIGKILL)
    return _result(proc, timed_out, t0, watched)


def cap_log(path: Path, cap: int = LOG_CAP_BYTES) -> dict[str, Any] | None:
    """Keep the last ``cap`` bytes; ``{"bytes": <size kept>, "truncated": <bool>}``, or None without a log."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= cap:
        return {"bytes": size, "truncated": False}
    with open(path, "rb") as fh:
        fh.seek(size - cap)
        tail = fh.read()
    path.write_bytes(tail)
    return {"bytes": len(tail), "truncated": True}


# --------------------------------------------------------------------------------------------------- outputs

def _read_regular(path: Path) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read()
    finally:
        os.close(fd)


def collect(unit: Mapping[str, Any], out: Path) -> dict[str, dict[str, Any]]:
    """Copy the adapter's allowlisted outputs (regular files only, also from a failed or timed-out run) to
    ``runs/<experiment>/<run id>/``; returns ``{path relative to OUT: {sha256, bytes}}``."""
    out = Path(out)
    source = harness_dir(unit, out)
    target = out / "runs" / unit["experiment"] / unit["run_id"]
    if not source.is_dir() or source.is_symlink():
        return {}
    base = source.resolve()
    files: dict[str, dict[str, Any]] = {}
    for pattern in ADAPTERS[unit["experiment"]].collect:
        for path in sorted(source.glob(pattern)):
            rel = path.relative_to(source)
            if base not in path.resolve().parents:
                continue
            data = _read_regular(path)
            if data is None:
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            files[dest.relative_to(out).as_posix()] = {"sha256": sha256_hex(data), "bytes": len(data)}
    return files


def _collected(unit: Mapping[str, Any], out: Path, patterns: tuple[str, ...]) -> list[Path]:
    target = Path(out) / "runs" / unit["experiment"] / unit["run_id"]
    return sorted({p for pattern in patterns for p in target.glob(pattern)})


def load_result(unit: Mapping[str, Any], out: Path) -> Any:
    path = Path(out) / "runs" / unit["experiment"] / unit["run_id"] / ADAPTERS[unit["experiment"]].result_file
    data = _read_regular(path)
    if data is None:
        return None
    failed = False
    try:
        obj = strict_load(data)
    except StrictJsonError:
        failed = True
    return None if failed else obj


def ledger_rows(unit: Mapping[str, Any], out: Path) -> list[dict[str, Any]] | None:
    """Every row of the collected ledgers; None when one cannot be read (a partial line from a killed run)."""
    rows: list[dict[str, Any]] = []
    for path in _collected(unit, out, ADAPTERS[unit["experiment"]].ledgers):
        failed = False
        try:
            rows.extend(read_ledger(path))
        except (OSError, ValueError):
            failed = True
        if failed:
            return None
    return rows


# --------------------------------------------------------------------------------------------------- one unit

def run_unit(unit: dict[str, Any], plan: dict[str, Any], out: Path, *, timeout_s: float,
             provider_override: str | None, on_start: Callable[[int], None] | None = None,
             serving: Serving | None = None) -> dict[str, Any]:
    out = Path(out)
    adapter = ADAPTERS[unit["experiment"]]
    entry = plan["models"][unit["model"]]
    persona = entry["persona"] if entry["kind"] == "fake" else "valid"
    provider = "fake" if provider_override == "fake" else plan["provider"]
    server_exit = None
    work = out / "work" / unit["unit"]
    unit_dir = out / "units" / unit["unit"]
    routing_path = out / "routing" / f"{unit['unit']}.json"
    started_at, t0 = utc_clock(), time.monotonic()
    proc: ProcessResult | None = None
    argv: list[str] = []
    routing_sha256 = None
    status: str | None = None
    reason: str | None = None
    interrupted = False
    server: FakeServer | None = None
    try:
        if serving is None:
            pack = load_pack(unit["params"]["pack"]) if unit["experiment"] == "g0" else None
            server = FakeServer(persona, pack)
            server.start()
            base_url, server_note = server.base_url, FAKE_SERVER_NOTE
        else:
            base_url, server_note = serving.base_url, serving.server_note
        routing_path.parent.mkdir(parents=True, exist_ok=True)
        routing_sha256 = write_json_atomic(routing_path, routing_doc(unit, entry, base_url))
        try:
            load_routing(routing_path, tasks=adapter.routed_tasks, check_env=False)
        except ConfigError:
            status, reason = "failed", LAB_ROUTING
        if status is None:
            argv = build_argv(unit, out, routing_path, server_note)
            work.mkdir(parents=True, exist_ok=True)
            unit_dir.mkdir(parents=True, exist_ok=True)
            proc = run_process(argv, subprocess_env(os.environ, unit["env"]), cwd=ROOT, timeout_s=timeout_s,
                               stdout_path=unit_dir / "stdout.log", stderr_path=unit_dir / "stderr.log",
                               on_start=on_start,
                               watch=(lambda: serving.poll() is not None) if serving is not None else None)
    except ShardInterrupted as exc:
        interrupted = True
        proc = exc.process or proc
    finally:
        if server is not None:
            server.stop()

    files = collect(unit, out)
    result = load_result(unit, out) if proc is not None else None
    if serving is not None and proc is not None:
        server_exit = serving.poll()
    if interrupted:
        status, reason = "interrupted", SHARD_INTERRUPTED
    elif proc is not None and serving is not None and (proc.stopped_by_watch or server_exit is not None):
        # the server died while the harness ran: whatever the harness made of it, nothing here was served
        status, reason = "invalid", exit_reason(server_exit or {})
    elif proc is not None:
        status, reason = harness_status(unit["experiment"], proc.exit_code, proc.timed_out, result)
    rows = ledger_rows(unit, out)
    record_participation = None
    if status in ("ok", "result_fail") and unit["kind"] != "none":
        if rows is None:
            status, reason = "failed", RESULT_MISSING
        else:
            record_participation, problem = participation(unit["experiment"], rows, required_tasks(unit), result)
            if problem is not None:
                status, reason = "invalid", problem
    if serving is not None and proc is not None and server_exit is None and not interrupted \
            and status not in ("ok", "result_fail"):
        # requests that just failed may have met a killed server that is not reaped yet: name the exit if it comes
        server_exit = serving.poll(EXIT_GRACE_S)
        if server_exit is not None:
            status, reason = "invalid", exit_reason(server_exit)
    harness_measurement = None
    if unit["experiment"] == "e3" and isinstance(result, dict):
        harness_measurement = result.get("measurement")
    checks, flags = None, []
    if serving is not None:
        checks, flags = model_checks(serving.evidence, rows or [], status, harness_verdict(unit["experiment"], result),
                                     serving.alias, serving.host_label)
    measurement, class_reason = measurement_class(unit["kind"], provider_override, rows or [], checks)
    logs = {name: cap_log(unit_dir / f"{name}.log") for name in ("stdout", "stderr")}
    shutil.rmtree(work, ignore_errors=True)
    return unit_record(
        unit, unit["shard"], provider, provider_override, argv=argv, timeout_s=round(timeout_s, 3),
        started_at=started_at, finished_at=utc_clock(),
        wall_s=proc.wall_s if proc is not None else round(time.monotonic() - t0, 3),
        exit_code=proc.exit_code if proc is not None else None,
        signal=proc.signal_name if proc is not None else None, status=status, status_reason=reason,
        measurement_class=measurement, class_reason=class_reason, notes=unit_notes(unit["experiment"], measurement),
        routing_sha256=routing_sha256, files=files,
        fake_rows=sum(1 for r in rows if r["fake_marker"]) if rows is not None else None,
        ledger_rows=len(rows) if rows is not None else None, participation=record_participation,
        harness_measurement=harness_measurement, logs=logs,
        serving=({"start": serving.start, "host": serving.host_label, "alias": serving.alias,
                  "class": serving.class_name} if serving is not None else None),
        server_exit=server_exit, class_checks=checks, class_flags=flags)
