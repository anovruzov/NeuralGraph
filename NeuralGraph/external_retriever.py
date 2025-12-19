"""External Retriever - Pluggable external knowledge sources for open-domain queries.

OPEN-DOMAIN RECOVERY PLAN IMPLEMENTATION:
The NeuralGraph system excels at session-scoped retrieval but struggles with
open-domain questions that require world knowledge not present in the conversation.

This module provides:
1. ExternalRetriever interface for pluggable knowledge sources
2. WikipediaRetriever for encyclopedic facts
3. CachedExternalRetriever for persistent caching of retrieved facts
4. Retrieval blending logic to merge external results with session memories

ARCHITECTURE:
    Query -> Open-Domain Detection -> External Retriever Fan-out
                                          |
                         +----------------+----------------+
                         |                                 |
                    Wikipedia API               Future: Web Search
                         |                                 |
                         +----------------+----------------+
                                          |
                                    Result Blending
                                          |
                                    Reranked Output

USAGE:
    retriever = WikipediaRetriever()
    results = await retriever.retrieve(
        query="What causes rain?",
        query_embedding=embedding_vector,
        limit=5
    )
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .data_types import NeuralNode

logger = logging.getLogger(__name__)


# =============================================================================
# INSTRUMENTATION & TRACING (PHASE 0)
# =============================================================================

@dataclass
class OpenDomainTrace:
    """Trace record for open-domain query routing decisions.

    PHASE 0: Instrumentation to understand how often open-domain queries
    skip filtering and how many have zero in-session hits.
    """
    query_text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Detection results
    is_open_domain: bool = False
    open_domain_confidence: float = 0.0
    detected_query_type: str = ""

    # Routing decisions
    strategy_used: str = "semantic"
    bypassed_entity_filter: bool = False
    bypassed_temporal_filter: bool = False

    # Session retrieval results
    session_candidates_count: int = 0
    session_hits_at_threshold: int = 0  # Candidates above relevance threshold

    # External retrieval (when implemented)
    external_retriever_called: bool = False
    external_results_count: int = 0
    external_retrieval_latency_ms: float = 0.0

    # Final blended results
    final_results_count: int = 0
    external_in_top_k: int = 0  # How many external results made it to top-K

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "query_text": self.query_text,
            "timestamp": self.timestamp.isoformat(),
            "is_open_domain": self.is_open_domain,
            "open_domain_confidence": self.open_domain_confidence,
            "detected_query_type": self.detected_query_type,
            "strategy_used": self.strategy_used,
            "bypassed_entity_filter": self.bypassed_entity_filter,
            "bypassed_temporal_filter": self.bypassed_temporal_filter,
            "session_candidates_count": self.session_candidates_count,
            "session_hits_at_threshold": self.session_hits_at_threshold,
            "external_retriever_called": self.external_retriever_called,
            "external_results_count": self.external_results_count,
            "external_retrieval_latency_ms": self.external_retrieval_latency_ms,
            "final_results_count": self.final_results_count,
            "external_in_top_k": self.external_in_top_k,
        }


class OpenDomainTracer:
    """Collects and analyzes traces for open-domain query routing.

    PHASE 0: Understanding the problem before fixing it.
    """

    def __init__(self, max_traces: int = 1000):
        self._traces: list[OpenDomainTrace] = []
        self._max_traces = max_traces
        self._stats = {
            "total_queries": 0,
            "open_domain_queries": 0,
            "zero_session_hits": 0,
            "external_called": 0,
            "external_improved_results": 0,
        }

    def record_trace(self, trace: OpenDomainTrace) -> None:
        """Record a new trace."""
        self._traces.append(trace)
        if len(self._traces) > self._max_traces:
            self._traces = self._traces[-self._max_traces:]

        # Update stats
        self._stats["total_queries"] += 1
        if trace.is_open_domain:
            self._stats["open_domain_queries"] += 1
        if trace.session_hits_at_threshold == 0:
            self._stats["zero_session_hits"] += 1
        if trace.external_retriever_called:
            self._stats["external_called"] += 1
            if trace.external_in_top_k > 0:
                self._stats["external_improved_results"] += 1

        # Log for debugging
        if trace.is_open_domain and trace.session_hits_at_threshold == 0:
            logger.warning(
                f"Open-domain query with zero session hits: {trace.query_text[:50]}... "
                f"(confidence={trace.open_domain_confidence:.2f})"
            )

    def get_stats(self) -> dict[str, Any]:
        """Get aggregated statistics."""
        stats = self._stats.copy()
        if stats["total_queries"] > 0:
            stats["open_domain_ratio"] = stats["open_domain_queries"] / stats["total_queries"]
            stats["zero_hit_ratio"] = stats["zero_session_hits"] / stats["total_queries"]
        if stats["open_domain_queries"] > 0:
            stats["external_coverage"] = stats["external_called"] / stats["open_domain_queries"]
        return stats

    def get_recent_traces(self, n: int = 10) -> list[dict[str, Any]]:
        """Get the N most recent traces."""
        return [t.to_dict() for t in self._traces[-n:]]


# Global tracer instance
_tracer = OpenDomainTracer()


def get_open_domain_tracer() -> OpenDomainTracer:
    """Get the global open-domain tracer."""
    return _tracer


# =============================================================================
# EXTERNAL RETRIEVER INTERFACE (PHASE 1a)
# =============================================================================

@dataclass
class ExternalResult:
    """A single result from an external knowledge source.

    Designed to be convertible to NeuralNode for unified processing.
    """
    content: str
    source: str  # e.g., "wikipedia", "web", "knowledge_base"
    source_url: str | None = None
    title: str | None = None

    # Relevance scoring
    relevance_score: float = 0.0
    confidence: float = 1.0

    # Embedding (optional, computed by retriever if needed)
    embedding: list[float] | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Cache key for deduplication
    _cache_key: str | None = field(default=None, repr=False)

    @property
    def cache_key(self) -> str:
        """Generate a cache key for this result."""
        if self._cache_key is None:
            content_hash = hashlib.md5(self.content.encode()).hexdigest()[:16]
            self._cache_key = f"{self.source}:{content_hash}"
        return self._cache_key

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "content": self.content,
            "source": self.source,
            "source_url": self.source_url,
            "title": self.title,
            "relevance_score": self.relevance_score,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "retrieved_at": self.retrieved_at.isoformat(),
            "cache_key": self.cache_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExternalResult":
        """Create from dictionary."""
        retrieved_at = data.get("retrieved_at")
        if isinstance(retrieved_at, str):
            retrieved_at = datetime.fromisoformat(retrieved_at)
        elif retrieved_at is None:
            retrieved_at = datetime.now(timezone.utc)

        return cls(
            content=data["content"],
            source=data["source"],
            source_url=data.get("source_url"),
            title=data.get("title"),
            relevance_score=data.get("relevance_score", 0.0),
            confidence=data.get("confidence", 1.0),
            metadata=data.get("metadata", {}),
            retrieved_at=retrieved_at,
        )


class ExternalRetriever(ABC):
    """Abstract interface for external knowledge retrievers.

    PHASE 1a: Define the pluggable adapter API so deployments can choose providers.

    Implementations should:
    1. Accept a text query and optional embedding
    2. Return a list of ExternalResult objects
    3. Handle timeouts and errors gracefully
    4. Support caching for repeated queries
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the name of this knowledge source."""
        pass

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        limit: int = 5,
        timeout_ms: float = 2000.0,
    ) -> list[ExternalResult]:
        """Retrieve relevant results for the query.

        Args:
            query: The search query text
            query_embedding: Optional query embedding for reranking
            limit: Maximum number of results to return
            timeout_ms: Maximum time to wait for results

        Returns:
            List of ExternalResult objects, sorted by relevance
        """
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this retriever is currently available."""
        pass

    def supports_embedding_search(self) -> bool:
        """Whether this retriever can use embeddings for search/reranking."""
        return False


