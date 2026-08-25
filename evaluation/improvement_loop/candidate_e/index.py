"""Candidate E: immutable raw-turn index with genuine entity incidence.

Deliberately minimal. This is a Mem0-style incidence baseline, not a typed-graph
redesign: index the raw conversation turns, link entities that are actually
mentioned in them, and let entity and lexical matches *introduce* candidates that
semantic top-k missed.

What is indexed is the immutable turn, never a derived summary or profile fact.
A profile fact that never enters retrieval cannot help retrieval, and depending
on one would make the result unattributable.

Scope isolation is enforced structurally rather than by convention: every unit
carries its conversation id, and the retriever filters to a single conversation
before scoring. First-person pronouns resolve to the turn's own speaker within
that conversation only, so "my sister" in conversation 3 can never bind to a
speaker in conversation 7.

`ENTITY` here means *this string is mentioned in this turn*. Same-speaker
proximity is not an entity relation and is not used.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable

RAW = Path("evaluation/locomo/locomo10.json")

# Words that look capitalised but never name an entity. Kept small and generic:
# an aggressive stoplist would silently delete real entities.
_STOP = {
    "i", "a", "an", "the", "and", "or", "but", "so", "then", "hey", "hi", "hello",
    "yeah", "yes", "no", "oh", "wow", "thanks", "thank", "you", "your", "yours",
    "we", "us", "our", "they", "them", "their", "he", "she", "it", "this", "that",
    "these", "those", "there", "here", "what", "when", "where", "who", "why",
    "how", "is", "are", "was", "were", "be", "been", "am", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "my", "me",
    "if", "of", "in", "on", "at", "to", "for", "with", "as", "by", "from", "just",
    "really", "very", "great", "good", "nice", "love", "like", "get", "got",
}

_FIRST_PERSON = re.compile(r"\b(i|me|my|mine|myself)\b", re.I)


@dataclass(frozen=True)
class Turn:
    """One immutable conversation turn."""
    uid: str                 # conv:dia_id — stable, unique
    conversation: str
    session: str
    dia_id: str
    speaker: str
    timestamp: str           # original session date_time, never ingestion time
    text: str
    blip_caption: str = ""

    @property
    def indexed_text(self) -> str:
        """Text actually searched: utterance plus any image caption."""
        if self.blip_caption:
            return f"{self.text} [image: {self.blip_caption}]"
        return self.text

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _session_number(key: str) -> int:
    m = re.search(r"session_(\d+)", key)
    return int(m.group(1)) if m else 0


def load_turns(path: Path = RAW) -> list[Turn]:
    """Every turn of every session of every conversation, in original order."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    turns: list[Turn] = []
    for conv_index, sample in enumerate(raw, start=1):
        conv = sample.get("conversation") or {}
        session_keys = sorted(
            (k for k in conv if re.fullmatch(r"session_\d+", k)),
            key=_session_number,
        )
        for skey in session_keys:
            entries = conv.get(skey)
            if not isinstance(entries, list):
                continue
            stamp = str(conv.get(f"{skey}_date_time") or "")
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                dia = str(entry.get("dia_id") or "")
                if not dia:
                    continue
                turns.append(Turn(
                    uid=f"{conv_index}:{dia}",
                    conversation=str(conv_index),
                    session=skey,
                    dia_id=dia,
                    speaker=str(entry.get("speaker") or ""),
                    timestamp=stamp,
                    text=str(entry.get("text") or ""),
                    blip_caption=str(entry.get("blip_caption") or ""),
                ))
    return turns


# --------------------------------------------------------------------------
# Entity extraction
# --------------------------------------------------------------------------

_QUOTED = re.compile(r'"([^"]{2,60})"|“([^”]{2,60})”')
_IDENTIFIER = re.compile(r"\b[A-Za-z]+[-_][A-Za-z0-9]+\b|\b[A-Z]{2,}\d*\b")
_PROPER_RUN = re.compile(r"\b([A-Z][a-z]{1,}(?:\s+(?:of|the|de|van|von)\s+)?"
                         r"(?:\s+[A-Z][a-z]{1,}){0,3})\b")


def extract_entities(turn: Turn, speakers: set[str]) -> set[str]:
    """Entities genuinely mentioned in this turn.

    Covers proper-noun runs (people, places, organisations), quoted phrases,
    compound topics and technical identifiers, plus the speaker aliases known
    for this conversation. First-person pronouns resolve to the turn's own
    speaker -- and only there, which is what keeps "my" from binding across
    conversations.
    """
    found: set[str] = set()
    body = turn.indexed_text

    for match in _QUOTED.finditer(body):
        phrase = (match.group(1) or match.group(2) or "").strip()
        if len(phrase) > 1:
            found.add(phrase.lower())

    for match in _IDENTIFIER.finditer(body):
        token = match.group(0).strip()
        if token.lower() not in _STOP and len(token) > 2:
            found.add(token.lower())

    for match in _PROPER_RUN.finditer(body):
        phrase = match.group(1).strip()
        words = [w for w in phrase.split() if w.lower() not in _STOP]
        if not words:
            continue
        cleaned = " ".join(words)
        if len(cleaned) < 2:
            continue
        # A run that is only a sentence-initial capital is not an entity.
        if len(words) == 1 and words[0].lower() in _STOP:
            continue
        found.add(cleaned.lower())
        # A multi-word run also yields its head token, so "Golden Gate Bridge"
        # is reachable from "bridge"-free queries mentioning "Golden Gate".
        if len(words) > 1:
            found.add(words[0].lower())

    # The speaker is an entity of their own turn, and first-person reference
    # resolves to them -- scoped to this conversation by construction.
    if turn.speaker:
        found.add(turn.speaker.lower())
    if _FIRST_PERSON.search(body) and turn.speaker:
        found.add(turn.speaker.lower())

    # Other known speakers of the same conversation, when named.
    lowered = body.lower()
    for name in speakers:
        if name and name.lower() in lowered:
            found.add(name.lower())

    return {e for e in found if e and e not in _STOP and len(e) > 1}


@dataclass
class TurnIndex:
    turns: list[Turn]
    by_uid: dict[str, Turn] = field(default_factory=dict)
    by_conversation: dict[str, list[Turn]] = field(default_factory=dict)
    entity_to_uids: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    uid_to_entities: dict[str, set[str]] = field(default_factory=dict)
    speakers: dict[str, set[str]] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        for t in self.turns:
            h.update(t.uid.encode())
            h.update(t.indexed_text.encode())
        return h.hexdigest()[:16]


def build_index(turns: Iterable[Turn] | None = None) -> TurnIndex:
    turns = list(turns if turns is not None else load_turns())
    idx = TurnIndex(turns=turns)
    for t in turns:
        idx.by_uid[t.uid] = t
        idx.by_conversation.setdefault(t.conversation, []).append(t)
        idx.speakers.setdefault(t.conversation, set()).add(t.speaker)

    for conv, conv_turns in idx.by_conversation.items():
        speakers = idx.speakers[conv]
        table: dict[str, set[str]] = defaultdict(set)
        for t in conv_turns:
            ents = extract_entities(t, speakers)
            idx.uid_to_entities[t.uid] = ents
            for e in ents:
                table[e].add(t.uid)
        # Entity tables are per conversation: an entity can never link a turn in
        # one conversation to a turn in another.
        idx.entity_to_uids[conv] = dict(table)
    return idx
