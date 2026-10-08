"""The Gmail connector against the offline Gmail mock, end to end through ``IngestPipeline`` into a real holder store:
connect and scopes (personal ownership only), label discovery with safe defaults, backfill windows on ``internalDate``,
incremental history, history expiry and the re-listing that follows, crash recovery, Google's rate limits, Pub/Sub push
(token check, ids-only parsing, notify-then-fetch, redelivery), deletions and leaving a label (only when no included label
keeps the message), quoted replies, HTML, forwards sharing the original's root, attachments as metadata, the private
mailbox ACL, OAuth code + PKCE against the mock, the mock's response shapes, and no content in logs."""
from __future__ import annotations

import base64
import json
import logging
import os
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from mycelic.ingest.connectors import register_builtin
from mycelic.ingest.connectors.gmail import GmailConnector
from mycelic.ingest.connectors.scaffolds import SCAFFOLD_CONNECTORS
from mycelic.ingest.contract import (AuthExpired, ConnectorContext, ConnectorLimits, InsufficientScope, PermanentError, RawItem, Secret,
                                     SourceDescriptor, connector_logger)
from mycelic.ingest.mocks import FakeClock, load_fixture
from mycelic.ingest.mocks.gmail_mock import GmailMock
from mycelic.ingest.oauth import OAuthAppConfig
from mycelic.ingest.registry import ConnectorRegistry, validate_manifest
from mycelic.ingest.service import IngestService
from mycelic.util import fingerprint

from .connector_support import (BEN, DEE, EMAIL_PRINCIPALS, GM_TOKEN, GMAIL_SCOPE, Collect, Env, full_sync, gmail_env, google_creds,
                                pin_anchor)
from .ingest_support import OWNER, TENANT, make_pipeline, make_store, question, table_contains

OWNER_READ = {"principal_ids": [], "complete": True, "owner": True}
PUSH_SECRET = b"mock-push-token-not-real"
CUSTOMERS, GITHUB_LABEL, NEWS = "Label_101", "Label_102", "Label_103"
M_RENEWAL, M_REPLY, M_BEN, M_NOTIF, M_FWD = "18f2a0c0de000001", "18f2a0c0de000002", "18f2a0c0de000007", "18f29c0de0000003", "18f2a0c0de000004"
M_NEWS, M_MISTAKE, M_QBR, M_OLD, M_TRASHED = "18f2a0c0de000005", "18f2a0c0de000006", "18f2a0c0de00000a", "18f2a0c0de00000b", "18f2a0c0de00000c"
REDIRECT = "https://mycelic.example/api/integrations/oauth/gmail/callback"
METADATA_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"


async def push(env: Env, delivery, *, secret: bytes = PUSH_SECRET):
    """What the webhook endpoint does with a Pub/Sub push: the URL's token into the headers, verify, parse (ids only), and the
    holder handles each notice."""
    headers = GmailConnector.with_query_token(delivery.headers, delivery.query)
    assert GmailConnector.verify_webhook(headers, delivery.body, secret, now=env.clock())
    reports = [await env.svc.handle_notice(env.cid, n) for n in GmailConnector.parse_webhook(headers, delivery.body)]
    await env.pipe.process_available()
    return reports


async def sync(env: Env, mode: str = "incremental"):
    report = await env.pipe.sync(env.cid, mode=mode)
    await env.pipe.process_available()
    return report


def record(env: Env, object_id: str) -> dict:
    return next(r for r in env.records() if r["source_object_id"] == object_id and r["kind"] == "message")


def tombstone_reason(env: Env, object_id: str) -> str | None:
    row = env.store.store._conn.execute("SELECT t.reason FROM deletion_tombstones t JOIN ingest_records r ON r.record_key=t.record_key "
                                        "WHERE r.source_object_id=?", (object_id,)).fetchone()
    return row["reason"] if row else None


def cited(resp: dict, root: str) -> list[dict]:
    return [r for r in resp["evidence_refs"] if r["source_root_id"] == root]


def _ctx(**config) -> ConnectorContext:
    return ConnectorContext(tenant_id=TENANT, holder_id="hold_x", connector_id="con_x", connector_type="gmail", source_app="gmail",
                            source_account_id="ana@acme.example", auth_account_id="ana@acme.example", config={"principal_map": EMAIL_PRINCIPALS, **config},
                            limits=ConnectorLimits(), http=None, secrets=None, checkpoints=None, log=connector_logger("gmail", "con_x"),
                            clock=FakeClock(0).datetime)


# ---------------------------------------------------------------------------------------------- manifest
def test_manifest_is_registered_personal_only_and_honest() -> None:
    m = register_builtin(ConnectorRegistry()).get("gmail").manifest
    assert validate_manifest(m) == [] and m.status == "tested-offline" and m.ownership == ("personal",)
    assert [(s.scope, s.required) for s in m.scopes] == [(GMAIL_SCOPE, True)] and m.auth_kinds == ("oauth2",)
    assert m.allowed_hosts == ("gmail.googleapis.com", "oauth2.googleapis.com", "accounts.google.com")
    notes = m.terms_notes
    assert "not live-verified" in notes and "OIDC" in notes and "never downloaded" in notes and "Limited Use" in notes
    assert "gmail" not in {c.manifest.connector_type for c in SCAFFOLD_CONNECTORS}


