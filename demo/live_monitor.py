"""Live progress monitor for final_fixed.json"""
import json
import time
from pathlib import Path
from collections import defaultdict

results_path = Path(__file__).parent / "final_fixed.json"

while True:
    try:
        with open(results_path) as f:
            data = json.load(f)

        total = len(data['results'])
        by_cat = defaultdict(lambda: {"correct": 0, "total": 0})
        for r in data['results']:
            cat = r['category']
            by_cat[cat]['total'] += 1
            if r['correct']:
                by_cat[cat]['correct'] += 1

        print(f"\r[{total} Qs] ", end="")
        for cat in ['single_hop', 'multi_hop', 'temporal', 'open_domain']:
            if cat in by_cat:
                stats = by_cat[cat]
                acc = 100 * stats['correct'] / stats['total']
                print(f"{cat[:4]}: {acc:.0f}%  ", end="")
        print(f"", end="", flush=True)

        time.sleep(5)
    except:
        print("Waiting for results...")
        time.sleep(5)
