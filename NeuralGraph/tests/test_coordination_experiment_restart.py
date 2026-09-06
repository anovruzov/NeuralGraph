"""Restart/persistence proof for the coordination reconstruction *experiment*.

Gate 1 ("minimal distributed reconstruction") requires restart/persistence
behavior, not just distributed reconstruction in memory.

``test_coordination_lineage.py`` already proves persistence at the *adapter*
level (``SQLiteRealAdapterTests``): one adapter's exported lineage survives a
stored round trip on a single store. It never runs the actual multi-node
reconstruction *experiment* -- the thing that combines claims from >=2
independent real per-node stores into one answer -- against real storage, and
it never closes and reopens storage to prove the result survives a restart,
not just a read from a store that has stayed open the whole time.

``coordination/experiment.py`` proves the opposite half: multi-node
reconstruction with reversible failure injection and repair strategies, but
entirely against ``MockMemoryNodeAdapter``, never real storage.

This module closes the remaining gap between the two with the smallest
fixture that still proves the claim: two independent NeuralGraph nodes, each
backed by its own real SQLite file, each holding exactly one half ("left" /
"right") of an answer that appears whole in neither store. The coordinator
reconstructs it from both. Storage is then closed and reopened from the same
files -- what a real process restart would do -- and reconstruction is re-run
and must match exactly, including the identifiers used to trace it.
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
)
from NeuralGraph.coordination.core import (
    CapabilityRegistry,
    ClaimNormalizer,
    FailureInjector,
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

CAPABILITY_ID = "restart_pair_reconstruction"
REQUIRED_SCOPE = "capability:restart-pair"
REQUIRED_SLOTS = ("left", "right")
SESSION_KEY = "restart-experiment-session"
EMBEDDING = [0.42, 0.17, 0.9]

NODE_A_ID = "restart-node-a"
NODE_B_ID = "restart-node-b"
EPISODE_A_ID = "episode-left"
EPISODE_B_ID = "episode-right"
ORIGIN_A_ID = "message-left-origin"
ORIGIN_B_ID = "message-right-origin"

LEFT_VALUE = "LEFT_HALF_9F2Q"
RIGHT_VALUE = "RIGHT_HALF_3KX7"
EXPECTED_ANSWER = f"{LEFT_VALUE}:{RIGHT_VALUE}"


async def _seed_slot_episode(
    storage: SQLiteNeuralGraphStorage,
    episode_id: str,
    origin_id: str,
    slot: str,
    value: str,
) -> None:
    """Persist one derived episode plus the origin message it derives from.

    Mirrors the real consolidation shape (hierarchy.py): the episode's own
    ``source_memory_ids`` names its origin, and a ``HIERARCHY`` edge runs
    PARENT -> CHILD from the episode to that origin. The origin itself
    records no further parent, so a lineage walk resolves it as a root.
    """
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


def _episode_retrieve(storage: SQLiteNeuralGraphStorage):
    """Real retrieval callback: the storage backend's own vector search.

    ``vector_search`` already returns ``(node, similarity)`` pairs, exactly
    the shape ``NeuralGraphMemoryAdapter.query`` expects -- no synthetic
    confidence is invented here.
    """

    async def retrieve(_request: QueryRequest):
        return await storage.vector_search(
            list(EMBEDDING), SESSION_KEY, limit=8, layer_filter=[NodeLayer.EPISODE]
        )

    return retrieve


def _allow_everything(_node, _authorization) -> PolicyStatus:
    return PolicyStatus.ALLOWED


def _capability(node_id: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=CAPABILITY_ID,
        node_id=node_id,
        description="local NeuralGraph retrieval over real storage",
        query_types=("composition",),
        policy_scope=(REQUIRED_SCOPE,),
        availability=Availability.AVAILABLE,
    )


def _adapter(node_id: str, storage: SQLiteNeuralGraphStorage, clock) -> NeuralGraphMemoryAdapter:
    return NeuralGraphMemoryAdapter(
        node_id,
        _capability(node_id),
        _episode_retrieve(storage),
        _allow_everything,
        clock,
        lineage_resolver=StorageLineageResolver(storage),
        claim_projection=_slot_projection,
    )


def _query(seed: int) -> QueryRequest:
    clock = LogicalClock(seed)
    issued_at = clock.now()
    return QueryRequest(
        query_id=f"restart_query_{seed}",
        content="Reconstruct the two-part answer split across independent nodes",
        requested_capability=CAPABILITY_ID,
        requester_id="experiment-controller",
        issued_at=issued_at,
        valid_at=issued_at,
        authorization=AuthorizationContext(
            scopes=(REQUIRED_SCOPE,),
            allowed_node_ids=(NODE_A_ID, NODE_B_ID),
        ),
        budget=QueryBudget(max_nodes=2, max_claims=8, max_verifications=0),
    )


async def _reconstruct(storage_a, storage_b, seed: int, phase: str):
    """Build a fresh coordinator over the given storages and execute once.

    Every collaborator -- clock, traces, failure injector, registry, router,
    coordinator -- is rebuilt from scratch here. Nothing is carried over
    between calls except what actually lives in the two SQLite files, so a
    second call against reopened storage is indistinguishable from what a
    freshly started process would produce.
    """
    clock = LogicalClock(seed)
    traces = TraceLogger(f"restart_run_{seed}", clock)
    failure = FailureInjector(f"restart_run_{seed}", seed, clock, traces)
    registry = CapabilityRegistry()
    registry.register(_adapter(NODE_A_ID, storage_a, clock.now))
    registry.register(_adapter(NODE_B_ID, storage_b, clock.now))
    router = Router(registry, failure)
    coordinator = TesseractCoordinator(
        registry=registry,
        router=router,
        normalizer=ClaimNormalizer(),
        synthesizer=RuleBasedSynthesizer(REQUIRED_SLOTS),
        analyzer=LineageAnalyzer(),
        traces=traces,
    )
    request = _query(seed)
    return await coordinator.execute(request, phase=phase, preferred_node_ids=(NODE_A_ID, NODE_B_ID))


class ReconstructionSurvivesRestartTests(unittest.IsolatedAsyncioTestCase):
    """Real per-node SQLite storage; reconstruction must survive a restart."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path_a = Path(self._tmpdir.name) / "node_a.db"
        self.path_b = Path(self._tmpdir.name) / "node_b.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    async def _seed_both_nodes(self) -> None:
        storage_a = SQLiteNeuralGraphStorage(db_path=self.path_a)
        storage_b = SQLiteNeuralGraphStorage(db_path=self.path_b)
        try:
            await _seed_slot_episode(storage_a, EPISODE_A_ID, ORIGIN_A_ID, "left", LEFT_VALUE)
            await _seed_slot_episode(storage_b, EPISODE_B_ID, ORIGIN_B_ID, "right", RIGHT_VALUE)
        finally:
            storage_a.close()
            storage_b.close()

    async def test_no_single_node_stores_the_full_answer(self) -> None:
        """Mechanical privacy check, mirroring fixture.assert_fixture_privacy."""
        await self._seed_both_nodes()
        storage_a = SQLiteNeuralGraphStorage(db_path=self.path_a)
        storage_b = SQLiteNeuralGraphStorage(db_path=self.path_b)
        try:
            episode_a = await storage_a.get_node(EPISODE_A_ID)
            episode_b = await storage_b.get_node(EPISODE_B_ID)
        finally:
            storage_a.close()
            storage_b.close()
        self.assertNotEqual(episode_a.content, EXPECTED_ANSWER)
        self.assertNotEqual(episode_b.content, EXPECTED_ANSWER)
        self.assertNotIn(RIGHT_VALUE, episode_a.content)
        self.assertNotIn(LEFT_VALUE, episode_b.content)

    async def test_reconstruction_survives_a_storage_restart(self) -> None:
        await self._seed_both_nodes()

        storage_a = SQLiteNeuralGraphStorage(db_path=self.path_a)
        storage_b = SQLiteNeuralGraphStorage(db_path=self.path_b)
        try:
            before = await _reconstruct(storage_a, storage_b, seed=7001, phase="before_restart")
        finally:
            # The actual restart: close both connections, then open brand new
            # ``SQLiteNeuralGraphStorage`` instances against the same files.
            # Nothing from the first storage objects is reused below.
            storage_a.close()
            storage_b.close()

        self.assertTrue(before.synthesis.success)
        self.assertEqual(before.synthesis.answer, EXPECTED_ANSWER)
        self.assertEqual(before.synthesis.covered_slots, REQUIRED_SLOTS)
        # No single node produced the whole answer: two distinct producers,
        # each contributing exactly one slot.
        self.assertEqual(len(set(before.synthesis.selected_node_ids)), 2)
        self.assertEqual(before.metrics.unique_lineage_root_count, 2)

        restarted_storage_a = SQLiteNeuralGraphStorage(db_path=self.path_a)
        restarted_storage_b = SQLiteNeuralGraphStorage(db_path=self.path_b)
        try:
            after = await _reconstruct(
                restarted_storage_a, restarted_storage_b, seed=7001, phase="after_restart"
            )
        finally:
            restarted_storage_a.close()
            restarted_storage_b.close()

        self.assertTrue(after.synthesis.success)
        self.assertEqual(after.synthesis.answer, EXPECTED_ANSWER)
        self.assertEqual(after.synthesis.confidence, before.synthesis.confidence)
        self.assertEqual(after.synthesis.covered_slots, before.synthesis.covered_slots)

        # Claim and trace identity are derived from (query_id, node_id,
        # memory_id) only -- never from wall-clock time or connection
        # identity -- so a restart with the same seed must reproduce them
        # exactly. This is the trace-stability property Gate 1 needs:
        # equivalent runs must not silently mint new identities.
        self.assertEqual(
            {claim.claim_id for claim in before.claims},
            {claim.claim_id for claim in after.claims},
        )
        before_roots = {claim.claim_id: claim.lineage_root_ids for claim in before.claims}
        after_roots = {claim.claim_id: claim.lineage_root_ids for claim in after.claims}
        self.assertEqual(before_roots, after_roots)
        self.assertEqual(
            {claim.lineage_root_ids for claim in after.claims},
            {(ORIGIN_A_ID,), (ORIGIN_B_ID,)},
        )


if __name__ == "__main__":
    unittest.main()
