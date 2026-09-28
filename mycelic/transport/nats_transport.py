"""NATS JetStream implementation of the durable transport (docs/mycelic/DECISIONS.md D4).

One stream (``MYCELIC`` by default) captures ``mycelic.>``; every subscription is a *durable pull consumer* with
explicit acks; ``publish`` sets ``Nats-Msg-Id`` so JetStream drops a repeat inside the duplicate window.

Design notes
------------
* The stream holds ``mycelic.>`` only. Reply inboxes (``_INBOX.>``) are deliberately *not* stream subjects: the
  reply path is core NATS, a reply is only useful while the requester is waiting, and a stream on ``_INBOX.>``
  would also swallow every JetStream API response and pull-fetch delivery in the account.
* ``request`` cannot use ``nc.request`` as-is: a JetStream consumer receives the *stored* message, whose
  ``reply`` is the ack subject, not the requester's inbox. So the requester allocates ``nc.new_inbox()``, writes
  it into ``Envelope.reply_to``, publishes through JetStream (durable, deduplicated) and waits on a core
  subscription; ``reply`` is a core publish to ``request.reply_to``.
* ``duplicate_window = min(120, retention_seconds)``: JetStream's default window is two minutes and the server
  refuses a window longer than ``max_age``. Idempotency beyond that window is the consumers' job, which they
  already do (every effect is keyed by ``msg_id`` in the coordination DB or the holder's store).
* Retention is ``limits`` with ``max_age = retention_seconds`` so a message survives until every consumer had
  the window to read it; ``prune`` is therefore a no-op that reports what the server holds.
* A handler that raises gets the message NAK'd with ``delay = ack_wait``, so redelivery timing matches the
  SQLite transport instead of spinning on a poison message. A payload that is not an envelope is terminated
  (``msg.term()``) because it can never be handled.
* Consumer names are durable identities. NATS forbids ``.``, ``*``, ``>``, ``/``, ``\\`` and whitespace in them;
  :func:`consumer_name` maps anything outside ``[A-Za-z0-9_-]`` to ``_``. Names must be stable across restarts
  and unique per logical consumer: two processes with the same name share one consumer (competing consumers,
  which is how workers scale out). ``deploy/mycelic/nats.conf`` confines a holder to the consumer named after
  its ``holder_id``, so a holder subscribes once, with ``consumer=holder_id``, to
  ``Subjects.holder_inbox(...)``'s parent wildcard ``mycelic.<tenant>.holder.<holder_id>.>``.
* The coordinator creates or updates the stream (``manage_stream=True``); a holder process, whose NATS user
  may only ``INFO`` the stream, constructs the transport with ``manage_stream=False`` (or sets the attribute
  before ``start``) and fails fast if the stream is missing.
* nats-py is imported inside the methods so this module imports without the optional dependency.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import Any

from ..util import j as _j
from .base import Envelope, Handler, Subjects, Subscription, TransportError

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600
MAX_DUPLICATE_WINDOW_SECONDS = 120.0     # JetStream's default; the server rejects a window above max_age
NATS_MSG_ID_HEADER = "Nats-Msg-Id"
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")


def consumer_name(name: str) -> str:
    """Durable consumer name NATS accepts: letters, digits, ``-`` and ``_`` only, at most 200 characters."""
    if not name:
        raise TransportError("consumer name is required")
    return _UNSAFE_NAME.sub("_", name)[:200]


def _require_nats() -> Any:
    try:
        import nats
        import nats.errors  # noqa: F401  (makes ``nats.errors`` resolvable on the module object)
        import nats.js.errors  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised only without the optional dependency
        raise TransportError("nats-py is not installed: pip install nats-py (see requirements-mycelic.txt)") from e
    return nats


class _NatsSubscription(Subscription):
    """A ``Subscription`` that also tears down the pull subscription's inbox on ``close``."""

    def __init__(self, subject: str, consumer: str, durable: str, psub: Any) -> None:
        super().__init__(subject, consumer)
        self.durable = durable
        self.closing = False        # checked by the fetch loop: it must stop even if a cancellation is swallowed
        self._psub = psub

    async def close(self) -> None:
        self.closing = True
        await super().close()
        with contextlib.suppress(Exception):
            await self._psub.unsubscribe()


