"""The follow-up ledger (G7): ``followups.sqlite3``, append-only and hash-chained, and its chain check.

    python -m mycelic.collective.followup.ledger verify --ledger PATH [--expected-head HEX64]
        [--expected-entries N] [--dry-run]

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

The ledger is its own SQLite file, apart from HQ's collective store and from the fabric's database, and holds the only
follow-up SQL. It is opened like the other stores: ``isolation_level=None``, WAL (refused when the file system cannot
do WAL), ``synchronous=FULL``, foreign keys on and a 5 s busy timeout; the connection allows use from any thread and
is always used under the ledger's own lock. Every write is one ``BEGIN IMMEDIATE ... COMMIT``
(:meth:`FollowupLedger.transaction`), rolled back when anything (a crash included) raises; every SELECT is one literal
with ``ORDER BY``.

Tables: ``ledger_info`` pins ``schema_version``, ``pack_id``, ``config_hash`` and ``enterprise``; ``entries`` holds
``(seq, at, kind, key, actor, payload, prev_hash, hash)``. Triggers abort any ``UPDATE`` or ``DELETE`` on either.
``seq`` is the last plus one (no autoincrement), so a gap can only come from tampering. Partial unique indexes allow
per key at most one ``proposed``, one ``assigned``, one terminal decision (``approved`` or ``rejected``), one
``executing``, one closing entry (``executed`` or ``outcome_unknown``) and one escalation (``escalated`` or
``escalation_failed``); a violation inside :meth:`LedgerTx.append` is :class:`LedgerConflict`.

Each kind (:data:`KINDS`) has an exact payload key set (:data:`PAYLOAD_KEYS`); payloads are stored as canonical JSON.
``hash = sha256(prev_hash + canonical {seq, at, kind, key, actor, payload})`` and the first ``prev_hash`` is
:func:`genesis` (the sha256 of the canonical ``ledger_info``).

:func:`verify_chain` reads the file read-only and walks the entries in ``seq`` order: a seq gap, a ``prev_hash`` that
is not the running hash, a payload that is not strict canonical JSON with its kind's keys, a text cell that is not
valid UTF-8, or a recomputed hash that differs is a problem at that seq; any SQLite error, a failing
``integrity_check`` or a file without a well-formed ``ledger_info`` (an undecodable cell included) is ``unreadable``,
and so is a SQLite error message that cannot be decoded (it quotes a damaged schema name). Every connection decodes
TEXT cells strictly through :func:`_text`, so a flipped byte inside a cell is a bad value and never a
``UnicodeDecodeError``.

The file check is ``PRAGMA integrity_check``, not ``quick_check``: the chain walk reads the table, but every per-key
read of the service (:meth:`LedgerTx.entries`, :meth:`LedgerTx.entries_for_conclusion`) goes through the
``entries_key`` index, and only ``integrity_check`` compares an index with its table. One flipped byte in an index
page passes ``quick_check`` and the chain walk, and would hand the service a key's entries without, say, its
``approved`` row. After :meth:`FollowupLedger.open`, a read or write that meets a damaged page (``SQLITE_CORRUPT`` or
``SQLITE_NOTADB``), or a cell that is no longer what the chain checked, is :class:`LedgerError` (``unreadable`` or
``chain:bad_entry``), never a raw SQLite or decoding error; other SQLite errors (a busy file) propagate as they are.
Damage made while a ledger is open is found by the read that meets it or at the next open; until then a damaged index
can still mislead a per-key read, and the partial unique indexes are what keep one terminal decision and one
execution per key.

**Its limit:** without an anchor, a truncated tail or a complete rewrite of the file passes. Anchor with the head hash
and entry count a run file exported (``expected_head``, ``expected_entries``); a mismatch is ``anchor_mismatch``.
:meth:`FollowupLedger.open` refuses a missing file (creating none), an unreadable or corrupt one, another pack config
or enterprise, a broken chain, and an impossible sequence of entries (the service's ``replay``). :class:`LedgerError`
never holds a traceback or a value.

The ``verify`` CLI prints the chain report as one canonical JSON line (never a payload) and exits 0 when the chain is
whole, 1 when it is not (an entry problem at a seq, or an anchor mismatch), and 2 on a usage error, a missing file or
a file that is not a readable ledger (``unreadable``).
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, NamedTuple

from ..edge.egress import FOLLOWUP_KEY_RE
from ..edge.weeks import TS_RE, local_date
from ..jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from .policy import PERSON_LABEL_RE

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack

CLI = "followup.ledger verify"
LEDGER_FILE = "followups.sqlite3"
SCHEMA_VERSION = 1
INFO_KEYS = ("schema_version", "pack_id", "config_hash", "enterprise")
KINDS = ("proposed", "refused", "assigned", "drafted", "draft_failed", "approved", "edited", "rejected", "executing",
         "executed", "outcome_unknown", "blocked", "escalated", "escalation_failed", "outcome")
PAYLOAD_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType({kind: tuple(sorted(keys)) for kind, keys in (
    ("proposed", ("conclusion_id", "conclusion_version", "question_id", "candidate_key", "type", "tier", "executor",
                  "args", "targets", "as_of")),
    ("refused", ("op", "code", "conclusion_id", "type", "status", "path", "keyword", "arg")),
    ("assigned", ("owner", "role", "unit", "assignment_unit", "ack_due", "approvers_hash")),
    ("drafted", ("attempt", "version", "draft", "source")),
    ("draft_failed", ("attempt", "reason")),
    ("approved", ("version", "role", "unit_path", "approvers_hash", "conclusion_version", "as_of")),
    ("edited", ("base_version", "version", "draft", "diff", "as_of")),
    ("rejected", ("version", "role", "unit_path", "approvers_hash", "conclusion_version", "as_of")),
    ("executing", ("as_of",)),
    ("executed", ("result", "as_of")),
    ("outcome_unknown", ("reason",)),
    ("blocked", ("reason", "source", "as_of")),
    ("escalated", ("reason", "to_role", "to", "unit", "as_of")),
    ("escalation_failed", ("reason", "to_role", "problem", "as_of")),
    ("outcome", ("result", "as_of")),
)})
CHAIN_PROBLEMS = ("unreadable", "seq_gap", "prev_hash_mismatch", "hash_mismatch", "bad_entry", "anchor_mismatch")
LEDGER_REASONS = ("missing", "exists", "unreadable", "wal", "conflict")
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
# A damaged file fails a read with a SQLite error, or with UnicodeDecodeError when SQLite's own message quotes a schema
# name whose bytes were flipped (Python decodes the message as UTF-8). Cells never raise: see _text.
_READ_ERRORS = (sqlite3.DatabaseError, UnicodeDecodeError)
# SQLite's primary result codes for a damaged file: SQLITE_CORRUPT and SQLITE_NOTADB (see _damage_is_unreadable).
_DAMAGED_CODES = (11, 26)
# The file check of open() and verify_chain(): integrity_check, because quick_check never compares an index with its
# table and the service reads a key's entries through the entries_key index (see the module docstring).
_INTEGRITY = "PRAGMA integrity_check"

_DDL = (
    "CREATE TABLE IF NOT EXISTS ledger_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS entries (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, "
    "key TEXT NOT NULL, actor TEXT NOT NULL, payload BLOB NOT NULL, prev_hash TEXT NOT NULL, "
    "hash TEXT NOT NULL UNIQUE)",
    "CREATE INDEX IF NOT EXISTS entries_key ON entries (key, seq)",
    "CREATE UNIQUE INDEX IF NOT EXISTS entries_one_proposed ON entries (key) WHERE kind = 'proposed'",
    "CREATE UNIQUE INDEX IF NOT EXISTS entries_one_assigned ON entries (key) WHERE kind = 'assigned'",
    "CREATE UNIQUE INDEX IF NOT EXISTS entries_one_decision ON entries (key) WHERE kind IN ('approved', 'rejected')",
    "CREATE UNIQUE INDEX IF NOT EXISTS entries_one_executing ON entries (key) WHERE kind = 'executing'",
    "CREATE UNIQUE INDEX IF NOT EXISTS entries_one_closing ON entries (key) "
    "WHERE kind IN ('executed', 'outcome_unknown')",
    "CREATE UNIQUE INDEX IF NOT EXISTS entries_one_escalation ON entries (key) "
    "WHERE kind IN ('escalated', 'escalation_failed')",
    *(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
      "BEGIN SELECT RAISE(ABORT, 'append-only'); END" for table in ("entries", "ledger_info")
      for op in ("UPDATE", "DELETE")),
)
_INFO = "SELECT key, value FROM ledger_info ORDER BY key"
_INSERT_INFO = "INSERT INTO ledger_info (key, value) VALUES (?, ?)"
_LAST = "SELECT seq, hash FROM entries ORDER BY seq DESC LIMIT 1"
_ENTRIES = "SELECT seq, at, kind, key, actor, payload, prev_hash, hash FROM entries ORDER BY seq"
_KEY_ENTRIES = ("SELECT seq, at, kind, key, actor, payload, prev_hash, hash FROM entries WHERE key = ? "
                "ORDER BY seq")
_RANGE_ENTRIES = ("SELECT seq, at, kind, key, actor, payload, prev_hash, hash FROM entries "
                  "WHERE key >= ? AND key < ? ORDER BY seq")
_PROPOSED = "SELECT seq, payload FROM entries WHERE kind = 'proposed' ORDER BY seq"
_INSERT_ENTRY = ("INSERT INTO entries (seq, at, kind, key, actor, payload, prev_hash, hash) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")


class LedgerError(ValueError):
    """``reason`` is one of :data:`LEDGER_REASONS`, ``info_mismatch:<key>`` or ``chain:<problem>``; ``seq`` is the
    entry a chain problem was found at. The text never holds a value."""

    def __init__(self, reason: str, seq: int | None = None) -> None:
        super().__init__()
        self.reason = reason
        self.seq = seq

    def __str__(self) -> str:
        if self.reason == "missing":
            return "ledger missing"
        if self.reason == "exists":
            return "ledger exists"
        if self.reason == "unreadable":
            return "ledger corrupt: unreadable"
        if self.reason == "wal":
            return "the ledger needs SQLite WAL mode"
        if self.reason == "conflict":
            return "ledger conflict: a unique entry already exists for this key"
        if self.reason.startswith("info_mismatch:"):
            return f"ledger_info mismatch: {self.reason.split(':', 1)[1]}"
        problem = self.reason.split(":", 1)[1] if self.reason.startswith("chain:") else self.reason
        return f"ledger corrupt: {problem}" + (f" at seq {self.seq}" if self.seq is not None else "")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(reason={self.reason!r}, seq={self.seq!r})"


class LedgerConflict(LedgerError):
    """A unique-index violation inside :meth:`LedgerTx.append`: another writer got there first."""

    def __init__(self) -> None:
        super().__init__("conflict")


class Entry(NamedTuple):
    seq: int
    at: str
    kind: str
    key: str
    actor: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    entries: int
    head_hash: str | None
    problem: str | None
    seq: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "entries": self.entries, "head_hash": self.head_hash, "problem": self.problem,
                "seq": self.seq}


def genesis(info: Mapping[str, str]) -> str:
    """The first ``prev_hash``: the sha256 of the canonical ``ledger_info``."""
    return sha256_hex(canonical_bytes({key: info[key] for key in INFO_KEYS}))


def entry_hash(prev_hash: str, seq: int, at: str, kind: str, key: str, actor: str, payload: Any) -> str:
    return sha256_hex(prev_hash.encode("ascii") + canonical_bytes({"seq": seq, "at": at, "kind": kind, "key": key,
                                                                    "actor": actor, "payload": payload}))


def _uri(path: Path, mode: str) -> str:
    return f"{path.resolve().as_uri()}?mode={mode}"


class _Undecodable:
    """What a TEXT cell that is not valid UTF-8 reads as (a flipped byte, a forged cell). It is never a str and equals
    only itself, so every check sees a bad value where Python's own decoding would raise ``UnicodeDecodeError``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<undecodable text>"


