"""The one OpenAI-compatible chat client: synchronous, standard library only, and careful about what it keeps.

Ported (adapted, not merged) from origin/claude/mycelic-implementation-vr034p@388aa30,
``mycelic/models/openai_provider.py``: the retry-loop shape (retry transient statuses with backoff, honour a numeric
``Retry-After``) and the joining of list-of-parts message content. Dropped from the port: aiohttp and the async
session/semaphore; ``_error_message``, which copied the server's error text into the exception; retries on 408 and
409; ``response_format`` only for one vendor's host and the reasoning-model special cases keyed on model-name
regexes; the default base URL and default model names; the ``<think>`` regex (see ``jsonparse``); the embeddings
client and the hash embedder (which imported NeuralGraph).

Why ``http.client`` and not ``urllib``: a redirect is never followed (a 3xx is an error, so a key cannot be
forwarded to another host), and the deadline covers every byte of the response. Per HTTP try:

* connect with ``min(connect_timeout_s, remaining)``; any failure there is ``network``. That bound applies to the
  TCP connect and, separately, to the TLS handshake; a proxy's ``CONNECT`` reply is read under the deadline. DNS
  resolution has no timeout in the standard library, so a name that resolves slowly is not bounded here;
* before every send the socket timeout is set to what is left of ``deadline_s``;
* every receive, status line and headers included, goes through ``_DeadlineReader``: it sets the socket timeout
  to what is left and refuses to start once the deadline has passed (``timeout``). A server that trickles the
  status line, the headers or the body therefore still times out at ``deadline_s``;
* the body is read with ``read1``, the deadline is checked after every chunk, and a body larger than
  ``max_response_bytes`` is cut off as ``too_large``;
* a stream that ends with neither ``[DONE]`` nor a ``finish_reason`` was cut short: ``network``.

Statuses: 200 is parsed. 429 and 5xx are retried while ``max_retries`` allows (then ``http_4xx`` / ``http_5xx``);
anything else, including 3xx, 401 and 403, is ``http_4xx`` at once. ``network`` failures are retried except a TLS
verification failure; ``timeout`` and ``too_large`` never are. Error bodies are read up to 64 KiB and discarded;
no body, header or reason phrase is ever stored, logged or raised. The only headers read are ``Content-Type``,
``Retry-After`` (ASCII digits only, capped at 30 s) and ``X-Mycelic-Fake``.

Proxies are decided per connection from the environment, never by mutating ``os.environ``, and only for an
endpoint that may use one (:func:`endpoint_proxy`; ``Endpoint.uses_env_proxy``: by default only an ``external``
one, since a request to an endpoint inside a site's or HQ's boundary carries data that must not pass an off-site
gateway): loopback, private and link-local IP literals and ``localhost`` always connect directly; other hosts use
``http_proxy``/``https_proxy`` unless ``no_proxy`` matches (absolute-form request for http, a ``CONNECT`` tunnel for
https). Proxy credentials are not supported. The ``Authorization`` header is sent only when the endpoint names an
``api_key_env`` that is set; the key is read at call time and never stored on any object.
"""
from __future__ import annotations

import http.client
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from ...version import VERSION
from .routing import Endpoint

RETRY_AFTER_CAP_S = 30
BACKOFF_BASE_S = 0.5
BACKOFF_MAX_S = 8
ERROR_BODY_CAP = 65536
READ_CHUNK = 65536
USER_AGENT = "mycelic-collective/" + VERSION

_RETRY_AFTER_RE = re.compile(r"[0-9]{1,6}", re.ASCII)
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}", re.ASCII)
_SAFE_FINISH_RE = re.compile(r"[a-z_]{1,32}", re.ASCII)


@dataclass(frozen=True)
class ChatResult:
    content: str | None
    finish_reason: str | None
    model_served: str | None
    tokens_in: int | None
    tokens_out: int | None
    http_status: int | None
    transport_retries: int
    latency_ms: float
    ttft_ms: float | None
    fake_marker: bool
    content_chunks: int
    reasoning_chunks: int = 0


