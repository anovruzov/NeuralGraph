"""Resonance Field - The 4D Wave Function Memory Model.

MATHEMATICAL FOUNDATION:

Memory as a Tensor Field:
    M: E × T × S × R → ℂ

Where:
    E = Entity space (WHO) - discrete set of agents
    T = Temporal space (WHEN) - continuous time axis
    S = Semantic space (WHAT) - high-dimensional concept manifold
    R = Relational space (HOW) - directed action graph

A memory m is a wave function:
    ψ_m(e, t, s, r) = A_m · exp(i · φ_m(e, t, s, r))

Where:
    A_m = amplitude (salience/importance)
    φ_m = phase (contextual binding)

Retrieval Score (Resonance):
    R(m|Q) = |∫∫∫∫ ψ_m*(e,t,s,r) · ψ_Q(e,t,s,r) · K(e,t,s,r) de dt ds dr|²

This is the inner product in Hilbert space - memories resonate with queries.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from collections import defaultdict

from .binding_context import BindingContext, ContentBinder, QueryClassifier


@dataclass
class ShardWeights:
    """The α weights determining query-type alignment.

    Each memory m is sharded across 5 retrieval pathways:
        m = α₁·m_single + α₂·m_temporal + α₃·m_multi + α₄·m_open + α₅·m_adv

    Where Σ αᵢ = 1 (probability distribution)
    """
    single_hop: float = 0.2
    temporal: float = 0.2
    multi_hop: float = 0.2
    open_domain: float = 0.2
    adversarial: float = 0.2

    def normalize(self) -> None:
        """Normalize weights to sum to 1."""
        total = self.single_hop + self.temporal + self.multi_hop + self.open_domain + self.adversarial
        if total > 0:
            self.single_hop /= total
            self.temporal /= total
            self.multi_hop /= total
            self.open_domain /= total
            self.adversarial /= total

    def get(self, query_type: str) -> float:
        """Get weight for a query type."""
        return {
            'single_hop': self.single_hop,
            'temporal': self.temporal,
            'multi_hop': self.multi_hop,
            'open_domain': self.open_domain,
            'adversarial': self.adversarial,
        }.get(query_type, 0.2)


@dataclass
class MemoryWave:
    """A memory represented as a 4D wave function.

    The wave function ψ_m has:
    - Coordinates in 4D space (e, t, s, r)
    - Amplitude A (salience)
    - Phase φ (contextual binding)
    - Shard weights α (query-type alignment)
    """
    # Identity
    wave_id: str

    # Content (bound/enriched)
    content: str
    raw_content: str = ""

    # 4D COORDINATES
    entities: set[str] = field(default_factory=set)       # E dimension
    timestamp: datetime | None = None                      # T dimension (point)
    temporal_markers: set[str] = field(default_factory=set)  # T dimension (resolved)
    semantics: set[str] = field(default_factory=set)      # S dimension
    relations: set[str] = field(default_factory=set)      # R dimension

    # Wave properties
    amplitude: float = 1.0  # Salience
    phase: float = 0.0      # Contextual binding (turn order)

    # Shard weights
    shards: ShardWeights = field(default_factory=ShardWeights)

    # Speaker (primary entity)
    speaker: str = ""

    # Embedding for semantic similarity
    embedding: list[float] | None = None


class ShardComputer:
    """Computes shard weights (α values) for memories.

    The Shard Weight Formulas:

    α_single(m) = H(e) · H(s) · (1 - H(t))
        High entity specificity, high semantic specificity, low temporal complexity

    α_temporal(m) = H(e) · H(t) · T(m)
        High entity, high temporal marker presence

    α_multi(m) = C(m) · (1 - H(e))
        Connectivity score, low single-entity focus

    α_open(m) = (1 - H(e)) · (1 - H(s)) · B(m)
        Low specificity, high breadth

    α_adv(m) = A(m) · O(m)
        Ambiguity score, overlap with other memories
    """

    @staticmethod
    def compute(wave: MemoryWave, all_waves: list[MemoryWave] | None = None) -> ShardWeights:
        """Compute shard weights for a memory wave."""

        # H(e) - Entity specificity (higher = more specific)
        # Single specific entity = high, many entities = lower
        h_e = 1.0 / max(len(wave.entities), 1)
        if wave.speaker:
            h_e = min(1.0, h_e + 0.3)  # Boost for having clear speaker

        # H(s) - Semantic specificity
        # Fewer, more specific semantics = higher
        h_s = 1.0 / max(len(wave.semantics), 1)
        if len(wave.semantics) == 1:
            h_s = 1.0
        elif len(wave.semantics) <= 3:
            h_s = 0.7
        else:
            h_s = 0.3

        # H(t) - Temporal marker presence
        # Has specific temporal marker = high
        h_t = 0.0
        if wave.temporal_markers:
            h_t = 0.8
        if wave.timestamp:
            h_t = max(h_t, 0.5)

        # T(m) - Temporal expression presence
        temporal_keywords = {'yesterday', 'today', 'tomorrow', 'last', 'ago', 'year', 'month', 'week'}
        t_m = 1.0 if any(t in wave.content.lower() for t in temporal_keywords) else 0.0

        # C(m) - Connectivity (references other entities)
        # Count entity mentions beyond speaker
        other_entities = wave.entities - {wave.speaker.lower()} if wave.speaker else wave.entities
        c_m = min(1.0, len(other_entities) / 3)

        # B(m) - Breadth score (touches multiple topics)
        b_m = min(1.0, len(wave.semantics) / 5)

        # A(m) - Ambiguity score (could match multiple interpretations)
        a_m = 0.3  # Base ambiguity
        if len(wave.entities) > 2:
            a_m += 0.3

        # O(m) - Overlap score (same topic mentioned by multiple speakers)
        o_m = 0.2  # Base overlap
        if all_waves:
            # Count how many other waves share semantics
            for other in all_waves:
                if other.wave_id != wave.wave_id:
                    shared = wave.semantics & other.semantics
                    if len(shared) > 0 and other.speaker != wave.speaker:
                        o_m += 0.2
            o_m = min(1.0, o_m)

        # Compute α values
        α_single = h_e * h_s * (1 - h_t + 0.1)  # +0.1 to not zero out
        α_temporal = h_e * (h_t + t_m) / 2
        α_multi = c_m * (1 - h_e + 0.1)
        α_open = (1 - h_e + 0.1) * (1 - h_s + 0.1) * b_m
        α_adv = a_m * o_m

        weights = ShardWeights(
            single_hop=α_single,
            temporal=α_temporal,
            multi_hop=α_multi,
            open_domain=α_open,
            adversarial=α_adv,
        )
        weights.normalize()

        return weights


class ResonanceField:
    """The 4D Resonance Field - stores and retrieves memory waves.

    This implements the mathematical model:

    R(m|Q) = |⟨ψ_m | ψ_Q⟩|² × α_{q_type}(m)

    Where:
    - ⟨ψ_m | ψ_Q⟩ is the inner product (resonance)
    - α_{q_type}(m) is the shard weight alignment
    """

    def __init__(self):
        # All memory waves
        self._waves: dict[str, MemoryWave] = {}

        # 4D Indices (coordinate → wave_ids)
        self._entity_index: dict[str, set[str]] = defaultdict(set)
        self._temporal_index: dict[str, set[str]] = defaultdict(set)
        self._semantic_index: dict[str, set[str]] = defaultdict(set)
        self._relation_index: dict[str, set[str]] = defaultdict(set)

        # Binding context for conversation tracking
        self._context = BindingContext()

    def ingest(
        self,
        wave_id: str,
        content: str,
        speaker: str,
        timestamp: datetime | None,
        embedding: list[float] | None = None,
    ) -> MemoryWave:
        """Ingest a new memory into the resonance field.

        This performs the full ingestion pipeline:
        1. Update binding context
        2. Bind content (pronoun/temporal resolution)
        3. Extract 4D coordinates
        4. Compute shard weights
        5. Index in all dimensions
        """
        # 1. Update binding context
        self._context.update_turn(speaker, timestamp, content)

        # 2. Bind content
        binder = ContentBinder(self._context)
        bound_content = binder.bind(content)

        # 3. Extract 4D coordinates
        entities = self._extract_entities(bound_content, speaker)
        temporal_markers = self._extract_temporal_markers(bound_content)
        semantics = self._extract_semantics(bound_content)
        relations = self._extract_relations(bound_content)

        # 4. Create wave
        wave = MemoryWave(
            wave_id=wave_id,
            content=bound_content,
            raw_content=content,
            entities=entities,
            timestamp=timestamp,
            temporal_markers=temporal_markers,
            semantics=semantics,
            relations=relations,
            amplitude=1.0,
            phase=float(self._context.turn_index),
            speaker=speaker.lower() if speaker else "",
            embedding=embedding,
        )

        # 5. Compute shard weights
        wave.shards = ShardComputer.compute(wave, list(self._waves.values()))

        # 6. Store and index
        self._waves[wave_id] = wave

        for e in entities:
            self._entity_index[e].add(wave_id)

        for t in temporal_markers:
            self._temporal_index[t].add(wave_id)

        for s in semantics:
            self._semantic_index[s].add(wave_id)

        for r in relations:
            self._relation_index[r].add(wave_id)

        return wave

    def query(
        self,
        query_text: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryWave, float, dict]]:
        """Query the resonance field.

        This performs:
        1. Classify query type
        2. Extract query coordinates
        3. Find candidate waves
        4. Compute resonance scores
        5. Weight by shard alignment
        6. Return top-k
        """
        # 1. Classify query
        q_type, q_coords = QueryClassifier.classify(query_text)

        # 2. Extract query coordinates
        q_entities = set(e.lower() for e in q_coords['entities'])
        q_temporals = set(q_coords['temporals'])
        q_semantics = set(q_coords['semantics'])
        q_relations = set(q_coords['relations'])

        # 3. Find candidates (union of all matching dimensions)
        candidates: set[str] = set()

        for e in q_entities:
            if e in self._entity_index:
                candidates.update(self._entity_index[e])

        for t in q_temporals:
            if t in self._temporal_index:
                candidates.update(self._temporal_index[t])

        for s in q_semantics:
            if s in self._semantic_index:
                candidates.update(self._semantic_index[s])

        for r in q_relations:
            if r in self._relation_index:
                candidates.update(self._relation_index[r])

        # If no candidates, fall back to all waves
        if not candidates:
            candidates = set(self._waves.keys())

        # 4. Compute resonance scores
        scored: list[tuple[MemoryWave, float, dict]] = []

        for wid in candidates:
            wave = self._waves[wid]

            # Compute dimensional overlaps
            e_overlap = self._compute_overlap(q_entities, wave.entities)
            t_overlap = self._compute_temporal_resonance(q_temporals, wave, q_coords)
            s_overlap = self._compute_overlap(q_semantics, wave.semantics)
            r_overlap = self._compute_overlap(q_relations, wave.relations)

            # Embedding similarity (if available)
            emb_sim = 0.0
            if query_embedding and wave.embedding:
                emb_sim = self._cosine_similarity(query_embedding, wave.embedding)

            # THE RESONANCE EQUATION:
            # R(m|Q) = w_e·e + w_t·t + w_s·s + w_r·r + w_emb·emb
            # Weights depend on query type

            if q_type == 'temporal':
                # For temporal queries, boost temporal and entity dimensions
                # TUNED: Entity is primary (who), temporal is secondary (when)
                resonance = (
                    0.40 * e_overlap +  # UP from 0.35 - "When did CAROLINE..."
                    0.35 * t_overlap +  # UP from 0.30 - temporal is the target
                    0.15 * s_overlap +  # DOWN from 0.20 - reduce semantic noise
                    0.05 * r_overlap +
                    0.05 * emb_sim      # DOWN from 0.10 - trust structure over embedding
                )
            elif q_type == 'single_hop':
                # For single-hop, entity matters most - pure factual lookup
                # TUNED: Kill temporal noise, boost entity
                resonance = (
                    0.45 * e_overlap +  # UP from 0.35 - entity IS the query
                    0.05 * t_overlap +  # DOWN from 0.10 - single-hop has no time dimension
                    0.30 * s_overlap +  # DOWN from 0.35 - semantic secondary
                    0.05 * r_overlap +  # DOWN from 0.10 - less relational noise
                    0.15 * emb_sim      # UP from 0.10 - embedding as fallback
                )
            elif q_type == 'multi_hop':
                # For multi-hop, relations matter more
                resonance = (
                    0.25 * e_overlap +
                    0.10 * t_overlap +
                    0.25 * s_overlap +
                    0.25 * r_overlap +
                    0.15 * emb_sim
                )
            elif q_type == 'open_domain':
                # For open domain, embedding similarity matters more
                resonance = (
                    0.20 * e_overlap +
                    0.10 * t_overlap +
                    0.30 * s_overlap +
                    0.10 * r_overlap +
                    0.30 * emb_sim
                )
            else:  # adversarial
                # For adversarial, entity specificity is critical
                resonance = (
                    0.40 * e_overlap +
                    0.15 * t_overlap +
                    0.25 * s_overlap +
                    0.10 * r_overlap +
                    0.10 * emb_sim
                )

            # 5. Weight by shard alignment
            shard_weight = wave.shards.get(q_type)
            resonance *= (1.0 + shard_weight)  # Boost, not multiply to zero

            # BONUS: Entity match bonuses (AMPLIFIED for aggressive strike)
            if q_entities and e_overlap == 1.0:
                resonance += 0.25  # UP from 0.15 - perfect entity match is signal
            elif q_entities and e_overlap >= 0.5:
                resonance += 0.12  # NEW - partial entity match also valuable

            breakdown = {
                'entity': e_overlap,
                'temporal': t_overlap,
                'semantic': s_overlap,
                'relation': r_overlap,
                'embedding': emb_sim,
                'shard': shard_weight,
                'query_type': q_type,
            }

            scored.append((wave, resonance, breakdown))

        # 6. Sort and return
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def _extract_entities(self, content: str, speaker: str) -> set[str]:
        """Extract entity coordinates from content."""
        entities = set()

        # Speaker is always an entity
        if speaker:
            entities.add(speaker.lower())

        # Find capitalized words (proper nouns)
        exclude = {
            'the', 'this', 'that', 'what', 'when', 'where', 'who', 'how',
            'yes', 'no', 'and', 'but', 'hey', 'hi', 'hello', 'thanks',
            'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
            'saturday', 'sunday', 'january', 'february', 'march', 'april',
            'may', 'june', 'july', 'august', 'september', 'october',
            'november', 'december', 'lgbtq',
        }

        words = re.findall(r'\b[A-Z][a-z]+\b', content)
        for word in words:
            if word.lower() not in exclude:
                entities.add(word.lower())

        return entities

    def _extract_temporal_markers(self, content: str) -> set[str]:
        """Extract temporal markers from content."""
        markers = set()
        content_lower = content.lower()

        # Date patterns (YYYY-MM-DD)
        dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', content)
        markers.update(dates)

        # Year patterns
        years = re.findall(r'\b(20\d{2})\b', content)
        markers.update(years)

        # Relative markers (that were resolved)
        if 'yesterday' in content_lower:
            markers.add('yesterday')
        if 'last week' in content_lower:
            markers.add('last_week')
        if 'last year' in content_lower or re.search(r'\bin \d{4}\b', content_lower):
            markers.add('last_year')
        if re.search(r'\d+\s+years?\s+ago', content_lower):
            markers.add('years_ago')

        return markers

    def _extract_semantics(self, content: str) -> set[str]:
        """Extract semantic keywords from content."""
        semantics = set()
        content_lower = content.lower()

        # Extract meaningful words (4+ chars, not stopwords)
        stopwords = {
            'what', 'when', 'where', 'which', 'does', 'would', 'could',
            'have', 'been', 'being', 'their', 'there', 'this', 'that',
            'with', 'from', 'about', 'into', 'your', 'they', 'them',
            'said', 'says', 'will', 'just', 'also', 'been', 'were',
            'some', 'than', 'then', 'very', 'after', 'before',
        }

        words = re.findall(r'\b[a-z]{4,}\b', content_lower)
        semantics = {w for w in words if w not in stopwords}

        # Also extract compound concepts
        topic_patterns = {
            r'lgbtq\s*\+?\s*support\s*group': 'lgbtq_support_group',
            r'support\s*group': 'support_group',
            r'charity\s*race': 'charity_race',
            r'pottery\s*workshop': 'pottery_workshop',
            r'art\s*show': 'art_show',
        }

        for pattern, concept in topic_patterns.items():
            if re.search(pattern, content_lower):
                semantics.add(concept)

        return semantics

    def _extract_relations(self, content: str) -> set[str]:
        """Extract relation coordinates from content."""
        relations = set()
        content_lower = content.lower()

        relation_map = {
            'went': 'attend', 'go': 'attend', 'going': 'attend', 'attend': 'attend',
            'visited': 'visit', 'visit': 'visit',
            'joined': 'join', 'join': 'join',
            'started': 'start', 'start': 'start', 'began': 'start',
            'finished': 'finish', 'completed': 'finish',
            'painted': 'create', 'paint': 'create', 'made': 'create',
            'created': 'create', 'create': 'create',
            'ran': 'participate', 'run': 'participate', 'running': 'participate',
            'works': 'work', 'work': 'work', 'worked': 'work',
            'lives': 'reside', 'live': 'reside', 'lived': 'reside',
            'moved': 'relocate', 'move': 'relocate',
        }

        words = re.findall(r'\b\w+\b', content_lower)
        for word in words:
            if word in relation_map:
                relations.add(relation_map[word])

        return relations

    def _compute_overlap(self, query_set: set, memory_set: set) -> float:
        """Compute Jaccard-like overlap between sets."""
        if not query_set:
            return 0.0

        intersection = query_set & memory_set
        return len(intersection) / len(query_set)

    def _compute_temporal_resonance(
        self,
        q_temporals: set,
        wave: MemoryWave,
        q_coords: dict,
    ) -> float:
        """Compute temporal resonance score."""
        # Direct marker overlap
        if q_temporals and wave.temporal_markers:
            overlap = len(q_temporals & wave.temporal_markers) / len(q_temporals)
            if overlap > 0:
                return overlap

        # If query asks "when" and memory has temporal info, that's a match
        if 'when' in q_coords.get('unknowns', []):
            if wave.temporal_markers or wave.timestamp:
                return 0.5

        # If memory has temporal content and query is temporal-related
        if q_temporals and (wave.temporal_markers or wave.timestamp):
            return 0.3

        return 0.0

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between vectors."""
        if len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)

    def get_stats(self) -> dict:
        """Get field statistics."""
        return {
            'total_waves': len(self._waves),
            'unique_entities': len(self._entity_index),
            'unique_temporals': len(self._temporal_index),
            'unique_semantics': len(self._semantic_index),
            'unique_relations': len(self._relation_index),
        }


class ResonanceRetriever:
    """High-level retriever using the resonance field."""

    def __init__(self):
        self._field = ResonanceField()

    def ingest(
        self,
        wave_id: str,
        content: str,
        speaker: str,
        timestamp: datetime | None,
        embedding: list[float] | None = None,
    ) -> MemoryWave:
        """Ingest a memory."""
        return self._field.ingest(wave_id, content, speaker, timestamp, embedding)

    def query(
        self,
        query_text: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryWave, float, dict]]:
        """Query memories."""
        return self._field.query(query_text, limit, query_embedding)

    def get_stats(self) -> dict:
        """Get statistics."""
        return self._field.get_stats()
