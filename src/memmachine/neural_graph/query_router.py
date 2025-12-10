"""Query Router - Intelligent Query Analysis and Pre-Filtering

THE FUNDAMENTAL INSIGHT:
Different question types need different retrieval strategies.

PROBLEM WITH CURRENT APPROACH:
    Query: "What is Caroline's job?"
    Message: "I work as a teacher" (speaker=Caroline)

    Embedding similarity: LOW (job ≠ teacher semantically)
    Result: MISS

SOLUTION:
    1. Detect query is asking about CAROLINE
    2. Filter to messages WHERE speaker=Caroline OR mentions Caroline
    3. Search within filtered set
    4. "I work as a teacher" now has a chance

QUERY TYPES AND STRATEGIES:

1. ENTITY-FOCUSED (single-hop)
   Pattern: "What is X's Y?" / "Where does X live?" / "What did X say about Y?"
   Strategy: Filter by entity X, then semantic search

2. TEMPORAL-FOCUSED
   Pattern: "When did X happen?" / "What happened on DATE?" / "After X, what?"
   Strategy: Filter by temporal markers, traverse temporal chain

3. MULTI-HOP
   Pattern: "What did the person who X say about Y?"
   Strategy: Resolve "the person who X" first, then filter

4. ADVERSARIAL
   Pattern: Negations, hypotheticals, "Did X NOT happen?"
   Strategy: Special negation handling, verify against all candidates
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage


class QueryType(Enum):
    """Classification of query intent."""
    ENTITY_ATTRIBUTE = "entity_attribute"      # "What is X's job?"
    ENTITY_ACTION = "entity_action"            # "What did X do?"
    ENTITY_RELATION = "entity_relation"        # "Who is X's friend?"
    TEMPORAL_WHEN = "temporal_when"            # "When did X happen?"
    TEMPORAL_SEQUENCE = "temporal_sequence"    # "What happened after X?"
    TEMPORAL_DURATION = "temporal_duration"    # "How long did X last?"
    MULTI_HOP = "multi_hop"                    # "What did the person who X..."
    ADVERSARIAL = "adversarial"                # Negations, hypotheticals
    OPEN_DOMAIN = "open_domain"                # General questions


@dataclass
class QueryAnalysis:
    """Result of analyzing a query."""
    query_text: str
    query_type: QueryType

    # Extracted entities (people, places, things)
    entities: list[str] = field(default_factory=list)

    # Primary subject (who/what the question is ABOUT)
    primary_subject: str | None = None

    # Speaker filter (if asking about what someone SAID)
    speaker_filter: str | None = None

    # Temporal markers
    temporal_markers: list[str] = field(default_factory=list)
    temporal_direction: str | None = None  # "before", "after", "during"

    # Negation detection
    has_negation: bool = False

    # Confidence in classification
    confidence: float = 0.5

    # Recommended retrieval strategy
    strategy: str = "semantic"  # "semantic", "entity_filter", "temporal_filter", "hybrid"

    # Filter parameters
    filter_params: dict[str, Any] = field(default_factory=dict)


class QueryRouter:
    """Analyzes queries and routes to appropriate retrieval strategy.

    THE KEY INSIGHT:
    Instead of treating all queries the same (embed → search all),
    we FIRST understand what the query is asking, THEN filter the
    search space to relevant messages.

    This is how humans think:
    - "What is Caroline's job?" → Think about what CAROLINE said
    - "When did they go to Paris?" → Think about PARIS + TIME
    - "What happened after the party?" → Think in SEQUENCE
    """

    # Patterns for entity-focused queries
    ENTITY_PATTERNS = [
        # Possessive patterns: "X's Y"
        (r"(?:what|where|who|how)\s+(?:is|are|was|were)\s+(\w+)'s\s+(\w+)", "entity_attribute"),
        # Direct questions about entity
        (r"(?:what|where|who)\s+(?:did|does|do|is|was)\s+(\w+)\s+", "entity_action"),
        # Questions about relationships
        (r"(?:who)\s+(?:is|are|was|were)\s+(\w+)'s\s+(\w+)", "entity_relation"),
    ]

    # Patterns for temporal queries
    TEMPORAL_PATTERNS = [
        (r"when\s+did\s+(\w+)", "temporal_when"),
        (r"what\s+(?:happened|occurred)\s+(?:after|before|during)\s+", "temporal_sequence"),
        (r"how\s+long\s+(?:did|does|was|were)", "temporal_duration"),
        (r"(?:yesterday|today|tomorrow|last\s+\w+|next\s+\w+)", "temporal_marker"),
    ]

    # Negation words
    NEGATION_WORDS = {"not", "never", "no", "none", "neither", "nor", "didn't", "doesn't",
                     "don't", "wasn't", "weren't", "isn't", "aren't", "hasn't", "haven't",
                     "hadn't", "won't", "wouldn't", "couldn't", "shouldn't", "can't"}

    # Common person name patterns (will be augmented with actual names from context)
    PERSON_INDICATORS = {"who", "person", "someone", "somebody", "they", "he", "she"}

    def __init__(self, known_entities: set[str] | None = None):
        """Initialize router.

        Args:
            known_entities: Set of known entity names from the conversation
        """
        self.known_entities = known_entities or set()

    def update_known_entities(self, entities: set[str]) -> None:
        """Update known entities from conversation context."""
        self.known_entities.update(entities)

    def analyze(self, query: str) -> QueryAnalysis:
        """Analyze a query and determine retrieval strategy.

        Args:
            query: The question to analyze

        Returns:
            QueryAnalysis with extracted info and recommended strategy
        """
        query_lower = query.lower().strip()

        analysis = QueryAnalysis(
            query_text=query,
            query_type=QueryType.OPEN_DOMAIN,
            confidence=0.5,
            strategy="semantic"
        )

        # 1. Check for negations (adversarial indicator)
        analysis.has_negation = any(neg in query_lower.split() for neg in self.NEGATION_WORDS)
        if analysis.has_negation:
            analysis.query_type = QueryType.ADVERSARIAL
            analysis.confidence = 0.7

        # 2. Extract entities (capitalized words, known names)
        entities = self._extract_entities(query)
        analysis.entities = entities

        # 3. Detect temporal markers
        temporal_markers = self._extract_temporal_markers(query_lower)
        analysis.temporal_markers = temporal_markers

        # 4. Classify query type and extract primary subject
        query_type, subject, confidence = self._classify_query(query, query_lower, entities, temporal_markers)

        if confidence > analysis.confidence:
            analysis.query_type = query_type
            analysis.confidence = confidence

        analysis.primary_subject = subject

        # 5. Determine if we need speaker filter
        if subject and self._is_asking_about_speech(query_lower):
            analysis.speaker_filter = subject

        # 6. Set retrieval strategy based on analysis
        analysis.strategy, analysis.filter_params = self._determine_strategy(analysis)

        return analysis

    def _extract_entities(self, query: str) -> list[str]:
        """Extract entity names from query."""
        entities = []

        # Find capitalized words (potential names)
        words = query.split()
        for i, word in enumerate(words):
            # Skip first word (sentence start) unless it's a known entity
            clean_word = re.sub(r'[^\w]', '', word)
            if clean_word and clean_word[0].isupper():
                if i > 0 or clean_word.lower() in {e.lower() for e in self.known_entities}:
                    entities.append(clean_word)

        # Also check for known entities in any case
        query_lower = query.lower()
        for entity in self.known_entities:
            if entity.lower() in query_lower and entity not in entities:
                entities.append(entity)

        return entities

    def _extract_temporal_markers(self, query_lower: str) -> list[str]:
        """Extract temporal markers from query."""
        markers = []

        temporal_words = [
            "yesterday", "today", "tomorrow", "morning", "afternoon", "evening", "night",
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "january", "february", "march", "april", "may", "june", "july", "august",
            "september", "october", "november", "december",
            "last week", "next week", "last month", "next month", "last year", "next year",
            "ago", "later", "before", "after", "during", "while", "when"
        ]

        for word in temporal_words:
            if word in query_lower:
                markers.append(word)

        # Also look for date patterns
        date_pattern = r'\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?'
        dates = re.findall(date_pattern, query_lower)
        markers.extend(dates)

        return markers

    def _classify_query(
        self,
        query: str,
        query_lower: str,
        entities: list[str],
        temporal_markers: list[str]
    ) -> tuple[QueryType, str | None, float]:
        """Classify query type and extract primary subject."""

        # Check entity patterns
        for pattern, q_type in self.ENTITY_PATTERNS:
            match = re.search(pattern, query_lower)
            if match:
                subject = match.group(1) if match.groups() else None
                # Capitalize subject if it matches a known entity
                if subject:
                    for entity in entities:
                        if entity.lower() == subject.lower():
                            subject = entity
                            break
                return QueryType[q_type.upper()], subject, 0.8

        # Check temporal patterns
        for pattern, q_type in self.TEMPORAL_PATTERNS:
            if re.search(pattern, query_lower):
                subject = entities[0] if entities else None
                return QueryType.TEMPORAL_WHEN if "when" in q_type else QueryType.TEMPORAL_SEQUENCE, subject, 0.75

        # If has temporal markers and entities, likely temporal query
        if temporal_markers and entities:
            return QueryType.TEMPORAL_WHEN, entities[0], 0.6

        # If has entity and asking direct question, entity-focused
        if entities and any(w in query_lower for w in ["what", "where", "who", "how"]):
            return QueryType.ENTITY_ACTION, entities[0], 0.6

        # Multi-hop detection: "the person who", "the one that", indirect references
        multi_hop_patterns = [
            r"the\s+(?:person|one|man|woman)\s+who",
            r"(?:whose|which)\s+\w+\s+(?:is|was|did)",
            r"the\s+\w+\s+that\s+\w+\s+mentioned"
        ]
        for pattern in multi_hop_patterns:
            if re.search(pattern, query_lower):
                return QueryType.MULTI_HOP, entities[0] if entities else None, 0.7

        return QueryType.OPEN_DOMAIN, entities[0] if entities else None, 0.5

    def _is_asking_about_speech(self, query_lower: str) -> bool:
        """Check if query is asking about what someone SAID."""
        speech_indicators = [
            "say", "said", "tell", "told", "mention", "mentioned",
            "talk", "talked", "speak", "spoke", "discuss", "discussed",
            "explain", "explained", "describe", "described"
        ]
        return any(ind in query_lower for ind in speech_indicators)

    def _determine_strategy(self, analysis: QueryAnalysis) -> tuple[str, dict]:
        """Determine retrieval strategy based on analysis."""
        params = {}

        # ENTITY-FOCUSED: Filter by entity first
        if analysis.query_type in {QueryType.ENTITY_ATTRIBUTE, QueryType.ENTITY_ACTION, QueryType.ENTITY_RELATION}:
            if analysis.primary_subject:
                params["entity_filter"] = analysis.primary_subject
                params["include_speaker_messages"] = True
                if analysis.speaker_filter:
                    params["speaker_filter"] = analysis.speaker_filter
                return "entity_filter", params

        # TEMPORAL: Use temporal chain traversal
        if analysis.query_type in {QueryType.TEMPORAL_WHEN, QueryType.TEMPORAL_SEQUENCE, QueryType.TEMPORAL_DURATION}:
            params["temporal_markers"] = analysis.temporal_markers
            if analysis.primary_subject:
                params["entity_filter"] = analysis.primary_subject
            return "temporal_filter", params

        # MULTI-HOP: Need two-stage retrieval
        if analysis.query_type == QueryType.MULTI_HOP:
            params["multi_hop"] = True
            params["entities"] = analysis.entities
            return "multi_hop", params

        # ADVERSARIAL: Need verification pass
        if analysis.query_type == QueryType.ADVERSARIAL:
            params["verify_negation"] = analysis.has_negation
            params["entities"] = analysis.entities
            return "adversarial", params

        # Default: Standard semantic search
        return "semantic", params


class FilteredRetriever:
    """Retrieves messages using query-aware filtering.

    THE CORE IDEA:
    Instead of searching ALL messages, we first FILTER to a relevant subset
    based on the query analysis, then search within that subset.

    This dramatically improves accuracy for entity-focused queries because:
    - "What is Caroline's job?" + filter(speaker=Caroline)
    - Now "I work as a teacher" is in the candidate set!
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self.storage = storage
        self.router = QueryRouter()

    def update_context(self, nodes: list["NeuralNode"]) -> None:
        """Update router with known entities from conversation."""
        entities = set()
        for node in nodes:
            # Extract speaker
            if node.metadata:
                speaker = node.metadata.get("speaker")
                if speaker:
                    entities.add(speaker)

            # Extract mentioned entities
            if node.entity_ids:
                entities.update(node.entity_ids)

        self.router.update_known_entities(entities)

    async def get_filtered_candidates(
        self,
        query: str,
        session_key: str,
        base_candidates: list["NeuralNode"] | None = None
    ) -> tuple[list["NeuralNode"], QueryAnalysis]:
        """Get filtered candidate nodes based on query analysis.

        Args:
            query: The question
            session_key: Session identifier
            base_candidates: Optional pre-retrieved candidates to filter

        Returns:
            Tuple of (filtered_nodes, analysis)
        """
        analysis = self.router.analyze(query)

        # Get all nodes if no base candidates
        if base_candidates is None:
            base_candidates = await self._get_session_nodes(session_key)

        # Apply filters based on strategy
        if analysis.strategy == "entity_filter":
            filtered = self._apply_entity_filter(base_candidates, analysis.filter_params)
        elif analysis.strategy == "temporal_filter":
            filtered = self._apply_temporal_filter(base_candidates, analysis.filter_params)
        elif analysis.strategy == "multi_hop":
            # For multi-hop, we return broader set but with analysis for downstream
            filtered = self._apply_entity_filter(base_candidates, analysis.filter_params)
        else:
            # Semantic or adversarial: return all candidates
            filtered = base_candidates

        return filtered, analysis

    async def _get_session_nodes(self, session_key: str) -> list["NeuralNode"]:
        """Get all nodes for a session."""
        from .data_types import NodeLayer
        # Get message-layer nodes
        return await self.storage.get_nodes_by_layer(session_key, NodeLayer.MESSAGE)

    def _apply_entity_filter(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> list["NeuralNode"]:
        """Filter candidates by entity."""
        entity = params.get("entity_filter", "").lower()
        include_speaker = params.get("include_speaker_messages", True)
        speaker_filter = params.get("speaker_filter", "").lower()

        if not entity:
            return candidates

        filtered = []
        for node in candidates:
            # Check if entity is speaker
            if include_speaker and node.metadata:
                speaker = (node.metadata.get("speaker") or "").lower()
                if entity in speaker or (speaker_filter and speaker_filter in speaker):
                    filtered.append(node)
                    continue

            # Check if entity is mentioned in content
            content_lower = (node.content or "").lower()
            if entity in content_lower:
                filtered.append(node)
                continue

            # Check entity_ids
            if node.entity_ids:
                for eid in node.entity_ids:
                    if entity in eid.lower():
                        filtered.append(node)
                        break

        # If filter is too aggressive (< 10% of candidates), loosen it
        if len(filtered) < len(candidates) * 0.1 and len(filtered) < 20:
            # Also include nodes that are temporally adjacent to filtered nodes
            filtered_ids = {n.node_id for n in filtered}
            for node in candidates:
                if node.node_id in filtered_ids:
                    continue
                # Check temporal edges (would need edge info here)
                # For now, just ensure minimum candidate set
                if len(filtered) < 20:
                    filtered.append(node)

        return filtered if filtered else candidates

    def _apply_temporal_filter(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> list["NeuralNode"]:
        """Filter candidates by temporal markers."""
        temporal_markers = params.get("temporal_markers", [])
        entity = params.get("entity_filter", "").lower()

        if not temporal_markers and not entity:
            return candidates

        filtered = []
        for node in candidates:
            content_lower = (node.content or "").lower()

            # Check temporal markers in content
            has_temporal = any(marker in content_lower for marker in temporal_markers)

            # Check entity if provided
            has_entity = True
            if entity:
                has_entity = (
                    entity in content_lower or
                    (node.metadata and entity in (node.metadata.get("speaker") or "").lower()) or
                    (node.entity_ids and any(entity in eid.lower() for eid in node.entity_ids))
                )

            if has_temporal or has_entity:
                filtered.append(node)

        return filtered if filtered else candidates


# Convenience function
def create_query_router(known_entities: set[str] | None = None) -> QueryRouter:
    """Create a query router with optional known entities."""
    return QueryRouter(known_entities)
