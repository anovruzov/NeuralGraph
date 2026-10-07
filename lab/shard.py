"""Prepare, run and seal one shard of a plan.

    python -m lab.shard prepare --plan PLAN --shard ID --provision-records R --cache-root C --out OUT
        [--github-output F]
    python -m lab.shard run --plan PLAN --shard ID --out OUT [--provider fake] [--deadline-epoch N]
    python -m lab.shard seal --out OUT [--shard ID] --step-outcome NAME=VALUE [--step-outcome ...]

The shard root ``OUT`` holds ``provision/`` and ``server/`` (written by ``prepare``), ``routing/``,
``units/<unit>/`` (``unit.json`` and the two capped logs), ``runs/<experiment>/<run id>/`` (collected harness
outputs), ``work/`` (scratch; deleted after each unit and by ``seal``, never uploaded), ``provenance.json`` and
``status.json``.

``prepare`` (``provision.prepare``) restores and re-verifies a gguf shard's server archive and model file against
the run's provision records (``R``), extracts the server fresh into ``OUT/server/bin`` and writes
``OUT/provision/prepare.json`` with copies of both records; a shard without a gguf model needs nothing
(``needed: false``). Its exit codes are those of ``lab.provision``.

``run`` refuses (exit 2) an unreadable plan, an unknown shard, an ``OUT`` holding anything but ``provision/`` and
``server/``, an ``OUT`` inside ``mycelic/``, ``research/``, ``NeuralGraph/`` or ``.github/``, and a gguf shard
(neither the plan nor ``--provider`` fake) without a ``prepare.json`` for this shard and plan (``not prepared``). It
then runs the units in plan order, each with ``min(minutes, deadline - now - 120 s)`` of time; once that is under
60 s, this unit and every later one are skipped (``shard budget exhausted``). The deadline is ``--deadline-epoch``,
else now plus the shard capacity. SIGTERM or SIGINT stops the running unit's process group (or the server being
started), records the unit as interrupted and skips the rest. Each unit prints exactly one line::

    lab: unit <unit> status <status> class <class> exit <code|none> wall_s <seconds>

A gguf shard serves its units from the prepared model server (:class:`_ModelServing`). Consecutive units of one
serving class share a start: ``quality`` (G0: one slot of ``ctx_per_slot``) and ``e3`` (as many slots as the
shard's highest E3 concurrency, each of ``e3_ctx_per_slot``); the seed is the first served unit's. Each start is
health-checked, its settings asserted and warmed up (``warmup.py``, within the shard budget less what one unit
needs) before a unit runs; a start that fails skips the class's units with its reason, a warm-up finding skips the
units that depend on it. The server is watched during every unit and asked ``/health`` after it; an unplanned exit
(or an unhealthy server) is tolerated once per shard (the next unit gets a fresh start, ``restart: true``), and after
a second one every remaining unit is skipped (``server unavailable``). A server that exits during its warm-up fails
its class with the exit as the reason (``server exited with SIGKILL`` and the out-of-memory hint) and counts as an
unplanned exit. The server is stopped at every class change, at the end and on an interrupt.

``provenance.json`` (keys :data:`PROVENANCE_KEYS`) is written before the first unit and after each one: the plan,
request, manifest and lock hashes, the commit and code hashes, the host, the provider, the server (for a gguf shard:
the pinned archive, how it was verified and every start record), the model (its pinned file and commit), the
provision record hashes, the deadline, wall times, the planned units and each unit's status and class.
``result_class`` is ``plumbing`` whenever a fake served the shard, and ``banner`` then carries the plumbing banner.
Exit 1 when a unit is invalid, failed, timed out, interrupted or skipped; else 0 (a harness FAIL verdict,
``result_fail``, is a valid result).

``seal`` always writes ``status.json`` (exit 0) unless its own arguments are malformed (exit 2, nothing written):
it creates ``OUT`` if missing, removes ``OUT/work`` and the server's ``bin``, ``home`` and ``tmp`` (its logs stay),
and records the workflow step outcomes it was given, the first failed or cancelled step, whether provenance is
present and complete, each unit's status, the planned units without a ``unit.json``, counts per status and the
sha256 and size of every file under ``OUT``.
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
from typing import Any, Callable

from mycelic.collective.experiments.common import (RUN_ID_RE, code_commit, code_dirty, code_hash, code_stamps,
                                                   utc_clock, write_json_atomic)
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load

from . import EXIT_OK, EXIT_UNIT, EXIT_USAGE, ROOT, LabError, display_path, forbidden_root, hostinfo
from . import provision as lab_provision
from . import server as lab_server
from .download import UrlMap
from .notes import (BUDGET_EXHAUSTED, NOT_PREPARED, PLUMBING_BANNER, SERVER_NOTE_MODEL, SERVER_UNAVAILABLE,
                    SERVER_UNHEALTHY_AFTER, SHARD_INTERRUPTED)
from .plan import SHARD_ID_RE, UNIT_ID_RE
from .server import HEALTH_DEADLINE_S, ModelServer, ServerError, ServerSpec, thread_counts
from .units import FAILING, Serving, ShardInterrupted, run_unit, unit_record
from .warmup import warm_tasks, warm_up

PROVENANCE_KEYS = ("schema_version", "kind", "shard", "complete", "interrupted", "result_class", "banner", "plan",
                   "request", "manifest_sha256", "lock_sha256", "git", "code", "host", "provider", "server", "model",
                   "provision", "deadline_epoch", "started_at", "finished_at", "wall_s", "planned_units", "units",
                   "exit_code")
STATUS_KEYS = ("schema_version", "kind", "shard", "out_existed", "steps", "failed_step", "provenance", "units",
               "missing_units", "counts", "files", "sealed_at")
PREPARED_DIRS = ("provision", "server")
SEALED_AWAY = ("work", "server/bin", "server/home", "server/tmp")
MIN_UNIT_SECONDS = 60
DEADLINE_MARGIN_S = 120
STEP_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}", re.ASCII)
STEP_VALUES = ("success", "failure", "cancelled", "skipped")


class ShardError(LabError):
    pass


class _Signals:
    """SIGTERM/SIGINT: the first one is remembered and, while a unit or a server start runs (``armed``), raises
    ShardInterrupted. Later signals change nothing, so the cleanup of the interrupted unit is never cut short."""

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
    name = forbidden_root(out)
    if name is not None:
        raise ShardError("$", f"--out must not lie inside {name}/") from None
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise ShardError("$", "--out must be a directory") from None
    if out.exists() and any(p.name not in PREPARED_DIRS for p in out.iterdir()):
        raise ShardError("$", "--out may hold only provision/ and server/ before a run") from None


def _load_prepared(out: Path, shard_id: str, plan_sha256: str) -> dict[str, Any]:
    """The prepare step's evidence for this shard and plan: ``prepare`` and both provision records with their
    sha256; :data:`~lab.notes.NOT_PREPARED` when any of it is missing or for another shard or plan."""
    failed = False
    try:
        prepare_bytes = (out / "provision" / "prepare.json").read_bytes()
        prepare = strict_load(prepare_bytes)
        server_bytes = (out / "provision" / "server.json").read_bytes()
        model_bytes = (out / "provision" / "model.json").read_bytes()
        server_record, model_record = strict_load(server_bytes), strict_load(model_bytes)
    except (OSError, StrictJsonError):
        failed = True
    ok = (not failed and isinstance(prepare, dict) and prepare.get("kind") == "lab_prepare"
          and prepare.get("shard") == shard_id and prepare.get("plan_sha256") == plan_sha256
          and prepare.get("needed") is True and isinstance(server_record, dict) and isinstance(model_record, dict)
          and isinstance(server_record.get("server"), dict) and isinstance(model_record.get("model"), dict))
    if not ok:
        raise ShardError("$", NOT_PREPARED) from None
    return {"prepare": prepare, "prepare_sha256": sha256_hex(prepare_bytes), "server_record": server_record,
            "server_record_sha256": sha256_hex(server_bytes), "model_record": model_record,
            "model_record_sha256": sha256_hex(model_bytes)}


def _status_line(record: dict[str, Any]) -> str:
    code = "none" if record["exit_code"] is None else str(record["exit_code"])
    return (f"lab: unit {record['unit']} status {record['status']} class {record['measurement_class']} "
            f"exit {code} wall_s {record['wall_s']:.1f}")


def _provenance(plan: dict[str, Any], plan_path: str, plan_bytes: bytes, shard: dict[str, Any], out: Path,
                provider_override: str | None, deadline: float, prepared: dict[str, Any] | None) -> dict[str, Any]:
    plumbing = plan["provider"] == "fake" or provider_override == "fake"
    entry = plan["models"].get(shard["model"]) if shard["model"] else None
    persona = entry["persona"] if entry is not None and entry["kind"] == "fake" else "valid"
    server: dict[str, Any] = {"kind": "fake", "implementation": "FakeOpenAIServer", "persona": persona}
    model = {"key": shard["model"], "kind": entry["kind"], "alias": entry["alias"]} if entry is not None else None
    provision = None
    if prepared is not None:
        srec, mrec = prepared["server_record"], prepared["model_record"]
        block, mblock = srec["server"], mrec["model"]
        server = {"kind": "model", "program": block.get("program"), "tag": block.get("tag"),
                  "asset": block.get("asset"), "sha256": block.get("sha256"), "verified_by": srec.get("verified_by"),
                  "first_use": srec.get("first_use"), "version": prepared["prepare"]["server"].get("version"),
                  "starts": [], "restarts": 0}
        model = {"key": shard["model"], "kind": entry["kind"], "alias": entry["alias"],
                 **{k: mblock.get(k) for k in ("repo", "file", "revision", "commit", "sha256", "size")},
                 "verified_by": mrec.get("verified_by"), "license": mblock.get("license")}
        provision = {"server_record_sha256": prepared["server_record_sha256"],
                     "model_record_sha256": prepared["model_record_sha256"],
                     "prepare_sha256": prepared["prepare_sha256"]}
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
        "server": server, "model": model, "provision": provision,
        "deadline_epoch": deadline, "started_at": utc_clock(), "finished_at": None, "wall_s": None,
        "planned_units": list(shard["units"]), "units": [], "exit_code": None,
    }


def serving_class(unit: dict[str, Any]) -> str:
    return "e3" if unit["experiment"] == "e3" else "quality"


class _ModelServing:
    """The prepared model server across one shard's units: one start per run of consecutive same-class units, the
    warm-up, the watch handle each unit gets, the health check after it, and the restart budget."""

    def __init__(self, plan: dict[str, Any], shard: dict[str, Any], out: Path, signals: _Signals, deadline: float,
                 prepared: dict[str, Any], provenance: dict[str, Any]) -> None:
        self.plan, self.shard, self.out, self.signals, self.deadline = plan, shard, out, signals, deadline
        self.prepared, self.provenance = prepared, provenance
        self.units = [u for uid in shard["units"] for u in plan["units"] if u["unit"] == uid]
        self.entry = plan["models"][shard["model"]]
        self.server: ModelServer | None = None
        self.cls: str | None = None
        self.failure: str | None = None
        self.skips: dict[str, str] = {}
        self.unplanned = 0
        self.pending_restart = False
        self.unavailable = False
        self.note = ""

    def handle(self, unit: dict[str, Any]) -> Serving | str:
        """A serving handle for the unit, or the reason it is skipped. May start the server (interruptible)."""
        if self.unavailable:
            return SERVER_UNAVAILABLE
        cls = serving_class(unit)
        if cls != self.cls:
            self.stop()
            self.cls, self.failure, self.skips = cls, None, {}
            self._start(unit)
        elif self.server is None and self.failure is None:
            self._start(unit)
        if self.failure is not None:
            return self.failure
        if unit["unit"] in self.skips:
            return self.skips[unit["unit"]]
        assert self.server is not None
        return Serving(base_url=self.server.base_url, alias=self.server.alias, host_label=self.server.host_label,
                       server_note=self.note, poll=self.server.poll, evidence={**self.prepared,
                                                                                "start": self.server.record},
                       start=self.server.index, class_name=cls)

    def _start(self, unit: dict[str, Any]) -> None:
        cls = serving_class(unit)
        index = self.units.index(unit)
        block = []
        for later in self.units[index:]:
            if serving_class(later) != cls:
                break
            block.append(later)
        health_deadline = min(HEALTH_DEADLINE_S, self.deadline - time.time() - DEADLINE_MARGIN_S)
        if health_deadline < MIN_UNIT_SECONDS:
            self.failure = BUDGET_EXHAUSTED
            return
        prepare, srec = self.prepared["prepare"], self.prepared["server_record"]
        block_server = srec["server"]
        if cls == "e3":
            slots = max(c for u in self.units if serving_class(u) == "e3" for c in u["params"]["concurrency"])
            ctx, field = self.entry["e3_ctx_per_slot"], "e3_ctx_per_slot"
        else:
            slots, ctx, field = 1, self.entry["ctx_per_slot"], "ctx_per_slot"
        threads, threads_batch = thread_counts(self.provenance["host"], block_server.get("threads", "physical"))
        spec = ServerSpec(binary=self.out / prepare["server"]["binary"], model_path=Path(prepare["model"]["path"]),
                          alias=self.entry["alias"], tag=block_server["tag"], slots=slots, ctx_per_slot=ctx,
                          seed=unit["seeds"][0], threads=threads, threads_batch=threads_batch,
                          cache_ram_mib=block_server.get("cache_ram_mib", 1024),
                          extra_args=(*block_server.get("args", []), *self.entry.get("server_args", [])))
        starts = self.provenance["server"]["starts"]
        server = ModelServer(spec, out=self.out, key=self.shard["model"], index=len(starts),
                             health_deadline_s=health_deadline)
        server.record.update({"class": cls, "units": [u["unit"] for u in block], "restart": self.pending_restart})
        if self.pending_restart:
            self.provenance["server"]["restarts"] += 1
            self.pending_restart = False
        starts.append(server.record)
        self.note = SERVER_NOTE_MODEL.format(
            program=block_server.get("program"), tag=block_server["tag"],
            server_digest=block_server["sha256"][:16], verified_by=srec.get("verified_by"), key=self.shard["model"],
            model_digest=prepare["model"]["sha256"][:16], slots=slots, ctx=ctx, threads=threads,
            threads_batch=threads_batch)
        try:
            server.start()
            self.server = server
            record, self.skips, stop_all = warm_up(server, self.entry, warm_tasks(block, self.plan),
                                                   ctx_per_slot=ctx, ctx_field=field, out=self.out,
                                                   start_index=server.index, meminfo=hostinfo.mem_available,
                                                   budget_s=self.deadline - time.time() - DEADLINE_MARGIN_S
                                                   - MIN_UNIT_SECONDS)
        except ServerError as err:
            server.stop()
            server.record["problem"] = err.problem
            self.server, self.failure = None, err.problem
            return
        except BaseException:
            server.stop()
            self.server = None
            raise
        server.record["warmup"] = record
        server.record["mem_available_after_load"] = record["mem_available_after_load"]
        exited = server.poll(lab_server.EXIT_GRACE_S if record["unanswered"] else 0)
        if exited is not None:
            # it died under the warm-up's load (SIGKILL: most likely out of memory); its failed calls say nothing
            # about context or schemas, so the exit is the reason, and it counts against the restart budget
            stop_all = lab_server.exit_reason(exited)
            self._unplanned()
        if stop_all is not None:
            server.stop()
            server.record["problem"] = stop_all
            self.server, self.failure = None, stop_all

    def after_unit(self, record: dict[str, Any]) -> None:
        """Count an exit during the unit, or an unhealthy ``/health`` after it, as an unplanned exit."""
        if self.server is None:
            return
        unplanned = self.server.poll() is not None
        if not unplanned:
            status = self.server.health(lab_server.AFTER_UNIT_HEALTH_S)
            if status != 200:
                record["server_after"] = {"health": status, "note": SERVER_UNHEALTHY_AFTER}
                unplanned = True
        if unplanned:
            self.server.stop()
            self.server = None
            self._unplanned()

    def _unplanned(self) -> None:
        """The first unplanned exit of a shard is tolerated (the next start is a restart); a second makes the server
        unavailable for every remaining unit."""
        self.unplanned += 1
        if self.unplanned >= 2:
            self.unavailable = True
        else:
            self.pending_restart = True

    def stop(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None


def run_shard(plan_path: str, shard_id: str, out: Path, provider_override: str | None,
              deadline_epoch: float | None) -> int:
    plan, plan_bytes = _load_plan(plan_path)
    shard = next((s for s in plan["shards"] if s["shard"] == shard_id), None)
    if shard is None:
        raise ShardError("$", "the shard is not in the plan") from None
    _check_out(out)
    prepared = None
    if shard["kind"] != "fake" and plan["provider"] != "fake" and provider_override != "fake":
        prepared = _load_prepared(out, shard_id, sha256_hex(plan_bytes))
    units = {u["unit"]: u for u in plan["units"]}
    provider = "fake" if provider_override == "fake" else plan["provider"]
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    deadline = deadline_epoch if deadline_epoch is not None else time.time() + plan["shard_capacity_minutes"] * 60
    signals = _Signals()
    previous = {sig: signal.signal(sig, signals) for sig in (signal.SIGTERM, signal.SIGINT)}
    serving: _ModelServing | None = None
    try:
        provenance = _provenance(plan, plan_path, plan_bytes, shard, out, provider_override, deadline, prepared)
        write_json_atomic(out / "provenance.json", provenance)
        if prepared is not None:
            serving = _ModelServing(plan, shard, out, signals, deadline, prepared, provenance)
        skip_reason: str | None = None
        for uid in shard["units"]:
            unit = units[uid]
            if signals.received is not None:
                skip_reason = SHARD_INTERRUPTED
            if skip_reason is None and deadline - time.time() - DEADLINE_MARGIN_S < MIN_UNIT_SECONDS:
                skip_reason = BUDGET_EXHAUSTED
            if skip_reason is not None:
                record = unit_record(unit, shard_id, provider, provider_override, status_reason=skip_reason)
            else:
                record = None
                try:
                    signals.armed = True
                    handle = serving.handle(unit) if serving is not None else None
                    timeout_s = min(unit["minutes"] * 60, deadline - time.time() - DEADLINE_MARGIN_S)
                    if isinstance(handle, str) or timeout_s < MIN_UNIT_SECONDS:
                        reason = handle if isinstance(handle, str) else BUDGET_EXHAUSTED
                        if reason == BUDGET_EXHAUSTED:
                            skip_reason = reason
                        record = unit_record(unit, shard_id, provider, provider_override, status_reason=reason)
                    else:
                        record = run_unit(unit, plan, out, timeout_s=timeout_s, provider_override=provider_override,
                                          serving=handle)
                        if serving is not None:
                            serving.after_unit(record)
                    signals.armed = False
                except ShardInterrupted:
                    # the handler disarmed itself; the signal landed outside run_unit's own interrupt handling
                    shutil.rmtree(out / "work" / uid, ignore_errors=True)
                    if serving is not None:
                        serving.stop()
                    if record is None:
                        record = unit_record(unit, shard_id, provider, provider_override, status="interrupted",
                                             status_reason=SHARD_INTERRUPTED)
            (out / "units" / uid).mkdir(parents=True, exist_ok=True)
            write_json_atomic(out / "units" / uid / "unit.json", record)
            print(_status_line(record), flush=True)
            provenance["units"].append({k: record[k] for k in ("unit", "run_id", "experiment", "status",
                                                                "measurement_class", "exit_code", "wall_s")})
            write_json_atomic(out / "provenance.json", provenance)
        if serving is not None:
            serving.stop()
        failing = any(u["status"] in FAILING or u["status"] == "skipped" for u in provenance["units"])
        code = EXIT_UNIT if failing else EXIT_OK
        provenance.update(complete=True, interrupted=signals.received is not None, finished_at=utc_clock(),
                          wall_s=round(time.monotonic() - t0, 3), exit_code=code)
        write_json_atomic(out / "provenance.json", provenance)
        return code
    finally:
        signals.armed = False
        if serving is not None:
            serving.stop()
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
    for rel in SEALED_AWAY:
        shutil.rmtree(out / rel, ignore_errors=True)
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
    p = argparse.ArgumentParser(prog="python -m lab.shard", description="Prepare, run or seal one shard of a lab plan.")
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("prepare", help="restore, verify and extract what a gguf shard needs")
    q.add_argument("--plan", required=True)
    q.add_argument("--shard", required=True)
    q.add_argument("--provision-records", required=True)
    q.add_argument("--cache-root", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--github-output")
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


def main(argv: list[str] | None = None, *, url_map: UrlMap | None = None,
         sleep: Callable[[float], Any] = time.sleep) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare":
        return lab_provision.prepare(args.plan, args.shard, args.provision_records, Path(args.cache_root),
                                     Path(args.out), github_output=args.github_output, url_map=url_map, sleep=sleep)
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
