"""Peer memory nodes for the coordinator: in-process and over MCP on stdio.

This is the transport the handoff notes named as the next step.  A peer is
another agent's NeuralGraph memory server.  ``McpPeerAdapter`` speaks the
Model Context Protocol to it over a real process boundary; ``InProcessPeer``
wraps a ``MemoryEngine`` directly for deterministic tests and pinned demos.
Both implement ``MemoryNodeAdapter`` and both return the same typed contracts,
rebuilt from JSON here with the contracts' own validation, so nothing a peer
sends is trusted before it is checked.

Failure injection stays at the orchestrator.  A peer's store is never
touched: every export is passed through the orchestrator's
``FailureView.blocked_reason`` (node, memory, source, lineage root, domain,
edge, scope), and a blocked export becomes a payload-free denial trace,
exactly as ``MockMemoryNodeAdapter`` does for the fixture.  That is what
makes a forecast over real memories reversible.

A transport failure (peer process gone, timeout) is reported as a denial
trace with ``retrieval_operator="transport_failure"`` rather than raised, so
a dead peer looks to the coordinator like an unavailable node.  One retry is
made on transport failure; a retried export carries the same claim ids, and
the coordinator drops the duplicate.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any, Callable

from .adapters import FailureView
from .contracts import (
    Availability,
    AuthorizationContext,
    CapabilityDescriptor,
    ClaimEnvelope,
    EvidenceExport,
    LearningDecision,
    LearningDisposition,
    LearningSignal,
    PolicyStatus,
    QueryRequest,
    RetrievalTrace,
    ValidTime,
    VerificationRequest,
    VerificationResult,
)

_TUPLE_FIELDS = ("evidence_refs", "source_ids", "parent_memory_ids", "lineage_root_ids", "failure_domains")


def _strs(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of identifiers")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} entries must be non-empty str")
        out.append(str.__str__(item))
    return tuple(out)


def claim_from_json(data: dict[str, Any]) -> ClaimEnvelope:
    """Rebuild a claim a peer sent; the contract validates it on construction."""
    content = data.get("content")
    if not isinstance(content, dict):
        raise ValueError("claim content must be an object")
    valid_time = data.get("valid_time")
    return ClaimEnvelope(
        claim_id=str(data["claim_id"]), query_id=str(data["query_id"]), producer_node_id=str(data["producer_node_id"]),
        content={str(k): v for k, v in content.items() if isinstance(v, (str, int, float, bool, type(None)))},
        confidence=float(data["confidence"]),
        **{f: _strs(data.get(f, []), f) for f in _TUPLE_FIELDS},
        policy_status=PolicyStatus(data["policy_status"]),
        created_at=str(data["created_at"]),
        valid_time=ValidTime(valid_time.get("from_time"), valid_time.get("to_time")) if isinstance(valid_time, dict) else None,
        derivation_operator=data.get("derivation_operator"),
    )


def trace_from_json(data: dict[str, Any]) -> RetrievalTrace:
    return RetrievalTrace(
        trace_id=str(data["trace_id"]), query_id=str(data["query_id"]), node_id=str(data["node_id"]),
        memory_ids=_strs(data.get("memory_ids", []), "memory_ids"), source_ids=_strs(data.get("source_ids", []), "source_ids"),
        parent_memory_ids=_strs(data.get("parent_memory_ids", []), "parent_memory_ids"),
        lineage_root_ids=_strs(data.get("lineage_root_ids", []), "lineage_root_ids"),
        edge_path=_strs(data.get("edge_path", []), "edge_path"),
        retrieval_operator=str(data["retrieval_operator"]), policy_status=PolicyStatus(data["policy_status"]),
        started_at=str(data["started_at"]), completed_at=str(data["completed_at"]),
    )


def exports_from_json(payload: dict[str, Any]) -> tuple[EvidenceExport, ...]:
    exports = []
    for item in payload.get("exports", []):
        trace = trace_from_json(item["trace"])
        claims = tuple(claim_from_json(c) for c in item.get("claims", []))
        if trace.policy_status is not PolicyStatus.ALLOWED and claims:
            raise ValueError("a denied trace must not carry claims")
        exports.append(EvidenceExport(claims=claims, trace=trace))
    return tuple(exports)


def verification_from_json(data: dict[str, Any], verification_id: str, node_id: str) -> VerificationResult:
    return VerificationResult(
        verification_id=verification_id, node_id=node_id, valid=bool(data["valid"]),
        lineage_root_ids=_strs(data.get("lineage_root_ids", []), "lineage_root_ids"),
        failure_domains=_strs(data.get("failure_domains", []), "failure_domains"),
        supported_slots=_strs(data.get("supported_slots", []), "supported_slots"),
        reason=str(data.get("reason", "")),
    )


class _PeerBase:
    """Shared boundary logic: masking through the orchestrator's failure view."""

    def __init__(self, node_id: str, failure_view: FailureView | None, clock: Callable[[], str],
                 keys_lookup: Callable[[str], tuple[str, ...]] | None = None, scope: str = "memory:read") -> None:
        self.node_id = node_id
        self._failure_view = failure_view
        self._clock = clock
        # The fabric records which keys a query asks for; the request itself is
        # the unchanged contract (contracts.py is not widened for this).
        self._keys_lookup = keys_lookup or (lambda _query_id: ())
        self._scope = scope

    def _denial(self, request: QueryRequest, trace_id: str, operator: str) -> EvidenceExport:
        started = self._clock()
        return EvidenceExport(claims=(), trace=RetrievalTrace(
            trace_id=trace_id, query_id=request.query_id, node_id=self.node_id,
            memory_ids=(), source_ids=(), parent_memory_ids=(), lineage_root_ids=(), edge_path=(),
            retrieval_operator=operator, policy_status=PolicyStatus.DENIED, started_at=started, completed_at=self._clock(),
        ))

    def _mask(self, request: QueryRequest, exports: tuple[EvidenceExport, ...]) -> tuple[EvidenceExport, ...]:
        if self._failure_view is None:
            return exports
        masked = []
        for export in exports:
            trace = export.trace
            if not export.claims:
                masked.append(export); continue
            claim = export.claims[0]
            blocked = self._failure_view.blocked_reason(
                node_id=self.node_id, memory_id=trace.memory_ids[0] if trace.memory_ids else claim.claim_id,
                source_ids=claim.source_ids, lineage_root_ids=claim.lineage_root_ids, edge_path=trace.edge_path,
                required_scope=self._scope, valid_until=None, valid_at=request.valid_at,
            )
            masked.append(self._denial(request, trace.trace_id, "policy_filter") if blocked else export)
        return tuple(masked)

    async def propose_learning(self, signal: LearningSignal) -> LearningDecision:
        return LearningDecision(signal_id=signal.signal_id, receiver_node_id=self.node_id,
                                disposition=LearningDisposition.REJECTED, reason="peer learning not enabled")


