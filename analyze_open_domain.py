import json

# Use latest results file
data = json.load(open('evaluation/results/locomo10_TESSERACT_20251210_143209.json'))

wrong = [r for r in data['results'] if r['category'] == 'open_domain' and not r['correct']]

print(f"Open-domain failures: {len(wrong)}/13")
print("=" * 80)

for i, r in enumerate(wrong):
    print(f"\n[{i+1}] Question: {r['question']}")
    print(f"Query Type: {r.get('query_type', 'N/A')}")
    print(f"Gold: {r['gold']}")
    print(f"Generated: {r['generated'][:400]}...")
    print(f"Charge: {r.get('charge', 'N/A')}")
    print("-" * 80)

# Also check which ones now got WRONG that might have been routed to direct_fact
print("\n\n=== Check if any open_domain routed to direct_fact ===")
for r in data['results']:
    if r['category'] == 'open_domain':
        print(f"[{r.get('question_id', '?')}] {r.get('query_type', 'N/A')}: {'CORRECT' if r['correct'] else 'WRONG'} - {r['question'][:60]}...")
