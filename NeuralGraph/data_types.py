"""Data types for Neural Memory Graph.

Implements neural-inspired graph structures for memory linking:
- NeuralNode: Hierarchical nodes with activation state
- NeuralEdge: Weighted, gated edges with LTP dynamics
- NodeLayer: 4-layer hierarchy (Message -> Episode -> Topic -> Persona)
- EdgeType: Different edge types with specialized gating

Design principles from neural network images:
- Image 1: Distributed weights, signed connections
- Image 2: Deep hierarchical structure
- Image 3: Non-linear gating
- Image 4: Temporal dynamics, LTP, refractory periods
- Image 5: Consolidation state tracking
- Image 6: Multi-stage execution support
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from typing import Any


class NodeLayer(IntEnum):
    """Hierarchical layer for nodes (Image 2: Deep Network).

    Layer 0: Raw messages (highest granularity)
    Layer 1: Episode segments (session groupings)
    Layer 2: Topic clusters (semantic groupings)
    Layer 3: Persona facets (stable user model)
    Layer 4: Temporal anchors (date/time nodes) - CRITICAL for "When did X happen?"
    """
    MESSAGE = 0
    EPISODE = 1
    TOPIC = 2
    PERSONA = 3
    TEMPORAL = 4  # New layer for temporal anchors


class EdgeType(str, Enum):
    """Types of edges with different gating functions (Image 3).

    Each edge type has specialized gating behavior:
    - TEMPORAL: Recency-biased, propagation delay
    - SEMANTIC: Hard similarity threshold
    - ENTITY: Co-occurrence + explicit relations
    - CAUSAL: Strict explicit causality
    - HIERARCHY: Layer transitions (up/down)
    - CO_ACTIVATION: LTP-based Hebbian learning
    """
    TEMPORAL = "temporal"
    SEMANTIC = "semantic"
    ENTITY = "entity"
    CAUSAL = "causal"
    HIERARCHY = "hierarchy"
    CO_ACTIVATION = "co_activation"


class ConsolidationState(str, Enum):
    """Node consolidation state (Image 5: Compression/Pruning)."""
    ACTIVE = "active"           # Normal state
    CONSOLIDATING = "consolidating"  # Being processed for promotion
    ARCHIVED = "archived"       # Promoted to higher layer
    EVICTED = "evicted"         # Marked for removal


@dataclass
class NeuralEdge:
    """Edge with neural-inspired dynamics.

    Implements:
    - Weighted, signed connections (Image 1)
    - Gating threshold (Image 3)
    - Temporal dynamics & LTP (Image 4)
    - Propagation delay modeling (Image 4)

    Attributes:
        edge_id: Unique identifier
        source_id: Source node ID
        target_id: Target node ID
        edge_type: Type determining gating function
        base_weight: Base connection weight [0, 1]
        sign: Excitatory (+1) or inhibitory (-1)
        confidence: Certainty in this edge [0, 1]
        created_at: When edge was created
        last_activated: Last activation timestamp (for LTP)
        activation_count: Times activated (for LTP)
        propagation_delay_ms: Signal propagation delay
        ltp_boost: Long-term potentiation multiplier
        ltp_decay_rate: Daily decay rate for LTP
        gate_threshold: Activation threshold for gating
    """
    edge_id: str
    source_id: str
    target_id: str
    edge_type: EdgeType

    # Base weight and sign (Image 1: signed weights)
    base_weight: float = 1.0
    sign: float = 1.0  # +1 excitatory, -1 inhibitory

    # Confidence and context
    confidence: float = 1.0
    context_embedding: list[float] | None = None

    # Temporal dynamics (Image 4: biological neuron)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    activation_count: int = 0
    propagation_delay_ms: float = 0.0

    # LTP parameters (Image 4: long-term potentiation)
    ltp_boost: float = 1.0
    ltp_decay_rate: float = 0.01  # Per day

    # Gating parameters (Image 3: non-linear activation)
    gate_threshold: float = 0.3

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Cache for effective_weight (invalidated on activation)
    _cached_weight: float | None = field(default=None, repr=False)
    _cached_weight_key: tuple | None = field(default=None, repr=False)

    def activate(self) -> None:
        """Record activation and update LTP (Hebbian learning).

        "Neurons that fire together wire together."
        Each activation strengthens the connection.
        """
        self.activation_count += 1
        self.last_activated = datetime.now(timezone.utc)
        # LTP boost increases logarithmically, capped at 3x
        self.ltp_boost = min(3.0, 1.0 + 0.15 * math.log(1 + self.activation_count))
        # Invalidate weight cache on activation
        self._cached_weight = None
        self._cached_weight_key = None

    @property
    def days_since_activation(self) -> float:
        """Days since last activation."""
        now = datetime.now(timezone.utc)
        if self.last_activated.tzinfo is None:
            last = self.last_activated.replace(tzinfo=timezone.utc)
        else:
            last = self.last_activated
        return (now - last).total_seconds() / 86400

    @property
    def days_since_creation(self) -> float:
        """Days since edge was created."""
        now = datetime.now(timezone.utc)
        if self.created_at.tzinfo is None:
            created = self.created_at.replace(tzinfo=timezone.utc)
        else:
            created = self.created_at
        return (now - created).total_seconds() / 86400

    @property
    def effective_weight(self) -> float:
        """Compute effective weight with LTP and decay (Image 4).

        OPTIMIZED: Caches result and only recomputes when activation state changes.
        Cache key is (last_activated, activation_count, ltp_boost).

        Combines:
        - Base weight and sign
        - Confidence
        - Recency decay (exponential)
        - LTP boost with its own decay

        Returns:
            Effective edge weight for signal propagation.
        """
        # Check cache - key is the activation state that affects weight
        cache_key = (self.last_activated, self.activation_count, self.ltp_boost)
        if self._cached_weight is not None and self._cached_weight_key == cache_key:
            return self._cached_weight

        # Recency decay: half-life of 30 days
        recency_factor = math.exp(-self.days_since_activation / 30.0)

        # LTP factor with decay
        ltp_decay = math.exp(-self.ltp_decay_rate * self.days_since_activation)
        current_ltp = 1.0 + (self.ltp_boost - 1.0) * ltp_decay

        # Cache and return
        self._cached_weight = self.sign * self.base_weight * self.confidence * recency_factor * current_ltp
        self._cached_weight_key = cache_key
        return self._cached_weight

    def gate(self, input_signal: float, steepness: float = 5.0) -> float:
        """Apply non-linear gating (Image 3: sigmoid activation).

        Args:
            input_signal: Input signal strength
            steepness: Sigmoid steepness (higher = sharper threshold)

        Returns:
            Gated output signal [0, 1]
        """
        raw_signal = input_signal * abs(self.effective_weight)
        # Sigmoid gating with threshold shift
        return 1.0 / (1.0 + math.exp(-steepness * (raw_signal - self.gate_threshold)))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "base_weight": self.base_weight,
            "sign": self.sign,
            "confidence": self.confidence,
            "context_embedding": self.context_embedding,
            "created_at": self.created_at.isoformat(),
            "last_activated": self.last_activated.isoformat(),
            "activation_count": self.activation_count,
            "propagation_delay_ms": self.propagation_delay_ms,
            "ltp_boost": self.ltp_boost,
            "ltp_decay_rate": self.ltp_decay_rate,
            "gate_threshold": self.gate_threshold,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NeuralEdge:
        """Create from dictionary."""
        return cls(
            edge_id=data["edge_id"],
            source_id=data["source_id"],
            target_id=data["target_id"],
            edge_type=EdgeType(data["edge_type"]),
            base_weight=data.get("base_weight", 1.0),
            sign=data.get("sign", 1.0),
            confidence=data.get("confidence", 1.0),
            context_embedding=data.get("context_embedding"),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else data.get("created_at", datetime.now(timezone.utc)),
            last_activated=datetime.fromisoformat(data["last_activated"]) if isinstance(data.get("last_activated"), str) else data.get("last_activated", datetime.now(timezone.utc)),
            activation_count=data.get("activation_count", 0),
            propagation_delay_ms=data.get("propagation_delay_ms", 0.0),
            ltp_boost=data.get("ltp_boost", 1.0),
            ltp_decay_rate=data.get("ltp_decay_rate", 0.01),
            gate_threshold=data.get("gate_threshold", 0.3),
            metadata=data.get("metadata", {}),
        )


@dataclass
class NeuralNode:
    """Node with layer information and activation state.

    Implements:
    - Hierarchical structure (Image 2: deep network)
    - Activation levels and refractory periods (Image 4)
    - Consolidation state tracking (Image 5)

    Attributes:
        node_id: Unique identifier
        layer: Hierarchical layer (MESSAGE, EPISODE, TOPIC, PERSONA)
        content: Text content
        embedding: Semantic embedding vector
        wave_amplitudes: 9-dimensional wave signal (from WaveMemory)
        activation_level: Current activation [0, 1]
        last_activated: Last activation timestamp
        refractory_until: Refractory period end
        heat_score: Consolidation heat (higher = more important)
        importance_score: Base importance [0, 1]
        consolidation_state: Current consolidation state
        parent_id: Parent node in hierarchy
        child_ids: Child nodes in hierarchy
        entity_ids: Linked entity IDs from knowledge graph
        session_key: Owner session
    """
    node_id: str
    layer: NodeLayer
    content: str

    # Embedding and wave signals
    embedding: list[float] | None = None
    wave_amplitudes: dict[str, float] = field(default_factory=dict)

    # Activation state (Image 4: neural dynamics)
    activation_level: float = 0.0
    last_activated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    refractory_until: datetime | None = None

    # Consolidation state (Image 5: compression/pruning)
    # Initial heat 0.3 gives breathing room above eviction threshold (0.25)
    # Math: 0.3 initial + (9 activations * 0.25 boost) = 2.55 (promotes at 2.5)
    heat_score: float = 0.3
    importance_score: float = 0.5
    consolidation_state: ConsolidationState = ConsolidationState.ACTIVE

    # Hierarchy links (Image 2: deep structure)
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)

    # Knowledge graph links
    entity_ids: list[str] = field(default_factory=list)

    # Ownership and timing
    session_key: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Source tracking
    source_memory_ids: list[str] = field(default_factory=list)

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # FAIRNESS metadata - allows balanced treatment across edge types
    # Tracks edge type distribution for this node
    edge_type_counts: dict[str, int] = field(default_factory=dict)
    # Tracks dominant wave dimension for query-aware prioritization
    dominant_dimension: str = ""
    # Fairness score - nodes with diverse edges are more valuable
    diversity_score: float = 0.5

    def update_edge_counts(self, edge_type: str) -> None:
        """Update edge type count for fairness tracking.

        Called when edges are created to track the distribution
        of edge types connected to this node.
        """
        if edge_type not in self.edge_type_counts:
            self.edge_type_counts[edge_type] = 0
        self.edge_type_counts[edge_type] += 1

        # Update diversity score (more edge types = higher diversity)
        if self.edge_type_counts:
            num_types = len(self.edge_type_counts)
            total_edges = sum(self.edge_type_counts.values())
            # Shannon diversity normalized: higher when edges are evenly distributed
            if total_edges > 0 and num_types > 1:
                import math
                entropy = 0.0
                for count in self.edge_type_counts.values():
                    if count > 0:
                        p = count / total_edges
                        entropy -= p * math.log(p)
                max_entropy = math.log(num_types)
                self.diversity_score = entropy / max_entropy if max_entropy > 0 else 0.5
            else:
                self.diversity_score = 0.5

    def update_dominant_dimension(self) -> None:
        """Update dominant wave dimension from wave_amplitudes.

        The dominant dimension determines what TYPE of queries
        this node is best suited to answer.
        """
        if self.wave_amplitudes:
            max_dim = max(self.wave_amplitudes.items(), key=lambda x: x[1])
            self.dominant_dimension = max_dim[0] if max_dim[1] > 0.2 else ""

    def get_fairness_weight(self, query_wave_amplitudes: dict[str, float]) -> float:
        """Get fairness weight based on query alignment.

        THE FAIRNESS PRINCIPLE:
        Each node should get a fair chance based on whether
        its dominant dimension matches what the query is asking for.

        Args:
            query_wave_amplitudes: The query's wave signature

        Returns:
            Fairness multiplier [0.5, 2.0]
        """
        if not query_wave_amplitudes or not self.dominant_dimension:
            return 1.0  # Neutral fairness

        # Get query's dominant dimension
        query_max = max(query_wave_amplitudes.items(), key=lambda x: x[1])
        query_dominant = query_max[0] if query_max[1] > 0.2 else ""

        if not query_dominant:
            return 1.0  # No clear query preference

        # FAIRNESS BOOST: If node's dominant dimension matches query's need
        if self.dominant_dimension == query_dominant:
            # Strong match - boost this node's priority
            return 1.5 + (self.diversity_score * 0.5)  # 1.5 to 2.0

        # FAIRNESS PENALTY: Misaligned dimensions get reduced priority
        # But not too harsh - allow serendipitous discovery
        return 0.7 + (self.diversity_score * 0.3)  # 0.7 to 1.0

    def is_in_refractory(self) -> bool:
        """Check if node is in refractory period (Image 4).

        During refractory period, activation is dampened
        to prevent retrieval loops.
        """
        if self.refractory_until is None:
            return False
        now = datetime.now(timezone.utc)
        if self.refractory_until.tzinfo is None:
            refractory = self.refractory_until.replace(tzinfo=timezone.utc)
        else:
            refractory = self.refractory_until
        return now < refractory

    @property
    def speaker_id(self) -> str:
        """Canonical accessor for speaker identity.

        SPEAKER BINDING CONTRACT:
        - Canonical key: metadata["speaker"]
        - Legacy fallback: metadata["producer_id"]
        - All readers MUST use this property instead of direct metadata access.
        - All writers SHOULD write to "speaker" key (and optionally "producer_id" for compat).

        Returns:
            Speaker identifier string (empty string if not set)
        """
        if not self.metadata:
            return ""
        # Canonical key first, then legacy fallback
        return self.metadata.get("speaker", "") or self.metadata.get("producer_id", "") or ""

    def activate(self, signal_strength: float, refractory_ms: float | None = None) -> float:
        """Activate node with adaptive refractory and LTP-based heat boost.

        Implements biological neural dynamics:
        - Layer-specific refractory periods (higher layers = slower response)
        - Frequency-dependent accommodation (recent activity = longer refractory)
        - Long-Term Potentiation (LTP) principles for heat boost

        Args:
            signal_strength: Incoming signal strength
            refractory_ms: Refractory period in ms (auto-computed if None)

        Returns:
            Actual activation level after gating
        """
        if self.is_in_refractory():
            # Reduced response during refractory
            signal_strength *= 0.3

        self.activation_level = min(1.0, self.activation_level + signal_strength)
        self.last_activated = datetime.now(timezone.utc)

        # Track activation count for LTP and accommodation
        if not hasattr(self, '_activation_count'):
            self._activation_count = 0
        self._activation_count += 1

        # ADAPTIVE REFRACTORY PERIOD (biological fidelity)
        if refractory_ms is None:
            # Layer-specific base refractory (higher layers = more stable = slower)
            layer_refractory = {
                NodeLayer.MESSAGE: 50,    # Fast for raw input
                NodeLayer.EPISODE: 100,   # Medium
                NodeLayer.TOPIC: 200,     # Slower for abstractions
                NodeLayer.PERSONA: 500,   # Very slow for stable concepts
            }
            refractory_ms = layer_refractory.get(self.layer, 100)

            # Frequency-dependent accommodation: longer refractory if recently active
            # This prevents rapid re-triggering (biological adaptation)
            if self._activation_count > 10:
                refractory_ms *= 1.5  # Accommodation factor

        self.refractory_until = datetime.now(timezone.utc) + timedelta(milliseconds=refractory_ms)

        # LTP-based heat boost (research: ~15-20% strengthening per activation)
        # Base boost + recency bonus for repeated access
        base_boost = 0.2  # Was 0.1 - increased based on LTP research
        recency_bonus = 0.05 * min(1.0, self._activation_count / 5.0)
        heat_boost = base_boost + recency_bonus

        # Apply heat boost with ceiling
        self.heat_score = min(10.0, self.heat_score + heat_boost)

        return self.activation_level

    @property
    def activation_count(self) -> int:
        """Get total activation count for this node."""
        return getattr(self, '_activation_count', 0)

    def decay_activation(self, decay_rate: float = 0.1) -> None:
        """Decay activation level over time."""
        self.activation_level = max(0.0, self.activation_level - decay_rate)

    @property
    def days_since_creation(self) -> float:
        """Days since node was created."""
        now = datetime.now(timezone.utc)
        if self.created_at.tzinfo is None:
            created = self.created_at.replace(tzinfo=timezone.utc)
        else:
            created = self.created_at
        return (now - created).total_seconds() / 86400

    @property
    def days_since_activation(self) -> float:
        """Days since node was last activated.

        CRITICAL: This is used for heat decay, not days_since_creation.
        Ebbinghaus forgetting curve applies to time since last retrieval,
        not time since initial encoding.
        """
        now = datetime.now(timezone.utc)
        if self.last_activated.tzinfo is None:
            last = self.last_activated.replace(tzinfo=timezone.utc)
        else:
            last = self.last_activated
        return (now - last).total_seconds() / 86400

    def get_dominant_wave_dimensions(self, threshold: float = 0.25) -> list[str]:
        """Get wave dimensions above threshold."""
        return [
            dim for dim, amp in self.wave_amplitudes.items()
            if amp >= threshold
        ]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "node_id": self.node_id,
            "layer": self.layer.value,
            "content": self.content,
            "embedding": self.embedding,
            "wave_amplitudes": self.wave_amplitudes,
            "activation_level": self.activation_level,
            "last_activated": self.last_activated.isoformat(),
            "refractory_until": self.refractory_until.isoformat() if self.refractory_until else None,
            "heat_score": self.heat_score,
            "importance_score": self.importance_score,
            "consolidation_state": self.consolidation_state.value,
            "parent_id": self.parent_id,
            "child_ids": self.child_ids,
            "entity_ids": self.entity_ids,
            "session_key": self.session_key,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source_memory_ids": self.source_memory_ids,
            "metadata": self.metadata,
            # FAIRNESS metadata
            "edge_type_counts": self.edge_type_counts,
            "dominant_dimension": self.dominant_dimension,
            "diversity_score": self.diversity_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NeuralNode:
        """Create from dictionary."""
        return cls(
            node_id=data["node_id"],
            layer=NodeLayer(data["layer"]),
            content=data["content"],
            embedding=data.get("embedding"),
            wave_amplitudes=data.get("wave_amplitudes", {}),
            activation_level=data.get("activation_level", 0.0),
            last_activated=datetime.fromisoformat(data["last_activated"]) if isinstance(data.get("last_activated"), str) else data.get("last_activated", datetime.now(timezone.utc)),
            refractory_until=datetime.fromisoformat(data["refractory_until"]) if data.get("refractory_until") else None,
            heat_score=data.get("heat_score", 0.5),
            importance_score=data.get("importance_score", 0.5),
            consolidation_state=ConsolidationState(data.get("consolidation_state", "active")),
            parent_id=data.get("parent_id"),
            child_ids=data.get("child_ids", []),
            entity_ids=data.get("entity_ids", []),
            session_key=data.get("session_key", ""),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else data.get("created_at", datetime.now(timezone.utc)),
            updated_at=datetime.fromisoformat(data["updated_at"]) if isinstance(data.get("updated_at"), str) else data.get("updated_at", datetime.now(timezone.utc)),
            source_memory_ids=data.get("source_memory_ids", []),
            metadata=data.get("metadata", {}),
            # FAIRNESS metadata
            edge_type_counts=data.get("edge_type_counts", {}),
            dominant_dimension=data.get("dominant_dimension", ""),
            diversity_score=data.get("diversity_score", 0.5),
        )

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"NeuralNode(id={self.node_id[:8]}..., "
            f"layer={self.layer.name}, "
            f"heat={self.heat_score:.2f}, "
            f"activation={self.activation_level:.2f})"
        )


@dataclass
class RetrievalStageConfig:
    """Configuration for a retrieval stage (Image 6: execution schedule).

    Defines parameters for one stage in the multi-stage retrieval pipeline.
    """
    name: str
    max_candidates: int
    edge_types: list[EdgeType]
    layer_filter: list[NodeLayer] | None
    max_hops: int
    score_threshold: float
    use_vector_search: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "max_candidates": self.max_candidates,
            "edge_types": [e.value for e in self.edge_types],
            "layer_filter": [l.value for l in self.layer_filter] if self.layer_filter else None,
            "max_hops": self.max_hops,
            "score_threshold": self.score_threshold,
            "use_vector_search": self.use_vector_search,
        }


@dataclass
class RetrievalResult:
    """Result from neural retrieval."""
    query_text: str
    nodes: list[tuple[NeuralNode, float]]  # (node, score) pairs
    stages_executed: list[str]
    total_candidates_seen: int
    co_activations_recorded: int
    total_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_text": self.query_text,
            "nodes": [(n.to_dict(), s) for n, s in self.nodes],
            "stages_executed": self.stages_executed,
            "total_candidates_seen": self.total_candidates_seen,
            "co_activations_recorded": self.co_activations_recorded,
            "total_time_ms": self.total_time_ms,
        }


@dataclass
class ConsolidationResult:
    """Result from consolidation cycle."""
    session_key: str
    edges_pruned: int
    nodes_merged: int
    hubs_created: int
    nodes_promoted: int
    nodes_evicted: int
    total_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "edges_pruned": self.edges_pruned,
            "nodes_merged": self.nodes_merged,
            "hubs_created": self.hubs_created,
            "nodes_promoted": self.nodes_promoted,
            "nodes_evicted": self.nodes_evicted,
            "total_time_ms": self.total_time_ms,
        }


# Utility functions

def generate_node_id() -> str:
    """Generate unique node ID."""
    return str(uuid.uuid4())


def generate_edge_id() -> str:
    """Generate unique edge ID."""
    return str(uuid.uuid4())


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def sigmoid(x: float, steepness: float = 1.0, threshold: float = 0.0) -> float:
    """Standard sigmoid with adjustable steepness and threshold."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - threshold)))


def relu(x: float, threshold: float = 0.0) -> float:
    """ReLU with adjustable threshold."""
    return max(0.0, x - threshold)
