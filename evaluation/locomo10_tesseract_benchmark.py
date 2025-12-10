"""
LoCoMo TESSERACT Benchmark v1.0
===============================
4D Brain-Inspired Memory Architecture

THE INSIGHT: The brain doesn't store all memories the same way.
Different brain regions specialize in different types of retrieval:

1. HIPPOCAMPUS (Temporal Store): When did X happen?
2. NEOCORTEX (Entity Store): What is X's identity?
3. PREFRONTAL (Reasoning Store): How many X did Y do?
4. ORBITOFRONTAL (Adversarial Store): Did X NOT do Y?

The TESSERACT queries all stores in parallel and FUSES results
based on detected query type.

Expected improvements:
- Temporal: Query routing to TemporalStore
- Single-hop: EntityStore's speaker binding boost
- Multi-hop: ReasoningStore's breadth-first retrieval
- Adversarial: AdversarialStore's negation awareness
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import requests
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NodeLayer
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract, detect_query_type, QueryType

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 3
CONTEXT_LIMIT = 25000
TOP_K_RETRIEVAL = 80

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

MONTH_NAMES_REVERSE = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


# =============================================================================
# TEMPORAL GROUNDING (reused from grounded benchmark)
# =============================================================================

def resolve_temporal_expressions(text: str, reference_date: datetime) -> str:
    """Resolve relative temporal expressions to absolute dates."""
    if not reference_date:
        return text

    resolved = text
    ref_date = reference_date

    if re.search(r'\byesterday\b', text, re.IGNORECASE):
        yesterday = ref_date - timedelta(days=1)
        resolved = re.sub(
            r'\byesterday\b',
            f"yesterday [= {yesterday.day} {MONTH_NAMES_REVERSE[yesterday.month]} {yesterday.year}]",
            resolved,
            flags=re.IGNORECASE
        )

    if re.search(r'\btoday\b', text, re.IGNORECASE):
        resolved = re.sub(
            r'\btoday\b',
            f"today [= {ref_date.day} {MONTH_NAMES_REVERSE[ref_date.month]} {ref_date.year}]",
            resolved,
            flags=re.IGNORECASE
        )

    if re.search(r'\blast week\b', text, re.IGNORECASE):
        last_week = ref_date - timedelta(days=7)
        week_start = last_week - timedelta(days=last_week.weekday())
        resolved = re.sub(
            r'\blast week\b',
            f"last week [= week of {week_start.day} {MONTH_NAMES_REVERSE[week_start.month]} {week_start.year}]",
            resolved,
            flags=re.IGNORECASE
        )

    if re.search(r'\bthis week\b', text, re.IGNORECASE):
        week_start = ref_date - timedelta(days=ref_date.weekday())
        resolved = re.sub(
            r'\bthis week\b',
            f"this week [= week of {week_start.day} {MONTH_NAMES_REVERSE[week_start.month]} {week_start.year}]",
            resolved,
            flags=re.IGNORECASE
        )

    if re.search(r'\blast month\b', text, re.IGNORECASE):
        if ref_date.month == 1:
            last_month = ref_date.replace(year=ref_date.year - 1, month=12)
        else:
            last_month = ref_date.replace(month=ref_date.month - 1)
        resolved = re.sub(
            r'\blast month\b',
            f"last month [= {MONTH_NAMES_REVERSE[last_month.month]} {last_month.year}]",
            resolved,
            flags=re.IGNORECASE
        )

    if re.search(r'\blast year\b', text, re.IGNORECASE):
        last_year = ref_date.year - 1
        resolved = re.sub(
            r'\blast year\b',
            f"last year [= {last_year}]",
            resolved,
            flags=re.IGNORECASE
        )

    # X years ago
    year_ago_matches = re.findall(r'(\d+)\s+years?\s+ago', text, re.IGNORECASE)
    for match in year_ago_matches:
        years = int(match)
        target_year = ref_date.year - years
        resolved = re.sub(
            rf'{match}\s+years?\s+ago',
            f"{match} years ago [= {target_year}]",
            resolved,
            flags=re.IGNORECASE
        )

    return resolved


def parse_datetime(datetime_str: str) -> datetime | None:
    """Parse datetime string from LoCoMo format.

    LoCoMo format examples:
    - "1:56 pm on 8 May, 2023"
    - "4:04 pm on 20 January, 2023"
    - "11:01 am on 17 December, 2022"
    """
    if not datetime_str:
        return None

    # Try LoCoMo format first: "1:56 pm on 8 May, 2023"
    locomo_patterns = [
        r'(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+(\w+),?\s*(\d{4})',
    ]

    for pattern in locomo_patterns:
        match = re.match(pattern, datetime_str.strip(), re.IGNORECASE)
        if match:
            hour, minute, ampm, day, month_name, year = match.groups()
            hour = int(hour)
            minute = int(minute)
            day = int(day)
            year = int(year)

            # Handle AM/PM
            if ampm.lower() == 'pm' and hour != 12:
                hour += 12
            elif ampm.lower() == 'am' and hour == 12:
                hour = 0

            # Convert month name to number
            month = MONTH_NAMES.get(month_name.lower())
            if month:
                return datetime(year, month, day, hour, minute)

    # Fallback patterns
    patterns = [
        "%d %B %Y %H:%M",
        "%d %B %Y %I:%M%p",
        "%d %b %Y %H:%M",
        "%B %d, %Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ]
    for pattern in patterns:
        try:
            return datetime.strptime(datetime_str.strip(), pattern)
        except ValueError:
            continue
    return None


def extract_entities(text: str) -> list[str]:
    """Extract named entities from text."""
    entities = re.findall(r'\b[A-Z][a-z]+\b', text)
    common = {'I', 'The', 'A', 'An', 'It', 'This', 'That', 'We', 'They', 'He', 'She',
              'But', 'And', 'So', 'Yes', 'No', 'What', 'When', 'Where', 'Who', 'Why', 'How'}
    return list(set(e for e in entities if e not in common))


# =============================================================================
# DATA EXTRACTION
# =============================================================================

def extract_messages(item) -> list[dict]:
    """Extract messages from conversation."""
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1

    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if session_key not in conversation:
            break

        session_messages = conversation[session_key]
        datetime_str = conversation.get(datetime_key, "")

        for msg in session_messages:
            messages.append({
                "speaker": msg.get("speaker", "Unknown"),
                "text": msg.get("text", ""),
                "datetime": datetime_str,
                "session": session_idx,
            })
        session_idx += 1

    return messages


def extract_questions(item) -> list[dict]:
    """Extract questions from conversation."""
    qa_list = item.get("qa", [])  # Use "qa" not "qa_pairs"
    questions = []

    for qa in qa_list:
        category_id = qa.get("category", 1)
        category = CATEGORIES.get(category_id, "unknown")

        questions.append({
            "question": qa.get("question", ""),
            "answer": qa.get("answer", ""),
            "category": category,
            "category_id": category_id,
        })

    return questions


# =============================================================================
# LLM CALLS
# =============================================================================

async def get_embedding(session, text: str) -> list[float]:
    """Get embedding from Ollama."""
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


async def generate_answer(session, question: str, context: str, query_types: dict) -> str:
    """Generate answer using LLM with query-type-aware prompt."""
    # Determine primary query type for prompt customization
    primary_type = max(query_types.items(), key=lambda x: x[1])[0]

    # Type-specific instructions
    type_instructions = {
        QueryType.TEMPORAL: """Focus on dates, times, and sequences. Extract specific dates mentioned in the context.
