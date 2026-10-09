"""HQ's side of the routing spike: the cell view, the routers, one ``SiteProxyAdapter`` per site,
``TesseractCoordinator`` and the shipped gate.

What HQ uses to route (``docs/collective/ROUTING-SPIKE.md`` 2.1): the org's site ids, each site's descriptor (from
``wire``), the k-suppressed cells visible at the candidate's ``as_of`` (``CollectiveStore``, run channel X), the
detector candidate's key, snapshot ``as_of``, window start and ``supporting_sites``, and the question HQ built itself.
It never uses a verdict to route (the routers are stateless and pick every site before any is asked), a record, a
narrative, a site graph, a Tesseract score or a label. The oracle arm O alone is handed the plant spec's sites for a
true candidate; it is labelled an oracle and is in no criterion.

**Rank fusion** here fuses four HQ-side site rankings into one holder ranking (reciprocal rank, constant 60, equal
weights): ``key_now`` (the key's lower-bound record count in the question window, ``'<k'`` counting 1),
``supporting`` (1 for a site in the snapshot's ``supporting_sites``), ``entity_span`` (the entity's lower-bound cell
volume, any predicate, over ``[window start - baseline weeks, window end]``) and ``type_span`` (the same for the entity
type). Per ranking only sites with a value above 0 get a rank, by (value descending, h); the fused score is
``sum 1 / (60 + rank)``; the order is (fused descending, h), with ``h(site) = sha256(tie_salt|question_id|site)``.
Tesseract scores never leave a site, so none is fused here.

Arms (section 4): R (all four), R1 (``key_now`` alone), R0 (``entity_span`` and ``type_span`` with the key's own window
cells subtracted), O (the planted sites first, then R's order), P (R's algorithm with each site scored with another
site's signals, by a permutation seeded from ``placebo|seed|question_id``), A (every eligible site) and U (every
m-site subset of the eligible sites, each its own route).

The coordinator runs with ``preferred_node_ids`` set to the arm's order and ``budget.max_nodes`` set to the route size;
``Router.select`` keeps the eligible sites in that order and takes the first m. Its rule-based synthesis is not the
decision: the shipped gate is (``pushdown.gate.evaluate`` over the routed sites' verdicts, each site's role as D3 sets
it: ``contributing`` when HQ holds a cell of the key in the question window at ``as_of``, else ``sibling``).

This module never imports ``site_process``, numpy, the backend module or the retrieval package (``tests`` check it in a
fresh interpreter).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.detect.store import RUN_CHANNELS, CollectiveStore
from mycelic.collective.edge.egress import check_artifact
from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs.loader import FrozenPack
from mycelic.collective.pushdown import gate
from mycelic.collective.pushdown.questions import build_question, question_window, weeks_back
from NeuralGraph.research.coordination import (AuthorizationContext, Availability, CapabilityDescriptor,
                                               CapabilityRegistry, ClaimEnvelope, ClaimNormalizer, EvidenceExport,
                                               FailureInjector, LearningDecision, LearningDisposition,
                                               LineageAnalyzer, LogicalClock, PolicyStatus, QueryBudget,
                                               QueryRequest, RetrievalTrace, Router, RuleBasedSynthesizer,
                                               TesseractCoordinator, TraceEventType, TraceLogger, VerificationResult)

from . import wire

ARMS = ("R", "U", "A", "R1", "R0", "O", "P")
RANKED_ARMS = ("R", "R1", "R0", "O", "P")
SIGNALS = ("key_now", "supporting", "entity_span", "type_span")
RRF_K = 60
RUN_CHANNEL = "X"
SLOT = "verdict"


# --------------------------------------------------------------------------------------------------- candidates

@dataclass(frozen=True)
class Candidate:
    """The fields of a detector candidate HQ routes on (section 2.2); the rest of the candidate is not read."""

    key: str
    entity_type: str
    entity_id: str
    predicate: str
    as_of: str
    week: str
    window_start: str
    supporting_sites: tuple[str, ...]

    @classmethod
    def from_detector(cls, c: Mapping[str, Any]) -> "Candidate":
        snap = c["snapshot"]
        return cls(key=c["key"], entity_type=c["entity_type"], entity_id=c["entity_id"], predicate=c["predicate"],
                   as_of=snap["as_of"], week=snap["week"], window_start=snap["window"][0],
                   supporting_sites=tuple(snap["supporting_sites"]))


def tie_hash(tie_salt: str, question_id: str, site: str) -> str:
    return sha256_hex(f"{tie_salt}|{question_id}|{site}")


# --------------------------------------------------------------------------------------------------- HQ's view

class HqView:
    """Read-only signals per site from HQ's own cells (run channel X), visible at the candidate's ``as_of``."""

    def __init__(self, store: CollectiveStore, pack: FrozenPack) -> None:
        self.store = store
        self.pack = pack
        self.channels = RUN_CHANNELS[RUN_CHANNEL]

    def signals(self, cand: Candidate, window: Mapping[str, str], sites: Sequence[str]) -> dict[str, dict[str, int]]:
        start, end, as_of = window["start_week"], window["end_week"], cand.as_of
        key_now = {s: 0 for s in sites}
        for c in self.store.key_cells(cand.entity_type, cand.entity_id, cand.predicate, start, end, as_of,
                                      self.channels):
            if c.site in key_now:
                key_now[c.site] += c.n if c.n is not None else 1
        span = weeks_back(start, self.pack.detectors["baseline_weeks"])
        entity = self.store.entity_site_volumes(cand.entity_type, cand.entity_id, span, end, as_of, self.channels)
        types = self.store.type_site_volumes(cand.entity_type, span, end, as_of, self.channels)
        supporting = set(cand.supporting_sites)
        return {"key_now": key_now,
                "supporting": {s: 1 if s in supporting else 0 for s in sites},
                "entity_span": {s: entity.get(s, 0) for s in sites},
                "type_span": {s: types.get(s, 0) for s in sites}}


# --------------------------------------------------------------------------------------------------- routers

def rank(values: Mapping[str, float], sites: Sequence[str], h: Mapping[str, str]) -> dict[str, int]:
    """Ranks 1.. for the sites with a value above 0, by (value descending, h)."""
    ranked = sorted((s for s in sites if values.get(s, 0) > 0), key=lambda s: (-values[s], h[s]))
    return {s: i for i, s in enumerate(ranked, start=1)}


def fuse(rank_maps: Sequence[Mapping[str, int]], sites: Sequence[str], h: Mapping[str, str],
         k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion with equal weights; order (fused descending, h)."""
    score = {s: math.fsum(1.0 / (k + r[s]) for r in rank_maps if s in r) for s in sites}
    return sorted(sites, key=lambda s: (-score[s], h[s]))


