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
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from NeuralGraph import NeuralNode, NeuralEdge, NodeLayer, EdgeType, generate_edge_id
from NeuralGraph.storage import InMemoryNeuralGraphStorage
from NeuralGraph.tesseract import (
    Tesseract,
    detect_list_question_universal,
    is_open_domain_world_query,
    should_use_open_domain_infer,
)
from NeuralGraph.dialogue_linker import DialogueLinker
from NeuralGraph.answering import AnsweringConfig, generate_answer, get_embedding
from NeuralGraph.openai_client import openai_api_key, openai_text
from NeuralGraph.reranker import rerank_candidates_parallel
from NeuralGraph.benchmarking import (
    check_evidence_recall,
    compose_memory_content,
    extract_target_speakers,
    fuse_ranked_candidates,
    is_abstention,
    rank_nodes_lexically,
)
from NeuralGraph.service import (
    extract_keywords,
    is_temporal_question,
    extract_count_answer,
    extract_relationship_status,
    extract_duration_answer,
)

# Import CORE temporal utilities - ensures benchmark uses production code
from NeuralGraph.temporal_utils import (
    # Ingestion-time functions (used during message indexing)
    parse_datetime_flexible,
    resolve_relative_dates,
    generate_temporal_tokens,
    extract_explicit_date,
    extract_duration_metadata,
    # Query-time functions (used during retrieval)
    infer_query_mode,
)
from NeuralGraph.speaker_profiles import UniversalSpeakerProfiler
import os

# GPT for generation; deterministic feature hashing for local embeddings.
ANSWER_MODEL = os.environ.get("NEURALGRAPH_ANSWER_MODEL", "gpt-5.6-sol")
ANSWERING_CONFIG = AnsweringConfig(
    answer_model=ANSWER_MODEL,
)

# Gold answers are sent only to the evaluator, never retrieval or answering.
JUDGE_MODEL = os.environ.get("NEURALGRAPH_JUDGE_MODEL", "gpt-5.6-terra")

TOP_K = int(os.environ.get("NEURALGRAPH_CANDIDATE_K", "80"))
EXTRACT_PROFILE_FACTS = os.environ.get("NEURALGRAPH_EXTRACT_PROFILE_FACTS", "0") == "1"

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

OUTPUT_PATH = Path(__file__).parent / "omega.json"
MAX_QUESTIONS = int(os.environ.get("NEURALGRAPH_MAX_QUESTIONS", "2000"))

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
            "answer_model": ANSWER_MODEL,
            "reranker_model": RERANKER_MODEL,
            "judge": JUDGE_MODEL,
            "embedding_model": "local-feature-hash-1024",
            "benchmark_scope": "all LoCoMo QA categories",
            "category_blind_inference": True,
            "multimodal_captions": True,
            "retrieval_metric": "evidence-id recall",
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


USE_SLM_RERANKER = os.environ.get("NEURALGRAPH_USE_RERANKER", "1") != "0"
RERANKER_MODEL = os.environ.get("NEURALGRAPH_RERANKER_MODEL", "gpt-5.6-terra")


# =============================================================================
# GPT answer-equivalence judge
# =============================================================================

JUDGE_SYSTEM = """You are a deterministic answer-equivalence evaluator.
Return only valid JSON with one key named label and a value of CORRECT or WRONG.
Do not reward answers that merely mention the same topic. Do not add explanations."""

