"""
Test reranking accuracy: Compare 3-batch Top-K vs sequential scoring.

Goal: Achieve 90%+ overlap with sequential (gold standard).
"""

import asyncio
import aiohttp
import time
import re
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OLLAMA_BASE_URL = "http://localhost:11434"
QWEN_MODEL = "qwen2.5:7b-instruct"

# Load real benchmark data
LOCOMO_PATH = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"


async def get_embedding(session, text):
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except:
        return []


async def sequential_score(session, question, memories, k=15):
    """Gold standard: Score each memory individually, return top-K."""
    scores = []

    for i, (text, speaker) in enumerate(memories):
        prompt = f"""Question: {question}
Memory from [{speaker}]: {text[:150]}

Score relevance (0-3):
0 = Wrong person OR unrelated
2 = Related context
3 = Directly answers

Score:"""

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
                score = int(digits[0]) if digits else 1
                scores.append((i, score))
        except:
            scores.append((i, 1))

    # Sort by score descending
    scores.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, score in scores[:k]]


async def topk_from_batch_v1(session, question, memories, k=5):
    """Original Top-K selection."""
    memory_list = "\n".join([
        f"{i}.[{speaker}]{text[:80]}"
        for i, (text, speaker) in enumerate(memories)
    ])

    prompt = f"""Q: {question}

{memory_list}

Select the {k} MOST relevant memory indices.
Rule: Only select memories from the person asked about that answer the question.
Output {k} numbers separated by commas:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": QWEN_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 30}},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            numbers = re.findall(r'\d+', response)
            return [int(n) for n in numbers if int(n) < len(memories)][:k]
    except:
        return list(range(min(k, len(memories))))


async def topk_from_batch_v2(session, question, memories, k=6):
    """Improved Top-K with better prompt."""
    memory_list = "\n".join([
        f"{i}.[{speaker}]{text[:100]}"
        for i, (text, speaker) in enumerate(memories)
    ])

    prompt = f"""Question: {question}

Memories:
{memory_list}

Task: Select the {k} memories MOST LIKELY to help answer the question.

RULES:
1. Prioritize memories from the SAME PERSON the question asks about
2. Select memories that contain or relate to the answer
3. Include supporting context if relevant

Output the {k} best memory indices as comma-separated numbers:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": QWEN_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 40}},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            numbers = re.findall(r'\d+', response)
            return [int(n) for n in numbers if int(n) < len(memories)][:k]
    except:
        return list(range(min(k, len(memories))))


async def three_batch_topk(session, question, memories, topk_fn, k_per_batch=5):
    """3-batch selection using specified topk function."""
    batch_size = len(memories) // 3 + 1
    batches = [
        (0, memories[0:batch_size]),
        (batch_size, memories[batch_size:batch_size*2]),
        (batch_size*2, memories[batch_size*2:])
    ]

    all_selected = []
    for offset, batch in batches:
        if batch:
            local_indices = await topk_fn(session, question, batch, k=k_per_batch)
            global_indices = [offset + i for i in local_indices if i < len(batch)]
            all_selected.extend(global_indices)

    return all_selected


