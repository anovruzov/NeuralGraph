"""Measurement (``docs/collective/E2E-INGEST.md`` section 10, M7, M14, M18, M19, M24; S7): the measure job and the
simulator's two columns the end-to-end job reads beside the path's discovery score (criterion 9).

    python -m research.e2e_ingest measure --out DIR [--sizes 200,600,2000] [--suites DIR]

The job runs the phases of M14's table in its order, each under its own wall-clock budget, and writes the report
(``e2e-ingest-measure.json``, ``write_json_atomic``) when each phase ends with ``completed`` and ``phase``, so a
timeout leaves a partial report:

1. **size 200** (60 candidates): one end-to-end run of the harness in a child process (``python -m
   research.e2e_ingest.harness run --world 200 --skip-drills --queries 0 --top-n 60``): the world, the seven site
   graphs, the main pass through Tesseract routing, the publish into a real keyed fabric, the lineage read-back,
   the discovery score and the simulator's columns; its report is the size's and its scaling row is taken from it
   (``report.scaling_row``). A size that exceeds its budget is interrupted (SIGINT, then the process group killed)
   and reported ``completed: false``.
2. **the rate sweep** (M7, M19): a fresh fabric; the size-200 run's verdict and conclusion events (read from its
   HQ store through the bridge's own planner) re-keyed ``{step}:{cycle}:{original key}`` and published one POST
   per event at 2, 4, 8, 16 and 32 events/s, each step for 60 s in its own organisation (``emergence-rate-02``,
   ...), each conclusion citing the verdict event ids the API returned for its cycle; per step ``created_at`` and
   ``applied_at`` of every event of the step are read from the service database (``events``, ``mode=ro``), a
   conclusion's latency being its ``memory.observed`` event's (M18); the sweep stops at the first step whose apply
   p95 exceeds 1.0 s, whose rate the publisher could not hold, or after 32 events/s;
   ``throughput.sustained_events_per_second`` is the highest held rate whose p95 stayed at or under 1.0 s.
3. **Tesseract at 600, 5,882 and 20,000 nodes**: three harness-side measurement graphs, never asked a question,
   each built with ``site_graph`` from the first N records of the 2,000-user world in record order (ingested into a
   scratch site store, the graph synced from it), and the 20 fixed queries of section 3.5 (the size-200 run's,
   ``fixed_questions.json`` beside its report) ranked over each; p50 and p95 per N.
4. **size 600** (60 candidates) and 5. **size 2,000** (20 candidates, M14), as phase 1.

Then the scaling table (one row per completed size), criterion 5's per-hop block, the checks ``c5_measured`` and,
with ``--suites DIR``, ``c10_suites_green`` (M2), and the verdict. Every number is computed from the phases' own
logs, the fabric's database and the sites' graphs; nothing is typed in. The service stamps ``created_at`` and
``applied_at`` at one-second resolution (``mycelic/models.py`` ``now_iso``), which the report states.

The simulator's columns (:func:`simulator_columns`) import ``runner.build_world`` (the same ``build_org``,
``build_corpus``, ``make_gold`` calls the world adapter makes, ``runner.py:54-62``), ``runner.run_arch``
(``runner.py:193-244``), ``models.allocation`` (``models.py:215-217``) and ``evalm.evaluate`` (``evalm.py:101``);
never ``run_matrix``, which appends to the committed artifacts (``runner.py:253-254``). They are labelled
``simulated: true`` (the tiers are assumed capability vectors, ``models.py:5``) and carry the allocation name and
the five metric fields only, never a tier label (M24). Everything here is synthetic and stamped ``measurement:
false``; no record text and no record ref is printed, logged or reported.
"""
from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.evaluate.baselines import closing_date, world_weeks
from mycelic.collective.experiments.common import code_dirty, utc_clock
from mycelic.collective.jsonio import sha256_hex, strict_load
from mycelic.collective.packs.loader import load_pack_dir
from mycelic.sdk import MycelicClient
from research.mycelic import evalm, runner
from research.mycelic.models import allocation
from research.routing_spike.site_process import SiteConfig, query_text

from . import drills
from . import emergence_world as ew
from . import report as rp
from . import site_graph as sg
from . import suites as su
from .hq_bridge import HqBridge
from .publish_api import AGENT_ID, CONCLUSION_EVENT, ORG_ID, VERDICT_EVENT

__all__ = ["ALLOCATION", "ARCHITECTURES", "METRICS", "SIMULATOR_BUDGET_SECONDS", "MeasureError", "MeasureOptions",
           "MeasureRun", "main", "phase_plan", "rekey", "simulator_columns", "sweep_agent", "sweep_org"]

