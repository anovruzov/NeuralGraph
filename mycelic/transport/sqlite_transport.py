"""SQLite implementation of the durable transport (docs/mycelic/DECISIONS.md D4).

The coordination database is the broker. ``publish`` appends to ``transport_messages`` (an outbox with a unique
``msg_id`` and an ``expires_at``); a durable consumer keeps its position in ``transport_cursors`` and records
every message it handled in ``transport_processed``. That gives the guarantees of the JetStream transport on a
single volume with no extra service:

* **at-least-once** — the consumer awaits the handler, then records ``transport_processed`` *and* advances the
  cursor in one transaction. A crash before that commit leaves the cursor behind, so the message is delivered
  again on restart. A handler that raises is retried after ``ack_wait`` and blocks later messages of the same
  consumer (head-of-line, like a JetStream consumer with ``max_ack_pending = 1``): ordering per consumer holds
  and nothing is skipped. Handlers are therefore idempotent on ``Envelope.msg_id``.
* **dedupe** — ``publish`` is ``INSERT ... ON CONFLICT(msg_id) DO NOTHING`` and returns ``False`` for a repeat.
  A consumer skips any ``msg_id`` already in ``transport_processed`` for its name, so a second process sharing
  the consumer name, or a cursor that was rebuilt, cannot double-handle a message.
* **wake-up without polling latency** — every committed ``CoordDB`` transaction sets the consumers'
  ``asyncio.Event`` (``db.add_waker``). The waker only reaches consumers in the same process, so consumers also
  poll every ``poll_interval``; that is what lets another process on the same ``coord.db`` (API, worker,
  holder) see published messages. SQLite in WAL mode serialises the writers and ``busy_timeout`` absorbs
  contention, so a consumer that hits ``SQLITE_BUSY`` simply tries again on its next tick.
* **bounded retention** — rows carry ``expires_at = published_at + retention_seconds``; ``prune`` drops expired
  messages and processed markers older than the window. The transport log is never memory: every effect a
  consumer produces is committed to the coordination DB or the holder's store.

``request`` / ``reply`` reuse the same table: the requester publishes with ``reply_to = "_INBOX.<random>"`` and
waits for one row on that subject; the responder's ``reply`` publishes it. Subjects follow NATS rules
(``.``-separated tokens, ``*`` one token, ``>`` the rest), so a subscription written for NATS works unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import Any

from ..db.coord import CoordDB
from ..util import now_iso, parse_iso, plus_seconds, token
from ..util import j as _j
from .base import Envelope, Handler, Subscription, TransportError

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600


def subject_matches(pattern: str, subject: str) -> bool:
    """NATS subject matching: ``*`` matches exactly one token, a trailing ``>`` matches one or more tokens."""
    if pattern == subject:
        return True
    pt, st = pattern.split("."), subject.split(".")
    for i, tok in enumerate(pt):
        if tok == ">":
            return i < len(st)
        if i >= len(st) or (tok != "*" and tok != st[i]):
            return False
    return len(pt) == len(st)


def _sql_subject_filter(pattern: str) -> tuple[str, list[Any]]:
    """SQL predicate that narrows rows to the pattern's literal prefix so the ``(subject, id)`` index is used.

    An exact subject becomes ``subject = ?``; a wildcard pattern becomes a byte-range on the literal tokens before
    the first wildcard (``mycelic.t1.`` <= subject < ``mycelic.t1/``), which is case-sensitive unlike ``LIKE``.
    Rows inside the range are still checked with :func:`subject_matches`.
    """
    tokens = pattern.split(".")
    if "*" not in tokens and ">" not in tokens:
        return "subject = ?", [pattern]
    literal: list[str] = []
    for tok in tokens:
        if tok in ("*", ">"):
            break
        literal.append(tok)
    if not literal:
        return "1 = 1", []
    prefix = ".".join(literal) + "."
    upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
    return "subject >= ? AND subject < ?", [prefix, upper]


async def _wait_for_wake(wake: asyncio.Event, timeout: float) -> None:
    """Wait for ``wake`` or ``timeout``, whichever comes first, without ``asyncio.wait_for``.

    ``wait_for`` on Python 3.11 can swallow an outer cancellation that lands in the same loop iteration as the
    inner future's completion (gh-86296); a publish that wakes a consumer at the instant ``close`` cancels it
    would then leave the consumer running and ``close`` waiting forever. A timer that sets the event has no
    such race: cancelling the task cancels the ``Event.wait`` itself.
    """
    handle = asyncio.get_running_loop().call_later(timeout, wake.set)
    try:
        await wake.wait()
    finally:
        handle.cancel()


class _SqliteSubscription(Subscription):
    """``Subscription`` with a ``closing`` flag the consumer loop checks, so it stops even if a cancellation is lost."""

    def __init__(self, subject: str, consumer: str) -> None:
        super().__init__(subject, consumer)
        self.closing = False

    async def close(self) -> None:
        self.closing = True
        await super().close()


class SqliteTransport:
    """Durable transport on the coordination DB. See the module docstring for the guarantees."""

    name = "sqlite"

    def __init__(self, db: CoordDB, *, retention_seconds: float = DEFAULT_RETENTION_SECONDS,
                 poll_interval: float = 0.25, batch_size: int = 64) -> None:
        if retention_seconds < 0:
            raise TransportError("retention_seconds must be >= 0")
        self.db = db
        self.retention_seconds = float(retention_seconds)
        self.poll_interval = max(0.001, float(poll_interval))
        self.batch_size = max(1, int(batch_size))
        self._subs: dict[tuple[str, str], Subscription] = {}
        self._closed = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._closed = False

    async def close(self) -> None:
        """Stop every consumer task; the ``CoordDB`` stays open because the caller owns it."""
        self._closed = True
        subs = list(self._subs.values())
        self._subs.clear()
        for sub in subs:
            await sub.close()
        if subs:
            logger.info("sqlite transport closed %d subscription(s)", len(subs))

    # ------------------------------------------------------------------ publish
    async def publish(self, env: Envelope) -> bool:
        if self._closed:
            raise TransportError("transport is closed")
        if not env.subject or not env.msg_id:
            raise TransportError("envelope needs a subject and a msg_id")
        now = now_iso()
        async with self.db.tx() as c:
            cur = c.execute(
                """INSERT INTO transport_messages(subject, msg_id, payload, headers, published_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(msg_id) DO NOTHING""",
                (env.subject, env.msg_id, _j(env.to_dict()), _j(env.headers), now, plus_seconds(self.retention_seconds)),
            )
            inserted = cur.rowcount == 1
        if not inserted:
            logger.info("transport publish deduplicated subject=%s msg_id=%s", env.subject, env.msg_id)
        return inserted

    # ------------------------------------------------------------------ subscribe
    async def subscribe(self, subject: str, *, consumer: str, handler: Handler, ack_wait: float = 60.0) -> Subscription:
        """Start (or return the running) durable consumer ``consumer`` on ``subject``.

        A new consumer name starts at cursor 0 and receives every retained message on the subject, so a holder
        that comes online late still gets what was routed to it. The cursor row is created here so a consumer
        that never handled anything is still visible in ``stats``.
        """
        if self._closed:
            raise TransportError("transport is closed")
        if not consumer or not subject:
            raise TransportError("subscribe needs a subject and a consumer name")
        key = (consumer, subject)
        existing = self._subs.get(key)
        if existing is not None and existing._task is not None and not existing._task.done():
            return existing
        async with self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO transport_cursors(consumer, subject, last_id, updated_at) VALUES (?, ?, 0, ?)",
                      (consumer, subject, now_iso()))
        sub = _SqliteSubscription(subject, consumer)
        sub._task = asyncio.create_task(self._consume(sub, handler, float(ack_wait)), name=f"transport-consumer:{consumer}")
        self._subs[key] = sub
        logger.info("transport subscribed consumer=%s subject=%s cursor=%d", consumer, subject, self.cursor(consumer, subject))
        return sub

    def cursor(self, consumer: str, subject: str) -> int:
        return int(self.db.scalar("SELECT last_id FROM transport_cursors WHERE consumer = ? AND subject = ?", (consumer, subject), default=0))

    def is_processed(self, consumer: str, msg_id: str) -> bool:
        return self.db.one("SELECT 1 FROM transport_processed WHERE consumer = ? AND msg_id = ?", (consumer, msg_id)) is not None

    async def _consume(self, sub: _SqliteSubscription, handler: Handler, ack_wait: float) -> None:
        wake = asyncio.Event()
        self.db.add_waker(wake)
        where, args = _sql_subject_filter(sub.subject)
        task = asyncio.current_task()
        try:
            # ``cancelling()`` stays raised if a cancellation was ever swallowed by a ``wait_for`` in a handler, so a
            # loop cancelled without ``close`` (interpreter shutdown) still ends
            while not sub.closing and not (task is not None and task.cancelling()):
                # cleared *before* reading, so a commit that lands while we work makes the next wait return at once
                wake.clear()
                try:
                    outcome = await self._deliver_batch(sub, handler, where, args)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # SQLITE_BUSY from another process, a closed connection, ...: the consumer must outlive it
                    logger.exception("transport consumer=%s could not process its batch; retrying", sub.consumer)
                    outcome = "idle"
                if sub.closing:
                    break
                if outcome == "failed":
                    await asyncio.sleep(ack_wait)
                elif outcome == "idle":
                    await _wait_for_wake(wake, self.poll_interval)
        finally:
            self.db.remove_waker(wake)
            logger.debug("transport consumer=%s stopped", sub.consumer)

    async def _deliver_batch(self, sub: _SqliteSubscription, handler: Handler, where: str, args: list[Any]) -> str:
        """Deliver up to ``batch_size`` rows past the cursor. Returns ``"more"``, ``"idle"`` or ``"failed"``.

        The cursor is read from the table every batch (never cached) so two processes sharing a consumer name
        see each other's progress. Rows that do not match the pattern or are already processed only move the
        cursor; the move is committed lazily (with the next success, or at the end of the batch) because the
        invariant is only that the cursor never passes an unprocessed matching row.
        """
        start = self.cursor(sub.consumer, sub.subject)
        rows = self.db.all(
            f"SELECT id, subject, msg_id, payload FROM transport_messages WHERE id > ? AND {where} ORDER BY id LIMIT ?",
            (start, *args, self.batch_size),
        )
        if not rows:
            return "idle"
        committed = advanced = start
        for r in rows:
            if sub.closing:
                break
            rid = int(r["id"])
            if not subject_matches(sub.subject, r["subject"]) or self.is_processed(sub.consumer, r["msg_id"]):
                advanced = rid
                continue
            try:
                env = Envelope.from_dict(json.loads(r["payload"]))
            except (TypeError, ValueError, KeyError):
                # not an envelope: it can never be handled, so it is skipped loudly rather than blocking the consumer
                logger.error("transport consumer=%s skipping undecodable row id=%d msg_id=%s", sub.consumer, rid, r["msg_id"])
                advanced = rid
                continue
            try:
                await handler(env)
            except asyncio.CancelledError:
                raise
            except Exception:
                sub.failed += 1
                logger.exception("transport handler failed consumer=%s subject=%s msg_id=%s; redelivery after ack_wait",
                                 sub.consumer, env.subject, env.msg_id)
                if advanced > committed:
                    await self._advance_cursor(sub, advanced)
                return "failed"
            async with self.db.tx() as c:
                c.execute("INSERT OR IGNORE INTO transport_processed(consumer, msg_id, processed_at) VALUES (?, ?, ?)",
                          (sub.consumer, env.msg_id, now_iso()))
                self._advance_cursor_sync(c, sub, rid)
            committed = advanced = rid
            sub.delivered += 1
            logger.debug("transport delivered consumer=%s subject=%s msg_id=%s", sub.consumer, env.subject, env.msg_id)
        if advanced > committed:
            await self._advance_cursor(sub, advanced)
        return "more"

    @staticmethod
    def _advance_cursor_sync(c: sqlite3.Connection, sub: Subscription, last_id: int) -> None:
        """Move the cursor forward only (``MAX``): a lagging process can never pull a shared consumer back."""
        c.execute(
            """INSERT INTO transport_cursors(consumer, subject, last_id, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(consumer, subject) DO UPDATE SET last_id = MAX(last_id, excluded.last_id), updated_at = excluded.updated_at""",
            (sub.consumer, sub.subject, int(last_id), now_iso()),
        )

    async def _advance_cursor(self, sub: Subscription, last_id: int) -> None:
        async with self.db.tx() as c:
            self._advance_cursor_sync(c, sub, last_id)

    # ------------------------------------------------------------------ request / reply
    async def request(self, env: Envelope, *, timeout: float = 10.0) -> Envelope:
        """Publish ``env`` with a private ``reply_to`` and wait for the first row on it.

        A repeated ``request`` with the same ``msg_id`` is deduplicated by ``publish``; the wait then targets the
        ``reply_to`` stored with the original row, so a retried request still receives the responder's reply.
        """
        inbox = f"_INBOX.{token(12)}"
        env.reply_to = inbox
        if not await self.publish(env):
            stored = self.db.one("SELECT payload FROM transport_messages WHERE msg_id = ?", (env.msg_id,))
            prior = json.loads(stored["payload"]).get("reply_to") if stored else None
            if prior:
                inbox = env.reply_to = prior
        wake = asyncio.Event()
        self.db.add_waker(wake)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        try:
            while True:
                wake.clear()
                row = self.db.one("SELECT payload FROM transport_messages WHERE subject = ? ORDER BY id LIMIT 1", (inbox,))
                if row is not None:
                    return Envelope.from_dict(json.loads(row["payload"]))
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TransportError(f"no reply on {env.subject} for msg_id={env.msg_id} within {timeout:g}s")
                await _wait_for_wake(wake, min(remaining, self.poll_interval))
        finally:
            self.db.remove_waker(wake)

    async def reply(self, request: Envelope, payload: dict[str, Any], *, kind: str = "reply") -> None:
        if not request.reply_to:
            raise TransportError(f"envelope msg_id={request.msg_id} has no reply_to")
        env = Envelope.new(request.reply_to, kind, request.tenant_id, payload, headers={"in_reply_to": request.msg_id})
        await self.publish(env)

    # ------------------------------------------------------------------ maintenance
    async def stats(self) -> dict[str, Any]:
        now = now_iso()
        head = int(self.db.scalar("SELECT COALESCE(MAX(id), 0) FROM transport_messages", default=0))
        cursors = [
            {"consumer": r["consumer"], "subject": r["subject"], "last_id": int(r["last_id"]),
             "lag": max(0, head - int(r["last_id"])), "updated_at": r["updated_at"]}
            for r in self.db.all("SELECT consumer, subject, last_id, updated_at FROM transport_cursors ORDER BY consumer, subject")
        ]
        return {
            "transport": self.name,
            "messages": int(self.db.scalar("SELECT COUNT(*) FROM transport_messages", default=0)),
            "expired": int(self.db.scalar("SELECT COUNT(*) FROM transport_messages WHERE expires_at <= ?", (now,), default=0)),
            "head": head,
            "oldest_published_at": self.db.scalar("SELECT MIN(published_at) FROM transport_messages"),
            "newest_published_at": self.db.scalar("SELECT MAX(published_at) FROM transport_messages"),
            "processed": int(self.db.scalar("SELECT COUNT(*) FROM transport_processed", default=0)),
            "retention_seconds": self.retention_seconds,
            "cursors": cursors,
            "subscriptions": [
                {"consumer": s.consumer, "subject": s.subject, "delivered": s.delivered, "failed": s.failed,
                 "running": s._task is not None and not s._task.done()}
                for s in self._subs.values()
            ],
        }

    async def prune(self, *, now: str | None = None) -> int:
        """Drop messages past ``expires_at`` and processed markers older than the retention window.

        A marker only matters while its message can still be delivered; ``processed_at >= published_at``, so
        pruning markers by the same window never removes one before its message. ``now`` exists for tests.
        """
        now_ts = now or now_iso()
        cutoff = plus_seconds(-self.retention_seconds, start=parse_iso(now_ts))
        async with self.db.tx() as c:
            dropped = c.execute("DELETE FROM transport_messages WHERE expires_at <= ?", (now_ts,)).rowcount
            markers = c.execute("DELETE FROM transport_processed WHERE processed_at <= ?", (cutoff,)).rowcount
        if dropped or markers:
            logger.info("transport pruned messages=%d processed_markers=%d", dropped, markers)
        return int(dropped)
