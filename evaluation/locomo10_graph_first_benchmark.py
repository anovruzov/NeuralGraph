"""
LoCoMo Graph-First Benchmark v1.0
Target: 92%+ Accuracy through Graph-First Retrieval

The fundamental insight for high accuracy:
- Embedding search finds SEMANTICALLY similar text
- But QA requires FACTUALLY relevant memories
- "What is Caroline's job?" needs memories about Caroline + job
- NOT memories that are semantically similar to the question

Graph-First Strategy:
1. Decompose query into structured components (entities, attributes, actions, temporal)
2. Use entity index for O(1) lookup of ALL entity mentions
3. Filter by attribute/action/temporal constraints
4. Rank within filtered candidates (embeddings only for tie-breaking)

This works because:
- Entity lookup is O(1) via index - guaranteed to find entity mentions
- Filtering is PRECISE (not approximate like embeddings)
- Only uses embeddings for final ranking within GOOD candidates
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

# Import Neural Graph System
from memmachine.neural_graph import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
    NeuralNode,
    NodeLayer,
)
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.query_decomposer import GraphFirstRetriever, QueryDecomposer

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


def _save_incremental(all_results: list, results_dir: Path):
    """Save results incrementally after each batch."""
    if not all_results:
        return

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])

    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    avg_retrieval_ms = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0

    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    incremental_file = results_dir / "graph_first_incremental_results.json"

    with open(incremental_file, 'w') as f:
        json.dump({
            "status": "in_progress",
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "avg_retrieval_ms": round(avg_retrieval_ms, 2),
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "results": all_results,
        }, f, indent=2)


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
# PROMPTS - Graph-First Specific
# =============================================================================

SYSTEM_MESSAGE = """You are a memory retrieval system implementing graph-based pattern completion.
Your task: Find and synthesize information from timestamped memories.
Answer based ONLY on what's in the memories. If not stated, say "Not mentioned in memories"."""

ANSWER_PROMPT = """You are a memory retrieval system using graph-first retrieval.

THESE MEMORIES WERE SELECTED because they contain:
- The EXACT ENTITY mentioned in the question
- Relevant ATTRIBUTES or ACTIONS related to the query

REASONING PROCESS:
1. WHO is the question about? (Entity is pre-filtered for you)
2. WHAT is being asked? Identify the topic/activity/relationship.
3. SCAN all memories for this person + topic combination.
4. SYNTHESIZE a complete answer from ALL relevant memories.

CRITICAL:
- These memories are GUARANTEED to mention the entity
- Focus on finding the SPECIFIC FACT being asked about
- If multiple memories describe the same thing, combine them

Memories:
{context}

Question: {question}

Answer (provide the specific fact requested):"""

TEMPORAL_PROMPT = """You are a memory retrieval system specialized in temporal (time) questions.

THESE MEMORIES were selected because they contain:
- The ENTITY mentioned in the question
- TEMPORAL information (dates, times, events)

CRITICAL FOR TEMPORAL QUESTIONS:
1. Look at the [YYYY-MM-DD HH:MM] timestamps on each memory
2. The timestamps show WHEN the conversation happened
3. Relative time references (e.g., "last Saturday") are relative to the MEMORY'S timestamp
4. Calculate: If memory timestamp is [2023-05-08] and says "last Saturday", find Saturday before May 8, 2023

{temporal_hints}

Memories:
{context}

Question: {question}

Answer (provide the specific date/time):"""

MULTI_HOP_PROMPT = """You are a memory retrieval system for multi-hop reasoning.

THESE MEMORIES were selected because they contain ENTITIES and RELATIONS needed for this question.

REASONING:
1. This question requires connecting information across MULTIPLE memories
2. Find the CHAIN: Entity A -> Relation -> Entity B -> Relation -> Answer
3. The answer is NOT in any single memory - you must COMBINE

Memories:
{context}

Question: {question}

Answer (synthesize from multiple memories):"""


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


def extract_entities_simple(text: str) -> list[str]:
    """Extract entity names from text."""
    words = re.findall(r'\b[A-Z][a-z]+\b', text)
    stop_words = {'The', 'This', 'That', 'These', 'Those', 'What', 'When', 'Where',
                  'Who', 'Why', 'How', 'Yes', 'No', 'And', 'But', 'Or', 'If', 'Then',
                  'So', 'Because', 'However', 'Although', 'While', 'Since', 'Just',
                  'Really', 'Actually', 'Probably', 'Maybe', 'Also', 'Always', 'Never',
                  'Today', 'Yesterday', 'Tomorrow', 'Monday', 'Tuesday', 'Wednesday',
                  'Thursday', 'Friday', 'Saturday', 'Sunday', 'January', 'February',
                  'March', 'April', 'May', 'June', 'July', 'August', 'September',
                  'October', 'November', 'December', 'Hello', 'Hi', 'Hey', 'Thanks',
                  'Thank', 'Please', 'Sorry', 'Great', 'Good', 'Nice', 'Cool', 'Awesome'}
    entities = [w for w in words if w not in stop_words and len(w) > 1]
    return list(set(entities))


