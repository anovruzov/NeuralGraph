"""Merge one run's shard artifacts into one report: which artifact counts for each shard, what each unit gave, the
E3, G0 and latency rows, the provision records and the lock candidate.

    python -m lab.aggregate --plan FILE --provision DIR --shards DIR --manifest FILE --out DIR

``--shards`` holds the downloaded run artifacts in either layout download-artifact produces: ``DIR`` itself is a
shard root (one artifact matched, extracted flat), or each child directory holding ``status.json`` or
``provenance.json`` is one; a missing ``DIR`` holds none. ``--provision`` is read the same way
(``provision.load_records``). A root's artifact name is its directory name when that is ``lab-run-<run id>-<attempt>-
<shard>``, else null: the attempt and the shard are always read from ``status.json``, never from a name.

Per plan shard, among the roots whose status (else provenance) names it: the highest ``run_attempt`` (default 1)
wins, and two roots of that attempt make the shard ``ambiguous``. The chosen root is ``other_plan`` when its status
or provenance names another plan's sha256, ``unsealed`` without a readable status, ``altered`` when a file of its
sealed list is missing or changed or a file outside that list, ``status.json`` and ``summary/`` is present, else
``sealed``; a shard without a root is ``no_artifact``. Roots of shards the plan does not hold are listed by artifact
name in ``ignored_artifacts``.

Every planned unit gets one row (sorted by unit id). In a sealed shard, a ``unit.json`` naming the planned unit and
run id is copied (status, reasons, classes, exit code, wall time, seeds, notes) and its ``display_class``
(``units.display_class``, never the record's own class alone) and CPU model added; when a file the record lists no
longer hashes as recorded the unit is ``excluded`` (``a unit file differs from the unit's record``). A sealed shard
without the unit's record gives ``not_run`` (the prepare problem, else the failed step, as the reason); a shard
without an artifact gives ``not_run`` and any other state ``excluded``, with the state's sentence as the reason. Units
that ran to a result give one E3 row per cell of their ``e3.json``, one G0 row per ``leakage.json`` (never its hit
lists) and their ledgers' successful calls, grouped into latency rows by display class, model, CPU model,
experiment and task (CPU models are never pooled): ``n`` and the 50th and 95th percentiles in ms
(``stats.percentile``, rounded to 3 places).

The lock: ``not_computed`` when the manifest or lock no longer hashes as the plan recorded or the provision records
are ambiguous; otherwise the verified records of this plan are merged into the current lock
(``provision.merge_candidates``): ``conflict`` (no file), else ``unchanged`` or ``new_entries`` and
``lock-candidate.json`` written for the team to commit.

``DIR`` must be absent or empty; it receives ``report.json`` (canonical JSON, no clock value: the same inputs give the
same bytes), ``plan.json`` (a byte copy) and ``lock-candidate.json``. stdout is one line::

    lab: aggregate units <n> shards <n> class <plumbing|real> measurements <true|false> lock <status>

Exit 0 once the report is written, whatever the units gave (their jobs already failed); 2 for a non-empty ``DIR``,
an unreadable plan or a path inside ``mycelic/``, ``research/``, ``NeuralGraph/`` or ``.github/``.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mycelic.collective.experiments.common import RUN_ID_RE, write_json_atomic
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.stats import percentile

from . import EXIT_OK, EXIT_USAGE, forbidden_root
from . import provision as lab_provision
from .manifest import ManifestError, load_manifest, lock_path
from .notes import (ALTERED, AMBIGUOUS_ARTIFACTS, FILES_DIFFER, NO_ARTIFACT, NOT_RUN, OTHER_PLAN, PLUMBING_BANNER,
                    STEP_FAILED, UNIT_RECORD_INVALID, UNSEALED)
from .plan import SHARD_ID_RE, UNIT_ID_RE
from .units import ADAPTERS, display_class, ledger_rows

ARTIFACT_RE = re.compile(r"lab-run-[0-9]{1,20}-[0-9]{1,6}-" + SHARD_ID_RE.pattern, re.ASCII)
STATE_REASONS = {"other_plan": OTHER_PLAN, "altered": ALTERED, "ambiguous": AMBIGUOUS_ARTIFACTS,
                 "unsealed": UNSEALED, "no_artifact": NO_ARTIFACT}
UNSEALED_DIR = "summary"
UNIT_FIELDS = ("status", "status_reason", "measurement_class", "class_reason", "kind", "provider", "exit_code",
               "wall_s", "seeds", "notes")
E3_FIELDS = ("workload", "task", "concurrency", "measured", "ok", "ttft_s", "e2e_s", "decode_tok_s", "throughput")
G0_FIELDS = ("pack", "pack_version", "seed", "records", "passed", "canaries_planted", "hit_count",
             "shingle_overlap_bytes", "world_digest")


class AggregateError(ValueError):
    pass


@dataclass
class _Root:
    path: Path
    artifact: str | None
    status: dict[str, Any] | None
    provenance: dict[str, Any] | None
    shard: str | None
    attempt: int


def _read(path: Path) -> Any:
    failed = False
    try:
        obj = strict_load(path.read_bytes())
    except (OSError, StrictJsonError):
        failed = True
    return None if failed else obj


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


# --------------------------------------------------------------------------------------------------- the plan

def load_plan(path: str | os.PathLike[str]) -> tuple[dict[str, Any], bytes]:
    try:
        data = Path(path).read_bytes()
        plan = strict_load(data)
    except (OSError, StrictJsonError):
        raise AggregateError("the plan cannot be read") from None

    def ok_id(obj: Any, key: str, pattern: re.Pattern[str]) -> bool:
        return isinstance(obj, dict) and isinstance(obj.get(key), str) and pattern.fullmatch(obj[key]) is not None

    ok = (isinstance(plan, dict) and plan.get("kind") == "lab_plan" and plan.get("schema_version") == 1
          and all(isinstance(plan.get(k), dict) for k in ("request", "manifest", "lock"))
          and isinstance(plan.get("units"), list) and isinstance(plan.get("shards"), list)
          and all(ok_id(u, "unit", UNIT_ID_RE) and ok_id(u, "run_id", RUN_ID_RE) and ok_id(u, "shard", SHARD_ID_RE)
                  and u.get("experiment") in ADAPTERS for u in plan["units"])
          and all(ok_id(s, "shard", SHARD_ID_RE) and isinstance(s.get("units"), list) for s in plan["shards"]))
    if not ok:
        raise AggregateError("not a lab plan") from None
    return plan, data


# --------------------------------------------------------------------------------------------------- shard roots

def find_roots(shards_dir: Path) -> list[_Root]:
    """The shard roots under ``shards_dir`` in either download layout; see the module docstring."""
    if (shards_dir / "status.json").exists() or (shards_dir / "provenance.json").exists():
        candidates = [shards_dir]
    elif shards_dir.is_dir() and not shards_dir.is_symlink():
        candidates = sorted(c for c in shards_dir.iterdir() if c.is_dir() and not c.is_symlink()
                            and ((c / "status.json").exists() or (c / "provenance.json").exists()))
    else:
        candidates = []
    roots = []
    for path in candidates:
        status = _read(path / "status.json")
        status = status if isinstance(status, dict) and status.get("kind") == "lab_shard_status" else None
        prov = _read(path / "provenance.json")
        prov = prov if isinstance(prov, dict) and prov.get("kind") == "lab_provenance" else None
        shard = _get(status, "shard")
        if not isinstance(shard, str):
            shard = _get(prov, "shard")
        attempt = _get(status, "run_attempt")
        roots.append(_Root(path=path, artifact=path.name if ARTIFACT_RE.fullmatch(path.name) else None,
                           status=status, provenance=prov, shard=shard if isinstance(shard, str) else None,
                           attempt=attempt if _is_int(attempt) and attempt >= 1 else 1))
    return roots


def _file_ok(root: Path, rel: Any, meta: Any) -> bool:
    if not isinstance(rel, str) or not isinstance(meta, dict) or rel.startswith("/") or ".." in rel.split("/"):
        return False
    path = root / rel
    if path.is_symlink() or not path.is_file():
        return False
    data = path.read_bytes()
    return meta.get("sha256") == sha256_hex(data) and meta.get("bytes") == len(data)


def _altered(root: _Root) -> bool:
    files = _get(root.status, "files")
    if not isinstance(files, dict) or not all(_file_ok(root.path, rel, meta) for rel, meta in files.items()):
        return True
    for directory, dirnames, filenames in os.walk(root.path):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            path = base / name
            rel = path.relative_to(root.path).as_posix()
            if rel == UNSEALED_DIR or rel.startswith(UNSEALED_DIR + "/"):
                continue
            if path.is_symlink():
                return True
            if name in filenames and rel not in files and rel != "status.json":
                return True
    return False


def shard_state(root: _Root, plan_sha256: str) -> str:
    if root.status is not None and root.status.get("plan_sha256") is not None \
            and root.status["plan_sha256"] != plan_sha256:
        return "other_plan"
    if root.provenance is not None and _get(root.provenance, "plan", "sha256") != plan_sha256:
        return "other_plan"
    if root.status is None:
        return "unsealed"
    return "altered" if _altered(root) else "sealed"


def choose(roots: list[_Root], shard_id: str, plan_sha256: str) -> tuple[str, _Root | None, int | None]:
    """(state, chosen root, its attempt) for one plan shard."""
    mine = [r for r in roots if r.shard == shard_id]
    if not mine:
        return "no_artifact", None, None
    top = max(r.attempt for r in mine)
    newest = [r for r in mine if r.attempt == top]
    if len(newest) > 1:
        return "ambiguous", None, top
    return shard_state(newest[0], plan_sha256), newest[0], top


# --------------------------------------------------------------------------------------------------- rows

def _shard_row(shard: dict[str, Any], state: str, root: _Root | None, attempt: int | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "shard": shard["shard"], "model": shard.get("model"), "kind": shard.get("kind"), "state": state,
        "state_reason": STATE_REASONS.get(state), "artifact": root.artifact if root is not None else None,
        "run_attempt": attempt, "failed_step": None, "prepare_problem": None, "provenance_complete": None,
        "interrupted": None, "exit_code": None, "wall_s": None, "host": None, "server": None, "model_file": None}
    if state != "sealed" or root is None:
        return row
    status, prov = root.status or {}, root.provenance
    row.update(failed_step=status.get("failed_step"), prepare_problem=_get(status, "prepare", "problem"),
               provenance_complete=_get(status, "provenance", "complete"),
               interrupted=_get(status, "provenance", "interrupted"),
               exit_code=_get(prov, "exit_code"), wall_s=_get(prov, "wall_s"))
    host = _get(prov, "host")
    if isinstance(host, dict):
        row["host"] = {"cpu_model": _get(host, "cpu", "model_name"), "nproc_available": host.get("nproc_available"),
                       "mem_total_bytes": host.get("mem_total_bytes"),
                       "os": _get(host, "os_release", "PRETTY_NAME"), "python": host.get("python")}
    server, model = _get(prov, "server"), _get(prov, "model")
    if isinstance(server, dict) and server.get("kind") == "model":
        row["server"] = {k: server.get(k) for k in ("tag", "version", "sha256", "verified_by")}
        if isinstance(model, dict):
            row["model_file"] = {k: model.get(k) for k in ("repo", "file", "commit", "sha256", "verified_by")}
    return row


def _not_run_reason(status: dict[str, Any]) -> str:
    problem = _get(status, "prepare", "problem")
    if isinstance(problem, str):
        return problem
    step = status.get("failed_step")
    return f"{STEP_FAILED} {step}" if isinstance(step, str) else NOT_RUN


def _unit_row(unit: dict[str, Any], state: str, root: _Root | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The unit's row and, when it holds a record, the record."""
    row: dict[str, Any] = {"unit": unit["unit"], "run_id": unit["run_id"], "experiment": unit["experiment"],
                           "model": unit.get("model"), "shard": unit["shard"], **dict.fromkeys(UNIT_FIELDS),
                           "kind": unit.get("kind"), "display_class": "no-result", "cpu_model": None}
    if state != "sealed" or root is None:
        row.update(status="not_run" if state == "no_artifact" else "excluded", status_reason=STATE_REASONS[state])
        return row, None
    path = root.path / "units" / unit["unit"] / "unit.json"
    if not path.exists():
        row.update(status="not_run", status_reason=_not_run_reason(root.status or {}))
        return row, None
    record = _read(path)
    if not isinstance(record, dict) or record.get("unit") != unit["unit"] or record.get("run_id") != unit["run_id"]:
        row.update(status="excluded", status_reason=UNIT_RECORD_INVALID)
        return row, None
    files = record.get("files")
    if not isinstance(files, dict) or not all(_file_ok(root.path, rel, meta) for rel, meta in files.items()):
        row.update(status="excluded", status_reason=FILES_DIFFER)
        return row, None
    row.update({k: record.get(k) for k in UNIT_FIELDS})
    row.update(display_class=display_class(record, root.provenance),
               cpu_model=_get(root.provenance, "host", "cpu", "model_name"))
    return row, record


