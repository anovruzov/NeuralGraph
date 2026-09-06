"""Lineage resolution and export tests against real NeuralGraph storage.

`StorageLineageResolver` (NeuralGraph/coordination/storage_adapter.py) is the only
part of the coordination package that reads a real graph.  Everything here runs
against actual `InMemoryNeuralGraphStorage` and `SQLiteNeuralGraphStorage`
instances holding real `NeuralNode` and `NeuralEdge` objects: no storage double,
no patched method, no faked retrieval.  The SQLite backend always receives an
explicit temporary `db_path`, because its default constructor argument points at
`~/.memmachine/memories.db` and no test may write there.

Three properties are under test:

* Direction.  A `HIERARCHY` edge runs PARENT -> CHILD (hierarchy.py:302-334), so
  the derivation parents of a node are the *targets* of its own outgoing edges.
  Reading `parent_id` or `get_edges_to` instead would report a summary as an
  ancestor of the very messages it was built from.
* Honesty.  Truncated walks, cycles and dangling ids under-report; they never
  invent a lineage root and never silently drop a recorded derivation fact.
* Containment.  Lineage crosses the boundary as identifier tuples only.  Node
  content, metadata and embeddings stay behind, including inside exception
  messages and canonical serialization.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

from NeuralGraph.coordination.adapters import NeuralGraphMemoryAdapter
from NeuralGraph.coordination.contracts import (
    AuthorizationContext,
    Availability,
    CapabilityDescriptor,
    PolicyStatus,
)
from NeuralGraph.coordination.core import (
    CapabilityRegistry,
    ClaimNormalizer,
    LineageAnalyzer,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
    canonical_json,
)
from NeuralGraph.coordination.fixture import REQUIRED_SCOPE, REQUIRED_SLOTS, build_runtime
from NeuralGraph.coordination.storage_adapter import StorageLineageResolver
from NeuralGraph.data_types import (
    ConsolidationState,
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
)
from NeuralGraph.sqlite_storage import SQLiteNeuralGraphStorage
from NeuralGraph.storage import InMemoryNeuralGraphStorage

SESSION_KEY = "coordination-lineage-session"
REAL_NODE_ID = "real-node"

MESSAGE_IDS = ("message-1", "message-2")
EPISODE_ID = "episode-1"
TOPIC_ID = "topic-1"
SECOND_EPISODE_ID = "episode-2"
SECOND_MESSAGE_ID = "message-3"
TOPIC_EDGE_ID = f"edge-{TOPIC_ID}-{EPISODE_ID}"
EPISODE_EDGE_IDS = tuple(f"edge-{EPISODE_ID}-{message_id}" for message_id in MESSAGE_IDS)

# Payload markers.  Only EPISODE_SUMMARY is allowed to cross the boundary, and
# only as the default `{"text": ...}` claim projection of the retrieved node.
EPISODE_SUMMARY = "EPISODE_SUMMARY_TEXT_5QV"
SECOND_EPISODE_SUMMARY = "EPISODE_SUMMARY_TEXT_2ND"
MESSAGE_CONTENTS = ("RAW_MESSAGE_CONTENT_7X1", "RAW_MESSAGE_CONTENT_7X2")
SECOND_MESSAGE_CONTENT = "RAW_MESSAGE_CONTENT_7X3"
EPISODE_METADATA_NOTE = "EPISODE_METADATA_3QW"
MESSAGE_METADATA_NOTE = "MESSAGE_METADATA_8ZR"
EMBEDDING = [0.9876543, 0.1234567, 0.5]
SECOND_EMBEDDING = [0.8765432, 0.2345678, 0.5]

PRIVATE_PAYLOAD_MARKERS = (
    MESSAGE_CONTENTS[0],
    MESSAGE_CONTENTS[1],
    SECOND_MESSAGE_CONTENT,
    EPISODE_METADATA_NOTE,
    MESSAGE_METADATA_NOTE,
    "0.9876543",
    "0.1234567",
    "0.8765432",
)


# =============================================================================
# Real graph builders (mirrors of hierarchy.py)
# =============================================================================


def make_message(node_id: str, content: str) -> NeuralNode:
    return NeuralNode(
        node_id=node_id,
        layer=NodeLayer.MESSAGE,
        content=content,
        embedding=list(EMBEDDING),
        session_key=SESSION_KEY,
        metadata={"note": MESSAGE_METADATA_NOTE},
    )


def make_episode(
    node_id: str,
    messages: list[NeuralNode],
    summary: str,
    embedding: list[float],
    source_memory_ids: list[str] | None = None,
) -> NeuralNode:
    """An episode node with the fields hierarchy.py:302-314 sets."""
    return NeuralNode(
        node_id=node_id,
        layer=NodeLayer.EPISODE,
        content=summary,
        embedding=list(embedding),
        session_key=SESSION_KEY,
        child_ids=[message.node_id for message in messages],
        source_memory_ids=(
            [message.node_id for message in messages]
            if source_memory_ids is None
            else list(source_memory_ids)
        ),
        metadata={"note": EPISODE_METADATA_NOTE},
    )


async def save_hierarchy_edge(storage, edge_id: str, parent_id: str, child_id: str) -> None:
    """Write a HIERARCHY edge exactly as hierarchy.py does: PARENT -> CHILD."""
    await storage.save_edge(NeuralEdge(
        edge_id=edge_id,
        source_id=parent_id,
        target_id=child_id,
        edge_type=EdgeType.HIERARCHY,
        base_weight=1.0,
        confidence=1.0,
    ))


async def consolidate_into_episode(storage, episode: NeuralNode, messages: list[NeuralNode]) -> None:
    """Mirror of hierarchy.py:302-334.

    The messages point UP at the episode through `parent_id`, while the edge runs
    DOWN from the episode to each message.  Both directions are written here so a
    resolver that follows the wrong one is caught.
    """
    await storage.save_node(episode)
    for message in messages:
        message.parent_id = episode.node_id
        message.consolidation_state = ConsolidationState.ARCHIVED
        await storage.save_node(message)
        await save_hierarchy_edge(
            storage, f"edge-{episode.node_id}-{message.node_id}", episode.node_id, message.node_id
        )


# `SQLiteNeuralGraphStorage` does not persist `NeuralNode.updated_at` or
# `NeuralEdge.created_at`, so each read regenerates them from `datetime.now()`.
# They are excluded below so a snapshot compares durable state only; every field
# a lineage walk could plausibly disturb (activation_count, ltp_boost,
# last_activated, heat_score, activation_level) is persisted and still compared.
NON_DURABLE_NODE_FIELDS = ("updated_at",)
NON_DURABLE_EDGE_FIELDS = ("created_at",)


def _durable(record: dict, dropped: tuple[str, ...]) -> dict:
    return {key: value for key, value in record.items() if key not in dropped}


async def snapshot_storage(storage) -> tuple[str, str]:
    """Serialize every stored node and edge, for read-only verification."""
    nodes = sorted(await storage.get_all_nodes(SESSION_KEY), key=lambda node: node.node_id)
    edges = sorted(await storage.get_all_edges(SESSION_KEY), key=lambda edge: edge.edge_id)
    return (
        json.dumps(
            [_durable(node.to_dict(), NON_DURABLE_NODE_FIELDS) for node in nodes],
            sort_keys=True, default=str,
        ),
        json.dumps(
            [_durable(edge.to_dict(), NON_DURABLE_EDGE_FIELDS) for edge in edges],
            sort_keys=True, default=str,
        ),
    )


# =============================================================================
# Adapter helpers
# =============================================================================


def allow_everything(_node, _authorization):
    return PolicyStatus.ALLOWED


def deny_everything(_node, _authorization):
    return PolicyStatus.DENIED


def make_request(runtime):
    """The fixture query, authorized for the real-storage node under test."""
    return replace(
        runtime.fixture.query,
        authorization=AuthorizationContext(
            scopes=(REQUIRED_SCOPE,), allowed_node_ids=(REAL_NODE_ID,)
        ),
    )


def make_capability(runtime):
    return CapabilityDescriptor(
        capability_id=runtime.fixture.query.requested_capability,
        node_id=REAL_NODE_ID,
        description="local NeuralGraph retrieval over real storage",
        query_types=("composition",),
        policy_scope=(REQUIRED_SCOPE,),
        availability=Availability.AVAILABLE,
    )


def make_adapter(runtime, retrieve, policy=allow_everything, **kwargs):
    return NeuralGraphMemoryAdapter(
        REAL_NODE_ID,
        make_capability(runtime),
        retrieve,
        policy,
        runtime.clock.now,
        **kwargs,
    )


def episode_search(storage):
    """Real retrieval callback: the storage backend's own vector search."""

    async def retrieve(_request):
        return await storage.vector_search(
            list(EMBEDDING), SESSION_KEY, limit=8, layer_filter=[NodeLayer.EPISODE]
        )

    return retrieve


