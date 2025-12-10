"""Field-Based Retrieval: The Brain's True Mechanism.

THE FUNDAMENTAL INSIGHT:
========================
The brain doesn't traverse edges - it activates FIELDS.

When you think "Caroline + Paris + When":
- "Caroline" wave radiates to ALL neurons encoding Caroline
- "Paris" wave radiates to ALL neurons encoding Paris
- "When" wave radiates to ALL neurons with temporal markers
- The ANSWER emerges where ALL waves constructively interfere

This is fundamentally different from graph traversal:
- Graph: "Start here, follow edges, find path"
- Field: "ALL nodes receive signal, answer emerges from resonance"

THE PHYSICS:
============
In electromagnetic fields:
    φ(r) = Σ (q_i / |r - r_i|²)

Every charge affects every point, weighted by distance.
We apply this principle to memory retrieval.

THE BINDING PROBLEM:
====================
Multi-hop questions fail because:
- "When did Caroline visit Paris?" needs Caroline + Paris + When
- Current system finds "Caroline" nodes, follows 8 edges, misses "Paris"
- Field approach: ALL nodes get charge, intersection EMERGES

COMPLEXITY:
===========
O(n) per query where n = number of nodes.
For 400 nodes, this is ~400 similarity computations.
Still faster than LLM inference (~200ms vs ~2000ms).
Can optimize with LSH for larger graphs if needed.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


# =============================================================================
# FIELD CONSTANTS: Based on Neuroscience
# =============================================================================

# Dimension base frequencies (Tesla's insight)
DIMENSION_FREQUENCIES = {
    "temporal": 7.83,     # Schumann resonance
    "entity": 14.1,       # Alpha wave - recognition
    "relational": 21.0,   # Beta wave - relationships
    "action": 30.0,       # Low gamma - action binding
    "state": 40.0,        # Gamma - state awareness
    "spatial": 10.0,      # Alpha - spatial mapping
    "causal": 25.0,       # Beta - causal reasoning
    "emotional": 6.0,     # Theta - emotional memory
    "quantitative": 12.0, # Alpha - numerical
}

# Resonance amplification (when waves align perfectly)
RESONANCE_MAX = 3.0
RESONANCE_MIN = 0.3

# Entity binding boost (when entities match)
ENTITY_BINDING_WEIGHT = 0.5

# Speaker binding boost (when speaker IS the queried entity)
SPEAKER_BINDING_WEIGHT = 0.8

# Semantic similarity floor (below this, node is too different)
SEMANTIC_FLOOR = 0.15


@dataclass
class FieldConfig:
    """Configuration for field-based retrieval."""

    # Whether to use wave amplitude resonance
    use_wave_resonance: bool = True
    wave_resonance_weight: float = 0.35

    # Whether to use entity binding
    use_entity_binding: bool = True
    entity_binding_weight: float = 0.45

    # Whether to use speaker binding (speaker IS entity)
    use_speaker_binding: bool = True
    speaker_binding_weight: float = 0.55

    # Semantic similarity weight
    semantic_weight: float = 0.40

    # Keyword matching weight
    keyword_weight: float = 0.30

    # Minimum field charge to include in results
    min_charge: float = 0.20

    # Maximum results to return
    max_results: int = 20


@dataclass
class FieldActivation:
    """Result of field activation for a single node."""
    node: "NeuralNode"
    total_charge: float
    semantic_charge: float
    wave_charge: float
    entity_charge: float
    speaker_charge: float
    keyword_charge: float


class FieldRetriever:
    """Field-based retrieval using continuous activation.

    Instead of graph traversal, every node receives charge from the query.
    Charge is computed based on:
    1. Semantic similarity (embedding cosine)
    2. Wave resonance (dimension alignment)
    3. Entity binding (shared entities)
    4. Speaker binding (speaker IS queried entity)
    5. Keyword matching (exact term overlap)

    The answer EMERGES from constructive interference of all dimensions.
    """

    def __init__(
        self,
        storage: "NeuralGraphStorage",
        config: FieldConfig | None = None
    ):
        self._storage = storage
        self._config = config or FieldConfig()

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        query_wave_amplitudes: dict[str, float] | None = None,
        limit: int | None = None
    ) -> list[tuple["NeuralNode", float]]:
        """Execute field-based retrieval.

        Every node in the session receives charge based on query resonance.
        Top nodes by total charge are returned.

        Args:
            query_text: The query text
            query_embedding: Query embedding vector
            session_key: Session identifier
            query_wave_amplitudes: Query's wave pattern (dimension amplitudes)
            limit: Maximum results (overrides config)

        Returns:
            List of (node, charge) tuples sorted by charge descending
        """
        final_limit = limit or self._config.max_results

        # Extract query features
        query_entities = self._extract_entities(query_text)
        query_keywords = self._extract_keywords(query_text)
        query_embedding_np = np.array(query_embedding, dtype=np.float32)

        # Normalize query embedding
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        # Get ALL nodes in session
        all_nodes = await self._storage.get_nodes_by_session(session_key)

        if not all_nodes:
            return []

        # Compute field activation for EVERY node
        activations: list[FieldActivation] = []

        for node in all_nodes:
            activation = self._compute_field_activation(
                node=node,
                query_embedding=query_embedding_np,
                query_entities=query_entities,
                query_keywords=query_keywords,
                query_waves=query_wave_amplitudes or {}
            )

            if activation.total_charge >= self._config.min_charge:
                activations.append(activation)

        # Sort by total charge descending
        activations.sort(key=lambda a: a.total_charge, reverse=True)

        # Return top results
        results = [(a.node, a.total_charge) for a in activations[:final_limit]]

        logger.info(
            f"Field retrieval: {len(all_nodes)} nodes -> {len(activations)} activated -> {len(results)} returned"
        )

        return results

    def _compute_field_activation(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_entities: set[str],
        query_keywords: set[str],
        query_waves: dict[str, float]
    ) -> FieldActivation:
        """Compute field activation for a single node.

        This is the core FIELD EQUATION:

        charge = (semantic * weight_s)
               + (wave_resonance * weight_w)
               + (entity_binding * weight_e)
               + (speaker_binding * weight_sp)
               + (keyword_match * weight_k)

        Where each component measures a different dimension of similarity.
        """
        # 1. SEMANTIC CHARGE: Embedding cosine similarity
        semantic_charge = 0.0
        if node.embedding:
            node_embedding = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_embedding)
            if node_norm > 1e-10:
                node_embedding = node_embedding / node_norm
                semantic_charge = float(np.dot(query_embedding, node_embedding))
                # Floor at minimum
                if semantic_charge < SEMANTIC_FLOOR:
                    semantic_charge = 0.0

        # 2. WAVE RESONANCE CHARGE: Dimension alignment
        wave_charge = 0.0
        if self._config.use_wave_resonance and query_waves and node.wave_amplitudes:
            wave_charge = self._compute_wave_resonance(query_waves, node.wave_amplitudes)

        # 3. ENTITY BINDING CHARGE: Shared entities
        entity_charge = 0.0
        if self._config.use_entity_binding and query_entities:
            entity_charge = self._compute_entity_binding(query_entities, node)

        # 4. SPEAKER BINDING CHARGE: Speaker IS queried entity
        speaker_charge = 0.0
        if self._config.use_speaker_binding and query_entities:
            speaker_charge = self._compute_speaker_binding(query_entities, node)

        # 5. KEYWORD CHARGE: Exact term matching
        keyword_charge = 0.0
        if query_keywords:
            keyword_charge = self._compute_keyword_match(query_keywords, node.content)

        # TOTAL CHARGE: Weighted sum
        total_charge = (
            semantic_charge * self._config.semantic_weight +
            wave_charge * self._config.wave_resonance_weight +
            entity_charge * self._config.entity_binding_weight +
            speaker_charge * self._config.speaker_binding_weight +
            keyword_charge * self._config.keyword_weight
        )

        # Bonus for multiple dimensions aligning (constructive interference)
        # If 3+ dimensions have strong signal, amplify
        strong_signals = sum([
            1 if semantic_charge > 0.5 else 0,
            1 if wave_charge > 0.5 else 0,
            1 if entity_charge > 0.3 else 0,
            1 if speaker_charge > 0.3 else 0,
            1 if keyword_charge > 0.3 else 0,
        ])
        if strong_signals >= 3:
            # Constructive interference bonus
            total_charge *= (1.0 + 0.15 * (strong_signals - 2))

        return FieldActivation(
            node=node,
            total_charge=total_charge,
            semantic_charge=semantic_charge,
            wave_charge=wave_charge,
            entity_charge=entity_charge,
            speaker_charge=speaker_charge,
            keyword_charge=keyword_charge
        )

    def _compute_wave_resonance(
        self,
        query_waves: dict[str, float],
        node_waves: dict[str, float]
    ) -> float:
        """Compute wave resonance between query and node.

        Uses cosine similarity in wave space.
        When waves align, energy transfer is maximized (Tesla's insight).
        """
        all_dims = set(query_waves.keys()) | set(node_waves.keys())
        if not all_dims:
            return 0.0

        # Compute dot product
        dot_product = sum(
            query_waves.get(dim, 0.0) * node_waves.get(dim, 0.0)
            for dim in all_dims
        )

        # Compute magnitudes
        query_mag = math.sqrt(sum(v ** 2 for v in query_waves.values()))
        node_mag = math.sqrt(sum(v ** 2 for v in node_waves.values()))

        if query_mag < 0.001 or node_mag < 0.001:
            return 0.0

        # Cosine similarity
        cosine_sim = dot_product / (query_mag * node_mag)

        # Tesla's resonance curve: exponential amplification near match
        if cosine_sim >= 0:
            resonance = 1.0 + (RESONANCE_MAX - 1.0) * (cosine_sim ** 2)
        else:
            resonance = RESONANCE_MIN

        # Normalize to [0, 1]
        return min(1.0, (resonance - RESONANCE_MIN) / (RESONANCE_MAX - RESONANCE_MIN))

    def _compute_entity_binding(
        self,
        query_entities: set[str],
        node: "NeuralNode"
    ) -> float:
        """Compute entity binding strength.

        Higher charge when node contains entities mentioned in query.
        """
        if not node.entity_ids:
            # Check content for entity mentions
            content_lower = node.content.lower()
            matches = sum(1 for e in query_entities if e.lower() in content_lower)
            if matches > 0:
                return min(1.0, matches / len(query_entities))
            return 0.0

        # Compare entity sets (case-insensitive)
        query_lower = {e.lower() for e in query_entities}
        node_lower = {e.lower() for e in node.entity_ids}

        overlap = len(query_lower & node_lower)
        if overlap > 0:
            return min(1.0, overlap / len(query_entities))

        # Also check content mentions
        content_lower = node.content.lower()
        content_matches = sum(1 for e in query_entities if e.lower() in content_lower)
        if content_matches > 0:
            return min(1.0, content_matches / len(query_entities)) * 0.8

        return 0.0

    def _compute_speaker_binding(
        self,
        query_entities: set[str],
        node: "NeuralNode"
    ) -> float:
        """Compute speaker binding strength.

        If the speaker of the message IS one of the queried entities,
        this is a strong signal. "What did Caroline say?" should find
        messages WHERE Caroline is the speaker.
        """
        # Get speaker from metadata
        speaker = ""
        if node.metadata:
            speaker = node.metadata.get("producer_id", "") or node.metadata.get("speaker", "")

        if not speaker:
            return 0.0

        speaker_lower = speaker.lower().strip()
        query_lower = {e.lower() for e in query_entities}

        if speaker_lower in query_lower:
            return 1.0  # Full match

        # Partial match (speaker name contains entity)
        for entity in query_lower:
            if entity in speaker_lower or speaker_lower in entity:
                return 0.7

        return 0.0

    def _compute_keyword_match(
        self,
        query_keywords: set[str],
        content: str
    ) -> float:
        """Compute keyword matching score.

        Exact term overlap - critical for factual QA.
        """
        if not query_keywords:
            return 0.0

        content_lower = content.lower()
        matches = sum(1 for kw in query_keywords if kw in content_lower)

        return min(1.0, matches / len(query_keywords))

    def _extract_entities(self, text: str) -> set[str]:
        """Extract entity names from text.

        Finds capitalized words likely to be proper nouns.
        """
        words = re.findall(r'\b[A-Z][a-z]+\b', text)
        return set(words)

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract important keywords from text.

        Filters stop words and keeps meaningful terms.
        """
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

        tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        keywords = {t for t in tokens if t not in stop_words and len(t) > 2}

        return keywords


# =============================================================================
# HYBRID FIELD RETRIEVER: Field + Graph for best of both
# =============================================================================

class HybridFieldRetriever:
    """Combines field-based retrieval with targeted graph expansion.

    Phase 1: Field activation finds top candidates (O(n))
    Phase 2: Graph expansion from top candidates (targeted)

    This gives us:
    - Global coverage from field (won't miss distant matches)
    - Local precision from graph (follows strong relationships)
    """

    def __init__(
        self,
        storage: "NeuralGraphStorage",
        field_config: FieldConfig | None = None
    ):
        self._storage = storage
        self._field_retriever = FieldRetriever(storage, field_config)
        self._config = field_config or FieldConfig()

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        query_wave_amplitudes: dict[str, float] | None = None,
        limit: int | None = None
    ) -> list[tuple["NeuralNode", float]]:
        """Execute hybrid field + graph retrieval.

        1. Field activation finds globally relevant nodes
        2. Speaker-based expansion (entities mentioned in query)
        3. Graph expansion from top field results adds connected context
        4. Re-rank combined results
        """
        final_limit = limit or self._config.max_results

        # Phase 1: Field activation
        field_results = await self._field_retriever.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            session_key=session_key,
            query_wave_amplitudes=query_wave_amplitudes,
            limit=final_limit * 3  # Get more for expansion
        )

        if not field_results:
            return []

        # Phase 2: Speaker-based expansion (THE FIX for single-hop!)
        # If query mentions a person, include MORE messages from that speaker
        candidates: dict[str, tuple["NeuralNode", float]] = {}

        # Add field results
        for node, charge in field_results:
            candidates[node.node_id] = (node, charge)

        # Extract entities from query (simple capitalized word extraction)
        query_entities = self._extract_query_entities(query_text)

        if query_entities:
            # Get ALL nodes and find ones from queried speakers
            all_nodes = await self._storage.get_all_nodes(session_key)

            # CRITICAL FIX: Ensure we get diverse speaker content
            # Don't just add highest-sim messages - add RANDOM sample of speaker messages
            # This helps surface unexpected but relevant info (like "Sweden" in a necklace msg)
            speaker_messages: list[tuple["NeuralNode", float]] = []

            for node in all_nodes:
                speaker = ""
                if node.metadata:
                    speaker = (node.metadata.get("producer_id", "") or
                              node.metadata.get("speaker", "")).lower()

                # Check if speaker matches any queried entity
                speaker_match = any(e.lower() in speaker or speaker in e.lower()
                                   for e in query_entities if e.lower())

                if speaker_match:
                    # Compute semantic similarity for ranking
                    sim = 0.3  # Default
                    if node.embedding:
                        import numpy as np
                        node_emb = np.array(node.embedding, dtype=np.float32)
                        query_emb = np.array(query_embedding, dtype=np.float32)
                        node_norm = np.linalg.norm(node_emb)
                        query_norm = np.linalg.norm(query_emb)
                        if node_norm > 1e-10 and query_norm > 1e-10:
                            sim = float(np.dot(node_emb / node_norm, query_emb / query_norm))

                    speaker_messages.append((node, sim))

            # Sort by similarity and take top 50 speaker messages
            # This ensures we get the speaker's most relevant content
            speaker_messages.sort(key=lambda x: x[1], reverse=True)

            # Add top 50 speaker messages to candidates
            for node, sim in speaker_messages[:50]:
                if node.node_id not in candidates:
                    # Speaker match bonus (0.5) ensures these get included
                    charge = max(0.5, sim) * 0.8
                    candidates[node.node_id] = (node, charge)
                else:
                    # Boost existing candidate if it's a speaker match
                    existing_node, existing_charge = candidates[node.node_id]
                    boost = 0.15  # Speaker match boost
                    candidates[node.node_id] = (existing_node, existing_charge + boost)

        # Phase 3: Graph expansion from top field results via temporal edges
        from .data_types import EdgeType

        top_seeds = field_results[:10]  # Top 10 as expansion seeds

        for seed_node, seed_charge in top_seeds:
            # Get temporal neighbors (before/after)
            temporal_edges_from = await self._storage.get_edges_from(
                seed_node.node_id, edge_types=[EdgeType.TEMPORAL]
            )
            temporal_edges_to = await self._storage.get_edges_to(
                seed_node.node_id, edge_types=[EdgeType.TEMPORAL]
            )

            for edge in (temporal_edges_from[:3] + temporal_edges_to[:3]):
                target_id = edge.target_id if edge.source_id == seed_node.node_id else edge.source_id

                if target_id not in candidates:
                    target = await self._storage.get_node(target_id)
                    if target:
                        # Context boost: temporal neighbors of high-charge nodes
                        context_charge = seed_charge * 0.7 * edge.effective_weight
                        candidates[target_id] = (target, context_charge)
                else:
                    # Boost existing candidate
                    existing_node, existing_charge = candidates[target_id]
                    boost = seed_charge * 0.3 * edge.effective_weight
                    candidates[target_id] = (existing_node, existing_charge + boost)

        # Sort and return
        results = sorted(
            candidates.values(),
            key=lambda x: x[1],
            reverse=True
        )[:final_limit]

        logger.info(
            f"Hybrid retrieval: {len(field_results)} field + {len(candidates)} candidates -> {len(results)} final"
        )

        return results

    def _extract_query_entities(self, query_text: str) -> set[str]:
        """Extract potential entity names from query (capitalized words)."""
        import re
        # Find capitalized words (likely names)
        entities = re.findall(r'\b[A-Z][a-z]+\b', query_text)
        # Filter common words
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                  'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The', 'And'}
        return {e for e in entities if e not in common}


# Export
__all__ = [
    "FieldConfig",
    "FieldActivation",
    "FieldRetriever",
    "HybridFieldRetriever",
]
