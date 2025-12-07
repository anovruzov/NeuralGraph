"""
LoCoMo-PARALLEL Benchmark v1.0
Parallel processing for faster execution using asyncio + concurrent requests.

Key improvements:
1. Parallel embedding computation (batch)
2. Concurrent question processing within conversations
3. Async HTTP requests
"""

import json
import asyncio
import aiohttp
import time
import re
import math
from datetime import datetime
from pathlib import Path
from collections import Counter
import numpy as np

# Ollama Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

# Parallel processing settings
CONCURRENT_QUESTIONS = 4  # Process 4 questions simultaneously
EMBEDDING_BATCH_SIZE = 10  # Embed 10 messages at once

# Categories mapping
CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

# Benchmark parameters
CONTEXT_LIMIT = 12000
TOP_K_RETRIEVAL = 30
CORRECT_THRESHOLD = 8
PARTIAL_THRESHOLD = 5

# Stopwords for BM25
STOPWORDS = set([
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she",
    "her", "hers", "herself", "it", "its", "itself", "they", "them", "their",
    "theirs", "themselves", "what", "which", "who", "whom", "this", "that",
    "these", "those", "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an",
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of",
    "at", "by", "for", "with", "about", "against", "between", "into", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down",
    "in", "out", "on", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just",
    "don", "should", "now", "d", "ll", "m", "o", "re", "ve", "y", "ain"
])


def simple_tokenize(text: str) -> list[str]:
    tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def compute_bm25_scores(query: str, documents: list[str], k1=1.5, b=0.75) -> list[float]:
    query_tokens = simple_tokenize(query)
    if not query_tokens:
        return [0.0] * len(documents)

    doc_tokens = [simple_tokenize(doc) for doc in documents]
    doc_lengths = [len(tokens) for tokens in doc_tokens]
    avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1

    N = len(documents)
    idf = {}
    for term in set(query_tokens):
        df = sum(1 for tokens in doc_tokens if term in tokens)
        idf[term] = math.log((N - df + 0.5) / (df + 0.5) + 1)

    scores = []
    for i, tokens in enumerate(doc_tokens):
        score = 0.0
        term_freq = Counter(tokens)
        doc_len = doc_lengths[i]
        for term in query_tokens:
            if term in term_freq:
                tf = term_freq[term]
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * doc_len / avg_doc_length)
                score += idf.get(term, 0) * (numerator / denominator)
        scores.append(score)
    return scores


def cosine_similarity(vec1, vec2) -> float:
    if vec1 is None or vec2 is None:
        return 0.0
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    dot = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


# =============================================================================
# ASYNC FUNCTIONS
# =============================================================================

async def get_embedding_async(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Get embedding asynchronously."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding")
    except Exception as e:
        print(f"    Embed error: {e}", flush=True)
        return None


