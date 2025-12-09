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

    # Hop decay
    hop_decay_factor: float = 0.8

    # Reranking
    rerank_by_heat: bool = True
    heat_weight: float = 0.2
    importance_weight: float = 0.15

    # NEW: Wave amplitude integration
    # Wave amplitudes stored on nodes can boost retrieval based on query type
    use_wave_amplitudes: bool = True
    wave_amplitude_weight: float = 0.1  # Small boost for wave alignment

    # CRITICAL: Hybrid retrieval (embedding + keyword)
    # Pure embedding search fails for factual QA - need keyword matching
    use_keyword_boost: bool = True
    keyword_boost_weight: float = 0.5  # Strong boost for keyword matches

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
                score_threshold=0.3,
                use_vector_search=True,
            ),
            RetrievalStageConfig(
                name="entity_expansion",
                max_candidates=100,
                edge_types=[EdgeType.ENTITY, EdgeType.SEMANTIC],
                layer_filter=[NodeLayer.MESSAGE, NodeLayer.EPISODE],
                max_hops=1,
                score_threshold=0.25,
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

        # Track candidates across stages
        candidates: dict[str, tuple[NeuralNode, float]] = {}
        stages_executed: list[str] = []
        total_candidates_seen = 0

        # Execute stages sequentially
        for stage in self._config.stages:
            stage_start = time.perf_counter()

            if stage.use_vector_search:
                # Stage 1: Fast hybrid recall (vector + keyword)
                stage_results = await self._fast_recall(
                    query_embedding, session_key, stage, query_keywords
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

            # Filter by minimum threshold
            if final_score >= self._config.min_score_threshold:
                reranked.append((node, final_score))

        # Sort by final score
        reranked.sort(key=lambda x: x[1], reverse=True)

        return reranked

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
