"""Wave Resonance Retriever - Retrieval through wave interference patterns.

Every query is a wave. Find memories whose waves resonate.
No classification routing - just resonance.

v1.1: Enhanced with lexical resonance and better signal matching
v1.2: Added entity mismatch penalty for adversarial robustness
"""

import math
import re
from datetime import datetime, timezone
from typing import Any, Callable
from collections import Counter

from .data_types import (
    WaveAmplitudes,
    MemoryWave,
    WaveSignal,
    SignalType,
    TemporalSignal,
    EntitySignal,
    RelationSignal,
    ActionSignal,
    StateSignal,
    DIMENSION_INDEX,
)
from .wave_encoder import WaveEncoder


# Stopwords for lexical matching
STOPWORDS = {
    "i", "me", "my", "myself", "we", "our", "ours", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them",
    "their", "what", "which", "who", "whom", "this", "that", "these", "those",
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "having", "do", "does", "did", "doing", "a", "an", "the", "and",
    "but", "if", "or", "because", "as", "until", "while", "of", "at", "by",
    "for", "with", "about", "against", "between", "into", "through", "during",
    "before", "after", "above", "below", "to", "from", "up", "down", "in",
    "out", "on", "off", "over", "under", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "all", "each", "few",
    "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "can", "will", "just",
    "don", "should", "now", "would", "could", "did",
}


def tokenize(text: str) -> list[str]:
    """Simple tokenizer for lexical matching."""
    tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 2]


def stem_word(word: str) -> str:
    """Simple stemmer for better matching."""
    if word.endswith('ing') and len(word) > 5:
        stem = word[:-3]
        if len(stem) > 3 and stem[-1] == stem[-2]:
            return stem[:-1]
        return stem
    if word.endswith('ed') and len(word) > 4:
        return word[:-2]
    if word.endswith('ies') and len(word) > 4:
        return word[:-3] + 'y'
    if word.endswith('s') and len(word) > 3 and not word.endswith('ss'):
        return word[:-1]
    return word


def tokenize_with_stemming(text: str) -> list[str]:
    """Tokenize and stem."""
    return [stem_word(t) for t in tokenize(text)]


