"""Capability-survival benchmark: placement strategies under targeted intervention.

This module answers one question under matched conditions:

    After an intervention removes some part of the system, can the collective
    still reconstruct the correct answer that no single node holds?

It sweeps eight placement/repair strategies against eight interventions and
reports capability survival plus the supporting cost, privacy, and fragility
metrics.  Everything here is deterministic: seeded symbol generation, a logical
clock, sorted aggregation, and no wall-clock or hash-order dependence.  Two runs
with the same seed produce byte-identical JSON.

Design commitments that keep the comparison fair, and their limits:

* Routing is bounded: a query contacts at most three nodes
  (``max_nodes=3``, ``max_claims=8``, ``max_verifications=2``), identical for
  every coordinated strategy.  The bound is load bearing and was verified to be
  so.  Lifting it to "contact everything" was tried and collapses the
  experiment: the independently rooted node is then reached by the ordinary
  query, repair never fires, and every repair policy scores identically at
  0.88.  A bound is also the realistic case -- a system that can afford to poll
  every holder of every premise does not need a repair policy at all.  What the
  bound must not do is decide the outcome by itself, which is why the ordinary
  route is fixed and identical across strategies, and only the *repair* ordering
  varies.  ``isolated_local`` reaches one node because it owns one node.
* Because routing is bounded, ``minimal_failure_domain_cut`` is reported twice:
  once as observed over the claims a single query actually returned, and once as
  ``structural_min_cut`` over the entire placement.  The structural figure is the
  design property being argued about; the observed figure is what a bounded
  query can see of it, and the two differ precisely when routing hides diversity.
* Strategies 4-8 place exactly four memory records, so redundancy efficiency is
  directly comparable among them.  ``isolated_local`` (1), ``centralized`` (2),
  and ``full_replication`` (8) differ by construction, so their efficiency
  numbers are reported but must not be read as ranked against the four-record
  group.
* Every intervention resolves its target from the *pre-failure* execution of the
  strategy being tested -- it disables whatever that strategy actually relied
  on.  A fixed target id would silently be a no-op for some placements and fatal
  for others, which would make the matrix meaningless.
* ``recovery_steps`` counts verification round trips and ``recovery_events``
  counts trace events emitted during repair.  Both are logical step counts, not
  wall-clock latency.  This harness deliberately measures no wall-clock time,
  because a mock adapter's timings would say nothing about a deployed system.

Authorization scopes are per-lineage-root (``capability:opaque-pair:R2``) so that
``authorization_revocation`` is a targeted intervention on the policy plane that
parallels ``lineage_root_failure`` on the storage plane.  Whether correlated
authorization is as dangerous as correlated lineage is then a measured result
rather than an assumption baked into the fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .adapters import MockMemoryNodeAdapter, PrivateMemoryRecord
from .contracts import (
    AuthorizationContext,
    InterventionTarget,
    LearningDisposition,
    QueryBudget,
    QueryRequest,
)
from .core import (
    CapabilityRegistry,
    ClaimNormalizer,
    FailureInjector,
    LineageAnalyzer,
    LogicalClock,
    QueryExecution,
    RepairPlanner,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
    TraceLogger,
    to_jsonable,
)

BENCHMARK_VERSION = "capability-survival-v1"
CAPABILITY_ID = "opaque_pair_reconstruction"
REQUIRED_SLOTS = ("left", "right")
DEFAULT_SEED = 20260813


def _scope_for(root: str) -> str:
    """Authorization scope tied to a lineage root, not to the whole capability."""
    return f"capability:opaque-pair:{root}"


# --------------------------------------------------------------------------
# Knowledge universe
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbols:
    left: str
    right: str
    distractors: tuple[str, ...]
    query_nonce: str

    @property
    def expected_answer(self) -> str:
        return f"{self.left}:{self.right}"


def _opaque(rng: random.Random, prefix: str) -> str:
    body = "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(14))
    return f"{prefix}_{body}"


def generate_symbols(seed: int) -> Symbols:
    """Seeded opaque symbols.

    The symbols are opaque so that no synthesizer can guess the answer from the
    query text, and so that a leak is detectable by substring search rather than
    by judgement.
    """
    rng = random.Random(seed)
    return Symbols(
        left=_opaque(rng, "LX"),
        right=_opaque(rng, "RX"),
        distractors=tuple(_opaque(rng, "DX") for _ in range(4)),
        query_nonce=_opaque(rng, "QX"),
    )


@dataclass(frozen=True)
class Placement:
    """One strategy's answer to 'where does knowledge live, and how do we repair'."""

    name: str
    description: str
    records_by_node: dict[str, tuple[PrivateMemoryRecord, ...]]
    repair_policy: str
    max_nodes: int
    coordinates: bool
    #: The premises this capability requires.  A placement, not the module,
    #: owns this so a capability of any arity can be benchmarked by the same
    #: machinery (see ``scale.py``); the eight named strategies all use the
    #: two-slot default.
    slots: tuple[str, ...] = REQUIRED_SLOTS
    #: Verification round trips repair may spend. Two is right for the two-slot
    #: fixture and wrong at scale: holding it fixed while the candidate set grows
    #: measures a shrinking relative budget, not the repair mechanism. ``scale.py``
    #: scales it with capability arity.
    max_verifications: int = 2

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.records_by_node))

    @property
    def record_count(self) -> int:
        return sum(len(records) for records in self.records_by_node.values())

    @property
    def all_scopes(self) -> tuple[str, ...]:
        return tuple(sorted({
            record.required_scope
            for records in self.records_by_node.values()
            for record in records
        }))


def expected_answer_for(placement: "Placement") -> str:
    """The correct reconstruction for a placement, in its own slot order.

    Derived from the records placed rather than from ``Symbols.expected_answer``,
    which only describes the two-slot pair. A capability of any arity is correct
    exactly when the synthesizer returns one value per premise, joined in slot
    order -- so this stays right as arity varies.
    """
    by_slot: dict[str, str] = {}
    for node_id in sorted(placement.records_by_node):
        for record in placement.records_by_node[node_id]:
            by_slot.setdefault(record.slot, record.value)
    return ":".join(by_slot[slot] for slot in placement.slots if slot in by_slot)