async def generate_answer_async(session: aiohttp.ClientSession, prompt: str) -> str:
    """Generate answer asynchronously."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.0}
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


async def batch_embed_messages(session: aiohttp.ClientSession, messages: list[dict]) -> list[dict]:
    """Embed all messages in parallel batches."""
    print(f"  Computing embeddings for {len(messages)} messages (parallel)...", flush=True)

    semaphore = asyncio.Semaphore(EMBEDDING_BATCH_SIZE)

    async def embed_with_semaphore(msg):
        async with semaphore:
            text = f"{msg['speaker']}: {msg['text']}"
            msg['embedding'] = await get_embedding_async(session, text)
            return msg

    # Process all at once with semaphore limiting concurrency
    tasks = [embed_with_semaphore(msg) for msg in messages]

    completed = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        completed += 1
        if completed % 50 == 0:
            print(f"    Embedded {completed}/{len(messages)} messages", flush=True)

    print(f"    Finished embedding {len(messages)} messages", flush=True)
    return messages


# =============================================================================
# DATA EXTRACTION
# =============================================================================

def extract_messages_with_timestamps(item) -> list[dict]:
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
                    "session": session_idx,
                    "session_time": session_time,
                    "msg_idx": msg_idx,
                    "speaker": msg.get("speaker", "Unknown"),
                    "text": msg.get("text", ""),
                    "dia_id": msg.get("dia_id", f"D{session_idx}:{msg_idx}"),
                })
        session_idx += 1
    return messages


# =============================================================================
# RETRIEVAL
# =============================================================================

async def retrieve_relevant_messages(
    session: aiohttp.ClientSession,
    question: str,
    messages: list[dict],
    top_k: int = TOP_K_RETRIEVAL,
    semantic_weight: float = 0.6
) -> list[dict]:
    question_embedding = await get_embedding_async(session, question)

    texts = [f"{m['speaker']}: {m['text']}" for m in messages]
    bm25_scores = compute_bm25_scores(question, texts)

    max_bm25 = max(bm25_scores) if bm25_scores else 1
    if max_bm25 > 0:
        bm25_scores = [s / max_bm25 for s in bm25_scores]

    semantic_scores = []
    for msg in messages:
        if msg.get('embedding') and question_embedding:
            sim = cosine_similarity(question_embedding, msg['embedding'])
            semantic_scores.append(sim)
        else:
            semantic_scores.append(0.0)

    max_sem = max(semantic_scores) if semantic_scores else 1
    if max_sem > 0:
        semantic_scores = [s / max_sem for s in semantic_scores]

    bm25_weight = 1 - semantic_weight
    combined_scores = [
        bm25_weight * bm25 + semantic_weight * sem
        for bm25, sem in zip(bm25_scores, semantic_scores)
    ]

    scored_messages = list(zip(messages, combined_scores))
    scored_messages.sort(key=lambda x: x[1], reverse=True)
    return [msg for msg, score in scored_messages[:top_k]]


def add_surrounding_context(selected: list[dict], all_messages: list[dict], window: int = 1) -> list[dict]:
    expanded = set()
    for msg in selected:
        session = msg['session']
        idx = msg['msg_idx']
        for offset in range(-window, window + 1):
            expanded.add((session, idx + offset))

    result = []
    seen = set()
    for msg in all_messages:
        key = (msg['session'], msg['msg_idx'])
        if key in expanded and key not in seen:
            result.append(msg)
            seen.add(key)

    result.sort(key=lambda x: (x['session'], x['msg_idx']))
    return result


def format_context_with_timestamps(messages: list[dict], max_chars: int = CONTEXT_LIMIT) -> str:
    sessions = {}
    for msg in messages:
        session = msg['session']
        if session not in sessions:
            sessions[session] = {'time': msg['session_time'], 'messages': []}
        sessions[session]['messages'].append(msg)

    lines = ["=== CONVERSATION MEMORIES WITH TIMESTAMPS ===\n"]
    total_chars = 0

    for session_num in sorted(sessions.keys()):
        session_data = sessions[session_num]
        header = f"\n--- SESSION {session_num} | DATE: {session_data['time']} ---\n"
        if total_chars + len(header) > max_chars:
            break
        lines.append(header)
        total_chars += len(header)

        for msg in session_data['messages']:
            line = f"  {msg['speaker']}: {msg['text']}\n"
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line)

    return "".join(lines)


# =============================================================================
# PROMPTS
# =============================================================================

STANDARD_PROMPT = """Based on the conversation memories below, answer the question.

{context}

QUESTION: {question}

INSTRUCTIONS:
- Answer based ONLY on information in the context above
- If the answer is not present, say "The answer is not in the provided context"
- Be specific and concise

ANSWER:"""

TEMPORAL_PROMPT = """Based on the conversation memories below, answer the question about WHEN something happened.

{context}

QUESTION: {question}

IMPORTANT FOR DATE QUESTIONS:
- Each session has a DATE in its header (e.g., "DATE: 1:51 pm on 15 July, 2023")
- Use these session dates to calculate absolute dates
- Give specific dates, not relative times like "yesterday"

ANSWER (include specific date if possible):"""

MULTI_HOP_PROMPT = """Based on the conversation memories below, answer the question that requires connecting multiple pieces of information.

{context}

QUESTION: {question}

INSTRUCTIONS:
- This question requires finding and connecting 2+ pieces of information
- Look across multiple sessions if needed

ANSWER:"""

ADVERSARIAL_PROMPT = """Based on the conversation memories below, carefully answer the question.

{context}

QUESTION: {question}

CRITICAL VERIFICATION:
1. Verify that entities in the question MATCH the context
2. If the question attributes something to the WRONG PERSON, correct it
3. Only answer as asked if all entities are correct

ANSWER (correct any errors first):"""


def get_prompt_for_category(category: str) -> str:
    if category == "temporal":
        return TEMPORAL_PROMPT
    elif category == "multi_hop":
        return MULTI_HOP_PROMPT
    elif category == "adversarial":
        return ADVERSARIAL_PROMPT
    return STANDARD_PROMPT


# =============================================================================
# JUDGING
# =============================================================================

JUDGE_PROMPT = """Score this answer 0-10.

QUESTION: {question}
REFERENCE: {gold_answer}
GENERATED: {generated_answer}

SCORING:
- 8-10: Correct, matches reference meaning
- 5-7: Partially correct
- 0-4: Wrong or missing key info

