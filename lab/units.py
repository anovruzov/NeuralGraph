"""One unit: an experiment harness run as a subprocess against one model server, and the record of what happened.

Inside a shard's output directory ``OUT``, a unit writes::

    routing/<unit>.json                      the routing file the harness reads (one endpoint, ``lab``; E1: the
                                             model's key, pinned by the preregistration)
    routing/<unit>/sites/<site>.json         E2: one routing file per site of the preregistered world, and
    routing/<unit>/central.json              the central comparator's routing file
    units/<unit>/{stdout.log,stderr.log}     the harness output, each cut to its last 64 KiB; never printed
    runs/<experiment>/<run id>/...           the allowlisted harness outputs, copied from the scratch directory
    work/<unit>/                             the harness scratch directory, deleted when the unit ends
    work/<unit>/hosted-routing/<name>.json   a routing file naming the hosted provider, the one the harness reads;
                                             its ``routing/`` twin is redacted (``lab.hosted.redact``)

and returns the record the shard writes to ``units/<unit>/unit.json``. The model server is either the shard's
started model server (a :class:`Serving` handle: its loopback URL, alias and the provenance evidence) or, for fake
entries and the ``--provider fake`` override, the collective's ``FakeOpenAIServer`` inside this process, answered by
:class:`~lab.responder.Responder` (``server.FakeServer``). Either way the routing file names it as an
``openai_compat`` endpoint (E3 refuses fake routing by design), so the harness talks HTTP to it the same way. While
a model server serves the unit, the harness is watched: if the server process exits, the harness group is stopped,
and a unit whose server exited while it ran (seen by the watch or right after the harness ended) is ``invalid``
(``server exited with ...``; SIGKILL adds the out-of-memory hint). The model-free units (kind ``none``: X1 and
openFDA) get no server and no routing. A unit that calls the hosted provider gets a :class:`lab.hosted.Handle` from
the shard: an E1 unit of a hosted model starts no server, its routing names the host (``lab.hosted.endpoint``,
boundary ``external``), and the harness reads the scratch routing, the only file that holds the base URL; an E2
unit's central comparator is the hosted model the same way, while its sites judge on the shard's server. Their
harness environment is the handle's (``lab.hosted.unit_environ``): the allowlist plus the unit's ``env``, so the
stripped key and never the base URL.

Adapters (``ADAPTERS``; every harness writes under ``work/<unit>/<experiment>/<run id>``, its kind being the
experiment): ``e3`` runs ``experiments.e3_latency`` (boundary ``central``) and keeps ``e3.json`` and ``ledger.jsonl``;
``g0`` runs ``experiments.g0_canary --mode routing`` (boundary ``any-simulated``, both routed tasks on the endpoint)
and keeps ``leakage.json`` and ``edge/site-*.ledger.jsonl``; ``sim`` runs ``lab.sim`` (the same routing as G0, with
``--budget-seconds`` the unit's whole seconds less :data:`SIM_BUDGET_MARGIN_S`, at least 1) and keeps
``scorecard.json``, ``progress.json``, ``labels.json`` and ``edge/site-*.ledger.jsonl``; ``e1`` runs
``experiments.e1_extract run`` (the preregistered prereg and labels, one repeat of one model) and keeps ``run.json``,
``predictions.jsonl`` and ``ledger.jsonl``; ``e2`` runs ``experiments.e2_pushdown run`` (the preregistered X1 prereg,
site routing at ``any-simulated`` and a central endpoint at ``central`` on the same server: central is the model
itself; ``--central-context-tokens``, the plan's ``central_context_tokens``, only when the installed harness takes it,
:data:`E2_CENTRAL_CONTEXT`) and keeps ``e2.json``, ``central.ledger.jsonl`` and
``work/seed-*/edge/site-*.ledger.jsonl``; ``x1`` runs ``evaluate.harness run`` and keeps ``scorecard.json`` and
``labels.json``; ``openfda`` is ``lab.openfda``'s steps (caches' manifests, the replay's prereg, signals and score,
and the sheets). Nothing else is collected: no ``private/``, no other ``work/``, no SQLite file, no ``pages/``, no
``hq*`` or ``followup/`` directory. E1, E2 and X1 need the preregistration (:mod:`lab.prereg`): without it they fail
with :data:`~lab.notes.PREREG_MISSING` and nothing starts. An E2 unit behind a model server is first projected
(:func:`e2_projection`): the rehearsal's call counts times the warm-up's latencies, against
:data:`lab.sim.PROJECTION_SHARE` of the unit's time; above it the unit is ``skipped`` with the projection recorded and
no harness started.

Status, from :func:`harness_status`, then the participation check, then G0's model path:

* ``timed_out`` whenever the wait timed out; ``interrupted`` for exit 130 or SIGINT; ``failed`` for another signal,
  for exit 2 (the harness refused its configuration) and for any exit the adapter does not expect;
* E3: exit 0 with an ``e3.json`` of kind ``e3`` is ``ok``, anything else ``failed``;
* E1: exit 0 with a ``run.json`` of kind ``e1_run`` that says ``complete: true`` is ``ok``, else ``failed``;
* E2: exit 0 with an ``e2.json`` of kind ``e2_pushdown`` is ``ok``; exit 1 (an uncaught error, the ledgers kept) is
  ``failed`` with :data:`~lab.notes.E2_ABORTED`;
* X1: exit 0 with a ``scorecard.json`` of kind ``x1_scorecard`` is ``ok``;
* G0: exit 0 with ``passed: true`` is ``ok``, exit 1 with ``passed: false`` is ``result_fail`` (a valid FAIL
  verdict), any other pairing of exit 0/1 and ``leakage.json`` (missing, unreadable, contradicting) is ``failed``;
* sim: exit 0 or 1 with a ``scorecard.json`` of kind ``lab_sim_scorecard``, else ``failed``. A ``skipped_projection``
  scorecard with exit 1 is ``skipped`` (:data:`~lab.notes.SIM_PROJECTED`, the projected and allowed minutes); a
  ``complete`` one must exit 0 exactly when its scan and its extraction both passed, and is ``result_fail`` when the
  scan failed (raw text crossed), else ``ok`` (the participation check below judges the extraction); any other
  pairing is ``failed``;
* participation: an ``ok`` or ``result_fail`` unit with a model is ``invalid`` unless the model answered. Per task
  in the ledgers, ``attempted`` counts calls (rows with attempt 0 or 1; a repair or escalation row is part of the
  same call) and ``ok`` the rows that succeeded. A required task (the E3 workload tasks; G0's, E1's and the sim's
  ``extract_claims``; E2's ``judge_record`` and ``judge_candidate_raw``) with no call, a sim whose scorecard says
  its extraction did not pass (lexical fallback share above 0.05, :data:`~lab.notes.SIM_LOW_PARTICIPATION`), any
  task with an ok share below 0.95, or an E3 run with more than 5% failed measured requests makes the unit invalid,
  checked in that order. A harness that exits 0 although
  the model never answered (E3 counts its failures, G0 and the sim fall back to the lexical extractor) can therefore
  never be ``ok``.
* G0's model path, last (:func:`g0_status`): an ``ok`` or ``result_fail`` G0 unit whose ``leakage.json`` lists
  ``model_path`` problems while nothing leaked is ``invalid`` (:data:`~lab.notes.G0_MODEL_PATH`); a harness without
  ``model_path`` is judged as before.

The measurement class (:func:`measurement_class`) says what the numbers are: ``plumbing`` when a fake answered (a
fake manifest entry, the ``--provider fake`` override, or any ledger row carrying the fake-server marker),
``no-model`` for a model-free unit; otherwise ``model`` only when every condition of :func:`model_checks` holds, in
this order: the model file was verified by the hub or the lock and its record is the one prepare used
(``model_verified``); the server archive likewise (``server_verified``; trusted on first use adds the flag
``server_first_use``); every download connection reached the host it named and none was private
(``download_hosts``); the server reported the verified file as its model (``model_path``); every ledger row went to
the started server (``ledger_host``) and every answer named the alias (``model_served``); the harness counted the
run as a measurement (``harness_measurement``, E1, E2, E3 and the sim: its result says ``measurement: true``, in the
sim's and E2's ``stamps``; :func:`harness_verdict`); and the unit ran to a valid result (``participation``).
Otherwise ``unverified``, naming the first condition that failed (``no_evidence`` without a model server). G0's own
``models_fake`` is ignored: in routing mode it is false even against the fake HTTP server. A hosted E1 unit (kind
``hosted``) is judged on :data:`HOSTED_CHECK_KEYS` instead (:func:`hosted_checks`: every ledger row went to the
configured host, the harness's verdict, the participation) and is at best ``hosted-api`` (``hosted_verified``): a result
of the configured host, never a measurement on this runner. An E2 unit with a hosted central comparator stays a
``model`` candidate (the local model is what it measures): ``ledger_host`` and ``model_served`` judge its site rows,
and ``ledger_host`` also needs every central row to have gone to the configured host.

Summaries and the aggregate report never trust a record's ``measurement_class`` alone: :func:`display_class` re-reads
the record and its shard's provenance and puts each unit in one of :data:`DISPLAY_CLASSES`; ``model`` needs every
fact that admits a measurement to hold at once, and so does ``hosted-api``. Hosted units' notes (:func:`unit_notes`)
drop ``runner_hardware`` and add ``hosted_api`` and ``hosted_raw``; a hosted central comparator adds
``central_hosted`` and ``hosted_raw``; a plumbing unit with a hosted role says ``plumbing_hosted``, not ``plumbing``
(its hosted calls went to the configured host). The record's ``hosted`` block names the key, role, model id, scheme,
host, transport, the shard's share and the calls before and after the unit (never the base URL).

The harness runs with an allowlisted environment (:data:`ENV_ALLOWLIST` plus ``PYTHONUNBUFFERED=1``, plus the
unit's ``env`` only through a hosted handle: the hosted key for a unit that calls the host, and nothing else), in its
own session; on a timeout, a watch that fired or an exception (a shard interrupt included) the whole process group gets
SIGTERM, then SIGKILL after 10 s, and after every exit the group is SIGKILLed again to remove stragglers.
"""
from __future__ import annotations

