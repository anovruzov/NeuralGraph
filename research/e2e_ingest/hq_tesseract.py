"""HQ with Tesseract routing, and the HQ-store rows every question leaves behind (S5; ``E2E-INGEST.md`` sections 1,
4 and 6, M4).

The routing is the spike's, imported and unchanged: :class:`~research.routing_spike.hq.Hq` builds one
``SiteProxyAdapter`` per site over ``wire``, fuses HQ's own cell signals into arm R's order, and hands that order to
``TesseractCoordinator.execute`` as ``preferred_node_ids`` with ``budget.max_nodes = m``; ``Router.select`` decides
eligibility and cuts the route; the shipped gate decides the status. This module adds two things the end-to-end run
needs and the spike did not:

* :class:`E2EHq` keeps the ``FailureInjector`` that ``Hq.__init__`` builds and drops (``hq.py:371``) and rebuilds the
  coordinator with it, so drill 2 can mask one site's route while its process is down and check that the exclusion
  was the Router's (M4, check 3). The six constructor arguments are the spike's, in the spike's order.
* :func:`record_question` writes what HQ asked and took in into the pipeline's own ``CollectiveStore``:
  ``pd_questions`` (the exact question bytes and HQ's display text), ``pd_routes`` (one row per routed site in route
  order with the gate's role), ``pd_verdicts`` (one row per ``Received``, the site's exact bytes or HQ's own error
  record) and ``pd_conclusions`` (version 1, the gate's status and body). :func:`save_detection_run` stores the
  detection run, whose ``candidates`` rows carry the snapshot week: the bridge reads it from there and refuses a
  question without it (``hq_bridge``'s contract).

HQ never sees a record, a narrative, a graph node or a Tesseract score: this module imports neither the site
modules, nor the retrieval package, nor numpy, nor ``mycelic.collective.edge.records`` / ``.site`` / ``.verify``, nor
``mycelic.collective.evaluate`` (``FORBIDDEN_HQ``, ``research/routing_spike/run.py:80-83``, plus
``research.e2e_ingest.site_graph``); the harness checks it statically and in a fresh interpreter (criterion 7).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.detect.store import CollectiveStore, ConclusionRow, QuestionRow, RouteRow
from mycelic.collective.jsonio import canonical_bytes, sha256_hex
from mycelic.collective.packs.loader import FrozenPack
from mycelic.collective.pushdown.questions import render_text
from NeuralGraph.research.coordination import (ClaimNormalizer, FailureInjector, InterventionRecord,
                                               InterventionTarget, LineageAnalyzer, Router, RuleBasedSynthesizer,
                                               TesseractCoordinator, TraceEventType)
from research.routing_spike.hq import SLOT, ArmResult, Asked, Candidate, Hq

__all__ = ["CONCLUSION_PREFIX", "ROUTE_LABEL", "E2EHq", "ProbeResult", "StoredQuestion", "conclusion_id_of",
           "record_question", "save_detection_run", "Candidate"]

ROUTE_LABEL = "R"
CONCLUSION_PREFIX = "c-"


class HqError(RuntimeError):
    """HQ could not write what it asked (a refused verdict row, a conclusion already stored)."""


def conclusion_id_of(question_id: str) -> str:
    """``"c-" + question_id[:32]``, as ``research/routing_spike/publish.py:76`` builds it."""
    return CONCLUSION_PREFIX + question_id[:32]


# --------------------------------------------------------------------------------------------------- HQ

class E2EHq(Hq):
    """The spike's ``Hq`` with its failure injector kept and the coordinator rebuilt around it (M4, check 3).

    ``Hq.__init__`` is run unchanged; the coordinator it built is then replaced by one with the same six arguments
    (``hq.py:379-380``) whose ``Router`` holds ``self.failure``. The registry, the proxies, the trace logger, the
    clock, the rank fusion and the gate are the spike's own objects."""

    def __init__(self, pack: FrozenPack, store: CollectiveStore, endpoints: Mapping[str, Any], *,
                 hq_dir: str | Path, tie_salt: str, m: int, seed: int, clock_seed: int = 0) -> None:
        super().__init__(pack, store, endpoints, hq_dir=hq_dir, tie_salt=tie_salt, m=m, seed=seed,
                         clock_seed=clock_seed)
        self.failure = FailureInjector(f"e2e-ingest-{seed}", seed, self._clock, self.traces)
        self.coordinator = TesseractCoordinator(self.registry, Router(self.registry, self.failure), ClaimNormalizer(),
                                                RuleBasedSynthesizer((SLOT,)), LineageAnalyzer(), self.traces)

    # ---- the endpoints
    def replace_endpoint(self, site: str, endpoint: Any) -> None:
        """Hand one site's proxy a new endpoint (drill 2 reopens a killed child process); the proxy's descriptor is
        cached, so HQ's own eligibility does not change (``hq.py:391-396``)."""
        self.proxies[site]._endpoint = endpoint

    # ---- the eligibility exercise of M4
    def failure_injected(self) -> int:
        return sum(1 for e in self.traces.events if e.event_type is TraceEventType.FAILURE_INJECTED)

    def intervention_records(self) -> tuple[InterventionRecord, ...]:
        return self.failure.records

    async def probe_masked(self, cand: Candidate, site: str, *, reason: str) -> "ProbeResult":
        """M4, check 3: with ``site``'s route masked, ask one probe question through ``hq.ask`` alone — nothing is
        written to the HQ store and nothing is published. Returns what the checks need."""
        before_injected = self.failure_injected()
        record = self.failure.apply(InterventionTarget.ROUTE, site, reason=reason)
        try:
            asked = self.prepare(cand)
            result = await self.ask(asked, ROUTE_LABEL)
        finally:
            self.failure.clear(InterventionTarget.ROUTE, site)
        ended = [r for r in self.failure.records if r.intervention_id == record.intervention_id and r.ended_at]
        return ProbeResult(question_id=asked.question["question_id"], masked_site=site, route=tuple(result.route),
                           eligible=len(asked.eligible), trace=list(result.trace),
                           sources=sorted({r.source for r in result.received}),
                           failure_injected=self.failure_injected() - before_injected,
                           intervention_records=len(self.failure.records), interventions_ended=len(ended))