class NatsTransport:
    """JetStream transport. See the module docstring for the guarantees and the naming rules."""

    name = "nats"

    def __init__(self, url: str, user: str | None = None, password: str | None = None, stream: str = "MYCELIC",
                 retention_seconds: float = DEFAULT_RETENTION_SECONDS, *, manage_stream: bool = True,
                 subjects: tuple[str, ...] = (Subjects.all_core_inbound(),), fetch_batch: int = 16,
                 fetch_timeout: float = 2.0, api_timeout: float = 5.0, connect_timeout: float = 5.0,
                 max_ack_pending: int = 256, client_name: str = "mycelic") -> None:
        if retention_seconds <= 0:
            raise TransportError("retention_seconds must be > 0 for JetStream max_age")
        self.url = url
        self.user = user or None
        self.password = password or None
        self.stream = stream
        self.retention_seconds = float(retention_seconds)
        self.duplicate_window = min(MAX_DUPLICATE_WINDOW_SECONDS, self.retention_seconds)
        self.manage_stream = manage_stream
        self.subjects = tuple(subjects)
        self.fetch_batch = max(1, int(fetch_batch))
        self.fetch_timeout = max(0.1, float(fetch_timeout))
        self.api_timeout = float(api_timeout)
        self.connect_timeout = float(connect_timeout)
        self.max_ack_pending = int(max_ack_pending)
        self.client_name = client_name
        self._nc: Any = None
        self._js: Any = None
        self._subs: dict[tuple[str, str], _NatsSubscription] = {}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        nats = _require_nats()
        from nats.js import api
        from nats.js.errors import BadRequestError, NotFoundError

        async def on_error(exc: Exception) -> None:
            logger.warning("nats client error: %s", exc)

        async def on_disconnected() -> None:
            logger.warning("nats disconnected from %s", self.url)

        async def on_reconnected() -> None:
            logger.info("nats reconnected to %s", self.url)

        # Reconnects after a successful connection are unbounded (a long-running process must ride out a server
        # restart), but nats-py applies the same policy to the *first* connection, so a wrong password or a dead
        # address would retry forever. The first connection therefore gets a deadline of two attempts.
        nc = nats.NATS()
        per_attempt = max(1, int(round(self.connect_timeout)))
        try:
            await asyncio.wait_for(
                nc.connect(
                    self.url, user=self.user, password=self.password, name=self.client_name,
                    connect_timeout=per_attempt, max_reconnect_attempts=-1, reconnect_time_wait=2,
                    error_cb=on_error, disconnected_cb=on_disconnected, reconnected_cb=on_reconnected,
                ),
                timeout=2 * per_attempt + 2,
            )
        except (Exception, asyncio.TimeoutError) as e:
            last = getattr(nc, "last_error", None) or e
            with contextlib.suppress(Exception):
                await nc.close()
            raise TransportError(f"cannot connect to NATS at {self.url}: {last}") from e
        self._nc = nc
        self._js = nc.jetstream(timeout=self.api_timeout)
        cfg = api.StreamConfig(
            name=self.stream, description="Mycelic artifact transport", subjects=list(self.subjects),
            retention=api.RetentionPolicy.LIMITS, storage=api.StorageType.FILE, discard=api.DiscardPolicy.OLD,
            max_age=self.retention_seconds, duplicate_window=self.duplicate_window,
        )
        try:
            if self.manage_stream:
                try:
                    info = await self._js.add_stream(cfg)
                except BadRequestError as e:
                    # the stream exists with another configuration (retention changed): converge it
                    logger.info("nats stream %s exists with a different configuration (%s); updating", self.stream, e.description)
                    info = await self._js.update_stream(cfg)
            else:
                try:
                    info = await self._js.stream_info(self.stream)
                except NotFoundError as e:
                    raise TransportError(f"NATS stream {self.stream} does not exist; start the coordinator first") from e
        except TransportError:
            await self._disconnect()
            raise
        except Exception as e:
            await self._disconnect()
            raise TransportError(
                f"cannot prepare NATS stream {self.stream}: {e} (check the user's $JS.API permissions in deploy/mycelic/nats.conf)"
            ) from e
        logger.info("nats transport ready url=%s stream=%s subjects=%s max_age=%ss duplicate_window=%ss messages=%d",
                    self.url, self.stream, list(info.config.subjects or []), info.config.max_age,
                    info.config.duplicate_window, info.state.messages)

    async def _disconnect(self) -> None:
        nc, self._nc, self._js = self._nc, None, None
        if nc is not None and not nc.is_closed:
            with contextlib.suppress(Exception):
                await nc.close()

    async def close(self) -> None:
        subs = list(self._subs.values())
        self._subs.clear()
        for sub in subs:
            await sub.close()
        await self._disconnect()
        if subs:
            logger.info("nats transport closed %d subscription(s)", len(subs))

    def _require_connection(self) -> Any:
        if self._nc is None or self._js is None or self._nc.is_closed:
            raise TransportError("NatsTransport is not started")
        return self._nc

    @staticmethod
    def _encode(env: Envelope) -> bytes:
        return _j(env.to_dict()).encode("utf-8")

    # ------------------------------------------------------------------ publish
    async def publish(self, env: Envelope) -> bool:
        nats = _require_nats()
        from nats.js.errors import NoStreamResponseError
        self._require_connection()
        if not env.subject or not env.msg_id:
            raise TransportError("envelope needs a subject and a msg_id")
        headers = {NATS_MSG_ID_HEADER: env.msg_id}
        try:
            ack = await self._js.publish(env.subject, self._encode(env), headers=headers, stream=self.stream, timeout=self.api_timeout)
        except NoStreamResponseError as e:
            raise TransportError(f"no JetStream stream captures {env.subject} (stream {self.stream} holds {list(self.subjects)})") from e
        except nats.errors.Error as e:
            raise TransportError(f"publish to {env.subject} failed: {e}") from e
        if ack.duplicate:
            logger.info("nats publish deduplicated subject=%s msg_id=%s", env.subject, env.msg_id)
            return False
        return True

    # ------------------------------------------------------------------ subscribe
    async def subscribe(self, subject: str, *, consumer: str, handler: Handler, ack_wait: float = 60.0) -> Subscription:
        """Bind (creating it on first use) the durable pull consumer ``consumer_name(consumer)`` on ``subject``.

        An existing durable keeps the ``filter_subject`` and ``ack_wait`` it was created with; change them by
        deleting the consumer (``nats consumer rm``) rather than by passing new values here.
        """
        nats = _require_nats()
        from nats.js import api
        self._require_connection()
        if not subject:
            raise TransportError("subscribe needs a subject")
        durable = consumer_name(consumer)
        key = (durable, subject)
        existing = self._subs.get(key)
        if existing is not None and existing._task is not None and not existing._task.done():
            return existing
        cfg = api.ConsumerConfig(
            name=durable, durable_name=durable, filter_subject=subject, ack_policy=api.AckPolicy.EXPLICIT,
            deliver_policy=api.DeliverPolicy.ALL, ack_wait=float(ack_wait), max_ack_pending=self.max_ack_pending,
        )
        try:
            psub = await self._js.pull_subscribe(subject, durable=durable, stream=self.stream, config=cfg)
        except nats.errors.Error as e:
            raise TransportError(f"cannot bind consumer {durable} on {subject}: {e} (check $JS.API.CONSUMER permissions)") from e
        sub = _NatsSubscription(subject, consumer, durable, psub)
        sub._task = asyncio.create_task(self._consume(sub, psub, handler, float(ack_wait)), name=f"nats-consumer:{durable}")
        self._subs[key] = sub
        logger.info("nats subscribed consumer=%s durable=%s subject=%s stream=%s", consumer, durable, subject, self.stream)
        return sub

    async def _consume(self, sub: _NatsSubscription, psub: Any, handler: Handler, ack_wait: float) -> None:
        nats = _require_nats()
        retry_delay = 1.0
        task = asyncio.current_task()
        # ``cancelling()`` stays raised when a cancellation was swallowed inside a library ``wait_for`` (gh-86296),
        # so a loop cancelled without ``close`` (interpreter shutdown) still ends
        while not sub.closing and not (task is not None and task.cancelling()):
            try:
                msgs = await psub.fetch(self.fetch_batch, timeout=self.fetch_timeout)
            except (nats.errors.TimeoutError, asyncio.TimeoutError):
                continue                                   # nothing to fetch: ask again
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("nats fetch failed consumer=%s; retrying in %.1fs", sub.durable, retry_delay, exc_info=True)
                await asyncio.sleep(retry_delay)
                continue
            for i, msg in enumerate(msgs):
                if sub.closing:
                    # hand the rest straight back so another instance of this consumer gets them now
                    for rest in msgs[i:]:
                        with contextlib.suppress(Exception):
                            await rest.nak()
                    break
                await self._handle(sub, msg, handler, ack_wait)
        logger.debug("nats consumer=%s stopped", sub.durable)

    async def _handle(self, sub: _NatsSubscription, msg: Any, handler: Handler, ack_wait: float) -> None:
        try:
            env = Envelope.from_dict(json.loads(msg.data))
        except (TypeError, ValueError, KeyError):
            logger.error("nats consumer=%s terminating undecodable message on %s", sub.durable, msg.subject)
            with contextlib.suppress(Exception):
                await msg.term()
            return
        try:
            await handler(env)
        except asyncio.CancelledError:
            raise                                           # unacked: JetStream redelivers after ack_wait
        except Exception:
            sub.failed += 1
            logger.exception("nats handler failed consumer=%s subject=%s msg_id=%s; NAK with delay=%ss",
                             sub.durable, env.subject, env.msg_id, ack_wait)
            with contextlib.suppress(Exception):
                await msg.nak(delay=ack_wait)
            return
        sub.delivered += 1
        try:
            await msg.ack()
        except Exception:
            logger.warning("nats ack failed consumer=%s msg_id=%s; the message will be redelivered", sub.durable, env.msg_id, exc_info=True)

    # ------------------------------------------------------------------ request / reply
    async def request(self, env: Envelope, *, timeout: float = 10.0) -> Envelope:
        nats = _require_nats()
        nc = self._require_connection()
        inbox = nc.new_inbox()
        env.reply_to = inbox
        sub = await nc.subscribe(inbox, max_msgs=1)
        try:
            await self.publish(env)
            try:
                msg = await sub.next_msg(timeout=timeout)
            except (nats.errors.TimeoutError, asyncio.TimeoutError) as e:
                raise TransportError(f"no reply on {env.subject} for msg_id={env.msg_id} within {timeout:g}s") from e
            try:
                return Envelope.from_dict(json.loads(msg.data))
            except (TypeError, ValueError, KeyError) as e:
                raise TransportError(f"reply on {inbox} is not an envelope") from e
        finally:
            with contextlib.suppress(Exception):
                await sub.unsubscribe()

    async def reply(self, request: Envelope, payload: dict[str, Any], *, kind: str = "reply") -> None:
        nats = _require_nats()
        nc = self._require_connection()
        if not request.reply_to:
            raise TransportError(f"envelope msg_id={request.msg_id} has no reply_to")
        env = Envelope.new(request.reply_to, kind, request.tenant_id, payload, headers={"in_reply_to": request.msg_id})
        try:
            await nc.publish(request.reply_to, self._encode(env))
        except nats.errors.Error as e:
            raise TransportError(f"reply to {request.reply_to} failed: {e}") from e

    # ------------------------------------------------------------------ maintenance
    async def stats(self) -> dict[str, Any]:
        nc = self._require_connection()
        info = await self._js.stream_info(self.stream)
        st = info.state
        return {
            "transport": self.name, "url": self.url, "connected": bool(nc.is_connected), "stream": self.stream,
            "subjects": list(info.config.subjects or []), "max_age_seconds": info.config.max_age,
            "duplicate_window_seconds": info.config.duplicate_window,
            "messages": st.messages, "bytes": st.bytes, "first_seq": st.first_seq, "last_seq": st.last_seq,
            "consumers": st.consumer_count,
            "subscriptions": [
                {"consumer": s.consumer, "durable": s.durable, "subject": s.subject, "delivered": s.delivered,
                 "failed": s.failed, "running": s._task is not None and not s._task.done()}
                for s in self._subs.values()
            ],
        }

    async def prune(self) -> int:
        """JetStream enforces ``max_age`` itself; nothing to do client-side."""
        return 0

    async def delete_stream(self) -> bool:
        """Remove the stream and every consumer on it. For tests and operators only: it drops retained messages."""
        self._require_connection()
        try:
            return bool(await self._js.delete_stream(self.stream))
        except Exception as e:
            raise TransportError(f"cannot delete stream {self.stream}: {e}") from e
