"""Retrieval routing benchmark (Task E): "which units hold evidence for cell C (label l)?"

A finished `Hierarchy` run is treated as a distributed knowledge store whose
leaf units (teams; squads / the executive in the depth ablations) retain
their own observations.  A query names a cell, a label and an *asker* (a leaf
unit).  A router decides which other leaf units to contact; every contacted
unit answers with a one-cell `Sketch` (bounded bytes, no raw records).

Honesty rules for this module
-----------------------------
* Ground truth (`World.attrs`, `World.effects`, `World.is_copy`, org membership
  of raw records) is read ONLY by `generate_workload` (to choose queries and to
  compute the true holder set) and by `measure_route` (to score a route).
  Both live in the "truth-side" section at the bottom of this file.
* Routers receive a `RoutingContext` (hierarchy state only: node membership,
  `received_from` sketches, claims' lineage, the contacted unit's own local
  store for answering) and a `QueryView` that carries no holder information.
  `route_oracle` is the single exception: it is the reference upper bound and
  is registered with `uses_truth=True`.

Routers (each returns an ordered contact list of leaf units, excluding the
asker, plus question messages / bytes / per-contact hop counts):

* `local_only`        own team only (never escalates).
* `global_only`       broadcast to every other leaf unit in random order.
* `hybrid`            own team; broadcast only when the local count < n_min.
* `hierarchy_aware`   walk up the tree; each ancestor forwards the question to
                      the children whose `received_from` sketch has the cell
                      (the relational plane of the Tesseract multi-plane
                      router).  For cells above the promoted sketch order the
                      ancestor uses the highest plane that exists: all order-k
                      sub-cells must be present (a necessary condition since
                      n(C) <= min_S n(S)).
* `provenance_aware`  ask the executive kernel for accepted claims about the
                      cell and contact their lineage `contributing_units`
                      first (resolved down to leaves through the planes), then
                      continue with the hierarchy walk.
* `lineage_aware`     candidates from the hierarchy walk, contacted in the
                      order that maximises *independent* support (new region
                      > new department > new team > more workers) and stopping
                      as soon as the estimated external independent support
                      reaches `support_min`.
* `oracle`            the true holders (reference only).

Metrics per query (aggregated as means; nan-aware):
retrieval accuracy = recall of true holders within a contact budget
(`recall_at_budget`, also `recall_at_R` with R = |holders| and `recall_full`),
`precision` of contacted units, `unnecessary_escalation_rate` (contacted
non-holders / contacted), `correct_destination` (first contacted unit is a
holder; or nothing contacted when nobody else holds), messages, bytes
(question artifacts + one-cell sketch answers), `latency_hops`,
`cross_team_discovery` (holders outside the asker's unit that were reached),
private-data movement (`raw_bytes_equivalent` of the records that would move
if raw records were shipped vs `sketch_bytes` that actually moved), and the
true independent support of the contacted external evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .hierarchy import Hierarchy, UnitNode
from .schemas import Claim, QuestionArtifact
from .sketch import COL_DW, COL_N, Sketch
from .vocab import N_ATTR, N_LABELS, cell_index

QUESTION_TRIGGER = "retrieval"
EFFECT_QUERY_KINDS = ("local", "cross_team", "global", "temporal", "contradiction", "decoy")
DEFAULT_ROUTERS = ("local_only", "global_only", "hybrid", "hierarchy_aware", "provenance_aware", "lineage_aware", "oracle")

DEFAULTS: dict[str, Any] = {
    "contact_budget": 5,          # units contacted for recall_at_budget
    "n_random_queries": 100,
    "max_effect_queries": 200,
    "random_orders": [2, 3, 4],   # order-2 cells match records in almost every unit; 3-4 are where routing matters
    "asker_holder_prob": 0.7,     # P(asker is itself a holder: "who else has evidence on C?")
    "strong_holder_min": None,    # holders with >= this many records ("strong"); None -> policy.min_cell_n (k-anonymity)
    "plane_fallback": 0,          # extra lower-order planes an ancestor may consult when the exact plane is empty
    "lineage_support_target": None,   # None -> policy.support_min
}


def routing_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULTS)
    if cfg:
        out.update({k: v for k, v in (cfg.get("routing") or {}).items()
                    if v is not None or k in ("lineage_support_target", "strong_holder_min")})
    return out


# --------------------------------------------------------------------------
# Query / route artifacts
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class QueryView:
    """What a router is allowed to see."""
    qid: str
    cell: int
    label: int
    asker: str


@dataclass
class Query:
    qid: str
    cell: int
    label: int
    order: int
    source: str                  # effect | random
    kind: str                    # local | cross_team | global | temporal | contradiction | decoy | random
    effect_id: str | None
    asker: str
    holders: dict[str, int]      # TRUTH (scoring only): leaf unit -> retained records matching the cell
    n_total: int

    def view(self) -> QueryView:
        return QueryView(self.qid, self.cell, self.label, self.asker)


@dataclass
class Route:
    router: str
    contacts: list[str]                    # ordered external leaf units (asker excluded)
    messages_question: int
    bytes_question: int
    hops: dict[str, int]                   # path length (tree edges / 1 for direct) per contact
    escalation_levels: int                 # ancestor levels the question climbed
    n_est: dict[str, int] = field(default_factory=dict)   # router's own evidence estimate per contact
    provenance_contacts: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Routing context: hierarchy state only
# --------------------------------------------------------------------------
class RoutingContext:
    """Read-only view of a finished hierarchy for routers.

    Exposes node membership (parent / children / layer), the relational plane
    (`received_from` sketches at every ancestor), the executive knowledge base
    (claims with lineage) and the contacted unit's local answer.  It never
    touches `hier.records` or the world.
    """

    def __init__(self, hier: Hierarchy, round_: int | None = None, plane_fallback: int = 0,
                 support_target: float | None = None, seed: int = 0) -> None:
        self.nodes: dict[str, UnitNode] = hier.nodes
        self.policy = hier.policy
        self.sketch_order = int(hier.policy.sketch_order)
        self.n_min = int(hier.policy.n_min)
        self.rho = (float(hier.policy.rho_team), float(hier.policy.rho_department), float(hier.policy.rho_region))
        self.support_target = float(hier.policy.support_min if support_target is None else support_target)
        self.leaf_layer = hier.layer_names[0]
        self.root_id = hier.root_node().unit_id
        self.leaf_ids: list[str] = sorted(n.unit_id for n in hier.by_layer.get(self.leaf_layer, []))
        self.round = int(round_) if round_ is not None else int(max((n.last_synth_round for n in hier.nodes.values()), default=0))
        self.plane_fallback = int(plane_fallback)
        self.rng = np.random.default_rng(seed)
        self.ci = cell_index()
        self.q_bytes = QuestionArtifact("q", "a", 0, 0, QUESTION_TRIGGER, 0, ["x"], self.round).wire_bytes()
        self.empty_answer_bytes = Sketch("x", self.leaf_layer, self.round).wire_bytes()
        org = hier.org
        self._team_department = np.asarray(org.team_department)
        self._department_region = np.asarray(org.department_region)
        self._plane_ids: dict[int, tuple[int, dict[int, np.ndarray]]] = {}
        self._answer_cache: dict[tuple[str, int], Sketch | None] = {}
        self._path_cache: dict[str, list[str]] = {}
        self.plane_lookups = 0

    # ---- topology --------------------------------------------------------
    def is_leaf(self, u: str) -> bool:
        return self.nodes[u].layer == self.leaf_layer

    def available(self, u: str) -> bool:
        n = self.nodes.get(u)
        return n is not None and n.available

    def path_to_root(self, u: str) -> list[str]:
        p = self._path_cache.get(u)
        if p is None:
            p = [u]
            cur = self.nodes[u].parent_id
            while cur is not None:
                p.append(cur)
                cur = self.nodes[cur].parent_id
            self._path_cache[u] = p
        return p

    def depth(self, u: str) -> int:
        return len(self.path_to_root(u)) - 1

    def membership(self, leaf: str) -> tuple[int, int, int]:
        """(team, department, region) indices of a leaf unit from org membership."""
        if leaf.startswith("T") and leaf[1:].isdigit():
            t = int(leaf[1:])
        elif leaf.startswith("S") and "." in leaf:
            t = int(leaf[1:5])
        else:
            return (-1, -1, -1)
        d = int(self._team_department[t])
        r = int(self._department_region[d])
        return (t, d, r)

    def independent_support(self, dw: int, dt: int, dd: int, dr: int) -> float:
        rho_t, rho_d, rho_r = self.rho
        if dw <= 0:
            return 0.0
        dr, dd, dt = max(dr, 1), max(dd, 1), max(dt, 1)
        return float(dr + rho_r * (dd - dr) + rho_d * (dt - dd) + rho_t * (dw - dt))

    # ---- relational plane ------------------------------------------------
    def _planes(self, cell: int) -> tuple[int, dict[int, np.ndarray]]:
        hit = self._plane_ids.get(cell)
        if hit is None:
            order = int(self.ci.order_of(np.array([cell]))[0])
            planes = {order: np.array([cell], dtype=np.int64)}
            ids, o = planes[order], order
            while o > 1:
                ids = np.unique(self.ci.sub_ids(ids, o).ravel())
                o -= 1
                planes[o] = ids
            hit = (order, planes)
            self._plane_ids[cell] = hit
        return hit

    def plane(self, parent: str, child: str, cell: int) -> tuple[int, int, int]:
        """(n_est, dw_est, plane_order) from the parent's index of the child; n_est = 0 when the
        child cannot be shown to hold the cell.  The exact plane is tried first; for cells above the
        promoted sketch order (and `plane_fallback` lower planes) all sub-cells must be present."""
        sk = self.nodes[parent].received_from.get(child)
        if sk is None or len(sk.ids) == 0:
            return (0, 0, 0)
        order, planes = self._planes(cell)
        lowest = max(1, min(order, self.sketch_order) - self.plane_fallback)
        for o in range(order, lowest - 1, -1):
            self.plane_lookups += 1
            cnt = sk.lookup(planes[o])
            n = cnt[:, COL_N]
            if (n > 0).all():
                return (int(n.min()), int(max(cnt[:, COL_DW].min(), 1)), o)
        return (0, 0, 0)

    def descend(self, u: str, cell: int, exclude: frozenset[str] | set[str] = frozenset()) -> list[tuple[str, int, int]]:
        """Leaves under `u` that the planes show as holding the cell: [(leaf, n_est, dw_est)], best first."""
        node = self.nodes[u]
        cands = []
        for ch in node.children:
            if ch in exclude or not self.available(ch):
                continue
            n_est, dw, _ = self.plane(u, ch, cell)
            if n_est > 0:
                cands.append((ch, n_est, dw))
        cands.sort(key=lambda t: (-t[1], t[0]))
        out: list[tuple[str, int, int]] = []
        for ch, n_est, dw in cands:
            if self.is_leaf(ch):
                out.append((ch, n_est, dw))
            else:
                out.extend(self.descend(ch, cell, exclude))
        return out

    def walk_up(self, asker: str, cell: int) -> tuple[list[tuple[str, int, int, int]], int]:
        """Climb from the asker to the root; every ancestor forwards to the children (outside the branch
        the question came from) whose plane shows the cell.  Returns [(leaf, n_est, dw_est, level)], levels."""
        found: list[tuple[str, int, int, int]] = []
        seen = {asker}
        came, cur, level = asker, self.nodes[asker].parent_id, 0
        while cur is not None:
            level += 1
            for leaf, n_est, dw in self.descend(cur, cell, exclude={came}):
                if leaf not in seen:
                    seen.add(leaf)
                    found.append((leaf, n_est, dw, level))
            came, cur = cur, self.nodes[cur].parent_id
        return found, level

    # ---- local evidence / answers ----------------------------------------
    def answer(self, u: str, cell: int) -> Sketch | None:
        """The contacted leaf's one-cell answer from its own local store (exact counts; no raw text)."""
        key = (u, cell)
        if key not in self._answer_cache:
            node = self.nodes[u]
            q = QuestionArtifact(f"Q:{u}:{cell}", u, int(cell), 0, QUESTION_TRIGGER, 0, [u], self.round)
            ans = node.answer_question(q, self.round) if node.available else None
            if ans is not None and (len(ans.ids) == 0 or int(ans.counts[0, COL_N]) <= 0):
                ans = None
            self._answer_cache[key] = ans
        return self._answer_cache[key]

    def own_evidence(self, u: str, cell: int) -> tuple[int, int]:
        ans = self.answer(u, cell)
        if ans is None:
            return (0, 0)
        return (int(ans.counts[0, COL_N]), int(ans.counts[0, COL_DW]))

    # ---- executive knowledge base ----------------------------------------
    def root_claims(self, cell: int, label: int) -> list[Claim]:
        """Claims at the root about the cell: exact signature first, then same cell / other label,
        super-cells (their holders certainly hold the cell), then sub-cells (possible holders)."""
        root = self.nodes[self.root_id]
        qset = set(self.ci.decode(int(cell)))
        groups: dict[int, list[Claim]] = {0: [], 1: [], 2: [], 3: []}
        for (c_cell, c_label, _sign, _scope), c in root.claims.items():
            if c.status not in ("accepted", "contested", "superseded"):
                continue
            if c_cell == cell:
                groups[0 if c_label == label else 1].append(c)
                continue
            cset = set(self.ci.decode(int(c_cell)))
            if qset < cset:
                groups[2].append(c)
            elif cset < qset:
                groups[3].append(c)
        out: list[Claim] = []
        for g in (0, 1, 2, 3):
            out.extend(sorted(groups[g], key=lambda c: (-c.confidence, -c.n, c.claim_id)))
        return out

    # ---- cost model --------------------------------------------------------
    def tree_cost(self, asker: str, contacts: list[str], to_root: bool, via_root: set[str] | None = None) -> tuple[int, dict[str, int]]:
        """Question messages = distinct tree edges traversed (the climb to the root when `to_root`, plus the
        asker -> LCA -> leaf path of each contact; `via_root` contacts are resolved at the root).  Returns
        (messages, hops per contact)."""
        up = self.path_to_root(asker)
        pos = {u: i for i, u in enumerate(up)}
        edges: set[tuple[str, str]] = set()
        if to_root:
            edges.update(zip(up[:-1], up[1:]))
        hops: dict[str, int] = {}
        for c in contacts:
            if c == asker:
                continue
            pc = self.path_to_root(c)
            if via_root and c in via_root:
                j = len(pc) - 1
            else:
                j = next(i for i, u in enumerate(pc) if u in pos)
            i_up = pos[pc[j]]
            edges.update(zip(up[:i_up], up[1:i_up + 1]))
            edges.update((b, a) for a, b in zip(pc[:j], pc[1:j + 1]))
            hops[c] = i_up + j
        return len(edges), hops


