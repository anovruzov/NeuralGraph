"""Scripted fake LLM used by the CLI ``--fake-llm`` flag, the demo and the tests.

The responder recognises the three prompt kinds by their headings and answers with plausible JSON built
from the NEW MESSAGES block itself: first-person sentences are rewritten to the third person, a few
keyword rules pick the memory kind and some typed relations, and duplicates/contradictions are decided by
simple text rules. It is deliberately dumb and deterministic, so tests are stable and the demo runs
without any model server; the real pipeline uses Qwen through ``NeuralGraph.llm_backend``.
"""
from __future__ import annotations

import json
import re

from .llm import FakeLLMClient

_MSG_RE = re.compile(r"^(\d+)\. \[([^\]]+)\]: (.*)$", re.M)
_CAP_RE = re.compile(r"\b([A-Z][a-zé]{2,})\b")
_NEW_BLOCK_RE = re.compile(r"NEW MESSAGES \(date: ([^)]*)\):\n(.*?)\n\nReturn ONLY JSON", re.S)
_ANNOT_RE = re.compile(r"\s*\[=\s*[^\]]*\]")
_SKIP_CAPS = {"the", "yes", "last", "next", "this", "my", "i", "also", "quick", "good", "rough", "remind", "mostly",
              "perfect", "actually", "update", "trip", "nice", "hi", "hey", "congrats", "welcome", "five", "day",
              "sunday", "saturday", "september", "october", "june", "april", "march", "may", "july", "august"}
_PLACES = {"Berlin", "Tokyo", "Paris", "London", "Baku", "Kreuzberg", "Hamburg", "Osaka", "Kyoto", "Prenzlauer"}
_ORGS = {"Charité", "Charite", "Lincoln", "TU"}


def _new_messages(prompt: str) -> tuple[str, list[tuple[int, str, str]]]:
    m = _NEW_BLOCK_RE.search(prompt)
    if not m:
        return "", []
    out = []
    for idx, who, text in _MSG_RE.findall(m.group(2)):
        out.append((int(idx), who.replace(" (assistant)", ""), _ANNOT_RE.sub("", text)))
    return m.group(1), out


def third_person(text: str, name: str) -> str:
    """Rewrite a first-person sentence as a third-person statement about ``name`` (cheap heuristic)."""
    first = name.split()[0]
    s = text.strip()
    subs = [
        (r"\bI am\b", f"{name} is"), (r"\bI'm\b", f"{name} is"), (r"\bI've\b", f"{name} has"), (r"\bI'd\b", f"{name} would"),
        (r"\bI'll\b", f"{name} will"), (r"\bI was\b", f"{name} was"), (r"\bI have\b", f"{name} has"),
        (r"\bI\b", name), (r"\bmy\b", f"{first}'s"), (r"\bMy\b", f"{first}'s"), (r"\bme\b", first), (r"\bmine\b", f"{first}'s"),
        (r"\bwe\b", first), (r"\bWe\b", first), (r"\bour\b", f"{first}'s"),
    ]
    for pat, rep in subs:
        s = re.sub(pat, rep, s)
    s = re.sub(rf"{re.escape(name)} (\w+)s\b", lambda m: f"{name} {m.group(1)}s", s)   # keep verb agreement as-is
    s = s[0].upper() + s[1:] if s else s
    if not s.endswith((".", "!", "?")):
        s += "."
    return s


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if len(p) > 12]


def _kind(low: str) -> str:
    if any(w in low for w in ("plan", "signed up", "thinking about", "going to", "will ", "next ", "want to")):
        return "plan"
    if any(w in low for w in ("love", "like", "prefer", "hate", "favorite", "favourite", "vegetarian", "vegan", "allergic")):
        return "preference"
    if any(w in low for w in ("moved", "went", "visited", "started", "finished", "did it", "yesterday", "last ", "switching")):
        return "event"
    if any(w in low for w in ("birthday", "is my", "i'm a", "work as", "nurse", "sister", "brother", "mother", "daughter", "cat", "dog")):
        return "identity" if any(w in low for w in ("i'm a", "work as", "nurse")) else "fact"
    return "fact"


def _entities(text: str) -> list[dict]:
    ents = []
    for c in dict.fromkeys(_CAP_RE.findall(text)):
        if c.lower() in _SKIP_CAPS:
            continue
        t = "place" if c in _PLACES else "org" if c in _ORGS else "person"
        ents.append({"name": c, "type": t})
    return ents[:5]


