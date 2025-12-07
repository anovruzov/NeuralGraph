"""Data types for multi-granularity routing (MemGAS architecture).

Implements entropy-based granularity selection for adaptive retrieval.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class GranularityLevel(str, Enum):
    """Levels of retrieval granularity."""

    KEYWORD = "keyword"       # Single keywords/entities (entropy < 1.5)
    SENTENCE = "sentence"     # Individual sentences (entropy < 3.0)
    PARAGRAPH = "paragraph"   # Paragraph-level chunks (entropy < 4.5)
    DOCUMENT = "document"     # Full document/episode (entropy >= 4.5)


# Entropy thresholds for granularity selection
ENTROPY_THRESHOLDS = {
    GranularityLevel.KEYWORD: 1.5,
    GranularityLevel.SENTENCE: 3.0,
    GranularityLevel.PARAGRAPH: 4.5,
    GranularityLevel.DOCUMENT: float('inf'),
}


@dataclass
class EntropyScore:
    """Entropy score for a query indicating complexity.

    Higher entropy = more complex query = coarser granularity needed.
    """

    query: str
    entropy: float
    token_count: int
    unique_tokens: int
    recommended_granularity: GranularityLevel

    @property
    def is_simple(self) -> bool:
        """Check if query is simple (single keyword/entity)."""
        return self.entropy < ENTROPY_THRESHOLDS[GranularityLevel.KEYWORD]

    @property
    def is_moderate(self) -> bool:
        """Check if query is moderate complexity."""
        return (
            ENTROPY_THRESHOLDS[GranularityLevel.KEYWORD] <=
            self.entropy <
            ENTROPY_THRESHOLDS[GranularityLevel.PARAGRAPH]
        )

    @property
    def is_complex(self) -> bool:
        """Check if query is complex."""
        return self.entropy >= ENTROPY_THRESHOLDS[GranularityLevel.PARAGRAPH]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "entropy": self.entropy,
            "token_count": self.token_count,
            "unique_tokens": self.unique_tokens,
            "recommended_granularity": self.recommended_granularity.value,
        }


@dataclass
class GranularChunk:
    """A chunk of content at a specific granularity level."""

    uid: str
    content: str
    granularity: GranularityLevel
    source_episode_uid: str
    session_key: str

    # Position within source
    start_pos: int = 0
    end_pos: int = 0
    sequence_num: int = 0

    # Metadata
    token_count: int = 0
    embedding: list[float] | None = None

    # Temporal
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __len__(self) -> int:
        """Return content length."""
        return len(self.content)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "uid": self.uid,
            "content": self.content,
            "granularity": self.granularity.value,
            "source_episode_uid": self.source_episode_uid,
            "session_key": self.session_key,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "sequence_num": self.sequence_num,
            "token_count": self.token_count,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class RoutingDecision:
    """Decision about which granularity level to use for retrieval."""

    query: str
    entropy_score: EntropyScore
    selected_granularity: GranularityLevel
    confidence: float
    reasoning: str

    # Alternative granularities to consider
    alternatives: list[GranularityLevel] = field(default_factory=list)

    # Performance hints
    estimated_chunks: int = 0
    estimated_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "entropy_score": self.entropy_score.to_dict(),
            "selected_granularity": self.selected_granularity.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "alternatives": [g.value for g in self.alternatives],
            "estimated_chunks": self.estimated_chunks,
            "estimated_latency_ms": self.estimated_latency_ms,
        }


@dataclass
class ChunkingConfig:
    """Configuration for multi-level chunking."""

    # Sentence chunking
    sentence_min_length: int = 10
    sentence_max_length: int = 500

    # Paragraph chunking
    paragraph_min_sentences: int = 2
    paragraph_max_sentences: int = 10
    paragraph_max_length: int = 2000

    # Keyword extraction
    min_keyword_length: int = 3
    max_keywords_per_chunk: int = 20

    # Overlap for context preservation
    sentence_overlap: int = 1
    paragraph_overlap: int = 1


@dataclass
class RetrievalResult:
    """Result of granularity-aware retrieval."""

    query: str
    routing_decision: RoutingDecision
    chunks: list[GranularChunk]
    scores: list[float]

    # Timing
    routing_time_ms: float = 0.0
    retrieval_time_ms: float = 0.0
    total_time_ms: float = 0.0

    @property
    def chunk_count(self) -> int:
        """Number of retrieved chunks."""
        return len(self.chunks)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "routing_decision": self.routing_decision.to_dict(),
            "chunks": [c.to_dict() for c in self.chunks],
            "scores": self.scores,
            "routing_time_ms": self.routing_time_ms,
            "retrieval_time_ms": self.retrieval_time_ms,
            "total_time_ms": self.total_time_ms,
        }


@dataclass
class GranularityStats:
    """Statistics for granularity routing."""

    total_queries: int = 0
    queries_by_granularity: dict[GranularityLevel, int] = field(
        default_factory=lambda: {g: 0 for g in GranularityLevel}
    )
    average_entropy: float = 0.0
    average_latency_ms: float = 0.0

    def record_query(
        self,
        granularity: GranularityLevel,
        entropy: float,
        latency_ms: float,
    ) -> None:
        """Record a query for statistics."""
        self.total_queries += 1
        self.queries_by_granularity[granularity] += 1

        # Running average
        n = self.total_queries
        self.average_entropy = (
            (self.average_entropy * (n - 1) + entropy) / n
        )
        self.average_latency_ms = (
            (self.average_latency_ms * (n - 1) + latency_ms) / n
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_queries": self.total_queries,
            "queries_by_granularity": {
                g.value: c for g, c in self.queries_by_granularity.items()
            },
            "average_entropy": self.average_entropy,
            "average_latency_ms": self.average_latency_ms,
        }
