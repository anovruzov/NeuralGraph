#!/usr/bin/env python3
"""Fixtures for the compatibility layer and the storage core: random.Random, numpy's legacy RandomState and
float32 BLAS arithmetic, LSH, the in-memory store and the data types. Writes ``foundation.json``."""
from __future__ import annotations

import asyncio
import hashlib
import random

import _reference as R
import numpy as np

import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.lsh as lsh_mod
import NeuralGraph.research.retrieval.storage as storage_mod

R.freeze_now([dt_mod, storage_mod])
from NeuralGraph.research.retrieval.data_types import (  # noqa: E402
    EdgeType, NeuralEdge, NeuralNode, NodeLayer, cosine_similarity,
)


def py_random():
    out = []
    for seed in [0, 1, 42, 20260813, 2**40 + 5, -7, 2**70 + 3]:
        r = random.Random(seed)
        case = {"seed": str(seed)}
        case["random"] = [r.random() for _ in range(5)]
        case["getrandbits"] = [str(r.getrandbits(k)) for k in [1, 5, 31, 32, 33, 64, 100]]
        case["randbelow"] = [r._randbelow(n) for n in [1, 2, 3, 7, 36, 1000, 2**31 + 11]]
        case["choice"] = "".join(r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(14))
        xs = list(range(20))
        r.shuffle(xs)
        case["shuffle"] = xs
        case["sample_small"] = r.sample(range(10), 5)
        case["sample_mid"] = r.sample(range(100), 10)
        case["sample_large"] = r.sample(range(1000), 60)
        case["randint"] = [r.randint(1, 6) for _ in range(10)]
        out.append(case)
    return out


def np_random():
    out = []
    for seed in [0, 1, 42, 2**32 - 1]:
        np.random.seed(seed)
        out.append({"seed": seed, "randn_3x4": R.f64_bits(np.random.randn(3, 4).ravel()),
                    "random_sample": R.f64_bits([np.random.random_sample() for _ in range(3)])})
    # the LSH draw: seed 42, eight 12x768 matrices back to back
    np.random.seed(42)
    blob = b"".join(np.random.randn(12, 768).astype("<f8").tobytes() for _ in range(8))
    return {"cases": out, "lsh_draw_sha256": hashlib.sha256(blob).hexdigest()}


def blas():
    rng = np.random.default_rng(7)
    cases = []
    for dim in [1, 2, 3, 5, 8, 9, 17, 33, 100, 129, 257, 768, 1024]:
        for _ in range(6):
            a = rng.standard_normal(dim).astype(np.float32)
            b = rng.standard_normal(dim).astype(np.float32)
            cases.append({"a": R.f32_bits(a), "b": R.f32_bits(b),
                          "dot": R.f32_bits([np.dot(a, b)])[0], "norm": R.f32_bits([np.linalg.norm(a)])[0],
                          "pairwise": R.f32_bits([np.add.reduce(a * b)])[0]})
    m = rng.standard_normal((40, 768)).astype(np.float32)
    q = rng.standard_normal(768).astype(np.float32)
    mn = m / (np.linalg.norm(m, axis=1, keepdims=True) + np.float32(1e-9))
    qn = q / (np.linalg.norm(q) + 1e-9)
    p64 = rng.standard_normal((12, 768))
    return {"vectors": cases, "matrix": R.f32_bits(m.ravel()), "query": R.f32_bits(q),
            "row_norms": R.f32_bits(np.linalg.norm(m, axis=1)),
            "normalized_query": R.f32_bits(qn), "sims": R.f32_bits(mn @ qn),
            "proj64": R.f64_bits(p64.ravel()), "proj64_times_q": R.f64_bits(p64 @ q)}


def embeddings(n, dim, seed):
    rng = np.random.default_rng(seed)
    base = rng.standard_normal((8, dim))
    out = []
    for i in range(n):
        v = base[i % 8] * 0.6 + rng.standard_normal(dim) * 0.4
        out.append([float(x) for x in v])
    return out


