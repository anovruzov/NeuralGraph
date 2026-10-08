"""Holder-local durable ingestion queue (docs/mycelic/INGESTION.md §3.6, §9.4, §9.5).

It lives in the holder's own file (never ``coord.jobs``) and mirrors the semantics of :mod:`mycelic.jobs`:

* **Idempotency.** ``event_key`` is UNIQUE: re-fetched pages, overlapping windows and re-delivered notices collapse.
* **Atomic pages.** :meth:`IngestQueue.commit_page` inserts a page's events *and* advances the stream cursor in one
  transaction, fenced on the checkpoint ``version``: a zombie runner whose view of the cursor is stale updates no row and
  its whole page rolls back. A crash before the commit means the page is fetched again and deduplicated; after it, nothing
  is lost.
* **Leases.** A leased item carries ``worker_id`` and ``leased_until``; acks are fenced on both (a worker whose lease
  expired gets :class:`LostLease`); expired leases are swept back to ``queued``.
* **Per-record ordering.** An item is not leased while an older item of the same record is queued or leased.
* **Retries and dead letters.** Backoff ``min(900, 5 * 2**(attempts-1)) * U(0.8, 1.2)`` seconds up to ``max_attempts``,
  then ``dead`` with the error code (never content). Rate limits park an item without consuming an attempt.
* **Priority classes.** ``delete`` is always served first; the rest share lease slots by deficit round robin with weights
  live 8, user 8, backfill 1, reindex 1, maintenance 1 — live ingestion is never starved by a backfill.
* **Backpressure.** :meth:`fetch_allowed` stops backfill fetching above a high watermark and resumes below the low one.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from ..util import j, jl, now_precise, plus_seconds
from .contract import Cursor, get_logger
from .events import CanonicalEvent, decode_payload, encode_payload

logger = get_logger(__name__)

PRIORITY_CLASSES = ("delete", "live", "user", "backfill", "reindex", "maintenance")
WEIGHTS = {"live": 8, "user": 8, "backfill": 1, "reindex": 1, "maintenance": 1}
DRR_ORDER = ("live", "user", "backfill", "reindex", "maintenance")
WATERMARKS = {"live": (2000, 500), "user": (2000, 500), "backfill": (5000, 1000), "reindex": (5000, 1000), "maintenance": (1000, 200)}
DEFAULT_LEASE_SECONDS = 120.0
DEFAULT_MAX_ATTEMPTS = 6


class LostLease(RuntimeError):
    """The item is no longer leased by this worker (lease expired and was swept or re-leased)."""


class StaleCheckpoint(RuntimeError):
    """``commit_page`` was fenced: the stream's checkpoint version moved on (another runner committed first)."""


@dataclass
class QueueItem:
    item_id: int
    event_key: str
    record_key: str
    connector_id: str
    stream: str
    kind: str
    priority_class: str
    order_key: str
    status: str
    attempts: int
    max_attempts: int
    worker_id: str | None
    last_error_code: str | None
    payload: bytes | None

    def event(self) -> CanonicalEvent:
        if self.payload is None:
            raise ValueError("queue item has no payload (purged)")
        return decode_payload(self.payload)


@dataclass
class CommitResult:
    enqueued: int
    duplicates: int
    version: int


def backoff_seconds(attempts: int, *, base: float = 5.0, cap: float = 900.0) -> float:
    return min(cap, base * (2 ** max(0, attempts - 1))) * (0.8 + 0.4 * random.random())


