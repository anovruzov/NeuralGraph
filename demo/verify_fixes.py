"""
Verify all 3 fixes are working in the new benchmark
"""
import json
from pathlib import Path

results_path = Path(__file__).parent / "optimism.json"
try:
    with open(results_path) as f:
        data = json.load(f)
except:
    print("No results yet...")
    exit(0)

print(f"\n{'='*80}")
print(f"VERIFICATION - {len(data['results'])} questions processed")
print(f"{'='*80}")

# FIX 1: Q140 - "What was X about?" should NOT route to TEMPORAL
q140 = [r for r in data['results'] if r['id'] == 140]
if q140:
    r = q140[0]
    print(f"\n[FIX 1] Q140 - 'What was poetry reading about?'")
    print(f"  Expected: Should NOT answer with date (should describe content)")
    print(f"  Answer: {r['generated_answer'][:100]}...")
    print(f"  Gold: {r['gold_answer'][:100]}...")
    is_date = any(x in r['generated_answer'].lower() for x in ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december', '2023', '2024'])
    print(f"  Status: {'[FAIL] Still answering with date!' if is_date else '[PASS] Not a date answer'}")
else:
    print(f"\n[FIX 1] Q140 not yet processed")

# FIX 2: Q175 - Temporal granularity (should be "June 2023" not "8 June 2023")
q175 = [r for r in data['results'] if r['id'] == 175]
if q175:
    r = q175[0]
    print(f"\n[FIX 2] Q175 - Temporal granularity")
    print(f"  Question: {r['question']}")
    print(f"  Answer: {r['generated_answer']}")
    print(f"  Gold: {r['gold_answer']}")
    has_specific_day = any(f"{i} " in r['generated_answer'] or f" {i}" in r['generated_answer'] for i in range(1, 32))
    print(f"  Status: {'[WARN] Has specific day' if has_specific_day and 'June' in r['generated_answer'] else '[PASS] Correct granularity'}")
else:
    print(f"\n[FIX 2] Q175 not yet processed")

# FIX 3: Q176 - Should not invent "ad campaign", "fashion bloggers"
q176 = [r for r in data['results'] if r['id'] == 176]
if q176:
    r = q176[0]
    print(f"\n[FIX 3] Q176 - Anti-hallucination")
    print(f"  Question: {r['question']}")
    print(f"  Answer: {r['generated_answer'][:150]}...")
    print(f"  Gold: {r['gold_answer'][:150]}...")
    has_hallucination = any(x in r['generated_answer'].lower() for x in ['ad campaign', 'bloggers', 'influencers'])
    print(f"  Status: {'[FAIL] Still inventing details!' if has_hallucination else '[PASS] No hallucinations'}")
else:
    print(f"\n[FIX 3] Q176 not yet processed")

# CONTEXT WINDOW FIX: Q16 should have 30 memories
q16 = [r for r in data['results'] if r['id'] == 16]
if q16:
    r = q16[0]
    mem_count = len(r.get('retrieved_memories', []))
    print(f"\n[CONTEXT FIX] Q16 - AGGREGATION should get 30 memories")
    print(f"  Memories: {mem_count}")
    print(f"  Correct: {r['correct']}")
    print(f"  Status: {'[PASS] Has 30 memories!' if mem_count == 30 else f'[FAIL] Only {mem_count} memories'}")
else:
    print(f"\n[CONTEXT FIX] Q16 not yet processed")

print(f"\n{'='*80}")