def _record(
    node: str,
    slot: str,
    value: str,
    memory: str,
    source: str,
    root: str,
    domain: str,
    confidence: float,
) -> PrivateMemoryRecord:
    return PrivateMemoryRecord(
        memory_id=memory,
        capability_id=CAPABILITY_ID,
        slot=slot,
        value=value,
        confidence=confidence,
        source_ids=(source,),
        parent_memory_ids=(f"parent-{memory}",),
        lineage_root_ids=(root,),
        failure_domains=(domain,),
        edge_path=(f"edge-{node}-{slot}",),
        required_scope=_scope_for(root),
    )


# --------------------------------------------------------------------------
# The eight placement / repair strategies
# --------------------------------------------------------------------------


def _isolated_local(sym: Symbols) -> Placement:
    return Placement(
        name="isolated_local",
        description="One node, its own memory only, no coordination. Capability floor.",
        records_by_node={
            "node-a": (_record("a", "left", sym.left, "memory-a", "source-1", "R1", "FD1", 0.96),),
        },
        repair_policy="none",
        max_nodes=1,
        coordinates=False,
    )


def _centralized(sym: Symbols) -> Placement:
    return Placement(
        name="centralized",
        description="One hub node holds every premise. Succeeds trivially; holds the answer.",
        records_by_node={
            "node-hub": (
                _record("hub", "left", sym.left, "memory-hub-l", "source-1", "R1", "FD1", 0.96),
                _record("hub", "right", sym.right, "memory-hub-r", "source-2", "R2", "FD2", 0.93),
            ),
        },
        repair_policy="none",
        max_nodes=1,
        coordinates=False,
    )


def _full_replication(sym: Symbols) -> Placement:
    """Every node holds every premise -- but every copy descends from one root.

    This is what 'replicate everything' actually buys: maximum apparent
    redundancy over minimum lineage diversity.
    """
    records = {}
    for node in ("a", "b", "c", "d"):
        records[f"node-{node}"] = (
            _record(node, "left", sym.left, f"memory-{node}-l", "source-1", "R1", "FD1", 0.96),
            _record(node, "right", sym.right, f"memory-{node}-r", "source-2", "R2", "FD2", 0.93),
        )
    return Placement(
        name="full_replication",
        description="All four nodes hold both premises; all copies share one root per slot.",
        records_by_node=records,
        repair_policy="none",
        max_nodes=3,
        coordinates=True,
    )


def _correlated_replica_records(sym: Symbols) -> dict[str, tuple[PrivateMemoryRecord, ...]]:
    """A holds left; B, C, D each hold right -- all three from the same root R2."""
    return {
        "node-a": (_record("a", "left", sym.left, "memory-a", "source-1", "R1", "FD1", 0.96),),
        "node-b": (_record("b", "right", sym.right, "memory-b", "source-2", "R2", "FD2", 0.93),),
        "node-c": (_record("c", "right", sym.right, "memory-c", "source-2-copy", "R2", "FD2", 0.91),),
        "node-d": (_record("d", "right", sym.right, "memory-d", "source-2-copy2", "R2", "FD2", 0.90),),
    }


def _fixed_distributed_replication(sym: Symbols) -> Placement:
    return Placement(
        name="fixed_distributed_replication",
        description="Three replicas of the right premise across three nodes, one shared root.",
        records_by_node=_correlated_replica_records(sym),
        repair_policy="none",
        max_nodes=3,
        coordinates=True,
    )


def _source_count_repair(sym: Symbols) -> Placement:
    return Placement(
        name="source_count_repair",
        description="Same placement; repair prefers the most-replicated support first.",
        records_by_node=_correlated_replica_records(sym),
        repair_policy="source_count",
        max_nodes=3,
        coordinates=True,
    )


def _diverse_records(sym: Symbols) -> dict[str, tuple[PrivateMemoryRecord, ...]]:
    """A holds left; B and C hold correlated right; D holds an independently rooted right."""
    return {
        "node-a": (_record("a", "left", sym.left, "memory-a", "source-1", "R1", "FD1", 0.96),),
        "node-b": (_record("b", "right", sym.right, "memory-b", "source-2", "R2", "FD2", 0.93),),
        "node-c": (_record("c", "right", sym.right, "memory-c", "source-2-copy", "R2", "FD2", 0.91),),
        "node-d": (_record("d", "right", sym.right, "memory-d", "source-3", "R3", "FD3", 0.94),),
    }


def _random_path_diversification(sym: Symbols) -> Placement:
    return Placement(
        name="random_path_diversification",
        description="Independent support exists; repair picks a candidate at random.",
        records_by_node=_diverse_records(sym),
        repair_policy="random",
        max_nodes=3,
        coordinates=True,
    )


def _lineage_aware_repair(sym: Symbols) -> Placement:
    return Placement(
        name="lineage_aware_repair",
        description="Same placement; repair excludes the failed roots and domains.",
        records_by_node=_diverse_records(sym),
        repair_policy="lineage_aware",
        max_nodes=3,
        coordinates=True,
    )


def _oracle_min_cut(sym: Symbols) -> Placement:
    """Same four-record budget, placed to maximise the minimum failure-domain cut.

    Both slots get two independently rooted holders in distinct domains, so no
    single domain failure can sever every reconstruction coalition.
    """
    return Placement(
        name="oracle_min_cut",
        description="Four records placed for maximum min-cut: both slots doubly rooted.",
        records_by_node={
            "node-a": (_record("a", "left", sym.left, "memory-a", "source-1", "R1", "FD1", 0.96),),
            "node-b": (_record("b", "right", sym.right, "memory-b", "source-2", "R2", "FD2", 0.93),),
            "node-c": (_record("c", "left", sym.left, "memory-c", "source-4", "R4", "FD4", 0.92),),
            "node-d": (_record("d", "right", sym.right, "memory-d", "source-3", "R3", "FD3", 0.94),),
        },
        repair_policy="lineage_aware",
        max_nodes=3,
        coordinates=True,
    )


