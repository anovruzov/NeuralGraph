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


def _time_bounds(t: str | None, precision: str | None) -> tuple[str, str]:
    """[start, end] ISO bounds of a memory's time given its precision ("2026-10" -> whole month)."""
    if not t:
        return "", ""
    t = t.strip()
    if precision == "year" or len(t) == 4:
        return f"{t[:4]}-01-01T00:00:00", f"{t[:4]}-12-31T23:59:59"
    if precision == "month" or len(t) == 7:
        return f"{t[:7]}-01T00:00:00", f"{t[:7]}-31T23:59:59"
    if len(t) == 10:
        return f"{t}T00:00:00", f"{t}T23:59:59"
    base = t[:19]
    return base, base


def _query_bounds(since: str | None, until: str | None) -> tuple[str, str]:
    lo = since or ""
    hi = until or ""
    if lo and len(lo) == 10:
        lo += "T00:00:00"
    elif lo and len(lo) == 7:
        lo += "-01T00:00:00"
    elif lo and len(lo) == 4:
        lo += "-01-01T00:00:00"
    if hi and len(hi) == 10:
        hi += "T23:59:59"
    elif hi and len(hi) == 7:
        hi += "-31T23:59:59"
    elif hi and len(hi) == 4:
        hi += "-12-31T23:59:59"
    return lo[:19], hi[:19]