_UNDECODABLE = _Undecodable()


def _text(data: bytes) -> Any:
    """Every ledger connection's ``text_factory``: strict UTF-8, else :data:`_UNDECODABLE`. BLOB cells still read as
    bytes, so a cell whose type was flipped stays distinguishable from its text."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return _UNDECODABLE


def _connect(database: str, *, uri: bool, shared: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(database, uri=uri, isolation_level=None, check_same_thread=not shared)
    conn.text_factory = _text
    return conn


def _payload(kind: Any, data: Any) -> dict[str, Any] | None:
    """The parsed payload when it is strict canonical JSON with ``kind``'s exact keys, else None."""
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(data, (bytes, bytearray)):
        return None
    value = None
    try:
        value = strict_load(bytes(data))
    except StrictJsonError:
        value = None
    if not isinstance(value, dict) or tuple(sorted(value)) != PAYLOAD_KEYS[kind]:
        return None
    return value if canonical_bytes(value) == bytes(data) else None


def _info_ok(info: Mapping[Any, Any]) -> bool:
    """Exactly the :data:`INFO_KEYS`, each a str (an undecodable or retyped cell is not; nothing here sorts them)."""
    return set(info) == set(INFO_KEYS) and all(isinstance(info[k], str) for k in INFO_KEYS)


