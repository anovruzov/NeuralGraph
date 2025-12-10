"""Query Decomposition for Graph-First Retrieval.

The key insight for 92%+ accuracy:
1. Embedding search finds SEMANTICALLY similar text
2. But QA requires finding FACTUALLY relevant memories
3. "What is Caroline's job?" needs memories about Caroline + job, not semantic similarity

This module decomposes queries into structured components:
- Entities: Named entities mentioned (Caroline, Melanie, Sweden)
- Relations: What relationship is being asked about (job, location, action)
- Temporal: Time constraints (when, last year, 4 years ago)
- Attributes: Properties being queried (name, age, profession)

Then routes to the appropriate retrieval strategy:
- Entity queries → Graph traversal from entity nodes
- Temporal queries → Time-filtered search
- Relation queries → Edge-following from source to target
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


class QueryType(Enum):
    """Types of queries for routing."""
    ENTITY_ATTRIBUTE = auto()  # "What is X's job?" → Find entity X, look for attribute
    ENTITY_ACTION = auto()     # "What did X do?" → Find entity X, look for actions
    ENTITY_TEMPORAL = auto()   # "When did X do Y?" → Find entity X + action Y, extract time
    ENTITY_RELATION = auto()   # "Who is X's friend?" → Find entity X, follow relation edges
    TEMPORAL_EVENT = auto()    # "What happened in 2022?" → Time-filtered search
    MULTI_HOP = auto()         # "What does X's friend do?" → Chain: X → friend → job
    GENERAL = auto()           # Fallback to embedding search


@dataclass
class DecomposedQuery:
    """Structured query representation."""

    # Original query
    original: str

    # Query type for routing
    query_type: QueryType

    # Extracted components
    entities: list[str] = field(default_factory=list)  # Named entities
    relations: list[str] = field(default_factory=list)  # Relationship types being queried
    attributes: list[str] = field(default_factory=list)  # Attributes being queried
    actions: list[str] = field(default_factory=list)  # Actions mentioned
    temporal_markers: list[str] = field(default_factory=list)  # Time references

    # Keywords for filtering
    key_terms: list[str] = field(default_factory=list)  # Important non-entity words

    # Confidence
    confidence: float = 0.5


class QueryDecomposer:
    """Decomposes natural language queries into structured components.

    This is essential for graph-first retrieval:
    1. Extract entities → Use entity index for O(1) lookup
    2. Identify query type → Route to appropriate strategy
    3. Extract constraints → Filter results accurately
    """

    # Entity patterns (capitalized words, excluding common words)
    STOP_WORDS = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need',
        'what', 'when', 'where', 'who', 'whom', 'which', 'why', 'how',
        'this', 'that', 'these', 'those', 'it', 'its',
        'i', 'you', 'he', 'she', 'we', 'they', 'me', 'him', 'her', 'us', 'them',
        'my', 'your', 'his', 'her', 'our', 'their',
        'and', 'or', 'but', 'if', 'because', 'as', 'until', 'while',
        'of', 'at', 'by', 'for', 'with', 'about', 'against', 'between',
        'into', 'through', 'during', 'before', 'after', 'above', 'below',
        'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over', 'under',
        'again', 'further', 'then', 'once', 'here', 'there', 'all', 'each',
        'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
        'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just',
        # Question starters
        'does', 'did', 'what', 'when', 'where', 'who', 'why', 'how',
        # Common false positives
        'yes', 'no', 'maybe', 'also', 'however', 'although', 'though',
    }

    # Attribute indicators
    ATTRIBUTE_PATTERNS = {
        'job': ['job', 'work', 'profession', 'occupation', 'career', 'employed', 'works as'],
        'location': ['live', 'lives', 'from', 'location', 'place', 'city', 'country', 'moved', 'move'],
        'age': ['age', 'old', 'years old', 'born', 'birthday'],
        'name': ['name', 'called', 'named'],
        'relationship': ['friend', 'partner', 'spouse', 'husband', 'wife', 'sibling', 'parent', 'child'],
        'education': ['study', 'studied', 'school', 'university', 'degree', 'major', 'research'],
        'hobby': ['hobby', 'hobbies', 'like', 'likes', 'enjoy', 'enjoys', 'interest', 'interests'],
        'possession': ['have', 'has', 'own', 'owns', 'pet', 'car', 'house'],
    }

    # Temporal patterns
    TEMPORAL_PATTERNS = [
        r'\b(\d+)\s*years?\s*ago\b',
        r'\b(last|next|this)\s+(year|month|week|day)\b',
        r'\bin\s+(\d{4})\b',
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b',
        r'\b(when|time|date|day|year|month)\b',
        r'\b(before|after|during|since|until)\b',
        r'\b(recently|lately|currently|now|today|yesterday|tomorrow)\b',
    ]

    # Action verbs
    ACTION_VERBS = {
        'paint', 'painted', 'painting',
        'research', 'researched', 'researching',
        'move', 'moved', 'moving',
        'travel', 'traveled', 'travelling',
        'visit', 'visited', 'visiting',
        'buy', 'bought', 'buying',
        'sell', 'sold', 'selling',
        'meet', 'met', 'meeting',
        'start', 'started', 'starting',
        'finish', 'finished', 'finishing',
        'create', 'created', 'creating',
        'make', 'made', 'making',
        'write', 'wrote', 'writing',
        'read', 'read', 'reading',
        'cook', 'cooked', 'cooking',
        'play', 'played', 'playing',
        'watch', 'watched', 'watching',
        'listen', 'listened', 'listening',
    }

    def decompose(self, query: str) -> DecomposedQuery:
        """Decompose a query into structured components.

        Args:
            query: Natural language query

        Returns:
            Structured DecomposedQuery for routing
        """
        query_lower = query.lower()

        # Extract entities (capitalized words)
        entities = self._extract_entities(query)

        # Extract attributes being queried
        attributes = self._extract_attributes(query_lower)

        # Extract temporal markers
        temporal_markers = self._extract_temporal(query_lower)

        # Extract actions
        actions = self._extract_actions(query_lower)

        # Extract key terms
        key_terms = self._extract_key_terms(query, entities)

        # Determine query type
        query_type = self._classify_query(
            query_lower, entities, attributes, temporal_markers, actions
        )

        # Calculate confidence
        confidence = self._calculate_confidence(
            entities, attributes, temporal_markers, actions, query_type
        )

        decomposed = DecomposedQuery(
            original=query,
            query_type=query_type,
            entities=entities,
            relations=[],  # TODO: Extract relations
            attributes=attributes,
            actions=actions,
            temporal_markers=temporal_markers,
            key_terms=key_terms,
            confidence=confidence,
        )

        logger.debug(
            f"Decomposed query: type={query_type.name}, "
            f"entities={entities}, attributes={attributes}, "
            f"temporal={temporal_markers}, actions={actions}"
        )

        return decomposed

    def _extract_entities(self, query: str) -> list[str]:
        """Extract named entities from query."""
        # Find capitalized words (likely proper nouns)
        words = re.findall(r'\b[A-Z][a-z]+\b', query)

        # Filter out stop words and common words
        entities = [
            w for w in words
            if w.lower() not in self.STOP_WORDS and len(w) > 1
        ]

        # Remove duplicates while preserving order
        seen = set()
        unique_entities = []
        for e in entities:
            if e not in seen:
                seen.add(e)
                unique_entities.append(e)

        return unique_entities

    def _extract_attributes(self, query_lower: str) -> list[str]:
        """Extract attributes being queried."""
        found = []

        for attr_type, patterns in self.ATTRIBUTE_PATTERNS.items():
            for pattern in patterns:
                if pattern in query_lower:
                    if attr_type not in found:
                        found.append(attr_type)
                    break

        return found

    def _extract_temporal(self, query_lower: str) -> list[str]:
        """Extract temporal markers from query."""
        found = []

        for pattern in self.TEMPORAL_PATTERNS:
            matches = re.findall(pattern, query_lower, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    found.append(' '.join(match))
                else:
                    found.append(match)

        return found

    def _extract_actions(self, query_lower: str) -> list[str]:
        """Extract action verbs from query."""
        found = []

        words = re.findall(r'\b\w+\b', query_lower)
        for word in words:
            if word in self.ACTION_VERBS:
                # Get base form
                base = word
                for verb_set in [
                    ('paint', 'painted', 'painting'),
                    ('research', 'researched', 'researching'),
                    ('move', 'moved', 'moving'),
                ]:
                    if word in verb_set:
                        base = verb_set[0]
                        break
                if base not in found:
                    found.append(base)

        return found

    def _extract_key_terms(self, query: str, entities: list[str]) -> list[str]:
        """Extract important non-entity keywords."""
        words = re.findall(r'\b\w+\b', query.lower())

        # Filter to meaningful terms
        key_terms = [
            w for w in words
            if (
                w not in self.STOP_WORDS and
                len(w) > 2 and
                w not in [e.lower() for e in entities]
            )
        ]

        return list(set(key_terms))

    def _classify_query(
        self,
        query_lower: str,
        entities: list[str],
        attributes: list[str],
        temporal_markers: list[str],
        actions: list[str]
    ) -> QueryType:
        """Classify query type for routing."""

        has_entities = len(entities) > 0
        has_attributes = len(attributes) > 0
        has_temporal = len(temporal_markers) > 0
        has_actions = len(actions) > 0

        # Check for possessive patterns (X's Y)
        possessive_match = re.search(r"[A-Z][a-z]+'s\s+\w+", query_lower, re.IGNORECASE)

        # Temporal query: "When did X..."
        if query_lower.startswith('when') and has_entities:
            return QueryType.ENTITY_TEMPORAL

        # Entity attribute: "What is X's job?"
        if has_entities and has_attributes:
            return QueryType.ENTITY_ATTRIBUTE

        # Entity action: "What did X do?"
        if has_entities and has_actions:
            return QueryType.ENTITY_ACTION

        # Entity temporal: "When did X..."
        if has_entities and has_temporal:
            return QueryType.ENTITY_TEMPORAL

        # Pure temporal: "What happened in 2022?"
        if has_temporal and not has_entities:
            return QueryType.TEMPORAL_EVENT

        # Entity relation: Possessive without specific attribute
        if possessive_match and has_entities:
            return QueryType.ENTITY_RELATION

        # Multi-hop detection (future)
        # if multiple relations detected...

        # Default to general
        return QueryType.GENERAL

    def _calculate_confidence(
        self,
        entities: list[str],
        attributes: list[str],
        temporal_markers: list[str],
        actions: list[str],
        query_type: QueryType
    ) -> float:
        """Calculate confidence in decomposition."""
        confidence = 0.5

        # Boost for clear signals
        if entities:
            confidence += 0.1 * min(len(entities), 3)
        if attributes:
            confidence += 0.15
        if temporal_markers:
            confidence += 0.1
        if actions:
            confidence += 0.1

        # Boost for non-general type
        if query_type != QueryType.GENERAL:
            confidence += 0.1

        return min(1.0, confidence)


class GraphFirstRetriever:
    """Multi-Signal Fusion Retrieval for 92%+ accuracy.

    LESSON LEARNED: Pure entity-index retrieval fails because it finds
    nodes mentioning the entity but NOT containing the answer.

    Example: "When did Caroline go to LGBTQ support group?"
    - Entity-only finds ALL Caroline mentions (hundreds)
    - But misses nodes about "LGBTQ support group" if Caroline not mentioned

    NEW APPROACH: Score ALL nodes with multi-signal fusion:
    1. Entity match signal (entity mentioned in content)
    2. Keyword match signal (key terms from query)
    3. Embedding similarity signal (semantic similarity)
    4. Temporal signal (for temporal queries)

    No filtering - pure scoring. Let the signals compete.
    """

    def __init__(
        self,
        storage: NeuralGraphStorage,
        decomposer: QueryDecomposer | None = None
    ):
        self._storage = storage
        self._decomposer = decomposer or QueryDecomposer()

    async def retrieve(
        self,
        query: str,
        session_key: str,
        limit: int = 20,
        query_embedding: list[float] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Multi-signal fusion retrieval.

        Args:
            query: Natural language query
            session_key: Session identifier
            limit: Maximum results
            query_embedding: Embedding for semantic similarity

        Returns:
            List of (node, score) tuples
        """
        # Step 1: Decompose query
        decomposed = self._decomposer.decompose(query)

        # Step 2: Get ALL nodes (no filtering!)
        all_nodes = await self._storage.get_nodes_by_session(session_key)

        if not all_nodes:
            return []

        # Step 3: Score ALL nodes with multi-signal fusion
        scored = self._score_all_nodes(all_nodes, decomposed, query_embedding)

        # Step 4: Return top-k
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    async def _retrieve_entity_attribute(
        self,
        decomposed: DecomposedQuery,
        session_key: str
    ) -> list[NeuralNode]:
        """Retrieve for entity-attribute queries.

        Strategy:
        1. Find all nodes mentioning the entity
        2. Filter to those mentioning the attribute
        3. Prioritize nodes with high entity + relevant wave amplitude
        """
        candidates = []

        # Get all nodes for entities
        for entity in decomposed.entities:
            entity_nodes = await self._storage.find_nodes_by_entities([entity], session_key)
            candidates.extend(entity_nodes)

        # Also search by entity name in content (case-insensitive)
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        for node in all_nodes:
            content_lower = node.content.lower()
            for entity in decomposed.entities:
                if entity.lower() in content_lower and node not in candidates:
                    candidates.append(node)

        # Filter by attribute keywords
        if decomposed.attributes:
            filtered = []
            for node in candidates:
                content_lower = node.content.lower()
                for attr in decomposed.attributes:
                    attr_keywords = self._decomposer.ATTRIBUTE_PATTERNS.get(attr, [attr])
                    if any(kw in content_lower for kw in attr_keywords):
                        filtered.append(node)
                        break

            # If filter is too strict, fall back to all candidates
            if len(filtered) >= 3:
                candidates = filtered

        return candidates

    async def _retrieve_entity_action(
        self,
        decomposed: DecomposedQuery,
        session_key: str
    ) -> list[NeuralNode]:
        """Retrieve for entity-action queries."""
        candidates = []

        # Get all nodes for entities
        for entity in decomposed.entities:
            entity_nodes = await self._storage.find_nodes_by_entities([entity], session_key)
            candidates.extend(entity_nodes)

        # Also search content
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        for node in all_nodes:
            content_lower = node.content.lower()
            for entity in decomposed.entities:
                if entity.lower() in content_lower and node not in candidates:
                    candidates.append(node)

        # Filter by action keywords
        if decomposed.actions:
            filtered = []
            for node in candidates:
                content_lower = node.content.lower()
                for action in decomposed.actions:
                    if action in content_lower:
                        filtered.append(node)
                        break

            if len(filtered) >= 3:
                candidates = filtered

        return candidates

    async def _retrieve_entity_temporal(
        self,
        decomposed: DecomposedQuery,
        session_key: str
    ) -> list[NeuralNode]:
        """Retrieve for entity-temporal queries."""
        candidates = []

        # Get all nodes for entities
        for entity in decomposed.entities:
            entity_nodes = await self._storage.find_nodes_by_entities([entity], session_key)
            candidates.extend(entity_nodes)

        # Also search content
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        for node in all_nodes:
            content_lower = node.content.lower()
            for entity in decomposed.entities:
                if entity.lower() in content_lower and node not in candidates:
                    candidates.append(node)

        # Prioritize nodes with temporal wave amplitude
        temporal_filtered = []
        for node in candidates:
            if node.wave_amplitudes and node.wave_amplitudes.get('temporal', 0) > 0.2:
                temporal_filtered.append(node)

        # If we have temporal nodes, prioritize them
        if len(temporal_filtered) >= 3:
            # But also keep non-temporal as fallback
            for node in candidates:
                if node not in temporal_filtered:
                    temporal_filtered.append(node)
            candidates = temporal_filtered

        return candidates

    async def _retrieve_temporal_event(
        self,
        decomposed: DecomposedQuery,
        session_key: str
    ) -> list[NeuralNode]:
        """Retrieve for pure temporal queries."""
        all_nodes = await self._storage.get_nodes_by_session(session_key)

        # Filter to nodes with temporal content
        candidates = []
        for node in all_nodes:
            has_temporal_wave = (
                node.wave_amplitudes and
                node.wave_amplitudes.get('temporal', 0) > 0.2
            )

            # Also check for temporal keywords in content
            content_lower = node.content.lower()
            has_temporal_content = any(
                marker in content_lower
                for marker in decomposed.temporal_markers
            )

            if has_temporal_wave or has_temporal_content:
                candidates.append(node)

        return candidates

    async def _retrieve_general(
        self,
        decomposed: DecomposedQuery,
        session_key: str
    ) -> list[NeuralNode]:
        """General retrieval fallback."""
        candidates = []

        # Get entity matches
        for entity in decomposed.entities:
            entity_nodes = await self._storage.find_nodes_by_entities([entity], session_key)
            candidates.extend(entity_nodes)

        # Get keyword matches from all nodes
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        for node in all_nodes:
            content_lower = node.content.lower()

            # Check for entity names
            for entity in decomposed.entities:
                if entity.lower() in content_lower and node not in candidates:
                    candidates.append(node)
                    break

            # Check for key terms
            key_term_matches = sum(
                1 for term in decomposed.key_terms
                if term in content_lower
            )
            if key_term_matches >= 2 and node not in candidates:
                candidates.append(node)

        # If no candidates, return all nodes (fallback)
        if not candidates:
            candidates = all_nodes

        return candidates

    def _score_all_nodes(
        self,
        all_nodes: list[NeuralNode],
        decomposed: DecomposedQuery,
        query_embedding: list[float] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Score ALL nodes with multi-signal fusion.

        Signal weights optimized for factual QA:
        - Entity match: 25% (including SPEAKER as entity - 4D binding!)
        - Keyword match: 35% (CRITICAL - contains the actual topic)
        - Embedding similarity: 30% (semantic relevance)
        - Temporal bonus: 10% (for temporal queries)

        THE 4D INSIGHT: When Caroline says "I went to support group",
        the word "Caroline" is NOT in the content. But the SPEAKER
        metadata IS "Caroline". We must check BOTH content AND metadata.
        """
        from .data_types import cosine_similarity

        scored = []
        query_lower = decomposed.original.lower()

        # Pre-extract all meaningful words from query for matching
        query_words = set(query_lower.split())
        meaningful_query_words = {
            w for w in query_words
            if len(w) > 3 and w not in self._decomposer.STOP_WORDS
        }

        for node in all_nodes:
            score = 0.0
            content_lower = node.content.lower()

            # Get speaker from metadata (4D BINDING - WHO said it)
            speaker = ""
            if node.metadata:
                speaker = node.metadata.get("producer_id", node.metadata.get("speaker", "")).lower()

            # SIGNAL 1: Entity match (25%)
            # Check BOTH content AND speaker (4D brain binding)
            entity_score = 0.0
            if decomposed.entities:
                entity_matches = 0
                for entity in decomposed.entities:
                    entity_lower = entity.lower()
                    # Check content
                    if entity_lower in content_lower:
                        entity_matches += 1
                    # Check speaker (4D BINDING - critical insight!)
                    elif entity_lower == speaker:
                        entity_matches += 1
                entity_score = entity_matches / len(decomposed.entities)
            score += 0.25 * entity_score

            # SIGNAL 2: Keyword/Key-term match (35%) - MOST IMPORTANT
            # This finds memories about "LGBTQ support group" even without entity
            keyword_score = 0.0
            if decomposed.key_terms:
                term_matches = sum(
                    1 for term in decomposed.key_terms
                    if term in content_lower
                )
                keyword_score = term_matches / len(decomposed.key_terms)

            # Also check meaningful words from original query
            if meaningful_query_words:
                word_matches = sum(
                    1 for word in meaningful_query_words
                    if word in content_lower
                )
                word_score = word_matches / len(meaningful_query_words)
                # Take the better of the two
                keyword_score = max(keyword_score, word_score)

            score += 0.35 * keyword_score

            # SIGNAL 3: Embedding similarity (30%)
            embedding_score = 0.0
            if query_embedding and node.embedding:
                sim = cosine_similarity(query_embedding, node.embedding)
                embedding_score = max(0, sim)
            score += 0.30 * embedding_score

            # SIGNAL 4: Temporal bonus (10%) - for temporal queries
            temporal_score = 0.0
            if decomposed.temporal_markers:
                # Check for temporal wave amplitude
                if node.wave_amplitudes and node.wave_amplitudes.get('temporal', 0) > 0.2:
                    temporal_score = 0.5

                # Check for temporal markers in content
                for marker in decomposed.temporal_markers:
                    if marker in content_lower:
                        temporal_score = max(temporal_score, 0.7)
                        break

                # Check for common temporal words
                temporal_words = ['yesterday', 'today', 'last', 'ago', 'when', 'year', 'month', 'week']
                if any(tw in content_lower for tw in temporal_words):
                    temporal_score = max(temporal_score, 0.3)

            score += 0.10 * temporal_score

            # BONUS: 4D BINDING BOOST - when speaker matches entity AND keywords match
            # This is the brain's holographic memory: Caroline + LGBTQ support group = perfect match
            speaker_matches_entity = any(
                entity.lower() == speaker for entity in decomposed.entities
            ) if decomposed.entities and speaker else False

            if speaker_matches_entity and keyword_score > 0.3:
                # MASSIVE boost - this is the 4D transcendent binding!
                score += 0.25  # This memory is ABOUT this person doing this thing
            elif entity_score > 0.5 and keyword_score > 0.3:
                score += 0.15  # Standard entity+keyword match

            scored.append((node, score))

        return scored

    def _score_candidates(
        self,
        candidates: list[NeuralNode],
        decomposed: DecomposedQuery,
        query_embedding: list[float] | None = None
    ) -> list[tuple[NeuralNode, float]]:
        """Legacy method - redirects to _score_all_nodes."""
        return self._score_all_nodes(candidates, decomposed, query_embedding)
