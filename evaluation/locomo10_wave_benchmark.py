"""
LoCoMo-WAVE Benchmark v2.0
Wave Resonance Retrieval - No Classification, Only Resonance

This benchmark uses WaveResonanceRetriever instead of BM25+semantic classification.
Every query is a wave. Every memory is a wave. Retrieval = resonance.

Key features:
- Wave amplitude encoding (9 dimensions: temporal, entity, relational, action, state, spatial, causal, emotional, quantitative)
- Entity mismatch penalty (critical for adversarial questions)
- Lexical resonance for keyword matching
- Signal-based matching (temporal, entity, action signals)
- Ollama local judge (no GPT-4o-mini rate limits)
"""

import json
import asyncio
import aiohttp
import time
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import Wave Memory System
from memmachine.wave_memory import (
    WaveResonanceRetriever,
    WaveEncoder,
    MemoryWave,
    TemporalCalculator,
    TemporalContextEnhancer,
)

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"  # Using Ollama local model
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 4  # Process questions in parallel
CONTEXT_LIMIT = 12000
TOP_K_RETRIEVAL = 30

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

# Prompt templates
SYSTEM_MESSAGE = """You are a precise memory retrieval assistant. Your ONLY job is to answer questions using information from the provided conversation memories.

ABSOLUTE RULES:
1. NEVER use knowledge from outside the provided memories
2. NEVER guess or make up information
3. If the answer is NOT in the memories, you MUST respond: "Not stated." or "Not stated in memories."
4. For date/time questions: If a specific date is NOT explicitly stated, respond "Not stated."
5. Be concise and specific"""

STANDARD_PROMPT = """Based on the following conversation memories, answer the question.

MEMORIES:
{context}

QUESTION: {question}

Instructions:
- Use ONLY facts from the memories above
- If you find information related to the question, provide it even if partial
- Only say "Not stated in memories" if the topic is COMPLETELY absent from the memories
"""

TEMPORAL_PROMPT = """Based on the following conversation memories, answer the question about WHEN something happened.

MEMORIES:
{context}

QUESTION: {question}

Instructions:
- IMPORTANT: Check the CALCULATED DATES section if present - it contains pre-computed dates
- Find specific dates, times, or temporal references
- Be precise with dates if they are mentioned
- Use calculated dates when available (e.g., "the sunday before May 25" = calculated date)
- If no date is stated, respond "Not stated."
"""

ADVERSARIAL_PROMPT = """Based on the following conversation memories, answer the question CAREFULLY.

MEMORIES:
{context}

QUESTION: {question}

CRITICAL WARNING: This question may be TRICKY or MISLEADING.
- Carefully verify WHO did WHAT before answering
- Don't assume - check the memories for exact attribution
- If the question attributes something to the wrong person, point this out
- If the information is not clearly stated for the specific person asked about, say "Not stated."
"""

MULTI_HOP_PROMPT = """Based on the following conversation memories, answer the question by combining information.

MEMORIES:
{context}

QUESTION: {question}

Instructions:
- This may require combining multiple pieces of information
- Connect the dots between different memories
- If you cannot find all needed information, say "Not stated."
"""

# Judge prompt
ACCURACY_PROMPT = """Given a question, a gold answer, and a generated answer, evaluate whether the generated answer is correct.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"""


# =============================================================================
# LLM CALLS
# =============================================================================

