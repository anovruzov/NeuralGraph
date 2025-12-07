"""Date-aware BM25 reranker with boosted temporal token matching."""

import asyncio
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from .reranker import Reranker

# Common date patterns for extraction
DATE_PATTERNS = [
    # Month name patterns
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?\b",
    # Day of week
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
    # ISO dates
    r"\b\d{4}-\d{2}-\d{2}\b",
    # Numeric dates (US format)
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    # Years
    r"\b(19|20)\d{2}\b",
    # Relative time references
    r"\b(last|next|this)\s+(week|month|year|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
    # Time references
    r"\b(yesterday|today|tomorrow)\b",
]

# Month and day names for token extraction
MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
DAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]
TEMPORAL_KEYWORDS = [
    "when", "date", "time", "day", "week", "month", "year",
    "yesterday", "today", "tomorrow", "last", "next", "ago",
]


class DateAwareBM25RerankerParams(BaseModel):
    """Parameters for DateAwareBM25Reranker."""

    k1: float = Field(1.5, description="BM25 k1 parameter")
    b: float = Field(0.75, description="BM25 b parameter")
    epsilon: float = Field(0.25, description="BM25 epsilon parameter")
    date_boost: float = Field(
        2.5,
        description="Multiplier for date token matches (higher = more weight on dates)",
    )
    temporal_query_boost: float = Field(
        1.5,
        description="Extra boost when query contains temporal keywords",
    )
    tokenize: Callable[[str], list[str]] = Field(
        ...,
        description="Tokenizer function to split text into tokens",
    )


class DateAwareBM25Reranker(Reranker):
    """
    BM25 reranker with enhanced date/temporal token weighting.

    This reranker boosts scores when:
    1. Date tokens in the query match date tokens in candidates
    2. The query contains temporal keywords (when, date, etc.)
    """

    def __init__(self, params: DateAwareBM25RerankerParams) -> None:
        """Initialize a DateAwareBM25Reranker with the provided parameters."""
        super().__init__()

        self._k1 = params.k1
        self._b = params.b
        self._epsilon = params.epsilon
        self._date_boost = params.date_boost
        self._temporal_query_boost = params.temporal_query_boost
        self._tokenize = params.tokenize
        self._date_patterns = [re.compile(p, re.IGNORECASE) for p in DATE_PATTERNS]

    async def score(self, query: str, candidates: list[str]) -> list[float]:
        """Score candidates with date-aware BM25."""
        # Check if query is temporal in nature
        is_temporal_query = self._is_temporal_query(query)

        # Extract date tokens from query
        query_date_tokens = self._extract_date_tokens(query)

        # Standard tokenization
        tokenized_query_future = asyncio.to_thread(self._tokenize, query)
        tokenized_candidates_future = asyncio.to_thread(
            self._tokenize_multiple,
            candidates,
        )

        tokenized_query = await tokenized_query_future
        tokenized_candidates = await tokenized_candidates_future

        if not any(tokenized_candidates):
            return [0.0 for _ in candidates]

        # Standard BM25 scoring
        bm25 = BM25Okapi(
            tokenized_candidates,
            k1=self._k1,
            b=self._b,
            epsilon=self._epsilon,
        )
        base_scores = [float(score) for score in bm25.get_scores(tokenized_query)]

        # Apply date token boosting
        boosted_scores = []
        for i, (score, candidate) in enumerate(zip(base_scores, candidates, strict=True)):
            boost = 1.0

            # Extract date tokens from candidate
            candidate_date_tokens = self._extract_date_tokens(candidate)

            # Check for date token matches
            if query_date_tokens and candidate_date_tokens:
                matching_tokens = query_date_tokens & candidate_date_tokens
                if matching_tokens:
                    # Apply date boost proportional to number of matches
                    boost *= self._date_boost ** min(len(matching_tokens), 3)

            # Apply temporal query boost
            if is_temporal_query and candidate_date_tokens:
                boost *= self._temporal_query_boost

            boosted_scores.append(score * boost)

        return boosted_scores

    def _is_temporal_query(self, query: str) -> bool:
        """Check if query contains temporal keywords."""
        query_lower = query.lower()
        return any(kw in query_lower for kw in TEMPORAL_KEYWORDS)

    def _extract_date_tokens(self, text: str) -> set[str]:
        """Extract normalized date tokens from text."""
        tokens: set[str] = set()
        text_lower = text.lower()

        # Extract month names
        for month in MONTH_NAMES:
            if month in text_lower:
                tokens.add(month)

        # Extract day names
        for day in DAY_NAMES:
            if day in text_lower:
                tokens.add(day)

        # Extract years (19xx or 20xx)
        year_matches = re.findall(r"\b(19|20)\d{2}\b", text)
        tokens.update(year_matches)

        # Extract day numbers (1-31)
        for pattern in self._date_patterns:
            matches = pattern.findall(text)
            for match in matches:
                if isinstance(match, tuple):
                    tokens.update(m.lower() for m in match if m)
                else:
                    tokens.add(match.lower())

        return tokens

    def _tokenize_multiple(self, corpus: list[str]) -> list[list[str]]:
        return [self._tokenize(document) for document in corpus]
