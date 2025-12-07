"""Granularity router for adaptive retrieval (MemGAS architecture)."""

import logging
import re
import time
import uuid
from typing import Any, Callable, Protocol

from .data_types import (
    ChunkingConfig,
    GranularChunk,
    GranularityLevel,
    GranularityStats,
    RetrievalResult,
    RoutingDecision,
)
from .entropy_calculator import EntropyCalculator

logger = logging.getLogger(__name__)


class ChunkRetriever(Protocol):
    """Protocol for chunk retrieval by granularity."""

    async def retrieve_chunks(
        self,
        query: str,
        session_key: str,
        granularity: GranularityLevel,
        limit: int = 10,
    ) -> tuple[list[GranularChunk], list[float]]:
        """Retrieve chunks at specified granularity.

        Returns:
            Tuple of (chunks, scores).
        """
        ...


class MultiGranularityChunker:
    """Creates chunks at multiple granularity levels."""

    def __init__(self, config: ChunkingConfig | None = None):
        """Initialize chunker.

        Args:
            config: Chunking configuration.
        """
        self._config = config or ChunkingConfig()

    def chunk_text(
        self,
        text: str,
        episode_uid: str,
        session_key: str,
    ) -> dict[GranularityLevel, list[GranularChunk]]:
        """Create chunks at all granularity levels.

        Args:
            text: Text to chunk.
            episode_uid: Source episode UID.
            session_key: Session key.

        Returns:
            Dictionary mapping granularity to chunks.
        """
        return {
            GranularityLevel.KEYWORD: self._extract_keywords(
                text, episode_uid, session_key
            ),
            GranularityLevel.SENTENCE: self._chunk_sentences(
                text, episode_uid, session_key
            ),
            GranularityLevel.PARAGRAPH: self._chunk_paragraphs(
                text, episode_uid, session_key
            ),
            GranularityLevel.DOCUMENT: [self._create_document_chunk(
                text, episode_uid, session_key
            )],
        }

    def _extract_keywords(
        self,
        text: str,
        episode_uid: str,
        session_key: str,
    ) -> list[GranularChunk]:
        """Extract keyword-level chunks."""
        # Simple keyword extraction (would use NER/keyphrase extraction in production)
        words = re.findall(r'\b[A-Za-z]+\b', text)

        # Filter by length and uniqueness
        keywords = set()
        for word in words:
            if len(word) >= self._config.min_keyword_length:
                keywords.add(word.lower())

        # Limit keywords
        keywords = list(keywords)[:self._config.max_keywords_per_chunk]

        chunks = []
        for i, keyword in enumerate(keywords):
            # Find position in original text
            match = re.search(rf'\b{re.escape(keyword)}\b', text, re.IGNORECASE)
            start_pos = match.start() if match else 0

            chunk = GranularChunk(
                uid=f"{episode_uid}_kw_{i}",
                content=keyword,
                granularity=GranularityLevel.KEYWORD,
                source_episode_uid=episode_uid,
                session_key=session_key,
                start_pos=start_pos,
                end_pos=start_pos + len(keyword),
                sequence_num=i,
                token_count=1,
            )
            chunks.append(chunk)

        return chunks

    def _chunk_sentences(
        self,
        text: str,
        episode_uid: str,
        session_key: str,
    ) -> list[GranularChunk]:
        """Create sentence-level chunks."""
        # Simple sentence splitting
        sentence_pattern = r'(?<=[.!?])\s+'
        sentences = re.split(sentence_pattern, text)

        chunks = []
        pos = 0

        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue

            # Check length constraints
            if (len(sentence) < self._config.sentence_min_length or
                len(sentence) > self._config.sentence_max_length):
                # Still include but note it's outside preferred range
                pass

            start_pos = text.find(sentence, pos)
            if start_pos == -1:
                start_pos = pos

            chunk = GranularChunk(
                uid=f"{episode_uid}_sent_{i}",
                content=sentence,
                granularity=GranularityLevel.SENTENCE,
                source_episode_uid=episode_uid,
                session_key=session_key,
                start_pos=start_pos,
                end_pos=start_pos + len(sentence),
                sequence_num=i,
                token_count=len(sentence.split()),
            )
            chunks.append(chunk)
            pos = start_pos + len(sentence)

        return chunks

    def _chunk_paragraphs(
        self,
        text: str,
        episode_uid: str,
        session_key: str,
    ) -> list[GranularChunk]:
        """Create paragraph-level chunks."""
        # Split by double newlines or significant whitespace
        paragraphs = re.split(r'\n\s*\n|\r\n\s*\r\n', text)

        chunks = []
        pos = 0

        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue

            # Check length constraint
            if len(para) > self._config.paragraph_max_length:
                # Split long paragraphs
                sub_paras = self._split_long_paragraph(para)
                for j, sub_para in enumerate(sub_paras):
                    chunk = GranularChunk(
                        uid=f"{episode_uid}_para_{i}_{j}",
                        content=sub_para,
                        granularity=GranularityLevel.PARAGRAPH,
                        source_episode_uid=episode_uid,
                        session_key=session_key,
                        start_pos=pos,
                        end_pos=pos + len(sub_para),
                        sequence_num=i * 100 + j,
                        token_count=len(sub_para.split()),
                    )
                    chunks.append(chunk)
                    pos += len(sub_para)
            else:
                start_pos = text.find(para, pos)
                if start_pos == -1:
                    start_pos = pos

                chunk = GranularChunk(
                    uid=f"{episode_uid}_para_{i}",
                    content=para,
                    granularity=GranularityLevel.PARAGRAPH,
                    source_episode_uid=episode_uid,
                    session_key=session_key,
                    start_pos=start_pos,
                    end_pos=start_pos + len(para),
                    sequence_num=i,
                    token_count=len(para.split()),
                )
                chunks.append(chunk)
                pos = start_pos + len(para)

        # If no paragraphs found, treat whole text as one paragraph
        if not chunks:
            chunks.append(self._create_document_chunk(
                text, episode_uid, session_key
            ))
            chunks[0].granularity = GranularityLevel.PARAGRAPH

        return chunks

    def _split_long_paragraph(self, para: str) -> list[str]:
        """Split a long paragraph into smaller chunks."""
        sentences = re.split(r'(?<=[.!?])\s+', para)
        chunks = []
        current_chunk = []
        current_length = 0

        for sentence in sentences:
            if current_length + len(sentence) > self._config.paragraph_max_length:
                if current_chunk:
                    chunks.append(' '.join(current_chunk))
                current_chunk = [sentence]
                current_length = len(sentence)
            else:
                current_chunk.append(sentence)
                current_length += len(sentence) + 1

        if current_chunk:
            chunks.append(' '.join(current_chunk))

        return chunks

    def _create_document_chunk(
        self,
        text: str,
        episode_uid: str,
        session_key: str,
    ) -> GranularChunk:
        """Create a document-level chunk (full episode)."""
        return GranularChunk(
            uid=f"{episode_uid}_doc",
            content=text,
            granularity=GranularityLevel.DOCUMENT,
            source_episode_uid=episode_uid,
            session_key=session_key,
            start_pos=0,
            end_pos=len(text),
            sequence_num=0,
            token_count=len(text.split()),
        )


