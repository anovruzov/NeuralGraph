"""The multi-site simulation: a model inside each simulated site of one seeded synthetic world, HQ detection over the
k-suppressed cells that left the sites, pushdown verification of the top candidates, and a scorecard against the
baselines. Synthetic and internal only; a measurement of the model only when no fake answered.

    python -m lab.sim --plant NAME --seed S --weeks W --eval-from F --eval-to T --grace-weeks G --tie-salt SALT
        --top-n N --bootstrap-b B --bootstrap-seed BS --routing FILE --run-id ID --budget-seconds X
        [--runs-dir runs] [--deadline-seconds 3600] [--dry-run]

**The world.** ``generate(pack, seed, 6, weeks)`` plus the plant :data:`PLANTS` names, planted into it
(``evaluate.plant``). Lab plants are same-author and not blind, so no figure here is a result. ``sim_small`` (this
lab's ``plants/device_quality/sim_small.json``) is sized for one runner job: at seed 1 and 34 weeks, 845 generated plus
186 planted records. ``plant_smoke`` is the collective's fixture, read only, and needs at least 50 weeks with the
evaluation from week 26 (its stale-chain decoy must be stale before the evaluation weeks). ``judge_calls_per_candidate``
is the mean number of judge calls the planner's lexical probe measured per verified candidate (sim_small 31.8, 30.7
and 23.6 at seeds 1 to 3; plant_smoke 35.4 at seed 1), rounded up: an assumption the projection uses until the run has
measured its own.

**The routing.** One file, as G0's routing mode takes it: both ``extract_claims`` and ``judge_record`` routed to
``any-simulated`` ``openai_compat`` endpoints without escalation (one machine plays every site); anything else is a
usage error. Each site gets its own runtime bound to ``site:<id>``, built with ``simulation=True`` and the data label
``synthetic``, ledgering to ``<run dir>/edge/site-<id>.ledger.jsonl`` (outside ``work/``, so a killed run still leaves
its ledgers).

**Phases**, each timed in ``timings``:

1. setup: plant the world; ``labels.json`` is the plant's labels plus ``seeds: [{seed, planted_records}]``;
2. ``list_models`` on every routed endpoint (``models_fake``);
3. the runtimes (:class:`ObservedRuntime`, which times every ``run`` for :class:`Progress` and memoizes every
   extraction), on a ``SimClock`` set to the pipeline's own stamp;
4. judge warm-up: :data:`JUDGE_WARMUP_CALLS` single judge calls on the first site's first record
   (``warmup.judge_example``);
5. the model pipeline (:func:`run_pipeline` with the runtimes, in ``work/model``): each site extracts its own records
   with the model (a reply invalid after its repair, or a failed call, falls back to the lexical extractor and is
   counted); after exactly :data:`PREFLIGHT_RECORDS` extraction records the projection check runs (below);
6. the lexical pipeline (``baselines.run_pipeline``, in ``work/lexical``), for ``X_lexical``;
7. the no-plant control, when the collective's harness scores one (:data:`HARNESS_CONTROL`: its ``channel_block``
   takes ``control_events``): the same seed's world without the plant (``world.records``) through the model pipeline
   (``work/control-model``, every runtime ``replaying``: an extraction the planted run made is replayed from its
   memo, not sent again) and the lexical pipeline (``work/control-lexical``), and its channels' windowed events; the
   scorecard's ``control`` block counts the replayed calls and the misses (sent and ledgered, never observed);
8. the channels, with X1's public functions and the tie salt: ``X_model`` and ``S`` (``hq_results`` over the model
   pipeline's HQ store), ``X_lexical`` (the lexical pipeline's X), ``R_mf`` and ``U`` (``exact_result`` over
   ``r_mf_cells`` and ``u_cells``), ``single_site`` and ``rules``; alerts before the evaluation weeks are dropped
   (``eval_events``), and ``channel_block``, ``pattern_outcome`` and ``lift`` read them against the labels. With the
   control, ``channel_block`` also gets the control's events and, for the detector channels, the stale chains'
   candidate weeks (X1's own inputs), and the lifts compare ``harness.net_found`` (finds net of chance); with any
   ``narrative_only`` pattern, :func:`by_construction` marks S and R_mf as X1's scorecard does;
9. pushdown: the ``X_model`` candidates whose first candidate week is an evaluation week, by (-snapshot score,
   sha256(tie salt | key)), the first ``--top-n``, verified in (snapshot as_of, key) order with ``verify_stored`` at
   each snapshot's ``as_of``; every site answers with its own runtime (``SiteVerifier``, seeded demo secret). Items
   are labelled as E2 labels them (:func:`pushdown_label`); AP is ``stats.tie_averaged_ap`` of the pushdown score
   (``STATUS_RANK * 1_000_000 + support lower bound``) and of the detector's snapshot score;
10. the raw-text scan (:func:`scan_pipeline`) of everything that crossed in the model pipeline (``work/model`` only,
    never the control's), with a positive control;
11. the scorecard; everything opened is closed in a ``finally``.

**Projection.** At the 20th extraction record (phase ``extract`` only, on the extracting thread) :func:`projection`
compares ``extract p50 x remaining records + judge p50 x top_n x judge calls per candidate`` with
:data:`PROJECTION_SHARE` of the budget left (``--budget-seconds`` less the time since the start). The judge p50 is
the warm-up's (``judge_source: warmup``), else the extraction p50 (``extract_p50``). Strictly above it, the run
stops: ``status: skipped_projection``, exit 1, every result block null. Every scorecard and ``progress.json`` carries
the measured per-call medians, an estimate and ``suggested_minutes = ceil(1.25 x estimate / 60)`` for the next
request: measured (the run's total) for a complete run, projected (elapsed plus projected) for a skipped one.

**Files** under ``<runs dir>/sim/<run id>/``: ``labels.json``, ``progress.json`` (rewritten atomically at setup,
after the warm-up, every :data:`PROGRESS_EVERY` extraction records, at each phase change, after each candidate and at
the end, so a killed run leaves a readable estimate), ``scorecard.json``, ``edge/site-<id>.ledger.jsonl`` and
``work/`` (every pipeline's stores and logs; the lab collects only the first four and deletes ``work/``).

**The scorecard** (``kind: lab_sim_scorecard``) is validated (:func:`scorecard_problems`) before it is written:
``stamps`` (``synthetic``, ``internal_only``, ``blind: false``, ``measurement`` and its reasons), ``world``,
``settings``, ``endpoint``, ``extraction`` (per site: records, claims, model and fallback counts, errors per kind of
:data:`EXTRACTION_ERROR_KINDS`, drops, invalid claims; ``passed`` when every site used only the model and the fallback
and the fallback share is at most :data:`MAX_SIM_FALLBACK_SHARE`), ``latency`` (the ledgers' successful calls,
``stats.percentile`` rounded to 3 places as ``aggregate.latency_rows`` does), ``channels`` (the harness's own channel
block schema, ``harness._CHANNEL_BLOCK``), ``patterns``, ``lifts`` (``harness._LIFT``), ``by_construction`` (X1's
entry shape: channel, the harness's statement and label, visibility, recall), ``control`` (null without the
no-plant control or for a skipped run), ``pushdown``, ``raw_text_crossed``, ``scan``, ``projection``, ``code``,
``timings``, ``notes``, ``paths`` and ``content_hash`` (sha256 of the canonical JSON without
:data:`CONTENT_HASH_EXCLUDES`, so equal across processes, hash seeds, run ids, ports and machines). A complete run has
every result block; a skipped one none.

**Honesty rules.** ``measurement`` is true only when no fake took part (``measurement_flag`` over the routed
endpoints, every site-ledger row and the model listing) and model participation passed, and never for a skipped run;
``measurement_reasons`` names each failing rule. Every figure comes from the functions X1 and E2 use; the lexical
extractor is exact on generator text, so every pattern only one of ``X_model`` and ``X_lexical`` found turns on the
model's extraction errors, which can lose a pattern and can also find one (a wrong predicate on a grounded entity adds
counts to a planted key; a lost claim elsewhere can free alert budget) (``lexical_exact``); ``model_beyond_exact`` is
present exactly when, in the lifts' count (net of chance with the control), ``X_model`` found a pattern ``X_lexical``
missed: such a find counts for the model in every lift; ``R_mf`` is model-free (``r_model_free``); with fewer than ten
patterns nothing is interpretable as an estimate (``few_patterns``); exactly one of ``no_control`` and
``chance_control`` says whether chance finds were left out. Text only: the scan covers bytes, not counts or timing.

**Exit codes**: 0 complete, scan passed and participation passed; 1 complete with the scan or participation failing,
or skipped by the projection; 2 usage, configuration or plant error (nothing written; the dry-run contract of
``experiments.common``); 130 interrupted (the partial run directory is left and its run id is refused later). stdout
is one line (``sim: plant=.. seed=.. status=.. measurement=.. raw_text_crossed=.. -> <scorecard> (synthetic, internal
only)``), which a shard never prints.

**Integration.** :func:`run_pipeline` copies ``mycelic.collective.evaluate.baselines.run_pipeline`` at b861362 and
adds a ``runtimes`` parameter (model extraction) and the per-site summaries; ``tests/lab/test_lab_sim.py``
(``PipelineEquivalenceTests``) catches drift. :data:`HARNESS_CONTROL`, :data:`EXTRACTION_ERROR_KINDS` and the
schemas read from ``harness._CHANNEL_BLOCK`` and ``harness._LIFT`` follow the installed collective by data shape;
``docs/lab/INTEGRATION.md`` lists each shim, the hooks wanted upstream and when to remove them.
"""
from __future__ import annotations

