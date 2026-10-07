"""HQ's collective store: one SQLite file (``collective.sqlite3``) holding the cells that left the sites, and the only
SQL on the HQ side.

The store is HQ's own file, apart from the fabric's database and from every site store. :data:`TABLES` is the whole
schema and no table has a fabric table name. ``cells`` is the HQ counts table of STRATEGY section 4.5.

Rules:

* Opened with ``isolation_level=None``, WAL (refused with :class:`StoreError` when the file system cannot do WAL, so
  ``':memory:'`` is refused), ``synchronous=FULL``, foreign keys on and a 5 s busy timeout. Every write runs in one
  ``BEGIN IMMEDIATE ... COMMIT`` and is rolled back when anything raises. Every ``SELECT`` carries ``ORDER BY``.
  Timestamps come only from the injected clock, which must return an ISO 8601 timestamp.
* ``store_info`` pins ``schema_version``, ``pack_id``, ``config_hash`` and ``enterprise``; reopening with another
  value raises ``StoreError('store_info mismatch: <key>')``.
* ``bundles`` and ``cells`` are immutable: triggers abort any ``UPDATE`` or ``DELETE`` on them, and this module has no
  such statement. A NULL count means ``'<k'`` and a NULL ``res_conf_min`` means absent.
* **Org sync on open**, in one transaction against ``org_sites``: a new site is inserted and logged
  ``(NULL, path)``; a changed unit path is updated and logged ``(old, new)``; a removed site is deleted and logged
  ``(old, NULL)``; a changed country or display name is updated without a log row; an identical org writes nothing.
  Detection always uses the ``OrgConfig`` passed in, and cells of sites no longer in the org are not read.
* **Ingest** (:meth:`CollectiveStore.ingest_bundle`), first failing check decides: (0) a sha256 already stored is a
  ``duplicate`` and writes nothing; (1) a body that is not an object is ``invalid``; (2) a str ``config_hash`` other
  than the pack's is ``config_hash``; (3) a str ``site`` outside the org is ``unknown_site``; (4) the Boundary's own
  validator (:func:`~..edge.egress.check_artifact`) finding a problem is ``invalid``, with its path and keyword; (5) a
  log row naming another site than the body is ``site_mismatch``; (6) a stored cell with the same key and other
  counts is ``conflict``; (7) ``after`` other than the site's last accepted ``closed_through``, or an ``as_of``
  before the site's last accepted one, is ``sequence``. An accepted bundle and all its cells are written in one
  transaction; a rejection is one ``rejections`` row (``INSERT OR IGNORE`` on ``(sha256, reason)``) holding the
  sha256, the site when it is a valid site id, the reason, the validator's path and keyword, and the log line
  number; never a value from the body. A bundle refused for ``sequence`` is accepted by a later ingest once its
  predecessor has arrived.
* :meth:`CollectiveStore.ingest_log` reads a receive log in file order: a line that is not strict JSON or not a
  well-formed log row (:func:`~..edge.egress.log_row_problem`) is ``bad_line`` (sha256 of the raw line, 1-based line
  number) and the next line is still read; a ``usage_summary`` row is counted as ignored; a ``cells_bundle`` row goes
  to ``ingest_bundle``. Re-ingesting a file is idempotent.
* :meth:`CollectiveStore.detection_inputs` reads only bundles with ``as_of`` on or before the run's ``as_of`` and
  cells with ``as_of`` on or before it, a week on or before its last closed week, and a channel of the run, of the
  current org's sites.
* :meth:`CollectiveStore.save_run` stores a detection result once: the same ``run_id`` with the same result sha256 is
  a no-op, with another one ``StoreError('run conflict')``. One writer per store.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, NamedTuple

from ..edge.egress import CHANNELS, SUPPRESSED, check_artifact, log_row_problem
from ..edge.weeks import TS_RE, closed_through, local_date
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from .org import SITE_ID_RE, OrgConfig

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack

SCHEMA_VERSION = 1
TABLES = ("store_info", "org_sites", "org_log", "bundles", "cells", "rejections", "detection_runs", "candidates",
          "rule_hits")
STORE_INFO_KEYS = ("schema_version", "pack_id", "config_hash", "enterprise")
REJECT_REASONS = ("bad_line", "config_hash", "unknown_site", "invalid", "site_mismatch", "conflict", "sequence")
RUN_CHANNELS: Mapping[str, tuple[str, ...]] = MappingProxyType({"X": CHANNELS, "S": CHANNELS[:1]})
CELLS_ARTIFACT = "cells_bundle"

_DDL = (
    "CREATE TABLE IF NOT EXISTS store_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS org_sites (site_id TEXT PRIMARY KEY, unit_path TEXT NOT NULL, country TEXT NOT NULL, "
    "display_name TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS org_log (seq INTEGER PRIMARY KEY, site_id TEXT NOT NULL, old_unit_path TEXT, "
    "new_unit_path TEXT, org_hash TEXT NOT NULL, at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS bundles (sha256 TEXT PRIMARY KEY, seq INTEGER UNIQUE NOT NULL, site TEXT NOT NULL, "
    "config_hash TEXT NOT NULL, as_of TEXT NOT NULL, after_week TEXT, closed_through TEXT NOT NULL, "
    "cells INTEGER NOT NULL, received_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS cells (site TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, "
    "predicate TEXT NOT NULL, iso_week TEXT NOT NULL, channel TEXT NOT NULL, n INTEGER, n_roots INTEGER, "
    "n_reporters INTEGER, res_conf_min REAL, as_of TEXT NOT NULL, bundle TEXT NOT NULL REFERENCES bundles(sha256), "
    "PRIMARY KEY (site, entity_type, entity_id, predicate, iso_week, channel))",
    "CREATE INDEX IF NOT EXISTS cells_site_week ON cells (site, iso_week)",
    "CREATE TRIGGER IF NOT EXISTS cells_no_update BEFORE UPDATE ON cells BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS cells_no_delete BEFORE DELETE ON cells BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS bundles_no_update BEFORE UPDATE ON bundles "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS bundles_no_delete BEFORE DELETE ON bundles "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TABLE IF NOT EXISTS rejections (seq INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, site TEXT, "
    "reason TEXT NOT NULL, path TEXT, keyword TEXT, line INTEGER, received_at TEXT NOT NULL, UNIQUE (sha256, reason))",
    "CREATE TABLE IF NOT EXISTS detection_runs (run_id TEXT PRIMARY KEY, run_channel TEXT NOT NULL, "
    "as_of TEXT NOT NULL, last_week TEXT NOT NULL, tie_salt TEXT NOT NULL, config_hash TEXT NOT NULL, "
    "detector_hash TEXT NOT NULL, org_hash TEXT NOT NULL, result_sha256 TEXT NOT NULL, saved_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS candidates (run_id TEXT NOT NULL REFERENCES detection_runs(run_id), "
    "key TEXT NOT NULL, first_candidate_week TEXT, detection_week TEXT, body BLOB NOT NULL, PRIMARY KEY (run_id, key))",
    "CREATE TABLE IF NOT EXISTS rule_hits (run_id TEXT NOT NULL REFERENCES detection_runs(run_id), "
    "rule_id TEXT NOT NULL, key TEXT NOT NULL, first_week TEXT NOT NULL, body BLOB NOT NULL, "
    "PRIMARY KEY (run_id, rule_id, key))",
)
_STORE_INFO = "SELECT key, value FROM store_info ORDER BY key"
_INSERT_STORE_INFO = "INSERT INTO store_info (key, value) VALUES (?, ?)"
_ORG_SITES = "SELECT site_id, unit_path, country, display_name FROM org_sites ORDER BY site_id"
_INSERT_ORG_SITE = "INSERT INTO org_sites (site_id, unit_path, country, display_name) VALUES (?, ?, ?, ?)"
_UPDATE_ORG_SITE = "UPDATE org_sites SET unit_path = ?, country = ?, display_name = ? WHERE site_id = ?"
_DELETE_ORG_SITE = "DELETE FROM org_sites WHERE site_id = ?"
_INSERT_ORG_LOG = ("INSERT INTO org_log (site_id, old_unit_path, new_unit_path, org_hash, at) "
                   "VALUES (?, ?, ?, ?, ?)")
_ORG_LOG = "SELECT seq, site_id, old_unit_path, new_unit_path, org_hash, at FROM org_log ORDER BY seq"
_HAS_BUNDLE = "SELECT seq FROM bundles WHERE sha256 = ? ORDER BY seq LIMIT 1"
_LAST_BUNDLE_SEQ = "SELECT seq FROM bundles ORDER BY seq DESC LIMIT 1"
_LAST_SITE_BUNDLE = "SELECT closed_through, as_of FROM bundles WHERE site = ? ORDER BY closed_through DESC LIMIT 1"
_STORED_IN_RANGE = (
    "SELECT entity_type, entity_id, predicate, iso_week, channel, n, n_roots, n_reporters, res_conf_min FROM cells "
    "WHERE site = ? AND iso_week <= ? AND (? IS NULL OR iso_week > ?) "
    "ORDER BY entity_type, entity_id, predicate, iso_week, channel")
_INSERT_BUNDLE = ("INSERT INTO bundles (sha256, seq, site, config_hash, as_of, after_week, closed_through, cells, "
                  "received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
_INSERT_CELL = ("INSERT INTO cells (site, entity_type, entity_id, predicate, iso_week, channel, n, n_roots, "
                "n_reporters, res_conf_min, as_of, bundle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
_INSERT_REJECTION = ("INSERT OR IGNORE INTO rejections (sha256, site, reason, path, keyword, line, received_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)")
_BUNDLES = "SELECT sha256, site, as_of, after_week, closed_through FROM bundles ORDER BY seq"
_REJECTIONS = "SELECT seq, sha256, site, reason, path, keyword, line, received_at FROM rejections ORDER BY seq"
_VISIBLE_BUNDLES = (
    "SELECT sha256, site, as_of, after_week, closed_through FROM bundles "
    "WHERE as_of <= ? AND site IN (SELECT site_id FROM org_sites) ORDER BY site, closed_through")
_VISIBLE_CELLS = (
    "SELECT site, entity_type, entity_id, predicate, iso_week, channel, n, n_roots, n_reporters, res_conf_min, "
    "bundle, as_of FROM cells WHERE as_of <= ? AND iso_week <= ? AND channel IN (?, ?) "
    "AND site IN (SELECT site_id FROM org_sites) "
    "ORDER BY site, entity_type, entity_id, predicate, iso_week, channel")
_RUN_SHA = "SELECT result_sha256 FROM detection_runs WHERE run_id = ? ORDER BY run_id"
_INSERT_RUN = ("INSERT INTO detection_runs (run_id, run_channel, as_of, last_week, tie_salt, config_hash, "
               "detector_hash, org_hash, result_sha256, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
_INSERT_CANDIDATE = ("INSERT INTO candidates (run_id, key, first_candidate_week, detection_week, body) "
                     "VALUES (?, ?, ?, ?, ?)")
_INSERT_RULE_HIT = "INSERT INTO rule_hits (run_id, rule_id, key, first_week, body) VALUES (?, ?, ?, ?, ?)"


class StoreError(ValueError):
    pass


class BundleRow(NamedTuple):
    sha256: str
    site: str
    as_of: str
    after: str | None
    closed_through: str


class CellRow(NamedTuple):
    """One stored cell; a count of None is ``'<k'`` and a ``res_conf_min`` of None is absent."""

    site: str
    entity_type: str
    entity_id: str
    predicate: str
    iso_week: str
    channel: str
    n: int | None
    n_roots: int | None
    n_reporters: int | None
    res_conf_min: float | None
    bundle: str
    as_of: str


class RejectionRow(NamedTuple):
    seq: int
    sha256: str
    site: str | None
    reason: str
    path: str | None
    keyword: str | None
    line: int | None
    received_at: str


class OrgLogRow(NamedTuple):
    seq: int
    site_id: str
    old_unit_path: str | None
    new_unit_path: str | None
    org_hash: str
    at: str


@dataclass(frozen=True)
class IngestReport:
    accepted: int
    duplicates: int
    ignored: int
    rejected: Mapping[str, int]


def _count(value: Any) -> int | None:
    return None if value == SUPPRESSED else value


def _valid_site(value: Any) -> str | None:
    return value if isinstance(value, str) and SITE_ID_RE.fullmatch(value) is not None else None


class CollectiveStore:
    def __init__(self, path: str | Path, pack: "FrozenPack", org: OrgConfig, *, clock: Callable[[], str]) -> None:
        self.path = Path(path)
        self.pack = pack
        self.org = org
        self._clock = clock
        conn = sqlite3.connect(str(self.path), isolation_level=None)
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if mode is None or str(mode[0]).lower() != "wal":
            conn.close()
            raise StoreError("the store needs SQLite WAL mode") from None
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        expected = {"schema_version": str(SCHEMA_VERSION), "pack_id": pack.id, "config_hash": pack.config_hash,
                    "enterprise": org.enterprise}
        mismatch = None
        opened = False
        try:
            with self._write():
                for statement in _DDL:
                    conn.execute(statement)
                stored = dict(conn.execute(_STORE_INFO).fetchall())
                if not stored:
                    conn.executemany(_INSERT_STORE_INFO, [(key, expected[key]) for key in STORE_INFO_KEYS])
                else:
                    mismatch = next((key for key in STORE_INFO_KEYS if stored.get(key) != expected[key]), None)
                if mismatch is None:
                    self._sync_org(conn)
            opened = mismatch is None
        finally:
            if not opened:
                conn.close()
        if mismatch is not None:
            raise StoreError(f"store_info mismatch: {mismatch}") from None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CollectiveStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    def _now(self) -> str:
        ts = self._clock()
        if not isinstance(ts, str) or TS_RE.fullmatch(ts) is None or local_date(ts) is None:
            raise ValueError("the clock must return an ISO 8601 timestamp") from None
        return ts

    def _sync_org(self, conn: sqlite3.Connection) -> None:
        stored = {row[0]: row[1:] for row in conn.execute(_ORG_SITES).fetchall()}
        current = self.org.sites
        writes: list[tuple[str, tuple[Any, ...]]] = []
        logs: list[tuple[str, str | None, str | None]] = []
        for site_id in sorted(set(stored) | set(current)):
            old, new = stored.get(site_id), current.get(site_id)
            if old is None:
                writes.append((_INSERT_ORG_SITE, (site_id, new.unit_path, new.country, new.display_name)))
                logs.append((site_id, None, new.unit_path))
            elif new is None:
                writes.append((_DELETE_ORG_SITE, (site_id,)))
                logs.append((site_id, old[0], None))
            elif tuple(old) != (new.unit_path, new.country, new.display_name):
                writes.append((_UPDATE_ORG_SITE, (new.unit_path, new.country, new.display_name, site_id)))
                if old[0] != new.unit_path:
                    logs.append((site_id, old[0], new.unit_path))
        if not writes:
            return
        at = self._now()
        for statement, args in writes:
            conn.execute(statement, args)
        conn.executemany(_INSERT_ORG_LOG, [(s, old, new, self.org.org_hash, at) for s, old, new in logs])

    # ------------------------------------------------------------------ ingest
    def _problem(self, conn: sqlite3.Connection, body: Any,
                 row_site: str | None) -> tuple[str, str | None, str | None] | None:
        """The first failing check ``(reason, path, keyword)`` of an already-parsed body, or None to accept."""
        if not isinstance(body, dict):
            return "invalid", "$", "type"
        if isinstance(body.get("config_hash"), str) and body["config_hash"] != self.pack.config_hash:
            return "config_hash", None, None
        site = body.get("site")
        if isinstance(site, str) and site not in self.org.sites:
            return "unknown_site", None, None
        # a site that is not a str cannot equal the empty site id the spec is then built for
        found = check_artifact(self.pack, site if isinstance(site, str) else "", CELLS_ARTIFACT, body)
        if found is not None:
            return "invalid", found[0], found[1]
        if row_site is not None and row_site != site:
            return "site_mismatch", None, None
        after, through = body["after"], body["closed_through"]
        stored = {tuple(row[:5]): tuple(row[5:])
                  for row in conn.execute(_STORED_IN_RANGE, (site, through, after, after)).fetchall()}
        for c in body["cells"]:
            key = (c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"], c["channel"])
            if key in stored and stored[key] != (_count(c["n"]), _count(c["n_roots"]), _count(c["n_reporters"]),
                                                 c.get("res_conf_min")):
                return "conflict", None, None
        last = conn.execute(_LAST_SITE_BUNDLE, (site,)).fetchone()
        if after != (last[0] if last is not None else None) or (last is not None and body["as_of"] < last[1]):
            return "sequence", None, None
        return None

    def ingest_bundle(self, body: Any, *, row_site: str | None = None) -> str:
        """``'accepted'``, ``'duplicate'`` or the rejection reason. A body canonical JSON refuses is a caller bug:
        :class:`StoreError`, and nothing is recorded."""
        data = None
        try:
            data = canonical_bytes(body)
        except StrictJsonError:
            data = None
        if data is None:
            raise StoreError("the bundle is not canonical JSON") from None
        sha = sha256_hex(data)
        parsed = strict_load(data)          # a copy that shares nothing with the caller's object
        with self._write() as conn:
            if conn.execute(_HAS_BUNDLE, (sha,)).fetchone() is not None:
                return "duplicate"
            problem = self._problem(conn, parsed, row_site)
            received_at = self._now()
            if problem is not None:
                reason, path, keyword = problem
                site = _valid_site(parsed.get("site")) if isinstance(parsed, dict) else None
                conn.execute(_INSERT_REJECTION, (sha, site, reason, path, keyword, None, received_at))
                return reason
            row = conn.execute(_LAST_BUNDLE_SEQ).fetchone()
            site = parsed["site"]
            conn.execute(_INSERT_BUNDLE, (sha, (row[0] if row is not None else 0) + 1, site, parsed["config_hash"],
                                          parsed["as_of"], parsed["after"], parsed["closed_through"],
                                          len(parsed["cells"]), received_at))
            conn.executemany(_INSERT_CELL, [
                (site, c["entity_type"], c["entity_id"], c["predicate"], c["iso_week"], c["channel"], _count(c["n"]),
                 _count(c["n_roots"]), _count(c["n_reporters"]), c.get("res_conf_min"), parsed["as_of"], sha)
                for c in parsed["cells"]])
        return "accepted"

    def _bad_line(self, raw: bytes, number: int) -> None:
        with self._write() as conn:
            conn.execute(_INSERT_REJECTION, (sha256_hex(raw), None, "bad_line", None, None, number, self._now()))

    def ingest_log(self, path: str | Path) -> IngestReport:
        """Every line of a receive log, in file order; an absent file is an empty report."""
        path = Path(path)
        accepted = duplicates = ignored = 0
        rejected = dict.fromkeys(REJECT_REASONS, 0)
        lines = path.read_bytes().split(b"\n") if path.exists() else []
        for number, raw in enumerate(lines, start=1):
            if not raw:
                continue
            row = problem = None
            try:
                row = strict_load(raw)
                problem = log_row_problem(row)
            except StrictJsonError:
                problem = "not strict JSON"
            if problem is not None:
                self._bad_line(raw, number)
                rejected["bad_line"] += 1
                continue
            if row["artifact_type"] != CELLS_ARTIFACT:
                ignored += 1
                continue
            result = self.ingest_bundle(row["body"], row_site=row["site"])
            if result == "accepted":
                accepted += 1
            elif result == "duplicate":
                duplicates += 1
            else:
                rejected[result] += 1
        return IngestReport(accepted=accepted, duplicates=duplicates, ignored=ignored,
                            rejected=MappingProxyType(rejected))

    # ------------------------------------------------------------------ readers
    def bundles(self) -> tuple[BundleRow, ...]:
        return tuple(BundleRow(*row) for row in self._conn.execute(_BUNDLES).fetchall())

    def rejections(self) -> tuple[RejectionRow, ...]:
        return tuple(RejectionRow(*row) for row in self._conn.execute(_REJECTIONS).fetchall())

    def org_log(self) -> tuple[OrgLogRow, ...]:
        return tuple(OrgLogRow(*row) for row in self._conn.execute(_ORG_LOG).fetchall())

    def detection_inputs(self, as_of: str, run_channel: str) -> tuple[tuple[BundleRow, ...], tuple[CellRow, ...]]:
        """The bundles and cells a run at ``as_of`` may see: nothing received with a later ``as_of``, no week after the
        last week closed at ``as_of``, only the run's channels and only the current org's sites."""
        if not isinstance(as_of, str) or local_date(as_of) != as_of:
            raise StoreError("as_of must be a calendar date YYYY-MM-DD") from None
        channels = RUN_CHANNELS.get(run_channel) if isinstance(run_channel, str) else None
        if channels is None:
            raise StoreError("run_channel must be X or S") from None
        last_week = closed_through(as_of, self.pack.egress.close_lag_days)
        bundles = tuple(BundleRow(*row) for row in self._conn.execute(_VISIBLE_BUNDLES, (as_of,)).fetchall())
        cells = tuple(CellRow(*row) for row in self._conn.execute(
            _VISIBLE_CELLS, (as_of, last_week, channels[0], channels[-1])).fetchall())
        return bundles, cells

    # ------------------------------------------------------------------ runs
    def save_run(self, result: Mapping[str, Any]) -> None:
        """Store a detection result, its candidates and its rule hits (each body as canonical bytes). Idempotent."""
        data = canonical_bytes(result)
        sha = sha256_hex(data)
        run_id = result["run_id"]
        with self._write() as conn:
            row = conn.execute(_RUN_SHA, (run_id,)).fetchone()
            if row is not None:
                if row[0] != sha:
                    raise StoreError("run conflict") from None
                return
            conn.execute(_INSERT_RUN, (run_id, result["run_channel"], result["as_of"], result["last_week"],
                                       result["tie_salt"], result["config_hash"], result["detector_hash"],
                                       result["org_hash"], sha, self._now()))
            conn.executemany(_INSERT_CANDIDATE, [(run_id, c["key"], c["first_candidate_week"], c["detection_week"],
                                                  canonical_bytes(c)) for c in result["candidates"]])
            conn.executemany(_INSERT_RULE_HIT, [(run_id, h["rule_id"], h["key"], h["first_week"], canonical_bytes(h))
                                                for h in result["rule_hits"]])
