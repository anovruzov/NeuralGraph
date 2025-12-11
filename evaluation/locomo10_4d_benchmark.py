"""
LoCoMo 4D Holographic Benchmark
TARGET: 92%+ Accuracy through 4D Concept Resonance

THE EVOLUTION FROM 3D TO 4D:

3D (Failed at 0-32%):
- Nodes stored at positions
- Query → Search → Find node
- Speaker in metadata, not content
- Linear traversal

4D (Brain-like):
- Memories in CONCEPTUAL space
- Query concepts → Intersection → Memory EMERGES
- Speaker IS a concept (not metadata)
- Pattern completion through resonance

The key insight: "When did Caroline go to LGBTQ support group?"
- "Caroline" = concept (activates all Caroline traces)
- "LGBTQ" = concept
- "support" = concept
- "group" = concept

The memory "Caroline: I went to LGBTQ support group yesterday"
has access_keys = {caroline, lgbtq, support, group, went, yesterday}

Intersection score = 4/4 = 1.0 → THIS IS THE ANSWER
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import requests
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import 4D Holographic Memory
from memmachine.neural_graph.holographic_memory import (
    HolographicRetriever,
    HolographicTrace,
)
from memmachine.neural_graph.data_types import NeuralNode, NodeLayer

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 3
CONTEXT_LIMIT = 15000
TOP_K_RETRIEVAL = 30

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
1. If gold answer has MULTIPLE items, the generated answer must mention ALL of them.
2. If gold answer is a date/time, the generated answer must match.
3. "Not mentioned in memories" when the gold answer exists = WRONG.
4. Partial answers are WRONG.

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
    except Exception:
        return 0


async def judge_answer_async(session: aiohttp.ClientSession, question: str, gold_answer: str, generated_answer: str) -> dict:
    """Async wrapper for LLM judge."""
    loop = asyncio.get_event_loop()
    score = await loop.run_in_executor(None, evaluate_llm_judge_sync, question, gold_answer, generated_answer)
    return {"binary_correct": score}


# =============================================================================
# DATA EXTRACTION
# =============================================================================

def extract_messages(item) -> list[dict]:
    """Extract messages from conversation item."""
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


# =============================================================================
# PROMPTS
# =============================================================================

SYSTEM_MESSAGE = """You are a memory retrieval system using 4D holographic pattern completion.
Your task: Find and synthesize information from memories.
Answer based ONLY on what's in the memories. If not stated, say "Not mentioned in memories"."""

ANSWER_PROMPT = """These memories were retrieved through 4D concept resonance.
The memories share the most concepts with your question.

CRITICAL: The speaker name at the start of each memory tells you WHO said it.
"Caroline: I went to support group" means CAROLINE went to the support group.

Memories:
{context}

Question: {question}

Answer (provide the specific fact requested):"""


# =============================================================================
# PROCESS QUESTION
# =============================================================================

