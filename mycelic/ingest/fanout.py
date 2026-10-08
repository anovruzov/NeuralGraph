"""Reads across the shards of one holder (docs/mycelic/INGESTION.md §7.4, §7.5).

* :class:`ShardedRetriever` — bounded fan-out hybrid retrieval. The query is embedded once and the vector reused by every
  shard; each shard (at most 8) is searched on its own read connection in a worker thread under a per-shard timeout
  (1.5 s), with the audience's allow-set computed for that shard; the per-shard rankings are merged by reciprocal-rank
  fusion, because scores from different shards are not comparable (each shard has its own BM25 statistics and priors,
  the same reason NeuralGraph fuses its channels by rank). A shard that fails or times out marks the result ``partial``;
  shards cut by the bound mark it ``truncated``.
* :class:`GraphTraverser` — breadth-first paths over the typed edges of every shard. Entity ids are stable across a
  holder's shards (canonical ids, or ``norm_entity(name)``), so an edge known in two shards is one edge with the union of
  its evidence. An edge is followed only when at least one of its ``relation_evidence`` rows is public, not negated, and
  from a record the audience may still see (§8.7, the rule ``linking.recompute_relation_sync`` applies per shard).

With a single shard neither class is used: ``EvidenceStore`` keeps its single-store path.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import Counter
from typing import Any, Callable, Iterable, Sequence

from .shards import MAX_SHARDS_PER_HOLDER, PER_SHARD_TIMEOUT, ShardRouter, ShardSet, ShardSpec

logger = logging.getLogger(__name__)

RRF_K = 60


class ShardedResults(list):
    """A list of ``RetrievedMemory`` (best first) that also says how complete it is."""

    def __init__(self, items: Iterable[Any] = (), *, partial: bool = False, truncated: bool = False, shards: Sequence[str] = (),
                 failed: dict[str, str] | None = None, timings_ms: dict[str, float] | None = None) -> None:
        super().__init__(items)
        self.partial = partial
        self.truncated = truncated
        self.shards = list(shards)
        self.failed = dict(failed or {})
        self.timings_ms = dict(timings_ms or {})

    def describe(self) -> dict[str, Any]:
        return {"queried": list(self.shards), "partial": self.partial, "truncated": self.truncated, "failed": dict(self.failed)}


def rrf_merge(per_shard: Sequence[tuple[ShardSpec, Sequence[Any]]], *, k: int, rrf_k: int = RRF_K) -> list[Any]:
    """Fuse per-shard rankings by rank (``1 / (rrf_k + rank)``); ties go to the higher in-shard score, then the lower
    ordinal. Every memory lives in exactly one shard, so its fused score is its own shard's rank term."""
    scored: list[tuple[float, float, int, int, Any]] = []
    for spec, results in per_shard:
        for rank, r in enumerate(results):
            scored.append((1.0 / (rrf_k + rank + 1), float(getattr(r, "score", 0.0) or 0.0), spec.ordinal, rank, r))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
    return [x[4] for x in scored[:max(0, k)]]


def channel_rrf_merge(per_shard: Sequence[tuple[ShardSpec, Sequence[Any]]], *, k: int, config: Any) -> list[Any]:
    """Alternative merge (``merge="channel"``): rank fusion per retrieval *channel* across shards, then the retriever's own
    priors. Every candidate carries its channel values (cosine, bm25, graph score) and priors (importance, recency, name
    boosts); the vector channel is comparable across shards (one embedding space), BM25 and graph scores only roughly (each
    shard has its own statistics), so channel lists are fused by rank exactly as one store fuses them. This reproduces the
    single-store ranking much more closely than shard-level RRF (research/ingest_bench measures both)."""
    from NeuralGraph.chat_memory.retrieval import rrf
    cands: dict[str, tuple[ShardSpec, Any]] = {}
    for spec, results in per_shard:
        for r in results:
            cands.setdefault(r.memory.memory_id, (spec, r))
    rankings, weights = [], []
    for ch, w in (("vector", config.weight_vector), ("keyword", config.weight_keyword), ("graph", config.weight_graph)):
        lst = sorted((x for x in cands.values() if ch in x[1].channels), key=lambda x: (-float(x[1].channels[ch]), x[0].ordinal))
        if lst:
            rankings.append([x[1].memory.memory_id for x in lst])
            weights.append(w)
    fused = rrf(rankings, weights, config.rrf_k)
    scored = []
    for mid, (spec, r) in cands.items():
        ch = r.channels
        s = fused.get(mid, 0.0)
        s *= (1 - config.importance_weight) + config.importance_weight * float(ch.get("importance", 0.5))
        s *= (1 - config.recency_weight) + config.recency_weight * float(ch.get("recency", 0.5))
        s *= float(ch.get("subject", ch.get("entity", 1.0)))
        scored.append((s, float(r.score or 0.0), spec.ordinal, mid, r))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
    return [x[4] for x in scored[:max(0, k)]]


