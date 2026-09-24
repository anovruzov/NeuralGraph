"""Message transport: NATS JetStream in production, an in-process log for unit tests.

Both implement the same small surface the service needs::

    await t.connect()                       # idempotent; JetStream: ensure stream + durable consumer
    seq = await t.publish(subject, bytes, msg_id)     # at-least-once; msg_id is the dedup key
    for d in await t.fetch(batch, timeout): ... await d.ack() / d.nak(delay) / d.term()
    await t.reset_consumer()                # replay: deliver everything from the first sequence again
    await t.info()                          # connected?, stream/consumer positions for /health

JetStream specifics that matter for correctness:

* the stream is file-backed (``storage=FILE``) with no age limit by default, because a rebuild after losing the
  Mycelic database replays it from sequence 1;
* every publish carries ``Nats-Msg-Id`` = the event id, so a retry after a lost ack is deduplicated inside the
  stream's duplicate window; beyond the window the consumer's idempotent apply covers it;
* the consumer is a *durable pull* consumer with explicit acks: a message is acked only after the apply
  transaction committed, so a crash in between redelivers it and the second apply is a no-op;
* a message that keeps failing is ``term``-ed after ``max_deliver`` attempts and recorded as failed, so one
  poison event cannot stall the log.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from .config import Settings

logger = logging.getLogger(__name__)

SUBJECT_PREFIX = "mycelic"


def subject_for(org_id: str, kind: str) -> str:
    """``mycelic.<org>.<kind-with-dashes>`` (a NATS subject token cannot contain dots)."""
    return f"{SUBJECT_PREFIX}.{org_id}.{kind.replace('.', '-')}"


class Delivery(Protocol):
    data: bytes
    subject: str
    msg_id: str | None
    seq: int | None
    num_delivered: int
    headers: dict[str, str]

    async def ack(self) -> None: ...
    async def nak(self, delay: float | None = None) -> None: ...
    async def term(self) -> None: ...


class Transport(Protocol):
    name: str

    @property
    def connected(self) -> bool: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def publish(self, subject: str, payload: bytes, msg_id: str, headers: dict[str, str] | None = None) -> int | None: ...
    async def fetch(self, batch: int, timeout: float) -> list[Delivery]: ...
    async def reset_consumer(self, start_seq: int | None = None) -> None: ...
    async def info(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------------------------
# In-process transport (tests / single-process development without a broker)
# ---------------------------------------------------------------------------------------------


@dataclass
class _MemDelivery:
    data: bytes
    subject: str
    msg_id: str | None
    seq: int | None
    num_delivered: int
    _log: "InProcessTransport"
    headers: dict[str, str] = field(default_factory=dict)
    acked: bool = False

    async def ack(self) -> None:
        self.acked = True
        self._log._inflight.pop(self.seq, None)

    async def nak(self, delay: float | None = None) -> None:
        self._log._inflight.pop(self.seq, None)
        self._log._redeliver.append(self.seq)

    async def term(self) -> None:
        self._log._inflight.pop(self.seq, None)
        self._log.terminated.append(self.seq)


@dataclass
class InProcessTransport:
    """An append-only log with one cursor. Survives nothing; exists so the whole service loop runs in tests."""

    name: str = "in-process"
    max_deliver: int = 8
    _log: list[tuple[str, bytes, str | None, dict[str, str]]] = field(default_factory=list)
    _seen_ids: dict[str, int] = field(default_factory=dict)
    _cursor: int = 0
    _inflight: dict[int, int] = field(default_factory=dict)
    _redeliver: list[int] = field(default_factory=list)
    _delivered: dict[int, int] = field(default_factory=dict)
    terminated: list[int] = field(default_factory=list)
    _connected: bool = False
    _wake: asyncio.Event | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        self._wake = asyncio.Event()

    async def close(self) -> None:
        self._connected = False

    async def publish(self, subject: str, payload: bytes, msg_id: str, headers: dict[str, str] | None = None) -> int | None:
        if not self._connected:
            raise ConnectionError("in-process transport is closed")
        if msg_id in self._seen_ids:
            return self._seen_ids[msg_id]
        self._log.append((subject, payload, msg_id, dict(headers or {})))
        seq = len(self._log)
        self._seen_ids[msg_id] = seq
        if self._wake:
            self._wake.set()
        return seq

    async def fetch(self, batch: int, timeout: float) -> list[Delivery]:
        if not self._connected:
            raise ConnectionError("in-process transport is closed")
        out: list[Delivery] = []
        while self._redeliver and len(out) < batch:
            seq = self._redeliver.pop(0)
            out.append(self._deliver(seq))
        if not out and self._cursor >= len(self._log) and self._wake is not None:
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except asyncio.TimeoutError:
                return out
        while self._cursor < len(self._log) and len(out) < batch:
            self._cursor += 1
            out.append(self._deliver(self._cursor))
        return out

    def _deliver(self, seq: int) -> _MemDelivery:
        subject, payload, msg_id, headers = self._log[seq - 1]
        n = self._delivered.get(seq, 0) + 1
        self._delivered[seq] = n
        self._inflight[seq] = n
        return _MemDelivery(data=payload, subject=subject, msg_id=msg_id, seq=seq, num_delivered=n, _log=self, headers=dict(headers))

    async def reset_consumer(self, start_seq: int | None = None) -> None:
        self._cursor = max(0, int(start_seq) - 1) if start_seq else 0
        self._redeliver.clear()
        self._inflight.clear()
        self._delivered.clear()

    async def info(self) -> dict[str, Any]:
        return {"transport": self.name, "connected": self._connected, "stream_messages": len(self._log),
                "consumer_pending": max(0, len(self._log) - self._cursor), "last_seq": len(self._log)}


# ---------------------------------------------------------------------------------------------
# NATS JetStream
# ---------------------------------------------------------------------------------------------


class _JsDelivery:
    def __init__(self, msg: Any) -> None:
        self._msg = msg
        self.data: bytes = msg.data
        self.subject: str = msg.subject
        self.headers: dict[str, str] = dict(msg.headers or {})
        self.msg_id: str | None = self.headers.get("Nats-Msg-Id")
        meta = msg.metadata
        self.seq: int | None = int(meta.sequence.stream) if meta and meta.sequence else None
        self.num_delivered: int = int(meta.num_delivered or 1) if meta else 1

    async def ack(self) -> None:
        await self._msg.ack()

    async def nak(self, delay: float | None = None) -> None:
        await self._msg.nak(delay=delay)

    async def term(self) -> None:
        await self._msg.term()


class JetStreamTransport:
    name = "nats-jetstream"

    def __init__(self, settings: Settings, *, on_state_change: Callable[[bool], None] | None = None) -> None:
        self.s = settings
        self._nc: Any = None
        self._js: Any = None
        self._sub: Any = None
        self._on_state = on_state_change
        self._connected = False
        self.reconnects = 0
        self.last_error: str | None = None
        self.filter_subject = f"{SUBJECT_PREFIX}.>"
        self.stream_bounded = False

    @property
    def connected(self) -> bool:
        return self._connected and self._nc is not None and self._nc.is_connected

    def _set_connected(self, value: bool) -> None:
        self._connected = value
        if self._on_state:
            try:
                self._on_state(value)
            except Exception:  # pragma: no cover - metrics must never break the transport
                logger.exception("transport state callback failed")

    async def connect(self) -> None:
        import nats

        if self._nc is not None and self._nc.is_connected:
            return
        options: dict[str, Any] = {
            "servers": [self.s.nats_url],
            "name": self.s.instance_id,
            "max_reconnect_attempts": -1,
            "reconnect_time_wait": 1,
            "connect_timeout": 5,
            "allow_reconnect": True,
            "error_cb": self._error_cb,
            "disconnected_cb": self._disconnected_cb,
            "reconnected_cb": self._reconnected_cb,
            "closed_cb": self._closed_cb,
        }
        if self.s.nats_user and self.s.nats_password:
            options["user"] = self.s.nats_user
            options["password"] = self.s.nats_password
        if self.s.nats_token:
            options["token"] = self.s.nats_token
        if self.s.nats_ca_file or (self.s.nats_url or "").startswith("tls://"):
            ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=self.s.nats_ca_file)
            options["tls"] = ctx
        self._nc = await nats.connect(**options)
        self._js = self._nc.jetstream()
        await self._ensure_stream()
        await self._ensure_consumer(reset=False)
        self._set_connected(True)
        logger.info("connected to NATS %s, stream %s, consumer %s", self.s.nats_url, self.s.nats_stream, self.s.nats_consumer)

    async def _error_cb(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("NATS error: %s", self.last_error)

    async def _disconnected_cb(self) -> None:
        logger.warning("NATS disconnected")
        self._set_connected(False)

    async def _reconnected_cb(self) -> None:
        self.reconnects += 1
        logger.info("NATS reconnected (%d)", self.reconnects)
        self._set_connected(True)

    async def _closed_cb(self) -> None:
        logger.warning("NATS connection closed")
        self._set_connected(False)

    async def _ensure_stream(self) -> None:
        from nats.js.api import RetentionPolicy, StorageType, StreamConfig
        from nats.js.errors import BadRequestError, NotFoundError

        from nats.js.api import DiscardPolicy

        config = StreamConfig(
            name=self.s.nats_stream,
            subjects=[self.filter_subject],
            storage=StorageType.FILE,
            retention=RetentionPolicy.LIMITS,
            discard=DiscardPolicy.NEW,          # when a limit is hit, refuse new events loudly; never drop history
            max_age=float(self.s.nats_max_age_seconds) if self.s.nats_max_age_seconds > 0 else 0,
            max_bytes=self.s.nats_max_bytes,
            max_msgs=-1,
            num_replicas=1,
            duplicate_window=float(self.s.nats_duplicate_window_seconds),
            description="Mycelic event log: agent observations and derived organizational memories",
        )
        try:
            info = await self._js.stream_info(self.s.nats_stream)
            existing = info.config
            if (existing.max_age or 0) > 0 or (existing.max_bytes or -1) > 0 or (existing.max_msgs or -1) > 0:
                logger.error("stream %s is bounded (max_age=%s max_bytes=%s max_msgs=%s): a rebuild from replay may be partial",
                             self.s.nats_stream, existing.max_age, existing.max_bytes, existing.max_msgs)
                self.stream_bounded = True
            await self._js.update_stream(config)
        except NotFoundError:
            await self._js.add_stream(config)
        except BadRequestError as exc:
            logger.warning("stream update rejected (%s); keeping the existing configuration", exc)

    async def _ensure_consumer(self, *, reset: bool, start_seq: int | None = None) -> None:
        """Bind the durable pull consumer; ``reset`` recreates it from the first sequence (or ``start_seq``).

        ``max_ack_pending`` equals the fetch batch, so with the default batch of 1 the stream is applied strictly in
        order: a failed message is redelivered in place (the consumer sleeps, then naks) and nothing overtakes it.
        """
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
        from nats.js.errors import NotFoundError

        if reset:
            try:
                await self._js.delete_consumer(self.s.nats_stream, self.s.nats_consumer)
                logger.info("deleted durable consumer %s for replay", self.s.nats_consumer)
            except NotFoundError:
                pass
        if self._sub is not None:
            try:
                await self._sub.unsubscribe()
            except Exception:
                pass
            self._sub = None
        config = ConsumerConfig(
            durable_name=self.s.nats_consumer,
            deliver_policy=DeliverPolicy.BY_START_SEQUENCE if start_seq else DeliverPolicy.ALL,
            opt_start_seq=int(start_seq) if start_seq else None,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=float(self.s.nats_ack_wait_seconds),
            max_deliver=int(self.s.nats_max_deliver),
            filter_subject=self.filter_subject,
            max_ack_pending=max(1, int(self.s.consume_batch)),
        )
        self._sub = await self._js.pull_subscribe(self.filter_subject, durable=self.s.nats_consumer,
                                                  stream=self.s.nats_stream, config=config)

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                try:
                    await self._nc.close()
                except Exception:
                    pass
        self._nc = None
        self._js = None
        self._sub = None
        self._set_connected(False)

    async def publish(self, subject: str, payload: bytes, msg_id: str, headers: dict[str, str] | None = None) -> int | None:
        if self._js is None:
            raise ConnectionError("transport not connected")
        ack = await self._js.publish(subject, payload, timeout=5.0, headers={**(headers or {}), "Nats-Msg-Id": msg_id})
        return int(ack.seq) if ack and ack.seq is not None else None

    async def fetch(self, batch: int, timeout: float) -> list[Delivery]:
        import nats.errors

        if self._sub is None:
            raise ConnectionError("transport not connected")
        try:
            msgs = await self._sub.fetch(batch, timeout=timeout)
        except nats.errors.TimeoutError:
            return []
        return [_JsDelivery(m) for m in msgs]

    async def reset_consumer(self, start_seq: int | None = None) -> None:
        await self._ensure_consumer(reset=True, start_seq=start_seq)

    async def consumer_ack_floor(self) -> int | None:
        """Stream sequence up to which the durable consumer has acknowledged everything (None when unknown)."""
        if self._js is None:
            return None
        try:
            ci = await self._js.consumer_info(self.s.nats_stream, self.s.nats_consumer)
            return int(ci.ack_floor.stream_seq) if ci.ack_floor else 0
        except Exception:
            return None

    async def info(self) -> dict[str, Any]:
        out: dict[str, Any] = {"transport": self.name, "connected": self.connected, "url": self.s.nats_url,
                               "stream": self.s.nats_stream, "consumer": self.s.nats_consumer,
                               "reconnects": self.reconnects, "last_error": self.last_error,
                               "stream_bounded": self.stream_bounded}
        if self._js is None or not self.connected:
            return out
        try:
            si = await self._js.stream_info(self.s.nats_stream)
            out["stream_messages"] = int(si.state.messages)
            out["last_seq"] = int(si.state.last_seq)
            out["first_seq"] = int(si.state.first_seq)
            out["stream_bytes"] = int(si.state.bytes)
            ci = await self._js.consumer_info(self.s.nats_stream, self.s.nats_consumer)
            out["consumer_pending"] = int(ci.num_pending)
            out["consumer_ack_pending"] = int(ci.num_ack_pending)
            out["consumer_delivered_seq"] = int(ci.delivered.stream_seq) if ci.delivered else 0
            out["consumer_ack_floor_seq"] = int(ci.ack_floor.stream_seq) if ci.ack_floor else 0
        except Exception as exc:  # broker mid-restart: report, do not raise
            out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    async def stream_message_count(self) -> int | None:
        if self._js is None:
            return None
        try:
            si = await self._js.stream_info(self.s.nats_stream)
            return int(si.state.messages)
        except Exception:
            return None


def build_transport(settings: Settings, *, on_state_change: Callable[[bool], None] | None = None) -> Transport:
    if settings.nats_enabled:
        return JetStreamTransport(settings, on_state_change=on_state_change)
    logger.warning("MYCELIC_NATS_URL is empty: using the in-process transport (no durability across restarts)")
    return InProcessTransport()
