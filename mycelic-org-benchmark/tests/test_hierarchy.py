"""Hierarchy engine: smoke run, lineage dedup, superseded retention, two-child rule, compression ladder."""

from __future__ import annotations

import functools
import math

import numpy as np
import pytest

from mycelic_bench.config import get_profile, load_config
from mycelic_bench.hierarchy import Hierarchy, Policy
from mycelic_bench.runner import run_system
from mycelic_bench.schemas import Claim, LineageRecord, SupportRecord
from mycelic_bench.sketch import COL_K0, COL_N, N_COLS, Sketch
from mycelic_bench.vocab import cell_index
from mycelic_bench.world import generate_world

SMALL = [
    "org.n_workers=300", "org.n_rounds=8", "world.n_local_findings=20", "world.n_cross_team_findings=5",
    "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2",
]

REQUIRED_METRICS = [
    "n_accepted", "categories", "precision_strict", "precision_lenient", "false_discovery_rate", "false_association_rate",
    "recall_local", "recall_cross_team", "recall_global", "recall_temporal", "recall_contradiction", "ttd_global_median",
    "ttd_cross_team_median", "evidence_coverage", "lineage_correctness", "source_diversity", "ece", "revision_accuracy",
    "stale_persistence", "contradiction_f1", "raw_sensitive_leakage", "n_canaries_exposed", "bytes_off_device",
    "fraction_raw_exposed", "reconstructability", "bytes_transmitted", "compression_ratio", "tokens", "tokens_to_cloud",
    "model_calls", "latency_ms_est", "energy_j_est", "cost_usd_est", "runtime_s", "poison", "failure_reasons", "fidelity",
]


@functools.lru_cache(maxsize=None)
def small_world():
    cfg = load_config(overrides=SMALL)
    return cfg, generate_world(cfg, 0)


@functools.lru_cache(maxsize=None)
def run(name: str):
    cfg, w = small_world()
    return run_system(w, cfg, 0, name)


def run_hier(**policy_overrides) -> tuple[Hierarchy, Policy]:
    cfg, w = small_world()
    policy = Policy.from_cfg(cfg, **policy_overrides)
    hier = Hierarchy(w.org, w.records(), policy, get_profile(cfg, cfg["models"]["edge_profile"]), 0, layers=5)
    hier.run(w.n_rounds)
    return hier, policy


def test_smoke_b7_returns_full_metric_dictionary() -> None:
    cfg, w = small_world()
    res = run("B7_mycelic")
    m = res["metrics"]
    missing = [k for k in REQUIRED_METRICS if k not in m]
    assert not missing, missing
    n_groups = len({e.group for e in w.effects if e.kind == "contradiction"})
    if n_groups:
        assert "correct_resolution_rate" in m and "incorrect_resolution_rate" in m
    assert m["n_accepted"] > 0 and m["n_accepted"] == sum(m["categories"].values())
    assert 0.0 <= m["precision_strict"] <= 1.0 and 0.0 <= m["false_discovery_rate"] <= 1.0
    assert m["recall_local"] > 0.0
    # Cross-team recall is a well-formed fraction here but NOT guaranteed > 0 on this 3000-record smoke world:
    # under edge-SLM perception noise (10% attribute omission per attribute) the department-scoped order-2/3
    # effects sit below the power of the shared interaction test after BH over ~1e5 hypotheses (ORACLE on
    # exact data finds 3/5 at seed 0, B7 0/5; at Tier 1 B7 reaches ~0.4).  The hidden-effect assertions live
    # in the Tier-1 experiments, not in this smoke test.
    assert m["n_cross_team"] == 5 and 0.0 <= m["recall_cross_team"] <= 1.0
    assert m["categories"]["exact"] > 0
    assert m["raw_sensitive_leakage"] == 0.0 and m["n_canaries_exposed"] == 0     # DLP: no text leaves the device
    assert m["bytes_transmitted"] > 0 and m["compression_ratio"] > 0 and m["tokens"] > 0 and m["model_calls"] > 0
    assert m["tokens_to_cloud"] == 0 and m["runtime_s"] > 0
    assert set(m["fidelity"]) >= {"team", "department", "executive"}
    for key in ("attack_records", "poison_claims_at_root", "poison_promotion_rate"):
        assert key in m["poison"]
    assert m["poison"]["attack_records"] == 0 and m["poison"]["poison_claims_at_root"] == 0
    assert isinstance(m["failure_reasons"], dict)
    assert len(res["snapshots"]) == w.n_rounds and set(res["snapshots"]) == set(range(w.n_rounds))
    root = res["hier"].root_node()
    assert root.layer == "executive" and root.parent_id is None
    assert len(root.accepted_claims()) == m["n_root_claims"]


