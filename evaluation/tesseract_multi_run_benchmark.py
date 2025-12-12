"""
LoCoMo TESSERACT Multi-Run Benchmark
=====================================
Runs the full tesseract benchmark 10 times with:
- Shuffled conversation order each run
- Shuffled question order within each conversation
- Live monitoring dashboard with real-time stats
- Aggregated statistics across all runs

Total: 10 runs x 10 conversations x ~199 questions = ~19,860 evaluations
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import random
import requests
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
import threading

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
NUM_RUNS = 10

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
# LIVE MONITORING DASHBOARD
# =============================================================================

@dataclass
class LiveStats:
    """Track live statistics across all runs."""
    total_questions: int = 0
    correct_answers: int = 0
    questions_per_run: dict = field(default_factory=lambda: defaultdict(int))
    correct_per_run: dict = field(default_factory=lambda: defaultdict(int))
    by_category: dict = field(default_factory=lambda: defaultdict(lambda: {"correct": 0, "total": 0}))
    by_category_per_run: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(lambda: {"correct": 0, "total": 0})))
    start_time: float = field(default_factory=time.time)
    current_run: int = 1
    current_conv: int = 0
    current_question: int = 0
    errors: int = 0
    last_update: float = field(default_factory=time.time)

    # Per-question tracking for consistency analysis
    question_results: dict = field(default_factory=lambda: defaultdict(list))  # question_hash -> [results across runs]

    def update(self, run: int, category: str, correct: bool, question_hash: str = None):
        """Update stats with a new result."""
        self.total_questions += 1
        self.questions_per_run[run] += 1
        self.by_category[category]["total"] += 1
        self.by_category_per_run[run][category]["total"] += 1

        if correct:
            self.correct_answers += 1
            self.correct_per_run[run] += 1
            self.by_category[category]["correct"] += 1
            self.by_category_per_run[run][category]["correct"] += 1

        # Track per-question consistency
        if question_hash:
            self.question_results[question_hash].append(correct)

        self.last_update = time.time()

    def get_accuracy(self) -> float:
        if self.total_questions == 0:
            return 0.0
        return 100 * self.correct_answers / self.total_questions

    def get_qps(self) -> float:
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0.0
        return self.total_questions / elapsed

    def get_eta(self, total_expected: int) -> str:
        """Estimate time remaining."""
        if self.total_questions == 0:
            return "calculating..."

        qps = self.get_qps()
        if qps == 0:
            return "unknown"

        remaining = total_expected - self.total_questions
        seconds = remaining / qps

        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds/60:.1f}m"
        else:
            return f"{seconds/3600:.1f}h"

    def get_consistency_stats(self) -> dict:
        """Calculate how consistent answers are across runs."""
        if not self.question_results:
            return {"consistent": 0, "inconsistent": 0, "rate": 0.0}

        consistent = 0
        inconsistent = 0

        for q_hash, results in self.question_results.items():
            if len(results) < 2:
                continue
            # Question is consistent if all runs agree
            if all(results) or not any(results):
                consistent += 1
            else:
                inconsistent += 1

        total = consistent + inconsistent
        rate = 100 * consistent / total if total > 0 else 0

        return {"consistent": consistent, "inconsistent": inconsistent, "rate": rate}


def print_dashboard(stats: LiveStats, total_expected: int, force: bool = False):
    """Print live monitoring dashboard."""
    # Only update every 2 seconds unless forced
    if not force and time.time() - stats.last_update < 2:
        return

    elapsed = time.time() - stats.start_time
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    # Clear previous output
    print("\033[2J\033[H", end="")  # Clear screen and move cursor to top

    print("=" * 80)
    print("  TESSERACT MULTI-RUN BENCHMARK - LIVE MONITORING")
    print("=" * 80)
    print(f"  Run: {stats.current_run}/{NUM_RUNS}  |  Conv: {stats.current_conv}/10  |  Elapsed: {mins:02d}:{secs:02d}")
    print(f"  Progress: {stats.total_questions}/{total_expected} ({100*stats.total_questions/total_expected:.1f}%)")
    print(f"  Speed: {stats.get_qps():.2f} q/s  |  ETA: {stats.get_eta(total_expected)}")
    print("-" * 80)

    # Overall accuracy
    print(f"\n  OVERALL ACCURACY: {stats.correct_answers}/{stats.total_questions} ({stats.get_accuracy():.2f}%)")

    # Per-run breakdown
    print("\n  PER-RUN RESULTS:")
    run_line = "  "
    for run in range(1, NUM_RUNS + 1):
        if stats.questions_per_run[run] > 0:
            acc = 100 * stats.correct_per_run[run] / stats.questions_per_run[run]
            run_line += f"R{run}:{acc:.0f}%  "
        else:
            run_line += f"R{run}:--  "
    print(run_line)

    # Category breakdown
    print("\n  BY CATEGORY:")
    cat_order = ["temporal", "single_hop", "multi_hop", "open_domain", "adversarial"]
    for cat in cat_order:
        data = stats.by_category[cat]
        if data["total"] > 0:
            acc = 100 * data["correct"] / data["total"]
            bar_len = int(acc / 5)  # 20 char max bar
            bar = "#" * bar_len + "-" * (20 - bar_len)
            print(f"    {cat:12s}: {data['correct']:4d}/{data['total']:4d} ({acc:5.1f}%) [{bar}]")
        else:
            print(f"    {cat:12s}: waiting...")

    # Consistency stats (if we have multi-run data)
    consistency = stats.get_consistency_stats()
    if consistency["consistent"] + consistency["inconsistent"] > 0:
        print(f"\n  CONSISTENCY: {consistency['rate']:.1f}% ({consistency['consistent']} stable, {consistency['inconsistent']} variable)")

    # Error count
    if stats.errors > 0:
        print(f"\n  ERRORS: {stats.errors}")

    print("\n" + "=" * 80)
    sys.stdout.flush()


# =============================================================================
# TEMPORAL GROUNDING
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
    primary_type = max(query_types.items(), key=lambda x: x[1])[0]

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

    if gold_lower in generated_lower or generated_lower in gold_lower:
        return True, 1.0

    prompt = f"""You are evaluating a memory system's answer.

