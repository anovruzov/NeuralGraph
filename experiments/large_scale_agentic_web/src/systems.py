"""The eight systems under test (B0-B7): placement policy + probe order + aggregation.

Model of a decentralised knowledge fabric
-----------------------------------------
Every agent keeps its own local memory (the *home* copy of each evidence item it
produced).  A distribution system decides what to additionally *publish* into the
fabric and where.  A system's retrievable index is exactly its own placement set
(B0 is the degenerate case: nothing is published, only local memory is readable).
Cost is therefore measured as published replicas (storage) and the messages used
to publish them (setup) plus the messages used to probe them (query).

Three policy dimensions are kept explicit so that a reviewer can see which one
does the work:
  placement   -- which evidence items are published, and onto which nodes
  probe order -- in what order a querier contacts the replicas of a claim
  aggregation -- how the retrieved evidence is turned into an answer + confidence

The aggregation rules are deliberately the *same function* for every system; the
only difference is what is counted: raw replicas (count) or distinct lineage
roots (lineage).  See metrics.aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from typing import Any

import numpy as np

from .world import World

AGG_COUNT = "count"
AGG_LINEAGE = "lineage"

SYSTEM_IDS = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]

SYSTEM_LABELS = {
    "B0": "B0 isolated local memory",
    "B1": "B1 centralized global store",
    "B2": "B2 full replication",
    "B3": "B3 random replication (equal budget)",
    "B4": "B4 gossip dissemination",
    "B5": "B5 diversity-blind (support count)",
    "B6": "B6 lineage-aware fabric",
    "B7": "B7 lineage + continual questioning",
    "B3Q": "B3+Q random replication + continual questioning (ablation: keeps the provenance pointer questioning needs, drops lineage-aware placement and aggregation)",
    "B3L": "ablation: random placement + lineage aggregation",
    "B6C": "ablation: lineage placement + count aggregation",
    "B2L": "ablation: broad replication + lineage aggregation",
}


@dataclass
class Placements:
    """Placement set of one system, grouped by claim in probe order."""

    system: str
    pl_claim: np.ndarray      # int32, sorted
    pl_item: np.ndarray       # int32
    pl_node: np.ndarray       # int32
    claim_ptr: np.ndarray     # int64, len n_claims+1
    aggregation: str
    storage_units: int        # published replicas (the storage cost axis)
    setup_messages: int       # messages spent publishing
    central_nodes: np.ndarray | None = None   # B1 only: cluster whose liveness is OR-ed
    isolated: bool = False    # B0 only: a placement is readable only by its own node
    code_override: np.ndarray | None = None   # int8[P], -1 = none (set by questioning)
    code_override_late: np.ndarray | None = None  # applied after partition staleness
    extra: dict[str, Any] | None = None

    @property
    def n_placements(self) -> int:
        return int(self.pl_claim.size)


def _root_item_ptr(w: World) -> np.ndarray:
    counts = np.bincount(w.item_root, minlength=w.n_roots)
    return np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)


def _group_by_claim(n_claims: int, pl_claim, pl_item, pl_node, key) -> tuple:
    order = np.lexsort((key, pl_claim))
    pl_claim = pl_claim[order].astype(np.int32)
    pl_item = pl_item[order].astype(np.int32)
    pl_node = pl_node[order].astype(np.int32)
    counts = np.bincount(pl_claim, minlength=n_claims)
    ptr = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    return pl_claim, pl_item, pl_node, ptr


def _nodes_in_regions(w: World, rng, regions: np.ndarray) -> np.ndarray:
    """Sample one node *uniformly among the nodes of* each requested region.

    Sampling a domain first and then a node inside it would concentrate placement
    on the members of small domains; that load imbalance, not lineage awareness,
    would then decide who survives a targeted attack.  Uniform-within-region
    sampling keeps every policy's load distribution comparable.
    """
    rsize = (w.region_ptr[1:] - w.region_ptr[:-1]).astype(np.int64)
    reg = np.asarray(regions, dtype=np.int64)
    empty = rsize[reg] == 0
    if empty.any():
        nonempty = np.flatnonzero(rsize > 0)
        reg = np.where(empty, nonempty[reg % nonempty.size], reg)
    off = (rng.random(reg.size) * rsize[reg]).astype(np.int64)
    return w.region_members[w.region_ptr[reg] + off].astype(np.int32)


# --------------------------------------------------------------------------
# individual placement policies
# --------------------------------------------------------------------------

def build_B0(w: World, cfg, rng) -> Placements:
    pl_claim, pl_item, pl_node, ptr = _group_by_claim(
        w.n_claims, w.item_claim.copy(), np.arange(w.n_items, dtype=np.int32),
        w.item_home.copy(), rng.random(w.n_items))
    return Placements("B0", pl_claim, pl_item, pl_node, ptr, AGG_COUNT,
                      storage_units=0, setup_messages=0, isolated=True)


def build_B1(w: World, cfg, rng) -> Placements:
    c = int(cfg.get("central_replicas", 3))
    # a central cluster of c mirrors placed in distinct regions
    n_regions = int(w.domain_region.max() + 1)
    central = _nodes_in_regions(w, rng, np.arange(c) % n_regions)
    pl_item = np.arange(w.n_items, dtype=np.int32)
    pl_node = np.full(w.n_items, central[0], dtype=np.int32)
    pl_claim, pl_item, pl_node, ptr = _group_by_claim(
        w.n_claims, w.item_claim.copy(), pl_item, pl_node, rng.random(w.n_items))
    return Placements("B1", pl_claim, pl_item, pl_node, ptr, AGG_COUNT,
                      storage_units=c * w.n_items, setup_messages=c * w.n_items,
                      central_nodes=central.astype(np.int32))


def build_B2(w: World, cfg, rng) -> Placements:
    r_full = int(cfg.get("full_replicas", 16))
    pl_item = np.repeat(np.arange(w.n_items, dtype=np.int32), r_full)
    pl_node = rng.integers(0, w.n_nodes, pl_item.size).astype(np.int32)
    pl_claim = w.item_claim[pl_item]
    pl_claim, pl_item, pl_node, ptr = _group_by_claim(
        w.n_claims, pl_claim, pl_item, pl_node, rng.random(pl_item.size))
    n = int(pl_item.size)
    return Placements("B2", pl_claim, pl_item, pl_node, ptr, AGG_COUNT,
                      storage_units=n, setup_messages=n)


def _budgeted_random(w: World, k: int, rng) -> tuple:
    """k placements per claim: items drawn uniformly from the claim's evidence."""
    m = w.n_claims
    claim_rep = np.repeat(np.arange(m, dtype=np.int32), k)
    n_items_c = w.claim_n_items[claim_rep].astype(np.int64)
    off = (rng.random(claim_rep.size) * n_items_c).astype(np.int64)
    pl_item = (w.claim_item_ptr[claim_rep] + off).astype(np.int32)
    return claim_rep, pl_item