# --------------------------------------------------------------------------
# Routers (hierarchy state only; `route_oracle` is the truth reference)
# --------------------------------------------------------------------------
def route_local_only(ctx: RoutingContext, q: QueryView) -> Route:
    return Route("local_only", [], 0, 0, {}, 0)


def route_global_only(ctx: RoutingContext, q: QueryView) -> Route:
    others = [u for u in ctx.leaf_ids if u != q.asker and ctx.available(u)]
    perm = ctx.rng.permutation(len(others))
    contacts = [others[int(i)] for i in perm]
    return Route("global_only", contacts, len(contacts), len(contacts) * ctx.q_bytes, {c: 1 for c in contacts}, 0,
                 notes={"broadcast": True})


def route_hybrid(ctx: RoutingContext, q: QueryView) -> Route:
    n_own, _ = ctx.own_evidence(q.asker, q.cell)
    if n_own >= ctx.n_min:
        return Route("hybrid", [], 0, 0, {}, 0, notes={"sufficient_locally": True})
    r = route_global_only(ctx, q)
    r.router = "hybrid"
    r.notes["sufficient_locally"] = False
    return r


def route_hierarchy_aware(ctx: RoutingContext, q: QueryView) -> Route:
    found, levels = ctx.walk_up(q.asker, q.cell)
    contacts = [f[0] for f in found]
    m, hops = ctx.tree_cost(q.asker, contacts, to_root=True)
    return Route("hierarchy_aware", contacts, m, m * ctx.q_bytes, hops, levels, n_est={f[0]: f[1] for f in found})


