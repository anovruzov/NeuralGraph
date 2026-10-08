"""What the Google connectors (Gmail, Drive) share: OAuth 2.0 authorization code + PKCE against Google's authorization
server (``/o/oauth2/auth``, ``/token``, ``/revoke``), token refresh under the connection's refresh lock, the scope check
on connect, and small helpers for Google API responses (error reasons, header lookup, e-mail addresses as principals).

OAuth (Google's "OAuth 2.0 for web server applications", with PKCE ``S256``): the authorize URL asks for exactly the
connector's read-only scope, ``access_type=offline`` (a refresh token; access tokens live about an hour) and
``prompt=consent`` (Google issues a refresh token only on consent). The code exchange and refreshes POST a form to the token
endpoint; the client secret, the PKCE verifier and the refresh token travel only in that form body. A refresh that Google
refuses with ``invalid_grant`` (revoked, or a refresh token expired after six months unused) leaves the connection
``auth_expired``: Google does not tell a revoked grant from an expired one on a 401, so neither does this code.

The OAuth app (client id and secret) is server configuration: ``GmailConnector(oauth=OAuthAppConfig(...))`` or
:func:`mycelic.ingest.oauth.register_oauth_app`. ``oauth_base`` is the authorize host (default ``https://accounts.google.com``)
and ``api_base`` the token host (default ``https://oauth2.googleapis.com``); tests point both at a mock.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Iterable, Mapping

from ..contract import (AuthExpired, AuthRevoked, AuthStart, ConnectorContext, ConnectorManifest, Credentials, HttpResult, InsufficientScope,
                        PermanentError, Secret)
from ..http import ConnectorHttpClient
from ..oauth import OAuthAppConfig, authorize_url, oauth_app, pkce_pair, require_code
from ._provider import iso_utc

DEFAULT_AUTH_BASE = "https://accounts.google.com"
DEFAULT_TOKEN_BASE = "https://oauth2.googleapis.com"
GOOGLE_AUTH_HOSTS = ("oauth2.googleapis.com", "accounts.google.com")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


def header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup (the API layer lower-cases names; providers do not)."""
    for k, v in headers.items():
        if k.lower() == name:
            return str(v)
    return ""


def valid_email(value: Any) -> str | None:
    s = str(value or "").strip().lower()
    return s if _EMAIL.match(s) else None


def email_principal(ctx: ConnectorContext, app: str, email: str) -> str:
    """An e-mail address as a Mycelic principal: ``config.principal_map`` (keys compared case-insensitively), otherwise
    ``<app>:<address>``, which never equals a Mycelic user id (an unmapped participant can never widen access)."""
    pm = ctx.config.get("principal_map") or {}
    mapped = None
    if isinstance(pm, dict):
        mapped = pm.get(email) or next((v for k, v in pm.items() if str(k).lower() == email), None)
    return str(mapped) if mapped else f"{app}:{email}"


def email_principals(ctx: ConnectorContext, app: str, emails: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({email_principal(ctx, app, e) for e in emails if e}))


def google_reasons(res: HttpResult) -> set[str]:
    """``error.errors[].reason`` and ``error.status`` of a Google error body (fixed provider codes, never content)."""
    data = res.json if isinstance(res.json, dict) else {}
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    out = {str(e.get("reason"))[:60] for e in err.get("errors") or [] if isinstance(e, dict) and e.get("reason")}
    if err.get("status"):
        out.add(str(err["status"])[:60])
    return out


