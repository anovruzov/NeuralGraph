"""
Simple benchmark runner that saves results with retrieved memories to JSON.

This benchmark uses the CORE temporal utilities from the memmachine system,
ensuring a fair evaluation that reflects actual production performance.

================================================================================
FAIRNESS & STABILITY GUARANTEES
================================================================================

1. NO SUBSTRING AUTO-PASS: The judge doesn't auto-pass on substring matches
   (e.g., gold="May 5", gen="not May 5" would incorrectly pass with substring).
   Only EXACT normalized matches auto-pass; everything else goes to LLM judge.

2. SAFE JSON PARSING: Judge response parsing properly handles "INCORRECT" vs
   "CORRECT" - won't match CORRECT as substring of INCORRECT.

3. DETERMINISTIC GENERATION: temperature=0 for all LLM calls ensures
   reproducible results across runs.

4. FAITHFUL TIMESTAMPS: created_at uses actual message_date (not datetime.now())
   so any recency/decay logic operates correctly.

5. PRODUCTION-EQUIVALENT STRUCTURE:
   The following features are used in this benchmark:
   - ENTITY edges (same-speaker message connections) - IN PRODUCTION
   - TEMPORAL edges (consecutive message links) - IN PRODUCTION
   - Dialogue Q/A aggregation expansion - IN PRODUCTION
   - Core temporal_utils (date resolution, tokens) - IN PRODUCTION

   To verify this is a fair test, these features should match what's shipped.
   If you add benchmark-only optimizations, document them here.

================================================================================
"""
import os
import json
import asyncio
import aiohttp
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NeuralEdge, NodeLayer, EdgeType, generate_edge_id
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract, detect_query_type, QueryType
from memmachine.neural_graph.dialogue_linker import DialogueLinker

