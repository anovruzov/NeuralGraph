"""Holographic Memory - 4D Memory Architecture.

THE EVOLUTION FROM 3D TO 4D:

3D (Broken):
- Nodes in positional space
- Linear edge traversal
- Query → Search → Find → Return
- Speaker metadata separate from content

4D (Brain-like):
- Memories in CONCEPTUAL space
- Any concept directly accesses memory
- Query concepts → Intersection → Memory emerges
- ALL context is unified in access keys

The key insight: In the brain, a memory is not STORED at a location.
A memory IS the pattern of activation across concepts.
"Caroline + LGBTQ + support + group + yesterday" = one unified trace.

Any of these concepts can trigger pattern completion to retrieve the full memory.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .data_types import NeuralNode

logger = logging.getLogger(__name__)


# Common words that don't help with memory access
STOP_CONCEPTS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'to', 'of',
    'and', 'or', 'but', 'if', 'then', 'so', 'for', 'with', 'at', 'by',
    'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over', 'under',
    'again', 'further', 'then', 'once', 'here', 'there', 'all', 'each',
    'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
    'only', 'own', 'same', 'than', 'too', 'very', 'just', 'also',
    'now', 'its', 'it', 'this', 'that', 'these', 'those',
    'i', 'you', 'he', 'she', 'we', 'they', 'me', 'him', 'her', 'us', 'them',
    'my', 'your', 'his', 'our', 'their', 'what', 'which', 'who', 'whom',
    'im', "i'm", "it's", "that's", "what's", "there's", 'youre', "you're",
    'really', 'just', 'like', 'know', 'think', 'want', 'going', 'got',
    'get', 'go', 'come', 'came', 'been', 'being', 'having', 'had',
}


@dataclass
class HolographicTrace:
    """A 4D memory trace - accessible from any concept dimension.

    Unlike a 3D node which must be FOUND, a holographic trace
    EMERGES when enough concepts resonate together.
    """
    # Unique identifier
    trace_id: str

    # The full memory content (unified - includes speaker context)
    content: str

    # ALL concepts that can access this memory (the 4D coordinates)
    access_keys: set[str] = field(default_factory=set)

    # Speaker as a first-class access key (not metadata!)
    speaker: str = ""

    # Temporal coordinate (4th dimension - not metadata!)
    timestamp: datetime | None = None

    # Original embedding for semantic similarity fallback
    embedding: list[float] | None = None

    # Reference to underlying node (for compatibility)
    node_id: str = ""

    def __post_init__(self):
        # Ensure speaker is in access keys
        if self.speaker:
            self.access_keys.add(self.speaker.lower())


class HolographicMemoryIndex:
    """4D Memory Index - concept-based access to memory traces.

    This is NOT a search index. This is a RESONANCE chamber.

    When query concepts enter, they activate all traces that
    share those concepts. The trace with highest overlap
    EMERGES as the retrieved memory.
    """

    def __init__(self):
        # All traces
        self._traces: dict[str, HolographicTrace] = {}

        # Inverted index: concept → trace_ids
        # This enables O(1) concept-based access
        self._concept_index: dict[str, set[str]] = {}

    def ingest(
        self,
        node: NeuralNode,
        speaker: str = "",
    ) -> HolographicTrace:
        """Ingest a node into the holographic memory.

        This transforms a 3D node into a 4D trace by:
        1. Extracting ALL concepts from content
        2. Adding speaker as a first-class concept
        3. Indexing by ALL concepts for O(1) access
        """
        # Extract concepts from content
        concepts = self._extract_concepts(node.content)

        # Get speaker from node metadata or parameter
        if not speaker and node.metadata:
            speaker = node.metadata.get("producer_id", node.metadata.get("speaker", ""))

        # Add speaker as concept (THE 4D FIX!)
        if speaker:
            concepts.add(speaker.lower())

        # Create unified content with speaker context
        unified_content = f"{speaker}: {node.content}" if speaker else node.content

        # Create trace
        trace = HolographicTrace(
            trace_id=node.node_id,
            content=unified_content,
            access_keys=concepts,
            speaker=speaker.lower() if speaker else "",
            timestamp=node.created_at,
            embedding=node.embedding,
            node_id=node.node_id,
        )

        # Store trace
        self._traces[trace.trace_id] = trace

        # Index by ALL concepts
        for concept in concepts:
            if concept not in self._concept_index:
                self._concept_index[concept] = set()
            self._concept_index[concept].add(trace.trace_id)

        return trace

    def retrieve(
        self,
        query: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[HolographicTrace, float]]:
        """Retrieve memories through concept resonance.

        This is NOT search. This is pattern completion.

        Query concepts activate traces. The trace with
        highest concept overlap EMERGES as the answer.
        """
        # Extract query concepts
        query_concepts = self._extract_concepts(query)

        if not query_concepts:
            return []

        # Find all traces that share ANY concept with query
        candidate_ids: set[str] = set()
        for concept in query_concepts:
            if concept in self._concept_index:
                candidate_ids.update(self._concept_index[concept])

        if not candidate_ids:
            # Fallback to all traces if no concept matches
            candidate_ids = set(self._traces.keys())

        # Score by concept intersection (RESONANCE)
        scored: list[tuple[HolographicTrace, float]] = []

        for trace_id in candidate_ids:
            trace = self._traces[trace_id]

            # Primary score: concept intersection ratio
            intersection = query_concepts & trace.access_keys
            intersection_score = len(intersection) / len(query_concepts)

            # Secondary score: embedding similarity (fallback)
            embedding_score = 0.0
            if query_embedding and trace.embedding:
                embedding_score = self._cosine_similarity(query_embedding, trace.embedding)

            # Combined score: 70% intersection, 30% embedding
            # Intersection is PRIMARY because it's the 4D access mechanism
            final_score = 0.7 * intersection_score + 0.3 * max(0, embedding_score)

            scored.append((trace, final_score))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:limit]

    def _extract_concepts(self, text: str) -> set[str]:
        """Extract all concepts from text.

        Concepts are the 4D coordinates of memory.
        Any concept can access memories that share it.
        """
        # Lowercase and extract words
        text_lower = text.lower()
        words = re.findall(r'\b[a-z]+\b', text_lower)

        # Filter to meaningful concepts
        concepts = {
            w for w in words
            if w not in STOP_CONCEPTS and len(w) > 2
        }

        # Also extract multi-word concepts (bigrams for compound terms)
        # e.g., "support group" should be a single concept
        words_list = [w for w in words if w not in STOP_CONCEPTS and len(w) > 2]
        for i in range(len(words_list) - 1):
            bigram = f"{words_list[i]}_{words_list[i+1]}"
            concepts.add(bigram)

        return concepts

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)


class HolographicRetriever:
    """4D Retriever using holographic memory.

    This replaces the 3D graph-based retrieval with
    true 4D concept-based pattern completion.
    """

    def __init__(self):
        self._index = HolographicMemoryIndex()

    def ingest_node(self, node: NeuralNode, speaker: str = "") -> HolographicTrace:
        """Ingest a node into holographic memory."""
        return self._index.ingest(node, speaker)

    async def retrieve(
        self,
        query: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[HolographicTrace, float]]:
        """Retrieve through concept resonance."""
        return self._index.retrieve(query, limit, query_embedding)

    def get_trace_count(self) -> int:
        """Get number of traces in memory."""
        return len(self._index._traces)

    def get_concept_count(self) -> int:
        """Get number of unique concepts indexed."""
        return len(self._index._concept_index)
