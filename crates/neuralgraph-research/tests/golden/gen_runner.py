#!/usr/bin/env python3
"""Fixtures for the LoCoMo harnesses ``research/benchmarks/runner.py``, ``two_agent_eval.py``, ``retrieval_eval.py``
and ``call_center.py``. Writes ``runner.json`` and ``runner_routing.json`` (routing and context cases).

Deterministic helpers on the real datasets:
* ``check_gold_in_memories_substring`` for every LoCoMo QA (category 5 too) against 15 messages per question (its
  evidence messages first, then a fixed spread of the conversation) at several ``top_k``, plus edge-case golds;
  ``compute_retrieval_summary``; ``save_results`` (metadata, latency statistics, category stats) on crafted rows;
  ``parse_judge_label`` and ``judge_answer`` (auto-pass and the judge prompt) on crafted replies.
* the message nodes ``runner.py`` builds (all metadata) for two conversations, and for every question of those the
  context lines (TEMPORAL and plain), ``retrieved_memories`` and the direct (extracted) answer, copied from the
  inline code of ``run_benchmark``;
* the routing decisions (query mode before/after the OPEN_DOMAIN_INFER upgrade, list detection, memories needed,
  answer and retry modes for every H5 flag combination) for every LoCoMo question, copied from ``run_benchmark``;
* ``expand_via_graph``, ``build_pair_nodes`` / ``unpair`` and ``cosine_topk`` on conversation 1;
* ``two_agent_eval``'s ``named_speakers``, ``swap_name``, ``gold_parts``, ``evidence_speakers``,
  ``substring_gold_speakers``, ``merge_by_charge`` / ``merge_round_robin`` / ``speaker_boost`` / ``append_extra``,
  ``hits3`` and its ``build_pair_nodes``; the bytes ``cached_embeddings`` writes.

End to end, with a scripted model (``fake_embedding(text, 256)`` embeddings, replies picked by the sha1 of the
prompt), "now" frozen and ``time.perf_counter`` frozen at 0: ``runner.run_benchmark`` on conversation 1 in every
retrieval mode with the H5 flags and the OpenAI judge path, recording stdout, the report and every prompt; and
``call_center.run_benchmark`` with a fake Ollama session.

Set order: the reference's storage indexes, Tesseract's novelty token sets, the dialogue linker and mega search are
made insertion-ordered (the orders the Rust port uses; Python's were hash- or uuid-randomised), and the runner's
``cosine_topk`` is keyed by node ids instead of ``id(nodes)`` (the CPython id-reuse bug, see the Rust module docs).

Run from the repository root: ``PYTHONHASHSEED=0 python crates/neuralgraph-research/tests/golden/gen_runner.py``.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import sys
import tempfile
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

import _reference as R
from _ordered_sets import load_ordered

# run_benchmark reads research/datasets relative to the reference tree, which holds only the code
_ref_datasets = R.REF / "research" / "datasets"
if not _ref_datasets.exists():
    _ref_datasets.symlink_to(R.DATASETS)

import NeuralGraph.llm_backend as llm_backend
import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.storage as storage_mod
import NeuralGraph.research.retrieval.tesseract as TS
import NeuralGraph.research.retrieval.mega_search as MS
import NeuralGraph.research.retrieval.dialogue_linker as DL
from NeuralGraph.chat_memory.llm import fake_embedding


class OrderedSet:
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
TS._tokenize_for_novelty = lambda text: OrderedSet(
    w for w in _NOVELTY_RE.findall(text.lower()) if w not in TS._NOVELTY_STOPWORDS)
storage_mod.InMemoryNeuralGraphStorage = OrderedStorage
DL.DialogueLinker = load_ordered("NeuralGraph.research.retrieval.dialogue_linker").DialogueLinker
MS.MegaIndex = load_ordered("NeuralGraph.research.retrieval.mega_search").MegaIndex

import runner  # noqa: E402
import retrieval_eval  # noqa: E402
import two_agent_eval as TA  # noqa: E402
import call_center as CC  # noqa: E402
import NeuralGraph.research.retrieval.service as service  # noqa: E402
import NeuralGraph.temporal_utils as TU  # noqa: E402
from NeuralGraph.research.retrieval.tesseract import (  # noqa: E402
    detect_list_question_universal, is_open_domain_world_query, looks_open_domain_question,
    should_use_open_domain_infer)

for m in (runner, retrieval_eval, TA, CC):
    if hasattr(m, "InMemoryNeuralGraphStorage"):
        m.InMemoryNeuralGraphStorage = OrderedStorage
    if hasattr(m, "DialogueLinker"):
        m.DialogueLinker = DL.DialogueLinker
R.freeze_now([runner, dt_mod, storage_mod, CC, TA, retrieval_eval])
FROZEN_TIME = type("T", (), {"perf_counter": staticmethod(lambda: 0.0), "time": staticmethod(lambda: 0.0)})
runner.time = FROZEN_TIME
CC.time = FROZEN_TIME

_mats = {}


def cosine_topk_fixed(nodes, query_vec, k, _cache=None):
    """runner.cosine_topk with the matrix cache keyed by the node ids (the id(nodes) reuse bug fixed)."""
    import numpy as np
    key = tuple(n.node_id for n in nodes)
    if key not in _mats:
        _mats.clear()
        M = np.array([n.embedding for n in nodes], dtype=np.float32)
        M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
        _mats[key] = M
    M = _mats[key]
    q = np.array(query_vec, dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9
    sims = M @ q
    order = np.argsort(-sims, kind="stable")[:k]
    return [(nodes[i], float(sims[i])) for i in order]


runner.cosine_topk = cosine_topk_fixed

LOCOMO = json.loads((R.DATASETS / "locomo10.json").read_text())
CATEGORIES = runner.CATEGORIES
DIM = 256


def sha1(t: str) -> str:
    return hashlib.sha1(t.encode()).hexdigest()


def flatten(conv):
    return retrieval_eval.flatten_messages(conv)


# ============================================================================== scripted model
REPLIES = [
    "7", "3", "Not mentioned in the memories", "She went camping with her kids", '{"label": "CORRECT"}',
    "The answer is WRONG", '{"label": "WRONG"}', "10", "May 2023", '["likes pottery", "has two kids"]',
    'Reasoning... {"label": "correct"}', "no information about that",
]


def scripted_reply(prompt: str) -> str:
    return REPLIES[int(sha1(prompt)[:8], 16) % len(REPLIES)]


PROMPTS: list[str] = []


async def fake_llm_generate(session, prompt, model=None, base_url=None, temperature=0.0, max_tokens=256,
                            timeout_seconds=60):
    PROMPTS.append(prompt)
    return scripted_reply(prompt)


llm_backend.llm_generate = fake_llm_generate


async def fake_get_embedding(http, text, config=None):
    return fake_embedding(text, DIM)


async def fake_embed_batch(http, texts):
    return [fake_embedding(t, DIM) for t in texts]


runner.get_embedding = fake_get_embedding
runner.embed_batch = fake_embed_batch


class _Resp:
    def __init__(self, data):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._data


class FakeSession:
    """aiohttp session stand-in: the OpenAI judge (runner) and the Ollama endpoints (call_center)."""

    def post(self, url, json=None, headers=None, timeout=None):
        if url.endswith("/api/embeddings"):
            return _Resp({"embedding": fake_embedding(json["prompt"], DIM)})
        if url.endswith("/api/generate"):
            PROMPTS.append(json["prompt"])
            return _Resp({"response": "  " + scripted_reply(json["prompt"]) + "\n"})
        if "openai.com" in url:
            prompt = json["messages"][0]["content"]
            PROMPTS.append(prompt)
            return _Resp({"choices": [{"message": {"content": " " + scripted_reply(prompt) + " "}}]})
        raise RuntimeError(url)


# ============================================================================== helpers
def message_index(conv):
    out = {}
    i = 0
    c = conv["conversation"]
    s = 1
    while f"session_{s}" in c:
        for m in c[f"session_{s}"]:
            out[m.get("dia_id")] = i
            i += 1
        s += 1
    return out


def memory_indices(conv, qa, qi, n):
    idx = message_index(conv)
    ev = qa.get("evidence", [])
    if isinstance(ev, str):
        ev = [ev]
    first = [idx[d] for d in re.findall(r"D\d+:\d+", " ".join(map(str, ev))) if d in idx]
    out = list(dict.fromkeys(first + [(qi * 7 + j * 13) % n for j in range(15)]))
    return out[:15]


def gold_cases():
    cases = []
    for ci, conv in enumerate(LOCOMO):
        msgs = flatten(conv)
        for qi, qa in enumerate(conv["qa"]):
            idxs = memory_indices(conv, qa, qi, len(msgs))
            texts = [msgs[i]["text"] for i in idxs]
            gold = qa.get("answer", qa.get("adversarial_answer", ""))
            cases.append({"conv": ci, "idx": idxs, "gold": gold,
                          "out": [list(runner.check_gold_in_memories_substring(gold, [{"text": t} for t in texts], k))
                                  for k in (1, 5, 10, 50)]})
    edge_texts = ["I adopted a dog named Max and a cat", "We went to Paris, France in 2022", "", "THE END",
                  "and so on", "she is 32 years old"]
    edges = []
    for gold in ["", 0, 2022, 32, "Max and a cat", "paris, france", "a and b", "  the end  ", "ab", "x, yz, Paris",
                 "Dog", 1.5, True, "and"]:
        edges.append({"gold": gold, "out": [list(runner.check_gold_in_memories_substring(
            gold, [{"text": t} for t in edge_texts], k)) for k in (1, 3, 6, 10)]})
    return {"locomo": cases, "edges": edges, "edge_texts": edge_texts}


def summary_cases():
    out = []
    inputs = [
        {},
        {"single_hop": {"recall_at_10": [], "recall_at_50": [], "oracle_rank": [], "filter_drop_count": [],
                        "total_candidates": []}},
        {"single_hop": {"recall_at_10": [1, 0, 0], "recall_at_50": [1, 1, 0], "oracle_rank": [3, 27, 0],
                        "filter_drop_count": [], "total_candidates": [50, 80, 50]},
         "temporal": {"recall_at_10": [0, 0], "recall_at_50": [0, 0], "oracle_rank": [0, 0],
                      "filter_drop_count": [], "total_candidates": [50, 50]}},
        {"multi_hop": {"recall_at_10": [1] * 49 + [0] * 51, "recall_at_50": [1] * 52 + [0] * 48,
                       "oracle_rank": list(range(1, 53)) + [0] * 48, "filter_drop_count": [],
                       "total_candidates": [80] * 100}},
        {"open_domain": {"recall_at_10": [0, 1, 0, 0, 0, 0, 1], "recall_at_50": [1, 1, 0, 1, 0, 0, 1],
                         "oracle_rank": [12, 2, 0, 45, 0, 0, 7], "filter_drop_count": [], "total_candidates": [1] * 7}},
    ]
    for m in inputs:
        out.append({"metrics": m, "summary": runner.compute_retrieval_summary(m)})
    return out


def save_results_cases(tmp):
    out = []
    rows = [
        [],
        [{"retrieval_latency_ms": 12.3, "rerank_latency_ms": 0.0, "answer_latency_ms": 0, "e2e_latency_ms": 99.95}],
        [{"retrieval_latency_ms": 10.0 + i * 1.37, "rerank_latency_ms": 200.0 + (i * 37 % 11), "answer_latency_ms":
          (0 if i % 3 == 0 else 450.25 + i), "e2e_latency_ms": 700.05 + i * 2.5, "id": i} for i in range(23)],
    ]
    stats_list = [
        {c: {"correct": 0, "total": 0} for c in CATEGORIES.values()},
        {"single_hop": {"correct": 1, "total": 1}, "temporal": {"correct": 0, "total": 0},
         "open_domain": {"correct": 0, "total": 0}, "multi_hop": {"correct": 0, "total": 0}},
        {"single_hop": {"correct": 7, "total": 9}, "temporal": {"correct": 2, "total": 7},
         "open_domain": {"correct": 0, "total": 3}, "multi_hop": {"correct": 3, "total": 4}},
    ]
    metrics = summary_cases()[2]["metrics"]
    for rs, st in zip(rows, stats_list):
        runner.RETRIEVAL_METRICS = {c: {"recall_at_10": [], "recall_at_50": [], "oracle_rank": [],
                                        "filter_drop_count": [], "total_candidates": []} for c in CATEGORIES.values()}
        runner.RETRIEVAL_METRICS.update(metrics)
        runner.OUTPUT_PATH = Path(tmp) / "save.json"
        runner.save_results(rs, st)
        out.append({"results": rs, "stats": st, "metrics": runner.RETRIEVAL_METRICS,
                    "file": runner.OUTPUT_PATH.read_text()})
    return out


JUDGE_REPLIES = [
    '{"label": "CORRECT"}', '{"label": "WRONG"}', ' {"label": " correct "} ', '{"label": "Correct!"}',
    'The answer matches. {"label": "CORRECT"}', 'Explanation {"label": "WRONG", "x": 1} trailing',
    '"label": "correct"', '"label" :  "WRONG"', "CORRECT", "WRONG", "", "[1, 2]", '{"label": null}',
    '{"label": true}', '{"label": ["CORRECT"]}', '{"answer": "CORRECT"}', '{"label": "CORRECT"', 'x {not json} y',
    '{"label": "WRONG"} {"label": "CORRECT"}', '"LABEL": "Correct"', '{"label":"CORRECT"}\n', "123",
    '{"label": 1}', '{"a": {"label": "CORRECT"}}', '"label":"CORRECT" and "label":"WRONG"',
]


def judge_cases():
    runner.USE_OPENAI_JUDGE = False
    out = {"labels": [[r, runner.parse_judge_label(r)] for r in JUDGE_REPLIES], "judge": []}
    cases = [("When?", "May 7, 2023", "7 May 2023"), ("Who?", "caroline", "Caroline"), ("Q", "Paris", "  paris "),
             ("Q", "She lives in Paris now", "paris"), ("Q", "", "x"), ("Q", "anything", ""), ("Q", "2022", 2022),
             ("Q", "In 2022.", 2022), ("Q", "camping", "Camping, hiking"), ("What did X do?", "pottery", "painting"),
             ("Q", "nope", 0), ("Q", "0", 0), ("Q", "True", True)]
    for q, gen, gold in cases:
        PROMPTS.clear()
        res = asyncio.run(runner.judge_answer(None, q, gen, gold))
        out["judge"].append({"q": q, "generated": gen, "gold": gold, "correct": res, "prompts": list(PROMPTS)})
    return out


def build_runner_nodes(conv_idx, conv):
    """The node metadata of runner.run_benchmark's ingestion loop (entities in first-occurrence order)."""
    nodes = []
    for msg_idx, msg in enumerate(flatten(conv)):
        keywords = service.extract_keywords(msg["text"])
        ents = [e for e in dict.fromkeys(re.findall(r'\b[A-Z][a-z]+\b', msg["text"])) if e != "I"]
        message_date = TU.parse_datetime_flexible(msg["datetime"])
        temporal_data = TU.generate_temporal_tokens(message_date, msg["text"])
        _, resolved_dates = TU.resolve_relative_dates(msg["text"], message_date)
        resolved_date = None
        resolved_date_source = None
        explicit_date = TU.extract_explicit_date(msg["text"], message_date)
        duration_meta = TU.extract_duration_metadata(msg["text"], message_date)
        if explicit_date:
            resolved_date = explicit_date
            resolved_date_source = "explicit"
        elif resolved_dates:
            date_values = [v for v in resolved_dates.values() if isinstance(v, str) and re.search(r'\d', v)]
            unique_values = list(dict.fromkeys(date_values))
            if len(unique_values) == 1:
                resolved_date = unique_values[0]
                resolved_date_source = "relative"
        if not resolved_date and message_date:
            resolved_date = message_date.strftime("%Y-%m-%d")
            resolved_date_source = "message"
        nodes.append(dt_mod.NeuralNode(
            node_id=f"msg_{conv_idx}_{msg_idx}", session_key=f"conv_{conv_idx}", content=msg["text"],
            layer=dt_mod.NodeLayer.MESSAGE, embedding=fake_embedding(msg["text"], DIM),
            created_at=runner.datetime.now(),
            metadata={
                "speaker": msg["speaker"], "datetime": msg["datetime"], "keywords": list(keywords),
                "entities": ents, "temporal_tokens": temporal_data["date_tokens"],
                "temporal_metadata": temporal_data["temporal_metadata"], "resolved_date": resolved_date,
                "resolved_date_source": resolved_date_source, "explicit_date": explicit_date,
                "duration_years": duration_meta.get("duration_years"),
                "duration_months": duration_meta.get("duration_months"),
                "since_year": duration_meta.get("since_year"), "since_date": duration_meta.get("since_date"),
                "date_tokens": temporal_data["date_tokens"], "temporal": temporal_data["temporal_metadata"],
                "resolved_dates": resolved_dates,
            }))
    return nodes


