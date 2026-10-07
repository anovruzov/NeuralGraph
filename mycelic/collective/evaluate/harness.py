"""X1/X2 evaluation harness: pre-register the settings, check a plant spec, run the seeds, write the scorecard.

    python -m mycelic.collective.evaluate.harness prereg --pack P --seeds 1,2,3 --eval-from F --eval-to T
        --tie-salt S --detector-author NAME [--sites 6] [--weeks 52] [--grace-weeks 4] [--bootstrap-b 10000]
        [--bootstrap-seed 1] --run-id ID [--allow-dirty]
    python -m mycelic.collective.evaluate.harness check-plant --prereg FILE --plant FILE
    python -m mycelic.collective.evaluate.harness run --prereg FILE --plant FILE --seeds 1,2,3 --run-id ID
        [--ablation-k1] [--allow-dirty]

Every subcommand takes ``--runs-dir`` (default ``runs``) and ``--dry-run`` (the common contract). Exit codes: 0 ok;
2 usage, pinned-value or plant error; 130 interrupted (the partial run directory is left, and a later run with the
same id is refused).

**What is pinned.** ``prereg`` writes ``runs/x1/<id>/prereg.json`` with the pack's four hashes, the hash of every
file the evaluation runs (:data:`EVAL_CODE_FILES`), the seeds, world size, evaluation weeks, grace, tie salt,
detector author and bootstrap settings, before any plant spec or outcome is seen. ``run`` refuses (exit 2, writing
no scorecard) a changed pack hash or code hash (every differing name is listed), other seeds, an existing run
directory, a plant spec bound to another prereg (its ``prereg_sha256``, when not null, must be the prereg file's
sha256, which ``check-plant`` prints for the planter), and dirty or unknown code state under
:data:`EVAL_DIRTY_PATHS` without ``--allow-dirty`` (stamped).

**What a run does.** Per seed: generate the world, plant the spec, run the real pipeline (``baselines``), and read
every channel: X and S from HQ's store, R (model-free), U, rules and single_site, and with ``--ablation-k1`` the
unsuppressed X and S. Alerts before the evaluation weeks are burn-in and dropped. A pattern is found by a channel
when an alert names its key inside ``[start, min(end + grace, eval_to)]`` (single_site: at any site); repeats count
once; every other alert is a false alarm. Recall is pooled over patterns x seeds; precision@40 and AP are
tie-averaged per seed over distinct alerted keys (score: the key's best alert) and averaged over seeds; lifts are
cluster bootstraps over patterns (each pattern's per-seed found differences). Diagnostics: the quiet precondition of
the structural decoys, the X flags at a decoy's detection, suppression shares and the analytic minimum detectable
rate of G4's per-site test.

**What the scorecard says about itself.** ``stamps`` are ``synthetic: true``, ``internal_only: true`` and
``measurement: false``; ``blind`` is self-declared (``blind_basis``). It is validated against
:data:`SCORECARD_SCHEMA` before it is written. ``content_hash`` is the sha256 of its canonical JSON without
``content_hash``, ``created_at``, ``run_id``, ``paths`` and ``timings``, so it is the same across processes, hash
seeds, run ids and work directories. G5 makes no model call: X uses the lexical extractor.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import schemacheck, stats
from ..detect.detectors import TIE_SALT_RE, DetectorConfig
from ..detect.store import CellRow
from ..experiments.common import (ROOT, DryRun, UsageError, check_run_id, code_commit, code_dirty, code_files,
                                  code_hash, fail, run_dir, split_list, utc_clock, write_json_atomic)
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from ..packs.generator import GeneratorError, generate
from ..packs.loader import FrozenPack, PackError, is_builtin_ref, load_pack
from .baselines import (ABLATION_CHANNELS, ABLATION_LABEL, CHANNEL_LABELS, CHANNELS, EvaluationError, Pipeline,
                        detector_alerts, exact_result, hq_results, k1_cells, r_mf_cells, rule_alerts, run_pipeline,
                        single_site_alerts, u_cells, world_weeks)
from .plant import (DECOY_CLASSES, HARD_CASE_CLASSES, VISIBILITIES, Decoy, PlantError, PlantSpec, check_plant,
                    is_blind, labels_doc, load_plant, plant)

CLI = "evaluate.harness"
KIND = "x1"
SCHEMA_VERSION = 1
EVAL_CODE_FILES = ("mycelic/collective/detect/*.py", "mycelic/collective/edge/*.py", "mycelic/collective/packs/*.py",
                   "mycelic/collective/evaluate/*.py", "mycelic/collective/stats.py", "mycelic/collective/jsonio.py",
                   "mycelic/collective/schemacheck.py", "mycelic/collective/experiments/common.py")
EVAL_DIRTY_PATHS = ("mycelic/collective/detect", "mycelic/collective/edge", "mycelic/collective/packs",
                    "mycelic/collective/evaluate", "mycelic/collective/stats.py", "mycelic/collective/jsonio.py",
                    "mycelic/collective/schemacheck.py", "mycelic/collective/experiments/common.py")
TOP_K = 40
MAX_SEEDS = 100
MAX_WEEKS = 520
MAX_GRACE = 52
MAX_AUTHOR = 80
MIN_BOOTSTRAP_B = 1000
X1_MIN_ITEMS = 20
FEW_PATTERNS = 10
MAX_RATE_SEARCH = 1000
LIFTS = (("X_minus_single_site", "X", "single_site"), ("X_minus_S", "X", "S"), ("X_minus_R_mf", "X", "R_mf"))
CONTENT_HASH_EXCLUDES = ("$.content_hash", "$.created_at", "$.run_id", "$.paths", "$.timings")
BLIND_BASIS = ("self-declared: planted_by differs from the detector author and planter_saw_detector_code is false")
BY_CONSTRUCTION_LABEL = "by construction, not a result"
BY_CONSTRUCTION_STATEMENT = ("planted narrative_only records carry no codes and no structured entities, so they add "
                             "nothing to the cells this channel reads")
KNOWN_HARD_NOTE = "copies without origin markers count as independent at each site (G3 limitation)"
FEW_PATTERNS_WARNING = "fewer than 10 patterns: the lift intervals are unstable"
MIN_RATE_LABEL = "analytic: constant weekly counts, G4's per-site D2 test only; not a measurement"
NOTES = [
    "Synthetic and same-author: the world, the pack and the detector were written by one author. Internal only; "
    "never shown to buyers (STRATEGY section 9.1). Nothing in this file is a measurement.",
    "blind is self-declared by the planter (blind_basis); the harness cannot check who planted.",
    "R_mf is not STRATEGY's R: it runs the same model-free detectors over record-level, unsuppressed counts of the "
    "fields allowed to leave, with no model.",
    "The exact channels (U, R_mf and the k=1 ablation) can never raise few_reporters, which G4 defines on a "
    "suppressed reporter count; R_mf also counts every record as its own root and reporter.",
]
PREREG_NOTES = ["Settings, code and pack are frozen and hashed here before any plant spec or outcome is seen."]


def eval_code_files() -> list[str]:
    return code_files(EVAL_CODE_FILES)


def eval_code_hash() -> str:
    return code_hash([ROOT / p for p in eval_code_files()])


def _dirty_state() -> bool | str:
    return code_dirty(list(EVAL_DIRTY_PATHS))


def check_clean(allow_dirty: bool, dry: DryRun | None, state: bool | str) -> None:
    """Dirty or unknown code needs --allow-dirty; a dry run lists it as a need instead of failing."""
    if state is False or allow_dirty:
        return
    if dry is not None:
        dry.need("committed evaluation code (or --allow-dirty, which is stamped)")
        return
    raise UsageError("the evaluation code has uncommitted changes (or git is unavailable); commit first or pass "
                     "--allow-dirty, which is stamped") from None


# --------------------------------------------------------------------------------------------------- schemas

def obj_schema(props: dict[str, Any], nullable: bool = False) -> dict[str, Any]:
    return {"type": ["object", "null"] if nullable else "object", "additionalProperties": False,
            "required": sorted(props), "properties": props}


def arr_schema(items: dict[str, Any], lo: int | None = None, hi: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "array", "items": items}
    if lo is not None:
        out["minItems"] = lo
    if hi is not None:
        out["maxItems"] = hi
    return out


def typed_schema(name: str, nullable: bool = False, **extra: Any) -> dict[str, Any]:
    return {"type": [name, "null"] if nullable else name, **extra}


_O, _A = obj_schema, arr_schema
_HEX = typed_schema("string", pattern="[0-9a-f]{64}")
_STR = typed_schema("string")
_INT = typed_schema("integer")
_NAT = typed_schema("integer", minimum=0)
_POS = typed_schema("integer", minimum=1)
_NUM = typed_schema("number")
_BOOL = typed_schema("boolean")
_WEEK = typed_schema("string", pattern="[0-9]{4}-W[0-9]{2}")
_NSTR = typed_schema("string", nullable=True)
_NINT = typed_schema("integer", nullable=True)
_NNUM = typed_schema("number", nullable=True)
# schemacheck has no union of boolean and string: code_dirty is declared boolean-or-null, and "unknown" is checked
# by union_problems before the schema runs
_DIRTY = typed_schema("boolean", nullable=True)


def _const(value: Any) -> dict[str, Any]:
    kind = {bool: "boolean", int: "integer", str: "string"}[type(value)]
    return typed_schema(kind, const=value)


def _enum(values: Sequence[str]) -> dict[str, Any]:
    return typed_schema("string", enum=list(values))


PREREG_SCHEMA = schemacheck.compile(_O({
    "kind": _const("x1_prereg"), "schema_version": _const(SCHEMA_VERSION), "run_id": _STR, "created_at": _STR,
    "pack": _O({"ref": _STR, "id": _STR, "version": _STR, "illustrative": _BOOL, "same_author_as_code": _BOOL,
                "config_hash": _HEX, "vocabulary_hash": _HEX, "detector_hash": _HEX, "fixtures_hash": _HEX, "k": _POS,
                "alert_budget_per_week": _NAT, "window_weeks": _POS}),
    "code_hash": _HEX, "code_files": _A(_STR), "code_commit": _STR, "code_dirty": _DIRTY, "allow_dirty": _BOOL,
    "world": _O({"seeds": _A(_NAT, 1, MAX_SEEDS), "sites": _POS, "site_ids": _A(_STR),
                 "weeks": typed_schema("integer", minimum=1, maximum=MAX_WEEKS), "start": _STR}),
    "evaluation": _O({"eval_from": _NAT, "eval_to": _NAT, "eval_from_week": _WEEK, "eval_to_week": _WEEK,
                      "grace_weeks": typed_schema("integer", minimum=0, maximum=MAX_GRACE), "top_k": _const(TOP_K)}),
    "tie_salt": typed_schema("string", pattern=TIE_SALT_RE.pattern),
    "detector_author": typed_schema("string", minLength=1, maxLength=MAX_AUTHOR),
    "bootstrap": _O({"B": typed_schema("integer", minimum=MIN_BOOTSTRAP_B), "seed": _INT}), "notes": _A(_STR)}))

_CHANNEL_ENUM = _enum([*CHANNELS, *ABLATION_CHANNELS])
_EVENT = _O({"week": _WEEK, "rank": _NINT, "key": _STR, "score": _NNUM, "site": _NSTR})
_VIS_BLOCK = _O({"units": _NAT, "found": _NAT, "recall": _NNUM})
_CHANNEL_BLOCK = _O({
    "label": _STR, "ranked": _BOOL, "units": _NAT, "found": _NAT, "recall": _NNUM,
    "by_visibility": _O({v: _VIS_BLOCK for v in VISIBILITIES}), "median_delay_weeks": _NNUM,
    "median_lead_weeks": _NNUM, "precision_at_40": _NNUM, "average_precision": _NNUM, "false_alarms": _NAT,
    "false_alarms_per_week": _NUM, "alerts": _NAT, "decoys_alerted": _O({c: _NAT for c in DECOY_CLASSES}),
    "per_seed": _A(_O({"seed": _NAT, "found": _NAT, "recall": _NNUM, "alerts": _NAT, "false_alarms": _NAT,
                       "false_alarms_per_week": _NUM, "precision_at_40": _NNUM, "average_precision": _NNUM}))})
_LIFT = _O({"estimate": _NUM, "ci_low": _NUM, "ci_high": _NUM, "B": _POS, "seed": _STR, "method": _STR,
            "n_patterns": _NAT, "n_seeds": _NAT, "n_units": _NAT})
_FLAGS = _O({"echo": _BOOL, "few_reporters_sites": _A(_STR), "high_base_rate": _BOOL}, nullable=True)
_SUPPRESSION = {"cells": _NAT, "cells_n_suppressed": _NAT, "share_n_suppressed": _NNUM}
_HASH_NAMES = ("config_hash", "vocabulary_hash", "detector_hash", "fixtures_hash", "code_hash", "org_hash",
               "prereg_sha256", "plant_sha256", "labels_sha256")
SCORECARD_SCHEMA = schemacheck.compile(_O({
    "kind": _const("x1_scorecard"), "schema_version": _const(SCHEMA_VERSION), "run_id": _STR, "created_at": _STR,
    "stamps": _O({"synthetic": _const(True), "internal_only": _const(True), "measurement": _const(False),
                  "same_author_pack": _BOOL, "blind": _BOOL, "blind_basis": _const(BLIND_BASIS),
                  "plant_bound_to_prereg": _BOOL, "ablation_k1": _BOOL, "allow_dirty": _BOOL,
                  "extractor": _const("lexical")}),
    "hashes": _O({name: _HEX for name in _HASH_NAMES}),
    "code": _O({"code_commit": _STR, "code_dirty": _DIRTY, "code_files": _A(_STR)}),
    "pack": _O({"id": _STR, "version": _STR, "illustrative": _BOOL, "k": _POS, "alert_budget_per_week": _NAT,
                "window_weeks": _POS}),
    "world": _O({"seeds": _A(_NAT, 1), "sites": _POS, "site_ids": _A(_STR), "weeks": _POS, "start": _STR,
                 "eval_from_week": _WEEK, "eval_to_week": _WEEK, "evaluation_weeks": _POS, "grace_weeks": _NAT}),
    "plant": _O({"planted_by": _STR, "planter_saw_detector_code": _BOOL, "detector_author": _STR,
                 "n_patterns": _NAT, "n_decoys": _NAT, "patterns_by_visibility": _O({v: _NAT for v in VISIBILITIES}),
                 "decoys_by_class": _O({c: _NAT for c in DECOY_CLASSES}),
                 "planted_records": _A(_O({"seed": _NAT, "records": _NAT}))}),
    "channel_labels": _O({c: _const(CHANNEL_LABELS[c]) for c in CHANNELS}),
    "channels": _O({c: _CHANNEL_BLOCK for c in CHANNELS}),
    "ablation": _O({"label": _const(ABLATION_LABEL), "channels": _O({c: _CHANNEL_BLOCK for c in ABLATION_CHANNELS})},
                   nullable=True),
    "lifts": _O({name: _LIFT for name, _, _ in LIFTS}),
    "by_construction": _A(_O({"channel": _enum(["S", "R_mf"]), "statement": _const(BY_CONSTRUCTION_STATEMENT),
                              "label": _const(BY_CONSTRUCTION_LABEL), "visibility": _const("narrative_only"),
                              "recall": _NNUM})),
    "known_hard_cases": _A(_O({"decoy_id": _STR, "class": _STR, "seed": _NAT, "note": _const(KNOWN_HARD_NOTE),
                               "alerted": _O({c: _BOOL for c in CHANNELS})})),
    "patterns": _A(_O({"id": _STR, "key": _STR, "visibility": _enum(VISIBILITIES), "sites": _A(_STR),
                       "start_week": _WEEK, "end_week": _WEEK,
                       "outcomes": _A(_O({"seed": _NAT, "channel": _CHANNEL_ENUM, "found": _BOOL,
                                          "first_alert_week": _NSTR, "delay_weeks": _NINT, "lead_weeks": _NINT}))})),
    "decoys": _A(_O({"id": _STR, "class": _enum(DECOY_CLASSES), "keys": _A(_STR), "sites": _A(_STR),
                     "watch_from": _WEEK, "watch_to": _WEEK,
                     "outcomes": _A(_O({"seed": _NAT, "channel": _CHANNEL_ENUM, "alerted": _BOOL,
                                        "first_alert_week": _NSTR})),
                     "quiet_elsewhere": _A(_O({"seed": _NAT, "quiet": typed_schema("boolean", nullable=True)})),
                     "flags_at_detection": _A(_O({"seed": _NAT, "flags": _A(_O({
                         "key": _STR, "detection_week": _NSTR, "flags": _FLAGS}))}))})),
    "suppression": _A(_O({"seed": _NAT, **_SUPPRESSION,
                          "by_channel": _O({"codes": _O(_SUPPRESSION), "text_only": _O(_SUPPRESSION)})})),
    "min_detectable_rate": _O({"label": _const(MIN_RATE_LABEL), "k": _POS,
                               "rows": _A(_O({"background": _NAT, "rate_at_k": _NINT, "rate_unsuppressed": _NINT}))}),
    "alerts": _A(_O({"seed": _NAT, "channel": _CHANNEL_ENUM, "items": _A(_EVENT)})),
    "warnings": _A(_STR),
    "x1": _O({"eligible": _BOOL, "reasons": _A(_STR),
              "verdict": _O({"lift_ci_low_above_0": _BOOL, "precision_at_40_at_least_0_25": _BOOL, "pass": _BOOL},
                            nullable=True)}),
    "notes": _A(_STR),
    "paths": _O({name: _STR for name in ("pack_ref", "prereg", "plant", "run_dir", "workdir")}),
    "timings": _O({"total_s": _NUM, "per_seed_s": _A(_NUM)}),
    "content_hash_excludes": _A(_STR), "content_hash": _HEX}))


def union_problems(schema: schemacheck.Schema, doc: Any, path: Sequence[str]) -> list[tuple[str, str]]:
    """``schema``'s problems for ``doc``, where the field at ``path`` may be true, false or "unknown"."""
    node, parent = doc, None
    for name in path:
        parent = node
        node = node.get(name) if isinstance(node, dict) else None
    extra: list[tuple[str, str]] = []
    if isinstance(parent, dict) and path[-1] in parent:
        if parent[path[-1]] == "unknown":
            doc = strict_load(canonical_bytes(doc))
            target = doc
            for name in path[:-1]:
                target = target[name]
            target[path[-1]] = None
        elif parent[path[-1]] is None:
            extra.append(("$." + ".".join(path), "type"))
    return extra + schema.validate(doc)


