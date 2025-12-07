"""Data types for mid-term memory (MemoryOS three-tier architecture).

Implements heat-based memory lifecycle with Ebbinghaus forgetting curve.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SegmentStatus(str, Enum):
    """Status of a memory segment in its lifecycle."""

    ACTIVE = "active"        # Currently being built (receiving episodes)
    CLOSED = "closed"        # Complete, awaiting promotion/eviction decision
    PROMOTED = "promoted"    # Consolidated into long-term memory
    EVICTED = "evicted"      # Forgotten (low heat score)


@dataclass
class HeatScore:
    """Heat-based memory scoring for lifecycle management.

    Implements the formula: Heat = alpha * N_visit + beta * L_interaction + gamma * R_recency

    Where:
    - N_visit: Access/visit count for the segment
    - L_interaction: Total interaction length (characters)
    - R_recency: Recency factor using Ebbinghaus forgetting curve
    """

    # Core metrics
    visit_count: int = 0           # N_visit
    interaction_length: int = 0    # L_interaction
    last_access_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Weight parameters
    alpha: float = 1.0    # Visit count weight
    beta: float = 0.1     # Interaction length weight
    gamma: float = 2.0    # Recency weight
    strength: float = 30.0  # Forgetting curve strength (days)

    def recency_factor(self, now: datetime | None = None) -> float:
        """Calculate recency using Ebbinghaus forgetting curve.

        R(t) = exp(-t/S)

        Args:
            now: Current time. Uses UTC now if None.

        Returns:
            Recency factor between 0.0 and 1.0.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # Convert to days
        elapsed = (now - self.last_access_time).total_seconds() / 86400.0
        elapsed = max(0.0, elapsed)

        # Forgetting curve: R = exp(-t/S)
        return math.exp(-elapsed / self.strength)

    def calculate(self, now: datetime | None = None) -> float:
        """Calculate current heat score.

        Heat = alpha * N_visit + beta * L_interaction + gamma * R_recency

        Args:
            now: Current time for recency calculation.

        Returns:
            Heat score (higher = more important to retain).
        """
        recency = self.recency_factor(now)

        heat = (
            self.alpha * self.visit_count +
            self.beta * self.interaction_length +
            self.gamma * recency
        )

        return heat

    def record_access(self, interaction_length: int = 0) -> None:
        """Record an access to this segment.

        Args:
            interaction_length: Length of interaction (characters).
        """
        self.visit_count += 1
        self.interaction_length += interaction_length
        self.last_access_time = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "visit_count": self.visit_count,
            "interaction_length": self.interaction_length,
            "last_access_time": self.last_access_time.isoformat(),
            "alpha": self.alpha,
            "beta": self.beta,
            "gamma": self.gamma,
            "strength": self.strength,
            "current_heat": self.calculate(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HeatScore":
        """Create from dictionary."""
        return cls(
            visit_count=data.get("visit_count", 0),
            interaction_length=data.get("interaction_length", 0),
            last_access_time=datetime.fromisoformat(data["last_access_time"]),
            alpha=data.get("alpha", 1.0),
            beta=data.get("beta", 0.1),
            gamma=data.get("gamma", 2.0),
            strength=data.get("strength", 30.0),
        )


@dataclass
class MemorySegment:
    """A segment of related episodes in mid-term memory.

    Segments group related episodes for consolidated processing
    and heat-based lifecycle management.
    """

    uid: str
    session_key: str
    episode_uids: list[str]
    status: SegmentStatus = SegmentStatus.ACTIVE

    # Heat scoring
    heat_score: HeatScore = field(default_factory=HeatScore)

    # Segment metadata
    topic: str = ""              # Inferred topic/theme
    summary: str | None = None   # Consolidated summary
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None

    # Emotional aggregation (from Phase 2)
    aggregate_emotion: dict[str, float] | None = None
    dominant_emotion: str | None = None

    # Configuration
    max_episodes: int = 20       # Max episodes per segment
    auto_close_hours: float = 2.0  # Auto-close after inactivity

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        """Validate segment."""
        if not self.uid:
            raise ValueError("Segment UID is required")
        if not self.session_key:
            raise ValueError("Session key is required")

    @property
    def episode_count(self) -> int:
        """Number of episodes in this segment."""
        return len(self.episode_uids)

    @property
    def is_full(self) -> bool:
        """Check if segment has reached max capacity."""
        return self.episode_count >= self.max_episodes

    @property
    def is_stale(self) -> bool:
        """Check if segment should be auto-closed due to inactivity."""
        if self.status != SegmentStatus.ACTIVE:
            return False

        now = datetime.now(timezone.utc)
        hours_since_update = (now - self.updated_at).total_seconds() / 3600.0
        return hours_since_update >= self.auto_close_hours

    @property
    def current_heat(self) -> float:
        """Get current heat score."""
        return self.heat_score.calculate()

    def add_episode(self, episode_uid: str, content_length: int = 0) -> bool:
        """Add an episode to this segment.

        Args:
            episode_uid: UID of the episode to add.
            content_length: Length of episode content for heat scoring.

        Returns:
            True if added, False if segment is full or closed.
        """
        if self.status != SegmentStatus.ACTIVE:
            return False

        if self.is_full:
            return False

        if episode_uid not in self.episode_uids:
            self.episode_uids.append(episode_uid)
            self.heat_score.record_access(content_length)
            self.updated_at = datetime.now(timezone.utc)

        return True

    def record_access(self, interaction_length: int = 0) -> None:
        """Record an access/retrieval of this segment."""
        self.heat_score.record_access(interaction_length)
        self.updated_at = datetime.now(timezone.utc)

    def close(self) -> None:
        """Close this segment, making it ready for promotion/eviction."""
        if self.status == SegmentStatus.ACTIVE:
            self.status = SegmentStatus.CLOSED
            self.end_time = datetime.now(timezone.utc)
            self.updated_at = datetime.now(timezone.utc)

    def promote(self) -> None:
        """Mark this segment as promoted to long-term memory."""
        self.status = SegmentStatus.PROMOTED
        self.updated_at = datetime.now(timezone.utc)

    def evict(self) -> None:
        """Mark this segment as evicted (forgotten)."""
        self.status = SegmentStatus.EVICTED
        self.updated_at = datetime.now(timezone.utc)

    def should_promote(self, threshold: float = 5.0) -> bool:
        """Check if segment should be promoted based on heat score.

        Args:
            threshold: Minimum heat score for promotion.

        Returns:
            True if segment should be promoted.
        """
        return self.status == SegmentStatus.CLOSED and self.current_heat >= threshold

    def should_evict(self, threshold: float = 0.5) -> bool:
        """Check if segment should be evicted based on heat score.

        Args:
            threshold: Maximum heat score for eviction.

        Returns:
            True if segment should be evicted.
        """
        return self.status == SegmentStatus.CLOSED and self.current_heat < threshold

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "uid": self.uid,
            "session_key": self.session_key,
            "episode_uids": self.episode_uids,
            "status": self.status.value,
            "heat_score": self.heat_score.to_dict(),
            "topic": self.topic,
            "summary": self.summary,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "aggregate_emotion": self.aggregate_emotion,
            "dominant_emotion": self.dominant_emotion,
            "max_episodes": self.max_episodes,
            "auto_close_hours": self.auto_close_hours,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemorySegment":
        """Create from dictionary."""
        return cls(
            uid=data["uid"],
            session_key=data["session_key"],
            episode_uids=data["episode_uids"],
            status=SegmentStatus(data["status"]),
            heat_score=HeatScore.from_dict(data["heat_score"]),
            topic=data.get("topic", ""),
            summary=data.get("summary"),
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=(
                datetime.fromisoformat(data["end_time"])
                if data.get("end_time")
                else None
            ),
            aggregate_emotion=data.get("aggregate_emotion"),
            dominant_emotion=data.get("dominant_emotion"),
            max_episodes=data.get("max_episodes", 20),
            auto_close_hours=data.get("auto_close_hours", 2.0),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass
class PromotionResult:
    """Result of a segment promotion operation."""

    segment_uid: str
    success: bool
    episodes_promoted: int = 0
    ltm_entry_uid: str | None = None
    error_message: str | None = None
    processing_time_ms: float = 0.0


@dataclass
class EvictionResult:
    """Result of a segment eviction operation."""

    segment_uid: str
    success: bool
    episodes_evicted: int = 0
    reason: str = ""
    error_message: str | None = None


@dataclass
class MidTermMemoryStats:
    """Statistics for mid-term memory operations."""

    active_segments: int = 0
    closed_segments: int = 0
    promoted_segments: int = 0
    evicted_segments: int = 0
    total_episodes: int = 0
    average_heat: float = 0.0
    oldest_segment_age_hours: float = 0.0
    promotion_rate: float = 0.0  # Promotions / (Promotions + Evictions)
