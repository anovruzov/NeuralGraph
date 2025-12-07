"""Emotional resonance scoring for mood-congruent memory retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .data_types import (
    EmotionalResonance,
    EmotionalVector,
    MoodState,
)

logger = logging.getLogger(__name__)


class ScoredMemory(Protocol):
    """Protocol for memories that can be scored."""

    @property
    def uid(self) -> str:
        """Unique identifier."""
        ...

    @property
    def content(self) -> str:
        """Memory content."""
        ...

    @property
    def emotional_vector(self) -> dict[str, float] | None:
        """Emotional vector dictionary."""
        ...


@dataclass
class ResonanceScoredResult:
    """Memory with emotional resonance scoring applied."""

    memory: Any  # The original memory object
    original_score: float  # Original retrieval score (semantic)
    resonance: EmotionalResonance  # Emotional resonance data
    final_score: float  # Combined score after resonance adjustment
    rank_boost: int  # Position change due to resonance


class EmotionalResonanceScorer:
    """Scores and re-ranks memories based on emotional resonance.

    Implements the Cognitive Weave principle of mood-congruent retrieval:
    memories with emotional signatures matching the user's current mood
    are boosted in ranking.
    """

    def __init__(
        self,
        mood_weight: float = 0.3,
        valence_boost: float = 0.1,
        min_confidence: float = 0.2,
        enable_logging: bool = True,
    ):
        """Initialize resonance scorer.

        Args:
            mood_weight: Weight for mood in final scoring (0.0-1.0).
            valence_boost: Additional boost for valence-congruent memories.
            min_confidence: Minimum confidence to apply resonance scoring.
            enable_logging: Whether to log scoring details.
        """
        self._mood_weight = max(0.0, min(1.0, mood_weight))
        self._valence_boost = valence_boost
        self._min_confidence = min_confidence
        self._enable_logging = enable_logging

        # Statistics
        self._total_scored = 0
        self._resonance_applied = 0

    @property
    def statistics(self) -> dict[str, int]:
        """Return scoring statistics."""
        return {
            "total_scored": self._total_scored,
            "resonance_applied": self._resonance_applied,
        }

    def score_memory(
        self,
        memory: Any,
        emotional_vector: EmotionalVector,
        mood_state: MoodState,
        original_score: float = 1.0,
    ) -> ResonanceScoredResult:
        """Score a single memory with emotional resonance.

        Args:
            memory: The memory object.
            emotional_vector: Emotional vector of the memory.
            mood_state: Current user mood state.
            original_score: Original retrieval score.

        Returns:
            ResonanceScoredResult with adjusted scoring.
        """
        self._total_scored += 1

        # Compute resonance
        resonance = EmotionalResonance.compute(
            memory_vector=emotional_vector,
            mood_state=mood_state,
            mood_weight=self._mood_weight,
        )

        # Calculate final score
        final_score = original_score * resonance.resonance_score

        # Apply valence boost if congruent
        if resonance.is_mood_congruent:
            final_score *= (1.0 + self._valence_boost)

        self._resonance_applied += 1

        if self._enable_logging:
            logger.debug(
                f"Resonance score: original={original_score:.3f}, "
                f"resonance={resonance.resonance_score:.3f}, "
                f"final={final_score:.3f}, "
                f"congruent={resonance.is_mood_congruent}"
            )

        return ResonanceScoredResult(
            memory=memory,
            original_score=original_score,
            resonance=resonance,
            final_score=final_score,
            rank_boost=0,  # Calculated during re-ranking
        )

    def score_memories(
        self,
        memories: list[Any],
        mood_state: MoodState,
        original_scores: list[float] | None = None,
        get_emotional_vector: Callable | None = None,
    ) -> list[ResonanceScoredResult]:
        """Score multiple memories with emotional resonance.

        Args:
            memories: List of memory objects.
            mood_state: Current user mood state.
            original_scores: Original retrieval scores. Uses 1.0 if None.
            get_emotional_vector: Function to extract emotional vector from memory.

        Returns:
            List of ResonanceScoredResult objects.
        """
        if original_scores is None:
            original_scores = [1.0] * len(memories)

        if get_emotional_vector is None:
            get_emotional_vector = self._default_get_emotional_vector

        results = []
        for memory, score in zip(memories, original_scores):
            vector = get_emotional_vector(memory)
            if vector is None:
                # No emotional data, use neutral
                vector = EmotionalVector.neutral()

            result = self.score_memory(
                memory=memory,
                emotional_vector=vector,
                mood_state=mood_state,
                original_score=score,
            )
            results.append(result)

        return results

    def _default_get_emotional_vector(self, memory: Any) -> EmotionalVector | None:
        """Default extractor for emotional vector from memory."""
        # Try common attribute patterns
        if hasattr(memory, "emotional_vector"):
            ev = memory.emotional_vector
            if isinstance(ev, dict):
                return EmotionalVector.from_dict(ev)
            elif isinstance(ev, EmotionalVector):
                return ev

        if hasattr(memory, "metadata"):
            metadata = memory.metadata
            if isinstance(metadata, dict) and "emotional_vector" in metadata:
                return EmotionalVector.from_dict(metadata["emotional_vector"])

        return None

    def rerank_by_resonance(
        self,
        scored_results: list[ResonanceScoredResult],
        top_k: int | None = None,
    ) -> list[ResonanceScoredResult]:
        """Re-rank scored results by final resonance-adjusted score.

        Args:
            scored_results: List of scored results.
            top_k: Return only top k results. None for all.

        Returns:
            Re-ranked list of results with rank_boost calculated.
        """
        # Store original positions
        for i, result in enumerate(scored_results):
            result._original_rank = i

        # Sort by final score descending
        sorted_results = sorted(
            scored_results,
            key=lambda x: x.final_score,
            reverse=True,
        )

        # Calculate rank boost
        for new_rank, result in enumerate(sorted_results):
            result.rank_boost = result._original_rank - new_rank

        if top_k is not None:
            return sorted_results[:top_k]

        return sorted_results

    def blend_with_semantic_score(
        self,
        semantic_score: float,
        resonance_score: float,
        semantic_weight: float = 0.7,
    ) -> float:
        """Blend semantic and resonance scores.

        Args:
            semantic_score: Original semantic similarity score.
            resonance_score: Emotional resonance score.
            semantic_weight: Weight for semantic score (0.0-1.0).

        Returns:
            Blended final score.
        """
        resonance_weight = 1.0 - semantic_weight
        return (semantic_weight * semantic_score) + (resonance_weight * resonance_score)


class MoodCongruentRetriever:
    """High-level retriever with integrated mood-congruent scoring.

    Wraps existing retrieval mechanisms and applies emotional resonance.
    """

    def __init__(
        self,
        resonance_scorer: EmotionalResonanceScorer | None = None,
        default_mood_weight: float = 0.3,
        enable_resonance: bool = True,
    ):
        """Initialize mood-congruent retriever.

        Args:
            resonance_scorer: Resonance scorer instance.
            default_mood_weight: Default weight for mood scoring.
            enable_resonance: Whether resonance scoring is enabled.
        """
        self._scorer = resonance_scorer or EmotionalResonanceScorer(
            mood_weight=default_mood_weight
        )
        self._enable_resonance = enable_resonance

    def apply_resonance(
        self,
        memories: list[Any],
        scores: list[float],
        mood_state: MoodState | None,
        rerank: bool = True,
        top_k: int | None = None,
    ) -> tuple[list[Any], list[float]]:
        """Apply resonance scoring to retrieval results.

        Args:
            memories: Retrieved memories.
            scores: Original retrieval scores.
            mood_state: Current mood state. None to skip resonance.
            rerank: Whether to re-rank by resonance.
            top_k: Return only top k results.

        Returns:
            Tuple of (re-ranked memories, adjusted scores).
        """
        if not self._enable_resonance or mood_state is None or mood_state.is_neutral():
            # No resonance adjustment
            if top_k:
                return memories[:top_k], scores[:top_k]
            return memories, scores

        # Score with resonance
        scored = self._scorer.score_memories(
            memories=memories,
            mood_state=mood_state,
            original_scores=scores,
        )

        if rerank:
            scored = self._scorer.rerank_by_resonance(scored, top_k=top_k)

        # Extract memories and scores
        reranked_memories = [r.memory for r in scored]
        adjusted_scores = [r.final_score for r in scored]

        return reranked_memories, adjusted_scores
