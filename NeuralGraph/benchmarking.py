"""Leakage-free helpers shared by NeuralGraph benchmark harnesses.

These functions deliberately operate only on the question and conversation
memory. Dataset labels, gold answers, and evidence IDs are reserved for
evaluation after retrieval and answer generation have completed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class EvidenceCoverage:
    """Evidence-ID coverage for one ranked memory list.

    ``any_found`` is the traditional hit/recall flag used by the old harness.
    It is useful for single-evidence questions but overstates retrieval quality
    whenever an answer requires several memories. ``all_found`` and
    ``coverage`` expose that distinction explicitly.
    """

    expected_count: int
    found_count: int
    any_found: bool
    all_found: bool
    coverage: float
    first_rank: int
    last_rank: int
    found_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]


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
    tokens = {
        _stem(token)
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 2
    }

    # Attribute questions often use a schema label while the supporting
    # dialogue uses a value or life event ("relationship status" vs
    # "single parent" / "breakup"). Expand both sides with small, general
    # concept bridges so lexical retrieval can connect them without knowing an
    # evaluator answer.
    relationship_schema = {"relationship", "status"}
    relationship_values = {
        "single", "marry", "married", "dating", "engage", "engaged",
        "divorce", "divorced", "partner", "breakup", "widow", "widowed",
    }
    if tokens & relationship_schema:
        tokens.update(relationship_values)
    if tokens & relationship_values:
        tokens.update(relationship_schema)

    identity_schema = {"identity", "gender"}
    identity_values = {"trans", "transgender", "nonbinary", "queer"}
    if tokens & identity_schema:
        tokens.update(identity_values)
    if tokens & identity_values:
        tokens.update(identity_schema)

    return tokens


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
    """Return legacy any-evidence recall and the first matching rank.

    New benchmark code should use :func:`measure_evidence_coverage` so a
    partial multi-memory hit cannot be mistaken for complete recall.
    """

    measured = measure_evidence_coverage(evidence_ids, memories, top_k)
    return measured.any_found, measured.first_rank


def measure_evidence_coverage(
    evidence_ids: Iterable[str],
    memories: Sequence[dict[str, Any]],
    top_k: int,
) -> EvidenceCoverage:
    """Measure any-hit, complete-hit, and fractional evidence coverage.

    The function is evaluation-only: it compares ranked ``dia_id`` values to
    LoCoMo annotations and never inspects answer text.
    """

    expected = {str(item) for item in evidence_ids if str(item)}
    if not expected:
        return EvidenceCoverage(
            expected_count=0,
            found_count=0,
            any_found=False,
            all_found=False,
            coverage=0.0,
            first_rank=0,
            last_rank=0,
            found_ids=(),
            missing_ids=(),
        )

    ranks: dict[str, int] = {}
    for rank, memory in enumerate(memories[:top_k], 1):
        dia_id = str(memory.get("dia_id", ""))
        if dia_id in expected and dia_id not in ranks:
            ranks[dia_id] = rank

    found = tuple(sorted(ranks, key=ranks.get))
    missing = tuple(sorted(expected - set(ranks)))
    found_count = len(found)
    rank_values = tuple(ranks.values())
    return EvidenceCoverage(
        expected_count=len(expected),
        found_count=found_count,
        any_found=found_count > 0,
        all_found=found_count == len(expected),
        coverage=found_count / len(expected),
        first_rank=min(rank_values, default=0),
        last_rank=max(rank_values, default=0),
        found_ids=found,
        missing_ids=missing,
    )


def is_abstention(answer: Any) -> bool:
    normalized = str(answer or "").strip().lower().replace("_", " ")
    return any(pattern in normalized for pattern in _ABSTENTION_PATTERNS)
