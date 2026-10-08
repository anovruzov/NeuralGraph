"""Per-holder domain sharding (docs/mycelic/INGESTION.md §7; product decision 1).

One store per holder; a domain subtree is split into its own file only when thresholds trip. Covered here: threshold
detection and recommendations, the split migration (copy, catch-up, cutover, cleanup) with no loss, no duplicates and a
crash at every state, sticky routing, bounded fan-out retrieval with RRF (and the unchanged single-shard path), the audience
filter per shard, traversal across shards, the intent log between a shard commit and its control commit, backup and
restore with deletion replay, and tenant isolation of shard files. Offline and deterministic (hash embeddings).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from mycelic.evidence import EvidenceStore
from mycelic.ingest.fanout import ShardedRetriever
from mycelic.ingest.reshard import MigrationInterrupted, SplitMigration, SplitRefused
from mycelic.ingest.service import IngestService
from mycelic.ingest.shard_backup import backup_shards, ledger_key, restore_shards
from mycelic.ingest.shards import (DEFAULT_SHARD, ShardPathError, ShardRouter, ShardSpec, ShardStats, SplitThresholds, recommend_splits,
                                   signals_for)
from mycelic.tests.ingest_support import OWNER, TENANT, append_jsonl, connect_export, make_pipeline, question, write_jsonl

HOLDER = "hold_h1"
PUBLIC = {"principal_ids": ["usr_someone"], "complete": True, "owner": False}
OWNER_AUDIENCE = {"principal_ids": [OWNER], "complete": True, "owner": True}

TEXTS = {
    "engineering": ["The checkout service deploy pipeline failed on the build step after the merge",
                    "httpclient upgrade caused a timeout regression in the payments service",
                    "We refactored the cache layer of the api gateway to cut latency",
                    "Code review backlog for the search indexer keeps growing every sprint"],
    "sales": ["The Acme renewal deal is at risk because of the pricing change",
              "Pipeline review: three opportunities moved to negotiation this week",
              "Discount approval requested for the Globex quote before quarter end",
              "Churn risk flagged for the Initech account after the support escalation"],
    "legal": ["The NDA with Umbrella needs a new confidentiality clause before signing",
              "GDPR data processing agreement review for our cloud vendors",
              "Contract renewal terms with Hooli were redlined by counsel",
              "Counsel reviewed the trademark dispute with a competitor"],
}
QUERIES = ["checkout deploy pipeline failed", "renewal deal pricing risk", "NDA confidentiality clause", "timeout regression payments",
           "trademark dispute counsel", "cache layer api gateway latency", "discount approval quote", "GDPR processing agreement vendors",
           "code review backlog indexer", "churn risk account escalation", "opportunities negotiation pipeline review", "contract renewal redlined"]


def token(dom: str, i: int) -> str:
    return f"zq{dom[:3]}{i:03d}x"


def records(dom: str, n: int) -> list[dict[str, Any]]:
    base = TEXTS[dom]
    return [{"type": "message", "id": f"{dom}-{i}", "text": f"{base[i % len(base)]}. Item {token(dom, i)} for {dom}.",
             "created_at": f"2026-03-{(i % 27) + 1:02d}T10:{i % 60:02d}:00Z", "author": "ana@acme.com"} for i in range(n)]


async def build(tmp_path: Path, *, n: int = 12, extra_sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One holder fed by three public apps (one per domain) through the real pipeline."""
    store = EvidenceStore(tmp_path / "evidence.db", holder_id=HOLDER, tenant_id=TENANT, owner_ids=[OWNER])
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    files: dict[str, Path] = {}
    for dom in TEXTS:
        files[dom] = write_jsonl(tmp_path / f"{dom}.jsonl", records(dom, n),
                                 header={"id": dom, "name": dom, "source_type": "channel", "visibility": "public", "domains": [dom]})
        await connect_export(svc, [files[dom]], source_app=f"app-{dom}", account_id=dom)
    for src in extra_sources or []:
        files[src["id"]] = write_jsonl(tmp_path / f"{src['id']}.jsonl", src["records"], header=src["header"])
        await connect_export(svc, [files[src["id"]]], source_app=src["app"], account_id=src["id"], **src.get("config", {}))
    await sync(pipe)
    return {"store": store, "pipe": pipe, "svc": svc, "files": files, "tmp": tmp_path}


async def sync(pipe) -> Any:
    for con in pipe.db.list_connectors():
        await pipe.sync(con["connector_id"])
    return await pipe.process_available()


def shard_conns(store: EvidenceStore) -> dict[str, sqlite3.Connection]:
    return {spec.shard_id: st.store._conn for spec, st in store.shards.open_stores(statuses=("active", "draining", "readonly"))}


def snapshot(store: EvidenceStore) -> dict[str, dict[str, Any]]:
    """Per record, wherever it lives: its live state (content hash, memory ids, message count, memberships)."""
    out: dict[str, dict[str, Any]] = {}
    for sid, c in shard_conns(store).items():
        for r in c.execute("SELECT record_id, content_hash, deletion_status, primary_domain_id FROM ingest_records"):
            loc = store.store._conn.execute("SELECT shard_id FROM record_locator WHERE record_id=?", (r["record_id"],)).fetchone()
            if loc is not None and loc["shard_id"] != sid:
                continue                                   # a stale source copy (none should remain after cleanup)
            assert r["record_id"] not in out, f"{r['record_id']} is live in two shards"
            mems = sorted(x[0] for x in c.execute("SELECT memory_id FROM memories WHERE chat_id=? AND status='active'", (r["record_id"],)))
            doc = c.execute("SELECT status, version, text FROM documents WHERE doc_id=?", (r["record_id"],)).fetchone()
            out[r["record_id"]] = {"shard": sid, "hash": r["content_hash"], "status": r["deletion_status"], "domain": r["primary_domain_id"],
                                   "memories": mems, "doc": (doc["status"], doc["version"], doc["text"]) if doc else None,
                                   "messages": int(c.execute("SELECT COUNT(*) FROM messages WHERE json_extract(metadata, '$.doc_id')=?", (r["record_id"],)).fetchone()[0]),
                                   "memberships": sorted(x[0] for x in c.execute("SELECT domain_id FROM domain_memberships WHERE record_id=? AND status='active'",
                                                                                 (r["record_id"],)))}
    return out


