"""A local fake of an OpenAI-compatible server, for CI and for rehearsing the founder harnesses.

``FakeOpenAIServer(persona)`` listens on ``127.0.0.1:<random port>`` and answers ``POST /v1/chat/completions``
the way the named persona says: well-behaved, slow, broken, echoing the prompt into its error, and so on. Every
response carries ``X-Mycelic-Fake: 1`` and ``GET /v1/models`` lists one model with ``"fake": true``, so anything
measured against it is flagged as a rehearsal (``measurement: false``) by the harnesses.

It records every request (method, raw target, path, lower-cased headers, body bytes, parsed JSON, monotonic time)
so tests can assert exactly what crossed the wire. It is a library, not a CLI; it uses only the standard library.

Personas (``n`` counts chat requests to this server, from 0): ``valid``, ``no-usage``, ``invalid-then-valid``,
``always-invalid``, ``prose-only``, ``http500-then-ok``, ``429-retry-after``, ``http-date-retry-after``, ``slow``
(waits ``slow_s`` before the status line), ``slow-status`` (one status-line byte every ``trickle_s``),
``slow-headers`` (the status line, then one header byte every ``trickle_s``), ``trickle`` (one body byte every
``trickle_s``), ``oversized``,
``deep-nesting``, ``rejects-json_schema``, ``rejects-strict-keywords``, ``echoes-prompt-in-error``,
``unauthorized``, ``content-parts``, ``empty-choices``, ``tool-calls-only``, ``two-objects``,
``think-unterminated``, ``think-closed``, ``think-closing-only``, ``fenced``, ``fenced-bare``, ``prose-around``,
``truncated``, ``reset-mid-body``, ``stream``, ``stream-stall`` and ``stream-unsupported``. ``valid``, ``no-usage``
and ``stream`` answer a streaming request with server-sent events: ``n_chunks`` content chunks (fewer only when the
reply is shorter than that), and by default a usage chunk whose ``completion_tokens`` is the number of chunks sent.

``responder``, when given, is called with each chat request's parsed JSON and its return value is the reply the
``valid`` and ``invalid-then-valid`` personas (and every persona that answers with the good reply) send instead of
``reply``. :func:`request_payload` recovers the task payload from such a request: the strict-parsed JSON in the
first ``<data>`` block of the last user message that does not start with the repair marker (a repair note's block,
appended after the payload's or in a turn of its own, holds the previous reply's excerpt, not the payload), or None.

Every persona first checks the roles the way common chat templates do: after an optional leading ``system``
message, ``user`` and ``assistant`` must alternate, starting with ``user``. Anything else (two user turns in a row,
for instance) gets ``400`` with the templates' wording ("Conversation roles must alternate ..."), as vLLM and
llama-server answer when the model's template raises.
"""
from __future__ import annotations

import json
import socket
import ssl
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

from ..jsonio import StrictJsonError, strict_load
from .tasks import REPAIR_MARKER

PERSONAS = (
    "valid", "no-usage", "invalid-then-valid", "always-invalid", "prose-only", "http500-then-ok", "429-retry-after",
    "http-date-retry-after", "slow", "slow-status", "slow-headers", "trickle", "oversized", "deep-nesting",
    "rejects-json_schema",
    "rejects-strict-keywords", "echoes-prompt-in-error", "unauthorized", "content-parts", "empty-choices",
    "tool-calls-only", "two-objects", "think-unterminated", "think-closed", "think-closing-only", "fenced",
    "fenced-bare", "prose-around", "truncated", "reset-mid-body", "stream", "stream-stall", "stream-unsupported",
)
STRICT_KEYWORDS = ("pattern", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems")
DEFAULT_REPLY = {"answer": "ok"}
HTTP_DATE = "Wed, 21 Oct 2015 07:28:00 GMT"
_DEFAULT_USAGE = object()
_DATA_OPEN, _DATA_CLOSE = "<data>", "</data>"


def request_payload(request_json: Any) -> Any | None:
    """The task payload of a chat request (see the module docstring), or None when there is none."""
    messages = request_json.get("messages") if isinstance(request_json, dict) else None
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str) or content.startswith(REPAIR_MARKER):
            continue
        start = content.find(_DATA_OPEN)
        end = content.find(_DATA_CLOSE, start) if start >= 0 else -1
        if start < 0 or end < start:
            return None
        failed = False
        try:
            value = strict_load(content[start + len(_DATA_OPEN):end])
        except StrictJsonError:
            failed = True
        return None if failed else value
    return None