async def process_question_4d(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    retriever: HolographicRetriever,
    reference_date: datetime | None,
) -> dict:
    """Process a question using 4D holographic retrieval."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # Get query embedding for fallback
    query_embedding = await get_embedding_async(http_session, question)

    # 4D HOLOGRAPHIC RETRIEVAL
    retrieval_start = time.time()
    results = await retriever.retrieve(
        query=question,
        limit=TOP_K_RETRIEVAL,
        query_embedding=query_embedding,
    )
    retrieval_time = time.time() - retrieval_start

    # Format context - content already includes speaker!
    context_lines = []
    for trace, score in results:
        timestamp = trace.timestamp.strftime("%Y-%m-%d %H:%M") if trace.timestamp else "Unknown"
        # Content already has "Speaker: text" format
        line = f"[{timestamp}] {trace.content}"
        context_lines.append(line)

    context = "\n".join(context_lines[:30])

    # Generate answer
    prompt = ANSWER_PROMPT.format(context=context, question=question)
    generated = await generate_answer_async(http_session, prompt, SYSTEM_MESSAGE)

    # Judge
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)
    binary_correct = judgment.get("binary_correct", 0)

    gen_time = time.time() - start_time
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    # Get top trace info for debugging
    top_score = results[0][1] if results else 0
    top_concepts = len(results[0][0].access_keys) if results else 0

    print(f"    [{q_idx + 1}] {category_name}: {status} | score={top_score:.3f} | concepts={top_concepts}", flush=True)

    return {
        "question_id": q_idx,
        "category": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "correct": binary_correct == 1,
        "time_seconds": round(gen_time, 2),
        "retrieval_time_ms": round(retrieval_time * 1000, 2),
        "retrieval_method": "4d_holographic",
        "top_score": round(top_score, 4),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_4d_benchmark(max_conversations: int = 10):
    """Run 4D Holographic Benchmark."""
    print("=" * 70)
    print("LoCoMo 4D HOLOGRAPHIC Benchmark")
    print("TARGET: 92%+ Accuracy via Concept Resonance")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Strategy: 4D Concept Intersection (NOT 3D Graph Search)")
    print(f"Conversations: {max_conversations}")
    print("=" * 70)
    print()

    # Load dataset
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path) as f:
        data = json.load(f)

    conversations = data[:max_conversations]
    print(f"Processing {len(conversations)} conversations...")

    all_results = []
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(conversations):
            conv_id = item.get("conversation_id", conv_idx)
            messages = extract_messages(item)

            speakers = list(set(m["speaker"] for m in messages))
            speaker_str = " & ".join(speakers[:2]) if speakers else f"Conv {conv_id}"

            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {speaker_str}")

            if not messages:
                continue

            # Get reference date
            reference_date = datetime.now(timezone.utc)
            for msg in reversed(messages):
                parsed = parse_session_datetime(msg.get("session_time", ""))
                if parsed:
                    reference_date = parsed
                    break

            # Create 4D Holographic Retriever
            retriever = HolographicRetriever()

            # INGEST messages as 4D traces
            print(f"  Ingesting {len(messages)} messages into 4D holographic memory...")

            for i, msg in enumerate(messages):
                parsed_time = parse_session_datetime(msg.get("session_time", ""))

                # Get embedding
                embedding = await get_embedding_async(http_session, msg["text"])

                # Create a minimal NeuralNode for ingestion
                node = NeuralNode(
                    node_id=f"conv_{conv_id}_msg_{i}",
                    content=msg["text"],
                    layer=NodeLayer.MESSAGE,
                    session_key=f"conv_{conv_id}",
                    created_at=parsed_time,
                    embedding=embedding,
                    metadata={"speaker": msg["speaker"]},
                )

                # Ingest with speaker as first-class concept
                retriever.ingest_node(node, speaker=msg["speaker"])

            print(f"  Created {retriever.get_trace_count()} traces, {retriever.get_concept_count()} concepts")

            # Process questions
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions...")

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_4d(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        retriever=retriever,
                        reference_date=reference_date,
                    )
                    for i, qa in enumerate(batch)
                ]

                batch_results = await asyncio.gather(*tasks)
                for r in batch_results:
                    r["conversation_id"] = conv_id
                    all_results.append(r)

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - 4D Holographic Retrieval")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    print(f"\nOverall: {correct}/{total} ({100*correct/total:.2f}%)")

    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    print("\nPer-Category Accuracy:")
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]:
        if cat in category_results:
            cat_results = category_results[cat]
            cat_correct = sum(1 for r in cat_results if r["correct"])
            print(f"  {cat}: {cat_correct}/{len(cat_results)} ({100*cat_correct/len(cat_results):.2f}%)")

    # Save final results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = results_dir / f"locomo10_4D_HOLOGRAPHIC_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "4D_HOLOGRAPHIC_V1.0",
            "retrieval_method": "concept_resonance",
            "answer_model": OLLAMA_MODEL,
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "results": all_results,
        }, f, indent=2)

    print(f"\nResults saved to: {results_file}")

    if correct / total >= 0.92:
        print("\n" + "=" * 70)
        print("TARGET ACHIEVED: 92%+ ACCURACY!")
        print("=" * 70)

    return all_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_4d_benchmark(args.max_conversations))
