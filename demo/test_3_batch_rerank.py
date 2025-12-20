"""
3-Batch Reranking: 50 candidates in 3 LLM calls (~17 each).

Goal: 95% accuracy with 3 calls instead of 50.
"""

import asyncio
import aiohttp
import time
import json
import re

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "qwen2.5:7b-instruct"

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
] + [
    ("John doesn't like Italian food much", "John"),
    ("Sarah mentioned Carbone has the best ambiance", "Sarah"),
    ("The rent in NYC is very expensive", "Guide"),
] * 3 + [
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

# Pad to 51 for even batches
while len(MEMORIES) < 51:
    MEMORIES.append(("Random filler memory about nothing", "Unknown"))


async def batch_score(session, question: str, memories: list[tuple], batch_start: int) -> list[int]:
    """Score a batch of memories in one LLM call. Returns list of scores."""

    # Shorter format for reliable parsing
    memory_list = "\n".join([
        f"{i}.[{speaker}]{text[:80]}"
        for i, (text, speaker) in enumerate(memories)
    ])

    prompt = f"""Q: {question}

{memory_list}

Score each 0-3 (0=wrong person, 3=answers). Output comma-separated scores:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 150}},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()

            # Parse comma-separated or JSON array
            # First try JSON
            try:
                start = response.find("[")
                end = response.rfind("]") + 1
                if start >= 0 and end > start:
                    scores = json.loads(response[start:end])
                    return [max(0, min(3, int(s))) for s in scores[:len(memories)]]
            except:
                pass

            # Try comma-separated
            try:
                parts = re.split(r'[,\s]+', response)
                scores = [int(p) for p in parts if p.isdigit() and int(p) <= 3]
                if len(scores) >= len(memories) * 0.8:  # At least 80% parsed
                    return scores[:len(memories)]
            except:
                pass

            # Fallback: extract all digits in sequence
            digits = re.findall(r'[0-3]', response)
            scores = [int(d) for d in digits]

            # Pad with 1s if too few
            while len(scores) < len(memories):
                scores.append(1)

            return scores[:len(memories)]

    except Exception as e:
        print(f"Batch error: {e}")
        return [1] * len(memories)


async def sequential_score(session, question: str, memories: list[tuple]) -> tuple[list[int], float]:
    """Score all memories sequentially (baseline)."""
    scores = []
    start = time.perf_counter()

    for text, speaker in memories:
        prompt = f"""Question: {question}
Memory from [{speaker}]: {text[:120]}

Score (0=wrong person/unrelated, 2=related, 3=answers):"""

        try:
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0, "num_predict": 5}},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                result = await resp.json()
                response = result.get("response", "").strip()
                digits = re.findall(r'[0-3]', response)
                scores.append(int(digits[0]) if digits else 1)
        except:
            scores.append(1)

    elapsed = (time.perf_counter() - start) * 1000
    return scores, elapsed


async def three_batch_score(session, question: str, memories: list[tuple], num_batches: int = 5) -> tuple[list[int], float]:
    """Score all memories in N batches."""
    start = time.perf_counter()

    # Split into batches
    batch_size = len(memories) // num_batches + 1
    batches = []
    for i in range(num_batches):
        batch_start = i * batch_size
        batch_end = min((i + 1) * batch_size, len(memories))
        if batch_start < len(memories):
            batches.append(memories[batch_start:batch_end])

    all_scores = []
    offset = 0
    for i, batch in enumerate(batches):
        if batch:
            batch_scores = await batch_score(session, question, batch, offset)
            all_scores.extend(batch_scores)
            print(f"  Batch {i+1}: {len(batch)} memories -> {len(batch_scores)} scores")
            offset += len(batch)

    elapsed = (time.perf_counter() - start) * 1000
    return all_scores, elapsed


async def main():
    print("="*60)
    print("3-BATCH RERANKING TEST")
    print("="*60)
    print(f"Question: {QUESTION}")
    print(f"Total memories: {len(MEMORIES)}")
    print(f"Batches: 3 x ~{len(MEMORIES)//3} memories")

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up Qwen 7B...")
        await batch_score(session, "test", [("test", "test")], 0)

        # Test 1: Sequential (baseline)
        print("\n[1] SEQUENTIAL (50 calls)...")
        seq_scores, seq_time = await sequential_score(session, QUESTION, MEMORIES[:50])
        print(f"    Time: {seq_time:.0f}ms ({seq_time/1000:.2f}s)")
        print(f"    Scores sample: {seq_scores[:10]}...")

        # Test 2: 5-batch (more reliable)
        print("\n[2] 5-BATCH (5 calls of ~10 each)...")
        batch_scores, batch_time = await three_batch_score(session, QUESTION, MEMORIES[:50], num_batches=5)
        print(f"    Time: {batch_time:.0f}ms ({batch_time/1000:.2f}s)")
        print(f"    Scores sample: {batch_scores[:10]}...")

        # Compare results
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")
        print(f"Sequential: {seq_time:.0f}ms ({len(MEMORIES[:50])} LLM calls)")
        print(f"3-Batch:    {batch_time:.0f}ms (3 LLM calls)")

        speedup = seq_time / batch_time if batch_time > 0 else 0
        print(f"Speedup:    {speedup:.1f}x")

        # Check agreement
        if len(seq_scores) == len(batch_scores):
            matches = sum(1 for a, b in zip(seq_scores, batch_scores) if a == b)
            close = sum(1 for a, b in zip(seq_scores, batch_scores) if abs(a - b) <= 1)
            print(f"\nAgreement with sequential:")
            print(f"  Exact: {matches}/{len(seq_scores)} ({100*matches/len(seq_scores):.1f}%)")
            print(f"  Close: {close}/{len(seq_scores)} ({100*close/len(seq_scores):.1f}%)")

        # Top-K comparison
        print(f"\n{'='*60}")
        print("TOP-10 COMPARISON")
        print(f"{'='*60}")

        seq_ranked = sorted(enumerate(seq_scores), key=lambda x: x[1], reverse=True)
        batch_ranked = sorted(enumerate(batch_scores), key=lambda x: x[1], reverse=True)

        seq_top10 = set(idx for idx, _ in seq_ranked[:10])
        batch_top10 = set(idx for idx, _ in batch_ranked[:10])

        overlap = len(seq_top10 & batch_top10)
        print(f"Top-10 overlap: {overlap}/10")

        print(f"\nTop 5 (3-batch):")
        for idx, score in batch_ranked[:5]:
            text, speaker = MEMORIES[idx]
            print(f"  [{score}] [{speaker}]: {text[:50]}...")


if __name__ == "__main__":
    asyncio.run(main())