ROOT = Path(__file__).resolve().parents[2]
ALLOCATION = "back-loaded"
ARCHITECTURES = ("H_mycelic_full", "A2_chunked_ctx")
METRICS = ("discovery_recall", "rare_signal_recall", "decoy_acceptance_all", "recall_at_100", "compute_units")
#: Section 10: the simulator's columns run under a 10-minute budget; past it they are null with ``reason: budget``,
#: which c9 accepts for the 2,000-user world only.
SIMULATOR_BUDGET_SECONDS = 600.0
#: The sweep's settle (idle) wait after each step's 60 s, and the share of a step's rate the publisher must hold.
SWEEP_SETTLE_TIMEOUT = 120.0
RATE_HELD_SHARE = 0.9
#: The 2,000-user world of section 3.1, the source of the measurement graphs' records.
GRAPH_WORLD = 2000
STAMP_RESOLUTION_SECONDS = 1


class MeasureError(RuntimeError):
    """A phase could not do what it set out to (a measurement store that took fewer records than given)."""


# --------------------------------------------------------------------------------------------------- the simulator

def simulator_columns(target_users: int, seed: int, *,
                      budget_seconds: float = SIMULATOR_BUDGET_SECONDS) -> tuple[dict[str, Any] | None, str | None]:
    """The simulator's ``H_mycelic_full`` and ``A2_chunked_ctx`` on the same world (``build_world(target_users,
    seed)``), under the committed calibration, as ``(columns, reason)``: the columns, or ``None`` with ``reason``
    ``"budget"`` when the budget ran out before both architectures were evaluated."""
    t0 = time.perf_counter()
    world = runner.build_world(target_users, seed)
    alloc = allocation(ALLOCATION)
    architectures: dict[str, dict[str, Any]] = {}
    for name in ARCHITECTURES:
        if time.perf_counter() - t0 > budget_seconds:
            return None, "budget"
        t1 = time.perf_counter()
        result = runner.run_arch(name, world, alloc, seed)
        metrics = evalm.evaluate(world.corpus, world.gold, result)
        architectures[name] = {**{k: float(metrics[k]) for k in METRICS}, "seconds": time.perf_counter() - t1}
    if time.perf_counter() - t0 > budget_seconds:
        return None, "budget"
    return {"simulated": True, "synthetic": True, "measurement": False, "allocation": ALLOCATION,
            "target_users": int(target_users), "seed": int(seed), "users": len(world.org.user_ids),
            "records": int(len(world.corpus.recs)), "metrics": list(METRICS), "architectures": architectures,
            "seconds": time.perf_counter() - t0, "budget_seconds": float(budget_seconds),
            "note": "the simulator's discovery_recall matches reported hypotheses through its own extraction and "
                    "chain checks; the path's score is at (anchor, chain) granularity with no temporal-order check "
                    "and the lexical judge; the two are not the same metric"}, None


# --------------------------------------------------------------------------------------------------- the plan

def phase_plan(sizes: Sequence[int]) -> list[str]:
    """M14's order: the smallest size, the rate sweep (it needs only that size's events), the three node counts,
    then the larger sizes."""
    ordered = sorted(set(int(s) for s in sizes))
    if not ordered:
        raise ValueError("at least one size") from None
    return [f"size-{ordered[0]}", "rate-sweep", "tesseract-nodes", *[f"size-{s}" for s in ordered[1:]]]


def budget_seconds(phase: str) -> float:
    return float(rp.MEASURE_PHASE_BUDGET_MINUTES[phase]) * 60.0


def sweep_org(rate: int) -> str:
    """Section 3.6: one organisation per rate step (``emergence-rate-02``, ``-04``, ...)."""
    return f"{ORG_ID}-rate-{int(rate):02d}"


def sweep_agent(rate: int) -> str:
    """The step's HQ agent (``hq-rate-02``, ...): agent ids are one namespace across organisations in the service
    (a second ``register_agent`` of ``hq`` is refused), so each step's organisation gets its own."""
    return f"{AGENT_ID}-rate-{int(rate):02d}"


def rekey(event: Mapping[str, Any], step: str, cycle: int) -> dict[str, Any]:
    """M7: the step's events are the end-to-end world's re-keyed ``{step}:{cycle}:{original key}``, so every POST
    is a new event."""
    return {**event, "idempotency_key": f"{step}:{int(cycle)}:{event['idempotency_key']}"}


@dataclass
class MeasureOptions:
    out: Path
    sizes: tuple[int, ...] = rp.MEASURE_SIZES
    seed: int = 0
    jobs: int = 4
    run_id: str = "local"
    suites: Path | None = None
    rates: tuple[int, ...] = rp.MEASURE_RATE_STEPS
    step_seconds: float = rp.MEASURE_STEP_SECONDS
    node_counts: tuple[int, ...] = rp.MEASURE_NODE_COUNTS
    queries: int = rp.FIXED_QUERIES
    python: str = sys.executable
    budgets: Mapping[str, float] = field(default_factory=lambda: {
        k: float(v) * 60.0 for k, v in rp.MEASURE_PHASE_BUDGET_MINUTES.items()})


