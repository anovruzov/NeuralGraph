"""The HQ-to-fabric bridge: a read-only reader of HQ's pushdown tables publishing through ``publish_api`` (S4;
``docs/collective/E2E-INGEST.md`` sections 1, 3.6, 7 and 8 drill 7).

HQ's collective store (``mycelic/collective/detect/store.py``) holds, for every question HQ asked, the question
(``pd_questions``: the exact body, the candidate key, the detection run), the route (``pd_routes``, one row per
routed site with its gate role and rank), every verdict HQ took in (``pd_verdicts``: the site's exact bytes and
their sha256, or an HQ record of a timeout or error) and the versioned conclusion (``pd_conclusions``: the gate's
status and body). The four tables are append-only (``store.py:137-138``), so their primary keys are stable
idempotency keys (section 7).

The bridge opens its own ``mode=ro`` connection per read, as :class:`~mycelic.collective.detect.store.HqReader`
does (``store.py:678-684``), runs literal ordered SELECTs and never writes the store; a test takes the sha256 of
the store file before and after a run. Per conclusion, in ``(created_at, conclusion_id, version)`` order past the
watermark, it publishes one ``collective.verdict`` event per ``pd_verdicts`` row of the question (``ORDER BY site,
seq``, the store's own order) and then one ``collective.conclusion`` event whose embedded memory cites the verdict
event ids the API returned. Running it twice, or again from an empty watermark, re-POSTs the same keys: every result
is ``created: false``, no memory is created and the stream is unchanged (criterion 3, bridge half).

What the bridge needs HQ to have written (the contract ``hq_tesseract.py`` keeps):

* ``pd_questions.body`` is the question bytes (``build_question``) made under the bridge's pack (its ``pack_hash``
  is checked), so ``params``, ``window``, ``as_of``, ``candidate_key`` and ``question_id`` are read from it;
  ``pd_questions.run_id`` names the detection run saved with
  ``CollectiveStore.save_run`` whose ``candidates`` row for ``candidate_key`` carries ``snapshot.week``, the
  conclusion memory's ``snapshot_week``. A question without a run or a candidate row is a :class:`BridgeError`:
  nothing is guessed.
* ``pd_routes`` holds the routed sites with their ranks (route order is rank order);
* ``pd_verdicts.sha256`` is ``sha256_hex(body)`` (``append_verdict`` recomputes it, ``store.py:628-633``) and the
  body is strict JSON whose ``verdict`` is the row's; the bridge checks both, before any POST, and refuses a row
  where they do not hold;
* ``pd_conclusions.status`` is the gate's status and ``conclusion_id`` is ``"c-" + question_id[:32]``.

Nothing read here is a record, a narrative or a graph node, and nothing of the verdict bodies beyond the keys
``publish.py:80-87`` names is published. This module must not import the retrieval package, numpy, the site modules
or ``mycelic.service`` (``FORBIDDEN_HQ``, ``research/routing_spike/run.py:80-83``); a test checks a fresh interpreter.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from mycelic.collective.detect.store import ConclusionRow, PdVerdictRow, QuestionRow, RouteRow
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load

from .publish_api import (CONCLUSION_EVENT, ROUTE_LABEL, VERDICT_EVENT, PublishResult, Publisher, conclusion_event,
                          conclusion_memory, utc_now, verdict_event, write_json_atomic)

WATERMARK_SCHEMA = 1
CONCLUSION_PREFIX = "c-"
_CONCLUSIONS = ("SELECT conclusion_id, version, question_id, as_of, status, body, sha256, created_at "
                "FROM pd_conclusions ORDER BY created_at, conclusion_id, version")
_QUESTION = ("SELECT question_id, candidate_key, run_id, template_id, as_of, window_start, window_end, pack_hash, "
             "body, display_text, created_at FROM pd_questions WHERE question_id = ? ORDER BY question_id")
_ROUTES = "SELECT question_id, site, role, rank FROM pd_routes WHERE question_id = ? ORDER BY rank, site"
_VERDICTS = ("SELECT question_id, site, seq, source, verdict, reason, body, sha256, received_as_of, received_at "
             "FROM pd_verdicts WHERE question_id = ? ORDER BY site, seq")
_CANDIDATE = "SELECT body FROM candidates WHERE run_id = ? AND key = ? ORDER BY key"
_COUNT = "SELECT COUNT(*) FROM {table}"
_TABLES = ("pd_questions", "pd_routes", "pd_verdicts", "pd_conclusions")


class BridgeError(ValueError):
    """The store is missing, a row the bridge needs is absent or a stored sha256 does not hold."""


# --------------------------------------------------------------------------------------------------- the reader

class HqStoreReader:
    """Read-only access to the pushdown tables: one ``mode=ro`` connection per call, literal ordered SELECTs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        if not self.path.exists():
            raise BridgeError("missing") from None
        conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
        try:
            yield conn
        finally:
            conn.close()

    def conclusions(self) -> tuple[ConclusionRow, ...]:
        """Every conclusion version, in ``(created_at, conclusion_id, version)`` order."""
        with self._read() as conn:
            rows = conn.execute(_CONCLUSIONS).fetchall()
        return tuple(ConclusionRow(*row[:5], bytes(row[5]), *row[6:]) for row in rows)

    def question(self, question_id: str) -> QuestionRow | None:
        with self._read() as conn:
            row = conn.execute(_QUESTION, (question_id,)).fetchone()
        return QuestionRow(*row[:8], bytes(row[8]), *row[9:]) if row is not None else None

    def routes(self, question_id: str) -> tuple[RouteRow, ...]:
        with self._read() as conn:
            rows = conn.execute(_ROUTES, (question_id,)).fetchall()
        return tuple(RouteRow(*row) for row in rows)

    def verdicts(self, question_id: str) -> tuple[PdVerdictRow, ...]:
        with self._read() as conn:
            rows = conn.execute(_VERDICTS, (question_id,)).fetchall()
        return tuple(PdVerdictRow(*row[:6], bytes(row[6]), *row[7:]) for row in rows)

    def candidate(self, run_id: str, key: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.execute(_CANDIDATE, (run_id, key)).fetchone()
        if row is None:
            return None
        body = strict_load(bytes(row[0]))
        return body if isinstance(body, dict) else None

    def counts(self) -> dict[str, int]:
        with self._read() as conn:
            return {t: int(conn.execute(_COUNT.format(table=t)).fetchone()[0]) for t in _TABLES}

    def file_sha256(self) -> str:
        """The sha256 of the main database file (the WAL and shm files are SQLite's own and are not the store)."""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------------------- the watermark

@dataclass(frozen=True, order=True)
class Position:
    """Where the bridge stopped: the ``(created_at, conclusion_id, version)`` of the last conclusion published."""

    created_at: str
    conclusion_id: str
    version: int

    @classmethod
    def of(cls, row: ConclusionRow) -> "Position":
        return cls(row.created_at, row.conclusion_id, int(row.version))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_watermark(path: str | Path | None) -> Position | None:
    if path is None or not Path(path).exists():
        return None
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema") != WATERMARK_SCHEMA:
        raise BridgeError(f"unreadable bridge watermark at {path}") from None
    last = loaded.get("last")
    if last is None:
        return None
    return Position(str(last["created_at"]), str(last["conclusion_id"]), int(last["version"]))


def save_watermark(path: str | Path, position: Position | None, *, published_conclusions: int,
                   clock: Callable[[], str] = utc_now) -> None:
    write_json_atomic(path, {"schema": WATERMARK_SCHEMA, "last": position.to_dict() if position else None,
                             "published_conclusions": published_conclusions, "updated_at": clock()})


# --------------------------------------------------------------------------------------------------- the bridge

@dataclass(frozen=True)
class ConclusionPlan:
    """Everything the bridge publishes for one conclusion, read from the store before any POST."""

    row: ConclusionRow
    question: dict[str, Any]
    route: tuple[str, ...]
    roles: dict[str, str]
    verdicts: tuple[PdVerdictRow, ...]
    verdict_bodies: tuple[dict[str, Any], ...]
    snapshot_week: str
    run_id: str


@dataclass
class BridgedConclusion:
    conclusion_id: str
    version: int
    question_id: str
    status: str
    route: list[str]
    verdict_rows: int
    verdict_event_ids: list[str]
    verdict_created: int
    conclusion_event_id: str
    conclusion_created: bool
    memory_id: str | None
    memory_event_id: str | None
    memory_created: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BridgeReport:
    """What one run did: counts, ids and the watermark; no record, narrative or node id."""

    store_counts: dict[str, int]
    store_sha256_before: str
    store_sha256_after: str | None = None
    watermark_before: dict[str, Any] | None = None
    watermark_after: dict[str, Any] | None = None
    conclusions_pending: int = 0
    conclusions: int = 0
    verdict_events: int = 0
    conclusion_events: int = 0
    publishes: int = 0
    created: int = 0
    duplicate: int = 0
    ambiguous: int = 0
    memory_ids: list[str] = field(default_factory=list)
    memory_ids_created: list[str] = field(default_factory=list)
    event_ids_created: list[str] = field(default_factory=list)
    rows: list[BridgedConclusion] = field(default_factory=list)

    @property
    def store_unchanged(self) -> bool:
        return self.store_sha256_after == self.store_sha256_before

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rows"] = [r.to_dict() for r in self.rows]
        d["store_unchanged"] = self.store_unchanged
        return d


class HqBridge:
    """Publish every conclusion of HQ's store past the watermark, verdict events first, through ``publish_api``."""

    def __init__(self, store_path: str | Path, pack: Any, publisher: Publisher, *,
                 watermark: str | Path | None = None, route_label: str = ROUTE_LABEL,
                 clock: Callable[[], str] = utc_now) -> None:
        self.reader = HqStoreReader(store_path)
        self.pack = pack
        self.publisher = publisher
        self.watermark_path = Path(watermark) if watermark is not None else None
        self.route_label = route_label
        self._clock = clock

    # ---- reading
    def pending(self) -> list[ConclusionRow]:
        """The conclusions past the watermark, in publish order."""
        position = load_watermark(self.watermark_path)
        rows = self.reader.conclusions()
        return [r for r in rows if position is None or Position.of(r) > position]

    def plan(self, row: ConclusionRow) -> ConclusionPlan:
        """Everything to publish for one conclusion, checked before any POST."""
        question_row = self.reader.question(row.question_id)
        if question_row is None:
            raise BridgeError(f"conclusion {row.conclusion_id} v{row.version} names no stored question") from None
        try:
            question = strict_load(question_row.body)
        except StrictJsonError as exc:
            raise BridgeError(f"question {row.question_id[:12]} body is not strict JSON ({exc.reason})") from None
        if (not isinstance(question, dict) or question.get("question_id") != row.question_id
                or question.get("candidate_key") != question_row.candidate_key
                or not isinstance(question.get("params"), dict) or not isinstance(question.get("window"), dict)):
            raise BridgeError(f"question {row.question_id[:12]} body disagrees with its row") from None
        if question.get("pack_hash") != self.pack.config_hash:
            raise BridgeError(f"question {row.question_id[:12]} was made under another pack hash") from None
        if row.conclusion_id != CONCLUSION_PREFIX + row.question_id[:32]:
            raise BridgeError(f"conclusion {row.conclusion_id} is not c- plus its question id") from None
        if question_row.run_id is None:
            raise BridgeError(f"question {row.question_id[:12]} names no detection run: snapshot week "
                              "unknown") from None
        candidate = self.reader.candidate(question_row.run_id, question_row.candidate_key)
        snapshot = candidate.get("snapshot") if candidate is not None else None
        week = snapshot.get("week") if isinstance(snapshot, dict) else None
        if not isinstance(week, str) or not week:
            raise BridgeError(f"question {row.question_id[:12]}: no candidate snapshot week in run "
                              f"{question_row.run_id}") from None
        routes = self.reader.routes(row.question_id)
        if not routes:
            raise BridgeError(f"question {row.question_id[:12]} has no route") from None
        verdicts = self.reader.verdicts(row.question_id)
        bodies: list[dict[str, Any]] = []
        for v in verdicts:
            where = f"verdict ({row.question_id[:12]}, {v.site}, {v.seq})"
            if sha256_hex(v.body) != v.sha256:
                raise BridgeError(f"{where}: stored sha256 does not match its body") from None
            try:
                body = strict_load(v.body)
            except StrictJsonError as exc:
                raise BridgeError(f"{where}: body is not strict JSON ({exc.reason})") from None
            if not isinstance(body, dict) or body.get("verdict") != v.verdict:
                raise BridgeError(f"{where}: body disagrees with its row") from None
            bodies.append(body)
        return ConclusionPlan(row=row, question=question, route=tuple(r.site for r in routes),
                              roles={r.site: r.role for r in routes}, verdicts=verdicts, verdict_bodies=tuple(bodies),
                              snapshot_week=week, run_id=question_row.run_id)

    # ---- publishing
    def events_for(self, plan: ConclusionPlan, verdict_event_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """The verdict events of a plan (``verdict_event_ids`` None), or its conclusion event given those ids."""
        if verdict_event_ids is None:
            # a site's verdict carries the sha256 of the bytes HQ received; an HQ record of a timeout or an error
            # has none (``publish.py:9-10``, ``hq.py:273``)
            return [verdict_event(plan.question, site=v.site, seq=v.seq, source=v.source, body=body,
                                  sha256=v.sha256 if v.source == "site" else None)
                    for v, body in zip(plan.verdicts, plan.verdict_bodies)]
        params = plan.question["params"]
        memory = conclusion_memory(self.pack, plan.question, status=plan.row.status,
                                   entity_type=params["entity_type"], candidate_key=plan.question["candidate_key"],
                                   conclusion_id=plan.row.conclusion_id, snapshot_week=plan.snapshot_week,
                                   as_of=plan.row.as_of, route=plan.route, verdict_event_ids=verdict_event_ids,
                                   route_label=self.route_label)
        return [conclusion_event(plan.question, conclusion_id=plan.row.conclusion_id, version=plan.row.version,
                                 status=plan.row.status, route=plan.route, verdict_event_ids=verdict_event_ids,
                                 memory=memory, route_label=self.route_label)]

    def publish_conclusion(self, plan: ConclusionPlan) -> BridgedConclusion:
        verdict_results: list[PublishResult] = [self.publisher.publish(e) for e in self.events_for(plan)]
        ids = [r.event_id for r in verdict_results]
        [conclusion] = [self.publisher.publish(e) for e in self.events_for(plan, ids)]
        return BridgedConclusion(conclusion_id=plan.row.conclusion_id, version=plan.row.version,
                                 question_id=plan.row.question_id, status=plan.row.status, route=list(plan.route),
                                 verdict_rows=len(plan.verdicts), verdict_event_ids=ids,
                                 verdict_created=sum(1 for r in verdict_results if r.created),
                                 conclusion_event_id=conclusion.event_id, conclusion_created=conclusion.created,
                                 memory_id=conclusion.memory_id, memory_event_id=conclusion.memory_event_id,
                                 memory_created=conclusion.memory_created)

    def run(self, *, limit: int | None = None) -> BridgeReport:
        """Publish every pending conclusion (at most ``limit``), moving the watermark after each one."""
        before = load_watermark(self.watermark_path)
        report = BridgeReport(store_counts=self.reader.counts(), store_sha256_before=self.reader.file_sha256(),
                              watermark_before=before.to_dict() if before else None)
        pending = self.pending()
        report.conclusions_pending = len(pending)
        if limit is not None:
            pending = pending[:max(0, int(limit))]
        plans = [self.plan(row) for row in pending]           # every row checked before the first POST
        first = len(self.publisher.results)
        published = 0
        position = before
        for plan in plans:
            bridged = self.publish_conclusion(plan)
            report.rows.append(bridged)
            published += 1
            position = Position.of(plan.row)
            if self.watermark_path is not None:
                save_watermark(self.watermark_path, position, published_conclusions=published, clock=self._clock)
        results = self.publisher.results[first:]
        report.conclusions = published
        report.verdict_events = sum(1 for r in results if r.type == VERDICT_EVENT)
        report.conclusion_events = sum(1 for r in results if r.type == CONCLUSION_EVENT)
        report.publishes = len(results)
        report.created = sum(1 for r in results if r.created)
        report.duplicate = sum(1 for r in results if not r.created)
        report.ambiguous = sum(1 for r in results if r.ambiguous)
        report.memory_ids = [r.memory_id for r in results if r.memory_id is not None]
        report.memory_ids_created = [r.memory_id for r in results if r.memory_id is not None and r.memory_created]
        report.event_ids_created = [r.event_id for r in results if r.created]
        report.watermark_after = position.to_dict() if position else None
        report.store_sha256_after = self.reader.file_sha256()
        return report
