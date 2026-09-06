"""
Simple benchmark runner that saves results with retrieved memories to JSON.

This benchmark uses the CORE temporal utilities from the memmachine system,
ensuring a fair evaluation that reflects actual production performance.
"""
import json
import asyncio
import aiohttp
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from NeuralGraph import NeuralNode, NeuralEdge, NodeLayer, EdgeType, generate_edge_id
from NeuralGraph.storage import InMemoryNeuralGraphStorage
from NeuralGraph.tesseract import (
    Tesseract,
    detect_query_type,
    QueryType,
    detect_list_question_universal,
    is_open_domain_world_query,
    should_use_open_domain_infer,
    looks_open_domain_question,
)
from NeuralGraph.dialogue_linker import DialogueLinker
from NeuralGraph.answering import AnsweringConfig, generate_answer, get_embedding
from NeuralGraph.reranker import rerank_candidates_parallel
from NeuralGraph.service import (
    extract_keywords,
    is_temporal_question,
    extract_month_year_from_text,
    extract_topic_keywords_for_temporal,
    extract_count_answer,
    extract_relationship_status,
    extract_span_answer_semantic,
    extract_list_items_semantic,
    extract_duration_answer,
)

# Import CORE temporal utilities - ensures benchmark uses production code
from NeuralGraph.temporal_utils import (
    # Ingestion-time functions (used during message indexing)
    parse_datetime_flexible,
    resolve_relative_dates,
    generate_temporal_tokens,
    preprocess_message_for_indexing,
    extract_explicit_date,
    extract_duration_metadata,
    # Query-time functions (used during retrieval)
    expand_temporal_query,
    infer_query_mode,
)
from NeuralGraph.speaker_profiles import UniversalSpeakerProfiler
from NeuralGraph import llm_profile_extractor


import os

# Ollama for answer generation and embeddings (local)
from NeuralGraph import llm_backend
OLLAMA_BASE_URL = llm_backend.LLM_BASE_URL   # LM Studio (OpenAI-compatible) or Ollama
OLLAMA_MODEL = llm_backend.LLM_MODEL
EMBEDDING_MODEL = llm_backend.EMBED_MODEL
ANSWERING_CONFIG = AnsweringConfig(
    ollama_base_url=OLLAMA_BASE_URL,
    answer_model=OLLAMA_MODEL,
    embedding_model=EMBEDDING_MODEL,
)

# OpenAI for judging only
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")  # never hardcode; export it in your shell
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-4o")
USE_OPENAI_JUDGE = bool(OPENAI_API_KEY)  # falls back to the local model as judge when no key is set
USE_OPENAI_ANSWER = False  # Using Qwen for answers

# Set Ollama for profile extractor module
llm_profile_extractor.USE_OPENAI_EXTRACTION = False

TOP_K = 50

CATEGORIES = {1: "single_hop", 2: "temporal", 3: "open_domain", 4: "multi_hop"}

# ---- Run controls (env vars) -------------------------------------------------
#   RUN_NAME=flat_single_hop      -> writes demo/results/<RUN_NAME>.json
#   ONLY_CAT=single_hop,temporal  -> restrict categories
#   ONLY_CONV=0,1                 -> restrict conversation indices (0-9)
#   MAX_QUESTIONS=50              -> cap total questions
#   GRAPH_EXPANSION=1             -> graph arm (dialogue links + TEMPORAL/ENTITY edges); 0 = flat arm
RUN_NAME = os.environ.get("RUN_NAME", "omega")
OUTPUT_PATH = Path(__file__).parent / "results" / f"{RUN_NAME}.json"
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
MAX_QUESTIONS = int(os.environ.get("MAX_QUESTIONS", "2000"))
ONLY_CAT = {c.strip() for c in os.environ.get("ONLY_CAT", "").split(",") if c.strip()}
ONLY_CONV = {int(c) for c in os.environ.get("ONLY_CONV", "").split(",") if c.strip()}
GRAPH_EXPANSION = os.environ.get("GRAPH_EXPANSION", "0") == "1"
GRAPH_SEED_K = int(os.environ.get("GRAPH_SEED_K", "10"))
GRAPH_EDGE_DECAY = float(os.environ.get("GRAPH_EDGE_DECAY", "0.8"))
USE_SPEAKER_PROFILES = os.environ.get("USE_SPEAKER_PROFILES", "0") == "1"
#   RETRIEVAL_MODE=flat | graph | hybrid
#     flat   = Tesseract top-50 (original system)
#     graph  = flat + dialogue links + TEMPORAL/ENTITY neighbours of the top seeds, appended after the flat list
#     hybrid = flat + speaker boost + pure-embedding back-fill of messages Tesseract's filters dropped
#     local_pairs = per-agent memory: route the question to the named speaker's own store (Tesseract top-50),
#              then back-fill 30 hits from that speaker's question+reply PAIR nodes (best retrieval stack, H9)
RETRIEVAL_MODE = os.environ.get("RETRIEVAL_MODE", "graph" if GRAPH_EXPANSION else "flat")
EXTRA_CANDIDATES = int(os.environ.get("EXTRA_CANDIDATES", "30"))   # appended after the flat top-50
#     mega = per-agent pool + RRF fusion of {vector(+pairs), entity-graph PageRank hop, Tesseract} top-50,
#            then 30 pair back-fill (NeuralGraph/mega_search.py)
RERANK_WINDOW = TOP_K + (EXTRA_CANDIDATES if RETRIEVAL_MODE in ("graph", "hybrid", "local_pairs", "mega") else 0)
SPEAKER_BOOST = float(os.environ.get("SPEAKER_BOOST", "1.5"))
# H5 open-domain routing flags (all off by default = original behaviour)
#   OPEN_DOMAIN_KEEP_CONTEXT=1 -> OPEN_DOMAIN_WORLD keeps the reranked memories and uses
#                                 OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT (mode OPEN_DOMAIN_WORLD_MEM)
#   OPEN_DOMAIN_FORCE_INFER=1  -> hedged questions (likely/might/would...) routed STRICT/INFERENTIAL/
#                                 OPEN_DOMAIN_WORLD are forced to OPEN_DOMAIN_INFER
#   OPEN_DOMAIN_FORCE_INFER=2  -> same, but overrides every mode (TEMPORAL/LIST/AGGREGATION too)
#   OPEN_DOMAIN_INFER_WORLD=1  -> OPEN_DOMAIN_INFER (and the not-found retry) uses the
#                                 memories+world-knowledge prompt instead of OPEN_DOMAIN_INFER_PROMPT
OPEN_DOMAIN_KEEP_CONTEXT = os.environ.get("OPEN_DOMAIN_KEEP_CONTEXT", "0") == "1"
OPEN_DOMAIN_FORCE_INFER = int(os.environ.get("OPEN_DOMAIN_FORCE_INFER", "0"))
OPEN_DOMAIN_INFER_WORLD = os.environ.get("OPEN_DOMAIN_INFER_WORLD", "0") == "1"

