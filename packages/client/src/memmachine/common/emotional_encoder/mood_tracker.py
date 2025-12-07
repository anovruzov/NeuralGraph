"""Mood tracking service for user emotional state management."""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .data_types import (
    EmotionalVector,
    EmotionDetectionResult,
    MoodState,
)
from .emotion_detector import EmotionDetector, RuleBasedEmotionDetector

logger = logging.getLogger(__name__)


@dataclass
class MoodHistory:
    """Track history of mood states for analysis."""

    max_history: int = 100
    states: deque[MoodState] = field(default_factory=lambda: deque(maxlen=100))

    def __post_init__(self) -> None:
        """Initialize with correct maxlen."""
        if len(self.states) == 0:
            self.states = deque(maxlen=self.max_history)

    def add(self, state: MoodState) -> None:
        """Add a mood state to history."""
        self.states.append(state)

    def get_recent(self, n: int = 10) -> list[MoodState]:
        """Get n most recent mood states."""
        return list(self.states)[-n:]

    def get_average_vector(self, n: int | None = None) -> EmotionalVector:
        """Calculate average emotional vector over recent states.

        Args:
            n: Number of recent states to average. None for all.

        Returns:
            Average EmotionalVector.
        """
        states = list(self.states) if n is None else list(self.states)[-n:]

        if not states:
            return EmotionalVector.neutral()

        # Sum all vectors
        totals = {
            "joy": 0.0,
            "trust": 0.0,
            "fear": 0.0,
            "surprise": 0.0,
            "sadness": 0.0,
            "disgust": 0.0,
            "anger": 0.0,
            "anticipation": 0.0,
        }

        for state in states:
            vec = state.emotional_vector.to_dict()
            for key in totals:
                totals[key] += vec[key] * state.intensity

        # Average
        count = len(states)
        for key in totals:
            totals[key] /= count

        return EmotionalVector.from_dict(totals)

    def get_trend(self, window: int = 5) -> dict[str, float]:
        """Calculate mood trend over recent states.

        Args:
            window: Number of states to compare.

        Returns:
            Dictionary of emotion deltas (positive = increasing).
        """
        if len(self.states) < window * 2:
            return {e.value: 0.0 for e in EmotionalVector.neutral().to_dict()}

        recent = list(self.states)[-window:]
        older = list(self.states)[-(window * 2):-window]

        recent_avg = self._average_states(recent)
        older_avg = self._average_states(older)

        trend = {}
        for key in recent_avg:
            trend[key] = recent_avg[key] - older_avg[key]

        return trend

    def _average_states(self, states: list[MoodState]) -> dict[str, float]:
        """Calculate average vector from states."""
        if not states:
            return EmotionalVector.neutral().to_dict()

        totals = {k: 0.0 for k in EmotionalVector.neutral().to_dict()}
        for state in states:
            vec = state.emotional_vector.to_dict()
            for key in totals:
                totals[key] += vec[key] * state.intensity

        count = len(states)
        return {k: v / count for k, v in totals.items()}


