"""Deterministic coordination, lineage analysis, failure, and repair logic."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from itertools import combinations, product
from typing import Any, Iterable

from .adapters import MemoryNodeAdapter
from .contracts import (
    AuthorizationContext,
    ClaimEnvelope,
    EvidenceExport,
    FragilityMetrics,
    InterventionRecord,
    InterventionTarget,
    PolicyStatus,
    QueryRequest,
    SynthesisResult,
    TraceEvent,
    TraceEventType,
    VerificationRequest,
    VerificationResult,
)


def to_jsonable(value: Any) -> Any:
    """Convert contracts to stable JSON-compatible structures."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"))


class LogicalClock:
    """Seed-derived clock used to make experiment replay byte-stable."""

    def __init__(self, seed: int) -> None:
        self._current = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seed % 86400)

    def now(self) -> str:
        value = self._current.isoformat().replace("+00:00", "Z")
        self._current += timedelta(milliseconds=1)
        return value


class TraceLogger:
    SCHEMA_VERSION = "1.0"

    def __init__(self, run_id: str, clock: LogicalClock) -> None:
        self.run_id = run_id
        self._clock = clock
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def emit(
        self,
        event_type: TraceEventType,
        trace_id: str,
        **details: Any,
    ) -> TraceEvent:
        sequence = len(self._events) + 1
        event = TraceEvent(
            schema_version=self.SCHEMA_VERSION,
            event_id=f"event_{sequence:04d}",
            run_id=self.run_id,
            trace_id=trace_id,
            event_type=event_type,
            sequence=sequence,
            occurred_at=self._clock.now(),
            details=to_jsonable(details),
        )
        self._events.append(event)
        return event


class FailureInjector:
    """Reversible experiment masks; no local memory is deleted or mutated."""

    def __init__(self, run_id: str, seed: int, clock: LogicalClock, traces: TraceLogger) -> None:
        self._run_id = run_id
        self._seed = seed
        self._clock = clock
        self._traces = traces
        self._active: dict[InterventionTarget, set[str]] = {
            target: set() for target in InterventionTarget
        }
        self._records: list[InterventionRecord] = []

    @property
    def records(self) -> tuple[InterventionRecord, ...]:
        return tuple(self._records)

    def apply(
        self,
        target_type: InterventionTarget,
        target_id: str,
        reason: str,
        expected_effect: str | None = None,
    ) -> InterventionRecord:
        if target_id in self._active[target_type]:
            raise ValueError(f"intervention already active: {target_type.value}/{target_id}")
        self._active[target_type].add(target_id)
        record = InterventionRecord(
            run_id=self._run_id,
            intervention_id=f"intervention_{len(self._records) + 1:03d}",
            target_type=target_type,
            target_id=target_id,
            started_at=self._clock.now(),
            reason=reason,
            random_seed=self._seed,
            expected_effect=expected_effect,
        )
        self._records.append(record)
        self._traces.emit(
            TraceEventType.FAILURE_INJECTED,
            trace_id=record.intervention_id,
            intervention_id=record.intervention_id,
            target_type=target_type.value,
            target_id=target_id,
            reason=reason,
            expected_effect=expected_effect,
        )
        return record

    def clear(self, target_type: InterventionTarget, target_id: str) -> None:
        self._active[target_type].discard(target_id)
        for index in range(len(self._records) - 1, -1, -1):
            record = self._records[index]
            if (
                record.target_type is target_type
                and record.target_id == target_id
                and record.ended_at is None
            ):
                self._records[index] = replace(record, ended_at=self._clock.now())
                return

    def is_active(self, target_type: InterventionTarget, target_id: str) -> bool:
        return target_id in self._active[target_type]

    def route_available(self, node_id: str) -> bool:
        return not self.is_active(InterventionTarget.ROUTE, node_id)

    def blocked_reason(
        self,
        *,
        node_id: str,
        memory_id: str,
        source_ids: tuple[str, ...],
        lineage_root_ids: tuple[str, ...],
        edge_path: tuple[str, ...],
        required_scope: str,
        valid_until: str | None,
        valid_at: str | None,
    ) -> str | None:
        checks = (
            (InterventionTarget.NODE, (node_id,), "node unavailable"),
            (InterventionTarget.MEMORY, (memory_id,), "memory unavailable"),
            (InterventionTarget.SOURCE, source_ids, "source unavailable"),
            (InterventionTarget.LINEAGE_ROOT, lineage_root_ids, "lineage root unavailable"),
            (InterventionTarget.EDGE, edge_path, "edge unavailable"),
            (InterventionTarget.AUTHORIZATION_SCOPE, (required_scope,), "authorization scope revoked"),
            (InterventionTarget.EVIDENCE, (memory_id,), "evidence stale or expired"),
        )
        for target_type, identifiers, reason in checks:
            if any(self.is_active(target_type, identifier) for identifier in identifiers):
                return reason
        if valid_until and valid_at:
            until = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            at = datetime.fromisoformat(valid_at.replace("Z", "+00:00"))
            if at > until:
                return "evidence expired"
        return None


class CapabilityRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, MemoryNodeAdapter] = {}

    def register(self, adapter: MemoryNodeAdapter) -> None:
        if adapter.node_id in self._adapters:
            raise ValueError(f"node already registered: {adapter.node_id}")
        self._adapters[adapter.node_id] = adapter

    def adapter_for(self, node_id: str) -> MemoryNodeAdapter:
        return self._adapters[node_id]

    def node_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    async def eligible_nodes(
        self,
        request: QueryRequest,
        failure: FailureInjector,
    ) -> tuple[str, ...]:
        eligible = []
        for node_id, adapter in self._adapters.items():
            if not failure.route_available(node_id):
                continue
            descriptors = await adapter.describe_capabilities(request.authorization)
            if any(
                descriptor.capability_id == request.requested_capability
                and descriptor.availability.value != "unavailable"
                for descriptor in descriptors
            ):
                eligible.append(node_id)
        return tuple(eligible)


class Router:
    def __init__(self, registry: CapabilityRegistry, failure: FailureInjector) -> None:
        self._registry = registry
        self._failure = failure

    async def select(
        self,
        request: QueryRequest,
        preferred_node_ids: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        eligible = await self._registry.eligible_nodes(request, self._failure)
        if preferred_node_ids is None:
            ordered = eligible
        else:
            preferred = [node for node in preferred_node_ids if node in eligible]
            ordered = tuple(preferred + [node for node in eligible if node not in preferred])
        return tuple(ordered[:request.budget.max_nodes])


class ClaimNormalizer:
    def normalize(
        self, claims: Iterable[ClaimEnvelope], query_id: str
    ) -> tuple[ClaimEnvelope, ...]:
        by_id: dict[str, ClaimEnvelope] = {}
        for claim in claims:
            if claim.query_id != query_id or claim.policy_status is not PolicyStatus.ALLOWED:
                continue
            if not isinstance(claim.content, dict):
                continue
            if not {"slot", "value"}.issubset(claim.content):
                continue
            by_id[claim.claim_id] = claim
        return tuple(sorted(by_id.values(), key=lambda claim: claim.claim_id))


class RuleBasedSynthesizer:
    """Fixture-neutral slot composition; no model output is involved."""

    def __init__(self, required_slots: tuple[str, ...]) -> None:
        self.required_slots = required_slots

    def synthesize(self, claims: Iterable[ClaimEnvelope]) -> SynthesisResult:
        selected = []
        covered = []
        for slot in self.required_slots:
            candidates = [
                claim for claim in claims
                if claim.content.get("slot") == slot
                and claim.content.get("value") != "[REDACTED]"
            ]
            if not candidates:
                continue
            candidates.sort(key=lambda claim: (-claim.confidence, claim.producer_node_id, claim.claim_id))
            selected.append(candidates[0])
            covered.append(slot)
        success = len(covered) == len(self.required_slots)
        answer = None
        confidence = max((claim.confidence for claim in claims), default=0.0)
        if success:
            answer = ":".join(str(claim.content["value"]) for claim in selected)
            confidence = min(claim.confidence for claim in selected)
        return SynthesisResult(
            success=success,
            answer=answer,
            confidence=round(confidence, 6),
            selected_claim_ids=tuple(claim.claim_id for claim in selected),
            selected_node_ids=tuple(claim.producer_node_id for claim in selected),
            covered_slots=tuple(covered),
        )


@dataclass(frozen=True)
class ReconstructionCoalition:
    lineage_root_ids: tuple[str, ...]
    failure_domains: tuple[str, ...]
    claim_ids: tuple[str, ...]


class LineageAnalyzer:
    def reconstruction_coalitions(
        self,
        claims: Iterable[ClaimEnvelope],
        required_slots: tuple[str, ...],
    ) -> tuple[ReconstructionCoalition, ...]:
        claims = tuple(claims)
        per_slot = [
            tuple(claim for claim in claims if claim.content.get("slot") == slot)
            for slot in required_slots
        ]
        if any(not slot_claims for slot_claims in per_slot):
            return ()
        canonical: dict[tuple[tuple[str, ...], tuple[str, ...]], ReconstructionCoalition] = {}
        for selected in product(*per_slot):
            roots = tuple(sorted({root for claim in selected for root in claim.lineage_root_ids}))
            domains = tuple(sorted({domain for claim in selected for domain in claim.failure_domains}))
            if not roots or not domains:
                continue
            key = (roots, domains)
            claim_ids = tuple(sorted(claim.claim_id for claim in selected))
            existing = canonical.get(key)
            if existing is None or claim_ids < existing.claim_ids:
                canonical[key] = ReconstructionCoalition(roots, domains, claim_ids)
        return tuple(canonical[key] for key in sorted(canonical))

    def score(
        self,
        claims: tuple[ClaimEnvelope, ...],
        required_slots: tuple[str, ...],
        synthesis: SynthesisResult,
    ) -> FragilityMetrics:
        coalitions = self.reconstruction_coalitions(claims, required_slots)
        roots = {root for claim in claims for root in claim.lineage_root_ids}
        domains = {domain for claim in claims for domain in claim.failure_domains}
        min_size = min((len(coalition.lineage_root_ids) for coalition in coalitions), default=0)
        min_cut = self._minimum_domain_cut(coalitions)
        coverage = len(synthesis.covered_slots) / len(required_slots) if required_slots else 1.0
        gap = max(0.0, synthesis.confidence - coverage)
        return FragilityMetrics(
            apparent_replica_count=len(claims),
            unique_lineage_root_count=len(roots),
            unique_failure_domain_count=len(domains),
            reconstruction_coalition_count=len(coalitions),
            minimal_support_coalition_size=min_size,
            minimal_failure_domain_cut=min_cut,
            current_task_success=synthesis.success,
            current_confidence=round(synthesis.confidence, 6),
            valid_support_coverage=round(coverage, 6),
            confidence_valid_support_gap=round(gap, 6),
        )

    @staticmethod
    def _minimum_domain_cut(coalitions: tuple[ReconstructionCoalition, ...]) -> int:
        if not coalitions:
            return 0
        domains = sorted({domain for coalition in coalitions for domain in coalition.failure_domains})
        coalition_domains = [set(coalition.failure_domains) for coalition in coalitions]
        for size in range(1, len(domains) + 1):
            for candidate in combinations(domains, size):
                if all(set(candidate) & domain_set for domain_set in coalition_domains):
                    return size
        return 0


@dataclass(frozen=True)
class QueryExecution:
    phase: str
    route: tuple[str, ...]
    claims: tuple[ClaimEnvelope, ...]
    exports: tuple[EvidenceExport, ...]
    synthesis: SynthesisResult
    metrics: FragilityMetrics
    exported_bytes: int
    policy_violations: int


class TesseractCoordinator:
    def __init__(
        self,
        registry: CapabilityRegistry,
        router: Router,
        normalizer: ClaimNormalizer,
        synthesizer: RuleBasedSynthesizer,
        analyzer: LineageAnalyzer,
        traces: TraceLogger,
    ) -> None:
        self.registry = registry
        self.router = router
        self.normalizer = normalizer
        self.synthesizer = synthesizer
        self.analyzer = analyzer
        self.traces = traces

    async def execute(
        self,
        request: QueryRequest,
        phase: str,
        preferred_node_ids: tuple[str, ...] | None = None,
    ) -> QueryExecution:
        trace_id = f"query_trace_{request.query_id}_{phase}"
        self.traces.emit(TraceEventType.QUERY_RECEIVED, trace_id, query_id=request.query_id, phase=phase)
        route = await self.router.select(request, preferred_node_ids)
        self.traces.emit(TraceEventType.ROUTE_SELECTED, trace_id, node_ids=route, phase=phase)
        exports: list[EvidenceExport] = []
        raw_claims: list[ClaimEnvelope] = []
        for node_id in route:
            node_exports = await self.registry.adapter_for(node_id).query(request)
            for export in node_exports:
                exports.append(export)
                self.traces.emit(
                    TraceEventType.RETRIEVAL_SELECTED,
                    trace_id,
                    node_id=node_id,
                    local_trace_id=export.trace.trace_id,
                    retrieval_operator=export.trace.retrieval_operator,
                    policy_status=export.trace.policy_status.value,
                    lineage_root_ids=export.trace.lineage_root_ids,
                )
                if export.trace.policy_status is not PolicyStatus.ALLOWED:
                    self.traces.emit(
                        TraceEventType.POLICY_BLOCK,
                        trace_id,
                        node_id=node_id,
                        policy_status=export.trace.policy_status.value,
                    )
                for claim in export.claims:
                    raw_claims.append(claim)
                    self.traces.emit(
                        TraceEventType.EVIDENCE_EXPORTED,
                        trace_id,
                        node_id=node_id,
                        claim_id=claim.claim_id,
                        lineage_root_ids=claim.lineage_root_ids,
                        failure_domains=claim.failure_domains,
                    )
        claims = self.normalizer.normalize(raw_claims[:request.budget.max_claims], request.query_id)
        synthesis = self.synthesizer.synthesize(claims)
        self.traces.emit(
            TraceEventType.CLAIM_SYNTHESIZED,
            trace_id,
            success=synthesis.success,
            selected_claim_ids=synthesis.selected_claim_ids,
            selected_node_ids=synthesis.selected_node_ids,
        )
        metrics = self.analyzer.score(claims, self.synthesizer.required_slots, synthesis)
        self.traces.emit(TraceEventType.FRAGILITY_SCORED, trace_id, metrics=metrics)
        self.traces.emit(
            TraceEventType.CAPABILITY_EVALUATED,
            trace_id,
            phase=phase,
            success=synthesis.success,
            confidence=synthesis.confidence,
        )
        exported_bytes = len(canonical_json(exports).encode("utf-8"))
        policy_violations = sum(
            1 for export in exports
            if export.trace.policy_status is not PolicyStatus.ALLOWED and export.claims
        )
        return QueryExecution(
            phase=phase,
            route=route,
            claims=claims,
            exports=tuple(exports),
            synthesis=synthesis,
            metrics=metrics,
            exported_bytes=exported_bytes,
            policy_violations=policy_violations,
        )


@dataclass(frozen=True)
class RepairDecision:
    selected_node_id: str | None
    verification_results: tuple[VerificationResult, ...]
    steps: int
    reason: str


class RepairPlanner:
    """Select the first verified path independent of current and failed support."""

    def __init__(self, registry: CapabilityRegistry, traces: TraceLogger) -> None:
        self._registry = registry
        self._traces = traces

    async def plan(
        self,
        request: QueryRequest,
        missing_slots: tuple[str, ...],
        candidate_node_ids: tuple[str, ...],
        excluded_roots: tuple[str, ...],
        excluded_failure_domains: tuple[str, ...],
    ) -> RepairDecision:
        trace_id = f"repair_{request.query_id}"
        self._traces.emit(
            TraceEventType.REPAIR_REQUESTED,
            trace_id,
            missing_slots=missing_slots,
            candidate_node_ids=candidate_node_ids,
            excluded_roots=excluded_roots,
            excluded_failure_domains=excluded_failure_domains,
        )
        results = []
        selected = None
        for step, node_id in enumerate(candidate_node_ids[:request.budget.max_verifications], 1):
            verification = VerificationRequest(
                verification_id=f"verification_{request.query_id}_{step:02d}",
                query=request,
                required_slots=missing_slots,
                excluded_lineage_roots=excluded_roots,
                excluded_failure_domains=excluded_failure_domains,
            )
            self._traces.emit(
                TraceEventType.VERIFICATION_REQUESTED,
                trace_id,
                verification_id=verification.verification_id,
                node_id=node_id,
            )
            result = await self._registry.adapter_for(node_id).verify(verification)
            results.append(result)
            self._traces.emit(
                TraceEventType.VERIFICATION_RESULT,
                trace_id,
                result=result,
            )
            if result.valid:
                selected = node_id
                break
        reason = "independent support selected" if selected else "no independent support within budget"
        self._traces.emit(
            TraceEventType.REPAIR_COMPLETED,
            trace_id,
            selected_node_id=selected,
            reason=reason,
            steps=len(results),
        )
        return RepairDecision(selected, tuple(results), len(results), reason)