def assert_no_rows_left(conn: sqlite3.Connection, record_ids: list[str], memory_ids: list[str]) -> None:
    """A moved record leaves nothing in its source shard (content, catalog rows, FTS tokens)."""
    for rid in record_ids:
        for table, col in (("documents", "doc_id"), ("document_versions", "doc_id"), ("ingest_records", "record_id"), ("record_memories", "record_id"),
                           ("record_entities", "record_id"), ("relation_evidence", "record_id"), ("domain_memberships", "record_id"),
                           ("ingest_versions", "record_id"), ("memories", "chat_id"), ("messages", "chat_id")):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (rid,)).fetchone()[0] == 0, (table, rid)
    for part in [memory_ids[i:i + 500] for i in range(0, len(memory_ids), 500)]:
        assert conn.execute(f"SELECT COUNT(*) FROM memories WHERE memory_id IN ({','.join('?' * len(part))})", part).fetchone()[0] == 0


def fts_hits(conn: sqlite3.Connection, tok: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?", (tok,)).fetchone()[0]) + \
        int(conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH ?", (tok,)).fetchone()[0])


def check_invariants(store: EvidenceStore, before: dict[str, dict[str, Any]], *, moved_domain: str = "engineering") -> dict[str, dict[str, Any]]:
    after = snapshot(store)
    assert set(after) == set(before), "a record was lost or duplicated"
    for rid, b in before.items():
        a = after[rid]
        assert (a["hash"], a["status"], a["memories"], a["doc"], a["messages"], a["memberships"]) == \
               (b["hash"], b["status"], b["memories"], b["doc"], b["messages"], b["memberships"]), rid
        loc = store.store._conn.execute("SELECT shard_id FROM record_locator WHERE record_id=?", (rid,)).fetchone()["shard_id"]
        assert loc == a["shard"]
        assert (loc != DEFAULT_SHARD) == (a["domain"] == moved_domain or (a["domain"] or "").startswith(moved_domain + ".")), rid
    moved = [rid for rid, a in after.items() if a["shard"] != DEFAULT_SHARD]
    assert moved
    assert_no_rows_left(store.store._conn, moved, [m for rid in moved for m in after[rid]["memories"]])
    assert not store.store._conn.execute("SELECT 1 FROM shard_control_intents LIMIT 1").fetchone()
    return after


# ================================================================================================ thresholds (§7.3, §7.9)
def test_signals_and_recommendation_pick_the_subtree_that_moves_most_load():
    from mycelic.ingest.domains import default_taxonomy
    tax = default_taxonomy()
    th = SplitThresholds(active_memories=100, matrix_bytes=10_000, sustain_seconds=0)
    st = ShardStats(shard_id="s0", active_memories=150, matrix_bytes=9_000, file_bytes=10)
    assert signals_for(st, th) == {"active_memories": 150.0}
    st.write_p95_ms, st.live_lag_seconds = 300.0, 10.0
    assert "write_p95" not in signals_for(st, th)                     # slow writes count only while live work lags
    st.live_lag_seconds = 900.0
    assert "write_p95" in signals_for(st, th)
    loads = {"s0": {"engineering.dependencies": {"records": 10, "memories": 60, "matrix_bytes": 600},
                    "engineering": {"records": 5, "memories": 30, "matrix_bytes": 300},
                    "sales.renewals": {"records": 20, "memories": 40, "matrix_bytes": 4000},
                    "unclassified": {"records": 99, "memories": 999, "matrix_bytes": 9999}}}
    specs = {"s0": ShardSpec("s0", 0, "evidence.db")}
    rec = recommend_splits({"s0": st}, {"s0": {"active_memories": 150.0}}, loads, specs, tax, thresholds=th)
    assert rec[0]["action"] == "split" and rec[0]["domain_ids"] == ["engineering"] and rec[0]["moves"]["memories"] == 90
    # a vector-matrix signal weighs vector bytes instead: sales moves more of the matrix
    rec = recommend_splits({"s0": st}, {"s0": {"matrix_bytes": 12_000.0}}, loads, specs, tax, thresholds=th)
    assert rec[0]["domain_ids"] == ["sales"]
    # a domain shard is split below its own root; a single subtree that is the whole load needs a time split instead
    specs["shd_aaaaaaaaaaaaaaaa"] = ShardSpec("shd_aaaaaaaaaaaaaaaa", 1, "shd_aaaaaaaaaaaaaaaa.db", ("engineering",))
    loads["shd_aaaaaaaaaaaaaaaa"] = {"engineering.dependencies": {"records": 10, "memories": 60, "matrix_bytes": 600}}
    rec = recommend_splits({}, {"shd_aaaaaaaaaaaaaaaa": {"active_memories": 1.0}}, loads, specs, tax, thresholds=th)
    assert rec[0]["domain_ids"] == ["engineering.dependencies"] and rec[0]["action"] == "split_by_time"