class GranularityRouter:
    """Routes queries to appropriate granularity level for retrieval."""

    def __init__(
        self,
        entropy_calculator: EntropyCalculator | None = None,
        chunker: MultiGranularityChunker | None = None,
        retriever: ChunkRetriever | None = None,
        enabled: bool = True,
    ):
        """Initialize granularity router.

        Args:
            entropy_calculator: Entropy calculator for query analysis.
            chunker: Multi-granularity chunker.
            retriever: Chunk retriever backend.
            enabled: Whether routing is enabled.
        """
        self._entropy_calc = entropy_calculator or EntropyCalculator()
        self._chunker = chunker or MultiGranularityChunker()
        self._retriever = retriever
        self._enabled = enabled

        # Statistics
        self._stats = GranularityStats()

    @property
    def enabled(self) -> bool:
        """Whether routing is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable routing."""
        self._enabled = value

    @property
    def statistics(self) -> GranularityStats:
        """Get routing statistics."""
        return self._stats

    def analyze_query(self, query: str) -> RoutingDecision:
        """Analyze a query and determine optimal granularity.

        Args:
            query: Query string.

        Returns:
            RoutingDecision with recommended granularity.
        """
        entropy_score = self._entropy_calc.analyze(query)

        # Determine alternatives
        alternatives = []
        if entropy_score.recommended_granularity != GranularityLevel.KEYWORD:
            alternatives.append(GranularityLevel.KEYWORD)
        if entropy_score.recommended_granularity != GranularityLevel.SENTENCE:
            alternatives.append(GranularityLevel.SENTENCE)

        # Calculate confidence based on how close to threshold boundaries
        confidence = self._calculate_confidence(entropy_score)

        # Generate reasoning
        reasoning = self._generate_reasoning(entropy_score)

        return RoutingDecision(
            query=query,
            entropy_score=entropy_score,
            selected_granularity=entropy_score.recommended_granularity,
            confidence=confidence,
            reasoning=reasoning,
            alternatives=alternatives,
        )

    def _calculate_confidence(self, entropy_score) -> float:
        """Calculate confidence in granularity decision."""
        entropy = entropy_score.entropy

        # Higher confidence when further from thresholds
        from .data_types import ENTROPY_THRESHOLDS

        thresholds = list(ENTROPY_THRESHOLDS.values())[:-1]  # Exclude inf

        min_distance = float('inf')
        for threshold in thresholds:
            distance = abs(entropy - threshold)
            if distance < min_distance:
                min_distance = distance

        # Normalize confidence (closer to threshold = lower confidence)
        confidence = min(1.0, min_distance / 1.0)  # 1.0 entropy unit = max confidence

        return max(0.5, confidence)  # Minimum 50% confidence

    def _generate_reasoning(self, entropy_score) -> str:
        """Generate human-readable reasoning for decision."""
        granularity = entropy_score.recommended_granularity
        entropy = entropy_score.entropy
        tokens = entropy_score.token_count

        if granularity == GranularityLevel.KEYWORD:
            return (
                f"Simple query ({tokens} tokens, entropy {entropy:.2f}). "
                f"Using keyword-level retrieval for precise matching."
            )
        elif granularity == GranularityLevel.SENTENCE:
            return (
                f"Moderate query ({tokens} tokens, entropy {entropy:.2f}). "
                f"Using sentence-level retrieval for balanced context."
            )
        elif granularity == GranularityLevel.PARAGRAPH:
            return (
                f"Complex query ({tokens} tokens, entropy {entropy:.2f}). "
                f"Using paragraph-level retrieval for broader context."
            )
        else:
            return (
                f"Highly complex query ({tokens} tokens, entropy {entropy:.2f}). "
                f"Using document-level retrieval for comprehensive context."
            )

    def chunk_episode(
        self,
        text: str,
        episode_uid: str,
        session_key: str,
    ) -> dict[GranularityLevel, list[GranularChunk]]:
        """Chunk an episode at all granularity levels.

        Args:
            text: Episode text.
            episode_uid: Episode UID.
            session_key: Session key.

        Returns:
            Dictionary of chunks by granularity.
        """
        return self._chunker.chunk_text(text, episode_uid, session_key)

    async def retrieve(
        self,
        query: str,
        session_key: str,
        limit: int = 10,
        granularity_override: GranularityLevel | None = None,
    ) -> RetrievalResult:
        """Perform granularity-aware retrieval.

        Args:
            query: Query string.
            session_key: Session key.
            limit: Maximum chunks to retrieve.
            granularity_override: Force specific granularity.

        Returns:
            RetrievalResult with chunks and metadata.
        """
        start_time = time.time()

        # Analyze query
        routing_start = time.time()
        decision = self.analyze_query(query)
        routing_time = (time.time() - routing_start) * 1000

        # Override if specified
        if granularity_override is not None:
            decision.selected_granularity = granularity_override

        # Retrieve if retriever available
        chunks = []
        scores = []
        retrieval_time = 0.0

        if self._retriever is not None:
            retrieval_start = time.time()
            chunks, scores = await self._retriever.retrieve_chunks(
                query, session_key, decision.selected_granularity, limit
            )
            retrieval_time = (time.time() - retrieval_start) * 1000

        total_time = (time.time() - start_time) * 1000

        # Update statistics
        self._stats.record_query(
            decision.selected_granularity,
            decision.entropy_score.entropy,
            total_time,
        )

        return RetrievalResult(
            query=query,
            routing_decision=decision,
            chunks=chunks,
            scores=scores,
            routing_time_ms=routing_time,
            retrieval_time_ms=retrieval_time,
            total_time_ms=total_time,
        )

    def set_retriever(self, retriever: ChunkRetriever) -> None:
        """Set the chunk retriever.

        Args:
            retriever: Chunk retriever implementation.
        """
        self._retriever = retriever
