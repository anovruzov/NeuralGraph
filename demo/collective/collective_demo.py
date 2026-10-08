#!/usr/bin/env python
"""The collective demo (G8): one fictional multi-site device maker, from run files only.

    python demo/collective/collective_demo.py --record [DIR]           # headless full loop; writes the six run files
    python demo/collective/collective_demo.py --serve [--out DIR] [--cut]   # live console; run files written at the end
    python demo/collective/collective_demo.py --replay [DIR]           # serve a recorded run, no engine
    python demo/collective/collective_demo.py --export PAGE [--run DIR]   # a standalone page of a recorded run

**Fictional company, synthetic data, an illustration, not a measured result; internal use only.** STRATEGY section
9.1, the YC demo's own rules, puts synthetic-fixture results of any kind off the screen, and section 12 keeps
synthetic-fixture numbers from buyers even with a label; this run shows such results, so it is internal until the
founder decides otherwise (README). Every number on screen is read from the run files and ``lint_numbers.py`` fails the build when
one is not; the sites are simulated in one process.

The loop (``DemoEngine``): **prepare** builds the scenario's world (``scenario.py``; the canary manifest goes to
``<workdir>/private/manifest.json``, never into a run directory), lets every site ingest and extract its own records
(a deterministic stand-in model per site, or ``--routing``), emit its k-suppressed weekly cells at each week's
closing date and its usage summary, and runs HQ's detection (X, and S) and the evaluation baselines R (model-free),
U and each site alone over the same world; **check** asks the sites about the hero key (pushdown verification at the
run's ``as_of``) and about every decoy key X alerted, and scans what crossed so far; **follow-up** (only on a
``supported`` conclusion; built ahead of E2 and X4, not measured) proposes a T0 evidence packet, then a T1 draft for
the named owner, each approved by that owner (scripted in ``--record``, pressed in the console in ``--serve``) and
executed at most once; ``--serve --cut`` presents the 60-second cut live (the problem, the alert, the check and real
data): the follow-up beat is not shown, and its follow-ups run with scripted approval when the presenter moves on
from the check, stamped ``recorded`` like ``--record``'s (audit round 2: the cut could not be given live before, the
server gating real data behind the hidden beat's approvals); **finish** scans every crossing and the primary run files, builds ``screen.json`` and writes the
six files. Every stored timestamp comes from a simulated clock; the wall clock is used only for ``created_at``,
``recorded_at``, the timings and the trace's ``t``.

Run files (``runfiles.RUN_FILES``): ``scorecard.json``, ``trace.json``, ``ledger.jsonl``, ``leakage.json``,
``approvals.jsonl`` (the primary five) and ``screen.json``, each validated against the closed schemas here
(:func:`validate_run`), every digest written as 32 hex, nothing a free-key map. ``ledger.jsonl`` holds HQ's own model
calls only; a site's calls appear only as the usage summaries that crossed its Boundary (``scorecard.ledger.
site_usage``, k-suppressed). The site ledgers are read for one thing, each site task's endpoint flags (every call to
an in-process fake, every call fake-marked), never for a count or a row. A run that fails (an endpoint, a
fallback, a configuration error, an interrupt) writes no run file.

Exit codes of ``--record``: 0 every check passed; 1 the run files were written and a check failed; 2 a usage,
configuration, routing, endpoint, fallback or run-file error (nothing written); 130 interrupted (nothing written).
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import getpass
import json
import logging
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from demo.collective import codes_miss as cm  # noqa: E402
from demo.collective import screen as scr  # noqa: E402
from demo.collective.scenario import DEFAULT as DEFAULT_SCENARIO  # noqa: E402
from demo.collective.scenario import DemoWorld, Scenario, ScenarioError, build_world, load_scenario  # noqa: E402
from mycelic.collective import runfiles, schemacheck  # noqa: E402
from mycelic.collective.detect.detectors import detect  # noqa: E402
from mycelic.collective.detect.store import CollectiveStore, HqReader  # noqa: E402
from mycelic.collective.edge.egress import read_log, verdict_buckets  # noqa: E402
from mycelic.collective.edge.extract import FALLBACK_EXTRACTOR, TASK_NAME, lexical_handler  # noqa: E402
from mycelic.collective.edge.packets import PacketAssembler  # noqa: E402
from mycelic.collective.edge.site import EdgeSite  # noqa: E402
from mycelic.collective.edge.verify import JUDGE_TASK, SiteVerifier, lexical_judge  # noqa: E402
from mycelic.collective.edge.weeks import closed_through, iso_week, next_week  # noqa: E402
from mycelic.collective.evaluate import baselines  # noqa: E402
from mycelic.collective.experiments.common import (RUN_ID_RE, DryRun, UsageError, code_commit,  # noqa: E402
                                                    code_dirty, code_hash, utc_clock)
from mycelic.collective.followup.drafts import (DRAFT_TASK, DraftWriter, template_draft,  # noqa: E402
                                                template_sources)
from mycelic.collective.followup.executors import ExecContext, OutboxExecutor, PacketExecutor  # noqa: E402
from mycelic.collective.followup.ledger import LEDGER_FILE, FollowupLedger  # noqa: E402
from mycelic.collective.followup.policy import (BUILT_AHEAD_LABEL, OUTCOME_LABEL, SYSTEM, KillSwitch,  # noqa: E402
                                                 human)
from mycelic.collective.followup.service import FollowupRefused, FollowupService, replay  # noqa: E402
from mycelic.collective.inference.client import list_models  # noqa: E402
from mycelic.collective.inference.fake import FakeProvider  # noqa: E402
from mycelic.collective.inference.ledger import read_ledger  # noqa: E402
from mycelic.collective.inference.routing import ConfigError, RoutingConfig, load_routing, parse_routing  # noqa: E402
from mycelic.collective.inference.runtime import Runtime  # noqa: E402
from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, strict_load  # noqa: E402
from mycelic.collective.leakage import NOT_COVERED, SHINGLE_CHARS, Artifact, LeakageError, scan  # noqa: E402
from mycelic.collective.leakage import write_manifest  # noqa: E402
from mycelic.collective.packs.canonical import Canonicaliser  # noqa: E402
from mycelic.collective.pushdown.gate import STATUS_RANK  # noqa: E402
from mycelic.collective.pushdown.orchestrator import Orchestrator  # noqa: E402

CLI = "demo.collective.collective_demo"
PROG = "python demo/collective/collective_demo.py"
SCHEMA_VERSION = 1
RECORDED = HERE / "recorded"
CONSOLE = HERE / "console.html"
SCREEN_MARKER = "<!-- collective:screen -->"
SCREEN_SCHEMA = scr.SCREEN_SCHEMA
RUNS_DIR = Path("runs") / "collective"
DAY_TIME = "T08:00:00.000Z"
LEDGER_PER_GROUP = 12
MAX_PAGE_BYTES = 2 * 1024 * 1024
SERVE_PORT, REPLAY_PORT = 8765, 8766
PING_SECONDS = 15.0
CHANNELS = ("X", "S", "R_mf", "U", "single_site")
TASKS = (("extract", TASK_NAME), ("judge", JUDGE_TASK), ("draft", DRAFT_TASK))
STUB_LABEL = "deterministic stand-in, no model"
TEST_SERVER_LABEL = "local test server (fake marker), no model"
TEMPLATE_LABEL = "template, no model"
SITES_NOTE = "sites simulated in one process, one shared model"
TEST_SERVER_SITES_NOTE = ("sites simulated in one process; every site's calls went to one local test server (fake "
                          "marker)")
NO_MODEL_LABELS = (STUB_LABEL, TEST_SERVER_LABEL, TEMPLATE_LABEL)
X_BY_CONSTRUCTION = ("the hero narratives were written by the scenario author in the pack lexicon and read by the "
                     "deterministic lexical handler; extraction is not tested here (see E1 and N1)")
LEDGER_SCOPE = ("HQ's own model calls, row by row; a site's calls only as the usage summaries that crossed its "
                "Boundary, k-suppressed")
X4_NOT_MEASURED = "not measured"
OUTCOME_STATUS = "not yet checked"
CONTENT_HASH_EXCLUDES = ("/code", "/content_hash", "/created_at", "/run_id", "/timings")
RUN_FILES_SCANNED = ("scorecard.json", "trace.json", "ledger.jsonl", "approvals.jsonl")
EVENT_TYPES = ("run_start", "stage", "beat_start", "beat_end", "alert", "question", "verdict", "gate", "followup",
               "packet", "draft", "approval", "execution", "outcome", "leakage", "error", "done")
STAGES = ("world", "sites", "hq", "baselines", "pushdown", "followup", "leakage", "files")
CHECK_TEXTS = (
    ("hero_alerted_by_x", "X alerted the hero key"),
    ("no_rule_for_hero", "No configured rule names the hero key"),
    ("contributing_confirms", "At least three contributing plants confirm"),
    ("sibling_refute", "At least one sibling plant refutes"),
    ("gate_supported", "The commit gate says supported"),
    ("evidence_resolvable", "Every counted confirm resolves at its own plant"),
    ("packets_from_contributing", "The evidence packet is complete, one ok packet per contributing plant"),
    ("executed_once", "Each approved follow-up ran exactly once"),
    ("ledger_chain_ok", "The follow-up ledger's hash chain verifies"),
    ("no_text_crossed", "Both scans found no canary and no narrative text"),
    ("positive_control", "The scanner finds canaries and narrative text in a plant's own database"),
    ("no_decoy_supported", "No decoy reached supported"),
)
_BUCKET_RE = "<k|[0-9]+(-[0-9]+)?|[0-9]+\\+"
_HEX32 = "[0-9a-f]{32}"


class DemoError(Exception):
    """A run that cannot go on: one line, never a traceback; ``hint`` is printed after it."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class SimClock:
    """The simulated clock every stored timestamp comes from; never the wall clock."""

    def __init__(self, value: str) -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


# =================================================================================================== schemas