class InProcessPeer(_PeerBase):
    """A ``MemoryEngine`` as a fabric peer, no transport.  Deterministic."""

    def __init__(self, engine: Any, failure_view: FailureView | None, clock: Callable[[], str],
                 keys_lookup: Callable[[str], tuple[str, ...]] | None = None) -> None:
        super().__init__(engine.node_id(), failure_view, clock, keys_lookup)
        self.engine = engine

    async def describe_capabilities(self, context: AuthorizationContext) -> tuple[CapabilityDescriptor, ...]:
        cap = self.engine.capability()
        permitted = any(context.permits(self.node_id, s) for s in cap["policy_scope"])
        return (CapabilityDescriptor(
            capability_id=cap["capability_id"], node_id=self.node_id, description=cap["description"],
            query_types=tuple(cap["query_types"]), policy_scope=tuple(cap["policy_scope"]),
            availability=Availability.AVAILABLE if permitted else Availability.UNAVAILABLE,
        ),)

    async def query(self, request: QueryRequest) -> tuple[EvidenceExport, ...]:
        payload = await self.engine.export_claims(request.content, limit=request.budget.max_claims, keys=self._keys_lookup(request.query_id))
        return self._mask(request, _rekey(exports_from_json(payload), request.query_id, self.node_id))

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        data = await self.engine.verify_support(request.required_slots, request.excluded_lineage_roots, request.excluded_failure_domains)
        return verification_from_json(data, request.verification_id, self.node_id)


