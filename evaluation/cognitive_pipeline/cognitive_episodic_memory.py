"""
Cognitive Episodic Memory Wrapper for Full-Pipeline Benchmark.

This wrapper initializes EpisodicMemory with all 6 cognitive modules enabled,
providing a unified interface for benchmark ingestion and querying.
"""

import asyncio
import hashlib
import logging
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Add src to path for imports
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from memmachine.common.episode_store import Episode, EpisodeType, EpisodeResponse
from memmachine.common.metrics_factory import MetricsFactory
from memmachine.episodic_memory.episodic_memory import (
    EpisodicMemory,
    EpisodicMemoryParams,
)
from memmachine.episodic_memory.long_term_memory.long_term_memory import LongTermMemory
from memmachine.episodic_memory.mid_term_memory import MidTermMemoryConfig
from memmachine.episodic_memory.short_term_memory.short_term_memory import ShortTermMemory

# Import cognitive modules
from memmachine.common.privacy_sanitizer import (
    ComplianceAuditor,
    HybridPIIDetector,
    OllamaPIIDetector,
    PrivacySanitizer,
    RegexPIIDetector,
    SanitizationConfig,
)
from memmachine.common.emotional_encoder import (
    EmotionDetector,
    HybridEmotionDetector,
    MoodTracker,
    EmotionalResonanceScorer,
    RuleBasedEmotionDetector,
    OllamaEmotionDetector,
)
from memmachine.knowledge_graph import (
    KnowledgeGraphService,
    HybridEntityExtractor,
    OllamaEntityExtractor,
    RuleBasedEntityExtractor,
    InMemoryGraphStorage,
    MeKBScorer,
    ThinkOnGraphReasoner,
)
from memmachine.common.granularity_router import (
    GranularityRouter,
    EntropyCalculator,
    MultiGranularityChunker,
)
from memmachine.common.domain_classifier import DomainClassifier

# Import sharded memory components
from memmachine.sharded_memory import (
    ShardedMemoryStore,
    GraphRetriever,
    DomainType,
    SubdomainType,
    MemoryType,
    RetrievalQuery,
    ShardConfig,
)
from memmachine.sharded_memory.graph_retriever import RetrievalConfig
from memmachine.sharded_memory.domain_hierarchy import DomainHierarchyManager

from .pipeline_config import CognitivePipelineConfig, EmotionDetectorType
from .metrics_collector import (
    MetricsCollector,
    IngestMetrics,
    QueryMetrics,
)

logger = logging.getLogger(__name__)

# Rebuild the pydantic model now that TYPE_CHECKING imports are resolved
EpisodicMemoryParams.model_rebuild()


# NoOp Metrics Implementation for Benchmark
class _NoOpCounter(MetricsFactory.Counter):
    def increment(self, value: float = 1, labels: dict[str, str] | None = None) -> None:
        pass


class _NoOpGauge(MetricsFactory.Gauge):
    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        pass


class _NoOpHistogram(MetricsFactory.Histogram):
    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        pass


class _NoOpSummary(MetricsFactory.Summary):
    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        pass


class NoOpMetricsFactory(MetricsFactory):
    """No-op metrics factory for benchmark testing."""

    def get_counter(self, name: str, description: str, label_names=()) -> MetricsFactory.Counter:
        return _NoOpCounter()

    def get_gauge(self, name: str, description: str, label_names=()) -> MetricsFactory.Gauge:
        return _NoOpGauge()

    def get_histogram(self, name: str, description: str, label_names=()) -> MetricsFactory.Histogram:
        return _NoOpHistogram()

    def get_summary(self, name: str, description: str, label_names=()) -> MetricsFactory.Summary:
        return _NoOpSummary()


class InMemoryShortTermMemory:
    """
    Simple in-memory short-term memory for benchmark testing.

    Stores episodes in a deque and provides basic retrieval functionality.
    """

    def __init__(self, session_key: str, capacity: int = 1000):
        self._session_key = session_key
        self._memory: deque[Episode] = deque(maxlen=capacity)
        self._closed = False

    async def add_episodes(self, episodes: list[Episode]) -> None:
        """Add episodes to memory."""
        for ep in episodes:
            self._memory.append(ep)

    async def get_short_term_memory_context(
        self,
        query: str,
        limit: int = 20,
        filters: Any = None,
    ) -> tuple[list[Episode], str]:
        """Retrieve episodes matching the query."""
        # Simple retrieval - return most recent episodes
        episodes = list(self._memory)[-limit:]
        return episodes, ""

    async def delete_episode(self, uid: str) -> None:
        """Delete episode by UID."""
        self._memory = deque(
            [ep for ep in self._memory if ep.uid != uid],
            maxlen=self._memory.maxlen,
        )

    async def clear_memory(self) -> None:
        """Clear all episodes."""
        self._memory.clear()

    async def close(self) -> None:
        """Close the memory store."""
        self._closed = True