STRATEGY_BUILDERS: dict[str, Callable[[Symbols], Placement]] = {
    "isolated_local": _isolated_local,
    "centralized": _centralized,
    "full_replication": _full_replication,
    "fixed_distributed_replication": _fixed_distributed_replication,
    "source_count_repair": _source_count_repair,
    "random_path_diversification": _random_path_diversification,
    "lineage_aware_repair": _lineage_aware_repair,
    "oracle_min_cut": _oracle_min_cut,
}

STRATEGIES = tuple(STRATEGY_BUILDERS)


# --------------------------------------------------------------------------
# Runtime assembly
# --------------------------------------------------------------------------


@dataclass
class Runtime:
    placement: Placement
    symbols: Symbols
    query: QueryRequest
    clock: LogicalClock
    traces: TraceLogger
    failure: FailureInjector
    registry: CapabilityRegistry
    coordinator: TesseractCoordinator
    planner: RepairPlanner


def build_runtime(seed: int, strategy: str, placement: Placement | None = None) -> Runtime:
    """Assemble a runtime for a named strategy, or for a caller-supplied placement.

    ``scale.py`` passes its own generated placement so capabilities of any arity
    run through exactly this path -- same coordinator, same router, same repair
    planner -- rather than a parallel harness that could drift from it.
    """
    symbols = generate_symbols(seed)
    if placement is None:
        placement = STRATEGY_BUILDERS[strategy](symbols)
    run_id = f"bench_{seed}_{strategy}"
    clock = LogicalClock(seed)
    issued_at = LogicalClock(seed).now()
    query = QueryRequest(
        query_id=f"query_{seed}_{strategy}",
        content=f"Reconstruct the two-part capability identified by {symbols.query_nonce}",
        requested_capability=CAPABILITY_ID,
        requester_id="benchmark-controller",
        issued_at=issued_at,
        valid_at=issued_at,
        authorization=AuthorizationContext(
            scopes=placement.all_scopes,
            allowed_node_ids=placement.node_ids,
        ),
        budget=QueryBudget(
            max_nodes=placement.max_nodes,
            # One claim per contacted node is the floor; the two-slot strategies
            # keep their original 8 so their pinned artifacts do not move.
            max_claims=max(8, 2 * len(placement.node_ids)),
            max_verifications=placement.max_verifications,
        ),
    )
    traces = TraceLogger(run_id, clock)
    failure = FailureInjector(run_id, seed, clock, traces)
    registry = CapabilityRegistry()
    for node_id in placement.node_ids:
        registry.register(MockMemoryNodeAdapter(
            node_id=node_id,
            records=placement.records_by_node[node_id],
            failure_view=failure,
            clock=clock.now,
            learning_policy=LearningDisposition.TENTATIVE,
        ))
    coordinator = TesseractCoordinator(
        registry=registry,
        router=Router(registry, failure),
        normalizer=ClaimNormalizer(),
        synthesizer=RuleBasedSynthesizer(placement.slots),
        analyzer=LineageAnalyzer(),
        traces=traces,
    )
    return Runtime(
        placement=placement,
        symbols=symbols,
        query=query,
        clock=clock,
        traces=traces,
        failure=failure,
        registry=registry,
        coordinator=coordinator,
        planner=RepairPlanner(registry, traces),
    )


# --------------------------------------------------------------------------
# Interventions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InterventionPlan:
    """A resolved intervention: which injector targets to apply, and why."""

    name: str
    description: str
    applications: tuple[tuple[InterventionTarget, str], ...]
    note: str = ""


def _supporting_claims(execution: QueryExecution, slot: str) -> tuple[Any, ...]:
    return tuple(claim for claim in execution.claims if claim.content.get("slot") == slot)


def _load_bearing_claim(execution: QueryExecution, slots: tuple[str, ...] = REQUIRED_SLOTS) -> Any | None:
    """The claim the synthesizer actually chose for the last required slot.

    Interventions target this rather than a hard-coded id so that each strategy
    is attacked where it is actually load bearing.
    """
    if not execution.synthesis.selected_claim_ids:
        return None
    target_slot = slots[-1]
    for claim in execution.claims:
        if (
            claim.claim_id in execution.synthesis.selected_claim_ids
            and claim.content.get("slot") == target_slot
        ):
            return claim
    for claim in execution.claims:
        if claim.claim_id in execution.synthesis.selected_claim_ids:
            return claim
    return None


def _memory_id_for(runtime: Runtime, claim: Any) -> str | None:
    """Recover the private memory id behind an exported claim.

    The claim contract deliberately does not carry it, so this reads the
    placement -- benchmark-side knowledge, never adapter-side.  Matching is by
    (producer node, slot), which is unique within a node in every placement here.
    """
    if claim is None:
        return None
    for record in runtime.placement.records_by_node.get(claim.producer_node_id, ()):
        if record.slot == claim.content.get("slot"):
            return record.memory_id
    return None


def _edge_path_for(runtime: Runtime, claim: Any) -> str | None:
    if claim is None:
        return None
    for record in runtime.placement.records_by_node.get(claim.producer_node_id, ()):
        if record.slot == claim.content.get("slot"):
            return record.edge_path[0] if record.edge_path else None
    return None


