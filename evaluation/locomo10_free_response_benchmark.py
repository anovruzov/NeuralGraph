"""
LoCoMo10 Free Response Benchmark
Tests Qwen's ability to answer questions from the actual LoCoMo10 dataset
using conversation context as retrieved memory.
"""

import json
import csv
import time
from datetime import datetime
from pathlib import Path
from openai import OpenAI

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
    5: "adversarial",  # Usually skipped
}

MAX_CONTEXT_CHARS = 12000  # Limit context to ~2000 words

ANSWER_PROMPT = """You are a helpful AI assistant with access to conversation memories. Based on the conversation context provided, answer the user's question as accurately as possible.

### CONVERSATION CONTEXT:
{context}

### QUESTION:
{question}

### INSTRUCTIONS:
- Answer based ONLY on the information in the conversation context
- Be concise and specific
- If the information is not in the context, say "I don't have that information"
- For dates, be as specific as possible based on the conversation timestamps

### YOUR ANSWER:"""

JUDGE_PROMPT = """You are an accuracy judge. Compare the generated answer against the gold (correct) answer and determine if they match.

### QUESTION:
{question}

### GOLD ANSWER (Ground Truth):
{gold_answer}

### GENERATED ANSWER:
{generated_answer}

### INSTRUCTIONS:
- Be generous with grading - if the generated answer captures the same meaning/information, mark as CORRECT
- For dates, accept different formats (e.g., "May 7" vs "7 May 2023") if they refer to the same date
- For time references, if the generated answer correctly interprets relative dates based on context, mark as CORRECT
- If the answer is partially correct but missing key details, mark as WRONG

Output ONLY a JSON object:
{{"correct": true/false, "reasoning": "brief explanation"}}"""


def extract_conversation_text(item: dict) -> str:
    """Extract all conversation sessions into readable text."""
    text_parts = []

    # The conversation data might be nested under 'conversation' key or at root
    conversation = item.get("conversation", item)

    speakers = f"Speakers: {conversation.get('speaker_a', 'Person A')} and {conversation.get('speaker_b', 'Person B')}"
    text_parts.append(speakers)
    text_parts.append("")

    session_idx = 1
    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if session_key not in conversation:
            break

        session_time = conversation.get(datetime_key, "Unknown time")
        text_parts.append(f"=== Session {session_idx} ({session_time}) ===")

        for msg in conversation[session_key]:
            if isinstance(msg, dict):
                speaker = msg.get("speaker", "Unknown")
                text = msg.get("text", "")
                dia_id = msg.get("dia_id", "")
                text_parts.append(f"[{dia_id}] {speaker}: {text}")
            elif isinstance(msg, str):
                # Handle string format
                text_parts.append(msg)

        text_parts.append("")
        session_idx += 1

    return "\n".join(text_parts)


def generate_answer(question: str, context: str) -> str:
    """Generate an answer using Qwen."""
    # Truncate context if too long
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n... [context truncated]"
    prompt = ANSWER_PROMPT.format(context=context, question=question)

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"


def judge_answer(question: str, gold_answer: str, generated_answer: str) -> dict:
    """Judge if the generated answer is correct."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        gold_answer=gold_answer,
        generated_answer=generated_answer,
    )

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = response.choices[0].message.content.strip()

        # Parse JSON
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        result = json.loads(content)
        return result
    except Exception as e:
        # Fallback: simple string matching
        gold_lower = str(gold_answer).lower()
        gen_lower = generated_answer.lower()
        is_correct = gold_lower in gen_lower or gen_lower in gold_lower
        return {"correct": is_correct, "reasoning": f"Fallback matching: {str(e)}"}


def run_benchmark(max_conversations: int = 3, max_questions_per_conv: int = 10):
    """Run the LoCoMo10 free response benchmark."""

    # Load dataset
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print("=" * 70)
    print("LoCoMo10 Free Response Benchmark")
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Conversations to test: {min(max_conversations, len(dataset))}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    results = []
    category_scores = {cat: [] for cat in CATEGORIES.values()}

    total_questions = 0
    correct_count = 0

    for conv_idx, item in enumerate(dataset[:max_conversations]):
        conversation = item.get("conversation", item)
        qa_list = item.get("qa", [])

        # Extract conversation text as context
        context = extract_conversation_text(item)

        print(f"\n--- Conversation {conv_idx + 1} ---")
        print(f"Speakers: {conversation.get('speaker_a')} & {conversation.get('speaker_b')}")
        print(f"Questions: {len(qa_list)}")
        print(f"Context length: {len(context)} chars")

        # Debug: Print first 500 chars of context to verify extraction
        if conv_idx == 0:
            print(f"Context preview: {context[:500]}...")

        for q_idx, qa in enumerate(qa_list[:max_questions_per_conv]):
            question = qa.get("question", "")
            gold_answer = str(qa.get("answer", ""))
            category_id = qa.get("category", 0)
            category_name = CATEGORIES.get(category_id, "unknown")

            # Skip adversarial questions (category 5)
            if category_id == 5:
                continue

            total_questions += 1
            print(f"\n  [{q_idx + 1}] {category_name}: {question[:50]}...")

            # Generate answer
            start_time = time.time()
            generated = generate_answer(question, context)
            gen_time = time.time() - start_time

            # Judge answer
            judgment = judge_answer(question, gold_answer, generated)
            is_correct = judgment.get("correct", False)

            if is_correct:
                correct_count += 1
                category_scores[category_name].append(1)
                status = "CORRECT"
            else:
                category_scores[category_name].append(0)
                status = "WRONG"

            print(f"      Gold: {gold_answer}")
            print(f"      Generated: {generated[:100]}...")
            print(f"      [{status}] ({gen_time:.1f}s)")

            results.append({
                "conversation_id": conv_idx,
                "question_id": q_idx,
                "category": category_name,
                "question": question,
                "gold_answer": gold_answer,
                "generated_answer": generated,
                "correct": is_correct,
                "reasoning": judgment.get("reasoning", ""),
                "time_seconds": round(gen_time, 2),
            })

    # Save results
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"locomo10_free_response_{timestamp}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    # Print summary
    print("\n" + "=" * 70)
    print("BENCHMARK SUMMARY")
    print("=" * 70)

    accuracy = (correct_count / total_questions * 100) if total_questions > 0 else 0

    for category, scores in category_scores.items():
        if scores:
            cat_accuracy = sum(scores) / len(scores) * 100
            print(f"{category:15} | Accuracy: {cat_accuracy:5.1f}% | Count: {len(scores)}")

    print("-" * 70)
    print(f"{'OVERALL':15} | Accuracy: {accuracy:5.1f}% | Total: {total_questions}")
    print(f"\nResults saved to: {csv_path}")

    # Save JSON summary
    json_path = output_dir / f"locomo10_free_response_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": OLLAMA_MODEL,
            "timestamp": timestamp,
            "total_questions": total_questions,
            "correct_count": correct_count,
            "overall_accuracy": accuracy,
            "category_accuracy": {
                k: (sum(v)/len(v)*100 if v else 0)
                for k, v in category_scores.items()
            },
            "results": results,
        }, f, indent=2)

    print(f"JSON saved to: {json_path}")

    return results


if __name__ == "__main__":
    # Run on first 3 conversations, up to 10 questions each
    run_benchmark(max_conversations=3, max_questions_per_conv=10)