import argparse
import math
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
from mycelic.collective.experiments import e2_pushdown
from mycelic.collective.experiments.e2_pushdown import CENTRAL_TASKS
from mycelic.collective.experiments.e3_latency import WORKLOADS
from mycelic.collective.inference.client import is_private_host
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import ConfigError, load_routing
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs.loader import load_pack

from . import ROOT
from . import hosted as lab_hosted
from . import prereg as lab_prereg
from .notes import (E2_ABORTED, E3_FAILURES, FAKE_SERVER_NOTE, G0_MODEL_PATH, HARNESS_INTERRUPTED, HARNESS_USAGE,
                    KILLED_BY_SIGNAL, LAB_ROUTING, LOW_PARTICIPATION, NO_MODEL_CALLS, PREREG_MISSING,
                    RESULT_CONTRADICTS_EXIT, RESULT_MISSING, SHARD_INTERRUPTED, SIM_LOW_PARTICIPATION, SIM_PROJECTED,
                    TIMED_OUT, UNEXPECTED_EXIT)
from .server import EXIT_GRACE_S, LOG_LINE_CAP, WATCH_INTERVAL_S, FakeServer, exit_reason, printable
from .sim import PROJECTION_SHARE, suggested_minutes

ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY",
                 "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
STATUSES = ("ok", "result_fail", "invalid", "failed", "timed_out", "interrupted", "skipped")
FAILING = ("invalid", "failed", "timed_out", "interrupted")
MIN_MODEL_OK_SHARE = 0.95
MAX_E3_FAILED_SHARE = 0.05
MAX_SIM_FALLBACK_SHARE = 0.05
SIM_BUDGET_MARGIN_S = 30
LOG_CAP_BYTES = 65536
TERM_GRACE_S = 10
ENDPOINT = "lab"
CENTRAL_ENDPOINT = "lab-central"
CENTRAL_DEADLINE_S = 3600
E2_DEADLINE_SECONDS = 3600
PREREG_EXPERIMENTS = ("e1", "e2", "x1")
WARMUP_LEDGER = "server/warmup.ledger.jsonl"
FLAG_NAME_RE = re.compile(r"[a-z][a-z0-9-]*", re.ASCII)
CHECK_KEYS = ("model_verified", "server_verified", "download_hosts", "model_path", "ledger_host", "model_served",
              "harness_measurement", "participation")
