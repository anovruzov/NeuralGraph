"""What the offline Google mocks (Gmail, Drive) share: bearer tokens with scopes, Google's error JSON, rate-limit and auth
fault injection, and a mock of Google's OAuth 2.0 authorization server (``GET /o/oauth2/auth``, ``POST /token``,
``POST /revoke``) with PKCE ``S256``.

Modelled behaviour (Google's API error format and OAuth endpoints as documented; re-verify against developers.google.com):

* Errors are ``{"error": {"code", "message", "errors": [{"message", "domain", "reason"}], "status"}}``: 401
  ``authError`` / ``UNAUTHENTICATED`` for a missing, unknown, expired or revoked token; 403 ``insufficientPermissions`` /
  ``PERMISSION_DENIED`` (``ACCESS_TOKEN_SCOPE_INSUFFICIENT``) when the token lacks the scope; 403 ``userRateLimitExceeded``
  or ``rateLimitExceeded`` (domain ``usageLimits``) and 429 ``rateLimitExceeded`` / ``RESOURCE_EXHAUSTED`` for rate limits,
  normally without ``Retry-After``; 403 ``dailyLimitExceeded``.
* OAuth: the authorize endpoint auto-approves as ``app.authorizing_user`` and redirects with ``code`` and ``state``; the
  token endpoint takes ``application/x-www-form-urlencoded`` and answers JSON (``access_token``, ``expires_in``,
  ``refresh_token`` only with ``access_type=offline``, ``scope`` space-separated, ``token_type: "Bearer"``); errors are 400
  ``invalid_grant`` (bad code, verifier, refresh token) and 401 ``invalid_client``. A refresh answers no new refresh token.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import secrets
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import web

from .common import MockProvider

STATUS_TEXT = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND", 429: "RESOURCE_EXHAUSTED",
               500: "INTERNAL", 503: "UNAVAILABLE"}
RATE_MESSAGES = {"userRateLimitExceeded": "User Rate Limit Exceeded", "rateLimitExceeded": "Rate Limit Exceeded",
                 "dailyLimitExceeded": "Daily Limit Exceeded"}


def google_error_body(status: int, message: str, reason: str, *, domain: str = "global", **extra: Any) -> dict[str, Any]:
    err: dict[str, Any] = {"message": message, "domain": domain, "reason": reason}
    err.update(extra)
    return {"error": {"code": status, "message": message, "errors": [err], "status": STATUS_TEXT.get(status, "UNKNOWN")}}


class GoogleMockBase(MockProvider):
    """Token auth, Google errors, faults and the OAuth server. Subclasses add their API routes in ``_api_routes`` and
    declare the scopes that read their API in ``read_scopes``."""

    read_scopes: frozenset[str] = frozenset()

    def __init__(self, fixture: Mapping[str, Any], *, clock: Callable[[], float] | None = None, latency: float = 0.0) -> None:
        fx = copy.deepcopy(dict(fixture))
        self.app_config: dict[str, Any] = dict(fx.get("app") or {})
        self.tokens: dict[str, dict[str, Any]] = {t["token"]: dict(t) for t in fx.get("tokens") or []}
        self._codes: dict[str, dict[str, Any]] = {}
        self._refresh: dict[str, dict[str, Any]] = {}
        self._rate_next: list[dict[str, Any]] = []
        super().__init__(clock=clock, latency=latency)

    # ------------------------------------------------------------------ routes
    def _routes(self, app: web.Application) -> None:
        app.router.add_get("/o/oauth2/auth", self.h_authorize)
        app.router.add_get("/o/oauth2/v2/auth", self.h_authorize)
        app.router.add_post("/token", self.h_token)
        app.router.add_post("/revoke", self.h_revoke)
        self._api_routes(app)

    def _api_routes(self, app: web.Application) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------ faults
    def rate_limit_next(self, *, status: int = 403, reason: str = "userRateLimitExceeded", count: int = 1, retry_after: int | None = None,
                        path_contains: str | None = None) -> None:
        """The next ``count`` API calls (whose path contains ``path_contains``, or any) answer a Google rate-limit error:
        403 ``userRateLimitExceeded`` / ``rateLimitExceeded`` / ``dailyLimitExceeded`` (domain ``usageLimits``) or 429
        ``rateLimitExceeded``. Google normally sends no ``Retry-After``; ``retry_after`` adds one."""
        self._rate_next.extend({"status": int(status), "reason": reason, "retry_after": retry_after, "path": path_contains} for _ in range(int(count)))

    def revoke_token(self, token: str) -> None:
        """The grant is revoked at Google: the access token answers 401 and its refresh token ``invalid_grant``."""
        info = self.tokens.get(token) or {}
        info["revoked"] = True
        for r in self._refresh.values():
            if r.get("access") == token:
                r["revoked"] = True

    def expire_token(self, token: str) -> None:
        """The access token expired (an hour after issue): 401; its refresh token still works."""
        self.tokens[token]["expired"] = True

    # ------------------------------------------------------------------ helpers
    def error(self, status: int, message: str, reason: str, *, headers: Mapping[str, str] | None = None, **extra: Any) -> web.Response:
        return web.json_response(google_error_body(status, message, reason, **extra), status=status, headers=dict(headers or {}))

    def _pending_rate_limit(self, request: web.Request) -> web.Response | None:
        for i, r in enumerate(self._rate_next):
            if r["path"] is None or r["path"] in request.path:
                self._rate_next.pop(i)
                headers = {"Retry-After": str(r["retry_after"])} if r["retry_after"] is not None else {}
                if r["status"] == 429:
                    return self.error(429, "Too many concurrent requests for user.", "rateLimitExceeded", headers=headers)
                return self.error(403, RATE_MESSAGES.get(r["reason"], "Rate Limit Exceeded"), r["reason"], domain="usageLimits", headers=headers)
        return None

    def authenticate(self, request: web.Request) -> tuple[dict[str, Any] | None, web.Response | None]:
        """``(token info, None)`` or ``(None, error response)``: rate limits first, then authentication, then scopes."""
        limited = self._pending_rate_limit(request)
        if limited is not None:
            return None, limited
        token = self._bearer(request)
        if not token:
            return None, self.error(401, "Request is missing required authentication credential. Expected OAuth 2 access token, login cookie or "
                                         "other valid authentication credential.", "required", location="Authorization", locationType="header")
        info = self.tokens.get(token)
        if info is None or info.get("revoked") or info.get("expired"):
            return None, self.error(401, "Request had invalid authentication credentials. Expected OAuth 2 access token, login cookie or other "
                                         "valid authentication credential.", "authError", location="Authorization", locationType="header")
        if self.read_scopes and not set(info.get("scopes") or []) & self.read_scopes:
            body = google_error_body(403, "Request had insufficient authentication scopes.", "insufficientPermissions")
            body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                                         "domain": "googleapis.com"}]
            body["error"]["errors"][0]["message"] = "Insufficient Permission"
            return None, web.json_response(body, status=403)
        return info, None

    # ------------------------------------------------------------------ OAuth 2.0 authorization server
    async def h_authorize(self, request: web.Request) -> web.StreamResponse:
        q = request.query
        if q.get("client_id") != self.app_config.get("client_id"):
            return web.Response(status=401, text="Error 401: invalid_client. The OAuth client was not found.")
        redirect = q.get("redirect_uri") or ""
        if q.get("response_type") != "code":
            return _redirect(redirect, {"error": "unsupported_response_type", "state": q.get("state", "")})
        method = q.get("code_challenge_method", "plain")
        if q.get("code_challenge") and method not in ("S256", "plain"):
            return _redirect(redirect, {"error": "invalid_request", "state": q.get("state", "")})
        scopes = [s for s in (q.get("scope") or "").split() if s]
        if not scopes:
            return _redirect(redirect, {"error": "invalid_scope", "state": q.get("state", "")})
        code = "4/0mock-" + secrets.token_urlsafe(18)
        self._codes[code] = {"user": self.app_config.get("authorizing_user"), "scopes": scopes, "redirect_uri": redirect,
                             "challenge": q.get("code_challenge"), "method": method, "offline": q.get("access_type") == "offline",
                             "expires": self.clock() + 600}
        return _redirect(redirect, {"code": code, "scope": " ".join(scopes), "state": q.get("state", "")})

    def _issue(self, user: str, scopes: list[str], *, offline: bool, refresh: str | None = None) -> dict[str, Any]:
        access = "ya29.mock-" + secrets.token_urlsafe(24)
        self.tokens[access] = {"token": access, "user": user, "scopes": list(scopes)}
        data: dict[str, Any] = {"access_token": access, "expires_in": 3599, "scope": " ".join(scopes), "token_type": "Bearer"}
        if offline and refresh is None:
            refresh = "1//mock-" + secrets.token_urlsafe(30)
            self._refresh[refresh] = {"user": user, "scopes": list(scopes), "access": access}
            data["refresh_token"] = refresh
        elif refresh is not None:
            self._refresh[refresh]["access"] = access
        return data

    async def h_token(self, request: web.Request) -> web.Response:
        form = {k: str(v) for k, v in (await request.post()).items()}
        if form.get("client_id") != self.app_config.get("client_id") or form.get("client_secret") != self.app_config.get("client_secret"):
            return web.json_response({"error": "invalid_client", "error_description": "The OAuth client was not found."}, status=401)
        grant = form.get("grant_type")
        if grant == "refresh_token":
            r = self._refresh.get(form.get("refresh_token", ""))
            if r is None or r.get("revoked"):
                return web.json_response({"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, status=400)
            return web.json_response(self._issue(r["user"], r["scopes"], offline=True, refresh=form["refresh_token"]))
        if grant != "authorization_code":
            return web.json_response({"error": "unsupported_grant_type", "error_description": "Invalid grant_type: " + str(grant)[:40]}, status=400)
        g = self._codes.pop(form.get("code", ""), None)
        if g is None or g["expires"] < self.clock():
            return web.json_response({"error": "invalid_grant", "error_description": "Malformed auth code."}, status=400)
        if form.get("redirect_uri") != g["redirect_uri"]:
            return web.json_response({"error": "redirect_uri_mismatch", "error_description": "Bad Request"}, status=400)
        if g["challenge"]:
            verifier = form.get("code_verifier", "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() if g["method"] == "S256" else verifier
            if not verifier or not hmac.compare_digest(digest, g["challenge"]):
                return web.json_response({"error": "invalid_grant", "error_description": "Invalid code verifier."}, status=400)
        return web.json_response(self._issue(g["user"], g["scopes"], offline=g["offline"]))

    async def h_revoke(self, request: web.Request) -> web.Response:
        form = {k: str(v) for k, v in (await request.post()).items()}
        token = form.get("token") or request.query.get("token", "")
        if token in self._refresh:
            self._refresh[token]["revoked"] = True
            access = self._refresh[token].get("access")
            if access in self.tokens:
                self.tokens[access]["revoked"] = True
            return web.json_response({})
        if token in self.tokens:
            self.tokens[token]["revoked"] = True
            return web.json_response({})
        return web.json_response({"error": "invalid_token", "error_description": "Token expired or revoked"}, status=400)


def field_mask(spec: str | None) -> dict[str, Any] | None:
    """A Google partial-response ``fields`` mask (``nextPageToken,files(id,name,owners(emailAddress))``) as a tree;
    ``None`` for ``*`` or no mask."""
    if not spec or spec.strip() == "*":
        return None
    pos = 0

    def parse() -> dict[str, Any]:
        nonlocal pos
        out: dict[str, Any] = {}
        name = ""
        while pos < len(spec):
            ch = spec[pos]
            pos += 1
            if ch == "(":
                out[name.strip()] = parse()
                name = ""
            elif ch == ")":
                break
            elif ch == ",":
                if name.strip():
                    out[name.strip()] = None
                name = ""
            else:
                name += ch
        if name.strip():
            out[name.strip()] = None
        return out
    tree = parse()
    for key in list(tree):
        if "/" in key:                                   # a/b paths: the same as a(b)
            head, rest = key.split("/", 1)
            tree.pop(key)
            tree.setdefault(head, {})[rest] = None
    return tree


def apply_mask(value: Any, tree: dict[str, Any] | None) -> Any:
    if tree is None:
        return value
    if isinstance(value, list):
        return [apply_mask(v, tree) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, sub in tree.items():
        if k == "*":
            return value
        if k in value:
            out[k] = apply_mask(value[k], sub)
    return out


def _redirect(uri: str, params: Mapping[str, str]) -> web.Response:
    parts = urlsplit(uri)
    q = parse_qsl(parts.query) + [(k, v) for k, v in params.items() if v]
    return web.Response(status=302, headers={"Location": urlunsplit(parts._replace(query=urlencode(q)))})


__all__ = ["GoogleMockBase", "apply_mask", "field_mask", "google_error_body"]