def build_context(reranked, num_memories_needed, query_mode):
    """The context loop of run_benchmark, verbatim."""
    context_parts = []
    retrieved_memories = []
    for node, charge in reranked[:num_memories_needed]:
        speaker = node.metadata.get("speaker", "Unknown")
        dt = node.metadata.get("datetime", "")
        original_content = node.content
        resolved_date = node.metadata.get("resolved_date", "")
        resolved_dates = node.metadata.get("resolved_dates", {}) or {}
        resolved_pairs = []
        if isinstance(resolved_dates, dict):
            for key, value in resolved_dates.items():
                resolved_pairs.append(f"{key}->{value}")
        resolved_relative = ", ".join(resolved_pairs)
        if query_mode == "TEMPORAL":
            context_parts.append(
                f"[MESSAGE_DATETIME={dt}] [RESOLVED_DATE={resolved_date}] "
                f"[RESOLVED_RELATIVE={resolved_relative}] [SPEAKER={speaker}] {original_content}")
        else:
            context_parts.append(f"[{speaker}] {original_content}\n  (sent: {dt})")
        retrieved_memories.append({"speaker": speaker, "text": original_content[:300], "datetime": dt,
                                   "charge": round(charge, 3)})
    candidate_memories = [{"text": node.content, "metadata": node.metadata}
                          for node, _charge in reranked[:num_memories_needed]]
    return context_parts, retrieved_memories, candidate_memories


