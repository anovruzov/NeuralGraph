"""
Top-K Selection Reranking: Single LLM call returns the K most relevant memory indices.

Instead of scoring all 50, ask: "Which 15 are most relevant? Return their indices."

Advantages:
1. One LLM call
2. Small output (just 15 indices vs 50 scores)
3. Model does comparative reasoning internally
4. Fastest possible reranking
"""

import asyncio
import aiohttp
import time
import json
import re

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "phi3.5"

QUESTION = "What is Sarah's favorite restaurant in New York?"

# 50 test memories
MEMORIES = [
    ("Sarah mentioned she loves the Italian place on 5th Avenue", "Sarah"),
    ("John said he prefers Chinese food downtown", "John"),
    ("Sarah went to Carbone last Tuesday and said it was amazing", "Sarah"),
    ("The weather in New York was cold yesterday", "Mike"),
    ("Sarah's birthday is in March", "Sarah"),
    ("I recommended a great sushi place to Sarah", "John"),
    ("Sarah said Carbone is her absolute favorite restaurant ever", "Sarah"),
    ("New York has over 27,000 restaurants", "Guide"),
    ("Sarah doesn't like fast food at all", "Sarah"),
    ("The best pizza in NYC is at Joe's Pizza", "Mike"),
    ("Sarah visited the MoMA museum last week", "Sarah"),
    ("John and Sarah had dinner at a steakhouse", "John"),
    ("Sarah prefers Italian cuisine over other types", "Sarah"),
    ("The subway was delayed yesterday morning", "Mike"),
    ("Sarah is planning a trip to Italy next year", "Sarah"),
    ("Caroline went to a support group meeting", "Caroline"),
    ("Sarah loves the pasta at this one place in Manhattan", "Sarah"),
    ("John doesn't like Italian food much", "John"),
    ("Sarah mentioned Carbone has the best ambiance", "Sarah"),
    ("The rent in NYC is very expensive", "Guide"),
] * 2 + [
    ("Sarah had brunch at Balthazar last Sunday", "Sarah"),
    ("Mike recommended a burger place to John", "Mike"),
    ("Sarah's mom loves French cuisine", "Sarah"),
    ("The restaurant was crowded on Friday night", "John"),
    ("Sarah made reservations at her favorite spot", "Sarah"),
    ("Sarah always orders the rigatoni at Carbone", "Sarah"),
    ("John thinks pizza is better than pasta", "John"),
    ("Sarah celebrated her promotion at Carbone", "Sarah"),
    ("The subway line 6 was running late", "Mike"),
    ("Sarah recommends Carbone to all her friends", "Sarah"),
]


async def topk_rerank(session, question: str, memories: list[tuple], k: int = 15) -> tuple[list[int], float, str]:
    """
    Single LLM call to select top-K most relevant memory indices.
    """
    memory_list = "\n".join([
        f"[{i}] [{speaker}]: {text[:150]}"
        for i, (text, speaker) in enumerate(memories)
    ])

    prompt = f"""<|user|>
Select the {k} most relevant memories for answering this question.

Question: {question}

Memories:
{memory_list}

Return ONLY a JSON array of the {k} most relevant memory indices, ranked by relevance (most relevant first).
Example: [6, 2, 0, 12, 8, ...]
<|end|>
<|assistant|>["""

    start = time.perf_counter()
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 80}
            },
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            result = await resp.json()
            elapsed = (time.perf_counter() - start) * 1000
            response = result.get("response", "").strip()

            # Parse JSON array
            try:
                full_response = "[" + response
                end_idx = full_response.rfind("]") + 1
                if end_idx > 1:
                    indices = json.loads(full_response[:end_idx])
                    # Filter valid indices
                    valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(memories)]
                    return valid[:k], elapsed, response
            except:
                # Fallback: extract numbers
                numbers = re.findall(r'\d+', response)
                indices = [int(n) for n in numbers if int(n) < len(memories)]
                return indices[:k], elapsed, response

            return [], elapsed, response

    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return [], elapsed, str(e)


async def sequential_rerank(session, question: str, memories: list[tuple], k: int = 15) -> tuple[list[int], float]:
    """Original sequential approach - score all, return top-K indices."""
    scores = []
    start = time.perf_counter()

    for idx, (text, speaker) in enumerate(memories):
        prompt = f"""<|user|>
Score relevance 0-3. Output only the number.
Question: {question}
Memory [{speaker}]: {text[:150]}
<|end|>
<|assistant|>"""

        try:
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0, "num_predict": 5}},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                result = await resp.json()
                response = result.get("response", "").strip()
                score = 1
                for char in response:
                    if char in "0123":
                        score = int(char)
                        break
                scores.append((idx, score))
        except:
            scores.append((idx, 1))

    elapsed = (time.perf_counter() - start) * 1000

    # Sort by score descending, return top-K indices
    scores.sort(key=lambda x: x[1], reverse=True)
    top_indices = [idx for idx, score in scores[:k]]

    return top_indices, elapsed


async def main():
    print("="*60)
    print("TOP-K SELECTION RERANKING TEST")
    print("="*60)
    print(f"Question: {QUESTION}")
    print(f"Memories: {len(MEMORIES)}")
    print(f"Selecting: Top 15")

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up model...")
        await topk_rerank(session, "test", [("test", "test")], k=1)

        # Test 1: Sequential (baseline)
        print("\n[1] SEQUENTIAL (score all 50, return top 15)...")
        seq_indices, seq_time = await sequential_rerank(session, QUESTION, MEMORIES, k=15)
        print(f"    Time: {seq_time:.0f}ms")
        print(f"    Top indices: {seq_indices[:10]}...")

        # Test 2: Top-K selection (single call)
        print("\n[2] TOP-K SELECTION (single LLM call)...")
        topk_indices, topk_time, raw = await topk_rerank(session, QUESTION, MEMORIES, k=15)
        print(f"    Time: {topk_time:.0f}ms")
        print(f"    Top indices: {topk_indices[:10]}...")

        # Summary
        speedup = seq_time / topk_time
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")
        print(f"Sequential: {seq_time:.0f}ms (50 LLM calls)")
        print(f"Top-K:      {topk_time:.0f}ms (1 LLM call)")
        print(f"Speedup:    {speedup:.1f}x")

        # Show what was selected
        print(f"\n{'='*60}")
        print("TOP 5 SELECTED MEMORIES")
        print(f"{'='*60}")
        for rank, idx in enumerate(topk_indices[:5], 1):
            text, speaker = MEMORIES[idx]
            print(f"  {rank}. [{idx}] [{speaker}]: {text[:60]}...")

        # Compare overlap
        seq_set = set(seq_indices[:10])
        topk_set = set(topk_indices[:10])
        overlap = len(seq_set & topk_set)
        print(f"\n  Overlap in top 10: {overlap}/10 ({overlap*10}% agreement)")

        # Extrapolate to benchmark
        print(f"\n{'='*60}")
        print("BENCHMARK EXTRAPOLATION")
        print(f"{'='*60}")
        print(f"Original benchmark reranking: ~5,700ms")
        print(f"With Top-K selection:         ~{topk_time:.0f}ms")
        print(f"Projected speedup:            ~{5700/topk_time:.0f}x")


if __name__ == "__main__":
    asyncio.run(main())
