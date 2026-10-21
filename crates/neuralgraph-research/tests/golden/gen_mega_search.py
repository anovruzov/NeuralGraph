#!/usr/bin/env python3
"""Fixtures for mega search (``retrieval/mega_search.py``). Writes ``mega_search.json``.

LoCoMo conversation 1 is indexed the way ``two_agent_eval.py::combined_main`` does it: message nodes, window-2
question+reply pair nodes, and the LLM entity graph from ``research/results/kg_cache/conv1.json`` (embedded in the
fixture; the generator reads ``KG_CACHE`` or the local copy below), with and without ``_union_regex``. Embeddings are
``fake_embedding(text, 256)``. Every QA question runs through every channel combination the harnesses use
(``combined_main``'s VK / VKG / VKG+extra / VG / VKG with G x2 / VG at 50 / VG+extra, the KG variants, and
``runner.py``'s mega mode, which is VG+extra at 50), with the speaker pool routing of the harness; a stand-in
ranking replaces Tesseract's as the extra channel.

The module runs with insertion-ordered sets (``_ordered_sets.py``); its graph-channel score maps are checked
against the original module's to 1e-12 (the original's differ only in summation order).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import _reference as R
from _ordered_sets import load_ordered

import NeuralGraph.research.retrieval.mega_search as mega_orig
from NeuralGraph.chat_memory.llm import fake_embedding

M = load_ordered("NeuralGraph.research.retrieval.mega_search")
from NeuralGraph.research.retrieval.data_types import NeuralNode, NodeLayer  # noqa: E402

CONV = 1
DIM = 256
TOP_K, PAIR_EXTRA = 50, 30
W = TOP_K + PAIR_EXTRA
FULL = 4
KG_CACHE = Path(os.environ.get("KG_CACHE", "/Users/nurmanmahammadov/Desktop/NeuralGraph/demo/results/kg_cache/conv1.json"))


def sha(obj):
    return hashlib.sha256(json.dumps(obj).encode()).hexdigest()


def flatten_messages(conv):
    c = conv.get("conversation", conv)
    messages, i = [], 1
    while f"session_{i}" in c:
        dt = c.get(f"session_{i}_date_time", "")
        for msg in c[f"session_{i}"]:
            messages.append({"speaker": msg.get("speaker", "Unknown"), "text": msg.get("text", ""), "datetime": dt})
        i += 1
    return messages


def build(conv):
    messages = flatten_messages(conv)
    nodes = [NeuralNode(node_id=f"msg_{CONV}_{i}", session_key=f"conv_{CONV}", content=m["text"],
                        layer=NodeLayer.MESSAGE, embedding=fake_embedding(m["text"], DIM),
                        metadata={"speaker": m["speaker"], "datetime": m["datetime"]})
             for i, m in enumerate(messages)]
    pairs = []
    for k, i in enumerate(range(1, len(messages))):
        text = "\n".join(f"[{messages[j]['speaker']}] {messages[j]['text']}" for j in (i - 1, i))
        pairs.append(NeuralNode(node_id=f"pair2_{CONV}_{k}", session_key=f"conv_{CONV}", content=text,
                                layer=NodeLayer.MESSAGE, embedding=fake_embedding(text, DIM),
                                metadata={"speaker": messages[i]["speaker"], "datetime": messages[i]["datetime"],
                                          "pair_members": [nodes[i].node_id, nodes[i - 1].node_id]}))
    return nodes, pairs


def stand_in_ranking(question, nodes, pool_ids):
    """Deterministic substitute for Tesseract's ranking (the extra channel): pool messages sharing the first
    query tokens, then a few ids from outside the pool, in a fixed order."""
    toks = M.tokenize(question)[:2]
    hits = [n.node_id for n in nodes if n.node_id in pool_ids and any(t in n.content.lower() for t in toks)]
    return hits[:TOP_K]


def capture_graph_scores(module, index, question, seeds, pool):
    """The final ``p`` of graph_rank, captured by shadowing ``sorted`` in the module."""
    seen = []

    def spy(items, key=None, reverse=False):
        items = list(items)
        seen.append(items)
        return sorted(items, key=key, reverse=reverse)

    module.sorted = spy
    try:
        index.graph_rank(question, seeds, pool, 100)
    finally:
        del module.sorted
    return dict(seen[-1]) if seen else {}


def main():
    data = json.load(open(R.DATASETS / "locomo10.json"))
    conv = data[CONV]
    nodes, pairs = build(conv)
    kg = json.load(open(KG_CACHE))
    speakers = sorted({n.metadata["speaker"].lower() for n in nodes})

    mega = M.MegaIndex(nodes, pairs)
    mega_kg = M.MegaIndex(nodes, pairs, kg=kg)
    mega_kgu = M.MegaIndex(nodes, pairs, kg=kg | {"_union_regex": True})
    orig = {"plain": mega_orig.MegaIndex(nodes, pairs), "kg": mega_orig.MegaIndex(nodes, pairs, kg=kg)}

    questions = [qa["question"] for qa in conv["qa"]]
    cases, full, channels = [], [], []
    for qi, q in enumerate(questions):
        qvec = fake_embedding(q, DIM)
        named = [sp for sp in speakers if sp in q.lower()]
        pool_nodes = [n for n in nodes if n.metadata["speaker"].lower() == named[0]] if len(named) == 1 else nodes
        pool_ids = {n.node_id for n in pool_nodes}
        tess_ids = stand_in_ranking(q, nodes, pool_ids)
        variants = {
            "VK": mega.retrieve(q, qvec, pool_ids, k=W, channels="VK"),
            "VKG": mega.retrieve(q, qvec, pool_ids, k=W, channels="VKG"),
            "VKG_tess": mega.retrieve(q, qvec, pool_ids, k=W, channels="VKG", extra_rankings=[tess_ids]),
            "VG": mega.retrieve(q, qvec, pool_ids, k=W, channels="VG"),
            "VKG_g2": mega.retrieve(q, qvec, pool_ids, k=W, channels="VKG", weights={"G": 2.0}),
            "VG50": mega.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG"),
            "VGT50": mega.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG", extra_rankings=[tess_ids]),
            "kg_VGT50": mega_kg.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG", extra_rankings=[tess_ids]),
            "kg_G": mega_kg.retrieve(q, qvec, pool_ids, k=W, channels="G"),
            "kg_VG50": mega_kg.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG"),
            "kgu_VGT50": mega_kgu.retrieve(q, qvec, pool_ids, k=TOP_K, channels="VG", extra_rankings=[tess_ids]),
            "all_default": mega.retrieve(q, qvec),
            "K_weighted_X": mega.retrieve(q, qvec, pool_ids, k=20, channels="KX", weights={"K": 0.5, "X": 3.0},
                                          extra_rankings=[tess_ids, tess_ids[::-1]], depth=30),
        }
        out = {name: [[n.node_id, s] for n, s in res] for name, res in variants.items()}
        cases.append({"question": q, "pool": "all" if len(named) != 1 else named[0], "tess_ids": tess_ids,
                      "sha256": {name: sha(v) for name, v in out.items()}})
        if qi < FULL:
            full.append(out)
        if qi < 12:
            seeds = [m for m, _ in M.rrf([mega.vector_rank(qvec, pool_ids, 100), mega.keyword_rank(q, pool_ids, 100)])[:10]]
            ch = {"seeds": seeds,
                  "vector": mega.vector_rank(qvec, pool_ids, 100),
                  "keyword": mega.keyword_rank(q, pool_ids, 100),
                  "graph": mega.graph_rank(q, seeds, pool_ids, 100),
                  "kg_graph": mega_kg.graph_rank(q, seeds, pool_ids, 100),
                  "graph_no_temporal": mega.graph_rank(q, seeds, pool_ids, 30, alpha=0.3, iters=5, hop_temporal=False),
                  "graph_scores": capture_graph_scores(M, mega, q, seeds, pool_ids),
                  "kg_graph_scores": capture_graph_scores(M, mega_kg, q, seeds, pool_ids)}
            # the ordered-set copy must agree with the original module up to summation order
            for name, ordered_key in (("plain", "graph_scores"), ("kg", "kg_graph_scores")):
                o = capture_graph_scores(mega_orig, orig[name], q, seeds, pool_ids)
                assert o.keys() == ch[ordered_key].keys(), (qi, name)
                for m, v in o.items():
                    assert abs(v - ch[ordered_key][m]) <= 1e-12 * max(1.0, abs(v)), (qi, name, m)
            channels.append(ch)

    errors = {}
    try:
        M.MegaIndex([], [])
    except ZeroDivisionError as e:
        errors["empty"] = f"ZeroDivisionError: {e}"
    try:
        mega.retrieve("q", fake_embedding("q", DIM), None, k=5, channels="", extra_rankings=[["ghost"]])
    except KeyError as e:
        errors["unknown_extra"] = f"KeyError: {e}"

    R.dump("mega_search.json", {
        "conv_idx": CONV, "dim": DIM, "speakers": speakers, "kg": kg,
        "cases": cases, "full": full, "channels": channels, "errors": errors,
        "tokenize": [[n.content, M.tokenize(n.content), list(M.entities_of(n.content))] for n in nodes[:30]],
        "tokenize_sha256": sha([[M.tokenize(n.content), list(M.entities_of(n.content))] for n in nodes]),
        "rrf": [M.rrf([["a", "b", "c"], ["c", "a"], []]), M.rrf([["a", "b"], ["b"]], [0.5, 2.0], k=1),
                M.rrf([["x", "y"]], []), M.rrf([])],
        "entities_edge": [[t, list(M.entities_of(t))] for t in
                          ["Hi Bob, Café x2 Ann‍ I OK_ Zed", "Melanie's LGBTQ group: Pride! pride parade",
                           "I I I", "", "Yoga\U0001F9D8‍♀️ Paris"]],
    })


if __name__ == "__main__":
    main()