Question: {question}
Gold (expected) answer: {gold}
Generated answer: {generated}

Evaluation criteria (answer CORRECT if ANY apply):
1. FACTUAL MATCH: The generated answer contains the same core facts, even if worded differently or with extra details
2. PARTIAL CREDIT: If the gold answer has multiple items, credit is given if the generated answer includes at least the main item(s)
3. DATE FLEXIBILITY: Different date formats are equivalent ("May 7" = "7 May" = "May 7th 2023"). Relative dates ("last Tuesday", "the week before") are correct if they reference the same time period
4. VERBOSE OK: A longer answer that contains the gold answer's information is CORRECT, even with additional context

Answer CORRECT unless the generated answer is factually wrong, contradicts the gold answer, or completely misses the point.

Respond with ONLY one word: CORRECT or WRONG"""

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
            is_correct = "CORRECT" in response_text and "WRONG" not in response_text and "INCORRECT" not in response_text
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
    run_id: int,
    stats: LiveStats,
) -> dict:
    """Process a single question using tesseract retrieval."""
    question = q["question"]
    gold_answer = q["answer"]
    category = q["category"]

    # Create question hash for consistency tracking
    question_hash = hash(question + str(gold_answer))

    query_types = detect_query_type(question)

    query_embedding = await get_embedding(http_session, question)
    if not query_embedding:
        stats.errors += 1
        return {
            "question": question,
            "gold": gold_answer,
            "generated": "Error: Could not embed question",
            "category": category,
            "correct": False,
            "run_id": run_id,
        }

    results = await tesseract.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
    )

    context_parts = []
    total_length = 0
    max_charge = 0.0

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
        max_charge = max(max_charge, charge)

    context = "\n\n".join(context_parts)

    generated = await generate_answer(http_session, question, context, query_types)

    is_correct, _ = await judge_answer(http_session, question, generated, gold_answer)

    # Update live stats
    stats.update(run_id, category, is_correct, str(question_hash))

    return {
        "question": question,
        "gold": gold_answer,
        "generated": generated,
        "category": category,
        "correct": is_correct,
        "charge": max_charge,
        "run_id": run_id,
        "nodes_retrieved": len(results),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_single_benchmark(
    run_id: int,
    conversations: list,
    http_session: aiohttp.ClientSession,
    stats: LiveStats,
    results_dir: Path,
) -> list:
    """Run a single benchmark iteration with shuffled data."""
    run_results = []

    # Shuffle conversations for this run
    shuffled_convs = list(enumerate(conversations))
    random.shuffle(shuffled_convs)

    for conv_order, (orig_idx, conversation) in enumerate(shuffled_convs):
        stats.current_conv = conv_order + 1

        messages = extract_messages(conversation)
        if not messages:
            continue

        speaker1 = messages[0].get("speaker", "Person1")
        speakers = set(m.get("speaker", "") for m in messages)
        speaker2 = [s for s in speakers if s != speaker1]
        speaker2 = speaker2[0] if speaker2 else "Person2"

        # Create storage and tesseract
        storage = InMemoryNeuralGraphStorage()
        tesseract = Tesseract(storage)
        session_key = f"run{run_id}_conv{orig_idx}"

        # Ingest messages
        for msg_idx, msg in enumerate(messages):
            content = msg.get("text", "")
            speaker = msg.get("speaker", "Unknown")
            datetime_str = msg.get("datetime", "")

            ref_datetime = parse_datetime(datetime_str) or datetime.now()
            grounded_content = resolve_temporal_expressions(content, ref_datetime)
            entities = extract_entities(grounded_content)
            embedding = await get_embedding(http_session, grounded_content)

            node = NeuralNode(
                node_id=f"r{run_id}_msg_{orig_idx}_{msg_idx}",
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

        # Extract and shuffle questions
        questions = extract_questions(conversation)
        random.shuffle(questions)

        for i, q in enumerate(questions):
            q["_index"] = i + 1
            stats.current_question = i + 1

        # Process questions in batches
        for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
            batch = questions[batch_start:batch_start + CONCURRENT_QUESTIONS]

            tasks = [
                process_question_tesseract(q, http_session, tesseract, session_key, run_id, stats)
                for q in batch
            ]

            batch_results = await asyncio.gather(*tasks)
            run_results.extend(batch_results)

    return run_results


async def run_multi_benchmark(num_runs: int = NUM_RUNS):
    """Run the full multi-run benchmark."""
    # Load data
    locomo_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    total_questions = sum(len(c.get('qa', [])) for c in data)
    total_expected = total_questions * num_runs

    print(f"Starting TESSERACT Multi-Run Benchmark")
    print(f"Runs: {num_runs}")
    print(f"Conversations: {len(data)}")
    print(f"Questions per run: {total_questions}")
    print(f"Total evaluations: {total_expected}")
    print("=" * 80)

    stats = LiveStats()
    all_results = []

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    async with aiohttp.ClientSession() as http_session:
        for run_id in range(1, num_runs + 1):
            stats.current_run = run_id
            stats.current_conv = 0

            # Set different random seed for each run
            random.seed(run_id * 42)

            run_results = await run_single_benchmark(
                run_id, data, http_session, stats, results_dir
            )
            all_results.extend(run_results)

            # Print dashboard after each run
            print_dashboard(stats, total_expected, force=True)

            # Save intermediate results
            intermediate_file = results_dir / f"tesseract_multirun_{timestamp}_partial.json"
            with open(intermediate_file, "w") as f:
                json.dump({
                    "results": all_results,
                    "stats": {
                        "total_questions": stats.total_questions,
                        "correct_answers": stats.correct_answers,
                        "accuracy": stats.get_accuracy(),
                        "runs_completed": run_id,
                    },
                    "timestamp": timestamp,
                }, f, indent=2)

    # Final summary
    print("\n" + "=" * 80)
    print("  FINAL RESULTS - TESSERACT MULTI-RUN BENCHMARK")
    print("=" * 80)

    # Overall accuracy
    print(f"\n  OVERALL: {stats.correct_answers}/{stats.total_questions} ({stats.get_accuracy():.2f}%)")

    # Per-run breakdown
    print("\n  PER-RUN ACCURACY:")
    for run in range(1, num_runs + 1):
        if stats.questions_per_run[run] > 0:
            acc = 100 * stats.correct_per_run[run] / stats.questions_per_run[run]
            print(f"    Run {run}: {stats.correct_per_run[run]}/{stats.questions_per_run[run]} ({acc:.2f}%)")

    # Category breakdown
    print("\n  BY CATEGORY:")
    for cat in ["temporal", "single_hop", "multi_hop", "open_domain", "adversarial"]:
        data_cat = stats.by_category[cat]
        if data_cat["total"] > 0:
            acc = 100 * data_cat["correct"] / data_cat["total"]
            print(f"    {cat:12s}: {data_cat['correct']:5d}/{data_cat['total']:5d} ({acc:.2f}%)")

    # Consistency analysis
    consistency = stats.get_consistency_stats()
    print(f"\n  ANSWER CONSISTENCY: {consistency['rate']:.2f}%")
    print(f"    Stable answers: {consistency['consistent']}")
    print(f"    Variable answers: {consistency['inconsistent']}")

    # Calculate variance across runs
    run_accuracies = []
    for run in range(1, num_runs + 1):
        if stats.questions_per_run[run] > 0:
            acc = 100 * stats.correct_per_run[run] / stats.questions_per_run[run]
            run_accuracies.append(acc)

    if len(run_accuracies) > 1:
        mean_acc = sum(run_accuracies) / len(run_accuracies)
        variance = sum((x - mean_acc) ** 2 for x in run_accuracies) / len(run_accuracies)
        std_dev = variance ** 0.5
        print(f"\n  STATISTICAL ANALYSIS:")
        print(f"    Mean accuracy: {mean_acc:.2f}%")
        print(f"    Std deviation: {std_dev:.2f}%")
        print(f"    Range: {min(run_accuracies):.2f}% - {max(run_accuracies):.2f}%")

    # Save final results
    final_file = results_dir / f"tesseract_multirun_{timestamp}_FINAL.json"
    with open(final_file, "w") as f:
        json.dump({
            "results": all_results,
            "summary": {
                "total_questions": stats.total_questions,
                "correct_answers": stats.correct_answers,
                "overall_accuracy": stats.get_accuracy(),
                "per_run": {
                    run: {
                        "correct": stats.correct_per_run[run],
                        "total": stats.questions_per_run[run],
                        "accuracy": 100 * stats.correct_per_run[run] / stats.questions_per_run[run] if stats.questions_per_run[run] > 0 else 0
                    }
                    for run in range(1, num_runs + 1)
                },
                "by_category": {
                    cat: {
                        "correct": stats.by_category[cat]["correct"],
                        "total": stats.by_category[cat]["total"],
                        "accuracy": 100 * stats.by_category[cat]["correct"] / stats.by_category[cat]["total"] if stats.by_category[cat]["total"] > 0 else 0
                    }
                    for cat in stats.by_category
                },
                "consistency": consistency,
                "statistics": {
                    "mean_accuracy": mean_acc if len(run_accuracies) > 1 else stats.get_accuracy(),
                    "std_deviation": std_dev if len(run_accuracies) > 1 else 0,
                    "min_accuracy": min(run_accuracies) if run_accuracies else 0,
                    "max_accuracy": max(run_accuracies) if run_accuracies else 0,
                }
            },
            "config": {
                "model": OLLAMA_MODEL,
                "embedding_model": EMBEDDING_MODEL,
                "top_k": TOP_K_RETRIEVAL,
                "context_limit": CONTEXT_LIMIT,
                "num_runs": num_runs,
                "approach": "TESSERACT_4D_MULTIRUN",
            },
            "timestamp": timestamp,
        }, f, indent=2)

    print(f"\n  Results saved to: {final_file}")
    print("=" * 80)

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=NUM_RUNS, help="Number of benchmark runs")
    args = parser.parse_args()

    asyncio.run(run_multi_benchmark(args.runs))