# ---------------------------------------------------------------------------------------------- connect and discover
async def test_connect_identity_scopes_and_refusals(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, token=None)
    try:
        cfg = {"api_base": env.url}
        con = await env.svc.add_connector("gmail", created_by=OWNER, config=cfg, credentials=google_creds(GM_TOKEN, GMAIL_SCOPE))
        assert (con["source_account_id"], con["auth_account_id"], con["account_label"]) == ("ana@acme.example",) * 3
        assert con["granted_scopes"] == [GMAIL_SCOPE] and any("Pub/Sub" in w for w in con["warnings"])
        assert [r.path for r in env.mock.requests] == ["/gmail/v1/users/me/profile"]                # identity only, no content
        await env.svc.disconnect(con["connector_id"], actor=OWNER)
        n = len(env.mock.requests)
        with pytest.raises(InsufficientScope) as e:            # the scope Google reported cannot read message bodies: refused locally
            await env.svc.add_connector("gmail", created_by=OWNER, config=cfg, credentials=google_creds("ya29.mock-ana-gmail-metadata", METADATA_SCOPE))
        assert e.value.missing == (GMAIL_SCOPE,) and len(env.mock.requests) == n
        with pytest.raises(InsufficientScope):                 # a token entered without its scope: Google's 403 decides
            await env.svc.add_connector("gmail", created_by=OWNER, config=cfg, credentials=google_creds("ya29.mock-ana-drive-only", None))
        modify = await env.svc.add_connector("gmail", created_by=OWNER, credentials=google_creds("ya29.mock-ana-gmail-modify",
                                                                                                  "https://www.googleapis.com/auth/gmail.modify"),
                                             config={**cfg, "pubsub_topic": "projects/acme-mycelic/topics/gmail"})
        assert any("allow changes" in w for w in modify["warnings"]) and not any("Pub/Sub" in w for w in modify["warnings"])
        await env.svc.disconnect(modify["connector_id"], actor=OWNER)
        with pytest.raises(AuthExpired):
            await env.svc.add_connector("gmail", created_by=OWNER, config=cfg, credentials=google_creds("ya29.not-a-token", GMAIL_SCOPE))
        with pytest.raises(PermanentError):
            await env.svc.add_connector("gmail", created_by=OWNER, config={**cfg, "internal_domains": "acme.example"},
                                        credentials=google_creds(GM_TOKEN, GMAIL_SCOPE))
        # a mailbox is personal: a unit holder cannot connect one
        unit_store = make_store(tmp_path, "unit_holder")
        try:
            with pytest.raises(ValueError, match="org"):
                await IngestService(make_pipeline(unit_store, holder_kind="unit")).add_connector("gmail", created_by=OWNER)
        finally:
            await unit_store.close()
    finally:
        await env.close()


async def test_discovery_offers_labels_with_safe_defaults(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, include=False, config={"auto_include": True})
    try:
        srcs = {s.external_id: s for s in env.svc.sources(env.cid)}
        assert set(srcs) == {"INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "CATEGORY_PERSONAL", "CATEGORY_UPDATES", "CATEGORY_PROMOTIONS", CUSTOMERS,
                             GITHUB_LABEL, NEWS}                     # UNREAD, STARRED, IMPORTANT, CHAT are states, never offered
        for lid in ("SPAM", "TRASH", "DRAFT"):                      # excluded by default, like direct messages
            assert (srcs[lid].selection, srcs[lid].selection_reason) == ("excluded", "default_spam_trash_draft_excluded")
        for lid in ("INBOX", "SENT", "CATEGORY_UPDATES"):           # reviewed, never included by a rule (even auto_include: true)
            assert (srcs[lid].selection, srcs[lid].selection_reason) == ("pending_review", "review_required_never_auto")
        for lid in (CUSTOMERS, GITHUB_LABEL, NEWS):
            assert (srcs[lid].selection, srcs[lid].selection_reason, srcs[lid].source_type) == ("included", "auto_rule", "label")
        assert srcs[CUSTOMERS].name == "Customers"
        assert all(s.visibility == "private" and not s.exportable and s.member_ids == [] for s in srcs.values())
        assert [r.path for r in env.mock.requests if "messages" in r.path] == []                    # discovery reads no content
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- pull
async def test_backfill_windows_then_incremental_history(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, config={"limits": {"page_size": 2}})
    try:
        n = len(env.mock.requests)
        first = await sync(env)                   # no cursor: the current history id first, then each label's last 30 days
        assert first.error_code is None
        assert env.live_ids("gmail_message") == sorted([M_RENEWAL, M_REPLY, M_BEN, M_MISTAKE, M_NOTIF, M_FWD])
        paths = [r.path for r in env.mock.requests[n:]]
        assert paths.index("/gmail/v1/users/me/profile") < paths.index("/gmail/v1/users/me/messages")
        back = await sync(env, mode="backfill")
        assert back.error_code is None
        starts = [s.split(":", 2)[2] for s in back.streams if s.startswith(f"backfill:{CUSTOMERS}:")]
        assert len(starts) == 13 and starts == sorted(starts, reverse=True)                        # 30-day windows, newest first
        live = env.live_ids("gmail_message")
        assert M_QBR in live and M_OLD not in live and M_TRASHED not in live and M_NEWS not in live      # July yes; 2025, trash, other labels no
        assert len(live) == 7
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})   # exactly once each
        listing = [r for r in env.mock.requests if r.path == "/gmail/v1/users/me/messages"]
        windowed = [r for r in listing if "before:" in r.query.get("q", "")]
        assert windowed and all(r.query["labelIds"] in (CUSTOMERS, GITHUB_LABEL) and r.query["maxResults"] == "2" for r in listing)
        assert any(r.query.get("pageToken") for r in listing)
        gets = [r for r in env.mock.requests if r.path.startswith("/gmail/v1/users/me/messages/")]
        assert gets and all(r.query["format"] == "full" for r in gets)
        # a thread is a conversation; its messages point at it
        conv = next(r for r in rows if r["kind"] == "conversation" and r["source_object_id"] == M_RENEWAL)
        assert {record(env, m)["conversation_record_id"] for m in (M_RENEWAL, M_REPLY, M_BEN)} == {conv["record_id"]}
        assert record(env, M_RENEWAL)["current_version"].isdigit()                                   # the message's historyId
        # incremental: the label's history since the stored id
        env.clock.advance(600)
        env.mock.deliver(sender="Globex Operations <ops@globex.example>", to=["Ana Lima <ana@acme.example>"], thread_id=M_RENEWAL,
                         subject="Re: Customer renewal at risk", text="Thank you, we will review the remediation plan on Monday.",
                         labels=("INBOX", CUSTOMERS))
        n = len(env.mock.requests)
        r = await sync(env)
        hist = [x for x in env.mock.requests[n:] if x.path == "/gmail/v1/users/me/history"]
        assert r.error_code is None and r.enqueued >= 1 and hist and {x.query["labelId"] for x in hist} == {CUSTOMERS, GITHUB_LABEL}
        assert all(x.query["startHistoryId"].isdigit() for x in hist)
        assert env.mock.history_types_seen[-1] == ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]
        assert len(env.live_ids("gmail_message")) == 8
        assert (await sync(env)).enqueued == 0                                                        # nothing new: nothing enqueued
    finally:
        await env.close()