def plan_intervention(name: str, runtime: Runtime, baseline: QueryExecution) -> InterventionPlan:
    """Resolve an intervention against what this strategy actually relied on."""
    claim = _load_bearing_claim(baseline, runtime.placement.slots)
    node_id = claim.producer_node_id if claim is not None else runtime.placement.node_ids[0]
    memory_id = _memory_id_for(runtime, claim)
    edge_id = _edge_path_for(runtime, claim)
    root_id = claim.lineage_root_ids[0] if claim is not None and claim.lineage_root_ids else "R2"

    if name == "node_failure":
        return InterventionPlan(
            name, "The node serving the load-bearing premise goes offline.",
            ((InterventionTarget.NODE, node_id),),
        )
    if name == "memory_deletion":
        target = memory_id or "memory-b"
        return InterventionPlan(
            name, "The specific memory backing the load-bearing premise is gone.",
            ((InterventionTarget.MEMORY, target),),
        )
    if name == "lineage_root_failure":
        return InterventionPlan(
            name, "The derivation root of the load-bearing premise is invalidated.",
            ((InterventionTarget.LINEAGE_ROOT, root_id),),
            note="Removes every copy descending from this root, however many nodes hold one.",
        )
    if name == "authorization_revocation":
        return InterventionPlan(
            name, "The authorization scope covering that root is revoked.",
            ((InterventionTarget.AUTHORIZATION_SCOPE, _scope_for(root_id)),),
            note="Policy-plane analogue of lineage_root_failure; scopes are per-root.",
        )
    if name == "route_removal":
        return InterventionPlan(
            name, "The route to the load-bearing node is withdrawn.",
            ((InterventionTarget.ROUTE, node_id),),
        )
    if name == "edge_corruption":
        target = edge_id or "edge-b-right"
        return InterventionPlan(
            name, "The graph edge carrying that derivation is corrupted.",
            ((InterventionTarget.EDGE, target),),
        )
    if name == "stale_knowledge":
        target = memory_id or "memory-b"
        return InterventionPlan(
            name, "The load-bearing memory is present but no longer valid at query time.",
            ((InterventionTarget.EVIDENCE, target),),
        )
    if name == "worst_single_domain_failure":
        # The adversarial intervention, and the one that tests the paper's
        # metric rather than restating it.  Every other intervention here
        # targets the last required slot, which silently spares a premise held
        # by exactly one node -- so a placement can look robust while resting on
        # an unattacked single point of failure.
        #
        # Failing the whole min-cut would be tautological: a cut severs every
        # coalition by definition, so every strategy would score zero and the
        # cell would carry no information.  Instead this fails exactly ONE
        # domain, the one appearing in the most reconstruction coalitions.  A
        # placement with min-cut 1 cannot survive it; a placement with min-cut 2
        # must.  Survival here is therefore a genuine test of whether the
        # minimum failure-domain cut predicts capability survival.
        worst = _worst_single_domain(runtime.placement)
        targets = roots_in_domains(runtime.placement, worst)
        diversity = structural_diversity(runtime.placement)
        return InterventionPlan(
            name,
            "The single failure domain carrying the most reconstruction coalitions fails.",
            tuple((InterventionTarget.LINEAGE_ROOT, root) for root in targets),
            note=(
                f"domain={list(worst)}, roots={list(targets)}, "
                f"structural min-cut={diversity['structural_min_cut']}: "
                "a min-cut of 1 predicts loss here, a min-cut of 2 predicts survival."
            ),
        )
    if name == "network_partition":
        reachable = runtime.placement.node_ids[:1]
        cut = tuple(
            (InterventionTarget.ROUTE, other)
            for other in runtime.placement.node_ids
            if other not in reachable
        )
        return InterventionPlan(
            name, "The coordinator is isolated with a minority of nodes.",
            cut,
            note="Routes to every node beyond the first are withdrawn simultaneously.",
        )
    raise ValueError(f"unknown intervention: {name}")


INTERVENTIONS = (
    "node_failure",
    "memory_deletion",
    "lineage_root_failure",
    "authorization_revocation",
    "route_removal",
    "edge_corruption",
    "stale_knowledge",
    "network_partition",
    "worst_single_domain_failure",
)


# --------------------------------------------------------------------------
# Repair policies
# --------------------------------------------------------------------------


def observed_lineage(baseline: QueryExecution) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """What the coordinator legitimately knows about each node's provenance.

    Derived only from exported ``ClaimEnvelope`` contracts seen during the
    pre-intervention query -- never from ``Placement`` internals.  A repair
    policy that peeked at node-private records would not be a policy, it would
    be an oracle, and the comparison against the real oracle row would be
    meaningless.
    """
    seen: dict[str, tuple[set[str], set[str]]] = {}
    for claim in baseline.claims:
        roots, domains = seen.setdefault(claim.producer_node_id, (set(), set()))
        roots.update(claim.lineage_root_ids)
        domains.update(claim.failure_domains)
    return {
        node: (frozenset(roots), frozenset(domains))
        for node, (roots, domains) in seen.items()
    }


def observed_slots(baseline: QueryExecution) -> dict[str, frozenset[str]]:
    """Which premises each contacted node was seen to produce.

    Like ``observed_lineage``, this is read only from exported claims. A node the
    bounded route never contacted simply does not appear, and "not observed" is
    genuinely different from "observed to hold nothing relevant".
    """
    seen: dict[str, set[str]] = {}
    for claim in baseline.claims:
        slot = claim.content.get("slot")
        if slot is not None:
            seen.setdefault(claim.producer_node_id, set()).add(str(slot))
    return {node: frozenset(slots) for node, slots in seen.items()}


def _slot_relevance(node: str, missing: frozenset[str], slots: dict[str, frozenset[str]]) -> int:
    """Rank a candidate by whether it could supply a premise that is missing.

    0  observed to hold one of the missing premises
    1  never contacted, so its contents are unknown and worth a verification
    2  observed, and observed to hold none of the missing premises

    This is a *tiebreaker*, applied within every policy's own principle rather
    than ahead of it. Both parts of that matter:

    * Applied to every policy, because a verification spent on a node that
      demonstrably holds the wrong premise is wasted under any policy, and
      letting only one policy avoid that waste would hand it an advantage
      unrelated to lineage.
    * Never ahead of the principle, because a node observed holding the missing
      premise may be exactly the one whose lineage just failed. Ranking on
      relevance first puts the known-compromised holders at the front and
      exhausts the budget on them -- which is the original failure this
      ordering exists to avoid.

    It matters more as arity grows: with K premises, most candidates hold a
    premise that is not the missing one.
    """
    if node not in slots:
        return 1
    return 0 if slots[node] & missing else 2


