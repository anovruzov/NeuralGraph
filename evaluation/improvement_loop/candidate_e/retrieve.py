"""Union candidate generation with deterministic RRF fusion.

Three generators, each able to *introduce* a candidate the others missed:

* **BM25** — lexical. Recovers exact names, quoted phrases and identifiers that
  a dense encoder smooths away.
* **Entity incidence** — a turn is a candidate if it mentions an entity the
  question mentions. This is the generator that matters for Candidate E: it can
  surface a turn whose wording shares almost nothing with the question.
* **Semantic** — dense similarity, optional and cached.

Fusion is Reciprocal Rank Fusion: order-independent, deterministic, and it needs
no score calibration between generators whose scores are not comparable. Ties
break on uid so the same inputs always produce the same ranking.

Final ordering is relevance-first. Timestamps are attached as annotations and
never used to reorder the context -- global chronological sorting was measured
to move the rank-1 excerpt to median position 11 of 15 and was reverted.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from evaluation.improvement_loop.candidate_e.index import (
    TurnIndex,
    Turn,
    _STOP,
    extract_entities,
)

RRF_K = 60  # standard damping; larger K flattens the rank advantage


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in _STOP and len(t) > 1]


class BM25:
    """Okapi BM25 over one conversation's turns."""

    def __init__(self, turns: list[Turn], k1: float = 1.5, b: float = 0.75) -> None:
        self.turns = turns
        self.k1, self.b = k1, b
        self.docs = [tokenize(t.indexed_text) for t in turns]
        self.len = [len(d) for d in self.docs]
        self.avgdl = (sum(self.len) / len(self.len)) if self.len else 0.0
        self.df: Counter[str] = Counter()
        for d in self.docs:
            self.df.update(set(d))
        self.tf = [Counter(d) for d in self.docs]
        self.N = len(turns)

    def score(self, query: str) -> list[tuple[str, float]]:
        q = tokenize(query)
        if not q or not self.N:
            return []
        out: list[tuple[str, float]] = []
        for i, turn in enumerate(self.turns):
            s = 0.0
            for term in q:
                f = self.tf[i].get(term, 0)
                if not f:
                    continue
                idf = math.log(1 + (self.N - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denom = f + self.k1 * (1 - self.b + self.b * self.len[i] / (self.avgdl or 1))
                s += idf * (f * (self.k1 + 1)) / denom
            if s > 0:
                out.append((turn.uid, s))
        out.sort(key=lambda kv: (-kv[1], kv[0]))
        return out


@dataclass
class Candidate:
    uid: str
    sources: dict[str, int] = field(default_factory=dict)   # generator -> rank
    rrf: float = 0.0
    stage_scores: dict[str, float] = field(default_factory=dict)


class UnionRetriever:
    """BM25 + entity incidence (+ optional semantic), fused by RRF."""

    def __init__(self, index: TurnIndex, embedder: Any | None = None) -> None:
        self.index = index
        self.embedder = embedder
        self._bm25: dict[str, BM25] = {}

    def bm25_for(self, conversation: str) -> BM25:
        if conversation not in self._bm25:
            self._bm25[conversation] = BM25(self.index.by_conversation[conversation])
        return self._bm25[conversation]

    def question_entities(self, question: str, conversation: str) -> set[str]:
        """Entities mentioned by the question, restricted to this conversation."""
        probe = Turn(uid="q", conversation=conversation, session="", dia_id="",
                     speaker="", timestamp="", text=question)
        ents = extract_entities(probe, self.index.speakers.get(conversation, set()))
        # Keep only entities that actually occur in this conversation's table.
        table = self.index.entity_to_uids.get(conversation, {})
        return {e for e in ents if e in table}

    def entity_candidates(self, question: str, conversation: str,
                          limit: int = 200) -> list[tuple[str, float]]:
        """Turns mentioning the question's entities, scored by entity overlap.

        Rarer entities weigh more: a turn matching a name that appears in three
        turns is far more informative than one matching a speaker name that
        appears in three hundred.
        """
        table = self.index.entity_to_uids.get(conversation, {})
        ents = self.question_entities(question, conversation)
        if not ents:
            return []
        scores: dict[str, float] = defaultdict(float)
        for e in ents:
            uids = table.get(e) or ()
            if not uids:
                continue
            weight = math.log(1 + len(self.index.by_conversation[conversation]) / len(uids))
            for uid in uids:
                scores[uid] += weight
        out = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return out[:limit]

    def semantic_candidates(self, question: str, conversation: str,
                            limit: int = 100) -> list[tuple[str, float]]:
        if self.embedder is None:
            return []
        return self.embedder.search(question, conversation, limit)

    def retrieve(self, question: str, conversation: str, top_k: int = 20
                 ) -> list[Candidate]:
        generators = {
            "bm25": self.bm25_for(conversation).score(question)[:100],
            "entity": self.entity_candidates(question, conversation),
            "semantic": self.semantic_candidates(question, conversation),
        }
        pool: dict[str, Candidate] = {}
        for name, ranked in generators.items():
            for rank, (uid, score) in enumerate(ranked, start=1):
                cand = pool.setdefault(uid, Candidate(uid=uid))
                cand.sources[name] = rank
                cand.stage_scores[name] = round(float(score), 6)
                cand.rrf += 1.0 / (RRF_K + rank)

        ordered = sorted(pool.values(), key=lambda c: (-c.rrf, c.uid))
        return ordered[:top_k]

    def introduced_by(self, candidates: Iterable[Candidate], generator: str
                      ) -> list[str]:
        """Candidates this generator contributed that no other generator found."""
        return [c.uid for c in candidates
                if generator in c.sources and len(c.sources) == 1]