def test_normalize_quotes_html_forwards_attachments_and_acl() -> None:
    mock = GmailMock(load_fixture("gmail_acme"))
    gm = GmailConnector()
    ctx = _ctx()
    src = SourceDescriptor(source_type="label", external_id=CUSTOMERS, name="Customers", visibility="private")

    def norm(mid: str, **changes):
        m = {**mock.messages[mid], **changes}
        return gm.normalize(RawItem("message", mock.message_json(m), src, "2026-10-01T12:00:00+00:00"), ctx)
    conv, renewal = norm(M_RENEWAL)
    assert (conv.kind, conv.source_object_type, conv.source_object_id) == ("conversation", "conversation", M_RENEWAL)
    assert conv.title.startswith("Customer renewal at risk")
    assert (renewal.kind, renewal.source_object_type, renewal.source_object_id, renewal.conversation_id) == ("message", "gmail_message", M_RENEWAL, M_RENEWAL)
    assert renewal.author_id == "ops@globex.example" and renewal.participant_ids == ("ops@globex.example", "ana@acme.example", "ben@acme.example")
    assert renewal.permissions.visibility == "private" and renewal.permissions.member_ids == ("gmail:ops@globex.example", OWNER, BEN)
    assert renewal.hints["org_domains"] == ["globex.example"] and renewal.hints["rfc822_message_id"] == "<CAGlobexOps.0930.renewal@mail.globex.example>"
    assert "1 Example Way" not in renewal.body and renewal.body.startswith("Hello Ana")              # the "-- " signature is not the text
    assert renewal.source_version == str(mock.messages[M_RENEWAL]["historyId"])
    assert renewal.order_key.endswith("|" + f"{mock.messages[M_RENEWAL]['historyId']:020d}")
    [att] = renewal.attachment_references                    # metadata only, with an id that survives Gmail's changing attachmentId
    assert (att.attachment_id, att.filename, att.content_type, att.size_bytes) == (f"{M_RENEWAL}:1", "globex-failed-orders-september.csv", "text/csv", 18432)
    assert norm(M_RENEWAL)[1].content_hash == renewal.content_hash and "labels" in renewal.hints and "UNREAD" not in renewal.hints["labels"]
    # a reply's root is its own text: the quoted customer e-mail is neither indexed nor counted
    _, reply = norm(M_REPLY)
    assert "web shop" not in reply.body and reply.body.startswith("Hello,\n\nWe are sorry") and reply.hints.get("quoted_segments") == 1
    assert reply.source_root_id == fingerprint(reply.body) and reply.source_root_id != renewal.source_root_id
    assert reply.hints["in_reply_to"] == "<CAGlobexOps.0930.renewal@mail.globex.example>"
    # the same reply as HTML only: the blockquote is a quote too, and the root is the same
    _, html_reply = norm(M_REPLY, text=None)
    assert html_reply.source_root_id == reply.source_root_id and "web shop" not in html_reply.body and "<" not in html_reply.body
    _, ben = norm(M_BEN)
    assert ben.body == ("Thanks Ana, I am drafting the incident review for Globex now and will put it in the Incidents folder of the Engineering "
                        "drive.") and ben.hints.get("quoted_segments") == 1
    _, news = norm(M_NEWS)                                    # HTML only: scripts, styles and the head never become text
    assert "Tip 1: keep it to fifteen minutes." in news.body and "track(" not in news.body and "color" not in news.body
    assert news.hints["auto_generated"] is True
    # a forward with almost no text of its own is a pure copy: it shares the forwarded notification's root
    _, notif = norm(M_NOTIF)
    _, fwd = norm(M_FWD)
    assert notif.hints["auto_generated"] and "https://github.com/acme/checkout/issues/482" in notif.links
    assert fwd.root_method == "pure_copy" and fwd.source_root_id == notif.source_root_id and fwd.body == notif.body
    assert "https://github.com/acme/checkout/issues/482" in fwd.links and fwd.permissions.member_ids == (OWNER, DEE)
    # deletions carry no content; leaving a label is conditional on the record being known
    [gone] = gm.normalize(RawItem("deletion", {"id": M_RENEWAL, "reason": "excluded_source", "still_in": ["INBOX"]}, src, "2026-10-02T00:00:00+00:00"), ctx)
    assert gone.kind == "deletion" and gone.body == "" and gone.hints == {"reason": "excluded_source", "label": CUSTOMERS, "if_known": True,
                                                                        "still_in": ["INBOX"]}
    with pytest.raises(PermanentError):
        gm.normalize(RawItem("message", {"payload": {}}, src, "2026-10-01T12:00:00+00:00"), ctx)


