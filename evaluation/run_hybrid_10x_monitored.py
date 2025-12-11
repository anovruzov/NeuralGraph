"""
Run Hybrid Benchmark 10 times with live monitoring.
Shows: progress %, latency (retrieval/answer), accuracy per run.
"""

import json
import subprocess
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import statistics

NUM_RUNS = 10
RESULTS_DIR = Path(__file__).parent / "results"


def parse_live_output(line: str, stats: dict):
    """Parse benchmark output for live stats."""
    # Match question results: [123] category: CORRECT/WRONG (boost: 0.123)
    match = re.search(r'\[(\d+)\]\s+(\w+):\s+(CORRECT|WRONG)', line)
    if match:
        q_num = int(match.group(1))
        category = match.group(2)
        correct = match.group(3) == "CORRECT"
        stats["questions_done"] = q_num
        stats["correct"] += 1 if correct else 0
        stats["by_category"][category]["total"] += 1
        if correct:
            stats["by_category"][category]["correct"] += 1
        return True

    # Match ingestion
    if "Ingesting" in line:
        match = re.search(r'Ingesting (\d+) messages', line)
        if match:
            stats["messages_ingesting"] = int(match.group(1))

    # Match conversation progress
    if "[Conv" in line:
        match = re.search(r'\[Conv (\d+)/(\d+)\]', line)
        if match:
            stats["conv_current"] = int(match.group(1))
            stats["conv_total"] = int(match.group(2))

    # Match question count
    if "Processing" in line and "questions" in line:
        match = re.search(r'Processing (\d+) questions', line)
        if match:
            stats["questions_in_conv"] = int(match.group(1))

    return False


def run_single_benchmark(run_number: int) -> dict:
    """Run single benchmark with live monitoring."""
    print(f"\n{'='*70}")
    print(f"RUN {run_number}/{NUM_RUNS}")
    print(f"{'='*70}")

    start_time = time.time()

    stats = {
        "questions_done": 0,
        "correct": 0,
        "conv_current": 0,
        "conv_total": 10,
        "questions_in_conv": 0,
        "messages_ingesting": 0,
        "by_category": defaultdict(lambda: {"total": 0, "correct": 0}),
    }

    # Estimate ~1986 questions total
    TOTAL_QUESTIONS_ESTIMATE = 1986

    cmd = [
        sys.executable,
        str(Path(__file__).parent / "locomo10_hybrid_benchmark.py"),
        "--max-conversations", "10"
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        cwd=str(Path(__file__).parent),
        bufsize=1
    )

    last_print_time = 0

    for line in process.stdout:
        line = line.strip()

        parse_live_output(line, stats)

        # Live update every 2 seconds or on question completion
        now = time.time()
        if now - last_print_time >= 2 or "[" in line and "]" in line:
            elapsed = now - start_time
            q_done = stats["questions_done"]
            pct = (q_done / TOTAL_QUESTIONS_ESTIMATE) * 100 if TOTAL_QUESTIONS_ESTIMATE > 0 else 0
            acc = (stats["correct"] / q_done * 100) if q_done > 0 else 0

            # Calculate avg time per question
            avg_latency = (elapsed / q_done) if q_done > 0 else 0

            # ETA
            remaining = TOTAL_QUESTIONS_ESTIMATE - q_done
            eta_seconds = remaining * avg_latency if avg_latency > 0 else 0
            eta_min = eta_seconds / 60

            status_line = (
                f"\r  [{pct:5.1f}%] Q: {q_done}/{TOTAL_QUESTIONS_ESTIMATE} | "
                f"Acc: {acc:5.1f}% ({stats['correct']}/{q_done}) | "
                f"Latency: {avg_latency:.1f}s/q | "
                f"ETA: {eta_min:.1f}m | "
                f"Conv: {stats['conv_current']}/{stats['conv_total']}"
            )
            print(status_line, end='', flush=True)
            last_print_time = now

    process.wait()
    elapsed = time.time() - start_time

    print()  # newline after progress

    # Find latest results file
    results_files = sorted(RESULTS_DIR.glob("locomo10_HYBRID_*.json"), key=lambda x: x.stat().st_mtime)

    if not results_files:
        print(f"  WARNING: No results file found")
        return None

    latest_file = results_files[-1]

    with open(latest_file) as f:
        data = json.load(f)

    # Calculate latency stats from results
    results = data.get("results", [])
    retrieval_times = [r.get("retrieval_time_ms", 0) for r in results if r.get("retrieval_time_ms")]
    answer_times = [r.get("time_seconds", 0) for r in results if r.get("time_seconds")]

    avg_retrieval_ms = statistics.mean(retrieval_times) if retrieval_times else 0
    avg_answer_s = statistics.mean(answer_times) if answer_times else 0

    print(f"\n  Run {run_number} Complete:")
    print(f"    Accuracy: {data['accuracy']*100:.2f}% ({data['correct']}/{data['total_questions']})")
    print(f"    Avg Retrieval: {avg_retrieval_ms:.1f}ms")
    print(f"    Avg Answer: {avg_answer_s:.2f}s")
    print(f"    Time: {elapsed/60:.1f} minutes")

    # Category breakdown
    print(f"    Categories:")
    for cat in ["temporal", "multi_hop", "adversarial", "single_hop", "open_domain"]:
        if cat in data.get("category_accuracy", {}):
            print(f"      {cat}: {data['category_accuracy'][cat]*100:.1f}%")

    return {
        "run_number": run_number,
        "file": str(latest_file),
        "accuracy": data["accuracy"],
        "total_questions": data["total_questions"],
        "correct": data["correct"],
        "category_accuracy": data["category_accuracy"],
        "elapsed_minutes": elapsed / 60,
        "avg_retrieval_ms": avg_retrieval_ms,
        "avg_answer_seconds": avg_answer_s,
    }