def direct_answer(question, query_mode, candidate_memories):
    """The extraction block of run_benchmark, verbatim."""
    extracted_answer = None
    if query_mode == "TEMPORAL":
        duration_direct = service.extract_duration_answer(question, candidate_memories)
        if duration_direct:
            extracted_answer = duration_direct
        else:
            topic_keywords = service.extract_topic_keywords_for_temporal(question)
            query_month, query_year = service.extract_month_year_from_text(question)
            for mem in candidate_memories:
                meta = mem.get("metadata", {})
                resolved_date = meta.get("resolved_date")
                source = meta.get("resolved_date_source")
                if source in ("explicit", "relative") and resolved_date:
                    text_lower = mem.get("text", "").lower()
                    if topic_keywords and not any(k in text_lower for k in topic_keywords):
                        continue
                    if query_month or query_year:
                        mem_month, mem_year = service.extract_month_year_from_text(resolved_date)
                        if query_month and mem_month != query_month:
                            continue
                        if query_year and mem_year != query_year:
                            continue
                    extracted_answer = resolved_date
                    break
    elif query_mode != "OPEN_DOMAIN_WORLD":
        count_direct = service.extract_count_answer(question, candidate_memories)
        if count_direct:
            extracted_answer = count_direct
        else:
            relation_direct = service.extract_relationship_status(question, candidate_memories)
            if relation_direct:
                extracted_answer = relation_direct
    return extracted_answer