def build_B3(w: World, cfg, rng) -> Placements:
    k = int(cfg.get("budget_k", 4))
    claim_rep, pl_item = _budgeted_random(w, k, rng)
    pl_node = rng.integers(0, w.n_nodes, pl_item.size).astype(np.int32)
    pl_claim, pl_item, pl_node, ptr = _group_by_claim(
        w.n_claims, claim_rep, pl_item, pl_node, rng.random(pl_item.size))
    n = int(pl_item.size)
    return Placements("B3", pl_claim, pl_item, pl_node, ptr, AGG_COUNT,
                      storage_units=n, setup_messages=n)


def build_B4(w: World, cfg, rng) -> Placements:
    """Gossip: every item spreads to g peers drawn from the home's locality."""
    g = int(cfg.get("gossip_replicas", 4))
    redundancy = float(cfg.get("gossip_redundancy", 1.8))
    pl_item = np.repeat(np.arange(w.n_items, dtype=np.int32), g)
    home = w.item_home[pl_item]
    u = rng.random(pl_item.size)
    node = np.empty(pl_item.size, np.int32)

    dom = w.node_domain[home]
    dsize = (w.domain_ptr[1:] - w.domain_ptr[:-1]).astype(np.int64)
    sel = u < 0.5
    idx = np.where(sel)[0]
    off = (rng.random(idx.size) * np.maximum(dsize[dom[idx]], 1)).astype(np.int64)
    node[idx] = w.domain_members[w.domain_ptr[dom[idx]] + off]

    org = w.node_org[home]
    osize = (w.org_ptr[1:] - w.org_ptr[:-1]).astype(np.int64)
    sel2 = (~sel) & (u < 0.85)
    idx = np.where(sel2)[0]
    off = (rng.random(idx.size) * np.maximum(osize[org[idx]], 1)).astype(np.int64)
    node[idx] = w.org_members[w.org_ptr[org[idx]] + off]

    sel3 = (~sel) & (~sel2) & (u < 0.95)
    idx = np.where(sel3)[0]
    if idx.size:
        node[idx] = _nodes_in_regions(w, rng, w.node_region[home[idx]])
    sel4 = ~(sel | sel2 | sel3)
    if sel4.any():
        node[sel4] = rng.integers(0, w.n_nodes, int(sel4.sum())).astype(np.int32)

    pl_claim = w.item_claim[pl_item]
    pl_claim, pl_item, pl_node, ptr = _group_by_claim(
        w.n_claims, pl_claim, pl_item, node, rng.random(pl_item.size))
    n = int(pl_item.size)
    return Placements("B4", pl_claim, pl_item, pl_node, ptr, AGG_COUNT,
                      storage_units=n, setup_messages=int(round(n * redundancy)))


