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


# --------------------------------------------------------------------------
# correctness review: pooled counts, distinct-source columns, revivals, answers, squads
# --------------------------------------------------------------------------
def _exact_sketch(hier, teams, max_order: int = 3) -> Sketch:
    org = hier.org
    cat = lambda name: np.concatenate([np.concatenate(getattr(hier.nodes[t], name)) for t in teams])  # noqa: E731
    attrs, labels, present, w = cat("obs_attrs"), cat("obs_labels"), cat("obs_present"), cat("obs_worker")
    return Sketch.from_interactions("X", "x", 0, attrs, labels, w, org.worker_team[w], org.worker_department[w],
                                    org.worker_region[w], max_order=max_order, present=present)


@pytest.mark.parametrize("compression", ["order_capped", "claims_only"])
def test_department_pool_matches_union_of_team_stores(compression: str) -> None:
    """Delta promotion never double-counts; every cell all teams promoted has exact n, k and distinct-source
    columns at the department (dd = dr = 1 in a one-department org, not the number of teams)."""
    from mycelic_bench.sketch import COL_DD, COL_DR, COL_DT, COL_DW
    hier, policy = run_hier(compression=compression, lineage=True)
    org = hier.org
    dept = hier.nodes[org.department_ids[0]]
    teams = [t for t in dept.children if t in hier.nodes]
    exact = _exact_sketch(hier, teams)
    cum = dept.cumulative
    ex = exact.lookup(cum.ids)
    ci = cell_index()
    assert (cum.counts[:, COL_N] <= ex[:, COL_N]).all()                       # never more than the teams hold
    thr = max(policy.min_cell_n, policy.k_anonymity)
    full = ci.order_of(cum.ids) == 1
    if compression == "order_capped":
        full = np.ones(len(cum.ids), dtype=bool)
        for t in teams:
            tc = hier.nodes[t].cumulative.lookup(cum.ids)[:, COL_N]
            full &= (tc >= thr) | (tc == 0) | (ci.order_of(cum.ids) == 1)
    assert full.sum() >= 50
    np.testing.assert_array_equal(cum.counts[full][:, :COL_DW], ex[full][:, :COL_DW])   # n and every label count
    np.testing.assert_array_equal(cum.counts[full][:, COL_DW], ex[full][:, COL_DW])
    np.testing.assert_array_equal(cum.counts[full][:, COL_DT], ex[full][:, COL_DT])
    assert (cum.counts[:, COL_DD] == (cum.counts[:, COL_N] > 0)).all()
    assert (cum.counts[:, COL_DR] == (cum.counts[:, COL_N] > 0)).all()
    own = [c for (cell, label, sign, scope), c in dept.claims.items() if scope == dept.unit_id and c.status == "accepted"]
    assert own
    for c in own:
        assert c.support.distinct_departments == 1 and c.support.distinct_regions == 1
        assert c.support.distinct_teams <= len(teams) and c.support.distinct_workers >= c.support.distinct_teams
    # the executive sees three teams, one department, one region
    root = hier.root_node().cumulative
    assert root.counts[:, COL_DT].max() == len(teams) and root.counts[:, COL_DD].max() == 1 and root.counts[:, COL_DR].max() == 1
    # leaf recent window: distinct workers are exact (not once per round a worker contributed in)
    team = hier.nodes[teams[0]]
    r = max(team.recent)
    rp = team._recent_pool(r)
    m = np.concatenate(team.obs_round) >= r - policy.recent_window + 1
    w = np.concatenate(team.obs_worker)[m]
    ex_r = Sketch.from_interactions("X", "x", 0, np.concatenate(team.obs_attrs)[m], np.concatenate(team.obs_labels)[m], w,
                                    org.worker_team[w], org.worker_department[w], org.worker_region[w], max_order=3,
                                    present=np.concatenate(team.obs_present)[m])
    np.testing.assert_array_equal(rp.lookup(rp.ids)[:, COL_DW], ex_r.lookup(rp.ids)[:, COL_DW])
    assert rp.counts[:, COL_DW].max() <= len(np.unique(w))


