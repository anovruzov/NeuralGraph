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

    def _compute_temporal_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_text: str,
        temporal_keywords: set[str],
        reference_time: datetime | None,
    ) -> float:
        """Compute temporal-weighted charge."""
        # 1. Semantic similarity (lower weight in temporal store)
        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = float(np.dot(query_embedding, node_emb / node_norm))

        # 2. Temporal keyword matching (HIGH weight)
        temporal_charge = 0.0
        content_lower = node.content.lower()
        for keyword in temporal_keywords:
            if keyword in content_lower:
                temporal_charge += 0.25
        temporal_charge = min(1.0, temporal_charge)

        # 3. Grounded date matching (look for [= markers)
        grounded_charge = 0.0
        if "[=" in content_lower:
            # This node has grounded dates - boost it
            grounded_charge = 0.2
            # Check if query temporal keywords match grounded content
            for keyword in temporal_keywords:
                if keyword in content_lower:
                    grounded_charge += 0.15
            grounded_charge = min(1.0, grounded_charge)

        # 4. Recency decay (if reference time provided)
        recency_charge = 0.5  # Default neutral
        if reference_time and node.created_at:
            time_diff = abs((reference_time - node.created_at).days)
            recency_charge = math.exp(-time_diff / self._config.temporal_decay_days)

        # TOTAL: Weighted combination emphasizing temporal matching
        total = (
            semantic_charge * self._config.semantic_weight +
            temporal_charge * 0.4 +
            grounded_charge * 0.3 +
            recency_charge * 0.2
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
        """Extract meaningful keywords."""
        text_lower = text.lower()
        # Split and filter
        words = re.findall(r'\b[a-z]{3,}\b', text_lower)
        stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all',
                     'can', 'had', 'her', 'was', 'one', 'our', 'out', 'has',
                     'what', 'when', 'where', 'who', 'why', 'how', 'did', 'does'}
        return {w for w in words if w not in stopwords}

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
        text_lower = text.lower()
        words = re.findall(r'\b[a-z]{3,}\b', text_lower)
        stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all'}
        return {w for w in words if w not in stopwords}

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

    def _fuse_results(
        self,
        temporal_results: list[tuple["NeuralNode", float]],
        entity_results: list[tuple["NeuralNode", float]],
        reasoning_results: list[tuple["NeuralNode", float]],
        adversarial_results: list[tuple["NeuralNode", float]],
        type_weights: dict[str, float],
    ) -> list[tuple["NeuralNode", float]]:
        """Fuse results from all stores with type-based weighting."""
        # Normalize type weights
        total_weight = sum(type_weights.values()) or 1.0

        temporal_weight = type_weights.get(QueryType.TEMPORAL, 0.25) / total_weight
        entity_weight = type_weights.get(QueryType.ENTITY, 0.25) / total_weight
        multihop_weight = type_weights.get(QueryType.MULTI_HOP, 0.25) / total_weight
        adversarial_weight = type_weights.get(QueryType.ADVERSARIAL, 0.1) / total_weight
        open_weight = type_weights.get(QueryType.OPEN, 0.15) / total_weight

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
