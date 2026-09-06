"""Mega search: vector + BM25 keyword + entity-graph hop, fused by Reciprocal Rank Fusion.

Design (borrowed from the 2025-26 hybrid-retrieval literature):
- Vector channel: cosine over message nodes AND question+reply pair nodes (pairs mapped back to messages).
- Keyword channel: BM25 over message text (exact entities, numbers, rare words).
- Graph channel: HippoRAG-style Personalized PageRank over a bipartite message<->entity graph plus
  message<->next-message temporal edges, seeded by messages that share entities with the query and by
  the top hits of the other two channels. Scores propagate one or two hops to messages that share
  entities with the seeds.
- Fusion: RRF (Cormack et al.) so channels with incomparable score scales can be combined.
All per-conversation; speaker routing is applied as a pool filter before every channel.
No LLM calls.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np
from rank_bm25 import BM25Okapi

_STOP = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our", "with",
    "that", "this", "have", "from", "they", "what", "when", "where", "which", "who", "how", "did", "does", "has",
    "his", "she", "him", "them", "been", "were", "will", "would", "could", "should", "about", "into", "your",
    "just", "like", "some", "than", "then", "there", "their", "also", "very", "really", "much", "many", "more",
    "yeah", "yes", "hey", "thanks", "thank", "sounds", "sure", "good", "great", "awesome", "love", "know",
}


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOP]


def entities_of(text: str) -> set[str]:
    """Cheap entity set: capitalized tokens (names, places, titles) + rare-ish content words."""
    caps = {w.lower() for w in re.findall(r"\b[A-Z][a-zA-Z]+\b", text)} - {"i"}
    words = set(tokenize(text))
    return caps | {w for w in words if len(w) >= 5}


def rrf(rankings: list[list[str]], weights: list[float] | None = None, k: int = 60) -> list[tuple[str, float]]:
    weights = weights or [1.0] * len(rankings)
    score: dict[str, float] = defaultdict(float)
    for ranking, w in zip(rankings, weights):
        for r, doc_id in enumerate(ranking):
            score[doc_id] += w / (k + r + 1)
    return sorted(score.items(), key=lambda x: -x[1])


class MegaIndex:
    def __init__(self, nodes, pair_nodes=None, kg: dict | None = None, kg_relation_weight: float = 0.5):
        """kg: output of NeuralGraph.kg_extractor.extract_conversation (LLM-built entities + triples),
        aligned by message index with `nodes`. When given, the graph channel uses those entities instead
        of the regex entity set, plus entity-entity relation edges for 2-hop propagation."""
        self.nodes = list(nodes)
        self.pairs = list(pair_nodes or [])
        self.by_id = {n.node_id: n for n in self.nodes}
        self.order = {n.node_id: i for i, n in enumerate(self.nodes)}
        # keyword channel
        self._bm25_tokens = [tokenize(n.content) for n in self.nodes]
        self.bm25 = BM25Okapi(self._bm25_tokens)
        # vector channel
        M = np.array([n.embedding for n in self.nodes], dtype=np.float32)
        self.M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
        if self.pairs:
            P = np.array([p.embedding for p in self.pairs], dtype=np.float32)
            self.P = P / (np.linalg.norm(P, axis=1, keepdims=True) + 1e-9)
        else:
            self.P = None
        # graph channel: message <-> entity, message <-> neighbour message
        self.kg = kg
        self.ent_ent: dict[str, set[str]] = defaultdict(set)
        self.kg_relation_weight = kg_relation_weight
        if kg:
            per_msg = {m["idx"]: m for m in kg["messages"]}
            self.msg_ents = {n.node_id: set(per_msg.get(i, {}).get("entities", [])) for i, n in enumerate(self.nodes)}
            for m in kg["messages"]:
                for s_, r_, o_ in m.get("triples", []):
                    self.ent_ent[s_].add(o_)
                    self.ent_ent[o_].add(s_)
        else:
            self.msg_ents = {n.node_id: entities_of(n.content) for n in self.nodes}
        self.ent_msgs: dict[str, set[str]] = defaultdict(set)
        for mid, ents in self.msg_ents.items():
            for e in ents:
                self.ent_msgs[e].add(mid)
        # drop hub entities that touch too many messages (they carry no signal)
        n_msgs = max(1, len(self.nodes))
        hub_cap = max(3, (0.10 if kg else 0.05) * n_msgs)   # speakers themselves are dropped as hubs either way
        self.ent_msgs = {e: ms for e, ms in self.ent_msgs.items() if len(ms) <= hub_cap}
        self.msg_ents = {mid: {e for e in ents if e in self.ent_msgs} for mid, ents in self.msg_ents.items()}
        self.next_id = {self.nodes[i].node_id: self.nodes[i + 1].node_id for i in range(len(self.nodes) - 1)}
        self.prev_id = {v: k for k, v in self.next_id.items()}

    # ---- channels -------------------------------------------------------------------------
    def vector_rank(self, qvec, pool: set[str], k: int) -> list[str]:
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-9)
        scored: dict[str, float] = {}
        sims = self.M @ q
        for i, n in enumerate(self.nodes):
            if n.node_id in pool:
                scored[n.node_id] = float(sims[i])
        if self.P is not None:
            psims = self.P @ q
            for j, p in enumerate(self.pairs):
                for mid in p.metadata.get("pair_members", []):
                    if mid in pool:
                        scored[mid] = max(scored.get(mid, -1.0), float(psims[j]))
        return [m for m, _ in sorted(scored.items(), key=lambda x: -x[1])[:k]]

    def keyword_rank(self, query: str, pool: set[str], k: int) -> list[str]:
        toks = tokenize(query)
        if not toks:
            return []
        scores = self.bm25.get_scores(toks)
        ranked = sorted(((self.nodes[i].node_id, float(s)) for i, s in enumerate(scores)
                         if self.nodes[i].node_id in pool and s > 0), key=lambda x: -x[1])
        return [m for m, _ in ranked[:k]]

    def graph_rank(self, query: str, seeds: list[str], pool: set[str], k: int,
                   alpha: float = 0.5, iters: int = 12, hop_temporal: bool = True) -> list[str]:
        """Personalized PageRank over the message-entity bipartite graph (+ temporal chain)."""
        q_ents = entities_of(query) & set(self.ent_msgs.keys())
        if self.kg:  # also match multi-word KG entity names appearing in the query
            ql = query.lower()
            q_ents |= {e for e in self.ent_msgs if len(e) >= 4 and e in ql}
        seed_w: dict[str, float] = defaultdict(float)
        for e in q_ents:
            for mid in self.ent_msgs[e]:
                if mid in pool:
                    seed_w[mid] += 1.0 / len(self.ent_msgs[e])
        for r, mid in enumerate(seeds[:10]):
            if mid in pool:
                seed_w[mid] += 1.0 / (r + 1)
        if not seed_w:
            return []
        total = sum(seed_w.values())
        p0 = {m: w / total for m, w in seed_w.items()}
        p = dict(p0)
        for _ in range(iters):
            nxt: dict[str, float] = defaultdict(float)
            for mid, mass in p.items():
                ents = self.msg_ents.get(mid, set())
                nbrs: list[str] = []
                for e in ents:
                    nbrs.extend(self.ent_msgs[e])
                    if self.ent_ent:  # 2-hop via typed relations: msg -> entity -> related entity -> its msgs
                        for e2 in self.ent_ent.get(e, ()):
                            ms2 = self.ent_msgs.get(e2)
                            if ms2:
                                nbrs.extend(list(ms2) * max(1, int(round(self.kg_relation_weight * 2))))
                if hop_temporal:
                    for t in (self.next_id.get(mid), self.prev_id.get(mid)):
                        if t:
                            nbrs.append(t)
                nbrs = [x for x in nbrs if x in pool and x != mid]
                if not nbrs:
                    nxt[mid] += mass * (1 - alpha)
                    continue
                share = mass * (1 - alpha) / len(nbrs)
                for x in nbrs:
                    nxt[x] += share
            for m, w in p0.items():
                nxt[m] += alpha * w
            p = nxt
        return [m for m, _ in sorted(p.items(), key=lambda x: -x[1])[:k]]

    # ---- fused retrieval ------------------------------------------------------------------
    def retrieve(self, query: str, qvec, pool_ids: set[str] | None = None, k: int = 50,
                 channels: str = "VKG", weights: dict[str, float] | None = None,
                 extra_rankings: list[list[str]] | None = None, depth: int = 100):
        pool = pool_ids if pool_ids is not None else set(self.by_id)
        weights = weights or {}
        rankings, ws = [], []
        v = self.vector_rank(qvec, pool, depth) if "V" in channels else []
        kw = self.keyword_rank(query, pool, depth) if "K" in channels else []
        if "V" in channels:
            rankings.append(v); ws.append(weights.get("V", 1.0))
        if "K" in channels:
            rankings.append(kw); ws.append(weights.get("K", 1.0))
        if "G" in channels:
            seeds = [m for m, _ in rrf([v, kw])[:10]] if (v or kw) else []
            g = self.graph_rank(query, seeds, pool, depth)
            rankings.append(g); ws.append(weights.get("G", 1.0))
        for extra in extra_rankings or []:
            rankings.append(extra); ws.append(weights.get("X", 1.0))
        fused = rrf(rankings, ws)
        return [(self.by_id[m], s) for m, s in fused[:k]]