class EpisodeLike:
    """Minimal Episode-like object for neural graph ingestion."""
    def __init__(self, content: str, uid: str, speaker: str, created_at: datetime | None, session_key: str, msg_idx: int):
        self.content = content
        self.uid = uid
        self.producer_id = speaker
        self.created_at = created_at

        entity_ids = extract_entities_simple(content)
        if speaker and speaker not in ['Unknown', 'user', 'assistant']:
            entity_ids.append(speaker)

        self.filterable_metadata = {
            "speaker": speaker,
            "session_key": session_key,
            "msg_idx": msg_idx,
            "entity_ids": entity_ids,
        }
        self.importance_score = 0.5


def calculate_temporal_hints(question: str, results: list[tuple], reference_date: datetime) -> str:
    """Calculate temporal hints for temporal questions."""
    from memmachine.wave_memory import TemporalCalculator

    q_lower = question.lower()
    is_temporal = any(t in q_lower for t in ["when", "what date", "what day", "how long", "which year"])
    if not is_temporal:
        return ""

    calculator = TemporalCalculator()
    hints = []

    for node, score in results[:15]:
        content = node.content if hasattr(node, 'content') else ""
        timestamp = node.created_at if hasattr(node, 'created_at') else None

        if not timestamp:
            continue

        content_lower = content.lower()

        # Pattern: "last [weekday]"
        match = re.search(r"last\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", content_lower)
        if match and timestamp:
            weekday = match.group(1)
            expr = f"last {weekday}"
            result = calculator.calculate(expr, timestamp)
            if result.calculated_date:
                date_str = result.calculated_date.strftime("%B %d, %Y")
                hints.append(f"'{expr}' (from {timestamp.strftime('%Y-%m-%d')}) = {date_str}")

    if hints:
        unique = list(dict.fromkeys(hints))
        return "\n\nCALCULATED DATES:\n" + "\n".join(f"- {h}" for h in unique[:5])

    return ""


def get_prompt_for_category(category_name: str, question: str = "") -> str:
    """Get appropriate prompt for category."""
    if category_name == "temporal":
        return TEMPORAL_PROMPT
    elif category_name == "multi_hop":
        return MULTI_HOP_PROMPT

    q_lower = question.lower()
    if any(t in q_lower for t in ["when", "what date", "how long"]):
        return TEMPORAL_PROMPT
    elif any(t in q_lower for t in ["both", "together", "and", "compared"]):
        return MULTI_HOP_PROMPT

    return ANSWER_PROMPT


def format_context(results: list[tuple], max_chars: int = 12000) -> str:
    """Format retrieval results as context."""
    lines = []
    total_chars = 0

    for node, score in results:
        timestamp = node.created_at.strftime("%Y-%m-%d %H:%M") if node.created_at else "Unknown"
        speaker = node.metadata.get("producer_id", node.metadata.get("speaker", "Unknown"))
        line = f"[{timestamp}] {speaker}: {node.content}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


# =============================================================================
# GRAPH-FIRST RETRIEVER WRAPPER
# =============================================================================

class GraphFirstBenchmarkRetriever:
    """Wrapper for GraphFirstRetriever optimized for benchmark."""

    def __init__(self, storage: InMemoryNeuralGraphStorage):
        self._storage = storage
        self._decomposer = QueryDecomposer()
        self._graph_retriever = GraphFirstRetriever(storage, self._decomposer)

    async def retrieve(
        self,
        query_text: str,
        session_key: str,
        query_embedding: list[float] | None = None,
        limit: int = 30,
    ) -> list[tuple[NeuralNode, float]]:
        """Graph-first retrieval."""
        return await self._graph_retriever.retrieve(
            query=query_text,
            session_key=session_key,
            limit=limit,
            query_embedding=query_embedding,
        )

    def get_decomposition(self, query: str):
        """Get query decomposition for debugging."""
        return self._decomposer.decompose(query)


# =============================================================================
# PROCESS QUESTION
# =============================================================================