async def test_forced_thresholds_trip_after_the_sustain_window_and_recommend_a_domain(tmp_path):
    h = await build(tmp_path, n=8)
    store = h["store"]
    store.shards.thresholds = SplitThresholds(active_memories=10, sustain_seconds=3600)
    t0 = time.time()
    stats = await store.shards.collect(now=t0, force=True)
    assert stats["s0"].active_memories == 24 and stats["s0"].matrix_bytes == 24 * 256 * 4 and stats["s0"].records == 24
    assert store.shards.recommendations(now=t0) == []                  # tripped, not yet sustained
    assert store.shards.health("s0") == "hot"
    await store.shards.collect(now=t0 + 1800, force=True)
    recs = store.shards.recommendations(now=t0 + 3700)
    assert len(recs) == 1 and recs[0]["shard_id"] == "s0" and recs[0]["action"] == "split" and recs[0]["signals"] == {"active_memories": 24.0}
    assert recs[0]["domain_ids"][0] in ("engineering", "sales", "legal") and recs[0]["moves"]["memories"] >= 8
    # the signal clears when the load is gone
    store.shards.thresholds.active_memories = 10_000
    await store.shards.collect(now=t0 + 4000, force=True)
    assert store.shards.recommendations(now=t0 + 9000) == [] and store.shards.health("s0") == "ok"
    report = await store.shards.report(collect=False)
    assert report["items"][0]["shard_id"] == "s0" and report["items"][0]["stats"]["active_memories"] == 24
    await store.close()


# ================================================================================================ split migration (§7.7)
async def test_split_moves_the_subtree_with_no_loss_and_no_duplicates(tmp_path):
    h = await build(tmp_path, n=12)
    store = h["store"]
    c0 = store.store._conn
    before = snapshot(store)
    eng = [rid for rid, b in before.items() if b["domain"] == "engineering"]
    assert len(eng) == 12
    # a reference exported before the split keeps resolving after it
    ans = await store.answer_question(question("checkout deploy pipeline failed build", audience=OWNER_AUDIENCE))
    assert ans["status"] == "answered"
    ref = ans["evidence_refs"][0]["ref_id"]
    raw_before = await store.raw_for_ref(ref, audience=OWNER_AUDIENCE)
    mig = await store.shards.start_split(["engineering"], requested_by=OWNER, batch_records=5)
    assert mig["state"] == "done" and mig["checkpoint"]["moved"] == 12 and mig["checkpoint"]["cleaned"] == 12
    target = mig["to_shard"]
    after = check_invariants(store, before)
    assert {after[r]["shard"] for r in eng} == {target}
    tc = store.shards.store(target).store._conn
    assert fts_hits(c0, token("engineering", 3)) == 0 and fts_hits(tc, token("engineering", 3)) >= 1
    assert fts_hits(c0, token("sales", 3)) >= 1
    # the new file: auto_vacuum incremental, full holder schema, nothing owed to s0
    assert tc.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert tc.execute("SELECT MAX(version) FROM holder_schema_migrations").fetchone()[0] == c0.execute("SELECT MAX(version) FROM holder_schema_migrations").fetchone()[0]
    counts = {(r[0], r[1]): r[2] for r in c0.execute("SELECT domain_id, shard_id, records FROM domain_shard_counts")}
    assert counts[("engineering", target)] == 12 and counts.get(("engineering", "s0"), 0) == 0
    # exports stayed in s0 and resolve through the locator
    raw_after = await store.raw_for_ref(ref, audience=OWNER_AUDIENCE)
    assert raw_after["text"] == raw_before["text"] and raw_after["doc_id"] == raw_before["doc_id"]
    # entities and edges follow the records: the target knows the author, s0 still does too (sales and legal records)
    assert tc.execute("SELECT 1 FROM entities WHERE entity_id='person:ana@acme.com'").fetchone()
    # the source's split is idempotent: running the finished migration again changes nothing
    assert (await SplitMigration.load(store.shards, mig["migration_id"]).run())["state"] == "done"
    assert snapshot(store) == after
    await store.close()


async def test_split_refuses_overlaps_unknown_domains_and_a_second_concurrent_split(tmp_path):
    h = await build(tmp_path, n=4)
    store = h["store"]
    with pytest.raises(SplitRefused) as e:
        await store.shards.start_split(["no-such-domain"], requested_by=OWNER)
    assert e.value.code == "unknown_domain"
    with pytest.raises(SplitRefused):
        await store.shards.start_split(["engineering", "engineering.dependencies"], requested_by=OWNER)
    mig = await SplitMigration.plan(store.shards, ["engineering"], requested_by=OWNER)
    with pytest.raises(SplitRefused) as e:
        await store.shards.start_split(["sales"], requested_by=OWNER)
    assert e.value.code == "busy"
    await mig.run()
    with pytest.raises(SplitRefused) as e:
        await store.shards.start_split(["engineering.dependencies"], requested_by=OWNER)
    assert e.value.code == "overlap"
    # an aborted split (before cutover) leaves no file and no routing behind
    m2 = await SplitMigration.plan(store.shards, ["sales"], requested_by=OWNER)
    with pytest.raises(MigrationInterrupted):
        await m2.run(crash_at="copying:batch")
    target = m2.row()["to_shard"]
    assert (tmp_path / f"{target}.db").exists()
    assert (await m2.abort())["state"] == "aborted"
    assert not (tmp_path / f"{target}.db").exists()
    assert store.shards.spec(target).status == "retired"
    assert store.store._conn.execute("SELECT COUNT(*) FROM record_locator WHERE shard_id=?", (target,)).fetchone()[0] == 0
    await store.close()


