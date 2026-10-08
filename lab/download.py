"""The lab's one downloader: pinned release archives and model files, fetched anonymously, resumably and boundedly.

``http.client`` and not ``urllib``, so the connect timeout (:data:`CONNECT_TIMEOUT_S`) and the idle timeout of every
read (:data:`IDLE_TIMEOUT_S`, never past the deadline) are separate, and every redirect is followed by hand.

:func:`download` makes at most :data:`MAX_ATTEMPTS` attempts within ``deadline_s``. Every attempt starts from the
original URL (so a signed CDN URL is always fresh) and follows 301/302/303/307/308 itself, at most
:data:`MAX_REDIRECTS` hops, resolving ``Location`` against the URL it came from. Each hop must be https, or plain
http to ``127.0.0.1``, ``::1`` or ``localhost`` (the test stubs); anything else is refused. The request carries
exactly ``User-Agent: mycelic-lab``, ``Accept: */*``, ``Accept-Encoding: identity`` and, when resuming, ``Range:
bytes=<n>-`` (kept on every hop); never ``Authorization``, never a cookie. Proxies follow the environment as the
collective client decides them (``proxy_for``): a ``CONNECT`` tunnel for https, never for loopback. A certificate
failure is a network failure that is not retried.

Statuses. From the first host: 200 and 206 are read; 401, 403, 404, 410 and every other 4xx but 416 and 429 are
refused at once; 429 and 5xx are retried after :func:`retry_delay`. From a redirect target: 403, 404 and 410 (an
expired or invalid signed URL) restart from the original URL at once; 429 and 5xx are retried after a delay. A 416
anywhere deletes the partial file and restarts from 0 without ``Range``.

Resume. ``Range`` is sent only for a partial of at least :data:`RESUME_MIN_BYTES`; a smaller one is deleted. A 206 is
accepted only with ``Content-Range: bytes <n>-<total-1>/<total>`` where ``n`` is the partial's size and ``total`` the
expected size (when known); otherwise the partial is deleted and the attempt fails. A 200 to a ``Range`` request
rewrites the file from 0. ``resumed_from`` records ``n`` for every accepted 206.

Checks. ``Content-Length``, when present, must equal what is still missing; a body cut short (by the server or the
network) fails the attempt and keeps the partial for the next one; more bytes than expected delete it. After the last
chunk the size must equal ``expected_size`` when known. ``ENOSPC``, ``EDQUOT`` or any other write error deletes the
partial and fails at once (``disk``). The deadline is checked before every attempt, every sleep (a sleep that would
end past it fails at once) and every chunk.

Errors are :class:`DownloadError` with a ``kind`` (:data:`KINDS`) and a fixed problem from ``notes`` (an HTTP status
is the only server value it may carry); the partial file is deleted whenever one is raised. Each hop appends a
connection record ``{"purpose": "api" | "download" | "redirect", "logical_host", "host", "status"}``: ``logical_host``
is the host of the URL as named (before the test-only ``url_map``), ``host`` the one actually connected; in
production they are equal.
"""
from __future__ import annotations

import hashlib
import http.client
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit

from mycelic.collective.inference.client import proxy_for
from mycelic.collective.jsonio import StrictJsonError, strict_load

from .notes import (DOWNLOAD_DEADLINE, DOWNLOAD_DISK, DOWNLOAD_INSECURE, DOWNLOAD_NETWORK, DOWNLOAD_REDIRECTS,
                    DOWNLOAD_REFUSED, DOWNLOAD_SIZE)

CONNECT_TIMEOUT_S = 30
IDLE_TIMEOUT_S = 120
MAX_ATTEMPTS = 6
BACKOFF_S = (2, 4, 8, 16, 32)
RETRY_AFTER_CAP_S = 120
MAX_REDIRECTS = 5
RESUME_MIN_BYTES = 1 << 20
CHUNK = 1 << 20
DEFAULT_DEADLINE_S = 1800
MAX_JSON_BYTES = 4 << 20
USER_AGENT = "mycelic-lab"
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
KINDS = ("refused", "size", "disk", "network", "deadline")
HEADER_SUBSET = ("content-type", "location", "retry-after", "x-ratelimit-remaining", "x-ratelimit-reset")
PROBLEMS = {"refused": DOWNLOAD_REFUSED, "size": DOWNLOAD_SIZE, "disk": DOWNLOAD_DISK, "network": DOWNLOAD_NETWORK,
            "deadline": DOWNLOAD_DEADLINE}