def _walk(info: Mapping[str, str], rows: list[tuple[Any, ...]]) -> ChainReport:
    running = genesis(info)
    for expected, row in enumerate(rows, start=1):
        seq, at, kind, key, actor, data, prev_hash, digest = row
        if seq != expected:
            return ChainReport(False, len(rows), None, "seq_gap", expected)
        if prev_hash != running:
            return ChainReport(False, len(rows), None, "prev_hash_mismatch", seq)
        payload = _payload(kind, data)
        if payload is None or not all(isinstance(v, str) for v in (at, key, actor, digest)):
            return ChainReport(False, len(rows), None, "bad_entry", seq)
        if digest != entry_hash(prev_hash, seq, at, kind, key, actor, payload):
            return ChainReport(False, len(rows), None, "hash_mismatch", seq)
        running = digest
    return ChainReport(True, len(rows), running, None, None)


def verify_chain(path: str | Path, *, expected_head: str | None = None,
                 expected_entries: int | None = None) -> ChainReport:
    """Walk the chain of the ledger at ``path`` read-only; see the module docstring."""
    path = Path(path)
    unreadable = ChainReport(False, 0, None, "unreadable", None)
    if not path.is_file():
        return unreadable
    rows = info = None
    conn = None
    try:
        conn = _connect(_uri(path, "ro"), uri=True)
        if conn.execute(_INTEGRITY).fetchall() == [("ok",)]:
            info = dict(conn.execute(_INFO).fetchall())
            rows = conn.execute(_ENTRIES).fetchall()
    except _READ_ERRORS:
        rows = None
    finally:
        if conn is not None:
            conn.close()
    if rows is None or info is None or not _info_ok(info):
        return unreadable
    report = _walk(info, rows)
    if report.ok and ((expected_head is not None and report.head_hash != expected_head)
                      or (expected_entries is not None and report.entries != expected_entries)):
        return ChainReport(False, report.entries, report.head_hash, "anchor_mismatch", None)
    return report


