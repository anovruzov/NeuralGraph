"""
3-Batch Top-K Selection: Each batch selects best 5, then combine.

50 memories -> 3 batches of 17 -> each selects top 5 -> 15 candidates
Then optionally: 1 more call to pick final top 10 from 15

Total: 3-4 LLM calls instead of 50
"""

import asyncio
import aiohttp
import time
import json
import re

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "qwen2.5:7b-instruct"

QUESTION = "What is Sarah's favorite restaurant in New York?"

# 50 test memories with known best answers
MEMORIES = [
    ("Sarah mentioned she loves the Italian place on 5th Avenue", "Sarah"),  # Good
    ("John said he prefers Chinese food downtown", "John"),  # Wrong person
    ("Sarah went to Carbone last Tuesday and said it was amazing", "Sarah"),  # BEST
    ("The weather in New York was cold yesterday", "Mike"),  # Irrelevant
    ("Sarah's birthday is in March", "Sarah"),  # Wrong topic
    ("I recommended a great sushi place to Sarah", "John"),  # Weak
    ("Sarah said Carbone is her absolute favorite restaurant ever", "Sarah"),  # BEST
    ("New York has over 27,000 restaurants", "Guide"),  # Irrelevant
    ("Sarah doesn't like fast food at all", "Sarah"),  # Weak
    ("The best pizza in NYC is at Joe's Pizza", "Mike"),  # Wrong person
    ("Sarah visited the MoMA museum last week", "Sarah"),  # Wrong topic
    ("John and Sarah had dinner at a steakhouse", "John"),  # Weak
    ("Sarah prefers Italian cuisine over other types", "Sarah"),  # Good
    ("The subway was delayed yesterday morning", "Mike"),  # Irrelevant
    ("Sarah is planning a trip to Italy next year", "Sarah"),  # Weak
    ("Caroline went to a support group meeting", "Caroline"),  # Wrong person
    ("Sarah loves the pasta at this one place in Manhattan", "Sarah"),  # Good
]

# Expand to 50
MEMORIES = MEMORIES + [
    ("John doesn't like Italian food much", "John"),
    ("Sarah mentioned Carbone has the best ambiance", "Sarah"),  # Good
    ("The rent in NYC is very expensive", "Guide"),
    ("Sarah always orders the rigatoni at Carbone", "Sarah"),  # BEST
    ("Mike had lunch at a diner yesterday", "Mike"),
    ("Sarah celebrated her promotion at Carbone", "Sarah"),  # Good
    ("John thinks pizza is better than pasta", "John"),
    ("Sarah recommends Carbone to all her friends", "Sarah"),  # Good
] + [("Random memory about nothing important", "Unknown")] * 25

MEMORIES = MEMORIES[:50]

# Expected best indices (memories that directly answer the question)
EXPECTED_BEST = {2, 6, 18, 20, 22, 24}  # Carbone mentions


