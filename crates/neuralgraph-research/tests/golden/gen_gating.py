#!/usr/bin/env python3
"""Fixtures for ``retrieval/gating.py``: the scalar activation functions, every edge-type gate of every registry
(balanced, strict, permissive, profiled, profile switches) on a grid of edges and contexts, the standalone
inhibitory gate, composite and adaptive gates, and the profile tables. Writes ``gating.json``."""
from __future__ import annotations

import itertools
from datetime import timedelta

import _engine as E
import _reference as R

E.install()
from NeuralGraph.research.retrieval import gating as G  # noqa: E402
from NeuralGraph.research.retrieval.data_types import EdgeType, NeuralEdge  # noqa: E402

CONTEXTS = [
    {},
    {"similarity_threshold": 0.2},
    {"entity_recency": 0.3},
    {"consolidation_factor": 0.8, "direction": "up"},
    {"direction": "down"},
    {"min_activations": 1},
    {"inhibition_strength": 0.5},
]


def edges():
    out = []
    grid = itertools.product(list(EdgeType), [0.05, 0.5, 1.6], [1.0, -1.0], [0.4, 0.9], [0, 4], [0.0, 3.5, 40.0])
    for i, (etype, weight, sign, conf, acts, age) in enumerate(grid):
        meta = {}
        if i % 3 == 1:
            meta["message_distance"] = 3
        elif i % 3 == 2:
            meta["message_distance"] = 20
        e = NeuralEdge(edge_id=f"g{i}", source_id="a", target_id="b", edge_type=etype, base_weight=weight,
                       sign=sign, confidence=conf, propagation_delay_ms=[0.0, 2500.0, 9000.0][i % 3], metadata=meta)
        for _ in range(acts):
            e.activate()
        e.last_activated = R.FIXED_NOW - timedelta(days=age)
        e.created_at = R.FIXED_NOW - timedelta(days=age + 1)
        out.append(e)
    return out


def config_dict(c):
    return None if c is None else {"threshold": c.threshold, "steepness": c.steepness,
                                   "recency_half_life_days": c.recency_half_life_days, "ltp_weight": c.ltp_weight,
                                   "confidence_weight": c.confidence_weight}


def profile_dict(p):
    return {"name": p.name, "semantic_weight": p.semantic_weight, "entity_weight": p.entity_weight,
            "temporal_weight": p.temporal_weight, "causal_weight": p.causal_weight,
            "hierarchy_weight": p.hierarchy_weight, "co_activation_weight": p.co_activation_weight,
            "base_config": config_dict(p.base_config), "exploration_factor": p.exploration_factor}


def scalars():
    xs = [-2.0, -0.3, 0.0, 0.1, 0.3, 0.45, 1.0, 2.5]
    return {
        "xs": xs,
        "sigmoid": [[G.sigmoid(x, s, t) for x in xs] for s, t in [(5.0, 0.0), (1.0, 0.3), (10.0, 0.4), (2.0, 0.15)]],
        "relu": [[G.relu(x, t) for x in xs] for t in [0.0, 0.4]],
        "leaky_relu": [[G.leaky_relu(x, t, l) for x in xs] for t, l in [(0.0, 0.1), (0.3, 0.2)]],
        "soft_threshold": [[G.soft_threshold(x, t, s) for x in xs] for t, s in [(0.3, 0.1), (0.2, 0.0), (0.5, -1.0), (0.1, 0.5)]],
    }


def main():
    es = edges()
    registries = {
        "balanced": G.create_balanced_registry(),
        "strict": G.create_strict_registry(),
        "permissive": G.create_permissive_registry(),
        "profiled_default": G.ProfiledGateRegistry(),
        "single_hop": G.create_single_hop_registry(),
        "multi_hop": G.create_multi_hop_registry(),
        "temporal": G.ProfiledGateRegistry(G.TEMPORAL_QUERY_PROFILE),
        "open_domain": G.ProfiledGateRegistry(G.OPEN_DOMAIN_PROFILE),
        "default_profile": G.ProfiledGateRegistry(G.get_profile("default")),
        "profiled_plain_multi_hop": G.create_profiled_registry(G.MULTI_HOP_PROFILE),
        "edge_registry_none": G.EdgeGateRegistry(None),
    }
    gates = {name: [[reg.compute_gate(e, ctx) for ctx in CONTEXTS] for e in es] for name, reg in registries.items()}
    balanced = registries["balanced"]
    direct = [[balanced.get_gate(e.edge_type)(e, ctx) for ctx in CONTEXTS] for e in es]
    default_gate = [balanced._default_gate(e, {}) for e in es]
    inhibitory = [[G.inhibitory_gate(e, ctx) for ctx in CONTEXTS] for e in es]
    none_context = [balanced.compute_gate(e) for e in es]

    # profile switching on one registry
    switching = []
    reg = G.ProfiledGateRegistry()
    for name in ["multi_hop", "default", "bogus", "temporal", "single_hop"]:
        reg.set_profile(name)
        switching.append({"name": name, "profile": reg._profile.name, "config": config_dict(reg._config),
                          "values": [reg.compute_gate(e, {}) for e in es[::7]]})

    composites = []
    for combo in ["multiply", "min", "max", "mean", "other"]:
        cg = G.CompositeGate(combo)
        cg.add_gate(balanced.compute_gate).add_gate(G.inhibitory_gate).add_gate(registries["strict"].compute_gate)
        composites.append({"combination": combo, "values": [cg(e, {"inhibition_strength": 0.7}) for e in es]})
    empty = G.CompositeGate()
    composites.append({"combination": "empty", "values": [empty(e, {}) for e in es[:5]]})

    adaptive = []
    ag = G.AdaptiveGate(balanced.compute_gate)
    for count, target in [(200, 50), (200, 50), (5, 50), (50, 50), (101, 50), (24, 50), (24, 50), (500, 50),
                          (500, 50), (500, 50), (500, 50), (500, 50), (500, 50), (500, 50), (10, 21), (0, 1)]:
        ag.adapt_threshold(count, target)
        adaptive.append({"count": count, "target": target, "threshold": ag._current_threshold,
                         "values": [ag(e, {}) for e in es[::5]]})
    ag2 = G.AdaptiveGate(registries["permissive"].compute_gate, min_threshold=0.2, max_threshold=0.4)
    adaptive2 = [ag2(e, {"similarity_threshold": 0.1}) for e in es]

    R.dump("gating.json", {
        "contexts": CONTEXTS,
        "edges": [e.to_dict() for e in es],
        "scalars": scalars(),
        "gates": gates,
        "direct": direct,
        "default_gate": default_gate,
        "inhibitory": inhibitory,
        "none_context": none_context,
        "switching": switching,
        "composites": composites,
        "adaptive": adaptive,
        "adaptive2": {"threshold": ag2._current_threshold, "values": adaptive2},
        "profiles": {k: profile_dict(v) for k, v in G.GATE_PROFILES.items()},
        "get_profile": {q: G.get_profile(q).name for q in ["single_hop", "multi_hop", "temporal", "open_domain",
                                                          "default", "unknown"]},
        "registry_configs": {k: config_dict(v._config) for k, v in registries.items()},
    })


main()