async def test_expired_history_is_cursor_invalid_and_the_window_is_listed_again(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, include=["Customers"])
    try:
        await full_sync(env, backfill=False)
        env.clock.advance(3600)
        env.mock.deliver(sender="Globex Operations <ops@globex.example>", to=["Ana Lima <ana@acme.example>"], subject="Escalation",
                         text="Globex asks for a call with engineering tomorrow morning.", labels=("INBOX", CUSTOMERS))
        env.mock.expire_history()                     # the stored start id is older than the history Gmail keeps
        broken = await env.pipe.sync(env.cid)
        assert broken.error_code == "cursor_invalid" and env.connector_row()["status"] == "active"
        row = env.store.store._conn.execute("SELECT cursor, status, last_error_code FROM connector_checkpoints WHERE connector_id=? AND stream=?",
                                            (env.cid, f"incr:{CUSTOMERS}")).fetchone()
        assert (row["cursor"], row["status"], row["last_error_code"]) == ("{}", "error", "cursor_invalid")
        n = len(env.mock.requests)
        healed = await sync(env)
        assert healed.error_code is None
        paths = [r.path for r in env.mock.requests[n:]]
        assert paths[0] == "/gmail/v1/users/me/profile" and "/gmail/v1/users/me/history" not in paths    # history id first, then a listing
        hits = await env.store.search("call with engineering tomorrow", audience=OWNER_READ)
        assert hits and len(env.live_ids("gmail_message")) == 5
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})       # the re-listing added no duplicates
        env.clock.advance(60)                         # and the next pass is back on the history, from the profile's id
        env.mock.deliver(sender="Cy Martin <cy@acme.example>", to=["Ana Lima <ana@acme.example>"], subject="Call booked",
                         text="Booked the Globex call for 10:00.", labels=("INBOX", CUSTOMERS))
        n = len(env.mock.requests)
        again = await sync(env)
        assert again.error_code is None and len(env.live_ids("gmail_message")) == 6
        assert any(r.path == "/gmail/v1/users/me/history" and r.status == 200 for r in env.mock.requests[n:])
    finally:
        await env.close()


class CrashingGmail(GmailConnector):
    crash_after: int | None = None

    async def initial_backfill(self, ctx, source, window, cursor):
        n = 0
        async for page in super().initial_backfill(ctx, source, window, cursor):
            if type(self).crash_after is not None and n >= type(self).crash_after:
                raise RuntimeError("simulated crash in the middle of a backfill")
            n += 1
            yield page


async def test_crash_mid_backfill_resumes_without_duplicates_or_loss(tmp_path: Path) -> None:
    reg = register_builtin(ConnectorRegistry())
    reg.register(CrashingGmail, replace=True)
    env = await gmail_env(tmp_path, include=["Customers"], registry=reg,
                          config={"limits": {"page_size": 1, "backfill_days": 120, "backfill_window_days": 120}})
    try:
        CrashingGmail.crash_after = 2
        first = await env.pipe.sync(env.cid, mode="backfill")
        assert first.error_code == "connector_crash" and first.pages == 2
        [stream] = first.streams
        _cur, v1 = await env.pipe.queue.load_checkpoint(env.cid, stream)
        assert v1 == 2
        await env.pipe.process_available()
        assert len(env.live_ids("gmail_message")) == 2                  # what was committed is processed, nothing partial
        CrashingGmail.crash_after = None
        second = await sync(env, mode="backfill")
        assert second.error_code is None
        assert env.live_ids("gmail_message") == sorted([M_RENEWAL, M_REPLY, M_BEN, M_MISTAKE, M_QBR])
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})
        fetched = Counter(r.path for r in env.mock.requests if r.path.startswith("/gmail/v1/users/me/messages/"))
        assert max(fetched.values()) <= 2                                # no message fetched more than twice
        # a provider-side crash (connections dropped) mid-window: the stream errors, the cursor holds, the resume completes
        env.mock.deliver(sender="Globex Operations <ops@globex.example>", to=["Ana Lima <ana@acme.example>"], subject="Order volumes",
                         text="September order volumes are attached to the portal.", labels=(CUSTOMERS,), at="2026-09-10T09:00:00Z")
        env.store.store._conn.execute("DELETE FROM connector_checkpoints WHERE stream=?", (stream,))
        env.mock.crash_after_pages(3)
        broken = await env.pipe.sync(env.cid, mode="backfill")
        assert broken.error_code == "transient"
        env.mock.recover()
        healed = await sync(env, mode="backfill")
        assert healed.error_code is None and len(env.live_ids("gmail_message")) == 6
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})
    finally:
        CrashingGmail.crash_after = None
        await env.close()


