"""Tests for failures.py (MODULE_SPEC Task F): failure injection acts on hierarchy state only,
stops / restores the affected units' artifact flow, and the paired survival metrics are well-formed."""

from __future__ import annotations

import numpy as np
import pytest

from mycelic_bench.config import get_profile, load_config
from mycelic_bench.failures import FailurePlan, chain_hooks, compare_runs, summarize_run
from mycelic_bench.hierarchy import Hierarchy, Policy
from mycelic_bench.runner import run_system
from mycelic_bench.sketch import COL_N
from mycelic_bench.world import generate_world

SMALL = ["org.n_workers=300", "org.n_rounds=9", "world.n_local_findings=20", "world.n_cross_team_findings=5",
         "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2"]
AT, REC = 3, 6


@pytest.fixture(scope="module")
def cfg():
    return load_config(overrides=SMALL)


@pytest.fixture(scope="module")
def world(cfg):
    return generate_world(cfg, 1)


def _run_plan(cfg, world, plan, watch=None):
    """Run a lineage-aware hierarchy with the plan wired in like the runner does; return (hier, per-round trace)."""
    policy = Policy.from_cfg(cfg, lineage=True)
    profile = get_profile(cfg, cfg["models"]["edge_profile"])
    hier = Hierarchy(world.org, world.records(), policy, profile, 1, attack_hook=plan.record_filter)
    trace = {}

    def on_round(h, r):
        uid = watch() if watch is not None else None
        if uid is not None:
            node = h.nodes[uid]
            parent = h.nodes[node.parent_id]
            sk = parent.received_from.get(uid)
            trace[r] = (node.available, int(sk.counts[:, COL_N].sum()) if sk is not None else 0, len(parent.inbox))
        plan.apply(h, r)   # the trunk runner calls apply after each round, exactly like this

    hier.run(world.n_rounds, on_round=on_round)
    return hier, trace


def test_department_failure_stops_its_artifacts_then_recovers(cfg, world):
    plan = FailurePlan("department", 0.5, seed=1, at_round=AT, recover_round=REC)
    hier, trace = _run_plan(cfg, world, plan, watch=lambda: "D000")
    avail = {r: a for r, (a, _, _) in trace.items()}
    n_from = {r: n for r, (_, n, _) in trace.items()}
    assert plan.failed_units == ["D000"]
    # available before, down during [AT, REC), back after
    assert all(avail[r] for r in range(0, AT)) and all(not avail[r] for r in range(AT, REC)) and all(avail[r] for r in range(REC, 9))
    # the parent's received_from for the department grows before the failure ...
    assert n_from[AT - 1] > n_from[0] > 0
    # ... stops growing while the department is down (no artifacts from it) ...
    assert n_from[AT - 1] == n_from[AT] == n_from[REC - 1]
    # ... and grows again after recovery (children resync the wiped node, which re-promotes)
    assert n_from[8] > n_from[REC - 1]
    assert hier.nodes["D000"].cumulative.total_n() > 0
    assert plan.summary()["worker_fraction_affected"] == 1.0   # the small org has a single department


def test_random_team_crash_loses_records_but_parent_keeps_old_artifacts(cfg, world):
    plan = FailurePlan("random_team", 0.30, seed=1, at_round=AT, recover_round=REC)
    hier, trace = _run_plan(cfg, world, plan, watch=lambda: plan.failed_units[0] if plan.failed_units else None)
    assert len(plan.failed_units) == 1   # ceil(0.30 * 3 teams)
    t = int(plan.failed_units[0][1:])
    expected_lost = int((np.isin(world.team, [t]) & (world.round >= AT) & (world.round < REC)).sum())
    assert plan.records_lost == expected_lost > 0
    n_from = {r: n for r, (_, n, _) in trace.items()}
    assert n_from[AT] == n_from[REC - 1] > 0            # frozen during the outage, old artifacts kept
    assert n_from[8] > n_from[REC - 1]                    # new evidence flows again after recovery
    assert hier.totals()["records_in"] == world.n - expected_lost


