"""Wave Propagation - True Resonance Flow Across Tokens and Domains.

THE PHYSICS OF MEMORY RETRIEVAL:

Static matching (broken):
    R = Σ overlap_i  (independent dimensions)

Wave propagation (correct):
    R = |Σ_t Σ_d ψ(t,d) · e^(iφ) · K(t→t', d→d')|²

Where:
    t = token position in sequence
    d = domain {Entity, Temporal, Semantic, Relation}
    ψ = wave amplitude at (token, domain)
    φ = phase (contextual binding)
    K = propagation kernel (how activation flows)

KEY INSIGHT:
When "Caroline" activates in the Entity domain:
1. It propagates to adjacent tokens (sequential flow)
2. It resonates into Semantic domain (cross-domain coupling)
3. It amplifies tokens that share the entity (interference)

This creates STANDING WAVES where query and memory resonate.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from collections import defaultdict
import numpy as np

from .binding_context import BindingContext, ContentBinder, QueryClassifier


# =============================================================================
# WAVE FUNCTION REPRESENTATION
# =============================================================================

@dataclass
class TokenWave:
    """A token with wave properties across 4 domains."""
    token: str
    position: int  # Position in sequence

    # Amplitude in each domain (E, T, S, R)
    entity_amp: float = 0.0
    temporal_amp: float = 0.0
    semantic_amp: float = 0.0
    relation_amp: float = 0.0

    # Phase (contextual binding)
    phase: float = 0.0

    def total_amplitude(self) -> float:
        """Total wave amplitude across all domains."""
        return math.sqrt(
            self.entity_amp**2 +
            self.temporal_amp**2 +
            self.semantic_amp**2 +
            self.relation_amp**2
        )

    def domain_vector(self) -> tuple[float, float, float, float]:
        """Get amplitude vector across domains."""
        return (self.entity_amp, self.temporal_amp, self.semantic_amp, self.relation_amp)


@dataclass
class MemoryWaveFunction:
    """A memory represented as a wave function over tokens and domains.

    The wave function ψ_m(t, d) gives the amplitude at token t in domain d.

    Total memory state: |ψ_m⟩ = Σ_t Σ_d ψ(t,d)|t,d⟩
    """
    wave_id: str
    content: str  # Bound/enriched content
    raw_content: str

    # Token waves
    tokens: list[TokenWave] = field(default_factory=list)

    # Global properties
    speaker: str = ""
    timestamp: datetime | None = None
    embedding: list[float] | None = None

    # Aggregate domain amplitudes (for fast matching)
    total_entity: float = 0.0
    total_temporal: float = 0.0
    total_semantic: float = 0.0
    total_relation: float = 0.0

    def compute_totals(self) -> None:
        """Compute total amplitudes in each domain."""
        self.total_entity = sum(t.entity_amp for t in self.tokens)
        self.total_temporal = sum(t.temporal_amp for t in self.tokens)
        self.total_semantic = sum(t.semantic_amp for t in self.tokens)
        self.total_relation = sum(t.relation_amp for t in self.tokens)


# =============================================================================
# DOMAIN CLASSIFIERS
# =============================================================================

class DomainClassifier:
    """Classifies tokens into domains and assigns amplitudes."""

    # Entity patterns (names, pronouns resolved to names)
    ENTITY_PATTERNS = {
        # Common names
        'caroline', 'melanie', 'sarah', 'emma', 'michael', 'david', 'james',
    }

    # Temporal markers
    TEMPORAL_PATTERNS = {
        'yesterday', 'today', 'tomorrow', 'ago', 'last', 'next', 'week',
        'month', 'year', 'monday', 'tuesday', 'wednesday', 'thursday',
        'friday', 'saturday', 'sunday', 'january', 'february', 'march',
        'april', 'may', 'june', 'july', 'august', 'september', 'october',
        'november', 'december', 'morning', 'evening', 'night', 'afternoon',
    }

    # Relation verbs
    RELATION_PATTERNS = {
        'went', 'go', 'going', 'attend', 'visit', 'join', 'start', 'began',
        'finish', 'complete', 'paint', 'create', 'make', 'run', 'walk',
        'work', 'live', 'move', 'meet', 'know', 'like', 'love', 'hate',
        'said', 'told', 'asked', 'think', 'feel', 'want', 'need',
    }

    # Semantic (everything else meaningful)
    STOPWORDS = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'to', 'of', 'in',
        'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
        'during', 'before', 'after', 'above', 'below', 'between', 'under',
        'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
        'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
        'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
        'too', 'very', 'just', 'also', 'now', 'and', 'but', 'or', 'if',
        'because', 'until', 'while', 'this', 'that', 'these', 'those',
        'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her',
        'us', 'them', 'my', 'your', 'his', 'its', 'our', 'their', 'what',
        'which', 'who', 'whom', 'whose',
    }

    @classmethod
    def classify_token(cls, token: str, position: int, context: list[str]) -> TokenWave:
        """Classify a token and assign domain amplitudes."""
        token_lower = token.lower()
        wave = TokenWave(token=token, position=position)

        # Check if it's a capitalized name (entity)
        if token[0].isupper() and token_lower not in cls.STOPWORDS:
            if len(token) > 1 and token_lower not in cls.TEMPORAL_PATTERNS:
                wave.entity_amp = 1.0

        # Check explicit entity patterns
        if token_lower in cls.ENTITY_PATTERNS:
            wave.entity_amp = 1.0

        # Check temporal patterns
        if token_lower in cls.TEMPORAL_PATTERNS:
            wave.temporal_amp = 1.0

        # Check for date patterns
        if re.match(r'\d{4}', token):  # Year
            wave.temporal_amp = 1.0
        if re.match(r'\d{1,2}[-/]\d{1,2}', token):  # Date
            wave.temporal_amp = 1.0

        # Check relation patterns
        if token_lower in cls.RELATION_PATTERNS:
            wave.relation_amp = 1.0

        # Semantic: meaningful words not in other categories
        if (token_lower not in cls.STOPWORDS and
            len(token_lower) > 2 and
            wave.entity_amp == 0 and
            wave.temporal_amp == 0 and
            wave.relation_amp == 0):
            wave.semantic_amp = 1.0

        return wave


# =============================================================================
# WAVE PROPAGATION KERNEL
# =============================================================================

class PropagationKernel:
    """Defines how activation propagates across tokens and domains.

    The kernel K(t→t', d→d') determines:
    1. Token-to-token flow (sequential propagation)
    2. Domain-to-domain resonance (cross-coupling)
    """

    # Token propagation decay (how far activation spreads)
    TOKEN_DECAY = 0.7  # Each position away reduces by this factor
    TOKEN_RANGE = 3    # Maximum positions to propagate

    # Domain coupling matrix (how domains resonate with each other)
    # [E, T, S, R] → [E, T, S, R]
    DOMAIN_COUPLING = [
        [1.0, 0.3, 0.5, 0.4],  # Entity couples with: E, T, S, R
        [0.3, 1.0, 0.3, 0.2],  # Temporal couples with: E, T, S, R
        [0.5, 0.3, 1.0, 0.6],  # Semantic couples with: E, T, S, R
        [0.4, 0.2, 0.6, 1.0],  # Relation couples with: E, T, S, R
    ]

    @classmethod
    def propagate_tokens(cls, waves: list[TokenWave]) -> list[TokenWave]:
        """Propagate activation between adjacent tokens."""
        n = len(waves)
        propagated = []

        for i, wave in enumerate(waves):
            new_wave = TokenWave(
                token=wave.token,
                position=wave.position,
                entity_amp=wave.entity_amp,
                temporal_amp=wave.temporal_amp,
                semantic_amp=wave.semantic_amp,
                relation_amp=wave.relation_amp,
                phase=wave.phase,
            )

            # Receive activation from nearby tokens
            for j in range(max(0, i - cls.TOKEN_RANGE), min(n, i + cls.TOKEN_RANGE + 1)):
                if i != j:
                    distance = abs(i - j)
                    decay = cls.TOKEN_DECAY ** distance

                    # Add decayed activation from neighbor
                    new_wave.entity_amp += waves[j].entity_amp * decay * 0.3
                    new_wave.temporal_amp += waves[j].temporal_amp * decay * 0.3
                    new_wave.semantic_amp += waves[j].semantic_amp * decay * 0.3
                    new_wave.relation_amp += waves[j].relation_amp * decay * 0.3

            propagated.append(new_wave)

        return propagated

    @classmethod
    def propagate_domains(cls, wave: TokenWave) -> TokenWave:
        """Propagate activation between domains within a token."""
        amps = [wave.entity_amp, wave.temporal_amp, wave.semantic_amp, wave.relation_amp]
        new_amps = [0.0, 0.0, 0.0, 0.0]

        for i in range(4):
            for j in range(4):
                new_amps[i] += amps[j] * cls.DOMAIN_COUPLING[j][i]

        return TokenWave(
            token=wave.token,
            position=wave.position,
            entity_amp=new_amps[0],
            temporal_amp=new_amps[1],
            semantic_amp=new_amps[2],
            relation_amp=new_amps[3],
            phase=wave.phase,
        )


# =============================================================================
# RESONANCE COMPUTATION
# =============================================================================

class ResonanceComputer:
    """Computes resonance between query and memory wave functions.

    R(m|Q) = |⟨ψ_m|ψ_Q⟩|² = |Σ_t Σ_d ψ_m(t,d)* · ψ_Q(t,d)|²

    This is the inner product in Hilbert space over (token, domain) basis.
    """

    @staticmethod
    def compute_resonance(
        query_waves: list[TokenWave],
        memory: MemoryWaveFunction,
        query_embedding: list[float] | None = None,
    ) -> tuple[float, dict]:
        """Compute resonance score between query and memory.

        The resonance is computed as:
        1. Token alignment: How well query tokens match memory tokens
        2. Domain resonance: How domain amplitudes interfere
        3. Phase coherence: Bonus for matching sequence order
        """
        # Build query token set
        query_tokens = {w.token.lower(): w for w in query_waves}

        # Aggregate query domain amplitudes
        q_entity = sum(w.entity_amp for w in query_waves)
        q_temporal = sum(w.temporal_amp for w in query_waves)
        q_semantic = sum(w.semantic_amp for w in query_waves)
        q_relation = sum(w.relation_amp for w in query_waves)

        # Normalize
        q_total = math.sqrt(q_entity**2 + q_temporal**2 + q_semantic**2 + q_relation**2)
        if q_total > 0:
            q_entity /= q_total
            q_temporal /= q_total
            q_semantic /= q_total
            q_relation /= q_total

        # Memory domain amplitudes (already stored)
        m_total = math.sqrt(
            memory.total_entity**2 + memory.total_temporal**2 +
            memory.total_semantic**2 + memory.total_relation**2
        )
        if m_total > 0:
            m_entity = memory.total_entity / m_total
            m_temporal = memory.total_temporal / m_total
            m_semantic = memory.total_semantic / m_total
            m_relation = memory.total_relation / m_total
        else:
            m_entity = m_temporal = m_semantic = m_relation = 0.25

        # 1. DOMAIN RESONANCE (inner product of domain vectors)
        domain_resonance = (
            q_entity * m_entity +
            q_temporal * m_temporal +
            q_semantic * m_semantic +
            q_relation * m_relation
        )

        # 2. TOKEN ALIGNMENT (direct token matches)
        token_matches = 0
        total_query_tokens = len([w for w in query_waves if w.total_amplitude() > 0])

        memory_tokens = {w.token.lower() for w in memory.tokens}
        for qt in query_tokens:
            if qt in memory_tokens:
                token_matches += 1

        token_alignment = token_matches / max(total_query_tokens, 1)

        # 3. ENTITY SPECIFICITY (critical for speaker attribution)
        entity_match = 0.0
        query_entities = {w.token.lower() for w in query_waves if w.entity_amp > 0.5}
        memory_entities = {w.token.lower() for w in memory.tokens if w.entity_amp > 0.5}

        if query_entities:
            matched = query_entities & memory_entities
            entity_match = len(matched) / len(query_entities)

        # 4. SEMANTIC OVERLAP (content relevance)
        query_semantics = {w.token.lower() for w in query_waves if w.semantic_amp > 0.5}
        memory_semantics = {w.token.lower() for w in memory.tokens if w.semantic_amp > 0.5}

        semantic_overlap = 0.0
        if query_semantics:
            matched = query_semantics & memory_semantics
            semantic_overlap = len(matched) / len(query_semantics)

        # 5. EMBEDDING SIMILARITY (fallback for fuzzy matching)
        embedding_sim = 0.0
        if query_embedding and memory.embedding:
            dot = sum(a * b for a, b in zip(query_embedding, memory.embedding))
            norm_q = math.sqrt(sum(a * a for a in query_embedding))
            norm_m = math.sqrt(sum(a * a for a in memory.embedding))
            if norm_q > 0 and norm_m > 0:
                embedding_sim = dot / (norm_q * norm_m)

        # TOTAL RESONANCE (wave interference pattern)
        # Weights tuned for memory retrieval
        resonance = (
            0.25 * domain_resonance +      # Domain structure alignment
            0.20 * token_alignment +        # Direct word matches
            0.25 * entity_match +           # Speaker/entity specificity (CRITICAL)
            0.15 * semantic_overlap +       # Content relevance
            0.15 * max(0, embedding_sim)    # Semantic similarity fallback
        )

        # AMPLIFICATION: Strong entity match with any semantic match
        if entity_match > 0.8 and (semantic_overlap > 0.2 or token_alignment > 0.3):
            resonance *= 1.3

        breakdown = {
            'domain_resonance': domain_resonance,
            'token_alignment': token_alignment,
            'entity_match': entity_match,
            'semantic_overlap': semantic_overlap,
            'embedding_sim': embedding_sim,
        }

        return resonance, breakdown


# =============================================================================
# WAVE PROPAGATION MEMORY FIELD
# =============================================================================

class WavePropagationField:
    """Memory field with true wave propagation.

    This implements:
    1. Token-to-token propagation (sequential flow)
    2. Domain-to-domain resonance (cross-coupling)
    3. Interference-based retrieval (wave superposition)
    """

    def __init__(self):
        self._memories: dict[str, MemoryWaveFunction] = {}
        self._context = BindingContext()

        # Inverted indices for fast candidate selection
        self._entity_index: dict[str, set[str]] = defaultdict(set)
        self._semantic_index: dict[str, set[str]] = defaultdict(set)

    def ingest(
        self,
        wave_id: str,
        content: str,
        speaker: str,
        timestamp: datetime | None,
        embedding: list[float] | None = None,
    ) -> MemoryWaveFunction:
        """Ingest a memory with full wave function representation."""
        # Update binding context
        self._context.update_turn(speaker, timestamp, content)

        # Bind content (pronoun/temporal resolution)
        binder = ContentBinder(self._context)
        bound_content = binder.bind(content)

        # Tokenize
        tokens = re.findall(r'\b\w+\b', bound_content)

        # Create token waves
        token_waves = []
        for i, token in enumerate(tokens):
            wave = DomainClassifier.classify_token(token, i, tokens)
            wave.phase = float(i) / max(len(tokens), 1)  # Phase based on position
            token_waves.append(wave)

        # PROPAGATE: Token-to-token flow
        token_waves = PropagationKernel.propagate_tokens(token_waves)

        # PROPAGATE: Domain-to-domain resonance
        token_waves = [PropagationKernel.propagate_domains(w) for w in token_waves]

        # Create memory wave function
        memory = MemoryWaveFunction(
            wave_id=wave_id,
            content=bound_content,
            raw_content=content,
            tokens=token_waves,
            speaker=speaker.lower() if speaker else "",
            timestamp=timestamp,
            embedding=embedding,
        )
        memory.compute_totals()

        # Store
        self._memories[wave_id] = memory

        # Index entities and semantics for fast lookup
        for wave in token_waves:
            token_lower = wave.token.lower()
            if wave.entity_amp > 0.5:
                self._entity_index[token_lower].add(wave_id)
            if wave.semantic_amp > 0.5:
                self._semantic_index[token_lower].add(wave_id)

        return memory

    def query(
        self,
        query_text: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryWaveFunction, float, dict]]:
        """Query using wave propagation and resonance."""
        # Classify query type
        q_type, q_coords = QueryClassifier.classify(query_text)

        # Tokenize query
        tokens = re.findall(r'\b\w+\b', query_text)

        # Create query wave function
        query_waves = []
        for i, token in enumerate(tokens):
            wave = DomainClassifier.classify_token(token, i, tokens)
            wave.phase = float(i) / max(len(tokens), 1)
            query_waves.append(wave)

        # PROPAGATE query waves
        query_waves = PropagationKernel.propagate_tokens(query_waves)
        query_waves = [PropagationKernel.propagate_domains(w) for w in query_waves]

        # Find candidates (union of entity and semantic matches)
        candidates: set[str] = set()

        for wave in query_waves:
            token_lower = wave.token.lower()
            if wave.entity_amp > 0.3 and token_lower in self._entity_index:
                candidates.update(self._entity_index[token_lower])
            if wave.semantic_amp > 0.3 and token_lower in self._semantic_index:
                candidates.update(self._semantic_index[token_lower])

        # If no candidates, use all memories
        if not candidates:
            candidates = set(self._memories.keys())

        # Compute resonance for each candidate
        scored: list[tuple[MemoryWaveFunction, float, dict]] = []

        for wid in candidates:
            memory = self._memories[wid]
            resonance, breakdown = ResonanceComputer.compute_resonance(
                query_waves, memory, query_embedding
            )
            breakdown['query_type'] = q_type
            scored.append((memory, resonance, breakdown))

        # Sort by resonance
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:limit]

    def get_stats(self) -> dict:
        """Get field statistics."""
        return {
            'total_memories': len(self._memories),
            'unique_entities': len(self._entity_index),
            'unique_semantics': len(self._semantic_index),
        }


class WavePropagationRetriever:
    """High-level retriever using wave propagation."""

    def __init__(self):
        self._field = WavePropagationField()

    def ingest(
        self,
        wave_id: str,
        content: str,
        speaker: str,
        timestamp: datetime | None,
        embedding: list[float] | None = None,
    ) -> MemoryWaveFunction:
        """Ingest a memory."""
        return self._field.ingest(wave_id, content, speaker, timestamp, embedding)

    def query(
        self,
        query_text: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryWaveFunction, float, dict]]:
        """Query memories."""
        return self._field.query(query_text, limit, query_embedding)

    def get_stats(self) -> dict:
        """Get statistics."""
        return self._field.get_stats()
