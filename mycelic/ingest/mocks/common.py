"""Shared machinery of the offline provider mocks: a deterministic clock, an aiohttp server on ``127.0.0.1:<random port>``
with a request log (per-token concurrency high-water mark), fault injection, fixture loading and time rebasing, and the
``http_factory`` that lets an :class:`~mycelic.ingest.pipeline.IngestPipeline` reach a mock.

Mocks are test and demo infrastructure: they hold fictional fixture data, dummy credentials and dummy signing secrets,
and listen on loopback only. Nothing in production imports them.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from aiohttp import web

from ..contract import ConnectorManifest, NoHttp, SecretAccessor
from ..http import ConnectorHttpClient

FIXTURES = Path(__file__).parent / "fixtures"
_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_SLACK_TS = re.compile(r"^\d{9,11}\.\d{6}$")
_PERMALINK = re.compile(r"(/archives/[A-Z0-9]+/p)(\d{16})")


# ---------------------------------------------------------------------------------------------- time
class FakeClock:
    """Deterministic time shared by a mock, the HTTP client (``clock=``, ``sleep=``) and the pipeline (``clock=clock.datetime``).
    ``await clock.sleep(s)`` advances time instead of waiting, and records the wait."""

    def __init__(self, start: float | str | datetime) -> None:
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if isinstance(start, datetime):
            start = (start if start.tzinfo else start.replace(tzinfo=timezone.utc)).timestamp()
        self.now = float(start)
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.now, tz=timezone.utc)

    def advance(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))

    async def sleep(self, seconds: float) -> None:
        self.slept.append(float(seconds))
        self.advance(seconds)
        await asyncio.sleep(0)


def iso_z(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------------------------------------- fixtures
def load_fixture(name: str, *, now: datetime | float | None = None) -> dict[str, Any]:
    """A fixture from ``mocks/fixtures`` (``github_acme``, ``slack_acme``, ``gmail_acme``, ``drive_acme``). With ``now``, every
    timestamp is shifted so the fixture's ``now`` becomes ``now`` (ISO times, Slack ``ts`` strings, ``created`` epochs and
    Slack permalinks), so a demo against the real clock sees the same relative history the tests see."""
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    if now is None:
        return data
    target = now.timestamp() if isinstance(now, datetime) else float(now)
    return rebase_fixture(data, target - parse_iso(data["now"]))


def rebase_fixture(data: Any, delta: float) -> Any:
    """Shift every timestamp of a fixture by ``delta`` seconds (pure; returns a new structure)."""
    def shift_ts(s: str) -> str:
        sec, frac = s.split(".")
        return f"{int(sec) + int(round(delta))}.{frac}"

    def walk(v: Any, key: str = "") -> Any:
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        if isinstance(v, str):
            if _ISO_Z.match(v):
                return iso_z(parse_iso(v) + delta)
            if _SLACK_TS.match(v):
                return shift_ts(v)
            return _PERMALINK.sub(lambda m: m.group(1) + shift_ts(m.group(2)[:10] + "." + m.group(2)[10:]).replace(".", ""), v)
        if key == "created" and isinstance(v, int):
            return v + int(round(delta))
        return v
    return walk(copy.deepcopy(data))


# ---------------------------------------------------------------------------------------------- the server base
@dataclass
class RequestRecord:
    method: str
    path: str
    query: dict[str, str]
    token: str                       # short hash of the bearer token ('' when anonymous); never the token itself
    if_none_match: bool
    concurrent: int                  # requests of the same token in flight when this one started (itself included)
    at: float
    status: int = 0
    form: dict[str, str] = field(default_factory=dict)


def token_key(token: str | None) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12] if token else ""


class MockProvider:
    """An aiohttp application on loopback with a request log and fault injection. Subclasses add routes in ``_routes``
    and decide auth in ``_bearer``."""

    name = "mock"

    def __init__(self, *, clock: Callable[[], float] | None = None, latency: float = 0.0) -> None:
        import time
        self.clock: Callable[[], float] = clock or time.time
        self.latency = float(latency)
        self.requests: list[RequestRecord] = []
        self._inflight: dict[str, int] = defaultdict(int)
        self._fail: list[int] = []
        self._crash_after: int | None = None
        self._crashed = False
        self._runner: web.AppRunner | None = None
        self.url = ""
        self.app = web.Application(middlewares=[self._middleware])
        self._routes(self.app)

    # ------------------------------------------------------------------ lifecycle
    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        sock = site._server.sockets[0]            # type: ignore[union-attr]
        self.url = f"http://{host}:{sock.getsockname()[1]}"
        return self.url

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def __aenter__(self) -> "MockProvider":
        if not self.url:
            await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------ faults and assertions
    def fail_next(self, n: int = 1, status: int = 502) -> None:
        """The next ``n`` requests (any path) answer ``status`` (a provider-side failure)."""
        self._fail.extend([int(status)] * int(n))

    def crash_after_pages(self, n: int) -> None:
        """After ``n`` more successful responses, every request has its connection dropped (no HTTP response) until
        :meth:`recover`: a network or provider crash in the middle of a paged walk."""
        self._crash_after = int(n) if n > 0 else None
        self._crashed = n <= 0

    def recover(self) -> None:
        self._crash_after = None
        self._crashed = False

    def clear_faults(self) -> None:
        """Drop pending injected failures and end a crash."""
        self._fail.clear()
        self.recover()

    def max_concurrency(self, token: str | None = None) -> int:
        key = token_key(token) if token else None
        return max([r.concurrent for r in self.requests if key is None or r.token == key] or [0])

    def paths(self) -> list[str]:
        return [r.path for r in self.requests]

    # ------------------------------------------------------------------ hooks for subclasses
    def _routes(self, app: web.Application) -> None:
        raise NotImplementedError

    def _bearer(self, request: web.Request) -> str | None:
        auth = request.headers.get("Authorization", "")
        for prefix in ("Bearer ", "bearer ", "token "):
            if auth.startswith(prefix):
                return auth[len(prefix):].strip() or None
        return None

    @web.middleware
    async def _middleware(self, request: web.Request, handler: Callable[[web.Request], Any]) -> web.StreamResponse:
        token = self._bearer(request)
        key = token_key(token)
        self._inflight[key] += 1
        form: dict[str, str] = {}
        if request.method == "POST" and request.content_type == "application/x-www-form-urlencoded":
            form = {k: str(v) for k, v in (await request.post()).items() if k not in ("client_secret", "code_verifier", "refresh_token", "token")}
        rec = RequestRecord(method=request.method, path=request.path, query=dict(request.query), token=key,
                            if_none_match=bool(request.headers.get("If-None-Match")), concurrent=self._inflight[key], at=self.clock(), form=form)
        self.requests.append(rec)
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
            if self._crashed:
                if request.transport is not None:
                    request.transport.abort()
                raise ConnectionResetError("mock crash")
            if self._fail:
                rec.status = self._fail.pop(0)
                return web.json_response({"message": "Server Error"}, status=rec.status)
            resp = await handler(request)
            rec.status = resp.status
            if self._crash_after is not None and 200 <= resp.status < 300:
                self._crash_after -= 1
                if self._crash_after <= 0:
                    self._crashed = True
            return resp
        finally:
            self._inflight[key] -= 1


def loopback_http_factory(clock: FakeClock | None = None, **kw: Any) -> Callable[[Mapping[str, Any], ConnectorManifest, SecretAccessor], Any]:
    """An ``IngestPipeline(http_factory=...)`` whose clients may reach ``http://127.0.0.1:<port>`` mocks (and sleep on the
    fake clock when one is given). Test and demo use only: production pipelines keep the default factory."""
    def factory(con: Mapping[str, Any], manifest: ConnectorManifest, secrets: SecretAccessor) -> Any:
        if not manifest.allowed_hosts:
            return NoHttp()
        opts: dict[str, Any] = dict(kw)
        if clock is not None:
            opts.setdefault("sleep", clock.sleep)
            opts.setdefault("clock", clock)
        return ConnectorHttpClient(manifest, secrets, allow_loopback_http=True, **opts)
    return factory