class ShardedRetriever:
    def __init__(self, shards: ShardSet, *, merge: str = "rrf") -> None:
        if merge not in ("rrf", "channel"):
            raise ValueError("merge must be rrf or channel")
        self.shards = shards
        self.control = shards.control
        self.merge = merge

    async def search(self, query: str, *, k: int = 10, since: str | None = None, until: str | None = None,
                     allowed_for: Callable[[ShardSpec], set[str] | None] | None = None, max_shards: int = MAX_SHARDS_PER_HOLDER,
                     per_shard_timeout: float = PER_SHARD_TIMEOUT, fetch_k: int | None = None, domain_ids: Sequence[str] | None = None,
                     filters: dict[str, Any] | None = None) -> ShardedResults:
        router = ShardRouter(self.shards, taxonomy=self.control.taxonomy())
        specs, truncated = router.shards_for_query(domain_ids=domain_ids, since=since, until=until, max_shards=max_shards)
        try:
            qvec = [float(x) for x in (await self.control.llm.embed(query) or [])] or None   # once, for every shard
        except Exception as exc:
            logger.warning("query embedding failed for holder %s (%s); keyword and graph channels only", self.shards.holder_id, type(exc).__name__)
            qvec = None
        per_k = int(fetch_k or max(1, k) * 2)
        timings: dict[str, float] = {}
        ok: list[tuple[ShardSpec, list[Any]]] = []
        failed: dict[str, str] = {}
        loop = asyncio.get_running_loop()
        # Shards are searched one after another on the holder's read worker thread, not in parallel threads: retrieval is
        # Python-heavy and every SQLite call releases the GIL, so parallel threads convoy on it (measured ~6x slower than
        # one thread in research/ingest_bench). The worker keeps the event loop free, and each shard's wait is bounded:
        # a shard that overruns its timeout is reported and its worker abandoned (it finishes in the background), so a
        # stalled shard never holds up the next one.
        for spec in specs:
            t0 = time.perf_counter()
            try:
                allowed = allowed_for(spec) if allowed_for is not None else None
                if allowed is not None and not allowed:
                    ok.append((spec, []))
                    continue
                reader = self.shards.reader(spec.shard_id)
                kw: dict[str, Any] = {**(filters or {}), "k": per_k, "since": since, "until": until, "query_embedding": qvec, "allowed_ids": allowed}
                if qvec is None:
                    kw["channels"] = "KG"                   # never embed again inside the worker thread
                fut = loop.run_in_executor(self.shards.read_executor(), functools.partial(reader.search_sync, query, wait=per_shard_timeout, **kw))
                res = await asyncio.wait_for(fut, per_shard_timeout)
                out = [r for r in res if r.memory.status == "active"][:per_k]
                for r in out:
                    r.shard_id = spec.shard_id
                ok.append((spec, out))
            except (asyncio.TimeoutError, TimeoutError):
                failed[spec.shard_id] = "timeout"
                self.shards.replace_read_executor()
            except Exception as exc:
                failed[spec.shard_id] = type(exc).__name__
                logger.warning("shard %s of holder %s failed a search: %s", spec.shard_id, self.shards.holder_id, type(exc).__name__)
            finally:
                ms = (time.perf_counter() - t0) * 1000
                timings[spec.shard_id] = round(ms, 3)
                self.shards.observe(spec.shard_id, "query", ms)
        merged = rrf_merge(ok, k=k) if self.merge == "rrf" else channel_rrf_merge(ok, k=k, config=self.control.retriever.config)
        return ShardedResults(merged, partial=bool(failed), truncated=truncated, shards=[s.shard_id for s in specs], failed=failed, timings_ms=timings)


