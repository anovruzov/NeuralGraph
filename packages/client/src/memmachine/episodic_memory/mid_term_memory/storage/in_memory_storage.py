"""In-memory storage implementation for mid-term memory."""

import logging
from datetime import datetime, timezone

from ..data_types import MemorySegment, SegmentStatus
from .storage_base import MidTermMemoryStorage

logger = logging.getLogger(__name__)


class InMemoryMidTermMemoryStorage(MidTermMemoryStorage):
    """In-memory implementation of mid-term memory storage.

    Suitable for development, testing, and single-instance deployments.
    Data is not persisted across restarts.
    """

    def __init__(self, max_segments: int = 1000):
        """Initialize in-memory storage.

        Args:
            max_segments: Maximum number of segments to store.
        """
        self._segments: dict[str, MemorySegment] = {}
        self._max_segments = max_segments
        self._episode_index: dict[str, str] = {}  # episode_uid -> segment_uid

    async def create_segment(self, segment: MemorySegment) -> None:
        """Create a new memory segment."""
        if segment.uid in self._segments:
            raise ValueError(f"Segment {segment.uid} already exists")

        # Enforce max segments limit
        if len(self._segments) >= self._max_segments:
            await self._evict_oldest_evicted()

        self._segments[segment.uid] = segment

        # Update episode index
        for ep_uid in segment.episode_uids:
            self._episode_index[ep_uid] = segment.uid

        logger.debug(f"Created segment {segment.uid} for session {segment.session_key}")

    async def _evict_oldest_evicted(self) -> None:
        """Remove oldest evicted segments to make room."""
        evicted = [
            s for s in self._segments.values()
            if s.status == SegmentStatus.EVICTED
        ]

        if not evicted:
            # No evicted segments, remove oldest promoted
            promoted = [
                s for s in self._segments.values()
                if s.status == SegmentStatus.PROMOTED
            ]
            if not promoted:
                return
            evicted = promoted

        # Sort by updated_at (oldest first)
        evicted.sort(key=lambda s: s.updated_at)

        # Remove oldest
        oldest = evicted[0]
        await self.delete_segment(oldest.uid)

    async def get_segment(self, uid: str) -> MemorySegment | None:
        """Get a segment by UID."""
        return self._segments.get(uid)

    async def update_segment(self, segment: MemorySegment) -> None:
        """Update an existing segment."""
        if segment.uid not in self._segments:
            raise ValueError(f"Segment {segment.uid} does not exist")

        old_segment = self._segments[segment.uid]

        # Update episode index for removed episodes
        for ep_uid in old_segment.episode_uids:
            if ep_uid not in segment.episode_uids:
                del self._episode_index[ep_uid]

        # Update episode index for new episodes
        for ep_uid in segment.episode_uids:
            self._episode_index[ep_uid] = segment.uid

        self._segments[segment.uid] = segment

    async def delete_segment(self, uid: str) -> bool:
        """Delete a segment."""
        if uid not in self._segments:
            return False

        segment = self._segments[uid]

        # Remove from episode index
        for ep_uid in segment.episode_uids:
            if ep_uid in self._episode_index:
                del self._episode_index[ep_uid]

        del self._segments[uid]
        return True

    async def get_segments_by_session(
        self,
        session_key: str,
        status: SegmentStatus | None = None,
    ) -> list[MemorySegment]:
        """Get all segments for a session."""
        segments = [
            s for s in self._segments.values()
            if s.session_key == session_key
        ]

        if status is not None:
            segments = [s for s in segments if s.status == status]

        return sorted(segments, key=lambda s: s.created_at, reverse=True)

    async def get_active_segment(
        self,
        session_key: str,
    ) -> MemorySegment | None:
        """Get the active (current) segment for a session."""
        for segment in self._segments.values():
            if (
                segment.session_key == session_key
                and segment.status == SegmentStatus.ACTIVE
            ):
                return segment
        return None

    async def get_segments_by_status(
        self,
        status: SegmentStatus,
        limit: int = 100,
    ) -> list[MemorySegment]:
        """Get segments by status."""
        segments = [
            s for s in self._segments.values()
            if s.status == status
        ]

        # Sort by heat score descending
        segments.sort(key=lambda s: s.current_heat, reverse=True)

        return segments[:limit]

    async def get_stale_segments(
        self,
        stale_hours: float = 2.0,
        limit: int = 100,
    ) -> list[MemorySegment]:
        """Get active segments that haven't been updated recently."""
        now = datetime.now(timezone.utc)
        stale_seconds = stale_hours * 3600.0

        segments = [
            s for s in self._segments.values()
            if s.status == SegmentStatus.ACTIVE
            and (now - s.updated_at).total_seconds() >= stale_seconds
        ]

        # Sort by oldest first
        segments.sort(key=lambda s: s.updated_at)

        return segments[:limit]

    async def get_segments_for_promotion(
        self,
        heat_threshold: float = 5.0,
        limit: int = 50,
    ) -> list[MemorySegment]:
        """Get closed segments ready for promotion."""
        segments = [
            s for s in self._segments.values()
            if s.status == SegmentStatus.CLOSED
            and s.current_heat >= heat_threshold
        ]

        # Sort by heat score descending (highest priority first)
        segments.sort(key=lambda s: s.current_heat, reverse=True)

        return segments[:limit]

    async def get_segments_for_eviction(
        self,
        heat_threshold: float = 0.5,
        limit: int = 50,
    ) -> list[MemorySegment]:
        """Get closed segments ready for eviction."""
        segments = [
            s for s in self._segments.values()
            if s.status == SegmentStatus.CLOSED
            and s.current_heat < heat_threshold
        ]

        # Sort by heat score ascending (lowest priority first)
        segments.sort(key=lambda s: s.current_heat)

        return segments[:limit]

    async def count_segments_by_status(self) -> dict[SegmentStatus, int]:
        """Count segments by status."""
        counts = {status: 0 for status in SegmentStatus}

        for segment in self._segments.values():
            counts[segment.status] += 1

        return counts

    async def get_segment_containing_episode(
        self,
        episode_uid: str,
    ) -> MemorySegment | None:
        """Find segment containing a specific episode."""
        segment_uid = self._episode_index.get(episode_uid)
        if segment_uid is None:
            return None
        return self._segments.get(segment_uid)

    async def clear_session(self, session_key: str) -> int:
        """Delete all segments for a session."""
        to_delete = [
            s.uid for s in self._segments.values()
            if s.session_key == session_key
        ]

        for uid in to_delete:
            await self.delete_segment(uid)

        return len(to_delete)

    async def close(self) -> None:
        """Close the storage connection."""
        # No-op for in-memory storage
        pass

    # Additional utility methods

    @property
    def segment_count(self) -> int:
        """Total number of segments."""
        return len(self._segments)

    @property
    def episode_count(self) -> int:
        """Total number of indexed episodes."""
        return len(self._episode_index)