Output ONLY JSON: {{"total": 0-10}}"""


async def judge_answer_async(session: aiohttp.ClientSession, question: str, gold_answer: str, generated_answer: str) -> dict:
    gold_lower = gold_answer.lower().strip()
    gen_lower = generated_answer.lower().strip()

    if gold_lower in gen_lower or gen_lower in gold_lower:
        return {"total": 10}

    prompt = JUDGE_PROMPT.format(
        question=question,
        gold_answer=gold_answer,
        generated_answer=generated_answer,
    )

    try:
        result_text = await generate_answer_async(session, prompt)
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0]
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0]
        return json.loads(result_text)
    except:
        return {"total": 0}


# =============================================================================
# PROCESS SINGLE QUESTION
# =============================================================================

async def process_question(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    messages: list[dict],
    all_messages: list[dict],
) -> dict:
    """Process a single question."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    adversarial_answer = qa.get("adversarial_answer", "")
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # Retrieve relevant messages
    relevant_messages = await retrieve_relevant_messages(http_session, question, messages)
    relevant_messages = add_surrounding_context(relevant_messages, all_messages)
    context = format_context_with_timestamps(relevant_messages)

    # Generate answer
    prompt_template = get_prompt_for_category(category_name)
    prompt = prompt_template.format(context=context, question=question)
    generated = await generate_answer_async(http_session, prompt)

    # Judge
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)

    gen_time = time.time() - start_time
    total_score = judgment.get("total", 0)

    if total_score >= CORRECT_THRESHOLD:
        status = "CORRECT"
    elif total_score >= PARTIAL_THRESHOLD:
        status = "PARTIAL"
    else:
        status = "WRONG"

    print(f"  [{q_idx + 1}] {category_name}: {question[:40]}... Score: {total_score}/10 [{status}] ({gen_time:.1f}s)", flush=True)

    return {
        "question_id": q_idx,
        "category": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "score": total_score,
        "correct": total_score >= CORRECT_THRESHOLD,
        "time_seconds": round(gen_time, 2),
    }


# =============================================================================
# MAIN
# =============================================================================

async def run_parallel_benchmark(max_conversations: int = 10):
    """Run benchmark with parallel processing."""

    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print("=" * 70)
    print("LoCoMo-PARALLEL Benchmark v1.0")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Concurrent questions: {CONCURRENT_QUESTIONS}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    all_results = []
    category_scores = {cat: [] for cat in CATEGORIES.values()}

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(dataset[:max_conversations]):
            conversation = item.get("conversation", item)
            qa_list = item.get("qa", [])

            speaker_a = conversation.get('speaker_a', 'Person A')
            speaker_b = conversation.get('speaker_b', 'Person B')

            print(f"\n{'='*60}")
            print(f"Conversation {conv_idx + 1}: {speaker_a} & {speaker_b}")
            print(f"Questions: {len(qa_list)}")
            print("="*60)

            # Extract and embed messages
            messages = extract_messages_with_timestamps(item)
            print(f"Total messages: {len(messages)}")
            messages = await batch_embed_messages(http_session, messages)

            # Process questions in parallel batches
            semaphore = asyncio.Semaphore(CONCURRENT_QUESTIONS)

            async def process_with_semaphore(q_idx, qa):
                async with semaphore:
                    return await process_question(http_session, q_idx, qa, messages, messages)

            tasks = [process_with_semaphore(q_idx, qa) for q_idx, qa in enumerate(qa_list)]
            results = await asyncio.gather(*tasks)

            for result in results:
                result["conversation_id"] = conv_idx
                all_results.append(result)
                category_scores[result["category"]].append(result)

    # Compute metrics
    print("\n" + "=" * 70)
    print("PARALLEL BENCHMARK RESULTS")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    accuracy = (correct / total * 100) if total > 0 else 0

    print(f"\nTotal Questions: {total}")
    print(f"Strict Accuracy (8+/10): {accuracy:.1f}%")

    print("\nBY CATEGORY:")
    for category, scores in category_scores.items():
        if scores:
            cat_correct = sum(1 for s in scores if s["correct"])
            cat_accuracy = cat_correct / len(scores) * 100
            print(f"  {category:15} | Acc: {cat_accuracy:5.1f}% | n={len(scores)}")

    # Save results
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"locomo10_PARALLEL_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "benchmark": "LoCoMo-PARALLEL v1.0",
            "model": OLLAMA_MODEL,
            "timestamp": timestamp,
            "metrics": {
                "total_questions": total,
                "strict_accuracy": round(accuracy, 2),
            },
            "results": all_results,
        }, f, indent=2)

    print(f"\nResults saved to: {json_path}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_parallel_benchmark(max_conversations=10))
