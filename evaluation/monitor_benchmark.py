"""
Benchmark Monitor Script
Monitors the v3.2 benchmark progress and displays real-time statistics.
"""

import os
import json
import time
import glob
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"

def get_latest_result_file():
    """Find the most recent benchmark result file."""
    pattern = str(RESULTS_DIR / "locomo10_PARALLEL_V3*.json")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def analyze_results(filepath):
    """Analyze benchmark results from JSON file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = data.get('results', [])
    if not results:
        return None

    # Category breakdown
    categories = {}
    for r in results:
        cat = r.get('category', 'unknown')
        if cat not in categories:
            categories[cat] = {'correct': 0, 'total': 0, 'questions': []}
        categories[cat]['total'] += 1
        if r.get('correct', False):
            categories[cat]['correct'] += 1
        else:
            categories[cat]['questions'].append({
                'q': r.get('question', '')[:60],
                'gold': r.get('gold_answer', '')[:40],
                'gen': r.get('generated_answer', '')[:40],
            })

    return {
        'filepath': filepath,
        'benchmark': data.get('benchmark', 'Unknown'),
        'total': len(results),
        'correct': sum(1 for r in results if r.get('correct', False)),
        'categories': categories,
        'timestamp': data.get('timestamp', 'Unknown'),
    }

def display_progress(stats):
    """Display benchmark progress."""
    if not stats:
        print("No results found yet...")
        return

    print("\n" + "=" * 70)
    print(f"BENCHMARK MONITOR - {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 70)
    print(f"Benchmark: {stats['benchmark']}")
    print(f"File: {Path(stats['filepath']).name}")
    print(f"Timestamp: {stats['timestamp']}")
    print("-" * 70)

    overall_acc = stats['correct'] / stats['total'] if stats['total'] > 0 else 0
    print(f"\nOVERALL: {stats['correct']}/{stats['total']} ({overall_acc*100:.1f}%)")

    print("\nCATEGORY BREAKDOWN:")
    print(f"{'Category':<15} {'Correct':<10} {'Total':<10} {'Accuracy':<10}")
    print("-" * 45)

    for cat, data in sorted(stats['categories'].items()):
        acc = data['correct'] / data['total'] if data['total'] > 0 else 0
        status = "OK" if acc >= 0.5 else "LOW"
        print(f"{cat:<15} {data['correct']:<10} {data['total']:<10} {acc*100:>6.1f}% {status}")

    # Focus on temporal
    if 'temporal' in stats['categories']:
        temp = stats['categories']['temporal']
        print("\n" + "-" * 70)
        print("TEMPORAL FOCUS (Target: 60%+)")
        temp_acc = temp['correct'] / temp['total'] if temp['total'] > 0 else 0
        print(f"Current: {temp['correct']}/{temp['total']} = {temp_acc*100:.1f}%")

        if temp['questions'] and len(temp['questions']) <= 10:
            print("\nRecent WRONG temporal questions:")
            for q in temp['questions'][-5:]:
                print(f"  Q: {q['q']}...")
                print(f"     Gold: {q['gold']}")
                print(f"     Gen:  {q['gen']}")

    print("=" * 70)

def monitor_loop(interval=30):
    """Continuously monitor benchmark progress."""
    print("Starting benchmark monitor...")
    print(f"Watching: {RESULTS_DIR}")
    print(f"Refresh interval: {interval}s")
    print("Press Ctrl+C to stop\n")

    last_file = None
    last_count = 0

    try:
        while True:
            latest = get_latest_result_file()

            if latest:
                stats = analyze_results(latest)
                if stats:
                    # Check if new data
                    if latest != last_file or stats['total'] != last_count:
                        display_progress(stats)
                        last_file = latest
                        last_count = stats['total']
                    else:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] No new results... (total: {stats['total']})")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for results file...")

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")

if __name__ == "__main__":
    import sys

    # One-shot mode or continuous
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        latest = get_latest_result_file()
        if latest:
            stats = analyze_results(latest)
            display_progress(stats)
        else:
            print("No results file found.")
    else:
        monitor_loop(interval=20)