def order_repair_candidates(
    policy: str,
    candidates: tuple[str, ...],
    lineage: dict[str, tuple[frozenset[str], frozenset[str]]],
    excluded_roots: tuple[str, ...],
    excluded_domains: tuple[str, ...],
    seed: int,
    missing_slots: tuple[str, ...] = (),
    slots: dict[str, frozenset[str]] | None = None,
) -> tuple[str, ...]:
    """Order candidate nodes for verification. The ordering *is* the repair policy.

    Slot relevance is a shared primary key across policies; the policies differ
    only in how they break ties within it.
    """
    missing = frozenset(missing_slots)
    known = slots or {}
    relevance = {node: _slot_relevance(node, missing, known) for node in candidates}
    ordered = tuple(sorted(candidates))
    if policy == "source_count":
        # "More copies means safer": rank by how many other nodes share a root
        # with this one.  This is the intuition the paper argues against, and it
        # spends a bounded verification budget on correlated nodes first.
        def replicas(node: str) -> int:
            roots = lineage.get(node, (frozenset(), frozenset()))[0]
            return sum(
                1 for other in ordered
                if other != node and roots & lineage.get(other, (frozenset(), frozenset()))[0]
            )
        return tuple(sorted(ordered, key=lambda node: (-replicas(node), relevance[node], node)))
    if policy == "random":
        shuffled = list(ordered)
        random.Random(seed).shuffle(shuffled)
        return tuple(shuffled)
    if policy == "lineage_aware":
        # Rank by independence from the support already known to be compromised.
        # Excluding correlated roots at verification time is not enough: with a
        # bounded verification budget, a lineage-blind ordering exhausts itself
        # on correlated nodes before ever reaching an independent one.  Ordering
        # is where lineage awareness has to act.
        bad_roots, bad_domains = frozenset(excluded_roots), frozenset(excluded_domains)

        def independence(node: str) -> tuple[int, int, int, str]:
            roots, domains = lineage.get(node, (frozenset(), frozenset()))
            return (
                1 if roots & bad_roots else 0,
                1 if domains & bad_domains else 0,
                relevance[node],
                node,
            )
        return tuple(sorted(ordered, key=independence))
    return ordered


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def _privacy_audit(runtime: Runtime, executions: tuple[QueryExecution, ...]) -> dict[str, Any]:
    """Does any single node hold the whole answer, and did anything leak?"""
    sym = runtime.symbols
    single_node_holds_answer = any(
        {record.slot for record in records} >= set(runtime.placement.slots)
        for records in runtime.placement.records_by_node.values()
    )
    answer_in_query = any(
        symbol in runtime.query.content
        for symbol in (sym.left, sym.right, sym.expected_answer)
    )
    complete_answer_in_a_memory = any(
        record.value == sym.expected_answer
        for records in runtime.placement.records_by_node.values()
        for record in records
    )
    payload_bearing_denials = sum(
        1
        for execution in executions
        for export in execution.exports
        if export.trace.policy_status.value != "allowed"
        and (export.claims or export.trace.memory_ids or export.trace.lineage_root_ids)
    )
    return {
        "single_node_holds_full_answer": single_node_holds_answer,
        "answer_present_in_query": answer_in_query,
        "complete_answer_in_a_single_memory": complete_answer_in_a_memory,
        "payload_bearing_denials": payload_bearing_denials,
        "policy_violations": sum(execution.policy_violations for execution in executions),
        "leak_free": (
            not answer_in_query
            and not complete_answer_in_a_memory
            and payload_bearing_denials == 0
            and sum(execution.policy_violations for execution in executions) == 0
        ),
    }


def structural_diversity(placement: Placement) -> dict[str, Any]:
    """Lineage diversity of the placement itself, independent of any routing bound.

    A bounded query sees only part of a placement, so the observed min-cut
    understates a well-placed system.  This computes the same quantity over every
    record placed, which is the property a placement strategy is actually
    choosing.  Enumerating one holder per slot is tractable here because the
    fixture has two slots and at most four holders each; this is a fixture-scale
    metric, not a general graph algorithm.
    """
    by_slot: dict[str, list[PrivateMemoryRecord]] = {slot: [] for slot in placement.slots}
    for records in placement.records_by_node.values():
        for record in records:
            if record.slot in by_slot:
                by_slot[record.slot].append(record)
    if any(not holders for holders in by_slot.values()):
        return {
            "structural_min_cut": 0,
            "structural_cut_domains": [],
            "structural_coalition_count": 0,
            "structural_root_count": len({
                root for records in placement.records_by_node.values()
                for record in records for root in record.lineage_root_ids
            }),
            "reconstructible": False,
        }

    from itertools import combinations, product

    coalitions: set[tuple[str, ...]] = set()
    for selected in product(*(sorted(by_slot[slot], key=lambda r: r.memory_id)
                              for slot in placement.slots)):
        domains = tuple(sorted({d for record in selected for d in record.failure_domains}))
        if domains:
            coalitions.add(domains)
    all_domains = sorted({d for coalition in coalitions for d in coalition})
    min_cut = 0
    cut_domains: tuple[str, ...] = ()
    for size in range(1, len(all_domains) + 1):
        found = next(
            (
                candidate for candidate in combinations(all_domains, size)
                if all(set(candidate) & set(coalition) for coalition in coalitions)
            ),
            None,
        )
        if found is not None:
            min_cut = size
            cut_domains = tuple(sorted(found))
            break
    return {
        "structural_min_cut": min_cut,
        "structural_cut_domains": list(cut_domains),
        "structural_coalition_count": len(coalitions),
        "structural_root_count": len({
            root for records in placement.records_by_node.values()
            for record in records for root in record.lineage_root_ids
        }),
        "reconstructible": True,
    }