async def test_google_rate_limits_back_off_inline_or_park_without_loss(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, include=["Customers"])
    try:
        env.mock.rate_limit_next(status=403, reason="userRateLimitExceeded")      # Google's shape, no Retry-After
        ok = await sync(env)
        assert ok.error_code is None and len(env.live_ids("gmail_message")) == 4
        assert any(1.0 <= s < 2.0 for s in env.clock.slept)                        # truncated exponential backoff: 1 s + jitter
        env.clock.advance(600)
        env.mock.deliver(sender="Globex Operations <ops@globex.example>", to=["Ana Lima <ana@acme.example>"], subject="Portal access",
                         text="Please renew the portal access for the Globex finance team.", labels=("INBOX", CUSTOMERS))
        env.mock.rate_limit_next(status=429, reason="rateLimitExceeded", count=4)
        before = len(env.clock.slept)
        parked = await env.pipe.sync(env.cid)
        assert parked.error_code == "rate_limited" and parked.enqueued == 0 and env.connector_row()["status"] == "active"
        waits = env.clock.slept[before:]
        assert len(waits) == 3 and [int(w) for w in waits] == [1, 2, 4]           # 1, 2, 4 s inline, then the stream is parked
        resumed = await sync(env)
        assert resumed.error_code is None and len(env.live_ids("gmail_message")) == 5
        env.mock.rate_limit_next(status=403, reason="dailyLimitExceeded")         # a daily quota parks at once, for an hour
        before = len(env.clock.slept)
        daily = await env.pipe.sync(env.cid)
        assert daily.error_code == "rate_limited" and len(env.clock.slept) == before
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- push
def test_push_token_verification_and_ids_only_parsing() -> None:
    mock = GmailMock(load_fixture("gmail_acme"), clock=lambda: 1790856000.0)
    sentinel = "Sentinelword4471"
    d = mock.deliver(sender="Globex Operations <ops@globex.example>", to=["ana@acme.example"], subject=f"{sentinel} renewal",
                     text=f"{sentinel} body", labels=("INBOX", CUSTOMERS))
    headers = GmailConnector.with_query_token(d.headers, d.query)
    assert GmailConnector.verify_webhook(headers, d.body, PUSH_SECRET, now=0)
    assert GmailConnector.verify_webhook({k.upper(): v for k, v in headers.items()}, d.body, PUSH_SECRET, now=0)
    wrong = mock.push_delivery(token="guessed-token")
    assert not GmailConnector.verify_webhook(GmailConnector.with_query_token(wrong.headers, wrong.query), wrong.body, PUSH_SECRET, now=0)
    assert not GmailConnector.verify_webhook(d.headers, d.body, PUSH_SECRET, now=0)                # no token in the URL
    assert not GmailConnector.verify_webhook(headers, d.body, b"", now=0)
    assert not GmailConnector.verify_webhook(headers, d.body, b"another-secret", now=0)
    [n] = GmailConnector.parse_webhook(headers, d.body)
    assert (n.connector_type, n.action, n.source_external_id, n.external_account_id) == ("gmail", "history", "*", "ana@acme.example")
    assert n.delivery_id == "pubsub:" + json.loads(d.body)["message"]["messageId"]                   # the dedupe key
    assert n.object_refs == ({"type": "history", "id": str(mock.history_id)},) and n.occurred_at
    for needle in (sentinel, "Globex", "renewal", "ops@globex.example"):
        assert needle not in repr(n), needle
    bad_data = json.dumps({"message": {"data": base64.b64encode(b'{"emailAddress": "x", "historyId": "1"}').decode(), "messageId": "1"}}).encode()
    assert GmailConnector.parse_webhook({}, b"not json") == [] and GmailConnector.parse_webhook({}, bad_data) == []
    assert GmailConnector.parse_webhook({}, json.dumps({"message": {"data": "!!", "messageId": "2"}}).encode()) == []
    assert GmailConnector.parse_webhook({}, json.dumps({"message": {"data": json.loads(d.body)["message"]["data"]}}).encode()) == []


