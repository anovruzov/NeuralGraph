"""
Metrics Collector for the Cognitive Pipeline Benchmark.

Provides comprehensive tracking of all cognitive module metrics during
ingestion and query operations.
"""

from dataclasses import dataclass, field
from typing import Any
import time
import statistics


@dataclass
class IngestMetrics:
    """Metrics captured during a single episode ingestion."""
    # Privacy metrics
    pii_detected: bool = False
    pii_types: list[str] = field(default_factory=list)
    was_blocked: bool = False
    sanitization_time_ms: float = 0.0

    # Emotional metrics
    emotional_vector: dict[str, float] | None = None
    dominant_emotion: str | None = None
    emotion_confidence: float = 0.0

    # Domain metrics
    domain: str | None = None
    domain_confidence: float = 0.0

    # Knowledge graph metrics
    entities_extracted: int = 0
    relations_extracted: int = 0
    entity_types: list[str] = field(default_factory=list)

    # MTM metrics
    segment_uid: str | None = None
    segment_heat: float = 0.0

    # Sharded memory metrics
    shard_id: str | None = None

    # Timing
    total_processing_time_ms: float = 0.0


@dataclass
class QueryMetrics:
    """Metrics captured during a single query."""
    # Granularity routing
    entropy_score: float = 0.0
    granularity_level: str = "medium"
    routing_time_ms: float = 0.0

    # Domain filtering
    domains_searched: list[str] = field(default_factory=list)

    # Mood state
    current_mood: dict[str, float] | None = None
    resonance_boost: float = 0.0

    # Knowledge graph reasoning
    kg_paths_found: int = 0
    kg_entities_involved: int = 0
    kg_reasoning_time_ms: float = 0.0

    # Retrieval
    stm_results: int = 0
    ltm_results: int = 0
    mtm_results: int = 0

    # Sharded memory metrics
    sharded_results: int = 0
    shards_accessed: int = 0
    graph_traversal_time_ms: float = 0.0

    # Timing
    total_query_time_ms: float = 0.0


