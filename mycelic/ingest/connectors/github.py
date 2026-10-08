"""GitHub connector: issues, pull requests (their conversation) and issue comments through the REST API (INGESTION.md §11.2).

Status ``tested-offline``: exercised end to end against the faithful mock in :mod:`mycelic.ingest.mocks.github_mock`,
whose responses are checked against GitHub's published OpenAPI description (``mocks/fixtures/github_openapi_subset.json``).
It has not been run against api.github.com by this code base.

Endpoints (request headers ``Accept: application/vnd.github+json``, ``X-GitHub-Api-Version: 2026-03-10``):

* connect: ``GET /user`` (identity; ``X-OAuth-Scopes`` for classic and OAuth-app tokens);
* discover: ``GET /user/repos`` (personal), ``GET /orgs/{org}/repos`` (an org token an admin supplied, ``config.org``),
  ``GET /installation/repositories`` (GitHub App installation token, ``config.auth_mode='installation'`` — see below);
* pull: ``GET /repos/{o}/{r}/issues`` (``state=all, sort=updated, direction=asc, since``) and
  ``GET /repos/{o}/{r}/issues/comments`` (``sort=updated, direction=asc, since``), following ``Link: rel="next"`` only;
* notify-then-fetch: ``GET /repositories/{id}`` (the container, by its stable id: notices carry ids, never names),
  ``GET /repos/{o}/{r}/issues/{n}`` (200, 301 transferred, 404/410 gone) and ``GET /repos/{o}/{r}/issues/comments/{id}``.

Identity. ``source_account_id`` is the API host (``api.github.com``; the GHES or mock host otherwise), so two tokens of
one holder that see the same issue produce one record. An issue or PR is ``<repo_id>#<number>`` (both an
``issue``/``pull_request`` message and its ``conversation``); a comment is its REST id with the issue as parent. A transfer
gives the issue a new repository and number, i.e. a new identity: the old record is tombstoned with reason ``moved`` and
the new one arrives through its own repository's stream with the same root (same text). ``source_version = updated_at``.

ACL (product decision 2: ``public | members | private``). public repo → ``public``; ``internal`` (enterprise) →
``members`` of the owning organization; private and organization-owned → ``members`` of the repository; private and
user-owned → ``private``. Member lists cannot be enumerated with read-only permissions (GitHub requires push access to
list collaborators), so restricted records carry a ``membership_ref`` that resolves to nobody until an admin maps members
(``config.acl_members``, or rows for that ref in ``acl_memberships``): until then only the holder owner sees them (fail closed).

Credentials. A fine-grained personal access token restricted to selected repositories with *Issues: Read-only* and
*Metadata: Read-only* (the least privilege; GitHub does not report fine-grained permissions in headers, so a missing one
surfaces as ``InsufficientScope`` on the first listing). Classic tokens: reading public repositories needs **no** scope;
``repo`` is needed for private ones and also grants write access, so the connector warns; ``public_repo`` is a write
scope this connector never needs (warned too). OAuth: an OAuth app or GitHub App user-to-server flow with PKCE
(``authorize``/``complete_authorization``), with refresh of expiring GitHub App user tokens under the refresh lock.
**GitHub App installation tokens are a scaffold**: discovery and reads work with one, but minting and renewing them (app
JWT, ``POST /app/installations/{id}/access_tokens``, one-hour expiry) is not implemented.

Configuration (non-secret)::

    {"api_base": "https://api.github.com", "org": "acme", "repos": ["acme/checkout"], "include_private": true,
     "auth_mode": "user" | "installation", "installation_id": "123",
     "principal_map": {"1001": "usr_ana"}, "acl_members": {"acme/checkout": ["1001", "1002"]},
     "overlap_seconds": 120, "initial_lookback_days": 30, "limits": {"page_size": 100, ...}}

Content is data: nothing read from an issue or comment changes what this connector does.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Iterable, Mapping
from urllib.parse import quote, urlsplit

from ..contract import (AuthExpired, AuthRevoked, AuthStart, BackfillWindow, Capabilities, ConnectorContext, ConnectorError, ConnectorManifest,
                        ConnectResult, Connector, Credentials, Cursor, HealthReport, HttpResult, InsufficientScope, ObjectGone, Page,
                        PermanentError, RateLimited, RateLimitSpec, RawItem, ScopeSpec, Secret, SourceDescriptor, SourceUnavailable,
                        TransientError, WebhookNotice)
from ..events import VISIBILITIES, CanonicalEvent, Permissions, normalize_ts
from ..http import ConnectorHttpClient
from ..oauth import OAuthAppConfig, authorize_url, oauth_app, pkce_pair, require_code
from ._provider import extract_urls, gh_time, iso_utc, principals, refresh_once

API_VERSION = "2026-03-10"
DEFAULT_API = "https://api.github.com"
DEFAULT_OAUTH = "https://github.com"
CURSOR_VERSION = 1
OVERLAP_SECONDS = 120
LISTINGS = ("issues", "comments")
WRITE_SCOPES = frozenset({"repo", "public_repo", "delete_repo", "workflow", "write:packages", "write:org", "write:discussion",
                          "admin:org", "admin:repo_hook", "admin:org_hook", "admin:public_key", "admin:gpg_key", "admin:enterprise",
                          "gist", "user", "codespace", "project"})
_FULL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_ORG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
_ISSUE_REF = re.compile(r"(?<![\w/#&])(?:([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100}))?#(\d{1,7})\b")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ID = re.compile(r"^\d{1,20}$")
PAT_INSTRUCTIONS = ("Create a fine-grained personal access token: Repository access 'Only select repositories' (the repositories to "
                    "remember); Repository permissions 'Issues: Read-only' and 'Metadata: Read-only'; nothing else. A classic token "
                    "needs no scope for public repositories and 'repo' for private ones ('repo' also grants write access).")


class GitHubConnector(Connector):
    manifest = ConnectorManifest(
        connector_type="github",
        display_name="GitHub (issues, pull requests, comments)",
        version="1.0.0",
        status="tested-offline",
        auth_kinds=("pat", "oauth2", "github_app_user"),
        scopes=(
            ScopeSpec("Issues: Read-only (fine-grained)", True,
                      "Read issues, pull request conversations and their comments in the repositories you select."),
            ScopeSpec("Metadata: Read-only (fine-grained)", True, "Mandatory for fine-grained tokens: repository names, ids and visibility."),
            ScopeSpec("repo (classic tokens only)", False,
                      "Only if private repositories should be remembered with a classic token. It also grants write access to every "
                      "private repository; a fine-grained token is preferred and the connector warns about it."),
            ScopeSpec("public_repo (classic tokens only)", False,
                      "Not needed: public repositories are readable with no scope. public_repo grants write access to public "
                      "repositories, so the connector warns when a token carries it."),
        ),
        modes=frozenset({"pull", "webhook"}),
        source_types=("repo",),
        capabilities=Capabilities(edits=True, deletes="webhook", threads=True, attachments=False, acl="visibility_only", exports=False),
        rate_limit=RateLimitSpec(kind="headers", default_rps=1.0, burst=1, serial_per_token=True),
        allowed_hosts=("api.github.com", "github.com"),
        default_poll_seconds=120,
        terms_notes=("Reads issues, pull request conversations (no diffs, no review comments) and issue comments through GitHub's REST API "
                     f"(version {API_VERSION}), with the owner's token, serially per token, honouring GitHub's primary and secondary rate "
                     "limits. Least privilege: a fine-grained token with Issues and Metadata read-only on selected repositories. "
                     "Members of private repositories are not enumerated (that needs push access), so their records are shown only to the "
                     "holder owner until an admin maps members. GitHub App installation tokens are a scaffold: they are accepted for "
                     "reads, but minting and renewing them is not implemented. Status: tested offline against a mock of the documented "
                     "API; not live-verified."),
        ownership=("personal", "org"),
    )

    def __init__(self, oauth: OAuthAppConfig | None = None) -> None:
        self._oauth = oauth

    @property
    def oauth(self) -> OAuthAppConfig | None:
        return self._oauth or oauth_app(self.manifest.connector_type)

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def _api(ctx: ConnectorContext) -> str:
        return str(ctx.config.get("api_base") or DEFAULT_API).rstrip("/")

    @classmethod
    def _host(cls, ctx: ConnectorContext) -> str:
        return (urlsplit(cls._api(ctx)).hostname or "api.github.com").lower()

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION, "User-Agent": f"mycelic-ingest/{self.manifest.version}"}

    async def _get(self, ctx: ConnectorContext, url: str, *, params: Mapping[str, Any] | None = None, etag_key: str | None = None,
                   expected: tuple[int, ...] = (200,)) -> HttpResult:
        full = url if url.startswith(("http://", "https://")) else self._api(ctx) + url

        async def go() -> HttpResult:
            return await ctx.http.request("GET", full, params=params, headers=self._headers(), etag_key=etag_key, expected=expected)
        try:
            return await go()
        except AuthExpired:
            if await refresh_once(ctx, self.refresh_credentials if self.oauth else None):
                return await go()
            raise

    def _relative(self, ctx: ConnectorContext, link: str) -> str:
        """A ``Link`` URL as a path + query on the configured API base (cursors never store hosts or credentials)."""
        api, nxt = urlsplit(self._api(ctx)), urlsplit(link)
        if (nxt.hostname, nxt.port) != (api.hostname, api.port):
            raise PermanentError("a pagination link left the API host", code="foreign_link", detail={"host": nxt.hostname})
        prefix = api.path.rstrip("/")
        path = nxt.path[len(prefix):] if prefix and nxt.path.startswith(prefix) else nxt.path
        return path + (f"?{nxt.query}" if nxt.query else "")

    @staticmethod
    def _per_page(ctx: ConnectorContext) -> int:
        return max(1, min(100, int(ctx.limits.page_size)))

    @staticmethod
    def _rate(ctx: ConnectorContext) -> dict[str, Any]:
        snap = getattr(ctx.http, "rate_snapshot", None)
        return snap() if callable(snap) else {}

    @staticmethod
    def _full(source: SourceDescriptor) -> str:
        full = str(source.metadata.get("full_name") or source.name or "")
        if not _FULL_NAME.match(full):
            raise PermanentError("source has no valid repository name", code="bad_source", detail={"source": source.external_id})
        return full

    @staticmethod
    def _list(res: HttpResult) -> list[dict[str, Any]]:
        if not isinstance(res.json, list):
            raise PermanentError("expected a JSON array", code="bad_response", detail={"status": res.status})
        return [x for x in res.json if isinstance(x, dict)]

    @staticmethod
    def _fetched(ctx: ConnectorContext) -> str:
        return iso_utc(ctx.clock())

    def _validate_config(self, ctx: ConnectorContext) -> None:
        cfg = ctx.config
        if cfg.get("auth_mode", "user") not in ("user", "installation"):
            raise PermanentError("auth_mode must be user or installation", code="bad_config")
        if cfg.get("org") is not None and not _ORG.match(str(cfg["org"])):
            raise PermanentError("org must be a GitHub organization login", code="bad_config")
        repos = cfg.get("repos")
        if repos is not None and (not isinstance(repos, list) or not all(isinstance(r, str) and _FULL_NAME.match(r) for r in repos)):
            raise PermanentError("repos must be a list of owner/name", code="bad_config")
        for key in ("principal_map", "acl_members"):
            if cfg.get(key) is not None and not isinstance(cfg.get(key), dict):
                raise PermanentError(f"{key} must be an object", code="bad_config")

    # ------------------------------------------------------------------ OAuth (code + PKCE)
    def _require_oauth(self) -> OAuthAppConfig:
        app = self.oauth
        if app is None:
            raise PermanentError("no GitHub OAuth app is configured", code="oauth_not_configured")
        return app

    def _oauth_http(self, app: OAuthAppConfig) -> ConnectorHttpClient:
        return ConnectorHttpClient(self.manifest, None, allow_loopback_http=app.allow_loopback_http, serial_per_token=False)

    @staticmethod
    def _oauth_base(app: OAuthAppConfig) -> str:
        return (app.oauth_base or DEFAULT_OAUTH).rstrip("/")

    async def authorize(self, *, tenant_id: str, holder_id: str, redirect_uri: str, state: str) -> AuthStart:
        app = self.oauth
        if app is None:
            return AuthStart(kind="token_entry", instructions=PAT_INSTRUCTIONS)
        verifier, challenge = pkce_pair()
        # least privilege: public repositories need no scope; 'repo' (read+write on private repositories) only when asked for
        scopes = tuple(app.scopes) if app.scopes is not None else ()
        url = authorize_url(self._oauth_base(app), "/login/oauth/authorize",
                            {"client_id": app.client_id, "redirect_uri": redirect_uri, "scope": " ".join(scopes), "state": state,
                             "code_challenge": challenge, "code_challenge_method": "S256", "allow_signup": "false"},
                            allowed_hosts=self.manifest.allowed_hosts, allow_loopback_http=app.allow_loopback_http)
        return AuthStart(kind="redirect", url=url, instructions="Approve read access on GitHub.", pkce_verifier=Secret(verifier))

    async def complete_authorization(self, params: Mapping[str, str], *, redirect_uri: str, pkce_verifier: Secret | None) -> Credentials:
        app = self._require_oauth()
        form: dict[str, Any] = {"client_id": app.client_id, "code": require_code(params), "redirect_uri": redirect_uri}
        if app.client_secret is not None:
            form["client_secret"] = app.client_secret
        if pkce_verifier is not None:
            form["code_verifier"] = pkce_verifier
        return await self._token_request(app, form)

    async def refresh_credentials(self, creds: Credentials) -> Credentials:
        """GitHub App user tokens expire (8 h) and come with a refresh token (6 months); OAuth-app tokens do not expire."""
        app = self._require_oauth()
        if creds.refresh_token is None:
            raise AuthExpired("nothing to refresh with", detail={"reason": "no_refresh_token"})
        form: dict[str, Any] = {"client_id": app.client_id, "grant_type": "refresh_token", "refresh_token": creds.refresh_token}
        if app.client_secret is not None:
            form["client_secret"] = app.client_secret
        return await self._token_request(app, form)

    async def _token_request(self, app: OAuthAppConfig, form: Mapping[str, Any]) -> Credentials:
        http = self._oauth_http(app)
        try:
            res = await http.request("POST", self._oauth_base(app) + "/login/oauth/access_token", form=form,
                                     headers={"Accept": "application/json"}, auth=False)
        finally:
            await http.close()
        data = res.json if isinstance(res.json, dict) else {}
        if data.get("error") or not data.get("access_token"):     # GitHub answers token errors with HTTP 200
            raise PermanentError("the token exchange was refused", code=f"oauth_{str(data.get('error') or 'no_token')[:40]}")
        expires_at = None
        if data.get("expires_in"):
            expires_at = iso_utc(datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"])))
        return Credentials(kind="github_app_user" if data.get("refresh_token") else "oauth2", access_token=Secret(str(data["access_token"])),
                           refresh_token=Secret(str(data["refresh_token"])) if data.get("refresh_token") else None, expires_at=expires_at,
                           extra={"token_type": str(data.get("token_type") or "bearer"), "scope": str(data.get("scope") or "")})

    # ------------------------------------------------------------------ lifecycle
    async def connect(self, ctx: ConnectorContext) -> ConnectResult:
        """``GET /user``: identity and (classic/OAuth-app tokens) granted scopes. Never fetches content."""
        self._validate_config(ctx)
        host = self._host(ctx)
        if ctx.config.get("auth_mode") == "installation":
            await self._get(ctx, "/installation/repositories", params={"per_page": 1})
            inst = str(ctx.config.get("installation_id") or "")
            return ConnectResult(source_account_id=host, auth_account_id=f"installation:{inst}", account_label="GitHub App installation",
                                 warnings=("installation tokens expire after one hour and this connector does not renew them (scaffold)",))
        res = await self._get(ctx, "/user")
        user = res.json if isinstance(res.json, dict) else {}
        if user.get("id") is None or not user.get("login"):
            raise PermanentError("unexpected identity response", code="bad_response")
        warnings: list[str] = []
        granted: tuple[str, ...] = ()
        header = res.headers.get("x-oauth-scopes")
        if header is not None:                      # classic PAT or OAuth-app token; fine-grained tokens report nothing here
            granted = tuple(sorted({s.strip() for s in header.split(",") if s.strip()}))
            if ctx.config.get("include_private") and "repo" not in granted:
                raise InsufficientScope(missing=("repo",), detail={"token": "classic"})
            if "repo" in granted:
                warnings.append("classic token with the 'repo' scope: it also grants write access to every private repository the "
                                "account can reach; prefer a fine-grained token with Issues and Metadata read-only")
            extra = sorted(s for s in granted if s != "repo" and (s in WRITE_SCOPES or s.startswith(("admin:", "write:", "delete"))))
            if extra:
                warnings.append("token carries write or admin scopes this connector never uses: " + ", ".join(extra))
        label = str(user["login"]) + (f" ({ctx.config['org']})" if ctx.config.get("org") else "")
        return ConnectResult(source_account_id=host, auth_account_id=str(user["id"]), account_label=label, granted_scopes=granted,
                             warnings=tuple(warnings))

    async def health(self, ctx: ConnectorContext) -> HealthReport:
        try:
            if ctx.config.get("auth_mode") == "installation":
                await self._get(ctx, "/installation/repositories", params={"per_page": 1})
            else:
                await self._get(ctx, "/user")
        except AuthRevoked as exc:
            return HealthReport("revoked", {"auth": False, "api_reachable": True}, self._rate(ctx), exc.code)
        except AuthExpired as exc:
            return HealthReport("auth_expired", {"auth": False, "api_reachable": True}, self._rate(ctx), exc.code)
        except RateLimited as exc:
            return HealthReport("rate_limited", {"auth": True, "api_reachable": True}, {**self._rate(ctx), "retry_after": exc.retry_after}, exc.code)
        except TransientError as exc:
            return HealthReport("degraded", {"auth": True, "api_reachable": False}, self._rate(ctx), exc.code)
        except ConnectorError as exc:
            return HealthReport("error", {"auth": False, "api_reachable": True}, self._rate(ctx), exc.code)
        return HealthReport("ok", {"auth": True, "api_reachable": True, "scopes": True}, self._rate(ctx))

    # ------------------------------------------------------------------ sources
    def _acl(self, ctx: ConnectorContext, repo: Mapping[str, Any], host: str) -> tuple[str, tuple[str, ...], str | None]:
        visibility = str(repo.get("visibility") or ("private" if repo.get("private") else "public"))
        owner = repo.get("owner") or {}
        if visibility == "public":
            return "public", (), None
        configured = (ctx.config.get("acl_members") or {}).get(str(repo.get("full_name")))
        members = principals(ctx, "github", configured or ())
        if visibility == "internal":
            return "members", members, f"github:{host}:org:{owner.get('id')}"
        if owner.get("type") == "Organization":
            return "members", members, f"github:{host}:repo:{repo.get('id')}"
        return "private", members, f"github:{host}:repo:{repo.get('id')}"

    def _descriptor(self, ctx: ConnectorContext, repo: Mapping[str, Any]) -> SourceDescriptor:
        owner = repo.get("owner") or {}
        visibility, members, ref = self._acl(ctx, repo, self._host(ctx))
        full = str(repo["full_name"])
        return SourceDescriptor(
            source_type="repo", external_id=str(repo["id"]), name=full, parent_external_id=str(owner.get("login") or "") or None,
            visibility=visibility, member_ids=members, membership_ref=ref,
            approx_items=repo.get("open_issues_count") if isinstance(repo.get("open_issues_count"), int) else None,
            metadata={"full_name": full, "owner_type": owner.get("type"), "owner_id": owner.get("id"),
                      "visibility": str(repo.get("visibility") or ("private" if repo.get("private") else "public")),
                      "archived": bool(repo.get("archived")), "default_branch": repo.get("default_branch"), "html_url": repo.get("html_url")})

    async def discover_sources(self, ctx: ConnectorContext) -> AsyncIterator[SourceDescriptor]:
        self._validate_config(ctx)
        allow = set(ctx.config.get("repos") or ())
        per_page = 100 if not ctx.config.get("discover_page_size") else max(1, min(100, int(ctx.config["discover_page_size"])))
        installation = ctx.config.get("auth_mode") == "installation"
        if installation:
            url: str | None = "/installation/repositories"
            params: dict[str, Any] | None = {"per_page": per_page}
        elif ctx.config.get("org"):
            url, params = f"/orgs/{quote(str(ctx.config['org']))}/repos", {"type": "all", "sort": "full_name", "per_page": per_page}
        else:
            url, params = "/user/repos", {"sort": "full_name", "per_page": per_page}
        while url:
            if ctx.cancelled.is_set():
                return
            res = await self._get(ctx, url, params=params)
            repos = (res.json or {}).get("repositories", []) if installation else self._list(res)
            for repo in repos:
                if not isinstance(repo, dict) or repo.get("id") is None or not _FULL_NAME.match(str(repo.get("full_name") or "")):
                    continue
                if allow and repo["full_name"] not in allow:
                    continue
                if repo.get("has_issues") is False:            # GitHub answers 410 on the issue listing of such repositories
                    continue
                yield self._descriptor(ctx, repo)
            nxt = res.links.get("next")
            url, params = (self._relative(ctx, nxt), None) if nxt else (None, None)

    # ------------------------------------------------------------------ pull
    def _raw(self, ctx: ConnectorContext, source: SourceDescriptor, listing: str, item: Mapping[str, Any]) -> RawItem:
        return RawItem(object_type="issue" if listing == "issues" else "issue_comment", payload=item, source=source,
                       fetched_at=self._fetched(ctx))

    @staticmethod
    def _listing_path(full: str, listing: str) -> str:
        return f"/repos/{full}/issues" if listing == "issues" else f"/repos/{full}/issues/comments"

    @staticmethod
    def _listing_params(listing: str, since: str, per_page: int) -> dict[str, Any]:
        params: dict[str, Any] = {"sort": "updated", "direction": "asc", "since": since, "per_page": per_page}
        if listing == "issues":
            params["state"] = "all"                     # the default is open only
        return params

    @staticmethod
    def _max_updated(current: str | None, items: Iterable[Mapping[str, Any]]) -> str | None:
        ts = [t for t in (normalize_ts(i.get("updated_at")) for i in items) if t]
        return max([current] + ts if current else ts) if ts or current else None

    async def incremental_sync(self, ctx: ConnectorContext, source: SourceDescriptor, cursor: Cursor | None) -> AsyncIterator[Page]:
        """Issues, then comments, each ``since = high watermark − overlap`` in ascending ``updated`` order, following
        ``rel="next"``. A listing's watermark advances only when the listing was consumed to its end; mid-listing the cursor
        holds the next link's path, so a crash resumes there. The first page of a pass is conditional (``If-None-Match``):
        an unchanged repository costs two 304s and no primary rate limit. A first pass (no cursor) starts
        ``initial_lookback_days`` (default: one backfill window) before now; the backfill windows cover the rest."""
        full = self._full(source)
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        lookback = int(ctx.config.get("initial_lookback_days") or ctx.limits.backfill_window_days)
        first = iso_utc(ctx.clock() - timedelta(days=max(1, lookback)))
        hw = {"issues": str(data.get("issues_hw") or first), "comments": str(data.get("comments_hw") or first)}
        overlap = timedelta(seconds=int(ctx.config.get("overlap_seconds", OVERLAP_SECONDS)))
        stream = f"incr:{source.external_id}"
        per_page = self._per_page(ctx)
        start = data.get("phase") if data.get("phase") in LISTINGS else "issues"
        for listing in LISTINGS[LISTINGS.index(start):]:
            resume = data.get("next") if data.get("phase") == listing else None
            pass_max = data.get("pass_max") if resume else None
            url = resume or self._listing_path(full, listing)
            params = None if resume else self._listing_params(listing, gh_time(_dt(hw[listing]) - overlap), per_page)
            etag = None if resume else f"incr:{listing}:{source.external_id}"
            while True:
                if ctx.cancelled.is_set():
                    return
                res = await self._get(ctx, url, params=params, etag_key=etag)
                if res.not_modified:
                    break                                  # nothing changed since this exact request last succeeded
                items = self._list(res)
                raw = [self._raw(ctx, source, listing, it) for it in items]
                pass_max = self._max_updated(pass_max, items)
                nxt = res.links.get("next")
                if nxt:
                    url, params, etag = self._relative(ctx, nxt), None, None
                    yield Page(stream=stream, items=raw, has_more=True, rate=self._rate(ctx),
                               next_cursor=Cursor(CURSOR_VERSION, {"issues_hw": hw["issues"], "comments_hw": hw["comments"], "phase": listing,
                                                                   "next": url, "pass_max": pass_max}, min(hw.values())))
                    continue
                if pass_max and pass_max > hw[listing]:
                    hw[listing] = pass_max
                last = listing == LISTINGS[-1]
                yield Page(stream=stream, items=raw, has_more=not last, rate=self._rate(ctx),
                           next_cursor=Cursor(CURSOR_VERSION, {"issues_hw": hw["issues"], "comments_hw": hw["comments"],
                                                               "phase": "issues" if last else "comments", "next": None}, min(hw.values())))
                break
            data = {}

    async def initial_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, window: BackfillWindow,
                               cursor: Cursor | None) -> AsyncIterator[Page]:
        """Items whose ``updated_at`` lies in ``[window.start, window.end)``: ``since=start`` ascending, stopping at the first
        item updated at or after ``end`` (the horizon is defined on ``updated_at``; later changes belong to the incremental
        stream). Issues first, then comments; resumable from the stored next-link path."""
        full = self._full(source)
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        if data.get("phase") == "done":
            return
        lo, hi = normalize_ts(window.start), normalize_ts(window.end)
        if not lo or not hi:
            raise PermanentError("invalid backfill window", code="bad_window")
        per_page = self._per_page(ctx)
        start = data.get("phase") if data.get("phase") in LISTINGS else "issues"
        for listing in LISTINGS[LISTINGS.index(start):]:
            resume = data.get("next") if data.get("phase") == listing else None
            url = resume or self._listing_path(full, listing)
            params = None if resume else self._listing_params(listing, gh_time(_dt(lo)), per_page)
            while True:
                if ctx.cancelled.is_set():
                    return
                res = await self._get(ctx, url, params=params)
                items = self._list(res)
                stamps = [normalize_ts(it.get("updated_at")) or "" for it in items]
                inside = [it for it, t in zip(items, stamps) if lo <= t < hi]
                past_end = any(t >= hi for t in stamps)
                raw = [self._raw(ctx, source, listing, it) for it in inside]
                nxt = res.links.get("next")
                if nxt and not past_end:
                    url, params = self._relative(ctx, nxt), None
                    yield Page(stream=window.stream, items=raw, has_more=True, rate=self._rate(ctx),
                               next_cursor=Cursor(CURSOR_VERSION, {"phase": listing, "next": url}))
                    continue
                last = listing == LISTINGS[-1]
                yield Page(stream=window.stream, items=raw, has_more=not last, rate=self._rate(ctx),
                           next_cursor=Cursor(CURSOR_VERSION, {"phase": "done" if last else "comments", "next": None}, hi if last else None))
                break
            data = {}

    # ------------------------------------------------------------------ webhooks (notify, then fetch)
    @classmethod
    def verify_webhook(cls, headers: Mapping[str, str], body: bytes, secret: bytes, *, now: float) -> bool:
        """``X-Hub-Signature-256: sha256=<hex HMAC-SHA256(secret, raw body)>``, constant-time; the legacy SHA-1 header is
        never accepted. GitHub signs no timestamp: replays are stopped by the ``X-GitHub-Delivery`` dedupe."""
        sig = _header(headers, "x-hub-signature-256")
        if not sig.startswith("sha256=") or not secret:
            return False
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.encode("ascii"), sig.encode("ascii", errors="replace"))

    @classmethod
    def parse_webhook(cls, headers: Mapping[str, str], body: bytes) -> list[WebhookNotice]:
        """Ids only (§3.7): ``repository.id``, ``issue.number``, ``issue.id``, ``comment.id``, the action and the delivery id
        (``X-GitHub-Delivery``, the dedupe key). Titles, bodies, names and logins are dropped here. ``issues.transferred``
        yields two notices (old repository, new repository) with delivery ids ``<guid>`` and ``<guid>:new``."""
        event = _header(headers, "x-github-event")
        delivery = _header(headers, "x-github-delivery")[:100]
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return []
        if not isinstance(data, dict) or not delivery or event in ("", "ping"):
            return []
        action = str(data.get("action") or "")[:40]
        repo = data.get("repository") if isinstance(data.get("repository"), dict) else {}
        installation = data.get("installation") if isinstance(data.get("installation"), dict) else {}
        account = _id(installation.get("id")) or _id((repo.get("owner") or {}).get("id")) or ""
        repo_id = _id(repo.get("id"))
        out: list[WebhookNotice] = []

        def notice(source_id: str, refs: tuple[dict[str, str], ...], *, suffix: str = "", occurred: Any = None) -> None:
            out.append(WebhookNotice(connector_type=cls.manifest.connector_type, delivery_id=delivery + suffix, external_account_id=account,
                                     source_external_id=source_id, action=f"{event}.{action}" if action else event, object_refs=refs,
                                     occurred_at=normalize_ts(occurred)))

        if event == "issues" and repo_id:
            issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
            number, issue_id = _id(issue.get("number")), _id(issue.get("id"))
            if not number:
                return []
            kind = "pull_request" if issue.get("pull_request") else "issue"
            notice(repo_id, ({"type": kind, "id": f"{repo_id}#{number}", "number": number, "issue_id": issue_id or ""},),
                   occurred=None if action == "deleted" else issue.get("updated_at"))
            changes = data.get("changes") if isinstance(data.get("changes"), dict) else {}
            if action == "transferred":
                new_issue = changes.get("new_issue") if isinstance(changes.get("new_issue"), dict) else {}
                new_repo = changes.get("new_repository") if isinstance(changes.get("new_repository"), dict) else {}
                nrid, nnum = _id(new_repo.get("id")), _id(new_issue.get("number"))
                if nrid and nnum:
                    notice(nrid, ({"type": "issue", "id": f"{nrid}#{nnum}", "number": nnum, "issue_id": _id(new_issue.get("id")) or ""},),
                           suffix=":new", occurred=new_issue.get("updated_at"))
        elif event == "issue_comment" and repo_id:
            comment = data.get("comment") if isinstance(data.get("comment"), dict) else {}
            issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
            cid, number = _id(comment.get("id")), _id(issue.get("number"))
            if not cid:
                return []
            notice(repo_id, ({"type": "issue_comment", "id": cid, "number": number or ""},),
                   occurred=None if action == "deleted" else comment.get("updated_at"))
        elif event == "github_app_authorization":
            sender = _id((data.get("sender") or {}).get("id"))
            notice("", ({"type": "user", "id": sender},) if sender else ())
        elif event == "installation" and _id(installation.get("id")):
            notice("", ({"type": "installation", "id": _id(installation.get("id"))},))
        elif event == "installation_repositories" and action == "removed":
            for i, r in enumerate(data.get("repositories_removed") or []):
                rid = _id((r or {}).get("id")) if isinstance(r, dict) else None
                if rid:
                    notice(rid, ({"type": "repository", "id": rid},), suffix=f":{i}" if i else "")
        return out

    async def _repo_by_id(self, ctx: ConnectorContext, repo_id: str) -> dict[str, Any]:
        """The container by its stable id. Unreadable (404/410/403) means access was lost, never that content was deleted."""
        if not _ID.match(repo_id or ""):
            raise PermanentError("invalid repository id", code="bad_notice")
        try:
            res = await self._get(ctx, f"/repositories/{repo_id}")
        except (ObjectGone, InsufficientScope):
            raise SourceUnavailable("access_lost", detail={"source": repo_id}) from None
        if not isinstance(res.json, dict) or not _FULL_NAME.match(str(res.json.get("full_name") or "")):
            raise PermanentError("unexpected repository response", code="bad_response")
        return res.json

    def _connection_notice(self, ctx: ConnectorContext, notice: WebhookNotice) -> None:
        ids = {str(r.get("id")) for r in notice.object_refs}
        if notice.action == "github_app_authorization.revoked" and ctx.auth_account_id in ids:
            raise AuthRevoked("the owner revoked the GitHub App authorization")
        if notice.action in ("installation.deleted", "installation.suspend") and str(ctx.config.get("installation_id") or "") in ids:
            raise AuthRevoked("the GitHub App installation was removed or suspended")

    async def handle_webhook(self, ctx: ConnectorContext, notice: WebhookNotice) -> AsyncIterator[Page]:
        """Fetch the authoritative state of each referenced object with the owner's token. ``404``/``410`` while the
        repository (fetched first, by id) is readable confirm a deletion; ``301`` is a transfer (reason ``moved``); an
        unreadable repository raises ``SourceUnavailable(access_lost)`` and nothing is deleted."""
        if not notice.source_external_id:
            self._connection_notice(ctx, notice)
            return
        repo = await self._repo_by_id(ctx, notice.source_external_id)
        source = self._descriptor(ctx, repo)
        full = str(repo["full_name"])
        fetched = self._fetched(ctx)
        items: list[RawItem] = []
        for ref in notice.object_refs:
            kind = str(ref.get("type") or "")
            if kind in ("issue", "pull_request"):
                number = str(ref.get("number") or "")
                if not _ID.match(number):
                    continue
                try:
                    res = await self._get(ctx, f"/repos/{full}/issues/{number}")
                    if isinstance(res.json, dict):
                        items.append(self._raw(ctx, source, "issues", res.json))
                except ObjectGone as gone:
                    moved = 300 <= gone.status < 400
                    oid = f"{source.external_id}#{number}"
                    for otype in (kind, "conversation"):
                        items.append(RawItem("deletion", {"object_type": otype, "object_id": oid, "status": gone.status,
                                                          "reason": "moved" if moved else "deleted_at_source",
                                                          "moved_to": urlsplit(gone.moved_to).path if (moved and gone.moved_to) else None},
                                             source, fetched))
            elif kind == "issue_comment":
                cid = str(ref.get("id") or "")
                if not _ID.match(cid):
                    continue
                try:
                    res = await self._get(ctx, f"/repos/{full}/issues/comments/{cid}")
                    if isinstance(res.json, dict):
                        items.append(self._raw(ctx, source, "comments", res.json))
                except ObjectGone as gone:
                    items.append(RawItem("deletion", {"object_type": "issue_comment", "object_id": cid, "status": gone.status,
                                                      "reason": "deleted_at_source"}, source, fetched))
        if items or notice.object_refs:
            yield Page(stream="webhook", items=items, next_cursor=None, has_more=False, rate=self._rate(ctx))

    # ------------------------------------------------------------------ normalize (pure)
    def normalize(self, raw: RawItem, ctx: ConnectorContext) -> list[CanonicalEvent]:
        p = raw.payload
        if not isinstance(p, dict):
            raise PermanentError("invalid GitHub item", code="normalize_failed")
        src = raw.source
        full = str(src.metadata.get("full_name") or src.name)
        perms = Permissions(src.visibility if src.visibility in VISIBILITIES else "private", tuple(src.member_ids), src.membership_ref)
        common: dict[str, Any] = dict(tenant_id=ctx.tenant_id, holder_id=ctx.holder_id, connector_id=ctx.connector_id, source_app=ctx.source_app,
                                      source_account_id=ctx.source_account_id, observed_at=raw.fetched_at, permissions=perms)
        try:
            if raw.object_type == "deletion":
                hints = {"reason": str(p.get("reason") or "deleted_at_source"), "repo": full, "status": p.get("status")}
                if p.get("moved_to"):
                    hints["moved_to"] = str(p["moved_to"])
                return [CanonicalEvent.create(kind="deletion", source_object_type=str(p["object_type"]), source_object_id=str(p["object_id"]),
                                              updated_at=raw.fetched_at, tiebreak="deleted", hints=hints, **common)]
            if raw.object_type == "issue":
                return self._issue_events(p, src, full, common)
            if raw.object_type == "issue_comment":
                return self._comment_events(p, src, full, common)
        except (KeyError, TypeError, ValueError):
            raise PermanentError("malformed GitHub item", code="normalize_failed", detail={"object_type": raw.object_type}) from None
        raise PermanentError("unknown GitHub item type", code="normalize_failed", detail={"object_type": raw.object_type})

    @staticmethod
    def _user(p: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
        user = p.get("user") if isinstance(p.get("user"), dict) else {}
        return (str(user["id"]) if user.get("id") is not None else None), user

    def _issue_events(self, p: Mapping[str, Any], src: SourceDescriptor, full: str, common: dict[str, Any]) -> list[CanonicalEvent]:
        number = int(p["number"])
        oid = f"{src.external_id}#{number}"
        kind = "pull_request" if p.get("pull_request") else "issue"
        title = str(p.get("title") or "")
        body = _HTML_COMMENT.sub("", str(p.get("body") or ""))
        author, user = self._user(p)
        assignees = [str(a["id"]) for a in p.get("assignees") or [] if isinstance(a, dict) and a.get("id") is not None]
        labels = sorted({str(lb.get("name") if isinstance(lb, dict) else lb) for lb in p.get("labels") or [] if lb})
        updated, created = p.get("updated_at"), p.get("created_at")
        version = normalize_ts(updated) or ""
        hints = {"repo": full, "container_name": f"{full}#{number}", "container_kind": "issue_thread", "labels": labels, "state": p.get("state"),
                 "number": number, "issue_id": str(p.get("id") or ""), "author_login": user.get("login"), "is_bot": user.get("type") == "Bot",
                 "html_url": p.get("html_url"), "pull_request": kind == "pull_request"}
        conv = CanonicalEvent.create(kind="conversation", source_object_type="conversation", source_object_id=oid, source_version=version,
                                     created_at=created, updated_at=updated, title=f"{full}#{number} {title}".strip(), body="", hints=hints, **common)
        msg = CanonicalEvent.create(kind="message", source_object_type=kind, source_object_id=oid, source_version=version, conversation_id=oid,
                                    author_id=author, participant_ids=[a for a in [author, *assignees] if a], created_at=created, updated_at=updated,
                                    title=title, body=body, content_type="text/markdown", hints=hints, links=self._links(body, src, full), **common)
        return [conv, msg]

    def _comment_events(self, p: Mapping[str, Any], src: SourceDescriptor, full: str, common: dict[str, Any]) -> list[CanonicalEvent]:
        number = _issue_number(p.get("issue_url"))
        thread = f"{src.external_id}#{number}"
        body = _HTML_COMMENT.sub("", str(p.get("body") or ""))
        if not body.strip():
            return []
        author, user = self._user(p)
        hints = {"repo": full, "container_name": f"{full}#{number}", "container_kind": "issue_thread", "number": number,
                 "author_login": user.get("login"), "is_bot": user.get("type") == "Bot", "html_url": p.get("html_url")}
        return [CanonicalEvent.create(kind="message", source_object_type="issue_comment", source_object_id=str(p["id"]),
                                      source_version=normalize_ts(p.get("updated_at")) or "", conversation_id=thread, parent_message_id=thread,
                                      author_id=author, participant_ids=[author] if author else [], created_at=p.get("created_at"),
                                      updated_at=p.get("updated_at"), title="", body=body, content_type="text/markdown", hints=hints,
                                      links=self._links(body, src, full), **common)]

    @staticmethod
    def _links(body: str, src: SourceDescriptor, full: str) -> list[str]:
        """URLs in the text plus issue references (``#482``, ``acme/platform#12``) as canonical issue URLs (§8.2)."""
        out = extract_urls(body)
        repo_url = str(src.metadata.get("html_url") or f"https://github.com/{full}").rstrip("/")
        site = repo_url[: -len(full) - 1] if repo_url.endswith("/" + full) else "https://github.com"
        for m in _ISSUE_REF.finditer(body or ""):
            owner, name, num = m.groups()
            url = f"{site}/{owner}/{name}/issues/{num}" if owner else f"{repo_url}/issues/{num}"
            if url not in out:
                out.append(url)
        return out


def _header(headers: Mapping[str, str], name: str) -> str:
    for k, v in headers.items():
        if k.lower() == name:
            return str(v)
    return ""


def _id(value: Any) -> str | None:
    s = str(value) if value is not None and not isinstance(value, bool) else ""
    return s if _ID.match(s) else None


def _issue_number(issue_url: Any) -> int:
    tail = str(issue_url or "").rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit():
        raise ValueError("comment without an issue number")
    return int(tail)


def _dt(iso: str) -> datetime:
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


__all__ = ["API_VERSION", "GitHubConnector"]