def _e3_rows(row: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    result = _read(root / "runs" / "e3" / row["run_id"] / "e3.json")
    cells = result.get("cells") if isinstance(result, dict) and result.get("kind") == "e3" else None
    if not isinstance(cells, list):
        return []
    measurement = result.get("measurement") is True
    return [{"unit": row["unit"], "model": row["model"], "cpu_model": row["cpu_model"],
             "display_class": row["display_class"], "measurement": measurement,
             **{k: cell.get(k) for k in E3_FIELDS}} for cell in cells if isinstance(cell, dict)]


def _g0_row(row: dict[str, Any], root: Path) -> dict[str, Any] | None:
    result = _read(root / "runs" / "g0" / row["run_id"] / "leakage.json")
    if not isinstance(result, dict) or result.get("kind") != "g0_leakage":
        return None
    control = result.get("positive_control")
    return {"unit": row["unit"], "model": row["model"], "display_class": row["display_class"],
            **{k: result.get(k) for k in G0_FIELDS},
            "positive_control": ({"canary_hits": control.get("canary_hits"),
                                  "shingle_overlap_bytes": control.get("shingle_overlap_bytes")}
                                 if isinstance(control, dict) else None)}


def _sort_key(values: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple("" if v is None else str(v) for v in values)


def latency_rows(samples: dict[tuple[Any, ...], list[float]]) -> list[dict[str, Any]]:
    rows = []
    for key in sorted(samples, key=_sort_key):
        values = samples[key]
        cls, model, cpu, experiment, task = key
        rows.append({"display_class": cls, "model": model, "cpu_model": cpu, "experiment": experiment, "task": task,
                     "n": len(values), "p50_ms": round(percentile(values, 50), 3),
                     "p95_ms": round(percentile(values, 95), 3)})
    return rows


def world_digest_groups(g0: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in g0:
        key = (row["pack"], row["pack_version"], row["seed"], row["records"])
        group = groups.setdefault(key, {"pack": row["pack"], "pack_version": row["pack_version"], "seed": row["seed"],
                                        "records": row["records"], "digests": set(), "units": []})
        group["digests"].add(row["world_digest"])
        group["units"].append(row["unit"])
    out = []
    for key in sorted(groups, key=_sort_key):
        group = groups[key]
        digests = sorted(group["digests"], key=lambda d: "" if d is None else str(d))
        out.append({**group, "digests": digests, "units": sorted(group["units"]), "consistent": len(digests) == 1})
    return out


# --------------------------------------------------------------------------------------------------- provision

def provision_rows(records: dict[tuple[str, str], tuple[dict[str, Any], bytes]] | None,
                   plan_sha256: str) -> list[dict[str, Any]]:
    if records is None:
        return [{"state": "ambiguous", "target": None, "key": None, "run_attempt": None, "verified": None,
                 "verified_by": None, "first_use": None, "problem": None, "source": None, "sha256": None,
                 "plan_matches": None, "total_s": None}]
    rows = []
    for (target, key), (rec, _) in sorted(records.items(), key=lambda kv: (kv[0][0] != "server", kv[0][1])):
        block = rec.get("server") if target == "server" else rec.get("model")
        source = "cache" if _get(rec, "cache", "hit") is True else "download" if rec.get("download") is not None \
            else None
        rows.append({"state": "record", "target": target, "key": key or None, "run_attempt": rec.get("run_attempt"),
                     "verified": rec.get("verified") is True, "verified_by": rec.get("verified_by"),
                     "first_use": rec.get("first_use") is True, "problem": rec.get("problem"), "source": source,
                     "sha256": _get(block, "sha256"), "plan_matches": rec.get("plan_sha256") == plan_sha256,
                     "total_s": _get(rec, "timings", "total_s")})
    return rows


def lock_status(plan: dict[str, Any], manifest_path: str,
                records: dict[tuple[str, str], tuple[dict[str, Any], bytes]] | None,
                plan_sha256: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(the report's lock block, the merged lock to write or None)."""
    not_computed = {"status": "not_computed", "new_entries": None, "problem": None}
    try:
        manifest = load_manifest(manifest_path)
        base = strict_load(lock_path(manifest_path).read_bytes())
    except (ManifestError, OSError, StrictJsonError):
        return not_computed, None
    if (records is None or manifest.sha256 != _get(plan, "manifest", "sha256")
            or manifest.lock.sha256 != _get(plan, "lock", "sha256")):
        return not_computed, None
    chosen = [rec for _, (rec, _) in sorted(records.items())
              if rec.get("verified") is True and rec.get("plan_sha256") == plan_sha256]
    try:
        merged, new = lab_provision.merge_candidates(manifest, base,
                                                     [lab_provision.candidate_from_record(r) for r in chosen])
    except lab_provision.ProvisionError as err:
        return {"status": "conflict", "new_entries": None, "problem": err.problem}, None
    return {"status": "unchanged" if new == 0 else "new_entries", "new_entries": new, "problem": None}, merged


# --------------------------------------------------------------------------------------------------- report

def build_report(plan: dict[str, Any], plan_bytes: bytes, provision_dir: Path, shards_dir: Path,
                 manifest_path: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    plan_sha256 = sha256_hex(plan_bytes)
    roots = find_roots(shards_dir)
    states: dict[str, tuple[str, _Root | None]] = {}
    shard_rows = []
    for shard in plan["shards"]:
        state, root, attempt = choose(roots, shard["shard"], plan_sha256)
        states[shard["shard"]] = (state, root)
        shard_rows.append(_shard_row(shard, state, root, attempt))
    planned = {s["shard"] for s in plan["shards"]}
    ignored = sorted({r.artifact for r in roots if r.shard not in planned and r.artifact is not None})
    units, e3, g0 = [], [], []
    samples: dict[tuple[Any, ...], list[float]] = {}
    for unit in sorted(plan["units"], key=lambda u: u["unit"]):
        state, root = states.get(unit["shard"], ("no_artifact", None))
        row, record = _unit_row(unit, state, root)
        units.append(row)
        if record is None or root is None or row["display_class"] == "no-result":
            continue
        if unit["experiment"] == "e3":
            e3 += _e3_rows(row, root.path)
        if unit["experiment"] == "g0":
            g0_row = _g0_row(row, root.path)
            if g0_row is not None:
                g0.append(g0_row)
        for ledger in ledger_rows(unit, root.path) or []:
            latency = ledger.get("latency_ms")
            if ledger.get("ok") is True and isinstance(latency, (int, float)) and not isinstance(latency, bool):
                key = (row["display_class"], row["model"], row["cpu_model"], unit["experiment"], ledger.get("task"))
                samples.setdefault(key, []).append(latency)
    try:
        records: dict[tuple[str, str], tuple[dict[str, Any], bytes]] | None = \
            lab_provision.load_records(provision_dir)
    except lab_provision.ProvisionError:
        records = None
    lock, merged = lock_status(plan, manifest_path, records, plan_sha256)
    cpu_models = sorted({row["host"]["cpu_model"] for row in shard_rows
                         if row["state"] == "sealed" and isinstance(row["host"], dict)
                         and isinstance(row["host"]["cpu_model"], str)})
    request = plan["request"]
    report = {
        "schema_version": 1, "kind": "lab_report", "result_class": plan.get("result_class"),
        "contains_measurements": any(u["display_class"] == "model" for u in units),
        "banner": PLUMBING_BANNER if plan.get("result_class") == "plumbing" else None,
        "request": {k: request.get(k) for k in ("path", "name", "sha256", "purpose")},
        "plan": {"sha256": plan_sha256, **{k: plan.get(k) for k in ("git_sha", "provider", "job_minutes",
                                                                     "max_parallel", "retention_days")}},
        "unit_count": len(units), "shard_count": len(shard_rows), "shards": shard_rows, "units": units,
        "e3": e3, "g0": g0, "latency": latency_rows(samples),
        "provision": provision_rows(records, plan_sha256), "lock": lock,
        "notes": {"cpu_models": cpu_models, "world_digest": world_digest_groups(g0)},
        "skipped": plan.get("skipped", []), "ignored_artifacts": ignored,
    }
    return report, merged


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.aggregate", description="Merge a run's shards into one report.")
    p.add_argument("--plan", required=True)
    p.add_argument("--provision", required=True, help="the downloaded provision artifacts (may be absent)")
    p.add_argument("--shards", required=True, help="the downloaded run artifacts (may be absent)")
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(os.path.abspath(args.out))
    for path in (args.plan, args.provision, args.shards, args.manifest, args.out):
        name = forbidden_root(path)
        if name is not None:
            print(f"error: no path may lie inside {name}/", file=sys.stderr)
            return EXIT_USAGE
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        print("error: --out must be absent or an empty directory", file=sys.stderr)
        return EXIT_USAGE
    try:
        plan, plan_bytes = load_plan(args.plan)
    except AggregateError as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_USAGE
    report, merged = build_report(plan, plan_bytes, Path(args.provision), Path(args.shards), args.manifest)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_bytes(plan_bytes)
    if merged is not None:
        lab_provision.write_lock(out / "lock-candidate.json", merged)
    write_json_atomic(out / "report.json", report)
    print(f"lab: aggregate units {report['unit_count']} shards {report['shard_count']} class "
          f"{report['result_class']} measurements {'true' if report['contains_measurements'] else 'false'} "
          f"lock {report['lock']['status']}", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