def placebo_permutation(sites: Sequence[str], seed: int, question_id: str) -> dict[str, str]:
    """site -> the site whose signals it is scored with."""
    ordered = sorted(sites)
    shuffled = list(ordered)
    random.Random(sha256_hex(f"placebo|{seed}|{question_id}")).shuffle(shuffled)
    return dict(zip(ordered, shuffled))


def order_for(arm: str, signals: Mapping[str, Mapping[str, int]], sites: Sequence[str], h: Mapping[str, str], *,
              seed: int | None = None, question_id: str | None = None,
              oracle_sites: Sequence[str] | None = None) -> list[str]:
    """The full site order an arm prefers (the coordinator takes the first m eligible)."""
    sites = sorted(sites)
    if arm == "R":
        return fuse([rank(signals[name], sites, h) for name in SIGNALS], sites, h)
    if arm == "R1":
        return fuse([rank(signals["key_now"], sites, h)], sites, h)
    if arm == "R0":
        key = signals["key_now"]
        entity = {s: signals["entity_span"][s] - key[s] for s in sites}
        types = {s: signals["type_span"][s] - key[s] for s in sites}
        return fuse([rank(entity, sites, h), rank(types, sites, h)], sites, h)
    if arm == "O":
        r_order = order_for("R", signals, sites, h)
        planted = set(oracle_sites or ())
        return [s for s in r_order if s in planted] + [s for s in r_order if s not in planted]
    if arm == "P":
        if seed is None or question_id is None:
            raise ValueError("the placebo needs the seed and the question id") from None
        sigma = placebo_permutation(sites, seed, question_id)
        permuted = {name: {s: signals[name][sigma[s]] for s in sites} for name in SIGNALS}
        return fuse([rank(permuted[name], sites, h) for name in SIGNALS], sites, h)
    if arm == "A":
        return list(sites)
    raise ValueError("unknown ranked arm") from None


def u_subsets(sites: Sequence[str], m: int) -> list[tuple[str, ...]]:
    """Every m-site subset of the eligible sites, in lexicographic order of the sorted site ids."""
    return list(combinations(sorted(sites), m))


