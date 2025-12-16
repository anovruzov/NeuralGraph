"""SQLite-based persistent storage for Neural Memory Graph.

Provides persistent storage using SQLite with sqlite-vec for vector search.
Implements the NeuralGraphStorage interface for use with MCP tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data_types import (
    ConsolidationState,
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
    cosine_similarity,
)
from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


def _serialize_node(node: NeuralNode) -> dict[str, Any]:
    """Serialize NeuralNode to JSON-compatible dict."""
    return {
        "node_id": node.node_id,
        "session_key": node.session_key,
        "layer": node.layer.value,
        "content": node.content,
        "embedding": node.embedding,
        "wave_amplitudes": node.wave_amplitudes,
        "activation_level": node.activation_level,
        "last_activated": node.last_activated.isoformat() if node.last_activated else None,
        "refractory_until": node.refractory_until.isoformat() if node.refractory_until else None,
        "heat_score": node.heat_score,
        "importance_score": node.importance_score,
        "consolidation_state": node.consolidation_state.value,
        "parent_id": node.parent_id,
        "child_ids": node.child_ids,
        "entity_ids": node.entity_ids,
        "edge_type_counts": node.edge_type_counts,
        "dominant_dimension": node.dominant_dimension,
        "diversity_score": node.diversity_score,
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "metadata": node.metadata,
    }


def _deserialize_node(data: dict[str, Any]) -> NeuralNode:
    """Deserialize dict to NeuralNode."""
    return NeuralNode(
        node_id=data["node_id"],
        session_key=data["session_key"],
        layer=NodeLayer(data["layer"]),
        content=data["content"],
        embedding=data.get("embedding"),
        wave_amplitudes=data.get("wave_amplitudes", {}),
        activation_level=data.get("activation_level", 0.0),
        last_activated=datetime.fromisoformat(data["last_activated"]) if data.get("last_activated") else datetime.now(timezone.utc),
        refractory_until=datetime.fromisoformat(data["refractory_until"]) if data.get("refractory_until") else None,
        heat_score=data.get("heat_score", 0.0),
        importance_score=data.get("importance_score", 0.5),
        consolidation_state=ConsolidationState(data.get("consolidation_state", "active")),
        parent_id=data.get("parent_id"),
        child_ids=data.get("child_ids", []),
        entity_ids=data.get("entity_ids", []),
        edge_type_counts=data.get("edge_type_counts", {}),
        dominant_dimension=data.get("dominant_dimension"),
        diversity_score=data.get("diversity_score", 0.0),
        created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
        metadata=data.get("metadata", {}),
    )


def _serialize_edge(edge: NeuralEdge) -> dict[str, Any]:
    """Serialize NeuralEdge to JSON-compatible dict."""
    return {
        "edge_id": edge.edge_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "edge_type": edge.edge_type.value,
        "base_weight": edge.base_weight,
        "sign": edge.sign,
        "confidence": edge.confidence,
        "ltp_boost": edge.ltp_boost,
        "ltp_decay_rate": edge.ltp_decay_rate,
        "activation_count": edge.activation_count,
        "last_activated": edge.last_activated.isoformat() if edge.last_activated else None,
        "gate_threshold": edge.gate_threshold,
        "propagation_delay_ms": edge.propagation_delay_ms,
        "metadata": edge.metadata,
    }


def _deserialize_edge(data: dict[str, Any]) -> NeuralEdge:
    """Deserialize dict to NeuralEdge."""
    return NeuralEdge(
        edge_id=data["edge_id"],
        source_id=data["source_id"],
        target_id=data["target_id"],
        edge_type=EdgeType(data["edge_type"]),
        base_weight=data.get("base_weight", 1.0),
        sign=data.get("sign", 1.0),
        confidence=data.get("confidence", 1.0),
        ltp_boost=data.get("ltp_boost", 1.0),
        ltp_decay_rate=data.get("ltp_decay_rate", 0.01),
        activation_count=data.get("activation_count", 0),
        last_activated=datetime.fromisoformat(data["last_activated"]) if data.get("last_activated") else datetime.now(timezone.utc),
        gate_threshold=data.get("gate_threshold", 0.3),
        propagation_delay_ms=data.get("propagation_delay_ms", 0.0),
        metadata=data.get("metadata", {}),
    )


class SQLiteNeuralGraphStorage(NeuralGraphStorage):
    """SQLite-based persistent storage for neural graph.

    Uses SQLite for durability with optional sqlite-vec for vector search.
    Falls back to brute-force cosine similarity if sqlite-vec is unavailable.
    """

    def __init__(self, db_path: str | Path = "~/.memmachine/memories.db"):
        """Initialize SQLite storage.

        Args:
            db_path: Path to SQLite database file. Defaults to ~/.memmachine/memories.db
        """
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._has_sqlite_vec = False

        # Initialize database
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # Try to load sqlite-vec extension
        try:
            self._conn.enable_load_extension(True)
            # Try common locations for sqlite-vec
            for ext_path in [
                "sqlite_vec",
                "vec0",
                "/usr/local/lib/sqlite-vec/vec0",
            ]:
                try:
                    self._conn.load_extension(ext_path)
                    self._has_sqlite_vec = True
                    logger.info("sqlite-vec extension loaded successfully")
                    break
                except sqlite3.OperationalError:
                    continue
        except Exception as e:
            logger.warning(f"Could not load sqlite-vec: {e}. Using brute-force vector search.")

        # Create tables
        cursor = self._conn.cursor()

        # Nodes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                layer TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB,
                data JSON NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Edges table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                data JSON NOT NULL,
                FOREIGN KEY (source_id) REFERENCES nodes(node_id),
                FOREIGN KEY (target_id) REFERENCES nodes(node_id)
            )
        """)

        # Categories table (for MCP tools)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                domain TEXT
            )
        """)

        # Domains table (for MCP tools)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_layer ON nodes(session_key, layer)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type)")

        self._conn.commit()
        logger.info(f"SQLite database initialized at {self._db_path}")

    def _embedding_to_blob(self, embedding: list[float] | None) -> bytes | None:
        """Convert embedding to bytes for storage."""
        if embedding is None:
            return None
        import struct
        return struct.pack(f'{len(embedding)}f', *embedding)

    def _blob_to_embedding(self, blob: bytes | None) -> list[float] | None:
        """Convert bytes back to embedding."""
        if blob is None:
            return None
        import struct
        count = len(blob) // 4  # 4 bytes per float
        return list(struct.unpack(f'{count}f', blob))

    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================

    async def save_node(self, node: NeuralNode) -> None:
        """Save or update a node."""
        async with self._lock:
            cursor = self._conn.cursor()
            data = _serialize_node(node)
            embedding_blob = self._embedding_to_blob(node.embedding)

            cursor.execute("""
                INSERT OR REPLACE INTO nodes (node_id, session_key, layer, content, embedding, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                node.node_id,
                node.session_key,
                node.layer.value,
                node.content,
                embedding_blob,
                json.dumps(data),
                node.created_at.isoformat() if node.created_at else datetime.now(timezone.utc).isoformat()
            ))
            self._conn.commit()

    async def get_node(self, node_id: str) -> NeuralNode | None:
        """Get a node by ID."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT data, embedding FROM nodes WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        data = json.loads(row["data"])
        data["embedding"] = self._blob_to_embedding(row["embedding"])
        return _deserialize_node(data)

    async def update_node(self, node: NeuralNode) -> None:
        """Update an existing node."""
        await self.save_node(node)

    async def delete_node(self, node_id: str) -> bool:
        """Delete a node and its edges."""
        async with self._lock:
            cursor = self._conn.cursor()

            # Check if node exists
            cursor.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,))
            if cursor.fetchone() is None:
                return False

            # Delete associated edges
            cursor.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", (node_id, node_id))

            # Delete node
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            self._conn.commit()

            return True

    async def get_nodes_by_session(
        self,
        session_key: str,
        layer: NodeLayer | None = None
    ) -> list[NeuralNode]:
        """Get all nodes for a session."""
        cursor = self._conn.cursor()

        if layer is not None:
            cursor.execute(
                "SELECT data, embedding FROM nodes WHERE session_key = ? AND layer = ?",
                (session_key, layer.value)
            )
        else:
            cursor.execute(
                "SELECT data, embedding FROM nodes WHERE session_key = ?",
                (session_key,)
            )

        nodes = []
        for row in cursor.fetchall():
            data = json.loads(row["data"])
            data["embedding"] = self._blob_to_embedding(row["embedding"])
            nodes.append(_deserialize_node(data))

        return nodes

    async def get_nodes_by_layer(
        self,
        session_key: str,
        layer: NodeLayer
    ) -> list[NeuralNode]:
        """Get nodes by layer."""
        return await self.get_nodes_by_session(session_key, layer)

    async def get_all_nodes(self, session_key: str) -> list[NeuralNode]:
        """Get all nodes for a session."""
        return await self.get_nodes_by_session(session_key)

    # =========================================================================
    # EDGE OPERATIONS
    # =========================================================================

    async def save_edge(self, edge: NeuralEdge) -> None:
        """Save or update an edge."""
        async with self._lock:
            cursor = self._conn.cursor()
            data = _serialize_edge(edge)

            cursor.execute("""
                INSERT OR REPLACE INTO edges (edge_id, source_id, target_id, edge_type, data)
                VALUES (?, ?, ?, ?, ?)
            """, (
                edge.edge_id,
                edge.source_id,
                edge.target_id,
                edge.edge_type.value,
                json.dumps(data)
            ))
            self._conn.commit()

    async def get_edge(self, edge_id: str) -> NeuralEdge | None:
        """Get an edge by ID."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT data FROM edges WHERE edge_id = ?", (edge_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        return _deserialize_edge(json.loads(row["data"]))

    async def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge."""
        async with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
            deleted = cursor.rowcount > 0
            self._conn.commit()
            return deleted

    async def get_edges_from(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None
    ) -> list[NeuralEdge]:
        """Get outgoing edges from a node."""
        cursor = self._conn.cursor()

        if edge_types:
            placeholders = ",".join("?" * len(edge_types))
            cursor.execute(
                f"SELECT data FROM edges WHERE source_id = ? AND edge_type IN ({placeholders})",
                (node_id, *[et.value for et in edge_types])
            )
        else:
            cursor.execute("SELECT data FROM edges WHERE source_id = ?", (node_id,))

        return [_deserialize_edge(json.loads(row["data"])) for row in cursor.fetchall()]

    async def get_edges_to(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None
    ) -> list[NeuralEdge]:
        """Get incoming edges to a node."""
        cursor = self._conn.cursor()

        if edge_types:
            placeholders = ",".join("?" * len(edge_types))
            cursor.execute(
                f"SELECT data FROM edges WHERE target_id = ? AND edge_type IN ({placeholders})",
                (node_id, *[et.value for et in edge_types])
            )
        else:
            cursor.execute("SELECT data FROM edges WHERE target_id = ?", (node_id,))

        return [_deserialize_edge(json.loads(row["data"])) for row in cursor.fetchall()]

    async def get_edge_between(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType | None = None
    ) -> NeuralEdge | None:
        """Get edge between two nodes."""
        cursor = self._conn.cursor()

        if edge_type:
            cursor.execute(
                "SELECT data FROM edges WHERE source_id = ? AND target_id = ? AND edge_type = ?",
                (source_id, target_id, edge_type.value)
            )
        else:
            cursor.execute(
                "SELECT data FROM edges WHERE source_id = ? AND target_id = ?",
                (source_id, target_id)
            )

        row = cursor.fetchone()
        if row is None:
            return None

        return _deserialize_edge(json.loads(row["data"]))

    async def get_all_edges(self, session_key: str) -> list[NeuralEdge]:
        """Get all edges for nodes in a session."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT e.data FROM edges e
            INNER JOIN nodes n ON e.source_id = n.node_id
            WHERE n.session_key = ?
        """, (session_key,))

        return [_deserialize_edge(json.loads(row["data"])) for row in cursor.fetchall()]

    # =========================================================================
    # VECTOR SEARCH
    # =========================================================================

    async def vector_search(
        self,
        query_embedding: list[float],
        session_key: str,
        limit: int = 50,
        layer_filter: list[NodeLayer] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Search nodes by vector similarity.

        Uses brute-force cosine similarity (sqlite-vec integration TODO).
        """
        nodes = await self.get_nodes_by_session(session_key)

        if layer_filter:
            nodes = [n for n in nodes if n.layer in layer_filter]

        # Compute similarities
        scored: list[tuple[NeuralNode, float]] = []
        for node in nodes:
            if node.embedding:
                similarity = cosine_similarity(query_embedding, node.embedding)
                if similarity > 0:
                    scored.append((node, similarity))

        # Sort by similarity and return top-k
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # =========================================================================
    # BATCH OPERATIONS
    # =========================================================================

    async def save_nodes_batch(self, nodes: list[NeuralNode]) -> int:
        """Save multiple nodes."""
        if not nodes:
            return 0

        async with self._lock:
            cursor = self._conn.cursor()

            for node in nodes:
                data = _serialize_node(node)
                embedding_blob = self._embedding_to_blob(node.embedding)

                cursor.execute("""
                    INSERT OR REPLACE INTO nodes (node_id, session_key, layer, content, embedding, data, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    node.node_id,
                    node.session_key,
                    node.layer.value,
                    node.content,
                    embedding_blob,
                    json.dumps(data),
                    node.created_at.isoformat() if node.created_at else datetime.now(timezone.utc).isoformat()
                ))

            self._conn.commit()
            return len(nodes)

    async def save_edges_batch(self, edges: list[NeuralEdge]) -> int:
        """Save multiple edges."""
        if not edges:
            return 0

        async with self._lock:
            cursor = self._conn.cursor()

            for edge in edges:
                data = _serialize_edge(edge)
                cursor.execute("""
                    INSERT OR REPLACE INTO edges (edge_id, source_id, target_id, edge_type, data)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    edge.edge_id,
                    edge.source_id,
                    edge.target_id,
                    edge.edge_type.value,
                    json.dumps(data)
                ))

            self._conn.commit()
            return len(edges)

    # =========================================================================
    # SESSION OPERATIONS
    # =========================================================================

    async def clear_session(self, session_key: str) -> int:
        """Clear all data for a session."""
        async with self._lock:
            cursor = self._conn.cursor()

            # Get node IDs for this session
            cursor.execute("SELECT node_id FROM nodes WHERE session_key = ?", (session_key,))
            node_ids = [row["node_id"] for row in cursor.fetchall()]

            if not node_ids:
                return 0

            # Delete edges
            placeholders = ",".join("?" * len(node_ids))
            cursor.execute(
                f"DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                (*node_ids, *node_ids)
            )
            edge_count = cursor.rowcount

            # Delete nodes
            cursor.execute("DELETE FROM nodes WHERE session_key = ?", (session_key,))
            node_count = cursor.rowcount

            self._conn.commit()
            return node_count + edge_count

    async def get_session_statistics(self, session_key: str) -> dict[str, Any]:
        """Get statistics for a session."""
        cursor = self._conn.cursor()

        # Node count by layer
        cursor.execute("""
            SELECT layer, COUNT(*) as count FROM nodes
            WHERE session_key = ? GROUP BY layer
        """, (session_key,))
        layer_counts = {row["layer"]: row["count"] for row in cursor.fetchall()}

        # Total nodes
        total_nodes = sum(layer_counts.values())

        # Edge count by type
        cursor.execute("""
            SELECT e.edge_type, COUNT(*) as count FROM edges e
            INNER JOIN nodes n ON e.source_id = n.node_id
            WHERE n.session_key = ? GROUP BY e.edge_type
        """, (session_key,))
        edge_type_counts = {row["edge_type"]: row["count"] for row in cursor.fetchall()}

        # Total edges
        total_edges = sum(edge_type_counts.values())

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_layer": layer_counts,
            "edges_by_type": edge_type_counts,
            "storage_type": "sqlite",
            "has_sqlite_vec": self._has_sqlite_vec,
        }

    # =========================================================================
    # CATEGORY & DOMAIN OPERATIONS (MCP-specific)
    # =========================================================================

    async def list_categories(self, domain: str | None = None) -> list[dict[str, Any]]:
        """List all categories, optionally filtered by domain."""
        cursor = self._conn.cursor()

        if domain:
            cursor.execute(
                "SELECT id, name, description, domain FROM categories WHERE domain = ?",
                (domain,)
            )
        else:
            cursor.execute("SELECT id, name, description, domain FROM categories")

        return [dict(row) for row in cursor.fetchall()]

    async def create_category(self, name: str, description: str | None = None, domain: str | None = None) -> int:
        """Create a new category."""
        async with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "INSERT INTO categories (name, description, domain) VALUES (?, ?, ?)",
                (name, description, domain)
            )
            self._conn.commit()
            return cursor.lastrowid

    async def list_domains(self) -> list[dict[str, Any]]:
        """List all domains."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, name, description FROM domains")
        return [dict(row) for row in cursor.fetchall()]

    async def create_domain(self, name: str, description: str | None = None) -> int:
        """Create a new domain."""
        async with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "INSERT INTO domains (name, description) VALUES (?, ?)",
                (name, description)
            )
            self._conn.commit()
            return cursor.lastrowid

    async def list_sessions(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List all sessions with basic stats."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT session_key, COUNT(*) as node_count, MIN(created_at) as first_memory, MAX(created_at) as last_memory
            FROM nodes
            GROUP BY session_key
            ORDER BY last_memory DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))

        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