HOSTED_CHECK_KEYS = ("hosted_host", "harness_measurement", "participation")
DISPLAY_CLASSES = ("model", "hosted-api", "unverified", "plumbing", "no-model", "no-result")
HOSTED_ROUTING = "hosted-routing"
MODEL_VERIFIED_BY = ("lock", "hf-api")
SERVER_VERIFIED_BY = ("lock", "github-api", "first-use")


def _e2_central_context() -> bool:
    """Whether the installed pushdown harness's ``run`` takes ``--central-context-tokens``, read from its own parser."""
    for action in e2_pushdown._parser()._actions:
        if isinstance(action, argparse._SubParsersAction) and "run" in action.choices:
            return any("--central-context-tokens" in a.option_strings for a in action.choices["run"]._actions)
    return False


E2_CENTRAL_CONTEXT = _e2_central_context()


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
    subcommand: str = ""            # the harness subcommand, the first argument after the module


E2_SITE_LEDGERS = "work/seed-*/edge/site-*.ledger.jsonl"
OPENFDA_COLLECT = ("cache/events/manifest.json", "cache/recalls/manifest.json", "runs/replay/prereg/prereg.json",
                   "runs/replay/signals/signals.json", "runs/replay/signals/phase1.json",
                   "runs/replay/score/replay.json", "runs/n1/n1/sample.json", "runs/n1/n1/sheet.csv",
                   "runs/n1/n1/sheet.jsonl", "runs/e1/e1/prepare.json", "runs/e1/e1/records.jsonl",
                   "runs/e1/e1/sheet.csv")
ADAPTERS = {
    "e3": Adapter("e3", "mycelic.collective.experiments.e3_latency", (), "central", 3600, "e3.json",
                  ("e3.json", "ledger.jsonl"), ("ledger.jsonl",)),
    "g0": Adapter("g0", "mycelic.collective.experiments.g0_canary", (TASK_NAME, JUDGE_TASK), "any-simulated", 900,
                  "leakage.json", ("leakage.json", "edge/site-*.ledger.jsonl"), ("edge/site-*.ledger.jsonl",)),
    "sim": Adapter("sim", "lab.sim", (TASK_NAME, JUDGE_TASK), "any-simulated", 900, "scorecard.json",
                   ("scorecard.json", "progress.json", "labels.json", "edge/site-*.ledger.jsonl"),
                   ("edge/site-*.ledger.jsonl",)),
    "e1": Adapter("e1", "mycelic.collective.experiments.e1_extract", (), "", 900, "run.json",
                  ("run.json", "predictions.jsonl", "ledger.jsonl"), ("ledger.jsonl",), "run"),
    "e2": Adapter("e2", "mycelic.collective.experiments.e2_pushdown", (JUDGE_TASK,), "any-simulated", 900, "e2.json",
                  ("e2.json", "central.ledger.jsonl", E2_SITE_LEDGERS), ("central.ledger.jsonl", E2_SITE_LEDGERS),
                  "run"),
    "x1": Adapter("x1", "mycelic.collective.evaluate.harness", (), "", 0, "scorecard.json",
                  ("scorecard.json", "labels.json"), (), "run"),
    "openfda": Adapter("openfda", "lab.openfda", (), "", 0, "runs/replay/score/replay.json", OPENFDA_COLLECT, ()),
}


# --------------------------------------------------------------------------------------------------- pure parts

def required_tasks(unit: Mapping[str, Any]) -> list[str]:
    if unit["experiment"] == "e3":
        return [WORKLOADS[w][0].name for w in unit["params"]["workloads"]]
    if unit["experiment"] == "e2":
        return [JUDGE_TASK, CENTRAL_TASKS[0]]
    return [TASK_NAME]


def harness_dir(unit: Mapping[str, Any], out: Path) -> Path:
    """Where the harness writes: E3 and the sim under ``--runs-dir`` (``<kind>/<run id>``), G0 at ``--out``; the
    same shape."""
    return Path(out) / "work" / unit["unit"] / unit["experiment"] / unit["run_id"]


def flag(name: str, value: Any) -> str:
    if FLAG_NAME_RE.fullmatch(name) is None:
        raise ValueError("flag names match [a-z][a-z0-9-]*") from None
    return f"--{name}={value}"