def build_B5(w: World, cfg, rng) -> Placements:
    """Diversity-blind: keep the k best-corroborated copies (largest family)."""
    k = int(cfg.get("budget_k", 4))
    rip = _root_item_ptr(w)
    fam = (rip[1:] - rip[:-1]).astype(np.int64)
    # largest family per claim
    m = w.n_claims
    best_root = np.zeros(m, np.int64)
    # vectorised argmax of family size within each claim's root block
    order = np.lexsort((np.arange(w.n_roots), -fam, w.root_claim))
    first = np.concatenate([[True], w.root_claim[order][1:] != w.root_claim[order][:-1]])
    best_root[w.root_claim[order][first]] = order[first]
    claim_rep = np.repeat(np.arange(m, dtype=np.int32), k)
    j = np.tile(np.arange(k, dtype=np.int64), m)
    r = best_root[claim_rep]
    pl_item = (rip[r] + (j % np.maximum(fam[r], 1))).astype(np.int32)
    pl_node = rng.integers(0, w.n_nodes, pl_item.size).astype(np.int32)
    # probe order: most-corroborated first (that is this policy's whole idea)
    pl_claim, pl_item, pl_node, ptr = _group_by_claim(
        w.n_claims, claim_rep, pl_item, pl_node, j.astype(np.float64))
    n = int(pl_item.size)
    return Placements("B5", pl_claim, pl_item, pl_node, ptr, AGG_COUNT,
                      storage_units=n, setup_messages=n)