def scorecard_problems(doc: Any) -> list[tuple[str, str]]:
    return union_problems(SCORECARD_SCHEMA, doc, ("code", "code_dirty"))


def content_hash(doc: Mapping[str, Any]) -> str:
    excluded = {p[2:] for p in CONTENT_HASH_EXCLUDES}
    return sha256_hex(canonical_bytes({k: doc[k] for k in sorted(doc) if k not in excluded}))


# --------------------------------------------------------------------------------------------------- settings

def parse_seeds(text: str) -> list[int]:
    seeds = []
    for value in split_list(text or ""):
        if not value.isdigit() or not value.isascii():
            raise UsageError("--seeds must be comma-separated ints >= 0") from None
        seeds.append(int(value))
    if not 1 <= len(seeds) <= MAX_SEEDS:
        raise UsageError(f"--seeds must list 1 to {MAX_SEEDS} seeds") from None
    if len(set(seeds)) != len(seeds):
        raise UsageError("--seeds lists a seed twice") from None
    return sorted(seeds)


def check_settings(pack: FrozenPack, *, sites: int, weeks: int, eval_from: int, eval_to: int,
                   grace_weeks: int) -> None:
    d = pack.detectors
    if not 1 <= sites <= len(pack.generator["sites"]):
        raise UsageError(f"--sites must be in [1, {len(pack.generator['sites'])}]") from None
    need = d["baseline_weeks"] + d["window_weeks"]
    if not need <= weeks <= MAX_WEEKS:
        raise UsageError(f"--weeks must be in [{need} (baseline_weeks + window_weeks), {MAX_WEEKS}]") from None
    low = d["window_weeks"] + d["min_history_weeks"] - 1
    if eval_from < low:
        raise UsageError(f"--eval-from must be >= {low} (window_weeks + min_history_weeks - 1)") from None
    if not eval_from <= eval_to <= weeks - 1:
        raise UsageError("--eval-to must be in [--eval-from, --weeks - 1]") from None
    if not 0 <= grace_weeks <= MAX_GRACE:
        raise UsageError(f"--grace-weeks must be in [0, {MAX_GRACE}]") from None


