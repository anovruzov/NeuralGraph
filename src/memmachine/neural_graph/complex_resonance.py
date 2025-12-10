# -*- coding: utf-8 -*-
"""Complex-Valued Wave Resonance Memory - Based on "Wave-Based Semantic Memory" Paper.

EXACT IMPLEMENTATION of the paper's formula:

Memory as Complex Waveform:
    psi(x) = A(x) * e^(i*phi(x))

Sign-Phase Mapping (converts real embeddings to complex):
    A(x) = |v(x)|
    phi(x) = 0 if v(x) >= 0, else pi

Resonance Score (the KEY formula):
    S(psi1, psi2) = (1/2) * (Sum|psi1 + psi2|^2) / (Sum(|psi1|^2 + |psi2|^2)) * R

    Where R = 2*sqrt(E1*E2) / (E1 + E2)  (scale-alignment factor)
    E1 = Sum|psi1|^2, E2 = Sum|psi2|^2

This measures CONSTRUCTIVE INTERFERENCE between query and memory waveforms.
When phases align, waves add constructively -> high resonance.
When phases oppose, waves cancel -> low resonance.

Target: 92%+ accuracy on LoCoMo benchmark.
"""

from __future__ import annotations

import math
import re
import cmath
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional
from collections import defaultdict

from .binding_context import BindingContext, ContentBinder, QueryClassifier


# =============================================================================
# COMPLEX WAVEFORM REPRESENTATION
# =============================================================================

@dataclass
class ComplexWaveform:
    """A memory/query represented as a complex-valued waveform.

    psi(x) = A(x) * e^(i*phi(x))

    Where x indexes over the embedding dimensions.
    """
    wave_id: str
    amplitudes: np.ndarray  # A(x) = |v(x)|
    phases: np.ndarray       # phi(x) = 0 if v(x) >= 0, else pi

    # Metadata
    content: str = ""
    bound_content: str = ""
    speaker: str = ""
    timestamp: Optional[datetime] = None

    # Extracted features for fast matching
    entities: set[str] = field(default_factory=set)
    temporals: set[str] = field(default_factory=set)
    semantics: set[str] = field(default_factory=set)

    @property
    def psi(self) -> np.ndarray:
        """The full complex waveform: psi = A * e^(i*phi)."""
        return self.amplitudes * np.exp(1j * self.phases)

    @property
    def energy(self) -> float:
        """Total energy: E = Sum|psi|^2 = Sum A^2."""
        return float(np.sum(self.amplitudes ** 2))

    @classmethod
    def from_embedding(cls, wave_id: str, embedding: list[float], **kwargs) -> "ComplexWaveform":
        """Convert real-valued embedding to complex waveform using sign-phase mapping.

        Sign-Phase Mapping (from paper):
            A(x) = |v(x)|
            phi(x) = 0 if v(x) >= 0, else pi
        """
        v = np.array(embedding, dtype=np.float64)

        # Sign-phase mapping
        amplitudes = np.abs(v)
        phases = np.where(v >= 0, 0.0, np.pi)

        return cls(
            wave_id=wave_id,
            amplitudes=amplitudes,
            phases=phases,
            **kwargs
        )


# =============================================================================
# RESONANCE SCORE - THE CORE FORMULA
# =============================================================================

class ResonanceScore:
    """Computes the exact resonance score from the paper.

    S(psi1, psi2) = (1/2) * (Sum|psi1 + psi2|^2) / (Sum(|psi1|^2 + |psi2|^2)) * R

    Where R = 2*sqrt(E1*E2) / (E1 + E2) is the scale-alignment factor.

    This measures constructive interference between waveforms.
    """

    @staticmethod
    def compute(psi1: np.ndarray, psi2: np.ndarray) -> float:
        """Compute resonance score between two complex waveforms.

        Args:
            psi1: First complex waveform (memory)
            psi2: Second complex waveform (query)

        Returns:
            Resonance score in [0, 1]
        """
        # Energies
        E1 = float(np.sum(np.abs(psi1) ** 2))
        E2 = float(np.sum(np.abs(psi2) ** 2))

        if E1 == 0 or E2 == 0:
            return 0.0

        # Interference term: |psi1 + psi2|^2
        interference = np.sum(np.abs(psi1 + psi2) ** 2)

        # Normalization: |psi1|^2 + |psi2|^2
        normalization = E1 + E2

        # Scale-alignment factor: R = 2*sqrt(E1*E2) / (E1 + E2)
        R = 2 * math.sqrt(E1 * E2) / (E1 + E2)

        # Full resonance score
        # S = (1/2) * interference / normalization * R
        # Simplified: S = interference * sqrt(E1*E2) / (E1 + E2)^2
        S = 0.5 * (interference / normalization) * R

        return float(S)

    @staticmethod
    def compute_from_waveforms(w1: ComplexWaveform, w2: ComplexWaveform) -> float:
        """Compute resonance between two ComplexWaveform objects."""
        return ResonanceScore.compute(w1.psi, w2.psi)

    @staticmethod
    def compute_from_embeddings(emb1: list[float], emb2: list[float]) -> float:
        """Compute resonance directly from real-valued embeddings.

        Uses sign-phase mapping internally.
        """
        v1 = np.array(emb1, dtype=np.float64)
        v2 = np.array(emb2, dtype=np.float64)

        # Sign-phase mapping
        A1 = np.abs(v1)
        phi1 = np.where(v1 >= 0, 0.0, np.pi)
        psi1 = A1 * np.exp(1j * phi1)

        A2 = np.abs(v2)
        phi2 = np.where(v2 >= 0, 0.0, np.pi)
        psi2 = A2 * np.exp(1j * phi2)

        return ResonanceScore.compute(psi1, psi2)


