"""
Batch reranking: Single LLM call scores ALL candidates at once.

Advantages:
1. One LLM call instead of 50 = ~10x faster
2. Model sees all candidates together = comparative reasoning
3. Can say "Memory 3 is more relevant than Memory 7"
"""

import asyncio
import aiohttp
import time
import json

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "phi3.5"

QUESTION = "What is Sarah's favorite restaurant in New York?"

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
] * 3 + [  # 45 + 5 more = 50 memories
    ("Sarah had brunch at Balthazar last Sunday", "Sarah"),
    ("Mike recommended a burger place to John", "Mike"),
    ("Sarah's mom loves French cuisine", "Sarah"),
    ("The restaurant was crowded on Friday night", "John"),
    ("Sarah made reservations at her favorite spot", "Sarah"),
]


async def batch_rerank(session, question: str, memories: list[tuple]) -> list[tuple[int, int]]:
    """
    Single LLM call to score all memories at once.
    Returns list of (memory_index, score) tuples.
    """
    # Build the prompt with all memories
    memory_list = "\n".join([
        f"[{i}] [{speaker}]: {text[:200]}"
        for i, (text, speaker) in enumerate(memories)
    ])

    prompt = f"""<|user|>
Score each memory's relevance to the question (0-3).

Question: {question}

{memory_list}

Output ONLY a JSON array of scores, one per memory: [score0, score1, ...]
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
                "options": {"temperature": 0, "num_predict": 200}
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            result = await resp.json()
            elapsed = (time.perf_counter() - start) * 1000
            response = result.get("response", "").strip()

            # Parse JSON array (we prefixed with "[" in prompt)
            try:
                full_response = "[" + response
                end_idx = full_response.rfind("]") + 1
                if end_idx > 1:
                    scores = json.loads(full_response[:end_idx])
                    return scores, elapsed, response
            except:
                # Fallback: extract all digits
                import re
                digits = re.findall(r'[0-3]', response)
                scores = [int(d) for d in digits[:len(memories)]]
                if scores:
                    return scores, elapsed, response

            return [], elapsed, response

    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return [], elapsed, str(e)


async def sequential_rerank(session, question: str, memories: list[tuple]) -> tuple[list, float]:
    """Original sequential approach for comparison."""
    scores = []
    start = time.perf_counter()

    for text, speaker in memories:
        prompt = f"""<|user|>
Score relevance 0-3. Output only the number.
Question: {question}
Memory [{speaker}]: {text[:200]}
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
                for char in response:
                    if char in "0123":
                        scores.append(int(char))
                        break
                else:
                    scores.append(1)
        except:
            scores.append(1)

    elapsed = (time.perf_counter() - start) * 1000
    return scores, elapsed


async def main():
    print("="*60)
    print("BATCH RERANKING TEST")
    print("="*60)
    print(f"Question: {QUESTION}")
    print(f"Memories: {len(MEMORIES)}")

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up model...")
        await batch_rerank(session, "test", [("test", "test")])

        # Test 1: Sequential (baseline)
        print("\n[1] SEQUENTIAL RERANKING...")
        seq_scores, seq_time = await sequential_rerank(session, QUESTION, MEMORIES)
        print(f"    Time: {seq_time:.0f}ms")
        print(f"    Scores: {seq_scores}")

        # Test 2: Batch (single call)
        print("\n[2] BATCH RERANKING (single LLM call)...")
        batch_scores, batch_time, raw_response = await batch_rerank(session, QUESTION, MEMORIES)
        print(f"    Time: {batch_time:.0f}ms")
        print(f"    Scores: {batch_scores}")
        print(f"    Raw: {raw_response[:200]}")

        # Summary
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")
        print(f"Sequential: {seq_time:.0f}ms ({len(MEMORIES)} LLM calls)")
        print(f"Batch:      {batch_time:.0f}ms (1 LLM call)")

        if batch_scores:
            speedup = seq_time / batch_time
            print(f"Speedup:    {speedup:.1f}x")

            # Show top ranked memories
            if len(batch_scores) == len(MEMORIES):
                ranked = sorted(enumerate(batch_scores), key=lambda x: x[1], reverse=True)
                print(f"\nTop 5 memories (batch ranking):")
                for idx, score in ranked[:5]:
                    text, speaker = MEMORIES[idx]
                    print(f"  [{score}] [{speaker}]: {text[:60]}...")

        # Extrapolate to 50 memories
        print(f"\n{'='*60}")
        print("EXTRAPOLATION TO 50 MEMORIES")
        print(f"{'='*60}")
        seq_50 = (seq_time / len(MEMORIES)) * 50
        batch_50 = batch_time * 1.5  # Slightly longer prompt
        print(f"Sequential (50 memories): ~{seq_50:.0f}ms")
        print(f"Batch (50 memories):      ~{batch_50:.0f}ms")
        print(f"Expected speedup:         ~{seq_50/batch_50:.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
