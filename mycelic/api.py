"""HTTP API (aiohttp).  Every route is thin: validation and authorization live in :mod:`mycelic.service`.

Routes and the scope each requires
----------------------------------
GET  /                         service index (no auth)
GET  /health                   liveness: 200 while the database is open (public: status only; details for admins)
GET  /ready                    readiness: database open, loops running, no replay in progress
GET  /metrics                  Prometheus text format (token: MYCELIC_METRICS_TOKEN, else admin token or agent key)
GET  /whoami                   the authenticated principal
POST /memory                   memory:write   store one memory (202 Accepted; aggregation is asynchronous)
GET  /memory/{id}              memory:read
POST /memory/{id}/retract      producer or admin
GET  /memories                 memory:read    ?scope=&layer=&limit=
POST /events                   events:write   {"events": [...]} (each may embed a "memory")
POST /query                    memory:read    {"query", "scope"?, "min_layer"?, "k"?, "include_lineage"?}
GET  /lineage/{id}             lineage:read
POST /admin/agents             admin          register an agent (the key is returned once)
GET  /admin/agents             admin
DELETE /admin/agents/{id}      admin          revoke
POST /admin/agents/{id}/rotate admin          new key (returned once)
GET/POST /admin/rules, DELETE /admin/rules/{id}   admin
POST /admin/replay             admin          re-deliver the whole event log to this instance
GET  /admin/status             admin          full health, settings (secrets masked), transport state
GET  /admin/audit, GET /admin/events            admin
*    /mcp                      MCP Streamable HTTP; identity = the Bearer agent key, same as the REST routes
"""
from __future__ import annotations

import json
import logging
import ssl
import time
from typing import Any, Awaitable, Callable

from aiohttp import web

from .auth import AuthError, Principal
from .hierarchy import HierarchyError
from .mcp import MycelicMCPTransport
from .service import Forbidden, MycelicService, NotFound, ValidationError

logger = logging.getLogger(__name__)

PUBLIC_PATHS = frozenset({"/", "/health", "/ready"})
_last_log: dict[tuple[str, str], float] = {}


def _json(data: Any, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, ensure_ascii=False, default=str), status=status,
                        content_type="application/json", headers={"Cache-Control": "no-store"})


def _error(message: str, status: int) -> web.Response:
    return _json({"error": message}, status)


def _suppressed_log(reason: str, remote: str, message: str) -> None:
    now = time.monotonic()
    key = (reason, remote)
    if now - _last_log.get(key, 0.0) > 60.0:
        _last_log[key] = now
        if len(_last_log) > 10_000:
            _last_log.clear()
        logger.warning("%s from %s: %s", reason, remote, message)


def _remote(request: web.Request, trust_proxy: bool) -> str:
    if trust_proxy:
        fwd = request.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote or "unknown"


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except web.HTTPException:
        raise                                        # 413 from client_max_size keeps its status
    except Exception as exc:
        raise ValidationError("invalid JSON body") from exc
    if not isinstance(body, dict):
        raise ValidationError("JSON body must be an object")
    return body


def _route_label(request: web.Request) -> str:
    info = request.match_info
    if info is None or info.route is None or info.route.resource is None:
        return "unmatched"
    return info.route.resource.canonical