class IngestQueue:
    """``store`` is the holder's :class:`~mycelic.evidence.store.MycelicMemoryStore` (the queue shares its connection,
    lock and transaction discipline)."""

    def __init__(self, store: Any, *, lease_seconds: float = DEFAULT_LEASE_SECONDS, watermarks: dict[str, tuple[int, int]] | None = None) -> None:
        self.store = store
        self.lease_seconds = float(lease_seconds)
        self.watermarks = dict(WATERMARKS, **(watermarks or {}))
        self._deficit = {c: 0 for c in DRR_ORDER}
        self._rr = 0
        self._fresh = True
        self._paused: dict[str, bool] = {c: False for c in PRIORITY_CLASSES}

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store._conn

    # ------------------------------------------------------------------ checkpoints
    def load_checkpoint_sync(self, c: sqlite3.Connection, connector_id: str, stream: str) -> tuple[Cursor | None, int]:
        r = c.execute("SELECT cursor, version FROM connector_checkpoints WHERE connector_id=? AND stream=?", (connector_id, stream)).fetchone()
        if r is None:
            return None, 0
        return Cursor.from_dict(jl(r["cursor"], {})), int(r["version"])

    async def load_checkpoint(self, connector_id: str, stream: str) -> tuple[Cursor | None, int]:
        return self.load_checkpoint_sync(self.conn, connector_id, stream)

    # ------------------------------------------------------------------ enqueue
    def _insert_events_sync(self, c: sqlite3.Connection, connector_id: str, stream: str, events: Iterable[CanonicalEvent], priority_class: str,
                            now: str) -> tuple[int, int]:
        enq = dup = 0
        for ev in events:
            cls = "delete" if ev.kind in ("deletion", "redaction") else priority_class
            blob = encode_payload(ev)
            cur = c.execute("""INSERT INTO ingest_queue(event_key, record_key, connector_id, stream, kind, priority_class, order_key, status, attempts,
                                                        max_attempts, available_at, payload, payload_bytes, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?) ON CONFLICT(event_key) DO NOTHING""",
                            (ev.event_key, ev.record_key, connector_id, stream, ev.kind, cls, ev.order_key, DEFAULT_MAX_ATTEMPTS, now, blob, len(blob),
                             now, now))
            if cur.rowcount:
                enq += 1
            else:
                dup += 1
        return enq, dup

    async def commit_page(self, connector_id: str, stream: str, events: list[CanonicalEvent], cursor: Cursor | None, *,
                          priority_class: str, expected_version: int, phase: str = "incremental", items_seen: int = 0,
                          window: tuple[str, str] | None = None, has_more: bool = True) -> CommitResult:
        """Events and the stream cursor in ONE transaction, fenced on the checkpoint version."""
        if priority_class not in PRIORITY_CLASSES:
            raise ValueError(f"unknown priority class {priority_class!r}")
        now = now_precise()

        def fn(c: sqlite3.Connection) -> CommitResult:
            row = c.execute("SELECT version FROM connector_checkpoints WHERE connector_id=? AND stream=?", (connector_id, stream)).fetchone()
            if row is None:
                if expected_version != 0:
                    raise StaleCheckpoint(stream)
                c.execute("""INSERT INTO connector_checkpoints(connector_id, stream, phase, cursor, window_start, window_end, status, items_seen,
                                                               items_enqueued, version, updated_at) VALUES (?, ?, ?, '{}', ?, ?, 'running', 0, 0, 0, ?)""",
                          (connector_id, stream, phase, window[0] if window else None, window[1] if window else None, now))
            enq, dup = self._insert_events_sync(c, connector_id, stream, events, priority_class, now)
            # a None cursor means "finished for now": the stored position is kept so the next pass continues from it
            cur = c.execute("""UPDATE connector_checkpoints SET cursor=COALESCE(?, cursor), version=version+1, items_seen=items_seen+?,
                               items_enqueued=items_enqueued+?, status=?, last_error_code=NULL, updated_at=?
                               WHERE connector_id=? AND stream=? AND version=?""",
                            (j(cursor.to_dict()) if cursor else None, int(items_seen), enq, "running" if has_more else "idle", now, connector_id,
                             stream, int(expected_version)))
            if cur.rowcount != 1:
                raise StaleCheckpoint(stream)       # rolls the whole page back
            return CommitResult(enq, dup, int(expected_version) + 1)

        return await self.store.run_in_tx(fn)

    async def enqueue(self, connector_id: str, stream: str, events: list[CanonicalEvent], *, priority_class: str = "user") -> CommitResult:
        """Enqueue without a cursor (owner actions, reindex, deletions requested by the owner)."""
        now = now_precise()

        def fn(c: sqlite3.Connection) -> CommitResult:
            enq, dup = self._insert_events_sync(c, connector_id, stream, events, priority_class, now)
            return CommitResult(enq, dup, 0)

        return await self.store.run_in_tx(fn)

    async def set_stream_status(self, connector_id: str, stream: str, status: str, error_code: str | None = None) -> None:
        def fn(c: sqlite3.Connection) -> None:
            c.execute("UPDATE connector_checkpoints SET status=?, last_error_code=?, updated_at=? WHERE connector_id=? AND stream=?",
                      (status, error_code, now_precise(), connector_id, stream))
        await self.store.run_in_tx(fn)

    # ------------------------------------------------------------------ lease
    def _runnable_classes_sync(self, c: sqlite3.Connection, now: str) -> set[str]:
        rows = c.execute("SELECT DISTINCT priority_class FROM ingest_queue WHERE status='queued' AND available_at <= ?", (now,)).fetchall()
        return {r["priority_class"] for r in rows}

    def _next_class(self, runnable: set[str]) -> str | None:
        if "delete" in runnable:
            return "delete"
        if not runnable & set(DRR_ORDER):
            return None
        for _ in range(4 * len(DRR_ORDER)):
            cls = DRR_ORDER[self._rr]
            if cls not in runnable:
                self._deficit[cls] = 0
                self._rr = (self._rr + 1) % len(DRR_ORDER)
                self._fresh = True
                continue
            if self._fresh:
                self._deficit[cls] += WEIGHTS[cls]
                self._fresh = False
            if self._deficit[cls] >= 1:
                self._deficit[cls] -= 1
                return cls
            self._rr = (self._rr + 1) % len(DRR_ORDER)
            self._fresh = True
        return None

    def _candidate_sync(self, c: sqlite3.Connection, cls: str, now: str) -> sqlite3.Row | None:
        return c.execute("""SELECT * FROM ingest_queue q
                            WHERE q.status='queued' AND q.priority_class=? AND q.available_at <= ?
                              AND NOT EXISTS (SELECT 1 FROM ingest_queue p WHERE p.record_key = q.record_key AND p.status IN ('queued','leased')
                                              AND p.item_id <> q.item_id AND p.order_key < q.order_key)
                            ORDER BY q.available_at, q.item_id LIMIT 1""", (cls, now)).fetchone()

    async def lease(self, worker_id: str, *, n: int = 1, lease_seconds: float | None = None) -> list[QueueItem]:
        """Lease up to ``n`` items, one class decision per item (delete first, then deficit round robin)."""
        until = plus_seconds(lease_seconds or self.lease_seconds)

        def fn(c: sqlite3.Connection) -> list[QueueItem]:
            now = now_precise()
            out: list[QueueItem] = []
            blocked: set[str] = set()
            for _ in range(max(1, n)):
                runnable = self._runnable_classes_sync(c, now) - blocked
                row = None
                while runnable:
                    cls = self._next_class(runnable)
                    if cls is None:
                        break
                    row = self._candidate_sync(c, cls, now)
                    if row is not None:
                        break
                    runnable.discard(cls)       # only items blocked behind an older version of their record
                    blocked.add(cls)
                if row is None:
                    break
                c.execute("UPDATE ingest_queue SET status='leased', leased_until=?, worker_id=?, attempts=attempts+1, updated_at=? WHERE item_id=?",
                          (until, worker_id, now, row["item_id"]))
                out.append(self._row(c.execute("SELECT * FROM ingest_queue WHERE item_id=?", (row["item_id"],)).fetchone()))
            return out

        return await self.store.run_in_tx(fn)

    @staticmethod
    def _row(r: sqlite3.Row) -> QueueItem:
        return QueueItem(item_id=int(r["item_id"]), event_key=r["event_key"], record_key=r["record_key"], connector_id=r["connector_id"],
                         stream=r["stream"], kind=r["kind"], priority_class=r["priority_class"], order_key=r["order_key"], status=r["status"],
                         attempts=int(r["attempts"]), max_attempts=int(r["max_attempts"]), worker_id=r["worker_id"],
                         last_error_code=r["last_error_code"], payload=r["payload"])

    def holds_lease_sync(self, c: sqlite3.Connection, item_id: int, worker_id: str) -> bool:
        return c.execute("SELECT 1 FROM ingest_queue WHERE item_id=? AND status='leased' AND worker_id=?", (item_id, worker_id)).fetchone() is not None

    async def heartbeat(self, item_id: int, worker_id: str, lease_seconds: float | None = None) -> bool:
        def fn(c: sqlite3.Connection) -> bool:
            cur = c.execute("UPDATE ingest_queue SET leased_until=?, updated_at=? WHERE item_id=? AND status='leased' AND worker_id=?",
                            (plus_seconds(lease_seconds or self.lease_seconds), now_precise(), item_id, worker_id))
            return cur.rowcount == 1
        return await self.store.run_in_tx(fn)

    # ------------------------------------------------------------------ finish
    def ack_sync(self, c: sqlite3.Connection, item_id: int, worker_id: str, outcome: str) -> None:
        """Mark done inside the caller's (effect) transaction; the payload leaves the queue. Raises :class:`LostLease`."""
        now = now_precise()
        cur = c.execute("""UPDATE ingest_queue SET status='done', outcome=?, payload=NULL, payload_bytes=0, finished_at=?, updated_at=?,
                           leased_until=NULL WHERE item_id=? AND status='leased' AND worker_id=?""", (outcome, now, now, item_id, worker_id))
        if cur.rowcount != 1:
            raise LostLease(str(item_id))

    async def ack(self, item_id: int, worker_id: str, outcome: str) -> None:
        await self.store.run_in_tx(lambda c: self.ack_sync(c, item_id, worker_id, outcome))

    async def fail(self, item_id: int, worker_id: str, error_code: str, *, retryable: bool = True) -> str:
        """Requeue with backoff, or dead-letter. Returns ``queued`` | ``dead`` | ``lost``. ``error_code`` is a code, never content."""
        code = (error_code or "error")[:80]

        def fn(c: sqlite3.Connection) -> str:
            r = c.execute("SELECT attempts, max_attempts, status, worker_id FROM ingest_queue WHERE item_id=?", (item_id,)).fetchone()
            if r is None or r["status"] != "leased" or r["worker_id"] != worker_id:
                return "lost"
            now = now_precise()
            if not retryable or int(r["attempts"]) >= int(r["max_attempts"]):
                c.execute("""UPDATE ingest_queue SET status='dead', last_error_code=?, finished_at=?, updated_at=?, leased_until=NULL, worker_id=NULL
                             WHERE item_id=?""", (code, now, now, item_id))
                return "dead"
            c.execute("""UPDATE ingest_queue SET status='queued', last_error_code=?, available_at=?, updated_at=?, leased_until=NULL, worker_id=NULL
                         WHERE item_id=?""", (code, plus_seconds(backoff_seconds(int(r["attempts"]))), now, item_id))
            return "queued"

        return await self.store.run_in_tx(fn)

    async def park(self, item_id: int, worker_id: str, *, seconds: float, code: str = "rate_limited") -> str:
        """Put an item back until ``seconds`` from now without consuming an attempt (rate limits, paused connectors)."""
        def fn(c: sqlite3.Connection) -> str:
            cur = c.execute("""UPDATE ingest_queue SET status='queued', attempts=MAX(0, attempts-1), last_error_code=?, available_at=?, updated_at=?,
                               leased_until=NULL, worker_id=NULL WHERE item_id=? AND status='leased' AND worker_id=?""",
                            (code, plus_seconds(seconds), now_precise(), item_id, worker_id))
            return "queued" if cur.rowcount == 1 else "lost"
        return await self.store.run_in_tx(fn)

    async def requeue_expired(self) -> int:
        def fn(c: sqlite3.Connection) -> int:
            now = now_precise()
            return c.execute("""UPDATE ingest_queue SET status='queued', leased_until=NULL, worker_id=NULL, updated_at=?,
                                last_error_code=COALESCE(last_error_code, 'lease_expired') WHERE status='leased' AND leased_until < ?""",
                             (now, now)).rowcount
        return await self.store.run_in_tx(fn)

    async def retry_dead(self, item_ids: Iterable[int]) -> int:
        ids = [int(i) for i in item_ids]
        if not ids:
            return 0

        def fn(c: sqlite3.Connection) -> int:
            now = now_precise()
            return c.execute(f"""UPDATE ingest_queue SET status='queued', attempts=0, available_at=?, updated_at=?, finished_at=NULL
                                 WHERE status='dead' AND payload IS NOT NULL AND item_id IN ({','.join('?' * len(ids))})""", (now, now, *ids)).rowcount
        return await self.store.run_in_tx(fn)

    async def discard_dead(self, item_ids: Iterable[int]) -> int:
        ids = [int(i) for i in item_ids]
        if not ids:
            return 0

        def fn(c: sqlite3.Connection) -> int:
            return c.execute(f"""UPDATE ingest_queue SET status='discarded', payload=NULL, payload_bytes=0, updated_at=?
                                 WHERE status='dead' AND item_id IN ({','.join('?' * len(ids))})""", (now_precise(), *ids)).rowcount
        return await self.store.run_in_tx(fn)

    # ------------------------------------------------------------------ introspection / backpressure
    def counts_sync(self, c: sqlite3.Connection | None = None) -> dict[str, dict[str, int]]:
        c = c or self.conn
        out: dict[str, dict[str, int]] = {cls: {} for cls in PRIORITY_CLASSES}
        for r in c.execute("SELECT priority_class, status, COUNT(*) AS n FROM ingest_queue GROUP BY priority_class, status").fetchall():
            out.setdefault(r["priority_class"], {})[r["status"]] = int(r["n"])
        return out

    def depth_sync(self, cls: str) -> int:
        r = self.conn.execute("SELECT COUNT(*) AS n FROM ingest_queue WHERE priority_class=? AND status IN ('queued','leased')", (cls,)).fetchone()
        return int(r["n"])

    def fetch_allowed(self, cls: str) -> bool:
        """Hysteresis: stop fetching for a class above its high watermark, resume below its low watermark."""
        if cls == "delete":
            return True
        high, low = self.watermarks.get(cls, (5000, 1000))
        depth = self.depth_sync(cls)
        if self._paused.get(cls):
            if depth <= low:
                self._paused[cls] = False
        elif depth >= high:
            self._paused[cls] = True
        return not self._paused.get(cls)

    def dead_letters_sync(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute("""SELECT item_id, connector_id, kind, priority_class, attempts, last_error_code, updated_at
                                    FROM ingest_queue WHERE status='dead' ORDER BY item_id DESC LIMIT ?""", (int(limit),)).fetchall()
        return [dict(r) for r in rows]
