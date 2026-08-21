"""The headline benchmark result, reproduced against real SQLite storage.

The capability-survival benchmark runs entirely on ``MockMemoryNodeAdapter``.
That is the first thing a reviewer will attack, and rightly: a result that only
holds against a mock of your own design is not a result about a system.

This module re-runs the load-bearing cell of the matrix -- replica count versus
lineage-independent repair under a lineage-root failure -- against four real
``SQLiteNeuralGraphStorage`` files, one per node, driven through the real
``NeuralGraphMemoryAdapter`` and the real ``StorageLineageResolver``.  Lineage
is not declared by the fixture here; it is *derived* from stored HIERARCHY edges
and ``source_memory_ids`` the way it would be in a deployment.

The four-node structure mirrors ``coordination/benchmark.py``:

    node-a  left   derived from origin O1   (root R1)
    node-b  right  derived from origin O2   (root R2)
    node-c  right  derived from origin O2   -- apparent replica, same root
    node-d  right  derived from origin O3   (root R3, genuinely independent)

Nodes b and c live in separate database files and share no rows.  What makes
them correlated is that both derive from the same upstream origin id, which is
exactly how correlated provenance appears in a real deployment: not as a shared
row, but as a shared ancestor.

WHAT THIS DOES NOT SHOW, stated plainly: ``StorageLineageResolver`` reports
``failure_domains=()`` because NeuralGraph records no failure-domain concept.
Every domain-derived metric in the benchmark -- coalition counts, minimum
failure-domain cut, and therefore the ``worst_single_domain_failure`` row -- is
structurally unavailable against real storage and is NOT reproduced here.  Only
the root-level mechanism is.  Closing that gap is a modeling decision, not a
code fix; see ``state.md``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from NeuralGraph.coordination.adapters import NeuralGraphMemoryAdapter
from NeuralGraph.coordination.contracts import (
    AuthorizationContext,
    Availability,
    CapabilityDescriptor,
    PolicyStatus,
    QueryBudget,
    QueryRequest,
    VerificationRequest,
)
from NeuralGraph.coordination.core import (
    CapabilityRegistry,
    ClaimNormalizer,
    LineageAnalyzer,
    LogicalClock,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
    TraceLogger,
)
from NeuralGraph.coordination.storage_adapter import StorageLineageResolver
from NeuralGraph.data_types import EdgeType, NeuralEdge, NeuralNode, NodeLayer
from NeuralGraph.sqlite_storage import SQLiteNeuralGraphStorage

CAPABILITY_ID = "real_storage_pair_reconstruction"
REQUIRED_SCOPE = "capability:real-pair"
REQUIRED_SLOTS = ("left", "right")
SESSION_KEY = "real-benchmark-session"
EMBEDDING = [0.31, 0.55, 0.78]
SEED = 20260813

LEFT_VALUE = "LX_REAL_STORAGE_LEFT"
RIGHT_VALUE = "RX_REAL_STORAGE_RIGHT"
EXPECTED_ANSWER = f"{LEFT_VALUE}:{RIGHT_VALUE}"

# node -> (slot, value, origin id).  b and c share origin O2: correlated
# provenance without a shared row.
LAYOUT = {
    "node-a": ("left", LEFT_VALUE, "origin-O1"),
    "node-b": ("right", RIGHT_VALUE, "origin-O2"),
    "node-c": ("right", RIGHT_VALUE, "origin-O2"),
    "node-d": ("right", RIGHT_VALUE, "origin-O3"),
}


async def _seed(storage: SQLiteNeuralGraphStorage, node_id: str) -> None:
    """Persist one derived episode plus the origin it derives from.

    Mirrors the real consolidation shape: the episode names its origin in
    ``source_memory_ids`` and a HIERARCHY edge runs episode -> origin.  The
    origin records no further parent, so a lineage walk resolves it as a root.
    """
    slot, value, origin_id = LAYOUT[node_id]
    episode_id = f"episode-{node_id}"
    await storage.save_node(NeuralNode(
        node_id=episode_id,
        layer=NodeLayer.EPISODE,
        content=value,
        embedding=list(EMBEDDING),
        session_key=SESSION_KEY,
        child_ids=[origin_id],
        source_memory_ids=[origin_id],
        metadata={"slot": slot},
    ))
    await storage.save_node(NeuralNode(
        node_id=origin_id,
        layer=NodeLayer.MESSAGE,
        content=f"raw-{value}",
        embedding=list(EMBEDDING),
        session_key=SESSION_KEY,
        parent_id=episode_id,
    ))
    await storage.save_edge(NeuralEdge(
        edge_id=f"edge-{episode_id}-{origin_id}",
        source_id=episode_id,
        target_id=origin_id,
        edge_type=EdgeType.HIERARCHY,
        base_weight=1.0,
        confidence=1.0,
    ))


def _slot_projection(node: NeuralNode) -> dict[str, str]:
    return {"slot": node.metadata.get("slot"), "value": node.content}


def _retrieve(storage: SQLiteNeuralGraphStorage):
    async def retrieve(_request: QueryRequest):
        return await storage.vector_search(
            list(EMBEDDING), SESSION_KEY, limit=8, layer_filter=[NodeLayer.EPISODE]
        )
    return retrieve


class RootFailurePolicy:
    """Failure injection for the real adapter, applied at its policy boundary.

    ``NeuralGraphMemoryAdapter`` takes a policy filter rather than a failure
    view, so an intervention reaches it the same way a real revocation would:
    policy runs before propagation, and a denied record fails closed to a
    payload-free trace.  Blocking is keyed on *derived* lineage -- the origin
    the stored node actually declares -- not on anything the fixture asserts,
    so this blocks exactly the nodes whose real provenance touches a failed root.
    """

    def __init__(self) -> None:
        self.failed_roots: set[str] = set()

    def fail_root(self, root_id: str) -> None:
        self.failed_roots.add(root_id)

    def __call__(self, node: NeuralNode, _authorization) -> PolicyStatus:
        declared = set(getattr(node, "source_memory_ids", ()) or ())
        if declared & self.failed_roots:
            return PolicyStatus.DENIED
        return PolicyStatus.ALLOWED


def _adapter(node_id, storage, clock, policy) -> NeuralGraphMemoryAdapter:
    return NeuralGraphMemoryAdapter(
        node_id,
        CapabilityDescriptor(
            capability_id=CAPABILITY_ID,
            node_id=node_id,
            description="local NeuralGraph retrieval over real storage",
            query_types=("composition",),
            policy_scope=(REQUIRED_SCOPE,),
            availability=Availability.AVAILABLE,
        ),
        _retrieve(storage),
        policy,
        clock,
        lineage_resolver=StorageLineageResolver(storage),
        claim_projection=_slot_projection,
    )


def _query(route_limit: int) -> QueryRequest:
    issued_at = LogicalClock(SEED).now()
    return QueryRequest(
        query_id=f"real_bench_query_{SEED}",
        content="Reconstruct the two-part answer split across independent real nodes",
        requested_capability=CAPABILITY_ID,
        requester_id="benchmark-controller",
        issued_at=issued_at,
        valid_at=issued_at,
        authorization=AuthorizationContext(
            scopes=(REQUIRED_SCOPE,),
            allowed_node_ids=tuple(sorted(LAYOUT)),
        ),
        budget=QueryBudget(max_nodes=route_limit, max_claims=8, max_verifications=2),
    )


class RealStorageBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.policy = RootFailurePolicy()
        self.storages: dict[str, SQLiteNeuralGraphStorage] = {}
        for node_id in sorted(LAYOUT):
            storage = SQLiteNeuralGraphStorage(db_path=root / f"{node_id}.db")
            await _seed(storage, node_id)
            self.storages[node_id] = storage

    async def asyncTearDown(self) -> None:
        for storage in self.storages.values():
            close = getattr(storage, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        self._tmp.cleanup()

    def _coordinator(self):
        clock = LogicalClock(SEED)
        traces = TraceLogger(f"real_bench_{SEED}", clock)
        registry = CapabilityRegistry()
        for node_id in sorted(LAYOUT):
            registry.register(
                _adapter(node_id, self.storages[node_id], clock.now, self.policy)
            )
        failure = _NullFailureView()
        coordinator = TesseractCoordinator(
            registry=registry,
            router=Router(registry, failure),
            normalizer=ClaimNormalizer(),
            synthesizer=RuleBasedSynthesizer(REQUIRED_SLOTS),
            analyzer=LineageAnalyzer(),
            traces=traces,
        )
        return registry, coordinator

    # -- the structural preconditions ------------------------------------

    async def test_no_single_real_store_holds_the_full_answer(self):
        """The invariant the whole experiment depends on, checked against storage."""
        for node_id, storage in self.storages.items():
            nodes = await storage.vector_search(
                list(EMBEDDING), SESSION_KEY, limit=16, layer_filter=[NodeLayer.EPISODE]
            )
            slots = {node.metadata.get("slot") for node, _ in nodes}
            with self.subTest(node=node_id):
                self.assertNotEqual(slots, set(REQUIRED_SLOTS))
                for node, _ in nodes:
                    self.assertNotEqual(node.content, EXPECTED_ANSWER)

    async def test_lineage_roots_are_derived_from_storage_not_declared(self):
        """b and c must resolve to the same root; d to a different one."""
        resolved = {}
        for node_id, storage in self.storages.items():
            resolver = StorageLineageResolver(storage)
            node = await storage.get_node(f"episode-{node_id}")
            facts = await resolver(node)
            resolved[node_id] = facts["lineage_root_ids"]

        self.assertEqual(resolved["node-b"], resolved["node-c"])
        self.assertNotEqual(resolved["node-b"], resolved["node-d"])
        self.assertNotEqual(resolved["node-a"], resolved["node-b"])
        for node_id, roots in resolved.items():
            with self.subTest(node=node_id):
                self.assertTrue(roots, f"{node_id} resolved no lineage root at all")

    async def test_failure_domains_are_empty_against_real_storage(self):
        """Documented limitation, asserted so it cannot regress into a silent fake.

        If this ever starts returning domains, something began synthesising a
        failure-domain model the graph does not record, and every domain-derived
        benchmark metric would silently become fiction.
        """
        storage = self.storages["node-b"]
        node = await storage.get_node("episode-node-b")
        facts = await StorageLineageResolver(storage)(node)
        self.assertEqual(facts["failure_domains"], ())

    # -- the headline result ---------------------------------------------

    async def test_reconstruction_succeeds_before_any_failure(self):
        _registry, coordinator = self._coordinator()
        execution = await coordinator.execute(
            _query(route_limit=3), phase="before", preferred_node_ids=tuple(sorted(LAYOUT)),
        )
        self.assertTrue(execution.synthesis.success)
        self.assertEqual(execution.synthesis.answer, EXPECTED_ANSWER)

    async def test_replica_count_does_not_survive_a_real_root_failure(self):
        """b and c are separate databases, and both die to one origin failing.

        This is the paper's central negative claim reproduced on real storage:
        the redundancy is apparent, because the copies share an ancestor.
        """
        self.policy.fail_root("origin-O2")
        _registry, coordinator = self._coordinator()
        # Route to the correlated replicas only, as a replica-count strategy would.
        execution = await coordinator.execute(
            _query(route_limit=3), phase="after",
            preferred_node_ids=("node-a", "node-b", "node-c"),
        )
        self.assertFalse(execution.synthesis.success)
        self.assertEqual(list(execution.synthesis.covered_slots), ["left"])

    async def test_silent_forgetting_signature_appears_on_real_storage(self):
        """Capability lost, confidence still above genuine coverage."""
        self.policy.fail_root("origin-O2")
        _registry, coordinator = self._coordinator()
        execution = await coordinator.execute(
            _query(route_limit=3), phase="after",
            preferred_node_ids=("node-a", "node-b", "node-c"),
        )
        self.assertFalse(execution.synthesis.success)
        self.assertGreater(
            execution.metrics.current_confidence,
            execution.metrics.valid_support_coverage,
        )
        self.assertGreater(execution.metrics.confidence_valid_support_gap, 0.0)

    async def test_lineage_independent_node_survives_the_same_failure(self):
        """Routing to the independently rooted node restores the capability."""
        self.policy.fail_root("origin-O2")
        _registry, coordinator = self._coordinator()
        execution = await coordinator.execute(
            _query(route_limit=3), phase="after_repair",
            preferred_node_ids=("node-a", "node-d", "node-b"),
        )
        self.assertTrue(execution.synthesis.success)
        self.assertEqual(execution.synthesis.answer, EXPECTED_ANSWER)

    async def test_verification_distinguishes_correlated_from_independent_support(self):
        """The mechanism lineage-aware repair relies on, against real lineage.

        Excluding the failed root must reject b and c -- whose independence is
        only apparent -- while accepting d.
        """
        self.policy.fail_root("origin-O2")
        registry, _coordinator = self._coordinator()
        request = VerificationRequest(
            verification_id="real_verification_01",
            query=_query(route_limit=3),
            required_slots=("right",),
            excluded_lineage_roots=("origin-O2",),
            excluded_failure_domains=(),
        )
        for node_id in ("node-b", "node-c"):
            with self.subTest(node=node_id):
                result = await registry.adapter_for(node_id).verify(request)
                self.assertFalse(result.valid, f"{node_id} should be rejected as correlated")
        independent = await registry.adapter_for("node-d").verify(request)
        self.assertTrue(independent.valid)
        self.assertIn("right", independent.supported_slots)

    async def test_denials_carry_no_payload_on_the_real_adapter(self):
        self.policy.fail_root("origin-O2")
        _registry, coordinator = self._coordinator()
        execution = await coordinator.execute(
            _query(route_limit=3), phase="after",
            preferred_node_ids=("node-a", "node-b", "node-c"),
        )
        denials = [
            export for export in execution.exports
            if export.trace.policy_status is not PolicyStatus.ALLOWED
        ]
        self.assertTrue(denials, "the intervention must actually have denied something")
        for export in denials:
            self.assertEqual(export.claims, ())
            self.assertEqual(export.trace.memory_ids, ())
            self.assertEqual(export.trace.lineage_root_ids, ())
            self.assertEqual(export.trace.source_ids, ())
            self.assertEqual(export.trace.edge_path, ())
        self.assertEqual(execution.policy_violations, 0)

    async def test_result_survives_closing_and_reopening_every_store(self):
        """A real restart, not a reused connection."""
        self.policy.fail_root("origin-O2")
        _registry, coordinator = self._coordinator()
        first = await coordinator.execute(
            _query(route_limit=3), phase="after_repair",
            preferred_node_ids=("node-a", "node-d", "node-b"),
        )

        paths = {}
        for node_id, storage in self.storages.items():
            paths[node_id] = Path(storage._db_path)
            close = getattr(storage, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        self.storages = {
            node_id: SQLiteNeuralGraphStorage(db_path=path)
            for node_id, path in sorted(paths.items())
        }

        _registry2, coordinator2 = self._coordinator()
        second = await coordinator2.execute(
            _query(route_limit=3), phase="after_repair",
            preferred_node_ids=("node-a", "node-d", "node-b"),
        )
        self.assertEqual(first.synthesis.answer, second.synthesis.answer)
        self.assertEqual(first.synthesis.confidence, second.synthesis.confidence)
        self.assertEqual(first.synthesis.selected_claim_ids, second.synthesis.selected_claim_ids)


class _NullFailureView:
    """Route-level failure is not exercised here; interventions run through policy."""

    def route_available(self, _node_id: str) -> bool:
        return True

    def blocked_reason(self, **_kwargs) -> str | None:
        return None


if __name__ == "__main__":
    unittest.main()