@contextmanager
def _damage_is_unreadable() -> Iterator[None]:
    """Around every read and write after open: a damaged page (``SQLITE_CORRUPT``, ``SQLITE_NOTADB``) or a SQLite
    message that cannot be decoded is ``LedgerError('unreadable')``; any other SQLite error (a busy file, a unique-index
    conflict) propagates unchanged."""
    damaged = False
    try:
        yield
    except UnicodeDecodeError:
        damaged = True
    except sqlite3.DatabaseError as err:
        if (getattr(err, "sqlite_errorcode", None) or 0) & 0xFF not in _DAMAGED_CODES:
            raise
        damaged = True
    if damaged:
        raise LedgerError("unreadable") from None


def _entry(row: tuple[Any, ...]) -> Entry:
    """A row read after the chain was checked; a payload damaged since (not a canonical blob of its kind's keys) or a
    text cell that is no longer a string (an undecodable cell reads as a non-string marker, see :func:`_text`) is
    ``LedgerError('chain:bad_entry', seq)``, never a decoding error."""
    seq, at, kind, key, actor, data, prev_hash, digest = row
    payload = _payload(kind, data)
    if payload is None or not all(isinstance(v, str) for v in (at, key, actor, prev_hash, digest)):
        raise LedgerError("chain:bad_entry", seq) from None
    return Entry(seq, at, kind, key, actor, payload, prev_hash, digest)


def _last(conn: sqlite3.Connection) -> tuple[int, str] | None:
    """The last entry's ``(seq, hash)``, or None for an empty ledger; a hash cell that is no longer 64 hex is
    ``LedgerError('chain:bad_entry', seq)``."""
    with _damage_is_unreadable():
        last = conn.execute(_LAST).fetchone()
    if last is None:
        return None
    if not isinstance(last[1], str) or _HEX64.fullmatch(last[1]) is None:
        raise LedgerError("chain:bad_entry", last[0]) from None
    return last[0], last[1]


def _configure(conn: sqlite3.Connection) -> bool:
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    if mode is None or str(mode[0]).lower() != "wal":
        return False
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return True