# =============================================================================
# Backends.  Every resolver and binding test runs once per real backend.
# =============================================================================


class _InMemoryBackend:
    backend = "in-memory"

    def setUp(self) -> None:
        super().setUp()
        self.storage = InMemoryNeuralGraphStorage()


class _SQLiteBackend:
    backend = "sqlite"

    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        # Explicit temp path: the default is ~/.memmachine/memories.db.
        self.db_path = Path(self._tmpdir.name) / "coordination_lineage_test.db"
        self.storage = SQLiteNeuralGraphStorage(db_path=self.db_path)

    def tearDown(self) -> None:
        self.storage.close()
        self._tmpdir.cleanup()
        super().tearDown()


class RealGraphCase:
    """Graph builders shared by the resolver and adapter suites."""

    async def build_episode_graph(self, source_memory_ids=None):
        messages = [
            make_message(message_id, content)
            for message_id, content in zip(MESSAGE_IDS, MESSAGE_CONTENTS)
        ]
        episode = make_episode(
            EPISODE_ID, messages, EPISODE_SUMMARY, EMBEDDING, source_memory_ids
        )
        await consolidate_into_episode(self.storage, episode, messages)
        return episode, messages

    async def build_second_episode_graph(self):
        message = make_message(SECOND_MESSAGE_ID, SECOND_MESSAGE_CONTENT)
        episode = make_episode(
            SECOND_EPISODE_ID, [message], SECOND_EPISODE_SUMMARY, SECOND_EMBEDDING
        )
        await consolidate_into_episode(self.storage, episode, [message])
        return episode, message

    async def build_topic_graph(self):
        """messages -> episode -> topic, exactly as hierarchy.py:470-505 writes it.

        The topic records `child_ids` and a HIERARCHY edge but, unlike the episode
        level, no `source_memory_ids`.  Edges are therefore the only evidence of
        its derivation.
        """
        episode, messages = await self.build_episode_graph()
        topic = NeuralNode(
            node_id=TOPIC_ID,
            layer=NodeLayer.TOPIC,
            content="topic summary",
            embedding=list(EMBEDDING),
            session_key=SESSION_KEY,
            child_ids=[EPISODE_ID],
        )
        await self.storage.save_node(topic)
        episode.parent_id = TOPIC_ID
        await self.storage.save_node(episode)
        await save_hierarchy_edge(self.storage, TOPIC_EDGE_ID, TOPIC_ID, EPISODE_ID)
        return topic, episode, messages

    async def build_derivation_chain(self):
        """persona <- topic <- episode <- message, one derivation parent each."""
        layers = (NodeLayer.MESSAGE, NodeLayer.EPISODE, NodeLayer.TOPIC, NodeLayer.PERSONA)
        chain = []
        for index, layer in enumerate(layers, 1):
            node = NeuralNode(
                node_id=f"chain-{index}",
                layer=layer,
                content=f"chain level {index}",
                session_key=SESSION_KEY,
            )
            await self.storage.save_node(node)
            chain.append(node)
        for child, parent in zip(chain, chain[1:]):
            await save_hierarchy_edge(
                self.storage,
                f"edge-{parent.node_id}-{child.node_id}",
                parent.node_id,
                child.node_id,
            )
        return chain

    async def build_cycle(self):
        """A derivation cycle: each node records the other as its parent."""
        for node_id in ("cycle-a", "cycle-b"):
            await self.storage.save_node(NeuralNode(
                node_id=node_id,
                layer=NodeLayer.EPISODE,
                content=f"cycle node {node_id}",
                session_key=SESSION_KEY,
            ))
        await save_hierarchy_edge(self.storage, "edge-cycle-a-cycle-b", "cycle-a", "cycle-b")
        await save_hierarchy_edge(self.storage, "edge-cycle-b-cycle-a", "cycle-b", "cycle-a")


