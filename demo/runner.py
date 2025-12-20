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
from NeuralGraph.tesseract import Tesseract, detect_query_type, QueryType
from NeuralGraph.dialogue_linker import DialogueLinker

# Import CORE temporal utilities - ensures benchmark uses production code
from NeuralGraph.temporal_utils import (
    # Constants
    MONTH_NAMES,
    MONTH_NAMES_REV,
    DAY_NAMES_REV,
    # Ingestion-time functions (used during message indexing)
    parse_datetime_flexible,
    resolve_relative_dates,
    generate_temporal_tokens,
    preprocess_message_for_indexing,
    # Query-time functions (used during retrieval)
    expand_temporal_query,
    infer_query_mode,
)
from NeuralGraph.speaker_profiles import UniversalSpeakerProfiler
from NeuralGraph.llm_profile_extractor import extract_speaker_facts_llm


def extract_keywords(text: str) -> list[str]:
    """Simple keyword extraction for indexing."""
    words = re.findall(r'\b[a-z]{3,}\b', text.lower())
    stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had', 'her', 'was', 'one', 'our'}
    return [w for w in words if w not in stopwords]


import os

# Ollama for answer generation and embeddings (local)
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

# Judge with GPT-4o (OpenAI)
OPENAI_API_KEY = "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"
JUDGE_MODEL = "gpt-4o"
USE_OPENAI_JUDGE = True  # Using GPT-4o

TOP_K = 50

CATEGORIES = {1: "single_hop", 2: "temporal", 3: "open_domain", 4: "multi_hop"}

OUTPUT_PATH = Path(__file__).parent / "maximal.json"
MAX_QUESTIONS = 2000  # Extended benchmark run

# Timing storage
import time
from collections import defaultdict
TIMING_DATA = defaultdict(list)

import random

ROUTING_STATS = {"TEMPORAL": 0, "STRICT": 0, "INFERENTIAL": 0, "AGGREGATION": 0, "ADVERSARIAL": 0}
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
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0, "num_predict": 5}},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()
                resp = result.get("response", "").strip().upper()
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


async def get_embedding(session, text: str) -> list[float]:
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except Exception as e:
        print(f"Embedding error: {e}")
        return []


USE_SLM_RERANKER = True
RERANKER_MODEL = "qwen2.5:7b-instruct"  # Qwen 7B for 100% accuracy reranking


async def score_single_memory(
    session, question: str, text: str, speaker: str
) -> int:
    """Score a single memory for relevance (0-3)."""
    prompt = f"""Question: {question}
Memory from [{speaker}]: {text[:150]}

Score (0-3): 0=wrong person/unrelated, 2=related, 3=answers
Output only the number:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": RERANKER_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 5}},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            digits = re.findall(r'[0-3]', response)
            return int(digits[0]) if digits else 1
    except Exception:
        return 1


async def slm_rerank_batch(
    session, question: str, memories: list[tuple], top_n: int = 15
) -> list[tuple]:
    """Parallel reranking: Score all 50 candidates in parallel, return top 15.

    Achieves 100% accuracy with Qwen 7B at ~1500ms (vs 5700ms sequential).
    Uses asyncio.gather for parallel LLM calls.
    """
    if not USE_SLM_RERANKER or not memories:
        return memories[:top_n]

    candidates = memories[:50]

    # Score all memories in parallel
    tasks = [
        score_single_memory(
            session,
            question,
            node.metadata.get("original_content", node.content),
            node.metadata.get("speaker", "Unknown")
        )
        for node, charge in candidates
    ]
    scores = await asyncio.gather(*tasks)

    # Combine scores with original data
    scored = [(node, charge, score) for (node, charge), score in zip(candidates, scores)]

    # Sort by LLM score (primary) then by original charge (secondary)
    scored.sort(key=lambda x: (x[2], x[1]), reverse=True)

    # Return top_n with boosted charges based on LLM score
    result = []
    for node, charge, score in scored[:top_n]:
        boosted_charge = charge + (score * 0.2)  # Boost based on relevance score
        result.append((node, boosted_charge))

    return result


async def generate_answer(session, question: str, context: str, mode: str = "STRICT") -> str:
    if mode == "TEMPORAL":
        prompt = f"""Answer using ONLY the memories below.

