"""Everything a request passes through before and after its handler (docs/mycelic/API.md, DECISIONS D8, D9).

One middleware does, in order: the ``Host`` allow-list (DNS-rebinding protection for loopback binds), CORS preflight,
the CSRF ``Origin`` check on state-changing ``/api`` requests, credential resolution (session cookie, bearer session
token, ``mk_`` API key or holder key) into ``request["principal"]``, and the mapping of service exceptions to the
error contract (``{"error", "code"}`` with 400/401/403/404/409/429/5xx). A denied ``Forbidden`` is audited, an
unexpected exception is reported through :class:`mycelic.observability.ErrorReporter`, and every response carries
``X-Request-Id`` plus one structured log line with the latency; the same id is bound to the logging context so
every line written while handling the request can be correlated.

Handlers stay small because the helpers here do the repetitive parts: ``read_json`` (400 on a bad body),
``require_user`` / ``require_admin`` (401/403), ``listing`` (the ``{"items", "total"}`` shape), ``limit_of`` (the
``limit`` query parameter clamped to the documented maximum) and ``ApiError`` for any status a handler needs.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import web

from ..auth import AuthError
from ..authz import Forbidden, Principal
from ..inquiry import CooldownActive, DuplicateQuestion
from ..models.base import ModelError
from ..transport import TransportError
from ..util import j, parse_iso, utcnow
from .. import observability as obs

logger = logging.getLogger("mycelic.api")

SESSION_COOKIE = "mycelic_session"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"})
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_LIMIT, MAX_LIMIT = 50, 500
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{4,80}$")
_CODE_FOR_STATUS = {400: "validation", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                    409: "conflict", 413: "too_large", 415: "unsupported_media_type", 421: "host_not_allowed", 429: "budget",
                    500: "internal", 502: "upstream", 503: "unavailable", 504: "timeout"}


# ---------------------------------------------------------------------------------------------
# Responses and errors
# ---------------------------------------------------------------------------------------------


def json_response(data: Any, status: int = 200, *, headers: dict[str, str] | None = None) -> web.Response:
    h = {"Cache-Control": "no-store"}
    if headers:
        h.update(headers)
    return web.Response(text=j(data), status=status, content_type="application/json", headers=h)


class ApiError(Exception):
    """Raised by handlers (and the middleware) for any error the contract describes; ``extra`` lands in the body
    (``existing_question_id`` for a duplicate question, for example)."""

    def __init__(self, status: int, message: str, code: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message
        self.code = code or _CODE_FOR_STATUS.get(self.status, "error")
        self.extra = extra

    def response(self) -> web.Response:
        return json_response({"error": self.message, "code": self.code, **self.extra}, self.status)


def error_response(status: int, message: str, code: str | None = None, **extra: Any) -> web.Response:
    return ApiError(status, message, code, **extra).response()


async def read_json(request: web.Request, *, required: bool = True) -> dict[str, Any]:
    """The JSON object body, or 400. An empty body is ``{}`` when ``required`` is False."""
    if not request.can_read_body:
        if required:
            raise ApiError(400, "a JSON object body is required")
        return {}
    try:
        data = await request.json()
    except Exception:
        raise ApiError(400, "invalid JSON body") from None
    if data is None and not required:
        return {}
    if not isinstance(data, dict):
        raise ApiError(400, "JSON body must be an object")
    return data


def need_str(body: dict[str, Any], key: str, *, max_len: int = 4000, allow_empty: bool = False) -> str:
    v = body.get(key)
    if not isinstance(v, str) or (not v.strip() and not allow_empty):
        raise ApiError(400, f"'{key}' is required")
    if len(v) > max_len:
        raise ApiError(400, f"'{key}' is too long (max {max_len} characters)")
    return v.strip() if not allow_empty else v


def opt_str(body: dict[str, Any], key: str, *, max_len: int = 4000) -> str | None:
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ApiError(400, f"'{key}' must be a string")
    if len(v) > max_len:
        raise ApiError(400, f"'{key}' is too long (max {max_len} characters)")
    return v


def opt_list(body: dict[str, Any], key: str) -> list[Any] | None:
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, list):
        raise ApiError(400, f"'{key}' must be a list")
    return v


def opt_dict(body: dict[str, Any], key: str) -> dict[str, Any] | None:
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ApiError(400, f"'{key}' must be an object")
    return v


def query_int(request: web.Request, name: str, default: int, *, lo: int = 0, hi: int = 1_000_000) -> int:
    raw = request.query.get(name)
    if raw in (None, ""):
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        raise ApiError(400, f"'{name}' must be an integer") from None


def query_flag(request: web.Request, name: str) -> bool:
    return (request.query.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def limit_of(request: web.Request, default: int = DEFAULT_LIMIT, max_: int = MAX_LIMIT) -> int:
    return query_int(request, "limit", default, lo=1, hi=max_)


def listing(items: list[Any], **extra: Any) -> dict[str, Any]:
    return {"items": items, "total": len(items), **extra}


# ---------------------------------------------------------------------------------------------
# Principals and sessions
# ---------------------------------------------------------------------------------------------


def principal_of(request: web.Request) -> Principal | None:
    return request.get("principal")


def require_user(request: web.Request) -> Principal:
    """A signed-in person. Anonymous -> 401; a holder or service credential -> 403 (it is not a person)."""
    p = principal_of(request)
    if p is None:
        raise ApiError(401, "sign in required")
    if not p.is_user:
        raise ApiError(403, "a user session is required for this endpoint")
    return p


def require_admin(request: web.Request) -> Principal:
    p = require_user(request)
    if not p.is_admin:
        raise Forbidden("admin", request.path, "organization administrator role required")
    return p


def require_holder(request: web.Request, holder_id: str) -> Principal:
    """The holder process itself (bearer = holder key), for bootstrap and heartbeat."""
    p = principal_of(request)
    if p is None:
        raise ApiError(401, "holder key required")
    if p.kind != "holder" or p.id != holder_id:
        raise Forbidden("holder.self", holder_id, "the holder key does not belong to this holder")
    return p


def bearer_token(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        tok = auth[7:].strip()
        return tok or None
    return None


def session_token(request: web.Request) -> str | None:
    """The token that authenticated this request as a session (cookie first, then bearer)."""
    meta = request.get("auth") or {}
    return meta.get("token") if meta.get("kind") == "session" else None


def set_session_cookie(resp: web.Response, token: str, settings: Any) -> None:
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax", secure=bool(getattr(settings, "secure_cookies", False)),
                    path="/", max_age=int(getattr(settings, "session_ttl_seconds", 14 * 24 * 3600)))


def clear_session_cookie(resp: web.Response, settings: Any) -> None:
    resp.del_cookie(SESSION_COOKIE, path="/")


def client_ip(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.remote or "")[:64]


async def resolve_principal(rt: Any, request: web.Request) -> tuple[Principal | None, dict[str, Any]]:
    """Credentials -> principal. Raises 401 for a credential that is present but invalid; returns ``(None, {})``
    when there is none (the handler decides whether that is acceptable). An expired cookie counts as none so a
    stale browser can still reach the login endpoints."""
    tok = bearer_token(request)
    if tok:
        if tok.startswith("mk_"):
            key = rt.auth.resolve_api_key(tok)
            if key is None:
                raise ApiError(401, "invalid api key")
            if key["principal_type"] == "user":
                p = rt.authz.principal_for_user(key["principal_id"], session_kind="api")
            elif key["principal_type"] == "holder":
                holder = rt.org.get_holder(key["principal_id"])
                p = rt.authz.principal_for_holder(holder) if holder and holder.get("status") != "revoked" else None
            else:
                p = None
            if p is None or p.tenant_id != key["tenant_id"]:
                raise ApiError(401, "api key principal is not active")
            return p, {"kind": "api_key", "key_id": key["key_id"], "token": tok}
        sess = rt.auth.resolve_session(tok)
        if sess is not None:
            p = _principal_for_session(rt, sess)
            if p is None:
                raise ApiError(401, "session user is not active")
            return p, {"kind": "session", "session": sess, "token": tok}
        holder = rt.auth.resolve_holder_key(tok)
        if holder is not None:
            return rt.authz.principal_for_holder(holder), {"kind": "holder_key", "holder_id": holder["holder_id"], "token": tok}
        raise ApiError(401, "invalid bearer token")
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        sess = rt.auth.resolve_session(cookie)
        if sess is None:
            return None, {"kind": "expired_cookie"}
        p = _principal_for_session(rt, sess)
        if p is None:
            return None, {"kind": "expired_cookie"}
        return p, {"kind": "session", "session": sess, "token": cookie}
    return None, {}


def _principal_for_session(rt: Any, sess: dict[str, Any]) -> Principal | None:
    p = rt.authz.principal_for_user(sess["user_id"], session_kind=sess.get("kind") or "web")
    if p is None or p.tenant_id != sess["tenant_id"]:
        return None
    return p


async def _touch(rt: Any, sess: dict[str, Any]) -> None:
    """``last_seen_at`` is written at most once a minute per session so reads do not turn into writes."""
    seen = parse_iso(sess.get("last_seen_at"))
    if seen is not None and (utcnow() - seen).total_seconds() < 60:
        return
    try:
        await rt.auth.touch_session(sess["session_id"])
    except Exception:  # never fail a request over bookkeeping
        logger.debug("touch_session failed", exc_info=True)


# ---------------------------------------------------------------------------------------------
# Host / origin checks (the pattern proven in NeuralGraph/chat_memory/ui/server.py)
# ---------------------------------------------------------------------------------------------


def _host_name(request: web.Request) -> str:
    host = (request.headers.get("Host") or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.split(":")[0]


def host_allowed(request: web.Request, allowed_hosts: frozenset[str] | None) -> bool:
    return allowed_hosts is None or _host_name(request) in allowed_hosts


def cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept, X-Requested-With, X-Request-Id",
        "Access-Control-Expose-Headers": "X-Request-Id",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


def route_label(request: web.Request) -> str:
    """The route template (``/api/goals/{goal_id}``) so metrics stay low-cardinality."""
    try:
        resource = request.match_info.route.resource
        canonical = resource.canonical if resource is not None else None
    except Exception:
        canonical = None
    if not canonical:
        return "unmatched"
    if canonical == "/{tail}":
        return "/api/*" if request.path.startswith("/api/") else "/static"
    return canonical


# ---------------------------------------------------------------------------------------------
# The middleware
# ---------------------------------------------------------------------------------------------


def build_middleware(rt: Any, settings: Any, *, allowed_hosts: list[str] | None = None, cors_origins: list[str] | None = None,
                     metrics: Any | None = None, reporter: Any | None = None) -> Callable[..., Awaitable[web.StreamResponse]]:
    hosts = frozenset(h.strip().lower() for h in allowed_hosts if h.strip()) if allowed_hosts else None
    cors_set = {o.strip().lower().rstrip("/") for o in (cors_origins or []) if o.strip()}
    public = (getattr(settings, "public_url", "") or "").strip().lower().rstrip("/")
    if public:
        parts = urlsplit(public)
        if parts.scheme and parts.netloc:
            cors_set.add(f"{parts.scheme}://{parts.netloc}")
    registry = metrics if metrics is not None else obs.metrics

    def origin_allowed(request: web.Request) -> bool:
        """No Origin (a non-browser client), same-origin, the public URL, or an origin on the allow-list."""
        origin = request.headers.get("Origin")
        if not origin:
            return True
        o = origin.strip().lower().rstrip("/")
        if "*" in cors_set or o in cors_set:
            return True
        host = (request.headers.get("Host") or "").strip().lower()
        return bool(host) and o.split("://", 1)[-1] == host

    def cors_for(request: web.Request) -> dict[str, str]:
        origin = request.headers.get("Origin")
        if not origin:
            return {}
        o = origin.strip().lower().rstrip("/")
        if "*" in cors_set or o in cors_set:
            return cors_headers(origin)
        return {}

    async def dispatch(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        if not host_allowed(request, hosts):
            return error_response(421, "host not allowed")
        if request.method == "OPTIONS":
            h = cors_for(request)
            return web.Response(status=204 if h else 403, headers=h)
        is_api = request.path.startswith("/api/")
        if is_api and request.method in STATE_CHANGING and not origin_allowed(request):
            return error_response(403, "origin not allowed (CSRF)", "csrf")
        try:
            if is_api:
                principal, meta = await resolve_principal(rt, request)
                request["principal"] = principal
                request["auth"] = meta
                if meta.get("kind") == "session":
                    await _touch(rt, meta["session"])
            return await handler(request)
        except ApiError as exc:
            return exc.response()
        except web.HTTPException as exc:
            if is_api:
                return error_response(exc.status, exc.reason or "error")
            raise
        except Forbidden as exc:
            p = request.get("principal")
            if p is not None:
                try:
                    await rt.authz.audit_denial(p, exc, request_id=request.get("request_id"))
                except Exception:
                    logger.exception("audit of a denial failed")
            return error_response(403, str(exc), "forbidden")
        except DuplicateQuestion as exc:
            return error_response(409, str(exc), "duplicate_question", existing_question_id=exc.existing_id)
        except CooldownActive as exc:
            return error_response(429, str(exc), "cooldown", existing_question_id=exc.existing_id, cooldown_until=exc.until)
        except AuthError as exc:
            return error_response(401, str(exc), "unauthorized")
        except KeyError as exc:
            key = exc.args[0] if exc.args else ""
            return error_response(404, f"not found: {key}" if key else "not found", "not_found")
        except ValueError as exc:
            return error_response(400, str(exc) or "invalid request", "validation")
        except TransportError as exc:
            return error_response(503, f"transport unavailable: {exc}", "transport")
        except ModelError as exc:
            return error_response(502, f"model call failed: {exc}", "model")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("unhandled error in %s %s", request.method, request.path)
            if reporter is not None:
                try:
                    p = request.get("principal")
                    await reporter.report("api", f"{type(exc).__name__}: {exc}", detail={"method": request.method, "path": request.path,
                                          "route": route_label(request)}, request_id=request.get("request_id"),
                                          tenant_id=p.tenant_id if p is not None else None)
                except Exception:
                    logger.exception("error report failed")
            return error_response(500, "internal error", "internal", request_id=request.get("request_id"))

    @web.middleware
    async def middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        raw = request.headers.get("X-Request-Id", "")
        rid = obs.bind_request_id(raw if _REQUEST_ID_RE.match(raw or "") else None)
        request["request_id"] = rid
        t0 = time.perf_counter()
        status = 500
        try:
            resp = await dispatch(request, handler)
            status = resp.status
            if not resp.prepared:
                resp.headers.setdefault("X-Request-Id", rid)
                for k, v in cors_for(request).items():
                    resp.headers.setdefault(k, v)
            return resp
        finally:
            dt = time.perf_counter() - t0
            route = route_label(request)
            try:
                registry.inc("mycelic_http_requests_total", method=request.method, route=route, status=str(status))
                registry.observe("mycelic_http_request_seconds", dt, method=request.method, route=route)
            except Exception:
                logger.debug("metrics update failed", exc_info=True)
            p = request.get("principal")
            level = logging.DEBUG if request.path in ("/healthz", "/readyz", "/metrics") else logging.INFO
            logger.log(level, "%s %s -> %s (%.1f ms)", request.method, request.path, status, dt * 1000,
                       extra={"method": request.method, "path": request.path, "route": route, "status": status, "duration_ms": round(dt * 1000, 1),
                              "principal_id": p.id if p is not None else None, "tenant_id": p.tenant_id if p is not None else None,
                              "ip": client_ip(request)})
            obs.clear_request_id()

    return middleware


__all__ = ["ApiError", "SESSION_COOKIE", "LOOPBACK_HOSTS", "json_response", "error_response", "read_json", "need_str", "opt_str", "opt_list",
           "opt_dict", "query_int", "query_flag", "limit_of", "listing", "principal_of", "require_user", "require_admin", "require_holder",
           "bearer_token", "session_token", "set_session_cookie", "clear_session_cookie", "client_ip", "resolve_principal", "build_middleware",
           "route_label", "host_allowed"]
