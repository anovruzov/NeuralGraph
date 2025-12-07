"""Emotional memory encoding module (Cognitive Weave architecture).

This module implements emotional encoding for memories based on Plutchik's
wheel of emotions, enabling mood-congruent memory retrieval.
"""

from .data_types import (
    EmotionalResonance,
    EmotionalVector,
    EmotionDetectionResult,
    EmotionType,
    MoodState,
)
from .emotion_detector import (
    EmotionDetector,
    HybridEmotionDetector,
    OllamaEmotionDetector,
    RuleBasedEmotionDetector,
)
from .mood_tracker import MoodHistory, MoodTracker
from .resonance_scorer import (
    EmotionalResonanceScorer,
    MoodCongruentRetriever,
    ResonanceScoredResult,
)

__all__ = [
    # Data types
    "EmotionType",
    "EmotionalVector",
    "MoodState",
    "EmotionalResonance",
    "EmotionDetectionResult",
    # Detectors
    "EmotionDetector",
    "RuleBasedEmotionDetector",
    "OllamaEmotionDetector",
    "HybridEmotionDetector",
    # Scoring
    "EmotionalResonanceScorer",
    "MoodCongruentRetriever",
    "ResonanceScoredResult",
    # Tracking
    "MoodTracker",
    "MoodHistory",
]