# --------------------------------------------------------------------------------------------------- the run

@dataclass
class MeasureRun:
    """The measure job: the phases of M14 in order, each under its budget, the report written after each."""

    options: MeasureOptions
    doc: dict[str, Any] = field(default_factory=dict)

    def log(self, message: str) -> None:
        print(f"[measure {time.strftime('%H:%M:%S')}] {message}", flush=True)

    @property
    def path(self) -> Path:
        return self.options.out / rp.MEASURE_REPORT_NAME

    def write(self) -> str:
        return rp.write(self.path, self.doc)

    # ---- phase bookkeeping
    def phase(self, name: str, work: Callable[[float], dict[str, Any]]) -> dict[str, Any]:
        """Run one phase under its budget: ``work(budget_seconds)`` returns the phase's block with ``completed``;
        an exception or an overrun is recorded, never raised, so the later phases and the report still happen."""
        budget = float(self.options.budgets[name])
        entry: dict[str, Any] = {"name": name, "budget_seconds": budget, "started_at": utc_clock(),
                                 "completed": False, "reason": None, "seconds": None}
        self.doc["phase"] = name
        self.doc["phases"].append(entry)
        self.write()
        self.log(f"phase {name}: budget {budget:.0f}s")
        t0 = time.perf_counter()
        block: dict[str, Any]
        try:
            block = work(budget)
        except Exception as exc:                     # noqa: BLE001 - the report must say what happened
            block = {"completed": False, "reason": f"{exc.__class__.__name__}: {str(exc)[:300]}"}
        seconds = time.perf_counter() - t0
        entry.update({"seconds": seconds, "finished_at": utc_clock(), "completed": bool(block.get("completed")),
                      "reason": block.get("reason")})
        if entry["completed"] and seconds > budget:
            entry.update({"completed": False, "reason": "budget"})
            block["completed"], block["reason"] = False, "budget"
        self.doc["completed"] = all(p["completed"] for p in self.doc["phases"])
        self.write()
        state = "completed" if entry["completed"] else f"NOT completed ({entry['reason']})"
        self.log(f"phase {name}: {state} in {seconds:.0f}s")
        return block

    # ---- phase 1, 4, 5: one size
    def size_phase(self, size: int) -> Callable[[float], dict[str, Any]]:
        def work(budget: float) -> dict[str, Any]:
            o = self.options
            out = o.out / f"size-{size}"
            log = o.out / f"size-{size}.log"
            command = [o.python, "-m", "research.e2e_ingest.harness", "run", "--world", str(size), "--seed",
                       str(o.seed), "--out", str(out), "--skip-drills", "--queries", "0", "--top-n",
                       str(rp.MEASURE_CANDIDATES.get(size, ew.TOP_N)), "--run-id", f"{o.run_id}-size-{size}",
                       "--jobs", str(o.jobs)]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            t0 = time.perf_counter()
            timed_out = False
            with open(log, "wb") as fh:
                proc = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                try:
                    proc.wait(timeout=budget)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self.log(f"size {size}: over its budget of {budget:.0f}s, interrupting")
                    _stop_group(proc)
            seconds = time.perf_counter() - t0
            block: dict[str, Any] = {"target_users": size, "command": command, "log": log.name, "exit_code":
                                     proc.returncode, "timed_out": timed_out, "seconds": seconds, "completed": False,
                                     "reason": None, "report": None, "report_sha256": None}
            report = out / rp.REPORT_NAME
            if not report.is_file():
                block["reason"] = "budget" if timed_out else f"no report (exit {proc.returncode})"
                return block
            data = report.read_bytes()
            size_doc = strict_load(data)
            block.update({"report": str(report.relative_to(o.out)), "report_sha256": sha256_hex(data),
                          "row": rp.scaling_row(size_doc), "checks": size_doc.get("checks"),
                          "verdict": size_doc.get("verdict"), "timings": size_doc.get("timings"),
                          "discovery": {k: v for k, v in (size_doc.get("discovery") or {}).items()
                                        if k not in ("oracle_rows", "fabric_conclusions")},
                          "world": {k: v for k, v in (size_doc.get("world") or {}).items()
                                    if k not in ("site_ids", "pack_hashes", "records_per_site")},
                          "paths": (size_doc.get("run") or {}).get("paths"),
                          "completed": not timed_out and bool(size_doc.get("checks"))})
            if timed_out:
                block["reason"] = "budget"
            self.doc["sizes"][str(size)] = block
            return block
        return work

    # ---- phase 2: the rate sweep
    def _first_size_block(self) -> dict[str, Any] | None:
        for size in sorted(int(s) for s in self.options.sizes):
            block = self.doc["sizes"].get(str(size))
            if block and block.get("completed") and block.get("paths"):
                return block
        return None

    def sweep_phase(self, budget: float) -> dict[str, Any]:
        o = self.options
        source = self._first_size_block()
        if source is None:
            return {"completed": False, "reason": "no completed size run to take the events from"}
        store = Path(source["paths"]["hq_store"])
        pack = load_pack_dir(ew.PACK_DIR, expected_id=ew.PACK_ID)
        # the bridge is the planner and event builder here (``plan``, ``events_for``); nothing is published through
        # it, so it gets no publisher
        bridge = HqBridge(store, pack, publisher=None)  # type: ignore[arg-type]
        plans = [bridge.plan(row) for row in bridge.reader.conclusions()]
        if not plans:
            return {"completed": False, "reason": "the size run's HQ store holds no conclusion"}
        # the harness imports this module for the simulator's columns, so its helpers are imported here, late
        from .harness import nats_binary_version, nats_server_bin
        t0 = time.perf_counter()
        nats_bin = nats_server_bin()
        driver = drills.E2EProcessDriver(workdir=o.out / "sweep" / "fabric", nats_bin=nats_bin)
        driver.up()
        steps: list[dict[str, Any]] = []
        stopped: str | None = None
        block: dict[str, Any] = {
            "completed": False, "reason": None, "steps": steps, "rates": list(o.rates),
            "step_seconds": o.step_seconds, "bound_seconds": rp.MEASURE_APPLY_P95_BOUND,
            "rate_held_share": RATE_HELD_SHARE, "stamp_resolution_seconds": STAMP_RESOLUTION_SECONDS,
            "events_source": {"size": source["target_users"], "conclusions": len(plans),
                              "events_per_cycle": sum(len(p.verdicts) + 1 for p in plans)},
            "sustained_events_per_second": None, "stopped": None}
        self.doc["throughput"] = block
        try:
            admin = MycelicClient(driver.base_url, driver.admin_token)
            block["fabric"] = {"broker_version": drills.broker_version(driver),
                               "broker_binary_version": nats_binary_version(nats_bin),
                               "driver_class": f"{type(driver).__module__}.{type(driver).__qualname__}"}
            for rate in o.rates:
                remaining = budget - (time.perf_counter() - t0)
                if remaining < o.step_seconds + 15.0:
                    stopped = "budget"
                    break
                step = self.sweep_step(driver, admin, bridge, plans, int(rate))
                steps.append(step)
                self.write()
                self.log(f"sweep: {rate} events/s -> {step['events']} events in {step['duration_seconds']:.1f}s, "
                         f"achieved {step['achieved_events_per_second']:.2f}/s, apply p50 {step['apply_p50']} "
                         f"p95 {step['apply_p95']} ({step['n']} samples), settle {step['settle_seconds']:.1f}s")
                if not step["rate_held"]:
                    stopped = "rate_not_held"
                    break
                if step["apply_p95"] is None or step["apply_p95"] > rp.MEASURE_APPLY_P95_BOUND:
                    stopped = "apply_p95_over_bound"
                    break
            else:
                stopped = "last_step"
        finally:
            driver.down()
        held = [s["rate"] for s in steps if s["rate_held"] and s["apply_p95"] is not None
                and s["apply_p95"] <= rp.MEASURE_APPLY_P95_BOUND]
        block.update({"sustained_events_per_second": max(held) if held else None, "stopped": stopped,
                      "completed": stopped != "budget" and bool(steps),
                      "reason": "budget" if stopped == "budget" else None, "seconds": time.perf_counter() - t0})
        return block

    def sweep_step(self, driver: Any, admin: MycelicClient, bridge: HqBridge, plans: Sequence[Any],
                   rate: int) -> dict[str, Any]:
        """One step: ``rate`` events/s for ``step_seconds`` in the step's own organisation, every POST a new event
        (M7), then idle, then every event's stamps read from the service database (``mode=ro``)."""
        o = self.options
        org, agent, step_id = sweep_org(rate), sweep_agent(rate), f"rate-{rate:02d}"
        registered = admin.register_agent(agent_id=agent, enterprise=org)
        client = MycelicClient(driver.base_url, registered["api_key"])
        results: list[dict[str, Any]] = []
        state = {"i": 0, "lag": 0.0}
        t0 = time.perf_counter()

        def post(event: Mapping[str, Any]) -> dict[str, Any]:
            target = t0 + state["i"] / rate
            now = time.perf_counter()
            if target > now:
                time.sleep(target - now)
            state["lag"] = max(state["lag"], time.perf_counter() - target)
            answer = client.publish_events([dict(event)])
            entry = answer["results"][0]
            state["i"] += 1
            result = {"type": event["type"], "event_id": entry["event_id"], "created": bool(entry.get("created")),
                      "memory_event_id": entry.get("memory_event_id"), "memory_id": entry.get("memory_id")}
            results.append(result)
            return result

        cycle = 0
        while time.perf_counter() - t0 < o.step_seconds:
            for plan in plans:
                if time.perf_counter() - t0 >= o.step_seconds:
                    break
                ids = [post(rekey(e, step_id, cycle))["event_id"] for e in bridge.events_for(plan)]
                post(rekey(bridge.events_for(plan, ids)[0], step_id, cycle))
            cycle += 1
        duration = time.perf_counter() - t0
        t1 = time.perf_counter()
        status = drills.fabric_counts(drills.wait_idle(admin, SWEEP_SETTLE_TIMEOUT))
        settle = time.perf_counter() - t1
        rows = db_events(driver, org)
        samples: list[dict[str, Any]] = []
        beside: list[dict[str, Any]] = []
        for r in results:
            if r["type"] == VERDICT_EVENT:
                sample = _sample(rows, r["event_id"], "verdict")
            else:
                sample = _sample(rows, r["memory_event_id"], "conclusion")
                agent = _sample(rows, r["event_id"], "conclusion_agent_event")
                if agent is not None:
                    beside.append(agent)
            if sample is not None:
                samples.append(sample)
        applied_all = len(samples) == len(results) and all(
            rows.get(r["event_id"], {}).get("status") == "applied" for r in results)
        achieved = len(results) / duration if duration > 0 else 0.0
        stat = rp.stat_block(samples)
        return {"rate": rate, "step": step_id, "org": org, "agent": agent, "events": len(results),
                "created": sum(1 for r in results if r["created"]),
                "duplicate": sum(1 for r in results if not r["created"]),
                "conclusions": sum(1 for r in results if r["type"] == CONCLUSION_EVENT), "cycles": cycle,
                "duration_seconds": duration, "achieved_events_per_second": achieved,
                "rate_held": achieved >= RATE_HELD_SHARE * rate, "max_lag_seconds": state["lag"],
                "settle_seconds": settle, "apply_p50": stat["p50"], "apply_p95": stat["p95"], "n": stat["n"],
                "samples": stat["samples"], "applied_all": applied_all, "events_in_db": len(rows),
                "conclusion_agent_events": {k: v for k, v in rp.stat_block(beside).items() if k != "samples"},
                "outbox_pending": status["outbox_pending"], "consumer_pending": status["consumer_pending"],
                "stream_messages": status["stream_messages"]}

    # ---- phase 3: Tesseract at three node counts
    def fixed_questions(self) -> tuple[list[dict[str, Any]], str]:
        """Section 3.5's 20 fixed questions: the smallest size run's file, else built from a scratch world."""
        source = self._first_size_block()
        if source is not None:
            path = Path(source["paths"]["fixed_questions"])
            if path.is_file():
                return list(strict_load(path.read_bytes())), str(path.relative_to(self.options.out))
        size = min(int(s) for s in self.options.sizes)
        world = ew.build_emergence_world(size, self.options.seed, self.options.out / "tesseract" / "world")
        try:
            return ew.fixed_questions(world, rp.FIXED_QUERIES), f"a scratch {size}-user world"
        finally:
            world.pipeline.close()

    def tesseract_phase(self, budget: float) -> dict[str, Any]:
        o = self.options
        t0 = time.perf_counter()
        questions, source = self.fixed_questions()
        questions = questions[:o.queries]
        org, corpus, gold = ew.generate(GRAPH_WORLD, o.seed)
        records = ew.build_records(corpus, seed=o.seed)
        ids = ew.entity_ids(corpus)
        pack = load_pack_dir(ew.PACK_DIR, expected_id=ew.PACK_ID)
        as_of = closing_date(pack, world_weeks(ew.START, ew.N_WEEKS)[-1])
        clock = f"{as_of}T12:00:00Z"
        graphs: dict[str, Any] = {}
        block: dict[str, Any] = {"completed": False, "reason": None, "graphs": graphs, "queries_from": source,
                                 "queries": len(questions), "node_counts": list(o.node_counts),
                                 "world": {"target_users": GRAPH_WORLD, "seed": o.seed, "records": len(records),
                                           "sites": len(ew.site_ids(org)), "as_of": as_of},
                                 "prepare_seconds": time.perf_counter() - t0}
        self.doc["tesseract_nodes"] = block
        for n in o.node_counts:
            if n > len(records):
                block["reason"] = f"the world holds {len(records)} records, fewer than {n}"
                return block
            if time.perf_counter() - t0 > budget:
                block["reason"] = "budget"
                return block
            site_id = f"measure-{n:05d}"
            edge, hq = o.out / "tesseract" / site_id / "edge", o.out / "tesseract" / site_id / "hq"
            edge.mkdir(parents=True, exist_ok=True)
            hq.mkdir(parents=True, exist_ok=True)
            # the first N records in record order, each with its ``site`` set to the measurement site (a site's
            # store takes its own records only); nothing else of a record changes
            site = EdgeSite(pack, site_id, edge, runtime=None, clock=lambda: clock, master_data={"entity": ids},
                            hq_dir=hq)
            t1 = time.perf_counter()
            try:
                got = site.ingest([{**r, "site": site_id} for r in records[:n]])
            finally:
                site.close()
            ingest_seconds = time.perf_counter() - t1
            if got.ingested != n or got.duplicates or any(got.rejected.values()):
                raise MeasureError(f"the measurement store took {got.ingested} of {n} records")
            cfg = SiteConfig(pack=pack.directory, site_id=site_id, edge_dir=str(edge), hq_dir=str(hq),
                             master_data={"entity": list(ids)}, demo_seed=o.seed, retrieval="tesseract",
                             max_records=ew.MAX_RECORDS, graph_path=str(edge / f"site-{site_id}.graph.sqlite3"),
                             clock_start=clock, embed_dim=ew.EMBED_DIM)
            process = sg.E2ESiteProcess(cfg)
            samples: list[dict[str, Any]] = []
            try:
                nodes, build = process.retrieval.nodes, process.retrieval.build_seconds
                for q in questions:
                    if time.perf_counter() - t0 > budget:
                        break
                    text = query_text(pack, q)
                    t2 = time.perf_counter()
                    order = process.retrieval.rank(text, q["as_of"])
                    samples.append({"question_id": q["question_id"], "seconds": time.perf_counter() - t2,
                                    "nodes": nodes, "returned": len(order)})
            finally:
                process.close()
            stat = rp.stat_block(samples)
            graphs[str(n)] = {"records": n, "ingested": got.ingested, "ingest_seconds": ingest_seconds,
                              "nodes": nodes, "graph_nodes_db": len(sg.graph_node_ids(cfg.graph_path, site_id)),
                              "build_seconds": build, "seconds_per_1k_records": build / nodes * 1000 if nodes else None,
                              "queries": stat["n"], "p50": stat["p50"], "p95": stat["p95"],
                              "samples": stat["samples"], "completed": stat["n"] == len(questions)}
            self.write()
            self.log(f"tesseract: {nodes} nodes built in {build:.1f}s; {stat['n']} queries p50 {stat['p50']} "
                     f"p95 {stat['p95']}")
            if not graphs[str(n)]["completed"]:
                block["reason"] = "budget"
                return block
        block["completed"] = all(str(n) in graphs and graphs[str(n)]["completed"] for n in o.node_counts)
        block["seconds"] = time.perf_counter() - t0
        return block

    # ---- the whole job
    def run(self) -> dict[str, Any]:
        o = self.options
        started = utc_clock()
        t0 = time.perf_counter()
        o.out.mkdir(parents=True, exist_ok=True)
        self.doc = rp.skeleton(run_id=o.run_id, job="measure", command=sys.argv, started_at=started)
        self.doc["run"].update({"seed": o.seed, "out": str(o.out), "python": o.python, "jobs": o.jobs})
        self.doc.update({"sizes_requested": [int(s) for s in sorted(set(o.sizes))],
                         "sizes_planned": [int(s) for s in rp.MEASURE_SIZES],
                         "candidates_per_size": {str(k): v for k, v in rp.MEASURE_CANDIDATES.items()},
                         "budgets_seconds": {k: float(v) for k, v in o.budgets.items()},
                         "phase_plan": phase_plan(o.sizes), "phases": [], "phase": None, "completed": False,
                         "sizes": {}, "throughput": None, "tesseract_nodes": None})
        for name in self.doc["phase_plan"]:
            if name.startswith("size-"):
                self.phase(name, self.size_phase(int(name[len("size-"):])))
            elif name == "rate-sweep":
                self.phase(name, self.sweep_phase)
            elif name == "tesseract-nodes":
                self.phase(name, self.tesseract_phase)
        self.finish(t0)
        return self.doc

    def finish(self, t0: float) -> None:
        o = self.options
        doc = self.doc
        rows = [doc["sizes"][str(s)]["row"] for s in doc["sizes_requested"]
                if (doc["sizes"].get(str(s)) or {}).get("completed")]
        graphs = (doc.get("tesseract_nodes") or {}).get("graphs") or {}
        th = doc.get("throughput") or {}
        doc["scaling_table"] = rows
        doc["per_hop"] = {
            "runner_dependent": True, "cpu_model": doc["runner"]["cpu_model"],
            "site_graph_seconds_per_1k_records": {str(r["target_users"]): r["graph_seconds_per_1k_records"]
                                                  for r in rows},
            "tesseract_per_question": {str(r["target_users"]): {"p50": r["tesseract_p50"], "p95": r["tesseract_p95"],
                                                                "rankings": r["rankings"]} for r in rows},
            "tesseract_nodes": {n: {"p50": g.get("p50"), "p95": g.get("p95"), "nodes": g.get("nodes")}
                                for n, g in sorted(graphs.items())},
            "question_to_verdict": {str(r["target_users"]): {
                "per_site_p50": r["question_to_verdict_per_site_p50"],
                "per_site_p95": r["question_to_verdict_per_site_p95"],
                "per_question_p50": r["question_to_verdict_per_question_p50"],
                "per_question_p95": r["question_to_verdict_per_question_p95"]} for r in rows},
            "created_to_applied": {str(r["target_users"]): {"p50": r["created_to_applied_p50"],
                                                            "p95": r["created_to_applied_p95"]} for r in rows},
            "sustained_events_per_second": th.get("sustained_events_per_second"),
            "stamp_resolution_seconds": STAMP_RESOLUTION_SECONDS}
        doc["discovery"] = {str(s): (doc["sizes"].get(str(s)) or {}).get("discovery") for s in doc["sizes_requested"]}
        doc["largest_size_completed"] = max((r["target_users"] for r in rows), default=None)
        doc["run"].update({"finished_at": utc_clock(), "seconds": time.perf_counter() - t0,
                           "code_dirty_end": code_dirty(list(rp.DIRTY_PATHS))})
        doc["run"].update(su.evidence_block(o.suites))
        not_run = {"c0_real_fabric_keyed": "per size, in each size's own report (sizes.<n>.checks)",
                   "c7_guards": "per size, in each size's own report", "c8_emergence_load": "per size",
                   "c9_benchmark": "per size (c5 asserts every requested size's checks ok)",
                   "c1_signed": "the end-to-end job's drills", "c2_lineage": "the end-to-end job's",
                   "c3_idempotent": "the end-to-end job's", "c4_recovery": "the end-to-end job's",
                   "c6_ci": "the workflow test (S8)"}
        if doc["run"]["suites_evidence"] != su.EVIDENCE:
            not_run["c10_suites_green"] = "no suites directory was given (--suites DIR; M2)"
        names = [n for n in rp.MEASURE_CHECKS if n not in not_run]
        doc["checks"] = rp.run_checks(doc, names)
        doc["verdict"] = rp.verdict(doc["checks"], not_run=not_run, suites_green=rp.suites_green(doc))
        doc["verdict"]["sizes_are_the_briefs"] = doc["sizes_requested"] == list(rp.MEASURE_SIZES)
        doc["phase"] = "finished"
        self.write()


