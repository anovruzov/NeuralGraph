"""Retrieval-only evaluator: recall@10 / recall@50 per retrieval variant, no LLM calls.

Runs in about a minute (embeddings are cached), so retrieval changes can be iterated fast
before paying for a full end-to-end run with reranking + answering + judging.

Usage:
    ONLY_CAT=single_hop ONLY_CONV=0,1 .venv/bin/python demo/retrieval_eval.py
    VARIANTS=embed,tesseract,tesseract+graph .venv/bin/python demo/retrieval_eval.py
    EMBEDDER=hash VARIANTS=embed,tesseract python research/benchmarks/retrieval_eval.py

Two scores per question:
- recall@k: the same substring check as the benchmark (gold answer split on "," / " and ").
- ev@k (evidence recall): the share of the question's gold evidence dia_ids (qa["evidence"])
  found among the dia_ids of the first k retrieved messages, averaged over the questions
  that cite evidence (n_ev).

EMBEDDER=hash uses the deterministic hash embedder (NeuralGraph.chat_memory.llm.fake_embedding)
instead of the embedding server. Its numbers are structural checks, not retrieval quality.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import aiohttp
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling harness modules


from NeuralGraph.research.retrieval import NeuralEdge, EdgeType, generate_edge_id
from NeuralGraph import llm_backend
from NeuralGraph.chat_memory.llm import fake_embedding
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage
from NeuralGraph.research.retrieval.tesseract import Tesseract
from NeuralGraph.research.retrieval.dialogue_linker import DialogueLinker
from NeuralGraph.research.retrieval.benchmark_ingest import (
    allow_missing_from_env, build_message_node, check_embeddings, flatten_locomo,
)
import runner  # reuse benchmark helpers so both evaluators agree
from runner import check_gold_in_memories_substring, expand_via_graph, CATEGORIES

CACHE_DIR = Path(os.environ.get("EMB_CACHE_DIR", Path(__file__).resolve().parents[2] / "research" / "results" / "emb_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
ONLY_CAT = {c.strip() for c in os.environ.get("ONLY_CAT", "").split(",") if c.strip()}
ONLY_CONV = {int(c) for c in os.environ.get("ONLY_CONV", "").split(",") if c.strip()}
VARIANTS = [v.strip() for v in os.environ.get("VARIANTS", "embed,tesseract,tesseract+graph").split(",")]
EMBEDDER = os.environ.get("EMBEDDER", "server")  # "hash" = offline hash embedder, structural numbers only
TOP_K = 50

_DIA_ID = re.compile(r"D\d+:\d+")


def evidence_ids(qa: dict) -> list[str]:
    """Gold evidence dia_ids of a question, in order, each once.

    An entry can hold several ids ("D8:6; D9:17", "D9:1 D4:4 D4:6"), so entries are split on
    ";", "," and whitespace. Tokens that are not dia_ids are dropped (the dataset has "D" and
    "D:11:26").
    """
    evidence = qa.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    out: list[str] = []
    for entry in evidence:
        for token in re.split(r"[;,\s]+", str(entry)):
            if _DIA_ID.fullmatch(token) and token not in out:
                out.append(token)
    return out


def evidence_recall(gold: list[str], retrieved: list[str | None], k: int) -> float:
    """Share of the gold dia_ids found among the first k retrieved dia_ids."""
    if not gold:
        raise ValueError("evidence recall needs at least one gold dia_id")
    found = set(retrieved[:k])
    return sum(1 for g in gold if g in found) / len(gold)


async def embed_batch(http, texts: list[str]) -> list[list[float]]:
    """Batch embeddings (OpenAI-style endpoint accepts a list); falls back to one-by-one."""
    out: list[list[float]] = []
    if llm_backend.backend_for(llm_backend.LLM_BASE_URL) == "openai":
        for i in range(0, len(texts), 64):
            chunk = texts[i:i + 64]
            async with http.post(
                f"{llm_backend.LLM_BASE_URL}/v1/embeddings",
                json={"model": llm_backend.EMBED_MODEL, "input": chunk},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                out.extend([d["embedding"] for d in data["data"]])
        return out
    for t in texts:
        out.append(await llm_backend.llm_embed(http, t))
    return out


async def cached_embeddings(http, key: str, texts: list[str]) -> list[list[float]]:
    if EMBEDDER == "hash":  # offline: no server, no cache file
        return [fake_embedding(t) for t in texts]
    # The file name carries a digest of the texts, so a cache built from other texts (e.g. before
    # image captions were added to message text) is never reused just because the count matches.
    digest = hashlib.sha256("\x00".join(texts).encode("utf-8")).hexdigest()[:12]
    path = CACHE_DIR / f"{key}-{digest}.json"
    if path.exists():
        cached = json.load(open(path))
        if len(cached) == len(texts):
            return cached
    vecs = await embed_batch(http, texts)
    if all(vecs):  # never cache a failed (empty) embedding
        json.dump(vecs, open(path, "w"))
    return vecs


def flatten_messages(conv: dict) -> list[dict]:
    """LoCoMo turns in order (shared helper: dia_id, session index and image caption kept)."""
    return flatten_locomo(conv)


async def ingest(http, conv_idx: int, conv: dict, allow_missing: bool | None = None):
    """Same ingestion as research/benchmarks/runner.py (shared node builder, same edges, same dialogue links).

    Raises EmbeddingFailureError naming the count if any message has no embedding, unless
    allow_missing (default: ALLOW_MISSING_EMBEDDINGS=1) is set; then those messages are skipped.
    """
    messages = flatten_messages(conv)
    embeddings = await cached_embeddings(http, f"conv{conv_idx}_msgs", [m["text"] for m in messages])
    if allow_missing is None:
        allow_missing = allow_missing_from_env()
    check_embeddings(embeddings, where=f"conv_{conv_idx}", allow_missing=allow_missing)
    storage = InMemoryNeuralGraphStorage()
    speaker_nodes: dict[str, list[str]] = defaultdict(list)
    all_ids: list[str] = []
    for idx, (msg, emb) in enumerate(zip(messages, embeddings)):
        if not emb:
            continue
        node_id = f"msg_{conv_idx}_{idx}"
        node = build_message_node(node_id, f"conv_{conv_idx}", msg, emb)
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
    linker = DialogueLinker(storage)
    nodes = [await storage.get_node(n) for n in all_ids]
    await linker.process_dialogue_sequence(nodes, f"conv_{conv_idx}")
    return storage, linker, nodes, list(speaker_nodes.keys())


def cosine_topk(nodes, query_vec, k):
    M = np.array([n.embedding for n in nodes], dtype=np.float32)
    M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
    q = np.array(query_vec, dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9
    sims = M @ q
    order = np.argsort(-sims)[:k]
    return [(nodes[i], float(sims[i])) for i in order]


async def retrieve(variant, question, qvec, storage, tesseract, linker, nodes, speakers, conv_idx, _cache={}):
    if variant == "embed":
        return cosine_topk(nodes, qvec, TOP_K)
    if variant.startswith("tesseract"):
        key = (tesseract, conv_idx, question)  # the instance too: another store may reuse conv_idx
        if key not in _cache:  # Tesseract retrieval is the slow part; share it across variants
            _cache.clear()
            _cache[key] = await tesseract.retrieve(query_text=question, query_embedding=qvec,
                                                   session_key=f"conv_{conv_idx}", limit=TOP_K, auto_expand_temporal=True)
        res = list(_cache[key])
        if variant == "tesseract+graph":
            res = await expand_via_graph(storage, linker, res)
            return res[:TOP_K]
        if variant == "tesseract+graph_append":
            # Flat top-K stays intact; graph neighbours are appended after it (rerank window grows to 80).
            flat_ids = {n.node_id for n, _ in res}
            expanded = await expand_via_graph(storage, linker, res)
            extra = [(n, c) for n, c in expanded if n.node_id not in flat_ids]
            return res[:TOP_K] + extra[:30]
        if variant in ("tesseract+embed_union", "tesseract+speaker+embed_union"):
            # Tesseract's min_charge filters drop some gold messages outright; back-fill with
            # pure-embedding hits it never saw (extra candidates go after the flat top-K).
            if "speaker" in variant:
                named = [sp for sp in speakers if sp in question.lower()]
                if len(named) == 1:
                    res = [(n, c * (1.5 if n.speaker_id.lower() == named[0] else 1.0)) for n, c in res]
                    res.sort(key=lambda x: x[1], reverse=True)
            res = res[:TOP_K]
            have = {n.node_id for n, _ in res}
            extra = [(n, c) for n, c in cosine_topk(nodes, qvec, TOP_K) if n.node_id not in have]
            return res + extra[:30]
        if variant == "tesseract+speaker":
            # If the question names exactly one participant, favour that participant's own messages.
            named = [sp for sp in speakers if sp in question.lower()]
            if len(named) == 1:
                res = [(n, c * (1.5 if n.speaker_id.lower() == named[0] else 1.0)) for n, c in res]
                res.sort(key=lambda x: x[1], reverse=True)
            return res[:TOP_K]
        return res[:TOP_K]
    raise ValueError(variant)


def new_counts() -> dict:
    return {"r10": 0, "r50": 0, "rall": 0, "n": 0, "e10": 0.0, "e50": 0.0, "n_ev": 0}


def score_question(h: dict, qa: dict, res) -> None:
    """Add one question's retrieved list to its category's counts (substring and evidence recall)."""
    gold = qa.get("answer", "")
    mems = [{"text": n.content, "speaker": n.speaker_id} for n, _ in res]
    r10, _ = check_gold_in_memories_substring(gold, mems, top_k=10)
    r50, _ = check_gold_in_memories_substring(gold, mems, top_k=50)
    rall, _ = check_gold_in_memories_substring(gold, mems, top_k=len(mems))
    h["n"] += 1
    h["r10"] += int(r10)
    h["r50"] += int(r50)
    h["rall"] += int(rall)
    evidence = evidence_ids(qa)
    if evidence:
        got = [(n.metadata or {}).get("dia_id") for n, _ in res]
        h["n_ev"] += 1
        h["e10"] += evidence_recall(evidence, got, 10)
        h["e50"] += evidence_recall(evidence, got, 50)


async def evaluate(data: list[dict], variants=None, only_conv=None, only_cat=None, http=None, log=print) -> dict:
    """Score each variant on the selected conversations: {variant: {category: counts}}."""
    variants = VARIANTS if variants is None else variants
    only_conv = ONLY_CONV if only_conv is None else only_conv
    only_cat = ONLY_CAT if only_cat is None else only_cat
    hits = {v: defaultdict(new_counts) for v in variants}
    t0 = time.time()
    for conv_idx, conv in enumerate(data):
        if only_conv and conv_idx not in only_conv:
            continue
        storage, linker, nodes, speakers = await ingest(http, conv_idx, conv)
        tesseract = Tesseract(storage)
        qas = [qa for qa in conv.get("qa", []) if qa.get("category") in CATEGORIES
               and (not only_cat or CATEGORIES[qa["category"]] in only_cat)]
        qvecs = await cached_embeddings(http, f"conv{conv_idx}_questions_all",
                                        [qa["question"] for qa in conv.get("qa", [])])
        qvec_by_text = {qa["question"]: v for qa, v in zip(conv.get("qa", []), qvecs)}
        for qa in qas:
            cat = CATEGORIES[qa["category"]]
            for v in variants:
                res = await retrieve(v, qa["question"], qvec_by_text[qa["question"]],
                                     storage, tesseract, linker, nodes, speakers, conv_idx)
                score_question(hits[v][cat], qa, res)
        log(f"conv {conv_idx}: {len(qas)} questions done ({time.time() - t0:.0f}s)")
    return hits


def format_table(hits: dict) -> list[str]:
    """One row per variant and category, plus ALL; ev@k averages over the n_ev questions with evidence."""
    def row(v, cat, h):
        ev = (f"{h['n_ev']:5d} {100 * h['e10'] / h['n_ev']:6.1f}% {100 * h['e50'] / h['n_ev']:6.1f}%"
              if h["n_ev"] else f"{0:5d} {'-':>7} {'-':>7}")
        return (f"{v:24} {cat:12} {h['n']:5d} {100 * h['r10'] / h['n']:9.1f}% {100 * h['r50'] / h['n']:9.1f}% "
                f"{100 * h['rall'] / h['n']:10.1f}% {ev}")

    lines = [f"{'variant':24} {'category':12} {'n':>5} {'recall@10':>10} {'recall@50':>10} {'recall@all':>11} "
             f"{'n_ev':>5} {'ev@10':>7} {'ev@50':>7}"]
    for v, cats in hits.items():
        for cat, h in sorted(cats.items()):
            lines.append(row(v, cat, h))
        tot = new_counts()
        for h in cats.values():
            for k in tot:
                tot[k] += h[k]
        if tot["n"]:
            lines.append(row(v, "ALL", tot))
        lines.append("")
    return lines


async def main():
    data = json.load(open(Path(__file__).resolve().parents[2] / "research" / "datasets" / "locomo10.json"))
    if EMBEDDER == "hash":
        print("EMBEDDER=hash: hash-embedder numbers are structural checks, not retrieval quality")
    async with aiohttp.ClientSession() as http:
        hits = await evaluate(data, http=http)
    print("\n" + "\n".join(format_table(hits)))


if __name__ == "__main__":
    asyncio.run(main())