import argparse
import copy
import inspect
import math
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective import schemacheck, stats
from mycelic.collective.detect.detectors import TIE_SALT_RE
from mycelic.collective.detect.store import CollectiveStore
from mycelic.collective.edge import extract as extract_module
from mycelic.collective.edge.egress import read_log
from mycelic.collective.edge.extract import DROP_REASONS, FALLBACK_EXTRACTOR, TASK_NAME
from mycelic.collective.edge.site import EdgeSite, ExtractSummary
from mycelic.collective.edge.verify import JUDGE_TASK, SiteVerifier, judge_schema, judge_task
from mycelic.collective.evaluate import baselines, harness
from mycelic.collective.evaluate.baselines import (EVAL_ENTERPRISE, EvaluationError, Pipeline, closing_date,
                                                   detector_alerts, exact_result, hq_results, org_for_sites,
                                                   r_mf_cells, rule_alerts, single_site_alerts, u_cells, world_weeks)
from mycelic.collective.evaluate.plant import (DECOY_CLASSES, VISIBILITIES, PlantError, PlantSpec, check_plant,
                                               labels_doc, load_plant, plant)
from mycelic.collective.experiments.common import (DryRun, UsageError, check_run_id, code_commit, code_dirty,
                                                   code_hash, fail, measurement_flag, run_dir, utc_clock,
                                                   write_json_atomic)
from mycelic.collective.experiments.e2_pushdown import STATUS_SCALE, UNKNOWN_REASONS, SimClock
from mycelic.collective.inference.client import list_models
from mycelic.collective.inference.errors import KINDS as ERROR_KINDS
from mycelic.collective.inference.errors import InferenceError
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import ConfigError, RoutingConfig, load_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.inference.tasks import TaskSpec
from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from mycelic.collective.leakage import CANARY_PREFIX, Artifact, LeakageError, Manifest, scan
from mycelic.collective.packs.generator import GeneratorError, World, generate, world_digest
from mycelic.collective.packs.loader import FrozenPack, load_pack
from mycelic.collective.pushdown.gate import STATUS_RANK, STATUSES
from mycelic.collective.pushdown.orchestrator import Orchestrator
from mycelic.collective.pushdown.questions import PushdownError

from . import ROOT, forbidden_root
from .notes import SIM_CHANNEL_LABELS, SIM_MEASUREMENT_REASONS, SIM_NOTES
from .warmup import judge_example

CLI = "lab.sim"
KIND = "sim"
SCHEMA_VERSION = 1
SITES = 6
MAX_WEEKS = 52
MAX_TOP_N = 60
MAX_SEEDS = 5
SEED_MAX = 2147483647
TIE_SALT = "lab-sim"
GRACE_WEEKS = 4
BOOTSTRAP_B = 10000
BOOTSTRAP_SEED = 1
PREFLIGHT_RECORDS = 20
PROGRESS_EVERY = 25
PROJECTION_SHARE = 0.9
SUGGESTED_FACTOR = 1.25
JUDGE_WARMUP_CALLS = 3
MAX_SIM_FALLBACK_SHARE = 0.05
DEADLINE_SECONDS = 3600.0
CHANNELS = ("X_model", "X_lexical", "S", "R_mf", "U", "single_site", "rules")
BY_CONSTRUCTION_CHANNELS = ("S", "R_mf")
LIFTS = (("X_model_minus_S", "X_model", "S"), ("X_model_minus_R_mf", "X_model", "R_mf"),
         ("X_model_minus_X_lexical", "X_model", "X_lexical"))
CONTENT_HASH_EXCLUDES = ("$.content_hash", "$.created_at", "$.run_id", "$.paths", "$.timings", "$.latency",
                         "$.projection", "$.endpoint")
SCAN_METHOD = ("leakage.scan narrative shingles (24 characters) of every record of the world, with an empty canary "
               "manifest, over what crossed in the model pipeline: each site's egress and ingress logs, HQ's "
               "directory (receive log, questions, collective database and its WAL) and every receive and question "
               "row body; positive control: the first site's own database and its WAL")
ROUTING_PROBLEM = "the routing file must route both tasks to any-simulated openai_compat endpoints without escalation"
PHASES = ("setup", "warmup", "extract", "lexical", "channels", "pushdown", "scan", "done", "skipped_projection",
          "control")
TIMING_KEYS = ("setup_s", "list_models_s", "warmup_s", "model_pipeline_s", "lexical_pipeline_s", "control_pipeline_s",
               "channels_s", "pushdown_s", "scan_s", "total_s")
LABELS = ("true", "decoy", "background")
STATUS_VALUES = ("complete", "skipped_projection")
CONTROL_NOTES = ("no_control", "chance_control")
DETECTOR_CHANNELS = ("X_model", "X_lexical", "S", "R_mf", "U")
# the collective's harness scores a no-plant control when its channel_block takes one (read from its signature)
HARNESS_CONTROL = "control_events" in inspect.signature(harness.channel_block).parameters
# the collective's harness adds bootstrap intervals to each channel block when it has interval_blocks
HARNESS_INTERVALS = hasattr(harness, "interval_blocks")
_NOT_SENT = getattr(extract_module, "NOT_SENT", None)
EXTRACTION_ERROR_KINDS = tuple(ERROR_KINDS) + ((_NOT_SENT,) if _NOT_SENT is not None else ())


# --------------------------------------------------------------------------------------------------- plants

@dataclass(frozen=True)
class LabPlant:
    name: str
    pack: str
    path: Path
    eval_from: int
    judge_calls_per_candidate: int


PLANTS = {
    "sim_small": LabPlant("sim_small", "device_quality", ROOT / "lab" / "plants" / "device_quality" / "sim_small.json",
                          19, 32),
    "plant_smoke": LabPlant("plant_smoke", "device_quality", ROOT / "mycelic" / "collective" / "packs" / "data"
                            / "device_quality" / "fixtures" / "plant_smoke.json", 26, 36),
}


def world_settings(plant_: LabPlant, weeks: int) -> dict[str, int]:
    """The evaluation settings a plant runs under for a world of ``weeks`` weeks."""
    return {"eval_from": plant_.eval_from, "eval_to": weeks - 1, "grace_weeks": GRACE_WEEKS}


def min_weeks(pack: FrozenPack) -> int:
    return pack.detectors["baseline_weeks"] + pack.detectors["window_weeks"]


def site_ids_of(pack: FrozenPack) -> list[str]:
    return [s["id"] for s in pack.generator["sites"][:SITES]]


@lru_cache(maxsize=None)
def plant_problem(name: str, seed: int, weeks: int) -> str | None:
    """The fixed problem ``check_plant`` finds for the plant in ``generate(pack, seed, 6, weeks)`` under
    :func:`world_settings`, or None when it fits."""
    lab_plant = PLANTS[name]
    pack = load_pack(lab_plant.pack)
    world = generate(pack, seed, SITES, weeks)
    settings = world_settings(lab_plant, weeks)
    problem = None
    try:
        spec = load_plant(lab_plant.path, pack)
        check_plant(spec, pack, site_ids=site_ids_of(pack), master_data=world.master_data, n_weeks=weeks,
                    eval_from=settings["eval_from"], eval_to=settings["eval_to"])
    except PlantError as err:
        problem = err.problem
    return problem