def create_app(service: MycelicService) -> web.Application:
    s = service.settings
    app = web.Application(client_max_size=s.max_body_bytes)
    app["service"] = service
    mcp = MycelicMCPTransport(service, allowed_origins=s.cors_origins or None)
    allowed_hosts = frozenset(h.lower() for h in s.allowed_hosts) if s.allowed_hosts else None
    m = service.metrics

    def host_ok(request: web.Request) -> bool:
        if allowed_hosts is None:
            return True
        host = (request.headers.get("Host") or "").strip().lower()
        name = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
        return name in allowed_hosts

    @web.middleware
    async def middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        route = _route_label(request)
        remote = _remote(request, s.trust_proxy_headers)
        if not host_ok(request):
            m.http_requests.labels(route, "421").inc()
            return _error("host not allowed", 421)
        if request.method == "OPTIONS":
            return web.Response(status=204 if s.cors_origins else 403)
        # rate limit by peer before authentication (a bad token must never spend the claimed agent's budget)
        if not service.limiter.allow(f"ip:{remote}"):
            m.http_requests.labels(route, "429").inc()
            m.auth_failures.labels("rate_limited").inc()
            return _error("rate limit exceeded", 429)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.can_read_body \
                and not request.content_type.startswith("application/json"):
            m.http_requests.labels(route, "415").inc()
            return _error("Content-Type must be application/json", 415)
        principal: Principal | None = None
        if request.path not in PUBLIC_PATHS:
            auth = request.headers.get("Authorization")
            if request.path == "/metrics" and s.metrics_token:
                import hmac

                token = (auth or "")[7:].strip() if (auth or "").startswith("Bearer ") else ""
                if not hmac.compare_digest(token.encode("utf-8"), s.metrics_token.encode("utf-8")):
                    m.auth_failures.labels("metrics_token").inc()
                    return _error("unauthorized", 401)
            elif request.path == "/metrics" and s.host in ("127.0.0.1", "localhost", "::1") and not auth:
                pass                                   # loopback development: scrape without a token
            else:
                try:
                    principal = service.authenticate(auth)
                except AuthError as exc:
                    m.auth_failures.labels(exc.reason.replace(" ", "_")).inc()
                    m.http_requests.labels(route, str(exc.status)).inc()
                    _suppressed_log("auth failure", remote, exc.reason)
                    headers = {"WWW-Authenticate": 'Bearer realm="mycelic"'} if exc.status == 401 else {}
                    return _json({"error": exc.reason}, exc.status) if not headers else web.Response(
                        text=json.dumps({"error": exc.reason}), status=exc.status, content_type="application/json", headers=headers)
                if not service.limiter.allow(f"principal:{principal.kind}:{principal.id}"):
                    m.http_requests.labels(route, "429").inc()
                    m.auth_failures.labels("rate_limited").inc()
                    return _error("rate limit exceeded", 429)
        request["principal"] = principal
        request["remote"] = remote
        try:
            response = await handler(request)
        except ValidationError as exc:
            response = _error(str(exc), 400)
        except HierarchyError as exc:
            response = _error(str(exc), 400)
        except Forbidden as exc:
            response = _error(str(exc), 403)
        except NotFound as exc:
            response = _error(f"no visible memory with id {exc.args[0] if exc.args else ''}", 404)
        except web.HTTPException:
            raise
        except Exception as exc:
            logger.exception("unhandled error on %s %s", request.method, request.path)
            response = _error(f"internal error: {type(exc).__name__}", 500)
        m.http_requests.labels(route, str(response.status)).inc()
        return response

    app.middlewares.append(middleware)

    def principal(request: web.Request) -> Principal:
        p = request.get("principal")
        if p is None:
            raise AuthError(401, "missing bearer token")
        return p

    def admin(request: web.Request) -> Principal:
        p = principal(request)
        if not p.is_admin:
            raise Forbidden("administrator token required")
        return p

    # ---------------------------------------------------------------- public
    async def index(request: web.Request) -> web.Response:
        return _json({"service": "mycelic", "version": service.settings and __import__("mycelic.service", fromlist=["VERSION"]).VERSION,
                      "docs": "https://github.com/anovruzov/NeuralGraph/blob/main/DEPLOYMENT.md",
                      "endpoints": ["/health", "/ready", "/metrics", "/whoami", "/memory", "/memories", "/events", "/query",
                                    "/lineage/{id}", "/admin/*", "/mcp"]})

    async def health(request: web.Request) -> web.Response:
        h = await service.health()
        status = 503 if h["status"] == "failing" else 200
        auth = request.headers.get("Authorization")
        if auth:
            try:
                p = service.authenticate(auth)
            except AuthError:
                p = None
            if p is not None and p.is_admin:
                return _json(h, status)
        return _json({"status": h["status"], "version": h["version"],
                      "transport_connected": bool(h["checks"]["transport"].get("connected")),
                      "consumer_running": h["checks"]["consumer"]["running"]}, status)

    async def ready(request: web.Request) -> web.Response:
        ok, detail = await service.ready()
        return _json(detail, 200 if ok else 503)

    async def metrics(request: web.Request) -> web.Response:
        m.refresh_from_stats(service.store.stats())
        body, content_type = m.render()
        return web.Response(body=body, content_type=content_type.split(";")[0], charset="utf-8")

    async def whoami(request: web.Request) -> web.Response:
        return _json(principal(request).to_dict())

    # ---------------------------------------------------------------- memories
    async def post_memory(request: web.Request) -> web.Response:
        p = principal(request)
        mem, created = await service.ingest_memory(p, await _body(request), remote=request["remote"])
        return _json({"memory_id": mem.memory_id, "event_id": mem.event_id, "created": created, "scope": mem.scope,
                      "status": "accepted"}, 202 if created else 200)

    async def get_memory(request: web.Request) -> web.Response:
        p = principal(request)
        mem = service.get_memory(p, request.match_info["id"])
        return _json({"memory": service.public_view(mem, p)})

    async def retract_memory(request: web.Request) -> web.Response:
        p = principal(request)
        body = await _body(request) if request.can_read_body else {}
        ev = await service.retract(p, request.match_info["id"], str(body.get("reason") or "retracted by producer"), remote=request["remote"])
        return _json({"memory_id": request.match_info["id"], "event_id": ev.event_id, "status": "accepted"}, 202)

    async def list_memories(request: web.Request) -> web.Response:
        p = principal(request)
        try:
            limit = max(1, min(500, int(request.query.get("limit", "50"))))
        except ValueError:
            raise ValidationError("limit must be an integer")
        layers = [x for x in (request.query.get("layer") or "").split(",") if x] or None
        rows = service.list_memories(p, scope=request.query.get("scope") or None, layers=layers, limit=limit,
                                     status=request.query.get("status") or "active")
        return _json({"memories": [service.public_view(mm, p) for mm in rows]})

    async def post_events(request: web.Request) -> web.Response:
        p = principal(request)
        body = await _body(request)
        results = await service.ingest_events(p, body.get("events"), remote=request["remote"])
        return _json({"results": results, "status": "accepted"}, 202)

    async def post_query(request: web.Request) -> web.Response:
        p = principal(request)
        res = service.query(p, await _body(request))
        res["results"] = [{**h, "memory": service.public_view(h["memory"], p)} for h in res["results"]]
        return _json(res)

    async def get_lineage(request: web.Request) -> web.Response:
        return _json(service.lineage(principal(request), request.match_info["id"]))

    # ---------------------------------------------------------------- admin
    async def admin_register(request: web.Request) -> web.Response:
        admin(request)
        agent, key = await service.register_agent(await _body(request), remote=request["remote"])
        return _json({"agent": agent.to_dict(), "api_key": key, "note": "store the key now; it is not shown again"}, 201)

    async def admin_list_agents(request: web.Request) -> web.Response:
        admin(request)
        agents = await service.store.list_agents(request.query.get("org") or None, include_revoked=request.query.get("all") == "1")
        return _json({"agents": [a.to_dict() for a in agents]})

    async def admin_revoke(request: web.Request) -> web.Response:
        admin(request)
        ok = await service.revoke_agent(request.match_info["id"], remote=request["remote"])
        return _json({"agent_id": request.match_info["id"], "revoked": ok}, 200 if ok else 404)

    async def admin_rotate(request: web.Request) -> web.Response:
        admin(request)
        key = await service.rotate_agent_key(request.match_info["id"], remote=request["remote"])
        if key is None:
            return _error("no such agent", 404)
        return _json({"agent_id": request.match_info["id"], "api_key": key, "note": "store the key now; it is not shown again"})

    async def admin_rules(request: web.Request) -> web.Response:
        admin(request)
        return _json({"rules": [r.to_dict() for r in service.store.list_rules(enabled_only=False)]})

    async def admin_put_rule(request: web.Request) -> web.Response:
        admin(request)
        rule = await service.upsert_rule(await _body(request), remote=request["remote"])
        return _json({"rule": rule.to_dict()}, 201)

    async def admin_delete_rule(request: web.Request) -> web.Response:
        admin(request)
        ok = await service.delete_rule(request.match_info["id"], remote=request["remote"])
        return _json({"rule_id": request.match_info["id"], "deleted": ok}, 200 if ok else 404)

    async def admin_replay(request: web.Request) -> web.Response:
        admin(request)
        return _json(await service.replay(remote=request["remote"]), 202)

    async def admin_status(request: web.Request) -> web.Response:
        admin(request)
        h = await service.health()
        h["settings"] = service.settings.redacted()
        return _json(h)

    async def admin_audit(request: web.Request) -> web.Response:
        admin(request)
        try:
            limit = max(1, min(1000, int(request.query.get("limit", "100"))))
        except ValueError:
            raise ValidationError("limit must be an integer")
        return _json({"audit": service.store.recent_audit(limit)})

    async def admin_events(request: web.Request) -> web.Response:
        admin(request)
        org = request.query.get("org")
        if not org:
            raise ValidationError("org is required")
        try:
            limit = max(1, min(1000, int(request.query.get("limit", "100"))))
        except ValueError:
            raise ValidationError("limit must be an integer")
        rows = service.store.list_events(org, agent_id=request.query.get("agent") or None, kind=request.query.get("kind") or None,
                                         status=request.query.get("status") or None, limit=limit)
        return _json({"events": [e.to_dict() for e in rows]})

    r = app.router
    r.add_get("/", index)
    r.add_get("/health", health)
    r.add_get("/ready", ready)
    r.add_get("/metrics", metrics)
    r.add_get("/whoami", whoami)
    r.add_post("/memory", post_memory)
    r.add_get("/memory/{id}", get_memory)
    r.add_post("/memory/{id}/retract", retract_memory)
    r.add_delete("/memory/{id}", retract_memory)
    r.add_get("/memories", list_memories)
    r.add_post("/events", post_events)
    r.add_post("/query", post_query)
    r.add_get("/lineage/{id}", get_lineage)
    r.add_post("/admin/agents", admin_register)
    r.add_get("/admin/agents", admin_list_agents)
    r.add_delete("/admin/agents/{id}", admin_revoke)
    r.add_post("/admin/agents/{id}/rotate", admin_rotate)
    r.add_get("/admin/rules", admin_rules)
    r.add_post("/admin/rules", admin_put_rule)
    r.add_delete("/admin/rules/{id}", admin_delete_rule)
    r.add_post("/admin/replay", admin_replay)
    r.add_get("/admin/status", admin_status)
    r.add_get("/admin/audit", admin_audit)
    r.add_get("/admin/events", admin_events)
    r.add_route("*", "/mcp", mcp.handle)
    app["mcp"] = mcp
    return app


def ssl_context(service: MycelicService) -> ssl.SSLContext | None:
    s = service.settings
    if not s.tls_enabled:
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=s.tls_cert_file, keyfile=s.tls_key_file)  # type: ignore[arg-type]
    return ctx


async def run_server(service: MycelicService) -> web.AppRunner:
    """Bind the HTTP server (before the transport connects, so probes answer during a slow broker start)."""
    app = create_app(service)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, service.settings.host, service.settings.port, ssl_context=ssl_context(service))
    await site.start()
    scheme = "https" if service.settings.tls_enabled else "http"
    logger.info("mycelic API on %s://%s:%d  (MCP at /mcp)", scheme, service.settings.host, service.settings.port)
    return runner
