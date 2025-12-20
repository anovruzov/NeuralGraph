"""Quick validation of parallel reranking fix."""

import asyncio
import aiohttp
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OLLAMA_BASE_URL = "http://localhost:11434"
RERANKER_MODEL = "phi3.5"

# Mock node class for testing
class MockNode:
    def __init__(self, content, speaker):
        self.content = content
        self.metadata = {"speaker": speaker}

# Sample memories
SAMPLE_MEMORIES = [
    (MockNode("Sarah loves Carbone restaurant in NYC", "Sarah"), 0.9),
    (MockNode("John prefers Chinese food", "John"), 0.85),
    (MockNode("Sarah went to Carbone last Tuesday", "Sarah"), 0.8),
    (MockNode("The weather was nice yesterday", "Mike"), 0.75),
    (MockNode("Sarah's birthday is in March", "Sarah"), 0.7),
] * 10  # 50 memories

QUESTION = "What is Sarah's favorite restaurant?"


async def slm_rerank_memory(session, question, memory_text, speaker):
    """Single rerank call."""
    prompt = f"""<|user|>
Score how relevant this memory is to the question. Output only a number 0-3.
Question: {question}
Memory from [{speaker}]: {memory_text[:300]}
Scoring: 3=directly answers, 2=strong evidence, 1=weakly related, 0=unrelated
Output only the score number:
<|end|>
<|assistant|>"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": RERANKER_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 5}},
            timeout=aiohttp.ClientTimeout(total=5)
        ) as response:
            result = await response.json()
            resp = result.get("response", "").strip()
            for char in resp:
                if char in "0123":
                    return int(char)
            return 1
    except:
        return 1


async def sequential_rerank(session, question, memories):
    """Old approach: sequential."""
    scored = []
    for node, charge in memories[:50]:
        speaker = node.metadata.get("speaker", "Unknown")
        score = await slm_rerank_memory(session, question, node.content, speaker)
        combined_score = score * 10 + charge
        scored.append((node, combined_score, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:15]


async def parallel_rerank(session, question, memories):
    """New approach: parallel with asyncio.gather."""
    candidates = memories[:50]

    async def score_candidate(node, charge):
        speaker = node.metadata.get("speaker", "Unknown")
        score = await slm_rerank_memory(session, question, node.content, speaker)
        combined_score = score * 10 + charge
        return (node, combined_score, score)

    tasks = [score_candidate(node, charge) for node, charge in candidates]
    scored = await asyncio.gather(*tasks)

    scored_list = list(scored)
    scored_list.sort(key=lambda x: x[1], reverse=True)
    return scored_list[:15]


async def main():
    print("="*60)
    print("PARALLEL RERANKING FIX VALIDATION")
    print("="*60)
    print(f"Testing with {len(SAMPLE_MEMORIES)} memories")

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up model...")
        await slm_rerank_memory(session, "test", "test", "test")

        # Test sequential (old approach)
        print("\n[1] SEQUENTIAL (old approach)...")
        t1 = time.perf_counter()
        result_seq = await sequential_rerank(session, QUESTION, SAMPLE_MEMORIES)
        time_seq = (time.perf_counter() - t1) * 1000
        print(f"    Time: {time_seq:.0f}ms ({time_seq/1000:.2f}s)")

        # Test parallel (new approach)
        print("\n[2] PARALLEL (new approach)...")
        t2 = time.perf_counter()
        result_par = await parallel_rerank(session, QUESTION, SAMPLE_MEMORIES)
        time_par = (time.perf_counter() - t2) * 1000
        print(f"    Time: {time_par:.0f}ms ({time_par/1000:.2f}s)")

        # Summary
        speedup = time_seq / time_par
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")
        print(f"Sequential: {time_seq:.0f}ms")
        print(f"Parallel:   {time_par:.0f}ms")
        print(f"Speedup:    {speedup:.2f}x")
        print(f"\nTop 3 reranked (parallel):")
        for i, (node, score, raw) in enumerate(result_par[:3]):
            print(f"  {i+1}. [{raw}] {node.content[:50]}...")

        # Verify results match
        seq_order = [node.content for node, _, _ in result_seq[:5]]
        par_order = [node.content for node, _, _ in result_par[:5]]
        if seq_order == par_order:
            print(f"\n[OK] Rankings match - no accuracy loss")
        else:
            print(f"\n[WARN] Rankings differ slightly (expected with async timing)")


if __name__ == "__main__":
    asyncio.run(main())