class _Index:
    """In-memory view of memories (active + superseded) for fast filtering and vector search.

    Built once, then refreshed incrementally: rows written since the last refresh are upserted, so an
    ingest-heavy server never rebuilds the whole matrix per query. One normalised matrix per embedding
    dimension, so a change of embedding model degrades gracefully (old rows keep matching queries of
    their own dimension until maintenance re-embeds them).
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.last_updated_at = ""
        self._dirty = True
        self.upsert(rows)

    # ---- mutation
    def upsert(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            if r["status"] in ("active", "superseded"):
                self.rows[r["memory_id"]] = r
            else:
                self.rows.pop(r["memory_id"], None)
            if r.get("updated_at") and r["updated_at"] > self.last_updated_at:
                self.last_updated_at = r["updated_at"]
        self._dirty = True

    def _build(self) -> None:
        rows = list(self.rows.values())
        self.ids = [r["memory_id"] for r in rows]
        self.pos = {mid: i for i, mid in enumerate(self.ids)}
        n = len(rows)
        self.subject = np.array([r["subject"] for r in rows], dtype=object) if n else np.array([], dtype=object)
        self.speaker = np.array([r["speaker"] for r in rows], dtype=object) if n else np.array([], dtype=object)
        self.chat = np.array([r["chat_id"] for r in rows], dtype=object) if n else np.array([], dtype=object)
        self.kind = np.array([r["kind"] for r in rows], dtype=object) if n else np.array([], dtype=object)
        bounds = [_time_bounds(r["event_time"] or r["observed_at"], r.get("event_time_precision") if r["event_time"] else None) for r in rows]
        self.t_lo = [b[0] for b in bounds]
        self.t_hi = [b[1] for b in bounds]
        self.importance = np.array([float(r["importance"]) for r in rows], dtype=np.float32) if n else np.zeros(0, dtype=np.float32)
        self.active = np.array([r["status"] == "active" for r in rows], dtype=bool) if n else np.zeros(0, dtype=bool)
        self.observed = [r["observed_at"] for r in rows]
        by_dim: dict[int, list[int]] = {}
        for i, r in enumerate(rows):
            e = r.get("embedding")
            if e:
                by_dim.setdefault(len(e), []).append(i)
        self.mats: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for dim, idxs in by_dim.items():
            M = np.array([rows[i]["embedding"] for i in idxs], dtype=np.float32)
            M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
            self.mats[dim] = (np.array(idxs, dtype=np.int64), M)
        self._dirty = False

    def _ensure(self) -> None:
        if self._dirty:
            self._build()

    # ---- queries
    def mask(self, *, subject: str | None, speaker: str | None, chat_id: str | None, kinds: set[str] | None,
             since: str | None, until: str | None, include_superseded: bool = False) -> np.ndarray:
        self._ensure()
        n = len(self.ids)
        m = np.ones(n, dtype=bool)
        if n == 0:
            return m
        # superseded memories are hidden unless asked for, or unless the query is about a time window (a fact
        # that was later replaced was still the truth back then)
        if not include_superseded and not (since or until):
            m &= self.active
        if subject:
            m &= self.subject == subject
        if speaker:
            m &= self.speaker == speaker
        if chat_id:
            m &= self.chat == chat_id
        if kinds:
            m &= np.isin(self.kind, list(kinds))
        lo, hi = _query_bounds(since, until)
        if lo:
            m &= np.array([x >= lo for x in self.t_hi], dtype=bool)   # memory interval ends after the window starts
        if hi:
            m &= np.array([x <= hi for x in self.t_lo], dtype=bool)   # and starts before the window ends
        return m

    def vector_rank(self, qvec: list[float], mask: np.ndarray, k: int, min_sim: float = 0.0) -> list[tuple[str, float]]:
        self._ensure()
        if not qvec or len(qvec) not in self.mats:
            return []
        idxs, M = self.mats[len(qvec)]
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        sims = M @ q
        allowed = mask[idxs] & (sims >= min_sim)
        sims = np.where(allowed, sims, -np.inf)
        if k < len(sims):
            top = np.argpartition(-sims, k)[:k]
        else:
            top = np.arange(len(sims))
        top = top[np.argsort(-sims[top])]
        out = []
        for i in top:
            if not np.isfinite(sims[i]):
                break
            out.append((self.ids[idxs[i]], float(sims[i])))
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
        if self._index is None:
            self._index = _Index(await self.store.index_rows(status=None))
            self._index_rev = key
        elif self._index_rev != key:
            # incremental: only rows written since the last refresh (inclusive; upsert is idempotent)
            changed = await self.store.index_rows(status=None, updated_after=self._index.last_updated_at or None)
            self._index.upsert(changed)
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
        q = " " + norm_entity(re.sub(r"'s\b|'\b", "", query)) + " "
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
        subject_id = None
        if subject:
            subject_id = await self.store.resolve_alias(subject) or norm_entity(subject)
        mask = index.mask(subject=subject_id, speaker=speaker, chat_id=chat_id, kinds=kind_set, since=since, until=until,
                          include_superseded=include_superseded)
        allowed = {index.ids[i] for i in np.nonzero(mask)[0]}
        if not allowed:
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
            kw_filters = {"subject": subject_id, "chat_id": chat_id, "speaker": speaker}
            kw_rows = await self.store.keyword_candidates(query, cfg.depth * 2, **kw_filters)
            if include_superseded or since or until:
                kw_rows = kw_rows + await self.store.keyword_candidates(query, cfg.depth * 2, status="superseded", **kw_filters)
            kr = [(m, s) for m, s in kw_rows if m in allowed][: cfg.depth]
            per_channel["keyword"] = dict(kr)
            rankings.append([m for m, _ in kr]); weights.append(cfg.weight_keyword)

        q_entities: list[str] = []
        if "G" in channels:
            q_entities = await self.query_entities(query)
            if q_entities:
                gr = await self._graph_rank(q_entities, allowed, cfg.depth, bool(include_superseded or since or until),
                                            subject=subject_id, chat_id=chat_id)
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
            rec = _recency(index.observed[i], cfg.recency_half_life_days, now)
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
            present = {r.memory.memory_id for r in out}
            hist: list[RetrievedMemory] = []
            for rm in list(out):
                for old in await self.store.history(rm.memory.memory_id):
                    if old.memory_id not in present:
                        present.add(old.memory_id)
                        hist.append(RetrievedMemory(memory=old, score=rm.score * 0.5, channels={"history_of": rm.score},
                                                    explanation=f"superseded by {rm.memory.memory_id}"))
            out.extend(hist)
        for r in out:
            if r.memory.status == "superseded" and "superseded" not in r.explanation:
                note = f"superseded by {r.memory.superseded_by}" if r.memory.superseded_by else "superseded"
                r.explanation = (r.explanation + ", " if r.explanation else "") + note + " (was current at that time)"
        if touch and out:
            await self.store.touch_access([r.memory.memory_id for r in out])
        return out

    async def _graph_rank(self, entity_ids: list[str], allowed: set[str], depth: int, with_superseded: bool = False,
                          *, subject: str | None = None, chat_id: str | None = None) -> list[tuple[str, float]]:
        counts = await self.store.entity_memory_counts(entity_ids)
        scores: dict[str, float] = {}
        # direct mentions, weighted by inverse entity frequency (hub entities carry less signal)
        flt = {"subject": subject, "chat_id": chat_id}
        rows = await self.store.memories_for_entities(entity_ids, limit=depth * 4, **flt)
        if with_superseded:
            rows = rows + await self.store.memories_for_entities(entity_ids, limit=depth * 4, status="superseded", **flt)
        for mid, eid in rows:
            if mid in allowed:
                scores[mid] = scores.get(mid, 0.0) + 1.0 / math.log(2.0 + counts.get(eid, 1))
        # one typed hop over relations
        nbrs = await self.store.neighbors(entity_ids, limit=100)
        hop = [n for lst in nbrs.values() for n, _, _ in lst if n not in entity_ids]
        if hop:
            hop_counts = await self.store.entity_memory_counts(hop)
            for mid, eid in await self.store.memories_for_entities(hop[:40], limit=depth * 2, **flt):
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
        block, _ = await self.context_with_results(query, k=k, max_chars=max_chars, header=header, **filters)
        return block

    async def context_with_results(self, query: str, *, k: int | None = None, max_chars: int | None = None,
                                   header: str = "Relevant long-term memories (most relevant first):",
                                   **filters: Any) -> tuple[str, list[RetrievedMemory]]:
        """Like :meth:`context_for` but also returns the memories that made it into the block."""
        cfg = self.config
        results = await self.search(query, k=k or cfg.context_k, with_sources=False, **filters)
        if not results:
            return "", []
        # evidence floor: a line needs a keyword or graph hit, or a reasonably similar embedding
        results = [r for r in results if "keyword" in r.channels or "graph" in r.channels
                   or r.channels.get("vector", 0.0) >= cfg.context_min_vector_sim]
        if not results:
            return "", []
        top_score = results[0].score or 1.0
        results = [r for r in results if r.score >= cfg.context_min_relative_score * top_score]
        lines = [header]
        used = len(header)
        limit = max_chars or cfg.context_max_chars
        included: list[RetrievedMemory] = []
        for r in results:
            m = r.memory
            when = f"{m.event_time}, " if m.event_time else ""
            line = f"- [{when}{m.kind}] {m.text} (from chat {m.chat_id}, {(m.observed_at or '')[:10]})"
            if used + len(line) + 1 > limit:
                continue   # too long for the remaining budget; a shorter, later line may still fit
            lines.append(line)
            included.append(r)
            used += len(line) + 1
        if len(lines) == 1:
            return "", []
        return "\n".join(lines), included

    async def profile(self, subject: str, *, limit: int = 60) -> dict[str, Any]:
        sid = await self.store.resolve_alias(subject) or norm_entity(subject)
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
            if m is not None and m.status != "retracted":
                direction = "out" if link.source_id == memory_id else "in"
                out.append({"memory": m.to_dict(), "link_type": link.link_type, "weight": link.weight, "direction": direction})
        return out

    async def timeline(self, *, subject: str | None = None, since: str | None = None, until: str | None = None,
                       limit: int = 100) -> list[Memory]:
        mems = await self.store.list_memories(subject=subject, since=since, until=until, limit=limit, order="observed_at ASC")
        return sorted(mems, key=lambda m: (m.event_time or m.observed_at or ""))
