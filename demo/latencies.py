"""
Latency tracking module for NeuralGraph benchmark.

Tracks timing for:
- Embedding generation
- Memory retrieval (Tesseract)
- Reranking (Qwen)
- Answer generation
- Judge evaluation
- Total end-to-end per question
"""

import time
import json
import statistics
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import defaultdict


@dataclass
class LatencyRecord:
    """Single latency measurement."""
    question_id: int
    category: str
    embedding_ms: float = 0.0
    retrieval_ms: float = 0.0
    rerank_ms: float = 0.0
    answer_gen_ms: float = 0.0
    judge_ms: float = 0.0
    total_ms: float = 0.0


class LatencyTracker:
    """Tracks and aggregates latency metrics for benchmark runs."""

    def __init__(self):
        self.records: list[LatencyRecord] = []
        self.current: dict = {}
        self._start_times: dict = {}

    def start(self, phase: str):
        """Start timing a phase."""
        self._start_times[phase] = time.perf_counter()

    def stop(self, phase: str) -> float:
        """Stop timing and return elapsed ms."""
        if phase not in self._start_times:
            return 0.0
        elapsed = (time.perf_counter() - self._start_times[phase]) * 1000
        self.current[phase] = elapsed
        del self._start_times[phase]
        return elapsed

    def record_question(self, question_id: int, category: str):
        """Finalize current timings into a record."""
        total = sum([
            self.current.get("embedding", 0),
            self.current.get("retrieval", 0),
            self.current.get("rerank", 0),
            self.current.get("answer_gen", 0),
            self.current.get("judge", 0),
        ])

        record = LatencyRecord(
            question_id=question_id,
            category=category,
            embedding_ms=self.current.get("embedding", 0),
            retrieval_ms=self.current.get("retrieval", 0),
            rerank_ms=self.current.get("rerank", 0),
            answer_gen_ms=self.current.get("answer_gen", 0),
            judge_ms=self.current.get("judge", 0),
            total_ms=total,
        )
        self.records.append(record)
        self.current = {}
        return record

    def get_stats(self, metric: str, category: Optional[str] = None) -> dict:
        """Get statistics for a specific metric."""
        if category:
            values = [getattr(r, metric) for r in self.records if r.category == category]
        else:
            values = [getattr(r, metric) for r in self.records]

        if not values:
            return {"count": 0, "mean": 0, "median": 0, "p95": 0, "p99": 0, "min": 0, "max": 0}

        values_sorted = sorted(values)
        n = len(values)

        return {
            "count": n,
            "mean": round(statistics.mean(values), 2),
            "median": round(statistics.median(values), 2),
            "p95": round(values_sorted[int(n * 0.95)] if n > 1 else values[0], 2),
            "p99": round(values_sorted[int(n * 0.99)] if n > 1 else values[0], 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "std": round(statistics.stdev(values), 2) if n > 1 else 0,
        }

    def get_summary(self) -> dict:
        """Get complete latency summary."""
        metrics = ["embedding_ms", "retrieval_ms", "rerank_ms", "answer_gen_ms", "judge_ms", "total_ms"]
        categories = list(set(r.category for r in self.records))

        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_questions": len(self.records),
            "overall": {},
            "by_category": {},
            "by_phase": {},
        }

        # Overall stats per metric
        for metric in metrics:
            summary["overall"][metric] = self.get_stats(metric)

        # Stats by category
        for cat in categories:
            summary["by_category"][cat] = {
                metric: self.get_stats(metric, cat) for metric in metrics
            }

        # Phase breakdown (what takes the most time)
        phase_totals = {
            "embedding": sum(r.embedding_ms for r in self.records),
            "retrieval": sum(r.retrieval_ms for r in self.records),
            "rerank": sum(r.rerank_ms for r in self.records),
            "answer_gen": sum(r.answer_gen_ms for r in self.records),
            "judge": sum(r.judge_ms for r in self.records),
        }
        total_time = sum(phase_totals.values())

        summary["by_phase"] = {
            phase: {
                "total_ms": round(ms, 2),
                "percentage": round(100 * ms / total_time, 1) if total_time > 0 else 0
            }
            for phase, ms in phase_totals.items()
        }

        return summary

    def save(self, path: Optional[Path] = None):
        """Save latency data to JSON."""
        if path is None:
            path = Path(__file__).parent / "latencies.json"

        output = {
            "summary": self.get_summary(),
            "records": [asdict(r) for r in self.records],
        }

        with open(path, "w") as f:
            json.dump(output, f, indent=2)

        return path

    def print_summary(self):
        """Print formatted latency summary to console."""
        summary = self.get_summary()

        print(f"\n{'='*70}")
        print(f"LATENCY METRICS ({summary['total_questions']} questions)")
        print(f"{'='*70}")

        # Overall metrics
        print(f"\n{'Phase':<15} {'Mean':>10} {'Median':>10} {'P95':>10} {'P99':>10} {'Max':>10}")
        print(f"{'-'*70}")

        phase_names = {
            "embedding_ms": "Embedding",
            "retrieval_ms": "Retrieval",
            "rerank_ms": "Rerank",
            "answer_gen_ms": "Answer Gen",
            "judge_ms": "Judge",
            "total_ms": "TOTAL",
        }

        for metric, name in phase_names.items():
            stats = summary["overall"][metric]
            print(f"{name:<15} {stats['mean']:>8.1f}ms {stats['median']:>8.1f}ms "
                  f"{stats['p95']:>8.1f}ms {stats['p99']:>8.1f}ms {stats['max']:>8.1f}ms")

        # Phase breakdown
        print(f"\n{'Phase':<15} {'Total Time':>15} {'% of Total':>12}")
        print(f"{'-'*45}")
        for phase, data in summary["by_phase"].items():
            total_sec = data["total_ms"] / 1000
            print(f"{phase.capitalize():<15} {total_sec:>12.1f}s {data['percentage']:>10.1f}%")

        # By category
        print(f"\n{'Category':<15} {'Mean Total':>12} {'P95 Total':>12} {'Count':>8}")
        print(f"{'-'*50}")
        for cat, metrics in summary["by_category"].items():
            stats = metrics["total_ms"]
            print(f"{cat:<15} {stats['mean']:>10.1f}ms {stats['p95']:>10.1f}ms {stats['count']:>8}")

        print(f"{'='*70}\n")