async def test_writes_during_the_copy_are_caught_up_and_deletions_win(tmp_path):
    h = await build(tmp_path, n=12)
    store, pipe, svc = h["store"], h["pipe"], h["svc"]
    mig = await SplitMigration.plan(store.shards, ["engineering"], requested_by=OWNER)
    with pytest.raises(MigrationInterrupted):
        await mig.run(crash_at="copying:batch", batch_records=4)
    # meanwhile the source keeps receiving writes (routing is sticky until cutover): an edit, a new record, a deletion
    append_jsonl(h["files"]["engineering"], [
        {"type": "edit", "id": "engineering-1", "text": "httpclient upgrade caused a timeout regression; fixed by pinning 4.1. Item zqedit001x.",
         "created_at": "2026-03-02T10:01:00Z", "updated_at": "2026-04-02T10:00:00Z", "author": "ana@acme.com"},
        {"type": "message", "id": "engineering-new", "text": "A brand new deploy failure of the checkout service. Item zqnew0001x.",
         "created_at": "2026-04-03T10:00:00Z", "author": "ana@acme.com"},
        {"type": "delete", "id": "engineering-2", "deleted_at": "2026-04-04T10:00:00Z"}])
    rep = await sync(pipe)
    assert rep.outcomes["update"] == 1 and rep.outcomes["new"] == 1 and rep.outcomes["delete"] == 1
    loc = {r["record_id"]: r["shard_id"] for r in store.store._conn.execute("SELECT record_id, shard_id FROM record_locator")}
    assert set(loc.values()) == {DEFAULT_SHARD}                         # still in the source before cutover
    before = snapshot(store)
    done = await SplitMigration.load(store.shards, mig.migration_id).run(batch_records=4)
    assert done["state"] == "done"
    after = check_invariants(store, before)
    target = done["to_shard"]
    rid_edit = next(r for r in after if after[r]["doc"] and "zqedit001x" in after[r]["doc"][2])
    rid_new = next(r for r in after if after[r]["doc"] and "zqnew0001x" in after[r]["doc"][2])
    assert after[rid_edit]["shard"] == target and after[rid_edit]["doc"][1] == 2
    assert after[rid_new]["shard"] == target
    deleted = [r for r, a in after.items() if a["status"] == "purged"]
    assert len(deleted) == 1 and after[deleted[0]]["shard"] == target and after[deleted[0]]["doc"][0] == "deleted" and after[deleted[0]]["memories"] == []
    tc = store.shards.store(target).store._conn
    assert fts_hits(tc, token("engineering", 2)) == 0 and fts_hits(store.store._conn, token("engineering", 2)) == 0
    tomb = store.store._conn.execute("SELECT shard_id FROM deletion_tombstones WHERE record_id=?", (deleted[0],)).fetchone()
    assert tomb is not None and tomb["shard_id"] == target
    # a late event for the deleted record cannot resurrect it in its new shard
    append_jsonl(h["files"]["engineering"], [{"type": "edit", "id": "engineering-2", "text": "resurrected? zqghost01x", "created_at": "2026-03-03T10:00:00Z",
                                               "updated_at": "2026-05-01T10:00:00Z", "author": "ana@acme.com"}])
    await sync(pipe)
    assert fts_hits(tc, "zqghost01x") == 0 and not (await store.search("resurrected zqghost01x", k=5, audience=OWNER_AUDIENCE))
    await store.close()


CRASH_POINTS = ["planned", "provisioning", "copying:batch", "copying", "catching_up:batch", "catching_up", "cutover:start", "cutover:batch", "cutover",
                "cutover:switched", "cleanup:batch", "cleanup"]


@pytest.mark.parametrize("point", CRASH_POINTS)
async def test_a_crash_at_any_state_resumes_to_the_same_result(tmp_path, point):
    h = await build(tmp_path, n=10)
    store, pipe = h["store"], h["pipe"]
    before = snapshot(store)
    mig = await SplitMigration.plan(store.shards, ["engineering"], requested_by=OWNER)
    if point.startswith(("catching_up", "cutover:batch")):
        # give the catch-up and the final delta something to re-copy: a source edit after the copy started
        with pytest.raises(MigrationInterrupted):
            await mig.run(crash_at="copying", batch_records=3)
        append_jsonl(h["files"]["engineering"], [{"type": "edit", "id": "engineering-4", "text": "Edited during the copy. Item zqedit004x.",
                                                   "created_at": "2026-03-05T10:04:00Z", "updated_at": "2026-04-05T10:00:00Z", "author": "ana@acme.com"}])
        await sync(pipe)
        before = snapshot(store)
        if point.startswith("cutover"):
            with pytest.raises(MigrationInterrupted):
                await SplitMigration.load(store.shards, mig.migration_id).run(crash_at="cutover:start", batch_records=3)
            append_jsonl(h["files"]["engineering"], [{"type": "edit", "id": "engineering-5", "text": "Edited before cutover. Item zqedit005x.",
                                                       "created_at": "2026-03-06T10:05:00Z", "updated_at": "2026-04-06T10:00:00Z", "author": "ana@acme.com"}])
            await sync(pipe)
            before = snapshot(store)
    with pytest.raises(MigrationInterrupted):
        await SplitMigration.load(store.shards, mig.migration_id).run(crash_at=point, batch_records=3)
    # the process dies: every handle is dropped and the holder is opened again from its files
    await store.close()
    store = EvidenceStore(tmp_path / "evidence.db", holder_id=HOLDER, tenant_id=TENANT, owner_ids=[OWNER])
    done = await store.shards.resume_migrations(batch_records=3)
    assert [m["state"] for m in done] == ["done"]
    check_invariants(store, before)
    hits = await store.search("checkout deploy pipeline failed", k=5, audience=OWNER_AUDIENCE)
    eng = [x for x in hits if "checkout service deploy" in x["text"]]
    assert eng and {x["shard_id"] for x in eng} == {done[0]["to_shard"]} and not hits.partial
    await store.close()


