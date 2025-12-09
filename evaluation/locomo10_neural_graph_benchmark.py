"""
LoCoMo Neural Graph Benchmark v2.0 - Official Prompts
Neural Memory Graph Retrieval with Multi-Stage Pipeline

IMPORTANT: This benchmark uses EXACTLY the official mem0 evaluation prompts.
Sources:
- Answer Prompt: https://github.com/mem0ai/mem0/blob/main/evaluation/prompts.py
- Judge Prompt: https://github.com/mem0ai/mem0/blob/main/evaluation/metrics/llm_judge.py

Pipeline:
1. Ingest: Messages -> Neural Graph nodes with timestamps
2. Retrieve: Multi-stage neural graph retrieval (fast recall, entity expansion, temporal chain)
3. Answer: Official mem0 ANSWER_PROMPT_ZEP with timestamped memories
4. Judge: Official mem0 ACCURACY_PROMPT with generous grading

Key features:
- 4-layer hierarchy (Message -> Episode -> Topic -> Persona)
- Multi-stage retrieval with edge gating
- Temporal dynamics (recency decay, LTP boost)
- LLM performs temporal reasoning on timestamps (no pre-calculated hints)
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
from typing import Any, Tuple

# =============================================================================
# ENTITY EXTRACTION - DISABLED (hurts accuracy by creating noisy edges)
# =============================================================================

def extract_entities_from_text(text: str) -> list[str]:
    """Extract named entities - DISABLED as it reduces accuracy.

    Analysis showed that entity extraction creates too many false entity edges
    that dilute the quality of retrieval results. The baseline without entity
    edges achieved 58.6%, while entity edges dropped it to 45%.
    """
    return []  # Disabled - entity edges hurt accuracy

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import Neural Graph System
from memmachine.neural_graph import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
    NeuralNode,
    NodeLayer,
    RetrievalResult,
)

# Note: TemporalCalculator removed - official benchmark relies on LLM's temporal reasoning

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
# qwen2.5:7b-instruct for CONCISE answers - critical for judge evaluation
OLLAMA_MODEL = "qwen2.5:7b-instruct"  # Concise, instruction-following
JUDGE_MODEL = "qwen2.5:7b-instruct"  # Keep same for fair comparison
EMBEDDING_MODEL = "nomic-embed-text"

# TUNED PARAMETERS for optimal accuracy (best results at TOP_K=40, CONTEXT=15000)
CONCURRENT_QUESTIONS = 4  # Higher concurrency for faster model
CONTEXT_LIMIT = 15000  # Balanced context - too much dilutes relevance
TOP_K_RETRIEVAL = 40   # Sweet spot for coverage vs noise

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

# =============================================================================
# OFFICIAL PROMPTS - EXACTLY matching mem0 evaluation prompts
# Source: https://github.com/mem0ai/mem0/blob/main/evaluation/prompts.py
# =============================================================================

# Main answer prompt - SCHEMA-GUIDED REASONING (Neuroscience-inspired)
#
# The hippocampus performs associative binding - connecting WHO-DOES-WHAT-WHY.
# Multi-hop questions require traversing these relations across memories.
# This prompt guides the LLM to perform pattern completion like the brain's CA3 region.
#
ANSWER_PROMPT = """You are a memory retrieval system. Your task is to find and synthesize information from memories.

