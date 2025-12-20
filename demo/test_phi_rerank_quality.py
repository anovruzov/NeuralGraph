"""
Test Phi 3.5 SLM reranking quality.

Questions:
1. Can Phi 3.5 correctly identify relevant memories?
2. Does it avoid entity confusion (Sarah vs John)?
3. How does it compare to no reranking (just embedding similarity)?
"""

import asyncio
import aiohttp
import time

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "phi3.5"

# Test cases with known correct answers
TEST_CASES = [
    {
        "question": "What is Sarah's favorite restaurant?",
        "memories": [
            ("John loves pizza at Joe's", "John", 0),  # Wrong person
            ("Sarah went to Carbone and loved it", "Sarah", 3),  # Direct answer
            ("Sarah's birthday is in March", "Sarah", 0),  # Irrelevant
            ("Sarah said Carbone is her favorite", "Sarah", 3),  # Direct answer
            ("The weather was nice yesterday", "Mike", 0),  # Irrelevant
        ],
        "expected_top": [1, 3],  # Indices of best memories
    },
    {
        "question": "When did John go to the gym?",
        "memories": [
            ("Sarah goes to yoga every Tuesday", "Sarah", 0),  # Wrong person
            ("John mentioned he hit the gym last Monday", "John", 3),  # Direct
            ("John likes to stay fit", "John", 1),  # Weak
            ("Mike runs every morning", "Mike", 0),  # Wrong person
            ("John went to the gym on December 5th", "John", 3),  # Direct
        ],
        "expected_top": [1, 4],
    },
    {
        "question": "What does Sarah do for work?",
        "memories": [
            ("Sarah is a software engineer at Google", "Sarah", 3),  # Direct
            ("John works as a doctor", "John", 0),  # Wrong person
            ("Sarah loves her job in tech", "Sarah", 2),  # Supporting
            ("Sarah graduated from MIT", "Sarah", 1),  # Weak
            ("Mike is a teacher", "Mike", 0),  # Wrong person
        ],
        "expected_top": [0, 2],
    },
    {
        "question": "Where did Caroline travel last summer?",
        "memories": [
            ("Caroline went to Paris last July", "Caroline", 3),  # Direct
            ("Sarah visited Tokyo in August", "Sarah", 0),  # Wrong person
            ("Caroline loved the Eiffel Tower", "Caroline", 2),  # Supporting
            ("John stayed home all summer", "John", 0),  # Wrong person
            ("Caroline's trip to France was amazing", "Caroline", 3),  # Direct
        ],
        "expected_top": [0, 4, 2],
    },
]


async def score_memory(session, question: str, memory: str, speaker: str) -> int:
    """Score a single memory using Phi 3.5."""
    prompt = f"""<|user|>
Score how relevant this memory is to the question. Output only a number 0-3.

Question: {question}
Memory from [{speaker}]: {memory}

Scoring guide:
3 = directly answers the question
2 = strong supporting evidence
1 = weakly related
0 = unrelated or wrong person

Output only the score number (0, 1, 2, or 3):
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
                    return int(char)
            return 1
    except:
        return 1


async def test_case(session, case: dict) -> dict:
    """Test a single case and return metrics."""
    question = case["question"]
    memories = case["memories"]
    expected_top = case["expected_top"]

    # Score each memory
    scores = []
    for i, (text, speaker, expected_score) in enumerate(memories):
        actual = await score_memory(session, question, text, speaker)
        scores.append({
            "idx": i,
            "text": text[:50],
            "speaker": speaker,
            "expected": expected_score,
            "actual": actual,
            "correct": actual == expected_score,
            "close": abs(actual - expected_score) <= 1,
        })

    # Check if top memories are correctly identified
    ranked = sorted(scores, key=lambda x: x["actual"], reverse=True)
    top_indices = [s["idx"] for s in ranked[:2]]

    top_correct = len(set(top_indices) & set(expected_top[:2]))

    return {
        "question": question,
        "scores": scores,
        "top_indices": top_indices,
        "expected_top": expected_top[:2],
        "top_correct": top_correct,
        "exact_matches": sum(1 for s in scores if s["correct"]),
        "close_matches": sum(1 for s in scores if s["close"]),
    }


async def main():
    print("="*70)
    print("PHI 3.5 RERANKING QUALITY TEST")
    print("="*70)

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up model...")
        await score_memory(session, "test", "test", "test")

        results = []
        total_exact = 0
        total_close = 0
        total_memories = 0
        total_top_correct = 0

        for i, case in enumerate(TEST_CASES):
            print(f"\n{'='*70}")
            print(f"TEST {i+1}: {case['question']}")
            print(f"{'='*70}")

            result = await test_case(session, case)
            results.append(result)

            print(f"\n{'Idx':<4} {'Speaker':<10} {'Expected':<10} {'Actual':<8} {'Match':<8}")
            print("-"*50)
            for s in result["scores"]:
                match = "OK" if s["correct"] else ("~" if s["close"] else "X")
                print(f"{s['idx']:<4} {s['speaker']:<10} {s['expected']:<10} {s['actual']:<8} {match:<8} {s['text'][:30]}...")

            print(f"\nTop-2 selected: {result['top_indices']} (expected: {result['expected_top']})")
            print(f"Top-2 correct: {result['top_correct']}/2")

            total_exact += result["exact_matches"]
            total_close += result["close_matches"]
            total_memories += len(result["scores"])
            total_top_correct += result["top_correct"]

        # Summary
        print(f"\n{'='*70}")
        print("SUMMARY")
        print(f"{'='*70}")
        print(f"Exact score matches: {total_exact}/{total_memories} ({100*total_exact/total_memories:.1f}%)")
        print(f"Close matches (±1):  {total_close}/{total_memories} ({100*total_close/total_memories:.1f}%)")
        print(f"Top-2 identification: {total_top_correct}/{len(TEST_CASES)*2} ({100*total_top_correct/(len(TEST_CASES)*2):.1f}%)")

        # Entity confusion check
        print(f"\n{'='*70}")
        print("ENTITY CONFUSION CHECK")
        print(f"{'='*70}")
        wrong_person_errors = 0
        for result in results:
            for s in result["scores"]:
                # Check if wrong person got high score
                if s["expected"] == 0 and s["actual"] >= 2:
                    print(f"  ERROR: [{s['speaker']}] scored {s['actual']} (expected 0): {s['text'][:40]}...")
                    wrong_person_errors += 1

        if wrong_person_errors == 0:
            print("  No entity confusion errors detected!")
        else:
            print(f"  Total entity confusion errors: {wrong_person_errors}")

        # Verdict
        print(f"\n{'='*70}")
        print("VERDICT")
        print(f"{'='*70}")
        top_accuracy = 100*total_top_correct/(len(TEST_CASES)*2)
        if top_accuracy >= 80 and wrong_person_errors == 0:
            print("[PASS] Phi 3.5 is SUITABLE for reranking")
        elif top_accuracy >= 60:
            print("[OK] Phi 3.5 is ACCEPTABLE for reranking (some errors)")
        else:
            print("[FAIL] Phi 3.5 may NOT be suitable for reranking")


if __name__ == "__main__":
    asyncio.run(main())
