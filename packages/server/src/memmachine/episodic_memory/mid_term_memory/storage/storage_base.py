"""Base storage interface for mid-term memory."""

from abc import ABC, abstractmethod
from datetime import datetime

from ..data_types import MemorySegment, SegmentStatus


class MidTermMemoryStorage(ABC):
    """Abstract base class for mid-term memory storage.

    Implementations can use SQL, Redis, or other backends.
    """

    @abstractmethod
    async def create_segment(self, segment: MemorySegment) -> None:
        """Create a new memory segment.

        Args:
            segment: The segment to create.

        Raises:
            ValueError: If segment with UID already exists.
        """
        ...

    @abstractmethod
    async def get_segment(self, uid: str) -> MemorySegment | None:
        """Get a segment by UID.

        Args:
            uid: The segment UID.

        Returns:
            The segment, or None if not found.
        """
        ...

    @abstractmethod
    async def update_segment(self, segment: MemorySegment) -> None:
        """Update an existing segment.

        Args:
            segment: The segment to update.

        Raises:
            ValueError: If segment does not exist.
        """
        ...

    @abstractmethod
    async def delete_segment(self, uid: str) -> bool:
        """Delete a segment.

        Args:
            uid: The segment UID.

        Returns:
            True if deleted, False if not found.
        """
        ...

    @abstractmethod
    async def get_segments_by_session(
        self,
        session_key: str,
        status: SegmentStatus | None = None,
    ) -> list[MemorySegment]:
        """Get all segments for a session.

        Args:
            session_key: The session key.
            status: Optional status filter.

        Returns:
            List of matching segments.
        """
        ...

    @abstractmethod
    async def get_active_segment(
        self,
        session_key: str,
    ) -> MemorySegment | None:
        """Get the active (current) segment for a session.

        Args:
            session_key: The session key.

        Returns:
            The active segment, or None if no active segment.
        """
        ...

    @abstractmethod
    async def get_segments_by_status(
        self,
        status: SegmentStatus,
        limit: int = 100,
    ) -> list[MemorySegment]:
        """Get segments by status.

        Args:
            status: The status to filter by.
            limit: Maximum number of segments to return.

        Returns:
            List of matching segments.
        """
        ...

    @abstractmethod
    async def get_stale_segments(
        self,
        stale_hours: float = 2.0,
        limit: int = 100,
    ) -> list[MemorySegment]:
        """Get active segments that haven't been updated recently.

        Args:
            stale_hours: Hours since last update to consider stale.
            limit: Maximum number of segments to return.

        Returns:
            List of stale segments.
        """
        ...

    @abstractmethod
    async def get_segments_for_promotion(
        self,
        heat_threshold: float = 5.0,
        limit: int = 50,
    ) -> list[MemorySegment]:
        """Get closed segments ready for promotion.

        Args:
            heat_threshold: Minimum heat score for promotion.
            limit: Maximum number of segments to return.

        Returns:
            List of segments eligible for promotion.
        """
        ...

    @abstractmethod
    async def get_segments_for_eviction(
        self,
        heat_threshold: float = 0.5,
        limit: int = 50,
    ) -> list[MemorySegment]:
        """Get closed segments ready for eviction.

        Args:
            heat_threshold: Maximum heat score for eviction.
            limit: Maximum number of segments to return.

        Returns:
            List of segments eligible for eviction.
        """
        ...

    @abstractmethod
    async def count_segments_by_status(self) -> dict[SegmentStatus, int]:
        """Count segments by status.

        Returns:
            Dictionary mapping status to count.
        """
        ...

    @abstractmethod
    async def get_segment_containing_episode(
        self,
        episode_uid: str,
    ) -> MemorySegment | None:
        """Find segment containing a specific episode.

        Args:
            episode_uid: The episode UID.

        Returns:
            The segment containing the episode, or None.
        """
        ...

    @abstractmethod
    async def clear_session(self, session_key: str) -> int:
        """Delete all segments for a session.

        Args:
            session_key: The session key.

        Returns:
            Number of segments deleted.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the storage connection."""
        ...
