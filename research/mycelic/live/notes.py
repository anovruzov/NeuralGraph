"""Private note stores: the only door between the world and a live agent.

``NoteStore`` renders every user's notes to text ONCE, with the generator's
own renderer (``Corpus.text``), when it is built.  After that it never touches
the record array again: an agent receives exactly

    (agent_id, [(note_index, text), ...])

for its own notes and nothing else - no record arrays, no gold, no patterns,
no hidden fields.  The note-index -> record-id mapping (the evidence pointer)
and the author's org node stay code-side, so a claim the agent returns can be
turned into an ``ExtractResult`` row that points at its source record.

A central reader (the A2 baseline) reads raw notes of many users; it gets the
same rendered strings through ``text_of`` and the same code-side mapping.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..corpus import Corpus

_HEAD = re.compile(r"^\[d(\d+)\] (\S+) :: (.*)$")


def split_note(text: str) -> Tuple[int, str, str]:
    """Observable header parse of a rendered note: (day, author, body)."""
    m = _HEAD.match(text)
    if not m:
        raise ValueError(f"not a rendered note: {text[:60]!r}")
    return int(m.group(1)), m.group(2), m.group(3)


def echo_signature(text: str, catalog: "set[str]") -> int:
    """Near-duplicate signature computed from the note TEXT alone.

    Recipe (fixed before any live run; documented in live/README.md):
      * take the body after the ``[dNNN] author ::`` header (an echo is a
        re-report by another author on a later day, so neither is part of
        the event's wording);
      * remove a trailing ``not`` and remember it (re-appended at the end);
      * drop the final remaining token (the last filler word, which the
        renderer paraphrases independently per note);
      * drop every catalog entity name after the first one (a secondary
        mention is context the re-reporter adds, not the event's wording);
      * hash what is left (sha256 -> positive int64).
    On this generator's template text this is an easy near-duplicate problem:
    an echo repeats the event's phrase, subject and filler words verbatim
    (the renderer keys them on the event), so the recipe recovers event
    identity almost exactly; real forwarded / paraphrased reports would be
    much harder.  The evaluator measures its accuracy against true event ids
    (live/score.py, ``signature_accuracy``)."""
    body = text.split(" :: ", 1)[1] if " :: " in text else text
    toks = body.split()
    neg = bool(toks) and toks[-1] == "not"
    if neg:
        toks = toks[:-1]
    toks = toks[:-1]
    out: List[str] = []
    seen_ent = False
    for t in toks:
        if t in catalog:
            if seen_ent:
                continue
            seen_ent = True
        out.append(t)
    if neg:
        out.append("not")
    h = hashlib.sha256(" ".join(out).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "little") & 0x3FFF_FFFF_FFFF_FFFF


class NoteStore:
    """Every user's notes, rendered once; agents see only their own text."""

    def __init__(self, corpus: Corpus, users: Optional[Sequence[int]] = None):
        org = corpus.org
        self.catalog: Tuple[str, ...] = tuple(corpus.entities)
        self._catalog_set = set(self.catalog)
        uids = list(org.user_ids) if users is None else list(users)
        index = {u: i for i, u in enumerate(org.user_ids)}
        self._agents: List[str] = []
        self._uid: Dict[str, int] = {}
        self._rids: Dict[str, np.ndarray] = {}
        self._text: Dict[str, Tuple[str, ...]] = {}
        self._rid_text: Dict[int, str] = {}
        self._rid_agent: Dict[int, str] = {}
        for u in uids:
            aid = org.nodes[int(u)].name
            rids = corpus.recs_of_user(index[int(u)]).astype(np.int64)
            texts = tuple(corpus.text(int(r)) for r in rids)
            self._agents.append(aid)
            self._uid[aid] = int(u)
            self._rids[aid] = rids
            self._text[aid] = texts
            for r, t in zip(rids.tolist(), texts):
                self._rid_text[r] = t
                self._rid_agent[r] = aid
        # the store keeps no reference to the corpus or its record array

    # ---- what an agent may receive ------------------------------------
    def agents(self) -> List[str]:
        return list(self._agents)

    def notes(self, agent_id: str) -> List[Tuple[int, str]]:
        """The agent's own notes, as (note_index, text).  Nothing else."""
        return list(enumerate(self._text[agent_id]))

    # ---- code-side only (never sent to a model) -----------------------
    def uid_of(self, agent_id: str) -> int:
        return self._uid[agent_id]

    def rid_of(self, agent_id: str, note_index: int) -> int:
        return int(self._rids[agent_id][note_index])

    def rids_of(self, agent_id: str) -> np.ndarray:
        return self._rids[agent_id].copy()

    def text_of(self, rid: int) -> str:
        return self._rid_text[int(rid)]

    def author_of(self, rid: int) -> str:
        return self._rid_agent[int(rid)]

    def all_rids(self) -> np.ndarray:
        return np.array(sorted(self._rid_text), dtype=np.int64)

    def signature(self, rid: int) -> int:
        return echo_signature(self._rid_text[int(rid)], self._catalog_set)

    def n_notes(self, agents: Optional[Iterable[str]] = None) -> int:
        a = self._agents if agents is None else agents
        return sum(len(self._text[x]) for x in a)