def _worst_single_domain(placement: Placement) -> tuple[str, ...]:
    """The one failure domain appearing in the most reconstruction coalitions.

    Ties break on domain id so the choice is deterministic.  Returns an empty
    tuple when the placement cannot reconstruct at all, in which case there is
    nothing to attack.
    """
    by_slot: dict[str, list[PrivateMemoryRecord]] = {slot: [] for slot in placement.slots}
    for records in placement.records_by_node.values():
        for record in records:
            if record.slot in by_slot:
                by_slot[record.slot].append(record)
    if any(not holders for holders in by_slot.values()):
        return ()

    from itertools import product

    coalitions: list[set[str]] = []
    for selected in product(*(sorted(by_slot[slot], key=lambda r: r.memory_id)
                              for slot in placement.slots)):
        domains = {d for record in selected for d in record.failure_domains}
        if domains:
            coalitions.append(domains)
    if not coalitions:
        return ()
    all_domains = sorted({d for coalition in coalitions for d in coalition})
    scored = sorted(
        all_domains,
        key=lambda domain: (-sum(1 for c in coalitions if domain in c), domain),
    )
    return (scored[0],)


def roots_in_domains(placement: Placement, domains: tuple[str, ...]) -> tuple[str, ...]:
    """Every lineage root that lives in one of the given failure domains."""
    wanted = set(domains)
    return tuple(sorted({
        root
        for records in placement.records_by_node.values()
        for record in records
        if wanted & set(record.failure_domains)
        for root in record.lineage_root_ids
    }))


def _independent_support(execution: QueryExecution) -> dict[str, Any]:
    metrics = execution.metrics
    return {
        "apparent_replica_count": metrics.apparent_replica_count,
        "unique_lineage_root_count": metrics.unique_lineage_root_count,
        "unique_failure_domain_count": metrics.unique_failure_domain_count,
        "reconstruction_coalition_count": metrics.reconstruction_coalition_count,
        "minimal_failure_domain_cut": metrics.minimal_failure_domain_cut,
    }


# --------------------------------------------------------------------------
# One cell of the matrix
# --------------------------------------------------------------------------


