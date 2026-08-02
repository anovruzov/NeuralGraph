"""Leakage-free helpers shared by NeuralGraph benchmark harnesses.

These functions deliberately operate only on the question and conversation
memory. Dataset labels, gold answers, and evidence IDs are reserved for
evaluation after retrieval and answer generation have completed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data_types import NeuralNode


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "about", "after", "all", "an", "and", "are", "as", "at",
    "be", "been", "before", "both", "by", "did", "do", "does", "for",
    "from", "had", "has", "have", "he", "her", "his", "how", "i", "in",
    "is", "it", "its", "me", "my", "of", "on", "or", "our", "she",
    "that", "the", "their", "them", "they", "this", "to", "was", "were",
    "what", "when", "where", "which", "who", "why", "with", "would",
    "you", "your",
}
_ABSTENTION_PATTERNS = (
    "not found",
    "not mentioned",
    "not in the memories",
    "no information",
    "insufficient information",
    "cannot determine",
    "can't determine",
    "unknown",
)


def _caption_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return ""


def compose_memory_content(message: dict[str, Any]) -> str:
    """Combine dialogue text with the dataset-provided image caption.

    LoCoMo is multimodal. Ignoring ``blip_caption`` drops evidence that the
    benchmark explicitly makes available to memory systems. Image-search
    queries and evaluator summaries are intentionally excluded.
    """

    text = str(message.get("text", "")).strip()
    caption = _caption_text(message.get("blip_caption"))
    if not caption or caption.lower() in text.lower():
        return text
    if not text:
        return f"[IMAGE] {caption}"
    return f"{text}\n[IMAGE] {caption}"


def _stem(token: str) -> str:
    """Return a conservative English retrieval stem without extra deps."""

    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        root = token[:-3]
        if len(root) > 2 and root[-1] == root[-2]:
            root = root[:-1]
        return root
    if len(token) > 4 and token.endswith("ed"):
        root = token[:-2]
        if len(root) > 2 and root[-1] == root[-2]:
            root = root[:-1]
        return root
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def retrieval_tokens(text: str) -> set[str]:
    return {
        _stem(token)
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 2
    }


def extract_target_speakers(question: str, speakers: Iterable[str]) -> list[str]:
    """Return speaker names explicitly named in a question."""

    targets: list[str] = []
    for speaker in sorted({s.strip() for s in speakers if s.strip()}, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(speaker)}(?:'s)?(?!\w)", question, re.IGNORECASE):
            targets.append(speaker)
    return targets


def rank_nodes_lexically(
    question: str,
    nodes: Sequence["NeuralNode"],
    speakers: Iterable[str] = (),
    limit: int = 80,
) -> list[tuple["NeuralNode", float]]:
    """High-recall BM25-like ranker with explicit speaker attribution.

    This is a complement to embedding/graph retrieval. It is especially
    useful for rare names, image-caption facts, exact event nouns, and entity
    swaps that otherwise create adversarial false positives.
    """

    if not nodes or limit <= 0:
        return []

    query_tokens = retrieval_tokens(question)
    targets = {speaker.lower() for speaker in extract_target_speakers(question, speakers)}
    documents = [retrieval_tokens(node.content) for node in nodes]
    doc_count = len(documents)
    doc_freq: dict[str, int] = {}
    for tokens in documents:
        for token in tokens:
            doc_freq[token] = doc_freq.get(token, 0) + 1

    scored: list[tuple["NeuralNode", float]] = []
    for node, doc_tokens in zip(nodes, documents):
        overlap = query_tokens & doc_tokens
        lexical = sum(
            math.log((doc_count + 1) / (doc_freq.get(token, 0) + 1)) + 1.0
            for token in overlap
        )
        lexical /= max(1.0, math.sqrt(len(query_tokens) or 1))

        speaker = node.speaker_id.strip().lower()
        speaker_bonus = 0.0
        if targets:
            speaker_bonus = 2.5 if speaker in targets else -0.35

        # Exact multiword question fragments are rare and highly diagnostic.
        content_lower = node.content.lower()
        phrase_bonus = 0.0
        meaningful = sorted(query_tokens, key=len, reverse=True)[:6]
        if sum(1 for token in meaningful if token in content_lower) >= 3:
            phrase_bonus = 0.75

        score = lexical + speaker_bonus + phrase_bonus
        if score > 0:
            scored.append((node, score))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def fuse_ranked_candidates(
    graph_results: Sequence[tuple["NeuralNode", float]],
    lexical_results: Sequence[tuple["NeuralNode", float]],
    limit: int = 80,
    rrf_k: int = 50,
) -> list[tuple["NeuralNode", float]]:
    """Fuse graph and lexical rankings using weighted reciprocal rank."""

    combined: dict[str, tuple["NeuralNode", float]] = {}
    for weight, ranking in ((1.0, graph_results), (0.9, lexical_results)):
        for rank, (node, _score) in enumerate(ranking, 1):
            contribution = weight / (rrf_k + rank)
            existing = combined.get(node.node_id)
            combined[node.node_id] = (
                node,
                contribution + (existing[1] if existing else 0.0),
            )

    fused = sorted(combined.values(), key=lambda item: item[1], reverse=True)
    if not fused:
        return []
    max_score = fused[0][1]
    return [(node, score / max_score) for node, score in fused[:limit]]


def check_evidence_recall(
    evidence_ids: Iterable[str],
    memories: Sequence[dict[str, Any]],
    top_k: int,
) -> tuple[bool, int]:
    """Evaluate recall using LoCoMo evidence IDs, never answer text."""

    expected = {str(item) for item in evidence_ids if str(item)}
    if not expected:
        return False, 0
    for rank, memory in enumerate(memories[:top_k], 1):
        if str(memory.get("dia_id", "")) in expected:
            return True, rank
    return False, 0


def is_abstention(answer: Any) -> bool:
    normalized = str(answer or "").strip().lower().replace("_", " ")
    return any(pattern in normalized for pattern in _ABSTENTION_PATTERNS)