def build_argv(unit: Mapping[str, Any], out: Path, routing_path: Path, server_note: str = FAKE_SERVER_NOTE,
               budget_s: int | None = None, prereg: Path | None = None,
               central_routing: Path | None = None) -> list[str]:
    """``[python, -m, <module>, (<subcommand>,) --flag=value ...]``: every option in one element, never a bare value,
    and never the harness option that accepts a dirty tree. A sim unit needs ``budget_s`` (its ``--budget-seconds``);
    E1, E2 and X1 need ``prereg``, the directory holding the plan and its ``prereg/``. ``central_routing`` replaces
    E2's ``routing/<unit>/central.json`` (the scratch routing of a hosted central comparator)."""
    p = unit["params"]
    runs_dir = Path(out) / "work" / unit["unit"]
    adapter = ADAPTERS[unit["experiment"]]
    if unit["experiment"] in PREREG_EXPERIMENTS and prereg is None:
        raise ValueError("E1, E2 and X1 units need the preregistration directory") from None
    if unit["experiment"] == "e1":
        flags = [("prereg", prereg / "prereg" / "e1" / "prereg" / "prereg.json"),
                 ("labels", prereg / "prereg" / "e1" / "labels.jsonl"), ("routing", routing_path),
                 ("endpoint", unit["model"]), ("repeat", p["repeat"]), ("run-id", unit["run_id"]),
                 ("runs-dir", runs_dir)]
    elif unit["experiment"] == "e2":
        flags = [("x1-prereg", prereg / "prereg" / "x1" / "e2" / "prereg.json"), ("plant", ROOT / p["plant_path"]),
                 ("run-id", unit["run_id"]), ("top-n", p["top_n"]), ("min-candidates", p["min_candidates"]),
                 ("site-routing", Path(out) / "routing" / unit["unit"] / "sites"),
                 ("central-routing", central_routing or Path(out) / "routing" / unit["unit"] / "central.json"),
                 ("allow-external-raw", "synthetic"), ("data-label", "synthetic"),
                 ("deadline-seconds", E2_DEADLINE_SECONDS), ("bootstrap-b", p["bootstrap_b"]),
                 ("bootstrap-seed", p["bootstrap_seed"]), ("runs-dir", runs_dir)]
        if E2_CENTRAL_CONTEXT:
            flags.append(("central-context-tokens", p["central_context_tokens"]))
    elif unit["experiment"] == "x1":
        flags = [("prereg", prereg / "prereg" / "x1" / "x1" / "prereg.json"), ("plant", ROOT / p["plant_path"]),
                 ("seeds", ",".join(str(s) for s in sorted(p["seeds"]))), ("run-id", unit["run_id"]),
                 ("runs-dir", runs_dir)]
    elif unit["experiment"] == "sim":
        if budget_s is None:
            raise ValueError("a sim unit needs its budget in seconds") from None
        flags = [("plant", p["plant"]), ("seed", p["seed"]), ("weeks", p["weeks"]), ("eval-from", p["eval_from"]),
                 ("eval-to", p["eval_to"]), ("grace-weeks", p["grace_weeks"]), ("tie-salt", p["tie_salt"]),
                 ("top-n", p["top_n"]), ("bootstrap-b", p["bootstrap_b"]), ("bootstrap-seed", p["bootstrap_seed"]),
                 ("routing", routing_path), ("run-id", unit["run_id"]), ("runs-dir", Path(out) / "work" / unit["unit"]),
                 ("budget-seconds", budget_s)]
    elif unit["experiment"] == "e3":
        flags = [("routing", routing_path), ("endpoint", ENDPOINT), ("run-id", unit["run_id"]),
                 ("runs-dir", Path(out) / "work" / unit["unit"]), ("boundary", "central"),
                 ("concurrency", ",".join(str(c) for c in p["concurrency"])), ("requests", p["requests"]),
                 ("warmup", p["warmup"]), ("workloads", ",".join(p["workloads"])), ("seed", p["seed"]),
                 ("server-note", server_note)]
    else:
        flags = [("pack", p["pack"]), ("records", p["records"]), ("seed", p["seed"]), ("out", harness_dir(unit, out)),
                 ("mode", "routing"), ("routing", routing_path)]
    head = [adapter.subcommand] if adapter.subcommand else []
    return [sys.executable, "-m", adapter.module, *head, *(flag(n, v) for n, v in flags)]


def _endpoint(entry: Mapping[str, Any], base_url: str, boundary: str, deadline_s: int) -> dict[str, Any]:
    return {"provider": "openai_compat", "boundary": boundary, "base_url": base_url, "model": entry["alias"],
            "response_format": entry["response_format"], "transport_schema": entry["transport_schema"],
            "connect_timeout_s": 5, "deadline_s": deadline_s, "max_retries": 1}


def routing_doc(unit: Mapping[str, Any], entry: Mapping[str, Any], base_url: str) -> dict[str, Any]:
    adapter = ADAPTERS[unit["experiment"]]
    endpoint = _endpoint(entry, base_url, adapter.boundary, adapter.deadline_s)
    return {"schema_version": 1, "endpoints": {ENDPOINT: endpoint},
            "routes": {task: {"endpoint": ENDPOINT} for task in adapter.routed_tasks}}


