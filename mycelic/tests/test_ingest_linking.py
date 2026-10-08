"""Cross-app linking inside one holder (INGESTION.md §8.1–§8.4, §8.7): shared canonical entities connect a chat message
and an issue that never reference each other, edges keep their modality, only public evidence makes an edge
traversable, and deleting a record withdraws the evidence it gave.
"""
from __future__ import annotations

from pathlib import Path

from mycelic.ingest.linking import extract_mentions, extract_references, link_record
from mycelic.ingest.service import IngestService
from mycelic.tests.ingest_support import OWNER, connect_export, make_pipeline, make_store, write_jsonl

SLACK = "Deployment of checkout failed after the dependency upgrade to httpclient 4.2; requests time out."
GITHUB = "httpclient 4.2 introduced a timeout regression in checkout. The default pool timeout dropped after the bump."


def test_references_need_known_context():
    refs = {r.entity_id for r in extract_references("See https://github.com/acme/checkout/issues/482 and acme/infra#7, plus #12.", own_repo="acme/checkout")}
    assert {"issue:github:acme/checkout#482", "issue:github:acme/infra#7", "issue:github:acme/checkout#12", "repo:github:acme/checkout"} <= refs
    # tracker keys only with known prefixes: UTF-8 and ISO-8601 are not issues
    assert not extract_references("Encoded as UTF-8, dates as ISO-8601, ticket OPS-12.")
    assert {r.entity_id for r in extract_references("ticket OPS-12", known_keys=["OPS"])} == {"issue:tracker:ops-12"}
    # versions only in dependency vocabulary
    assert not [r for r in extract_mentions("We met on page 4.2 of the Q3 report.") if r.kind == "version"]


def test_modality_after_is_temporal_hedged_is_hypothesized_negated_is_negated():
    a = link_record("", SLACK, record_id="rec_a")
    preds = {(e.predicate, e.modality) for e in a.edges}
    assert ("followed", "temporal") in preds and not any(p == "caused" for p, _ in preds)
    b = link_record("", "The timeouts might be caused by httpclient 4.2 after the upgrade.", record_id="rec_b")
    assert {e.modality for e in b.edges if e.predicate == "caused"} == {"hypothesized"}
    c = link_record("", "The timeouts were not caused by httpclient 4.2; the upgrade was ruled out.", record_id="rec_c")
    assert {e.modality for e in c.edges if e.predicate == "caused"} <= {"negated"}


async def _two_apps(tmp_path: Path, *, slack_visibility: str = "public"):
    store = make_store(tmp_path)
    svc = IngestService(make_pipeline(store))
    slack = write_jsonl(tmp_path / "slack.jsonl", [{"type": "message", "id": "m1", "text": SLACK, "created_at": "2026-09-14T10:00:00+00:00",
                                                    "author": "u1"}],
                        header={"id": "C-deploys", "name": "#deployments", "source_type": "channel", "visibility": slack_visibility,
                                "member_ids": ["u1"] if slack_visibility != "public" else []})
    gh = write_jsonl(tmp_path / "gh.jsonl", [{"type": "document", "id": "482", "title": "httpclient 4.2 introduced a timeout regression in checkout",
                                              "text": GITHUB, "created_at": "2026-09-15T09:00:00+00:00", "author": "u2"}],
                     header={"id": "acme/checkout", "name": "acme/checkout", "source_type": "repo", "visibility": "public"})
    await connect_export(svc, [slack], source_app="slack", account_id="acme-ws", principal_map={"u1": OWNER})
    await connect_export(svc, [gh], source_app="github", account_id="acme-gh")
    for con in svc.p.db.list_connectors():
        await svc.p.sync(con["connector_id"], mode="incremental")
    await svc.p.process_available()
    return store, svc


async def test_records_from_two_apps_share_entities_and_retrieval_connects_them(tmp_path):
    store, svc = await _two_apps(tmp_path)
    c = store.store._conn
    by_app: dict[str, set[str]] = {}
    for r in c.execute("SELECT r.source_app, e.entity_id FROM record_entities e JOIN ingest_records r ON r.record_id=e.record_id WHERE e.method<>'structural'"):
        by_app.setdefault(r["source_app"], set()).add(r["entity_id"])
    shared = by_app["slack"] & by_app["github"]
    # (a real GitHub record also names service:checkout through its repository hint; this export has none)
    assert {"component:httpclient", "version:httpclient@4.2", "symptom:timeout"} <= shared
    # the memories carry the shared entities, so the graph channel joins them
    apps = {r["source_app"] for r in c.execute(
        "SELECT DISTINCT ir.source_app FROM memory_entities me JOIN record_memories rm ON rm.memory_id=me.memory_id "
        "JOIN ingest_records ir ON ir.record_id=rm.record_id WHERE me.entity_id='version:httpclient@4.2'")}
    assert apps == {"slack", "github"}
    assert await store.store.resolve_alias("httpclient 4.2") == "version:httpclient@4.2"
    rel = c.execute("SELECT status, metadata FROM relations WHERE predicate='introduced_regression'").fetchone()
    assert rel is not None and rel["status"] == "active" and '"asserted"' in rel["metadata"]
    await store.close()


async def test_edges_from_restricted_sources_are_not_traversable_and_deletion_withdraws_evidence(tmp_path):
    store, svc = await _two_apps(tmp_path, slack_visibility="members")
    c = store.store._conn
    followed = c.execute("SELECT relation_id, status FROM relations WHERE predicate='followed'").fetchone()
    assert followed is not None and followed["status"] == "restricted"          # only known from a members-only channel
    gh_rec = c.execute("SELECT record_id FROM ingest_records WHERE source_app='github'").fetchone()["record_id"]
    assert c.execute("SELECT status FROM relations WHERE predicate='introduced_regression'").fetchone()["status"] == "active"
    await svc.delete_record(gh_rec, actor=OWNER)
    await svc.p.process_available()
    assert c.execute("SELECT COUNT(*) AS n FROM relation_evidence WHERE record_id=?", (gh_rec,)).fetchone()["n"] == 0
    assert c.execute("SELECT status FROM relations WHERE predicate='introduced_regression'").fetchone()["status"] == "retracted"
    await store.close()
