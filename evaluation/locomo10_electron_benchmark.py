"""
LoCoMo Electron Benchmark v1.0
True Electrical Simulation - Memory Retrieval as Charge Propagation

This benchmark tests the Electron abstraction:
- Electrons = fundamental units of electrical activation
- Propagation through edges with energy decay, phase shifts
- Charge accumulation and threshold-based firing
- Like action potentials cascading through neural circuits

Both AI and biological brains are electrical systems.
Memory retrieval is NOT a search - it's electrical activation cascading through circuits.
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import requests
import math
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import Neural Graph System with Electron Retriever
from memmachine.neural_graph import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
    NeuralNode,
    NodeLayer,
    RetrievalResult,
)
from memmachine.neural_graph.temporal_resolver import enrich_message_with_temporal
from memmachine.neural_graph.electron import ElectronRetrieverConfig

# Import Wave Memory System (for wave encoding)
from memmachine.wave_memory import (
    WaveEncoder,
    WaveAmplitudes,
)

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 3
CONTEXT_LIMIT = 15000
TOP_K_RETRIEVAL = 45

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}


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

    incremental_file = results_dir / "electron_incremental_results.json"

    with open(incremental_file, 'w') as f:
        json.dump({
            "status": "in_progress",
            "retrieval_method": "ELECTRON_FLOW",
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

SYSTEM_MESSAGE = """You are a memory retrieval system using electrical activation patterns.
Like neurons in a brain, memories are activated by charge propagation.
Answer based ONLY on what's in the activated memories. If not stated, say "Not mentioned in memories"."""

ANSWER_PROMPT = """You are a memory retrieval system. Memories have been electrically activated based on the query.

REASONING PROCESS:
1. WHO is the question about? Identify the person/entity.
2. WHAT is being asked? Identify the topic/activity/relationship.
3. SCAN the activated memories for this person + topic combination.
4. CONNECT related memories - they may describe the same thing differently.
5. SYNTHESIZE a complete answer from ALL relevant memories.

Activated Memories (sorted by charge accumulation):
{context}

Question: {question}

Answer (be complete - include all relevant details):"""

TEMPORAL_PROMPT = """You are a memory retrieval system specialized in temporal (time) questions.

CRITICAL FOR TEMPORAL QUESTIONS:
1. Look at the [YYYY-MM-DD HH:MM] timestamps on each memory
2. The timestamps show WHEN the conversation happened
3. Relative time references are relative to the MEMORY'S timestamp

Activated Memories:
{context}

Question: {question}

Answer (provide the specific date/time):"""


# =============================================================================
# DATA EXTRACTION
# =============================================================================

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


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
    """Extract entity names from text using simple heuristics."""
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
    def __init__(self, content: str, uid: str, speaker: str, created_at: datetime | None, session_key: str, msg_idx: int, session_time: str = ""):
        # CRITICAL: Enrich content with resolved temporal dates
        # This transforms "I went to Paris yesterday" + session date "8 May 2023"
        # into "I went to Paris yesterday [dates: 7 May 2023]"
        enriched_content, temporal_metadata = enrich_message_with_temporal(content, session_time)

        self.content = enriched_content
        self.uid = uid
        self.producer_id = speaker
        self.created_at = created_at

        entity_ids = extract_entities_simple(content)  # Use original content for entity extraction
        if speaker and speaker not in ['Unknown', 'user', 'assistant']:
            entity_ids.append(speaker)

        self.filterable_metadata = {
            "speaker": speaker,
            "session_key": session_key,
            "msg_idx": msg_idx,
            "entity_ids": entity_ids,
            **temporal_metadata,  # Include resolved dates in metadata
        }
        self.importance_score = 0.5


