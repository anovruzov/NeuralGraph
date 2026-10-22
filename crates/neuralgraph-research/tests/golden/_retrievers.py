"""Shared setup for the retriever fixture generators (``gen_retrievers.py``, ``gen_electron.py``).

Builds a LoCoMo memory graph the way the service did: MESSAGE nodes with ``fake_embedding`` vectors, wave
amplitudes, entity ids (proper names as written plus ``ent:`` ids), speakers under ``speaker`` and on some nodes
``producer_id``, temporal metadata in its different shapes (``resolved_date`` as ISO date / "May 2023" /
"8 May 2023" / year / junk, ``temporal_tokens``, ``date_tokens``); then a temporal chain, semantic and entity
edges, episodes, topics and a few co-activation edges. The Rust twin is ``tests/retrievers_common``.

``install()`` extends ``_engine.install()``: the retrieval modules' ``set`` becomes an insertion-ordered set
(with the reflected operators ``builtin_set & ordered_set`` needs), so sums over unions of wave dimensions are
reproducible.
"""
from __future__ import annotations

import re
from datetime import datetime

import _engine as E

from NeuralGraph.chat_memory.llm import fake_embedding

NAMES = ["Caroline", "Melanie", "Mel", "Sweden", "Oliver", "Luna", "Bailey", "Pride", "Grandma"]
_CAP = re.compile(r"\b[A-Z][a-z]+\b")


class OSet(E.OrderedSet):
    def __rand__(self, other):
        return OSet(x for x in other if x in self)

    def __ror__(self, other):
        r = OSet(other)
        r.update(self)
        return r

    def __and__(self, other):
        return OSet(x for x in self if x in other)

    def __or__(self, other):
        r = OSet(self)
        r.update(other)
        return r


def install(modules):
    E.install()
    for m in modules:
        m.set = OSet


def jsonable_waves(w):
    """Wave dicts as recorded in fixtures (Python's ``True`` flag is 1.0 in arithmetic and in the Rust map)."""
    return None if w is None else {k: float(v) for k, v in w.items()}


def message_specs(conv_index=0, sessions=(1, 2, 3), max_turns=16):
    specs = E.locomo_messages(conv_index, list(sessions), max_turns=max_turns)
    for i, s in enumerate(specs):
        names = [w for w in dict.fromkeys(_CAP.findall(s["content"])) if w in NAMES]
        s["entity_ids"] = names + s["entity_ids"]
        date = s["created_at"][:10]
        resolved = {0: date, 1: "May 2023", 2: "8 May 2023", 3: date[:4], 4: None, 5: "sometime soon"}[i % 6]
        s["resolved_date"] = resolved
        s["temporal_tokens"] = [f"YEAR_{date[:4]}", f"MONTH_{date[5:7]}", f"DATE_{date}"] if i % 4 == 0 else None
        extra = {}
        if i % 3 == 1 and s["speaker"]:
            extra["producer_id"] = s["speaker"]
        if i % 7 == 3:
            extra["date_tokens"] = [f"YEAR_{date[:4]}", f"MONTH_{date[5:7]}"]
        s["extra"] = extra
    return specs


async def add_nodes(hierarchy, specs, session_key):
    nodes = []
    for spec in specs:
        meta = {"dia_index": len(nodes)}
        if spec["speaker"]:
            meta["speaker"] = spec["speaker"]
        meta.update(spec["extra"])
        node = await hierarchy.create_message_node(
            content=spec["content"], session_key=session_key, embedding=fake_embedding(spec["content"], E.DIM),
            wave_amplitudes=dict(spec["wave_amplitudes"]), entity_ids=list(spec["entity_ids"]),
            importance_score=spec["importance_score"], resolved_date=spec["resolved_date"],
            temporal_tokens=spec["temporal_tokens"], created_at=datetime.fromisoformat(spec["created_at"]), **meta)
        nodes.append(node)
    return nodes


async def build_graph(storage, specs, session_key):
    from NeuralGraph.research.retrieval.hierarchy import HierarchyManager
    from NeuralGraph.research.retrieval.temporal import TemporalChainManager
    h = HierarchyManager(storage)
    tm = TemporalChainManager(storage)
    msgs = await add_nodes(h, specs, session_key)
    await tm.build_temporal_chain(msgs, session_key)
    await E.add_semantic_and_entity_edges(storage, msgs)
    episodes = await h.auto_segment_to_episodes(session_key)
    topics = await h.auto_cluster_to_topics(session_key)
    for _ in range(2):
        await tm.record_co_activation(msgs[:4], context="seed")
        await tm.record_co_activation([msgs[10], msgs[20], msgs[30]], context="seed")
    return h, tm, msgs, episodes, topics


def result(r):
    return {"nodes": E.scored(r.nodes), "stages": r.stages_executed, "seen": r.total_candidates_seen,
            "co": r.co_activations_recorded}


async def call(fn):
    try:
        return result(await fn())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
