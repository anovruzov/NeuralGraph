"""The connectors' HTTP client: :class:`ConnectorHttpClient`, the aiohttp implementation of ``contract.ConnectorHttp``
(docs/mycelic/INGESTION.md §3.3, §3.10, §10.5, §11.2).

What it guarantees, whatever the connector does:

* **Egress allow-list.** Only ``https://`` URLs whose host is in ``manifest.allowed_hosts`` are requested. Redirects are
  never followed (a 3xx could leave the allow-list carrying the owner's token); the caller sees the status and
  ``Location`` instead. ``allow_loopback_http=True`` additionally permits ``http://127.0.0.1:<port>`` / ``[::1]`` so the
  offline mock providers can be used; it is a constructor argument only and nothing reads it from the environment.
* **Credentials.** ``Authorization: Bearer`` is built from ``secrets.get()`` per request (decrypted on demand, never kept
  on the client) and is the only place :meth:`Secret.reveal` is called for API traffic. Tokens never appear in logs,
  exceptions or the ETag cache (requests are keyed by a hash of the token).
* **Rate limits.** Provider headers first: GitHub's ``x-ratelimit-*`` (requests are spread when fewer than 5% remain, and
  wait until ``reset`` at zero), ``Retry-After`` on 429 and secondary-limit 403s, exponential waits for a secondary limit
  without a hint. A wait up to ``max_inline_wait`` seconds is slept inline; a longer one raises :class:`RateLimited` so the
  scheduler can park the stream and serve other connectors. ``serial_per_token`` (GitHub best practice) holds one
  ``asyncio.Lock`` per credential around each request chain, so a token never has two requests in flight in this process.
* **Retries.** Connection errors, timeouts and 5xx are retried ``max_retries`` times with exponential backoff and jitter,
  then raised as :class:`TransientError`.
* **Status mapping** (unless the status is in ``expected``): 401 → :class:`AuthExpired`; 429 / rate-limited 403 →
  :class:`RateLimited`; other 403 → :class:`InsufficientScope`; 404 / 410 / 3xx → :class:`ObjectGone` (with
  ``moved_to``); 5xx → :class:`TransientError`; anything else → :class:`PermanentError`. Connectors that give a status a
  provider-specific meaning list it in ``expected`` and decide themselves.
* **Conditional requests.** With ``etag_key`` the response ``ETag`` is remembered (per key, URL and token) and sent as
  ``If-None-Match`` next time; a 304 returns ``HttpResult(not_modified=True)`` with an empty body. Bodies are not cached
  (data minimization): a 304 means "nothing changed since you last processed this exact request".
* **Limits.** ``max_bytes`` caps the body (``Content-Length`` checked first, then the stream); ``timeout`` bounds each attempt.
* **Logs** carry method, host, a path template (names and ids replaced), status, duration and rate-limit numbers — never
  query strings, bodies or tokens.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json as _json
import random
import re
import time
import weakref
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlencode, urlsplit

import aiohttp

from .contract import (AuthExpired, ConnectorHttp, ConnectorManifest, HttpResult, InsufficientScope, NoHttp, ObjectGone, PermanentError,
                       RateLimited, Secret, SecretAccessor, TransientError, get_logger)

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 10_000_000
MAX_INLINE_WAIT = 30.0           # §3.3: longer waits raise RateLimited so the scheduler can work on other connectors
MAX_RETRIES = 3
SPREAD_BELOW = 0.05              # §11.2: below 5% of the primary limit, spread the remaining requests until reset
SECONDARY_BASE_WAIT = 60.0       # GitHub: without a hint, wait at least a minute, growing exponentially
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_LINK_PART = re.compile(r'\s*<([^>]*)>\s*((?:;\s*[^;,]+)*)')
_REL = re.compile(r'rel\s*=\s*"?([^";,]+)"?', re.IGNORECASE)
_WORD = re.compile(r"[a-z][a-z_.-]{0,40}")
_RATE_MESSAGE = re.compile(r"rate limit", re.IGNORECASE)
_SECONDARY_MESSAGE = re.compile(r"secondary rate limit|abuse", re.IGNORECASE)

Sleep = Callable[[float], Awaitable[None]]


# ---------------------------------------------------------------------------------------------- helpers
def parse_link_header(value: str | None) -> dict[str, str]:
    """RFC 8288 ``Link`` header → ``{rel: url}`` (``next``, ``prev``, ``first``, ``last``)."""
    out: dict[str, str] = {}
    if not value:
        return out
    for part in _split_links(value):
        m = _LINK_PART.match(part)
        if not m:
            continue
        url, params = m.group(1).strip(), m.group(2) or ""
        rel = _REL.search(params)
        if rel:
            for name in rel.group(1).split():
                out.setdefault(name.strip().lower(), url)
    return out


def _split_links(value: str) -> list[str]:
    """Split on commas that are outside ``<...>`` (URLs may contain commas)."""
    parts, buf, depth = [], [], 0
    for ch in value:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def path_template(path: str) -> str:
    """A loggable path: API words are kept, owner/repo names, ids and anything else become placeholders, e.g.
    ``/repos/acme/checkout/issues/482`` → ``/repos/{name}/{name}/issues/{id}``."""
    segs = [s for s in (path or "/").split("?", 1)[0].strip("/").split("/") if s]
    out: list[str] = []
    names = 0
    for s in segs:
        if names:
            out.append("{name}")
            names -= 1
            continue
        if s == "repos":
            out.append(s)
            names = 2
            continue
        if s in ("users", "orgs", "repositories"):
            out.append(s)
            names = 1
            continue
        out.append(s if _WORD.fullmatch(s) else "{id}")
    return "/" + "/".join(out)


def check_egress(url: str, allowed_hosts: Sequence[str], *, allow_loopback_http: bool = False) -> str:
    """The URL's host if the URL may be requested, else :class:`PermanentError` ``egress_denied``. Only https to an
    allow-listed host; plain http only to a loopback address and only when explicitly allowed (offline mocks)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise PermanentError("malformed URL", code="egress_denied") from None
    if parts.username or parts.password:
        raise PermanentError("credentials in URLs are refused", code="egress_denied", detail={"host": host})
    allowed = {h.split("://", 1)[-1].lower() for h in allowed_hosts}
    if allow_loopback_http and host in LOOPBACK_HOSTS and parts.scheme in ("http", "https"):
        return host
    if parts.scheme != "https" or not host:
        raise PermanentError("only https egress is allowed", code="egress_denied", detail={"host": host})
    if host not in allowed and (f"{host}:{port}" not in allowed if port else True):
        raise PermanentError("host is not on the connector's egress allow-list", code="egress_denied", detail={"host": host})
    return host


