"""Data types for emotional memory encoding (Cognitive Weave architecture)."""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EmotionType(str, Enum):
    """Plutchik's 8 basic emotions."""

    JOY = "joy"
    TRUST = "trust"
    FEAR = "fear"
    SURPRISE = "surprise"
    SADNESS = "sadness"
    DISGUST = "disgust"
    ANGER = "anger"
    ANTICIPATION = "anticipation"

    @classmethod
    def opposites(cls) -> dict["EmotionType", "EmotionType"]:
        """Return opposing emotion pairs (Plutchik's wheel)."""
        return {
            cls.JOY: cls.SADNESS,
            cls.SADNESS: cls.JOY,
            cls.TRUST: cls.DISGUST,
            cls.DISGUST: cls.TRUST,
            cls.FEAR: cls.ANGER,
            cls.ANGER: cls.FEAR,
            cls.SURPRISE: cls.ANTICIPATION,
            cls.ANTICIPATION: cls.SURPRISE,
        }

    @classmethod
    def adjacent(cls) -> dict["EmotionType", tuple["EmotionType", "EmotionType"]]:
        """Return adjacent emotion pairs (for blending)."""
        return {
            cls.JOY: (cls.TRUST, cls.ANTICIPATION),
            cls.TRUST: (cls.JOY, cls.FEAR),
            cls.FEAR: (cls.TRUST, cls.SURPRISE),
            cls.SURPRISE: (cls.FEAR, cls.SADNESS),
            cls.SADNESS: (cls.SURPRISE, cls.DISGUST),
            cls.DISGUST: (cls.SADNESS, cls.ANGER),
            cls.ANGER: (cls.DISGUST, cls.ANTICIPATION),
            cls.ANTICIPATION: (cls.ANGER, cls.JOY),
        }