class LedgerTx:
    """The reads and the one write of a ledger transaction; only :meth:`FollowupLedger.transaction` makes one."""

    def __init__(self, ledger: "FollowupLedger") -> None:
        self._ledger = ledger
        self._conn = ledger._conn

    def entries(self, key: str) -> list[Entry]:
        with _damage_is_unreadable():
            rows = self._conn.execute(_KEY_ENTRIES, (key,)).fetchall()
        return [_entry(row) for row in rows]

    def entries_for_conclusion(self, conclusion_id: str) -> list[Entry]:
        """Every entry whose key belongs to ``conclusion_id`` (``act:<cid>:...``), in seq order."""
        with _damage_is_unreadable():
            rows = self._conn.execute(_RANGE_ENTRIES, (f"act:{conclusion_id}:", f"act:{conclusion_id};")).fetchall()
        return [_entry(row) for row in rows]

    def proposed_count(self, type_id: str, day: str) -> int:
        """``proposed`` entries of ``type_id`` whose ``as_of`` falls on the UTC date ``day``."""
        count = 0
        with _damage_is_unreadable():
            rows = self._conn.execute(_PROPOSED).fetchall()
        for seq, data in rows:
            payload = _payload("proposed", data)
            if payload is None:
                raise LedgerError("chain:bad_entry", seq) from None
            if payload["type"] == type_id and payload["as_of"][:10] == day:
                count += 1
        return count

    def append(self, kind: str, key: str, actor: str, payload: Mapping[str, Any]) -> Entry:
        if kind not in KINDS:
            raise ValueError("unknown ledger entry kind") from None
        if not isinstance(payload, Mapping) or tuple(sorted(payload)) != PAYLOAD_KEYS[kind]:
            raise ValueError("the payload does not have the kind's exact keys") from None
        if not isinstance(key, str) or (key != "-" and FOLLOWUP_KEY_RE.fullmatch(key) is None):
            raise ValueError("the key is a follow-up key or '-'") from None
        if not isinstance(actor, str) or (actor != "system" and PERSON_LABEL_RE.fullmatch(actor) is None):
            raise ValueError("the actor is system or a person label") from None
        data = canonical_bytes(dict(payload))
        at = self._ledger._now()
        last = _last(self._conn)
        seq = (last[0] if last is not None else 0) + 1
        prev_hash = last[1] if last is not None else self._ledger.genesis
        parsed = strict_load(data)
        digest = entry_hash(prev_hash, seq, at, kind, key, actor, parsed)
        conflict = False
        try:
            with _damage_is_unreadable():
                self._conn.execute(_INSERT_ENTRY, (seq, at, kind, key, actor, data, prev_hash, digest))
        except sqlite3.IntegrityError:
            conflict = True
        if conflict:
            raise LedgerConflict() from None
        return Entry(seq, at, kind, key, actor, parsed, prev_hash, digest)


