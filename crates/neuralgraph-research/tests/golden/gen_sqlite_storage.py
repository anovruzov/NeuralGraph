#!/usr/bin/env python3
"""Fixtures for the SQLite store (``retrieval/sqlite_storage.py``). Writes ``sqlite_storage.json``.

A script of storage operations runs against a fresh database file; each step records its inputs (nodes and edges
as ``to_dict()``, rebuilt with ``from_dict`` on both sides) and its result. After the writes the raw database is
dumped too (``sqlite_master`` and every row, embedding blobs as hex, the ``data`` JSON text), so the Rust store must
write byte-identical files. LoCoMo messages provide the text; embeddings are ``fake_embedding(text, 24)``.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import _reference as R

import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.sqlite_storage as sq_mod
from NeuralGraph.chat_memory.llm import fake_embedding

R.freeze_now([dt_mod, sq_mod])
from NeuralGraph.research.retrieval.data_types import EdgeType, NeuralEdge, NeuralNode, NodeLayer  # noqa: E402
from NeuralGraph.research.retrieval.sqlite_storage import SQLiteNeuralGraphStorage  # noqa: E402

DIM = 24


def raw_dump(path):
    conn = sqlite3.connect(str(path))
    try:
        master = [list(r) for r in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY rowid")]
        nodes = [list(r) for r in conn.execute(
            "SELECT node_id, session_key, layer, typeof(layer), content, hex(embedding), typeof(embedding), data,"
            " created_at FROM nodes ORDER BY rowid")]
        edges = [list(r) for r in conn.execute(
            "SELECT edge_id, source_id, target_id, edge_type, data FROM edges ORDER BY rowid")]
        cats = [list(r) for r in conn.execute("SELECT * FROM categories ORDER BY rowid")]
        seq = [list(r) for r in conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY rowid")]
    finally:
        conn.close()
    return {"master": master, "nodes": nodes, "edges": edges, "categories": cats, "sqlite_sequence": seq}


def make_nodes():
    data = json.load(open(R.DATASETS / "locomo10.json"))
    msgs = data[2]["conversation"]["session_1"][:14]
    nodes = []
    for i, m in enumerate(msgs):
        n = NeuralNode(node_id=f"msg_2_{i}", layer=NodeLayer.MESSAGE if i % 4 else NodeLayer.EPISODE,
                       content=m["text"], embedding=fake_embedding(m["text"], DIM),
                       session_key="conv_2" if i < 11 else "conv_9",
                       metadata={"speaker": m["speaker"], "dia_id": m.get("dia_id"), "i": i, "ratio": i / 7,
                                 "flags": [True, None, 1e-7, 2 ** 62], "nested": {"é": "naïve 🎉", "q": "\"x\"\n"}},
                       entity_ids=[f"ent{i % 3}"], child_ids=[f"msg_2_{i + 1}"] if i % 5 == 0 else [],
                       parent_id="msg_2_0" if i % 5 else None, source_memory_ids=[f"mem-{i}"] if i % 2 else [],
                       heat_score=0.3 + i / 10, importance_score=0.5 - i / 100, activation_level=i / 3,
                       wave_amplitudes={"emotional": i / 9, "temporal": 0.25} if i % 3 == 0 else {},
                       edge_type_counts={"temporal": i, "entity": 1} if i % 2 else {},
                       dominant_dimension="emotional" if i % 3 == 0 else "", diversity_score=0.5 + i / 50)
        if i == 3:
            n.refractory_until = R.FIXED_NOW
        if i == 4:
            n.embedding = None
        if i == 5:
            n.embedding = []
        if i == 6:
            n.embedding = [0.1, -2.5, 3.4028235e38, 1e-45, 0.0, -0.0] + n.embedding[6:]
        nodes.append(n)
    return nodes


def make_edges(nodes):
    edges = []
    types = [EdgeType.TEMPORAL, EdgeType.ENTITY, EdgeType.SEMANTIC, EdgeType.CO_ACTIVATION]
    for i in range(len(nodes) - 1):
        e = NeuralEdge(edge_id=f"e{i:02d}", source_id=nodes[i].node_id, target_id=nodes[i + 1].node_id,
                       edge_type=types[i % 4], base_weight=0.85 - i / 100, sign=-1.0 if i == 2 else 1.0,
                       confidence=0.9, activation_count=i, ltp_boost=1.0 + i / 20,
                       metadata={"direction": "before", "w": i / 3} if i % 2 else {})
        edges.append(e)
    # a back edge and a dangling one (target not stored)
    edges.append(NeuralEdge(edge_id="e-back", source_id=nodes[5].node_id, target_id=nodes[1].node_id,
                            edge_type=EdgeType.TEMPORAL))
    edges.append(NeuralEdge(edge_id="e-dangling", source_id=nodes[2].node_id, target_id="ghost",
                            edge_type=EdgeType.CAUSAL))
    return edges


def nd(n):
    return None if n is None else n.to_dict()


def ed(e):
    return None if e is None else e.to_dict()


async def script(path):
    steps = []
    store = SQLiteNeuralGraphStorage(db_path=path)

    def rec(op, args, result):
        steps.append({"op": op, "args": args, "result": result})

    nodes, edges = make_nodes(), make_edges(make_nodes())
    for n in nodes[:8]:
        await store.save_node(n)
        rec("save_node", {"node": n.to_dict()}, None)
    rec("save_nodes_batch", {"nodes": []}, await store.save_nodes_batch([]))
    rec("save_nodes_batch", {"nodes": [n.to_dict() for n in nodes[8:]]}, await store.save_nodes_batch(nodes[8:]))
    for e in edges[:6]:
        await store.save_edge(e)
        rec("save_edge", {"edge": e.to_dict()}, None)
    rec("save_edges_batch", {"edges": []}, await store.save_edges_batch([]))
    rec("save_edges_batch", {"edges": [e.to_dict() for e in edges[6:]]}, await store.save_edges_batch(edges[6:]))
    steps.append({"op": "raw", "result": raw_dump(path)})

    for nid in ["msg_2_0", "msg_2_4", "msg_2_5", "msg_2_6", "msg_2_12", "nope"]:
        rec("get_node", {"node_id": nid}, nd(await store.get_node(nid)))
    for sk in ["conv_2", "conv_9", "none"]:
        rec("get_nodes_by_session", {"session_key": sk, "layer": None},
            [nd(n) for n in await store.get_nodes_by_session(sk)])
        for layer in [NodeLayer.MESSAGE, NodeLayer.EPISODE, NodeLayer.TOPIC]:
            rec("get_nodes_by_session", {"session_key": sk, "layer": layer.value},
                [nd(n) for n in await store.get_nodes_by_session(sk, layer)])
        rec("get_nodes_by_layer", {"session_key": sk, "layer": 1},
            [nd(n) for n in await store.get_nodes_by_layer(sk, NodeLayer.EPISODE)])
        rec("get_all_nodes", {"session_key": sk}, [nd(n) for n in await store.get_all_nodes(sk)])
        rec("get_all_edges", {"session_key": sk}, [ed(e) for e in await store.get_all_edges(sk)])
    for eid in ["e00", "e-back", "missing"]:
        rec("get_edge", {"edge_id": eid}, ed(await store.get_edge(eid)))
    for nid in ["msg_2_1", "msg_2_2", "msg_2_5", "ghost"]:
        for types in [None, [], ["temporal"], ["entity", "causal", "temporal"]]:
            ets = None if types is None else [EdgeType(t) for t in types]
            rec("get_edges_from", {"node_id": nid, "edge_types": types},
                [ed(e) for e in await store.get_edges_from(nid, ets)])
            rec("get_edges_to", {"node_id": nid, "edge_types": types},
                [ed(e) for e in await store.get_edges_to(nid, ets)])
    for s, t, et in [("msg_2_0", "msg_2_1", None), ("msg_2_0", "msg_2_1", "temporal"), ("msg_2_0", "msg_2_1", "entity"),
                     ("msg_2_5", "msg_2_1", None), ("msg_2_1", "msg_2_0", None)]:
        rec("get_edge_between", {"source_id": s, "target_id": t, "edge_type": et},
            ed(await store.get_edge_between(s, t, EdgeType(et) if et else None)))
    for q_text, sk, limit, lf in [(nodes[0].content, "conv_2", 50, None), (nodes[2].content, "conv_2", 3, None),
                                  (nodes[2].content, "conv_2", 50, [0]), (nodes[12].content, "conv_9", 50, [1, 2]),
                                  ("zzz qqq", "conv_2", 5, None)]:
        q = fake_embedding(q_text, DIM)
        res = await store.vector_search(q, sk, limit, [NodeLayer(x) for x in lf] if lf else None)
        rec("vector_search", {"query": q, "session_key": sk, "limit": limit, "layer_filter": lf},
            [[nd(n), s] for n, s in res])
    rec("get_session_statistics", {"session_key": "conv_2"}, await store.get_session_statistics("conv_2"))

    # update moves the row to the end (INSERT OR REPLACE deletes and re-inserts)
    upd = nodes[1]
    upd.content = "updated content"
    upd.heat_score = 9.5
    await store.update_node(upd)
    rec("update_node", {"node": upd.to_dict()}, None)
    rec("get_nodes_by_session", {"session_key": "conv_2", "layer": None},
        [nd(n) for n in await store.get_nodes_by_session("conv_2")])
    rec("delete_edge", {"edge_id": "e03"}, await store.delete_edge("e03"))
    rec("delete_edge", {"edge_id": "e03"}, await store.delete_edge("e03"))
    rec("delete_node", {"node_id": "msg_2_5"}, await store.delete_node("msg_2_5"))
    rec("delete_node", {"node_id": "msg_2_5"}, await store.delete_node("msg_2_5"))
    rec("get_all_edges", {"session_key": "conv_2"}, [ed(e) for e in await store.get_all_edges("conv_2")])
    rec("get_session_statistics", {"session_key": "conv_2"}, await store.get_session_statistics("conv_2"))
    rec("get_session_statistics", {"session_key": "none"}, await store.get_session_statistics("none"))

    for name, desc, dom in [("travel", "Trips", "life"), ("work", None, None), ("pets", "Animals", "life")]:
        rec("create_category", {"name": name, "description": desc, "domain": dom},
            await store.create_category(name, desc, dom))
    try:
        await store.create_category("travel")
        dup = None
    except sqlite3.IntegrityError as e:
        dup = str(e)
    rec("create_category", {"name": "travel", "description": None, "domain": None}, {"error": dup})
    for dom in [None, "life", "", "none"]:
        rec("list_categories", {"domain": dom}, await store.list_categories(dom))
    rec("create_domain", {"name": "life", "description": "Everyday"}, await store.create_domain("life", "Everyday"))
    rec("create_domain", {"name": "tech", "description": None}, await store.create_domain("tech"))
    try:
        await store.create_domain("tech")
        dup = None
    except sqlite3.IntegrityError as e:
        dup = str(e)
    rec("create_domain", {"name": "tech", "description": None}, {"error": dup})
    rec("list_domains", {}, await store.list_domains())
    for limit, offset in [(100, 0), (1, 0), (1, 1), (5, 7)]:
        rec("list_sessions", {"limit": limit, "offset": offset}, await store.list_sessions(limit, offset))

    # beyond float32: native-mode struct.pack stores infinity (no OverflowError)
    big = NeuralNode(node_id="big", layer=NodeLayer.MESSAGE, content="x", embedding=[1e39, -1e39, 0.5],
                     session_key="conv_2")
    await store.save_node(big)
    rec("save_node", {"node": big.to_dict()}, None)

    steps.append({"op": "raw", "result": raw_dump(path)})
    rec("clear_session", {"session_key": "conv_2"}, await store.clear_session("conv_2"))
    rec("clear_session", {"session_key": "conv_2"}, await store.clear_session("conv_2"))
    rec("get_all_nodes", {"session_key": "conv_9"}, [nd(n) for n in await store.get_all_nodes("conv_9")])
    rec("get_all_edges", {"session_key": "conv_9"}, [ed(e) for e in await store.get_all_edges("conv_9")])
    store.close()

    reopened = SQLiteNeuralGraphStorage(db_path=path)
    rec("reopen_get_node", {"node_id": "msg_2_12"}, nd(await reopened.get_node("msg_2_12")))
    reopened.close()
    steps.append({"op": "raw", "result": raw_dump(path)})
    return steps


async def legacy(path):
    """``test_coordination_storage.py``'s legacy row: no ``source_memory_ids`` in the JSON blob."""
    store = SQLiteNeuralGraphStorage(db_path=path)
    created_at = "2026-01-01T00:00:00+00:00"
    legacy_data = {
        "node_id": "legacy-node", "session_key": "s", "layer": 1, "content": "row persisted before",
        "embedding": None, "wave_amplitudes": {}, "activation_level": 0.0, "last_activated": created_at,
        "refractory_until": None, "heat_score": 0.3, "importance_score": 0.5, "consolidation_state": "active",
        "parent_id": None, "child_ids": [], "entity_ids": [], "edge_type_counts": {}, "dominant_dimension": "",
        "diversity_score": 0.0, "created_at": created_at, "metadata": {},
    }
    minimal = {"node_id": "minimal", "session_key": "s", "layer": 0, "content": "only required keys",
               "dominant_dimension": ""}
    conn = sqlite3.connect(str(path))
    for d, layer_text, blob in [(legacy_data, "1", None), (minimal, "0", bytes.fromhex("0000803f000000c0"))]:
        conn.execute("INSERT INTO nodes (node_id, session_key, layer, content, embedding, data, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (d["node_id"], "s", layer_text, d["content"], blob, json.dumps(d), created_at))
    conn.commit()
    conn.close()
    out = {"rows": [[legacy_data, "1", None], [minimal, "0", "0000803f000000c0"]],
           "created_at": created_at,
           "loaded": [nd(await store.get_node("legacy-node")), nd(await store.get_node("minimal"))]}
    store.close()
    return out


def main():
    with tempfile.TemporaryDirectory() as d:
        steps = asyncio.run(script(Path(d) / "nested" / "graph.db"))
    with tempfile.TemporaryDirectory() as d:
        leg = asyncio.run(legacy(Path(d) / "legacy.db"))
    R.dump("sqlite_storage.json", {"steps": steps, "legacy": leg})


if __name__ == "__main__":
    main()
