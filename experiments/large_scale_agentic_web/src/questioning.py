"""Continual discovery: agents question their own knowledge, at a real cost.

Three question types mutate the fabric.  Each consumes messages, and ADD/RESOLVE
also consume storage, so any gain has a price that is reported alongside it:

  REVERIFY     "has this fact changed since it was last verified?"
               re-contacts the origin of every stored lineage root for the claim.
               If the origin is corrupt, re-verification installs the *false*
               value -- questioning can make things worse, and we measure that.
  ADD_SUPPORT  "which independent source confirms this claim?" /
               "which dependency for this claim is currently unknown?"
               publishes one replica from a lineage root not yet covered.
  RESOLVE      "why does node A report x and node B report y?"
               re-verifies and adds one independent freshly-verified replica.

Targeting strategies
  strategic  -- budget goes to importance x lineage fragility (fewest distinct
                stored roots first): the paper's "what has the weakest independent
                support?" question
  random     -- budget spread uniformly (ablation: is the *targeting* what helps?)
  flood      -- budget far exceeds the useful targets (negative-result regime)

`equalize_storage=True` shrinks the base placement budget by exactly the number
of replicas questioning adds, so B7 can be compared with B6 at identical storage.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace as dc_replace

import numpy as np

from .evaluate import PairIndex, precompute
from .systems import (AGG_COUNT, AGG_LINEAGE, Placements, _group_by_claim, _root_item_ptr,
                      diverse_nodes, lineage_placement)
from .world import World

TYPE_MIX = {"reverify": 0.45, "add_support": 0.30, "resolve": 0.25}


@dataclass
class QuestionPlan:
    strategy: str = "strategic"
    budget_per_claim: float = 0.6      # continual-discovery questions per claim
    equalize_storage: bool = True
    mix: tuple = (0.45, 0.30, 0.25)
    msg_reverify: int = 2
    msg_add: int = 3
    msg_resolve: int = 4


@dataclass
class QuestionCost:
    n_questions: int
    messages: int
    writes: int
    extra_storage: int
    n_reverify: int
    n_add: int
    n_resolve: int
    n_targets: int


def _claim_age(w: World, k: int) -> np.ndarray:
    """Age of the oldest lineage timestamp the fabric would store for each claim.

    This uses only metadata the fabric actually holds (observation times of the
    roots it stores), not knowledge of which facts truly changed.
    """
    first = w.claim_root_ptr[:-1]
    last = np.minimum(w.claim_root_ptr[1:], first + k)
    n = (last - first).astype(np.int64)
    csum = np.concatenate([[0.0], np.cumsum(w.root_obs_time.astype(np.float64))])
    mean_t = (csum[last] - csum[first]) / np.maximum(n, 1)
    return (1.0 - mean_t).astype(np.float64)


def _fragility_priority(w: World, base_roots_covered: np.ndarray, strategy: str, rng, k: int):
    """Rank claims for the question budget under the chosen targeting policy."""
    if strategy in ("random", "flood"):
        return rng.permutation(w.n_claims)
    imp = w.claim_importance.astype(np.float64)
    frag = 1.0 / np.maximum(base_roots_covered.astype(np.float64), 0.5)
    age = _claim_age(w, k)
    if strategy == "strategic":          # weakest independent support first
        score = imp * frag
    elif strategy == "temporal":         # oldest verification first
        score = imp * age
    elif strategy == "hybrid":
        score = imp * frag * age
    else:
        raise ValueError(f"unknown questioning strategy {strategy}")
    return np.argsort(-(score + rng.random(w.n_claims) * 1e-9))


def build_questioning_system(
    w: World, cfg: dict, rng, *,
    base: str = "B6",
    plan: QuestionPlan | None = None,
    node_corrupt: np.ndarray | None = None,
    root_corrupt: np.ndarray | None = None,
    system_name: str = "B7",
) -> tuple[Placements, QuestionCost]:
    plan = plan or QuestionPlan()
    if cfg.get("question_mix"):
        plan = dataclasses.replace(plan, mix=tuple(cfg["question_mix"]))
    k = int(cfg.get("budget_k", 4))
    m = w.n_claims
    rip = _root_item_ptr(w)

    n_questions = int(round(plan.budget_per_claim * m))
    f_rev, f_add, f_res = plan.mix
    n_rev = int(round(n_questions * f_rev))
    n_add = int(round(n_questions * f_add))
    n_res = n_questions - n_rev - n_add

    covered = np.minimum(w.claim_n_roots, k).astype(np.int32)
    order = _fragility_priority(w, covered, plan.strategy, rng, k)

    def take(start, count):
        """Claims receiving `count` questions, wrapping when the budget floods."""
        if count <= 0:
            return np.empty(0, np.int64)
        idxs = (np.arange(start, start + count) % m)
        return order[idxs]

    tgt_rev = take(0, n_rev)
    tgt_add = take(n_rev, n_add)
    tgt_res = take(n_rev + n_add, n_res)

    # ADD_SUPPORT / RESOLVE only pay off where an uncovered root exists
    add_pool = np.unique(np.concatenate([tgt_add, tgt_res]))
    has_spare = w.claim_n_roots[add_pool] > k if base == "B6" else np.ones(add_pool.size, bool)
    add_claims = add_pool[has_spare]

    # ---------------- base placement (optionally storage-equalised) ----------
    extra_per_claim = np.full(m, k, np.int64)
    if plan.equalize_storage and add_claims.size:
        extra_per_claim[add_claims] -= 1
        extra_per_claim = np.maximum(extra_per_claim, 1)

    if base == "B6":
        claim_rep, pl_item, pl_node, j = lineage_placement(w, k, rng, extra_per_claim=extra_per_claim)
        agg = AGG_LINEAGE
        order_key = j.astype(np.float64)
    elif base == "B3":
        n_items_c = w.claim_n_items[np.repeat(np.arange(m, dtype=np.int32), extra_per_claim)]
        claim_rep = np.repeat(np.arange(m, dtype=np.int32), extra_per_claim)
        off = (rng.random(claim_rep.size) * n_items_c.astype(np.int64)).astype(np.int64)
        pl_item = (w.claim_item_ptr[claim_rep] + off).astype(np.int32)
        pl_node = rng.integers(0, w.n_nodes, pl_item.size).astype(np.int32)
        agg = AGG_COUNT
        order_key = rng.random(pl_item.size)
    else:
        raise ValueError(base)

    # ---------------- ADD_SUPPORT: publish an uncovered lineage root ---------
    if add_claims.size:
        nr = w.claim_n_roots[add_claims].astype(np.int64)
        root = (w.claim_root_ptr[add_claims] + (k % np.maximum(nr, 1))).astype(np.int64)
        add_item = rip[root].astype(np.int32)
        claim_rep = np.concatenate([claim_rep, add_claims.astype(np.int32)])
        pl_item = np.concatenate([pl_item, add_item])
        pl_node = np.concatenate([pl_node, np.zeros(add_claims.size, np.int32)])
        order_key = np.concatenate([order_key, np.full(add_claims.size, 1e6)])
        if base == "B6":
            pl_node = diverse_nodes(w, rng, claim_rep)
        else:
            pl_node[-add_claims.size:] = rng.integers(0, w.n_nodes, add_claims.size).astype(np.int32)

    pl_claim, pl_item, pl_node, ptr = _group_by_claim(m, claim_rep, pl_item, pl_node, order_key)

    # ---------------- REVERIFY: refresh from the lineage origins -------------
    refreshed_claims = np.unique(np.concatenate([tgt_rev, tgt_res, add_claims]))
    touched = np.zeros(m, bool)
    touched[refreshed_claims] = True
    pl_root = w.item_root[pl_item]
    origin = w.root_origin_node[pl_root]
    bad_origin = np.zeros(pl_item.size, bool)
    if node_corrupt is not None:
        bad_origin |= node_corrupt[origin]
    if root_corrupt is not None:
        bad_origin |= root_corrupt[pl_root]
    override = np.full(pl_item.size, -1, np.int8)
    sel = touched[pl_claim]
    # not every re-verification succeeds: origins can be unreachable or silent
    p_ok = float(cfg.get("reverify_success_prob", 0.9))
    ok = rng.random(pl_item.size) < p_ok
    sel = sel & ok
    override[sel] = np.where(bad_origin[sel], np.int8(2), np.int8(0))
    writes = int(sel.sum())

    n_extra = int(add_claims.size)
    # every question costs a round trip, and every refreshed replica costs a write
    messages = (len(tgt_rev) * plan.msg_reverify + len(tgt_add) * plan.msg_add
                + len(tgt_res) * plan.msg_resolve + writes)
    cost = QuestionCost(
        n_questions=n_questions, messages=messages, writes=writes,
        extra_storage=0 if plan.equalize_storage else n_extra,
        n_reverify=len(tgt_rev), n_add=len(tgt_add), n_resolve=len(tgt_res),
        n_targets=int(touched.sum()),
    )

    n_pl = int(pl_item.size)
    touched_pl = touched[pl_claim]
    pl = Placements(system_name, pl_claim, pl_item, pl_node, ptr, agg,
                    storage_units=n_pl, setup_messages=n_pl + messages,
                    code_override=override,
                    extra={"question_cost": cost.__dict__,
                           "touched_claims": touched,
                           "touched_placements": touched_pl,
                           "bad_origin": bad_origin,
                           "plan": plan})
    return pl, cost


def apply_late_reverify(pl: Placements) -> int:
    """Post-reconnection re-verification: questioned claims re-contact their origins.

    Returns the number of extra messages spent.  A corrupt origin still returns a
    false value, so this can hurt as well as help.
    """
    touched_pl = pl.extra["touched_placements"]
    bad = pl.extra["bad_origin"]
    late = np.full(pl.n_placements, -1, np.int8)
    late[touched_pl] = np.where(bad[touched_pl], np.int8(2), np.int8(0))
    pl.code_override_late = late
    plan = pl.extra["plan"]
    return int(pl.extra["touched_claims"].sum()) * plan.msg_reverify


def clear_late_reverify(pl: Placements) -> None:
    pl.code_override_late = None
