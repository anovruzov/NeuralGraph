"""Private local-memory adapters for the coordination boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from .contracts import (
    AuthorizationContext,
    Availability,
    CapabilityDescriptor,
    ClaimEnvelope,
    EvidenceExport,
    LearningDecision,
    LearningDisposition,
    LearningSignal,
    PolicyStatus,
    QueryRequest,
    RetrievalTrace,
    VerificationRequest,
    VerificationResult,
)


class FailureView(Protocol):
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
    ) -> str | None: ...


class MemoryNodeAdapter(Protocol):
    node_id: str

    async def describe_capabilities(
        self, context: AuthorizationContext
    ) -> tuple[CapabilityDescriptor, ...]: ...

    async def query(self, request: QueryRequest) -> tuple[EvidenceExport, ...]: ...

    async def verify(self, request: VerificationRequest) -> VerificationResult: ...

    async def propose_learning(self, signal: LearningSignal) -> LearningDecision: ...


@dataclass(frozen=True)
class PrivateMemoryRecord:
    """Internal mock record. It is never returned across the adapter boundary."""

    memory_id: str
    capability_id: str
    slot: str
    value: str
    confidence: float
    source_ids: tuple[str, ...]
    parent_memory_ids: tuple[str, ...]
    lineage_root_ids: tuple[str, ...]
    failure_domains: tuple[str, ...]
    edge_path: tuple[str, ...]
    required_scope: str
    retrieval_operator: str = "mock_exact_lookup"
    policy_mode: PolicyStatus = PolicyStatus.ALLOWED
    valid_until: str | None = None


class MockMemoryNodeAdapter:
    """Deterministic adapter over one node's private immutable memory records."""

    def __init__(
        self,
        node_id: str,
        records: tuple[PrivateMemoryRecord, ...],
        failure_view: FailureView,
        clock: Callable[[], str],
        learning_policy: LearningDisposition = LearningDisposition.REJECTED,
    ) -> None:
        self.node_id = node_id
        self.__records = records
        self._failure_view = failure_view
        self._clock = clock
        self._learning_policy = learning_policy

    async def describe_capabilities(
        self, context: AuthorizationContext
    ) -> tuple[CapabilityDescriptor, ...]:
        descriptors = []
        for capability_id in sorted({record.capability_id for record in self.__records}):
            scopes = tuple(sorted({
                record.required_scope for record in self.__records
                if record.capability_id == capability_id
            }))
            availability = (
                Availability.AVAILABLE
                if any(context.permits(self.node_id, scope) for scope in scopes)
                else Availability.UNAVAILABLE
            )
            descriptors.append(CapabilityDescriptor(
                capability_id=capability_id,
                node_id=self.node_id,
                description=f"Private evidence for {capability_id}",
                query_types=("composition",),
                policy_scope=scopes,
                availability=availability,
            ))
        return tuple(descriptors)

    async def query(self, request: QueryRequest) -> tuple[EvidenceExport, ...]:
        exports = []
        for index, record in enumerate(self.__records):
            if request.requested_capability != record.capability_id:
                continue
            started = self._clock()
            trace_id = self._opaque_id("trace", request.query_id, record.memory_id, str(index))
            authorized = request.authorization.permits(self.node_id, record.required_scope)
            blocked = self._failure_view.blocked_reason(
                node_id=self.node_id,
                memory_id=record.memory_id,
                source_ids=record.source_ids,
                lineage_root_ids=record.lineage_root_ids,
                edge_path=record.edge_path,
                required_scope=record.required_scope,
                valid_until=record.valid_until,
                valid_at=request.valid_at,
            )
            policy = record.policy_mode
            if not authorized or blocked:
                policy = PolicyStatus.DENIED
            elif policy is PolicyStatus.REDACTED and not request.authorization.allow_redacted:
                policy = PolicyStatus.DENIED

            if policy is PolicyStatus.DENIED:
                exports.append(EvidenceExport(
                    claims=(),
                    trace=RetrievalTrace(
                        trace_id=trace_id,
                        query_id=request.query_id,
                        node_id=self.node_id,
                        memory_ids=(),
                        source_ids=(),
                        parent_memory_ids=(),
                        lineage_root_ids=(),
                        edge_path=(),
                        retrieval_operator="policy_filter",
                        policy_status=PolicyStatus.DENIED,
                        started_at=started,
                        completed_at=self._clock(),
                    ),
                ))
                continue

            if policy is PolicyStatus.REDACTED:
                content: Any = {"slot": record.slot, "value": "[REDACTED]"}
                structural = ()
            else:
                content = {"slot": record.slot, "value": record.value}
                structural = None

            claim_id = self._opaque_id("claim", request.query_id, self.node_id, record.memory_id)
            claim = ClaimEnvelope(
                claim_id=claim_id,
                query_id=request.query_id,
                producer_node_id=self.node_id,
                content=content,
                confidence=record.confidence,
                evidence_refs=structural if structural is not None else (self._opaque_id("evidence", record.memory_id),),
                source_ids=structural if structural is not None else record.source_ids,
                parent_memory_ids=structural if structural is not None else record.parent_memory_ids,
                lineage_root_ids=structural if structural is not None else record.lineage_root_ids,
                failure_domains=structural if structural is not None else record.failure_domains,
                policy_status=policy,
                derivation_operator="local_policy_filtered_export",
                created_at=self._clock(),
            )
            trace = RetrievalTrace(
                trace_id=trace_id,
                query_id=request.query_id,
                node_id=self.node_id,
                memory_ids=structural if structural is not None else (record.memory_id,),
                source_ids=structural if structural is not None else record.source_ids,
                parent_memory_ids=structural if structural is not None else record.parent_memory_ids,
                lineage_root_ids=structural if structural is not None else record.lineage_root_ids,
                edge_path=structural if structural is not None else record.edge_path,
                retrieval_operator=record.retrieval_operator,
                policy_status=policy,
                started_at=started,
                completed_at=self._clock(),
            )
            exports.append(EvidenceExport(claims=(claim,), trace=trace))
        return tuple(exports)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        matching = [
            record for record in self.__records
            if record.capability_id == request.query.requested_capability
            and record.slot in request.required_slots
        ]
        valid_records = []
        reasons = []
        for record in matching:
            blocked = self._failure_view.blocked_reason(
                node_id=self.node_id,
                memory_id=record.memory_id,
                source_ids=record.source_ids,
                lineage_root_ids=record.lineage_root_ids,
                edge_path=record.edge_path,
                required_scope=record.required_scope,
                valid_until=record.valid_until,
                valid_at=request.query.valid_at,
            )
            independent = not (
                set(record.lineage_root_ids) & set(request.excluded_lineage_roots)
                or set(record.failure_domains) & set(request.excluded_failure_domains)
            )
            authorized = request.query.authorization.permits(self.node_id, record.required_scope)
            if not independent:
                reasons.append("correlated support")
            elif blocked:
                reasons.append(blocked)
            elif not authorized:
                reasons.append("policy denied")
            else:
                valid_records.append(record)
        valid = bool(valid_records)
        return VerificationResult(
            verification_id=request.verification_id,
            node_id=self.node_id,
            valid=valid,
            lineage_root_ids=tuple(sorted({root for record in matching for root in record.lineage_root_ids})),
            failure_domains=tuple(sorted({domain for record in matching for domain in record.failure_domains})),
            supported_slots=tuple(sorted({record.slot for record in valid_records})),
            reason="independent support available" if valid else (reasons[0] if reasons else "no matching support"),
        )

    async def propose_learning(self, signal: LearningSignal) -> LearningDecision:
        if signal.receiver_node_id != self.node_id:
            return LearningDecision(
                signal_id=signal.signal_id,
                receiver_node_id=self.node_id,
                disposition=LearningDisposition.REJECTED,
                reason="receiver mismatch",
            )
        # Acceptance is only a receiver decision. This mock intentionally does not
        # commit a record; a separate receiver-owned callback would perform that.
        return LearningDecision(
            signal_id=signal.signal_id,
            receiver_node_id=self.node_id,
            disposition=self._learning_policy,
            reason="receiver policy decision",
        )

    @staticmethod
    def _opaque_id(kind: str, *parts: str) -> str:
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
        return f"{kind}_{digest}"