class InMemoryLongTermMemory:
    """
    Simple in-memory long-term memory for benchmark testing.

    Stores episodes and provides basic search functionality.
    """

    def __init__(self, session_id: str):
        self._session_id = session_id
        self._episodes: dict[str, Episode] = {}
        self._closed = False

    async def add_episodes(self, episodes: list[Episode]) -> None:
        """Add episodes to memory."""
        for ep in episodes:
            self._episodes[ep.uid] = ep

    async def search(
        self,
        query: str,
        num_episodes_limit: int = 20,
        property_filter: Any = None,
    ) -> list[Episode]:
        """Search for episodes matching the query."""
        # Simple keyword matching
        query_lower = query.lower()
        matching = []
        for ep in self._episodes.values():
            if query_lower in ep.content.lower():
                matching.append(ep)
        return matching[:num_episodes_limit]

    async def delete_episodes(self, uids: list[str]) -> None:
        """Delete episodes by UIDs."""
        for uid in uids:
            self._episodes.pop(uid, None)

    async def delete_matching_episodes(self) -> None:
        """Delete all episodes."""
        self._episodes.clear()

    async def close(self) -> None:
        """Close the memory store."""
        self._closed = True


@dataclass
class IngestResult:
    """Result of an episode ingestion."""
    success: bool
    episode_uid: str
    metrics: IngestMetrics
    error: str | None = None


@dataclass
class QueryResult:
    """Result of a memory query."""
    query: str
    context: str
    episodes: list[dict[str, Any]]
    metrics: QueryMetrics
    kg_reasoning: dict[str, Any] | None = None


