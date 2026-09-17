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


def create_app(cm: ChatMemory, *, mcp_token: str | None = None, api_token: str | None = None,
               allowed_origins: list[str] | None = None, cors: bool = True) -> web.Application:
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app["cm"] = cm
    mcp = StreamableHTTPTransport(cm, token=mcp_token, allowed_origins=allowed_origins)

    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.method == "OPTIONS" and cors:
            return web.Response(status=204, headers=_cors_headers(request))
        if api_token and request.path.startswith("/api/"):
            auth = request.headers.get("Authorization", "")
            if not (auth.startswith("Bearer ") and auth[7:].strip() == api_token):
                return _bad("unauthorized", 401)
        resp = await handler(request)
        if cors:
            for k, v in _cors_headers(request).items():
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
        try:
            body = await request.json()
        except Exception:
            return _bad("invalid JSON body")
        for f in ("chat_id", "speaker", "text"):
            if not isinstance(body.get(f), str) or not body[f].strip():
                return _bad(f"'{f}' is required")
        msg = await cm.add_message(body["chat_id"], body["speaker"], body["text"], role=body.get("role", "") or "",
                                   sent_at=body.get("sent_at"), message_id=body.get("message_id"), metadata=body.get("metadata"))
        return _json({"queued": True, "message": msg.to_dict()}, 202)

    async def add_batch(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _bad("invalid JSON body")
        chat_id = body.get("chat_id")
        items = body.get("messages")
        if not isinstance(chat_id, str) or not chat_id or not isinstance(items, list):
            return _bad("'chat_id' and 'messages' are required")
        msgs = await cm.add_messages(chat_id, items)
        return _json({"queued": len(msgs), "message_ids": [m.message_id for m in msgs]}, 202)

    async def remember(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _bad("invalid JSON body")
        if not isinstance(body.get("text"), str) or not body["text"].strip():
            return _bad("'text' is required")
        mem = await cm.remember(body["text"], subject=body.get("subject") or "user", kind=body.get("kind") or "fact",
                                importance=float(body.get("importance", 0.8)), chat_id=body.get("chat_id") or "manual",
                                when=body.get("when"))
        return _json({"memory": mem.to_dict()}, 201)

    async def forget(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _bad("invalid JSON body")
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


def _cors_headers(request: web.Request) -> dict[str, str]:
    origin = request.headers.get("Origin", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Accept",
        "Access-Control-Expose-Headers": "Mcp-Session-Id, MCP-Protocol-Version",
        "Vary": "Origin",
    }


async def run_server(cm: ChatMemory, *, host: str = "127.0.0.1", port: int = 8765, mcp_token: str | None = None,
                     api_token: str | None = None, allowed_origins: list[str] | None = None) -> web.AppRunner:
    """Start the HTTP server (returns the runner; call ``await runner.cleanup()`` to stop)."""
    app = create_app(cm, mcp_token=mcp_token, api_token=api_token, allowed_origins=allowed_origins)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("dashboard at http://%s:%d/  MCP at http://%s:%d/mcp", host, port, host, port)
    return runner