def pipeline_stamp(pack: FrozenPack, weeks: Sequence[str]) -> str:
    """The constant clock :func:`run_pipeline` gives its sites: the day after the last week's closing date."""
    as_of = closing_date(pack, weeks[-1])
    return (date.fromisoformat(as_of) + timedelta(days=1)).isoformat() + "T00:00:00Z"


# --------------------------------------------------------------------------------------------------- the pipeline

def run_pipeline(pack: FrozenPack, records: Sequence[Mapping[str, Any]], *, site_ids: Sequence[str],
                 master_data: Mapping[str, Mapping[str, Sequence[str]]], weeks: Sequence[str], workdir: str | Path,
                 runtimes: Mapping[str, Runtime] | None = None,
                 enterprise: str = EVAL_ENTERPRISE) -> tuple[Pipeline, dict[str, ExtractSummary], dict[str, int]]:
    """``baselines.run_pipeline`` at b861362 with a ``runtimes`` parameter: with runtimes (exactly one per site id),
    each site extracts with its model (``site.extract("model")``, lexical fallback on); without, lexically. Returns
    the pipeline, each site's extraction summary and each site's ingested count. Each site ingests its records (in
    list order); every site then emits its cells at each week's closing date through its Boundary; HQ ingests the
    receive log. Any rejection or duplicate raises."""
    if runtimes is not None and sorted(runtimes) != sorted(site_ids):
        raise ValueError("runtimes must hold exactly one runtime per site id") from None
    workdir = Path(workdir)
    weeks = tuple(weeks)
    as_of = closing_date(pack, weeks[-1])
    stamp = (date.fromisoformat(as_of) + timedelta(days=1)).isoformat() + "T00:00:00Z"

    def clock() -> str:
        return stamp

    org = org_for_sites(site_ids, enterprise)
    sites: dict[str, EdgeSite] = {}
    summaries: dict[str, ExtractSummary] = {}
    ingested: dict[str, int] = {}
    store = None
    try:
        for sid in site_ids:
            site = sites[sid] = EdgeSite(pack, sid, workdir / "edge",
                                         runtime=runtimes[sid] if runtimes is not None else None, clock=clock,
                                         master_data=master_data[sid], hq_dir=workdir / "hq")
            got = site.ingest([r for r in records if r["site"] == sid])
            if got.duplicates or sum(got.rejected.values()):
                raise EvaluationError(f"site {sid} rejected or duplicated records") from None
            ingested[sid] = got.ingested
            summaries[sid] = site.extract("model" if runtimes is not None else "lexical")
        for week in weeks:
            for sid in site_ids:
                sites[sid].emit_cells(closing_date(pack, week))
        store = CollectiveStore(workdir / "hq" / "collective.sqlite3", pack, org, clock=clock)
        report = store.ingest_log(workdir / "hq" / "receive.jsonl")
        if report.duplicates or sum(report.rejected.values()):
            raise EvaluationError("HQ rejected or duplicated a bundle") from None
    except BaseException:
        if store is not None:
            store.close()
        for site in sites.values():
            site.close()
        raise
    return Pipeline(pack=pack, org=org, weeks=weeks, as_of=as_of, sites=sites, store=store), summaries, ingested


# --------------------------------------------------------------------------------------------------- observation

class ProjectionExceeded(Exception):
    """Raised by :class:`Progress` on the extracting thread when the projected run does not fit the budget."""

    def __init__(self, projection_: dict[str, Any]) -> None:
        super().__init__("the projected run does not fit its budget")
        self.projection = projection_


def projection(*, extract_p50_s: float, remaining_records: int, judge_p50_s: float, top_n: int, judge_calls: float,
               budget_s: float, elapsed_s: float) -> dict[str, Any]:
    """The projection check (pure): it exceeds when the projected seconds are strictly above
    :data:`PROJECTION_SHARE` of the budget left."""
    projected = extract_p50_s * remaining_records + judge_p50_s * top_n * judge_calls
    remaining_budget = budget_s - elapsed_s
    return {"after_records": PREFLIGHT_RECORDS, "extract_record_s_p50": extract_p50_s, "judge_s_p50": judge_p50_s,
            "judge_calls_assumed": judge_calls, "top_n": top_n, "remaining_records": remaining_records,
            "projected_s": projected, "budget_s": budget_s, "remaining_budget_s": remaining_budget,
            "threshold": PROJECTION_SHARE, "exceeds": projected > PROJECTION_SHARE * remaining_budget}


def suggested_minutes(estimate_s: float | None) -> int | None:
    return math.ceil(SUGGESTED_FACTOR * estimate_s / 60) if estimate_s is not None else None


def _p50(values: Sequence[float]) -> float | None:
    value = stats.percentile(values, 50)
    return round(value, 6) if value is not None else None