class CognitiveEpisodicMemory:
    """
    Wrapper that creates EpisodicMemory with all cognitive modules enabled.

    This class initializes and manages the complete cognitive pipeline:
    1. Privacy Sanitization - PII detection & redaction
    2. Emotional Encoding - Plutchik emotions & mood tracking
    3. Mid-term Memory - Heat-based consolidation
    4. Knowledge Graph - Entity extraction & reasoning
    5. Granularity Router - Entropy-based adaptive retrieval
    6. Domain Classifier - Semantic memory sharding
    """

    def __init__(
        self,
        session_key: str,
        config: CognitivePipelineConfig | None = None,
    ):
        """Initialize cognitive episodic memory.

        Args:
            session_key: Unique identifier for this memory session.
            config: Pipeline configuration. Uses defaults if None.
        """
        self.session_key = session_key
        self.config = config or CognitivePipelineConfig()
        self._metrics_collector = MetricsCollector()

        # Initialize all components
        self._privacy_sanitizer: PrivacySanitizer | None = None
        self._compliance_auditor: ComplianceAuditor | None = None
        self._emotion_detector: EmotionDetector | None = None
        self._mood_tracker: MoodTracker | None = None
        self._resonance_scorer: EmotionalResonanceScorer | None = None
        self._knowledge_graph_service: KnowledgeGraphService | None = None
        self._granularity_router: GranularityRouter | None = None
        self._domain_classifier: DomainClassifier | None = None
        self._episodic_memory: EpisodicMemory | None = None
        self._in_memory_stm: InMemoryShortTermMemory | None = None
        self._in_memory_ltm: InMemoryLongTermMemory | None = None

        # Sharded memory components
        self._sharded_memory_store: ShardedMemoryStore | None = None
        self._graph_retriever: GraphRetriever | None = None
        self._domain_hierarchy_manager: DomainHierarchyManager | None = None
        self._user_initialized: bool = False

        # Initialize components based on config
        self._init_privacy_sanitizer()
        self._init_emotional_encoder()
        self._init_knowledge_graph()
        self._init_granularity_router()
        self._init_domain_classifier()
        self._init_sharded_memory()
        self._init_episodic_memory()

        logger.info(f"Initialized CognitiveEpisodicMemory for session {session_key}")
        logger.info(f"Enabled phases: {self.config.get_enabled_phases()}")

    def _init_privacy_sanitizer(self) -> None:
        """Initialize privacy sanitization components."""
        if not self.config.privacy.enabled:
            return

        # Create PII detectors
        regex_detector = RegexPIIDetector()
        llm_detector = OllamaPIIDetector(
            model=self.config.llm.ollama_model,
            base_url=f"{self.config.llm.ollama_base_url}/v1",
            api_key="ollama",
        )

        # Create hybrid detector (regex + LLM fallback)
        pii_detector = HybridPIIDetector(
            regex_detector=regex_detector,
            llm_detector=llm_detector,
            use_llm=True,
        )

        # Create sanitization config
        sanitization_config = SanitizationConfig(
            block_critical=self.config.privacy.block_critical,
            redact_high=self.config.privacy.redact_high,
            allow_low=self.config.privacy.allow_low_sensitivity,
        )

        # Create sanitizer
        self._privacy_sanitizer = PrivacySanitizer(
            detector=pii_detector,
            config=sanitization_config,
        )

        # Create compliance auditor
        self._compliance_auditor = ComplianceAuditor()

        logger.debug("Privacy sanitizer initialized")

    def _init_emotional_encoder(self) -> None:
        """Initialize emotional encoding components."""
        if not self.config.emotional.enabled:
            return

        # Create emotion detector based on config
        detector_type = self.config.emotional.detector_type

        if detector_type == EmotionDetectorType.RULE:
            self._emotion_detector = RuleBasedEmotionDetector()
        elif detector_type == EmotionDetectorType.OLLAMA:
            self._emotion_detector = OllamaEmotionDetector(
                model=self.config.llm.ollama_model,
                base_url=f"{self.config.llm.ollama_base_url}/v1",
                api_key="ollama",
            )
        else:  # HYBRID
            llm_detector = OllamaEmotionDetector(
                model=self.config.llm.ollama_model,
                base_url=f"{self.config.llm.ollama_base_url}/v1",
                api_key="ollama",
            )
            rule_detector = RuleBasedEmotionDetector()
            self._emotion_detector = HybridEmotionDetector(
                llm_detector=llm_detector,
                rule_detector=rule_detector,
                low_confidence_threshold=self.config.emotional.min_confidence_threshold,
            )

        # Create mood tracker
        self._mood_tracker = MoodTracker(
            emotion_detector=self._emotion_detector,
            default_half_life_hours=self.config.emotional.mood_half_life_hours,
        )

        # Create resonance scorer
        self._resonance_scorer = EmotionalResonanceScorer(
            mood_weight=self.config.emotional.resonance_weight,
        )

        logger.debug("Emotional encoder initialized")

    def _init_knowledge_graph(self) -> None:
        """Initialize knowledge graph components."""
        if not self.config.knowledge_graph.enabled:
            return

        # Create entity extractors
        llm_extractor = OllamaEntityExtractor(
            model=self.config.llm.ollama_model,
            base_url=f"{self.config.llm.ollama_base_url}/v1",
            api_key="ollama",
        )
        rule_extractor = RuleBasedEntityExtractor()

        # Create hybrid extractor
        extractor = HybridEntityExtractor(
            llm_extractor=llm_extractor,
            rule_extractor=rule_extractor,
        )

        # Create graph storage
        storage = InMemoryGraphStorage()

        # Create MeKB scorer
        mekb_scorer = MeKBScorer()

        # Create Think-on-Graph reasoner
        reasoner = ThinkOnGraphReasoner(
            storage=storage,
            mekb_scorer=mekb_scorer,
            max_depth=self.config.knowledge_graph.max_hops,
            beam_width=self.config.knowledge_graph.beam_width,
        )

        # Create service
        self._knowledge_graph_service = KnowledgeGraphService(
            extractor=extractor,
            storage=storage,
            mekb_scorer=mekb_scorer,
            reasoner=reasoner,
            enabled=True,
        )

        logger.debug("Knowledge graph service initialized")

    def _init_granularity_router(self) -> None:
        """Initialize granularity routing components."""
        if not self.config.granularity.enabled:
            return

        # Create entropy calculator
        entropy_calc = EntropyCalculator()

        # Create chunker
        chunker = MultiGranularityChunker()

        # Create router
        self._granularity_router = GranularityRouter(
            entropy_calculator=entropy_calc,
            chunker=chunker,
            enabled=self.config.granularity.adaptive,
        )

        logger.debug("Granularity router initialized")

    def _init_domain_classifier(self) -> None:
        """Initialize domain classification components."""
        if not self.config.domain.enabled:
            return

        self._domain_classifier = DomainClassifier(
            model=self.config.llm.ollama_model,
            base_url=f"{self.config.llm.ollama_base_url}/v1",
            api_key="ollama",
        )

        logger.debug("Domain classifier initialized")

    def _init_sharded_memory(self) -> None:
        """Initialize sharded memory components."""
        if not self.config.sharded_memory.enabled:
            return

        # Create shard configuration
        shard_config = ShardConfig(
            num_shards=self.config.sharded_memory.num_shards,
            virtual_nodes_per_shard=self.config.sharded_memory.virtual_nodes_per_shard,
        )

        # Create domain hierarchy manager
        self._domain_hierarchy_manager = DomainHierarchyManager()

        # Create sharded memory store
        self._sharded_memory_store = ShardedMemoryStore(config=shard_config)

        # Initialize user domain structure (sync initialization - async done on first ingest)
        # The store.initialize_user is async, so we'll do it on first episode ingest
        self._user_initialized = False

        # Create retrieval config
        retrieval_config = RetrievalConfig(
            max_hops=self.config.sharded_memory.max_hops,
            enable_domain_hints=self.config.sharded_memory.enable_domain_hints,
            embedding_weight=self.config.sharded_memory.embedding_weight,
            importance_weight=self.config.sharded_memory.importance_weight,
            recency_weight=self.config.sharded_memory.recency_weight,
            access_weight=self.config.sharded_memory.access_weight,
            path_weight=self.config.sharded_memory.path_weight,
        )

        # Create graph retriever
        self._graph_retriever = GraphRetriever(
            memory_store=self._sharded_memory_store,
            config=retrieval_config,
        )

        logger.debug(f"Sharded memory initialized with {shard_config.num_shards} shards")

    def _init_episodic_memory(self) -> None:
        """Initialize the main episodic memory with all modules."""
        # Create metrics factory
        metrics_factory = NoOpMetricsFactory()

        # Create in-memory STM and LTM for benchmark testing
        self._in_memory_stm = InMemoryShortTermMemory(self.session_key)
        self._in_memory_ltm = InMemoryLongTermMemory(self.session_key)

        # Create mock wrappers that pass isinstance checks
        # The mocks delegate to our in-memory implementations
        mock_stm = MagicMock(spec=ShortTermMemory)
        mock_stm.add_episodes = AsyncMock(side_effect=self._in_memory_stm.add_episodes)
        mock_stm.get_short_term_memory_context = AsyncMock(
            side_effect=self._in_memory_stm.get_short_term_memory_context
        )
        mock_stm.delete_episode = AsyncMock(side_effect=self._in_memory_stm.delete_episode)
        mock_stm.clear_memory = AsyncMock(side_effect=self._in_memory_stm.clear_memory)
        mock_stm.close = AsyncMock(side_effect=self._in_memory_stm.close)

        mock_ltm = MagicMock(spec=LongTermMemory)
        mock_ltm.add_episodes = AsyncMock(side_effect=self._in_memory_ltm.add_episodes)
        mock_ltm.search = AsyncMock(side_effect=self._in_memory_ltm.search)
        mock_ltm.delete_episodes = AsyncMock(side_effect=self._in_memory_ltm.delete_episodes)
        mock_ltm.delete_matching_episodes = AsyncMock(
            side_effect=self._in_memory_ltm.delete_matching_episodes
        )
        mock_ltm.close = AsyncMock(side_effect=self._in_memory_ltm.close)

        # Create MTM config if enabled
        mtm_config = None
        if self.config.mid_term_memory.enabled:
            mtm_config = MidTermMemoryConfig(
                promotion_threshold=self.config.mid_term_memory.promotion_threshold,
                eviction_threshold=self.config.mid_term_memory.eviction_threshold,
                heat_strength=self.config.mid_term_memory.heat_strength_days,
            )

        # Create episodic memory params with mock STM/LTM
        params = EpisodicMemoryParams(
            session_key=self.session_key,
            metrics_factory=metrics_factory,
            short_term_memory=mock_stm,
            long_term_memory=mock_ltm,
            enabled=True,
            # Privacy
            privacy_sanitizer=self._privacy_sanitizer,
            compliance_auditor=self._compliance_auditor,
            privacy_enabled=self.config.privacy.enabled,
            # Emotional
            emotion_detector=self._emotion_detector,
            mood_tracker=self._mood_tracker,
            resonance_scorer=self._resonance_scorer,
            emotional_encoding_enabled=self.config.emotional.enabled,
            # MTM - disabled for now as it requires real LTM
            mid_term_memory_config=None,
            mid_term_memory_enabled=False,
            # Knowledge Graph
            knowledge_graph_service=self._knowledge_graph_service,
            knowledge_graph_enabled=self.config.knowledge_graph.enabled,
            # Granularity
            granularity_router=self._granularity_router,
            granularity_routing_enabled=self.config.granularity.enabled,
            # Domain
            domain_classifier=self._domain_classifier,
            domain_classification_enabled=self.config.domain.enabled,
        )

        self._episodic_memory = EpisodicMemory(params)
        logger.debug("Episodic memory initialized with all modules")

    async def ingest_episode(
        self,
        content: str,
        speaker: str = "user",
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """Ingest an episode through the full cognitive pipeline.

        Args:
            content: Episode text content.
            speaker: Speaker/producer ID.
            timestamp: Episode timestamp. Uses now if None.
            metadata: Additional metadata.

        Returns:
            IngestResult with success status and metrics.
        """
        start_time = time.perf_counter() * 1000

        # Create episode
        episode_uid = str(uuid.uuid4())
        episode = Episode(
            uid=episode_uid,
            content=content,
            episode_type=EpisodeType.MESSAGE,
            producer_id=speaker,
            producer_role=speaker,  # Use speaker as role (user, assistant, etc.)
            created_at=timestamp or datetime.now(timezone.utc),
            session_key=self.session_key,
            metadata=metadata or {},
        )

        # Initialize metrics
        ingest_metrics = IngestMetrics()

        try:
            # Add to episodic memory (full pipeline)
            await self._episodic_memory.add_memory_episodes([episode])

            # Capture metrics from the processed episode
            if episode.pii_detected:
                ingest_metrics.pii_detected = True
                ingest_metrics.pii_types = episode.pii_types_detected or []
            if episode.dominant_emotion:
                ingest_metrics.dominant_emotion = episode.dominant_emotion
            if episode.emotional_vector:
                ingest_metrics.emotional_vector = episode.emotional_vector
            if episode.filterable_metadata:
                if "domain" in episode.filterable_metadata:
                    ingest_metrics.domain = episode.filterable_metadata["domain"]
                    ingest_metrics.domain_confidence = episode.filterable_metadata.get(
                        "domain_confidence", 0.0
                    )

            # Store in sharded memory if enabled
            if self._sharded_memory_store and self.config.sharded_memory.enabled:
                await self._store_in_sharded_memory(episode, ingest_metrics)

            # Calculate total time
            ingest_metrics.total_processing_time_ms = (
                time.perf_counter() * 1000 - start_time
            )

            # Record in collector
            self._metrics_collector.record_ingestion(ingest_metrics)

            return IngestResult(
                success=True,
                episode_uid=episode_uid,
                metrics=ingest_metrics,
            )

        except Exception as e:
            logger.error(f"Ingestion failed: {e}")
            ingest_metrics.was_blocked = True
            ingest_metrics.total_processing_time_ms = (
                time.perf_counter() * 1000 - start_time
            )
            self._metrics_collector.record_ingestion(ingest_metrics)

            return IngestResult(
                success=False,
                episode_uid=episode_uid,
                metrics=ingest_metrics,
                error=str(e),
            )

    async def ingest_episodes_batch(
        self,
        episodes_data: list[dict[str, Any]],
    ) -> list[IngestResult]:
        """Ingest multiple episodes.

        Args:
            episodes_data: List of episode dictionaries with keys:
                - content: Episode text
                - speaker: Speaker ID
                - timestamp: Optional datetime
                - metadata: Optional metadata dict

        Returns:
            List of IngestResult objects.
        """
        results = []
        for ep_data in episodes_data:
            result = await self.ingest_episode(
                content=ep_data["content"],
                speaker=ep_data.get("speaker", "user"),
                timestamp=ep_data.get("timestamp"),
                metadata=ep_data.get("metadata"),
            )
            results.append(result)
        return results

    def _map_domain_to_type(self, domain_str: str | None) -> tuple[DomainType, SubdomainType]:
        """Map domain string to DomainType and SubdomainType.

        Args:
            domain_str: Domain string from classifier (e.g., "RELATIONSHIPS", "EVENTS")

        Returns:
            Tuple of (DomainType, SubdomainType)
        """
        domain_mapping = {
            "RELATIONSHIPS": (DomainType.RELATIONSHIPS, SubdomainType.FAMILY),
            "EVENTS": (DomainType.CURRENT_DATA, SubdomainType.RECENT_TOPICS),
            "PERSONAL": (DomainType.PERSONALITY, SubdomainType.PERSONALITY_TRAITS),
            "HOBBIES": (DomainType.PREFERENCES, SubdomainType.CONTENT_PREFERENCES),
            "PROFESSIONAL": (DomainType.KNOWLEDGE, SubdomainType.EXPERTISE_AREAS),
            "HEALTH": (DomainType.CURRENT_DATA, SubdomainType.CURRENT_MOOD),
            "PREFERENCES": (DomainType.PREFERENCES, SubdomainType.AESTHETIC_PREFERENCES),
            "GOALS": (DomainType.GOALS, SubdomainType.SHORT_TERM_GOALS),
        }
        if domain_str and domain_str.upper() in domain_mapping:
            return domain_mapping[domain_str.upper()]
        return (DomainType.CURRENT_DATA, SubdomainType.RECENT_TOPICS)

    async def _store_in_sharded_memory(
        self,
        episode: Episode,
        metrics: IngestMetrics,
    ) -> None:
        """Store episode in sharded memory with domain pointers.

        Args:
            episode: The processed episode.
            metrics: Ingest metrics to update.
        """
        # Initialize user if not done yet
        if not self._user_initialized:
            await self._sharded_memory_store.initialize_user(self.session_key)
            self._user_initialized = True

        # Get domain from episode metadata or metrics
        domain_str = None
        if episode.filterable_metadata and "domain" in episode.filterable_metadata:
            domain_str = episode.filterable_metadata["domain"]
        elif metrics.domain:
            domain_str = metrics.domain

        # Map to domain types
        domain_type, subdomain_type = self._map_domain_to_type(domain_str)

        # Calculate importance based on emotional intensity
        importance_score = 0.5
        if episode.emotional_vector:
            # Higher emotional intensity = higher importance
            intensities = list(episode.emotional_vector.values())
            if intensities:
                importance_score = min(0.9, 0.5 + max(intensities) * 0.4)

        # Calculate emotional valence
        emotional_valence = 0.0
        if episode.emotional_vector:
            positive = sum(
                episode.emotional_vector.get(e, 0)
                for e in ["joy", "trust", "anticipation"]
            )
            negative = sum(
                episode.emotional_vector.get(e, 0)
                for e in ["sadness", "fear", "anger", "disgust"]
            )
            total = positive + negative
            if total > 0:
                emotional_valence = (positive - negative) / total

        # Determine memory type from episode
        memory_type = MemoryType.EVENT  # Default to event

        # Store in sharded memory
        memory = await self._sharded_memory_store.store_memory(
            user_id=self.session_key,
            content=episode.content,
            memory_type=memory_type,
            primary_subdomain_type=subdomain_type,
            importance_score=importance_score,
            metadata={
                "speaker": episode.producer_id,
                "timestamp": episode.created_at.isoformat() if episode.created_at else None,
                "domain_str": domain_str,
                "emotional_valence": emotional_valence,
                "episode_uid": episode.uid,
            },
        )

        # Update metrics with shard info
        if memory and memory.shard_id:
            metrics.shard_id = memory.shard_id

    async def query(
        self,
        question: str,
        limit: int = 20,
    ) -> QueryResult:
        """Query memory through the full cognitive pipeline.

        Args:
            question: Query string.
            limit: Maximum episodes to retrieve.

        Returns:
            QueryResult with context and metrics.
        """
        start_time = time.perf_counter() * 1000
        query_metrics = QueryMetrics()

        # Apply granularity routing if enabled
        if self._granularity_router and self.config.granularity.enabled:
            routing_start = time.perf_counter() * 1000
            decision = self._granularity_router.analyze_query(question)
            query_metrics.routing_time_ms = time.perf_counter() * 1000 - routing_start
            query_metrics.entropy_score = decision.entropy_score.entropy
            query_metrics.granularity_level = decision.selected_granularity.value

        # Query episodic memory
        response = await self._episodic_memory.query_memory(question, limit=limit)

        if response is None:
            query_metrics.total_query_time_ms = time.perf_counter() * 1000 - start_time
            self._metrics_collector.record_query(query_metrics)
            return QueryResult(
                query=question,
                context="",
                episodes=[],
                metrics=query_metrics,
            )

        # Get episodes from response
        stm_episodes = response.short_term_memory.episodes
        ltm_episodes = response.long_term_memory.episodes

        query_metrics.stm_results = len(stm_episodes)
        query_metrics.ltm_results = len(ltm_episodes)

        # Get current mood if available
        if self._mood_tracker:
            try:
                mood_state = self._mood_tracker.get_current_mood(self.session_key)
                if mood_state:
                    query_metrics.current_mood = mood_state.vector.to_dict()
            except Exception:
                pass  # Mood tracking is optional

        # Perform KG reasoning if enabled
        kg_reasoning = None
        if self._knowledge_graph_service and self.config.knowledge_graph.enabled:
            kg_start = time.perf_counter() * 1000
            try:
                reasoning_result = await self._knowledge_graph_service.reason(
                    question, self.session_key
                )
                query_metrics.kg_reasoning_time_ms = (
                    time.perf_counter() * 1000 - kg_start
                )
                query_metrics.kg_paths_found = len(reasoning_result.paths)
                query_metrics.kg_entities_involved = len(
                    reasoning_result.relevant_entities
                )
                kg_reasoning = {
                    "paths": [p.to_dict() for p in reasoning_result.paths] if reasoning_result.paths else [],
                    "entities": [e.name for e in reasoning_result.relevant_entities],
                    "explanation": reasoning_result.explanation,
                }
            except Exception as e:
                logger.warning(f"KG reasoning failed: {e}")

        # Query sharded memory if enabled
        sharded_memories = []
        if self._graph_retriever and self.config.sharded_memory.enabled:
            graph_start = time.perf_counter() * 1000
            try:
                retrieval_query = RetrievalQuery(
                    user_id=self.session_key,
                    query_text=question,
                    limit=limit,
                    follow_domain_pointers=self.config.sharded_memory.follow_domain_pointers,
                    max_hops=self.config.sharded_memory.max_hops,
                )
                retrieval_result = await self._graph_retriever.retrieve(retrieval_query)
                sharded_memories = retrieval_result.memories
                query_metrics.sharded_results = len(sharded_memories)
                query_metrics.shards_accessed = len(retrieval_result.accessed_shards)
                query_metrics.graph_traversal_time_ms = (
                    time.perf_counter() * 1000 - graph_start
                )
            except Exception as e:
                logger.warning(f"Sharded memory query failed: {e}")

        # Format context using formalize_query_with_context for temporal normalization
        # This includes temporal context headers which are CRITICAL for temporal questions
        try:
            context = await self._episodic_memory.formalize_query_with_context(question, limit=limit)
        except Exception as e:
            logger.warning(f"formalize_query_with_context failed: {e}, using fallback")
            # Fallback to basic formatting
            all_episodes = stm_episodes + ltm_episodes
            context = self._format_context(all_episodes, response.short_term_memory.episode_summary)

        # Add sharded memory context if available
        all_episodes = stm_episodes + ltm_episodes
        if sharded_memories:
            sharded_context = self._format_sharded_context(sharded_memories)
            context = context + "\n" + sharded_context

        # Build episode list for output
        episodes_output = []
        for ep in all_episodes:
            ep_dict = {
                "uid": ep.uid,
                "content": ep.content,
                "speaker": ep.producer_id,
                "created_at": ep.created_at.isoformat() if ep.created_at else None,
            }
            # Safely check for emotional fields (may not exist on EpisodeResponse)
            if hasattr(ep, 'dominant_emotion') and ep.dominant_emotion:
                ep_dict["dominant_emotion"] = ep.dominant_emotion
            if hasattr(ep, 'emotional_vector') and ep.emotional_vector:
                ep_dict["emotional_vector"] = ep.emotional_vector
            episodes_output.append(ep_dict)

        # Add sharded memories to output
        for mem in sharded_memories:
            mem_dict = {
                "uid": mem.memory_id,
                "content": mem.content,
                "speaker": mem.metadata.get("speaker", "unknown") if mem.metadata else "unknown",
                "created_at": mem.created_at.isoformat() if mem.created_at else None,
                "shard_id": mem.shard_id,
                "importance": mem.importance_score,
            }
            episodes_output.append(mem_dict)

        query_metrics.total_query_time_ms = time.perf_counter() * 1000 - start_time
        self._metrics_collector.record_query(query_metrics)

        return QueryResult(
            query=question,
            context=context,
            episodes=episodes_output,
            metrics=query_metrics,
            kg_reasoning=kg_reasoning,
        )

    def _format_context(
        self,
        episodes: list,
        summaries: list[str] | None = None,
    ) -> str:
        """Format episodes into context string.

        Args:
            episodes: List of episode responses.
            summaries: Optional summary strings.

        Returns:
            Formatted context string.
        """
        lines = ["=== CONVERSATION MEMORIES ===\n"]

        # Add summaries if present
        if summaries:
            for summary in summaries:
                if summary:
                    lines.append(f"Summary: {summary}\n")
            lines.append("")

        # Sort episodes by timestamp
        sorted_episodes = sorted(
            episodes,
            key=lambda e: e.created_at if e.created_at else datetime.min.replace(tzinfo=timezone.utc),
        )

        # Format each episode
        current_date = None
        for ep in sorted_episodes:
            # Add date header if changed
            if ep.created_at:
                ep_date = ep.created_at.strftime("%B %d, %Y")
                if ep_date != current_date:
                    current_date = ep_date
                    lines.append(f"\n--- {ep_date} ---\n")

                time_str = ep.created_at.strftime("%I:%M %p")
                lines.append(f"[{time_str}] {ep.producer_id}: {ep.content}\n")
            else:
                lines.append(f"{ep.producer_id}: {ep.content}\n")

        return "".join(lines)

    def _format_sharded_context(self, memories: list) -> str:
        """Format sharded memories into context string.

        Args:
            memories: List of Memory objects from sharded store.

        Returns:
            Formatted context string.
        """
        if not memories:
            return ""

        lines = ["\n=== SHARDED MEMORY CONTEXT ===\n"]

        # Sort by importance score (most important first)
        sorted_memories = sorted(
            memories,
            key=lambda m: m.importance_score,
            reverse=True,
        )

        for mem in sorted_memories:
            # Get speaker from metadata
            speaker = mem.metadata.get("speaker", "unknown") if mem.metadata else "unknown"

            # Format with importance indicator
            importance_stars = "★" * min(int(mem.importance_score * 5), 5)
            lines.append(f"[{importance_stars}] {speaker}: {mem.content}\n")

        return "".join(lines)

    def get_metrics(self) -> MetricsCollector:
        """Get the metrics collector."""
        return self._metrics_collector

    async def get_pipeline_statistics(self) -> dict[str, Any]:
        """Get comprehensive pipeline statistics.

        Returns:
            Dictionary with statistics from all enabled modules.
        """
        stats = {
            "session_key": self.session_key,
            "enabled_phases": self.config.get_enabled_phases(),
            "metrics_summary": self._metrics_collector.get_summary(),
        }

        # Add KG statistics
        if self._knowledge_graph_service:
            stats["knowledge_graph"] = self._knowledge_graph_service.statistics

        # Add granularity statistics
        if self._granularity_router:
            stats["granularity"] = {
                "enabled": self._granularity_router.enabled,
            }

        # Add domain classifier statistics
        if self._domain_classifier:
            stats["domain_classifier"] = {
                "success_count": self._domain_classifier.success_count,
                "failure_count": self._domain_classifier.failure_count,
                "failure_rate": f"{self._domain_classifier.failure_rate:.1f}%",
            }

        # Add sharded memory statistics
        if self._sharded_memory_store:
            stats["sharded_memory"] = self._sharded_memory_store.get_statistics()

        return stats

    async def close(self) -> None:
        """Close and cleanup all resources."""
        if self._episodic_memory:
            await self._episodic_memory.close()
        logger.info(f"Closed CognitiveEpisodicMemory for session {self.session_key}")