def route_provenance_aware(ctx: RoutingContext, q: QueryView) -> Route:
    prov: list[str] = []
    n_est: dict[str, int] = {}
    for c in ctx.root_claims(q.cell, q.label):
        units = list(c.lineage.contributing_units)
        if any(u.startswith("W") for u in units) and c.lineage.path:
            units = [c.lineage.path[0]] + [u for u in units if not u.startswith("W")]
        for u in units:
            if u not in ctx.nodes or not ctx.available(u):
                continue
            if ctx.is_leaf(u):
                if u != q.asker and u not in prov:
                    prov.append(u); n_est[u] = max(int(c.n), 1)
            else:
                for leaf, n_e, _dw in ctx.descend(u, q.cell):
                    if leaf != q.asker and leaf not in prov:
                        prov.append(leaf); n_est[leaf] = n_e
    found, levels = ctx.walk_up(q.asker, q.cell)
    rest = [f[0] for f in found if f[0] not in prov]
    n_est.update({f[0]: f[1] for f in found if f[0] not in n_est})
    contacts = prov + rest
    m, hops = ctx.tree_cost(q.asker, contacts, to_root=True, via_root=set(prov))
    return Route("provenance_aware", contacts, m, m * ctx.q_bytes, hops, max(levels, ctx.depth(q.asker)),
                 n_est=n_est, provenance_contacts=len(prov))


