"""Multi-stage neural retriever for Neural Memory Graph.

Implements the multi-stage retrieval pipeline (Image 6: Algorithmic Flow):
1. Fast approximate recall (vector similarity)
2. Entity graph expansion
3. Temporal chain following
4. Hierarchy traversal
5. Co-activation boosting with LTP

The pipeline enables:
- Efficient retrieval through staged filtering
- Multi-hop reasoning through graph traversal
- Temporal context through chain following
- Learning through co-activation recording
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .data_types import (
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
    RetrievalResult,
    RetrievalStageConfig,
    cosine_similarity,
)
from .gating import EdgeGateRegistry, create_balanced_registry
from .pattern_completion import CA3PatternCompleter, PatternCompletionConfig
from .interference import InterferenceScorer, InterferenceType
from .wavefront import WavefrontPropagator
from .temporal_utils import MONTH_NAMES

if TYPE_CHECKING:
    from .storage import NeuralGraphStorage
    from .temporal import TemporalChainManager

logger = logging.getLogger(__name__)


@dataclass
class RetrieverConfig:
    """Configuration for the neural retriever."""

    # Stage configurations
    stages: list[RetrievalStageConfig] = field(default_factory=list)

    # Global settings
    final_limit: int = 20
    record_co_activations: bool = True
    min_score_threshold: float = 0.1

    # Hop decay - TUNED for better multi-hop signal preservation
    hop_decay_factor: float = 0.88  # UP from 0.8 - preserve signal across hops

    # Reranking - Phase 4 Fix: Increased weights for computed signals
    rerank_by_heat: bool = True
    heat_weight: float = 0.30  # UP from 0.2 - hot memories are more relevant
    importance_weight: float = 0.25  # UP from 0.15 - importance captures intrinsic node value

    # Wave amplitude integration - AMPLIFIED for aggressive wave routing
    # Wave amplitudes stored on nodes can boost retrieval based on query type
    use_wave_amplitudes: bool = True
    wave_amplitude_weight: float = 0.18  # UP from 0.1 - trust wave signals more

    # CRITICAL: Hybrid retrieval (embedding + keyword)
    # Pure embedding search fails for factual QA - need keyword matching
    use_keyword_boost: bool = True
    keyword_boost_weight: float = 0.70  # UP from 0.5 - keywords are fact anchors

    # CA3-STYLE PATTERN COMPLETION (Brain-Inspired)
    # Uses Hopfield attractor dynamics for pattern completion
    use_pattern_completion: bool = True
    pattern_completion_iterations: int = 4  # UP from 3 - one more gamma cycle

    # WAVEFRONT PROPAGATION (Phase 5 - Interference Wavefront)
    # Systolic-style diagonal propagation through TIME × HIERARCHY
    use_wavefront_propagation: bool = True
    wavefront_constructive_boost: float = 0.40  # Boost for constructive interference
    wavefront_destructive_penalty: float = 0.50  # Penalty for destructive interference

    def __post_init__(self):
        if not self.stages:
            self.stages = self._default_stages()

    def _default_stages(self) -> list[RetrievalStageConfig]:
        """Create default retrieval stages."""
        return [
            RetrievalStageConfig(
                name="fast_recall",
                max_candidates=200,
                edge_types=[],
                layer_filter=None,
                max_hops=0,
                score_threshold=0.25,  # DOWN from 0.3 - cast wider net
                use_vector_search=True,
            ),
            RetrievalStageConfig(
                name="entity_expansion",
                max_candidates=150,  # Phase 5 Fix: Increased to accommodate 3-hop expansion
                edge_types=[EdgeType.ENTITY, EdgeType.SEMANTIC],
                layer_filter=[NodeLayer.MESSAGE, NodeLayer.EPISODE],
                max_hops=3,  # Phase 5 Fix: Increased from 1 to match temporal_chain for distributed facts
                score_threshold=0.20,  # Phase 5 Fix: Slightly lowered for better recall
            ),
            RetrievalStageConfig(
                name="temporal_chain",
                max_candidates=50,
                edge_types=[EdgeType.TEMPORAL, EdgeType.CAUSAL],
                layer_filter=[NodeLayer.MESSAGE],
                max_hops=3,
                score_threshold=0.2,
            ),
            RetrievalStageConfig(
                name="hierarchy_traversal",
                max_candidates=30,
                edge_types=[EdgeType.HIERARCHY],
                layer_filter=None,
                max_hops=2,
                score_threshold=0.15,
            ),
            RetrievalStageConfig(
                name="co_activation_boost",
                max_candidates=20,
                edge_types=[EdgeType.CO_ACTIVATION],
                layer_filter=None,
                max_hops=1,
                score_threshold=0.3,
            ),
        ]


class NeuralRetriever:
    """Multi-stage retrieval pipeline (Image 6: Algorithmic Flow).

    Implements a staged retrieval process:
    1. Fast vector recall for initial candidates
    2. Entity/semantic expansion through edges
    3. Temporal chain following for context
    4. Hierarchy traversal for abstraction
    5. Co-activation boosting for learned associations

    Each stage uses gated edge traversal (Image 3) and
    records co-activations for LTP (Image 4).
    """

    def __init__(
        self,
        storage: NeuralGraphStorage,
        gate_registry: EdgeGateRegistry | None = None,
        temporal_manager: TemporalChainManager | None = None,
        config: RetrieverConfig | None = None
    ):
        """Initialize neural retriever.

        Args:
            storage: Neural graph storage backend
            gate_registry: Edge gate registry
            temporal_manager: Optional temporal chain manager
            config: Retriever configuration
        """
        self._storage = storage
        self._gates = gate_registry or create_balanced_registry()
        self._temporal = temporal_manager
        self._config = config or RetrieverConfig()

        # Initialize CA3 pattern completer (brain-inspired)
        if self._config.use_pattern_completion:
            pc_config = PatternCompletionConfig(
                max_iterations=self._config.pattern_completion_iterations
            )
            self._pattern_completer = CA3PatternCompleter(storage, pc_config)
        else:
            self._pattern_completer = None

        # Initialize wavefront propagator (Phase 5 - Interference Wavefront)
        if self._config.use_wavefront_propagation:
            self._wavefront_propagator = WavefrontPropagator(
                storage=storage,
                temporal_decay=0.85,
                hierarchy_decay=0.92,
                min_activation=0.15
            )
        else:
            self._wavefront_propagator = None

    # =========================================================================
    # MAIN RETRIEVAL
    # =========================================================================

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int | None = None,
        layer_filter: list[NodeLayer] | None = None,
        query_wave_amplitudes: dict[str, float] | None = None
    ) -> RetrievalResult:
        """Execute multi-stage retrieval pipeline.

        Args:
            query_text: Query text
            query_embedding: Query embedding vector
            session_key: Session identifier
            limit: Maximum results (overrides config)
            layer_filter: Optional layer filter for final results
            query_wave_amplitudes: Optional wave amplitudes for query (e.g. from wave memory)

        Returns:
            RetrievalResult with ranked nodes and metadata
        """
        start_time = time.perf_counter()
        final_limit = limit or self._config.final_limit

        # Extract query keywords for hybrid retrieval
        query_keywords = self._extract_keywords(query_text) if self._config.use_keyword_boost else set()
        query_temporal_tokens, query_target_date = self._extract_temporal_tokens(query_text)

        # Track candidates across stages
        candidates: dict[str, tuple[NeuralNode, float]] = {}
        stages_executed: list[str] = []
        total_candidates_seen = 0

        # =====================================================================
        # PHASE I: ENTITY PRE-POPULATION + EDGE TRAVERSAL (Wave-Routed Retrieval)
        # =====================================================================
        # If query has entity wave amplitude, pre-populate candidates
        # with entity matches BEFORE Stage 1. Then TRAVERSE ENTITY edges
        # to find related memories (horizontal linking in action).
        # =====================================================================
        if query_wave_amplitudes and query_wave_amplitudes.get("entity", 0) > 0.3:
            query_entities = self._extract_entities(query_text)
            if query_entities:
                # Step 1: Find nodes by entity index
                entity_matches = await self._storage.find_nodes_by_entities(
                    query_entities, session_key
                )
                for node in entity_matches[:50]:
                    candidates[node.node_id] = (node, 0.68)  # Base score for entity match (UP from 0.6)
                    total_candidates_seen += 1

                # Step 2: CRITICAL - Follow ENTITY edges to find related memories
                # This is the horizontal linking traversal
                # BUG FIX: Must check BOTH directions since entity edges use canonical ordering
                expanded_via_edges = set()
                for node in entity_matches[:30]:  # Top 30 seeds
                    # Get edges in BOTH directions (entity edges use min/max canonical ordering)
                    edges_from = await self._storage.get_edges_from(
                        node.node_id, edge_types=[EdgeType.ENTITY]
                    )
                    edges_to = await self._storage.get_edges_to(
                        node.node_id, edge_types=[EdgeType.ENTITY]
                    )
                    all_entity_edges = edges_from[:10] + edges_to[:10]

                    for edge in all_entity_edges:
                        # Determine the "other" node in the edge
                        other_id = edge.target_id if edge.source_id == node.node_id else edge.source_id
                        if other_id not in candidates and other_id not in expanded_via_edges:
                            target = await self._storage.get_node(other_id)
                            if target:
                                # Score based on edge weight and parent score
                                edge_score = 0.5 * edge.effective_weight
                                candidates[other_id] = (target, edge_score)
                                expanded_via_edges.add(other_id)
                                total_candidates_seen += 1

                if entity_matches or expanded_via_edges:
                    stages_executed.append("entity_prepopulation")
                    logger.debug(
                        f"Entity pre-population: {len(entity_matches[:50])} direct + {len(expanded_via_edges)} via edges"
                    )

                # =====================================================================
                # CRITICAL FIX: TEMPORAL-ENTITY LINKING
                # =====================================================================
                # For queries like "When did Caroline do X?", we have BOTH high entity
                # AND high temporal. After finding entity matches, traverse TEMPORAL
                # edges to find time-related memories about that entity.
                # This bridges entity→time, solving the 17% temporal accuracy.
                # =====================================================================
                temporal_amp = query_wave_amplitudes.get("temporal", 0)
                if temporal_amp > 0.3 and entity_matches:
                    temporal_from_entity = set()
                    for node in entity_matches[:20]:  # Top entity matches
                        # Get temporal edges from entity-matched nodes
                        temp_edges_from = await self._storage.get_edges_from(
                            node.node_id, edge_types=[EdgeType.TEMPORAL]
                        )
                        temp_edges_to = await self._storage.get_edges_to(
                            node.node_id, edge_types=[EdgeType.TEMPORAL]
                        )
                        all_temp_edges = temp_edges_from[:5] + temp_edges_to[:5]

                        for edge in all_temp_edges:
                            other_id = edge.target_id if edge.source_id == node.node_id else edge.source_id
                            if other_id not in candidates and other_id not in temporal_from_entity:
                                target = await self._storage.get_node(other_id)
                                if target:
                                    # High score for temporal-linked entity content
                                    temp_entity_score = 0.65 * edge.effective_weight
                                    candidates[other_id] = (target, temp_entity_score)
                                    temporal_from_entity.add(other_id)
                                    total_candidates_seen += 1

                    if temporal_from_entity:
                        stages_executed.append("temporal_entity_bridge")
                        logger.debug(
                            f"Temporal-entity bridge: {len(temporal_from_entity)} nodes"
                        )

        # Store query info for use in stages and reranking
        self._query_wave_amplitudes = query_wave_amplitudes
        self._current_query_text = query_text

        # =====================================================================
        # PHASE III: FACTUAL QUERY EXPANSION (Wave-Routed Retrieval)
        # =====================================================================
        # For factual queries (high entity, low complexity), expand the
        # candidate pool to catch more potential matches.
        # =====================================================================
        is_factual = self._is_factual_query(query_wave_amplitudes)

        # Execute stages sequentially
        for stage in self._config.stages:
            stage_start = time.perf_counter()

            # Adjust Stage 1 for factual queries
            effective_max = stage.max_candidates
            effective_threshold = stage.score_threshold
            if is_factual and stage.use_vector_search:
                effective_max = 300  # Up from 200
                effective_threshold = 0.2  # Down from 0.3

            if stage.use_vector_search:
                # Stage 1: Fast hybrid recall (vector + keyword)
                # Create modified stage config for factual queries
                effective_stage = RetrievalStageConfig(
                    name=stage.name,
                    max_candidates=effective_max,
                    edge_types=stage.edge_types,
                    layer_filter=stage.layer_filter,
                    max_hops=stage.max_hops,
                    score_threshold=effective_threshold,
                    use_vector_search=stage.use_vector_search,
                )
                stage_results = await self._fast_recall(
                    query_embedding, session_key, effective_stage, query_keywords
                )
            else:
                # Graph expansion stages
                stage_results = await self._expand_stage(
                    query_embedding, candidates, stage, session_key
                )

            # Merge results
            for node, score in stage_results:
                total_candidates_seen += 1
                if node.node_id in candidates:
                    _, old_score = candidates[node.node_id]
                    candidates[node.node_id] = (node, max(old_score, score))
                else:
                    candidates[node.node_id] = (node, score)

            stages_executed.append(stage.name)

            stage_time = (time.perf_counter() - stage_start) * 1000
            logger.debug(
                f"Stage '{stage.name}': {len(stage_results)} results in {stage_time:.1f}ms"
            )

        # =====================================================================
        # CA3-STYLE PATTERN COMPLETION (Brain-Inspired Attractor Dynamics)
        # =====================================================================
        # Use Hopfield-style spreading activation to complete partial patterns.
        # Seeds (current candidates) activate related memories through
        # recurrent connections, converging to stable attractor states.
        # =====================================================================
        if self._pattern_completer and candidates:
            pc_start = time.perf_counter()

            # Use top candidates as seeds for pattern completion
            seed_nodes = sorted(
                candidates.values(),
                key=lambda x: x[1],
                reverse=True
            )[:30]  # Top 30 seeds

            # Get all nodes for pattern completion
            all_nodes = await self._storage.get_nodes_by_session(session_key)

            # Run pattern completion
            completed = await self._pattern_completer.complete_pattern(
                seed_nodes, session_key, all_nodes
            )

            # Merge completed patterns back into candidates
            for node, activation in completed:
                if node.node_id in candidates:
                    _, old_score = candidates[node.node_id]
                    # Blend original score with activation
                    new_score = 0.6 * old_score + 0.4 * activation
                    candidates[node.node_id] = (node, new_score)
                else:
                    # Add new nodes found through pattern completion
                    candidates[node.node_id] = (node, activation * 0.8)
                    total_candidates_seen += 1

            stages_executed.append("ca3_pattern_completion")

            pc_time = (time.perf_counter() - pc_start) * 1000
            logger.debug(
                f"CA3 pattern completion: {len(seed_nodes)} seeds -> {len(completed)} completed in {pc_time:.1f}ms"
            )

        # =====================================================================
        # PHASE VII: WAVEFRONT PROPAGATION (Interference Wavefront)
        # =====================================================================
        # Systolic-style diagonal propagation through TIME × HIERARCHY.
        # Uses wave interference to identify constructive/destructive matches.
        # Constructive interference = high activation = likely match
        # Destructive interference = low activation = suppress
        # =====================================================================
        if self._wavefront_propagator and candidates and query_wave_amplitudes:
            wf_start = time.perf_counter()

            # Use top candidates as seeds for wavefront propagation
            seed_nodes = [
                node for node, score in sorted(
                    candidates.values(),
                    key=lambda x: x[1],
                    reverse=True
                )[:20]  # Top 20 seeds
            ]

            # Propagate wavefront through the graph
            wf_result = await self._wavefront_propagator.propagate(
                query_wave=query_wave_amplitudes,
                seed_nodes=seed_nodes,
                max_time_hops=5,
                max_hierarchy_hops=2
            )

            # Merge wavefront results with interference-based scoring
            for wf_node in wf_result.nodes:
                node = wf_node.node
                activation = wf_node.activation
                itype = wf_node.interference_type

                if node.node_id in candidates:
                    _, old_score = candidates[node.node_id]

                    # Apply interference-based adjustment
                    if itype == InterferenceType.CONSTRUCTIVE:
                        # Boost constructive interference
                        new_score = old_score + self._config.wavefront_constructive_boost * activation
                    elif itype == InterferenceType.DESTRUCTIVE:
                        # Penalize destructive interference
                        new_score = old_score * self._config.wavefront_destructive_penalty
                    else:
                        # Partial - slight boost
                        new_score = old_score + 0.15 * activation

                    candidates[node.node_id] = (node, new_score)
                else:
                    # Add new nodes found through wavefront propagation
                    base_score = 0.5 * activation
                    if itype == InterferenceType.CONSTRUCTIVE:
                        base_score += self._config.wavefront_constructive_boost * 0.5
                    candidates[node.node_id] = (node, base_score)
                    total_candidates_seen += 1

            stages_executed.append("wavefront_propagation")

            wf_time = (time.perf_counter() - wf_start) * 1000
            logger.debug(
                f"Wavefront propagation: {len(seed_nodes)} seeds -> "
                f"{len(wf_result.nodes)} activated "
                f"(constructive={wf_result.constructive_count}, "
                f"destructive={wf_result.destructive_count}) "
                f"in {wf_time:.1f}ms"
            )

        # Apply final reranking with keyword boost
        ranked = await self._final_rerank(
            candidates, query_embedding, query_wave_amplitudes, query_keywords
        )

        # Apply layer filter
        if layer_filter:
            ranked = [(n, s) for n, s in ranked if n.layer in layer_filter]

        # Trim to limit
        final_results = ranked[:final_limit]

        # Record co-activations for LTP
        co_activations = 0
        if self._config.record_co_activations and len(final_results) > 1:
            co_activations = await self._record_co_activations(
                [n for n, _ in final_results]
            )

        total_time = (time.perf_counter() - start_time) * 1000

        result = RetrievalResult(
            query_text=query_text,
            nodes=final_results,
            stages_executed=stages_executed,
            total_candidates_seen=total_candidates_seen,
            co_activations_recorded=co_activations,
            total_time_ms=total_time,
        )

        logger.info(
            f"Retrieved {len(final_results)} nodes in {total_time:.1f}ms "
            f"(candidates={total_candidates_seen}, co-activations={co_activations})"
        )

        return result

    # =========================================================================
    # STAGE 1: FAST RECALL
    # =========================================================================

    async def _fast_recall(
        self,
        query_embedding: list[float],
        session_key: str,
        stage: RetrievalStageConfig,
        query_keywords: set[str] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Stage 1: Hybrid recall (vector + keyword).

        CRITICAL FIX: Pure vector similarity misses factual matches.
        Now does BOTH vector search AND keyword search, merging results.

        Args:
            query_embedding: Query embedding
            session_key: Session identifier
            stage: Stage configuration
            query_keywords: Keywords for hybrid search

        Returns:
            List of (node, score) tuples
        """
        # Vector-based retrieval
        vector_results = await self._storage.vector_search(
            query_embedding,
            session_key,
            limit=stage.max_candidates,
            layer_filter=stage.layer_filter
        )

        # Build candidate dict from vector results
        candidates: dict[str, tuple[NeuralNode, float]] = {}
        for node, score in vector_results:
            if score >= stage.score_threshold:
                candidates[node.node_id] = (node, score)

        # HYBRID: Also search by keywords if enabled
        if self._config.use_keyword_boost and query_keywords:
            all_nodes = await self._storage.get_nodes_by_session(session_key)

            for node in all_nodes:
                # Apply layer filter
                if stage.layer_filter and node.layer not in stage.layer_filter:
                    continue

                # Compute keyword score
                keyword_score = self._compute_keyword_score(node.content, query_keywords)

                if keyword_score > 0.2:  # At least 20% keyword match
                    if node.node_id in candidates:
                        # Boost existing candidate
                        _, old_score = candidates[node.node_id]
                        new_score = old_score + self._config.keyword_boost_weight * keyword_score
                        candidates[node.node_id] = (node, new_score)
                    else:
                        # Add new candidate from keyword match
                        # Use keyword score as base (scaled)
                        base_score = 0.3 + keyword_score * 0.5
                        candidates[node.node_id] = (node, base_score)

        # =====================================================================
        # PHASE II: TEMPORAL FILTERING + EDGE TRAVERSAL (Wave-Routed Retrieval)
        # =====================================================================
        # For temporal queries (high temporal wave amplitude):
        # 1. Boost nodes with temporal content
        # 2. TRAVERSE TEMPORAL edges to find related events (horizontal linking)
        # This helps "When did X do Y?" type questions find relevant memories.
        # =====================================================================
        if hasattr(self, '_query_wave_amplitudes') and self._query_wave_amplitudes:
            temporal_amp = self._query_wave_amplitudes.get("temporal", 0)
            if temporal_amp > 0.3:  # Lowered from 0.5 for broader activation
                # Step 0: Boost nodes whose temporal metadata matches query tokens/targets
                if query_temporal_tokens:
                    for node_id, (node, score) in list(candidates.items()):
                        node_meta = node.metadata or {}
                        node_tokens = set(node_meta.get("temporal_tokens", []) or node_meta.get("date_tokens", []) or [])
                        overlap = len(node_tokens & query_temporal_tokens)

                        # Also consider exact/near date proximity using resolved_date/created_at
                        node_date = self._parse_node_date(node)
                        proximity_boost = 0.0
                        if query_target_date and node_date:
                            delta_days = abs((node_date.date() - query_target_date.date()).days)
                            if delta_days <= 1:
                                proximity_boost = 0.25
                            elif delta_days <= 7:
                                proximity_boost = 0.15
                            elif delta_days <= 31:
                                proximity_boost = 0.08

                        if overlap or proximity_boost:
                            boost = 0.12 * overlap + proximity_boost
                            candidates[node_id] = (node, score + boost)

                # Step 1: Boost/penalize based on temporal wave amplitude
                for node_id, (node, score) in list(candidates.items()):
                    node_temporal = node.wave_amplitudes.get("temporal", 0) if node.wave_amplitudes else 0
                    if node_temporal > 0.3:
                        # Boost nodes with temporal content
                        new_score = score + 0.25 * node_temporal
                        candidates[node_id] = (node, new_score)
                    elif node_temporal < 0.1:
                        # Penalize nodes without temporal content (but not too harshly)
                        new_score = score * 0.8
                        candidates[node_id] = (node, new_score)

                # Step 2: CRITICAL - Follow TEMPORAL edges to find related events
                # This is horizontal linking in action for temporal queries
                temporal_expanded = set()
                top_temporal_nodes = [
                    (nid, n, s) for nid, (n, s) in candidates.items()
                    if n.wave_amplitudes and n.wave_amplitudes.get("temporal", 0) > 0.3
                ][:20]  # Top 20 temporal nodes as seeds

                for node_id, node, score in top_temporal_nodes:
                    # Get temporal edges (both directions for context)
                    edges_from = await self._storage.get_edges_from(
                        node_id, edge_types=[EdgeType.TEMPORAL]
                    )
                    edges_to = await self._storage.get_edges_to(
                        node_id, edge_types=[EdgeType.TEMPORAL]
                    )
                    all_temporal_edges = edges_from[:5] + edges_to[:5]

                    for edge in all_temporal_edges:
                        target_id = edge.target_id if edge.source_id == node_id else edge.source_id
                        if target_id not in candidates and target_id not in temporal_expanded:
                            target = await self._storage.get_node(target_id)
                            if target:
                                # Score based on edge weight and temporal relevance
                                edge_score = 0.4 * edge.effective_weight * score
                                candidates[target_id] = (target, edge_score)
                                temporal_expanded.add(target_id)

        # Sort and return top candidates
        sorted_candidates = sorted(
            candidates.values(),
            key=lambda x: x[1],
            reverse=True
        )

        return sorted_candidates[:stage.max_candidates]

    # =========================================================================
    # STAGES 2-5: GRAPH EXPANSION
    # =========================================================================

    async def _expand_stage(
        self,
        query_embedding: list[float],
        current_candidates: dict[str, tuple[NeuralNode, float]],
        stage: RetrievalStageConfig,
        session_key: str
    ) -> list[tuple[NeuralNode, float]]:
        """Execute a graph expansion stage with gated traversal.

        Args:
            query_embedding: Query embedding
            current_candidates: Current candidate set
            stage: Stage configuration
            session_key: Session identifier

        Returns:
            List of newly found (node, score) tuples
        """
        expanded: list[tuple[NeuralNode, float]] = []
        visited = set(current_candidates.keys())

        # Get seed nodes from current candidates
        seeds = sorted(
            current_candidates.values(),
            key=lambda x: x[1],
            reverse=True
        )[:50]  # Top 50 seeds

        # BFS expansion with gating
        frontier = list(seeds)

        for hop in range(stage.max_hops):
            if not frontier:
                break

            next_frontier: list[tuple[NeuralNode, float]] = []
            hop_decay = self._config.hop_decay_factor ** (hop + 1)

            for node, parent_score in frontier:
                # Get edges of relevant types
                edges = await self._storage.get_edges_from(
                    node.node_id, edge_types=stage.edge_types
                )

                for edge in edges:
                    if edge.target_id in visited:
                        continue

                    # Apply gating (Image 3)
                    gate_context = {
                        "query_embedding": query_embedding,
                        "parent_score": parent_score,
                    }
                    gate_value = self._gates.compute_gate(edge, gate_context)

                    if gate_value < stage.score_threshold:
                        continue

                    # Get target node
                    target = await self._storage.get_node(edge.target_id)
                    if target is None:
                        continue

                    # Layer filter
                    if stage.layer_filter and target.layer not in stage.layer_filter:
                        continue

                    # Compute score
                    score = parent_score * gate_value * hop_decay

                    if score >= stage.score_threshold:
                        expanded.append((target, score))
                        next_frontier.append((target, score))
                        visited.add(target.node_id)

                        # Activate edge (LTP)
                        edge.activate()
                        await self._storage.save_edge(edge)

            # Prepare next hop
            frontier = sorted(
                next_frontier,
                key=lambda x: x[1],
                reverse=True
            )[:stage.max_candidates // (hop + 2)]

        return expanded[:stage.max_candidates]

    # =========================================================================
    # FINAL RERANKING
    # =========================================================================

    async def _final_rerank(
        self,
        candidates: dict[str, tuple[NeuralNode, float]],
        query_embedding: list[float],
        query_wave_amplitudes: dict[str, float] | None = None,
        query_keywords: set[str] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Final reranking with multiple signals.

        Combines:
        - Base retrieval score
        - Heat score (activity)
        - Importance score
        - Embedding similarity (recomputed)
        - Wave amplitude alignment
        - KEYWORD MATCHING (CRITICAL for factual QA)

        Args:
            candidates: Candidate nodes with scores
            query_embedding: Query embedding
            query_wave_amplitudes: Optional wave amplitudes from query
            query_keywords: Keywords extracted from query for hybrid retrieval

        Returns:
            Reranked list of (node, score) tuples
        """
        reranked: list[tuple[NeuralNode, float]] = []
        query_keywords = query_keywords or set()

        for node, base_score in candidates.values():
            final_score = base_score

            # Heat boost
            if self._config.rerank_by_heat:
                heat_normalized = min(1.0, node.heat_score / 10.0)
                final_score += self._config.heat_weight * heat_normalized

            # Importance boost
            final_score += self._config.importance_weight * node.importance_score

            # Embedding similarity (if available)
            if node.embedding:
                sim = cosine_similarity(query_embedding, node.embedding)
                final_score = final_score * 0.7 + sim * 0.3

            # Wave amplitude alignment boost
            if (
                self._config.use_wave_amplitudes
                and query_wave_amplitudes
                and node.wave_amplitudes
            ):
                wave_alignment = self._compute_wave_alignment(
                    query_wave_amplitudes, node.wave_amplitudes
                )
                final_score += self._config.wave_amplitude_weight * wave_alignment

            # CRITICAL: Keyword matching boost
            # This is essential for factual QA - pure embedding similarity misses exact matches
            if self._config.use_keyword_boost and query_keywords:
                keyword_score = self._compute_keyword_score(node.content, query_keywords)
                final_score += self._config.keyword_boost_weight * keyword_score

            # =================================================================
            # PHASE IV: ENTITY OVERLAP BOOSTING (Wave-Routed Retrieval)
            # =================================================================
            # Boost nodes that share entities with the query. This ensures
            # "What did Caroline do?" finds memories about Caroline.
            # =================================================================
            query_entities = []
            if hasattr(self, '_current_query_text') and self._current_query_text:
                query_entities = self._extract_entities(self._current_query_text)
                if query_entities and node.entity_ids:
                    # Match by entity name (case-insensitive)
                    query_entity_lower = {e.lower() for e in query_entities}
                    node_entity_lower = {e.lower() for e in node.entity_ids}
                    overlap = len(query_entity_lower & node_entity_lower)
                    if overlap > 0:
                        entity_boost = 0.35 * (overlap / len(query_entities))  # UP from 0.3
                        final_score += entity_boost

            # =================================================================
            # PHASE V: WAVE DIMENSION FILTERING (Aggressive Strike)
            # =================================================================
            # Penalize candidates that don't have signal in the query's
            # dominant wave dimensions. If query has high entity+temporal,
            # memories without those signals are noise.
            # =================================================================
            if query_wave_amplitudes:
                # Identify query's dominant dimensions (amplitude > 0.4)
                dominant_dims = [
                    k for k, v in query_wave_amplitudes.items()
                    if v > 0.4 and k in ('entity', 'temporal', 'action', 'spatial', 'relational')
                ]

                if dominant_dims and node.wave_amplitudes:
                    # Check if node has signal in at least one dominant dimension
                    has_signal = any(
                        node.wave_amplitudes.get(dim, 0) > 0.2 for dim in dominant_dims
                    )
                    if not has_signal:
                        # Penalize mismatched candidates
                        final_score *= 0.65  # Stronger penalty for wave mismatch

            # =================================================================
            # PHASE VI: SPEAKER-ENTITY BINDING (4D Transcendence)
            # =================================================================
            # When the query asks about a person (Caroline, Melanie), and
            # a memory is SPOKEN BY that person (producer_id), it's highly
            # relevant. This is the 4D binding - speaker IS the entity.
            # =================================================================
            if query_entities:
                query_entities_lower = {e.lower() for e in query_entities}
                # Check metadata for producer_id (speaker)
                node_metadata = node.metadata or {}
                speaker = node_metadata.get("producer_id", "")
                if not speaker:
                    # Also check speaker field directly on node if available
                    speaker = getattr(node, 'speaker', '') or ""
                speaker_lower = speaker.lower().strip()

                if speaker_lower and speaker_lower in query_entities_lower:
                    # Speaker IS the queried entity - high confidence boost
                    final_score += 0.35  # Strong boost for speaker match

            # Filter by minimum threshold
            if final_score >= self._config.min_score_threshold:
                reranked.append((node, final_score))

        # Sort by final score
        reranked.sort(key=lambda x: x[1], reverse=True)

        return reranked

    def _extract_entities(self, text: str) -> list[str]:
        """Extract entity names (proper nouns) from text.

        WAVE-ROUTED RETRIEVAL: Entity extraction for entity-first retrieval.
        Used to pre-populate candidates when query has high entity wave amplitude.

        Args:
            text: Input text (query)

        Returns:
            List of entity names (capitalized words likely to be proper nouns)
        """
        import re
        # Find capitalized words (likely proper nouns/entity names)
        # Pattern: word boundary, uppercase letter, lowercase letters
        words = re.findall(r'\b[A-Z][a-z]+\b', text)
        # Remove duplicates while preserving order
        seen = set()
        entities = []
        for w in words:
            if w not in seen:
                seen.add(w)
                entities.append(w)
        return entities

    def _is_factual_query(self, query_waves: dict[str, float] | None) -> bool:
        """Detect factual queries: high entity, low complexity.

        WAVE-ROUTED RETRIEVAL: Factual queries need larger candidate pools.

        Args:
            query_waves: Query wave amplitudes

        Returns:
            True if query appears to be factual/entity-centric
        """
        if not query_waves:
            return False
        return (
            query_waves.get("entity", 0) > 0.3 and  # Lowered from 0.5
            query_waves.get("emotional", 0) < 0.4 and  # Relaxed from 0.3
            query_waves.get("causal", 0) < 0.4  # Relaxed from 0.3
        )

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract important keywords from text for hybrid retrieval.

        Filters out common stop words and keeps meaningful terms.

        Args:
            text: Input text (query)

        Returns:
            Set of lowercase keywords
        """
        # Common stop words to filter out
        stop_words = {
            'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
            'ought', 'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
            'from', 'as', 'into', 'through', 'during', 'before', 'after',
            'above', 'below', 'between', 'under', 'again', 'further', 'then',
            'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all',
            'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor',
            'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just',
            'and', 'but', 'if', 'or', 'because', 'until', 'while', 'although',
            'though', 'what', 'which', 'who', 'whom', 'this', 'that', 'these',
            'those', 'am', 'it', 'its', 'itself', 'they', 'them', 'their',
            'theirs', 'themselves', 'you', 'your', 'yours', 'yourself', 'he',
            'him', 'his', 'himself', 'she', 'her', 'hers', 'herself', 'we',
            'us', 'our', 'ours', 'ourselves', 'i', 'me', 'my', 'myself', 'about',
        }

        # Tokenize: split on non-alphanumeric, lowercase
        import re
        tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())

        # Filter: remove stop words and very short words
        keywords = {
            token for token in tokens
            if token not in stop_words and len(token) > 2
        }

        return keywords

    def _compute_keyword_score(self, content: str, query_keywords: set[str]) -> float:
        """Compute keyword overlap score between content and query.

        Args:
            content: Node content text
            query_keywords: Keywords from query

        Returns:
            Score in [0, 1] based on keyword overlap
        """
        if not query_keywords:
            return 0.0

        content_lower = content.lower()

        # Count how many query keywords appear in content
        matches = sum(1 for kw in query_keywords if kw in content_lower)

        # Normalize by number of query keywords
        return matches / len(query_keywords)

    def _extract_temporal_tokens(self, text: str) -> tuple[set[str], datetime | None]:
        """Extract coarse temporal tokens and a target date from query text."""
        import re

        tokens: set[str] = set()
        target_date: datetime | None = None

        # Exact date YYYY-MM-DD or YYYY/MM/DD
        match = re.search(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', text)
        if match:
            y, mo, d = match.groups()
            try:
                target_date = datetime(int(y), int(mo), int(d))
                tokens.add(f"DATE_{int(y):04d}-{int(mo):02d}-{int(d):02d}")
                tokens.add(f"YEAR_{int(y):04d}")
                tokens.add(f"MONTH_{int(mo):02d}")
            except ValueError:
                pass

        # Year-month YYYY-MM or YYYY/MM
        match = re.search(r'\b(20\d{2})[-/](\d{1,2})\b', text)
        if match:
            y, mo = match.groups()
            try:
                tokens.add(f"YEAR_{int(y):04d}")
                tokens.add(f"MONTH_{int(mo):02d}")
                if not target_date:
                    target_date = datetime(int(y), int(mo), 1)
            except ValueError:
                pass

        # Month name + year
        match = re.search(
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b',
            text,
            re.IGNORECASE,
        )
        if match:
            month_name, y = match.groups()
            month_idx = MONTH_NAMES.get(month_name.lower())
            if month_idx:
                tokens.add(f"YEAR_{int(y):04d}")
                tokens.add(f"MONTH_{month_idx:02d}")
                tokens.add(f"MONTH_{month_name.upper()}")
                if not target_date:
                    target_date = datetime(int(y), month_idx, 1)

        # Standalone year
        years = re.findall(r'\b(20\d{2})\b', text)
        for y in years:
            tokens.add(f"YEAR_{int(y):04d}")
            if not target_date:
                try:
                    target_date = datetime(int(y), 1, 1)
                except ValueError:
                    pass

        return tokens, target_date

    def _parse_node_date(self, node: NeuralNode) -> datetime | None:
        """Parse a node's resolved_date or created_at into a datetime."""
        import re

        meta = node.metadata or {}
        resolved = meta.get("resolved_date")
        if isinstance(resolved, datetime):
            return resolved
        if isinstance(resolved, str):
            try:
                return datetime.fromisoformat(resolved)
            except ValueError:
                pass
            # Year-only
            if re.fullmatch(r"\d{4}", resolved):
                return datetime(int(resolved), 1, 1)
            # Month Year (e.g., "June 2023")
            match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", resolved)
            if match:
                month_name, year = match.groups()
                month_idx = MONTH_NAMES.get(month_name.lower())
                if month_idx:
                    return datetime(int(year), month_idx, 1)
            # Day Month Year (e.g., "7 May 2023")
            match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", resolved)
            if match:
                day, month_name, year = match.groups()
                month_idx = MONTH_NAMES.get(month_name.lower())
                if month_idx:
                    return datetime(int(year), month_idx, int(day))

        created = getattr(node, "created_at", None)
        if isinstance(created, datetime):
            return created

        return None

    def _compute_wave_alignment(
        self,
        query_waves: dict[str, float],
        node_waves: dict[str, float]
    ) -> float:
        """Compute wave amplitude alignment between query and node.

        Uses dot product normalized by vector magnitudes (cosine similarity
        in wave space). Aligns dimensions like 'temporal', 'entity', 'emotion'.

        Args:
            query_waves: Query wave amplitudes
            node_waves: Node wave amplitudes

        Returns:
            Alignment score in [0, 1]
        """
        # Get all wave dimensions present in either
        all_dims = set(query_waves.keys()) | set(node_waves.keys())

        if not all_dims:
            return 0.0

        # Compute dot product
        dot_product = sum(
            query_waves.get(dim, 0.0) * node_waves.get(dim, 0.0)
            for dim in all_dims
        )

        # Compute magnitudes
        query_mag = sum(v ** 2 for v in query_waves.values()) ** 0.5
        node_mag = sum(v ** 2 for v in node_waves.values()) ** 0.5

        if query_mag == 0 or node_mag == 0:
            return 0.0

        # Cosine similarity
        alignment = dot_product / (query_mag * node_mag)

        # Clamp to [0, 1] (negative alignment not useful for retrieval)
        return max(0.0, alignment)

    # =========================================================================
    # CO-ACTIVATION RECORDING (LTP)
    # =========================================================================

    async def _record_co_activations(
        self,
        retrieved_nodes: list[NeuralNode]
    ) -> int:
        """Record co-activation for LTP (Image 4: Hebbian learning).

        Nodes retrieved together strengthen their connections.

        OPTIMIZED: Uses canonical ordering (min_id, max_id) and batch operations.

        Args:
            retrieved_nodes: Nodes that were retrieved together

        Returns:
            Number of co-activation edges created/strengthened
        """
        if self._temporal:
            return await self._temporal.record_co_activation(
                retrieved_nodes, context="retrieval"
            )

        # Manual recording without temporal manager
        from .data_types import generate_edge_id

        if len(retrieved_nodes) < 2:
            return 0

        node_ids = [n.node_id for n in retrieved_nodes]

        # Build canonical pairs (min_id, max_id) to ensure single direction
        pairs: list[tuple[str, str]] = []
        for i, id1 in enumerate(node_ids):
            for id2 in node_ids[i + 1:]:
                canonical = (min(id1, id2), max(id1, id2))
                pairs.append(canonical)

        if not pairs:
            return 0

        # Batch fetch existing edges
        existing_edges = await self._storage.get_edges_batch(pairs, EdgeType.CO_ACTIVATION)

        # Build edges to save
        edges_to_save: list[NeuralEdge] = []
        for (id1, id2), edge in zip(pairs, existing_edges):
            if edge is None:
                edge = NeuralEdge(
                    edge_id=generate_edge_id(),
                    source_id=id1,
                    target_id=id2,
                    edge_type=EdgeType.CO_ACTIVATION,
                    base_weight=0.3,  # Start stronger (was 0.1)
                )

            edge.activate()
            edges_to_save.append(edge)

        # Batch save
        await self._storage.save_edges_batch(edges_to_save)

        return len(edges_to_save)

    # =========================================================================
    # SPECIALIZED RETRIEVERS
    # =========================================================================

    async def retrieve_by_entities(
        self,
        entity_ids: list[str],
        session_key: str,
        limit: int = 20
    ) -> list[tuple[NeuralNode, float]]:
        """Retrieve nodes by entity references.

        Args:
            entity_ids: Entity IDs to search for
            session_key: Session identifier
            limit: Maximum results

        Returns:
            List of (node, score) tuples
        """
        matching = await self._storage.find_nodes_by_entities(entity_ids, session_key)

        # Score by entity overlap
        entity_set = set(entity_ids)
        scored = []
        for node in matching:
            overlap = len(entity_set & set(node.entity_ids))
            score = overlap / len(entity_ids) if entity_ids else 0
            scored.append((node, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    async def retrieve_temporal_context(
        self,
        node: NeuralNode,
        before_count: int = 3,
        after_count: int = 3
    ) -> dict[str, list[tuple[NeuralNode, float]]]:
        """Retrieve temporal context around a node.

        Args:
            node: Center node
            before_count: Number of preceding nodes
            after_count: Number of following nodes

        Returns:
            Dictionary with 'before' and 'after' node lists
        """
        if self._temporal:
            context = await self._temporal.get_temporal_context(
                node, before_count, after_count
            )
            # Add scores
            return {
                "before": [(n, 0.8 ** (i + 1)) for i, n in enumerate(context["before"])],
                "after": [(n, 0.8 ** (i + 1)) for i, n in enumerate(context["after"])],
            }

        # Manual retrieval without temporal manager
        result: dict[str, list[tuple[NeuralNode, float]]] = {
            "before": [],
            "after": []
        }

        # Get temporal edges
        edges_to = await self._storage.get_edges_to(
            node.node_id, edge_types=[EdgeType.TEMPORAL]
        )
        edges_from = await self._storage.get_edges_from(
            node.node_id, edge_types=[EdgeType.TEMPORAL]
        )

        # Before nodes (predecessors)
        for edge in sorted(edges_to, key=lambda e: e.effective_weight, reverse=True)[:before_count]:
            pred = await self._storage.get_node(edge.source_id)
            if pred:
                result["before"].append((pred, edge.effective_weight))

        # After nodes (successors)
        for edge in sorted(edges_from, key=lambda e: e.effective_weight, reverse=True)[:after_count]:
            succ = await self._storage.get_node(edge.target_id)
            if succ:
                result["after"].append((succ, edge.effective_weight))

        return result

    async def retrieve_with_hierarchy(
        self,
        query_embedding: list[float],
        session_key: str,
        target_layer: NodeLayer = NodeLayer.EPISODE,
        limit: int = 10
    ) -> list[tuple[NeuralNode, float]]:
        """Retrieve at a specific hierarchy layer.

        First retrieves at message level, then traverses up
        to the target layer.

        Args:
            query_embedding: Query embedding
            session_key: Session identifier
            target_layer: Target hierarchy layer
            limit: Maximum results

        Returns:
            List of (node, score) tuples at target layer
        """
        # Get message-level results
        messages = await self._storage.vector_search(
            query_embedding,
            session_key,
            limit=limit * 3,
            layer_filter=[NodeLayer.MESSAGE]
        )

        if target_layer == NodeLayer.MESSAGE:
            return messages[:limit]

        # Traverse up to target layer
        target_nodes: dict[str, tuple[NeuralNode, float]] = {}

        for msg, score in messages:
            current = msg
            current_score = score

            while current.layer.value < target_layer.value:
                if current.parent_id is None:
                    break

                parent = await self._storage.get_node(current.parent_id)
                if parent is None:
                    break

                current = parent
                current_score *= 0.9  # Slight decay for hierarchy traversal

            if current.layer == target_layer:
                if current.node_id in target_nodes:
                    _, old_score = target_nodes[current.node_id]
                    target_nodes[current.node_id] = (current, max(old_score, current_score))
                else:
                    target_nodes[current.node_id] = (current, current_score)

        results = sorted(target_nodes.values(), key=lambda x: x[1], reverse=True)
        return results[:limit]

    # =========================================================================
    # QUERY EXPANSION
    # =========================================================================

    async def retrieve_with_expansion(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        expansion_rounds: int = 2,
        limit: int = 20
    ) -> RetrievalResult:
        """Retrieve with iterative query expansion.

        Uses top results to expand the query and re-retrieve.

        Args:
            query_text: Query text
            query_embedding: Query embedding
            session_key: Session identifier
            expansion_rounds: Number of expansion rounds
            limit: Maximum final results

        Returns:
            RetrievalResult
        """
        # Initial retrieval
        result = await self.retrieve(
            query_text, query_embedding, session_key, limit=limit * 2
        )

        if expansion_rounds <= 0 or len(result.nodes) < 3:
            result.nodes = result.nodes[:limit]
            return result

        # Iterative expansion
        all_candidates: dict[str, tuple[NeuralNode, float]] = {
            n.node_id: (n, s) for n, s in result.nodes
        }

        for round_num in range(expansion_rounds):
            # Get top nodes for expansion
            top_nodes = [n for n, s in sorted(
                all_candidates.values(),
                key=lambda x: x[1],
                reverse=True
            )[:5]]

            # Expand query using top nodes' embeddings
            expansion_embeddings = [n.embedding for n in top_nodes if n.embedding]
            if not expansion_embeddings:
                break

            # Compute expanded query (weighted average)
            expanded_embedding = list(query_embedding)
            for i in range(len(expanded_embedding)):
                exp_sum = sum(e[i] for e in expansion_embeddings)
                expanded_embedding[i] = (
                    0.7 * expanded_embedding[i] +
                    0.3 * exp_sum / len(expansion_embeddings)
                )

            # Re-retrieve with expanded query
            new_result = await self.retrieve(
                query_text,
                expanded_embedding,
                session_key,
                limit=limit
            )

            # Merge results
            for node, score in new_result.nodes:
                if node.node_id in all_candidates:
                    _, old_score = all_candidates[node.node_id]
                    all_candidates[node.node_id] = (node, max(old_score, score * 0.9))
                else:
                    all_candidates[node.node_id] = (node, score * 0.9)

        # Final ranking
        final_nodes = sorted(
            all_candidates.values(),
            key=lambda x: x[1],
            reverse=True
        )[:limit]

        return RetrievalResult(
            query_text=query_text,
            nodes=final_nodes,
            stages_executed=result.stages_executed + [f"expansion_x{expansion_rounds}"],
            total_candidates_seen=len(all_candidates),
            co_activations_recorded=result.co_activations_recorded,
            total_time_ms=result.total_time_ms,
        )
