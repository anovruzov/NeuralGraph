"""GitHub and Slack feed one holder (the cross-app demo story, offline): a Slack #deployments thread about the failed
checkout deployment and the GitHub issue about the httpclient 4.2 timeout regression become evidence of one store, an
owner answer cites both apps, and the private incident channel stays inside its members. Also checks fixture rebasing,
which the demo uses to run the same story against the real clock."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mycelic.ingest.crypto import TokenVault
from mycelic.ingest.mocks import FakeClock, load_fixture, loopback_http_factory, start_github_mock, start_slack_mock
from mycelic.ingest.service import IngestService
from mycelic.util import fingerprint

from .connector_support import BEN, DEE, GH_TOKEN, GITHUB_PRINCIPALS, SL_TOKEN, SLACK_PRINCIPALS, VAULT_SECRET, creds
from .ingest_support import OWNER, make_pipeline, make_store, question


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
        hits = await store.search("deployment of checkout failed httpclient 4.2 timeout regression", k=10)
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