If you see [= date] markers, those are RESOLVED dates - use them for your answer.""",
        QueryType.ENTITY: """Focus on facts about the person/entity mentioned. Look for attributes, characteristics, and specific information.
Pay special attention to what the person SAYS about themselves.""",
        QueryType.MULTI_HOP: """This requires combining multiple pieces of information. List out all relevant items, count them if asked.
Be thorough - look across ALL the context for relevant information.""",
        QueryType.ADVERSARIAL: """This may contain negation or exceptions. Pay careful attention to what did NOT happen or what is excluded.
Look for contradictions or alternative facts.""",
        QueryType.OPEN: """Provide a comprehensive answer based on all available information.""",
    }

    special_instruction = type_instructions.get(primary_type, type_instructions[QueryType.OPEN])

    prompt = f"""You are a memory recall assistant. Answer the question based ONLY on the provided memories.

RULES:
1. Only use information explicitly stated in the memories
2. If the exact answer is in the memories, give that specific answer
3. {special_instruction}
4. Be specific - give names, dates, places, not vague descriptions
5. If asked for a list or count, be exhaustive

MEMORIES:
{context}

QUESTION: {question}

Provide a direct, specific answer:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 200}
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            result = await response.json()
            return result.get("response", "").strip()
    except Exception as e:
        return f"Error: {e}"