def route_lineage_aware(ctx: RoutingContext, q: QueryView) -> Route:
    found, levels = ctx.walk_up(q.asker, q.cell)
    cands: dict[str, tuple[int, int]] = {leaf: (n_e, dw) for leaf, n_e, dw, _lvl in found}
    chosen: list[str] = []
    teams: set[int] = set(); depts: set[int] = set(); regs: set[int] = set()
    dw_tot, is_cur = 0, 0.0
    while cands and is_cur < ctx.support_target:
        best, best_key = None, None
        for leaf, (n_e, dw) in cands.items():
            t, d, r = ctx.membership(leaf)
            is_new = ctx.independent_support(dw_tot + dw, len(teams | {t}), len(depts | {d}), len(regs | {r}))
            key = (is_new, n_e, leaf)
            if best_key is None or key[:2] > best_key[:2] or (key[:2] == best_key[:2] and leaf < best_key[2]):
                best, best_key = leaf, key
        assert best is not None
        n_e, dw = cands.pop(best)
        t, d, r = ctx.membership(best)
        teams.add(t); depts.add(d); regs.add(r); dw_tot += dw
        is_cur = ctx.independent_support(dw_tot, len(teams), len(depts), len(regs))
        chosen.append(best)
    m, hops = ctx.tree_cost(q.asker, chosen, to_root=True)
    return Route("lineage_aware", chosen, m, m * ctx.q_bytes, hops, levels,
                 n_est={leaf: n_e for leaf, n_e, _dw, _l in found if leaf in chosen},
                 notes={"is_estimated": is_cur, "candidates": len(found), "target_reached": is_cur >= ctx.support_target})