def jsha(obj) -> str:
    return sha1(json.dumps(obj))


def context_cases():
    """Node metadata (sha1 of ``json.dumps`` per node; in full for the first 20 nodes and every node whose date was
    not the message date) and, per question, the context / memories (sha1; in full for every 20th question) and
    the direct answers."""
    out = []
    for ci in (2, 5):
        conv = LOCOMO[ci]
        nodes = build_runner_nodes(ci, conv)
        n = len(nodes)
        entry = {"conv": ci, "metadata_sha": [jsha(nd.metadata) for nd in nodes],
                 "metadata_full": {str(i): nd.metadata for i, nd in enumerate(nodes)
                                   if i < 20 or nd.metadata["resolved_date_source"] != "message"},
                 "questions": []}
        for qi, qa in enumerate(conv["qa"]):
            idxs = memory_indices(conv, qa, qi, n) + [(qi * 11 + j * 17) % n for j in range(15)]
            idxs = list(dict.fromkeys(idxs))[:30]
            charges = [round(1.0 / (1 + j) + qi * 1e-4, 6) + 0.0004999 for j in range(len(idxs))]
            reranked = [(nodes[i], c) for i, c in zip(idxs, charges)]
            q = qa["question"]
            row = {"idx": idxs, "charges": charges, "q": q}
            for mode, k in (("TEMPORAL", 15), ("STRICT", 30)):
                parts, mems, cands = build_context(reranked, k, mode)
                ctx = "\n".join(parts)
                row[mode] = {"context_sha": sha1(ctx), "memories_sha": jsha(mems),
                             "direct": direct_answer(q, mode, cands)}
                if qi % 20 == 0:
                    row[mode]["context"] = ctx
                    row[mode]["memories"] = mems
            row["OPEN_DOMAIN_WORLD"] = direct_answer(q, "OPEN_DOMAIN_WORLD", build_context(reranked, 15, "X")[2])
            entry["questions"].append(row)
        out.append(entry)
    return out