# Import CORE temporal utilities - ensures benchmark uses production code
from memmachine.neural_graph.temporal_utils import (
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

# NOTE: resolve_relative_dates, generate_temporal_tokens, expand_temporal_query,
# and infer_query_mode are now imported from CORE memmachine.neural_graph.temporal_utils
# This ensures the benchmark tests the actual production code path.


def extract_keywords(text: str) -> list[str]:
    """Simple keyword extraction for indexing."""
    words = re.findall(r'\b[a-z]{3,}\b', text.lower())
    stopwords = {
        # Articles & determiners
        'the', 'a', 'an', 'this', 'that', 'these', 'those', 'some', 'any', 'each', 'every',
        # Pronouns
        'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', 'your', 'yours',
        'yourself', 'yourselves', 'he', 'him', 'his', 'himself', 'she', 'her', 'hers',
        'herself', 'it', 'its', 'itself', 'they', 'them', 'their', 'theirs', 'themselves',
        'what', 'which', 'who', 'whom', 'whose',
        # Prepositions
        'in', 'on', 'at', 'by', 'for', 'with', 'about', 'against', 'between', 'into',
        'through', 'during', 'before', 'after', 'above', 'below', 'to', 'from', 'up',
        'down', 'out', 'off', 'over', 'under', 'again', 'further', 'then', 'once',
        # Conjunctions
        'and', 'but', 'or', 'nor', 'so', 'yet', 'both', 'either', 'neither', 'not', 'only',
        'own', 'same', 'than', 'too', 'very', 'just', 'also',
        # Verbs (common/auxiliary)
        'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
        'having', 'do', 'does', 'did', 'doing', 'would', 'should', 'could', 'ought',
        'might', 'must', 'shall', 'will', 'can', 'need', 'dare', 'may', 'get', 'got',
        # Common words
        'all', 'one', 'two', 'more', 'most', 'other', 'into', 'when', 'where', 'why',
        'how', 'here', 'there', 'now', 'because', 'while', 'although', 'though',
        'if', 'unless', 'until', 'whether', 'since', 'such', 'no', 'yes', 'like',
        'well', 'back', 'even', 'still', 'way', 'take', 'come', 'make', 'know', 'think',
        'see', 'look', 'want', 'give', 'use', 'find', 'tell', 'ask', 'seem', 'feel',
        'try', 'leave', 'call', 'keep', 'let', 'begin', 'show', 'hear', 'play', 'run',
        'move', 'live', 'believe', 'hold', 'bring', 'happen', 'write', 'sit', 'stand',
        'lose', 'pay', 'meet', 'include', 'continue', 'set', 'learn', 'change', 'lead',
        'understand', 'watch', 'follow', 'stop', 'create', 'speak', 'read', 'spend',
        'grow', 'open', 'walk', 'win', 'teach', 'offer', 'remember', 'consider', 'appear',
        'buy', 'wait', 'serve', 'die', 'send', 'build', 'stay', 'fall', 'cut', 'reach',
        'kill', 'remain', 'suggest', 'raise', 'pass', 'sell', 'require', 'report',
        'decide', 'pull', 'develop', 'thank', 'okay', 'really', 'something', 'anything',
        'everything', 'nothing', 'someone', 'anyone', 'everyone', 'nobody',
    }
    return [w for w in words if w not in stopwords]

# Ollama for answer generation and embeddings (local)
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"          # For answer generation
EMBEDDING_MODEL = "nomic-embed-text"

# OpenAI for judging only (GPT-4o)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")  # set in your shell; never hardcode
JUDGE_MODEL = "gpt-4o"
TOP_K = 50  # MAXIMUS v2: Increased for better recall (was 30)

CATEGORIES = {1: "single_hop", 2: "temporal", 3: "open_domain", 4: "multi_hop", 5: "adversarial"}

OUTPUT_PATH = Path(__file__).parent / "final.json"  # v8.0: Fair benchmark with LoCoMo standard judge
MAX_QUESTIONS = 2000  # Full LoCoMo benchmark (1986 questions)

# Timing storage
import time
from collections import defaultdict
TIMING_DATA = defaultdict(list)

# =============================================================================
# BENCHMARK UTILITIES (evaluation-specific, not in core)
# =============================================================================
import random

ROUTING_STATS = {"TEMPORAL": 0, "STRICT": 0, "INFERENTIAL": 0, "AGGREGATION": 0, "ADVERSARIAL": 0}
ROUTING_SAMPLES = []  # Will hold up to 30 samples for instrumentation
INFERENTIAL_SAMPLES = []  # PATCH 4: Track inferential questions

# =============================================================================
# MAXIMUS v5: DIAGNOSTIC METRICS
# =============================================================================
RETRIEVAL_METRICS = {
    cat: {
        "recall_at_10": [],      # Was gold in top-10?
        "recall_at_50": [],      # Was gold in top-50?
        "oracle_rank": [],       # Rank where gold first appears (0 if not found)
        "filter_drop_count": [], # How many candidates dropped by filters
        "total_candidates": [],  # Total candidates before filtering
    }
    for cat in CATEGORIES.values()
}


def check_gold_in_memories_substring(gold_answer: str, memories: list, top_k: int = 10) -> tuple[bool, int]:
    """LEGACY: Substring-based check (fast but inaccurate for paraphrased answers)."""
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
    """v6.1: Qwen YES/NO oracle for accurate recall measurement.

    Instead of substring matching, ask Qwen: "Does this memory contain evidence
    that could answer the question with [gold_answer]?"

    This catches paraphrased answers that substring matching misses.
    """
    if not gold_answer:
        return False, 0

    # Check each memory with Qwen oracle
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
            # Fall back to substring on error
            gold_lower = str(gold_answer).lower()
            if gold_lower in mem_text.lower():
                return True, rank

    return False, 0


# Global flag to control oracle mode (expensive but accurate)
USE_QWEN_ORACLE = True  # v6.1: Enable Qwen oracle for recall


async def check_gold_in_memories(
    session, question: str, gold_answer: str, memories: list, top_k: int = 10
) -> tuple[bool, int]:
    """Unified interface: uses Qwen oracle if enabled, else substring."""
    if USE_QWEN_ORACLE and session is not None:
        return await check_gold_in_memories_oracle(session, question, gold_answer, memories, top_k)
    else:
        return check_gold_in_memories_substring(gold_answer, memories, top_k)

# NOTE: infer_query_mode is now imported from core memmachine.neural_graph.temporal_utils


def compute_retrieval_summary(metrics: dict) -> dict:
    """Compute summary statistics for retrieval metrics."""
    summary = {}
    for cat, data in metrics.items():
        if not data["recall_at_10"]:
            continue
        n = len(data["recall_at_10"])
        recall_10 = sum(data["recall_at_10"]) / n * 100 if n > 0 else 0
        recall_50 = sum(data["recall_at_50"]) / n * 100 if n > 0 else 0

        # Oracle ranks (non-zero only for found items)
        found_ranks = [r for r in data["oracle_rank"] if r > 0]
        avg_oracle_rank = sum(found_ranks) / len(found_ranks) if found_ranks else 0

        # Rerank gain = Recall@50 - Recall@10 (shows potential from better ranking)
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
    """Save current results to JSON file."""
    total_correct = sum(s["correct"] for s in stats.values())
    total_questions = sum(s["total"] for s in stats.values())
    accuracy = 100 * total_correct / total_questions if total_questions > 0 else 0

    # Compute retrieval metrics summary
    retrieval_summary = compute_retrieval_summary(RETRIEVAL_METRICS)

    # Compute latency metrics for both retrieval and answer generation
    latency_metrics = {}

    def compute_latency_stats(times_list, name):
        if not times_list:
            return {}
        times = sorted(times_list)
        n = len(times)
        return {
            f"{name}_count": n,
            f"{name}_mean_ms": round(sum(times) / n, 1),
            f"{name}_min_ms": round(min(times), 1),
            f"{name}_max_ms": round(max(times), 1),
            f"{name}_p50_ms": round(times[n // 2], 1),
            f"{name}_p90_ms": round(times[int(n * 0.9)], 1),
            f"{name}_p99_ms": round(times[int(n * 0.99)], 1) if n >= 100 else None,
            f"{name}_total_seconds": round(sum(times) / 1000, 2),
        }

    if "t_retrieval" in TIMING_DATA:
        latency_metrics.update(compute_latency_stats(TIMING_DATA["t_retrieval"], "retrieval"))
    if "t_answer" in TIMING_DATA:
        latency_metrics.update(compute_latency_stats(TIMING_DATA["t_answer"], "answer"))

    output = {
        "metadata": {
            "model": "Tesseract 4D Memory",
            "timestamp": datetime.now().isoformat(),
            "total_questions": total_questions,
            "total_correct": total_correct,
            "accuracy": round(accuracy, 2),
            "category_stats": {
                cat: {
                    "accuracy": round(100 * s["correct"] / s["total"], 1) if s["total"] > 0 else 0,
                    **s
                } for cat, s in stats.items()
            },
            "retrieval_metrics": retrieval_summary,
            "latency_metrics": latency_metrics,
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


# =============================================================================
# v6.1: QWEN RERANKER - Score memories 0-3 for relevance
# =============================================================================
USE_QWEN_RERANKER = True  # Enable neural reranking


async def qwen_rerank_memory(
    session, question: str, memory_text: str, speaker: str
) -> int:
    """Score a single memory 0-3 for relevance to the question.

    Scoring:
    3 = directly answers the question about the subject
    2 = strong supporting detail about the subject
    1 = weakly related or tangential
    0 = irrelevant or about a different person/topic
    """
    # Extract the subject entity from the question
    subject_match = re.search(r"(?:What|When|Where|Who|How|Does|Did|Is|Has|Have)\s+(?:is|does|did|has|have|was|were)?\s*([A-Z][a-z]+(?:'s)?)", question)
    subject = subject_match.group(1).rstrip("'s") if subject_match else ""

    prompt = f"""Score this memory's relevance to the question (0-3).

Question: {question}
Subject being asked about: {subject if subject else "unknown"}

Memory from [{speaker}]: {memory_text[:350]}

SCORING:
3 = Directly answers the question about {subject if subject else "the subject"}
2 = Strong supporting detail about {subject if subject else "the subject"}
1 = Weakly related or tangential information
0 = Irrelevant OR about a different person (wrong entity)

IMPORTANT: If the memory is from/about a DIFFERENT person than "{subject if subject else "the subject"}", score 0.

Reply with ONLY a single digit: 0, 1, 2, or 3"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 3}},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as response:
            result = await response.json()
            resp = result.get("response", "").strip()
            # Extract first digit
            for char in resp:
                if char in "0123":
                    return int(char)
            return 1  # Default to weakly related if parsing fails
    except Exception:
        return 1  # Default on error


async def qwen_rerank_batch(
    session, question: str, memories: list[tuple], top_n: int = 10
) -> list[tuple]:
    """Rerank top-50 memories using Qwen scoring, return top-N.

    This is the key to fixing R0→accuracy: even if retrieval finds the answer
    in position 30, the reranker can boost it to position 1.
    """
    if not USE_QWEN_RERANKER or not memories:
        return memories[:top_n]

    # Score each memory (limit to top 50 for speed)
    scored = []
    for node, charge in memories[:50]:
        speaker = node.metadata.get("speaker", "Unknown")
        score = await qwen_rerank_memory(session, question, node.content, speaker)
        # Combined score: reranker score * 10 + original charge (reranker dominates)
        combined_score = score * 10 + charge
        scored.append((node, combined_score, score))

    # Sort by combined score (reranker-dominant)
    scored.sort(key=lambda x: x[1], reverse=True)

    # Log reranking effect
    reranked_top3_scores = [s[2] for s in scored[:3]]
    if reranked_top3_scores:
        avg_top3 = sum(reranked_top3_scores) / len(reranked_top3_scores)
        if avg_top3 < 2:
            pass  # Could log: "Warning: top-3 after rerank have low relevance"

    # Return top-N with original charge structure
    return [(node, charge) for node, charge, _ in scored[:top_n]]


async def generate_answer(session, question: str, context: str, mode: str = "STRICT") -> str:
    """Generate answer with five-mode prompt routing (PATCH 5: EVIDENCE-FLEX v2).

    mode: "TEMPORAL", "AGGREGATION", "ADVERSARIAL", "INFERENTIAL", or "STRICT"
    """
    if mode == "TEMPORAL":
        # TEMPORAL prompt: resolve relative dates correctly
        prompt = f"""Answer the question using the memories below.

CRITICAL RULES:
1. If content contains [= resolved_date] brackets, USE THAT DATE AS THE ANSWER
   Example: "last week [= The week before 9 June 2023]" → answer is "The week before 9 June 2023"
2. If content says "yesterday/last week/last month" WITHOUT brackets, calculate from MESSAGE_DATETIME
3. For "when did X happen", find the event in the content and use the resolved date
4. DO NOT just output MESSAGE_DATETIME - use the RESOLVED DATE from [= ...] brackets
5. Answer with just the date/time period, keep it concise

MEMORIES:
{context}

QUESTION: {question}

Answer (extract the resolved date from [= ...] brackets):"""

    elif mode == "INFERENTIAL":
        # INFERENTIAL prompt (PATCH 5): AGGRESSIVE inference with evidence - the memories ARE sufficient
        prompt = f"""Answer the question using the memories below. This question requires INFERENCE.

=== INFERENCE RULES ===
1. You MUST make reasonable inferences from available evidence. The memories ARE sufficient.
2. Look for: stated interests, hobbies, careers, goals, personality traits, relationships, preferences
3. If Person X says "I collect classic children's books" then "Would X have Dr. Seuss books?" = YES
4. If Person X says "I want to be a counselor" and "I studied psychology", infer: "What field would X pursue?" = counseling/psychology
5. ANTI-SWAP RULE: Never attribute Person A's traits to Person B. Each person's info is theirs alone.
6. For YES/NO questions, COMMIT to an answer based on available evidence. Do NOT say "insufficient evidence" unless truly ZERO relevant memories.
7. Keep answer to 1-2 sentences. Be direct.

MEMORIES (ranked by relevance, first = most relevant):
{context}

QUESTION: {question}

Answer (make inferences from evidence, answer YES/NO questions definitively):"""

    elif mode == "AGGREGATION":
        # AGGREGATION prompt (PATCH 5): questions expecting multiple answers
        prompt = f"""Answer the question by AGGREGATING information from ALL memories below.

=== AGGREGATION RULES ===
1. This question expects MULTIPLE answers - find ALL relevant items
2. Scan EVERY memory for relevant information, not just the top one
3. Combine answers from different memories into a complete list
4. Present as comma-separated list when appropriate
5. If memories mention: beach, mountains, forest → answer: "beach, mountains, forest"
6. ANTI-SWAP RULE: Only aggregate items for the person being asked about

MEMORIES (scan ALL for relevant items):
{context}

QUESTION: {question}

Answer (aggregate ALL relevant items from ALL memories, comma-separated):"""

    elif mode == "ADVERSARIAL":
        # ADVERSARIAL prompt (PATCH 5): negation and unanswerable detection
        prompt = f"""Answer the question using ONLY the memories below.

=== ADVERSARIAL RULES ===
1. CRITICAL: If the information is NOT in the memories, say "Not mentioned in the memories" or "Information not available"
2. DO NOT make up facts or assume information exists
3. For "What did X NOT do?" - look for explicit statements about what X did, then identify what's missing
4. For negation questions, be precise about what IS and IS NOT stated
5. If the question assumes something not in evidence, REJECT the premise
6. When uncertain, say "Not mentioned" rather than guessing

MEMORIES:
{context}

QUESTION: {question}

Answer (if not explicitly in memories, say "Not mentioned"):"""

    else:
        # STRICT prompt (MAXIMUS v3): balanced extraction, speaker-aware
        prompt = f"""Answer the question using the memories below.

=== RULES ===
1. Speaker metadata shows WHO said what: [Name] = that person's message
2. For "What does X do?" - ONLY use facts stated by X or explicitly about X
3. ANTI-SWAP: If asked about Person A, never use Person B's info
4. Combine facts from multiple memories if they're all about the SAME person
5. SCAN ALL MEMORIES - the answer may be in any of them, not just the first
6. Keep answer SHORT: 1-2 sentences
7. Only say "not specified" if you've checked ALL memories and found nothing

MEMORIES (ranked by relevance):
{context}

QUESTION: {question}

Answer (check ALL memories, extract facts from correct person):"""

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

    # Method 2: Extract JSON from response (model might add explanation)
    try:
        json_match = re.search(r'\{[^}]+\}', resp)
        if json_match:
            obj = json_module.loads(json_match.group())
            label = str(obj.get("label", "")).strip().upper()
            return label == "CORRECT"
    except Exception:
        pass

    # Method 3: Regex on label field only (handles malformed JSON)
    m = re.search(r'"label"\s*:\s*"(CORRECT|WRONG)"', resp, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper() == "CORRECT"

    # Default: if we can't parse, it's WRONG (fail-safe)
    return False


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

UNANSWERABLE_PROMPT = """
Your task is to label an answer to an UNANSWERABLE question as 'CORRECT' or 'WRONG'.
The information requested does not exist in the memories, so the generated answer should indicate this.

Question: {question}
Generated answer: {generated_answer}

CORRECT if: The answer says the information is not available, unknown, cannot be determined, not mentioned, or similar.
WRONG if: The answer invents a specific fact or claims to know something that wasn't in the memories.

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"""


async def judge_answer(session, question: str, generated: str, gold) -> bool:
    """Judge whether generated answer matches gold answer using LoCoMo-standard prompt.

    FAIRNESS GUARANTEES:
    1. NO substring auto-pass (only exact match after normalization)
    2. Strict JSON parsing (won't confuse INCORRECT with CORRECT)
    3. Sufficient tokens (150) to avoid truncation
    4. Uses standard LoCoMo ACCURACY_PROMPT
    5. Fail-safe: unparseable responses default to WRONG
    """
    gen_lower = str(generated).lower().strip()
    gold_lower = str(gold).lower().strip()

    # ONLY exact match auto-pass (no substring matching!)
    if gold_lower and gen_lower == gold_lower:
        return True

    # Select prompt based on whether question is unanswerable
    if not gold_lower:
        prompt = UNANSWERABLE_PROMPT.format(
            question=question,
            generated_answer=generated
        )
    else:
        prompt = ACCURACY_PROMPT.format(
            question=question,
            gold_answer=gold,
            generated_answer=generated
        )

    try:
        async with session.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": JUDGE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 150  # Enough tokens to avoid truncation
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            result = await response.json()
            resp_text = result["choices"][0]["message"]["content"].strip()
            return parse_judge_label(resp_text)
    except Exception:
        return False  # Fail-safe: errors default to WRONG


async def run_benchmark():
    # Load LoCoMo data
    locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
    with open(locomo_path) as f:
        data = json.load(f)

    results = []
    stats = {cat: {"correct": 0, "total": 0} for cat in CATEGORIES.values()}

    async with aiohttp.ClientSession() as http:
        # Process ALL 10 conversations (1986 questions total)
        for conv_idx, conv in enumerate(data):
            print(f"\n{'='*60}")
            print(f"CONVERSATION {conv_idx + 1}")
            print(f"{'='*60}")

            # Extract messages
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

            # Build memory graph WITH EDGES
            storage = InMemoryNeuralGraphStorage()
            tesseract = Tesseract(storage)

            # Track nodes by speaker for ENTITY edge building
            speaker_nodes: dict[str, list[str]] = {}
            all_node_ids: list[str] = []

            # PASS 1: Create all nodes
            for msg_idx, msg in enumerate(messages):
                embedding = await get_embedding(http, msg["text"])
                if not embedding:
                    continue

                # Extract keywords at ingestion time
                keywords = extract_keywords(msg["text"])

                # Extract entities mentioned in content
                content_entities = set(re.findall(r'\b[A-Z][a-z]+\b', msg["text"]))
                content_entities.discard("I")  # Remove "I" as it's not an entity

                # v6.3: Generate temporal tokens for BM25 indexing
                message_date = parse_datetime_flexible(msg["datetime"])
                temporal_data = generate_temporal_tokens(message_date, msg["text"])
                date_tokens = temporal_data["date_tokens"]
                temporal_metadata = temporal_data["temporal_metadata"]

                # v7.0: Pre-resolve relative dates during ingestion
                # This ensures dates are computed ONCE with correct message datetime
                resolved_content, resolved_dates = resolve_relative_dates(msg["text"], message_date)

                # Append temporal tokens to content for BM25 searchability
                # This allows queries like "May 2023" to match MONTH_MAY YEAR_2023
                content_with_tokens = resolved_content  # Use resolved content!
                if date_tokens:
                    content_with_tokens += " " + " ".join(date_tokens)

                node_id = f"msg_{conv_idx}_{msg_idx}"
                node = NeuralNode(
                    node_id=node_id,
                    session_key=f"conv_{conv_idx}",
                    content=content_with_tokens,  # v7.0: Resolved content + temporal tokens
                    layer=NodeLayer.MESSAGE,
                    embedding=embedding,
                    created_at=message_date or datetime.now(),  # Use actual message time for faithful recency/decay
                    metadata={
                        "speaker": msg["speaker"],
                        "datetime": msg["datetime"],
                        "keywords": list(keywords),
                        "entities": list(content_entities),
                        "date_tokens": date_tokens,  # v6.3: For filtering
                        "temporal": temporal_metadata,  # v6.3: Structured date info
                        "original_content": msg["text"],  # Preserve original
                        "resolved_content": resolved_content,  # v7.0: Pre-resolved dates
                        "resolved_dates": resolved_dates,  # v7.0: Date metadata for filtering
                    },
                )
                await storage.save_node(node)
                all_node_ids.append(node_id)

                # Index by speaker
                speaker = msg["speaker"].lower()
                if speaker not in speaker_nodes:
                    speaker_nodes[speaker] = []
                speaker_nodes[speaker].append(node_id)

            # PASS 2: Build ENTITY edges - connect messages BY same speaker
            # This creates the neural pathways for entity retrieval
            edge_count = 0
            for speaker, node_ids in speaker_nodes.items():
                # Connect each message to the next few by same speaker
                for i, src_id in enumerate(node_ids):
                    # Connect to next 3 messages by same speaker
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

            # PASS 3: Build TEMPORAL edges - connect consecutive messages (dialogue turns)
            # This enables charge flow across dialogue exchanges
            temporal_edge_count = 0
            for i in range(len(all_node_ids) - 1):
                src_id = all_node_ids[i]
                tgt_id = all_node_ids[i + 1]
                edge = NeuralEdge(
                    edge_id=generate_edge_id(),
                    source_id=src_id,
                    target_id=tgt_id,
                    edge_type=EdgeType.TEMPORAL,
                    base_weight=0.85,  # Strong temporal links
                    metadata={"direction": "before"},
                )
                await storage.save_edge(edge)
                temporal_edge_count += 1

            # PASS 4: DIALOGUE LINKING - Create Q+A aggregators for atomic retrieval
            # This binds question-answer pairs so they're retrieved together
            dialogue_linker = DialogueLinker(storage)

            # Get all nodes for dialogue linking
            all_nodes = [await storage.get_node(nid) for nid in all_node_ids]
            all_nodes = [n for n in all_nodes if n is not None]

            # Process dialogue sequence to create exchange aggregators
            aggregators, bindings = await dialogue_linker.process_dialogue_sequence(
                all_nodes, f"conv_{conv_idx}"
            )

            print(f"Indexed {len(all_node_ids)} memories, {edge_count} entity + {temporal_edge_count} temporal edges")
            print(f"  Dialogue links: {len(aggregators)} aggregators, {len(bindings)} bindings")


            # Process questions
            qa_list = conv.get("qa", [])
            for q_idx, qa in enumerate(qa_list):
                # Napoleon exit: limited battlefield
                if len(results) >= MAX_QUESTIONS:
                    break

                category_id = qa.get("category", 1)
                category = CATEGORIES.get(category_id, "unknown")
                question = qa.get("question", "")
                gold = qa.get("answer", "")

                # Get query embedding
                query_emb = await get_embedding(http, question)
                if not query_emb:
                    continue

                # Retrieve memories - Tesseract now auto-expands temporal queries
                # via the core temporal_utils.expand_temporal_query function
                t_retrieval_start = time.perf_counter()
                retrieved = await tesseract.retrieve(
                    query_text=question,  # Tesseract handles temporal expansion internally
                    query_embedding=query_emb,
                    session_key=f"conv_{conv_idx}",
                    limit=TOP_K,
                    auto_expand_temporal=True,  # Uses core expand_temporal_query
                )
                t_retrieval_ms = (time.perf_counter() - t_retrieval_start) * 1000

                # DIALOGUE EXPANSION: Expand results through Q+A aggregators
                # If we retrieved a question, also get its answer (and vice versa)
                expanded_results = []
                seen_ids = set()
                for node, charge in retrieved:
                    if node.node_id not in seen_ids:
                        expanded_results.append((node, charge))
                        seen_ids.add(node.node_id)

                    # Get linked messages through dialogue aggregators
                    linked_ids = dialogue_linker.get_linked_messages(node.node_id)
                    for linked_id in linked_ids:
                        if linked_id not in seen_ids:
                            linked_node = await storage.get_node(linked_id)
                            if linked_node:
                                # Give linked messages slightly lower charge
                                linked_charge = charge * 0.85
                                expanded_results.append((linked_node, linked_charge))
                                seen_ids.add(linked_id)

                # Re-sort by charge and use expanded results
                expanded_results.sort(key=lambda x: x[1], reverse=True)
                retrieved = expanded_results

                # =============================================================
                # v6.1: DIAGNOSTIC METRICS WITH QWEN ORACLE
                # =============================================================
                # Build full text list for gold-checking
                all_memories_text = [
                    {"text": node.content, "speaker": node.metadata.get("speaker", "")}
                    for node, charge in retrieved[:50]
                ]

                # Recall@10: Was gold answer in top-10 memories? (Qwen oracle)
                recall_10, rank_10 = await check_gold_in_memories(
                    http, question, gold, all_memories_text, top_k=10
                )
                RETRIEVAL_METRICS[category]["recall_at_10"].append(1 if recall_10 else 0)

                # Recall@50: Was gold answer in top-50 memories? (Qwen oracle)
                # Only check @50 if not found in @10 (optimization)
                if recall_10:
                    recall_50, rank_50 = True, rank_10
                else:
                    recall_50, rank_50 = await check_gold_in_memories(
                        http, question, gold, all_memories_text, top_k=50
                    )
                RETRIEVAL_METRICS[category]["recall_at_50"].append(1 if recall_50 else 0)

                # Oracle rank: Where does gold first appear? (0 if not found)
                oracle_rank = rank_50 if recall_50 else 0
                RETRIEVAL_METRICS[category]["oracle_rank"].append(oracle_rank)

                # Total candidates (approximation - actual count comes from tesseract internal)
                RETRIEVAL_METRICS[category]["total_candidates"].append(len(retrieved))

                # v6.1: Qwen neural reranking for better precision
                # Rerank top-50 candidates, keep top-15
                if USE_QWEN_RERANKER:
                    reranked = await qwen_rerank_batch(http, question, retrieved[:50], top_n=15)
                else:
                    reranked = retrieved[:15]

                # Route query to appropriate mode
                query_mode = infer_query_mode(question)
                ROUTING_STATS[query_mode] += 1

                num_memories = 15
                context_parts = []
                retrieved_memories = []
                for node, charge in reranked[:num_memories]:
                    speaker = node.metadata.get("speaker", "Unknown")
                    dt = node.metadata.get("datetime", "")

                    # v7.0: Use pre-resolved content from metadata (computed at ingestion time)
                    # This ensures dates are resolved with CORRECT message datetime
                    resolved_content = node.metadata.get("resolved_content", node.metadata.get("original_content", node.content))

                    # Mode-aware context formatting
                    if query_mode == "TEMPORAL":
                        # TEMPORAL: datetime is prominent (authoritative)
                        # v6.3: Include date tokens for clarity
                        date_tokens = node.metadata.get("date_tokens", [])[:3]
                        token_str = f" [{' '.join(date_tokens)}]" if date_tokens else ""
                        context_parts.append(f"[MESSAGE_DATETIME={dt}]{token_str} [SPEAKER={speaker}] {resolved_content}")
                    else:
                        # STRICT/INFERENTIAL: content-first, speaker prominent, datetime trailing
                        context_parts.append(f"[{speaker}] {resolved_content}\n  (sent: {dt})")

                    retrieved_memories.append({
                        "speaker": speaker,
                        "text": resolved_content[:300],
                        "datetime": dt,
                        "charge": round(charge, 3),
                    })

                context = "\n".join(context_parts)

                # INSTRUMENTATION: Sample 30 random questions for routing log
                current_id = len(results) + 1
                if len(ROUTING_SAMPLES) < 30 and random.random() < 0.1:
                    ROUTING_SAMPLES.append({
                        "id": current_id,
                        "mode": query_mode,
                        "question": question[:60],
                        "context_preview": context[:200],
                    })

                # PATCH 4 INSTRUMENTATION: Sample 30 INFERENTIAL questions for debugging
                if query_mode == "INFERENTIAL" and len(INFERENTIAL_SAMPLES) < 30 and random.random() < 0.15:
                    INFERENTIAL_SAMPLES.append({
                        "id": current_id,
                        "category": category,
                        "question": question[:80],
                        "first_memory": context_parts[0][:150] if context_parts else "N/A",
                    })

                # Generate answer with LLM + timing
                t_answer_start = time.perf_counter()
                generated = await generate_answer(http, question, context, mode=query_mode)
                t_answer_ms = (time.perf_counter() - t_answer_start) * 1000
                TIMING_DATA["t_answer"].append(t_answer_ms)
                TIMING_DATA["t_retrieval"].append(t_retrieval_ms)

                # Judge
                correct = await judge_answer(http, question, generated, gold)

                # Update stats
                stats[category]["total"] += 1
                if correct:
                    stats[category]["correct"] += 1

                # Store result with per-question latencies
                result = {
                    "id": len(results) + 1,
                    "conversation": conv_idx + 1,
                    "category": category,
                    "question": question,
                    "generated_answer": generated,
                    "gold_answer": gold,
                    "correct": correct,
                    "retrieved_memories": retrieved_memories,
                    "latency_ms": {
                        "retrieval": round(t_retrieval_ms, 2),
                        "answer_generation": round(t_answer_ms, 2),
                        "total": round(t_retrieval_ms + t_answer_ms, 2),
                    },
                }
                results.append(result)

                # Print progress
                status = "OK" if correct else "FAIL"
                print(f"  [{status}] Q{q_idx+1} ({category}): {question[:50]}...")

                # Save incrementally every 10 questions
                if len(results) % 10 == 0:
                    save_results(results, stats)
                    print(f"    -> Saved {len(results)} results to file")

            # Napoleon exit at conversation level
            if len(results) >= MAX_QUESTIONS:
                print(f"\n*** NAPOLEON EXIT: {MAX_QUESTIONS} questions reached ***")
                break

    # Final save
    save_results(results, stats)

    # Print summary
    total_correct = sum(s["correct"] for s in stats.values())
    total_questions = sum(s["total"] for s in stats.values())
    accuracy = 100 * total_correct / total_questions if total_questions > 0 else 0

    print(f"\n{'='*60}")
    print(f"BENCHMARK COMPLETE")
    print(f"{'='*60}")
    print(f"Total: {total_correct}/{total_questions} ({accuracy:.1f}%)")
    for cat, s in stats.items():
        if s["total"] > 0:
            acc = 100 * s["correct"] / s["total"]
            print(f"  {cat}: {s['correct']}/{s['total']} ({acc:.1f}%)")

    # PATCH 4 INSTRUMENTATION: Three-mode routing stats
    print(f"\n{'='*60}")
    print(f"ROUTING STATS (EVIDENCE-FLEX)")
    print(f"{'='*60}")
    print(f"TEMPORAL:    {ROUTING_STATS['TEMPORAL']} questions")
    print(f"STRICT:      {ROUTING_STATS['STRICT']} questions")
    print(f"INFERENTIAL: {ROUTING_STATS['INFERENTIAL']} questions")
    print(f"\nSample routing decisions ({len(ROUTING_SAMPLES)} samples):")
    for sample in ROUTING_SAMPLES[:10]:
        print(f"  [{sample['mode']}] Q{sample['id']}: {sample['question']}...")

    # PATCH 4: INFERENTIAL samples for debugging inference questions
    print(f"\nINFERENTIAL prompt samples ({len(INFERENTIAL_SAMPLES)} samples):")
    for sample in INFERENTIAL_SAMPLES[:10]:
        print(f"  [{sample['category']}] Q{sample['id']}: {sample['question']}")
        print(f"    First memory: {sample['first_memory'][:100]}...")

    # =============================================================
    # MAXIMUS v5: RETRIEVAL METRICS SUMMARY
    # =============================================================
    print(f"\n{'='*60}")
    print(f"RETRIEVAL METRICS (MAXIMUS v5 - DIAGNOSTIC)")
    print(f"{'='*60}")
    print(f"{'Category':<15} {'Recall@10':<12} {'Recall@50':<12} {'RerankGain':<12} {'AvgOracleRank':<15}")
    print(f"{'-'*60}")

    retrieval_summary = compute_retrieval_summary(RETRIEVAL_METRICS)
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]:
        if cat in retrieval_summary:
            m = retrieval_summary[cat]
            print(f"{cat:<15} {m['recall_at_10']:>8.1f}%    {m['recall_at_50']:>8.1f}%    {m['rerank_gain']:>+8.1f}%    {m['avg_oracle_rank']:>10.1f}")

    # Summary insight
    print(f"\n*** KEY INSIGHT ***")
    sh_recall = retrieval_summary.get("single_hop", {}).get("recall_at_10", 0)
    if sh_recall < 75:
        print(f"CRITICAL: Single-hop Recall@10 = {sh_recall:.1f}% < 75%")
        print(f"  -> You will NEVER hit 82% accuracy without fixing retrieval!")
        print(f"  -> Focus on: keyword matching, speaker binding, BFS propagation")
    else:
        print(f"Single-hop Recall@10 = {sh_recall:.1f}% - retrieval is adequate")
        print(f"  -> Focus on: prompt engineering, LLM answer extraction")

    # =============================================================
    # LATENCY SUMMARY
    # =============================================================
    print(f"\n{'='*60}")
    print(f"LATENCY METRICS")
    print(f"{'='*60}")
    print(f"Model: {OLLAMA_MODEL}")
    print(f"\n{'Stage':<15} {'Median':<12} {'P90':<12} {'Count':<10}")
    print(f"{'-'*50}")

    if "t_answer" in TIMING_DATA and TIMING_DATA["t_answer"]:
        sorted_times = sorted(TIMING_DATA["t_answer"])
        n = len(sorted_times)
        median = sorted_times[n // 2]
        p90 = sorted_times[int(n * 0.90)] if n >= 10 else sorted_times[-1]
        print(f"{'t_answer':<15} {median:>8.0f}ms    {p90:>8.0f}ms    {n:>6}")

    print(f"\nResults saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
