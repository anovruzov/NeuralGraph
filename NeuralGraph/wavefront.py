"""Wavefront propagation through memory graph.

This module implements systolic-style wavefront propagation as seen in
the systolic array diagram - diagonal sweep through TIME × HIERARCHY.

Key principles from the images:
- Image 3 (Systolic Array): Diagonal wavefront propagation
  - Each node receives from LEFT (past/temporal) + TOP (context hierarchy)
  - Forward computation → Back substitution (propagate, then reflect)
  - Rectangular structure = temporal × hierarchical grid

- Image 1 (Natural Networks): Activation patterns
  - HIGH activation = waves in phase = MATCH
  - NO activation = waves out of phase = NO MATCH

Memory retrieval is wave propagation, not search:
1. Query enters as wave at t=0
2. Propagates through temporal edges (past → present)
3. Propagates through hierarchy (MESSAGE → EPISODE → TOPIC → PERSONA)
4. Constructive interference = MATCH
5. Destructive interference = MISMATCH

The diagonal sweep pattern:
     TIME →
   ┌─────────────────┐
H  │ ○ ○ ○ ○ ○ ○ ○ ○ │  MESSAGE layer
I  │   ○ ○ ○ ○ ○ ○ ○ │  EPISODE layer
E  │     ○ ○ ○ ○ ○ ○ │  TOPIC layer
R  │       ○ ○ ○ ○ ○ │  PERSONA layer
↓  └─────────────────┘
   Wavefront sweeps diagonally
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .data_types import EdgeType, NodeLayer, NeuralEdge, NeuralNode
from .interference import InterferenceScorer, InterferenceType, InterferenceResult

if TYPE_CHECKING:
    from .storage import NeuralGraphStorage


@dataclass
class WavefrontNode:
    """A node in the propagating wavefront."""
    node: NeuralNode
    activation: float
    phase: float
    interference_type: InterferenceType
    hop_depth: int
    path: list[str]  # Node IDs in path from seed


@dataclass
class WavefrontResult:
    """Result from wavefront propagation."""
    nodes: list[WavefrontNode]
    total_propagations: int
    constructive_count: int
    destructive_count: int
    partial_count: int
    max_depth_reached: int


@runtime_checkable
class StorageProtocol(Protocol):
    """Protocol for storage access."""

    async def get_node(self, node_id: str) -> NeuralNode | None:
        """Get a single node by ID."""
        ...

    async def get_edges_from(
        self,
        node_id: str,
        edge_types: list[EdgeType] | None = None
    ) -> list[NeuralEdge]:
        """Get outgoing edges from a node."""
        ...


class WavefrontPropagator:
    """Systolic-style wavefront propagation through memory graph.

    Like the diagonal pattern in the systolic array:
    - Query enters at t=0
    - Propagates through temporal edges (LEFT in array = past)
    - Propagates through hierarchy (TOP in array = context)
    - Activation flows in diagonal wavefront

    The propagation follows two axes:
    1. TEMPORAL axis: MESSAGE(t) → MESSAGE(t-1) → MESSAGE(t-2) ...
    2. HIERARCHICAL axis: MESSAGE → EPISODE → TOPIC → PERSONA

    At each node, interference is computed with the query wave:
    - Constructive = high activation = likely match
    - Destructive = low activation = likely mismatch
    """

    def __init__(
        self,
        storage: StorageProtocol,
        temporal_decay: float = 0.85,
        hierarchy_decay: float = 0.92,
        min_activation: float = 0.15
    ):
        """Initialize propagator.

        Args:
            storage: Storage backend for node/edge access
            temporal_decay: Decay factor per temporal hop
            hierarchy_decay: Decay factor per hierarchy hop
            min_activation: Minimum activation to continue propagation
        """
        self._storage = storage
        self._temporal_decay = temporal_decay
        self._hierarchy_decay = hierarchy_decay
        self._min_activation = min_activation

    async def propagate(
        self,
        query_wave: dict[str, float],
        seed_nodes: list[NeuralNode],
        max_time_hops: int = 5,
        max_hierarchy_hops: int = 2,
        reference_time: datetime | None = None
    ) -> WavefrontResult:
        """Propagate query wave through memory graph.

        Implements diagonal wavefront sweep through TIME × HIERARCHY grid.

        Args:
            query_wave: Query wave amplitudes by dimension
            seed_nodes: Initial seed nodes to start propagation
            max_time_hops: Maximum temporal propagation depth
            max_hierarchy_hops: Maximum hierarchy propagation depth
            reference_time: Reference time for phase computation

        Returns:
            WavefrontResult with activated nodes and statistics
        """
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)

        # Track visited nodes to avoid cycles
        visited: set[str] = set()

        # Current wavefront: {node_id: WavefrontNode}
        wavefront: dict[str, WavefrontNode] = {}

        # Results accumulator
        results: list[WavefrontNode] = []
        total_propagations = 0
        constructive_count = 0
        destructive_count = 0
        partial_count = 0
        max_depth_reached = 0

        # Initialize wavefront with seed nodes
        for node in seed_nodes:
            if node.node_id in visited:
                continue

            phase = InterferenceScorer.compute_temporal_phase(
                node.created_at if hasattr(node, 'created_at') and node.created_at else reference_time,
                reference_time
            )

            # Compute initial interference
            activation, itype = InterferenceScorer.score_memory_match(
                query_wave, node, reference_time
            )

            wf_node = WavefrontNode(
                node=node,
                activation=min(1.0, activation + 0.5),  # Seed boost
                phase=phase,
                interference_type=itype,
                hop_depth=0,
                path=[node.node_id]
            )

            wavefront[node.node_id] = wf_node
            visited.add(node.node_id)

            # Count interference types
            if itype == InterferenceType.CONSTRUCTIVE:
                constructive_count += 1
            elif itype == InterferenceType.DESTRUCTIVE:
                destructive_count += 1
            else:
                partial_count += 1

        # Diagonal sweep: iterate through combined temporal + hierarchy hops
        max_sweeps = max_time_hops + max_hierarchy_hops

        for sweep in range(max_sweeps):
            if not wavefront:
                break

            # Process current wavefront
            next_wavefront: dict[str, WavefrontNode] = {}

            for node_id, wf_node in wavefront.items():
                # Only propagate if activation above threshold
                if wf_node.activation < self._min_activation:
                    continue

                total_propagations += 1
                max_depth_reached = max(max_depth_reached, wf_node.hop_depth)

                # Add to results
                results.append(wf_node)

                # Propagate to neighbors if within hop limits
                temporal_hops_left = max_time_hops - wf_node.hop_depth
                hierarchy_hops_left = max_hierarchy_hops - (wf_node.hop_depth // 2)

                if temporal_hops_left > 0 or hierarchy_hops_left > 0:
                    # Get neighbor activations
                    neighbors = await self._propagate_from_node(
                        wf_node,
                        query_wave,
                        reference_time,
                        visited,
                        temporal_hops_left > 0,
                        hierarchy_hops_left > 0
                    )

                    for neighbor in neighbors:
                        if neighbor.node.node_id not in next_wavefront:
                            next_wavefront[neighbor.node.node_id] = neighbor
                            visited.add(neighbor.node.node_id)

                            # Count interference types
                            if neighbor.interference_type == InterferenceType.CONSTRUCTIVE:
                                constructive_count += 1
                            elif neighbor.interference_type == InterferenceType.DESTRUCTIVE:
                                destructive_count += 1
                            else:
                                partial_count += 1

            # Move to next wavefront
            wavefront = next_wavefront

        # Sort results by activation (highest first)
        results.sort(key=lambda x: x.activation, reverse=True)

        return WavefrontResult(
            nodes=results,
            total_propagations=total_propagations,
            constructive_count=constructive_count,
            destructive_count=destructive_count,
            partial_count=partial_count,
            max_depth_reached=max_depth_reached
        )

    async def _propagate_from_node(
        self,
        source: WavefrontNode,
        query_wave: dict[str, float],
        reference_time: datetime,
        visited: set[str],
        allow_temporal: bool,
        allow_hierarchy: bool
    ) -> list[WavefrontNode]:
        """Propagate from a single node to its neighbors.

        Args:
            source: Source wavefront node
            query_wave: Query wave amplitudes
            reference_time: Reference time for phase computation
            visited: Set of already visited node IDs
            allow_temporal: Whether to propagate through temporal edges
            allow_hierarchy: Whether to propagate through hierarchy

        Returns:
            List of activated neighbor WavefrontNodes
        """
        neighbors: list[WavefrontNode] = []

        # Determine which edge types to follow
        edge_types = []
        if allow_temporal:
            edge_types.extend([EdgeType.TEMPORAL, EdgeType.SEMANTIC, EdgeType.ENTITY])
        if allow_hierarchy:
            edge_types.append(EdgeType.HIERARCHY)

        # Get edges from storage
        edges = await self._storage.get_edges_from(source.node.node_id, edge_types)

        # Limit to top edges to prevent explosion
        edges = edges[:8]

        for edge in edges:
            target_id = edge.target_id
            if target_id in visited:
                continue

            # Get target node
            target_node = await self._storage.get_node(target_id)
            if target_node is None:
                continue

            # Compute phase for target
            target_phase = InterferenceScorer.compute_temporal_phase(
                target_node.created_at if hasattr(target_node, 'created_at') and target_node.created_at else reference_time,
                reference_time
            )

            # Compute interference
            result = InterferenceScorer.compute_interference(
                query_amps=query_wave,
                memory_amps=target_node.wave_amplitudes or {},
                query_phase=0.0,  # Query is reference
                memory_phase=target_phase
            )

            # Determine decay based on edge type
            if edge.edge_type == EdgeType.HIERARCHY:
                decay = self._hierarchy_decay
            else:
                decay = self._temporal_decay

            # Compute activation with decay and edge weight
            edge_weight = edge.effective_weight if hasattr(edge, 'effective_weight') else edge.base_weight
            new_activation = source.activation * decay * abs(edge_weight) * (result.activation + 0.3)

            # Boost for constructive interference
            if result.interference_type == InterferenceType.CONSTRUCTIVE:
                new_activation *= 1.3
            elif result.interference_type == InterferenceType.DESTRUCTIVE:
                new_activation *= 0.5

            # Skip if too weak
            if new_activation < self._min_activation * 0.5:
                continue

            neighbors.append(WavefrontNode(
                node=target_node,
                activation=min(1.0, new_activation),
                phase=target_phase,
                interference_type=result.interference_type,
                hop_depth=source.hop_depth + 1,
                path=source.path + [target_id]
            ))

        return neighbors


class DiagonalSweepRetriever:
    """High-level retriever using diagonal wavefront sweep.

    Combines wavefront propagation with interference scoring
    to retrieve memories that resonate with the query.
    """

    def __init__(
        self,
        propagator: WavefrontPropagator,
        constructive_boost: float = 0.4,
        destructive_penalty: float = 0.5
    ):
        """Initialize retriever.

        Args:
            propagator: WavefrontPropagator instance
            constructive_boost: Score boost for constructive interference
            destructive_penalty: Score multiplier for destructive interference
        """
        self._propagator = propagator
        self._constructive_boost = constructive_boost
        self._destructive_penalty = destructive_penalty

    async def retrieve(
        self,
        query_wave: dict[str, float],
        seed_nodes: list[NeuralNode],
        limit: int = 20
    ) -> list[tuple[NeuralNode, float]]:
        """Retrieve memories using wavefront propagation.

        Args:
            query_wave: Query wave amplitudes
            seed_nodes: Initial seed nodes
            limit: Maximum number of results

        Returns:
            List of (node, score) tuples sorted by score
        """
        result = await self._propagator.propagate(
            query_wave=query_wave,
            seed_nodes=seed_nodes,
            max_time_hops=5,
            max_hierarchy_hops=2
        )

        # Convert WavefrontNodes to (node, score) tuples
        scored: list[tuple[NeuralNode, float]] = []

        for wf_node in result.nodes:
            # Base score from activation
            score = wf_node.activation

            # Apply interference-based adjustments
            if wf_node.interference_type == InterferenceType.CONSTRUCTIVE:
                score += self._constructive_boost
            elif wf_node.interference_type == InterferenceType.DESTRUCTIVE:
                score *= self._destructive_penalty

            # Depth penalty (prefer shallower matches)
            depth_penalty = 1.0 / (1.0 + 0.1 * wf_node.hop_depth)
            score *= depth_penalty

            scored.append((wf_node.node, score))

        # Sort by score and return top results
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]
