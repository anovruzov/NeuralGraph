"""
Check Ollama's actual parallelism and recommend optimal settings for 16GB RAM.

Memory math for Phi 3.5 on 16GB RAM:
- Model weights: ~4GB
- Available for KV cache: 16GB - 4GB = 12GB
- KV cache per parallel slot (short prompt): ~150-200MB
- Theoretical max parallel: 12GB / 0.2GB = 60 slots
- Safe recommendation: 16-32 slots (leaves headroom)
"""

import asyncio
import aiohttp
import time
import os

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "phi3.5"


async def timed_call(session, idx):
    """Single inference call with timing."""
    start = time.perf_counter()
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": MODEL, "prompt": f"Say {idx}", "stream": False,
                  "options": {"num_predict": 3}},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            await resp.json()
            return time.perf_counter() - start
    except:
        return time.perf_counter() - start


async def measure_parallelism():
    """Measure actual parallelism by firing N requests and analyzing completion pattern."""
    print("="*60)
    print("OLLAMA PARALLELISM ANALYSIS")
    print("="*60)

    # Check env var
    num_parallel_env = os.environ.get("OLLAMA_NUM_PARALLEL", "not set")
    print(f"\nOLLAMA_NUM_PARALLEL env: {num_parallel_env}")

    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Warmup
        await timed_call(session, 0)

        # Fire 50 requests simultaneously
        print("\nFiring 50 simultaneous requests...")
        t_start = time.perf_counter()
        tasks = [timed_call(session, i) for i in range(50)]
        durations = await asyncio.gather(*tasks)
        total_time = time.perf_counter() - t_start

        # Analyze completion pattern
        sorted_dur = sorted(durations)
        avg_single = sum(durations) / len(durations)

        # Estimate actual parallelism
        # If fully parallel: total_time ≈ max(durations)
        # If sequential: total_time ≈ sum(durations)
        sequential_expected = sum(durations)
        parallel_ratio = sequential_expected / total_time

        print(f"\nResults:")
        print(f"  Total wall time: {total_time*1000:.0f}ms")
        print(f"  Sum of all calls: {sequential_expected*1000:.0f}ms")
        print(f"  Avg per call: {avg_single*1000:.0f}ms")
        print(f"  First complete: {sorted_dur[0]*1000:.0f}ms")
        print(f"  Last complete: {sorted_dur[-1]*1000:.0f}ms")
        print(f"  Effective parallelism: ~{parallel_ratio:.1f}x")

        # Estimate OLLAMA_NUM_PARALLEL
        # If num_parallel=N, then 50 requests take ceil(50/N) rounds
        # Each round takes ~avg_single time
        estimated_parallel = int(round(parallel_ratio))

        print(f"\n{'='*60}")
        print("DIAGNOSIS")
        print(f"{'='*60}")
        print(f"  Estimated OLLAMA_NUM_PARALLEL: ~{estimated_parallel}")

        if estimated_parallel < 8:
            print(f"""
  STATUS: SUBOPTIMAL for 16GB RAM

  Current parallelism ({estimated_parallel}) is too low.
  You have memory headroom for much higher parallelism.

  RECOMMENDED FIX:
  ================
  1. Stop Ollama
  2. Set environment variable:

     Windows (PowerShell, run as admin):
       [System.Environment]::SetEnvironmentVariable('OLLAMA_NUM_PARALLEL', '16', 'User')

     Or for current session only:
       $env:OLLAMA_NUM_PARALLEL=16

  3. Restart Ollama:
       ollama serve

  4. Re-run this test

  EXPECTED IMPROVEMENT:
  - Current: {total_time*1000:.0f}ms for 50 reranks
  - With NUM_PARALLEL=16: ~{total_time*1000/2:.0f}ms (2x faster)
  - With NUM_PARALLEL=32: ~{total_time*1000/3:.0f}ms (3x faster)
""")
        else:
            print(f"""
  STATUS: GOOD parallelism (~{estimated_parallel}x)

  For 16GB RAM with Phi 3.5, optimal range is 16-32.

  Current: {total_time*1000:.0f}ms for 50 reranks

  To push further (if stable):
    $env:OLLAMA_NUM_PARALLEL=32; ollama serve
""")

        # Memory estimate
        print(f"\n{'='*60}")
        print("MEMORY ANALYSIS (16GB RAM)")
        print(f"{'='*60}")
        model_size = 4.0  # GB for Phi 3.5
        kv_per_slot = 0.15  # GB per parallel slot (short prompts)
        available = 16 - model_size
        max_slots = int(available / kv_per_slot)
        safe_slots = min(32, max_slots)

        print(f"  Model weights: ~{model_size}GB")
        print(f"  Available RAM: ~{available}GB")
        print(f"  KV cache/slot: ~{kv_per_slot*1000:.0f}MB")
        print(f"  Max theoretical slots: {max_slots}")
        print(f"  Safe recommendation: {safe_slots} parallel slots")


if __name__ == "__main__":
    asyncio.run(measure_parallelism())
