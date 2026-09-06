"""Storage-level regression tests for lineage fields read by the coordination adapter.

`NeuralGraphMemoryAdapter` (NeuralGraph/coordination/adapters.py) derives claim
`source_ids` from `NeuralNode.source_memory_ids`. That field must therefore survive
a durable round-trip through the persistent backend, exactly as it already does for
`InMemoryNeuralGraphStorage` and for `NeuralNode.to_dict` / `NeuralNode.from_dict`.

These tests run against the real `SQLiteNeuralGraphStorage` writing to a real SQLite
file inside a per-test temporary directory. Nothing here is mocked, and no test is
allowed to touch the default `~/.memmachine` database.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from NeuralGraph.data_types import NeuralNode, NodeLayer
from NeuralGraph.sqlite_storage import SQLiteNeuralGraphStorage
from NeuralGraph.storage import InMemoryNeuralGraphStorage

SESSION_KEY = "coordination-storage-session"
SOURCE_MEMORY_IDS = ["memory-root-1", "memory-root-2", "memory-root-3"]


def make_node(
    node_id: str = "node-1",
    source_memory_ids: list[str] | None = None,
    session_key: str = SESSION_KEY,
) -> NeuralNode:
    """Build a node carrying lineage the coordination adapter reads."""
    return NeuralNode(
        node_id=node_id,
        layer=NodeLayer.EPISODE,
        content="locally held fact",
        embedding=[0.25, 0.5, 0.75],
        session_key=session_key,
        source_memory_ids=(
            list(SOURCE_MEMORY_IDS) if source_memory_ids is None else list(source_memory_ids)
        ),
    )


class SQLiteTempDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    """Base case that pins every SQLite storage instance to a temp db file.

    The default constructor argument (sqlite_storage.py) points at
    ``~/.memmachine/memories.db``. Tests must never write there, so ``db_path`` is
    always passed explicitly and the connection is closed during teardown.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "coordination_storage_test.db"
        self.storage = SQLiteNeuralGraphStorage(db_path=self.db_path)

    def tearDown(self) -> None:
        self.storage.close()
        self._tmpdir.cleanup()

    def read_raw_data_blob(self, node_id: str) -> dict:
        """Read the stored JSON blob straight from the temp db file.

        Uses an independent connection so the assertion is about what is durable on
        disk, not about anything cached by the storage instance.
        """
        connection = sqlite3.connect(str(self.db_path))
        try:
            row = connection.execute(
                "SELECT data FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row, f"node {node_id} was not persisted to {self.db_path}")
        return json.loads(row[0])


class SQLiteSourceMemoryIdsRoundTripTests(SQLiteTempDatabaseTestCase):
    async def test_save_node_then_get_node_preserves_source_memory_ids(self) -> None:
        node = make_node()
        await self.storage.save_node(node)

        loaded = await self.storage.get_node(node.node_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.source_memory_ids, SOURCE_MEMORY_IDS)

    async def test_persisted_data_blob_contains_source_memory_ids(self) -> None:
        node = make_node()
        await self.storage.save_node(node)

        blob = self.read_raw_data_blob(node.node_id)

        self.assertIn("source_memory_ids", blob)
        self.assertEqual(blob["source_memory_ids"], SOURCE_MEMORY_IDS)

    async def test_get_nodes_by_session_preserves_source_memory_ids(self) -> None:
        node = make_node()
        await self.storage.save_node(node)

        nodes = await self.storage.get_nodes_by_session(SESSION_KEY)

        self.assertEqual([n.node_id for n in nodes], [node.node_id])
        self.assertEqual(nodes[0].source_memory_ids, SOURCE_MEMORY_IDS)

    async def test_get_all_nodes_preserves_source_memory_ids(self) -> None:
        node = make_node()
        await self.storage.save_node(node)

        nodes = await self.storage.get_all_nodes(SESSION_KEY)

        self.assertEqual([n.node_id for n in nodes], [node.node_id])
        self.assertEqual(nodes[0].source_memory_ids, SOURCE_MEMORY_IDS)

    async def test_vector_search_preserves_source_memory_ids(self) -> None:
        node = make_node()
        await self.storage.save_node(node)

        results = await self.storage.vector_search([0.25, 0.5, 0.75], SESSION_KEY)

        self.assertEqual([n.node_id for n, _score in results], [node.node_id])
        self.assertEqual(results[0][0].source_memory_ids, SOURCE_MEMORY_IDS)

    async def test_save_nodes_batch_preserves_source_memory_ids(self) -> None:
        first = make_node("batch-node-1", ["memory-a"])
        second = make_node("batch-node-2", ["memory-b", "memory-c"])

        saved = await self.storage.save_nodes_batch([first, second])

        self.assertEqual(saved, 2)
        loaded_first = await self.storage.get_node("batch-node-1")
        loaded_second = await self.storage.get_node("batch-node-2")
        self.assertEqual(loaded_first.source_memory_ids, ["memory-a"])
        self.assertEqual(loaded_second.source_memory_ids, ["memory-b", "memory-c"])

    async def test_node_without_lineage_round_trips_as_empty_list(self) -> None:
        node = make_node("no-lineage-node", [])
        await self.storage.save_node(node)

        loaded = await self.storage.get_node("no-lineage-node")

        self.assertEqual(loaded.source_memory_ids, [])

    async def test_sqlite_round_trip_matches_neuralnode_dict_round_trip(self) -> None:
        node = make_node()
        await self.storage.save_node(node)

        from_storage = await self.storage.get_node(node.node_id)
        from_dict = NeuralNode.from_dict(node.to_dict())

        self.assertEqual(from_storage.source_memory_ids, from_dict.source_memory_ids)
        self.assertEqual(from_storage.source_memory_ids, node.source_memory_ids)


class SQLiteLegacyRowCompatibilityTests(SQLiteTempDatabaseTestCase):
    """Rows written before `source_memory_ids` was serialized must still load."""

    LEGACY_NODE_ID = "legacy-node"

    def insert_legacy_row(self) -> None:
        """Insert a row whose JSON blob has the pre-fix key set (no lineage field).

        Written through an independent connection so the row is genuinely foreign to
        the current serializer, exactly like a row persisted by an older build.
        """
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
        legacy_data = {
            "node_id": self.LEGACY_NODE_ID,
            "session_key": SESSION_KEY,
            "layer": NodeLayer.EPISODE.value,
            "content": "row persisted before source_memory_ids was serialized",
            "embedding": None,
            "wave_amplitudes": {},
            "activation_level": 0.0,
            "last_activated": created_at,
            "refractory_until": None,
            "heat_score": 0.3,
            "importance_score": 0.5,
            "consolidation_state": "active",
            "parent_id": None,
            "child_ids": [],
            "entity_ids": [],
            "edge_type_counts": {},
            "dominant_dimension": "",
            "diversity_score": 0.0,
            "created_at": created_at,
            "metadata": {},
        }
        self.assertNotIn("source_memory_ids", legacy_data)

        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute(
                "INSERT INTO nodes (node_id, session_key, layer, content, embedding, data,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self.LEGACY_NODE_ID,
                    SESSION_KEY,
                    str(NodeLayer.EPISODE.value),
                    legacy_data["content"],
                    None,
                    json.dumps(legacy_data),
                    created_at,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    async def test_legacy_row_loads_without_raising_and_yields_empty_lineage(self) -> None:
        self.insert_legacy_row()

        loaded = await self.storage.get_node(self.LEGACY_NODE_ID)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.source_memory_ids, [])

    async def test_legacy_row_keeps_its_other_fields(self) -> None:
        """Guards against a load that "succeeds" by discarding the rest of the row."""
        self.insert_legacy_row()

        loaded = await self.storage.get_node(self.LEGACY_NODE_ID)

        self.assertEqual(loaded.node_id, self.LEGACY_NODE_ID)
        self.assertEqual(loaded.session_key, SESSION_KEY)
        self.assertEqual(loaded.layer, NodeLayer.EPISODE)
        self.assertEqual(
            loaded.content, "row persisted before source_memory_ids was serialized"
        )

    async def test_legacy_row_is_upgraded_once_it_is_saved_again(self) -> None:
        self.insert_legacy_row()
        loaded = await self.storage.get_node(self.LEGACY_NODE_ID)
        loaded.source_memory_ids = ["memory-added-later"]

        await self.storage.save_node(loaded)
        reloaded = await self.storage.get_node(self.LEGACY_NODE_ID)

        self.assertEqual(reloaded.source_memory_ids, ["memory-added-later"])


class StorageBackendAgreementTests(SQLiteTempDatabaseTestCase):
    async def test_inmemory_and_sqlite_agree_on_source_memory_ids(self) -> None:
        """Both backends must expose the same lineage for the same saved node.

        Scoped deliberately to `source_memory_ids`: the SQLite serializer has other
        known divergences from `NeuralNode.to_dict` (for example `updated_at`) that
        are outside this phase and are not asserted here either way.
        """
        node = make_node("agreement-node")
        in_memory = InMemoryNeuralGraphStorage()

        await in_memory.save_node(node)
        await self.storage.save_node(node)
        from_memory = await in_memory.get_node("agreement-node")
        from_sqlite = await self.storage.get_node("agreement-node")

        self.assertEqual(from_memory.source_memory_ids, SOURCE_MEMORY_IDS)
        self.assertEqual(from_sqlite.source_memory_ids, from_memory.source_memory_ids)

    async def test_backends_agree_on_empty_lineage(self) -> None:
        node = make_node("agreement-empty-node", [])
        in_memory = InMemoryNeuralGraphStorage()

        await in_memory.save_node(node)
        await self.storage.save_node(node)
        from_memory = await in_memory.get_node("agreement-empty-node")
        from_sqlite = await self.storage.get_node("agreement-empty-node")

        self.assertEqual(from_sqlite.source_memory_ids, from_memory.source_memory_ids)
        self.assertEqual(from_sqlite.source_memory_ids, [])


if __name__ == "__main__":
    unittest.main()