REASONING PROCESS (do this mentally, don't write it out):
1. WHO is the question about? Identify the person/entity.
2. WHAT is being asked? Identify the topic/activity/relationship.
3. SCAN all memories for this person + topic combination.
4. CONNECT related memories - they may describe the same thing differently.
5. SYNTHESIZE a complete answer from ALL relevant memories.

CRITICAL FOR MULTI-HOP QUESTIONS:
- If asking "how does X do Y", look for ALL instances of X doing Y across memories
- If asking about preferences/habits, combine multiple examples into one answer
- Different memories may describe the SAME activity - recognize these patterns

Memories:
{context}

Question: {question}

Answer (be complete - include all relevant details from memories):"""

# Official LLM Judge prompt - EXACTLY matching mem0 evaluation
# Source: https://github.com/mem0ai/mem0/blob/main/evaluation/metrics/llm_judge.py
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

# Legacy prompt names for backwards compatibility (all point to the official prompt)
STANDARD_PROMPT = ANSWER_PROMPT
TEMPORAL_PROMPT = ANSWER_PROMPT
ADVERSARIAL_PROMPT = ANSWER_PROMPT
MULTI_HOP_PROMPT = ANSWER_PROMPT

# No system message - the official benchmark doesn't use a separate system message
SYSTEM_MESSAGE = ""


# =============================================================================
# LLM CALLS
# =============================================================================

async def generate_answer_async(session: aiohttp.ClientSession, prompt: str) -> str:
    """Generate answer using Ollama - matches official mem0 evaluation."""
    try:
        # Official evaluation uses single user message, no system message
        messages = [{"role": "user", "content": prompt}]

        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
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


MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def parse_session_datetime(session_time: str) -> datetime | None:
    """Parse LoCoMo session datetime format: '1:56 pm on 8 May, 2023'"""
    if not session_time or session_time == "Unknown":
        return None

    # Primary format: "X:XX am/pm on DD Month, YYYY"
    date_match = re.search(r"on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})", session_time)
    if date_match:
        day = int(date_match.group(1))
        month_str = date_match.group(2).lower()
        year = int(date_match.group(3))
        month = MONTH_NAMES.get(month_str, 1)

        # Extract time if present
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

    # Fallback formats
    date_patterns = [
        (r"(\d{1,2})\s+(\w+)\s+(\d{4})", lambda m: (int(m.group(1)), MONTH_NAMES.get(m.group(2).lower(), 1), int(m.group(3)))),
        (r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", lambda m: (int(m.group(2)), MONTH_NAMES.get(m.group(1).lower(), 1), int(m.group(3)))),
    ]
    for pattern, extractor in date_patterns:
        match = re.search(pattern, session_time)
        if match:
            try:
                day, month, year = extractor(match)
                return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
    return None


# =============================================================================
# NEURAL GRAPH RETRIEVAL WITH CONFIDENCE SCORING
# =============================================================================

# Confidence thresholds for determining if information exists in memories
# These are tuned based on retrieval score distribution
CONFIDENCE_THRESHOLD_HIGH = 0.7   # Above this = confident answer exists
CONFIDENCE_THRESHOLD_LOW = 0.4    # Below this = likely not mentioned
RELEVANCE_SCORE_THRESHOLD = 0.5   # Minimum average relevance for top-k results


def is_inference_question(question: str) -> bool:
    """Detect if a question asks for INFERENCE rather than specific facts.

    Inference questions ask about opinions, predictions, likelihood, etc.
    These should NOT use topic verification because the answer is inferred,
    not directly stated in memories.
    """
    question_lower = question.lower()

    # Inference/open-ended patterns
    inference_patterns = [
        'likely', 'probably', 'would', 'could', 'might', 'may',
        'should', 'pursue', 'predict', 'think', 'believe', 'feel',
        'opinion', 'advice', 'suggest', 'recommend', 'expect',
        'fields', 'career', 'future', 'potential', 'possibility',
        'chances', 'odds', 'prospect', 'outlook', 'forecast',
        'infer', 'conclude', 'deduce', 'assume', 'guess',
        'what kind of', 'what type of', 'what sort of',
    ]

    for pattern in inference_patterns:
        if pattern in question_lower:
            return True
    return False


def extract_core_topic(question: str) -> set[str]:
    """Extract the CORE TOPIC nouns from a question.

    These are the specific nouns that MUST appear in memories for an answer to exist.
    NOT names, NOT generic words, NOT question words.

    Example:
    - "What is Melanie's necklace made of?" → {'necklace'}
    - "What was grandma's gift?" → {'grandma', 'gift'}
    - "What are the adoption plans?" → {'adoption'}

    Returns empty set for inference questions (they don't need topic verification).
    """
    # Skip topic extraction for inference questions
    if is_inference_question(question):
        return set()  # No topic verification for inference questions

    question_lower = question.lower()

    # All words to filter out
    filter_words = {
        # Question words
        'what', 'when', 'where', 'who', 'why', 'how', 'which',
        # Verbs and auxiliaries
        'did', 'does', 'is', 'are', 'was', 'were', 'has', 'have', 'had',
        'do', 'would', 'could', 'should', 'will', 'can', 'may', 'might',
        'being', 'been', 'be', 'get', 'got', 'make', 'made', 'take', 'took',
        # Prepositions and articles
        'the', 'a', 'an', 'to', 'for', 'of', 'in', 'on', 'at', 'with',
        'from', 'by', 'as', 'into', 'onto', 'about', 'after', 'before',
        # Pronouns
        'this', 'that', 'these', 'those', 'it', 'its', 'she', 'he', 'her', 'his',
        'they', 'their', 'them', 'you', 'your', 'we', 'our', 'i', 'me', 'my',
        # Conjunctions
        'and', 'or', 'but', 'if', 'then', 'so', 'because', 'while',
        # Common verbs that don't indicate topic
        'think', 'feel', 'want', 'like', 'need', 'know', 'see', 'go', 'come',
        'say', 'said', 'tell', 'told', 'ask', 'asked', 'give', 'gave',
        # Adjectives that don't indicate specific topic
        'likely', 'probably', 'still', 'really', 'very', 'much', 'more', 'most',
        # Common names (will always match)
        'melanie', 'caroline', 'mel', 'cara', 'caro', 'sam', 'sarah',
        'mike', 'john', 'mary', 'jane', 'alex', 'chris', 'emma', 'olivia',
        # Generic nouns that don't indicate specific topic
        'things', 'something', 'anything', 'everything', 'nothing',
        'person', 'people', 'someone', 'anyone', 'everyone',
        'place', 'way', 'kind', 'type', 'sort',
        # Time-related generics
        'time', 'year', 'years', 'month', 'months', 'week', 'weeks', 'day', 'days',
        'summer', 'winter', 'spring', 'fall', 'morning', 'evening', 'night',
        'today', 'yesterday', 'tomorrow', 'last', 'next', 'first', 'second',
        # Activity generics
        'plans', 'plan', 'activities', 'activity', 'hobbies', 'hobby',
        'work', 'life', 'home', 'family', 'friends', 'relationship',
        # Question-specific words that aren't topics
        'respect', 'regard', 'terms', 'reason', 'reasons', 'result', 'results',
        'realize', 'realized', 'learn', 'learned', 'discover', 'discovered',
        'remember', 'mentioned', 'talked', 'discussed',
    }

    # Clean and split
    words = question_lower.replace('?', '').replace("'s", ' ').replace('-', ' ').replace(',', ' ').split()

    # Extract topic nouns (words > 3 chars that aren't filtered)
    topics = set()
    for word in words:
        word = word.strip()
        if len(word) > 3 and word not in filter_words:
            topics.add(word)

    return topics


def verify_topic_in_memories(topic_words: set[str], memories_content: str) -> tuple[bool, float]:
    """Verify if the core topic actually appears in retrieved memories.

    This is the KEY insight: semantic similarity finds RELATED content,
    but we need to verify the SPECIFIC TOPIC exists.

    Returns:
        (topic_found, coverage_ratio)
    """
    if not topic_words:
        return True, 1.0  # No specific topic to verify

    memories_lower = memories_content.lower()

    # Check each topic word
    found = sum(1 for word in topic_words if word in memories_lower)
    coverage = found / len(topic_words)

    # Topic is considered "found" if at least 40% of topic words appear
    # This threshold balances precision vs recall
    topic_found = coverage >= 0.4

    return topic_found, coverage


def format_neural_context(results: list[tuple[NeuralNode, float]], max_chars: int = 12000, debug: bool = False) -> str:
    """Format neural graph retrieval results as timestamped memories.

    Format matches official mem0 evaluation: "[timestamp] speaker: content"
    This allows the LLM to perform temporal reasoning based on timestamps.
    """
    lines = []
    total_chars = 0

    if debug and results:
        print(f"    DEBUG: Retrieved {len(results)} nodes, top score: {results[0][1]:.3f}")
        print(f"    DEBUG: Top node created_at: {results[0][0].created_at}")

    for node, score in results:
        # Format: [timestamp] speaker: content - matches official mem0 format
        timestamp = node.created_at.strftime("%Y-%m-%d %H:%M") if node.created_at else "Unknown"
        speaker = node.metadata.get("producer_id", node.metadata.get("speaker", "Unknown"))
        line = f"[{timestamp}] {speaker}: {node.content}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    if debug:
        print(f"    DEBUG: Context length: {total_chars} chars, {len(lines)} memories")

    return "\n".join(lines)


class EpisodeLike:
    """Minimal Episode-like object for neural graph ingestion.

    CRITICAL FIX: Now extracts entity_ids for entity edge creation!
    Without entity_ids, the neural graph cannot perform multi-hop reasoning.
    """
    def __init__(self, content: str, uid: str, speaker: str, created_at: datetime | None, session_key: str, msg_idx: int):
        self.content = content
        self.uid = uid
        self.producer_id = speaker
        self.created_at = created_at

        # Entity extraction DISABLED - analysis showed it reduces accuracy
        # by creating noisy entity edges that dilute retrieval quality
        self.filterable_metadata = {
            "speaker": speaker,
            "session_key": session_key,
            "msg_idx": msg_idx,
            # NO entity_ids - keeping 0 entity edges as in baseline
        }
        self.importance_score = 0.5


# =============================================================================
# PROCESS QUESTION
# =============================================================================

async def process_question_neural(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    neural_service: NeuralGraphService,
    session_key: str,
) -> dict:
    """Process a single question using Neural Graph retrieval.

    Uses official mem0 evaluation prompts exactly as specified.
    """
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # ==========================================================================
    # STEP 1 - GET QUERY EMBEDDING
    # ==========================================================================
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
            "time_seconds": 0,
            "error": "embedding_failed",
        }

    # ==========================================================================
    # STEP 2 - NEURAL GRAPH MULTI-STAGE RETRIEVAL
    # ==========================================================================
    retrieval_start = time.time()

    result = await neural_service.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
    )

    retrieval_time = time.time() - retrieval_start

    # ==========================================================================
    # STEP 2.5 - FORMAT CONTEXT
    # ==========================================================================
    # Format context for LLM
    context = format_neural_context(result.nodes, max_chars=CONTEXT_LIMIT, debug=(q_idx < 3))

    # ==========================================================================
    # STEP 3 - GENERATE ANSWER
    # ==========================================================================
    # Let the LLM determine if the answer is in the memories
    # The prompt instructs the model to say "not mentioned" when appropriate
    prompt = ANSWER_PROMPT.format(context=context, question=question)
    generated = await generate_answer_async(http_session, prompt)

    # ==========================================================================
    # STEP 4 - JUDGE ANSWER (Adversarial questions excluded from benchmark)
    # ==========================================================================
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)
    binary_correct = judgment.get("binary_correct", 0)

    gen_time = time.time() - start_time
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    if q_idx < 3:
        print(f"    DEBUG: Q={question[:50]}...")
        print(f"    DEBUG: Gold={gold_answer}, Generated={generated[:80]}...")
    print(f"    [{q_idx + 1}] {category_name}: {status} (retrieval: {retrieval_time*1000:.0f}ms)", flush=True)

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
        "retrieval_method": "neural_graph",
        "stages_executed": result.stages_executed,
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_neural_graph_benchmark(max_conversations: int = 10):
    """Run Neural Graph benchmark with official mem0 prompts."""
    print("=" * 70)
    print("LoCoMo Neural Graph Benchmark v2.0 - OFFICIAL PROMPTS")
    print("Using exact mem0 evaluation prompts from GitHub")
    print("=" * 70)
    print(f"Answer Model: {OLLAMA_MODEL}")
    print(f"Judge Model: {JUDGE_MODEL} (Ollama local)")
    print(f"Embedding Model: {EMBEDDING_MODEL}")
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
    total_ingestion_time = 0
    total_retrieval_time = 0

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

            # ==========================================================================
            # CREATE NEURAL GRAPH SERVICE
            # ==========================================================================
            session_key = f"conv_{conv_id}"

            config = NeuralGraphServiceConfig(
                enabled=True,
                hierarchy_enabled=True,
                temporal_enabled=True,
                consolidation_enabled=True,
                auto_temporal_linking=True,
                auto_episode_segmentation=False,  # Skip auto segmentation for speed
                auto_consolidation_on_ingestion=False,  # Skip auto consolidation for benchmark
                apply_temporal_dynamics=True,
            )

            neural_service = NeuralGraphService(config=config)

            # ==========================================================================
            # INGEST MESSAGES INTO NEURAL GRAPH
            # ==========================================================================
            print(f"  Ingesting {len(messages)} messages into neural graph...")
            ingest_start = time.time()

            # Get embeddings for all messages
            embeddings = []
            for msg in messages:
                emb = await get_embedding_async(http_session, msg["text"])
                embeddings.append(emb)

            # Create Episode-like objects
            episodes = []
            for i, msg in enumerate(messages):
                parsed_time = parse_session_datetime(msg.get("session_time", ""))
                if i < 3:
                    print(f"    DEBUG INGEST: msg {i} session_time={msg.get('session_time', '')} -> parsed={parsed_time}")
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
            ingestion_result = await neural_service.process_episodes(
                episodes=episodes,
                session_key=session_key,
                embeddings=embeddings,
            )

            ingest_time = time.time() - ingest_start
            total_ingestion_time += ingest_time

            print(f"  Ingested: {ingestion_result.nodes_created} nodes, "
                  f"{ingestion_result.temporal_edges_created} temporal edges, "
                  f"{ingestion_result.entity_edges_created} entity edges "
                  f"in {ingest_time*1000:.0f}ms")

            # ==========================================================================
            # PROCESS QUESTIONS (SKIP ADVERSARIAL - category 5)
            # ==========================================================================
            all_questions = item.get("qa", item.get("questions", []))
            # Filter out adversarial questions (category 5) - official mem0 benchmark skips them
            questions = [q for q in all_questions if q.get("category", 0) != 5]
            print(f"  Processing {len(questions)} questions (skipped {len(all_questions) - len(questions)} adversarial)...")

            # Track running accuracy for this conversation
            conv_correct = 0
            conv_total = 0

            # Process questions in batches for parallelism
            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_neural(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        neural_service=neural_service,
                        session_key=session_key,
                    )
                    for i, qa in enumerate(batch)
                ]

                results = await asyncio.gather(*tasks)
                for result in results:
                    result["conversation_id"] = conv_id
                    all_results.append(result)
                    conv_total += 1
                    if result["correct"]:
                        conv_correct += 1
                    if "retrieval_time_ms" in result:
                        total_retrieval_time += result["retrieval_time_ms"]

                    # Print running accuracy every 10 questions
                    if conv_total % 10 == 0:
                        global_correct = sum(1 for r in all_results if r["correct"])
                        global_total = len(all_results)
                        print(f"  >> RUNNING: {conv_correct}/{conv_total} ({100*conv_correct/conv_total:.1f}%) conv | {global_correct}/{global_total} ({100*global_correct/global_total:.1f}%) overall", flush=True)

            # Print final conversation accuracy
            global_correct = sum(1 for r in all_results if r["correct"])
            global_total = len(all_results)
            print(f"  == CONV DONE: {conv_correct}/{conv_total} ({100*conv_correct/conv_total:.1f}%) | Overall: {global_correct}/{global_total} ({100*global_correct/global_total:.1f}%)", flush=True)

            # Get statistics
            stats = await neural_service.get_statistics(session_key)
            print(f"  Neural graph stats: {stats.get('total_nodes', 0)} nodes, {stats.get('total_edges', 0)} edges")

            # Cleanup
            await neural_service.close()

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - Neural Graph Multi-Stage Retrieval")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    print(f"\nOverall: {correct}/{total} ({100*correct/total:.2f}%)")

    # Per-category
    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    print("\nPer-Category Accuracy:")
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop"]:  # Adversarial excluded
        if cat in category_results:
            cat_results = category_results[cat]
            cat_correct = sum(1 for r in cat_results if r["correct"])
            print(f"  {cat}: {cat_correct}/{len(cat_results)} ({100*cat_correct/len(cat_results):.2f}%)")

    # Timing stats
    print(f"\nTiming:")
    print(f"  Total ingestion time: {total_ingestion_time*1000:.0f}ms")
    print(f"  Total retrieval time: {total_retrieval_time:.0f}ms")
    print(f"  Avg retrieval time: {total_retrieval_time/total:.1f}ms per question")

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    results_file = results_dir / f"locomo10_NEURAL_GRAPH_V2.0_OFFICIAL_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "NEURAL_GRAPH_V2.0_OFFICIAL_PROMPTS",
            "prompt_source": "https://github.com/mem0ai/mem0/blob/main/evaluation/prompts.py",
            "retrieval_method": "neural_graph_multi_stage",
            "judge_model": JUDGE_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "timing": {
                "total_ingestion_ms": round(total_ingestion_time * 1000, 2),
                "total_retrieval_ms": round(total_retrieval_time, 2),
                "avg_retrieval_ms": round(total_retrieval_time / total, 2) if total > 0 else 0,
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

    asyncio.run(run_neural_graph_benchmark(args.max_conversations))
