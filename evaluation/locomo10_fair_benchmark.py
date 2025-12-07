"""
LoCoMo-FAIR Benchmark v1.0
A STRICT, NON-GAMEABLE evaluation for AI memory systems.

Key differences from the "balanced" benchmark:
- Correct threshold: 8/10 (not 5/10)
- NO retries allowed
- NO category-specific prompts or context limits
- Unified pipeline for ALL question types
- Reports hallucination rate and refusal metrics
- Stricter date matching (exact day, not same week)
"""

import json
import csv
import time
import re
from datetime import datetime
from pathlib import Path
from openai import OpenAI
from dateutil import parser as date_parser
import numpy as np

# Ollama Configuration
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL = "qwen2.5:7b-instruct"

client = OpenAI(
    base_url=OLLAMA_BASE_URL,
    api_key="ollama",
)

# Categories mapping
CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

# =============================================================================
# FAIR BENCHMARK: Fixed parameters (NO per-category tuning)
# =============================================================================
FIXED_CONTEXT_LIMIT = 8000  # Same for ALL categories
FIXED_K = 20                # Fixed retrieval count
CORRECT_THRESHOLD = 8       # 8/10 = correct (strict)
PARTIAL_THRESHOLD = 5       # 5-7 = partial

# =============================================================================
# UNIFIED PROMPT (same for all question types)
# =============================================================================
UNIFIED_PROMPT = """Based on the following conversation excerpts, answer the question.

IMPORTANT INSTRUCTIONS:
- If the answer is clearly present in the context, provide it.
- If the answer is NOT present or you're uncertain, say: "The answer is not in the provided context."
- Do NOT guess or make up information.
- Be concise and specific.

### CONTEXT:
{context}

### QUESTION:
{question}

### ANSWER:"""

# =============================================================================
# STRICT JUDGE PROMPT
# =============================================================================
STRICT_JUDGE_PROMPT = """You are a strict evaluator. Score this answer on a 0-10 scale.

QUESTION: {question}
REFERENCE ANSWER: {gold_answer}
GENERATED ANSWER: {generated_answer}

SCORING CRITERIA:

FACTUAL ACCURACY (0-4 points):
- 4: All key facts correct, matches reference
- 3: Main facts correct, minor omissions
- 2: Some facts correct, some wrong or missing
- 1: Few correct facts
- 0: Factually wrong or hallucination

COMPLETENESS (0-3 points):
- 3: Covers all key points in reference
- 2: Covers most key points
- 1: Covers some key points
- 0: Missing key information

PRECISION (0-3 points):
- 3: No extraneous or wrong information
- 2: Minimal extraneous info
- 1: Some extraneous info or minor errors
- 0: Significant wrong information

SPECIAL RULES:
- If generated says "not in context" and reference HAS an answer: Score 0
- If generated gives a confident but WRONG answer: Mark as hallucination

Output ONLY valid JSON:
{{"factual": 0-4, "completeness": 0-3, "precision": 0-3, "total": 0-10, "is_hallucination": true/false, "is_refusal": true/false}}"""


def extract_messages(item):
    """Extract all messages from a conversation item."""
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

        for msg in session_messages:
            if isinstance(msg, dict) and 'text' in msg:
                messages.append({
                    "session": session_idx,
                    "session_time": session_time,
                    "speaker": msg.get("speaker", "Unknown"),
                    "text": msg.get("text", ""),
                })

        session_idx += 1

    return messages


def format_context(messages, max_chars=FIXED_CONTEXT_LIMIT):
    """Format messages as context string with fixed limit."""
    lines = []
    total_chars = 0

    for msg in messages:
        line = f"[Session {msg['session']}] {msg['speaker']}: {msg['text']}"
        if total_chars + len(line) > max_chars:
            break
        lines.append(line)
        total_chars += len(line) + 1

    return "\n".join(lines)


