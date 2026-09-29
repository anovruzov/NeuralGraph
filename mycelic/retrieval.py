"""Retrieval over organizational memory: BM25 keyword ranking scoped to what the caller may see.

The index is built per organization from every active memory and rebuilt lazily when the store's revision
changes (the same strategy as ``NeuralGraph.chat_memory.retrieval``, whose tokenizer is reused).  Ranking is
BM25 with a mild boost for higher organizational layers, so an enterprise conclusion outranks one of the raw
observations it was built from when both match the query, while a precise raw observation still wins when the
conclusion does not mention the query's terms.  Visibility is a predicate evaluated per row (see
``auth.memory_visible``), so an agent never sees a hit it could not ``GET``.

Embeddings are deliberately not part of this slice: the deployment must run without any model server.
``Retriever`` is the seam where a vector channel plus reciprocal-rank fusion would go.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import math
from collections import Counter

from NeuralGraph.chat_memory.textutil import tokenize

from .hierarchy import LAYERS
from .models import Memory
from .store import MycelicStore

LAYER_BOOST = 0.15      # per layer above 'agent'


@dataclass
class Hit:
    memory: Memory
    score: float
    bm25: float
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {"memory": self.memory.to_dict(), "score": round(self.score, 4), "bm25": round(self.bm25, 4),
                "explanation": self.explanation}


def _doc_tokens(m: Memory) -> list[str]:
    parts = [m.text, m.topic or "", m.entity or "", m.slot or "", m.kind or ""]
    return tokenize(" ".join(parts)) or ["_empty_"]


class BM25:
    """Okapi BM25 with the Lucene-style IDF ``log(1 + (N - df + 0.5) / (df + 0.5))``, which is always positive.

    ``rank_bm25`` was tried first; its IDF goes negative for terms present in most documents, which makes every score
    of a one- or two-memory organization negative and the retriever return nothing for a brand-new deployment.
    """

    def __init__(self, docs: list[list[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tf: list[Counter[str]] = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter[str] = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1.0 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def get_scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, dl in zip(self.tf, self.len):
            score = 0.0
            norm = self.k1 * (1.0 - self.b + self.b * (dl / self.avg if self.avg else 1.0))
            for t in query:
                f = tf.get(t)
                if f:
                    score += self.idf[t] * f * (self.k1 + 1.0) / (f + norm)
            out.append(score)
        return out


class _OrgIndex:
    """Tokens of every active memory of an organization; the BM25 statistics are computed per caller view."""

    def __init__(self, rows: list[Memory]) -> None:
        self.rows = rows
        self.docs = [_doc_tokens(m) for m in rows]


class Retriever:
    def __init__(self, store: MycelicStore) -> None:
        self.store = store
        self._cache: dict[str, tuple[int, _OrgIndex]] = {}

    def _index(self, org_id: str) -> _OrgIndex:
        cached = self._cache.get(org_id)
        if cached is not None and cached[0] == self.store.revision:
            return cached[1]
        rows = self.store.visible_rows(org_id, agent_path=None, team_path=None, scope=None)
        index = _OrgIndex(rows)
        self._cache[org_id] = (self.store.revision, index)
        return index

    def invalidate(self) -> None:
        self._cache.clear()

    def search(self, org_id: str, query: str, *, visible: Callable[[Memory], bool], scope: str | None = None,
               min_layer: str = "agent", k: int = 10, topic: str | None = None, entity: str | None = None) -> list[Hit]:
        index = self._index(org_id)
        if not index.rows:
            return []
        min_idx = LAYERS.index(min_layer)
        q = tokenize(query or "")
        # BM25 statistics (IDF, average length) are computed over the caller's view only, so memories the caller
        # cannot read never influence its ranking, not even through term statistics
        view = [i for i, m in enumerate(index.rows) if visible(m)]
        if not view:
            return []
        scores = BM25([index.docs[i] for i in view]).get_scores(q) if q else [0.0] * len(view)
        hits: list[Hit] = []
        for i, s in zip(view, scores):
            m = index.rows[i]
            if scope and not (m.scope == scope or m.scope.startswith(scope + "/")):
                continue
            if LAYERS.index(m.layer) < min_idx:
                continue
            if topic and m.topic != topic:
                continue
            if entity and m.entity != entity:
                continue
            if q and s <= 0.0:
                continue
            boost = 1.0 + LAYER_BOOST * LAYERS.index(m.layer)
            score = float(s) * boost if q else boost * (1.0 + m.confidence)
            why = f"bm25={float(s):.3f} × layer boost {boost:.2f} ({m.layer})" if q else f"no query terms: ranked by layer and confidence ({m.layer})"
            hits.append(Hit(memory=m, score=score, bm25=float(s), explanation=why))
        # a memory promoted unchanged to a single-child parent unit is the same knowledge: keep the highest copy only
        promoted_away = {h.memory.metadata.get("promoted_from") for h in hits
                         if h.memory.layer != "agent" and h.memory.metadata.get("promoted_from")}
        hits = [h for h in hits if h.memory.memory_id not in promoted_away]
        hits.sort(key=lambda h: (-h.score, -LAYERS.index(h.memory.layer), h.memory.created_at, h.memory.memory_id))
        return hits[: max(1, int(k))]