class GoogleOAuthMixin:
    """OAuth for a Google connector. The class sets ``manifest`` and ``oauth_scopes`` (requested, least privilege),
    ``satisfying_scopes`` (any of them is enough to read) and ``write_scopes`` (accepted with a warning)."""

    manifest: ClassVar[ConnectorManifest]
    oauth_scopes: ClassVar[tuple[str, ...]]
    satisfying_scopes: ClassVar[frozenset[str]]
    write_scopes: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, oauth: OAuthAppConfig | None = None) -> None:
        self._oauth = oauth

    @property
    def oauth(self) -> OAuthAppConfig | None:
        return self._oauth or oauth_app(self.manifest.connector_type)

    def _require_oauth(self) -> OAuthAppConfig:
        app = self.oauth
        if app is None:
            raise PermanentError("no Google OAuth client is configured", code="oauth_not_configured")
        return app

    @staticmethod
    def _token_base(app: OAuthAppConfig) -> str:
        return (app.api_base or DEFAULT_TOKEN_BASE).rstrip("/")

    async def authorize(self, *, tenant_id: str, holder_id: str, redirect_uri: str, state: str) -> AuthStart:
        app = self._require_oauth()
        verifier, challenge = pkce_pair()
        scopes = tuple(app.scopes) if app.scopes is not None else self.oauth_scopes
        url = authorize_url((app.oauth_base or DEFAULT_AUTH_BASE), "/o/oauth2/auth",
                            {"client_id": app.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
                             "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline",
                             "prompt": "consent"},
                            allowed_hosts=self.manifest.allowed_hosts, allow_loopback_http=app.allow_loopback_http)
        return AuthStart(kind="redirect", url=url, instructions="Approve read-only access in your Google account.", pkce_verifier=Secret(verifier))

    async def complete_authorization(self, params: Mapping[str, str], *, redirect_uri: str, pkce_verifier: Secret | None) -> Credentials:
        app = self._require_oauth()
        form: dict[str, Any] = {"code": require_code(params), "client_id": app.client_id, "redirect_uri": redirect_uri,
                                "grant_type": "authorization_code"}
        if app.client_secret is not None:
            form["client_secret"] = app.client_secret
        if pkce_verifier is not None:
            form["code_verifier"] = pkce_verifier
        return await self._token_request(app, form)

    async def refresh_credentials(self, creds: Credentials) -> Credentials:
        app = self._require_oauth()
        if creds.refresh_token is None:
            raise AuthExpired("nothing to refresh with", detail={"reason": "no_refresh_token"})
        form: dict[str, Any] = {"client_id": app.client_id, "grant_type": "refresh_token", "refresh_token": creds.refresh_token}
        if app.client_secret is not None:
            form["client_secret"] = app.client_secret
        return await self._token_request(app, form, previous=creds)

    async def _token_request(self, app: OAuthAppConfig, form: Mapping[str, Any], previous: Credentials | None = None) -> Credentials:
        http = ConnectorHttpClient(self.manifest, None, allow_loopback_http=app.allow_loopback_http, serial_per_token=False)
        try:
            res = await http.request("POST", self._token_base(app) + "/token", form=form, auth=False, expected=(200, 400, 401))
        finally:
            await http.close()
        data = res.json if isinstance(res.json, dict) else {}
        if res.status != 200 or not data.get("access_token"):
            error = str(data.get("error") or "refused")[:40]
            if error == "invalid_grant" and previous is not None:
                raise AuthRevoked("Google refused the refresh token (revoked or expired)", detail={"error": error})
            raise PermanentError("the token exchange was refused", code=f"oauth_{error}")
        expires_at = iso_utc(datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))) if data.get("expires_in") else None
        refresh = data.get("refresh_token") or (previous.refresh_token.reveal() if previous and previous.refresh_token else None)
        extra = dict(previous.extra) if previous else {}
        extra.update({"token_type": str(data.get("token_type") or "Bearer"), "scope": str(data.get("scope") or extra.get("scope") or "")})
        return Credentials(kind="oauth2", access_token=Secret(str(data["access_token"])), refresh_token=Secret(str(refresh)) if refresh else None,
                           expires_at=expires_at, extra=extra)

    async def revoke(self, creds: Credentials) -> None:
        """Google's revocation endpoint; revoking the refresh token also revokes the access tokens issued from it."""
        app = self.oauth
        token = creds.refresh_token or creds.access_token
        if token is None:
            return
        http = ConnectorHttpClient(self.manifest, None, allow_loopback_http=bool(app and app.allow_loopback_http), serial_per_token=False)
        try:
            await http.request("POST", self._token_base(app) + "/revoke" if app else DEFAULT_TOKEN_BASE + "/revoke", form={"token": token},
                               auth=False, expected=(200, 400))
        finally:
            await http.close()

    async def check_scopes(self, ctx: ConnectorContext) -> tuple[tuple[str, ...], list[str]]:
        """``(granted scopes, warnings)`` from the scope Google reported with the token. Refuses a token that cannot read; warns
        about write scopes this connector never uses. A token entered without its scope is checked by the first API call
        (Google answers 403 ``insufficientPermissions``)."""
        creds = await ctx.secrets.get()
        granted = tuple(sorted({s for s in str((creds.extra or {}).get("scope") or "").replace(",", " ").split() if s}))
        warnings: list[str] = []
        if granted:
            if not set(granted) & self.satisfying_scopes:
                raise InsufficientScope(missing=self.oauth_scopes, detail={"missing": ",".join(self.oauth_scopes)})
            extra = sorted(set(granted) & self.write_scopes)
            if extra:
                warnings.append("the token carries scopes that allow changes this connector never makes: " + ", ".join(extra)
                                + "; authorize again with the read-only scope")
        return granted, warnings


__all__ = ["DEFAULT_AUTH_BASE", "DEFAULT_TOKEN_BASE", "GOOGLE_AUTH_HOSTS", "GoogleOAuthMixin", "email_principal", "email_principals", "google_reasons",
           "header", "valid_email"]
