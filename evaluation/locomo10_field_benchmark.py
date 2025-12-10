"""
LoCoMo Field-Based Benchmark v1.0
=================================
FIELD-BASED RETRIEVAL: The Brain's True Mechanism

THE FUNDAMENTAL INSIGHT:
========================
The brain doesn't traverse edges - it activates FIELDS.

When you think "Caroline + Paris + When":
- "Caroline" wave radiates to ALL neurons encoding Caroline
- "Paris" wave radiates to ALL neurons encoding Paris
- "When" wave radiates to ALL neurons with temporal markers
- The ANSWER emerges where ALL waves constructively interfere

This benchmark tests the field-based approach vs graph traversal.

Architecture:
- Field activation: EVERY node receives charge from query
- Charge components:
  1. Semantic similarity (embedding cosine)
  2. Entity binding (shared entities)
  3. Speaker binding (speaker IS entity)
  4. Keyword matching (exact terms)
- Answer emerges from constructive interference
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import requests
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import Neural Graph System (for storage)
from memmachine.neural_graph import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
    NeuralNode,
    NodeLayer,
)
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.field_retriever import FieldRetriever, FieldConfig, HybridFieldRetriever

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 3
CONTEXT_LIMIT = 15000
TOP_K_RETRIEVAL = 40

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def extract_messages(item) -> list[dict]:
    """Extract messages from conversation item (handles LoCoMo format)."""
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1

    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if session_key not in conversation:
            break

        session_time = conversation.get(datetime_key, "Unknown")
        session_messages = conversation[session_key]

        for msg_idx, msg in enumerate(session_messages):
            if isinstance(msg, dict) and 'text' in msg:
                messages.append({
                    "speaker": msg.get("speaker", "Unknown"),
                    "text": msg.get("text", ""),
                    "session_time": session_time,
                    "session": session_idx,
                    "msg_idx": msg_idx,
                })

        session_idx += 1

    return messages


def parse_session_datetime(session_time: str) -> datetime | None:
    """Parse LoCoMo session datetime format."""
    if not session_time or session_time == "Unknown":
        return None

    # Primary format: "X:XX am/pm on DD Month, YYYY"
    date_match = re.search(r"on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})", session_time)
    if date_match:
        day = int(date_match.group(1))
        month_str = date_match.group(2).lower()
        year = int(date_match.group(3))
        month = MONTH_NAMES.get(month_str, 1)

        time_match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", session_time, re.IGNORECASE)
        hour, minute = 12, 0
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            if time_match.group(3).lower() == "pm" and hour != 12:
                hour += 12
            elif time_match.group(3).lower() == "am" and hour == 12:
                hour = 0

        try:
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def extract_questions(item) -> list[dict]:
    """Extract questions from QA data."""
    questions = []
    qa = item.get("qa", [])
    for q in qa:
        questions.append({
            "question": q.get("question", ""),
            "answer": q.get("answer", ""),
            "category": q.get("category", 1),
        })
    return questions


def _save_incremental(all_results: list, results_dir: Path):
    """Save results incrementally after each batch."""
    if not all_results:
        return

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])

    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    answer_times = [r.get("time_seconds", 0) for r in all_results if r.get("time_seconds")]

    avg_retrieval_ms = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0
    avg_answer_s = sum(answer_times) / len(answer_times) if answer_times else 0

    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    incremental_file = results_dir / "field_incremental_results.json"

    with open(incremental_file, 'w') as f:
        json.dump({
            "status": "in_progress",
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "avg_retrieval_ms": round(avg_retrieval_ms, 2),
            "avg_answer_seconds": round(avg_answer_s, 2),
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "results": all_results,
        }, f, indent=2)


# =============================================================================
# FIELD-BASED RETRIEVER WRAPPER
# =============================================================================

class FieldBasedRetriever:
    """
    Field-based retriever wrapper for benchmark.

    Key difference from graph traversal:
    - Graph: Start at seed nodes, follow edges[:8], miss distant matches
    - Field: ALL nodes receive charge, answer emerges from resonance
    """

    def __init__(self, storage: InMemoryNeuralGraphStorage):
        self._storage = storage
        self._field_config = FieldConfig(
            use_wave_resonance=True,
            wave_resonance_weight=0.30,
            use_entity_binding=True,
            entity_binding_weight=0.50,  # CRITICAL for entity questions
            use_speaker_binding=True,
            speaker_binding_weight=0.55,  # Speaker IS entity = strong signal
            semantic_weight=0.45,
            keyword_weight=0.35,
            min_charge=0.15,
            max_results=TOP_K_RETRIEVAL,
        )
        self._field_retriever = HybridFieldRetriever(storage, self._field_config)

        # Node wave amplitudes registry
        self._wave_amplitudes: dict[str, dict[str, float]] = {}

    def register_node_waves(self, node_id: str, content: str):
        """Register wave amplitudes for a node based on content analysis."""
        self._wave_amplitudes[node_id] = self._compute_wave_amplitudes(content)

    def _compute_wave_amplitudes(self, content: str) -> dict[str, float]:
        """Compute wave amplitudes from content (dimension detection)."""
        content_lower = content.lower()

        amplitudes = {
            "temporal": 0.0,
            "entity": 0.0,
            "relational": 0.0,
            "action": 0.0,
            "state": 0.0,
            "spatial": 0.0,
            "causal": 0.0,
            "emotional": 0.0,
            "quantitative": 0.0,
        }

        # Temporal markers
        temporal_markers = [
            'when', 'yesterday', 'today', 'tomorrow', 'last week', 'next month',
            'january', 'february', 'march', 'april', 'may', 'june', 'july',
            'august', 'september', 'october', 'november', 'december',
            'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
            '2020', '2021', '2022', '2023', '2024', 'ago', 'later', 'before', 'after'
        ]
        amplitudes["temporal"] = min(1.0, sum(0.15 for m in temporal_markers if m in content_lower))

        # Entity markers (capitalized words)
        entity_count = len(re.findall(r'\b[A-Z][a-z]+\b', content))
        amplitudes["entity"] = min(1.0, entity_count * 0.1)

        # Action markers (verbs)
        action_markers = ['go', 'went', 'visit', 'play', 'work', 'eat', 'drink', 'run', 'walk', 'see', 'meet', 'call', 'buy', 'sell', 'make', 'do', 'say', 'said', 'told', 'ask', 'help']
        amplitudes["action"] = min(1.0, sum(0.12 for m in action_markers if m in content_lower))

        # Spatial markers
        spatial_markers = ['in', 'at', 'on', 'near', 'by', 'between', 'city', 'town', 'country', 'place', 'paris', 'london', 'tokyo', 'york', 'home', 'office', 'restaurant', 'hotel']
        amplitudes["spatial"] = min(1.0, sum(0.1 for m in spatial_markers if m in content_lower))

        # Relational markers
        relational_markers = ['friend', 'family', 'mother', 'father', 'sister', 'brother', 'wife', 'husband', 'colleague', 'boss', 'partner', 'knows', 'met', 'works with']
        amplitudes["relational"] = min(1.0, sum(0.12 for m in relational_markers if m in content_lower))

        # Emotional markers
        emotional_markers = ['happy', 'sad', 'excited', 'worried', 'love', 'hate', 'like', 'enjoy', 'fear', 'hope', 'angry', 'surprised']
        amplitudes["emotional"] = min(1.0, sum(0.15 for m in emotional_markers if m in content_lower))

        # Quantitative markers
        quant_markers = ['how many', 'how much', 'number', 'count', 'total', 'first', 'second', 'third', 'once', 'twice']
        amplitudes["quantitative"] = min(1.0, sum(0.15 for m in quant_markers if m in content_lower))
        if re.search(r'\b\d+\b', content):
            amplitudes["quantitative"] = min(1.0, amplitudes["quantitative"] + 0.2)

        return amplitudes

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = TOP_K_RETRIEVAL,
    ) -> list[tuple[NeuralNode, float]]:
        """Retrieve using field-based activation."""
        # Compute query wave amplitudes
        query_waves = self._compute_wave_amplitudes(query_text)

        # Use field retriever
        results = await self._field_retriever.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            session_key=session_key,
            query_wave_amplitudes=query_waves,
            limit=limit,
        )

        return results


# =============================================================================
# LLM CALLS
# =============================================================================

async def get_embedding_async(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Get embedding for text."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except Exception:
        return []


async def generate_answer_async(session: aiohttp.ClientSession, prompt: str, system_msg: str = "") -> str:
    """Generate answer using Ollama."""
    try:
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})

        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 8192}
            },
            timeout=aiohttp.ClientTimeout(total=180)
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


def evaluate_llm_judge_sync(question: str, gold_answer: str, generated_answer: str) -> int:
    """Evaluate using LLM judge."""
    JUDGE_PROMPT = """You are a STRICT evaluator. Label the answer as 'CORRECT' or 'WRONG'.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