# =============================================================================
# Resolver behaviour
# =============================================================================


class LineageResolverBehaviour(RealGraphCase):
    """Resolution of derivation facts recorded by a real graph."""

    RESOLVED_KEYS = [
        "edge_path",
        "failure_domains",
        "lineage_root_ids",
        "parent_memory_ids",
        "source_ids",
    ]

    async def test_episode_over_messages_resolves_exactly(self):
        await self.build_episode_graph()
        stored = await self.storage.get_node(EPISODE_ID)

        resolved = await StorageLineageResolver(self.storage)(stored)

        self.assertEqual(resolved, {
            "source_ids": MESSAGE_IDS,
            "parent_memory_ids": MESSAGE_IDS,
            "lineage_root_ids": MESSAGE_IDS,
            "edge_path": tuple(sorted(EPISODE_EDGE_IDS)),
            "failure_domains": (),
        })

    async def test_topic_derivation_is_carried_by_edges_without_source_ids(self):
        await self.build_topic_graph()
        stored = await self.storage.get_node(TOPIC_ID)
        self.assertEqual(stored.source_memory_ids, [])
        self.assertEqual(stored.child_ids, [EPISODE_ID])

        resolved = await StorageLineageResolver(self.storage, max_depth=2)(stored)

        self.assertEqual(resolved["source_ids"], ())
        self.assertEqual(resolved["parent_memory_ids"], (EPISODE_ID,))
        self.assertEqual(resolved["lineage_root_ids"], MESSAGE_IDS)
        self.assertEqual(
            resolved["edge_path"], tuple(sorted(EPISODE_EDGE_IDS + (TOPIC_EDGE_ID,)))
        )

    async def test_topic_under_reports_roots_below_its_depth(self):
        await self.build_topic_graph()
        stored = await self.storage.get_node(TOPIC_ID)

        resolved = await StorageLineageResolver(self.storage, max_depth=1)(stored)

        self.assertEqual(resolved["parent_memory_ids"], (EPISODE_ID,))
        self.assertEqual(resolved["lineage_root_ids"], ())

    async def test_origin_node_reports_no_parent_and_never_roots_itself(self):
        await self.storage.save_node(NeuralNode(
            node_id="origin-1",
            layer=NodeLayer.MESSAGE,
            content="origin content",
            session_key=SESSION_KEY,
        ))
        stored = await self.storage.get_node("origin-1")

        resolved = await StorageLineageResolver(self.storage)(stored)

        self.assertEqual(sorted(resolved), self.RESOLVED_KEYS)
        self.assertEqual(resolved["parent_memory_ids"], ())
        self.assertEqual(resolved["lineage_root_ids"], ())
        self.assertNotIn("origin-1", resolved["lineage_root_ids"])

    async def test_cycle_terminates_and_repeats_identically(self):
        await self.build_cycle()
        stored = await self.storage.get_node("cycle-a")
        resolver = StorageLineageResolver(self.storage)

        first = await resolver(stored)
        second = await resolver(stored)

        self.assertEqual(first, second)
        self.assertEqual(first["parent_memory_ids"], ("cycle-b",))
        self.assertEqual(first["lineage_root_ids"], ())
        self.assertEqual(
            first["edge_path"], ("edge-cycle-a-cycle-b", "edge-cycle-b-cycle-a")
        )
        mirrored = await resolver(await self.storage.get_node("cycle-b"))
        self.assertEqual(mirrored["parent_memory_ids"], ("cycle-a",))
        self.assertEqual(mirrored["lineage_root_ids"], ())

    async def test_chain_deeper_than_the_bound_under_reports_without_inventing(self):
        chain = await self.build_derivation_chain()
        origin = await self.storage.get_node(chain[-1].node_id)

        full = await StorageLineageResolver(self.storage, max_depth=3)(origin)
        shallow = await StorageLineageResolver(self.storage, max_depth=2)(origin)
        one_hop = await StorageLineageResolver(self.storage, max_depth=1)(origin)
        narrow = await StorageLineageResolver(self.storage, max_nodes=1)(origin)

        self.assertEqual(full["lineage_root_ids"], ("chain-1",))
        for bounded in (shallow, one_hop, narrow):
            self.assertEqual(bounded["lineage_root_ids"], ())
            self.assertEqual(bounded["parent_memory_ids"], ("chain-3",))
            self.assertLessEqual(set(bounded["lineage_root_ids"]), {"chain-1"})

    async def test_dangling_recorded_source_stays_a_parent_but_never_a_root(self):
        await self.build_episode_graph()
        self.assertTrue(await self.storage.delete_node("message-2"))
        stored = await self.storage.get_node(EPISODE_ID)
        self.assertEqual(stored.source_memory_ids, list(MESSAGE_IDS))
        self.assertIsNone(await self.storage.get_node("message-2"))

        resolved = await StorageLineageResolver(self.storage)(stored)

        self.assertEqual(resolved["parent_memory_ids"], MESSAGE_IDS)
        self.assertEqual(resolved["lineage_root_ids"], ("message-1",))
        self.assertEqual(resolved["edge_path"], (EPISODE_EDGE_IDS[0],))

    async def test_hierarchy_edge_direction_is_never_inverted(self):
        """A summary is not an ancestor of the message it was built from."""
        await self.build_episode_graph()
        stored = await self.storage.get_node("message-1")
        incoming = await self.storage.get_edges_to("message-1", [EdgeType.HIERARCHY])

        resolved = await StorageLineageResolver(self.storage)(stored)

        # The upward links really exist; the resolver deliberately ignores them.
        self.assertEqual(stored.parent_id, EPISODE_ID)
        self.assertEqual([edge.source_id for edge in incoming], [EPISODE_ID])
        self.assertEqual(resolved["source_ids"], ())
        self.assertEqual(resolved["parent_memory_ids"], ())
        self.assertEqual(resolved["lineage_root_ids"], ())
        self.assertEqual(resolved["edge_path"], ())
        for field, value in resolved.items():
            with self.subTest(field=field):
                self.assertNotIn(EPISODE_ID, value)

    async def test_every_field_is_sorted_deduped_and_idempotent(self):
        await self.build_episode_graph(
            source_memory_ids=["message-2", "message-1", "message-2"]
        )
        stored = await self.storage.get_node(EPISODE_ID)
        self.assertEqual(
            stored.source_memory_ids, ["message-2", "message-1", "message-2"]
        )
        resolver = StorageLineageResolver(self.storage)

        first = await resolver(stored)
        second = await resolver(stored)

        self.assertEqual(first, second)
        self.assertEqual(sorted(first), self.RESOLVED_KEYS)
        for field, value in first.items():
            with self.subTest(field=field):
                self.assertEqual(value, tuple(sorted(set(value))))
        self.assertEqual(first["source_ids"], MESSAGE_IDS)

    async def test_resolution_never_writes_to_the_graph(self):
        """Read-only in fact, not only by intent: no node or edge changes."""
        topic, _episode, _messages = await self.build_topic_graph()
        before = await snapshot_storage(self.storage)

        resolved = await StorageLineageResolver(self.storage)(
            await self.storage.get_node(topic.node_id)
        )

        self.assertEqual(resolved["lineage_root_ids"], MESSAGE_IDS)
        self.assertEqual(await snapshot_storage(self.storage), before)

    async def test_storage_failures_propagate_instead_of_emptying_lineage(self):
        """A read error must not be reported as "this memory has no ancestors"."""
        await self.build_episode_graph()
        stored = await self.storage.get_node(EPISODE_ID)
        broken = StorageLineageResolver(BrokenStorage(self.storage))

        with self.assertRaises(StorageUnavailable):
            await broken(stored)


