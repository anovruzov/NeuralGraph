"""Failure regimes, from random churn up to white-box adversarial lineage attack.

Every regime returns a node-liveness mask (and, where relevant, reachability,
corruption and partition structure) for a given severity.  Random deletion alone
is a weak test, so the suite also contains:

  correlated  -- whole failure domains / organisations / regions disappear
  targeted    -- an attacker that *knows the placement* removes the hosts of the
                 most important surviving independent evidence first (white box)
  targeted_world -- the same attack computed from the world only, so that a single
                 identical node set can be applied to every system (comparable)
  partition   -- the network splits, updates happen independently, then reconnects
  corruption  -- a fraction of nodes assert a coordinated false value
"""

from __future__ import annotations

import numpy as np

from .evaluate import PairIndex
from .systems import Placements
from .world import World

REGIMES = ["random", "correlated_domain", "correlated_org", "correlated_region",
           "targeted_world", "targeted_whitebox"]


def random_churn(w: World, rng: np.random.Generator, severity: float) -> np.ndarray:
    n_down = int(round(severity * w.n_nodes))
    alive = np.ones(w.n_nodes, bool)
    if n_down:
        alive[rng.choice(w.n_nodes, n_down, replace=False)] = False
    return alive


def _unit_failure(w: World, rng, severity: float, unit: np.ndarray) -> np.ndarray:
    """Remove whole units (domains / orgs / regions) until severity is reached."""
    n_units = int(unit.max() + 1)
    order = rng.permutation(n_units)
    sizes = np.bincount(unit, minlength=n_units)
    target = severity * w.n_nodes
    down = np.zeros(n_units, bool)
    total = 0
    for u in order:
        if total >= target:
            break
        down[u] = True
        total += sizes[u]
    alive = ~down[unit]
    # trim/extend at node granularity so every regime hits the same severity
    n_down = int((~alive).sum())
    want = int(round(severity * w.n_nodes))
    if n_down > want:
        idx = np.flatnonzero(~alive)
        alive[rng.choice(idx, n_down - want, replace=False)] = True
    elif n_down < want:
        idx = np.flatnonzero(alive)
        alive[rng.choice(idx, min(want - n_down, idx.size), replace=False)] = False
    return alive


def correlated_failure(w: World, rng, severity: float, unit: str = "domain") -> np.ndarray:
    key = {"domain": w.node_domain, "org": w.node_org, "region": w.node_region}[unit]
    return _unit_failure(w, rng, severity, key)


def targeted_world_attack(w: World, rng, severity: float) -> np.ndarray:
    """System-agnostic attack: remove the hosts of the world's independent evidence.

    Node score = sum over the evidence it homes of importance(claim) / n_roots(claim),
    i.e. nodes carrying scarce independent support for important claims die first.
    The identical node set is applied to every system, so this regime compares
    architectures under one common adversary.
    """
    weight = (w.claim_importance[w.item_claim] /
              np.maximum(w.claim_n_roots[w.item_claim], 1)).astype(np.float64)
    score = np.bincount(w.item_home, weights=weight, minlength=w.n_nodes)
    score = score + rng.random(w.n_nodes) * 1e-9      # deterministic tie-break
    n_down = int(round(severity * w.n_nodes))
    alive = np.ones(w.n_nodes, bool)
    if n_down:
        alive[np.argsort(-score)[:n_down]] = False
    return alive


def targeted_lineage_attack(w: World, pl: Placements, idx: PairIndex, rng,
                            severity: float, rounds: int = 8) -> np.ndarray:
    """White-box greedy attack against one system's own placement.

    In each round every still-live node is scored by the marginal independent
    support it would destroy:
        score(v) = sum over live (claim, root) pairs hosted by v of
                   importance(claim) / (live_hosts(pair) * live_roots(claim))
    The top nodes are removed and the scores recomputed.  This is the strongest
    adversary in the suite and is recomputed *per system*, so no architecture is
    attacked with another architecture's weak spots.
    """
    n_down = int(round(severity * w.n_nodes))
    alive = np.ones(w.n_nodes, bool)
    if n_down == 0:
        return alive
    per_round = int(np.ceil(n_down / rounds))
    removed = 0
    imp = w.claim_importance.astype(np.float64)
    while removed < n_down:
        alive_pl = alive[pl.pl_node]
        if not alive_pl.any():
            break
        hosts = np.bincount(idx.root_pair[alive_pl], minlength=idx.n_root_pairs)
        pair_live = hosts > 0
        live_roots = np.bincount(idx.root_pair_claim[np.flatnonzero(pair_live)],
                                 minlength=w.n_claims).astype(np.float64)
        sel = np.flatnonzero(alive_pl)
        pr = idx.root_pair[sel]
        cl = pl.pl_claim[sel]
        wgt = imp[cl] / (np.maximum(hosts[pr], 1) * np.maximum(live_roots[cl], 1.0))
        score = np.bincount(pl.pl_node[sel], weights=wgt, minlength=w.n_nodes)
        score = np.where(alive, score, -1.0) + rng.random(w.n_nodes) * 1e-12
        take = min(per_round, n_down - removed)
        victims = np.argsort(-score)[:take]
        alive[victims] = False
        removed += take
    return alive


def make_alive(w: World, regime: str, severity: float, rng, pl=None, idx=None) -> np.ndarray:
    if regime == "random":
        return random_churn(w, rng, severity)
    if regime == "correlated_domain":
        return correlated_failure(w, rng, severity, "domain")
    if regime == "correlated_org":
        return correlated_failure(w, rng, severity, "org")
    if regime == "correlated_region":
        return correlated_failure(w, rng, severity, "region")
    if regime == "targeted_world":
        return targeted_world_attack(w, rng, severity)
    if regime == "targeted_whitebox":
        return targeted_lineage_attack(w, pl, idx, rng, severity)
    raise ValueError(f"unknown regime {regime}")


IS_WHITEBOX = {"targeted_whitebox"}


# -------------------------------------------------------------------------
# corruption
# -------------------------------------------------------------------------

def corrupt_nodes(w: World, rng, level: float, coordinated: bool = True,
                  n_variants: int = 4):
    mask = rng.random(w.n_nodes) < level
    variant = None
    if not coordinated:
        variant = rng.integers(0, n_variants, w.n_nodes).astype(np.int8)
    return mask, variant


def corrupt_roots(w: World, rng, level: float) -> np.ndarray:
    """Corrupt origin sources: every derived copy inherits the false value."""
    return rng.random(w.n_roots) < level


# -------------------------------------------------------------------------
# partition
# -------------------------------------------------------------------------

def partition_components(w: World, rng, n_parts: int) -> np.ndarray:
    n_regions = int(w.domain_region.max() + 1)
    assign = rng.integers(0, n_parts, n_regions)
    # guarantee every part is non-empty
    for p in range(min(n_parts, n_regions)):
        assign[p] = p
    return assign[w.node_region].astype(np.int32)


def partition_updates(w: World, rng, comp: np.ndarray, update_fraction: float):
    """Claims updated during the split, and which components observed the update."""
    updated = rng.random(w.n_claims) < update_fraction
    n_parts = int(comp.max() + 1)
    # the update is observed by the component holding the claim's first origin
    first_root = w.claim_root_ptr[:-1].astype(np.int64)
    origin_comp = comp[w.root_origin_node[first_root]]
    saw = np.zeros((w.n_claims, n_parts), bool)
    saw[np.arange(w.n_claims), origin_comp] = True
    return updated, origin_comp