def _now() -> float:
    return time.time()


# per event loop, one lock per credential (keyed by a hash of the token): locks are loop-bound objects, and tests run each
# case on a fresh loop, so a process-wide dict of locks would leak across loops
_TOKEN_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]]" = weakref.WeakKeyDictionary()


def _token_lock(key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _TOKEN_LOCKS.get(loop)
    if locks is None:
        locks = {}
        _TOKEN_LOCKS[loop] = locks
    lock = locks.get(key)
    if lock is None:
        lock = locks[key] = asyncio.Lock()
    return lock


# ---------------------------------------------------------------------------------------------- the client
class ConnectorHttpClient:
    """``ConnectorHttp`` on aiohttp. One instance per connection (the pipeline caches it per ``connector_id``), so the ETag
    cache and the rate-limit view survive between polls. ``secrets=None`` makes an unauthenticated client (OAuth token
    endpoints, where the client credentials travel in the form body)."""

    def __init__(self, manifest: ConnectorManifest, secrets: SecretAccessor | None = None, *, allow_loopback_http: bool = False,
                 timeout: float = DEFAULT_TIMEOUT, max_bytes: int = DEFAULT_MAX_BYTES, max_retries: int = MAX_RETRIES,
                 max_inline_wait: float = MAX_INLINE_WAIT, serial_per_token: bool | None = None, sleep: Sleep | None = None,
                 clock: Callable[[], float] | None = None, trust_env: bool = False, user_agent: str | None = None) -> None:
        self.manifest = manifest
        self.secrets = secrets
        self.allowed_hosts = tuple(manifest.allowed_hosts)
        self.allow_loopback_http = bool(allow_loopback_http)
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self.max_retries = int(max_retries)
        self.max_inline_wait = float(max_inline_wait)
        self.serial = manifest.rate_limit.serial_per_token if serial_per_token is None else bool(serial_per_token)
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or _now
        self.trust_env = bool(trust_env)
        self.user_agent = user_agent or f"mycelic-ingest/{manifest.version}"
        self._session: aiohttp.ClientSession | None = None
        self._session_loop: asyncio.AbstractEventLoop | None = None
        self._etags: dict[str, str] = {}
        self.rate: dict[str, Any] = {}           # last seen provider rate-limit numbers (no content)
        self.requests_made = 0

    # ------------------------------------------------------------------ lifecycle
    async def _get_session(self) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        if self._session is None or self._session.closed or self._session_loop is not loop:
            if self._session is not None and not self._session.closed and self._session_loop is not None and not self._session_loop.is_closed():
                with contextlib.suppress(Exception):
                    await self._session.close()
            self._session = aiohttp.ClientSession(trust_env=self.trust_env, auto_decompress=True)
            self._session_loop = loop
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def rate_snapshot(self) -> dict[str, Any]:
        return dict(self.rate)

    # ------------------------------------------------------------------ request
    async def request(self, method: str, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None,
                      json: Any | None = None, etag_key: str | None = None, expected: Sequence[int] = (200,), max_bytes: int | None = None,
                      form: Mapping[str, Any] | None = None, auth: bool = True) -> HttpResult:
        """See the module docstring. ``form`` sends ``application/x-www-form-urlencoded`` (OAuth token endpoints);
        ``auth=False`` sends no Authorization header."""
        host = check_egress(url, self.allowed_hosts, allow_loopback_http=self.allow_loopback_http)
        token = None
        if auth:
            if self.secrets is None:
                raise AuthExpired("no credentials for this connection", detail={"reason": "no_credentials"})
            creds = await self.secrets.get()
            token = creds.access_token.reveal() if creds.access_token is not None else None
            if not token:
                raise AuthExpired("no credentials for this connection", detail={"reason": "no_credentials"})
        token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16] if token else ""
        lock = _token_lock(token_key) if (self.serial and token_key) else None
        if lock is None:
            return await self._send(method, url, host, params=params, headers=headers, json=json, form=form, etag_key=etag_key,
                                    expected=tuple(expected), max_bytes=max_bytes, token=token, token_key=token_key)
        async with lock:
            return await self._send(method, url, host, params=params, headers=headers, json=json, form=form, etag_key=etag_key,
                                    expected=tuple(expected), max_bytes=max_bytes, token=token, token_key=token_key)

    def _etag_slot(self, etag_key: str, method: str, url: str, params: Mapping[str, Any] | None, token_key: str) -> str:
        q = urlencode(sorted((str(k), str(v)) for k, v in (params or {}).items()))
        return f"{etag_key}|{hashlib.sha256(f'{method} {url}?{q}|{token_key}'.encode()).hexdigest()[:24]}"

    async def _send(self, method: str, url: str, host: str, *, params: Mapping[str, Any] | None, headers: Mapping[str, str] | None,
                    json: Any | None, form: Mapping[str, Any] | None, etag_key: str | None, expected: tuple[int, ...], max_bytes: int | None,
                    token: str | None, token_key: str) -> HttpResult:
        path = urlsplit(url).path
        tmpl = path_template(path)
        cap = int(max_bytes or self.max_bytes)
        slot = self._etag_slot(etag_key, method, url, params, token_key) if etag_key else None
        attempt = 0
        while True:
            await self._respect_primary(tmpl)
            hdrs = {"User-Agent": self.user_agent}
            hdrs.update(dict(headers or {}))
            if token:
                hdrs["Authorization"] = f"Bearer {token}"
            sent_etag = self._etags.get(slot) if slot else None
            if sent_etag:
                hdrs["If-None-Match"] = sent_etag
            t0 = time.perf_counter()
            try:
                status, rh, body = await self._once(method, url, params=params, headers=hdrs, json=json, form=form, cap=cap)
            except PermanentError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
                ms = (time.perf_counter() - t0) * 1000
                if attempt >= self.max_retries:
                    logger.info("http %s %s%s failed after %d attempts (%s, %.0fms)", method, host, tmpl, attempt + 1, type(exc).__name__, ms)
                    raise TransientError("provider unreachable", code="transient", detail={"host": host, "error": type(exc).__name__}) from None
                await self._sleep(self._backoff(attempt))
                attempt += 1
                continue
            ms = (time.perf_counter() - t0) * 1000
            self.requests_made += 1
            self._update_rate(rh)
            logger.debug("http %s %s%s -> %s in %.0fms (rate %s/%s)", method, host, tmpl, status, ms, self.rate.get("remaining", "-"),
                         self.rate.get("limit", "-"))
            if status == 304 and sent_etag:
                return HttpResult(status=304, headers=rh, body=b"", json=None, not_modified=True, links={})
            if status in expected:
                parsed = _parse_json(body, rh)
                if slot and status == 200 and rh.get("etag"):
                    self._etags[slot] = rh["etag"]
                return HttpResult(status=status, headers=rh, body=body, json=parsed, not_modified=False, links=parse_link_header(rh.get("link")))
            # ---- errors
            message = _error_message(body, rh)
            if status == 429 or (status == 403 and self._rate_signal(rh, message)):
                wait, scope, reset_at = self._rate_wait(rh, message, attempt)
                if wait <= self.max_inline_wait and attempt < self.max_retries:
                    logger.info("http %s %s%s rate limited (%s); waiting %.1fs", method, host, tmpl, scope, wait)
                    await self._sleep(wait)
                    attempt += 1
                    continue
                raise RateLimited(wait, scope=scope, reset_at=reset_at, detail={"host": host, "status": status})
            if status >= 500:
                if attempt < self.max_retries:
                    await self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise TransientError(f"provider error {status}", detail={"host": host, "status": status})
            if status == 401:
                raise AuthExpired("the provider rejected the credentials", detail={"host": host, "status": status})
            if status == 403:
                raise InsufficientScope(detail={"host": host, "status": status, "path": tmpl})
            if status in (301, 302, 303, 307, 308, 404, 410):
                loc = rh.get("location")
                raise ObjectGone(tmpl, status=status, moved_to=loc, detail={"host": host, "status": status})
            raise PermanentError(f"unexpected status {status}", code="http_error", detail={"host": host, "status": status})

    async def _once(self, method: str, url: str, *, params: Mapping[str, Any] | None, headers: Mapping[str, str], json: Any | None,
                    form: Mapping[str, Any] | None, cap: int) -> tuple[int, dict[str, str], bytes]:
        session = await self._get_session()
        timeout = aiohttp.ClientTimeout(total=self.timeout, connect=min(10.0, self.timeout))
        kw: dict[str, Any] = {"params": {str(k): str(v) for k, v in (params or {}).items()} or None, "headers": dict(headers),
                              "allow_redirects": False, "timeout": timeout}
        if json is not None:
            kw["json"] = json
        elif form is not None:
            # Secret values (client secrets, PKCE verifiers, refresh tokens) are revealed here, at the wire, and nowhere else
            kw["data"] = {str(k): (v.reveal() if isinstance(v, Secret) else str(v)) for k, v in form.items()}
        async with session.request(method, url, **kw) as resp:
            rh = {k.lower(): v for k, v in resp.headers.items()}
            length = rh.get("content-length")
            if length and length.isdigit() and int(length) > cap:
                raise PermanentError("response exceeds the size limit", code="response_too_large", detail={"limit": cap})
            body = await resp.content.read(cap + 1)
            if len(body) > cap:
                raise PermanentError("response exceeds the size limit", code="response_too_large", detail={"limit": cap})
            return resp.status, rh, body

    # ------------------------------------------------------------------ rate limits
    def _update_rate(self, rh: Mapping[str, str]) -> None:
        for name in ("limit", "remaining", "used", "reset"):
            v = rh.get(f"x-ratelimit-{name}")
            if v is not None and v.strip().lstrip("-").isdigit():
                self.rate[name] = int(v)
        if rh.get("x-ratelimit-resource"):
            self.rate["resource"] = rh["x-ratelimit-resource"][:40]

    async def _respect_primary(self, tmpl: str) -> None:
        """Before a request: wait (or raise) when the primary budget is exhausted, spread requests when it is nearly so."""
        limit, remaining, reset = self.rate.get("limit"), self.rate.get("remaining"), self.rate.get("reset")
        if not limit or remaining is None or reset is None:
            return
        now = self._clock()
        if reset <= now:
            return
        if remaining <= 0:
            wait = reset - now + 1.0
        elif remaining < SPREAD_BELOW * limit:
            wait = (reset - now) / (remaining + 1)
        else:
            return
        if wait > self.max_inline_wait:
            raise RateLimited(wait, scope="token", reset_at=float(reset), detail={"remaining": remaining})
        await self._sleep(wait)

    @staticmethod
    def _rate_signal(rh: Mapping[str, str], message: str) -> bool:
        return rh.get("x-ratelimit-remaining") == "0" or "retry-after" in rh or bool(_RATE_MESSAGE.search(message))

    def _rate_wait(self, rh: Mapping[str, str], message: str, attempt: int) -> tuple[float, str, float | None]:
        now = self._clock()
        reset = rh.get("x-ratelimit-reset")
        reset_at = float(reset) if reset and reset.isdigit() else None
        ra = (rh.get("retry-after") or "").strip()
        if ra:
            try:
                wait = max(0.0, float(ra))
            except ValueError:
                wait = SECONDARY_BASE_WAIT
            scope = "secondary" if _SECONDARY_MESSAGE.search(message) else ("token" if rh.get("x-ratelimit-remaining") == "0" else "endpoint")
            return wait, scope, reset_at
        if rh.get("x-ratelimit-remaining") == "0" and reset_at is not None:
            return max(0.0, reset_at - now) + 1.0, "token", reset_at
        return SECONDARY_BASE_WAIT * (2 ** attempt), "secondary", reset_at

    def _backoff(self, attempt: int) -> float:
        return min(30.0, 0.5 * (2 ** attempt)) * (0.8 + 0.4 * random.random())


def _parse_json(body: bytes, rh: Mapping[str, str]) -> Any | None:
    if not body or "json" not in (rh.get("content-type") or "").lower():
        return None
    try:
        return _json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PermanentError("provider returned malformed JSON", code="bad_json") from None


def _error_message(body: bytes, rh: Mapping[str, str]) -> str:
    """The provider's error ``message`` (a fixed provider string such as "Bad credentials"); used only for classification."""
    try:
        data = _parse_json(body[:4096], rh) if body else None
    except PermanentError:
        return ""
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or "")[:200]
    return ""


def default_http_factory(con: Mapping[str, Any], manifest: ConnectorManifest, secrets: SecretAccessor) -> ConnectorHttp:
    """What :class:`~mycelic.ingest.pipeline.IngestPipeline` gives a connection by default: no network for file-based
    connectors (empty allow-list), otherwise a real client restricted to the manifest's hosts."""
    if not manifest.allowed_hosts:
        return NoHttp()
    return ConnectorHttpClient(manifest, secrets)


__all__ = ["ConnectorHttpClient", "check_egress", "default_http_factory", "parse_link_header", "path_template"]
