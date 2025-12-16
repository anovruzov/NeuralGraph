"""Edge gating functions for Neural Memory Graph.

Implements non-linear activation functions (Image 3: Sigmoid Gating).

Each edge type has a specialized gating function that determines
whether and how strongly the edge should propagate signals.

Design principles:
- Image 3: Sigmoid/ReLU non-linear activation between layers
- Image 4: Temporal dynamics affect gating (recency, LTP)
- Different thresholds for different relation types
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from .data_types import NeuralEdge

from .data_types import EdgeType


def sigmoid(x: float, steepness: float = 5.0, threshold: float = 0.0) -> float:
    """Standard sigmoid with adjustable steepness and threshold.

    Args:
        x: Input value
        steepness: Controls how sharp the transition is (higher = sharper)
        threshold: Shifts the threshold point

    Returns:
        Output in range [0, 1]
    """
    return 1.0 / (1.0 + math.exp(-steepness * (x - threshold)))


def relu(x: float, threshold: float = 0.0) -> float:
    """ReLU with adjustable threshold.

    Args:
        x: Input value
        threshold: Minimum value to pass through

    Returns:
        max(0, x - threshold)
    """
    return max(0.0, x - threshold)


def leaky_relu(x: float, threshold: float = 0.0, leak: float = 0.1) -> float:
    """Leaky ReLU: allows small negative gradients.

    Args:
        x: Input value
        threshold: Threshold for full activation
        leak: Slope for values below threshold

    Returns:
        Full value if above threshold, leak * (x - threshold) otherwise
    """
    if x >= threshold:
        return x - threshold
    return leak * (x - threshold)


def soft_threshold(x: float, threshold: float = 0.3, softness: float = 0.1) -> float:
    """Soft threshold: smooth transition around threshold.

    Args:
        x: Input value
        threshold: Center of transition
        softness: Width of transition region

    Returns:
        Smoothly thresholded value
    """
    if softness <= 0:
        return x if x >= threshold else 0.0
    return x * sigmoid(x, steepness=1.0/softness, threshold=threshold)


class GateFunction(Protocol):
    """Protocol for gate functions."""

    def __call__(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Compute gate value for an edge.

        Args:
            edge: The edge to gate
            context: Additional context (query embedding, etc.)

        Returns:
            Gate value in [0, 1]
        """
        ...


@dataclass
class GateConfig:
    """Configuration for a gate function."""
    threshold: float = 0.3
    steepness: float = 5.0
    recency_half_life_days: float = 7.0
    ltp_weight: float = 0.3
    confidence_weight: float = 0.3


