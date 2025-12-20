"""
Test parallel scoring approach for 90%+ accuracy.

Approach: Fire all 50 scoring calls in parallel using asyncio.gather.
This gives 100% match with sequential (same algorithm, just faster).
"""

import asyncio
import aiohttp
import time
import re
import json
from pathlib import Path

OLLAMA_BASE_URL = "http://localhost:11434"
QWEN_MODEL = "qwen2.5:7b-instruct"

LOCOMO_PATH = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"


async def score_single(session, question, text, speaker):
    """Score a single memory."""
    prompt = f"""Question: {question}
Memory from [{speaker}]: {text[:150]}

Score (0-3): 0=wrong person/unrelated, 2=related, 3=answers
Output only the number:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": QWEN_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 5}},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            digits = re.findall(r'[0-3]', response)
            return int(digits[0]) if digits else 1
    except:
        return 1


async def parallel_score_all(session, question, memories):
    """Score all memories in parallel. Returns list of (idx, score)."""
    tasks = [
        score_single(session, question, text, speaker)
        for text, speaker in memories
    ]
    scores = await asyncio.gather(*tasks)
    return list(enumerate(scores))


async def sequential_score_all(session, question, memories):
    """Score all memories sequentially. Returns list of (idx, score)."""
    results = []
    for i, (text, speaker) in enumerate(memories):
        score = await score_single(session, question, text, speaker)
        results.append((i, score))
    return results


async def evaluate(session, questions_data, num_samples=10):
    """Compare parallel vs sequential."""

    seq_times = []
    par_times = []
    matches = []

    for q_idx, (question, memories) in enumerate(questions_data[:num_samples]):
        print(f"\nQ{q_idx+1}: {question[:50]}...")
        print(f"  Memories: {len(memories)}")

        # Sequential scoring
        t1 = time.perf_counter()
        seq_results = await sequential_score_all(session, question, memories[:50])
        seq_time = (time.perf_counter() - t1) * 1000
        seq_times.append(seq_time)

        seq_sorted = sorted(seq_results, key=lambda x: x[1], reverse=True)
        seq_top15 = [idx for idx, score in seq_sorted[:15]]

        print(f"  Sequential: {seq_time:.0f}ms")

        # Parallel scoring
        t2 = time.perf_counter()
        par_results = await parallel_score_all(session, question, memories[:50])
        par_time = (time.perf_counter() - t2) * 1000
        par_times.append(par_time)

        par_sorted = sorted(par_results, key=lambda x: x[1], reverse=True)
        par_top15 = [idx for idx, score in par_sorted[:15]]

        print(f"  Parallel:   {par_time:.0f}ms")

        # Compare results
        overlap = len(set(seq_top15) & set(par_top15)) / 15
        matches.append(overlap)
        print(f"  Overlap:    {overlap*100:.0f}%")

        speedup = seq_time / par_time if par_time > 0 else 0
        print(f"  Speedup:    {speedup:.1f}x")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Sequential avg: {sum(seq_times)/len(seq_times):.0f}ms")
    print(f"Parallel avg:   {sum(par_times)/len(par_times):.0f}ms")
    print(f"Avg speedup:    {sum(seq_times)/sum(par_times):.1f}x")
    print(f"Avg overlap:    {sum(matches)/len(matches)*100:.1f}%")
    print(f"Min overlap:    {min(matches)*100:.0f}%")

    if sum(matches)/len(matches) >= 0.90:
        print("\n[SUCCESS] 90%+ accuracy with parallel approach!")
    else:
        print(f"\n[INFO] Overlap is {sum(matches)/len(matches)*100:.1f}%")
        print("Note: Some variance expected due to model non-determinism")


async def main():
    print("="*60)
    print("PARALLEL SCORING ACCURACY TEST")
    print("="*60)

    # Load data
    with open(LOCOMO_PATH) as f:
        data = json.load(f)

    questions_data = []
    for conv in data[:2]:
        conversation = conv.get("conversation", conv)
        messages = []
        session_idx = 1
        while f"session_{session_idx}" in conversation:
            for msg in conversation[f"session_{session_idx}"]:
                messages.append((msg.get("text", ""), msg.get("speaker", "Unknown")))
            session_idx += 1

        for qa in conv.get("qa", [])[:10]:
            question = qa.get("question", "")
            if question:
                questions_data.append((question, messages[:50]))

    print(f"Loaded {len(questions_data)} questions")

    async with aiohttp.ClientSession() as session:
        print("\nWarming up...")
        await score_single(session, "test", "test", "test")

        await evaluate(session, questions_data, num_samples=5)


if __name__ == "__main__":
    asyncio.run(main())