def test_random_user_filter_drops_exactly_the_failed_workers_records(cfg, world):
    plan = FailurePlan("random_user", 0.2, seed=1, at_round=AT, recover_round=REC, org=world.org)
    assert len(plan.failed_workers) == 60
    hier, _ = _run_plan(cfg, world, plan)
    fw = np.array(sorted(plan.failed_workers))
    expected = int((np.isin(world.worker, fw) & (world.round >= AT) & (world.round < REC)).sum())
    assert plan.records_lost == expected > 0
    assert hier.totals()["records_in"] == world.n - expected
    # nested target sets across rates for the same seed
    bigger = FailurePlan("random_user", 0.5, seed=1, at_round=AT, recover_round=REC, org=world.org)
    assert plan.failed_workers <= bigger.failed_workers


def test_stale_replica_buffers_and_replays(cfg, world):
    plan = FailurePlan("stale_replica", 0.34, seed=1, at_round=AT, recover_round=REC)
    hier, trace = _run_plan(cfg, world, plan, watch=lambda: plan.failed_units[0] if plan.failed_units else None)
    assert plan.records_buffered == plan.records_replayed > 0 and plan.records_lost == 0
    assert hier.totals()["records_in"] == world.n
    n_from = {r: n for r, (_, n, _) in trace.items()}
    assert n_from[AT] == n_from[REC - 1] and n_from[8] > n_from[REC - 1]


def test_partition_queues_then_delivers(cfg, world):
    plan = FailurePlan("partition", 0.34, seed=1, at_round=AT, recover_round=REC)
    hier, trace = _run_plan(cfg, world, plan, watch=lambda: plan.failed_units[0] if plan.failed_units else None)
    uid = plan.failed_units[0]
    avail = {r: a for r, (a, _, _) in trace.items()}
    n_from = {r: n for r, (_, n, _) in trace.items()}
    inbox = {r: q for r, (_, _, q) in trace.items()}
    assert all(avail.values())                                # partition never marks nodes unavailable
    assert n_from[AT] == n_from[REC - 1] and inbox[REC - 1] > 0  # artifacts queued in the parent's inbox
    assert inbox[REC] == 0 and n_from[REC] > n_from[REC - 1]     # delivered at recovery
    assert uid not in hier.delay


def test_knowledge_survival_metrics_are_well_formed(cfg, world):
    base = run_system(world, cfg, 1, "B7_mycelic")
    base_view = summarize_run(base)
    same = compare_runs(world, base_view, base_view, None)
    assert same["knowledge_survival"] in (None, 1.0) and same["failure_kind"] == "none"
    plan = FailurePlan("random_team", 0.30, seed=1, at_round=AT, recover_round=REC)
    fail = run_system(world, cfg, 1, "B7_mycelic", attack_hook=chain_hooks(None, plan.record_filter), failure_plan=plan)
    m = compare_runs(world, base_view, summarize_run(fail), plan)
    assert m["n_base_discoveries"] >= 0
    for key in ("knowledge_survival", "useful_discovery_survival", "lineage_survival", "conflict_survival",
                "contradiction_detection_survival", "repair_quality", "false_reconstruction_rate", "survival_pre_recovery"):
        v = m[key]
        assert v is None or 0.0 <= v <= 1.0, (key, v)
    if m["n_base_discoveries"] > 0:
        assert m["knowledge_survival"] is not None
    assert len(m["survival_curve"]) == world.n_rounds
    assert m["recovery_latency"] is None or 0 <= m["recovery_latency"] <= world.n_rounds - REC
    assert m["failure_kind"] == "random_team" and m["failure_rate"] == plan.rate and m["records_lost"] == plan.records_lost


def test_plan_validation():
    with pytest.raises(ValueError):
        FailurePlan("nope", 0.1, 0, 3)
    with pytest.raises(ValueError):
        FailurePlan("random_team", 0.1, 0, 3, recover_round=3)
    with pytest.raises(ValueError):
        FailurePlan("random_team", 1.5, 0, 3)