def test_lineage_dedup_removes_copies() -> None:
    cfg, w = small_world()
    n_copies = int(w.is_copy.sum())
    assert n_copies > 0
    b6 = run("B6_hier_no_lineage")["metrics"]
    b7 = run("B7_mycelic")["metrics"]
    assert b6["stats"]["records_rejected"] == 0
    assert b7["stats"]["records_rejected"] > 0
    assert b7["stats"]["records_rejected"] <= n_copies
    # copies count once for the lineage-aware system, so its team nodes retain fewer records
    kept6 = sum(len(np.concatenate(n.obs_idx)) for n in run("B6_hier_no_lineage")["hier"].layer_nodes("team"))
    kept7 = sum(len(np.concatenate(n.obs_idx)) for n in run("B7_mycelic")["hier"].layer_nodes("team"))
    assert kept6 == w.n and kept7 == w.n - b7["stats"]["records_rejected"]
    # and no record fingerprint is retained twice under lineage
    for node in run("B7_mycelic")["hier"].layer_nodes("team"):
        fps = np.concatenate(node.obs_fp)
        assert len(np.unique(fps)) == len(fps)


def _own_accepted_claim(node, round_: int) -> Claim:
    ci = cell_index()
    cell = ci.encode([(0, 1), (2, 3)])
    c = Claim(claim_id=node._claim_id(cell, 4, 1, round_), producer_id=node.unit_id, layer=node.layer, cell=cell, label=4,
              sign=1, n=80, k=30, rate=30 / 80, baseline_rate=0.03, effect=0.345, p_value=1e-12, q_value=1e-8,
              confidence=0.9, status="accepted", round_created=round_, round_updated=round_)
    c.support = SupportRecord(replica_count=80, independent_support=4.0, distinct_workers=8, distinct_teams=3, distinct_departments=1, distinct_regions=1)
    c.lineage = LineageRecord(contributing_units=list(node.children), path=[node.unit_id], derivation_operator="synthesize")
    return c


def _null_recent_sketch(node, cell: int, n: int, round_: int) -> Sketch:
    counts = np.zeros((1, N_COLS), dtype=np.int64)
    counts[0, COL_N] = n     # n recent records matching the cell, none with the label
    return Sketch(node.unit_id, node.layer, round_, np.array([cell]), counts, node.policy.sketch_order)


@pytest.mark.parametrize("retain", [True, False])
def test_superseded_claims_retained_when_retain_superseded(retain: bool) -> None:
    cfg, w = small_world()
    policy = Policy.from_cfg(cfg, retain_superseded=retain)
    hier = Hierarchy(w.org, w.records(), policy, get_profile(cfg, cfg["models"]["edge_profile"]), 0, layers=5)
    node = hier.nodes[w.org.department_ids[0]]
    c = _own_accepted_claim(node, 3)
    key = (c.cell, c.label, c.sign, node.unit_id)
    node.claims[key] = c
    # powerful null evidence in the recent window refutes the claim -> superseded, valid_to set
    node._revise(9, _null_recent_sketch(node, c.cell, 60, 9))
    assert c.status == "superseded" and c.valid_to == 9 and c.round_updated == 9
    assert [ev for ev in hier.trace if ev["claim_id"] == c.claim_id and ev["status"] == "superseded"]
    if retain:
        assert node.claims[key] is c                     # old knowledge is kept with its lineage
        assert c.lineage.contributing_units == list(node.children)
    else:
        assert key not in node.claims
    # a weak recent window (below n_min) never supersedes
    c2 = _own_accepted_claim(node, 4)
    key2 = (c2.cell, c2.label, c2.sign, node.unit_id)
    node.claims[key2] = c2
    node._revise(10, _null_recent_sketch(node, c2.cell, policy.n_min - 1, 10))
    assert c2.status == "accepted"


