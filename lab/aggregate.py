"""Merge one run's shard artifacts into one report: which artifact counts for each shard, what each unit gave, the
E3, G0, sim, sizing, E2, X1, openFDA and latency rows, the E1 comparison, the provision records and the lock
candidate.

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
lists), one sim row per complete ``scorecard.json`` (:data:`SIM_CHANNEL_FIELDS` of each channel, the lifts, the
pushdown summary, the raw text crossed and the fallback share; never an item or a key) and their ledgers' successful
calls, grouped into latency rows by display class, model, CPU model, experiment and task (CPU models are never
pooled): ``n`` and the 50th and 95th percentiles in ms (``stats.percentile``, rounded to 3 places). Every planned sim
unit with a record, whatever its status (a skipped or timed-out one included), also gets a ``sim_sizing`` row when
its collected ``scorecard.json`` or, failing that, ``progress.json`` reads: records done of all, the measured
extraction and judge medians in seconds, the estimate and the suggested minutes for the next request.
``notes.sim_world_digest`` groups the sim rows by (plant, seed, weeks): one world digest per group is
``consistent``. G0 rows also carry the protocol's record count (:data:`G0_PROTOCOL_RECORDS`, STRATEGY 11.2) and
whether the scan was below it.

E2 rows (one per ``e2.json``) copy its stamps, the protocol minimums, the central comparator, the candidates, the
conditions' AP and precision at k, the ratio, the raw text bytes, the pushdown statuses and resolvability share and the
bar verdict with its withheld reason; every E2 unit record with a projection also gets an ``e2_sizing`` row. X1 rows
copy the scorecard's stamps, channels, lifts, eligibility, plant counts and warnings; openFDA rows the replay's data
label and declaration, channels, recall counts and warnings, each fetch's per-code totals and the two sheets' sizes,
and every step's status. No row copies an item, a key, a record or a sheet row.

**E1** (``e1``; null without E1 units) compares the models' repeats with the harness's own ``compare``. The plan's
preregistration must verify (``lab.prereg.load_prereg``, else the reason is :data:`~lab.notes.PREREG_MISSING`). A
model is complete when every repeat ``1..runs`` is ``ok`` and its files verify (a unit whose files differ from its
record is excluded); an incomplete reference means no comparison (:data:`~lab.notes.E1_NO_REFERENCE`). Otherwise each
complete model's ``run.json``, ``predictions.jsonl`` and ``ledger.jsonl`` are copied byte for byte to
``DIR/e1/runs/<run id>/`` and the prereg to ``DIR/e1/prereg.json``, and ``e1_extract compare`` runs over them (run
id ``compare``, ``--runs-dir DIR/e1``, ``--allow-incomplete`` exactly when a non-reference model was left out; its log
in ``DIR/e1/compare.stdout.log`` and ``.stderr.log``); a refusal is :data:`~lab.notes.E1_COMPARE_FAILED`. The block
holds the labels, the prereg's thresholds, every repeat's status, the excluded models, the endpoints without runs,
``measurement`` and ``verdicts_shown`` (a measurement shown in the ``model`` class only), the pooled F1 blocks per
model and the paired comparison against the reference, never e1.json's clock or paths (``e1_json`` is its path
relative to ``DIR``). Its ``display_class`` is ``plumbing`` when any compared unit is, ``model`` when all are, else
``unverified``.

The lock: ``not_computed`` when the manifest or lock no longer hashes as the plan recorded or the provision records
are ambiguous; otherwise the verified records of this plan are merged into the current lock
(``provision.merge_candidates``): ``conflict`` (no file), else ``unchanged`` or ``new_entries`` and
``lock-candidate.json`` written for the team to commit.

``DIR`` must be absent or empty; it receives ``report.json`` (canonical JSON, no clock value: the same inputs give the
same bytes), ``plan.json`` (a byte copy), ``lock-candidate.json`` and the E1 comparison's ``e1/``. stdout is one
line::

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
from mycelic.collective.experiments.e2_pushdown import PROTOCOL_MIN_CANDIDATES, PROTOCOL_MIN_SEEDS
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.stats import percentile

from . import EXIT_OK, EXIT_USAGE, ROOT, forbidden_root
from . import provision as lab_provision
from . import units as lab_units
from .manifest import ManifestError, load_manifest, lock_path
from .notes import (ALTERED, AMBIGUOUS_ARTIFACTS, E1_COMPARE_FAILED, E1_NO_REFERENCE, FILES_DIFFER, NO_ARTIFACT,
                    NOT_RUN, OTHER_PLAN, PLUMBING_BANNER, PREREG_MISSING, STEP_FAILED, UNIT_RECORD_INVALID, UNSEALED)
from .plan import SHARD_ID_RE, UNIT_ID_RE
from .prereg import PreregError, load_prereg
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
SIM_CHANNEL_FIELDS = ("found", "units", "recall", "precision_at_40", "average_precision", "alerts", "false_alarms")
SIM_LIFT_FIELDS = ("estimate", "ci_low", "ci_high")
G0_PROTOCOL_RECORDS = 1000
E1_MODULE = "mycelic.collective.experiments.e1_extract"
E1_FILES = ("run.json", "predictions.jsonl", "ledger.jsonl")
E1_COMPARE_TIMEOUT_S = 1200
E1_JSON = "e1/e1/compare/e1.json"
E1_F1 = ("field_f1", "claim_f1", "entity_f1", "predicate_f1")
E2_CONDITION_FIELDS = ("ap", "ap_ci_low", "ap_ci_high", "precision_at_k")
OPENFDA_CHANNEL_FIELDS = ("in_scope", "found", "recall_rate", "median_lead_days", "post_recall_alerts", "false_alarms",
                          "false_alarms_per_week")
OPENFDA_RECALL_COUNTS = ("cache_records", "duplicates", "bad_record", "other_firm", "bad_date", "out_of_range",
                         "not_evaluable")


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
    records = result.get("records")
    return {"unit": row["unit"], "model": row["model"], "display_class": row["display_class"],
            **{k: result.get(k) for k in G0_FIELDS},
            "positive_control": ({"canary_hits": control.get("canary_hits"),
                                  "shingle_overlap_bytes": control.get("shingle_overlap_bytes")}
                                 if isinstance(control, dict) else None),
            "protocol_records": G0_PROTOCOL_RECORDS,
            "below_protocol": records < G0_PROTOCOL_RECORDS if _is_int(records) else None}


def _e2_row(row: dict[str, Any], root: Path, unit: dict[str, Any]) -> dict[str, Any] | None:
    result = _read(root / "runs" / "e2" / row["run_id"] / "e2.json")
    if not isinstance(result, dict) or result.get("kind") != "e2_pushdown":
        return None
    conditions = result.get("conditions") if isinstance(result.get("conditions"), dict) else {}
    pushdown = result.get("pushdown")
    return {"unit": row["unit"], "model": row["model"], "cpu_model": row["cpu_model"],
            "display_class": row["display_class"], "measurement": _get(result, "stamps", "measurement"),
            "below_protocol_minimum": _get(result, "stamps", "below_protocol_minimum"),
            "same_author_pack": _get(result, "stamps", "same_author_pack"),
            "protocol_min_candidates": PROTOCOL_MIN_CANDIDATES, "protocol_min_seeds": PROTOCOL_MIN_SEEDS,
            "central": _get(unit, "params", "central"),
            "candidates": {k: _get(result, "candidates", k) for k in ("total", "seeds", "by_label")},
            "conditions": {name: {k: _get(block, k) for k in E2_CONDITION_FIELDS}
                           for name, block in sorted(conditions.items())},
            "ratio": {k: _get(result, "ratio", k) for k in ("estimate", "ci_low", "ci_high")},
            "raw_text_bytes": result.get("raw_text_bytes"),
            "pushdown": {"statuses": _get(pushdown, "statuses"),
                         "resolvability_share": _get(pushdown, "resolvability", "share")},
            "verdict": result.get("verdict"), "withheld_reason": result.get("withheld_reason"),
            "top_n": _get(result, "settings", "top_n"), "seeds": _get(result, "settings", "seeds")}


def _e2_sizing_row(row: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    projection = record.get("projection")
    if not isinstance(projection, dict):
        return None
    return {"unit": row["unit"], "model": row["model"], "cpu_model": row["cpu_model"], "status": row["status"],
            "display_class": row["display_class"],
            **{k: projection.get(k) for k in ("projected_s", "budget_s", "share", "exceeds", "suggested_minutes")}}


def _x1_row(row: dict[str, Any], root: Path) -> dict[str, Any] | None:
    result = _read(root / "runs" / "x1" / row["run_id"] / "scorecard.json")
    if not isinstance(result, dict) or result.get("kind") != "x1_scorecard":
        return None
    channels = result.get("channels") if isinstance(result.get("channels"), dict) else {}
    lifts = result.get("lifts") if isinstance(result.get("lifts"), dict) else {}
    return {"unit": row["unit"], "display_class": row["display_class"],
            "stamps": {k: _get(result, "stamps", k) for k in ("measurement", "blind", "extractor",
                                                               "same_author_pack")},
            "channels": {name: {k: _get(block, k) for k in SIM_CHANNEL_FIELDS}
                         for name, block in sorted(channels.items())},
            "lifts": {name: {k: _get(block, k) for k in SIM_LIFT_FIELDS} for name, block in sorted(lifts.items())},
            "x1": {"eligible": _get(result, "x1", "eligible"), "reasons": _get(result, "x1", "reasons")},
            "plant": {k: _get(result, "plant", k) for k in ("n_patterns", "n_decoys")},
            "warnings": result.get("warnings")}


def _openfda_row(row: dict[str, Any], root: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    base = root / "runs" / "openfda" / row["run_id"]
    replay = _read(base / "runs" / "replay" / "score" / "replay.json")
    if not isinstance(replay, dict) or replay.get("kind") != "openfda_replay":
        return None
    channels = replay.get("channels") if isinstance(replay.get("channels"), dict) else {}
    fetch = {}
    for dataset, rel in (("event", "cache/events/manifest.json"), ("recall", "cache/recalls/manifest.json")):
        per_code = _get(_read(base / rel), "per_code")
        fetch[dataset] = ({code: {k: _get(info, k) for k in ("total", "fetched", "truncated", "reason")}
                           for code, info in sorted(per_code.items())} if isinstance(per_code, dict) else None)
    n1 = _read(base / "runs" / "n1" / "n1" / "sample.json")
    e1 = _read(base / "runs" / "e1" / "e1" / "prepare.json")
    steps = record.get("steps") if isinstance(record.get("steps"), list) else []
    items = _get(replay, "recalls", "items")
    return {"unit": row["unit"], "display_class": row["display_class"], "data_label": replay.get("data_label"),
            "recall_outcomes_seen_before_prereg": replay.get("recall_outcomes_seen_before_prereg"),
            "channels": {name: {**{k: _get(block, "summary", k) for k in OPENFDA_CHANNEL_FIELDS},
                                "reason": _get(block, "reason")} for name, block in sorted(channels.items())},
            "recalls": {**{k: _get(replay, "recalls", k) for k in OPENFDA_RECALL_COUNTS},
                        "items": len(items) if isinstance(items, list) else None},
            "warnings": replay.get("warnings"), "fetch": fetch,
            "sheets": {"n1": ({k: n1.get(k) for k in ("n_requested", "n_sampled")} if isinstance(n1, dict) else None),
                       "e1": ({k: e1.get(k) for k in ("n_requested", "n_written")} if isinstance(e1, dict) else None)},
            "steps": {s.get("step"): s.get("status") for s in steps if isinstance(s, dict)}}


# --------------------------------------------------------------------------------------------------- E1 comparison

def _class_of(classes: list[str], fallback: str) -> str:
    if not classes:
        return fallback
    if "plumbing" in classes:
        return "plumbing"
    return "model" if all(c == "model" for c in classes) else "unverified"


def _e1_endpoint(block: Any) -> dict[str, Any]:
    exact = _get(block, "exact_match")
    return {"runs": _get(block, "runs"),
            **{name: {k: _get(block, name, k) for k in ("value", "ci_low", "ci_high")} for name in E1_F1},
            "json_validity_rate": _get(block, "json_validity_rate"),
            "valid_after_repair_rate": _get(block, "valid_after_repair_rate"),
            "exact_match": ({t: {k: _get(entry, k) for k in ("n", "matches", "rate")}
                             for t, entry in sorted(exact.items())} if isinstance(exact, dict) else None),
            "latency_ms_p50": _get(block, "latency_ms_p50"), "latency_ms_p95": _get(block, "latency_ms_p95"),
            "model_mismatch": _get(block, "model_mismatch")}


def _e1_paired(entry: Any) -> dict[str, Any]:
    ci = _get(entry, "ci95")
    ci = ci if isinstance(ci, list) and len(ci) == 2 else [None, None]
    return {"against": _get(entry, "against"), "n": _get(entry, "n"), "mean_diff": _get(entry, "mean_diff"),
            "ci_low": ci[0], "ci_high": ci[1], "sign_p": _get(entry, "sign_p"),
            "underpowered": _get(entry, "underpowered"), "non_inferior": _get(entry, "non_inferior"),
            "kill_flag": _get(entry, "kill_flag")}


def e1_compare_argv(prereg: Path, run_dirs: list[Path], out: Path, allow_incomplete: bool) -> list[str]:
    """``e1_extract compare`` over absolute run directories (sorted by run id); ``--run-dirs`` is the harness's one
    option that takes several values, so the directories follow it as separate elements."""
    return [sys.executable, "-m", E1_MODULE, "compare", lab_units.flag("prereg", prereg), "--run-dirs",
            *(str(Path(os.path.abspath(d))) for d in sorted(run_dirs, key=lambda d: Path(d).name)),
            lab_units.flag("run-id", "compare"), lab_units.flag("runs-dir", out),
            *(["--allow-incomplete"] if allow_incomplete else [])]


E1Source = tuple[dict[str, Any], dict[str, Any] | None, Path | None]


def e1_block(plan: dict[str, Any], plan_path: Path, sources: dict[str, E1Source], out: Path) -> dict[str, Any] | None:
    """The report's E1 comparison; ``sources`` maps each E1 unit to (its row, its record, its shard root)."""
    units = sorted((u for u in plan["units"] if u["experiment"] == "e1"), key=lambda u: u["unit"])
    if not units:
        return None
    fallback = "plumbing" if plan.get("result_class") == "plumbing" else "unverified"
    block: dict[str, Any] = {
        "compared": False, "reason": None, "label": None, "display_class": None, "labels": None,
        "underpowered_below": None, "kill_below": None, "margin": None, "runs": None, "reference": None,
        "units": [{"unit": u["unit"], "model": u["model"], "repeat": u["params"]["repeat"],
                   "status": sources[u["unit"]][0]["status"], "display_class": sources[u["unit"]][0]["display_class"],
                   "included": False} for u in units],
        "excluded": [], "allow_incomplete": False, "endpoints_without_runs": [], "measurement": None,
        "verdicts_shown": False, "e1_json": None, "endpoints": {}, "paired": {}}
    shown = [r["display_class"] for r in block["units"] if r["display_class"] != "no-result"]
    block["display_class"] = _class_of(shown, fallback)
    try:
        prereg = load_prereg(plan_path)
        manifest = prereg.manifest["e1"]
        prereg_bytes = (prereg.dir / manifest["prereg"]).read_bytes()
        e1_prereg = strict_load(prereg_bytes)
    except (PreregError, OSError, StrictJsonError, KeyError, TypeError):
        block["reason"] = PREREG_MISSING
        return block
    labels = manifest["labels"]
    block.update(label="generator_text" if labels["source"] == "generator" else "fixtures",
                 labels={k: labels[k] for k in ("source", "pack", "n", "seed", "records", "claims", "sha256")},
                 **{k: e1_prereg.get(k) for k in ("underpowered_below", "kill_below", "margin", "runs", "reference")})
    reference, runs = manifest["reference"], e1_prereg.get("runs")
    complete = []
    for model in manifest["endpoints"]:
        mine = {u["params"]["repeat"]: u for u in units if u["model"] == model}
        if sorted(mine) == list(range(1, (runs or 0) + 1)) and all(
                sources[u["unit"]][0]["status"] == "ok" for u in mine.values()):
            complete.append(model)
    block["excluded"] = [m for m in manifest["endpoints"] if m not in complete]
    if reference not in complete:
        block["reason"] = E1_NO_REFERENCE
        return block
    target = out / "e1"
    run_dirs = []
    for row in block["units"]:
        if row["model"] not in complete:
            continue
        row["included"] = True
        unit = next(u for u in units if u["unit"] == row["unit"])
        _, record, root = sources[row["unit"]]
        dest = target / "runs" / unit["run_id"]
        dest.mkdir(parents=True)
        for name in E1_FILES:
            rel = f"runs/e1/{unit['run_id']}/{name}"
            if root is not None and record is not None and rel in record.get("files", {}):
                (dest / name).write_bytes((root / rel).read_bytes())
        run_dirs.append(dest)
    block["display_class"] = _class_of([r["display_class"] for r in block["units"] if r["included"]], fallback)
    (target / "prereg.json").write_bytes(prereg_bytes)
    allow_incomplete = bool(block["excluded"])
    argv = e1_compare_argv(target / "prereg.json", run_dirs, target, allow_incomplete)
    stdout, stderr = target / "compare.stdout.log", target / "compare.stderr.log"
    result = lab_units.run_process(argv, lab_units.subprocess_env(os.environ, []), cwd=ROOT,
                                   timeout_s=E1_COMPARE_TIMEOUT_S, stdout_path=stdout, stderr_path=stderr)
    lab_units.cap_log(stdout)
    lab_units.cap_log(stderr)
    doc = _read(out / E1_JSON)
    if result.exit_code != 0 or result.timed_out or not isinstance(doc, dict) or doc.get("kind") != "e1":
        block["reason"] = E1_COMPARE_FAILED
        return block
    endpoints = doc.get("endpoints") if isinstance(doc.get("endpoints"), dict) else {}
    paired = doc.get("paired") if isinstance(doc.get("paired"), dict) else {}
    block.update(compared=True, measurement=doc.get("measurement"), allow_incomplete=doc.get("allow_incomplete"),
                 endpoints_without_runs=doc.get("endpoints_without_runs"), e1_json=E1_JSON,
                 endpoints={name: _e1_endpoint(b) for name, b in sorted(endpoints.items())},
                 paired={name: _e1_paired(e) for name, e in sorted(paired.items())})
    block["verdicts_shown"] = block["measurement"] is True and block["display_class"] == "model"
    return block


