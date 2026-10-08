"""A faithful offline Slack Web API and Events API (INGESTION.md §11.5) for tests and demos.

Start it with :func:`start_slack_mock`::

    url, sl = await start_slack_mock(load_fixture("slack_acme"), clock=FakeClock(...))
    # connector config: {"api_base": url + "/api", "slack_app_class": "internal"}; OAuth: OAuthAppConfig(client_id,
    # Secret(secret), oauth_base=url, api_base=url + "/api", allow_loopback_http=True)
    headers, body = sl.signed_event(sl.edit_message("C0DEPLOY", ts, "new text"))   # an Events API delivery
    await sl.close()

Modelled behaviour (from Slack's docs as reflected in the official python-slack-sdk; re-verify against docs.slack.dev):

* ``/api/<method>`` for ``auth.test``, ``auth.revoke``, ``conversations.list|info|members|history|replies``, ``users.info``,
  ``oauth.v2.access``; GET query or POST form; ``Authorization: Bearer``.
* **Errors are HTTP 200 with** ``{"ok": false, "error": ...}``: ``not_authed``, ``invalid_auth``, ``token_revoked``,
  ``token_expired``, ``missing_scope`` (with ``needed``/``provided``), ``channel_not_found``, ``thread_not_found``,
  ``invalid_cursor``, ``user_not_found``. Rate limits are HTTP 429 with ``Retry-After`` and ``error: ratelimited``.
* A user token sees every public channel, the private channels and DMs its user is a member of.
* Cursor pagination (``response_metadata.next_cursor``, empty when done), ``limit`` caps, ``oldest``/``latest``/``inclusive``
  bounds; history newest first without thread replies (except ``thread_broadcast``); replies parent first, then oldest
  first; deleted parents with replies remain as ``tombstone`` messages; edited messages carry ``edited.ts``.
* App classes: with ``app.app_class = "distributed"`` the mock enforces Slack's 2025 limits for non-Marketplace
  distributed apps on ``conversations.history`` / ``.replies``: ``limit`` clamped to 15 and 1 request per minute per
  workspace and method (429 otherwise).
* Faults: :meth:`fail_next`, :meth:`rate_limit_next`, :meth:`revoke_token`, :meth:`expire_token`, :meth:`crash_after_pages`.
* Mutations return an Events API envelope (``event_callback`` with ``event_id``, ``authorizations``); :meth:`signed_event`
  signs it with the ``v0`` scheme (``tamper=True`` flips one byte after signing; ``timestamp=`` makes stale ones).
* OAuth v2: ``GET /oauth/v2/authorize`` (auto-approves as ``app.authorizing_user``; PKCE ``S256``) and
  ``POST /api/oauth.v2.access`` (code exchange; token rotation when ``app.token_rotation``).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import math
import secrets
import uuid
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from .common import MockProvider, load_fixture

READ_SCOPE = {"public": "channels:read", "private": "groups:read", "im": "im:read", "mpim": "mpim:read"}
HISTORY_SCOPE = {"public": "channels:history", "private": "groups:history", "im": "im:history", "mpim": "mpim:history"}
TYPE_OF = {"public_channel": "public", "private_channel": "private", "im": "im", "mpim": "mpim"}
PACED = ("conversations.history", "conversations.replies")


async def start_slack_mock(fixture: Mapping[str, Any] | None = None, *, clock: Callable[[], float] | None = None, latency: float = 0.0,
                           host: str = "127.0.0.1", port: int = 0) -> tuple[str, "SlackMock"]:
    """Start a mock on loopback; returns ``(base_url, controller)`` (the Web API is at ``base_url + "/api"``)."""
    mock = SlackMock(fixture if fixture is not None else load_fixture("slack_acme"), clock=clock, latency=latency)
    return await mock.start(host, port), mock


class SlackMock(MockProvider):
    name = "slack"

    def __init__(self, fixture: Mapping[str, Any], *, clock: Callable[[], float] | None = None, latency: float = 0.0) -> None:
        fx = copy.deepcopy(dict(fixture))
        self.team = dict(fx["team"])
        self.app_config = dict(fx.get("app") or {})
        self.users: dict[str, dict[str, Any]] = {u["id"]: dict(u) for u in fx.get("users") or []}
        self.tokens: dict[str, dict[str, Any]] = {t["token"]: dict(t) for t in fx.get("tokens") or []}
        self.channels: dict[str, dict[str, Any]] = {c["id"]: dict(c) for c in fx.get("channels") or []}
        self.messages: dict[str, dict[str, dict[str, Any]]] = {cid: {} for cid in self.channels}
        for m in fx.get("messages") or []:
            self.messages.setdefault(m["channel"], {})[m["ts"]] = {k: v for k, v in m.items() if k != "channel"}
        self._paced: dict[str, float] = {}
        self._rate_next: list[dict[str, Any]] = []
        self._codes: dict[str, dict[str, Any]] = {}
        self._seq = 0
        super().__init__(clock=clock, latency=latency)

    # ------------------------------------------------------------------ routes
    def _routes(self, app: web.Application) -> None:
        app.router.add_route("*", "/api/{method}", self.h_method)
        app.router.add_get("/oauth/v2/authorize", self.h_authorize)

    @property
    def app_class(self) -> str:
        return str(self.app_config.get("app_class") or "internal")

    # ------------------------------------------------------------------ faults
    def rate_limit_next(self, *, method: str | None = None, retry_after: int = 1, count: int = 1) -> None:
        """The next ``count`` calls (to ``method``, or any) answer HTTP 429 with ``Retry-After``."""
        self._rate_next.extend({"method": method, "retry_after": int(retry_after)} for _ in range(int(count)))

    def revoke_token(self, token: str) -> dict[str, Any]:
        """Revoke a token; returns the ``tokens_revoked`` event envelope Slack would send."""
        self.tokens[token]["revoked"] = True
        return self.envelope({"type": "tokens_revoked", "tokens": {"oauth": [self.tokens[token]["user"]], "bot": []},
                              "event_ts": self._event_ts()})

    def expire_token(self, token: str) -> None:
        self.tokens[token]["expired"] = True

    def uninstall(self) -> dict[str, Any]:
        for t in self.tokens.values():
            t["revoked"] = True
        return self.envelope({"type": "app_uninstalled", "event_ts": self._event_ts()})

    # ------------------------------------------------------------------ model
    def _kind(self, ch: Mapping[str, Any]) -> str:
        if ch.get("is_im"):
            return "im"
        if ch.get("is_mpim"):
            return "mpim"
        return "private" if ch.get("is_private") else "public"

    def _readable(self, user: str, ch: Mapping[str, Any]) -> bool:
        return self._kind(ch) == "public" or user in (ch.get("members") or [])

    def channel_json(self, ch: Mapping[str, Any], user: str) -> dict[str, Any]:
        kind = self._kind(ch)
        created = int(ch.get("created") or 1700000000)
        if kind == "im":
            other = ch.get("user") or next((m for m in ch.get("members") or [] if m != user), "")
            return {"id": ch["id"], "created": created, "is_archived": False, "is_im": True, "is_org_shared": False, "context_team_id": self.team["id"],
                    "updated": created * 1000, "user": other, "is_user_deleted": False, "priority": 0}
        members = list(ch.get("members") or [])
        return {"id": ch["id"], "name": ch["name"], "is_channel": kind == "public", "is_group": kind == "private", "is_im": False,
                "is_mpim": kind == "mpim", "is_private": kind != "public", "created": created, "is_archived": bool(ch.get("is_archived")),
                "is_general": bool(ch.get("is_general")), "unlinked": 0, "name_normalized": ch["name"], "is_shared": False, "is_org_shared": False,
                "is_pending_ext_shared": False, "pending_shared": [], "context_team_id": self.team["id"], "updated": created * 1000,
                "parent_conversation": None, "creator": members[0] if members else "", "is_ext_shared": False, "shared_team_ids": [self.team["id"]],
                "pending_connected_team_ids": [], "is_member": user in members,
                "topic": {"value": ch.get("topic", ""), "creator": "", "last_set": 0}, "purpose": {"value": ch.get("purpose", ""), "creator": "", "last_set": 0},
                "previous_names": [], "num_members": len(members)}

    def _replies_of(self, cid: str, parent_ts: str) -> list[dict[str, Any]]:
        return sorted((m for ts, m in self.messages.get(cid, {}).items() if m.get("thread_ts") == parent_ts and ts != parent_ts and not m.get("deleted")),
                      key=lambda m: float(m["ts"]))

    def message_json(self, cid: str, m: Mapping[str, Any]) -> dict[str, Any] | None:
        replies = self._replies_of(cid, m["ts"]) if (m.get("thread_ts") in (None, m["ts"])) else []
        if m.get("deleted"):
            if not replies:
                return None
            # a deleted parent whose thread still has replies stays as a tombstone
            return {"type": "message", "subtype": "tombstone", "text": "This message was deleted.", "user": "USLACKBOT", "hidden": True,
                    "ts": m["ts"], "thread_ts": m["ts"], "reply_count": len(replies), "reply_users_count": len({r.get("user") for r in replies}),
                    "latest_reply": replies[-1]["ts"], "reply_users": sorted({r.get("user") for r in replies if r.get("user")}), "is_locked": False}
        d: dict[str, Any] = {"type": "message", "text": m.get("text", ""), "ts": m["ts"], "team": self.team["id"]}
        if m.get("bot_id"):
            d.update({"subtype": "bot_message", "bot_id": m["bot_id"], "username": m.get("username") or "bot"})
        else:
            d["user"] = m["user"]
            d["client_msg_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{cid}:{m['ts']}"))
        if m.get("subtype") and not m.get("bot_id"):
            d["subtype"] = m["subtype"]
        if m.get("edited"):
            d["edited"] = dict(m["edited"])
        if m.get("files"):
            d["files"] = [dict(f) for f in m["files"]]
        if m.get("thread_ts"):
            d["thread_ts"] = m["thread_ts"]
            if m["thread_ts"] != m["ts"]:
                d["parent_user_id"] = self.messages[cid].get(m["thread_ts"], {}).get("user", "")
        if replies:
            d.update({"thread_ts": m["ts"], "reply_count": len(replies), "reply_users_count": len({r.get("user") for r in replies}),
                      "latest_reply": replies[-1]["ts"], "reply_users": sorted({r.get("user") for r in replies if r.get("user")}),
                      "is_locked": False, "subscribed": False})
        return d

    # ------------------------------------------------------------------ dispatch
    def _ok(self, data: Mapping[str, Any], *, scopes: list[str] | None = None, accepted: str = "") -> web.Response:
        headers = {"x-slack-req-id": uuid.uuid4().hex, "x-accepted-oauth-scopes": accepted}
        if scopes is not None:
            headers["x-oauth-scopes"] = ",".join(scopes)
        return web.json_response({"ok": True, **data}, headers=headers)

    def _err(self, error: str, *, scopes: list[str] | None = None, **extra: Any) -> web.Response:
        headers = {"x-slack-req-id": uuid.uuid4().hex}
        if scopes is not None:
            headers["x-oauth-scopes"] = ",".join(scopes)
        return web.json_response({"ok": False, "error": error, **extra}, headers=headers)      # HTTP 200, as Slack does

    async def h_method(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        params: dict[str, str] = dict(request.query)
        if request.method == "POST":
            if request.content_type == "application/json":
                body = await request.json()
                params.update({k: str(v) for k, v in (body or {}).items()})
            else:
                params.update({k: str(v) for k, v in (await request.post()).items()})
        if method == "oauth.v2.access":
            return self.oauth_access(params)
        for i, r in enumerate(self._rate_next):
            if r["method"] in (None, method):
                self._rate_next.pop(i)
                return web.json_response({"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": str(r["retry_after"])})
        token = self._bearer(request) or params.get("token")
        if not token:
            return self._err("not_authed")
        info = self.tokens.get(token)
        if info is None:
            return self._err("invalid_auth")
        if info.get("revoked"):
            return self._err("token_revoked")
        if info.get("expired"):
            return self._err("token_expired")
        scopes = list(info.get("scopes") or [])
        if method in PACED and self.app_class == "distributed":
            key = f"{self.team['id']}|{method}"
            now = self.clock()
            last = self._paced.get(key)
            if last is not None and now - last < 60:
                return web.json_response({"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": str(math.ceil(60 - (now - last)))})
            self._paced[key] = now
        handler = {"auth.test": self.auth_test, "auth.revoke": self.auth_revoke, "conversations.list": self.conv_list,
                   "conversations.info": self.conv_info, "conversations.members": self.conv_members, "conversations.history": self.conv_history,
                   "conversations.replies": self.conv_replies, "users.info": self.users_info}.get(method)
        if handler is None:
            return self._err("unknown_method", scopes=scopes)
        return handler(token, info, scopes, params)

    def _limit(self, params: Mapping[str, str], *, default: int, maximum: int, method: str = "") -> int:
        try:
            n = int(params.get("limit") or default)
        except ValueError:
            n = default
        if method in PACED and self.app_class == "distributed":
            maximum = 15
        return max(1, min(maximum, n))

    @staticmethod
    def _decode_cursor(c: str | None) -> int | None:
        if not c:
            return 0
        try:
            raw = base64.b64decode(c.encode()).decode()
        except Exception:
            return None
        return int(raw.split(":", 1)[1]) if raw.startswith("next:") and raw.split(":", 1)[1].isdigit() else None

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.b64encode(f"next:{offset}".encode()).decode()

    def _page(self, items: list[Any], params: Mapping[str, str], limit: int) -> tuple[list[Any], str] | None:
        off = self._decode_cursor(params.get("cursor"))
        if off is None:
            return None
        chunk = items[off: off + limit]
        return chunk, (self._encode_cursor(off + limit) if off + limit < len(items) else "")

    def _channel_for(self, user: str, scopes: list[str], params: Mapping[str, str], scope_map: Mapping[str, str]) -> tuple[dict[str, Any] | None, web.Response | None]:
        ch = self.channels.get(params.get("channel", ""))
        if ch is None or not self._readable(user, ch):
            return None, self._err("channel_not_found", scopes=scopes)
        need = scope_map[self._kind(ch)]
        if need not in scopes:
            return None, self._err("missing_scope", scopes=scopes, needed=need, provided=",".join(scopes))
        return ch, None

    # ------------------------------------------------------------------ methods
    def auth_test(self, token: str, info: Mapping[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        u = self.users[info["user"]]
        data = {"url": f"https://{self.team['domain']}.slack.com/", "team": self.team["name"], "user": u["name"], "team_id": self.team["id"],
                "user_id": u["id"], "is_enterprise_install": False}
        if self.team.get("enterprise_id"):
            data["enterprise_id"] = self.team["enterprise_id"]
        if info.get("bot_id"):
            data["bot_id"] = info["bot_id"]
        return self._ok(data, scopes=scopes)

    def auth_revoke(self, token: str, info: dict[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        info["revoked"] = True
        return self._ok({"revoked": True}, scopes=scopes)

    def conv_list(self, token: str, info: Mapping[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        types = [t.strip() for t in (params.get("types") or "public_channel").split(",") if t.strip()]
        kinds = []
        for t in types:
            if t not in TYPE_OF:
                return self._err("invalid_types", scopes=scopes)
            need = READ_SCOPE[TYPE_OF[t]]
            if need not in scopes:
                return self._err("missing_scope", scopes=scopes, needed=need, provided=",".join(scopes))
            kinds.append(TYPE_OF[t])
        user = info["user"]
        chans = [c for c in self.channels.values() if self._kind(c) in kinds and self._readable(user, c)
                 and not (params.get("exclude_archived") == "true" and c.get("is_archived"))]
        chans.sort(key=lambda c: c["id"])
        page = self._page(chans, params, self._limit(params, default=100, maximum=1000))
        if page is None:
            return self._err("invalid_cursor", scopes=scopes)
        chunk, nxt = page
        return self._ok({"channels": [self.channel_json(c, user) for c in chunk], "response_metadata": {"next_cursor": nxt}}, scopes=scopes,
                        accepted=",".join(READ_SCOPE[k] for k in kinds))

    def conv_info(self, token: str, info: Mapping[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        ch, err = self._channel_for(info["user"], scopes, params, READ_SCOPE)
        if err:
            return err
        return self._ok({"channel": self.channel_json(ch, info["user"])}, scopes=scopes)

    def conv_members(self, token: str, info: Mapping[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        ch, err = self._channel_for(info["user"], scopes, params, READ_SCOPE)
        if err:
            return err
        page = self._page(sorted(ch.get("members") or []), params, self._limit(params, default=100, maximum=1000))
        if page is None:
            return self._err("invalid_cursor", scopes=scopes)
        chunk, nxt = page
        return self._ok({"members": chunk, "response_metadata": {"next_cursor": nxt}}, scopes=scopes)

    @staticmethod
    def _in_bounds(ts: str, params: Mapping[str, str]) -> bool:
        inclusive = params.get("inclusive") in ("true", "1")
        t = float(ts)
        if params.get("oldest"):
            o = float(params["oldest"])
            if t < o or (t == o and not inclusive):
                return False
        if params.get("latest"):
            lt = float(params["latest"])
            if t > lt or (t == lt and not inclusive):
                return False
        return True

    def conv_history(self, token: str, info: Mapping[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        ch, err = self._channel_for(info["user"], scopes, params, HISTORY_SCOPE)
        if err:
            return err
        cid = ch["id"]
        top = []
        for ts, m in self.messages.get(cid, {}).items():
            is_reply = m.get("thread_ts") not in (None, ts)
            if is_reply and m.get("subtype") != "thread_broadcast":
                continue                                    # replies live in conversations.replies
            if not self._in_bounds(ts, params):
                continue
            j = self.message_json(cid, m)
            if j is not None:
                top.append(j)
        top.sort(key=lambda m: float(m["ts"]), reverse=True)
        page = self._page(top, params, self._limit(params, default=100, maximum=999, method="conversations.history"))
        if page is None:
            return self._err("invalid_cursor", scopes=scopes)
        chunk, nxt = page
        data: dict[str, Any] = {"messages": chunk, "has_more": bool(nxt), "pin_count": 0, "channel_actions_ts": None, "channel_actions_count": 0}
        if nxt:
            data["response_metadata"] = {"next_cursor": nxt}
        return self._ok(data, scopes=scopes, accepted=HISTORY_SCOPE[self._kind(ch)])

    def conv_replies(self, token: str, info: Mapping[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        ch, err = self._channel_for(info["user"], scopes, params, HISTORY_SCOPE)
        if err:
            return err
        cid = ch["id"]
        parent = self.messages.get(cid, {}).get(params.get("ts", ""))
        parent_json = self.message_json(cid, parent) if parent is not None else None
        if parent_json is None:
            return self._err("thread_not_found", scopes=scopes)
        replies = [r for r in self._replies_of(cid, parent["ts"]) if self._in_bounds(r["ts"], params)]
        page = self._page(replies, params, self._limit(params, default=100, maximum=1000, method="conversations.replies"))
        if page is None:
            return self._err("invalid_cursor", scopes=scopes)
        chunk, nxt = page
        msgs = [parent_json] + [self.message_json(cid, r) for r in chunk]
        data: dict[str, Any] = {"messages": [m for m in msgs if m is not None], "has_more": bool(nxt)}
        if nxt:
            data["response_metadata"] = {"next_cursor": nxt}
        return self._ok(data, scopes=scopes, accepted=HISTORY_SCOPE[self._kind(ch)])

    def users_info(self, token: str, info: Mapping[str, Any], scopes: list[str], params: Mapping[str, str]) -> web.Response:
        if "users:read" not in scopes:
            return self._err("missing_scope", scopes=scopes, needed="users:read", provided=",".join(scopes))
        u = self.users.get(params.get("user", ""))
        if u is None:
            return self._err("user_not_found", scopes=scopes)
        profile = {"real_name": u.get("real_name", ""), "display_name": u.get("name", "")}
        if "users:read.email" in scopes and u.get("email"):
            profile["email"] = u["email"]
        return self._ok({"user": {"id": u["id"], "team_id": self.team["id"], "name": u["name"], "deleted": False, "real_name": u.get("real_name", ""),
                                  "is_bot": bool(u.get("is_bot")), "is_admin": False, "updated": 1700000000, "profile": profile}}, scopes=scopes)

    # ------------------------------------------------------------------ OAuth v2
    async def h_authorize(self, request: web.Request) -> web.StreamResponse:
        q = request.query
        redirect = q.get("redirect_uri") or ""
        if q.get("client_id") != self.app_config.get("client_id"):
            return web.Response(status=400, text="invalid_client_id")
        if q.get("code_challenge") and q.get("code_challenge_method", "plain") != "S256":
            return _redirect(redirect, {"error": "invalid_code_challenge_method", "state": q.get("state", "")})
        code = secrets.token_hex(10)
        self._codes[code] = {"user": self.app_config.get("authorizing_user") or next(iter(self.users)),
                             "user_scopes": [s for s in q.get("user_scope", "").split(",") if s], "redirect_uri": redirect,
                             "challenge": q.get("code_challenge"), "expires": self.clock() + 600}
        return _redirect(redirect, {"code": code, "state": q.get("state", "")})

    def oauth_access(self, params: Mapping[str, str]) -> web.Response:
        if params.get("client_id") != self.app_config.get("client_id"):
            return self._err("invalid_client_id")
        if params.get("client_secret") != self.app_config.get("client_secret"):
            return self._err("bad_client_secret")
        rotation = bool(self.app_config.get("token_rotation"))
        if params.get("grant_type") == "refresh_token":
            old = next((t for t, i in self.tokens.items() if i.get("refresh_token") and i["refresh_token"] == params.get("refresh_token")), None)
            if old is None:
                return self._err("invalid_refresh_token")
            info = self.tokens[old]
            info["revoked"] = True
            new = self._issue(info["user"], info.get("scopes") or [], rotation=True)
            return self._ok({"app_id": self.app_config.get("app_id"), "access_token": new["token"], "token_type": "user", "scope": ",".join(new["scopes"]),
                             "refresh_token": new["refresh_token"], "expires_in": 43200, "user_id": new["user"],
                             "team": {"id": self.team["id"], "name": self.team["name"]}, "enterprise": None, "is_enterprise_install": False})
        grant = self._codes.pop(params.get("code", ""), None)
        if grant is None or grant["expires"] < self.clock():
            return self._err("invalid_code")
        if params.get("redirect_uri") and params["redirect_uri"] != grant["redirect_uri"]:
            return self._err("bad_redirect_uri")
        if grant["challenge"]:
            verifier = params.get("code_verifier", "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or not hmac.compare_digest(digest, grant["challenge"]):
                return self._err("invalid_code_verifier")
        new = self._issue(grant["user"], grant["user_scopes"], rotation=rotation)
        authed = {"id": new["user"], "scope": ",".join(new["scopes"]), "access_token": new["token"], "token_type": "user"}
        if rotation:
            authed.update({"refresh_token": new["refresh_token"], "expires_in": 43200})
        return self._ok({"app_id": self.app_config.get("app_id"), "authed_user": authed, "team": {"id": self.team["id"], "name": self.team["name"]},
                         "enterprise": None, "is_enterprise_install": False})

    def _issue(self, user: str, scopes: list[str], *, rotation: bool) -> dict[str, Any]:
        token = ("xoxe.xoxp-mock-" if rotation else "xoxp-mock-") + secrets.token_hex(12)
        info = {"token": token, "user": user, "scopes": list(scopes)}
        if rotation:
            info["refresh_token"] = "xoxe-1-mock-" + secrets.token_hex(16)
        self.tokens[token] = info
        return info

    # ------------------------------------------------------------------ mutations (each returns the Events API envelope)
    def _event_ts(self) -> str:
        self._seq += 1
        return f"{int(self.clock())}.{self._seq:06d}"

    def envelope(self, event: Mapping[str, Any], *, event_id: str | None = None, authed_user: str | None = None) -> dict[str, Any]:
        """An ``event_callback`` delivery as Slack posts it to the Request URL."""
        user = authed_user or self.app_config.get("authorizing_user") or next(iter(self.users))
        return {"token": "mock-verification-token", "team_id": self.team["id"], "enterprise_id": self.team.get("enterprise_id"),
                "api_app_id": self.app_config.get("app_id", "A0MOCK"), "event": dict(event), "type": "event_callback",
                "event_id": event_id or ("Ev" + secrets.token_hex(5).upper()), "event_time": int(self.clock()),
                "authorizations": [{"enterprise_id": self.team.get("enterprise_id"), "team_id": self.team["id"], "user_id": user, "is_bot": False,
                                    "is_enterprise_install": False}],
                "is_ext_shared_channel": False, "event_context": "4-mock-" + secrets.token_hex(6)}

    def _channel_type(self, cid: str) -> str:
        return {"public": "channel", "private": "group", "im": "im", "mpim": "mpim"}[self._kind(self.channels[cid])]

    def post_message(self, channel: str, user: str, text: str, *, thread_ts: str | None = None, broadcast: bool = False,
                     ts: str | None = None) -> dict[str, Any]:
        ts = ts or self._event_ts()
        m: dict[str, Any] = {"ts": ts, "user": user, "text": text}
        if thread_ts:
            m["thread_ts"] = thread_ts
            if broadcast:
                m["subtype"] = "thread_broadcast"
        self.messages.setdefault(channel, {})[ts] = m
        ev = {**(self.message_json(channel, m) or {}), "channel": channel, "event_ts": ts, "channel_type": self._channel_type(channel)}
        if broadcast:
            ev["root"] = self.message_json(channel, self.messages[channel][thread_ts]) or {}
        return self.envelope(ev)

    def edit_message(self, channel: str, ts: str, text: str) -> dict[str, Any]:
        m = self.messages[channel][ts]
        previous = self.message_json(channel, m) or {}
        edit_ts = self._event_ts()
        m["text"] = text
        m["edited"] = {"user": m.get("user", ""), "ts": edit_ts}
        return self.envelope({"type": "message", "subtype": "message_changed", "hidden": True, "channel": channel, "ts": edit_ts, "event_ts": edit_ts,
                              "message": self.message_json(channel, m), "previous_message": previous, "channel_type": self._channel_type(channel)})

    def delete_message(self, channel: str, ts: str) -> dict[str, Any]:
        m = self.messages[channel][ts]
        previous = self.message_json(channel, m) or {}
        m["deleted"] = True
        event_ts = self._event_ts()
        return self.envelope({"type": "message", "subtype": "message_deleted", "hidden": True, "channel": channel, "ts": event_ts, "deleted_ts": ts,
                              "event_ts": event_ts, "previous_message": previous, "channel_type": self._channel_type(channel)})

    def leave_channel(self, channel: str, user: str) -> dict[str, Any]:
        ch = self.channels[channel]
        ch["members"] = [u for u in ch.get("members") or [] if u != user]
        return self.envelope({"type": "member_left_channel", "user": user, "channel": channel,
                              "channel_type": "G" if self._kind(ch) == "private" else "C", "team": self.team["id"], "event_ts": self._event_ts()})

    def url_verification(self, challenge: str) -> dict[str, Any]:
        return {"token": "mock-verification-token", "challenge": challenge, "type": "url_verification"}

    def signed_event(self, envelope: Mapping[str, Any], *, secret: bytes | str | None = None, timestamp: int | None = None, tamper: bool = False,
                     retry_num: int | None = None) -> tuple[dict[str, str], bytes]:
        """``(headers, raw body)`` as Slack posts an event: ``X-Slack-Request-Timestamp`` and the ``v0`` signature over
        ``v0:{timestamp}:{body}`` with the signing secret (default: the fixture's dummy ``app.signing_secret``)."""
        key = secret if isinstance(secret, bytes) else str(secret or self.app_config.get("signing_secret") or "").encode()
        body = json.dumps(envelope).encode("utf-8")
        ts = str(int(timestamp if timestamp is not None else self.clock()))
        sig = "v0=" + hmac.new(key, b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "User-Agent": "Slackbot 1.0 (+https://api.slack.com/robots)",
                   "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}
        if retry_num is not None:
            headers.update({"X-Slack-Retry-Num": str(retry_num), "X-Slack-Retry-Reason": "http_timeout"})
        if tamper:
            b = bytearray(body)
            b[len(b) // 2] ^= 0x01
            body = bytes(b)
        return headers, body

    async def send_event(self, url: str, envelope: Mapping[str, Any], **kw: Any) -> int:
        headers, body = self.signed_event(envelope, **kw)
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data=body, headers=headers) as resp:
                return resp.status


def _redirect(uri: str, params: Mapping[str, str]) -> web.Response:
    parts = urlsplit(uri)
    q = parse_qsl(parts.query) + [(k, v) for k, v in params.items() if v]
    return web.Response(status=302, headers={"Location": urlunsplit(parts._replace(query=urlencode(q)))})


__all__ = ["SlackMock", "start_slack_mock"]
