"""
Defines the core memory instance for a specific conversational context.

This module provides the `EpisodicMemory` class, which acts as the primary
orchestrator for an individual memory session. It integrates short-term
(session) and long-term (declarative) memory stores to provide a unified
interface for adding and retrieving conversational data.

Key responsibilities include:
- Managing the lifecycle of the memory instance through reference counting.
- Adding new conversational `Episode` objects to both session and declarative
  memory.
- Retrieving relevant context for a query by searching both memory types.
- Interacting with a language model for memory-related tasks.
- Privacy sanitization of content before storage.
- Each instance is managed by the `EpisodicMemoryManager`.
"""

import asyncio
import datetime
import hashlib
import json
import logging
import time
from collections.abc import Coroutine, Iterable
from typing import TYPE_CHECKING, cast, get_args

from pydantic import BaseModel, Field, InstanceOf, model_validator

from memmachine.common.data_types import FilterablePropertyValue
from memmachine.common.episode_store import (
    Episode,
    EpisodeResponse,
    EpisodeType,
)
from memmachine.common.episode_store.episode_model import (
    SegmentFoldInfo,
    SensitivityLevel,
)
from memmachine.common.filter.filter_parser import (
    FilterExpr,
)
from memmachine.common.metrics_factory import MetricsFactory
from memmachine.episodic_memory.long_term_memory.long_term_memory import LongTermMemory
from memmachine.episodic_memory.mid_term_memory import (
    MidTermMemory,
    MidTermMemoryConfig,
)
from memmachine.episodic_memory.short_term_memory.short_term_memory import (
    ShortTermMemory,
)

# Temporal normalization - CRITICAL: Must be runtime import, not TYPE_CHECKING only
from memmachine.common.temporal_normalizer import TemporalNormalizer, TemporalContext

# Conditional imports to avoid circular dependencies
if TYPE_CHECKING:
    from memmachine.common.privacy_sanitizer import (
        ComplianceAuditor,
        PrivacySanitizer,
    )
    from memmachine.common.emotional_encoder import (
        EmotionDetector,
        MoodTracker,
        EmotionalResonanceScorer,
    )
    from memmachine.knowledge_graph import KnowledgeGraphService
    from memmachine.common.granularity_router import GranularityRouter, RoutingDecision
    from memmachine.common.domain_classifier import DomainClassifier, DomainClassification
    from memmachine.knowledge_graph.entity_verifier import VerificationResult
    from memmachine.neural_graph import NeuralGraphService

logger = logging.getLogger(__name__)