def _memories_for(prompt: str) -> str:
    date, msgs = _new_messages(prompt)
    mems = []
    for idx, who, text in msgs:
        if who.lower() in ("assistant", "system"):
            continue
        if len(text) < 15:
            continue
        for sent in _split_sentences(text):
            sent = re.sub(r"^(?:hi!?|hello!?|also,?|quick one:|mostly|update:|actually,?|remind me later:|good to know\.?|perfect,?|thanks,?)\s*", "", sent, flags=re.I).strip()
            sent = sent[0].upper() + sent[1:] if sent else sent
            sl = sent.lower()
            if len(sent) < 25 or re.match(r"^(ok|thanks|thank you|perfect|good night|good to know)", sl):
                continue
            has_marker = bool(re.search(r"\b(i|i'm|i've|i'll|my|we)\b", sl)) or bool(_entities(sent))
            if not has_marker:
                continue
            if sl.endswith("?") and not any(w in sl for w in ("i'm", "i am", "my ")):
                continue
            if any(w in sl for w in ("can you", "could you", "suggest")) and "birthday" not in sl:
                continue
            kind = _kind(sl)
            when = None
            m = re.search(r"\b(\d{1,2}) (january|february|march|april|may|june|july|august|september|october|november|december)\b", sl)
            if m and date:
                months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
                when = f"{date[:4]}-{months.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"
            elif kind == "event" and date:
                when = date
            mems.append({"text": third_person(sent, who), "kind": kind, "subject": who, "when": when,
                         "importance": 0.9 if kind == "identity" else 0.7, "confidence": 0.9, "sources": [idx],
                         "entities": _entities(sent)})
    return json.dumps({"memories": mems[:8]})


_REL_RULES = [
    (r"moved (?:from \w+ )?to (?P<o>[A-Z][a-zé]+)", "lives_in"), (r"live in (?P<o>[A-Z][a-zé]+)", "lives_in"),
    (r"now in (?P<o>[A-Z][a-zé]+)", "lives_in"), (r"moved from (?P<o>[A-Z][a-zé]+)", "moved_from"),
    (r"job at (?P<o>[A-Z][a-zé]+)", "works_at"), (r"work at (?P<o>[A-Z][a-zé]+)", "works_at"),
    (r"(?:my )?(?:cat|dog) (?:called |named )?(?P<o>[A-Z][a-z]+)", "has_pet"),
    (r"(?:my )?sister (?P<o>[A-Z][a-z]+)", "sibling_of"), (r"(?:my )?daughter (?P<o>[A-Z][a-z]+)", "parent_of"),
    (r"colleague (?P<o>[A-Z][a-z]+)", "colleague_of"), (r"allergic to (?P<o>\w+)", "allergic_to"),
    (r"master'?s in (?P<o>[a-z ]+?) at", "plans_to_study"), (r"at (?P<o>TU [A-Z][a-z]+)", "plans_to_study_at"),
    (r"(?P<o>[A-Z][a-z]+) marathon", "plans_to_run"),
]


def _relations_for(prompt: str) -> str:
    _, msgs = _new_messages(prompt)
    triples, ents = [], []
    for idx, who, text in msgs:
        if who.lower() in ("assistant", "system"):
            continue
        for e in _entities(text):
            if e not in ents:
                ents.append(e)
        for pat, rel in _REL_RULES:
            for m in re.finditer(pat, text):
                o = m.group("o").strip()
                if o.lower() in _SKIP_CAPS or o.lower() == who.lower():
                    continue
                triples.append({"s": who, "r": rel, "o": o, "source": idx, "confidence": 0.9})
        if "vegetarian" in text.lower():
            triples.append({"s": who, "r": "diet", "o": "vegetarian", "source": idx, "confidence": 0.9})
    return json.dumps({"entities": ents[:10], "triples": triples[:12]})


def _reconcile_for(prompt: str) -> str:
    new = re.search(r'NEW \(observed [^)]*\): "(.*)"', prompt)
    existing = re.findall(r'^([A-H])\. \(observed [^)]*\) "(.*)"$', prompt, re.M)
    if new and existing:
        nt = new.group(1).lower()
        for letter, txt in existing:
            t = txt.lower()
            if nt == t:
                return json.dumps({"decision": "DUPLICATE", "target": letter, "text": None, "reason": "same"})
            both_move = any(w in nt for w in ("moved", "now in", "lives in")) and any(w in t for w in ("moved", "now in", "lives in"))
            if both_move:
                return json.dumps({"decision": "CONTRADICT", "target": letter, "text": None, "reason": "location changed"})
    return json.dumps({"decision": "ADD", "target": None, "text": None, "reason": "different"})


def scripted_responder(prompt: str) -> str:
    if prompt.startswith("You maintain the long-term memory"):
        return _memories_for(prompt)
    if prompt.startswith("You are building a knowledge graph"):
        return _relations_for(prompt)
    if prompt.startswith("You maintain a memory store"):
        return _reconcile_for(prompt)
    return "{}"


def scripted_fake_llm(**kwargs) -> FakeLLMClient:
    return FakeLLMClient(responder=scripted_responder, **kwargs)