STRICT RULES:
1. If gold answer has MULTIPLE items (e.g., "A, B, C"), the generated answer must mention ALL of them to be CORRECT. Missing ANY item = WRONG.
2. If gold answer is a specific date/time, the generated answer must match that date. "Not mentioned" when there IS a gold answer = WRONG.
3. If gold answer says "Yes" or "No", the generated answer must convey the same meaning. Saying "Not mentioned" when the gold answer exists = WRONG.
4. For dates: different formats are OK (e.g., "May 7" vs "7 May 2023" vs "2023-05-07"), but the date must be CORRECT.
5. Partial answers are WRONG. If gold says "Running, pottery" and answer only says "Running" = WRONG.
6. "Not mentioned in memories" when the gold answer exists = WRONG.

Return JSON: {{"label": "CORRECT"}} or {{"label": "WRONG"}}"""

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": JUDGE_MODEL,
                "messages": [{"role": "user", "content": JUDGE_PROMPT.format(
                    question=question,
                    gold_answer=gold_answer,
                    generated_answer=generated_answer,
                )}],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0}
            },
            timeout=180
        )
        result = json.loads(response.json()["message"]["content"])
        return 1 if result.get("label") == "CORRECT" else 0
    except Exception as e:
        print(f"    Judge error: {e}", flush=True)
        return 0


async def judge_answer_async(session: aiohttp.ClientSession, question: str, gold_answer: str, generated_answer: str) -> dict:
    """Async wrapper for LLM judge."""
    loop = asyncio.get_event_loop()
    score = await loop.run_in_executor(None, evaluate_llm_judge_sync, question, gold_answer, generated_answer)
    return {"binary_correct": score}


# =============================================================================
# PROMPTS
# =============================================================================

SYSTEM_MESSAGE = """You are a memory retrieval system. Your task: Find and synthesize information from timestamped memories.
Answer based ONLY on what's in the memories. If not stated, say "Not mentioned in memories"."""

