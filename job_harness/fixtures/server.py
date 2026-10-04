"""Serve the bundled HTML form fixtures over HTTP.

Tests and end-to-end runs use this instead of file:// so the whole stack --
discovery over HTTP, navigation, form submission -- exercises the real paths.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

# The fixtures carry a fixed datePosted. Rewriting it on the way out keeps every
# fixture-based test independent of the wall clock: a posting checked in last
# year would otherwise start failing the freshness filter.
_DATE_POSTED = re.compile(rb'("datePosted"\s*:\s*")[^"]*(")')


def _fresh_date(days_ago: int = 2) -> bytes:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)) \
        .date().isoformat().encode()

FIXTURE_DIR = Path(__file__).resolve().parent / "forms"

BOARD_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>Fixture Job Board</title></head><body>
<h1>Open roles</h1><ul>{items}</ul></body></html>"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                              # noqa: N802
        name = self.path.lstrip("/").split("?")[0]
        if name.endswith(".html") and "/" not in name:
            source = FIXTURE_DIR / name
            if source.is_file():
                body = _DATE_POSTED.sub(rb"\g<1>" + _fresh_date() + rb"\g<2>",
                                        source.read_bytes())
                return self._send_html(body)
        if self.path in ("/", "/index.html", "/board"):
            items = "".join(
                f'<li><a href="/{p.name}">{p.stem}</a></li>'
                for p in sorted(FIXTURE_DIR.glob("*.html"))
            )
            return self._send_html(BOARD_TEMPLATE.format(items=items).encode())
        super().do_GET()


class FixtureServer:
    def __init__(self, directory: Optional[Path] = None, port: int = 0) -> None:
        self.directory = str(directory or FIXTURE_DIR)
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", port), partial(QuietHandler, directory=self.directory))
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url_for(self, name: str) -> str:
        return f"{self.base_url}/{name}"

    def start(self) -> "FixtureServer":
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="fixtures", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "FixtureServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
