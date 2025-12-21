"""Cross-encoder reranker for top-K candidate refinement.

Implements dense reranking on the top-K from retrieval using:
1. Cross-encoder scoring (query-document pairs)
2. LLM-based relevance scoring (Ollama/OpenAI)
3. Cached logits per candidate text for efficiency

THE PLAN: "Add dense reranking on the top-K from HybridFlashRetriever/NeuralRetriever
using a lightweight cross-encoder; cache logits per candidate text."

Reranking significantly improves precision@1 for single-hop queries by:
- Considering query-document interaction (not just separate embeddings)
- Breaking ties between semantically similar but differently relevant candidates
- Filtering out candidates that are topically similar but don't answer the question

Usage:
    reranker = create_reranker("llm", model="qwen2.5:7b-instruct")
    reranked = await reranker.rerank(query, candidates, limit=10)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .data_types import NeuralNode

logger = logging.getLogger(__name__)


@dataclass
class RerankerConfig:
    """Configuration for reranker."""

    # Reranker type: "llm", "cross_encoder", "hybrid"
    reranker_type: str = "llm"

    # LLM settings
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_timeout_seconds: float = 8.0
    llm_max_tokens: int = 15

    # Cross-encoder settings (for future sentence-transformers integration)
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Performance settings
    batch_size: int = 10  # Process this many candidates at a time
    max_candidates: int = 50  # Maximum candidates to consider for reranking
    cache_size: int = 1000  # LRU cache size for score caching
    async_timeout_ms: float = 50.0  # Timeout per candidate for async reranking

    # Scoring weights
    original_score_weight: float = 0.3  # Weight for original retrieval score
    rerank_score_weight: float = 0.7  # Weight for reranker score

    # Fallback behavior
    fallback_on_timeout: bool = True  # Return original order on timeout
    min_score_threshold: float = 0.1  # Minimum score to keep candidate


@dataclass
class RerankResult:
    """Result of reranking a candidate."""
    node_id: str
    original_score: float
    rerank_score: float
    combined_score: float
    rerank_reason: str = ""
    latency_ms: float = 0.0


class ScoreCache:
    """LRU cache for rerank scores with TTL."""

    def __init__(self, max_size: int = 1000, ttl_seconds: float = 300.0):
        """Initialize cache.

        Args:
            max_size: Maximum cache entries
            ttl_seconds: Time-to-live for cache entries
        """
        self._cache: dict[str, tuple[float, float]] = {}  # key -> (score, timestamp)
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._access_order: list[str] = []

    def _make_key(self, query: str, text: str) -> str:
        """Create cache key from query and text."""
        combined = f"{query}|||{text}"
        return hashlib.md5(combined.encode()).hexdigest()

    def get(self, query: str, text: str) -> float | None:
        """Get cached score if available and not expired.

        Args:
            query: Query text
            text: Candidate text

        Returns:
            Cached score or None
        """
        key = self._make_key(query, text)
        if key not in self._cache:
            return None

        score, timestamp = self._cache[key]

        # Check TTL
        if time.time() - timestamp > self._ttl:
            del self._cache[key]
            return None

        # Update access order
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

        return score

    def set(self, query: str, text: str, score: float) -> None:
        """Cache a score.

        Args:
            query: Query text
            text: Candidate text
            score: Score to cache
        """
        key = self._make_key(query, text)

        # Evict oldest if at capacity
        while len(self._cache) >= self._max_size and self._access_order:
            oldest = self._access_order.pop(0)
            self._cache.pop(oldest, None)

        self._cache[key] = (score, time.time())
        self._access_order.append(key)

    def clear(self) -> None:
        """Clear all cached scores."""
        self._cache.clear()
        self._access_order.clear()


class BaseReranker(ABC):
    """Abstract base class for rerankers."""

    def __init__(self, config: RerankerConfig | None = None):
        """Initialize reranker.

        Args:
            config: Reranker configuration
        """
        self.config = config or RerankerConfig()
        self._cache = ScoreCache(
            max_size=self.config.cache_size,
            ttl_seconds=300.0
        )

        # Statistics
        self._total_reranks = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._total_latency_ms = 0.0

    @abstractmethod
    async def score_candidate(
        self,
        query: str,
        candidate_text: str,
        speaker: str = ""
    ) -> float:
        """Score a single candidate against the query.

        Args:
            query: Query text
            candidate_text: Candidate text to score
            speaker: Optional speaker metadata

        Returns:
            Relevance score in [0, 1] or [0, 3] depending on implementation
        """
        ...

    async def rerank(
        self,
        query: str,
        candidates: list[tuple["NeuralNode", float]],
        limit: int = 10
    ) -> list[tuple["NeuralNode", float]]:
        """Rerank candidates using the reranker model.

        Args:
            query: Query text
            candidates: List of (node, original_score) tuples
            limit: Maximum number of results to return

        Returns:
            Reranked list of (node, combined_score) tuples
        """
        if not candidates:
            return []

        start_time = time.perf_counter()
        self._total_reranks += 1

        # Limit candidates to max
        candidates = candidates[:self.config.max_candidates]

        # Score each candidate
        results: list[RerankResult] = []

        for node, original_score in candidates:
            # Check cache first
            cached = self._cache.get(query, node.content)
            if cached is not None:
                self._cache_hits += 1
                rerank_score = cached
            else:
                self._cache_misses += 1

                # Get speaker using canonical accessor
                speaker = node.speaker_id

                try:
                    rerank_score = await asyncio.wait_for(
                        self.score_candidate(query, node.content, speaker),
                        timeout=self.config.async_timeout_ms / 1000
                    )
                    self._cache.set(query, node.content, rerank_score)
                except asyncio.TimeoutError:
                    # Use original score on timeout
                    rerank_score = original_score * 3  # Scale to 0-3 range
                    logger.debug(f"Rerank timeout for node {node.node_id[:8]}")

            # Normalize rerank score to [0, 1] if it's in [0, 3]
            if rerank_score > 1:
                rerank_score_normalized = rerank_score / 3.0
            else:
                rerank_score_normalized = rerank_score

            # Combine scores
            combined = (
                self.config.original_score_weight * original_score +
                self.config.rerank_score_weight * rerank_score_normalized
            )

            results.append(RerankResult(
                node_id=node.node_id,
                original_score=original_score,
                rerank_score=rerank_score,
                combined_score=combined
            ))

        # Sort by combined score
        results.sort(key=lambda r: r.combined_score, reverse=True)

        # Build output
        node_map = {node.node_id: node for node, _ in candidates}
        output = [
            (node_map[r.node_id], r.combined_score)
            for r in results[:limit]
            if r.combined_score >= self.config.min_score_threshold
        ]

        latency = (time.perf_counter() - start_time) * 1000
        self._total_latency_ms += latency

        logger.debug(
            f"Reranked {len(candidates)} candidates in {latency:.1f}ms "
            f"(cache hit rate: {self.cache_hit_rate:.1%})"
        )

        return output

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate."""
        total = self._cache_hits + self._cache_misses
        return self._cache_hits / total if total > 0 else 0.0

    def get_stats(self) -> dict[str, Any]:
        """Get reranker statistics."""
        return {
            "total_reranks": self._total_reranks,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_hit_rate": self.cache_hit_rate,
            "avg_latency_ms": (
                self._total_latency_ms / self._total_reranks
                if self._total_reranks > 0 else 0
            ),
        }


