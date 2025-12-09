"""Storage interface and in-memory implementation for Neural Memory Graph.

Defines the abstract storage protocol and provides an in-memory
implementation for testing and development.

The storage layer handles:
- Node CRUD operations
- Edge CRUD operations
- Vector similarity search
- Graph traversal queries
- Session-based isolation
"""

from __future__ import annotations

import asyncio
import heapq
import math
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .data_types import (
    ConsolidationState,
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
    cosine_similarity,
)


class NeuralGraphStorage(ABC):
    """Abstract base class for neural graph storage.

    Implementations must provide all node and edge operations,
    as well as vector search capabilities.
    """

    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================

    @abstractmethod
    async def save_node(self, node: NeuralNode) -> None:
        """Save or update a node.

        Args:
            node: Node to save
        """
        ...

    @abstractmethod
    async def get_node(self, node_id: str) -> NeuralNode | None:
        """Get a node by ID.

        Args:
            node_id: Node identifier

        Returns:
            Node if found, None otherwise
        """
        ...

    @abstractmethod
    async def delete_node(self, node_id: str) -> bool:
        """Delete a node and its edges.

        Args:
            node_id: Node identifier

        Returns:
            True if deleted, False if not found
        """
        ...

    @abstractmethod
    async def get_nodes_by_session(
        self,
        session_key: str,
        layer: NodeLayer | None = None
    ) -> list[NeuralNode]:
        """Get all nodes for a session.

        Args:
            session_key: Session identifier
            layer: Optional layer filter

        Returns:
            List of nodes
        """
        ...

    @abstractmethod
    async def get_nodes_by_layer(
        self,
        session_key: str,
        layer: NodeLayer
    ) -> list[NeuralNode]:
        """Get nodes by layer.

        Args:
            session_key: Session identifier
            layer: Layer to filter by

        Returns:
            List of nodes in that layer
        """
        ...

    @abstractmethod
    async def get_all_nodes(self, session_key: str) -> list[NeuralNode]:
        """Get all nodes for a session.

        Args:
            session_key: Session identifier

        Returns:
            List of all nodes
        """
        ...

    # =========================================================================
    # EDGE OPERATIONS
    # =========================================================================

    @abstractmethod
    async def save_edge(self, edge: NeuralEdge) -> None:
        """Save or update an edge.

        Args:
            edge: Edge to save
        """
        ...

    @abstractmethod
    async def get_edge(self, edge_id: str) -> NeuralEdge | None:
        """Get an edge by ID.

        Args:
            edge_id: Edge identifier

        Returns:
            Edge if found, None otherwise
        """
        ...

    @abstractmethod
    async def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge.

        Args:
            edge_id: Edge identifier

        Returns:
            True if deleted, False if not found
        """
        ...

    @abstractmethod
    async def get_edges_from(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None
    ) -> list[NeuralEdge]:
        """Get outgoing edges from a node.

        Args:
            node_id: Source node ID
            edge_types: Optional filter by edge types

        Returns:
            List of outgoing edges
        """
        ...

    @abstractmethod
    async def get_edges_to(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None
    ) -> list[NeuralEdge]:
        """Get incoming edges to a node.

        Args:
            node_id: Target node ID
            edge_types: Optional filter by edge types

        Returns:
            List of incoming edges
        """
        ...

    @abstractmethod
    async def get_edge_between(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType | None = None
    ) -> NeuralEdge | None:
        """Get edge between two nodes.

        Args:
            source_id: Source node ID
            target_id: Target node ID
            edge_type: Optional specific edge type

        Returns:
            Edge if found, None otherwise
        """
        ...

    @abstractmethod
    async def get_all_edges(self, session_key: str) -> list[NeuralEdge]:
        """Get all edges for nodes in a session.

        Args:
            session_key: Session identifier

        Returns:
            List of all edges
        """
        ...

    # =========================================================================
    # VECTOR SEARCH
    # =========================================================================

    @abstractmethod
    async def vector_search(
        self,
        query_embedding: list[float],
        session_key: str,
        limit: int = 50,
        layer_filter: list[NodeLayer] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Search nodes by vector similarity.

        Args:
            query_embedding: Query embedding vector
            session_key: Session identifier
            limit: Maximum results
            layer_filter: Optional layer filter

        Returns:
            List of (node, similarity_score) tuples
        """
        ...

    # =========================================================================
    # BATCH OPERATIONS
    # =========================================================================

    @abstractmethod
    async def save_nodes_batch(self, nodes: list[NeuralNode]) -> int:
        """Save multiple nodes.

        Args:
            nodes: Nodes to save

        Returns:
            Number of nodes saved
        """
        ...

    @abstractmethod
    async def save_edges_batch(self, edges: list[NeuralEdge]) -> int:
        """Save multiple edges.

        Args:
            edges: Edges to save

        Returns:
            Number of edges saved
        """
        ...

    # =========================================================================
    # SESSION OPERATIONS
    # =========================================================================

    @abstractmethod
    async def clear_session(self, session_key: str) -> int:
        """Clear all data for a session.

        Args:
            session_key: Session identifier

        Returns:
            Number of items deleted
        """
        ...

    @abstractmethod
    async def get_session_statistics(self, session_key: str) -> dict[str, Any]:
        """Get statistics for a session.

        Args:
            session_key: Session identifier

        Returns:
            Statistics dictionary
        """
        ...