def route(question, speakers, force):
    """The routing block of run_benchmark, verbatim (plus the post-rerank upgrade and the answer modes)."""
    list_question, list_type = detect_list_question_universal(question)
    query_mode = TU.infer_query_mode(question)
    if list_question:
        query_mode = "AGGREGATION" if list_type == "aggregation" else "LIST"
    if service.is_temporal_question(question):
        query_mode = "TEMPORAL"
    elif query_mode == "TEMPORAL":
        query_mode = "INFERENTIAL"
    names_in_q = any(sp in question.lower() for sp in speakers)
    if is_open_domain_world_query(question) and not names_in_q:
        query_mode = "OPEN_DOMAIN_WORLD"
    if force and looks_open_domain_question(question):
        if force >= 2 or query_mode in ("STRICT", "INFERENTIAL", "OPEN_DOMAIN_WORLD"):
            query_mode = "OPEN_DOMAIN_INFER"
    before = query_mode
    needed = 30 if query_mode in {"AGGREGATION", "LIST"} else 15
    if query_mode == "INFERENTIAL" and should_use_open_domain_infer(question):
        query_mode = "OPEN_DOMAIN_INFER"
    modes = []
    for keep in (False, True):
        for world in (False, True):
            answer_mode = query_mode
            if query_mode == "OPEN_DOMAIN_WORLD" and keep:
                answer_mode = "OPEN_DOMAIN_WORLD_MEM"
            elif query_mode == "OPEN_DOMAIN_INFER" and world:
                answer_mode = "OPEN_DOMAIN_INFER_WORLD"
            retry_mode = "OPEN_DOMAIN_INFER_WORLD" if world else "OPEN_DOMAIN_INFER"
            modes.append([answer_mode, retry_mode])
    return [before, list_question, needed, query_mode, modes]


