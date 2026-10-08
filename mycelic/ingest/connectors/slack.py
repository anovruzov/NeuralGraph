"""Slack connector: public and private channels (and, only when the owner opts in, DMs) through the Web API with a user
token, plus the Events API for live edits and deletions (INGESTION.md §11.5).

Status ``tested-offline``: exercised end to end against the mock in :mod:`mycelic.ingest.mocks.slack_mock`, which models
Slack's documented behaviour (HTTP 200 with ``ok:false`` for most errors, 429 + ``Retry-After``, cursor pagination,
``v0`` request signing). It has not been run against slack.com by this code base, and Slack's documentation was not
reachable when it was written (the facts come from the official ``python-slack-sdk`` and Slack's changelog); re-verify
before relying on it.

Methods: ``auth.test`` (connect), ``conversations.list`` (discover; ``public_channel,private_channel``, plus ``im,mpim`` only
with ``config.include_dms``), ``conversations.members`` (private channel ACLs), ``conversations.history`` and
``conversations.replies`` (cursor pagination through ``response_metadata.next_cursor``), ``conversations.info`` (the
container of a notice), ``auth.revoke`` (disconnect), ``oauth.v2.access`` (code + PKCE exchange, token rotation).
``users.info`` is never needed: authors are kept as ids and mapped through ``config.principal_map``.

Rate limits. ``config.slack_app_class`` ∈ ``internal | marketplace | distributed`` (default ``distributed``, the
conservative choice). Since Slack's 2025 change, a *commercially distributed app that is not in the Slack Marketplace* may
call ``conversations.history`` / ``conversations.replies`` once per minute with ``limit ≤ 15``; for that class this
connector enforces both itself (one call per minute per workspace and method, in this process; a longer wait raises
``RateLimited`` so the stream is parked and resumed from its cursor). Internal and Marketplace apps use Slack's tiers and
``Retry-After``. Every request's result is committed before the next request (thread replies are a work list in the
cursor), so even at one call per minute a sync always makes progress.

What pull does not see: an edit or deletion of an older message, and a new reply to a thread whose parent is older than
the incremental window (``conversations.history`` returns neither). The Events API (``message_changed``,
``message_deleted``, ``thread_broadcast``, ``message``) delivers those; with webhooks off they are missed until a backfill
window covering them runs again.

Identity: ``source_account_id = <enterprise_id or ''>/<team_id>``; message ``<channel_id>:<ts>``; ``thread_id =
thread_ts``; ``source_version = edited.ts or ts`` (also the order-key tiebreak). ACL: public channel → ``public`` (the
workspace); private channel → ``members`` (``conversations.members`` mapped to Mycelic principals, plus a
``membership_ref``); DM / group DM → ``private`` with the participants (never exportable until the owner opts in).

Configuration (non-secret)::

    {"slack_app_class": "internal", "include_dms": false, "principal_map": {"U0ANA": "usr_ana"},
     "api_base": "https://slack.com/api", "overlap_seconds": 120, "initial_lookback_days": 30, "max_inline_wait_seconds": 30}
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import re
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping

from ..contract import (AuthExpired, AuthRevoked, AuthStart, BackfillWindow, Capabilities, ConnectorContext, ConnectorError, ConnectorManifest,
                        ConnectResult, Connector, Credentials, Cursor, CursorInvalid, HealthReport, InsufficientScope, ObjectGone, Page,
                        PermanentError, RateLimited, RateLimitSpec, RawItem, ScopeSpec, Secret, SourceDescriptor, SourceUnavailable,
                        TransientError, WebhookNotice)
from ..events import VISIBILITIES, AttachmentRef, CanonicalEvent, Permissions, normalize_ts
from ..http import ConnectorHttpClient
from ..oauth import OAuthAppConfig, authorize_url, oauth_app, pkce_pair, require_code
from ._provider import extract_urls, iso_utc, principals, refresh_once

DEFAULT_API = "https://slack.com/api"
DEFAULT_OAUTH = "https://slack.com"
CURSOR_VERSION = 1
OVERLAP_SECONDS = 120
SIGNATURE_TOLERANCE_SECONDS = 300
APP_CLASSES = ("internal", "marketplace", "distributed")
DEFAULT_APP_CLASS = "distributed"
DISTRIBUTED_LIMIT = 15                 # Slack 2025: limit <= 15 for non-Marketplace distributed apps
DISTRIBUTED_INTERVAL = 60.0            # ... and 1 request per minute for these methods
PACED_METHODS = frozenset({"conversations.history", "conversations.replies"})
STANDARD_LIMIT = 200
DEFAULT_USER_SCOPES = ("channels:read", "channels:history", "groups:read", "groups:history")
REQUIRED_SCOPES = ("channels:read", "channels:history")
KEPT_SUBTYPES = frozenset({"", "thread_broadcast", "bot_message", "me_message", "file_share"})
_ID = re.compile(r"^[A-Z0-9]{2,32}$")
_TS = re.compile(r"^\d{1,12}\.\d{1,9}$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_\-.]{1,200}$")
_LINK_LABEL = re.compile(r"<(https?://[^|>\s]+)\|([^>]*)>")
_LINK_BARE = re.compile(r"<(https?://[^|>\s]+)>")
_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]*))?>")
_SPECIAL = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_AUTH_EXPIRED = frozenset({"not_authed", "invalid_auth", "token_expired"})
_AUTH_REVOKED = frozenset({"token_revoked", "account_inactive", "team_disabled", "user_removed_from_team"})
_ACCESS_LOST = frozenset({"channel_not_found", "not_in_channel"})
_GONE = frozenset({"thread_not_found", "message_not_found"})
_TRANSIENT = frozenset({"internal_error", "fatal_error", "service_unavailable", "request_timeout"})

# last call per (workspace, method), per event loop: Slack's per-method limits are per app and workspace, shared by every
# connection of this process to that workspace
_PACERS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, float]]" = weakref.WeakKeyDictionary()


def _pacers() -> dict[str, float]:
    loop = asyncio.get_running_loop()
    d = _PACERS.get(loop)
    if d is None:
        d = _PACERS[loop] = {}
    return d


def slack_error(error: str, method: str, data: Mapping[str, Any]) -> ConnectorError:
    """Slack's ``ok:false`` error codes (fixed provider strings, never content) as contract errors."""
    detail = {"slack_error": error[:60], "method": method}
    if error in _AUTH_EXPIRED:
        return AuthExpired("Slack rejected the token", detail=detail)
    if error in _AUTH_REVOKED:
        return AuthRevoked("the Slack grant was revoked", detail=detail)
    if error == "missing_scope":
        return InsufficientScope(missing=tuple(s for s in str(data.get("needed") or "").split(",") if s), detail=detail)
    if error in _ACCESS_LOST:
        return SourceUnavailable("access_lost", detail=detail)
    if error in _GONE:
        return ObjectGone(method, status=404, detail=detail)
    if error == "invalid_cursor":
        return CursorInvalid("Slack refused the stored cursor", detail=detail)
    if error == "ratelimited":
        return RateLimited(DISTRIBUTED_INTERVAL, scope="endpoint", detail=detail)
    if error in _TRANSIENT:
        return TransientError("Slack is unavailable", detail=detail)
    return PermanentError("Slack refused the request", code="slack_error", detail=detail)


