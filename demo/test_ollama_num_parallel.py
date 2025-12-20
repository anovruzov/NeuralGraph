"""
Test true parallel inference by checking OLLAMA_NUM_PARALLEL effect.

OLLAMA_NUM_PARALLEL controls how many requests Ollama processes concurrently.
Default is 1 (sequential). Higher values enable true batching.

On 16GB RAM with Phi 3.5 (3.8B):
- Model weights: ~4GB
- KV cache per parallel slot: ~200MB for short prompts
- Theoretical max: (16GB - 4GB) / 0.2GB = 60 parallel slots
- Safe recommendation: 8-16 parallel slots
"""

import asyncio
import aiohttp
import time
import os
import subprocess
import sys

OLLAMA_BASE_URL = "http://localhost:11434"
RERANKER_MODEL = "phi3.5"

SAMPLE_QUESTION = "What is Sarah's favorite restaurant?"
SAMPLE_MEMORIES = [
    ("Sarah loves Carbone in NYC", "Sarah"),
    ("John prefers pizza", "John"),
    ("Sarah said Carbone is amazing", "Sarah"),
] * 17  # 51 memories


async def rerank_single(session, memory, speaker):
    prompt = f"""<|user|>
Score relevance 0-3. Output only the number.
Question: {SAMPLE_QUESTION}
Memory [{speaker}]: {memory}
<|end|>
<|assistant|>"""

    start = time.perf_counter()
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": RERANKER_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 3}},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            await resp.json()
            return (time.perf_counter() - start) * 1000
    except:
        return (time.perf_counter() - start) * 1000


async def test_all_parallel(n_requests=50):
    """Fire all requests at once, measure total time."""
    async with aiohttp.ClientSession() as session:
        # Warmup
        await rerank_single(session, "test", "test")

        start = time.perf_counter()
        tasks = [rerank_single(session, m, s) for m, s in SAMPLE_MEMORIES[:n_requests]]
        latencies = await asyncio.gather(*tasks)
        total = (time.perf_counter() - start) * 1000

        return total, latencies


async def main():
    print("="*60)
    print("OLLAMA_NUM_PARALLEL TEST")
    print("="*60)

    # Check current OLLAMA_NUM_PARALLEL
    num_parallel = os.environ.get("OLLAMA_NUM_PARALLEL", "not set (default=1)")
    print(f"Current OLLAMA_NUM_PARALLEL: {num_parallel}")
    print(f"Testing with 50 concurrent requests...")

    total, latencies = await test_all_parallel(50)

    # Analyze timing pattern
    sorted_lat = sorted(latencies)

    print(f"\nResults:")
    print(f"  Total time: {total:.0f}ms ({total/1000:.2f}s)")
    print(f"  Effective per-call: {total/50:.1f}ms")
    print(f"  Individual call times:")
    print(f"    Min: {min(latencies):.0f}ms")
    print(f"    Max: {max(latencies):.0f}ms")
    print(f"    Spread: {max(latencies) - min(latencies):.0f}ms")

    # Estimate actual parallelism from timing pattern
    # If truly parallel, all calls complete around the same time
    # If sequential, calls complete in staggered fashion
    avg_call = sum(latencies) / len(latencies)
    if total < avg_call * 2:
        estimated_parallel = len(latencies)
    else:
        estimated_parallel = max(1, int(sum(latencies) / total))

    print(f"\n  Estimated actual parallelism: ~{estimated_parallel} concurrent requests")

    # Recommendations
    print(f"\n{'='*60}")
    print("RECOMMENDATIONS")
    print(f"{'='*60}")

    if estimated_parallel < 4:
        print("""
Current Ollama is processing mostly sequentially.

To enable true parallel inference, restart Ollama with:

  Windows (PowerShell):
    $env:OLLAMA_NUM_PARALLEL=8; ollama serve

  Or set system environment variable:
    setx OLLAMA_NUM_PARALLEL 8

Then restart Ollama and re-run this test.

With OLLAMA_NUM_PARALLEL=8:
  - Expected speedup: ~4-6x
  - 50 calls in ~400-600ms instead of ~2000ms
  - Memory usage: +1.5GB for KV caches
""")
    else:
        theoretical_sequential = avg_call * 50
        actual_speedup = theoretical_sequential / total
        print(f"""
Ollama is processing with parallelism ~{estimated_parallel}.

Current speedup: {actual_speedup:.1f}x vs pure sequential

To increase further, try:
  $env:OLLAMA_NUM_PARALLEL=16; ollama serve
""")


if __name__ == "__main__":
    asyncio.run(main())