def _sim_files(row: dict[str, Any], root: Path) -> tuple[Any, Any]:
    """(the collected scorecard, the collected progress.json), each None unless it reads as its kind."""
    run = root / "runs" / "sim" / row["run_id"]
    scorecard, progress = _read(run / "scorecard.json"), _read(run / "progress.json")
    return (scorecard if isinstance(scorecard, dict) and scorecard.get("kind") == "lab_sim_scorecard" else None,
            progress if isinstance(progress, dict) and progress.get("kind") == "lab_sim_progress" else None)


def _sizing_row(row: dict[str, Any], scorecard: Any, progress: Any) -> dict[str, Any] | None:
    head = {"unit": row["unit"], "model": row["model"], "cpu_model": row["cpu_model"], "status": row["status"],
            "display_class": row["display_class"]}
    if scorecard is not None:
        projection = _get(scorecard, "projection")
        measured = _get(projection, "measured")
        done = _get(scorecard, "extraction", "records")
        if done is None and _get(projection, "exceeds") is True:
            done = _get(projection, "after_records")
        judge = _get(measured, "judge_s_p50")
        return {**head, "source": "scorecard", "records_done": done,
                "records_total": _get(scorecard, "world", "records", "total"),
                "extract_record_s_p50": _get(measured, "extract_record_s_p50"),
                "judge_s_p50": judge if judge is not None else _get(projection, "judge_s_p50"),
                "estimate_s": _get(projection, "estimate_s"),
                "suggested_minutes": _get(projection, "suggested_minutes")}
    if progress is not None:
        latency = _get(progress, "latency_s")
        judge = _get(latency, "judge_record", "p50")
        return {**head, "source": "progress", "records_done": _get(progress, "records", "done"),
                "records_total": _get(progress, "records", "total"),
                "extract_record_s_p50": _get(latency, "extract_record", "p50"),
                "judge_s_p50": judge if judge is not None else _get(latency, "judge_warmup", "p50"),
                "estimate_s": _get(progress, "estimate_s"),
                "suggested_minutes": _get(progress, "suggested_minutes")}
    return None


