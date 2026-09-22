"""Wave interference-based memory scoring.

This module implements the core principle from biological neural networks:
- Memory activation = |query_wave + memory_wave|²
- When waves are IN PHASE: constructive → HIGH score
- When waves are OUT OF PHASE: destructive → LOW score

The interference pattern follows the electron-perceptron model:
- Positive weights (blue) = constructive interference = REINFORCE
- Negative weights (red) = destructive interference = SUPPRESS
- HIGH activation = waves in phase = MATCH
- NO activation = waves out of phase = NO MATCH
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data_types import NeuralNode


class InterferenceType(Enum):
    """Type of wave interference."""
    CONSTRUCTIVE = "constructive"  # Waves reinforce (in phase)
    PARTIAL = "partial"            # Partial interference
    DESTRUCTIVE = "destructive"    # Waves cancel (out of phase)


@dataclass
class InterferenceResult:
    """Result of wave interference computation."""
    activation: float
    interference_type: InterferenceType
    phase_diff: float
    amplitude_product: float


# Theta rhythm frequency (~6Hz) for phase computation
THETA_FREQUENCY_HZ = 6.0
THETA_PERIOD_MS = 1000.0 / THETA_FREQUENCY_HZ  # ~167ms per cycle


class InterferenceScorer:
    """Wave interference-based memory scoring.

    Memory activation = |query_wave + memory_wave|²

    When waves are IN PHASE: constructive → HIGH score
    When waves are OUT OF PHASE: destructive → LOW score

    This follows the biological principle where neurons with HIGH
    activation are where waves REINFORCE each other.
    """

    # Phase thresholds (in radians)
    CONSTRUCTIVE_THRESHOLD = math.pi / 4      # < 45° = constructive
    DESTRUCTIVE_THRESHOLD = 3 * math.pi / 4   # > 135° = destructive

    # Dimensional weights for amplitude product
    # Trunk dimensions (higher weight): entity, temporal, action
    # Support dimensions (lower weight): relational, state, spatial, etc.
    DIMENSION_WEIGHTS = {
        "entity": 1.0,      # Entity trunk
        "temporal": 0.9,    # Temporal trunk
        "action": 0.8,      # Action trunk
        "relational": 0.6,  # Support for entity
        "state": 0.5,       # Support for action
        "spatial": 0.4,     # Support for action
        "causal": 0.3,      # Causality chain
        "emotional": 0.2,   # Emotional coloring
        "quantitative": 0.2 # Numeric data
    }

    @classmethod
    def compute_interference(
        cls,
        query_amps: dict[str, float],
        memory_amps: dict[str, float],
        query_phase: float = 0.0,
        memory_phase: float = 0.0
    ) -> InterferenceResult:
        """Compute interference pattern between query and memory waves.

        Args:
            query_amps: Query wave amplitudes by dimension
            memory_amps: Memory wave amplitudes by dimension
            query_phase: Query wave phase (radians), default 0 (reference)
            memory_phase: Memory wave phase (radians)

        Returns:
            InterferenceResult with activation score and type
        """
        # Normalize phase difference to [0, 2π)
        phase_diff = abs(query_phase - memory_phase) % (2 * math.pi)

        # Compute weighted amplitude product across dimensions
        amp_product = 0.0
        total_weight = 0.0

        for dim, q_amp in query_amps.items():
            m_amp = memory_amps.get(dim, 0.0)
            weight = cls.DIMENSION_WEIGHTS.get(dim, 0.3)
            amp_product += q_amp * m_amp * weight
            total_weight += weight * max(q_amp, 0.01)  # Avoid div by zero

        # Normalize by total weight
        if total_weight > 0:
            amp_product /= total_weight

        # Determine interference type and compute activation
        if phase_diff < cls.CONSTRUCTIVE_THRESHOLD:
            # CONSTRUCTIVE: waves reinforce (< 45° phase diff)
            # Activation boosted by cos(phase_diff), max at 0°
            activation = amp_product * (1.0 + math.cos(phase_diff))
            itype = InterferenceType.CONSTRUCTIVE

        elif phase_diff > cls.DESTRUCTIVE_THRESHOLD:
            # DESTRUCTIVE: waves cancel (> 135° phase diff)
            # Activation reduced significantly
            activation = amp_product * (1.0 - math.cos(phase_diff)) * 0.3
            itype = InterferenceType.DESTRUCTIVE

        else:
            # PARTIAL: somewhere in between (45° - 135°)
            activation = amp_product * 0.5 * (1.0 + math.cos(phase_diff))
            itype = InterferenceType.PARTIAL

        return InterferenceResult(
            activation=activation,
            interference_type=itype,
            phase_diff=phase_diff,
            amplitude_product=amp_product
        )

    @staticmethod
    def compute_temporal_phase(
        timestamp: datetime,
        reference: datetime | None = None
    ) -> float:
        """Convert temporal position to wave phase.

        Like theta rhythm: time maps to phase angle.
        Theta frequency ~6Hz → 167ms per cycle

        Args:
            timestamp: The timestamp to convert
            reference: Reference timestamp (default: epoch)

        Returns:
            Phase angle in radians [0, 2π)
        """
        if reference is None:
            # Use a fixed reference for consistency (timezone-aware)
            reference = datetime(2020, 1, 1, tzinfo=timezone.utc)

        delta_ms = (timestamp - reference).total_seconds() * 1000
        phase = (delta_ms / THETA_PERIOD_MS) * 2 * math.pi
        return phase % (2 * math.pi)

    @classmethod
    def score_memory_match(
        cls,
        query_amps: dict[str, float],
        memory_node: "NeuralNode",
        reference_time: datetime | None = None
    ) -> tuple[float, InterferenceType]:
        """Score how well a memory matches a query using interference.

        Args:
            query_amps: Query wave amplitudes
            memory_node: Memory node to score
            reference_time: Reference time for phase computation

        Returns:
            (score, interference_type) tuple
        """
        memory_amps = memory_node.wave_amplitudes or {}

        # Compute phase from memory timestamp
        memory_phase = 0.0
        if hasattr(memory_node, 'created_at') and memory_node.created_at:
            memory_phase = cls.compute_temporal_phase(
                memory_node.created_at,
                reference_time
            )

        result = cls.compute_interference(
            query_amps=query_amps,
            memory_amps=memory_amps,
            query_phase=0.0,  # Query is reference
            memory_phase=memory_phase
        )

        return (result.activation, result.interference_type)


class ResonanceAmplifier:
    """Amplify resonance based on dimensional alignment.

    Like an electron moving through layers of a perceptron:
    - Layer 9 (quantitative) → compressed → Layer 3 (action trunk)
    - Layer 2 (entity) → compressed → Layer 1 (entity trunk)
    - Layer 1 (temporal) → compressed → Layer 2 (temporal trunk)

    The 3D trunk captures the essence, support layers add color.
    """

    # Trunk compression weights (9D → 3D)
    TRUNK_COMPRESSION = {
        "entity_trunk": {
            "entity": 0.7,
            "relational": 0.3
        },
        "temporal_trunk": {
            "temporal": 0.8,
            "causal": 0.2
        },
        "action_trunk": {
            "action": 0.5,
            "state": 0.25,
            "spatial": 0.15,
            "quantitative": 0.1
        }
    }

    @classmethod
    def compress_to_trunk(
        cls,
        amplitudes: dict[str, float]
    ) -> dict[str, float]:
        """Compress 9D wave amplitudes to 3D trunk representation.

        Args:
            amplitudes: Original 9D wave amplitudes

        Returns:
            3D trunk amplitudes (entity, temporal, action)
        """
        trunk = {}

        for trunk_name, compression in cls.TRUNK_COMPRESSION.items():
            trunk_value = 0.0
            for dim, weight in compression.items():
                trunk_value += amplitudes.get(dim, 0.0) * weight
            trunk[trunk_name] = min(1.0, trunk_value)  # Cap at 1.0

        return trunk

    @classmethod
    def compute_trunk_alignment(
        cls,
        query_trunk: dict[str, float],
        memory_trunk: dict[str, float]
    ) -> float:
        """Compute alignment score between two trunk representations.

        Args:
            query_trunk: Query's 3D trunk
            memory_trunk: Memory's 3D trunk

        Returns:
            Alignment score [0, 1]
        """
        alignment = 0.0
        total_weight = 0.0

        for trunk_dim in ("entity_trunk", "temporal_trunk", "action_trunk"):
            q_val = query_trunk.get(trunk_dim, 0.0)
            m_val = memory_trunk.get(trunk_dim, 0.0)

            # Cosine-like similarity
            if q_val > 0 and m_val > 0:
                similarity = min(q_val, m_val) / max(q_val, m_val)
                alignment += similarity * q_val  # Weight by query importance
                total_weight += q_val

        return alignment / total_weight if total_weight > 0 else 0.0
