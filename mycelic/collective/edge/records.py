"""The site record store: one SQLite file per site, the only SQL inside a site; HQ's is detect/store.py.

A site's raw records, their claims and its emission log live in ``site-<id>.sqlite3`` inside the site's boundary.
Nothing here leaves the site; :mod:`.egress` decides what may.

Rules:

* One file per site, opened with ``isolation_level=None``, WAL (refused with :class:`StoreError` when the file
  system cannot do WAL), ``synchronous=FULL``, foreign keys on and a 5 s busy timeout. No table has a fabric table
  name (:data:`TABLES` is the whole schema). G6 added ``question_log`` and ``verdict_log`` with ``CREATE ... IF NOT
  EXISTS``; the change is additive, so ``SCHEMA_VERSION`` stays 1 and a G3 store gains the two tables on open.
* ``site_info`` pins ``schema_version``, ``site_id``, ``pack_id`` and ``config_hash``. A store opened with any other
  value raises ``StoreError('site_info mismatch: <key>')``: claims are tied to a pack's vocabulary, so a changed pack
  config needs a new store.
* Every write runs in one ``BEGIN IMMEDIATE ... COMMIT`` and is rolled back when anything raises.
* Every ``SELECT`` carries ``ORDER BY``; counts are taken in Python from ordered selects.

Ingest (records already validated and normalised by ``EdgeSite``), per record in input order:

* a ``record_ref`` already stored, or seen earlier in the batch, is a duplicate: the first wins;
* the record counts in its received week, unless that week is at or before the cells watermark (the last
  ``closed_through`` emitted). Such a late record counts in ``max(ingest week, the week after the watermark)`` and
  gets a ``late_records`` row, so an emitted week is never revised;
* ``forwarded_in`` is 1 when the record names an origin at another site;
* ``root_ref`` is the origin's ref when there is one; else the root of the earliest record at this site with the
  same folded narrative (``narrative_key``, the sha256 of the folded text; an empty narrative has none and never
  shares a root); else the record's own ref. A copy that reached another site without an origin marker therefore
  counts as independent there.

``emitted_weeks`` holds one row per emission per artifact type, with the exact bytes sent; the latest
``closed_through`` is that type's watermark, and every week at or before it is final, including weeks without a
record.

Pushdown verification (G6, ``edge/verify.py``) reads and writes here only through the methods below:

* ``question_log`` has one row per question the site took in on a site-clock day: the question id, the entity and
  an outcome (``answered``, ``budget``, ``no_secret`` or ``degraded``: the judge failed on more than half the
  records, which is not an answer); :meth:`RecordStore.answered_count` is the per-entity daily budget's count of
  distinct answered questions;
* ``verdict_log`` has one row per answered question (``question_id`` and ``evidence_ref`` unique): the exact bytes
  sent and, for the site's auditor only, the confirming and entity record refs, the judged, failure and unclear
  counts, the extraction misses and the local reason for an unknown. None of these leaves the site;
* :meth:`RecordStore.window_records` reads the site's own records (forwarded-in excluded) received in a window of
  ISO weeks, newest first, which is what a question is answered from.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Sequence

from ..jsonio import canonical_dumps, sha256_hex, strict_load
from ..packs.canonical import folded
from .weeks import iso_week, local_date, next_week

SCHEMA_VERSION = 1
TABLES = ("site_info", "records", "claims", "extraction_stats", "late_records", "emitted_weeks", "question_log",
          "verdict_log")
QUESTION_OUTCOMES = ("answered", "budget", "no_secret", "degraded")
LOCAL_REASONS = ("no_records", "unclear", "degraded")
CELLS_ARTIFACT = "cells_bundle"
SITE_INFO_KEYS = ("schema_version", "site_id", "pack_id", "config_hash")

_DDL = (
    "CREATE TABLE IF NOT EXISTS site_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS records ("
    "record_ref TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, "
    "received_date TEXT NOT NULL, iso_week TEXT NOT NULL, "
    "ingested_at TEXT NOT NULL, ingest_week TEXT NOT NULL, count_week TEXT NOT NULL, "
    "language TEXT, codes TEXT NOT NULL, structured TEXT NOT NULL, "
    "narrative TEXT NOT NULL, narrative_key TEXT, person TEXT NOT NULL, reporter_id TEXT, "
    "origin_ref TEXT, origin_site TEXT, root_ref TEXT NOT NULL, "
    "forwarded_in INTEGER NOT NULL, synthetic INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS records_narrative_key ON records (narrative_key, seq)",
    "CREATE INDEX IF NOT EXISTS records_count_week ON records (count_week)",
    "CREATE TABLE IF NOT EXISTS claims ("
    "record_ref TEXT NOT NULL REFERENCES records(record_ref), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, "
    "predicate TEXT NOT NULL, channel TEXT NOT NULL, extractor TEXT NOT NULL, res_conf REAL NOT NULL, "
    "PRIMARY KEY (record_ref, entity_type, entity_id, predicate))",
    "CREATE TABLE IF NOT EXISTS extraction_stats ("
    "record_ref TEXT PRIMARY KEY REFERENCES records(record_ref), mode TEXT NOT NULL, extractor TEXT NOT NULL, "
    "error_kind TEXT, truncated INTEGER, language_supported INTEGER, drops TEXT NOT NULL, unresolved TEXT NOT NULL, "
    "unknown_codes INTEGER, structured_unresolved INTEGER, invalid_claims INTEGER, extracted_at TEXT)",
    "CREATE TABLE IF NOT EXISTS late_records ("
    "record_ref TEXT PRIMARY KEY REFERENCES records(record_ref), received_week TEXT NOT NULL, "
    "count_week TEXT NOT NULL, watermark TEXT NOT NULL, ingested_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS emitted_weeks ("
    "artifact_type TEXT, closed_through TEXT, as_of TEXT NOT NULL, after_week TEXT, ledger_rows INTEGER, "
    "body BLOB NOT NULL, sha256 TEXT NOT NULL, cells INTEGER, created_at TEXT NOT NULL, sent_at TEXT, "
    "PRIMARY KEY (artifact_type, closed_through))",
    "CREATE TABLE IF NOT EXISTS question_log ("
    "seq INTEGER PRIMARY KEY, question_id TEXT NOT NULL, day TEXT NOT NULL, entity_type TEXT NOT NULL, "
    "entity_id TEXT NOT NULL, outcome TEXT NOT NULL, at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS question_log_entity_day ON question_log (entity_type, entity_id, day)",
    "CREATE TABLE IF NOT EXISTS verdict_log ("
    "verdict_id TEXT PRIMARY KEY, question_id TEXT NOT NULL UNIQUE, evidence_ref TEXT UNIQUE, body BLOB NOT NULL, "
    "sha256 TEXT NOT NULL, verdict TEXT NOT NULL, confirming_refs TEXT NOT NULL, entity_refs TEXT NOT NULL, "
    "judged INTEGER NOT NULL, failures INTEGER NOT NULL, unclear INTEGER NOT NULL, extraction_misses INTEGER NOT NULL, "
    "local_reason TEXT, truncated INTEGER NOT NULL, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS records_iso_week ON records (iso_week, seq)",
)
_SITE_INFO = "SELECT key, value FROM site_info ORDER BY key"
_INSERT_SITE_INFO = "INSERT INTO site_info (key, value) VALUES (?, ?)"
_LAST_SEQ = "SELECT seq FROM records ORDER BY seq DESC LIMIT 1"
_HAS_REF = "SELECT seq FROM records WHERE record_ref = ? ORDER BY seq LIMIT 1"
_ROOT_BY_KEY = "SELECT root_ref FROM records WHERE narrative_key = ? ORDER BY seq LIMIT 1"
_INSERT_RECORD = (
    "INSERT INTO records (record_ref, seq, received_date, iso_week, ingested_at, ingest_week, count_week, language, "
    "codes, structured, narrative, narrative_key, person, reporter_id, origin_ref, origin_site, root_ref, "
    "forwarded_in, synthetic) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
_INSERT_LATE = ("INSERT INTO late_records (record_ref, received_week, count_week, watermark, ingested_at) "
                "VALUES (?, ?, ?, ?, ?)")
_UNEXTRACTED = (
    "SELECT r.seq, r.record_ref, r.received_date, r.language, r.codes, r.structured, r.narrative, r.person, "
    "r.reporter_id, r.origin_ref, r.origin_site, r.synthetic FROM records r "
    "LEFT JOIN extraction_stats s ON s.record_ref = r.record_ref WHERE s.record_ref IS NULL "
    "ORDER BY r.seq LIMIT ?")
_PENDING_THROUGH = (
    "SELECT r.seq FROM records r LEFT JOIN extraction_stats s ON s.record_ref = r.record_ref "
    "WHERE s.record_ref IS NULL AND r.count_week <= ? ORDER BY r.seq")
_PENDING_NON_SYNTHETIC = (
    "SELECT r.seq FROM records r LEFT JOIN extraction_stats s ON s.record_ref = r.record_ref "
    "WHERE s.record_ref IS NULL AND r.synthetic = 0 ORDER BY r.seq")
_INSERT_STATS = (
    "INSERT INTO extraction_stats (record_ref, mode, extractor, error_kind, truncated, language_supported, drops, "
    "unresolved, unknown_codes, structured_unresolved, invalid_claims, extracted_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
_INSERT_CLAIM = ("INSERT INTO claims (record_ref, entity_type, entity_id, predicate, channel, extractor, res_conf) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)")
_EMISSION_INPUTS = (
    "SELECT r.count_week, r.record_ref, r.root_ref, r.reporter_id, c.entity_type, c.entity_id, c.predicate, "
    "c.channel, c.res_conf FROM claims c JOIN records r ON r.record_ref = c.record_ref "
    "WHERE r.forwarded_in = 0 AND r.count_week <= ? AND (? IS NULL OR r.count_week > ?) "
    "ORDER BY r.count_week, r.record_ref, c.entity_type, c.entity_id, c.predicate")
_WINDOW_RECORDS = ("SELECT forwarded_in FROM records WHERE count_week <= ? AND (? IS NULL OR count_week > ?) "
                   "ORDER BY seq")
_WINDOW_LATE = ("SELECT record_ref FROM late_records WHERE count_week <= ? AND (? IS NULL OR count_week > ?) "
                "ORDER BY record_ref")
_LAST_EMISSION = (
    "SELECT artifact_type, closed_through, as_of, after_week, ledger_rows, body, sha256, cells, created_at, sent_at "
    "FROM emitted_weeks WHERE artifact_type = ? ORDER BY closed_through DESC LIMIT 1")
_UNSENT = (
    "SELECT artifact_type, closed_through, as_of, after_week, ledger_rows, body, sha256, cells, created_at, sent_at "
    "FROM emitted_weeks WHERE artifact_type = ? AND sent_at IS NULL ORDER BY closed_through")
_INSERT_EMISSION = (
    "INSERT INTO emitted_weeks (artifact_type, closed_through, as_of, after_week, ledger_rows, body, sha256, cells, "
    "created_at, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
_MARK_SENT = "UPDATE emitted_weeks SET sent_at = ? WHERE artifact_type = ? AND closed_through = ?"
_WINDOW_OWN = (
    "SELECT record_ref, iso_week, root_ref, reporter_id, language, codes, structured, narrative FROM records "
    "WHERE forwarded_in = 0 AND iso_week >= ? AND iso_week <= ? ORDER BY iso_week DESC, seq DESC")
_CLAIMED = (
    "SELECT DISTINCT c.record_ref FROM claims c JOIN records r ON r.record_ref = c.record_ref "
    "WHERE c.entity_type = ? AND c.entity_id = ? AND r.forwarded_in = 0 AND r.iso_week >= ? AND r.iso_week <= ? "
    "ORDER BY c.record_ref")
_HAS_CLAIM = ("SELECT record_ref FROM claims WHERE record_ref = ? AND entity_type = ? AND entity_id = ? "
              "AND predicate = ? ORDER BY record_ref LIMIT 1")
_ANSWERED = ("SELECT DISTINCT question_id FROM question_log WHERE entity_type = ? AND entity_id = ? AND day = ? "
             "AND outcome = 'answered' ORDER BY question_id")
_INSERT_QUESTION = ("INSERT INTO question_log (question_id, day, entity_type, entity_id, outcome, at) "
                    "VALUES (?, ?, ?, ?, ?, ?)")
_VERDICT_COLUMNS = ("verdict_id, question_id, evidence_ref, body, sha256, verdict, confirming_refs, entity_refs, "
                    "judged, failures, unclear, extraction_misses, local_reason, truncated, created_at")
_VERDICT_BY_QUESTION = (
    "SELECT verdict_id, question_id, evidence_ref, body, sha256, verdict, confirming_refs, entity_refs, judged, "
    "failures, unclear, extraction_misses, local_reason, truncated, created_at FROM verdict_log "
    "WHERE question_id = ? ORDER BY verdict_id LIMIT 1")
_VERDICT_BY_REF = (
    "SELECT verdict_id, question_id, evidence_ref, body, sha256, verdict, confirming_refs, entity_refs, judged, "
    "failures, unclear, extraction_misses, local_reason, truncated, created_at FROM verdict_log "
    "WHERE evidence_ref = ? ORDER BY verdict_id LIMIT 1")
_VERDICT_BY_ID = (
    "SELECT verdict_id, question_id, evidence_ref, body, sha256, verdict, confirming_refs, entity_refs, judged, "
    "failures, unclear, extraction_misses, local_reason, truncated, created_at FROM verdict_log "
    "WHERE verdict_id = ? ORDER BY verdict_id LIMIT 1")
_INSERT_VERDICT = ("INSERT INTO verdict_log (" + _VERDICT_COLUMNS + ") "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
_QUESTION_LOG = "SELECT seq, question_id, day, entity_type, entity_id, outcome, at FROM question_log ORDER BY seq"


class StoreError(ValueError):
    pass


@dataclass(frozen=True)
class StoreIngest:
    ingested: int
    duplicates: int
    late: int
    forwarded_in: int


@dataclass(frozen=True)
class ExtractionRow:
    """One record's extraction result as stored: ``extraction_stats`` plus its claims (``edge.extract.Claim``)."""

    record_ref: str
    mode: str
    extractor: str
    error_kind: str | None
    truncated: bool
    language_supported: bool
    drops: Any
    unresolved: Any
    unknown_codes: int
    structured_unresolved: int
    invalid_claims: int
    extracted_at: str
    claims: tuple[Any, ...]


