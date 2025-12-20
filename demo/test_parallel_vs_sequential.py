"""
Diagnose why parallel scoring hurts temporal accuracy.

Hypothesis: Ollama overwhelmed by 50 concurrent requests -> timeouts -> wrong scores.
"""

import asyncio
import aiohttp
import time
import re
import json
from pathlib import Path

OLLAMA_BASE_URL = "http://localhost:11434"
RERANKER_MODEL = "qwen2.5:7b-instruct"

LOCOMO_PATH = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"


async def score_single(session, question, text, speaker, timeout=10):
    """Score a single memory. Returns (score, latency_ms, error)."""
    prompt = f"""Question: {question}
Memory from [{speaker}]: {text[:150]}

Score (0-3): 0=wrong person/unrelated, 2=related, 3=answers
Output only the number:"""

    t1 = time.perf_counter()
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": RERANKER_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 5}},
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            digits = re.findall(r'[0-3]', response)
            score = int(digits[0]) if digits else 1
            latency = (time.perf_counter() - t1) * 1000
            return score, latency, None
    except asyncio.TimeoutError:
        latency = (time.perf_counter() - t1) * 1000
        return 1, latency, "TIMEOUT"
    except Exception as e:
        latency = (time.perf_counter() - t1) * 1000
        return 1, latency, str(e)[:30]


async def sequential_score_all(session, question, memories):
    """Score all memories sequentially."""
    results = []
    for text, speaker in memories:
        score, latency, error = await score_single(session, question, text, speaker)
        results.append((score, latency, error))
    return results


async def parallel_score_all(session, question, memories):
    """Score all memories in parallel."""
    tasks = [
        score_single(session, question, text, speaker)
        for text, speaker in memories
    ]
    return await asyncio.gather(*tasks)


async def main():
    print("=" * 70)
    print("PARALLEL vs SEQUENTIAL SCORING DIAGNOSIS")
    print("=" * 70)

    # Load data
    with open(LOCOMO_PATH) as f:
        data = json.load(f)

    # Get temporal questions specifically
    temporal_questions = []
    for conv in data[:2]:
        conversation = conv.get("conversation", conv)
        messages = []
        session_idx = 1
        while f"session_{session_idx}" in conversation:
            for msg in conversation[f"session_{session_idx}"]:
                messages.append((msg.get("text", ""), msg.get("speaker", "Unknown")))
            session_idx += 1

        for qa in conv.get("qa", []):
            if qa.get("category") == 2:  # Temporal questions only
                question = qa.get("question", "")
                if question:
                    temporal_questions.append((question, messages[:50]))

    print(f"\nTesting on {len(temporal_questions)} TEMPORAL questions")
    print(f"Each question scores 50 memories")

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up Ollama...")
        await score_single(session, "test", "test", "test")

        for q_idx, (question, memories) in enumerate(temporal_questions[:5]):
            print(f"\n{'='*70}")
            print(f"Q{q_idx+1}: {question[:60]}...")
            print(f"{'='*70}")

            # Sequential scoring
            print("\n[SEQUENTIAL]")
            t1 = time.perf_counter()
            seq_results = await sequential_score_all(session, question, memories)
            seq_time = (time.perf_counter() - t1) * 1000

            seq_scores = [r[0] for r in seq_results]
            seq_errors = [r[2] for r in seq_results if r[2]]
            seq_latencies = [r[1] for r in seq_results]

            print(f"  Total time:    {seq_time:.0f}ms")
            print(f"  Avg latency:   {sum(seq_latencies)/len(seq_latencies):.0f}ms per call")
            print(f"  Errors:        {len(seq_errors)}")
            print(f"  Score dist:    0={seq_scores.count(0)}, 1={seq_scores.count(1)}, 2={seq_scores.count(2)}, 3={seq_scores.count(3)}")

            # Get top 15 indices from sequential
            seq_indexed = list(enumerate(seq_scores))
            seq_indexed.sort(key=lambda x: x[1], reverse=True)
            seq_top15 = set([idx for idx, _ in seq_indexed[:15]])

            # Wait a bit to let Ollama cool down
            await asyncio.sleep(1)

            # Parallel scoring
            print("\n[PARALLEL]")
            t2 = time.perf_counter()
            par_results = await parallel_score_all(session, question, memories)
            par_time = (time.perf_counter() - t2) * 1000

            par_scores = [r[0] for r in par_results]
            par_errors = [r[2] for r in par_results if r[2]]
            par_latencies = [r[1] for r in par_results]

            print(f"  Total time:    {par_time:.0f}ms")
            print(f"  Avg latency:   {sum(par_latencies)/len(par_latencies):.0f}ms per call")
            print(f"  Max latency:   {max(par_latencies):.0f}ms")
            print(f"  Errors:        {len(par_errors)}")
            if par_errors:
                print(f"  Error types:   {par_errors[:5]}")
            print(f"  Score dist:    0={par_scores.count(0)}, 1={par_scores.count(1)}, 2={par_scores.count(2)}, 3={par_scores.count(3)}")

            # Get top 15 indices from parallel
            par_indexed = list(enumerate(par_scores))
            par_indexed.sort(key=lambda x: x[1], reverse=True)
            par_top15 = set([idx for idx, _ in par_indexed[:15]])

            # Compare
            overlap = len(seq_top15 & par_top15)
            print(f"\n[COMPARISON]")
            print(f"  Speedup:       {seq_time/par_time:.1f}x")
            print(f"  Top-15 overlap: {overlap}/15 ({overlap/15*100:.0f}%)")

            # Score differences
            diff_count = sum(1 for s, p in zip(seq_scores, par_scores) if s != p)
            print(f"  Score diffs:   {diff_count}/50 memories have different scores")

            # Show where they differ
            if diff_count > 0:
                print(f"  Differences (idx: seq->par):")
                for i, (s, p) in enumerate(zip(seq_scores, par_scores)):
                    if s != p:
                        print(f"    Memory {i}: {s} -> {p}")
                        if len([1 for j, (s2, p2) in enumerate(zip(seq_scores, par_scores)) if s2 != p2 and j <= i]) >= 5:
                            print(f"    ... and {diff_count - 5} more")
                            break

        # Summary
        print(f"\n{'='*70}")
        print("DIAGNOSIS SUMMARY")
        print(f"{'='*70}")
        print("""
The issue is likely one of:
1. TIMEOUTS: Parallel requests overwhelm Ollama, causing timeouts
2. QUEUING: Ollama queues requests, some take too long
3. RESOURCE CONTENTION: GPU/CPU can't handle 50 simultaneous inferences

SOLUTIONS:
A) Use batched parallelism (e.g., 10 at a time instead of 50)
B) Increase timeout for parallel requests
C) Use sequential for small candidate sets
D) Add retry logic for failed requests
""")


if __name__ == "__main__":
    asyncio.run(main())