class StorageUnavailable(RuntimeError):
    """Raised by BrokenStorage to stand in for a real backend read failure."""


class BrokenStorage:
    """A real storage instance whose node reads fail.

    Deliberately not a stand-in for the graph: edges still come from the real
    backend, and only `get_node` fails, which is exactly the case where a
    resolver could be tempted to report empty lineage.
    """

    def __init__(self, storage) -> None:
        self._storage = storage

    async def get_node(self, node_id: str):
        raise StorageUnavailable("node read failed")

    async def get_edges_from(self, node_id: str, edge_types=None):
        return await self._storage.get_edges_from(node_id, edge_types)


class InMemoryLineageResolverTests(
    _InMemoryBackend, LineageResolverBehaviour, unittest.IsolatedAsyncioTestCase
):
    pass


class SQLiteLineageResolverTests(
    _SQLiteBackend, LineageResolverBehaviour, unittest.IsolatedAsyncioTestCase
):
    pass


# =============================================================================
# Adapter bound to real storage
# =============================================================================


class RealStorageAdapterBinding(RealGraphCase):
    """The adapter exports the stored graph's lineage and nothing else."""

    async def test_exported_lineage_matches_the_stored_graph(self):
        await self.build_episode_graph()
        runtime = build_runtime(60, "real-storage")
        adapter = make_adapter(
            runtime,
            episode_search(self.storage),
            lineage_resolver=StorageLineageResolver(self.storage),
        )

        exports = await adapter.query(make_request(runtime))

        stored = await self.storage.get_node(EPISODE_ID)
        stored_edges = await self.storage.get_edges_from(EPISODE_ID, [EdgeType.HIERARCHY])
        expected_sources = tuple(sorted(set(stored.source_memory_ids)))
        expected_edges = tuple(sorted(edge.edge_id for edge in stored_edges))
        self.assertEqual(len(exports), 1)
        claim = exports[0].claims[0]
        trace = exports[0].trace
        self.assertEqual(trace.memory_ids, (EPISODE_ID,))
        self.assertEqual(claim.source_ids, expected_sources)
        self.assertEqual(claim.parent_memory_ids, expected_sources)
        self.assertEqual(claim.lineage_root_ids, expected_sources)
        self.assertEqual(claim.failure_domains, ())
        self.assertEqual(trace.source_ids, expected_sources)
        self.assertEqual(trace.parent_memory_ids, expected_sources)
        self.assertEqual(trace.lineage_root_ids, expected_sources)
        self.assertEqual(trace.edge_path, expected_edges)
        self.assertEqual(trace.retrieval_operator, "neuralgraph_local_retrieval")
        self.assertEqual(claim.derivation_operator, "neuralgraph_local_retrieval")
        self.assertIs(claim.policy_status, PolicyStatus.ALLOWED)

    async def test_export_carries_no_stored_content_metadata_or_embedding(self):
        await self.build_episode_graph()
        runtime = build_runtime(61, "real-storage")
        adapter = make_adapter(
            runtime,
            episode_search(self.storage),
            lineage_resolver=StorageLineageResolver(self.storage),
        )

        exports = await adapter.query(make_request(runtime))
        payload = canonical_json(exports)

        # Exactly one payload field crosses: the retrieved node's own text.
        self.assertEqual(exports[0].claims[0].content, {"text": EPISODE_SUMMARY})
        for marker in PRIVATE_PAYLOAD_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, payload)

    async def test_denied_policy_exports_an_empty_trace_and_no_payload(self):
        await self.build_episode_graph()
        runtime = build_runtime(62, "real-storage")
        adapter = make_adapter(
            runtime,
            episode_search(self.storage),
            policy=deny_everything,
            lineage_resolver=StorageLineageResolver(self.storage),
        )

        exports = await adapter.query(make_request(runtime))
        payload = canonical_json(exports)

        self.assertEqual(len(exports), 1)
        trace = exports[0].trace
        self.assertEqual(exports[0].claims, ())
        self.assertIs(trace.policy_status, PolicyStatus.DENIED)
        self.assertEqual(
            (trace.memory_ids, trace.source_ids, trace.parent_memory_ids,
             trace.lineage_root_ids, trace.edge_path),
            ((), (), (), (), ()),
        )
        for marker in PRIVATE_PAYLOAD_MARKERS + (EPISODE_SUMMARY, EPISODE_ID) + MESSAGE_IDS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, payload)

    async def test_two_stored_memories_export_two_distinct_identities(self):
        await self.build_episode_graph()
        await self.build_second_episode_graph()
        runtime = build_runtime(63, "real-storage")
        adapter = make_adapter(
            runtime,
            episode_search(self.storage),
            lineage_resolver=StorageLineageResolver(self.storage),
        )

        exports = await adapter.query(make_request(runtime))

        self.assertEqual(len(exports), 2)
        memory_ids = {export.trace.memory_ids for export in exports}
        self.assertEqual(memory_ids, {(EPISODE_ID,), (SECOND_EPISODE_ID,)})
        self.assertEqual(len({export.trace.trace_id for export in exports}), 2)
        self.assertEqual(
            len({claim.claim_id for export in exports for claim in export.claims}), 2
        )
        roots = {
            export.trace.memory_ids[0]: export.claims[0].lineage_root_ids
            for export in exports
        }
        self.assertEqual(roots[EPISODE_ID], MESSAGE_IDS)
        self.assertEqual(roots[SECOND_EPISODE_ID], (SECOND_MESSAGE_ID,))

    async def test_one_memory_reached_twice_exports_once_with_the_same_identity(self):
        """A callback unioning two retrieval paths must not double-count a memory."""
        await self.build_episode_graph()
        runtime = build_runtime(64, "real-storage")
        single = await make_adapter(
            runtime,
            episode_search(self.storage),
            lineage_resolver=StorageLineageResolver(self.storage),
        ).query(make_request(runtime))

        searched = await self.storage.vector_search(
            list(EMBEDDING), SESSION_KEY, limit=8, layer_filter=[NodeLayer.EPISODE]
        )
        # A second, independently rehydrated view of the same stored memory.
        reloaded = NeuralNode.from_dict((await self.storage.get_node(EPISODE_ID)).to_dict())

        async def union_retrieve(_request):
            return list(searched) + [(reloaded, 0.5)]

        duplicated = await make_adapter(
            runtime,
            union_retrieve,
            lineage_resolver=StorageLineageResolver(self.storage),
        ).query(make_request(runtime))

        self.assertIsNot(reloaded, searched[0][0])
        self.assertEqual(reloaded.node_id, searched[0][0].node_id)
        self.assertEqual(len(single), 1)
        self.assertEqual(len(duplicated), 1)
        self.assertEqual(duplicated[0].trace.trace_id, single[0].trace.trace_id)
        self.assertEqual(duplicated[0].claims[0].claim_id, single[0].claims[0].claim_id)
        self.assertEqual(duplicated[0].trace.memory_ids, (EPISODE_ID,))
        self.assertEqual(duplicated[0].claims[0].lineage_root_ids, MESSAGE_IDS)

    async def build_distinct_episodes(self, count: int) -> tuple[NeuralNode, ...]:
        """`count` independently rooted episodes, each with its own message."""
        stored = []
        for index in range(count):
            message = make_message(f"budget-message-{index}", MESSAGE_CONTENTS[0])
            episode = make_episode(
                f"budget-episode-{index}", [message], f"BUDGET_SUMMARY_{index}", EMBEDDING
            )
            await consolidate_into_episode(self.storage, episode, [message])
            stored.append(await self.storage.get_node(episode.node_id))
        return tuple(stored)

    async def test_repeats_do_not_spend_budget_reserved_for_distinct_memories(self):
        """Budget bounds what is exported, not how many candidates were offered.

        A benign callback unioning two retrieval paths repeats memories.  Those
        repeats must not consume export slots that distinct, in-budget memories
        still need, or evidence disappears for a reason unrelated to the graph.
        """
        stored = await self.build_distinct_episodes(6)
        runtime = build_runtime(68, "real-storage")
        request = make_request(runtime)

        async def union_retrieve(_request):
            return (
                [(node, 0.9) for node in stored[:3]]
                + [(node, 0.5) for node in stored]
            )

        exports = await make_adapter(
            runtime, union_retrieve, lineage_resolver=StorageLineageResolver(self.storage)
        ).query(request)

        exported_ids = [export.trace.memory_ids[0] for export in exports]
        # 9 candidates, 3 of them repeats, for a budget of 8 export slots.
        self.assertEqual(request.budget.max_claims, 8)
        self.assertLessEqual(len(exports), request.budget.max_claims)
        self.assertEqual(
            sorted(exported_ids), sorted(node.node_id for node in stored)
        )
        self.assertEqual(len(set(exported_ids)), len(stored))
        self.assertEqual(len({export.trace.trace_id for export in exports}), len(stored))

    async def test_budget_still_bounds_exports_of_distinct_memories(self):
        """The bound itself is unchanged, and a denial keeps consuming a slot."""
        stored = await self.build_distinct_episodes(10)
        runtime = build_runtime(69, "real-storage")
        request = make_request(runtime)

        async def retrieve_all_stored(_request):
            return [(node, 0.9) for node in stored]

        for label, policy, expected_status in (
            ("allowed", allow_everything, PolicyStatus.ALLOWED),
            ("denied", deny_everything, PolicyStatus.DENIED),
        ):
            with self.subTest(policy=label):
                exports = await make_adapter(
                    runtime,
                    retrieve_all_stored,
                    policy=policy,
                    lineage_resolver=StorageLineageResolver(self.storage),
                ).query(request)

                self.assertEqual(len(exports), request.budget.max_claims)
                self.assertEqual(
                    {export.trace.policy_status for export in exports}, {expected_status}
                )

    async def test_duplicate_is_skipped_on_the_denied_path_too(self):
        await self.build_episode_graph()
        runtime = build_runtime(65, "real-storage")
        stored = await self.storage.get_node(EPISODE_ID)
        reloaded = NeuralNode.from_dict(stored.to_dict())

        async def union_retrieve(_request):
            return [(stored, 0.9), (reloaded, 0.5)]

        exports = await make_adapter(
            runtime,
            union_retrieve,
            policy=deny_everything,
            lineage_resolver=StorageLineageResolver(self.storage),
        ).query(make_request(runtime))

        self.assertEqual(len(exports), 1)
        self.assertIs(exports[0].trace.policy_status, PolicyStatus.DENIED)


