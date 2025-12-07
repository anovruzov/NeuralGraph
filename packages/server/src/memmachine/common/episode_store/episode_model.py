"""Data models for representing episodes and related enumerations."""

from enum import Enum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field, JsonValue

from memmachine.common.data_types import FilterablePropertyValue

EpisodeIdT = str


class ContentType(Enum):
    """Enumeration for the type of content within an Episode."""

    STRING = "string"
    # Other content types like 'vector', 'image' could be added here.


class EpisodeType(Enum):
    """Enumeration for the type of an Episode."""

    MESSAGE = "message"
    THOUGHT = "thought"   # Internal agent thoughts (Theory of Mind)
    ACTION = "action"     # Agent actions taken
    # Other episode types could be added here.


class SensitivityLevel(str, Enum):
    """Classification of privacy sensitivity for episodes."""

    NONE = "none"         # No PII detected
    LOW = "low"           # Low sensitivity PII (IP addresses)
    MEDIUM = "medium"     # Medium sensitivity (names, emails)
    HIGH = "high"         # High sensitivity (addresses, medical)
    CRITICAL = "critical" # Critical (SSN, credit cards) - should be blocked


class EpisodeEntry(BaseModel):
    """Payload used when creating a new episode entry."""

    content: str

    producer_id: str
    producer_role: str

    produced_for_id: str | None = None
    episode_type: EpisodeType | None = None
    metadata: dict[str, JsonValue] | None = None
    created_at: AwareDatetime | None = None


class EpisodeResponse(EpisodeEntry):
    """Episode data returned in responses."""

    uid: EpisodeIdT


class SegmentFoldInfo(BaseModel):
    """Lightweight segment fold information stored with episodes."""

    fold_id: str
    start_pos: int
    end_pos: int
    fold_type: str  # "redacted", "encrypted", "pseudonymized"
    pii_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "fold_id": self.fold_id,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "fold_type": self.fold_type,
            "pii_type": self.pii_type,
        }


class Episode(BaseModel):
    """Conversation message stored in history together with persistence metadata."""

    uid: EpisodeIdT
    content: str
    session_key: str
    created_at: AwareDatetime

    producer_id: str
    producer_role: str
    produced_for_id: str | None = None

    sequence_num: int = 0

    episode_type: EpisodeType = EpisodeType.MESSAGE
    content_type: ContentType = ContentType.STRING
    filterable_metadata: dict[str, FilterablePropertyValue] | None = None
    metadata: dict[str, JsonValue] | None = None

    # Privacy-related fields (Phase 1: Privacy Sanitization)
    sanitization_applied: bool = Field(
        default=False,
        description="Whether content was sanitized for PII",
    )
    pii_detected: bool = Field(
        default=False,
        description="Whether any PII was detected in original content",
    )
    sensitivity_level: SensitivityLevel = Field(
        default=SensitivityLevel.NONE,
        description="Highest sensitivity level of detected PII",
    )
    segment_folds: list[SegmentFoldInfo] | None = Field(
        default=None,
        description="Segment folds created during sanitization",
    )
    pii_types_detected: list[str] | None = Field(
        default=None,
        description="List of PII types detected (e.g., 'email', 'phone')",
    )
    original_content_hash: str | None = Field(
        default=None,
        description="SHA-256 hash of original content before sanitization",
    )

    # Emotional memory fields (Phase 2: Cognitive Weave - placeholder)
    emotional_vector: dict[str, float] | None = Field(
        default=None,
        description="8-dimensional emotional vector (Plutchik's wheel)",
    )
    dominant_emotion: str | None = Field(
        default=None,
        description="Dominant emotion for quick filtering",
    )

    def __hash__(self) -> int:
        """Hash an episode by its UID."""
        return hash(self.uid)

    @property
    def has_privacy_data(self) -> bool:
        """Check if episode has privacy-related data."""
        return self.sanitization_applied or self.pii_detected

    @property
    def is_sanitized(self) -> bool:
        """Check if episode content was sanitized."""
        return self.sanitization_applied and self.segment_folds is not None

    def get_segment_folds_dict(self) -> list[dict[str, Any]]:
        """Get segment folds as list of dicts for serialization."""
        if not self.segment_folds:
            return []
        return [fold.to_dict() for fold in self.segment_folds]
