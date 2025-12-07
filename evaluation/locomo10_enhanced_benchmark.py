"""
LoCoMo-Enhanced Benchmark v2.0
A comprehensive evaluation with Memory Shards, strict metrics, and evidence tracing.

Key Features:
1. Memory Shards Architecture - Partitioned memory retrieval
2. Enhanced Task Categories - 7 specialized types
3. Strict Evaluation - Zero hallucination, refusal scoring, evidence tracing
4. Statistical Rigor - 95% CI on all metrics
"""

import json
import time
import re
import math
from datetime import datetime
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Optional
import requests
import numpy as np

# Import memory shards
from memory_shards import (
    MemoryShardSystem, MemoryMessage, EvidenceTrace,
    populate_shard_system, extract_messages_from_conversation,
    ShardType, get_embedding_ollama, cosine_similarity
)

# Ollama Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

# Categories mapping
CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
    6: "forgetting",      # NEW: Outdated facts
    7: "memory_overload", # NEW: Large context
}

# =============================================================================
# BENCHMARK PARAMETERS
# =============================================================================
CONTEXT_LIMIT = 12000
TOP_K_RETRIEVAL = 30
CORRECT_THRESHOLD = 8       # 8/10 = correct
PARTIAL_THRESHOLD = 5       # 5-7 = partial

# =============================================================================
# STOPWORDS FOR BM25
# =============================================================================
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
    "don", "should", "now", "d", "ll", "m", "o", "re", "ve", "y", "ain", "aren",
    "couldn", "didn", "doesn", "hadn", "hasn", "haven", "isn", "ma", "mightn",
    "mustn", "needn", "shan", "shouldn", "wasn", "weren", "won", "wouldn",
])


