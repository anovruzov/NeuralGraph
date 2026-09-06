"""LLM-built entity graph for a conversation (Graphiti / HippoRAG style), no regex.

For every message the LLM returns entities (with type) and subject-relation-object triples. The prompt
includes the conversation's running entity registry, so mentions are resolved to existing nodes
("Mel" -> "Melanie", "my daughter" -> "Sophie") instead of creating duplicates. First-person references
are resolved to the speaker. Output is cached per conversation.

Result per conversation:
    {"messages": [{"idx": i, "entities": ["melanie", "art show"], "triples": [["melanie","attended","art show"]]}, ...],
     "registry": {"melanie": {"type": "person", "count": 120}, ...}}
"""
from __future__ import annotations

import asyncio
import difflib
import json
import re
from pathlib import Path

import aiohttp

from . import llm_backend

PROMPT = """You are building a knowledge graph from a chat between {speakers}.
Extract from the MESSAGE below:
1. entities: people, places, organisations, pets, objects, events, activities, media titles, dates, health conditions, jobs, hobbies. Short canonical names, lowercase, singular. Resolve first person ("I", "my", "me") to the speaker "{speaker}". Resolve nicknames and pronouns to the KNOWN ENTITIES list when they refer to the same thing; reuse the exact known name.
2. triples: [subject, relation, object] facts stated or clearly implied by the message. Subject and object must be entities from your list. Relation is a short verb phrase (has_pet, lives_in, attended, likes, works_as, plans, bought, read, moved_from, ...).

KNOWN ENTITIES (reuse these names when they match): {registry}

SPEAKER: {speaker}
MESSAGE: {text}

Return ONLY JSON: {{"entities": [{{"name": "...", "type": "..."}}], "triples": [["subject", "relation", "object"]]}}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _norm(name: str) -> str:
    name = re.sub(r"[^a-z0-9 '\-]", " ", str(name).lower()).strip()
    name = re.sub(r"\s+", " ", name)
    if name.endswith("'s"):
        name = name[:-2]
    return name


def _resolve(name: str, registry: dict[str, dict], speaker: str) -> str:
    """Map a mention to a registry entity when it clearly is one; else return the normalised name."""
    n = _norm(name)
    if not n:
        return ""
    if n in ("i", "me", "my", "myself", "mine"):
        return speaker.lower()
    if n in registry:
        return n
    # nickname / prefix match against people, then fuzzy match against everything
    for known, meta in registry.items():
        if meta.get("type") == "person" and (known.startswith(n) or n.startswith(known)) and min(len(n), len(known)) >= 3:
            return known
    best = difflib.get_close_matches(n, list(registry.keys()), n=1, cutoff=0.88)
    return best[0] if best else n


def _parse(resp: str) -> dict:
    m = _JSON_RE.search(resp or "")
    if not m:
        return {"entities": [], "triples": []}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return {"entities": [], "triples": []}
    ents = obj.get("entities") or []
    ents = [e if isinstance(e, dict) else {"name": str(e), "type": ""} for e in ents]
    trips = [t for t in (obj.get("triples") or []) if isinstance(t, (list, tuple)) and len(t) == 3]
    return {"entities": ents, "triples": trips}


async def extract_conversation(http: aiohttp.ClientSession, messages: list[dict], speakers: list[str],
                               cache_path: Path | None = None, max_registry: int = 120, log_every: int = 50) -> dict:
    """messages: [{"speaker":..., "text":...}] in order. Sequential within a conversation (registry grows)."""
    if cache_path and cache_path.exists():
        cached = json.load(open(cache_path))
        if len(cached.get("messages", [])) == len(messages):
            return cached
    registry: dict[str, dict] = {sp.lower(): {"type": "person", "count": 1} for sp in speakers}
    out_msgs = []
    for i, msg in enumerate(messages):
        speaker = msg["speaker"]
        top = sorted(registry.items(), key=lambda kv: -kv[1]["count"])[:max_registry]
        reg_txt = ", ".join(f"{k} ({v['type']})" if v.get("type") else k for k, v in top)
        prompt = PROMPT.format(speakers=" and ".join(speakers), speaker=speaker, text=msg["text"][:800], registry=reg_txt)
        try:
            resp = await llm_backend.llm_generate(http, prompt, temperature=0, max_tokens=300, timeout_seconds=60)
        except Exception:
            resp = ""
        parsed = _parse(resp)
        ents, types = [], {}
        for e in parsed["entities"]:
            name = _resolve(e.get("name", ""), registry, speaker)
            if not name or len(name) > 60:
                continue
            types[name] = _norm(e.get("type", "")) or registry.get(name, {}).get("type", "")
            if name not in ents:
                ents.append(name)
        triples = []
        for s, r, o in parsed["triples"]:
            s2, o2 = _resolve(s, registry, speaker), _resolve(o, registry, speaker)
            if s2 and o2 and s2 != o2:
                triples.append([s2, _norm(r).replace(" ", "_"), o2])
                for x in (s2, o2):
                    if x not in ents:
                        ents.append(x)
        if speaker.lower() not in ents:
            ents.append(speaker.lower())
        for name in ents:
            reg = registry.setdefault(name, {"type": types.get(name, ""), "count": 0})
            reg["count"] += 1
            if not reg.get("type") and types.get(name):
                reg["type"] = types[name]
        out_msgs.append({"idx": i, "entities": ents, "triples": triples})
        if log_every and (i + 1) % log_every == 0:
            print(f"    kg: {i + 1}/{len(messages)} messages, {len(registry)} entities", flush=True)
    result = {"messages": out_msgs, "registry": registry}
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(result, open(cache_path, "w"))
    return result


async def _main():
    """Extract all 10 LoCoMo conversations concurrently (each sequential inside) into demo/results/kg_cache/."""
    import sys
    root = Path(__file__).resolve().parent.parent
    data = json.load(open(root / "evaluation" / "locomo" / "locomo10.json"))
    only = {int(x) for x in sys.argv[1:]} if len(sys.argv) > 1 else set(range(len(data)))

    def flatten(conv):
        c = conv.get("conversation", conv)
        msgs, i = [], 1
        while f"session_{i}" in c:
            for m in c[f"session_{i}"]:
                msgs.append({"speaker": m.get("speaker", "Unknown"), "text": m.get("text", "")})
            i += 1
        return msgs

    async with aiohttp.ClientSession() as http:
        async def one(ci, conv):
            msgs = flatten(conv)
            speakers = sorted({m["speaker"] for m in msgs})
            res = await extract_conversation(http, msgs, speakers, root / "demo" / "results" / "kg_cache" / f"conv{ci}.json")
            n_tr = sum(len(m["triples"]) for m in res["messages"])
            print(f"conv {ci}: {len(msgs)} msgs -> {len(res['registry'])} entities, {n_tr} triples", flush=True)
        await asyncio.gather(*(one(ci, conv) for ci, conv in enumerate(data) if ci in only))


if __name__ == "__main__":
    asyncio.run(_main())