class InMemoryRealAdapterTests(
    _InMemoryBackend, RealStorageAdapterBinding, unittest.IsolatedAsyncioTestCase
):
    pass


class SQLiteRealAdapterTests(
    _SQLiteBackend, RealStorageAdapterBinding, unittest.IsolatedAsyncioTestCase
):
    pass


# =============================================================================
# Resolver output is untrusted boundary input
# =============================================================================


@dataclass(frozen=True)
class LeakyRecord:
    """A dataclass is fully expanded by `to_jsonable`, so it must never be exported."""

    content: str
    metadata: dict


class HostileIdentifier(str):
    """`str` subclass smuggling payload through `repr` and an attribute."""

    def __new__(cls, value, smuggled="SMUGGLED_ROOT_ATTR_8W"):
        identifier = super().__new__(cls, value)
        identifier.smuggled = smuggled
        return identifier

    def __repr__(self):
        return "SECRET_FROM_ROOT_REPR_2K"


LINEAGE_FIELDS = (
    "source_ids",
    "parent_memory_ids",
    "lineage_root_ids",
    "failure_domains",
    "edge_path",
)
LEAK_MARKER = "RAW_PRIVATE_CONTENT_7X"
LEAK_METADATA_MARKER = "SECRET_META_3Q"


def static_resolver(known):
    async def resolve(_node):
        return known

    return resolve


