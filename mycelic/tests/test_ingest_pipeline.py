"""End-to-end ingestion through the pipeline into one holder (acceptance items 1, 2, 6, 7, 8, 12, 14 of Phase A).

Two ``local_export`` instances with different ``source_app`` labels play two apps. Offline and deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mycelic.ingest.connectors.local_export import LocalExportConnector
from mycelic.ingest.contract import ConnectorManifest
from mycelic.ingest.events import record_key
from mycelic.ingest.queue import StaleCheckpoint
from mycelic.ingest.registry import ConnectorRegistry
from mycelic.ingest.service import IngestService
from mycelic.ingest.store import IngestStore
from mycelic.ingest.connectors import register_builtin
from mycelic.ingest.contract import WebhookNotice

from .ingest_support import (OWNER, TENANT, append_jsonl, connect_export, make_pipeline, make_store, question,
                             table_contains, write_json, write_jsonl)

CHAT_HEADER = {"id": "deploys", "name": "#deploys", "source_type": "channel", "visibility": "public"}
CHAT = [
    {"type": "message", "id": "m1", "conversation_id": "deploys", "author": "ana@acme.com", "participants": ["ana@acme.com", "ben@acme.com"],
     "created_at": "2026-03-04T10:00:00Z", "text": "The checkout deploy failed because the payment gateway timed out after the rollout. "
                                                     "We rolled back and opened kumquat ticket 482."},
    {"type": "message", "id": "m2", "conversation_id": "deploys", "author": "ben@acme.com", "created_at": "2026-03-04T10:05:00Z",
     "text": "Gateway timeouts started right after the http client library was upgraded to version 4.2 on the quokka cluster."},
]
WIKI = [
    {"type": "document", "id": "d1", "author": "ana@acme.com", "created_at": "2026-03-05T09:00:00Z", "title": "Checkout incident review",
     "text": "Root cause: the payment gateway timed out after the http client library upgrade.\n\nMitigation: pin the library version."},
]


async def two_apps(tmp_path: Path, **kw: Any):
    store = make_store(tmp_path, **kw.pop("store_kw", {}))
    pipe = make_pipeline(store, **kw)
    svc = IngestService(pipe)
    chat = write_jsonl(tmp_path / "deploys.jsonl", CHAT, header=CHAT_HEADER)
    wiki = write_json(tmp_path / "wiki.json", WIKI, header={"id": "eng-wiki", "name": "Engineering wiki", "type": "folder", "visibility": "public"})
    a = await connect_export(svc, [chat], source_app="teamchat")
    b = await connect_export(svc, [wiki], source_app="wiki")
    return store, pipe, svc, a, b, chat, wiki


async def sync_all(pipe, *connectors) -> None:
    for con in connectors:
        await pipe.sync(con["connector_id"])
    await pipe.process_available()


def counts(store) -> dict[str, int]:
    c = store.store._conn
    q = lambda sql: int(c.execute(sql).fetchone()[0])  # noqa: E731
    return {"documents": q("SELECT COUNT(*) FROM documents"), "memories": q("SELECT COUNT(*) FROM memories"),
            "messages": q("SELECT COUNT(*) FROM messages"), "records": q("SELECT COUNT(*) FROM ingest_records"),
            "versions": q("SELECT COUNT(*) FROM document_versions"), "entities": q("SELECT COUNT(*) FROM entities"),
            "applied": q("SELECT COUNT(*) FROM applied_events"), "outbox": q("SELECT COUNT(*) FROM ingest_outbox"),
            "exports": q("SELECT COUNT(*) FROM exports"), "memberships": q("SELECT COUNT(*) FROM domain_memberships")}


# ---------------------------------------------------------------------------------------------- 1. two apps, one holder
async def test_two_apps_feed_the_same_holder(tmp_path: Path) -> None:
    store, pipe, _svc, a, b, *_ = await two_apps(tmp_path)
    await sync_all(pipe, a, b)
    c = store.store._conn
    apps = {r["source_app"]: r["n"] for r in c.execute("SELECT source_app, COUNT(*) AS n FROM ingest_records GROUP BY source_app")}
    assert apps == {"teamchat": 2, "wiki": 1}
    # one holder, one file: every record is a document of the same evidence.db
    assert store.path == str(tmp_path / "h1.db")
    assert c.execute("SELECT COUNT(*) FROM documents d JOIN ingest_records r ON r.record_id = d.doc_id").fetchone()[0] == 3
    hits = await store.search("payment gateway timed out after the library upgrade", k=10)
    hit_apps = {c.execute("SELECT source_app FROM ingest_records WHERE record_id=?", (h["doc_id"],)).fetchone()[0] for h in hits}
    assert hit_apps == {"teamchat", "wiki"}
    # one entity table: the same person (an email address) authored records in both apps
    ana_apps = {r[0] for r in c.execute("""SELECT DISTINCT r.source_app FROM record_entities e JOIN ingest_records r ON r.record_id = e.record_id
                                           WHERE e.entity_id='person:ana@acme.com' AND e.role='author'""")}
    assert ana_apps == {"teamchat", "wiki"}
    assert c.execute("SELECT COUNT(*) FROM entities WHERE entity_id='person:ana@acme.com'").fetchone()[0] == 1
    # the coordinator learns about new evidence from one batched, title-free ingest_result
    batches = pipe.publisher.of("ingest_result")
    assert len(batches) == 1 and batches[0]["batch"]["records"] == 3 and batches[0]["batch"]["by_app"] == {"teamchat": 2, "wiki": 1}
    assert batches[0]["title"] == "" and batches[0]["document"] is None and "Checkout" not in json.dumps(batches[0])
    await store.close()


# ---------------------------------------------------------------------------------------------- 2. source identity
async def test_source_identity_is_preserved_and_survives_reconnect(tmp_path: Path) -> None:
    store, pipe, svc, a, b, chat, _ = await two_apps(tmp_path)
    await sync_all(pipe, a, b)
    rec = store.store._conn.execute("SELECT * FROM ingest_records WHERE source_object_id='m1'").fetchone()
    assert (rec["source_app"], rec["source_account_id"], rec["source_object_type"], rec["source_object_id"]) == ("teamchat", "acme", "message", "m1")
    assert rec["record_key"] == record_key(TENANT, store.holder_id, "teamchat", "acme", "message", "m1")
    assert rec["connector_id"] == a["connector_id"] and rec["object_key"].startswith("ok1_")
    # the evidence that leaves the holder names its app (meta), never its record or document id
    resp = await store.answer_question(question("Why did the checkout deploy fail after the rollout?"))
    assert resp["status"] == "answered"
    metas = [r["meta"] for r in resp["evidence_refs"]]
    assert metas and {m["source_app"] for m in metas} <= {"teamchat", "wiki"} and all(m["record_kind"] for m in metas)
    assert rec["record_id"] not in json.dumps(resp)
    # reconnecting the same account (a new connection row) re-reads the export without duplicating any object
    before = counts(store)
    await svc.disconnect(a["connector_id"], actor=OWNER, data="keep")
    a2 = await connect_export(svc, [chat], source_app="teamchat")
    assert a2["connector_id"] != a["connector_id"]
    await sync_all(pipe, a2)
    after = counts(store)
    assert after["records"] == before["records"] and after["documents"] == before["documents"] and after["memories"] == before["memories"]
    await store.close()


# ---------------------------------------------------------------------------------------------- 6. replay
async def test_replaying_events_does_not_duplicate_memory(tmp_path: Path) -> None:
    store, pipe, svc, a, b, *_ = await two_apps(tmp_path)
    await sync_all(pipe, a, b)
    base = counts(store)
    # the same pages again: cursor reset (a re-import of the same file) plus a full second pass
    store.store._conn.execute("UPDATE connector_checkpoints SET cursor='{}'")
    again = await pipe.sync(a["connector_id"])
    assert again.enqueued == 0 and again.duplicates == 2            # absorbed at enqueue by event_key
    await pipe.process_available()
    # the same delivery twice (notice dedupe) and a fresh delivery for unchanged objects (event dedupe)
    src = svc.sources(a["connector_id"])[0]
    reports = []
    for delivery in ("dlv-1", "dlv-1", "dlv-2"):
        reports.append(await svc.handle_notice(a["connector_id"], WebhookNotice("local_export", delivery, "acme", src.external_id, "changed",
                                                                                ({"type": "message", "id": "m1"},))))
        await pipe.process_available()
    assert [r.error_code for r in reports] == [None, None, None]
    assert reports[1].duplicates == 1 and reports[1].pages == 0      # the redelivered notice did nothing
    assert reports[2].pages == 1 and reports[2].enqueued == 0        # the refetched object was already applied
    # nothing is left queued, and every count is what the first pass produced
    assert all(not {"queued", "leased"} & set(v) for v in pipe.queue.counts_sync().values())
    assert counts(store) == base
    await store.close()


async def test_an_event_queued_again_after_it_was_applied_is_a_noop(tmp_path: Path) -> None:
    store, pipe, _svc, a, _b, *_ = await two_apps(tmp_path)
    await sync_all(pipe, a)
    base = counts(store)
    # the queue row is gone (pruned) but the effect marker remains: the replayed event is acknowledged as a duplicate
    payload = store.store._conn.execute("SELECT event_key FROM applied_events LIMIT 1").fetchone()[0]
    store.store._conn.execute("DELETE FROM ingest_queue WHERE event_key=?", (payload,))
    store.store._conn.execute("UPDATE connector_checkpoints SET cursor='{}'")
    rep = await pipe.sync(a["connector_id"])
    assert rep.enqueued == 1
    out = await pipe.process_available()
    assert out.outcomes["duplicate"] == 1 and counts(store) == base
    await store.close()


async def test_a_crash_between_lease_and_ack_is_redone_exactly_once(tmp_path: Path) -> None:
    store, pipe, _svc, a, _b, *_ = await two_apps(tmp_path)
    await pipe.sync(a["connector_id"])
    leased = await pipe.queue.lease("dead-worker", n=1)
    assert leased
    store.store._conn.execute("UPDATE ingest_queue SET leased_until='2000-01-01T00:00:00.000000+00:00' WHERE status='leased'")
    report = await pipe.process_available()           # sweeps the expired lease first
    assert report.outcomes["new"] == 2
    with pytest.raises(Exception):
        await pipe.queue.ack(leased[0].item_id, "dead-worker", "new")     # the zombie cannot ack
    assert store.store._conn.execute("SELECT COUNT(*) FROM ingest_records").fetchone()[0] == 2
    await store.close()


# ---------------------------------------------------------------------------------------------- 7. update re-indexes
async def test_an_update_changes_the_indexed_representation(tmp_path: Path) -> None:
    store, pipe, _svc, a, _b, chat, _ = await two_apps(tmp_path)
    await sync_all(pipe, a)
    resp = await store.answer_question(question("Which kumquat ticket was opened after the checkout deploy failed?"))
    refs = {r["ref_id"] for r in resp["evidence_refs"]}
    assert resp["status"] == "answered" and refs
    rid = store.store._conn.execute("SELECT record_id FROM ingest_records WHERE source_object_id='m1'").fetchone()[0]
    old_ids = await store.store.document_memory_ids(rid)
    append_jsonl(chat, [{"type": "edit", "id": "m1", "conversation_id": "deploys", "author": "ana@acme.com", "created_at": "2026-03-04T10:00:00Z",
                         "updated_at": "2026-03-06T08:30:00Z",
                         "text": "The checkout deploy failed because the payment gateway timed out. We rolled forward with zebracorn hotfix 7."}])
    report = await pipe.sync(a["connector_id"])
    assert report.enqueued == 1
    out = await pipe.process_available()
    assert out.outcomes["update"] == 1
    assert [h["doc_id"] for h in await store.search("zebracorn hotfix")] == [rid]
    assert all(h["doc_id"] != rid for h in await store.search("kumquat ticket"))
    old = await store.store.get_memories_by_ids(old_ids)
    assert old and all(m.status in ("superseded", "retracted") for m in old)
    doc = await store.document(rid)
    assert doc["status"] == "revised" and doc["version"] == 2
    assert doc["observed_at"].startswith("2026-03-06T08:30")        # freshness = last content change, not fetch time
    rec = store.store._conn.execute("SELECT version_count, content_changed_at FROM ingest_records WHERE record_id=?", (rid,)).fetchone()
    assert rec["version_count"] == 2 and rec["content_changed_at"].startswith("2026-03-06T08:30")
    events = pipe.publisher.of("evidence_event")
    assert len(events) == 1 and events[0]["event"] == "revised" and set(events[0]["affected_ref_ids"]) == refs
    assert events[0]["new_source_root_id"] and events[0]["new_source_root_id"] != events[0]["previous_source_root_id"]
    assert events[0]["doc_id"] is None and "zebracorn" not in json.dumps(events[0])
    # a metadata-only change (labels) re-routes without a new version or an evidence event
    append_jsonl(chat, [{"type": "edit", "id": "m1", "conversation_id": "deploys", "author": "ana@acme.com", "created_at": "2026-03-04T10:00:00Z",
                         "updated_at": "2026-03-07T08:00:00Z", "labels": ["incident"],
                         "text": "The checkout deploy failed because the payment gateway timed out. We rolled forward with zebracorn hotfix 7."}])
    await pipe.sync(a["connector_id"])
    out = await pipe.process_available()
    assert out.outcomes["metadata_only"] == 1
    assert (await store.document(rid))["version"] == 2 and len(pipe.publisher.of("evidence_event")) == 1
    await store.close()


# ---------------------------------------------------------------------------------------------- 8. delete withdraws content
async def test_a_delete_withdraws_content_and_emits_an_evidence_event(tmp_path: Path) -> None:
    store, pipe, _svc, a, _b, chat, _ = await two_apps(tmp_path)
    await sync_all(pipe, a)
    resp = await store.answer_question(question("Which cluster had gateway timeouts after the library was upgraded?"))
    assert resp["status"] == "answered"
    rid = store.store._conn.execute("SELECT record_id FROM ingest_records WHERE source_object_id='m2'").fetchone()[0]
    refs = {r[0] for r in store.store._conn.execute("SELECT ref_id FROM exports WHERE doc_id=?", (rid,))}
    assert refs and refs <= {r["ref_id"] for r in resp["evidence_refs"]}
    assert table_contains(store, "quokka")
    append_jsonl(chat, [{"type": "delete", "id": "m2", "deleted_at": "2026-03-08T12:00:00Z"}])
    await pipe.sync(a["connector_id"])
    out = await pipe.process_available()
    assert out.outcomes["delete"] == 1
    # nothing of the text survives anywhere in the holder file: documents, versions, chunks, FTS, messages, audit, outcomes
    assert table_contains(store, "quokka") == []
    for suffix in ("", "-wal"):                                   # not even in freed pages or the write-ahead log
        p = Path(store.path + suffix)
        assert not p.exists() or b"quokka" not in p.read_bytes(), suffix
    c = store.store._conn
    assert c.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'quokka'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'quokka'").fetchone()[0] == 0
    assert all(h["doc_id"] != rid for h in await store.search("quokka cluster gateway timeouts"))
    doc = c.execute("SELECT text, title, status FROM documents WHERE doc_id=?", (rid,)).fetchone()
    assert doc["text"] == "" and doc["status"] == "deleted" and doc["title"] == "[deleted]"
    assert c.execute("SELECT COUNT(*) FROM document_versions WHERE doc_id=?", (rid,)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM memories WHERE chat_id=? AND (text<>'' OR embedding IS NOT NULL)", (rid,)).fetchone()[0] == 0
    raw = await store.raw_for_ref(next(iter(refs)), audience={"owner": True})
    assert raw["text"] == "" and raw["chunk_text"] is None and raw["status"] == "deleted"
    # the same evidence_event the holder already sends for withdrawals, naming every exported reference
    ev = pipe.publisher.of("evidence_event")
    assert len(ev) == 1 and ev[0]["event"] == "deleted" and set(ev[0]["affected_ref_ids"]) == refs and ev[0]["reason"] == "deleted_at_source"
    tomb = c.execute("SELECT * FROM deletion_tombstones WHERE record_id=?", (rid,)).fetchone()
    assert tomb["purged_at"] and tomb["propagated_at"] and set(json.loads(tomb["affected_ref_ids"])) == refs
    # a late edit for the deleted object cannot resurrect it
    append_jsonl(chat, [{"type": "edit", "id": "m2", "author": "ben@acme.com", "created_at": "2026-03-04T10:05:00Z",
                         "updated_at": "2026-03-09T00:00:00Z", "text": "Late quokka edit arriving after the delete."}])
    rep = await pipe.sync(a["connector_id"])
    await pipe.process_available()
    assert rep.excluded >= 1 and table_contains(store, "quokka") == []
    again = await store.answer_question(question("Which cluster had gateway timeouts after the library was upgraded?"))
    assert not {r["ref_id"] for r in again["evidence_refs"]} & refs
    await store.close()


async def test_a_redaction_removes_content_but_keeps_identity_until_the_source_shows_content_again(tmp_path: Path) -> None:
    store, pipe, _svc, a, _b, chat, _ = await two_apps(tmp_path)
    await sync_all(pipe, a)
    rid = store.store._conn.execute("SELECT record_id FROM ingest_records WHERE source_object_id='m1'").fetchone()[0]
    append_jsonl(chat, [{"type": "redact", "id": "m1", "redacted_at": "2026-03-05T00:00:00Z"}])
    await pipe.sync(a["connector_id"])
    assert (await pipe.process_available()).outcomes["redaction"] == 1
    c = store.store._conn
    assert table_contains(store, "kumquat") == []
    assert c.execute("SELECT deletion_status FROM ingest_records WHERE record_id=?", (rid,)).fetchone()[0] == "redacted"
    assert c.execute("SELECT status FROM documents WHERE doc_id=?", (rid,)).fetchone()[0] == "redacted"
    assert pipe.publisher.of("evidence_event")[-1]["reason"] == "redaction"
    # a version older than the redaction cannot bring the text back; a newer one shown by the source can
    append_jsonl(chat, [{"type": "edit", "id": "m1", "author": "ana@acme.com", "created_at": "2026-03-04T10:00:00Z", "updated_at": "2026-03-04T12:00:00Z",
                         "text": "Older kumquat text that predates the redaction."},
                        {"type": "edit", "id": "m1", "author": "ana@acme.com", "created_at": "2026-03-04T10:00:00Z", "updated_at": "2026-03-06T00:00:00Z",
                         "text": "Republished note: the checkout deploy was fixed by the pomelo patch."}])
    await pipe.sync(a["connector_id"])
    out = await pipe.process_available()
    assert out.outcomes["historical"] == 1 and out.outcomes["update"] == 1
    assert table_contains(store, "kumquat") == [] and [h["doc_id"] for h in await store.search("pomelo patch")] == [rid]
    assert c.execute("SELECT deletion_status FROM ingest_records WHERE record_id=?", (rid,)).fetchone()[0] == "live"
    assert c.execute("SELECT COUNT(*) FROM deletion_tombstones WHERE record_id=?", (rid,)).fetchone()[0] == 0
    await store.close()


async def test_deletion_publishes_a_signed_evidence_event_over_the_holder_transport(tmp_path: Path, db) -> None:
    from mycelic.holder import HolderService
    from mycelic.transport.base import Subjects
    from mycelic.transport.sqlite_transport import SqliteTransport

    from .test_holder import collect, wait_for

    transport = SqliteTransport(db, retention_seconds=3600)
    await transport.start()
    store, pipe, _svc, a, _b, chat, _ = await two_apps(tmp_path)
    svc = HolderService(store, transport, holder_id=store.holder_id, tenant_id=TENANT, route_key="rk-test", heartbeat_interval=1000.0)
    pipe.publisher = svc
    events = await collect(transport, Subjects.evidence_events(TENANT))
    results = await collect(transport, Subjects.ingest_results(TENANT))
    await sync_all(pipe, a)
    await store.answer_question(question("Which cluster had gateway timeouts after the library was upgraded?"))
    rid = store.store._conn.execute("SELECT record_id FROM ingest_records WHERE source_object_id='m2'").fetchone()[0]
    refs = {r[0] for r in store.store._conn.execute("SELECT ref_id FROM exports WHERE doc_id=?", (rid,))}
    append_jsonl(chat, [{"type": "delete", "id": "m2", "deleted_at": "2026-03-08T12:00:00Z"}])
    await sync_all(pipe, a)
    await wait_for(lambda: len(events.items) == 1 and len(results.items) >= 1)
    env = events.items[0]
    assert env.kind == "evidence_event" and env.verify("rk-test") and env.payload["holder_id"] == store.holder_id
    assert env.payload["event"] == "deleted" and refs and set(env.payload["affected_ref_ids"]) == refs
    assert results.items[0].kind == "ingest_result" and results.items[0].payload["document"] is None
    await transport.close()
    await store.close()


# ---------------------------------------------------------------------------------------------- 12. one root across apps
async def test_the_same_text_through_two_apps_shares_one_source_root(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    text = "Renewal risk: Globex will not renew unless the checkout timeouts are fixed by the end of the quarter."
    chat = write_jsonl(tmp_path / "sales.jsonl", [{"type": "message", "id": "s1", "author": "cy@acme.com", "created_at": "2026-04-01T09:00:00Z",
                                                   "text": text}], header={"id": "sales", "source_type": "channel", "visibility": "public"})
    mail = write_jsonl(tmp_path / "mail.jsonl", [
        # reformatted copy (case, whitespace) in a second app
        {"type": "message", "id": "e1", "author": "dee@acme.com", "created_at": "2026-04-02T09:00:00Z", "title": "fwd",
         "text": "  " + text.upper().replace(" ", "   ") + "\n"},
        # a pure forward: almost no own text, the forwarded block is the content
        {"type": "message", "id": "e2", "author": "dee@acme.com", "created_at": "2026-04-03T09:00:00Z", "title": "Fwd: renewal",
         "text": "FYI\n\n---------- Forwarded message ---------\nFrom: Cy <cy@acme.com>\nSubject: renewal\n\n" + text},
        # an independent statement gets its own root
        {"type": "message", "id": "e3", "author": "dee@acme.com", "created_at": "2026-04-04T09:00:00Z", "text": "Globex asked for a discount."},
    ], header={"id": "inbox", "source_type": "mailbox", "visibility": "public"})
    a = await connect_export(svc, [chat], source_app="teamchat")
    b = await connect_export(svc, [mail], source_app="mailexport")
    await sync_all(pipe, a, b)
    roots = {r["source_object_id"]: (r["source_root_id"], r["root_method"]) for r in store.store._conn.execute(
        "SELECT source_object_id, source_root_id, root_method FROM ingest_records")}
    assert roots["s1"][0] == roots["e1"][0] == roots["e2"][0] and roots["e2"][1] == "pure_copy"
    assert roots["e3"][0] != roots["s1"][0]
    doc_roots = {r[0] for r in store.store._conn.execute(
        "SELECT d.source_root_id FROM documents d JOIN ingest_records r ON r.record_id=d.doc_id WHERE r.source_object_id IN ('s1','e1','e2')")}
    assert doc_roots == {roots["s1"][0]}
    resp = await store.answer_question(question("Will Globex renew unless the checkout timeouts are fixed?"))
    used = [r for r in resp["evidence_refs"] if r["source_root_id"] == roots["s1"][0]]
    assert len(used) >= 2 and {r["meta"]["source_app"] for r in used} == {"teamchat", "mailexport"}   # copies count once downstream
    await store.close()


# ---------------------------------------------------------------------------------------------- 14. crash and resume
class CrashingExport(LocalExportConnector):
    manifest = ConnectorManifest(**{**LocalExportConnector.manifest.__dict__, "connector_type": "crashy_export"})
    crash_after: int | None = None
    fetched: list[int] = []

    async def incremental_sync(self, ctx, source, cursor):
        n = 0
        async for page in super().incremental_sync(ctx, source, cursor):
            if self.crash_after is not None and n >= self.crash_after:
                raise RuntimeError("simulated crash")
            type(self).fetched.append(page.items[0].extra["position"])
            n += 1
            yield page


async def test_a_crash_mid_pipeline_resumes_from_the_checkpoint_without_duplicates(tmp_path: Path) -> None:
    reg = register_builtin(ConnectorRegistry())
    reg.register(CrashingExport)
    store = make_store(tmp_path)
    pipe = make_pipeline(store, registry=reg)
    svc = IngestService(pipe)
    records = [{"type": "message", "id": f"r{i}", "author": "ana@acme.com", "created_at": f"2026-05-01T10:{i:02d}:00Z",
                "text": f"Line {i}: deployment note number {i} about the walrus rollout."} for i in range(25)]
    f = write_jsonl(tmp_path / "big.jsonl", records, header={"id": "big", "source_type": "channel", "visibility": "public"})
    con = await svc.add_connector("crashy_export", created_by=OWNER,
                                  config={"source_app": "teamchat", "account_id": "acme", "paths": [str(f)], "auto_include": True,
                                          "limits": {"page_size": 3}})
    await svc.discover_sources(con["connector_id"])
    CrashingExport.crash_after, CrashingExport.fetched = 3, []
    first = await pipe.sync(con["connector_id"])
    assert first.error_code == "connector_crash" and first.pages == 3
    stream = "incr:big"
    _cur, v1 = await pipe.queue.load_checkpoint(con["connector_id"], stream)
    assert v1 == 3
    # the work committed before the crash is processed; nothing partial exists
    await pipe.process_available()
    assert store.store._conn.execute("SELECT COUNT(*) FROM ingest_records").fetchone()[0] == 9
    # a stale (zombie) runner holding version 2 cannot commit a page
    with pytest.raises(StaleCheckpoint):
        await pipe.queue.commit_page(con["connector_id"], stream, [], None, priority_class="live", expected_version=2)
    CrashingExport.crash_after = None
    second = await pipe.sync(con["connector_id"])
    assert second.error_code is None
    _cur, v2 = await pipe.queue.load_checkpoint(con["connector_id"], stream)
    assert v2 > v1                                                    # monotonic
    await pipe.process_available()
    ids = [r[0] for r in store.store._conn.execute("SELECT source_object_id FROM ingest_records ORDER BY source_object_id")]
    assert sorted(ids) == sorted(f"r{i}" for i in range(25)) and len(ids) == len(set(ids))
    assert store.store._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 25
    assert max(CrashingExport.fetched.count(p) for p in set(CrashingExport.fetched)) <= 2   # no page fetched more than twice
    await store.close()


async def test_a_failure_inside_the_write_transaction_leaves_no_partial_record(tmp_path: Path, monkeypatch) -> None:
    store, pipe, _svc, a, _b, *_ = await two_apps(tmp_path)
    await pipe.sync(a["connector_id"])
    calls = {"n": 0}
    real = IngestStore.note_batch_sync

    def flaky(c, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash after the document rows were written")
        return real(c, **kw)
    monkeypatch.setattr(IngestStore, "note_batch_sync", staticmethod(flaky))
    first = await pipe.process_available()
    assert first.outcomes["retry"] == 1 and first.outcomes["new"] == 1
    c = store.store._conn
    assert c.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1          # the failed record left nothing behind
    assert c.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == c.execute(
        "SELECT COUNT(*) FROM record_memories").fetchone()[0]
    c.execute("UPDATE ingest_queue SET available_at='2000-01-01T00:00:00.000000+00:00' WHERE status='queued'")
    second = await pipe.process_available()
    assert second.outcomes["new"] == 1 and c.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
    await store.close()


# ---------------------------------------------------------------------------------------------- connector failures are contained
class FlakyExport(LocalExportConnector):
    manifest = ConnectorManifest(**{**LocalExportConnector.manifest.__dict__, "connector_type": "flaky_export"})

    def normalize(self, raw, ctx):
        if isinstance(raw.payload, dict) and raw.payload.get("id") == "bad":
            raise ValueError("parser bug")
        evs = super().normalize(raw, ctx)
        if isinstance(raw.payload, dict) and raw.payload.get("id") == "alien":
            return [e.with_(holder_id="hold_somebody_else") for e in evs]     # tries to write into another holder
        return evs


async def test_connector_failures_never_corrupt_the_holder(tmp_path: Path) -> None:
    reg = register_builtin(ConnectorRegistry())
    reg.register(FlakyExport)
    store = make_store(tmp_path)
    pipe = make_pipeline(store, registry=reg)
    svc = IngestService(pipe)
    f = tmp_path / "mixed.jsonl"
    f.write_text("\n".join([json.dumps({"type": "source", "id": "mixed", "source_type": "channel", "visibility": "public"}),
                            json.dumps({"type": "message", "id": "ok1", "author": "a", "created_at": "2026-01-01T00:00:00Z", "text": "First good line"}),
                            "{not json",
                            json.dumps({"type": "message", "id": "bad", "author": "a", "created_at": "2026-01-01T00:01:00Z", "text": "Breaks"}),
                            json.dumps({"type": "message", "id": "alien", "author": "a", "created_at": "2026-01-01T00:02:00Z", "text": "Wrong holder"}),
                            json.dumps({"type": "message", "id": "empty", "author": "a", "created_at": "2026-01-01T00:03:00Z", "text": ""}),
                            json.dumps({"type": "message", "id": "ok2", "author": "a", "created_at": "2026-01-01T00:04:00Z", "text": "Second good line"})]) + "\n")
    con = await svc.add_connector("flaky_export", created_by=OWNER, config={"source_app": "teamchat", "paths": [str(f)], "auto_include": True})
    await svc.discover_sources(con["connector_id"])
    report = await pipe.sync(con["connector_id"])
    assert report.normalize_errors == 4 and report.enqueued == 2
    await pipe.process_available()
    c = store.store._conn
    assert sorted(r[0] for r in c.execute("SELECT source_object_id FROM ingest_records")) == ["ok1", "ok2"]
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    # every record has its document and its chunk memories; no memory without a document
    assert c.execute("SELECT COUNT(*) FROM ingest_records r LEFT JOIN documents d ON d.doc_id=r.record_id WHERE d.doc_id IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM memories m LEFT JOIN documents d ON d.doc_id=m.chat_id WHERE d.doc_id IS NULL").fetchone()[0] == 0
    assert table_contains(store, "Wrong holder") == [] and table_contains(store, "Breaks") == []
    await store.close()


# ---------------------------------------------------------------------------------------------- metering
async def test_every_stage_is_metered_separately(tmp_path: Path) -> None:
    store, pipe, _svc, a, b, *_ = await two_apps(tmp_path)
    await sync_all(pipe, a, b)
    stages = {m["stage"] for m in pipe.db.metrics()}
    assert {"fetch", "admit", "normalize", "redact", "enqueue", "dedupe", "classify", "route", "write", "publish"} <= stages
    blob = json.dumps(pipe.db.metrics())
    assert "payment" not in blob and "deploys" not in blob          # counts and codes only
    await store.close()


async def test_heartbeat_stats_carry_ingest_counts_only(tmp_path: Path) -> None:
    from mycelic.holder.embedded import _heartbeat_stats
    store, pipe, _svc, a, b, *_ = await two_apps(tmp_path)
    await sync_all(pipe, a, b)
    hb = _heartbeat_stats(await store.stats())
    assert hb["ingest"]["records"] == 3 and hb["ingest"]["by_app"] == {"teamchat": 2, "wiki": 1}
    assert "gateway" not in json.dumps(hb)
    await store.close()


async def test_publish_failures_keep_envelopes_in_the_outbox(tmp_path: Path) -> None:
    store, pipe, _svc, a, _b, *_ = await two_apps(tmp_path)
    pipe.publisher.fail = True
    await sync_all(pipe, a)
    assert pipe.db.pending_outbox()
    pipe.publisher.fail = False
    assert await pipe.publish_pending() >= 1 and pipe.db.pending_outbox() == []
    await store.close()