# =============================================================================
# ENTITY & SEMANTIC EXTRACTION
# =============================================================================

class FeatureExtractor:
    """Extracts entities, temporals, and semantics from text."""

    TEMPORAL_PATTERNS = {
        'yesterday', 'today', 'tomorrow', 'ago', 'last', 'next', 'week',
        'month', 'year', 'monday', 'tuesday', 'wednesday', 'thursday',
        'friday', 'saturday', 'sunday', 'morning', 'evening', 'night',
        'january', 'february', 'march', 'april', 'may', 'june', 'july',
        'august', 'september', 'october', 'november', 'december', 'recently',
    }

    STOPWORDS = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'to', 'of', 'in',
        'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
        'and', 'but', 'or', 'if', 'because', 'until', 'while', 'this', 'that',
        'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her',
        'us', 'them', 'my', 'your', 'his', 'its', 'our', 'their', 'what',
        'which', 'who', 'whom', 'whose', 'where', 'when', 'why', 'how',
        'not', 'no', 'so', 'very', 'just', 'also', 'only', 'such', 'than',
        'too', 'some', 'any', 'all', 'each', 'every', 'both', 'few', 'more',
        'most', 'other', 'into', 'over', 'after', 'before', 'between', 'under',
    }

    @classmethod
    def extract(cls, text: str) -> tuple[set[str], set[str], set[str]]:
        """Extract entities, temporals, and semantics from text.

        Returns:
            (entities, temporals, semantics)
        """
        tokens = re.findall(r'\b\w+\b', text)
        entities = set()
        temporals = set()
        semantics = set()

        for i, token in enumerate(tokens):
            lower = token.lower()

            # Entity: Capitalized words (names, places)
            if (token[0].isupper() and
                len(token) > 1 and
                lower not in cls.STOPWORDS and
                lower not in cls.TEMPORAL_PATTERNS):
                entities.add(lower)

            # Temporal
            if lower in cls.TEMPORAL_PATTERNS:
                temporals.add(lower)

            # Check for year patterns
            if re.match(r'^\d{4}$', token):
                temporals.add(token)

            # Semantic: meaningful words
            if (lower not in cls.STOPWORDS and
                lower not in cls.TEMPORAL_PATTERNS and
                len(lower) > 2 and
                not token[0].isupper()):
                semantics.add(lower)

        return entities, temporals, semantics


# =============================================================================
# COMPLEX RESONANCE MEMORY
# =============================================================================

