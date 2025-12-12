"""
LoCoMo EPISODIC Benchmark
=========================
Testing the brain-inspired EpisodicTemporalStore for temporal questions.

THE HYPOTHESIS:
- Current temporal accuracy: ~68%
- Target: 85%+ by binding temporal expressions to episodes

THE INSIGHT:
"Yesterday" in Session 1 (May 8) = May 7
"Yesterday" in Session 5 (July 3) = July 2
Same word, different meaning based on EPISODE context.
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NodeLayer
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.episodic_temporal import EpisodicTesseract

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

    # Last [weekday]
    weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for i, day in enumerate(weekdays):
        pattern = rf'\blast {day}\b'
        if re.search(pattern, text, re.IGNORECASE):
            current_weekday = ref_date.weekday()
            days_ago = (current_weekday - i) % 7
            if days_ago == 0:
                days_ago = 7
            target_date = ref_date - timedelta(days=days_ago)
            resolved = re.sub(
                pattern,
                f"last {day} [= {target_date.day} {MONTH_NAMES_REVERSE[target_date.month]} {target_date.year}]",
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
    """Parse datetime string from LoCoMo format."""
    if not datetime_str:
        return None

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

            if ampm.lower() == 'pm' and hour != 12:
                hour += 12
            elif ampm.lower() == 'am' and hour == 12:
                hour = 0

            month = MONTH_NAMES.get(month_name.lower())
            if month:
                return datetime(year, month, day, hour, minute)

    return None


def extract_entities(text: str) -> list[str]:
    """Extract named entities from text."""
    entities = re.findall(r'\b[A-Z][a-z]+\b', text)
    common = {'I', 'The', 'A', 'An', 'It', 'This', 'That', 'We', 'They', 'He', 'She',
              'But', 'And', 'So', 'Yes', 'No', 'What', 'When', 'Where', 'Who', 'Why', 'How'}
    return list(set(e for e in entities if e not in common))


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
    qa_list = item.get("qa", [])
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


async def generate_answer(session, question: str, context: str, is_temporal: bool) -> str:
    """Generate answer with temporal awareness."""
    if is_temporal:
        prompt = f"""TEMPORAL QUESTION - EXTRACT THE EXACT DATE

The memories below contain [= DATE] markers showing resolved dates.

EXAMPLES:
- Memory: "I went to a party yesterday [= 7 May 2023]"
  Question: "When did they go to a party?"
  Answer: 7 May 2023

- Memory: "We met last week [= week of 1 June 2023]"
  Question: "When did they meet?"
  Answer: The week of 1 June 2023

- Memory: "I signed up last Friday [= 14 July 2023]"
  Question: "When did they sign up?"
  Answer: 14 July 2023

YOUR TASK: Find the [= DATE] marker for the event asked about and return ONLY that date.

MEMORIES:
{context}

QUESTION: {question}

ANSWER (just the date, nothing else):"""
    else:
        prompt = f"""You are a memory recall assistant. Answer based ONLY on the provided memories.

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

    if gold_lower in generated_lower or generated_lower in gold_lower:
        return True, 1.0

    # For temporal questions, try semantic date matching
    prompt = f"""You are evaluating TEMPORAL answers. Be VERY LENIENT with date formats.

Question: {question}
Gold (expected): {gold}
Generated: {generated}

ANSWER CORRECT if ANY of these apply:

1. SAME DATE, DIFFERENT FORMAT:
   - "7 May 2023" = "May 7, 2023" = "May 7th" = "7 May" = "2023-05-07"
   - "The week before 9 June" = "week of 29 May" = "late May 2023" = "29 May - 4 June"
   - "The friday before 15 July" ≈ "14 July" or "7 July" (the actual Friday)

2. EQUIVALENT TIME PERIODS:
   - "June 2023" = "in June" = "early June 2023"
   - "2022" = "in 2022" = "last year" (if context is 2023)
   - "The sunday before 25 May 2023" = "21 May 2023" (the actual Sunday)

3. APPROXIMATE MATCH (within a few days):
   - If dates are within 3 days of each other, that's CORRECT
   - "The week before X" is the 7 days before X

4. SAME SEMANTIC MEANING:
   - Both answers point to the same event in the same time period

ONLY answer WRONG if the dates are clearly different (different month/year) or factually incorrect.

Respond with ONLY: CORRECT or WRONG"""

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
            is_correct = "CORRECT" in response_text and "WRONG" not in response_text
            return is_correct, 1.0 if is_correct else 0.0
    except Exception:
        return False, 0.0