def roles_alternate(messages: Any) -> bool:
    """After an optional leading ``system`` message, ``user`` and ``assistant`` alternate, starting with ``user``."""
    if not isinstance(messages, list) or not messages:
        return False
    roles = [m.get("role") if isinstance(m, dict) else None for m in messages]
    if roles[0] == "system":
        roles = roles[1:]
    return bool(roles) and all(role == ("user", "assistant")[i % 2] for i, role in enumerate(roles))


def _contains_key(obj: Any, keys: tuple[str, ...]) -> bool:
    if isinstance(obj, dict):
        return any(k in keys for k in obj) or any(_contains_key(v, keys) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key(v, keys) for v in obj)
    return False


def _header_safe(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").encode("latin-1", "replace").decode("latin-1")[:16000]


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    fake: "FakeOpenAIServer"

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError, ssl.SSLError)):
            return                       # the client hung up (deadline, size cap, test teardown)
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "fake-openai"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass

    def do_GET(self) -> None:
        self.server.fake._handle(self, "GET")

    def do_POST(self) -> None:
        self.server.fake._handle(self, "POST")


class FakeOpenAIServer:
    def __init__(self, persona: str = "valid", *, reply: dict[str, Any] | None = None,
                 invalid_reply: dict[str, Any] | None = None, model_id: str = "fake-model",
                 served_model: str | None = None, usage: Any = _DEFAULT_USAGE, retry_after: str = "1",
                 slow_s: float = 5.0, trickle_s: float = 0.4, first_token_s: float = 0.3, token_s: float = 0.02,
                 n_chunks: int = 20, slots: int | None = None, oversize_bytes: int = 2097152,
                 tls: tuple[str, str] | None = None,
                 responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        if persona not in PERSONAS:
            raise ValueError(f"unknown persona {persona!r}") from None
        self.persona = persona
        self.reply = reply if reply is not None else dict(DEFAULT_REPLY)
        self.invalid_reply = invalid_reply
        self.model_id = model_id
        self.served_model = served_model
        self._usage = usage
        self.retry_after = retry_after
        self.slow_s, self.trickle_s = slow_s, trickle_s
        self.first_token_s, self.token_s, self.n_chunks = first_token_s, token_s, max(1, int(n_chunks))
        self.oversize_bytes = oversize_bytes
        self.tls = tls
        self.responder = responder
        self.requests: list[dict[str, Any]] = []
        self.port = 0
        self._slots = threading.Semaphore(slots) if slots else None
        self._lock = threading.Lock()
        self._chat_count = 0
        self._stop = threading.Event()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def base_url(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.port}/v1"

    @property
    def chat_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self.requests if r["path"] == "/v1/chat/completions"]

    def start(self) -> "FakeOpenAIServer":
        server = _Server(("127.0.0.1", 0), _Handler)
        server.fake = self
        if self.tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*self.tls)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "FakeOpenAIServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------ request handling
    def _handle(self, h: _Handler, method: str) -> None:
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length > 0 else b""
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        target = h.path
        path = urlsplit(target).path if target.startswith(("http://", "https://")) else target.split("?", 1)[0]
        record = {"method": method, "target": target, "path": path,
                  "headers": {k.lower(): v for k, v in h.headers.items()}, "body": body, "json": parsed,
                  "t": time.monotonic()}
        is_chat = method == "POST" and path == "/v1/chat/completions"
        with self._lock:
            self.requests.append(record)
            n = self._chat_count
            if is_chat:
                self._chat_count += 1
        if method == "GET" and path == "/v1/models":
            self._send(h, 200, {"object": "list", "data": [{"id": self.model_id, "object": "model", "fake": True}]})
        elif is_chat:
            request = parsed if isinstance(parsed, dict) else {}
            if self._slots is not None:
                with self._slots:
                    self._chat(h, request, body, n)
            else:
                self._chat(h, request, body, n)
        else:
            self._send(h, 404, {"error": {"code": "not_found", "message": "unknown route"}})

    def _send(self, h: _Handler, status: int, obj: Any, *, headers: tuple[tuple[str, str], ...] = (),
              reason: str | None = None, content_type: str = "application/json") -> None:
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h.send_response(status, reason)
        h.send_header("Content-Type", content_type)
        h.send_header("Content-Length", str(len(data)))
        h.send_header("X-Mycelic-Fake", "1")
        for name, value in headers:
            h.send_header(name, value)
        h.end_headers()
        h.wfile.write(data)

    def _model(self, request: dict[str, Any]) -> str:
        if self.served_model:
            return self.served_model
        model = request.get("model")
        return model if isinstance(model, str) else self.model_id

    def _usage_pair(self, completion_tokens: int = 45) -> tuple[int, int] | None:
        """``usage`` as configured; by default 123 prompt tokens and ``completion_tokens`` (45, or chunks sent)."""
        if self._usage is _DEFAULT_USAGE:
            return (123, completion_tokens)
        return self._usage

    def _completion(self, request: dict[str, Any], content: Any, *, finish: str = "stop", usage: bool = True,
                    message: dict[str, Any] | None = None) -> bytes:
        msg = message if message is not None else {"role": "assistant", "content": content}
        obj: dict[str, Any] = {"id": "chatcmpl-fake", "object": "chat.completion", "created": 0,
                               "model": self._model(request),
                               "choices": [{"index": 0, "message": msg, "finish_reason": finish}]}
        pair = self._usage_pair() if usage else None
        if pair is not None:
            obj["usage"] = {"prompt_tokens": pair[0], "completion_tokens": pair[1], "total_tokens": sum(pair)}
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    def _last_user(self, request: dict[str, Any]) -> str:
        for message in reversed(request.get("messages") or []):
            if isinstance(message, dict) and message.get("role") == "user" and isinstance(message.get("content"), str):
                return message["content"]
        return ""

    def _invalid(self, request: dict[str, Any]) -> str:
        reply = self.invalid_reply
        if reply is None:
            reply = {"unexpected": f"FAKE-REPLY-MARKER-{self.port} " + self._last_user(request)[:200]}
        return json.dumps(reply, ensure_ascii=False)

    def _chat(self, h: _Handler, request: dict[str, Any], raw: bytes, n: int) -> None:
        persona = self.persona
        if not roles_alternate(request.get("messages")):
            return self._send(h, 400, {"error": {"code": 400, "type": "invalid_request_error", "message": (
                "Conversation roles must alternate user/assistant/user/assistant/...")}})
        good = json.dumps(self.responder(request) if self.responder is not None else self.reply, ensure_ascii=False)
        wants_stream = request.get("stream") is True
        if persona in ("valid", "stream") and wants_stream:
            return self._stream(h, request, good, usage=True)
        if persona == "no-usage" and wants_stream:
            return self._stream(h, request, good, usage=False)
        if persona == "stream-stall" and wants_stream:
            return self._stall(h, request, good)

        if persona == "no-usage":
            return self._send(h, 200, self._completion(request, good, usage=False))
        if persona == "invalid-then-valid":
            return self._send(h, 200, self._completion(request, self._invalid(request) if n == 0 else good))
        if persona == "always-invalid":
            return self._send(h, 200, self._completion(request, self._invalid(request)))
        if persona == "prose-only":
            return self._send(h, 200, self._completion(request, "I could not find a structured answer in the text."))
        if persona == "http500-then-ok" and n == 0:
            return self._send(h, 500, {"error": {"message": "internal error", "type": "server_error"}})
        if persona == "429-retry-after" and n == 0:
            return self._send(h, 429, {"error": {"message": "slow down"}}, headers=(("Retry-After", self.retry_after),))
        if persona == "http-date-retry-after" and n == 0:
            return self._send(h, 429, {"error": {"message": "slow down"}}, headers=(("Retry-After", HTTP_DATE),))
        if persona == "slow":
            if self._stop.wait(self.slow_s):
                return None
            return self._send(h, 200, self._completion(request, good))
        if persona == "slow-status":
            return self._slow_head(h, b"", b"HTTP/1.0 200 OK\r\n", self._completion(request, good))
        if persona == "slow-headers":
            return self._slow_head(h, b"HTTP/1.0 200 OK\r\nX-Mycelic-Fake: 1\r\n", b"X-Padding: " + b"." * 48 + b"\r\n",
                                   self._completion(request, good))
        if persona == "trickle":
            return self._trickle(h, self._completion(request, good))
        if persona == "oversized":
            return self._send(h, 200, self._completion(request, "x" * self.oversize_bytes))
        if persona == "deep-nesting":
            return self._send(h, 200, self._completion(request, "[" * 100000))
        if persona == "rejects-json_schema":
            fmt = request.get("response_format")
            if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
                return self._send(h, 400, {"error": {"message": "response_format json_schema is not supported"}})
        if persona == "rejects-strict-keywords":
            fmt = request.get("response_format")
            schema = fmt.get("json_schema", {}).get("schema") if isinstance(fmt, dict) else None
            if _contains_key(schema, STRICT_KEYWORDS):
                return self._send(h, 400, {"error": {"message": "schema keyword not supported"}})
        if persona == "echoes-prompt-in-error":
            text = raw.decode("utf-8", "replace")
            return self._send(h, 400, {"error": {"message": text, "type": "invalid_request_error"}},
                              headers=(("X-Echo", _header_safe(text)),), reason="Bad Request " + _header_safe(text))
        if persona == "unauthorized":
            auth = h.headers.get("Authorization") or ""
            return self._send(h, 401, {"error": {"message": "invalid key: " + auth}},
                              headers=(("X-Echo", _header_safe(auth)),), reason="Unauthorized " + _header_safe(auth))
        if persona == "content-parts":
            half = len(good) // 2
            parts = [{"type": "text", "text": good[:half]}, {"type": "text", "text": good[half:]}]
            return self._send(h, 200, self._completion(request, parts))
        if persona == "empty-choices":
            return self._send(h, 200, {"id": "chatcmpl-fake", "object": "chat.completion",
                                       "model": self._model(request), "choices": []})
        if persona == "tool-calls-only":
            message = {"role": "assistant", "content": None,
                       "tool_calls": [{"id": "call_0", "type": "function",
                                       "function": {"name": "lookup", "arguments": good}}]}
            return self._send(h, 200, self._completion(request, None, message=message, finish="tool_calls"))
        if persona == "two-objects":
            return self._send(h, 200, self._completion(request, good + "\n" + json.dumps({"second": True})))
        if persona == "think-unterminated":
            return self._send(h, 200, self._completion(request, "<think>I am still weighing " + good))
        if persona == "think-closed":
            content = "<think>a first draft: " + json.dumps({"decoy": True}) + "</think>\n" + good
            return self._send(h, 200, self._completion(request, content))
        if persona == "think-closing-only":
            content = "weighing a draft " + json.dumps({"decoy": True}) + "</think>" + good
            return self._send(h, 200, self._completion(request, content))
        if persona == "fenced":
            return self._send(h, 200, self._completion(request, "```json\n" + good + "\n```"))
        if persona == "fenced-bare":
            return self._send(h, 200, self._completion(request, "```\n" + good + "\n```"))
        if persona == "prose-around":
            return self._send(h, 200, self._completion(request, "Here is the result: " + good + " Hope this helps."))
        if persona == "truncated":
            return self._send(h, 200, self._completion(request, good[: len(good) // 2], finish="length"))
        if persona == "reset-mid-body" and n == 0:
            return self._reset(h, self._completion(request, good))
        return self._send(h, 200, self._completion(request, good))

    def _trickle(self, h: _Handler, data: bytes) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.send_header("X-Mycelic-Fake", "1")
        h.end_headers()
        h.wfile.flush()
        for i in range(len(data)):
            if self._stop.wait(self.trickle_s):
                return
            h.wfile.write(data[i:i + 1])
            h.wfile.flush()

    def _slow_head(self, h: _Handler, sent: bytes, trickled: bytes, data: bytes) -> None:
        """Write ``sent`` at once, then ``trickled`` one byte every ``trickle_s``, then the rest of a 200 response."""
        h.close_connection = True
        h.wfile.write(sent)
        h.wfile.flush()
        for i in range(len(trickled)):
            if self._stop.wait(self.trickle_s):
                return
            h.wfile.write(trickled[i:i + 1])
            h.wfile.flush()
        head = b"" if sent else b"X-Mycelic-Fake: 1\r\n"
        h.wfile.write(head + b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(data) + data)
        h.wfile.flush()

    def _reset(self, h: _Handler, data: bytes) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.send_header("X-Mycelic-Fake", "1")
        h.end_headers()
        h.wfile.write(data[:10])
        h.wfile.flush()
        h.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        h.rfile.close()
        h.connection.close()             # with linger 0 this sends a reset, not a clean end of stream
        h.close_connection = True

    def _sse_head(self, h: _Handler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("X-Mycelic-Fake", "1")
        h.end_headers()
        h.wfile.flush()

    def _event(self, h: _Handler, obj: Any) -> None:
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h.wfile.write(b"data: " + data + b"\n\n")
        h.wfile.flush()

    def _chunk(self, request: dict[str, Any], delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": self._model(request),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    def _pieces(self, content: str) -> list[str]:
        """Exactly ``n_chunks`` non-empty slices (fewer only when the content is shorter than that)."""
        n = max(1, min(self.n_chunks, len(content)))
        bounds = [len(content) * i // n for i in range(n + 1)]
        return [content[bounds[i]:bounds[i + 1]] for i in range(n)]

    def _stream(self, h: _Handler, request: dict[str, Any], content: str, *, usage: bool) -> None:
        self._sse_head(h)
        if self._stop.wait(self.first_token_s):
            return
        pieces = self._pieces(content)
        for i, piece in enumerate(pieces):
            if i and self._stop.wait(self.token_s):
                return
            delta = {"role": "assistant", "content": piece} if i == 0 else {"content": piece}
            self._event(h, self._chunk(request, delta))
        self._event(h, self._chunk(request, {}, "stop"))
        options = request.get("stream_options")
        pair = self._usage_pair(len(pieces)) if usage else None
        if pair is not None and isinstance(options, dict) and options.get("include_usage") is True:
            self._event(h, {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0,
                            "model": self._model(request), "choices": [],
                            "usage": {"prompt_tokens": pair[0], "completion_tokens": pair[1],
                                      "total_tokens": sum(pair)}})
        self._event(h, b"[DONE]")

    def _stall(self, h: _Handler, request: dict[str, Any], content: str) -> None:
        self._sse_head(h)
        for piece in self._pieces(content)[:2]:
            self._event(h, self._chunk(request, {"content": piece}))
        self._stop.wait(30.0)