class SlackConnector(Connector):
    manifest = ConnectorManifest(
        connector_type="slack",
        display_name="Slack (channels and threads)",
        version="1.0.0",
        status="tested-offline",
        auth_kinds=("oauth2", "pat"),
        scopes=(
            ScopeSpec("channels:read", True, "List the public channels you can see."),
            ScopeSpec("channels:history", True, "Read messages and threads of the public channels you select."),
            ScopeSpec("groups:read", False, "List the private channels you are a member of, and their members (for access control)."),
            ScopeSpec("groups:history", False, "Read the private channels you select; their records stay restricted to the channel's members."),
            ScopeSpec("users:read", False, "Not used by default; authors are kept as ids."),
            ScopeSpec("im:read / mpim:read", False, "Only if you opt into direct messages (off by default)."),
            ScopeSpec("im:history / mpim:history", False, "Only if you opt into direct messages; they stay private and are never "
                                                          "exported unless you allow it per conversation."),
        ),
        modes=frozenset({"pull", "webhook"}),
        source_types=("channel", "dm", "mpim"),
        capabilities=Capabilities(edits=True, deletes="webhook", threads=True, attachments=False, acl="full", exports=False),
        rate_limit=RateLimitSpec(kind="tiered", default_rps=0.3, burst=1, serial_per_token=True),
        allowed_hosts=("slack.com",),
        default_poll_seconds=300,
        terms_notes=("Reads Slack through the Web API with a user token, so it sees what you can read. Rate limits depend on the app "
                     "class you configure: a commercially distributed app that is not in the Slack Marketplace may read history and "
                     "threads only once per minute, 15 messages at a time (Slack's 2025 change); this connector enforces that, which "
                     "makes history backfills very slow for such apps. Internal and Marketplace apps follow Slack's documented tiers. "
                     "Slack's API Terms of Service restrict some uses of data obtained through the API (they were tightened in 2025); "
                     "this connector does not and cannot check those terms. Before enabling it, the workspace owner must confirm that "
                     "their app's terms with Slack permit storing, indexing and analysing this data in Mycelic. Direct messages are not "
                     "read unless you opt in. File contents are never downloaded. Status: tested offline against a mock of the "
                     "documented API; not live-verified."),
        ownership=("personal", "org"),
    )

    def __init__(self, oauth: OAuthAppConfig | None = None, *, sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        self._oauth = oauth
        self._sleep = sleep or asyncio.sleep

    @property
    def oauth(self) -> OAuthAppConfig | None:
        return self._oauth or oauth_app(self.manifest.connector_type)

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def _api(ctx: ConnectorContext) -> str:
        return str(ctx.config.get("api_base") or DEFAULT_API).rstrip("/")

    @staticmethod
    def app_class(ctx: ConnectorContext) -> str:
        return str(ctx.config.get("slack_app_class") or DEFAULT_APP_CLASS)

    def _limit(self, ctx: ConnectorContext) -> int:
        cap = DISTRIBUTED_LIMIT if self.app_class(ctx) == "distributed" else STANDARD_LIMIT
        return max(1, min(cap, int(ctx.limits.page_size)))

    @staticmethod
    def _rate(ctx: ConnectorContext) -> dict[str, Any]:
        snap = getattr(ctx.http, "rate_snapshot", None)
        return snap() if callable(snap) else {}

    async def _pace(self, ctx: ConnectorContext, method: str) -> None:
        """Client-side enforcement of the distributed-app limit: one call per minute per workspace and method."""
        if self.app_class(ctx) != "distributed" or method not in PACED_METHODS:
            return
        key = f"{ctx.source_account_id}|{method}"
        pacers = _pacers()
        now = ctx.clock().timestamp()
        last = pacers.get(key)
        if last is not None and now < last + DISTRIBUTED_INTERVAL:
            wait = last + DISTRIBUTED_INTERVAL - now
            if wait > float(ctx.config.get("max_inline_wait_seconds", 30)):
                raise RateLimited(wait, scope="endpoint", detail={"method": method, "app_class": "distributed"})
            await self._sleep(wait)
            now = ctx.clock().timestamp()
        pacers[key] = now

    async def _call_once(self, ctx: ConnectorContext, method: str, params: Mapping[str, Any] | None) -> tuple[dict[str, Any], Any]:
        await self._pace(ctx, method)
        res = await ctx.http.request("GET", f"{self._api(ctx)}/{method}", params=params, expected=(200,))
        data = res.json
        if not isinstance(data, dict):
            raise PermanentError("unexpected Slack response", code="bad_response", detail={"method": method})
        if data.get("ok") is True:
            return data, res
        raise slack_error(str(data.get("error") or "unknown"), method, data)     # Slack answers most errors with HTTP 200

    async def _call(self, ctx: ConnectorContext, method: str, params: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], Any]:
        try:
            return await self._call_once(ctx, method, params)
        except AuthExpired:
            if await refresh_once(ctx, self.refresh_credentials if self.oauth else None):
                return await self._call_once(ctx, method, params)
            raise

    def _validate_config(self, ctx: ConnectorContext) -> None:
        if self.app_class(ctx) not in APP_CLASSES:
            raise PermanentError("slack_app_class must be internal, marketplace or distributed", code="bad_config")
        if ctx.config.get("principal_map") is not None and not isinstance(ctx.config.get("principal_map"), dict):
            raise PermanentError("principal_map must be an object", code="bad_config")

    @staticmethod
    def _fetched(ctx: ConnectorContext) -> str:
        return iso_utc(ctx.clock())

    # ------------------------------------------------------------------ OAuth v2 (user token, code + PKCE)
    def _require_oauth(self) -> OAuthAppConfig:
        app = self.oauth
        if app is None:
            raise PermanentError("no Slack app is configured", code="oauth_not_configured")
        return app

    async def authorize(self, *, tenant_id: str, holder_id: str, redirect_uri: str, state: str) -> AuthStart:
        app = self.oauth
        if app is None:
            return AuthStart(kind="token_entry", instructions=("Paste a user token (xoxp-) of a Slack app installed in your workspace with the "
                                                                 "user scopes channels:read, channels:history and, for private channels, "
                                                                 "groups:read and groups:history."))
        verifier, challenge = pkce_pair()
        scopes = tuple(app.scopes) if app.scopes is not None else DEFAULT_USER_SCOPES
        url = authorize_url((app.oauth_base or DEFAULT_OAUTH), "/oauth/v2/authorize",
                            {"client_id": app.client_id, "user_scope": ",".join(scopes), "redirect_uri": redirect_uri, "state": state,
                             "code_challenge": challenge, "code_challenge_method": "S256"},
                            allowed_hosts=self.manifest.allowed_hosts, allow_loopback_http=app.allow_loopback_http)
        return AuthStart(kind="redirect", url=url, instructions="Approve read access in Slack.", pkce_verifier=Secret(verifier))

    async def complete_authorization(self, params: Mapping[str, str], *, redirect_uri: str, pkce_verifier: Secret | None) -> Credentials:
        app = self._require_oauth()
        form: dict[str, Any] = {"client_id": app.client_id, "code": require_code(params), "redirect_uri": redirect_uri}
        if app.client_secret is not None:
            form["client_secret"] = app.client_secret
        if pkce_verifier is not None:
            form["code_verifier"] = pkce_verifier
        return await self._token_request(app, form)

    async def refresh_credentials(self, creds: Credentials) -> Credentials:
        """Token rotation: user tokens of apps with rotation enabled expire; ``oauth.v2.access`` with the refresh token."""
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
            res = await http.request("POST", (app.api_base or DEFAULT_API).rstrip("/") + "/oauth.v2.access", form=form, auth=False)
        finally:
            await http.close()
        data = res.json if isinstance(res.json, dict) else {}
        if data.get("ok") is not True:
            raise PermanentError("the token exchange was refused", code=f"oauth_{str(data.get('error') or 'refused')[:40]}")
        # a user-token install answers in authed_user; a rotation refresh answers at the top level
        user = data.get("authed_user") if isinstance(data.get("authed_user"), dict) and data["authed_user"].get("access_token") else data
        token = user.get("access_token")
        if not token:
            raise PermanentError("the token exchange returned no user token", code="oauth_no_user_token")
        expires_at = iso_utc(datetime.now(timezone.utc) + timedelta(seconds=int(user["expires_in"]))) if user.get("expires_in") else None
        team = data.get("team") if isinstance(data.get("team"), dict) else {}
        extra = dict(previous.extra) if previous else {}
        extra.update({k: str(v) for k, v in (("token_type", user.get("token_type") or "user"), ("scope", user.get("scope")),
                                             ("team_id", team.get("id")), ("user_id", user.get("id"))) if v})
        return Credentials(kind="oauth2", access_token=Secret(str(token)),
                           refresh_token=Secret(str(user["refresh_token"])) if user.get("refresh_token") else None, expires_at=expires_at,
                           extra=extra)

    # ------------------------------------------------------------------ lifecycle
    async def connect(self, ctx: ConnectorContext) -> ConnectResult:
        """``auth.test``: workspace, enterprise and user ids, and the token's scopes (``x-oauth-scopes``). No content."""
        self._validate_config(ctx)
        data, res = await self._call(ctx, "auth.test")
        team, user = str(data.get("team_id") or ""), str(data.get("user_id") or "")
        if not _ID.match(team) or not _ID.match(user):
            raise PermanentError("unexpected auth.test response", code="bad_response")
        enterprise = str(data.get("enterprise_id") or "")
        header = res.headers.get("x-oauth-scopes")
        granted = tuple(sorted({s.strip() for s in (header or "").split(",") if s.strip()}))
        warnings: list[str] = []
        if header is not None:
            missing = tuple(s for s in REQUIRED_SCOPES if s not in granted)
            if missing:
                raise InsufficientScope(missing=missing, detail={"missing": ",".join(missing)})
            if ctx.config.get("include_dms") and not {"im:read", "im:history"} <= set(granted):
                warnings.append("direct messages were opted in but the token lacks im:read / im:history; they will not be listed")
        if data.get("bot_id"):
            warnings.append("this is a bot token: it sees only channels the bot joined; a user token reflects what you can read")
        if self.app_class(ctx) == "distributed":
            warnings.append("distributed (non-Marketplace) app: history and threads are read once per minute, 15 messages at a time")
        return ConnectResult(source_account_id=f"{enterprise}/{team}", auth_account_id=user,
                             account_label=f"{data.get('team') or team} / {data.get('user') or user}", granted_scopes=granted,
                             warnings=tuple(warnings))

    async def health(self, ctx: ConnectorContext) -> HealthReport:
        try:
            await self._call(ctx, "auth.test")
        except AuthRevoked as exc:
            return HealthReport("revoked", {"auth": False, "api_reachable": True}, self._rate(ctx), exc.code)
        except AuthExpired as exc:
            return HealthReport("auth_expired", {"auth": False, "api_reachable": True}, self._rate(ctx), exc.code)
        except RateLimited as exc:
            return HealthReport("rate_limited", {"auth": True, "api_reachable": True}, {"retry_after": exc.retry_after}, exc.code)
        except TransientError as exc:
            return HealthReport("degraded", {"auth": True, "api_reachable": False}, {}, exc.code)
        except ConnectorError as exc:
            return HealthReport("error", {"auth": False, "api_reachable": True}, {}, exc.code)
        return HealthReport("ok", {"auth": True, "api_reachable": True}, {"app_class": self.app_class(ctx)})

    async def disconnect(self, ctx: ConnectorContext, *, revoke_at_provider: bool) -> None:
        if revoke_at_provider:
            await self._call(ctx, "auth.revoke")

    # ------------------------------------------------------------------ sources
    async def _members(self, ctx: ConnectorContext, channel: str) -> list[str]:
        out: list[str] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"channel": channel, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data, _ = await self._call(ctx, "conversations.members", params)
            out += [str(m) for m in data.get("members") or [] if _ID.match(str(m))]
            cursor = str((data.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                return out

    async def _descriptor(self, ctx: ConnectorContext, ch: Mapping[str, Any], *, with_members: bool) -> SourceDescriptor | None:
        cid = str(ch.get("id") or "")
        if not _ID.match(cid):
            return None
        meta = {"is_private": bool(ch.get("is_private")), "is_archived": bool(ch.get("is_archived")), "is_member": bool(ch.get("is_member")),
                "created": ch.get("created") if isinstance(ch.get("created"), int) else None}
        ref = f"slack:{ctx.source_account_id}:{cid}"
        if ch.get("is_im"):
            other = str(ch.get("user") or "")
            return SourceDescriptor(source_type="dm", external_id=cid, name=f"DM {other}", visibility="private",
                                    member_ids=principals(ctx, "slack", [other, ctx.auth_account_id]), metadata={**meta, "is_im": True})
        name = str(ch.get("name") or cid)
        if ch.get("is_mpim"):
            members = await self._members(ctx, cid) if with_members else []
            return SourceDescriptor(source_type="mpim", external_id=cid, name=name, visibility="private",
                                    member_ids=principals(ctx, "slack", members), membership_ref=ref, metadata={**meta, "is_mpim": True})
        if ch.get("is_private") or ch.get("is_group"):
            members = await self._members(ctx, cid) if with_members else []
            return SourceDescriptor(source_type="channel", external_id=cid, name=f"#{name}", visibility="members",
                                    member_ids=principals(ctx, "slack", members), membership_ref=ref, metadata=meta)
        return SourceDescriptor(source_type="channel", external_id=cid, name=f"#{name}", visibility="public", metadata=meta)

    async def discover_sources(self, ctx: ConnectorContext) -> AsyncIterator[SourceDescriptor]:
        self._validate_config(ctx)
        types = "public_channel,private_channel" + (",mpim,im" if ctx.config.get("include_dms") else "")
        cursor = ""
        while True:
            if ctx.cancelled.is_set():
                return
            params: dict[str, Any] = {"types": types, "exclude_archived": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data, _ = await self._call(ctx, "conversations.list", params)
            for ch in data.get("channels") or []:
                if isinstance(ch, dict):
                    desc = await self._descriptor(ctx, ch, with_members=True)
                    if desc is not None:
                        yield desc
            cursor = str((data.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                return

    # ------------------------------------------------------------------ pull
    def _raw_message(self, ctx: ConnectorContext, source: SourceDescriptor, m: Mapping[str, Any]) -> RawItem:
        return RawItem(object_type="message", payload=m, source=source, fetched_at=self._fetched(ctx))

    @staticmethod
    def _is_parent(m: Mapping[str, Any]) -> bool:
        return bool(m.get("thread_ts")) and m.get("thread_ts") == m.get("ts") and int(m.get("reply_count") or 0) > 0

    async def _run_pass(self, ctx: ConnectorContext, source: SourceDescriptor, st: dict[str, Any], *, stream: str, hw: str | None,
                        window_end: float | None, final: Callable[[str | None], Cursor]) -> AsyncIterator[Page]:
        """One pass over a channel as a small work list kept in the cursor: history pages (newest first), then the replies
        of every thread parent seen, one request per committed page. ``st`` is the in-progress pass state."""
        cid = source.external_id
        limit = self._limit(ctx)
        while True:
            if ctx.cancelled.is_set():
                return
            items: list[RawItem] = []
            if not st.get("started"):
                items.append(RawItem(object_type="conversation", payload={"id": cid}, source=source, fetched_at=self._fetched(ctx)))
                st["started"] = True
            if st["threads"]:
                parent = st["threads"][0]
                params: dict[str, Any] = {"channel": cid, "ts": parent, "limit": limit}
                if st.get("thread_cursor"):
                    params["cursor"] = st["thread_cursor"]
                try:
                    data, _ = await self._call(ctx, "conversations.replies", params)
                    msgs = [m for m in data.get("messages") or [] if isinstance(m, dict) and _TS.match(str(m.get("ts") or ""))]
                    items += [self._raw_message(ctx, source, m) for m in msgs if m.get("ts") != parent]
                    nxt = str((data.get("response_metadata") or {}).get("next_cursor") or "")
                except ObjectGone:                         # the thread vanished between the history page and now
                    nxt = ""
                if nxt:
                    st["thread_cursor"] = nxt
                else:
                    st["threads"].pop(0)
                    st["thread_cursor"] = None
            elif not st["history_done"]:
                params = {"channel": cid, "limit": limit, "oldest": st["oldest"], "inclusive": "true"}
                if st.get("latest"):
                    params["latest"] = st["latest"]
                if st.get("cursor"):
                    params["cursor"] = st["cursor"]
                data, _ = await self._call(ctx, "conversations.history", params)
                msgs = [m for m in data.get("messages") or [] if isinstance(m, dict) and _TS.match(str(m.get("ts") or ""))]
                if window_end is not None:
                    msgs = [m for m in msgs if float(m["ts"]) < window_end]       # windows are [start, end)
                items += [self._raw_message(ctx, source, m) for m in msgs]
                stamps = [m["ts"] for m in msgs] + ([st["max_ts"]] if st.get("max_ts") else [])
                st["max_ts"] = max(stamps, key=float) if stamps else None
                st["threads"] += [m["ts"] for m in msgs if self._is_parent(m) and m["ts"] not in st["threads"]]
                nxt = str((data.get("response_metadata") or {}).get("next_cursor") or "")
                st["cursor"] = nxt or None
                st["history_done"] = not nxt
            if st["history_done"] and not st["threads"]:
                done_hw = hw
                if st.get("max_ts"):
                    seen = normalize_ts(st["max_ts"])
                    done_hw = max(hw, seen) if (hw and seen) else (seen or hw)
                yield Page(stream=stream, items=items, next_cursor=final(done_hw), has_more=False, rate=self._rate(ctx))
                return
            yield Page(stream=stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"pass": json.loads(json.dumps(st))}, hw), has_more=True,
                       rate=self._rate(ctx))

    async def incremental_sync(self, ctx: ConnectorContext, source: SourceDescriptor, cursor: Cursor | None) -> AsyncIterator[Page]:
        """New top-level messages since ``high watermark − overlap`` and the threads they start. The watermark advances to
        the newest message of a pass only when the pass (history and threads) is complete."""
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        lookback = int(ctx.config.get("initial_lookback_days") or ctx.limits.backfill_window_days)
        hw = (cursor.high_watermark if cursor is not None and cursor.high_watermark else None) or iso_utc(ctx.clock() - timedelta(days=max(1, lookback)))
        st = data.get("pass")
        if not isinstance(st, dict):
            oldest = _epoch(hw) - int(ctx.config.get("overlap_seconds", OVERLAP_SECONDS))
            st = {"oldest": f"{max(0.0, oldest):.6f}", "latest": None, "cursor": None, "history_done": False, "threads": [], "thread_cursor": None,
                  "max_ts": None, "started": False}
        async for page in self._run_pass(ctx, source, st, stream=f"incr:{source.external_id}", hw=hw, window_end=None,
                                         final=lambda h: Cursor(CURSOR_VERSION, {"pass": None}, h)):
            yield page

    async def initial_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, window: BackfillWindow,
                               cursor: Cursor | None) -> AsyncIterator[Page]:
        """Top-level messages with ``ts`` in ``[window.start, window.end)`` and all replies of the threads they start."""
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        if data.get("done"):
            return
        lo, hi = _epoch(window.start), _epoch(window.end)
        st = data.get("pass")
        if not isinstance(st, dict):
            st = {"oldest": f"{lo:.6f}", "latest": f"{hi:.6f}", "cursor": None, "history_done": False, "threads": [], "thread_cursor": None,
                  "max_ts": None, "started": False}
        async for page in self._run_pass(ctx, source, st, stream=window.stream, hw=None, window_end=hi,
                                         final=lambda _h: Cursor(CURSOR_VERSION, {"done": True}, normalize_ts(window.end))):
            yield page

    # ------------------------------------------------------------------ Events API (notify, then fetch)
    @classmethod
    def verify_webhook(cls, headers: Mapping[str, str], body: bytes, secret: bytes, *, now: float) -> bool:
        """Slack signing secret, version ``v0``: ``X-Slack-Signature = "v0=" + hex HMAC-SHA256(secret, "v0:{ts}:{raw body}")``
        with ``ts = X-Slack-Request-Timestamp``; constant-time; requests more than 5 minutes from ``now`` (either way) are
        rejected, which bounds replays (``event_id`` dedupe covers the rest)."""
        ts = _header(headers, "x-slack-request-timestamp")
        sig = _header(headers, "x-slack-signature")
        if not ts.isdigit() or not sig.startswith("v0=") or not secret:
            return False
        if abs(float(now) - int(ts)) > SIGNATURE_TOLERANCE_SECONDS:
            return False
        expected = "v0=" + hmac.new(secret, b"v0:" + ts.encode("ascii") + b":" + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.encode("ascii"), sig.encode("ascii", errors="replace"))

    @classmethod
    def challenge_response(cls, headers: Mapping[str, str], body: bytes) -> str | None:
        """The ``challenge`` of a ``url_verification`` request (to echo back), else ``None``. Call it only after
        :meth:`verify_webhook` succeeded: Slack signs these requests too, and an unsigned challenge must get the same 401."""
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("type") != "url_verification":
            return None
        challenge = data.get("challenge")
        return challenge if isinstance(challenge, str) and _CHALLENGE.match(challenge) else None

    @classmethod
    def parse_webhook(cls, headers: Mapping[str, str], body: bytes) -> list[WebhookNotice]:
        """Ids only (§3.7): channel, ts, thread_ts, user ids and the event type; the dedupe key (``delivery_id``) is the
        envelope's ``event_id``. Text, ``previous_message`` and every other content field are dropped here. Handles
        ``message`` (plain, ``bot_message``, ``me_message``, ``file_share``), ``message_changed``, ``message_deleted``,
        ``thread_broadcast``, ``member_left_channel``, ``tokens_revoked`` and ``app_uninstalled``; ``url_verification`` is
        answered by the caller through :meth:`challenge_response`; anything else yields no notice."""
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return []
        if not isinstance(data, dict) or data.get("type") != "event_callback":
            return []
        ev = data.get("event") if isinstance(data.get("event"), dict) else {}
        event_id = str(data.get("event_id") or "")
        team = str(data.get("team_id") or "")
        if not event_id or len(event_id) > 64 or not _ID.match(team):
            return []
        auths = data.get("authorizations") if isinstance(data.get("authorizations"), list) else []
        enterprise = data.get("enterprise_id") or next((a.get("enterprise_id") for a in auths if isinstance(a, dict) and a.get("enterprise_id")), None)
        account = f"{enterprise if enterprise and _ID.match(str(enterprise)) else ''}/{team}"
        occurred = normalize_ts(data.get("event_time"))
        etype, sub = str(ev.get("type") or ""), str(ev.get("subtype") or "")

        def notice(source_id: str, action: str, refs: tuple[dict[str, str], ...]) -> list[WebhookNotice]:
            return [WebhookNotice(connector_type=cls.manifest.connector_type, delivery_id=event_id, external_account_id=account,
                                  source_external_id=source_id, action=action, object_refs=refs, occurred_at=occurred)]

        if etype == "message":
            channel = str(ev.get("channel") or "")
            if not _ID.match(channel):
                return []
            if sub == "message_changed":
                inner = ev.get("message") if isinstance(ev.get("message"), dict) else {}
                ts, thread_ts, action = inner.get("ts"), inner.get("thread_ts"), "message_changed"
            elif sub == "message_deleted":
                prev = ev.get("previous_message") if isinstance(ev.get("previous_message"), dict) else {}
                ts, thread_ts, action = ev.get("deleted_ts"), prev.get("thread_ts"), "message_deleted"
            elif sub == "thread_broadcast":
                ts, thread_ts, action = ev.get("ts"), ev.get("thread_ts"), "thread_broadcast"
            elif sub in KEPT_SUBTYPES:
                ts, thread_ts, action = ev.get("ts"), ev.get("thread_ts"), "message"
            else:
                return []                                   # joins, topic changes ...: nothing to remember
            if not _TS.match(str(ts or "")):
                return []
            ref = {"type": "slack_message", "id": f"{channel}:{ts}", "channel": channel, "ts": str(ts)}
            if thread_ts and _TS.match(str(thread_ts)):
                ref["thread_ts"] = str(thread_ts)
            return notice(channel, action, (ref,))
        if etype == "member_left_channel":
            channel, user = str(ev.get("channel") or ""), str(ev.get("user") or "")
            if _ID.match(channel) and _ID.match(user):
                return notice(channel, "member_left_channel", ({"type": "user", "id": user},))
            return []
        if etype == "tokens_revoked":
            tokens = ev.get("tokens") if isinstance(ev.get("tokens"), dict) else {}
            users = [str(u) for k in ("oauth", "bot") for u in (tokens.get(k) or []) if _ID.match(str(u))]
            return notice("", "tokens_revoked", tuple({"type": "user", "id": u} for u in users))
        if etype == "app_uninstalled":
            return notice("", "app_uninstalled", ())
        return []

    async def _fetch_message(self, ctx: ConnectorContext, channel: str, ts: str, thread_ts: str | None) -> dict[str, Any] | None:
        """The current state of one message, or ``None`` when it no longer exists (the call itself proves the channel is
        readable; an unreadable channel raises ``SourceUnavailable`` instead)."""
        bounds = {"channel": channel, "oldest": ts, "latest": ts, "inclusive": "true"}
        if thread_ts and thread_ts != ts:
            try:
                data, _ = await self._call(ctx, "conversations.replies", {**bounds, "ts": thread_ts, "limit": 2})
            except ObjectGone:
                return None                                  # thread_not_found: the whole thread is gone
        else:
            data, _ = await self._call(ctx, "conversations.history", {**bounds, "limit": 1})
        for m in data.get("messages") or []:
            if isinstance(m, dict) and m.get("ts") == ts:
                return m
        return None

    def _connection_notice(self, ctx: ConnectorContext, notice: WebhookNotice) -> None:
        if notice.action == "app_uninstalled":
            raise AuthRevoked("the Slack app was uninstalled")
        if notice.action == "tokens_revoked" and ctx.auth_account_id in {str(r.get("id")) for r in notice.object_refs}:
            raise AuthRevoked("the Slack token was revoked")

    async def handle_webhook(self, ctx: ConnectorContext, notice: WebhookNotice) -> AsyncIterator[Page]:
        """Fetch the authoritative state: ``conversations.info`` (the container; unreadable → ``SourceUnavailable``, never a
        deletion), then each message by its ``ts``. A message that is absent (or a ``tombstone``) while the channel answered
        is confirmed deleted. ``member_left_channel`` for the connection's own user re-checks readability (access loss);
        for another user it changes nothing here — that member's access narrows when the source is re-discovered."""
        if not notice.source_external_id:
            self._connection_notice(ctx, notice)
            return
        channel = notice.source_external_id
        if not _ID.match(channel):
            raise PermanentError("invalid channel id", code="bad_notice")
        info, _ = await self._call(ctx, "conversations.info", {"channel": channel})
        source = await self._descriptor(ctx, info.get("channel") or {"id": channel}, with_members=False)
        if source is None:
            raise PermanentError("unexpected conversations.info response", code="bad_response")
        if notice.action == "member_left_channel":
            if ctx.auth_account_id in {str(r.get("id")) for r in notice.object_refs}:
                await self._call(ctx, "conversations.history", {"channel": channel, "limit": 1})
            return
        fetched = self._fetched(ctx)
        items: list[RawItem] = []
        for ref in notice.object_refs:
            ts = str(ref.get("ts") or "")
            if ref.get("type") != "slack_message" or not _TS.match(ts):
                continue
            msg = await self._fetch_message(ctx, channel, ts, ref.get("thread_ts"))
            if msg is None or msg.get("subtype") == "tombstone":
                items.append(RawItem("deletion", {"ts": ts, "reason": "deleted_at_source"}, source, fetched))
            else:
                items.append(RawItem("message", msg, source, fetched))
        yield Page(stream="webhook", items=items, next_cursor=None, has_more=False, rate=self._rate(ctx))

    # ------------------------------------------------------------------ normalize (pure)
    def normalize(self, raw: RawItem, ctx: ConnectorContext) -> list[CanonicalEvent]:
        p = raw.payload
        if not isinstance(p, dict):
            raise PermanentError("invalid Slack item", code="normalize_failed")
        src = raw.source
        channel = src.external_id
        perms = Permissions(src.visibility if src.visibility in VISIBILITIES else "private", tuple(src.member_ids), src.membership_ref)
        common: dict[str, Any] = dict(tenant_id=ctx.tenant_id, holder_id=ctx.holder_id, connector_id=ctx.connector_id, source_app=ctx.source_app,
                                      source_account_id=ctx.source_account_id, observed_at=raw.fetched_at, permissions=perms)
        container = {"container_name": src.name, "container_kind": src.source_type} if src.name else {"container_kind": src.source_type}
        if raw.object_type == "conversation":
            return [CanonicalEvent.create(kind="conversation", source_object_type="conversation", source_object_id=channel,
                                          created_at=src.metadata.get("created"), title=src.name or channel, hints=dict(container), **common)]
        ts = str(p.get("ts") or "")
        if not _TS.match(ts):
            raise PermanentError("Slack message without a valid ts", code="normalize_failed")
        oid = f"{channel}:{ts}"
        if raw.object_type == "deletion" or p.get("subtype") == "tombstone":
            return [CanonicalEvent.create(kind="deletion", source_object_type="slack_message", source_object_id=oid, updated_at=raw.fetched_at,
                                          tiebreak="deleted", hints={"reason": str(p.get("reason") or "deleted_at_source")}, **common)]
        if raw.object_type != "message":
            raise PermanentError("unknown Slack item type", code="normalize_failed", detail={"object_type": raw.object_type})
        sub = str(p.get("subtype") or "")
        if sub not in KEPT_SUBTYPES:
            return []
        raw_text = str(p.get("text") or "")
        body = mrkdwn_to_text(raw_text)
        if not body.strip():
            return []
        edited = p.get("edited") if isinstance(p.get("edited"), dict) else {}
        edited_ts = str(edited.get("ts") or "") if _TS.match(str(edited.get("ts") or "")) else ""
        version = edited_ts or ts
        thread_ts = str(p.get("thread_ts") or "") if _TS.match(str(p.get("thread_ts") or "")) else ""
        author = str(p.get("user") or p.get("bot_id") or "") or None
        files = tuple(AttachmentRef(attachment_id=str(f["id"]), filename=str(f.get("name") or ""),
                                    content_type=str(f.get("mimetype") or "application/octet-stream"),
                                    size_bytes=f.get("size") if isinstance(f.get("size"), int) else None)
                      for f in p.get("files") or [] if isinstance(f, dict) and f.get("id"))
        hints = {**container, "labels": [], "is_bot": bool(p.get("bot_id")) or sub == "bot_message", "subtype": sub or None, "ts": ts}
        if thread_ts:
            hints["thread_ts"] = thread_ts
        return [CanonicalEvent.create(kind="message", source_object_type="slack_message", source_object_id=oid, source_version=version,
                                      conversation_id=channel, thread_id=thread_ts or None,
                                      parent_message_id=f"{channel}:{thread_ts}" if thread_ts and thread_ts != ts else None,
                                      author_id=author, participant_ids=[author] if author else [], created_at=ts, updated_at=edited_ts or ts,
                                      title="", body=body, content_type="text/plain", attachment_references=files, hints=hints,
                                      links=_slack_links(raw_text), tiebreak=version, **common)]


def mrkdwn_to_text(text: str) -> str:
    """Slack ``mrkdwn`` to plain text: ``<url|label>`` → ``label (url)``, ``<@U1>`` → ``@U1``, ``<#C1|name>`` → ``#name``,
    ``<!here>`` → ``@here``, then Slack's three HTML escapes."""
    s = _LINK_LABEL.sub(lambda m: f"{m.group(2)} ({m.group(1)})" if m.group(2) else m.group(1), text or "")
    s = _LINK_BARE.sub(lambda m: m.group(1), s)
    s = _MENTION.sub(lambda m: f"@{m.group(1)}", s)
    s = _CHANNEL.sub(lambda m: f"#{m.group(2) or m.group(1)}", s)
    s = _SPECIAL.sub(lambda m: f"@{m.group(1)}", s)
    return html.unescape(s)


def _slack_links(text: str) -> list[str]:
    out = [m.group(1) for m in _LINK_LABEL.finditer(text or "")] + [m.group(1) for m in _LINK_BARE.finditer(text or "")]
    for u in extract_urls(_LINK_LABEL.sub(" ", _LINK_BARE.sub(" ", text or ""))):
        out.append(u)
    return list(dict.fromkeys(out))


def _header(headers: Mapping[str, str], name: str) -> str:
    for k, v in headers.items():
        if k.lower() == name:
            return str(v)
    return ""


def _epoch(iso: str) -> float:
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


__all__ = ["SlackConnector", "mrkdwn_to_text", "slack_error"]