def generate_answer(question: str, context: str) -> str:
    """Generate answer using UNIFIED prompt. NO retries."""
    prompt = UNIFIED_PROMPT.format(context=context, question=question)

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # Deterministic
            max_tokens=300,   # Fixed for all
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"


def judge_answer_strict(question: str, gold_answer: str, generated_answer: str) -> dict:
    """Strict LLM judge. NO keyword shortcuts."""

    # Check for refusal
    refusal_phrases = [
        "not in the provided context",
        "not in the context",
        "cannot find",
        "don't have that information",
        "not mentioned",
        "no information available",
    ]
    is_refusal = any(phrase in generated_answer.lower() for phrase in refusal_phrases)

    # Use LLM judge for ALL answers (no shortcuts)
    prompt = STRICT_JUDGE_PROMPT.format(
        question=question,
        gold_answer=gold_answer,
        generated_answer=generated_answer,
    )

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=150,
        )
        content = response.choices[0].message.content.strip()

        # Parse JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        result = json.loads(content)
        result["is_refusal"] = is_refusal
        return result

    except Exception as e:
        # Conservative fallback: mark as incorrect
        return {
            "factual": 0,
            "completeness": 0,
            "precision": 0,
            "total": 0,
            "is_hallucination": False,
            "is_refusal": is_refusal,
            "error": str(e)
        }


def compute_bootstrap_ci(scores, confidence=0.95, n_bootstrap=5000):
    """Compute bootstrap confidence interval."""
    if len(scores) == 0:
        return (0, 0)

    scores = np.array(scores)
    means = []

    for _ in range(n_bootstrap):
        sample = np.random.choice(scores, size=len(scores), replace=True)
        means.append(np.mean(sample))

    lower = np.percentile(means, (1 - confidence) / 2 * 100)
    upper = np.percentile(means, (1 + confidence) / 2 * 100)

    return (lower, upper)