# Global tracker instance for easy import
LATENCY_TRACKER = LatencyTracker()


def track_latency(phase: str):
    """Decorator to track latency of async functions."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            LATENCY_TRACKER.start(phase)
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                LATENCY_TRACKER.stop(phase)
        return wrapper
    return decorator


# Convenience functions for direct use
def start_timer(phase: str):
    """Start timing a phase."""
    LATENCY_TRACKER.start(phase)

def stop_timer(phase: str) -> float:
    """Stop timing and return elapsed ms."""
    return LATENCY_TRACKER.stop(phase)

def record_question(question_id: int, category: str):
    """Record latencies for completed question."""
    return LATENCY_TRACKER.record_question(question_id, category)

def save_latencies(path: Optional[Path] = None):
    """Save latency data to JSON."""
    return LATENCY_TRACKER.save(path)

def print_latency_summary():
    """Print formatted latency summary."""
    LATENCY_TRACKER.print_summary()

def get_latency_summary() -> dict:
    """Get latency summary as dict."""
    return LATENCY_TRACKER.get_summary()


if __name__ == "__main__":
    # Demo/test
    import asyncio
    import random

    async def demo():
        tracker = LatencyTracker()

        for i in range(20):
            cat = random.choice(["single_hop", "temporal", "multi_hop", "open_domain"])

            tracker.start("embedding")
            await asyncio.sleep(random.uniform(0.01, 0.05))
            tracker.stop("embedding")

            tracker.start("retrieval")
            await asyncio.sleep(random.uniform(0.02, 0.1))
            tracker.stop("retrieval")

            tracker.start("rerank")
            await asyncio.sleep(random.uniform(0.1, 0.3))
            tracker.stop("rerank")

            tracker.start("answer_gen")
            await asyncio.sleep(random.uniform(0.2, 0.5))
            tracker.stop("answer_gen")

            tracker.start("judge")
            await asyncio.sleep(random.uniform(0.1, 0.2))
            tracker.stop("judge")

            tracker.record_question(i + 1, cat)

        tracker.print_summary()
        path = tracker.save()
        print(f"Saved to: {path}")

    asyncio.run(demo())