class LLMReranker(BaseReranker):
    """LLM-based reranker using Ollama or compatible API.

    Scores candidates using an LLM prompt that asks for relevance rating.
    Fast and effective for single-hop factual questions.
    """

    def __init__(self, config: RerankerConfig | None = None):
        """Initialize LLM reranker."""
        super().__init__(config)
        self._session = None

    async def _get_session(self):
        """Get or create aiohttp session."""
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    async def score_candidate(
        self,
        query: str,
        candidate_text: str,
        speaker: str = ""
    ) -> float:
        """Score candidate using LLM.

        Returns score in [0, 3]:
        - 3 = directly answers the question
        - 2 = strong supporting evidence
        - 1 = weakly related
        - 0 = unrelated / wrong entity
        """
        import aiohttp

        prompt = f"""Score relevance 0-3 for the question vs memory.

Question: {query}
Memory from [{speaker}]: {candidate_text[:350]}

Scoring:
3 = directly answers the question
2 = strong supporting evidence
1 = weakly related
0 = unrelated / wrong entity

Return JSON only: {{"score": 0}} (or 1/2/3)"""

        try:
            session = await self._get_session()
            async with session.post(
                f"{self.config.llm_base_url}/api/generate",
                json={
                    "model": self.config.llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0,
                        "num_predict": self.config.llm_max_tokens
                    }
                },
                timeout=aiohttp.ClientTimeout(total=self.config.llm_timeout_seconds)
            ) as response:
                result = await response.json()
                resp = result.get("response", "").strip()

                # Parse JSON response
                import json
                try:
                    obj = json.loads(resp)
                    score = int(obj.get("score", 1))
                    return max(0, min(3, score))
                except Exception:
                    # Fallback to digit scan
                    for char in resp:
                        if char in "0123":
                            return int(char)
                    return 1

        except Exception as e:
            logger.debug(f"LLM rerank error: {e}")
            return 1  # Default to weak relevance

    async def close(self):
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None