class McpPeerAdapter(_PeerBase):
    """A peer memory server reached over MCP on stdio (a separate process)."""

    def __init__(self, node_id: str, command: str, args: list[str], env: dict[str, str] | None,
                 failure_view: FailureView | None, clock: Callable[[], str], timeout_seconds: float = 20.0,
                 keys_lookup: Callable[[str], tuple[str, ...]] | None = None) -> None:
        super().__init__(node_id, failure_view, clock, keys_lookup)
        self._params = (command, list(args), dict(env or {}))
        self._timeout = timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self.transport_failures = 0

    async def connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command, args, env = self._params
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(StdioServerParameters(command=command, args=args, env={**os.environ, **env})))
        self._session = await self._stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=self._timeout))
        await self._session.initialize()

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            self._stack = None
            self._session = None

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """One call with one retry on transport failure; None when the peer is gone."""
        for attempt in range(2):
            if self._session is None:
                try:
                    await self.connect()
                except Exception:
                    self.transport_failures += 1
                    continue
            try:
                result = await self._session.call_tool(tool, arguments)
                if getattr(result, "is_error", False):
                    return None
                return result.structured_content or {}
            except Exception:
                self.transport_failures += 1
                await self.close()
        return None

    async def describe_capabilities(self, context: AuthorizationContext) -> tuple[CapabilityDescriptor, ...]:
        cap = await self._call("describe_capability", {})
        if not cap:
            return ()
        permitted = any(context.permits(self.node_id, s) for s in cap.get("policy_scope", []))
        return (CapabilityDescriptor(
            capability_id=str(cap["capability_id"]), node_id=self.node_id, description=str(cap.get("description", "peer")),
            query_types=tuple(cap.get("query_types", ())), policy_scope=tuple(cap.get("policy_scope", ())),
            availability=Availability.AVAILABLE if permitted else Availability.UNAVAILABLE,
        ),)

    async def query(self, request: QueryRequest) -> tuple[EvidenceExport, ...]:
        payload = await self._call("export_claims", {"query": request.content, "limit": request.budget.max_claims, "keys": list(self._keys_lookup(request.query_id))})
        if payload is None:
            return (self._denial(request, f"trace_transport_{request.query_id}_{self.node_id}", "transport_failure"),)
        return self._mask(request, _rekey(exports_from_json(payload), request.query_id, self.node_id))

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        data = await self._call("verify_support", {
            "required_keys": list(request.required_slots),
            "excluded_lineage_roots": list(request.excluded_lineage_roots),
            "excluded_failure_domains": list(request.excluded_failure_domains),
        })
        if data is None:
            return VerificationResult(request.verification_id, self.node_id, False, (), (), (), "transport failure")
        return verification_from_json(data, request.verification_id, self.node_id)


def _rekey(exports: tuple[EvidenceExport, ...], query_id: str, node_id: str) -> tuple[EvidenceExport, ...]:
    """Bind a peer's exports to the orchestrator's query id and node id.

    The peer signs its export with its own query id and its own name.  The
    coordinator's normalizer drops claims whose query id differs, and the
    orchestrator names its peers: a peer cannot choose its identity in
    someone else's registry, which also stops one peer impersonating another.
    Claim ids stay as the peer issued them, so a retried export still
    deduplicates.
    """
    from dataclasses import replace

    out = []
    for export in exports:
        out.append(EvidenceExport(
            claims=tuple(replace(c, query_id=query_id, producer_node_id=node_id) for c in export.claims),
            trace=replace(export.trace, query_id=query_id, node_id=node_id),
        ))
    return tuple(out)
