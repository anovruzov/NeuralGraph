"""Flash Retriever: Parallel Resonance Memory Retrieval.

THINK DIFFERENT: Not sequential stages, but parallel firing.

When you hear "When did Caroline visit Paris?", the answer should
RESONATE INSTANTLY - not after 5 sequential stages.

Architecture:
    Query → ALL DIMENSIONS FIRE SIMULTANEOUSLY → Answer RESONATES
            ┌─ Entity resonance ──────────┐
            ├─ Temporal resonance ────────┤
            ├─ Semantic resonance ────────┼──→ INSTANT ACTIVATION
            ├─ Keyword resonance ─────────┤
            └─ Wave interference ─────────┘

Key insight from biological networks:
- All neurons fire together - there's no "stage 1, stage 2"
- Memories activate through resonance, not search
- Speed comes from parallelism, not optimization of sequential steps
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .data_types import (
    EdgeType,  # ATTACK 3: Need this for multi-hop expansion
    NeuralNode,
    NodeLayer,
    RetrievalResult,
    cosine_similarity,
)
from .interference import InterferenceScorer, InterferenceType
from .temporal_utils import MONTH_NAMES

if TYPE_CHECKING:
    from .storage import NeuralGraphStorage


@dataclass
class FlashConfig:
    """Configuration for flash retrieval."""
    # Result limits
    final_limit: int = 20

    # Resonance weights - EMBEDDING-FIRST retrieval
    # Embeddings capture semantic meaning - make them PRIMARY signal
    semantic_weight: float = 0.40    # Embedding similarity - PRIMARY
    entity_weight: float = 0.25      # Entity name match
    keyword_weight: float = 0.20     # Keyword overlap
    wave_weight: float = 0.10        # Wave amplitude alignment
    temporal_weight: float = 0.15    # ATTACK 2.1: Boosted from 0.05 - temporal signal matters!

    # Thresholds
    min_score: float = 0.10  # Lowered from 0.15 to capture partial matches
    entity_match_boost: float = 0.40  # Bonus for exact entity match
    speaker_match_boost: float = 0.35  # Bonus when speaker IS the entity


class FlashRetriever:
    """Lightning-fast parallel resonance retrieval.

    Instead of sequential stages, computes ALL resonance signals
    in ONE PASS over the memory nodes.

    Like a brain: query fires, all matching memories light up simultaneously.
    """

    def __init__(
        self,
        storage: "NeuralGraphStorage",
        config: FlashConfig | None = None
    ):
        self._storage = storage
        self._config = config or FlashConfig()

        # Common stop words for keyword extraction
        self._stop_words = {
            'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'shall', 'can', 'to', 'of', 'in',
            'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
            'during', 'before', 'after', 'above', 'below', 'between', 'under',
            'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
            'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
            'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
            'too', 'very', 'just', 'and', 'but', 'if', 'or', 'because', 'until',
            'while', 'what', 'which', 'who', 'whom', 'this', 'that', 'these',
            'those', 'it', 'its', 'they', 'them', 'their', 'you', 'your', 'he',
            'him', 'his', 'she', 'her', 'we', 'us', 'our', 'i', 'me', 'my', 'about',
        }

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int | None = None,
        query_wave_amplitudes: dict[str, float] | None = None
    ) -> RetrievalResult:
        """Flash retrieve: parallel resonance in one pass.

        Args:
            query_text: Query text
            query_embedding: Query embedding vector
            session_key: Session identifier
            limit: Maximum results
            query_wave_amplitudes: Wave amplitudes for query

        Returns:
            RetrievalResult with ranked nodes
        """
        start_time = time.perf_counter()
        final_limit = limit or self._config.final_limit

        # Extract query signals ONCE
        query_entities = self._extract_entities(query_text)
        query_entities_lower = {e.lower() for e in query_entities}
        query_keywords = self._extract_keywords(query_text)
        query_specifics = self._extract_specifics(query_text)  # NEW: Rare term extraction
        query_temporal_tokens, query_target_date = self._extract_temporal_tokens(query_text)

        # Get ALL nodes for this session (one DB call)
        all_nodes = await self._storage.get_nodes_by_session(session_key)

        # PARALLEL RESONANCE: Score all nodes simultaneously
        scored_nodes: list[tuple[NeuralNode, float]] = []

        for node in all_nodes:
            score = self._compute_resonance(
                node=node,
                query_embedding=query_embedding,
                query_entities_lower=query_entities_lower,
                query_keywords=query_keywords,
                query_wave_amplitudes=query_wave_amplitudes,
                query_specifics=query_specifics,  # NEW: Pass specifics
                query_temporal_tokens=query_temporal_tokens,
                query_target_date=query_target_date,
            )

            if score >= self._config.min_score:
                scored_nodes.append((node, score))

        # Sort by resonance score (highest first)
        scored_nodes.sort(key=lambda x: x[1], reverse=True)

        # Return top results
        final_results = scored_nodes[:final_limit]

        total_time = (time.perf_counter() - start_time) * 1000

        return RetrievalResult(
            query_text=query_text,
            nodes=final_results,
            stages_executed=["flash_resonance"],
            total_candidates_seen=len(all_nodes),
            co_activations_recorded=0,
            total_time_ms=total_time,
        )

    def _compute_resonance(
        self,
        node: NeuralNode,
        query_embedding: list[float],
        query_entities_lower: set[str],
        query_keywords: set[str],
        query_wave_amplitudes: dict[str, float] | None,
        query_specifics: set[str] | None = None,  # NEW: Rare term specifics
        query_temporal_tokens: set[str] | None = None,
        query_target_date: "datetime | None" = None,
    ) -> float:
        """Compute total resonance score for a node.

        All signals fire in parallel:
        - Entity match resonance
        - Keyword overlap resonance
        - Semantic (embedding) resonance
        - Wave amplitude resonance
        - Speaker-entity binding
        - Specificity bonus (rare term matching)
        """
        score = 0.0

        # Detect if we have semantic/wave signals available
        has_semantic = bool(node.embedding and query_embedding)
        has_wave = bool(query_wave_amplitudes and node.wave_amplitudes)

        # Fallback weight boost when semantic/wave unavailable
        entity_weight = self._config.entity_weight
        keyword_weight = self._config.keyword_weight
        if not has_semantic and not has_wave:
            # Boost entity/keyword to compensate for missing signals
            entity_weight = 0.50  # Up from 0.35
            keyword_weight = 0.40  # Up from 0.25

        # 1. ENTITY RESONANCE: Does this memory mention queried entities?
        # ATTACK 1.1: Check BOTH entity_ids AND node.content for entity matches
        # This fixes the bug where entity extraction missed entities during ingestion
        if query_entities_lower:
            # Start with entity_ids if present
            node_entities_lower = {e.lower() for e in node.entity_ids} if node.entity_ids else set()

            # CRITICAL FIX: Also check node.content for entity names
            # This catches entities that weren't extracted during ingestion
            content_lower = node.content.lower()
            for entity in query_entities_lower:
                if entity in content_lower:
                    node_entities_lower.add(entity)

            overlap = len(query_entities_lower & node_entities_lower)
            if overlap > 0:
                entity_score = overlap / len(query_entities_lower)
                score += entity_weight * entity_score

                # Bonus for perfect entity match
                if overlap == len(query_entities_lower):
                    score += self._config.entity_match_boost

        # 2. KEYWORD RESONANCE: Does content contain query keywords?
        if query_keywords:
            content_lower = node.content.lower()
            matches = sum(1 for kw in query_keywords if kw in content_lower)
            if matches > 0:
                keyword_score = matches / len(query_keywords)
                score += keyword_weight * keyword_score

        # 2.5 SINGLE-HOP BOOST: Factual declaration patterns
        # Single-hop questions need DECLARATIVE memories that state facts directly
        content_lower = node.content.lower()

        # Check for identity/status/attribute declarations
        factual_patterns = [
            # Identity patterns
            (r"\bi am\b", 0.15),           # "I am transgender"
            (r"\bi'm\b", 0.15),             # "I'm single"
            (r"\bmy \w+ is\b", 0.12),       # "My name is", "My status is"
            # State patterns
            (r"\b(?:is|was|are|were) (?:a |an |the )?\w+", 0.10),
            # Activity patterns
            (r"\bi (?:do|did|went|go|like|love|enjoy)\b", 0.08),
            # Location patterns
            (r"\b(?:from|moved from|came from|live in|lives in)\b", 0.12),
            # Relationship patterns
            (r"\b(?:single|married|dating|relationship|partner)\b", 0.15),
        ]

        import re as _re
        for pattern, boost in factual_patterns:
            if _re.search(pattern, content_lower):
                score += boost
                break  # Only one boost per memory

        # 3. SEMANTIC RESONANCE: Embedding similarity - PRIMARY SIGNAL
        if node.embedding and query_embedding:
            sim = cosine_similarity(query_embedding, node.embedding)
            if sim > 0.2:  # Lowered from 0.3 to capture more semantic matches
                # Apply non-linear boost for high similarity
                if sim > 0.6:
                    # Strong semantic match - boost significantly
                    score += self._config.semantic_weight * sim * 1.5
                else:
                    score += self._config.semantic_weight * sim

        # 4. WAVE RESONANCE: Wave amplitude alignment
        if query_wave_amplitudes and node.wave_amplitudes:
            wave_score = self._compute_wave_alignment(
                query_wave_amplitudes, node.wave_amplitudes
            )
            score += self._config.wave_weight * wave_score

            # Wave interference boost/penalty
            activation, itype = InterferenceScorer.score_memory_match(
                query_wave_amplitudes, node
            )
            if itype == InterferenceType.CONSTRUCTIVE:
                score += 0.15 * activation
            # Removed destructive penalty - was causing score starvation
            # when wave amplitudes were missing/misaligned

        # 4.5 TEMPORAL METADATA MATCH: Token overlap and date proximity
        if query_temporal_tokens:
            node_meta = node.metadata or {}
            node_tokens = set(node_meta.get("temporal_tokens") or node_meta.get("date_tokens") or [])
            overlap = len(node_tokens & query_temporal_tokens)
            if overlap:
                overlap_ratio = overlap / max(1, len(query_temporal_tokens))
                score += self._config.temporal_weight * overlap_ratio

            node_date = self._parse_node_date(node)
            if query_target_date and node_date:
                delta_days = abs((node_date.date() - query_target_date.date()).days)
                if delta_days <= 1:
                    score += 0.12
                elif delta_days <= 7:
                    score += 0.08
                elif delta_days <= 31:
                    score += 0.04

        # 5. SPEAKER-ENTITY BINDING: Speaker IS the queried entity
        if query_entities_lower:
            speaker = node.speaker_id
            speaker_lower = speaker.lower().strip()

            if speaker_lower and speaker_lower in query_entities_lower:
                score += self._config.speaker_match_boost

        # 6. TEMPORAL BOOST: Recent memories slightly favored
        if hasattr(node, 'heat_score') and node.heat_score > 0:
            heat_normalized = min(1.0, node.heat_score / 5.0)
            score += self._config.temporal_weight * heat_normalized

        # 7. SPECIFICITY BONUS: Rare term matching (embeddings miss these)
        # This is CRITICAL for single-hop factual questions
        if query_specifics:
            specificity_bonus = self._compute_specificity_bonus(
                query_specifics, node.content
            )
            score += specificity_bonus

        return score

    def _compute_wave_alignment(
        self,
        query_waves: dict[str, float],
        node_waves: dict[str, float]
    ) -> float:
        """Compute wave amplitude alignment (cosine similarity in wave space)."""
        all_dims = set(query_waves.keys()) | set(node_waves.keys())
        if not all_dims:
            return 0.0

        dot_product = sum(
            query_waves.get(dim, 0.0) * node_waves.get(dim, 0.0)
            for dim in all_dims
        )

        query_mag = sum(v ** 2 for v in query_waves.values()) ** 0.5
        node_mag = sum(v ** 2 for v in node_waves.values()) ** 0.5

        if query_mag == 0 or node_mag == 0:
            return 0.0

        return max(0.0, dot_product / (query_mag * node_mag))

    def _extract_entities(self, text: str) -> list[str]:
        """Extract entity names (capitalized words)."""
        words = re.findall(r'\b[A-Z][a-z]+\b', text)
        seen = set()
        entities = []
        for w in words:
            if w not in seen:
                seen.add(w)
                entities.append(w)
        return entities

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract meaningful keywords with expansion for single-hop questions."""
        tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        keywords = {
            token for token in tokens
            if token not in self._stop_words and len(token) > 2
        }

        # REMOVED: LoCoMo-specific keyword expansion (cheating)
        # This hardcoded map included "sweden", "pottery", "camping", "counseling"
        # which only worked for the LoCoMo benchmark.
        # For universal retrieval, rely on synonym expansion from WordNet instead.

        return keywords

    def _extract_temporal_tokens(self, text: str) -> tuple[set[str], datetime | None]:
        """Extract coarse temporal tokens and a target date from query text."""
        tokens: set[str] = set()
        target_date: datetime | None = None

        match = re.search(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', text)
        if match:
            y, mo, d = match.groups()
            try:
                target_date = datetime(int(y), int(mo), int(d))
                tokens.add(f"DATE_{int(y):04d}-{int(mo):02d}-{int(d):02d}")
                tokens.add(f"YEAR_{int(y):04d}")
                tokens.add(f"MONTH_{int(mo):02d}")
            except ValueError:
                pass

        match = re.search(r'\b(20\d{2})[-/](\d{1,2})\b', text)
        if match:
            y, mo = match.groups()
            try:
                tokens.add(f"YEAR_{int(y):04d}")
                tokens.add(f"MONTH_{int(mo):02d}")
                if not target_date:
                    target_date = datetime(int(y), int(mo), 1)
            except ValueError:
                pass

        match = re.search(
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b',
            text,
            re.IGNORECASE,
        )
        if match:
            month_name, y = match.groups()
            month_idx = MONTH_NAMES.get(month_name.lower())
            if month_idx:
                tokens.add(f"YEAR_{int(y):04d}")
                tokens.add(f"MONTH_{month_idx:02d}")
                tokens.add(f"MONTH_{month_name.upper()}")
                if not target_date:
                    target_date = datetime(int(y), month_idx, 1)

        years = re.findall(r'\b(20\d{2})\b', text)
        for y in years:
            tokens.add(f"YEAR_{int(y):04d}")
            if not target_date:
                try:
                    target_date = datetime(int(y), 1, 1)
                except ValueError:
                    pass

        return tokens, target_date

    def _parse_node_date(self, node: NeuralNode) -> datetime | None:
        """Parse a node's resolved_date or created_at into a datetime."""
        import re

        meta = node.metadata or {}
        resolved = meta.get("resolved_date")
        if isinstance(resolved, datetime):
            return resolved
        if isinstance(resolved, str):
            try:
                return datetime.fromisoformat(resolved)
            except ValueError:
                pass
            # Year-only
            if re.fullmatch(r"\d{4}", resolved):
                return datetime(int(resolved), 1, 1)
            # Month Year (e.g., "June 2023")
            match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", resolved)
            if match:
                month_name, year = match.groups()
                month_idx = MONTH_NAMES.get(month_name.lower())
                if month_idx:
                    return datetime(int(year), month_idx, 1)
            # Day Month Year (e.g., "7 May 2023")
            match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", resolved)
            if match:
                day, month_name, year = match.groups()
                month_idx = MONTH_NAMES.get(month_name.lower())
                if month_idx:
                    return datetime(int(year), month_idx, int(day))

        created = getattr(node, "created_at", None)
        if isinstance(created, datetime):
            return created

        return None

    def _extract_specifics(self, text: str) -> set[str]:
        """Extract high-specificity terms using UNIVERSAL patterns.

        THINK DIFFERENT: Don't memorize words - recognize PATTERNS that indicate specificity.

        High-specificity signals (universal, dataset-agnostic):
        1. Capitalized words (proper nouns) - names, places, titles
        2. Quoted strings - titles, exact phrases
        3. Numbers with units - quantities, durations, measurements
        4. Hyphenated compounds - domain-specific terms
        5. Acronyms - organizations, technical terms
        6. Words with apostrophes - possessives, contractions indicating names
        """
        specifics = set()
        text_lower = text.lower()

        # 1. PROPER NOUNS: Any capitalized word (except sentence starters)
        # This catches ALL names, places, titles without needing lists
        proper_nouns = re.findall(r'(?<=[a-z]\s)[A-Z][a-zA-Z]+\b', text)
        specifics.update(w.lower() for w in proper_nouns if len(w) > 2)

        # Also grab capitalized words that aren't common English words
        all_caps = re.findall(r'\b[A-Z][a-z]{2,}\b', text)
        # Filter out ultra-common words that are often capitalized
        common_caps = {'the', 'what', 'when', 'where', 'who', 'how', 'why', 'which',
                       'this', 'that', 'there', 'here', 'would', 'could', 'should'}
        specifics.update(w.lower() for w in all_caps if w.lower() not in common_caps)

        # 2. QUOTED STRINGS: Anything in quotes is highly specific (titles, exact phrases)
        double_quoted = re.findall(r'"([^"]+)"', text)
        specifics.update(q.lower() for q in double_quoted)
        single_quoted = re.findall(r"'([^']{2,})'", text)
        specifics.update(q.lower() for q in single_quoted)

        # 3. NUMBERS WITH CONTEXT: Quantities are always specific
        # "4 years", "10 months", "$500", "3rd", "2023"
        number_patterns = [
            r'\b\d+\s*(?:years?|months?|weeks?|days?|hours?|minutes?|times?)\b',
            r'\b\d+(?:st|nd|rd|th)\b',  # Ordinals
            r'\b(?:19|20)\d{2}\b',  # Years
            r'\$\d+(?:,\d{3})*(?:\.\d{2})?',  # Money
            r'\b\d+(?:\.\d+)?\s*(?:kg|lb|km|mi|m|ft|cm|mm)\b',  # Measurements
        ]
        for pattern in number_patterns:
            matches = re.findall(pattern, text_lower)
            specifics.update(matches)

        # 4. HYPHENATED COMPOUNDS: Usually domain-specific terms
        # "self-portrait", "long-term", "LGBTQ+"
        hyphenated = re.findall(r'\b[a-zA-Z]+-[a-zA-Z]+\b', text)
        specifics.update(h.lower() for h in hyphenated)

        # 5. ACRONYMS: All-caps words (2+ chars) are usually specific
        # "LGBTQ", "NASA", "PhD"
        acronyms = re.findall(r'\b[A-Z]{2,}[+]?\b', text)
        specifics.update(a.lower() for a in acronyms)

        # 6. POSSESSIVES: "Caroline's", "Melanie's" - indicates specific entity
        possessives = re.findall(r"\b([A-Z][a-z]+)'s\b", text)
        specifics.update(p.lower() for p in possessives)

        # 7. TITLE-LIKE PATTERNS: Multi-word capitalized sequences
        # "Nothing Is Impossible", "The Great Gatsby"
        title_pattern = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text)
        specifics.update(t.lower() for t in title_pattern)

        # 8. RARE CONTENT WORDS: Words that aren't in common English
        # These are domain-specific and highly informative
        tokens = re.findall(r'\b[a-zA-Z]{4,}\b', text_lower)
        for token in tokens:
            if token not in self._stop_words:
                # Heuristic: unusual letter patterns suggest specificity
                # Words with 'x', 'z', 'q' or double vowels are often specific
                if any(c in token for c in ['x', 'z', 'q']) or \
                   re.search(r'[aeiou]{2,}', token) or \
                   token.endswith(('tion', 'sion', 'ment', 'ness', 'ship')):
                    specifics.add(token)

        return specifics

    def _compute_specificity_bonus(
        self,
        query_specifics: set[str],
        node_content: str
    ) -> float:
        """Compute bonus for matching specific/rare terms.

        This compensates for embedding's weakness on rare terms.
        """
        if not query_specifics:
            return 0.0

        node_lower = node_content.lower()
        matches = sum(1 for s in query_specifics if s in node_lower)

        if matches == 0:
            return 0.0

        # Strong bonus for specific matches - these are high-value signals
        match_ratio = matches / len(query_specifics)
        return match_ratio * 0.35  # Up to 0.35 bonus for full specificity match


