"""The Slack connector against the offline Slack mock, end to end through ``IngestPipeline`` into a real holder store:
connect and scopes, discovery (DMs opt-in), backfill windows and incremental sync with threads, cursor pagination, the
distributed-app limits (1 request per minute, ``limit <= 15``), 429 ``Retry-After``, ``ok:false`` auth errors as connector
status, Events API signatures (``v0``), ids-only parsing, notify-then-fetch edits and confirmed deletions, private channel
ACLs, member and token revocation events, OAuth v2 + PKCE with token rotation, mock conformance and no content in logs."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from mycelic.ingest.connectors import register_builtin
from mycelic.ingest.connectors.slack import SlackConnector, mrkdwn_to_text
from mycelic.ingest.contract import AuthExpired, AuthRevoked, InsufficientScope, PermanentError, Secret
from mycelic.ingest.mocks import load_fixture
from mycelic.ingest.oauth import OAuthAppConfig
from mycelic.ingest.registry import ConnectorRegistry, validate_manifest
from mycelic.util import fingerprint

from .connector_support import BEN, CY, DEE, SL_TOKEN, Collect, Env, creds, full_sync, slack_env
from .ingest_support import OWNER, TENANT, question, table_contains

SIGNING = b"mock-signing-secret-not-real"
PARENT = "1790674800.000100"             # "Deployment of checkout failed after the dependency upgrade to httpclient 4.2; requests time out"
REPLY = "1790675100.000200"
INCIDENT_TEXT = ("Root cause for the checkout timeouts: httpclient 4.2 changed the default connection pool size, so payment gateway calls "
                 "queue up. Keep this channel confidential until the postmortem.")
INCIDENT_Q = "What is the root cause of the checkout timeouts with httpclient 4.2 and the connection pool?"

OWNER_READ = {"principal_ids": [], "complete": True, "owner": True}          # the holder owner's own view (reads fail closed without an audience)


async def deliver(env: Env, envelope: dict, **kw):
    """The coordinator endpoint: verify the v0 signature, answer url_verification, parse ids, hand each notice to the holder."""
    headers, body = env.mock.signed_event(envelope, **kw)
    assert SlackConnector.verify_webhook(headers, body, SIGNING, now=env.clock())
    assert SlackConnector.challenge_response(headers, body) is None
    reports = [await env.svc.handle_notice(env.cid, n) for n in SlackConnector.parse_webhook(headers, body)]
    await env.pipe.process_available()
    return reports


def cited(resp: dict, text: str) -> list[dict]:
    return [r for r in resp["evidence_refs"] if r["source_root_id"] == fingerprint(text)]


# ---------------------------------------------------------------------------------------------- manifest
def test_manifest_is_registered_and_honest_about_terms_and_limits() -> None:
    m = register_builtin(ConnectorRegistry()).get("slack").manifest
    assert validate_manifest(m) == [] and m.status == "tested-offline" and m.allowed_hosts == ("slack.com",)
    assert {s.scope for s in m.scopes if s.required} == {"channels:read", "channels:history"}
    assert all(not s.required for s in m.scopes if s.scope.startswith(("im:", "groups:", "users:")))
    notes = m.terms_notes
    assert "once per minute" in notes and "15" in notes and "API Terms" in notes and "must confirm" in notes and "not live-verified" in notes
    assert m.capabilities.acl == "full" and m.capabilities.deletes == "webhook"


def test_mrkdwn_conversion() -> None:
    assert mrkdwn_to_text("<https://github.com/acme/checkout/issues/482|acme/checkout#482> cc <@U0ANA> in <#C0DEPLOY|deployments> "
                          "&lt;ok&gt; &amp; <!here>") == ("acme/checkout#482 (https://github.com/acme/checkout/issues/482) cc @U0ANA in "
                                                          "#deployments <ok> & @here")


# ---------------------------------------------------------------------------------------------- connect and discover
async def test_connect_identity_scopes_and_config(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, token=None)
    try:
        cfg = {"api_base": env.url + "/api", "slack_app_class": "internal"}
        con = await env.svc.add_connector("slack", created_by=OWNER, config=cfg, credentials=creds(SL_TOKEN))
        assert (con["source_account_id"], con["auth_account_id"], con["warnings"]) == ("/T0ACME", "U0ANA", [])
        assert {"channels:history", "groups:history"} <= set(con["granted_scopes"]) and con["account_label"] == "Acme / ana"
        assert [r.path for r in env.mock.requests] == ["/api/auth.test"]                       # identity only, no content
        await env.svc.disconnect(con["connector_id"], actor=OWNER)
        with pytest.raises(InsufficientScope) as e:
            await env.svc.add_connector("slack", created_by=OWNER, config=cfg, credentials=creds("xoxp-mock-ana-no-history"))
        assert e.value.missing == ("channels:history",)
        bot = await env.svc.add_connector("slack", created_by=OWNER, config={**cfg, "slack_app_class": "distributed"},
                                          credentials=creds("xoxb-mock-bot-token"))
        assert any("bot token" in w for w in bot["warnings"]) and any("once per minute" in w for w in bot["warnings"])
        with pytest.raises(PermanentError):
            await env.svc.add_connector("slack", created_by=OWNER, config={**cfg, "slack_app_class": "public"}, credentials=creds(SL_TOKEN))
        with pytest.raises(AuthExpired):
            await env.svc.add_connector("slack", created_by=OWNER, config=cfg, credentials=creds("xoxp-not-a-known-token"))
        env.mock.revoke_token("xoxp-mock-dee-user-token")
        with pytest.raises(AuthRevoked):
            await env.svc.add_connector("slack", created_by=OWNER, config=cfg, credentials=creds("xoxp-mock-dee-user-token"))
    finally:
        await env.close()


async def test_discovery_acls_and_dm_opt_in(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=False)
    try:
        srcs = {s.external_id: s for s in env.svc.sources(env.cid)}
        assert set(srcs) == {"C0DEPLOY", "C0RANDOM", "G0INCIDENT"}                     # no DMs, no archived channels
        assert (srcs["C0DEPLOY"].visibility, srcs["C0DEPLOY"].name) == ("public", "#deployments")
        inc = srcs["G0INCIDENT"]
        assert (inc.visibility, inc.member_ids, inc.membership_ref) == ("members", sorted([OWNER, BEN]), "slack:/T0ACME:G0INCIDENT")
        assert all(s.selection == "pending_review" for s in srcs.values())
        listed = [r for r in env.mock.requests if r.path == "/api/conversations.list"]
        assert listed and all(r.query["types"] == "public_channel,private_channel" for r in listed)
        # DMs only when the owner opts in, and even then excluded until included one by one (same account: reconnect)
        await env.svc.disconnect(env.cid, actor=OWNER)
        dms = await env.svc.add_connector("slack", created_by=OWNER, credentials=creds("xoxp-mock-ana-dm-token"),
                                          config={"api_base": env.url + "/api", "slack_app_class": "internal", "include_dms": True,
                                                  "auto_include": True, "principal_map": {"U0ANA": OWNER, "U0BEN": BEN}})
        await env.svc.discover_sources(dms["connector_id"])
        dm = next(s for s in env.svc.sources(dms["connector_id"]) if s.external_id == "D0ANABEN")
        assert (dm.source_type, dm.visibility, dm.selection, dm.selection_reason) == ("dm", "private", "excluded", "default_dm_excluded")
        assert dm.member_ids == sorted([OWNER, BEN]) and dm.exportable is False
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- pull
async def test_backfill_and_incremental_with_threads_edits_and_tombstones(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=["C0DEPLOY"], config={"limits": {"page_size": 2}})
    try:
        await env.pipe.sync(env.cid)
        await env.pipe.process_available()
        live = env.live_ids("slack_message")
        assert f"C0DEPLOY:{PARENT}" in live and "C0DEPLOY:1787220000.001100" not in live      # older than the first pass reaches
        await env.pipe.sync(env.cid, mode="backfill")
        await env.pipe.process_available()
        live = env.live_ids("slack_message")
        assert live == sorted(f"C0DEPLOY:{ts}" for ts in ("1787220000.001100", "1789473600.001000", "1790611500.000800", PARENT, REPLY,
                                                          "1790675520.000300", "1790677800.000400", "1790679600.000500", "1790755200.000900"))
        assert "C0DEPLOY:1790611200.000700" not in live and "C0DEPLOY:1790756400.001200" not in live    # tombstone parent, deleted message
        recs = {r["source_object_id"]: r for r in env.records()}
        assert len(env.records()) == len(recs)
        reply = recs[f"C0DEPLOY:{REPLY}"]
        assert reply["parent_record_id"] == f"C0DEPLOY:{PARENT}" and reply["thread_record_id"] == PARENT
        assert recs["C0DEPLOY:1790679600.000500"]["current_version"] == "1790679900.000600"        # edited.ts is the version
        assert recs["C0DEPLOY:1790677800.000400"]["author_entity_id"] == "person:slack:b0deploybot"
        assert recs["C0DEPLOY"]["kind"] == "conversation" and env.live_ids("conversation") == ["C0DEPLOY"]
        text = await env.store.document_text(reply["record_id"], audience=OWNER_READ)
        assert text.startswith("Rolling back checkout") and "(https://github.com/acme/checkout/issues/482)" in text
        tomb = env.store.store._conn.execute("SELECT reason FROM deletion_tombstones t JOIN ingest_records r ON r.record_id=t.record_id "
                                             "WHERE r.source_object_id='C0DEPLOY:1790611200.000700'").fetchone()
        assert tomb is None or tomb["reason"] == "deleted_at_source"
        history = [r for r in env.mock.requests if r.path == "/api/conversations.history"]
        assert any(r.query.get("cursor") for r in history) and all(int(r.query["limit"]) == 2 for r in history)
        assert any(r.path == "/api/conversations.replies" and r.query["ts"] == PARENT for r in env.mock.requests)
        # incremental again: nothing new is enqueued; a new message shows up on the next poll
        assert (await env.pipe.sync(env.cid)).enqueued == 0
        env.clock.advance(600)
        env.mock.post_message("C0DEPLOY", "U0CY", "checkout v2.14.2 with the httpclient pin is live.")
        assert (await env.pipe.sync(env.cid)).enqueued == 1
    finally:
        await env.close()


async def test_distributed_apps_are_paced_to_one_call_per_minute_and_15_messages(tmp_path: Path) -> None:
    fx = load_fixture("slack_acme")
    fx["app"]["app_class"] = "distributed"                         # the mock enforces Slack's 2025 limits for this app class
    env = await slack_env(tmp_path, include=["C0DEPLOY"], fixture=fx, config={"slack_app_class": "distributed", "limits": {"page_size": 100}})
    try:
        rounds = 0
        while True:
            report = await env.pipe.sync(env.cid)
            rounds += 1
            if report.error_code != "rate_limited":
                break
            assert env.connector_row()["status"] == "active"           # parked, not failed
            env.clock.advance(60)
            assert rounds < 20
        assert report.error_code is None and rounds >= 2                # limits are per method: the second thread waits a minute
        await env.pipe.process_available()
        assert f"C0DEPLOY:{REPLY}" in env.live_ids("slack_message")
        paced = [r for r in env.mock.requests if r.path in ("/api/conversations.history", "/api/conversations.replies")]
        assert all(r.status == 200 for r in paced)                      # the connector paced itself: Slack never had to refuse
        assert all(int(r.query["limit"]) <= 15 for r in paced)
        for method in ("/api/conversations.history", "/api/conversations.replies"):
            times = [r.at for r in paced if r.path == method]
            assert all(b - a >= 60 for a, b in zip(times, times[1:]))
    finally:
        await env.close()


async def test_retry_after_is_honoured_inline_or_by_parking(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=["C0DEPLOY"])
    try:
        env.mock.rate_limit_next(method="conversations.history", retry_after=1)
        ok = await env.pipe.sync(env.cid)
        assert ok.error_code is None and 1.0 in env.clock.slept
        env.clock.advance(600)
        env.mock.post_message("C0DEPLOY", "U0BEN", "Second rollout of checkout is green.")
        env.mock.rate_limit_next(method="conversations.history", retry_after=120)
        parked = await env.pipe.sync(env.cid)
        assert parked.error_code == "rate_limited" and parked.enqueued == 0
        env.clock.advance(121)
        resumed = await env.pipe.sync(env.cid)
        await env.pipe.process_available()
        assert resumed.error_code is None and resumed.enqueued == 1
        rows = env.records()
        assert len(rows) == len({r["source_object_id"] for r in rows})
    finally:
        await env.close()


async def test_auth_errors_become_connector_status(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=["C0DEPLOY"])
    try:
        env.mock.revoke_token(SL_TOKEN)                 # Slack answers HTTP 200 {"ok": false, "error": "token_revoked"}
        report = await env.pipe.sync(env.cid)
        assert report.error_code == "auth_revoked" and env.connector_row()["status"] == "revoked"
        health = await env.pipe.connector(env.cid).health(env.pipe.context(env.connector_row()))
        assert health.status == "revoked"
    finally:
        await env.close()
    env = await slack_env(tmp_path / "b", include=["C0DEPLOY"])
    try:
        env.mock.expire_token(SL_TOKEN)                 # token_expired without a refresh token: re-authorization needed
        report = await env.pipe.sync(env.cid)
        assert report.error_code == "auth_expired" and env.connector_row()["status"] == "auth_expired"
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- Events API
def test_v0_signature_verification() -> None:
    # Slack's documented example ("Verifying requests from Slack")
    body = (b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&channel_id=G8PSS9T3V&channel_name=foobar"
            b"&user_id=U2CERLKJA&user_name=roadrunner&command=%2Fwebhook-collect&text=&response_url=https%3A%2F%2Fhooks.slack.com%2Fcommands"
            b"%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c")
    headers = {"X-Slack-Request-Timestamp": "1531420618", "X-Slack-Signature": "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503"}
    secret = b"8f742231b10e8888abcd99yyyzzz85a5"
    assert SlackConnector.verify_webhook(headers, body, secret, now=1531420618 + 10)
    assert SlackConnector.verify_webhook({k.lower(): v for k, v in headers.items()}, body, secret, now=1531420618)
    assert not SlackConnector.verify_webhook(headers, body + b"x", secret, now=1531420618)            # tampered
    assert not SlackConnector.verify_webhook(headers, body, b"another-secret", now=1531420618)
    assert not SlackConnector.verify_webhook(headers, body, secret, now=1531420618 + 301)              # stale (older than 5 minutes)
    assert not SlackConnector.verify_webhook(headers, body, secret, now=1531420618 - 301)              # from the future
    assert not SlackConnector.verify_webhook({"X-Slack-Signature": headers["X-Slack-Signature"]}, body, secret, now=1531420618)
    assert not SlackConnector.verify_webhook({**headers, "X-Slack-Signature": "v1=" + headers["X-Slack-Signature"][3:]}, body, secret,
                                             now=1531420618)
    from mycelic.ingest.mocks.slack_mock import SlackMock
    mock = SlackMock(load_fixture("slack_acme"), clock=lambda: 1790856000.0)
    env_ = mock.envelope({"type": "message", "channel": "C0DEPLOY", "user": "U0ANA", "text": "hi", "ts": "1790856000.000001"})
    h, b = mock.signed_event(env_)
    assert SlackConnector.verify_webhook(h, b, SIGNING, now=1790856000)
    assert not SlackConnector.verify_webhook(*mock.signed_event(env_, tamper=True), SIGNING, now=1790856000)
    assert not SlackConnector.verify_webhook(*mock.signed_event(env_, timestamp=1790856000 - 600), SIGNING, now=1790856000)


def test_challenge_and_ids_only_parsing() -> None:
    from mycelic.ingest.mocks.slack_mock import SlackMock
    mock = SlackMock(load_fixture("slack_acme"), clock=lambda: 1790856000.0)
    h, b = mock.signed_event(mock.url_verification("3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P"))
    assert SlackConnector.verify_webhook(h, b, SIGNING, now=1790856000)
    assert SlackConnector.challenge_response(h, b) == "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P"
    assert SlackConnector.parse_webhook(h, b) == []
    assert SlackConnector.challenge_response(h, json.dumps({"type": "url_verification", "challenge": "<script>"}).encode()) is None
    sentinel = "Sentinelword5512"
    cases = [
        (mock.post_message("C0DEPLOY", "U0CY", f"{sentinel} new message"), "message", None),
        (mock.post_message("C0DEPLOY", "U0CY", f"{sentinel} reply", thread_ts=PARENT), "message", PARENT),
        (mock.post_message("C0DEPLOY", "U0CY", f"{sentinel} broadcast", thread_ts=PARENT, broadcast=True), "thread_broadcast", PARENT),
        (mock.edit_message("C0DEPLOY", REPLY, f"{sentinel} edited"), "message_changed", PARENT),
        (mock.delete_message("C0DEPLOY", REPLY), "message_deleted", PARENT),
    ]
    for envelope, action, thread in cases:
        h, b = mock.signed_event(envelope)
        assert sentinel.encode() in b or action == "message_deleted"
        [n] = SlackConnector.parse_webhook(h, b)
        assert (n.connector_type, n.action, n.external_account_id, n.source_external_id) == ("slack", action, "/T0ACME", "C0DEPLOY")
        assert n.delivery_id == envelope["event_id"]                                         # the dedupe key
        ref = n.object_refs[0]
        assert ref["type"] == "slack_message" and ref["id"] == f"C0DEPLOY:{ref['ts']}" and ref.get("thread_ts") == thread
        for needle in (sentinel, "Rolling back", "httpclient", "deployments", "Ana Lima"):
            assert needle not in repr(n), needle
    [left] = SlackConnector.parse_webhook(*mock.signed_event(mock.leave_channel("G0INCIDENT", "U0BEN")))
    assert (left.action, left.source_external_id, left.object_refs) == ("member_left_channel", "G0INCIDENT", ({"type": "user", "id": "U0BEN"},))
    [rev] = SlackConnector.parse_webhook(*mock.signed_event(mock.revoke_token(SL_TOKEN)))
    assert (rev.action, rev.source_external_id, rev.object_refs) == ("tokens_revoked", "", ({"type": "user", "id": "U0ANA"},))
    [gone] = SlackConnector.parse_webhook(*mock.signed_event(mock.uninstall()))
    assert (gone.action, gone.object_refs) == ("app_uninstalled", ())
    join = mock.envelope({"type": "message", "subtype": "channel_join", "channel": "C0DEPLOY", "user": "U0DEE", "ts": "1790856000.000009",
                          "text": "<@U0DEE> has joined the channel"})
    assert SlackConnector.parse_webhook(*mock.signed_event(join)) == []
    assert SlackConnector.parse_webhook({}, json.dumps({"type": "app_rate_limited", "team_id": "T0ACME", "minute_rate_limited": 1}).encode()) == []
    assert SlackConnector.parse_webhook({}, b"not json") == []


async def test_events_edit_reindex_and_delete_purge_after_confirmation(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=["C0DEPLOY", "C0RANDOM"])
    try:
        await full_sync(env, backfill=False)
        resp = await env.store.answer_question(question("Why did the deployment of checkout fail after the httpclient upgrade?"))
        assert resp["status"] == "answered"
        # an edited thread reply: the notice has ids only; the holder fetches the reply's current state
        [r] = await deliver(env, env.mock.edit_message("C0DEPLOY", REPLY, "Rolling back checkout to httpclient 4.1 (zebracorn build)."))
        assert r.error_code is None and r.enqueued == 1
        rid = next(x["record_id"] for x in env.records() if x["source_object_id"] == f"C0DEPLOY:{REPLY}")
        assert [h["doc_id"] for h in await env.store.search("zebracorn build", audience=OWNER_READ)] == [rid]
        assert any(x.path == "/api/conversations.replies" and x.query["ts"] == PARENT for x in env.mock.requests)
        # a redelivery of the same event_id does nothing
        envelope = env.mock.post_message("C0RANDOM", "U0BEN", "Lunch moved to 13:00 because of the deploy freeze.")
        first = await deliver(env, envelope)
        again = await deliver(env, envelope, retry_num=1)
        assert first[0].enqueued == 1 and again[0].duplicates == 1
        # deleting a message: absent while conversations.info and history answer → confirmed deletion → purge
        [d] = await deliver(env, env.mock.delete_message("C0RANDOM", "1790776800.002000"))
        assert d.error_code is None and "C0RANDOM:1790776800.002000" not in env.live_ids("slack_message")
        assert table_contains(env.store, "Anyone up for lunch") == []
        # deleting a thread parent that still has replies leaves a tombstone, which is a deletion of the parent only
        await deliver(env, env.mock.delete_message("C0DEPLOY", PARENT))
        live = env.live_ids("slack_message")
        assert f"C0DEPLOY:{PARENT}" not in live and f"C0DEPLOY:{REPLY}" in live
        assert table_contains(env.store, "requests time out") == []
        # a thread_broadcast is fetched and stored like any reply
        [b] = await deliver(env, env.mock.post_message("C0DEPLOY", "U0CY", "Broadcast: the pin is merged.", thread_ts=REPLY, broadcast=True))
        assert b.enqueued == 1
    finally:
        await env.close()


async def test_a_deletion_is_never_inferred_from_an_unreadable_channel(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=["G0INCIDENT"])
    try:
        await full_sync(env, backfill=False)
        envelope = env.mock.delete_message("G0INCIDENT", "1790679600.003100")
        env.mock.channels["G0INCIDENT"]["members"] = ["U0BEN"]          # the owner lost access at the same time
        [r] = await deliver(env, envelope)
        assert r.error_code == "source_unavailable"
        assert "G0INCIDENT:1790679600.003100" in env.live_ids("slack_message") and table_contains(env.store, "Postmortem draft due Friday")
        assert env.source("G0INCIDENT").access_state == "lost"
    finally:
        await env.close()


async def test_private_channel_records_follow_the_channel_membership(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=["G0INCIDENT", "C0DEPLOY"])
    try:
        await full_sync(env, backfill=False)
        ask = lambda aud: env.store.answer_question(question(INCIDENT_Q, audience=aud))            # noqa: E731
        assert cited(await ask({"principal_ids": [BEN], "complete": True}), INCIDENT_TEXT)        # inside the members
        for outsider in ({"principal_ids": [DEE], "complete": True}, {"principal_ids": [BEN, CY], "complete": True},
                         {"principal_ids": [BEN], "complete": False}, None):
            resp = await ask(outsider)
            assert not cited(resp, INCIDENT_TEXT) and "connection pool size" not in json.dumps(resp)
        assert cited(await ask({"owner": True}), INCIDENT_TEXT)
        public = await env.store.answer_question(question("Why did the deployment of checkout fail after the dependency upgrade?",
                                                          audience={"principal_ids": [DEE], "complete": True}))
        assert public["status"] == "answered" and public["evidence_refs"]                      # the public channel is answerable
        # another member leaves: nothing changes until the source is re-discovered, then the membership narrows at once
        [r] = await deliver(env, env.mock.leave_channel("G0INCIDENT", "U0BEN"))
        assert r.error_code is None and r.pages == 0
        await env.svc.discover_sources(env.cid)
        assert not cited(await ask({"principal_ids": [BEN], "complete": True}), INCIDENT_TEXT)
        # the owner leaves: access lost, so the records are withheld from everyone but the owner
        [r] = await deliver(env, env.mock.leave_channel("G0INCIDENT", "U0ANA"))
        assert r.error_code == "source_unavailable" and env.source("G0INCIDENT").access_state == "lost"
        assert cited(await ask({"owner": True}), INCIDENT_TEXT)
    finally:
        await env.close()


async def test_token_revocation_and_uninstall_events_revoke_the_connection(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, include=["C0DEPLOY"])
    try:
        other = env.mock.envelope({"type": "tokens_revoked", "tokens": {"oauth": ["U0DEE"], "bot": []}, "event_ts": "1790856000.000001"})
        [r] = await deliver(env, other)
        assert r.error_code is None and env.connector_row()["status"] == "active"           # someone else's token
        [r] = await deliver(env, env.mock.revoke_token(SL_TOKEN))
        assert r.error_code == "auth_revoked" and env.connector_row()["status"] == "revoked"
        env.store.store._conn.execute("UPDATE connectors SET status='active' WHERE connector_id=?", (env.cid,))
        [r] = await deliver(env, env.mock.uninstall())
        assert r.error_code == "auth_revoked" and env.connector_row()["status"] == "revoked"
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- OAuth v2
async def test_oauth_v2_user_token_with_pkce_and_rotation(tmp_path: Path) -> None:
    fx = load_fixture("slack_acme")
    fx["app"]["token_rotation"] = True
    env = await slack_env(tmp_path, token=None, fixture=fx)
    try:
        app = OAuthAppConfig(client_id=fx["app"]["client_id"], client_secret=Secret(fx["app"]["client_secret"]), oauth_base=env.url,
                             api_base=env.url + "/api", allow_loopback_http=True)
        sl = SlackConnector(oauth=app)
        start = await sl.authorize(tenant_id=TENANT, holder_id="h", redirect_uri="https://mycelic.example/oauth/slack", state="st")
        q = parse_qs(urlsplit(start.url).query)
        assert q["user_scope"] == ["channels:read,channels:history,groups:read,groups:history"] and q["code_challenge_method"] == ["S256"]
        assert "im:history" not in start.url                                                   # no DM scopes unless asked for
        async with aiohttp.ClientSession() as s:
            async with s.get(start.url, allow_redirects=False) as resp:
                params = {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["Location"]).query).items()}
        got = await sl.complete_authorization(params, redirect_uri="https://mycelic.example/oauth/slack", pkce_verifier=start.pkce_verifier)
        assert got.kind == "oauth2" and got.refresh_token is not None and got.extra["team_id"] == "T0ACME" and got.extra["user_id"] == "U0ANA"
        with pytest.raises(PermanentError):                                                    # a code is single-use
            await sl.complete_authorization(params, redirect_uri="https://mycelic.example/oauth/slack", pkce_verifier=start.pkce_verifier)
        reg = register_builtin(ConnectorRegistry())

        class AppSlack(SlackConnector):
            def __init__(self) -> None:
                super().__init__(oauth=app)
        reg.register(AppSlack, replace=True)
        env.pipe.registry = reg
        env.con = await env.svc.add_connector("slack", created_by=OWNER, credentials=got,
                                              config={"api_base": env.url + "/api", "slack_app_class": "internal", "auto_include": ["#deployments"]})
        await env.svc.discover_sources(env.cid)
        env.mock.expire_token(got.access_token.reveal())                   # rotation: the token expires, the refresh token renews it
        report = await env.pipe.sync(env.cid)
        await env.pipe.process_available()
        assert report.error_code is None and env.live_ids("slack_message")
        stored = await env.pipe.context(env.connector_row()).secrets.get()
        assert stored.access_token != got.access_token and stored.extra["team_id"] == "T0ACME"
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- conformance
# the keys (and types) the connector reads from each Slack object; the mock must carry them as Slack documents them
SLACK_SHAPES = {
    "auth.test": {"ok": bool, "team_id": str, "user_id": str, "team": str, "user": str, "url": str},
    "channel": {"id": str, "name": str, "is_channel": bool, "is_group": bool, "is_im": bool, "is_mpim": bool, "is_private": bool, "created": int,
                "is_archived": bool, "is_member": bool},
    "im": {"id": str, "is_im": bool, "user": str, "created": int},
    "message": {"type": str, "ts": str, "text": str},
    "page": {"ok": bool, "has_more": bool},
}


def _shape(obj: dict, shape: dict, label: str) -> None:
    for k, t in shape.items():
        assert k in obj, f"{label}: missing {k}"
        assert isinstance(obj[k], t) and not (t is int and isinstance(obj[k], bool)), f"{label}: {k} is {type(obj[k]).__name__}"


async def test_mock_responses_carry_what_the_connector_relies_on(tmp_path: Path) -> None:
    env = await slack_env(tmp_path, token=None)
    try:
        h = {"Authorization": "Bearer xoxp-mock-ana-dm-token"}
        async with aiohttp.ClientSession() as s:
            async def call(method, **params):
                async with s.get(f"{env.url}/api/{method}", params=params, headers=h) as resp:
                    return resp.status, dict(resp.headers), await resp.json()
            status, headers, me = await call("auth.test")
            _shape(me, SLACK_SHAPES["auth.test"], "auth.test")
            assert "x-oauth-scopes" in {k.lower() for k in headers}
            _, _, listed = await call("conversations.list", types="public_channel,private_channel,im", limit="2")
            assert listed["response_metadata"]["next_cursor"]                                       # cursor pagination
            _, _, everything = await call("conversations.list", types="public_channel,private_channel,im")
            for ch in everything["channels"]:
                _shape(ch, SLACK_SHAPES["im" if ch.get("is_im") else "channel"], f"channel {ch['id']}")
            _, _, hist = await call("conversations.history", channel="C0DEPLOY", limit="3")
            _shape(hist, SLACK_SHAPES["page"], "history")
            assert [float(m["ts"]) for m in hist["messages"]] == sorted((float(m["ts"]) for m in hist["messages"]), reverse=True)   # newest first
            for m in hist["messages"]:
                _shape(m, SLACK_SHAPES["message"], "history message")
            _, _, full = await call("conversations.history", channel="C0DEPLOY")
            parent = next(m for m in full["messages"] if m["ts"] == PARENT)
            assert parent["thread_ts"] == PARENT and parent["reply_count"] == 2 and isinstance(parent["latest_reply"], str)
            assert any(m.get("subtype") == "tombstone" for m in full["messages"])
            assert next(m for m in full["messages"] if m["ts"] == "1790679600.000500")["edited"]["ts"] == "1790679900.000600"
            _, _, replies = await call("conversations.replies", channel="C0DEPLOY", ts=PARENT)
            assert replies["messages"][0]["ts"] == PARENT and [m["ts"] for m in replies["messages"][1:]] == [REPLY, "1790675520.000300"]
            # errors are HTTP 200 with ok:false (rate limits are the exception: HTTP 429 with Retry-After)
            for method, params, error in (("conversations.history", {"channel": "C0NOPE"}, "channel_not_found"),
                                          ("conversations.history", {"channel": "C0DEPLOY", "cursor": "garbage"}, "invalid_cursor"),
                                          ("conversations.replies", {"channel": "C0DEPLOY", "ts": "1.000001"}, "thread_not_found")):
                st, _, data = await call(method, **params)
                assert (st, data["ok"], data["error"]) == (200, False, error)
            async with s.get(f"{env.url}/api/auth.test", headers={"Authorization": "Bearer nope"}) as resp:
                assert resp.status == 200 and (await resp.json())["error"] == "invalid_auth"
            env.mock.rate_limit_next(retry_after=7)
            st, headers, data = await call("auth.test")
            assert st == 429 and headers.get("Retry-After") == "7" and data["error"] == "ratelimited"
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- logs
async def test_nothing_content_bearing_is_logged(tmp_path: Path) -> None:
    sentinel = "Sentinelword7316"
    fx = load_fixture("slack_acme")
    fx["messages"][0]["text"] += f" {sentinel}"
    handler = Collect()
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        env = await slack_env(tmp_path, include=True, fixture=fx)
        try:
            await full_sync(env)
            env.mock.fail_next(1, status=503)
            await deliver(env, env.mock.edit_message("C0DEPLOY", PARENT, f"{sentinel} edited"))
            await deliver(env, env.mock.delete_message("C0DEPLOY", PARENT))
            env.mock.revoke_token(SL_TOKEN)
            await env.pipe.sync(env.cid)
            metrics, outbox = json.dumps(env.pipe.db.metrics()), json.dumps(env.pipe.publisher.sent)
        finally:
            await env.close()
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    blob = "\n".join(handler.lines)
    assert handler.lines and "/api/conversations.history" in blob
    for needle in (sentinel, SL_TOKEN, "httpclient 4.2", "incident-checkout", "deployments", "Bearer"):
        assert needle not in blob, needle
    assert sentinel not in metrics and sentinel not in outbox


# ---------------------------------------------------------------------------------------------- live (opt-in, never required)
@pytest.mark.live
@pytest.mark.skipif(not (os.environ.get("MYCELIC_LIVE_SLACK_TOKEN") and os.environ.get("MYCELIC_LIVE_SLACK_CHANNEL")),
                    reason="live Slack test: set MYCELIC_LIVE_SLACK_TOKEN (user token) and MYCELIC_LIVE_SLACK_CHANNEL (channel id) to run")
async def test_live_slack_read_only(tmp_path: Path) -> None:      # pragma: no cover - needs real credentials
    from mycelic.ingest.crypto import TokenVault
    from mycelic.ingest.service import IngestService

    from .ingest_support import make_pipeline, make_store
    store = make_store(tmp_path, "live")
    pipe = make_pipeline(store, vault=TokenVault.from_secret("live-test-master-key-0001"))
    svc = IngestService(pipe)
    try:
        con = await svc.add_connector("slack", created_by=OWNER, credentials=creds(os.environ["MYCELIC_LIVE_SLACK_TOKEN"]),
                                      config={"slack_app_class": os.environ.get("MYCELIC_LIVE_SLACK_APP_CLASS", "distributed"),
                                              "auto_include": [os.environ["MYCELIC_LIVE_SLACK_CHANNEL"]], "limits": {"page_size": 15}})
        await svc.discover_sources(con["connector_id"])
        report = await pipe.sync(con["connector_id"])
        assert report.error_code in (None, "rate_limited")
    finally:
        await pipe.aclose()
        await store.close()