# =============================================================================
# WIKIPEDIA RETRIEVER (PHASE 1b)
# =============================================================================

@dataclass
class WikipediaConfig:
    """Configuration for Wikipedia retriever."""
    api_url: str = "https://en.wikipedia.org/w/api.php"
    search_limit: int = 5
    extract_chars: int = 1000  # Characters to extract per article
    timeout_seconds: float = 2.0
    cache_ttl_hours: float = 24.0  # How long to cache results
    user_agent: str = "NeuralGraph/1.0 (Memory System; contact@example.com)"


class WikipediaRetriever(ExternalRetriever):
    """Wikipedia-based external retriever for open-domain facts.

    PHASE 1b: Implement a simple Wikipedia/HTTP client with caching.

    Uses the Wikipedia API to:
    1. Search for relevant articles
    2. Extract summaries/introductions
    3. Cache results for efficiency
    """

    def __init__(self, config: WikipediaConfig | None = None):
        self._config = config or WikipediaConfig()
        self._cache: dict[str, tuple[list[ExternalResult], float]] = {}  # query -> (results, timestamp)
        self._available = True
        self._last_error: str | None = None

    @property
    def source_name(self) -> str:
        return "wikipedia"

    async def retrieve(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        limit: int = 5,
        timeout_ms: float = 2000.0,
    ) -> list[ExternalResult]:
        """Retrieve Wikipedia articles relevant to the query."""
        # Check cache first
        cache_key = self._make_cache_key(query)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug(f"Wikipedia cache hit for: {query[:30]}...")
            return cached[:limit]

        try:
            # Search Wikipedia
            start_time = time.perf_counter()
            results = await self._search_and_extract(query, limit, timeout_ms)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            logger.info(
                f"Wikipedia retrieval: {len(results)} results for '{query[:30]}...' "
                f"in {elapsed_ms:.1f}ms"
            )

            # Cache results
            self._cache[cache_key] = (results, time.time())
            self._available = True

            return results

        except asyncio.TimeoutError:
            logger.warning(f"Wikipedia timeout for query: {query[:30]}...")
            self._last_error = "timeout"
            return []
        except Exception as e:
            logger.error(f"Wikipedia retrieval error: {e}")
            self._last_error = str(e)
            return []

    async def _search_and_extract(
        self,
        query: str,
        limit: int,
        timeout_ms: float,
    ) -> list[ExternalResult]:
        """Search Wikipedia and extract article summaries."""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Step 1: Search for articles
            search_params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit * 2,  # Get more to filter
                "format": "json",
            }

            async with session.get(
                self._config.api_url,
                params=search_params,
                headers={"User-Agent": self._config.user_agent}
            ) as response:
                response.raise_for_status()
                search_data = await response.json()

            search_results = search_data.get("query", {}).get("search", [])
            if not search_results:
                return []

            # Step 2: Get extracts for top results
            page_ids = [str(r["pageid"]) for r in search_results[:limit]]

            extract_params = {
                "action": "query",
                "pageids": "|".join(page_ids),
                "prop": "extracts|info",
                "exintro": "true",  # Only intro
                "explaintext": "true",  # Plain text, not HTML
                "excharacters": self._config.extract_chars,
                "inprop": "url",
                "format": "json",
            }

            async with session.get(
                self._config.api_url,
                params=extract_params,
                headers={"User-Agent": self._config.user_agent}
            ) as response:
                response.raise_for_status()
                extract_data = await response.json()

            pages = extract_data.get("query", {}).get("pages", {})

            # Step 3: Build results
            results = []
            for i, search_result in enumerate(search_results[:limit]):
                page_id = str(search_result["pageid"])
                page_data = pages.get(page_id, {})

                extract = page_data.get("extract", "")
                if not extract:
                    continue

                # Calculate relevance score based on search rank
                relevance = 1.0 - (i * 0.1)  # Simple rank-based scoring

                result = ExternalResult(
                    content=extract,
                    source="wikipedia",
                    source_url=page_data.get("fullurl"),
                    title=search_result.get("title"),
                    relevance_score=relevance,
                    confidence=0.9,  # Wikipedia is generally reliable
                    metadata={
                        "page_id": page_id,
                        "snippet": search_result.get("snippet", ""),
                        "word_count": search_result.get("wordcount", 0),
                    }
                )
                results.append(result)

            return results

    async def is_available(self) -> bool:
        """Check if Wikipedia API is reachable."""
        if not self._available:
            return False

        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=1.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                params = {"action": "query", "meta": "siteinfo", "format": "json"}
                async with session.get(
                    self._config.api_url,
                    params=params,
                    headers={"User-Agent": self._config.user_agent}
                ) as response:
                    self._available = response.status == 200
                    return self._available
        except Exception:
            self._available = False
            return False

    def _make_cache_key(self, query: str) -> str:
        """Generate a cache key for a query."""
        normalized = query.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()

    def _get_cached(self, cache_key: str) -> list[ExternalResult] | None:
        """Get cached results if not expired."""
        if cache_key not in self._cache:
            return None

        results, timestamp = self._cache[cache_key]
        age_hours = (time.time() - timestamp) / 3600

        if age_hours > self._config.cache_ttl_hours:
            del self._cache[cache_key]
            return None

        return results