async def process_question_graph_first(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    retriever: GraphFirstBenchmarkRetriever,
    session_key: str,
    reference_date: datetime | None,
) -> dict:
    """Process a question using graph-first retrieval."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # Get query embedding (for tie-breaking only)
    query_embedding = await get_embedding_async(http_session, question)

    # Graph-first retrieval
    retrieval_start = time.time()
    results = await retriever.retrieve(
        query_text=question,
        session_key=session_key,
        query_embedding=query_embedding,
        limit=TOP_K_RETRIEVAL,
    )
    retrieval_time = time.time() - retrieval_start

    # Get decomposition for debugging
    decomposed = retriever.get_decomposition(question)

    # Format context
    context = format_context(results, max_chars=CONTEXT_LIMIT)

    # Calculate temporal hints for temporal questions
    temporal_hints = ""
    if category_name == "temporal" or "when" in question.lower():
        temporal_hints = calculate_temporal_hints(question, results, reference_date)

    # Get prompt
    prompt_template = get_prompt_for_category(category_name, question)

    if "{temporal_hints}" in prompt_template:
        prompt = prompt_template.format(
            context=context,
            question=question,
            temporal_hints=temporal_hints,
        )
    else:
        if temporal_hints:
            context = context + temporal_hints
        prompt = prompt_template.format(context=context, question=question)

    # Generate answer
    generated = await generate_answer_async(http_session, prompt, SYSTEM_MESSAGE)

    # Judge
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)
    binary_correct = judgment.get("binary_correct", 0)

    gen_time = time.time() - start_time
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    print(f"    [{q_idx + 1}] {category_name}: {status} | entities={decomposed.entities} | type={decomposed.query_type.name}", flush=True)

    return {
        "question_id": q_idx,
        "category": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "llm_score": binary_correct,
        "correct": binary_correct == 1,
        "time_seconds": round(gen_time, 2),
        "retrieval_time_ms": round(retrieval_time * 1000, 2),
        "retrieval_method": "graph_first",
        "decomposition": {
            "query_type": decomposed.query_type.name,
            "entities": decomposed.entities,
            "attributes": decomposed.attributes,
            "key_terms": decomposed.key_terms,
        },
        "num_results": len(results),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_graph_first_benchmark(max_conversations: int = 10):
    """Run Graph-First Benchmark targeting 92%+ accuracy."""
    print("=" * 70)
    print("LoCoMo Graph-First Benchmark v1.0")
    print("TARGET: 92%+ Accuracy via Graph-First Retrieval")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Strategy: Entity Index + Attribute Filtering + Edge Traversal")
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

            # Create Neural Graph Service with storage
            session_key = f"conv_{conv_id}"
            config = NeuralGraphServiceConfig(
                enabled=True,
                hierarchy_enabled=True,
                temporal_enabled=True,
                consolidation_enabled=True,
                auto_temporal_linking=True,
            )
            neural_service = NeuralGraphService(config=config)

            # Ingest messages
            print(f"  Ingesting {len(messages)} messages...")
            embeddings = []
            for msg in messages:
                emb = await get_embedding_async(http_session, msg["text"])
                embeddings.append(emb)

            episodes = []
            for i, msg in enumerate(messages):
                parsed_time = parse_session_datetime(msg.get("session_time", ""))
                episode = EpisodeLike(
                    content=msg["text"],
                    uid=f"{session_key}_msg_{i}",
                    speaker=msg["speaker"],
                    created_at=parsed_time,
                    session_key=session_key,
                    msg_idx=i,
                )
                episodes.append(episode)

            # Ingest into neural graph
            result = await neural_service.process_episodes(
                episodes=episodes,
                embeddings=embeddings,
                session_key=session_key,
            )

            print(f"  Ingested {result.nodes_created} nodes")

            # Create Graph-First Retriever using the neural service's storage
            retriever = GraphFirstBenchmarkRetriever(neural_service._storage)

            # Process questions
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions...")

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_graph_first(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        retriever=retriever,
                        session_key=session_key,
                        reference_date=reference_date,
                    )
                    for i, qa in enumerate(batch)
                ]

                batch_results = await asyncio.gather(*tasks)
                for r in batch_results:
                    r["conversation_id"] = conv_id
                    all_results.append(r)

                # Save incrementally
                _save_incremental(all_results, results_dir)

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - Graph-First Retrieval")
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

    # Retrieval time stats
    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    avg_retrieval_ms = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0
    print(f"\nAvg Retrieval Time: {avg_retrieval_ms:.2f}ms")

    # Save final results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = results_dir / f"locomo10_GRAPH_FIRST_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "GRAPH_FIRST_V1.0",
            "retrieval_method": "graph_first_entity_index",
            "answer_model": OLLAMA_MODEL,
            "judge_model": JUDGE_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "avg_retrieval_ms": round(avg_retrieval_ms, 2),
            "results": all_results,
        }, f, indent=2)

    print(f"\nResults saved to: {results_file}")

    # Achievement check
    if correct / total >= 0.92:
        print("\n" + "=" * 70)
        print("TARGET ACHIEVED: 92%+ ACCURACY!")
        print("=" * 70)
    elif correct / total >= 0.70:
        print(f"\nGood progress! {100*correct/total:.1f}% - Need {92 - 100*correct/total:.1f}% more for target")
    else:
        print(f"\nCurrent: {100*correct/total:.1f}% - Significant gap to 92% target")

    return all_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_graph_first_benchmark(args.max_conversations))