def diverse_nodes(w: World, rng, pl_claim: np.ndarray, rounds: int = 6) -> np.ndarray:
    """Uniformly random hosts, subject to a distinct-failure-domain constraint.

    Hosts are drawn uniformly over the whole network -- so the per-node storage
    load matches random replication exactly -- and any placement that lands in a
    failure domain already used by the same claim is redrawn.  Enforcing
    diversity by *rejection* rather than by cycling over domains matters: cycling
    puts an equal number of replicas in every domain regardless of its size,
    which creates hot-spot nodes in small domains that a targeted attacker can
    exploit.  That would make the lineage system look fragile for reasons that
    have nothing to do with lineage.
    """
    p = pl_claim.size
    nodes = rng.integers(0, w.n_nodes, p).astype(np.int32)
    n_dom = int(w.domain_ptr.size - 1)
    tie = np.arange(p)
    for _ in range(rounds):
        key = pl_claim.astype(np.int64) * (n_dom + 1) + w.node_domain[nodes]
        order = np.lexsort((tie, key))
        ks = key[order]
        dup_sorted = np.concatenate([[False], ks[1:] == ks[:-1]])
        if not dup_sorted.any():
            break
        dup = np.zeros(p, bool)
        dup[order] = dup_sorted
        nodes[dup] = rng.integers(0, w.n_nodes, int(dup.sum())).astype(np.int32)
    return nodes


def lineage_placement(w: World, k: int, rng, extra_per_claim: np.ndarray | None = None):
    """Round-robin over distinct lineage roots, spread across regions/domains.

    Placement j of claim c uses root (j mod n_roots(c)) and a node drawn from
    region (base(c)+j) mod n_regions, so the first placements are maximally
    diverse in both provenance and failure domain.
    """
    m = w.n_claims
    rip = _root_item_ptr(w)
    fam = (rip[1:] - rip[:-1]).astype(np.int64)
    if extra_per_claim is None:
        counts = np.full(m, k, np.int64)
    else:
        counts = np.maximum(extra_per_claim.astype(np.int64), 0)
    claim_rep = np.repeat(np.arange(m, dtype=np.int32), counts)
    total = claim_rep.size
    starts = np.repeat(np.concatenate([[0], np.cumsum(counts)[:-1]]), counts)
    j = (np.arange(total, dtype=np.int64) - starts)
    nr = w.claim_n_roots[claim_rep].astype(np.int64)
    root = (w.claim_root_ptr[claim_rep] + (j % nr)).astype(np.int64)
    within = (j // nr) % np.maximum(fam[root], 1)
    pl_item = (rip[root] + within).astype(np.int32)
    pl_node = diverse_nodes(w, rng, claim_rep)
    return claim_rep, pl_item, pl_node, j


def build_B6(w: World, cfg, rng) -> Placements:
    k = int(cfg.get("budget_k", 4))
    claim_rep, pl_item, pl_node, j = lineage_placement(w, k, rng)
    pl_claim, pl_item, pl_node, ptr = _group_by_claim(
        w.n_claims, claim_rep, pl_item, pl_node, j.astype(np.float64))
    n = int(pl_item.size)
    return Placements("B6", pl_claim, pl_item, pl_node, ptr, AGG_LINEAGE,
                      storage_units=n, setup_messages=n)


def build_B3L(w: World, cfg, rng) -> Placements:
    """Ablation cell: random placement + lineage-aware aggregation."""
    pl = build_B3(w, cfg, rng)
    return dc_replace(pl, system="B3L", aggregation=AGG_LINEAGE)


def build_B6C(w: World, cfg, rng) -> Placements:
    """Ablation cell: lineage-aware placement + replica-count aggregation."""
    pl = build_B6(w, cfg, rng)
    return dc_replace(pl, system="B6C", aggregation=AGG_COUNT)


def build_B2L(w: World, cfg, rng) -> Placements:
    """Ablation cell: broad replication + lineage-aware aggregation."""
    pl = build_B2(w, cfg, rng)
    return dc_replace(pl, system="B2L", aggregation=AGG_LINEAGE)


BUILDERS = {
    "B0": build_B0, "B1": build_B1, "B2": build_B2, "B3": build_B3,
    "B4": build_B4, "B5": build_B5, "B6": build_B6,
    "B3L": build_B3L, "B6C": build_B6C, "B2L": build_B2L,
}


def build_system(system: str, w: World, cfg: dict, rng) -> Placements:
    """B7 is built by questioning.build_B7 (it needs the failure context)."""
    return BUILDERS[system](w, cfg, rng)