def route_oracle(ctx: RoutingContext, q: Query) -> Route:
    """Reference router: contacts exactly the true holders (largest first).  Uses truth."""
    contacts = [u for u, _n in sorted(q.holders.items(), key=lambda t: (-t[1], t[0])) if u != q.asker and ctx.available(u)]
    return Route("oracle", contacts, len(contacts), len(contacts) * ctx.q_bytes, {c: 1 for c in contacts}, 0,
                 n_est={c: q.holders[c] for c in contacts})


ROUTERS: dict[str, tuple[Callable[..., Route], bool]] = {   # name -> (fn, uses_truth)
    "local_only": (route_local_only, False),
    "global_only": (route_global_only, False),
    "hybrid": (route_hybrid, False),
    "hierarchy_aware": (route_hierarchy_aware, False),
    "provenance_aware": (route_provenance_aware, False),
    "lineage_aware": (route_lineage_aware, False),
    "oracle": (route_oracle, True),
}


def run_router(name: str, ctx: RoutingContext, q: Query) -> Route:
    fn, uses_truth = ROUTERS[name]
    return fn(ctx, q if uses_truth else q.view())


# ==========================================================================
# TRUTH SIDE: workload generation and scoring (the only place `World` is read)
# ==========================================================================
def leaf_units_of_records(hier: Hierarchy, world) -> tuple[list[str], np.ndarray]:
    """Leaf unit id list and, per record, the index of the leaf whose local store retains it
    (mirrors `Hierarchy._ingest_workers`: teams, squads for 7 layers, the executive for 1 layer)."""
    org = world.org
    leaf_layer = hier.layer_names[0]
    leaf_ids = sorted(n.unit_id for n in hier.by_layer.get(leaf_layer, []))
    index = {u: i for i, u in enumerate(leaf_ids)}
    team = np.asarray(world.team)
    if hier.layers_n == 1:
        names = np.array(["EXEC"] * world.n)
    elif hier.layers_n >= 7:
        wpt = max(1, org.n_workers // max(org.n_teams, 1))
        squad = ((np.asarray(world.worker) - team * wpt) * 4 // max(wpt, 1)).clip(0, 3)
        names = np.array([f"S{int(t):04d}.{int(s)}" for t, s in zip(team, squad)])
    else:
        names = np.array([org.team_ids[int(t)] for t in team])
    idx = np.array([index.get(x, -1) for x in names], dtype=np.int64)
    return leaf_ids, idx


@dataclass
class Workload:
    queries: list[Query]
    leaf_ids: list[str]
    leaf_idx: np.ndarray          # per record
    avg_record_bytes: float
    n_leaves: int
    summary: dict[str, Any]
    _world: Any = None

    def independent_support_true(self, cell: int, units: list[str], rho: tuple[float, float, float]) -> float:
        """Ground-truth independent support of the matching, honest, non-copied records held by `units`."""
        w = self._world
        if not units:
            return 0.0
        from .world import cell_match_mask
        idx = {u: i for i, u in enumerate(self.leaf_ids)}
        sel = np.isin(self.leaf_idx, [idx[u] for u in units if u in idx])
        m = cell_match_mask(w.attrs, cell) & ~w.is_copy & (w.attack_tag == 0) & sel
        if not m.any():
            return 0.0
        ws = np.unique(w.worker[m])
        org = w.org
        dw = len(ws); dt = len(np.unique(org.worker_team[ws]))
        dd = len(np.unique(org.worker_department[ws])); dr = len(np.unique(org.worker_region[ws]))
        rho_t, rho_d, rho_r = rho
        return float(dr + rho_r * (dd - dr) + rho_d * (dt - dd) + rho_t * (dw - dt))


def _holders(world, leaf_ids: list[str], leaf_idx: np.ndarray, cell: int) -> dict[str, int]:
    from .world import cell_match_mask
    m = cell_match_mask(world.attrs, cell) & ~world.is_copy & (leaf_idx >= 0)
    counts = np.bincount(leaf_idx[m], minlength=len(leaf_ids))
    return {leaf_ids[i]: int(counts[i]) for i in np.flatnonzero(counts > 0)}


def generate_workload(world, hier: Hierarchy, cfg: dict[str, Any] | None = None, seed: int = 0, **overrides: Any) -> Workload:
    """Queries from (a) ground-truth effect signatures (deduplicated by (cell, label)) and (b) random cells
    drawn from real records (so every query has >= 1 holder).  The asker is a holder with probability
    `asker_holder_prob`, else a uniformly random leaf unit."""
    rc = routing_config(cfg if cfg is not None else world.cfg)
    rc.update({k: v for k, v in overrides.items() if v is not None})
    rng = np.random.default_rng(seed * 104729 + 7)
    ci = cell_index()
    leaf_ids, leaf_idx = leaf_units_of_records(hier, world)
    queries: list[Query] = []
    seen: set[tuple[int, int]] = set()

    def make(cell: int, label: int, source: str, kind: str, effect_id: str | None) -> Query | None:
        holders = _holders(world, leaf_ids, leaf_idx, cell)
        if not holders:
            return None
        hl = sorted(holders)
        if rng.random() < float(rc["asker_holder_prob"]):
            asker = hl[int(rng.integers(0, len(hl)))]
        else:
            asker = leaf_ids[int(rng.integers(0, len(leaf_ids)))]
        order = int(ci.order_of(np.array([cell]))[0])
        return Query(qid=f"{source[0]}{len(queries):04d}", cell=int(cell), label=int(label), order=order, source=source,
                     kind=kind, effect_id=effect_id, asker=asker, holders=holders, n_total=int(sum(holders.values())))

    # (a) effect-derived queries
    eff = [e for e in world.effects if e.kind in EFFECT_QUERY_KINDS]
    order_ = rng.permutation(len(eff))
    max_eff = int(rc["max_effect_queries"])
    for i in order_:
        e = eff[int(i)]
        key = (int(e.cell), int(e.label))
        if key in seen:
            continue
        seen.add(key)
        q = make(int(e.cell), int(e.label), "effect", e.kind, e.effect_id)
        if q is not None:
            queries.append(q)
        if sum(1 for q in queries if q.source == "effect") >= max_eff:
            break
    # (b) random cells from real records
    orders = [int(o) for o in rc["random_orders"]]
    n_random = int(rc["n_random_queries"])
    attempts = 0
    while sum(1 for q in queries if q.source == "random") < n_random and attempts < 20 * max(n_random, 1):
        attempts += 1
        i = int(rng.integers(0, world.n))
        k = int(orders[int(rng.integers(0, len(orders)))])
        attrs = sorted(int(a) for a in rng.choice(N_ATTR, size=k, replace=False))
        cell = ci.encode([(a, int(world.attrs[i, a])) for a in attrs])
        label = int(rng.integers(0, N_LABELS))
        if (cell, label) in seen:
            continue
        seen.add((cell, label))
        q = make(cell, label, "random", "random", None)
        if q is not None:
            queries.append(q)
    from .evaluate import raw_record_bytes
    avg_bytes = raw_record_bytes(world) / max(world.n, 1)
    kinds: dict[str, int] = {}
    for q in queries:
        kinds[q.kind] = kinds.get(q.kind, 0) + 1
    summary = {"n_queries": len(queries), "by_kind": kinds, "n_leaves": len(leaf_ids), "leaf_layer": hier.layer_names[0],
               "mean_holders": float(np.mean([len(q.holders) for q in queries])) if queries else float("nan"),
               "mean_holder_fraction": float(np.mean([len(q.holders) / len(leaf_ids) for q in queries])) if queries else float("nan"),
               "asker_is_holder_fraction": float(np.mean([q.asker in q.holders for q in queries])) if queries else float("nan"),
               "config": {k: v for k, v in rc.items()}}
    return Workload(queries=queries, leaf_ids=leaf_ids, leaf_idx=leaf_idx, avg_record_bytes=float(avg_bytes),
                    n_leaves=len(leaf_ids), summary=summary, _world=world)


def measure_route(ctx: RoutingContext, wl: Workload, q: Query, route: Route, budget: int, strong_min: int | None = None) -> dict[str, Any]:
    """Score one route against the true holders; answer bytes come from the contacted units' local stores."""
    strong_min = int(ctx.policy.min_cell_n if strong_min is None else strong_min)
    holders = q.holders
    asker = q.asker
    local_hit = asker in holders
    ext_holders = set(holders) - {asker}
    contacts = route.contacts
    hits = [c for c in contacts if c in holders]
    n_h = max(len(holders), 1)
    n_ext = len(contacts)

    def recall_at(b: int) -> float:
        return (int(local_hit) + sum(1 for c in contacts[:b] if c in holders)) / n_h

    m: dict[str, Any] = {}
    m["recall_at_budget"] = recall_at(int(budget))
    m["recall_at_R"] = recall_at(len(holders))
    m["recall_full"] = recall_at(n_ext)
    m["evidence_recall"] = (holders.get(asker, 0) + sum(holders[c] for c in hits)) / max(q.n_total, 1)
    # strong holders: units with >= strong_min matching records (what a k-anonymous plane may legitimately index)
    strong = {u for u, n in holders.items() if n >= strong_min}
    if strong:
        m["recall_strong_at_budget"] = (int(asker in strong) + sum(1 for c in contacts[:int(budget)] if c in strong)) / len(strong)
        m["recall_strong_full"] = (int(asker in strong) + sum(1 for c in contacts if c in strong)) / len(strong)
    else:
        m["recall_strong_at_budget"] = float("nan"); m["recall_strong_full"] = float("nan")
    m["n_strong_holders"] = len(strong)
    m["precision"] = len(hits) / n_ext if n_ext else float("nan")
    m["unnecessary_escalation_rate"] = (n_ext - len(hits)) / n_ext if n_ext else 0.0
    m["n_contacted"] = n_ext
    m["n_unnecessary"] = n_ext - len(hits)
    m["escalated"] = float(n_ext > 0)
    if ext_holders:
        m["correct_destination"] = float(n_ext > 0 and contacts[0] in holders)
        m["cross_team_discovery"] = len(set(hits)) / len(ext_holders)
    else:
        m["correct_destination"] = float(n_ext == 0)
        m["cross_team_discovery"] = float("nan")
    # answers (hierarchy state): one-cell sketch or an empty reply
    bytes_ans, answered = 0, 0
    for c in contacts:
        ans = ctx.answer(c, q.cell)
        if ans is None:
            bytes_ans += ctx.empty_answer_bytes
        else:
            bytes_ans += ans.wire_bytes(); answered += 1
    m["messages_question"] = int(route.messages_question)
    m["messages_answer"] = n_ext
    m["messages"] = int(route.messages_question) + n_ext
    m["bytes_question"] = int(route.bytes_question)
    m["bytes_answer"] = int(bytes_ans)
    m["bytes"] = int(route.bytes_question) + int(bytes_ans)
    m["latency_hops"] = (max(route.hops.values()) if route.hops else 0) + (1 if n_ext else 0)
    m["hops_mean"] = float(np.mean(list(route.hops.values()))) if route.hops else 0.0
    m["escalation_levels"] = int(route.escalation_levels)
    m["answered_units"] = answered
    m["answered_fraction"] = answered / n_ext if n_ext else float("nan")
    raw_eq = sum(holders[c] for c in hits) * wl.avg_record_bytes
    m["raw_records_equivalent"] = int(sum(holders[c] for c in hits))
    m["raw_bytes_equivalent"] = float(raw_eq)
    m["sketch_bytes"] = int(bytes_ans)
    m["private_data_ratio"] = raw_eq / bytes_ans if bytes_ans > 0 else float("nan")
    is_true = wl.independent_support_true(q.cell, hits, ctx.rho)
    m["independent_support_true"] = is_true
    m["support_sufficient"] = float(is_true >= ctx.policy.support_min)
    m["provenance_contacts"] = int(route.provenance_contacts)
    m["n_holders"] = len(holders)
    m["n_ext_holders"] = len(ext_holders)
    m["local_hit"] = float(local_hit)
    return m


_MEAN_KEYS = ("recall_at_budget", "recall_at_R", "recall_full", "evidence_recall", "recall_strong_at_budget", "recall_strong_full",
              "n_strong_holders", "precision", "unnecessary_escalation_rate",
              "n_contacted", "n_unnecessary", "escalated", "correct_destination", "cross_team_discovery", "messages_question",
              "messages_answer", "messages", "bytes_question", "bytes_answer", "bytes", "latency_hops", "hops_mean",
              "escalation_levels", "answered_units", "answered_fraction", "raw_records_equivalent", "raw_bytes_equivalent",
              "sketch_bytes", "private_data_ratio", "independent_support_true", "support_sufficient", "provenance_contacts",
              "n_holders", "n_ext_holders", "local_hit")
_BY_KIND_KEYS = ("recall_at_budget", "recall_full", "recall_strong_full", "precision", "unnecessary_escalation_rate",
                 "cross_team_discovery", "correct_destination", "bytes", "messages", "n_holders")


def aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...] = _MEAN_KEYS) -> dict[str, Any]:
    out: dict[str, Any] = {"n_queries": len(rows)}
    for k in keys:
        vals = np.array([r[k] for r in rows if r.get(k) is not None], dtype=float)
        vals = vals[~np.isnan(vals)]
        out[k] = float(vals.mean()) if len(vals) else float("nan")
    return out


