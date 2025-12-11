"""
Full LoCoMo Benchmark - 10 Complete Runs
All 10 conversations, all 1986+ questions, 10 times
Statistically significant results for publication.
"""

import json
import asyncio
import subprocess
import time
import statistics
from datetime import datetime
from pathlib import Path
from collections import defaultdict


NUM_RUNS = 10
RESULTS_DIR = Path(__file__).parent / "results"


def run_single_benchmark(run_number: int) -> dict:
    """Run single full benchmark (all 10 conversations)."""
    print(f"\n{'='*70}")
    print(f"RUN {run_number}/{NUM_RUNS} - Starting full LoCoMo benchmark")
    print(f"{'='*70}")

    start_time = time.time()

    # Run the hybrid benchmark with all 10 conversations
    cmd = [
        "python",
        str(Path(__file__).parent / "locomo10_hybrid_benchmark.py"),
        "--max-conversations", "10"
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        cwd=str(Path(__file__).parent)
    )

    elapsed = time.time() - start_time
    print(f"Run {run_number} completed in {elapsed/60:.1f} minutes")

    # Find the latest results file
    results_files = sorted(RESULTS_DIR.glob("locomo10_HYBRID_*.json"), key=lambda x: x.stat().st_mtime)

    if not results_files:
        print(f"WARNING: No results file found for run {run_number}")
        return None

    latest_file = results_files[-1]
    print(f"Results file: {latest_file.name}")

    with open(latest_file) as f:
        data = json.load(f)

    return {
        "run_number": run_number,
        "file": str(latest_file),
        "accuracy": data["accuracy"],
        "total_questions": data["total_questions"],
        "correct": data["correct"],
        "category_accuracy": data["category_accuracy"],
        "elapsed_minutes": elapsed / 60,
    }


def aggregate_results(runs: list[dict]) -> dict:
    """Aggregate results from all runs with statistics."""
    valid_runs = [r for r in runs if r is not None]

    if not valid_runs:
        return {"error": "No valid runs"}

    # Overall accuracy stats
    accuracies = [r["accuracy"] * 100 for r in valid_runs]

    # Per-category stats
    categories = ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]
    category_stats = {}

    for cat in categories:
        cat_accuracies = [r["category_accuracy"].get(cat, 0) * 100 for r in valid_runs if cat in r["category_accuracy"]]
        if cat_accuracies:
            category_stats[cat] = {
                "mean": statistics.mean(cat_accuracies),
                "std": statistics.stdev(cat_accuracies) if len(cat_accuracies) > 1 else 0,
                "min": min(cat_accuracies),
                "max": max(cat_accuracies),
                "values": cat_accuracies,
            }

    return {
        "num_runs": len(valid_runs),
        "total_questions_per_run": valid_runs[0]["total_questions"],
        "total_questions_all_runs": sum(r["total_questions"] for r in valid_runs),
        "overall_accuracy": {
            "mean": statistics.mean(accuracies),
            "std": statistics.stdev(accuracies) if len(accuracies) > 1 else 0,
            "min": min(accuracies),
            "max": max(accuracies),
            "median": statistics.median(accuracies),
            "values": accuracies,
        },
        "category_accuracy": category_stats,
        "per_run": valid_runs,
    }


def print_summary(stats: dict):
    """Print LinkedIn-ready summary."""
    print("\n" + "="*70)
    print("MEMMACHINE LOCOMO BENCHMARK - FINAL RESULTS")
    print("="*70)

    overall = stats["overall_accuracy"]
    print(f"\nRuns: {stats['num_runs']}")
    print(f"Total Questions Evaluated: {stats['total_questions_all_runs']}")
    print(f"Questions Per Run: {stats['total_questions_per_run']}")

    print(f"\n{'='*50}")
    print("OVERALL ACCURACY")
    print(f"{'='*50}")
    print(f"  Mean:   {overall['mean']:.2f}% (+/- {overall['std']:.2f}%)")
    print(f"  Median: {overall['median']:.2f}%")
    print(f"  Range:  {overall['min']:.2f}% - {overall['max']:.2f}%")

    print(f"\n{'='*50}")
    print("PER-CATEGORY ACCURACY (Mean +/- Std)")
    print(f"{'='*50}")

    for cat in ["temporal", "multi_hop", "adversarial", "single_hop", "open_domain"]:
        if cat in stats["category_accuracy"]:
            cat_stats = stats["category_accuracy"][cat]
            print(f"  {cat:15s}: {cat_stats['mean']:5.2f}% (+/- {cat_stats['std']:.2f}%)")

    print(f"\n{'='*50}")
    print("LINKEDIN SUMMARY")
    print(f"{'='*50}")

    temporal = stats["category_accuracy"].get("temporal", {})
    adversarial = stats["category_accuracy"].get("adversarial", {})
    multi_hop = stats["category_accuracy"].get("multi_hop", {})

    print(f"""
MemMachine achieves {overall['mean']:.1f}% (+/-{overall['std']:.1f}%) on LoCoMo benchmark

Key Results (n={stats['num_runs']} runs, {stats['total_questions_per_run']} questions each):
- Temporal Reasoning: {temporal.get('mean', 0):.1f}% (+/-{temporal.get('std', 0):.1f}%)
- Multi-hop Reasoning: {multi_hop.get('mean', 0):.1f}% (+/-{multi_hop.get('std', 0):.1f}%)
- Adversarial Robustness: {adversarial.get('mean', 0):.1f}% (+/-{adversarial.get('std', 0):.1f}%)

Architecture: Neural Graph + Theta-Gamma Phase Coupling
Memory Model: Hippocampal-inspired pattern completion
Hardware: Runs on 8GB RAM (local Ollama)
""")


def main():
    print("="*70)
    print("MEMMACHINE FULL BENCHMARK - 10 COMPLETE RUNS")
    print("="*70)
    print(f"Starting at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Each run: 10 conversations, ~1986 questions")
    print(f"Total questions to evaluate: ~19,860")
    print()

    RESULTS_DIR.mkdir(exist_ok=True)

    runs = []
    total_start = time.time()

    for i in range(1, NUM_RUNS + 1):
        run_result = run_single_benchmark(i)
        runs.append(run_result)

        if run_result:
            print(f"\nRun {i} result: {run_result['accuracy']*100:.2f}%")
            print(f"  Temporal: {run_result['category_accuracy'].get('temporal', 0)*100:.2f}%")

        # Save intermediate results
        intermediate_file = RESULTS_DIR / f"benchmark_10x_intermediate.json"
        with open(intermediate_file, 'w') as f:
            json.dump({
                "completed_runs": i,
                "total_runs": NUM_RUNS,
                "runs": runs,
            }, f, indent=2)

        print(f"\nProgress: {i}/{NUM_RUNS} runs completed")

    total_elapsed = time.time() - total_start

    # Aggregate and print final results
    stats = aggregate_results(runs)
    stats["total_elapsed_hours"] = total_elapsed / 3600

    print_summary(stats)

    # Save final results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_file = RESULTS_DIR / f"locomo10_FINAL_10RUNS_{timestamp}.json"
    with open(final_file, 'w') as f:
        json.dump(stats, f, indent=2)

    print(f"\nFinal results saved to: {final_file}")
    print(f"Total time: {total_elapsed/3600:.1f} hours")


if __name__ == "__main__":
    main()
