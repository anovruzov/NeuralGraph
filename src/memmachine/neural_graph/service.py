"""Neural Graph Service - High-level service coordinating all neural graph components.

This service integrates the neural_graph module with the existing MemMachine
architecture, providing a unified interface for:
- Memory ingestion with neural encoding
- Multi-stage retrieval
- Consolidation scheduling
- Integration with wave_memory, knowledge_graph, and episodic_memory

Design principles:
- Works IN PARALLEL with existing systems (not replacing them)
- Graceful degradation if neural components fail
- Automatic temporal chain and entity linking
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .data_types import (
    ConsolidationResult,
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
    RetrievalResult,
    generate_edge_id,
    generate_node_id,
    cosine_similarity,
)
from .storage import InMemoryNeuralGraphStorage, NeuralGraphStorage
from .gating import EdgeGateRegistry, create_balanced_registry
from .hierarchy import HierarchyManager, HierarchyConfig
from .temporal import TemporalChainManager, TemporalConfig
from .consolidation import ConsolidationManager, ConsolidationConfig
from .retriever import NeuralRetriever, RetrieverConfig

if TYPE_CHECKING:
    from memmachine.common.episode_store import Episode
    from memmachine.wave_memory.data_types import MemoryWave
    from memmachine.knowledge_graph.data_types import Entity, Relation, ExtractionResult

logger = logging.getLogger(__name__)


@dataclass
class NeuralGraphServiceConfig:
    """Configuration for NeuralGraphService."""

    # Enable/disable components
    enabled: bool = True
    hierarchy_enabled: bool = True
    temporal_enabled: bool = True
    consolidation_enabled: bool = True

    # Auto-consolidation
    auto_consolidation_interval_seconds: float = 300.0  # 5 minutes
    auto_temporal_linking: bool = True
    auto_episode_segmentation: bool = True

    # NEW: Post-ingestion consolidation check
    # If True, checks if consolidation should run after each ingestion batch
    auto_consolidation_on_ingestion: bool = True

    # NEW: Temporal dynamics application
    # If True, applies STDP and LTP decay after retrieval
    apply_temporal_dynamics: bool = True
    temporal_dynamics_probability: float = 0.1  # 10% chance per retrieval for LTP decay

    # Component configs
    hierarchy_config: HierarchyConfig | None = None
    temporal_config: TemporalConfig | None = None
    consolidation_config: ConsolidationConfig | None = None
    retriever_config: RetrieverConfig | None = None

    # Retrieval settings
    use_neural_retrieval: bool = True
    neural_retrieval_weight: float = 0.5  # Blend with existing retrieval


@dataclass
class NeuralIngestionResult:
    """Result of neural memory ingestion."""
    nodes_created: int = 0
    temporal_edges_created: int = 0
    entity_edges_created: int = 0
    semantic_edges_created: int = 0
    processing_time_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


class NeuralGraphService:
    """High-level service for neural graph operations.

    Coordinates:
    - HierarchyManager: 4-layer node structure
    - TemporalChainManager: Temporal edges and LTP
    - ConsolidationManager: Pruning and compression
    - NeuralRetriever: Multi-stage retrieval

    Integrates with:
    - EpisodicMemory: Memory ingestion pipeline
    - KnowledgeGraphService: Entity extraction
    - WaveMemory: Signal amplitudes
    """

    def __init__(
        self,
        storage: NeuralGraphStorage | None = None,
        config: NeuralGraphServiceConfig | None = None
    ):
        """Initialize neural graph service.

        Args:
            storage: Graph storage backend. Creates in-memory if None.
            config: Service configuration.
        """
        self._config = config or NeuralGraphServiceConfig()
        self._storage = storage or InMemoryNeuralGraphStorage()

        # Initialize components
        self._gate_registry = create_balanced_registry()

        self._hierarchy = HierarchyManager(
            self._storage,
            self._config.hierarchy_config or HierarchyConfig()
        ) if self._config.hierarchy_enabled else None

        self._temporal = TemporalChainManager(
            self._storage,
            self._config.temporal_config or TemporalConfig()
        ) if self._config.temporal_enabled else None

        self._consolidation = ConsolidationManager(
            self._storage,
            self._hierarchy,
            temporal_manager=self._temporal,  # NEW: Pass temporal manager for hub linking
            config=self._config.consolidation_config or ConsolidationConfig()
        ) if self._config.consolidation_enabled else None

        self._retriever = NeuralRetriever(
            self._storage,
            gate_registry=self._gate_registry,
            temporal_manager=self._temporal,
            config=self._config.retriever_config or RetrieverConfig()
        )

        # Background consolidation task
        self._consolidation_task: asyncio.Task | None = None
        self._closed = False

        # Concurrency protection
        # Prevents race conditions between retrieval and consolidation
        self._consolidation_lock = asyncio.Lock()
        self._retrieval_semaphore = asyncio.Semaphore(10)  # Max concurrent retrievals

        # Statistics
        self._total_nodes_created = 0
        self._total_edges_created = 0
        self._total_retrievals = 0

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def storage(self) -> NeuralGraphStorage:
        return self._storage

    @property
    def hierarchy(self) -> HierarchyManager | None:
        return self._hierarchy

    @property
    def temporal(self) -> TemporalChainManager | None:
        return self._temporal

    @property
    def consolidation(self) -> ConsolidationManager | None:
        return self._consolidation

    @property
    def retriever(self) -> NeuralRetriever:
        return self._retriever

    # =========================================================================
    # EPISODE INGESTION
    # =========================================================================

    async def process_episodes(
        self,
        episodes: list[Episode],
        session_key: str,
        embeddings: list[list[float]] | None = None,
        wave_amplitudes_list: list[dict[str, float]] | None = None
    ) -> NeuralIngestionResult:
        """Process episodes into neural graph nodes.

        Creates Layer 0 message nodes for each episode, then:
        - Builds temporal chains between messages
        - Creates entity edges if entity_ids are present
        - Optionally segments into episode nodes

        Args:
            episodes: Episodes to process
            session_key: Session identifier
            embeddings: Optional pre-computed embeddings per episode
            wave_amplitudes_list: Optional wave amplitudes per episode

        Returns:
            NeuralIngestionResult with statistics
        """
        if not self._config.enabled:
            return NeuralIngestionResult()

        start_time = time.perf_counter()
        result = NeuralIngestionResult()

        if not episodes:
            return result

        # Create message nodes
        nodes: list[NeuralNode] = []

        for i, episode in enumerate(episodes):
            try:
                # Get embedding and wave amplitudes if available
                embedding = embeddings[i] if embeddings and i < len(embeddings) else None
                wave_amps = wave_amplitudes_list[i] if wave_amplitudes_list and i < len(wave_amplitudes_list) else {}

                # Extract entity IDs from episode metadata if present
                entity_ids = []
                if hasattr(episode, 'filterable_metadata') and episode.filterable_metadata:
                    entity_ids = episode.filterable_metadata.get('entity_ids', [])

                # Create message node
                if self._hierarchy:
                    node = await self._hierarchy.create_message_node(
                        content=episode.content,
                        session_key=session_key,
                        embedding=embedding,
                        wave_amplitudes=wave_amps,
                        entity_ids=entity_ids,
                        importance_score=getattr(episode, 'importance_score', 0.5),
                        episode_uid=episode.uid,
                        producer_id=getattr(episode, 'producer_id', None),
                        created_at=getattr(episode, 'created_at', None),
                    )
                    nodes.append(node)
                    result.nodes_created += 1

            except Exception as e:
                logger.error(f"Failed to create node for episode {episode.uid}: {e}")
                result.errors.append(str(e))

        # Build temporal chains
        if self._config.auto_temporal_linking and self._temporal and len(nodes) >= 2:
            try:
                temporal_edges = await self._temporal.build_temporal_chain(nodes, session_key)
                result.temporal_edges_created = len(temporal_edges)
            except Exception as e:
                logger.error(f"Failed to build temporal chain: {e}")
                result.errors.append(f"Temporal chain error: {e}")

        # Auto-segment into episodes if enabled
        if self._config.auto_episode_segmentation and self._hierarchy:
            try:
                episode_nodes = await self._hierarchy.auto_segment_to_episodes(session_key)
                result.nodes_created += len(episode_nodes)
            except Exception as e:
                logger.warning(f"Auto episode segmentation failed: {e}")

        # NEW: Auto-create entity edges between nodes sharing entities
        for node in nodes:
            try:
                entity_edges = await self._create_entity_edges(node, session_key)
                result.entity_edges_created += entity_edges
            except Exception as e:
                logger.warning(f"Entity edge creation failed for {node.node_id[:8]}: {e}")

        result.processing_time_ms = (time.perf_counter() - start_time) * 1000
        self._total_nodes_created += result.nodes_created
        self._total_edges_created += result.entity_edges_created

        logger.info(
            f"Neural ingestion: {result.nodes_created} nodes, "
            f"{result.temporal_edges_created} temporal edges, "
            f"{result.entity_edges_created} entity edges in {result.processing_time_ms:.1f}ms"
        )

        # NEW: Post-ingestion consolidation check
        # Checks if any nodes are ready for promotion and runs consolidation if needed
        if self._config.auto_consolidation_on_ingestion and self._consolidation:
            try:
                stats = await self._consolidation.get_consolidation_statistics(session_key)
                heat_stats = stats.get("heat", {})
                ready_for_promotion = heat_stats.get("ready_for_promotion", 0)
                ready_for_eviction = heat_stats.get("ready_for_eviction", 0)

                # Run consolidation if there are candidates
                if ready_for_promotion > 0 or ready_for_eviction > 0:
                    logger.info(
                        f"Post-ingestion consolidation triggered: "
                        f"{ready_for_promotion} ready for promotion, "
                        f"{ready_for_eviction} ready for eviction"
                    )
                    consolidation_result = await self.run_consolidation(session_key)
                    logger.info(
                        f"Post-ingestion consolidation complete: "
                        f"{consolidation_result.nodes_promoted} promoted, "
                        f"{consolidation_result.nodes_evicted} evicted"
                    )
            except Exception as e:
                logger.warning(f"Post-ingestion consolidation check failed: {e}")

        return result

    async def _create_entity_edges(
        self,
        node: NeuralNode,
        session_key: str
    ) -> int:
        """Create edges between nodes sharing entities.

        When a new node is created, finds other nodes with overlapping
        entity_ids and creates ENTITY edges to connect them.

        Args:
            node: Newly created node
            session_key: Session identifier

        Returns:
            Number of entity edges created
        """
        if not node.entity_ids:
            return 0

        edges_created = 0

        for entity_id in node.entity_ids:
            # Find other nodes with this entity
            related = await self._storage.find_nodes_by_entities([entity_id], session_key)

            for other in related:
                if other.node_id == node.node_id:
                    continue

                # Use canonical ordering to avoid duplicate edges
                source_id = min(node.node_id, other.node_id)
                target_id = max(node.node_id, other.node_id)

                # Check if edge already exists
                existing = await self._storage.get_edge_between(
                    source_id, target_id, EdgeType.ENTITY
                )

                if existing:
                    # Edge exists, strengthen it via activation
                    existing.activate()
                    await self._storage.save_edge(existing)
                else:
                    # Create new entity edge
                    edge = NeuralEdge(
                        edge_id=generate_edge_id(),
                        source_id=source_id,
                        target_id=target_id,
                        edge_type=EdgeType.ENTITY,
                        base_weight=0.5,
                        metadata={"shared_entity": entity_id}
                    )
                    await self._storage.save_edge(edge)
                    edges_created += 1

        return edges_created

    async def process_wave_memory(
        self,
        wave: MemoryWave,
        session_key: str,
        episode_uid: str | None = None
    ) -> NeuralNode | None:
        """Process a wave memory into a neural node.

        Extracts amplitudes and signals from the wave memory
        and creates corresponding neural edges.

        Args:
            wave: Wave memory with amplitudes and signals
            session_key: Session identifier
            episode_uid: Optional linked episode UID

        Returns:
            Created neural node
        """
        if not self._config.enabled or not self._hierarchy:
            return None

        try:
            # Extract amplitudes
            wave_amps = wave.amplitudes.to_dict() if hasattr(wave, 'amplitudes') else {}

            # Create node
            node = await self._hierarchy.create_message_node(
                content=wave.content,
                session_key=session_key,
                embedding=wave.embedding,
                wave_amplitudes=wave_amps,
                importance_score=wave_amps.get('entity', 0.5),  # Use entity amplitude as importance
                wave_id=wave.wave_id,
                episode_uid=episode_uid,
            )

            # Create edges from signals
            await self._process_wave_signals(node, wave, session_key)

            return node

        except Exception as e:
            logger.error(f"Failed to process wave memory: {e}")
            return None

    async def _process_wave_signals(
        self,
        node: NeuralNode,
        wave: MemoryWave,
        session_key: str
    ) -> int:
        """Create edges from wave signals.

        Maps wave signal types to neural edge types:
        - Temporal signals → TEMPORAL edges
        - Entity signals → ENTITY edges
        - Relation signals → SEMANTIC edges
        - Causal signals → CAUSAL edges (NEW: now creates traversable edges)

        Args:
            node: Node to link from
            wave: Wave with signals
            session_key: Session identifier

        Returns:
            Number of edges created
        """
        edges_created = 0

        # Get signals by type
        temporal_signals = wave.get_temporal_signals() if hasattr(wave, 'get_temporal_signals') else []
        entity_signals = wave.get_entity_signals() if hasattr(wave, 'get_entity_signals') else []
        relation_signals = wave.get_relation_signals() if hasattr(wave, 'get_relation_signals') else []

        # Store signal metadata on node
        if temporal_signals:
            node.metadata['temporal_signals'] = [
                {'raw': s.raw_expression, 'resolved': s.resolved_date.isoformat() if s.resolved_date else None}
                for s in temporal_signals
            ]

        if entity_signals:
            # Add entity IDs to node
            entity_names = [s.entity_name for s in entity_signals if s.entity_name]
            node.entity_ids = list(set(node.entity_ids + entity_names))
            await self._storage.save_node(node)

        # Process causal signals - create CAUSAL edges (not just metadata)
        causal_signals = wave.signals if hasattr(wave, 'signals') else []
        for signal in causal_signals:
            if hasattr(signal, 'signal_type') and signal.signal_type.value == 'causal':
                cause_text = getattr(signal, 'cause', '')
                effect_text = getattr(signal, 'effect', '')
                amplitude = getattr(signal, 'amplitude', 0.5)

                # Store metadata
                node.metadata['causal_signal'] = {
                    'cause': cause_text,
                    'effect': effect_text,
                }

                # NEW: Find effect node by embedding similarity and create causal edge
                if effect_text and hasattr(signal, 'embedding') and signal.embedding:
                    try:
                        # Search for nodes that might match the effect
                        effect_nodes = await self._storage.vector_search(
                            query_embedding=signal.embedding,
                            session_key=session_key,
                            limit=3
                        )

                        for effect_node, similarity in effect_nodes:
                            # Skip self and require reasonable similarity
                            if effect_node.node_id == node.node_id:
                                continue
                            if similarity < 0.5:
                                continue

                            # Check if causal edge already exists
                            existing = await self._storage.get_edge_between(
                                node.node_id, effect_node.node_id, EdgeType.CAUSAL
                            )

                            if existing:
                                # Strengthen existing edge
                                existing.activate()
                                await self._storage.save_edge(existing)
                            else:
                                # Create new causal edge
                                causal_edge = NeuralEdge(
                                    edge_id=generate_edge_id(),
                                    source_id=node.node_id,
                                    target_id=effect_node.node_id,
                                    edge_type=EdgeType.CAUSAL,
                                    base_weight=amplitude * similarity,  # Weight by signal strength and similarity
                                    confidence=similarity,
                                    metadata={
                                        'cause': cause_text,
                                        'effect': effect_text,
                                        'causal_type': 'causes',
                                    }
                                )
                                await self._storage.save_edge(causal_edge)
                                edges_created += 1
                                logger.debug(
                                    f"Created causal edge: {node.node_id[:8]} -> {effect_node.node_id[:8]} "
                                    f"(similarity={similarity:.2f})"
                                )
                                break  # Only link to best match

                    except Exception as e:
                        logger.warning(f"Failed to create causal edge: {e}")

                await self._storage.save_node(node)

        return edges_created

    # =========================================================================
    # KNOWLEDGE GRAPH INTEGRATION
    # =========================================================================

    async def process_extraction_result(
        self,
        extraction: ExtractionResult,
        session_key: str,
        source_node_id: str | None = None
    ) -> int:
        """Process knowledge graph extraction result.

        Creates ENTITY and SEMANTIC edges from extracted entities
        and relations.

        Args:
            extraction: KG extraction result with entities and relations
            session_key: Session identifier
            source_node_id: Optional source node to link from

        Returns:
            Number of edges created
        """
        if not self._config.enabled:
            return 0

        edges_created = 0

        # Find or create entity nodes
        entity_node_map: dict[str, str] = {}  # entity_uid -> node_id

        for entity in extraction.entities:
            # Check if we have a node with this entity
            existing = await self._storage.find_nodes_by_entities(
                [entity.uid], session_key
            )

            if existing:
                entity_node_map[entity.uid] = existing[0].node_id
            elif self._hierarchy:
                # Create a dedicated entity node (Layer 1)
                entity_node = await self._hierarchy.create_message_node(
                    content=f"Entity: {entity.name} ({entity.entity_type.value})",
                    session_key=session_key,
                    entity_ids=[entity.uid],
                    importance_score=entity.mekb_score,
                    entity_name=entity.name,
                    entity_type=entity.entity_type.value,
                )
                entity_node_map[entity.uid] = entity_node.node_id

        # Create relation edges
        for relation in extraction.relations:
            source_entity_node = entity_node_map.get(relation.source_entity_uid)
            target_entity_node = entity_node_map.get(relation.target_entity_uid)

            if source_entity_node and target_entity_node:
                edge = NeuralEdge(
                    edge_id=generate_edge_id(),
                    source_id=source_entity_node,
                    target_id=target_entity_node,
                    edge_type=EdgeType.SEMANTIC,
                    base_weight=relation.weight / 10.0,  # Normalize from 0-10 to 0-1
                    confidence=relation.confidence,
                    metadata={
                        'relation_type': relation.relation_type.value,
                        'kg_relation_uid': relation.uid,
                    }
                )
                await self._storage.save_edge(edge)
                edges_created += 1

        # Link source node to entities if provided
        if source_node_id:
            for entity_uid, entity_node_id in entity_node_map.items():
                if entity_node_id != source_node_id:
                    edge = NeuralEdge(
                        edge_id=generate_edge_id(),
                        source_id=source_node_id,
                        target_id=entity_node_id,
                        edge_type=EdgeType.ENTITY,
                        base_weight=0.8,
                        confidence=extraction.confidence,
                    )
                    await self._storage.save_edge(edge)
                    edges_created += 1

        self._total_edges_created += edges_created
        return edges_created

    # =========================================================================
    # RETRIEVAL
    # =========================================================================

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 20,
        layer_filter: list[NodeLayer] | None = None
    ) -> RetrievalResult:
        """Execute multi-stage neural retrieval.

        Uses semaphore to limit concurrent retrievals and waits for any
        ongoing consolidation to complete to prevent race conditions.

        Args:
            query_text: Query text
            query_embedding: Query embedding vector
            session_key: Session identifier
            limit: Maximum results
            layer_filter: Optional layer filter

        Returns:
            RetrievalResult with nodes and metadata
        """
        if not self._config.enabled or not self._config.use_neural_retrieval:
            return RetrievalResult(
                query_text=query_text,
                nodes=[],
                stages_executed=[],
                total_candidates_seen=0,
                co_activations_recorded=0,
                total_time_ms=0,
            )

        # Concurrency protection: limit concurrent retrievals and wait for consolidation
        async with self._retrieval_semaphore:
            # If consolidation is running, wait for it to finish
            if self._consolidation_lock.locked():
                async with self._consolidation_lock:
                    pass  # Just wait for consolidation to release

            self._total_retrievals += 1

            result = await self._retriever.retrieve(
                query_text=query_text,
                query_embedding=query_embedding,
                session_key=session_key,
                limit=limit,
                layer_filter=layer_filter
            )

            # NEW: Apply temporal dynamics (STDP and LTP decay) after retrieval
            if self._config.apply_temporal_dynamics and self._temporal and result.nodes:
                await self._apply_temporal_dynamics(session_key, result.nodes)

            return result

    async def retrieve_with_context(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 20
    ) -> dict[str, Any]:
        """Retrieve with full context including temporal and hierarchical info.

        Args:
            query_text: Query text
            query_embedding: Query embedding
            session_key: Session identifier
            limit: Maximum results

        Returns:
            Dictionary with nodes, temporal context, and hierarchy info
        """
        result = await self.retrieve(
            query_text, query_embedding, session_key, limit
        )

        context = {
            'nodes': result.nodes,
            'stages_executed': result.stages_executed,
            'total_time_ms': result.total_time_ms,
            'temporal_context': {},
            'hierarchy_summary': {},
        }

        # Get temporal context for top results
        if result.nodes and self._temporal:
            top_node = result.nodes[0][0]
            context['temporal_context'] = await self._retriever.retrieve_temporal_context(
                top_node, before_count=3, after_count=3
            )

        # Get hierarchy summary
        if self._hierarchy:
            context['hierarchy_summary'] = await self._hierarchy.get_layer_summary(session_key)

        return context

    async def _apply_temporal_dynamics(
        self,
        session_key: str,
        retrieved_nodes: list[tuple[NeuralNode, float]]
    ) -> None:
        """Apply STDP and LTP decay after retrieval.

        Implements Hebbian learning principle: "neurons that fire together wire together."
        When nodes are retrieved together, temporal edges between them are strengthened.

        Also applies probabilistic LTP decay to prevent runaway potentiation.

        Args:
            session_key: Session identifier
            retrieved_nodes: List of (node, score) tuples from retrieval
        """
        if not self._temporal or len(retrieved_nodes) < 2:
            return

        # Apply STDP on temporal edges between retrieved nodes
        # When nodes are co-retrieved, strengthen the temporal connection
        nodes = [node for node, _ in retrieved_nodes]
        stdp_applied = 0

        for i, node1 in enumerate(nodes):
            for node2 in nodes[i+1:]:
                # Only apply STDP if both have created_at timestamps
                if node1.created_at and node2.created_at:
                    # Compute time delta in milliseconds
                    delta_ms = (node2.created_at - node1.created_at).total_seconds() * 1000

                    try:
                        # Apply STDP (strengthens edge if pre fires before post)
                        success = await self._temporal.apply_stdp(
                            node1.node_id, node2.node_id, delta_ms
                        )
                        if success:
                            stdp_applied += 1
                    except Exception as e:
                        # STDP failure is non-critical, just log
                        logger.debug(f"STDP application failed: {e}")

        if stdp_applied > 0:
            logger.debug(f"Applied STDP to {stdp_applied} temporal edges")

        # Probabilistic LTP decay (don't run every retrieval to save compute)
        if random.random() < self._config.temporal_dynamics_probability:
            try:
                decayed = await self._temporal.decay_ltp(session_key)
                if decayed > 0:
                    logger.debug(f"LTP decay applied to {decayed} edges")
            except Exception as e:
                logger.warning(f"LTP decay failed: {e}")

    # =========================================================================
    # CONSOLIDATION
    # =========================================================================

    async def run_consolidation(self, session_key: str) -> ConsolidationResult:
        """Run manual consolidation cycle.

        Uses lock to prevent concurrent consolidation and retrieval race conditions.

        Args:
            session_key: Session identifier

        Returns:
            ConsolidationResult with statistics
        """
        if not self._config.consolidation_enabled or not self._consolidation:
            return ConsolidationResult(
                session_key=session_key,
                edges_pruned=0,
                nodes_merged=0,
                hubs_created=0,
                nodes_promoted=0,
                nodes_evicted=0,
                total_time_ms=0,
            )

        # Concurrency protection: exclusive lock during consolidation
        async with self._consolidation_lock:
            return await self._consolidation.run_consolidation_cycle(session_key)

    async def start_auto_consolidation(self, session_key: str) -> None:
        """Start background consolidation task.

        Args:
            session_key: Session identifier
        """
        if self._consolidation_task is not None:
            return

        async def consolidation_loop():
            while not self._closed:
                try:
                    await asyncio.sleep(self._config.auto_consolidation_interval_seconds)
                    if not self._closed and self._consolidation:
                        await self._consolidation.run_consolidation_cycle(session_key)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Consolidation cycle error: {e}")

        self._consolidation_task = asyncio.create_task(consolidation_loop())
        logger.info(f"Started auto-consolidation for session {session_key}")

    async def stop_auto_consolidation(self) -> None:
        """Stop background consolidation task."""
        if self._consolidation_task:
            self._consolidation_task.cancel()
            try:
                await self._consolidation_task
            except asyncio.CancelledError:
                pass
            self._consolidation_task = None
            logger.info("Stopped auto-consolidation")

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def get_statistics(self, session_key: str) -> dict[str, Any]:
        """Get comprehensive statistics.

        Args:
            session_key: Session identifier

        Returns:
            Statistics dictionary
        """
        stats = await self._storage.get_session_statistics(session_key)

        stats.update({
            'service': {
                'enabled': self._config.enabled,
                'total_nodes_created': self._total_nodes_created,
                'total_edges_created': self._total_edges_created,
                'total_retrievals': self._total_retrievals,
            }
        })

        if self._consolidation:
            consolidation_stats = await self._consolidation.get_consolidation_statistics(session_key)
            stats['consolidation'] = consolidation_stats

        if self._hierarchy:
            hierarchy_summary = await self._hierarchy.get_layer_summary(session_key)
            stats['hierarchy'] = hierarchy_summary

        return stats

    async def clear_session(self, session_key: str) -> int:
        """Clear all neural graph data for a session.

        Args:
            session_key: Session identifier

        Returns:
            Number of items cleared
        """
        return await self._storage.clear_session(session_key)

    async def close(self) -> None:
        """Close the service and release resources."""
        self._closed = True
        await self.stop_auto_consolidation()
        logger.info("NeuralGraphService closed")

    # =========================================================================
    # INTEGRATION HELPERS
    # =========================================================================

    def get_episode_node_ids(self, session_key: str) -> list[str]:
        """Get mapping of episode UIDs to node IDs (sync wrapper for async)."""
        # This would be used for linking existing episode results
        # Implementation depends on storage indexing
        return []

    async def link_to_existing_memories(
        self,
        node_id: str,
        memory_ids: list[str],
        edge_type: EdgeType = EdgeType.SEMANTIC
    ) -> int:
        """Create edges linking a node to existing memory IDs.

        Used to connect neural nodes to existing LTM/STM memories.

        Args:
            node_id: Source node ID
            memory_ids: Target memory IDs to link to
            edge_type: Type of edge to create

        Returns:
            Number of edges created
        """
        edges_created = 0

        for memory_id in memory_ids:
            # Check if target exists as a node
            target = await self._storage.get_node(memory_id)
            if target:
                edge = NeuralEdge(
                    edge_id=generate_edge_id(),
                    source_id=node_id,
                    target_id=memory_id,
                    edge_type=edge_type,
                    base_weight=0.5,
                )
                await self._storage.save_edge(edge)
                edges_created += 1

        return edges_created

    # =========================================================================
    # EXPOSED TEMPORAL DYNAMICS
    # =========================================================================

    async def apply_stdp(
        self,
        source_id: str,
        target_id: str,
        time_delta_ms: float
    ) -> bool:
        """Apply spike-timing dependent plasticity to temporal edge.

        STDP strengthens connections where the pre-synaptic neuron fires
        before the post-synaptic neuron (causal relationship).

        Args:
            source_id: Pre-synaptic node ID
            target_id: Post-synaptic node ID
            time_delta_ms: Time difference in milliseconds (post - pre)

        Returns:
            True if STDP was applied successfully
        """
        if not self._temporal:
            return False
        return await self._temporal.apply_stdp(source_id, target_id, time_delta_ms)

    async def get_temporal_summation(self, node_id: str) -> float:
        """Get temporal summation of activation signals for a node.

        Computes the cumulative signal strength from recent activations,
        modeling how biological neurons integrate signals over time.

        Args:
            node_id: Node to compute summation for

        Returns:
            Summation value (higher = more recent/stronger activations)
        """
        if not self._temporal:
            return 0.0

        node = await self._storage.get_node(node_id)
        if node:
            return await self._temporal.temporal_summation(node)
        return 0.0

    async def analyze_temporal_patterns(self, session_key: str) -> list[dict]:
        """Detect recurring temporal patterns in the graph.

        Finds sequences of nodes that frequently activate together,
        which can reveal narrative structures or habits.

        Args:
            session_key: Session identifier

        Returns:
            List of pattern dictionaries with sequence and frequency info
        """
        if not self._temporal:
            return []
        return await self._temporal.find_temporal_patterns(session_key)

    async def get_refractory_state(self, node_id: str) -> dict[str, Any]:
        """Get the refractory state of a node.

        Returns whether the node is in refractory period (recently activated)
        and when it will be available again.

        Args:
            node_id: Node to check

        Returns:
            Dictionary with 'in_refractory', 'remaining_ms', 'last_activation'
        """
        if not self._temporal:
            return {"in_refractory": False, "remaining_ms": 0}

        node = await self._storage.get_node(node_id)
        if not node:
            return {"in_refractory": False, "remaining_ms": 0}

        in_refractory = await self._temporal.is_in_refractory(node)
        remaining = await self._temporal.get_refractory_remaining(node)

        return {
            "in_refractory": in_refractory,
            "remaining_ms": remaining,
            "last_activation": node.last_activated.isoformat() if node.last_activated else None
        }

    # =========================================================================
    # EXPOSED HEAT MANAGEMENT
    # =========================================================================

    async def boost_node_heat(self, node_id: str, amount: float = 0.5) -> float:
        """Manually boost a node's heat score.

        Use this to mark nodes as important or to prevent eviction.

        Args:
            node_id: Node to boost
            amount: Amount to add to heat score

        Returns:
            New heat score after boost
        """
        if not self._consolidation:
            return 0.0
        return await self._consolidation.boost_heat(node_id, amount)

    async def decay_all_heat(self, session_key: str) -> int:
        """Apply heat decay to all nodes in a session.

        Heat naturally decays over time if nodes aren't accessed.
        This can be called manually to age the graph.

        Args:
            session_key: Session identifier

        Returns:
            Number of nodes that had heat decayed
        """
        if not self._consolidation:
            return 0
        return await self._consolidation.decay_all_heat(session_key)

    async def get_nodes_by_heat(
        self,
        session_key: str,
        min_heat: float = 0.0,
        max_heat: float = 10.0,
        limit: int = 50
    ) -> list[tuple[NeuralNode, float]]:
        """Get nodes filtered by heat score range.

        Useful for finding hot (frequently accessed) or cold (stale) nodes.

        Args:
            session_key: Session identifier
            min_heat: Minimum heat score
            max_heat: Maximum heat score
            limit: Maximum nodes to return

        Returns:
            List of (node, heat_score) tuples sorted by heat descending
        """
        nodes = await self._storage.get_nodes_by_session(session_key)
        filtered = [
            (node, node.heat_score)
            for node in nodes
            if min_heat <= node.heat_score <= max_heat
        ]
        filtered.sort(key=lambda x: x[1], reverse=True)
        return filtered[:limit]

    # =========================================================================
    # EXPOSED GATE TUNING
    # =========================================================================

    def adapt_gate_threshold(
        self,
        candidate_count: int,
        target_count: int = 50
    ) -> None:
        """Adapt gating thresholds based on retrieval performance.

        If retrieval returns too many candidates, increase threshold.
        If too few, decrease threshold. This provides automatic tuning.

        Args:
            candidate_count: Number of candidates from last retrieval
            target_count: Desired number of candidates
        """
        # Check if gate registry supports adaptation
        if hasattr(self._gate_registry, 'adapt_threshold'):
            self._gate_registry.adapt_threshold(candidate_count, target_count)
        else:
            logger.debug("Gate registry does not support threshold adaptation")

    def set_gate_strictness(self, strictness: str = "balanced") -> None:
        """Set overall gate strictness level.

        Args:
            strictness: One of "strict", "balanced", "permissive"
        """
        from .gating import (
            create_strict_registry,
            create_balanced_registry,
            create_permissive_registry
        )

        if strictness == "strict":
            self._gate_registry = create_strict_registry()
        elif strictness == "permissive":
            self._gate_registry = create_permissive_registry()
        else:
            self._gate_registry = create_balanced_registry()

        # Update retriever's gate registry reference
        if hasattr(self._retriever, '_gate_registry'):
            self._retriever._gate_registry = self._gate_registry

        logger.info(f"Gate strictness set to: {strictness}")
