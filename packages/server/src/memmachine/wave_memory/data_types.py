"""Data types for Wave Memory System.

Each memory is encoded as a multi-dimensional wave with:
- 9 amplitude dimensions (temporal, entity, relational, action, state, spatial, causal, emotional, quantitative)
- Extracted signals when amplitude exceeds threshold
- Optional semantic embedding for fallback resonance
"""

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class SignalType(str, Enum):
    """Types of signals that can be extracted from memories."""

    TEMPORAL = "temporal"           # When-ness
    ENTITY = "entity"               # Who/what-ness
    RELATION = "relation"           # Connection-ness
    ACTION = "action"               # Doing-ness
    STATE = "state"                 # Being-ness
    SPATIAL = "spatial"             # Where-ness
    CAUSAL = "causal"               # Why-ness
    EMOTIONAL = "emotional"         # Feeling-ness
    QUANTITATIVE = "quantitative"   # How-much-ness


# Mapping from SignalType to index for vector operations
DIMENSION_INDEX: dict[str, int] = {
    "temporal": 0,
    "entity": 1,
    "relational": 2,
    "action": 3,
    "state": 4,
    "spatial": 5,
    "causal": 6,
    "emotional": 7,
    "quantitative": 8,
}


@dataclass
class WaveAmplitudes:
    """9-dimensional amplitude vector representing memory's signal strength.

    Each dimension represents how strongly a memory exhibits that characteristic.
    Values are normalized to [0.0, 1.0].

    Follows the pattern of EmotionalVector for consistency.
    """

    temporal: float = 0.0       # When-ness (dates, durations, sequences)
    entity: float = 0.0         # Who/what-ness (people, things, concepts)
    relational: float = 0.0     # Connection-ness (relationships, associations)
    action: float = 0.0         # Doing-ness (verbs, activities, behaviors)
    state: float = 0.0          # Being-ness (conditions, attributes, states)
    spatial: float = 0.0        # Where-ness (locations, places, directions)
    causal: float = 0.0         # Why-ness (reasons, causes, effects)
    emotional: float = 0.0      # Feeling-ness (emotions, sentiments)
    quantitative: float = 0.0   # How-much-ness (numbers, quantities, magnitudes)

    def __post_init__(self) -> None:
        """Validate and clamp values to [0.0, 1.0]."""
        self.temporal = max(0.0, min(1.0, self.temporal))
        self.entity = max(0.0, min(1.0, self.entity))
        self.relational = max(0.0, min(1.0, self.relational))
        self.action = max(0.0, min(1.0, self.action))
        self.state = max(0.0, min(1.0, self.state))
        self.spatial = max(0.0, min(1.0, self.spatial))
        self.causal = max(0.0, min(1.0, self.causal))
        self.emotional = max(0.0, min(1.0, self.emotional))
        self.quantitative = max(0.0, min(1.0, self.quantitative))

    def to_list(self) -> list[float]:
        """Convert to list of values (in DIMENSION_INDEX order)."""
        return [
            self.temporal,
            self.entity,
            self.relational,
            self.action,
            self.state,
            self.spatial,
            self.causal,
            self.emotional,
            self.quantitative,
        ]

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {
            "temporal": self.temporal,
            "entity": self.entity,
            "relational": self.relational,
            "action": self.action,
            "state": self.state,
            "spatial": self.spatial,
            "causal": self.causal,
            "emotional": self.emotional,
            "quantitative": self.quantitative,
        }

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> "WaveAmplitudes":
        """Create from dictionary."""
        return cls(
            temporal=data.get("temporal", 0.0),
            entity=data.get("entity", 0.0),
            relational=data.get("relational", 0.0),
            action=data.get("action", 0.0),
            state=data.get("state", 0.0),
            spatial=data.get("spatial", 0.0),
            causal=data.get("causal", 0.0),
            emotional=data.get("emotional", 0.0),
            quantitative=data.get("quantitative", 0.0),
        )

    @classmethod
    def from_list(cls, values: list[float]) -> "WaveAmplitudes":
        """Create from list (expects 9 values in DIMENSION_INDEX order)."""
        if len(values) != 9:
            raise ValueError(f"Expected 9 values, got {len(values)}")
        return cls(
            temporal=values[0],
            entity=values[1],
            relational=values[2],
            action=values[3],
            state=values[4],
            spatial=values[5],
            causal=values[6],
            emotional=values[7],
            quantitative=values[8],
        )

    @classmethod
    def neutral(cls) -> "WaveAmplitudes":
        """Create a neutral wave with zero amplitudes."""
        return cls()

    def magnitude(self) -> float:
        """Calculate the magnitude (L2 norm) of the amplitude vector."""
        values = self.to_list()
        return math.sqrt(sum(v * v for v in values))

    def normalize(self) -> "WaveAmplitudes":
        """Return a normalized version of this vector (unit magnitude)."""
        mag = self.magnitude()
        if mag == 0:
            return WaveAmplitudes.neutral()
        values = self.to_list()
        normalized = [v / mag for v in values]
        return WaveAmplitudes.from_list(normalized)

    def dominant_dimensions(self, threshold: float = 0.25) -> list[str]:
        """Return dimension names where amplitude exceeds threshold.

        Args:
            threshold: Minimum amplitude to be considered dominant.

        Returns:
            List of dimension names sorted by amplitude (highest first).
        """
        dims = self.to_dict()
        dominant = [(name, amp) for name, amp in dims.items() if amp >= threshold]
        dominant.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in dominant]

    def max_amplitude(self) -> tuple[str, float]:
        """Return the dimension with highest amplitude and its value."""
        dims = self.to_dict()
        max_dim = max(dims.items(), key=lambda x: x[1])
        return max_dim

    def cosine_similarity(self, other: "WaveAmplitudes") -> float:
        """Calculate cosine similarity with another amplitude vector.

        Returns:
            Similarity score between 0.0 and 1.0 (always positive for amplitudes).
        """
        a = self.to_list()
        b = other.to_list()

        dot_product = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot_product / (mag_a * mag_b)

    def weighted_dot_product(
        self,
        other: "WaveAmplitudes",
        weights: dict[str, float] | None = None
    ) -> float:
        """Calculate weighted dot product with another amplitude vector.

        Args:
            other: Another amplitude vector.
            weights: Optional weights for each dimension. If None, uses uniform weights.

        Returns:
            Weighted dot product score.
        """
        a = self.to_dict()
        b = other.to_dict()

        if weights is None:
            weights = {dim: 1.0 for dim in a.keys()}

        total = 0.0
        for dim in a.keys():
            w = weights.get(dim, 1.0)
            total += w * a[dim] * b[dim]

        return total

    def resonance_with(
        self,
        other: "WaveAmplitudes",
        weights: dict[str, float] | None = None
    ) -> float:
        """Calculate resonance score with another amplitude vector.

        Resonance is high when:
        1. Both vectors have aligned dominant dimensions
        2. The query's strong dimensions are matched in the memory

        Args:
            other: Another amplitude vector (typically a memory).
            weights: Optional weights. If None, self's amplitudes weight the comparison.

        Returns:
            Resonance score between 0.0 and 1.0.
        """
        # Use self as the query - its amplitudes weight the comparison
        q_vec = self.to_dict()
        m_vec = other.to_dict()

        if weights is None:
            # Query dimensions weight the comparison
            weights = {dim: amp for dim, amp in q_vec.items() if amp > 0.1}

        if not weights:
            # Fallback to cosine similarity if query has no strong dimensions
            return self.cosine_similarity(other)

        weighted_sum = 0.0
        weight_total = 0.0

        for dim, w in weights.items():
            q_amp = q_vec.get(dim, 0.0)
            m_amp = m_vec.get(dim, 0.0)

            if q_amp > 0.1:  # Query cares about this dimension
                # Alignment = minimum of both (bottleneck approach)
                alignment = min(q_amp, m_amp)
                weighted_sum += w * alignment
                weight_total += w

        if weight_total == 0:
            return 0.0

        return weighted_sum / weight_total

    def blend(self, other: "WaveAmplitudes", weight: float = 0.5) -> "WaveAmplitudes":
        """Blend with another amplitude vector.

        Args:
            other: Another amplitude vector.
            weight: Weight for the other vector (0.0-1.0).

        Returns:
            Blended amplitude vector.
        """
        w = max(0.0, min(1.0, weight))
        a = self.to_list()
        b = other.to_list()
        blended = [(1 - w) * x + w * y for x, y in zip(a, b)]
        return WaveAmplitudes.from_list(blended)