class InMemoryNeuralGraphStorage(NeuralGraphStorage):
    """In-memory implementation of neural graph storage.

    Suitable for testing and development. For production,
    use a persistent storage implementation.
    """

    def __init__(self):
        """Initialize in-memory storage."""
        self._nodes: dict[str, NeuralNode] = {}
        self._edges: dict[str, NeuralEdge] = {}

        # Indexes for efficient lookup
        self._nodes_by_session: dict[str, set[str]] = defaultdict(set)
        self._edges_from: dict[str, set[str]] = defaultdict(set)
        self._edges_to: dict[str, set[str]] = defaultdict(set)

        # NEW INDICES: O(1) lookup instead of O(n) iteration
        self._edges_by_session: dict[str, set[str]] = defaultdict(set)
        self._nodes_by_entity: dict[str, set[str]] = defaultdict(set)
        self._nodes_by_layer: dict[str, dict[NodeLayer, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )

        # Lock for thread safety
        self._lock = asyncio.Lock()

    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================

    async def save_node(self, node: NeuralNode) -> None:
        """Save or update a node."""
        async with self._lock:
            # Clean up old entity index if updating
            old_node = self._nodes.get(node.node_id)
            if old_node:
                for entity_id in old_node.entity_ids:
                    self._nodes_by_entity[entity_id].discard(node.node_id)
                self._nodes_by_layer[old_node.session_key][old_node.layer].discard(node.node_id)

            # Save node
            self._nodes[node.node_id] = node
            self._nodes_by_session[node.session_key].add(node.node_id)

            # NEW: Maintain entity index for O(1) entity lookup
            for entity_id in node.entity_ids:
                self._nodes_by_entity[entity_id].add(node.node_id)

            # NEW: Maintain layer index for O(1) layer lookup
            self._nodes_by_layer[node.session_key][node.layer].add(node.node_id)

    async def get_node(self, node_id: str) -> NeuralNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    async def delete_node(self, node_id: str) -> bool:
        """Delete a node and its edges."""
        async with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return False

            # Delete associated edges
            edges_to_delete = (
                list(self._edges_from.get(node_id, set())) +
                list(self._edges_to.get(node_id, set()))
            )
            for edge_id in edges_to_delete:
                if edge_id in self._edges:
                    edge = self._edges[edge_id]
                    del self._edges[edge_id]
                    self._edges_from[edge.source_id].discard(edge_id)
                    self._edges_to[edge.target_id].discard(edge_id)
                    # Clean up session edge index
                    self._edges_by_session[node.session_key].discard(edge_id)

            # Clean up entity index
            for entity_id in node.entity_ids:
                self._nodes_by_entity[entity_id].discard(node_id)

            # Clean up layer index
            self._nodes_by_layer[node.session_key][node.layer].discard(node_id)

            # Delete node
            del self._nodes[node_id]
            self._nodes_by_session[node.session_key].discard(node_id)

            return True

    async def get_nodes_by_session(
        self,
        session_key: str,
        layer: NodeLayer | None = None
    ) -> list[NeuralNode]:
        """Get all nodes for a session."""
        node_ids = self._nodes_by_session.get(session_key, set())
        nodes = [self._nodes[nid] for nid in node_ids if nid in self._nodes]

        if layer is not None:
            nodes = [n for n in nodes if n.layer == layer]

        return nodes

    async def get_nodes_by_layer(
        self,
        session_key: str,
        layer: NodeLayer
    ) -> list[NeuralNode]:
        """Get nodes by layer. OPTIMIZED: Uses layer index for O(1) lookup."""
        # Use the layer index instead of filtering all nodes
        node_ids = self._nodes_by_layer[session_key][layer]
        return [self._nodes[nid] for nid in node_ids if nid in self._nodes]

    async def get_all_nodes(self, session_key: str) -> list[NeuralNode]:
        """Get all nodes for a session."""
        return await self.get_nodes_by_session(session_key)

    # =========================================================================
    # EDGE OPERATIONS
    # =========================================================================

    async def save_edge(self, edge: NeuralEdge) -> None:
        """Save or update an edge."""
        async with self._lock:
            # Remove from old indexes if updating
            if edge.edge_id in self._edges:
                old_edge = self._edges[edge.edge_id]
                self._edges_from[old_edge.source_id].discard(edge.edge_id)
                self._edges_to[old_edge.target_id].discard(edge.edge_id)
                # Clean up old session index (need to find session from source node)
                source_node = self._nodes.get(old_edge.source_id)
                if source_node:
                    self._edges_by_session[source_node.session_key].discard(edge.edge_id)

            # Save edge
            self._edges[edge.edge_id] = edge

            # Update indexes
            self._edges_from[edge.source_id].add(edge.edge_id)
            self._edges_to[edge.target_id].add(edge.edge_id)

            # NEW: Maintain session edge index for O(1) get_all_edges
            source_node = self._nodes.get(edge.source_id)
            if source_node:
                self._edges_by_session[source_node.session_key].add(edge.edge_id)

    async def get_edge(self, edge_id: str) -> NeuralEdge | None:
        """Get an edge by ID."""
        return self._edges.get(edge_id)

    async def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge."""
        async with self._lock:
            edge = self._edges.get(edge_id)
            if edge is None:
                return False

            # Clean up session edge index
            source_node = self._nodes.get(edge.source_id)
            if source_node:
                self._edges_by_session[source_node.session_key].discard(edge_id)

            del self._edges[edge_id]
            self._edges_from[edge.source_id].discard(edge_id)
            self._edges_to[edge.target_id].discard(edge_id)

            return True

    async def get_edges_from(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None
    ) -> list[NeuralEdge]:
        """Get outgoing edges from a node."""
        edge_ids = self._edges_from.get(node_id, set())
        edges = [self._edges[eid] for eid in edge_ids if eid in self._edges]

        if edge_types:
            edges = [e for e in edges if e.edge_type in edge_types]

        return edges

    async def get_edges_to(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None
    ) -> list[NeuralEdge]:
        """Get incoming edges to a node."""
        edge_ids = self._edges_to.get(node_id, set())
        edges = [self._edges[eid] for eid in edge_ids if eid in self._edges]

        if edge_types:
            edges = [e for e in edges if e.edge_type in edge_types]

        return edges

    async def get_edge_between(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType | None = None
    ) -> NeuralEdge | None:
        """Get edge between two nodes."""
        edges_from_source = self._edges_from.get(source_id, set())

        for edge_id in edges_from_source:
            edge = self._edges.get(edge_id)
            if edge and edge.target_id == target_id:
                if edge_type is None or edge.edge_type == edge_type:
                    return edge

        return None

    async def get_all_edges(self, session_key: str) -> list[NeuralEdge]:
        """Get all edges for nodes in a session.

        OPTIMIZED: Uses _edges_by_session index for O(1) lookup instead of
        iterating through all nodes O(n) and collecting their edges.
        """
        # Direct O(1) lookup from session edge index
        edge_ids = self._edges_by_session.get(session_key, set())
        return [self._edges[eid] for eid in edge_ids if eid in self._edges]

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

        OPTIMIZED: Uses heapq.nlargest for O(n log k) instead of O(n log n) sort.
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

        # Use heapq for O(n log k) instead of O(n log n) full sort
        return heapq.nlargest(limit, scored, key=lambda x: x[1])

    # =========================================================================
    # BATCH OPERATIONS
    # =========================================================================

    async def save_nodes_batch(self, nodes: list[NeuralNode]) -> int:
        """Save multiple nodes.

        OPTIMIZED: Single lock acquisition for all nodes instead of N lock
        acquisitions. Reduces lock contention and context switching.
        """
        if not nodes:
            return 0

        async with self._lock:
            for node in nodes:
                # Clean up old indices if updating
                old_node = self._nodes.get(node.node_id)
                if old_node:
                    for entity_id in old_node.entity_ids:
                        self._nodes_by_entity[entity_id].discard(node.node_id)
                    self._nodes_by_layer[old_node.session_key][old_node.layer].discard(node.node_id)

                # Save node
                self._nodes[node.node_id] = node
                self._nodes_by_session[node.session_key].add(node.node_id)

                # Maintain entity index
                for entity_id in node.entity_ids:
                    self._nodes_by_entity[entity_id].add(node.node_id)

                # Maintain layer index
                self._nodes_by_layer[node.session_key][node.layer].add(node.node_id)

        return len(nodes)

    async def save_edges_batch(self, edges: list[NeuralEdge]) -> int:
        """Save multiple edges.

        OPTIMIZED: Single lock acquisition for all edges instead of N lock
        acquisitions. Reduces lock contention and context switching.
        """
        if not edges:
            return 0

        async with self._lock:
            for edge in edges:
                # Remove from old indexes if updating
                if edge.edge_id in self._edges:
                    old_edge = self._edges[edge.edge_id]
                    self._edges_from[old_edge.source_id].discard(edge.edge_id)
                    self._edges_to[old_edge.target_id].discard(edge.edge_id)
                    source_node = self._nodes.get(old_edge.source_id)
                    if source_node:
                        self._edges_by_session[source_node.session_key].discard(edge.edge_id)

                # Save edge
                self._edges[edge.edge_id] = edge

                # Update indexes
                self._edges_from[edge.source_id].add(edge.edge_id)
                self._edges_to[edge.target_id].add(edge.edge_id)

                # Maintain session edge index
                source_node = self._nodes.get(edge.source_id)
                if source_node:
                    self._edges_by_session[source_node.session_key].add(edge.edge_id)

        return len(edges)

    # =========================================================================
    # SESSION OPERATIONS
    # =========================================================================

    async def clear_session(self, session_key: str) -> int:
        """Clear all data for a session."""
        async with self._lock:
            node_ids = list(self._nodes_by_session.get(session_key, set()))
            count = 0

            for node_id in node_ids:
                node = self._nodes.get(node_id)

                # Delete edges
                edges_to_delete = (
                    list(self._edges_from.get(node_id, set())) +
                    list(self._edges_to.get(node_id, set()))
                )
                for edge_id in edges_to_delete:
                    if edge_id in self._edges:
                        edge = self._edges[edge_id]
                        del self._edges[edge_id]
                        self._edges_from[edge.source_id].discard(edge_id)
                        self._edges_to[edge.target_id].discard(edge_id)
                        count += 1

                # Clean up entity index
                if node:
                    for entity_id in node.entity_ids:
                        self._nodes_by_entity[entity_id].discard(node_id)

                # Delete node
                if node_id in self._nodes:
                    del self._nodes[node_id]
                    count += 1

            # Clear all session-level indices
            self._nodes_by_session[session_key].clear()
            self._edges_by_session[session_key].clear()
            self._nodes_by_layer[session_key].clear()

            return count

    async def get_session_statistics(self, session_key: str) -> dict[str, Any]:
        """Get statistics for a session."""
        nodes = await self.get_nodes_by_session(session_key)
        edges = await self.get_all_edges(session_key)

        # Count by layer
        layer_counts = defaultdict(int)
        for node in nodes:
            layer_counts[node.layer.name] += 1

        # Count by edge type
        edge_type_counts = defaultdict(int)
        for edge in edges:
            edge_type_counts[edge.edge_type.value] += 1

        # Average heat and importance
        avg_heat = sum(n.heat_score for n in nodes) / len(nodes) if nodes else 0
        avg_importance = sum(n.importance_score for n in nodes) / len(nodes) if nodes else 0

        # Consolidation state counts
        state_counts = defaultdict(int)
        for node in nodes:
            state_counts[node.consolidation_state.value] += 1

        return {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "nodes_by_layer": dict(layer_counts),
            "edges_by_type": dict(edge_type_counts),
            "average_heat_score": avg_heat,
            "average_importance_score": avg_importance,
            "consolidation_states": dict(state_counts),
        }

    # =========================================================================
    # ADDITIONAL QUERY METHODS
    # =========================================================================

    async def get_neighbors(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None,
        direction: str = "outgoing"
    ) -> list[tuple[NeuralNode, NeuralEdge]]:
        """Get neighboring nodes.

        Args:
            node_id: Center node ID
            edge_types: Optional edge type filter
            direction: "outgoing", "incoming", or "both"

        Returns:
            List of (neighbor_node, connecting_edge) tuples
        """
        neighbors: list[tuple[NeuralNode, NeuralEdge]] = []

        if direction in ("outgoing", "both"):
            edges = await self.get_edges_from(node_id, edge_types)
            for edge in edges:
                neighbor = await self.get_node(edge.target_id)
                if neighbor:
                    neighbors.append((neighbor, edge))

        if direction in ("incoming", "both"):
            edges = await self.get_edges_to(node_id, edge_types)
            for edge in edges:
                neighbor = await self.get_node(edge.source_id)
                if neighbor:
                    neighbors.append((neighbor, edge))

        return neighbors

    async def get_children(self, node_id: str) -> list[NeuralNode]:
        """Get child nodes in hierarchy."""
        node = await self.get_node(node_id)
        if node is None:
            return []

        children = []
        for child_id in node.child_ids:
            child = await self.get_node(child_id)
            if child:
                children.append(child)

        return children

    async def get_parent(self, node_id: str) -> NeuralNode | None:
        """Get parent node in hierarchy."""
        node = await self.get_node(node_id)
        if node is None or node.parent_id is None:
            return None

        return await self.get_node(node.parent_id)

    async def get_edges_batch(
        self,
        pairs: list[tuple[str, str]],
        edge_type: EdgeType | None = None
    ) -> list[NeuralEdge | None]:
        """Get edges between multiple node pairs in batch.

        OPTIMIZED: Single method call instead of N individual get_edge_between calls.
        Uses canonical ordering (source_id < target_id) for CO_ACTIVATION edges.

        Args:
            pairs: List of (source_id, target_id) tuples
            edge_type: Optional edge type filter

        Returns:
            List of edges (None for missing edges) in same order as input pairs
        """
        results: list[NeuralEdge | None] = []

        for source_id, target_id in pairs:
            # For CO_ACTIVATION edges, use canonical ordering
            edges_from_source = self._edges_from.get(source_id, set())

            found = None
            for edge_id in edges_from_source:
                edge = self._edges.get(edge_id)
                if edge and edge.target_id == target_id:
                    if edge_type is None or edge.edge_type == edge_type:
                        found = edge
                        break

            results.append(found)

        return results

    async def find_nodes_by_entities(
        self,
        entity_ids: list[str],
        session_key: str
    ) -> list[NeuralNode]:
        """Find nodes that reference specific entities.

        OPTIMIZED: Uses _nodes_by_entity index for O(1) lookup per entity
        instead of O(n) iteration through all session nodes.

        Args:
            entity_ids: Entity IDs to search for
            session_key: Session identifier

        Returns:
            Nodes that reference any of the entities
        """
        # Use entity index for O(1) lookup per entity
        session_node_ids = self._nodes_by_session.get(session_key, set())
        matching_ids: set[str] = set()

        for entity_id in entity_ids:
            # Get all nodes referencing this entity
            entity_node_ids = self._nodes_by_entity.get(entity_id, set())
            # Filter to only those in the requested session
            matching_ids.update(entity_node_ids & session_node_ids)

        return [self._nodes[nid] for nid in matching_ids if nid in self._nodes]

    async def get_hot_nodes(
        self,
        session_key: str,
        threshold: float = 5.0,
        limit: int = 50
    ) -> list[NeuralNode]:
        """Get nodes with high heat scores.

        OPTIMIZED: Uses heapq.nlargest for O(n log k) instead of O(n log n) sort.

        Args:
            session_key: Session identifier
            threshold: Minimum heat score
            limit: Maximum results

        Returns:
            Hot nodes sorted by heat score
        """
        nodes = await self.get_nodes_by_session(session_key)
        hot = [n for n in nodes if n.heat_score >= threshold]
        # Use heapq for O(n log k) instead of O(n log n) full sort
        return heapq.nlargest(limit, hot, key=lambda n: n.heat_score)

    async def get_cold_nodes(
        self,
        session_key: str,
        threshold: float = 0.5,
        min_age_days: float = 7.0,
        limit: int = 50
    ) -> list[NeuralNode]:
        """Get nodes with low heat scores (candidates for eviction).

        OPTIMIZED: Uses heapq.nsmallest for O(n log k) instead of O(n log n) sort.

        Args:
            session_key: Session identifier
            threshold: Maximum heat score
            min_age_days: Minimum age in days
            limit: Maximum results

        Returns:
            Cold nodes sorted by heat score ascending
        """
        nodes = await self.get_nodes_by_session(session_key)
        cold = [
            n for n in nodes
            if n.heat_score <= threshold and n.days_since_creation >= min_age_days
        ]
        # Use heapq for O(n log k) instead of O(n log n) full sort
        return heapq.nsmallest(limit, cold, key=lambda n: n.heat_score)