async def test_push_notices_fetch_history_dedupe_and_confirm_deletions(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, include=["Customers"], config={"pubsub_topic": "projects/acme-mycelic/topics/gmail"})
    try:
        await full_sync(env, backfill=False)
        gm = env.pipe.connector(env.cid)
        ctx = env.pipe.context(env.connector_row())
        watch = await gm.start_push(ctx, [CUSTOMERS])                       # users.watch on the configured topic
        assert watch["historyId"].isdigit() and env.mock.watch == {"topicName": "projects/acme-mycelic/topics/gmail", "labelIds": [CUSTOMERS],
                                                                   "labelFilterBehavior": "include"}
        d = env.mock.deliver(sender="Globex Operations <ops@globex.example>", to=["Ana Lima <ana@acme.example>"], thread_id=M_RENEWAL,
                             subject="Re: Customer renewal at risk", text="We accept the remediation plan; the renewal can go ahead.",
                             labels=("INBOX", CUSTOMERS))
        [r] = await push(env, d)
        assert r.error_code is None and r.enqueued >= 1
        assert [h for h in await env.store.search("we accept the remediation plan", audience=OWNER_READ) if h["doc_id"]]
        [again] = await push(env, d)                                       # Pub/Sub redelivers the same message id: nothing happens
        assert again.duplicates == 1 and again.pages == 0
        n = len(env.mock.requests)
        [gone] = await push(env, env.mock.delete_message(M_MISTAKE))
        assert gone.error_code is None and M_MISTAKE not in env.live_ids("gmail_message")
        assert table_contains(env.store, "sent it by mistake") == [] and tombstone_reason(env, M_MISTAKE) == "deleted_at_source"
        assert not any(r.path.endswith(f"/messages/{M_MISTAKE}") for r in env.mock.requests[n:])   # the history said so: no fetch needed
        await env.svc.disconnect(env.cid, actor=OWNER)                     # users.stop
        assert env.mock.watch is None
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- deletions and leaving a label
async def test_leaving_a_label_or_the_mailbox_removes_records_only_when_no_included_label_keeps_them(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, include=["Customers", "GitHub"])
    try:
        await full_sync(env, backfill=False)
        # the forward also gets the Customers label, then loses GitHub: still in an included label, so it stays
        env.mock.modify_labels(M_FWD, add=[CUSTOMERS])
        await sync(env)
        env.mock.modify_labels(M_FWD, remove=[GITHUB_LABEL])
        r = await sync(env)
        assert r.error_code is None and M_FWD in env.live_ids("gmail_message") and tombstone_reason(env, M_FWD) is None
        # losing its only included label: the message left the source (INBOX is not included)
        env.mock.modify_labels(M_RENEWAL, remove=[CUSTOMERS])
        await sync(env)
        assert M_RENEWAL not in env.live_ids("gmail_message") and tombstone_reason(env, M_RENEWAL) == "excluded_source"
        assert table_contains(env.store, "Card payments from our web shop") == []
        # moved to the trash: Gmail keeps the user label, but spam and trash are outside the mailbox
        env.mock.trash(M_BEN)
        await sync(env)
        assert M_BEN not in env.live_ids("gmail_message") and tombstone_reason(env, M_BEN) == "excluded_source"
        # a message the holder never stored leaves no tombstone behind, so it can still be included later
        env.mock.modify_labels(M_NEWS, add=[CUSTOMERS])
        env.mock.modify_labels(M_NEWS, remove=[CUSTOMERS])
        quiet = await sync(env)
        assert quiet.error_code is None and quiet.excluded >= 1 and tombstone_reason(env, M_NEWS) is None
        env.mock.modify_labels(M_NEWS, add=[CUSTOMERS])
        await sync(env)
        assert M_NEWS in env.live_ids("gmail_message")
        # deleted without a history record: a 404 on the fetch while the mailbox still answers is a deletion
        env.mock.modify_labels(M_REPLY, add=["STARRED"])
        env.mock.messages[M_REPLY]["deleted"] = True
        n = len(env.mock.requests)
        await sync(env)
        later = [(r.path, r.status) for r in env.mock.requests[n:]]
        assert (f"/gmail/v1/users/me/messages/{M_REPLY}", 404) in later
        assert later.index(("/gmail/v1/users/me/profile", 200)) > later.index((f"/gmail/v1/users/me/messages/{M_REPLY}", 404))
        assert M_REPLY not in env.live_ids("gmail_message") and tombstone_reason(env, M_REPLY) == "deleted_at_source"
        # sticky: a stale state of a deleted message never comes back
        env.mock.messages[M_REPLY]["deleted"] = False
        env.mock.modify_labels(M_REPLY, remove=["STARRED"])
        await sync(env)
        assert M_REPLY not in env.live_ids("gmail_message")
    finally:
        await env.close()


