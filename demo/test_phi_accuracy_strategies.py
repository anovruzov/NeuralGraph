"""
Test 3 strategies to improve Phi 3.5 reranking accuracy.

Strategy 1: Entity-First Filtering
Strategy 2: Chain-of-Thought Prompting
Strategy 3: Few-Shot Examples
"""

import asyncio
import aiohttp
import time
import re

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "phi3.5"

# Test cases - focus on entity confusion scenarios
TEST_CASES = [
    {
        "question": "What is Sarah's favorite restaurant?",
        "memories": [
            ("John loves pizza at Joe's Pizza", "John", 0),
            ("Sarah went to Carbone and loved it", "Sarah", 3),
            ("Sarah's birthday is in March", "Sarah", 0),
            ("Sarah said Carbone is her favorite", "Sarah", 3),
            ("Mike eats at Shake Shack daily", "Mike", 0),
        ],
    },
    {
        "question": "Where did Caroline travel last summer?",
        "memories": [
            ("Caroline went to Paris last July", "Caroline", 3),
            ("Sarah visited Tokyo in August", "Sarah", 0),  # TRAP: similar topic, wrong person
            ("Caroline loved the Eiffel Tower", "Caroline", 2),
            ("John stayed home all summer", "John", 0),
            ("Caroline's trip to France was amazing", "Caroline", 3),
        ],
    },
    {
        "question": "What is John's job?",
        "memories": [
            ("Sarah works as a nurse", "Sarah", 0),  # TRAP: job topic, wrong person
            ("John is a software developer at Microsoft", "John", 3),
            ("Mike teaches high school math", "Mike", 0),  # TRAP: job topic, wrong person
            ("John loves coding in Python", "John", 2),
            ("John got promoted last month", "John", 2),
        ],
    },
    {
        "question": "When is Mike's birthday?",
        "memories": [
            ("Sarah's birthday is December 25th", "Sarah", 0),  # TRAP: birthday, wrong person
            ("John was born in January", "John", 0),  # TRAP: birthday, wrong person
            ("Mike celebrates his birthday on July 4th", "Mike", 3),
            ("Mike had a party last summer", "Mike", 1),
            ("Caroline's birthday is next week", "Caroline", 0),  # TRAP
        ],
    },
]


# =============================================================================
# BASELINE: Original prompt
# =============================================================================
async def score_baseline(session, question, memory, speaker):
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

    return await call_model(session, prompt)


# =============================================================================
# STRATEGY 1: Entity-First Filtering
# =============================================================================
async def score_entity_first(session, question, memory, speaker):
    # Extract entity from question
    prompt = f"""<|user|>
STEP 1: Who is the question asking about?
STEP 2: Is this memory from or about that person?
STEP 3: If NO, score 0. If YES, score relevance 0-3.

Question: {question}
Memory from [{speaker}]: {memory}

If the memory is about a DIFFERENT person than asked, score 0.
Otherwise: 3=direct answer, 2=supporting, 1=weak, 0=unrelated.

Output only the final score (0, 1, 2, or 3):
<|end|>
<|assistant|>"""

    return await call_model(session, prompt)


# =============================================================================
# STRATEGY 2: Chain-of-Thought
# =============================================================================
async def score_chain_of_thought(session, question, memory, speaker):
    prompt = f"""<|user|>
Score this memory's relevance. Think step by step:

Question: {question}
Memory from [{speaker}]: {memory}

Step 1 - Who is the question about?
Step 2 - Who is the memory from/about?
Step 3 - Do they match? If NO -> Score 0
Step 4 - If YES, how relevant? 3=answers, 2=supports, 1=weak, 0=unrelated

Think through each step, then output ONLY the score number at the end.
<|end|>
<|assistant|>"""

    return await call_model(session, prompt, max_tokens=100)


# =============================================================================
# STRATEGY 3: Few-Shot Examples
# =============================================================================
async def score_few_shot(session, question, memory, speaker):
    prompt = f"""<|user|>
Score memory relevance 0-3. Examples:

Q: What is Sarah's favorite color?
M: [John]: John loves the color blue
Score: 0 (wrong person)

Q: What is Sarah's favorite color?
M: [Sarah]: Sarah said she loves purple
Score: 3 (direct answer)

Q: What is Sarah's favorite color?
M: [Sarah]: Sarah bought a new dress
Score: 0 (unrelated topic)

Now score this:
Q: {question}
M: [{speaker}]: {memory}

Score (0-3):
<|end|>
<|assistant|>"""

    return await call_model(session, prompt)