def evaluate_routers(hier: Hierarchy, wl: Workload, routers: tuple[str, ...] | list[str] = DEFAULT_ROUTERS, budget: int | None = None,
                     cfg: dict[str, Any] | None = None, seed: int = 0, ctx: RoutingContext | None = None
                     ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Run every router on the workload.  Returns ({router: metrics}, per-query rows)."""
    rc = routing_config(cfg)
    budget = int(rc["contact_budget"] if budget is None else budget)
    if ctx is None:
        ctx = RoutingContext(hier, plane_fallback=int(rc["plane_fallback"]), support_target=rc["lineage_support_target"], seed=seed)
    strong_min = rc.get("strong_holder_min")
    metrics: dict[str, dict[str, Any]] = {}
    detail: list[dict[str, Any]] = []
    for name in routers:
        rows = []
        for q in wl.queries:
            route = run_router(name, ctx, q)
            m = measure_route(ctx, wl, q, route, budget, strong_min=strong_min)
            m.update({"router": name, "qid": q.qid, "kind": q.kind, "source": q.source, "order": q.order, "asker": q.asker,
                      "cell": q.cell, "label": q.label, "effect_id": q.effect_id, "contacts": list(route.contacts)})
            rows.append(m)
        agg = aggregate(rows)
        agg["contact_budget"] = budget
        agg["by_kind"] = {}
        for kind in sorted({r["kind"] for r in rows}):
            agg["by_kind"][kind] = aggregate([r for r in rows if r["kind"] == kind], _BY_KIND_KEYS)
        agg["by_source"] = {src: aggregate([r for r in rows if r["source"] == src], _BY_KIND_KEYS)
                            for src in sorted({r["source"] for r in rows})}
        agg["by_order"] = {str(o): aggregate([r for r in rows if r["order"] == o],
                                             ("recall_at_budget", "recall_full", "recall_strong_full", "precision", "n_holders", "bytes"))
                           for o in sorted({r["order"] for r in rows})}
        metrics[name] = agg
        detail.extend(rows)
    metrics["_workload"] = dict(wl.summary)
    metrics["_workload"]["plane_lookups"] = ctx.plane_lookups
    return metrics, detail


def overbroadening_summary(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Does global broadcast buy recall with precision?  Compare precision / bytes against the plane router."""
    g = metrics.get("global_only"); h = metrics.get("hierarchy_aware"); o = metrics.get("oracle")
    out: dict[str, Any] = {}
    if g and h:
        out["precision_global"] = g["precision"]; out["precision_hierarchy_aware"] = h["precision"]
        out["precision_ratio_global_vs_hierarchy"] = g["precision"] / h["precision"] if h["precision"] and not math.isnan(h["precision"]) else float("nan")
        out["recall_full_global"] = g["recall_full"]; out["recall_full_hierarchy_aware"] = h["recall_full"]
        out["recall_at_budget_global"] = g["recall_at_budget"]; out["recall_at_budget_hierarchy_aware"] = h["recall_at_budget"]
        out["bytes_ratio_global_vs_hierarchy"] = g["bytes"] / h["bytes"] if h["bytes"] else float("nan")
        out["unnecessary_global"] = g["unnecessary_escalation_rate"]; out["unnecessary_hierarchy_aware"] = h["unnecessary_escalation_rate"]
    if g and o:
        out["precision_drop_global_vs_oracle"] = o["precision"] - g["precision"]
    return out
