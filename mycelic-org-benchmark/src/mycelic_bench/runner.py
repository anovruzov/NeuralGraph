"""Run one system on one world and return a metrics dictionary.

`run_system(world, system_name, cfg, seed, overrides)` dispatches to the
hierarchy engine or a baseline, snapshots the executive knowledge base per
round, and evaluates.  Everything the report needs is in the returned dict;
`manifest.py` writes it with provenance.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .config import get_profile
from .evaluate import (ClaimRecord, contradiction_metrics, evidence_metrics, match_effects, privacy_metrics,
                       raw_record_bytes, temporal_metrics, transition_fidelity, unit_index)
from .hierarchy import Hierarchy, Policy
from .world import World

HIERARCHY_SYSTEMS = {"B5_flat_agents", "B6_hier_no_lineage", "B7_mycelic", "B8_mycelic_security", "B9_mycelic_questioning"}


def run_system(world: World, cfg: dict[str, Any], seed: int, name: str, sys_cfg: dict[str, Any] | None = None,
               **kwargs: Any) -> dict[str, Any]:
    """Dispatch to the hierarchy engine (kind: hierarchy) or a baseline (kind: baseline | oracle)."""
    sys_cfg = dict(sys_cfg if sys_cfg is not None else cfg["systems"][name])
    kind = sys_cfg.get("kind", "hierarchy")
    if kind == "hierarchy":
        return run_hierarchy(world, cfg, seed, name, sys_cfg, **kwargs)
    from .baselines import run_baseline
    return run_baseline(world, cfg, seed, name, sys_cfg, **kwargs)


def claim_records_from_node(world: World, node, include_superseded: bool = True) -> list[ClaimRecord]:
    out = []
    for (cell, label, sign, scope), c in node.claims.items():
        if c.status not in ("accepted", "superseded", "contested"):
            continue
        if c.status == "superseded" and not include_superseded:
            continue
        sl, su = unit_index(world, scope)
        out.append(ClaimRecord(cell=cell, label=label, sign=sign, scope_layer=sl, scope_unit=su, layer=node.layer,
                               round_accepted=c.round_created, confidence=c.confidence,
                               independent_support=c.support.independent_support, replica_count=c.support.replica_count,
                               contributing_units=list(c.lineage.contributing_units), origin_attack=c.origin_attack,
                               status=c.status, conditional=bool(c.conditional_on), claim_id=c.claim_id, valid_to=c.valid_to,
                               disputed=c.disputed))
    return out


def _first_accept_rounds(hier: Hierarchy) -> dict[str, int]:
    """First round each (cell,label,sign,scope) was accepted at the root."""
    first: dict[str, int] = {}
    for ev in hier.trace:
        if ev["status"] == "accepted" and ev["unit"] == hier.root_node().unit_id:
            first.setdefault(ev["claim_id"], ev["round"])
    return first


def run_hierarchy(world: World, cfg: dict[str, Any], seed: int, name: str, sys_cfg: dict[str, Any],
                  policy_overrides: dict[str, Any] | None = None, security=None, question_policy=None, attack_hook=None,
                  failure_plan=None, snapshot_every: int = 1) -> dict[str, Any]:
    overrides = {k: v for k, v in sys_cfg.items() if k not in ("kind", "layers", "local_slm_classifier")}
    if policy_overrides:
        overrides.update(policy_overrides)
    policy = Policy.from_cfg(cfg, **overrides)
    layers = int(sys_cfg.get("layers", cfg["org"].get("layers", 5)))
    profile = get_profile(cfg, cfg["models"]["edge_profile"])
    if security is None and (policy.detector != "none" or sys_cfg.get("local_slm_classifier")):
        from .security import build_security
        security = build_security(policy.detector, cfg, profile, seed, local_slm=bool(sys_cfg.get("local_slm_classifier")))
    if question_policy is None and policy.questioning != "none":
        from .questioning import build_question_policy
        question_policy = build_question_policy(policy.questioning, policy, seed)
    hier = Hierarchy(world.org, world.records(), policy, profile, seed, layers=layers, security=security,
                     question_policy=question_policy, attack_hook=attack_hook, name=name)
    snapshots: dict[int, list[ClaimRecord]] = {}
    t0 = time.time()

    def on_round(h: Hierarchy, r: int) -> None:
        if failure_plan is not None:
            failure_plan.apply(h, r)
        if r % snapshot_every == 0 or r == world.n_rounds - 1:
            snapshots[r] = claim_records_from_node(world, h.root_node())

    hier.run(world.n_rounds, on_round=on_round)
    runtime = time.time() - t0
    root = hier.root_node()
    kb = claim_records_from_node(world, root)
    # use first-acceptance rounds at the root for time-to-discovery
    first = _first_accept_rounds(hier)
    for c in kb:
        if c.claim_id in first:
            c.round_accepted = first[c.claim_id]
    m = match_effects(world, kb, policy.effect_min)
    metrics: dict[str, Any] = {k: v for k, v in m.items() if k not in ("classified", "discovered")}
    metrics.update(evidence_metrics(world, m["discovered"], hier))
    # per-layer knowledge bases for the fidelity table
    kb_by_layer: dict[str, list[ClaimRecord]] = {}
    for layer in hier.layer_names:
        recs: list[ClaimRecord] = []
        for node in hier.layer_nodes(layer):
            recs.extend(claim_records_from_node(world, node, include_superseded=False))
        kb_by_layer[layer if layer in ("team", "department", "region", "executive") else layer] = recs
    fid = transition_fidelity(world, {k: v for k, v in kb_by_layer.items() if k in ("team", "department", "region", "executive")}, policy.effect_min)
    metrics["fidelity"] = fid["table"]
    metrics["per_layer"] = fid["per_layer"]
    metrics.update(temporal_metrics(world, snapshots))
    conflicts = []
    for node in hier.nodes.values():
        for conf in node.conflicts.values():
            conflicts.append({"unit": node.unit_id, "layer": node.layer, "cell": conf.cell, "label": conf.label, "status": conf.status,
                              "winner": conf.winner, "round_opened": conf.round_opened, "reason": conf.reason})
    metrics.update(contradiction_metrics(world, conflicts, kb))
    # privacy & bandwidth
    tot = hier.totals()
    canaries = [q for node in hier.nodes.values() for q in node.quotes_received]
    import re
    exposed = []
    for q in canaries:
        exposed.extend(re.findall(r"CANARY-[a-z_]+-[0-9a-f]{8}", q))
    raw_bytes = raw_record_bytes(world)
    # reconstructability: promoted higher-order cells with n < k
    small = total_cells = 0
    from .vocab import cell_index
    ci = cell_index()
    for node in hier.nodes.values():
        if node.parent_id is None or len(node.sent.ids) == 0:
            continue
        orders = ci.order_of(node.sent.ids)
        hi = orders >= 2
        total_cells += int(hi.sum())
        small += int((node.sent.counts[hi, 0] < max(policy.k_anonymity, 1)).sum())
    bytes_up = sum(n.bytes_out for n in hier.nodes.values()) + tot["bytes_worker_to_team"]
    metrics.update(privacy_metrics(world, exposed, int(tot["bytes_worker_to_team"]), raw_bytes, small, total_cells))
    metrics["bytes_transmitted"] = int(bytes_up)
    metrics["bytes_worker_to_team"] = int(tot["bytes_worker_to_team"])
    metrics["bytes_above_team"] = int(sum(n.bytes_out for n in hier.nodes.values()))
    metrics["compression_ratio"] = raw_bytes / max(metrics["bytes_above_team"], 1)
    metrics["compression_ratio_total"] = raw_bytes / max(bytes_up, 1)
    metrics["tokens"] = int(tot["tokens"]); metrics["model_calls"] = int(tot["model_calls"])
    metrics["tokens_to_cloud"] = 0
    metrics["latency_ms_est"] = float(tot["tokens"] / 1000 * profile.latency_ms_per_1k_tokens / max(len(hier.nodes), 1))
    metrics["energy_j_est"] = float(tot["tokens"] / 1000 * profile.energy_j_per_1k_tokens)
    metrics["cost_usd_est"] = float(tot["tokens"] / 1000 * profile.usd_per_1k_tokens)
    metrics["runtime_s"] = runtime
    metrics["stats"] = {k: (int(v) if isinstance(v, (int, np.integer)) else v) for k, v in hier.stats.items()}
    metrics["per_layer_bytes"] = tot["per_layer"]
    # poisoning accounting (only meaningful when attacks are active)
    metrics["poison"] = poison_accounting(world, hier, m["classified"])
    metrics["n_root_claims"] = len(root.accepted_claims())
    metrics["failure_reasons"] = failure_reasons(world, m, hier, policy)
    return {"metrics": metrics, "hier": hier, "classified": m["classified"], "discovered": m["discovered"], "snapshots": snapshots}


def poison_accounting(world: World, hier: Hierarchy, classified: list[dict[str, Any]]) -> dict[str, Any]:
    attack_records = int((world.attack_tag > 0).sum())
    kept = sum(b["attack_kept"] for b in hier.batch_trace)
    benign_dropped = sum(b["benign_dropped"] for b in hier.batch_trace)
    quarantined = len(hier.quarantine_trace)
    q_attack = sum(1 for q in hier.quarantine_trace if q["attack"])
    root_poison = sum(1 for c in classified if c["category"] == "poison")
    root_total = len(classified)
    # poison survival by layer: accepted claims with origin_attack per layer
    by_layer: dict[str, int] = {}
    for node in hier.nodes.values():
        n_p = sum(1 for c in node.accepted_claims() if c.origin_attack)
        by_layer[node.layer] = by_layer.get(node.layer, 0) + n_p
    # contaminated discoveries: exact claims whose lineage includes attacker units? (approximation: cells with attack records)
    return {"attack_records": attack_records, "attack_records_kept_at_team": kept, "benign_records_dropped": benign_dropped,
            "claims_quarantined": quarantined, "attack_claims_quarantined": q_attack,
            "poison_claims_at_root": root_poison, "poison_promotion_rate": root_poison / max(root_total, 1),
            "poison_by_layer": by_layer}


def failure_reasons(world: World, m: dict[str, Any], hier: Hierarchy, policy: Policy) -> dict[str, int]:
    """Classify why each hidden (cross-team/global) effect was NOT discovered at the root.

    Categories follow the task's list: retrieval failure (n/a for hierarchy), aggregation loss (evidence never
    pooled where it was needed), compression loss (cell order or cell suppression removed it), routing failure
    (n/a), insufficient independent support, contradiction mishandling, privacy/security suppression, poison
    contamination, temporal staleness, model reasoning failure (perception noise erased the signal),
    hierarchy isolation (found at a lower layer but never forwarded), statistical power.
    """
    from .sketch import COL_K0, COL_N
    from .vocab import cell_index
    ci = cell_index()
    reasons: dict[str, int] = {}
    root = hier.root_node()
    disc = m["discovered"]
    for e in world.effects:
        if e.kind not in ("cross_team", "global") or (e.effect_id in disc and disc[e.effect_id]["status"] == "accepted"):
            continue
        order = int(ci.order_of(np.array([e.cell]))[0])
        cnt = root.cumulative.lookup(np.array([e.cell]))[0]
        n_root = int(cnt[COL_N]); k_root = int(cnt[COL_K0 + e.label])
        all_claims = [(node, c) for node in hier.nodes.values() for (cell, label, sign, scope), c in node.claims.items()
                      if cell == e.cell and label == e.label]
        statuses = {c.status for _, c in all_claims}
        found_below = any(c.status == "accepted" and node.unit_id != root.unit_id for node, c in all_claims)
        if e.effect_id in disc and disc[e.effect_id]["status"] == "contested":
            r = "contradiction_mishandling"
        elif "quarantined" in {c.status for node in hier.nodes.values() for c in node.quarantined if c.cell == e.cell and c.label == e.label}:
            r = "privacy_or_security_suppression"
        elif "rejected" in statuses:
            r = "contradiction_mishandling"
        elif found_below:
            r = "hierarchy_isolation"
        elif "proposed" in statuses:
            r = "insufficient_independent_support"
        elif order > policy.sketch_order and n_root == 0:
            r = "compression_loss"
        elif n_root < 0.6 * e.unit_counts.get("executive", 0):
            r = "compression_loss" if n_root > 0 else "aggregation_loss"
        elif n_root < e.n_min:
            r = "statistical_power"
        elif k_root / max(n_root, 1) < e.delta * 0.35:
            r = "model_reasoning_failure"
        elif e.valid_to is not None:
            r = "temporal_staleness"
        else:
            r = "statistical_power"
        reasons[r] = reasons.get(r, 0) + 1
    return reasons
