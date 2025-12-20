"""
Quick Results Analysis - Compare baseline vs optimism
"""
import json
from pathlib import Path
from collections import defaultdict

results_path = Path(__file__).parent / "optimism.json"
with open(results_path) as f:
    data = json.load(f)

# Group by category
by_category = defaultdict(lambda: {"correct": 0, "total": 0})
for r in data['results']:
    cat = r['category']
    by_category[cat]['total'] += 1
    if r['correct']:
        by_category[cat]['correct'] += 1

# Calculate overall
total_correct = sum(r['correct'] for r in data['results'])
total_questions = len(data['results'])

print("="*80)
print("BENCHMARK RESULTS - OPTIMISM.JSON")
print("="*80)
print(f"\nTotal Questions: {total_questions}")
print(f"Overall Accuracy: {total_correct}/{total_questions} ({100*total_correct/total_questions:.1f}%)")

print("\n" + "="*80)
print("BY CATEGORY:")
print("="*80)

categories_order = ['single_hop', 'multi_hop', 'temporal', 'open_domain']
for cat in categories_order:
    if cat in by_category:
        stats = by_category[cat]
        acc = 100 * stats['correct'] / stats['total']
        print(f"{cat:15} {stats['correct']:3}/{stats['total']:3}  ({acc:5.1f}%)")

# Find failing single_hop questions
single_hop_fails = [r for r in data['results'] if r['category'] == 'single_hop' and not r['correct']]
print(f"\n" + "="*80)
print(f"SINGLE_HOP FAILURES: {len(single_hop_fails)} questions")
print("="*80)

# Show first 5 failures
for i, f in enumerate(single_hop_fails[:5]):
    print(f"\nQ{f['id']}: {f['question']}")
    print(f"  Gold: {f['gold_answer']}")
    print(f"  Got:  {str(f['generated_answer'])[:100]}...")
    print(f"  Memories: {len(f['retrieved_memories'])}")

# Latency stats
latencies = [r['latency_ms'] for r in data['results']]
avg_latency = sum(latencies) / len(latencies)
print(f"\n" + "="*80)
print(f"LATENCY:")
print("="*80)
print(f"Average: {avg_latency:.1f}ms")
print(f"Min: {min(latencies):.1f}ms")
print(f"Max: {max(latencies):.1f}ms")