def routing_cases():
    out = []
    for ci, conv in enumerate(LOCOMO):
        speakers = list(dict.fromkeys(m["speaker"].lower() for m in flatten(conv)))
        out.append({"speakers": speakers, "questions": [
            [route(qa["question"], speakers, 0)] + [route(qa["question"], speakers, f)[:4] for f in (1, 2)]
            for qa in conv["qa"]]})
    return out


def ranked(res):
    return [[n.node_id, c] for n, c in res]


async def graph_cases():
    conv_idx = 1
    conv = LOCOMO[conv_idx]
    messages = flatten(conv)
    storage = OrderedStorage()
    nodes = build_runner_nodes(conv_idx, conv)
    speaker_nodes = {}
    for nd in nodes:
        await storage.save_node(nd)
        speaker_nodes.setdefault(nd.metadata["speaker"].lower(), []).append(nd.node_id)
    for speaker, node_ids in speaker_nodes.items():
        for i, src_id in enumerate(node_ids):
            for j in range(i + 1, min(i + 4, len(node_ids))):
                await storage.save_edge(dt_mod.NeuralEdge(edge_id=dt_mod.generate_edge_id(), source_id=src_id,
                                                          target_id=node_ids[j], edge_type=dt_mod.EdgeType.ENTITY,
                                                          base_weight=0.8, metadata={"speaker": speaker}))
    ids = [n.node_id for n in nodes]
    for i in range(len(ids) - 1):
        await storage.save_edge(dt_mod.NeuralEdge(edge_id=dt_mod.generate_edge_id(), source_id=ids[i],
                                                  target_id=ids[i + 1], edge_type=dt_mod.EdgeType.TEMPORAL,
                                                  base_weight=0.85, metadata={"direction": "before"}))
    linker = DL.DialogueLinker(storage)
    await linker.process_dialogue_sequence(nodes, f"conv_{conv_idx}")
    tess = TS.Tesseract(storage)
    pairs = await runner.build_pair_nodes(None, conv_idx, messages, nodes)
    node_by_id = {n.node_id: n for n in nodes}
    out = {"conv": conv_idx, "pairs": [[p.node_id, p.content, p.metadata] for p in pairs[:40]], "questions": []}
    for qa in conv["qa"][:30]:
        q = qa["question"]
        qvec = fake_embedding(q, DIM)
        res = await tess.retrieve(query_text=q, query_embedding=qvec, session_key=f"conv_{conv_idx}", limit=50,
                                  auto_expand_temporal=True)
        row = {"q": q}
        for seed_k, decay in ((10, 0.8), (3, 0.5), (0, 0.8)):
            runner.GRAPH_SEED_K, runner.GRAPH_EDGE_DECAY = seed_k, decay
            row[f"graph_{seed_k}_{decay}"] = ranked(await runner.expand_via_graph(storage, linker, res))
        cos = runner.cosine_topk(nodes, qvec, 20)
        row["cosine"] = [[n.node_id, R.f32_bits([c])[0]] for n, c in cos]
        pool = [p for p in pairs if p.speaker_id.lower() == messages[0]["speaker"].lower()]
        seen = {n.node_id for n, _ in res[:10]}
        row["unpair"] = ranked(runner.unpair(runner.cosine_topk(pool, qvec, 30), node_by_id, seen))
        row["unpair_all"] = ranked(runner.unpair(runner.cosine_topk(pairs + nodes, qvec, 40), node_by_id, set()))
        out["questions"].append(row)
    runner.GRAPH_SEED_K, runner.GRAPH_EDGE_DECAY = 10, 0.8
    return out