def e2_routing_docs(entry: Mapping[str, Any], base_url: str, site_ids: list[str],
                    hosted: lab_hosted.Handle | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """({site id: the site's routing}, the central routing): every site judges with the model inside
    ``any-simulated``; the central comparator is the same server at ``central``, or the hosted model of ``hosted``
    (``lab.hosted.endpoint``, boundary ``external``)."""
    adapter = ADAPTERS["e2"]
    site = {"schema_version": 1, "endpoints": {ENDPOINT: _endpoint(entry, base_url, adapter.boundary,
                                                                   adapter.deadline_s)},
            "routes": {JUDGE_TASK: {"endpoint": ENDPOINT}}}
    central_endpoint = (lab_hosted.endpoint(hosted.entry, hosted.base_url) if hosted is not None
                        else _endpoint(entry, base_url, "central", CENTRAL_DEADLINE_S))
    central = {"schema_version": 1, "endpoints": {CENTRAL_ENDPOINT: central_endpoint},
               "routes": {task: {"endpoint": CENTRAL_ENDPOINT} for task in CENTRAL_TASKS}}
    return {sid: site for sid in site_ids}, central


def write_routing(unit: Mapping[str, Any], plan: Mapping[str, Any], entry: Mapping[str, Any], base_url: str,
                  out: Path, prereg: Any, hosted: lab_hosted.Handle | None = None
                  ) -> tuple[str | None, dict[str, str] | None, bool, dict[str, Path]]:
    """Write and validate the unit's routing: (routing_sha256, routing_files, valid, harness_paths). E1's routing
    comes from the one builder the preregistration also uses; E2's site list from its preregistered world. A routing
    document that names a hosted endpoint (``hosted``: an E1 hosted model, or E2's hosted central comparator) is
    written whole only to the scratch path ``work/<unit>/hosted-routing/<name>.json`` (validated there, and handed to
    the harness through ``harness_paths``: ``routing`` or ``central_routing``); the usual path under ``routing/``
    gets ``lab.hosted.redact``'s copy, whose hash is the one recorded."""
    out = Path(out)
    files: list[tuple[Path, dict[str, Any], tuple[str, ...], str]] = []
    if unit["experiment"] == "e1":
        files.append((out / "routing" / f"{unit['unit']}.json",
                      lab_prereg.e1_routing_doc(plan["models"], [unit["model"]],
                                                hosted.base_url if hosted is not None else base_url), (), "routing"))
    elif unit["experiment"] == "e2":
        doc = strict_load((prereg.dir / prereg.manifest["e2"]["prereg"]).read_bytes())
        sites, central = e2_routing_docs(entry, base_url, list(doc["world"]["site_ids"]), hosted)
        base = out / "routing" / unit["unit"]
        files += [(base / "sites" / f"{sid}.json", body, (JUDGE_TASK,), "") for sid, body in sites.items()]
        files.append((base / "central.json", central, CENTRAL_TASKS, "central_routing"))
    else:
        files.append((out / "routing" / f"{unit['unit']}.json", routing_doc(unit, entry, base_url),
                      ADAPTERS[unit["experiment"]].routed_tasks, ""))
    hashes: dict[str, str] = {}
    harness_paths: dict[str, Path] = {}
    valid = True
    for path, body, tasks, name in files:
        read = path
        if lab_hosted.is_hosted_doc(body):
            read = out / "work" / unit["unit"] / HOSTED_ROUTING / path.name
            read.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(read, body)
            harness_paths[name] = read
            body = lab_hosted.redact(body)
        path.parent.mkdir(parents=True, exist_ok=True)
        hashes[path.relative_to(out).as_posix()] = write_json_atomic(path, body)
        try:
            load_routing(read, tasks=tasks, check_env=False)
        except ConfigError:
            valid = False
    main = files[-1][0].relative_to(out).as_posix()
    return (hashes[main], (dict(sorted(hashes.items())) if unit["experiment"] == "e2" else None), valid,
            harness_paths)


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
    if experiment == "e2" and exit_code == 1:
        return "failed", E2_ABORTED
    if experiment in ("e1", "e2", "x1"):
        if exit_code != 0:
            return "failed", UNEXPECTED_EXIT
        kind = result.get("kind") if isinstance(result, dict) else None
        good = {"e1": kind == "e1_run" and result.get("complete") is True, "e2": kind == "e2_pushdown",
                "x1": kind == "x1_scorecard"}[experiment]
        return ("ok", None) if good else ("failed", RESULT_MISSING)
    if exit_code not in (0, 1):
        return "failed", UNEXPECTED_EXIT
    if experiment == "sim":
        return _sim_status(exit_code, result)
    passed = result.get("passed") if isinstance(result, dict) and result.get("kind") == "g0_leakage" else None
    if not isinstance(passed, bool):
        return "failed", RESULT_MISSING
    if exit_code == 0 and passed:
        return "ok", None
    if exit_code == 1 and not passed:
        return "result_fail", None
    return "failed", RESULT_CONTRADICTS_EXIT


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sim_status(exit_code: int, result: Any) -> tuple[str, str | None]:
    """The sim's reading of exit 0 or 1 and its scorecard (see the module docstring)."""
    if not isinstance(result, dict) or result.get("kind") != "lab_sim_scorecard":
        return "failed", RESULT_MISSING
    if result.get("status") == "skipped_projection":
        projected = result.get("projection")
        values = [projected.get(k) if isinstance(projected, dict) else None
                  for k in ("projected_s", "threshold", "remaining_budget_s")]
        if not all(_number(v) for v in values):
            return "failed", RESULT_MISSING
        if exit_code != 1:
            return "failed", RESULT_CONTRADICTS_EXIT
        projected_s, threshold, remaining = values
        return "skipped", SIM_PROJECTED.format(projected=f"{projected_s / 60:.1f}",
                                               budget=f"{threshold * remaining / 60:.1f}")
    if result.get("status") != "complete":
        return "failed", RESULT_MISSING
    scan = result.get("scan")
    extraction = result.get("extraction")
    passed = [block.get("passed") if isinstance(block, dict) else None for block in (scan, extraction)]
    if not all(isinstance(v, bool) for v in passed):
        return "failed", RESULT_MISSING
    if exit_code != (0 if all(passed) else 1):
        return "failed", RESULT_CONTRADICTS_EXIT
    return ("ok", None) if passed[0] else ("result_fail", None)


def g0_status(status: str | None, reason: str | None, result: Any) -> tuple[str | None, str | None]:
    """G0's model path, read by data shape: an ``ok`` or ``result_fail`` G0 unit whose ``leakage.json`` has a
    ``model_path`` listing problems, with nothing leaked (no canary hit, no shingle overlap, no site-ledger hit or
    overlap), is ``invalid`` (:data:`~lab.notes.G0_MODEL_PATH` and the harness's problems): the scan did not test the
    model in the loop. A leak stays ``result_fail``; a result without ``model_path`` keeps its status."""
    model_path = result.get("model_path") if isinstance(result, dict) else None
    problems = model_path.get("problems") if isinstance(model_path, dict) else None
    if status not in ("ok", "result_fail") or not isinstance(problems, list) or not problems:
        return status, reason
    hygiene = result.get("site_ledger_hygiene")
    no_leak = (result.get("hit_count") == 0 and result.get("shingle_overlap_bytes") == 0 and isinstance(hygiene, dict)
               and hygiene.get("hits") == [] and hygiene.get("shingle_overlap_bytes") == 0)
    if not no_leak:
        return status, reason
    return "invalid", printable(f"{G0_MODEL_PATH}: " + "; ".join(str(p) for p in problems), LOG_LINE_CAP)


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
    record: dict[str, Any] = {"threshold": MIN_MODEL_OK_SHARE, "required": list(required), "tasks": tasks, "e3": e3,
                              "sim": None}
    for task in required:
        if counts[task]["attempted"] == 0:
            return record, f"{NO_MODEL_CALLS}: {task}"
    if experiment == "sim":
        extraction = result.get("extraction") if isinstance(result, dict) else None
        share = extraction.get("fallback_share") if isinstance(extraction, dict) else None
        record["sim"] = {"fallback_share": share, "max_share": MAX_SIM_FALLBACK_SHARE}
        if not isinstance(extraction, dict) or extraction.get("passed") is not True:
            return record, (f"{SIM_LOW_PARTICIPATION}: lexical fallback share {share} above "
                            f"{MAX_SIM_FALLBACK_SHARE}")
    for task, c in sorted(counts.items()):
        if c["attempted"] and c["ok"] * 100 < round(MIN_MODEL_OK_SHARE * 100) * c["attempted"]:
            return record, f"{LOW_PARTICIPATION}: {task} ok share {tasks[task]['share']} below {MIN_MODEL_OK_SHARE}"
    if e3 is not None and e3["measured"] and e3["failed"] * 100 > round(MAX_E3_FAILED_SHARE * 100) * e3["measured"]:
        return record, f"{E3_FAILURES}: {e3['failed']} of {e3['measured']} measured requests failed"
    return record, None


def harness_measurement(experiment: str, result: Any) -> Any:
    """What the harness's result says about being a measurement, as recorded: E1's and E3's ``measurement``, the
    sim's, E2's and X1's ``stamps.measurement``; None for G0, openFDA or a result that is not an object."""
    if not isinstance(result, dict):
        return None
    if experiment in ("e1", "e3"):
        return result.get("measurement")
    if experiment in ("sim", "e2", "x1"):
        stamps = result.get("stamps")
        return stamps.get("measurement") if isinstance(stamps, dict) else None
    return None


def harness_verdict(experiment: str, result: Any) -> bool | None:
    """E1's, E2's, E3's and the sim's own verdict that the run was a measurement, for :func:`model_checks`: only
    ``measurement: true`` counts (a missing key or result does not); None for a harness that gives no such verdict
    (G0, X1, openFDA)."""
    if experiment not in ("e1", "e2", "e3", "sim"):
        return None
    return harness_measurement(experiment, result) is True


def model_checks(evidence: Mapping[str, Any], rows: list[dict[str, Any]], status: str | None,
                 harness_measurement: bool | None, alias: str, host: str,
                 hosted_rows: list[dict[str, Any]] | None = None,
                 hosted_host: str | None = None) -> tuple[dict[str, bool], list[str]]:
    """Every condition that admits a model measurement (:data:`CHECK_KEYS`, in order) and the flags; see the module
    docstring. ``harness_measurement`` is None when the harness has no such verdict (G0), which passes. For an E2
    unit with a hosted central comparator, ``rows`` are the site rows only (``ledger_host`` and ``model_served`` judge
    the local server on them) and ``hosted_rows`` the central rows, each of which must have gone to ``hosted_host``."""
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
        "ledger_host": held(lambda: all(row["host"] == host for row in rows)
                            and all(row["host"] == hosted_host for row in hosted_rows or [])),
        "model_served": held(lambda: all(row["model_served"] == alias for row in rows if row["http_status"] == 200)),
        "harness_measurement": harness_measurement is None or harness_measurement is True,
        "participation": status in ("ok", "result_fail"),
    }
    if held(lambda: server_record["verified_by"] == "first-use"):
        flags.append("server_first_use")
    return checks, flags