async def topk_from_batch(session, question: str, memories: list[tuple], k: int = 5) -> list[int]:
    """Select top-K indices from a batch of memories."""

    memory_list = "\n".join([
        f"{i}.[{speaker}]{text[:80]}"
        for i, (text, speaker) in enumerate(memories)
    ])

    prompt = f"""Q: {question}

{memory_list}

Select the {k} MOST relevant memory indices (numbers only).
Rule: Only select memories from the person asked about that answer the question.

Output {k} numbers separated by commas:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 30}},
            timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()

            # Extract all numbers
            numbers = re.findall(r'\d+', response)
            indices = [int(n) for n in numbers if int(n) < len(memories)]

            return indices[:k]

    except Exception as e:
        print(f"TopK error: {e}")
        return list(range(min(k, len(memories))))


async def three_batch_topk(session, question: str, memories: list[tuple]) -> tuple[list[int], float]:
    """3-batch top-K selection."""
    start = time.perf_counter()

    batch_size = len(memories) // 3 + 1
    batches = [
        (0, memories[0:batch_size]),
        (batch_size, memories[batch_size:batch_size*2]),
        (batch_size*2, memories[batch_size*2:])
    ]

    # Stage 1: Get top 5 from each batch
    all_candidates = []
    for offset, batch in batches:
        if batch:
            local_indices = await topk_from_batch(session, question, batch, k=5)
            global_indices = [offset + i for i in local_indices]
            all_candidates.extend(global_indices)
            print(f"  Batch (offset {offset}): selected {local_indices} -> global {global_indices}")

    elapsed = (time.perf_counter() - start) * 1000
    return all_candidates, elapsed


async def four_batch_topk(session, question: str, memories: list[tuple]) -> tuple[list[int], float]:
    """3-batch top-K selection + 1 final selection."""
    start = time.perf_counter()

    batch_size = len(memories) // 3 + 1
    batches = [
        (0, memories[0:batch_size]),
        (batch_size, memories[batch_size:batch_size*2]),
        (batch_size*2, memories[batch_size*2:])
    ]

    # Stage 1: Get top 5 from each batch (3 calls)
    all_candidates = []
    for offset, batch in batches:
        if batch:
            local_indices = await topk_from_batch(session, question, batch, k=5)
            global_indices = [offset + i for i in local_indices]
            all_candidates.extend(global_indices)

    print(f"  Stage 1: {len(all_candidates)} candidates from 3 batches")

    # Stage 2: Pick final top 10 from the 15 candidates (1 call)
    candidate_memories = [(memories[i], i) for i in all_candidates if i < len(memories)]

    # Build prompt for final selection
    final_list = "\n".join([
        f"{i}.[{mem[0][1]}]{mem[0][0][:80]}"
        for i, mem in enumerate(candidate_memories)
    ])

    prompt = f"""Q: {question}

{final_list}

From these {len(candidate_memories)} pre-selected memories, pick the 10 BEST indices.
Output 10 numbers separated by commas:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 50}},
            timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()

            numbers = re.findall(r'\d+', response)
            final_local = [int(n) for n in numbers if int(n) < len(candidate_memories)]
            final_global = [candidate_memories[i][1] for i in final_local[:10]]

            print(f"  Stage 2: Final top 10 = {final_global}")

    except Exception as e:
        print(f"Final selection error: {e}")
        final_global = all_candidates[:10]

    elapsed = (time.perf_counter() - start) * 1000
    return final_global, elapsed


async def main():
    print("="*60)
    print("3-BATCH TOP-K SELECTION")
    print("="*60)
    print(f"Question: {QUESTION}")
    print(f"Memories: {len(MEMORIES)}")
    print(f"Expected best indices: {EXPECTED_BEST}")

    async with aiohttp.ClientSession() as session:
        print("\nWarming up...")
        await topk_from_batch(session, "test", [("test", "test")], k=1)

        # Test 1: 3-batch (3 calls)
        print("\n[1] 3-BATCH TOP-K (3 calls)...")
        candidates_3, time_3 = await three_batch_topk(session, QUESTION, MEMORIES)
        print(f"    Time: {time_3:.0f}ms")
        print(f"    Selected: {candidates_3}")

        # Test 2: 4-batch (3 + 1 calls)
        print("\n[2] 4-BATCH TOP-K (3+1 calls)...")
        candidates_4, time_4 = await four_batch_topk(session, QUESTION, MEMORIES)
        print(f"    Time: {time_4:.0f}ms")
        print(f"    Selected: {candidates_4}")

        # Evaluate
        print(f"\n{'='*60}")
        print("EVALUATION")
        print(f"{'='*60}")

        recall_3 = len(set(candidates_3) & EXPECTED_BEST) / len(EXPECTED_BEST)
        recall_4 = len(set(candidates_4) & EXPECTED_BEST) / len(EXPECTED_BEST)

        print(f"3-batch recall: {recall_3*100:.0f}% of best memories found")
        print(f"4-batch recall: {recall_4*100:.0f}% of best memories found")

        print(f"\nLatency comparison:")
        print(f"  50 sequential calls: ~4000-5000ms")
        print(f"  3-batch TopK: {time_3:.0f}ms")
        print(f"  4-batch TopK: {time_4:.0f}ms")

        print(f"\nSpeedup vs sequential: ~{4500/time_4:.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