# =============================================================================
# SIGNAL DATA TYPES
# =============================================================================

@dataclass
class WaveSignal:
    """Base class for extracted signals.

    Signals are extracted when a memory's amplitude in a dimension
    exceeds the threshold, providing structured information.
    """

    signal_type: SignalType
    amplitude: float  # The amplitude that triggered extraction
    confidence: float = 0.8  # Confidence in the extraction
    source_span: tuple[int, int] | None = None  # Character offsets in source

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "signal_type": self.signal_type.value,
            "amplitude": self.amplitude,
            "confidence": self.confidence,
            "source_span": self.source_span,
        }


@dataclass
class TemporalSignal(WaveSignal):
    """Temporal signal with date/duration information."""

    signal_type: SignalType = field(default=SignalType.TEMPORAL, init=False)

    # Temporal-specific fields
    raw_expression: str = ""  # Original text ("last Sunday", "May 21")
    resolved_date: datetime | None = None  # Calculated absolute date
    grain: str = "day"  # "instant", "day", "week", "month", "year"
    is_range: bool = False
    range_start: datetime | None = None
    range_end: datetime | None = None
    duration: timedelta | None = None
    temporal_relation: str | None = None  # "before", "after", "during", "at"
    is_relative: bool = False  # Was this a relative expression?

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "raw_expression": self.raw_expression,
            "resolved_date": self.resolved_date.isoformat() if self.resolved_date else None,
            "grain": self.grain,
            "is_range": self.is_range,
            "range_start": self.range_start.isoformat() if self.range_start else None,
            "range_end": self.range_end.isoformat() if self.range_end else None,
            "duration": str(self.duration) if self.duration else None,
            "temporal_relation": self.temporal_relation,
            "is_relative": self.is_relative,
        })
        return base