def aggregate_and_print(runs: list[dict]):
    """Aggregate all runs and print final stats."""
    valid_runs = [r for r in runs if r is not None]

    if not valid_runs:
        print("No valid runs!")
        return {}

    accuracies = [r["accuracy"] * 100 for r in valid_runs]
    retrieval_times = [r["avg_retrieval_ms"] for r in valid_runs]
    answer_times = [r["avg_answer_seconds"] for r in valid_runs]

    print("\n" + "=" * 70)
    print("FINAL RESULTS - 10 RUNS")
    print("=" * 70)

    print(f"\nRuns: {len(valid_runs)}")
    print(f"Total Questions Evaluated: {sum(r['total_questions'] for r in valid_runs)}")

    print(f"\n{'='*50}")
    print("ACCURACY")
    print(f"{'='*50}")
    print(f"  Mean:   {statistics.mean(accuracies):.2f}% (+/- {statistics.stdev(accuracies):.2f}%)" if len(accuracies) > 1 else f"  Mean: {accuracies[0]:.2f}%")
    print(f"  Median: {statistics.median(accuracies):.2f}%")
    print(f"  Range:  {min(accuracies):.2f}% - {max(accuracies):.2f}%")
    print(f"  All:    {[f'{a:.1f}' for a in accuracies]}")

    print(f"\n{'='*50}")
    print("LATENCY")
    print(f"{'='*50}")
    print(f"  Retrieval: {statistics.mean(retrieval_times):.1f}ms (+/- {statistics.stdev(retrieval_times):.1f}ms)" if len(retrieval_times) > 1 else f"  Retrieval: {retrieval_times[0]:.1f}ms")
    print(f"  Answer:    {statistics.mean(answer_times):.2f}s (+/- {statistics.stdev(answer_times):.2f}s)" if len(answer_times) > 1 else f"  Answer: {answer_times[0]:.2f}s")

    print(f"\n{'='*50}")
    print("PER-CATEGORY ACCURACY")
    print(f"{'='*50}")

    categories = ["temporal", "multi_hop", "adversarial", "single_hop", "open_domain"]
    category_stats = {}

    for cat in categories:
        cat_accs = [r["category_accuracy"].get(cat, 0) * 100 for r in valid_runs if cat in r["category_accuracy"]]
        if cat_accs:
            mean_acc = statistics.mean(cat_accs)
            std_acc = statistics.stdev(cat_accs) if len(cat_accs) > 1 else 0
            print(f"  {cat:15s}: {mean_acc:5.2f}% (+/- {std_acc:.2f}%)")
            category_stats[cat] = {"mean": mean_acc, "std": std_acc, "values": cat_accs}

    # Save final aggregated results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_file = RESULTS_DIR / f"locomo10_HYBRID_10RUNS_{timestamp}.json"

    final_data = {
        "num_runs": len(valid_runs),
        "total_questions_all_runs": sum(r['total_questions'] for r in valid_runs),
        "accuracy": {
            "mean": statistics.mean(accuracies),
            "std": statistics.stdev(accuracies) if len(accuracies) > 1 else 0,
            "median": statistics.median(accuracies),
            "min": min(accuracies),
            "max": max(accuracies),
            "values": accuracies,
        },
        "latency": {
            "retrieval_ms": {
                "mean": statistics.mean(retrieval_times),
                "std": statistics.stdev(retrieval_times) if len(retrieval_times) > 1 else 0,
            },
            "answer_seconds": {
                "mean": statistics.mean(answer_times),
                "std": statistics.stdev(answer_times) if len(answer_times) > 1 else 0,
            },
        },
        "category_accuracy": category_stats,
        "per_run": valid_runs,
    }

    with open(final_file, 'w') as f:
        json.dump(final_data, f, indent=2)

    print(f"\nResults saved to: {final_file}")

    return final_data


def main():
    print("=" * 70)
    print("HYBRID BENCHMARK - 10 RUNS WITH LIVE MONITORING")
    print("=" * 70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    RESULTS_DIR.mkdir(exist_ok=True)

    runs = []
    total_start = time.time()

    for i in range(1, NUM_RUNS + 1):
        run_result = run_single_benchmark(i)
        runs.append(run_result)

        # Save intermediate
        intermediate_file = RESULTS_DIR / "hybrid_10x_intermediate.json"
        with open(intermediate_file, 'w') as f:
            json.dump({
                "completed": i,
                "total": NUM_RUNS,
                "runs": [r for r in runs if r],
            }, f, indent=2)

        print(f"\n  Progress: {i}/{NUM_RUNS} runs complete")
        if i < NUM_RUNS:
            print(f"  Starting run {i+1} in 5 seconds...")
            time.sleep(5)

    total_elapsed = time.time() - total_start

    final_stats = aggregate_and_print(runs)

    print(f"\nTotal time: {total_elapsed/3600:.2f} hours")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
