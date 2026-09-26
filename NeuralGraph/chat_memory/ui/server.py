"""aiohttp application: live dashboard, JSON API, and the MCP Streamable HTTP endpoint.

Routes
------
GET  /                      dashboard (single self-contained HTML page, no external assets)
GET  /healthz               liveness
GET  /api/status            everything the dashboard shows (auto-polled)
GET  /api/search?q=..&k=..  hybrid search across chats
GET  /api/context?q=..      prompt-ready context block
GET  /api/memories          recent memories (?subject=&kind=&limit=)
GET  /api/entities          top entities (?limit=)
GET  /api/profile/{subject}
POST /api/messages          {"chat_id", "speaker", "text", "role"?, "sent_at"?, "message_id"?}  -> queued
POST /api/messages/batch    {"chat_id", "messages": [...]}
POST /api/remember          {"text", "subject"?, "kind"?, "importance"?, "when"?}
POST /api/forget            {"memory_id", "reason"?}
POST /api/maintain          run one maintenance pass now
*    /mcp                   MCP Streamable HTTP transport (see mcp_server.py)

The API is what a chat application calls after every turn ("all automatic"): POST the message, forget
about it; the worker learns in the background and the dashboard shows it happening.
"""
from __future__ import annotations

import hmac
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from ..mcp_server import StreamableHTTPTransport
from ..service import ChatMemory

logger = logging.getLogger(__name__)

_HTML_PATH = Path(__file__).with_name("dashboard.html")


def _json(data: Any, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, ensure_ascii=False, default=str), status=status,
                        content_type="application/json", headers={"Cache-Control": "no-store"})


def _bad(msg: str, status: int = 400) -> web.Response:
    return _json({"error": msg}, status)


async def _body_object(request: web.Request) -> tuple[dict[str, Any] | None, web.Response | None]:
    """Decode the JSON body; (dict, None) on success, (None, 400 response) otherwise."""
    try:
        body = await request.json()
    except Exception:
        return None, _bad("invalid JSON body")
    if not isinstance(body, dict):
        return None, _bad("JSON body must be an object")
    return body, None


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"})


def _host_allowed(request: web.Request, allowed_hosts: frozenset[str] | None) -> bool:
    if allowed_hosts is None:
        return True
    host = (request.headers.get("Host") or "").strip().lower()
    if host.startswith("["):  # [::1]:8765
        name = host.split("]")[0] + "]"
    else:
        name = host.split(":")[0]
    return name in allowed_hosts