@dataclass
class EntitySignal(WaveSignal):
    """Entity signal with identified entity information."""

    signal_type: SignalType = field(default=SignalType.ENTITY, init=False)

    # Entity-specific fields
    entity_name: str = ""
    entity_type: str = ""  # "PERSON", "ORG", "GPE", "LOC", "OBJECT", "CONCEPT"
    aliases: list[str] = field(default_factory=list)
    coreference_id: str | None = None  # For pronoun resolution
    is_speaker: bool = False  # Is this the speaker?
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "aliases": self.aliases,
            "coreference_id": self.coreference_id,
            "is_speaker": self.is_speaker,
            "attributes": self.attributes,
        })
        return base


@dataclass
class RelationSignal(WaveSignal):
    """Relationship signal between entities."""

    signal_type: SignalType = field(default=SignalType.RELATION, init=False)

    # Relation-specific fields
    source_entity: str = ""  # Who
    target_entity: str = ""  # To whom/what
    relation_type: str = ""  # "friend", "family", "colleague", "knows", etc.
    strength: float = 0.5  # Relation strength (0.0-1.0)
    bidirectional: bool = False  # Is this mutual?

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "source_entity": self.source_entity,
            "target_entity": self.target_entity,
            "relation_type": self.relation_type,
            "strength": self.strength,
            "bidirectional": self.bidirectional,
        })
        return base


