"""Standalone evidence-holder process: ``python -m mycelic holder --holder-id ... --key ... --core-url ...``.

The process owns one evidence store on the machine where the evidence lives. It authenticates to the core with the
holder key (``Authorization: Bearer <key>``), fetches its bootstrap (tenant, route key, policy), joins the transport
and runs a :class:`~mycelic.holder.service.HolderService` until SIGTERM. Every 20 s it posts a heartbeat with its
stats; the reply may carry an updated export policy, which is applied immediately. An optional local HTTP API on
``--local-port`` lets the owner ingest and search without going through the core.

Bootstrap contract the core API serves (``GET /api/holders/{holder_id}/bootstrap``, bearer = holder key)::

    {
      "holder_id": "hold_...", "tenant_id": "ten_...", "name": "Ana's evidence", "mode": "external",
      "route_key": "<HMAC key that signs core -> holder envelopes>",
      "export_policy": {"disclosure": "excerpt", "max_excerpt_chars": 480, "answer_scopes": ["unit", "org"], "deny_patterns": []},
      "domains": ["ops", "support"],
      "heartbeat_seconds": 20,
      "transport": {                      # optional; overrides the process settings when present
        "kind": "sqlite" | "nats",
        "coord_db": "/shared/coord.db",   # sqlite: the shared coordination DB (MYCELIC_COORD_DB)
        "nats_url": "nats://...", "nats_user": "...", "nats_password": "...", "nats_stream": "MYCELIC"
      }
    }

Heartbeat (``POST /api/holders/{holder_id}/heartbeat``, same bearer)::

    request  {"stats": {...}, "status": "online"}
    response {"ok": true, "export_policy": {...}?, "domains": [...]?}   # policy fields are optional hot-reloads

Local API (bearer = holder key): ``POST /documents`` {title, text, kind?, observed_at?, domains?, origin_id?} ->
{document}; ``GET /documents``; ``GET /search?q=&k=`` -> {results}; ``GET /stats``;
``POST /documents/{doc_id}/revise`` {text, title?, observed_at?, reason?}; ``POST /documents/{doc_id}/retract`` {reason}.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hmac
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from ..evidence.service import EvidenceStore
from ..transport.base import Transport, TransportError, build_transport
from ..util import now_iso
from .service import HolderService

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_SECONDS = 20.0


class CoreClient:
    """Thin HTTP client for the two holder endpoints of the core API."""

    def __init__(self, core_url: str, holder_id: str, key: str, *, timeout: float = 15.0) -> None:
        import aiohttp

        self.core_url = core_url.rstrip("/")
        self.holder_id = holder_id
        self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                                              timeout=aiohttp.ClientTimeout(total=timeout))

    async def bootstrap(self) -> dict[str, Any]:
        async with self._session.get(f"{self.core_url}/api/holders/{self.holder_id}/bootstrap") as resp:
            if resp.status != 200:
                raise RuntimeError(f"bootstrap failed: HTTP {resp.status} {await resp.text()}")
            data = await resp.json()
        for key in ("tenant_id", "route_key"):
            if not data.get(key):
                raise RuntimeError(f"bootstrap response lacks {key!r}")
        return data

    async def heartbeat(self, stats: dict[str, Any], *, status: str = "online") -> dict[str, Any]:
        async with self._session.post(f"{self.core_url}/api/holders/{self.holder_id}/heartbeat", json={"stats": stats, "status": status}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"heartbeat failed: HTTP {resp.status}")
            try:
                return await resp.json()
            except Exception:
                return {}

    async def close(self) -> None:
        await self._session.close()


# ---------------------------------------------------------------------------------------------
# Local owner API
# ---------------------------------------------------------------------------------------------


def build_local_app(store: EvidenceStore, key: str) -> Any:
    """aiohttp application for the owner's local access; every request needs ``Authorization: Bearer <holder key>``."""
    from aiohttp import web

    async def auth_middleware(request: web.Request, handler):
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if not token or not hmac.compare_digest(token, key):
            return web.json_response({"error": "unauthorized", "code": "unauthorized"}, status=401)
        try:
            return await handler(request)
        except KeyError as exc:
            return web.json_response({"error": f"not found: {exc}", "code": "not_found"}, status=404)
        except ValueError as exc:
            return web.json_response({"error": str(exc), "code": "validation"}, status=400)

    async def body(request: web.Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            raise ValueError("body must be a JSON object")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    async def post_document(request: web.Request) -> web.Response:
        p = await body(request)
        doc = await store.ingest_document(str(p.get("title") or ""), str(p.get("text") or ""), kind=str(p.get("kind") or "note"),
                                          observed_at=p.get("observed_at"), domains=p.get("domains"), origin_id=p.get("origin_id"),
                                          uploaded_by=p.get("uploaded_by") or "owner", doc_id=p.get("doc_id"))
        return web.json_response({"document": doc}, status=201)

    async def list_documents(request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", "100"))
        return web.json_response({"items": await store.list_documents(status=request.query.get("status") or None, limit=limit)})

    async def get_document(request: web.Request) -> web.Response:
        doc = await store.document(request.match_info["doc_id"])
        if doc is None:
            raise KeyError(request.match_info["doc_id"])
        return web.json_response({"document": doc, "text": await store.document_text(doc["doc_id"])})

    async def revise(request: web.Request) -> web.Response:
        p = await body(request)
        result = await store.revise_document(request.match_info["doc_id"], str(p.get("text") or ""), title=p.get("title"),
                                             observed_at=p.get("observed_at"), reason=str(p.get("reason") or ""), domains=p.get("domains"))
        return web.json_response(result)

    async def retract(request: web.Request) -> web.Response:
        p = await body(request)
        return web.json_response(await store.retract_document(request.match_info["doc_id"], str(p.get("reason") or "")))

    async def search(request: web.Request) -> web.Response:
        q = request.query.get("q", "")
        k = max(1, min(50, int(request.query.get("k", "10"))))
        return web.json_response({"results": await store.search(q, k=k) if q else []})

    async def stats(request: web.Request) -> web.Response:
        return web.json_response(await store.stats())

    app = web.Application(middlewares=[web.middleware(auth_middleware)])
    app.router.add_post("/documents", post_document)
    app.router.add_get("/documents", list_documents)
    app.router.add_get("/documents/{doc_id}", get_document)
    app.router.add_post("/documents/{doc_id}/revise", revise)
    app.router.add_post("/documents/{doc_id}/retract", retract)
    app.router.add_get("/search", search)
    app.router.add_get("/stats", stats)
    return app


# ---------------------------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------------------------


def _settings_with_transport(settings: Any, override: dict[str, Any] | None) -> Any:
    """Apply the bootstrap's transport block on a copy of the settings (``Settings.__post_init__`` derives
    ``coord_db`` itself, so attributes are set after construction rather than passed in)."""
    if not override:
        return settings
    s = copy.copy(settings)
    if override.get("kind"):
        s.transport = override["kind"]
    if override.get("coord_db"):
        s.coord_db = override["coord_db"]
    for k in ("nats_url", "nats_user", "nats_password", "nats_stream"):
        if override.get(k):
            setattr(s, k, override[k])
    return s


def _build_transport(settings: Any) -> tuple[Transport, Any]:
    if getattr(settings, "transport", "sqlite") == "sqlite":
        from ..db.coord import CoordDB

        path = Path(settings.coord_db)
        if not path.exists():
            raise TransportError(f"coordination DB {path} not found; the sqlite transport needs the core's coord.db on a shared volume "
                                 "(set MYCELIC_COORD_DB) or MYCELIC_TRANSPORT=nats")
        db = CoordDB(path, migrate=False)
        return build_transport(settings, db), db
    return build_transport(settings, None), None


async def run_holder(settings: Any, *, holder_id: str, key: str, core_url: str, data_dir: str | Path, local_port: int | None = None,
                     local_host: str = "127.0.0.1", stop: asyncio.Event | None = None, heartbeat_seconds: float | None = None,
                     transport: Transport | None = None, bootstrap: dict[str, Any] | None = None, core: Any | None = None,
                     llm: Any | None = None, router: Any | None = None) -> dict[str, Any]:
    """Run one holder until ``stop`` is set (or SIGTERM/SIGINT). ``transport``, ``bootstrap`` and ``core`` can be
    injected (tests, embedding in another process); otherwise they come from the core API and the settings."""
    core = core or CoreClient(core_url, holder_id, key)
    own_stop = stop is None
    stop = stop or asyncio.Event()
    if own_stop:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
    boot = bootstrap or await core.bootstrap()
    tenant_id = boot["tenant_id"]
    interval = float(heartbeat_seconds or boot.get("heartbeat_seconds") or DEFAULT_HEARTBEAT_SECONDS)
    db = None
    built_transport = transport is None
    if transport is None:
        transport, db = _build_transport(_settings_with_transport(settings, boot.get("transport")))
        await transport.start()
    store = EvidenceStore(Path(data_dir).expanduser() / holder_id / "evidence.db", holder_id=holder_id, tenant_id=tenant_id, llm=llm,
                          router=router, export_policy=boot.get("export_policy"), domains=boot.get("domains") or [])

    async def heartbeat(stats: dict[str, Any]) -> None:
        reply = await core.heartbeat(stats)
        if isinstance(reply, dict) and ("export_policy" in reply or "domains" in reply):
            store.update_policy(reply.get("export_policy"), reply.get("domains"))

    service = HolderService(store, transport, holder_id=holder_id, tenant_id=tenant_id, route_key=boot["route_key"],
                            heartbeat=heartbeat, heartbeat_interval=interval)
    runner = None
    summary: dict[str, Any] = {"holder_id": holder_id, "tenant_id": tenant_id, "started_at": now_iso(), "local_port": None}
    try:
        await service.start()
        if local_port:
            from aiohttp import web

            runner = web.AppRunner(build_local_app(store, key))
            await runner.setup()
            site = web.TCPSite(runner, local_host, int(local_port))
            await site.start()
            summary["local_port"] = int(local_port)
            logger.info("holder %s local API on http://%s:%d", holder_id, local_host, int(local_port))
        logger.info("holder %s running (tenant %s, transport %s)", holder_id, tenant_id, getattr(transport, "name", "?"))
        await stop.wait()
    finally:
        await service.stop()
        try:
            await core.heartbeat(await store.stats(), status="offline")
        except Exception:
            logger.debug("offline heartbeat failed", exc_info=True)
        if runner is not None:
            await runner.cleanup()
        await store.close()
        if built_transport:
            try:
                await transport.close()
            except Exception:
                logger.debug("transport close failed", exc_info=True)
        if db is not None:
            await db.close()
        await core.close()
        summary["stopped_at"] = now_iso()
        summary["counters"] = dict(service.counters)
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entry (the top-level ``python -m mycelic holder`` delegates here). Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="mycelic holder", description="Run a standalone Mycelic evidence holder.")
    parser.add_argument("--holder-id", default=os.environ.get("MYCELIC_HOLDER_ID", ""), help="holder id (env MYCELIC_HOLDER_ID)")
    parser.add_argument("--key", default=os.environ.get("MYCELIC_HOLDER_KEY", ""), help="holder key (env MYCELIC_HOLDER_KEY; never logged)")
    parser.add_argument("--core-url", default=os.environ.get("MYCELIC_CORE_URL", "http://127.0.0.1:8780"), help="core API base URL")
    parser.add_argument("--data-dir", default=os.environ.get("MYCELIC_HOLDER_DATA_DIR", ""), help="where <holder_id>/evidence.db lives")
    parser.add_argument("--local-port", type=int, default=int(os.environ.get("MYCELIC_HOLDER_LOCAL_PORT", "0") or 0),
                        help="optional local owner API port (0 = off)")
    parser.add_argument("--local-host", default=os.environ.get("MYCELIC_HOLDER_LOCAL_HOST", "127.0.0.1"))
    parser.add_argument("--heartbeat-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    if not args.holder_id or not args.key:
        parser.error("--holder-id and --key (or MYCELIC_HOLDER_ID / MYCELIC_HOLDER_KEY) are required")
    from ..config import load_settings

    settings = load_settings()
    data_dir = args.data_dir or str(Path(settings.holders_dir))
    try:
        from ..observability import configure_logging
        settings.service_name = f"holder:{args.holder_id}"
        configure_logging(settings)
    except Exception:  # observability module unavailable: plain logging
        logging.basicConfig(level=getattr(logging, str(settings.log_level).upper(), logging.INFO),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        asyncio.run(run_holder(settings, holder_id=args.holder_id, key=args.key, core_url=args.core_url, data_dir=data_dir,
                               local_port=args.local_port or None, local_host=args.local_host, heartbeat_seconds=args.heartbeat_seconds))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error("holder %s stopped with an error: %s", args.holder_id, exc)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