def create_app(cm: ChatMemory, *, mcp_token: str | None = None, api_token: str | None = None,
               allowed_origins: list[str] | None = None, cors_origins: list[str] | None = None,
               allowed_hosts: list[str] | None = None) -> web.Application:
    """Build the aiohttp application.

    Security defaults: no CORS headers unless ``cors_origins`` lists the browser origins allowed to call the
    API (``"*"`` for any); the MCP endpoint additionally validates ``Origin`` against ``allowed_origins``;
    ``allowed_hosts`` (when given) rejects requests whose ``Host`` header is not in the list, which blocks DNS
    rebinding against a server bound to localhost (``run_server`` sets it automatically for loopback binds).
    """
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app["cm"] = cm
    mcp = StreamableHTTPTransport(cm, token=mcp_token, allowed_origins=allowed_origins)
    cors_set = {o.lower() for o in cors_origins} if cors_origins else set()
    hosts = frozenset(h.lower() for h in allowed_hosts) if allowed_hosts else None

    def origin_allowed(request: web.Request) -> bool:
        """Same-origin, no Origin (non-browser client), or an origin on the allow-list."""
        origin = request.headers.get("Origin")
        if not origin:
            return True
        o = origin.lower()
        if "*" in cors_set or o in cors_set:
            return True
        host = (request.headers.get("Host") or "").lower()
        return bool(host) and o.split("://", 1)[-1] == host

    def cors_for(request: web.Request) -> dict[str, str]:
        origin = request.headers.get("Origin")
        if not origin or not cors_set or not ("*" in cors_set or origin.lower() in cors_set):
            return {}
        return _cors_headers(origin)

    @web.middleware
    async def middleware(request: web.Request, handler):
        if not _host_allowed(request, hosts):
            return _bad("host not allowed", 421)
        if request.method == "OPTIONS":
            h = cors_for(request)
            return web.Response(status=204 if h else 403, headers=h)
        protected = request.path.startswith("/api/") or request.path == "/mcp"
        if protected and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # CSRF: a browser page on another origin cannot write, even with a text/plain body that skips preflight
            if not origin_allowed(request):
                return _bad("origin not allowed", 403)
            if request.can_read_body and not request.content_type.startswith("application/json"):
                return _bad("Content-Type must be application/json", 415)
        if api_token and request.path.startswith("/api/"):
            auth = request.headers.get("Authorization", "")
            if not (auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip().encode("utf-8"), api_token.encode("utf-8"))):
                return _bad("unauthorized", 401)
        resp = await handler(request)
        for k, v in cors_for(request).items():
            resp.headers.setdefault(k, v)
        return resp

    app.middlewares.append(middleware)

    async def index(request: web.Request) -> web.Response:
        return web.Response(text=_HTML_PATH.read_text(encoding="utf-8"), content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    async def healthz(request: web.Request) -> web.Response:
        return _json({"ok": True, "worker_running": cm.worker.running})

    async def status(request: web.Request) -> web.Response:
        return _json(await cm.status())

    async def search(request: web.Request) -> web.Response:
        q = (request.query.get("q") or "").strip()
        if not q:
            return _bad("q is required")
        try:
            k = max(1, min(50, int(request.query.get("k", "10"))))
        except ValueError:
            return _bad("k must be an integer")
        kinds = [x for x in (request.query.get("kinds") or "").split(",") if x] or None
        hits = await cm.search(q, k=k, subject=request.query.get("subject") or None, chat_id=request.query.get("chat_id") or None,
                               kinds=kinds, since=request.query.get("since") or None, until=request.query.get("until") or None,
                               include_superseded=request.query.get("history") == "1")
        return _json({"query": q, "results": [h.to_dict() for h in hits]})

    async def context(request: web.Request) -> web.Response:
        q = (request.query.get("q") or "").strip()
        if not q:
            return _bad("q is required")
        try:
            k = max(1, min(30, int(request.query.get("k", "10"))))
            max_chars = max(200, min(20000, int(request.query.get("max_chars", "2400"))))
        except ValueError:
            return _bad("k/max_chars must be integers")
        block = await cm.context_for(q, k=k, max_chars=max_chars, subject=request.query.get("subject") or None)
        return _json({"query": q, "context": block})

    async def memories(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(500, int(request.query.get("limit", "50"))))
        except ValueError:
            return _bad("limit must be an integer")
        kinds = [x for x in (request.query.get("kind") or "").split(",") if x] or None
        mems = await cm.store.list_memories(subject=request.query.get("subject") or None, kinds=kinds,
                                            chat_id=request.query.get("chat_id") or None,
                                            status=request.query.get("status") or "active", limit=limit,
                                            order="created_at DESC", with_sources=True)
        return _json({"memories": [m.to_dict() for m in mems]})

    async def entities(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(500, int(request.query.get("limit", "50"))))
        except ValueError:
            return _bad("limit must be an integer")
        ents = await cm.store.top_entities(limit)
        out = []
        for e in ents:
            d = e.to_dict()
            d["relations"] = [r.to_dict() for r in await cm.store.relations_for(e.entity_id, limit=20)]
            out.append(d)
        return _json({"entities": out})

    async def profile(request: web.Request) -> web.Response:
        return _json(await cm.profile(request.match_info["subject"]))

    async def add_message(request: web.Request) -> web.Response:
        body, err = await _body_object(request)
        if err is not None:
            return err
        for f in ("chat_id", "speaker", "text"):
            if not isinstance(body.get(f), str) or not body[f].strip():
                return _bad(f"'{f}' is required")
        for f in ("role", "sent_at", "message_id"):
            if body.get(f) is not None and not isinstance(body.get(f), str):
                return _bad(f"'{f}' must be a string")
        if body.get("metadata") is not None and not isinstance(body.get("metadata"), dict):
            return _bad("'metadata' must be an object")
        msg = await cm.add_message(body["chat_id"], body["speaker"], body["text"], role=body.get("role", "") or "",
                                   sent_at=body.get("sent_at"), message_id=body.get("message_id"), metadata=body.get("metadata"))
        return _json({"queued": True, "message": msg.to_dict()}, 202)

    async def add_batch(request: web.Request) -> web.Response:
        body, err = await _body_object(request)
        if err is not None:
            return err
        chat_id = body.get("chat_id")
        items = body.get("messages")
        if not isinstance(chat_id, str) or not chat_id or not isinstance(items, list):
            return _bad("'chat_id' and 'messages' are required")
        for i, it in enumerate(items):   # validate everything before inserting anything (all-or-nothing)
            if not isinstance(it, dict) or not isinstance(it.get("text"), str):
                return _bad(f"messages[{i}] must be an object with a 'text' string")
            for f in ("speaker", "role", "sent_at", "message_id"):
                if it.get(f) is not None and not isinstance(it.get(f), str):
                    return _bad(f"messages[{i}].{f} must be a string")
            if it.get("metadata") is not None and not isinstance(it.get("metadata"), dict):
                return _bad(f"messages[{i}].metadata must be an object")
        msgs = await cm.add_messages(chat_id, items)
        return _json({"queued": len(msgs), "message_ids": [m.message_id for m in msgs]}, 202)

    async def remember(request: web.Request) -> web.Response:
        body, err = await _body_object(request)
        if err is not None:
            return err
        if not isinstance(body.get("text"), str) or not body["text"].strip():
            return _bad("'text' is required")
        try:
            importance = float(body.get("importance") if body.get("importance") is not None else 0.8)
        except (TypeError, ValueError):
            return _bad("'importance' must be a number")
        for f in ("subject", "kind", "chat_id", "when"):
            if body.get(f) is not None and not isinstance(body.get(f), str):
                return _bad(f"'{f}' must be a string")
        try:
            mem = await cm.remember(body["text"], subject=body.get("subject") or "user", kind=body.get("kind") or "fact",
                                    importance=importance, chat_id=body.get("chat_id") or "manual", when=body.get("when"))
        except ValueError as exc:
            return _bad(str(exc))
        return _json({"memory": mem.to_dict()}, 201)

    async def forget(request: web.Request) -> web.Response:
        body, err = await _body_object(request)
        if err is not None:
            return err
        if not isinstance(body.get("memory_id"), str):
            return _bad("'memory_id' is required")
        ok = await cm.retract(body["memory_id"], body.get("reason") or "user request")
        return _json({"retracted": ok})

    async def maintain(request: web.Request) -> web.Response:
        return _json(await cm.maintain())

    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/context", context)
    app.router.add_get("/api/memories", memories)
    app.router.add_get("/api/entities", entities)
    app.router.add_get("/api/profile/{subject}", profile)
    app.router.add_post("/api/messages", add_message)
    app.router.add_post("/api/messages/batch", add_batch)
    app.router.add_post("/api/remember", remember)
    app.router.add_post("/api/forget", forget)
    app.router.add_post("/api/maintain", maintain)
    app.router.add_route("*", "/mcp", mcp.handle)
    app["mcp"] = mcp
    return app


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Accept",
        "Access-Control-Expose-Headers": "Mcp-Session-Id, MCP-Protocol-Version",
        "Vary": "Origin",
    }


async def run_server(cm: ChatMemory, *, host: str = "127.0.0.1", port: int = 8765, mcp_token: str | None = None,
                     api_token: str | None = None, allowed_origins: list[str] | None = None,
                     cors_origins: list[str] | None = None, allowed_hosts: list[str] | None = None) -> web.AppRunner:
    """Start the HTTP server (returns the runner; call ``await runner.cleanup()`` to stop).

    When bound to a loopback address and no ``allowed_hosts`` are given, only loopback Host headers are
    accepted (DNS-rebinding protection for the local dashboard/API).
    """
    if allowed_hosts is None and host in LOOPBACK_HOSTS - {"0.0.0.0"}:
        allowed_hosts = sorted(LOOPBACK_HOSTS - {"0.0.0.0"})
    app = create_app(cm, mcp_token=mcp_token, api_token=api_token, allowed_origins=allowed_origins,
                     cors_origins=cors_origins, allowed_hosts=allowed_hosts)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("dashboard at http://%s:%d/  MCP at http://%s:%d/mcp", host, port, host, port)
    return runner