def pack_ref(ref: str) -> str:
    return ref if is_builtin_ref(ref) else str(Path(ref).resolve())


def _load_pack(ref: str) -> FrozenPack:
    try:
        return load_pack(ref)
    except PackError as err:
        raise UsageError(f"pack: {err}") from None


def read_prereg(path: str | Path) -> tuple[dict[str, Any], bytes]:
    """The prereg document and its exact bytes; any structural problem is a UsageError naming only its path."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read prereg {path} ({exc.__class__.__name__})") from None
    doc = None
    try:
        doc = strict_load(data)
    except StrictJsonError as err:
        raise UsageError(f"prereg {path} is not strict JSON ({err.reason})") from None
    problems = union_problems(PREREG_SCHEMA, doc, ("code_dirty",))
    if problems:
        raise UsageError(f"prereg {path} is not an x1 prereg.json ({problems[0][0]} {problems[0][1]})") from None
    return doc, data


def pinned_differences(prereg: Mapping[str, Any], pack: FrozenPack, code: str | None) -> list[str]:
    """The names of every pinned value that differs: the pack's four hashes and, unless ``code`` is None, the code
    hash."""
    names = [name for name in ("config_hash", "vocabulary_hash", "detector_hash", "fixtures_hash")
             if prereg["pack"][name] != getattr(pack, name)]
    if code is not None and prereg["code_hash"] != code:
        names.append("code_hash")
    return names


# --------------------------------------------------------------------------------------------------- metrics

def eval_events(events: Sequence[Mapping[str, Any]], first: str, last: str) -> list[dict[str, Any]]:
    """Events in the evaluation weeks; earlier weeks are burn-in."""
    return [dict(e) for e in events if first <= e["week"] <= last]


def _in_window(event: Mapping[str, Any], label: Mapping[str, Any]) -> bool:
    return event["key"] == label["key"] and label["found_from"] <= event["week"] <= label["found_to"]


def pattern_outcome(events: Sequence[Mapping[str, Any]], label: Mapping[str, Any],
                    index: Mapping[str, int]) -> dict[str, Any]:
    weeks = sorted(e["week"] for e in events if _in_window(e, label))
    if not weeks:
        return {"found": False, "first_alert_week": None, "delay_weeks": None, "lead_weeks": None}
    first = index[weeks[0]]
    return {"found": True, "first_alert_week": weeks[0], "delay_weeks": first - label["start_index"],
            "lead_weeks": label["end_index"] - first}


def false_alarms(events: Sequence[Mapping[str, Any]], patterns: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for e in events if not any(_in_window(e, p) for p in patterns))


def ranking(events: Sequence[Mapping[str, Any]], patterns: Sequence[Mapping[str, Any]],
            top_k: int = TOP_K) -> tuple[float, float | None]:
    """(precision@k, AP), tie-averaged over the distinct keys with an event; a key scores its best event and is
    relevant when one of its events lies in its pattern's found window."""
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for e in events:
        by_key.setdefault(e["key"], []).append(e)
    keys = sorted(by_key)
    scores = [max(e["score"] for e in by_key[k]) for k in keys]
    relevant = [any(_in_window(e, p) for e in by_key[k] for p in patterns) for k in keys]
    return (stats.tie_averaged_precision_at_k(scores, relevant, top_k),
            stats.tie_averaged_ap(scores, relevant, len(patterns)))


