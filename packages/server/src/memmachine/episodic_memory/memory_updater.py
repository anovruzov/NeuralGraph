"""
Memory Updater (Sino-Slavic-Iranian Architecture - Phase 8)

Asynchronous memory consolidation (Chinese MemoryOS Updater pattern).

This module implements the background consolidation process that:
1. Extracts new factual triples from recent episodes
2. Generates reflective insights from fact patterns
3. Validates insights using RATE (Iranian)
4. Updates heat scores and promotes/evicts from MTM

The Updater acts as the "Observer" in control theory terms (Sharif University),
estimating the true state based on noisy measurements (dialogue).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .mid_term_memory.mid_term_memory import MidTermMemory
from .mid_term_memory.data_types import MemorySegment, SegmentStatus

logger = logging.getLogger(__name__)


@dataclass
class ConsolidationStats:
    """Statistics from a consolidation cycle."""
    cycle_number: int
    start_time: datetime
    end_time: datetime
    facts_extracted: int = 0
    insights_generated: int = 0
    insights_validated: int = 0
    segments_promoted: int = 0
    segments_evicted: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()


class MemoryUpdater:
    """Asynchronous memory consolidation (Chinese MemoryOS Updater pattern).

    The Updater runs in the background to:
    1. Extract facts from recent episodes -> FactualMemoryStore
    2. Generate insights from fact patterns -> ReflectiveMemoryStore
    3. Validate insights using RATE -> Only commit validated
    4. Update heat scores -> Promote/evict from MTM

    This implements the control theory perspective from Sharif University:
    - Memory is the "state of the plant"
    - Updater is the "Observer" (like a Kalman Filter)
    - Estimates true state from noisy dialogue measurements

    Key features:
    - Runs asynchronously in background
    - Consolidates related memories
    - Applies RATE validation before committing insights
    - Updates heat scores with importance signals
    """

    def __init__(
        self,
        mid_term_memory: MidTermMemory,
        factual_store: Any = None,      # FactualMemoryStore
        reflective_store: Any = None,   # ReflectiveMemoryStore
        rate_validator: Any = None,     # RATEValidator
        knowledge_graph: Any = None,    # KnowledgeGraphService
        emotion_detector: Any = None,   # EmotionDetector
        domain_classifier: Any = None,  # DomainClassifier
        on_promotion: Optional[Callable[[MemorySegment], None]] = None,
        on_eviction: Optional[Callable[[MemorySegment], None]] = None,
    ):
        """Initialize the memory updater.

        Args:
            mid_term_memory: MTM instance to manage
            factual_store: Store for immutable facts
            reflective_store: Store for evolving insights
            rate_validator: RATE validator for insight validation
            knowledge_graph: KG service for entity extraction
            emotion_detector: Emotion detector for emotional intensity
            domain_classifier: Domain classifier for semantic importance
            on_promotion: Callback when segment is promoted
            on_eviction: Callback when segment is evicted
        """
        self._mtm = mid_term_memory
        self._factual = factual_store
        self._reflective = reflective_store
        self._validator = rate_validator
        self._kg = knowledge_graph
        self._emotion = emotion_detector
        self._domain = domain_classifier

        self._on_promotion = on_promotion
        self._on_eviction = on_eviction

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._cycle_count = 0
        self._stats_history: list[ConsolidationStats] = []

    @property
    def is_running(self) -> bool:
        """Check if updater is running."""
        return self._running

    @property
    def cycle_count(self) -> int:
        """Number of completed consolidation cycles."""
        return self._cycle_count

    async def start(self, interval_seconds: float = 60.0):
        """Start the background consolidation loop.

        Args:
            interval_seconds: Time between consolidation cycles
        """
        if self._running:
            logger.warning("MemoryUpdater already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._consolidation_loop(interval_seconds))
        logger.info(f"MemoryUpdater started (interval={interval_seconds}s)")

    async def stop(self):
        """Stop the background consolidation loop."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("MemoryUpdater stopped")

    async def _consolidation_loop(self, interval: float):
        """Background loop for memory consolidation."""
        while self._running:
            try:
                await asyncio.sleep(interval)

                if not self._running:
                    break

                await self.run_consolidation_cycle()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Consolidation cycle error: {e}")

    async def run_consolidation_cycle(self) -> ConsolidationStats:
        """Run one consolidation cycle.

        This performs:
        1. Extract facts from recent episodes
        2. Generate insights from patterns
        3. Validate insights using RATE
        4. Update importance signals
        5. Process segment lifecycle (promote/evict)

        Returns:
            ConsolidationStats with cycle results
        """
        self._cycle_count += 1
        start_time = datetime.now(timezone.utc)

        stats = ConsolidationStats(
            cycle_number=self._cycle_count,
            start_time=start_time,
            end_time=start_time,  # Will be updated
        )

        try:
            # Step 1: Extract new facts
            facts_count = await self._extract_new_facts()
            stats.facts_extracted = facts_count

            # Step 2: Generate insights
            insights_count = await self._generate_insights()
            stats.insights_generated = insights_count

            # Step 3: Validate pending insights
            validated_count = await self._validate_pending_insights()
            stats.insights_validated = validated_count

            # Step 4: Update importance signals on segments
            await self._update_segment_importance()

            # Step 5: Process segment lifecycle
            promoted, evicted = await self._process_segment_lifecycle()
            stats.segments_promoted = promoted
            stats.segments_evicted = evicted

        except Exception as e:
            stats.errors.append(str(e))
            logger.error(f"Consolidation cycle {self._cycle_count} error: {e}")

        stats.end_time = datetime.now(timezone.utc)
        self._stats_history.append(stats)

        logger.info(
            f"Consolidation cycle {self._cycle_count} complete: "
            f"facts={stats.facts_extracted}, insights={stats.insights_generated}, "
            f"validated={stats.insights_validated}, "
            f"promoted={stats.segments_promoted}, evicted={stats.segments_evicted}, "
            f"duration={stats.duration_seconds:.2f}s"
        )

        return stats

    async def _extract_new_facts(self) -> int:
        """Extract factual triples from recent episodes.

        Uses KnowledgeGraphService to extract entities and relations,
        then stores them in FactualMemoryStore.

        Returns:
            Number of facts extracted
        """
        if not self._kg or not self._factual:
            return 0

        count = 0

        # Get closed segments awaiting processing
        segments = await self._mtm.get_all_segments(status=SegmentStatus.CLOSED)

        for segment in segments[:10]:  # Limit per cycle
            try:
                # Get episode content (would need episode store access)
                # For now, use segment summary if available
                if segment.summary:
                    # Extract entities using KG service
                    result = await self._kg.extract(
                        segment.summary,
                        session_key=segment.session_key,
                    )

                    # Store as factual triples
                    for entity in result.entities:
                        for relation in result.relations:
                            if relation.source == entity.name:
                                triple, conflict = self._factual.store_fact(
                                    entity=entity.name,
                                    attribute=relation.type.value,
                                    value=relation.target,
                                    source_uid=segment.uid,
                                    confidence=relation.confidence,
                                )
                                if not conflict:
                                    count += 1

            except Exception as e:
                logger.warning(f"Fact extraction error for segment {segment.uid}: {e}")

        return count

    async def _generate_insights(self) -> int:
        """Generate reflective insights from fact patterns.

        Analyzes patterns in factual memory and generates
        higher-order insights for reflective memory.

        Returns:
            Number of insights generated
        """
        if not self._factual or not self._reflective:
            return 0

        count = 0

        # Get recent facts grouped by entity
        stats = self._factual.get_stats()

        if stats["total_facts"] < 5:
            return 0  # Need minimum facts to generate insights

        # Simple pattern: entity profiles
        # More sophisticated patterns would analyze behavior over time

        try:
            # Get all entities
            for entity in list(self._factual._entity_index.keys())[:5]:
                profile = self._factual.get_entity_profile(entity)

                if len(profile) >= 3:
                    # Generate insight about entity
                    insight_content = f"{entity.title()} profile: " + ", ".join(
                        f"{k}={v}" for k, v in list(profile.items())[:5]
                    )

                    # Store as reflective insight (OBSERVATION mode)
                    from ..reflective_memory import MemoryMode

                    await self._reflective.store_insight(
                        content=insight_content,
                        mode=MemoryMode.OBSERVATION,
                        supporting_facts=[
                            f.uid for f in self._factual.get_facts(entity=entity)
                        ],
                        confidence=0.7,
                    )
                    count += 1

        except Exception as e:
            logger.warning(f"Insight generation error: {e}")

        return count

    async def _validate_pending_insights(self) -> int:
        """Validate pending insights using RATE validator.

        Only validated insights are marked as trusted.

        Returns:
            Number of insights validated
        """
        if not self._validator or not self._reflective:
            return 0

        count = 0

        # Get unvalidated insights
        try:
            from ..reflective_memory import MemoryMode

            insights = self._reflective.get_inference_insights(
                limit=10,
                validated_only=False,
            )

            for insight in insights:
                if insight.is_validated:
                    continue

                # Gather evidence
                evidence = []
                for fact_uid in insight.supporting_facts:
                    fact = self._factual.get_fact_by_uid(fact_uid) if self._factual else None
                    if fact:
                        evidence.append(f"{fact.entity}: {fact.attribute}={fact.value}")

                # Validate using RATE
                result = await self._validator.validate_insight(
                    insight=insight.content,
                    evidence=evidence,
                )

                if result.is_valid:
                    self._reflective.mark_validated(insight.uid)
                    count += 1

        except Exception as e:
            logger.warning(f"Insight validation error: {e}")

        return count

    async def _update_segment_importance(self):
        """Update importance signals on segments.

        Uses cognitive modules to compute:
        - semantic_importance from domain classifier
        - emotional_intensity from emotion detector
        - entity_centrality from knowledge graph
        """
        segments = await self._mtm.get_all_segments(status=SegmentStatus.CLOSED)

        for segment in segments:
            try:
                # Compute semantic importance
                if self._domain and segment.summary:
                    # Would call domain classifier
                    semantic = 0.5  # Default

                    # Update heat score
                    segment.heat_score.update_importance(semantic=semantic)

                # Compute emotional intensity
                if self._emotion and segment.aggregate_emotion:
                    # Use aggregate emotion
                    intensities = list(segment.aggregate_emotion.values())
                    if intensities:
                        emotional = max(intensities)
                        segment.heat_score.update_importance(emotional=emotional)

                # Compute entity centrality
                if self._kg and segment.summary:
                    # Would analyze entity connections
                    entity = 0.3  # Default

                    segment.heat_score.update_importance(entity=entity)

                # Save updated segment
                await self._mtm._storage.update_segment(segment)

            except Exception as e:
                logger.warning(f"Importance update error for segment {segment.uid}: {e}")

    async def _process_segment_lifecycle(self) -> tuple[int, int]:
        """Process segment promotion and eviction.

        Returns:
            Tuple of (promoted_count, evicted_count)
        """
        promoted = 0
        evicted = 0

        segments = await self._mtm.get_all_segments(status=SegmentStatus.CLOSED)

        for segment in segments:
            if segment.should_promote():
                segment.promote()
                await self._mtm._storage.update_segment(segment)
                promoted += 1

                if self._on_promotion:
                    self._on_promotion(segment)

            elif segment.should_evict():
                segment.evict()
                await self._mtm._storage.update_segment(segment)
                evicted += 1

                if self._on_eviction:
                    self._on_eviction(segment)

        return promoted, evicted

    def get_stats(self) -> dict:
        """Get updater statistics."""
        return {
            "is_running": self._running,
            "cycle_count": self._cycle_count,
            "last_cycle": (
                self._stats_history[-1].to_dict()
                if self._stats_history
                else None
            ),
            "total_facts_extracted": sum(s.facts_extracted for s in self._stats_history),
            "total_insights_generated": sum(s.insights_generated for s in self._stats_history),
            "total_validated": sum(s.insights_validated for s in self._stats_history),
            "total_promoted": sum(s.segments_promoted for s in self._stats_history),
            "total_evicted": sum(s.segments_evicted for s in self._stats_history),
        }

    async def force_cycle(self) -> ConsolidationStats:
        """Force an immediate consolidation cycle.

        Useful for testing or when immediate consolidation is needed.

        Returns:
            ConsolidationStats from the cycle
        """
        return await self.run_consolidation_cycle()