@dataclass(frozen=True)
class ProbeResult:
    """The probe of M4, check 3: the route the Router chose with one site masked, and the witnesses of the mask."""

    question_id: str
    masked_site: str
    route: tuple[str, ...]
    eligible: int
    trace: list[str]
    sources: list[str]
    failure_injected: int
    intervention_records: int
    interventions_ended: int

    @property
    def excluded(self) -> bool:
        return self.masked_site not in self.route

    def to_dict(self) -> dict[str, Any]:
        return {"question_id": self.question_id, "masked_site": self.masked_site, "route": list(self.route),
                "hq_eligible": self.eligible, "route_size": len(self.route), "excluded": self.excluded,
                "trace": list(self.trace), "verdict_sources": list(self.sources),
                "failure_injected": self.failure_injected, "intervention_records": self.intervention_records,
                "interventions_ended": self.interventions_ended}


# --------------------------------------------------------------------------------------------------- the store

def save_detection_run(store: CollectiveStore, result: Mapping[str, Any]) -> str:
    """Store the detection run (idempotent, ``store.py:558``) and return its ``run_id``: the candidates' rows carry
    the snapshot week the conclusion memory needs, and ``pd_questions.run_id`` names this run."""
    store.save_run(result)
    return str(result["run_id"])


@dataclass(frozen=True)
class StoredQuestion:
    """What one question left in HQ's store."""

    question_id: str
    candidate_key: str
    conclusion_id: str
    status: str
    route: tuple[str, ...]
    roles: dict[str, str]
    verdict_rows: int
    verdict_outcomes: dict[str, str]
    sources: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"question_id": self.question_id, "candidate_key": self.candidate_key,
                "conclusion_id": self.conclusion_id, "status": self.status, "route": list(self.route),
                "roles": dict(self.roles), "verdict_rows": self.verdict_rows,
                "verdict_outcomes": dict(self.verdict_outcomes), "sources": dict(self.sources)}


def record_question(store: CollectiveStore, pack: FrozenPack, asked: Asked, result: ArmResult, *, run_id: str,
                    clock: Callable[[], str], version: int = 1) -> StoredQuestion:
    """Write the question, its route, every verdict HQ took in and the conclusion, in the orchestrator's shape.

    The question's body is the exact bytes HQ asked with; ``pd_routes`` is one row per routed site in route order
    with the gate's role; ``pd_verdicts`` is one row per ``Received`` (the site's own bytes, or HQ's error record,
    whose sha256 the store recomputes, ``store.py:628-633``); the conclusion is the gate's status and its
    ``to_dict`` body. A verdict row the store refuses is a :class:`HqError`: nothing is guessed later."""
    question = asked.question
    qid = question["question_id"]
    as_of = asked.candidate.as_of
    window = question["window"]
    store.add_question(
        QuestionRow(question_id=qid, candidate_key=question["candidate_key"], run_id=run_id,
                    template_id=question["template_id"], as_of=question["as_of"],
                    window_start=window["start_week"], window_end=window["end_week"],
                    pack_hash=question["pack_hash"], body=asked.question_bytes,
                    display_text=render_text(pack, question), created_at=clock()),
        tuple(RouteRow(qid, site, result.roles[site], rank) for rank, site in enumerate(result.route, start=1)))
    outcomes: dict[str, str] = {}
    sources: dict[str, str] = {}
    rows = 0
    for received in result.received:
        body = canonical_bytes(received.body)
        outcome = store.append_verdict(question_id=qid, site=received.site, source=received.source,
                                       verdict=received.body["verdict"], reason=received.body.get("reason"),
                                       body=body, received_as_of=as_of)
        if outcome != "accepted":
            raise HqError(f"HQ's store refused the verdict of {received.site} for {qid[:12]}: {outcome}")
        outcomes[received.site] = outcome
        sources[received.site] = received.source
        rows += 1
    doc = canonical_bytes(result.gate)
    cid = conclusion_id_of(qid)
    store.add_conclusion(ConclusionRow(conclusion_id=cid, version=version, question_id=qid, as_of=as_of,
                                       status=result.status, body=doc, sha256=sha256_hex(doc), created_at=clock()))
    return StoredQuestion(question_id=qid, candidate_key=question["candidate_key"], conclusion_id=cid,
                          status=result.status, route=tuple(result.route), roles=dict(result.roles),
                          verdict_rows=rows, verdict_outcomes=outcomes, sources=sources)


def route_of(orders: Mapping[str, Sequence[str]], eligible: Sequence[str], m: int,
             label: str = ROUTE_LABEL) -> tuple[str, ...]:
    """The route the coordinator will choose when every eligible site answers: the first ``m`` of the arm's order
    that are eligible (``Router.select``, ``coordination/core.py:230-241``). Used to plan the run (which site holds
    the most routed questions, drill 2); every check reads the route the Router actually chose."""
    preferred = [s for s in orders[label] if s in set(eligible)]
    return tuple(preferred[:m])
