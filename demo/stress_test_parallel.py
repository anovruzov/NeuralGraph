"""
Stress test: Fire all 50 requests in ONE asyncio.gather call.
Measures true parallel throughput.
"""

import asyncio
import aiohttp
import time

OLLAMA_BASE_URL = "http://localhost:11434"
RERANKER_MODEL = "phi3.5"

async def single_call(session, idx):
    """Minimal rerank call."""
    prompt = f"""<|user|>
Score 0-3: Question: What is Sarah's favorite food?
Memory: Sarah loves pasta at restaurant {idx}.
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
    except Exception as e:
        return (time.perf_counter() - start) * 1000


async def main():
    print("="*60)
    print("STRESS TEST: 50 SIMULTANEOUS REQUESTS")
    print("="*60)

    connector = aiohttp.TCPConnector(limit=100, limit_per_host=100)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Warmup
        print("Warming up...")
        await single_call(session, 0)
        await asyncio.sleep(0.5)

        # Test 1: Sequential
        print("\n[SEQUENTIAL] 50 calls one-by-one...")
        t1 = time.perf_counter()
        for i in range(50):
            await single_call(session, i)
        seq_time = (time.perf_counter() - t1) * 1000
        print(f"  Result: {seq_time:.0f}ms")

        await asyncio.sleep(1)

        # Test 2: All at once
        print("\n[PARALLEL] 50 calls via asyncio.gather...")
        t2 = time.perf_counter()
        tasks = [single_call(session, i) for i in range(50)]
        latencies = await asyncio.gather(*tasks)
        par_time = (time.perf_counter() - t2) * 1000
        print(f"  Result: {par_time:.0f}ms")
        print(f"  First complete: {min(latencies):.0f}ms")
        print(f"  Last complete: {max(latencies):.0f}ms")

        # Summary
        speedup = seq_time / par_time
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Sequential: {seq_time:.0f}ms ({seq_time/1000:.2f}s)")
        print(f"Parallel:   {par_time:.0f}ms ({par_time/1000:.2f}s)")
        print(f"Speedup:    {speedup:.2f}x")
        print(f"\nFor 5700ms baseline -> expected: {5700/speedup:.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