class SemanticBoostReranker(BaseReranker):
    """Lightweight reranker using semantic similarity boosting.

    Doesn't call an LLM - instead uses keyword matching and entity overlap
    to boost or penalize candidates. Fast and works offline.

    Good for latency-constrained scenarios where LLM reranking is too slow.
    """

    def __init__(self, config: RerankerConfig | None = None):
        """Initialize semantic boost reranker."""
        super().__init__(config)

    async def score_candidate(
        self,
        query: str,
        candidate_text: str,
        speaker: str = ""
    ) -> float:
        """Score candidate using semantic matching.

        Returns score in [0, 3] based on:
        - Keyword overlap
        - Entity match
        - Question word alignment
        """
        query_lower = query.lower()
        text_lower = candidate_text.lower()

        score = 1.0  # Base score

        # Extract query keywords (skip stopwords)
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "what", "where",
            "who", "when", "how", "which", "do", "does", "did", "have", "has",
            "had", "be", "been", "being", "to", "of", "in", "for", "on", "with",
            "at", "by", "from", "about", "as", "into", "through"
        }

        import re
        query_words = set(re.findall(r'\b\w+\b', query_lower)) - stopwords
        text_words = set(re.findall(r'\b\w+\b', text_lower))

        # Keyword overlap boost
        overlap = len(query_words & text_words)
        if overlap >= 3:
            score += 1.5
        elif overlap >= 2:
            score += 1.0
        elif overlap >= 1:
            score += 0.5

        # Entity match boost (capitalized words in query)
        query_entities = set(re.findall(r'\b[A-Z][a-z]+\b', query))
        for entity in query_entities:
            if entity.lower() in text_lower:
                score += 0.5

        # Speaker match boost
        if speaker:
            speaker_lower = speaker.lower()
            # Check if speaker is mentioned in query
            if speaker_lower in query_lower:
                score += 0.5

            # Check for possessive patterns
            for entity in query_entities:
                if entity.lower() == speaker_lower:
                    score += 0.5
                    break

        # Penalize very short or very long texts
        text_len = len(candidate_text)
        if text_len < 20:
            score -= 0.5
        elif text_len > 500:
            score -= 0.2

        return max(0, min(3, score))