async def run_cell(
    seed: int,
    strategy: str,
    intervention: str,
    placement: Placement | None = None,
    preferred_node_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run one (strategy, intervention) pair through before / after / repair."""
    runtime = build_runtime(seed, strategy, placement)
    expected = expected_answer_for(runtime.placement)

    route_preference = preferred_node_ids or runtime.placement.node_ids
    before = await runtime.coordinator.execute(
        runtime.query, phase="before_intervention",
        preferred_node_ids=route_preference,
    )
    before_correct = before.synthesis.success and before.synthesis.answer == expected

    plan = plan_intervention(intervention, runtime, before)
    for target_type, target_id in plan.applications:
        runtime.failure.apply(
            target_type, target_id,
            reason=f"benchmark intervention: {plan.name}",
            expected_effect=plan.description,
        )

    events_before_repair = len(runtime.traces.events)
    after = await runtime.coordinator.execute(
        runtime.query, phase="after_intervention",
        preferred_node_ids=route_preference,
    )
    after_correct = after.synthesis.success and after.synthesis.answer == expected

    # Silent forgetting: the system lost a capability it demonstrably had, and
    # still reports confidence above the fraction of slots it genuinely covers.
    # Conditioning on ``before_correct`` matters -- a system that never had the
    # capability has not forgotten anything, and counting that as forgetting
    # would flatter every strategy that starts out broken.
    silent_forgetting = (
        before_correct
        and not after_correct
        and after.metrics.confidence_valid_support_gap > 0.0
    )

    repair: Any = None
    repaired: QueryExecution | None = None
    repaired_correct = after_correct
    recovery_steps = 0

    if not after_correct and runtime.placement.repair_policy != "none":
        missing = tuple(
            slot for slot in runtime.placement.slots
            if slot not in after.synthesis.covered_slots
        )
        failed_claim = _load_bearing_claim(before, runtime.placement.slots)
        excluded_roots = tuple(failed_claim.lineage_root_ids) if failed_claim else ()
        excluded_domains = tuple(failed_claim.failure_domains) if failed_claim else ()
        if runtime.placement.repair_policy == "source_count":
            # This policy is lineage-blind by construction: it never tells the
            # verifier which support is already known to be compromised.
            excluded_roots, excluded_domains = (), ()
        candidates = order_repair_candidates(
            runtime.placement.repair_policy,
            tuple(
                node for node in runtime.placement.node_ids
                if node not in after.synthesis.selected_node_ids
            ),
            observed_lineage(before),
            excluded_roots,
            excluded_domains,
            seed,
            missing_slots=missing,
            slots=observed_slots(before),
        )
        repair = await runtime.planner.plan(
            runtime.query, missing, candidates, excluded_roots, excluded_domains,
        )
        recovery_steps = repair.steps
        if repair.selected_node_id:
            preferred = (repair.selected_node_id,) + tuple(
                node for node in route_preference if node != repair.selected_node_id
            )
            repaired = await runtime.coordinator.execute(
                runtime.query, phase="after_repair", preferred_node_ids=preferred,
            )
            repaired_correct = repaired.synthesis.success and repaired.synthesis.answer == expected

    executions = tuple(e for e in (before, after, repaired) if e is not None)
    recovery_events = len(runtime.traces.events) - events_before_repair
    bytes_moved = sum(execution.exported_bytes for execution in executions)
    survived = repaired_correct

    return {
        "strategy": strategy,
        "intervention": intervention,
        "intervention_plan": {
            "description": plan.description,
            "note": plan.note,
            "applications": [
                {"target_type": target.value, "target_id": target_id}
                for target, target_id in plan.applications
            ],
        },
        "capability_survival": survived,
        "baseline_correct": before_correct,
        "immediate_correct": after_correct,
        "repaired_correct": repaired_correct,
        "silent_forgetting": silent_forgetting,
        # The distinction that matters for the paper's claim: transient silent
        # forgetting is repaired and the system ends up correct; unrecovered
        # silent forgetting means the system is permanently wrong while still
        # reporting confidence it has not earned.  Only the second is collective
        # forgetting in the sense the paper argues is dangerous.
        "unrecovered_silent_forgetting": silent_forgetting and not survived,
        "transient_silent_forgetting": silent_forgetting and survived,
        "confidence_valid_support_gap": after.metrics.confidence_valid_support_gap,
        "post_intervention_confidence": after.metrics.current_confidence,
        "post_intervention_coverage": after.metrics.valid_support_coverage,
        "recovery_steps": recovery_steps,
        "recovery_events": recovery_events,
        "repair_reason": repair.reason if repair else "no repair policy",
        "repair_selected_node": repair.selected_node_id if repair else None,
        "bytes_moved": bytes_moved,
        "record_count": runtime.placement.record_count,
        "node_count": len(runtime.placement.node_ids),
        "redundancy_efficiency": round(
            (1.0 if survived else 0.0) / runtime.placement.record_count, 6
        ),
        "structural_diversity": structural_diversity(runtime.placement),
        "independent_support_before": _independent_support(before),
        "independent_support_after": _independent_support(after),
        "privacy": _privacy_audit(runtime, executions),
        "phases": {
            execution.phase: {
                "route": list(execution.route),
                "success": execution.synthesis.success,
                "answer_correct": execution.synthesis.answer == expected,
                "confidence": execution.synthesis.confidence,
                "covered_slots": list(execution.synthesis.covered_slots),
                "exported_bytes": execution.exported_bytes,
                "claim_count": len(execution.claims),
            }
            for execution in executions
        },
        "interventions_applied": to_jsonable(runtime.failure.records),
    }


# --------------------------------------------------------------------------
# Full matrix
# --------------------------------------------------------------------------


async def run_benchmark(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        for intervention in INTERVENTIONS:
            cell = await run_cell(seed, strategy, intervention)
            cells[f"{strategy}::{intervention}"] = cell

    per_strategy: dict[str, Any] = {}
    for strategy in STRATEGIES:
        rows = [cells[f"{strategy}::{i}"] for i in INTERVENTIONS]
        survived = sum(1 for row in rows if row["capability_survival"])
        silent = sum(1 for row in rows if row["silent_forgetting"])
        leak_free = all(row["privacy"]["leak_free"] for row in rows)
        holds_answer = rows[0]["privacy"]["single_node_holds_full_answer"]
        per_strategy[strategy] = {
            "description": STRATEGY_BUILDERS[strategy](generate_symbols(seed)).description,
            "capability_survival_rate": round(survived / len(INTERVENTIONS), 6),
            "survived_interventions": sorted(
                row["intervention"] for row in rows if row["capability_survival"]
            ),
            "lost_interventions": sorted(
                row["intervention"] for row in rows if not row["capability_survival"]
            ),
            "silent_forgetting_count": silent,
            "silent_forgetting_rate": round(silent / len(INTERVENTIONS), 6),
            "mean_recovery_steps": round(
                sum(row["recovery_steps"] for row in rows) / len(rows), 6
            ),
            "total_bytes_moved": sum(row["bytes_moved"] for row in rows),
            "mean_bytes_moved": round(sum(row["bytes_moved"] for row in rows) / len(rows), 6),
            "record_count": rows[0]["record_count"],
            "node_count": rows[0]["node_count"],
            "observed_min_failure_domain_cut": rows[0]["independent_support_before"][
                "minimal_failure_domain_cut"
            ],
            "structural_min_cut": rows[0]["structural_diversity"]["structural_min_cut"],
            "structural_root_count": rows[0]["structural_diversity"]["structural_root_count"],
            "unique_lineage_roots": rows[0]["independent_support_before"][
                "unique_lineage_root_count"
            ],
            "redundancy_efficiency": round(survived / len(INTERVENTIONS) / rows[0]["record_count"], 6),
            "privacy_preserved": leak_free and not holds_answer,
            "single_node_holds_full_answer": holds_answer,
            "leak_free": leak_free,
        }

    per_intervention: dict[str, Any] = {}
    for intervention in INTERVENTIONS:
        rows = [cells[f"{s}::{intervention}"] for s in STRATEGIES]
        per_intervention[intervention] = {
            "description": rows[0]["intervention_plan"]["description"],
            "strategies_surviving": sorted(
                row["strategy"] for row in rows if row["capability_survival"]
            ),
            "survival_rate": round(
                sum(1 for row in rows if row["capability_survival"]) / len(STRATEGIES), 6
            ),
            "silent_forgetting_count": sum(1 for row in rows if row["silent_forgetting"]),
        }

    return {
        "schema_version": "1.0",
        "benchmark_version": BENCHMARK_VERSION,
        "seed": seed,
        "strategies": list(STRATEGIES),
        "interventions": list(INTERVENTIONS),
        "matrix": {
            strategy: {
                intervention: cells[f"{strategy}::{intervention}"]["capability_survival"]
                for intervention in INTERVENTIONS
            }
            for strategy in STRATEGIES
        },
        "per_strategy": per_strategy,
        "per_intervention": per_intervention,
        "cells": cells,
    }


async def run_sweep(seed: int = DEFAULT_SEED, trials: int = 30) -> dict[str, Any]:
    """Aggregate capability survival over ``trials`` consecutive seeds.

    A single seed is not enough to characterise a stochastic policy.
    ``random_path_diversification`` survives or does not depending on the shuffle,
    so its single-seed cell is a coin flip reported as a fact.  Deterministic
    strategies return identical results every trial, which is itself the check
    that the sweep is not introducing noise of its own.
    """
    seeds = tuple(range(seed, seed + trials))
    per_cell: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        for intervention in INTERVENTIONS:
            survivals: list[bool] = []
            unrecovered: list[bool] = []
            steps: list[int] = []
            byte_costs: list[int] = []
            for trial_seed in seeds:
                cell = await run_cell(trial_seed, strategy, intervention)
                survivals.append(cell["capability_survival"])
                unrecovered.append(cell["unrecovered_silent_forgetting"])
                steps.append(cell["recovery_steps"])
                byte_costs.append(cell["bytes_moved"])
            count = len(survivals)
            rate = sum(survivals) / count
            per_cell[f"{strategy}::{intervention}"] = {
                "strategy": strategy,
                "intervention": intervention,
                "trials": count,
                "survival_count": sum(survivals),
                "survival_rate": round(rate, 6),
                "deterministic": len(set(survivals)) == 1,
                "unrecovered_silent_forgetting_rate": round(sum(unrecovered) / count, 6),
                "mean_recovery_steps": round(sum(steps) / count, 6),
                "mean_bytes_moved": round(sum(byte_costs) / count, 6),
            }

    per_strategy: dict[str, Any] = {}
    for strategy in STRATEGIES:
        rows = [per_cell[f"{strategy}::{i}"] for i in INTERVENTIONS]
        per_strategy[strategy] = {
            "capability_survival_rate": round(
                sum(row["survival_rate"] for row in rows) / len(rows), 6
            ),
            "unrecovered_silent_forgetting_rate": round(
                sum(row["unrecovered_silent_forgetting_rate"] for row in rows) / len(rows), 6
            ),
            "mean_bytes_moved": round(
                sum(row["mean_bytes_moved"] for row in rows) / len(rows), 6
            ),
            "fully_deterministic": all(row["deterministic"] for row in rows),
            "nondeterministic_cells": sorted(
                row["intervention"] for row in rows if not row["deterministic"]
            ),
        }

    return {
        "schema_version": "1.0",
        "benchmark_version": BENCHMARK_VERSION,
        "mode": "sweep",
        "seed_start": seed,
        "trials": trials,
        "seeds": list(seeds),
        "strategies": list(STRATEGIES),
        "interventions": list(INTERVENTIONS),
        "survival_matrix": {
            strategy: {
                intervention: per_cell[f"{strategy}::{intervention}"]["survival_rate"]
                for intervention in INTERVENTIONS
            }
            for strategy in STRATEGIES
        },
        "per_strategy": per_strategy,
        "cells": per_cell,
    }


def render_sweep_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"# Capability survival sweep ({result['trials']} seeds "
        f"from {result['seed_start']})"
    )
    lines.append("")
    lines.append("Mean capability survival rate per (strategy, intervention).")
    lines.append("")
    header = [i.replace("_", " ") for i in result["interventions"]]
    lines.append("| strategy | " + " | ".join(header) + " | mean |")
    lines.append("|" + "---|" * (len(header) + 2))
    for strategy in result["strategies"]:
        row = result["survival_matrix"][strategy]
        cells = " | ".join(f"{row[i]:.2f}" for i in result["interventions"])
        mean = result["per_strategy"][strategy]["capability_survival_rate"]
        lines.append(f"| `{strategy}` | {cells} | **{mean:.3f}** |")
    lines.append("")
    lines.append("| strategy | survival | unrecovered silent forgetting | "
                 "mean bytes | deterministic |")
    lines.append("|---|---|---|---|---|")
    for strategy in result["strategies"]:
        s = result["per_strategy"][strategy]
        det = "yes" if s["fully_deterministic"] else \
            "NO: " + ", ".join(s["nondeterministic_cells"])
        lines.append(
            f"| `{strategy}` | {s['capability_survival_rate']:.3f} | "
            f"{s['unrecovered_silent_forgetting_rate']:.3f} | "
            f"{s['mean_bytes_moved']:.0f} | {det} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_markdown(result: dict[str, Any]) -> str:
    """Human-readable summary of the matrix; the JSON stays the source of truth."""
    lines: list[str] = []
    lines.append(f"# Capability survival benchmark (seed {result['seed']})")
    lines.append("")
    lines.append(f"`{result['benchmark_version']}` · "
                 f"{len(result['strategies'])} strategies × {len(result['interventions'])} interventions")
    lines.append("")
    lines.append("## Capability survival matrix")
    lines.append("")
    short = {i: i.replace("_", " ") for i in result["interventions"]}
    lines.append("| strategy | " + " | ".join(short[i] for i in result["interventions"]) + " | rate |")
    lines.append("|" + "---|" * (len(result["interventions"]) + 2))
    for strategy in result["strategies"]:
        row = result["matrix"][strategy]
        cells = " | ".join("✅" if row[i] else "❌" for i in result["interventions"])
        rate = result["per_strategy"][strategy]["capability_survival_rate"]
        lines.append(f"| `{strategy}` | {cells} | **{rate:.2f}** |")
    lines.append("")
    lines.append("## Per-strategy summary")
    lines.append("")
    lines.append("| strategy | survival | silent forgetting | records | struct min-cut | "
                 "observed min-cut | roots | mean bytes | privacy preserved |")
    lines.append("|" + "---|" * 9)
    for strategy in result["strategies"]:
        s = result["per_strategy"][strategy]
        lines.append(
            f"| `{strategy}` | {s['capability_survival_rate']:.2f} | "
            f"{s['silent_forgetting_rate']:.2f} | {s['record_count']} | "
            f"{s['structural_min_cut']} | {s['observed_min_failure_domain_cut']} | "
            f"{s['structural_root_count']} | "
            f"{s['mean_bytes_moved']:.0f} | {'yes' if s['privacy_preserved'] else 'NO'} |"
        )
    lines.append("")
    lines.append("## Per-intervention summary")
    lines.append("")
    lines.append("| intervention | survival rate | strategies surviving |")
    lines.append("|---|---|---|")
    for intervention in result["interventions"]:
        s = result["per_intervention"][intervention]
        surviving = ", ".join(f"`{x}`" for x in s["strategies_surviving"]) or "_none_"
        lines.append(f"| `{intervention}` | {s['survival_rate']:.2f} | {surviving} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument(
        "--sweep",
        type=int,
        metavar="TRIALS",
        help="aggregate over TRIALS consecutive seeds instead of reporting one seed",
    )
    args = parser.parse_args(argv)
    if args.sweep:
        result = asyncio.run(run_sweep(args.seed, args.sweep))
        payload = (
            render_sweep_markdown(result) if args.format == "markdown"
            else json.dumps(result, sort_keys=True, indent=2) + "\n"
        )
    else:
        result = asyncio.run(run_benchmark(args.seed))
        payload = (
            render_markdown(result) if args.format == "markdown"
            else json.dumps(result, sort_keys=True, indent=2) + "\n"
        )
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