def hosted_checks(rows: list[dict[str, Any]], status: str | None, harness_measurement: bool | None,
                  host: str) -> dict[str, bool]:
    """The conditions that admit a hosted API result (:data:`HOSTED_CHECK_KEYS`): every ledger row went to the
    configured host (``hosted_host``), the harness counted the run as a measurement (None passes) and the unit ran
    to a valid result."""
    return {"hosted_host": all(row.get("host") == host for row in rows),
            "harness_measurement": harness_measurement is None or harness_measurement is True,
            "participation": status in ("ok", "result_fail")}


def measurement_class(kind: str, provider_override: str | None, rows: list[dict[str, Any]],
                      checks: Mapping[str, bool] | None = None) -> tuple[str, str]:
    """(class, reason key); the reason keys are those of ``notes.CLASS_REASONS``. A hosted unit (kind ``hosted``)
    is judged on :data:`HOSTED_CHECK_KEYS` and is at best ``hosted-api``, never ``model``."""
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
    for key in (HOSTED_CHECK_KEYS if kind == "hosted" else CHECK_KEYS):
        if checks.get(key) is not True:
            return "unverified", key
    return ("hosted-api", "hosted_verified") if kind == "hosted" else ("model", "verified")


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
    5. ``hosted-api`` only when the record says hosted-api, its kind is hosted, the provenance is ``real``, it counted
       zero fake rows and every one of :data:`HOSTED_CHECK_KEYS` is exactly true: a result of the configured host,
       never a measurement on this runner;
    6. otherwise ``unverified``.
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
    if (record.get("measurement_class") == "hosted-api" and record.get("kind") == "hosted" and prov is not None
            and prov.get("result_class") == "real" and prov.get("provider") != "fake" and _is_int(fake_rows)
            and fake_rows == 0 and isinstance(checks, Mapping)
            and all(checks.get(key) is True for key in HOSTED_CHECK_KEYS)):
        return "hosted-api"
    return "unverified"


