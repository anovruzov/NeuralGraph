"""
Test parallel reranking throughput on 16GB RAM machine.

Tests:
1. Sequential baseline (current approach)
2. Parallel with asyncio.gather() - relies on OLLAMA_NUM_PARALLEL
3. Different parallelism levels (4, 8, 16, 32)

Architecture considerations for 16GB RAM:
- Phi 3.5 (3.8B) model weights: ~2-4GB
- KV cache per request (short prompt): ~100-200MB
- With 16GB RAM, theoretical max parallel: 8-16 concurrent requests
- Sweet spot likely: 8-12 parallel requests
"""

import asyncio
import aiohttp
import time
import statistics

OLLAMA_BASE_URL = "http://localhost:11434"
RERANKER_MODEL = "phi3.5"  # Same as runner.py

# Sample test data - simulating 50 memories to rerank
SAMPLE_QUESTION = "What is Sarah's favorite restaurant in New York?"
SAMPLE_MEMORIES = [
    ("Sarah mentioned she loves the Italian place on 5th Avenue", "Sarah"),
    ("John said he prefers Chinese food", "John"),
    ("Sarah went to Carbone last Tuesday and said it was amazing", "Sarah"),
    ("The weather in New York was cold yesterday", "Mike"),
    ("Sarah's birthday is in March", "Sarah"),
    ("I recommended a great sushi place to Sarah", "John"),
    ("Sarah said Carbone is her absolute favorite restaurant", "Sarah"),
    ("New York has many great restaurants", "Guide"),
    ("Sarah doesn't like fast food", "Sarah"),
    ("The best pizza in NYC is at Joe's", "Mike"),
] * 5  # Repeat to get 50 memories


async def rerank_single(session: aiohttp.ClientSession, question: str, memory: str, speaker: str) -> tuple[int, float]:
    """Single rerank call - returns (score, latency_ms)."""
    prompt = f"""<|user|>
Score how relevant this memory is to the question. Output only a number 0-3.

Question: {question}
Memory from [{speaker}]: {memory[:300]}

Scoring guide:
3 = directly answers the question
2 = strong supporting evidence
1 = weakly related
0 = unrelated or wrong person

Output only the score number (0, 1, 2, or 3):
<|end|>
<|assistant|>"""

    start = time.perf_counter()
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": RERANKER_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 5}
            },
            timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            result = await response.json()
            resp = result.get("response", "").strip()
            elapsed = (time.perf_counter() - start) * 1000

            for char in resp:
                if char in "0123":
                    return int(char), elapsed
            return 1, elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return 1, elapsed


async def test_sequential(session: aiohttp.ClientSession, memories: list) -> dict:
    """Test sequential reranking (current approach)."""
    print(f"\n{'='*60}")
    print("TEST 1: SEQUENTIAL (current baseline)")
    print(f"{'='*60}")

    start = time.perf_counter()
    latencies = []

    for i, (memory, speaker) in enumerate(memories):
        score, lat = await rerank_single(session, SAMPLE_QUESTION, memory, speaker)
        latencies.append(lat)
        if (i + 1) % 10 == 0:
            print(f"  Processed {i+1}/{len(memories)} - last call: {lat:.1f}ms")

    total = (time.perf_counter() - start) * 1000

    return {
        "method": "sequential",
        "total_ms": total,
        "per_call_mean": statistics.mean(latencies),
        "per_call_p50": statistics.median(latencies),
        "per_call_min": min(latencies),
        "per_call_max": max(latencies),
    }


