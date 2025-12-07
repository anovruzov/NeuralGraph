"""Data structures for declarative episodic memory entries."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import JsonValue

from memmachine.common.data_types import FilterablePropertyValue


class ContentType(Enum):
    """Types of content stored in declarative memory."""

    MESSAGE = "message"
    TEXT = "text"


@dataclass(kw_only=True)
class TimestampMetadata:
    """Structured timestamp metadata for improved temporal retrieval."""

    date_str: str  # ISO format date string (e.g., "2024-12-06")
    day_of_week: str  # e.g., "Monday", "Tuesday"
    month_name: str  # e.g., "December", "January"
    year: int
    day: int
    month: int
    event_id: str | None = None  # Optional event identifier
    message_id: str | None = None  # Optional message identifier

    @classmethod
    def from_datetime(
        cls,
        dt: datetime,
        event_id: str | None = None,
        message_id: str | None = None,
    ) -> "TimestampMetadata":
        """Create TimestampMetadata from a datetime object."""
        return cls(
            date_str=dt.strftime("%Y-%m-%d"),
            day_of_week=dt.strftime("%A"),
            month_name=dt.strftime("%B"),
            year=dt.year,
            day=dt.day,
            month=dt.month,
            event_id=event_id,
            message_id=message_id,
        )

    def to_searchable_tokens(self) -> list[str]:
        """Generate searchable date tokens for BM25 matching."""
        tokens = [
            self.date_str,  # "2024-12-06"
            self.day_of_week,  # "Monday"
            self.day_of_week.lower(),  # "monday"
            self.month_name,  # "December"
            self.month_name.lower(),  # "december"
            str(self.year),  # "2024"
            str(self.day),  # "6"
            f"{self.day:02d}",  # "06"
            f"{self.month_name} {self.day}",  # "December 6"
            f"{self.month_name} {self.day:02d}",  # "December 06"
            f"{self.day} {self.month_name}",  # "6 December"
            f"{self.day_of_week}, {self.month_name} {self.day}",  # "Monday, December 6"
        ]
        return tokens


@dataclass(kw_only=True)
class Episode:
    """A single episodic memory entry."""

    uid: str
    timestamp: datetime
    source: str
    content_type: ContentType
    content: Any
    filterable_properties: dict[str, FilterablePropertyValue] = field(
        default_factory=dict,
    )
    user_metadata: JsonValue = None
    timestamp_metadata: TimestampMetadata | None = None  # Structured timestamp for retrieval

    def __eq__(self, other: object) -> bool:
        """Compare episodes by UID."""
        if not isinstance(other, Episode):
            return False
        return (
            self.uid == other.uid
            and self.timestamp == other.timestamp
            and self.source == other.source
            and self.content_type == other.content_type
            and self.content == other.content
            and self.filterable_properties == other.filterable_properties
            and self.user_metadata == other.user_metadata
            and self.timestamp_metadata == other.timestamp_metadata
        )

    def __hash__(self) -> int:
        """Hash an episode by its UID."""
        return hash(self.uid)


@dataclass(kw_only=True)
class Derivative:
    """A derived episodic memory linked to a source episode."""

    uid: str
    timestamp: datetime
    source: str
    content_type: ContentType
    content: Any
    filterable_properties: dict[str, FilterablePropertyValue] = field(
        default_factory=dict,
    )
    timestamp_metadata: TimestampMetadata | None = None  # Structured timestamp for retrieval

    def __eq__(self, other: object) -> bool:
        """Compare derivatives by UID."""
        if not isinstance(other, Derivative):
            return False
        return (
            self.uid == other.uid
            and self.timestamp == other.timestamp
            and self.source == other.source
            and self.content_type == other.content_type
            and self.content == other.content
            and self.filterable_properties == other.filterable_properties
            and self.timestamp_metadata == other.timestamp_metadata
        )

    def __hash__(self) -> int:
        """Hash a derivative by its UID."""
        return hash(self.uid)


_MANGLE_FILTERABLE_PROPERTY_KEY_PREFIX = "filterable_"


def mangle_filterable_property_key(key: str) -> str:
    """Prefix filterable property keys with the mangling token."""
    return _MANGLE_FILTERABLE_PROPERTY_KEY_PREFIX + key


def demangle_filterable_property_key(mangled_key: str) -> str:
    """Remove the mangling prefix from a filterable property key."""
    return mangled_key.removeprefix(_MANGLE_FILTERABLE_PROPERTY_KEY_PREFIX)


def is_mangled_filterable_property_key(candidate_key: str) -> bool:
    """Check whether the provided key contains the mangling prefix."""
    return candidate_key.startswith(_MANGLE_FILTERABLE_PROPERTY_KEY_PREFIX)
