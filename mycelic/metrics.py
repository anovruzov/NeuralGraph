"""Prometheus metrics for a live deployment. One registry per process; ``render()`` serves ``GET /metrics``.

The names answer the operational questions in the deployment brief: ingestion rate, retrieval and
aggregation latency, event throughput, failed events, replay/recovery events, active agents, memory counts by
layer, lineage reconstruction success/failure, downward verification verdicts and reasons.  Gauges that describe
stored state (memories by layer, outbox depth, active agents) are refreshed by ``Metrics.refresh_from_store`` before
every scrape.
"""
from __future__ import annotations

from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from .hierarchy import LAYERS
from .verification import REASONS, VERDICTS

_LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Metrics:
    def __init__(self) -> None:
        r = self.registry = CollectorRegistry()
        self.memories_ingested = Counter("mycelic_memories_ingested_total", "Memories accepted by the API", ["layer"], registry=r)
        self.events_received = Counter("mycelic_events_received_total", "Events accepted by POST /events", registry=r)
        self.events_published = Counter("mycelic_events_published_total", "Events published to the transport", ["kind"], registry=r)
        self.events_applied = Counter("mycelic_events_applied_total", "Events applied by the consumer", ["kind", "result"], registry=r)
        self.events_failed = Counter("mycelic_events_failed_total", "Events that failed to publish or apply", ["stage"], registry=r)
        self.replay_events = Counter("mycelic_replay_events_total", "Events re-applied during a replay", registry=r)
        self.recoveries = Counter("mycelic_recovery_total", "Recovery actions taken", ["kind"], registry=r)
        self.derived = Counter("mycelic_memories_derived_total", "Higher-layer memories derived by aggregation", ["layer", "operator"], registry=r)
        self.events_ignored = Counter("mycelic_events_ignored_total", "Events applied without effect", ["reason"], registry=r)
        self.aggregation_inconsistency = Counter("mycelic_aggregation_inconsistency_total",
                                                 "Derived memories whose stored row disagreed with their recomputation", ["kind"], registry=r)
        self.aggregation_truncated = Counter("mycelic_aggregation_truncated_total", "Aggregation steps cut short at a bound",
                                             ["what"], registry=r)
        self.reaggregation_steps = Counter("mycelic_reaggregation_steps_total", "Reaggregation job steps (one transaction each)",
                                           registry=r)
        self.integrity_backfilled = Counter("mycelic_integrity_backfilled_total", "Memories given a digest by the start-up backfill",
                                            registry=r)
        self.retrieval_latency = Histogram("mycelic_retrieval_latency_seconds", "POST /query latency", buckets=_LATENCY_BUCKETS, registry=r)
        self.aggregation_latency = Histogram("mycelic_aggregation_latency_seconds", "Time to apply one event including aggregation", buckets=_LATENCY_BUCKETS, registry=r)
        self.lineage_latency = Histogram("mycelic_lineage_latency_seconds", "Lineage reconstruction latency", buckets=_LATENCY_BUCKETS, registry=r)
        self.lineage_results = Counter("mycelic_lineage_reconstruction_total", "Lineage reconstructions", ["result"], registry=r)
        self.verifications = Counter("mycelic_verifications_total", "Downward verifications by verdict", ["verdict"], registry=r)
        self.verification_latency = Histogram("mycelic_verification_latency_seconds", "Downward verification latency",
                                              buckets=_LATENCY_BUCKETS, registry=r)
        self.verification_reasons = Counter("mycelic_verification_reasons_total",
                                            "Reason codes found by downward verification, each once per verification", ["reason"],
                                            registry=r)
        self.http_requests = Counter("mycelic_http_requests_total", "HTTP requests", ["route", "status"], registry=r)
        self.auth_failures = Counter("mycelic_auth_failures_total", "Rejected requests", ["reason"], registry=r)
        self.active_agents = Gauge("mycelic_active_agents", "Agents seen in the last 15 minutes", registry=r)
        self.registered_agents = Gauge("mycelic_registered_agents", "Agents registered and not revoked", registry=r)
        self.memories_by_layer = Gauge("mycelic_memories", "Active memories by layer", ["layer"], registry=r)
        self.outbox_depth = Gauge("mycelic_outbox_pending", "Events waiting to be published", registry=r)
        self.transport_connected = Gauge("mycelic_transport_connected", "1 when the transport is connected", registry=r)
        self.consumer_pending = Gauge("mycelic_consumer_pending", "Stream messages not yet delivered to the consumer", registry=r)
        self.info = Gauge("mycelic_build_info", "Build information", ["version"], registry=r)
        for layer in LAYERS:
            self.memories_by_layer.labels(layer).set(0)
        for kind in ("memory.observed", "memory.derived", "memory.retracted", "agent.event"):
            self.events_published.labels(kind)
        for reason in ("derived_not_reproduced", "retraction_target", "unknown_kind"):
            self.events_ignored.labels(reason)
        for kind in ("id_collision", "reactivation_mismatch"):
            self.aggregation_inconsistency.labels(kind)
        for what in ("dependents", "cascade", "candidates"):
            self.aggregation_truncated.labels(what)
        for verdict in VERDICTS:
            self.verifications.labels(verdict)
        for code in REASONS:
            if not code.startswith("hidden_"):          # what a caller sees of hidden nodes is never counted
                self.verification_reasons.labels(code)

    def refresh_from_stats(self, stats: dict[str, Any]) -> None:
        for layer in LAYERS:
            self.memories_by_layer.labels(layer).set(stats.get("memories_by_layer", {}).get(layer, 0))
        self.outbox_depth.set(stats.get("outbox_pending", 0))
        self.active_agents.set(stats.get("active_agents", 0))
        self.registered_agents.set(stats.get("registered_agents", 0))

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
