"""Local control dashboard.

Standard-library HTTP server (no extra dependency) with bearer/token auth.
Binding anything other than loopback without a token is refused by config
validation, so the control surface is never exposed unauthenticated.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from ..config.logging_setup import get_logger
from ..config.settings import Config, DashboardConfig
from ..database.db import Database
from . import stats

log = get_logger("dashboard")

INDEX_HTML = Path(__file__).with_name("index.html")

CONTROL_ACTIONS = {"start": "RUNNING", "resume": "RUNNING",
                   "pause": "PAUSED", "stop": "STOPPED"}


class DashboardState:
    """Shared between the HTTP server and the campaign thread."""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.lock = threading.Lock()
        self.on_control: Optional[Callable[[str], None]] = None

    def snapshot(self) -> dict[str, Any]:
        data = stats.collect(self.db)
        data["config"] = {
            "mode": self.config.run.mode,
            "max_applications": self.config.run.max_applications,
            "max_applications_per_hour": self.config.run.max_applications_per_hour,
            "max_applications_per_company": self.config.run.max_applications_per_company,
            "min_score": self.config.run.min_score,
            "freshness_days": self.config.discovery.freshness_days,
            "remote_only": self.config.discovery.remote_only,
            "target_roles": self.config.discovery.target_roles,
            "allow_senior_roles": self.config.scoring.allow_senior_roles,
        }
        return data

    def apply_control(self, action: str) -> dict[str, Any]:
        state = CONTROL_ACTIONS.get(action)
        if state is None:
            return {"ok": False, "error": f"unknown action: {action}"}
        with self.lock:
            self.db.set_control("harness_state", state)
        if self.on_control:
            try:
                self.on_control(action)
            except Exception as exc:
                log.warning("control callback failed", extra={"error": str(exc)[:200]})
        log.info("control action", extra={"action": action, "state": state})
        return {"ok": True, "state": state}

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply the settings the dashboard is allowed to change at runtime."""
        applied: dict[str, Any] = {}
        run, discovery, scoring = self.config.run, self.config.discovery, self.config.scoring
        numeric = {
            "max_applications": (run, "max_applications", int, 0, 100000),
            "max_applications_per_hour": (run, "max_applications_per_hour", int, 0, 1000),
            "max_applications_per_company": (run, "max_applications_per_company", int, 0, 100),
            "min_score": (run, "min_score", int, 0, 100),
            "freshness_days": (discovery, "freshness_days", int, 0, 365),
        }
        for key, (target, attr, cast, low, high) in numeric.items():
            if key in payload:
                try:
                    value = cast(payload[key])
                except (TypeError, ValueError):
                    continue
                value = max(low, min(high, value))
                setattr(target, attr, value)
                applied[key] = value
        if "remote_only" in payload:
            discovery.remote_only = bool(payload["remote_only"])
            applied["remote_only"] = discovery.remote_only
        if "allow_senior_roles" in payload:
            scoring.allow_senior_roles = bool(payload["allow_senior_roles"])
            applied["allow_senior_roles"] = scoring.allow_senior_roles
        if "target_roles" in payload:
            roles = payload["target_roles"]
            if isinstance(roles, str):
                roles = [r.strip() for r in roles.split(",") if r.strip()]
            if isinstance(roles, list) and roles:
                discovery.target_roles = [str(r)[:120] for r in roles][:100]
                applied["target_roles"] = discovery.target_roles
        # min_score changes what gets queued next; scoring threshold follows it.
        if "min_score" in applied:
            scoring.apply_threshold = applied["min_score"]
        log.info("config updated from dashboard", extra={"applied": list(applied)})
        return {"ok": True, "applied": applied}


def make_handler(state: DashboardState, token: str) -> type[BaseHTTPRequestHandler]:

    class Handler(BaseHTTPRequestHandler):
        server_version = "JobHarness/1.0"
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------ auth

        def _authorized(self, query: dict[str, list[str]]) -> bool:
            if not token:
                return True                      # loopback-only, validated in config
            header = self.headers.get("Authorization", "")
            supplied = ""
            if header.startswith("Bearer "):
                supplied = header[7:].strip()
            elif query.get("token"):
                supplied = query["token"][0]
            elif self.headers.get("X-Auth-Token"):
                supplied = self.headers["X-Auth-Token"].strip()
            elif "harness_token=" in (self.headers.get("Cookie") or ""):
                cookie = self.headers["Cookie"]
                supplied = cookie.split("harness_token=", 1)[1].split(";")[0].strip()
            return bool(supplied) and hmac.compare_digest(supplied, token)

        # --------------------------------------------------------- helpers

        def _send(self, code: int, body: bytes, content_type: str,
                  extra_headers: Optional[dict[str, str]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: Any) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

        def _deny(self) -> None:
            self._send(401, b'{"error":"unauthorized"}', "application/json",
                       {"WWW-Authenticate": 'Bearer realm="job-harness"'})

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("http", extra={"client": self.address_string(),
                                     "message": fmt % args})

        # ----------------------------------------------------------- routes

        def do_GET(self) -> None:                      # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/healthz":
                return self._json(200, {"ok": True})
            if not self._authorized(query):
                return self._deny()

            if parsed.path in ("/", "/index.html"):
                try:
                    html = INDEX_HTML.read_bytes()
                except OSError:
                    return self._json(500, {"error": "dashboard template missing"})
                headers = {}
                if token and query.get("token"):
                    headers["Set-Cookie"] = (
                        f"harness_token={query['token'][0]}; Path=/; SameSite=Strict; HttpOnly")
                return self._send(200, html, "text/html; charset=utf-8", headers)

            if parsed.path == "/api/status":
                return self._json(200, state.snapshot())

            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:                     # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if not self._authorized(query):
                return self._deny()

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                return self._json(400, {"error": "invalid JSON body"})

            if parsed.path == "/api/control":
                action = str(payload.get("action", "")).lower()
                result = state.apply_control(action)
                return self._json(200 if result.get("ok") else 400, result)

            if parsed.path == "/api/config":
                return self._json(200, state.update_config(payload))

            return self._json(404, {"error": "not found"})

    return Handler


class DashboardServer:
    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.state = DashboardState(config, db)
        self.token = config.dashboard.auth_token
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        host = self.config.dashboard.host
        display = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        base = f"http://{display}:{self.config.dashboard.port}/"
        return f"{base}?token={self.token}" if self.token else base

    def start(self) -> "DashboardServer":
        cfg: DashboardConfig = self.config.dashboard
        if not self.token and cfg.host not in ("127.0.0.1", "localhost", "::1"):
            # Belt and braces: config validation already refuses this.
            self.token = secrets.token_urlsafe(24)
            log.warning("generated a dashboard token for a non-loopback bind",
                        extra={"token": self.token})
        handler = make_handler(self.state, self.token)
        self._server = ThreadingHTTPServer((cfg.host, cfg.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="dashboard", daemon=True)
        self._thread.start()
        log.info("dashboard listening", extra={"url": self.url, "authenticated": bool(self.token)})
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        log.info("dashboard stopped")

    def __enter__(self) -> "DashboardServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