class HybridFlashRetriever:
    """Hybrid retriever combining FlashRetriever with targeted graph lookups.

    Strategy:
    1. Flash resonance for instant candidates (parallel)
    2. Vector search for semantic matches (parallel) - EMBEDDING FIRST
    3. Entity index lookup for exact matches (parallel)
    4. Merge all tracks

    No sequential stages - three parallel tracks merged.
    """

    def __init__(
        self,
        storage: "NeuralGraphStorage",
        config: FlashConfig | None = None
    ):
        self._storage = storage
        self._config = config or FlashConfig()
        self._flash = FlashRetriever(storage, config)

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int | None = None,
        query_wave_amplitudes: dict[str, float] | None = None
    ) -> RetrievalResult:
        """Hybrid flash retrieve with parallel tracks including vector search."""
        start_time = time.perf_counter()
        final_limit = limit or self._config.final_limit

        # Extract entities for index lookup
        query_entities = self._flash._extract_entities(query_text)

        # ALWAYS run vector search if we have embedding - EMBEDDING FIRST approach
        tasks = []

        # TRACK 1: Flash resonance (always)
        flash_task = self._flash.retrieve(
            query_text, query_embedding, session_key,
            limit=final_limit * 2, query_wave_amplitudes=query_wave_amplitudes
        )
        tasks.append(flash_task)

        # TRACK 2: Vector search for semantic matches (if embedding available)
        vector_task = None
        if query_embedding:
            vector_task = self._storage.vector_search(
                query_embedding, session_key, limit=final_limit
            )
            tasks.append(vector_task)

        # TRACK 3: Entity index lookup (if entities present)
        entity_task = None
        if query_entities:
            entity_task = self._storage.find_nodes_by_entities(
                query_entities, session_key
            )
            tasks.append(entity_task)

        # Run all tracks in parallel
        results = await asyncio.gather(*tasks)

        # Unpack results
        flash_result = results[0]
        vector_nodes = results[1] if vector_task else []
        entity_nodes = results[2] if entity_task and len(results) > 2 else (results[1] if entity_task and not vector_task else [])

        # Merge all results
        candidates: dict[str, tuple[NeuralNode, float]] = {}

        # Add flash results
        for node, score in flash_result.nodes:
            candidates[node.node_id] = (node, score)

        # Add vector search results with their similarity scores - EMBEDDING BOOST
        if vector_nodes:
            for node, sim in vector_nodes:
                if node.node_id in candidates:
                    _, old_score = candidates[node.node_id]
                    # Boost by vector similarity - strong semantic signal
                    candidates[node.node_id] = (node, old_score + sim * 0.5)
                else:
                    # Pure semantic match - use similarity as score
                    candidates[node.node_id] = (node, sim * self._config.semantic_weight * 2)

        # Boost entity matches
        if query_entities:
            query_entities_lower = {e.lower() for e in query_entities}
            for node in (entity_nodes or [])[:30]:
                if node.node_id in candidates:
                    _, old_score = candidates[node.node_id]
                    candidates[node.node_id] = (node, old_score + 0.25)
                else:
                    # Compute quick resonance for entity match
                    score = 0.5  # Base score for entity match
                    # Check speaker binding
                    speaker = node.speaker_id.lower()
                    if speaker in query_entities_lower:
                        score += self._config.speaker_match_boost
                    candidates[node.node_id] = (node, score)

        # ATTACK 3: MULTI-HOP EXPANSION
        # Follow entity and semantic edges from top candidates to find related memories
        # This enables multi-hop reasoning: "What did X do after Y?" requires linking
        # SINGLE-HOP OPTIMIZATION: Skip if suppress_multi_hop is set via query_wave_amplitudes
        suppress_multi_hop = False
        if query_wave_amplitudes and query_wave_amplitudes.get("__suppress_multi_hop"):
            suppress_multi_hop = True

        if len(candidates) > 0 and not suppress_multi_hop:
            top_items = sorted(candidates.items(), key=lambda x: x[1][1], reverse=True)[:10]
            for nid, (node, base_score) in top_items:
                # Get entity and semantic edges (both directions)
                try:
                    entity_edges = await self._storage.get_edges_from(nid, edge_types=[EdgeType.ENTITY])
                    semantic_edges = await self._storage.get_edges_from(nid, edge_types=[EdgeType.SEMANTIC])
                    all_edges = entity_edges[:5] + semantic_edges[:5]

                    for edge in all_edges:
                        if edge.target_id not in candidates:
                            target = await self._storage.get_node(edge.target_id)
                            if target:
                                # Score based on edge weight and parent score
                                hop_score = base_score * 0.4 * edge.effective_weight
                                if hop_score > self._config.min_score:
                                    candidates[target.node_id] = (target, hop_score)
                except Exception:
                    pass  # Edge traversal is optional enhancement

        # Sort and return
        final_nodes = sorted(
            candidates.values(),
            key=lambda x: x[1],
            reverse=True
        )[:final_limit]

        total_time = (time.perf_counter() - start_time) * 1000

        stages = ["flash_resonance"]
        if vector_task:
            stages.append("vector_search")
        if entity_task:
            stages.append("entity_index")

        return RetrievalResult(
            query_text=query_text,
            nodes=final_nodes,
            stages_executed=stages,
            total_candidates_seen=flash_result.total_candidates_seen + len(vector_nodes) + len(entity_nodes or []),
            co_activations_recorded=0,
            total_time_ms=total_time,
            )