async def lsh_and_storage():
    embs = embeddings(160, 48, 11)
    queries = embeddings(6, 48, 12)
    lsh = lsh_mod.RandomProjectionLSH(lsh_mod.LSHConfig(num_tables=8, num_hashes=12))
    for i, e in enumerate(embs):
        lsh.index(f"n{i}", e)
    removed = [lsh.remove("n3"), lsh.remove("missing")]
    lsh_out = {"projections_sha256": hashlib.sha256(b"".join(p.astype("<f8").tobytes() for p in lsh._projections)).hexdigest(),
               "removed": removed, "query": [], "probe": [], "stats": None}
    for q in queries:
        lsh_out["query"].append([[nid, s] for nid, s in lsh.query(q, k=15)])
        lsh_out["probe"].append([[nid, s] for nid, s in lsh.query_with_probing(q, k=30, num_probes=3)])
    lsh_out["stats"] = lsh.get_stats()
    simhash = lsh_mod.SimHash(embedding_dim=48, num_bits=64, seed=42)
    lsh_out["simhash"] = [str(simhash.compute_signature(e)) for e in embs[:20]]
    lsh_out["near_duplicates"] = [list(t) for t in simhash.find_near_duplicates({f"n{i}": e for i, e in enumerate(embs[:40])}, threshold=0.6)]

    st = storage_mod.InMemoryNeuralGraphStorage()
    for i, e in enumerate(embs):
        node = NeuralNode(node_id=f"n{i}", layer=NodeLayer.MESSAGE if i % 5 else NodeLayer.EPISODE,
                          content=f"message {i}", embedding=e, session_key="big",
                          entity_ids=[f"ent{i % 7}"], metadata={"speaker": "A" if i % 2 else "B"})
        await st.save_node(node)
    for i, e in enumerate(embs[:30]):
        await st.save_node(NeuralNode(node_id=f"s{i}", layer=NodeLayer.MESSAGE, content=f"small {i}",
                                      embedding=e, session_key="small", heat_score=0.1 * i))
    for i in range(0, 60, 3):
        await st.save_edge(NeuralEdge(edge_id=f"e{i}", source_id=f"n{i}", target_id=f"n{i + 1}",
                                      edge_type=EdgeType.TEMPORAL if i % 2 else EdgeType.ENTITY, base_weight=0.5 + i / 100))
    await st.save_edge(NeuralEdge(edge_id="e3", source_id="n3", target_id="n9", edge_type=EdgeType.SEMANTIC))
    st_out = {"vector_big": [], "vector_small": [], "vector_filtered": []}
    for q in queries:
        st_out["vector_big"].append([[n.node_id, s] for n, s in await st.vector_search(q, "big", limit=20)])
        st_out["vector_small"].append([[n.node_id, s] for n, s in await st.vector_search(q, "small", limit=10)])
        st_out["vector_filtered"].append([[n.node_id, s] for n, s in await st.vector_search(q, "big", limit=10, layer_filter=[NodeLayer.EPISODE])])
    n0 = await st.get_node("n0")
    st_out["n0_edge_counts"] = n0.edge_type_counts
    st_out["n0_diversity"] = n0.diversity_score
    st_out["n3_edge_counts"] = (await st.get_node("n3")).edge_type_counts
    st_out["neighbors_n3"] = sorted([n.node_id, e.edge_id] for n, e in await st.get_neighbors("n3", direction="both"))
    st_out["stats_big"] = await st.get_session_statistics("big")
    st_out["hot"] = [n.node_id for n in await st.get_hot_nodes("small", threshold=1.5, limit=5)]
    st_out["entities"] = sorted(n.node_id for n in await st.find_nodes_by_entities(["ent1", "ent3"], "big"))
    dup = NeuralNode(node_id="dup", layer=NodeLayer.MESSAGE, content="dup", embedding=embs[10], session_key="big")
    try:
        st_out["dedup"] = list(await st.save_node_with_dedup(dup))
    except TypeError as exc:
        st_out["dedup"] = {"error": str(exc)}
    fresh = storage_mod.InMemoryNeuralGraphStorage()
    await fresh.save_node(NeuralNode(node_id="plain", layer=NodeLayer.MESSAGE, content="no embedding", session_key="z"))
    dup2 = NeuralNode(node_id="dup2", layer=NodeLayer.MESSAGE, content="d", embedding=embs[1], session_key="z")
    st_out["dedup_no_lsh"] = list(await fresh.save_node_with_dedup(dup2))
    st_out["deleted"] = [await st.delete_node("n6"), await st.delete_node("n6")]
    st_out["edges_after_delete"] = sorted(e.edge_id for e in await st.get_all_edges("big"))
    st_out["cleared"] = await st.clear_session("small")
    return {"embeddings": embs, "queries": queries}, lsh_out, st_out


def data_types():
    n = NeuralNode(node_id="x", layer=NodeLayer.TOPIC, content="c", session_key="s",
                   wave_amplitudes={"temporal": 0.1, "entity": 0.7, "causal": 0.7},
                   metadata={"producer_id": "Bob"})
    out = {"speaker": n.speaker_id}
    for t in ["entity", "temporal", "entity", "semantic"]:
        n.update_edge_counts(t)
    n.update_dominant_dimension()
    out["diversity"] = n.diversity_score
    out["dominant"] = n.dominant_dimension
    out["fairness"] = [n.get_fairness_weight({"entity": 0.9}), n.get_fairness_weight({"temporal": 0.5}),
                       n.get_fairness_weight({"temporal": 0.1})]
    levels = [n.activate(0.4) for _ in range(12)]
    out["activate"] = {"levels": levels, "heat": n.heat_score, "refractory_until": n.refractory_until.isoformat()}
    out["node_dict"] = n.to_dict()
    e = NeuralEdge(edge_id="e", source_id="a", target_id="b", edge_type=EdgeType.CAUSAL, base_weight=0.7, sign=-1.0)
    for _ in range(3):
        e.activate()
    out["edge"] = {"ltp": e.ltp_boost, "weight": e.effective_weight, "gate": e.gate(0.8), "dict": e.to_dict()}
    out["roundtrip_node"] = NeuralNode.from_dict(n.to_dict()).to_dict()
    out["roundtrip_edge"] = NeuralEdge.from_dict(e.to_dict()).to_dict()
    a = embeddings(3, 16, 5)
    out["cosine"] = {"a": a, "values": [cosine_similarity(a[0], a[1]), cosine_similarity(a[1], a[2]), cosine_similarity(a[0], [])]}
    return out


async def main():
    inputs, lsh_out, st_out = await lsh_and_storage()
    R.dump("foundation.json", {
        "py_random": py_random(),
        "np_random": np_random(),
        "blas": blas(),
        "inputs": inputs,
        "lsh": lsh_out,
        "storage": st_out,
        "data_types": data_types(),
    })


asyncio.run(main())
