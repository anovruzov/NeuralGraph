"""Attribution logging for retrieval pipeline performance tracking.

Tracks per-stage timing, scoring, and candidate flow to enable:
1. A/B testing of improvements
2. Regression detection
3. Bottleneck identification
4. Per-component accuracy attribution

ENHANCED: Now captures detailed score breakdowns per candidate:
- base_score, semantic_score, entity_score, keyword_score
- temporal_score, speaker_boost, specificity_bonus
- rerank_delta (score change from reranking)
- Evidence snippets used in final answer

Output: JSONL format for easy analysis with pandas/jq.

Usage:
    attr = RetrievalAttribution(session_key, query_text)
    attr.log_stage("vector_search", candidates=50, top_score=0.85, latency_ms=12.3)
    attr.log_stage("entity_filter", candidates=45, top_score=0.85, latency_ms=2.1)
    attr.log_stage("rerank", candidates=10, top_score=0.92, latency_ms=45.0)
    attr.log_evidence(nodes, answer)  # Track evidence used
    attr.finalize(final_candidates=10, answer_correct=True)

    # Get attribution summary
    summary = attr.get_summary()
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScoreBreakdown:
    """Detailed score breakdown for a single candidate."""
    node_id: str
    content_preview: str  # First 100 chars
    speaker: str
    rank: int

    # Score components
    base_score: float = 0.0
    semantic_score: float = 0.0
    entity_score: float = 0.0
    keyword_score: float = 0.0
    temporal_score: float = 0.0
    speaker_boost: float = 0.0
    specificity_bonus: float = 0.0
    rerank_delta: float = 0.0
    final_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id[:12],  # Truncate for readability
            "content": self.content_preview,
            "speaker": self.speaker,
            "rank": self.rank,
            "scores": {
                "base": round(self.base_score, 4),
                "semantic": round(self.semantic_score, 4),
                "entity": round(self.entity_score, 4),
                "keyword": round(self.keyword_score, 4),
                "temporal": round(self.temporal_score, 4),
                "speaker_boost": round(self.speaker_boost, 4),
                "specificity": round(self.specificity_bonus, 4),
                "rerank_delta": round(self.rerank_delta, 4),
                "final": round(self.final_score, 4),
            }
        }


@dataclass
class EvidenceSnippet:
    """An evidence snippet used in answer generation."""
    node_id: str
    speaker: str
    content: str
    score: float
    used_in_answer: bool = False
    quoted_text: str = ""

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id[:12],
            "speaker": self.speaker,
            "content": self.content[:200],
            "score": round(self.score, 4),
            "used": self.used_in_answer,
            "quoted": self.quoted_text[:100] if self.quoted_text else "",
        }


@dataclass
class StageMetrics:
    """Metrics for a single retrieval stage."""
    stage_name: str
    candidates_in: int
    candidates_out: int
    top_score: float
    latency_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # NEW: Detailed score breakdowns for top 50 candidates
    top_50_breakdowns: list[ScoreBreakdown] = field(default_factory=list)


@dataclass
class RetrievalAttribution:
    """Tracks attribution across retrieval pipeline stages.

    Enables per-stage performance analysis for:
    - FlashRetriever (parallel resonance)
    - NeuralRetriever (sequential stages)
    - Query router filtering
    - Reranking
    - Dialogue expansion
    """

    session_key: str
    query_text: str
    query_type: str = "unknown"

    # Stage tracking
    stages: list[StageMetrics] = field(default_factory=list)

    # Timing
    start_time: float = field(default_factory=time.perf_counter)
    end_time: float | None = None

    # Result tracking
    final_candidates: int = 0
    gold_in_top_1: bool = False
    gold_in_top_5: bool = False
    gold_in_top_10: bool = False
    gold_rank: int = -1  # -1 if not found
    answer_correct: bool = False

    # Router attribution
    router_strategy: str = "semantic"
    router_confidence: float = 0.5
    entities_detected: list[str] = field(default_factory=list)
    temporal_markers: list[str] = field(default_factory=list)

    # NEW: Evidence tracking
    evidence_snippets: list[EvidenceSnippet] = field(default_factory=list)
    pre_rerank_order: list[str] = field(default_factory=list)  # node_ids
    post_rerank_order: list[str] = field(default_factory=list)
    rerank_deltas: dict[str, float] = field(default_factory=dict)

    # NEW: Answer tracking
    generated_answer: str = ""
    gold_answer: str = ""
    gate_profile: str = "default"
    is_list_question: bool = False

    def log_stage(
        self,
        stage_name: str,
        candidates_in: int = 0,
        candidates_out: int = 0,
        top_score: float = 0.0,
        latency_ms: float = 0.0,
        breakdowns: list[ScoreBreakdown] | None = None,
        **metadata
    ) -> None:
        """Log metrics for a retrieval stage.

        Args:
            stage_name: Name of stage (e.g., "vector_search", "entity_filter", "rerank")
            candidates_in: Number of candidates entering stage
            candidates_out: Number of candidates after stage
            top_score: Highest score in output
            latency_ms: Stage latency in milliseconds
            breakdowns: Optional detailed score breakdowns for top candidates
            **metadata: Additional stage-specific metadata
        """
        stage = StageMetrics(
            stage_name=stage_name,
            candidates_in=candidates_in,
            candidates_out=candidates_out,
            top_score=top_score,
            latency_ms=latency_ms,
            metadata=metadata,
            top_50_breakdowns=breakdowns[:50] if breakdowns else []
        )
        self.stages.append(stage)

        logger.debug(
            f"[Attribution] {stage_name}: {candidates_in} -> {candidates_out} "
            f"(top={top_score:.3f}, {latency_ms:.1f}ms)"
        )

    def log_evidence(
        self,
        nodes: list[tuple],  # List of (NeuralNode, score)
        answer: str = ""
    ) -> None:
        """Log evidence snippets used in answer generation.

        Args:
            nodes: List of (node, score) tuples used as context
            answer: Generated answer to check for quote usage
        """
        answer_lower = answer.lower() if answer else ""

        for node, score in nodes[:20]:
            speaker = ""
            if hasattr(node, 'metadata') and node.metadata:
                speaker = node.metadata.get("producer_id", node.metadata.get("speaker", ""))

            # Detect if content was quoted in answer
            content_lower = node.content.lower() if hasattr(node, 'content') else ""
            # Check for 3+ consecutive word overlap
            content_words = content_lower.split()[:15]
            used = False
            quoted = ""
            for i in range(len(content_words) - 2):
                phrase = " ".join(content_words[i:i+3])
                if phrase in answer_lower:
                    used = True
                    quoted = phrase
                    break

            snippet = EvidenceSnippet(
                node_id=node.node_id if hasattr(node, 'node_id') else str(id(node)),
                speaker=speaker,
                content=node.content[:200] if hasattr(node, 'content') else str(node)[:200],
                score=score,
                used_in_answer=used,
                quoted_text=quoted,
            )
            self.evidence_snippets.append(snippet)

    def log_rerank(
        self,
        before: list[tuple],  # (node, score)
        after: list[tuple]    # (node, score)
    ) -> None:
        """Log reranking effect on candidate order and scores.

        Args:
            before: Candidates before reranking
            after: Candidates after reranking
        """
        self.pre_rerank_order = [n.node_id for n, _ in before[:50]]
        self.post_rerank_order = [n.node_id for n, _ in after[:50]]

        before_scores = {n.node_id: s for n, s in before}
        for node, after_score in after[:50]:
            before_score = before_scores.get(node.node_id, 0.0)
            delta = after_score - before_score
            if abs(delta) > 0.01:
                self.rerank_deltas[node.node_id[:12]] = round(delta, 4)

    def log_router(
        self,
        strategy: str,
        confidence: float,
        entities: list[str],
        temporal_markers: list[str],
        query_type: str
    ) -> None:
        """Log query router attribution.

        Args:
            strategy: Chosen strategy (semantic, entity_filter, temporal_filter, etc.)
            confidence: Router confidence in classification
            entities: Detected entities
            temporal_markers: Detected temporal markers
            query_type: Classified query type
        """
        self.router_strategy = strategy
        self.router_confidence = confidence
        self.entities_detected = entities
        self.temporal_markers = temporal_markers
        self.query_type = query_type

        logger.debug(
            f"[Attribution] Router: strategy={strategy}, type={query_type}, "
            f"confidence={confidence:.2f}, entities={entities}"
        )

    def log_gold_position(
        self,
        gold_answer: str,
        retrieved_texts: list[str],
        top_k: int = 10
    ) -> None:
        """Check and log where gold answer appears in retrieved results.

        Args:
            gold_answer: The correct answer
            retrieved_texts: List of retrieved text content (in rank order)
            top_k: How many to check
        """
        if not gold_answer:
            return

        gold_lower = gold_answer.lower().strip()
        gold_parts = [p.strip() for p in gold_lower.replace(",", "|").replace(" and ", "|").split("|")]
        gold_parts = [p for p in gold_parts if len(p) > 2]

        for rank, text in enumerate(retrieved_texts[:top_k], 1):
            text_lower = text.lower()
            found = gold_lower in text_lower or any(p in text_lower for p in gold_parts)

            if found:
                self.gold_rank = rank
                self.gold_in_top_1 = rank == 1
                self.gold_in_top_5 = rank <= 5
                self.gold_in_top_10 = rank <= 10
                break

    def finalize(
        self,
        final_candidates: int = 0,
        answer_correct: bool = False
    ) -> None:
        """Finalize attribution tracking.

        Args:
            final_candidates: Final number of candidates returned
            answer_correct: Whether the generated answer was correct
        """
        self.end_time = time.perf_counter()
        self.final_candidates = final_candidates
        self.answer_correct = answer_correct

    @property
    def total_latency_ms(self) -> float:
        """Total pipeline latency in milliseconds."""
        if self.end_time is None:
            return (time.perf_counter() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    @property
    def stage_latencies(self) -> dict[str, float]:
        """Dictionary of stage name to latency."""
        return {s.stage_name: s.latency_ms for s in self.stages}

    def get_summary(self) -> dict[str, Any]:
        """Get complete attribution summary.

        Returns:
            Dictionary with all attribution data
        """
        return {
            "session_key": self.session_key,
            "query_text": self.query_text[:100],
            "query_type": self.query_type,
            "gate_profile": self.gate_profile,
            "is_list_question": self.is_list_question,

            # Router attribution
            "router": {
                "strategy": self.router_strategy,
                "confidence": self.router_confidence,
                "entities": self.entities_detected,
                "temporal_markers": self.temporal_markers,
            },

            # Stage metrics with score breakdowns
            "stages": [
                {
                    "name": s.stage_name,
                    "candidates_in": s.candidates_in,
                    "candidates_out": s.candidates_out,
                    "top_score": s.top_score,
                    "latency_ms": s.latency_ms,
                    "metadata": s.metadata,
                    "top_50": [b.to_dict() for b in s.top_50_breakdowns[:50]],
                }
                for s in self.stages
            ],

            # Reranking attribution
            "rerank": {
                "pre_order": self.pre_rerank_order[:20],
                "post_order": self.post_rerank_order[:20],
                "score_deltas": self.rerank_deltas,
            },

            # Evidence attribution
            "evidence": [e.to_dict() for e in self.evidence_snippets],

            # Timing
            "total_latency_ms": self.total_latency_ms,
            "stage_latencies": self.stage_latencies,

            # Results
            "final_candidates": self.final_candidates,
            "gold_rank": self.gold_rank,
            "gold_in_top_1": self.gold_in_top_1,
            "gold_in_top_5": self.gold_in_top_5,
            "gold_in_top_10": self.gold_in_top_10,
            "answer_correct": self.answer_correct,
            "generated_answer": self.generated_answer[:200],
            "gold_answer": self.gold_answer,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.get_summary(), indent=2, default=str)


class AttributionLogger:
    """Aggregates attribution data across multiple queries for analysis.

    Tracks:
    - Per-category precision@k
    - Per-stage latency distributions
    - Router strategy effectiveness
    - Reranker gain

    Output: JSONL format - one JSON object per line for easy streaming analysis.
    """

    def __init__(
        self,
        log_path: Path | str | None = None,
        enabled: bool = True,
        buffer_size: int = 10
    ):
        """Initialize attribution logger.

        Args:
            log_path: Optional path to save attribution logs (JSONL format)
            enabled: Whether logging is enabled
            buffer_size: Number of records to buffer before flushing
        """
        self.attributions: list[RetrievalAttribution] = []
        self.log_path = Path(log_path) if log_path else None
        self.enabled = enabled
        self._buffer_size = buffer_size
        self._buffer: list[dict] = []

        # Ensure output directory exists
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Aggregated metrics
        self._by_category: dict[str, list[RetrievalAttribution]] = {}
        self._by_strategy: dict[str, list[RetrievalAttribution]] = {}

    def add(self, attribution: RetrievalAttribution) -> None:
        """Add an attribution record.

        Args:
            attribution: Completed attribution record
        """
        if not self.enabled:
            return

        self.attributions.append(attribution)

        # Index by category
        cat = attribution.query_type
        if cat not in self._by_category:
            self._by_category[cat] = []
        self._by_category[cat].append(attribution)

        # Index by strategy
        strat = attribution.router_strategy
        if strat not in self._by_strategy:
            self._by_strategy[strat] = []
        self._by_strategy[strat].append(attribution)

        # Buffer for batch writing
        if self.log_path:
            self._buffer.append(attribution.get_summary())
            if len(self._buffer) >= self._buffer_size:
                self.flush()

    def flush(self) -> None:
        """Flush buffered attributions to JSONL file."""
        if not self._buffer or not self.log_path:
            return

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                for record in self._buffer:
                    # Write one compact JSON per line (JSONL format)
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            logger.debug(f"Flushed {len(self._buffer)} attribution records to {self.log_path}")
        except Exception as e:
            logger.warning(f"Failed to write attribution log: {e}")
        finally:
            self._buffer.clear()

    def close(self) -> None:
        """Close logger and flush remaining buffer."""
        self.flush()

    def _append_to_log(self, attribution: RetrievalAttribution) -> None:
        """Append single attribution to log file (deprecated, use flush())."""
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(attribution.get_summary(), ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write attribution log: {e}")

    def get_precision_at_k(self, k: int = 1) -> dict[str, float]:
        """Get precision@k by category.

        Args:
            k: Position threshold (1, 5, or 10)

        Returns:
            Dictionary of category -> precision@k
        """
        results = {}

        for category, attrs in self._by_category.items():
            if not attrs:
                continue

            if k == 1:
                hits = sum(1 for a in attrs if a.gold_in_top_1)
            elif k == 5:
                hits = sum(1 for a in attrs if a.gold_in_top_5)
            else:
                hits = sum(1 for a in attrs if a.gold_in_top_10)

            results[category] = hits / len(attrs) if attrs else 0.0

        return results

    def get_latency_stats(self) -> dict[str, dict[str, float]]:
        """Get latency statistics by stage.

        Returns:
            Dictionary of stage -> {mean, p50, p95, p99}
        """
        # Collect latencies by stage
        stage_latencies: dict[str, list[float]] = {}

        for attr in self.attributions:
            for stage in attr.stages:
                if stage.stage_name not in stage_latencies:
                    stage_latencies[stage.stage_name] = []
                stage_latencies[stage.stage_name].append(stage.latency_ms)

        # Compute statistics
        results = {}
        for stage, latencies in stage_latencies.items():
            if not latencies:
                continue

            sorted_lat = sorted(latencies)
            n = len(sorted_lat)

            results[stage] = {
                "mean": sum(latencies) / n,
                "p50": sorted_lat[n // 2],
                "p95": sorted_lat[int(n * 0.95)] if n >= 20 else sorted_lat[-1],
                "p99": sorted_lat[int(n * 0.99)] if n >= 100 else sorted_lat[-1],
                "count": n,
            }

        return results

    def get_strategy_effectiveness(self) -> dict[str, dict[str, float]]:
        """Get effectiveness metrics by router strategy.

        Returns:
            Dictionary of strategy -> {accuracy, precision_at_1, avg_latency}
        """
        results = {}

        for strategy, attrs in self._by_strategy.items():
            if not attrs:
                continue

            correct = sum(1 for a in attrs if a.answer_correct)
            top1 = sum(1 for a in attrs if a.gold_in_top_1)

            results[strategy] = {
                "count": len(attrs),
                "accuracy": correct / len(attrs),
                "precision_at_1": top1 / len(attrs),
                "avg_latency_ms": sum(a.total_latency_ms for a in attrs) / len(attrs),
            }

        return results

    def get_rerank_gain(self) -> dict[str, float]:
        """Calculate reranking gain (improvement from reranker).

        Returns:
            Dictionary with rerank metrics
        """
        with_rerank = [a for a in self.attributions if any(s.stage_name == "rerank" for s in a.stages)]

        if not with_rerank:
            return {"rerank_gain": 0.0, "samples": 0}

        # Compare pre-rerank vs post-rerank scores
        total_gain = 0.0

        for attr in with_rerank:
            pre_score = 0.0
            post_score = 0.0

            for stage in attr.stages:
                if stage.stage_name in ("vector_search", "flash_retrieve", "neural_retrieve"):
                    pre_score = stage.top_score
                elif stage.stage_name == "rerank":
                    post_score = stage.top_score

            if pre_score > 0:
                total_gain += (post_score - pre_score) / pre_score

        return {
            "rerank_gain_pct": (total_gain / len(with_rerank)) * 100,
            "samples": len(with_rerank),
        }

    def get_summary(self) -> dict[str, Any]:
        """Get complete attribution summary.

        Returns:
            Comprehensive summary dictionary
        """
        return {
            "total_queries": len(self.attributions),
            "precision_at_1": self.get_precision_at_k(1),
            "precision_at_5": self.get_precision_at_k(5),
            "precision_at_10": self.get_precision_at_k(10),
            "latency_stats": self.get_latency_stats(),
            "strategy_effectiveness": self.get_strategy_effectiveness(),
            "rerank_gain": self.get_rerank_gain(),
            "by_category_count": {k: len(v) for k, v in self._by_category.items()},
            "by_strategy_count": {k: len(v) for k, v in self._by_strategy.items()},
        }


# Global attribution logger for benchmark runs
_global_logger: AttributionLogger | None = None


def get_attribution_logger() -> AttributionLogger:
    """Get or create the global attribution logger."""
    global _global_logger
    if _global_logger is None:
        _global_logger = AttributionLogger()
    return _global_logger


def reset_attribution_logger() -> None:
    """Reset the global attribution logger."""
    global _global_logger
    _global_logger = None


def create_attribution(session_key: str, query_text: str) -> RetrievalAttribution:
    """Create a new attribution tracker.

    Args:
        session_key: Session identifier
        query_text: The query being processed

    Returns:
        New RetrievalAttribution instance
    """
    return RetrievalAttribution(
        session_key=session_key,
        query_text=query_text
    )