def two_agent_cases():
    out = {"named": [], "swap": [], "gold_parts": [], "evidence": [], "substring": []}
    for ci, conv in enumerate(LOCOMO):
        c = conv["conversation"]
        speakers = list(dict.fromkeys(m["speaker"].lower() for m in flatten(conv)))
        dia = {m["dia_id"]: m["speaker"] for k, v in c.items()
               if k.startswith("session_") and isinstance(v, list) for m in v if "dia_id" in m}
        sp_a, sp_b = c["speaker_a"], c["speaker_b"]
        for qa in conv["qa"]:
            q = qa["question"]
            named = TA.named_speakers(q, speakers)
            out["named"].append(named)
            if len(named) == 1:
                src = {sp_a.lower(): sp_a, sp_b.lower(): sp_b}[named[0]]
                other = sp_b if named[0] == sp_a.lower() else sp_a
                out["swap"].append([q, src, other, TA.swap_name(q, src, other)])
            out["evidence"].append(sorted(TA.evidence_speakers(qa, dia)))
            if ci < 2:
                gold = qa.get("answer", "")
                out["gold_parts"].append([gold, TA.gold_parts(gold)])
    for q, src, dst in [("What did CAROLINE say to caroline and Caroline's mom?", "Caroline", "Melanie"),
                        ("Is jon Jon's friend? JON!", "Jon", "Gina"), ("Carolines are many", "Caroline", "Mel"),
                        ("caroline.caroline", "Caroline", "mELANIE"), ("Ça va Caroline", "Caroline", "Émile")]:
        out["swap"].append([q, src, dst, TA.swap_name(q, src, dst)])
    for gold in ["", 0, 2022, "a and b", "x, y, zz", "ab", "Paris, France and Rome"]:
        out["gold_parts"].append([gold, TA.gold_parts(gold)])
    conv = LOCOMO[0]
    nodes = build_runner_nodes(0, conv)
    for qa in conv["qa"][:40]:
        gold = qa.get("answer", "")
        out["substring"].append([gold, sorted(TA.substring_gold_speakers(gold, nodes))])
    # merges on crafted charge lists (ties included)
    mk = lambda pairs: [(nodes[i], c) for i, c in pairs]  # noqa: E731
    a = mk([(0, 0.9), (1, 0.8), (2, 0.8), (3, 0.5), (4, 0.4)])
    b = mk([(5, 0.95), (1, 0.85), (6, 0.8), (7, 0.1)])
    out["merge"] = {
        "a": [[i, c] for i, c in [(0, 0.9), (1, 0.8), (2, 0.8), (3, 0.5), (4, 0.4)]],
        "b": [[i, c] for i, c in [(5, 0.95), (1, 0.85), (6, 0.8), (7, 0.1)]],
        "by_charge": ranked(TA.merge_by_charge(a, b)), "by_charge_3": ranked(TA.merge_by_charge(a, b, k=3)),
        "round_robin": ranked(TA.merge_round_robin(a, b)), "round_robin_4": ranked(TA.merge_round_robin(a, b, k=4)),
        "boost": ranked(TA.speaker_boost(a + b, nodes[0].speaker_id.lower())),
        "append": ranked(TA.append_extra(a, b)), "append_1": ranked(TA.append_extra(a, b, k=1)),
        "hits3": [TA.hits3(a + b, qa.get("answer", "")) for qa in conv["qa"][:60]],
    }
    # two_agent_eval.build_pair_nodes with cached_embeddings patched to fake embeddings
    async def fake_cached(http, key, texts):
        return [fake_embedding(t, DIM) for t in texts]
    TA.cached_embeddings = fake_cached
    pairs = asyncio.run(TA.build_pair_nodes(None, 0, flatten(conv), nodes, 2))
    out["pairs2"] = [[p.node_id, p.content, p.metadata] for p in pairs[:30]]
    # the bytes cached_embeddings writes
    with tempfile.TemporaryDirectory() as d:
        retrieval_eval.CACHE_DIR = Path(d)

        async def eb(http, texts):
            return [[0.1, -2.5e-07, 3.0, 1e16, 0.30000000000000004][: 1 + i % 5] for i, _ in enumerate(texts)]
        retrieval_eval.embed_batch = eb
        asyncio.run(retrieval_eval.cached_embeddings(None, "k", ["a", "b", "c"]))
        out["cache_file"] = (Path(d) / "k.json").read_text()
    return out