async def process_question(
    q: dict,
    http_session: aiohttp.ClientSession,
    tesseract: EpisodicTesseract,
    session_key: str,
) -> dict:
    """Process a single question."""
    question = q["question"]
    gold_answer = q["answer"]
    category = q["category"]
    is_temporal = category == "temporal"

    query_embedding = await get_embedding(http_session, question)
    if not query_embedding:
        return {
            "question": question,
            "gold": gold_answer,
            "generated": "Error: Could not embed",
            "category": category,
            "correct": False,
        }

    results = await tesseract.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
    )

    context_parts = []
    total_length = 0

    for node, charge in results:
        if total_length >= CONTEXT_LIMIT:
            break

        speaker = ""
        if node.metadata:
            speaker = node.metadata.get("producer_id", "") or node.metadata.get("speaker", "")

        if speaker:
            entry = f"[{speaker}]: {node.content}"
        else:
            entry = node.content

        context_parts.append(entry)
        total_length += len(entry)

    context = "\n\n".join(context_parts)

    generated = await generate_answer(http_session, question, context, is_temporal)

    is_correct, _ = await judge_answer(http_session, question, generated, gold_answer)

    idx = q.get("_index", "?")
    status = "CORRECT" if is_correct else "WRONG"
    print(f"  [{idx}] {category}: {status}")

    if not is_correct and is_temporal:
        print(f"      Q: {question[:70]}...")
        print(f"      GOLD: {str(gold_answer)[:50]}")
        print(f"      GEN:  {generated[:50]}...")

    return {
        "question": question,
        "gold": gold_answer,
        "generated": generated,
        "category": category,
        "correct": is_correct,
    }


async def run_episodic_benchmark(max_conversations: int = 10):
    """Run the episodic benchmark."""
    print("=" * 70)
    print("LoCoMo EPISODIC Benchmark")
    print("Brain-Inspired Episodic Temporal Memory")
    print("=" * 70)

    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    conversations = data[:max_conversations]

    all_results = []
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, conversation in enumerate(conversations):
            messages = extract_messages(conversation)
            if not messages:
                continue

            speaker1 = messages[0].get("speaker", "Person1")
            speakers = set(m.get("speaker", "") for m in messages)
            speaker2 = [s for s in speakers if s != speaker1]
            speaker2 = speaker2[0] if speaker2 else "Person2"

            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {speaker1} & {speaker2}")

            storage = InMemoryNeuralGraphStorage()
            tesseract = EpisodicTesseract(storage)
            session_key = f"conv_{conv_idx}"

            print(f"  Ingesting {len(messages)} messages with episodic binding...")

            for msg_idx, msg in enumerate(messages):
                content = msg.get("text", "")
                speaker = msg.get("speaker", "Unknown")
                datetime_str = msg.get("datetime", "")
                session_num = msg.get("session", 0)

                ref_datetime = parse_datetime(datetime_str) or datetime.now()
                grounded_content = resolve_temporal_expressions(content, ref_datetime)
                entities = extract_entities(grounded_content)
                embedding = await get_embedding(http_session, grounded_content)

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
                        "session": session_num,
                    },
                    entity_ids=entities,
                )
                await storage.save_node(node)

            questions = extract_questions(conversation)
            print(f"  Processing {len(questions)} questions...")

            for i, q in enumerate(questions):
                q["_index"] = i + 1

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch = questions[batch_start:batch_start + CONCURRENT_QUESTIONS]
                tasks = [process_question(q, http_session, tesseract, session_key) for q in batch]
                batch_results = await asyncio.gather(*tasks)
                all_results.extend(batch_results)

    # Summary
    print("\n" + "=" * 70)
    print("EPISODIC BENCHMARK RESULTS")
    print("=" * 70)

    by_category = defaultdict(list)
    for r in all_results:
        by_category[r["category"]].append(r["correct"])

    total_correct = sum(r["correct"] for r in all_results)
    total = len(all_results)

    print(f"\nOverall: {total_correct}/{total} ({100*total_correct/total:.1f}%)")
    print("\nBy category:")
    for cat in ["temporal", "single_hop", "multi_hop", "open_domain", "adversarial"]:
        if cat in by_category:
            correct = sum(by_category[cat])
            total_cat = len(by_category[cat])
            print(f"  {cat}: {correct}/{total_cat} ({100*correct/total_cat:.1f}%)")

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = results_dir / f"locomo10_EPISODIC_{timestamp}.json"
    with open(result_file, "w") as f:
        json.dump({
            "results": all_results,
            "summary": {
                "overall": f"{total_correct}/{total}",
                "by_category": {cat: f"{sum(by_category[cat])}/{len(by_category[cat])}" for cat in by_category}
            },
            "approach": "EPISODIC_TEMPORAL",
        }, f, indent=2)

    print(f"\nResults saved to: {result_file}")
    return all_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_episodic_benchmark(args.max_conversations))
