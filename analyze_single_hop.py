import json

# Use latest results file
data = json.load(open('evaluation/results/locomo10_TESSERACT_20251210_143209.json'))

wrong = [r for r in data['results'] if r['category'] == 'single_hop' and not r['correct']]

print(f"Single-hop failures: {len(wrong)}/32")
print("=" * 80)

for i, r in enumerate(wrong):
    print(f"\n[{i+1}] Question: {r['question']}")
    print(f"Gold: {r['gold']}")
    print(f"Generated: {r['generated'][:300]}...")
    print(f"Charge: {r.get('charge', 'N/A')}")

    # Classify failure type
    gold_lower = r['gold'].lower()
    gen_lower = r['generated'].lower()

    if "not" in gen_lower and ("stated" in gen_lower or "provided" in gen_lower or "mentioned" in gen_lower):
        failure_type = "SEMANTIC_GAP (LLM says info not available)"
    elif any(word in gold_lower for word in gold_lower.split(',')) and not all(word.strip() in gen_lower for word in gold_lower.split(',')):
        failure_type = "INCOMPLETE_LIST (missing items)"
    elif len(r['generated']) > len(r['gold']) * 3:
        failure_type = "OVER_VERBOSE (too much info)"
    else:
        failure_type = "OTHER"

    print(f"Failure Type: {failure_type}")
    print("-" * 80)