class NeuralGraphMemoryAdapter:
    """Narrow adapter for a real local NeuralGraph retrieval callback.

    The callback executes inside the owner boundary.  The optional lineage
    resolver may return only facts the local graph actually knows; absent fields
    remain empty and are never synthesized by this adapter.
    """

    def __init__(
        self,
        node_id: str,
        capability: CapabilityDescriptor,
        local_retrieve: Callable[[QueryRequest], Awaitable[list[tuple[Any, float]]]],
        policy_filter: Callable[[Any, AuthorizationContext], PolicyStatus],
        clock: Callable[[], str],
        lineage_resolver: Callable[[Any], dict[str, tuple[str, ...]]] | None = None,
    ) -> None:
        self.node_id = node_id
        self._capability = capability
        self._local_retrieve = local_retrieve
        self._policy_filter = policy_filter
        self._lineage_resolver = lineage_resolver
        self._clock = clock

    async def describe_capabilities(
        self, context: AuthorizationContext
    ) -> tuple[CapabilityDescriptor, ...]:
        return (self._capability,)

    async def query(self, request: QueryRequest) -> tuple[EvidenceExport, ...]:
        results = await self._local_retrieve(request)
        exports = []
        for index, (node, confidence) in enumerate(results[:request.budget.max_claims]):
            started = self._clock()
            policy = self._policy_filter(node, request.authorization)
            if policy is not PolicyStatus.ALLOWED:
                exports.append(EvidenceExport(
                    claims=(),
                    trace=RetrievalTrace(
                        trace_id=MockMemoryNodeAdapter._opaque_id("trace", request.query_id, self.node_id, str(index)),
                        query_id=request.query_id,
                        node_id=self.node_id,
                        memory_ids=(), source_ids=(), parent_memory_ids=(),
                        lineage_root_ids=(), edge_path=(),
                        retrieval_operator="policy_filter",
                        policy_status=policy,
                        started_at=started, completed_at=self._clock(),
                    ),
                ))
                continue
            known = self._lineage_resolver(node) if self._lineage_resolver else {}
            memory_id = str(getattr(node, "node_id"))
            source_ids = tuple(known.get("source_ids", tuple(getattr(node, "source_memory_ids", ()))))
            parent_ids = tuple(known.get("parent_memory_ids", ()))
            root_ids = tuple(known.get("lineage_root_ids", ()))
            failure_domains = tuple(known.get("failure_domains", ()))
            edge_path = tuple(known.get("edge_path", ()))
            content = known.get("claim_content", {"text": str(getattr(node, "content"))})
            claim = ClaimEnvelope(
                claim_id=MockMemoryNodeAdapter._opaque_id("claim", request.query_id, self.node_id, memory_id),
                query_id=request.query_id,
                producer_node_id=self.node_id,
                content=content,
                confidence=max(0.0, min(1.0, float(confidence))),
                evidence_refs=(MockMemoryNodeAdapter._opaque_id("evidence", memory_id),),
                source_ids=source_ids,
                parent_memory_ids=parent_ids,
                lineage_root_ids=root_ids,
                failure_domains=failure_domains,
                policy_status=PolicyStatus.ALLOWED,
                derivation_operator=known.get("retrieval_operator", "neuralgraph_local_retrieval"),
                created_at=self._clock(),
            )
            trace = RetrievalTrace(
                trace_id=MockMemoryNodeAdapter._opaque_id("trace", request.query_id, self.node_id, str(index)),
                query_id=request.query_id,
                node_id=self.node_id,
                memory_ids=(memory_id,), source_ids=source_ids,
                parent_memory_ids=parent_ids, lineage_root_ids=root_ids,
                edge_path=edge_path,
                retrieval_operator=str(known.get("retrieval_operator", "neuralgraph_local_retrieval")),
                policy_status=PolicyStatus.ALLOWED,
                started_at=started, completed_at=self._clock(),
            )
            exports.append(EvidenceExport(claims=(claim,), trace=trace))
        return tuple(exports)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        return VerificationResult(
            verification_id=request.verification_id,
            node_id=self.node_id,
            valid=False,
            lineage_root_ids=(), failure_domains=(), supported_slots=(),
            reason="verification callback not configured",
        )

    async def propose_learning(self, signal: LearningSignal) -> LearningDecision:
        return LearningDecision(
            signal_id=signal.signal_id,
            receiver_node_id=self.node_id,
            disposition=LearningDisposition.REJECTED,
            reason="real adapter requires a receiver-owned learning callback",
        )
