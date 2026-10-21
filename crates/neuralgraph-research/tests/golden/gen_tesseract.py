#!/usr/bin/env python3
"""Fixtures for ``retrieval/tesseract.py``: every question predicate and query parser over all LoCoMo questions,
the content helpers over all LoCoMo messages (``tesseract_predicates.json``), and end-to-end ``Tesseract.retrieve``
(plus the per-store results) on LoCoMo conversations ingested like ``research/benchmarks/retrieval_eval.py::ingest``
(``tesseract_retrieve.json``).

Ingestion differences from ``retrieval_eval.ingest`` (none of them is read by Tesseract): embeddings are the
deterministic ``fake_embedding(text)`` (256 dims; the Rust side computes the same vectors), the ``keywords``
metadata (``service.extract_keywords``, ported elsewhere) is left out, ``entities`` is in first-occurrence order
instead of set order, and the DialogueLinker step is skipped (ported elsewhere; it keeps its aggregators and
bindings in its own memory and never writes to the storage or the nodes, so Tesseract's results are the same).

Set order: Python iterated sets in two places that reach Tesseract's output, the storage indexes (node order,
hence the order of equal charges) and the novelty token sets (a float sum). Both were hash-randomised. Here the
reference storage's index sets and ``_tokenize_for_novelty`` are replaced by an insertion-ordered set, the order
the Rust port uses, so charges and rankings compare exactly. ``created_at`` is the frozen "now" (naive), so
``msg_year`` is 2026 as it would have been on that day."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

import _reference as R

import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.external_retriever as ext_mod
import NeuralGraph.research.retrieval.storage as storage_mod
import NeuralGraph.research.retrieval.tesseract as TS
import NeuralGraph.temporal_utils as TU
from NeuralGraph.chat_memory.llm import fake_embedding

R.freeze_now([dt_mod, storage_mod])
R.ordered_ids([dt_mod])
from NeuralGraph.research.retrieval.data_types import EdgeType, NeuralEdge, NeuralNode, NodeLayer, generate_edge_id  # noqa: E402

CREATED_AT = R.FIXED_NOW.replace(tzinfo=None)
RETRIEVE_CONVS = [0, 1]
STORE_QUESTIONS = 40


class OrderedSet:
    """The parts of ``set`` the reference storage and novelty code use, iterating in insertion order."""

    def __init__(self, items=()):
        self._d = dict.fromkeys(items)

    def add(self, x):
        self._d[x] = None

    def discard(self, x):
        self._d.pop(x, None)

    def remove(self, x):
        del self._d[x]

    def clear(self):
        self._d.clear()

    def update(self, items):
        for x in items:
            self._d[x] = None

    def __contains__(self, x):
        return x in self._d

    def __iter__(self):
        return iter(list(self._d))

    def __len__(self):
        return len(self._d)

    def __and__(self, other):
        return OrderedSet(x for x in self._d if x in other)


class OrderedStorage(storage_mod.InMemoryNeuralGraphStorage):
    def __init__(self):
        super().__init__()
        self._nodes_by_session = defaultdict(OrderedSet)
        self._edges_from = defaultdict(OrderedSet)
        self._edges_to = defaultdict(OrderedSet)
        self._edges_by_session = defaultdict(OrderedSet)
        self._nodes_by_entity = defaultdict(OrderedSet)
        self._nodes_by_layer = defaultdict(lambda: defaultdict(OrderedSet))


_NOVELTY_RE = re.compile(r'\b[a-z0-9]{3,}\b')


def _ordered_tokenize_for_novelty(text):
    return OrderedSet(w for w in _NOVELTY_RE.findall(text.lower()) if w not in TS._NOVELTY_STOPWORDS)


TS._tokenize_for_novelty = _ordered_tokenize_for_novelty


def digest(items) -> str:
    return hashlib.sha256(" ".join(sorted(items)).encode()).hexdigest()[:16]


def flatten_messages(conv):
    conversation = conv.get("conversation", conv)
    messages, i = [], 1
    while f"session_{i}" in conversation:
        dt = conversation.get(f"session_{i}_date_time", "")
        for msg in conversation[f"session_{i}"]:
            messages.append({"speaker": msg.get("speaker", "Unknown"), "text": msg.get("text", ""), "datetime": dt})
        i += 1
    return messages


def predicates(q):
    qt = TS.detect_query_type(q)
    return [[qt[k] for k in qt], list(ext_mod.is_open_domain_query(q)), TS.should_use_open_domain_infer(q),
            TS.looks_open_domain_question(q), TS.is_yes_no_question(q),
            list(TS.detect_list_question_universal(q)), TS.is_open_domain_world_query(q)]


def parsers(q, stores):
    t, e, r, a = stores
    ql = q.lower()
    tk = t._extract_temporal_keywords(ql)
    neg, pos = a._parse_adversarial(q)
    return {
        "topic_universal": digest(TS.extract_topic_keywords_universal(q)),
        "temporal_keywords": sorted(tk),
        "decay": t._adjust_temporal_decay(ql, tk),
        "temporal_topics": digest(t._extract_topic_keywords(ql)),
        "entity_entities": sorted(e._extract_entities(q)),
        "entity_keywords": digest(e._extract_keywords(q)),
        "reasoning_entities": sorted(r._extract_entities(q)),
        "reasoning_keywords": digest(r._extract_keywords(q)),
        "adversarial": [sorted(neg), sorted(pos), sorted(a._extract_entities(q))],
        "novelty_tokens": list(TS._tokenize_for_novelty(q)),
    }


async def ingest(conv_idx, conv):
    messages = flatten_messages(conv)
    storage = OrderedStorage()
    speaker_nodes = defaultdict(list)
    all_ids = []
    for idx, msg in enumerate(messages):
        message_date = TU.parse_datetime_flexible(msg["datetime"])
        temporal = TU.generate_temporal_tokens(message_date, msg["text"])
        _, resolved_dates = TU.resolve_relative_dates(msg["text"], message_date)
        explicit_date = TU.extract_explicit_date(msg["text"], message_date)
        duration = TU.extract_duration_metadata(msg["text"], message_date)
        resolved_date, source = None, None
        if explicit_date:
            resolved_date, source = explicit_date, "explicit"
        elif resolved_dates:
            vals = list(dict.fromkeys(v for v in resolved_dates.values() if isinstance(v, str) and re.search(r"\d", v)))
            if len(vals) == 1:
                resolved_date, source = vals[0], "relative"
        if not resolved_date and message_date:
            resolved_date, source = message_date.strftime("%Y-%m-%d"), "message"
        entities = [e for e in dict.fromkeys(re.findall(r"\b[A-Z][a-z]+\b", msg["text"])) if e != "I"]
        node_id = f"msg_{conv_idx}_{idx}"
        node = NeuralNode(
            node_id=node_id, session_key=f"conv_{conv_idx}", content=msg["text"],
            layer=NodeLayer.MESSAGE, embedding=fake_embedding(msg["text"]), created_at=CREATED_AT,
            metadata={
                "speaker": msg["speaker"], "datetime": msg["datetime"], "entities": entities,
                "temporal_tokens": temporal["date_tokens"], "temporal_metadata": temporal["temporal_metadata"],
                "resolved_date": resolved_date, "resolved_date_source": source, "explicit_date": explicit_date,
                "duration_years": duration.get("duration_years"), "duration_months": duration.get("duration_months"),
                "since_year": duration.get("since_year"), "since_date": duration.get("since_date"),
                "date_tokens": temporal["date_tokens"], "temporal": temporal["temporal_metadata"],
                "resolved_dates": resolved_dates,
            },
        )
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
                                           edge_type=EdgeType.TEMPORAL, base_weight=0.85, metadata={"direction": "before"}))
    return storage


def ranked(results):
    """``[(node, charge)]`` -> ``[[message index, charge]]`` (node ids are ``msg_<conv>_<index>``)."""
    return [[int(n.node_id.rsplit("_", 1)[1]), c] for n, c in results]


async def store_results(tess, q, qvec, session):
    """The four store calls exactly as ``Tesseract.retrieve`` makes them."""
    query_types = TS.detect_query_type(q)
    effective = TU.expand_temporal_query(q) if query_types.get(TS.QueryType.TEMPORAL, 0) > 0.3 else q
    all_nodes = await tess._storage.get_nodes_by_session(session)
    novelty = TS._build_novelty_context(q, all_nodes)
    kw = dict(all_nodes=all_nodes, novelty_context=novelty)
    return {
        "effective": effective,
        "temporal": ranked(await tess._temporal_store.retrieve(effective, qvec, session, None, limit=40, **kw)),
        "entity": ranked(await tess._entity_store.retrieve(q, qvec, session, limit=50, **kw)),
        "reasoning": ranked(await tess._reasoning_store.retrieve(q, qvec, session, limit=40, **kw)),
        "adversarial": ranked(await tess._adversarial_store.retrieve(q, qvec, session, limit=30, **kw)),
        "novelty": None if novelty is None else [list(novelty.query_tokens), novelty.token_idf, novelty.max_idf],
    }


async def guarded(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 - the raised type is the observable outcome
        return {"error": type(exc).__name__}


async def main():
    data = json.load(open(R.DATASETS / "locomo10.json"))
    probe = TS.Tesseract(OrderedStorage())
    stores = (probe._temporal_store, probe._entity_store, probe._reasoning_store, probe._adversarial_store)

    questions = []
    for conv in data:
        for qa in conv["qa"]:
            q = qa["question"]
            questions.append({"p": predicates(q), "s": parsers(q, stores)})

    t = probe._temporal_store
    messages = []
    for ci, conv in enumerate(data):
        for mi, m in enumerate(flatten_messages(conv)):
            lower = m["text"].lower()
            rel = t._extract_relative_time_type(lower)
            year = t._extract_event_year_from_content(lower, 2023)
            if rel is not None or year is not None:
                messages.append([ci, mi, rel, year])

    retrieval = []
    for ci in RETRIEVE_CONVS:
        storage = await ingest(ci, data[ci])
        tess = TS.Tesseract(storage)
        session = f"conv_{ci}"
        conv_out = {"conv": ci, "questions": [], "stores": [], "variants": []}
        for k, qa in enumerate(data[ci]["qa"]):
            q = qa["question"]
            qvec = fake_embedding(q)
            res = await tess.retrieve(query_text=q, query_embedding=qvec, session_key=session, limit=50,
                                      auto_expand_temporal=True)
            conv_out["questions"].append(ranked(res))
            if k < STORE_QUESTIONS:
                conv_out["stores"].append(await store_results(tess, q, qvec, session))
            if k < 6:
                variants = {
                    "no_expand": ranked(await tess.retrieve(q, qvec, session, limit=80, auto_expand_temporal=False)),
                    "reference_naive": ranked(await tess.retrieve(q, qvec, session, reference_time=datetime(2026, 7, 1, 12, 0), limit=20)),
                    "reference_aware": await guarded(tess.retrieve(q, qvec, session, reference_time=datetime(2026, 7, 1, tzinfo=timezone.utc), limit=20)),
                    "unknown_session": ranked(await tess.retrieve(q, qvec, "nope", limit=20)),
                }
                conv_out["variants"].append(variants)
        retrieval.append(conv_out)

    R.dump("tesseract_retrieve.json", {"retrieval": retrieval})
    R.dump("tesseract_predicates.json", {
        "questions": questions,
        "messages": messages,
        "morphology": {w: sorted(TS._expand_morphology_basic(w)) for w in
                       ["camping", "moved", "studies", "running", "hopped", "tried", "bus", "class", "make", "ed",
                        "painted", "dancing", "skiing", "agreed", "cities", "x", "Caroline", "café"]},
        "robust": [[s, TS._robust_similarity(s)] for s in [-0.5, 0.0, 0.1, 0.33, 0.5, 0.999, 1.0, 1.7]],
    })


asyncio.run(main())