def format_context(results: list[tuple], max_chars: int = 12000) -> str:
    """Format retrieval results as context."""
    lines = []
    total_chars = 0

    for item in results:
        node = item[0]
        score = item[1] if len(item) > 1 else 0.0
        timestamp = node.created_at.strftime("%Y-%m-%d %H:%M") if node.created_at else "Unknown"
        speaker = node.metadata.get("producer_id", node.metadata.get("speaker", "Unknown"))
        line = f"[{timestamp}] (charge: {score:.3f}) {speaker}: {node.content}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


def get_prompt_for_category(category_name: str, question: str = "") -> str:
    """Get appropriate prompt for category."""
    q_lower = question.lower()
    if category_name == "temporal" or any(t in q_lower for t in ["when", "what date", "how long"]):
        return TEMPORAL_PROMPT
    return ANSWER_PROMPT


# =============================================================================
# PROCESS QUESTION
# =============================================================================

async def process_question_electron(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    neural_service: NeuralGraphService,
    wave_encoder: WaveEncoder,
    session_key: str,
) -> dict:
    """Process a question using Electron-based retrieval."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # Get query embedding
    query_embedding = await get_embedding_async(http_session, question)
    if not query_embedding:
        return {
            "question_id": q_idx,
            "category": category_name,
            "question": question,
            "gold_answer": gold_answer,
            "generated_answer": "Error: Failed to get embedding",
            "llm_score": 0,
            "correct": False,
        }

    # Encode query wave amplitudes
    query_wave = wave_encoder.encode(question)
    query_amplitudes = query_wave.amplitudes
    query_wave_dict = {
        "temporal": getattr(query_amplitudes, 'temporal', 0.0),
        "entity": getattr(query_amplitudes, 'entity', 0.0),
        "relational": getattr(query_amplitudes, 'relational', 0.0),
        "action": getattr(query_amplitudes, 'action', 0.0),
        "state": getattr(query_amplitudes, 'state', 0.0),
        "spatial": getattr(query_amplitudes, 'spatial', 0.0),
        "causal": getattr(query_amplitudes, 'causal', 0.0),
        "emotional": getattr(query_amplitudes, 'emotional', 0.0),
        "quantitative": getattr(query_amplitudes, 'quantitative', 0.0),
    }

    # Electron-based retrieval
    retrieval_start = time.time()
    result = await neural_service.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
        query_wave_amplitudes=query_wave_dict,
    )
    retrieval_time = time.time() - retrieval_start

    # Format context
    context = format_context(result.nodes, max_chars=CONTEXT_LIMIT)

    # Get prompt
    prompt_template = get_prompt_for_category(category_name, question)
    prompt = prompt_template.format(context=context, question=question)

    # Generate answer
    generated = await generate_answer_async(http_session, prompt, SYSTEM_MESSAGE)

    # Judge
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)
    binary_correct = judgment.get("binary_correct", 0)

    gen_time = time.time() - start_time
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    # Log retrieval stats
    stages = ", ".join(result.stages_executed) if result.stages_executed else "unknown"
    print(f"    [{q_idx + 1}] {category_name}: {status} ({stages}, {retrieval_time*1000:.0f}ms)", flush=True)

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
        "retrieval_method": "electron_flow",
        "stages": result.stages_executed,
        "candidates_seen": result.total_candidates_seen,
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_electron_benchmark(max_conversations: int = 10):
    """Run Electron Benchmark - True Electrical Simulation."""
    print("=" * 70)
    print("LoCoMo Electron Benchmark v1.0")
    print("True Electrical Simulation - Memory as Charge Propagation")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Retrieval: ELECTRON FLOW (charge propagation + threshold firing)")
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

    wave_encoder = WaveEncoder()

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(conversations):
            conv_id = item.get("conversation_id", conv_idx)
            messages = extract_messages(item)

            speakers = list(set(m["speaker"] for m in messages))
            speaker_str = " & ".join(speakers[:2]) if speakers else f"Conv {conv_id}"

            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {speaker_str}")

            if not messages:
                continue

            # Create Neural Graph Service WITH ELECTRON RETRIEVER ENABLED
            session_key = f"conv_{conv_id}"

            # Configure Electron Retriever
            electron_config = ElectronRetrieverConfig(
                theta_frequency_hz=6.0,      # Biological theta rhythm
                firing_threshold=0.5,         # Node activation threshold
                refractory_ms=2.0,           # Refractory period after firing
                max_hops=5,                  # Maximum propagation depth
                energy_floor=0.1,            # Minimum energy to propagate
                seed_count=20,               # Number of seed nodes from vector search
                initial_energy=1.0,          # Initial electron energy
                max_propagation_steps=50,    # Max simulation steps
                final_limit=45,              # Final result limit
            )

            config = NeuralGraphServiceConfig(
                enabled=True,
                hierarchy_enabled=True,
                temporal_enabled=True,
                consolidation_enabled=True,
                auto_temporal_linking=True,
                auto_episode_segmentation=False,
                auto_consolidation_on_ingestion=False,
                apply_temporal_dynamics=True,
                # ELECTRON RETRIEVER: True electrical simulation
                use_electron_retriever=True,
                electron_config=electron_config,
            )
            neural_service = NeuralGraphService(config=config)

            # Ingest messages
            print(f"  Ingesting {len(messages)} messages...")

            # First create episodes with temporal enrichment
            episodes = []
            for i, msg in enumerate(messages):
                parsed_time = parse_session_datetime(msg.get("session_time", ""))
                session_time_str = msg.get("session_time", "")  # Raw session datetime string
                episode = EpisodeLike(
                    content=msg["text"],
                    uid=f"{session_key}_msg_{i}",
                    speaker=msg["speaker"],
                    created_at=parsed_time,
                    session_key=session_key,
                    msg_idx=i,
                    session_time=session_time_str,  # Pass for temporal resolution
                )
                episodes.append(episode)

            # Compute embeddings from ENRICHED content (includes resolved dates)
            embeddings = []
            for episode in episodes:
                emb = await get_embedding_async(http_session, episode.content)
                embeddings.append(emb)

            # Ingest into neural graph
            result = await neural_service.process_episodes(
                episodes=episodes,
                embeddings=embeddings,
                session_key=session_key,
            )

            print(f"  Ingested {result.nodes_created} nodes (Electron retriever active)")

            # Process questions
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions...")

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_electron(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        neural_service=neural_service,
                        wave_encoder=wave_encoder,
                        session_key=session_key,
                    )
                    for i, qa in enumerate(batch)
                ]

                results = await asyncio.gather(*tasks)
                for r in results:
                    r["conversation_id"] = conv_id
                    all_results.append(r)

                # Save incrementally after each batch
                _save_incremental(all_results, results_dir)

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - Electron Flow Retrieval (True Electrical Simulation)")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    print(f"\nOverall: {correct}/{total} ({100*correct/total:.2f}%)")

    # Latency stats
    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    if retrieval_times:
        avg_retrieval = sum(retrieval_times) / len(retrieval_times)
        print(f"Avg Retrieval: {avg_retrieval:.1f}ms")

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
    results_file = results_dir / f"locomo10_ELECTRON_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "ELECTRON_V1.0",
            "retrieval_method": "electron_flow_charge_propagation",
            "description": "True electrical simulation - memory retrieval as charge cascading through neural circuits",
            "answer_model": OLLAMA_MODEL,
            "judge_model": JUDGE_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "electron_config": {
                "theta_frequency_hz": 6.0,
                "firing_threshold": 0.5,
                "refractory_ms": 2.0,
                "max_hops": 5,
                "energy_floor": 0.1,
                "seed_count": 20,
            },
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "avg_retrieval_ms": sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0,
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "results": all_results,
        }, f, indent=2)

    print(f"\nResults saved to: {results_file}")

    return all_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_electron_benchmark(args.max_conversations))