# ============================================================================== end to end
RUNS = [
    {"name": "flat", "mode": "flat"},
    {"name": "graph", "mode": "graph"},
    {"name": "hybrid", "mode": "hybrid", "keep": True},
    {"name": "local_pairs", "mode": "local_pairs", "force": 1, "world": True},
    {"name": "mega", "mode": "mega", "force": 2, "openai": True},
    {"name": "open_domain", "mode": "flat", "cats": ["open_domain", "multi_hop"]},
    {"name": "open_domain_keep", "mode": "graph", "cats": ["open_domain", "single_hop"], "keep": True, "world": True},
]
E2E_CONV, E2E_MAX = 1, 16


def reset_runner(run, tmp):
    runner.RETRIEVAL_MODE = run["mode"]
    runner.RERANK_WINDOW = runner.TOP_K + (runner.EXTRA_CANDIDATES if run["mode"] in (
        "graph", "hybrid", "local_pairs", "mega") else 0)
    runner.ONLY_CONV = {E2E_CONV}
    runner.ONLY_CAT = set(run.get("cats", []))
    runner.MAX_QUESTIONS = E2E_MAX
    runner.OPEN_DOMAIN_KEEP_CONTEXT = run.get("keep", False)
    runner.OPEN_DOMAIN_FORCE_INFER = run.get("force", 0)
    runner.OPEN_DOMAIN_INFER_WORLD = run.get("world", False)
    runner.OPENAI_API_KEY = "sk-test" if run.get("openai") else ""
    runner.USE_OPENAI_JUDGE = bool(run.get("openai"))
    runner.OUTPUT_PATH = Path(tmp) / f"{run['name']}.json"
    runner.RETRIEVAL_METRICS = {c: {"recall_at_10": [], "recall_at_50": [], "oracle_rank": [],
                                    "filter_drop_count": [], "total_candidates": []} for c in CATEGORIES.values()}
    for k in runner.ROUTING_STATS:
        runner.ROUTING_STATS[k] = 0
    runner.ROUTING_SAMPLES.clear()
    runner.INFERENTIAL_SAMPLES.clear()
    runner.FINAL_MODE_STATS.clear()
    runner.TIMING_DATA.clear()


class _Http:
    async def __aenter__(self):
        return FakeSession()

    async def __aexit__(self, *a):
        return False


def e2e_runner(tmp):
    import types, aiohttp
    runner.aiohttp = types.SimpleNamespace(ClientSession=lambda *a, **k: _Http(), ClientTimeout=aiohttp.ClientTimeout)
    out = []
    for run in RUNS:
        reset_runner(run, tmp)
        PROMPTS.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            asyncio.run(runner.run_benchmark())
        text = buf.getvalue().replace(str(runner.OUTPUT_PATH), "<OUT>")
        out.append({**run, "stdout": text, "report": json.loads(runner.OUTPUT_PATH.read_text()),
                    "prompts": [sha1(p) for p in PROMPTS], "prompt_samples": PROMPTS[:3] + PROMPTS[-2:]})
    return out


def e2e_call_center(tmp):
    import types, aiohttp
    CC.aiohttp = types.SimpleNamespace(ClientSession=lambda *a, **k: _Http(), ClientTimeout=aiohttp.ClientTimeout)
    CC.OUTPUT_PATH = Path(tmp) / "cc.json"
    CC.BENCHMARK_PATH = R.DATASETS / "call_center_benchmark.json"
    PROMPTS.clear()
    buf = io.StringIO()
    with redirect_stdout(buf):
        asyncio.run(CC.run_benchmark())
    return {"stdout": buf.getvalue().replace(str(CC.OUTPUT_PATH), "<OUT>"),
            "report": json.loads(CC.OUTPUT_PATH.read_text()), "prompts": [sha1(p) for p in PROMPTS],
            "prompt_samples": PROMPTS[:2] + PROMPTS[-2:]}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = {
            "dim": DIM, "replies": REPLIES, "answer_model": runner.OLLAMA_MODEL, "base_url": runner.OLLAMA_BASE_URL,
            "e2e_conv": E2E_CONV, "e2e_max": E2E_MAX,
            "gold": gold_cases(),
            "summary": summary_cases(),
            "save_results": save_results_cases(tmp),
            "judge": judge_cases(),
            "context": context_cases(),
            "routing": routing_cases(),
            "graph": asyncio.run(graph_cases()),
            "two_agent": two_agent_cases(),
            "e2e": e2e_runner(tmp),
            "call_center": e2e_call_center(tmp),
        }
    split = {"context": fixture.pop("context"), "routing": fixture.pop("routing")}
    dump_compact("runner.json", fixture)
    dump_compact("runner_routing.json", split)


def dump_compact(name, obj):
    """``R.dump`` without the indentation (these fixtures are large and machine-compared only)."""
    path = R.HERE / name
    path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(R.ROOT)} ({path.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