# ---------------------------------------------------------------------------------------------- traversal (§7.5)
class GraphTraverser:
    """Breadth-first paths over one holder's edges, across its shards. Local to the holder: it never crosses into
    another holder (cross-holder connections exist only as Mycelic claims and lineage, §8.6)."""

    def __init__(self, shards: ShardSet) -> None:
        self.shards = shards
        self.control = shards.control

    def _edges(self, frontier: set[str], specs: Sequence[ShardSpec], audience: Any, acl_cache: dict[tuple[str, str], bool]) -> dict[tuple[str, str, str], dict[str, Any]]:
        from .acl import Audience
        aud = Audience.from_payload(audience)
        ids = sorted(frontier)
        ph = ",".join("?" * len(ids))
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        for spec in specs:
            store = self.control if spec.shard_id == "s0" else self.shards.store(spec.shard_id)
            c = store.store._conn
            rels = c.execute(f"SELECT relation_id, subject_id, predicate, object_id FROM relations WHERE status<>'retracted' "
                             f"AND (subject_id IN ({ph}) OR object_id IN ({ph}))", (*ids, *ids)).fetchall()
            if not rels:
                continue
            by_id = {r["relation_id"]: (r["subject_id"], r["predicate"], r["object_id"]) for r in rels}
            rid_list = list(by_id)
            for i in range(0, len(rid_list), 500):
                part = rid_list[i:i + 500]
                for ev in c.execute(f"SELECT relation_id, record_id, confidence, modality, visibility FROM relation_evidence "
                                    f"WHERE relation_id IN ({','.join('?' * len(part))})", part):
                    key = by_id[ev["relation_id"]]
                    e = edges.setdefault(key, {"subject": key[0], "predicate": key[1], "object": key[2], "confidence": 0.0,
                                               "modalities": Counter(), "shards": set(), "traversable": False, "evidence": 0})
                    e["modalities"][ev["modality"]] += 1
                    e["shards"].add(spec.shard_id)
                    if ev["modality"] == "negated" or ev["visibility"] != "public":
                        continue
                    ck = (spec.shard_id, ev["record_id"])
                    if ck not in acl_cache:
                        acl = self.control._record_acl_sync(c, ev["record_id"])
                        acl_cache[ck] = acl is not None and self.control._acl_allows(acl, aud)[0]
                    if acl_cache[ck]:
                        e["traversable"] = True
                        e["evidence"] += 1
                        e["confidence"] = max(e["confidence"], float(ev["confidence"]))
        return edges

    def paths(self, start_entities: Iterable[str], *, targets: Iterable[str] | None = None, max_depth: int = 3, max_edges: int = 300,
              audience: Any = None, domain_ids: Sequence[str] | None = None, max_shards: int = MAX_SHARDS_PER_HOLDER) -> dict[str, Any]:
        router = ShardRouter(self.shards, taxonomy=self.control.taxonomy())
        specs, cut = router.shards_for_query(domain_ids=domain_ids, max_shards=max_shards)
        start = [s for s in dict.fromkeys(start_entities) if s]
        want = set(targets) if targets is not None else None
        parent: dict[str, tuple[str, tuple[str, str, str]]] = {}
        seen_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        visited = set(start)
        frontier = set(start)
        truncated = False
        used = 0
        acl_cache: dict[tuple[str, str], bool] = {}
        for _depth in range(max(0, int(max_depth))):
            if not frontier:
                break
            nxt: set[str] = set()
            for key, e in sorted(self._edges(frontier, specs, audience, acl_cache).items()):
                if not e["traversable"]:
                    continue                                   # restricted, negated or hypothetical-only: not followed, not shown
                if used >= max_edges:
                    truncated = True
                    break
                used += 1
                seen_edges[key] = e
                s, _p, o = key
                for here, there in ((s, o), (o, s)):
                    if here in frontier and there not in visited:
                        visited.add(there)
                        parent[there] = (here, key)
                        nxt.add(there)
            if truncated:
                break
            frontier = nxt
        ends = [n for n in visited if n not in start and (want is None or n in want)]
        paths = []
        for node in sorted(ends):
            chain: list[dict[str, Any]] = []
            cur = node
            while cur in parent:
                prev, key = parent[cur]
                e = seen_edges[key]
                chain.append({"subject": e["subject"], "predicate": e["predicate"], "object": e["object"], "confidence": round(e["confidence"], 4),
                              "modalities": dict(e["modalities"]), "shards": sorted(e["shards"]), "evidence": e["evidence"]})
                cur = prev
            chain.reverse()
            conf = 1.0
            for x in chain:
                conf *= x["confidence"]
            weakest = min(chain, key=lambda x: x["confidence"]) if chain else None
            paths.append({"from": cur, "to": node, "edges": chain, "confidence": round(conf, 6), "weakest": weakest})
        paths.sort(key=lambda p: (-p["confidence"], len(p["edges"]), p["to"]))
        return {"start": start, "paths": paths, "edges_traversed": used, "truncated": truncated or cut, "shards": [s.shard_id for s in specs]}
