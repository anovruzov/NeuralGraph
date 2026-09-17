"""Scripted fake LLM used by the CLI ``--fake-llm`` flag, the demo and the tests.

The responder recognises the three prompt kinds by their headings and answers with plausible JSON built
from the NEW MESSAGES block itself, so a demo run produces sensible memories without any model server.
"""
from __future__ import annotations

import json
import re

from .llm import FakeLLMClient

_MSG_RE = re.compile(r"^(\d+)\. \[([^\]]+)\]: (.*)$", re.M)
_CAP_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")
_DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})\)")
_NEW_BLOCK_RE = re.compile(r"NEW MESSAGES \(date: ([^)]*)\):\n(.*?)\n\nReturn ONLY JSON", re.S)


def _new_messages(prompt: str) -> tuple[str, list[tuple[int, str, str]]]:
    m = _NEW_BLOCK_RE.search(prompt)
    if not m:
        return "", []
    out = []
    for idx, who, text in _MSG_RE.findall(m.group(2)):
        out.append((int(idx), who.replace(" (assistant)", ""), text))
    return m.group(1), out


def _memories_for(prompt: str) -> str:
    date, msgs = _new_messages(prompt)
    mems = []
    for idx, who, text in msgs:
        if "(assistant)" in prompt.split("\n")[0] or who.lower() in ("assistant", "system"):
            continue
        low = text.lower()
        if len(text) < 15 or any(w in low for w in ("thanks", "hello", "how are you", "haha", "good to see")):
            continue
        ents = [{"name": c, "type": "place" if c in ("Berlin", "Tokyo", "Paris", "London") else "person"}
                for c in dict.fromkeys(_CAP_RE.findall(text)) if c.lower() not in ("the", "yes", "last", "next", "this", "my", "i")]
        kind = "plan" if any(w in low for w in ("plan", "going to", "will ", "next ")) else \
               "preference" if any(w in low for w in ("love", "like", "prefer", "hate", "favorite", "favourite")) else \
               "event" if any(w in low for w in ("moved", "went", "visited", "started", "yesterday", "last ")) else "fact"
        mems.append({"text": f"{who} said: {text.rstrip('.!')}." if not text.lower().startswith(who.lower()) else text,
                     "kind": kind, "subject": who, "when": date if kind == "event" else None, "importance": 0.7,
                     "confidence": 0.9, "sources": [idx], "entities": ents[:4]})
    return json.dumps({"memories": mems[:12]})


def _relations_for(prompt: str) -> str:
    _, msgs = _new_messages(prompt)
    triples, ents = [], []
    for idx, who, text in msgs:
        caps = [c for c in dict.fromkeys(_CAP_RE.findall(text)) if c.lower() not in ("the", "yes", "last", "next", "this", "my", "i")]
        low = text.lower()
        for c in caps[:3]:
            ents.append({"name": c, "type": "place" if c in ("Berlin", "Tokyo", "Paris", "London") else "other"})
            rel = "lives_in" if "moved to" in low or "live in" in low else "mentions"
            if rel != "mentions":
                triples.append({"s": who, "r": rel, "o": c, "source": idx, "confidence": 0.9})
        if "cat" in low or "dog" in low:
            m = re.search(r"(?:cat|dog)(?: named| called)? ([A-Z][a-z]+)", text)
            if m:
                triples.append({"s": who, "r": "has_pet", "o": m.group(1), "source": idx, "confidence": 0.9})
                ents.append({"name": m.group(1), "type": "pet"})
    return json.dumps({"entities": ents[:10], "triples": triples[:15]})


def _reconcile_for(prompt: str) -> str:
    new = re.search(r'NEW \(observed [^)]*\): "(.*)"', prompt)
    existing = re.findall(r'^([A-H])\. \(observed [^)]*\) "(.*)"$', prompt, re.M)
    if new and existing:
        nt = new.group(1).lower()
        for letter, txt in existing:
            t = txt.lower()
            if nt == t:
                return json.dumps({"decision": "DUPLICATE", "target": letter, "text": None, "reason": "same"})
            if ("moved to" in nt and "moved to" in t) or ("lives in" in nt and "lives in" in t):
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
