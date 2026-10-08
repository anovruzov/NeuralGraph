"""The pushdown orchestrator at HQ: route a question, deliver it with a deadline, take the verdicts in, keep the
versioned conclusions (G6).

:meth:`Orchestrator.verify_stored` is the production path (a candidate HQ's detection stored);
:meth:`Orchestrator.verify_candidate` is the documented test path for any well-formed candidate, such as one
:func:`constructed_candidate` builds from HQ's cells. Both run the same steps:

1. **Question.** The window is :func:`~.questions.question_window` at ``as_of`` (the candidate's window start, widened
   to ``min_window_weeks``); the body comes from :func:`~.questions.build_question`. A question id already stored is
   reused with its stored body and routes, so re-asking is idempotent.
2. **Routes** (D3). Contributing sites are computed from the cells visible at ``as_of``: a site of the current org with
   a cell of the key, in the run channel's cell channels, inside the question window. Verified at the snapshot's own
   ``as_of`` this is the snapshot's ``contributing_sites``. Siblings come from the span of ``baseline_weeks`` before
   the window to its end, contributing sites excluded: first the sites with any cell of the entity (by its
   lower-bound volume, descending, then site id), then the sites with cells of the entity type (by type volume, then
   site id); the first ``max_sibling_sites`` are kept.
3. **Delivery.** The routed sites are asked one at a time, in sorted site order: each site's handler (``site id ->
   callable(question body) -> verdict body``) runs on a daemon thread joined against the deadline
   (``deadline_seconds``), so the sites' log lines and HQ's rows come in a fixed order and a hung site delays the
   others by at most the deadline. A missing handler or an exception is an HQ record ``unknown`` with reason
   ``error``; a handler still running at the deadline is an HQ record with reason ``timeout``, and its thread is
   registered as late; a returned body goes through :meth:`Orchestrator.receive_verdict`, and a refused one also gets
   an HQ ``error`` record. No SQLite object is used off the calling thread; the clock is read only for
   ``received_at``.
4. **Gate.** :meth:`Orchestrator.regate` evaluates the gate over every verdict received at or before ``as_of`` and
   appends a conclusion version only when ``{status, reasons, used}`` changed; earlier versions stay readable.

Intake (:meth:`Orchestrator.receive_verdict`), the first failing check decides: ``invalid`` (the Boundary's own
validator, with its path and keyword), ``unknown_question``, ``unrouted``, ``pack_hash`` (not the question's),
``window`` (not the question's). A refusal is one ``pd_verdict_rejections`` row and nothing else. A body whose sha256
equals the site's latest verdict for the question is a ``duplicate`` (nothing written); any other body is appended
with the next ``seq`` and supersedes the earlier one in the gate.

Late verdicts: :meth:`Orchestrator.collect_late` takes in the body of every late thread that has finished, received
at the collection's ``as_of``, and re-gates those questions there; re-gating at an ``as_of`` before the latest
version is refused, and the gate never sees a verdict received after its ``as_of``. :meth:`Orchestrator.join_late`
waits a bounded time for the late threads, for a caller (E2) that must not score a deadline. A site answers one
question at a time (its verifier's lock), so a question sent to a site still busy with a late one waits behind it,
and that wait counts against the new question's deadline.

A conclusion is ``c-`` plus the first 32 hex of the question id, versioned from 1; its body holds the gate result, the
pack hash and gate parameters, the decision unit (of the confirming sites, else the contributing ones) and the
lineage candidate -> cells -> question -> verdicts. One writer per store.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..detect.org import SITE_ID_RE
from ..detect.rules import series_key
from ..detect.store import RUN_CHANNELS, CellRow, CollectiveStore, ConclusionRow, QuestionRow, RouteRow
from ..edge.egress import check_artifact
from ..edge.weeks import TS_RE, closed_through, local_date, valid_week, week_sunday
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from . import gate
from .questions import PushdownError, build_question, check_params, question_window, render_text, weeks_back

SCHEMA_VERSION = 1
CONCLUSION_PREFIX = "c-"
MAX_DEADLINE_SECONDS = 3600.0
RECEIVED = ("accepted", "duplicate")
REJECT_REASONS = ("invalid", "unknown_question", "unrouted", "pack_hash", "window")


@dataclass(frozen=True)
class Conclusion:
    conclusion_id: str
    version: int
    question_id: str
    as_of: str
    status: str
    reasons: tuple[str, ...]
    body: Mapping[str, Any]


@dataclass(frozen=True)
class _Candidate:
    key: str
    entity_type: str
    entity_id: str
    predicate: str
    run_channel: str
    window_start: str
    snapshot_as_of: str | None
    rule_first_week: str | None
    lineage: tuple[Any, ...]


@dataclass(frozen=True)
class _Late:
    question_id: str
    site: str
    thread: threading.Thread
    holder: dict[str, Any]


def _date(value: Any, path: str) -> str:
    if not isinstance(value, str) or local_date(value) != value:
        raise PushdownError(path, "not a calendar date") from None
    return value


def _lineage_item(c: CellRow) -> dict[str, Any]:
    return {"site": c.site, "entity_type": c.entity_type, "entity_id": c.entity_id, "predicate": c.predicate,
            "iso_week": c.iso_week, "channel": c.channel, "bundle": c.bundle}


def _conclusion(row: ConclusionRow) -> Conclusion:
    body = strict_load(row.body)
    return Conclusion(conclusion_id=row.conclusion_id, version=row.version, question_id=row.question_id,
                      as_of=row.as_of, status=row.status, reasons=tuple(body["reasons"]), body=body)


def _call(handler: Callable[[dict[str, Any]], Any], body: dict[str, Any], holder: dict[str, Any]) -> None:
    try:
        holder["result"] = handler(body)
    except Exception:            # an error at the site is an HQ record; nothing of it is kept
        holder["error"] = True


def constructed_candidate(store: CollectiveStore, *, entity_type: str, entity_id: str, predicate: str, as_of: str,
                          run_channel: str = "X", window_start: str | None = None) -> dict[str, Any]:
    """A candidate built from HQ's cells, for the test path: its snapshot week is the last week closed at ``as_of``,
    its window ``[window_start or week - (window_weeks - 1), week]``, and its contributing sites and lineage are the
    key's visible cells there. ``score`` is None and ``constructed`` true."""
    pack = store.pack
    check_params(pack, entity_type, entity_id, predicate)
    _date(as_of, "$.as_of")
    if run_channel not in RUN_CHANNELS:
        raise PushdownError("$.run_channel", "must be X or S") from None
    week = closed_through(as_of, pack.egress.close_lag_days)
    start = window_start if window_start is not None else weeks_back(week, pack.detectors["window_weeks"] - 1)
    if not valid_week(start) or start > week:
        raise PushdownError("$.window_start", "must be an ISO week at or before the last closed week") from None
    cells = store.key_cells(entity_type, entity_id, predicate, start, week, as_of, RUN_CHANNELS[run_channel])
    return {"key": series_key(entity_type, entity_id, predicate), "entity_type": entity_type, "entity_id": entity_id,
            "predicate": predicate, "run_channel": run_channel,
            "snapshot": {"week": week, "as_of": as_of, "window": [start, week], "score": None,
                         "contributing_sites": sorted({c.site for c in cells}),
                         "lineage": [_lineage_item(c) for c in cells]},
            "rule": None, "constructed": True}