async def test_routing_is_sticky_and_picks_the_most_specific_active_shard(tmp_path):
    h = await build(tmp_path, n=6)
    store, pipe = h["store"], h["pipe"]
    mig = await store.shards.start_split(["engineering"], requested_by=OWNER)
    eng_shard = mig["to_shard"]
    sub = await store.shards.start_split(["engineering.dependencies"], requested_by=OWNER, from_shard=eng_shard)
    dep_shard = sub["to_shard"]
    router = ShardRouter(store.shards, taxonomy=store.taxonomy())
    c = store.store._conn
    assert router.route_write_sync(c, "rk-new-1", primary_domain_id="engineering.dependencies") == dep_shard
    assert router.route_write_sync(c, "rk-new-2", primary_domain_id="engineering") == eng_shard
    assert router.route_write_sync(c, "rk-new-3", primary_domain_id="engineering.qa") == eng_shard
    assert router.route_write_sync(c, "rk-new-4", primary_domain_id="sales.renewals") == DEFAULT_SHARD
    assert router.route_write_sync(c, "rk-new-5", primary_domain_id=None) == DEFAULT_SHARD
    # an existing record stays where it is whatever its domain becomes (only a reshard moves data)
    rk, rid = c.execute("SELECT record_key, record_id FROM record_locator WHERE shard_id='s0' LIMIT 1").fetchone()
    assert router.route_write_sync(c, rk, primary_domain_id="engineering.dependencies") == DEFAULT_SHARD
    # a record re-labelled by its source keeps its shard; a domain correction too
    await h["svc"].correct_domains(rid, actor=OWNER, add=["engineering"], primary="engineering")
    assert store.shards.shard_of_record(rid) == DEFAULT_SHARD
    # new records of the subtree land in its shard through the pipeline
    append_jsonl(h["files"]["engineering"], [{"type": "message", "id": "engineering-late", "text": "Another deploy failure of checkout. zqlate001x",
                                               "created_at": "2026-04-07T10:00:00Z", "author": "ana@acme.com"}])
    await sync(pipe)
    new_rid = next(r for r, a in snapshot(store).items() if a["doc"] and "zqlate001x" in a["doc"][2])
    assert store.shards.shard_of_record(new_rid) == eng_shard
    # a shard being provisioned receives nothing yet
    plan = await SplitMigration.plan(store.shards, ["legal"], requested_by=OWNER)
    assert router.route_write_sync(c, "rk-new-6", primary_domain_id="legal") == DEFAULT_SHARD
    await plan.run()
    assert router.route_write_sync(c, "rk-new-6", primary_domain_id="legal") == plan.row()["to_shard"]
    await store.close()


# ================================================================================================ the intent log (§7.1)
async def test_a_crash_between_the_shard_commit_and_the_control_commit_is_repaired(tmp_path, monkeypatch):
    h = await build(tmp_path, n=4)
    store, pipe = h["store"], h["pipe"]
    target = (await store.shards.start_split(["engineering"], requested_by=OWNER))["to_shard"]
    shard_store = store.shards.store(target).store
    real = type(shard_store).apply_pending_intents
    calls = {"n": 0}

    async def dies_once(self):
        # the first time ops are actually owed, the process "dies" between the shard commit and the s0 commit
        if self._conn.execute("SELECT 1 FROM shard_control_intents").fetchone() is not None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("process died after the shard commit")
        return await real(self)
    monkeypatch.setattr(type(shard_store), "apply_pending_intents", dies_once)
    append_jsonl(h["files"]["engineering"], [{"type": "message", "id": "engineering-x", "text": "Deploy of checkout rolled back. zqintent1x",
                                               "created_at": "2026-04-08T10:00:00Z", "author": "ana@acme.com"}])
    for con in pipe.db.list_connectors():
        await pipe.sync(con["connector_id"])
    rep = await pipe.process_available()
    assert rep.outcomes.get("retry") == 1                       # the effect is in the shard, s0 has not heard of it yet
    tc = shard_store._conn
    rid = tc.execute("SELECT doc_id FROM documents WHERE text LIKE '%zqintent1x%'").fetchone()[0]
    assert tc.execute("SELECT COUNT(*) FROM shard_control_intents").fetchone()[0] == 1
    assert store.store._conn.execute("SELECT 1 FROM record_locator WHERE record_id=?", (rid,)).fetchone() is None
    # the next pass replays the owed ops first: locator, applied index, queue ack; the item is not written twice
    rep = await pipe.process_available()
    assert rep.processed == 0
    assert tc.execute("SELECT COUNT(*) FROM shard_control_intents").fetchone()[0] == 0
    assert store.store._conn.execute("SELECT shard_id FROM record_locator WHERE record_id=?", (rid,)).fetchone()[0] == target
    assert store.store._conn.execute("SELECT status FROM ingest_queue WHERE record_key=(SELECT record_key FROM record_locator WHERE record_id=?)",
                                     (rid,)).fetchone()[0] == "done"
    assert tc.execute("SELECT COUNT(*) FROM documents WHERE doc_id=?", (rid,)).fetchone()[0] == 1
    assert [x["doc_id"] for x in await store.search("checkout rolled back zqintent1x", k=3, audience=OWNER_AUDIENCE)][:1] == [rid]
    await store.close()


# ================================================================================================ fan-out retrieval (§7.4)
# Mean top-5 overlap with the single-store search of the same corpus (same memory ids, before and after the split). Plain
# shard-level RRF gives every shard's best hit the same rank weight, so the shards that hold weaker matches still place
# their top results in the merged top 5: on this domain-partitioned fixture it agrees at ~0.6 (stated threshold 0.5). The
# channel-level fusion (merge="channel") ranks like one store and agrees at ~0.95 (stated threshold 0.85). See
# research/ingest_bench/RESULTS.md for the same measurement at scale.
AGREEMENT_RRF = 0.5
AGREEMENT_CHANNEL = 0.85


