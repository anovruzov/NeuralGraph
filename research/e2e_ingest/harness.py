"""The end-to-end run: the EMERGENCE world through NeuralGraph, Tesseract, the API, the outbox, JetStream and apply
(``docs/collective/E2E-INGEST.md`` sections 1, 4, 8 and 9; S5 and S6).

    python -m research.e2e_ingest.harness run --world 200 --seed 0 --out DIR [--skip-drills] [--suites DIR]
                                               [--top-n 60] [--queries 20]

(``python -m research.e2e_ingest run --size 200 --mode process --out DIR`` is the same command, section 6.)
``--suites DIR`` reads the suites' junit evidence of M2 (``python -m research.e2e_ingest suites --out DIR``)
and runs criterion 10's check over it; without it the run's checks are reported and ``verdict.pass`` is false
with reason ``suites_not_green``. ``--queries 0`` asks no fixed query (the measure job, whose sizes do not drill
criterion 3) and ``--top-n`` bounds the candidate list (the measure job asks 20 at the 2,000-user size, M14).

What one run does, in the order section 8 fixes:

0. **The world.** ``emergence_world.build_emergence_world`` generates the EMERGENCE world (one pack record per
   EMERGENCE record, one site per org SITE node), runs the shipped pipeline (every site ingests its own records,
   extracts them lexically and emits weekly k-suppressed cells through its Boundary; HQ ingests the receive log) and
   the shipped detector. The detection run is re-run here and saved with ``CollectiveStore.save_run``, so the
   bridge can read each candidate's snapshot week from HQ's own store; the ordered candidate list is asserted to be
   byte-identical with the world's.
1. **Reference pass** (drill 1, in process): every question asked of the three sites arm R's order prefers, each
   site on its own thread (M15). Nothing is published; its stores are a copy of the pristine ones.
2. **Main pass** (drill 2, process mode): every site a child process over its persistent graph; HQ asks through
   ``TesseractCoordinator.execute`` with arm R's order as ``preferred_node_ids`` and ``budget.max_nodes = 3``; the
   gate decides; every question, route, verdict and conclusion is written to HQ's store. Half way through the
   questions of the most-routed site its process is SIGKILLed; while it is down the Router's eligibility filter is
   exercised with the failure injector (M4) and one probe question is asked outside the answer matrix; then the
   process is reopened on its persistent graph and store and answers the rest.
3. **Main publish with the broker kill** (drill 3): the bridge publishes one event per ``pd_verdicts`` row and one
   per conclusion through ``publish_api``; after publish k = N // 2 nats-server is killed, the rest is taken by the
   outbox, the broker comes back and everything is applied exactly once.
4. **Signatures** (drill 4): every stream message is read by sequence and checked under the driver's key; then the
   three forgeries of M8 go straight to the broker and are terminated, counted and absent from the database.
5. **Lineage** (drill 5): every conclusion reconstructable, its verdict events witnessed five times (M1) starting
   from the ``sha256`` the fabric itself holds in the event's payload (read back from the service, never from HQ's
   store), the site-side join to the records read on or before ``as_of`` with every audited ref dated, the scan of
   the fabric's database and stream for record text, record refs and narrative prefixes, and (section 4) the
   spike's own leakage scan (``run.py`` ``leakage_check``, narrative shingle overlap) and closed-shape check
   (``crossing_check``) over every byte that crossed a site boundary in the main pass, plus the leakage scan over
   the pipeline's crossing bytes.
6. **Service rebuild** (drill 6): the service SIGKILLed, its database deleted, restarted: the same active memory
   ids, the same verification digests, the three forgeries rejected again by the replay (M5).
7. **Re-ingest** (drill 7): every site ingests its records again (all duplicates) and re-syncs its graph — the same
   node ids, the same count, the same top 10 and the same full order on the 20 fixed queries at every site; then the
   bridge runs again from an empty watermark and creates nothing.

Then the checks of section 9 (``report.py``) and the JSON report of section 5. The harness exits 1 when any check
that ran is not ``ok``, so a CI job fails on it. The partial document is written after every phase (``phase``
named, ``completed`` false), so a job timeout leaves a report to amend from.

Raw records and graph nodes never cross the Boundary: the fabric receives verdicts and conclusions only, and nothing
this module prints, logs or reports is a record's text or ref — every value is an id, a count, a status, a digest or
a number of seconds. Everything is synthetic and stamped ``measurement: false``.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import math
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.detect.detectors import detect
from mycelic.collective.detect.rules import series_key
from mycelic.collective.edge.egress import read_log
from mycelic.collective.edge.records import RecordStore
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.weeks import iso_week, week_sunday
from mycelic.collective.experiments.common import code_dirty, utc_clock, write_json_atomic
from mycelic.collective.jsonio import canonical_bytes, sha256_hex, strict_load
from mycelic.sdk import MycelicClient, MycelicError
from research.routing_spike import wire
from research.routing_spike.hq import Candidate, HqView, plan as hq_plan
from research.routing_spike.run import DIRTY_PATHS as SPIKE_DIRTY_PATHS
from research.routing_spike.run import FORBIDDEN_HQ, crossing_check, leakage_check
from research.routing_spike.site_process import query_text, write_config
from research.routing_spike.world import backup_sqlite, candidate_label, restore_store

from . import drills
from . import emergence_world as ew
from . import hq_tesseract as hqt
from . import measure as ms
from . import report as rp
from . import site_graph as sg
from . import suites as su
from .hq_bridge import HqBridge, HqStoreReader
from .publish_api import AGENT_ID, ORG_ID, VERDICT_EVENT, Publisher, event_ids

ROOT = Path(__file__).resolve().parents[2]
LOCAL_NATS = Path("/root/.local/bin/nats-server")
MODES = ("process", "in_process")
FIXED_QUERIES = 20
FRESHNESS_DAYS = 42
ORACLE_MIN_RECORDS = 3
ORACLE_MIN_USERS = 3
ORACLE_MIN_EVENTS = 3
FORGED_IDS = ("mem_forged_e2e_1", "mem_forged_e2e_2", "mem_forged_e2e_3")
FIXED_QUESTIONS_NAME = "fixed_questions.json"
#: ``MYCELIC_ENTERPRISE.md:579-581``: the published 10,000- and 50,000-user worlds, the denominators of
#: ``world.fraction_of_benchmark``.
BENCHMARK_10K = (9999, 329730)
BENCHMARK_50K = (49999, 1639365)
#: The modules criterion 7 covers and what each must not import (``run.py:80-84`` plus the site module).
GUARDED_MODULES: Mapping[str, tuple[str, ...]] = {
    "hq_tesseract": FORBIDDEN_HQ + ("research.e2e_ingest.site_graph",),
    "hq_bridge": FORBIDDEN_HQ + ("research.e2e_ingest.site_graph",),
    "publish_api": FORBIDDEN_HQ + ("research.e2e_ingest.site_graph", "mycelic.service"),
}
DRILL_FORBIDDEN = ("numpy", "NeuralGraph.research.retrieval", "NeuralGraph.llm_backend", "mycelic.service",
                   "research.routing_spike.site_process", "research.e2e_ingest.site_graph")


class HarnessError(RuntimeError):
    """The run could not go on (a missing broker binary, a world that is not the brief's, a refused store row)."""


# --------------------------------------------------------------------------------------------------- small helpers

def nats_server_bin() -> str:
    """``MYCELIC_NATS_SERVER_BIN``, then PATH, then the sandbox's local install. A miss is a failure, never a
    skip (M11): a run without a real broker is not a run."""
    for candidate in (os.environ.get("MYCELIC_NATS_SERVER_BIN"), shutil.which("nats-server"), str(LOCAL_NATS)):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise HarnessError("nats-server not found: set MYCELIC_NATS_SERVER_BIN or install it")


def nats_binary_version(nats_bin: str) -> str:
    """The numeric part of ``nats-server --version`` (``v2.10.22`` -> ``2.10.22``)."""
    out = subprocess.run([nats_bin, "--version"], capture_output=True, text=True, timeout=30, check=True).stdout
    return out.strip().split()[-1].lstrip("v")


def class_name(obj: Any) -> str:
    return f"{type(obj).__module__}.{type(obj).__qualname__}"


def seconds_between(created_at: str, applied_at: str) -> float | None:
    """``applied_at - created_at`` in seconds for two ISO 8601 stamps the service wrote."""
    try:
        a = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(applied_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds()


class StepClock:
    """One ISO 8601 second per call, so HQ's rows are ordered by ``created_at`` as the bridge reads them."""

    def __init__(self, start: str) -> None:
        self._t = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    def __call__(self) -> str:
        self._t += timedelta(seconds=1)
        return self._t.strftime("%Y-%m-%dT%H:%M:%SZ")


class TimedEndpoint:
    """One site's endpoint with the wall seconds of every request kept harness side (section 5,
    ``routing.question_to_verdict``). It adds nothing to what crosses: the inner endpoint does the framing, the
    logging and the transport."""

    def __init__(self, inner: Any, site: str, sink: list[dict[str, Any]]) -> None:
        self.inner = inner
        self.site = site
        self._sink = sink

    def request(self, kind: str, body: bytes = b"") -> bytes:
        t0 = time.perf_counter()
        try:
            return self.inner.request(kind, body)
        finally:
            self._sink.append({"site": self.site, "kind": kind, "seconds": time.perf_counter() - t0})

    @property
    def pid(self) -> int:
        return int(self.inner.pid)

    def close(self) -> None:
        self.inner.close()


def own_records(store_path: str | Path) -> dict[str, str]:
    """``record_ref -> received_date`` of a site's own records (``forwarded_in = 0``), read ``mode=ro``: the count
    criterion 8 calls ``ingested`` (M16) and the dates the lineage audit bounds by ``as_of``."""
    conn = sqlite3.connect(f"file:{Path(store_path)}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT record_ref, received_date FROM records WHERE forwarded_in = 0 "
                                 "ORDER BY seq").fetchall())
    finally:
        conn.close()


def site_store_path(edge: Path, site: str) -> Path:
    return Path(edge) / f"site-{site}.sqlite3"


# --------------------------------------------------------------------------------------------------- c7's guards

def static_imports(path: Path, package: str) -> list[str]:
    """Every module an ``import`` or ``from ... import`` in ``path`` names, relative ones resolved in ``package``
    (the shape of ``run.py:109-126``, re-implemented for this package: the spike's own version resolves relative
    imports in ``research.routing_spike``)."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                                 # ``from . import x`` / ``from .mod import x``
                base = f"{package}.{node.module}" if node.module else package
                out += [base] + [f"{base}.{a.name}" for a in node.names]
            else:
                out.append(node.module or "")
    return sorted(set(out))


def imports_loaded(module: str, prefixes: Sequence[str]) -> list[str]:
    """In a fresh interpreter: which of ``prefixes`` importing ``module`` loads (``run.py:97-107``)."""
    code = ("import importlib, json, sys; importlib.import_module(%r); "
            "print(json.dumps(sorted(m for m in sys.modules)))" % module)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), env=env,
                         timeout=600)
    if out.returncode != 0:
        return ["<import failed>"]
    loaded = set(strict_load(out.stdout.strip().encode("utf-8")))
    return sorted(p for p in prefixes if any(m == p or m.startswith(p + ".") for m in loaded))


def guard_report() -> dict[str, Any]:
    """Criterion 7: the HQ-side and bridge modules against their forbidden sets, statically and in a fresh
    interpreter; the drills against the site and service modules; the spike's paths clean."""
    modules = {}
    for name, forbidden in sorted(GUARDED_MODULES.items()):
        path = ROOT / "research" / "e2e_ingest" / f"{name}.py"
        named = static_imports(path, "research.e2e_ingest")
        modules[name] = {
            "forbidden": list(forbidden),
            "static": [m for m in named if any(m == f or m.startswith(f + ".") for f in forbidden)],
            "fresh_interpreter": imports_loaded(f"research.e2e_ingest.{name}", forbidden),
        }
    return {"modules": modules, "drills": imports_loaded("research.e2e_ingest.drills", DRILL_FORBIDDEN),
            "spike_code_dirty": code_dirty(list(SPIKE_DIRTY_PATHS))}


# --------------------------------------------------------------------------------------------------- the oracle

@dataclass(frozen=True)
class SiteFacts:
    """What Gold says one routed site holds of one pattern inside the question window (harness side, synthetic)."""

    records: int
    users: int
    events: int
    contradicted: bool
    newest_week: str | None

    @property
    def strong(self) -> bool:
        return (self.records >= ORACLE_MIN_RECORDS and self.users >= ORACLE_MIN_USERS
                and self.events >= ORACLE_MIN_EVENTS and not self.contradicted)

    def to_dict(self) -> dict[str, Any]:
        return {"records": self.records, "users": self.users, "events": self.events,
                "contradicted": self.contradicted, "newest_week": self.newest_week, "strong": self.strong}


def pattern_facts(world: ew.EmergenceWorld, pattern_id: int, sites: Mapping[int, str], window: Mapping[str, str],
                  route: Sequence[str]) -> dict[str, SiteFacts]:
    """Per routed site, the pattern's facet records inside the question window: how many, from how many users and
    events, whether one of them is a polarity −1 record, and the newest week. Read from the generator's own rows
    (``corpus.recs``), harness side; no record ref and no text leaves this function."""
    corpus = world.corpus
    assert corpus is not None
    pattern = corpus.patterns[pattern_id]
    rows = [r for facet in pattern.facet_records for r in facet]
    out: dict[str, SiteFacts] = {}
    for site in route:
        records, users, events, weeks, contradicted = 0, set(), set(), [], False
        for rid in rows:
            rec = corpus.recs[rid]
            if sites[int(rec["uid"])] != site:
                continue
            week = iso_week(ew.received_date(int(rec["t"])))
            if not window["start_week"] <= week <= window["end_week"]:
                continue
            if int(rec["polarity"]) < 0:
                contradicted = True
                continue
            records += 1
            users.add(int(rec["uid"]))
            events.add(int(rec["event"]))
            weeks.append(week)
        out[site] = SiteFacts(records=records, users=len(users), events=len(events), contradicted=contradicted,
                              newest_week=max(weeks) if weeks else None)
    return out


def expected_status(facts: Mapping[str, SiteFacts], as_of: str) -> tuple[str | None, bool, str]:
    """Section 3.5 and M20: ``(expected, ambiguous, reason)`` from Gold and the pack thresholds alone.

    ``supported`` when at least two routed sites are strong, no routed site holds a polarity −1 record of the
    pattern and the newest strong week is within ``freshness_days`` of ``as_of``; ``stale`` when the same holds but
    the newest week is older; ``hypothesis`` when no routed site is strong; a candidate with exactly one strong site
    or contradicted at any routed site is ambiguous (reported with both values, never asserted)."""
    strong = sorted(s for s, f in facts.items() if f.strong)
    contradicted = sorted(s for s, f in facts.items() if f.contradicted)
    if contradicted:
        return None, True, f"contradicted at {len(contradicted)} routed site(s)"
    if len(strong) == 1:
        return None, True, "exactly one strong routed site"
    if not strong:
        return "hypothesis", False, "no routed site holds the pattern strongly"
    weeks = [facts[s].newest_week for s in strong if facts[s].newest_week]
    newest = max(weeks) if weeks else None
    age = (date.fromisoformat(as_of) - week_sunday(newest)).days if newest else None
    if age is not None and age > FRESHNESS_DAYS:
        return "stale", False, f"{len(strong)} strong sites; newest strong week {newest} is {age} days old"
    return "supported", False, f"{len(strong)} strong sites; newest strong week {newest}"


# --------------------------------------------------------------------------------------------------- the run

@dataclass
class Options:
    out: Path
    world: int = 200
    seed: int = 0
    mode: str = "process"
    skip_drills: bool = False
    jobs: int = 4
    top_n: int | None = ew.TOP_N
    queries: int = FIXED_QUERIES
    run_id: str = "local"
    idle_timeout: float = 300.0
    suites: Path | None = None
    routing: str | None = None          # a directory of <site_id>.routing.json: each site's verifier judges through its own


@dataclass
class E2ERun:
    """One end-to-end run: the world, the passes, the fabric, the drills, the checks and the report."""

    options: Options
    doc: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    # ---- phase bookkeeping
    def _phase(self, name: str) -> Callable[[], None]:
        t0 = time.perf_counter()

        def done() -> None:
            self.timings[name] = time.perf_counter() - t0
        return done

    def log(self, message: str) -> None:
        print(f"[e2e {time.strftime('%H:%M:%S')}] {message}", flush=True)

    # ------------------------------------------------------------------ 0. the world
    def build_world(self) -> None:
        done = self._phase("world_seconds")
        o = self.options
        self.log(f"world: EMERGENCE target_users={o.world} seed={o.seed}")
        world_timings: dict[str, float] = {}
        self.world = ew.build_emergence_world(o.world, o.seed, self.dirs["world"], top_n=o.top_n,
                                              timings=world_timings)
        self.timings.update({f"world_{k}": v for k, v in world_timings.items()})
        world = self.world
        result = detect(world.pipeline.store, as_of=world.as_of, run_channel=ew.RUN_CHANNEL,
                        tie_salt=world.tie_salt)
        ordered, detected = ew.ordered_candidates(result, tie_salt=world.tie_salt, top_n=o.top_n)
        if canonical_bytes(ordered) != canonical_bytes(world.candidates) or detected != world.detected:
            raise HarnessError("the saved detection run is not the world's own detection")
        self.detection = result
        self.all_candidates, _ = ew.ordered_candidates(result, tie_salt=world.tie_salt, top_n=None)
        self.run_id_detection = hqt.save_detection_run(world.pipeline.store, result)
        self.candidates = [Candidate.from_detector(c) for c in world.candidates]
        self.candidates.sort(key=lambda c: (c.as_of, c.key))
        self.questions_fixed = ew.fixed_questions(world, o.queries)
        # section 3.5's 20 fixed questions, written beside the report (entity id, predicate, window, as_of: no
        # record, no ref); the measure job's Tesseract phase ranks the same 20 over its measurement graphs
        self.fixed_questions_path = o.out / FIXED_QUESTIONS_NAME
        write_json_atomic(self.fixed_questions_path, ew.fixed_questions(world, FIXED_QUERIES))
        self.prefixes = ew.narrative_prefixes(world.corpus)
        counts = dict(world.counts)
        self.doc["world"] = {
            "benchmark": ew.BENCHMARK, "target_users": o.world, "seed": o.seed, "n_days": ew.N_DAYS,
            "weeks": len(world.weeks), "node_granularity": "site", "site_ids": list(world.site_ids),
            "as_of": world.as_of, "pack_id": world.pack.id, "pack_hashes": world.pack.hashes(),
            "expected_counts": dict(ew.WORLD_TABLE[o.world]), "records_per_site": dict(world.per_site_records),
            "fraction_of_benchmark": {
                "users_vs_10k": counts["users"] / BENCHMARK_10K[0],
                "records_vs_10k": counts["records"] / BENCHMARK_10K[1],
                "users_vs_50k": counts["users"] / BENCHMARK_50K[0],
                "records_vs_50k": counts["records"] / BENCHMARK_50K[1]},
            **counts}
        self.log(f"world: {counts['users']} users, {counts['sites']} sites, {counts['records']} records; "
                 f"{detected} snapshot candidates, {len(self.candidates)} asked; run {self.run_id_detection}")
        done()

    def pristine_copies(self) -> None:
        world = self.world
        for site in world.pipeline.sites.values():
            site.close()
        for sid in world.site_ids:
            backup_sqlite(site_store_path(world.workdir / "edge", sid), self.dirs["pristine"] / f"site-{sid}.sqlite3")

    # ------------------------------------------------------------------ 1. the reference pass
    def reference_pass(self) -> None:
        done = self._phase("reference_pass_seconds")
        world, o = self.world, self.options
        edge, hq_dir = self.dirs["ref"] / "edge", self.dirs["ref"] / "hq"
        for sid in world.site_ids:
            restore_store(sid, self.dirs["pristine"], edge)
        configs = ew.site_configs(world, edge=edge, hq_dir=hq_dir, routing=self.options.routing)
        endpoints = sg.open_endpoints(configs, "in_process", hq_dir / "wire.jsonl", self.dirs["ref"] / "configs")
        view = HqView(world.pipeline.store, world.pack)
        self.reference: dict[tuple[str, str], str] = {}
        self.reference_routes: dict[str, tuple[str, ...]] = {}
        try:
            with ThreadPoolExecutor(max_workers=min(o.jobs, ew.M)) as pool:
                for cand in self.candidates:
                    question, _, orders = hq_plan(view, world.pack, cand, world.site_ids, tie_salt=world.tie_salt,
                                                  m=ew.M, seed=o.seed)
                    qid = question["question_id"]
                    route = tuple(orders[hqt.ROUTE_LABEL][:ew.M])
                    self.reference_routes[qid] = route
                    data = canonical_bytes(question)
                    futures = {site: pool.submit(endpoints[site].request, "question", data) for site in route}
                    for site, future in futures.items():
                        self.reference[(qid, site)] = sha256_hex(future.result())
        finally:
            sg.close_endpoints(endpoints)
        done()
        self.log(f"reference pass: {len(self.reference_routes)} questions, {len(self.reference)} (question, site) "
                 f"answers in {self.timings['reference_pass_seconds']:.0f}s")

    # ------------------------------------------------------------------ 2. the main pass
    def open_main_endpoints(self) -> dict[str, Any]:
        configs, mode = self.main_configs, self.options.mode
        endpoints = sg.open_endpoints(configs, mode, self.main_hq / "wire.jsonl", self.dirs["main"] / "configs")
        return {sid: TimedEndpoint(endpoints[sid], sid, self.wire_times) for sid in sorted(endpoints)}

    def reopen_site(self, site: str) -> Any:
        """One site's endpoint again, on the same config, graph and store (drill 2)."""
        if self.options.mode == "process":
            path = self.dirs["main"] / "configs" / f"site-{site}.json"
            inner: Any = sg.E2EChildProcessEndpoint(site, path, log_path=self.main_hq / "wire.jsonl")
        else:
            cfg = self.main_configs[site]
            inner = wire.InProcessEndpoint(site, lambda: sg.E2ESiteProcess(cfg), log_path=self.main_hq / "wire.jsonl")
        return TimedEndpoint(inner, site, self.wire_times)

    def main_pass(self) -> None:
        done = self._phase("main_pass_seconds")
        world, o = self.world, self.options
        self.main_edge, self.main_hq = self.dirs["main"] / "edge", self.dirs["main"] / "hq"
        for sid in world.site_ids:
            restore_store(sid, self.dirs["pristine"], self.main_edge)
        self.main_hq.mkdir(parents=True, exist_ok=True)
        self.main_configs = ew.site_configs(world, edge=self.main_edge, hq_dir=self.main_hq, routing=self.options.routing)
        for sid in world.site_ids:                       # the process-mode endpoints read these config files
            write_config(self.main_configs[sid], self.dirs["main"] / "configs" / f"site-{sid}.json")
        self.wire_times: list[dict[str, Any]] = []
        self.question_times: list[dict[str, Any]] = []
        endpoints = self.open_main_endpoints()
        clock = StepClock(f"{world.as_of}T08:00:00Z")
        self.stored: list[hqt.StoredQuestion] = []
        self.asked_by_id: dict[str, Any] = {}
        self.results_by_id: dict[str, Any] = {}
        probe: dict[str, Any] | None = None
        restart: dict[str, Any] = {}
        try:
            hq = hqt.E2EHq(world.pack, world.pipeline.store, endpoints, hq_dir=self.main_hq,
                           tie_salt=world.tie_salt, m=ew.M, seed=o.seed)
            self.hq = hq
            hq.describe_all()
            eligible = hq.eligible()
            if sorted(eligible) != sorted(world.site_ids):
                raise HarnessError("not every site offered the capability")
            asked = [hq.prepare(c) for c in self.candidates]
            planned = {a.question["question_id"]: hqt.route_of(a.orders, eligible, ew.M) for a in asked}
            self.route_by_key = {a.candidate.key: planned[a.question["question_id"]] for a in asked}
            per_site: dict[str, int] = {s: 0 for s in world.site_ids}
            for route in planned.values():
                for site in route:
                    per_site[site] += 1
            restart_site = min(sorted(per_site), key=lambda s: (-per_site[s], s))
            cut = math.ceil(per_site[restart_site] / 2)
            answered = 0
            self.log(f"main pass ({o.mode}): {len(asked)} questions, restart site {restart_site} after {cut} of its "
                     f"{per_site[restart_site]}")
            for a in asked:
                qid = a.question["question_id"]
                t0 = time.perf_counter()
                result = asyncio.run(hq.ask(a, hqt.ROUTE_LABEL))
                self.question_times.append({"question_id": qid, "seconds": time.perf_counter() - t0,
                                            "sites": len(result.route)})
                self.asked_by_id[qid] = a
                self.results_by_id[qid] = result
                self.stored.append(hqt.record_question(world.pipeline.store, world.pack, a, result,
                                                       run_id=self.run_id_detection, clock=clock))
                if restart_site in result.route:
                    answered += 1
                if answered == cut and not restart:
                    restart = self.restart_and_probe(hq, endpoints, restart_site, answered)
            self.eligible = list(eligible)
            self.planned_routes = planned
            probe = restart.get("probe")
        finally:
            sg.close_endpoints(endpoints)
        self.restart = restart
        self.probe = probe
        self.collect_routing(probe=probe, restart=restart)
        done()
        self.log(f"main pass: {len(self.stored)} questions asked, routed and written to HQ's store in "
                 f"{self.timings['main_pass_seconds']:.0f}s; statuses "
                 f"{json.dumps(self.doc['routing']['statuses'], sort_keys=True)}")

    def restart_and_probe(self, hq: hqt.E2EHq, endpoints: dict[str, Any], site: str,
                          answered: int) -> dict[str, Any]:
        """Drill 2: kill the site's process, exercise the Router's eligibility filter while it is down (M4) and
        reopen it on its persistent graph and store."""
        nodes_before = len(sg.graph_node_ids(self.main_configs[site].graph_path, site))
        watermark_before = sg.graph_watermark(self.main_configs[site].graph_path, site)
        self.log(f"drill 2: SIGKILL the process of {site} after {answered} of its questions ({nodes_before} nodes)")
        probe_cand = self.probe_candidate(site)
        killed = False
        if self.options.mode == "process":
            os.kill(endpoints[site].pid, signal.SIGKILL)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and endpoints[site].inner._proc.poll() is None:
                time.sleep(0.05)
            killed = endpoints[site].inner._proc.poll() is not None
        else:
            killed = False                               # no process exists to kill: never typed in (c4 reads it)
        endpoints[site].close()
        probe = asyncio.run(hq.probe_masked(probe_cand, site, reason="e2e drill 2: process down"))
        endpoints[site] = self.reopen_site(site)
        hq.replace_endpoint(site, endpoints[site])
        nodes_after = len(sg.graph_node_ids(self.main_configs[site].graph_path, site))
        self.log(f"drill 2: {site} reopened; probe route {'/'.join(probe.route)} (masked {site})")
        return {"site": site, "questions_before": answered, "killed": killed, "nodes_before": nodes_before,
                "nodes_after": nodes_after, "watermark_before": watermark_before,
                "watermark_after": sg.graph_watermark(self.main_configs[site].graph_path, site), "probe": probe}

    def probe_candidate(self, site: str) -> Candidate:
        """M4's probe question: the next candidate in order whose arm-R route holds the masked site, so the
        exclusion the Router makes is visible. A candidate beyond the asked ones is preferred (the probe is outside
        the answer matrix); when the world's candidate list is exhausted — 200/seed 0 detects fewer than ``top_n``
        — the last such asked candidate is re-asked, which leaves the matrix untouched (its result is a separate
        ``Asked``) and is recorded in the report."""
        asked_keys = {c.key for c in self.candidates}
        spare = [Candidate.from_detector(c) for c in self.all_candidates if c["key"] not in asked_keys]
        world = self.world
        view = HqView(world.pipeline.store, world.pack)
        for cand in spare:
            _, _, orders = hq_plan(view, world.pack, cand, world.site_ids, tie_salt=world.tie_salt, m=ew.M,
                                   seed=self.options.seed)
            if site in orders[hqt.ROUTE_LABEL][:ew.M]:
                self.probe_source = "spare"
                self.probe_unmasked_route = tuple(orders[hqt.ROUTE_LABEL][:ew.M])
                return cand
        for cand in self.candidates:
            route = self.route_by_key[cand.key]
            if site in route:
                self.probe_source = "reused"
                self.probe_unmasked_route = route
                return cand
        raise HarnessError(f"no candidate routes to {site}: the eligibility probe of M4 cannot run")

    def collect_routing(self, *, probe: Any, restart: Mapping[str, Any]) -> None:
        """Section 5's ``routing`` block: the coordinator's and router's live classes, the per-question route and
        trace (M4's ``routing.questions[]``), the Tesseract samples from the site diagnostics reconciled with the
        verdicts they produced (section 4, item 2), the wire timings and the verdict tally."""
        world = self.world
        questions: list[dict[str, Any]] = []
        verdicts = {"confirm": 0, "refute": 0, "unknown": 0, "hq_error": 0, "budget_unknown": 0}
        route_first = True
        routes_equal = True
        per_question_ok = True
        asked_pairs: set[tuple[str, str]] = set()         # (question, site) answered by the site itself
        read_pairs: set[tuple[str, str]] = set()          # ... with a verdict that came from a record read
        for qid, result in self.results_by_id.items():
            asked = self.asked_by_id[qid]
            planned = self.planned_routes[qid]
            route_first = route_first and result.route_selected_first
            routes_equal = routes_equal and tuple(result.route) == planned
            per_question_ok = per_question_ok and (len(asked.eligible) == len(world.site_ids)
                                                   and len(result.route) == min(ew.M, len(asked.eligible)))
            for received in result.received:
                if received.source == "hq":
                    verdicts["hq_error"] += 1
                    continue
                verdict = received.body["verdict"]
                verdicts[verdict] = verdicts.get(verdict, 0) + 1
                if verdict == "unknown" and received.body.get("reason") == "budget":
                    verdicts["budget_unknown"] += 1
                asked_pairs.add((qid, received.site))
                if verdict in ("confirm", "refute"):
                    read_pairs.add((qid, received.site))
            questions.append({"question_id": qid, "hq_eligible": len(asked.eligible), "route": list(result.route),
                              "route_size": len(result.route), "status": result.status,
                              "trace": list(result.trace)})
        if probe is not None:                              # the probe's sites answered too (M4; outside the matrix)
            for r in probe.received:
                if r["source"] == "site":
                    asked_pairs.add((probe.question_id, r["site"]))
        samples = []
        rows_by_pair: dict[tuple[str, str], int] = {}
        for sid in world.site_ids:
            path = self.main_edge / f"site-{sid}.tesseract.jsonl"
            for line in path.read_bytes().split(b"\n") if path.exists() else []:
                if not line:
                    continue
                row = strict_load(line)
                samples.append({"question_id": row["question_id"], "site": sid, "seconds": row["seconds"],
                                "nodes": row["nodes"], "nodes_as_of": row["nodes_as_of"],
                                "returned": row["returned"], "read": row["read"]})
                rows_by_pair[(row["question_id"], sid)] = rows_by_pair.get((row["question_id"], sid), 0) + 1
        # section 4, item 2: one diagnostics row per (question, site) whose verdict came from a record read; a row
        # is written only by a site that ran its retrieval for the question (``site_process.py:330-335``), so every
        # confirm or refute has one, no pair has two, and no row names a pair HQ did not ask
        rows_for_read = sum(1 for pair in read_pairs if rows_by_pair.get(pair, 0) == 1)
        rows_outside = sum(n for pair, n in rows_by_pair.items() if pair not in asked_pairs)
        rows_duplicated = sum(1 for n in rows_by_pair.values() if n > 1)
        nodes = sorted(s["nodes"] for s in samples)
        per_site = [s for s in self.wire_times if s["kind"] == "question"]
        self.doc["routing"] = {
            "candidates_detected": world.detected, "candidates_asked": len(self.candidates), "m": ew.M,
            "route_label": hqt.ROUTE_LABEL, "questions_asked": len(questions), "eval_window": None,
            "tie_salt": world.tie_salt, "run_channel": ew.RUN_CHANNEL,
            "coordinator_class": class_name(self.hq.coordinator),
            "router_class": class_name(self.hq.coordinator.router),
            "route_selected_first_all": route_first, "routes_equal_arm_order": routes_equal,
            "per_question_route_ok": per_question_ok, "hq_eligible_expected": len(world.site_ids),
            "router_excluded_masked": int(bool(probe is not None and probe.excluded)),
            "intervention_records": int(probe.intervention_records) if probe is not None else 0,
            "probe": (probe.to_dict() | {"candidate_source": getattr(self, "probe_source", None),
                                         "unmasked_route": list(getattr(self, "probe_unmasked_route", ()))})
            if probe is not None else None,
            "tesseract": {"rankings": len(samples), "nodes_min": nodes[0] if nodes else None,
                          "nodes_median": nodes[len(nodes) // 2] if nodes else None,
                          "nodes_max": nodes[-1] if nodes else None,
                          **{f"seconds_{k}": v for k, v in rp.stat_block(samples).items() if k in ("p50", "p95")},
                          "read_verdicts": len(read_pairs), "rows_for_read_verdicts": rows_for_read,
                          "rows_outside_asked_pairs": rows_outside, "rows_duplicated": rows_duplicated,
                          "asked_pairs": len(asked_pairs),
                          "rows_equal_read_verdicts": (rows_for_read == len(read_pairs) and rows_outside == 0
                                                       and rows_duplicated == 0),
                          "samples": samples},
            "question_to_verdict": {
                "per_site_p50": rp.stat_block(per_site)["p50"], "per_site_p95": rp.stat_block(per_site)["p95"],
                "per_question_p50": rp.stat_block(self.question_times)["p50"],
                "per_question_p95": rp.stat_block(self.question_times)["p95"],
                "n": len(self.question_times), "per_site_n": len(per_site), "samples": self.question_times,
                "per_site_samples": per_site},
            "verdicts": verdicts, "questions": questions,
            "statuses": {s: sum(1 for q in self.stored if q.status == s) for s in sorted({q.status for q in
                                                                                          self.stored})},
        }

    # ------------------------------------------------------------------ the site reads (c8, c3's site half)
    def rank_fixed_queries(self, label: str) -> dict[str, dict[str, Any]]:
        """The 20 fixed queries at every site, each site in its own thread over its own process (M15); the top 10
        and the full returned order as digests, never a ref."""
        done = self._phase(f"fixed_queries_{label}_seconds")
        world = self.world
        texts = [(query_text(world.pack, q), q["as_of"]) for q in self.questions_fixed]

        def one(site: str) -> dict[str, Any]:
            process = sg.E2ESiteProcess(self.main_configs[site])
            try:
                orders = [process.retrieval.rank(text, as_of) for text, as_of in texts]
                sync = dict(process.retrieval.sync)
                nodes = process.retrieval.nodes
            finally:
                process.close()
            return {"queries": len(orders), "nodes": nodes, "sync": sync,
                    "top10": [sg.node_ids_digest(o[:10]) for o in orders],
                    "order": [sha256_hex("\n".join(o)) for o in orders],
                    "returned": [len(o) for o in orders]}

        with ThreadPoolExecutor(max_workers=max(1, self.options.jobs)) as pool:
            out = dict(zip(world.site_ids, pool.map(one, world.site_ids)))
        done()
        self.log(f"fixed queries ({label}): {sum(r['queries'] for r in out.values())} rankings in "
                 f"{self.timings.get(f'fixed_queries_{label}_seconds', 0):.0f}s")
        return out

    def collect_ingest(self) -> None:
        """Criterion 8: per site the records the store holds (M16), the graph's node ids read ``mode=ro`` (M9) and
        the digests against the expected refs, computed from ``corpus.recs`` and ``org.ancestor_at`` alone."""
        world = self.world
        expected_refs = {sid: [] for sid in world.site_ids}
        for record in world.records:
            expected_refs[record["site"]].append(record["record_ref"])
        per_site: dict[str, Any] = {}
        total_expected = total_ingested = 0
        for sid in world.site_ids:
            cfg = self.main_configs[sid]
            records = own_records(site_store_path(self.main_edge, sid))
            ids = sg.graph_node_ids(cfg.graph_path, sid)
            rows = sg.read_sync_log(self.main_edge, sid)
            expected = len(expected_refs[sid])
            build = sum(float(r["seconds"]) for r in rows[:1])
            # ``duplicates`` and ``rejected`` are 0 because ``run_pipeline`` returned: it raises on any duplicate or
            # rejection (``baselines.py:136-138``), which is what ``ingest.pipeline_raised: false`` records (M16).
            per_site[sid] = {
                "expected_records": expected, "ingested": len(records), "duplicates": 0, "rejected": 0,
                "graph_nodes": rows[0]["nodes"] if rows else None, "graph_nodes_db": len(ids),
                "node_ids_sha256": sg.node_ids_digest(ids),
                "record_refs_sha256": sg.node_ids_digest(expected_refs[sid]),
                "graph_watermark": sg.graph_watermark(cfg.graph_path, sid),
                "graph_build_seconds": build, "syncs": len(rows),
                "seconds_per_1k_records": (build / len(ids) * 1000) if ids else None}
            total_expected += expected
            total_ingested += len(records)
        self.doc["ingest"] = {"per_site": per_site, "total_expected": total_expected,
                              "total_ingested": total_ingested, "loss": total_expected - total_ingested,
                              "pipeline_raised": False,
                              "seconds_per_1k_records": (sum(v["graph_build_seconds"] for v in per_site.values())
                                                         / max(1, total_ingested) * 1000)}

    # ------------------------------------------------------------------ 3. the fabric and the bridge
    def start_fabric(self) -> None:
        done = self._phase("fabric_start_seconds")
        self.nats_bin = nats_server_bin()
        self.driver = drills.E2EProcessDriver(workdir=self.dirs["fabric"], nats_bin=self.nats_bin)
        self.driver.up()
        self.admin = MycelicClient(self.driver.base_url, self.driver.admin_token)
        registered = self.admin.register_agent(agent_id=AGENT_ID, enterprise=ORG_ID)
        self.agent_path = registered["agent"]["path"]
        self.hq_client = MycelicClient(self.driver.base_url, registered["api_key"])
        self.log(f"fabric: nats-server {nats_binary_version(self.nats_bin)} and the service under "
                 f"{class_name(self.driver)}; agent {AGENT_ID} in {ORG_ID}")
        done()

    def publish_with_broker_kill(self) -> None:
        done = self._phase("publish_seconds")
        self.store_path = Path(self.world.pipeline.store.path)       # HQ's own store (``store.py:347``)
        self.doc["run"]["paths"] = {"hq_store": str(self.store_path), "fixed_questions": str(self.fixed_questions_path),
                                    "out": str(self.options.out)}
        reader = HqStoreReader(self.store_path)
        counts = reader.counts()
        n = counts["pd_verdicts"] + counts["pd_conclusions"]
        k = n // 2 if not self.options.skip_drills else 0
        state: dict[str, Any] = {"killed_at": None, "down_from": None, "outage": None}

        def on_publish(count: int, result: Any) -> None:
            if k and count == k and state["killed_at"] is None:
                self.driver.kill("nats")
                state["killed_at"] = count
                state["down_from"] = time.perf_counter()
                time.sleep(1.0)
                self.log(f"drill 3: nats-server killed after publish {count} of {n}")

        before = drills.event_counters(self.driver.base_url, self.driver.admin_token)
        publisher = Publisher(self.hq_client, watermark=self.dirs["bridge"] / "publish_watermark.json",
                              ledger=self.dirs["bridge"] / "publishes.jsonl", on_publish=on_publish)
        bridge = HqBridge(self.store_path, self.world.pack, publisher,
                          watermark=self.dirs["bridge"] / "watermark.json")
        self.bridge_report = bridge.run()
        self.publisher = publisher
        self.api_ids = event_ids(publisher.results)
        if k:
            # the outage is witnessed by the service once every publish of k+1..N has been taken by the outbox
            # (M6): the sample is read after the last POST and before the broker comes back
            state["outage"] = drills.fabric_counts(self.admin.status())
            self.driver.start("nats")
            state["down_seconds"] = time.perf_counter() - (state["down_from"] or time.perf_counter())
        status = drills.wait_idle(self.admin, self.options.idle_timeout)
        counts_after = drills.fabric_counts(status)
        after = drills.event_counters(self.driver.base_url, self.driver.admin_token)
        events = {e["event_id"]: e for e in self.admin.events(ORG_ID, limit=1000)}
        api_ids = self.api_ids["agent.event"] + self.api_ids["memory.observed"]
        applied = [i for i in api_ids if events.get(i, {}).get("status") == "applied"
                   and events[i].get("applied_at")]

        def sample(event_id: str | None, role: str) -> dict[str, Any] | None:
            row = events.get(event_id or "")
            if not row or not row.get("applied_at"):
                return None
            return {"event_id": event_id, "kind": row["kind"], "role": role, "created_at": row["created_at"],
                    "applied_at": row["applied_at"], "seconds": seconds_between(row["created_at"], row["applied_at"])}

        # M18: a verdict POST's latency is its ``agent.event``'s; a conclusion POST's is the ``memory.observed``
        # event's (the note's apply), with the conclusion's own ``agent.event`` reported beside it
        in_order: list[dict[str, Any] | None] = []       # one entry per publish, in publish order
        beside: list[dict[str, Any]] = []
        for result in publisher.results:
            if result.type == VERDICT_EVENT:
                in_order.append(sample(result.event_id, "verdict"))
            else:
                in_order.append(sample(result.memory_event_id, "conclusion"))
                beside.append(sample(result.event_id, "conclusion_agent_event"))
        samples = [s for s in in_order if s is not None]
        # the first k publishes were taken by the live broker; k+1..N waited in the outbox through drill 3's
        # outage, so the full block measures the outage, not apply latency (criterion 5's figures are the measure
        # job's, whose sizes run without the kill)
        before_kill = [s for s in in_order[:k] if s is not None] if k else samples
        beside = [s for s in beside if s is not None]
        notes = self.admin.list_memories(scope=ORG_ID, limit=1000)
        r = self.bridge_report
        self.doc["publish"] = {
            "publishes": r.publishes, "verdict_events": r.verdict_events, "conclusion_events": r.conclusion_events,
            "events_created": r.created, "events_duplicate": r.duplicate, "events_ambiguous": r.ambiguous,
            "conclusions": r.conclusions, "memory_ids": len(r.memory_ids),
            "memory_ids_created": len(r.memory_ids_created), "store_unchanged": r.store_unchanged,
            "created_to_applied": {
                **rp.stat_block(samples),
                "note": ("the full block includes drill 3's outage (publishes k+1..N waited in the outbox); "
                         "created_to_applied_before_kill holds the first k publishes, taken by the live broker; "
                         "the measure job's sizes run without the kill" if k else
                         "no broker kill in this run (--skip-drills): every publish was taken by the live broker")},
            "created_to_applied_before_kill": {**rp.stat_block(before_kill), "k": k},
            "created_to_applied_conclusion_agent_events": rp.stat_block(beside)}
        self.doc.setdefault("recovery", {})["broker_kill"] = {
            "N": r.publishes, "k": state["killed_at"] or 0,
            "applied_exactly_once": len(applied) == len(api_ids) and len(set(api_ids)) == len(api_ids),
            "applied_ids": len(applied), "api_ids": len(api_ids),
            "outbox_pending": counts_after["outbox_pending"], "consumer_pending": counts_after["consumer_pending"],
            "broker_down_seconds": state.get("down_seconds"),
            "outbox_pending_during_outage": (state["outage"] or {}).get("outbox_pending"),
            "transport_connected_during_outage": (state["outage"] or {}).get("connected"),
            "status_during_outage": (state["outage"] or {}).get("status"),
            "transport_reconnects_after": counts_after["reconnects"],
            "applied_delta_agent_event": int(after["applied_agent_event"] - before["applied_agent_event"]),
            "applied_delta_memory_observed": int(after["applied_memory_observed"]
                                                 - before["applied_memory_observed"]),
            "ignored_delta": int(after["ignored_total"] - before["ignored_total"]),
            "active_notes_after": len(notes), "stream_duplicates": None}
        self.notes = notes
        self.events_rows = events
        self.log(f"drill 3: {r.publishes} publishes ({r.verdict_events} verdicts, {r.conclusion_events} "
                 f"conclusions), {len(applied)} applied, outbox {counts_after['outbox_pending']}")
        done()

    # ------------------------------------------------------------------ 4. signatures
    def stream_block(self) -> dict[str, Any]:
        """One read of the whole stream (M3) beside ``/admin/status``'s own figures: ``first_seq`` and ``last_seq``
        are the broker's (``stream_info``), ``status_first_seq`` and ``status_last_seq`` the service's
        ``checks.transport``, and c1 asserts the two agree."""
        stream = drills.read_stream(self.driver)
        status = drills.fabric_counts(self.admin.status())
        api_ids = self.api_ids["agent.event"] + self.api_ids["memory.observed"]
        once, duplicated = stream.ids_in_stream(api_ids)
        return {"fetched": stream.fetched, "verified": stream.verified, "foreign": stream.foreign,
                "kinds": stream.kinds, "api_event_ids_in_stream": once, "api_event_ids_duplicated": duplicated,
                "stream_messages": status["stream_messages"], "first_seq": stream.first_seq,
                "last_seq": stream.last_seq, "status_first_seq": status["first_seq"],
                "status_last_seq": status["last_seq"], "broker_version": stream.broker_version,
                "memory_observed_seqs": [m.seq for m in stream.messages if m.kind == "memory.observed"][:1]}

    def forgeries(self) -> None:
        done = self._phase("forgery_seconds")
        before = drills.signature_failures(self.driver.base_url, self.driver.admin_token)
        stream_before = self.stream_block()
        rows: list[dict[str, Any]] = []
        seq = (stream_before["memory_observed_seqs"] or [stream_before["first_seq"]])[0]
        plans = [("unsigned", lambda mid: drills.forge_unsigned(self.driver, org_id=ORG_ID, memory_id=mid,
                                                                producer_id=AGENT_ID, scope=self.agent_path)),
                 ("wrong_key", lambda mid: drills.forge_wrong_key(self.driver, org_id=ORG_ID, memory_id=mid,
                                                                  producer_id=AGENT_ID, scope=self.agent_path)),
                 ("replayed_and_altered", lambda mid: drills.forge_replay(self.driver, seq=seq, memory_id=mid))]
        absent = True
        for i, ((kind, publish), memory_id) in enumerate(zip(plans, FORGED_IDS), start=1):
            stream_seq = publish(memory_id)
            ok = drills.wait_for(lambda: drills.signature_failures(self.driver.base_url,
                                                                   self.driver.admin_token) == before + i, 60.0)
            counter = drills.signature_failures(self.driver.base_url, self.driver.admin_token) - before
            try:
                self.admin.get_memory(memory_id)
                found = True
            except MycelicError as exc:                    # 404 is the evidence; any other status is reported as is
                found = exc.status != 404
            absent = absent and not found
            rows.append({"forgery": kind, "memory_id": memory_id, "stream_seq": stream_seq, "counter": counter,
                         "counted_within_timeout": ok, "memory_absent": not found})
            self.log(f"drill 4: forgery {i} ({kind}) counted {counter}, memory absent {not found}")
        audit = drills.audit_rows(self.driver.base_url, self.driver.admin_token)
        rejected_all = [a for a in audit if a.get("action") == "event.rejected"]
        # the service's audit row of a terminated event names the stream sequence it terminated
        # (``service.py`` ``event.rejected``: ``{"reason": "invalid signature", "seq", "subject"}``), so the three
        # forgeries are counted by their own sequences (M8: exactly three), never by any rejection
        forged_seqs = {r["stream_seq"] for r in rows}
        rejected = [a for a in rejected_all if (a.get("detail") or {}).get("seq") in forged_seqs]
        stream_after = self.stream_block()
        self.doc["signatures"] = {
            "failed_before_forgery": before, "failed_after_forgery": rows[-1]["counter"] if rows else None,
            "forged_memory_absent": absent, "forged_event_terminated": len(rejected) == rp.FORGERIES,
            "event_rejected_audit_rows": len(rejected_all), "forged_seq_rejected_audit_rows": len(rejected),
            "forged_stream_seqs": sorted(forged_seqs), "forgeries": rows,
            "stream_before": stream_before, "stream_after": stream_after,
            "stream_messages_fetched": stream_after["fetched"],
            "stream_messages_verified": stream_after["verified"],
            "stream_messages_foreign": stream_after["foreign"], "stream_kinds": stream_after["kinds"],
            "api_event_ids_in_stream": stream_after["api_event_ids_in_stream"],
            "api_event_ids_duplicated": stream_after["api_event_ids_duplicated"]}
        self.doc["recovery"]["broker_kill"]["stream_duplicates"] = stream_after["api_event_ids_duplicated"]
        done()

    def collect_fabric(self) -> None:
        """Section 5's ``fabric`` block, sampled once the forgeries are in and the fabric is idle: nothing after
        drill 4 adds a stream message (the rebuild replays, the bridge rerun creates nothing)."""
        status = self.admin.status()
        counts = drills.fabric_counts(status)
        keyring = drills.Keyring(self.driver.signing_key)
        known = drills.known_key_ids(self.driver)
        memory_id = self.bridge_report.rows[0].memory_id if self.bridge_report.rows else None
        verify = self.admin.verify(memory_id) if memory_id else {}
        self.doc["fabric"] = {
            "driver_is_process_driver": isinstance(self.driver, drills.ProcessDriver),
            "driver_class": class_name(self.driver), "nats_bin": bool(self.driver.nats_bin),
            "keyring": {"harness_key_set": bool(keyring.keyed), "key_id": keyring.key_id,
                        "key_id_in_service_db": keyring.key_id in known, "known_key_ids": known},
            "transport": counts["transport"], "broker_version": drills.broker_version(self.driver),
            "broker_binary_version": nats_binary_version(self.nats_bin),
            "integrity_mode": verify.get("integrity_mode"),
            "stream_messages": counts["stream_messages"], "consumer_pending": counts["consumer_pending"],
            "outbox_pending": counts["outbox_pending"], "service_version": status.get("version"),
            "org_id": ORG_ID, "agent_id": AGENT_ID, "active_notes": len(self.notes),
            "max_active_memories_per_org": drills.MAX_ACTIVE_MEMORIES_PER_ORG,
            "replay_max_reject_ratio": float(drills.REPLAY_MAX_REJECT_RATIO), "status": counts["status"]}

    # ------------------------------------------------------------------ 5. lineage and the scan
    def fabric_event_payloads(self) -> tuple[dict[str, Any], str]:
        """Each event's ``payload`` as the fabric stores it (``events.payload``: ``{type, occurred_at, agent_id,
        path, payload: <the POSTed payload>}``), keyed by event id, and where it was read: the admin view's rows
        when they hold the organisation whole (``/admin/events`` caps at 1,000 rows), else the service database
        read ``mode=ro`` (M7). M1's first witness is the ``sha256`` inside the POSTed payload."""
        if len(self.events_rows) < 1000:
            return {eid: row.get("payload") for eid, row in self.events_rows.items()}, "admin_view"
        return drills.db_event_payloads(self.driver, ORG_ID), "service_db"

    def crossing_checks(self) -> None:
        """Section 4: the spike's leakage scan (``run.py`` ``leakage_check``: narrative shingle overlap and canary
        hits over every byte that crossed a site boundary, HQ's wire, intake, questions and descriptor logs and
        the received verdicts, the sites' ingress, egress and descriptor logs) and its closed-shape check
        (``crossing_check``: only questions in, verdicts and the descriptor out, every frame a known kind, every
        artifact passing ``check_artifact`` on both sides, no intake refusal) over the main pass's directory, which
        has the spike's layout; and the leakage scan over the pipeline's directory, where the cells crossed."""
        done = self._phase("crossing_seconds")
        view = dataclasses.replace(self.world, workdir=self.dirs["main"])
        main = {"leakage": leakage_check(view), "crossing": crossing_check(view)}
        pipeline = {"leakage": leakage_check(self.world)}
        self.crossing = {"main": main, "pipeline": pipeline}
        done()
        self.log(f"crossing: main pass leakage overlap {main['leakage']['overlap_bytes']} bytes over "
                 f"{main['leakage']['scanned_items']} items, shapes ok {main['crossing']['ok']}; pipeline overlap "
                 f"{pipeline['leakage']['overlap_bytes']} over {pipeline['leakage']['scanned_items']} items")

    def lineage(self) -> None:
        done = self._phase("lineage_seconds")
        world = self.world
        reader = HqStoreReader(self.store_path)
        payloads, payloads_source = self.fabric_event_payloads()
        wire_shas: set[tuple[str, str]] = set()
        for row in wire.read_frames(self.main_hq / "wire.jsonl"):
            if row["direction"] != "response":
                continue
            try:
                body = wire.decode_response(row["frame"].encode("utf-8"))
            except wire.WireError:
                continue
            wire_shas.add((row["site"], sha256_hex(body)))
        intake = {(r["site"], r["sha256"], r["question_id"]) for r in
                  [strict_load(line) for line in (self.main_hq / "intake.jsonl").read_bytes().split(b"\n") if line]
                  if r["source"] == "site"}
        egress: dict[str, set[str]] = {}
        for sid in world.site_ids:
            egress[sid] = {r["sha256"] for r in read_log(self.main_edge / f"site-{sid}.egress.jsonl")
                           if r["artifact_type"] == "verdict"}
        received = {sid: own_records(site_store_path(self.main_edge, sid)) for sid in world.site_ids}
        stores = {sid: RecordStore(site_store_path(self.main_edge, sid), site_id=sid, pack_id=world.pack.id,
                                   config_hash=world.pack.config_hash) for sid in world.site_ids}
        counters = dict.fromkeys(("fabric_event", "pd_verdicts", "wire", "intake", "egress", "store"), 0)
        site_sourced = answered = joined = 0
        late = unknown = 0
        rows: list[dict[str, Any]] = []
        lineage_ok: dict[str, bool] = {}
        try:
            for bridged in self.bridge_report.rows:
                qid = bridged.question_id
                store_rows = reader.verdicts(qid)
                as_of = self.asked_by_id[qid].candidate.as_of
                event_ids_by_site: dict[str, str] = {}
                shas: list[str | None] = []
                confirming = entity = 0
                newest = None
                for row, event_id in zip(store_rows, bridged.verdict_event_ids):
                    if row.source != "site":
                        continue
                    site_sourced += 1
                    event_ids_by_site[row.site] = event_id
                    # M1's chain starts at the fabric: the ``sha256`` the service stored in this event's payload
                    # (witness 1 is ``event.sha256 == pd_verdicts.sha256``; witnesses 2-5 are compared with the
                    # fabric's value, so a bridge that published a wrong or null sha256 fails every one)
                    fabric_sha = ((payloads.get(event_id) or {}).get("payload") or {}).get("sha256")
                    held = isinstance(fabric_sha, str) and bool(fabric_sha)
                    shas.append(fabric_sha if held else None)
                    body = strict_load(row.body)
                    counters["fabric_event"] += int(held and fabric_sha == row.sha256)
                    counters["pd_verdicts"] += int(held and sha256_hex(row.body) == row.sha256 == fabric_sha)
                    counters["wire"] += int(held and (row.site, fabric_sha) in wire_shas)
                    counters["intake"] += int(held and (row.site, fabric_sha, qid) in intake)
                    counters["egress"] += int(held and fabric_sha in egress[row.site])
                    stored = stores[row.site].verdict_by_id(body["verdict_id"])
                    counters["store"] += int(held and stored is not None and stored.sha256 == fabric_sha)
                    if body["verdict"] in ("confirm", "refute"):
                        answered += 1
                        by_ref = (stores[row.site].verdict_for_ref(stored.evidence_ref)
                                  if stored is not None and stored.evidence_ref else None)
                        if by_ref is not None and by_ref.verdict_id == body["verdict_id"]:
                            joined += 1
                            refs = tuple(by_ref.confirming_refs) + tuple(by_ref.entity_refs)
                            confirming += len(by_ref.confirming_refs)
                            entity += len(by_ref.entity_refs)
                            days = [received[row.site][ref] for ref in refs if ref in received[row.site]]
                            # a ref the site's own store cannot date is counted, never dropped (c2 holds it at 0)
                            unknown += sum(1 for ref in refs if ref not in received[row.site])
                            late += sum(1 for day in days if day > as_of)
                            if days:
                                newest = max([newest or "", *days]) or None
                memory_id = bridged.memory_id
                graph = self.admin.lineage(memory_id)
                note = self.admin.get_memory(memory_id)
                sources = set(note.get("source_event_ids") or [])
                complete = set(event_ids_by_site.values()) <= sources
                reconstructable = bool(graph["evidence"]["reconstructable"])
                lineage_ok[qid] = reconstructable and complete
                rows.append({"memory_id": memory_id, "question_id": qid, "status": bridged.status,
                             "verdict_event_ids": list(event_ids_by_site.values()), "sha256s": shas,
                             "confirming_refs_count": confirming, "entity_refs_count": entity,
                             "max_received_date": newest, "as_of": as_of,
                             "reconstructable": reconstructable, "source_events_complete": complete,
                             "entity_id": note["metadata"].get("entity_id"),
                             "predicate": note["metadata"].get("predicate"),
                             "snapshot_week": note["metadata"].get("snapshot_week"),
                             "value": note["metadata"].get("value")})
        finally:
            for store in stores.values():
                store.close()
        self.lineage_ok = lineage_ok
        self.notes_by_question = {r["question_id"]: r for r in rows}
        scan = self.scan()
        self.doc["lineage"] = {
            "conclusions": len(rows), "reconstructable": sum(1 for r in rows if r["reconstructable"]),
            "source_events_complete": sum(1 for r in rows if r["source_events_complete"]),
            "site_sourced_verdict_events": site_sourced,
            "site_sourced_answered_verdict_events": answered,
            "fabric_events_source": payloads_source,
            "sha256_in_fabric_event": counters["fabric_event"],
            "sha256_in_pd_verdicts": counters["pd_verdicts"], "sha256_in_wire_log": counters["wire"],
            "sha256_in_intake_log": counters["intake"], "sha256_in_site_egress": counters["egress"],
            "sha256_in_site_store": counters["store"], "site_audit_joined": joined,
            "records_on_or_before_as_of": late == 0, "records_after_as_of": late, "unknown_refs": unknown,
            "rows": rows, "scan": scan, "crossing": getattr(self, "crossing", None)}
        self.log(f"drill 5: {len(rows)} conclusions reconstructable, {site_sourced} verdict events, "
                 f"{counters['fabric_event']} fabric sha256s equal to HQ's rows with five witnesses each, scan hits "
                 f"{scan['record_ref_hits']}/{scan['narrative_marker_hits']}/{scan['narrative_prefix_hits']}")
        done()

    def scan(self) -> dict[str, Any]:
        """Drill 5's scan: every row of the fabric's database and every message of the stream against the
        record-ref pattern, the narrative marker and the first 24 characters of every narrative."""
        db_text, db_bytes, tables = drills.fabric_db_text(self.driver)
        stream_text, messages = drills.stream_payloads(self.driver)
        hits = {"record_ref_hits": 0, "narrative_marker_hits": 0, "narrative_prefix_hits": 0}
        for text in (db_text, stream_text):
            found = rp.scan_hits(text, record_ref=ew.RECORD_REF_RE, narrative_marker=ew.NARRATIVE_MARKER_RE,
                                 narrative_prefixes=self.prefixes, prefix_length=ew.NARRATIVE_PREFIX)
            for key, value in found.items():
                hits[key] += value
        return {**hits, "db_bytes": db_bytes, "db_rows": tables, "stream_messages_scanned": messages,
                "stream_bytes": len(stream_text.encode("utf-8")), "prefixes": len(self.prefixes)}

    # ------------------------------------------------------------------ 6. the service rebuild
    def rebuild_service(self) -> None:
        done = self._phase("rebuild_seconds")
        ids_before = sorted(m["memory_id"] for m in self.admin.list_memories(scope=ORG_ID, limit=1000))
        digests_before = {r.memory_id: self.admin.verify(r.memory_id)["report_digest"]
                          for r in self.bridge_report.rows}
        applied_conclusions = sum(1 for i in self.api_ids["memory.observed"]
                                  if self.events_rows.get(i, {}).get("status") == "applied")
        t0 = time.perf_counter()
        self.driver.kill("mycelic")
        self.driver.wipe_database()
        try:
            self.driver.start("mycelic")
        except TimeoutError:                              # the replay may outlast the driver's own 60 s wait
            drills.wait_for(lambda: drills.ready(self.driver.base_url)[0] == 200, 120.0)
        status = drills.wait_idle(self.admin, self.options.idle_timeout)
        rebuild_seconds = time.perf_counter() - t0
        ids_after = sorted(m["memory_id"] for m in self.admin.list_memories(scope=ORG_ID, limit=1000))
        digests_after = {r.memory_id: self.admin.verify(r.memory_id)["report_digest"]
                         for r in self.bridge_report.rows}
        audit = [a for a in drills.audit_rows(self.driver.base_url, self.driver.admin_token)
                 if a.get("action") == "recovery.signature_rejections"]
        detail = (audit[0].get("detail") or {}) if audit else {}
        code, _ = drills.ready(self.driver.base_url)
        checks = (status.get("checks") or {}).get("consumer") or {}
        self.doc["recovery"]["service_rebuild"] = {
            "conclusions_applied_before_kill": applied_conclusions,
            "active_memory_ids_before": len(ids_before), "active_memory_ids_after": len(ids_after),
            "active_memory_ids_identical": ids_before == ids_after,
            "verify_digest_identical": digests_before == digests_after,
            "conclusions_verified": len(digests_after), "rebuild_seconds": rebuild_seconds,
            "replay_seen": detail.get("seen"), "replay_rejected": detail.get("rejected"),
            "replay_blocked": detail.get("blocked"), "ready_block": checks.get("ready_block"),
            "ready_after_rebuild": code == 200, "audit_rows": len(audit)}
        self.log(f"drill 6: rebuilt in {rebuild_seconds:.0f}s; {len(ids_after)} active notes, replay seen "
                 f"{detail.get('seen')} rejected {detail.get('rejected')}")
        done()

    # ------------------------------------------------------------------ 7. re-ingest and the bridge rerun
    def reingest(self) -> None:
        done = self._phase("reingest_seconds")
        world = self.world
        ingested = duplicates = rejected = 0
        for sid in world.site_ids:
            records = [r for r in world.records if r["site"] == sid]
            site = EdgeSite(world.pack, sid, self.main_edge, runtime=None,
                            clock=lambda: f"{world.as_of}T12:00:00Z", master_data=world.master_data[sid],
                            hq_dir=self.main_hq)
            try:
                got = site.ingest(records)
            finally:
                site.close()
            ingested += got.ingested
            duplicates += got.duplicates
            rejected += sum(got.rejected.values())
        after = self.rank_fixed_queries("after")
        before = self.rankings_before
        expected_queries = len(self.questions_fixed)
        counts = {sid: int(after[sid]["queries"]) for sid in world.site_ids}
        nodes_same = count_same = top10_same = order_same = True
        for sid in world.site_ids:
            ids_after = sg.graph_node_ids(self.main_configs[sid].graph_path, sid)
            nodes_same = nodes_same and sg.node_ids_digest(ids_after) == self.node_ids_before[sid]
            # the same node count at every site, and the same number of rankings before and after, which is the
            # number of fixed questions asked (measured from the rankings, never the command-line option)
            count_same = (count_same and len(ids_after) == self.node_count_before[sid]
                          and counts[sid] == before.get(sid, {}).get("queries") == expected_queries)
            top10_same = top10_same and after[sid]["top10"] == before.get(sid, {}).get("top10")
            order_same = order_same and after[sid]["order"] == before.get(sid, {}).get("order")
        self.doc.setdefault("idempotency", {})["site_reingest"] = {
            "ingested": ingested, "duplicates": duplicates, "rejected": rejected,
            "queries_per_site": counts[world.site_ids[0]] if len(set(counts.values())) == 1 else None,
            "queries_per_site_by_site": counts, "queries_expected": expected_queries,
            "same_node_ids": nodes_same, "same_count": count_same,
            "same_top10": top10_same, "same_order": order_same,
            "nodes_added_on_resync": {sid: after[sid]["sync"]["nodes_added"] for sid in world.site_ids},
            "returned_per_query": {sid: after[sid]["returned"] for sid in world.site_ids}}
        self.log(f"drill 7: re-ingest {duplicates} duplicates, 0 new nodes, rankings unchanged "
                 f"{top10_same and order_same}")
        done()

    def rerun_bridge(self) -> None:
        done = self._phase("bridge_rerun_seconds")
        before = drills.fabric_counts(self.admin.status())
        notes_before = sorted(m["memory_id"] for m in self.admin.list_memories(scope=ORG_ID, limit=1000))
        events_before = len(self.admin.events(ORG_ID, limit=1000))
        publisher = Publisher(self.hq_client, watermark=self.dirs["bridge2"] / "publish_watermark.json",
                              ledger=self.dirs["bridge2"] / "publishes.jsonl")
        bridge = HqBridge(self.store_path, self.world.pack, publisher,
                          watermark=self.dirs["bridge2"] / "watermark.json")
        rerun = bridge.run()
        after = drills.fabric_counts(drills.wait_idle(self.admin, self.options.idle_timeout))
        notes_after = sorted(m["memory_id"] for m in self.admin.list_memories(scope=ORG_ID, limit=1000))
        self.doc.setdefault("idempotency", {})["bridge_rerun"] = {
            "publishes": rerun.publishes, "created": rerun.created, "duplicate": rerun.duplicate,
            "ambiguous": rerun.ambiguous, "new_memory_ids": len(rerun.memory_ids_created),
            "new_event_ids": len(rerun.event_ids_created),
            "stream_messages_before": before["stream_messages"], "stream_messages_after": after["stream_messages"],
            "active_notes_before": len(notes_before), "active_notes_after": len(notes_after),
            "notes_identical": notes_before == notes_after,
            "events_before": events_before, "events_after": len(self.admin.events(ORG_ID, limit=1000)),
            "store_unchanged": rerun.store_unchanged}
        self.log(f"drill 7: bridge rerun {rerun.publishes} publishes, {rerun.created} created, stream "
                 f"{before['stream_messages']} -> {after['stream_messages']}")
        done()

    # ------------------------------------------------------------------ the site restart's second half
    def restart_witness(self) -> None:
        """Drill 2's second half: a fresh ``E2ESiteProcess`` of the restarted site in a scratch directory, its graph
        built from nothing, re-answers the questions it answered after the restart with identical verdict bytes."""
        done = self._phase("restart_witness_seconds")
        world = self.world
        site = self.restart["site"]
        after = [qid for qid, result in self.results_by_id.items() if site in result.route]
        cut = self.restart["questions_before"]
        post = after[cut:]
        scratch = self.dirs["scratch"]
        restore_store(site, self.dirs["pristine"], scratch / "edge")
        cfg = ew.site_configs(world, edge=scratch / "edge", hq_dir=scratch / "hq", routing=self.options.routing)[site]
        (scratch / "hq").mkdir(parents=True, exist_ok=True)
        process = sg.E2ESiteProcess(cfg)
        identical = True
        try:
            for qid in post:
                question = self.asked_by_id[qid].question_bytes
                fresh = process.handle("question", question)
                stored = [r for r in self.results_by_id[qid].received if r.site == site]
                identical = identical and bool(stored) and sha256_hex(fresh) == stored[0].sha256
        finally:
            process.close()
        matrix = all(self.reference.get((qid, r.site)) == r.sha256
                     for qid, result in self.results_by_id.items() for r in result.received
                     if r.source == "site") if self.reference else None
        mismatches = [qid for qid, result in self.results_by_id.items() for r in result.received
                      if r.source == "site" and self.reference.get((qid, r.site)) != r.sha256]
        self.doc["recovery"]["site_restart"] = {
            "site": site, "questions_before": cut, "questions_after": len(post),
            "verdict_bytes_identical": identical, "nodes_before": self.restart["nodes_before"],
            "nodes_after": self.restart["nodes_after"],
            "nodes_added_after_restart": self.restart["nodes_after"] - self.restart["nodes_before"],
            "killed": self.restart["killed"], "answer_matrix_identical": matrix,
            "answer_matrix_mismatches": len(mismatches), "fresh_graph_questions": len(post)}
        self.log(f"drill 2: {len(post)} post-restart questions re-answered from a fresh graph, identical "
                 f"{identical}; answer matrix equals the reference {matrix}")
        done()

    # ------------------------------------------------------------------ the discovery score (c9's path side)
    def fabric_conclusions(self) -> list[dict[str, Any]]:
        """What the fabric holds per conclusion, as drill 5 read it back (``GET /memory``): the note's metadata
        (entity id, predicate, snapshot week; ``publish_api.conclusion_memory``), its ``metadata.value`` (the
        status, stored canonical by the service) and whether its lineage check held. The discovery score of
        section 10 is computed from these, never from HQ's store."""
        return [{"question_id": qid, "memory_id": row["memory_id"], "entity_id": row["entity_id"],
                 "predicate": row["predicate"], "snapshot_week": row["snapshot_week"], "status": row["value"],
                 "lineage_ok": bool(self.lineage_ok.get(qid))} for qid, row in self.notes_by_question.items()]

    def discovery(self) -> None:
        """Section 10's ``discovery``: the path's score against Gold, from the fabric's own notes and the labels
        doc (the planted patterns' keys and windows, every decoy's facet-0 key and window), the oracle rows of
        section 3.5 (M12, M20) with the fabric's status beside HQ's, and the simulator's two columns (M14, M24)."""
        done = self._phase("discovery_seconds")
        world, o = self.world, self.options
        corpus, gold = world.corpus, world.gold
        user_site: dict[int, str] = {}
        for record, row in zip(world.records, corpus.recs):
            user_site[int(row["uid"])] = record["site"]
        patterns = {p["id"]: p for p in world.labels["patterns"]}
        labels = {}
        counts = {"true": 0, "decoy": 0, "background": 0}
        for cand in self.candidates:
            lab = candidate_label(cand.key, cand.week, world.labels)
            labels[cand.key] = lab
            counts[lab["label"]] += 1
        fabric = self.fabric_conclusions()
        fabric_status = {c["question_id"]: c["status"] for c in fabric}
        oracle_rows: list[dict[str, Any]] = []
        for q in self.stored:
            cand = self.asked_by_id[q.question_id].candidate
            lab = labels[cand.key]
            if lab["label"] != "true":
                continue
            pattern = patterns[lab["pattern"]]
            window = self.asked_by_id[q.question_id].question["window"]
            facts = pattern_facts(world, int(pattern["pattern"]), user_site, window, q.route)
            expected, ambiguous, reason = expected_status(facts, cand.as_of)
            oracle_rows.append({"question_id": q.question_id, "anchor": pattern["anchor"],
                                "chain": pattern["chain"], "pattern": pattern["id"], "as_of": cand.as_of,
                                "snapshot_week": cand.week, "route": list(q.route), "expected": expected,
                                "ambiguous": ambiguous, "reason": reason,
                                "status": fabric_status.get(q.question_id), "hq_status": q.status,
                                "per_site_facet_counts": {s: f.to_dict() for s, f in facts.items()},
                                "lineage_ok": self.lineage_ok.get(q.question_id)})
        # found: Gold-discoverable patterns with an active ``supported`` note in the fabric whose metadata key and
        # snapshot week are the pattern's, and whose lineage check of drill 5 held (section 10)
        supported = [c for c in fabric if c["status"] == "supported" and c["lineage_ok"]]
        found: set[str] = set()
        for c in supported:
            key = series_key(ew.ENTITY_TYPE, str(c["entity_id"]), str(c["predicate"]))
            lab = candidate_label(key, str(c["snapshot_week"]), world.labels)
            if lab["label"] == "true":
                found.add(lab["pattern"])
        rare = {p["id"] for p in world.labels["patterns"] if p["rare"]}
        # decoys: a decoy of type t is accepted when a supported note sits on its (facet-0 anchor, chain) key with
        # a snapshot week inside its window; the acceptance of type t is the accepted over the count of type t
        by_type: dict[str, int] = {}
        accepted_decoys: dict[str, set[int]] = {}
        decoy_keys: dict[int, tuple[str, str, str]] = {}
        for decoy in world.labels["decoys"]:
            pid = int(decoy["pattern"])
            p = corpus.patterns[pid]
            first = int(p.facet_records[0][0]) if p.facet_records and p.facet_records[0] else None
            if first is None:
                continue
            anchor = ew.canonical_entity_id(corpus.entities[int(corpus.recs[first]["anchor"])])
            decoy_keys[pid] = (series_key(ew.ENTITY_TYPE, anchor, ew.CHAIN_PREDICATES[int(p.chain)]),
                               decoy["found_from"], decoy["found_to"])
            by_type[str(decoy["class"])] = by_type.get(str(decoy["class"]), 0) + 1
        classes = {int(d["pattern"]): str(d["class"]) for d in world.labels["decoys"]}
        for c in supported:
            key = series_key(ew.ENTITY_TYPE, str(c["entity_id"]), str(c["predicate"]))
            week = str(c["snapshot_week"])
            for pid, (key0, first, last) in decoy_keys.items():
                if key == key0 and first <= week <= last:
                    accepted_decoys.setdefault(classes[pid], set()).add(pid)
        accepted = {t: len(v) for t, v in accepted_decoys.items()}
        acceptance = {t: (accepted.get(t, 0) / by_type[t]) for t in by_type}
        background = 0
        for c in supported:
            key = series_key(ew.ENTITY_TYPE, str(c["entity_id"]), str(c["predicate"]))
            if candidate_label(key, str(c["snapshot_week"]), world.labels)["label"] == "background":
                background += 1
        discoverable = len(gold.discoverable)
        simulator, reason = ms.simulator_columns(o.world, o.seed)
        found_ids = sorted(found)
        self.doc["discovery"] = {
            "synthetic": True, "measurement": False, "gold_discoverable": discoverable,
            "gold_rare": len(gold.rare), "gold_common": discoverable - len(gold.rare),
            "gold_discoverable_patterns": sorted(patterns), "gold_rare_patterns": sorted(rare),
            "decoys_by_type": dict(sorted(by_type.items())),
            "candidates_true": counts["true"], "candidates_decoy": counts["decoy"],
            "candidates_background": counts["background"],
            "found_patterns": found_ids, "found": len(found_ids),
            "recall_all": len(found_ids) / discoverable if discoverable else None,
            "recall_rare": len([p for p in found_ids if p in rare]) / len(gold.rare) if gold.rare else None,
            "recall_common": (len([p for p in found_ids if p not in rare])
                              / (discoverable - len(gold.rare)) if discoverable - len(gold.rare) else None),
            "decoy_accepted_by_type": dict(sorted(accepted.items())),
            "decoy_acceptance_by_type": dict(sorted(acceptance.items())), "background_supported": background,
            "supported_conclusions": len(supported), "fabric_conclusions": fabric, "oracle_rows": oracle_rows,
            "granularity": "(anchor, chain predicate); no temporal-order check; the lexical judge",
            "simulator": simulator, "simulator_reason": reason}
        unambiguous = [r for r in oracle_rows if not r["ambiguous"]]
        self.log(f"discovery: {counts['true']} true / {counts['decoy']} decoy / {counts['background']} background "
                 f"candidates; {len(unambiguous)} unambiguous planted, {len(found_ids)} patterns found; the "
                 f"simulator's columns in {simulator['seconds'] if simulator else reason}")
        done()

    # ------------------------------------------------------------------ the whole run
    def checkpoint(self, name: str) -> None:
        """The partial document after one phase, written atomically to the report's own path (``phase`` named,
        ``completed`` false, the timings so far): a job stopped at its bound leaves a report to amend from
        (section 11's one-amendment rule needs the artifact), and the measure job's size phases read the same file
        (a report without ``checks`` is a phase that did not finish)."""
        rp.write(self.options.out / rp.REPORT_NAME,
                 {**self.doc, "phase": name, "completed": False, "timings": dict(sorted(self.timings.items()))})

    def run(self) -> dict[str, Any]:
        o = self.options
        started = utc_clock()
        t0 = time.perf_counter()
        self.doc = rp.skeleton(run_id=o.run_id, job="e2e", command=sys.argv, started_at=started)
        self.doc["run"].update({"world": o.world, "seed": o.seed, "mode": o.mode, "skip_drills": o.skip_drills,
                               "top_n": o.top_n, "out": str(o.out)})
        self.dirs = {name: o.out / name for name in ("world", "pristine", "ref", "main", "scratch", "fabric",
                                                     "bridge", "bridge2")}
        for path in self.dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        self.reference = {}
        self.restart = {}
        self.crossing = None
        self.doc["recovery"] = {}
        try:
            self.build_world()
            self.pristine_copies()
            self.checkpoint("world")
            if not o.skip_drills:
                self.reference_pass()
                self.checkpoint("reference_pass")
            self.main_pass()
            self.checkpoint("main_pass")
            self.rankings_before = self.rank_fixed_queries("before") if o.queries > 0 else {}
            self.node_ids_before = {sid: sg.node_ids_digest(sg.graph_node_ids(self.main_configs[sid].graph_path, sid))
                                    for sid in self.world.site_ids}
            self.node_count_before = {sid: len(sg.graph_node_ids(self.main_configs[sid].graph_path, sid))
                                      for sid in self.world.site_ids}
            self.collect_ingest()
            self.checkpoint("fixed_queries")
            if not o.skip_drills:
                self.restart_witness()
                self.checkpoint("restart_witness")
                # section 4's scans over every crossing byte: the end-to-end job's (c2); the measure job's sizes
                # (``--skip-drills``) carry c0, c7, c8 and c9 only and stay inside M14's budgets
                self.crossing_checks()
                self.checkpoint("crossing")
            self.start_fabric()
            try:
                self.publish_with_broker_kill()
                self.checkpoint("publish")
                if not o.skip_drills:
                    self.forgeries()
                    self.checkpoint("forgeries")
                else:
                    self.doc["signatures"] = {"skipped": True}
                self.collect_fabric()
                self.lineage()
                self.checkpoint("lineage")
                self.discovery()
                self.checkpoint("discovery")
                if not o.skip_drills:
                    self.rebuild_service()
                    self.checkpoint("rebuild")
                    self.reingest()
                    self.checkpoint("reingest")
                    self.rerun_bridge()
                    self.checkpoint("bridge_rerun")
            finally:
                self.driver.down()
        finally:
            self.world.pipeline.store.close()
        self.doc["guards"] = guard_report()
        self.doc["timings"] = dict(sorted(self.timings.items()))
        self.doc["run"].update({"finished_at": utc_clock(), "seconds": time.perf_counter() - t0,
                                "code_dirty_end": code_dirty(list(rp.DIRTY_PATHS))})
        self.doc.update({"phase": "finished", "completed": True})
        # M2: the suites' junit evidence is read from the suites CLI's directory (``--suites DIR``) and criterion
        # 10's check runs over it; without it the run's checks are reported but the run does not count
        # (``verdict.pass`` false, reason ``suites_not_green``)
        self.doc["run"].update(su.evidence_block(o.suites))
        not_run = {"c5_measured": "the measure job (S7): the rate sweep and the three node counts",
                   "c6_ci": "the workflow test (S8)"}
        if self.doc["run"]["suites_evidence"] != su.EVIDENCE:
            not_run["c10_suites_green"] = "no suites directory was given (--suites DIR; M2)"
        if o.skip_drills:
            not_run.update({name: "--skip-drills" for name in ("c1_signed", "c2_lineage", "c3_idempotent",
                                                               "c4_recovery")})
        names = [n for n in rp.E2E_CHECKS if n not in not_run]
        self.doc["checks"] = rp.run_checks(self.doc, names)
        self.doc["verdict"] = rp.verdict(self.doc["checks"], not_run=not_run,
                                         suites_green=rp.suites_green(self.doc))
        return self.doc


# --------------------------------------------------------------------------------------------------- the CLI

def summary(doc: Mapping[str, Any]) -> str:
    v = doc["verdict"]
    lines = [f"e2e-ingest: verdict pass={v['pass']} checks_pass={v.get('checks_pass')} "
             f"reason={v.get('reason')} complete={v['complete']} "
             f"world={doc['world']['target_users']} seed={doc['world']['seed']} "
             f"seconds={doc['run']['seconds']:.0f} (synthetic, measurement: false)"]
    for name, check in doc["checks"].items():
        state = "ok" if check["ok"] else "FAILED: " + ", ".join(check["failed"])
        lines.append(f"  {name}: {state} ({check['assertions']} assertions)")
    for name, why in doc["verdict"]["not_run"].items():
        lines.append(f"  {name}: not run ({why})")
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"error: {out} exists and is not empty (pick a new --out)", file=sys.stderr)
        return 2
    if args.mode not in MODES:
        print(f"error: --mode is one of {', '.join(MODES)}", file=sys.stderr)
        return 2
    if args.queries < 0 or (args.top_n is not None and args.top_n < 1):
        print("error: --queries is 0 or more and --top-n is 1 or more", file=sys.stderr)
        return 2
    suites = Path(args.suites) if args.suites else None
    if suites is not None and not suites.is_dir():
        print(f"error: --suites {suites} is not a directory", file=sys.stderr)
        return 2
    options = Options(out=out, world=args.world, seed=args.seed, mode=args.mode, skip_drills=bool(args.skip_drills),
                      jobs=args.jobs, run_id=args.run_id, queries=args.queries, top_n=args.top_n, suites=suites,
                      routing=str(Path(args.routing).resolve()) if args.routing else None)
    run = E2ERun(options=options)
    doc = run.run()
    path = out / rp.REPORT_NAME
    sha = rp.write(path, doc)
    print(summary(doc))
    print(f"report: {path} sha256={sha}")
    return 0 if doc["verdict"]["pass"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    """M12: a written report re-read; every aggregate recomputed from the per-item arrays it carries and every check
    run again over it (the CI-only assertions included when the report names a CI run, wherever ``check`` runs).
    Exit 0 only when the recomputation agrees, the checks reproduce the stored ones assertion for assertion and the
    stored verdict passes; ``--recorded`` also requires ``run.github_run_id`` and ``run.command``."""
    path = Path(args.report)
    doc = strict_load(path.read_bytes())
    measure = (doc.get("run") or {}).get("job") == "measure"
    problems = rp.recompute(doc, base_dir=path.parent)
    stored = doc.get("checks") or {}
    again = rp.run_checks(doc, [n for n in stored if n in rp.CHECKS])
    for name, check in again.items():
        was = stored[name]
        if (check["ok"] != was.get("ok") or check["failed"] != was.get("failed")
                or check["assertions"] != was.get("assertions")):
            problems.append(f"checks.{name}: the report says ok={was.get('ok')} failed={was.get('failed')} "
                            f"assertions={was.get('assertions')}, re-run gives ok={check['ok']} "
                            f"failed={check['failed']} assertions={check['assertions']}")
    if args.recorded:
        run = doc.get("run") or {}
        if not run.get("github_run_id"):
            problems.append("run.github_run_id: a recorded run names its CI run")
        if not run.get("command"):
            problems.append("run.command: a recorded run names its command")
    if measure:
        from .measure import summary as measure_summary
        print(measure_summary(doc))
    else:
        print(summary(doc))
    for problem in problems:
        print(f"  recompute: {problem}")
    print(f"check: {path} aggregates {'agree' if not problems else 'DISAGREE'} ({len(problems)} problems); "
          f"verdict pass={doc.get('verdict', {}).get('pass')}")
    return 0 if not problems and bool(doc.get("verdict", {}).get("pass")) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.e2e_ingest.harness",
                                     description="The EMERGENCE end-to-end ingestion run (synthetic).")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="one end-to-end run and its report")
    r.add_argument("--world", "--size", dest="world", type=int, default=200, choices=sorted(ew.WORLD_TABLE),
                   help="target_users of the EMERGENCE world (section 3.1); --size is the brief's spelling")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--out", required=True)
    r.add_argument("--mode", default="process", choices=MODES)
    r.add_argument("--skip-drills", action="store_true",
                   help="the world, the main pass and the publish only; the drilled criteria are then not run")
    r.add_argument("--jobs", type=int, default=4, help="threads for the reference pass and the fixed queries")
    r.add_argument("--queries", type=int, default=FIXED_QUERIES,
                   help="fixed queries per site (the brief: 20; 0 asks none, the measure job's sizes)")
    r.add_argument("--top-n", type=int, default=ew.TOP_N,
                   help="the candidate list's bound (the brief: 60; the measure job asks 20 at 2,000 users)")
    r.add_argument("--run-id", default="local")
    r.add_argument("--routing", default=None,
                   help="a directory holding <site_id>.routing.json for every site; each file's judge_record route "
                        "(an endpoint with boundary exactly site:<site_id>) judges that site's records instead of the "
                        "lexical judge")
    r.add_argument("--suites", default=None,
                   help="the directory the suites CLI wrote (M2's junit evidence); criterion 10 runs over it")
    c = sub.add_parser("check", help="re-read a report: recompute its aggregates and re-run its checks (M12)")
    c.add_argument("report")
    c.add_argument("--recorded", action="store_true", help="require the CI run id and the command of a recorded run")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return {"run": cmd_run, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
