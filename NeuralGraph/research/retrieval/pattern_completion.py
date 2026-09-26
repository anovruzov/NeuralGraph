"""CA3-Inspired Pattern Completion for Memory Retrieval.

Implements Hopfield-style attractor dynamics for pattern completion:
- Partial cue activates stored memory pattern
- Spreading activation through recurrent connections
- Convergence to stable attractor (complete memory)

Mathematical Foundation:
- Energy: E = -Σᵢⱼ wᵢⱼ Sᵢ Sⱼ
- Update: Sᵢ(t+1) = sgn[Σⱼ wᵢⱼ Sⱼ(t)]
- Capacity: P = c/a² (Willshaw) where c=connectivity, a=sparsity

Based on:
- Hopfield (1982): Neural networks and physical systems
- Rolls & Treves: CA3 autoassociative network models
- Lisman & Idiart (1995): Theta-gamma coding for 7±2 items

References:
- https://neuronaldynamics.epfl.ch/online/Ch17.S2.html
- https://learnmem.cshlp.org/content/14/11/795.full.html
- https://pmc.ncbi.nlm.nih.gov/articles/PMC3812781/
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


@dataclass
class PatternCompletionConfig:
    """Configuration for CA3-style pattern completion."""

    # Attractor dynamics
    max_iterations: int = 3  # Gamma cycles (biological: ~3 cycles for completion)
    convergence_threshold: float = 0.01  # Stop when activation change < threshold

    # Connectivity (CA3 has ~2% recurrent connectivity)
    connectivity_radius: float = 0.3  # Cosine similarity threshold for "connected" nodes

    # Activation
    activation_decay: float = 0.8  # Decay per iteration (prevents runaway activation)
    spreading_factor: float = 0.5  # How much activation spreads to neighbors

    # Sparsity (CA3 uses sparse representations)
    sparsity_k: int = 20  # Keep top-k most active nodes per iteration

    # Energy-based selection
    use_energy_function: bool = True  # Use Hopfield energy for final ranking


@dataclass
class ActivationState:
    """Tracks activation state during pattern completion."""

    # Node activations: node_id -> activation level [0, 1]
    activations: dict[str, float] = field(default_factory=dict)

    # Energy (Hopfield): lower = more stable/complete pattern
    energy: float = 0.0

    # Iteration count
    iteration: int = 0

    # Convergence flag
    converged: bool = False


class CA3PatternCompleter:
    """CA3-inspired pattern completion using attractor dynamics.

    The hippocampal CA3 region performs pattern completion through:
    1. Sparse distributed coding (few neurons active per memory)
    2. Recurrent collateral connections (2% connectivity)
    3. Attractor dynamics (partial cue -> complete pattern)

    This module implements these principles for memory retrieval:
    - Query activates initial "seed" nodes
    - Activation spreads through similarity-weighted connections
    - System converges to stable pattern (retrieved memory)
    """

    def __init__(
        self,
        storage: NeuralGraphStorage,
        config: PatternCompletionConfig | None = None
    ):
        """Initialize pattern completer.

        Args:
            storage: Neural graph storage backend
            config: Pattern completion configuration
        """
        self._storage = storage
        self._config = config or PatternCompletionConfig()

        # Cache for node embeddings (avoid repeated fetches)
        self._embedding_cache: dict[str, list[float]] = {}

        # Cache for edge connectivity (use SEMANTIC/ENTITY edges as synaptic connections)
        # This is the CRITICAL link between horizontal linking and pattern completion
        self._edge_cache: dict[str, list[tuple[str, float]]] = {}  # node_id -> [(target_id, weight)]

    async def complete_pattern(
        self,
        seed_nodes: list[tuple[NeuralNode, float]],
        session_key: str,
        all_nodes: list[NeuralNode] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Complete partial memory pattern using CA3 attractor dynamics.

        Given partial cue (seed nodes), spread activation through the
        memory network until convergence to a stable attractor state.

        Args:
            seed_nodes: Initial activated nodes with scores
            session_key: Session identifier
            all_nodes: Optional pre-fetched nodes (avoids DB call)

        Returns:
            Completed pattern: list of (node, activation) tuples
        """
        if not seed_nodes:
            return []

        # Get all nodes in session if not provided
        if all_nodes is None:
            all_nodes = await self._storage.get_nodes_by_session(session_key)

        if not all_nodes:
            return seed_nodes

        # Build embedding cache
        self._build_embedding_cache(all_nodes)

        # Build edge cache (CRITICAL for horizontal linking integration)
        # Uses SEMANTIC and ENTITY edges created during ingestion as synaptic connections
        await self._build_edge_cache(all_nodes)

        # Initialize activation state from seeds
        state = self._initialize_state(seed_nodes)

        # Iterative spreading (gamma cycles)
        for iteration in range(self._config.max_iterations):
            state.iteration = iteration

            # Spread activation through network using BOTH embedding similarity AND edge connections
            # This integrates horizontal linking (edges) with pattern completion (embeddings)
            new_activations = await self._spread_activation_hybrid(state, all_nodes)

            # Check convergence
            delta = self._compute_activation_delta(state.activations, new_activations)

            if delta < self._config.convergence_threshold:
                state.converged = True
                logger.debug(f"Pattern completion converged at iteration {iteration}")
                break

            # Update state
            state.activations = new_activations

            # Enforce sparsity (keep top-k)
            state.activations = self._enforce_sparsity(state.activations)

        # Compute final energy
        if self._config.use_energy_function:
            state.energy = self._compute_energy(state.activations, all_nodes)

        # Build result from final activations
        result = self._build_result(state, all_nodes)

        logger.info(
            f"Pattern completion: {len(seed_nodes)} seeds -> {len(result)} nodes "
            f"({state.iteration + 1} iterations, converged={state.converged})"
        )

        return result

    def _build_embedding_cache(self, nodes: list[NeuralNode]) -> None:
        """Cache node embeddings for fast access."""
        self._embedding_cache.clear()
        for node in nodes:
            if node.embedding:
                self._embedding_cache[node.node_id] = node.embedding

    async def _build_edge_cache(self, nodes: list[NeuralNode]) -> None:
        """Build edge cache from SEMANTIC and ENTITY edges.

        These edges represent the actual connections learned during ingestion
        (horizontal linking). Using them for spreading activation is more
        accurate than recomputing embedding similarity.
        """
        from .data_types import EdgeType

        self._edge_cache.clear()

        # Get all node IDs for quick lookup
        node_ids = {n.node_id for n in nodes}

        # For each node, get its SEMANTIC and ENTITY edges
        for node in nodes:
            connections: list[tuple[str, float]] = []

            # Get SEMANTIC edges (similar content)
            semantic_edges = await self._storage.get_edges_from(
                node.node_id, edge_types=[EdgeType.SEMANTIC]
            )
            for edge in semantic_edges:
                if edge.target_id in node_ids:
                    connections.append((edge.target_id, edge.effective_weight))

            # Get ENTITY edges (shared entities - horizontal linking)
            entity_edges = await self._storage.get_edges_from(
                node.node_id, edge_types=[EdgeType.ENTITY]
            )
            for edge in entity_edges:
                if edge.target_id in node_ids:
                    # Entity edges are important - boost their weight
                    connections.append((edge.target_id, edge.effective_weight * 1.2))

            # Also check incoming edges (edges are bidirectional conceptually)
            semantic_in = await self._storage.get_edges_to(
                node.node_id, edge_types=[EdgeType.SEMANTIC]
            )
            for edge in semantic_in:
                if edge.source_id in node_ids and edge.source_id not in [c[0] for c in connections]:
                    connections.append((edge.source_id, edge.effective_weight))

            entity_in = await self._storage.get_edges_to(
                node.node_id, edge_types=[EdgeType.ENTITY]
            )
            for edge in entity_in:
                if edge.source_id in node_ids and edge.source_id not in [c[0] for c in connections]:
                    connections.append((edge.source_id, edge.effective_weight * 1.2))

            self._edge_cache[node.node_id] = connections

    async def _spread_activation_hybrid(
        self,
        state: ActivationState,
        all_nodes: list[NeuralNode]
    ) -> dict[str, float]:
        """Spread activation using BOTH edge connections AND embedding similarity.

        This hybrid approach:
        1. Uses actual edges (horizontal linking) as primary connections
        2. Falls back to embedding similarity for unconnected nodes
        3. Combines both signals for connected nodes

        This is the critical integration point between horizontal linking and
        pattern completion.
        """
        new_activations: dict[str, float] = {}

        # Get currently active nodes
        active_nodes = [
            (node_id, activation)
            for node_id, activation in state.activations.items()
            if activation > 0.1
        ]

        if not active_nodes:
            return state.activations.copy()

        # For each node in the network, compute new activation
        for node in all_nodes:
            node_id = node.node_id
            node_emb = self._embedding_cache.get(node_id)

            # Base activation (with decay)
            old_activation = state.activations.get(node_id, 0.0)
            new_act = self._config.activation_decay * old_activation

            # Part 1: Spreading via EDGES (horizontal linking)
            edge_spreading = 0.0
            node_connections = self._edge_cache.get(node_id, [])
            connection_targets = {c[0] for c in node_connections}

            for active_id, active_level in active_nodes:
                if active_id == node_id:
                    continue

                # Check if there's an edge connection
                for target_id, weight in node_connections:
                    if target_id == active_id:
                        edge_spreading += weight * active_level
                        break

            # Part 2: Spreading via EMBEDDING similarity (fallback for unconnected)
            embedding_spreading = 0.0
            if node_emb:
                for active_id, active_level in active_nodes:
                    if active_id == node_id:
                        continue

                    # Skip if already connected via edge (don't double-count)
                    if active_id in connection_targets:
                        continue

                    active_emb = self._embedding_cache.get(active_id)
                    if not active_emb:
                        continue

                    weight = self._compute_connection_weight(node_emb, active_emb)
                    if weight > self._config.connectivity_radius:
                        embedding_spreading += weight * active_level

            # Combine: Edge connections are weighted higher (they're learned associations)
            total_spreading = 0.7 * edge_spreading + 0.3 * embedding_spreading
            new_act += self._config.spreading_factor * total_spreading

            # Clamp to [0, 1]
            new_activations[node_id] = min(1.0, max(0.0, new_act))

        return new_activations

    def _initialize_state(
        self,
        seed_nodes: list[tuple[NeuralNode, float]]
    ) -> ActivationState:
        """Initialize activation state from seed nodes."""
        state = ActivationState()

        # Set initial activations from seeds
        for node, score in seed_nodes:
            # Normalize score to [0, 1] activation
            activation = min(1.0, max(0.0, score))
            state.activations[node.node_id] = activation

        return state

    def _spread_activation(
        self,
        state: ActivationState,
        all_nodes: list[NeuralNode]
    ) -> dict[str, float]:
        """Spread activation through recurrent connections.

        Implements: new_activation[i] = decay * old[i] + spread * Σⱼ w_ij * old[j]

        The weight w_ij is based on cosine similarity of embeddings,
        simulating recurrent collateral strength in CA3.
        """
        new_activations: dict[str, float] = {}

        # Get currently active nodes
        active_nodes = [
            (node_id, activation)
            for node_id, activation in state.activations.items()
            if activation > 0.1  # Only spread from significantly active nodes
        ]

        if not active_nodes:
            return state.activations.copy()

        # For each node in the network, compute new activation
        for node in all_nodes:
            node_id = node.node_id
            node_emb = self._embedding_cache.get(node_id)

            if not node_emb:
                continue

            # Base activation (with decay)
            old_activation = state.activations.get(node_id, 0.0)
            new_act = self._config.activation_decay * old_activation

            # Spreading activation from active neighbors
            spreading_sum = 0.0
            for active_id, active_level in active_nodes:
                if active_id == node_id:
                    continue

                # Compute connection weight (similarity)
                active_emb = self._embedding_cache.get(active_id)
                if not active_emb:
                    continue

                weight = self._compute_connection_weight(node_emb, active_emb)

                # Only spread through "connected" nodes (within radius)
                if weight > self._config.connectivity_radius:
                    spreading_sum += weight * active_level

            # Add spreading contribution
            new_act += self._config.spreading_factor * spreading_sum

            # Clamp to [0, 1]
            new_activations[node_id] = min(1.0, max(0.0, new_act))

        return new_activations

    def _compute_connection_weight(
        self,
        emb1: list[float],
        emb2: list[float]
    ) -> float:
        """Compute connection weight between nodes (cosine similarity).

        In CA3, recurrent collateral strength is learned through Hebbian
        plasticity. We approximate this with embedding similarity.
        """
        # Fast cosine similarity using numpy
        arr1 = np.array(emb1)
        arr2 = np.array(emb2)

        norm1 = np.linalg.norm(arr1)
        norm2 = np.linalg.norm(arr2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        similarity = np.dot(arr1, arr2) / (norm1 * norm2)

        # Return positive similarity only
        return max(0.0, float(similarity))

    def _compute_activation_delta(
        self,
        old: dict[str, float],
        new: dict[str, float]
    ) -> float:
        """Compute change in activation state."""
        all_ids = set(old.keys()) | set(new.keys())

        if not all_ids:
            return 0.0

        total_delta = sum(
            abs(new.get(nid, 0) - old.get(nid, 0))
            for nid in all_ids
        )

        return total_delta / len(all_ids)

    def _enforce_sparsity(
        self,
        activations: dict[str, float]
    ) -> dict[str, float]:
        """Keep only top-k most active nodes (sparse coding).

        CA3 uses sparse distributed representations where only a small
        fraction of neurons are active for any given memory.
        """
        if len(activations) <= self._config.sparsity_k:
            return activations

        # Sort by activation and keep top-k
        sorted_acts = sorted(
            activations.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return dict(sorted_acts[:self._config.sparsity_k])

    def _compute_energy(
        self,
        activations: dict[str, float],
        all_nodes: list[NeuralNode]
    ) -> float:
        """Compute Hopfield energy: E = -Σᵢⱼ wᵢⱼ Sᵢ Sⱼ

        Lower energy = more stable/coherent pattern.
        """
        energy = 0.0

        active_ids = list(activations.keys())

        for i, id1 in enumerate(active_ids):
            emb1 = self._embedding_cache.get(id1)
            if not emb1:
                continue

            s1 = activations[id1]

            for id2 in active_ids[i + 1:]:
                emb2 = self._embedding_cache.get(id2)
                if not emb2:
                    continue

                s2 = activations[id2]
                w = self._compute_connection_weight(emb1, emb2)

                # E = -Σ wᵢⱼ Sᵢ Sⱼ
                energy -= w * s1 * s2

        return energy

    def _build_result(
        self,
        state: ActivationState,
        all_nodes: list[NeuralNode]
    ) -> list[tuple[NeuralNode, float]]:
        """Build final result from activation state."""
        # Create node lookup
        node_lookup = {n.node_id: n for n in all_nodes}

        # Build result with activations as scores
        result = []
        for node_id, activation in state.activations.items():
            if activation > 0.1:  # Minimum activation threshold
                node = node_lookup.get(node_id)
                if node:
                    result.append((node, activation))

        # Sort by activation (descending)
        result.sort(key=lambda x: x[1], reverse=True)

        return result


class ThetaGammaOrganizer:
    """Theta-Gamma phase coding for memory slot organization.

    The theta-gamma neural code (Lisman & Idiart, 1995):
    - Theta rhythm (~4-8 Hz): defines memory "windows"
    - Gamma rhythm (~30-80 Hz): defines slots within theta
    - 7±2 gamma cycles per theta = 7±2 memory slots

    This organizes retrieved memories into coherent "episodes"
    with temporal/sequential structure.
    """

    def __init__(self, gamma_slots: int = 7):
        """Initialize organizer.

        Args:
            gamma_slots: Number of gamma slots per theta cycle (default: 7)
        """
        self.gamma_slots = gamma_slots

    def organize_by_phase(
        self,
        nodes: list[tuple[NeuralNode, float]],
        query_wave_amplitudes: dict[str, float] | None = None
    ) -> list[list[tuple[NeuralNode, float]]]:
        """Organize nodes into theta-gamma phase slots.

        Groups related memories into slots within a theta cycle,
        preserving sequential/temporal relationships.

        Args:
            nodes: Retrieved nodes with scores
            query_wave_amplitudes: Query wave pattern for organization

        Returns:
            List of gamma slots, each containing nodes assigned to that phase
        """
        if not nodes:
            return [[] for _ in range(self.gamma_slots)]

        # Initialize slots
        slots: list[list[tuple[NeuralNode, float]]] = [
            [] for _ in range(self.gamma_slots)
        ]

        # Assign nodes to slots based on their properties
        for node, score in nodes:
            slot_idx = self._compute_slot_assignment(node, query_wave_amplitudes)
            slots[slot_idx].append((node, score))

        return slots

    def _compute_slot_assignment(
        self,
        node: NeuralNode,
        query_waves: dict[str, float] | None
    ) -> int:
        """Assign node to gamma slot based on wave properties.

        Uses wave amplitudes to determine which "phase" of the
        theta cycle this memory belongs to.
        """
        if not node.wave_amplitudes:
            # Default to slot 0 if no wave info
            return 0

        # Map wave dimensions to slots
        # Slot assignment based on dominant dimension
        dimension_to_slot = {
            "temporal": 0,
            "entity": 1,
            "action": 2,
            "relational": 3,
            "state": 4,
            "spatial": 5,
            "emotional": 6,
        }

        # Find dominant dimension
        max_amp = 0.0
        dominant_dim = "temporal"

        for dim, amp in node.wave_amplitudes.items():
            if amp > max_amp:
                max_amp = amp
                dominant_dim = dim

        # Get slot (mod gamma_slots to handle overflow)
        base_slot = dimension_to_slot.get(dominant_dim, 0)
        return base_slot % self.gamma_slots

    def flatten_organized(
        self,
        slots: list[list[tuple[NeuralNode, float]]]
    ) -> list[tuple[NeuralNode, float]]:
        """Flatten organized slots back into ordered list.

        Preserves theta-gamma ordering: items from each slot
        appear in sequence.
        """
        result = []

        # Interleave from slots (round-robin within theta cycle)
        max_depth = max(len(slot) for slot in slots) if slots else 0

        for depth in range(max_depth):
            for slot in slots:
                if depth < len(slot):
                    result.append(slot[depth])

        return result