class MoodTracker:
    """Tracks and manages user mood state over time.

    Provides current mood estimation, mood updates from conversation,
    and mood decay over time.
    """

    def __init__(
        self,
        emotion_detector: EmotionDetector | None = None,
        default_half_life_hours: float = 4.0,
        mood_update_weight: float = 0.3,
        min_update_intensity: float = 0.2,
        max_history: int = 100,
    ):
        """Initialize mood tracker.

        Args:
            emotion_detector: Detector for inferring mood from text.
            default_half_life_hours: Default mood decay half-life.
            mood_update_weight: Weight for blending new emotions.
            min_update_intensity: Minimum intensity to update mood.
            max_history: Maximum mood states to keep in history.
        """
        self._detector = emotion_detector or RuleBasedEmotionDetector()
        self._half_life = default_half_life_hours
        self._update_weight = mood_update_weight
        self._min_intensity = min_update_intensity

        # Per-session mood tracking
        self._session_moods: dict[str, MoodState] = {}
        self._session_history: dict[str, MoodHistory] = {}
        self._max_history = max_history

    def get_current_mood(
        self,
        session_key: str,
        apply_decay: bool = True,
    ) -> MoodState:
        """Get current mood for a session.

        Args:
            session_key: Session identifier.
            apply_decay: Whether to apply temporal decay.

        Returns:
            Current MoodState (decayed if applicable).
        """
        if session_key not in self._session_moods:
            return MoodState.neutral()

        mood = self._session_moods[session_key]

        if apply_decay:
            mood = mood.decay(self._half_life)
            # Update stored mood with decayed value
            if mood.intensity < 0.01:
                # Mood has effectively decayed to nothing
                del self._session_moods[session_key]
                return MoodState.neutral()

        return mood

    def set_mood(
        self,
        session_key: str,
        mood: MoodState,
        record_history: bool = True,
    ) -> None:
        """Explicitly set mood for a session.

        Args:
            session_key: Session identifier.
            mood: Mood state to set.
            record_history: Whether to record in history.
        """
        self._session_moods[session_key] = mood

        if record_history:
            self._ensure_history(session_key)
            self._session_history[session_key].add(mood)

        logger.debug(
            f"Set mood for {session_key}: "
            f"dominant={mood.emotional_vector.dominant_emotion()}, "
            f"intensity={mood.intensity:.2f}"
        )

    async def update_from_text(
        self,
        session_key: str,
        text: str,
        source: str = "inferred",
    ) -> MoodState:
        """Update mood based on text content.

        Args:
            session_key: Session identifier.
            text: Text to analyze for emotional content.
            source: Source of the mood update.

        Returns:
            Updated MoodState.
        """
        # Detect emotions in text
        detection = await self._detector.detect(text)

        # Skip if low confidence or neutral
        if detection.confidence < self._min_intensity:
            return self.get_current_mood(session_key)

        if detection.emotional_vector.magnitude() < self._min_intensity:
            return self.get_current_mood(session_key)

        # Get current mood
        current = self.get_current_mood(session_key, apply_decay=True)

        # Blend new emotions with current mood
        if current.is_neutral():
            # No current mood, use detected emotions directly
            new_mood = MoodState(
                emotional_vector=detection.emotional_vector,
                intensity=detection.confidence,
                source=source,
                context=text[:100] if len(text) > 100 else text,
            )
        else:
            # Blend with existing mood
            blended_vector = current.emotional_vector.blend(
                detection.emotional_vector,
                weight=self._update_weight,
            )
            new_mood = MoodState(
                emotional_vector=blended_vector,
                intensity=max(current.intensity, detection.confidence),
                source=source,
                context=text[:100] if len(text) > 100 else text,
            )

        self.set_mood(session_key, new_mood)
        return new_mood

    async def infer_mood_from_messages(
        self,
        session_key: str,
        messages: list[str],
        decay_between: bool = False,
    ) -> MoodState:
        """Infer mood from a sequence of messages.

        Args:
            session_key: Session identifier.
            messages: List of message texts.
            decay_between: Whether to apply decay between messages.

        Returns:
            Final inferred MoodState.
        """
        for message in messages:
            await self.update_from_text(session_key, message, source="inferred")
            if decay_between:
                # Small decay between messages
                current = self.get_current_mood(session_key)
                if current.intensity > 0:
                    current.intensity *= 0.95
                    self._session_moods[session_key] = current

        return self.get_current_mood(session_key)

    def get_mood_history(
        self,
        session_key: str,
        n: int | None = None,
    ) -> list[MoodState]:
        """Get mood history for a session.

        Args:
            session_key: Session identifier.
            n: Number of recent states to return. None for all.

        Returns:
            List of MoodState objects.
        """
        if session_key not in self._session_history:
            return []

        history = self._session_history[session_key]
        if n is None:
            return list(history.states)
        return history.get_recent(n)

    def get_mood_trend(
        self,
        session_key: str,
        window: int = 5,
    ) -> dict[str, float]:
        """Get mood trend for a session.

        Args:
            session_key: Session identifier.
            window: Comparison window size.

        Returns:
            Dictionary of emotion trends.
        """
        if session_key not in self._session_history:
            return {e: 0.0 for e in EmotionalVector.neutral().to_dict()}

        return self._session_history[session_key].get_trend(window)

    def get_baseline_mood(
        self,
        session_key: str,
        n: int = 20,
    ) -> EmotionalVector:
        """Get baseline mood from history average.

        Args:
            session_key: Session identifier.
            n: Number of recent states to average.

        Returns:
            Baseline EmotionalVector.
        """
        if session_key not in self._session_history:
            return EmotionalVector.neutral()

        return self._session_history[session_key].get_average_vector(n)

    def clear_session(self, session_key: str) -> None:
        """Clear mood data for a session.

        Args:
            session_key: Session identifier.
        """
        if session_key in self._session_moods:
            del self._session_moods[session_key]
        if session_key in self._session_history:
            del self._session_history[session_key]

    def _ensure_history(self, session_key: str) -> None:
        """Ensure history exists for session."""
        if session_key not in self._session_history:
            self._session_history[session_key] = MoodHistory(
                max_history=self._max_history
            )

    def get_statistics(self) -> dict[str, Any]:
        """Get tracker statistics."""
        return {
            "active_sessions": len(self._session_moods),
            "sessions_with_history": len(self._session_history),
            "half_life_hours": self._half_life,
            "update_weight": self._update_weight,
        }