ACCURACY_PROMPT = """Decide whether the generated answer is semantically equivalent to the gold answer.

Requirements:
- All material items in a list answer must be present; harmless extra wording is allowed.
- Dates and relative periods must refer to the same time.
- A related topic, unsupported hedge, or different named entity is WRONG.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

Return {{"label":"CORRECT"}} or {{"label":"WRONG"}}.
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
    """Evaluate only after answer generation has finished."""
    gen_lower = str(generated).lower().strip()
    gold_lower = str(gold).lower().strip()

    # LoCoMo adversarial items use null gold: the correct behavior is to
    # abstain rather than copy the other speaker's plausible answer.
    if gold is None or not gold_lower:
        return is_abstention(generated)

    # Exact match auto-pass
    if gold_lower and gen_lower == gold_lower:
        return True

    prompt = ACCURACY_PROMPT.format(
        question=question,
        gold_answer=gold,
        generated_answer=generated
    )

    try:
        resp_text = await openai_text(
            session,
            prompt,
            instructions=JUDGE_SYSTEM,
            model=JUDGE_MODEL,
            max_output_tokens=256,
            timeout_seconds=60,
            reasoning_effort="low",
        )
        return parse_judge_label(resp_text)
    except Exception as e:
        print(f"Judge error: {e}")
        return False


async def run_benchmark():
    if not openai_api_key():
        raise RuntimeError(
            "OPENAI_API_KEY is required. Configure it in the environment; "
            "never paste it into source or benchmark output."
        )
    # Load LoCoMo data
    locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    results = []
    stats = {cat: {"correct": 0, "total": 0} for cat in CATEGORIES.values()}

    async with aiohttp.ClientSession(trust_env=True) as http:
        for conv_idx, conv in enumerate(data):
            print(f"\n{'='*60}")
            print(f"CONVERSATION {conv_idx + 1}")
            print(f"{'='*60}")

            conversation = conv.get("conversation", conv)
            messages = []
            session_idx = 1
            while f"session_{session_idx}" in conversation:
                datetime_str = conversation.get(f"session_{session_idx}_date_time", "")
                for msg in conversation[f"session_{session_idx}"]:
                    memory_content = compose_memory_content(msg)
                    messages.append({
                        "speaker": msg.get("speaker", "Unknown"),
                        "text": msg.get("text", ""),
                        "content": memory_content,
                        "blip_caption": msg.get("blip_caption", ""),
                        "dia_id": msg.get("dia_id", ""),
                        "datetime": datetime_str,
                    })
                session_idx += 1

            print(f"Loaded {len(messages)} messages")

            storage = InMemoryNeuralGraphStorage()
            tesseract = Tesseract(storage)

            # SPEAKER PROFILES: Build profiles for ALL speakers in this conversation
            speaker_profiler = UniversalSpeakerProfiler()

            if EXTRACT_PROFILE_FACTS:
                print(f"Extracting facts from {len(messages)} messages in parallel...")
                from NeuralGraph.llm_profile_extractor import extract_facts_parallel
                extraction_start = time.perf_counter()
                extraction_messages = [{**msg, "text": msg["content"]} for msg in messages]
                all_extracted_facts = await extract_facts_parallel(
                    http, extraction_messages, batch_size=15
                )
                extraction_time = time.perf_counter() - extraction_start
                print(
                    f"  [OK] Extracted facts in {extraction_time:.1f}s "
                    f"({len(messages)/extraction_time:.1f} msgs/sec)"
                )
            else:
                # Raw messages are always retained in profiles. Avoid thousands
                # of redundant extraction calls unless explicitly requested.
                all_extracted_facts = [[] for _message in messages]

            # Add messages and extracted facts to profiles (fast - no LLM calls)
            for msg_idx, (msg, facts) in enumerate(zip(messages, all_extracted_facts)):
                # Add without LLM extraction (we already did it in parallel)
                await speaker_profiler.add_message(msg["speaker"], msg["content"], llm_extractor=None)
                # Manually add the pre-extracted facts
                if facts:
                    speaker_profiler.profiles[msg["speaker"]].extracted_facts.extend(facts)

            speaker_nodes: dict[str, list[str]] = {}
            all_node_ids: list[str] = []

            for msg_idx, msg in enumerate(messages):

                embedding = await get_embedding(http, msg["content"], config=ANSWERING_CONFIG)
                if not embedding:
                    continue

                keywords = extract_keywords(msg["content"])
                content_entities = set(re.findall(r'\b[A-Z][a-z]+\b', msg["content"]))
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
                    content=msg["content"],
                    layer=NodeLayer.MESSAGE,
                    embedding=embedding,
                    created_at=message_date or datetime.now(),
                    metadata={
                        "speaker": msg["speaker"],
                        "dia_id": msg["dia_id"],
                        "image_caption": msg["blip_caption"],
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
                category = CATEGORIES.get(category_id, "unknown")
                question = qa.get("question", "")
                gold = qa.get("answer", "")
                evidence_ids = qa.get("evidence", [])

                # Inference mode is derived from the question only. Dataset
                # category remains available solely for aggregate reporting.
                list_question, list_type = detect_list_question_universal(question)
                explicit_targets = extract_target_speakers(
                    question, speaker_profiler.profiles.keys()
                )
                query_mode = infer_query_mode(question)
                if list_question:
                    query_mode = "AGGREGATION" if list_type == "aggregation" else "LIST"
                if is_temporal_question(question):
                    query_mode = "TEMPORAL"
                elif query_mode == "TEMPORAL":
                    query_mode = "INFERENTIAL"
                if is_open_domain_world_query(question) and not explicit_targets:
                    query_mode = "OPEN_DOMAIN_WORLD"
                elif query_mode == "INFERENTIAL" and should_use_open_domain_infer(question):
                    query_mode = "OPEN_DOMAIN_INFER"
                ROUTING_STATS[query_mode] += 1

                query_emb = await get_embedding(http, question, config=ANSWERING_CONFIG)
                if not query_emb:
                    continue

                # Start end-to-end and retrieval timing
                t_e2e_start = time.perf_counter()
                t_retrieval_start = time.perf_counter()

                retrieved = await tesseract.retrieve(
                    query_text=question,
                    query_embedding=query_emb,
                    session_key=f"conv_{conv_idx}",
                    limit=TOP_K,
                    auto_expand_temporal=True,
                )

                # Fuse the neural graph with an independent high-recall lexical
                # path. This recovers rare caption terms and exact speaker facts
                # without consulting the category, evidence IDs, or gold answer.
                lexical_results = rank_nodes_lexically(
                    question,
                    all_nodes,
                    speakers=speaker_profiler.profiles.keys(),
                    limit=TOP_K,
                )
                retrieved = fuse_ranked_candidates(
                    retrieved,
                    lexical_results,
                    limit=TOP_K,
                )

                # Dialogue facts often arrive as a question/answer pair or an
                # image followed by an explanation. Expand only around strong
                # seeds, with a wider radius for question-derived multi-step
                # modes. This uses conversation order, not evaluator evidence.
                sequence_positions = {node.node_id: index for index, node in enumerate(all_nodes)}
                neighbor_radius = 2 if query_mode in {
                    "AGGREGATION", "LIST", "INFERENTIAL", "OPEN_DOMAIN_INFER"
                } else 1
                with_neighbors = {node.node_id: (node, score) for node, score in retrieved}
                for seed, seed_score in retrieved[:30]:
                    position = sequence_positions.get(seed.node_id)
                    if position is None:
                        continue
                    for distance in range(1, neighbor_radius + 1):
                        for neighbor_index in (position - distance, position + distance):
                            if not 0 <= neighbor_index < len(all_nodes):
                                continue
                            neighbor = all_nodes[neighbor_index]
                            neighbor_score = seed_score * (0.94 if distance == 1 else 0.86)
                            existing = with_neighbors.get(neighbor.node_id)
                            if existing is None or neighbor_score > existing[1]:
                                with_neighbors[neighbor.node_id] = (neighbor, neighbor_score)
                retrieved = sorted(with_neighbors.values(), key=lambda item: item[1], reverse=True)[:TOP_K]

                expanded_results = []
                seen_ids = set()
                for node, charge in retrieved:
                    if node.node_id not in seen_ids:
                        expanded_results.append((node, charge))
                        seen_ids.add(node.node_id)

                    linked_ids = dialogue_linker.get_linked_messages(node.node_id)
                    for linked_id in linked_ids:
                        if linked_id not in seen_ids:
                            linked_node = await storage.get_node(linked_id)
                            if linked_node:
                                linked_charge = charge * 0.85
                                expanded_results.append((linked_node, linked_charge))
                                seen_ids.add(linked_id)

                expanded_results.sort(key=lambda x: x[1], reverse=True)
                retrieved = expanded_results

                # End pure retrieval timing (tesseract + dialogue linking only)
                t_retrieval = (time.perf_counter() - t_retrieval_start) * 1000
                TIMING_DATA["t_retrieval"].append(t_retrieval)

                all_memories_text = [
                    {
                        "text": node.content,
                        "speaker": node.metadata.get("speaker", ""),
                        "dia_id": node.metadata.get("dia_id", ""),
                    }
                    for node, charge in retrieved[:50]
                ]

                recall_10, rank_10 = check_evidence_recall(evidence_ids, all_memories_text, top_k=10)
                RETRIEVAL_METRICS[category]["recall_at_10"].append(1 if recall_10 else 0)

                if recall_10:
                    recall_50, rank_50 = True, rank_10
                else:
                    recall_50, rank_50 = check_evidence_recall(evidence_ids, all_memories_text, top_k=50)
                RETRIEVAL_METRICS[category]["recall_at_50"].append(1 if recall_50 else 0)

                oracle_rank = rank_50 if recall_50 else 0
                RETRIEVAL_METRICS[category]["oracle_rank"].append(oracle_rank)
                RETRIEVAL_METRICS[category]["total_candidates"].append(len(retrieved))

                # SPEAKER PROFILES: For single_hop, check profile FIRST before retrieval
                profile_answer = None
                used_profile = False
                target_speakers = explicit_targets
                if query_mode == "STRICT" and not list_question and len(target_speakers) == 1:
                    profile_result = await speaker_profiler.query_single_hop(http, question)
                    if profile_result['found'] and profile_result['confidence'] >= 0.8:
                        profile_answer = profile_result['answer']
                        used_profile = True
                        print(f"  [PROFILE] Using profile answer (confidence: {profile_result['confidence']:.0%})")

                # Determine how many memories we need based on query type
                # Lists and multi-step inferences need broader evidence coverage.
                if query_mode in {"AGGREGATION", "LIST"}:
                    num_memories_needed = 48
                elif query_mode in {"TEMPORAL", "INFERENTIAL", "OPEN_DOMAIN_INFER", "ADVERSARIAL"}:
                    num_memories_needed = 30
                else:
                    num_memories_needed = 24

                # Reranking timing (separate - uses Phi 3.5 SLM)
                t_rerank_start = time.perf_counter()
                if USE_SLM_RERANKER:
                    reranked = await rerank_candidates_parallel(
                        question,
                        retrieved[:TOP_K],
                        limit=num_memories_needed,
                        llm_model=RERANKER_MODEL,
                        max_candidates=TOP_K,
                    )
                else:
                    reranked = retrieved[:num_memories_needed]
                t_rerank = (time.perf_counter() - t_rerank_start) * 1000
                TIMING_DATA["t_rerank"].append(t_rerank)

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
                        context_parts.append(
                            f"[SPEAKER={speaker}] [MESSAGE_DATETIME={dt}] {original_content}"
                        )


                    retrieved_memories.append({
                        "speaker": speaker,
                        "text": original_content[:300],
                        "datetime": dt,
                        "dia_id": node.metadata.get("dia_id", ""),
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

                if query_mode == "OPEN_DOMAIN_WORLD":
                    candidate_memories = []
                    context = ""

                extracted_answer = None
                if query_mode == "TEMPORAL":
                    # Duration arithmetic is deterministic. Event-date selection
                    # is not: choosing the first resolved date caused correct
                    # evidence to be replaced by a nearby event's timestamp.
                    duration_direct = extract_duration_answer(question, candidate_memories)
                    if duration_direct:
                        extracted_answer = duration_direct
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
                        http, question, context, mode=query_mode, config=ANSWERING_CONFIG
                    )
                    t_answer = (time.perf_counter() - t_answer_start) * 1000
                TIMING_DATA["t_answer"].append(t_answer)

                # End-to-end timing (retrieval + rerank + answer generation)
                t_e2e = (time.perf_counter() - t_e2e_start) * 1000
                TIMING_DATA["t_e2e"].append(t_e2e)

                # Judge only after generation; gold never enters retrieval.
                correct = await judge_answer(http, question, generated, gold)

                stats[category]["total"] += 1
                if correct:
                    stats[category]["correct"] += 1

                result = {
                    "id": len(results) + 1,
                    "conversation": conv_idx + 1,
                    "category": category,
                    "inference_mode": query_mode,
                    "question": question,
                    "generated_answer": generated,
                    "gold_answer": gold,
                    "correct": correct,
                    "used_speaker_profile": used_profile,  # Track if profile was used
                    "evidence_recall_at_10": recall_10,
                    "evidence_recall_at_50": recall_50,
                    "evidence_rank": oracle_rank,
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
    judge_name = JUDGE_MODEL
    print(f"BENCHMARK COMPLETE (Answer: {ANSWER_MODEL}, Judge: {judge_name})")
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
    print_latency_stats(f"Answer Generation ({ANSWER_MODEL})", TIMING_DATA.get("t_answer", []))
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

    print(f"\n{'='*60}")
    print(f"RETRIEVAL METRICS")
    print(f"{'='*60}")
    print(f"{'Category':<15} {'Recall@10':<12} {'Recall@50':<12} {'RerankGain':<12}")
    print(f"{'-'*60}")

    retrieval_summary = compute_retrieval_summary(RETRIEVAL_METRICS)
    for cat in CATEGORIES.values():
        if cat in retrieval_summary:
            m = retrieval_summary[cat]
            print(f"{cat:<15} {m['recall_at_10']:>8.1f}%    {m['recall_at_50']:>8.1f}%    {m['rerank_gain']:>+8.1f}%")

    print(f"\nResults saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