@dataclass
class ActionSignal(WaveSignal):
    """Action signal with verb/event information."""

    signal_type: SignalType = field(default=SignalType.ACTION, init=False)

    # Action-specific fields
    verb_lemma: str = ""  # Lemmatized verb
    verb_tense: str = ""  # "past", "present", "future"
    agent: str | None = None  # Who did it
    patient: str | None = None  # To whom/what
    instrument: str | None = None  # With what
    manner: str | None = None  # How

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "verb_lemma": self.verb_lemma,
            "verb_tense": self.verb_tense,
            "agent": self.agent,
            "patient": self.patient,
            "instrument": self.instrument,
            "manner": self.manner,
        })
        return base


@dataclass
class StateSignal(WaveSignal):
    """State/attribute signal."""

    signal_type: SignalType = field(default=SignalType.STATE, init=False)

    # State-specific fields
    entity: str = ""  # Who/what has the state
    attribute: str = ""  # What attribute
    value: Any = None  # The value
    is_current: bool = True  # Ongoing vs past state

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "entity": self.entity,
            "attribute": self.attribute,
            "value": self.value,
            "is_current": self.is_current,
        })
        return base


@dataclass
class SpatialSignal(WaveSignal):
    """Spatial/location signal."""

    signal_type: SignalType = field(default=SignalType.SPATIAL, init=False)

    # Spatial-specific fields
    location_name: str = ""
    location_type: str = ""  # "city", "building", "region", "relative"
    reference_entity: str | None = None  # "near X", "at X's house"
    preposition: str | None = None  # "in", "at", "near", "by"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "location_name": self.location_name,
            "location_type": self.location_type,
            "reference_entity": self.reference_entity,
            "preposition": self.preposition,
        })
        return base


@dataclass
class CausalSignal(WaveSignal):
    """Causal/reason signal."""

    signal_type: SignalType = field(default=SignalType.CAUSAL, init=False)

    # Causal-specific fields
    cause: str = ""
    effect: str = ""
    causal_strength: float = 0.5  # How direct is the causation
    temporal_order: str = "cause_first"  # "cause_first", "simultaneous", "implied"
    connector: str | None = None  # "because", "therefore", "so"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "cause": self.cause,
            "effect": self.effect,
            "causal_strength": self.causal_strength,
            "temporal_order": self.temporal_order,
            "connector": self.connector,
        })
        return base


@dataclass
class EmotionalSignal(WaveSignal):
    """Emotional signal."""

    signal_type: SignalType = field(default=SignalType.EMOTIONAL, init=False)

    # Emotional-specific fields
    emotion: str = ""  # Primary emotion label
    valence: float = 0.0  # -1 (negative) to +1 (positive)
    arousal: float = 0.5  # 0 (calm) to 1 (intense)
    source_entity: str | None = None  # Who feels this
    target_entity: str | None = None  # Toward whom/what

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "emotion": self.emotion,
            "valence": self.valence,
            "arousal": self.arousal,
            "source_entity": self.source_entity,
            "target_entity": self.target_entity,
        })
        return base


@dataclass
class QuantitativeSignal(WaveSignal):
    """Quantitative/numeric signal."""

    signal_type: SignalType = field(default=SignalType.QUANTITATIVE, init=False)

    # Quantitative-specific fields
    value: float | int | None = None
    unit: str | None = None  # "dollars", "miles", "years", etc.
    comparison: str | None = None  # "more than", "less than", "equal to"
    reference: str | None = None  # What is being quantified

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        base = super().to_dict()
        base.update({
            "value": self.value,
            "unit": self.unit,
            "comparison": self.comparison,
            "reference": self.reference,
        })
        return base


# =============================================================================
# MEMORY WAVE - THE CORE DATA STRUCTURE
# =============================================================================