class FollowupLedger:
    """One ledger file. Build it with :meth:`create` or :meth:`open`."""

    def __init__(self, conn: sqlite3.Connection, path: Path, info: Mapping[str, str],
                 clock: Callable[[], str]) -> None:
        self._conn = conn
        self.path = path
        self.info: Mapping[str, str] = MappingProxyType(dict(info))
        self.genesis = genesis(info)
        self._clock = clock
        self._lock = threading.RLock()

    @staticmethod
    def _expected(pack: "FrozenPack", enterprise: str) -> dict[str, str]:
        return {"schema_version": str(SCHEMA_VERSION), "pack_id": pack.id, "config_hash": pack.config_hash,
                "enterprise": enterprise}

    @classmethod
    def create(cls, path: str | Path, *, pack: "FrozenPack", enterprise: str,
               clock: Callable[[], str]) -> "FollowupLedger":
        """A new ledger at ``path``; any existing path (an empty file included) is :class:`LedgerError` ``exists``."""
        path = Path(path)
        if path.exists() or path.is_symlink():
            raise LedgerError("exists") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(str(path), uri=False, shared=True)
        if not _configure(conn):
            conn.close()
            raise LedgerError("wal") from None
        info = cls._expected(pack, enterprise)
        committed = False
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _DDL:
                conn.execute(statement)
            conn.executemany(_INSERT_INFO, [(key, info[key]) for key in INFO_KEYS])
            conn.execute("COMMIT")
            committed = True
        finally:
            if not committed:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                conn.close()
        return cls(conn, path, info, clock)

    @classmethod
    def open(cls, path: str | Path, *, pack: "FrozenPack", enterprise: str,
             clock: Callable[[], str]) -> "FollowupLedger":
        """An existing ledger, checked: missing, unreadable, ``ledger_info`` of another pack config or enterprise, a
        broken chain or an impossible sequence of entries is :class:`LedgerError`. A missing path creates no file."""
        from .service import replay           # the follow-up state lives in service, which imports this module
        path = Path(path)
        if not path.is_file():
            raise LedgerError("missing") from None
        conn = None
        stored: dict[str, Any] | None = None
        wal = True
        try:
            conn = _connect(_uri(path, "rw"), uri=True, shared=True)
            if conn.execute(_INTEGRITY).fetchall() == [("ok",)]:
                stored = dict(conn.execute(_INFO).fetchall())
                wal = _configure(conn)
        except _READ_ERRORS:
            stored = None
        if stored is None or not _info_ok(stored) or not wal:
            if conn is not None:
                conn.close()
            raise LedgerError("unreadable" if wal else "wal") from None
        expected = cls._expected(pack, enterprise)
        mismatch = next((key for key in INFO_KEYS if stored[key] != expected[key]), None)
        report = verify_chain(path) if mismatch is None else None
        failure: LedgerError | None = None
        if mismatch is not None:
            failure = LedgerError(f"info_mismatch:{mismatch}")
        elif not report.ok:
            failure = LedgerError("unreadable" if report.problem == "unreadable" else f"chain:{report.problem}",
                                  report.seq)
        ledger = None
        if failure is None:
            ledger = cls(conn, path, stored, clock)
            try:
                replay(ledger.entries())
            except LedgerError as err:
                failure = err
        if failure is not None:
            conn.close()
            raise failure from None
        return ledger

    def _now(self) -> str:
        ts = self._clock()
        if not isinstance(ts, str) or TS_RE.fullmatch(ts) is None or local_date(ts) is None:
            raise ValueError("the clock must return an ISO 8601 timestamp") from None
        return ts

    @contextmanager
    def transaction(self) -> Iterator[LedgerTx]:
        """One ``BEGIN IMMEDIATE ... COMMIT`` under the ledger's lock; rolled back when the body raises anything."""
        with self._lock:
            with _damage_is_unreadable():
                self._conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                yield LedgerTx(self)
                with _damage_is_unreadable():
                    self._conn.execute("COMMIT")
                committed = True
            finally:
                if not committed and self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")

    def entries(self, key: str | None = None) -> list[Entry]:
        with self._lock, _damage_is_unreadable():
            rows = (self._conn.execute(_ENTRIES).fetchall() if key is None
                    else self._conn.execute(_KEY_ENTRIES, (key,)).fetchall())
        return [_entry(row) for row in rows]

    def head_hash(self) -> str:
        """The hash of the last entry, or the genesis hash of an empty ledger."""
        with self._lock:
            last = _last(self._conn)
        return last[1] if last is not None else self.genesis

    def verify_chain(self, *, expected_head: str | None = None, expected_entries: int | None = None) -> ChainReport:
        return verify_chain(self.path, expected_head=expected_head, expected_entries=expected_entries)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.followup.ledger",
                                description="Check a follow-up ledger's hash chain (G7; built ahead of E2 and X4).")
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="walk the chain and print one JSON line; exit 0 whole, 1 broken, 2 unusable")
    v.add_argument("--ledger", required=True, help="the followups.sqlite3 file")
    v.add_argument("--expected-head", help="the head hash a run file exported (64 hex)")
    v.add_argument("--expected-entries", type=int, help="the entry count a run file exported")
    v.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.expected_head is not None and _HEX64.fullmatch(args.expected_head) is None:
        print("error: --expected-head must be 64 lowercase hex characters", file=sys.stderr)
        return 2
    if args.expected_entries is not None and args.expected_entries < 0:
        print("error: --expected-entries must be >= 0", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"dry-run: {CLI}")
        if not Path(args.ledger).exists():
            print(f"would need: ledger {args.ledger}")
        return 0
    if not Path(args.ledger).is_file():
        print("error: ledger missing", file=sys.stderr)
        return 2
    report = verify_chain(args.ledger, expected_head=args.expected_head, expected_entries=args.expected_entries)
    print(canonical_dumps(report.to_dict()))
    if report.problem == "unreadable":
        return 2
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