async def agreement(store: EvidenceStore, single: dict[str, list[str]], *, k: int = 5) -> tuple[float, float]:
    overlaps, top1 = [], 0
    for q in QUERIES:
        res = await store.search(q, k=k, audience=OWNER_AUDIENCE)
        assert not res.partial and not res.truncated and len(res.shards) == 3
        got = [x["memory_id"] for x in res]
        overlaps.append(len(set(got) & set(single[q])) / k)
        top1 += int(single[q][0] in got)              # the single-store best hit survives the merge
    return sum(overlaps) / len(overlaps), top1 / len(QUERIES)


async def test_sharded_rrf_search_agrees_with_single_store_search(tmp_path):
    h = await build(tmp_path, n=24)
    store = h["store"]
    single = {q: [x["memory_id"] for x in await store.search(q, k=5, audience=OWNER_AUDIENCE)] for q in QUERIES}
    single_answers = {q: (await store.answer_question(question(q, audience=OWNER_AUDIENCE)))["status"] for q in QUERIES[:4]}
    await store.shards.start_split(["engineering"], requested_by=OWNER)
    await store.shards.start_split(["legal"], requested_by=OWNER)
    assert len(store.shards.queryable()) == 3 and store._multi()
    mean, top1 = await agreement(store, single)
    assert mean >= AGREEMENT_RRF and top1 >= 0.9, (mean, top1)
    store.fanout_merge = "channel"
    mean_c, top1_c = await agreement(store, single)
    assert mean_c >= AGREEMENT_CHANNEL and top1_c >= 0.9 and mean_c > mean, (mean_c, top1_c)
    store.fanout_merge = "rrf"
    for q in QUERIES[:4]:
        ans = await store.answer_question(question(q, audience=OWNER_AUDIENCE))
        assert ans["status"] == single_answers[q] and ans["provenance"]["shards"]["partial"] is False
    await store.close()


async def test_a_failing_or_slow_shard_marks_the_result_partial_and_the_bound_marks_it_truncated(tmp_path, monkeypatch):
    h = await build(tmp_path, n=6)
    store = h["store"]
    t_eng = (await store.shards.start_split(["engineering"], requested_by=OWNER))["to_shard"]
    await store.shards.start_split(["legal"], requested_by=OWNER)
    reader = store.shards.reader(t_eng)

    def slow(*a, **kw):
        time.sleep(0.4)
        return []
    monkeypatch.setattr(reader, "search_sync", slow)
    res = await ShardedRetriever(store.shards).search("renewal deal pricing", k=5, per_shard_timeout=0.1)
    assert res.partial and res.failed == {t_eng: "timeout"} and res and all(r.shard_id != t_eng for r in res)

    def broken(*a, **kw):
        raise sqlite3.DatabaseError("disk image is malformed")
    monkeypatch.setattr(reader, "search_sync", broken)
    out = await store.search("renewal deal pricing", k=5, audience=OWNER_AUDIENCE)
    assert out.partial and out.failed[t_eng] == "DatabaseError" and out
    ans = await store.answer_question(question("renewal deal pricing risk", audience=OWNER_AUDIENCE))
    assert ans["status"] == "answered" and ans["provenance"]["shards"]["partial"] is True
    monkeypatch.undo()
    cut = await ShardedRetriever(store.shards).search("renewal deal pricing", k=5, max_shards=2)
    assert cut.truncated and len(cut.shards) == 2 and not cut.partial
    await store.close()


async def test_one_shard_takes_the_single_store_path_with_identical_results(tmp_path, monkeypatch):
    h = await build(tmp_path, n=8)
    store = h["store"]

    async def never(*a, **kw):
        raise AssertionError("a single-shard holder never fans out")
    monkeypatch.setattr(ShardedRetriever, "search", never)
    for q in QUERIES[:6]:
        # what the store returned before sharding existed: the retriever over the whole file, active memories, top k
        raw = await store.retriever.search(q, k=10, since=None, until=None)
        expected = [EvidenceStore._result_dict(r) for r in [r for r in raw if r.memory.status == "active"][:5]]
        got = await store.search(q, k=5, audience=OWNER_AUDIENCE)
        assert type(got) is list and [g["memory_id"] for g in got] == [e["memory_id"] for e in expected]
        assert [(g["text"], g["doc_id"], g["channels"]) for g in got] == [(e["text"], e["doc_id"], e["channels"]) for e in expected]
    ans = await store.answer_question(question("checkout deploy pipeline failed", audience=OWNER_AUDIENCE))
    assert ans["status"] == "answered" and "shards" not in ans["provenance"]
    # a planned split (shard not yet active) does not change the read path either
    await SplitMigration.plan(store.shards, ["engineering"], requested_by=OWNER)
    assert store._sharded() and not store._multi()
    assert [g["memory_id"] for g in await store.search(QUERIES[0], k=5, audience=OWNER_AUDIENCE)]
    await store.close()