@dataclass
class CognitiveMetrics:
    """Aggregate metrics across all operations."""
    # Privacy Metrics
    pii_detections: int = 0
    pii_by_type: dict[str, int] = field(default_factory=dict)
    episodes_blocked: int = 0
    sanitization_times_ms: list[float] = field(default_factory=list)

    # Emotional Metrics
    emotional_vectors: list[dict[str, float]] = field(default_factory=list)
    mood_updates: int = 0
    resonance_adjustments: list[float] = field(default_factory=list)
    dominant_emotions: dict[str, int] = field(default_factory=dict)

    # MTM Metrics
    segments_created: int = 0
    segments_promoted: int = 0
    segments_evicted: int = 0
    heat_scores: list[float] = field(default_factory=list)

    # Knowledge Graph Metrics
    entities_extracted: int = 0
    relations_extracted: int = 0
    entity_types: dict[str, int] = field(default_factory=dict)
    reasoning_paths: int = 0

    # Granularity Metrics
    routing_decisions: dict[str, int] = field(default_factory=dict)
    entropy_scores: list[float] = field(default_factory=list)

    # Domain Metrics
    domain_classifications: dict[str, int] = field(default_factory=dict)
    domain_confidences: list[float] = field(default_factory=list)

    # Overall Metrics
    total_episodes: int = 0
    total_queries: int = 0
    ingestion_times_ms: list[float] = field(default_factory=list)
    query_times_ms: list[float] = field(default_factory=list)

    def get_summary(self) -> dict[str, Any]:
        """Generate summary statistics."""
        summary = {
            "total_episodes": self.total_episodes,
            "total_queries": self.total_queries,
            "privacy": {
                "pii_detection_rate": (
                    self.pii_detections / self.total_episodes * 100
                    if self.total_episodes > 0 else 0
                ),
                "blocked_rate": (
                    self.episodes_blocked / self.total_episodes * 100
                    if self.total_episodes > 0 else 0
                ),
                "pii_by_type": self.pii_by_type,
                "avg_sanitization_ms": (
                    statistics.mean(self.sanitization_times_ms)
                    if self.sanitization_times_ms else 0
                ),
            },
            "emotional": {
                "dominant_emotions": self.dominant_emotions,
                "mood_updates": self.mood_updates,
                "avg_resonance_adjustment": (
                    statistics.mean(self.resonance_adjustments)
                    if self.resonance_adjustments else 0
                ),
            },
            "mtm": {
                "segments_created": self.segments_created,
                "segments_promoted": self.segments_promoted,
                "segments_evicted": self.segments_evicted,
                "avg_heat_score": (
                    statistics.mean(self.heat_scores)
                    if self.heat_scores else 0
                ),
            },
            "knowledge_graph": {
                "entities_extracted": self.entities_extracted,
                "relations_extracted": self.relations_extracted,
                "entity_types": self.entity_types,
                "reasoning_paths": self.reasoning_paths,
            },
            "granularity": {
                "routing_decisions": self.routing_decisions,
                "avg_entropy": (
                    statistics.mean(self.entropy_scores)
                    if self.entropy_scores else 0
                ),
            },
            "domain": {
                "classifications": self.domain_classifications,
                "avg_confidence": (
                    statistics.mean(self.domain_confidences)
                    if self.domain_confidences else 0
                ),
            },
            "performance": {
                "avg_ingestion_ms": (
                    statistics.mean(self.ingestion_times_ms)
                    if self.ingestion_times_ms else 0
                ),
                "avg_query_ms": (
                    statistics.mean(self.query_times_ms)
                    if self.query_times_ms else 0
                ),
                "p95_ingestion_ms": (
                    sorted(self.ingestion_times_ms)[int(len(self.ingestion_times_ms) * 0.95)]
                    if len(self.ingestion_times_ms) > 20 else 0
                ),
                "p95_query_ms": (
                    sorted(self.query_times_ms)[int(len(self.query_times_ms) * 0.95)]
                    if len(self.query_times_ms) > 20 else 0
                ),
            },
        }
        return summary