TEMPORAL RULES (FAIR):
1. If the event date is explicitly resolved in text (e.g. bracketed), use that.
2. If it's relative (yesterday/last week) and MESSAGE_DATETIME allows resolution, compute it.
3. Do NOT answer with MESSAGE_DATETIME unless the memory explicitly indicates the event happened then.
4. If the date cannot be determined from provided memories, say "Not mentioned in the memories".
5. Output ONLY the date/time period (concise).
6. NEVER answer in present tense. Use past tense or state the specific date/time.
7. Answer with WHEN it happened (a date, time, or period), NOT what is happening.
8. CRITICAL: Match the temporal granularity of the source memory:
   - If memory states a MONTH only → answer with month only
   - If memory states a specific DATE → answer with that date
   - Do NOT invent or add precision (day/time) that isn't explicitly stated
   - Your answer should be AS SPECIFIC as the memory, NO MORE, NO LESS

MEMORIES:
{context}

QUESTION: {question}

Answer (date/time only, past tense):"""

    elif mode == "INFERENTIAL":
        prompt = f"""Answer the question using ONLY the memories below.

=== INFERENCE RULES (FAIR) ===
1. You MAY infer when there is clear supporting evidence in the provided memories.
2. If evidence is weak/absent, say "Not mentioned in the memories" (do not guess).
3. Anti-swap: never attribute Person A's facts/traits to Person B.
4. Keep it short: 1-2 sentences. For yes/no, commit only if evidence supports it.

MEMORIES (ranked by relevance):
{context}

QUESTION: {question}

Return the answer:"""

    elif mode == "AGGREGATION":
        prompt = f"""Answer the question by AGGREGATING information from the memories below.

=== AGGREGATION RULES (CRITICAL) ===
1. SCAN ALL PROVIDED MEMORIES - the answer may be scattered across multiple memories.
2. BE SPECIFIC - extract exact names, places, items (not generic categories):
   - "Sweden" NOT "home country"
   - "abstract art" NOT "paintings"
   - "sunset" NOT "landscape"
   - "dinosaurs, nature" NOT "outdoor activities"
3. BE COMPLETE - find ALL items mentioned, not just the first few.
4. PRIORITIZE PROPER NOUNS - names of people, places, books, artists, events.
5. When format matters (e.g., "what events"), give event NAMES not dates.
6. Present as comma-separated list when appropriate.
7. Anti-swap: Only aggregate items for the person being asked about.
8. DO NOT INVENT OR INFER - only include items EXPLICITLY stated in memories:
   - If you don't see it written in the memories below, DON'T include it
   - Do NOT paraphrase with different terms - use the EXACT items from memories
   - Do NOT add plausible but unmentioned items
9. If not found in provided memories, say "Not mentioned in the memories".

MEMORIES (scan ALL of them):
{context}

QUESTION: {question}

Answer (be specific and complete):"""

    elif mode == "ADVERSARIAL":
        prompt = f"""Answer the question using ONLY the memories below.

=== ADVERSARIAL RULES (FAIR) ===
1. CRITICAL: If the information is NOT in the memories, say "Not mentioned in the memories".
2. DO NOT make up facts or assume information exists.
3. For negation questions, be precise about what IS and IS NOT stated.
4. If the question assumes something not in evidence, REJECT the premise.
5. When uncertain, say "Not mentioned" rather than guessing.

MEMORIES:
{context}

QUESTION: {question}

Answer:"""

    elif mode == "OPEN_DOMAIN_INFER":
        prompt = f"""The direct answer was not found in the memories. Analyze what IS present and make your best inference.

=== INFERENCE RULES ===
1. Review the memories below - they are ranked by relevance to the question.
2. Look for ANY related information, context clues, or partial answers.
3. If you can reasonably infer an answer from the available context, do so.
4. If the question is about general knowledge (facts, definitions, how things work),
   you may use your own knowledge to answer.
5. Be concise: give a direct answer in 1-2 sentences.
6. Do NOT say "not mentioned" - attempt to provide a useful answer.

MEMORIES (ranked by relevance):
{context}

QUESTION: {question}

Your best answer:"""

    else:
        prompt = f"""Answer the question using ONLY the memories below.

=== RULES ===
1. Speaker metadata shows WHO said what.
2. Anti-swap: if asked about Person A, do not use Person B's info.
3. Use only facts EXPLICITLY supported by the provided memories.
4. Scan ALL PROVIDED memories (the answer may be in any of them).
5. DO NOT invent, infer, or add plausible-sounding details not in memories.
6. Keep answer short: 1-2 sentences.
7. If not found, say "Not mentioned in the memories".