# --------------------------------------------------------------------------------------------------- helpers

def _stop_group(proc: subprocess.Popen[bytes]) -> None:
    """Interrupt a phase's process group (SIGINT lets the harness close its sites and fabric), then kill it."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def db_events(driver: Any, org_id: str) -> dict[str, dict[str, Any]]:
    """Every row of the service's ``events`` table for one organisation, read ``mode=ro`` (M7: the admin view caps
    at 1,000 rows), keyed by event id: kind, status, ``created_at``, ``applied_at``."""
    conn = sqlite3.connect(f"file:{drills.service_db_path(driver)}?mode=ro", uri=True, isolation_level=None)
    try:
        rows = conn.execute("SELECT event_id, kind, status, created_at, applied_at FROM events WHERE org_id = ? "
                            "ORDER BY rid", (org_id,)).fetchall()
    finally:
        conn.close()
    return {eid: {"kind": kind, "status": status, "created_at": created, "applied_at": applied}
            for eid, kind, status, created, applied in rows}


def _sample(rows: Mapping[str, Mapping[str, Any]], event_id: str | None, role: str) -> dict[str, Any] | None:
    from .harness import seconds_between
    row = rows.get(event_id or "")
    if not row or not row.get("applied_at"):
        return None
    return {"event_id": event_id, "kind": row["kind"], "role": role, "created_at": row["created_at"],
            "applied_at": row["applied_at"], "seconds": seconds_between(row["created_at"], row["applied_at"])}


# --------------------------------------------------------------------------------------------------- the CLI

def summary(doc: Mapping[str, Any]) -> str:
    v = doc.get("verdict") or {}
    seconds = float(doc["run"].get("seconds") or 0)
    lines = [f"e2e-ingest measure: verdict pass={v.get('pass')} checks_pass={v.get('checks_pass')} "
             f"reason={v.get('reason')} completed={doc.get('completed')} sizes={doc.get('sizes_requested')} "
             f"largest_completed={doc.get('largest_size_completed')} seconds={seconds:.0f} "
             f"(synthetic, measurement: false)"]
    for p in doc.get("phases") or []:
        lines.append(f"  phase {p['name']}: {'completed' if p['completed'] else 'NOT completed'} "
                     f"{float(p.get('seconds') or 0):.0f}s of {p['budget_seconds']:.0f}s"
                     + (f" ({p['reason']})" if p.get("reason") else ""))
    for r in doc.get("scaling_table") or []:
        n = r["nodes_per_site"]
        lines.append(f"  size {r['target_users']}: users {r['users']} sites {r['sites']} records {r['records']} "
                     f"nodes/site {n['min']}/{n['median']}/{n['max']} build {r['graph_build_seconds']:.1f}s "
                     f"({r['graph_seconds_per_1k_records']:.2f}/1k) pipeline {r['pipeline_seconds']:.1f}s "
                     f"detect {r['detection_seconds']:.1f}s candidates {r['candidates']} rankings {r['rankings']} "
                     f"tesseract p50/p95 {r['tesseract_p50']:.2f}/{r['tesseract_p95']:.2f}s q->v "
                     f"{r['question_to_verdict_per_question_p50']:.2f}/"
                     f"{r['question_to_verdict_per_question_p95']:.2f}s "
                     f"publishes {r['publishes']} created->applied {r['created_to_applied_p50']}/"
                     f"{r['created_to_applied_p95']}s wall {r['wall_seconds']:.0f}s")
    for n, g in sorted(((doc.get("tesseract_nodes") or {}).get("graphs") or {}).items(), key=lambda kv: int(kv[0])):
        lines.append(f"  tesseract {g['nodes']} nodes: build {g['build_seconds']:.1f}s, {g['queries']} queries "
                     f"p50 {g['p50']} p95 {g['p95']}s")
    th = doc.get("throughput") or {}
    for s in th.get("steps") or []:
        lines.append(f"  sweep {s['rate']}/s: {s['events']} events in {s['duration_seconds']:.0f}s "
                     f"(achieved {s['achieved_events_per_second']:.2f}/s, held {s['rate_held']}) apply p50/p95 "
                     f"{s['apply_p50']}/{s['apply_p95']}s n={s['n']} applied_all={s['applied_all']}")
    if th:
        lines.append(f"  sustained events/s: {th.get('sustained_events_per_second')} (stopped: {th.get('stopped')})")
    for name, check in (doc.get("checks") or {}).items():
        state = "ok" if check["ok"] else "FAILED: " + ", ".join(check["failed"])
        lines.append(f"  {name}: {state} ({check['assertions']} assertions)")
    return "\n".join(lines)


def parse_sizes(text: str) -> tuple[int, ...]:
    sizes = tuple(int(part) for part in text.split(",") if part.strip())
    bad = [s for s in sizes if s not in ew.WORLD_TABLE]
    if not sizes or bad:
        raise ValueError(f"--sizes takes a comma-separated subset of {','.join(map(str, sorted(ew.WORLD_TABLE)))}")
    return sizes


def cmd_measure(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"error: {out} exists and is not empty (pick a new --out)", file=sys.stderr)
        return 2
    try:
        sizes = parse_sizes(args.sizes)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    suites = Path(args.suites) if args.suites else None
    if suites is not None and not suites.is_dir():
        print(f"error: --suites {suites} is not a directory", file=sys.stderr)
        return 2
    options = MeasureOptions(out=out, sizes=sizes, seed=args.seed, jobs=args.jobs, run_id=args.run_id,
                             suites=suites)
    run = MeasureRun(options=options)
    doc = run.run()
    sha = sha256_hex(run.path.read_bytes())
    print(summary(doc))
    print(f"report: {run.path} sha256={sha}")
    return 0 if doc["verdict"]["pass"] else 1


def add_measure_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True)
    parser.add_argument("--sizes", default=",".join(map(str, rp.MEASURE_SIZES)),
                        help="the sizes to run, smallest first (the brief: 200,600,2000)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--run-id", default="local")
    parser.add_argument("--suites", default=None,
                        help="the directory the suites CLI wrote (M2's junit evidence); criterion 10 runs over it")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.e2e_ingest measure",
                                     description="The measure job of E2E-INGEST.md section 10 (synthetic).")
    add_measure_arguments(parser)
    return cmd_measure(parser.parse_args(list(argv) if argv is not None else None))


if __name__ == "__main__":
    sys.exit(main())
