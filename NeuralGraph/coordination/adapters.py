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

    Only ``PolicyStatus.ALLOWED`` produces a claim.  Every other status,
    ``REDACTED`` included, fails closed as a structurally empty denial trace:
    this adapter owns no redaction projection, so it cannot construct a
    sanitized payload it can prove is safe.
    """

    #: The only value types a claim may carry across the boundary.
    _EXPORTABLE_VALUE_TYPES = (str, int, float, bool, type(None))

    def __init__(
        self,
        node_id: str,
        capability: CapabilityDescriptor,
        local_retrieve: Callable[[QueryRequest], Awaitable[list[tuple[Any, float]]]],
        policy_filter: Callable[[Any, AuthorizationContext], PolicyStatus],
        clock: Callable[[], str],
        lineage_resolver: Callable[[Any], Awaitable[dict[str, Any]]] | None = None,
        claim_projection: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        self.node_id = node_id
        self._capability = capability
        self._local_retrieve = local_retrieve
        self._policy_filter = policy_filter
        self._lineage_resolver = lineage_resolver
        self._claim_projection = claim_projection
        self._clock = clock

    async def describe_capabilities(
        self, context: AuthorizationContext
    ) -> tuple[CapabilityDescriptor, ...]:
        """Advertise the capability only to a context holding one of its scopes.

        An owner-declared availability (for example ``DEGRADED``) is preserved
        for an authorized context; an empty ``policy_scope`` stays unavailable.
        """
        from dataclasses import replace

        if any(
            context.permits(self.node_id, scope)
            for scope in self._capability.policy_scope
        ):
            return (self._capability,)
        return (replace(self._capability, availability=Availability.UNAVAILABLE),)

    async def query(self, request: QueryRequest) -> tuple[EvidenceExport, ...]:
        if request.requested_capability != self._capability.capability_id:
            return ()
        results = await self._local_retrieve(request)
        exports = []
        exported_memory_ids: set[str] = set()
        for node, confidence in results:
            # The budget bounds exports, not candidates.  A repeat skipped below
            # never occupied a slot, so a distinct memory that still fits is not
            # evicted by how many times the callback offered its neighbours.  A
            # denial trace is an export and does consume a slot: refusing to
            # answer is still an answer this query has to pay for.
            if len(exports) >= request.budget.max_claims:
                break
            started = self._clock()
            policy = self._policy_filter(node, request.authorization)
            # Identifier only, read without touching any payload field.  It is
            # never exported on a non-allowed path; it only binds the opaque
            # trace digest to one memory so retrieval order cannot alias traces.
            memory_id = self._memory_id(node)
            # One memory exports at most once per query.  A benign callback may
            # union two retrieval paths over the same store, and a repeat is not
            # an error, but exporting it twice would inflate the apparent
            # replica count of a single memory.  First occurrence wins, so the
            # surviving export is whichever path reached the memory first.
            if memory_id in exported_memory_ids:
                continue
            exported_memory_ids.add(memory_id)
            trace_id = MockMemoryNodeAdapter._opaque_id(
                "trace", request.query_id, self.node_id, memory_id
            )
            if policy is not PolicyStatus.ALLOWED:
                exports.append(EvidenceExport(
                    claims=(),
                    trace=RetrievalTrace(
                        trace_id=trace_id,
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
            known = self._exportable_lineage(
                await self._lineage_resolver(node) if self._lineage_resolver else {}
            )
            # A resolver is owner-supplied code and the node's own stored
            # lineage is a rehydrated row, so both are untrusted input here.
            # Every identifier is validated before any envelope is built.
            source_ids = self._exportable_identifiers(
                known.get("source_ids", getattr(node, "source_memory_ids", ())), "source_ids"
            )
            parent_ids = self._exportable_identifiers(
                known.get("parent_memory_ids", ()), "parent_memory_ids"
            )
            root_ids = self._exportable_identifiers(
                known.get("lineage_root_ids", ()), "lineage_root_ids"
            )
            failure_domains = self._exportable_identifiers(
                known.get("failure_domains", ()), "failure_domains"
            )
            edge_path = self._exportable_identifiers(known.get("edge_path", ()), "edge_path")
            retrieval_operator = self._exportable_operator(known)
            content = self._claim_content(node, known)
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
                derivation_operator=retrieval_operator,
                created_at=self._clock(),
            )
            trace = RetrievalTrace(
                trace_id=trace_id,
                query_id=request.query_id,
                node_id=self.node_id,
                memory_ids=(memory_id,), source_ids=source_ids,
                parent_memory_ids=parent_ids, lineage_root_ids=root_ids,
                edge_path=edge_path,
                retrieval_operator=retrieval_operator,
                policy_status=PolicyStatus.ALLOWED,
                started_at=started, completed_at=self._clock(),
            )
            exports.append(EvidenceExport(claims=(claim,), trace=trace))
        return tuple(exports)

    @staticmethod
    def _memory_id(node: Any) -> str:
        """Return the retrieved object's identifier without reading its payload.

        A blank or whitespace-only ``node_id`` is rejected exactly like a
        missing one.  It is not an identifier: two distinct memories carrying it
        would derive the same ``claim_id`` and ``trace_id``, making correlated
        copies indistinguishable from independent roots.  Both messages name
        only the local type, never the rejected object's payload or identifier.

        A ``str`` identifier is read through ``str.__str__`` for the same
        reason: a subclass overriding ``__str__`` could otherwise collapse two
        distinct memories onto one exported identity, and the second one would
        be dropped as a duplicate.
        """
        identifier = getattr(node, "node_id", None)
        if identifier is None:
            raise TypeError(
                f"{type(node).__name__} carries no node_id and cannot be exported"
            )
        memory_id = (
            str.__str__(identifier) if isinstance(identifier, str) else str(identifier)
        )
        if not memory_id.strip():
            raise TypeError(
                f"{type(node).__name__} carries a blank node_id and cannot be exported"
            )
        return memory_id

    @staticmethod
    def _exportable_lineage(known: Any) -> Any:
        """Reject a resolver result the adapter cannot read as a mapping.

        The resolver is owner-supplied code; a non-mapping return would
        otherwise be silently read as "no known lineage", turning a broken
        resolver into a confident claim of no ancestry.
        """
        from collections.abc import Mapping

        if not isinstance(known, Mapping):
            raise ValueError(
                f"lineage resolver must return a mapping, got {type(known).__name__}"
            )
        return known

    @staticmethod
    def _exportable_identifiers(value: Any, field: str) -> tuple[str, ...]:
        """Validate one lineage field, naming the field but never its content.

        ``str``/``bytes`` are rejected instead of iterated: a bare
        ``"memory-42"`` would otherwise cross the boundary as nine
        single-character identifiers.  Entries are re-exported through
        ``str.__str__`` so a ``str`` subclass cannot carry hidden attributes or
        a payload-bearing ``__repr__`` past this point.  Order and multiplicity
        are preserved exactly: normalising them is the resolver's job, and
        silently sorting or de-duplicating here would rewrite the lineage the
        owner actually reported.
        """
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, (tuple, list)):
            raise ValueError(
                f"{field} must be a tuple or list of identifiers, "
                f"got {type(value).__name__}"
            )
        identifiers = []
        for entry in value:
            if not isinstance(entry, str):
                raise ValueError(
                    f"{field} entries must be str, got {type(entry).__name__}"
                )
            if not entry.strip():
                raise ValueError(f"{field} entries must not be blank")
            identifiers.append(str.__str__(entry))
        return tuple(identifiers)

    @staticmethod
    def _exportable_operator(known: Any) -> str:
        """Validate the retrieval operator label the resolver may override."""
        value = known.get("retrieval_operator", "neuralgraph_local_retrieval")
        if not isinstance(value, str):
            raise ValueError(
                f"retrieval_operator must be a str, got {type(value).__name__}"
            )
        if not value.strip():
            raise ValueError("retrieval_operator must not be blank")
        return str.__str__(value)

    def _claim_content(self, node: Any, known: dict[str, Any]) -> dict[str, Any]:
        """Build the exported claim payload.

        An explicit ``claim_projection`` wins over a resolver-supplied
        ``claim_content``; with neither configured the payload stays exactly the
        historical single-field text projection.
        """
        if self._claim_projection is not None:
            content = self._claim_projection(node)
        else:
            content = known.get("claim_content", {"text": str(getattr(node, "content"))})
        return self._exportable_content(content)

    @classmethod
    def _exportable_content(cls, content: Any) -> dict[str, Any]:
        """Reject content the boundary cannot carry, naming keys but never values.

        The copy is deliberate: it stops an owner-supplied projection from
        mutating the payload after it was checked.  Keys *and* values are then
        re-exported as exact builtins: values are the channel that actually
        carries payload, so any subclass surviving here would keep a live
        reference to owner-side state and a caller-controlled ``__repr__`` that
        any log line, dump or traceback would render.  ``str``, ``int`` and
        ``float`` are all subclassable, so each is rebuilt through its *base*
        method (``str.__str__``, ``int.__index__``, ``float.__float__``), which
        reads the real stored value and ignores subclass overrides.  ``bool``
        and ``None`` are singletons that cannot be subclassed, so they pass
        through unchanged -- and ``bool`` is tested before ``int`` because it is
        an ``int`` subclass that must stay ``True``/``False``, not ``1``/``0``.

        Non-finite floats are refused as well.  ``NaN`` and ``Infinity`` are not
        JSON, so exporting them would emit a serialization a conforming parser
        rejects, and the exported bytes are derived from that serialization.
        The refusal stays ahead of the coercion, and both it and the coercion
        read the stored double, so a subclass advertising a finite ``__float__``
        cannot smuggle one past either step.
        """
        import math

        if not isinstance(content, dict):
            raise ValueError(
                f"claim content must be a dict, got {type(content).__name__}"
            )
        exportable: dict[str, Any] = {}
        for key, value in content.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"claim content keys must be str, got {type(key).__name__}"
                )
            if not isinstance(value, cls._EXPORTABLE_VALUE_TYPES):
                raise ValueError(
                    f"claim content value for key {str.__repr__(key)} is not "
                    f"exportable: {type(value).__name__}"
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(
                    f"claim content value for key {str.__repr__(key)} must be finite"
                )
            if isinstance(value, str):
                coerced: Any = str.__str__(value)
            elif value is None or type(value) is bool:
                coerced = value
            elif isinstance(value, int):
                coerced = int.__index__(value)
            else:
                coerced = float.__float__(value)
            exportable[str.__str__(key)] = coerced
        return exportable

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Report whether this node holds support independent of the excluded lineage.

        This is the mechanism lineage-aware repair depends on: after a failure,
        the coordinator asks each candidate whether it can supply the missing
        slots *without* leaning on the roots or domains already known to be
        compromised.  It previously returned a fixed ``valid=False``, so on the
        real adapter no repair could ever succeed and lineage-aware repair
        existed only against the mock.

        It is answered here from this adapter's own real machinery -- the
        owner's retrieval callback, the lineage resolver, and the policy filter
        -- rather than from an injected verification callback, so there is one
        source of truth about what this node can support and no second
        configuration surface that can silently disagree with ``query``.

        Two deliberate differences from ``MockMemoryNodeAdapter.verify``:

        * Only policy-ALLOWED records contribute to the reported
          ``lineage_root_ids`` and ``failure_domains``.  The mock reports the
          lineage of every matching record including denied ones; doing that
          here would export provenance for a record this adapter just refused
          to export, which is precisely the payload-free-denial rule that
          ``RetrievalTrace`` enforces structurally.
        * ``failure_domains`` stays empty whenever the resolver reports none, so
          a domain exclusion cannot be satisfied by an invented domain.  Against
          NeuralGraph storage today that is always: the graph records no
          failure-domain concept, so domain-based exclusion is inert here and
          only root-based exclusion does real work.
        """
        if request.query.requested_capability != self._capability.capability_id:
            return VerificationResult(
                verification_id=request.verification_id,
                node_id=self.node_id,
                valid=False,
                lineage_root_ids=(), failure_domains=(), supported_slots=(),
                reason="capability not served by this node",
            )

        excluded_roots = frozenset(request.excluded_lineage_roots)
        excluded_domains = frozenset(request.excluded_failure_domains)
        required = frozenset(request.required_slots)

        supported: set[str] = set()
        roots: set[str] = set()
        domains: set[str] = set()
        reasons: list[str] = []
        seen_memory_ids: set[str] = set()

        for candidate in await self._local_retrieve(request.query):
            node = candidate[0] if isinstance(candidate, tuple) else candidate
            memory_id = self._memory_id(node)
            if memory_id in seen_memory_ids:
                continue
            seen_memory_ids.add(memory_id)

            if self._policy_filter(node, request.query.authorization) is not PolicyStatus.ALLOWED:
                reasons.append("policy denied")
                continue

            known: dict[str, Any] = {}
            if self._lineage_resolver is not None:
                known = self._exportable_lineage(await self._lineage_resolver(node))
            node_roots = self._exportable_identifiers(
                known.get("lineage_root_ids", ()), "lineage_root_ids"
            )
            node_domains = self._exportable_identifiers(
                known.get("failure_domains", ()), "failure_domains"
            )
            roots.update(node_roots)
            domains.update(node_domains)

            if excluded_roots.intersection(node_roots) or excluded_domains.intersection(node_domains):
                reasons.append("correlated support")
                continue

            slot = None
            if self._claim_projection is not None:
                content = self._exportable_content(self._claim_projection(node))
                slot = content.get("slot")
            if slot is not None and slot in required:
                supported.add(str(slot))

        valid = bool(supported)
        if valid:
            reason = "independent support available"
        elif reasons:
            reason = reasons[0]
        else:
            reason = "no matching support"
        return VerificationResult(
            verification_id=request.verification_id,
            node_id=self.node_id,
            valid=valid,
            lineage_root_ids=tuple(sorted(roots)),
            failure_domains=tuple(sorted(domains)),
            supported_slots=tuple(sorted(supported)),
            reason=reason,
        )

    async def propose_learning(self, signal: LearningSignal) -> LearningDecision:
        return LearningDecision(
            signal_id=signal.signal_id,
            receiver_node_id=self.node_id,
            disposition=LearningDisposition.REJECTED,
            reason="real adapter requires a receiver-owned learning callback",
        )
