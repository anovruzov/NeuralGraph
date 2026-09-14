"""Memory engine for the MCP server: create memories that stay useful.

Design goals, each pinned by a test in ``test_mcp_memory.py``:

- **Works offline.** Embeddings come from a local model when one is reachable
  (``NEURALGRAPH_EMBED=model``) and from a deterministic hashed bag-of-words
  otherwise.  Every node records ``embedding_source`` so vectors from
  different spaces are never compared; BM25 and entity overlap work across all.
- **Creation is idempotent.** Exact duplicates (same normalized text in a
  session) and near-duplicates (cosine >= 0.97 in the same embedding space)
  return the existing memory and strengthen it instead of cloning it.
- **Facts can change without losing history.** A memory created with a
  ``key`` supersedes the previous memory with that key: the old one is
  archived with ``valid_to`` and ``superseded_by``, the new one carries the
  old id in ``source_memory_ids`` and a HIERARCHY edge new -> old (derivation
  parents are the *targets* of outgoing HIERARCHY edges, the direction the
  coordination layer's lineage resolver reads).  A differing value is
  recorded as a contradiction on the new memory.
- **Provenance is never dropped.** ``forget`` is soft (state EVICTED, reason
  recorded); ``decay`` archives cold memories, never deletes.  Failure domains
  are recorded only when the operator supplies one; nothing infers them.
- **Recall explains itself.** Vector, keyword, entity and recency ranks are
  fused by reciprocal rank fusion and returned per hit when asked.
- **Used knowledge strengthens.** Recalled memories gain heat; cold, low
  importance memories lose it and are archived by ``decay``.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from rank_bm25 import BM25Okapi

from ..data_types import (
    ConsolidationState,
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
    cosine_similarity,
    generate_edge_id,
    generate_node_id,
)
from ..mega_search import entities_of, rrf
from ..sqlite_storage import SQLiteNeuralGraphStorage
from . import lexicon


class Features:
    """Recall preprocessing switches, each measurable on its own (evaluation.py)."""

    def __init__(self, spelling: bool = True, thesaurus: bool = True, names: bool = True) -> None:
        self.spelling = spelling
        self.thesaurus = thesaurus
        self.names = names

    def as_dict(self) -> dict[str, bool]:
        return {"spelling": self.spelling, "thesaurus": self.thesaurus, "names": self.names}

MAX_TEXT_CHARS = 8000
#: Words that flip meaning; never dropped from fingerprints or embeddings.
NEGATIONS = {"not", "no", "never", "none", "without", "non"}
#: Kept in the dedupe fingerprint only: they flip meaning ("on"/"off") but are
#: far too common to rank by.
FINGERPRINT_ONLY = {"on", "off"}
#: How a memory's own key contributes to recall (chosen by evaluation.py sweep).
KEY_MATCH_MODE = "last"
KEY_WEIGHT = 0.5
_WORD = re.compile(r"\w+", re.UNICODE)
DEFAULT_DB = "~/.neuralgraph/memory.db"
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_HANDLE = re.compile(r"(?<![\w.])@([A-Za-z0-9_][A-Za-z0-9_.-]{1,38})")
_PATH = re.compile(r"(?<![\w])((?:[A-Za-z]:)?(?:/[\w.\-]+){2,}|[\w.\-]+/[\w.\-/]+\.[a-z]{1,5})")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_time(value: str | None, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> list[str]:
    """Unicode-aware tokens for embedding and keyword ranking.

    Keeps numbers, short tokens and negation words: '09:30' vs '10:30' and
    'enabled' vs 'not enabled' must produce different tokens, or dedupe and
    recall silently merge facts that differ.  Only pure stop words are dropped.
    """
    from ..mega_search import _STOP

    return [w for w in _WORD.findall(text.lower()) if w in NEGATIONS or (w not in _STOP and len(w) >= 2)]


def fingerprint(text: str) -> tuple[str, ...]:
    """Lexical identity of a memory: the sorted multiset of its content tokens.

    Two texts are near-duplicates only when every token matches; punctuation,
    case and spacing differences collapse, any changed word or number does not.
    """
    extra = [w for w in _WORD.findall(text.lower()) if w in FINGERPRINT_ONLY]
    return tuple(sorted(content_tokens(text) + extra))


class Embedder(Protocol):
    name: str

    async def embed(self, text: str) -> list[float]: ...


class HashedEmbedder:
    """Deterministic feature-hashed bag of words and bigrams, L2-normalized.

    No model, no network.  Captures lexical overlap only; the name is stored on
    every node so a later model embedding is never compared against it.
    """

    name = "hashed-bow-v1"

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims

    async def embed(self, text: str) -> list[float]:
        return self.embed_sync(text)

    def embed_sync(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        tokens = content_tokens(text)
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dims
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class ModelEmbedder:
    """Embeddings from the configured local model (Ollama or OpenAI-compatible)."""

    def __init__(self, model: str | None = None, base_url: str | None = None, timeout_seconds: float = 20.0) -> None:
        from ..llm_backend import EMBED_MODEL, LLM_BASE_URL

        self.model = model or EMBED_MODEL
        self.base_url = base_url or LLM_BASE_URL
        self.timeout_seconds = timeout_seconds
        self.name = f"model:{self.model}"

    async def embed(self, text: str) -> list[float]:
        import aiohttp

        from ..llm_backend import llm_embed

        async with aiohttp.ClientSession() as session:
            vector = await llm_embed(session, text, model=self.model, base_url=self.base_url, timeout_seconds=self.timeout_seconds)
        if not vector:
            raise RuntimeError("embedding model returned an empty vector")
        return [float(v) for v in vector]


async def choose_embedder(mode: str = "auto", existing_source: str | None = None) -> Embedder:
    """``hashed`` never touches the network; ``model`` requires one; ``auto`` probes once.

    In ``auto`` mode a store that already holds memories keeps their space:
    if they were hashed, no probe is made and no model is used, so the choice
    is deterministic across restarts.  If they came from a model, that model
    is tried first and hashed is the fallback.
    """
    if mode == "hashed":
        return HashedEmbedder()
    model_name = existing_source[6:] if existing_source and existing_source.startswith("model:") else None
    model = ModelEmbedder(model=model_name, timeout_seconds=3.0)
    if mode == "model":
        await model.embed("probe")
        return model
    if existing_source == HashedEmbedder.name:
        return HashedEmbedder()
    try:
        await model.embed("probe")
        return model
    except Exception:
        return HashedEmbedder()


_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)([A-Z][a-zA-Z]+)")
_COMMON_STARTERS = {
    "the", "what", "this", "these", "those", "after", "before", "use", "using", "add", "run", "note", "todo",
    "remember", "always", "never", "every", "each", "please", "when", "where", "while", "if", "it", "its", "we",
    "our", "you", "your", "they", "there", "here", "also", "make", "keep", "set", "do", "don", "let", "check",
}


def extract_entities(text: str, explicit: tuple[str, ...] = ()) -> tuple[str, ...]:
    from ..mega_search import _STOP

    found = set(entities_of(text))
    # A sentence-initial capitalised word is a name unless it is a common
    # function word ('The', 'What', 'After'): those are dropped, 'Ali' stays.
    starts = {w.lower() for w in _SENTENCE_START.findall(text)}
    found -= {w for w in starts if w in _STOP or w in _COMMON_STARTERS}
    found -= _STOP
    found.update(h.lower() for h in _HANDLE.findall(text))
    found.update(p for p in _PATH.findall(text))
    found.update(e.strip().lower() for e in explicit if e and e.strip())
    return tuple(sorted(found))


class MemoryEngine:
    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB,
        session_key: str = "default",
        embedder: Embedder | None = None,
        clock: Callable[[], datetime] = _now,
        failure_domain: str | None = None,
        storage: SQLiteNeuralGraphStorage | None = None,
        features: Features | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.session_key = session_key
        self.features = features or Features()
        # Tests and pinned demos pass a deterministic factory; production uses uuid4.
        self._new_id = id_factory or generate_node_id
        self.embedder: Embedder = embedder or HashedEmbedder()
        self._clock = clock
        # Recorded only when the operator names it; never inferred from the
        # database path, host, or anything else (CLAUDE.md rule 5).
        self.failure_domain = failure_domain or os.environ.get("NEURALGRAPH_FAILURE_DOMAIN") or None
        self.storage = storage or SQLiteNeuralGraphStorage(db_path)
        self._db_path = getattr(self.storage, "_db_path", None)
        # One writer at a time: the check-then-write in remember() must not race.
        self._lock = asyncio.Lock()
        self._cache: list[NeuralNode] | None = None
        self._cache_stamp: tuple[int, int] | None = None
        # Private memories are never sent to a model endpoint.
        self._private_embedder = HashedEmbedder()

    # ------------------------------------------------------------------ create
    async def remember(
        self,
        text: str,
        *,
        kind: str = "note",
        key: str | None = None,
        tags: tuple[str, ...] | list[str] = (),
        speaker: str | None = None,
        source: str | None = None,
        when: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        importance: float = 0.5,
        entities: tuple[str, ...] | list[str] = (),
        derived_from: tuple[str, ...] | list[str] = (),
        private: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            return await self._remember(
                text, kind=kind, key=key, tags=tags, speaker=speaker, source=source, when=when,
                valid_from=valid_from, valid_to=valid_to, importance=importance, entities=entities,
                derived_from=derived_from, private=private,
            )

    async def _remember(
        self,
        text: str,
        *,
        kind: str,
        key: str | None,
        tags: tuple[str, ...] | list[str],
        speaker: str | None,
        source: str | None,
        when: str | None,
        valid_from: str | None,
        valid_to: str | None,
        importance: float,
        entities: tuple[str, ...] | list[str],
        derived_from: tuple[str, ...] | list[str],
        private: bool,
    ) -> dict[str, Any]:
        content = normalize_text(text or "")
        if not content:
            raise ValueError("text must not be empty")
        if len(content) > MAX_TEXT_CHARS:
            raise ValueError(f"text longer than {MAX_TEXT_CHARS} characters; split it into separate memories")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        kind = (kind or "note").strip().lower()
        key = key.strip() if isinstance(key, str) and key.strip() else None
        tags = tuple(sorted({t.strip().lower() for t in tags if t and t.strip()}))
        event_time = _parse_time(when, "when")
        valid_from_dt = _parse_time(valid_from, "valid_from")
        valid_to_dt = _parse_time(valid_to, "valid_to")
        if valid_from_dt and valid_to_dt and valid_to_dt < valid_from_dt:
            raise ValueError("valid_to must not precede valid_from")
        now = self._clock()
        content_hash = hashlib.sha256(f"{self.session_key}\n{content.lower()}".encode("utf-8")).hexdigest()

        nodes = await self._session_nodes()
        # Duplicate = same lexical fingerprint (every token equal; numbers and
        # negations count) in the same kind.  A duplicate strengthens the
        # existing memory and merges tags/source; it never clones.  A key on the
        # new statement is adopted by an unkeyed existing memory; a different
        # key is a different fact.  A private re-statement upgrades the
        # existing memory to private rather than leaving the text exposed.
        new_fp = fingerprint(content)
        for node in nodes:
            if node.consolidation_state is not ConsolidationState.ACTIVE or node.metadata.get("kind") != kind:
                continue
            same_text = node.metadata.get("content_hash") == content_hash or tuple(node.metadata.get("fingerprint", ())) == new_fp
            existing_key = node.metadata.get("key")
            if not same_text or (key and existing_key and existing_key != key):
                continue
            if key and not existing_key:
                node.metadata["key"] = key
            node.metadata["tags"] = sorted(set(node.metadata.get("tags", [])) | set(tags))
            if source and not node.metadata.get("source"):
                node.metadata["source"] = source
            if private and not node.metadata.get("private"):
                node.metadata["private"] = True
                if node.metadata.get("embedding_source") != HashedEmbedder.name:
                    node.embedding = await self._private_embedder.embed(content)
                    node.metadata["embedding_source"] = HashedEmbedder.name
            node.heat_score = min(5.0, node.heat_score + 0.25)
            node.updated_at = now
            await self.storage.update_node(node)
            self._invalidate()
            return self._summary(node, deduplicated=True)

        for parent in derived_from:
            parent_node = await self.storage.get_node(parent)
            if parent_node is None or parent_node.session_key != self.session_key:
                raise ValueError(f"derived_from names an unknown memory: {parent}")

        embedder = self._private_embedder if private else self.embedder
        embedding, embedding_source = await self._embed(embedder, content)

        name_ids = lexicon.names(content, tuple(entities)) if self.features.names else ()
        name_candidates = lexicon.sentence_initial_candidates(content) if self.features.names else ()
        entity_ids = tuple(sorted(set(extract_entities(content, tuple(entities))) | set(name_ids)))
        superseded: NeuralNode | None = None
        if key:
            superseded = next(
                (n for n in nodes if n.metadata.get("key") == key and n.consolidation_state is ConsolidationState.ACTIVE),
                None,
            )
        metadata: dict[str, Any] = {
            "kind": kind,
            "key": key,
            "tags": list(tags),
            "speaker": speaker,
            "source": source,
            "private": bool(private),
            "content_hash": content_hash,
            "fingerprint": list(new_fp),
            "embedding_source": embedding_source,
            "event_time": _iso(event_time),
            # A superseding fact starts being valid now unless told otherwise,
            # so as_of before it existed does not return it.
            "valid_from": _iso(valid_from_dt or (now if superseded is not None else None)),
            "valid_to": _iso(valid_to_dt),
            "mentioned_dates": sorted(set(_ISO_DATE.findall(content))),
            "names": list(name_ids),
            "name_candidates": list(name_candidates),
            "created_by": "neuralgraph-mcp",
        }
        if self.failure_domain:
            metadata["failure_domain"] = self.failure_domain
        node = NeuralNode(
            node_id=self._new_id(),
            layer=NodeLayer.MESSAGE,
            content=content,
            embedding=embedding,
            importance_score=importance,
            heat_score=0.3 + 0.4 * importance,
            entity_ids=list(entity_ids),
            session_key=self.session_key,
            created_at=now,
            updated_at=now,
            last_activated=now,
            source_memory_ids=list(dict.fromkeys(list(derived_from) + ([superseded.node_id] if superseded else []))),
            metadata=metadata,
        )
        if superseded is not None:
            node.metadata["supersedes"] = superseded.node_id
            if superseded.content != content:
                node.metadata["contradicts"] = {"memory_id": superseded.node_id, "previous": superseded.content}
        await self.storage.save_node(node)

        # derivation edges: new -> parent (parents are targets of outgoing HIERARCHY edges)
        for parent_id in node.source_memory_ids:
            await self.storage.save_edge(NeuralEdge(
                edge_id=generate_edge_id(), source_id=node.node_id, target_id=parent_id,
                edge_type=EdgeType.HIERARCHY, base_weight=1.0, confidence=1.0,
            ))
        if superseded is not None:
            superseded.consolidation_state = ConsolidationState.ARCHIVED
            superseded.metadata["valid_to"] = superseded.metadata.get("valid_to") or _iso(now)
            superseded.metadata["superseded_by"] = node.node_id
            superseded.updated_at = now
            await self.storage.update_node(superseded)
        # entity edges to the most related recent memories
        related = []
        for other in nodes:
            if other.node_id == node.node_id or other.consolidation_state is not ConsolidationState.ACTIVE:
                continue
            shared = set(other.entity_ids) & set(entity_ids)
            if shared:
                union = set(other.entity_ids) | set(entity_ids)
                related.append((len(shared) / len(union), other))
        # strongest overlap first, then most recent
        related.sort(key=lambda item: (-item[0], -item[1].created_at.timestamp()))
        for weight, other in related[:5]:
            await self.storage.save_edge(NeuralEdge(
                edge_id=generate_edge_id(), source_id=node.node_id, target_id=other.node_id,
                edge_type=EdgeType.ENTITY, base_weight=round(weight, 6), confidence=round(weight, 6),
            ))
        # temporal chain to the previous memory in this session
        previous = max((n for n in nodes if n.node_id != node.node_id), key=lambda n: n.created_at, default=None)
        if previous is not None:
            await self.storage.save_edge(NeuralEdge(
                edge_id=generate_edge_id(), source_id=previous.node_id, target_id=node.node_id,
                edge_type=EdgeType.TEMPORAL, base_weight=0.5, confidence=1.0,
            ))
        self._invalidate()
        return self._summary(node, deduplicated=False, superseded=superseded.node_id if superseded else None)

    # ------------------------------------------------------------------ recall
    async def recall(
        self,
        query: str,
        *,
        limit: int = 8,
        kind: str | None = None,
        tags: tuple[str, ...] | list[str] = (),
        as_of: str | None = None,
        include_superseded: bool = False,
        explain: bool = False,
        strengthen: bool = True,
        min_confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        query = normalize_text(query or "")
        if not query:
            raise ValueError("query must not be empty")
        limit = max(1, min(int(limit), 50))
        as_of_dt = _parse_time(as_of, "as_of")
        wanted_tags = {t.strip().lower() for t in tags if t and t.strip()}
        candidates = []
        for node in await self._session_nodes():
            state = node.consolidation_state
            if state is ConsolidationState.EVICTED:
                continue
            if state is ConsolidationState.ARCHIVED and not include_superseded:
                continue
            if kind and node.metadata.get("kind") != kind.strip().lower():
                continue
            if wanted_tags and not wanted_tags <= set(node.metadata.get("tags", [])):
                continue
            if as_of_dt and not self._valid_at(node, as_of_dt):
                continue
            candidates.append(node)
        if not candidates:
            return []
        by_id = {n.node_id: n for n in candidates}
        corpus = [content_tokens(n.content) for n in candidates]
        vocab: dict[str, int] = {}
        for doc in corpus:
            for w in doc:
                vocab[w] = vocab.get(w, 0) + 1
        corpus_names = {name for n in candidates for name in n.metadata.get("names", [])}
        # A sentence-initial capitalised word ("Ali prefers ...") counts as a
        # name once the session has seen it as a name anywhere else.
        effective_names = {
            n.node_id: set(n.metadata.get("names", [])) | (set(n.metadata.get("name_candidates", [])) & corpus_names)
            for n in candidates
        }
        corrections: dict[str, str] = {}
        raw_tokens = content_tokens(query)
        if self.features.spelling and raw_tokens:
            # correct against the corpus first (jargon and names are valid), never a capitalised word
            original_case = {w.lower(): w for w in lexicon.query_words(query)}
            q_tokens, corrections = lexicon.correct_tokens(
                [original_case.get(w, w) for w in raw_tokens], vocab, protected=corpus_names,
            )
        else:
            q_tokens = raw_tokens
        expansions = lexicon.expand(q_tokens, set(vocab)) if self.features.thesaurus and q_tokens else {}
        vector_query = " ".join(q_tokens) if corrections else query
        query_vec, query_source = await self._embed(self.embedder, vector_query)
        vector_rank = [
            n.node_id for n, _ in sorted(
                ((n, cosine_similarity(query_vec, n.embedding)) for n in candidates
                 if n.embedding and n.metadata.get("embedding_source") == query_source),
                key=lambda item: -item[1],
            ) if _ > 0.0
        ]
        keyword_rank: list[str] = []
        if q_tokens and any(corpus):
            # Overlap fraction is the primary signal so a one-memory corpus or a
            # term present in every memory still ranks; BM25 breaks ties.
            bm25 = BM25Okapi([doc or ["_"] for doc in corpus]).get_scores(q_tokens)
            q_set = set(q_tokens)
            overlap = [len(q_set & set(doc)) / len(q_set) for doc in corpus]
            order = sorted(range(len(candidates)), key=lambda i: (-overlap[i], -bm25[i]))
            keyword_rank = [candidates[i].node_id for i in order if overlap[i] > 0]
        synonym_rank: list[str] = []
        if expansions:
            syn_set = {s for syns in expansions.values() for s in syns}
            syn_overlap = [len(syn_set & set(doc)) / len(syn_set) for doc in corpus]
            synonym_rank = [candidates[i].node_id for i in sorted(range(len(candidates)), key=lambda i: -syn_overlap[i]) if syn_overlap[i] > 0]
        q_names: set[str] = set()
        name_rank: list[str] = []
        if self.features.names:
            q_names = set(lexicon.names(query)) | {w for w in q_tokens if w in corpus_names}
            q_set_for_names = set(q_tokens)
            name_hits = [
                (n, len(q_names & effective_names[n.node_id]) / len(q_names),
                 len(q_set_for_names & set(corpus[i])) / (len(q_set_for_names) or 1))
                for i, n in enumerate(candidates)
            ] if q_names else []
            name_rank = [n.node_id for n, o, kw in sorted(name_hits, key=lambda item: (-item[1], -item[2])) if o > 0]
        # Key rank: a memory's own key ("user.editor", "team.standup") is the
        # agent's index term for it; query words that hit it are strong evidence.
        key_rank: list[str] = []
        if q_tokens:
            q_set_for_keys = set(q_tokens)
            key_hits = []
            for n in candidates:
                key = n.metadata.get("key")
                if not key:
                    continue
                segments = [s for s in re.split(r"[._\-\s/]+", key.lower()) if s]
                key_tokens = set(segments if KEY_MATCH_MODE == "all" else segments[-1:])
                overlap = len(q_set_for_keys & key_tokens) / (len(key_tokens) or 1)
                if overlap > 0:
                    key_hits.append((n.node_id, overlap))
            key_rank = [node_id for node_id, _ in sorted(key_hits, key=lambda item: -item[1])]
        q_entities = set(extract_entities(query)) | q_names
        entity_rank = [
            n.node_id for n, overlap in sorted(
                ((n, len(q_entities & set(n.entity_ids)) / (len(q_entities | set(n.entity_ids)) or 1)) for n in candidates),
                key=lambda item: -item[1],
            ) if overlap > 0
        ]
        recency_rank = [n.node_id for n in sorted(candidates, key=lambda n: n.created_at, reverse=True)]
        fused = rrf(
            [vector_rank, keyword_rank, entity_rank, recency_rank, synonym_rank, name_rank, key_rank],
            weights=[1.0, 1.0, 0.6, 0.25, 0.5, 0.6, KEY_WEIGHT],
        )
        # recency alone never surfaces a memory: at least one content signal must match
        content_hits = set(vector_rank) | set(keyword_rank) | set(entity_rank) | set(synonym_rank) | set(name_rank) | set(key_rank)
        hits = [(node_id, score) for node_id, score in fused if node_id in content_hits][:limit]
        results = []
        q_content = set(q_tokens)
        syn_map = {s: k for k, syns in expansions.items() for s in syns}
        idx = {n.node_id: i for i, n in enumerate(candidates)}
        for node_id, score in hits:
            node = by_id[node_id]
            # Evidence coverage: the share of query terms this memory supports
            # directly, through a synonym, or as a name.  Unlike the fused rank
            # score it is comparable across queries, so it can gate "no answer".
            doc = set(corpus[idx[node_id]])
            supported = (q_content & doc) | {syn_map[s] for s in doc if s in syn_map} | (q_names & effective_names[node_id])
            coverage = round(len(supported) / len(q_content | q_names), 4) if (q_content | q_names) else 0.0
            if coverage < min_confidence:
                continue
            if strengthen and node.consolidation_state is ConsolidationState.ACTIVE:
                node.heat_score = min(5.0, node.heat_score + 0.1)
                node.activation_level = min(1.0, node.activation_level + 0.2)
                node.last_activated = self._clock()
                try:
                    await self.storage.update_node(node)
                    self._invalidate()
                except sqlite3.OperationalError:
                    pass  # read-only or locked store: recall still answers
            item = self._summary(node)
            item["score"] = round(score, 6)
            item["confidence"] = coverage
            if explain:
                item["why"] = {
                    "vector_rank": vector_rank.index(node_id) + 1 if node_id in vector_rank else None,
                    "keyword_rank": keyword_rank.index(node_id) + 1 if node_id in keyword_rank else None,
                    "entity_rank": entity_rank.index(node_id) + 1 if node_id in entity_rank else None,
                    "recency_rank": recency_rank.index(node_id) + 1,
                    "shared_entities": sorted(q_entities & set(node.entity_ids)),
                    "synonym_rank": synonym_rank.index(node_id) + 1 if node_id in synonym_rank else None,
                    "name_rank": name_rank.index(node_id) + 1 if node_id in name_rank else None,
                    "matched_names": sorted(q_names & effective_names[node_id]),
                    "key_rank": key_rank.index(node_id) + 1 if node_id in key_rank else None,
                    "corrected_query_terms": corrections,
                    "synonyms_used": {k: list(v) for k, v in expansions.items()},
                    "embedding_source": node.metadata.get("embedding_source"),
                }
            results.append(item)
        return results

    # ------------------------------------------------------------ maintenance
    async def forget(self, memory_id: str, reason: str = "forgotten by agent") -> bool:
        node = await self.storage.get_node(memory_id)
        if node is None or node.session_key != self.session_key:
            return False
        async with self._lock:
            node.consolidation_state = ConsolidationState.EVICTED
            node.metadata["forgotten_reason"] = reason
            node.metadata["forgotten_at"] = _iso(self._clock())
            node.updated_at = self._clock()
            await self.storage.update_node(node)
            # A superseding memory keeps a verbatim copy of what it replaced;
            # forgetting the original must scrub that copy too.
            for other in await self._session_nodes():
                c = other.metadata.get("contradicts")
                if isinstance(c, dict) and c.get("memory_id") == memory_id and "previous" in c:
                    other.metadata["contradicts"] = {"memory_id": memory_id, "previous": None, "scrubbed": True}
                    await self.storage.update_node(other)
            self._invalidate()
        return True

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        node = await self.storage.get_node(memory_id)
        if node is None or node.session_key != self.session_key:
            return None
        item = self._summary(node)
        if node.consolidation_state is ConsolidationState.EVICTED:
            item["content"] = None  # forgotten: the text is not served again
            item["contradicts"] = None
        edges = await self.storage.get_edges_from(memory_id)
        item["derived_from"] = [e.target_id for e in edges if e.edge_type is EdgeType.HIERARCHY]
        item["related"] = [e.target_id for e in edges if e.edge_type is EdgeType.ENTITY]
        return item

    async def list_recent(self, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
        nodes = [
            n for n in await self._session_nodes()
            if n.consolidation_state is ConsolidationState.ACTIVE and (not kind or n.metadata.get("kind") == kind.strip().lower())
        ]
        nodes.sort(key=lambda n: n.created_at, reverse=True)
        return [self._summary(n) for n in nodes[: max(1, min(int(limit), 200))]]

    async def decay(self, rate: float = 0.05, archive_below: float = 0.1, protect_importance: float = 0.8) -> dict[str, int]:
        """Cool every active memory; archive cold, unimportant ones. Nothing is deleted."""
        if rate < 0 or archive_below < 0 or not 0.0 <= protect_importance <= 1.0:
            raise ValueError("decay parameters must be non-negative (rate, archive_below) and protect_importance in [0, 1]")
        cooled = archived = 0
        async with self._lock:
          for node in await self._session_nodes():
            if node.consolidation_state is not ConsolidationState.ACTIVE:
                continue
            node.heat_score = max(0.0, node.heat_score - rate)
            node.decay_activation(rate)
            cooled += 1
            if node.heat_score < archive_below and node.importance_score < protect_importance and not node.metadata.get("key"):
                node.consolidation_state = ConsolidationState.ARCHIVED
                node.metadata["archived_reason"] = "decayed"
                node.metadata["archived_at"] = _iso(self._clock())
                archived += 1
            node.updated_at = self._clock()
            await self.storage.update_node(node)
          self._invalidate()
        return {"cooled": cooled, "archived": archived}

    async def stats(self) -> dict[str, Any]:
        nodes = await self._session_nodes()
        by_state: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        sources: dict[str, int] = {}
        for n in nodes:
            by_state[n.consolidation_state.value] = by_state.get(n.consolidation_state.value, 0) + 1
            by_kind[n.metadata.get("kind", "?")] = by_kind.get(n.metadata.get("kind", "?"), 0) + 1
            src = n.metadata.get("embedding_source", "?")
            sources[src] = sources.get(src, 0) + 1
        return {
            "session": self.session_key,
            "memories": len(nodes),
            "by_state": dict(sorted(by_state.items())),
            "by_kind": dict(sorted(by_kind.items())),
            "embedding_sources": dict(sorted(sources.items())),
            "embedder": self.embedder.name,
            "failure_domain": self.failure_domain,
        }

    # ------------------------------------------------------ coordination bridge
    CAPABILITY_ID = "memory"
    SCOPE = "memory:read"

    def node_id(self) -> str:
        return f"neuralgraph:{self.session_key}"

    def capability(self) -> dict[str, Any]:
        """What this node advertises to a fabric: id, scope, declared domain."""
        return {
            "capability_id": self.CAPABILITY_ID,
            "node_id": self.node_id(),
            "description": "private NeuralGraph memory",
            "query_types": ["recall", "keyed"],
            "policy_scope": [self.SCOPE],
            "availability": "available",
            "failure_domain": self.failure_domain,
        }

    async def _claim_candidates(self, query: str, keys: tuple[str, ...] | list[str], limit: int) -> list[tuple[NeuralNode, float]]:
        """Memories to offer a fabric: keyed facts for ``keys``, else recall hits."""
        if keys:
            wanted = {k.strip() for k in keys if k and k.strip()}
            nodes = [n for n in await self._session_nodes()
                     if n.consolidation_state is ConsolidationState.ACTIVE and n.metadata.get("key") in wanted]
            nodes.sort(key=lambda n: (n.metadata.get("key") or "", n.created_at))
            return [(n, min(1.0, 0.5 + 0.5 * n.importance_score)) for n in nodes[:limit]]
        hits = await self.recall(query, limit=limit, strengthen=False)
        out = []
        for h in hits:
            node = await self.storage.get_node(h["id"])
            if node is not None:
                out.append((node, max(0.0, min(1.0, h["confidence"]))))
        return out

    async def export_claims(
        self,
        query: str,
        capability_id: str = CAPABILITY_ID,
        scope: str = SCOPE,
        limit: int = 8,
        keys: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        """Export memories as policy-filtered claims through the real adapter.

        With ``keys`` the claims are keyed facts (``content = {"slot": key,
        "value": text}``) so a fabric can compose an answer across peers; without
        them they are recall hits (``slot = "memory"``).  Private memories are
        denied and cross the boundary as payload-free traces; everything else
        carries its lineage from the store and the domain declared on its root.
        """
        from ..coordination.adapters import NeuralGraphMemoryAdapter
        from ..coordination.contracts import (
            AuthorizationContext,
            CapabilityDescriptor,
            PolicyStatus,
            QueryBudget,
            QueryRequest,
        )
        from ..coordination.core import to_jsonable
        from ..coordination.storage_adapter import StorageLineageResolver

        ordered = await self._claim_candidates(query, keys, limit)

        async def local_retrieve(_request):
            return ordered

        def policy(node, _context):
            return PolicyStatus.DENIED if node.metadata.get("private") else PolicyStatus.ALLOWED

        resolver = StorageLineageResolver(self.storage, domain_key="failure_domain")

        async def lineage(node):
            # The resolver reports only ancestors it reached as roots.  A memory
            # that records no derivation is its own origin: it is reported as
            # its own root, with the domain *declared on it* (never inferred).
            known = await resolver(node)
            if not known["lineage_root_ids"] and not known["parent_memory_ids"]:
                known = dict(known)
                known["lineage_root_ids"] = (node.node_id,)
                declared = node.metadata.get("failure_domain")
                known["failure_domains"] = (str.__str__(declared),) if isinstance(declared, str) and declared.strip() else ()
            return known

        def projection(node):
            key = node.metadata.get("key")
            return {"slot": key if keys and key else "memory", "value": node.content, "kind": node.metadata.get("kind")}

        adapter = NeuralGraphMemoryAdapter(
            node_id=self.node_id(),
            capability=CapabilityDescriptor(
                capability_id=capability_id, node_id=self.node_id(),
                description="private NeuralGraph memory", query_types=("recall", "keyed"), policy_scope=(scope,),
            ),
            local_retrieve=local_retrieve,
            policy_filter=policy,
            clock=lambda: _iso(self._clock()) or "",
            lineage_resolver=lineage,
            claim_projection=projection,
        )
        request = QueryRequest(
            query_id=hashlib.sha256(("|".join(sorted(keys)) + "\n" + query).encode("utf-8")).hexdigest()[:16],
            content=query, requester_id="fabric", issued_at=_iso(self._clock()) or "",
            authorization=AuthorizationContext(scopes=(scope,)),
            requested_capability=capability_id, budget=QueryBudget(max_nodes=1, max_claims=limit, max_verifications=0),
        )
        exports = await adapter.query(request)
        return {"node_id": self.node_id(), "exports": to_jsonable(exports), "denied": sum(1 for e in exports if not e.claims), "allowed": sum(1 for e in exports if e.claims)}

    async def verify_support(
        self,
        required_keys: tuple[str, ...] | list[str],
        excluded_lineage_roots: tuple[str, ...] | list[str] = (),
        excluded_failure_domains: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        """Can this node support ``required_keys`` independently of the exclusions?

        Mirrors ``MemoryNodeAdapter.verify``: support is valid only when the
        memory's lineage roots and declared domain share nothing with what is
        already known to be compromised.  Private memories never count.
        """
        from ..coordination.storage_adapter import StorageLineageResolver

        wanted = {k.strip() for k in required_keys if k and k.strip()}
        ex_roots, ex_domains = set(excluded_lineage_roots), set(excluded_failure_domains)
        resolver = StorageLineageResolver(self.storage, domain_key="failure_domain")
        roots: set[str] = set(); domains: set[str] = set(); supported: set[str] = set(); reasons: list[str] = []
        for n in await self._session_nodes():
            key = n.metadata.get("key")
            if n.consolidation_state is not ConsolidationState.ACTIVE or key not in wanted:
                continue
            if n.metadata.get("private"):
                reasons.append("policy denied"); continue
            known = await resolver(n)
            n_roots = set(known["lineage_root_ids"]) or {n.node_id}
            declared = n.metadata.get("failure_domain")
            n_domains = set(known["failure_domains"]) or ({declared} if isinstance(declared, str) and declared.strip() else set())
            roots |= n_roots; domains |= n_domains
            if n_roots & ex_roots or n_domains & ex_domains:
                reasons.append("correlated support"); continue
            supported.add(key)
        valid = bool(supported)
        return {
            "node_id": self.node_id(), "valid": valid,
            "lineage_root_ids": sorted(roots), "failure_domains": sorted(domains),
            "supported_slots": sorted(supported),
            "reason": "independent support available" if valid else (reasons[0] if reasons else "no matching support"),
        }

    def close(self) -> None:
        self.storage.close()

    # ----------------------------------------------------------------- helpers
    async def _session_nodes(self) -> list[NeuralNode]:
        """Session memories, cached until this engine writes or the file changes."""
        stamp = self._file_stamp()
        if self._cache is None or stamp != self._cache_stamp:
            self._cache = await self.storage.get_nodes_by_session(self.session_key, NodeLayer.MESSAGE)
            self._cache_stamp = stamp
        return self._cache

    def _file_stamp(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self._db_path) if self._db_path else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st else None

    def _invalidate(self) -> None:
        self._cache = None

    async def _embed(self, embedder: Embedder, content: str) -> tuple[list[float], str]:
        """Embed with the given embedder; if a model dies, fall back to hashed honestly."""
        try:
            return await embedder.embed(content), embedder.name
        except Exception as exc:  # network / model failure
            if isinstance(embedder, HashedEmbedder):
                raise
            print(f"neuralgraph-mcp: embedding model failed ({type(exc).__name__}); using hashed embedding", file=sys.stderr)
            return await self._private_embedder.embed(content), HashedEmbedder.name

    def dominant_embedding_source(self) -> str | None:
        """The embedding space most of this session's memories live in, or None."""
        counts: dict[str, int] = {}
        try:
            rows = self.storage._conn.execute(
                "SELECT data FROM nodes WHERE session_key = ?", (self.session_key,)
            ).fetchall()
        except Exception:
            return None
        import json as _json
        for row in rows:
            src = (_json.loads(row[0]).get("metadata") or {}).get("embedding_source")
            if src:
                counts[src] = counts.get(src, 0) + 1
        return max(counts, key=counts.get) if counts else None

    @staticmethod
    def _valid_at(node: NeuralNode, at: datetime) -> bool:
        start = _parse_time(node.metadata.get("valid_from"), "valid_from")
        end = _parse_time(node.metadata.get("valid_to"), "valid_to")
        return (start is None or start <= at) and (end is None or at <= end)

    def _summary(self, node: NeuralNode, **extra: Any) -> dict[str, Any]:
        m = node.metadata
        item = {
            "id": node.node_id,
            "content": node.content,
            "kind": m.get("kind"),
            "key": m.get("key"),
            "tags": m.get("tags", []),
            "speaker": m.get("speaker"),
            "source": m.get("source"),
            "private": bool(m.get("private")),
            "entities": list(node.entity_ids),
            "created_at": _iso(node.created_at),
            "event_time": m.get("event_time"),
            "valid_from": m.get("valid_from"),
            "valid_to": m.get("valid_to"),
            "state": node.consolidation_state.value,
            "heat": round(node.heat_score, 4),
            "importance": node.importance_score,
            "source_memory_ids": list(node.source_memory_ids),
            "names": m.get("names", []),
            "supersedes": m.get("supersedes"),
            "superseded_by": m.get("superseded_by"),
            "contradicts": m.get("contradicts"),
            "embedding_source": m.get("embedding_source"),
        }
        item.update(extra)
        return item
