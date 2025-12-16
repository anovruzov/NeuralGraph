"""Temporal chain management for Neural Memory Graph.

Implements biological neuron dynamics (Image 4):
- Temporal edges with direction and propagation delay
- Long-term potentiation (LTP) through repeated co-activation
- Temporal summation of signals over time windows
- Causal chain linking

The temporal system enables:
- Tracking "what happened before/after" relationships
- Strengthening connections through use (Hebbian learning)
- Following causal chains across memories
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .data_types import (
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
    generate_edge_id,
)

if TYPE_CHECKING:
    from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


@dataclass
class TemporalConfig:
    """Configuration for temporal chain management."""

    # Temporal edge creation
    max_time_gap_hours: float = 24.0  # Max gap for automatic temporal linking
    min_propagation_delay_ms: float = 10.0
    max_propagation_delay_ms: float = 10000.0

    # Temporal summation
    summation_window_hours: float = 24.0
    summation_threshold: float = 0.5

    # Causal linking
    causal_confidence_threshold: float = 0.6
    causal_time_order_required: bool = True

    # LTP parameters
    ltp_activation_threshold: int = 2  # Minimum activations for LTP effect
    ltp_max_boost: float = 3.0
    ltp_decay_rate: float = 0.01  # Per day


class TemporalChainManager:
    """Manages temporal edges and causal chains (Image 4: Biological Neuron).

    Implements:
    - BEFORE/AFTER temporal edges with propagation delay
    - Long-term potentiation (LTP) for Hebbian learning
    - Temporal summation (signals over time window)
    - Causal chain following

    The temporal dynamics enable:
    - Memory of sequences and causation
    - Strengthening of frequently co-activated memories
    - Decay of unused connections
    """

    def __init__(
        self,
        storage: NeuralGraphStorage,
        config: TemporalConfig | None = None
    ):
        """Initialize temporal chain manager.

        Args:
            storage: Neural graph storage backend
            config: Temporal configuration
        """
        self._storage = storage
        self._config = config or TemporalConfig()

    # =========================================================================
    # TEMPORAL EDGE CREATION
    # =========================================================================

    async def create_temporal_edge(
        self,
        source_node: NeuralNode,
        target_node: NeuralNode,
        direction: str = "before"
    ) -> NeuralEdge:
        """Create a temporal edge with direction and delay.

        The source "happened before" the target if direction="before".

        Args:
            source_node: Earlier node
            target_node: Later node
            direction: "before" means source happened before target

        Returns:
            Created temporal edge
        """
        # Compute propagation delay based on time difference
        if source_node.created_at and target_node.created_at:
            # Ensure both have timezone info
            source_time = source_node.created_at
            target_time = target_node.created_at
            if source_time.tzinfo is None:
                source_time = source_time.replace(tzinfo=timezone.utc)
            if target_time.tzinfo is None:
                target_time = target_time.replace(tzinfo=timezone.utc)

            time_diff_seconds = abs((target_time - source_time).total_seconds())
            # Scale delay: longer gaps = longer propagation delay
            delay_ms = min(
                self._config.max_propagation_delay_ms,
                max(
                    self._config.min_propagation_delay_ms,
                    time_diff_seconds * 10  # 10ms per second of gap
                )
            )
        else:
            delay_ms = self._config.min_propagation_delay_ms

        # Compute base weight from temporal proximity
        # Closer in time = stronger link
        hours_diff = delay_ms / (1000 * 3600)
        base_weight = math.exp(-hours_diff / self._config.max_time_gap_hours)

        edge = NeuralEdge(
            edge_id=generate_edge_id(),
            source_id=source_node.node_id,
            target_id=target_node.node_id,
            edge_type=EdgeType.TEMPORAL,
            base_weight=base_weight,
            propagation_delay_ms=delay_ms,
            confidence=1.0,
            metadata={"direction": direction, "time_diff_seconds": time_diff_seconds},
        )

        await self._storage.save_edge(edge)

        logger.debug(
            f"Created temporal edge {source_node.node_id[:8]} -> {target_node.node_id[:8]} "
            f"(delay={delay_ms:.0f}ms, weight={base_weight:.3f})"
        )

        return edge

    async def build_temporal_chain(
        self,
        nodes: list[NeuralNode],
        session_key: str
    ) -> list[NeuralEdge]:
        """Build temporal chain from sequence of nodes.

        Creates BEFORE edges between consecutive nodes in time order.

        Args:
            nodes: Nodes to chain
            session_key: Session identifier

        Returns:
            List of created temporal edges
        """
        if len(nodes) < 2:
            return []

        # Sort by creation time
        sorted_nodes = sorted(
            nodes,
            key=lambda n: n.created_at if n.created_at else datetime.min.replace(tzinfo=timezone.utc)
        )

        edges = []
        for i in range(len(sorted_nodes) - 1):
            source = sorted_nodes[i]
            target = sorted_nodes[i + 1]

            # Check time gap isn't too large
            if source.created_at and target.created_at:
                source_time = source.created_at
                target_time = target.created_at
                if source_time.tzinfo is None:
                    source_time = source_time.replace(tzinfo=timezone.utc)
                if target_time.tzinfo is None:
                    target_time = target_time.replace(tzinfo=timezone.utc)

                hours_diff = (target_time - source_time).total_seconds() / 3600
                if hours_diff > self._config.max_time_gap_hours:
                    continue

            edge = await self.create_temporal_edge(source, target, direction="before")
            edges.append(edge)

        logger.info(f"Built temporal chain with {len(edges)} edges")
        return edges

    async def auto_link_temporal(
        self,
        session_key: str,
        layer: NodeLayer = NodeLayer.MESSAGE
    ) -> list[NeuralEdge]:
        """Automatically create temporal links for unlinked nodes.

        Args:
            session_key: Session identifier
            layer: Layer to process

        Returns:
            List of created edges
        """
        nodes = await self._storage.get_nodes_by_layer(session_key, layer)

        # Filter nodes without temporal edges
        unlinked = []
        for node in nodes:
            edges_from = await self._storage.get_edges_from(
                node.node_id, edge_types=[EdgeType.TEMPORAL]
            )
            edges_to = await self._storage.get_edges_to(
                node.node_id, edge_types=[EdgeType.TEMPORAL]
            )
            if not edges_from and not edges_to:
                unlinked.append(node)

        if len(unlinked) < 2:
            return []

        return await self.build_temporal_chain(unlinked, session_key)

    # =========================================================================
    # CAUSAL EDGE CREATION
    # =========================================================================

    async def create_causal_edge(
        self,
        cause_node: NeuralNode,
        effect_node: NeuralNode,
        confidence: float = 0.8,
        causal_type: str = "causes"
    ) -> NeuralEdge | None:
        """Create a causal edge from cause to effect.

        Causal edges represent explicit cause-effect relationships
        extracted from content or inferred from patterns.

        Args:
            cause_node: Cause node
            effect_node: Effect node
            confidence: Confidence in causation [0, 1]
            causal_type: Type of causation ("causes", "enables", "prevents")

        Returns:
            Created causal edge, or None if validation fails
        """
        # Validate confidence threshold
        if confidence < self._config.causal_confidence_threshold:
            logger.debug(
                f"Causal edge rejected: confidence {confidence} < "
                f"threshold {self._config.causal_confidence_threshold}"
            )
            return None

        # Optionally validate temporal order (cause before effect)
        if self._config.causal_time_order_required:
            if cause_node.created_at and effect_node.created_at:
                cause_time = cause_node.created_at
                effect_time = effect_node.created_at
                if cause_time.tzinfo is None:
                    cause_time = cause_time.replace(tzinfo=timezone.utc)
                if effect_time.tzinfo is None:
                    effect_time = effect_time.replace(tzinfo=timezone.utc)

                if cause_time > effect_time:
                    logger.debug(
                        "Causal edge rejected: cause must precede effect in time"
                    )
                    return None

        edge = NeuralEdge(
            edge_id=generate_edge_id(),
            source_id=cause_node.node_id,
            target_id=effect_node.node_id,
            edge_type=EdgeType.CAUSAL,
            base_weight=1.0,
            confidence=confidence,
            gate_threshold=0.4,  # Higher threshold for causal
            metadata={"causal_type": causal_type},
        )

        await self._storage.save_edge(edge)

        logger.debug(
            f"Created causal edge {cause_node.node_id[:8]} --{causal_type}--> "
            f"{effect_node.node_id[:8]} (confidence={confidence:.2f})"
        )

        return edge

    # =========================================================================
    # TEMPORAL CHAIN TRAVERSAL
    # =========================================================================

    async def follow_temporal_chain(
        self,
        start_node: NeuralNode,
        direction: str = "backward",
        max_hops: int = 5,
        min_weight: float = 0.1
    ) -> list[tuple[NeuralNode, NeuralEdge]]:
        """Follow temporal chain in given direction.

        Args:
            start_node: Starting node
            direction: "backward" (find predecessors) or "forward" (find successors)
            max_hops: Maximum chain length
            min_weight: Minimum edge weight to follow

        Returns:
            List of (node, connecting_edge) tuples in chain order
        """
        chain: list[tuple[NeuralNode, NeuralEdge]] = []
        current = start_node
        visited = {start_node.node_id}

        for _ in range(max_hops):
            # Get temporal edges based on direction
            if direction == "backward":
                # Find edges where current is the target (i.e., predecessors)
                edges = await self._storage.get_edges_to(
                    current.node_id, edge_types=[EdgeType.TEMPORAL]
                )
            else:
                # Find edges where current is the source (i.e., successors)
                edges = await self._storage.get_edges_from(
                    current.node_id, edge_types=[EdgeType.TEMPORAL]
                )

            if not edges:
                break

            # Filter by weight and sort
            valid_edges = [e for e in edges if abs(e.effective_weight) >= min_weight]
            if not valid_edges:
                break

            valid_edges.sort(key=lambda e: abs(e.effective_weight), reverse=True)
            best_edge = valid_edges[0]

            # Get the other node
            if direction == "backward":
                next_id = best_edge.source_id
            else:
                next_id = best_edge.target_id

            if next_id in visited:
                break

            next_node = await self._storage.get_node(next_id)
            if next_node is None:
                break

            chain.append((next_node, best_edge))
            visited.add(next_id)
            current = next_node

            # Activate the edge (LTP)
            best_edge.activate()
            await self._storage.save_edge(best_edge)

        return chain

    async def follow_causal_chain(
        self,
        start_node: NeuralNode,
        direction: str = "effects",
        max_hops: int = 3
    ) -> list[tuple[NeuralNode, NeuralEdge]]:
        """Follow causal chain to find causes or effects.

        Args:
            start_node: Starting node
            direction: "effects" (forward) or "causes" (backward)
            max_hops: Maximum chain length

        Returns:
            List of (node, connecting_edge) tuples
        """
        chain: list[tuple[NeuralNode, NeuralEdge]] = []
        current = start_node
        visited = {start_node.node_id}

        for _ in range(max_hops):
            if direction == "causes":
                # Find incoming causal edges
                edges = await self._storage.get_edges_to(
                    current.node_id, edge_types=[EdgeType.CAUSAL]
                )
            else:
                # Find outgoing causal edges
                edges = await self._storage.get_edges_from(
                    current.node_id, edge_types=[EdgeType.CAUSAL]
                )

            if not edges:
                break

            # Sort by confidence
            edges.sort(key=lambda e: e.confidence, reverse=True)
            best_edge = edges[0]

            # Get the other node
            if direction == "causes":
                next_id = best_edge.source_id
            else:
                next_id = best_edge.target_id

            if next_id in visited:
                break

            next_node = await self._storage.get_node(next_id)
            if next_node is None:
                break

            chain.append((next_node, best_edge))
            visited.add(next_id)
            current = next_node

        return chain

    # =========================================================================
    # TEMPORAL SUMMATION (Image 4: Neural Summation)
    # =========================================================================

    async def temporal_summation(
        self,
        node: NeuralNode,
        window_hours: float | None = None
    ) -> float:
        """Compute temporal summation of activation signals.

        Multiple weak inputs over time sum to strong signal,
        similar to dendritic temporal summation in biological neurons.

        OPTIMIZED: Caches datetime.now() to avoid repeated syscalls in hot loop.

        Args:
            node: Target node
            window_hours: Time window in hours (uses config default if None)

        Returns:
            Summed activation signal
        """
        window = window_hours or self._config.summation_window_hours

        # OPTIMIZATION: Cache datetime.now() outside the loop
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=window)

        # Get all incoming edges
        edges = await self._storage.get_edges_to(node.node_id)

        # Filter to recent activations
        recent_edges = []
        for edge in edges:
            last_activated = edge.last_activated
            if last_activated.tzinfo is None:
                last_activated = last_activated.replace(tzinfo=timezone.utc)
            if last_activated > window_start:
                recent_edges.append(edge)

        if not recent_edges:
            return 0.0

        # Temporal kernel: recent activations weight more
        # OPTIMIZED: Pass cached 'now' to avoid repeated datetime.now() calls
        def kernel(edge: NeuralEdge, now: datetime) -> float:
            last_activated = edge.last_activated
            if last_activated.tzinfo is None:
                last_activated = last_activated.replace(tzinfo=timezone.utc)
            hours_ago = (now - last_activated).total_seconds() / 3600
            return math.exp(-hours_ago / window)

        # Sum signals with temporal weighting
        summed_signal = sum(
            edge.effective_weight * kernel(edge, now)
            for edge in recent_edges
        )

        return summed_signal

    async def check_summation_threshold(
        self,
        node: NeuralNode,
        threshold: float | None = None
    ) -> bool:
        """Check if node's temporal summation exceeds threshold.

        Used to determine if a node should "fire" based on
        accumulated input over time.

        Args:
            node: Target node
            threshold: Activation threshold (uses config default if None)

        Returns:
            True if threshold exceeded
        """
        thresh = threshold or self._config.summation_threshold
        summation = await self.temporal_summation(node)
        return summation >= thresh

    # =========================================================================
    # LONG-TERM POTENTIATION (LTP)
    # =========================================================================

    async def record_co_activation(
        self,
        nodes: list[NeuralNode],
        context: str = "retrieval"
    ) -> int:
        """Record co-activation of nodes for LTP.

        When nodes are activated together (e.g., retrieved together),
        strengthen their mutual connections.

        "Neurons that fire together wire together" - Hebbian learning

        OPTIMIZED: Uses canonical ordering (min_id, max_id) to avoid checking
        both directions. Uses batch edge fetch and batch save for O(1) lock
        acquisitions instead of O(n²).

        Args:
            nodes: Nodes that were co-activated
            context: Context of co-activation

        Returns:
            Number of edges strengthened
        """
        if len(nodes) < 2:
            return 0

        node_ids = [n.node_id for n in nodes]

        # Build canonical pairs (min_id, max_id) to ensure single direction
        pairs: list[tuple[str, str]] = []
        for i, id1 in enumerate(node_ids):
            for id2 in node_ids[i + 1:]:
                # Canonical ordering: smaller ID is always source
                canonical = (min(id1, id2), max(id1, id2))
                pairs.append(canonical)

        if not pairs:
            return 0

        # Batch fetch existing edges
        existing_edges = await self._storage.get_edges_batch(pairs, EdgeType.CO_ACTIVATION)

        # Build list of edges to save
        edges_to_save: list[NeuralEdge] = []
        for (id1, id2), edge in zip(pairs, existing_edges):
            if edge is None:
                # Create new co-activation edge with stronger initial weight
                edge = NeuralEdge(
                    edge_id=generate_edge_id(),
                    source_id=id1,
                    target_id=id2,
                    edge_type=EdgeType.CO_ACTIVATION,
                    base_weight=0.3,  # Start stronger (was 0.1 - too weak)
                    metadata={"context": context},
                )

            # LTP: strengthen with activation
            edge.activate()
            edges_to_save.append(edge)

        # Batch save all edges
        await self._storage.save_edges_batch(edges_to_save)

        logger.debug(f"Recorded co-activation for {len(nodes)} nodes, strengthened {len(edges_to_save)} edges")
        return len(edges_to_save)

    async def apply_stdp(
        self,
        source_id: str,
        target_id: str,
        time_delta_ms: float
    ) -> bool:
        """Apply Spike-Timing Dependent Plasticity to temporal edges.

        STDP is a biological learning rule that strengthens or weakens
        synapses based on the relative timing of pre/post-synaptic spikes:
        - Pre fires before post (causal): strengthen (LTP)
        - Post fires before pre (anti-causal): weaken (LTD)

        Args:
            source_id: ID of presynaptic node
            target_id: ID of postsynaptic node
            time_delta_ms: Time difference (positive = pre before post)

        Returns:
            True if edge was modified, False otherwise
        """
        edge = await self._storage.get_edge_between(
            source_id, target_id, EdgeType.TEMPORAL
        )
        if not edge:
            return False

        # STDP time constant (biological: ~20ms)
        tau = 20.0

        if time_delta_ms > 0:  # Source fired first (causal)
            # LTP: strengthen the connection
            stdp_factor = math.exp(-time_delta_ms / tau)
            edge.ltp_boost = min(self._config.ltp_max_boost, edge.ltp_boost + 0.1 * stdp_factor)
        else:  # Target fired first (anti-causal)
            # LTD: weaken the connection (depression)
            stdp_factor = math.exp(time_delta_ms / tau)  # Note: time_delta_ms is negative
            edge.ltp_boost = max(1.0, edge.ltp_boost - 0.05 * stdp_factor)

        # Invalidate edge weight cache
        edge._cached_weight = None
        edge._cached_weight_key = None

        await self._storage.save_edge(edge)
        return True

    async def get_ltp_strength(
        self,
        node1: NeuralNode,
        node2: NeuralNode
    ) -> float:
        """Get LTP-based connection strength between two nodes.

        Uses canonical ordering (min_id → max_id) for single lookup.

        Args:
            node1: First node
            node2: Second node

        Returns:
            LTP strength (0 if no co-activation edge)
        """
        # Use canonical ordering for single lookup
        source_id = min(node1.node_id, node2.node_id)
        target_id = max(node1.node_id, node2.node_id)

        edge = await self._storage.get_edge_between(
            source_id, target_id, EdgeType.CO_ACTIVATION
        )

        if edge is None:
            return 0.0

        return edge.ltp_boost

    async def decay_ltp(
        self,
        session_key: str,
        decay_factor: float | None = None
    ) -> int:
        """Apply decay to all LTP-boosted edges.

        Args:
            session_key: Session identifier
            decay_factor: Decay factor (uses config rate if None)

        Returns:
            Number of edges decayed
        """
        decay = decay_factor or self._config.ltp_decay_rate

        edges = await self._storage.get_all_edges(session_key)
        decayed = 0

        for edge in edges:
            if edge.ltp_boost > 1.0:
                # Apply decay
                days = edge.days_since_activation
                edge.ltp_boost = 1.0 + (edge.ltp_boost - 1.0) * math.exp(-decay * days)

                # Remove boost if it's negligible
                if edge.ltp_boost < 1.01:
                    edge.ltp_boost = 1.0

                await self._storage.save_edge(edge)
                decayed += 1

        return decayed

    # =========================================================================
    # TEMPORAL ANALYSIS
    # =========================================================================

    async def get_temporal_context(
        self,
        node: NeuralNode,
        before_count: int = 3,
        after_count: int = 3
    ) -> dict[str, list[NeuralNode]]:
        """Get temporal context around a node.

        Args:
            node: Center node
            before_count: Number of preceding nodes
            after_count: Number of following nodes

        Returns:
            Dictionary with 'before' and 'after' node lists
        """
        # Get predecessors
        before_chain = await self.follow_temporal_chain(
            node, direction="backward", max_hops=before_count
        )
        before_nodes = [n for n, _ in before_chain]

        # Get successors
        after_chain = await self.follow_temporal_chain(
            node, direction="forward", max_hops=after_count
        )
        after_nodes = [n for n, _ in after_chain]

        return {
            "before": before_nodes,
            "after": after_nodes,
        }

    async def find_temporal_patterns(
        self,
        session_key: str,
        min_occurrences: int = 2
    ) -> list[dict[str, Any]]:
        """Find recurring temporal patterns.

        Identifies sequences of entities/topics that recur across time.

        Args:
            session_key: Session identifier
            min_occurrences: Minimum pattern occurrences

        Returns:
            List of pattern dictionaries
        """
        # Get all temporal edges
        edges = await self._storage.get_all_edges(session_key)
        temporal_edges = [e for e in edges if e.edge_type == EdgeType.TEMPORAL]

        if len(temporal_edges) < min_occurrences:
            return []

        # Build transition counts
        transitions: dict[tuple[str, str], int] = {}
        for edge in temporal_edges:
            source = await self._storage.get_node(edge.source_id)
            target = await self._storage.get_node(edge.target_id)

            if source and target:
                # Use dominant wave dimensions as pattern key
                source_key = tuple(sorted(source.get_dominant_wave_dimensions(0.3)))
                target_key = tuple(sorted(target.get_dominant_wave_dimensions(0.3)))

                key = (str(source_key), str(target_key))
                transitions[key] = transitions.get(key, 0) + 1

        # Filter to patterns
        patterns = []
        for (source_dims, target_dims), count in transitions.items():
            if count >= min_occurrences:
                patterns.append({
                    "source_dimensions": source_dims,
                    "target_dimensions": target_dims,
                    "occurrences": count,
                })

        patterns.sort(key=lambda p: p["occurrences"], reverse=True)
        return patterns

    # =========================================================================
    # REFRACTORY PERIOD MANAGEMENT
    # =========================================================================

    async def is_in_refractory(self, node: NeuralNode) -> bool:
        """Check if a node is in its refractory period.

        After a neuron fires, it enters a refractory period during which
        it cannot fire again. This prevents runaway excitation.

        Args:
            node: Node to check

        Returns:
            True if node is in refractory period (cannot be activated)
        """
        if node.refractory_until is None:
            return False

        now = datetime.now(timezone.utc)

        # Ensure timezone awareness
        refractory_until = node.refractory_until
        if refractory_until.tzinfo is None:
            refractory_until = refractory_until.replace(tzinfo=timezone.utc)

        return now < refractory_until

    async def get_refractory_remaining(self, node: NeuralNode) -> float:
        """Get remaining refractory time in milliseconds.

        Args:
            node: Node to check

        Returns:
            Remaining refractory time in ms (0 if not in refractory)
        """
        if node.refractory_until is None:
            return 0.0

        now = datetime.now(timezone.utc)

        # Ensure timezone awareness
        refractory_until = node.refractory_until
        if refractory_until.tzinfo is None:
            refractory_until = refractory_until.replace(tzinfo=timezone.utc)

        if now >= refractory_until:
            return 0.0

        remaining = (refractory_until - now).total_seconds() * 1000
        return max(0.0, remaining)

    async def set_refractory_period(
        self,
        node: NeuralNode,
        duration_ms: float | None = None
    ) -> None:
        """Set a node into refractory period.

        Called after a node "fires" to prevent immediate re-activation.

        Args:
            node: Node to put in refractory
            duration_ms: Refractory duration in ms (uses config default if None)
        """
        duration = duration_ms or self._config.refractory_period_ms

        now = datetime.now(timezone.utc)
        node.refractory_until = now + timedelta(milliseconds=duration)

        await self._storage.save_node(node)