# =============================================================================
# CACHED EXTERNAL RETRIEVER (PERSISTENT CACHE)
# =============================================================================

class PersistentExternalCache:
    """Persistent cache for external retrieval results.

    PHASE 3 Preview: Maintain a separate long-lived store for high-value
    open-domain facts retrieved externally.
    """

    def __init__(self, cache_dir: str | Path | None = None):
        self._cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".neuralgraph" / "external_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self._cache_dir / "index.json"
        self._index: dict[str, dict[str, Any]] = self._load_index()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        """Load the cache index from disk."""
        if self._index_file.exists():
            try:
                with open(self._index_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load external cache index: {e}")
        return {}

    def _save_index(self) -> None:
        """Save the cache index to disk."""
        try:
            with open(self._index_file, "w") as f:
                json.dump(self._index, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save external cache index: {e}")

    def get(self, query: str) -> list[ExternalResult] | None:
        """Get cached results for a query."""
        cache_key = hashlib.md5(query.lower().encode()).hexdigest()

        if cache_key not in self._index:
            return None

        entry = self._index[cache_key]
        cache_file = self._cache_dir / f"{cache_key}.json"

        if not cache_file.exists():
            del self._index[cache_key]
            return None

        try:
            with open(cache_file, "r") as f:
                data = json.load(f)
            return [ExternalResult.from_dict(r) for r in data["results"]]
        except Exception as e:
            logger.warning(f"Failed to load cached results: {e}")
            return None

    def put(self, query: str, results: list[ExternalResult]) -> None:
        """Cache results for a query."""
        cache_key = hashlib.md5(query.lower().encode()).hexdigest()
        cache_file = self._cache_dir / f"{cache_key}.json"

        try:
            data = {
                "query": query,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "results": [r.to_dict() for r in results],
            }
            with open(cache_file, "w") as f:
                json.dump(data, f, indent=2)

            self._index[cache_key] = {
                "query": query,
                "cached_at": data["cached_at"],
                "result_count": len(results),
            }
            self._save_index()
        except Exception as e:
            logger.warning(f"Failed to cache external results: {e}")

    def clear(self) -> int:
        """Clear all cached results. Returns count of cleared entries."""
        count = len(self._index)
        for cache_key in list(self._index.keys()):
            cache_file = self._cache_dir / f"{cache_key}.json"
            try:
                cache_file.unlink(missing_ok=True)
            except Exception:
                pass
        self._index = {}
        self._save_index()
        return count


# =============================================================================
# EXTERNAL RETRIEVER REGISTRY
# =============================================================================

class ExternalRetrieverRegistry:
    """Registry for managing multiple external retrievers.

    Allows adding/removing retrievers dynamically and provides
    unified retrieval across all registered sources.
    """

    def __init__(self):
        self._retrievers: dict[str, ExternalRetriever] = {}
        self._enabled: dict[str, bool] = {}

    def register(self, retriever: ExternalRetriever, enabled: bool = True) -> None:
        """Register an external retriever."""
        self._retrievers[retriever.source_name] = retriever
        self._enabled[retriever.source_name] = enabled
        logger.info(f"Registered external retriever: {retriever.source_name}")

    def unregister(self, source_name: str) -> bool:
        """Unregister a retriever by name."""
        if source_name in self._retrievers:
            del self._retrievers[source_name]
            del self._enabled[source_name]
            return True
        return False

    def set_enabled(self, source_name: str, enabled: bool) -> None:
        """Enable or disable a retriever."""
        if source_name in self._enabled:
            self._enabled[source_name] = enabled

    def get_enabled_retrievers(self) -> list[ExternalRetriever]:
        """Get list of currently enabled retrievers."""
        return [
            r for name, r in self._retrievers.items()
            if self._enabled.get(name, False)
        ]

    async def retrieve_all(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        limit_per_source: int = 5,
        timeout_ms: float = 3000.0,
    ) -> dict[str, list[ExternalResult]]:
        """Retrieve from all enabled sources in parallel.

        Returns:
            Dict mapping source name to list of results
        """
        enabled = self.get_enabled_retrievers()
        if not enabled:
            return {}

        # Run all retrievers in parallel
        tasks = [
            r.retrieve(query, query_embedding, limit_per_source, timeout_ms)
            for r in enabled
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, list[ExternalResult]] = {}
        for retriever, result in zip(enabled, results):
            if isinstance(result, Exception):
                logger.warning(f"External retriever {retriever.source_name} failed: {result}")
                output[retriever.source_name] = []
            else:
                output[retriever.source_name] = result

        return output


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================

def create_wikipedia_retriever(config: WikipediaConfig | None = None) -> WikipediaRetriever:
    """Create a Wikipedia retriever with optional config."""
    return WikipediaRetriever(config)


def create_default_registry() -> ExternalRetrieverRegistry:
    """Create a registry with default retrievers (Wikipedia enabled)."""
    registry = ExternalRetrieverRegistry()
    registry.register(WikipediaRetriever(), enabled=True)
    return registry


# =============================================================================
# OPEN-DOMAIN DETECTION HELPERS
# =============================================================================

def is_open_domain_query(query: str) -> tuple[bool, float]:
    """Detect if a query is likely open-domain (requires world knowledge).

    PHASE 1 FIX: Better detection of open-domain queries.

    Returns:
        Tuple of (is_open_domain, confidence)
    """
    import re

    query_lower = query.lower().strip()
    confidence = 0.0

    # Definitional patterns (highly likely open-domain)
    definitional_patterns = [
        r"^what is (?:a |an |the )?",
        r"^what are ",
        r"^who is ",
        r"^who was ",
        r"^define ",
        r"^explain ",
        r"^how does .* work",
        r"^why does ",
        r"^why do ",
        r"^what causes ",
        r"^what makes ",
    ]

    for pattern in definitional_patterns:
        if re.search(pattern, query_lower):
            confidence = max(confidence, 0.8)
            break

    # Factual patterns (moderately likely open-domain)
    factual_patterns = [
        r"^how many .* are there",
        r"^how much ",
        r"^when was .* invented",
        r"^when was .* discovered",
        r"^where is ",
        r"^what country ",
        r"^what year ",
        r"capital of",
        r"president of",
        r"population of",
        r"largest ",
        r"smallest ",
        r"oldest ",
        r"youngest ",
    ]

    for pattern in factual_patterns:
        if re.search(pattern, query_lower):
            confidence = max(confidence, 0.7)
            break

    # Scientific/technical terms (suggests open-domain)
    scientific_terms = [
        "photosynthesis", "evolution", "gravity", "atom", "molecule",
        "electron", "proton", "neutron", "cell", "dna", "rna",
        "gene", "chromosome", "protein", "enzyme", "virus", "bacteria",
        "planet", "star", "galaxy", "solar", "orbit", "mass",
        "energy", "force", "momentum", "velocity", "acceleration",
        "temperature", "pressure", "volume", "density",
    ]

    for term in scientific_terms:
        if term in query_lower:
            confidence = max(confidence, 0.6)
            break

    # Check for absence of conversational context markers
    # (entity names, pronouns referring to conversation)
    conversational_markers = [
        r"\bshe\b", r"\bhe\b", r"\bthey\b", r"\bwe\b",
        r"\byesterday\b", r"\blast week\b", r"\btoday\b",
        r"\bour\b", r"\bmy\b", r"\byour\b",
        r"\bthe meeting\b", r"\bthe conversation\b",
    ]

    has_conversational_marker = any(
        re.search(p, query_lower) for p in conversational_markers
    )

    if not has_conversational_marker and confidence < 0.5:
        # Query lacks conversational context, might be open-domain
        confidence = max(confidence, 0.4)

    is_open = confidence >= 0.5
    return is_open, confidence