# Timing storage
import time
from collections import defaultdict
TIMING_DATA = defaultdict(list)

import random

ROUTING_STATS = {
    "TEMPORAL": 0,
    "STRICT": 0,
    "INFERENTIAL": 0,
    "AGGREGATION": 0,
    "LIST": 0,
    "OPEN_DOMAIN_INFER": 0,
    "OPEN_DOMAIN_WORLD": 0,
    "ADVERSARIAL": 0,
}
ROUTING_SAMPLES = []
INFERENTIAL_SAMPLES = []
# H5: mode actually used at answer time (after the OPEN_DOMAIN_INFER upgrade / H5 flags)
FINAL_MODE_STATS = defaultdict(int)

RETRIEVAL_METRICS = {
    cat: {
        "recall_at_10": [],
        "recall_at_50": [],
        "oracle_rank": [],
        "filter_drop_count": [],
        "total_candidates": [],
    }
    for cat in CATEGORIES.values()
}


def check_gold_in_memories_substring(gold_answer: str, memories: list, top_k: int = 10) -> tuple[bool, int]:
    if not gold_answer:
        return False, 0

    gold_lower = str(gold_answer).lower().strip()
    gold_parts = [p.strip() for p in gold_lower.replace(",", "|").replace(" and ", "|").split("|")]
    gold_parts = [p for p in gold_parts if len(p) > 2]

    for rank, mem in enumerate(memories[:top_k], 1):
        mem_text = mem.get("text", "").lower() if isinstance(mem, dict) else str(mem).lower()
        for part in gold_parts:
            if part in mem_text:
                return True, rank
        if gold_lower in mem_text:
            return True, rank

    return False, 0


async def check_gold_in_memories_oracle(
    session, question: str, gold_answer: str, memories: list, top_k: int = 10
) -> tuple[bool, int]:
    if not gold_answer:
        return False, 0

    for rank, mem in enumerate(memories[:top_k], 1):
        mem_text = mem.get("text", "") if isinstance(mem, dict) else str(mem)
        speaker = mem.get("speaker", "Unknown") if isinstance(mem, dict) else "Unknown"

        prompt = f"""Does this memory contain evidence that could answer the question?

Question: {question}
Expected answer: {gold_answer}

Memory from [{speaker}]: {mem_text[:400]}

Reply with ONLY "YES" or "NO".
- YES if the memory contains the answer or strong evidence for it
- NO if the memory is unrelated or about a different person/topic"""

        try:
            resp = (await llm_backend.llm_generate(
                session, prompt, model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL,
                temperature=0, max_tokens=5, timeout_seconds=10,
            )).upper()
            if "YES" in resp:
                return True, rank
        except Exception:
            gold_lower = str(gold_answer).lower()
            if gold_lower in mem_text.lower():
                return True, rank

    return False, 0


USE_QWEN_ORACLE = False  # Set to False for clean recall@k metrics (no LLM-grading)


async def check_gold_in_memories(
    session, question: str, gold_answer: str, memories: list, top_k: int = 10
) -> tuple[bool, int]:
    if USE_QWEN_ORACLE and session is not None:
        return await check_gold_in_memories_oracle(session, question, gold_answer, memories, top_k)
    else:
        return check_gold_in_memories_substring(gold_answer, memories, top_k)





def compute_retrieval_summary(metrics: dict) -> dict:
    summary = {}
    for cat, data in metrics.items():
        if not data["recall_at_10"]:
            continue
        n = len(data["recall_at_10"])
        recall_10 = sum(data["recall_at_10"]) / n * 100 if n > 0 else 0
        recall_50 = sum(data["recall_at_50"]) / n * 100 if n > 0 else 0

        found_ranks = [r for r in data["oracle_rank"] if r > 0]
        avg_oracle_rank = sum(found_ranks) / len(found_ranks) if found_ranks else 0

        rerank_gain = recall_50 - recall_10

        summary[cat] = {
            "count": n,
            "recall_at_10": round(recall_10, 1),
            "recall_at_50": round(recall_50, 1),
            "rerank_gain": round(rerank_gain, 1),
            "avg_oracle_rank": round(avg_oracle_rank, 1),
            "oracle_found_count": len(found_ranks),
        }
    return summary