class TransportFailure(Exception):
    """A failed logical attempt at the transport level. Internal; carries a kind and numbers, never text."""

    def __init__(self, *, kind: str, http_status: int | None, transport_retries: int, latency_ms: float | None,
                 fake_marker: bool) -> None:
        super().__init__()
        self.kind = kind
        self.http_status = http_status
        self.transport_retries = transport_retries
        self.latency_ms = latency_ms
        self.fake_marker = fake_marker

    def __str__(self) -> str:
        return f"transport failure: kind={self.kind} http_status={self.http_status}"

    def __repr__(self) -> str:
        return f"TransportFailure(kind={self.kind!r}, http_status={self.http_status!r})"


# --------------------------------------------------------------------------------------------------- proxies

def is_private_host(host: str) -> bool:
    """True for ``localhost`` and for loopback, private or link-local IP literals. Never resolves DNS."""
    h = (host or "").strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _proxies(environ: Mapping[str, str] | None) -> dict[str, str]:
    """``urllib.request.getproxies_environment`` over an explicit mapping (same precedence rules)."""
    if environ is None:
        return urllib.request.getproxies_environment()
    proxies: dict[str, str] = {}
    for name, value in environ.items():
        lname = name.lower()
        if value and lname[-6:] == "_proxy":
            proxies[lname[:-6]] = value
    if "REQUEST_METHOD" in environ:
        proxies.pop("http", None)
    for name, value in environ.items():
        if name[-6:] == "_proxy":
            lname = name.lower()
            if value:
                proxies[lname[:-6]] = value
            else:
                proxies.pop(lname[:-6], None)
    return proxies


def proxy_for(url: str, environ: Mapping[str, str] | None = None) -> str | None:
    parts = urlsplit(url)
    if is_private_host(parts.hostname or ""):
        return None
    proxies = _proxies(environ)
    proxy = proxies.get(parts.scheme)
    if not proxy:
        return None
    if urllib.request.proxy_bypass_environment(parts.netloc, proxies):
        return None
    return proxy if "://" in proxy else "http://" + proxy


def endpoint_proxy(endpoint: Endpoint, environ: Mapping[str, str] | None = None) -> str | None:
    """The proxy a request to ``endpoint`` goes through: :func:`proxy_for` its base URL when the endpoint may use
    the environment's proxy (``Endpoint.uses_env_proxy``), else None (a direct connection). The fake provider makes
    no connection."""
    if endpoint.provider == "fake" or not endpoint.base_url or not endpoint.uses_env_proxy:
        return None
    return proxy_for(endpoint.base_url, environ)


# --------------------------------------------------------------------------------------------------- one HTTP try

class _SSE:
    """Incremental server-sent-events reader for chat-completion chunks."""

    def __init__(self) -> None:
        self.buf = b""
        self.parts: list[str] = []
        self.finish: Any = None
        self.model: Any = None
        self.usage: Any = None
        self.chunks = 0
        self.reasoning_chunks = 0
        self.malformed = False
        self.done = False

    def feed(self, data: bytes) -> bool:
        """Consume bytes; True when a token (content or reasoning) arrived in them."""
        self.buf += data
        token = False
        while not self.done:
            i = self.buf.find(b"\n")
            if i < 0:
                break
            line, self.buf = self.buf[:i].rstrip(b"\r"), self.buf[i + 1:]
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                self.done = True
                break
            try:
                event = json.loads(payload)
            except (ValueError, RecursionError):
                event = None
            if not isinstance(event, dict):
                self.malformed = True
                self.done = True
                break
            if event.get("model") is not None:
                self.model = event.get("model")
            if isinstance(event.get("usage"), dict):
                self.usage = event["usage"]
            choices = event.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta")
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        self.parts.append(content)
                        self.chunks += 1
                        token = True
                    # thinking deltas: llama-server names them reasoning_content, Ollama and vLLM reasoning;
                    # either is a token for TTFT (audit round 2: reasoning alone used to start no clock)
                    if any(isinstance(delta.get(f), str) and delta.get(f) for f in ("reasoning_content", "reasoning")):
                        self.reasoning_chunks += 1
                        token = True
                if choices[0].get("finish_reason") is not None:
                    self.finish = choices[0].get("finish_reason")
        return token


