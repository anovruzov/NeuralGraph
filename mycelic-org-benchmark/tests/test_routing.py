"""Routing (MODULE_SPEC.md Task E): workload generation from a finished hierarchy, every router returns a
valid ordered contact list of leaf units, the oracle reaches every holder, and the aggregate metrics are
well-formed."""

from __future__ import annotations

import functools
import math

import numpy as np
import pytest

from mycelic_bench import routing as R
from mycelic_bench.config import load_config
from mycelic_bench.runner import run_system
from mycelic_bench.world import generate_world

SMALL = [
    "org.n_workers=300", "org.n_rounds=8", "world.n_local_findings=20", "world.n_cross_team_findings=5",
    "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2",
]


@functools.lru_cache(maxsize=None)
def small_world():
    cfg = load_config(overrides=SMALL)
    return cfg, generate_world(cfg, 0)


@functools.lru_cache(maxsize=None)
def finished_hierarchy():
    cfg, w = small_world()
    return run_system(w, cfg, 0, "B7_mycelic")["hier"]


@functools.lru_cache(maxsize=None)
def workload():
    cfg, w = small_world()
    return R.generate_workload(w, finished_hierarchy(), cfg, seed=0, n_random_queries=12, max_effect_queries=12)


def test_workload_generation() -> None:
    cfg, w = small_world()
    hier = finished_hierarchy()
    wl = workload()
    leaf_ids = set(wl.leaf_ids)
    assert leaf_ids == {n.unit_id for n in hier.layer_nodes("team")} and wl.n_leaves == w.org.n_teams
    assert wl.leaf_idx.shape == (w.n,) and set(np.unique(wl.leaf_idx)) <= set(range(w.n_teams if hasattr(w, "n_teams") else w.org.n_teams))
    assert wl.queries and wl.summary["n_queries"] == len(wl.queries)
    kinds = {q.kind for q in wl.queries}
    assert "random" in kinds and kinds & {"local", "cross_team", "global", "temporal", "contradiction", "decoy"}
    assert sum(1 for q in wl.queries if q.source == "random") == 12
    assert sum(1 for q in wl.queries if q.source == "effect") <= 12
    seen = set()
    for q in wl.queries:
        assert q.asker in leaf_ids and q.holders and set(q.holders) <= leaf_ids
        assert all(n >= 1 for n in q.holders.values()) and q.n_total == sum(q.holders.values())
        assert q.order in (1, 2, 3, 4) and 0 <= q.label < 18
        assert (q.cell, q.label) not in seen
        seen.add((q.cell, q.label))
        v = q.view()
        assert not hasattr(v, "holders") and (v.qid, v.cell, v.label, v.asker) == (q.qid, q.cell, q.label, q.asker)
        # holders are computed from non-copied records only (copies are not evidence)
        from mycelic_bench.world import cell_match_mask
        m = cell_match_mask(w.attrs, q.cell) & ~w.is_copy
        assert q.n_total == int(m.sum())
    assert wl.avg_record_bytes > 0
    # deterministic for a seed
    wl2 = R.generate_workload(w, hier, cfg, seed=0, n_random_queries=12, max_effect_queries=12)
    assert [(q.cell, q.label, q.asker) for q in wl2.queries] == [(q.cell, q.label, q.asker) for q in wl.queries]


@pytest.mark.parametrize("name", list(R.DEFAULT_ROUTERS))
def test_router_returns_valid_unit_ids(name: str) -> None:
    cfg, w = small_world()
    hier = finished_hierarchy()
    wl = workload()
    ctx = R.RoutingContext(hier, seed=0)
    leaf_ids = set(wl.leaf_ids)
    for q in wl.queries:
        route = R.run_router(name, ctx, q)
        assert route.router == name
        assert isinstance(route.contacts, list)
        assert set(route.contacts) <= leaf_ids, (name, route.contacts)
        assert q.asker not in route.contacts
        assert len(set(route.contacts)) == len(route.contacts)          # ordered, no repeats
        assert route.messages_question >= 0 and route.bytes_question >= 0 and route.escalation_levels >= 0
        assert set(route.hops) <= set(route.contacts) and all(h >= 1 for h in route.hops.values())
        if name == "local_only":
            assert route.contacts == [] and route.messages_question == 0
        if name == "global_only":
            assert set(route.contacts) == leaf_ids - {q.asker}
        m = R.measure_route(ctx, wl, q, route, budget=5)
        for k in ("recall_at_budget", "recall_full", "precision", "unnecessary_escalation_rate", "correct_destination"):
            v = m[k]
            assert (isinstance(v, float) and math.isnan(v)) or 0.0 <= v <= 1.0, (name, k, v)
        assert m["bytes"] == m["bytes_question"] + m["bytes_answer"] >= 0 and m["messages"] >= m["n_contacted"]
        assert m["latency_hops"] >= 0 and m["n_holders"] == len(q.holders)


def test_oracle_reaches_every_holder() -> None:
    hier = finished_hierarchy()
    wl = workload()
    ctx = R.RoutingContext(hier, seed=0)
    for q in wl.queries:
        route = R.run_router("oracle", ctx, q)
        assert set(route.contacts) == set(q.holders) - {q.asker}
        m = R.measure_route(ctx, wl, q, route, budget=len(q.holders))
        assert m["recall_full"] == 1.0 and m["recall_at_R"] == 1.0 and m["precision"] in (1.0,) or not route.contacts
        assert m["unnecessary_escalation_rate"] == 0.0
    metrics, detail = R.evaluate_routers(hier, wl, ("oracle", "global_only", "local_only"), cfg=small_world()[0], seed=0)
    assert metrics["oracle"]["recall_full"] == 1.0 and metrics["global_only"]["recall_full"] == 1.0
    assert metrics["oracle"]["unnecessary_escalation_rate"] == 0.0
    assert metrics["local_only"]["recall_full"] <= metrics["oracle"]["recall_full"]
    assert metrics["global_only"]["precision"] <= metrics["oracle"]["precision"] + 1e-9
    assert len(detail) == 3 * len(wl.queries) and all("contacts" in r and "router" in r for r in detail)
    assert metrics["_workload"]["n_queries"] == len(wl.queries)
    ob = R.overbroadening_summary(metrics)
    assert "precision_drop_global_vs_oracle" in ob and ob["precision_drop_global_vs_oracle"] >= 0.0
    full, _ = R.evaluate_routers(hier, wl, ("global_only", "hierarchy_aware", "oracle"), cfg=small_world()[0], seed=0)
    ob = R.overbroadening_summary(full)
    assert {"precision_global", "precision_hierarchy_aware", "bytes_ratio_global_vs_hierarchy"} <= set(ob)


def test_routers_do_not_read_truth() -> None:
    """Routers other than the oracle receive a `QueryView` (no holders); the oracle is flagged."""
    assert all(not uses for name, (_, uses) in R.ROUTERS.items() if name != "oracle")
    assert R.ROUTERS["oracle"][1] is True
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "mycelic_bench" / "routing.py").read_text()
    tree = ast.parse(src)
    # `world` / `.effects` / `.attrs` appear only in the truth-side functions
    truth_fns = {"leaf_units_of_records", "generate_workload", "_holders", "measure_route", "independent_support_true"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("route_") and node.name != "route_oracle":
            names = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            assert not (names & {"effects", "holders", "attrs", "p_true", "true_labels"}), (node.name, names)
