"""
Graph-Based Memory Retriever.

Implements intelligent retrieval that combines:
1. Hash-based shard lookup for speed
2. Graph traversal for accuracy
3. Embedding similarity for relevance
4. Neural graph multi-stage retrieval (Phase 7)

RETRIEVAL ALGORITHM:
====================

Step 1: QUERY ANALYSIS
    - Extract domain/subdomain hints from query text
    - Generate query embedding (if available)
    - Determine primary search domains

Step 2: SHARD LOOKUP (Fast path)
    - Use shard router to identify target shards
    - Only query relevant shards (not all N shards)
    - Time: O(1) for shard identification

Step 3: DIRECT RETRIEVAL
    - Query primary domains/subdomains in target shards
    - Apply filters (time, type, etc.)
    - Collect candidate memories

Step 4: GRAPH EXPANSION (Smart path)
    - Follow hash pointers from primary domains
    - Traverse cross-domain edges (max_hops)
    - Collect related memories from connected domains
    - Examples:
        - Query "current goals" → follow pointer to "long_term_goals"
        - Query "coding style" → follow pointer to "personality_traits"

Step 5: RANKING
    - Score all candidates:
        - Embedding similarity (if available)
        - Importance score
        - Recency
        - Access frequency
        - Graph path weight (decay with hops)
    - Return top-K results

PERFORMANCE CHARACTERISTICS:
============================

| Operation            | Time Complexity | Description                    |
|---------------------|-----------------|--------------------------------|
| Shard lookup        | O(log V)        | Binary search on hash ring     |
| Direct retrieval    | O(M/S)          | M memories, S shards           |
| Graph traversal     | O(E * H)        | E edges, H max hops            |
| Ranking             | O(N log K)      | N candidates, K results        |

Total: O(log V) + O(M/S) + O(E * H) + O(N log K)

For typical usage:
- 16 shards, 1M memories → ~60K memories per shard
- 2 hops, ~5 edges per domain → 10 edges traversed
- 100 candidates, top 20 → minimal ranking overhead

vs Flat Store:
- No sharding: O(M) scan of all memories
- No graph: Miss related memories in other domains
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TYPE_CHECKING

from .data_types import (
    Domain,
    DomainType,
    HashPointer,
    Memory,
    MemoryType,
    RetrievalQuery,
    RetrievalResult,
    Subdomain,
    SubdomainType,
    TraversalPath,
)
from .domain_hierarchy import DOMAIN_HIERARCHY, SUBDOMAIN_TO_DOMAIN
from .memory_store import ShardedMemoryStore

if TYPE_CHECKING:
    from memmachine.neural_graph import NeuralGraphService

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class RetrievalConfig:
    """Configuration for the graph retriever."""

    # Graph traversal
    max_hops: int = 2                      # Maximum edge traversal depth
    follow_bidirectional: bool = True      # Follow edges in both directions
    max_edges_per_hop: int = 5             # Limit edges explored per hop

    # Retrieval limits
    max_candidates: int = 200              # Max candidates to collect
    max_results: int = 20                  # Final results to return

    # Scoring weights
    embedding_weight: float = 0.4          # Weight for embedding similarity
    importance_weight: float = 0.25        # Weight for importance score
    recency_weight: float = 0.2            # Weight for temporal decay
    access_weight: float = 0.1             # Weight for access frequency
    path_weight: float = 0.05              # Weight for graph path score

    # Time decay
    recency_half_life_days: float = 7.0    # Half-life for recency scoring

    # Domain hints
    enable_domain_hints: bool = True       # Extract domains from query
    fallback_to_current_data: bool = True  # Default to CURRENT_DATA if no hints

    # Neural graph integration (Phase 7)
    neural_graph_enabled: bool = False     # Enable neural graph retrieval
    neural_graph_weight: float = 0.3       # Weight for neural graph scores in final ranking
    neural_graph_min_score: float = 0.1    # Minimum neural score to include


# =============================================================================
# DOMAIN HINT EXTRACTION
# =============================================================================

# Keywords that hint at specific domains/subdomains
DOMAIN_KEYWORDS: dict[str, list[DomainType]] = {
    # Personality keywords
    "personality": [DomainType.PERSONALITY],
    "traits": [DomainType.PERSONALITY],
    "mood": [DomainType.PERSONALITY, DomainType.CURRENT_DATA],
    "emotion": [DomainType.PERSONALITY],
    "feel": [DomainType.PERSONALITY, DomainType.CURRENT_DATA],
    "style": [DomainType.PERSONALITY, DomainType.PREFERENCES],

    # Preference keywords
    "prefer": [DomainType.PREFERENCES],
    "like": [DomainType.PREFERENCES],
    "favorite": [DomainType.PREFERENCES],
    "code": [DomainType.PREFERENCES, DomainType.KNOWLEDGE],
    "music": [DomainType.PREFERENCES],
    "food": [DomainType.PREFERENCES],

    # Goal keywords
    "goal": [DomainType.GOALS],
    "objective": [DomainType.GOALS],
    "plan": [DomainType.GOALS],
    "deadline": [DomainType.GOALS],
    "milestone": [DomainType.GOALS],
    "aspiration": [DomainType.GOALS],
    "want": [DomainType.GOALS],

    # Current data keywords
    "current": [DomainType.CURRENT_DATA],
    "recent": [DomainType.CURRENT_DATA],
    "today": [DomainType.CURRENT_DATA],
    "now": [DomainType.CURRENT_DATA],
    "project": [DomainType.CURRENT_DATA],
    "working": [DomainType.CURRENT_DATA],
    "task": [DomainType.CURRENT_DATA, DomainType.GOALS],

    # Relationship keywords
    "friend": [DomainType.RELATIONSHIPS],
    "family": [DomainType.RELATIONSHIPS],
    "colleague": [DomainType.RELATIONSHIPS],
    "pet": [DomainType.RELATIONSHIPS],
    "relationship": [DomainType.RELATIONSHIPS],

    # Life advice keywords
    "advice": [DomainType.LIFE_ADVICE],
    "strength": [DomainType.LIFE_ADVICE],
    "weakness": [DomainType.LIFE_ADVICE],
    "growth": [DomainType.LIFE_ADVICE],
    "cope": [DomainType.LIFE_ADVICE],
    "resilience": [DomainType.LIFE_ADVICE],

    # Knowledge keywords
    "know": [DomainType.KNOWLEDGE],
    "learn": [DomainType.KNOWLEDGE],
    "skill": [DomainType.KNOWLEDGE],
    "expertise": [DomainType.KNOWLEDGE],
    "education": [DomainType.KNOWLEDGE],

    # Historical keywords
    "history": [DomainType.HISTORICAL],
    "past": [DomainType.HISTORICAL],
    "remember": [DomainType.HISTORICAL],
    "memory": [DomainType.HISTORICAL],
    "event": [DomainType.HISTORICAL],
}


def extract_domain_hints(query: str) -> list[DomainType]:
    """
    Extract domain hints from a query string.

    Uses keyword matching to identify likely relevant domains.

    Args:
        query: The search query.

    Returns:
        List of likely relevant domain types.
    """
    query_lower = query.lower()
    domain_scores: dict[DomainType, int] = {}

    for keyword, domains in DOMAIN_KEYWORDS.items():
        if keyword in query_lower:
            for domain in domains:
                domain_scores[domain] = domain_scores.get(domain, 0) + 1

    # Sort by score and return
    sorted_domains = sorted(
        domain_scores.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    return [d for d, _ in sorted_domains]


# =============================================================================
# GRAPH RETRIEVER
# =============================================================================

class GraphRetriever:
    """
    Intelligent memory retrieval using shard routing and graph traversal.

    This is the main retrieval interface that combines:
    - Fast hash-based shard lookup
    - Knowledge graph traversal for related memories
    - Ranking with multiple signals
    - Neural graph multi-stage retrieval (Phase 7)
    """

    def __init__(
        self,
        memory_store: ShardedMemoryStore,
        config: RetrievalConfig | None = None,
        embedding_fn: Callable[[str], list[float]] | None = None,
        neural_graph_service: "NeuralGraphService | None" = None,
    ):
        """
        Initialize the graph retriever.

        Args:
            memory_store: The sharded memory store.
            config: Retrieval configuration.
            embedding_fn: Optional function to compute query embeddings.
            neural_graph_service: Optional neural graph service for multi-stage retrieval.
        """
        self._store = memory_store
        self._config = config or RetrievalConfig()
        self._embedding_fn = embedding_fn
        self._neural_graph_service = neural_graph_service

    @property
    def neural_graph_service(self) -> "NeuralGraphService | None":
        """Get the neural graph service."""
        return self._neural_graph_service

    @neural_graph_service.setter
    def neural_graph_service(self, value: "NeuralGraphService | None") -> None:
        """Set the neural graph service."""
        self._neural_graph_service = value

    async def retrieve(
        self,
        query: RetrievalQuery,
    ) -> RetrievalResult:
        """
        Retrieve memories matching the query.

        This is the main entry point for retrieval. It implements the
        full algorithm:
        1. Query analysis
        2. Shard lookup
        3. Direct retrieval
        4. Graph expansion
        5. Ranking

        Args:
            query: The retrieval query.

        Returns:
            RetrievalResult with ranked memories and metadata.
        """
        start_time = time.perf_counter()

        # Initialize result
        result = RetrievalResult(
            query=query,
            memories=[],
        )

        # Step 1: Query Analysis
        domain_hints = self._analyze_query(query)
        result.accessed_domains = [d.value for d in domain_hints]

        # Step 2-3: Shard Lookup + Direct Retrieval
        shard_start = time.perf_counter()
        direct_memories = await self._direct_retrieval(
            query, domain_hints
        )
        result.shard_lookup_time_ms = (time.perf_counter() - shard_start) * 1000
        result.memories_from_direct = len(direct_memories)

        # Step 4: Graph Expansion
        graph_start = time.perf_counter()
        expanded_memories, paths = await self._graph_expansion(
            query, domain_hints, direct_memories
        )
        result.graph_traversal_time_ms = (time.perf_counter() - graph_start) * 1000
        result.memories_from_graph = len(expanded_memories) - len(direct_memories)
        result.traversal_paths = paths

        # Step 4b: Neural Graph Retrieval (Phase 7)
        neural_scores: dict[str, float] = {}
        if self._config.neural_graph_enabled and self._neural_graph_service:
            neural_start = time.perf_counter()
            neural_scores = await self._neural_graph_retrieval(query)
            result.neural_graph_time_ms = (time.perf_counter() - neural_start) * 1000
            result.neural_nodes_retrieved = len(neural_scores)

        # Step 5: Ranking
        ranking_start = time.perf_counter()
        ranked_memories = self._rank_memories(
            query, expanded_memories, neural_scores
        )
        result.ranking_time_ms = (time.perf_counter() - ranking_start) * 1000

        # Apply limit
        result.memories = ranked_memories[:query.limit or self._config.max_results]
        result.total_candidates = len(expanded_memories)
        result.total_time_ms = (time.perf_counter() - start_time) * 1000

        logger.debug(
            f"Retrieved {len(result.memories)} memories in {result.total_time_ms:.1f}ms "
            f"(direct={result.memories_from_direct}, graph={result.memories_from_graph})"
        )

        return result

    def _analyze_query(self, query: RetrievalQuery) -> list[DomainType]:
        """
        Analyze query to determine target domains.

        Args:
            query: The retrieval query.

        Returns:
            List of domain types to search.
        """
        # Use explicit domain filters if provided
        if query.domain_types:
            return query.domain_types

        # Extract hints from query text
        if self._config.enable_domain_hints:
            hints = extract_domain_hints(query.query_text)
            if hints:
                return hints

        # Fallback to CURRENT_DATA
        if self._config.fallback_to_current_data:
            return [DomainType.CURRENT_DATA]

        # Search all domains
        return list(DomainType)

    async def _direct_retrieval(
        self,
        query: RetrievalQuery,
        domain_types: list[DomainType],
    ) -> list[Memory]:
        """
        Perform direct retrieval from shards.

        This is the fast path that queries specific domains/subdomains
        in their respective shards.

        Args:
            query: The retrieval query.
            domain_types: Domain types to search.

        Returns:
            List of directly retrieved memories.
        """
        memories = []

        # Query each domain
        for domain_type in domain_types[:5]:  # Limit domains to avoid explosion
            domain_memories = await self._store.get_memories_by_domain(
                query.user_id,
                domain_type,
                limit=self._config.max_candidates // len(domain_types),
            )
            memories.extend(domain_memories)

        # If subdomain filters specified, also query those
        if query.subdomain_types:
            for subdomain_type in query.subdomain_types:
                subdomain_memories = await self._store.get_memories_by_subdomain(
                    query.user_id,
                    subdomain_type,
                    limit=self._config.max_candidates,
                )
                memories.extend(subdomain_memories)

        # Deduplicate by memory_id
        seen = set()
        unique_memories = []
        for memory in memories:
            if memory.memory_id not in seen:
                seen.add(memory.memory_id)
                unique_memories.append(memory)

        return unique_memories[:self._config.max_candidates]

    async def _graph_expansion(
        self,
        query: RetrievalQuery,
        domain_types: list[DomainType],
        direct_memories: list[Memory],
    ) -> tuple[list[Memory], list[TraversalPath]]:
        """
        Expand retrieval by following graph edges.

        This traverses:
        1. Cross-domain edges to find related domains
        2. Hash pointers from memories to other domains

        Args:
            query: The retrieval query.
            domain_types: Initial domain types.
            direct_memories: Memories from direct retrieval.

        Returns:
            Tuple of (all memories, traversal paths).
        """
        if not query.follow_domain_pointers:
            return direct_memories, []

        all_memories = list(direct_memories)
        seen_memory_ids = {m.memory_id for m in direct_memories}
        traversal_paths = []

        # Get related domains via domain edges
        related_domain_types = set()
        for domain_type in domain_types:
            edges = await self._store.get_domain_edges(query.user_id, domain_type)
            for edge in edges[:self._config.max_edges_per_hop]:
                # Extract domain type from edge target
                target_parts = edge.target_domain_id.split(":")
                if len(target_parts) >= 3:
                    try:
                        target_domain_type = DomainType(target_parts[2])
                        if target_domain_type not in domain_types:
                            related_domain_types.add(target_domain_type)

                            # Create traversal path
                            path = TraversalPath(
                                path_id=str(uuid.uuid4()),
                                start_node_type="domain",
                                start_node_id=f"{query.user_id}:domain:{domain_type.value}",
                            )
                            # Create a pointer for the edge
                            pointer = HashPointer(
                                pointer_id=edge.edge_id,
                                source_type="domain",
                                source_id=edge.source_domain_id,
                                target_type="domain",
                                target_id=edge.target_domain_id,
                                target_hash="",
                                relation_type=edge.relation_type,
                                weight=edge.weight,
                            )
                            path.add_hop(pointer)
                            path.path_score = edge.weight
                            traversal_paths.append(path)
                    except ValueError:
                        pass

        # Query related domains (hop 1)
        for hop in range(min(query.max_hops, self._config.max_hops)):
            if not related_domain_types:
                break

            new_related = set()
            for related_type in list(related_domain_types):
                related_memories = await self._store.get_memories_by_domain(
                    query.user_id,
                    related_type,
                    limit=self._config.max_candidates // (hop + 2),
                )

                for memory in related_memories:
                    if memory.memory_id not in seen_memory_ids:
                        seen_memory_ids.add(memory.memory_id)
                        # Apply hop decay
                        memory.decay_factor = 0.8 ** (hop + 1)
                        all_memories.append(memory)

                # Get next hop domains
                if hop < query.max_hops - 1:
                    next_edges = await self._store.get_domain_edges(
                        query.user_id, related_type
                    )
                    for edge in next_edges[:3]:  # Limit expansion
                        target_parts = edge.target_domain_id.split(":")
                        if len(target_parts) >= 3:
                            try:
                                target_type = DomainType(target_parts[2])
                                if (
                                    target_type not in domain_types and
                                    target_type not in related_domain_types
                                ):
                                    new_related.add(target_type)
                            except ValueError:
                                pass

            related_domain_types = new_related

        return all_memories, traversal_paths

    async def _neural_graph_retrieval(
        self,
        query: RetrievalQuery,
    ) -> dict[str, float]:
        """
        Perform neural graph multi-stage retrieval.

        Uses the NeuralRetriever for multi-stage retrieval with:
        - Fast recall (vector search)
        - Entity expansion
        - Temporal chain traversal
        - Hierarchy traversal
        - Co-activation boost

        Args:
            query: The retrieval query.

        Returns:
            Dictionary mapping memory_id to neural score.
        """
        if not self._neural_graph_service:
            return {}

        neural_scores: dict[str, float] = {}

        try:
            # Get query embedding
            query_embedding = query.query_embedding
            if not query_embedding and self._embedding_fn:
                query_embedding = self._embedding_fn(query.query_text)

            if not query_embedding:
                logger.debug("Neural graph retrieval skipped: no query embedding")
                return {}

            # Perform neural retrieval
            neural_result = await self._neural_graph_service.retrieve(
                query_text=query.query_text,
                query_embedding=query_embedding,
                session_key=query.user_id,
                limit=self._config.max_candidates,
            )

            # Convert neural nodes to scores, keyed by episode_uid if available
            for node, score in neural_result.nodes:
                # Try to get associated episode/memory UID
                memory_id = node.metadata.get('episode_uid') or node.metadata.get('memory_id')
                if memory_id and score >= self._config.neural_graph_min_score:
                    neural_scores[memory_id] = score

            logger.debug(
                f"Neural graph retrieval: {len(neural_result.nodes)} nodes, "
                f"{len(neural_scores)} matched memories, "
                f"stages={neural_result.stages_executed}"
            )

        except Exception as e:
            logger.warning(f"Neural graph retrieval failed: {e}")

        return neural_scores

    def _rank_memories(
        self,
        query: RetrievalQuery,
        memories: list[Memory],
        neural_scores: dict[str, float] | None = None,
    ) -> list[Memory]:
        """
        Rank memories by relevance.

        Combines multiple signals:
        - Embedding similarity
        - Importance score
        - Recency
        - Access frequency
        - Graph path decay
        - Neural graph score (Phase 7)

        Args:
            query: The retrieval query.
            memories: Candidate memories.
            neural_scores: Optional neural graph scores keyed by memory_id.

        Returns:
            Ranked list of memories.
        """
        import math

        now = datetime.now(timezone.utc)
        half_life_seconds = self._config.recency_half_life_days * 24 * 3600
        neural_scores = neural_scores or {}

        scored_memories = []

        for memory in memories:
            score = 0.0

            # Importance score
            score += self._config.importance_weight * memory.importance_score

            # Recency (exponential decay)
            age_seconds = (now - memory.created_at).total_seconds()
            recency = 0.5 ** (age_seconds / half_life_seconds)
            score += self._config.recency_weight * recency

            # Access frequency (logarithmic)
            access_score = math.log(1 + memory.access_count) / 5.0
            access_score = min(1.0, access_score)
            score += self._config.access_weight * access_score

            # Graph path decay
            score += self._config.path_weight * memory.decay_factor

            # Embedding similarity if query embedding available
            if query.query_embedding and memory.embedding:
                # Cosine similarity
                dot = sum(a * b for a, b in zip(query.query_embedding, memory.embedding))
                norm_q = sum(a * a for a in query.query_embedding) ** 0.5
                norm_m = sum(a * a for a in memory.embedding) ** 0.5
                if norm_q > 0 and norm_m > 0:
                    similarity = dot / (norm_q * norm_m)
                    score += self._config.embedding_weight * similarity

            # Neural graph score (Phase 7: multi-stage neural retrieval)
            if neural_scores and memory.memory_id in neural_scores:
                neural_score = neural_scores[memory.memory_id]
                score += self._config.neural_graph_weight * neural_score

            scored_memories.append((score, memory))

        # Sort by score descending
        scored_memories.sort(key=lambda x: x[0], reverse=True)

        return [m for _, m in scored_memories]

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================

    async def query_current_data(
        self,
        user_id: str,
        query_text: str,
        limit: int = 10,
    ) -> RetrievalResult:
        """
        Query current data with automatic graph expansion.

        This is optimized for queries about recent/current information,
        which automatically follows pointers to related domains.

        Args:
            user_id: User identifier.
            query_text: Query text.
            limit: Maximum results.

        Returns:
            RetrievalResult.
        """
        query = RetrievalQuery(
            user_id=user_id,
            query_text=query_text,
            domain_types=[DomainType.CURRENT_DATA],
            follow_domain_pointers=True,
            max_hops=2,
            limit=limit,
            include_related_domains=True,
        )

        return await self.retrieve(query)

    async def query_goals_with_context(
        self,
        user_id: str,
        limit: int = 10,
    ) -> RetrievalResult:
        """
        Query goals with full context from related domains.

        Retrieves goals and follows pointers to:
        - Life advice (informed by)
        - Current data (active goals)
        - Knowledge (learning goals)

        Args:
            user_id: User identifier.
            limit: Maximum results.

        Returns:
            RetrievalResult.
        """
        query = RetrievalQuery(
            user_id=user_id,
            query_text="goals and objectives",
            domain_types=[DomainType.GOALS],
            follow_domain_pointers=True,
            max_hops=2,
            limit=limit,
        )

        return await self.retrieve(query)

    async def query_preferences(
        self,
        user_id: str,
        subdomain: SubdomainType | None = None,
        limit: int = 10,
    ) -> RetrievalResult:
        """
        Query user preferences.

        Args:
            user_id: User identifier.
            subdomain: Optional specific preference subdomain.
            limit: Maximum results.

        Returns:
            RetrievalResult.
        """
        query = RetrievalQuery(
            user_id=user_id,
            query_text="preferences",
            domain_types=[DomainType.PREFERENCES],
            subdomain_types=[subdomain] if subdomain else None,
            follow_domain_pointers=True,
            max_hops=1,
            limit=limit,
        )

        return await self.retrieve(query)