async def judge_answer(session, question: str, generated: str, gold) -> tuple[bool, float]:
    """Judge if generated answer matches gold answer."""
    generated_lower = str(generated).lower().strip()
    gold_lower = str(gold).lower().strip()

    # Quick exact/substring match
    if gold_lower in generated_lower or generated_lower in gold_lower:
        return True, 1.0

    # Use LLM judge
    prompt = f"""Compare these two answers to the same question.

Question: {question}
Gold answer: {gold}
Generated answer: {generated}

Are they semantically equivalent? The generated answer is CORRECT if it conveys the same essential information as the gold answer, even if worded differently.

Respond with only: CORRECT or INCORRECT"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": JUDGE_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 10}
            },
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            response_text = result.get("response", "").strip().upper()
            is_correct = "CORRECT" in response_text and "INCORRECT" not in response_text
            return is_correct, 1.0 if is_correct else 0.0
    except Exception:
        return False, 0.0


# =============================================================================
# QUESTION PROCESSING WITH TESSERACT
# =============================================================================

async def process_question_tesseract(
    q: dict,
    http_session: aiohttp.ClientSession,
    tesseract: Tesseract,
    session_key: str,
) -> dict:
    """Process a single question using tesseract retrieval."""
    question = q["question"]
    gold_answer = q["answer"]
    category = q["category"]

    # Detect query type for routing
    query_types = detect_query_type(question)

    # Get question embedding
    query_embedding = await get_embedding(http_session, question)
    if not query_embedding:
        return {
            "question": question,
            "gold": gold_answer,
            "generated": "Error: Could not embed question",
            "category": category,
            "correct": False,
            "charge": 0.0,
            "query_types": query_types,
        }

    # Retrieve using TESSERACT
    results = await tesseract.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
    )

    # Build context from retrieved memories
    context_parts = []
    total_length = 0
    max_charge = 0.0

    for node, charge in results:
        if total_length >= CONTEXT_LIMIT:
            break

        # Get speaker info
        speaker = ""
        if node.metadata:
            speaker = node.metadata.get("producer_id", "") or node.metadata.get("speaker", "")

        # Format context entry
        if speaker:
            entry = f"[{speaker}]: {node.content}"
        else:
            entry = node.content

        context_parts.append(entry)
        total_length += len(entry)
        max_charge = max(max_charge, charge)

    context = "\n\n".join(context_parts)

    # Generate answer with query-type-aware prompt
    generated = await generate_answer(http_session, question, context, query_types)

    # Judge answer
    is_correct, _ = await judge_answer(http_session, question, generated, gold_answer)

    idx = q.get("_index", "?")
    status = "CORRECT" if is_correct else "WRONG"
    primary_type = max(query_types.items(), key=lambda x: x[1])[0]
    print(f"    [{idx}] {category} ({primary_type}): {status} (charge: {max_charge:.3f})")

    return {
        "question": question,
        "gold": gold_answer,
        "generated": generated,
        "category": category,
        "correct": is_correct,
        "charge": max_charge,
        "query_types": query_types,
        "nodes_retrieved": len(results),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_tesseract_benchmark(max_conversations: int = 10):
    """Run the tesseract benchmark."""
    print("=" * 70)
    print("LoCoMo TESSERACT Benchmark v1.0")
    print("4D BRAIN-INSPIRED MEMORY ARCHITECTURE")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Innovation: 4D Tesseract with specialized stores")
    print(f"Conversations: {max_conversations}")
    print("=" * 70)

    # Load data
    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    conversations = data[:max_conversations]
    print(f"\nProcessing {len(conversations)} conversations...")

    all_results = []
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, conversation in enumerate(conversations):
            # Get conversation name
            first_msg = extract_messages(conversation)[0] if extract_messages(conversation) else {}
            speaker1 = first_msg.get("speaker", "Person1")
            messages = extract_messages(conversation)

            # Find second speaker
            speakers = set(m.get("speaker", "") for m in messages)
            speaker2 = [s for s in speakers if s != speaker1]
            speaker2 = speaker2[0] if speaker2 else "Person2"

            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {speaker1} & {speaker2}")

            # Create storage and tesseract
            storage = InMemoryNeuralGraphStorage()
            tesseract = Tesseract(storage)

            session_key = f"conv_{conv_idx}"

            # Ingest messages with temporal grounding
            print(f"  Ingesting {len(messages)} messages with temporal grounding...")

            for msg_idx, msg in enumerate(messages):
                content = msg.get("text", "")
                speaker = msg.get("speaker", "Unknown")
                datetime_str = msg.get("datetime", "")

                # Parse reference datetime
                ref_datetime = parse_datetime(datetime_str) or datetime.now()

                # Apply temporal grounding
                grounded_content = resolve_temporal_expressions(content, ref_datetime)

                # Extract entities
                entities = extract_entities(grounded_content)

                # Get embedding
                embedding = await get_embedding(http_session, grounded_content)

                # Create node
                node = NeuralNode(
                    node_id=f"msg_{conv_idx}_{msg_idx}",
                    session_key=session_key,
                    content=grounded_content,
                    layer=NodeLayer.MESSAGE,
                    embedding=embedding,
                    created_at=ref_datetime,
                    metadata={
                        "producer_id": speaker,
                        "speaker": speaker,
                        "message_index": msg_idx,
                        "session": msg.get("session", 0),
                    },
                    entity_ids=entities,
                )

                await storage.save_node(node)

            print(f"  Ingested {len(messages)} grounded nodes")

            # Process questions
            questions = extract_questions(conversation)
            print(f"  Processing {len(questions)} questions...")

            for i, q in enumerate(questions):
                q["_index"] = i + 1

            # Process in batches
            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch = questions[batch_start:batch_start + CONCURRENT_QUESTIONS]

                tasks = [
                    process_question_tesseract(q, http_session, tesseract, session_key)
                    for q in batch
                ]

                batch_results = await asyncio.gather(*tasks)
                all_results.extend(batch_results)

                # Save incrementally
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                result_file = results_dir / f"locomo10_TESSERACT_{timestamp}.json"
                with open(result_file, "w") as f:
                    json.dump({
                        "results": all_results,
                        "timestamp": timestamp,
                        "config": {
                            "model": OLLAMA_MODEL,
                            "top_k": TOP_K_RETRIEVAL,
                            "context_limit": CONTEXT_LIMIT,
                            "approach": "TESSERACT_4D",
                        }
                    }, f, indent=2)

    # Final summary
    print("\n" + "=" * 70)
    print("TESSERACT BENCHMARK RESULTS")
    print("=" * 70)

    by_category = defaultdict(list)
    for r in all_results:
        by_category[r["category"]].append(r["correct"])

    total_correct = sum(r["correct"] for r in all_results)
    total = len(all_results)

    if total == 0:
        print("\nNo questions processed!")
        return all_results
    print(f"\nOverall: {total_correct}/{total} ({100*total_correct/total:.1f}%)")
    print("\nBy category:")
    for cat in ["temporal", "single_hop", "multi_hop", "open_domain", "adversarial"]:
        if cat in by_category:
            correct = sum(by_category[cat])
            total_cat = len(by_category[cat])
            print(f"  {cat}: {correct}/{total_cat} ({100*correct/total_cat:.1f}%)")

    # Save final results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = results_dir / f"locomo10_TESSERACT_{timestamp}.json"
    with open(result_file, "w") as f:
        json.dump({
            "results": all_results,
            "summary": {
                "overall": f"{total_correct}/{total}",
                "overall_pct": 100 * total_correct / total,
                "by_category": {
                    cat: f"{sum(by_category[cat])}/{len(by_category[cat])}"
                    for cat in by_category
                }
            },
            "config": {
                "model": OLLAMA_MODEL,
                "top_k": TOP_K_RETRIEVAL,
                "context_limit": CONTEXT_LIMIT,
                "approach": "TESSERACT_4D",
            }
        }, f, indent=2)

    print(f"\nResults saved to: {result_file}")
    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=1)
    args = parser.parse_args()

    asyncio.run(run_tesseract_benchmark(args.max_conversations))