def arm_labels(sites: Sequence[str], m: int) -> list[str]:
    """Every route label of one candidate: A first (the A pass), then R, R1, R0, O, P, then U01, U02, ..."""
    return ["A", *RANKED_ARMS, *(f"U{i:02d}" for i in range(1, len(u_subsets(sites, m)) + 1))]


def plan(view: HqView, pack: FrozenPack, cand: Candidate, sites: Sequence[str], *, tie_salt: str, m: int, seed: int,
         oracle_sites: Sequence[str] | None = None) -> tuple[dict[str, Any], dict[str, dict[str, int]],
                                                             dict[str, list[str]]]:
    """The question, the signals and every route label's order for one candidate, from HQ's cells alone."""
    window = question_window(pack, as_of=cand.as_of, candidate_window_start=cand.window_start)
    question = build_question(pack, entity_type=cand.entity_type, entity_id=cand.entity_id,
                              predicate=cand.predicate, window=window, as_of=cand.as_of)
    qid = question["question_id"]
    sites = sorted(sites)
    h = {s: tie_hash(tie_salt, qid, s) for s in sites}
    signals = view.signals(cand, window, sites)
    orders = {arm: order_for(arm, signals, sites, h, seed=seed, question_id=qid, oracle_sites=oracle_sites)
              for arm in ("A", *RANKED_ARMS)}
    for i, subset in enumerate(u_subsets(sites, min(m, len(sites))), start=1):
        orders[f"U{i:02d}"] = list(subset) + [s for s in sites if s not in subset]
    return question, signals, orders


# --------------------------------------------------------------------------------------------------- intake

@dataclass
class Received:
    """One verdict HQ took in for a (question, site): the site's body (``source`` ``site``), or HQ's own unknown
    record (``source`` ``hq``, reason ``error``) when nothing valid came back."""

    site: str
    question_id: str
    source: str
    body: dict[str, Any]
    sha256: str | None
    problem: str | None = None


