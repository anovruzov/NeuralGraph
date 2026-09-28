"""Live updates: the transactional outbox tailed into Server-Sent Events (docs/mycelic/DECISIONS.md D6).

``EventHub`` reads ``events`` rows after the last id it delivered, every ``poll_seconds`` and immediately after any
commit on the coordination DB (``db.add_waker``), and pushes each row into the queue of every subscriber whose
principal is in the event's audience. The audience filter is the only place a row can leak, so it is deliberately
narrow: the tenant must match, then one of (a) an administrator when the audience names ``org_admin``, (b) the
principal's user id in ``user_ids``, (c) one of its roles in ``roles``, (d) ``visibility == "org"``, (e) a unit it
may see (memberships, led closure, projects) in ``unit_ids``, or (f) an untargeted, non-private audience, which is
tenant-wide. Nothing ever crosses tenants, whatever the audience says.

The stream handler writes ``id:`` / ``event:`` / ``data:`` frames, a ``: ping`` comment every ``ping_seconds`` so
proxies keep the connection open, honors ``?since=<id>`` by backfilling from the outbox (filtered the same way), and
re-resolves the principal periodically so a revoked session or a lost membership stops the stream instead of
outliving the permission.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Awaitable, Callable

from aiohttp import web

from ..authz import Principal
from ..util import j

logger = logging.getLogger(__name__)

PrincipalRefresher = Callable[[], Awaitable[Principal | None]]


class Subscriber:
    def __init__(self, principal: Principal, *, refresh: PrincipalRefresher | None = None, queue_size: int = 1000) -> None:
        self.principal = principal
        self.refresh = refresh
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self.overflowed = False
        self.delivered = 0
        self.created_at = time.monotonic()

    def push(self, ev: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(ev)
            self.delivered += 1
        except asyncio.QueueFull:
            # A client that cannot keep up is closed and reconnects with ``since``; dropping silently would lose events.
            self.overflowed = True


class EventHub:
    def __init__(self, db: Any, authz: Any, *, poll_seconds: float = 0.5, ping_seconds: float = 15.0, batch: int = 500,
                 queue_size: int = 1000) -> None:
        self.db = db
        self.authz = authz
        self.poll_seconds = float(poll_seconds)
        self.ping_seconds = float(ping_seconds)
        self.batch = int(batch)
        self.queue_size = int(queue_size)
        self._subs: set[Subscriber] = set()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_id = 0
        self.running = False
        self.events_seen = 0

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.running:
            return
        self._last_id = int(self.db.last_event_id())
        self.db.add_waker(self._wake)
        self.running = True
        self._task = asyncio.create_task(self._run(), name="mycelic-sse-hub")

    async def stop(self) -> None:
        self.running = False
        self.db.remove_waker(self._wake)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        for sub in list(self._subs):
            sub.push({"__close__": True})
        self._subs.clear()

    @property
    def last_id(self) -> int:
        return self._last_id

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    # ------------------------------------------------------------------ subscriptions
    def subscribe(self, principal: Principal, *, since: int | None = None, refresh: PrincipalRefresher | None = None) -> Subscriber:
        """Register a viewer. With ``since`` the outbox rows after that id (up to the hub's head) are queued first,
        filtered exactly like live rows; live rows after the head follow from the tailer, so nothing is missed or
        duplicated between the two."""
        sub = Subscriber(principal, refresh=refresh, queue_size=self.queue_size)
        if since is not None and since < self._last_id:
            for ev in self._fetch(since, self._last_id):
                if self.authorized(principal, ev):
                    sub.push(self._frame(ev))
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def _fetch(self, after: int, upto: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = after
        while cursor < upto:
            rows = self.db.events_after(cursor, limit=self.batch)
            if not rows:
                break
            for r in rows:
                if int(r["id"]) > upto:
                    return out
                out.append(r)
            cursor = int(rows[-1]["id"])
            if len(rows) < self.batch:
                break
        return out

    # ------------------------------------------------------------------ tailer
    async def _run(self) -> None:
        while self.running:
            try:
                self._drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("event hub tail failed; retrying")
                await asyncio.sleep(1.0)
                continue
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)

    def _drain(self) -> None:
        while True:
            rows = self.db.events_after(self._last_id, limit=self.batch)
            if not rows:
                return
            for ev in rows:
                self._last_id = int(ev["id"])
                self.events_seen += 1
                frame = self._frame(ev)
                for sub in list(self._subs):
                    if self.authorized(sub.principal, ev):
                        sub.push(frame)
            if len(rows) < self.batch:
                return

    @staticmethod
    def _frame(ev: dict[str, Any]) -> dict[str, Any]:
        return {"id": int(ev["id"]), "tenant_id": ev.get("tenant_id"), "kind": ev.get("kind"), "ref_type": ev.get("ref_type"),
                "ref_id": ev.get("ref_id"), "payload": ev.get("payload") or {}, "at": ev.get("at")}

    # ------------------------------------------------------------------ audience filter
    def authorized(self, p: Principal, ev: dict[str, Any]) -> bool:
        if not p.is_user or ev.get("tenant_id") != p.tenant_id:
            return False
        aud = ev.get("audience")
        if not isinstance(aud, dict):
            aud = {}
        roles = {r for r in (aud.get("roles") or []) if r}
        users = {u for u in (aud.get("user_ids") or []) if u}
        units = {u for u in (aud.get("unit_ids") or []) if u}
        vis = aud.get("visibility")
        if p.is_admin and "org_admin" in roles:
            return True
        if p.id in users:
            return True
        if roles & p.roles:
            return True
        if vis == "org":
            return True
        if units:
            try:
                mine = self.authz.visible_unit_ids(p) | self.authz.led_unit_ids(p)
            except Exception:
                logger.exception("scope lookup failed for %s", p.id)
                return False
            if units & mine:
                return True
        if not users and not units and not roles and vis != "private":
            return True
        return False


# ---------------------------------------------------------------------------------------------
# The HTTP handler
# ---------------------------------------------------------------------------------------------


def sse_frame(ev: dict[str, Any]) -> bytes:
    data = j(ev)
    return f"id: {ev['id']}\nevent: {ev['kind']}\ndata: {data}\n\n".encode("utf-8")


async def stream(request: web.Request, hub: EventHub, principal: Principal, *, since: int | None, refresh: PrincipalRefresher | None,
                 refresh_seconds: float = 60.0) -> web.StreamResponse:
    resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform",
                                                   "X-Accel-Buffering": "no", "Connection": "keep-alive",
                                                   "X-Request-Id": request.get("request_id", "")})
    await resp.prepare(request)
    sub = hub.subscribe(principal, since=since, refresh=refresh)
    last_refresh = time.monotonic()
    try:
        await resp.write(f": connected head={hub.last_id}\n\n".encode("utf-8"))
        while True:
            if sub.overflowed:
                await resp.write(b": overflow; reconnect with since\n\n")
                break
            if refresh is not None and time.monotonic() - last_refresh >= refresh_seconds:
                last_refresh = time.monotonic()
                fresh = await refresh()
                if fresh is None:
                    await resp.write(b": session ended\n\n")
                    break
                sub.principal = fresh
            try:
                ev = await asyncio.wait_for(sub.queue.get(), timeout=hub.ping_seconds)
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            if ev.get("__close__"):
                break
            await resp.write(sse_frame(ev))
            # a membership or grant change for this very user changes what they may see: re-resolve at once
            if refresh is not None and ev.get("kind") in ("membership.changed", "grant.changed") and ev.get("ref_id") == principal.id:
                fresh = await refresh()
                if fresh is None:
                    break
                sub.principal = fresh
                last_refresh = time.monotonic()
    except (asyncio.CancelledError, ConnectionResetError, ConnectionError):
        pass
    finally:
        hub.unsubscribe(sub)
    with contextlib.suppress(Exception):
        await resp.write_eof()
    return resp


__all__ = ["EventHub", "Subscriber", "stream", "sse_frame"]