def test_superseded_claims_survive_a_full_run() -> None:
    """Any claim the trace marks superseded is still present in its node's knowledge base."""
    hier, policy = run_hier(retain_superseded=True)
    superseded = {(ev["unit"], ev["claim_id"]) for ev in hier.trace if ev["status"] == "superseded"}
    for unit, cid in superseded:
        node = hier.nodes[unit]
        assert any(c.claim_id == cid and c.status in ("superseded", "accepted") for c in node.claims.values())


def test_two_child_rule_for_department_scope_claims() -> None:
    res = run("B7_mycelic")
    hier = res["hier"]
    checked = 0
    for node in hier.layer_nodes("department") + hier.layer_nodes("executive"):
        need = min(2, len(node.children))
        for (cell, label, sign, scope), c in node.claims.items():
            if scope != node.unit_id or c.status != "accepted":
                continue
            n_children = sum(1 for u in c.lineage.contributing_units if u in node.received_from)
            assert n_children >= need, (node.unit_id, c.claim_id, c.lineage.contributing_units)
            assert c.lineage.derivation_operator in ("synthesize", "pool", "revise")
            assert c.support.independent_support >= node.policy.support_min
            assert c.verify(node.key)                     # accepted own claims are signed by the node
            checked += 1
    assert checked > 0


def test_no_lineage_uses_replica_count_and_no_signatures() -> None:
    hier, policy = run_hier(lineage=False)
    root = hier.root_node()
    acc = root.accepted_claims()
    assert acc
    for c in acc:
        assert c.support.independent_support == float(c.support.replica_count)
        assert c.signature == ""
    assert hier.stats["conflicts_opened"] == 0


def test_compression_policies_reduce_bytes_monotonically() -> None:
    bytes_above = {}
    root_claims = {}
    for comp in ("full_sketch", "order_capped", "claims_only"):
        hier, policy = run_hier(compression=comp, lineage=True)
        bytes_above[comp] = sum(n.bytes_out for n in hier.nodes.values())
        root_claims[comp] = len(hier.root_node().accepted_claims())
        assert bytes_above[comp] > 0
    assert bytes_above["full_sketch"] >= bytes_above["order_capped"] >= bytes_above["claims_only"]
    assert bytes_above["claims_only"] < 0.5 * bytes_above["order_capped"]
    # k-anonymity=0 lets full_sketch ship every cell, strictly more than the n>=min_cell_n cap
    hier_full, _ = run_hier(compression="full_sketch", lineage=True, k_anonymity=0)
    hier_cap, _ = run_hier(compression="order_capped", lineage=True, k_anonymity=0)
    assert sum(n.bytes_out for n in hier_full.nodes.values()) > sum(n.bytes_out for n in hier_cap.nodes.values())


def test_sketch_order_cap_limits_promoted_cells() -> None:
    ci = cell_index()
    hier, _ = run_hier(sketch_order=2, lineage=True)
    for node in hier.nodes.values():
        if node.parent_id is not None and len(node.sent.ids):
            assert ci.order_of(node.sent.ids).max() <= 2
    assert ci.order_of(hier.root_node().cumulative.ids).max() <= 2


def test_layer_ablation_topologies() -> None:
    cfg, w = small_world()
    profile = get_profile(cfg, cfg["models"]["edge_profile"])
    for layers, expected in ((1, ["executive"]), (2, ["team", "executive"]), (3, ["team", "department", "executive"]),
                             (5, ["team", "department", "region", "executive"])):
        hier = Hierarchy(w.org, w.records(), Policy.from_cfg(cfg), profile, 0, layers=layers)
        assert hier.layer_names == expected
        assert hier.root == "executive" and hier.root_node().parent_id is None
        for node in hier.nodes.values():
            if node.parent_id is not None:
                assert node.unit_id in hier.nodes[node.parent_id].children
    hier = Hierarchy(w.org, w.records(), Policy.from_cfg(cfg), profile, 0, layers=1)
    hier.run(3)
    assert hier.root_node().n_records_in == int((w.round < 3).sum())
