"""Domain-neutral contracts for coordination across private memory nodes.

These objects deliberately contain no NeuralGraph node or edge instances.  They
are the complete trust boundary between a local memory owner and Tesseract's
coordination plane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


def _require(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_iso(value: str | None, name: str) -> None:
    if value is None:
        return
    _require(value, name)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc


class Availability(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class PolicyStatus(str, Enum):
    ALLOWED = "allowed"
    REDACTED = "redacted"
    DENIED = "denied"


class LearningDisposition(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TENTATIVE = "tentative"


class InterventionTarget(str, Enum):
    NODE = "node"
    MEMORY = "memory"
    SOURCE = "source"
    LINEAGE_ROOT = "lineage_root"
    EDGE = "edge"
    AUTHORIZATION_SCOPE = "authorization_scope"
    ROUTE = "route"
    EVIDENCE = "evidence"


class TraceEventType(str, Enum):
    QUERY_RECEIVED = "query_received"
    ROUTE_SELECTED = "route_selected"
    RETRIEVAL_SELECTED = "retrieval_selected"
    POLICY_BLOCK = "policy_block"
    EVIDENCE_EXPORTED = "evidence_exported"
    CLAIM_SYNTHESIZED = "claim_synthesized"
    VERIFICATION_REQUESTED = "verification_requested"
    VERIFICATION_RESULT = "verification_result"
    FRAGILITY_SCORED = "fragility_scored"
    FAILURE_INJECTED = "failure_injected"
    CAPABILITY_EVALUATED = "capability_evaluated"
    REPAIR_REQUESTED = "repair_requested"
    REPAIR_COMPLETED = "repair_completed"
    LEARNING_PROPOSED = "learning_proposed"
    MEMORY_UPDATED = "memory_updated"


@dataclass(frozen=True)
class AuthorizationContext:
    scopes: tuple[str, ...]
    allowed_node_ids: tuple[str, ...] = ()
    allow_redacted: bool = False

    def __post_init__(self) -> None:
        if not self.scopes:
            raise ValueError("authorization scopes must not be empty")

    def permits(self, node_id: str, scope: str) -> bool:
        node_allowed = not self.allowed_node_ids or node_id in self.allowed_node_ids
        return node_allowed and scope in self.scopes


@dataclass(frozen=True)
class QueryBudget:
    max_nodes: int = 3
    max_claims: int = 8
    max_verifications: int = 2

    def __post_init__(self) -> None:
        if min(self.max_nodes, self.max_claims, self.max_verifications) < 0:
            raise ValueError("query budget values must be non-negative")


@dataclass(frozen=True)
class QueryRequest:
    query_id: str
    content: str
    requester_id: str
    issued_at: str
    authorization: AuthorizationContext
    requested_capability: str | None = None
    valid_at: str | None = None
    budget: QueryBudget = field(default_factory=QueryBudget)

    def __post_init__(self) -> None:
        _require(self.query_id, "query_id")
        _require(self.content, "content")
        _require(self.requester_id, "requester_id")
        _require_iso(self.issued_at, "issued_at")
        _require_iso(self.valid_at, "valid_at")


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    node_id: str
    description: str
    query_types: tuple[str, ...]
    policy_scope: tuple[str, ...]
    availability: Availability = Availability.AVAILABLE

    def __post_init__(self) -> None:
        _require(self.capability_id, "capability_id")
        _require(self.node_id, "node_id")
        _require(self.description, "description")


@dataclass(frozen=True)
class ValidTime:
    from_time: str | None = None
    to_time: str | None = None

    def __post_init__(self) -> None:
        _require_iso(self.from_time, "valid_time.from")
        _require_iso(self.to_time, "valid_time.to")


@dataclass(frozen=True)
class ClaimEnvelope:
    claim_id: str
    query_id: str
    producer_node_id: str
    content: Any
    confidence: float
    evidence_refs: tuple[str, ...]
    source_ids: tuple[str, ...]
    parent_memory_ids: tuple[str, ...]
    lineage_root_ids: tuple[str, ...]
    failure_domains: tuple[str, ...]
    policy_status: PolicyStatus
    created_at: str
    valid_time: ValidTime | None = None
    derivation_operator: str | None = None

    def __post_init__(self) -> None:
        _require(self.claim_id, "claim_id")
        _require(self.query_id, "query_id")
        _require(self.producer_node_id, "producer_node_id")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("claim confidence must be between 0 and 1")
        if self.policy_status is PolicyStatus.DENIED:
            raise ValueError("denied evidence must not be exported as a claim")
        _require_iso(self.created_at, "created_at")


@dataclass(frozen=True)
class RetrievalTrace:
    trace_id: str
    query_id: str
    node_id: str
    memory_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    parent_memory_ids: tuple[str, ...]
    lineage_root_ids: tuple[str, ...]
    edge_path: tuple[str, ...]
    retrieval_operator: str
    policy_status: PolicyStatus
    started_at: str
    completed_at: str

    def __post_init__(self) -> None:
        _require(self.trace_id, "trace_id")
        _require(self.query_id, "query_id")
        _require(self.node_id, "node_id")
        _require(self.retrieval_operator, "retrieval_operator")
        _require_iso(self.started_at, "started_at")
        _require_iso(self.completed_at, "completed_at")
        if self.policy_status is not PolicyStatus.ALLOWED and any(
            (self.memory_ids, self.source_ids, self.parent_memory_ids,
             self.lineage_root_ids, self.edge_path)
        ):
            raise ValueError("blocked or redacted traces must not expose structural identifiers")


@dataclass(frozen=True)
class EvidenceExport:
    claims: tuple[ClaimEnvelope, ...]
    trace: RetrievalTrace


@dataclass(frozen=True)
class VerificationRequest:
    verification_id: str
    query: QueryRequest
    required_slots: tuple[str, ...]
    excluded_lineage_roots: tuple[str, ...] = ()
    excluded_failure_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    verification_id: str
    node_id: str
    valid: bool
    lineage_root_ids: tuple[str, ...]
    failure_domains: tuple[str, ...]
    supported_slots: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class LearningSignal:
    signal_id: str
    sender_node_id: str
    receiver_node_id: str
    proposed_content: Any
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class LearningDecision:
    signal_id: str
    receiver_node_id: str
    disposition: LearningDisposition
    reason: str
    committed_memory_id: str | None = None


@dataclass(frozen=True)
class TraceEvent:
    schema_version: str
    event_id: str
    run_id: str
    trace_id: str
    event_type: TraceEventType
    sequence: int
    occurred_at: str
    details: dict[str, Any]


@dataclass(frozen=True)
class InterventionRecord:
    run_id: str
    intervention_id: str
    target_type: InterventionTarget
    target_id: str
    started_at: str
    reason: str
    random_seed: int
    expected_effect: str | None = None
    ended_at: str | None = None


@dataclass(frozen=True)
class ExperimentManifest:
    run_id: str
    seed: int
    started_at: str
    fixture_version: str
    strategy: str
    budget: QueryBudget
    interventions: tuple[InterventionRecord, ...] = ()
    code_version: str | None = None


@dataclass(frozen=True)
class FragilityMetrics:
    apparent_replica_count: int
    unique_lineage_root_count: int
    unique_failure_domain_count: int
    reconstruction_coalition_count: int
    minimal_support_coalition_size: int
    minimal_failure_domain_cut: int
    current_task_success: bool
    current_confidence: float
    valid_support_coverage: float
    confidence_valid_support_gap: float


@dataclass(frozen=True)
class SynthesisResult:
    success: bool
    answer: str | None
    confidence: float
    selected_claim_ids: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    covered_slots: tuple[str, ...]