class _DeadlineReader(io.RawIOBase):
    """The socket as ``http.client`` reads it during one try.

    ``HTTPResponse`` reads the status line, the headers and the body through ``sock.makefile("rb")``. It is handed
    this reader in place of the socket (see ``_deadline_response``), so each ``recv`` is given only the time left
    before the deadline, and none starts after it. Without this, ``getresponse()`` would read up to 100 header lines
    under one socket timeout per ``recv``, and a server could hold the call far past ``deadline_s``.
    """

    def __init__(self, sock: socket.socket, deadline: float) -> None:
        super().__init__()
        self._sock = sock
        # an unbuffered socket file holds an io reference, so the socket stays open after the connection object
        # closes it (http.client does that as soon as the headers say the server will close), as its own file would
        self._file = sock.makefile("rb", buffering=0)
        self._deadline = deadline

    def makefile(self, mode: str = "rb") -> io.BufferedReader:
        return io.BufferedReader(self, READ_CHUNK)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout() from None
        self._sock.settimeout(left)
        return self._file.readinto(buffer)

    def close(self) -> None:
        if not self.closed:
            self._file.close()
        super().close()


def _deadline_response(deadline: float) -> Callable[..., http.client.HTTPResponse]:
    """An ``HTTPConnection.response_class`` that reads a response, or a proxy's ``CONNECT`` reply, by the deadline."""

    def make(sock: socket.socket, *args: Any, **kwargs: Any) -> http.client.HTTPResponse:
        return http.client.HTTPResponse(_DeadlineReader(sock, deadline), *args, **kwargs)  # type: ignore[arg-type]

    return make


@dataclass
class _Outcome:
    kind: str | None = None        # a transport failure in this try; None when a status line arrived
    status: int | None = None
    fake: bool = False
    retry_after: int | None = None
    tls_verify: bool = False
    body: bytes | None = None
    sse: _SSE | None = None
    latency_ms: float = 0.0
    ttft_ms: float | None = None


def _ssl_context(endpoint: Endpoint) -> ssl.SSLContext:
    if endpoint.ca_file:
        return ssl.create_default_context(cafile=endpoint.ca_file)
    return ssl.create_default_context()


def _connection(endpoint: Endpoint, url: str, proxy: str | None,
                timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    target = parts.path or "/"
    pp = urlsplit(proxy) if proxy else None
    if parts.scheme == "https":
        context = _ssl_context(endpoint)
        if pp is None:
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=context), target
        conn = http.client.HTTPSConnection(pp.hostname or "", pp.port or (443 if pp.scheme == "https" else 80),
                                           timeout=timeout, context=context)
        conn.set_tunnel(host, port)
        return conn, target
    if pp is None:
        return http.client.HTTPConnection(host, port, timeout=timeout), target
    conn = http.client.HTTPConnection(pp.hostname or "", pp.port or (443 if pp.scheme == "https" else 80),
                                      timeout=timeout)
    return conn, f"{parts.scheme}://{parts.netloc}{target}"


