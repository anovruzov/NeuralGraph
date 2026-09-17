"""Cross-chat memory retrieval.

Three channels, fused by Reciprocal Rank Fusion (the report's lesson: scores from different channels are
not comparable, ranks are):

* **vector** — cosine between the query embedding and memory embeddings (numpy matrix cached per store
  revision; filters are boolean masks over parallel arrays, so 50k memories stay in the low milliseconds).
* **keyword** — FTS5 bm25 over memory text (or in-process BM25 when FTS5 is missing): exact names,
  numbers, rare words.
* **graph** — entities named in the query (matched against the entity registry and its aliases, longest
  match first) pull in the memories that mention them, plus one typed hop over relations.

After fusion, priors reorder the top of the list: a query that *names* a subject boosts that subject's own
memories (identity comes from metadata, never from text similarity — the single biggest win in the LoCoMo
campaign), importance and mild recency are multiplied in, and superseded memories are excluded unless asked
for. Every result carries its provenance (source messages) and a per-channel breakdown.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

from .llm import LLMClient
from .models import ChatMessage, Memory, RetrievedMemory, parse_iso, utcnow
from .store import ChatMemoryStore
from .textutil import norm_entity, normalize_ws, tokenize


@dataclass
class RetrievalConfig:
    depth: int = 100                     # candidates per channel before fusion
    rrf_k: int = 60
    weight_vector: float = 1.0
    weight_keyword: float = 0.8
    weight_graph: float = 0.7
    subject_boost: float = 1.6           # query names the memory's subject
    entity_boost: float = 1.25           # query names an entity the memory mentions
    importance_weight: float = 0.35      # score *= (1 - w) + w * importance
    recency_weight: float = 0.2          # score *= (1 - w) + w * recency
    recency_half_life_days: float = 365.0
    min_channel_hits: int = 1
    context_max_chars: int = 2400
    context_k: int = 10
    context_min_relative_score: float = 0.25   # drop context lines scoring < this fraction of the top hit
    vector_min_sim: float = 0.05               # cosine floor for the vector channel (drops ~orthogonal noise)
    context_min_vector_sim: float = 0.3        # a context line needs a keyword/graph hit or at least this cosine


def rrf(rankings: list[list[str]], weights: list[float] | None = None, k: int = 60) -> dict[str, float]:
    weights = weights or [1.0] * len(rankings)
    score: dict[str, float] = {}
    for ranking, w in zip(rankings, weights):
        for r, doc_id in enumerate(ranking):
            score[doc_id] = score.get(doc_id, 0.0) + w / (k + r + 1)
    return score


def _recency(observed_at: str | None, half_life_days: float, now: datetime) -> float:
    dt = parse_iso(observed_at)
    if dt is None:
        return 0.5
    days = max(0.0, (now - dt).total_seconds() / 86400.0)
    return math.exp(-math.log(2) * days / max(1.0, half_life_days))


class _Index:
    """Per-revision cache of active memories for fast filtering + vector search."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.ids = [r["memory_id"] for r in rows]
        self.pos = {mid: i for i, mid in enumerate(self.ids)}
        self.subject = np.array([r["subject"] for r in rows], dtype=object) if rows else np.array([], dtype=object)
        self.speaker = np.array([r["speaker"] for r in rows], dtype=object) if rows else np.array([], dtype=object)
        self.chat = np.array([r["chat_id"] for r in rows], dtype=object) if rows else np.array([], dtype=object)
        self.kind = np.array([r["kind"] for r in rows], dtype=object) if rows else np.array([], dtype=object)
        self.t = np.array([(r["event_time"] or r["observed_at"] or "") for r in rows], dtype=object) if rows else np.array([], dtype=object)
        self.importance = np.array([float(r["importance"]) for r in rows], dtype=np.float32) if rows else np.zeros(0, dtype=np.float32)
        vec_rows = [i for i, r in enumerate(rows) if r["embedding"]]
        self.vec_pos = np.array(vec_rows, dtype=np.int64)
        if vec_rows:
            dim = len(rows[vec_rows[0]]["embedding"])
            M = np.array([rows[i]["embedding"] if len(rows[i]["embedding"]) == dim else [0.0] * dim for i in vec_rows], dtype=np.float32)
            self.M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
            self.dim = dim
        else:
            self.M = None
            self.dim = 0

    def mask(self, *, subject: str | None, speaker: str | None, chat_id: str | None, kinds: set[str] | None,
             since: str | None, until: str | None) -> np.ndarray:
        n = len(self.ids)
        m = np.ones(n, dtype=bool)
        if n == 0:
            return m
        if subject:
            m &= self.subject == norm_entity(subject)
        if speaker:
            m &= self.speaker == speaker
        if chat_id:
            m &= self.chat == chat_id
        if kinds:
            m &= np.isin(self.kind, list(kinds))
        if since:
            m &= np.array([x >= since for x in self.t], dtype=bool)
        if until:
            m &= np.array([x <= until for x in self.t], dtype=bool)
        return m

    def vector_rank(self, qvec: list[float], mask: np.ndarray, k: int, min_sim: float = 0.0) -> list[tuple[str, float]]:
        if self.M is None or not qvec or len(qvec) != self.dim:
            return []
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        sims = self.M @ q
        allowed = mask[self.vec_pos] & (sims >= min_sim)
        sims = np.where(allowed, sims, -np.inf)
        if k < len(sims):
            idx = np.argpartition(-sims, k)[:k]
        else:
            idx = np.arange(len(sims))
        idx = idx[np.argsort(-sims[idx])]
        out = []
        for i in idx:
            if not np.isfinite(sims[i]):
                break
            out.append((self.ids[self.vec_pos[i]], float(sims[i])))
        return out


