"""The aiohttp application: routes, live updates, health, metrics, static UI, and ``run_server``.

``create_app(rt, settings)`` wires the route modules (grouped as in docs/mycelic/API.md) under ``/api``, the event
hub that turns the outbox into Server-Sent Events, the three unauthenticated operational endpoints (``/healthz``,
``/readyz``, ``/metrics``) and, last, the static catch-all for the React build. Startup starts the runtime (transport,
embedded holders, the in-process worker when enabled) and the hub; cleanup stops them in reverse.

``/readyz`` answers 503 until the coordination DB answers, the transport reports, no migration is pending and,
when this process is expected to run the worker, a worker heartbeat is at most 60 s old: a load balancer then
keeps traffic away from a process that could accept a goal but never tick it. ``/metrics`` renders the registry in
``mycelic.observability``; the database-derived families are computed by collectors at scrape time so the numbers
are live without a background task.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time
import warnings
from pathlib import Path
from typing import Any

from aiohttp import web

# Handlers reach the runtime as ``request.app["rt"]`` (the contract in mycelic/runtime.py); aiohttp's typed AppKey
# recommendation does not apply to a string-keyed contract, so its advisory warning is silenced for this package.
warnings.filterwarnings("ignore", category=web.NotAppKeyWarning)

from .. import __version__
from .. import observability as obs
from ..db.migrate import migration_status
from ..util import now_iso, parse_iso, utcnow
from . import routes_admin, routes_auth, routes_chats, routes_goals, routes_knowledge, routes_org, routes_questions, routes_workspaces
from .middleware import LOOPBACK_HOSTS, ApiError, build_middleware, json_response, query_int, require_user
from .sse import EventHub, stream
from .static import setup_static

logger = logging.getLogger("mycelic.api")

WORKER_HEARTBEAT_MAX_AGE = 60.0
CLIENT_MAX_SIZE = 16 * 1024 * 1024


# ---------------------------------------------------------------------------------------------
# Health, readiness, metrics
# ---------------------------------------------------------------------------------------------


def _worker_heartbeat_age(rt: Any) -> float | None:
    hb = rt.db.scalar("SELECT MAX(last_heartbeat_at) FROM workers")
    t = parse_iso(hb) if hb else None
    return round((utcnow() - t).total_seconds(), 1) if t else None


async def readiness(app: web.Application) -> tuple[bool, dict[str, Any]]:
    rt, settings = app["rt"], app["settings"]
    db_ok = False
    try:
        db_ok = int(rt.db.scalar("SELECT 1", default=0)) == 1
    except Exception as exc:
        logger.warning("readiness: db check failed: %s", exc)
    transport: Any = "none"
    if rt.transport is not None:
        try:
            await rt.transport.stats()
            transport = getattr(rt.transport, "name", None) or settings.transport
        except Exception as exc:
            logger.warning("readiness: transport check failed: %s", exc)
            transport = False
    pending = 0
    try:
        pending = len(migration_status(rt.db.conn)["pending"])
    except Exception as exc:
        logger.warning("readiness: migration status failed: %s", exc)
        pending = -1
    age = _worker_heartbeat_age(rt) if db_ok else None
    worker_expected = bool(app.get("run_worker"))
    worker_ok = (not worker_expected) or (age is not None and age <= WORKER_HEARTBEAT_MAX_AGE)
    ok = db_ok and transport is not False and pending == 0 and worker_ok
    return ok, {"ok": ok, "db": db_ok, "transport": transport, "worker_heartbeat_age_seconds": age, "worker_expected": worker_expected,
                "migrations_pending": pending, "version": __version__, "at": now_iso()}


def register_collectors(rt: Any, registry: Any) -> Any:
    """Gauges and the cumulative counters that live in the database, refreshed at scrape time."""

    def collect(m: Any) -> None:
        counts = rt.jobs.counts()
        m.set("mycelic_jobs_backlog", counts.get("queued", 0) + counts.get("leased", 0))
        m.set("mycelic_jobs_dead", counts.get("dead", 0))
        by_status = {r["status"]: int(r["n"]) for r in rt.db.all("SELECT status, COUNT(*) AS n FROM claims GROUP BY status")}
        for s in ("hypothesis", "supported", "contested", "stale", "retracted"):
            m.set("mycelic_claims", by_status.get(s, 0), status=s)
        stale = 0
        for t in rt.org.list_tenants():
            try:
                stale += int(rt.knowledge.evidence_freshness(t["tenant_id"]).get("stale", 0))
            except Exception:
                logger.debug("freshness failed for %s", t["tenant_id"], exc_info=True)
        m.set("mycelic_evidence_stale", stale)
        age = _worker_heartbeat_age(rt)
        m.set("mycelic_worker_heartbeat_age_seconds", age if age is not None else -1)
        outcomes = {r["status"]: int(r["n"]) for r in rt.db.all("SELECT status, COUNT(*) AS n FROM questions WHERE status IN "
                                                                 "('committed','retained_uncertain','expired','failed','cancelled') GROUP BY status")}
        for o in ("committed", "retained_uncertain", "expired", "failed", "cancelled"):
            m.set("mycelic_questions_total", outcomes.get(o, 0), outcome=o)
        m.set("mycelic_routes_failed_total", int(rt.db.scalar("SELECT COUNT(*) FROM question_routes WHERE status IN ('failed','timeout')", default=0)))
        for r in rt.db.all("SELECT provider, model, tier, ok, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost FROM model_usage GROUP BY provider, model, tier, ok"):
            m.set("mycelic_model_calls_total", int(r["n"]), provider=r["provider"], model=r["model"], tier=r["tier"], ok="true" if r["ok"] else "false")
        for r in rt.db.all("SELECT provider, model, tier, COALESCE(SUM(cost_usd), 0) AS cost FROM model_usage GROUP BY provider, model, tier"):
            m.set("mycelic_model_cost_usd_total", float(r["cost"]), provider=r["provider"], model=r["model"], tier=r["tier"])

    registry.add_collector(collect)
    return collect


# ---------------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------------


def create_app(rt: Any, settings: Any, *, run_worker: bool | None = None, run_holders: bool | None = None, allowed_hosts: list[str] | None = None,
               cors_origins: list[str] | None = None, dist_dir: Path | str | None = None, metrics: Any | None = None,
               reporter: Any | None = None) -> web.Application:
    registry = metrics if metrics is not None else obs.metrics
    reporter = reporter if reporter is not None else obs.ErrorReporter(rt.db, settings)
    app = web.Application(client_max_size=CLIENT_MAX_SIZE)
    app["rt"] = rt
    app["settings"] = settings
    app["metrics"] = registry
    app["reporter"] = reporter
    app["run_worker"] = settings.run_worker_in_api if run_worker is None else bool(run_worker)
    app["run_holders"] = settings.embedded_holders if run_holders is None else bool(run_holders)
    app["started_monotonic"] = time.monotonic()
    app["started_at"] = now_iso()
    app["synthesis_cache"] = {}
    app["hub"] = EventHub(rt.db, rt.authz)
    app.middlewares.append(build_middleware(rt, settings, allowed_hosts=allowed_hosts, cors_origins=list(cors_origins if cors_origins is not None else settings.cors_origins),
                                            metrics=registry, reporter=reporter))

    for module in (routes_auth, routes_org, routes_chats, routes_goals, routes_questions, routes_knowledge, routes_workspaces, routes_admin):
        module.setup(app, "/api")

    async def events_stream(request: web.Request) -> web.StreamResponse:
        p = require_user(request)
        since_raw = request.query.get("since") or request.headers.get("Last-Event-ID")
        since = None
        if since_raw:
            try:
                since = max(0, int(since_raw))
            except ValueError:
                raise ApiError(400, "'since' must be an integer event id") from None
        auth = request.get("auth") or {}
        token = auth.get("token") if auth.get("kind") in ("session", "api_key") else None

        async def refresh() -> Any:
            """Re-resolve the viewer from their credential; None ends the stream (revoked session, disabled user)."""
            try:
                if auth.get("kind") == "session":
                    sess = rt.auth.resolve_session(token)
                    if sess is None:
                        return None
                    fresh = rt.authz.principal_for_user(sess["user_id"], session_kind=sess.get("kind") or "web")
                    return fresh if fresh is not None and fresh.tenant_id == p.tenant_id else None
                return rt.authz.principal_for_user(p.id)
            except Exception:
                logger.exception("principal refresh failed")
                return None

        return await stream(request, app["hub"], p, since=since, refresh=refresh)

    async def healthz(request: web.Request) -> web.Response:
        return json_response({"ok": True, "service": settings.service_name or "api", "version": __version__, "at": now_iso()})

    async def readyz(request: web.Request) -> web.Response:
        ok, body = await readiness(app)
        return json_response(body, 200 if ok else 503)

    async def metrics_handler(request: web.Request) -> web.Response:
        return web.Response(body=registry.render_prometheus().encode("utf-8"), headers={"Content-Type": obs.PROMETHEUS_CONTENT_TYPE, "Cache-Control": "no-store"})

    async def api_root(request: web.Request) -> web.Response:
        return json_response({"service": "mycelic", "version": __version__, "docs": "docs/mycelic/API.md"})

    app.router.add_get("/api/events/stream", events_stream)
    app.router.add_get("/api", api_root)
    app.router.add_get("/api/", api_root)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", readyz)
    app.router.add_get("/metrics", metrics_handler)
    setup_static(app, dist_dir)

    collector = register_collectors(rt, registry)
    app["collector"] = collector

    async def on_startup(_app: web.Application) -> None:
        if app["run_holders"] and rt.holders is None and rt.transport is not None and settings.embedded_holders:
            try:
                from ..holder.embedded import EmbeddedHolders
                rt.holders = EmbeddedHolders(settings, rt.db, rt.org, rt.transport, router=rt.router, embedder=rt.embedder)
            except Exception as exc:
                logger.warning("embedded holders unavailable: %s", exc)
        await rt.start(run_worker=app["run_worker"], run_holders=app["run_holders"])
        await app["hub"].start()
        logger.info("mycelic api %s ready (worker in process: %s, embedded holders: %s)", __version__, app["run_worker"], app["run_holders"])

    async def on_shutdown(_app: web.Application) -> None:
        # runs before aiohttp waits for in-flight handlers: closing the hub ends every open SSE stream at once
        with contextlib.suppress(Exception):
            await app["hub"].stop()

    async def on_cleanup(_app: web.Application) -> None:
        with contextlib.suppress(Exception):
            registry.remove_collector(collector)
        with contextlib.suppress(Exception):
            await reporter.close()
        with contextlib.suppress(Exception):
            await rt.stop()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


def default_allowed_hosts(settings: Any) -> list[str] | None:
    """Explicit ``MYCELIC_ALLOWED_HOSTS`` wins; a loopback bind accepts loopback Host headers only; otherwise any."""
    if settings.allowed_hosts:
        return list(settings.allowed_hosts)
    if settings.host in LOOPBACK_HOSTS - {"0.0.0.0"}:
        return sorted(LOOPBACK_HOSTS - {"0.0.0.0"})
    return None


async def run_server(rt: Any, settings: Any, *, run_worker: bool | None = None, run_holders: bool | None = None, stop: asyncio.Event | None = None,
                     dist_dir: Path | str | None = None) -> dict[str, Any]:
    """Serve until SIGTERM/SIGINT (or ``stop`` is set), then shut down cleanly. Returns a small summary."""
    app = create_app(rt, settings, run_worker=run_worker, run_holders=run_holders, allowed_hosts=default_allowed_hosts(settings),
                     cors_origins=settings.cors_origins, dist_dir=dist_dir)
    own_stop = stop is None
    stop = stop or asyncio.Event()
    if own_stop:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):  # Windows / non-main thread
                pass
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, settings.host, int(settings.port), reuse_address=True)
    await site.start()
    logger.info("listening on http://%s:%d/ (public url %s)", settings.host, int(settings.port), settings.public_url or "-")
    started = time.monotonic()
    try:
        await stop.wait()
    finally:
        logger.info("shutting down")
        await runner.cleanup()
    return {"host": settings.host, "port": int(settings.port), "uptime_seconds": round(time.monotonic() - started, 1)}


__all__ = ["create_app", "run_server", "readiness", "register_collectors", "default_allowed_hosts"]