class Progress:
    """What the run has done so far, from every observed model call (thread-safe: the orchestrator's worker threads
    report judge calls), rewritten to ``progress.json`` under the lock (see the module docstring for when). The
    projection check runs once, at the :data:`PREFLIGHT_RECORDS`-th extraction record in phase ``extract``, and is
    the only place :class:`ProjectionExceeded` is raised."""

    def __init__(self, path: Path, *, run_id: str, budget_s: float, started: float, sites: Mapping[str, int],
                 top_n: int, judge_calls: int) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.budget_s = budget_s
        self.started = started
        self.top_n = top_n
        self.judge_calls_assumed = judge_calls
        self.sites = {sid: {"done": 0, "total": n} for sid, n in sites.items()}
        self.total = sum(sites.values())
        self.done = 0
        self.phase = "setup"
        self.seconds: dict[str, list[float]] = {TASK_NAME: [], JUDGE_TASK: []}
        self.warmup: list[float] = []
        self.candidates_total: int | None = None
        self.candidates_done = 0
        self.projection: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    # ------------------------------------------------------------------ events
    def observe(self, boundary: str, task: str, seconds: float) -> None:
        with self._lock:
            self.seconds.setdefault(task, []).append(seconds)
            if task != TASK_NAME:
                return
            self.sites[boundary.split(":", 1)[1]]["done"] += 1
            self.done += 1
            check = self.phase == "extract" and self.done == PREFLIGHT_RECORDS and self.projection is None
            if check:
                self.projection = self._project()
            if check or self.done % PROGRESS_EVERY == 0:
                self._write()
            if check and self.projection["exceeds"]:
                raise ProjectionExceeded(dict(self.projection))

    def set_phase(self, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError("unknown phase") from None
        with self._lock:
            self.phase = phase
            self._write()

    def warmed(self, seconds: Sequence[float]) -> None:
        with self._lock:
            self.warmup = list(seconds)
            self._write()

    def set_candidates(self, total: int) -> None:
        with self._lock:
            self.candidates_total = total
            self._write()

    def candidate_done(self) -> None:
        with self._lock:
            self.candidates_done += 1
            self._write()

    def write(self) -> None:
        with self._lock:
            self._write()

    # ------------------------------------------------------------------ reads
    @property
    def judge_calls(self) -> int:
        with self._lock:
            return len(self.seconds[JUDGE_TASK])

    def measured(self) -> dict[str, Any]:
        with self._lock:
            extract, judge = self.seconds[TASK_NAME], self.seconds[JUDGE_TASK]
            return {"extract_record_s_p50": _p50(extract), "judge_s_p50": _p50(judge),
                    "judge_calls_per_candidate": (round(len(judge) / self.candidates_done, 6)
                                                  if self.candidates_done else None),
                    "model_s": round(sum(extract) + sum(judge), 6)}

    # ------------------------------------------------------------------ under the lock
    def _project(self) -> dict[str, Any]:
        extract_p50 = stats.percentile(self.seconds[TASK_NAME], 50)
        if self.warmup:
            judge_p50, source = stats.percentile(self.warmup, 50), "warmup"
        else:
            judge_p50, source = extract_p50, "extract_p50"
        return {**projection(extract_p50_s=extract_p50, remaining_records=self.total - PREFLIGHT_RECORDS,
                             judge_p50_s=judge_p50, top_n=self.top_n, judge_calls=self.judge_calls_assumed,
                             budget_s=self.budget_s, elapsed_s=self.elapsed()), "judge_source": source}

    def _estimate(self, elapsed: float) -> float | None:
        extract, judge = self.seconds[TASK_NAME], self.seconds[JUDGE_TASK]
        extract_p50 = stats.percentile(extract, 50)
        if extract_p50 is None:
            return None
        judge_p50 = stats.percentile(judge or self.warmup or extract, 50)
        calls = len(judge) / self.candidates_done if self.candidates_done else self.judge_calls_assumed
        remaining = (self.top_n if self.candidates_total is None
                     else max(0, self.candidates_total - self.candidates_done))
        return round(elapsed + extract_p50 * (self.total - self.done) + judge_p50 * remaining * calls, 3)

    def _write(self) -> None:
        elapsed = self.elapsed()
        estimate = self._estimate(elapsed)
        doc = {"schema_version": SCHEMA_VERSION, "kind": "lab_sim_progress", "run_id": self.run_id,
               "phase": self.phase, "elapsed_s": round(elapsed, 3), "budget_s": self.budget_s,
               "records": {"done": self.done, "total": self.total,
                           "sites": {sid: dict(v) for sid, v in sorted(self.sites.items())}},
               "candidates": {"done": self.candidates_done, "total": self.candidates_total},
               "latency_s": {name: {"n": len(values), "p50": _p50(values)} for name, values in (
                   ("extract_record", self.seconds[TASK_NAME]), ("judge_record", self.seconds[JUDGE_TASK]),
                   ("judge_warmup", self.warmup))},
               "projection": self.projection, "estimate_s": estimate, "suggested_minutes": suggested_minutes(estimate)}
        write_json_atomic(self.path, doc)


class ObservedRuntime(Runtime):
    """A :class:`~mycelic.collective.inference.runtime.Runtime` that reports the wall seconds of every ``run`` (its
    repair attempt included, a failure too) to ``observer``; ``single`` is not observed.

    It also memoizes every extraction (``TASK_NAME``) ``run``: the key is the sha256 of the canonical task name,
    endpoint and payload (``model_payload`` is a function of the record, so a record has the same key in the planted
    and the control world), the value the result or the ``InferenceError`` it raised. While ``replaying`` (the no-plant
    control), an extraction found in the memo returns a copy of its result or raises its error again, without a model
    call, a ledger row or an observation (``replayed``); one not found is called through and ledgered but not observed
    (``replay_misses``), so the planted run's progress and projection stay its own."""

    def __init__(self, config: RoutingConfig, *, observer: Progress, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.observer = observer
        self.memo: dict[str, tuple[str, Any]] = {}
        self.replaying = False
        self.replayed = 0
        self.replay_misses = 0

    def run(self, task: TaskSpec, payload: dict[str, Any], schema: Any, *, ref: str,
            endpoint: str | None = None) -> dict[str, Any]:
        if task.name != TASK_NAME:
            return self._observed(task, payload, schema, ref, endpoint)
        key = sha256_hex(canonical_bytes({"task": task.name, "endpoint": endpoint, "payload": payload}))
        if not self.replaying:
            try:
                result = self._observed(task, payload, schema, ref, endpoint)
            except InferenceError as exc:
                self.memo[key] = ("error", exc)
                raise
            self.memo[key] = ("ok", copy.deepcopy(result))
            return result
        if key in self.memo:
            self.replayed += 1
            outcome, value = self.memo[key]
            if outcome == "error":
                raise value
            return copy.deepcopy(value)
        self.replay_misses += 1
        return super().run(task, payload, schema, ref=ref, endpoint=endpoint)

    def _observed(self, task: TaskSpec, payload: dict[str, Any], schema: Any, ref: str,
                  endpoint: str | None) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            return super().run(task, payload, schema, ref=ref, endpoint=endpoint)
        finally:
            self.observer.observe(self.boundary, task.name, time.perf_counter() - t0)


# --------------------------------------------------------------------------------------------------- pushdown

def pushdown_label(key: str, week: str, labels: Mapping[str, Any]) -> str:
    """E2's label rule (``experiments.e2_pushdown._label`` at b861362): ``true`` when the key is a pattern key and the
    week lies in that pattern's found window, ``decoy`` for a decoy key, else ``background``."""
    for p in labels["patterns"]:
        if p["key"] == key and p["found_from"] <= week <= p["found_to"]:
            return "true"
    if any(key in d["keys"] for d in labels["decoys"]):
        return "decoy"
    return "background"


def _used_body(store: CollectiveStore, question_id: str, used: Mapping[str, Any]) -> dict[str, Any]:
    """The stored body of one used verdict, as E2's ``_body`` reads it."""
    for v in store.pd_verdicts(question_id):
        if v.site == used["site"] and v.seq == used["seq"]:
            return strict_load(v.body)
    raise AssertionError("a used verdict is not stored: a bug")


def _pushdown(model: Pipeline, x_model: dict[str, Any], runtimes: Mapping[str, Runtime], clock: SimClock,
              labels: Mapping[str, Any], weeks: Sequence[str], args: argparse.Namespace,
              progress: Progress) -> dict[str, Any]:
    salt = args.tie_salt
    first, last = weeks[args.eval_from], weeks[args.eval_to]
    model.store.save_run(x_model)
    detected = [c for c in x_model["candidates"]
                if c["first_candidate_week"] is not None and first <= c["first_candidate_week"] <= last]
    detected.sort(key=lambda c: (-c["snapshot"]["score"], sha256_hex(f"{salt}|{c['key']}")))
    chosen = detected[:args.top_n]
    progress.set_candidates(len(chosen))
    verifiers = {sid: SiteVerifier(model.sites[sid], runtime=runtimes[sid], clock=clock, demo_seed=args.seed)
                 for sid in model.sites}
    orchestrator = Orchestrator(model.store, handlers={sid: v.answer for sid, v in verifiers.items()}, clock=clock,
                                deadline_seconds=args.deadline_seconds)
    statuses = dict.fromkeys(STATUSES, 0)
    verdicts: dict[str, Any] = {"confirm": 0, "refute": 0, "unknown": dict.fromkeys(UNKNOWN_REASONS, 0)}
    items = []
    for as_of, key, c in sorted(((c["snapshot"]["as_of"], c["key"], c) for c in chosen), key=lambda w: w[:2]):
        clock.set(f"{as_of}T12:00:00Z")
        conclusion = orchestrator.verify_stored(x_model["run_id"], key)
        gate_doc = conclusion.body["gate"]
        statuses[conclusion.status] += 1
        for u in gate_doc["used"]:
            if u["verdict"] != "unknown":
                verdicts[u["verdict"]] += 1
                continue
            body = _used_body(model.store, conclusion.question_id, u)
            reason = body.get("reason") or ("degraded" if body.get("quality") == "degraded" else "none")
            verdicts["unknown"][reason] += 1
        items.append({"key": key, "label": pushdown_label(key, c["snapshot"]["week"], labels),
                      "week": c["snapshot"]["week"], "status": conclusion.status,
                      "score_pushdown": STATUS_RANK[conclusion.status] * STATUS_SCALE
                      + gate_doc["support"]["support_lb"], "score_stats_only": c["snapshot"]["score"]})
        progress.candidate_done()
    relevant = [i["label"] == "true" for i in items]
    return {"detected": len(detected), "n": len(items), "n_true": sum(relevant),
            "labels": {label: sum(1 for i in items if i["label"] == label) for label in LABELS},
            "statuses": statuses, "verdicts": verdicts, "timeouts": verdicts["unknown"]["timeout"],
            "judge_calls": progress.judge_calls,
            "ap_pushdown": stats.tie_averaged_ap([i["score_pushdown"] for i in items], relevant),
            "ap_stats_only": stats.tie_averaged_ap([i["score_stats_only"] for i in items], relevant),
            "items": items}


# --------------------------------------------------------------------------------------------------- the scan

def scan_pipeline(pack: FrozenPack, workdir: Path, site_ids: Sequence[str],
                  narratives: Sequence[str]) -> dict[str, Any]:
    """The raw-text scan of a pipeline directory (:data:`SCAN_METHOD`); ``passed`` needs no overlap in what crossed
    and an overlap in the positive control."""
    edge, hq = Path(workdir) / "edge", Path(workdir) / "hq"
    artifacts = []
    for sid in site_ids:
        artifacts.append(Artifact("site_egress_log", f"edge/site-{sid}.egress.jsonl",
                                  path=edge / f"site-{sid}.egress.jsonl"))
        ingress = edge / f"site-{sid}.ingress.jsonl"
        if ingress.exists():
            artifacts.append(Artifact("site_ingress_log", f"edge/{ingress.name}", path=ingress))
    artifacts.append(Artifact("hq", "hq", path=hq))
    for name in ("receive.jsonl", "questions.jsonl"):
        for number, row in enumerate(read_log(hq / name), start=1):
            artifacts.append(Artifact("hq_rows", f"hq/{name}#{number}", data=canonical_bytes(row["body"])))
    manifest = Manifest(pack_id=pack.id, config_hash=pack.config_hash, prefix=CANARY_PREFIX, canaries=())
    crossed = scan(artifacts, manifest, narratives, pack)
    db = edge / f"site-{site_ids[0]}.sqlite3"
    control = scan([Artifact("positive_control", f"edge/{p.name}", path=p)
                    for p in (db, db.with_name(db.name + "-wal")) if p.exists()], manifest, narratives, pack)
    overlap, positive = crossed["shingle_overlap_bytes"], control["shingle_overlap_bytes"]
    return {"method": SCAN_METHOD, "artifacts": len(artifacts),
            "bytes": sum(entry["bytes"] for entry in crossed["artifact_classes"].values()),
            "shingle_overlap_bytes": overlap, "positive_control_bytes": positive,
            "passed": overlap == 0 and positive > 0}


# --------------------------------------------------------------------------------------------------- the scorecard

_O, _A, _T = harness.obj_schema, harness.arr_schema, harness.typed_schema
_HEX = _T("string", pattern="[0-9a-f]{64}")
_STR = _T("string")
_NSTR = _T("string", nullable=True)
_NAT = _T("integer", minimum=0)
_NNAT = _T("integer", nullable=True, minimum=0)
_POS = _T("integer", minimum=1)
_INT = _T("integer")
_NINT = _T("integer", nullable=True)
_NUM = _T("number")
_NNUM = _T("number", nullable=True)
_BOOL = _T("boolean")
_NBOOL = _T("boolean", nullable=True)
_WEEK = _T("string", pattern="[0-9]{4}-W[0-9]{2}")


def _const(value: Any) -> dict[str, Any]:
    return _T({bool: "boolean", int: "integer", str: "string"}[type(value)], const=value)


def _enum(values: Sequence[str]) -> dict[str, Any]:
    return _T("string", enum=list(values))


def _channel_schema(name: str) -> dict[str, Any]:
    """The collective harness's own channel block (``harness._CHANNEL_BLOCK``, so the scorecard tracks whatever the
    installed harness writes), with this channel's label and ranking as constants."""
    block = copy.deepcopy(harness._CHANNEL_BLOCK)
    block["properties"].update(label=_const(SIM_CHANNEL_LABELS[name]), ranked=_const(name != "rules"))
    return block


@lru_cache(maxsize=None)
def _schema(site_ids: tuple[str, ...]) -> schemacheck.Schema:
    latency = _O({"n": _NAT, "p50_ms": _NNUM, "p95_ms": _NNUM})
    outcome = _O({"found": _BOOL, "first_alert_week": _NSTR, "delay_weeks": _NINT, "lead_weeks": _NINT})
    site = _O({"ingested": _NAT, "records": _NAT, "claims": _NAT, "model": _NAT, "fallback": _NAT,
               "fallback_share": _NNUM, "errors": _O({k: _NAT for k in EXTRACTION_ERROR_KINDS}),
               "drops": _O({r: _NAT for r in DROP_REASONS}), "invalid_claims": _NAT})
    return schemacheck.compile(_O({
        "kind": _const("lab_sim_scorecard"), "schema_version": _const(SCHEMA_VERSION), "run_id": _STR,
        "created_at": _STR, "status": _enum(STATUS_VALUES),
        "stamps": _O({"synthetic": _const(True), "internal_only": _const(True), "blind": _const(False),
                      "measurement": _BOOL, "measurement_reasons": _A(_enum(list(SIM_MEASUREMENT_REASONS))),
                      "extractor": _STR, "data_label": _const("synthetic"), "same_author_pack": _BOOL}),
        "world": _O({"pack": _STR, "pack_version": _STR, "config_hash": _HEX, "plant": _enum(list(PLANTS)),
                     "plant_sha256": _HEX, "seed": _NAT, "site_ids": _A(_STR), "weeks": _POS, "start": _STR,
                     "eval_from_week": _WEEK, "eval_to_week": _WEEK, "evaluation_weeks": _POS, "grace_weeks": _NAT,
                     "world_digest": _HEX, "records": _O({"generated": _NAT, "planted": _NAT, "total": _NAT}),
                     "patterns": _NAT, "decoys": _NAT}),
        "settings": _O({"top_n": _POS, "tie_salt": _STR, "bootstrap_b": _POS, "bootstrap_seed": _INT,
                        "deadline_seconds": _NUM}),
        "endpoint": _O({"name": _STR, "provider": _STR, "boundary": _STR, "model": _STR, "routing_sha256": _HEX,
                        "listing": _O({"ok": _BOOL, "http_status": _NINT, "fake": _BOOL, "ids": _A(_STR)})}),
        "extraction": _O({"sites": _O({sid: site for sid in site_ids}), "records": _NAT, "fallback": _NAT,
                          "fallback_share": _NNUM, "max_fallback_share": _NUM, "passed": _BOOL}, nullable=True),
        "latency": _O({TASK_NAME: latency, JUDGE_TASK: latency}),
        "channels": _O({c: _channel_schema(c) for c in CHANNELS}, nullable=True),
        "patterns": _T("array", nullable=True, items=_O({
            "id": _STR, "key": _STR, "visibility": _enum(VISIBILITIES), "sites": _A(_STR),
            "outcomes": _O({c: outcome for c in CHANNELS})})),
        "lifts": _O({name: harness._LIFT for name, _, _ in LIFTS}, nullable=True),
        "by_construction": _T("array", nullable=True, items=_O({
            "channel": _enum(list(BY_CONSTRUCTION_CHANNELS)), "statement": _const(harness.BY_CONSTRUCTION_STATEMENT),
            "label": _const(harness.BY_CONSTRUCTION_LABEL), "visibility": _const("narrative_only"),
            "recall": _NNUM})),
        "control": _O({"world": _const("no-plant"), "records": _NAT, "replayed_calls": _NAT, "replay_misses": _NAT},
                      nullable=True),
        "pushdown": _O({
            "detected": _NAT, "n": _NAT, "n_true": _NAT, "labels": _O({label: _NAT for label in LABELS}),
            "statuses": _O({s: _NAT for s in STATUSES}),
            "verdicts": _O({"confirm": _NAT, "refute": _NAT, "unknown": _O({r: _NAT for r in UNKNOWN_REASONS})}),
            "timeouts": _NAT, "judge_calls": _NAT, "ap_pushdown": _NNUM, "ap_stats_only": _NNUM,
            "items": _A(_O({"key": _STR, "label": _enum(LABELS), "week": _WEEK, "status": _enum(STATUSES),
                            "score_pushdown": _NUM, "score_stats_only": _NUM}))}, nullable=True),
        "raw_text_crossed": _NNAT,
        "scan": _O({"method": _const(SCAN_METHOD), "artifacts": _NAT, "bytes": _NAT, "shingle_overlap_bytes": _NAT,
                    "positive_control_bytes": _NAT, "passed": _BOOL}, nullable=True),
        "projection": _O({
            "checked": _BOOL, "after_records": _const(PREFLIGHT_RECORDS), "extract_record_s_p50": _NNUM,
            "judge_s_p50": _NNUM, "judge_source": _T("string", nullable=True, enum=["warmup", "extract_p50", None]),
            "judge_calls_assumed": _POS, "top_n": _POS, "remaining_records": _NINT, "projected_s": _NNUM,
            "budget_s": _NUM, "remaining_budget_s": _NNUM, "threshold": _NUM, "exceeds": _NBOOL,
            "measured": _O({"extract_record_s_p50": _NNUM, "judge_s_p50": _NNUM, "judge_calls_per_candidate": _NNUM,
                            "model_s": _NUM}),
            "estimate_s": _NNUM, "suggested_minutes": _NNAT, "suggested_basis": _enum(["measured", "projected"])}),
        "code": _O({"code_commit": _STR, "code_dirty": _NBOOL, "collective_code_hash": _HEX, "lab_code_hash": _HEX}),
        "timings": _O({key: _NUM if key == "total_s" else _NNUM for key in TIMING_KEYS}),
        "notes": _A(_enum(list(SIM_NOTES))),
        "paths": _O({name: _STR for name in ("run_dir", "routing", "plant")}),
        "content_hash_excludes": _A(_STR), "content_hash": _HEX}))


RESULT_BLOCKS = ("extraction", "channels", "patterns", "lifts", "by_construction", "pushdown", "raw_text_crossed",
                 "scan")


def scorecard_problems(doc: Any) -> list[tuple[str, str]]:
    """The schema's problems (``code.code_dirty`` may also be ``"unknown"``), then the cross rule: a complete run has
    every result block, a skipped one none."""
    site_ids = doc.get("world", {}).get("site_ids") if isinstance(doc, dict) else None
    ids = tuple(site_ids) if isinstance(site_ids, list) and all(isinstance(s, str) for s in site_ids) else ()
    problems = harness.union_problems(_schema(ids), doc, ("code", "code_dirty"))
    if isinstance(doc, dict):
        status = doc.get("status")
        for block in RESULT_BLOCKS:
            if (status == "complete" and doc.get(block) is None) or (status == "skipped_projection"
                                                                    and doc.get(block) is not None):
                problems.append((f"$.{block}", "status"))
    return problems


def content_hash(doc: Mapping[str, Any]) -> str:
    excluded = {p[2:] for p in CONTENT_HASH_EXCLUDES}
    return sha256_hex(canonical_bytes({k: doc[k] for k in sorted(doc) if k not in excluded}))


def _latency(rows: Sequence[Mapping[str, Any]], task: str) -> dict[str, Any]:
    values = [r["latency_ms"] for r in rows if r["task"] == task and r["ok"] is True
              and isinstance(r["latency_ms"], (int, float)) and not isinstance(r["latency_ms"], bool)]
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None}
    return {"n": len(values), "p50_ms": round(stats.percentile(values, 50), 3),
            "p95_ms": round(stats.percentile(values, 95), 3)}


def _extraction(summaries: Mapping[str, ExtractSummary], ingested: Mapping[str, int], extractor: str,
                site_ids: Sequence[str]) -> dict[str, Any]:
    sites = {}
    for sid in site_ids:
        s = summaries[sid]
        fallback = s.extractors.get(FALLBACK_EXTRACTOR, 0)
        sites[sid] = {"ingested": ingested[sid], "records": s.records, "claims": s.claims,
                      "model": s.extractors.get(extractor, 0), "fallback": fallback,
                      "fallback_share": round(fallback / s.records, 6) if s.records else None,
                      "errors": {k: s.errors.get(k, 0) for k in EXTRACTION_ERROR_KINDS},
                      "drops": {r: s.drops.get(r, 0) for r in DROP_REASONS}, "invalid_claims": s.invalid_claims}
    records = sum(v["records"] for v in sites.values())
    fallback = sum(v["fallback"] for v in sites.values())
    only = all(set(summaries[sid].extractors) <= {extractor, FALLBACK_EXTRACTOR} for sid in site_ids)
    passed = (only and records > 0
              and fallback * 100 <= round(MAX_SIM_FALLBACK_SHARE * 100) * records)
    return {"sites": sites, "records": records, "fallback": fallback,
            "fallback_share": round(fallback / records, 6) if records else None,
            "max_fallback_share": MAX_SIM_FALLBACK_SHARE, "passed": passed}


# --------------------------------------------------------------------------------------------------- the run

@dataclass(frozen=True)
class _Checked:
    out_dir: Path
    plant: LabPlant
    pack: FrozenPack
    spec: PlantSpec
    world: World
    site_ids: list[str]
    config: RoutingConfig | None


def _routing(path: str, *, check_env: bool) -> RoutingConfig:
    try:
        config = load_routing(path, tasks=[TASK_NAME, JUDGE_TASK], check_env=check_env)
    except ConfigError as err:
        raise UsageError(f"routing: {err}") from None
    for task in (TASK_NAME, JUDGE_TASK):
        route = config.routes[task]
        endpoint = config.endpoints[route.endpoint]
        if endpoint.boundary != "any-simulated" or endpoint.provider != "openai_compat" or route.escalate_to:
            raise UsageError(ROUTING_PROBLEM) from None
    return config


def _check(args: argparse.Namespace, dry: DryRun | None) -> _Checked:
    """Every refusal, in the documented order, before anything is written; in a dry run a missing routing file is a
    need."""
    out_dir = run_dir(args.runs_dir, KIND, check_run_id(args.run_id))
    for flag, path in (("--runs-dir", args.runs_dir), ("--routing", args.routing)):
        name = forbidden_root(path)
        if name is not None:
            raise UsageError(f"{flag} must not lie inside {name}/") from None
    if args.plant not in PLANTS:
        raise UsageError(f"--plant must be one of {', '.join(sorted(PLANTS))}") from None
    lab_plant = PLANTS[args.plant]
    if not 0 <= args.seed <= SEED_MAX:
        raise UsageError(f"--seed must be in [0, {SEED_MAX}]") from None
    pack = load_pack(lab_plant.pack)
    low = min_weeks(pack)
    if not low <= args.weeks <= MAX_WEEKS:
        raise UsageError(f"--weeks must be in [{low} (baseline_weeks + window_weeks), {MAX_WEEKS}]") from None
    harness.check_settings(pack, sites=SITES, weeks=args.weeks, eval_from=args.eval_from, eval_to=args.eval_to,
                           grace_weeks=args.grace_weeks)
    if TIE_SALT_RE.fullmatch(args.tie_salt) is None:
        raise UsageError("--tie-salt must be 1 to 128 printable ASCII characters") from None
    if not 1 <= args.top_n <= MAX_TOP_N:
        raise UsageError(f"--top-n must be in [1, {MAX_TOP_N}]") from None
    if args.bootstrap_b < harness.MIN_BOOTSTRAP_B:
        raise UsageError(f"--bootstrap-b must be >= {harness.MIN_BOOTSTRAP_B}") from None
    if not (math.isfinite(args.budget_seconds) and args.budget_seconds > 0):
        raise UsageError("--budget-seconds must be a finite number > 0") from None
    if not (math.isfinite(args.deadline_seconds) and 0 < args.deadline_seconds <= DEADLINE_SECONDS):
        raise UsageError(f"--deadline-seconds must be in (0, {DEADLINE_SECONDS:.0f}]") from None
    config = None
    if dry is not None and not Path(args.routing).is_file():
        dry.need(f"routing file {args.routing}")
    else:
        config = _routing(args.routing, check_env=dry is None)
    spec = load_plant(lab_plant.path, pack)
    world = generate(pack, args.seed, SITES, args.weeks)
    site_ids = site_ids_of(pack)
    check_plant(spec, pack, site_ids=site_ids, master_data=world.master_data, n_weeks=args.weeks,
                eval_from=args.eval_from, eval_to=args.eval_to)
    return _Checked(out_dir=out_dir, plant=lab_plant, pack=pack, spec=spec, world=world, site_ids=site_ids,
                    config=config)


def _since(t0: float) -> float:
    return round(time.perf_counter() - t0, 3)


def _channels(pack: FrozenPack, model: Pipeline, lexical: Pipeline, records: Sequence[Mapping[str, Any]],
              master_data: Mapping[str, Mapping[str, Sequence[str]]], weeks: Sequence[str],
              salt: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """(the detection result of each of :data:`DETECTOR_CHANNELS`, every channel's alert events), with X1's
    functions."""
    hq = hq_results(model.store, as_of=model.as_of, tie_salt=salt)
    results = {"X_model": hq["X"], "X_lexical": hq_results(lexical.store, as_of=lexical.as_of, tie_salt=salt)["X"],
               "S": hq["S"],
               "R_mf": exact_result(pack, model.org, r_mf_cells(pack, records, master_data=master_data,
                                                                last_week=weeks[-1]),
                                    as_of=model.as_of, run_channel="S", tie_salt=salt),
               "U": exact_result(pack, model.org, u_cells(model), as_of=model.as_of, run_channel="X", tie_salt=salt)}
    events = {name: detector_alerts(result) for name, result in results.items()}
    events.update(single_site=single_site_alerts(model, tie_salt=salt), rules=rule_alerts(hq["X"]))
    return results, events


def beyond_lexical(found: Mapping[str, Mapping[str, Sequence[bool]]]) -> list[str]:
    """The patterns ``X_model`` found and ``X_lexical`` missed in the lifts' count (``found`` as :func:`_score` gives
    it: one bool per pattern, net of chance with the control). The lexical extractor is exact on generator text, so
    each is a find of the model's extraction errors, not of better reading (``model_beyond_exact``)."""
    return [pid for pid, hits in found["X_model"].items()
            if any(m and not x for m, x in zip(hits, found["X_lexical"][pid]))]


def by_construction(channels: Mapping[str, Mapping[str, Any]],
                    patterns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """X1's ``by_construction`` entries for these channels: with any ``narrative_only`` pattern, S and R_mf read no
    narrative, so planted narrative_only records add nothing to their cells and their recall on those patterns is
    the harness's "by construction, not a result" (``harness.BY_CONSTRUCTION_LABEL``); none without such a
    pattern."""
    if not any(p["visibility"] == "narrative_only" for p in patterns):
        return []
    return [{"channel": name, "statement": harness.BY_CONSTRUCTION_STATEMENT, "label": harness.BY_CONSTRUCTION_LABEL,
             "visibility": "narrative_only", "recall": channels[name]["by_visibility"]["narrative_only"]["recall"]}
            for name in BY_CONSTRUCTION_CHANNELS]


def _score(results: Mapping[str, Any], events: Mapping[str, list[dict[str, Any]]],
           control: Mapping[str, list[dict[str, Any]]] | None, labels: Mapping[str, Any], index: Mapping[str, int],
           evaluation_weeks: int, seed: int, first: str, last: str
           ) -> tuple[dict[str, Any], dict[str, dict[str, list[bool]]]]:
    """(every channel's block, every channel's found per pattern for the lifts), with the harness's functions. With
    the no-plant control (``control``, the control world's windowed events), each block also scores the control's and
    the chance finds and, for the detector channels, a stale chain's candidacy as X1 does (each candidate week of the
    stale chains' keys inside the evaluation weeks); the lifts then compare finds net of chance (``harness.net_found``).
    Without it, the blocks and the lifts are the raw finds."""
    patterns = labels["patterns"]
    if control is None:
        channels = {name: harness.channel_block(name, SIM_CHANNEL_LABELS[name], {seed: events[name]}, labels, index,
                                                evaluation_weeks) for name in CHANNELS}
        found = {name: {p["id"]: [harness.pattern_outcome(events[name], p, index)["found"]] for p in patterns}
                 for name in CHANNELS}
        return channels, found
    stale = {key for d in labels["decoys"] if d["class"] == "stale_chain" for key in d["keys"]}
    candidates = {name: ([e for e in harness.eval_events(baselines.detector_candidates(results[name]), first, last)
                          if e["key"] in stale] if name in DETECTOR_CHANNELS else None) for name in CHANNELS}
    channels = {name: harness.channel_block(name, SIM_CHANNEL_LABELS[name], {seed: events[name]}, labels, index,
                                            evaluation_weeks, {seed: control[name]}, {seed: candidates[name]})
                for name in CHANNELS}
    found = {name: {p["id"]: [harness.net_found(events[name], control[name], p, index)] for p in patterns}
             for name in CHANNELS}
    return channels, found


def run(args: argparse.Namespace, c: _Checked, started: float) -> int:
    pack, world, site_ids, config = c.pack, c.world, c.site_ids, c.config
    assert config is not None
    out_dir = c.out_dir
    timings: dict[str, float | None] = dict.fromkeys(TIMING_KEYS)
    weeks = world_weeks(pack.generator["start"], args.weeks)
    index = {w: i for i, w in enumerate(weeks)}
    first, last = weeks[args.eval_from], weeks[args.eval_to]
    evaluation_weeks = args.eval_to - args.eval_from + 1

    # 1. setup
    t0 = time.perf_counter()
    planted = plant(world, c.spec, pack)
    records = list(world.records) + list(planted.records)
    labels = labels_doc(c.spec, pack, weeks=weeks, eval_from=args.eval_from, eval_to=args.eval_to,
                        grace_weeks=args.grace_weeks)
    labels = {**labels, "seeds": [{"seed": args.seed, "planted_records": len(planted.records)}]}
    out_dir.mkdir(parents=True)
    write_json_atomic(out_dir / "labels.json", labels)
    progress = Progress(out_dir / "progress.json", run_id=args.run_id, budget_s=args.budget_seconds, started=started,
                        sites={sid: sum(1 for r in records if r["site"] == sid) for sid in site_ids},
                        top_n=args.top_n, judge_calls=c.plant.judge_calls_per_candidate)
    progress.write()
    timings["setup_s"] = _since(t0)

    # 2. the model listing
    t0 = time.perf_counter()
    routed = sorted(config.routed_endpoint_names())
    listings = {name: list_models(config.endpoints[name]) for name in routed}
    models_fake = any(listing["fake"] for listing in listings.values())
    endpoint = config.endpoints[config.routes[TASK_NAME].endpoint]
    extractor = f"model:{endpoint.name}"
    timings["list_models_s"] = _since(t0)

    # 3 to 9
    (out_dir / "edge").mkdir()
    clock = SimClock(pipeline_stamp(pack, weeks))
    runtimes: dict[str, ObservedRuntime] = {}
    model = lexical = control_model = control_lexical = None
    skipped: dict[str, Any] | None = None
    extraction = channels = patterns = lifts = constructed = pushdown = scanned = control = None
    beyond_exact: list[str] = []
    try:
        for sid in site_ids:
            runtimes[sid] = ObservedRuntime(config, observer=progress, boundary=f"site:{sid}",
                                            ledger_path=out_dir / "edge" / f"site-{sid}.ledger.jsonl",
                                            run_id=args.run_id, clock=clock, data_label="synthetic", simulation=True)
        progress.set_phase("warmup")
        t0 = time.perf_counter()
        record = next(r for r in records if r["site"] == site_ids[0])
        warm = []
        for i in range(JUDGE_WARMUP_CALLS):
            attempt = runtimes[site_ids[0]].single(judge_task(), judge_example(pack, record, record["narrative"]),
                                                   judge_schema(), ref=f"jw:{i}")
            if attempt.row["latency_ms"] is not None:
                warm.append(attempt.row["latency_ms"] / 1000)
        progress.warmed(warm)
        timings["warmup_s"] = _since(t0)

        progress.set_phase("extract")
        t0 = time.perf_counter()
        try:
            model, summaries, ingested = run_pipeline(pack, records, site_ids=site_ids,
                                                      master_data=world.master_data, weeks=weeks,
                                                      workdir=out_dir / "work" / "model", runtimes=runtimes)
        except ProjectionExceeded as exc:
            skipped = exc.projection
        timings["model_pipeline_s"] = _since(t0)

        if skipped is None:
            extraction = _extraction(summaries, ingested, extractor, site_ids)
            progress.set_phase("lexical")
            t0 = time.perf_counter()
            lexical = baselines.run_pipeline(pack, records, site_ids=site_ids, master_data=world.master_data,
                                             weeks=weeks, workdir=out_dir / "work" / "lexical")
            timings["lexical_pipeline_s"] = _since(t0)

            control_events = None
            if HARNESS_CONTROL:
                progress.set_phase("control")
                t0 = time.perf_counter()
                for runtime in runtimes.values():
                    runtime.replaying = True
                control_model, _, _ = run_pipeline(pack, list(world.records), site_ids=site_ids,
                                                   master_data=world.master_data, weeks=weeks,
                                                   workdir=out_dir / "work" / "control-model", runtimes=runtimes)
                control_lexical = baselines.run_pipeline(pack, list(world.records), site_ids=site_ids,
                                                         master_data=world.master_data, weeks=weeks,
                                                         workdir=out_dir / "work" / "control-lexical")
                for runtime in runtimes.values():
                    runtime.replaying = False
                _, control_events = _channels(pack, control_model, control_lexical, list(world.records),
                                              world.master_data, weeks, args.tie_salt)
                control_events = {name: harness.eval_events(e, first, last) for name, e in control_events.items()}
                control = {"world": "no-plant", "records": len(world.records),
                           "replayed_calls": sum(r.replayed for r in runtimes.values()),
                           "replay_misses": sum(r.replay_misses for r in runtimes.values())}
                timings["control_pipeline_s"] = _since(t0)

            progress.set_phase("channels")
            t0 = time.perf_counter()
            results, events = _channels(pack, model, lexical, records, world.master_data, weeks, args.tie_salt)
            events = {name: harness.eval_events(e, first, last) for name, e in events.items()}
            channels, found = _score(results, events, control_events, labels, index, evaluation_weeks, args.seed,
                                     first, last)
            if HARNESS_INTERVALS:
                channels = {name: {**block, **harness.interval_blocks(name, block, found[name], B=args.bootstrap_b,
                                                                     seed=args.bootstrap_seed)}
                            for name, block in channels.items()}
            outcomes = {(p["id"], name): harness.pattern_outcome(events[name], p, index)
                        for p in labels["patterns"] for name in CHANNELS}
            patterns = [{"id": p["id"], "key": p["key"], "visibility": p["visibility"], "sites": list(p["sites"]),
                         "outcomes": {name: outcomes[(p["id"], name)] for name in CHANNELS}}
                        for p in labels["patterns"]]
            lifts = {name: harness.lift(name, found[a], found[b], B=args.bootstrap_b, seed=args.bootstrap_seed)
                     for name, a, b in LIFTS}
            beyond_exact = beyond_lexical(found)
            constructed = by_construction(channels, labels["patterns"])
            timings["channels_s"] = _since(t0)

            progress.set_phase("pushdown")
            t0 = time.perf_counter()
            pushdown = _pushdown(model, results["X_model"], runtimes, clock, labels, weeks, args, progress)
            timings["pushdown_s"] = _since(t0)

            progress.set_phase("scan")
            t0 = time.perf_counter()
            scanned = scan_pipeline(pack, out_dir / "work" / "model", site_ids, [r["narrative"] for r in records])
            timings["scan_s"] = _since(t0)
    finally:
        for runtime in runtimes.values():
            runtime.close()
        for pipeline in (model, lexical, control_model, control_lexical):
            if pipeline is not None:
                pipeline.close()

    rows = [row for path in sorted((out_dir / "edge").glob("site-*.ledger.jsonl")) for row in read_ledger(path)]
    flag = measurement_flag([config.endpoints[name] for name in routed], rows, models_fake)
    reasons = [key for key, failing in (("fake_model", not flag),
                                        ("low_participation", extraction is not None and not extraction["passed"]),
                                        ("skipped_projection", skipped is not None)) if failing]
    measurement = flag and extraction is not None and extraction["passed"]
    timings["total_s"] = _since(started)
    measured = progress.measured()
    checked = skipped if skipped is not None else progress.projection
    if skipped is not None:
        estimate, basis = round(skipped["budget_s"] - skipped["remaining_budget_s"] + skipped["projected_s"], 3), \
            "projected"
    else:
        estimate, basis = timings["total_s"], "measured"
    core = {k: (checked or {}).get(k) for k in ("extract_record_s_p50", "judge_s_p50", "judge_source",
                                                "remaining_records", "projected_s", "remaining_budget_s", "exceeds")}
    status = "complete" if skipped is None else "skipped_projection"
    listing = listings[endpoint.name]
    doc: dict[str, Any] = {
        "kind": "lab_sim_scorecard", "schema_version": SCHEMA_VERSION, "run_id": args.run_id,
        "created_at": utc_clock(), "status": status,
        "stamps": {"synthetic": True, "internal_only": True, "blind": False, "measurement": measurement,
                   "measurement_reasons": sorted(reasons), "extractor": extractor, "data_label": "synthetic",
                   "same_author_pack": pack.same_author_as_code},
        "world": {"pack": pack.id, "pack_version": pack.version, "config_hash": pack.config_hash,
                  "plant": c.plant.name, "plant_sha256": c.spec.sha256, "seed": args.seed, "site_ids": list(site_ids),
                  "weeks": args.weeks, "start": pack.generator["start"], "eval_from_week": first,
                  "eval_to_week": last, "evaluation_weeks": evaluation_weeks, "grace_weeks": args.grace_weeks,
                  "world_digest": world_digest(world),
                  "records": {"generated": len(world.records), "planted": len(planted.records),
                              "total": len(records)},
                  "patterns": len(c.spec.patterns), "decoys": len(c.spec.decoys)},
        "settings": {"top_n": args.top_n, "tie_salt": args.tie_salt, "bootstrap_b": args.bootstrap_b,
                     "bootstrap_seed": args.bootstrap_seed, "deadline_seconds": float(args.deadline_seconds)},
        "endpoint": {"name": endpoint.name, "provider": endpoint.provider, "boundary": endpoint.boundary,
                     "model": endpoint.model, "routing_sha256": config.sha256,
                     "listing": {k: listing[k] for k in ("ok", "http_status", "fake", "ids")}},
        "extraction": extraction,
        "latency": {task: _latency(rows, task) for task in (TASK_NAME, JUDGE_TASK)},
        "channels": channels, "patterns": patterns, "lifts": lifts, "by_construction": constructed,
        "control": control, "pushdown": pushdown,
        "raw_text_crossed": scanned["shingle_overlap_bytes"] if scanned is not None else None, "scan": scanned,
        "projection": {"checked": checked is not None, "after_records": PREFLIGHT_RECORDS, **core,
                       "judge_calls_assumed": c.plant.judge_calls_per_candidate, "top_n": args.top_n,
                       "budget_s": float(args.budget_seconds), "threshold": PROJECTION_SHARE, "measured": measured,
                       "estimate_s": estimate, "suggested_minutes": suggested_minutes(estimate),
                       "suggested_basis": basis},
        "code": {"code_commit": code_commit(), "code_dirty": code_dirty(["mycelic/collective", "lab"]),
                 "collective_code_hash": code_hash(),
                 "lab_code_hash": code_hash(sorted((ROOT / "lab").rglob("*.py")), ROOT)},
        "timings": timings,
        "notes": [key for key in SIM_NOTES
                  if (key != "few_patterns" or len(c.spec.patterns) < harness.FEW_PATTERNS)
                  and (key != "model_beyond_exact" or beyond_exact)
                  and (key not in CONTROL_NOTES or key == ("chance_control" if control is not None else "no_control"))],
        "paths": {"run_dir": str(out_dir.resolve()), "routing": str(Path(args.routing).resolve()),
                  "plant": str(c.plant.path)},
        "content_hash_excludes": list(CONTENT_HASH_EXCLUDES),
    }
    doc["content_hash"] = content_hash(doc)
    problems = scorecard_problems(doc)
    if problems:
        raise AssertionError(f"scorecard fails its schema at {problems[0][0]} ({problems[0][1]}): a bug")
    write_json_atomic(out_dir / "scorecard.json", doc)
    progress.set_phase("done" if skipped is None else "skipped_projection")
    raw = doc["raw_text_crossed"]
    print(f"sim: plant={c.plant.name} seed={args.seed} status={status} measurement={str(measurement).lower()} "
          f"raw_text_crossed={'null' if raw is None else raw} -> {out_dir / 'scorecard.json'} "
          "(synthetic, internal only)")
    passed = scanned is not None and scanned["passed"] and extraction is not None and extraction["passed"]
    return 0 if skipped is None and passed else 1


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.sim",
                                description="The multi-site simulation: a model inside each simulated site of a "
                                            "seeded synthetic world (synthetic, internal only).")
    p.add_argument("--plant", required=True, help="a lab plant: " + ", ".join(sorted(PLANTS)))
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--weeks", type=int, required=True)
    p.add_argument("--eval-from", type=int, required=True, help="first evaluation week (0-based index)")
    p.add_argument("--eval-to", type=int, required=True, help="last evaluation week (0-based index)")
    p.add_argument("--grace-weeks", type=int, required=True)
    p.add_argument("--tie-salt", required=True)
    p.add_argument("--top-n", type=int, required=True, help="candidates verified by pushdown")
    p.add_argument("--bootstrap-b", type=int, required=True)
    p.add_argument("--bootstrap-seed", type=int, required=True)
    p.add_argument("--routing", required=True, help="both tasks on any-simulated openai_compat endpoints")
    p.add_argument("--run-id", required=True)
    p.add_argument("--budget-seconds", type=float, required=True, help="the time the run may take")
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--deadline-seconds", type=float, default=DEADLINE_SECONDS,
                   help="each site's pushdown answer deadline")
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    args = _parser().parse_args(argv)
    dry = DryRun(CLI) if args.dry_run else None
    try:
        checked = _check(args, dry)
        if dry is not None:
            for name in ("labels.json", "progress.json", "scorecard.json", "edge/site-<site>.ledger.jsonl", "work/"):
                dry.write(f"{checked.out_dir}/{name}")
            return dry.emit()
    except (UsageError, PlantError, GeneratorError) as exc:
        return fail(str(exc))
    try:
        return run(args, checked, started)
    except KeyboardInterrupt:
        print(f"sim: interrupted; {checked.out_dir} is partial and its run id cannot be reused", file=sys.stderr)
        return 130
    except (EvaluationError, GeneratorError, PlantError, PushdownError, LeakageError) as exc:
        return fail(str(exc))
    except (OSError, StrictJsonError) as exc:
        return fail(f"cannot read or write a run file ({exc.__class__.__name__})")


if __name__ == "__main__":
    sys.exit(main())