async def evaluate_on_sample(session, questions_data, num_samples=10):
    """Evaluate reranking accuracy on sample questions."""

    results = {
        "v1_k5": {"overlaps": [], "times": []},
        "v2_k6": {"overlaps": [], "times": []},
        "v2_k7": {"overlaps": [], "times": []},
    }

    for q_idx, (question, memories) in enumerate(questions_data[:num_samples]):
        print(f"\nQ{q_idx+1}: {question[:50]}...")
        print(f"  Memories: {len(memories)}")

        # Gold standard: Sequential scoring
        t0 = time.perf_counter()
        gold_top15 = await sequential_score(session, question, memories, k=15)
        gold_time = (time.perf_counter() - t0) * 1000
        print(f"  Gold (sequential): {gold_time:.0f}ms -> {gold_top15[:5]}...")

        # Test V1: Original 3-batch with k=5
        t1 = time.perf_counter()
        v1_selected = await three_batch_topk(session, question, memories, topk_from_batch_v1, k_per_batch=5)
        v1_time = (time.perf_counter() - t1) * 1000
        v1_overlap = len(set(v1_selected) & set(gold_top15)) / len(gold_top15)
        results["v1_k5"]["overlaps"].append(v1_overlap)
        results["v1_k5"]["times"].append(v1_time)
        print(f"  V1 (k=5): {v1_time:.0f}ms, overlap={v1_overlap*100:.0f}%")

        # Test V2: Improved prompt with k=6
        t2 = time.perf_counter()
        v2_selected = await three_batch_topk(session, question, memories, topk_from_batch_v2, k_per_batch=6)
        v2_time = (time.perf_counter() - t2) * 1000
        v2_overlap = len(set(v2_selected) & set(gold_top15)) / len(gold_top15)
        results["v2_k6"]["overlaps"].append(v2_overlap)
        results["v2_k6"]["times"].append(v2_time)
        print(f"  V2 (k=6): {v2_time:.0f}ms, overlap={v2_overlap*100:.0f}%")

        # Test V2: With k=7 per batch
        t3 = time.perf_counter()
        v2_k7_selected = await three_batch_topk(session, question, memories, topk_from_batch_v2, k_per_batch=7)
        v2_k7_time = (time.perf_counter() - t3) * 1000
        v2_k7_overlap = len(set(v2_k7_selected) & set(gold_top15)) / len(gold_top15)
        results["v2_k7"]["overlaps"].append(v2_k7_overlap)
        results["v2_k7"]["times"].append(v2_k7_time)
        print(f"  V2 (k=7): {v2_k7_time:.0f}ms, overlap={v2_k7_overlap*100:.0f}%")

    return results


async def main():
    print("="*60)
    print("RERANKING ACCURACY TEST")
    print("="*60)
    print("Goal: 90%+ overlap with sequential gold standard")

    # Load LoCoMo data
    with open(LOCOMO_PATH) as f:
        data = json.load(f)

    # Extract questions and their memory contexts
    questions_data = []

    for conv in data[:2]:  # First 2 conversations
        conversation = conv.get("conversation", conv)

        # Collect all messages
        messages = []
        session_idx = 1
        while f"session_{session_idx}" in conversation:
            for msg in conversation[f"session_{session_idx}"]:
                messages.append((msg.get("text", ""), msg.get("speaker", "Unknown")))
            session_idx += 1

        # Get QA pairs
        for qa in conv.get("qa", [])[:10]:
            question = qa.get("question", "")
            if question:
                # Use first 50 messages as memory candidates
                questions_data.append((question, messages[:50]))

    print(f"\nLoaded {len(questions_data)} questions for testing")

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up Qwen 7B...")
        await topk_from_batch_v1(session, "test", [("test", "test")], k=1)

        # Run evaluation
        results = await evaluate_on_sample(session, questions_data, num_samples=10)

        # Summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")

        for name, data in results.items():
            avg_overlap = sum(data["overlaps"]) / len(data["overlaps"]) * 100
            avg_time = sum(data["times"]) / len(data["times"])
            min_overlap = min(data["overlaps"]) * 100

            status = "[PASS]" if avg_overlap >= 90 else "[FAIL]"
            print(f"{status} {name}: {avg_overlap:.1f}% avg overlap, {min_overlap:.0f}% min, {avg_time:.0f}ms")

        # Best method
        best = max(results.items(), key=lambda x: sum(x[1]["overlaps"]))
        best_avg = sum(best[1]["overlaps"]) / len(best[1]["overlaps"]) * 100

        print(f"\nBest: {best[0]} with {best_avg:.1f}% average overlap")

        if best_avg >= 90:
            print("\n[SUCCESS] 90%+ reranking accuracy achieved!")
        else:
            print(f"\n[NEEDS WORK] Best is {best_avg:.1f}%, need 90%+")


if __name__ == "__main__":
    asyncio.run(main())
