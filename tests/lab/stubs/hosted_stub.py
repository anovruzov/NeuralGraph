"""A loopback stand-in for a hosted OpenAI-compatible provider: a front that serves a base URL under a path prefix.

``PrefixFront(target_base_url, prefix)`` listens on ``127.0.0.1:<random port>``; its :attr:`base_url` is
``http://127.0.0.1:<port><prefix>``, so a test can put a sentinel in the path a hosted base URL may carry. It records
every request it receives (method, path and lower-cased headers, :attr:`requests`), forwards ``POST
<prefix>/chat/completions`` to ``<target_base_url>/chat/completions`` with every header and the body unchanged, and
returns the target's status, body and headers. ``GET <prefix>/models`` is forwarded the same way.

Modes: ``strip_fake`` drops the target's ``X-Mycelic-Fake`` header from every reply and answers ``GET
<prefix>/models`` itself with the target's model ids and no fake flag (so a run through it looks like a real host);
``fail_status`` answers every chat request with that status; ``redirect_to`` answers every request with ``307`` and
``Location: <redirect_to>``. Any other path is 404. The target is normally the collective's ``FakeOpenAIServer``.
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

HOP_HEADERS = ("host", "content-length", "connection", "transfer-encoding")
FAKE_HEADER = "x-mycelic-fake"


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


class PrefixFront:
    def __init__(self, target_base_url: str, prefix: str, *, strip_fake: bool = False, fail_status: int | None = None,
                 redirect_to: str | None = None) -> None:
        self.target = urlsplit(target_base_url)
        self.prefix = prefix.rstrip("/")
        self.strip_fake = strip_fake
        self.fail_status = fail_status
        self.redirect_to = redirect_to
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.httpd: _Server | None = None

    @property
    def base_url(self) -> str:
        assert self.httpd is not None
        return f"http://127.0.0.1:{self.httpd.server_address[1]}{self.prefix}"

    @property
    def chat_requests(self) -> list[dict[str, Any]]:
        with self.lock:
            return [r for r in self.requests if r["path"] == f"{self.prefix}/chat/completions"]

    def _forward(self, method: str, path: str, headers: dict[str, str],
                 body: bytes | None) -> tuple[int, bytes, list[tuple[str, str]]]:
        conn = http.client.HTTPConnection(self.target.hostname or "", self.target.port or 80, timeout=60)
        try:
            conn.request(method, self.target.path.rstrip("/") + path,
                         body=body, headers={k: v for k, v in headers.items() if k not in HOP_HEADERS})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data, [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_HEADERS]
        finally:
            conn.close()

    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length > 0 else None
        headers = {k.lower(): v for k, v in h.headers.items()}
        path = h.path.split("?", 1)[0]
        with self.lock:
            self.requests.append({"method": method, "path": path, "headers": headers})
        if self.redirect_to is not None:
            return self._send(h, 307, b"", [("Location", self.redirect_to)])
        if method == "POST" and path == f"{self.prefix}/chat/completions":
            if self.fail_status is not None:
                return self._send(h, self.fail_status, b'{"error": {"message": "unavailable"}}', [])
            return self._send(h, *self._forward("POST", "/chat/completions", headers, body))
        if method == "GET" and path == f"{self.prefix}/models":
            status, data, reply_headers = self._forward("GET", "/models", headers, None)
            if self.strip_fake and status == 200:
                listing = json.loads(data)
                data = json.dumps({"object": "list", "data": [{"id": e["id"], "object": "model"}
                                                              for e in listing.get("data", [])]}).encode()
                reply_headers = [("Content-Type", "application/json")]
            return self._send(h, status, data, reply_headers)
        return self._send(h, 404, b'{"error": {"message": "unknown route"}}', [])

    def _send(self, h: BaseHTTPRequestHandler, status: int, data: bytes, headers: list[tuple[str, str]]) -> None:
        h.send_response(status)
        for name, value in headers:
            if self.strip_fake and name.lower() == FAKE_HEADER:
                continue
            h.send_header(name, value)
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def start(self) -> "PrefixFront":
        front = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's signature
                pass

            def do_GET(self) -> None:
                front._handle(self, "GET")

            def do_POST(self) -> None:
                front._handle(self, "POST")

        self.httpd = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()

    def __enter__(self) -> "PrefixFront":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
