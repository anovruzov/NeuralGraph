"""Run one shard of a plan, and seal its output directory whatever happened.

    python -m lab.shard run --plan PLAN --shard ID --out OUT [--provider fake] [--deadline-epoch N]
    python -m lab.shard seal --out OUT [--shard ID] --step-outcome NAME=VALUE [--step-outcome ...]

The shard root ``OUT`` holds ``provision/`` and ``server/`` (written by the prepare step, G2), ``routing/``,
``units/<unit>/`` (``unit.json`` and the two capped logs), ``runs/<experiment>/<run id>/`` (collected harness
outputs), ``work/`` (scratch; deleted after each unit and by ``seal``, never uploaded), ``provenance.json`` and
``status.json``.

``run`` refuses (exit 2) an unreadable plan, an unknown shard, an ``OUT`` holding anything but ``provision/`` and
``server/``, an ``OUT`` inside ``mycelic/``, ``research/``, ``NeuralGraph/`` or ``.github/``, and a shard whose
model needs a server while neither the plan nor ``--provider`` is ``fake`` (the prepare step arrives in G2). It
then runs the units in plan order, each with ``min(minutes, deadline - now - 120 s)`` of time; once that is under
60 s, this unit and every later one are skipped (``shard budget exhausted``). The deadline is ``--deadline-epoch``,
else now plus the shard capacity. SIGTERM or SIGINT stops the running unit's process group, records the unit as
interrupted and skips the rest. Each unit prints exactly one line::

    lab: unit <unit> status <status> class <class> exit <code|none> wall_s <seconds>

``provenance.json`` (keys :data:`PROVENANCE_KEYS`) is written before the first unit and after each one: the plan,
request, manifest and lock hashes, the commit and code hashes, the host, the server, the model, the deadline, wall
times, the planned units and each unit's status and class. ``result_class`` is ``plumbing`` whenever a fake served
the shard, and ``banner`` then carries the plumbing banner. Exit 1 when a unit is invalid, failed, timed out or was
interrupted, or the budget ran out; else 0 (a harness FAIL verdict, ``result_fail``, is a valid result).

``seal`` always writes ``status.json`` (exit 0) unless its own arguments are malformed (exit 2, nothing written):
it creates ``OUT`` if missing, removes ``OUT/work`` and records the workflow step outcomes it was given, the first
failed or cancelled step, whether provenance is present and complete, each unit's status, the planned units without
a ``unit.json``, counts per status and the sha256 and size of every file under ``OUT``.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

from mycelic.collective.experiments.common import (RUN_ID_RE, code_commit, code_dirty, code_hash, code_stamps,
                                                   utc_clock, write_json_atomic)
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load

from . import EXIT_OK, EXIT_UNIT, EXIT_USAGE, ROOT, LabError, display_path, hostinfo
from .notes import BUDGET_EXHAUSTED, NO_MODEL_SERVER, PLUMBING_BANNER, SHARD_INTERRUPTED
from .plan import SHARD_ID_RE, UNIT_ID_RE
from .units import FAILING, ShardInterrupted, run_unit, unit_record

PROVENANCE_KEYS = ("schema_version", "kind", "shard", "complete", "interrupted", "result_class", "banner", "plan",
                   "request", "manifest_sha256", "lock_sha256", "git", "code", "host", "provider", "server", "model",
                   "deadline_epoch", "started_at", "finished_at", "wall_s", "planned_units", "units", "exit_code")
STATUS_KEYS = ("schema_version", "kind", "shard", "out_existed", "steps", "failed_step", "provenance", "units",
               "missing_units", "counts", "files", "sealed_at")
PREPARED_DIRS = ("provision", "server")
FORBIDDEN_ROOTS = ("mycelic", "research", "NeuralGraph", ".github")
MIN_UNIT_SECONDS = 60
DEADLINE_MARGIN_S = 120
STEP_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}", re.ASCII)
STEP_VALUES = ("success", "failure", "cancelled", "skipped")
SKIP_FAILS = (BUDGET_EXHAUSTED, SHARD_INTERRUPTED)


class ShardError(LabError):
    pass


class _Signals:
    """SIGTERM/SIGINT: the first one is remembered and, while a unit runs (``armed``), raises ShardInterrupted.
    Later signals change nothing, so the cleanup of the interrupted unit is never cut short."""

    def __init__(self) -> None:
        self.received: str | None = None
        self.armed = False

    def __call__(self, signum: int, frame: Any) -> None:
        if self.received is not None:
            return
        self.received = signal.Signals(signum).name
        if self.armed:
            self.armed = False
            raise ShardInterrupted()


# --------------------------------------------------------------------------------------------------- run

def _load_plan(path: str) -> tuple[dict[str, Any], bytes]:
    try:
        data = Path(path).read_bytes()
        plan = strict_load(data)
    except (OSError, StrictJsonError):
        raise ShardError("$", "the plan cannot be read") from None
    if not isinstance(plan, dict) or plan.get("kind") != "lab_plan" or plan.get("schema_version") != 1:
        raise ShardError("$", "not a lab plan") from None
    def matches(obj: Any, key: str, pattern: re.Pattern[str]) -> bool:
        return isinstance(obj, dict) and isinstance(obj.get(key), str) and pattern.fullmatch(obj[key]) is not None

    units, shards = plan.get("units"), plan.get("shards")
    ids_ok = (isinstance(units, list) and isinstance(shards, list)
              and all(matches(u, "unit", UNIT_ID_RE) and matches(u, "run_id", RUN_ID_RE) for u in units)
              and all(matches(s, "shard", SHARD_ID_RE) for s in shards))
    if not ids_ok:
        raise ShardError("$", "the plan holds a malformed unit, run or shard id") from None
    return plan, data


def _check_out(out: Path) -> None:
    resolved = out.resolve()
    for name in FORBIDDEN_ROOTS:
        root = (ROOT / name).resolve()
        if resolved == root or root in resolved.parents:
            raise ShardError("$", f"--out must not lie inside {name}/") from None
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise ShardError("$", "--out must be a directory") from None
    if out.exists() and any(p.name not in PREPARED_DIRS for p in out.iterdir()):
        raise ShardError("$", "--out may hold only provision/ and server/ before a run") from None


def _status_line(record: dict[str, Any]) -> str:
    code = "none" if record["exit_code"] is None else str(record["exit_code"])
    return (f"lab: unit {record['unit']} status {record['status']} class {record['measurement_class']} "
            f"exit {code} wall_s {record['wall_s']:.1f}")


def _provenance(plan: dict[str, Any], plan_path: str, plan_bytes: bytes, shard: dict[str, Any], out: Path,
                provider_override: str | None, deadline: float) -> dict[str, Any]:
    plumbing = plan["provider"] == "fake" or provider_override == "fake"
    entry = plan["models"].get(shard["model"]) if shard["model"] else None
    persona = entry["persona"] if entry is not None and entry["kind"] == "fake" else "valid"
    return {
        "schema_version": 1, "kind": "lab_provenance", "shard": shard["shard"], "complete": False,
        "interrupted": False, "result_class": "plumbing" if plumbing else "real",
        "banner": PLUMBING_BANNER if plumbing else None,
        "plan": {"path": display_path(plan_path), "sha256": sha256_hex(plan_bytes)},
        "request": {k: plan["request"][k] for k in ("path", "name", "sha256")},
        "manifest_sha256": plan["manifest"]["sha256"], "lock_sha256": plan["lock"]["sha256"],
        "git": {"plan_sha": plan["git_sha"], "checkout_sha": code_commit(), "lab_dirty": code_dirty(["lab"])},
        "code": {"collective": code_stamps(), "lab_code_hash": code_hash(sorted((ROOT / "lab").rglob("*.py")), ROOT)},
        "host": hostinfo.collect(out), "provider": "fake" if provider_override == "fake" else plan["provider"],
        "server": {"kind": "fake", "implementation": "FakeOpenAIServer", "persona": persona},
        "model": ({"key": shard["model"], "kind": entry["kind"], "alias": entry["alias"]}
                  if entry is not None else None),
        "deadline_epoch": deadline, "started_at": utc_clock(), "finished_at": None, "wall_s": None,
        "planned_units": list(shard["units"]), "units": [], "exit_code": None,
    }


def run_shard(plan_path: str, shard_id: str, out: Path, provider_override: str | None,
              deadline_epoch: float | None) -> int:
    plan, plan_bytes = _load_plan(plan_path)
    shard = next((s for s in plan["shards"] if s["shard"] == shard_id), None)
    if shard is None:
        raise ShardError("$", "the shard is not in the plan") from None
    _check_out(out)
    if shard["kind"] != "fake" and plan["provider"] != "fake" and provider_override != "fake":
        raise ShardError("$", NO_MODEL_SERVER) from None
    units = {u["unit"]: u for u in plan["units"]}
    provider = "fake" if provider_override == "fake" else plan["provider"]
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    deadline = deadline_epoch if deadline_epoch is not None else time.time() + plan["shard_capacity_minutes"] * 60
    signals = _Signals()
    previous = {sig: signal.signal(sig, signals) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        provenance = _provenance(plan, plan_path, plan_bytes, shard, out, provider_override, deadline)
        write_json_atomic(out / "provenance.json", provenance)
        skip_reason: str | None = None
        for uid in shard["units"]:
            unit = units[uid]
            if signals.received is not None:
                skip_reason = SHARD_INTERRUPTED
            timeout_s = min(unit["minutes"] * 60, deadline - time.time() - DEADLINE_MARGIN_S)
            if skip_reason is None and timeout_s < MIN_UNIT_SECONDS:
                skip_reason = BUDGET_EXHAUSTED
            if skip_reason is not None:
                record = unit_record(unit, shard_id, provider, provider_override, status_reason=skip_reason)
            else:
                record = None
                try:
                    signals.armed = True
                    record = run_unit(unit, plan, out, timeout_s=timeout_s, provider_override=provider_override)
                    signals.armed = False
                except ShardInterrupted:
                    # the handler disarmed itself; the signal landed outside run_unit's own interrupt handling
                    shutil.rmtree(out / "work" / uid, ignore_errors=True)
                    if record is None:
                        record = unit_record(unit, shard_id, provider, provider_override, status="interrupted",
                                             status_reason=SHARD_INTERRUPTED)
            (out / "units" / uid).mkdir(parents=True, exist_ok=True)
            write_json_atomic(out / "units" / uid / "unit.json", record)
            print(_status_line(record), flush=True)
            provenance["units"].append({k: record[k] for k in ("unit", "run_id", "experiment", "status",
                                                                "measurement_class", "exit_code", "wall_s")})
            write_json_atomic(out / "provenance.json", provenance)
        failing = any(u["status"] in FAILING for u in provenance["units"]) or skip_reason in SKIP_FAILS
        code = EXIT_UNIT if failing else EXIT_OK
        provenance.update(complete=True, interrupted=signals.received is not None, finished_at=utc_clock(),
                          wall_s=round(time.monotonic() - t0, 3), exit_code=code)
        write_json_atomic(out / "provenance.json", provenance)
        return code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


# --------------------------------------------------------------------------------------------------- seal

def _read_json(path: Path) -> Any:
    failed = False
    try:
        obj = strict_load(path.read_bytes())
    except (OSError, StrictJsonError):
        failed = True
    return None if failed else obj


def file_map(out: Path, exclude: tuple[str, ...] = ("status.json",)) -> dict[str, dict[str, Any]]:
    """``{relative POSIX path: {sha256, bytes}}`` of every regular file under ``out`` (symlinks not followed)."""
    files: dict[str, dict[str, Any]] = {}
    for directory, dirnames, filenames in os.walk(out):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(directory) / name
            rel = path.relative_to(out).as_posix()
            if rel in exclude or path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            files[rel] = {"sha256": sha256_hex(data), "bytes": len(data)}
    return dict(sorted(files.items()))


def seal(out: Path, shard_id: str | None, steps: dict[str, str]) -> int:
    out_existed = out.is_dir()
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / "work", ignore_errors=True)
    provenance = _read_json(out / "provenance.json")
    prov = provenance if isinstance(provenance, dict) else {}
    units: dict[str, str] = {}
    for path in sorted((out / "units").glob("*/unit.json")):
        record = _read_json(path)
        status = record.get("status") if isinstance(record, dict) else None
        units[path.parent.name] = status if isinstance(status, str) else "unreadable"
    planned = prov.get("planned_units")
    missing = [u for u in planned if u not in units] if isinstance(planned, list) else None
    counts: dict[str, int] = {}
    for status in units.values():
        counts[status] = counts.get(status, 0) + 1
    failed_step = next((name for name, value in steps.items() if value in ("failure", "cancelled")), None)
    shard = shard_id if shard_id is not None else prov.get("shard") if isinstance(prov.get("shard"), str) else None
    write_json_atomic(out / "status.json", {
        "schema_version": 1, "kind": "lab_shard_status", "shard": shard, "out_existed": out_existed,
        "steps": steps, "failed_step": failed_step,
        "provenance": {"present": (out / "provenance.json").exists(), "complete": prov.get("complete") is True,
                       "interrupted": prov.get("interrupted") is True},
        "units": units, "missing_units": missing, "counts": dict(sorted(counts.items())),
        "files": file_map(out), "sealed_at": utc_clock()})
    return EXIT_OK


def _steps(values: list[str]) -> dict[str, str] | None:
    steps: dict[str, str] = {}
    for item in values:
        name, sep, value = item.partition("=")
        if not sep or STEP_NAME_RE.fullmatch(name) is None or value not in STEP_VALUES or name in steps:
            return None
        steps[name] = value
    return steps


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.shard", description="Run or seal one shard of a lab plan.")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run the shard's units")
    r.add_argument("--plan", required=True)
    r.add_argument("--shard", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--provider", choices=("fake",), help="serve every unit from the fake server (plumbing)")
    r.add_argument("--deadline-epoch", type=float, help="the shard's deadline (seconds since the epoch)")
    s = sub.add_parser("seal", help="write status.json for a shard root, whatever state it is in")
    s.add_argument("--out", required=True)
    s.add_argument("--shard")
    s.add_argument("--step-outcome", action="append", default=[], metavar="NAME=VALUE")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "seal":
        steps = _steps(args.step_outcome)
        if steps is None or (args.shard is not None and SHARD_ID_RE.fullmatch(args.shard) is None):
            print("error: --step-outcome takes NAME=success|failure|cancelled|skipped once per step, and --shard a "
                  "shard id", file=sys.stderr)
            return EXIT_USAGE
        return seal(Path(args.out), args.shard, steps)
    if args.deadline_epoch is not None and not math.isfinite(args.deadline_epoch):
        print("error: --deadline-epoch must be a finite number of seconds", file=sys.stderr)
        return EXIT_USAGE
    try:
        return run_shard(args.plan, args.shard, Path(args.out), args.provider, args.deadline_epoch)
    except ShardError as err:
        print(f"error: {err.problem}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