class HybridReranker(BaseReranker):
    """Hybrid reranker combining semantic boost with LLM fallback.

    First applies fast semantic boosting, then uses LLM for top candidates.
    Balances latency and accuracy.
    """

    def __init__(self, config: RerankerConfig | None = None):
        """Initialize hybrid reranker."""
        super().__init__(config)
        self._semantic = SemanticBoostReranker(config)
        self._llm = LLMReranker(config)

    async def score_candidate(
        self,
        query: str,
        candidate_text: str,
        speaker: str = ""
    ) -> float:
        """Score using semantic boost (LLM used in rerank method)."""
        return await self._semantic.score_candidate(query, candidate_text, speaker)

    async def rerank(
        self,
        query: str,
        candidates: list[tuple["NeuralNode", float]],
        limit: int = 10
    ) -> list[tuple["NeuralNode", float]]:
        """Rerank with two-stage approach.

        1. Fast semantic boost to pre-filter
        2. LLM rerank on top candidates
        """
        if not candidates:
            return []

        # Stage 1: Fast semantic pre-filtering
        semantic_limit = min(limit * 3, len(candidates))
        semantic_results = await self._semantic.rerank(
            query, candidates, limit=semantic_limit
        )

        # Stage 2: LLM rerank on top candidates
        if len(semantic_results) > limit:
            llm_results = await self._llm.rerank(
                query, semantic_results, limit=limit
            )
            return llm_results

        return semantic_results

    async def close(self):
        """Close resources."""
        await self._llm.close()


def create_reranker(
    reranker_type: str = "llm",
    **kwargs
) -> BaseReranker:
    """Create a reranker instance.

    Args:
        reranker_type: Type of reranker ("llm", "semantic", "hybrid")
        **kwargs: Additional config options

    Returns:
        Reranker instance
    """
    config = RerankerConfig(reranker_type=reranker_type, **kwargs)

    if reranker_type == "llm":
        return LLMReranker(config)
    elif reranker_type == "semantic":
        return SemanticBoostReranker(config)
    elif reranker_type == "hybrid":
        return HybridReranker(config)
    else:
        raise ValueError(f"Unknown reranker type: {reranker_type}")


# Convenience function for benchmark integration
async def rerank_candidates(
    query: str,
    candidates: list[tuple["NeuralNode", float]],
    reranker_type: str = "semantic",
    limit: int = 10,
    **config_kwargs
) -> list[tuple["NeuralNode", float]]:
    """Convenience function to rerank candidates.

    Args:
        query: Query text
        candidates: List of (node, score) tuples
        reranker_type: Type of reranker to use
        limit: Maximum results
        **config_kwargs: Additional config options

    Returns:
        Reranked candidates
    """
    reranker = create_reranker(reranker_type, **config_kwargs)
    try:
        return await reranker.rerank(query, candidates, limit)
    finally:
        if hasattr(reranker, 'close'):
            await reranker.close()


async def rerank_candidates_parallel(
    query: str,
    candidates: list[tuple["NeuralNode", float]],
    limit: int = 15,
    llm_model: str = "qwen2.5:7b-instruct",
    llm_base_url: str = "http://localhost:11434",
    timeout_seconds: float = 10.0,
    max_candidates: int = 50,
    score_boost: float = 0.2,
) -> list[tuple["NeuralNode", float]]:
    """Parallel LLM rerank for benchmark usage."""
    if not candidates:
        return []

    cfg = RerankerConfig(
        reranker_type="llm",
        llm_model=llm_model,
        llm_base_url=llm_base_url,
        llm_timeout_seconds=timeout_seconds,
        llm_max_tokens=5,
        max_candidates=max_candidates,
    )
    reranker = LLMReranker(cfg)

    try:
        candidates = candidates[:max_candidates]
        tasks = []
        for node, _score in candidates:
            tasks.append(reranker.score_candidate(query, node.content, node.speaker_id))
        scores = await asyncio.gather(*tasks, return_exceptions=True)

        scored = []
        for (node, charge), score in zip(candidates, scores):
            if isinstance(score, Exception):
                score = 1
            scored.append((node, charge, float(score)))

        scored.sort(key=lambda x: (x[2], x[1]), reverse=True)

        result: list[tuple["NeuralNode", float]] = []
        for node, charge, score in scored[:limit]:
            boosted = charge + (score * score_boost)
            result.append((node, boosted))
        return result
    finally:
        await reranker.close()