class EpisodicMemoryParams(BaseModel):
    """
    Parameters for configuring the EpisodicMemory.

    Attributes:
        session_key (str): The unique identifier for the session.
        metrics_factory (MetricsFactory): The metrics factory.
        long_term_memory (LongTermMemory): The long-term memory.
        short_term_memory (ShortTermMemory): The short-term memory.
        enabled (bool): Whether the episodic memory is enabled.
        privacy_sanitizer: Optional privacy sanitizer for PII detection.
        compliance_auditor: Optional compliance auditor for logging.
        privacy_enabled: Whether privacy sanitization is enabled.

    """

    session_key: str = Field(
        ...,
        min_length=1,
        description="The unique identifier for the session",
    )
    metrics_factory: InstanceOf[MetricsFactory] = Field(
        ...,
        description="The metrics factory",
    )
    long_term_memory: InstanceOf[LongTermMemory] | None = Field(
        default=None,
        description="The long-term memory",
    )
    short_term_memory: InstanceOf[ShortTermMemory] | None = Field(
        default=None,
        description="The short-term memory",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the episodic memory is enabled",
    )

    # Privacy sanitization (Phase 1: Cognitive Architecture)
    privacy_sanitizer: "PrivacySanitizer | None" = Field(
        default=None,
        description="Privacy sanitizer for PII detection and handling",
    )
    compliance_auditor: "ComplianceAuditor | None" = Field(
        default=None,
        description="Compliance auditor for logging privacy events",
    )
    privacy_enabled: bool = Field(
        default=False,
        description="Whether privacy sanitization is enabled",
    )

    # Emotional encoding (Phase 2: Cognitive Weave)
    emotion_detector: "EmotionDetector | None" = Field(
        default=None,
        description="Emotion detector for analyzing content emotions",
    )
    mood_tracker: "MoodTracker | None" = Field(
        default=None,
        description="Mood tracker for user emotional state",
    )
    resonance_scorer: "EmotionalResonanceScorer | None" = Field(
        default=None,
        description="Resonance scorer for mood-congruent retrieval",
    )
    emotional_encoding_enabled: bool = Field(
        default=False,
        description="Whether emotional encoding is enabled",
    )

    # Mid-term memory (Phase 3: Three-tier hierarchy)
    mid_term_memory_config: MidTermMemoryConfig | None = Field(
        default=None,
        description="Configuration for mid-term memory",
    )
    mid_term_memory_enabled: bool = Field(
        default=False,
        description="Whether mid-term memory tier is enabled",
    )

    # Knowledge Graph (Phase 4: MeKB architecture)
    knowledge_graph_service: "KnowledgeGraphService | None" = Field(
        default=None,
        description="Knowledge graph service for entity extraction and reasoning",
    )
    knowledge_graph_enabled: bool = Field(
        default=False,
        description="Whether knowledge graph processing is enabled",
    )

    # Granularity Router (Phase 5: MemGAS architecture)
    granularity_router: "GranularityRouter | None" = Field(
        default=None,
        description="Granularity router for adaptive retrieval",
    )
    granularity_routing_enabled: bool = Field(
        default=False,
        description="Whether granularity routing is enabled",
    )

    # Domain Classifier (Phase 6: Memory sharding)
    domain_classifier: "DomainClassifier | None" = Field(
        default=None,
        description="Domain classifier for memory sharding",
    )
    domain_classification_enabled: bool = Field(
        default=False,
        description="Whether domain classification is enabled",
    )

    # Temporal Normalization (CRITICAL for temporal queries)
    temporal_normalizer: TemporalNormalizer | None = Field(
        default=None,
        description="Temporal normalizer for resolving relative dates",
    )
    temporal_normalization_enabled: bool = Field(
        default=True,  # Enable by default - this is critical for temporal accuracy
        description="Whether temporal normalization is enabled",
    )

    # Neural Graph (Phase 7: Neural-inspired memory linking)
    neural_graph_service: "NeuralGraphService | None" = Field(
        default=None,
        description="Neural graph service for neural-inspired memory linking",
    )
    neural_graph_enabled: bool = Field(
        default=False,
        description="Whether neural graph processing is enabled",
    )

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def validate_memory_params(self) -> "EpisodicMemoryParams":
        if not self.enabled:
            return self
        if self.short_term_memory is None and self.long_term_memory is None:
            raise ValueError(
                "At least one of short_term_memory or long_term_memory must be provided.",
            )
        return self