def unit_notes(experiment: str, measurement: str, hosted_role: str | None = None) -> list[str]:
    """The note keys (``notes.NOTES``) of a unit. A hosted E1 endpoint (``hosted_role`` ``endpoint``) drops
    ``runner_hardware`` and adds ``hosted_api`` (unless plumbing) and ``hosted_raw``; a hosted central comparator
    (``central``) adds ``central_hosted`` and ``hosted_raw``. A plumbing unit with a hosted role gets
    ``plumbing_hosted`` in place of ``plumbing``: its hosted calls went to the configured host, not to a fake."""
    plumbing = "plumbing" if hosted_role is None else "plumbing_hosted"
    notes = [plumbing] if measurement == "plumbing" else ["model_measurement"] if measurement == "model" else []
    if experiment == "openfda":
        return [*notes, "public_data"]
    notes.append("synthetic")
    if experiment in ("e1", "e2", "e3", "sim") and hosted_role != "endpoint":
        notes.append("runner_hardware")
    if experiment in ("e2", "g0", "sim"):
        notes.append("text_only_scan")
    if hosted_role == "endpoint":
        notes += [*(["hosted_api"] if measurement != "plumbing" else []), "hosted_raw"]
    elif hosted_role == "central":
        notes += ["central_hosted", "hosted_raw"]
    return notes


def hosted_role(unit: Mapping[str, Any]) -> str | None:
    """``endpoint``, ``central`` or None, from the unit alone (its kind comes from the manifest, as
    ``lab.hosted.role`` reads it)."""
    if unit["experiment"] == "e1" and unit["kind"] == "hosted":
        return "endpoint"
    if unit["experiment"] == "e2" and unit["params"].get("central", "self") != "self":
        return "central"
    return None


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
        "notes": unit_notes(unit["experiment"], measurement, hosted_role(unit)), "routing_sha256": None,
        "files": {}, "fake_rows": None, "ledger_rows": None, "participation": None, "harness_measurement": None,
        "logs": None, "serving": None, "server_exit": None, "server_after": None, "class_checks": None,
        "class_flags": [], "routing_files": None, "steps": None, "projection": None, "hosted": None,
    }
    unknown = set(fields) - set(record)
    if unknown:
        raise ValueError(f"unknown unit.json keys {sorted(unknown)}") from None
    record.update(fields)
    return record


def e2_projection(rehearsal: Mapping[str, Any], rows: list[dict[str, Any]],
                  timeout_s: float) -> dict[str, Any] | None:
    """The E2 run's projected seconds on this server, or None when the warm-up has no successful row for one of the
    three tasks (the unit then runs unprojected). ``rows`` are the start's successful warm-up ledger rows; each
    task's latency is the slowest of them. The rehearsal ran the same candidates without a model, so::

        projected_s = pipeline_s + calls.judge_record x judge_s
                      + (central_raw_records.sum / central_raw_records.max) x raw_s
                      + calls.judge_candidate_allowed x allowed_s

    where ``raw_s`` was measured on the worst-case payload of ``central_raw_records.max`` records, so the raw term
    scales it by the records the run reads. ``exceeds`` when above :data:`~lab.sim.PROJECTION_SHARE` of
    ``timeout_s``."""
    latency: dict[str, float] = {}
    for row in rows:
        value = row.get("latency_ms")
        if row.get("ok") is True and _number(value):
            latency[row["task"]] = max(latency.get(row["task"], 0.0), value / 1000)
    if any(task not in latency for task in (JUDGE_TASK, *CENTRAL_TASKS)):
        return None
    calls, raw = rehearsal["calls"], rehearsal["central_raw_records"]
    inputs = {"pipeline_s": rehearsal["pipeline_s"], "judge_record_calls": calls["judge_record"],
              "judge_s": latency[JUDGE_TASK], "raw_records_sum": raw["sum"], "raw_records_max": raw["max"],
              "raw_s": latency[CENTRAL_TASKS[0]], "allowed_calls": calls["judge_candidate_allowed"],
              "allowed_s": latency[CENTRAL_TASKS[1]]}
    raw_calls = inputs["raw_records_sum"] / inputs["raw_records_max"] if inputs["raw_records_max"] else 0.0
    projected = (inputs["pipeline_s"] + inputs["judge_record_calls"] * inputs["judge_s"] + raw_calls * inputs["raw_s"]
                 + inputs["allowed_calls"] * inputs["allowed_s"])
    return {"projected_s": round(projected, 3), "budget_s": round(timeout_s, 3), "share": PROJECTION_SHARE,
            "exceeds": projected > PROJECTION_SHARE * timeout_s, "suggested_minutes": suggested_minutes(projected),
            "inputs": inputs}


def warmup_rows(out: Path, start: int) -> list[dict[str, Any]]:
    """The successful rows of one server start's warm-up (run id ``warmup-<start>``)."""
    path = Path(out) / WARMUP_LEDGER
    failed = False
    try:
        rows = read_ledger(path) if path.exists() else []
    except (OSError, ValueError):
        failed = True
    if failed:
        return []
    return [r for r in rows if r["run_id"] == f"warmup-{start}" and r["ok"] is True]


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


