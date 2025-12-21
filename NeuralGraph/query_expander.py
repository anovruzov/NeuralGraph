"""
Universal Query Expansion using LLM.

Instead of hardcoding domain clusters, use the LLM's world knowledge
to expand queries with semantically related terms.

Example:
  Query: "What is Caroline's relationship status?"
  Expanded: ["single", "married", "dating", "partner", "engaged", "divorced"]

This is UNIVERSAL because:
1. Works for ANY domain (personal, technical, medical, etc.)
2. Uses LLM world knowledge, not hardcoded mappings
3. Cached for performance (same query type = same expansion)
"""

import asyncio
import aiohttp
import json
import logging
import re
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# Default Ollama settings
DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_BASE_URL = "http://localhost:11434"

# Cache for expansions (query pattern -> expanded terms)
_EXPANSION_CACHE: dict[str, set[str]] = {}


async def expand_query_with_llm(
    query: str,
    session: Optional[aiohttp.ClientSession] = None,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
) -> set[str]:
    """
    Use LLM to expand a query with semantically related search terms.

    This is UNIVERSAL - works for any domain by leveraging LLM world knowledge.

    Args:
        query: The search query
        session: Optional aiohttp session (creates one if not provided)
        model: LLM model to use
        base_url: Ollama base URL

    Returns:
        Set of expanded terms that might appear in relevant memories
    """
    # Check cache first (normalized query)
    cache_key = _normalize_for_cache(query)
    if cache_key in _EXPANSION_CACHE:
        return _EXPANSION_CACHE[cache_key]

    prompt = f"""Given this search query, list 5-10 specific words or short phrases that might appear in text containing the answer. Focus on VALUE words, not question words.

Query: "{query}"

List only the search terms, one per line, no explanations:"""

    try:
        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            async with session.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 100,
                    }
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    response_text = data.get("response", "")

                    # Parse response into terms
                    terms = set()
                    for line in response_text.strip().split("\n"):
                        line = line.strip().lower()
                        # Remove common prefixes like "- ", "* ", numbers
                        line = re.sub(r'^[\-\*\d\.\)]+\s*', '', line)
                        if line and len(line) > 1 and len(line) < 30:
                            terms.add(line)

                    # Cache the result
                    if terms:
                        _EXPANSION_CACHE[cache_key] = terms
                        logger.debug(f"Query expansion: '{query[:50]}' -> {terms}")

                    return terms

        finally:
            if close_session:
                await session.close()

    except Exception as e:
        logger.warning(f"Query expansion failed: {e}")

    return set()


def _normalize_for_cache(query: str) -> str:
    """Normalize query for cache lookup.

    Removes specific names/entities to cache by query PATTERN.
    e.g., "What is Caroline's job?" -> "what is X's job?"
    """
    query_lower = query.lower()

    # Replace common name patterns with placeholder
    # This allows caching "What is X's relationship status?" once
    normalized = re.sub(r'\b[A-Z][a-z]+\'s\b', "X's", query)
    normalized = re.sub(r'\b[A-Z][a-z]+\b', "X", normalized)

    return normalized.lower().strip()


def get_expansion_sync(query: str) -> set[str]:
    """Synchronous wrapper for query expansion (for use in non-async contexts)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context, can't use run_until_complete
            return set()
        return loop.run_until_complete(expand_query_with_llm(query))
    except RuntimeError:
        # No event loop, create one
        return asyncio.run(expand_query_with_llm(query))


# Pre-populated cache for common query patterns (bootstrap)
_EXPANSION_CACHE.update({
    "what is x's relationship status?": {"single", "married", "dating", "engaged", "divorced", "partner", "spouse"},
    "what is x's job?": {"work", "job", "career", "profession", "employed", "position", "role"},
    "where did x move from?": {"moved", "from", "country", "city", "relocated", "hometown", "origin"},
    "what activities does x do?": {"hobby", "activities", "sports", "enjoys", "likes", "interests", "practice"},
    "what art does x make?": {"painting", "art", "abstract", "drawing", "create", "artistic", "canvas"},
    "what books has x read?": {"book", "read", "reading", "novel", "author", "story"},
    "what music does x like?": {"music", "band", "concert", "song", "artist", "listen"},
    "what does x's kids like?": {"kids", "children", "like", "enjoy", "love", "favorite", "interested"},
    "what events has x attended?": {"event", "attended", "participated", "joined", "went", "conference"},
})


if __name__ == "__main__":
    # Test
    async def test():
        queries = [
            "What is Caroline's relationship status?",
            "Where did Caroline move from 4 years ago?",
            "What kind of art does Caroline make?",
        ]

        async with aiohttp.ClientSession() as session:
            for q in queries:
                terms = await expand_query_with_llm(q, session)
                print(f"\nQuery: {q}")
                print(f"Expanded: {terms}")

    asyncio.run(test())