def simple_tokenize(text: str) -> list[str]:
    """Tokenize and remove stopwords."""
    words = re.findall(r'\b[a-z]+\b', text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def compute_bm25_scores(query: str, documents: list[str], k1=1.5, b=0.75) -> list[float]:
    """Compute BM25 scores for documents."""
    query_tokens = simple_tokenize(query)
    if not query_tokens or not documents:
        return [0.0] * len(documents)

    doc_tokens_list = [simple_tokenize(doc) for doc in documents]
    doc_lengths = [len(tokens) for tokens in doc_tokens_list]
    avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1

    # Compute document frequencies
    df = Counter()
    for tokens in doc_tokens_list:
        for token in set(tokens):
            df[token] += 1

    N = len(documents)
    scores = []

    for doc_tokens, doc_len in zip(doc_tokens_list, doc_lengths):
        if doc_len == 0:
            scores.append(0.0)
            continue

        score = 0.0
        tf = Counter(doc_tokens)

        for term in query_tokens:
            if term not in tf:
                continue
            f = tf[term]
            doc_freq = df.get(term, 0)
            if doc_freq == 0:
                continue
            idf = math.log((N - doc_freq + 0.5) / (doc_freq + 0.5) + 1)
            numerator = f * (k1 + 1)
            denominator = f + k1 * (1 - b + b * (doc_len / avg_doc_length))
            score += idf * (numerator / denominator)

        scores.append(score)

    return scores


# =============================================================================
# PROMPTS
# =============================================================================

STANDARD_PROMPT = """Based on the following conversation memories, answer the question accurately.

{context}

QUESTION: {question}

INSTRUCTIONS:
1. Search the context for relevant information
2. If found, provide a specific, factual answer
3. If NOT found, say: "I don't recall this information from the conversations."
4. Do NOT make up information

ANSWER:"""

TEMPORAL_PROMPT = """Based on the following conversation memories with timestamps, answer the temporal question.

{context}

QUESTION: {question}

TEMPORAL INSTRUCTIONS:
1. Look at session timestamps (format: "Session X - TIME on DATE")
2. Look for relative time references ("last week", "yesterday", "two days ago")
3. Calculate specific dates when possible
4. If you find the information, provide the specific date/time
5. If NOT found, say: "I don't recall this temporal information."

ANSWER (include specific date/time if found):"""

MULTI_HOP_PROMPT = """Based on the following conversation memories, answer this question that requires connecting multiple pieces of information.

{context}

QUESTION: {question}

MULTI-HOP INSTRUCTIONS:
1. This question requires connecting 2+ pieces of information
2. Find evidence for EACH part of the question
3. Chain the evidence together
4. If you cannot find all required pieces, say what you did find

EVIDENCE CHAIN:
Step 1: [First piece of evidence]
Step 2: [Second piece of evidence]

FINAL ANSWER:"""

ADVERSARIAL_PROMPT = """Based on the conversation memories below, carefully verify and answer the question.

{context}

QUESTION: {question}

CRITICAL VERIFICATION INSTRUCTIONS:
1. VERIFY that entities mentioned in the question MATCH the context
2. If the question attributes something to the WRONG PERSON, CORRECT it
   - Example: If question asks about "Melanie's necklace" but it was CAROLINE's, say so
3. If the question asks about something that NEVER HAPPENED, say so
4. Only answer as asked if all entities in the question are CORRECT

VERIFICATION:
- Who does this actually apply to in the context?
- Is the question accurate?

ANSWER (correct any errors first):"""

FORGETTING_PROMPT = """Based on the following conversation memories, answer with the MOST RECENT information.

{context}

QUESTION: {question}

TEMPORAL PRIORITY INSTRUCTIONS:
1. Look for the MOST RECENT mention of the topic
2. If information changed over time, use the LATEST value
3. Earlier sessions have OLDER (potentially outdated) information
4. Later sessions have NEWER (current) information
5. Always prefer recent information over old

ANSWER (using most recent information):"""

# =============================================================================
# HALLUCINATION DETECTION
# =============================================================================

HALLUCINATION_JUDGE_PROMPT = """Check if this answer contains ANY information NOT present in the context.

CONTEXT:
{context}

GENERATED ANSWER:
{generated}

EVALUATION:
1. List each factual claim in the answer
2. For each claim, check if it has supporting evidence in the context
3. If ANY claim lacks evidence, mark as HALLUCINATION

Output ONLY valid JSON:
{{"claims": ["claim1", "claim2"], "ungrounded_claims": ["any claims without evidence"], "is_hallucination": true/false}}"""


STANDARD_JUDGE_PROMPT = """Score this answer on a 0-10 scale.

QUESTION: {question}
REFERENCE ANSWER: {gold_answer}
GENERATED ANSWER: {generated_answer}

SCORING:
- 10: Perfect match or semantically equivalent
- 8-9: Correct with minor differences
- 5-7: Partially correct
- 3-4: Some relevant info but mostly wrong
- 0-2: Wrong or no relevant information

If generated says "don't recall" and reference HAS an answer: Score 0

Output ONLY valid JSON:
{{"score": 0-10, "is_hallucination": false, "is_refusal": true/false}}"""


ADVERSARIAL_JUDGE_PROMPT = """Evaluate if the AI correctly identified an ADVERSARIAL question.

The question contained INCORRECT information (wrong person, wrong attribution).
The AI should have CORRECTED the error or said the premise is wrong.

QUESTION: {question}
WRONG ANSWER (what the question falsely suggests): {adversarial_answer}
GENERATED ANSWER: {generated_answer}

SCORING:
Did the AI give the WRONG answer (accepting the false premise)?
- If YES (was tricked): Score 0-2

Did the AI CORRECT the error?
- 10: Explicitly corrected ("Actually, it was X not Y")
- 8-9: Indicated something was wrong, didn't give adversarial answer
- 5-7: Avoided adversarial answer but didn't correct
- 0-2: Gave the adversarial answer (was tricked)

Output ONLY valid JSON:
{{"avoided_trap": true/false, "corrected_error": true/false, "score": 0-10, "is_hallucination": false, "is_refusal": false}}"""


# =============================================================================
# OLLAMA FUNCTIONS
# =============================================================================

def ollama_chat(prompt: str, temperature: float = 0.0, max_tokens: int = 300) -> str:
    """Call Ollama chat API."""
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens}
            },
            timeout=60
        )
        response.raise_for_status()
        return response.json()["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


def get_embedding(text: str) -> Optional[list[float]]:
    """Get embedding from Ollama."""
    return get_embedding_ollama(text, OLLAMA_BASE_URL, EMBEDDING_MODEL)


# =============================================================================
# RETRIEVAL WITH MEMORY SHARDS
# =============================================================================

def retrieve_context_with_shards(
    question: str,
    shard_system: MemoryShardSystem,
    category: str,
    max_chars: int = CONTEXT_LIMIT,
    top_k: int = TOP_K_RETRIEVAL
) -> tuple[str, list[str], EvidenceTrace]:
    """Retrieve context using memory shards and hybrid ranking."""

    # Route question to relevant shards
    relevant_shards = shard_system.route_question(question)
    shard_sources = [s.value for s in relevant_shards]

    # Collect messages from relevant shards
    candidate_messages = []
    for shard_type in relevant_shards:
        shard = shard_system.get_shard(shard_type)
        candidate_messages.extend(shard.messages)

    # Deduplicate
    seen = set()
    unique_messages = []
    for msg in candidate_messages:
        key = (msg.session, msg.text[:50])
        if key not in seen:
            seen.add(key)
            unique_messages.append(msg)

    if not unique_messages:
        unique_messages = shard_system.all_messages[:top_k]

    # Compute BM25 scores
    texts = [msg.text for msg in unique_messages]
    bm25_scores = compute_bm25_scores(question, texts)

    # Compute semantic scores
    question_embedding = get_embedding(question)
    semantic_scores = []
    for msg in unique_messages:
        if msg.embedding and question_embedding:
            score = cosine_similarity(question_embedding, msg.embedding)
        else:
            score = 0.0
        semantic_scores.append(score)

    # Hybrid ranking: BM25 * 0.4 + Semantic * 0.6
    hybrid_scores = []
    max_bm25 = max(bm25_scores) if bm25_scores and max(bm25_scores) > 0 else 1
    max_sem = max(semantic_scores) if semantic_scores and max(semantic_scores) > 0 else 1

    for i, msg in enumerate(unique_messages):
        bm25_norm = bm25_scores[i] / max_bm25 if max_bm25 > 0 else 0
        sem_norm = semantic_scores[i] / max_sem if max_sem > 0 else 0
        hybrid = 0.4 * bm25_norm + 0.6 * sem_norm
        hybrid_scores.append((hybrid, msg, bm25_scores[i], semantic_scores[i]))

    # Sort by hybrid score
    hybrid_scores.sort(key=lambda x: x[0], reverse=True)

    # Format context
    lines = []
    total_chars = 0
    retrieved_messages = []

    for _, msg, bm25, sem in hybrid_scores[:top_k]:
        line = msg.to_context_line()
        if total_chars + len(line) > max_chars:
            break
        lines.append(line)
        total_chars += len(line) + 1
        retrieved_messages.append({
            "session": msg.session,
            "speaker": msg.speaker,
            "text": msg.text[:100],
            "bm25_score": round(bm25, 3),
            "semantic_score": round(sem, 3)
        })

    context = "\n".join(lines)

    # Create evidence trace
    trace = EvidenceTrace(
        question=question,
        category=category,
        retrieved_messages=retrieved_messages,
        shard_sources=shard_sources,
        bm25_scores=[m["bm25_score"] for m in retrieved_messages],
        semantic_scores=[m["semantic_score"] for m in retrieved_messages],
        final_context=context
    )

    return context, shard_sources, trace


# =============================================================================
# ANSWER GENERATION
# =============================================================================

def get_prompt_for_category(category: str) -> str:
    """Get appropriate prompt based on question category."""
    prompts = {
        "temporal": TEMPORAL_PROMPT,
        "multi_hop": MULTI_HOP_PROMPT,
        "adversarial": ADVERSARIAL_PROMPT,
        "forgetting": FORGETTING_PROMPT,
    }
    return prompts.get(category, STANDARD_PROMPT)


def generate_answer(question: str, context: str, category: str) -> str:
    """Generate answer using category-specific prompt."""
    prompt_template = get_prompt_for_category(category)
    prompt = prompt_template.format(context=context, question=question)
    return ollama_chat(prompt)


# =============================================================================
# EVALUATION FUNCTIONS
# =============================================================================

def detect_refusal(generated: str) -> bool:
    """Detect if the answer is a refusal."""
    refusal_phrases = [
        "don't recall", "do not recall",
        "not in the context", "not in the provided",
        "cannot find", "can't find",
        "no information", "don't have that information",
        "not mentioned", "wasn't mentioned",
    ]
    return any(phrase in generated.lower() for phrase in refusal_phrases)


def detect_hallucination(generated: str, context: str) -> dict:
    """Check if answer contains hallucinated information."""
    prompt = HALLUCINATION_JUDGE_PROMPT.format(
        context=context[:4000],
        generated=generated
    )

    try:
        response = ollama_chat(prompt, max_tokens=200)

        # Parse JSON
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            response = response.split("```")[1].split("```")[0]

        result = json.loads(response)
        return {
            "is_hallucination": result.get("is_hallucination", False),
            "ungrounded_claims": result.get("ungrounded_claims", []),
        }
    except Exception as e:
        return {"is_hallucination": False, "error": str(e)}


def judge_answer(question: str, gold_answer: str, generated: str) -> dict:
    """Judge answer quality with LLM."""
    prompt = STANDARD_JUDGE_PROMPT.format(
        question=question,
        gold_answer=gold_answer,
        generated_answer=generated
    )

    try:
        response = ollama_chat(prompt, max_tokens=150)

        if "```json" in response:
            response = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            response = response.split("```")[1].split("```")[0]

        result = json.loads(response)
        result["is_refusal"] = detect_refusal(generated)
        return result
    except Exception as e:
        return {
            "score": 0,
            "is_hallucination": False,
            "is_refusal": detect_refusal(generated),
            "error": str(e)
        }


def judge_adversarial_answer(question: str, adversarial_answer: str, generated: str) -> dict:
    """Judge adversarial question handling."""
    prompt = ADVERSARIAL_JUDGE_PROMPT.format(
        question=question,
        adversarial_answer=adversarial_answer,
        generated_answer=generated
    )

    try:
        response = ollama_chat(prompt, max_tokens=150)

        if "```json" in response:
            response = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            response = response.split("```")[1].split("```")[0]

        result = json.loads(response)
        # Rename 'score' to 'total' for consistency
        if "score" in result:
            result["total"] = result.pop("score")
        return result
    except Exception as e:
        return {
            "avoided_trap": False,
            "corrected_error": False,
            "total": 0,
            "is_hallucination": False,
            "is_refusal": False,
            "error": str(e)
        }


# =============================================================================
# STATISTICAL FUNCTIONS
# =============================================================================

def compute_bootstrap_ci(scores: list, confidence: float = 0.95,
                         n_bootstrap: int = 10000) -> tuple[float, float]:
    """Compute bootstrap confidence interval."""
    if len(scores) == 0:
        return (0.0, 0.0)

    scores = np.array(scores)
    means = []

    for _ in range(n_bootstrap):
        sample = np.random.choice(scores, size=len(scores), replace=True)
        means.append(np.mean(sample))

    lower = np.percentile(means, (1 - confidence) / 2 * 100)
    upper = np.percentile(means, (1 + confidence) / 2 * 100)

    return (round(lower, 4), round(upper, 4))


def compute_all_metrics_with_ci(results: list[dict]) -> dict:
    """Compute all metrics with 95% confidence intervals."""
    if not results:
        return {}

    # Binary correct scores
    correct_binary = [1 if r.get("correct", False) else 0 for r in results]
    hallucination_binary = [1 if r.get("is_hallucination", False) else 0 for r in results]
    refusal_binary = [1 if r.get("is_refusal", False) else 0 for r in results]

    accuracy = np.mean(correct_binary) * 100
    accuracy_ci = compute_bootstrap_ci(correct_binary)

    halluc_rate = np.mean(hallucination_binary) * 100
    halluc_ci = compute_bootstrap_ci(hallucination_binary)

    refusal_rate = np.mean(refusal_binary) * 100
    refusal_ci = compute_bootstrap_ci(refusal_binary)

    # Per-category metrics
    category_metrics = {}
    categories = set(r.get("category", "unknown") for r in results)

    for category in categories:
        cat_results = [r for r in results if r.get("category") == category]
        if cat_results:
            cat_correct = [1 if r.get("correct", False) else 0 for r in cat_results]
            cat_accuracy = np.mean(cat_correct) * 100
            cat_ci = compute_bootstrap_ci(cat_correct)
            category_metrics[category] = {
                "accuracy": round(cat_accuracy, 2),
                "ci": (round(cat_ci[0] * 100, 2), round(cat_ci[1] * 100, 2)),
                "n": len(cat_results)
            }

    return {
        "total_questions": len(results),
        "accuracy": {
            "mean": round(accuracy, 2),
            "ci": (round(accuracy_ci[0] * 100, 2), round(accuracy_ci[1] * 100, 2))
        },
        "hallucination_rate": {
            "mean": round(halluc_rate, 2),
            "ci": (round(halluc_ci[0] * 100, 2), round(halluc_ci[1] * 100, 2))
        },
        "refusal_rate": {
            "mean": round(refusal_rate, 2),
            "ci": (round(refusal_ci[0] * 100, 2), round(refusal_ci[1] * 100, 2))
        },
        "by_category": category_metrics
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

def run_enhanced_benchmark(max_conversations: int = 3, check_hallucinations: bool = True):
    """Run the enhanced benchmark with memory shards and strict evaluation."""

    # Load dataset
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print("=" * 70)
    print("LoCoMo-Enhanced Benchmark v2.0")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Memory Shards: ENABLED")
    print(f"Hallucination Detection: {'ENABLED' if check_hallucinations else 'DISABLED'}")
    print(f"Correct threshold: {CORRECT_THRESHOLD}/10 (strict)")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    results = []
    evidence_traces = []
    category_scores = {cat: [] for cat in CATEGORIES.values()}

    total_questions = 0
    correct_count = 0
    partial_count = 0
    hallucination_count = 0
    refusal_count = 0

    for conv_idx, item in enumerate(dataset[:max_conversations]):
        conversation = item.get("conversation", item)
        qa_list = item.get("qa", [])

        speaker_a = conversation.get('speaker_a', 'Person A')
        speaker_b = conversation.get('speaker_b', 'Person B')

        print(f"\n{'='*60}")
        print(f"Conversation {conv_idx + 1}: {speaker_a} & {speaker_b}")
        print(f"Questions: {len(qa_list)}")
        print("=" * 60)

        # Build memory shard system
        shard_system = populate_shard_system(item)
        stats = shard_system.get_stats()
        print(f"  Shard distribution: {stats}")

        # Compute embeddings for all messages
        print(f"  Computing embeddings for {len(shard_system.all_messages)} messages...")
        for i, msg in enumerate(shard_system.all_messages):
            if msg.embedding is None:
                msg.embedding = get_embedding(msg.text)
            if (i + 1) % 50 == 0:
                print(f"    Embedded {i + 1}/{len(shard_system.all_messages)} messages")

        for q_idx, qa in enumerate(qa_list):
            question = qa.get("question", "")
            gold_answer = str(qa.get("answer", ""))
            adversarial_answer = qa.get("adversarial_answer", "")
            category_id = qa.get("category", 0)
            category_name = CATEGORIES.get(category_id, "unknown")

            total_questions += 1
            start_time = time.time()

            # Retrieve context with shards
            context, shard_sources, trace = retrieve_context_with_shards(
                question, shard_system, category_name
            )

            # Generate answer
            generated = generate_answer(question, context, category_name)
            trace.answer = generated
            trace.gold_answer = gold_answer

            # Judge answer
            if category_id == 5:  # Adversarial
                judgment = judge_adversarial_answer(question, adversarial_answer, generated)
                total_score = judgment.get("total", 0)
            else:
                judgment = judge_answer(question, gold_answer, generated)
                total_score = judgment.get("score", 0)

            # Check hallucination (optional)
            is_hallucination = judgment.get("is_hallucination", False)
            if check_hallucinations and not is_hallucination:
                halluc_check = detect_hallucination(generated, context)
                is_hallucination = halluc_check.get("is_hallucination", False)

            is_refusal = judgment.get("is_refusal", detect_refusal(generated))

            gen_time = time.time() - start_time

            # Classification
            if total_score >= CORRECT_THRESHOLD:
                is_correct = True
                correct_count += 1
                status = "CORRECT"
            elif total_score >= PARTIAL_THRESHOLD:
                is_correct = False
                partial_count += 1
                status = "PARTIAL"
            else:
                is_correct = False
                status = "WRONG"

            if is_hallucination:
                hallucination_count += 1
                status += " (HALLUC)"

            if is_refusal:
                refusal_count += 1

            # Update trace
            trace.score = total_score
            trace.is_correct = is_correct
            trace.is_hallucination = is_hallucination
            trace.is_refusal = is_refusal
            trace.grounded = not is_hallucination

            evidence_traces.append(trace)

            category_scores[category_name].append({
                "correct": is_correct,
                "score": total_score,
                "hallucination": is_hallucination,
                "refusal": is_refusal,
            })

            # Print progress
            shard_str = ",".join(shard_sources)
            print(f"  [{q_idx + 1}] {category_name}: {question[:40]}...")
            print(f"      Score: {total_score}/10 [{status}] Shards: {shard_str} ({gen_time:.1f}s)")

            results.append({
                "conversation_id": conv_idx,
                "question_id": q_idx,
                "category": category_name,
                "question": question,
                "gold_answer": gold_answer,
                "generated_answer": generated,
                "score": total_score,
                "correct": is_correct,
                "is_hallucination": is_hallucination,
                "is_refusal": is_refusal,
                "shard_sources": shard_sources,
                "time_seconds": round(gen_time, 2),
            })

    # Compute metrics with CI
    print("\n" + "=" * 70)
    print("ENHANCED BENCHMARK RESULTS")
    print("=" * 70)

    metrics = compute_all_metrics_with_ci(results)

    print(f"\n{'METRIC':<30} | {'VALUE':>10} | {'95% CI':>25}")
    print("-" * 70)
    print(f"{'Strict Accuracy (8+/10)':<30} | {metrics['accuracy']['mean']:>9.1f}% | ({metrics['accuracy']['ci'][0]:.1f}% - {metrics['accuracy']['ci'][1]:.1f}%)")
    print(f"{'Hallucination Rate':<30} | {metrics['hallucination_rate']['mean']:>9.1f}% | ({metrics['hallucination_rate']['ci'][0]:.1f}% - {metrics['hallucination_rate']['ci'][1]:.1f}%)")
    print(f"{'Refusal Rate':<30} | {metrics['refusal_rate']['mean']:>9.1f}% | ({metrics['refusal_rate']['ci'][0]:.1f}% - {metrics['refusal_rate']['ci'][1]:.1f}%)")

    print("\n" + "-" * 70)
    print("BY CATEGORY:")
    print("-" * 70)
    for category, cat_metrics in metrics.get("by_category", {}).items():
        print(f"  {category:15} | Acc: {cat_metrics['accuracy']:5.1f}% | CI: ({cat_metrics['ci'][0]:.1f}% - {cat_metrics['ci'][1]:.1f}%) | n={cat_metrics['n']}")

    # Save results
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON results
    json_path = output_dir / f"locomo10_enhanced_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "benchmark": "LoCoMo-Enhanced v2.0",
            "model": OLLAMA_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "timestamp": timestamp,
            "features": {
                "memory_shards": True,
                "hallucination_detection": check_hallucinations,
                "evidence_tracing": True,
                "confidence_intervals": True,
            },
            "metrics": metrics,
            "results": results,
        }, f, indent=2)

    # Evidence traces
    traces_path = output_dir / f"evidence_traces_{timestamp}.json"
    with open(traces_path, "w", encoding="utf-8") as f:
        json.dump({
            "traces": [t.to_dict() for t in evidence_traces]
        }, f, indent=2)

    print(f"\nResults saved to: {json_path}")
    print(f"Evidence traces saved to: {traces_path}")
    print("=" * 70)

    return {
        "metrics": metrics,
        "results": results,
    }


if __name__ == "__main__":
    run_enhanced_benchmark(max_conversations=3, check_hallucinations=True)