async def test_a_deletion_is_never_inferred_from_an_unreadable_mailbox(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, include=["Customers"])
    try:
        await full_sync(env, backfill=False)
        env.mock.modify_labels(M_REPLY, add=["STARRED"])
        env.mock.messages[M_REPLY]["deleted"] = True
        env.mock.rate_limit_next(status=403, reason="dailyLimitExceeded", path_contains="/profile")   # the confirmation cannot be made
        report = await sync(env)
        assert report.error_code == "rate_limited" and M_REPLY in env.live_ids("gmail_message")
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- permissions
async def test_the_mailbox_is_private_until_the_owner_opts_a_label_in(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, include=["Customers"])
    try:
        await full_sync(env, backfill=False)
        reply = record(env, M_REPLY)
        q = "What caused the checkout timeouts behind the Globex renewal risk, and when was httpclient rolled back?"

        async def ask(audience):
            return await env.store.answer_question(question(q, audience=audience))
        assert cited(await ask({"owner": True}), reply["source_root_id"])
        ben = {"principal_ids": [BEN], "complete": True}
        assert not cited(await ask(ben), reply["source_root_id"])                         # a participant, but the label is not opted in
        src = env.source(CUSTOMERS)
        await env.svc.set_source(src.source_id, actor=OWNER, exportable=True)
        assert cited(await ask(ben), reply["source_root_id"])                             # opted in: Ben was on the thread
        for outsider in ({"principal_ids": [DEE], "complete": True}, {"principal_ids": [BEN, DEE], "complete": True},
                         {"principal_ids": [BEN], "complete": False}, None):
            assert not cited(await ask(outsider), reply["source_root_id"])
        assert json.loads(reply["permissions"]) == {"visibility": "private", "member_ids": ["gmail:ops@globex.example", OWNER, BEN],
                                                    "membership_ref": None, "acl_version": None}
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- OAuth
async def test_oauth_code_pkce_refresh_and_revocation_against_the_mock(tmp_path: Path) -> None:
    fx = load_fixture("gmail_acme")
    env = await gmail_env(tmp_path, token=None, fixture=fx)
    try:
        app = OAuthAppConfig(client_id=fx["app"]["client_id"], client_secret=Secret(fx["app"]["client_secret"]), oauth_base=env.url,
                             api_base=env.url, allow_loopback_http=True)
        gm = GmailConnector(oauth=app)
        start = await gm.authorize(tenant_id=TENANT, holder_id="h", redirect_uri=REDIRECT, state="st-123")
        u = urlsplit(start.url)
        q = parse_qs(u.query)
        assert u.path == "/o/oauth2/auth" and q["scope"] == [GMAIL_SCOPE] and q["response_type"] == ["code"] and q["state"] == ["st-123"]
        assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
        assert start.pkce_verifier is not None and start.pkce_verifier.reveal() not in start.url

        async def approve(url: str) -> dict:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, allow_redirects=False) as resp:
                    assert resp.status == 302
                    return {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["Location"]).query).items()}
        params = await approve(start.url)
        assert params["state"] == "st-123"
        other = await gm.authorize(tenant_id=TENANT, holder_id="h", redirect_uri=REDIRECT, state="st-456")
        with pytest.raises(PermanentError) as e:                                   # a wrong verifier: Google answers invalid_grant
            await gm.complete_authorization(await approve(other.url), redirect_uri=REDIRECT, pkce_verifier=Secret("x" * 64))
        assert e.value.code == "oauth_invalid_grant"
        got = await gm.complete_authorization(params, redirect_uri=REDIRECT, pkce_verifier=start.pkce_verifier)
        assert got.kind == "oauth2" and got.refresh_token is not None and got.extra["scope"] == GMAIL_SCOPE and got.expires_at
        with pytest.raises(PermanentError):                                        # a code is single-use
            await gm.complete_authorization(params, redirect_uri=REDIRECT, pkce_verifier=start.pkce_verifier)
        tokens = [r for r in env.mock.requests if r.path == "/token"]
        assert tokens and all("code_verifier" not in r.form and "client_secret" not in r.form for r in tokens)     # never logged

        class AppGmail(GmailConnector):
            def __init__(self) -> None:
                super().__init__(oauth=app)
        reg = register_builtin(ConnectorRegistry())
        reg.register(AppGmail, replace=True)
        env.pipe.registry = reg
        env.con = await env.svc.add_connector("gmail", created_by=OWNER, credentials=got, config={"api_base": env.url, "auto_include": ["Customers"]})
        pin_anchor(env)
        await env.svc.discover_sources(env.cid)
        env.mock.expire_token(got.access_token.reveal())                           # an hour later: the refresh token renews it
        report = await sync(env)
        assert report.error_code is None and env.live_ids("gmail_message")
        stored = await env.pipe.context(env.connector_row()).secrets.get()
        assert stored.access_token != got.access_token and stored.refresh_token == got.refresh_token and stored.extra["scope"] == GMAIL_SCOPE
        env.mock.revoke_token(stored.access_token.reveal())                        # revoked at Google: the refresh is refused too
        bad = await env.pipe.sync(env.cid)
        assert bad.error_code == "auth_expired" and env.connector_row()["status"] == "auth_expired"
        await env.svc.disconnect(env.cid, actor=OWNER, revoke=True)
        assert env.mock.requests[-1].path == "/revoke"
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- conformance of the mock
async def test_mock_speaks_gmails_documented_shapes(tmp_path: Path) -> None:
    env = await gmail_env(tmp_path, token=None)
    try:
        async with aiohttp.ClientSession() as s:
            async def get(path: str, token: str = GM_TOKEN, **params):
                async with s.get(f"{env.url}/gmail/v1/users/me{path}", params=params, headers={"Authorization": f"Bearer {token}"}) as resp:
                    return resp.status, dict(resp.headers), await resp.json()
            st, _, profile = await get("/profile")
            assert st == 200 and profile["emailAddress"] == "ana@acme.example" and isinstance(profile["messagesTotal"], int)
            assert profile["historyId"].isdigit()
            _, _, labels = await get("/labels")
            assert {"id", "name", "type"} <= set(labels["labels"][0]) and {lb["type"] for lb in labels["labels"]} == {"system", "user"}
            _, _, empty = await get("/messages", labelIds=CUSTOMERS, q="after:2030/01/01")
            assert empty == {"resultSizeEstimate": 0}                                    # no "messages" key at all
            _, _, page1 = await get("/messages", labelIds=CUSTOMERS, maxResults="2")
            _, _, page2 = await get("/messages", labelIds=CUSTOMERS, maxResults="2", pageToken=page1["nextPageToken"])
            assert len(page1["messages"]) == 2 and {m["id"] for m in page1["messages"]}.isdisjoint(m["id"] for m in page2["messages"])
            assert set(page1["messages"][0]) == {"id", "threadId"}
            _, _, full = await get(f"/messages/{M_RENEWAL}", format="full")
            assert {"id", "threadId", "labelIds", "snippet", "historyId", "internalDate", "payload", "sizeEstimate"} <= set(full)
            payload = full["payload"]
            assert payload["partId"] == "" and payload["mimeType"] == "multipart/mixed" and {"name", "value"} == set(payload["headers"][0])
            text_part = payload["parts"][0]
            assert base64.urlsafe_b64decode(text_part["body"]["data"] + "==").decode().startswith("Hello Ana")
            att = payload["parts"][1]
            _, _, full_again = await get(f"/messages/{M_RENEWAL}", format="full")
            assert att["filename"] and att["body"]["size"] == 18432 and att["body"]["attachmentId"] != full_again["payload"]["parts"][1]["body"]["attachmentId"]
            _, _, raw = await get(f"/messages/{M_RENEWAL}", format="raw")
            assert b"Subject: Customer renewal at risk" in base64.urlsafe_b64decode(raw["raw"] + "==")
            st, _, refused = await get(f"/messages/{M_RENEWAL}", token="ya29.mock-ana-gmail-metadata", format="full")
            assert st == 403 and refused["error"]["errors"][0]["reason"] == "forbidden"
            st, _, meta = await get(f"/messages/{M_RENEWAL}", token="ya29.mock-ana-gmail-metadata", format="metadata", metadataHeaders="Subject")
            assert st == 200 and [h["name"] for h in meta["payload"]["headers"]] == ["Subject"]
            env.mock.expire_history()
            st, _, gone = await get("/history", startHistoryId=str(env.mock.history_id - 1))
            assert st == 404 and gone["error"]["code"] == 404 and gone["error"]["status"] == "NOT_FOUND" and gone["error"]["errors"][0]["reason"] == "notFound"
            env.mock.rate_limit_next(status=429, reason="rateLimitExceeded")
            st, headers, limited = await get("/profile")
            assert st == 429 and limited["error"]["status"] == "RESOURCE_EXHAUSTED" and "Retry-After" not in headers
            env.mock.rate_limit_next(status=403, reason="userRateLimitExceeded")
            st, _, limited = await get("/profile")
            assert st == 403 and limited["error"]["errors"][0] == {"message": "User Rate Limit Exceeded", "domain": "usageLimits",
                                                                   "reason": "userRateLimitExceeded"}
            st, _, unauth = await get("/profile", token="nope")
            assert st == 401 and unauth["error"]["status"] == "UNAUTHENTICATED" and unauth["error"]["errors"][0]["reason"] == "authError"
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- logs
async def test_nothing_content_bearing_is_logged(tmp_path: Path) -> None:
    sentinel = "Sentinelword6620"
    fx = load_fixture("gmail_acme")
    fx["messages"][0]["text"] += f"\n{sentinel} in the customer e-mail."
    handler = Collect()
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        env = await gmail_env(tmp_path, fixture=fx)
        try:
            await full_sync(env)
            env.mock.fail_next(1, status=503)
            await push(env, env.mock.deliver(sender="Globex Operations <ops@globex.example>", to=["Ana Lima <ana@acme.example>"],
                                             subject=f"{sentinel} subject", text=f"{sentinel} pushed", labels=("INBOX", CUSTOMERS)))
            await push(env, env.mock.delete_message(M_RENEWAL))
            env.mock.expire_history()
            await env.pipe.sync(env.cid)
            env.mock.revoke_token(GM_TOKEN)
            await env.pipe.sync(env.cid)
            metrics, outbox = json.dumps(env.pipe.db.metrics()), json.dumps(env.pipe.publisher.sent)
        finally:
            await env.close()
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    blob = "\n".join(handler.lines)
    assert handler.lines and "/gmail/{id}/users/{name}/messages" in blob          # path templates: names and ids never logged
    for needle in (sentinel, GM_TOKEN, "httpclient 4.2", "Customer renewal", "globex.example", "Bearer", "mock-push-token"):
        assert needle not in blob, needle
    assert sentinel not in metrics and sentinel not in outbox