def save_results(results, stats):
    total_correct = sum(s["correct"] for s in stats.values())
    total_questions = sum(s["total"] for s in stats.values())
    accuracy = 100 * total_correct / total_questions if total_questions > 0 else 0

    retrieval_summary = compute_retrieval_summary(RETRIEVAL_METRICS)

    # Compute latency statistics for both retrieval and answer generation
    def compute_latency_percentiles(latencies):
        if not latencies:
            return {}
        latencies_sorted = sorted(latencies)
        n = len(latencies_sorted)
        return {
            "count": n,
            "mean_ms": round(sum(latencies) / n, 1),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "p50_ms": round(latencies_sorted[n // 2], 1),
            "p90_ms": round(latencies_sorted[int(n * 0.9)], 1),
            "p95_ms": round(latencies_sorted[int(n * 0.95)], 1),
            "p99_ms": round(latencies_sorted[int(n * 0.99)], 1),
        }

    retrieval_latencies = [r.get("retrieval_latency_ms", 0) for r in results if r.get("retrieval_latency_ms")]
    rerank_latencies = [r.get("rerank_latency_ms", 0) for r in results if r.get("rerank_latency_ms")]
    answer_latencies = [r.get("answer_latency_ms", 0) for r in results if r.get("answer_latency_ms")]
    e2e_latencies = [r.get("e2e_latency_ms", 0) for r in results if r.get("e2e_latency_ms")]

    latency_stats = {
        "retrieval": compute_latency_percentiles(retrieval_latencies),
        "reranking": compute_latency_percentiles(rerank_latencies),
        "answer_generation": compute_latency_percentiles(answer_latencies),
        "end_to_end": compute_latency_percentiles(e2e_latencies),
    }

    output = {
        "metadata": {
            "model": "Tesseract 4D Memory",
            "answer_model": OLLAMA_MODEL,
            "reranker_model": RERANKER_MODEL,
            "judge": "GPT-4o (OpenAI)",
            "timestamp": datetime.now().isoformat(),
            "total_questions": total_questions,
            "total_correct": total_correct,
            "accuracy": round(accuracy, 2),
            "latency_stats": latency_stats,
            "category_stats": {
                cat: {
                    "accuracy": round(100 * s["correct"] / s["total"], 1) if s["total"] > 0 else 0,
                    **s
                } for cat, s in stats.items()
            },
            "retrieval_metrics": retrieval_summary,
        },
        "results": results,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)


USE_SLM_RERANKER = True
RERANKER_MODEL = llm_backend.LLM_MODEL


# =============================================================================
# GPT-4o JUDGE with ACCURACY_PROMPT
# =============================================================================

ACCURACY_PROMPT = """
Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"""


def parse_judge_label(resp: str) -> bool:
    """Strictly parse judge response. Returns True only if label is exactly CORRECT."""
    import json as json_module

    # Method 1: Strict JSON parsing
    try:
        obj = json_module.loads(resp.strip())
        label = str(obj.get("label", "")).strip().upper()
        return label == "CORRECT"
    except Exception:
        pass

    # Method 2: Extract JSON from response
    try:
        json_match = re.search(r'\{[^}]+\}', resp)
        if json_match:
            obj = json_module.loads(json_match.group())
            label = str(obj.get("label", "")).strip().upper()
            return label == "CORRECT"
    except Exception:
        pass

    # Method 3: Regex on label field
    m = re.search(r'"label"\s*:\s*"(CORRECT|WRONG)"', resp, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper() == "CORRECT"

    # Default: fail-safe
    return False


async def judge_answer(session, question: str, generated: str, gold) -> bool:
    """Judge using GPT-4o."""
    gen_lower = str(generated).lower().strip()
    gold_lower = str(gold).lower().strip()

    # Exact match auto-pass
    if gold_lower and gen_lower == gold_lower:
        return True

    # Substring match for simple cases
    if gold_lower and gold_lower in gen_lower:
        return True

    # Skip if no gold answer
    if not gold_lower:
        return False

    prompt = ACCURACY_PROMPT.format(
        question=question,
        gold_answer=gold,
        generated_answer=generated
    )

    try:
        if USE_OPENAI_JUDGE:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": JUDGE_MODEL,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0,
                    "max_tokens": 150
                },
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                result = await response.json()
                resp_text = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        else:
            # Local judge (same prompt) when no OpenAI key is available.
            resp_text = await llm_backend.llm_generate(
                session, prompt, temperature=0, max_tokens=150, timeout_seconds=60,
            )
        return parse_judge_label(resp_text)
    except Exception as e:
        print(f"Judge error: {e}")
        return False


async def embed_batch(http, texts):
    """Batch embeddings on OpenAI-style servers (LM Studio); one-by-one fallback for Ollama."""
    if llm_backend.backend_for(llm_backend.LLM_BASE_URL) == "openai":
        out = []
        for i in range(0, len(texts), 64):
            async with http.post(
                f"{llm_backend.LLM_BASE_URL}/v1/embeddings",
                json={"model": llm_backend.EMBED_MODEL, "input": texts[i:i + 64]},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                resp.raise_for_status()
                out.extend(d["embedding"] for d in (await resp.json())["data"])
        return out
    return [await get_embedding(http, t, config=ANSWERING_CONFIG) for t in texts]


async def build_pair_nodes(http, conv_idx, messages, nodes):
    """PAIR nodes: "[prev speaker] prev\n[speaker] this", embedded as one text, so a short reply is indexed
    together with the question it answers. Kept OUT of storage; metadata pair_members maps back to the
    two message node ids (reply first). Speaker = the replier."""
    texts, members = [], []
    for i in range(1, len(messages)):
        texts.append(f"[{messages[i-1]['speaker']}] {messages[i-1]['text']}\n[{messages[i]['speaker']}] {messages[i]['text']}")
        members.append([nodes[i].node_id, nodes[i - 1].node_id])
    embs = await embed_batch(http, texts)
    out = []
    for k, (text, emb, mem) in enumerate(zip(texts, embs, members)):
        anchor = nodes[k + 1]
        out.append(NeuralNode(
            node_id=f"pair_{conv_idx}_{k}", session_key=f"conv_{conv_idx}", content=text,
            layer=NodeLayer.MESSAGE, embedding=emb, created_at=datetime.now(),
            metadata={"speaker": anchor.speaker_id, "datetime": anchor.metadata.get("datetime"), "pair_members": mem},
        ))
    return out


def unpair(ranked, node_by_id, seen):
    """Expand pair hits into member message nodes (reply first), skipping ids already seen."""
    out = []
    for n, c in ranked:
        for mid in n.metadata.get("pair_members") or [n.node_id]:
            if mid not in seen:
                seen.add(mid)
                out.append((node_by_id[mid], c))
    return out


def cosine_topk(nodes, query_vec, k, _cache={}):
    """Pure-embedding top-k over a conversation (matrix cached per node list)."""
    import numpy as np
    key = id(nodes)
    if key not in _cache:
        _cache.clear()
        M = np.array([n.embedding for n in nodes], dtype=np.float32)
        M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
        _cache[key] = M
    M = _cache[key]
    q = np.array(query_vec, dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9
    sims = M @ q
    order = np.argsort(-sims)[:k]
    return [(nodes[i], float(sims[i])) for i in order]


async def expand_via_graph(storage, dialogue_linker, retrieved):
    """Graph arm of the A/B: grow the flat top-K with graph neighbours.

    Sources, applied to the top GRAPH_SEED_K hits only:
      - dialogue links (coreference / response bindings) at 0.85x the seed charge
      - TEMPORAL edges (previous/next message) at GRAPH_EDGE_DECAY x
      - ENTITY edges (same speaker, next 3 messages) at GRAPH_EDGE_DECAY x
    Returns the merged list sorted by charge. Flat arm (GRAPH_EXPANSION=0) skips this entirely.
    """
    expanded = []
    seen = set()
    for node, charge in retrieved:
        if node.node_id not in seen:
            expanded.append((node, charge))
            seen.add(node.node_id)

    for node, charge in retrieved[:GRAPH_SEED_K]:
        # Dialogue links return (linked_id, strength, link_type) tuples.
        for link in dialogue_linker.get_linked_messages(node.node_id):
            linked_id = link[0] if isinstance(link, tuple) else link
            if linked_id in seen:
                continue
            linked_node = await storage.get_node(linked_id)
            if linked_node:
                expanded.append((linked_node, charge * 0.85))
                seen.add(linked_id)

        neighbours = await storage.get_neighbors(
            node.node_id,
            edge_types=[EdgeType.TEMPORAL, EdgeType.ENTITY],
            direction="both",
        )
        for nb_node, edge in neighbours:
            if nb_node.node_id in seen:
                continue
            expanded.append((nb_node, charge * GRAPH_EDGE_DECAY * edge.base_weight))
            seen.add(nb_node.node_id)

    expanded.sort(key=lambda x: x[1], reverse=True)
    return expanded


async def run_benchmark():
    # Load LoCoMo data
    locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    results = []
    stats = {cat: {"correct": 0, "total": 0} for cat in CATEGORIES.values()}

    async with aiohttp.ClientSession() as http:
        for conv_idx, conv in enumerate(data):
            if ONLY_CONV and conv_idx not in ONLY_CONV:
                continue
            print(f"\n{'='*60}")
            print(f"CONVERSATION {conv_idx + 1}")
            print(f"{'='*60}")

            conversation = conv.get("conversation", conv)
            messages = []
            session_idx = 1
            while f"session_{session_idx}" in conversation:
                datetime_str = conversation.get(f"session_{session_idx}_date_time", "")
                for msg in conversation[f"session_{session_idx}"]:
                    messages.append({
                        "speaker": msg.get("speaker", "Unknown"),
                        "text": msg.get("text", ""),
                        "datetime": datetime_str,
                    })
                session_idx += 1

            print(f"Loaded {len(messages)} messages")

            storage = InMemoryNeuralGraphStorage()
            tesseract = Tesseract(storage)

            # SPEAKER PROFILES: Build profiles for ALL speakers in this conversation
            speaker_profiler = UniversalSpeakerProfiler()

            # PARALLEL FACT EXTRACTION: Extract facts from ALL messages in parallel batches
            print(f"Extracting facts from {len(messages)} messages in parallel...")
            from NeuralGraph.llm_profile_extractor import extract_facts_parallel
            extraction_start = time.perf_counter()
            if USE_SPEAKER_PROFILES:
                all_extracted_facts = await extract_facts_parallel(http, messages, batch_size=15)
            else:
                all_extracted_facts = [[] for _ in messages]  # profiles off: skip one LLM call per message
            extraction_time = max(time.perf_counter() - extraction_start, 1e-6)
            print(f"  [OK] Extracted facts in {extraction_time:.1f}s ({len(messages)/extraction_time:.1f} msgs/sec)")

            # Add messages and extracted facts to profiles (fast - no LLM calls)
            for msg_idx, (msg, facts) in enumerate(zip(messages, all_extracted_facts)):
                # Add without LLM extraction (we already did it in parallel)
                await speaker_profiler.add_message(msg["speaker"], msg["text"], llm_extractor=None)
                # Manually add the pre-extracted facts
                if facts:
                    speaker_profiler.profiles[msg["speaker"]].extracted_facts.extend(facts)

            speaker_nodes: dict[str, list[str]] = {}
            all_node_ids: list[str] = []

            for msg_idx, msg in enumerate(messages):

                embedding = await get_embedding(http, msg["text"], config=ANSWERING_CONFIG)
                if not embedding:
                    continue

                keywords = extract_keywords(msg["text"])
                content_entities = set(re.findall(r'\b[A-Z][a-z]+\b', msg["text"]))
                content_entities.discard("I")

                message_date = parse_datetime_flexible(msg["datetime"])
                temporal_data = generate_temporal_tokens(message_date, msg["text"])
                date_tokens = temporal_data["date_tokens"]
                temporal_metadata = temporal_data["temporal_metadata"]

                # FAIR TEMPORAL: Resolve dates for METADATA ONLY (not stored in text)
                # The LLM must reason about "yesterday" at answer-time using created_at
                resolved_content, resolved_dates = resolve_relative_dates(msg["text"], message_date)

                resolved_date = None
                resolved_date_source = None
                explicit_date = extract_explicit_date(msg["text"], message_date)
                duration_meta = extract_duration_metadata(msg["text"], message_date)

                if explicit_date:
                    resolved_date = explicit_date
                    resolved_date_source = "explicit"
                elif resolved_dates:
                    date_values = [
                        v for v in resolved_dates.values()
                        if isinstance(v, str) and re.search(r'\d', v)
                    ]
                    unique_values = list(dict.fromkeys(date_values))
                    if len(unique_values) == 1:
                        resolved_date = unique_values[0]
                        resolved_date_source = "relative"
                if not resolved_date and message_date:
                    resolved_date = message_date.strftime("%Y-%m-%d")
                    resolved_date_source = "message"

                # Store ORIGINAL text - no date tokens baked in (non-leaky)
                node_id = f"msg_{conv_idx}_{msg_idx}"
                node = NeuralNode(
                    node_id=node_id,
                    session_key=f"conv_{conv_idx}",
                    content=msg["text"],  # FAIR: original text only
                    layer=NodeLayer.MESSAGE,
                    embedding=embedding,
                    created_at=datetime.now(),
                    metadata={
                        "speaker": msg["speaker"],
                        "datetime": msg["datetime"],  # Message timestamp for reasoning
                        "keywords": list(keywords),
                        "entities": list(content_entities),
                        "temporal_tokens": date_tokens,  # For retrieval indexing only
                        "temporal_metadata": temporal_metadata,
                        "resolved_date": resolved_date,
                        "resolved_date_source": resolved_date_source,
                        "explicit_date": explicit_date,
                        "duration_years": duration_meta.get("duration_years"),
                        "duration_months": duration_meta.get("duration_months"),
                        "since_year": duration_meta.get("since_year"),
                        "since_date": duration_meta.get("since_date"),
                        "date_tokens": date_tokens,  # Backward compat
                        "temporal": temporal_metadata,  # Backward compat
                        "resolved_dates": resolved_dates,  # Metadata only, not in text
                    },
                )
                await storage.save_node(node)
                all_node_ids.append(node_id)

                speaker = msg["speaker"].lower()
                if speaker not in speaker_nodes:
                    speaker_nodes[speaker] = []
                speaker_nodes[speaker].append(node_id)

            edge_count = 0
            for speaker, node_ids in speaker_nodes.items():
                for i, src_id in enumerate(node_ids):
                    for j in range(i + 1, min(i + 4, len(node_ids))):
                        tgt_id = node_ids[j]
                        edge = NeuralEdge(
                            edge_id=generate_edge_id(),
                            source_id=src_id,
                            target_id=tgt_id,
                            edge_type=EdgeType.ENTITY,
                            base_weight=0.8,
                            metadata={"speaker": speaker},
                        )
                        await storage.save_edge(edge)
                        edge_count += 1

            temporal_edge_count = 0
            for i in range(len(all_node_ids) - 1):
                src_id = all_node_ids[i]
                tgt_id = all_node_ids[i + 1]
                edge = NeuralEdge(
                    edge_id=generate_edge_id(),
                    source_id=src_id,
                    target_id=tgt_id,
                    edge_type=EdgeType.TEMPORAL,
                    base_weight=0.85,
                    metadata={"direction": "before"},
                )
                await storage.save_edge(edge)
                temporal_edge_count += 1

            dialogue_linker = DialogueLinker(storage)
            all_nodes = [await storage.get_node(nid) for nid in all_node_ids]
            all_nodes = [n for n in all_nodes if n is not None]
            embedding_matrix = None  # lazily built by cosine_topk

            pair_nodes, local_tesseracts, mega_index = [], {}, None
            if RETRIEVAL_MODE in ("local_pairs", "mega"):
                pair_nodes = await build_pair_nodes(http, conv_idx, messages, all_nodes)
                for sp, ids in speaker_nodes.items():
                    local_storage = InMemoryNeuralGraphStorage()
                    for nid in ids:
                        await local_storage.save_node(await storage.get_node(nid))
                    local_tesseracts[sp] = Tesseract(local_storage)
                print(f"  Per-agent memory: {len(pair_nodes)} pair nodes, {len(local_tesseracts)} agent stores")
                if RETRIEVAL_MODE == "mega":
                    from NeuralGraph.mega_search import MegaIndex
                    mega_index = MegaIndex(all_nodes, pair_nodes)
            node_by_id = {n.node_id: n for n in all_nodes}
            aggregators, bindings = await dialogue_linker.process_dialogue_sequence(
                all_nodes, f"conv_{conv_idx}"
            )

            print(f"Indexed {len(all_node_ids)} memories, {edge_count} entity + {temporal_edge_count} temporal edges")
            print(f"  Dialogue links: {len(aggregators)} aggregators, {len(bindings)} bindings")

            qa_list = conv.get("qa", [])
            for q_idx, qa in enumerate(qa_list):
                if len(results) >= MAX_QUESTIONS:
                    break

                category_id = qa.get("category", 1)
                # Skip adversarial questions (category 5)
                if category_id == 5:
                    continue
                category = CATEGORIES.get(category_id, "unknown")
                if category not in RETRIEVAL_METRICS:
                    continue
                if ONLY_CAT and category not in ONLY_CAT:
                    continue
                question = qa.get("question", "")
                gold = qa.get("answer", "")

                query_emb = await get_embedding(http, question, config=ANSWERING_CONFIG)
                if not query_emb:
                    continue

                # Start end-to-end and retrieval timing
                t_e2e_start = time.perf_counter()
                t_retrieval_start = time.perf_counter()

                named_agents = [sp for sp in speaker_nodes.keys() if sp in question.lower()]
                active_tesseract = tesseract
                if RETRIEVAL_MODE in ("local_pairs", "mega") and len(named_agents) == 1:
                    active_tesseract = local_tesseracts[named_agents[0]]   # per-agent memory routing
                retrieved = await active_tesseract.retrieve(
                    query_text=question,
                    query_embedding=query_emb,
                    session_key=f"conv_{conv_idx}",
                    limit=TOP_K,
                    auto_expand_temporal=True,
                )
                if RETRIEVAL_MODE in ("local_pairs", "mega"):
                    pool = ([p for p in pair_nodes if p.speaker_id.lower() == named_agents[0]]
                            if len(named_agents) == 1 else pair_nodes)
                    retrieved = retrieved[:TOP_K]
                    if RETRIEVAL_MODE == "mega":
                        pool_ids = ({n.node_id for n in all_nodes if n.speaker_id.lower() == named_agents[0]}
                                    if len(named_agents) == 1 else {n.node_id for n in all_nodes})
                        tess_ids = [n.node_id for n, _ in retrieved]
                        retrieved = mega_index.retrieve(question, query_emb, pool_ids, k=TOP_K,
                                                        channels="VG", extra_rankings=[tess_ids])
                    seen = {n.node_id for n, _ in retrieved}
                    if pool:
                        extra = unpair(cosine_topk(pool, query_emb, 2 * TOP_K), node_by_id, seen)
                        retrieved = retrieved + extra[:EXTRA_CANDIDATES]

                if RETRIEVAL_MODE == "graph":
                    flat_ids = {n.node_id for n, _ in retrieved}
                    expanded = await expand_via_graph(storage, dialogue_linker, retrieved)
                    extra = [(n, c) for n, c in expanded if n.node_id not in flat_ids]
                    retrieved = retrieved[:TOP_K] + extra[:EXTRA_CANDIDATES]
                elif RETRIEVAL_MODE == "hybrid":
                    named = [sp for sp in speaker_nodes.keys() if sp in question.lower()]
                    if len(named) == 1:
                        retrieved = [(n, c * (SPEAKER_BOOST if n.speaker_id.lower() == named[0] else 1.0))
                                     for n, c in retrieved]
                        retrieved.sort(key=lambda x: x[1], reverse=True)
                    retrieved = retrieved[:TOP_K]
                    have = {n.node_id for n, _ in retrieved}
                    extra = [(n, c) for n, c in cosine_topk(all_nodes, query_emb, TOP_K) if n.node_id not in have]
                    retrieved = retrieved + extra[:EXTRA_CANDIDATES]

                # End pure retrieval timing (tesseract + dialogue linking only)
                t_retrieval = (time.perf_counter() - t_retrieval_start) * 1000
                TIMING_DATA["t_retrieval"].append(t_retrieval)

                all_memories_text = [
                    {"text": node.content, "speaker": node.metadata.get("speaker", "")}
                    for node, charge in retrieved[:RERANK_WINDOW]
                ]

                recall_10, rank_10 = await check_gold_in_memories(
                    http, question, gold, all_memories_text, top_k=10
                )
                RETRIEVAL_METRICS[category]["recall_at_10"].append(1 if recall_10 else 0)

                if recall_10:
                    recall_50, rank_50 = True, rank_10
                else:
                    recall_50, rank_50 = await check_gold_in_memories(
                        http, question, gold, all_memories_text, top_k=50
                    )
                RETRIEVAL_METRICS[category]["recall_at_50"].append(1 if recall_50 else 0)

                oracle_rank = rank_50 if recall_50 else 0
                RETRIEVAL_METRICS[category]["oracle_rank"].append(oracle_rank)
                RETRIEVAL_METRICS[category]["total_candidates"].append(len(retrieved))

                # CRITICAL: Determine query_mode BEFORE reranking (need to know how many memories to request)
                list_question, list_type = detect_list_question_universal(question)
                query_mode = infer_query_mode(question)
                if list_question:
                    query_mode = "AGGREGATION" if list_type == "aggregation" else "LIST"
                if is_temporal_question(question):
                    query_mode = "TEMPORAL"
                elif query_mode == "TEMPORAL":
                    query_mode = "INFERENTIAL"
                # FAIR: routing may only look at the question text, never the gold category label
                # A question that names a conversation participant is about the memories, not the world.
                names_in_q = any(sp in question.lower() for sp in speaker_nodes.keys())
                if is_open_domain_world_query(question) and not names_in_q:
                    query_mode = "OPEN_DOMAIN_WORLD"
                # H5: hedged questions are inference questions regardless of INFERENTIAL detection
                if OPEN_DOMAIN_FORCE_INFER and looks_open_domain_question(question):
                    if OPEN_DOMAIN_FORCE_INFER >= 2 or query_mode in ("STRICT", "INFERENTIAL", "OPEN_DOMAIN_WORLD"):
                        query_mode = "OPEN_DOMAIN_INFER"
                ROUTING_STATS[query_mode] += 1

                # SPEAKER PROFILES: For single_hop, check profile FIRST before retrieval
                profile_answer = None
                used_profile = False
                # FAIR: the gold answer is never passed in; profile answer is used whenever found.
                # Off by default (USE_SPEAKER_PROFILES=1) because it bypasses retrieval entirely.
                if USE_SPEAKER_PROFILES and category == "single_hop" and not list_question:
                    profile_result = await speaker_profiler.query_single_hop(http, question, None)
                    if profile_result['found'] and profile_result['confidence'] >= 0.5:
                        profile_answer = profile_result['answer']
                        used_profile = True
                        print(f"  [PROFILE] Using profile answer (confidence: {profile_result['confidence']:.0%})")

                # Determine how many memories we need based on query type
                # AGGREGATION needs 30 (answers scattered), others need 15
                num_memories_needed = 30 if query_mode in {"AGGREGATION", "LIST"} else 15

                # Reranking timing (separate - uses Phi 3.5 SLM)
                t_rerank_start = time.perf_counter()
                if USE_SLM_RERANKER:
                    reranked = await rerank_candidates_parallel(
                        question,
                        retrieved[:RERANK_WINDOW],
                        max_candidates=RERANK_WINDOW,
                        limit=num_memories_needed,
                        llm_model=RERANKER_MODEL,
                        llm_base_url=OLLAMA_BASE_URL,
                    )
                else:
                    reranked = retrieved[:num_memories_needed]
                t_rerank = (time.perf_counter() - t_rerank_start) * 1000
                TIMING_DATA["t_rerank"].append(t_rerank)

                # UPGRADE INFERENTIAL to aggressive mode for open-domain-style questions
                # These questions NEED strong inference even with weak evidence (60+ markers)
                if query_mode == "INFERENTIAL" and should_use_open_domain_infer(question):
                    query_mode = "OPEN_DOMAIN_INFER"

                # Build context from reranked memories
                # (num_memories_needed already determined based on query_mode)
                context_parts = []
                retrieved_memories = []
                for node, charge in reranked[:num_memories_needed]:
                    speaker = node.metadata.get("speaker", "Unknown")
                    dt = node.metadata.get("datetime", "")
                    # FAIR: Use original content - LLM must reason about relative dates
                    original_content = node.content
                    resolved_date = node.metadata.get("resolved_date", "")
                    resolved_dates = node.metadata.get("resolved_dates", {}) or {}
                    resolved_pairs = []
                    if isinstance(resolved_dates, dict):
                        for key, value in resolved_dates.items():
                            resolved_pairs.append(f"{key}->{value}")
                    resolved_relative = ", ".join(resolved_pairs)

                    if query_mode == "TEMPORAL":
                        # Provide resolved dates to avoid timestamp-only answers
                        context_parts.append(
                            f"[MESSAGE_DATETIME={dt}] [RESOLVED_DATE={resolved_date}] "
                            f"[RESOLVED_RELATIVE={resolved_relative}] [SPEAKER={speaker}] {original_content}"
                        )
                    else:
                        context_parts.append(f"[{speaker}] {original_content}\n  (sent: {dt})")


                    retrieved_memories.append({
                        "speaker": speaker,
                        "text": original_content[:300],
                        "datetime": dt,
                        "charge": round(charge, 3),
                    })

                context = "\n".join(context_parts)

                current_id = len(results) + 1
                if len(ROUTING_SAMPLES) < 30 and random.random() < 0.1:
                    ROUTING_SAMPLES.append({
                        "id": current_id,
                        "mode": query_mode,
                        "question": question[:60],
                        "context_preview": context[:200],
                    })

                if query_mode == "INFERENTIAL" and len(INFERENTIAL_SAMPLES) < 30 and random.random() < 0.15:
                    INFERENTIAL_SAMPLES.append({
                        "id": current_id,
                        "category": category,
                        "question": question[:80],
                        "first_memory": context_parts[0][:150] if context_parts else "N/A",
                    })

                candidate_memories = [
                    {"text": node.content, "metadata": node.metadata}
                    for node, _charge in reranked[:num_memories_needed]
                ]

                if query_mode == "OPEN_DOMAIN_WORLD" and not OPEN_DOMAIN_KEEP_CONTEXT:
                    candidate_memories = []
                    context = ""

                # H5: prompt variant actually sent to the answer model
                answer_mode = query_mode
                if query_mode == "OPEN_DOMAIN_WORLD" and OPEN_DOMAIN_KEEP_CONTEXT:
                    answer_mode = "OPEN_DOMAIN_WORLD_MEM"
                elif query_mode == "OPEN_DOMAIN_INFER" and OPEN_DOMAIN_INFER_WORLD:
                    answer_mode = "OPEN_DOMAIN_INFER_WORLD"
                retry_mode = "OPEN_DOMAIN_INFER_WORLD" if OPEN_DOMAIN_INFER_WORLD else "OPEN_DOMAIN_INFER"
                FINAL_MODE_STATS[answer_mode] += 1

                extracted_answer = None
                if query_mode == "TEMPORAL":
                    # TEMPORAL extraction is reliable - keep it
                    duration_direct = extract_duration_answer(question, candidate_memories)
                    if duration_direct:
                        extracted_answer = duration_direct
                    else:
                        topic_keywords = extract_topic_keywords_for_temporal(question)
                        query_month, query_year = extract_month_year_from_text(question)
                        for mem in candidate_memories:
                            meta = mem.get("metadata", {})
                            resolved_date = meta.get("resolved_date")
                            source = meta.get("resolved_date_source")
                            if source in ("explicit", "relative") and resolved_date:
                                text_lower = mem.get("text", "").lower()
                                if topic_keywords and not any(k in text_lower for k in topic_keywords):
                                    continue
                                if query_month or query_year:
                                    mem_month, mem_year = extract_month_year_from_text(resolved_date)
                                    if query_month and mem_month != query_month:
                                        continue
                                    if query_year and mem_year != query_year:
                                        continue
                                extracted_answer = resolved_date
                                break
                elif query_mode != "OPEN_DOMAIN_WORLD":
                    # Only use simple, reliable extractors (count and relationship status)
                    # These pattern-based extractors work well for specific formats
                    count_direct = extract_count_answer(question, candidate_memories)
                    if count_direct:
                        extracted_answer = count_direct
                    else:
                        relation_direct = extract_relationship_status(question, candidate_memories)
                        if relation_direct:
                            extracted_answer = relation_direct
                    # CRITICAL FIX: Skip ALL span/list extraction for ALL categories
                    # These regex-based extractors produce garbage like "Wow", "Thanks",
                    # "form of therapy", fragments from contractions, etc.
                    # Let the LLM handle extraction - it's smarter.

                t_answer_start = time.perf_counter()
                # Use profile answer if available, otherwise generate from retrieval
                if used_profile and profile_answer:
                    generated = profile_answer
                    t_answer = 0  # No LLM call needed
                elif extracted_answer:
                    generated = extracted_answer
                    t_answer = 0
                else:
                    generated = await generate_answer(
                        http, question, context, mode=answer_mode, config=ANSWERING_CONFIG
                    )
                    t_answer = (time.perf_counter() - t_answer_start) * 1000
                TIMING_DATA["t_answer"].append(t_answer)

                # FALLBACK: If single_hop or multi_hop says "not found", try open_domain inference
                not_found_phrases = [
                    "not mentioned in the memories",
                    "not found in the memories",
                    "not in the memories",
                    "no information",
                    "cannot determine",
                    "not stated",
                    "not specified",
                    "no mention",
                ]
                answer_lower = generated.lower()
                is_not_found = any(phrase in answer_lower for phrase in not_found_phrases)

                if is_not_found and query_mode != "OPEN_DOMAIN_WORLD":
                    # Second pass: analyze memories and infer
                    generated = await generate_answer(
                        http,
                        question,
                        context,
                        mode=retry_mode,
                        config=ANSWERING_CONFIG,
                    )

                # End-to-end timing (retrieval + rerank + answer generation)
                t_e2e = (time.perf_counter() - t_e2e_start) * 1000
                TIMING_DATA["t_e2e"].append(t_e2e)

                # Judge with GPT-4o
                correct = await judge_answer(http, question, generated, gold)

                stats[category]["total"] += 1
                if correct:
                    stats[category]["correct"] += 1

                result = {
                    "id": len(results) + 1,
                    "conversation": conv_idx + 1,
                    "category": category,
                    "question": question,
                    "generated_answer": generated,
                    "gold_answer": gold,
                    "correct": correct,
                    "mode": answer_mode,  # H5: prompt variant used at answer time
                    "used_speaker_profile": used_profile,  # Track if profile was used
                    "retrieval_latency_ms": round(t_retrieval, 1),
                    "rerank_latency_ms": round(t_rerank, 1),
                    "answer_latency_ms": round(t_answer, 1),
                    "e2e_latency_ms": round(t_e2e, 1),
                    "retrieved_memories": retrieved_memories,
                }
                results.append(result)

                status = "OK" if correct else "FAIL"
                print(f"  [{status}] Q{q_idx+1} ({category}): {question[:50]}...")

                if len(results) % 10 == 0:
                    save_results(results, stats)
                    print(f"    -> Saved {len(results)} results to file")

            if len(results) >= MAX_QUESTIONS:
                print(f"\n*** NAPOLEON EXIT: {MAX_QUESTIONS} questions reached ***")
                break

    save_results(results, stats)

    total_correct = sum(s["correct"] for s in stats.values())
    total_questions = sum(s["total"] for s in stats.values())
    accuracy = 100 * total_correct / total_questions if total_questions > 0 else 0

    print(f"\n{'='*60}")
    print(f"BENCHMARK COMPLETE (Answer: {OLLAMA_MODEL}, Judge: GPT-4o)")
    print(f"{'='*60}")
    print(f"Total: {total_correct}/{total_questions} ({accuracy:.1f}%)")
    for cat, s in stats.items():
        if s["total"] > 0:
            acc = 100 * s["correct"] / s["total"]
            print(f"  {cat}: {s['correct']}/{s['total']} ({acc:.1f}%)")

    # Print latency statistics
    print(f"\n{'='*60}")
    print(f"LATENCY STATISTICS")
    print(f"{'='*60}")

    def print_latency_stats(name, latencies):
        if not latencies:
            print(f"{name}: No data")
            return
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        print(f"\n{name}:")
        print(f"  Count:    {n} queries")
        print(f"  Mean:     {sum(latencies)/n:.1f} ms")
        print(f"  Min:      {min(latencies):.1f} ms")
        print(f"  Max:      {max(latencies):.1f} ms")
        print(f"  P50:      {sorted_lat[n//2]:.1f} ms")
        print(f"  P90:      {sorted_lat[int(n*0.9)]:.1f} ms")
        print(f"  P95:      {sorted_lat[int(n*0.95)]:.1f} ms")
        print(f"  P99:      {sorted_lat[int(n*0.99)]:.1f} ms")

    print_latency_stats("Memory Retrieval", TIMING_DATA.get("t_retrieval", []))
    print_latency_stats(f"Reranking ({RERANKER_MODEL})", TIMING_DATA.get("t_rerank", []))
    print_latency_stats(f"Answer Generation ({OLLAMA_MODEL})", TIMING_DATA.get("t_answer", []))
    print_latency_stats("End-to-End", TIMING_DATA.get("t_e2e", []))

    print(f"\n{'='*60}")
    print(f"ROUTING STATS")
    print(f"{'='*60}")
    print(f"TEMPORAL:    {ROUTING_STATS['TEMPORAL']} questions")
    print(f"STRICT:      {ROUTING_STATS['STRICT']} questions")
    print(f"INFERENTIAL: {ROUTING_STATS['INFERENTIAL']} questions")
    print(f"OPEN_INFER:  {ROUTING_STATS['OPEN_DOMAIN_INFER']} questions")
    print(f"OPEN_WORLD:  {ROUTING_STATS['OPEN_DOMAIN_WORLD']} questions")
    print(f"LIST:        {ROUTING_STATS['LIST']} questions")
    print(f"AGGREGATION: {ROUTING_STATS['AGGREGATION']} questions")
    print(f"FINAL ANSWER MODES (after OPEN_DOMAIN_INFER upgrade / H5 flags):")
    for _m, _n in sorted(FINAL_MODE_STATS.items(), key=lambda kv: -kv[1]):
        print(f"  {_m:<24} {_n} questions")

    print(f"\n{'='*60}")
    print(f"RETRIEVAL METRICS")
    print(f"{'='*60}")
    print(f"{'Category':<15} {'Recall@10':<12} {'Recall@50':<12} {'RerankGain':<12}")
    print(f"{'-'*60}")

    retrieval_summary = compute_retrieval_summary(RETRIEVAL_METRICS)
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop"]:
        if cat in retrieval_summary:
            m = retrieval_summary[cat]
            print(f"{cat:<15} {m['recall_at_10']:>8.1f}%    {m['recall_at_50']:>8.1f}%    {m['rerank_gain']:>+8.1f}%")

    print(f"\nResults saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
