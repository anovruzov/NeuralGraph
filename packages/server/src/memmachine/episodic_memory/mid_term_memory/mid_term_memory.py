"""Mid-term memory implementation (MemoryOS three-tier architecture).

Manages memory segments with heat-based lifecycle for consolidation
into long-term memory or eviction (forgetting).
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from memmachine.common.episode_store import Episode

from .data_types import (
    EvictionResult,
    HeatScore,
    MemorySegment,
    MidTermMemoryStats,
    PromotionResult,
    SegmentStatus,
)
from .storage import InMemoryMidTermMemoryStorage, MidTermMemoryStorage

if TYPE_CHECKING:
    from memmachine.episodic_memory.long_term_memory.long_term_memory import (
        LongTermMemory,
    )

logger = logging.getLogger(__name__)


class MidTermMemoryConfig:
    """Configuration for mid-term memory."""

    def __init__(
        self,
        max_segments_per_session: int = 50,
        max_episodes_per_segment: int = 20,
        auto_close_hours: float = 2.0,
        promotion_threshold: float = 5.0,
        eviction_threshold: float = 0.5,
        heat_alpha: float = 1.0,
        heat_beta: float = 0.1,
        heat_gamma: float = 2.0,
        heat_strength: float = 30.0,
        background_task_interval_seconds: float = 60.0,
    ):
        """Initialize configuration.

        Args:
            max_segments_per_session: Maximum segments per session.
            max_episodes_per_segment: Maximum episodes per segment.
            auto_close_hours: Hours of inactivity before auto-closing.
            promotion_threshold: Minimum heat score for promotion.
            eviction_threshold: Maximum heat score for eviction.
            heat_alpha: Visit count weight.
            heat_beta: Interaction length weight.
            heat_gamma: Recency weight.
            heat_strength: Forgetting curve strength (days).
            background_task_interval_seconds: Background task interval.
        """
        self.max_segments_per_session = max_segments_per_session
        self.max_episodes_per_segment = max_episodes_per_segment
        self.auto_close_hours = auto_close_hours
        self.promotion_threshold = promotion_threshold
        self.eviction_threshold = eviction_threshold
        self.heat_alpha = heat_alpha
        self.heat_beta = heat_beta
        self.heat_gamma = heat_gamma
        self.heat_strength = heat_strength
        self.background_task_interval_seconds = background_task_interval_seconds


class MidTermMemory:
    """Mid-term memory manager for the three-tier memory hierarchy.

    Manages memory segments between short-term and long-term memory,
    using heat-based scoring for consolidation decisions.
    """

    def __init__(
        self,
        session_key: str,
        storage: MidTermMemoryStorage | None = None,
        long_term_memory: "LongTermMemory | None" = None,
        config: MidTermMemoryConfig | None = None,
    ):
        """Initialize mid-term memory.

        Args:
            session_key: The session key for this memory instance.
            storage: Storage backend. Uses in-memory if None.
            long_term_memory: Long-term memory for promotion.
            config: Configuration options.
        """
        self._session_key = session_key
        self._storage = storage or InMemoryMidTermMemoryStorage()
        self._ltm = long_term_memory
        self._config = config or MidTermMemoryConfig()

        self._background_task: asyncio.Task | None = None
        self._closed = False

        # Statistics
        self._promotions = 0
        self._evictions = 0

    @property
    def session_key(self) -> str:
        """Get the session key."""
        return self._session_key

    async def add_episode(self, episode: Episode) -> MemorySegment:
        """Add an episode to mid-term memory.

        Creates or uses existing active segment for the session.

        Args:
            episode: The episode to add.

        Returns:
            The segment the episode was added to.
        """
        # Get or create active segment
        segment = await self._storage.get_active_segment(self._session_key)

        if segment is None or segment.is_full:
            # Close current segment if exists
            if segment is not None:
                segment.close()
                await self._storage.update_segment(segment)

            # Create new segment
            segment = await self._create_segment()

        # Add episode to segment
        segment.add_episode(episode.uid, len(episode.content))
        await self._storage.update_segment(segment)

        logger.debug(
            f"Added episode {episode.uid} to segment {segment.uid} "
            f"(heat={segment.current_heat:.2f})"
        )

        return segment

    async def add_episodes(self, episodes: list[Episode]) -> list[MemorySegment]:
        """Add multiple episodes to mid-term memory.

        Args:
            episodes: The episodes to add.

        Returns:
            List of segments the episodes were added to.
        """
        segments = []
        for episode in episodes:
            segment = await self.add_episode(episode)
            if segment not in segments:
                segments.append(segment)
        return segments

    async def _create_segment(self) -> MemorySegment:
        """Create a new memory segment."""
        segment = MemorySegment(
            uid=str(uuid.uuid4()),
            session_key=self._session_key,
            episode_uids=[],
            status=SegmentStatus.ACTIVE,
            heat_score=HeatScore(
                alpha=self._config.heat_alpha,
                beta=self._config.heat_beta,
                gamma=self._config.heat_gamma,
                strength=self._config.heat_strength,
            ),
            max_episodes=self._config.max_episodes_per_segment,
            auto_close_hours=self._config.auto_close_hours,
        )

        await self._storage.create_segment(segment)
        return segment

    async def get_segment(self, uid: str) -> MemorySegment | None:
        """Get a segment by UID."""
        return await self._storage.get_segment(uid)

    async def get_active_segment(self) -> MemorySegment | None:
        """Get the active segment for this session."""
        return await self._storage.get_active_segment(self._session_key)

    async def get_all_segments(
        self,
        status: SegmentStatus | None = None,
    ) -> list[MemorySegment]:
        """Get all segments for this session."""
        return await self._storage.get_segments_by_session(
            self._session_key,
            status=status,
        )

    async def record_access(
        self,
        episode_uid: str,
        interaction_length: int = 0,
    ) -> None:
        """Record an access to an episode for heat scoring.

        Args:
            episode_uid: The episode that was accessed.
            interaction_length: Length of the interaction.
        """
        segment = await self._storage.get_segment_containing_episode(episode_uid)
        if segment is not None:
            segment.record_access(interaction_length)
            await self._storage.update_segment(segment)

    async def close_stale_segments(self) -> list[MemorySegment]:
        """Close segments that have been inactive too long.

        Returns:
            List of segments that were closed.
        """
        stale = await self._storage.get_stale_segments(
            stale_hours=self._config.auto_close_hours,
        )

        for segment in stale:
            segment.close()
            await self._storage.update_segment(segment)
            logger.info(f"Closed stale segment {segment.uid}")

        return stale

    async def promote_segment(
        self,
        segment: MemorySegment,
    ) -> PromotionResult:
        """Promote a segment to long-term memory.

        Args:
            segment: The segment to promote.

        Returns:
            PromotionResult with operation details.
        """
        if segment.status != SegmentStatus.CLOSED:
            return PromotionResult(
                segment_uid=segment.uid,
                success=False,
                error_message="Segment must be closed before promotion",
            )

        if self._ltm is None:
            return PromotionResult(
                segment_uid=segment.uid,
                success=False,
                error_message="Long-term memory not configured",
            )

        try:
            # Promotion would involve:
            # 1. Retrieve all episodes in the segment
            # 2. Consolidate/summarize them
            # 3. Store in LTM
            # For now, we just mark as promoted

            segment.promote()
            await self._storage.update_segment(segment)

            self._promotions += 1

            logger.info(
                f"Promoted segment {segment.uid} with {segment.episode_count} episodes "
                f"(heat={segment.current_heat:.2f})"
            )

            return PromotionResult(
                segment_uid=segment.uid,
                success=True,
                episodes_promoted=segment.episode_count,
            )

        except Exception as e:
            logger.error(f"Failed to promote segment {segment.uid}: {e}")
            return PromotionResult(
                segment_uid=segment.uid,
                success=False,
                error_message=str(e),
            )

    async def evict_segment(
        self,
        segment: MemorySegment,
        reason: str = "low_heat",
    ) -> EvictionResult:
        """Evict (forget) a segment.

        Args:
            segment: The segment to evict.
            reason: Reason for eviction.

        Returns:
            EvictionResult with operation details.
        """
        if segment.status != SegmentStatus.CLOSED:
            return EvictionResult(
                segment_uid=segment.uid,
                success=False,
                error_message="Segment must be closed before eviction",
            )

        try:
            episode_count = segment.episode_count

            segment.evict()
            await self._storage.update_segment(segment)

            self._evictions += 1

            logger.info(
                f"Evicted segment {segment.uid} with {episode_count} episodes "
                f"(heat={segment.current_heat:.2f}, reason={reason})"
            )

            return EvictionResult(
                segment_uid=segment.uid,
                success=True,
                episodes_evicted=episode_count,
                reason=reason,
            )

        except Exception as e:
            logger.error(f"Failed to evict segment {segment.uid}: {e}")
            return EvictionResult(
                segment_uid=segment.uid,
                success=False,
                error_message=str(e),
            )

    async def process_lifecycle(self) -> dict[str, Any]:
        """Process segment lifecycle (close stale, promote/evict).

        Returns:
            Summary of actions taken.
        """
        results = {
            "closed": 0,
            "promoted": 0,
            "evicted": 0,
        }

        # Close stale segments
        closed = await self.close_stale_segments()
        results["closed"] = len(closed)

        # Get segments for promotion
        for_promotion = await self._storage.get_segments_for_promotion(
            heat_threshold=self._config.promotion_threshold,
        )

        for segment in for_promotion:
            result = await self.promote_segment(segment)
            if result.success:
                results["promoted"] += 1

        # Get segments for eviction
        for_eviction = await self._storage.get_segments_for_eviction(
            heat_threshold=self._config.eviction_threshold,
        )

        for segment in for_eviction:
            result = await self.evict_segment(segment, reason="low_heat")
            if result.success:
                results["evicted"] += 1

        return results

    async def get_statistics(self) -> MidTermMemoryStats:
        """Get mid-term memory statistics."""
        counts = await self._storage.count_segments_by_status()

        # Calculate average heat for closed segments
        closed = await self._storage.get_segments_by_status(SegmentStatus.CLOSED)
        avg_heat = (
            sum(s.current_heat for s in closed) / len(closed)
            if closed
            else 0.0
        )

        # Calculate oldest segment age
        all_segments = await self.get_all_segments()
        oldest_age = 0.0
        if all_segments:
            now = datetime.now(timezone.utc)
            oldest = min(all_segments, key=lambda s: s.created_at)
            oldest_age = (now - oldest.created_at).total_seconds() / 3600.0

        # Calculate promotion rate
        total_decisions = self._promotions + self._evictions
        promotion_rate = (
            self._promotions / total_decisions
            if total_decisions > 0
            else 0.0
        )

        total_episodes = sum(s.episode_count for s in all_segments)

        return MidTermMemoryStats(
            active_segments=counts.get(SegmentStatus.ACTIVE, 0),
            closed_segments=counts.get(SegmentStatus.CLOSED, 0),
            promoted_segments=counts.get(SegmentStatus.PROMOTED, 0),
            evicted_segments=counts.get(SegmentStatus.EVICTED, 0),
            total_episodes=total_episodes,
            average_heat=avg_heat,
            oldest_segment_age_hours=oldest_age,
            promotion_rate=promotion_rate,
        )

    async def start_background_task(self) -> None:
        """Start background task for periodic lifecycle processing."""
        if self._background_task is not None:
            return

        self._background_task = asyncio.create_task(self._background_loop())
        logger.info(f"Started MTM background task for session {self._session_key}")

    async def stop_background_task(self) -> None:
        """Stop background task."""
        if self._background_task is not None:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
            self._background_task = None
            logger.info(f"Stopped MTM background task for session {self._session_key}")

    async def _background_loop(self) -> None:
        """Background loop for periodic lifecycle processing."""
        while not self._closed:
            try:
                await asyncio.sleep(self._config.background_task_interval_seconds)
                results = await self.process_lifecycle()
                if any(v > 0 for v in results.values()):
                    logger.debug(f"MTM lifecycle: {results}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in MTM background loop: {e}")

    async def clear(self) -> int:
        """Clear all segments for this session.

        Returns:
            Number of segments cleared.
        """
        return await self._storage.clear_session(self._session_key)

    async def close(self) -> None:
        """Close mid-term memory."""
        self._closed = True
        await self.stop_background_task()
        await self._storage.close()
