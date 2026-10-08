"""Gmail connector (personal mailboxes only): the labels the owner includes, read through the Gmail API v1 with the single
scope ``https://www.googleapis.com/auth/gmail.readonly``, kept current by the mailbox history and Cloud Pub/Sub push.

Status ``tested-offline``: exercised end to end against the mock in :mod:`mycelic.ingest.mocks.gmail_mock`, which models the
documented behaviour of the endpoints below (Google's error JSON, ``pageToken`` paging, history expiry as 404, 429 and 403
``rateLimitExceeded`` / ``userRateLimitExceeded``, Pub/Sub push bodies). It has not been run against Google by this code
base; re-verify the facts marked in the mock before relying on them.

Endpoints (``https://gmail.googleapis.com/gmail/v1/users/me/...``):

* connect: ``profile`` (address and current ``historyId``; no content);
* discover: ``labels``. Every user label is a source (auto-include rules apply). ``INBOX``, ``SENT`` and the ``CATEGORY_*``
  tabs are offered for review and never auto-included; ``SPAM``, ``TRASH`` and ``DRAFT`` are excluded by default (like
  direct messages). ``UNREAD``, ``STARRED``, ``IMPORTANT`` and ``CHAT`` are states, not containers, and are not offered:
  a label that comes and goes with reading would delete records;
* backfill: ``messages?labelIds=<label>&q=after:<s> before:<e>&maxResults&pageToken`` (newest first), then
  ``messages/{id}?format=full``. Windows are on ``internalDate`` (the query uses a one-second margin on both sides and the
  connector keeps exactly ``[start, end)``). **Why ``format=full``:** Gmail parses the MIME tree, text parts arrive base64url
  and attachments arrive as references (``attachmentId``, size), never bytes, so a fetch is bounded by the text and
  attachment contents never leave Google (metadata only). ``format=raw`` would return the whole RFC 5322 message with every
  attachment (25 MB and more) only to be parsed and mostly discarded here; ``format=metadata`` has no body;
* incremental, per label: ``history?startHistoryId&labelId&historyTypes=messageAdded&historyTypes=messageDeleted&
  historyTypes=labelAdded&historyTypes=labelRemoved``, then ``messages/{id}`` for what was added or relabelled. A **404**
  means the start id is older than the history Gmail keeps: :class:`CursorInvalid`, the pipeline resets the stream, and
  the next pass (no cursor) reads the current ``historyId`` from the profile *first*, then lists the label's recent window
  again (``initial_lookback_days``, default one backfill window); event-key dedupe absorbs what was already stored. The same
  happens on the very first pass. History pages are resumable (the start id and page token live in the cursor).

Deletions and leaving the source: ``messagesDeleted`` in the history, or a 404 on ``messages/{id}`` while ``profile`` still
answers, is a deletion at the source. A message that lost the label, or was moved to spam or trash, *left the source*:
a deletion with reason ``excluded_source`` that applies only if the holder has the record and the message is in no other
included label of this connection (``hints.if_known``/``still_in``; spam and trash count as no label). Deletions are sticky:
a message restored from the trash, or labelled again, does not come back (re-include it by re-adding the source).

Identity and order. ``source_account_id`` is the mailbox address (Gmail ids are unique per mailbox). A thread is a
``conversation`` with id ``<threadId>``; a message is a ``gmail_message`` ``<id>``. Content is immutable, labels are not:
``source_version`` is the message's ``historyId`` and the order key is ``internalDate | historyId`` (20 digits), so a label
change is a metadata update and a stale fetch is historical. The RFC 5322 ``Message-ID`` (and ``In-Reply-To``) go in hints.

Normalization (pure): the ``text/plain`` parts (charset honoured), else the HTML converted with
:func:`mycelic.ingest.normalize.html_to_text` after Gmail's ``<blockquote>`` quotes are turned into ``>`` lines; Gmail's
attribution line ("On ... wrote:") is joined to its quote. Quoted replies are then stripped by §4.5 (a reply's root is its
own text) and a forward with fewer than 24 characters of its own is a pure copy that shares the forwarded original's root.
Attachments are metadata only, identified by ``<message id>:<partId>`` because Gmail's ``attachmentId`` changes between
fetches. Author and participants are the From/To/Cc addresses (lower-case), so person entities are ``person:<address>``;
the organization domains of external participants go in ``hints.org_domains``.

Permissions: ``private``, members = the mailbox owner plus the participants, mapped through ``config.principal_map``
(``{"ben@acme.example": "usr_ben"}``; unmapped addresses stay ``gmail:<address>`` and never match a user). Label sources are
``private`` and not exportable: only the owner reads the mailbox until the owner opts a label in.

Push (Cloud Pub/Sub). A push subscription POSTs ``{"message": {"data": base64({"emailAddress", "historyId"}), "messageId",
"publishTime"}, "subscription"}``. **Verification** here is a shared secret in the push endpoint URL (``...?token=<secret>``),
compared in constant time. :meth:`verify_webhook` sees headers and body only, so the webhook endpoint must pass the URL's
``token`` in as the ``X-Mycelic-Push-Token`` header (:meth:`with_query_token` does exactly that). Google's production option
for push authentication, an OIDC JWT in ``Authorization`` signed by Google and checked against Google's published keys
and the subscription's audience, is **not implemented**. The notice carries ids only (the address and the history id,
``delivery_id = pubsub:<messageId>``); the holder answers it with an incremental history fetch of the included labels.
:meth:`start_push` / ``disconnect`` call ``users.watch`` / ``users.stop``; a watch lasts at most seven days and nothing in
this code base renews it yet.

Configuration (non-secret)::

    {"api_base": "https://gmail.googleapis.com", "principal_map": {"ana@acme.example": "usr_ana"}, "internal_domains": ["acme.example"],
     "initial_lookback_days": 30, "pubsub_topic": "projects/<p>/topics/<t>", "limits": {"page_size": 100}}
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Any, AsyncIterator, Iterable, Mapping
from urllib.parse import urlencode

from ..contract import (AuthExpired, AuthRevoked, BackfillWindow, Capabilities, ConnectorContext, ConnectorError, ConnectorManifest, ConnectResult,
                        Connector, Cursor, CursorInvalid, HealthReport, HttpResult, ObjectGone, Page, PermanentError, RateLimited, RateLimitSpec,
                        RawItem, ScopeSpec, SourceDescriptor, TransientError, WebhookNotice)
from ..events import AttachmentRef, CanonicalEvent, Permissions, normalize_ts
from ..normalize import html_to_text
from ._google import GOOGLE_AUTH_HOSTS, GoogleOAuthMixin, email_principals, header, valid_email
from ._provider import extract_urls, iso_utc, refresh_once

DEFAULT_API = "https://gmail.googleapis.com"
READONLY = "https://www.googleapis.com/auth/gmail.readonly"
WRITE_SCOPES = frozenset({"https://mail.google.com/", "https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.compose",
                          "https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.insert",
                          "https://www.googleapis.com/auth/gmail.labels", "https://www.googleapis.com/auth/gmail.settings.basic"})
SATISFYING = frozenset({READONLY, "https://mail.google.com/", "https://www.googleapis.com/auth/gmail.modify"})
CURSOR_VERSION = 1
HISTORY_TYPES = ("messageAdded", "messageDeleted", "labelAdded", "labelRemoved")
EXCLUDED_LABELS = frozenset({"SPAM", "TRASH", "DRAFT"})
REVIEW_LABELS = frozenset({"INBOX", "SENT"})
STATE_LABELS = frozenset({"UNREAD", "STARRED", "IMPORTANT", "CHAT"})
OUT_OF_MAILBOX = frozenset({"SPAM", "TRASH"})
PUSH_TOKEN_HEADER = "x-mycelic-push-token"
_LABEL = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_MSG_ID = re.compile(r"^[0-9A-Za-z_\-]{6,64}$")
_DIGITS = re.compile(r"^\d{1,20}$")
_PUBSUB_ID = re.compile(r"^[0-9A-Za-z_\-.]{1,100}$")
_SUBJECT_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|sv|wg)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)
_CHARSET = re.compile(r"charset\s*=\s*\"?([A-Za-z0-9_\-:.]+)", re.IGNORECASE)
_BLOCKQUOTE = re.compile(r"<(/?)blockquote\b[^>]*>", re.IGNORECASE)
_ATTRIBUTION_WRAPPED = re.compile(r"(?m)^(On\b[^\n]{0,200})\n(wrote:)[ \t]*$")
_ATTRIBUTION_GAP = re.compile(r"(?m)(wrote:)[ \t]*\n(?:[ \t]*\n)+(?=[ \t]*>)")


class GmailConnector(GoogleOAuthMixin, Connector):
    oauth_scopes = (READONLY,)
    satisfying_scopes = SATISFYING
    write_scopes = WRITE_SCOPES
    manifest = ConnectorManifest(
        connector_type="gmail",
        display_name="Gmail (labels you choose)",
        version="1.0.0",
        status="tested-offline",
        auth_kinds=("oauth2",),
        scopes=(ScopeSpec(READONLY, True, "Read the messages and labels of the labels you choose to include; nothing is ever sent, changed "
                                          "or deleted in your mailbox."),),
        modes=frozenset({"pull", "webhook"}),
        source_types=("label",),
        capabilities=Capabilities(edits=False, deletes="webhook", threads=True, attachments=False, acl="full", exports=False),
        rate_limit=RateLimitSpec(kind="tiered", default_rps=5.0, burst=5, serial_per_token=True),
        allowed_hosts=("gmail.googleapis.com",) + GOOGLE_AUTH_HOSTS,
        default_poll_seconds=300,
        terms_notes=("Personal mailboxes only. Reads the Gmail labels you include, with the read-only scope; inbox and sent mail are offered "
                     "for review but never included by a rule, and spam, trash and drafts are excluded. Messages are private to you and "
                     "their participants and are not shared until you opt a label in. Attachments are recorded as names and sizes only; "
                     "their contents are never downloaded. Google's API Services User Data Policy (Limited Use) applies to data from "
                     "Gmail: confirm your use of Mycelic fits it; restricted scopes need Google's verification for apps used beyond "
                     "testing. Gmail's per-user quota is honoured with Google's backoff. Push uses Cloud Pub/Sub with a shared secret in "
                     "the push URL; Google's OIDC push authentication is not implemented. Status: tested offline against a mock of the "
                     "documented API; not live-verified."),
        ownership=("personal",),
    )

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def _base(ctx: ConnectorContext) -> str:
        return str(ctx.config.get("api_base") or DEFAULT_API).rstrip("/") + "/gmail/v1/users/me"

    async def _get(self, ctx: ConnectorContext, path: str, *, params: Mapping[str, Any] | None = None, query: Iterable[tuple[str, str]] | None = None,
                   expected: tuple[int, ...] = (200,)) -> HttpResult:
        url = self._base(ctx) + path
        if query is not None:                     # repeated parameters (historyTypes) need an explicit query string
            url += "?" + urlencode(list(query))

        async def go() -> HttpResult:
            return await ctx.http.request("GET", url, params=params, expected=expected)
        try:
            return await go()
        except AuthExpired:
            if await refresh_once(ctx, self.refresh_credentials if self.oauth else None):
                return await go()
            raise

    async def _post(self, ctx: ConnectorContext, path: str, body: Mapping[str, Any] | None, *, expected: tuple[int, ...] = (200, 204)) -> HttpResult:
        async def go() -> HttpResult:
            return await ctx.http.request("POST", self._base(ctx) + path, json=dict(body or {}), expected=expected)
        try:
            return await go()
        except AuthExpired:
            if await refresh_once(ctx, self.refresh_credentials if self.oauth else None):
                return await go()
            raise

    @staticmethod
    def _rate(ctx: ConnectorContext) -> dict[str, Any]:
        snap = getattr(ctx.http, "rate_snapshot", None)
        return snap() if callable(snap) else {}

    @staticmethod
    def _page_size(ctx: ConnectorContext) -> int:
        return max(1, min(500, int(ctx.limits.page_size)))

    @staticmethod
    def _fetched(ctx: ConnectorContext) -> str:
        return iso_utc(ctx.clock())

    @staticmethod
    def _label(source: SourceDescriptor) -> str:
        if not _LABEL.match(source.external_id or ""):
            raise PermanentError("source is not a Gmail label", code="bad_source", detail={"source": source.external_id[:64]})
        return source.external_id

    def _validate_config(self, ctx: ConnectorContext) -> None:
        cfg = ctx.config
        if cfg.get("principal_map") is not None and not isinstance(cfg.get("principal_map"), dict):
            raise PermanentError("principal_map must be an object", code="bad_config")
        doms = cfg.get("internal_domains")
        if doms is not None and (not isinstance(doms, list) or not all(isinstance(d, str) for d in doms)):
            raise PermanentError("internal_domains must be a list of domains", code="bad_config")

    async def _message(self, ctx: ConnectorContext, message_id: str) -> dict[str, Any] | None:
        """``messages/{id}?format=full``; ``None`` when Gmail answers 404 (deleted, or never existed)."""
        if not _MSG_ID.match(message_id or ""):
            return None
        try:
            res = await self._get(ctx, f"/messages/{message_id}", params={"format": "full"})
        except ObjectGone:
            return None
        msg = res.json if isinstance(res.json, dict) else None
        if msg is None or str(msg.get("id")) != message_id:
            raise PermanentError("unexpected message response", code="bad_response")
        return msg

    # ------------------------------------------------------------------ lifecycle
    async def connect(self, ctx: ConnectorContext) -> ConnectResult:
        """The scope Google reported with the token, then ``profile`` (identity only)."""
        self._validate_config(ctx)
        granted, warnings = await self.check_scopes(ctx)
        res = await self._get(ctx, "/profile")
        data = res.json if isinstance(res.json, dict) else {}
        email = valid_email(data.get("emailAddress"))
        if email is None:
            raise PermanentError("unexpected profile response", code="bad_response")
        if not ctx.config.get("pubsub_topic"):
            warnings.append("no Pub/Sub topic configured: changes arrive by polling only")
        return ConnectResult(source_account_id=email, auth_account_id=email, account_label=email, granted_scopes=granted, warnings=tuple(warnings))

    async def health(self, ctx: ConnectorContext) -> HealthReport:
        try:
            await self._get(ctx, "/profile")
        except AuthRevoked as exc:
            return HealthReport("revoked", {"auth": False, "api_reachable": True}, {}, exc.code)
        except AuthExpired as exc:
            return HealthReport("auth_expired", {"auth": False, "api_reachable": True}, {}, exc.code)
        except RateLimited as exc:
            return HealthReport("rate_limited", {"auth": True, "api_reachable": True}, {"retry_after": exc.retry_after}, exc.code)
        except TransientError as exc:
            return HealthReport("degraded", {"auth": True, "api_reachable": False}, {}, exc.code)
        except ConnectorError as exc:
            return HealthReport("error", {"auth": False, "api_reachable": True}, {}, exc.code)
        return HealthReport("ok", {"auth": True, "api_reachable": True, "scopes": True}, {})

    async def start_push(self, ctx: ConnectorContext, label_ids: Iterable[str]) -> dict[str, Any]:
        """``users.watch`` for the given labels on ``config.pubsub_topic``. Returns ``{historyId, expiration}`` (ids only)."""
        topic = str(ctx.config.get("pubsub_topic") or "")
        if not re.match(r"^projects/[a-z][a-z0-9\-]{4,61}/topics/[A-Za-z][\w\-.~+%]{2,254}$", topic):
            raise PermanentError("config.pubsub_topic must be projects/<project>/topics/<topic>", code="bad_config")
        labels = [x for x in label_ids if _LABEL.match(x)]
        res = await self._post(ctx, "/watch", {"topicName": topic, "labelIds": labels, "labelFilterBehavior": "include"}, expected=(200,))
        data = res.json if isinstance(res.json, dict) else {}
        return {"historyId": str(data.get("historyId") or ""), "expiration": str(data.get("expiration") or "")}

    async def disconnect(self, ctx: ConnectorContext, *, revoke_at_provider: bool) -> None:
        if ctx.config.get("pubsub_topic"):
            try:
                await self._post(ctx, "/stop", None)
            except ConnectorError:
                pass
        if revoke_at_provider:
            await self.revoke(await ctx.secrets.get())

    # ------------------------------------------------------------------ sources
    async def discover_sources(self, ctx: ConnectorContext) -> AsyncIterator[SourceDescriptor]:
        self._validate_config(ctx)
        res = await self._get(ctx, "/labels")
        labels = (res.json or {}).get("labels") if isinstance(res.json, dict) else None
        for lb in sorted((x for x in labels or [] if isinstance(x, dict)), key=lambda x: (x.get("type") != "user", str(x.get("id")))):
            lid, ltype = str(lb.get("id") or ""), str(lb.get("type") or "user")
            if not _LABEL.match(lid) or lid in STATE_LABELS:
                continue
            meta: dict[str, Any] = {"label_type": ltype}
            if lid in EXCLUDED_LABELS:
                meta.update(default_selection="excluded", default_reason="default_spam_trash_draft_excluded")
            elif lid in REVIEW_LABELS or lid.startswith("CATEGORY_"):
                meta.update(default_selection="pending_review", default_reason="review_required_never_auto")
            elif ltype != "user":
                continue                                         # other system labels: nothing an owner would choose
            # a mailbox is personal: private, no source members (each message lists its own participants)
            yield SourceDescriptor(source_type="label", external_id=lid, name=str(lb.get("name") or lid)[:200], visibility="private",
                                   metadata=meta)

    # ------------------------------------------------------------------ pull
    def _raw(self, ctx: ConnectorContext, source: SourceDescriptor, msg: Mapping[str, Any]) -> RawItem:
        return RawItem(object_type="message", payload=msg, source=source, fetched_at=self._fetched(ctx))

    @staticmethod
    def _in_label(msg: Mapping[str, Any], label: str) -> bool:
        labels = set(msg.get("labelIds") or [])
        return label in labels and (label in OUT_OF_MAILBOX or not labels & OUT_OF_MAILBOX)

    async def _list_pages(self, ctx: ConnectorContext, source: SourceDescriptor, q: str, token: str | None,
                          keep: Any) -> AsyncIterator[tuple[list[RawItem], str | None]]:
        """``messages.list`` of one label, page by page, each id fetched with ``format=full``; ``keep(msg)`` filters."""
        label = self._label(source)
        while True:
            if ctx.cancelled.is_set():
                return
            params: dict[str, Any] = {"labelIds": label, "q": q, "maxResults": self._page_size(ctx)}
            if token:
                params["pageToken"] = token
            res = await self._get(ctx, "/messages", params=params)
            data = res.json if isinstance(res.json, dict) else {}
            items: list[RawItem] = []
            for ref in data.get("messages") or []:          # Gmail omits "messages" altogether when nothing matches
                mid = str((ref or {}).get("id") or "") if isinstance(ref, dict) else ""
                msg = await self._message(ctx, mid)
                if msg is not None and self._in_label(msg, label) and keep(msg):
                    items.append(self._raw(ctx, source, msg))
            token = str(data.get("nextPageToken") or "") or None
            yield items, token
            if not token:
                return

    async def initial_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, window: BackfillWindow,
                               cursor: Cursor | None) -> AsyncIterator[Page]:
        """Messages of the label with ``internalDate`` in ``[window.start, window.end)``, newest first, resumable."""
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        if data.get("done"):
            return
        lo, hi = _epoch_ms(window.start), _epoch_ms(window.end)
        q = f"after:{lo // 1000 - 1} before:{-(-hi // 1000) + 1}"
        async for items, token in self._list_pages(ctx, source, q, data.get("page"), lambda m: lo <= _int(m.get("internalDate")) < hi):
            if token:
                yield Page(stream=window.stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"page": token}), has_more=True, rate=self._rate(ctx))
            else:
                yield Page(stream=window.stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"done": True}, normalize_ts(window.end)),
                           has_more=False, rate=self._rate(ctx))

    async def incremental_sync(self, ctx: ConnectorContext, source: SourceDescriptor, cursor: Cursor | None) -> AsyncIterator[Page]:
        """The label's history since the stored ``historyId``; without one, the current history id and then a listing of the
        recent window (see module docstring)."""
        self._label(source)
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        stream = f"incr:{source.external_id}"
        if _DIGITS.match(str(data.get("history_id") or "")):
            async for page in self._history_pass(ctx, source, data, stream):
                yield page
            return
        relist = data.get("relist") if isinstance(data.get("relist"), dict) else {}
        start = str(relist.get("history_id") or "")
        if not _DIGITS.match(start):
            prof = await self._get(ctx, "/profile")      # before listing: whatever changes during the listing is in the history
            start = str((prof.json or {}).get("historyId") or "") if isinstance(prof.json, dict) else ""
            if not _DIGITS.match(start):
                raise PermanentError("profile has no history id", code="bad_response")
        lookback = int(ctx.config.get("initial_lookback_days") or ctx.limits.backfill_window_days)
        after = int(relist.get("after") or int((ctx.clock() - timedelta(days=max(1, lookback))).timestamp()))
        now = iso_utc(ctx.clock())
        async for items, token in self._list_pages(ctx, source, f"after:{after - 1}", relist.get("page"), lambda m: True):
            if token:
                yield Page(stream=stream, items=items, has_more=True, rate=self._rate(ctx),
                           next_cursor=Cursor(CURSOR_VERSION, {"relist": {"history_id": start, "after": after, "page": token}}))
            else:
                yield Page(stream=stream, items=items, has_more=False, rate=self._rate(ctx),
                           next_cursor=Cursor(CURSOR_VERSION, {"history_id": start}, now))

    async def _history_pass(self, ctx: ConnectorContext, source: SourceDescriptor, data: Mapping[str, Any], stream: str) -> AsyncIterator[Page]:
        label = source.external_id
        start = str(data["history_id"])
        token = str(data.get("page") or "") or None
        mailbox_ok = False
        while True:
            if ctx.cancelled.is_set():
                return
            query = [("startHistoryId", start), ("labelId", label), ("maxResults", str(self._page_size(ctx)))]
            query += [("historyTypes", t) for t in HISTORY_TYPES]
            if token:
                query.append(("pageToken", token))
            try:
                res = await self._get(ctx, "/history", query=query)
            except ObjectGone:
                # the start id is older than the history Gmail keeps (or the page token expired): start over
                raise CursorInvalid("the Gmail history id expired", detail={"source": label}) from None
            body = res.json if isinstance(res.json, dict) else {}
            fate: dict[str, str] = {}
            for rec in body.get("history") or []:
                if not isinstance(rec, dict):
                    continue
                for key in ("messagesAdded", "labelsAdded", "labelsRemoved", "messagesDeleted"):
                    for entry in rec.get(key) or []:
                        mid = str(((entry or {}).get("message") or {}).get("id") or "") if isinstance(entry, dict) else ""
                        if _MSG_ID.match(mid):
                            fate[mid] = "deleted" if (key == "messagesDeleted" or fate.get(mid) == "deleted") else "fetch"
            fetched = self._fetched(ctx)
            items: list[RawItem] = []
            for mid, what in fate.items():
                if what == "deleted":
                    items.append(RawItem("deletion", {"id": mid, "reason": "deleted_at_source"}, source, fetched))
                    continue
                msg = await self._message(ctx, mid)
                if msg is None:
                    if not mailbox_ok:                   # 404 while the mailbox answers: deleted (an unreadable mailbox raises here)
                        await self._get(ctx, "/profile")
                        mailbox_ok = True
                    items.append(RawItem("deletion", {"id": mid, "reason": "deleted_at_source"}, source, fetched))
                elif self._in_label(msg, label):
                    items.append(self._raw(ctx, source, msg))
                else:
                    labels = set(msg.get("labelIds") or [])
                    still = [] if labels & OUT_OF_MAILBOX else sorted(labels - STATE_LABELS)
                    items.append(RawItem("deletion", {"id": mid, "thread_id": str(msg.get("threadId") or ""), "reason": "excluded_source",
                                                      "still_in": still}, source, fetched))
            token = str(body.get("nextPageToken") or "") or None
            if token:
                yield Page(stream=stream, items=items, has_more=True, rate=self._rate(ctx),
                           next_cursor=Cursor(CURSOR_VERSION, {"history_id": start, "page": token}))
                continue
            latest = str(body.get("historyId") or start)
            yield Page(stream=stream, items=items, has_more=False, rate=self._rate(ctx),
                       next_cursor=Cursor(CURSOR_VERSION, {"history_id": latest if _DIGITS.match(latest) else start}, iso_utc(ctx.clock())))
            return

    # ------------------------------------------------------------------ push (Cloud Pub/Sub)
    @classmethod
    def with_query_token(cls, headers: Mapping[str, str], query: Mapping[str, str]) -> dict[str, str]:
        """What the webhook endpoint passes to :meth:`verify_webhook`: the request headers plus the push URL's ``token``
        query parameter as ``X-Mycelic-Push-Token`` (a sender setting that header itself still has to know the secret)."""
        out = {k.lower(): str(v) for k, v in headers.items()}
        token = query.get("token") if query else None
        if token:
            out[PUSH_TOKEN_HEADER] = str(token)
        return out

    @classmethod
    def verify_webhook(cls, headers: Mapping[str, str], body: bytes, secret: bytes, *, now: float) -> bool:
        """The shared secret of the push URL (``?token=``, passed in as ``X-Mycelic-Push-Token``), constant-time. Pub/Sub
        signs nothing by itself; replays are stopped by the ``messageId`` dedupe. OIDC JWT verification is not implemented."""
        token = header(headers, PUSH_TOKEN_HEADER)
        if not token or not secret:
            return False
        return hmac.compare_digest(token.encode("utf-8", errors="replace"), bytes(secret))

    @classmethod
    def parse_webhook(cls, headers: Mapping[str, str], body: bytes) -> list[WebhookNotice]:
        """A Pub/Sub push: ``message.data`` is base64 JSON ``{emailAddress, historyId}``. Ids only: the dedupe key is the
        Pub/Sub ``messageId``; the notice is account-wide (``source_external_id='*'``), so the holder runs an incremental
        history fetch of every included label."""
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return []
        msg = data.get("message") if isinstance(data, dict) and isinstance(data.get("message"), dict) else None
        if msg is None:
            return []
        mid = str(msg.get("messageId") or msg.get("message_id") or "")
        if not _PUBSUB_ID.match(mid):
            return []
        try:
            inner = json.loads(base64.b64decode(str(msg.get("data") or ""), validate=False).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return []
        if not isinstance(inner, dict):
            return []
        email, history = valid_email(inner.get("emailAddress")), str(inner.get("historyId") or "")
        if email is None or not _DIGITS.match(history):
            return []
        return [WebhookNotice(connector_type=cls.manifest.connector_type, delivery_id=f"pubsub:{mid}", external_account_id=email, source_external_id="*",
                              action="history", object_refs=({"type": "history", "id": history},),
                              occurred_at=normalize_ts(msg.get("publishTime") or msg.get("publish_time")))]

    # ------------------------------------------------------------------ normalize (pure)
    def normalize(self, raw: RawItem, ctx: ConnectorContext) -> list[CanonicalEvent]:
        p = raw.payload
        if not isinstance(p, dict):
            raise PermanentError("invalid Gmail item", code="normalize_failed")
        common: dict[str, Any] = dict(tenant_id=ctx.tenant_id, holder_id=ctx.holder_id, connector_id=ctx.connector_id, source_app=ctx.source_app,
                                      source_account_id=ctx.source_account_id, observed_at=raw.fetched_at)
        try:
            if raw.object_type == "deletion":
                mid = str(p["id"])
                if not _MSG_ID.match(mid):
                    raise ValueError("bad id")
                reason = str(p.get("reason") or "deleted_at_source")
                hints: dict[str, Any] = {"reason": reason, "label": raw.source.external_id}
                if reason == "excluded_source":
                    hints.update(if_known=True, still_in=[str(x) for x in p.get("still_in") or []])
                return [CanonicalEvent.create(kind="deletion", source_object_type="gmail_message", source_object_id=mid, updated_at=raw.fetched_at,
                                              tiebreak="deleted", permissions=Permissions("private"), hints=hints, **common)]
            if raw.object_type == "message":
                return self._message_events(p, raw, ctx, common)
        except (KeyError, TypeError, ValueError):
            raise PermanentError("malformed Gmail item", code="normalize_failed", detail={"object_type": raw.object_type}) from None
        raise PermanentError("unknown Gmail item type", code="normalize_failed", detail={"object_type": raw.object_type})

    def _message_events(self, msg: Mapping[str, Any], raw: RawItem, ctx: ConnectorContext, common: dict[str, Any]) -> list[CanonicalEvent]:
        mid, thread = str(msg["id"]), str(msg.get("threadId") or msg["id"])
        if not _MSG_ID.match(mid) or not _MSG_ID.match(thread):
            raise ValueError("bad id")
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        hdrs = _headers(payload)
        subject = (hdrs.get("subject") or [""])[0].strip()
        sender = [a for a in _addresses(hdrs.get("from")) if a]
        to, cc = _addresses(hdrs.get("to")), _addresses(hdrs.get("cc"))
        author = sender[0] if sender else None
        participants = list(dict.fromkeys(a for a in [*sender, *to, *cc] if a))
        owner = valid_email(ctx.auth_account_id) or ""
        text, files = _body_and_attachments(payload, mid)
        if not text.strip() and not subject:
            return []
        perms = Permissions("private", email_principals(ctx, ctx.source_app, [owner, *participants]))
        hid = str(msg.get("historyId") or "")
        internal = _int(msg.get("internalDate"))
        created = iso_utc(datetime.fromtimestamp(internal / 1000, tz=timezone.utc)) if internal else None
        own_domains = {owner.rsplit("@", 1)[-1]} if owner else set()
        own_domains |= {str(d).lower() for d in ctx.config.get("internal_domains") or []}
        auto = bool(hdrs.get("list-id") or hdrs.get("x-github-reason") or
                    any(v.strip().lower() not in ("", "no") for v in hdrs.get("auto-submitted") or []) or
                    any(v.strip().lower() in ("bulk", "list", "junk") for v in hdrs.get("precedence") or []))
        thread_title = _SUBJECT_PREFIX.sub("", subject).strip() or subject or "(no subject)"
        labels = sorted(str(x) for x in msg.get("labelIds") or [] if str(x) not in STATE_LABELS and not str(x).startswith("CATEGORY_"))
        hints: dict[str, Any] = {"container_name": thread_title, "container_kind": "email_thread", "labels": labels, "thread_id": thread,
                                 "rfc822_message_id": (hdrs.get("message-id") or [None])[0], "in_reply_to": (hdrs.get("in-reply-to") or [None])[0],
                                 "org_domains": sorted({a.rsplit("@", 1)[-1] for a in participants} - own_domains), "is_bot": auto,
                                 "auto_generated": auto}
        if any(f.size_bytes and f.size_bytes > 0 for f in files):
            hints["attachments"] = len(files)
        tiebreak = f"{int(hid):020d}" if _DIGITS.match(hid) else None
        conv = CanonicalEvent.create(kind="conversation", source_object_type="conversation", source_object_id=thread, created_at=created,
                                     title=thread_title, permissions=perms, hints={"container_name": thread_title, "container_kind": "email_thread"},
                                     **common)
        message = CanonicalEvent.create(kind="message", source_object_type="gmail_message", source_object_id=mid, source_version=hid,
                                        conversation_id=thread, author_id=author, participant_ids=participants, created_at=created, updated_at=created,
                                        title=subject, body=text, content_type="text/plain", attachment_references=files, permissions=perms,
                                        hints=hints, links=extract_urls(text), tiebreak=tiebreak, **common)
        return [conv, message]


# ---------------------------------------------------------------------------------------------- MIME helpers (pure)
def _headers(part: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for h in part.get("headers") or []:
        if isinstance(h, dict) and h.get("name"):
            out.setdefault(str(h["name"]).lower(), []).append(str(h.get("value") or ""))
    return out


def _addresses(values: list[str] | None) -> list[str]:
    out = []
    for _name, addr in getaddresses(values or []):
        e = valid_email(addr)
        if e and e not in out:
            out.append(e)
    return out


def _decode(body: Mapping[str, Any], part_headers: Mapping[str, list[str]]) -> str:
    data = str(body.get("data") or "")
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    m = _CHARSET.search(" ".join(part_headers.get("content-type") or []))
    charset = m.group(1) if m else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _body_and_attachments(payload: Mapping[str, Any], message_id: str) -> tuple[str, tuple[AttachmentRef, ...]]:
    """The message text (all inline ``text/plain`` parts; else the HTML parts converted) and attachment metadata."""
    plain: list[str] = []
    html: list[str] = []
    files: list[AttachmentRef] = []

    def walk(part: Mapping[str, Any], depth: int) -> None:
        if depth > 12:
            return
        mt = str(part.get("mimeType") or "").lower()
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        hdrs = _headers(part)
        disposition = " ".join(hdrs.get("content-disposition") or []).lower()
        filename = str(part.get("filename") or "")
        if mt.startswith("multipart/"):
            for sub in part.get("parts") or []:
                if isinstance(sub, dict):
                    walk(sub, depth + 1)
            return
        if filename or body.get("attachmentId") or disposition.startswith("attachment") or mt == "message/rfc822":
            if filename:
                size = body.get("size")
                files.append(AttachmentRef(attachment_id=f"{message_id}:{part.get('partId') or ''}", filename=filename[:255], content_type=mt or
                                           "application/octet-stream", size_bytes=int(size) if isinstance(size, int) else None))
            return
        if mt == "text/plain":
            plain.append(_decode(body, hdrs))
        elif mt == "text/html":
            html.append(_decode(body, hdrs))

    walk(payload, 0)
    if any(t.strip() for t in plain):
        text = "\n\n".join(t for t in plain if t.strip())
    elif html:
        text = "\n\n".join(_html_with_quotes(h) for h in html if h.strip())
    else:
        text = ""
    return _join_attribution(text.replace("\r\n", "\n").replace("\r", "\n")), tuple(files)


def _html_with_quotes(markup: str) -> str:
    """HTML to text with every top-level ``<blockquote>`` (Gmail's quoted reply) turned into ``> `` lines, so the quote
    splitting of §4.5 sees it; everything else goes through :func:`html_to_text` (scripts and styles dropped)."""
    out: list[str] = []
    depth, start, last = 0, 0, 0
    for m in _BLOCKQUOTE.finditer(markup):
        closing = m.group(1) == "/"
        if not closing:
            if depth == 0:
                out.append(markup[last:m.start()])
                start = m.end()
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                quoted = html_to_text(markup[start:m.start()])
                lines = "<br>".join(_escape("> " + ln if ln.strip() else ">") for ln in quoted.split("\n"))
                out.append(f"<div>{lines}</div>")
                last = m.end()
    out.append(markup[last:] if depth == 0 else _escape_tail(markup[start:]))
    return html_to_text("".join(out))


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_tail(markup: str) -> str:
    """An unterminated blockquote: everything after it is quoted."""
    quoted = html_to_text(markup)
    return "<div>" + "<br>".join(_escape("> " + ln) for ln in quoted.split("\n")) + "</div>"


def _join_attribution(text: str) -> str:
    """Gmail writes "On <date> <name> wrote:" (sometimes wrapped before "wrote:") and a blank line before the quote; joined
    so that :func:`mycelic.ingest.normalize.split_body` recognises the attribution as part of the quote."""
    return _ATTRIBUTION_GAP.sub(r"\1\n", _ATTRIBUTION_WRAPPED.sub(r"\1 \2", text))


def _int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _epoch_ms(iso: str) -> int:
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)


__all__ = ["GmailConnector", "PUSH_TOKEN_HEADER", "READONLY"]