def _sim_row(row: dict[str, Any], scorecard: Any) -> dict[str, Any] | None:
    if scorecard is None or scorecard.get("status") != "complete":
        return None
    channels = scorecard.get("channels") if isinstance(scorecard.get("channels"), dict) else {}
    lifts = scorecard.get("lifts") if isinstance(scorecard.get("lifts"), dict) else {}
    pushdown = scorecard.get("pushdown")
    return {"unit": row["unit"], "model": row["model"], "cpu_model": row["cpu_model"],
            "display_class": row["display_class"], "measurement": _get(scorecard, "stamps", "measurement"),
            "plant": _get(scorecard, "world", "plant"), "seed": _get(scorecard, "world", "seed"),
            "weeks": _get(scorecard, "world", "weeks"), "records": _get(scorecard, "world", "records", "total"),
            "world_digest": _get(scorecard, "world", "world_digest"),
            "channels": {name: {k: _get(block, k) for k in SIM_CHANNEL_FIELDS}
                         for name, block in sorted(channels.items())},
            "lifts": {name: {k: _get(block, k) for k in SIM_LIFT_FIELDS} for name, block in sorted(lifts.items())},
            "pushdown": {"n": _get(pushdown, "n"), "n_true": _get(pushdown, "n_true"),
                         "supported": _get(pushdown, "statuses", "supported"),
                         "ap_pushdown": _get(pushdown, "ap_pushdown"),
                         "ap_stats_only": _get(pushdown, "ap_stats_only")},
            "raw_text_crossed": scorecard.get("raw_text_crossed"),
            "fallback_share": _get(scorecard, "extraction", "fallback_share"),
            "notes": list(scorecard["notes"]) if isinstance(scorecard.get("notes"), list) else []}


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


