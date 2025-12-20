"""
Test Phi 3.5 parallel with improved prompt for 90%+ accuracy.

Goal: Faster than Qwen 7B but still 90%+ accuracy.
"""

import asyncio
import aiohttp
import time
import re
import json
from pathlib import Path

OLLAMA_BASE_URL = "http://localhost:11434"
PHI_MODEL = "phi3.5"
QWEN_MODEL = "qwen2.5:7b-instruct"

LOCOMO_PATH = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"


async def score_phi(session, question, text, speaker):
    """Score with Phi 3.5 + improved prompt."""
    prompt = f"""<|user|>
Question: {question}
Memory from [{speaker}]: {text[:150]}

RULES:
- Score 0 if memory is about a DIFFERENT person than the question asks about
- Score 0 if completely unrelated topic
- Score 3 if memory NAMES or CONTAINS the answer
- Score 2 if related supporting context

Output only 0, 2, or 3:
<|end|>
<|assistant|>"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": PHI_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 5}},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            digits = re.findall(r'[0-3]', response)
            return int(digits[0]) if digits else 1
    except:
        return 1


async def score_qwen(session, question, text, speaker):
    """Score with Qwen 7B (gold standard)."""
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


async def parallel_score(session, question, memories, score_fn):
    """Score all memories in parallel."""
    tasks = [score_fn(session, question, text, speaker) for text, speaker in memories]
    scores = await asyncio.gather(*tasks)
    indexed = list(enumerate(scores))
    indexed.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, score in indexed[:15]]


async def main():
    print("="*60)
    print("PHI 3.5 PARALLEL vs QWEN 7B ACCURACY TEST")
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

        for qa in conv.get("qa", [])[:5]:
            question = qa.get("question", "")
            if question:
                questions_data.append((question, messages[:50]))

    print(f"Testing on {len(questions_data)} questions")

    async with aiohttp.ClientSession() as session:
        # Warmup both models
        print("\nWarming up models...")
        await score_phi(session, "test", "test", "test")
        await score_qwen(session, "test", "test", "test")

        phi_times = []
        qwen_times = []
        overlaps = []

        for q_idx, (question, memories) in enumerate(questions_data):
            print(f"\nQ{q_idx+1}: {question[:50]}...")

            # Qwen (gold standard)
            t1 = time.perf_counter()
            qwen_top15 = await parallel_score(session, question, memories, score_qwen)
            qwen_time = (time.perf_counter() - t1) * 1000
            qwen_times.append(qwen_time)

            # Phi with improved prompt
            t2 = time.perf_counter()
            phi_top15 = await parallel_score(session, question, memories, score_phi)
            phi_time = (time.perf_counter() - t2) * 1000
            phi_times.append(phi_time)

            # Compare
            overlap = len(set(phi_top15) & set(qwen_top15)) / 15
            overlaps.append(overlap)

            print(f"  Qwen 7B: {qwen_time:.0f}ms")
            print(f"  Phi 3.5: {phi_time:.0f}ms ({phi_time/qwen_time*100:.0f}% of Qwen)")
            print(f"  Overlap: {overlap*100:.0f}%")

        # Summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        avg_qwen = sum(qwen_times) / len(qwen_times)
        avg_phi = sum(phi_times) / len(phi_times)
        avg_overlap = sum(overlaps) / len(overlaps) * 100

        print(f"Qwen 7B parallel:  {avg_qwen:.0f}ms avg")
        print(f"Phi 3.5 parallel:  {avg_phi:.0f}ms avg")
        print(f"Phi speedup:       {avg_qwen/avg_phi:.1f}x faster")
        print(f"Phi accuracy:      {avg_overlap:.1f}% overlap with Qwen")

        if avg_overlap >= 90:
            print(f"\n[SUCCESS] Phi 3.5 achieves {avg_overlap:.0f}% accuracy at {avg_phi:.0f}ms!")
        else:
            print(f"\n[FAIL] Phi 3.5 at {avg_overlap:.0f}% - need Qwen for 90%+")
            print(f"Recommendation: Use Qwen 7B parallel ({avg_qwen:.0f}ms) for 100% accuracy")


if __name__ == "__main__":
    asyncio.run(main())