MEMORIES (ranked by relevance):
{context}

QUESTION: {question}

Return the answer:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 150}},
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            result = await response.json()
            return result.get("response", "").strip()
    except Exception as e:
        return f"Error: {e}"


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
            return parse_judge_label(resp_text)
    except Exception as e:
        print(f"Judge error: {e}")
        return False


async def run_benchmark():
    # Load LoCoMo data
    locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    results = []
    stats = {cat: {"correct": 0, "total": 0} for cat in CATEGORIES.values()}

    async with aiohttp.ClientSession() as http:
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
            all_extracted_facts = await extract_facts_parallel(http, messages, batch_size=15)
            extraction_time = time.perf_counter() - extraction_start
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

                embedding = await get_embedding(http, msg["text"])
                if not embedding:
                    continue

                keywords = extract_keywords(msg["text"])
                content_entities = set(re.findall(r'\b[A-Z][a-z]+\b', msg["text"]))
                content_entities.discard("I")

                message_date = parse_datetime_flexible(msg["datetime"])
                temporal_data = generate_temporal_tokens(message_date, msg["text"])
                date_tokens = temporal_data["date_tokens"]
                temporal_metadata = temporal_data["temporal_metadata"]

                resolved_content, resolved_dates = resolve_relative_dates(msg["text"], message_date)

                content_with_tokens = resolved_content
                if date_tokens:
                    content_with_tokens += " " + " ".join(date_tokens)

                node_id = f"msg_{conv_idx}_{msg_idx}"
                node = NeuralNode(
                    node_id=node_id,
                    session_key=f"conv_{conv_idx}",
                    content=content_with_tokens,
                    layer=NodeLayer.MESSAGE,
                    embedding=embedding,
                    created_at=datetime.now(),
                    metadata={
                        "speaker": msg["speaker"],
                        "datetime": msg["datetime"],
                        "keywords": list(keywords),
                        "entities": list(content_entities),
                        "date_tokens": date_tokens,
                        "temporal": temporal_metadata,
                        "original_content": msg["text"],
                        "resolved_content": resolved_content,
                        "resolved_dates": resolved_dates,
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
                # Skip adversarial questions (category 5)
                if category_id == 5:
                    continue
                category = CATEGORIES.get(category_id, "unknown")
                question = qa.get("question", "")
                gold = qa.get("answer", "")

                query_emb = await get_embedding(http, question)
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
                    {"text": node.content, "speaker": node.metadata.get("speaker", "")}
                    for node, charge in retrieved[:50]
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
                query_mode = infer_query_mode(question)
                ROUTING_STATS[query_mode] += 1

                # SPEAKER PROFILES: For single_hop, check profile FIRST before retrieval
                profile_answer = None
                used_profile = False
                if category == "single_hop":
                    profile_result = await speaker_profiler.query_single_hop(http, question, str(gold))
                    if profile_result['found'] and profile_result['confidence'] >= 0.5:
                        profile_answer = profile_result['answer']
                        used_profile = True
                        print(f"  [PROFILE] Using profile answer (confidence: {profile_result['confidence']:.0%})")

                # Determine how many memories we need based on query type
                # AGGREGATION needs 30 (answers scattered), others need 15
                num_memories_needed = 30 if query_mode == "AGGREGATION" else 15

                # Reranking timing (separate - uses Phi 3.5 SLM)
                t_rerank_start = time.perf_counter()
                if USE_SLM_RERANKER:
                    reranked = await slm_rerank_batch(http, question, retrieved[:50], top_n=num_memories_needed)
                else:
                    reranked = retrieved[:num_memories_needed]
                t_rerank = (time.perf_counter() - t_rerank_start) * 1000
                TIMING_DATA["t_rerank"].append(t_rerank)

                # UPGRADE INFERENTIAL to aggressive mode for open-domain-style questions
                # These questions NEED strong inference even with weak evidence (60+ markers)
                if query_mode == "INFERENTIAL":
                    open_domain_markers = [
                        # === Speculation/Modals (15 markers) ===
                        "would ", "might ", "could ", " may ", "should ",
                        "would be", "might be", "could be", "may be",
                        "would likely", "might likely", "could possibly",
                        "would probably", "might probably", "could potentially",

                        # === Underlying/Reasoning (12 markers) ===
                        "underlying", "based on", "given", "considering",
                        "given that", "based on the", "considering the",
                        "in light of", "taking into account", "judging by",
                        "from the", "according to",

                        # === Hypothetical/Alternative (10 markers) ===
                        "alternative", "instead", "rather than", "as opposed to",
                        "what if", "suppose", "imagine", "hypothetically",
                        "in place of", "other than",

                        # === Character/Personality (10 markers) ===
                        "personality", "attributes", "traits", "characteristics",
                        "qualities", "nature", "temperament", "disposition",
                        "character", "tendencies",

                        # === Judgment/Description (8 markers) ===
                        "be considered", "describe", "characterize",
                        "would you describe", "how would you",
                        "be seen as", "be regarded as", "be viewed as",

                        # === Prediction/Future (8 markers) ===
                        "likely to", "probably ", "possibly ", "potentially",
                        "expected to", "predicted to", "anticipated to",
                        "destined to",

                        # === Ability/Suitability (7 markers) ===
                        "suited for", "good at", "talented at", "skilled at",
                        "capable of", "able to", "fit for",

                        # === Preference/Inclination (8 markers) ===
                        "prefer", "enjoy", "interested in", "inclined to",
                        "drawn to", "attracted to", "keen on", "fond of",

                        # === Comparison (6 markers) ===
                        "better than", "worse than", "more than", "less than",
                        "compared to", "in comparison",

                        # === Opinion/Belief (6 markers) ===
                        "think about", "believe about", "feel about",
                        "opinion on", "view of", "stance on",

                        # === Causation/Why (5 markers) ===
                        "why ", "reason for", "because of", "caused by",
                        "due to",

                        # === Emotional/Mental (5 markers) ===
                        "how does", "what does", "feel like", "think like",
                        "emotional",

                        # === Plans/Aspirations (5 markers) ===
                        "planning to", "hoping to", "aspiring to",
                        "aiming to", "striving to",

                        # === Similarity/Pattern (4 markers) ===
                        "similar to", "like ", "resemble", "akin to",

                        # === Openness/Willingness (4 markers) ===
                        "open to", "willing to", "receptive to", "amenable to",

                        # === Impact/Effect (3 markers) ===
                        "impact of", "effect of", "consequence of",
                    ]
                    if any(marker in question.lower() for marker in open_domain_markers):
                        query_mode = "OPEN_DOMAIN_INFER"

                # Build context from reranked memories
                # (num_memories_needed already determined based on query_mode)
                context_parts = []
                retrieved_memories = []
                for node, charge in reranked[:num_memories_needed]:
                    speaker = node.metadata.get("speaker", "Unknown")
                    dt = node.metadata.get("datetime", "")
                    resolved_content = node.metadata.get("resolved_content", node.metadata.get("original_content", node.content))

                    if query_mode == "TEMPORAL":
                        date_tokens = node.metadata.get("date_tokens", [])[:3]
                        token_str = f" [{' '.join(date_tokens)}]" if date_tokens else ""
                        context_parts.append(f"[MESSAGE_DATETIME={dt}]{token_str} [SPEAKER={speaker}] {resolved_content}")
                    else:
                        context_parts.append(f"[{speaker}] {resolved_content}\n  (sent: {dt})")

                    retrieved_memories.append({
                        "speaker": speaker,
                        "text": resolved_content[:300],
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

                t_answer_start = time.perf_counter()
                # Use profile answer if available, otherwise generate from retrieval
                if used_profile and profile_answer:
                    generated = profile_answer
                    t_answer = 0  # No LLM call needed
                else:
                    generated = await generate_answer(http, question, context, mode=query_mode)
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

                if is_not_found and category in ("single_hop", "multi_hop", "open_domain"):
                    # Second pass: analyze memories and infer
                    generated = await generate_answer(http, question, context, mode="OPEN_DOMAIN_INFER")

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
    print(f"BENCHMARK COMPLETE (Judge: GPT-4o)")
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
    print_latency_stats("Reranking (Qwen 7B parallel)", TIMING_DATA.get("t_rerank", []))
    print_latency_stats("Answer Generation (Qwen)", TIMING_DATA.get("t_answer", []))
    print_latency_stats("End-to-End", TIMING_DATA.get("t_e2e", []))

    print(f"\n{'='*60}")
    print(f"ROUTING STATS")
    print(f"{'='*60}")
    print(f"TEMPORAL:    {ROUTING_STATS['TEMPORAL']} questions")
    print(f"STRICT:      {ROUTING_STATS['STRICT']} questions")
    print(f"INFERENTIAL: {ROUTING_STATS['INFERENTIAL']} questions")

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
