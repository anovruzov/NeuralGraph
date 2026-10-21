"""Shared setup for the engine fixture generators (``gen_gating.py`` ... ``gen_consolidation.py``).

The engine modules (hierarchy, temporal, consolidation, pattern completion, wavefront) read ``datetime.now``,
mint uuids and iterate ``set``s (the store's indexes, unions of entity ids and wave dimensions). A Python
``set`` of strings iterates in an order that depends on the per-process hash seed, so those orders were never
reproducible in Python itself; the Rust port uses insertion order (``IndexSet``). ``install()`` makes the
reference deterministic the same way: ``set`` in the engine modules becomes ``OrderedSet`` (a dict-backed set
with insertion order; discarding and re-adding moves an element to the end, like ``IndexSet::shift_remove`` +
``insert``), "now" is frozen and uuids count up.

Graphs are built from LoCoMo conversations: one MESSAGE node per turn, with the product's deterministic
``fake_embedding`` (Rust: ``neuralgraph_memory::fake_embedding``), wave amplitudes and entity ids derived from
the text here and recorded in the fixture, and ``created_at`` from the session date plus one minute per turn.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import _reference as R

import NeuralGraph.research.retrieval.consolidation as consolidation_mod
import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.gating as gating_mod
import NeuralGraph.research.retrieval.hierarchy as hierarchy_mod
import NeuralGraph.research.retrieval.interference as interference_mod
import NeuralGraph.research.retrieval.pattern_completion as pc_mod
import NeuralGraph.research.retrieval.storage as storage_mod
import NeuralGraph.research.retrieval.temporal as temporal_mod
import NeuralGraph.research.retrieval.wavefront as wavefront_mod
from NeuralGraph.chat_memory.llm import fake_embedding

DIM = 32
TIME_MODULES = [dt_mod, storage_mod, hierarchy_mod, temporal_mod, consolidation_mod, interference_mod,
                wavefront_mod, pc_mod, gating_mod]


class OrderedSet:
    """``set`` with insertion-ordered iteration (only the operations the engine uses)."""

    def __init__(self, items=()):
        self._d = dict.fromkeys(items)

    def add(self, x):
        self._d[x] = None

    def discard(self, x):
        self._d.pop(x, None)

    def remove(self, x):
        del self._d[x]

    def update(self, *iterables):
        for it in iterables:
            for x in it:
                self._d[x] = None

    def copy(self):
        return OrderedSet(self._d)

    def __or__(self, other):
        r = OrderedSet(self._d)
        r.update(other)
        return r

    def __and__(self, other):
        return OrderedSet(x for x in self._d if x in other)

    def __sub__(self, other):
        return OrderedSet(x for x in self._d if x not in other)

    def __contains__(self, x):
        return x in self._d

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __bool__(self):
        return bool(self._d)

    def __repr__(self):
        return f"OrderedSet({list(self._d)!r})"


def install(now=R.FIXED_NOW):
    for m in [storage_mod, hierarchy_mod, temporal_mod, consolidation_mod, pc_mod, wavefront_mod]:
        m.set = OrderedSet
    R.ordered_ids([dt_mod])
    set_now(now)
    R.UUIDS.reset()


def set_now(now):
    R.freeze_now(TIME_MODULES, now)


def iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------------------------------------------------
# LoCoMo messages
# ---------------------------------------------------------------------------------------------------------

ENTITY_WORDS = ["caroline", "melanie", "lgbtq", "school", "kids", "painting", "adoption", "camping", "pottery",
                "art", "music", "family", "support", "transgender", "counseling", "beach", "friends", "career"]
WAVE_DIMS = ["entity", "temporal", "action", "relational", "state", "spatial", "causal", "emotional", "quantitative"]
_WORD = re.compile(r"[a-z]+")


def session_datetime(s: str) -> datetime:
    return datetime.strptime(s, "%I:%M %p on %d %B, %Y").replace(tzinfo=timezone.utc)


def wave_amplitudes(text: str) -> dict[str, float]:
    """Deterministic amplitudes from word statistics (recorded in the fixture, never recomputed in Rust)."""
    words = _WORD.findall(text.lower())
    amps = {}
    for i, dim in enumerate(WAVE_DIMS):
        hits = sum(1 for w in words if (len(w) + i) % 9 == 0)
        if hits:
            amps[dim] = round(min(1.0, 0.15 + 0.2 * hits), 3)
    return amps


def entity_ids(text: str) -> list[str]:
    words = set(_WORD.findall(text.lower()))
    return [f"ent:{w}" for w in ENTITY_WORDS if w in words]


def locomo_messages(conv_index: int, sessions: list[int], max_turns: int | None = None):
    """Message specs (content, speaker, created_at, waves, entities) for the given LoCoMo sessions."""
    data = json.loads((R.DATASETS / "locomo10.json").read_text(encoding="utf-8"))
    conv = data[conv_index]["conversation"]
    out = []
    for s in sessions:
        start = session_datetime(conv[f"session_{s}_date_time"])
        turns = conv[f"session_{s}"][:max_turns] if max_turns else conv[f"session_{s}"]
        for t, turn in enumerate(turns):
            text = turn["text"]
            out.append({
                "content": text,
                # every seventh turn has no speaker: exercises the speaker-less paths
                "speaker": None if len(out) % 7 == 6 else turn["speaker"],
                "created_at": iso(start + timedelta(minutes=t)),
                "wave_amplitudes": wave_amplitudes(text),
                "entity_ids": entity_ids(text),
                "importance_score": round(0.3 + 0.05 * (len(text) % 9), 2),
            })
    return out


async def add_messages(hierarchy, specs, session_key):
    nodes = []
    for spec in specs:
        meta = {"dia_index": len(nodes)}
        if spec["speaker"]:
            meta["speaker"] = spec["speaker"]
        node = await hierarchy.create_message_node(
            content=spec["content"], session_key=session_key, embedding=fake_embedding(spec["content"], DIM),
            wave_amplitudes=dict(spec["wave_amplitudes"]), entity_ids=list(spec["entity_ids"]),
            importance_score=spec["importance_score"], created_at=datetime.fromisoformat(spec["created_at"]),
            **meta)
        nodes.append(node)
    return nodes


async def add_semantic_and_entity_edges(storage, nodes, window=6):
    """SEMANTIC edges between nearby similar messages, ENTITY edges between nearby messages sharing an entity
    (the same rule is in the Rust ``engine_common``)."""
    from NeuralGraph.research.retrieval.data_types import (
        EdgeType, NeuralEdge, cosine_similarity, generate_edge_id)
    for i, a in enumerate(nodes):
        for j in range(i + 1, min(len(nodes), i + 1 + window)):
            b = nodes[j]
            sim = cosine_similarity(a.embedding, b.embedding)
            if sim >= 0.3:
                await storage.save_edge(NeuralEdge(edge_id=generate_edge_id(), source_id=a.node_id,
                                                   target_id=b.node_id, edge_type=EdgeType.SEMANTIC, base_weight=sim))
            shared = [e for e in a.entity_ids if e in b.entity_ids]
            if shared:
                await storage.save_edge(NeuralEdge(
                    edge_id=generate_edge_id(), source_id=a.node_id, target_id=b.node_id, edge_type=EdgeType.ENTITY,
                    base_weight=round(0.9 / (j - i), 6), confidence=0.9,
                    metadata={"message_distance": j - i, "entity": shared[0]}))


async def dump_graph(storage, session_key):
    return {"nodes": [n.to_dict() for n in await storage.get_nodes_by_session(session_key)],
            "edges": [e.to_dict() for e in await storage.get_all_edges(session_key)]}


def ids(nodes):
    return [n.node_id for n in nodes]


def scored(pairs):
    return [[n.node_id, s] for n, s in pairs]
