"""
Live progress monitor - check if AGGREGATION fix is working
"""
import json
from pathlib import Path
from collections import defaultdict

results_path = Path(__file__).parent / "maximizer.json"

try:
    with open(results_path) as f:
        data = json.load(f)
except:
    print("No results yet...")
    exit(0)

total = len(data['results'])
print(f"\n{'='*80}")
print(f"PROGRESS: {total} questions completed")
print(f"{'='*80}")

# Check if AGGREGATION questions are getting 30 memories
aggregation_questions = [r for r in data['results'] if r.get('query_mode') == 'AGGREGATION']
if aggregation_questions:
    print(f"\nAGGREGATION questions: {len(aggregation_questions)}")
    # Check first few to see memory count
    for r in aggregation_questions[:5]:
        mem_count = len(r.get('retrieved_memories', []))
        status = "[YES] 30 memories!" if mem_count >= 30 else f"[NO] Only {mem_count} memories"
        print(f"  Q{r['id']}: {status}")

# Quick accuracy breakdown
by_category = defaultdict(lambda: {"correct": 0, "total": 0})
for r in data['results']:
    cat = r['category']
    by_category[cat]['total'] += 1
    if r['correct']:
        by_category[cat]['correct'] += 1

print(f"\n{'='*80}")
print("ACCURACY SO FAR:")
print(f"{'='*80}")
for cat in ['single_hop', 'multi_hop', 'temporal', 'open_domain']:
    if cat in by_category:
        stats = by_category[cat]
        acc = 100 * stats['correct'] / stats['total']
        print(f"{cat:15} {stats['correct']:2}/{stats['total']:2}  ({acc:5.1f}%)")
