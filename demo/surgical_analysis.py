"""
SURGICAL ANALYSIS - Single_hop Failure Deep Dive
Trace each failing question through the entire pipeline to find exact failure point.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from NeuralGraph.temporal_utils import infer_query_mode

# Load results
results_path = Path(__file__).parent / "optimism.json"
with open(results_path) as f:
    data = json.load(f)

# Get failing single_hop questions
fails = [r for r in data['results'] if r['category'] == 'single_hop' and not r['correct']]

print("="*80)
print("SURGICAL ANALYSIS - SINGLE_HOP FAILURES")
print("="*80)

for i, f in enumerate(fails[:10]):
    print(f"\n{'='*80}")
    print(f"FAILURE #{i+1} - Q{f['id']}")
    print(f"{'='*80}")

    question = f['question']
    gold = str(f['gold_answer'])
    generated = str(f['generated_answer'])

    print(f"\nQUESTION: {question}")
    print(f"GOLD ANSWER: {gold}")
    print(f"GENERATED: {generated[:150]}...")

    # Step 1: Check routing
    routed_mode = infer_query_mode(question)
    print(f"\n[STEP 1] ROUTING: {routed_mode}")

    # Step 2: Check if gold answer is in retrieved memories
    memories_text = [m['text'] for m in f['retrieved_memories']]
    gold_lower = gold.lower()

    # Split gold by comma/and to handle multiple items
    gold_parts = [p.strip() for p in gold_lower.replace(",", "|").replace(" and ", "|").split("|")]
    gold_parts = [p for p in gold_parts if len(p) > 2]

    found_in_memories = {}
    for part in gold_parts:
        for idx, mem_text in enumerate(memories_text):
            if part in mem_text.lower():
                if part not in found_in_memories:
                    found_in_memories[part] = []
                found_in_memories[part].append(idx + 1)

    print(f"\n[STEP 2] GOLD ANSWER IN MEMORIES?")
    if found_in_memories:
        print(f"  [YES] - Found in memories:")
        for part, positions in found_in_memories.items():
            print(f"    '{part}' -> memories {positions}")
    else:
        print(f"  [NO] - Gold answer not found in any of the 15 memories")
        print(f"  Gold parts searched: {gold_parts}")
        print(f"  -> RETRIEVAL FAILURE")
        continue

    # Step 3: Check coverage
    coverage = len(found_in_memories) / len(gold_parts) if gold_parts else 0
    print(f"\n[STEP 3] COVERAGE: {len(found_in_memories)}/{len(gold_parts)} gold parts found ({coverage*100:.0f}%)")

    if coverage < 1.0:
        missing = [p for p in gold_parts if p not in found_in_memories]
        print(f"  Missing parts: {missing}")
        print(f"  -> INCOMPLETE RETRIEVAL")

    # Step 4: Check if model generated any of the gold parts
    gen_lower = generated.lower()
    generated_parts = []
    missed_parts = []

    for part in gold_parts:
        if part in gen_lower:
            generated_parts.append(part)
        else:
            missed_parts.append(part)

    print(f"\n[STEP 4] EXTRACTION ANALYSIS:")
    print(f"  Generated {len(generated_parts)}/{len(gold_parts)} gold parts")
    if generated_parts:
        print(f"    [+] Generated: {generated_parts}")
    if missed_parts:
        print(f"    [-] Missed: {missed_parts}")

        # Check if missed parts were in memories
        for part in missed_parts:
            if part in found_in_memories:
                print(f"      '{part}' WAS in memories {found_in_memories[part]} but NOT extracted!")
                print(f"      -> EXTRACTION FAILURE")

    # Step 5: Diagnosis
    print(f"\n[STEP 5] DIAGNOSIS:")

    if not found_in_memories:
        print(f"  ROOT CAUSE: Retrieval failed to find gold answer")
        print(f"  FIX: Improve retrieval/reranking to surface memories containing '{gold_parts}'")
    elif coverage < 1.0:
        missing = [p for p in gold_parts if p not in found_in_memories]
        print(f"  ROOT CAUSE: Incomplete retrieval - missing {missing}")
        print(f"  FIX: Retrieval found some parts but not all")
    elif missed_parts:
        # All parts in memories but not all extracted
        print(f"  ROOT CAUSE: Extraction failure")
        print(f"  DETAILS: Memories contained {list(found_in_memories.keys())}")
        print(f"           but only extracted {generated_parts}")
        print(f"  FIX: Improve AGGREGATION prompt to be more thorough")
    else:
        # Generated all parts but still marked wrong
        print(f"  ROOT CAUSE: Format/wording mismatch")
        print(f"  DETAILS: Generated '{generated[:80]}' vs gold '{gold}'")
        print(f"  FIX: Judge may be too strict, or answer has extra/wrong info")

    # Show top 3 memories for context
    print(f"\n[CONTEXT] TOP 3 MEMORIES:")
    for idx in range(min(3, len(f['retrieved_memories']))):
        mem = f['retrieved_memories'][idx]
        print(f"  {idx+1}. [{mem['speaker']}] {mem['text'][:100]}...")

print(f"\n{'='*80}")
print("SUMMARY")
print(f"{'='*80}")
print(f"Analyzed {min(10, len(fails))} failures")
print("\nRun this script to see detailed pipeline trace for each failure.")