class WindowRecord(NamedTuple):
    """One of the site's own records as a question is answered from it; ``codes`` and ``structured`` parsed."""

    record_ref: str
    iso_week: str
    root_ref: str
    reporter_id: str | None
    language: str | None
    codes: list[str]
    structured: dict[str, list[str]]
    narrative: str


class QuestionLogRow(NamedTuple):
    seq: int
    question_id: str
    day: str
    entity_type: str
    entity_id: str
    outcome: str
    at: str


@dataclass(frozen=True)
class VerdictRow:
    """One answered question: the exact bytes sent and what only the site's auditor reads."""

    verdict_id: str
    question_id: str
    evidence_ref: str | None
    body: bytes
    sha256: str
    verdict: str
    confirming_refs: tuple[str, ...]
    entity_refs: tuple[str, ...]
    judged: int
    failures: int
    unclear: int
    extraction_misses: int
    local_reason: str | None
    truncated: bool
    created_at: str


class InputRow(NamedTuple):
    count_week: str
    record_ref: str
    root_ref: str
    reporter_id: str | None
    entity_type: str
    entity_id: str
    predicate: str
    channel: str
    res_conf: float


@dataclass(frozen=True)
class EmissionRow:
    artifact_type: str
    closed_through: str
    as_of: str
    after_week: str | None
    ledger_rows: int | None
    body: bytes
    sha256: str
    cells: int | None
    created_at: str
    sent_at: str | None


