"""
Test different Qwen model sizes for reranking accuracy.

Goal: Find smallest model that achieves 90%+ accuracy overlap with Qwen 7B.
"""

import asyncio
import aiohttp
import time
import re
import json
from pathlib import Path

OLLAMA_BASE_URL = "http://localhost:11434"

# Models to test (smallest to largest)
MODELS = [
    "qwen2.5:0.5b-instruct",
    "qwen2.5:1.5b-instruct",
    "qwen2.5:3b-instruct",
    "qwen2.5:7b-instruct",  # Gold standard
]

LOCOMO_PATH = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"


async def check_model_exists(session, model):
    """Check if model is available in Ollama."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": "test", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            return resp.status == 200
    except:
        return False


async def score_single(session, model, question, text, speaker):
    """Score a single memory with specified model."""
    prompt = f"""Question: {question}
Memory from [{speaker}]: {text[:150]}

Score (0-3): 0=wrong person/unrelated, 2=related, 3=answers
Output only the number:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 5}},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            digits = re.findall(r'[0-3]', response)
            return int(digits[0]) if digits else 1
    except:
        return 1


async def parallel_score_all(session, model, question, memories):
    """Score all memories in parallel with specified model."""
    tasks = [
        score_single(session, model, question, text, speaker)
        for text, speaker in memories
    ]
    scores = await asyncio.gather(*tasks)

    # Sort by score descending, return top 15 indices
    indexed = list(enumerate(scores))
    indexed.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, score in indexed[:15]]


async def main():
    print("="*60)
    print("QWEN MODEL SIZE COMPARISON")
    print("="*60)
    print("Goal: Find smallest model with 90%+ accuracy\n")

    # Load data
    with open(LOCOMO_PATH) as f:
        data = json.load(f)

    questions_data = []
    for conv in data:  # ALL conversations
        conversation = conv.get("conversation", conv)
        messages = []
        session_idx = 1
        while f"session_{session_idx}" in conversation:
            for msg in conversation[f"session_{session_idx}"]:
                messages.append((msg.get("text", ""), msg.get("speaker", "Unknown")))
            session_idx += 1

        for qa in conv.get("qa", []):  # ALL questions
            question = qa.get("question", "")
            category = qa.get("category", 1)
            # Skip adversarial (category 5)
            if question and category != 5:
                questions_data.append((question, messages[:50]))

    # Limit to 200 questions for faster testing
    questions_data = questions_data[:200]
    print(f"Testing on {len(questions_data)} questions\n")

    async with aiohttp.ClientSession() as session:
        # Check which models are available
        available_models = []
        print("Checking available models...")
        for model in MODELS:
            exists = await check_model_exists(session, model)
            status = "OK" if exists else "NOT FOUND"
            print(f"  {model}: {status}")
            if exists:
                available_models.append(model)

        if not available_models:
            print("\nNo models available! Run: ollama pull qwen2.5:3b-instruct")
            return

        # Use largest available as gold standard
        gold_model = available_models[-1]
        print(f"\nGold standard: {gold_model}")

        # Test each model - GOLD STANDARD FIRST
        results = {"gold_results": {}}

        # Reorder to run gold standard first
        models_ordered = [gold_model] + [m for m in available_models if m != gold_model]

        for model in models_ordered:
            print(f"\n{'='*40}")
            print(f"Testing: {model}")
            print(f"{'='*40}")

            times = []
            overlaps = []

            for q_idx, (question, memories) in enumerate(questions_data):
                # Get gold standard (7B) results
                if model == gold_model:
                    t1 = time.perf_counter()
                    top15 = await parallel_score_all(session, model, question, memories)
                    elapsed = (time.perf_counter() - t1) * 1000
                    times.append(elapsed)
                    overlaps.append(1.0)  # 100% overlap with itself

                    # Store gold results for comparison
                    if "gold_results" not in results:
                        results["gold_results"] = {}
                    results["gold_results"][q_idx] = set(top15)
                else:
                    # Compare against gold
                    t1 = time.perf_counter()
                    top15 = await parallel_score_all(session, model, question, memories)
                    elapsed = (time.perf_counter() - t1) * 1000
                    times.append(elapsed)

                    gold_top15 = results["gold_results"].get(q_idx, set())
                    overlap = len(set(top15) & gold_top15) / 15 if gold_top15 else 0
                    overlaps.append(overlap)

                print(f"  Q{q_idx+1}: {elapsed:.0f}ms, overlap={overlaps[-1]*100:.0f}%")

            avg_time = sum(times) / len(times)
            avg_overlap = sum(overlaps) / len(overlaps) * 100

            results[model] = {
                "avg_time_ms": avg_time,
                "avg_overlap": avg_overlap,
                "min_overlap": min(overlaps) * 100,
            }

            print(f"\n  Average: {avg_time:.0f}ms, {avg_overlap:.0f}% overlap")

        # Summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        print(f"{'Model':<25} {'Time (ms)':<12} {'Accuracy':<12} {'Status'}")
        print("-"*60)

        for model in available_models:
            if model in results and model != "gold_results":
                r = results[model]
                status = "PASS" if r["avg_overlap"] >= 90 else "FAIL"
                print(f"{model:<25} {r['avg_time_ms']:<12.0f} {r['avg_overlap']:<10.1f}%  [{status}]")

        # Find best model that passes
        passing_models = [
            (m, results[m]) for m in available_models
            if m in results and m != "gold_results" and results[m]["avg_overlap"] >= 90
        ]

        if passing_models:
            # Sort by time (fastest first)
            passing_models.sort(key=lambda x: x[1]["avg_time_ms"])
            best_model, best_stats = passing_models[0]
            print(f"\nBest model: {best_model}")
            print(f"  Speed: {best_stats['avg_time_ms']:.0f}ms")
            print(f"  Accuracy: {best_stats['avg_overlap']:.1f}%")

            speedup = results[gold_model]["avg_time_ms"] / best_stats["avg_time_ms"]
            print(f"  Speedup vs 7B: {speedup:.1f}x")
        else:
            print(f"\nNo model achieved 90%+ accuracy.")
            print("Recommendation: Use Qwen 7B for required accuracy.")


if __name__ == "__main__":
    asyncio.run(main())