class MetricsCollector:
    """Collects and aggregates metrics from cognitive pipeline operations."""

    def __init__(self):
        self.metrics = CognitiveMetrics()
        self._start_time: float | None = None

    def start_timer(self) -> float:
        """Start a timer and return the start time."""
        return time.perf_counter() * 1000  # Convert to ms

    def elapsed_ms(self, start_time: float) -> float:
        """Calculate elapsed time in milliseconds."""
        return (time.perf_counter() * 1000) - start_time

    def record_ingestion(self, ingest_metrics: IngestMetrics) -> None:
        """Record metrics from an ingestion operation."""
        self.metrics.total_episodes += 1
        self.metrics.ingestion_times_ms.append(ingest_metrics.total_processing_time_ms)

        # Privacy
        if ingest_metrics.pii_detected:
            self.metrics.pii_detections += 1
        if ingest_metrics.was_blocked:
            self.metrics.episodes_blocked += 1
        for pii_type in ingest_metrics.pii_types:
            self.metrics.pii_by_type[pii_type] = (
                self.metrics.pii_by_type.get(pii_type, 0) + 1
            )
        if ingest_metrics.sanitization_time_ms > 0:
            self.metrics.sanitization_times_ms.append(ingest_metrics.sanitization_time_ms)

        # Emotional
        if ingest_metrics.emotional_vector:
            self.metrics.emotional_vectors.append(ingest_metrics.emotional_vector)
        if ingest_metrics.dominant_emotion:
            self.metrics.dominant_emotions[ingest_metrics.dominant_emotion] = (
                self.metrics.dominant_emotions.get(ingest_metrics.dominant_emotion, 0) + 1
            )

        # Domain
        if ingest_metrics.domain:
            self.metrics.domain_classifications[ingest_metrics.domain] = (
                self.metrics.domain_classifications.get(ingest_metrics.domain, 0) + 1
            )
            self.metrics.domain_confidences.append(ingest_metrics.domain_confidence)

        # Knowledge graph
        self.metrics.entities_extracted += ingest_metrics.entities_extracted
        self.metrics.relations_extracted += ingest_metrics.relations_extracted
        for entity_type in ingest_metrics.entity_types:
            self.metrics.entity_types[entity_type] = (
                self.metrics.entity_types.get(entity_type, 0) + 1
            )

        # MTM
        if ingest_metrics.segment_uid:
            self.metrics.segments_created += 1
            self.metrics.heat_scores.append(ingest_metrics.segment_heat)

    def record_query(self, query_metrics: QueryMetrics) -> None:
        """Record metrics from a query operation."""
        self.metrics.total_queries += 1
        self.metrics.query_times_ms.append(query_metrics.total_query_time_ms)

        # Granularity
        if query_metrics.granularity_level:
            self.metrics.routing_decisions[query_metrics.granularity_level] = (
                self.metrics.routing_decisions.get(query_metrics.granularity_level, 0) + 1
            )
            self.metrics.entropy_scores.append(query_metrics.entropy_score)

        # Mood
        if query_metrics.resonance_boost != 0:
            self.metrics.resonance_adjustments.append(query_metrics.resonance_boost)
            self.metrics.mood_updates += 1

        # Knowledge graph
        self.metrics.reasoning_paths += query_metrics.kg_paths_found

    def get_metrics(self) -> CognitiveMetrics:
        """Get the aggregated metrics."""
        return self.metrics

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics."""
        return self.metrics.get_summary()

    def format_report(self) -> str:
        """Format metrics as a human-readable report."""
        summary = self.get_summary()
        lines = [
            "\n=== Pipeline Statistics ===",
            f"  Total Episodes: {summary['total_episodes']}",
            f"  Total Queries: {summary['total_queries']}",
            "",
            "  Privacy:",
            f"    PII Detection Rate: {summary['privacy']['pii_detection_rate']:.1f}%",
            f"    Blocked Rate: {summary['privacy']['blocked_rate']:.1f}%",
            f"    PII by Type: {summary['privacy']['pii_by_type']}",
            "",
            "  Emotional:",
            f"    Dominant Emotions: {summary['emotional']['dominant_emotions']}",
            f"    Mood Updates: {summary['emotional']['mood_updates']}",
            "",
            "  Mid-term Memory:",
            f"    Segments Created: {summary['mtm']['segments_created']}",
            f"    Segments Promoted: {summary['mtm']['segments_promoted']}",
            f"    Avg Heat Score: {summary['mtm']['avg_heat_score']:.2f}",
            "",
            "  Knowledge Graph:",
            f"    Entities Extracted: {summary['knowledge_graph']['entities_extracted']}",
            f"    Relations Extracted: {summary['knowledge_graph']['relations_extracted']}",
            f"    Reasoning Paths: {summary['knowledge_graph']['reasoning_paths']}",
            "",
            "  Granularity:",
            f"    Routing Decisions: {summary['granularity']['routing_decisions']}",
            f"    Avg Entropy: {summary['granularity']['avg_entropy']:.3f}",
            "",
            "  Domain:",
            f"    Classifications: {summary['domain']['classifications']}",
            f"    Avg Confidence: {summary['domain']['avg_confidence']:.3f}",
            "",
            "  Performance:",
            f"    Avg Ingestion: {summary['performance']['avg_ingestion_ms']:.1f}ms",
            f"    Avg Query: {summary['performance']['avg_query_ms']:.1f}ms",
            f"    P95 Ingestion: {summary['performance']['p95_ingestion_ms']:.1f}ms",
            f"    P95 Query: {summary['performance']['p95_query_ms']:.1f}ms",
        ]
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all collected metrics."""
        self.metrics = CognitiveMetrics()