async def test_parallel(session: aiohttp.ClientSession, memories: list, parallelism: int) -> dict:
    """Test parallel reranking with specified concurrency."""
    print(f"\n{'='*60}")
    print(f"TEST: PARALLEL (concurrency={parallelism})")
    print(f"{'='*60}")

    start = time.perf_counter()

    # Process in batches of `parallelism` size
    all_latencies = []

    for batch_start in range(0, len(memories), parallelism):
        batch = memories[batch_start:batch_start + parallelism]
        batch_start_time = time.perf_counter()

        # Launch all requests in this batch concurrently
        tasks = [
            rerank_single(session, SAMPLE_QUESTION, memory, speaker)
            for memory, speaker in batch
        ]
        results = await asyncio.gather(*tasks)

        batch_time = (time.perf_counter() - batch_start_time) * 1000
        latencies = [r[1] for r in results]
        all_latencies.extend(latencies)

        print(f"  Batch {batch_start//parallelism + 1}: {len(batch)} calls in {batch_time:.1f}ms "
              f"(effective: {batch_time/len(batch):.1f}ms/call)")

    total = (time.perf_counter() - start) * 1000

    return {
        "method": f"parallel_{parallelism}",
        "total_ms": total,
        "per_call_mean": statistics.mean(all_latencies),
        "per_call_p50": statistics.median(all_latencies),
        "effective_per_call": total / len(memories),
        "speedup_vs_sequential": None,  # Filled in later
    }


async def check_ollama_status():
    """Check Ollama is running and get model info."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                models = [m["name"] for m in data.get("models", [])]
                print(f"Ollama running. Available models: {models}")
                if RERANKER_MODEL not in str(models):
                    print(f"WARNING: {RERANKER_MODEL} may not be loaded. Run: ollama pull {RERANKER_MODEL}")
                return True
        except Exception as e:
            print(f"ERROR: Ollama not accessible at {OLLAMA_BASE_URL}: {e}")
            return False


async def main():
    print("="*60)
    print("PARALLEL RERANKING THROUGHPUT TEST")
    print("="*60)
    print(f"Model: {RERANKER_MODEL}")
    print(f"Candidates: {len(SAMPLE_MEMORIES)}")
    print(f"Target: Find optimal parallelism for 16GB RAM")

    if not await check_ollama_status():
        return

    # Warm up the model
    print("\nWarming up model...")
    async with aiohttp.ClientSession() as session:
        await rerank_single(session, "test", "test memory", "test")
        print("Model warm.")

    results = []

    async with aiohttp.ClientSession() as session:
        # Test 1: Sequential baseline
        seq_result = await test_sequential(session, SAMPLE_MEMORIES)
        results.append(seq_result)
        print(f"\n  TOTAL: {seq_result['total_ms']:.1f}ms ({seq_result['total_ms']/1000:.2f}s)")

        # Test 2-5: Different parallelism levels
        for parallelism in [4, 8, 16, 25, 50]:
            par_result = await test_parallel(session, SAMPLE_MEMORIES, parallelism)
            par_result["speedup_vs_sequential"] = seq_result["total_ms"] / par_result["total_ms"]
            results.append(par_result)
            print(f"\n  TOTAL: {par_result['total_ms']:.1f}ms ({par_result['total_ms']/1000:.2f}s)")
            print(f"  SPEEDUP: {par_result['speedup_vs_sequential']:.2f}x vs sequential")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"\n{'Method':<20} {'Total (ms)':<12} {'Effective/call':<15} {'Speedup':<10}")
    print("-"*60)

    for r in results:
        speedup = r.get("speedup_vs_sequential", 1.0) or 1.0
        effective = r.get("effective_per_call", r["per_call_mean"])
        print(f"{r['method']:<20} {r['total_ms']:<12.1f} {effective:<15.1f} {speedup:<10.2f}x")

    # Recommendation
    best = max(results[1:], key=lambda x: x.get("speedup_vs_sequential", 0))
    print(f"\n{'='*60}")
    print(f"RECOMMENDATION FOR 16GB RAM:")
    print(f"  Best parallelism: {best['method']}")
    print(f"  Expected latency: {best['total_ms']:.0f}ms ({best['total_ms']/1000:.2f}s)")
    print(f"  Speedup: {best['speedup_vs_sequential']:.2f}x")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