class EpisodicMemory:
    """
    Represents a single, isolated memory instance for a specific session.

    This class orchestrates the interaction between short-term (session)
    memory and long-term (declarative) memory. It manages the lifecycle of
    the memory, handles adding new information (episodes), and provides
    methods to retrieve contextual information for queries.

    Each instance is tied to a unique session key
    """

    def __init__(
        self,
        params: EpisodicMemoryParams,
    ) -> None:
        """
        Initialize a EpisodicMemory instance.

        Args:
            params (EpisodicMemoryParams): Parameters for the EpisodicMemory.

        """
        self._closed = False

        self._session_key = params.session_key

        self._short_term_memory: ShortTermMemory | None = params.short_term_memory
        self._long_term_memory: LongTermMemory | None = params.long_term_memory

        # Privacy sanitization (Phase 1: Cognitive Architecture)
        self._privacy_sanitizer = params.privacy_sanitizer
        self._compliance_auditor = params.compliance_auditor
        self._privacy_enabled = params.privacy_enabled

        # Emotional encoding (Phase 2: Cognitive Weave)
        self._emotion_detector = params.emotion_detector
        self._mood_tracker = params.mood_tracker
        self._resonance_scorer = params.resonance_scorer
        self._emotional_encoding_enabled = params.emotional_encoding_enabled

        # Mid-term memory (Phase 3: Three-tier hierarchy)
        self._mid_term_memory: MidTermMemory | None = None
        self._mid_term_memory_enabled = params.mid_term_memory_enabled
        if self._mid_term_memory_enabled:
            mtm_config = params.mid_term_memory_config or MidTermMemoryConfig()
            self._mid_term_memory = MidTermMemory(
                session_key=params.session_key,
                long_term_memory=params.long_term_memory,
                config=mtm_config,
            )

        # Knowledge Graph (Phase 4: MeKB architecture)
        self._knowledge_graph_service = params.knowledge_graph_service
        self._knowledge_graph_enabled = params.knowledge_graph_enabled

        # Granularity Router (Phase 5: MemGAS architecture)
        self._granularity_router = params.granularity_router
        self._granularity_routing_enabled = params.granularity_routing_enabled

        # Domain Classifier (Phase 6: Memory sharding)
        self._domain_classifier = params.domain_classifier
        self._domain_classification_enabled = params.domain_classification_enabled

        # Temporal Normalization (CRITICAL for temporal queries)
        self._temporal_normalizer = params.temporal_normalizer or TemporalNormalizer()
        self._temporal_normalization_enabled = params.temporal_normalization_enabled

        # Neural Graph (Phase 7: Neural-inspired memory linking)
        self._neural_graph_service = params.neural_graph_service
        self._neural_graph_enabled = params.neural_graph_enabled

        self._enabled = params.enabled
        if not self._enabled:
            return
        if self._short_term_memory is None and self._long_term_memory is None:
            raise ValueError("No memory is configured")

        metrics_manager = params.metrics_factory
        # Initialize metrics
        self._ingestion_latency_summary = metrics_manager.get_summary(
            "Ingestion_latency",
            "Latency of Episode ingestion in milliseconds",
        )
        self._query_latency_summary = metrics_manager.get_summary(
            "query_latency",
            "Latency of query processing in milliseconds",
        )
        self._ingestion_counter = metrics_manager.get_counter(
            "Ingestion_count",
            "Count of Episode ingestion",
        )
        self._query_counter = metrics_manager.get_counter(
            "query_count",
            "Count of query processing",
        )
        # Privacy metrics
        self._sanitization_counter = metrics_manager.get_counter(
            "sanitization_count",
            "Count of episodes sanitized for PII",
        )
        self._blocked_counter = metrics_manager.get_counter(
            "blocked_count",
            "Count of episodes blocked due to critical PII",
        )
        # Emotional encoding metrics
        self._emotion_encoded_counter = metrics_manager.get_counter(
            "emotion_encoded_count",
            "Count of episodes with emotional encoding",
        )
        self._mood_updated_counter = metrics_manager.get_counter(
            "mood_updated_count",
            "Count of mood state updates",
        )
        # Mid-term memory metrics
        self._mtm_segment_counter = metrics_manager.get_counter(
            "mtm_segment_count",
            "Count of mid-term memory segments created",
        )
        self._mtm_promotion_counter = metrics_manager.get_counter(
            "mtm_promotion_count",
            "Count of segments promoted to LTM",
        )
        # Knowledge graph metrics
        self._kg_entity_counter = metrics_manager.get_counter(
            "kg_entity_count",
            "Count of entities extracted by knowledge graph",
        )
        self._kg_reasoning_counter = metrics_manager.get_counter(
            "kg_reasoning_count",
            "Count of knowledge graph reasoning queries",
        )
        # Granularity routing metrics
        self._granularity_routing_counter = metrics_manager.get_counter(
            "granularity_routing_count",
            "Count of granularity routing decisions",
        )
        # Domain classification metrics
        self._domain_classification_counter = metrics_manager.get_counter(
            "domain_classification_count",
            "Count of domain classifications performed",
        )
        # Temporal normalization metrics
        self._temporal_normalization_counter = metrics_manager.get_counter(
            "temporal_normalization_count",
            "Count of temporal normalizations performed",
        )
        # Neural graph metrics
        self._neural_graph_node_counter = metrics_manager.get_counter(
            "neural_graph_node_count",
            "Count of neural graph nodes created",
        )
        self._neural_graph_retrieval_counter = metrics_manager.get_counter(
            "neural_graph_retrieval_count",
            "Count of neural graph retrievals performed",
        )

    @property
    def short_term_memory(self) -> ShortTermMemory | None:
        return self._short_term_memory

    @short_term_memory.setter
    def short_term_memory(self, value: ShortTermMemory | None) -> None:
        self._short_term_memory = value

    @property
    def long_term_memory(self) -> LongTermMemory | None:
        return self._long_term_memory

    @long_term_memory.setter
    def long_term_memory(self, value: LongTermMemory | None) -> None:
        self._long_term_memory = value

    @property
    def mid_term_memory(self) -> MidTermMemory | None:
        return self._mid_term_memory

    @property
    def session_key(self) -> str:
        return self._session_key

    async def add_memory_episodes(self, episodes: list[Episode]) -> None:
        if not self._enabled:
            return
        start_time = time.monotonic_ns()

        if self._closed:
            raise RuntimeError(f"Memory is closed {self._session_key}")

        if self._privacy_enabled and self._privacy_sanitizer:
            episodes = await self._sanitize_episodes(episodes)
            if not episodes:
                logger.info(f"All episodes blocked by privacy sanitizer for {self._session_key}")
                return

        if self._emotional_encoding_enabled and self._emotion_detector:
            episodes = await self._encode_emotions(episodes)

        if self._domain_classification_enabled and self._domain_classifier:
            episodes = await self._classify_domain(episodes)

        for episode in episodes:
            if episode.metadata is not None and episode.filterable_metadata is None:
                episode.filterable_metadata = {}
                for key, value in episode.metadata.items():
                    if isinstance(value, get_args(FilterablePropertyValue)):
                        episode.filterable_metadata[key] = value

            if episode.pii_detected:
                if episode.filterable_metadata is None:
                    episode.filterable_metadata = {}
                episode.filterable_metadata["pii_detected"] = episode.pii_detected
                episode.filterable_metadata["sensitivity_level"] = episode.sensitivity_level.value

            if episode.dominant_emotion:
                if episode.filterable_metadata is None:
                    episode.filterable_metadata = {}
                episode.filterable_metadata["dominant_emotion"] = episode.dominant_emotion

        tasks: list[Coroutine] = []

        if self._short_term_memory:
            tasks.append(self._short_term_memory.add_episodes(episodes))

        if self._mid_term_memory_enabled and self._mid_term_memory:
            tasks.append(self._add_to_mid_term_memory(episodes))
        elif self._long_term_memory:
            tasks.append(self._long_term_memory.add_episodes(episodes))

        await asyncio.gather(*tasks)

        if self._knowledge_graph_enabled and self._knowledge_graph_service:
            await self._process_knowledge_graph(episodes)

        # Neural graph processing (Phase 7: Neural-inspired linking)
        if self._neural_graph_enabled and self._neural_graph_service:
            await self._process_neural_graph(episodes)

        end_time = time.monotonic_ns()
        delta = (end_time - start_time) / 1000000
        self._ingestion_latency_summary.observe(delta)
        self._ingestion_counter.increment()

        if self._compliance_auditor:
            pii_detected = any(ep.pii_detected for ep in episodes)
            sanitization_applied = any(ep.sanitization_applied for ep in episodes)
            await self._compliance_auditor.log_data_ingestion(
                session_key=self._session_key,
                episode_count=len(episodes),
                pii_detected=pii_detected,
                sanitization_applied=sanitization_applied,
            )

    async def _sanitize_episodes(self, episodes: list[Episode]) -> list[Episode]:
        sanitized_episodes: list[Episode] = []
        for episode in episodes:
            try:
                result = await self._privacy_sanitizer.sanitize(episode.content)
                if result.is_blocked:
                    logger.warning(f"Episode blocked due to critical PII: {result.blocking_reason}")
                    self._blocked_counter.increment()
                    if self._compliance_auditor:
                        await self._compliance_auditor.log_sanitization(result=result, session_key=self._session_key)
                    continue
                episode.content = result.sanitized_content
                episode.sanitization_applied = True
                episode.pii_detected = result.pii_detected
                episode.original_content_hash = hashlib.sha256(result.original_content.encode("utf-8")).hexdigest()
                if result.highest_sensitivity:
                    episode.sensitivity_level = SensitivityLevel(result.highest_sensitivity.value)
                if result.segment_folds:
                    episode.segment_folds = [
                        SegmentFoldInfo(fold_id=fold.fold_id, start_pos=fold.start_pos, end_pos=fold.end_pos, fold_type=fold.fold_type, pii_type=fold.pii_type.value if fold.pii_type else None)
                        for fold in result.segment_folds
                    ]
                if result.detections:
                    episode.pii_types_detected = list(set(d.pii_type.value for d in result.detections))
                self._sanitization_counter.increment()
                sanitized_episodes.append(episode)
                if self._compliance_auditor:
                    await self._compliance_auditor.log_sanitization(result=result, session_key=self._session_key)
            except Exception as e:
                logger.error(f"Privacy sanitization failed for episode: {e}")
                sanitized_episodes.append(episode)
        return sanitized_episodes

    async def _encode_emotions(self, episodes: list[Episode]) -> list[Episode]:
        for episode in episodes:
            try:
                result = await self._emotion_detector.detect(episode.content)
                episode.emotional_vector = result.emotional_vector.to_dict()
                if result.dominant_emotion:
                    episode.dominant_emotion = result.dominant_emotion.value
                self._emotion_encoded_counter.increment()
                if self._mood_tracker:
                    await self._mood_tracker.update_from_text(session_key=self._session_key, text=episode.content, source="episode_ingestion")
                    self._mood_updated_counter.increment()
                logger.debug(f"Encoded emotions for episode: dominant={episode.dominant_emotion}, vector_magnitude={result.emotional_vector.magnitude():.3f}")
            except Exception as e:
                logger.warning(f"Emotional encoding failed for episode: {e}")
                episode.emotional_vector = None
                episode.dominant_emotion = None
        return episodes

    async def _add_to_mid_term_memory(self, episodes: list[Episode]) -> None:
        if self._mid_term_memory is None:
            return
        try:
            segments = await self._mid_term_memory.add_episodes(episodes)
            self._mtm_segment_counter.increment(len(set(s.uid for s in segments)))
            if self._long_term_memory:
                await self._long_term_memory.add_episodes(episodes)
            logger.debug(f"Added {len(episodes)} episodes to MTM across {len(segments)} segment(s)")
        except Exception as e:
            logger.error(f"Failed to add episodes to MTM: {e}")
            if self._long_term_memory:
                await self._long_term_memory.add_episodes(episodes)

    async def _process_knowledge_graph(self, episodes: list[Episode]) -> None:
        if self._knowledge_graph_service is None:
            return
        for episode in episodes:
            try:
                result = await self._knowledge_graph_service.process_episode(episode=episode, session_key=self._session_key)
                self._kg_entity_counter.increment(len(result.entities))
                logger.debug(f"Processed KG for episode {episode.uid}: {len(result.entities)} entities, {len(result.relations)} relations")
            except Exception as e:
                logger.warning(f"Knowledge graph processing failed for episode: {e}")

    async def _classify_domain(self, episodes: list[Episode]) -> list[Episode]:
        if self._domain_classifier is None:
            return episodes
        for episode in episodes:
            try:
                classification = await self._domain_classifier.classify(episode.content)
                if episode.filterable_metadata is None:
                    episode.filterable_metadata = {}
                episode.filterable_metadata["domain"] = classification.domain.value
                episode.filterable_metadata["domain_confidence"] = classification.confidence
                self._domain_classification_counter.increment()
                logger.debug(f"Classified episode domain: {classification.domain.value} (confidence={classification.confidence:.3f})")
            except Exception as e:
                logger.warning(f"Domain classification failed for episode: {e}")
        return episodes

    async def _process_neural_graph(self, episodes: list[Episode]) -> None:
        """Process episodes through neural graph for neural-inspired linking.

        Creates Layer 0 message nodes, builds temporal chains, and optionally
        segments into episode clusters. Implements neural linking principles:
        - Hierarchical structure (Image 2)
        - Temporal chains with LTP (Image 4)
        - Distributed weighted connections (Image 1)
        """
        if self._neural_graph_service is None:
            return

        try:
            # Extract embeddings if available from episodes
            embeddings = []
            wave_amplitudes_list = []

            for episode in episodes:
                # Try to get embedding from episode metadata
                embedding = None
                wave_amps = {}

                if hasattr(episode, 'embedding') and episode.embedding:
                    embedding = episode.embedding
                elif episode.filterable_metadata and 'embedding' in episode.filterable_metadata:
                    embedding = episode.filterable_metadata.get('embedding')

                # Try to get wave amplitudes from metadata
                if episode.filterable_metadata:
                    wave_amps = {
                        k.replace('wave_', ''): v
                        for k, v in episode.filterable_metadata.items()
                        if k.startswith('wave_') and isinstance(v, (int, float))
                    }

                embeddings.append(embedding)
                wave_amplitudes_list.append(wave_amps)

            # Process through neural graph service
            result = await self._neural_graph_service.process_episodes(
                episodes=episodes,
                session_key=self._session_key,
                embeddings=embeddings if any(e is not None for e in embeddings) else None,
                wave_amplitudes_list=wave_amplitudes_list if any(w for w in wave_amplitudes_list) else None,
            )

            self._neural_graph_node_counter.increment(result.nodes_created)

            logger.debug(
                f"Neural graph processed {len(episodes)} episodes: "
                f"{result.nodes_created} nodes, {result.temporal_edges_created} temporal edges "
                f"in {result.processing_time_ms:.1f}ms"
            )

            if result.errors:
                for error in result.errors:
                    logger.warning(f"Neural graph processing error: {error}")

        except Exception as e:
            logger.warning(f"Neural graph processing failed: {e}")

    async def _apply_granularity_routing(self, query: str) -> "RoutingDecision | None":
        if self._granularity_router is None:
            return None
        try:
            decision = self._granularity_router.analyze_query(query)
            self._granularity_routing_counter.increment()
            logger.debug(f"Granularity routing: level={decision.selected_granularity.value}, entropy={decision.entropy_score.entropy:.3f}")
            return decision
        except Exception as e:
            logger.warning(f"Granularity routing failed: {e}")
            return None

    async def close(self) -> None:
        self._closed = True
        if not self._enabled:
            return
        tasks = []
        if self._short_term_memory:
            tasks.append(self._short_term_memory.close())
        if self._mid_term_memory:
            tasks.append(self._mid_term_memory.close())
        if self._long_term_memory:
            tasks.append(self._long_term_memory.close())
        if self._neural_graph_service:
            tasks.append(self._neural_graph_service.close())
        await asyncio.gather(*tasks)

    async def delete_episodes(self, uids: Iterable[str]) -> None:
        if not self._enabled:
            return
        uids = list(uids)
        delete_episodes_coroutines: list[Coroutine] = []
        if self._short_term_memory:
            delete_episodes_coroutines.extend(self._short_term_memory.delete_episode(uid) for uid in uids)
        if self._long_term_memory:
            delete_episodes_coroutines.append(self._long_term_memory.delete_episodes(uids))
        await asyncio.gather(*delete_episodes_coroutines)

    async def delete_session_episodes(self) -> None:
        if not self._enabled:
            return
        tasks = []
        if self._short_term_memory:
            tasks.append(self._short_term_memory.clear_memory())
        if self._long_term_memory:
            tasks.append(self._long_term_memory.delete_matching_episodes())
        await asyncio.gather(*tasks)

    class QueryResponse(BaseModel):
        class ShortTermMemoryResponse(BaseModel):
            episodes: list[EpisodeResponse]
            episode_summary: list[str]
        class LongTermMemoryResponse(BaseModel):
            episodes: list[EpisodeResponse]
        class EntityVerificationResponse(BaseModel):
            """Entity verification results for adversarial detection."""
            is_adversarial: bool = False
            entity_swaps: list[dict] = []
            corrections: list[str] = []
            explanation: str = ""
        class ReasoningResponse(BaseModel):
            """Knowledge graph reasoning results."""
            relevant_entities: list[str] = []
            reasoning_paths: list[str] = []
            explanation: str = ""
        class NeuralGraphResponse(BaseModel):
            """Neural graph retrieval results."""
            node_ids: list[str] = []
            node_scores: list[float] = []
            stages_executed: list[str] = []
            temporal_context: dict = {}
            total_time_ms: float = 0.0
        long_term_memory: LongTermMemoryResponse
        short_term_memory: ShortTermMemoryResponse
        entity_verification: EntityVerificationResponse | None = None
        reasoning: ReasoningResponse | None = None
        neural_graph: NeuralGraphResponse | None = None

    async def query_memory(self, query: str, limit: int | None = None, property_filter: FilterExpr | None = None) -> QueryResponse | None:
        if not self._enabled:
            return None
        start_time = time.monotonic_ns()
        search_limit = limit if limit is not None else 20

        routing_decision = None
        if self._granularity_routing_enabled and self._granularity_router:
            routing_decision = await self._apply_granularity_routing(query)

        # Temporal normalization - resolve relative dates to absolute dates
        search_query = query
        extracted_dates = []
        if self._temporal_normalization_enabled and self._temporal_normalizer:
            search_query, extracted_dates = self._temporal_normalizer.normalize_query(query)
            if extracted_dates:
                logger.debug(f"Temporal normalization: extracted {len(extracted_dates)} dates from query")
                self._temporal_normalization_counter.increment()

        if self._short_term_memory is None:
            short_episode: list[Episode] = []
            short_summary = ""
            long_episode = await cast("LongTermMemory", self._long_term_memory).search(search_query, num_episodes_limit=search_limit, property_filter=property_filter)
        elif self._long_term_memory is None:
            session_result = await self._short_term_memory.get_short_term_memory_context(search_query, limit=search_limit, filters=property_filter)
            long_episode = []
            short_episode, short_summary = session_result
        else:
            session_result, long_episode = await asyncio.gather(
                self._short_term_memory.get_short_term_memory_context(search_query, limit=search_limit, filters=property_filter),
                self._long_term_memory.search(search_query, num_episodes_limit=search_limit, property_filter=property_filter),
            )
            short_episode, short_summary = session_result

        episode_uid_set = {episode.uid for episode in short_episode}
        unique_long_episodes = []
        for episode in long_episode:
            if episode.uid not in episode_uid_set:
                episode_uid_set.add(episode.uid)
                unique_long_episodes.append(episode)

        # Knowledge graph reasoning and entity verification
        kg_reasoning_result = None
        entity_verification_result = None
        reasoning_response = None
        entity_verification_response = None

        if self._knowledge_graph_enabled and self._knowledge_graph_service:
            try:
                # Perform reasoning
                kg_reasoning_result = await self._knowledge_graph_service.reason(
                    query=query, session_key=self._session_key
                )
                self._kg_reasoning_counter.increment()

                # Build reasoning response
                reasoning_response = EpisodicMemory.QueryResponse.ReasoningResponse(
                    relevant_entities=[e.name for e in kg_reasoning_result.relevant_entities],
                    reasoning_paths=[p.to_string() for p in kg_reasoning_result.paths],
                    explanation=kg_reasoning_result.explanation,
                )

                # Perform entity verification for adversarial detection
                entity_verification_result = await self._knowledge_graph_service.verify_query(
                    query=query, session_key=self._session_key
                )

                if entity_verification_result:
                    entity_verification_response = EpisodicMemory.QueryResponse.EntityVerificationResponse(
                        is_adversarial=entity_verification_result.is_adversarial,
                        entity_swaps=entity_verification_result.entity_swaps_detected,
                        corrections=entity_verification_result.corrections,
                        explanation=entity_verification_result.explanation,
                    )

            except Exception as e:
                logger.warning(f"Knowledge graph reasoning/verification failed: {e}")

        # Neural graph retrieval (Phase 7: Multi-stage neural retrieval)
        neural_graph_response = None
        if self._neural_graph_enabled and self._neural_graph_service:
            try:
                # Get query embedding if available from long-term memory
                query_embedding = None
                if self._long_term_memory and hasattr(self._long_term_memory, 'get_embedding'):
                    query_embedding = await self._long_term_memory.get_embedding(search_query)

                if query_embedding:
                    neural_result = await self._neural_graph_service.retrieve(
                        query_text=search_query,
                        query_embedding=query_embedding,
                        session_key=self._session_key,
                        limit=search_limit,
                    )

                    self._neural_graph_retrieval_counter.increment()

                    neural_graph_response = EpisodicMemory.QueryResponse.NeuralGraphResponse(
                        node_ids=[node.node_id for node, _ in neural_result.nodes],
                        node_scores=[score for _, score in neural_result.nodes],
                        stages_executed=neural_result.stages_executed,
                        total_time_ms=neural_result.total_time_ms,
                    )

                    logger.debug(
                        f"Neural graph retrieval: {len(neural_result.nodes)} nodes, "
                        f"stages={neural_result.stages_executed}, time={neural_result.total_time_ms:.1f}ms"
                    )
                else:
                    logger.debug("Neural graph retrieval skipped: no query embedding available")

            except Exception as e:
                logger.warning(f"Neural graph retrieval failed: {e}")

        end_time = time.monotonic_ns()
        delta = (end_time - start_time) / 1000000
        self._query_latency_summary.observe(delta)
        self._query_counter.increment()

        return EpisodicMemory.QueryResponse(
            short_term_memory=EpisodicMemory.QueryResponse.ShortTermMemoryResponse(
                episodes=[EpisodeResponse(**episode.model_dump()) for episode in short_episode],
                episode_summary=[short_summary],
            ),
            long_term_memory=EpisodicMemory.QueryResponse.LongTermMemoryResponse(
                episodes=[EpisodeResponse(**episode.model_dump()) for episode in unique_long_episodes],
            ),
            entity_verification=entity_verification_response,
            reasoning=reasoning_response,
            neural_graph=neural_graph_response,
        )

    async def formalize_query_with_context(self, query: str, limit: int | None = None, property_filter: FilterExpr | None = None) -> str:
        query_result = await self.query_memory(query, limit, property_filter)
        if query_result is None:
            logger.warning("Query result is None in formalize_query_with_context")
            return query
        episodes = sorted(query_result.short_term_memory.episodes + query_result.long_term_memory.episodes, key=lambda x: cast(datetime.datetime, x.created_at))
        finalized_query = ""

        # Add temporal context header for temporal queries (CRITICAL for temporal accuracy)
        if self._temporal_normalization_enabled and self._temporal_normalizer and episodes:
            temporal_context = self._temporal_normalizer.build_temporal_context(episodes)
            temporal_header = self._temporal_normalizer.format_temporal_context(temporal_context)
            if temporal_header:
                finalized_query += temporal_header + "\n\n"

        # Include entity verification warnings for adversarial detection
        if query_result.entity_verification and query_result.entity_verification.is_adversarial:
            finalized_query += "<EntityVerificationWarning>\n"
            finalized_query += "IMPORTANT: This query may contain entity attribution errors.\n"
            for correction in query_result.entity_verification.corrections:
                finalized_query += f"  - {correction}\n"
            finalized_query += "Please verify entity facts against the memories before answering.\n"
            finalized_query += "</EntityVerificationWarning>\n"

        # Include reasoning context from knowledge graph
        if query_result.reasoning and query_result.reasoning.relevant_entities:
            finalized_query += "<RelevantEntities>\n"
            finalized_query += f"Key entities: {', '.join(query_result.reasoning.relevant_entities)}\n"
            finalized_query += "</RelevantEntities>\n"

        # Include neural graph retrieval metadata (Phase 7: Multi-stage retrieval)
        if query_result.neural_graph and query_result.neural_graph.node_ids:
            finalized_query += "<NeuralGraphContext>\n"
            finalized_query += f"Neural retrieval: {len(query_result.neural_graph.node_ids)} linked memories\n"
            finalized_query += f"Stages: {' → '.join(query_result.neural_graph.stages_executed)}\n"
            finalized_query += "</NeuralGraphContext>\n"

        if query_result.short_term_memory.episode_summary and len(query_result.short_term_memory.episode_summary) > 0:
            total_summary = ""
            for summ in query_result.short_term_memory.episode_summary:
                if not summ:
                    continue
                total_summary = total_summary + summ + "\n"
            total_summary = total_summary.strip()
            if total_summary:
                finalized_query += "<Summary>\n"
                finalized_query += total_summary
                finalized_query += "\n</Summary>\n"
        if episodes and len(episodes) > 0:
            finalized_query += "<Episodes>\n"
            finalized_query += EpisodicMemory.string_from_episode_response_context(episodes)
            finalized_query += "</Episodes>\n"
        finalized_query += f"<Query>\n{query}\n</Query>"
        return finalized_query

    @staticmethod
    def string_from_episode_response_context(episode_response_context: Iterable[EpisodeResponse]) -> str:
        context_string = ""
        for episode_response in episode_response_context:
            match episode_response.episode_type:
                case EpisodeType.MESSAGE:
                    context_date = EpisodicMemory._format_date(episode_response.created_at.date()) if episode_response.created_at else "Unknown Date"
                    context_time = EpisodicMemory._format_time(episode_response.created_at.time()) if episode_response.created_at else "Unknown Time"
                    context_string += f"[{context_date} at {context_time}] {episode_response.producer_id}: {json.dumps(episode_response.content)}\n"
                case _:
                    context_string += json.dumps(episode_response.content) + "\n"
        return context_string

    @staticmethod
    def _format_date(date: datetime.date) -> str:
        return date.strftime("%A, %B %d, %Y")

    @staticmethod
    def _format_time(time: datetime.time) -> str:
        return time.strftime("%I:%M %p")
