"""Durable artifact transport (docs/mycelic/DECISIONS.md D4).

Two implementations share this contract:

* :class:`mycelic.transport.sqlite_transport.SqliteTransport` — outbox table in ``coord.db`` with per-consumer
  cursors and bounded retention. Default; used by tests and single-node deployments.
* :class:`mycelic.transport.nats_transport.NatsTransport` — NATS JetStream (stream ``MYCELIC``, subjects
  ``mycelic.>``), durable pull consumers, ``Nats-Msg-Id`` de-duplication, per-user subject permissions.

Semantics every implementation must give:

* **at-least-once** delivery to a named durable consumer; the consumer's ``handler`` is awaited and the message
  is acknowledged only when it returns without raising. A raising handler leaves the message to be
  redelivered (after ``ack_wait``). Handlers are therefore idempotent on ``Envelope.msg_id``.
* ``publish`` is idempotent on ``msg_id`` inside the retention window (a duplicate publish is dropped).
* ``request`` sends an envelope and waits for one reply on a private reply subject (holder raw-evidence
  fetches use it); a missing responder raises ``TransportError`` after ``timeout``.
* Retention is bounded (``retention_seconds``); the transport log is *not* memory — every effect a
  consumer produces is committed to the coordination DB or the holder's store.

Subjects (``Subjects``) are tenant-scoped so NATS permissions can confine a holder to its own inbox.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from ..util import canonical_json, hmac_sign, hmac_verify, new_id, now_iso


class TransportError(RuntimeError):
    pass


@dataclass
class Envelope:
    """What travels over the transport. ``payload`` is the artifact (question, response, ingest request ...).

    ``signature`` is an HMAC over (msg_id, subject, kind, tenant_id, payload) with the *holder's route_key* for
    core → holder messages, so a holder only acts on envelopes the coordinator produced for it.
    """
    msg_id: str
    subject: str
    kind: str                       # question | response | ingest | ingest_result | evidence_event | raw_request | raw_reply | control | event
    tenant_id: str
    payload: dict[str, Any]
    sent_at: str = field(default_factory=now_iso)
    reply_to: str | None = None
    signature: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(cls, subject: str, kind: str, tenant_id: str, payload: dict[str, Any], *, msg_id: str | None = None,
            reply_to: str | None = None, headers: dict[str, str] | None = None) -> "Envelope":
        return cls(msg_id=msg_id or new_id("msg"), subject=subject, kind=kind, tenant_id=tenant_id, payload=payload,
                   reply_to=reply_to, headers=headers or {})

    def signing_input(self) -> str:
        return canonical_json({"msg_id": self.msg_id, "subject": self.subject, "kind": self.kind, "tenant_id": self.tenant_id,
                               "payload": self.payload})

    def sign(self, key: str) -> "Envelope":
        self.signature = hmac_sign(key, self.signing_input())
        return self

    def verify(self, key: str) -> bool:
        return bool(self.signature) and hmac_verify(key, self.signing_input(), self.signature or "")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Envelope":
        return cls(msg_id=d["msg_id"], subject=d["subject"], kind=d["kind"], tenant_id=d["tenant_id"], payload=d.get("payload") or {},
                   sent_at=d.get("sent_at") or now_iso(), reply_to=d.get("reply_to"), signature=d.get("signature"), headers=d.get("headers") or {})


class Subjects:
    """Subject layout. A holder user in NATS gets: subscribe ``holder_inbox(t, h)``, ``holder_ingest(t, h)``,
    ``holder_raw(t, h)`` and publish ``responses(t)``, ``evidence_events(t)``, ``ingest_results(t)``, ``_INBOX.>``."""
    ROOT = "mycelic"

    @staticmethod
    def holder_inbox(tenant_id: str, holder_id: str) -> str:
        return f"mycelic.{tenant_id}.holder.{holder_id}.inbox"

    @staticmethod
    def holder_ingest(tenant_id: str, holder_id: str) -> str:
        return f"mycelic.{tenant_id}.holder.{holder_id}.ingest"

    @staticmethod
    def holder_raw(tenant_id: str, holder_id: str) -> str:
        return f"mycelic.{tenant_id}.holder.{holder_id}.raw"

    @staticmethod
    def holder_control(tenant_id: str, holder_id: str) -> str:
        return f"mycelic.{tenant_id}.holder.{holder_id}.control"

    @staticmethod
    def responses(tenant_id: str) -> str:
        return f"mycelic.{tenant_id}.responses"

    @staticmethod
    def ingest_results(tenant_id: str) -> str:
        return f"mycelic.{tenant_id}.ingest_results"

    @staticmethod
    def evidence_events(tenant_id: str) -> str:
        return f"mycelic.{tenant_id}.evidence_events"

    @staticmethod
    def events(tenant_id: str) -> str:
        return f"mycelic.{tenant_id}.events"

    @staticmethod
    def core_inbound(tenant_id: str) -> str:
        """Wildcard the core subscribes to for everything holders send back."""
        return f"mycelic.{tenant_id}.>"

    @staticmethod
    def all_core_inbound() -> str:
        return "mycelic.>"


Handler = Callable[[Envelope], Awaitable[None]]


@runtime_checkable
class Transport(Protocol):
    name: str

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def publish(self, env: Envelope) -> bool:
        """Durably publish. Returns False when ``msg_id`` was already published (deduplicated)."""
        ...

    async def subscribe(self, subject: str, *, consumer: str, handler: Handler, ack_wait: float = 60.0) -> "Subscription":
        """Durable subscription. ``subject`` may end in ``.>`` (wildcard). Idempotent per ``consumer`` name."""
        ...

    async def request(self, env: Envelope, *, timeout: float = 10.0) -> Envelope:
        """Publish and wait for one reply on a private reply subject."""
        ...

    async def reply(self, request: Envelope, payload: dict[str, Any], *, kind: str = "reply") -> None: ...

    async def stats(self) -> dict[str, Any]: ...

    async def prune(self) -> int:
        """Drop messages past retention. Returns how many."""
        ...


class Subscription:
    """Handle returned by ``subscribe``; ``await sub.close()`` stops delivery."""

    def __init__(self, subject: str, consumer: str, task: asyncio.Task | None = None) -> None:
        self.subject = subject
        self.consumer = consumer
        self._task = task
        self.delivered = 0
        self.failed = 0

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


def build_transport(settings: Any, db: Any | None = None) -> Transport:
    """Factory used by every process: ``MYCELIC_TRANSPORT=sqlite`` (needs the CoordDB) or ``nats``."""
    if getattr(settings, "transport", "sqlite") == "nats":
        from .nats_transport import NatsTransport
        return NatsTransport(url=settings.nats_url, user=settings.nats_user or None, password=settings.nats_password or None,
                             stream=settings.nats_stream, retention_seconds=settings.transport_retention_seconds)
    from .sqlite_transport import SqliteTransport
    if db is None:
        raise TransportError("SqliteTransport needs the coordination DB")
    return SqliteTransport(db, retention_seconds=settings.transport_retention_seconds)