class Orchestrator:
    def __init__(self, store: CollectiveStore, *, handlers: Mapping[str, Callable[[dict[str, Any]], Any]],
                 clock: Callable[[], str], deadline_seconds: float = 600.0) -> None:
        if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float))
                or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= MAX_DEADLINE_SECONDS):
            raise PushdownError("$.deadline_seconds", "must be a finite number in (0, 3600]") from None
        for site in handlers:
            if not isinstance(site, str) or SITE_ID_RE.fullmatch(site) is None or not callable(handlers[site]):
                raise PushdownError("$.handlers", "must map site ids to callables") from None
        self.store = store
        self.pack = store.pack
        self.org = store.org
        self.handlers = MappingProxyType(dict(handlers))
        self.deadline_seconds = float(deadline_seconds)
        self._clock = clock
        self._late: list[_Late] = []

    def _now(self) -> str:
        ts = self._clock()
        if not isinstance(ts, str) or TS_RE.fullmatch(ts) is None or local_date(ts) is None:
            raise ValueError("the clock must return an ISO 8601 timestamp") from None
        return ts

    # ------------------------------------------------------------------ candidates
    def _candidate(self, candidate: Any) -> _Candidate:
        """A well-formed candidate, or :class:`PushdownError` naming the first problem; nothing is written."""
        if not isinstance(candidate, dict):
            raise PushdownError("$", "not a candidate object") from None
        for key in ("key", "entity_type", "entity_id", "predicate", "run_channel"):
            if key not in candidate:
                raise PushdownError(f"$.{key}", "missing key") from None
        t, eid, p = candidate["entity_type"], candidate["entity_id"], candidate["predicate"]
        check_params(self.pack, t, eid, p)
        if candidate["key"] != series_key(t, eid, p):
            raise PushdownError("$.key", "not the series key of the entity and predicate") from None
        if candidate["run_channel"] not in RUN_CHANNELS:
            raise PushdownError("$.run_channel", "must be X or S") from None
        snapshot, rule = candidate.get("snapshot"), candidate.get("rule")
        if snapshot is not None:
            part, path, fields = snapshot, "$.snapshot", ("week", "as_of", "window", "lineage")
        elif rule is not None:
            part, path, fields = rule, "$.rule", ("first_week", "window", "lineage")
        else:
            raise PushdownError("$.snapshot", "a candidate needs a snapshot or a rule") from None
        if not isinstance(part, dict):
            raise PushdownError(path, "must be an object") from None
        for key in fields:
            if key not in part:
                raise PushdownError(f"{path}.{key}", "missing key") from None
        week = part["week"] if snapshot is not None else part["first_week"]
        if not valid_week(week):
            raise PushdownError(f"{path}.{fields[0]}", "not an ISO week") from None
        if snapshot is not None:
            _date(part["as_of"], f"{path}.as_of")
        window = part["window"]
        if (not isinstance(window, list) or len(window) != 2 or not all(valid_week(w) for w in window)
                or window[0] > window[1]):
            raise PushdownError(f"{path}.window", "must be two ISO weeks, start first") from None
        lineage = part["lineage"]
        if not isinstance(lineage, list) or not all(isinstance(c, dict) and c.get("site") in self.org.sites
                                                     for c in lineage):
            raise PushdownError(f"{path}.lineage", "must list cells of sites in the org") from None
        return _Candidate(key=candidate["key"], entity_type=t, entity_id=eid, predicate=p,
                          run_channel=candidate["run_channel"], window_start=window[0],
                          snapshot_as_of=part["as_of"] if snapshot is not None else None,
                          rule_first_week=None if snapshot is not None else week,
                          lineage=tuple(strict_load(canonical_bytes(lineage))))

    def _born(self, cand: _Candidate, run_as_of: str | None) -> str:
        """The earliest ``as_of`` the candidate existed at: its snapshot's ``as_of``; for a rule-only candidate
        ``min(run as_of, sunday(first week) + lag + 6)``, or the first week's closing date without a run."""
        if cand.snapshot_as_of is not None:
            return cand.snapshot_as_of
        lag = self.pack.egress.close_lag_days
        sunday = week_sunday(cand.rule_first_week)
        if run_as_of is None:
            return (sunday + timedelta(days=lag)).isoformat()
        return min(run_as_of, (sunday + timedelta(days=lag + 6)).isoformat())

    def verify_stored(self, run_id: str, key: str, *, as_of: str | None = None) -> Conclusion:
        """Verify a candidate HQ's detection stored, at ``as_of`` (default: when it existed)."""
        raw = self.store.candidate(run_id, key) if isinstance(run_id, str) and isinstance(key, str) else None
        if raw is None:
            raise PushdownError("$.key", "no stored candidate for this run and key") from None
        cand = self._candidate(raw)
        born = self._born(cand, self.store.run_as_of(run_id))
        as_of = born if as_of is None else _date(as_of, "$.as_of")
        if as_of < born:
            raise PushdownError("$.as_of", "before the candidate existed") from None
        return self._verify(cand, as_of, run_id=run_id)

    def verify_candidate(self, candidate: Any, *, as_of: str) -> Conclusion:
        """The test path: verify any well-formed candidate (not stored) at ``as_of``."""
        cand = self._candidate(candidate)
        _date(as_of, "$.as_of")
        if as_of < self._born(cand, None):
            raise PushdownError("$.as_of", "before the candidate existed") from None
        return self._verify(cand, as_of, run_id=None)

    # ------------------------------------------------------------------ routing and delivery
    def _routes(self, cand: _Candidate, question_id: str, window: Mapping[str, str],
                as_of: str) -> tuple[RouteRow, ...]:
        channels = RUN_CHANNELS[cand.run_channel]
        start, end = window["start_week"], window["end_week"]
        cells = self.store.key_cells(cand.entity_type, cand.entity_id, cand.predicate, start, end, as_of, channels)
        contributing = sorted({c.site for c in cells})
        span = weeks_back(start, self.pack.detectors["baseline_weeks"])
        entity = self.store.entity_site_volumes(cand.entity_type, cand.entity_id, span, end, as_of, channels)
        types = self.store.type_site_volumes(cand.entity_type, span, end, as_of, channels)
        order = [s for s in sorted(entity, key=lambda s: (-entity[s], s)) if s not in contributing]
        order += [s for s in sorted(types, key=lambda s: (-types[s], s)) if s not in contributing and s not in entity]
        siblings = order[:self.pack.pushdown.max_sibling_sites]
        return (tuple(RouteRow(question_id, s, "contributing", rank) for rank, s in enumerate(contributing, start=1))
                + tuple(RouteRow(question_id, s, "sibling", rank)
                        for rank, s in enumerate(siblings, start=len(contributing) + 1)))

    def _hq_record(self, question_id: str, site: str, reason: str, as_of: str) -> None:
        body = gate.hq_record_body(question_id, site, reason)
        self.store.append_verdict(question_id=question_id, site=site, source="hq", verdict="unknown", reason=reason,
                                  body=canonical_bytes(body), received_as_of=as_of)

    def _deliver(self, question: dict[str, Any], routes: Mapping[str, str], as_of: str) -> None:
        """One site at a time, in sorted site order, so the sites' log lines and HQ's rows come in a fixed order; only
        a site that timed out can write later (its late thread)."""
        question_id = question["question_id"]
        for site in sorted(routes):
            handler = self.handlers.get(site)
            if handler is None:
                self._hq_record(question_id, site, "error", as_of)
                continue
            holder: dict[str, Any] = {}
            thread = threading.Thread(target=_call, args=(handler, strict_load(canonical_bytes(question)), holder),
                                      name=f"pushdown-{site}", daemon=True)
            thread.start()
            thread.join(timeout=self.deadline_seconds)
            if thread.is_alive():
                self._hq_record(question_id, site, "timeout", as_of)
                self._late.append(_Late(question_id, site, thread, holder))
                continue
            outcome = None
            if "result" in holder:
                try:
                    outcome = self.receive_verdict(holder["result"], site=site, as_of=as_of)
                except PushdownError:
                    outcome = None
            if outcome not in RECEIVED:
                self._hq_record(question_id, site, "error", as_of)

    def _verify(self, cand: _Candidate, as_of: str, *, run_id: str | None) -> Conclusion:
        window = question_window(self.pack, as_of=as_of, candidate_window_start=cand.window_start)
        body = build_question(self.pack, entity_type=cand.entity_type, entity_id=cand.entity_id,
                              predicate=cand.predicate, window=window, as_of=as_of)
        question_id = body["question_id"]
        stored = self.store.question(question_id)
        if stored is not None:
            question = strict_load(stored.body)
        else:
            question = body
            routes = self._routes(cand, question_id, window, as_of)
            self.store.add_question(
                QuestionRow(question_id=question_id, candidate_key=cand.key, run_id=run_id,
                            template_id=body["template_id"], as_of=as_of, window_start=window["start_week"],
                            window_end=window["end_week"], pack_hash=self.pack.config_hash,
                            body=canonical_bytes(body), display_text=render_text(self.pack, body),
                            created_at=self._now()), routes)
        self._deliver(question, {r.site: r.role for r in self.store.routes(question_id)}, as_of)
        lineage = {"candidate": {"run_id": run_id, "key": cand.key, "constructed": run_id is None},
                   "cells": list(cand.lineage)}
        return self._regate(question_id, as_of, lineage)

    # ------------------------------------------------------------------ intake
    def receive_verdict(self, body: Any, *, site: str, as_of: str) -> str:
        """``'accepted'``, ``'duplicate'`` or the rejection reason (:data:`REJECT_REASONS`)."""
        _date(as_of, "$.as_of")
        data = None
        try:
            data = canonical_bytes(body)
        except StrictJsonError:
            data = None
        if data is None:
            raise PushdownError("$", "the verdict is not canonical JSON") from None
        parsed = strict_load(data)
        problem: tuple[str, str | None, str | None] | None = None
        found = check_artifact(self.pack, site if isinstance(site, str) else "", "verdict", parsed)
        if found is not None:
            problem = ("invalid", found[0], found[1])
        else:
            stored = self.store.question(parsed["question_id"])
            if stored is None:
                problem = ("unknown_question", None, None)
            else:
                question = strict_load(stored.body)
                if site not in {r.site for r in self.store.routes(parsed["question_id"])}:
                    problem = ("unrouted", None, None)
                elif parsed["pack_hash"] != question["pack_hash"]:
                    problem = ("pack_hash", None, None)
                elif parsed["window"] != question["window"]:
                    problem = ("window", None, None)
        if problem is not None:
            reason, path, keyword = problem
            self.store.add_verdict_rejection(sha256=sha256_hex(data), site=site,
                                             question_id=parsed.get("question_id") if isinstance(parsed, dict)
                                             else None, reason=reason, path=path, keyword=keyword)
            return reason
        return self.store.append_verdict(question_id=parsed["question_id"], site=site, source="site",
                                         verdict=parsed["verdict"], reason=parsed["reason"], body=data,
                                         received_as_of=as_of)

    @property
    def late_deliveries(self) -> int:
        """Late deliveries not collected yet (finished or still running)."""
        return len(self._late)

    def join_late(self, timeout_seconds: float) -> int:
        """Wait up to ``timeout_seconds`` in all for every late delivery's thread to finish; returns how many are
        still running. Nothing is taken in: :meth:`collect_late` does that."""
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        for late in list(self._late):
            late.thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return sum(1 for late in self._late if late.thread.is_alive())

    def collect_late(self, *, as_of: str) -> list[Conclusion]:
        """Take in every finished late verdict, received at ``as_of``, and re-gate those questions at ``as_of``."""
        _date(as_of, "$.as_of")
        finished = [late for late in self._late if not late.thread.is_alive()]
        self._late = [late for late in self._late if late.thread.is_alive()]
        question_ids = set()
        for late in finished:
            if "result" in late.holder:
                try:
                    self.receive_verdict(late.holder["result"], site=late.site, as_of=as_of)
                except PushdownError:
                    pass
            question_ids.add(late.question_id)
        return [self.regate(q, as_of=as_of) for q in sorted(question_ids)]

    # ------------------------------------------------------------------ the gate and the conclusions
    def regate(self, question_id: str, *, as_of: str) -> Conclusion:
        return self._regate(question_id, as_of, None)

    def _regate(self, question_id: str, as_of: str, lineage: Mapping[str, Any] | None) -> Conclusion:
        _date(as_of, "$.as_of")
        stored = self.store.question(question_id) if isinstance(question_id, str) else None
        if stored is None:
            raise PushdownError("$.question_id", "unknown question") from None
        conclusion_id = CONCLUSION_PREFIX + question_id[:32]
        versions = self.store.conclusions(conclusion_id)
        latest = versions[-1] if versions else None
        if latest is not None and as_of < latest.as_of:
            raise PushdownError("$.as_of", "before the latest conclusion version") from None
        question = strict_load(stored.body)
        routes = {r.site: r.role for r in self.store.routes(question_id)}
        records = [gate.VerdictRecord(site=v.site, seq=v.seq, source=v.source, body=strict_load(v.body),
                                      received_as_of=v.received_as_of)
                   for v in self.store.pd_verdicts(question_id) if v.received_as_of <= as_of]
        result = gate.evaluate(self.pack, question, routes, records, as_of=as_of)
        result_doc = result.to_dict()
        previous = strict_load(latest.body) if latest is not None else None
        if previous is not None and canonical_bytes({"status": previous["status"], "reasons": previous["reasons"],
                                                     "used": previous["gate"]["used"]}) == canonical_bytes(
                {"status": result_doc["status"], "reasons": result_doc["reasons"], "used": result_doc["used"]}):
            return _conclusion(latest)
        if lineage is None:
            lineage = previous["lineage"] if previous is not None else self._stored_lineage(stored)
        confirming = [s for s in result_doc["support"]["confirming_sites"] if s in self.org.sites]
        contributing = sorted(s for s, role in routes.items() if role == "contributing" and s in self.org.sites)
        unit_sites = confirming or contributing
        version = latest.version + 1 if latest is not None else 1
        body = {"schema_version": SCHEMA_VERSION, "conclusion_id": conclusion_id, "version": version,
                "question_id": question_id, "candidate_key": stored.candidate_key, "as_of": as_of,
                "status": result.status, "reasons": list(result.reasons), "gate": result_doc,
                "pack_hash": self.pack.config_hash, "gate_params": gate.GateParams.from_pack(self.pack).to_dict(),
                "decision_unit": self.org.decision_unit(unit_sites) if unit_sites else None,
                "lineage": {"candidate": dict(lineage["candidate"]), "cells": list(lineage["cells"]),
                            "question_id": question_id,
                            "verdicts": [{"site": u["site"], "seq": u["seq"], "sha256": u["sha256"]}
                                         for u in result_doc["used"]]}}
        data = canonical_bytes(body)
        row = ConclusionRow(conclusion_id=conclusion_id, version=version, question_id=question_id, as_of=as_of,
                            status=result.status, body=data, sha256=sha256_hex(data), created_at=self._now())
        self.store.add_conclusion(row)
        return _conclusion(row)

    def _stored_lineage(self, stored: QuestionRow) -> dict[str, Any]:
        """The lineage of a question re-gated before any version exists: its stored candidate's cells, if any."""
        cells: list[Any] = []
        if stored.run_id is not None:
            raw = self.store.candidate(stored.run_id, stored.candidate_key)
            part = (raw.get("snapshot") or raw.get("rule")) if raw is not None else None
            cells = list(part["lineage"]) if part is not None else []
        return {"candidate": {"run_id": stored.run_id, "key": stored.candidate_key,
                              "constructed": stored.run_id is None}, "cells": cells}

    def conclusion(self, conclusion_id: str, version: int | None = None) -> Conclusion | None:
        versions = self.store.conclusions(conclusion_id)
        if not versions:
            return None
        if version is None:
            return _conclusion(versions[-1])
        found = [row for row in versions if row.version == version]
        return _conclusion(found[0]) if found else None

    def versions(self, conclusion_id: str) -> list[Conclusion]:
        return [_conclusion(row) for row in self.store.conclusions(conclusion_id)]
