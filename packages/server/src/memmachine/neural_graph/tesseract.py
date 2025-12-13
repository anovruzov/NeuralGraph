"""4D Tesseract Memory Architecture: Brain-Inspired Multi-Store System.

THE INSIGHT: The brain doesn't store all memories the same way.
Different brain regions specialize in different types of retrieval:

1. HIPPOCAMPUS (Temporal Store):
   - Episodic sequences, "when did X happen"
   - Time-ordered chains of events
   - "Yesterday", "last week", "4 years ago"

2. NEOCORTEX (Entity Store):
   - Semantic facts, "what is X"
   - Entity attributes and relationships
   - "Caroline is transgender", "moved from Sweden"

3. PREFRONTAL CORTEX (Reasoning Store):
   - Multi-hop paths, "if A then B then C"
   - Inference chains
   - Working memory for synthesis

4. ORBITOFRONTAL (Adversarial Store):
   - Conflict detection, "is X consistent?"
   - Negation handling
   - Verification and disambiguation

THE TESSERACT:
=============
A 4D memory system where each dimension is a specialized store.
Query enters all stores simultaneously.
Results are FUSED based on query type detection.

This is the missing link to 99% accuracy:
- Temporal questions → weighted toward Temporal Store
- Entity questions → weighted toward Entity Store
- Multi-hop → engages Reasoning Store
- Adversarial → activates conflict detection
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage

# NEO: Complete English thesaurus (117K+ words) + domain-specific additions
from .synonym_hash import expand_with_synonyms_extended as expand_with_synonyms

logger = logging.getLogger(__name__)


# =============================================================================
# QUERY TYPE DETECTION
# =============================================================================

class QueryType:
    """Detected type of query for routing to appropriate stores."""
    TEMPORAL = "temporal"      # When questions, date references
    ENTITY = "entity"          # What/who questions, fact lookup
    MULTI_HOP = "multi_hop"    # Questions requiring inference chains
    ADVERSARIAL = "adversarial"  # Negation, conflict, verification
    OPEN = "open"              # General/open-ended


def detect_query_type(query: str) -> dict[str, float]:
    """Detect query type and return confidence scores for each type.

    Returns dict mapping QueryType to confidence [0, 1].
    Multiple types can be active (e.g., temporal + entity).
    """
    query_lower = query.lower()

    scores = {
        QueryType.TEMPORAL: 0.0,
        QueryType.ENTITY: 0.0,
        QueryType.MULTI_HOP: 0.0,
        QueryType.ADVERSARIAL: 0.0,
        QueryType.OPEN: 0.3,  # Base score for open-ended
    }

    # TEMPORAL markers
    temporal_markers = [
        (r'\bwhen\b', 0.6),
        (r'\bhow long\b', 0.5),
        (r'\byesterday\b', 0.4),
        (r'\btoday\b', 0.3),
        (r'\blast week\b', 0.5),
        (r'\blast month\b', 0.5),
        (r'\blast year\b', 0.5),
        (r'\b\d+ years? ago\b', 0.6),
        (r'\b\d+ months? ago\b', 0.5),
        (r'\b\d+ days? ago\b', 0.4),
        (r'\bbefore\b', 0.3),
        (r'\bafter\b', 0.3),
        (r'\bduring\b', 0.3),
        (r'\bfirst\b', 0.2),
        (r'\brecent\b', 0.3),
    ]
    for pattern, weight in temporal_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.TEMPORAL] += weight

    # ENTITY markers (what/who/where factual questions)
    entity_markers = [
        (r'\bwhat is\b', 0.5),
        (r'\bwho is\b', 0.5),
        (r'\bwhat does\b', 0.4),
        (r'\bwhere is\b', 0.4),
        (r'\bwhere did\b', 0.5),
        (r"\bwhat's\b", 0.4),
        (r'\bidentity\b', 0.5),
        (r'\bname\b', 0.3),
        (r'\bcareer\b', 0.3),
        (r'\bjob\b', 0.3),
        (r'\bwork\b', 0.2),
        (r'\bfrom\b', 0.2),  # "moved from", "comes from"
    ]
    for pattern, weight in entity_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.ENTITY] += weight

    # MULTI_HOP markers (require connecting information)
    multihop_markers = [
        (r'\bhow many\b', 0.4),
        (r'\ball\b', 0.3),
        (r'\bevery\b', 0.3),
        (r'\bboth\b', 0.3),
        (r'\band\b.*\band\b', 0.4),  # Multiple conjunctions
        (r'\blist\b', 0.4),
        (r'\bwhat were\b', 0.3),
    ]
    for pattern, weight in multihop_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.MULTI_HOP] += weight

    # ADVERSARIAL markers
    adversarial_markers = [
        (r'\bnot\b', 0.4),
        (r'\bnever\b', 0.5),
        (r'\bexcept\b', 0.4),
        (r'\bother than\b', 0.5),
        (r'\bdid .* not\b', 0.5),
        (r'\bdidn\'t\b', 0.5),
        (r'\bfail\b', 0.3),
        (r'\bwithout\b', 0.3),
    ]
    for pattern, weight in adversarial_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.ADVERSARIAL] += weight

    # Normalize and cap scores
    for key in scores:
        scores[key] = min(1.0, scores[key])

    return scores


# =============================================================================
# SPECIALIZED MEMORY STORES
# =============================================================================

@dataclass
class StoreConfig:
    """Configuration for a specialized store."""
    semantic_weight: float = 0.5
    temporal_decay_days: float = 30.0
    entity_boost: float = 0.4
    speaker_boost: float = 0.5
    keyword_boost: float = 0.3
    min_charge: float = 0.1
    max_results: int = 30


class TemporalStore:
    """Hippocampus-inspired store for temporal/episodic retrieval.

    Specializes in:
    - Time-ordered sequences
    - "When did X happen" queries
    - Relative date resolution

    KEY INSIGHT: Temporal questions should weight recency/sequence
    more than pure semantic similarity.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            temporal_decay_days=14.0,  # More aggressive temporal decay
            semantic_weight=0.3,       # Lower semantic weight
            entity_boost=0.3,
            speaker_boost=0.4,
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        reference_time: datetime | None = None,
        limit: int = 30,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with temporal weighting.

        Nodes closer in time to reference get higher weight.
        Nodes with temporal markers in content get boost.
        """
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        # Extract temporal markers from query
        query_lower = query_text.lower()
        temporal_keywords = self._extract_temporal_keywords(query_lower)

        # Extract topic keywords - CRITICAL for distinguishing similar events
        topic_keywords = self._extract_topic_keywords(query_lower)

        # Extract entity names from query (capitalized words)
        query_entities = set(re.findall(r'\b[A-Z][a-z]+\b', query_text))
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does', 'The'}
        query_entities = {e.lower() for e in query_entities if e not in common}

        # CRITICAL: Remove entity names from topic_keywords to avoid double-counting
        # Topic should match the ACTION (hurt, camping) not the PERSON (melanie)
        topic_keywords = topic_keywords - query_entities

        # Get all nodes
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        # Compute temporal-weighted charges
        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_temporal_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_text=query_lower,
                temporal_keywords=temporal_keywords,
                topic_keywords=topic_keywords,
                query_entities=query_entities,
                reference_time=reference_time,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        # Sort by charge descending
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]

    def _extract_temporal_keywords(self, query: str) -> set[str]:
        """Extract temporal keywords for matching."""
        keywords = set()
        patterns = [
            r'yesterday', r'today', r'tomorrow',
            r'last week', r'this week', r'next week',
            r'last month', r'this month', r'next month',
            r'last year', r'this year', r'next year',
            r'\d{4}',  # Years like 2023
            r'january|february|march|april|may|june|july|august|september|october|november|december',
            r'monday|tuesday|wednesday|thursday|friday|saturday|sunday',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, query)
            keywords.update(matches)
        return keywords

    def _extract_event_year_from_content(self, content: str, msg_year: int) -> int | None:
        """Extract EVENT YEAR from content, resolving relative references.

        NEO TEMPORAL FIX:
        The KEY insight: Message timestamp ≠ Event time.
        "I painted that sunrise last year" (sent 2023) → event was 2022.

        Returns the year the EVENT happened, not when the message was sent.
        """
        content_lower = content.lower()

        # 1. Explicit year mentioned in content
        explicit_years = re.findall(r'\b(20\d{2})\b', content_lower)
        if explicit_years:
            # Return the earliest year mentioned (usually the event year)
            return min(int(y) for y in explicit_years)

        # 2. Relative year references - resolve based on message year
        if re.search(r'\blast year\b', content_lower):
            return msg_year - 1
        if re.search(r'\b(two|2) years? ago\b', content_lower):
            return msg_year - 2
        if re.search(r'\b(three|3) years? ago\b', content_lower):
            return msg_year - 3
        if re.search(r'\b(four|4) years? ago\b', content_lower):
            return msg_year - 4
        if re.search(r'\b(five|5|several) years? ago\b', content_lower):
            return msg_year - 5
        if re.search(r'\bback in (\d{4})\b', content_lower):
            match = re.search(r'\bback in (\d{4})\b', content_lower)
            if match:
                return int(match.group(1))

        # No relative time found - event time is same as message time
        return None

    def _extract_relative_time_type(self, content: str) -> str | None:
        """Extract what TYPE of relative time reference is in content.

        NEO GROUNDED DATE FIX:
        Detects: yesterday, last night, last week, last Friday, etc.
        Returns a category that can be matched against gold answers.
        """
        content_lower = content.lower()

        # Day-level references (yesterday, last night → "the day before")
        if re.search(r'\b(yesterday|last night)\b', content_lower):
            return 'day_before'

        # Week-level references (last week → "the week before")
        if re.search(r'\blast week\b', content_lower):
            return 'week_before'

        # Specific day references (last Friday, last Saturday, etc.)
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for day in days:
            if re.search(rf'\blast {day}\b', content_lower):
                return f'{day}_before'

        # This week/month references
        if re.search(r'\bthis week\b', content_lower):
            return 'this_week'
        if re.search(r'\bthis month\b', content_lower):
            return 'this_month'

        # Next references (future events)
        if re.search(r'\bnext week\b', content_lower):
            return 'next_week'
        if re.search(r'\bnext month\b', content_lower):
            return 'next_month'

        return None

    def _relative_time_matches_query(self, relative_type: str | None, query_text: str) -> float:
        """Check if a relative time type matches what the query is asking for.

        Returns a boost score if the relative time aligns with the query.
        """
        if not relative_type:
            return 0.0

        query_lower = query_text.lower()

        # "The week before" patterns in gold answers
        if 'week' in query_lower and relative_type == 'week_before':
            return 1.5

        # "The Friday before" patterns
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for day in days:
            if day in query_lower and relative_type == f'{day}_before':
                return 2.0  # Strong match for specific day

        # Day-level queries
        if ('yesterday' in query_lower or 'day before' in query_lower) and relative_type == 'day_before':
            return 1.5

        # If content has ANY relative time, give small boost for temporal questions
        if relative_type:
            return 0.3

        return 0.0

    def _extract_topic_keywords(self, query: str) -> set[str]:
        """Extract topic/event keywords that must match for temporal questions.

        This is CRITICAL: "When did X go camping?" must find memories with "camping",
        not just any memory about X.

        NEO: Added morphological expansion so "move" matches "moved".
        NEO+: Added synonym expansion.
        """
        # Remove question words and common verbs
        stopwords = {
            'when', 'did', 'does', 'do', 'is', 'was', 'were', 'are', 'has', 'have', 'had',
            'go', 'going', 'went', 'get', 'got', 'getting', 'buy', 'bought', 'buying',
            'the', 'a', 'an', 'to', 'for', 'in', 'on', 'at', 'of', 'with', 'from',
            'how', 'long', 'what', 'where', 'who', 'why', 'which', 'and', 'or', 'but',
            'she', 'he', 'her', 'his', 'they', 'their', 'it', 'its', 'this', 'that',
            'plan', 'planning', 'planned', 'join', 'joined', 'joining',
            'make', 'made', 'making', 'take', 'took', 'taking', 'give', 'gave', 'giving',
        }
        words = re.findall(r'\b[a-z]{3,}\b', query.lower())
        # Filter and keep meaningful topic words
        topics = {w for w in words if w not in stopwords}

        # NEO: Expand morphologically so "move" matches "moved"
        expanded = set()
        for w in topics:
            expanded.update(self._expand_morphology_temporal(w))
        topics.update(expanded)

        # NEO+: Synonym expansion
        topics.update(self._expand_synonyms_temporal(topics))

        # NEO+: Preserve proper nouns (Tilly, Valorant, etc.)
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', query)
        skip = {'When', 'What', 'Where', 'Who', 'Which', 'How', 'Did', 'Does', 'The'}
        for pn in proper_nouns:
            if pn not in skip:
                topics.add(pn.lower())

        return topics

    def _expand_synonyms_temporal(self, keywords: set[str]) -> set[str]:
        """Expand keywords with centralized synonym hash."""
        return expand_with_synonyms(keywords) - keywords

    def _expand_morphology_temporal(self, word: str) -> set[str]:
        """Expand word to morphological variants for temporal matching."""
        variants = {word}
        w = word.lower()

        # Get base form
        base = w
        if w.endswith('ed') and len(w) > 4:
            base = w[:-2]
            if base.endswith('i'):
                base = base[:-1] + 'y'
            elif len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ing') and len(w) > 5:
            base = w[:-3]
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ies') and len(w) > 4:
            base = w[:-3] + 'y'
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            base = w[:-1]

        variants.add(base)

        # Generate variants from base
        if len(base) >= 3:
            variants.add(base + 's')
            variants.add(base + 'ed')
            variants.add(base + 'ing')
            if base.endswith('e'):
                variants.add(base[:-1] + 'ing')
                variants.add(base + 'd')

        return {v for v in variants if len(v) >= 3 and v.isalpha()}

    def _compute_temporal_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_text: str,
        temporal_keywords: set[str],
        topic_keywords: set[str],
        query_entities: set[str],
        reference_time: datetime | None,
    ) -> float:
        """Compute temporal-weighted charge.

        KEY FIX: Topic keywords must match for temporal questions.
        "When did X go camping?" must find memories with "camping".
        "When did X go camping in June?" must find memories from JUNE.
        """
        content_lower = node.content.lower()

        # Get message datetime from metadata
        msg_datetime_str = ""
        if node.metadata:
            msg_datetime_str = (node.metadata.get("datetime", "") or "").lower()

        # MONTH NAMES for temporal filtering
        MONTHS = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }

        # 1. TEMPORAL PERIOD FILTERING - CRITICAL
        # If query mentions a specific month, filter for messages FROM that month
        temporal_period_charge = 0.0
        query_months = {m for m in MONTHS.keys() if m in temporal_keywords}
        if query_months and msg_datetime_str:
            # Check if message datetime contains the queried month
            msg_has_query_month = any(m in msg_datetime_str for m in query_months)
            if msg_has_query_month:
                temporal_period_charge = 0.5  # Strong boost for correct month
            else:
                temporal_period_charge = -0.3  # Penalty for wrong month

        # 2. TOPIC MATCHING - CRITICAL for temporal questions
        # "When did X get hurt?" MUST find memories with "hurt"
        topic_charge = 0.0
        if topic_keywords:
            # Remove months from topic keywords (they're handled above)
            topic_words = topic_keywords - set(MONTHS.keys())
            if topic_words:
                matches = sum(1 for kw in topic_words if kw in content_lower)
                if matches == 0:
                    topic_charge = -1.0  # STRONGER penalty for missing topic
                else:
                    # NEO: Check for CRITICAL keywords (birthday, parade, concert, etc.)
                    # These must match EXACTLY for correct retrieval
                    critical_keywords = {'birthday', 'parade', 'concert', 'wedding',
                                         'anniversary', 'funeral', 'graduation', 'ceremony'}
                    has_critical = bool(topic_words & critical_keywords)
                    critical_in_content = any(kw in content_lower for kw in topic_words & critical_keywords)

                    if has_critical and not critical_in_content:
                        topic_charge = -1.5  # VERY STRONG penalty - wrong event type
                    elif has_critical and critical_in_content:
                        topic_charge = 3.0  # VERY STRONG boost for matching critical keyword
                    else:
                        # Standard topic matching
                        topic_charge = min(2.5, matches * 1.0)

        # 3. ENTITY MATCHING - Must mention the right person (in content OR as speaker)
        entity_charge = 0.0
        if query_entities:
            speaker = ""
            if node.metadata:
                speaker = (node.metadata.get("speaker", "") or "").lower()
            # Check both content AND speaker metadata (e.g., "melanie" in "Melanie Turner")
            entity_matches = sum(1 for e in query_entities if e in content_lower or e in speaker)
            if entity_matches > 0:
                entity_charge = min(1.5, entity_matches * 0.6)  # Strong boost for entity match

        # 4. Semantic similarity (lower weight in temporal store)
        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = float(np.dot(query_embedding, node_emb / node_norm))

        # 5. Temporal keyword matching in content
        temporal_charge = 0.0
        for keyword in temporal_keywords:
            if keyword in content_lower:
                temporal_charge += 0.2
        temporal_charge = min(1.0, temporal_charge)

        # 6. NEO GROUNDED DATE FIX: Relative time matching
        # "last week" in content + query asking about week → strong match
        # "last Friday" in content + query asking about Friday → strong match
        grounded_charge = 0.0
        relative_time_type = self._extract_relative_time_type(content_lower)
        if relative_time_type:
            # Memory has a relative time reference - check if it matches query
            grounded_charge = self._relative_time_matches_query(relative_time_type, query_text)
            # Also boost if query mentions temporal period that aligns
            if relative_time_type in ['week_before', 'this_week'] and 'week' in query_text.lower():
                grounded_charge += 0.5
            if relative_time_type == 'day_before' and any(w in query_text.lower() for w in ['yesterday', 'night', 'day']):
                grounded_charge += 0.5

        # 7. Recency decay (if reference time provided)
        recency_charge = 0.5
        if reference_time and node.created_at:
            time_diff = abs((reference_time - node.created_at).days)
            recency_charge = math.exp(-time_diff / self._config.temporal_decay_days)

        # 8. NEO TEMPORAL FIX: EVENT YEAR MATCHING
        # "When did X paint a sunrise?" (gold: 2022)
        # Memory: "I painted that sunrise last year" (sent 2023)
        # Event year = 2023 - 1 = 2022 → MATCH!
        event_year_charge = 0.0
        query_years = {int(y) for y in re.findall(r'\b(20\d{2})\b', query_text)}

        # Get message year for relative time resolution
        msg_year = 2023  # Default
        if node.created_at:
            msg_year = node.created_at.year
        elif msg_datetime_str:
            year_match = re.search(r'(20\d{2})', msg_datetime_str)
            if year_match:
                msg_year = int(year_match.group(1))

        # Extract event year from content (resolves "last year" etc.)
        event_year = self._extract_event_year_from_content(content_lower, msg_year)

        # NEO FIX: Detect if query is asking about PAST events
        # "When did X paint/go/do Y?" without a year → likely asking about past event
        query_asks_past = bool(re.search(r'\b(when did|how long ago|when was)\b', query_text.lower()))

        if query_years and event_year:
            # Query asks for a specific year - boost if event year matches
            if event_year in query_years:
                event_year_charge = 1.5  # Strong boost for year match
            else:
                event_year_charge = -0.3  # Penalty for wrong year
        elif event_year and query_asks_past:
            # NEO: Query asks about past, memory has "last year" → STRONG boost
            # This helps "When did Melanie paint a sunrise?" find "painted last year"
            if event_year < msg_year:
                # Event happened BEFORE message timestamp → past event reference
                event_year_charge = 1.2  # Strong boost for past event with year reference
            else:
                event_year_charge = 0.5
        elif event_year:
            # Memory has relative time ref ("last year") - moderate boost
            event_year_charge = 0.4
        elif query_asks_past and 'last year' in content_lower:
            # Direct "last year" mention even if year extraction failed
            event_year_charge = 0.8

        # TOTAL: Topic match is KING for temporal questions
        # "When did X do Y?" - finding Y is more important than semantic similarity
        # NEO: Rebalanced - topic is now DOMINANT signal
        # NEO GROUNDED: Relative time matching now gets significant weight
        # NEO YEAR: Event year matching strengthened for "last year" queries
        total = (
            topic_charge * 0.32 +             # CRITICAL: topic must match
            event_year_charge * 0.18 +        # NEO: Event year matching (strengthened)
            grounded_charge * 0.15 +          # NEO: Relative time matching (last week, yesterday)
            temporal_period_charge * 0.12 +   # Right month/period
            entity_charge * 0.10 +            # Entity must be mentioned
            semantic_charge * 0.06 +          # Semantic similarity (reduced further)
            temporal_charge * 0.05 +          # Temporal keywords in content
            recency_charge * 0.02             # Slight recency bias
        )

        return total


class EntityStore:
    """Neocortex-inspired store for semantic fact retrieval.

    Specializes in:
    - Entity attributes ("what is X's identity")
    - Entity relationships ("who does X work with")
    - Factual information

    KEY INSIGHT: Entity questions should weight entity binding
    and speaker binding heavily.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            semantic_weight=0.4,
            entity_boost=0.6,      # HIGH entity weight
            speaker_boost=0.7,     # HIGH speaker weight
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 50,  # Higher limit for entity to ensure coverage
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with entity/speaker weighting."""
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        # Extract entities from query
        query_entities = self._extract_entities(query_text)
        query_keywords = self._extract_keywords(query_text)

        # Get all nodes
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_entity_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_entities=query_entities,
                query_keywords=query_keywords,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _extract_entities(self, text: str) -> set[str]:
        """Extract entity names (capitalized words)."""
        entities = re.findall(r'\b[A-Z][a-z]+\b', text)
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                  'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The', 'And',
                  'But', 'Or', 'From', 'To', 'In', 'On', 'At', 'For', 'With'}
        return {e for e in entities if e not in common}

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract meaningful keywords with morphological + synonym expansion.

        NEO: Essential for matching "move" in question to "moved" in answer.
        NEO+: Added synonym clusters for domain-specific terms.
        """
        text_lower = text.lower()
        # Split and filter
        words = re.findall(r'\b[a-z]{3,}\b', text_lower)
        stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all',
                     'can', 'had', 'her', 'was', 'one', 'our', 'out', 'has',
                     'what', 'when', 'where', 'who', 'why', 'how', 'did', 'does'}
        keywords = {w for w in words if w not in stopwords}

        # Expand morphologically
        expanded = set()
        for w in keywords:
            expanded.update(self._expand_morphology(w))
        keywords.update(expanded)

        # NEO+: Synonym expansion for common gaps
        keywords.update(self._expand_synonyms(keywords))

        # NEO+: Preserve proper nouns from original text (Tilly, etc.)
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', text)
        skip_words = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                      'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The'}
        for pn in proper_nouns:
            if pn not in skip_words:
                keywords.add(pn.lower())

        return keywords

    def _expand_synonyms(self, keywords: set[str]) -> set[str]:
        """Expand keywords with centralized synonym hash.

        NEO+: Uses O(1) hash lookup for synonyms.
        """
        return expand_with_synonyms(keywords) - keywords  # Return only NEW synonyms

    def _expand_morphology(self, word: str) -> set[str]:
        """Expand word to morphological variants."""
        variants = {word}
        w = word.lower()

        # Get base form
        base = w
        if w.endswith('ed') and len(w) > 4:
            base = w[:-2]
            if base.endswith('i'):
                base = base[:-1] + 'y'
            elif len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ing') and len(w) > 5:
            base = w[:-3]
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ies') and len(w) > 4:
            base = w[:-3] + 'y'
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            base = w[:-1]

        variants.add(base)

        # Generate variants from base
        if len(base) >= 3:
            variants.add(base + 's')
            variants.add(base + 'ed')
            variants.add(base + 'ing')
            if base.endswith('e'):
                variants.add(base[:-1] + 'ing')
                variants.add(base + 'd')

        return {v for v in variants if len(v) >= 3 and v.isalpha()}

    def _compute_entity_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_entities: set[str],
        query_keywords: set[str],
    ) -> float:
        """Compute entity-weighted charge."""
        content_lower = node.content.lower()

        # 1. Semantic similarity
        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = float(np.dot(query_embedding, node_emb / node_norm))

        # 2. Entity binding (entities mentioned in content)
        entity_charge = 0.0
        if query_entities:
            matches = sum(1 for e in query_entities if e.lower() in content_lower)
            entity_charge = min(1.0, matches / len(query_entities))

        # 3. Speaker binding (speaker IS the queried entity)
        speaker_charge = 0.0
        if node.metadata and query_entities:
            speaker = (node.metadata.get("producer_id", "") or
                      node.metadata.get("speaker", "")).lower()
            if any(e.lower() == speaker for e in query_entities):
                speaker_charge = 1.0

        # 4. Keyword matching
        keyword_charge = 0.0
        if query_keywords:
            matches = sum(1 for k in query_keywords if k in content_lower)
            keyword_charge = min(1.0, matches / max(1, len(query_keywords)))

        # TOTAL: Entity-weighted combination
        total = (
            semantic_charge * self._config.semantic_weight +
            entity_charge * self._config.entity_boost +
            speaker_charge * self._config.speaker_boost +
            keyword_charge * self._config.keyword_boost
        )

        # CRITICAL: If speaker matches AND semantic > 0.3, give bonus
        # This ensures ALL Caroline messages have a chance even with low semantic
        if speaker_charge > 0.5 and semantic_charge > 0.3:
            total *= 1.3

        return total