class ComplexResonanceMemory:
    """Memory system using complex-valued wave resonance.

    This implements the full pipeline:
    1. Ingest: Convert text + embedding to ComplexWaveform
    2. Index: Track entities/temporals/semantics for fast lookup
    3. Query: Find memories via resonance scoring
    """

    def __init__(self):
        self._memories: dict[str, ComplexWaveform] = {}
        self._context = BindingContext()

        # Inverted indices
        self._entity_index: dict[str, set[str]] = defaultdict(set)
        self._temporal_index: dict[str, set[str]] = defaultdict(set)
        self._semantic_index: dict[str, set[str]] = defaultdict(set)
        self._speaker_index: dict[str, set[str]] = defaultdict(set)

    def ingest(
        self,
        wave_id: str,
        content: str,
        speaker: str,
        timestamp: Optional[datetime],
        embedding: list[float],
    ) -> ComplexWaveform:
        """Ingest a memory as a complex waveform.

        Args:
            wave_id: Unique identifier
            content: Raw text content
            speaker: Who said it
            timestamp: When it was said
            embedding: Real-valued embedding from embedding model
        """
        # Update binding context
        self._context.update_turn(speaker, timestamp, content)

        # Bind content (resolve pronouns and temporals)
        binder = ContentBinder(self._context)
        bound_content = binder.bind(content)

        # Extract features from bound content
        entities, temporals, semantics = FeatureExtractor.extract(bound_content)

        # Add speaker as entity
        if speaker:
            entities.add(speaker.lower())

        # Create complex waveform
        waveform = ComplexWaveform.from_embedding(
            wave_id=wave_id,
            embedding=embedding,
            content=content,
            bound_content=bound_content,
            speaker=speaker.lower() if speaker else "",
            timestamp=timestamp,
            entities=entities,
            temporals=temporals,
            semantics=semantics,
        )

        # Store
        self._memories[wave_id] = waveform

        # Index
        for e in entities:
            self._entity_index[e].add(wave_id)
        for t in temporals:
            self._temporal_index[t].add(wave_id)
        for s in semantics:
            self._semantic_index[s].add(wave_id)
        if speaker:
            self._speaker_index[speaker.lower()].add(wave_id)

        return waveform

    def query(
        self,
        query_text: str,
        query_embedding: list[float],
        limit: int = 30,
    ) -> list[tuple[ComplexWaveform, float, dict]]:
        """Query memories using complex resonance.

        Args:
            query_text: The question
            query_embedding: Embedding of the question
            limit: Max results to return

        Returns:
            List of (waveform, score, breakdown) tuples
        """
        # Classify query type
        q_type, q_coords = QueryClassifier.classify(query_text)

        # Extract query features
        q_entities, q_temporals, q_semantics = FeatureExtractor.extract(query_text)

        # Create query waveform
        query_wave = ComplexWaveform.from_embedding(
            wave_id="query",
            embedding=query_embedding,
            content=query_text,
            entities=q_entities,
            temporals=q_temporals,
            semantics=q_semantics,
        )

        # Find candidates via indices
        candidates: set[str] = set()

        # Entity matches (HIGH priority)
        for e in q_entities:
            if e in self._entity_index:
                candidates.update(self._entity_index[e])
            if e in self._speaker_index:
                candidates.update(self._speaker_index[e])

        # Temporal matches
        for t in q_temporals:
            if t in self._temporal_index:
                candidates.update(self._temporal_index[t])

        # Semantic matches
        for s in q_semantics:
            if s in self._semantic_index:
                candidates.update(self._semantic_index[s])

        # If no candidates, search all
        if not candidates:
            candidates = set(self._memories.keys())

        # Score each candidate
        scored: list[tuple[ComplexWaveform, float, dict]] = []

        for wid in candidates:
            memory = self._memories[wid]

            # CORE: Complex resonance score (from paper)
            resonance = ResonanceScore.compute_from_waveforms(query_wave, memory)

            # Entity match bonus (critical for speaker attribution)
            entity_overlap = len(q_entities & memory.entities)
            entity_bonus = 0.3 * (entity_overlap / max(len(q_entities), 1)) if q_entities else 0

            # Temporal match bonus
            temporal_overlap = len(q_temporals & memory.temporals)
            temporal_bonus = 0.2 * (temporal_overlap / max(len(q_temporals), 1)) if q_temporals else 0

            # Semantic match bonus
            semantic_overlap = len(q_semantics & memory.semantics)
            semantic_bonus = 0.1 * (semantic_overlap / max(len(q_semantics), 1)) if q_semantics else 0

            # Combined score
            total_score = resonance + entity_bonus + temporal_bonus + semantic_bonus

            breakdown = {
                'query_type': q_type,
                'resonance': resonance,
                'entity_bonus': entity_bonus,
                'temporal_bonus': temporal_bonus,
                'semantic_bonus': semantic_bonus,
                'entity_overlap': entity_overlap,
                'temporal_overlap': temporal_overlap,
                'semantic_overlap': semantic_overlap,
            }

            scored.append((memory, total_score, breakdown))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:limit]

    def get_stats(self) -> dict:
        """Get memory statistics."""
        return {
            'total_memories': len(self._memories),
            'unique_entities': len(self._entity_index),
            'unique_temporals': len(self._temporal_index),
            'unique_semantics': len(self._semantic_index),
            'unique_speakers': len(self._speaker_index),
        }


# =============================================================================
# HIGH-LEVEL RETRIEVER
# =============================================================================

class ComplexResonanceRetriever:
    """High-level retriever using complex-valued wave resonance."""

    def __init__(self):
        self._memory = ComplexResonanceMemory()

    def ingest(
        self,
        wave_id: str,
        content: str,
        speaker: str,
        timestamp: Optional[datetime],
        embedding: list[float],
    ) -> ComplexWaveform:
        """Ingest a memory."""
        return self._memory.ingest(wave_id, content, speaker, timestamp, embedding)

    def query(
        self,
        query_text: str,
        limit: int = 30,
        query_embedding: Optional[list[float]] = None,
    ) -> list[tuple[ComplexWaveform, float, dict]]:
        """Query memories."""
        if query_embedding is None:
            # Fallback: cannot compute resonance without embedding
            return []
        return self._memory.query(query_text, query_embedding, limit)

    def get_stats(self) -> dict:
        """Get statistics."""
        return self._memory.get_stats()