def decoy_outcome(events: Sequence[Mapping[str, Any]], label: Mapping[str, Any]) -> dict[str, Any]:
    weeks = sorted(e["week"] for e in events
                   if e["key"] in label["keys"] and label["watch_from"] <= e["week"] <= label["watch_to"])
    return {"alerted": bool(weeks), "first_alert_week": weeks[0] if weeks else None}


def _recall(found: int, units: int) -> float | None:
    return found / units if units else None


def _mean(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return math.fsum(known) / len(known) if known else None


def channel_block(channel: str, label: str, per_seed_events: Mapping[int, Sequence[Mapping[str, Any]]],
                  labels: Mapping[str, Any], index: Mapping[str, int], evaluation_weeks: int) -> dict[str, Any]:
    patterns, decoys = labels["patterns"], labels["decoys"]
    seeds = sorted(per_seed_events)
    ranked = channel != "rules"
    outcomes = {(p["id"], s): pattern_outcome(per_seed_events[s], p, index) for p in patterns for s in seeds}
    found_units = [o for o in outcomes.values() if o["found"]]
    by_visibility = {}
    for v in VISIBILITIES:
        units = [(p["id"], s) for p in patterns if p["visibility"] == v for s in seeds]
        found = sum(1 for u in units if outcomes[u]["found"])
        by_visibility[v] = {"units": len(units), "found": found, "recall": _recall(found, len(units))}
    per_seed = []
    total_alerts = total_false = 0
    for s in seeds:
        events = per_seed_events[s]
        found = sum(1 for p in patterns if outcomes[(p["id"], s)]["found"])
        fa = false_alarms(events, patterns)
        p_at_k, ap = ranking(events, patterns) if ranked else (None, None)
        per_seed.append({"seed": s, "found": found, "recall": _recall(found, len(patterns)), "alerts": len(events),
                         "false_alarms": fa, "false_alarms_per_week": fa / evaluation_weeks,
                         "precision_at_40": p_at_k, "average_precision": ap})
        total_alerts += len(events)
        total_false += fa
    alerted = dict.fromkeys(DECOY_CLASSES, 0)
    for d in decoys:
        for s in seeds:
            alerted[d["class"]] += int(decoy_outcome(per_seed_events[s], d)["alerted"])
    units = len(patterns) * len(seeds)
    return {"label": label, "ranked": ranked, "units": units, "found": len(found_units),
            "recall": _recall(len(found_units), units), "by_visibility": by_visibility,
            "median_delay_weeks": stats.percentile([o["delay_weeks"] for o in found_units], 50),
            "median_lead_weeks": stats.percentile([o["lead_weeks"] for o in found_units], 50),
            "precision_at_40": _mean([r["precision_at_40"] for r in per_seed]) if ranked else None,
            "average_precision": _mean([r["average_precision"] for r in per_seed]) if ranked else None,
            "false_alarms": total_false, "false_alarms_per_week": total_false / (evaluation_weeks * len(seeds)),
            "alerts": total_alerts, "decoys_alerted": alerted, "per_seed": per_seed}


def lift(name: str, found_a: Mapping[str, Sequence[bool]], found_b: Mapping[str, Sequence[bool]], *, B: int,
         seed: int) -> dict[str, Any]:
    """Cluster bootstrap over patterns of the per-seed differences found_a - found_b (each -1, 0 or 1)."""
    clusters = [[int(a) - int(b) for a, b in zip(found_a[p], found_b[p])] for p in sorted(found_a)]
    boot = stats.cluster_bootstrap_mean(clusters, B=B, seed=f"x1:{seed}:{name}")
    return {"estimate": boot["mean"], "ci_low": boot["ci_low"], "ci_high": boot["ci_high"], "B": boot["B"],
            "seed": boot["seed"], "method": boot["method"], "n_patterns": len(clusters),
            "n_seeds": len(clusters[0]) if clusters else 0, "n_units": boot["n_units"]}


def _lb(cell: CellRow) -> int:
    return cell.n if cell.n is not None else 1


def quiet_elsewhere(decoy: Decoy, label: Mapping[str, Any], cells: Sequence[CellRow]) -> bool | None:
    """The structural classes' precondition on HQ's X cells: ``other_sites_at_most_one`` (per key, every site that
    planted no counted record of it sums at most 1, a ``'<k'`` cell counting 1) or ``all_sites_zero``."""
    rule = label["quiet_rule"]
    if rule is None:
        return None
    for key, _, counted in decoy.counted():
        per_site: dict[str, int] = {}
        for c in cells:
            if (f"{c.entity_type}:{c.entity_id}:{c.predicate}" == key
                    and label["quiet_from"] <= c.iso_week <= label["quiet_to"]):
                per_site[c.site] = per_site.get(c.site, 0) + _lb(c)
        for site in sorted(per_site):
            if per_site[site] > (0 if rule == "all_sites_zero" else 1) \
                    and (rule == "all_sites_zero" or site not in counted):
                return False
    return True


def flags_at_detection(result_x: Mapping[str, Any], label: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_key = {c["key"]: c for c in result_x["candidates"]}
    out = []
    for key in label["keys"]:
        c = by_key.get(key)
        week = c["detection_week"] if c is not None else None
        flags = None
        if week is not None and label["watch_from"] <= week <= label["watch_to"] and c["snapshot"] is not None:
            f = c["snapshot"]["flags"]
            flags = {"echo": f["echo"], "few_reporters_sites": list(f["few_reporters_sites"]),
                     "high_base_rate": f["high_base_rate"]}
        out.append({"key": key, "detection_week": week, "flags": flags})
    return out


def suppression(cells: Sequence[CellRow]) -> dict[str, Any]:
    def block(rows: Sequence[CellRow]) -> dict[str, Any]:
        n = sum(1 for c in rows if c.n is None)
        return {"cells": len(rows), "cells_n_suppressed": n, "share_n_suppressed": n / len(rows) if rows else None}
    return {**block(cells), "by_channel": {ch: block([c for c in cells if c.channel == ch])
                                          for ch in ("codes", "text_only")}}


def min_detectable_rate(pack: FrozenPack) -> dict[str, Any]:
    """For a constant weekly background count b at a site with full history, the smallest weekly planted rate r for
    which G4's per-site D2 test certainly exceeds: ``c = window * lb(b + r)`` against ``max(lambda_floor, ub(b))``,
    with the cells' bounds at the pack's k and without suppression."""
    cfg = DetectorConfig.from_pack(pack)
    log_alpha = math.log(cfg.alpha_site)

    def rate(b: int, k: int) -> int | None:
        def bounds(v: int) -> tuple[int, int]:
            return (0, 0) if v == 0 else (1, k - 1) if v < k else (v, v)
        lam = max(cfg.lambda_floor, bounds(b)[1])
        for r in range(1, MAX_RATE_SEARCH + 1):
            c = cfg.window_weeks * bounds(b + r)[0]
            if c >= 1 and stats.poisson_logsf(c, lam * cfg.window_weeks) < log_alpha:
                return r
        return None

    k = pack.egress.k
    return {"label": MIN_RATE_LABEL, "k": k,
            "rows": [{"background": b, "rate_at_k": rate(b, k), "rate_unsuppressed": rate(b, 1)}
                     for b in range(2 * k + 1)]}


def x1_block(*, blind: bool, bound: bool, n_patterns: int, n_decoys: int, lift_x: Mapping[str, Any],
             precision_x: float | None) -> dict[str, Any]:
    reasons = [text for failing, text in (
        (not blind, "not blind (self-declared: planter_saw_detector_code or planted_by equals the detector author)"),
        (not bound, "the plant spec is not bound to the prereg (prereg_sha256 is null)"),
        (n_patterns < X1_MIN_ITEMS, f"fewer than {X1_MIN_ITEMS} patterns"),
        (n_decoys < X1_MIN_ITEMS, f"fewer than {X1_MIN_ITEMS} decoys")) if failing]
    verdict = None
    if not reasons:
        above = lift_x["ci_low"] > 0
        precise = precision_x is not None and precision_x >= 0.25
        verdict = {"lift_ci_low_above_0": above, "precision_at_40_at_least_0_25": precise, "pass": above and precise}
    return {"eligible": not reasons, "reasons": reasons, "verdict": verdict}


# --------------------------------------------------------------------------------------------------- one seed

def _seed_run(pack: FrozenPack, spec: PlantSpec, prereg: Mapping[str, Any], labels: Mapping[str, Any], seed: int,
              workdir: Path, *, ablation_k1: bool) -> dict[str, Any]:
    world_spec, ev = prereg["world"], prereg["evaluation"]
    weeks = world_weeks(world_spec["start"], world_spec["weeks"])
    world = generate(pack, seed, world_spec["sites"], world_spec["weeks"])
    planted = plant(world, spec, pack)
    records = list(world.records) + list(planted.records)
    salt = prereg["tie_salt"]
    pipeline: Pipeline = run_pipeline(pack, records, site_ids=world_spec["site_ids"], master_data=world.master_data,
                                      weeks=weeks, workdir=workdir)
    try:
        hq = hq_results(pipeline.store, as_of=pipeline.as_of, tie_salt=salt)
        u = exact_result(pack, pipeline.org, u_cells(pipeline), as_of=pipeline.as_of, run_channel="X", tie_salt=salt)
        r = exact_result(pack, pipeline.org, r_mf_cells(pack, records, master_data=world.master_data,
                                                        last_week=weeks[-1]),
                         as_of=pipeline.as_of, run_channel="S", tie_salt=salt)
        events = {"X": detector_alerts(hq["X"]), "S": detector_alerts(hq["S"]), "R_mf": detector_alerts(r),
                  "U": detector_alerts(u), "rules": rule_alerts(hq["X"]),
                  "single_site": single_site_alerts(pipeline, tie_salt=salt)}
        if ablation_k1:
            k1 = k1_cells(pipeline, ablation_k1=True)
            for name, channel in zip(ABLATION_CHANNELS, ("X", "S")):
                events[name] = detector_alerts(exact_result(pack, pipeline.org, k1, as_of=pipeline.as_of,
                                                            run_channel=channel, tie_salt=salt))
        _, cells = pipeline.store.detection_inputs(pipeline.as_of, "X")
        decoys = [{"quiet": quiet_elsewhere(d, dl, cells), "flags": flags_at_detection(hq["X"], dl)}
                  for d, dl in zip(spec.decoys, labels["decoys"])]
        org_hash = pipeline.org.org_hash
        cells_stats = suppression(cells)
    finally:
        pipeline.close()
    return {"events": {name: eval_events(e, ev["eval_from_week"], ev["eval_to_week"]) for name, e in events.items()},
            "decoys": decoys, "suppression": cells_stats, "org_hash": org_hash,
            "planted_records": len(planted.records)}


# --------------------------------------------------------------------------------------------------- scorecard

def build_scorecard(*, run_id: str, pack: FrozenPack, prereg: Mapping[str, Any], prereg_sha: str, spec: PlantSpec,
                    labels: Mapping[str, Any], labels_sha: str, runs: Mapping[int, Mapping[str, Any]],
                    ablation_k1: bool, allow_dirty: bool, dirty: bool | str, paths: Mapping[str, str],
                    timings: Mapping[str, Any]) -> dict[str, Any]:
    world_spec, ev, boot = prereg["world"], prereg["evaluation"], prereg["bootstrap"]
    weeks = world_weeks(world_spec["start"], world_spec["weeks"])
    index = {w: i for i, w in enumerate(weeks)}
    evaluation_weeks = ev["eval_to"] - ev["eval_from"] + 1
    seeds = sorted(runs)
    patterns, decoys = labels["patterns"], labels["decoys"]
    names = list(CHANNELS) + (list(ABLATION_CHANNELS) if ablation_k1 else [])
    per_channel = {name: {s: runs[s]["events"][name] for s in seeds} for name in names}
    blocks = {name: channel_block(name, CHANNEL_LABELS[name] if name in CHANNEL_LABELS else ABLATION_LABEL,
                                  per_channel[name], labels, index, evaluation_weeks) for name in names}
    found = {name: {p["id"]: [pattern_outcome(per_channel[name][s], p, index)["found"] for s in seeds]
                    for p in patterns} for name in CHANNELS}
    lifts = {name: lift(name, found[a], found[b], B=boot["B"], seed=boot["seed"]) for name, a, b in LIFTS}
    blind = is_blind(spec, prereg["detector_author"])
    bound = spec.prereg_sha256 is not None
    warnings = [FEW_PATTERNS_WARNING] if len(patterns) < FEW_PATTERNS else []
    decoy_docs = []
    for i, (d, dl) in enumerate(zip(spec.decoys, decoys)):
        quiet = [{"seed": s, "quiet": runs[s]["decoys"][i]["quiet"]} for s in seeds]
        warnings += [f"decoy {d.id} seed {q['seed']}: not quiet elsewhere; an alert on its key may come from "
                     f"background" for q in quiet if q["quiet"] is False]
        decoy_docs.append({
            "id": d.id, "class": d.decoy_class, "keys": list(dl["keys"]), "sites": list(dl["sites"]),
            "watch_from": dl["watch_from"], "watch_to": dl["watch_to"],
            "outcomes": [{"seed": s, "channel": name, **decoy_outcome(per_channel[name][s], dl)}
                         for s in seeds for name in CHANNELS],
            "quiet_elsewhere": quiet,
            "flags_at_detection": [{"seed": s, "flags": runs[s]["decoys"][i]["flags"]} for s in seeds]})
    by_construction = []
    if any(p["visibility"] == "narrative_only" for p in patterns):
        by_construction = [{"channel": name, "statement": BY_CONSTRUCTION_STATEMENT, "label": BY_CONSTRUCTION_LABEL,
                            "visibility": "narrative_only",
                            "recall": blocks[name]["by_visibility"]["narrative_only"]["recall"]}
                           for name in ("S", "R_mf")]
    known_hard = [{"decoy_id": dl["id"], "class": dl["class"], "seed": s, "note": KNOWN_HARD_NOTE,
                   "alerted": {name: decoy_outcome(per_channel[name][s], dl)["alerted"] for name in CHANNELS}}
                  for dl in decoys if dl["class"] in HARD_CASE_CLASSES for s in seeds]
    doc: dict[str, Any] = {
        "kind": "x1_scorecard", "schema_version": SCHEMA_VERSION, "run_id": run_id, "created_at": utc_clock(),
        "stamps": {"synthetic": True, "internal_only": True, "measurement": False,
                   "same_author_pack": pack.same_author_as_code, "blind": blind, "blind_basis": BLIND_BASIS,
                   "plant_bound_to_prereg": bound, "ablation_k1": ablation_k1, "allow_dirty": allow_dirty,
                   "extractor": "lexical"},
        "hashes": {"config_hash": pack.config_hash, "vocabulary_hash": pack.vocabulary_hash,
                   "detector_hash": pack.detector_hash, "fixtures_hash": pack.fixtures_hash,
                   "code_hash": prereg["code_hash"], "org_hash": runs[seeds[0]]["org_hash"],
                   "prereg_sha256": prereg_sha, "plant_sha256": spec.sha256, "labels_sha256": labels_sha},
        "code": {"code_commit": code_commit(), "code_dirty": dirty, "code_files": list(prereg["code_files"])},
        "pack": {"id": pack.id, "version": pack.version, "illustrative": pack.illustrative, "k": pack.egress.k,
                 "alert_budget_per_week": pack.detectors["alert_budget_per_week"],
                 "window_weeks": pack.detectors["window_weeks"]},
        "world": {"seeds": seeds, "sites": world_spec["sites"], "site_ids": list(world_spec["site_ids"]),
                  "weeks": world_spec["weeks"], "start": world_spec["start"], "eval_from_week": ev["eval_from_week"],
                  "eval_to_week": ev["eval_to_week"], "evaluation_weeks": evaluation_weeks,
                  "grace_weeks": ev["grace_weeks"]},
        "plant": {"planted_by": spec.planted_by, "planter_saw_detector_code": spec.planter_saw_detector_code,
                  "detector_author": prereg["detector_author"], "n_patterns": len(patterns),
                  "n_decoys": len(decoys),
                  "patterns_by_visibility": {v: sum(1 for p in patterns if p["visibility"] == v)
                                             for v in VISIBILITIES},
                  "decoys_by_class": {c: sum(1 for d in decoys if d["class"] == c) for c in DECOY_CLASSES},
                  "planted_records": [{"seed": s, "records": runs[s]["planted_records"]} for s in seeds]},
        "channel_labels": dict(CHANNEL_LABELS),
        "channels": {name: blocks[name] for name in CHANNELS},
        "ablation": ({"label": ABLATION_LABEL, "channels": {name: blocks[name] for name in ABLATION_CHANNELS}}
                     if ablation_k1 else None),
        "lifts": lifts, "by_construction": by_construction, "known_hard_cases": known_hard,
        "patterns": [{"id": p["id"], "key": p["key"], "visibility": p["visibility"], "sites": list(p["sites"]),
                      "start_week": p["start_week"], "end_week": p["end_week"],
                      "outcomes": [{"seed": s, "channel": name, **pattern_outcome(per_channel[name][s], p, index)}
                                   for s in seeds for name in CHANNELS]} for p in patterns],
        "decoys": decoy_docs,
        "suppression": [{"seed": s, **runs[s]["suppression"]} for s in seeds],
        "min_detectable_rate": min_detectable_rate(pack),
        "alerts": [{"seed": s, "channel": name, "items": per_channel[name][s]} for s in seeds for name in names],
        "warnings": warnings,
        "x1": x1_block(blind=blind, bound=bound, n_patterns=len(patterns), n_decoys=len(decoys),
                       lift_x=lifts["X_minus_single_site"], precision_x=blocks["X"]["precision_at_40"]),
        "notes": list(NOTES), "paths": dict(paths), "timings": dict(timings),
        "content_hash_excludes": list(CONTENT_HASH_EXCLUDES),
    }
    doc["content_hash"] = content_hash(doc)
    return doc


# --------------------------------------------------------------------------------------------------- commands

def cmd_prereg(args: argparse.Namespace) -> int:
    try:
        check_run_id(args.run_id)
        seeds = parse_seeds(args.seeds)
        if TIE_SALT_RE.fullmatch(args.tie_salt) is None:
            raise UsageError("--tie-salt must be 1 to 128 printable ASCII characters") from None
        if not 1 <= len(args.detector_author) <= MAX_AUTHOR or not args.detector_author.isprintable():
            raise UsageError(f"--detector-author must be 1 to {MAX_AUTHOR} printable characters") from None
        if args.bootstrap_b < MIN_BOOTSTRAP_B:
            raise UsageError(f"--bootstrap-b must be >= {MIN_BOOTSTRAP_B}") from None
        out_dir = run_dir(args.runs_dir, KIND, args.run_id)
        dry = DryRun(f"{CLI} prereg") if args.dry_run else None
        if dry is not None and not is_builtin_ref(args.pack) and not Path(args.pack).exists():
            dry.need(f"pack {args.pack}")
            dry.write(str(out_dir / "prereg.json"))
            return dry.emit()
        pack = _load_pack(args.pack)
        check_settings(pack, sites=args.sites, weeks=args.weeks, eval_from=args.eval_from, eval_to=args.eval_to,
                       grace_weeks=args.grace_weeks)
        dirty = _dirty_state()
        check_clean(args.allow_dirty, dry, dirty)
        if dry is not None:
            dry.write(str(out_dir / "prereg.json"))
            return dry.emit()
    except UsageError as exc:
        return fail(str(exc))
    start = pack.generator["start"]
    weeks = world_weeks(start, args.weeks)
    doc = {
        "kind": "x1_prereg", "schema_version": SCHEMA_VERSION, "run_id": args.run_id, "created_at": utc_clock(),
        "pack": {"ref": pack_ref(args.pack), "id": pack.id, "version": pack.version,
                 "illustrative": pack.illustrative, "same_author_as_code": pack.same_author_as_code,
                 **pack.hashes(), "k": pack.egress.k,
                 "alert_budget_per_week": pack.detectors["alert_budget_per_week"],
                 "window_weeks": pack.detectors["window_weeks"]},
        "code_hash": eval_code_hash(), "code_files": eval_code_files(), "code_commit": code_commit(),
        "code_dirty": dirty, "allow_dirty": bool(args.allow_dirty),
        "world": {"seeds": seeds, "sites": args.sites,
                  "site_ids": [s["id"] for s in pack.generator["sites"][:args.sites]], "weeks": args.weeks,
                  "start": start},
        "evaluation": {"eval_from": args.eval_from, "eval_to": args.eval_to, "eval_from_week": weeks[args.eval_from],
                       "eval_to_week": weeks[args.eval_to], "grace_weeks": args.grace_weeks, "top_k": TOP_K},
        "tie_salt": args.tie_salt, "detector_author": args.detector_author,
        "bootstrap": {"B": args.bootstrap_b, "seed": args.bootstrap_seed}, "notes": list(PREREG_NOTES),
    }
    out_dir.mkdir(parents=True)
    digest = write_json_atomic(out_dir / "prereg.json", doc)
    print(f"x1 prereg: wrote {out_dir / 'prereg.json'} sha256 {digest}")
    return 0


def _prereg_pack(prereg: Mapping[str, Any]) -> FrozenPack:
    """The pack the prereg names, refusing any of its four hashes that changed (all named)."""
    pack = _load_pack(prereg["pack"]["ref"])
    if pack.id != prereg["pack"]["id"]:
        raise UsageError("the prereg's pack ref now loads another pack id") from None
    return pack


def _check_world(pack: FrozenPack, prereg: Mapping[str, Any]) -> None:
    w, ev = prereg["world"], prereg["evaluation"]
    check_settings(pack, sites=w["sites"], weeks=w["weeks"], eval_from=ev["eval_from"], eval_to=ev["eval_to"],
                   grace_weeks=ev["grace_weeks"])
    weeks = world_weeks(pack.generator["start"], w["weeks"])
    site_ids = [s["id"] for s in pack.generator["sites"][:w["sites"]]]
    if (w["site_ids"] != site_ids or w["start"] != pack.generator["start"] or w["seeds"] != sorted(set(w["seeds"]))
            or ev["eval_from_week"] != weeks[ev["eval_from"]] or ev["eval_to_week"] != weeks[ev["eval_to"]]):
        raise UsageError("the prereg's world settings are inconsistent with its pack") from None


def _plant(path: str, pack: FrozenPack, prereg: Mapping[str, Any]) -> PlantSpec:
    w, ev = prereg["world"], prereg["evaluation"]
    spec = load_plant(path, pack)
    world = generate(pack, w["seeds"][0], w["sites"], w["weeks"])
    check_plant(spec, pack, site_ids=w["site_ids"], master_data=world.master_data, n_weeks=w["weeks"],
                eval_from=ev["eval_from"], eval_to=ev["eval_to"])
    return spec


def cmd_check_plant(args: argparse.Namespace) -> int:
    dry = DryRun(f"{CLI} check-plant") if args.dry_run else None
    try:
        missing = [(what, p) for what, p in (("prereg file", args.prereg), ("plant file", args.plant))
                   if not Path(p).is_file()]
        if dry is not None and missing:
            for what, p in missing:
                dry.need(f"{what} {p}")
            return dry.emit()
        prereg, data = read_prereg(args.prereg)
        pack = _prereg_pack(prereg)
        differing = pinned_differences(prereg, pack, None)
        if differing:
            raise UsageError(f"pinned values differ from the prereg: {', '.join(differing)}") from None
        _check_world(pack, prereg)
        spec = _plant(args.plant, pack, prereg)
    except (UsageError, PlantError, GeneratorError) as exc:
        return fail(str(exc))
    if dry is not None:
        return dry.emit()
    print(f"prereg_sha256: {sha256_hex(data)}")
    print(f"plant: ok (patterns={len(spec.patterns)} decoys={len(spec.decoys)})")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    dry = DryRun(f"{CLI} run") if args.dry_run else None
    try:
        out_dir = run_dir(args.runs_dir, KIND, check_run_id(args.run_id))
        seeds = parse_seeds(args.seeds)
        missing = [(what, p) for what, p in (("prereg file", args.prereg), ("plant file", args.plant))
                   if not Path(p).is_file()]
        if dry is not None and missing:
            for what, p in missing:
                dry.need(f"{what} {p}")
            for name in ("labels.json", "scorecard.json", "work/seed-<seed>/"):
                dry.write(str(out_dir / name))
            return dry.emit()
        prereg, data = read_prereg(args.prereg)
        prereg_sha = sha256_hex(data)
        if seeds != prereg["world"]["seeds"]:
            raise UsageError("--seeds differ from the prereg's seeds") from None
        pack = _prereg_pack(prereg)
        differing = pinned_differences(prereg, pack, eval_code_hash())
        if differing:
            raise UsageError(f"pinned values differ from the prereg: {', '.join(differing)}") from None
        dirty = _dirty_state()
        check_clean(args.allow_dirty, dry, dirty)
        _check_world(pack, prereg)
        spec = _plant(args.plant, pack, prereg)
        if spec.prereg_sha256 is not None and spec.prereg_sha256 != prereg_sha:
            raise UsageError("the plant spec's prereg_sha256 is not the sha256 of this prereg file") from None
        if dry is not None:
            for name in ("labels.json", "scorecard.json", "work/seed-<seed>/"):
                dry.write(str(out_dir / name))
            return dry.emit()
    except (UsageError, PlantError, GeneratorError) as exc:
        return fail(str(exc))
    world_spec, ev = prereg["world"], prereg["evaluation"]
    weeks = world_weeks(world_spec["start"], world_spec["weeks"])
    labels = labels_doc(spec, pack, weeks=weeks, eval_from=ev["eval_from"], eval_to=ev["eval_to"],
                        grace_weeks=ev["grace_weeks"])
    runs: dict[int, dict[str, Any]] = {}
    per_seed_s: list[float] = []
    out_dir.mkdir(parents=True)
    try:
        for seed in seeds:
            t0 = time.perf_counter()
            runs[seed] = _seed_run(pack, spec, prereg, labels, seed, out_dir / "work" / f"seed-{seed}",
                                   ablation_k1=args.ablation_k1)
            per_seed_s.append(time.perf_counter() - t0)
    except KeyboardInterrupt:
        print(f"x1 run: interrupted; {out_dir} is partial and its run id cannot be reused", file=sys.stderr)
        return 130
    except (EvaluationError, GeneratorError, PlantError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"cannot read or write a run file ({exc.__class__.__name__})")
    labels = {**labels, "seeds": [{"seed": s, "planted_records": runs[s]["planted_records"]} for s in seeds]}
    labels_sha = write_json_atomic(out_dir / "labels.json", labels)
    paths = {"pack_ref": pack.directory, "prereg": str(Path(args.prereg).resolve()),
             "plant": str(Path(args.plant).resolve()),
             "run_dir": str(out_dir.resolve()), "workdir": str((out_dir / "work").resolve())}
    doc = build_scorecard(run_id=args.run_id, pack=pack, prereg=prereg, prereg_sha=prereg_sha, spec=spec,
                          labels=labels, labels_sha=labels_sha, runs=runs, ablation_k1=args.ablation_k1,
                          allow_dirty=bool(args.allow_dirty), dirty=dirty, paths=paths,
                          timings={"total_s": time.perf_counter() - started, "per_seed_s": per_seed_s})
    problems = scorecard_problems(doc)
    if problems:
        raise AssertionError(f"scorecard fails its schema at {problems[0][0]} ({problems[0][1]}): a bug")
    write_json_atomic(out_dir / "scorecard.json", doc)
    recall = " ".join(f"{name}={doc['channels'][name]['found']}/{doc['channels'][name]['units']}"
                      for name in ("X", "S", "R_mf", "U", "single_site"))
    print(f"x1: pack={pack.id} seeds={len(seeds)} patterns={len(spec.patterns)} decoys={len(spec.decoys)} "
          f"blind={str(doc['stamps']['blind']).lower()} recall {recall} -> {out_dir / 'scorecard.json'} "
          "(synthetic, internal only, not a measurement)")
    return 0


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.evaluate.harness",
                                description="X1/X2: pre-registered evaluation of the frozen detectors on planted "
                                            "synthetic worlds (internal only, not a measurement).")
    sub = p.add_subparsers(dest="command", required=True)

    def common(s: argparse.ArgumentParser) -> None:
        s.add_argument("--runs-dir", default="runs")
        s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("prereg", help="freeze and hash the settings, the pack and the code before any plant is seen")
    s.add_argument("--pack", required=True, help="a built-in pack id or a pack directory")
    s.add_argument("--seeds", required=True, help="comma-separated world seeds")
    s.add_argument("--sites", type=int, default=6)
    s.add_argument("--weeks", type=int, default=52)
    s.add_argument("--eval-from", type=int, required=True, help="first evaluation week (0-based index)")
    s.add_argument("--eval-to", type=int, required=True, help="last evaluation week (0-based index)")
    s.add_argument("--grace-weeks", type=int, default=4)
    s.add_argument("--tie-salt", required=True)
    s.add_argument("--detector-author", required=True, help="who wrote the detector code (blindness check)")
    s.add_argument("--bootstrap-b", type=int, default=10000)
    s.add_argument("--bootstrap-seed", type=int, default=1)
    s.add_argument("--run-id", required=True)
    s.add_argument("--allow-dirty", action="store_true")
    common(s)
    s = sub.add_parser("check-plant", help="check a plant spec against a prereg and print the prereg's sha256")
    s.add_argument("--prereg", required=True)
    s.add_argument("--plant", required=True)
    common(s)
    s = sub.add_parser("run", help="run every seed and write labels.json and scorecard.json")
    s.add_argument("--prereg", required=True)
    s.add_argument("--plant", required=True)
    s.add_argument("--seeds", required=True, help="the prereg's seeds, repeated")
    s.add_argument("--run-id", required=True)
    s.add_argument("--ablation-k1", action="store_true", help="also run X and S on unsuppressed cells (internal)")
    s.add_argument("--allow-dirty", action="store_true")
    common(s)
    return p


COMMANDS = {"prereg": cmd_prereg, "check-plant": cmd_check_plant, "run": cmd_run}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
