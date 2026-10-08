"""The GitHub connector against the offline GitHub mock, end to end through ``IngestPipeline`` into a real holder store:
connect and scopes, discovery and ACLs, backfill windows and incremental sync, Link pagination, ETag 304s, rate-limit
parking, crash recovery (acceptance test 13), signed webhooks (verify, ids-only parsing, notify-then-fetch edits,
confirmed deletions, transfers), revocation, OAuth code + PKCE, conformance of the mock to GitHub's OpenAPI description,
and no content in logs. Everything is offline; ``@pytest.mark.live`` tests run only with real credentials in env vars."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from mycelic.ingest.connectors import register_builtin
from mycelic.ingest.connectors.github import GitHubConnector
from mycelic.ingest.contract import (AuthExpired, ConnectorContext, ConnectorLimits, InsufficientScope, PermanentError, RawItem, Secret,
                                     connector_logger)
from mycelic.ingest.events import record_id_for, record_key
from mycelic.ingest.mocks import FakeClock, load_fixture
from mycelic.ingest.mocks.common import FIXTURES
from mycelic.ingest.oauth import OAuthAppConfig
from mycelic.ingest.queue import StaleCheckpoint
from mycelic.ingest.registry import ConnectorRegistry, validate_manifest

from .connector_support import BEN, GH_TOKEN, Collect, Env, creds, full_sync, github_env
from .ingest_support import OWNER, TENANT, question, table_contains

CHECKOUT, PLATFORM = "5001", "5002"


def gh_request_paths(env: Env) -> list[str]:
    return [r.path for r in env.mock.requests]


async def deliver(env: Env, event: str, payload: dict, *, delivery_id: str | None = None):
    """What the coordinator endpoint does with a delivery: verify, parse (ids only), then the holder handles each notice."""
    headers, body = env.mock.signed_delivery(event, payload, delivery_id=delivery_id)
    secret = env.fixture["app"]["webhook_secret"].encode()
    assert GitHubConnector.verify_webhook(headers, body, secret, now=env.clock())
    reports = []
    for notice in GitHubConnector.parse_webhook(headers, body):
        reports.append(await env.svc.handle_notice(env.cid, notice))
    await env.pipe.process_available()
    return reports


# ---------------------------------------------------------------------------------------------- manifest
def test_manifest_is_registered_and_states_its_limits_honestly() -> None:
    reg = register_builtin(ConnectorRegistry())
    m = reg.get("github").manifest
    assert validate_manifest(m) == [] and m.status == "tested-offline" and m.allowed_hosts == ("api.github.com", "github.com")
    required = {s.scope for s in m.scopes if s.required}
    assert required == {"Issues: Read-only (fine-grained)", "Metadata: Read-only (fine-grained)"}
    optional = {s.scope: s.reason for s in m.scopes if not s.required}
    assert "write access" in optional["repo (classic tokens only)"] and "no scope" in optional["public_repo (classic tokens only)"]
    assert m.rate_limit.serial_per_token and "scaffold" in m.terms_notes and "not live-verified" in m.terms_notes
    assert m.capabilities.deletes == "webhook" and m.modes == frozenset({"pull", "webhook"})


# ---------------------------------------------------------------------------------------------- connect
async def test_connect_reports_identity_scopes_and_warnings(tmp_path: Path) -> None:
    env = await github_env(tmp_path, token=None)
    try:
        cfg = {"api_base": env.url}
        fine = await env.svc.add_connector("github", created_by=OWNER, config=cfg, credentials=creds(GH_TOKEN))
        assert (fine["source_account_id"], fine["auth_account_id"], fine["account_label"]) == ("127.0.0.1", "1001", "ana")
        assert fine["granted_scopes"] == [] and fine["warnings"] == [] and fine["status"] == "active"
        assert gh_request_paths(env) == ["/user"]                                  # identity only, no content
        await env.svc.disconnect(fine["connector_id"], actor=OWNER)
        classic = await env.svc.add_connector("github", created_by=OWNER, config=cfg, credentials=creds("gh-mock-ana-classic-repo"))
        assert classic["granted_scopes"] == ["read:org", "repo"]
        assert any("write access" in w for w in classic["warnings"])
        await env.svc.disconnect(classic["connector_id"], actor=OWNER)
        with pytest.raises(InsufficientScope) as e:          # private repositories wanted, token cannot read them
            await env.svc.add_connector("github", created_by=OWNER, config={**cfg, "include_private": True},
                                        credentials=creds("gh-mock-ana-classic-noscope"))
        assert e.value.missing == ("repo",)
        env.mock.revoke_token("gh-mock-ben-classic-repo")
        with pytest.raises(AuthExpired):
            await env.svc.add_connector("github", created_by=OWNER, config=cfg, credentials=creds("gh-mock-ben-classic-repo"))
        with pytest.raises(PermanentError):
            await env.svc.add_connector("github", created_by=OWNER, config={**cfg, "repos": ["not a repo"]}, credentials=creds(GH_TOKEN))
        live = env.store.store._conn.execute("SELECT COUNT(*) FROM connectors WHERE status='active'").fetchone()[0]
        assert live == 0                                                          # failed connects leave nothing behind
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- discovery and ACLs
async def test_discovery_maps_repository_visibility_to_source_acls(tmp_path: Path) -> None:
    env = await github_env(tmp_path, token="gh-mock-ana-classic-repo", include=False,
                           config={"discover_page_size": 1, "acl_members": {"acme/checkout": ["1001", "1002", "4242"]}})
    try:
        srcs = {s.name: s for s in env.svc.sources(env.cid)}
        assert set(srcs) == {"acme/checkout", "acme/platform", "ana/notes"}
        checkout, notes = srcs["acme/checkout"], srcs["ana/notes"]
        assert (checkout.visibility, checkout.membership_ref) == ("members", "github:127.0.0.1:repo:5001")
        assert checkout.member_ids == sorted([OWNER, BEN, "github:4242"])          # unmapped ids stay namespaced
        assert srcs["acme/platform"].visibility == "members" and srcs["acme/platform"].member_ids == []
        assert notes.visibility == "private" and notes.exportable is False         # a user-owned private repo
        assert all(s.selection == "pending_review" for s in srcs.values())          # nothing is ingested silently
        assert checkout.metadata["full_name"] == "acme/checkout" and checkout.metadata["owner_type"] == "Organization"
        members = [r[0] for r in env.store.store._conn.execute("SELECT member_id FROM acl_memberships WHERE membership_ref=?",
                                                                 ("github:127.0.0.1:repo:5001",))]
        assert sorted(members) == sorted([OWNER, BEN, "github:4242"])
        pages = [r.query.get("page", "1") for r in env.mock.requests if r.path in ("/user/repos", "/user/repos/")]
        assert len(pages) == 3 and "2" in pages                                     # discovery followed Link pages
    finally:
        await env.close()


async def test_org_and_installation_discovery(tmp_path: Path) -> None:
    env = await github_env(tmp_path, token="gh-mock-ana-classic-repo", include=False, config={"org": "acme"})
    try:
        names = {s.name: s.visibility for s in env.svc.sources(env.cid)}
        assert names == {"acme/checkout": "members", "acme/platform": "members", "acme/website": "public"}
        inst = await env.svc.add_connector("github", created_by=OWNER, config={"api_base": env.url, "auth_mode": "installation",
                                                                               "installation_id": "77001"},
                                           credentials=creds("gh-mock-acme-installation"))
        assert inst["auth_account_id"] == "installation:77001" and any("scaffold" in w for w in inst["warnings"])
        await env.svc.discover_sources(inst["connector_id"])
        assert {s.name for s in env.svc.sources(inst["connector_id"])} == {"acme/checkout", "acme/website"}
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- pull
async def test_backfill_windows_then_incremental_into_the_holder(tmp_path: Path) -> None:
    env = await github_env(tmp_path, config={"limits": {"page_size": 2}})
    try:
        # the first incremental pass reaches back one window (30 days): June's issue #470 is older
        await env.pipe.sync(env.cid)
        await env.pipe.process_available()
        assert f"{CHECKOUT}#470" not in env.live_ids("issue") and f"{CHECKOUT}#482" in env.live_ids("issue")
        # backfill windows (newest first) bring the history; nothing is duplicated
        report = await env.pipe.sync(env.cid, mode="backfill")
        assert report.error_code is None
        for repo in (CHECKOUT, PLATFORM):                                            # windows run newest first, per source
            starts = [s.split(":", 2)[2] for s in report.streams if s.startswith(f"backfill:{repo}:")]
            assert len(starts) > 1 and starts == sorted(starts, reverse=True)
        await env.pipe.process_available()
        assert env.live_ids("issue") == sorted([f"{CHECKOUT}#470", f"{CHECKOUT}#482", f"{CHECKOUT}#484", f"{PLATFORM}#12"])
        assert env.live_ids("pull_request") == [f"{CHECKOUT}#483"]
        assert env.live_ids("issue_comment") == ["880101", "880102", "880104", "880105"]      # 880103 was deleted at GitHub
        assert len(env.live_ids("conversation")) == 5 and f"{CHECKOUT}#475" not in env.live_ids()   # #475 was transferred away
        recs = {(r["source_object_type"], r["source_object_id"]): r for r in env.records()}
        assert len(env.records()) == len(set(recs))                                  # exactly once each
        thread = record_id_for(record_key(TENANT, env.store.holder_id, "github", "127.0.0.1", "conversation", f"{CHECKOUT}#482"))
        assert recs[("issue_comment", "880101")]["conversation_record_id"] == thread
        assert recs[("issue_comment", "880101")]["parent_record_id"] == f"{CHECKOUT}#482"
        assert recs[("issue", f"{CHECKOUT}#482")]["current_version"].startswith("2026-09-30T14:10:00")     # source_version = updated_at
        assert recs[("issue", f"{CHECKOUT}#482")]["visibility"] == "members"
        rid = recs[("issue", f"{CHECKOUT}#482")]["record_id"]
        doc, text = await env.store.document(rid), await env.store.document_text(rid)
        assert doc["observed_at"].startswith("2026-09-30T14:10") and "payment gateway" in text and "triage: p1" not in text   # HTML comments stripped
        # Link pagination: later pages were requested through the absolute /repositories/{id} URLs GitHub returns
        assert any(r.path == f"/repositories/{CHECKOUT}/issues" and r.query.get("page") == "2" for r in env.mock.requests)
        listing = [r for r in env.mock.requests if r.path == "/repos/acme/checkout/issues"]
        assert listing and all(r.query["state"] == "all" and r.query["sort"] == "updated" and r.query["direction"] == "asc" for r in listing)
        assert all(int(r.query.get("per_page", "2")) == 2 for r in listing)
    finally:
        await env.close()


async def test_incremental_sees_changes_and_unchanged_repositories_cost_304s(tmp_path: Path) -> None:
    env = await github_env(tmp_path, include=["acme/checkout"])
    try:
        await full_sync(env, backfill=False)
        await env.pipe.sync(env.cid)                    # the watermark moved: the next pass has a new `since`
        n = len(env.mock.requests)
        again = await env.pipe.sync(env.cid)
        quiet = env.mock.requests[n:]
        assert again.enqueued == 0 and [r.status for r in quiet] == [304, 304] and all(r.if_none_match for r in quiet)
        env.clock.advance(3600)
        env.mock.add_comment("acme/checkout", 482, body="The upstream fix ships in httpclient 4.2.1 next week.", user="ben")
        env.mock.edit_issue("acme/checkout", 484, labels=["test", "flaky"])
        report = await env.pipe.sync(env.cid)
        out = await env.pipe.process_available()
        assert report.enqueued >= 2 and out.outcomes["new"] == 1 and out.outcomes["metadata_only"] == 1
        assert [h for h in await env.store.search("httpclient 4.2.1 next week") if h["doc_id"]]
    finally:
        await env.close()


async def test_rate_limits_park_the_stream_and_resume_without_loss(tmp_path: Path) -> None:
    env = await github_env(tmp_path, include=["acme/checkout"])
    try:
        env.mock.exhaust_primary(GH_TOKEN, reset_in=600)
        parked = await env.pipe.sync(env.cid)
        assert parked.error_code == "rate_limited" and parked.enqueued == 0
        con = env.connector_row()
        assert con["status"] == "active" and con["status_code"] == "rate_limited"           # parked, not broken
        row = env.store.store._conn.execute("SELECT status, last_error_code FROM connector_checkpoints WHERE stream=?",
                                            (f"incr:{CHECKOUT}",)).fetchone()
        assert row is None or row["status"] == "paused"
        n = len(env.mock.requests)
        assert (await env.pipe.sync(env.cid)).error_code == "rate_limited" and len(env.mock.requests) == n   # no request before reset
        env.clock.advance(601)
        resumed = await env.pipe.sync(env.cid)
        await env.pipe.process_available()
        assert resumed.error_code is None and env.connector_row()["status_code"] == ""
        assert f"{CHECKOUT}#482" in env.live_ids("issue")
        # a secondary limit with a short Retry-After is waited out inline (on the fake clock) and the sync completes
        env.mock.secondary_limit(after=0, retry_after=1)
        env.clock.advance(3600)
        env.mock.add_comment("acme/checkout", 484, body="Seen again on the nightly run.", user="dee")
        before = list(env.clock.slept)
        ok = await env.pipe.sync(env.cid)
        await env.pipe.process_available()
        assert ok.error_code is None and 1.0 in env.clock.slept[len(before):]
        assert len(env.live_ids("issue_comment")) == 4
    finally:
        await env.close()


class CrashingGitHub(GitHubConnector):
    crash_after: int | None = None

    async def initial_backfill(self, ctx, source, window, cursor):
        n = 0
        async for page in super().initial_backfill(ctx, source, window, cursor):
            if type(self).crash_after is not None and n >= type(self).crash_after:
                raise RuntimeError("simulated crash in the middle of a backfill")
            n += 1
            yield page


async def test_crash_mid_backfill_resumes_without_duplicates_or_loss(tmp_path: Path) -> None:
    """Acceptance test 13 with a real provider connector: a process crash inside a paged backfill window, then resume."""
    reg = register_builtin(ConnectorRegistry())
    reg.register(CrashingGitHub, replace=True)
    env = await github_env(tmp_path, include=["acme/checkout"], registry=reg,
                           config={"limits": {"page_size": 1, "backfill_days": 200, "backfill_window_days": 200}})
    try:
        CrashingGitHub.crash_after = 3
        first = await env.pipe.sync(env.cid, mode="backfill")
        assert first.error_code == "connector_crash" and first.pages == 3
        [stream] = first.streams
        _cur, v1 = await env.pipe.queue.load_checkpoint(env.cid, stream)
        assert v1 == 3
        await env.pipe.process_available()
        assert len(env.live_ids("issue") + env.live_ids("pull_request")) == 3        # what was committed is processed, nothing partial
        with pytest.raises(StaleCheckpoint):                                         # a zombie runner holding an old version is fenced
            await env.pipe.queue.commit_page(env.cid, stream, [], None, priority_class="backfill", expected_version=2)
        CrashingGitHub.crash_after = None
        second = await env.pipe.sync(env.cid, mode="backfill")
        assert second.error_code is None
        _cur, v2 = await env.pipe.queue.load_checkpoint(env.cid, stream)
        assert v2 > v1
        await env.pipe.process_available()
        assert env.live_ids("issue") == sorted([f"{CHECKOUT}#470", f"{CHECKOUT}#482", f"{CHECKOUT}#484"])
        assert env.live_ids("issue_comment") == ["880101", "880102", "880104", "880105"]
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})
        fetched = Counter((r.path.replace("/repos/acme/checkout", f"/repositories/{CHECKOUT}"), r.query.get("page", "1"))
                          for r in env.mock.requests if "/issues" in r.path)
        assert max(fetched.values()) <= 2                                            # no page fetched more than twice
        # a provider-side crash (connections dropped) mid-window: the stream errors, the cursor holds, the resume completes
        env.mock.add_issue("acme/checkout", title="Checkout latency dashboard", body="Add p99 latency per gateway.", user="cy",
                           at="2026-09-10T09:00:00Z")
        env.store.store._conn.execute("DELETE FROM connector_checkpoints WHERE stream=?", (stream,))
        env.mock.crash_after_pages(2)
        broken = await env.pipe.sync(env.cid, mode="backfill")
        assert broken.error_code == "transient"
        env.mock.recover()
        healed = await env.pipe.sync(env.cid, mode="backfill")
        await env.pipe.process_available()
        assert healed.error_code is None and len(env.live_ids("issue")) == 4
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})
    finally:
        CrashingGitHub.crash_after = None
        await env.close()


# ---------------------------------------------------------------------------------------------- webhooks
def test_webhook_signatures() -> None:
    # GitHub's documented test vector ("Validating webhook deliveries")
    vector = {"X-Hub-Signature-256": "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"}
    assert GitHubConnector.verify_webhook(vector, b"Hello, World!", b"It's a Secret to Everybody", now=0)
    assert not GitHubConnector.verify_webhook(vector, b"Hello, World?", b"It's a Secret to Everybody", now=0)
    from mycelic.ingest.mocks.github_mock import GitHubMock
    mock = GitHubMock(load_fixture("github_acme"))
    mock.url = "https://api.github.com"
    payload = mock.webhook_payload("issue_comment", "edited", repo="acme/checkout", number=482, comment_id=880101)
    secret = b"mock-webhook-secret-not-real"
    headers, body = mock.signed_delivery("issue_comment", payload)
    assert GitHubConnector.verify_webhook(headers, body, secret, now=0)
    assert GitHubConnector.verify_webhook({k.lower(): v for k, v in headers.items()}, body, secret, now=0)     # header case is irrelevant
    t_headers, t_body = mock.signed_delivery("issue_comment", payload, tamper=True)
    assert not GitHubConnector.verify_webhook(t_headers, t_body, secret, now=0)
    assert not GitHubConnector.verify_webhook(headers, body, b"another secret", now=0)
    assert not GitHubConnector.verify_webhook({k: v for k, v in headers.items() if k != "X-Hub-Signature-256"}, body, secret, now=0)
    sha1_only = {k: v for k, v in headers.items() if k != "X-Hub-Signature-256"}
    assert "X-Hub-Signature" in sha1_only and not GitHubConnector.verify_webhook(sha1_only, body, secret, now=0)   # SHA-1 never accepted
    assert not GitHubConnector.verify_webhook(headers, body, b"", now=0)


def test_parse_webhook_keeps_ids_only() -> None:
    from mycelic.ingest.mocks.github_mock import GitHubMock
    mock = GitHubMock(load_fixture("github_acme"))
    mock.url = "https://api.github.com"
    sentinel = "Sentinelword4471"
    payloads = [
        ("issue_comment", mock.edit_comment(880101, f"{sentinel} edited comment text"), [("issue_comment", "880101")]),
        ("issues", mock.edit_issue("acme/checkout", 482, title=f"{sentinel} title", body=f"{sentinel} body"), [("issue", f"{CHECKOUT}#482")]),
        ("issue_comment", mock.delete_comment(880102), [("issue_comment", "880102")]),
        ("issues", mock.delete_issue("acme/checkout", 484), [("issue", f"{CHECKOUT}#484")]),
    ]
    for event, payload, expected in payloads:
        headers, body = mock.signed_delivery(event, payload, delivery_id="d-123")
        assert sentinel.encode() in body or event == "issue_comment" or "deleted" in payload["action"]
        [notice] = GitHubConnector.parse_webhook(headers, body)
        assert notice.delivery_id == "d-123" and notice.source_external_id == CHECKOUT and notice.external_account_id == "9001"
        assert [(r["type"], r["id"]) for r in notice.object_refs] == expected
        text = repr(notice)
        for needle in (sentinel, "httpclient", "acme/checkout", "ana", "Ana Lima", "checkout"):
            assert needle not in text, needle
    transfer = mock.transfer_issue("acme/checkout", 482, "acme/platform")
    headers, body = mock.signed_delivery("issues", transfer, delivery_id="d-9")
    old, new = GitHubConnector.parse_webhook(headers, body)
    assert (old.delivery_id, old.source_external_id, old.action) == ("d-9", CHECKOUT, "issues.transferred")
    assert (new.delivery_id, new.source_external_id, new.object_refs[0]["id"]) == ("d-9:new", PLATFORM, f"{PLATFORM}#13")
    assert GitHubConnector.parse_webhook({"X-GitHub-Event": "ping", "X-GitHub-Delivery": "p"}, b'{"zen": "x"}') == []
    revoked = json.dumps({"action": "revoked", "sender": {"id": 1001, "login": "ana"}}).encode()
    [rev] = GitHubConnector.parse_webhook({"X-GitHub-Event": "github_app_authorization", "X-GitHub-Delivery": "r"}, revoked)
    assert rev.source_external_id == "" and rev.object_refs == ({"type": "user", "id": "1001"},) and "ana" not in repr(rev)
    assert GitHubConnector.parse_webhook({"X-GitHub-Event": "issues", "X-GitHub-Delivery": "x"}, b"not json") == []


async def test_webhook_edits_reindex_and_deletions_purge(tmp_path: Path) -> None:
    env = await github_env(tmp_path, include=["acme/checkout"])
    try:
        await full_sync(env, backfill=False)
        resp = await env.store.answer_question(question("Does pinning httpclient to 4.1 make the checkout timeouts go away?",
                                                        audience={"owner": True}))
        assert resp["status"] == "answered"
        # an edited comment: the notice carries ids, the holder fetches the new text with the owner's token
        env.clock.advance(60)
        payload = env.mock.edit_comment(880102, "Confirmed with zebracorn tracing: pinning httpclient to 4.1 removes the timeouts.")
        [report] = await deliver(env, "issue_comment", payload, delivery_id="dlv-edit")
        assert report.error_code is None and report.enqueued == 1
        rid = next(r["record_id"] for r in env.records() if r["source_object_id"] == "880102")
        assert [h["doc_id"] for h in await env.store.search("zebracorn tracing")] == [rid]
        revised = [e for e in env.pipe.publisher.of("evidence_event") if e["event"] == "revised"]
        assert len(revised) == 1 and "zebracorn" not in json.dumps(revised)
        # the same delivery again (a provider retry) is a no-op
        [dup] = await deliver(env, "issue_comment", payload, delivery_id="dlv-edit")
        assert dup.duplicates == 1 and dup.pages == 0
        # a deleted comment: 404 while the repository answers 200 → confirmed deletion → purge
        assert table_contains(env.store, "Linking the Slack discussion")
        [gone] = await deliver(env, "issue_comment", env.mock.delete_comment(880101))
        assert gone.error_code is None
        assert table_contains(env.store, "Linking the Slack discussion") == []
        assert "880101" not in env.live_ids("issue_comment")
        tomb = env.store.store._conn.execute("SELECT reason FROM deletion_tombstones t JOIN ingest_records r ON r.record_id=t.record_id "
                                             "WHERE r.source_object_id='880101'").fetchone()
        assert tomb["reason"] == "deleted_at_source"
        # a deleted issue (410): the issue message and its conversation are tombstoned
        await deliver(env, "issues", env.mock.delete_issue("acme/checkout", 484))
        assert f"{CHECKOUT}#484" not in env.live_ids("issue") and f"{CHECKOUT}#484" not in env.live_ids("conversation")
        assert table_contains(env.store, "fails about one run in ten") == []
    finally:
        await env.close()


async def test_deletion_is_never_inferred_from_an_unreadable_repository(tmp_path: Path) -> None:
    env = await github_env(tmp_path, include=["acme/checkout"])
    try:
        await full_sync(env, backfill=False)
        payload = env.mock.delete_comment(880104)
        env.mock.hide_repo("acme/checkout")               # access lost at the same time: 404 means nothing about the comment
        [report] = await deliver(env, "issue_comment", payload)
        assert report.error_code == "source_unavailable"
        assert "880104" in env.live_ids("issue_comment") and table_contains(env.store, "passes locally")
        assert env.source(CHECKOUT).access_state == "lost"
        # records of a source whose access was lost are withheld from everyone but the owner
        q = "Does the flaky Safari checkout UI test pass locally?"
        assert not [r for r in (await env.store.answer_question(question(q, audience={"principal_ids": [BEN], "complete": True})))["evidence_refs"]]
        assert (await env.store.answer_question(question(q, audience={"owner": True})))["evidence_refs"]
    finally:
        await env.close()


async def test_a_transferred_issue_is_tombstoned_and_reappears_in_its_new_repository(tmp_path: Path) -> None:
    env = await github_env(tmp_path)
    try:
        await full_sync(env, backfill=False)
        old_root = next(r["source_root_id"] for r in env.records() if r["source_object_id"] == f"{CHECKOUT}#484" and r["kind"] == "message")
        env.clock.advance(60)
        reports = await deliver(env, "issues", env.mock.transfer_issue("acme/checkout", 484, "acme/platform"))
        assert [r.error_code for r in reports] == [None, None]
        assert f"{CHECKOUT}#484" not in env.live_ids("issue")
        reason = env.store.store._conn.execute("SELECT t.reason FROM deletion_tombstones t JOIN ingest_records r ON r.record_id=t.record_id "
                                               "WHERE r.source_object_id=? AND r.source_object_type='issue'", (f"{CHECKOUT}#484",)).fetchone()[0]
        assert reason == "moved"
        new = next(r for r in env.records() if r["source_object_id"] == f"{PLATFORM}#13" and r["kind"] == "message")
        assert new["deletion_status"] == "live" and new["source_root_id"] == old_root          # same text, same root: no inflated support
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- revocation
async def test_revoked_tokens_surface_as_connector_status(tmp_path: Path) -> None:
    env = await github_env(tmp_path, include=["acme/checkout"])
    try:
        env.mock.revoke_token(GH_TOKEN)
        report = await env.pipe.sync(env.cid)
        assert report.error_code == "auth_expired"
        assert (env.connector_row()["status"], env.connector_row()["status_code"]) == ("auth_expired", "auth_expired")
        health = await env.pipe.connector(env.cid).health(env.pipe.context(env.connector_row()))
        assert health.status == "auth_expired" and health.checks["auth"] is False
    finally:
        await env.close()


async def test_an_app_authorization_revoked_notice_revokes_the_connection(tmp_path: Path) -> None:
    env = await github_env(tmp_path, include=["acme/checkout"])
    try:
        body = json.dumps({"action": "revoked", "sender": {"id": 1001, "login": "ana"}}).encode()
        headers = {"X-GitHub-Event": "github_app_authorization", "X-GitHub-Delivery": "rev-1"}
        [notice] = GitHubConnector.parse_webhook(headers, body)
        report = await env.svc.handle_notice(env.cid, notice)
        assert report.error_code == "auth_revoked" and env.connector_row()["status"] == "revoked"
        other = GitHubConnector.parse_webhook({**headers, "X-GitHub-Delivery": "rev-2"},
                                              json.dumps({"action": "revoked", "sender": {"id": 4242}}).encode())[0]
        env.store.store._conn.execute("UPDATE connectors SET status='active' WHERE connector_id=?", (env.cid,))
        assert (await env.svc.handle_notice(env.cid, other)).error_code is None      # another user's grant: not this connection
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- OAuth
async def test_oauth_code_pkce_flow_and_refresh_against_the_mock(tmp_path: Path) -> None:
    fx = load_fixture("github_acme")
    fx["app"]["expiring_user_tokens"] = True                       # a GitHub App: user tokens expire and come with a refresh token
    env = await github_env(tmp_path, token=None, fixture=fx)
    try:
        app = OAuthAppConfig(client_id=fx["app"]["client_id"], client_secret=Secret(fx["app"]["client_secret"]), oauth_base=env.url,
                             allow_loopback_http=True)
        assert (await GitHubConnector().authorize(tenant_id=TENANT, holder_id="h", redirect_uri="https://m/cb", state="s")).kind == "token_entry"
        gh = GitHubConnector(oauth=app)
        start = await gh.authorize(tenant_id=TENANT, holder_id="h", redirect_uri="https://mycelic.example/oauth/callback", state="st-1")
        q = parse_qs(urlsplit(start.url).query)
        assert start.kind == "redirect" and q["code_challenge_method"] == ["S256"] and q["state"] == ["st-1"] and "scope" not in q
        assert start.pkce_verifier is not None and start.pkce_verifier.reveal() not in start.url
        async with aiohttp.ClientSession() as s:
            async with s.get(start.url, allow_redirects=False) as resp:
                location = resp.headers["Location"]
        params = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        assert params["state"] == "st-1"
        with pytest.raises(PermanentError) as e:                    # a wrong verifier is refused by the token endpoint
            await gh.complete_authorization(dict(params), redirect_uri="https://mycelic.example/oauth/callback", pkce_verifier=Secret("x" * 50))
        assert e.value.code.startswith("oauth_")
        start2 = await gh.authorize(tenant_id=TENANT, holder_id="h", redirect_uri="https://mycelic.example/oauth/callback", state="st-2")
        async with aiohttp.ClientSession() as s:
            async with s.get(start2.url, allow_redirects=False) as resp:
                params = {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["Location"]).query).items()}
        got = await gh.complete_authorization(params, redirect_uri="https://mycelic.example/oauth/callback", pkce_verifier=start2.pkce_verifier)
        assert got.kind == "github_app_user" and got.refresh_token is not None and got.expires_at
        token_calls = [r for r in env.mock.requests if r.path == "/login/oauth/access_token"]
        assert token_calls and all(r.method == "POST" and not r.query for r in token_calls)      # secrets travel in the form body only
        # the credentials work for a connection; an expired user token is refreshed once under the refresh lock
        reg = register_builtin(ConnectorRegistry())
        env.pipe.registry = reg

        class AppConnector(GitHubConnector):
            def __init__(self) -> None:
                super().__init__(oauth=app)
        reg.register(AppConnector, replace=True)
        con = await env.svc.add_connector("github", created_by=OWNER, config={"api_base": env.url}, credentials=got)
        env.con = con
        await env.svc.discover_sources(env.cid)
        for s in env.svc.sources(env.cid):
            await env.svc.set_source(s.source_id, actor=OWNER, selection="included")
        env.mock.expire_token(got.access_token.reveal())
        report = await env.pipe.sync(env.cid)
        await env.pipe.process_available()
        assert report.error_code is None and env.live_ids("issue")
        stored = await env.pipe.context(env.connector_row()).secrets.get()
        assert stored.access_token != got.access_token and stored.refresh_token != got.refresh_token
        assert any(r.path == "/login/oauth/access_token" and r.form.get("grant_type") == "refresh_token" for r in env.mock.requests)
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- normalize
def test_normalize_identity_hints_links_and_acl() -> None:
    from mycelic.ingest.mocks.github_mock import GitHubMock
    mock = GitHubMock(load_fixture("github_acme"))
    mock.url = "https://api.github.com"
    gh = GitHubConnector()
    ctx = ConnectorContext(tenant_id=TENANT, holder_id="hold_x", connector_id="con_x", connector_type="github", source_app="github",
                           source_account_id="api.github.com", auth_account_id="1001", config={}, limits=ConnectorLimits(), http=None,
                           secrets=None, checkpoints=None, log=connector_logger("github", "con_x"), clock=FakeClock(0).datetime)
    src = gh._descriptor(ctx, mock.repo_json(mock.repos["acme/checkout"]))
    issue = mock.issue_json(mock.issues[("acme/checkout", 482)])
    conv, msg = gh.normalize(RawItem("issue", issue, src, "2026-10-01T12:00:00+00:00"), ctx)
    assert (conv.kind, conv.source_object_type, conv.source_object_id) == ("conversation", "conversation", f"{CHECKOUT}#482")
    assert (msg.kind, msg.source_object_type, msg.conversation_id, msg.author_id) == ("message", "issue", f"{CHECKOUT}#482", "1002")
    assert msg.source_version == "2026-09-30T14:10:00.000000+00:00" and msg.order_key.startswith("2026-09-30T14:10:00.000000+00:00|")
    assert set(msg.participant_ids) == {"1002", "1001"} and msg.hints["labels"] == ["bug", "regression"] and msg.content_type == "text/markdown"
    assert conv.title == "acme/checkout#482 httpclient 4.2 introduced a timeout regression in checkout"
    pr_conv, pr = gh.normalize(RawItem("issue", mock.issue_json(mock.issues[("acme/checkout", 483)]), src, "2026-10-01T12:00:00+00:00"), ctx)
    assert pr.source_object_type == "pull_request" and "https://github.com/acme/checkout/issues/482" in pr.links
    [comment] = gh.normalize(RawItem("issue_comment", mock.comment_json(mock.comments[880101]), src, "2026-10-01T12:00:00+00:00"), ctx)
    assert comment.parent_message_id == f"{CHECKOUT}#482" and comment.conversation_record_id() == conv.record_id
    assert "https://acme.slack.com/archives/C0DEPLOY/p1790674800000100" in comment.links
    # every issue, PR, comment and conversation event names its repository (used by the linking stage)
    assert {e.hints["repo"] for e in (conv, msg, pr_conv, pr, comment)} == {"acme/checkout"}
    assert msg.permissions.visibility == "members" and msg.permissions.membership_ref == "github:api.github.com:repo:5001"
    [gone] = gh.normalize(RawItem("deletion", {"object_type": "issue_comment", "object_id": "880101", "reason": "deleted_at_source"}, src,
                                  "2026-10-02T00:00:00+00:00"), ctx)
    assert gone.kind == "deletion" and gone.body == "" and gone.hints["repo"] == "acme/checkout"
    with pytest.raises(PermanentError):
        gh.normalize(RawItem("issue", {"no": "number"}, src, "2026-10-01T12:00:00+00:00"), ctx)


# ---------------------------------------------------------------------------------------------- conformance
def _type_ok(value, spec) -> bool:
    if value is None:
        return bool(spec.get("nullable")) or spec.get("type") == "any"
    return {"string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool), "boolean": isinstance(value, bool),
            "object": isinstance(value, dict), "array": isinstance(value, list), "number": isinstance(value, (int, float)),
            "any": True}.get(spec.get("type"), True)


def _conforms(obj: dict, schema: dict, label: str) -> None:
    missing = [k for k in schema["required"] if k not in obj]
    assert not missing, f"{label}: missing {missing}"
    bad = [k for k, spec in schema["properties"].items() if k in obj and not _type_ok(obj[k], spec)]
    assert not bad, f"{label}: wrong types {bad}"


# the keys the connector reads from each object; each must be declared by GitHub's description
CONNECTOR_KEYS = {"issue": ("id", "number", "title", "body", "user", "labels", "assignees", "state", "created_at", "updated_at", "html_url", "pull_request"),
                  "issue-comment": ("id", "body", "user", "created_at", "updated_at", "issue_url", "html_url"),
                  "repository": ("id", "full_name", "owner", "private", "visibility", "archived", "default_branch", "html_url", "has_issues",
                                 "open_issues_count"),
                  "simple-user": ("id", "login", "type")}


async def test_mock_responses_conform_to_githubs_openapi_description(tmp_path: Path) -> None:
    subset = json.loads((FIXTURES / "github_openapi_subset.json").read_text())
    schemas = subset["schemas"]
    for name, keys in CONNECTOR_KEYS.items():
        declared = set(schemas[name]["required"]) | set(schemas[name]["properties"])
        assert set(keys) <= declared, (name, set(keys) - declared)
    ops = subset["operations"]
    assert ops["issues/list-for-repo"]["params"]["state"]["default"] == "open"             # why the connector sends state=all
    assert set(ops["issues/get"]["responses"]) >= {"200", "301", "404", "410"}
    env = await github_env(tmp_path, token="gh-mock-ana-classic-repo", include=False)
    try:
        h = {"Authorization": "Bearer gh-mock-ana-classic-repo", "X-GitHub-Api-Version": "2026-03-10"}
        async with aiohttp.ClientSession() as s:
            async def get(path):
                async with s.get(env.url + path, headers=h, allow_redirects=False) as resp:
                    return resp.status, await resp.json()
            _, me = await get("/user")
            _conforms(me, schemas["private-user"], "GET /user")
            _, repos = await get("/user/repos")
            for r in repos:
                _conforms(r, schemas["repository"], "GET /user/repos[]")
                _conforms(r["owner"], schemas["simple-user"], "repository.owner")
            _, org = await get("/orgs/acme/repos")
            for r in org:
                _conforms(r, schemas["minimal-repository"], "GET /orgs/{org}/repos[]")
            _, full = await get("/repos/acme/checkout")
            _conforms(full, schemas["full-repository"], "GET /repos/{o}/{r}")
            _, issues = await get("/repos/acme/checkout/issues?state=all")
            assert issues
            for i in issues:
                _conforms(i, schemas["issue"], "GET issues[]")
                _conforms(i["user"], schemas["simple-user"], "issue.user")
                for lb in i["labels"]:
                    _conforms(lb, schemas["label"], "issue.labels[]")
            _, one = await get("/repos/acme/checkout/issues/482")
            _conforms(one, schemas["issue"], "GET issue")
            _, comments = await get("/repos/acme/checkout/issues/comments")
            for c in comments:
                _conforms(c, schemas["issue-comment"], "GET comments[]")
            status, gone = await get("/repos/acme/checkout/issues/475")
            assert status == 301 and "url" in gone
        for event, action, kw in (("issues", "edited", {"number": 482, "changes": {"body": {"from": "x"}}}), ("issues", "deleted", {"number": 482}),
                                  ("issue_comment", "edited", {"number": 482, "comment_id": 880101, "changes": {"body": {"from": "x"}}}),
                                  ("issue_comment", "deleted", {"number": 482, "comment_id": 880101})):
            payload = env.mock.webhook_payload(event, action, repo="acme/checkout", **kw)
            spec = subset["webhooks"][f"{event.replace('_', '-')}-{action}"]
            assert not [k for k in spec["required"] if k not in payload], (event, action)
            assert {"X-GitHub-Event", "X-GitHub-Delivery", "X-Hub-Signature-256"} <= set(env.mock.signed_delivery(event, payload)[0])
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- logs
async def test_nothing_content_bearing_is_logged(tmp_path: Path) -> None:
    sentinel = "Sentinelword9083"
    fx = load_fixture("github_acme")
    fx["issues"][2]["body"] += f"\n{sentinel} in the issue body."
    fx["comments"][0]["body"] += f" {sentinel} in a comment."
    handler = Collect()
    import logging
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        env = await github_env(tmp_path, include=["acme/checkout"], fixture=fx)
        try:
            await full_sync(env)
            env.mock.fail_next(1)
            await deliver(env, "issue_comment", env.mock.edit_comment(880101, f"{sentinel} edited again"))
            await deliver(env, "issue_comment", env.mock.delete_comment(880101))
            env.mock.revoke_token(GH_TOKEN)
            await env.pipe.sync(env.cid)
            metrics, outbox = json.dumps(env.pipe.db.metrics()), json.dumps(env.pipe.publisher.sent)
        finally:
            await env.close()
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    blob = "\n".join(handler.lines)
    assert handler.lines and "/repos/{name}/{name}/issues" in blob
    for needle in (sentinel, GH_TOKEN, "httpclient 4.2", "acme/checkout", "Bearer"):
        assert needle not in blob, needle
    assert sentinel not in metrics and sentinel not in outbox


# ---------------------------------------------------------------------------------------------- live (opt-in, never required)
@pytest.mark.live
@pytest.mark.skipif(not (os.environ.get("MYCELIC_LIVE_GITHUB_TOKEN") and os.environ.get("MYCELIC_LIVE_GITHUB_REPO")),
                    reason="live GitHub test: set MYCELIC_LIVE_GITHUB_TOKEN and MYCELIC_LIVE_GITHUB_REPO (owner/name) to run")
async def test_live_github_read_only(tmp_path: Path) -> None:      # pragma: no cover - needs real credentials
    from mycelic.ingest.crypto import TokenVault
    from mycelic.ingest.service import IngestService

    from .ingest_support import make_pipeline, make_store
    store = make_store(tmp_path, "live")
    pipe = make_pipeline(store, vault=TokenVault.from_secret("live-test-master-key-0001"))
    svc = IngestService(pipe)
    try:
        con = await svc.add_connector("github", created_by=OWNER, config={"repos": [os.environ["MYCELIC_LIVE_GITHUB_REPO"]],
                                                                          "limits": {"page_size": 5}},
                                      credentials=creds(os.environ["MYCELIC_LIVE_GITHUB_TOKEN"]))
        await svc.discover_sources(con["connector_id"])
        for s in svc.sources(con["connector_id"]):
            await svc.set_source(s.source_id, actor=OWNER, selection="included")
        first = await pipe.sync(con["connector_id"])
        assert first.error_code is None
        await pipe.sync(con["connector_id"])
        third = await pipe.sync(con["connector_id"])
        assert third.error_code is None and third.enqueued == 0
    finally:
        await pipe.aclose()
        await store.close()
