"""
Latency checker for MemMachine benchmark results.

This script analyzes latency data from creditcard.json benchmark output.
It provides detailed breakdowns by category, percentile analysis, and identifies slow queries.
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Default paths
RESULTS_PATH = Path(__file__).parent / "creditcard.json"


def load_results(path: Path = RESULTS_PATH) -> dict:
    """Load benchmark results from JSON file."""
    if not path.exists():
        print(f"Error: Results file not found: {path}")
        sys.exit(1)

    with open(path) as f:
        return json.load(f)


def calculate_percentiles(values: list[float], percentiles: list[int] = [50, 90, 95, 99]) -> dict:
    """Calculate percentile values from a list of floats."""
    if not values:
        return {f"p{p}": 0 for p in percentiles}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    result = {}

    for p in percentiles:
        idx = int(n * p / 100)
        idx = min(idx, n - 1)
        result[f"p{p}"] = sorted_vals[idx]

    result["min"] = sorted_vals[0]
    result["max"] = sorted_vals[-1]
    result["mean"] = sum(sorted_vals) / n
    result["count"] = n

    return result


def analyze_latencies(data: dict) -> dict:
    """Analyze latency data from benchmark results."""
    results = data.get("results", [])
    metadata = data.get("metadata", {})

    if not results:
        return {"error": "No results found in benchmark data"}

    # Check if results have latency data
    sample = results[0] if results else {}
    has_latency = "latency_ms" in sample or "timing" in sample

    # Collect latencies by category
    latencies_by_category = defaultdict(list)
    latencies_by_correct = {"correct": [], "incorrect": []}
    all_latencies = []
    slow_queries = []

    for r in results:
        # Extract latency - try multiple field names
        latency = r.get("latency_ms") or r.get("timing", {}).get("total_ms", 0)

        if latency > 0:
            category = r.get("category", "unknown")
            correct = r.get("correct", False)

            latencies_by_category[category].append(latency)
            all_latencies.append(latency)

            if correct:
                latencies_by_correct["correct"].append(latency)
            else:
                latencies_by_correct["incorrect"].append(latency)

            # Track slow queries (>5s)
            if latency > 5000:
                slow_queries.append({
                    "id": r.get("id"),
                    "category": category,
                    "question": r.get("question", "")[:80],
                    "latency_ms": latency,
                    "correct": correct,
                })

    analysis = {
        "has_latency_data": has_latency or len(all_latencies) > 0,
        "total_questions": len(results),
        "questions_with_latency": len(all_latencies),
    }

    if all_latencies:
        analysis["overall"] = calculate_percentiles(all_latencies)
        analysis["by_category"] = {
            cat: calculate_percentiles(lats)
            for cat, lats in latencies_by_category.items()
        }
        analysis["by_correctness"] = {
            key: calculate_percentiles(lats)
            for key, lats in latencies_by_correct.items()
            if lats
        }
        analysis["slow_queries"] = slow_queries[:10]  # Top 10 slowest

    return analysis


def print_latency_report(analysis: dict, metadata: dict):
    """Print a formatted latency report."""
    print("=" * 70)
    print("MEMACHINE BENCHMARK LATENCY REPORT")
    print("=" * 70)

    # Metadata
    print(f"\nBenchmark: {metadata.get('model', 'Unknown')}")
    print(f"Timestamp: {metadata.get('timestamp', 'Unknown')}")
    print(f"Total Questions: {metadata.get('total_questions', 0)}")
    print(f"Accuracy: {metadata.get('accuracy', 0):.1f}%")

    if not analysis.get("has_latency_data"):
        print("\n" + "=" * 70)
        print("WARNING: No latency data found in benchmark results!")
        print("=" * 70)
        print("\nTo enable latency tracking, the benchmark needs to record timing data.")
        print("The run_benchmark.py script should be modified to include 'latency_ms'")
        print("in each result entry.")
        print("\nAlternatively, run the benchmark with timing enabled:")
        print("  python check_latencies.py --run")
        return

    # Overall latency stats
    print("\n" + "-" * 70)
    print("OVERALL LATENCY STATISTICS")
    print("-" * 70)

    overall = analysis.get("overall", {})
    print(f"\n{'Metric':<15} {'Value':>12}")
    print(f"{'-'*30}")
    print(f"{'Count':<15} {overall.get('count', 0):>12}")
    print(f"{'Min':<15} {overall.get('min', 0):>10.0f}ms")
    print(f"{'Mean':<15} {overall.get('mean', 0):>10.0f}ms")
    print(f"{'P50 (Median)':<15} {overall.get('p50', 0):>10.0f}ms")
    print(f"{'P90':<15} {overall.get('p90', 0):>10.0f}ms")
    print(f"{'P95':<15} {overall.get('p95', 0):>10.0f}ms")
    print(f"{'P99':<15} {overall.get('p99', 0):>10.0f}ms")
    print(f"{'Max':<15} {overall.get('max', 0):>10.0f}ms")

    # Latency by category
    by_category = analysis.get("by_category", {})
    if by_category:
        print("\n" + "-" * 70)
        print("LATENCY BY CATEGORY")
        print("-" * 70)
        print(f"\n{'Category':<15} {'Count':>8} {'Mean':>10} {'P50':>10} {'P90':>10} {'P99':>10}")
        print(f"{'-'*65}")

        for cat in ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]:
            if cat in by_category:
                stats = by_category[cat]
                print(f"{cat:<15} {stats['count']:>8} {stats['mean']:>9.0f}ms {stats['p50']:>9.0f}ms {stats['p90']:>9.0f}ms {stats['p99']:>9.0f}ms")

    # Latency by correctness
    by_correct = analysis.get("by_correctness", {})
    if by_correct:
        print("\n" + "-" * 70)
        print("LATENCY BY CORRECTNESS")
        print("-" * 70)
        print(f"\n{'Result':<15} {'Count':>8} {'Mean':>10} {'P50':>10} {'P90':>10}")
        print(f"{'-'*55}")

        for key in ["correct", "incorrect"]:
            if key in by_correct:
                stats = by_correct[key]
                print(f"{key:<15} {stats['count']:>8} {stats['mean']:>9.0f}ms {stats['p50']:>9.0f}ms {stats['p90']:>9.0f}ms")

    # Slow queries
    slow_queries = analysis.get("slow_queries", [])
    if slow_queries:
        print("\n" + "-" * 70)
        print(f"SLOW QUERIES (>{5000}ms) - Top {len(slow_queries)}")
        print("-" * 70)

        for sq in slow_queries:
            status = "OK" if sq["correct"] else "FAIL"
            print(f"\n  [{status}] Q{sq['id']} ({sq['category']}) - {sq['latency_ms']:.0f}ms")
            print(f"       {sq['question']}...")

    print("\n" + "=" * 70)


def check_benchmark_running():
    """Check if the benchmark is currently running by watching the file."""
    import time

    if not RESULTS_PATH.exists():
        return False

    # Check if file was modified in last 30 seconds
    mtime = RESULTS_PATH.stat().st_mtime
    now = time.time()
    return (now - mtime) < 30


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Check latencies from MemMachine benchmark")
    parser.add_argument("--file", "-f", type=Path, default=RESULTS_PATH,
                       help="Path to benchmark results JSON file")
    parser.add_argument("--watch", "-w", action="store_true",
                       help="Watch mode: continuously monitor the file for updates")
    parser.add_argument("--json", "-j", action="store_true",
                       help="Output analysis as JSON")

    args = parser.parse_args()

    if args.watch:
        import time
        print(f"Watching {args.file} for updates... (Ctrl+C to stop)")
        last_mtime = 0

        while True:
            try:
                if args.file.exists():
                    mtime = args.file.stat().st_mtime
                    if mtime > last_mtime:
                        last_mtime = mtime
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] File updated, analyzing...")
                        data = load_results(args.file)
                        analysis = analyze_latencies(data)

                        if args.json:
                            print(json.dumps(analysis, indent=2))
                        else:
                            print_latency_report(analysis, data.get("metadata", {}))

                time.sleep(2)
            except KeyboardInterrupt:
                print("\nStopped watching.")
                break
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(5)
    else:
        # Single run
        data = load_results(args.file)
        analysis = analyze_latencies(data)

        if args.json:
            print(json.dumps(analysis, indent=2))
        else:
            print_latency_report(analysis, data.get("metadata", {}))


if __name__ == "__main__":
    main()