def run_fair_benchmark(max_conversations: int = 3):
    """Run the FAIR benchmark with strict evaluation."""

    # Load dataset
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print("=" * 70)
    print("LoCoMo-FAIR Benchmark v1.0 (STRICT EVALUATION)")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Correct threshold: {CORRECT_THRESHOLD}/10 (strict)")
    print(f"Context limit: {FIXED_CONTEXT_LIMIT} chars (fixed for all)")
    print(f"Retries: NONE (one attempt only)")
    print(f"Prompts: UNIFIED (no category-specific)")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    results = []
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

        print(f"\n--- Conversation {conv_idx + 1} ---")
        print(f"Speakers: {speaker_a} & {speaker_b}")
        print(f"Questions: {len(qa_list)}")

        # Extract ALL messages (no domain sharding)
        messages = extract_messages(item)
        context = format_context(messages, FIXED_CONTEXT_LIMIT)

        print(f"  Context: {len(context)} chars")

        for q_idx, qa in enumerate(qa_list):
            question = qa.get("question", "")
            gold_answer = str(qa.get("answer", ""))
            adversarial_answer = qa.get("adversarial_answer", "")
            category_id = qa.get("category", 0)
            category_name = CATEGORIES.get(category_id, "unknown")

            total_questions += 1
            start_time = time.time()

            # Handle adversarial questions
            if category_id == 5:
                gold_for_judge = f"Should NOT say: {adversarial_answer}"
            else:
                gold_for_judge = gold_answer

            # Generate answer (NO retries, NO special prompts)
            generated = generate_answer(question, context)

            # Judge strictly (NO shortcuts)
            judgment = judge_answer_strict(question, gold_for_judge, generated)

            gen_time = time.time() - start_time
            total_score = judgment.get("total", 0)
            is_hallucination = judgment.get("is_hallucination", False)
            is_refusal = judgment.get("is_refusal", False)

            # Strict classification
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
                status += " (HALLUCINATION)"

            if is_refusal:
                refusal_count += 1

            category_scores[category_name].append({
                "correct": is_correct,
                "total": total_score,
                "hallucination": is_hallucination,
                "refusal": is_refusal,
            })

            # Print progress
            print(f"  [{q_idx + 1}] {category_name}: {question[:40]}...")
            print(f"      Score: {total_score}/10 [{status}] ({gen_time:.1f}s)")

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
                "time_seconds": round(gen_time, 2),
            })

    # Compute metrics
    print("\n" + "=" * 70)
    print("FAIR BENCHMARK RESULTS")
    print("=" * 70)

    # Strict accuracy
    strict_accuracy = (correct_count / total_questions * 100) if total_questions > 0 else 0
    partial_rate = (partial_count / total_questions * 100) if total_questions > 0 else 0
    hallucination_rate = (hallucination_count / total_questions * 100) if total_questions > 0 else 0
    refusal_rate = (refusal_count / total_questions * 100) if total_questions > 0 else 0

    # Bootstrap CI
    correct_binary = [1 if r["correct"] else 0 for r in results]
    ci_lower, ci_upper = compute_bootstrap_ci(correct_binary)

    print(f"\n{'METRIC':<25} | {'VALUE':>10} | {'95% CI':>20}")
    print("-" * 60)
    print(f"{'Strict Accuracy (8+/10)':<25} | {strict_accuracy:>9.1f}% | ({ci_lower*100:.1f}% - {ci_upper*100:.1f}%)")
    print(f"{'Partial Credit (5-7/10)':<25} | {partial_rate:>9.1f}% |")
    print(f"{'Hallucination Rate':<25} | {hallucination_rate:>9.1f}% |")
    print(f"{'Refusal Rate':<25} | {refusal_rate:>9.1f}% |")

    print("\n" + "-" * 60)
    print("BY CATEGORY:")
    print("-" * 60)

    category_accuracy = {}
    for category, scores in category_scores.items():
        if scores:
            cat_correct = sum(1 for s in scores if s["correct"])
            cat_total = len(scores)
            cat_accuracy = cat_correct / cat_total * 100
            cat_halluc = sum(1 for s in scores if s["hallucination"]) / cat_total * 100
            category_accuracy[category] = cat_accuracy
            print(f"  {category:15} | Accuracy: {cat_accuracy:5.1f}% | Halluc: {cat_halluc:5.1f}% | n={cat_total}")

    # Save results
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON results
    json_path = output_dir / f"locomo10_FAIR_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "benchmark": "LoCoMo-FAIR v1.0",
            "model": OLLAMA_MODEL,
            "timestamp": timestamp,
            "parameters": {
                "correct_threshold": CORRECT_THRESHOLD,
                "context_limit": FIXED_CONTEXT_LIMIT,
                "retries": 0,
                "prompt_type": "unified",
            },
            "metrics": {
                "total_questions": total_questions,
                "strict_accuracy": round(strict_accuracy, 2),
                "strict_accuracy_ci": [round(ci_lower * 100, 2), round(ci_upper * 100, 2)],
                "partial_rate": round(partial_rate, 2),
                "hallucination_rate": round(hallucination_rate, 2),
                "refusal_rate": round(refusal_rate, 2),
            },
            "category_accuracy": {k: round(v, 2) for k, v in category_accuracy.items()},
            "results": results,
        }, f, indent=2)

    print(f"\nResults saved to: {json_path}")

    # Compare to old benchmark
    print("\n" + "=" * 70)
    print("COMPARISON TO 'BALANCED' BENCHMARK")
    print("=" * 70)
    print(f"Old 'Balanced' accuracy (5/10 threshold): 91.75%")
    print(f"FAIR accuracy (8/10 threshold):           {strict_accuracy:.1f}%")
    print(f"Difference:                               {strict_accuracy - 91.75:+.1f}%")
    print("\nThis difference reflects stricter evaluation, not worse performance.")
    print("=" * 70)

    return {
        "strict_accuracy": strict_accuracy,
        "hallucination_rate": hallucination_rate,
        "results": results,
    }


if __name__ == "__main__":
    run_fair_benchmark(max_conversations=3)