class EdgeGateRegistry:
    """Registry of gating functions for different edge types (Image 3).

    Each edge type has a specialized gating function that determines:
    1. Whether the edge should pass signal (threshold)
    2. How much signal should pass (activation level)

    This implements the non-linear activation principle from feedforward
    networks: linear combination -> non-linear gating -> next layer.
    """

    def __init__(self, config: GateConfig | None = None):
        """Initialize registry with optional config.

        Args:
            config: Gate configuration. Uses defaults if None.
        """
        self._config = config or GateConfig()
        self._gates: dict[EdgeType, Callable[[NeuralEdge, dict[str, Any]], float]] = {
            EdgeType.TEMPORAL: self._temporal_gate,
            EdgeType.SEMANTIC: self._semantic_gate,
            EdgeType.ENTITY: self._entity_gate,
            EdgeType.CAUSAL: self._causal_gate,
            EdgeType.HIERARCHY: self._hierarchy_gate,
            EdgeType.CO_ACTIVATION: self._co_activation_gate,
        }

    def get_gate(self, edge_type: EdgeType) -> Callable[[NeuralEdge, dict[str, Any]], float]:
        """Get the gate function for an edge type.

        Args:
            edge_type: The type of edge

        Returns:
            Gate function for that edge type
        """
        return self._gates.get(edge_type, self._default_gate)

    def compute_gate(
        self,
        edge: NeuralEdge,
        context: dict[str, Any] | None = None
    ) -> float:
        """Compute gate value for an edge.

        Args:
            edge: The edge to gate
            context: Additional context

        Returns:
            Gate value in [0, 1]
        """
        gate_fn = self.get_gate(edge.edge_type)
        return gate_fn(edge, context or {})

    def _temporal_gate(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Temporal gate: recent edges more active.

        Includes:
        - Recency decay (exponential)
        - Propagation delay modeling (Image 4)
        - LTP boost

        Temporal edges should fire more readily when:
        - They were recently activated
        - They have been repeatedly co-activated (LTP)
        - They have low propagation delay

        Args:
            edge: Temporal edge
            context: May contain 'reference_time'

        Returns:
            Gate value favoring recent, strong temporal links
        """
        age_days = edge.days_since_activation
        recency_signal = math.exp(-age_days / self._config.recency_half_life_days)

        # Factor in propagation delay (Image 4: conduction delay)
        # Higher delay = slower signal = lower gate value
        delay_factor = 1.0 - (edge.propagation_delay_ms / 1000.0) * 0.1
        delay_factor = max(0.5, delay_factor)

        # Combine with effective weight (includes LTP)
        raw = edge.effective_weight * recency_signal * delay_factor

        return sigmoid(raw, steepness=self._config.steepness, threshold=0.3)

    def _semantic_gate(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Semantic gate: hard threshold on similarity with recency/LTP awareness.

        Only pass if genuinely semantically related.
        Uses ReLU-style hard cutoff to prevent noise propagation.

        FIXED: Now uses effective_weight instead of base_weight, so semantic
        edges decay with recency and strengthen with LTP like other edge types.

        Args:
            edge: Semantic edge
            context: May contain 'similarity_threshold'

        Returns:
            Gate value with hard cutoff at 0.4 similarity
        """
        # FIXED: Use effective_weight to include recency decay and LTP boost
        # Previously used base_weight which ignored temporal dynamics
        similarity = abs(edge.effective_weight)
        threshold = context.get("similarity_threshold", 0.4)

        # Hard threshold - below threshold returns 0
        return relu(similarity, threshold=threshold)

    def _entity_gate(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Entity gate: SMOOTH CONTINUOUS gating like temporal edges.

        TESLA FIX: Entity gates now use SMOOTH exponential decay instead of
        sharp sigmoids. This allows entity edges to compete fairly with
        temporal edges during electron propagation.

        Entity links should fire when:
        - Entities appear together often (co-occurrence)
        - There's an explicit relation with high confidence
        - The edge has been used in retrievals (LTP)
        - The message distance is within context window (NEW!)

        Args:
            edge: Entity edge
            context: May contain 'message_distance', 'entity_recency'

        Returns:
            Gate value based on entity relationship strength
        """
        # SMOOTH CO-OCCURRENCE: Use continuous signal, not discrete steps
        # Saturates smoothly rather than jumping at thresholds
        co_occurrence_signal = 1.0 - math.exp(-edge.activation_count / 3.0)

        # LTP signal (already continuous)
        ltp_signal = (edge.ltp_boost - 1.0) / 2.0  # Normalize to ~[0, 1]

        # Entity recency - decay based on how recently the entity was mentioned
        entity_recency = context.get("entity_recency", 1.0)

        # Message distance decay (from edge metadata if available)
        message_distance = edge.metadata.get("message_distance", 0) if edge.metadata else 0
        distance_decay = math.exp(-message_distance / 15.0)  # 15-message context window

        # Combine signals with SMOOTH weighting
        raw = (
            0.35 * edge.effective_weight * distance_decay +  # Distance-weighted base
            0.25 * co_occurrence_signal +                     # Co-occurrence
            0.20 * edge.confidence * ltp_signal +             # LTP learning
            0.20 * entity_recency                             # Temporal recency
        )

        # SMOOTH sigmoid with gentler steepness (2.0 instead of 5.0)
        # This allows partial activation instead of all-or-nothing
        return sigmoid(raw, steepness=2.0, threshold=0.15)

    def _causal_gate(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Causal gate: strict, only pass explicit causal links.

        Causal links require high confidence and should only fire
        when there's strong evidence of causation.

        Uses steeper sigmoid for sharper threshold.

        Args:
            edge: Causal edge
            context: May contain 'strict_mode'

        Returns:
            Gate value with strict threshold for causal relations
        """
        # Require high confidence for causal links
        if edge.confidence < 0.6:
            return 0.0

        # Steeper sigmoid for causal - we want clear yes/no
        return sigmoid(
            edge.effective_weight * edge.confidence,
            steepness=10.0,
            threshold=0.4
        )

    def _hierarchy_gate(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Hierarchy gate: always allow up/down hierarchy traversal.

        Hierarchy edges (parent-child relationships) should generally
        pass signal, but weighted by consolidation state.

        Moving up the hierarchy (to summaries) and down (to details)
        should both be permitted.

        Args:
            edge: Hierarchy edge
            context: May contain 'consolidation_factor', 'direction'

        Returns:
            Gate value for hierarchy traversal
        """
        consolidation_factor = context.get("consolidation_factor", 1.0)
        direction = context.get("direction", "both")

        # Base hierarchy traversal is always allowed
        base_gate = edge.effective_weight * consolidation_factor

        # Optional direction bias
        if direction == "up":
            # Favor traversal toward higher layers (summaries)
            base_gate *= 1.2
        elif direction == "down":
            # Favor traversal toward lower layers (details)
            base_gate *= 1.1

        return min(1.0, base_gate)

    def _co_activation_gate(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Co-activation gate: LTP-based Hebbian link.

        "Neurons that fire together wire together."

        Co-activation edges are created when nodes are retrieved
        together. They should only fire if:
        - They've been activated multiple times (minimum threshold)
        - They have significant LTP boost

        FIXED: Now uses normalized LTP signal consistent with entity_gate.
        Previously used raw ltp_boost - 1.0, now normalizes by /2.0 like entity_gate.

        Args:
            edge: Co-activation edge
            context: May contain 'min_activations'

        Returns:
            Gate value based on Hebbian learning strength
        """
        min_activations = context.get("min_activations", 2)

        # Only active if activated multiple times together
        if edge.activation_count < min_activations:
            return 0.0

        # LTP signal is the primary driver
        # FIXED: Normalize by /2.0 to match entity_gate (ltp_boost ranges ~1.0 to 3.0)
        # This gives ltp_signal in range ~[0, 1] instead of raw ~[0, 2]
        ltp_signal = (edge.ltp_boost - 1.0) / 2.0

        # Also factor in effective_weight for consistency with other gates
        combined_signal = 0.7 * ltp_signal + 0.3 * edge.effective_weight

        return sigmoid(combined_signal, steepness=self._config.steepness, threshold=0.3)

    def _default_gate(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Default gate with proper inhibitory handling.

        For inhibitory edges (sign < 0), returns suppression factor.
        Biological principle: inhibitory neurons REDUCE downstream activation.

        Args:
            edge: Any edge
            context: Ignored

        Returns:
            For excitatory: effective weight (positive contribution)
            For inhibitory: suppression factor (0 = full suppression, 1 = no effect)
        """
        if edge.sign < 0:
            # Inhibitory edge: suppress based on weight magnitude
            # Higher weight = more suppression (lower return value)
            # Example: weight=-0.8 → suppression=0.2 (strong inhibition)
            return max(0.0, 1.0 - abs(edge.effective_weight))
        else:
            # Excitatory edge: return weight as contribution
            return abs(edge.effective_weight)


def inhibitory_gate(edge: NeuralEdge, context: dict[str, Any]) -> float:
    """Standalone inhibitory gating function.

    Use this to apply inhibitory effects based on edge sign.
    Biological principle: GABAergic neurons suppress downstream activity.

    Args:
        edge: The edge (checks sign attribute)
        context: May contain 'inhibition_strength'

    Returns:
        1.0 for excitatory edges (no suppression)
        Suppression factor for inhibitory edges (0 = full, 1 = none)
    """
    if edge.sign >= 0:
        return 1.0  # Excitatory: no suppression

    # Inhibitory strength can be adjusted by context
    strength = context.get("inhibition_strength", 1.0)

    # Compute suppression factor
    # Higher weight magnitude = more suppression
    suppression = abs(edge.effective_weight) * strength
    return max(0.0, 1.0 - suppression)


class CompositeGate:
    """Combine multiple gate functions.

    Allows building complex gates from simple components.
    """

    def __init__(self, combination: str = "multiply"):
        """Initialize composite gate.

        Args:
            combination: How to combine gates ("multiply", "min", "max", "mean")
        """
        self._gates: list[Callable[[NeuralEdge, dict[str, Any]], float]] = []
        self._combination = combination

    def add_gate(self, gate_fn: Callable[[NeuralEdge, dict[str, Any]], float]) -> CompositeGate:
        """Add a gate function.

        Args:
            gate_fn: Gate function to add

        Returns:
            Self for chaining
        """
        self._gates.append(gate_fn)
        return self

    def __call__(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Compute composite gate value.

        Args:
            edge: The edge to gate
            context: Additional context

        Returns:
            Combined gate value
        """
        if not self._gates:
            return 1.0

        values = [g(edge, context) for g in self._gates]

        if self._combination == "multiply":
            result = 1.0
            for v in values:
                result *= v
            return result
        elif self._combination == "min":
            return min(values)
        elif self._combination == "max":
            return max(values)
        elif self._combination == "mean":
            return sum(values) / len(values)
        else:
            return values[0]


class AdaptiveGate:
    """Gate that adapts threshold based on context.

    Useful for dynamic adjustment of gating based on:
    - Query complexity
    - Number of candidates
    - Time constraints
    """

    def __init__(
        self,
        base_gate: Callable[[NeuralEdge, dict[str, Any]], float],
        min_threshold: float = 0.1,
        max_threshold: float = 0.7
    ):
        """Initialize adaptive gate.

        Args:
            base_gate: Underlying gate function
            min_threshold: Minimum threshold (permissive)
            max_threshold: Maximum threshold (strict)
        """
        self._base_gate = base_gate
        self._min_threshold = min_threshold
        self._max_threshold = max_threshold
        self._current_threshold = (min_threshold + max_threshold) / 2

    def adapt_threshold(self, candidate_count: int, target_count: int = 50) -> None:
        """Adapt threshold based on candidate count.

        If too many candidates, increase threshold.
        If too few, decrease threshold.

        Args:
            candidate_count: Current number of candidates
            target_count: Desired number of candidates
        """
        if candidate_count > target_count * 2:
            # Too many - increase threshold
            self._current_threshold = min(
                self._max_threshold,
                self._current_threshold + 0.05
            )
        elif candidate_count < target_count / 2:
            # Too few - decrease threshold
            self._current_threshold = max(
                self._min_threshold,
                self._current_threshold - 0.05
            )

    def __call__(self, edge: NeuralEdge, context: dict[str, Any]) -> float:
        """Compute adaptive gate value.

        Args:
            edge: The edge to gate
            context: Additional context

        Returns:
            Gate value with adaptive threshold
        """
        base_value = self._base_gate(edge, context)
        return base_value if base_value >= self._current_threshold else 0.0


# Pre-configured gate registries for different use cases

def create_strict_registry() -> EdgeGateRegistry:
    """Create registry with strict thresholds.

    Use for high-precision retrieval where false positives
    are more costly than false negatives.
    """
    return EdgeGateRegistry(GateConfig(
        threshold=0.5,
        steepness=8.0,
        recency_half_life_days=3.0,
        ltp_weight=0.2,
        confidence_weight=0.4,
    ))


def create_permissive_registry() -> EdgeGateRegistry:
    """Create registry with permissive thresholds.

    Use for high-recall retrieval where false negatives
    are more costly than false positives.
    """
    return EdgeGateRegistry(GateConfig(
        threshold=0.15,
        steepness=3.0,
        recency_half_life_days=14.0,
        ltp_weight=0.4,
        confidence_weight=0.2,
    ))


def create_balanced_registry() -> EdgeGateRegistry:
    """Create registry with balanced thresholds.

    Default configuration for general use.
    """
    return EdgeGateRegistry(GateConfig())