_DIGITS_RE = re.compile(r"[0-9]{1,12}", re.ASCII)
_CONTENT_RANGE_RE = re.compile(r"bytes ([0-9]{1,15})-([0-9]{1,15})/([0-9]{1,15})", re.ASCII)

UrlMap = Callable[[str], str]


class DownloadError(Exception):
    def __init__(self, kind: str, problem: str, status: int | None = None,
                 connections: list[dict[str, Any]] | None = None) -> None:
        super().__init__(problem)
        self.kind = kind
        self.problem = problem
        self.status = status
        self.connections = connections if connections is not None else []


@dataclass(frozen=True)
class Download:
    path: Path
    size: int
    sha256: str
    attempts: int
    resumed_from: list[int]
    connections: list[dict[str, Any]]
    wall_s: float


@dataclass(frozen=True)
class JsonReply:
    status: int | None
    headers: dict[str, str]
    obj: Any
    connection: dict[str, Any]


# --------------------------------------------------------------------------------------------------- helpers

def sha256_file(path: str | os.PathLike[str]) -> tuple[str, int]:
    """(hex digest, size) of a file, streamed."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def retry_delay(retry: int, retry_after: str | None) -> float:
    """Seconds before delayed retry number ``retry`` (1-based): a digits-only ``Retry-After`` capped at
    :data:`RETRY_AFTER_CAP_S`, else :data:`BACKOFF_S`."""
    if retry_after is not None and _DIGITS_RE.fullmatch(retry_after.strip()):
        return float(min(int(retry_after.strip()), RETRY_AFTER_CAP_S))
    return float(BACKOFF_S[min(retry - 1, len(BACKOFF_S) - 1)])


def hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def hop_allowed(url: str) -> bool:
    """https anywhere, plain http only to loopback; nothing else."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return bool(host) and (parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK_HOSTS))