def _obj(props: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": sorted(props), "properties": dict(props)}


def _nobj(props: Mapping[str, Any]) -> dict[str, Any]:
    out = _obj(props)
    out["type"] = ["object", "null"]
    return out


def _arr(items: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": "array", "items": dict(items), **extra}


_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_I = {"type": "integer", "minimum": 0}
_NI = {"type": ["integer", "null"], "minimum": 0}
_B = {"type": "boolean"}
_NUM = {"type": "number"}
_H32 = {"type": "string", "pattern": _HEX32}
_NH32 = {"type": ["string", "null"], "pattern": _HEX32}
_BUCKET = {"type": ["string", "null"], "pattern": _BUCKET_RE}
_WEEK = {"type": "string", "pattern": "[0-9]{4}-W[0-9]{2}"}
_NWEEK = {"type": ["string", "null"], "pattern": "[0-9]{4}-W[0-9]{2}"}
_DATE = {"type": "string", "pattern": "[0-9]{4}-[0-9]{2}-[0-9]{2}"}
_TS = {"type": "string", "pattern": "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z"}
_WINDOW = _obj({"start_week": _WEEK, "end_week": _WEEK})
_RELATED = _arr(_obj({"key": _S, "week": _WEEK, "rank": _I}))
_CHANNEL = _obj({"rank": _NI, "caught": _B, "detection_week": _NWEEK, "related": _RELATED})
_CODES = _arr(_obj({"code": _S, "n": _S}))
_CO = _arr(_obj({"entity_type": _S, "entity_id": _S, "n": _S}))
_FOLLOWUP_PART = {"key": _S, "type": _S, "type_label": _S, "state": _S, "owner": _NS, "owner_role": _NS,
                  "owner_role_label": _NS, "ack_due": _NS,
                  "approval": {"type": ["string", "null"], "enum": ["recorded", "live", None]},
                  "execute_requests": _I, "executor_calls": _I}
SCORECARD_SCHEMA = _obj({
    "kind": {"type": "string", "const": "collective_demo_scorecard"},
    "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
    "run_id": {"type": "string", "pattern": scr.RUN_ID_PATTERN},
    "created_at": _TS,
    "mode": {"type": "string", "enum": ["record", "live"]},
    "stamps": _obj({"fictional": {"type": "boolean", "const": True}, "synthetic": {"type": "boolean", "const": True},
                    "internal_only": {"type": "boolean", "const": True},
                    "illustration": {"type": "boolean", "const": True},
                    "measurement": {"type": "boolean", "const": False}, "same_author_pack": _B,
                    "approval": {"type": "string", "enum": ["recorded", "live"]},
                    "sites_simulated": {"type": "boolean", "const": True}, "shared_model": _B,
                    "secret_mode": {"type": "string", "const": "seeded-demo"}}),
    "company": _S,
    "illustration": _S,
    "scenario": _obj({"digest": _H32, "seed": _I, "weeks": _I, "first_week": _WEEK, "last_week": _WEEK,
                      "as_of": _DATE, "sites": _I, "countries": _I, "records": _I, "hero_records": _I,
                      "hero_sites": _I, "visibility": _S,
                      "structured_fill": _arr(_obj({"entity_type": _S, "entity_type_label": _S, "filled": _I,
                                                    "records": _I}))}),
    "pack": _obj({"id": _S, "version": _S, "illustrative": _B, "config_digest": _H32, "vocabulary_digest": _H32,
                  "detector_digest": _H32, "fixtures_digest": _H32, "k": _I, "alert_budget_per_week": _I,
                  "window_weeks": _I, "min_window_weeks": _I}),
    "providers": _arr(_obj({"task": {"type": "string", "enum": [t for t, _ in TASKS]}, "task_name": _S, "label": _S,
                            "calls": _NI, "fake": _B})),
    "hero": _obj({
        "key": _obj({"entity_type": _S, "entity_type_label": _S, "entity_id": _S, "predicate": _S,
                     "predicate_label": _S, "key": _S}),
        "code_labels": _arr(_S),
        "sites": _arr(_obj({"site": _S, "language": _S})),
        "window": _WINDOW,
        "case_keys": _arr(_S),
        "case_key_labels": _arr(_obj({"key": _S, "entity_type_label": _S, "entity_id": _S,
                                      "predicate_label": _S})),
        "detection": _obj({
            "budget": _I,
            "X": _obj({"rank": _NI, "score": {"type": ["number", "null"]}, "detection_week": _NWEEK, "caught": _B,
                       "related": _RELATED}),
            "S": _CHANNEL, "R_mf": _CHANNEL, "U": _CHANNEL, "single_site": _CHANNEL,
            "r_caught": _B, "s_caught": _B,
            "by_construction": _obj({"S": _B, "R_mf": _B, "reason": _NS, "X": _B, "x_reason": _NS}),
            "rule_candidates": _arr(_S),
            "rule_hits_case_keys": _arr(_obj({"rule_id": _S, "key": _S, "first_week": _WEEK})),
            "channel_labels": _arr(_obj({"channel": {"type": "string", "enum": list(CHANNELS)}, "label": _S}))}),
        "pushdown": _nobj({
            "question_id": _H32, "question_text": _S, "template_id": _S, "window": _WINDOW, "as_of": _DATE,
            "detection_week": _WEEK,
            "routes": _arr(_obj({"site": _S, "role": {"type": "string", "enum": ["contributing", "sibling"]}})),
            "verdicts": _arr(_obj({"site": _S, "role": {"type": "string", "enum": ["contributing", "sibling"]},
                                   "verdict": {"type": "string", "enum": ["confirm", "refute", "unknown"]},
                                   "reason": _NS, "support_bucket": _BUCKET, "roots_bucket": _BUCKET,
                                   "reporters_bucket": _BUCKET, "entity_records_bucket": _BUCKET,
                                   "newest_week": _NWEEK,
                                   "evidence_ref": {"type": ["string", "null"], "pattern": "[0-9a-f]{16}"},
                                   "quality": _NS, "truncated": _B})),
            "contributing_confirms": _I, "sibling_refutes": _I,
            "gate": _obj({"status": _S, "reasons": _arr(_S), "support_lb": _I, "roots_lb": _I, "reporters_lb": _I,
                          "confirming_sites": _arr(_S), "decision_unit": _NS}),
            "conclusion_id": {"type": "string", "pattern": "c-" + _HEX32}, "conclusion_version": _I,
            "resolvable": _B}),
        "followup": _obj({
            "proposed": _B, "reason": _NS, "label": _NS,
            "x4": {"type": ["string", "null"], "enum": [X4_NOT_MEASURED, None]},
            "packet": _nobj({**_FOLLOWUP_PART, "result_status": _NS,
                             "packets": _arr(_obj({"site": _S, "status": _S, "verdict": _NS,
                                                   "support_bucket": _BUCKET,
                                                   "codes": _arr(_obj({"code": _S, "code_label": _S, "n": _S})),
                                                   "co_mentions": _CO}))}),
            "draft": _nobj({**_FOLLOWUP_PART, "version": _NI, "source": _NS,
                            "fields": _arr(_obj({"name": _S, "value": _S, "source": _S})),
                            "lists": _arr(_obj({"name": _S, "items": _arr(_S), "source": _S}))}),
            "ledger_head": _NH32, "ledger_entries": _NI,
            "outcome": _nobj({"status": {"type": "string", "const": OUTCOME_STATUS}, "label": _S})})}),
    "decoys": _arr(_obj({"id": _S, "label": _S, "keys": _arr(_S), "x_alerted": _B, "x_best_rank": _NI,
                         "x_detection_week": _NWEEK,
                         "flags": _nobj({"echo": _B, "few_reporters": _B, "high_base_rate": _B}),
                         "s_alerted": _B, "r_alerted": _B, "verified": _B, "status": _NS, "reason": _NS})),
    "leakage": _obj({"scope": {"type": "string", "const": "text-only"}, "canaries_planted": _I, "window_chars": _I,
                     "after_pushdown": _nobj({"hit_count": _I, "shingle_overlap_bytes": _I}),
                     "artifact_classes": _arr(_S)}),
    "ledger": _obj({"scope": {"type": "string", "const": LEDGER_SCOPE}, "rows_total": _I, "rows_written": _I,
                    "per_group": _I, "capped": _B,
                    "by_task": _arr(_obj({"task": _S, "calls": _I, "ok": _I, "errors": _I, "tokens_in": _I,
                                          "tokens_out": _I, "tokens_missing": _I, "fake": _B})),
                    "site_usage": _arr(_obj({"site": _S, "closed_through": _S, "task": _S, "endpoint": _S,
                                             "calls": _NI, "ok": _NI, "errors": _arr(_obj({"kind": _S, "n": _NI})),
                                             "fake": _B, "suppressed": _arr(_S)}))}),
    "checks": _arr(_obj({"id": {"type": "string", "enum": [c for c, _ in CHECK_TEXTS]}, "ok": _B, "text": _S})),
    "code": _obj({"commit": _S, "dirty": {"type": "string", "enum": ["yes", "no", "unknown"]}, "digest": _H32}),
    "timings": _obj({"total_s": _NUM, "prepare_s": _NUM, "check_s": _NUM, "followup_s": _NUM, "finish_s": _NUM}),
    "content_hash_excludes": _arr(_S),
    "content_hash": _H32,
})
TRACE_SCHEMA = _obj({
    "kind": {"type": "string", "const": "collective_demo_trace"},
    "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
    "meta": _obj({"run_id": {"type": "string", "pattern": scr.RUN_ID_PATTERN},
                  "mode": {"type": "string", "enum": ["record", "live"]}, "recorded_at": _TS,
                  "duration_s": {"type": ["number", "null"]}, "company": _S,
                  "fictional": {"type": "boolean", "const": True}, "synthetic": {"type": "boolean", "const": True},
                  "internal_only": {"type": "boolean", "const": True},
                  "approval": {"type": "string", "enum": ["recorded", "live"]},
                  "sites_note": {"type": ["string", "null"], "enum": [SITES_NOTE, TEST_SERVER_SITES_NOTE, None]},
                  "replay_command": _S}),
    "org": _obj({"enterprise": _S, "sites": _arr(_obj({
        "site_id": _S, "display_name": _S, "country": _S, "unit_path": _S,
        "hero_role": {"type": ["string", "null"], "enum": ["contributing", "sibling", None]}}))}),
    "beats": _arr(_obj({"id": {"type": "string", "enum": [b for b, _ in scr.BEATS]}, "index": _I, "title": _S,
                        "in_cut": _B})),
    "cut_60s": _arr({"type": "string", "enum": [b for b, _ in scr.BEATS]}),
    "events": _arr(_obj({}), maxItems=0),
    "status": {"type": "string", "enum": ["running", "complete"]},
})
_VERDICT_DATA = {"question_id": _H32, "site": _S, "role": {"type": "string", "enum": ["contributing", "sibling"]},
                 "verdict": {"type": "string", "enum": ["confirm", "refute", "unknown"]}, "reason": _NS,
                 "support_bucket": _BUCKET, "roots_bucket": _BUCKET, "reporters_bucket": _BUCKET,
                 "entity_records_bucket": _BUCKET, "newest_week": _NWEEK,
                 "evidence_ref": {"type": ["string", "null"], "pattern": "[0-9a-f]{16}"}, "quality": _NS}
EVENT_DATA_SCHEMAS: dict[str, dict[str, Any]] = {
    "run_start": _obj({"mode": {"type": "string", "enum": ["record", "live"]}, "scenario_digest": _H32}),
    "stage": _obj({"name": {"type": "string", "enum": list(STAGES)},
                   "values": _arr(_obj({"name": _S, "value": _I}))}),
    "beat_start": _obj({"beat": {"type": "string", "enum": [b for b, _ in scr.BEATS]}}),
    "beat_end": _obj({"beat": {"type": "string", "enum": [b for b, _ in scr.BEATS]}}),
    "alert": _obj({"channel": {"type": "string", "enum": list(CHANNELS)}, "key": _S, "week": _WEEK, "rank": _I}),
    "question": _obj({"question_id": _H32, "key": _S, "text": _S, "window": _WINDOW, "as_of": _DATE,
                      "routes": _arr(_obj({"site": _S, "role": {"type": "string",
                                                                "enum": ["contributing", "sibling"]}}))}),
    "verdict": _obj(_VERDICT_DATA),
    "gate": _obj({"question_id": _H32, "conclusion_id": {"type": "string", "pattern": "c-" + _HEX32},
                   "version": _I, "status": _S, "reasons": _arr(_S)}),
    "followup": _obj({"key": _S, "type": _S, "tier": _S, "state": _S, "owner": _NS, "owner_role": _NS,
                      "ack_due": _NS}),
    "packet": _obj({"key": _S, "site": _S, "status": _S, "verdict": _NS, "support_bucket": _BUCKET, "codes": _CODES,
                    "co_mentions": _CO}),
    "draft": _obj({"key": _S, "version": _I, "source": _S, "fields": _arr(_obj({"name": _S, "value": _S})),
                   "lists": _arr(_obj({"name": _S, "items": _arr(_S)}))}),
    "approval": _obj({"key": _S, "version": _I, "by": _S, "role": _S,
                      "mode": {"type": "string", "enum": ["recorded", "live"]}}),
    "execution": _obj({"key": _S, "request": _I, "status": _S, "executor_calls": _I}),
    "outcome": _obj({"key": _S, "status": _S, "label": _S}),
    "leakage": _obj({"scan": {"type": "string", "enum": ["after_pushdown", "final"]}, "hit_count": _I,
                     "shingle_overlap_bytes": _I, "canaries_planted": _I}),
    "error": _obj({"message": _S, "hint": _S}),
    "done": _obj({}),
}
EVENT_SCHEMAS = {t: _obj({"seq": _I, "t": _NUM, "beat": {"type": ["string", "null"],
                                                         "enum": [b for b, _ in scr.BEATS] + [None]},
                          "type": {"type": "string", "const": t}, "data": EVENT_DATA_SCHEMAS[t]})
                 for t in EVENT_TYPES}
LEDGER_ROW_SCHEMA = _obj({"site": _S, "task": _S, "attempt": _I, "endpoint": _S, "provider": _S,
                          "model_requested": _S, "boundary_mode": _S, "data_label": _S, "ok": _B,
                          "error_kind": _NS, "tokens_in": _NI, "tokens_out": _NI,
                          "latency_ms": {"type": ["number", "null"]}, "cost_basis": _S, "fake_marker": _B})
_SCAN = _obj({"scan": {"type": "string", "enum": ["after_pushdown", "final"]},
              "artifact_classes": _arr(_obj({"class": _S, "bytes": _I, "items": _I})),
              "hit_count": _I,
              "hits": _arr(_obj({"canary_id": _S, "artifact_class": _S, "file": _S, "match": _S})),
              "shingle_overlap_bytes": _I,
              "shingle_hits": _arr(_obj({"artifact_class": _S, "file": _S, "bytes": _I})),
              "known_limitation_count": _I})
LEAKAGE_SCHEMA = _obj({
    "kind": {"type": "string", "const": "collective_demo_leakage"},
    "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
    "scope": {"type": "string", "const": "text-only"}, "canaries_planted": _I,
    "canaries_by_class": _arr(_obj({"class": _S, "n": _I})), "window_chars": _I,
    "scans": _arr(_SCAN, minItems=2, maxItems=2),
    "run_files_scanned": _arr(_S),
    "positive_control": _obj({"canary_hits": _I, "shingle_overlap_bytes": _I}),
    "site_ledger_hygiene": _obj({"hit_count": _I, "shingle_overlap_bytes": _I}),
    "not_covered": _arr(_S),
})
APPROVAL_HEAD_SCHEMA = _obj({"kind": {"type": "string", "const": runfiles.HEAD_KIND}, "head_hash": _NH32,
                             "entries": _I, "label": _S})
_CODES_MISS = schemacheck.compile(cm.SCHEMA)     # only a codes-miss scenario's scorecard carries the block
_COMPILED = {name: schemacheck.compile(s) for name, s in (
    ("scorecard.json", SCORECARD_SCHEMA), ("trace.json", TRACE_SCHEMA), ("leakage.json", LEAKAGE_SCHEMA),
    ("ledger.jsonl", LEDGER_ROW_SCHEMA), ("head", APPROVAL_HEAD_SCHEMA))}
_EVENTS = {t: schemacheck.compile(s) for t, s in EVENT_SCHEMAS.items()}
_ENTRY_KEYS = ("actor", "at", "hash", "key", "kind", "payload", "prev_hash", "seq")


def _approval_problems(lines: Any) -> list[tuple[str, str]]:
    """The approvals lines: one closed envelope per follow-up ledger entry (its payload holds exactly its kind's keys,
    ``followup.ledger.PAYLOAD_KEYS``), then exactly one ``ledger_head`` line, last."""
    from mycelic.collective.followup.ledger import PAYLOAD_KEYS
    if not isinstance(lines, list) or not lines:
        return [("$", "minItems")]
    out = [(f"$[{len(lines) - 1}]" + p[1:], k) for p, k in _COMPILED["head"].validate(lines[-1])]
    for n, line in enumerate(lines[:-1]):
        path = f"$[{n}]"
        if not isinstance(line, dict) or sorted(line) != list(_ENTRY_KEYS):
            out.append((path, "additionalProperties"))
            continue
        if line["kind"] not in PAYLOAD_KEYS or not isinstance(line["payload"], dict) \
                or sorted(line["payload"]) != list(PAYLOAD_KEYS[line["kind"]]):
            out.append((path + ".payload", "additionalProperties"))
        for key in ("prev_hash", "hash"):
            if not isinstance(line[key], str) or re.fullmatch(_HEX32, line[key]) is None:
                out.append((f"{path}.{key}", "pattern"))
        if isinstance(line["seq"], bool) or not isinstance(line["seq"], int) or line["seq"] != n + 1:
            out.append((path + ".seq", "const"))
    return out


def validate_run(docs: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Every schema problem of the six run files as ``(file, path, keyword)``; a missing file is ``(file, $,
    required)``."""
    out: list[tuple[str, str, str]] = []
    for name in runfiles.RUN_FILES:
        if name not in docs:
            out.append((name, "$", "required"))
            continue
        doc = docs[name]
        if name == "trace.json":
            events = doc.get("events") if isinstance(doc, dict) else None
            out += [(name, p, k) for p, k in _COMPILED[name].validate({**doc, "events": []}
                                                                       if isinstance(doc, dict) else doc)]
            if not isinstance(events, list):
                out.append((name, "$.events", "type"))
                continue
            for n, ev in enumerate(events):
                schema = _EVENTS.get(ev.get("type")) if isinstance(ev, dict) else None
                if schema is None:
                    out.append((name, f"$.events[{n}].type", "enum"))
                    continue
                out += [(name, f"$.events[{n}]" + p[1:], k) for p, k in schema.validate(ev)]
                if ev["seq"] != n:
                    out.append((name, f"$.events[{n}].seq", "const"))
        elif name == "ledger.jsonl":
            if not isinstance(doc, list):
                out.append((name, "$", "type"))
                continue
            for n, row in enumerate(doc):
                out += [(name, f"$[{n}]" + p[1:], k) for p, k in _COMPILED[name].validate(row)]
        elif name == "approvals.jsonl":
            out += [(name, p, k) for p, k in _approval_problems(doc)]
        elif name == "screen.json":
            out += [(name, p, k) for p, k in scr.screen_problems(doc)]
        elif name == "scorecard.json" and isinstance(doc, dict) and "codes_miss" in doc:
            rest = {k: v for k, v in doc.items() if k != "codes_miss"}
            out += [(name, p, k) for p, k in _COMPILED[name].validate(rest)]
            out += [(name, "$.codes_miss" + p[1:], k) for p, k in _CODES_MISS.validate(doc["codes_miss"])]
        else:
            out += [(name, p, k) for p, k in _COMPILED[name].validate(doc)]
    return out


# =================================================================================================== the recorder

class Recorder:
    """The trace being recorded and the current screen, with a condition variable the SSE handlers wait on."""

    def __init__(self, *, run_id: str, mode: str) -> None:
        self._t0 = time.monotonic()
        self._cv = threading.Condition()
        self.run_id = run_id
        self.mode = mode
        self.beat: str | None = None
        self.events: list[dict[str, Any]] = []
        self.screen: dict[str, Any] = scr.build_screen(None, mode=mode, phase="preparing", run_id=run_id)
        self.revision = 0
        self.recorded_at = utc_clock()

    def elapsed(self) -> float:
        return round(time.monotonic() - self._t0, 3)

    def emit(self, type_: str, data: Mapping[str, Any]) -> dict[str, Any]:
        with self._cv:
            event = {"seq": len(self.events), "t": self.elapsed(), "beat": self.beat, "type": type_,
                     "data": strict_load(canonical_bytes(data))}
            problems = _EVENTS[type_].validate(event)
            if problems:
                raise DemoError(f"internal: a {type_} event breaks its schema at {problems[0][0]}")
            self.events.append(event)
            self._cv.notify_all()
            return event

    def beat_start(self, beat: str) -> None:
        with self._cv:
            self.beat = beat
        self.emit("beat_start", {"beat": beat})

    def beat_end(self) -> None:
        if self.beat is not None:
            self.emit("beat_end", {"beat": self.beat})

    def publish(self, screen: Mapping[str, Any]) -> None:
        with self._cv:
            if canonical_bytes(screen) != canonical_bytes(self.screen):
                self.screen = strict_load(canonical_bytes(screen))
                self.revision += 1
                self._cv.notify_all()

    def snapshot(self) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        with self._cv:
            return self.screen, list(self.events), self.revision

    def wait(self, after: int, revision: int, timeout: float) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        with self._cv:
            self._cv.wait_for(lambda: len(self.events) > after + 1 or self.revision != revision, timeout)
            return [dict(e) for e in self.events[after + 1:]], self.revision, self.screen


# =================================================================================================== helpers

def _hint(run_dir: str) -> str:
    return f"replay a recorded run instead: {PROG} --replay {run_dir}"


def _committed_dir() -> Path | None:
    dirs = sorted(p for p in RECORDED.iterdir() if p.is_dir()) if RECORDED.is_dir() else []
    return dirs[0] if len(dirs) == 1 else None


def _replay_hint() -> str:
    committed = _committed_dir()
    return _hint(committed.relative_to(ROOT).as_posix() if committed is not None else "<recorded-dir>")


def _relative(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None


def _key_parts(key: str) -> tuple[str, str, str]:
    t, rest = key.split(":", 1)
    i, p = rest.rsplit(":", 1)
    return t, i, p


class CountingExecutor:
    """Counts the executor calls per follow-up key around the real executor."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: dict[str, int] = {}

    def run(self, ctx: ExecContext) -> dict[str, Any]:
        self.calls[ctx.state.key] = self.calls.get(ctx.state.key, 0) + 1
        return self.inner.run(ctx)


def _first_alerts(alerts: Sequence[Mapping[str, Any]], keys: set[str], first: str,
                  last: str) -> dict[str, Mapping[str, Any]]:
    """The first alert of each key in ``keys`` inside ``[first, last]`` (earliest week, then best rank)."""
    out: dict[str, Mapping[str, Any]] = {}
    for a in sorted(alerts, key=lambda a: (a["week"], a["rank"], a["key"])):
        if a["key"] in keys and first <= a["week"] <= last and a["key"] not in out:
            out[a["key"]] = a
    return out


def sites_stamp(routed: bool, providers: Sequence[Mapping[str, Any]]) -> tuple[bool, str | None]:
    """``(shared_model, sites_note)``: a routed run whose extract or judge calls went to a model shares one model
    across the simulated sites (:data:`SITES_NOTE`); a routed run whose site calls all went to the stand-in or a local
    test server shares none (:data:`TEST_SERVER_SITES_NOTE`); without routing there is no note."""
    labels = {p["task"]: p["label"] for p in providers}
    shared = routed and any(labels.get(task) not in NO_MODEL_LABELS for task in ("extract", "judge"))
    if shared:
        return True, SITES_NOTE
    return False, TEST_SERVER_SITES_NOTE if routed else None


def field_sources(ft: Any, draft_provider_label: str, ledger_source: str) -> dict[str, str]:
    """Where each draft field came from: the template's source (``drafts.template_sources``) for a generated draft
    written without a model, ``model`` for one a model wrote, else the ledger's source (``edited``)."""
    if ledger_source == "generated" and draft_provider_label in NO_MODEL_LABELS:
        return template_sources(ft)
    source = "model" if ledger_source == "generated" else ledger_source
    return {name: source for name in sorted(ft.draft_json_schema()["properties"])}


# =================================================================================================== routing

def check_routing(path: str, *, check_env: bool) -> RoutingConfig:
    """The routing file of a recording with a real local model: every extract and judge endpoint (escalations
    included) is openai_compat with boundary any-simulated; a draft endpoint is openai_compat at central or
    any-simulated."""
    config = load_routing(path, tasks=[TASK_NAME, JUDGE_TASK], check_env=check_env)
    for task in (TASK_NAME, JUDGE_TASK, DRAFT_TASK):
        route = config.routes.get(task)
        if route is None:
            continue
        for name in (route.endpoint, route.escalate_to):
            if name is None:
                continue
            endpoint = config.endpoints[name]
            if endpoint.provider != "openai_compat":
                raise ConfigError(f"$.endpoints.{name}.provider", "must be openai_compat: the fake provider runs "
                                                                  "without --routing") from None
            allowed = ("central", "any-simulated") if task == DRAFT_TASK else ("any-simulated",)
            if endpoint.boundary not in allowed:
                raise ConfigError(f"$.endpoints.{name}.boundary",
                                  "must be " + " or ".join(allowed) + ": one machine plays every simulated site") \
                    from None
    return config


def routed_names(config: RoutingConfig, task: str) -> list[str]:
    route = config.routes.get(task)
    if route is None:
        return []
    return [n for n in (route.endpoint, route.escalate_to) if n is not None]


def preflight(config: RoutingConfig) -> None:
    """``GET /models`` on every routed endpoint; one that does not answer stops the run before anything is built."""
    names = sorted({n for task in (TASK_NAME, JUDGE_TASK, DRAFT_TASK) for n in routed_names(config, task)})
    for name in names:
        endpoint = config.endpoints[name]
        listed = list_models(endpoint)
        if not listed["ok"]:
            status = listed["http_status"]
            why = f"HTTP {status}" if status is not None else "no HTTP response"
            raise DemoError(f"endpoint {name} ({endpoint.host_label}) did not answer GET /models ({why})",
                            _replay_hint())


# =================================================================================================== the engine

@dataclass
class _Followup:
    type_id: str
    args: dict[str, Any]
    key: str | None = None
    approval: str | None = None
    approved_version: int | None = None
    execute_requests: int = 0
    result_status: str | None = None
    executed: bool = False


class DemoEngine:
    """One run: ``prepare``, ``check``, ``start_followup``, ``approve`` (per follow-up key) and ``finish``."""

    def __init__(self, sc: Scenario, *, mode: str, run_id: str, workdir: Path, routing: RoutingConfig | None,
                 recorder: Recorder, out_dir: Path, cut: bool = False) -> None:
        self.sc = sc
        self.pack = sc.pack
        self.mode = mode
        self.cut = cut                      # --serve --cut: the follow-up beat is not shown; approvals scripted
        self.run_id = run_id
        self.workdir = workdir
        self.routing = routing
        self.rec = recorder
        self.out_dir = out_dir
        self.t0 = time.monotonic()
        self.timings = {"prepare_s": 0.0, "check_s": 0.0, "followup_s": 0.0, "finish_s": 0.0}
        self.world: DemoWorld | None = None
        self.clock: SimClock | None = None
        self.runtimes: dict[str, Runtime] = {}
        self.store: CollectiveStore | None = None
        self.verifiers: dict[str, SiteVerifier] = {}
        self.verifier_sites: list[EdgeSite] = []
        self.manifest: Any = None
        self.detection: dict[str, Any] | None = None
        self.without: str | None = None     # an item left out of the world (the codes-miss counterfactual)
        self.raw: dict[str, Any] = {}       # each rule channel's full detection result (codes_miss.py reads it)
        self.codes_miss: dict[str, Any] | None = None   # the rule's block without the gate (codes_miss.py)
        self.codes_miss_variants = True     # False in the rule's own detection-only variant engines
        self.alerts: dict[str, Any] = {}
        self.x_result: dict[str, Any] | None = None
        self.decoys: list[dict[str, Any]] = []
        self.pushdown: dict[str, Any] | None = None
        self.checked = False
        self.scans: list[dict[str, Any]] = []
        self.service: FollowupService | None = None
        self.fu_sites: list[EdgeSite] = []
        self.fu_runtime: Runtime | None = None
        self.counters: dict[str, CountingExecutor] = {}
        self.followups: list[_Followup] = []
        self.followup_reason: str | None = None
        self.chain_ok: bool | None = None
        self.ledger_head: str | None = None
        self.ledger_entries: int | None = None
        self.entries: list[Any] = []
        self.code = _code_stamp()
        self.finished = False
        self._final_scan: dict[str, Any] | None = None
        self._positive: dict[str, int] | None = None
        self._hygiene: dict[str, int] | None = None
        org = sc.raw["org"]["sites"]
        self.site_ids = tuple(s["site_id"] for s in org)

    # ------------------------------------------------------------------ paths
    @property
    def edge(self) -> Path:
        return self.workdir / "edge"

    @property
    def hq(self) -> Path:
        return self.workdir / "hq"

    @property
    def hqdb(self) -> Path:
        return self.workdir / "hqdb" / "collective.sqlite3"

    @property
    def fu_dir(self) -> Path:
        return self.workdir / "followup"

    def close(self) -> None:
        for site in self.verifier_sites + self.fu_sites:
            site.close()
        self.verifier_sites, self.fu_sites = [], []
        if self.service is not None:
            self.service.close()
            self.service = None
        if self.store is not None:
            self.store.close()
            self.store = None
        for runtime in list(self.runtimes.values()) + ([self.fu_runtime] if self.fu_runtime else []):
            runtime.close()
        self.runtimes, self.fu_runtime = {}, None

    # ------------------------------------------------------------------ runtimes
    def _runtime(self, site_id: str) -> Runtime:
        ledger = self.edge / f"site-{site_id}.ledger.jsonl"
        boundary = f"site:{site_id}"
        if self.routing is not None:
            return Runtime(self.routing, boundary=boundary, ledger_path=ledger, run_id=self.run_id, clock=self.clock,
                           data_label="synthetic", simulation=True, environ=os.environ)
        routes = {TASK_NAME: {"endpoint": "site-fake"}, JUDGE_TASK: {"endpoint": "site-fake"}}
        config = parse_routing({"schema_version": 1, "endpoints": {"site-fake": {"provider": "fake",
                                                                                 "boundary": boundary}},
                                "routes": routes}, allow_fake=True)
        provider = FakeProvider()
        canonicaliser = Canonicaliser(self.pack, known=self.world.master_data[site_id])
        provider.register(TASK_NAME, lexical_handler(self.pack, canonicaliser))
        provider.register(JUDGE_TASK, lexical_judge(self.pack, canonicaliser))
        return Runtime(config, boundary=boundary, ledger_path=ledger, run_id=self.run_id, clock=self.clock,
                       data_label="synthetic", allow_fake=True, fake=provider, sleep=lambda s: None, environ={})

    def _drafter(self) -> DraftWriter:
        ledger = self.fu_dir / "central.ledger.jsonl"
        if self.routing is not None:
            if DRAFT_TASK not in self.routing.routes:
                return DraftWriter(self.pack, runtime=None)
            self.fu_runtime = Runtime(self.routing, boundary="central", ledger_path=ledger, run_id=self.run_id,
                                      clock=self.clock, data_label="synthetic", simulation=True, environ=os.environ)
            return DraftWriter(self.pack, runtime=self.fu_runtime)
        config = parse_routing({"schema_version": 1, "endpoints": {"hq-fake": {"provider": "fake",
                                                                               "boundary": "central"}},
                                "routes": {DRAFT_TASK: {"endpoint": "hq-fake"}}}, allow_fake=True)
        provider = FakeProvider()
        provider.register(DRAFT_TASK, template_draft(self.pack))
        self.fu_runtime = Runtime(config, boundary="central", ledger_path=ledger, run_id=self.run_id,
                                  clock=self.clock, data_label="synthetic", allow_fake=True, fake=provider,
                                  sleep=lambda s: None, environ={})
        return DraftWriter(self.pack, runtime=self.fu_runtime)

    def _endpoint_of(self, task: str) -> str:
        if self.routing is None:
            return "site-fake" if task != DRAFT_TASK else "hq-fake"
        names = routed_names(self.routing, task)
        return names[0] if names else "template"

    # ------------------------------------------------------------------ prepare (steps 1 to 3)
    def prepare(self) -> None:
        t0 = time.monotonic()
        sc, pack, rec = self.sc, self.pack, self.rec
        rec.emit("run_start", {"mode": self.mode, "scenario_digest": sc.digest})
        world = self.world = build_world(sc, without=self.without)
        self.manifest = write_manifest(world.manifest, self.workdir / "private" / "manifest.json")
        rec.emit("stage", {"name": "world", "values": [
            {"name": "records", "value": len(world.records)}, {"name": "sites", "value": len(self.site_ids)},
            {"name": "weeks", "value": len(world.weeks)},
            {"name": "canaries_planted", "value": len(world.manifest.canaries)}]})
        ingest_day = date.fromisoformat(max(r["received_date"] for r in world.records)) + timedelta(days=1)
        self.as_of = (ingest_day + timedelta(days=7 + pack.egress.close_lag_days)).isoformat()
        self.last_week = closed_through(self.as_of, pack.egress.close_lag_days)
        self.clock = SimClock(ingest_day.isoformat() + DAY_TIME)
        sites: dict[str, EdgeSite] = {}
        claims = 0
        try:
            for sid in self.site_ids:
                runtime = self.runtimes[sid] = self._runtime(sid)
                site = sites[sid] = EdgeSite(pack, sid, self.edge, runtime=runtime, clock=self.clock,
                                             master_data=world.master_data[sid], hq_dir=self.hq)
                got = site.ingest([r for r in world.records if r["site"] == sid])
                if got.duplicates or sum(got.rejected.values()):
                    raise DemoError(f"site {sid} rejected or duplicated records")
                summary = site.extract("model")
                failed = summary.extractors.get(FALLBACK_EXTRACTOR, 0) + sum(summary.errors.values())
                if failed:
                    kinds = ", ".join(sorted(summary.errors)) or FALLBACK_EXTRACTOR
                    raise DemoError(f"extraction at site {sid} used endpoint {self._endpoint_of(TASK_NAME)} and "
                                    f"failed for {summary.extractors.get(FALLBACK_EXTRACTOR, 0)} records ({kinds}); "
                                    "the demo never falls back silently", _replay_hint())
                claims += summary.claims
            self.clock.set(self.as_of + DAY_TIME)
            for week in world.weeks:
                for sid in self.site_ids:
                    sites[sid].emit_cells(baselines.closing_date(pack, week))
            for sid in self.site_ids:
                sites[sid].emit_cells(self.as_of)
                sites[sid].emit_usage(self.as_of)
            rec.emit("stage", {"name": "sites", "values": [
                {"name": "records_ingested", "value": len(world.records)}, {"name": "claims", "value": claims}]})
            self.hqdb.parent.mkdir(parents=True, exist_ok=True)
            store = self.store = CollectiveStore(self.hqdb, pack, sc.org, clock=self.clock)
            report = store.ingest_log(self.hq / "receive.jsonl")
            if report.duplicates or sum(report.rejected.values()):
                raise DemoError("HQ rejected or duplicated a cells bundle")
            rec.emit("stage", {"name": "hq", "values": [{"name": "bundles", "value": report.accepted}]})
            self._detect(sites)
        finally:
            for site in sites.values():
                site.close()
        if sc.codes_miss is not None and self.codes_miss_variants:
            # the pre-registered rule (docs/collective/b1/PREREG.md): the robustness seeds and the grid are
            # detection-only runs of variant scenarios, each against its own world without the hero item; the gate is
            # applied when the scorecard is written, so every screen of the run carries the statement
            self.codes_miss = cm.evaluate_detection(sc, cm.engine_detection(self),
                                                    functools.partial(cm.detect_only, routing=self.routing))
        self.timings["prepare_s"] = round(time.monotonic() - t0, 3)

    def _detect(self, sites: Mapping[str, EdgeSite]) -> None:
        sc, pack, store, world = self.sc, self.pack, self.store, self.world
        salt = sc.tie_salt
        x = self.x_result = detect(store, as_of=self.as_of, run_channel="X", tie_salt=salt)
        store.save_run(x)
        s = detect(store, as_of=self.as_of, run_channel="S", tie_salt=salt)
        weeks = list(world.weeks)
        while weeks[-1] < self.last_week:
            weeks.append(next_week(weeks[-1]))
        pipeline = baselines.Pipeline(pack=pack, org=sc.org, weeks=tuple(weeks), as_of=self.as_of,
                                      sites=dict(sites), store=store)
        r = baselines.exact_result(pack, sc.org, baselines.r_mf_cells(pack, world.records,
                                                                      master_data=world.master_data,
                                                                      last_week=self.last_week),
                                   as_of=self.as_of, run_channel="S", tie_salt=salt)
        u = baselines.exact_result(pack, sc.org, baselines.u_cells(pipeline), as_of=self.as_of, run_channel="X",
                                   tie_salt=salt)
        single = baselines.single_site_alerts(pipeline, tie_salt=salt)
        self.rec.emit("stage", {"name": "baselines", "values": [{"name": "channels", "value": len(CHANNELS)}]})
        alerts = {"X": x["alerts"], "S": s["alerts"], "R_mf": r["alerts"], "U": u["alerts"], "single_site": single}
        self.raw = {"X": x, "S": s, "R_mf": r, "weeks": tuple(weeks), "last_week": self.last_week,
                    "cooldown_weeks": pack.detectors["cooldown_weeks"]}
        hero = sc.hero
        hero_key = hero.keys[0]
        first = world.weeks[hero.start_week]
        case = set(world.case_keys)
        detection: dict[str, Any] = {"budget": pack.detectors["alert_budget_per_week"]}
        self.alerts = {}
        for channel in CHANNELS:
            found = _first_alerts(alerts[channel], case, first, self.last_week)
            self.alerts[channel] = found
            own = found.get(hero_key)
            related = [{"key": k, "week": a["week"], "rank": a["rank"]}
                       for k, a in sorted(found.items(), key=lambda kv: (kv[1]["week"], kv[1]["rank"], kv[0]))
                       if k != hero_key]
            # every channel says when it first flagged the hero key: the screen shows the single-site week beside
            # X's and says so when one plant alone was no later (audit round 2)
            block: dict[str, Any] = {"rank": own["rank"] if own else None, "caught": own is not None,
                                     "detection_week": own["week"] if own else None, "related": related}
            if channel == "X":
                block["score"] = own["score"] if own else None
            detection[channel] = block
        detection["r_caught"] = detection["R_mf"]["caught"]
        detection["s_caught"] = detection["S"]["caught"]
        detection["by_construction"] = dict(world.by_construction)
        detection["rule_candidates"] = sorted({h["rule_id"] for h in x["rule_hits"] if h["key"] == hero_key})
        detection["rule_hits_case_keys"] = sorted(
            ({"rule_id": h["rule_id"], "key": h["key"], "first_week": h["first_week"]} for h in x["rule_hits"]
             if h["key"] in case and h["key"] != hero_key), key=lambda h: (h["first_week"], h["rule_id"], h["key"]))
        detection["channel_labels"] = [{"channel": c, "label": baselines.CHANNEL_LABELS[c]} for c in CHANNELS]
        self.detection = detection
        demo_first = world.weeks[min(i.start_week for i in sc.items)]
        candidates = {c["key"]: c for c in x["candidates"]}
        self.decoys = []
        for item in sc.items:
            if item.role != "decoy":
                continue
            keys = set(item.keys)
            xa = _first_alerts(alerts["X"], keys, demo_first, self.last_week)
            best = min(xa.values(), key=lambda a: (a["rank"], a["week"], a["key"])) if xa else None
            snapshot = candidates[best["key"]]["snapshot"] if best is not None else None
            flags = None
            if snapshot is not None:
                f = snapshot["flags"]
                flags = {"echo": bool(f["echo"]), "few_reporters": bool(f["few_reporters_sites"]),
                         "high_base_rate": bool(f["high_base_rate"])}
            self.decoys.append({
                "id": item.id, "label": item.label, "keys": sorted(keys), "x_alerted": bool(xa),
                "x_best_rank": best["rank"] if best else None,
                "x_detection_week": min(a["week"] for a in xa.values()) if xa else None, "flags": flags,
                "s_alerted": bool(_first_alerts(alerts["S"], keys, demo_first, self.last_week)),
                "r_alerted": bool(_first_alerts(alerts["R_mf"], keys, demo_first, self.last_week)),
                "verified": False, "status": None, "reason": "not_alerted" if not xa else None,
                "_alerted_keys": sorted(xa)})

    def emit_alerts(self) -> None:
        """One event per first alert of a case key in each channel: channel, key, week and rank only (a baseline's
        counts and features never leave its function)."""
        for channel in CHANNELS:
            for key, a in sorted(self.alerts[channel].items(), key=lambda kv: (kv[1]["week"], kv[1]["rank"], kv[0])):
                self.rec.emit("alert", {"channel": channel, "key": key, "week": a["week"], "rank": a["rank"]})

    # ------------------------------------------------------------------ check (steps 4 and 5)
    def check(self) -> bool:
        """Ask the sites; False when it ran before (nothing is asked or emitted again)."""
        if self.checked:
            return False
        t0 = time.monotonic()
        pack, sc = self.pack, self.sc
        hero_key = sc.hero.keys[0]
        for sid in self.site_ids:
            site = EdgeSite(pack, sid, self.edge, runtime=None, clock=self.clock,
                            master_data=self.world.master_data[sid], hq_dir=self.hq)
            self.verifier_sites.append(site)
            self.verifiers[sid] = SiteVerifier(site, runtime=self.runtimes[sid], clock=self.clock,
                                               demo_seed=sc.seed)
        orchestrator = Orchestrator(self.store, handlers={s: v.answer for s, v in self.verifiers.items()},
                                    clock=self.clock)
        if self.detection["X"]["caught"]:
            conclusion = orchestrator.verify_stored(self.x_result["run_id"], hero_key, as_of=self.as_of)
            self.pushdown = self._pushdown_block(conclusion, emit=True)
        for decoy in self.decoys:
            statuses = []
            for key in decoy["_alerted_keys"]:
                conclusion = orchestrator.verify_stored(self.x_result["run_id"], key, as_of=self.as_of)
                self._pushdown_block(conclusion, emit=True)
                statuses.append(conclusion)
            if statuses:
                top = max(statuses, key=lambda c: (STATUS_RANK[c.status], c.conclusion_id))
                decoy.update(verified=True, status=top.status, reason=top.reasons[0] if top.reasons else None)
        rows = read_log(self.hq / "questions.jsonl") if (self.hq / "questions.jsonl").exists() else []
        verdicts = sum(1 for r in read_log(self.hq / "receive.jsonl") if r["artifact_type"] == "verdict")
        self.rec.emit("stage", {"name": "pushdown", "values": [
            {"name": "questions", "value": len({r["body"]["question_id"] for r in rows})},
            {"name": "deliveries", "value": len(rows)}, {"name": "verdicts", "value": verdicts}]})
        self.checked = True
        report = self._scan("after_pushdown", final=False)
        self.scans.append(report)
        self.rec.emit("leakage", {"scan": "after_pushdown", "hit_count": report["hit_count"],
                                  "shingle_overlap_bytes": report["shingle_overlap_bytes"],
                                  "canaries_planted": len(self.manifest.canaries)})
        self.timings["check_s"] = round(time.monotonic() - t0, 3)
        return True

    def _judge_failures(self, question_id: str) -> None:
        for v in self.store.pd_verdicts(question_id):
            body = strict_load(v.body)
            endpoint = self._endpoint_of(JUDGE_TASK)
            if v.source == "hq":
                raise DemoError(f"the check at site {v.site} used endpoint {endpoint} and ended in {v.reason}; the "
                                "demo never falls back silently", _replay_hint())
            audit = self.verifiers[v.site].audit(body["verdict_id"])
            failures = audit.failures if audit is not None else 0
            if body["quality"] == "degraded" or failures:
                rows = read_ledger(self.runtimes[v.site].ledger.path)
                kinds = sorted({r["error_kind"] for r in rows if r["task"] == JUDGE_TASK and not r["ok"]
                                and r["error_kind"]})
                raise DemoError(f"judging at site {v.site} used endpoint {endpoint} and failed for {failures} records "
                                f"({', '.join(kinds) or 'degraded'}); the demo never falls back silently",
                                _replay_hint())

    def _pushdown_block(self, conclusion: Any, *, emit: bool) -> dict[str, Any]:
        store, pack = self.store, self.pack
        qid = conclusion.question_id
        self._judge_failures(qid)
        question = store.question(qid)
        body = strict_load(question.body)
        routes = {r.site: r.role for r in store.routes(qid)}
        latest: dict[str, Any] = {}
        for v in store.pd_verdicts(qid):
            latest[v.site] = strict_load(v.body)
        verdicts = []
        for site in sorted(routes):
            v = latest.get(site, {})
            verdicts.append({"site": site, "role": routes[site], "verdict": v.get("verdict", "unknown"),
                             "reason": v.get("reason"), "support_bucket": v.get("support_bucket"),
                             "roots_bucket": v.get("roots_bucket"), "reporters_bucket": v.get("reporters_bucket"),
                             "entity_records_bucket": v.get("entity_records_bucket"),
                             "newest_week": v.get("newest_week"), "evidence_ref": v.get("evidence_ref"),
                             "quality": v.get("quality"), "truncated": bool(v.get("truncated", False))})
        gate = conclusion.body["gate"]
        support = gate["support"]
        resolvable = all(self._resolvable(latest[u["site"]]) for u in gate["used"] if u["counted"])
        candidate = store.candidate(self.x_result["run_id"], body["candidate_key"])
        block = {
            "question_id": runfiles.digest(qid), "question_text": question.display_text,
            "template_id": body["template_id"], "window": dict(body["window"]), "as_of": body["as_of"],
            "detection_week": candidate["detection_week"],
            "routes": [{"site": s, "role": routes[s]} for s in sorted(routes)], "verdicts": verdicts,
            "contributing_confirms": sum(1 for v in verdicts
                                         if v["role"] == "contributing" and v["verdict"] == "confirm"),
            "sibling_refutes": sum(1 for v in verdicts if v["role"] == "sibling" and v["verdict"] == "refute"),
            "gate": {"status": conclusion.status, "reasons": list(conclusion.reasons),
                     "support_lb": support["support_lb"], "roots_lb": support["roots_lb"],
                     "reporters_lb": support["reporters_lb"], "confirming_sites": list(support["confirming_sites"]),
                     "decision_unit": conclusion.body["decision_unit"]},
            "conclusion_id": conclusion.conclusion_id, "conclusion_version": conclusion.version,
            "resolvable": resolvable}
        if emit:
            self.rec.emit("question", {"question_id": block["question_id"], "key": body["candidate_key"],
                                       "text": block["question_text"], "window": block["window"],
                                       "as_of": block["as_of"], "routes": block["routes"]})
            for v in verdicts:
                self.rec.emit("verdict", {"question_id": block["question_id"],
                                          **{k: v[k] for k in _VERDICT_DATA if k != "question_id"}})
            self.rec.emit("gate", {"question_id": block["question_id"], "conclusion_id": block["conclusion_id"],
                                   "version": block["conclusion_version"], "status": conclusion.status,
                                   "reasons": list(conclusion.reasons)})
        return block

    def _resolvable(self, body: Mapping[str, Any]) -> bool:
        """The confirm's evidence reference resolves at its own site to that site's own, non-forwarded records of the
        window, all of them its confirming records."""
        verifier = self.verifiers[body["site"]]
        resolution = verifier.resolve(body["evidence_ref"])
        audit = verifier.audit(body["verdict_id"])
        if resolution is None or audit is None or resolution.verdict != "confirm":
            return False
        window = body["window"]
        own = {r.record_ref for r in verifier.site.store.window_records(window["start_week"], window["end_week"])}
        return (bool(resolution.record_refs) and len(resolution.record_refs) == audit.confirming
                and all(ref in own for ref in resolution.record_refs))

    # ------------------------------------------------------------------ follow-up (steps 6 to 9)
    def start_followup(self) -> None:
        if self.service is not None or self.followup_reason is not None:
            return
        if self.pushdown is None:
            self.followup_reason = "not_alerted"
            return
        if self.pushdown["gate"]["status"] != "supported":
            self.followup_reason = "conclusion_not_supported"
            return
        t0 = time.monotonic()
        pack, sc = self.pack, self.sc
        base = self.fu_dir
        base.mkdir(parents=True, exist_ok=True)
        approvers, kill = base / "approvers.json", base / "kill.json"
        approvers.write_bytes(canonical_bytes(dict(sc.approvers)) + b"\n")
        kill.write_bytes(canonical_bytes({"schema_version": 1, "global": "off", "types": {}}) + b"\n")
        outbox = base / "outbox.jsonl"
        outbox.touch()
        ledger = FollowupLedger.create(base / LEDGER_FILE, pack=pack, enterprise=sc.org.enterprise, clock=self.clock)
        handlers = {}
        for sid in self.site_ids:
            site = EdgeSite(pack, sid, self.edge, runtime=None, clock=self.clock,
                            master_data=self.world.master_data[sid], hq_dir=self.hq)
            self.fu_sites.append(site)
            handlers[sid] = PacketAssembler(site, clock=self.clock).handle
        self.counters = {"packet": CountingExecutor(PacketExecutor(pack, handlers)),
                         "draft": CountingExecutor(OutboxExecutor(outbox))}
        self.service = FollowupService(ledger, pack=pack, org=sc.org, hq=HqReader(self.hqdb),
                                       approvers_path=approvers, kill_switch=KillSwitch(kill, pack, environ={}),
                                       drafter=self._drafter(), executors=dict(self.counters))
        cid = self.pushdown["conclusion_id"]
        for spec in sc.followups:
            ft = pack.followups[spec["type"]]
            args = dict(spec["args"])
            for name in sorted(ft.args):
                if ft.args[name]["kind"] == "conclusion_id":
                    args[name] = cid
            self.followups.append(_Followup(type_id=spec["type"], args=args))
        self._propose_next()
        self.timings["followup_s"] = round(self.timings["followup_s"] + time.monotonic() - t0, 3)

    def _propose_next(self) -> None:
        pending = [f for f in self.followups if f.key is None]
        if not pending or any(f.key is not None and not f.executed for f in self.followups):
            return
        f = pending[0]
        f.key = self.service.propose(self.pushdown["conclusion_id"], f.type_id, f.args, principal=SYSTEM,
                                     as_of=self.as_of)
        state = self.service.state(f.key)
        self.rec.emit("followup", {"key": f.key, "type": f.type_id, "tier": state.tier, "state": state.status,
                                   "owner": state.owner, "owner_role": state.owner_role, "ack_due": state.ack_due})
        for version, draft, source in state.drafts:
            self.rec.emit("draft", {"key": f.key, "version": version, "source": source, **_draft_parts(draft)})

    def awaiting(self) -> list[str]:
        """The follow-up keys proposed and not yet approved."""
        return [f.key for f in self.followups if f.key is not None and f.approval is None]

    @property
    def approval(self) -> str:
        """``recorded`` (scripted) in ``--record`` and in the live cut, which does not show the follow-up beat;
        ``live`` when the presenter approves in the console."""
        return "recorded" if self.mode == "record" or self.cut else "live"

    def followups_done(self) -> bool:
        return self.followup_reason is not None or (bool(self.followups) and all(f.executed for f in self.followups))

    def approve(self, key: str) -> str:
        """``approved`` (and executed), or ``done_before`` for a key already approved; a scripted approval (record
        mode and the live cut) executes twice to show at-most-once, a live one once per press."""
        f = next((f for f in self.followups if f.key == key), None)
        if f is None:
            raise DemoError("internal: no such follow-up key")
        if f.approval is not None:
            return "done_before"
        t0 = time.monotonic()
        state = self.service.state(key)
        try:
            self.service.approve(key, state.latest_version, principal=human(state.owner), as_of=self.as_of)
        except FollowupRefused as exc:
            if exc.code == "terminal":
                return "done_before"
            raise DemoError(f"the follow-up was refused ({exc.code})") from None
        f.approval = self.approval
        f.approved_version = state.latest_version
        self.rec.emit("approval", {"key": key, "version": state.latest_version, "by": state.owner,
                                   "role": state.owner_role, "mode": f.approval})
        for request in range(1, (2 if f.approval == "recorded" else 1) + 1):
            done = self.service.execute(key, as_of=self.as_of)
            f.execute_requests += 1
            calls = self.counters[state.executor].calls.get(key, 0)
            self.rec.emit("execution", {"key": key, "request": request, "status": done.status,
                                        "executor_calls": calls})
            if done.status != "executed":
                raise DemoError(f"the follow-up did not execute ({done.status})")
            if request == 1 and state.executor == "packet":
                f.result_status = done.result["status"]
                for p in done.result["packets"]:
                    self.rec.emit("packet", {"key": key, **_packet_summary(p)})
            elif request == 1:
                f.result_status = "written"
        f.executed = True
        self.rec.emit("outcome", {"key": key, "status": OUTCOME_STATUS, "label": OUTCOME_LABEL})
        self._propose_next()
        if self.followups_done():
            self._close_followup()
        self.timings["followup_s"] = round(self.timings["followup_s"] + time.monotonic() - t0, 3)
        return "approved"

    def _capture_ledger(self) -> None:
        entries = self.service.ledger.entries()
        self.entries = entries
        head = self.service.head_hash()
        self.chain_ok = self.service.verify_chain(expected_head=head, expected_entries=len(entries)).ok
        self.ledger_head, self.ledger_entries = runfiles.digest(head), len(entries)

    def _close_followup(self) -> None:
        self._capture_ledger()
        executed = sum(1 for f in self.followups if f.executed)
        self.rec.emit("stage", {"name": "followup", "values": [{"name": "proposed", "value": len(self.followups)},
                                                               {"name": "executed", "value": executed}]})

    # ------------------------------------------------------------------ leakage
    def _crossing(self, final: bool, run_docs: Mapping[str, Any] | None) -> tuple[list[Artifact], list[Artifact]]:
        classes = {"cells_bundle": "cells", "usage_summary": "usage_summary", "verdict": "verdicts",
                   "packet": "packets"}
        out = [Artifact(classes[row["artifact_type"]], f"hq/receive.jsonl#{n}", data=canonical_bytes(row["body"]))
               for n, row in enumerate(read_log(self.hq / "receive.jsonl"), start=1)]
        for name, cls in (("questions.jsonl", "questions"), ("packet_requests.jsonl", "packet_requests")):
            if (self.hq / name).exists():
                out += [Artifact(cls, f"hq/{name}#{n}", data=canonical_bytes(row["body"]))
                        for n, row in enumerate(read_log(self.hq / name), start=1)]
        for sid in self.site_ids:
            for name, cls in ((f"site-{sid}.egress.jsonl", "site_egress_log"),
                              (f"site-{sid}.ingress.jsonl", "site_ingress_log")):
                if (self.edge / name).exists():
                    out.append(Artifact(cls, f"edge/{name}", path=self.edge / name))
        for path in (self.hqdb, self.hqdb.with_name(self.hqdb.name + "-wal")):
            if path.exists():
                out.append(Artifact("collective_sqlite3", f"hqdb/{path.name}", data=path.read_bytes()))
        if final:
            ledger = self.fu_dir / LEDGER_FILE
            for path in (ledger, ledger.with_name(ledger.name + "-wal")):
                if path.exists():
                    out.append(Artifact("approvals_ledger", f"followup/{path.name}", data=path.read_bytes()))
            for name, cls in (("outbox.jsonl", "outbox"), ("central.ledger.jsonl", "hq_draft_ledger")):
                if (self.fu_dir / name).exists():
                    out.append(Artifact(cls, f"followup/{name}", path=self.fu_dir / name))
            out += [Artifact("drafts", f"followup/{LEDGER_FILE}#seq-{e.seq}", data=canonical_bytes(e.payload["draft"]))
                    for e in self.entries if e.kind in ("drafted", "edited")]
            for name in RUN_FILES_SCANNED:
                out.append(Artifact("run_files", f"run/{name}", data=runfiles.serialise(name, run_docs[name])))
        hygiene = [Artifact("site_ledger_hygiene", f"edge/site-{s}.ledger.jsonl", path=self.edge /
                            f"site-{s}.ledger.jsonl") for s in self.site_ids
                   if (self.edge / f"site-{s}.ledger.jsonl").exists()]
        return out, hygiene

    def _scan(self, name: str, *, final: bool, run_docs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        crossing, hygiene = self._crossing(final, run_docs)
        report = scan(crossing, self.manifest, self.world.narratives, self.pack, hygiene=hygiene)
        if final:
            self._hygiene = {"hit_count": len(report["site_ledger_hygiene"]["hits"]),
                             "shingle_overlap_bytes": report["site_ledger_hygiene"]["shingle_overlap_bytes"]}
        return {"scan": name,
                "artifact_classes": [{"class": c, "bytes": v["bytes"], "items": v["items"]}
                                     for c, v in sorted(report["artifact_classes"].items())],
                "hit_count": report["hit_count"],
                "hits": [{"canary_id": h["canary_id"], "artifact_class": h["artifact_class"], "file": h["file"],
                          "match": h["match"]} for h in report["hits"]],
                "shingle_overlap_bytes": report["shingle_overlap_bytes"],
                "shingle_hits": [{"artifact_class": s["artifact_class"], "file": s["file"], "bytes": s["bytes"]}
                                 for s in report["shingle_hits"]],
                "known_limitation_count": len(report["known_limitation"])}

    def _positive_control(self) -> dict[str, int]:
        db = self.edge / f"site-{self.site_ids[0]}.sqlite3"
        artifacts = [Artifact("positive_control", f"edge/{db.name}", path=db)]
        wal = db.with_name(db.name + "-wal")
        if wal.exists():
            artifacts.append(Artifact("positive_control", f"edge/{wal.name}", path=wal))
        report = scan(artifacts, self.manifest, self.world.narratives, self.pack)
        return {"canary_hits": report["hit_count"] + len(report["known_limitation"]),
                "shingle_overlap_bytes": report["shingle_overlap_bytes"]}

    # ------------------------------------------------------------------ documents
    def _providers(self) -> list[dict[str, Any]]:
        hq_rows, flags = self._hq_rows(), self._site_flags()
        out = []
        for task, name in TASKS:
            hq_task = [r for r in hq_rows if r["task"] == name]
            seen, all_stub, all_marked, any_marked = (flags[name] if name in flags else (
                bool(hq_task), all(r["provider"] == "fake" for r in hq_task), all(r["fake_marker"] for r in hq_task),
                any(r["fake_marker"] for r in hq_task)))
            if seen and all_stub:
                label = STUB_LABEL
            elif seen and all_marked:
                label = TEST_SERVER_LABEL
            elif self.routing is None:
                label = STUB_LABEL
            elif not routed_names(self.routing, name):
                label = TEMPLATE_LABEL
            else:
                endpoint = self.routing.endpoints[routed_names(self.routing, name)[0]]
                where = "structured inputs only, at HQ" if task == "draft" else SITES_NOTE
                label = f"{endpoint.name}: model {endpoint.model}, {where}"
            out.append({"task": task, "task_name": name, "label": label,
                        "calls": None if name in flags else len(hq_task), "fake": any_marked})
        return out

    def _hq_rows(self) -> list[dict[str, Any]]:
        """HQ's own ledger (the central drafter's): the only per-call rows a run file may hold."""
        central = self.fu_dir / "central.ledger.jsonl"
        return read_ledger(central) if central.exists() else []

    def _site_flags(self) -> dict[str, tuple[bool, bool, bool, bool]]:
        """Per site task: ``(any call, every call to an in-process fake, every call fake-marked, any call
        fake-marked)`` over every site ledger. Only these flags leave this method: no count and no row."""
        flags: dict[str, tuple[bool, bool, bool, bool]] = {}
        for sid in self.site_ids:
            path = self.edge / f"site-{sid}.ledger.jsonl"
            for r in read_ledger(path) if path.exists() else []:
                seen, stub, marked, any_marked = flags.get(r["task"], (False, True, True, False))
                flags[r["task"]] = (True, stub and r["provider"] == "fake", marked and bool(r["fake_marker"]),
                                    any_marked or bool(r["fake_marker"]))
        return {name: flags.get(name, (False, True, True, False)) for _, name in TASKS if name != DRAFT_TASK}

    def _followup_doc(self, draft_label: str) -> dict[str, Any]:
        if self.followup_reason is not None or not self.followups:
            reason = self.followup_reason if self.followup_reason is not None else (
                "not_alerted" if self.detection is None or not self.detection["X"]["caught"] else "not_yet_checked"
                if not self.checked else "awaiting_followup")
            return {"proposed": False, "reason": reason, "label": None, "x4": None, "packet": None, "draft": None,
                    "ledger_head": None, "ledger_entries": None, "outcome": None}
        pack = self.pack
        parts: dict[str, Any] = {"packet": None, "draft": None}
        states = replay(self.service.ledger.entries() if self.service is not None else self.entries)
        for f in self.followups:
            if f.key is None:
                continue
            state = states.get(f.key)
            ft = pack.followups[f.type_id]
            common = {"key": f.key, "type": f.type_id, "type_label": ft.label,
                      "state": state.status if state else "executed", "owner": state.owner if state else None,
                      "owner_role": state.owner_role if state else None,
                      "owner_role_label": pack.roles[state.owner_role].label if state and state.owner_role else None,
                      "ack_due": state.ack_due if state else None, "approval": f.approval,
                      "execute_requests": f.execute_requests,
                      "executor_calls": self.counters[ft.executor].calls.get(f.key, 0)}
            if ft.executor == "packet":
                packets = [_packet_summary(p) for p in (state.result or {}).get("packets", [])] if state else []
                for p in packets:
                    p["codes"] = [{"code": c["code"], "code_label": pack.codes[c["code"]].label, "n": c["n"]}
                                  for c in p["codes"]]
                parts["packet"] = {**common, "result_status": f.result_status, "packets": packets}
            else:
                version, draft, source = state.drafts[-1] if state and state.drafts else (None, {}, None)
                sources = field_sources(ft, draft_label, source) if source is not None else {}
                drafted = _draft_parts(draft)
                parts["draft"] = {**common, "version": version, "source": source,
                                  "fields": [{**f, "source": sources[f["name"]]} for f in drafted["fields"]],
                                  "lists": [{**x, "source": sources[x["name"]]} for x in drafted["lists"]]}
        return {"proposed": True, "reason": None, "label": BUILT_AHEAD_LABEL, "x4": X4_NOT_MEASURED,
                "packet": parts["packet"], "draft": parts["draft"], "ledger_head": self.ledger_head,
                "ledger_entries": self.ledger_entries,
                "outcome": {"status": OUTCOME_STATUS, "label": OUTCOME_LABEL}}

    def _checks(self) -> list[dict[str, Any]]:
        det, pd = self.detection, self.pushdown
        fu = [f for f in self.followups]
        packet = next((f for f in fu if self.pack.followups[f.type_id].executor == "packet"), None)
        contributing = sorted(s for s, _, _ in self.sc.hero.sites)
        packet_ok = False
        if packet is not None and packet.key is not None:
            result = next((e.payload["result"] for e in self.entries if e.kind == "executed" and e.key == packet.key),
                          None)
            if result is not None:
                ok_sites = sorted(p["site"] for p in result["packets"] if p["status"] == "ok")
                packet_ok = result["status"] == "complete" and ok_sites == contributing
        final = self._final_scan
        after = self.scans[0] if self.scans else None
        positive = self._positive or {"canary_hits": 0, "shingle_overlap_bytes": 0}
        values = {
            "hero_alerted_by_x": det["X"]["caught"],
            "no_rule_for_hero": det["rule_candidates"] == [],
            "contributing_confirms": pd is not None and pd["contributing_confirms"] >= 3,
            "sibling_refute": pd is not None and pd["sibling_refutes"] >= 1,
            "gate_supported": pd is not None and pd["gate"]["status"] == "supported",
            "evidence_resolvable": pd is not None and pd["resolvable"],
            "packets_from_contributing": packet_ok,
            "executed_once": bool(fu) and all(
                f.executed and self.counters[self.pack.followups[f.type_id].executor].calls.get(f.key) == 1
                and f.execute_requests == (2 if f.approval == "recorded" else 1) for f in fu),
            "ledger_chain_ok": self.chain_ok is True,
            "no_text_crossed": after is not None and final is not None and all(
                s["hit_count"] == 0 and s["shingle_overlap_bytes"] == 0 for s in (after, final)),
            "positive_control": positive["canary_hits"] > 0 and positive["shingle_overlap_bytes"] > 0,
            "no_decoy_supported": all(d["status"] != "supported" for d in self.decoys),
        }
        return [{"id": cid, "ok": bool(values[cid]), "text": text} for cid, text in CHECK_TEXTS]

    def docs(self, *, final: bool = False) -> dict[str, Any] | None:
        """The primary documents for the run so far (None before ``prepare`` finished)."""
        if self.detection is None:
            return None
        sc, pack, world = self.sc, self.pack, self.world
        hero = sc.hero
        key = hero.key
        hero_records = world.item_records[hero.id]
        ledger_rows, ledger_summary = runfiles.project_ledger(self._hq_rows(), per_group=LEDGER_PER_GROUP)
        ledger_summary = {"scope": LEDGER_SCOPE, **ledger_summary,
                          "site_usage": runfiles.crossed_usage(read_log(self.hq / "receive.jsonl"))}
        labels = []
        for k in world.case_keys:
            t, i, p = _key_parts(k)
            labels.append({"key": k, "entity_type_label": pack.entity_types[t].label, "entity_id": i,
                           "predicate_label": pack.predicates[p].label})
        after = self.scans[0] if self.scans else None
        providers = self._providers()
        labels_by_task = {p["task"]: p["label"] for p in providers}
        shared_model, _ = sites_stamp(self.routing is not None, providers)
        # X reads the hero perfectly when its narratives went to the lexical handler (the stand-in, a test server
        # replaying it, or the template); a model's extraction is E1's question, not this run's (B1)
        x_by = labels_by_task["extract"] in NO_MODEL_LABELS
        detection = {**self.detection, "by_construction": {**self.detection["by_construction"], "X": x_by,
                                                           "x_reason": X_BY_CONSTRUCTION if x_by else None}}
        scorecard = {
            "kind": "collective_demo_scorecard", "schema_version": SCHEMA_VERSION, "run_id": self.run_id,
            "created_at": utc_clock(), "mode": self.mode,
            "stamps": {"fictional": True, "synthetic": True, "internal_only": True, "illustration": True,
                       "measurement": False, "same_author_pack": pack.same_author_as_code,
                       "approval": self.approval, "sites_simulated": True,
                       "shared_model": shared_model, "secret_mode": "seeded-demo"},
            "company": sc.company, "illustration": sc.illustration,
            "scenario": {"digest": sc.digest, "seed": sc.seed, "weeks": sc.weeks, "first_week": world.weeks[0],
                         "last_week": self.last_week, "as_of": self.as_of, "sites": len(self.site_ids),
                         "countries": len({s.country for s in sc.org.sites.values()}),
                         "records": len(world.records), "hero_records": len(hero_records),
                         "hero_sites": len(hero.sites), "visibility": hero.visibility,
                         "structured_fill": [dict(f) for f in world.structured_fill]},
            "pack": {"id": pack.id, "version": pack.version, "illustrative": pack.illustrative,
                     "config_digest": runfiles.digest(pack.config_hash),
                     "vocabulary_digest": runfiles.digest(pack.vocabulary_hash),
                     "detector_digest": runfiles.digest(pack.detector_hash),
                     "fixtures_digest": runfiles.digest(pack.fixtures_hash), "k": pack.egress.k,
                     "alert_budget_per_week": pack.detectors["alert_budget_per_week"],
                     "window_weeks": pack.detectors["window_weeks"],
                     "min_window_weeks": pack.egress.min_window_weeks},
            "providers": providers,
            "hero": {
                "key": {"entity_type": key.entity_type, "entity_type_label": pack.entity_types[key.entity_type].label,
                        "entity_id": key.entity_id, "predicate": key.predicate,
                        "predicate_label": pack.predicates[key.predicate].label, "key": hero.keys[0]},
                "code_labels": [pack.codes[c].label for c in hero.codes],
                "sites": [{"site": s, "language": lang} for s, lang, _ in hero.sites],
                "window": {"start_week": world.weeks[hero.start_week], "end_week": self.last_week},
                "case_keys": list(world.case_keys), "case_key_labels": labels,
                "detection": detection, "pushdown": self.pushdown,
                "followup": self._followup_doc(labels_by_task["draft"])},
            "decoys": [{k: v for k, v in d.items() if not k.startswith("_")} for d in self.decoys],
            **({"codes_miss": cm.with_gate(self.codes_miss, self.pushdown["gate"]["status"] if self.pushdown
                                           else None)} if self.codes_miss is not None else {}),
            "leakage": {"scope": "text-only", "canaries_planted": len(self.manifest.canaries),
                        "window_chars": SHINGLE_CHARS,
                        "after_pushdown": ({"hit_count": after["hit_count"],
                                            "shingle_overlap_bytes": after["shingle_overlap_bytes"]}
                                           if after is not None else None),
                        "artifact_classes": sorted({c["class"] for s in self.scans for c in s["artifact_classes"]})},
            "ledger": ledger_summary,
            "checks": self._checks() if final else [],
            "code": dict(self.code),
            "timings": {"total_s": round(time.monotonic() - self.t0, 3), **self.timings},
            "content_hash_excludes": list(CONTENT_HASH_EXCLUDES), "content_hash": "0" * 32,
        }
        scorecard["content_hash"] = runfiles.content_hash(scorecard, CONTENT_HASH_EXCLUDES)
        trace = self._trace(final, providers)
        approvals = runfiles.project_entries(self.entries, label=BUILT_AHEAD_LABEL)
        leakage = self._leakage_doc()
        return {"scorecard.json": runfiles.shorten(scorecard), "trace.json": runfiles.shorten(trace),
                "ledger.jsonl": runfiles.shorten(ledger_rows), "leakage.json": runfiles.shorten(leakage),
                "approvals.jsonl": approvals}

    def _trace(self, final: bool, providers: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        sc = self.sc
        _, sites_note = sites_stamp(self.routing is not None,
                                    self._providers() if providers is None else providers)
        roles: dict[str, str] = {}
        for item in sc.items:
            for s in item.site_ids:
                if item.role == "hero":
                    roles[s] = "contributing"
                elif item.role == "sibling":
                    roles.setdefault(s, "sibling")
        relative = _relative(self.out_dir)
        _, events, _ = self.rec.snapshot()
        return {"kind": "collective_demo_trace", "schema_version": SCHEMA_VERSION,
                "meta": {"run_id": self.run_id, "mode": self.mode, "recorded_at": self.rec.recorded_at,
                         "duration_s": self.rec.elapsed() if final else None, "company": sc.company,
                         "fictional": True, "synthetic": True, "internal_only": True,
                         "approval": self.approval,
                         "sites_note": sites_note,
                         "replay_command": f"{PROG} --replay {relative if relative else '<run-dir>'}"},
                "org": {"enterprise": sc.org.enterprise, "sites": [
                    {"site_id": s["site_id"], "display_name": s["display_name"], "country": s["country"],
                     "unit_path": s["unit_path"], "hero_role": roles.get(s["site_id"])}
                    for s in sc.raw["org"]["sites"]]},
                "beats": [{"id": b, "index": n, "title": t, "in_cut": b in scr.CUT_60S}
                          for n, (b, t) in enumerate(scr.BEATS)],
                "cut_60s": list(scr.CUT_60S), "events": events, "status": "complete" if final else "running"}

    def _leakage_doc(self) -> dict[str, Any]:
        by_class = {}
        for c in self.manifest.canaries:
            by_class[c.canary_class] = by_class.get(c.canary_class, 0) + 1
        return {"kind": "collective_demo_leakage", "schema_version": SCHEMA_VERSION, "scope": "text-only",
                "canaries_planted": len(self.manifest.canaries),
                "canaries_by_class": [{"class": c, "n": by_class[c]} for c in sorted(by_class)],
                "window_chars": SHINGLE_CHARS, "scans": list(self.scans),
                "run_files_scanned": list(RUN_FILES_SCANNED),
                "positive_control": self._positive or {"canary_hits": 0, "shingle_overlap_bytes": 0},
                "site_ledger_hygiene": self._hygiene or {"hit_count": 0, "shingle_overlap_bytes": 0},
                "not_covered": list(NOT_COVERED)}

    def screen(self, phase: str, controls: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        docs = self.docs()
        return scr.build_screen(docs, mode=self.mode, phase=phase, controls=controls, run_id=self.run_id,
                                cut_only=self.cut)

    # ------------------------------------------------------------------ finish (step 10)
    def finish(self, *, forbidden: Sequence[str]) -> tuple[dict[str, Any], int]:
        """The six documents, checked; nothing is written here. Returns (docs, exit code 0 or 1)."""
        t0 = time.monotonic()
        if self.service is not None:
            self._capture_ledger()
            self.service.close()
            self.service = None
        if self.store is not None:
            self.store.close()
            self.store = None
        self._positive = self._positive_control()
        provisional = self.docs()
        self._final_scan = self._scan("final", final=True, run_docs=provisional)
        self.scans.append(self._final_scan)
        self.rec.emit("leakage", {"scan": "final", "hit_count": self._final_scan["hit_count"],
                                  "shingle_overlap_bytes": self._final_scan["shingle_overlap_bytes"],
                                  "canaries_planted": len(self.manifest.canaries)})
        self.rec.emit("stage", {"name": "files", "values": [{"name": "files", "value": len(runfiles.RUN_FILES)}]})
        self.rec.emit("done", {})
        self.timings["finish_s"] = round(time.monotonic() - t0, 3)
        docs = self.docs(final=True)
        checks = docs["scorecard.json"]["checks"]
        phase = "complete"
        docs["screen.json"] = scr.build_screen(docs, mode=self.mode, phase=phase, run_id=self.run_id,
                                               cut_only=self.cut)
        self._self_check(docs, forbidden)
        self.finished = True
        return docs, 0 if all(c["ok"] for c in checks) else 1

    def _self_check(self, docs: Mapping[str, Any], forbidden: Sequence[str]) -> None:
        problems = validate_run(docs)
        if problems:
            file, path, keyword = problems[0]
            raise DemoError(f"internal: {file} breaks its schema at {path} ({keyword}); nothing was written")
        artifacts = [Artifact("run_files", f"run/{name}", data=runfiles.serialise(name, docs[name]))
                     for name in runfiles.RUN_FILES]
        report = scan(artifacts, self.manifest, self.world.narratives, self.pack)
        if report["hit_count"] or report["shingle_overlap_bytes"]:
            raise DemoError("the final self-check found a canary or narrative text in a run file; nothing was "
                            "written")
        for name in runfiles.RUN_FILES:
            found = runfiles.portability_problems(runfiles.serialise(name, docs[name]), forbidden=forbidden)
            if found:
                raise DemoError(f"{name} is not portable ({', '.join(found)}); nothing was written")


def _code_stamp() -> dict[str, str]:
    """The code a run was recorded with: the commit, whether the collective code or the demo had uncommitted
    changes, and a digest over both (excluded from the content hash)."""
    paths = sorted((ROOT / "mycelic" / "collective").rglob("*.py")) + sorted(HERE.glob("*.py"))
    dirty = code_dirty(["mycelic/collective", "demo/collective"])
    return {"commit": code_commit(), "dirty": dirty if dirty == "unknown" else ("yes" if dirty else "no"),
            "digest": runfiles.digest(code_hash(paths))}


def _draft_parts(draft: Mapping[str, Any]) -> dict[str, Any]:
    return {"fields": [{"name": k, "value": draft[k]} for k in sorted(draft) if isinstance(draft[k], str)],
            "lists": [{"name": k, "items": [str(x) for x in draft[k]]} for k in sorted(draft)
                      if isinstance(draft[k], list)]}


def _packet_summary(p: Mapping[str, Any]) -> dict[str, Any]:
    return {"site": p["site"], "status": p["status"], "verdict": p["verdict"], "support_bucket": p["support_bucket"],
            "codes": [{"code": c["code"], "n": c["n"]} for c in p["codes"]],
            "co_mentions": [{"entity_type": c["entity_type"], "entity_id": c["entity_id"], "n": c["n"]}
                            for c in p["co_mentions"]]}


# =================================================================================================== pages

def html_json(data: Any) -> str:
    """JSON that is safe inside a <script> element: no '<' survives."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def build_page(screen: Mapping[str, Any] | None, *, fragment: bool = False, console: Path = CONSOLE) -> str:
    """The console page; with ``screen`` the page is static (the screen embedded in place of the marker)."""
    body = console.read_text(encoding="utf-8")
    if screen is not None:
        tag = f'<script id="collective-screen" type="application/json">{html_json(screen)}</script>'
        body = body.replace(SCREEN_MARKER, tag, 1)
    if fragment:
        return body
    head = ""
    m = re.match(r"\s*<title>.*?</title>\s*<style>.*?</style>\s*", body, re.DOTALL)
    if m:
        head, body = m.group(0).strip() + "\n", body[m.end():]
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + head + '</head>\n<body>\n'
            + body + "\n</body>\n</html>\n")


EXPECTED_KINDS = {"scorecard.json": "collective_demo_scorecard", "trace.json": "collective_demo_trace",
                  "leakage.json": "collective_demo_leakage", "screen.json": scr.KIND}


def read_run(directory: Path) -> dict[str, Any]:
    """The six run files of a directory, or :class:`DemoError` with one line: missing, invalid, another schema
    version or not a run of this demo."""
    if not directory.is_dir():
        raise DemoError(f"{directory}: not a run directory")
    try:
        docs = runfiles.read_run_files(directory, runfiles.RUN_FILES)
    except runfiles.RunFileError as exc:
        raise DemoError(f"{directory}: {exc.where} is {exc.problem}") from None
    for name, kind in EXPECTED_KINDS.items():
        doc = docs[name]
        version = doc.get("schema_version") if isinstance(doc, dict) else None
        if version != SCHEMA_VERSION:
            raise DemoError(f"{directory}: run files are schema version {version}; this engine reads "
                            f"{SCHEMA_VERSION}; record a new run with --record")
        if doc.get("kind") != kind:
            raise DemoError(f"{directory}: {name} is not a collective demo run file")
    return docs


# =================================================================================================== the server

class Controller:
    """What ``POST /control`` may do now; updated by the main thread, read by the HTTP threads."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.controls: list[dict[str, Any]] = []
        self.busy = False
        self.complete = False
        self.failed = False
        self.checked = False
        self.approved: set[str] = set()

    def classify(self, action: str, key: Any) -> tuple[int, dict[str, Any]]:
        with self.lock:
            if self.complete:
                return 409, {"ok": False, "error": "the run is complete"}
            if self.failed:
                return 409, {"ok": False, "error": "the run stopped"}
            if action == "check" and self.checked:
                return 200, {"ok": True, "done_before": True}
            if action == "approve" and key in self.approved:
                return 200, {"ok": True, "done_before": True}
            if self.busy or not self.queue.empty():
                return 200, {"ok": False, "busy": True}
            allowed = any(c["enabled"] and c["action"] == action and (action != "approve" or c["key"] == key)
                          for c in self.controls)
            if not allowed:
                return 409, {"ok": False, "error": "not valid in the current beat"}
            self.queue.put((action, key if action == "approve" else None))
            return 200, {"ok": True, "queued": action}


class _QuietHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class ConsoleServer:
    """GET / (console), GET /screen, GET /trace, GET /events (SSE), POST /control on a ThreadingHTTPServer. With
    ``static`` (replay) every answer comes from the recorded files and no control is accepted."""

    def __init__(self, host: str, port: int, *, recorder: Recorder | None = None,
                 static: Mapping[str, Any] | None = None, controller: Controller | None = None,
                 trace: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.recorder, self.static, self.controller, self.trace = recorder, static, controller, trace
        self.stopping = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                pass

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, data: Any, status: int = 200) -> None:
                self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    page = build_page(server.static["screen.json"] if server.static is not None else None)
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/screen":
                    self._json(server.static["screen.json"] if server.static is not None
                               else server.recorder.snapshot()[0])
                elif path == "/trace":
                    self._json(server.static["trace.json"] if server.static is not None else server.trace())
                elif path == "/events":
                    self._events()
                elif path == "/favicon.ico":
                    self._send(204, b"", "image/x-icon")
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0] != "/control":
                    return self._json({"error": "not found"}, 404)
                body = None
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
                except ValueError:
                    body = None
                if not isinstance(body, dict):
                    return self._json({"ok": False, "error": "body must be a JSON object"}, 400)
                if body.get("action") not in scr.ACTIONS:
                    return self._json({"ok": False, "error": "unknown action; expected next, check or approve"}, 400)
                if server.controller is None:
                    return self._json({"ok": False, "error": "replay mode: no engine"}, 409)
                status, answer = server.controller.classify(body["action"], body.get("key"))
                return self._json(answer, status)

            def _events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    last = int(self.headers.get("Last-Event-ID", "-1"))
                except ValueError:
                    last = -1
                try:
                    if server.static is not None:
                        events = server.static["trace.json"]["events"]
                        if last >= len(events) or last < -1:
                            last = -1
                        self._snapshot(server.static["screen.json"])
                        for ev in events[last + 1:]:
                            self._message(ev)
                        self.wfile.flush()
                        while not server.stopping.wait(PING_SECONDS):
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                        return
                    rec = server.recorder
                    screen, events, revision = rec.snapshot()
                    if last >= len(events) or last < -1:
                        last = -1
                    self._snapshot(screen)
                    for ev in events[last + 1:]:
                        self._message(ev)
                        last = ev["seq"]
                    self.wfile.flush()
                    while not server.stopping.is_set():
                        new, rev, screen = rec.wait(last, revision, PING_SECONDS)
                        if rev != revision:
                            revision = rev
                            self._snapshot(screen)
                        for ev in new:
                            self._message(ev)
                            last = ev["seq"]
                        if not new and rev == revision:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def _snapshot(self, screen: Any) -> None:
                self.wfile.write(b"event: snapshot\ndata: " + json.dumps(screen, ensure_ascii=False).encode("utf-8")
                                 + b"\n\n")

            def _message(self, ev: Mapping[str, Any]) -> None:
                self.wfile.write(f"id: {ev['seq']}\ndata: ".encode() + json.dumps(ev, ensure_ascii=False)
                                 .encode("utf-8") + b"\n\n")

        self.httpd = _QuietHTTPServer((host, port), Handler)
        self.url = f"http://{host}:{self.httpd.server_address[1]}/"
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="collective-demo-http", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


# =================================================================================================== signals

def _interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


STOP_SIGNALS = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s is not None]


@contextlib.contextmanager
def interrupts_raise() -> Iterator[None]:
    previous = {s: signal.signal(s, _interrupt) for s in STOP_SIGNALS}
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


@contextlib.contextmanager
def interrupts_ignored() -> Iterator[None]:
    sigs = [signal.SIGINT] + STOP_SIGNALS
    previous = {s: signal.signal(s, signal.SIG_IGN) for s in sigs}
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def wait_or_interrupt(deadline: float | None) -> None:
    while deadline is None or time.monotonic() < deadline:
        time.sleep(0.25)


# =================================================================================================== modes

def _forbidden(workdir: Path, out_dir: Path) -> list[str]:
    names = [str(workdir.resolve()), str(out_dir.resolve()), str(ROOT.resolve()), str(Path.home())]
    for get in (socket.gethostname, getpass.getuser):
        try:
            names.append(get())
        except (OSError, KeyError):
            pass
    return names


def _out_dir(args: argparse.Namespace) -> Path:
    given = args.record if args.record is not None else args.out
    return Path(given) if given else RUNS_DIR / args.run_id


def _empty_or_absent(path: Path, flag: str) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise UsageError(f"{flag} must be absent or an empty directory") from None


def _engine_setup(args: argparse.Namespace, mode: str) -> tuple[Scenario, RoutingConfig | None, Path, Path, bool]:
    sc = load_scenario(args.scenario)
    routing = check_routing(args.routing, check_env=True) if args.routing else None
    out_dir = _out_dir(args)
    _empty_or_absent(out_dir, "the output directory")
    if args.workdir:
        workdir = Path(args.workdir)
        _empty_or_absent(workdir, "--workdir")
        workdir.mkdir(parents=True, exist_ok=True)
        made = True
    else:
        workdir, made = Path(tempfile.mkdtemp(prefix="mycelic-collective-")), True
    return sc, routing, out_dir, workdir, made


def run_record(args: argparse.Namespace) -> int:
    t0 = time.monotonic()
    sc, routing, out_dir, workdir, _ = _engine_setup(args, "record")
    rec = Recorder(run_id=args.run_id, mode="record")
    engine = None
    try:
        with interrupts_raise():
            try:
                if routing is not None:
                    preflight(routing)
                engine = DemoEngine(sc, mode="record", run_id=args.run_id, workdir=workdir, routing=routing,
                                    recorder=rec, out_dir=out_dir)
                rec.beat_start("problem")
                engine.prepare()
                rec.beat_end()
                rec.beat_start("alert")
                engine.emit_alerts()
                rec.beat_end()
                rec.beat_start("check")
                engine.check()
                rec.beat_end()
                rec.beat_start("followup")
                engine.start_followup()
                while engine.awaiting():
                    engine.approve(engine.awaiting()[0])
                rec.beat_end()
                rec.beat_start("real_data")
                rec.beat_end()
                docs, code = engine.finish(forbidden=_forbidden(workdir, out_dir))
                runfiles.write_run_files(out_dir, docs, forbidden=_forbidden(workdir, out_dir))
            except KeyboardInterrupt:
                print("interrupted: nothing written", file=sys.stderr)
                return 130
    finally:
        with interrupts_ignored():
            if engine is not None:
                engine.close()
            if not args.keep_workdir:
                shutil.rmtree(workdir, ignore_errors=True)
    sc_doc = docs["scorecard.json"]
    hero = sc_doc["hero"]
    passed = sum(1 for c in sc_doc["checks"] if c["ok"])
    status = hero["pushdown"]["gate"]["status"] if hero["pushdown"] else "not checked"
    print(f"collective demo: run={args.run_id} hero X rank={hero['detection']['X']['rank']} gate={status} "
          f"checks {passed}/{len(sc_doc['checks'])} -> {out_dir}")
    if "codes_miss" in sc_doc:
        for line in cm.summary_lines(sc_doc["codes_miss"]):
            print(f"codes miss: {line}")
    print(f"record: {time.monotonic() - t0:.1f} s")
    return code


def _controls(engine: DemoEngine, beat: str, phase: str) -> list[dict[str, Any]]:
    if phase in ("complete", "failed", "preparing"):
        return []
    out = []
    if beat == "check":
        out.append({"action": "check", "key": None, "beat": beat, "label": scr.CONTROL_LABELS["check"],
                    "enabled": not engine.checked})
    if beat == "followup":
        for key in engine.awaiting():
            out.append({"action": "approve", "key": key, "beat": beat, "label": scr.CONTROL_LABELS["approve"],
                        "enabled": True})
    ready = {"check": engine.checked, "followup": engine.followups_done()}.get(beat, True)
    out.append({"action": "next", "key": None, "beat": beat, "label": scr.CONTROL_LABELS["next"], "enabled": ready})
    return out


def run_serve(args: argparse.Namespace) -> int:
    if not port_free(args.host, args.port):
        print(f"error: port {args.port} on {args.host} is in use: pass --port <another>", file=sys.stderr)
        return 2
    sc, routing, out_dir, workdir, _ = _engine_setup(args, "live")
    rec = Recorder(run_id=args.run_id, mode="live")
    controller = Controller()
    engine = DemoEngine(sc, mode="live", run_id=args.run_id, workdir=workdir, routing=routing, recorder=rec,
                        out_dir=out_dir, cut=args.cut)
    written: dict[str, Any] = {}
    server = ConsoleServer(args.host, args.port, recorder=rec, controller=controller,
                           trace=lambda: written.get("trace.json") or engine._trace(False))
    deadline = None if args.exit_after is None else time.monotonic() + args.exit_after
    beats = list(scr.CUT_60S) if args.cut else [b for b, _ in scr.BEATS]
    code = 2

    def publish(phase: str) -> None:
        controls = _controls(engine, rec.beat or beats[0], phase)
        with controller.lock:
            controller.controls = controls
            controller.checked = engine.checked
            controller.approved = {f.key for f in engine.followups if f.approval is not None}
        rec.publish(engine.screen(phase, controls) if engine.detection is not None
                    else scr.build_screen(None, mode="live", phase=phase, controls=controls, run_id=args.run_id,
                                          cut_only=args.cut))

    try:
        with interrupts_raise():
            try:
                server.start()
                print(f"collective demo console: {server.url}  (live; advance from the console)")
                if not args.no_browser:
                    webbrowser.open(server.url)
                if routing is not None:
                    preflight(routing)
                rec.beat_start("problem")
                publish("preparing")
                engine.prepare()
                publish("ready")
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        return 0 if engine.finished else 2
                    try:
                        action, key = controller.queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    with controller.lock:
                        controller.busy = True
                    publish("running")
                    try:
                        if action == "check":
                            engine.check()
                        elif action == "approve":
                            engine.approve(key)
                        else:
                            index = beats.index(rec.beat)
                            if args.cut and rec.beat == "check":
                                # the cut does not show the follow-up beat: its follow-ups run here, scripted
                                engine.start_followup()
                                while engine.awaiting():
                                    engine.approve(engine.awaiting()[0])
                            if index + 1 < len(beats):
                                rec.beat_end()
                                rec.beat_start(beats[index + 1])
                                if beats[index + 1] == "alert":
                                    engine.emit_alerts()
                                if beats[index + 1] == "followup":
                                    engine.start_followup()
                            else:
                                rec.beat_end()
                                docs, code = engine.finish(forbidden=_forbidden(workdir, out_dir))
                                runfiles.write_run_files(out_dir, docs, forbidden=_forbidden(workdir, out_dir))
                                written.update(docs)
                                with controller.lock:
                                    controller.complete = True
                                rec.publish(docs["screen.json"])
                                print(f"run files written to {out_dir}")
                                break
                    finally:
                        with controller.lock:
                            controller.busy = False
                    publish("ready")
                wait_or_interrupt(deadline)
                return code
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - a live run shows every failure on the console and stays up
                failure = exc if isinstance(exc, DemoError) else DemoError(
                    f"the live run stopped on an unexpected {type(exc).__name__}; no run files were written")
                with controller.lock:
                    controller.failed = True
                rec.emit("error", {"message": failure.message, "hint": failure.hint or _replay_hint()})
                try:
                    publish("failed")
                except Exception:  # noqa: BLE001 - the screen of the run so far may be what failed
                    rec.publish(scr.build_screen(None, mode="live", phase="failed", run_id=args.run_id))
                print(f"error: {failure.message}", file=sys.stderr)
                print(failure.hint or _replay_hint(), file=sys.stderr)
                print("the console stays up with the error: Ctrl-C to stop", file=sys.stderr)
                wait_or_interrupt(deadline)
                return 2
    except KeyboardInterrupt:
        return code if engine.finished else 130
    finally:
        with interrupts_ignored():
            server.stop()
            engine.close()
            if not args.keep_workdir:
                shutil.rmtree(workdir, ignore_errors=True)


def run_replay(args: argparse.Namespace) -> int:
    directory = Path(args.replay) if args.replay else _committed_dir()
    if directory is None:
        raise DemoError("there is not exactly one recorded run under demo/collective/recorded; pass its directory")
    docs = read_run(directory)
    docs = {**docs, "screen.json": scr.presented(docs["screen.json"], "recorded")}
    if not port_free(args.host, args.port):
        print(f"error: port {args.port} on {args.host} is in use: pass --port <another>", file=sys.stderr)
        return 2
    server = ConsoleServer(args.host, args.port, static=docs)
    server.start()
    print(f"replaying {directory} (RECORDED, {len(docs['trace.json']['events'])} events) at {server.url}")
    if not args.no_browser:
        webbrowser.open(server.url)
    deadline = None if args.exit_after is None else time.monotonic() + args.exit_after
    with interrupts_raise():
        try:
            wait_or_interrupt(deadline)
        except KeyboardInterrupt:
            pass
        finally:
            with interrupts_ignored():
                server.stop()
    return 0


def run_export(args: argparse.Namespace) -> int:
    directory = Path(args.run) if args.run else _committed_dir()
    if directory is None:
        raise DemoError("there is not exactly one recorded run under demo/collective/recorded; pass --run")
    docs = read_run(directory)
    page = build_page(scr.presented(docs["screen.json"], "recorded")).encode("utf-8")
    if len(page) > MAX_PAGE_BYTES:
        raise DemoError(f"the page would be {len(page)} bytes, over the {MAX_PAGE_BYTES} byte limit")
    target = Path(args.export)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(page)
    print(f"wrote {target} ({len(page) // 1024} KiB, standalone page of run {docs['scorecard.json']['run_id']})")
    return 0


def run_dry(args: argparse.Namespace) -> int:
    dry = DryRun(CLI)
    if args.record is not None or args.serve:
        if Path(args.scenario).exists():
            load_scenario(args.scenario)
        else:
            dry.need(f"scenario {args.scenario}")
        if args.routing:
            if Path(args.routing).is_file():
                config = check_routing(args.routing, check_env=False)
                for task in (TASK_NAME, JUDGE_TASK, DRAFT_TASK):
                    for name in routed_names(config, task):
                        dry.need(f"network {config.endpoints[name].host_label} (endpoint {name}, a running server)")
            else:
                dry.need(f"routing file {args.routing}")
        out_dir = _out_dir(args)
        _empty_or_absent(out_dir, "the output directory")
        if args.serve:
            dry.need(f"a free port {args.host}:{args.port} for the console")
        for name in runfiles.RUN_FILES:
            dry.write(str(out_dir / name))
        dry.write("a temporary work directory (removed at exit unless --keep-workdir)")
    elif args.replay is not None or args.export is not None:
        given = args.replay if args.replay is not None else args.run
        directory = Path(given) if given else _committed_dir()
        if directory is None or not directory.exists():
            dry.need(f"recorded run directory {given or RECORDED}")
        else:
            read_run(directory)
        if args.replay is not None:
            dry.need(f"a free port {args.host}:{args.port} for the console")
        else:
            dry.write(str(args.export))
    return dry.emit()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="The collective demo: a fictional multi-site device maker "
                                                       "(synthetic data; internal use only).")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", nargs="?", const="", metavar="DIR",
                      help="headless full loop; DIR defaults to runs/collective/<run-id>")
    mode.add_argument("--serve", action="store_true", help="the live console; run files are written at the end")
    mode.add_argument("--replay", nargs="?", const="", metavar="DIR",
                      help="serve a recorded run with no engine (default: the committed run)")
    mode.add_argument("--export", metavar="PAGE", help="write a standalone page of a recorded run")
    p.add_argument("--out", metavar="DIR", help="--serve: where the run files go (default runs/collective/<run-id>)")
    p.add_argument("--cut", action="store_true",
                   help="--serve: present the 60-second cut live; the follow-ups run with scripted approval")
    p.add_argument("--run", metavar="DIR", help="--export: the recorded run (default: the committed run)")
    p.add_argument("--scenario", default=str(DEFAULT_SCENARIO), help="the scenario file")
    p.add_argument("--routing", metavar="FILE", help="--record/--serve: a routing file for a real local model")
    p.add_argument("--run-id", default=None, help="default: the name of the run directory when it is a valid run id, "
                                                  "else collective- plus random hex")
    p.add_argument("--workdir", metavar="DIR", help="default: a temporary directory, removed at exit")
    p.add_argument("--keep-workdir", action="store_true")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=None, help=f"default {SERVE_PORT} (serve) or {REPLAY_PORT} (replay)")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--exit-after", type=float, default=None, metavar="SECONDS")
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # every failed attempt is in the run's ledger, and a run that needed a fallback stops with one line naming the
    # endpoint and the error kinds; the runtime's per-attempt warnings would bury that line
    logging.getLogger("mycelic.collective.inference").addHandler(logging.NullHandler())
    try:
        given = args.record if args.record else args.out
        if args.run_id is None and given and RUN_ID_RE.fullmatch(Path(given).name) is not None:
            args.run_id = Path(given).name            # the run directory's name is the run id
        if args.run_id is None:
            args.run_id = "collective-" + secrets.token_hex(6)
        elif RUN_ID_RE.fullmatch(args.run_id) is None:
            raise UsageError("--run-id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}") from None
        if args.port is None:
            args.port = REPLAY_PORT if args.replay is not None else SERVE_PORT
        if args.routing and (args.replay is not None or args.export is not None):
            raise UsageError("--routing goes with --record or --serve") from None
        if args.out and not args.serve:
            raise UsageError("--out goes with --serve") from None
        if args.cut and not args.serve:
            raise UsageError("--cut goes with --serve") from None
        if args.run and args.export is None:
            raise UsageError("--run goes with --export") from None
        if args.dry_run:
            return run_dry(args)
        if args.record is not None:
            return run_record(args)
        if args.serve:
            return run_serve(args)
        if args.replay is not None:
            return run_replay(args)
        return run_export(args)
    except DemoError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(exc.hint, file=sys.stderr)
        return 2
    except (UsageError, ScenarioError, ConfigError, runfiles.RunFileError, LeakageError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, StrictJsonError) as exc:
        print(f"error: cannot read or write a file ({exc.__class__.__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