class SiteProxyAdapter:
    """The coordination package's ``MemoryNodeAdapter`` for one site, over ``wire`` only."""

    def __init__(self, site_id: str, endpoint: Any, pack: FrozenPack, *, clock: Any,
                 intake_log: str | Path | None = None, descriptor_log: str | Path | None = None) -> None:
        self.node_id = site_id
        self._endpoint = endpoint
        self._pack = pack
        self._clock = clock
        self._intake_log = Path(intake_log) if intake_log is not None else None
        self._descriptor_log = Path(descriptor_log) if descriptor_log is not None else None
        self.descriptor: CapabilityDescriptor | None = None
        self.descriptor_bytes: bytes | None = None
        self.last: dict[str, Received] = {}

    def describe(self) -> CapabilityDescriptor:
        """Ask the site for its descriptor once; checked against the closed schema on this side too."""
        data = self._endpoint.request("describe", b"")
        obj = wire.parse_descriptor(data, site_id=self.node_id, pack_id=self._pack.id,
                                    config_hash=self._pack.config_hash, template_ids=sorted(self._pack.questions))
        if self._descriptor_log is not None:
            with open(self._descriptor_log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(canonical_dumps({"body": obj, "bytes": len(data), "sha256": sha256_hex(data),
                                          "site": self.node_id}) + "\n")
        self.descriptor_bytes = data
        self.descriptor = CapabilityDescriptor(
            capability_id=obj["capability_id"], node_id=obj["node_id"], description=obj["description"],
            query_types=tuple(obj["query_types"]), policy_scope=tuple(obj["policy_scope"]),
            availability=Availability(obj["availability"]))
        return self.descriptor

    async def describe_capabilities(self, context: AuthorizationContext) -> tuple[CapabilityDescriptor, ...]:
        if self.descriptor is None:
            return ()
        if any(context.permits(self.node_id, scope) for scope in self.descriptor.policy_scope):
            return (self.descriptor,)
        from dataclasses import replace
        return (replace(self.descriptor, availability=Availability.UNAVAILABLE),)

    def _intake(self, question: Mapping[str, Any], data: bytes | None, problem: str | None) -> Received:
        qid = question["question_id"]
        if data is not None and problem is None:
            body = None
            try:
                body = strict_load(data)
            except StrictJsonError:
                problem = "json"
            if body is not None:
                found = check_artifact(self._pack, self.node_id, "verdict", body)
                if found is not None or canonical_bytes(body) != data:
                    problem = "invalid"
                elif body["question_id"] != qid:
                    problem = "question_id"
                elif body["pack_hash"] != question["pack_hash"]:
                    problem = "pack_hash"
                elif body["window"] != question["window"]:
                    problem = "window"
            if problem is None:
                received = Received(self.node_id, qid, "site", body, sha256_hex(data))
        if problem is not None:
            received = Received(self.node_id, qid, "hq", gate.hq_record_body(qid, self.node_id, "error"), None,
                                problem)
        if self._intake_log is not None:
            with open(self._intake_log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(canonical_dumps({"problem": received.problem, "question_id": qid,
                                          "sha256": received.sha256, "site": self.node_id,
                                          "source": received.source}) + "\n")
        self.last[qid] = received
        return received

    async def query(self, request: QueryRequest) -> tuple[EvidenceExport, ...]:
        if self.descriptor is None or request.requested_capability != self.descriptor.capability_id:
            return ()
        qbytes = request.content.encode("utf-8")
        question = strict_load(qbytes)
        started = self._clock()
        try:
            data, problem = self._endpoint.request("question", qbytes), None
        except wire.WireError as err:
            data, problem = None, f"wire:{err.code}"
        received = self._intake(question, data, problem)
        body = received.body
        content: dict[str, Any] = {"slot": SLOT, "value": body["verdict"], "source": received.source}
        if received.source == "site":
            content.update({"support_bucket": body["support_bucket"], "roots_bucket": body["roots_bucket"],
                            "reporters_bucket": body["reporters_bucket"], "newest_week": body["newest_week"]})
            root = f"{self.node_id}:{body['verdict_id']}"
            refs: tuple[str, ...] = (body["evidence_ref"],) if body["evidence_ref"] is not None else ()
        else:
            root = f"{self.node_id}:hq-error:{question['question_id'][:16]}"
            refs = ()
        claim = ClaimEnvelope(
            claim_id="claim_" + sha256_hex(f"{request.query_id}|{self.node_id}")[:20], query_id=request.query_id,
            producer_node_id=self.node_id, content=content, confidence=1.0, evidence_refs=refs, source_ids=(),
            parent_memory_ids=(), lineage_root_ids=(root,), failure_domains=(self.node_id,),
            policy_status=PolicyStatus.ALLOWED, created_at=self._clock(), derivation_operator="pushdown_verdict")
        trace = RetrievalTrace(
            trace_id="trace_" + sha256_hex(f"{request.query_id}|{self.node_id}|trace")[:20], query_id=request.query_id,
            node_id=self.node_id, memory_ids=(), source_ids=(), parent_memory_ids=(), lineage_root_ids=(root,),
            edge_path=(), retrieval_operator="pushdown_verdict", policy_status=PolicyStatus.ALLOWED,
            started_at=started, completed_at=self._clock())
        return (EvidenceExport(claims=(claim,), trace=trace),)

    async def verify(self, request: Any) -> VerificationResult:
        return VerificationResult(verification_id=request.verification_id, node_id=self.node_id, valid=False,
                                  lineage_root_ids=(), failure_domains=(), supported_slots=(),
                                  reason="not used by the spike")

    async def propose_learning(self, signal: Any) -> LearningDecision:
        return LearningDecision(signal_id=signal.signal_id, receiver_node_id=self.node_id,
                                disposition=LearningDisposition.REJECTED, reason="not used by the spike")


# --------------------------------------------------------------------------------------------------- HQ

@dataclass
class ArmResult:
    """One route of one candidate: what HQ asked, what it took in, and the gate's status."""

    label: str
    route: tuple[str, ...]
    roles: dict[str, str]
    received: list[Received]
    status: str
    gate: dict[str, Any]
    exported_bytes: int
    route_selected_first: bool
    trace: list[str] = field(default_factory=list)


@dataclass
class Asked:
    """One candidate after every arm: the question and each route's result."""

    candidate: Candidate
    question: dict[str, Any]
    question_bytes: bytes
    eligible: tuple[str, ...]
    orders: dict[str, list[str]]
    signals: dict[str, dict[str, int]]
    results: dict[str, ArmResult] = field(default_factory=dict)


class Hq:
    """HQ for one world: proxies, the registry and one coordinator; the routers; the gate."""

    def __init__(self, pack: FrozenPack, store: CollectiveStore, endpoints: Mapping[str, Any], *, hq_dir: str | Path,
                 tie_salt: str, m: int, seed: int, clock_seed: int = 0) -> None:
        self.pack = pack
        self.store = store
        self.view = HqView(store, pack)
        self.tie_salt = tie_salt
        self.m = m
        self.seed = seed
        hq_dir = Path(hq_dir)
        hq_dir.mkdir(parents=True, exist_ok=True)
        self._clock = LogicalClock(clock_seed)
        self.traces = TraceLogger(f"routing-spike-{seed}", self._clock)
        failure = FailureInjector(f"routing-spike-{seed}", seed, self._clock, self.traces)
        self.registry = CapabilityRegistry()
        self.proxies: dict[str, SiteProxyAdapter] = {}
        for site in sorted(endpoints):
            proxy = SiteProxyAdapter(site, endpoints[site], pack, clock=self._clock.now,
                                     intake_log=hq_dir / "intake.jsonl", descriptor_log=hq_dir / "descriptors.jsonl")
            self.proxies[site] = proxy
            self.registry.register(proxy)
        self.coordinator = TesseractCoordinator(self.registry, Router(self.registry, failure), ClaimNormalizer(),
                                                RuleBasedSynthesizer((SLOT,)), LineageAnalyzer(), self.traces)
        self.capability_id = wire.capability_id(pack.id, pack.config_hash)
        self.authorization = AuthorizationContext(scopes=wire.POLICY_SCOPE)

    def describe_all(self) -> dict[str, bytes]:
        out = {}
        for site, proxy in self.proxies.items():
            proxy.describe()
            out[site] = proxy.descriptor_bytes or b""
        return out

    def eligible(self) -> tuple[str, ...]:
        """E: the sites whose descriptor offers the capability and is not unavailable (the registry's rule)."""
        return tuple(sorted(s for s, p in self.proxies.items() if p.descriptor is not None
                            and p.descriptor.capability_id == self.capability_id
                            and p.descriptor.availability is not Availability.UNAVAILABLE
                            and any(self.authorization.permits(s, scope) for scope in p.descriptor.policy_scope)))

    def prepare(self, cand: Candidate, *, oracle_sites: Sequence[str] | None = None) -> Asked:
        """The question and every arm's order, before any site is asked."""
        sites = self.eligible()
        question, signals, orders = plan(self.view, self.pack, cand, sites, tie_salt=self.tie_salt, m=self.m,
                                         seed=self.seed, oracle_sites=oracle_sites)
        return Asked(candidate=cand, question=question, question_bytes=canonical_bytes(question), eligible=sites,
                     orders=orders, signals=signals)

    def replace_endpoints(self, endpoints: Mapping[str, Any]) -> None:
        """Per-arm mode (section 4): fresh site state behind the same proxies."""
        for site, proxy in self.proxies.items():
            proxy._endpoint = endpoints[site]

    def size(self, label: str, eligible: Sequence[str]) -> int:
        return len(eligible) if label == "A" else min(self.m, len(eligible))

    async def ask(self, asked: Asked, label: str) -> ArmResult:
        """Run one route through the coordinator and the gate."""
        qid = asked.question["question_id"]
        size = self.size(label, asked.eligible)
        request = QueryRequest(query_id=qid, content=asked.question_bytes.decode("utf-8"), requester_id="hq",
                               issued_at=f"{asked.candidate.as_of}T12:00:00Z", authorization=self.authorization,
                               requested_capability=self.capability_id,
                               budget=QueryBudget(max_nodes=size, max_claims=max(size, 1), max_verifications=0))
        before = len(self.traces.events)
        execution = await self.coordinator.execute(request, phase=label,
                                                   preferred_node_ids=tuple(asked.orders[label]))
        trace = [e for e in self.traces.events[before:] if e.trace_id == f"query_trace_{qid}_{label}"]
        events = [e.event_type for e in trace]
        selected = events.index(TraceEventType.ROUTE_SELECTED) if TraceEventType.ROUTE_SELECTED in events else None
        retrieved = [i for i, e in enumerate(events) if e is TraceEventType.RETRIEVAL_SELECTED]
        first = selected is not None and all(selected < i for i in retrieved)
        route = tuple(execution.route)
        roles = {s: ("contributing" if asked.signals["key_now"][s] > 0 else "sibling") for s in route}
        received = [self.proxies[s].last[qid] for s in route]
        records = [gate.VerdictRecord(site=r.site, seq=1, source=r.source, body=r.body,
                                      received_as_of=asked.candidate.as_of) for r in received]
        result = gate.evaluate(self.pack, asked.question, roles, records, as_of=asked.candidate.as_of)
        out = ArmResult(label=label, route=route, roles=roles, received=received, status=result.status,
                        gate=result.to_dict(), exported_bytes=execution.exported_bytes, route_selected_first=first,
                        trace=[e.value for e in events])
        asked.results[label] = out
        return out