class RecordStore:
    def __init__(self, path: str | Path, *, site_id: str, pack_id: str, config_hash: str) -> None:
        self.path = Path(path)
        self.site_id = site_id
        conn = sqlite3.connect(str(self.path), isolation_level=None)
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if mode is None or str(mode[0]).lower() != "wal":
            conn.close()
            raise StoreError("the store needs SQLite WAL mode") from None
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        expected = {"schema_version": str(SCHEMA_VERSION), "site_id": site_id, "pack_id": pack_id,
                    "config_hash": config_hash}
        mismatch = None
        with self._write():
            for statement in _DDL:
                conn.execute(statement)
            stored = dict(conn.execute(_SITE_INFO).fetchall())
            if not stored:
                conn.executemany(_INSERT_SITE_INFO, [(key, expected[key]) for key in SITE_INFO_KEYS])
            else:
                mismatch = next((key for key in SITE_INFO_KEYS if stored.get(key) != expected[key]), None)
        if mismatch is not None:
            conn.close()
            raise StoreError(f"site_info mismatch: {mismatch}") from None

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One explicit transaction; rolled back when the body (or the commit) raises, and the error propagates."""
        self._conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            yield self._conn
            self._conn.execute("COMMIT")
            committed = True
        finally:
            if not committed and self._conn.in_transaction:
                self._conn.execute("ROLLBACK")

    # ------------------------------------------------------------------ ingest
    def ingest(self, records: Sequence[dict[str, Any]], *, ingested_at: str) -> StoreIngest:
        day = local_date(ingested_at)
        if day is None:
            raise ValueError("ingested_at must be an ISO 8601 timestamp") from None
        ingest_week = iso_week(day)
        ingested = duplicates = late = forwarded = 0
        with self._write() as conn:
            last = self.last_emission(CELLS_ARTIFACT)
            watermark = last.closed_through if last is not None else None
            row = conn.execute(_LAST_SEQ).fetchone()
            seq = row[0] if row is not None else 0
            for rec in records:
                ref = rec["record_ref"]
                if conn.execute(_HAS_REF, (ref,)).fetchone() is not None:
                    duplicates += 1
                    continue
                week = iso_week(rec["received_date"])
                is_late = watermark is not None and week <= watermark
                count_week = max(ingest_week, next_week(watermark)) if is_late else week
                is_forwarded = rec["origin_site"] is not None and rec["origin_site"] != self.site_id
                text = folded(rec["narrative"])
                key = sha256_hex(text) if text else None
                root = rec["origin_ref"]
                if root is None and key is not None:
                    found = conn.execute(_ROOT_BY_KEY, (key,)).fetchone()
                    root = found[0] if found is not None else None
                seq += 1
                conn.execute(_INSERT_RECORD, (
                    ref, seq, rec["received_date"], week, ingested_at, ingest_week, count_week, rec["language"],
                    canonical_dumps(rec["codes"]), canonical_dumps(rec["entities"]), rec["narrative"], key,
                    canonical_dumps(rec["persons"]), rec["reporter"], rec["origin_ref"], rec["origin_site"],
                    root if root is not None else ref, int(is_forwarded), int(bool(rec["synthetic"]))))
                if is_late:
                    conn.execute(_INSERT_LATE, (ref, week, count_week, watermark, ingested_at))
                    late += 1
                ingested += 1
                forwarded += int(is_forwarded)
        return StoreIngest(ingested=ingested, duplicates=duplicates, late=late, forwarded_in=forwarded)

    # ------------------------------------------------------------------ extraction
    def unextracted(self, limit: int) -> list[tuple[int, dict[str, Any]]]:
        """(seq, record) for records without extraction stats, oldest first; the record has exactly the connector's
        record keys."""
        out = []
        for (seq, ref, received, language, codes, structured, narrative, person, reporter, origin_ref, origin_site,
             synthetic) in self._conn.execute(_UNEXTRACTED, (limit,)).fetchall():
            out.append((seq, {
                "record_ref": ref, "site": self.site_id, "received_date": received, "language": language,
                "codes": strict_load(codes), "entities": strict_load(structured), "narrative": narrative,
                "persons": strict_load(person), "reporter": reporter, "origin_ref": origin_ref,
                "origin_site": origin_site, "synthetic": bool(synthetic)}))
        return out

    def pending_through(self, week: str) -> int:
        return len(self._conn.execute(_PENDING_THROUGH, (week,)).fetchall())

    def pending_non_synthetic(self) -> int:
        return len(self._conn.execute(_PENDING_NON_SYNTHETIC).fetchall())

    def save_extractions(self, rows: Sequence[ExtractionRow]) -> None:
        with self._write() as conn:
            for r in rows:
                conn.execute(_INSERT_STATS, (
                    r.record_ref, r.mode, r.extractor, r.error_kind, int(bool(r.truncated)),
                    int(bool(r.language_supported)), canonical_dumps(dict(r.drops)),
                    canonical_dumps(dict(r.unresolved)), r.unknown_codes, r.structured_unresolved, r.invalid_claims,
                    r.extracted_at))
                for c in r.claims:
                    conn.execute(_INSERT_CLAIM, (r.record_ref, c.entity_type, c.entity_id, c.predicate, c.channel,
                                                 c.extractor, c.res_conf))

    # ------------------------------------------------------------------ emission
    def emission_inputs(self, after: str | None, through: str) -> list[InputRow]:
        """Claims of the site's own records (forwarded-in excluded) counted in a week of ``(after, through]``."""
        rows = self._conn.execute(_EMISSION_INPUTS, (through, after, after)).fetchall()
        return [InputRow(*row) for row in rows]

    def window_stats(self, after: str | None, through: str) -> dict[str, int]:
        flags = [f for (f,) in self._conn.execute(_WINDOW_RECORDS, (through, after, after)).fetchall()]
        late = self._conn.execute(_WINDOW_LATE, (through, after, after)).fetchall()
        return {"records": flags.count(0), "forwarded_excluded": flags.count(1), "late": len(late)}

    def last_emission(self, artifact_type: str) -> EmissionRow | None:
        row = self._conn.execute(_LAST_EMISSION, (artifact_type,)).fetchone()
        return _emission(row) if row is not None else None

    def unsent(self, artifact_type: str) -> list[EmissionRow]:
        return [_emission(row) for row in self._conn.execute(_UNSENT, (artifact_type,)).fetchall()]

    def add_emission(self, row: EmissionRow) -> None:
        with self._write() as conn:
            conn.execute(_INSERT_EMISSION, (row.artifact_type, row.closed_through, row.as_of, row.after_week,
                                            row.ledger_rows, row.body, row.sha256, row.cells, row.created_at,
                                            row.sent_at))

    def mark_sent(self, artifact_type: str, closed_through: str, sent_at: str) -> None:
        with self._write() as conn:
            conn.execute(_MARK_SENT, (sent_at, artifact_type, closed_through))

    # ------------------------------------------------------------------ pushdown verification (G6)
    def window_records(self, first_week: str, last_week: str) -> list[WindowRecord]:
        """The site's own records (forwarded-in excluded) received in ``[first_week, last_week]``, newest first."""
        return [WindowRecord(ref, week, root, reporter, language, strict_load(codes), strict_load(structured),
                             narrative)
                for ref, week, root, reporter, language, codes, structured, narrative
                in self._conn.execute(_WINDOW_OWN, (first_week, last_week)).fetchall()]

    def claimed_refs(self, entity_type: str, entity_id: str, first_week: str, last_week: str) -> list[str]:
        """Refs of own records received in the window with any stored claim on the entity (any predicate)."""
        return [ref for (ref,) in self._conn.execute(_CLAIMED, (entity_type, entity_id, first_week,
                                                                last_week)).fetchall()]

    def has_claim(self, record_ref: str, entity_type: str, entity_id: str, predicate: str) -> bool:
        return self._conn.execute(_HAS_CLAIM, (record_ref, entity_type, entity_id, predicate)).fetchone() is not None

    def answered_count(self, entity_type: str, entity_id: str, day: str) -> int:
        """Distinct questions about the entity answered on the site-clock ``day``."""
        return len(self._conn.execute(_ANSWERED, (entity_type, entity_id, day)).fetchall())

    def log_question(self, row: QuestionLogRow) -> None:
        """One question_log row (``row.seq`` is ignored: the store numbers rows)."""
        with self._write() as conn:
            conn.execute(_INSERT_QUESTION, row[1:])

    def question_log(self) -> list[QuestionLogRow]:
        return [QuestionLogRow(*row) for row in self._conn.execute(_QUESTION_LOG).fetchall()]

    def _verdict(self, sql: str, value: str) -> VerdictRow | None:
        row = self._conn.execute(sql, (value,)).fetchone()
        return _verdict_row(row) if row is not None else None

    def verdict_for_question(self, question_id: str) -> VerdictRow | None:
        return self._verdict(_VERDICT_BY_QUESTION, question_id)

    def verdict_for_ref(self, evidence_ref: str) -> VerdictRow | None:
        return self._verdict(_VERDICT_BY_REF, evidence_ref)

    def verdict_by_id(self, verdict_id: str) -> VerdictRow | None:
        return self._verdict(_VERDICT_BY_ID, verdict_id)

    def add_verdict(self, verdict: VerdictRow, question: QuestionLogRow) -> None:
        """The verdict and its ``answered`` question_log row, in one transaction."""
        with self._write() as conn:
            conn.execute(_INSERT_VERDICT, (
                verdict.verdict_id, verdict.question_id, verdict.evidence_ref, verdict.body, verdict.sha256,
                verdict.verdict, canonical_dumps(list(verdict.confirming_refs)),
                canonical_dumps(list(verdict.entity_refs)), verdict.judged, verdict.failures, verdict.unclear,
                verdict.extraction_misses, verdict.local_reason, int(bool(verdict.truncated)), verdict.created_at))
            conn.execute(_INSERT_QUESTION, question[1:])


def _verdict_row(row: tuple[Any, ...]) -> VerdictRow:
    (verdict_id, question_id, evidence_ref, body, sha256, verdict, confirming, entity, judged, failures, unclear,
     misses, local_reason, truncated, created_at) = row
    return VerdictRow(verdict_id=verdict_id, question_id=question_id, evidence_ref=evidence_ref, body=bytes(body),
                      sha256=sha256, verdict=verdict, confirming_refs=tuple(strict_load(confirming)),
                      entity_refs=tuple(strict_load(entity)), judged=judged, failures=failures, unclear=unclear,
                      extraction_misses=misses, local_reason=local_reason, truncated=bool(truncated),
                      created_at=created_at)


def _emission(row: tuple[Any, ...]) -> EmissionRow:
    (artifact_type, closed_through, as_of, after_week, ledger_rows, body, sha256, cells, created_at, sent_at) = row
    return EmissionRow(artifact_type=artifact_type, closed_through=closed_through, as_of=as_of, after_week=after_week,
                       ledger_rows=ledger_rows, body=bytes(body), sha256=sha256, cells=cells, created_at=created_at,
                       sent_at=sent_at)
