"""The openFDA unit: fetch public device events, replay them with recalls held back, and write labelling sheets.

:func:`run_unit` runs the collective's openFDA connector and harnesses as steps, each a subprocess (through
``units.run_process``) with the units' environment allowlist, in this order, a step running only when the steps it
needs succeeded (else ``skipped``, :data:`~lab.notes.STEP_SKIPPED`):

1. ``fetch-events``: ``connectors.openfda fetch --dataset=event`` into ``R/cache/events``;
2. ``replay-prereg`` (needs 1): ``openfda_replay prereg``, freezing the settings and the events cache's hash;
3. ``replay-signals`` (needs 2): phase 1, the alerts from the events alone;
4. ``fetch-recalls`` (needs 3): ``fetch --dataset=recall`` into ``R/cache/recalls``, so no recall is fetched before
   the signals are frozen;
5. ``replay-score`` (needs 4): the frozen signals scored against the recalls;
6. ``n1-sample`` (needs 1, when the block asks for an ``n1_sheet``): the N1 labelling sheet;
7. ``e1-prepare`` (needs 1, when the block asks for an ``e1_sheet``): the openFDA E1 labelling sheet.

``R`` is ``units.harness_dir`` (removed when the unit ends); the harnesses write under ``R/runs``. The lab produces
sheets only: a human labels them, and N1's scoring step and E1's label check run outside the lab, so no label and no
N1 or openFDA E1 result is ever generated here.

The fetch steps get ``--api-key-env=MYCELIC_LAB_OPENFDA_API_KEY`` and that one variable in their environment only when
it is set and non-empty (the workflow passes the repository secret to the openFDA shard's run step); no other step
ever sees it, and the connector puts it on the wire only. ``base_url_override`` (``lab.shard run --openfda-base-url``,
tests only) points both fetches at a stub.

Each step's time is what is left of the unit's (``timeout_s`` from the start); under 1 s left, the step is not started
and is ``timed_out``. A fetch that exits 3 is :data:`~lab.notes.OPENFDA_UNREACHABLE`; one that exits 2 with a first
stderr line starting ``error: HTTP 429 `` (retries exhausted) :data:`~lab.notes.OPENFDA_RATE_LIMITED`; another exit
2 :data:`~lab.notes.OPENFDA_FETCH_REFUSED`; a harness exit 2 is ``the harness refused its configuration: <step>``; a
signal, another exit, or exit 0 without the step's file are failures too, and a shard interrupt marks the running step
``interrupted`` and skips the rest. The unit is ``interrupted``, else ``timed_out``, else ``failed`` (the first failed
step's reason), else ``ok``. Only the files :data:`lab.units.OPENFDA_COLLECT` names are kept, under
``runs/openfda/<run id>/``: the caches' manifests (never their pages), the replay's files and the sheets.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from mycelic.collective.experiments.common import utc_clock

from . import ROOT
from . import units as lab_units
from .notes import (HARNESS_INTERRUPTED, HARNESS_USAGE, KILLED_BY_SIGNAL, OPENFDA_FETCH_REFUSED, OPENFDA_RATE_LIMITED,
                    OPENFDA_UNREACHABLE, RESULT_MISSING, SHARD_INTERRUPTED, STEP_SKIPPED, TIMED_OUT, UNEXPECTED_EXIT)

KEY_VAR = "MYCELIC_LAB_OPENFDA_API_KEY"
CONNECTOR = "mycelic.collective.connectors.openfda"
REPLAY = "mycelic.collective.experiments.openfda_replay"
N1 = "mycelic.collective.experiments.n1_narratives"
E1 = "mycelic.collective.experiments.e1_extract"
RATE_LIMITED_PREFIX = "error: HTTP 429 "
MIN_STEP_S = 1.0
FETCH_STEPS = ("fetch-events", "fetch-recalls")
STEP_FILES = {"fetch-events": "cache/events/manifest.json", "replay-prereg": "runs/replay/prereg/prereg.json",
              "replay-signals": "runs/replay/signals/signals.json", "fetch-recalls": "cache/recalls/manifest.json",
              "replay-score": "runs/replay/score/replay.json", "n1-sample": "runs/n1/n1/sample.json",
              "e1-prepare": "runs/e1/e1/prepare.json"}
NEEDS = {"fetch-events": None, "replay-prereg": "fetch-events", "replay-signals": "replay-prereg",
         "fetch-recalls": "replay-signals", "replay-score": "fetch-recalls", "n1-sample": "fetch-events",
         "e1-prepare": "fetch-events"}


def key_present(environ: Mapping[str, str]) -> bool:
    return bool(environ.get(KEY_VAR))


def step_argvs(params: Mapping[str, Any], r: Path, *, key: bool,
               base_url: str | None) -> list[tuple[str, list[str]]]:
    """(step, argv) for each step the block asks for, in order; every option is one ``--flag=value`` element."""
    flag = lab_units.flag
    codes = ",".join(params["product_codes"])
    dates = [flag("date-from", params["date_from"]), flag("date-to", params["date_to"])]

    def fetch(dataset: str, out: Path) -> list[str]:
        extra = [flag("api-key-env", KEY_VAR)] if key else []
        extra += [flag("base-url", base_url)] if base_url is not None else []
        return [sys.executable, "-m", CONNECTOR, "fetch", flag("dataset", dataset), flag("product-codes", codes),
                *dates, flag("out", out), flag("max-records", params["max_records_per_code"]), *extra]

    runs = r / "runs"
    prereg = runs / "replay" / "prereg" / "prereg.json"
    replay_prereg = [
        sys.executable, "-m", REPLAY, "prereg", flag("pack", params["pack"]),
        flag("events-cache", r / "cache" / "events"), *(flag("manufacturer", m) for m in params["manufacturers"]),
        flag("manufacturer-field", params["manufacturer_field"]), flag("partition-field", params["partition_field"]),
        *dates, flag("tie-salt", params["tie_salt"]), flag("saw-recall-outcomes", params["saw_recall_outcomes"]),
        *(flag("recalling-firm", f) for f in params["recalling_firms"]),
        flag("min-partition-coverage", params["min_partition_coverage"]),
        flag("lookback-weeks", params["lookback_weeks"]), flag("post-weeks", params["post_weeks"]),
        flag("run-id", "prereg"), flag("runs-dir", runs)]
    steps = [("fetch-events", fetch("event", r / "cache" / "events")), ("replay-prereg", replay_prereg),
             ("replay-signals", [sys.executable, "-m", REPLAY, "signals", flag("prereg", prereg),
                                 flag("run-id", "signals"), flag("runs-dir", runs)]),
             ("fetch-recalls", fetch("recall", r / "cache" / "recalls")),
             ("replay-score", [sys.executable, "-m", REPLAY, "score", flag("prereg", prereg),
                               flag("signals", runs / "replay" / "signals"),
                               flag("recalls-cache", r / "cache" / "recalls"), flag("run-id", "score"),
                               flag("runs-dir", runs)])]
    if params.get("n1_sheet") is not None:
        sheet = params["n1_sheet"]
        steps.append(("n1-sample", [sys.executable, "-m", N1, "sample", flag("cache", r / "cache" / "events"),
                                    flag("product-codes", codes), flag("n", sheet["n"]), flag("seed", sheet["seed"]),
                                    flag("run-id", "n1"), flag("runs-dir", runs)]))
    if params.get("e1_sheet") is not None:
        sheet = params["e1_sheet"]
        steps.append(("e1-prepare", [sys.executable, "-m", E1, "prepare", flag("pack", params["pack"]),
                                     flag("source", "openfda"), flag("cache", r / "cache" / "events"),
                                     flag("n", sheet["n"]), flag("seed", sheet["seed"]), flag("run-id", "e1"),
                                     flag("runs-dir", runs)]))
    return steps


def _first_line(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.readline().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _outcome(step: str, result: Any, r: Path, stderr_path: Path) -> tuple[str, str | None]:
    """(status, reason) of a finished step."""
    if result.timed_out:
        return "timed_out", TIMED_OUT
    code = result.exit_code
    if code is None:
        return "failed", f"{UNEXPECTED_EXIT}: {step}"
    if code in (130, -2):
        return "interrupted", HARNESS_INTERRUPTED
    if code < 0:
        return "failed", KILLED_BY_SIGNAL
    if step in FETCH_STEPS and code == 3:
        return "failed", OPENFDA_UNREACHABLE
    if step in FETCH_STEPS and code == 2:
        rate_limited = _first_line(stderr_path).startswith(RATE_LIMITED_PREFIX)
        return "failed", OPENFDA_RATE_LIMITED if rate_limited else OPENFDA_FETCH_REFUSED
    if code == 2:
        return "failed", f"{HARNESS_USAGE}: {step}"
    if code != 0:
        return "failed", f"{UNEXPECTED_EXIT}: {step}"
    if not (r / STEP_FILES[step]).is_file():
        return "failed", f"{RESULT_MISSING}: {step}"
    return "ok", None


def run_unit(unit: dict[str, Any], plan: Mapping[str, Any], out: Path, *, timeout_s: float,
             base_url_override: str | None = None, provider_override: str | None = None) -> dict[str, Any]:
    out = Path(out)
    r = lab_units.harness_dir(unit, out)
    unit_dir = out / "units" / unit["unit"]
    r.mkdir(parents=True, exist_ok=True)
    (r / "cache").mkdir(exist_ok=True)
    unit_dir.mkdir(parents=True, exist_ok=True)
    key = key_present(os.environ)
    started_at, t0 = utc_clock(), time.monotonic()
    deadline = t0 + timeout_s
    steps: list[dict[str, Any]] = []
    logs: dict[str, Any] = {}
    status_of: dict[str, str] = {}
    interrupted = False
    for step, argv in step_argvs(unit["params"], r, key=key, base_url=base_url_override):
        record: dict[str, Any] = {"step": step, "argv": argv, "exit_code": None, "signal": None, "timed_out": False,
                                  "wall_s": 0.0, "status": "skipped", "reason": STEP_SKIPPED}
        steps.append(record)
        need = NEEDS[step]
        if interrupted or (need is not None and status_of.get(need) != "ok"):
            status_of[step] = "skipped"
            continue
        left = deadline - time.monotonic()
        if left < MIN_STEP_S:
            record.update(timed_out=True, status="timed_out", reason=TIMED_OUT)
            status_of[step] = "timed_out"
            continue
        env = lab_units.subprocess_env(os.environ, [KEY_VAR] if key and step in FETCH_STEPS else [])
        stdout_path, stderr_path = unit_dir / f"{step}.stdout.log", unit_dir / f"{step}.stderr.log"
        try:
            result = lab_units.run_process(argv, env, cwd=ROOT, timeout_s=left, stdout_path=stdout_path,
                                           stderr_path=stderr_path)
            status, reason = _outcome(step, result, r, stderr_path)
        except lab_units.ShardInterrupted as exc:
            interrupted = True
            result = exc.process
            status, reason = "interrupted", SHARD_INTERRUPTED
        if result is not None:
            record.update(exit_code=result.exit_code, signal=result.signal_name, timed_out=result.timed_out,
                          wall_s=result.wall_s)
        record.update(status=status, reason=reason)
        status_of[step] = status
        logs[f"{step}.stdout"] = lab_units.cap_log(stdout_path)
        logs[f"{step}.stderr"] = lab_units.cap_log(stderr_path)
    unit_status, unit_reason = "ok", None
    for status in ("interrupted", "timed_out", "failed"):
        first = next((s for s in steps if s["status"] == status), None)
        if first is not None:
            unit_status, unit_reason = status, first["reason"]
            break
    ended = next((s for s in steps if s["status"] not in ("ok", "skipped")), None)
    files = lab_units.collect(unit, out)
    shutil.rmtree(out / "work" / unit["unit"], ignore_errors=True)
    provider = "fake" if provider_override == "fake" else plan["provider"]
    measurement, class_reason = lab_units.measurement_class(unit["kind"], provider_override, [])
    return lab_units.unit_record(
        unit, unit["shard"], provider, provider_override, timeout_s=round(timeout_s, 3), started_at=started_at,
        finished_at=utc_clock(), wall_s=round(time.monotonic() - t0, 3),
        exit_code=0 if unit_status == "ok" else ended["exit_code"] if ended is not None else None,
        signal=ended["signal"] if ended is not None else None, status=unit_status, status_reason=unit_reason,
        measurement_class=measurement, class_reason=class_reason, notes=lab_units.unit_notes("openfda", measurement),
        files=files, logs=logs, steps=steps, harness_measurement=None)
