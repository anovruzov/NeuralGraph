"""Failure injection and recovery scoring for the hierarchy (MODULE_SPEC Task F).

A `FailurePlan` acts only on *hierarchy state* (`node.available`, `node.inbox`,
`hier.delay`, node stores) and on observation batches (`record_filter`, chained
with the attack hook).  Ground truth (`world.effects`, population truth) is
read only by the scoring functions (`compare_runs`) — never by the plan.

Kinds and their exact semantics
-------------------------------
random_user
    A fraction `rate` of workers disappears for the failure window: their
    records are removed from the batches (`record_filter`).  Records produced
    while the user is gone are lost (the user left; nothing is replayed).
random_team / department / region
    `ceil(rate x n_units)` units of that layer *crash*: `available=False` and the
    node's local state is wiped (pooled sketch, claims, local record store,
    inbox).  Worker batches addressed to a dead team are lost (trunk drops
    them); artifacts children send to a dead parent are dropped by the trunk.
    The parent keeps everything the dead node promoted before (pooled counts,
    inherited claims with dangling lineage).  On recovery the node comes back
    empty and its children *resync*: each child's `sent` sketch is reset to what
    the recovered node still holds from it, so the next promotion carries
    exactly the missing delta (a fresh replica catching up from its children).
    A recovered internal node re-promotes only what its own parent lacks.
targeted_high_support
    Same crash semantics, targets = the `layer` (default team) nodes with the
    most accepted own-scope claims at the failure round.
targeted_lineage
    Same crash semantics, targets = teams named most often (directly, through
    their workers, or through an ancestor) in the root's accepted claims'
    `lineage.contributing_units` at the failure round.
stale_replica
    A unit freezes: `available=False`, state intact, previously promoted
    artifacts remain in the parent.  Worker batches addressed to a frozen team
    are buffered by `record_filter` and replayed at recovery; children resync at
    recovery; the unit then promotes its accumulated delta.
partition
    A contiguous block of `ceil(rate x n_teams)` teams is cut off from the rest
    of the organisation: `hier.delay[unit] = 10**6`, so their artifacts queue in
    the parent's inbox.  At recovery the queued artifacts are re-stamped as due
    and delivered in order.  (Questions to partitioned children are still
    answered synchronously by the trunk — a known limitation.)
delayed_sync
    `ceil(rate x n_teams)` teams sync with `delay_rounds` rounds of latency
    during the window.

Timing: the failure is in effect during rounds `[at_round, recover_round)`;
`apply(hier, r)` (called by the runner after round r) arms it for round r+1.
Node-level kinds therefore need `at_round >= 1`; `random_user` works from
round 0 because `record_filter` checks `batch.round` directly.  Random target
sets are nested across rates for the same seed (a fixed permutation is cut at
`ceil(rate x n)`), so sweeps are monotone and paired.
"""

from __future__ import annotations

import math
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .agents import ObservationBatch
from .evaluate import ClaimRecord, scopes_nested
from .sketch import COL_N, Sketch
from .world import Effect, World

KINDS: tuple[str, ...] = (
    "random_user", "random_team", "department", "region", "targeted_high_support", "targeted_lineage",
    "stale_replica", "partition", "delayed_sync",
)
CRASH_KINDS = {"random_team", "department", "region", "targeted_high_support", "targeted_lineage"}
NODE_KINDS = CRASH_KINDS | {"stale_replica"}
LINK_KINDS = {"partition", "delayed_sync"}
PARTITION_DELAY = 10**6
DEFAULT_LAYER = {"random_team": "team", "department": "department", "region": "region", "targeted_high_support": "team",
                 "targeted_lineage": "team", "stale_replica": "team", "partition": "team", "delayed_sync": "team"}
_LAYER_FALLBACK = ("region", "division", "department", "team", "squad")

Hook = Callable[[ObservationBatch, Any], ObservationBatch]


def _n_of(rate: float, n: int) -> int:
    """ceil(rate x n) with a float guard, at least 1 when n > 0, never more than n."""
    if n <= 0:
        return 0
    return int(min(n, max(1, math.ceil(rate * n - 1e-9))))