@dataclass
class EmotionalVector:
    """8-dimensional emotional vector based on Plutchik's wheel of emotions.

    Each dimension represents the intensity (0.0-1.0) of a basic emotion.
    Supports cosine similarity for emotional resonance matching.
    """

    joy: float = 0.0
    trust: float = 0.0
    fear: float = 0.0
    surprise: float = 0.0
    sadness: float = 0.0
    disgust: float = 0.0
    anger: float = 0.0
    anticipation: float = 0.0

    def __post_init__(self) -> None:
        """Validate and clamp values to [0.0, 1.0]."""
        self.joy = max(0.0, min(1.0, self.joy))
        self.trust = max(0.0, min(1.0, self.trust))
        self.fear = max(0.0, min(1.0, self.fear))
        self.surprise = max(0.0, min(1.0, self.surprise))
        self.sadness = max(0.0, min(1.0, self.sadness))
        self.disgust = max(0.0, min(1.0, self.disgust))
        self.anger = max(0.0, min(1.0, self.anger))
        self.anticipation = max(0.0, min(1.0, self.anticipation))

    def to_list(self) -> list[float]:
        """Convert to list of values."""
        return [
            self.joy,
            self.trust,
            self.fear,
            self.surprise,
            self.sadness,
            self.disgust,
            self.anger,
            self.anticipation,
        ]

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {
            "joy": self.joy,
            "trust": self.trust,
            "fear": self.fear,
            "surprise": self.surprise,
            "sadness": self.sadness,
            "disgust": self.disgust,
            "anger": self.anger,
            "anticipation": self.anticipation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> "EmotionalVector":
        """Create from dictionary."""
        return cls(
            joy=data.get("joy", 0.0),
            trust=data.get("trust", 0.0),
            fear=data.get("fear", 0.0),
            surprise=data.get("surprise", 0.0),
            sadness=data.get("sadness", 0.0),
            disgust=data.get("disgust", 0.0),
            anger=data.get("anger", 0.0),
            anticipation=data.get("anticipation", 0.0),
        )

    @classmethod
    def neutral(cls) -> "EmotionalVector":
        """Create a neutral emotional vector."""
        return cls()

    @classmethod
    def from_emotion(
        cls, emotion: EmotionType, intensity: float = 1.0
    ) -> "EmotionalVector":
        """Create a vector from a single dominant emotion."""
        kwargs = {emotion.value: min(1.0, max(0.0, intensity))}
        return cls(**kwargs)

    def magnitude(self) -> float:
        """Calculate the magnitude (L2 norm) of the vector."""
        values = self.to_list()
        return math.sqrt(sum(v * v for v in values))

    def normalize(self) -> "EmotionalVector":
        """Return a normalized version of this vector."""
        mag = self.magnitude()
        if mag == 0:
            return EmotionalVector.neutral()
        values = self.to_list()
        normalized = [v / mag for v in values]
        return EmotionalVector(
            joy=normalized[0],
            trust=normalized[1],
            fear=normalized[2],
            surprise=normalized[3],
            sadness=normalized[4],
            disgust=normalized[5],
            anger=normalized[6],
            anticipation=normalized[7],
        )

    def cosine_similarity(self, other: "EmotionalVector") -> float:
        """Calculate cosine similarity with another emotional vector.

        Returns:
            Similarity score between -1.0 and 1.0.
        """
        a = self.to_list()
        b = other.to_list()

        dot_product = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot_product / (mag_a * mag_b)

    def dominant_emotion(self) -> EmotionType | None:
        """Return the emotion with highest intensity."""
        values = self.to_dict()
        if all(v == 0 for v in values.values()):
            return None
        max_emotion = max(values.items(), key=lambda x: x[1])
        return EmotionType(max_emotion[0])

    def top_emotions(self, n: int = 3) -> list[tuple[EmotionType, float]]:
        """Return top n emotions by intensity."""
        values = self.to_dict()
        sorted_emotions = sorted(values.items(), key=lambda x: x[1], reverse=True)
        return [
            (EmotionType(e), v)
            for e, v in sorted_emotions[:n]
            if v > 0
        ]

    def blend(self, other: "EmotionalVector", weight: float = 0.5) -> "EmotionalVector":
        """Blend with another emotional vector.

        Args:
            other: Another emotional vector.
            weight: Weight for the other vector (0.0-1.0).

        Returns:
            Blended emotional vector.
        """
        w = max(0.0, min(1.0, weight))
        a = self.to_list()
        b = other.to_list()
        blended = [(1 - w) * x + w * y for x, y in zip(a, b)]
        return EmotionalVector(
            joy=blended[0],
            trust=blended[1],
            fear=blended[2],
            surprise=blended[3],
            sadness=blended[4],
            disgust=blended[5],
            anger=blended[6],
            anticipation=blended[7],
        )

    def valence(self) -> float:
        """Calculate emotional valence (positive vs negative).

        Returns:
            Valence score from -1.0 (negative) to 1.0 (positive).
        """
        positive = self.joy + self.trust + self.anticipation + self.surprise * 0.5
        negative = self.sadness + self.disgust + self.anger + self.fear
        total = positive + negative
        if total == 0:
            return 0.0
        return (positive - negative) / total

    def arousal(self) -> float:
        """Calculate emotional arousal (high vs low energy).

        Returns:
            Arousal score from 0.0 (calm) to 1.0 (activated).
        """
        high_arousal = self.anger + self.fear + self.surprise + self.joy * 0.5
        low_arousal = self.sadness + self.disgust * 0.5
        return min(1.0, high_arousal / 4.0) if high_arousal > low_arousal else 0.0


@dataclass
class MoodState:
    """User mood state with temporal decay.

    Mood represents the ambient emotional state that influences memory retrieval.
    Implements Ebbinghaus-style decay for mood fading over time.
    """

    emotional_vector: EmotionalVector
    intensity: float = 1.0  # 0.0-1.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "inferred"  # "explicit", "inferred", "default"
    context: str | None = None  # Optional context description

    def __post_init__(self) -> None:
        """Validate intensity."""
        self.intensity = max(0.0, min(1.0, self.intensity))

    def decay(self, half_life_hours: float = 4.0) -> "MoodState":
        """Apply temporal decay to mood intensity.

        Args:
            half_life_hours: Time for mood to decay to half intensity.

        Returns:
            New MoodState with decayed intensity.
        """
        now = datetime.now(timezone.utc)
        elapsed_hours = (now - self.timestamp).total_seconds() / 3600.0

        # Exponential decay: I(t) = I_0 * e^(-lambda * t)
        # Half-life: lambda = ln(2) / half_life
        decay_constant = math.log(2) / half_life_hours
        decayed_intensity = self.intensity * math.exp(-decay_constant * elapsed_hours)

        return MoodState(
            emotional_vector=self.emotional_vector,
            intensity=max(0.0, decayed_intensity),
            timestamp=self.timestamp,
            source=self.source,
            context=self.context,
        )

    def effective_vector(self) -> EmotionalVector:
        """Return emotional vector scaled by current intensity."""
        v = self.emotional_vector
        i = self.intensity
        return EmotionalVector(
            joy=v.joy * i,
            trust=v.trust * i,
            fear=v.fear * i,
            surprise=v.surprise * i,
            sadness=v.sadness * i,
            disgust=v.disgust * i,
            anger=v.anger * i,
            anticipation=v.anticipation * i,
        )

    def is_neutral(self, threshold: float = 0.1) -> bool:
        """Check if mood is effectively neutral."""
        return self.intensity < threshold or self.emotional_vector.magnitude() < threshold

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "emotional_vector": self.emotional_vector.to_dict(),
            "intensity": self.intensity,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MoodState":
        """Create from dictionary."""
        return cls(
            emotional_vector=EmotionalVector.from_dict(data["emotional_vector"]),
            intensity=data.get("intensity", 1.0),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data.get("source", "inferred"),
            context=data.get("context"),
        )

    @classmethod
    def neutral(cls) -> "MoodState":
        """Create a neutral mood state."""
        return cls(
            emotional_vector=EmotionalVector.neutral(),
            intensity=0.0,
            source="default",
        )


@dataclass
class EmotionalResonance:
    """Resonance score between a memory and current mood.

    Used for mood-congruent memory retrieval (Cognitive Weave principle).
    """

    memory_vector: EmotionalVector
    mood_vector: EmotionalVector
    similarity: float  # Cosine similarity
    resonance_score: float  # Weighted score for retrieval
    is_mood_congruent: bool  # Whether memory matches mood valence

    def __post_init__(self) -> None:
        """Clamp scores."""
        self.similarity = max(-1.0, min(1.0, self.similarity))
        self.resonance_score = max(0.0, self.resonance_score)

    @classmethod
    def compute(
        cls,
        memory_vector: EmotionalVector,
        mood_state: MoodState,
        mood_weight: float = 0.3,
    ) -> "EmotionalResonance":
        """Compute emotional resonance between memory and mood.

        Args:
            memory_vector: Emotional vector of the memory.
            mood_state: Current user mood state.
            mood_weight: Weight for mood-congruent scoring (0.0-1.0).

        Returns:
            EmotionalResonance with computed scores.
        """
        # Apply decay to mood
        decayed_mood = mood_state.decay()
        effective_mood = decayed_mood.effective_vector()

        # Calculate cosine similarity
        similarity = memory_vector.cosine_similarity(effective_mood)

        # Check valence congruence
        memory_valence = memory_vector.valence()
        mood_valence = effective_mood.valence()
        is_congruent = (memory_valence >= 0) == (mood_valence >= 0)

        # Calculate resonance score
        # Higher weight when mood is strong and similar
        base_score = 1.0
        mood_bonus = similarity * mood_weight * decayed_mood.intensity
        resonance_score = base_score + mood_bonus

        return cls(
            memory_vector=memory_vector,
            mood_vector=effective_mood,
            similarity=similarity,
            resonance_score=resonance_score,
            is_mood_congruent=is_congruent,
        )


@dataclass
class EmotionDetectionResult:
    """Result of emotion detection from text."""

    text: str
    emotional_vector: EmotionalVector
    confidence: float  # 0.0-1.0
    dominant_emotion: EmotionType | None
    processing_time_ms: float = 0.0
    detector_type: str = "unknown"  # "ollama", "rule_based", "hybrid"
    raw_response: str | None = None  # For debugging

    def __post_init__(self) -> None:
        """Set dominant emotion if not provided."""
        if self.dominant_emotion is None:
            self.dominant_emotion = self.emotional_vector.dominant_emotion()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "text_length": len(self.text),
            "emotional_vector": self.emotional_vector.to_dict(),
            "confidence": self.confidence,
            "dominant_emotion": (
                self.dominant_emotion.value if self.dominant_emotion else None
            ),
            "processing_time_ms": self.processing_time_ms,
            "detector_type": self.detector_type,
        }