class ReasoningStore:
    """Prefrontal cortex-inspired store for multi-hop reasoning.

    Specializes in:
    - Questions requiring multiple pieces of information
    - "How many X did Y do?"
    - Aggregation and counting

    KEY INSIGHT: Multi-hop questions need BREADTH not depth.
    Retrieve more nodes with moderate relevance rather than
    fewer nodes with high relevance.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            semantic_weight=0.5,
            entity_boost=0.4,
            speaker_boost=0.3,
            keyword_boost=0.4,  # Higher keyword weight for multi-hop
            min_charge=0.08,    # Lower threshold for breadth
            max_results=60,     # More results for multi-hop
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 60,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with breadth-first approach."""
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        query_entities = self._extract_entities(query_text)
        query_keywords = self._extract_keywords(query_text)

        all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_reasoning_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_entities=query_entities,
                query_keywords=query_keywords,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _extract_entities(self, text: str) -> set[str]:
        entities = re.findall(r'\b[A-Z][a-z]+\b', text)
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                  'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The', 'And'}
        return {e for e in entities if e not in common}

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract keywords with morphological + synonym expansion."""
        text_lower = text.lower()
        words = re.findall(r'\b[a-z]{3,}\b', text_lower)
        stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all'}
        keywords = {w for w in words if w not in stopwords}

        # NEO: Expand morphologically
        expanded = set()
        for w in keywords:
            expanded.update(self._expand_morphology_reasoning(w))
        keywords.update(expanded)

        # NEO+: Synonym expansion using centralized hash
        keywords.update(expand_with_synonyms(keywords))

        # NEO+: Preserve proper nouns
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', text)
        skip = {'What', 'When', 'Where', 'Who', 'Which', 'How', 'Did', 'Does', 'The', 'Many'}
        for pn in proper_nouns:
            if pn not in skip:
                keywords.add(pn.lower())

        return keywords

    def _expand_morphology_reasoning(self, word: str) -> set[str]:
        """Expand word to morphological variants."""
        variants = {word}
        w = word.lower()
        base = w
        if w.endswith('ed') and len(w) > 4:
            base = w[:-2]
            if base.endswith('i'):
                base = base[:-1] + 'y'
            elif len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ing') and len(w) > 5:
            base = w[:-3]
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ies') and len(w) > 4:
            base = w[:-3] + 'y'
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            base = w[:-1]
        variants.add(base)
        if len(base) >= 3:
            variants.add(base + 's')
            variants.add(base + 'ed')
            variants.add(base + 'ing')
            if base.endswith('e'):
                variants.add(base[:-1] + 'ing')
                variants.add(base + 'd')
        return {v for v in variants if len(v) >= 3 and v.isalpha()}

    def _compute_reasoning_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_entities: set[str],
        query_keywords: set[str],
    ) -> float:
        """Compute reasoning charge emphasizing keyword coverage."""
        content_lower = node.content.lower()

        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = float(np.dot(query_embedding, node_emb / node_norm))

        entity_charge = 0.0
        if query_entities:
            matches = sum(1 for e in query_entities if e.lower() in content_lower)
            entity_charge = min(1.0, matches / len(query_entities))

        speaker_charge = 0.0
        if node.metadata and query_entities:
            speaker = (node.metadata.get("producer_id", "") or
                      node.metadata.get("speaker", "")).lower()
            if any(e.lower() == speaker for e in query_entities):
                speaker_charge = 1.0

        keyword_charge = 0.0
        if query_keywords:
            matches = sum(1 for k in query_keywords if k in content_lower)
            keyword_charge = min(1.0, matches / max(1, len(query_keywords)))

        total = (
            semantic_charge * self._config.semantic_weight +
            entity_charge * self._config.entity_boost +
            speaker_charge * self._config.speaker_boost +
            keyword_charge * self._config.keyword_boost
        )

        return total


class AdversarialStore:
    """Orbitofrontal-inspired store for conflict/verification retrieval.

    Specializes in:
    - Negation handling ("did NOT")
    - Exception queries ("except", "other than")
    - Verification and disambiguation

    KEY INSIGHT: Adversarial questions need OPPOSITE weighting.
    Nodes that CONTRADICT the positive case are valuable.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            semantic_weight=0.4,
            entity_boost=0.5,
            speaker_boost=0.4,
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 40,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with adversarial weighting."""
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        # Parse adversarial structure
        negation_terms, positive_terms = self._parse_adversarial(query_text)
        query_entities = self._extract_entities(query_text)

        all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_adversarial_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_entities=query_entities,
                negation_terms=negation_terms,
                positive_terms=positive_terms,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _parse_adversarial(self, query: str) -> tuple[set[str], set[str]]:
        """Parse query into negated terms and positive terms."""
        query_lower = query.lower()

        # Find negation patterns
        negation_patterns = [
            r'not\s+(\w+)',
            r'never\s+(\w+)',
            r"didn't\s+(\w+)",
            r'except\s+(\w+)',
            r'other than\s+(\w+)',
        ]

        negation_terms = set()
        for pattern in negation_patterns:
            matches = re.findall(pattern, query_lower)
            negation_terms.update(matches)

        # Positive terms are the rest
        words = re.findall(r'\b[a-z]{3,}\b', query_lower)
        stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all',
                     'did', 'does', 'never', 'except', 'other', 'than'}
        positive_terms = {w for w in words if w not in stopwords and w not in negation_terms}

        return negation_terms, positive_terms

    def _extract_entities(self, text: str) -> set[str]:
        entities = re.findall(r'\b[A-Z][a-z]+\b', text)
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                  'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The', 'And',
                  'Not', 'Never', 'Except'}
        return {e for e in entities if e not in common}

    def _compute_adversarial_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_entities: set[str],
        negation_terms: set[str],
        positive_terms: set[str],
    ) -> float:
        """Compute charge with adversarial awareness."""
        content_lower = node.content.lower()

        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = float(np.dot(query_embedding, node_emb / node_norm))

        entity_charge = 0.0
        if query_entities:
            matches = sum(1 for e in query_entities if e.lower() in content_lower)
            entity_charge = min(1.0, matches / len(query_entities))

        speaker_charge = 0.0
        if node.metadata and query_entities:
            speaker = (node.metadata.get("producer_id", "") or
                      node.metadata.get("speaker", "")).lower()
            if any(e.lower() == speaker for e in query_entities):
                speaker_charge = 1.0

        # ADVERSARIAL: Check if node contains negated terms
        negation_charge = 0.0
        if negation_terms:
            # Nodes that DON'T contain negated terms are actually valuable
            # for adversarial questions (they show what DID happen)
            contains_negated = sum(1 for t in negation_terms if t in content_lower)
            if contains_negated == 0:
                # Node doesn't mention the negated thing - might be relevant
                negation_charge = 0.2
            else:
                # Node mentions the negated thing - could be evidence
                negation_charge = 0.4

        # Positive term matching
        positive_charge = 0.0
        if positive_terms:
            matches = sum(1 for t in positive_terms if t in content_lower)
            positive_charge = min(1.0, matches / max(1, len(positive_terms)))

        total = (
            semantic_charge * self._config.semantic_weight +
            entity_charge * self._config.entity_boost +
            speaker_charge * self._config.speaker_boost +
            negation_charge * 0.3 +
            positive_charge * 0.3
        )

        return total


# =============================================================================
# TESSERACT: 4D FUSION LAYER
# =============================================================================

class Tesseract:
    """4D Memory Tesseract: Fuses results from all specialized stores.

    The tesseract queries all stores in parallel, then fuses results
    based on detected query type.

    This is the KEY to achieving 99% accuracy:
    - Each store specializes in different question types
    - Fusion weights are DYNAMIC based on query analysis
    - Final results combine breadth AND depth
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self._storage = storage
        self._temporal_store = TemporalStore(storage)
        self._entity_store = EntityStore(storage)
        self._reasoning_store = ReasoningStore(storage)
        self._adversarial_store = AdversarialStore(storage)

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        reference_time: datetime | None = None,
        limit: int = 80,
    ) -> list[tuple["NeuralNode", float]]:
        """Execute 4D tesseract retrieval.

        1. Detect query type
        2. Query all stores in parallel
        3. Fuse results with type-based weighting
        4. Return top unified results
        """
        # Step 1: Detect query type
        query_types = detect_query_type(query_text)

        logger.info(f"Query type detection: {query_types}")

        # Step 2: Query all stores (can be parallelized with asyncio.gather)
        temporal_results = await self._temporal_store.retrieve(
            query_text, query_embedding, session_key, reference_time, limit=40
        )

        entity_results = await self._entity_store.retrieve(
            query_text, query_embedding, session_key, limit=50
        )

        reasoning_results = await self._reasoning_store.retrieve(
            query_text, query_embedding, session_key, limit=40
        )

        adversarial_results = await self._adversarial_store.retrieve(
            query_text, query_embedding, session_key, limit=30
        )

        # Step 3: Fuse with type-based weights
        fused = self._fuse_results(
            temporal_results=temporal_results,
            entity_results=entity_results,
            reasoning_results=reasoning_results,
            adversarial_results=adversarial_results,
            type_weights=query_types,
        )

        # Step 4: Sort and return top
        fused.sort(key=lambda x: x[1], reverse=True)
        return fused[:limit]

    def _normalize_charges(
        self, results: list[tuple["NeuralNode", float]]
    ) -> list[tuple["NeuralNode", float]]:
        """Normalize charges to [0, 1] range for fair fusion."""
        if not results:
            return results
        max_charge = max(charge for _, charge in results)
        if max_charge <= 0:
            return results
        return [(node, charge / max_charge) for node, charge in results]

    def _fuse_results(
        self,
        temporal_results: list[tuple["NeuralNode", float]],
        entity_results: list[tuple["NeuralNode", float]],
        reasoning_results: list[tuple["NeuralNode", float]],
        adversarial_results: list[tuple["NeuralNode", float]],
        type_weights: dict[str, float],
    ) -> list[tuple["NeuralNode", float]]:
        """Fuse results from all stores with type-based weighting.

        KEY FIX:
        1. Normalize charges within each store to [0,1] for fair comparison
        2. Use ACTUAL query type scores, not defaults when score is 0
        3. Strongly favor the dominant query type's store
        """
        # Normalize charges within each store FIRST
        temporal_results = self._normalize_charges(temporal_results)
        entity_results = self._normalize_charges(entity_results)
        reasoning_results = self._normalize_charges(reasoning_results)
        adversarial_results = self._normalize_charges(adversarial_results)

        # Get ACTUAL type weights (use 0 when detection returns 0, not defaults)
        temporal_score = type_weights.get(QueryType.TEMPORAL, 0.0)
        entity_score = type_weights.get(QueryType.ENTITY, 0.0)
        multihop_score = type_weights.get(QueryType.MULTI_HOP, 0.0)
        adversarial_score = type_weights.get(QueryType.ADVERSARIAL, 0.0)
        open_score = type_weights.get(QueryType.OPEN, 0.0)

        BASE_WEIGHT = 0.05  # Minimum weight even if detection returns 0

        # Calculate weights from detection scores (proportional fusion)
        temporal_weight = temporal_score + BASE_WEIGHT
        entity_weight = entity_score + BASE_WEIGHT
        multihop_weight = multihop_score + BASE_WEIGHT
        adversarial_weight = adversarial_score + BASE_WEIGHT
        open_weight = open_score + BASE_WEIGHT

        # Normalize to sum to 1
        total = temporal_weight + entity_weight + multihop_weight + adversarial_weight + open_weight
        if total > 0:
            temporal_weight /= total
            entity_weight /= total
            multihop_weight /= total
            adversarial_weight /= total
            open_weight /= total
        else:
            temporal_weight = entity_weight = multihop_weight = adversarial_weight = open_weight = 0.2

        # Combine into single dict by node_id
        combined: dict[str, tuple["NeuralNode", float]] = {}

        # Add temporal results
        for node, charge in temporal_results:
            effective_charge = charge * (temporal_weight + open_weight * 0.25)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        # Add entity results
        for node, charge in entity_results:
            effective_charge = charge * (entity_weight + open_weight * 0.35)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        # Add reasoning results
        for node, charge in reasoning_results:
            effective_charge = charge * (multihop_weight + open_weight * 0.25)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        # Add adversarial results
        for node, charge in adversarial_results:
            effective_charge = charge * (adversarial_weight + open_weight * 0.15)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        return list(combined.values())