def test_skipped_layer_distinct_counts_in_two_layer_topology() -> None:
    """With teams reporting straight to the executive, distinct departments / regions per cell are counted from
    the contributing teams' membership, not from the number of teams."""
    from mycelic_bench.sketch import COL_DD, COL_DR, COL_DT
    cfg, w = small_world()
    policy = Policy.from_cfg(cfg)
    hier = Hierarchy(w.org, w.records(), policy, get_profile(cfg, cfg["models"]["edge_profile"]), 0, layers=2)
    hier.run(w.n_rounds)
    root = hier.root_node()
    cum = root.cumulative
    org = w.org
    for i in range(0, len(cum.ids), max(1, len(cum.ids) // 50)):
        cell = int(cum.ids[i])
        teams = [t for t, sk in root.received_from.items() if sk.lookup(np.array([cell]))[0, COL_N] > 0]
        tidx = [org.team_ids.index(t) for t in teams]
        assert cum.counts[i, COL_DT] == len(teams)
        assert cum.counts[i, COL_DD] == len({int(org.team_department[t]) for t in tidx})
        assert cum.counts[i, COL_DR] == len({int(org.department_region[org.team_department[t]]) for t in tidx})


def test_revived_claim_replaces_superseded_copy_at_parent() -> None:
    """A claim revived after supersession carries a new claim_id under the same key; the parent must adopt it."""
    cfg, w = small_world()
    policy = Policy.from_cfg(cfg, lineage=True)
    hier = Hierarchy(w.org, w.records(), policy, get_profile(cfg, cfg["models"]["edge_profile"]), 0, layers=5)
    dept = hier.nodes[w.org.department_ids[0]]
    parent = hier.nodes[dept.parent_id]
    c1 = _own_accepted_claim(dept, 2); c1.sign_with(dept.key)
    key = (c1.cell, c1.label, c1.sign, dept.unit_id)
    dept.claims[key] = c1
    parent.ingest_artifact(dept.promote(2), 2); parent._inherit(2)
    assert parent.claims[key].claim_id == c1.claim_id and parent.claims[key].status == "accepted"
    c1.status = "superseded"; c1.valid_to = 4; c1.round_updated = 4
    parent.ingest_artifact(dept.promote(4), 4); parent._inherit(4)
    assert parent.claims[key].status == "superseded"
    c2 = _own_accepted_claim(dept, 6); c2.revision_of = c1.claim_id; c2.sign_with(dept.key)
    dept.claims[key] = c2
    parent.ingest_artifact(dept.promote(6), 6); parent._inherit(6)
    assert parent.claims[key].claim_id == c2.claim_id and parent.claims[key].status == "accepted"
    # and the grandparent learns it through the parent's own promotion
    grand = hier.nodes[parent.parent_id]
    grand.ingest_artifact(parent.promote(6), 6); grand._inherit(6)
    assert grand.claims[key].claim_id == c2.claim_id and grand.claims[key].status == "accepted"


def test_question_answers_are_absorbed_without_double_counting() -> None:
    """An answer is the child's exact count; pooling it on top of a count the parent already holds from that
    child (a promoted delta, an earlier answer) must add only the difference, and the child's `sent` follows."""
    from mycelic_bench.schemas import QuestionArtifact
    hier, policy = run_hier(compression="order_capped", lineage=True, k_anonymity=0)   # exact counts: no k-anonymity floor on answers
    dept = hier.nodes[hier.org.department_ids[0]]
    teams = [t for t in dept.children if t in hier.nodes]
    exact = _exact_sketch(hier, teams)
    ci = cell_index()
    # an order-2 cell the department already holds partially (some team below min_cell_n)
    cum = dept.cumulative
    ex = exact.lookup(cum.ids)
    partial = np.flatnonzero((ci.order_of(cum.ids) == 2) & (cum.counts[:, COL_N] > 0) & (cum.counts[:, COL_N] < ex[:, COL_N]))
    assert len(partial)
    cell = int(cum.ids[partial[0]])
    true_n = int(ex[partial[0], COL_N])
    r = 9
    for _ in range(2):   # asking twice is idempotent
        dept.ask([(cell, 0, 1.0, "fixed")], r)
        got = dept.cumulative.lookup(np.array([cell]))[0]
        assert int(got[COL_N]) == true_n, (int(got[COL_N]), true_n)
        for t in teams:
            child = hier.nodes[t]
            held = dept.received_from[t].lookup(np.array([cell]))[0, COL_N]
            assert int(child.sent.lookup(np.array([cell]))[0, COL_N]) == int(held) == int(child.cumulative.lookup(np.array([cell]))[0, COL_N])
        r += 1
    # a later promotion by the teams must not re-send the answered cell
    for t in teams:
        art = hier.nodes[t].promote(r)
        assert cell not in set(art.sketch.ids.tolist())


def test_seven_layer_routes_bare_claims_to_one_squad() -> None:
    """Injected bare claims of a team batch reach only the squad of the worker they name (not all four)."""
    from mycelic_bench import attacks as A
    cfg, w = small_world()
    plan = A.apply_attacks(w, cfg, seed=0, fraction=0.10, type_mix={"false_claim": 1.0})
    try:
        hier = Hierarchy(w.org, w.records(), Policy.from_cfg(cfg, lineage=True), get_profile(cfg, cfg["models"]["edge_profile"]), 0,
                         layers=7, attack_hook=plan.hook)
        hier.run(w.n_rounds)
        injected = plan.hook_stats["claims_injected"]
        quarantined = sum(len(n.quarantined) for n in hier.layer_nodes("squad"))
        received = sum(len(ch) for n in hier.layer_nodes("squad") for ch in n.received_claims.values())
        assert injected > 0 and quarantined + received <= injected
        wpt = max(1, w.org.n_workers // w.org.n_teams)
        for n in hier.layer_nodes("squad"):
            t, s = int(n.unit_id[1:5]), int(n.unit_id[-1])
            for c in n.quarantined:
                wk = int(c.producer_id[1:])
                assert int(np.clip((wk - t * wpt) * 4 // wpt, 0, 3)) == s
    finally:
        plan.restore()


def test_fidelity_duplicated_is_a_fraction() -> None:
    from mycelic_bench.evaluate import ClaimRecord, transition_fidelity
    cfg, w = small_world()
    e = next(x for x in w.effects if x.kind == "local" and x.delta > 0)
    sl, su = (e.scope_layer, int(e.scope_unit)) if e.scope_layer in ("team", "department", "region") else ("executive", 0)
    recs = [ClaimRecord(cell=e.cell, label=e.label, sign=e.sign, scope_layer=sl, scope_unit=su, layer="team", round_accepted=1,
                        confidence=0.9, independent_support=3.0, replica_count=10, claim_id=f"c{i}") for i in range(3)]
    fid = transition_fidelity(w, {"team": recs}, 0.08)
    assert fid["table"]["team"]["duplicated"] == 1.0
    fid1 = transition_fidelity(w, {"team": recs[:1]}, 0.08)
    assert fid1["table"]["team"]["duplicated"] == 0.0