class WaveResonanceRetriever:
    """Retrieve memories through wave resonance, not classification routing.

    Every query is a wave. Find memories whose waves resonate.
    Resonance = dimensional alignment + signal matching + semantic similarity
    """

    def __init__(
        self,
        encoder: WaveEncoder | None = None,
        use_semantic_resonance: bool = True,
    ):
        """Initialize the retriever.

        Args:
            encoder: WaveEncoder instance. Creates one if not provided.
            use_semantic_resonance: Whether to use embedding similarity.
        """
        self.encoder = encoder or WaveEncoder()
        self.use_semantic_resonance = use_semantic_resonance
        self._memories: list[MemoryWave] = []
        self._memory_index: dict[str, MemoryWave] = {}

    def clear(self) -> None:
        """Clear all stored memories."""
        self._memories.clear()
        self._memory_index.clear()

    def add_memory(self, wave: MemoryWave) -> None:
        """Add a memory wave to the store."""
        self._memories.append(wave)
        self._memory_index[wave.wave_id] = wave

    def add_memories(self, waves: list[MemoryWave]) -> None:
        """Batch add memories."""
        for wave in waves:
            self.add_memory(wave)

    def ingest(
        self,
        content: str,
        speaker: str = "",
        timestamp: datetime | None = None,
        session_key: str = "",
        msg_idx: int = 0,
        reference_date: datetime | None = None,
    ) -> MemoryWave:
        """Ingest text directly as a memory wave."""
        wave = self.encoder.encode(
            content=content,
            speaker=speaker,
            timestamp=timestamp,
            session_key=session_key,
            msg_idx=msg_idx,
            reference_date=reference_date,
        )
        self.add_memory(wave)
        return wave

    def get_memory(self, wave_id: str) -> MemoryWave | None:
        """Get a specific memory by ID."""
        return self._memory_index.get(wave_id)

    @property
    def memory_count(self) -> int:
        """Number of stored memories."""
        return len(self._memories)

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        reference_date: datetime | None = None,
        filter_fn: Callable[[MemoryWave], bool] | None = None,
        min_resonance: float = 0.0,
    ) -> list[tuple[MemoryWave, float]]:
        """Find memories that resonate with the query.

        Args:
            query: Query text.
            top_k: Maximum number of results.
            reference_date: Reference date for temporal resolution.
            filter_fn: Optional filter function for memories.
            min_resonance: Minimum resonance score to include.

        Returns:
            List of (memory, resonance_score) tuples, sorted by resonance.
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)

        # Encode query as a wave
        query_wave = self.encoder.encode(
            content=query,
            speaker="query",
            timestamp=reference_date,
            reference_date=reference_date,
        )

        # Compute resonance with all memories
        scored = []
        for memory in self._memories:
            # Apply filter if provided
            if filter_fn is not None and not filter_fn(memory):
                continue

            resonance = self._compute_resonance(query_wave, memory)

            if resonance >= min_resonance:
                scored.append((memory, resonance))

        # Sort by resonance (highest first)
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:top_k]

    def retrieve_with_wave(
        self,
        query_wave: MemoryWave,
        top_k: int = 20,
        filter_fn: Callable[[MemoryWave], bool] | None = None,
        min_resonance: float = 0.0,
    ) -> list[tuple[MemoryWave, float]]:
        """Retrieve using a pre-encoded query wave."""
        scored = []
        for memory in self._memories:
            if filter_fn is not None and not filter_fn(memory):
                continue

            resonance = self._compute_resonance(query_wave, memory)

            if resonance >= min_resonance:
                scored.append((memory, resonance))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def _compute_resonance(
        self,
        query: MemoryWave,
        memory: MemoryWave
    ) -> float:
        """Compute total resonance between query wave and memory wave.

        Like wave interference:
        - Aligned amplitudes = constructive interference = high resonance
        - Misaligned amplitudes = destructive interference = low resonance

        Total resonance = weighted combination of:
        1. Lexical resonance (keyword matching) - CRITICAL for retrieval
        2. Dimensional resonance (amplitude vector alignment)
        3. Signal resonance (extracted signal matching)
        4. Semantic resonance (embedding similarity)
        """
        # Get dynamic weights based on query's amplitude profile
        weights = self._dynamic_weights(query.amplitudes)

        # Compute component resonances
        lex_res = self._lexical_resonance(query.content, memory.content)
        dim_res = self._dimensional_resonance(query.amplitudes, memory.amplitudes)
        sig_res = self._signal_resonance(query, memory)
        sem_res = self._semantic_resonance(query, memory) if self.use_semantic_resonance else 0.5

        # Speaker matching boost
        speaker_boost = self._speaker_match(query.content, memory.speaker)

        # Entity mismatch penalty (critical for adversarial questions)
        # This penalizes memories about the wrong person
        entity_penalty = self._entity_mismatch_penalty(query.content, memory)

        # Combine resonances - lexical is heavily weighted for retrieval accuracy
        total = (
            weights["lexical"] * lex_res +
            weights["dimensional"] * dim_res +
            weights["signal"] * sig_res +
            weights["semantic"] * sem_res +
            speaker_boost +
            entity_penalty  # Negative value when mismatch detected
        )

        return max(min(total, 1.0), 0.0)  # Clamp to [0, 1]

    def _lexical_resonance(self, query_text: str, memory_text: str) -> float:
        """Compute lexical resonance based on keyword overlap.

        This is critical for finding relevant memories based on content.
        """
        query_tokens = set(tokenize_with_stemming(query_text))
        memory_tokens = set(tokenize_with_stemming(memory_text))

        if not query_tokens:
            return 0.0

        # Jaccard-like overlap
        overlap = len(query_tokens & memory_tokens)
        if overlap == 0:
            return 0.0

        # Weighted by how much of the query is covered
        query_coverage = overlap / len(query_tokens)

        # Bonus for exact phrase matches (important keywords)
        query_lower = query_text.lower()
        memory_lower = memory_text.lower()

        # Check for important entity names in query appearing in memory
        entity_bonus = 0.0
        # Extract capitalized words from original query (likely entities)
        entities = re.findall(r'\b[A-Z][a-z]+\b', query_text)
        for entity in entities:
            if entity.lower() in memory_lower:
                entity_bonus += 0.15

        return min(query_coverage + entity_bonus, 1.0)

    def _speaker_match(self, query_text: str, memory_speaker: str) -> float:
        """Boost resonance if query mentions the memory's speaker."""
        if not memory_speaker:
            return 0.0

        query_lower = query_text.lower()
        speaker_lower = memory_speaker.lower()

        # Direct speaker mention
        if speaker_lower in query_lower:
            return 0.1

        # First name match
        first_name = speaker_lower.split()[0] if speaker_lower else ""
        if first_name and first_name in query_lower:
            return 0.08

        return 0.0

    def _entity_mismatch_penalty(self, query_text: str, memory: MemoryWave) -> float:
        """PENALIZE when query asks about person A but memory is about person B.

        This is critical for adversarial questions that swap entities.
        Example: "What did Caroline realize?" when memory is from Melanie.

        Returns:
            Negative penalty (typically -0.15 to -0.25) if mismatch detected.
            0.0 if no mismatch or uncertain.
        """
        if not memory.speaker:
            return 0.0

        # Extract person names from query (capitalized words, likely person names)
        query_entities = set(re.findall(r'\b[A-Z][a-z]{2,}\b', query_text))

        # Filter out common non-name words
        non_names = {"What", "How", "When", "Where", "Why", "Who", "Which", "Does",
                     "Did", "Was", "Were", "Has", "Have", "Can", "Could", "Would",
                     "Should", "The", "This", "That", "These", "Those", "After",
                     "Before", "During", "About", "Because", "Since", "Also", "Just"}
        query_entities = query_entities - non_names

        if not query_entities:
            return 0.0  # No specific person mentioned in query

        # Get memory speaker's first name
        speaker_first = memory.speaker.split()[0] if memory.speaker else ""
        speaker_first_lower = speaker_first.lower()

        # Check if speaker matches any query entity
        query_entities_lower = {e.lower() for e in query_entities}

        if speaker_first_lower in query_entities_lower:
            return 0.0  # Speaker matches query entity - no penalty

        # Query mentions specific people, but memory's speaker isn't one of them
        # This is the adversarial pattern!
        #
        # Penalty depends on how specific the query is:
        # - One specific person asked about -> strong penalty
        # - Multiple people -> weaker penalty (might be about interaction)
        if len(query_entities) == 1:
            return -0.20  # Strong penalty - query is clearly about one person
        elif len(query_entities) == 2:
            return -0.12  # Moderate - could be about either person
        else:
            return -0.08  # Weak - multiple people mentioned

    def _dimensional_resonance(
        self,
        query: WaveAmplitudes,
        memory: WaveAmplitudes
    ) -> float:
        """Resonance based on amplitude vector alignment.

        Focus on dimensions the query cares about (high amplitude).
        """
        q_vec = query.to_list()
        m_vec = memory.to_list()

        # Weighted alignment - query dimensions weight the comparison
        weighted_sum = 0.0
        weight_total = 0.0

        for q_amp, m_amp in zip(q_vec, m_vec):
            if q_amp > 0.15:  # Query cares about this dimension
                # Alignment: minimum of both (bottleneck)
                alignment = min(q_amp, m_amp)
                weighted_sum += q_amp * alignment
                weight_total += q_amp

        if weight_total > 0:
            return weighted_sum / weight_total

        # Fallback to cosine similarity if query has no strong dimensions
        return query.cosine_similarity(memory)

    def _signal_resonance(
        self,
        query: MemoryWave,
        memory: MemoryWave
    ) -> float:
        """Resonance based on extracted signal matching."""
        resonances = []

        # Temporal signal resonance
        if query.get_temporal_signals() and memory.get_temporal_signals():
            temp_res = self._temporal_signal_match(query, memory)
            resonances.append(("temporal", query.amplitudes.temporal, temp_res))

        # Entity signal resonance
        if query.get_entity_signals() and memory.get_entity_signals():
            ent_res = self._entity_signal_match(query, memory)
            resonances.append(("entity", query.amplitudes.entity, ent_res))

        # Action signal resonance
        if query.get_action_signals() and memory.get_action_signals():
            act_res = self._action_signal_match(query, memory)
            resonances.append(("action", query.amplitudes.action, act_res))

        # Relation signal resonance
        if query.get_relation_signals() and memory.get_relation_signals():
            rel_res = self._relation_signal_match(query, memory)
            resonances.append(("relation", query.amplitudes.relational, rel_res))

        # State signal resonance
        q_states = [s for s in query.signals if isinstance(s, StateSignal)]
        m_states = [s for s in memory.signals if isinstance(s, StateSignal)]
        if q_states and m_states:
            state_res = self._state_signal_match(q_states, m_states)
            resonances.append(("state", query.amplitudes.state, state_res))

        if not resonances:
            return 0.5  # Neutral if no signals to compare

        # Weighted average by query amplitude
        total_weight = sum(amp for _, amp, _ in resonances)
        if total_weight == 0:
            return 0.5

        weighted_res = sum(amp * res for _, amp, res in resonances) / total_weight
        return weighted_res

    def _temporal_signal_match(
        self,
        query: MemoryWave,
        memory: MemoryWave
    ) -> float:
        """Match temporal signals."""
        best_match = 0.0

        q_signals = query.get_temporal_signals()
        m_signals = memory.get_temporal_signals()

        for q_sig in q_signals:
            for m_sig in m_signals:
                # Compare resolved dates if both exist
                if q_sig.resolved_date and m_sig.resolved_date:
                    diff = abs((q_sig.resolved_date - m_sig.resolved_date).days)
                    if diff == 0:
                        score = 1.0
                    elif diff <= 1:
                        score = 0.9
                    elif diff <= 7:
                        score = 0.7
                    elif diff <= 30:
                        score = 0.4
                    else:
                        score = 0.2
                else:
                    # Compare raw expressions
                    if q_sig.raw_expression.lower() == m_sig.raw_expression.lower():
                        score = 0.7
                    else:
                        score = 0.3

                # Weight by confidence
                score *= (q_sig.confidence + m_sig.confidence) / 2
                best_match = max(best_match, score)

        return best_match

    def _entity_signal_match(
        self,
        query: MemoryWave,
        memory: MemoryWave
    ) -> float:
        """Match entity signals."""
        q_entities = {s.entity_name.lower() for s in query.get_entity_signals()}
        m_entities = {s.entity_name.lower() for s in memory.get_entity_signals()}

        if not q_entities:
            return 0.5

        overlap = len(q_entities & m_entities)
        return min(overlap / len(q_entities), 1.0)

    def _action_signal_match(
        self,
        query: MemoryWave,
        memory: MemoryWave
    ) -> float:
        """Match action signals by verb lemma."""
        q_actions = {s.verb_lemma for s in query.get_action_signals()}
        m_actions = {s.verb_lemma for s in memory.get_action_signals()}

        if not q_actions:
            return 0.5

        overlap = len(q_actions & m_actions)
        return min(overlap / len(q_actions), 1.0)

    def _relation_signal_match(
        self,
        query: MemoryWave,
        memory: MemoryWave
    ) -> float:
        """Match relation signals."""
        best_match = 0.0

        q_signals = query.get_relation_signals()
        m_signals = memory.get_relation_signals()

        for q_rel in q_signals:
            for m_rel in m_signals:
                score = 0.0

                # Match entities involved
                q_ents = {q_rel.source_entity.lower(), q_rel.target_entity.lower()}
                m_ents = {m_rel.source_entity.lower(), m_rel.target_entity.lower()}

                entity_overlap = len(q_ents & m_ents) / max(len(q_ents), 1)
                score += entity_overlap * 0.5

                # Match relation type
                if q_rel.relation_type.lower() == m_rel.relation_type.lower():
                    score += 0.5

                best_match = max(best_match, score)

        return best_match

    def _state_signal_match(
        self,
        q_states: list[StateSignal],
        m_states: list[StateSignal]
    ) -> float:
        """Match state signals."""
        best_match = 0.0

        for q_state in q_states:
            for m_state in m_states:
                score = 0.0

                # Entity match
                if q_state.entity.lower() == m_state.entity.lower():
                    score += 0.4

                # Attribute match
                if q_state.attribute and m_state.attribute:
                    if q_state.attribute.lower() == m_state.attribute.lower():
                        score += 0.3

                # Value match
                if str(q_state.value).lower() == str(m_state.value).lower():
                    score += 0.3

                best_match = max(best_match, score)

        return best_match

    def _semantic_resonance(
        self,
        query: MemoryWave,
        memory: MemoryWave
    ) -> float:
        """Embedding cosine similarity."""
        if query.embedding is None or memory.embedding is None:
            return 0.5

        # Cosine similarity
        dot = sum(a * b for a, b in zip(query.embedding, memory.embedding))
        norm_q = math.sqrt(sum(a * a for a in query.embedding))
        norm_m = math.sqrt(sum(b * b for b in memory.embedding))

        if norm_q == 0 or norm_m == 0:
            return 0.5

        # Scale from [-1, 1] to [0, 1]
        return (dot / (norm_q * norm_m) + 1) / 2

    def _dynamic_weights(self, query_amplitudes: WaveAmplitudes) -> dict[str, float]:
        """Compute weights dynamically from query's amplitude profile.

        The query wave itself tells us what matters.
        Lexical is ALWAYS heavily weighted - keyword matching is critical.
        """
        max_amp = max(query_amplitudes.to_list())

        if max_amp > 0.6:
            # Query has strong dimensional focus -> weight signals higher
            return {
                "lexical": 0.35,      # Keyword matching always important
                "dimensional": 0.15,
                "signal": 0.35,       # Signal matching for structured queries
                "semantic": 0.15,
            }
        elif max_amp > 0.3:
            # Query has moderate focus -> balanced
            return {
                "lexical": 0.40,      # Lexical is key
                "dimensional": 0.15,
                "signal": 0.25,
                "semantic": 0.20,
            }
        else:
            # Query is diffuse -> rely more on lexical and semantic
            return {
                "lexical": 0.45,      # Lexical dominates for vague queries
                "dimensional": 0.10,
                "signal": 0.15,
                "semantic": 0.30,
            }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_wave_retriever(
    signal_threshold: float = 0.25,
    use_semantic: bool = True,
) -> WaveResonanceRetriever:
    """Create a configured wave retriever.

    Args:
        signal_threshold: Minimum amplitude to extract signals.
        use_semantic: Whether to use embedding-based semantic resonance.

    Returns:
        Configured WaveResonanceRetriever instance.
    """
    encoder = WaveEncoder(signal_threshold=signal_threshold)
    return WaveResonanceRetriever(
        encoder=encoder,
        use_semantic_resonance=use_semantic,
    )


def retrieve_by_resonance(
    query: str,
    memories: list[MemoryWave],
    top_k: int = 20,
    reference_date: datetime | None = None,
) -> list[tuple[MemoryWave, float]]:
    """One-shot retrieval without persistent storage.

    Args:
        query: Query text.
        memories: List of memory waves to search.
        top_k: Maximum results.
        reference_date: Reference date for temporal resolution.

    Returns:
        List of (memory, resonance_score) tuples.
    """
    retriever = create_wave_retriever()
    retriever.add_memories(memories)
    return retriever.retrieve(query, top_k=top_k, reference_date=reference_date)