# =============================================================================
# STRATEGY 4: Hybrid - Soft Entity Check + Relevance
# =============================================================================
async def score_hybrid(session, question, memory, speaker):
    prompt = f"""<|user|>
Question: {question}
Memory from [{speaker}]: {memory}

First, check if this memory is about the SAME PERSON the question asks about.
- If DIFFERENT person: Score 0
- If SAME person or clearly relevant: Score based on how well it answers (1-3)

Scoring: 3=direct answer, 2=supporting evidence, 1=weakly related, 0=wrong person/unrelated

Output ONLY the score (0, 1, 2, or 3):
<|end|>
<|assistant|>"""

    return await call_model(session, prompt)


# =============================================================================
# STRATEGY 5: Two-Step with Speaker Match
# =============================================================================
async def score_two_step(session, question, memory, speaker):
    # Extract the person from question using simple heuristics
    q_lower = question.lower()

    # Common patterns: "What is X's", "Where did X", "When did X"
    import re
    match = re.search(r"(?:what is|where did|when did|when is|what does|where is|how did)\s+(\w+)'?s?", q_lower)
    if match:
        question_person = match.group(1).lower()
        speaker_lower = speaker.lower()

        # If clear mismatch, score 0 immediately
        if question_person != speaker_lower and question_person not in memory.lower():
            return 0

    # Otherwise, use baseline scoring
    return await score_baseline(session, question, memory, speaker)


async def call_model(session, prompt, max_tokens=10):
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": max_tokens}},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            result = await resp.json()
            response = result.get("response", "").strip()
            # Extract last digit (for CoT which may have reasoning)
            digits = re.findall(r'[0-3]', response)
            if digits:
                return int(digits[-1])
            return 1
    except:
        return 1


async def test_strategy(session, name, score_fn):
    """Test a scoring strategy on all test cases."""
    print(f"\n{'='*60}")
    print(f"STRATEGY: {name}")
    print(f"{'='*60}")

    total_exact = 0
    total_memories = 0
    entity_errors = 0

    for case in TEST_CASES:
        question = case["question"]
        for text, speaker, expected in case["memories"]:
            actual = await score_fn(session, question, text, speaker)
            is_correct = actual == expected

            if is_correct:
                total_exact += 1
            elif expected == 0 and actual >= 2:
                entity_errors += 1
                print(f"  ENTITY ERROR: Q={question[:30]}... [{speaker}] got {actual}, expected 0")

            total_memories += 1

    accuracy = 100 * total_exact / total_memories
    print(f"\nAccuracy: {total_exact}/{total_memories} ({accuracy:.1f}%)")
    print(f"Entity confusion errors: {entity_errors}")

    return {"name": name, "accuracy": accuracy, "entity_errors": entity_errors}


async def main():
    print("="*60)
    print("PHI 3.5 ACCURACY IMPROVEMENT STRATEGIES")
    print("="*60)
    print(f"Testing on {sum(len(c['memories']) for c in TEST_CASES)} memories")
    print("Focus: Entity confusion prevention")

    async with aiohttp.ClientSession() as session:
        # Warmup
        print("\nWarming up model...")
        await call_model(session, "test")

        results = []

        # Test each strategy
        results.append(await test_strategy(session, "BASELINE (original)", score_baseline))
        results.append(await test_strategy(session, "CHAIN-OF-THOUGHT", score_chain_of_thought))
        results.append(await test_strategy(session, "HYBRID", score_hybrid))
        results.append(await test_strategy(session, "TWO-STEP (heuristic)", score_two_step))

        # Summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        print(f"{'Strategy':<25} {'Accuracy':<12} {'Entity Errors':<15}")
        print("-"*55)
        for r in results:
            print(f"{r['name']:<25} {r['accuracy']:<12.1f}% {r['entity_errors']:<15}")

        # Best strategy
        best = max(results, key=lambda x: (x['accuracy'], -x['entity_errors']))
        print(f"\nBEST: {best['name']} ({best['accuracy']:.1f}% accuracy, {best['entity_errors']} entity errors)")


if __name__ == "__main__":
    asyncio.run(main())
