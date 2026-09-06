"""Serve the bundled HTML form fixtures over HTTP.

Tests and end-to-end runs use this instead of file:// so the whole stack --
discovery over HTTP, navigation, form submission -- exercises the real paths.
"""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "forms"

BOARD_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>Fixture Job Board</title></head><body>
<h1>Open roles</h1><ul>{items}</ul></body></html>"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:                              # noqa: N802
        if self.path in ("/", "/index.html", "/board"):
            items = "".join(
                f'<li><a href="/{p.name}">{p.stem}</a></li>'
                for p in sorted(FIXTURE_DIR.glob("*.html"))
            )
            body = BOARD_TEMPLATE.format(items=items).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