@dataclass
class MemoryWave:
    """A memory encoded as a multi-dimensional wave.

    This is the core data structure of the Wave Memory System.
    Each memory is represented by:
    1. Content and metadata
    2. 9-dimensional amplitude vector
    3. Extracted signals (when amplitude > threshold)
    4. Optional semantic embedding for fallback
    """

    # Identification
    wave_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Content
    content: str = ""
    speaker: str = ""

    # Temporal context
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session_key: str = ""
    msg_idx: int = 0

    # Wave representation
    amplitudes: WaveAmplitudes = field(default_factory=WaveAmplitudes)
    signals: list[WaveSignal] = field(default_factory=list)

    # Optional semantic embedding
    embedding: list[float] | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_signals_by_type(self, signal_type: SignalType) -> list[WaveSignal]:
        """Get all signals of a specific type."""
        return [s for s in self.signals if s.signal_type == signal_type]

    def get_temporal_signals(self) -> list[TemporalSignal]:
        """Get all temporal signals."""
        return [s for s in self.signals if isinstance(s, TemporalSignal)]

    def get_entity_signals(self) -> list[EntitySignal]:
        """Get all entity signals."""
        return [s for s in self.signals if isinstance(s, EntitySignal)]

    def get_relation_signals(self) -> list[RelationSignal]:
        """Get all relation signals."""
        return [s for s in self.signals if isinstance(s, RelationSignal)]

    def get_action_signals(self) -> list[ActionSignal]:
        """Get all action signals."""
        return [s for s in self.signals if isinstance(s, ActionSignal)]

    def get_emotional_signals(self) -> list[EmotionalSignal]:
        """Get all emotional signals."""
        return [s for s in self.signals if isinstance(s, EmotionalSignal)]

    def has_strong_dimension(self, dimension: str, threshold: float = 0.25) -> bool:
        """Check if a specific dimension has strong amplitude."""
        amp_dict = self.amplitudes.to_dict()
        return amp_dict.get(dimension, 0.0) >= threshold

    def dominant_dimensions(self, threshold: float = 0.25) -> list[str]:
        """Get dimensions with amplitude above threshold."""
        return self.amplitudes.dominant_dimensions(threshold)

    def amplitude_vector(self) -> list[float]:
        """Get amplitude values as a list."""
        return self.amplitudes.to_list()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "wave_id": self.wave_id,
            "content": self.content,
            "speaker": self.speaker,
            "timestamp": self.timestamp.isoformat(),
            "session_key": self.session_key,
            "msg_idx": self.msg_idx,
            "amplitudes": self.amplitudes.to_dict(),
            "signals": [s.to_dict() for s in self.signals],
            "embedding": self.embedding,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryWave":
        """Create from dictionary (basic reconstruction, signals need type handling)."""
        return cls(
            wave_id=data.get("wave_id", str(uuid.uuid4())),
            content=data.get("content", ""),
            speaker=data.get("speaker", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now(timezone.utc),
            session_key=data.get("session_key", ""),
            msg_idx=data.get("msg_idx", 0),
            amplitudes=WaveAmplitudes.from_dict(data.get("amplitudes", {})),
            signals=[],  # Signals need specialized reconstruction
            embedding=data.get("embedding"),
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        """String representation."""
        dominant = self.dominant_dimensions(0.3)
        return f"MemoryWave(id={self.wave_id[:8]}, speaker={self.speaker}, dominant={dominant}, content={self.content[:50]}...)"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def wave_combine(signals: list[float]) -> float:
    """Combine signals like wave interference.

    Multiple strong signals reinforce. Weak signals don't dominate.
    Uses geometric mean + max + arithmetic mean blend.

    Args:
        signals: List of signal strengths (0.0-1.0).

    Returns:
        Combined signal strength (0.0-1.0).
    """
    if not signals:
        return 0.0

    # Filter out zeros
    nonzero = [s for s in signals if s > 0]
    if not nonzero:
        return 0.0

    # Geometric mean for reinforcement
    product = 1.0
    for s in nonzero:
        product *= (s + 0.1)
    geo_mean = (product ** (1 / len(nonzero))) - 0.1

    # Max signal (one strong signal matters)
    max_signal = max(signals)

    # Mean signal
    mean_signal = sum(signals) / len(signals)

    # Blend: emphasize consensus, but don't ignore strong singles
    return 0.4 * geo_mean + 0.35 * max_signal + 0.25 * mean_signal