def ledger_rows(unit: Mapping[str, Any], out: Path,
                patterns: tuple[str, ...] | None = None) -> list[dict[str, Any]] | None:
    """Every row of the collected ledgers (or of those matching ``patterns``); None when one cannot be read (a
    partial line from a killed run)."""
    rows: list[dict[str, Any]] = []
    for path in _collected(unit, out, ADAPTERS[unit["experiment"]].ledgers if patterns is None else patterns):
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
             serving: Serving | None = None, prereg: Any = None,
             openfda_base_url: str | None = None, hosted: lab_hosted.Handle | None = None) -> dict[str, Any]:
    """Run one unit and return its record. ``prereg`` is the shard's verified :class:`~lab.prereg.Prereg` (E1, E2
    and X1 fail without it); ``openfda_base_url`` replaces api.fda.gov for the openFDA unit (tests only). ``hosted``
    is the shard's :class:`lab.hosted.Handle` for a unit that calls the hosted provider: an E1 hosted unit then
    starts no server and sends its calls to the host, an E2 unit's central comparator is the hosted model, and the
    harness environment is the handle's (the allowlist plus the unit's ``env``: the stripped key)."""
    out = Path(out)
    experiment = unit["experiment"]
    provider = "fake" if provider_override == "fake" else plan["provider"]
    if experiment == "openfda":
        from . import openfda as lab_openfda
        return lab_openfda.run_unit(unit, plan, out, timeout_s=timeout_s, base_url_override=openfda_base_url,
                                    provider_override=provider_override)
    if experiment in PREREG_EXPERIMENTS and prereg is None:
        return unit_record(unit, unit["shard"], provider, provider_override, status="failed",
                           status_reason=PREREG_MISSING)
    adapter = ADAPTERS[experiment]
    entry = plan["models"][unit["model"]] if unit["model"] is not None else None
    persona = entry["persona"] if entry is not None and entry["kind"] == "fake" else "valid"
    server_exit = None
    work = out / "work" / unit["unit"]
    unit_dir = out / "units" / unit["unit"]
    routing_path = out / "routing" / f"{unit['unit']}.json"
    started_at, t0 = utc_clock(), time.monotonic()
    proc: ProcessResult | None = None
    argv: list[str] = []
    routing_sha256: str | None = None
    routing_files: dict[str, str] | None = None
    projection: dict[str, Any] | None = None
    status: str | None = None
    reason: str | None = None
    interrupted = False
    server: FakeServer | None = None
    server_note = FAKE_SERVER_NOTE
    harness_paths: dict[str, Path] = {}
    endpoint_role = hosted is not None and hosted.role == "endpoint"
    try:
        if entry is not None:
            if endpoint_role:
                base_url = hosted.base_url
            elif serving is None:
                with_pack = experiment in ("e1", "e2", "g0", "sim")
                server = FakeServer(persona, load_pack(unit["params"]["pack"]) if with_pack else None)
                server.start()
                base_url = server.base_url
            else:
                base_url, server_note = serving.base_url, serving.server_note
            routing_sha256, routing_files, valid, harness_paths = write_routing(unit, plan, entry, base_url, out,
                                                                                prereg, hosted)
            if not valid:
                status, reason = "failed", LAB_ROUTING
        if status is None and experiment == "e2" and serving is not None:
            warm = warmup_rows(out, serving.start)
            if hosted is not None:
                warm += lab_hosted.preflight_rows(out, hosted.key)     # the central latencies, measured on the host
            projection = e2_projection(prereg.manifest["e2"]["rehearsal"], warm, timeout_s)
            if projection is not None and projection["exceeds"]:
                status = "skipped"
                reason = SIM_PROJECTED.format(projected=f"{projection['projected_s'] / 60:.1f}",
                                              budget=f"{projection['share'] * projection['budget_s'] / 60:.1f}")
        if status is None:
            budget_s = max(1, math.floor(timeout_s) - SIM_BUDGET_MARGIN_S) if experiment == "sim" else None
            argv = build_argv(unit, out, harness_paths.get("routing", routing_path), server_note, budget_s=budget_s,
                              prereg=prereg.dir if prereg is not None else None,
                              central_routing=harness_paths.get("central_routing"))
            work.mkdir(parents=True, exist_ok=True)
            unit_dir.mkdir(parents=True, exist_ok=True)
            # the unit's env (the hosted key) only through the shard's handle, never from this process's environment
            env = subprocess_env(hosted.environ, unit["env"]) if hosted is not None else subprocess_env(os.environ, [])
            proc = run_process(argv, env, cwd=ROOT, timeout_s=timeout_s,
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
        status, reason = harness_status(experiment, proc.exit_code, proc.timed_out, result)
    rows = ledger_rows(unit, out)
    record_participation = None
    if status in ("ok", "result_fail") and unit["kind"] != "none":
        if rows is None:
            status, reason = "failed", RESULT_MISSING
        else:
            record_participation, problem = participation(experiment, rows, required_tasks(unit), result)
            if problem is not None:
                status, reason = "invalid", problem
    if experiment == "g0":
        status, reason = g0_status(status, reason, result)
    if serving is not None and proc is not None and server_exit is None and not interrupted \
            and status not in ("ok", "result_fail"):
        # requests that just failed may have met a killed server that is not reaped yet: name the exit if it comes
        server_exit = serving.poll(EXIT_GRACE_S)
        if server_exit is not None:
            status, reason = "invalid", exit_reason(server_exit)
    checks, flags = None, []
    if endpoint_role:
        checks = hosted_checks(rows or [], status, harness_verdict(experiment, result), hosted.host)
    elif serving is not None and hosted is not None:
        site_rows = ledger_rows(unit, out, (E2_SITE_LEDGERS,))
        central_rows = ledger_rows(unit, out, (lab_hosted.HOSTED_LEDGERS["e2"],))
        checks, flags = model_checks(serving.evidence, site_rows or [], status, harness_verdict(experiment, result),
                                     serving.alias, serving.host_label, hosted_rows=central_rows or [],
                                     hosted_host=hosted.host)
    elif serving is not None:
        checks, flags = model_checks(serving.evidence, rows or [], status, harness_verdict(experiment, result),
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
        measurement_class=measurement, class_reason=class_reason,
        notes=unit_notes(experiment, measurement, hosted.role if hosted is not None else None),
        routing_sha256=routing_sha256, routing_files=routing_files, projection=projection, files=files,
        fake_rows=sum(1 for r in rows if r["fake_marker"]) if rows is not None else None,
        ledger_rows=len(rows) if rows is not None else None, participation=record_participation,
        harness_measurement=harness_measurement(experiment, result), logs=logs,
        serving=({"start": serving.start, "host": serving.host_label, "alias": serving.alias,
                  "class": serving.class_name} if serving is not None else None),
        server_exit=server_exit, class_checks=checks, class_flags=flags,
        hosted=hosted_record(hosted))


def hosted_record(hosted: lab_hosted.Handle | None) -> dict[str, Any] | None:
    """The unit record's ``hosted`` block (no base URL, no path; ``calls_after`` is filled by the shard)."""
    if hosted is None:
        return None
    return {"key": hosted.key, "role": hosted.role, "model": hosted.entry["model"], "scheme": hosted.scheme,
            "host": hosted.host, "response_format": hosted.entry["response_format"],
            "transport_schema": hosted.entry["transport_schema"], "share": hosted.share,
            "calls_before": hosted.calls_before, "calls_after": None}