def chain_hooks(*hooks: Hook | None) -> Hook | None:
    """Compose batch hooks left to right (attack hooks first, then failure filters)."""
    hs = [h for h in hooks if h is not None]
    if not hs:
        return None
    if len(hs) == 1:
        return hs[0]

    def hook(batch: ObservationBatch, slm: Any = None) -> ObservationBatch:
        for h in hs:
            batch = h(batch, slm)
        return batch
    return hook


# --------------------------------------------------------------------------
# batch helpers
# --------------------------------------------------------------------------
def _filter_batch(b: ObservationBatch, keep: np.ndarray, dropped_workers: set[int] | None = None) -> ObservationBatch:
    n = len(b)
    if n == 0 or bool(keep.all()):
        return b
    kept = int(keep.sum())
    quotes = [q for q, k in zip(b.quotes, keep) if k] if len(b.quotes) == n else list(b.quotes)
    inj = list(b.injected_claims)
    if dropped_workers:
        inj = [d for d in inj if int(d.get("worker", -1)) not in dropped_workers]
    return ObservationBatch(
        unit_id=b.unit_id, round=b.round, idx=b.idx[keep], attrs=b.attrs[keep], present=b.present[keep],
        labels=b.labels[keep], worker=b.worker[keep], fingerprint=b.fingerprint[keep], confidence=b.confidence[keep],
        signature_ok=b.signature_ok[keep], is_attack=b.is_attack[keep], quotes=quotes, injected_claims=inj,
        model_calls=int(round(b.model_calls * kept / n)), tokens_in=int(round(b.tokens_in * kept / n)),
    )


def _concat_batches(parts: list[ObservationBatch]) -> ObservationBatch:
    parts = [p for p in parts if len(p)] or parts[-1:]
    if len(parts) == 1:
        return parts[0]
    last = parts[-1]
    per_record_quotes = all(len(p.quotes) == len(p) for p in parts)
    quotes = [q for p in parts for q in p.quotes] if per_record_quotes else [q for p in parts for q in p.quotes]
    cat = lambda name: np.concatenate([getattr(p, name) for p in parts])  # noqa: E731
    return ObservationBatch(
        unit_id=last.unit_id, round=last.round, idx=cat("idx"), attrs=cat("attrs"), present=cat("present"),
        labels=cat("labels"), worker=cat("worker"), fingerprint=cat("fingerprint"), confidence=cat("confidence"),
        signature_ok=cat("signature_ok"), is_attack=cat("is_attack"), quotes=quotes,
        injected_claims=[d for p in parts for d in p.injected_claims],
        model_calls=sum(p.model_calls for p in parts), tokens_in=sum(p.tokens_in for p in parts),
    )


def _team_index(unit_id: str) -> int:
    try:
        return int(unit_id[1:5]) if unit_id.startswith("S") else int(unit_id[1:])
    except ValueError:
        return -1