class ResolverBoundaryValidationTests(unittest.IsolatedAsyncioTestCase):
    """An owner-supplied resolver is caller-controlled code, so it is validated.

    The node under test is a real `NeuralNode` held by a real
    `InMemoryNeuralGraphStorage`; only the resolver is hostile.
    """

    async def asyncSetUp(self) -> None:
        self.storage = InMemoryNeuralGraphStorage()
        self.runtime = build_runtime(66, "resolver-boundary")
        await self.storage.save_node(make_episode(
            EPISODE_ID, [], EPISODE_SUMMARY, EMBEDDING, source_memory_ids=[]
        ))
        self.node = await self.storage.get_node(EPISODE_ID)

    def retrieve_stored(self, node=None):
        target = self.node if node is None else node

        async def retrieve(_request):
            return [(target, 0.9)]

        return retrieve

    async def query_with(self, **kwargs):
        adapter = make_adapter(self.runtime, self.retrieve_stored(), **kwargs)
        return await adapter.query(make_request(self.runtime))

    async def assert_rejected(self, expected_fragments, forbidden, **kwargs):
        with self.assertRaises(ValueError) as raised:
            await self.query_with(**kwargs)
        message = f"{raised.exception}|{raised.exception!r}"
        for fragment in expected_fragments:
            self.assertIn(fragment, message)
        for leak in forbidden:
            self.assertNotIn(leak, message)
        return message

    async def test_bare_string_is_rejected_instead_of_exploded_into_characters(self):
        for field in LINEAGE_FIELDS:
            with self.subTest(field=field):
                await self.assert_rejected(
                    (field, "str"),
                    ("memory-42",),
                    lineage_resolver=static_resolver({field: "memory-42"}),
                )

    async def test_non_sequence_lineage_values_are_rejected(self):
        rejected = {
            "bytes": b"memory-42",
            "set": {"memory-42"},
            "mapping": {"memory-42": 1},
            "integer": 42,
        }
        for case, value in rejected.items():
            with self.subTest(case=case):
                await self.assert_rejected(
                    ("lineage_root_ids",),
                    ("memory-42",),
                    lineage_resolver=static_resolver({"lineage_root_ids": value}),
                )

    async def test_object_entry_is_rejected_without_serializing_it(self):
        leaky = LeakyRecord(content=LEAK_MARKER, metadata={"k": LEAK_METADATA_MARKER})

        await self.assert_rejected(
            ("lineage_root_ids", "LeakyRecord"),
            (LEAK_MARKER, LEAK_METADATA_MARKER),
            lineage_resolver=static_resolver({"lineage_root_ids": (leaky,)}),
        )

    async def test_blank_entries_are_rejected(self):
        for label, entry in (("empty", ""), ("whitespace", "   ")):
            with self.subTest(entry=label):
                await self.assert_rejected(
                    ("parent_memory_ids", "blank"),
                    (EPISODE_SUMMARY,),
                    lineage_resolver=static_resolver({"parent_memory_ids": (entry,)}),
                )

    async def test_non_mapping_resolver_result_is_rejected(self):
        for label, result in (
            ("list", ["lineage_root_ids"]),
            ("tuple", ("lineage_root_ids",)),
            ("none", None),
            ("string", "lineage_root_ids"),
        ):
            with self.subTest(result=label):
                await self.assert_rejected(
                    ("lineage resolver must return a mapping",),
                    (EPISODE_SUMMARY,),
                    lineage_resolver=static_resolver(result),
                )

    async def test_retrieval_operator_must_be_a_non_blank_string(self):
        for label, value in (
            ("object", LeakyRecord(content=LEAK_MARKER, metadata={})),
            ("integer", 7),
            ("blank", "   "),
        ):
            with self.subTest(operator=label):
                await self.assert_rejected(
                    ("retrieval_operator",),
                    (LEAK_MARKER, EPISODE_SUMMARY),
                    lineage_resolver=static_resolver({"retrieval_operator": value}),
                )

    async def test_valid_retrieval_operator_is_exported_as_a_plain_string(self):
        exports = await self.query_with(
            lineage_resolver=static_resolver(
                {"retrieval_operator": HostileIdentifier("owner_declared_operator")}
            )
        )

        operator = exports[0].trace.retrieval_operator
        self.assertEqual(operator, "owner_declared_operator")
        self.assertIs(type(operator), str)
        self.assertIsNone(getattr(operator, "smuggled", None))
        self.assertEqual(exports[0].claims[0].derivation_operator, "owner_declared_operator")
        self.assertNotIn("SMUGGLED_ROOT_ATTR_8W", canonical_json(exports))

    async def test_string_subclass_entry_crosses_as_a_plain_string(self):
        exports = await self.query_with(
            lineage_resolver=static_resolver(
                {"lineage_root_ids": (HostileIdentifier("root-1"),)}
            )
        )

        claim = exports[0].claims[0]
        (entry,) = claim.lineage_root_ids
        payload = canonical_json(exports)

        self.assertEqual(claim.lineage_root_ids, ("root-1",))
        self.assertIs(type(entry), str)
        self.assertIsNone(getattr(entry, "smuggled", None))
        self.assertIs(type(exports[0].trace.lineage_root_ids[0]), str)
        for marker in ("SMUGGLED_ROOT_ATTR_8W", "SECRET_FROM_ROOT_REPR_2K"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, payload)

    async def test_order_and_multiplicity_are_preserved_exactly(self):
        """The adapter validates; it does not normalize what the owner reported."""
        exports = await self.query_with(lineage_resolver=static_resolver({
            "parent_memory_ids": ("memory-b", "memory-a", "memory-b"),
            "lineage_root_ids": ["root-b", "root-a"],
            "edge_path": ("edge-2", "edge-1"),
            "source_ids": ("source-2", "source-1"),
            "failure_domains": ("domain-2", "domain-1"),
        }))

        claim = exports[0].claims[0]
        trace = exports[0].trace
        self.assertEqual(claim.parent_memory_ids, ("memory-b", "memory-a", "memory-b"))
        self.assertEqual(claim.lineage_root_ids, ("root-b", "root-a"))
        self.assertEqual(claim.source_ids, ("source-2", "source-1"))
        self.assertEqual(claim.failure_domains, ("domain-2", "domain-1"))
        self.assertEqual(trace.edge_path, ("edge-2", "edge-1"))
        self.assertEqual(trace.parent_memory_ids, ("memory-b", "memory-a", "memory-b"))

    async def test_stored_lineage_fallback_is_validated_too(self):
        """A rehydrated row is untrusted: legacy JSON can hold anything."""
        cases = {
            "bare_string": ("memory-42", ("source_ids", "str"), ("memory-42",)),
            "non_string_entry": ([7], ("source_ids", "int"), ()),
            "blank_entry": ([" "], ("source_ids", "blank"), ()),
        }
        for label, (stored_value, expected, forbidden) in cases.items():
            with self.subTest(case=label):
                node = make_episode(
                    f"legacy-{label}", [], EPISODE_SUMMARY, EMBEDDING, source_memory_ids=[]
                )
                node.source_memory_ids = stored_value
                await self.storage.save_node(node)
                loaded = await self.storage.get_node(f"legacy-{label}")
                adapter = make_adapter(self.runtime, self.retrieve_stored(loaded))

                with self.assertRaises(ValueError) as raised:
                    await adapter.query(make_request(self.runtime))

                message = f"{raised.exception}|{raised.exception!r}"
                for fragment in expected:
                    self.assertIn(fragment, message)
                for leak in forbidden + (EPISODE_SUMMARY, EPISODE_METADATA_NOTE):
                    self.assertNotIn(leak, message)

    async def test_a_valid_resolver_still_exports_normally(self):
        """Positive control: validation rejects malformed input, not all input."""
        exports = await self.query_with(
            lineage_resolver=StorageLineageResolver(self.storage)
        )

        self.assertEqual(exports[0].trace.memory_ids, (EPISODE_ID,))
        self.assertEqual(exports[0].claims[0].lineage_root_ids, ())
        self.assertEqual(exports[0].claims[0].content, {"text": EPISODE_SUMMARY})