class MemoryRetriever:
    def __init__(self, store: ChatMemoryStore, llm: LLMClient, config: RetrievalConfig | None = None) -> None:
        self.store = store
        self.llm = llm
        self.config = config or RetrievalConfig()
        self._index: _Index | None = None
        self._index_rev: tuple[int, int] | None = None
        self._entity_keys: dict[str, str] = {}
        self._entity_rev: tuple[int, int] | None = None

    # ------------------------------------------------------------------ caches
    async def _get_index(self) -> _Index:
        key = self.store.cache_key()
        if self._index is None or self._index_rev != key:
            self._index = _Index(await self.store.index_rows())
            self._index_rev = key
        return self._index

    async def _get_entity_keys(self) -> dict[str, str]:
        key = self.store.cache_key()
        if self._entity_rev != key:
            self._entity_keys = await self.store.all_entity_keys()
            self._entity_rev = key
        return self._entity_keys

    def invalidate(self) -> None:
        self._index_rev = None
        self._entity_rev = None

    # ------------------------------------------------------------------ query analysis
    async def query_entities(self, query: str) -> list[str]:
        """Entity ids named in the query (longest alias match first, no overlaps)."""
        keys = await self._get_entity_keys()
        if not keys:
            return []
        q = " " + norm_entity(query) + " "
        found: list[tuple[int, str]] = []
        for alias, eid in keys.items():
            if len(alias) < 3:
                continue
            pos = q.find(" " + alias + " ")
            if pos >= 0:
                found.append((len(alias), eid))
        # words that are only stop-ish tokens produce false positives; require a real token
        out: list[str] = []
        for _, eid in sorted(found, key=lambda x: -x[0]):
            if eid not in out:
                out.append(eid)
        return out

    # ------------------------------------------------------------------ search
    async def search(
        self,
        query: str,
        *,
        k: int = 10,
        subject: str | None = None,
        speaker: str | None = None,
        chat_id: str | None = None,
        kinds: Iterable[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        include_superseded: bool = False,
        with_sources: bool = True,
        query_embedding: list[float] | None = None,
        channels: str = "VKG",
        touch: bool = True,
    ) -> list[RetrievedMemory]:
        cfg = self.config
        query = normalize_ws(query)
        if not query:
            return []
        index = await self._get_index()
        kind_set = set(kinds) if kinds else None
        mask = index.mask(subject=subject, speaker=speaker, chat_id=chat_id, kinds=kind_set, since=since, until=until)
        allowed = {index.ids[i] for i in np.nonzero(mask)[0]}
        if not allowed and not include_superseded:
            return []

        rankings: list[list[str]] = []
        weights: list[float] = []
        per_channel: dict[str, dict[str, float]] = {"vector": {}, "keyword": {}, "graph": {}}

        if "V" in channels:
            qvec = query_embedding
            if qvec is None:
                try:
                    qvec = await self.llm.embed(query)
                except Exception:
                    qvec = None
            if qvec:
                vr = index.vector_rank(qvec, mask, cfg.depth, cfg.vector_min_sim)
                per_channel["vector"] = dict(vr)
                rankings.append([m for m, _ in vr]); weights.append(cfg.weight_vector)

        if "K" in channels:
            kr = [(m, s) for m, s in await self.store.keyword_candidates(query, cfg.depth * 2) if m in allowed][: cfg.depth]
            per_channel["keyword"] = dict(kr)
            rankings.append([m for m, _ in kr]); weights.append(cfg.weight_keyword)

        q_entities: list[str] = []
        if "G" in channels:
            q_entities = await self.query_entities(query)
            if q_entities:
                gr = await self._graph_rank(q_entities, allowed, cfg.depth)
                per_channel["graph"] = dict(gr)
                rankings.append([m for m, _ in gr]); weights.append(cfg.weight_graph)

        fused = rrf(rankings, weights, cfg.rrf_k)
        if not fused:
            return []

        # priors
        now = utcnow()
        ent_map = await self.store.entity_ids_for_memories(list(fused.keys()))
        q_ent_set = set(q_entities)
        scored: list[tuple[str, float, dict[str, float]]] = []
        for mid, base in fused.items():
            i = index.pos.get(mid)
            if i is None:
                continue
            imp = float(index.importance[i])
            rec = _recency(index.rows[i]["observed_at"], cfg.recency_half_life_days, now)
            s = base * ((1 - cfg.importance_weight) + cfg.importance_weight * imp)
            s *= (1 - cfg.recency_weight) + cfg.recency_weight * rec
            boosts = {}
            if q_ent_set and index.subject[i] in q_ent_set:
                s *= cfg.subject_boost; boosts["subject"] = cfg.subject_boost
            elif q_ent_set and q_ent_set & set(ent_map.get(mid, [])):
                s *= cfg.entity_boost; boosts["entity"] = cfg.entity_boost
            hits = sum(1 for ch in per_channel.values() if mid in ch)
            if hits < cfg.min_channel_hits:
                continue
            ch = {name: round(vals[mid], 4) for name, vals in per_channel.items() if mid in vals}
            ch.update({"rrf": round(base, 5), "importance": round(imp, 2), "recency": round(rec, 3), **boosts})
            scored.append((mid, s, ch))
        scored.sort(key=lambda x: -x[1])
        top = scored[:k]
        mems = await self.store.get_memories_by_ids([m for m, _, _ in top], with_sources=with_sources)
        by_id = {m.memory_id: m for m in mems}
        out: list[RetrievedMemory] = []
        src_cache: dict[str, ChatMessage] = {}
        if with_sources:
            all_src = [s for m in mems for s in m.source_message_ids]
            for msg in await self.store.get_messages_by_ids(all_src):
                src_cache[msg.message_id] = msg
        for mid, s, ch in top:
            m = by_id.get(mid)
            if m is None:
                continue
            out.append(RetrievedMemory(
                memory=m, score=round(float(s), 6), channels=ch,
                sources=[src_cache[x] for x in m.source_message_ids if x in src_cache],
                entities=ent_map.get(mid, []),
                explanation=self._explain(ch),
            ))
        if include_superseded:
            hist: list[RetrievedMemory] = []
            for rm in out:
                for old in await self.store.history(rm.memory.memory_id):
                    hist.append(RetrievedMemory(memory=old, score=rm.score * 0.5, channels={"history_of": rm.score},
                                                explanation=f"superseded by {rm.memory.memory_id}"))
            out.extend(hist)
        if touch and out:
            await self.store.touch_access([r.memory.memory_id for r in out])
        return out

    async def _graph_rank(self, entity_ids: list[str], allowed: set[str], depth: int) -> list[tuple[str, float]]:
        counts = await self.store.entity_memory_counts(entity_ids)
        scores: dict[str, float] = {}
        # direct mentions, weighted by inverse entity frequency (hub entities carry less signal)
        for mid, eid in await self.store.memories_for_entities(entity_ids, limit=depth * 4):
            if mid in allowed:
                scores[mid] = scores.get(mid, 0.0) + 1.0 / math.log(2.0 + counts.get(eid, 1))
        # one typed hop over relations
        nbrs = await self.store.neighbors(entity_ids, limit=100)
        hop = [n for lst in nbrs.values() for n, _, _ in lst if n not in entity_ids]
        if hop:
            hop_counts = await self.store.entity_memory_counts(hop)
            for mid, eid in await self.store.memories_for_entities(hop[:40], limit=depth * 2):
                if mid in allowed:
                    scores[mid] = scores.get(mid, 0.0) + 0.35 / math.log(2.0 + hop_counts.get(eid, 1))
        return sorted(scores.items(), key=lambda x: -x[1])[:depth]

    @staticmethod
    def _explain(ch: dict[str, float]) -> str:
        parts = []
        if "vector" in ch:
            parts.append(f"semantic {ch['vector']:.2f}")
        if "keyword" in ch:
            parts.append("keyword match")
        if "graph" in ch:
            parts.append("entity graph")
        if "subject" in ch:
            parts.append("names the subject")
        elif "entity" in ch:
            parts.append("names a mentioned entity")
        return ", ".join(parts)

    # ------------------------------------------------------------------ higher-level helpers
    async def context_for(self, query: str, *, k: int | None = None, max_chars: int | None = None,
                          header: str = "Relevant long-term memories (most relevant first):", **filters: Any) -> str:
        """A compact block to prepend to a new chat's prompt. Empty string when nothing is relevant."""
        cfg = self.config
        results = await self.search(query, k=k or cfg.context_k, with_sources=False, **filters)
        if not results:
            return ""
        # evidence floor: a line needs a keyword or graph hit, or a reasonably similar embedding
        results = [r for r in results if "keyword" in r.channels or "graph" in r.channels
                   or r.channels.get("vector", 0.0) >= cfg.context_min_vector_sim]
        if not results:
            return ""
        top_score = results[0].score or 1.0
        results = [r for r in results if r.score >= cfg.context_min_relative_score * top_score]
        lines = [header]
        used = len(header)
        limit = max_chars or cfg.context_max_chars
        for r in results:
            m = r.memory
            when = f"{m.event_time}, " if m.event_time else ""
            line = f"- [{when}{m.kind}] {m.text} (from chat {m.chat_id}, {(m.observed_at or '')[:10]})"
            if used + len(line) + 1 > limit:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines) if len(lines) > 1 else ""

    async def profile(self, subject: str, *, limit: int = 60) -> dict[str, Any]:
        sid = norm_entity(subject)
        mems = await self.store.list_memories(subject=sid, limit=limit, order="importance DESC", with_sources=True)
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for m in mems:
            by_kind.setdefault(m.kind, []).append(m.to_dict())
        entity = await self.store.get_entity(sid)
        rels = await self.store.relations_for(sid, limit=100)
        return {
            "subject": sid,
            "name": entity.name if entity else subject,
            "entity": entity.to_dict() if entity else None,
            "memory_count": len(mems),
            "memories_by_kind": by_kind,
            "relations": [r.to_dict() for r in rels],
        }

    async def related(self, memory_id: str) -> list[dict[str, Any]]:
        out = []
        for link in await self.store.links_for(memory_id):
            other = link.target_id if link.source_id == memory_id else link.source_id
            m = await self.store.get_memory(other)
            if m is not None:
                direction = "out" if link.source_id == memory_id else "in"
                out.append({"memory": m.to_dict(), "link_type": link.link_type, "weight": link.weight, "direction": direction})
        return out

    async def timeline(self, *, subject: str | None = None, since: str | None = None, until: str | None = None,
                       limit: int = 100) -> list[Memory]:
        mems = await self.store.list_memories(subject=subject, since=since, until=until, limit=limit, order="observed_at ASC")
        return sorted(mems, key=lambda m: (m.event_time or m.observed_at or ""))
