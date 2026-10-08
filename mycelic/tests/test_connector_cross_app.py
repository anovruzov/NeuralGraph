"""GitHub, Slack, Gmail and Google Drive feed one holder (the cross-app demo story, offline): a Slack #deployments thread
about the failed checkout deployment, the GitHub issue about the httpclient 4.2 timeout regression, a customer's e-mail
that the renewal is at risk (with Ana's reply and a forwarded GitHub notification) and the incident review in the
Engineering shared drive become evidence of one store and share entities (the issue, the dependency version, people);
an owner answer cites several apps, and member-restricted or private content stays inside its audience. Also checks
fixture rebasing, which the demo uses to run the same story against the real clock."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mycelic.ingest.crypto import TokenVault
from mycelic.ingest.mocks import (FakeClock, load_fixture, loopback_http_factory, start_drive_mock, start_github_mock, start_gmail_mock,
                                  start_slack_mock)
from mycelic.ingest.service import IngestService
from mycelic.util import fingerprint

from .connector_support import (BEN, CUSTOMER_NOTES, DEE, DRIVE_SCOPE, EMAIL_PRINCIPALS, GD_TOKEN, GH_TOKEN, GITHUB_PRINCIPALS, GM_TOKEN,
                                GMAIL_SCOPE, SL_TOKEN, SLACK_PRINCIPALS, VAULT_SECRET, creds, google_creds)
from .ingest_support import OWNER, make_pipeline, make_store, question

OWNER_READ = {"principal_ids": [], "complete": True, "owner": True}          # the holder owner's own view (reads fail closed without an audience)


async def test_github_and_slack_feed_one_holder(tmp_path: Path) -> None:
    gh_fx, sl_fx = load_fixture("github_acme"), load_fixture("slack_acme")
    clock = FakeClock(gh_fx["now"])
    gh_url, gh = await start_github_mock(gh_fx, clock=clock)
    sl_url, sl = await start_slack_mock(sl_fx, clock=clock)
    store = make_store(tmp_path)
    pipe = make_pipeline(store, vault=TokenVault.from_secret(VAULT_SECRET), http_factory=loopback_http_factory(clock), clock=clock.datetime)
    svc = IngestService(pipe)
    try:
        g = await svc.add_connector("github", created_by=OWNER, credentials=creds(GH_TOKEN),
                                    config={"api_base": gh_url, "auto_include": ["acme/checkout"], "principal_map": GITHUB_PRINCIPALS})
        s = await svc.add_connector("slack", created_by=OWNER, credentials=creds(SL_TOKEN),
                                    config={"api_base": sl_url + "/api", "slack_app_class": "internal", "principal_map": SLACK_PRINCIPALS,
                                            "auto_include": ["#deployments", "#incident-checkout"]})
        for con in (g, s):
            await svc.discover_sources(con["connector_id"])
            assert (await pipe.sync(con["connector_id"], mode="backfill")).error_code is None
            assert (await pipe.sync(con["connector_id"])).error_code is None
        await pipe.process_available()
        c = store.store._conn
        apps = {r[0]: r[1] for r in c.execute("SELECT source_app, COUNT(*) FROM ingest_records WHERE kind='message' GROUP BY source_app")}
        assert apps["github"] >= 6 and apps["slack"] >= 9
        hits = await store.search("deployment of checkout failed httpclient 4.2 timeout regression", k=10, audience=OWNER_READ)
        hit_apps = {c.execute("SELECT source_app FROM ingest_records WHERE record_id=?", (h["doc_id"],)).fetchone()[0] for h in hits}
        assert hit_apps == {"github", "slack"}
        resp = await store.answer_question(question("Did the httpclient 4.2 dependency upgrade cause the checkout deployment failure and "
                                                    "the timeouts?", audience={"owner": True}))
        assert resp["status"] == "answered" and {r["meta"]["source_app"] for r in resp["evidence_refs"]} == {"github", "slack"}
        # the private incident channel stays inside its members, whichever app the question also reaches
        q = "What is the root cause of the checkout timeouts with httpclient 4.2 and the connection pool size?"
        inside = await store.answer_question(question(q, audience={"principal_ids": [BEN], "complete": True}))
        outside = await store.answer_question(question(q, audience={"principal_ids": [DEE], "complete": True}))
        assert "connection pool size" not in json.dumps(outside)
        incident = fingerprint(next(m["text"] for m in sl_fx["messages"] if m["channel"] == "G0INCIDENT" and "Root cause" in m["text"]))
        assert any(r["source_root_id"] == incident for r in inside["evidence_refs"])
        assert not any(r["source_root_id"] == incident for r in outside["evidence_refs"])
    finally:
        await pipe.aclose()
        await store.close()
        await gh.close()
        await sl.close()


async def test_github_slack_gmail_and_drive_feed_one_holder_and_share_entities(tmp_path: Path) -> None:
    fixtures = {name: load_fixture(name) for name in ("github_acme", "slack_acme", "gmail_acme", "drive_acme")}
    assert len({fx["now"] for fx in fixtures.values()}) == 1                          # one story, one clock
    clock = FakeClock(fixtures["github_acme"]["now"])
    gh_url, gh = await start_github_mock(fixtures["github_acme"], clock=clock)
    sl_url, sl = await start_slack_mock(fixtures["slack_acme"], clock=clock)
    gm_url, gm = await start_gmail_mock(fixtures["gmail_acme"], clock=clock)
    gd_url, gd = await start_drive_mock(fixtures["drive_acme"], clock=clock)
    store = make_store(tmp_path)
    pipe = make_pipeline(store, vault=TokenVault.from_secret(VAULT_SECRET), http_factory=loopback_http_factory(clock), clock=clock.datetime)
    svc = IngestService(pipe)
    try:
        cons = [
            await svc.add_connector("github", created_by=OWNER, credentials=creds(GH_TOKEN),
                                    config={"api_base": gh_url, "auto_include": ["acme/checkout"], "principal_map": GITHUB_PRINCIPALS}),
            await svc.add_connector("slack", created_by=OWNER, credentials=creds(SL_TOKEN),
                                    config={"api_base": sl_url + "/api", "slack_app_class": "internal", "principal_map": SLACK_PRINCIPALS,
                                            "auto_include": ["#deployments", "#incident-checkout"]}),
            await svc.add_connector("gmail", created_by=OWNER, credentials=google_creds(GM_TOKEN, GMAIL_SCOPE),
                                    config={"api_base": gm_url, "principal_map": EMAIL_PRINCIPALS, "auto_include": ["Customers", "GitHub", "INBOX"]}),
            await svc.add_connector("google_drive", created_by=OWNER, credentials=google_creds(GD_TOKEN, DRIVE_SCOPE),
                                    config={"api_base": gd_url, "principal_map": EMAIL_PRINCIPALS, "folder_ids": [CUSTOMER_NOTES],
                                            "auto_include": ["Engineering", CUSTOMER_NOTES]}),
        ]
        for con in cons:
            # backfill windows are anchored at the connection's creation time: on the story's clock
            store.store._conn.execute("UPDATE connectors SET created_at=? WHERE connector_id=?", (clock.datetime().isoformat(), con["connector_id"]))
            await svc.discover_sources(con["connector_id"])
            assert (await pipe.sync(con["connector_id"])).error_code is None
            assert (await pipe.sync(con["connector_id"], mode="backfill")).error_code is None
        await pipe.process_available()
        c = store.store._conn
        inbox = next(s for s in svc.sources(cons[2]["connector_id"]) if s.external_id == "INBOX")
        assert inbox.selection == "pending_review"                                     # a rule never includes the inbox
        apps = {r[0]: r[1] for r in c.execute("SELECT source_app, COUNT(*) FROM ingest_records WHERE kind IN ('message', 'document') "
                                              "AND deletion_status='live' GROUP BY source_app")}
        assert set(apps) == {"github", "slack", "gmail", "google_drive"} and apps["gmail"] >= 6 and apps["google_drive"] >= 8

        def apps_of(entity: str) -> set[str]:
            return {r[0] for r in c.execute("SELECT DISTINCT r.source_app FROM record_entities e JOIN ingest_records r ON r.record_id=e.record_id "
                                            "WHERE e.entity_id=? AND r.deletion_status='live'", (entity,))}
        # one connected graph: the same issue, dependency version and people, reached from every app
        assert apps_of("issue:github:acme/checkout#482") == {"github", "slack", "gmail", "google_drive"}
        assert apps_of("version:httpclient@4.2") == {"github", "slack", "gmail", "google_drive"}
        assert apps_of("person:ben@acme.example") == {"gmail", "google_drive"} and "gmail" in apps_of("org:globex.example")
        hits = await store.search("httpclient 4.2 timeouts checkout connection pool renewal at risk", k=30, audience=OWNER_READ)
        hit_apps = {c.execute("SELECT source_app FROM ingest_records WHERE record_id=?", (h["doc_id"],)).fetchone()[0] for h in hits if h["doc_id"]}
        assert hit_apps == {"github", "slack", "gmail", "google_drive"}
        resp = await store.answer_question(question("Did the httpclient 4.2 dependency upgrade cause the checkout timeouts, and is the Globex "
                                                    "renewal at risk because of the outages?", audience={"owner": True}))
        assert resp["status"] == "answered" and len({r["meta"]["source_app"] for r in resp["evidence_refs"]}) >= 2
        # the forwarded GitHub notification and the notification itself are one source, not two
        fwd, notif = (c.execute("SELECT source_root_id FROM ingest_records WHERE source_app='gmail' AND source_object_id=?", (m,)).fetchone()[0]
                      for m in ("18f2a0c0de000004", "18f29c0de0000003"))
        assert fwd == notif
        # restricted content stays inside its audience, whichever app the question also reaches
        review_q = "What does the checkout incident review say about httpclient 4.2 and the connection pool size?"
        review_root = c.execute("SELECT source_root_id FROM ingest_records WHERE source_object_id='1CheckoutIncidentReviewDoc'").fetchone()[0]
        inside = await store.answer_question(question(review_q, audience={"principal_ids": [BEN], "complete": True}))
        outside = await store.answer_question(question(review_q, audience={"principal_ids": [DEE], "complete": True}))
        assert any(r["source_root_id"] == review_root for r in inside["evidence_refs"])
        assert not any(r["source_root_id"] == review_root for r in outside["evidence_refs"])
        reply_root = c.execute("SELECT source_root_id FROM ingest_records WHERE source_app='gmail' AND source_object_id='18f2a0c0de000002'").fetchone()[0]
        mailbox = await store.answer_question(question("What did Ana reply to Globex about the checkout timeouts and the rollback to httpclient 4.1?",
                                                       audience={"principal_ids": [BEN], "complete": True}))
        assert not any(r["source_root_id"] == reply_root for r in mailbox["evidence_refs"])      # a mailbox is private until a label is opted in
    finally:
        await pipe.aclose()
        await store.close()
        for mock in (gh, sl, gm, gd):
            await mock.close()


def test_fixtures_rebase_consistently_for_a_live_demo() -> None:
    now = datetime(2027, 1, 15, 9, 30, tzinfo=timezone.utc)
    gh, sl = load_fixture("github_acme", now=now), load_fixture("slack_acme", now=now)
    assert gh["now"] == sl["now"] == "2027-01-15T09:30:00Z"
    parent = next(m for m in sl["messages"] if m["text"].startswith("Deployment of checkout failed"))
    link = next(c for c in gh["comments"] if c["id"] == 880101)["body"]
    assert f"/archives/C0DEPLOY/p{parent['ts'].replace('.', '')}" in link          # the cross-app link still points at the thread
    shift = now.timestamp() - datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()
    assert float(parent["ts"]) == 1790674800.0001 + round(shift)
    issue = next(i for i in gh["issues"] if i["number"] == 482)
    assert issue["updated_at"] == datetime.fromtimestamp(datetime(2026, 9, 30, 14, 10, tzinfo=timezone.utc).timestamp() + shift,
                                                         tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert load_fixture("github_acme") != gh                                       # loading again without `now` is the original
    gm, gd = load_fixture("gmail_acme", now=now), load_fixture("drive_acme", now=now)
    assert gm["now"] == gd["now"] == "2027-01-15T09:30:00Z"
    renewal = next(m for m in gm["messages"] if m["id"] == "18f2a0c0de000001")
    assert renewal["internalDate"] == datetime.fromtimestamp(datetime(2026, 9, 30, 9, 15, tzinfo=timezone.utc).timestamp() + shift,
                                                             tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    review = next(f for f in gd["files"] if f["id"] == "1CheckoutIncidentReviewDoc")
    assert review["modifiedTime"] == datetime.fromtimestamp(datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc).timestamp() + shift,
                                                            tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