# =============================================================================
# Documented limitations of the real-storage path
# =============================================================================


class RealStorageAnalysisLimitationTests(unittest.IsolatedAsyncioTestCase):
    """What a real-storage run can and cannot currently say about fragility.

    These assertions record known limits of this slice.  They are deliberately
    not "fixed" here: synthesizing failure domains the graph does not record, or
    changing the default claim projection, would fabricate exactly the evidence
    the fragility metrics are supposed to measure.
    """

    async def asyncSetUp(self) -> None:
        self.storage = InMemoryNeuralGraphStorage()
        self.runtime = build_runtime(67, "real-storage-limits")
        self.request = make_request(self.runtime)
        left_message = make_message("message-left", MESSAGE_CONTENTS[0])
        right_message = make_message("message-right", MESSAGE_CONTENTS[1])
        self.left = make_episode("episode-left", [left_message], "LEFT_VALUE_1", EMBEDDING)
        self.right = make_episode(
            "episode-right", [right_message], "RIGHT_VALUE_1", SECOND_EMBEDDING
        )
        self.left.metadata["slot"] = "left"
        self.right.metadata["slot"] = "right"
        await consolidate_into_episode(self.storage, self.left, [left_message])
        await consolidate_into_episode(self.storage, self.right, [right_message])

    def make_slot_adapter(self):
        """An owner-supplied projection that emits the slots synthesis needs."""
        return make_adapter(
            self.runtime,
            episode_search(self.storage),
            lineage_resolver=StorageLineageResolver(self.storage),
            claim_projection=lambda node: {
                "slot": str(node.metadata["slot"]),
                "value": str(node.content),
            },
        )

    async def test_absent_failure_domains_zero_every_coalition_metric(self):
        exports = await self.make_slot_adapter().query(self.request)
        claims = ClaimNormalizer().normalize(
            [claim for export in exports for claim in export.claims], self.request.query_id
        )
        synthesis = RuleBasedSynthesizer(REQUIRED_SLOTS).synthesize(claims)
        analyzer = LineageAnalyzer()

        metrics = analyzer.score(claims, REQUIRED_SLOTS, synthesis)

        # Real lineage roots are known; failure domains are not modelled at all.
        self.assertEqual(len(claims), 2)
        self.assertTrue(synthesis.success)
        self.assertEqual(metrics.unique_lineage_root_count, 2)
        self.assertEqual(metrics.unique_failure_domain_count, 0)
        # Therefore every coalition metric reads 0.  That is missing evidence,
        # not a demonstration that the collective answer is robust.
        self.assertEqual(analyzer.reconstruction_coalitions(claims, REQUIRED_SLOTS), ())
        self.assertEqual(metrics.reconstruction_coalition_count, 0)
        self.assertEqual(metrics.minimal_support_coalition_size, 0)
        self.assertEqual(metrics.minimal_failure_domain_cut, 0)

    async def test_uncovered_slot_short_circuits_coalition_analysis(self):
        exports = await self.make_slot_adapter().query(self.request)
        claims = ClaimNormalizer().normalize(
            [claim for export in exports for claim in export.claims], self.request.query_id
        )
        left_only = tuple(claim for claim in claims if claim.content["slot"] == "left")

        self.assertEqual(len(left_only), 1)
        self.assertEqual(
            LineageAnalyzer().reconstruction_coalitions(left_only, REQUIRED_SLOTS), ()
        )

    async def test_default_text_projection_is_dropped_before_synthesis(self):
        """The default `{"text": ...}` claim carries no slot, so it is normalized away."""
        registry = CapabilityRegistry()
        registry.register(make_adapter(
            self.runtime,
            episode_search(self.storage),
            lineage_resolver=StorageLineageResolver(self.storage),
        ))
        coordinator = TesseractCoordinator(
            registry,
            Router(registry, self.runtime.failure),
            ClaimNormalizer(),
            RuleBasedSynthesizer(REQUIRED_SLOTS),
            LineageAnalyzer(),
            self.runtime.traces,
        )

        execution = await coordinator.execute(self.request, "real-storage")

        # The evidence really was exported and really carries lineage; the drop
        # happens at normalization, because the payload has no slot or value.
        self.assertEqual(execution.route, (REAL_NODE_ID,))
        self.assertEqual(len(execution.exports), 2)
        exported = [claim for export in execution.exports for claim in export.claims]
        self.assertEqual(len(exported), 2)
        self.assertEqual(
            {claim.content["text"] for claim in exported}, {"LEFT_VALUE_1", "RIGHT_VALUE_1"}
        )
        self.assertTrue(all(claim.lineage_root_ids for claim in exported))
        self.assertEqual(execution.claims, ())
        self.assertFalse(execution.synthesis.success)
        self.assertEqual(execution.metrics.unique_lineage_root_count, 0)

    async def test_slot_projection_lets_the_same_evidence_reach_synthesis(self):
        """Positive control: the drop is caused by the projection, not the adapter."""
        registry = CapabilityRegistry()
        registry.register(self.make_slot_adapter())
        coordinator = TesseractCoordinator(
            registry,
            Router(registry, self.runtime.failure),
            ClaimNormalizer(),
            RuleBasedSynthesizer(REQUIRED_SLOTS),
            LineageAnalyzer(),
            self.runtime.traces,
        )

        execution = await coordinator.execute(self.request, "real-storage")

        self.assertEqual(len(execution.claims), 2)
        self.assertTrue(execution.synthesis.success)
        self.assertEqual(execution.synthesis.answer, "LEFT_VALUE_1:RIGHT_VALUE_1")
        self.assertEqual(execution.metrics.unique_lineage_root_count, 2)
        self.assertEqual(execution.metrics.reconstruction_coalition_count, 0)


if __name__ == "__main__":
    unittest.main()
