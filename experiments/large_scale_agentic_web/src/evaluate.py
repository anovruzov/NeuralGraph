"""Vectorised probe + aggregation engine.

Every evidence item carries one of a small number of *value codes*:
    0                     -> the claim's current true value
    1                     -> the claim's previous value (stale observation)
    2 .. 2+F-1            -> false value variant (corrupted evidence)
An answer is correct iff its code is 0.  Because the code space is small, the
whole retrieval + aggregation pipeline is a handful of ``bincount`` calls, which
is what makes N = 10^4-10^5 with 30 seeds tractable.

Retrieval model
---------------
A querier asks for claim c.  The system hands it an ordered candidate list (its
placements for c, in that system's probe order).  The querier walks the list and
fetches from live, reachable hosts until it has ``max_probe`` successes or the
list is exhausted.  *Every* attempt costs a message, including attempts on dead
hosts -- so broad replication buys availability but pays for it in probe traffic.

Aggregation
-----------
Identical function for every system; the only difference is the support unit:
    count   aggregation -> support = number of retrieved replicas
    lineage aggregation -> support = number of distinct lineage roots
Confidence uses the same formula in both cases:
    conf = (support_max / support_total) * (1 - 2 ** -support_max)
so any calibration difference comes from *what is counted*, not from a tuned
confidence curve.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .systems import AGG_LINEAGE, Placements
from .world import World


@dataclass
class PairIndex:
    """Per-system precomputation reused across every failure condition."""

    pl_start: np.ndarray        # int64[P] index of the first placement of the claim
    local_idx: np.ndarray       # int64[P] 1-based position within the claim
    claim_len: np.ndarray       # int64[M]
    root_pair: np.ndarray       # int64[P] id of the (claim, lineage root) pair
    root_pair_claim: np.ndarray  # int64[n_root_pairs]
    n_root_pairs: int
    dom_pair: np.ndarray        # int64[P] id of the (claim, failure domain) pair
    dom_pair_claim: np.ndarray
    n_dom_pairs: int
    pl_root: np.ndarray         # int32[P] lineage root of each placement
    pl_stale: np.ndarray        # bool[P]


def precompute(w: World, pl: Placements) -> PairIndex:
    p = pl.n_placements
    pl_start = pl.claim_ptr[pl.pl_claim]
    local_idx = np.arange(p, dtype=np.int64) - pl_start + 1
    claim_len = (pl.claim_ptr[1:] - pl.claim_ptr[:-1]).astype(np.int64)

    pl_root = w.item_root[pl.pl_item]
    key = pl.pl_claim.astype(np.int64) * (w.n_roots + 1) + pl_root
    uk, root_pair = np.unique(key, return_inverse=True)
    root_pair_claim = (uk // (w.n_roots + 1)).astype(np.int64)

    n_dom = int(w.domain_ptr.size - 1)
    key2 = pl.pl_claim.astype(np.int64) * (n_dom + 1) + w.node_domain[pl.pl_node]
    uk2, dom_pair = np.unique(key2, return_inverse=True)
    dom_pair_claim = (uk2 // (n_dom + 1)).astype(np.int64)

    return PairIndex(
        pl_start=pl_start, local_idx=local_idx, claim_len=claim_len,
        root_pair=root_pair.astype(np.int64), root_pair_claim=root_pair_claim,
        n_root_pairs=int(uk.size),
        dom_pair=dom_pair.astype(np.int64), dom_pair_claim=dom_pair_claim,
        n_dom_pairs=int(uk2.size),
        pl_root=pl_root.astype(np.int32), pl_stale=w.item_stale[pl.pl_item],
    )


@dataclass
class Condition:
    """One failure configuration applied to the world."""

    alive: np.ndarray            # bool[N]
    reachable: np.ndarray | None  # bool[N] or None (fully reachable)
    node_corrupt: np.ndarray | None   # bool[N]
    root_corrupt: np.ndarray | None   # bool[R]
    querier: np.ndarray          # int32[M] node issuing the query for each claim
    n_false_variants: int = 1
    node_false_variant: np.ndarray | None = None  # int8[N] for uncoordinated corruption
    max_probe: int = 8
    parallel_probe: int = 4
    # partition regime: claims updated while the network was split, and the set
    # of nodes that were in a component which saw the update
    partition_updated: np.ndarray | None = None   # bool[M]
    node_saw_update: np.ndarray | None = None     # bool[N]


@dataclass
class ClaimResult:
    answered: np.ndarray        # bool[M]  any evidence retrieved
    correct: np.ndarray         # bool[M]  retrieved answer is the current truth
    answer_code: np.ndarray     # int8[M]  -1 if unanswered
    confidence: np.ndarray      # float32[M]
    support_max: np.ndarray     # float32[M]
    support_total: np.ndarray   # float32[M]
    n_distinct_values: np.ndarray   # int32[M] distinct value codes retrieved
    iss: np.ndarray             # float32[M] surviving independent roots / original
    surviving_roots: np.ndarray  # int32[M]
    probed_roots: np.ndarray    # int32[M]
    attempts: np.ndarray        # int64[M] messages spent probing
    rounds: np.ndarray          # float32[M] sequential probe rounds (T_R proxy)


def _value_codes_subset(w: World, pl: Placements, idx: PairIndex,
                        cond: Condition, sel: np.ndarray) -> np.ndarray:
    """Value code of each *retrieved* placement (see module docstring)."""
    codes = idx.pl_stale[sel].astype(np.int64)          # 0 fresh, 1 stale
    ov = getattr(pl, "code_override", None)
    if ov is not None:
        o = ov[sel]
        codes = np.where(o >= 0, o.astype(np.int64), codes)
    if cond.partition_updated is not None:
        cl = pl.pl_claim[sel]
        stale_split = cond.partition_updated[cl] & (~cond.node_saw_update[pl.pl_node[sel]])
        codes = np.where(stale_split, 1, codes)
    late = getattr(pl, "code_override_late", None)
    if late is not None:
        o = late[sel]
        codes = np.where(o >= 0, o.astype(np.int64), codes)
    if cond.root_corrupt is not None:
        rc = cond.root_corrupt[idx.pl_root[sel]]
        if rc.any():
            codes = np.where(rc, 2, codes)
    if cond.node_corrupt is not None:
        nodes = pl.pl_node[sel]
        nc = cond.node_corrupt[nodes]
        if nc.any():
            var = (cond.node_false_variant[nodes].astype(np.int64)
                   if cond.node_false_variant is not None else 0)
            codes = np.where(nc, 2 + var, codes)
    return codes.astype(np.int64)


def evaluate(w: World, pl: Placements, idx: PairIndex, cond: Condition) -> ClaimResult:
    m = w.n_claims
    v = 2 + max(1, cond.n_false_variants)

    live = cond.alive
    if cond.reachable is not None:
        live = live & cond.reachable

    if pl.central_nodes is not None:
        central_up = bool(live[pl.central_nodes].any())
        alive_pl = np.full(pl.n_placements, central_up, dtype=bool)
    elif pl.isolated:
        alive_pl = live[pl.pl_node] & (pl.pl_node == cond.querier[pl.pl_claim])
    else:
        alive_pl = live[pl.pl_node]

    # ---- ordered probing: first `max_probe` live candidates per claim --------
    cum = np.cumsum(alive_pl, dtype=np.int32)
    rank = cum - np.where(idx.pl_start > 0, cum[idx.pl_start - 1], 0)
    rank[idx.pl_start == 0] = cum[idx.pl_start == 0]
    selected = alive_pl & (rank <= cond.max_probe)

    # exactly one placement per claim can carry rank == max_probe while alive
    attempts = idx.claim_len.copy()
    hit = np.flatnonzero(alive_pl & (rank == cond.max_probe))
    if hit.size:
        attempts[pl.pl_claim[hit]] = idx.local_idx[hit]
    attempts = np.minimum(attempts, idx.claim_len)
    if pl.isolated:
        attempts = np.zeros(m, np.int64)     # local memory read: no network traffic

    # ---- support tallies, computed only over what was actually retrieved -----
    sel = np.flatnonzero(selected)
    codes = _value_codes_subset(w, pl, idx, cond, sel)
    sc = pl.pl_claim[sel].astype(np.int64)

    replica_cnt = np.bincount(sc * v + codes, minlength=m * v).reshape(m, v).astype(np.float32)

    rp = idx.root_pair[sel]
    root_present = np.zeros(idx.n_root_pairs * v, bool)
    root_present[rp * v + codes] = True
    rp_hit = np.flatnonzero(root_present)
    root_cnt = np.bincount(idx.root_pair_claim[rp_hit // v] * v + (rp_hit % v),
                           minlength=m * v).reshape(m, v).astype(np.float32)

    dp = idx.dom_pair[sel]
    dom_present = np.zeros(idx.n_dom_pairs * v, bool)
    dom_present[dp * v + codes] = True
    dp_hit = np.flatnonzero(dom_present)
    dom_cnt = np.bincount(idx.dom_pair_claim[dp_hit // v] * v + (dp_hit % v),
                          minlength=m * v).reshape(m, v).astype(np.float32)

    if pl.aggregation == AGG_LINEAGE:
        primary, secondary = root_cnt, dom_cnt
    else:
        primary, secondary = replica_cnt, None

    # argmax over value codes: primary support, then (lineage only) failure-domain
    # diversity, then a truth-neutral per-(claim, code) jitter shared by every
    # system, so ties never resolve preferentially toward the true value
    jitter = w.code_tiebreak[:, :v]
    score = primary * 1e6 + jitter if secondary is None else primary * 1e6 + secondary * 1e2 + jitter
    answer_code = np.argmax(score, axis=1).astype(np.int8)
    rows = np.arange(m)
    support_max = primary[rows, answer_code]
    support_total = primary.sum(axis=1)
    answered = replica_cnt.sum(axis=1) > 0
    answer_code = np.where(answered, answer_code, np.int8(-1)).astype(np.int8)

    share = np.where(support_total > 0, support_max / np.maximum(support_total, 1e-12), 0.0)
    conf = (share * (1.0 - np.power(2.0, -np.maximum(support_max, 0.0)))).astype(np.float32)
    conf = np.where(answered, conf, 0.0).astype(np.float32)

    correct = answered & (answer_code == 0)
    n_distinct = (replica_cnt > 0).sum(axis=1).astype(np.int32)
    probed_roots = root_cnt.sum(axis=1).astype(np.int32)

    # ---- independent-support survival over the whole stored set --------------
    pair_live = np.zeros(idx.n_root_pairs, bool)
    pair_live[idx.root_pair[alive_pl]] = True
    surviving_roots = np.bincount(idx.root_pair_claim[np.flatnonzero(pair_live)],
                                  minlength=m).astype(np.int32)
    iss = (surviving_roots / np.maximum(w.claim_n_roots, 1)).astype(np.float32)

    rounds = np.ceil(attempts / max(1, cond.parallel_probe)).astype(np.float32)

    return ClaimResult(
        answered=answered, correct=correct, answer_code=answer_code, confidence=conf,
        support_max=support_max.astype(np.float32), support_total=support_total.astype(np.float32),
        n_distinct_values=n_distinct, iss=iss, surviving_roots=surviving_roots,
        probed_roots=probed_roots, attempts=attempts, rounds=rounds,
    )
