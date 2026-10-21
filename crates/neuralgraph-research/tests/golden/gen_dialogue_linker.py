#!/usr/bin/env python3
"""Fixtures for the dialogue linker (``retrieval/dialogue_linker.py``) and Python's ``\\w`` class. Writes
``dialogue_linker.json``.

Two full LoCoMo conversations are ingested the way ``research/benchmarks/retrieval_eval.py::ingest`` does (same
node ids, session keys, speaker/temporal edges and edge ids through the shared uuid hook), with
``fake_embedding(text, 256)`` standing in for the embedding model; the node metadata is reduced to what the linker
reads (speaker). The linker is the reference module re-executed with insertion-ordered sets (see
``_ordered_sets.py``); the original module is run too and must agree on everything that does not depend on set
order. Its ``uuid`` is patched so ``uuid4().hex[:8]`` counts in the leading digits (Rust:
``dialogue_linker::set_deterministic_hex_ids``).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import unicodedata
import uuid as _uuid
from collections import Counter, defaultdict

import _reference as R
from _ordered_sets import load_ordered

import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.dialogue_linker as dl_orig
import NeuralGraph.research.retrieval.storage as storage_mod
from NeuralGraph.chat_memory.llm import fake_embedding

R.freeze_now([dt_mod, storage_mod])
R.ordered_ids([dt_mod])
dl = load_ordered("NeuralGraph.research.retrieval.dialogue_linker")
from NeuralGraph.research.retrieval.data_types import (  # noqa: E402
    EdgeType, NeuralEdge, NeuralNode, NodeLayer, generate_edge_id,
)
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402

DIM = 256
CONVS = [0, 1]
FULL_LINKED = 25


class HexIds:
    """``uuid`` stand-in whose ``uuid4().hex`` is ``f"{n:08x}" + "0" * 24``."""

    def __init__(self):
        self.n = 0

    def uuid4(self):
        self.n += 1
        return _uuid.UUID(int=self.n << 96)


def flatten_messages(conv):
    conversation = conv.get("conversation", conv)
    messages, i = [], 1
    while f"session_{i}" in conversation:
        dt = conversation.get(f"session_{i}_date_time", "")
        for msg in conversation[f"session_{i}"]:
            messages.append({"speaker": msg.get("speaker", "Unknown"), "text": msg.get("text", ""), "datetime": dt})
        i += 1
    return messages


async def ingest(conv_idx, conv):
    messages = flatten_messages(conv)
    storage = InMemoryNeuralGraphStorage()
    speaker_nodes = defaultdict(list)
    all_ids = []
    for idx, msg in enumerate(messages):
        node_id = f"msg_{conv_idx}_{idx}"
        node = NeuralNode(node_id=node_id, session_key=f"conv_{conv_idx}", content=msg["text"],
                          layer=NodeLayer.MESSAGE, embedding=fake_embedding(msg["text"], DIM),
                          metadata={"speaker": msg["speaker"], "datetime": msg["datetime"]})
        await storage.save_node(node)
        all_ids.append(node_id)
        speaker_nodes[msg["speaker"].lower()].append(node_id)
    for ids in speaker_nodes.values():
        for i, src in enumerate(ids):
            for j in range(i + 1, min(i + 4, len(ids))):
                await storage.save_edge(NeuralEdge(edge_id=generate_edge_id(), source_id=src, target_id=ids[j],
                                                   edge_type=EdgeType.ENTITY, base_weight=0.8))
    for i in range(len(all_ids) - 1):
        await storage.save_edge(NeuralEdge(edge_id=generate_edge_id(), source_id=all_ids[i], target_id=all_ids[i + 1],
                                           edge_type=EdgeType.TEMPORAL, base_weight=0.85,
                                           metadata={"direction": "before"}))
    nodes = [await storage.get_node(n) for n in all_ids]
    return storage, nodes


def agg_dict(a):
    return {"aggregator_id": a.aggregator_id, "aggregator_type": a.aggregator_type.value, "session_key": a.session_key,
            "message_ids": list(a.message_ids), "topic": a.topic, "referent": a.referent, "mentions": list(a.mentions),
            "temporal_expression": a.temporal_expression, "resolved_date": a.resolved_date,
            "embedding_sha256": None if a.embedding is None else sha(a.embedding),
            "binding_weights": dict(a.binding_weights), "cohesion_score": a.cohesion_score}


def binding_dict(b):
    return {"binding_id": b.binding_id, "binding_type": b.binding_type.value, "source_id": b.source_id,
            "target_id": b.target_id, "source_span": b.source_span, "target_span": b.target_span,
            "confidence": b.confidence, "temporal_relation": b.temporal_relation}


def sha(obj):
    return hashlib.sha256(json.dumps(obj).encode()).hexdigest()


def run_linker(module, storage, nodes, conv_idx):
    module.uuid = HexIds()
    linker = module.DialogueLinker(storage)
    aggs, binds = asyncio.run(linker.process_dialogue_sequence(nodes, f"conv_{conv_idx}"))
    return linker, aggs, binds


def order_free(aggs, binds):
    """What must not depend on set iteration order."""
    a = Counter((x.aggregator_type.value, tuple(sorted(x.message_ids)), x.cohesion_score,
                 x.temporal_expression, x.topic if x.aggregator_type.value != "exchange" else len(x.topic))
                for x in aggs)
    b = Counter((x.binding_type.value, x.source_id, x.source_span, x.confidence) for x in binds)
    return a, b


def conversation_case(conv_idx, conv):
    R.UUIDS.reset()
    storage, nodes = asyncio.run(ingest(conv_idx, conv))
    edges = sorted(asyncio.run(storage.get_all_edges(f"conv_{conv_idx}")), key=lambda e: e.edge_id)
    edge_rows = [[e.edge_id, e.source_id, e.target_id, e.edge_type.value, e.base_weight, e.metadata] for e in edges]
    linker, aggs, binds = run_linker(dl, storage, nodes, conv_idx)
    _, aggs_o, binds_o = run_linker(dl_orig, storage, nodes, conv_idx)
    assert order_free(aggs, binds) == order_free(aggs_o, binds_o), "ordered-set copy diverged from the original"

    ids = [n.node_id for n in nodes]
    linked = {i: [list(t) for t in linker.get_linked_messages(i)] for i in ids}
    topics = [a.topic for a in aggs if a.aggregator_type.value == "topic_thread"][:5] + ["", "zzz-none"]
    corefs = [(b.source_id, b.source_span) for b in binds if b.binding_type.value == "refers_to"][:40]
    return {
        "conv_idx": conv_idx,
        "n_messages": len(nodes),
        "edges_sha256": sha(edge_rows),
        "edges_head": edge_rows[:10],
        "edge_count": len(edge_rows),
        "diversity_scores": [n.diversity_score for n in nodes],
        "aggregators": [agg_dict(a) for a in aggs],
        "bindings": [binding_dict(b) for b in binds],
        "stored_aggregator_ids": list(linker._aggregators),
        "stored_binding_count": len(linker._bindings),
        "linked_sha256": [sha(linked[i]) for i in ids],
        "linked_full": {i: linked[i] for i in ids[:FULL_LINKED]},
        "linked_lengths": [len(linked[i]) for i in ids],
        "exchange_pairs": [linker.get_exchange_pair(i) for i in ids],
        "atomic_units": linker.get_atomic_retrieval_units(ids[::7] + ids[:3]),
        "aggregators_for_message": {i: [a.aggregator_id for a in linker.get_aggregators_for_message(i)]
                                    for i in ids[:40]},
        "topic_context": [[t, linker.get_topic_context(t)] for t in topics],
        "resolve_coreference": [[s, p, linker.resolve_coreference(s, p)] for s, p in corefs]
                               + [[ids[0], "it", linker.resolve_coreference(ids[0], "it")]],
    }


def helper_cases():
    linker = dl.DialogueLinker(None)
    texts = [
        "", "   ", "What do you think?", "how are you", "Is it raining", "  Wow, that's great!", "Oh nice",
        "I went to Paris with Melanie and Caroline yesterday.", "The Eiffel Tower was beautiful at sunset!",
        "Yoga\U0001F9D8‍♀️ is my thing", "Café Müller's réunion — CITY park", "Sure thing: next week",
        "Caroline: I love the Central Park tower", "Thanks!! It's been ages since last year...", "x" * 60 + ": late",
        "THE AND But For WITH This That Them", "\U0001F44D Awesome", "re-read Lord_of_the_Rings 2nd time",
    ]
    out = {"is_question": [], "is_reaction": [], "extract_entities": [], "find_pronouns": [], "categorize": [],
           "get_speaker": [], "extract_topic": []}
    for t in texts:
        out["is_question"].append([t, linker._is_question(t)])
        try:
            out["is_reaction"].append([t, linker._is_reaction(t)])
        except IndexError as e:
            out["is_reaction"].append([t, f"IndexError: {e}"])
        out["extract_entities"].append([t, list(linker._extract_entities(t))])
        out["find_pronouns"].append([t, [list(p) for p in linker._find_pronouns(t)]])
        for speaker in ["", "Melanie"]:
            node = NeuralNode(node_id="n", layer=NodeLayer.MESSAGE, content=t, metadata={"speaker": speaker})
            out["get_speaker"].append([speaker, t, linker._get_speaker(node)])
    for e in ["Paris", "Central Park", "Eiffel Tower", "Melanie", "CityHall", "ab", "x"]:
        out["categorize"].append([e, linker._categorize_entity(e)])
    cats = ["thing", "place", "person", "person_male", "person_plural", "thing_near", "time"]
    out["categories_match"] = [[p, e, linker._categories_match(p, e)] for p in cats for e in ["thing", "place", "person"]]
    for a, b in zip(texts, texts[1:] + texts[:1]):
        out["extract_topic"].append([a, b, linker._extract_topic(a, b)])
    out["extract_topic"].append(["Paris Melanie Tower here", "Tower and Paris and Melanie", ""])
    out["extract_topic"][-1][2] = linker._extract_topic(out["extract_topic"][-1][0], out["extract_topic"][-1][1])
    return out


def small_sequences():
    """Hand-made sequences: speaker fallbacks, empty embeddings, the IndexError, linker reuse across calls."""
    cases = []

    def node(i, content, speaker="", emb=True, entity_ids=None):
        return NeuralNode(node_id=f"s{i}", layer=NodeLayer.MESSAGE, content=content,
                          embedding=fake_embedding(content, 32) if emb else None,
                          metadata={"speaker": speaker} if speaker else {}, entity_ids=entity_ids or [])

    seqs = {
        "prefix_speakers": [node(0, "Anna: Did you see Paris yesterday?"), node(1, "Ben: Yes, Paris was lovely."),
                            node(2, "Anna: And the Tower? It was tall"), node(3, "no speaker here, it is fine"),
                            node(4, "nobody either but Paris again today")],
        "entity_ids_and_no_embeddings": [node(0, "We met Joanna at the park", "A", emb=False, entity_ids=["joanna", "Park"]),
                                         node(1, "Joanna loves the park too", "B", entity_ids=["Joanna"]),
                                         node(2, "Then Joanna left. Joanna!", "A", emb=False),
                                         node(3, "She said thanks. However it rained", "B")],
        "whitespace_reply": [node(0, "Hello there?", "A"), node(1, "   ", "B")],
        "single": [node(0, "Just one message about Rome", "A")],
    }
    for name, msgs in seqs.items():
        dl.uuid = HexIds()
        linker = dl.DialogueLinker(None)
        try:
            aggs, binds = asyncio.run(linker.process_dialogue_sequence(msgs, "s"))
            # the same linker again: indexes accumulate, ids keep counting
            aggs2, binds2 = asyncio.run(linker.process_dialogue_sequence(msgs[::-1], "s"))
            case = {"name": name, "aggregators": [agg_dict(a) for a in aggs + aggs2],
                    "bindings": [binding_dict(b) for b in binds + binds2],
                    "linked": {m.node_id: [list(t) for t in linker.get_linked_messages(m.node_id)] for m in msgs},
                    "error": None}
        except IndexError as e:
            case = {"name": name, "error": f"IndexError: {e}"}
        cases.append(case)
    # __post_init__ ids
    dl.uuid = HexIds()
    cases.append({"name": "post_init_ids",
                  "ids": [dl.DialogueAggregator(aggregator_id="", aggregator_type=dl.AggregatorType.EXCHANGE,
                                                session_key="s").aggregator_id,
                          dl.CrossMessageBinding(binding_id="", binding_type=dl.BindingType.RESPONDS_TO,
                                                 source_id="a", target_id="b").binding_id]})
    return cases


def ranges(pred):
    out, start = [], None
    for cp in range(sys.maxunicode + 1):
        v = pred(cp)
        if v and start is None:
            start = cp
        if not v and start is not None:
            out.append([start, cp - 1])
            start = None
    if start is not None:
        out.append([start, sys.maxunicode])
    return out


def main():
    data = json.load(open(R.DATASETS / "locomo10.json"))
    word = re.compile(r"\w")
    R.dump("dialogue_linker.json", {
        "dim": DIM,
        "conversations": [conversation_case(i, data[i]) for i in CONVS],
        "helpers": helper_cases(),
        "sequences": small_sequences(),
        "py_word": {"unicode_version": unicodedata.unidata_version,
                    "word_ranges": ranges(lambda cp: bool(word.match(chr(cp)))),
                    "unassigned_ranges": ranges(lambda cp: unicodedata.category(chr(cp)) == "Cn")},
    })


if __name__ == "__main__":
    main()