def connection(url: str, environ: Mapping[str, str] | None,
               timeout: float = CONNECT_TIMEOUT_S) -> tuple[http.client.HTTPConnection, str]:
    """An unconnected connection for ``url`` and the request target, through the environment's proxy when one
    applies (an https ``CONNECT`` tunnel; an absolute-form target for http). Loopback is never proxied."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    https = parts.scheme == "https"
    port = parts.port or (443 if https else 80)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    proxy = proxy_for(url, environ)
    pp = urlsplit(proxy) if proxy else None
    if https:
        context = ssl.create_default_context()
        if pp is None:
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=context), target
        conn = http.client.HTTPSConnection(pp.hostname or "", pp.port or 80, timeout=timeout, context=context)
        conn.set_tunnel(host, port)
        return conn, target
    if pp is None:
        return http.client.HTTPConnection(host, port, timeout=timeout), target
    return (http.client.HTTPConnection(pp.hostname or "", pp.port or 80, timeout=timeout),
            f"{parts.scheme}://{parts.netloc}{target}")


def _close(*things: Any) -> None:
    for thing in things:
        if thing is not None:
            try:
                thing.close()
            except OSError:
                pass


def _record(purpose: str, logical: str, actual: str) -> dict[str, Any]:
    return {"purpose": purpose, "logical_host": hostname(logical), "host": hostname(actual), "status": None}


# --------------------------------------------------------------------------------------------------- JSON API calls

def get_json(url: str, *, headers: Mapping[str, str] | None = None, url_map: UrlMap | None = None,
             environ: Mapping[str, str] | None = None) -> JsonReply:
    """One GET of a JSON API: never follows a redirect (a 3xx comes back as its status), reads at most
    :data:`MAX_JSON_BYTES` (``obj`` None beyond that, for a non-200 or for invalid JSON); a network failure gives
    status None."""
    actual = url_map(url) if url_map is not None else url
    record = _record("api", url, actual)
    if not hop_allowed(url) or not hop_allowed(actual):
        return JsonReply(None, {}, None, record)
    sent = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "identity"}
    sent.update(headers or {})
    conn = resp = None
    try:
        conn, target = connection(actual, environ)
        conn.connect()
        conn.sock.settimeout(IDLE_TIMEOUT_S)
        conn.putrequest("GET", target, skip_accept_encoding=True)
        for name, value in sent.items():
            conn.putheader(name, value)
        conn.endheaders()
        resp = conn.getresponse()
        record["status"] = resp.status
        subset = {name: resp.getheader(name) for name in HEADER_SUBSET if resp.getheader(name) is not None}
        body = resp.read(MAX_JSON_BYTES + 1)
    except (OSError, http.client.HTTPException, ValueError):
        return JsonReply(None, {}, None, record)
    finally:
        _close(resp, conn)
    obj = None
    if resp.status == 200 and len(body) <= MAX_JSON_BYTES:
        try:
            obj = strict_load(body)
        except StrictJsonError:
            obj = None
    return JsonReply(resp.status, subset, obj, record)


# --------------------------------------------------------------------------------------------------- downloads

@dataclass(frozen=True)
class _Outcome:
    """One attempt: ``done``, or a failure that is ``final`` or retried (``immediate`` retries skip the backoff)."""

    done: bool = False
    kind: str = "network"
    status: int | None = None
    final: bool = False
    immediate: bool = False
    retry_after: str | None = None
    problem: str | None = None


class _Deadline(Exception):
    pass


def _hash_prefix(path: Path) -> Any:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _body(resp: http.client.HTTPResponse, part: Path, start: int, expected_size: int | None, deadline: float,
          monotonic: Callable[[], float], sock: socket.socket | None) -> tuple[_Outcome, Any]:
    """Write the body from offset ``start``; (outcome, hasher of the whole file when done)."""
    hasher = _hash_prefix(part) if start else hashlib.sha256()
    written = 0
    try:
        fh = open(part, "ab" if start else "wb")
    except OSError:
        return _Outcome(kind="disk", final=True), None
    with fh:
        while True:
            left = deadline - monotonic()
            if left <= 0:
                raise _Deadline()
            try:
                if sock is not None:
                    sock.settimeout(min(IDLE_TIMEOUT_S, max(left, 0.001)))
                chunk = resp.read1(CHUNK)
            except (OSError, http.client.HTTPException, ValueError):
                return _Outcome(kind="network"), None
            if not chunk:
                break
            written += len(chunk)
            if expected_size is not None and start + written > expected_size:
                fh.close()
                _remove(part)
                return _Outcome(kind="size"), None
            try:
                fh.write(chunk)
            except OSError:                  # ENOSPC, EDQUOT or another write failure: nothing to resume from
                fh.close()
                _remove(part)
                return _Outcome(kind="disk", final=True), None
            hasher.update(chunk)
    if resp.length:                          # the server promised more bytes than it sent
        return _Outcome(kind="network"), None
    if expected_size is not None and start + written != expected_size:
        return _Outcome(kind="network"), None  # a connection that closed early without a Content-Length
    return _Outcome(done=True), hasher


def _attempt(url: str, part: Path, expected_size: int | None, deadline: float, connections: list[dict[str, Any]],
             resumed_from: list[int], url_map: UrlMap | None, environ: Mapping[str, str] | None,
             monotonic: Callable[[], float]) -> tuple[_Outcome, Any]:
    partial = part.stat().st_size if part.exists() else 0
    if partial < RESUME_MIN_BYTES or (expected_size is not None and partial > expected_size):
        _remove(part)
        partial = 0
    logical = url
    redirects = 0
    hop = 0
    while True:
        hop += 1
        if monotonic() >= deadline:
            raise _Deadline()
        actual = url_map(logical) if url_map is not None else logical
        record = _record("download" if hop == 1 else "redirect", logical, actual)
        connections.append(record)
        if not hop_allowed(logical) or not hop_allowed(actual):
            return _Outcome(kind="refused", final=True, problem=DOWNLOAD_INSECURE), None
        conn = resp = None
        try:
            try:
                conn, target = connection(actual, environ, min(CONNECT_TIMEOUT_S, max(deadline - monotonic(), 0.001)))
                conn.connect()
            except ssl.SSLCertVerificationError:
                return _Outcome(kind="network", final=True), None
            except (OSError, http.client.HTTPException, ValueError):
                return _Outcome(kind="network"), None
            try:
                conn.sock.settimeout(min(IDLE_TIMEOUT_S, max(deadline - monotonic(), 0.001)))
                conn.putrequest("GET", target, skip_accept_encoding=True)
                conn.putheader("User-Agent", USER_AGENT)
                conn.putheader("Accept", "*/*")
                conn.putheader("Accept-Encoding", "identity")
                if partial:
                    conn.putheader("Range", f"bytes={partial}-")
                conn.endheaders()
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException, ValueError):
                return _Outcome(kind="network"), None
            status = resp.status
            record["status"] = status
            if status in REDIRECT_STATUSES:
                location = resp.getheader("Location")
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    return _Outcome(kind="refused", status=status, final=True, problem=DOWNLOAD_REDIRECTS), None
                if not location:
                    return _Outcome(kind="refused", status=status, final=True), None
                logical = urljoin(logical, location.strip())
                continue
            if status == 416:
                _remove(part)
                return _Outcome(kind="size", status=status, immediate=True), None
            if status == 429 or 500 <= status <= 599:
                return _Outcome(kind="network", status=status, retry_after=resp.getheader("Retry-After")), None
            if status not in (200, 206):
                if hop > 1 and status in (403, 404, 410):
                    return _Outcome(kind="refused", status=status, immediate=True), None
                return _Outcome(kind="refused", status=status, final=True), None
            start = 0
            if status == 206:
                m = _CONTENT_RANGE_RE.fullmatch((resp.getheader("Content-Range") or "").strip())
                ok = (m is not None and int(m.group(1)) == partial and int(m.group(2)) == int(m.group(3)) - 1
                      and (expected_size is None or int(m.group(3)) == expected_size))
                if not ok:
                    _remove(part)
                    return _Outcome(kind="size", status=status), None
                start = partial
            missing = expected_size - start if expected_size is not None else None
            length = (resp.getheader("Content-Length") or "").strip()
            if missing is not None and _DIGITS_RE.fullmatch(length) and int(length) != missing:
                return _Outcome(kind="size", status=status), None
            outcome, hasher = _body(resp, part, start, expected_size, deadline, monotonic, conn.sock)
            if outcome.done and start:
                resumed_from.append(start)
            return outcome, hasher
        finally:
            _close(resp, conn)


def download(url: str, part: str | os.PathLike[str], *, expected_size: int | None,
             deadline_s: float = DEFAULT_DEADLINE_S, max_attempts: int = MAX_ATTEMPTS, url_map: UrlMap | None = None,
             environ: Mapping[str, str] | None = None, sleep: Callable[[float], Any] = time.sleep,
             monotonic: Callable[[], float] = time.monotonic) -> Download:
    """Download ``url`` to ``part`` (see the module docstring); the caller verifies the digest and renames."""
    part = Path(part)
    part.parent.mkdir(parents=True, exist_ok=True)
    t0 = monotonic()
    deadline = t0 + deadline_s
    connections: list[dict[str, Any]] = []
    resumed_from: list[int] = []
    delayed = 0
    last = _Outcome()
    try:
        for attempt in range(1, max_attempts + 1):
            if attempt > 1 and not last.immediate:
                delayed += 1
                delay = retry_delay(delayed, last.retry_after)
                if monotonic() + delay > deadline:
                    raise _Deadline()
                sleep(delay)
            if monotonic() >= deadline:
                raise _Deadline()
            outcome, hasher = _attempt(url, part, expected_size, deadline, connections, resumed_from, url_map,
                                       environ, monotonic)
            if outcome.done:
                size = part.stat().st_size
                return Download(path=part, size=size, sha256=hasher.hexdigest(), attempts=attempt,
                                resumed_from=resumed_from, connections=connections,
                                wall_s=round(monotonic() - t0, 3))
            last = outcome
            if outcome.final:
                break
    except _Deadline:
        _remove(part)
        raise DownloadError("deadline", DOWNLOAD_DEADLINE, last.status, connections) from None
    except BaseException:
        _remove(part)
        raise
    _remove(part)
    problem = last.problem or PROBLEMS[last.kind]
    if last.kind == "refused" and last.status is not None and last.problem is None:
        problem = f"{problem} (HTTP {last.status})"
    raise DownloadError(last.kind, problem, last.status, connections) from None