# ================================================================================================ ACL across shards
async def test_audience_filter_applies_in_every_shard(tmp_path):
    secret = [{"type": "message", "id": f"s{i}", "text": f"Private postmortem: the checkout deploy failed because of a leaked key zqsecret{i}x.",
               "created_at": f"2026-03-1{i}T10:00:00Z", "author": "u1"} for i in range(3)]
    h = await build(tmp_path, n=6, extra_sources=[{"id": "eng-private", "app": "chat", "records": secret, "config": {"principal_map": {"u1": OWNER}},
                                                   "header": {"id": "eng-private", "name": "#eng-private", "source_type": "channel", "visibility": "members",
                                                              "member_ids": ["u1"], "domains": ["engineering"]}}])
    store = h["store"]
    target = (await store.shards.start_split(["engineering"], requested_by=OWNER))["to_shard"]
    tc = store.shards.store(target).store._conn
    private_ids = {r[0] for r in tc.execute("SELECT record_id FROM ingest_records WHERE visibility='members'")}
    assert len(private_ids) == 3                                       # the members-only records moved with their domain
    q = "checkout deploy failed leaked key postmortem"
    outsider = await store.search(q, k=10, audience=PUBLIC)
    assert outsider and not {x["doc_id"] for x in outsider} & private_ids
    assert not any("zqsecret" in x["text"] for x in outsider)
    owner = await store.search(q, k=10, audience=OWNER_AUDIENCE)
    assert {x["doc_id"] for x in owner} & private_ids
    member = await store.search(q, k=10, audience={"principal_ids": [OWNER], "complete": True, "owner": False})
    assert {x["doc_id"] for x in member} & private_ids
    ans = await store.answer_question(question(q, audience=PUBLIC))
    assert "zqsecret" not in json.dumps(ans)
    # the shard's allow-set: active memories of the shard minus the restricted records'
    allowed = store._allowed_memory_ids_in(store.shards.store(target), store_audience(PUBLIC))
    blocked = {r[0] for r in tc.execute(f"SELECT memory_id FROM memories WHERE chat_id IN ({','.join('?' * len(private_ids))})", list(private_ids))}
    assert allowed is not None and not allowed & blocked and allowed
    # raw access through an exported reference re-checks the ACL in the record's shard
    own_ans = await store.answer_question(question(q, audience=OWNER_AUDIENCE))
    ref = next(r["ref_id"] for r in own_ans["evidence_refs"])
    raw = await store.raw_for_ref(ref, audience=PUBLIC)
    assert raw is not None and (raw.get("status") == "withheld" or "zqsecret" not in raw.get("text", ""))
    await store.close()


def store_audience(a: dict[str, Any]) -> Any:
    from mycelic.ingest.acl import Audience
    return Audience.from_payload(a)


# ================================================================================================ traversal (§7.5)
async def test_traversal_merges_edges_across_shards_and_follows_only_public_evidence(tmp_path):
    slack = [{"type": "message", "id": "m1", "text": "Deployment of checkout failed after the dependency upgrade to httpclient 4.2; requests time out.",
              "created_at": "2026-09-14T10:00:00+00:00", "author": "u1"}]
    gh = [{"type": "document", "id": "482", "title": "httpclient 4.2 introduced a timeout regression in checkout",
           "text": "httpclient 4.2 introduced a timeout regression in checkout. The default pool timeout dropped after the bump.",
           "created_at": "2026-09-15T09:00:00+00:00", "author": "u2"}]
    dm = [{"type": "message", "id": "p1", "text": "Between us: the checkout outage might be caused by the vendor sdk 2.0 upgrade.",
           "created_at": "2026-09-16T09:00:00+00:00", "author": "u1"}]
    h = await build(tmp_path, n=2, extra_sources=[
        {"id": "C-deploys", "app": "slack", "records": slack, "header": {"id": "C-deploys", "name": "#deploys", "source_type": "channel", "visibility": "public",
                                                                          "domains": ["infrastructure"]}},
        {"id": "acme-checkout", "app": "github", "records": gh, "header": {"id": "acme/checkout", "name": "acme/checkout", "source_type": "repo",
                                                                            "visibility": "public", "domains": ["engineering"]}},
        {"id": "dm-1", "app": "slackdm", "records": dm, "config": {"principal_map": {"u1": OWNER}},
         "header": {"id": "dm-1", "name": "dm", "source_type": "channel", "visibility": "members", "member_ids": ["u1"], "domains": ["engineering"]}}])
    store = h["store"]
    start = ["version:httpclient@4.2"]
    single = store.traverser().paths(start, max_depth=2, audience=PUBLIC)
    assert single["paths"]
    target = (await store.shards.start_split(["engineering"], requested_by=OWNER))["to_shard"]
    tc = store.shards.store(target).store._conn
    assert tc.execute("SELECT COUNT(*) FROM relation_evidence").fetchone()[0] > 0
    assert store.store._conn.execute("SELECT COUNT(*) FROM relation_evidence").fetchone()[0] > 0
    out = store.traverser().paths(start, max_depth=2, audience=PUBLIC)
    shards_seen = {s for p in out["paths"] for e in p["edges"] for s in e["shards"]}
    assert {DEFAULT_SHARD, target} <= shards_seen                      # one path, edges from both files
    assert {p["to"] for p in out["paths"]} == {p["to"] for p in single["paths"]}
    for p in out["paths"]:
        assert all(e["evidence"] >= 1 for e in p["edges"])               # every edge followed has public, non-negated evidence
        assert abs(p["weakest"]["confidence"] - min(e["confidence"] for e in p["edges"])) < 1e-9
    # an edge known only from the members-only DM is not traversable, for anyone (the public-evidence rule of §8.7)
    nodes = {p["to"] for p in out["paths"]} | {p["to"] for p in store.traverser().paths(["component:vendor-sdk", "version:sdk@2.0"], audience=OWNER_AUDIENCE)["paths"]}
    assert not any("sdk" in n for n in nodes)
    cut = store.traverser().paths(start, max_depth=3, max_edges=1, audience=PUBLIC)
    assert cut["truncated"] and cut["edges_traversed"] == 1
    await store.close()