def _headers(endpoint: Endpoint, environ: Mapping[str, str], *, accept: str,
             body: bytes | None) -> list[tuple[str, str]]:
    headers = [("User-Agent", USER_AGENT), ("Accept", accept)]
    if body is not None:
        headers += [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
    if endpoint.api_key_env:
        key = environ.get(endpoint.api_key_env)
        if key:
            headers.append(("Authorization", "Bearer " + key))
    return headers


def _one_try(endpoint: Endpoint, method: str, path: str, body: bytes | None, *, accept: str, deadline_s: float,
             stream: bool, environ: Mapping[str, str] | None) -> _Outcome:
    """One HTTP request/response. Never raises for transport problems: they come back in ``_Outcome.kind``."""
    out = _Outcome()
    t0 = time.perf_counter()
    deadline = time.monotonic() + deadline_s

    def remaining() -> float:
        return max(0.001, deadline - time.monotonic())

    url = (endpoint.base_url or "") + path
    env = os.environ if environ is None else environ
    conn = sock = resp = None
    try:
        try:
            conn, target = _connection(endpoint, url, endpoint_proxy(endpoint, environ),
                                       min(endpoint.connect_timeout_s, remaining()))
            conn.response_class = _deadline_response(deadline)
            conn.connect()
        except ssl.SSLCertVerificationError:
            out.kind, out.tls_verify = "network", True
        except (OSError, http.client.HTTPException, ValueError):
            out.kind = "network"
        if out.kind is not None:
            return out
        sock = conn.sock
        try:
            conn.putrequest(method, target)
            for name, value in _headers(endpoint, env, accept=accept, body=body):
                conn.putheader(name, value)
            sock.settimeout(remaining())
            conn.endheaders()
            if body is not None:
                sock.settimeout(remaining())
                conn.send(body)
            resp = conn.getresponse()       # status line and headers: every recv is bounded by the deadline
        except socket.timeout:
            out.kind = "timeout"
        except (OSError, http.client.HTTPException, ValueError):
            out.kind = "network"
        if out.kind is not None:
            return out

        out.status = resp.status
        out.fake = resp.getheader("X-Mycelic-Fake") == "1"
        retry_after = (resp.getheader("Retry-After") or "").strip()
        if _RETRY_AFTER_RE.fullmatch(retry_after):
            out.retry_after = int(retry_after)
        if out.status != 200:
            _drain(resp, deadline)
            return out

        is_stream = stream and (resp.getheader("Content-Type") or "").lower().startswith("text/event-stream")
        sse = _SSE() if is_stream else None
        chunks: list[bytes] = []
        total = 0
        try:
            while not resp.isclosed():       # the response closes itself once Content-Length is satisfied
                chunk = resp.read1(READ_CHUNK)
                if not chunk:
                    if resp.length:          # the server promised more bytes than it sent
                        out.kind = "network"
                    break
                total += len(chunk)
                if total > endpoint.max_response_bytes:
                    out.kind = "too_large"
                    break
                if sse is not None:
                    if sse.feed(chunk) and out.ttft_ms is None:
                        out.ttft_ms = round((time.perf_counter() - t0) * 1000, 1)
                    if sse.done:
                        break
                else:
                    chunks.append(chunk)
                if time.monotonic() > deadline:
                    out.kind = "timeout"
                    break
        except socket.timeout:
            out.kind = "timeout"
        except (OSError, http.client.HTTPException):
            out.kind = "network"
        if out.kind is None and sse is not None and not sse.done and sse.finish is None:
            out.kind = "network"             # the stream ended with neither [DONE] nor a finish_reason: cut short
        if out.kind is None:
            out.sse = sse
            out.body = None if sse is not None else b"".join(chunks)
        return out
    finally:
        for closeable in (resp, conn, sock):
            if closeable is not None:
                try:
                    closeable.close()
                except OSError:
                    pass
        out.latency_ms = round((time.perf_counter() - t0) * 1000, 1)


def _drain(resp: http.client.HTTPResponse, deadline: float) -> None:
    """Read an error body up to ``ERROR_BODY_CAP`` and drop it; the status is all we keep."""
    total = 0
    try:
        while total < ERROR_BODY_CAP and time.monotonic() < deadline and not resp.isclosed():
            chunk = resp.read1(min(READ_CHUNK, ERROR_BODY_CAP - total))
            if not chunk:
                break
            total += len(chunk)
    except (OSError, http.client.HTTPException):
        pass


# --------------------------------------------------------------------------------------------------- parsing

def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe(value: Any, pattern: re.Pattern[str]) -> str | None:
    """Server-supplied labels go into the ledger, so only short, plain identifiers are kept."""
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _parse_body(body: bytes) -> tuple[str | None, str | None, str | None, int | None, int | None]:
    """(content, finish_reason, model, tokens_in, tokens_out) from a non-streamed 200 body."""
    try:
        obj = json.loads(body)
    except (ValueError, RecursionError):
        obj = None
    if not isinstance(obj, dict):
        return None, None, None, None, None
    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    tokens_in, tokens_out = _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
    model = _safe(obj.get("model"), _SAFE_ID_RE)
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, None, model, tokens_in, tokens_out
    finish = _safe(choices[0].get("finish_reason"), _SAFE_FINISH_RE)
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    if not isinstance(content, str):
        content = None
    return content, finish, model, tokens_in, tokens_out


def _classify(out: _Outcome) -> tuple[str | None, bool]:
    """(failure kind or None, retryable)."""
    if out.kind == "network":
        return "network", not out.tls_verify
    if out.kind is not None:
        return out.kind, False
    if out.status == 200:
        return None, False
    if out.status == 429:
        return "http_4xx", True
    if out.status is not None and 500 <= out.status <= 599:
        return "http_5xx", True
    return "http_4xx", False


def retry_delay(retry: int, retry_after: int | None) -> float:
    """Sleep before retry number ``retry`` (1-based)."""
    if retry_after is not None:
        return float(min(retry_after, RETRY_AFTER_CAP_S))
    return float(min(BACKOFF_BASE_S * 2 ** (retry - 1), BACKOFF_MAX_S))


# --------------------------------------------------------------------------------------------------- public API

def chat(endpoint: Endpoint, messages: list[dict[str, str]], *, max_tokens: int, response_format: dict[str, Any] | None,
         stream: bool = False, environ: Mapping[str, str] | None = None,
         sleep: Callable[[float], Any] = time.sleep) -> ChatResult:
    if endpoint.provider != "openai_compat" or not endpoint.base_url:
        raise ValueError("chat() needs an openai_compat endpoint") from None
    request: dict[str, Any] = {"model": endpoint.model, "messages": messages, "temperature": 0,
                               "max_tokens": max_tokens}
    if response_format is not None:
        request["response_format"] = response_format
    if endpoint.seed is not None:
        request["seed"] = endpoint.seed
    if endpoint.reasoning_effort is not None:
        request["reasoning_effort"] = endpoint.reasoning_effort
    if endpoint.chat_template_kwargs is not None:
        request["chat_template_kwargs"] = dict(endpoint.chat_template_kwargs)
    if stream:
        request["stream"] = True
        request["stream_options"] = {"include_usage": True}
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    accept = "text/event-stream" if stream else "application/json"

    retries = 0
    fake = False
    while True:
        out = _one_try(endpoint, "POST", "/chat/completions", body, accept=accept, deadline_s=endpoint.deadline_s,
                       stream=stream, environ=environ)
        fake = fake or out.fake
        kind, retryable = _classify(out)
        if kind is None:
            break
        if retryable and retries < endpoint.max_retries:
            retries += 1
            sleep(retry_delay(retries, out.retry_after))
            continue
        raise TransportFailure(kind=kind, http_status=out.status, transport_retries=retries,
                               latency_ms=out.latency_ms, fake_marker=fake) from None

    if out.sse is not None:
        sse = out.sse
        usage = sse.usage if isinstance(sse.usage, dict) else {}
        return ChatResult(content=None if sse.malformed else "".join(sse.parts),
                          finish_reason=_safe(sse.finish, _SAFE_FINISH_RE), model_served=_safe(sse.model, _SAFE_ID_RE),
                          tokens_in=_count(usage.get("prompt_tokens")),
                          tokens_out=_count(usage.get("completion_tokens")),
                          http_status=out.status, transport_retries=retries, latency_ms=out.latency_ms,
                          ttft_ms=out.ttft_ms, fake_marker=fake, content_chunks=sse.chunks,
                          reasoning_chunks=sse.reasoning_chunks)
    content, finish, model, tokens_in, tokens_out = _parse_body(out.body or b"")
    return ChatResult(content=content, finish_reason=finish, model_served=model, tokens_in=tokens_in,
                      tokens_out=tokens_out, http_status=out.status, transport_retries=retries,
                      latency_ms=out.latency_ms, ttft_ms=None, fake_marker=fake, content_chunks=0)


def list_models(endpoint: Endpoint, *, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """``GET <base_url>/models`` under the same proxy, TLS and auth rules. Not a model call: no ledger row."""
    if endpoint.provider != "openai_compat" or not endpoint.base_url:
        raise ValueError("list_models() needs an openai_compat endpoint") from None
    out = _one_try(endpoint, "GET", "/models", None, accept="application/json",
                   deadline_s=min(10.0, endpoint.deadline_s), stream=False, environ=environ)
    result: dict[str, Any] = {"ok": False, "http_status": out.status, "ids": [], "fake": out.fake}
    if out.kind is not None or out.status != 200:
        return result
    try:
        obj = json.loads(out.body or b"")
    except (ValueError, RecursionError):
        obj = None
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, list):
        result["ok"] = True
        for entry in data:
            if isinstance(entry, dict):
                ident = _safe(entry.get("id"), _SAFE_ID_RE)
                if ident is not None:
                    result["ids"].append(ident)
                if entry.get("fake") is True:
                    result["fake"] = True
    return result
