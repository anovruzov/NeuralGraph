"""
Reflective Memory Store (Sino-Slavic-Iranian Architecture - Phase 5)

Vector-based storage for evolving insights (Chinese MemoryOS pattern).

Reflective Memory stores higher-order insights:
- Personality profiling
- Preference inference
- Behavioral patterns

Key difference from Factual Memory:
- Subjective and evolving (not immutable)
- Uses embeddings for semantic retrieval
- Tagged with participation/observation modes (MemBench)
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class MemoryMode(str, Enum):
    """Memory mode tags per MemBench specification.

    PARTICIPATION: Memories of how the agent acted and user's reaction.
                   Feeds into agent's self-correction.

    OBSERVATION: Memories of user's behavior independent of agent.
                 Feeds into user profile.

    INFERENCE: Derived from multiple sources through reasoning.
               Higher uncertainty, requires validation.
    """
    PARTICIPATION = "participation"
    OBSERVATION = "observation"
    INFERENCE = "inference"


@dataclass
class ReflectiveInsight:
    """An evolving insight stored in reflective memory.

    Unlike FactualTriple which is immutable, ReflectiveInsight
    can be updated as understanding evolves.
    """
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""                   # The insight text
    mode: MemoryMode = MemoryMode.OBSERVATION
    confidence: float = 0.8             # Confidence score (can decrease)
    supporting_facts: list[str] = field(default_factory=list)  # FactualTriple UIDs
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    revision_count: int = 0             # How many times updated
    embedding: Optional[list[float]] = None  # Vector embedding
    tags: list[str] = field(default_factory=list)  # Additional tags
    is_validated: bool = False          # Has passed RATE validation


@dataclass
class InsightQuery:
    """Query for retrieving insights."""
    query_text: str = ""
    mode: Optional[MemoryMode] = None
    min_confidence: float = 0.0
    limit: int = 10


class ReflectiveMemoryStore:
    """Vector-based storage for evolving insights (Chinese MemoryOS pattern).

    This store implements the MemBench Reflective Memory requirements:
    - Participation/Observation mode tagging
    - Semantic similarity retrieval via embeddings
    - Confidence decay and revision tracking
    - Integration with RATE validator

    Key principles from MemoryOS:
    - Insights are evolving (can be revised)
    - Mode-specific queries enable different reasoning paths
    - Lower confidence than factual memory
    """

    def __init__(self, embedding_dim: int = 768):
        """Initialize the reflective memory store.

        Args:
            embedding_dim: Dimension of embedding vectors
        """
        self._insights: dict[str, ReflectiveInsight] = {}
        self._mode_index: dict[MemoryMode, list[str]] = defaultdict(list)
        self._tag_index: dict[str, list[str]] = defaultdict(list)
        self._embedding_dim = embedding_dim

        # Statistics
        self._total_insights = 0
        self._revisions = 0

    async def store_insight(
        self,
        content: str,
        mode: MemoryMode,
        supporting_facts: Optional[list[str]] = None,
        confidence: float = 0.8,
        embedding: Optional[list[float]] = None,
        tags: Optional[list[str]] = None,
    ) -> ReflectiveInsight:
        """Store a reflective insight.

        Args:
            content: The insight text
            mode: Memory mode (participation/observation/inference)
            supporting_facts: UIDs of supporting FactualTriples
            confidence: Confidence score
            embedding: Pre-computed embedding (optional)
            tags: Additional classification tags

        Returns:
            The stored ReflectiveInsight
        """
        insight = ReflectiveInsight(
            content=content,
            mode=mode,
            confidence=confidence,
            supporting_facts=supporting_facts or [],
            embedding=embedding,
            tags=tags or [],
        )

        self._insights[insight.uid] = insight
        self._mode_index[mode].append(insight.uid)

        for tag in (tags or []):
            self._tag_index[tag.lower()].append(insight.uid)

        self._total_insights += 1
        logger.debug(f"Stored insight: {content[:50]}... (mode={mode.value})")

        return insight

    def get_insight(self, uid: str) -> Optional[ReflectiveInsight]:
        """Get insight by UID."""
        return self._insights.get(uid)

    def update_insight(
        self,
        uid: str,
        content: Optional[str] = None,
        confidence: Optional[float] = None,
        supporting_facts: Optional[list[str]] = None,
        embedding: Optional[list[float]] = None,
    ) -> Optional[ReflectiveInsight]:
        """Update an existing insight.

        Args:
            uid: Insight UID
            content: New content (if updating)
            confidence: New confidence (if updating)
            supporting_facts: Additional supporting facts
            embedding: New embedding

        Returns:
            Updated insight or None if not found
        """
        insight = self._insights.get(uid)
        if not insight:
            return None

        if content is not None:
            insight.content = content
        if confidence is not None:
            insight.confidence = confidence
        if supporting_facts is not None:
            insight.supporting_facts.extend(supporting_facts)
        if embedding is not None:
            insight.embedding = embedding

        insight.updated_at = datetime.now(timezone.utc)
        insight.revision_count += 1
        self._revisions += 1

        return insight

    def mark_validated(self, uid: str) -> bool:
        """Mark an insight as validated by RATE validator.

        Args:
            uid: Insight UID

        Returns:
            True if marked, False if not found
        """
        insight = self._insights.get(uid)
        if insight:
            insight.is_validated = True
            return True
        return False

    def decay_confidence(self, uid: str, decay_factor: float = 0.9) -> Optional[float]:
        """Decay confidence of an insight over time.

        Args:
            uid: Insight UID
            decay_factor: Multiplicative decay factor

        Returns:
            New confidence or None if not found
        """
        insight = self._insights.get(uid)
        if insight:
            insight.confidence *= decay_factor
            return insight.confidence
        return None

    def query_by_mode(
        self,
        mode: MemoryMode,
        limit: int = 10,
        min_confidence: float = 0.0,
    ) -> list[ReflectiveInsight]:
        """Query insights by mode.

        Args:
            mode: Memory mode to filter by
            limit: Maximum results
            min_confidence: Minimum confidence threshold

        Returns:
            List of matching insights
        """
        uids = self._mode_index.get(mode, [])
        results = []

        for uid in uids:
            insight = self._insights.get(uid)
            if insight and insight.confidence >= min_confidence:
                results.append(insight)

        # Sort by confidence and recency
        results.sort(
            key=lambda i: (i.confidence, i.updated_at.timestamp()),
            reverse=True
        )

        return results[:limit]

    def query_by_tag(
        self,
        tag: str,
        limit: int = 10,
    ) -> list[ReflectiveInsight]:
        """Query insights by tag.

        Args:
            tag: Tag to filter by
            limit: Maximum results

        Returns:
            List of matching insights
        """
        uids = self._tag_index.get(tag.lower(), [])
        results = [self._insights[uid] for uid in uids if uid in self._insights]
        return results[:limit]

    async def semantic_search(
        self,
        query_embedding: list[float],
        mode: Optional[MemoryMode] = None,
        limit: int = 10,
        min_confidence: float = 0.0,
    ) -> list[tuple[ReflectiveInsight, float]]:
        """Search insights by semantic similarity.

        Args:
            query_embedding: Query embedding vector
            mode: Optional mode filter
            limit: Maximum results
            min_confidence: Minimum confidence threshold

        Returns:
            List of (insight, similarity_score) tuples
        """
        candidates = []

        if mode:
            uids = self._mode_index.get(mode, [])
            insights = [self._insights[uid] for uid in uids if uid in self._insights]
        else:
            insights = list(self._insights.values())

        query_vec = np.array(query_embedding)

        for insight in insights:
            if insight.confidence < min_confidence:
                continue
            if insight.embedding is None:
                continue

            # Cosine similarity
            insight_vec = np.array(insight.embedding)
            norm_q = np.linalg.norm(query_vec)
            norm_i = np.linalg.norm(insight_vec)

            if norm_q > 0 and norm_i > 0:
                similarity = float(np.dot(query_vec, insight_vec) / (norm_q * norm_i))
                candidates.append((insight, similarity))

        # Sort by similarity
        candidates.sort(key=lambda x: x[1], reverse=True)

        return candidates[:limit]

    def get_participation_insights(self, limit: int = 10) -> list[ReflectiveInsight]:
        """Get insights about agent-user interactions.

        These help the agent understand how its actions affected the user.
        """
        return self.query_by_mode(MemoryMode.PARTICIPATION, limit)

    def get_observation_insights(self, limit: int = 10) -> list[ReflectiveInsight]:
        """Get insights about user behavior independent of agent.

        These help build the user profile.
        """
        return self.query_by_mode(MemoryMode.OBSERVATION, limit)

    def get_inference_insights(
        self,
        limit: int = 10,
        validated_only: bool = False,
    ) -> list[ReflectiveInsight]:
        """Get derived/inferred insights.

        Args:
            limit: Maximum results
            validated_only: Only return RATE-validated insights

        Returns:
            List of inference insights
        """
        insights = self.query_by_mode(MemoryMode.INFERENCE, limit * 2)

        if validated_only:
            insights = [i for i in insights if i.is_validated]

        return insights[:limit]

    def find_similar_insights(
        self,
        insight_uid: str,
        limit: int = 5,
    ) -> list[tuple[ReflectiveInsight, float]]:
        """Find insights similar to a given one.

        Args:
            insight_uid: Reference insight UID
            limit: Maximum results

        Returns:
            List of (insight, similarity) tuples
        """
        ref_insight = self._insights.get(insight_uid)
        if not ref_insight or not ref_insight.embedding:
            return []

        candidates = []

        for uid, insight in self._insights.items():
            if uid == insight_uid:
                continue
            if not insight.embedding:
                continue

            ref_vec = np.array(ref_insight.embedding)
            insight_vec = np.array(insight.embedding)

            norm_r = np.linalg.norm(ref_vec)
            norm_i = np.linalg.norm(insight_vec)

            if norm_r > 0 and norm_i > 0:
                similarity = float(np.dot(ref_vec, insight_vec) / (norm_r * norm_i))
                candidates.append((insight, similarity))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:limit]

    def delete_insight(self, uid: str) -> bool:
        """Delete an insight.

        Args:
            uid: Insight UID

        Returns:
            True if deleted, False if not found
        """
        insight = self._insights.pop(uid, None)
        if not insight:
            return False

        # Remove from mode index
        if uid in self._mode_index.get(insight.mode, []):
            self._mode_index[insight.mode].remove(uid)

        # Remove from tag indexes
        for tag in insight.tags:
            if uid in self._tag_index.get(tag.lower(), []):
                self._tag_index[tag.lower()].remove(uid)

        return True

    def get_stats(self) -> dict:
        """Get storage statistics."""
        mode_counts = {
            mode.value: len(uids)
            for mode, uids in self._mode_index.items()
        }

        return {
            "total_insights": self._total_insights,
            "active_insights": len(self._insights),
            "revisions": self._revisions,
            "mode_counts": mode_counts,
            "validated_count": sum(
                1 for i in self._insights.values() if i.is_validated
            ),
        }

    def clear(self):
        """Clear all stored insights."""
        self._insights.clear()
        self._mode_index.clear()
        self._tag_index.clear()
        self._total_insights = 0
        self._revisions = 0