ANSWER_PROMPT = """You are a memory retrieval system. Your task is to find and synthesize information from memories.

REASONING PROCESS (do this mentally):
1. WHO is the question about? Identify the person/entity.
2. WHAT is being asked? Identify the topic/activity/relationship.
3. SCAN all memories for this person + topic combination.
4. CONNECT related memories - they may describe the same thing differently.
5. SYNTHESIZE a complete answer from ALL relevant memories.

CRITICAL:
- For "how does X do Y" questions, find ALL instances of X doing Y
- For preferences/habits, combine multiple examples
- For factual questions, cite the specific memory
- If truly not mentioned, say "Not mentioned in memories"

## MEMORIES (sorted by relevance):
{context}

## QUESTION:
{question}

## ANSWER (be concise and specific):"""


# =============================================================================
# QUESTION PROCESSING
# =============================================================================

async def process_question_field(
    question_data: dict,
    session: aiohttp.ClientSession,
    retriever: FieldBasedRetriever,
    session_key: str,
) -> dict:
    """Process a single question using field-based retrieval."""
    question = question_data["question"]
    gold_answer = question_data["answer"]
    category = CATEGORIES.get(question_data.get("category", 1), "unknown")
    q_index = question_data.get("_index", 0)

    start_time = time.time()

    # Get query embedding
    query_embedding = await get_embedding_async(session, question)
    if not query_embedding:
        return {
            "question": question,
            "gold_answer": gold_answer,
            "generated_answer": "Error: embedding failed",
            "correct": False,
            "category": category,
            "time_seconds": 0,
        }

    # Field-based retrieval
    retrieval_start = time.time()
    results = await retriever.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
    )
    retrieval_ms = (time.time() - retrieval_start) * 1000

    # Build context from retrieved memories
    context_parts = []
    total_len = 0

    for node, score in results:
        # Format memory
        timestamp_str = ""
        if node.created_at:
            ts = node.created_at if isinstance(node.created_at, datetime) else datetime.fromisoformat(node.created_at.replace('Z', '+00:00'))
            timestamp_str = ts.strftime("%Y-%m-%d %H:%M")

        speaker = ""
        if node.metadata:
            speaker = node.metadata.get("producer_id", "") or node.metadata.get("speaker", "")

        if speaker:
            entry = f"[{timestamp_str}] {speaker}: {node.content} (score: {score:.3f})"
        else:
            entry = f"[{timestamp_str}] {node.content} (score: {score:.3f})"

        if total_len + len(entry) > CONTEXT_LIMIT:
            break

        context_parts.append(entry)
        total_len += len(entry)

    context = "\n".join(context_parts)

    # Generate answer
    prompt = ANSWER_PROMPT.format(context=context, question=question)
    generated_answer = await generate_answer_async(session, prompt, SYSTEM_MESSAGE)

    # Judge answer
    judge_result = await judge_answer_async(session, question, gold_answer, generated_answer)
    is_correct = judge_result.get("binary_correct", 0) == 1

    elapsed = time.time() - start_time
    status = "CORRECT" if is_correct else "WRONG"

    # Get average charge for logging
    avg_charge = sum(s for _, s in results[:5]) / min(5, len(results)) if results else 0

    print(f"    [{q_index}] {category}: {status} (charge: {avg_charge:.3f})", flush=True)

    return {
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated_answer,
        "correct": is_correct,
        "category": category,
        "time_seconds": round(elapsed, 2),
        "retrieval_time_ms": round(retrieval_ms, 2),
        "nodes_retrieved": len(results),
        "avg_charge": round(avg_charge, 3),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_field_benchmark(max_conversations: int = 10):
    """Run the field-based benchmark."""
    print("=" * 70)
    print("LoCoMo Field-Based Benchmark v1.0")
    print("FIELD ACTIVATION: ALL nodes receive charge from query")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Retrieval: Field-based (entity_binding=0.50, speaker_binding=0.55)")
    print(f"Conversations: {max_conversations}")
    print("=" * 70)

    # Load LoCoMo dataset
    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    all_results = []
    conversations = data[:max_conversations]

    print(f"\nProcessing {len(conversations)} conversations...")

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, conversation in enumerate(conversations):
            # Get speaker names from conversation structure
            conv_data = conversation.get("conversation", {})
            speaker_a = conv_data.get("speaker_a", "Unknown")
            speaker_b = conv_data.get("speaker_b", "Unknown")
            conv_name = f"{speaker_a} & {speaker_b}"
            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {conv_name}")

            # Create fresh storage and retriever for each conversation
            storage = InMemoryNeuralGraphStorage()
            retriever = FieldBasedRetriever(storage)
            session_key = f"conv_{conv_idx}"

            # Extract and ingest all messages using the proper format
            messages = extract_messages(conversation)
            print(f"  Ingesting {len(messages)} messages...")

            for msg_idx, msg in enumerate(messages):
                # Get embedding
                content = msg.get("text", "")
                if not content:
                    continue

                embedding = await get_embedding_async(http_session, content)

                if not embedding:
                    continue

                # Parse timestamp from session_time
                ts = parse_session_datetime(msg.get("session_time", ""))
                if ts is None:
                    ts = datetime.now(timezone.utc)

                speaker = msg.get("speaker", "Unknown")

                # Create node
                node = NeuralNode(
                    node_id=f"msg_{conv_idx}_{msg_idx}",
                    session_key=session_key,
                    content=content,
                    layer=NodeLayer.MESSAGE,
                    embedding=embedding,
                    created_at=ts,
                    metadata={
                        "producer_id": speaker,
                        "speaker": speaker,
                        "message_index": msg_idx,
                        "session": msg.get("session", 0),
                    },
                    entity_ids=[],  # Could extract entities here
                    wave_amplitudes=retriever._compute_wave_amplitudes(content),
                )

                # Save to storage (node already contains session_key)
                await storage.save_node(node)

            print(f"  Ingested {len(messages)} nodes")

            # Extract and process questions using the proper format
            questions = extract_questions(conversation)
            print(f"  Processing {len(questions)} questions...")

            # Add index to questions for logging
            for i, q in enumerate(questions):
                q["_index"] = i + 1

            # Process in batches
            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch = questions[batch_start:batch_start + CONCURRENT_QUESTIONS]

                tasks = [
                    process_question_field(q, http_session, retriever, session_key)
                    for q in batch
                ]

                batch_results = await asyncio.gather(*tasks)
                all_results.extend(batch_results)

                # Save incremental results
                _save_incremental(all_results, results_dir)

    # Final summary
    print("\n" + "=" * 70)
    print("FIELD-BASED BENCHMARK RESULTS")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])

    print(f"\nOverall: {correct}/{total} = {correct/total*100:.1f}%")

    # Category breakdown
    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    print("\nBy Category:")
    for cat in ["temporal", "single_hop", "multi_hop", "adversarial", "open_domain"]:
        if cat in category_results:
            cat_correct = sum(1 for r in category_results[cat] if r["correct"])
            cat_total = len(category_results[cat])
            print(f"  {cat}: {cat_correct}/{cat_total} = {cat_correct/cat_total*100:.1f}%")

    # Latency stats
    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    avg_retrieval = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0
    print(f"\nAvg retrieval time: {avg_retrieval:.1f}ms")

    # Save final results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_file = results_dir / f"locomo10_FIELD_{timestamp}.json"

    with open(final_file, 'w') as f:
        json.dump({
            "benchmark": "LoCoMo Field-Based v1.0",
            "model": OLLAMA_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "retrieval_method": "field_activation",
            "config": {
                "entity_binding_weight": 0.50,
                "speaker_binding_weight": 0.55,
                "semantic_weight": 0.45,
                "keyword_weight": 0.35,
            },
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "category_results": {
                cat: {
                    "correct": sum(1 for r in results if r["correct"]),
                    "total": len(results),
                    "accuracy": sum(1 for r in results if r["correct"]) / len(results)
                }
                for cat, results in category_results.items()
            },
            "avg_retrieval_ms": round(avg_retrieval, 2),
            "results": all_results,
        }, f, indent=2)

    print(f"\nResults saved to: {final_file}")
    return correct / total if total > 0 else 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_field_benchmark(args.max_conversations))