# --------------------------------------------------------------------------
# organisation helpers (topology only, no truth)
# --------------------------------------------------------------------------
def workers_under(org, unit_id: str) -> np.ndarray:
    """Boolean mask over workers in the subtree of a unit id (W/S/T/D/V/R/EXEC)."""
    n = org.n_workers
    if unit_id == "EXEC":
        return np.ones(n, dtype=bool)
    tag, rest = unit_id[0], unit_id[1:]
    if tag == "W" and rest.isdigit():
        m = np.zeros(n, dtype=bool)
        w = int(rest)
        if 0 <= w < n:
            m[w] = True
        return m
    if tag == "T" and rest.isdigit():
        return org.worker_team == int(rest)
    if tag == "D" and rest.isdigit():
        return org.worker_department == int(rest)
    if tag == "R" and rest.isdigit():
        return org.worker_region == int(rest)
    if tag == "V" and rest.isdigit():
        v = int(rest)
        return np.isin(org.worker_department, [2 * v, 2 * v + 1])
    if tag == "S" and "." in rest:
        t, s = rest.split(".")
        t, s = int(t), int(s)
        wpt = max(1, org.n_workers // max(org.n_teams, 1))
        w = np.arange(n)
        squad = ((w - t * wpt) * 4 // max(wpt, 1)).clip(0, 3)
        return (org.worker_team == t) & (squad == s)
    return np.zeros(n, dtype=bool)


def unit_ancestors(org, unit_id: str) -> set[str]:
    """The unit and every organisational ancestor (5-layer ids; squads/divisions map through their team/departments)."""
    out = {unit_id, "EXEC"}
    m = workers_under(org, unit_id)
    if not m.any():
        return out
    ws = np.flatnonzero(m)
    teams = np.unique(org.worker_team[ws])
    depts = np.unique(org.worker_department[ws])
    regs = np.unique(org.worker_region[ws])
    if len(teams) == 1:
        out.add(org.team_ids[int(teams[0])])
    if len(depts) == 1:
        out.add(org.department_ids[int(depts[0])])
    if len(regs) == 1:
        out.add(org.region_ids[int(regs[0])])
    return out


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------
class FailurePlan:
    """Failure of `kind` at `rate`, active during rounds [at_round, recover_round)."""

    def __init__(self, kind: str, rate: float, seed: int, at_round: int, recover_round: int | None = None, *,
                 layer: str | None = None, delay_rounds: int = 3, org=None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown failure kind {kind!r}; known: {KINDS}")
        if not 0.0 <= float(rate) <= 1.0:
            raise ValueError("rate must be in [0, 1]")
        if recover_round is not None and int(recover_round) <= int(at_round):
            raise ValueError("recover_round must be > at_round")
        self.kind = kind
        self.rate = float(rate)
        self.seed = int(seed)
        self.at_round = int(at_round)
        self.recover_round = None if recover_round is None else int(recover_round)
        self.layer = layer or DEFAULT_LAYER.get(kind, "team")
        self.delay_rounds = int(delay_rounds)
        self.rng = np.random.default_rng([self.seed, 0xFA11, KINDS.index(kind)])
        self.hier = None
        self.active = False
        self.done = False
        self.activated_round: int | None = None
        self.recovered_round: int | None = None
        self.failed_units: list[str] = []
        self.wiped_units: list[str] = []
        self.failed_workers: set[int] = set()
        self._failed_worker_arr = np.zeros(0, dtype=np.int64)
        self._receiving_teams: set[int] = set()
        self._buffer: dict[int, list[ObservationBatch]] = {}
        self.records_lost = 0
        self.records_buffered = 0
        self.records_replayed = 0
        self.worker_fraction_affected = 0.0
        self.n_units_failed = 0
        self.target_scores: dict[str, float] = {}
        self.log: list[dict[str, Any]] = []
        if org is not None:
            self._prepare(org)

    # ---- preparation -----------------------------------------------------
    def _prepare(self, org) -> None:
        if self.kind == "random_user" and not len(self._failed_worker_arr):
            perm = self.rng.permutation(org.n_workers)
            k = _n_of(self.rate, org.n_workers)
            self._failed_worker_arr = np.sort(perm[:k]).astype(np.int64)
            self.failed_workers = set(int(w) for w in self._failed_worker_arr)
            self.worker_fraction_affected = k / max(org.n_workers, 1)
            self.n_units_failed = k

    def attach(self, hier) -> None:
        self.hier = hier
        self._prepare(hier.org)

    # ---- window ----------------------------------------------------------
    def in_window(self, round_: int) -> bool:
        return self.at_round <= round_ and (self.recover_round is None or round_ < self.recover_round)

    def unreachable_units(self) -> set[str]:
        """Units whose evidence cannot be reached from the root while the plan is active."""
        if not self.active:
            return set()
        out = set(self.failed_units) if self.kind != "delayed_sync" else set()
        out |= {f"W{w:06d}" for w in self.failed_workers}
        return out

    # ---- runner entry point ---------------------------------------------
    def apply(self, hier, round_: int) -> None:
        """Called after round `round_`; arms / disarms the failure for round `round_ + 1`."""
        if not hasattr(hier, "nodes"):
            return
        if self.hier is None:
            self.attach(hier)
        nxt = round_ + 1
        if not self.active and not self.done and nxt >= self.at_round and (self.recover_round is None or nxt < self.recover_round):
            self.activate(hier, nxt)
        elif self.active and self.recover_round is not None and nxt >= self.recover_round:
            self.recover(hier, nxt)

    # ---- target selection ------------------------------------------------
    def _layer_units(self, hier, layer: str) -> tuple[str, list[str]]:
        cands = [layer] + [l for l in _LAYER_FALLBACK if l != layer]
        for l in cands:
            nodes = [n for n in hier.by_layer.get(l, []) if n.parent_id is not None]
            if nodes:
                return l, sorted(n.unit_id for n in nodes)
        return layer, []

    def _select_units(self, hier, round_: int) -> list[str]:
        kind = self.kind
        layer, units = self._layer_units(hier, self.layer)
        self.layer = layer
        if not units:
            return []
        k = _n_of(self.rate, len(units))
        if kind in ("random_team", "department", "region", "stale_replica", "delayed_sync"):
            perm = self.rng.permutation(len(units))
            return [units[i] for i in perm[:k]]
        if kind == "partition":
            start = int(self.rng.integers(0, len(units) - k + 1))
            return units[start:start + k]
        if kind == "targeted_high_support":
            score = {}
            for uid in units:
                node = hier.nodes[uid]
                score[uid] = float(sum(1 for (c, l, s, scope), cl in node.claims.items()
                                       if scope == uid and cl.status == "accepted"))
        elif kind == "targeted_lineage":
            root = hier.root_node()
            named: Counter = Counter()
            for c in root.accepted_claims():
                named.update(c.lineage.contributing_units)
            score = {uid: 0.0 for uid in units}
            org = hier.org
            for u, cnt in named.items():
                ws = np.flatnonzero(workers_under(org, u))
                if len(ws) == 0:
                    continue
                if layer == "team":
                    members = [org.team_ids[int(t)] for t in np.unique(org.worker_team[ws])]
                elif layer == "department":
                    members = [org.department_ids[int(d)] for d in np.unique(org.worker_department[ws])]
                elif layer == "region":
                    members = [org.region_ids[int(r)] for r in np.unique(org.worker_region[ws])]
                else:
                    members = [m for m in units if (workers_under(org, m) & workers_under(org, u)).any()]
                members = [m for m in members if m in score]
                for m in members:
                    score[m] += cnt / len(members)
        else:
            raise ValueError(kind)
        self.target_scores = dict(score)
        tie = self.rng.permutation(len(units))
        order = sorted(range(len(units)), key=lambda i: (-score[units[i]], tie[i]))
        return [units[i] for i in order[:k]]

    # ---- activation ------------------------------------------------------
    def activate(self, hier, round_: int) -> None:
        if self.active:
            return
        self.attach(hier)
        self.active = True
        self.activated_round = round_
        org = hier.org
        if self.kind == "random_user":
            self.log.append({"round": round_, "event": "activate", "workers": int(len(self._failed_worker_arr))})
            return
        units = self._select_units(hier, round_)
        self.failed_units = list(units)
        self.n_units_failed = len(units)
        affected = np.zeros(org.n_workers, dtype=bool)
        for uid in units:
            affected |= workers_under(org, uid)
        self.worker_fraction_affected = float(affected.mean()) if org.n_workers else 0.0
        failed_set = set(units)
        self._receiving_teams = {t for t, nid in hier.team_node_of.items() if nid in failed_set}
        if self.kind in CRASH_KINDS:
            for uid in units:
                node = hier.nodes[uid]
                node.available = False
                _wipe_node(node)
            self.wiped_units = list(units)
        elif self.kind == "stale_replica":
            for uid in units:
                hier.nodes[uid].available = False
        elif self.kind == "partition":
            for uid in units:
                hier.delay[uid] = PARTITION_DELAY
        elif self.kind == "delayed_sync":
            for uid in units:
                hier.delay[uid] = max(1, self.delay_rounds)
        hier.unavailable = set(getattr(hier, "unavailable", set())) | (failed_set if self.kind in NODE_KINDS else set())
        self.log.append({"round": round_, "event": "activate", "units": list(units),
                         "worker_fraction": self.worker_fraction_affected})

    # ---- recovery --------------------------------------------------------
    def recover(self, hier, round_: int) -> None:
        if not self.active:
            return
        self.active = False
        self.done = True
        self.recovered_round = round_
        failed_set = set(self.failed_units)
        if self.kind in NODE_KINDS:
            for uid in self.failed_units:
                node = hier.nodes[uid]
                node.available = True
                is_leaf = not any(cid in hier.nodes for cid in node.children)
                if uid in self.wiped_units and not is_leaf and node.parent_id in hier.nodes:
                    # the rebuilt node must only re-promote what its parent does not already hold
                    prev = hier.nodes[node.parent_id].received_from.get(uid)
                    node.sent = _sketch_copy(prev, uid, node.layer, node.policy.sketch_order)
                _resync_children(hier, node)
            hier.unavailable = set(getattr(hier, "unavailable", set())) - failed_set
        elif self.kind in LINK_KINDS:
            for uid in self.failed_units:
                hier.delay.pop(uid, None)
                node = hier.nodes[uid]
                parent = hier.nodes.get(node.parent_id) if node.parent_id else None
                if parent is None or not parent.inbox:
                    continue
                items = [((round_ if art.sender_id in failed_set else due), art) for due, art in parent.inbox]
                items.sort(key=lambda x: x[0])
                parent.inbox = deque(items)
        self.log.append({"round": round_, "event": "recover", "units": list(self.failed_units)})

    # ---- observation-batch hook -----------------------------------------
    def record_filter(self, batch: ObservationBatch, slm: Any = None) -> ObservationBatch:
        """Chainable batch hook: user loss, accounting of records addressed to dead nodes,
        buffering/replay for stale replicas.  Safe to call without `apply` for `random_user`."""
        r = int(batch.round)
        t = _team_index(batch.unit_id)
        if self.kind == "random_user":
            if self.in_window(r) and len(self._failed_worker_arr) and len(batch):
                keep = ~np.isin(batch.worker, self._failed_worker_arr)
                n_drop = int((~keep).sum())
                if n_drop:
                    self.records_lost += n_drop
                    batch = _filter_batch(batch, keep, self.failed_workers)
            return batch
        if self.active and t in self._receiving_teams:
            if self.kind == "stale_replica":
                self._buffer.setdefault(t, []).append(batch)
                self.records_buffered += len(batch)
                return _filter_batch(batch, np.zeros(len(batch), dtype=bool))
            self.records_lost += len(batch)
            return batch
        if self.done and self._buffer.get(t):
            parts = self._buffer.pop(t) + [batch]
            self.records_replayed += sum(len(p) for p in parts[:-1])
            return _concat_batches(parts)
        return batch

    # ---- reporting -------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "rate": self.rate, "seed": self.seed, "at_round": self.at_round,
            "recover_round": self.recover_round, "layer": self.layer, "delay_rounds": self.delay_rounds,
            "activated_round": self.activated_round, "recovered_round": self.recovered_round,
            "failed_units": list(self.failed_units), "n_failed_workers": int(len(self._failed_worker_arr)),
            "n_units_failed": int(self.n_units_failed), "worker_fraction_affected": float(self.worker_fraction_affected),
            "records_lost": int(self.records_lost), "records_buffered": int(self.records_buffered),
            "records_replayed": int(self.records_replayed), "log": list(self.log),
        }


# --------------------------------------------------------------------------
# node state manipulation
# --------------------------------------------------------------------------
def _sketch_copy(prev: Sketch | None, uid: str, layer: str, max_order: int) -> Sketch:
    if prev is None or len(prev.ids) == 0:
        return Sketch(uid, layer, 0, max_order=max_order)
    return Sketch(uid, layer, prev.round, prev.ids.copy(), prev.counts.copy(), max_order)


def _wipe_node(node) -> None:
    """Crash semantics: the node's local state is gone (its parent keeps what it already received)."""
    p = node.policy
    node.cumulative = Sketch(node.unit_id, node.layer, 0, max_order=p.sketch_order)
    node.sent = Sketch(node.unit_id, node.layer, 0, max_order=p.sketch_order)
    node.recent = OrderedDict()
    node.received_from = {}
    node.received_claims = {}
    node.claims = {}
    node.conflicts = {}
    node.quarantined = []
    node.questions = {}
    node.pending_answers = []
    node.inbox = deque()
    node.obs_attrs = []; node.obs_labels = []; node.obs_present = []; node.obs_worker = []
    node.obs_fp = []; node.obs_idx = []; node.obs_round = []
    node.seen_fingerprints = set()
    node.sent_claim_version = {}
    node._answer_cells = None
    node.last_synth_round = -1


def _resync_children(hier, node) -> None:
    """Children re-send what the recovered node lacks: reset each child's `sent` to the parent's holdings."""
    for cid in node.children:
        child = hier.nodes.get(cid)
        if child is None:
            continue
        child.sent = _sketch_copy(node.received_from.get(cid), cid, child.layer, child.policy.sketch_order)
        child.sent_claim_version = {}


# --------------------------------------------------------------------------
# scoring (reads ground truth: allowed here, DESIGN.md honesty rules)
# --------------------------------------------------------------------------
@dataclass
class RunView:
    """The parts of a `run_system` result the failure comparison needs (the hierarchy object can be dropped)."""
    snapshots: dict[int, list[ClaimRecord]]
    discovered: dict[str, dict[str, Any]]
    classified: list[dict[str, Any]]
    conflict_keys: set[tuple[int, int]] = field(default_factory=set)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def last_round(self) -> int:
        return max(self.snapshots) if self.snapshots else -1


def summarize_run(result: dict[str, Any]) -> RunView:
    keys: set[tuple[int, int]] = set()
    hier = result.get("hier")
    if hier is not None:
        for node in hier.nodes.values():
            for conf in node.conflicts.values():
                keys.add((int(conf.cell), int(conf.label)))
    return RunView(snapshots=dict(result.get("snapshots", {})), discovered=dict(result.get("discovered", {})),
                   classified=list(result.get("classified", [])), conflict_keys=keys, metrics=dict(result.get("metrics", {})))


def _effect_scope(e: Effect) -> tuple[str, int]:
    if e.scope_layer in ("team", "department", "region"):
        return e.scope_layer, int(e.scope_unit)
    return "executive", 0


def held_effects(world: World, kb: list[ClaimRecord], effects: list[Effect]) -> set[str]:
    """Effects with an exact accepted claim in `kb` (same rule as `evaluate.match_effects` 'exact')."""
    by_sig: dict[tuple[int, int, int], list[ClaimRecord]] = {}
    for c in kb:
        if c.status == "accepted":
            by_sig.setdefault((int(c.cell), int(c.label), int(c.sign)), []).append(c)
    held: set[str] = set()
    for e in effects:
        for c in by_sig.get((int(e.cell), int(e.label), int(e.sign)), []):
            if not scopes_nested(world, (c.scope_layer, c.scope_unit), _effect_scope(e)):
                continue
            if e.kind == "temporal":
                ok = c.round_accepted < (e.valid_to or 10**9) + 2 and (c.valid_to is None or c.valid_to >= e.valid_from)
                if not ok:
                    continue
            held.add(e.effect_id)
            break
    return held


def _ratio(num: float, den: float) -> float | None:
    return None if den <= 0 else float(num) / float(den)


def compare_runs(world: World, base: RunView | dict[str, Any], fail: RunView | dict[str, Any],
                 plan: FailurePlan | None, survival_threshold: float = 0.95) -> dict[str, Any]:
    """Failure metrics of `fail` against the paired no-failure run `base` (same seed/world/system).

    knowledge_survival          fraction of the base root's accepted exact discoveries accepted at the end of `fail`
    useful_discovery_survival   same, restricted to cross_team + global effects
    survival_curve[r]           |held_fail(r) & held_base(r)| / |held_base(r)| per round (relative to the paired run)
    survival_pre_recovery       survival_curve just before recovery (or at the end when there is no recovery)
    survival_min                minimum of the curve from at_round on
    lineage_survival            fraction of contributing units of the claims surviving at the end of the outage that
                                were still reachable from the root at that time
    conflict_survival           fraction of the base run's conflicts (any node, by (cell,label)) still raised in `fail`
    contradiction_detection_survival   same restricted to true contradiction signatures
    recovery_latency            rounds after recover_round until survival_curve >= threshold (None when never)
    repair_quality              fraction of discoveries dropped during the outage that are back by the end
    false_reconstruction_rate   fraction of root claims first accepted at/after recovery that are population-false
    """
    if isinstance(base, dict):
        base = summarize_run(base)
    if isinstance(fail, dict):
        fail = summarize_run(fail)
    effects_by_id = {e.effect_id: e for e in world.effects}
    base_ids = [eid for eid, d in base.discovered.items() if d.get("status") == "accepted" and eid in effects_by_id]
    base_effects = [effects_by_id[eid] for eid in base_ids]
    useful_ids = {e.effect_id for e in base_effects if e.kind in ("cross_team", "global")}
    n_rounds = int(world.n_rounds)
    at_round = plan.at_round if plan is not None else n_rounds
    recover_round = plan.recover_round if plan is not None else None
    last = max(fail.last_round, base.last_round, n_rounds - 1)

    # per-round held sets
    held_b: dict[int, set[str]] = {}
    held_f: dict[int, set[str]] = {}
    prev_b: set[str] = set(); prev_f: set[str] = set()
    for r in range(last + 1):
        if r in base.snapshots:
            prev_b = held_effects(world, base.snapshots[r], base_effects)
        if r in fail.snapshots:
            prev_f = held_effects(world, fail.snapshots[r], base_effects)
        held_b[r] = set(prev_b); held_f[r] = set(prev_f)
    curve: list[float | None] = []
    for r in range(last + 1):
        curve.append(_ratio(len(held_b[r] & held_f[r]), len(held_b[r])))

    end_held = held_f[last]
    knowledge_survival = _ratio(len(end_held), len(base_ids))
    useful_survival = _ratio(len(end_held & useful_ids), len(useful_ids))
    pre = (recover_round - 1) if recover_round is not None else last
    pre = max(0, min(pre, last))
    survival_pre = curve[pre]
    window_vals = [v for r, v in enumerate(curve) if r >= at_round and v is not None]
    survival_min = min(window_vals) if window_vals else None

    # dropped during the outage / recovered by the end
    dropped = (held_b[pre] - held_f[pre]) & set(base_ids)
    recovered = dropped & end_held
    repair_quality = _ratio(len(recovered), len(dropped))

    # recovery latency
    latency: int | None = None
    recovered_flag = False
    if recover_round is not None and recover_round <= last:
        for r in range(recover_round, last + 1):
            v = curve[r]
            if v is not None and v >= survival_threshold:
                latency = r - recover_round
                recovered_flag = True
                break
    elif recover_round is None:
        latency = None
    censored = (last - recover_round + 1) if (recover_round is not None and not recovered_flag) else None

    # lineage survival at the end of the outage window
    unreachable = plan.unreachable_units() if (plan is not None and plan.active) else set()
    if plan is not None and not plan.active:
        # the plan has recovered: reconstruct the outage set from the plan's own record
        unreachable = set(plan.failed_units) if plan.kind != "delayed_sync" else set()
        unreachable |= {f"W{w:06d}" for w in plan.failed_workers}
    lineage_vals: list[float] = []
    if plan is not None:
        kb_pre = fail.snapshots.get(pre, [])
        surviving_sigs = {(effects_by_id[eid].cell, effects_by_id[eid].label, effects_by_id[eid].sign) for eid in held_f[pre]}
        org = world.org
        anc_cache: dict[str, bool] = {}

        def reachable(u: str) -> bool:
            if u not in anc_cache:
                anc_cache[u] = not (unit_ancestors(org, u) & unreachable)
            return anc_cache[u]
        for c in kb_pre:
            if c.status != "accepted" or (int(c.cell), int(c.label), int(c.sign)) not in surviving_sigs:
                continue
            units = [u for u in c.contributing_units if u]
            if not units:
                continue
            lineage_vals.append(sum(1 for u in units if reachable(u)) / len(units))
    lineage_survival = float(np.mean(lineage_vals)) if lineage_vals else (1.0 if plan is None else None)

    # conflicts
    gt_keys = set()
    for e in world.effects:
        if e.kind == "contradiction":
            gt_keys.add((int(e.cell), int(e.label)))
    conflict_survival = _ratio(len(base.conflict_keys & fail.conflict_keys), len(base.conflict_keys))
    base_true = base.conflict_keys & gt_keys
    contradiction_survival = _ratio(len(base_true & fail.conflict_keys), len(base_true))

    # false reconstruction after recovery
    cut = recover_round if recover_round is not None else at_round
    after = [c for c in fail.classified if c.get("status") == "accepted" and int(c.get("round", -1)) >= cut]
    n_false_after = sum(1 for c in after if not c.get("population_true"))
    after_b = [c for c in base.classified if c.get("status") == "accepted" and int(c.get("round", -1)) >= cut]
    n_false_after_b = sum(1 for c in after_b if not c.get("population_true"))

    bm, fm = base.metrics, fail.metrics
    out: dict[str, Any] = {
        "n_base_discoveries": len(base_ids), "n_base_useful": len(useful_ids),
        "n_surviving": len(end_held), "n_dropped": len(dropped), "n_recovered": len(recovered),
        "knowledge_survival": knowledge_survival, "useful_discovery_survival": useful_survival,
        "survival_pre_recovery": survival_pre, "survival_min": survival_min,
        "lineage_survival": lineage_survival,
        "conflict_survival": conflict_survival, "contradiction_detection_survival": contradiction_survival,
        "n_base_conflicts": len(base.conflict_keys), "n_base_true_conflicts": len(base_true),
        "recovery_latency": latency, "recovered": bool(recovered_flag), "recovery_latency_censored": censored,
        "repair_quality": repair_quality,
        "false_reconstruction_rate": _ratio(n_false_after, len(after)), "n_accepted_after_recovery": len(after),
        "n_false_after_recovery": n_false_after,
        "false_reconstruction_rate_base": _ratio(n_false_after_b, len(after_b)),
        "delta_recall_local": _delta(fm, bm, "recall_local"), "delta_recall_cross_team": _delta(fm, bm, "recall_cross_team"),
        "delta_recall_global": _delta(fm, bm, "recall_global"), "delta_precision_strict": _delta(fm, bm, "precision_strict"),
        "delta_false_discovery_rate": _delta(fm, bm, "false_discovery_rate"),
        "delta_bytes_transmitted": _delta(fm, bm, "bytes_transmitted"),
        "survival_curve": curve,
    }
    if plan is not None:
        s = plan.summary()
        out.update({"failure_kind": plan.kind, "failure_rate": plan.rate, "at_round": plan.at_round,
                    "recover_round": plan.recover_round, "n_units_failed": s["n_units_failed"],
                    "worker_fraction_affected": s["worker_fraction_affected"], "records_lost": s["records_lost"],
                    "records_buffered": s["records_buffered"], "records_replayed": s["records_replayed"]})
    else:
        out.update({"failure_kind": "none", "failure_rate": 0.0, "at_round": None, "recover_round": None,
                    "n_units_failed": 0, "worker_fraction_affected": 0.0, "records_lost": 0, "records_buffered": 0,
                    "records_replayed": 0})
    return out


def _delta(fm: dict[str, Any], bm: dict[str, Any], key: str) -> float | None:
    a, b = fm.get(key), bm.get(key)
    try:
        if a is None or b is None or a != a or b != b:
            return None
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


__all__ = ["KINDS", "CRASH_KINDS", "NODE_KINDS", "LINK_KINDS", "FailurePlan", "RunView", "chain_hooks", "compare_runs",
           "held_effects", "summarize_run", "unit_ancestors", "workers_under"]