def sim_world_groups(sim: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The sim rows grouped by (plant, seed, weeks): each group's digests and units, consistent with one digest."""
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in sim:
        key = (row["plant"], row["seed"], row["weeks"])
        group = groups.setdefault(key, {"plant": row["plant"], "seed": row["seed"], "weeks": row["weeks"],
                                        "digests": set(), "units": []})
        group["digests"].add(row["world_digest"])
        group["units"].append(row["unit"])
    out = []
    for key in sorted(groups, key=_sort_key):
        group = groups[key]
        digests = sorted(group["digests"], key=lambda d: "" if d is None else str(d))
        out.append({**group, "digests": digests, "units": sorted(group["units"]), "consistent": len(digests) == 1})
    return out


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
                 manifest_path: str, plan_path: Path, out: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The report; ``out`` receives the E1 comparison's files (``plan_path`` locates the preregistration)."""
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
    units, e3, g0, sim, sim_sizing = [], [], [], [], []
    e2, e2_sizing, x1, openfda = [], [], [], []
    e1_sources: dict[str, E1Source] = {}
    samples: dict[tuple[Any, ...], list[float]] = {}
    for unit in sorted(plan["units"], key=lambda u: u["unit"]):
        state, root = states.get(unit["shard"], ("no_artifact", None))
        row, record = _unit_row(unit, state, root)
        units.append(row)
        if unit["experiment"] == "e1":
            e1_sources[unit["unit"]] = (row, record, root.path if root is not None else None)
        if unit["experiment"] == "e2" and record is not None:
            sizing_row = _e2_sizing_row(row, record)
            if sizing_row is not None:
                e2_sizing.append(sizing_row)
        scorecard = None
        if unit["experiment"] == "sim" and record is not None and root is not None:
            scorecard, progress = _sim_files(row, root.path)
            sizing = _sizing_row(row, scorecard, progress)
            if sizing is not None:
                sim_sizing.append(sizing)
        if record is None or root is None or row["display_class"] == "no-result":
            continue
        if unit["experiment"] == "sim":
            sim_row = _sim_row(row, scorecard)
            if sim_row is not None:
                sim.append(sim_row)
        if unit["experiment"] == "e3":
            e3 += _e3_rows(row, root.path)
        if unit["experiment"] == "g0":
            g0_row = _g0_row(row, root.path)
            if g0_row is not None:
                g0.append(g0_row)
        new_row = {"e2": lambda: _e2_row(row, root.path, unit), "x1": lambda: _x1_row(row, root.path),
                   "openfda": lambda: _openfda_row(row, root.path, record)}.get(unit["experiment"], lambda: None)()
        if new_row is not None:
            {"e2": e2, "x1": x1, "openfda": openfda}[unit["experiment"]].append(new_row)
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
        "e3": e3, "g0": g0, "sim": sim, "sim_sizing": sim_sizing, "e1": e1_block(plan, plan_path, e1_sources, out),
        "e2": e2, "e2_sizing": e2_sizing, "x1": x1, "openfda": openfda, "latency": latency_rows(samples),
        "provision": provision_rows(records, plan_sha256), "lock": lock,
        "notes": {"cpu_models": cpu_models, "world_digest": world_digest_groups(g0),
                  "sim_world_digest": sim_world_groups(sim)},
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
    out.mkdir(parents=True, exist_ok=True)
    report, merged = build_report(plan, plan_bytes, Path(args.provision), Path(args.shards), args.manifest,
                                  Path(args.plan), out)
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