# ================================================================================================ backup and restore (§7.8)
async def test_deletion_after_a_backup_survives_the_restore(tmp_path):
    h = await build(tmp_path, n=6)
    store, svc = h["store"], h["svc"]
    target = (await store.shards.start_split(["engineering"], requested_by=OWNER))["to_shard"]
    manifest = await backup_shards(store, tmp_path / "backup")
    assert {f["shard_id"] for f in manifest["files"]} == {DEFAULT_SHARD, target}
    assert all(r["last_backup_at"] for r in (await store.shards.report(collect=False))["items"] if r["status"] == "active")
    snap = snapshot(store)
    eng_rid = next(r for r, a in snap.items() if a["shard"] == target and a["doc"] and token("engineering", 1) in a["doc"][2])
    sales_rid = next(r for r, a in snap.items() if a["shard"] == DEFAULT_SHARD and a["doc"] and token("sales", 1) in a["doc"][2])
    eng_key = store.store._conn.execute("SELECT record_key FROM record_locator WHERE record_id=?", (eng_rid,)).fetchone()[0]
    await svc.delete_record(eng_rid, actor=OWNER)
    await svc.delete_record(sales_rid, actor=OWNER)
    await store.close()
    # restore the backup taken before the deletions: the live store's tombstones are replayed into the restored files
    out = await restore_shards(tmp_path / "backup", tmp_path / "evidence.db", holder_id=HOLDER, tenant_id=TENANT)
    assert out["replay"]["purged"] == 2 and len(out["moved_aside"]) == 2
    store = EvidenceStore(tmp_path / "evidence.db", holder_id=HOLDER, tenant_id=TENANT, owner_ids=[OWNER])
    after = snapshot(store)
    for rid, tok in ((eng_rid, token("engineering", 1)), (sales_rid, token("sales", 1))):
        assert after[rid]["doc"][0] == "deleted" and after[rid]["memories"] == [] and after[rid]["status"] == "purged"
        assert not [x for x in await store.search(tok, k=5, audience=OWNER_AUDIENCE) if x["doc_id"] == rid]
        assert all(fts_hits(c, tok) == 0 for c in shard_conns(store).values())
    others = [r for r in snap if r not in (eng_rid, sales_rid)]
    assert all(after[r]["doc"] == snap[r]["doc"] and after[r]["memories"] == snap[r]["memories"] for r in others)
    assert store.store._conn.execute("SELECT resurrectable FROM deletion_tombstones WHERE record_id=?", (eng_rid,)).fetchone()[0] == 0
    await store.close()
    # a disk loss of the control file too: only the coordinator's ledger knows; it is replayed by sha256(record_key)
    for p in tmp_path.glob("evidence.db*"):
        p.unlink()
    out = await restore_shards(tmp_path / "backup", tmp_path / "evidence.db", holder_id=HOLDER, tenant_id=TENANT,
                               ledger=[{"record_key_hash": ledger_key(eng_key), "reason": "deleted_at_source", "requested_at": "2026-10-08T00:00:00+00:00",
                                        "ref_ids": "[]"}])
    assert out["replay"]["purged"] == 1
    store = EvidenceStore(tmp_path / "evidence.db", holder_id=HOLDER, tenant_id=TENANT, owner_ids=[OWNER])
    assert snapshot(store)[eng_rid]["doc"][0] == "deleted"
    assert not [x for x in await store.search(token("engineering", 1), k=5, audience=OWNER_AUDIENCE) if x["doc_id"] == eng_rid]
    await store.close()
    with pytest.raises(ValueError):
        await restore_shards(tmp_path / "backup", tmp_path / "other" / "evidence.db", holder_id="hold_other", tenant_id=TENANT)


async def test_the_server_backup_bundle_carries_every_shard_file(tmp_path):
    from mycelic.config import Settings
    from mycelic.observability import backup_bundle, restore_bundle
    src = Settings(data_dir=tmp_path / "src")
    holder_dir = Path(src.holders_dir) / HOLDER
    holder_dir.mkdir(parents=True)
    h = await build(holder_dir, n=4)
    target = (await h["store"].shards.start_split(["engineering"], requested_by=OWNER))["to_shard"]
    await h["store"].close()
    manifest = backup_bundle(src, tmp_path / "bundle.tar.gz", include_coord=False)
    assert [(f["kind"], f["path"]) for f in manifest["files"]] == [("holder", f"holders/{HOLDER}/evidence.db"),
                                                                  ("holder_shard", f"holders/{HOLDER}/{target}.db")]
    dst = Settings(data_dir=tmp_path / "dst")
    out = restore_bundle(dst, tmp_path / "bundle.tar.gz")
    assert [r["kind"] for r in out["restored"]] == ["holder", "holder_shard"]
    store = EvidenceStore(Path(dst.holders_dir) / HOLDER / "evidence.db", holder_id=HOLDER, tenant_id=TENANT, owner_ids=[OWNER])
    assert len([a for a in snapshot(store).values() if a["shard"] == target]) == 4
    await store.close()


# ================================================================================================ tenant isolation (§7.10)
async def test_a_shard_file_resolves_only_through_its_own_holder(tmp_path):
    h = await build(tmp_path, n=2)
    store = h["store"]
    sh = store.shards
    for bad in ("../evidence.db", "/etc/passwd", "a/b.db", ".hidden.db", "..", "x.db\x00"):
        with pytest.raises(ShardPathError):
            sh.path_for(bad)
    assert sh.path_for("shd_0123456789abcdef.db").parent == tmp_path.resolve()
    # a tampered shard map cannot point a shard at another holder's file
    other = tmp_path.parent / f"{tmp_path.name}-other"
    other.mkdir()
    (other / "evidence.db").write_bytes(b"")
    now = "2026-10-08T00:00:00+00:00"
    store.store._conn.execute("INSERT INTO shard_map(shard_id, ordinal, file_name, domain_ids, status, schema_version, created_at, updated_at) "
                              "VALUES ('shd_0123456789abcdef', 9, ?, '[\"sales\"]', 'active', 3, ?, ?)", (f"../{other.name}/evidence.db", now, now))
    with pytest.raises(ShardPathError):
        sh.store("shd_0123456789abcdef")
    assert [s for s, _ in [(spec.shard_id, st) for spec, st in sh.open_stores()]] == [DEFAULT_SHARD]
    # an unknown shard id is not open-able, and a non-shard id never names a file
    with pytest.raises(Exception):
        sh.store("shd_ffffffffffffffff")
    await store.close()