# Emotion keywords for rule-based detection
EMOTION_KEYWORDS: dict[EmotionType, list[str]] = {
    EmotionType.JOY: [
        "happy", "joy", "joyful", "delighted", "pleased", "glad", "cheerful",
        "elated", "thrilled", "ecstatic", "wonderful", "amazing", "fantastic",
        "love", "loved", "loving", "excited", "exciting", "great", "awesome",
        "brilliant", "excellent", "perfect", "beautiful", "celebrate", "success",
    ],
    EmotionType.TRUST: [
        "trust", "believe", "faith", "reliable", "honest", "loyal", "safe",
        "secure", "confident", "dependable", "faithful", "sincere", "genuine",
        "support", "supportive", "friend", "friendship", "ally", "partner",
    ],
    EmotionType.FEAR: [
        "afraid", "fear", "scared", "terrified", "anxious", "worried", "nervous",
        "panic", "dread", "horror", "frightened", "alarmed", "concerned",
        "apprehensive", "uneasy", "tense", "stressed", "threat", "danger",
    ],
    EmotionType.SURPRISE: [
        "surprised", "amazed", "astonished", "shocked", "stunned", "unexpected",
        "sudden", "wow", "unbelievable", "incredible", "startled", "bewildered",
        "caught off guard", "taken aback", "speechless",
    ],
    EmotionType.SADNESS: [
        "sad", "unhappy", "depressed", "miserable", "grief", "sorrow", "lonely",
        "disappointed", "heartbroken", "devastated", "hopeless", "despair",
        "melancholy", "gloomy", "down", "upset", "crying", "tears", "loss",
    ],
    EmotionType.DISGUST: [
        "disgusted", "revolted", "repulsed", "gross", "awful", "terrible",
        "horrible", "nasty", "sick", "sickening", "appalling", "offensive",
        "distasteful", "repugnant", "vile", "loathe", "hate", "hatred",
    ],
    EmotionType.ANGER: [
        "angry", "furious", "rage", "mad", "annoyed", "irritated", "frustrated",
        "outraged", "hostile", "resentful", "bitter", "enraged", "livid",
        "infuriated", "aggravated", "exasperated", "fed up",
    ],
    EmotionType.ANTICIPATION: [
        "anticipate", "expect", "looking forward", "eager", "hopeful", "curious",
        "interested", "intrigued", "awaiting", "planning", "preparing",
        "excited about", "can't wait", "upcoming", "future", "soon",
    ],
}