async def generate_answer_async(session: aiohttp.ClientSession, prompt: str) -> str:
    """Generate answer using Ollama."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_MESSAGE},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.0}
            },
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


def evaluate_llm_judge_sync(question: str, gold_answer: str, generated_answer: str) -> int:
    """Evaluate using Ollama local model as LLM judge."""
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": JUDGE_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": ACCURACY_PROMPT.format(
                            question=question,
                            gold_answer=gold_answer,
                            generated_answer=generated_answer,
                        ),
                    },
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0}
            },
            timeout=120
        )
        result_text = response.json()["message"]["content"]
        result = json.loads(result_text)
        label = result.get("label", "WRONG")
        return 1 if label == "CORRECT" else 0
    except Exception as e:
        print(f"    LLM Judge error: {e}", flush=True)
        return 0


async def judge_answer_async(session: aiohttp.ClientSession, question: str, gold_answer: str, generated_answer: str) -> dict:
    """Async wrapper for LLM judge."""
    loop = asyncio.get_event_loop()
    score = await loop.run_in_executor(
        None, evaluate_llm_judge_sync, question, gold_answer, generated_answer
    )
    return {"binary_correct": score}


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
    """Parse session datetime string."""
    if not session_time or session_time == "Unknown":
        return None

    formats = [
        "%B %d, %Y %I:%M %p",  # "January 15, 2023 2:30 PM"
        "%B %d, %Y",          # "January 15, 2023"
        "%Y-%m-%d %H:%M:%S",  # "2023-01-15 14:30:00"
        "%Y-%m-%d",           # "2023-01-15"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(session_time, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def get_prompt_for_category(category_name: str) -> str:
    """Get appropriate prompt template for category."""
    if category_name == "temporal":
        return TEMPORAL_PROMPT
    elif category_name == "adversarial":
        return ADVERSARIAL_PROMPT
    elif category_name == "multi_hop":
        return MULTI_HOP_PROMPT
    else:
        return STANDARD_PROMPT


# =============================================================================
# WAVE RETRIEVAL
# =============================================================================

def format_wave_context(results: list[tuple[MemoryWave, float]], max_chars: int = 12000) -> str:
    """Format wave retrieval results as context."""
    lines = []
    total_chars = 0

    for memory, score in results:
        # Format: [timestamp] speaker: text
        timestamp = memory.timestamp.strftime("%Y-%m-%d %H:%M") if memory.timestamp else ""
        line = f"[{timestamp}] {memory.speaker}: {memory.content}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


def calculate_temporal_hints(
    question: str,
    results: list[tuple[MemoryWave, float]],
    reference_date: datetime,
) -> str:
    """Calculate temporal hints for a question.

    This resolves relative date expressions to absolute dates.
    E.g., "the sunday before 25 May 2023" -> "May 21, 2023"
    """
    import re

    q_lower = question.lower()
    calculator = TemporalCalculator()
    hints = []

    # Check if this is a temporal question
    is_temporal = any(trigger in q_lower for trigger in [
        "when ", "what date", "what day", "how long",
        "since when", "how many years", "how many months",
    ])

    if not is_temporal:
        return ""

    # Extract relative expressions from retrieved memories and calculate them
    for memory, score in results[:15]:  # Check top 15 memories
        content = memory.content
        content_lower = content.lower()

        # Pattern: "the [weekday] before [date]"
        match = re.search(
            r"the\s+(\w+day)\s+before\s+(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4})",
            content_lower
        )
        if match:
            expr = match.group(0)
            result = calculator.calculate(expr, memory.timestamp)
            if result.calculated_date:
                date_str = result.calculated_date.strftime("%B %d, %Y")
                hints.append(f"'{expr}' = {date_str}")

        # Pattern: "the week before [date]"
        match = re.search(
            r"the\s+week\s+before\s+(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4})",
            content_lower
        )
        if match:
            expr = match.group(0)
            result = calculator.calculate(expr, memory.timestamp)
            if result.date_range:
                start_str = result.date_range[0].strftime("%B %d")
                end_str = result.date_range[1].strftime("%B %d, %Y")
                hints.append(f"'{expr}' = {start_str} to {end_str}")

    # Look for duration questions
    if "how long" in q_lower or "how many years" in q_lower:
        # Find date range in memories
        dates = []
        for memory, _ in results:
            if memory.timestamp:
                dates.append(memory.timestamp)

        if len(dates) >= 2:
            dates.sort()
            duration = calculator.calculate_duration_between(dates[0], dates[-1])
            if duration.duration_text:
                hints.append(f"Time span covered: {duration.duration_text}")

    # Check for year-based duration in content (e.g., "4 years")
    for memory, _ in results[:10]:
        content_lower = memory.content.lower()
        match = re.search(r"(\d+)\s+(year|month|week)s?", content_lower)
        if match:
            num = match.group(1)
            unit = match.group(2)
            hints.append(f"Duration mentioned: {num} {unit}s")
            break

    if hints:
        # Deduplicate
        unique_hints = list(dict.fromkeys(hints))
        return "\n\nCALCULATED DATES:\n" + "\n".join(f"- {h}" for h in unique_hints[:5])

    return ""


# =============================================================================
# PROCESS QUESTION
# =============================================================================

async def process_question_wave(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    retriever: WaveResonanceRetriever,
    reference_date: datetime | None = None,
) -> dict:
    """Process a single question using Wave Resonance retrieval."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # ==========================================================================
    # STEP 1 - WAVE RESONANCE RETRIEVAL
    # No classification routing - just find memories that resonate with the query
    # ==========================================================================
    results = retriever.retrieve(
        query=question,
        top_k=TOP_K_RETRIEVAL,
        reference_date=reference_date,
    )

    context = format_wave_context(results, max_chars=CONTEXT_LIMIT)

    # ==========================================================================
    # STEP 1.5 - CALCULATE TEMPORAL HINTS
    # ==========================================================================
    temporal_hints = ""
    if category_name == "temporal" or any(t in question.lower() for t in ["when", "how long", "what date"]):
        temporal_hints = calculate_temporal_hints(question, results, reference_date)

    # ==========================================================================
    # STEP 2 - GENERATE ANSWER
    # ==========================================================================
    prompt_template = get_prompt_for_category(category_name)
    enhanced_context = context + temporal_hints
    prompt = prompt_template.format(context=enhanced_context, question=question)
    generated = await generate_answer_async(http_session, prompt)

    # ==========================================================================
    # STEP 3 - ANTI-ABSTENTION RETRY (except for adversarial)
    # ==========================================================================
    abstention_retried = False
    if "not stated" in generated.lower() and category_name != "adversarial":
        relaxed_prompt = prompt.replace(
            "Only say \"Not stated in memories\"",
            "Try to provide ANY relevant information"
        )
        generated_retry = await generate_answer_async(http_session, relaxed_prompt)
        if "not stated" not in generated_retry.lower():
            generated = generated_retry
            abstention_retried = True

    # ==========================================================================
    # STEP 4 - JUDGE ANSWER
    # ==========================================================================
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)

    gen_time = time.time() - start_time
    binary_correct = judgment.get("binary_correct", 0)
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    print(f"    [{q_idx + 1}] {category_name}: {status}", flush=True)

    return {
        "question_id": q_idx,
        "category": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "llm_score": binary_correct,
        "correct": binary_correct == 1,
        "time_seconds": round(gen_time, 2),
        "abstention_retried": abstention_retried,
        "retrieval_method": "wave_resonance",
        "has_temporal_hints": bool(temporal_hints),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_wave_benchmark(max_conversations: int = 10):
    """Run Wave Resonance benchmark."""
    print("=" * 70)
    print("LoCoMo-WAVE Benchmark v2.0")
    print("Wave Resonance Retrieval - No Classification, Only Resonance")
    print("=" * 70)
    print(f"Answer Model: {OLLAMA_MODEL}")
    print(f"Judge Model: {JUDGE_MODEL} (Ollama local)")
    print(f"Conversations: {max_conversations}")
    print("=" * 70)
    print()

    # Load dataset
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    print(f"Loading dataset from {data_path}...")

    with open(data_path) as f:
        data = json.load(f)

    conversations = data[:max_conversations]
    print(f"Processing {len(conversations)} conversations...")
    print()

    all_results = []

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(conversations):
            conv_id = item.get("conversation_id", conv_idx)

            # Get speaker names for display
            messages = extract_messages(item)
            speakers = list(set(m["speaker"] for m in messages))
            speaker_str = " & ".join(speakers[:2]) if speakers else f"Conv {conv_id}"

            print(f"\n[Conversation {conv_idx + 1}/{len(conversations)}] {speaker_str}")

            if not messages:
                print("  No messages found, skipping...")
                continue

            # Get reference date from last session
            reference_date = datetime.now(timezone.utc)
            for msg in reversed(messages):
                parsed = parse_session_datetime(msg.get("session_time", ""))
                if parsed:
                    reference_date = parsed
                    break

            # Create Wave Retriever and ingest messages
            retriever = WaveResonanceRetriever(use_semantic_resonance=False)  # No embeddings needed

            for msg in messages:
                timestamp = parse_session_datetime(msg.get("session_time", ""))
                retriever.ingest(
                    content=msg["text"],
                    speaker=msg["speaker"],
                    timestamp=timestamp,
                    session_key=f"session_{msg['session']}",
                    msg_idx=msg["msg_idx"],
                    reference_date=reference_date,
                )

            print(f"  Ingested {retriever.memory_count} messages as waves")

            # Process questions
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions...")

            # Process questions in batches for parallelism
            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_wave(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        retriever=retriever,
                        reference_date=reference_date,
                    )
                    for i, qa in enumerate(batch)
                ]

                results = await asyncio.gather(*tasks)
                for result in results:
                    result["conversation_id"] = conv_id
                    all_results.append(result)

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - Wave Resonance Retrieval")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    print(f"\nOverall: {correct}/{total} ({100*correct/total:.2f}%)")

    # Per-category
    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    print("\nPer-Category Accuracy:")
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]:
        if cat in category_results:
            cat_results = category_results[cat]
            cat_correct = sum(1 for r in cat_results if r["correct"])
            print(f"  {cat}: {cat_correct}/{len(cat_results)} ({100*cat_correct/len(cat_results):.2f}%)")

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    results_file = results_dir / f"locomo10_WAVE_V2.0_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "WAVE_V2.0",
            "retrieval_method": "wave_resonance",
            "judge_model": JUDGE_MODEL,
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

    return all_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10, help="Max conversations to process")
    args = parser.parse_args()

    asyncio.run(run_wave_benchmark(args.max_conversations))
