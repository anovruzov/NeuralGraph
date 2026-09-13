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

import hashlib
import math
import os
import re
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
from ..mega_search import entities_of, rrf, tokenize
from ..sqlite_storage import SQLiteNeuralGraphStorage

MAX_TEXT_CHARS = 8000
NEAR_DUPLICATE_COSINE = 0.97
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
        tokens = tokenize(text)
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


async def choose_embedder(mode: str = "auto") -> Embedder:
    """``hashed`` never touches the network; ``model`` requires one; ``auto`` probes once."""
    if mode == "hashed":
        return HashedEmbedder()
    model = ModelEmbedder()
    if mode == "model":
        await model.embed("probe")
        return model
    try:
        await model.embed("probe")
        return model
    except Exception:
        return HashedEmbedder()


def extract_entities(text: str, explicit: tuple[str, ...] = ()) -> tuple[str, ...]:
    found = set(entities_of(text))
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
    ) -> None:
        self.session_key = session_key
        self.embedder: Embedder = embedder or HashedEmbedder()
        self._clock = clock
        # Recorded only when the operator names it; never inferred from the
        # database path, host, or anything else (CLAUDE.md rule 5).
        self.failure_domain = failure_domain or os.environ.get("NEURALGRAPH_FAILURE_DOMAIN") or None
        self.storage = storage or SQLiteNeuralGraphStorage(db_path)

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
        content = normalize_text(text or "")
        if not content:
            raise ValueError("text must not be empty")
        if len(content) > MAX_TEXT_CHARS:
            raise ValueError(f"text longer than {MAX_TEXT_CHARS} characters; split it into separate memories")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        kind = (kind or "note").strip().lower()
        tags = tuple(sorted({t.strip().lower() for t in tags if t and t.strip()}))
        event_time = _parse_time(when, "when")
        valid_from_dt = _parse_time(valid_from, "valid_from")
        valid_to_dt = _parse_time(valid_to, "valid_to")
        if valid_from_dt and valid_to_dt and valid_to_dt < valid_from_dt:
            raise ValueError("valid_to must not precede valid_from")
        now = self._clock()
        content_hash = hashlib.sha256(f"{self.session_key}\n{content.lower()}".encode("utf-8")).hexdigest()

        nodes = await self._session_nodes()
        # exact duplicate: strengthen, do not clone
        for node in nodes:
            if node.metadata.get("content_hash") == content_hash and node.consolidation_state is ConsolidationState.ACTIVE:
                node.heat_score = min(5.0, node.heat_score + 0.25)
                node.updated_at = now
                await self.storage.update_node(node)
                return self._summary(node, deduplicated=True)

        embedding = await self.embedder.embed(content)
        # near duplicate in the same embedding space and kind
        for node in nodes:
            if (
                node.consolidation_state is ConsolidationState.ACTIVE
                and node.embedding
                and node.metadata.get("embedding_source") == self.embedder.name
                and node.metadata.get("kind") == kind
                and node.metadata.get("key") == key
                and cosine_similarity(embedding, node.embedding) >= NEAR_DUPLICATE_COSINE
            ):
                node.heat_score = min(5.0, node.heat_score + 0.25)
                node.updated_at = now
                await self.storage.update_node(node)
                return self._summary(node, deduplicated=True, near_duplicate_of=node.node_id)

        for parent in derived_from:
            if await self.storage.get_node(parent) is None:
                raise ValueError(f"derived_from names an unknown memory: {parent}")

        entity_ids = extract_entities(content, tuple(entities))
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
            "embedding_source": self.embedder.name,
            "event_time": _iso(event_time),
            "valid_from": _iso(valid_from_dt),
            "valid_to": _iso(valid_to_dt),
            "mentioned_dates": sorted(set(_ISO_DATE.findall(content))),
            "created_by": "neuralgraph-mcp",
        }
        if self.failure_domain:
            metadata["failure_domain"] = self.failure_domain
        node = NeuralNode(
            node_id=generate_node_id(),
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
        related.sort(key=lambda item: (-item[0], item[1].created_at), reverse=False)
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
        query_vec = await self.embedder.embed(query)
        vector_rank = [
            n.node_id for n, _ in sorted(
                ((n, cosine_similarity(query_vec, n.embedding)) for n in candidates
                 if n.embedding and n.metadata.get("embedding_source") == self.embedder.name),
                key=lambda item: -item[1],
            ) if _ > 0.0
        ]
        corpus = [tokenize(n.content) for n in candidates]
        keyword_rank: list[str] = []
        q_tokens = tokenize(query)
        if q_tokens and any(corpus):
            scores = BM25Okapi([doc or ["_"] for doc in corpus]).get_scores(q_tokens)
            keyword_rank = [candidates[i].node_id for i in sorted(range(len(candidates)), key=lambda i: -scores[i]) if scores[i] > 0]
        q_entities = set(extract_entities(query))
        entity_rank = [
            n.node_id for n, overlap in sorted(
                ((n, len(q_entities & set(n.entity_ids)) / (len(q_entities | set(n.entity_ids)) or 1)) for n in candidates),
                key=lambda item: -item[1],
            ) if overlap > 0
        ]
        recency_rank = [n.node_id for n in sorted(candidates, key=lambda n: n.created_at, reverse=True)]
        fused = rrf([vector_rank, keyword_rank, entity_rank, recency_rank], weights=[1.0, 1.0, 0.6, 0.25])
        # recency alone never surfaces a memory: at least one content signal must match
        content_hits = set(vector_rank) | set(keyword_rank) | set(entity_rank)
        hits = [(node_id, score) for node_id, score in fused if node_id in content_hits][:limit]
        results = []
        for node_id, score in hits:
            node = by_id[node_id]
            if strengthen and node.consolidation_state is ConsolidationState.ACTIVE:
                node.heat_score = min(5.0, node.heat_score + 0.1)
                node.activation_level = min(1.0, node.activation_level + 0.2)
                node.last_activated = self._clock()
                await self.storage.update_node(node)
            item = self._summary(node)
            item["score"] = round(score, 6)
            if explain:
                item["why"] = {
                    "vector_rank": vector_rank.index(node_id) + 1 if node_id in vector_rank else None,
                    "keyword_rank": keyword_rank.index(node_id) + 1 if node_id in keyword_rank else None,
                    "entity_rank": entity_rank.index(node_id) + 1 if node_id in entity_rank else None,
                    "recency_rank": recency_rank.index(node_id) + 1,
                    "shared_entities": sorted(q_entities & set(node.entity_ids)),
                    "embedding_source": node.metadata.get("embedding_source"),
                }
            results.append(item)
        return results

    # ------------------------------------------------------------ maintenance
    async def forget(self, memory_id: str, reason: str = "forgotten by agent") -> bool:
        node = await self.storage.get_node(memory_id)
        if node is None or node.session_key != self.session_key:
            return False
        node.consolidation_state = ConsolidationState.EVICTED
        node.metadata["forgotten_reason"] = reason
        node.metadata["forgotten_at"] = _iso(self._clock())
        node.updated_at = self._clock()
        await self.storage.update_node(node)
        return True

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        node = await self.storage.get_node(memory_id)
        if node is None or node.session_key != self.session_key:
            return None
        item = self._summary(node)
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
        cooled = archived = 0
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
    async def export_claims(self, query: str, capability_id: str = "memory", scope: str = "memory:read", limit: int = 8) -> dict[str, Any]:
        """Export recall results as policy-filtered claims through the real adapter.

        Private memories are denied and cross the boundary as payload-free
        traces; everything else carries its lineage from the store.
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

        hits = await self.recall(query, limit=limit, strengthen=False)
        nodes = {h["id"]: await self.storage.get_node(h["id"]) for h in hits}
        ordered = [(nodes[h["id"]], h["score"]) for h in hits if nodes.get(h["id"])]

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

        adapter = NeuralGraphMemoryAdapter(
            node_id=f"neuralgraph:{self.session_key}",
            capability=CapabilityDescriptor(
                capability_id=capability_id, node_id=f"neuralgraph:{self.session_key}",
                description="private NeuralGraph memory", query_types=("recall",), policy_scope=(scope,),
            ),
            local_retrieve=local_retrieve,
            policy_filter=policy,
            clock=lambda: _iso(self._clock()) or "",
            lineage_resolver=lineage,
            claim_projection=lambda node: {"text": node.content, "kind": node.metadata.get("kind")},
        )
        request = QueryRequest(
            query_id=hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
            content=query, requester_id="mcp-client", issued_at=_iso(self._clock()) or "",
            authorization=AuthorizationContext(scopes=(scope,)),
            requested_capability=capability_id, budget=QueryBudget(max_nodes=1, max_claims=limit, max_verifications=0),
        )
        exports = await adapter.query(request)
        return {"exports": to_jsonable(exports), "denied": sum(1 for e in exports if not e.claims), "allowed": sum(1 for e in exports if e.claims)}

    def close(self) -> None:
        self.storage.close()

    # ----------------------------------------------------------------- helpers
    async def _session_nodes(self) -> list[NeuralNode]:
        return await self.storage.get_nodes_by_session(self.session_key, NodeLayer.MESSAGE)

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
            "supersedes": m.get("supersedes"),
            "superseded_by": m.get("superseded_by"),
            "contradicts": m.get("contradicts"),
            "embedding_source": m.get("embedding_source"),
        }
        item.update(extra)
        return item