# ---------------------------------------------------------------------------------------------- live (opt-in, never required)
@pytest.mark.live
@pytest.mark.skipif(not (os.environ.get("MYCELIC_LIVE_GMAIL_TOKEN") and os.environ.get("MYCELIC_LIVE_GMAIL_LABEL")),
                    reason="live Gmail test: set MYCELIC_LIVE_GMAIL_TOKEN (an access token with gmail.readonly) and MYCELIC_LIVE_GMAIL_LABEL")
async def test_live_gmail_read_only(tmp_path: Path) -> None:      # pragma: no cover - needs real credentials
    from mycelic.ingest.crypto import TokenVault
    store = make_store(tmp_path, "live")
    pipe = make_pipeline(store, vault=TokenVault.from_secret("live-test-master-key-0001"))
    svc = IngestService(pipe)
    try:
        con = await svc.add_connector("gmail", created_by=OWNER, credentials=google_creds(os.environ["MYCELIC_LIVE_GMAIL_TOKEN"], GMAIL_SCOPE),
                                      config={"auto_include": [os.environ["MYCELIC_LIVE_GMAIL_LABEL"]], "limits": {"page_size": 5}})
        await svc.discover_sources(con["connector_id"])
        first = await pipe.sync(con["connector_id"])
        assert first.error_code is None
        second = await pipe.sync(con["connector_id"])
        assert second.error_code is None
    finally:
        await pipe.aclose()
        await store.close()
