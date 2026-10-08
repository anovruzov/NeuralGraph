"""The coordinator side of sharding: the shard registry mirrored from heartbeats (counts only), and the admin API
(``GET /api/admin/shards``, ``POST /api/admin/shards/{holder_id}/split``, ``GET /api/admin/shards/migrations/{id}``):
admin only, audited, tenant-isolated, embedded holders split in-process and external holders through a signed control
envelope. Offline and deterministic.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mycelic.holder.service import HolderService
from mycelic.ingest.service import IngestService
from mycelic.shard_registry import mirror_shards_sync
from mycelic.tests.ingest_support import write_jsonl
from mycelic.tests.test_api import Api, api  # noqa: F401  (fixture)
from mycelic.tests.test_ingest_shards import TEXTS, records
from mycelic.tests.test_knowledge_goals import _org


async def _feed(svc: IngestService, owner: str, tmp: Path, *, n: int = 6) -> None:
    for dom in TEXTS:
        f = write_jsonl(tmp / f"{dom}.jsonl", records(dom, n), header={"id": dom, "name": dom, "source_type": "channel", "visibility": "public",
                                                                       "domains": [dom]})
        con = await svc.add_connector("local_export", created_by=owner, config={"source_app": f"app-{dom}", "account_id": dom, "paths": [str(f)]})
        await svc.discover_sources(con["connector_id"])
        for s in svc.sources(con["connector_id"]):
            await svc.set_source(s.source_id, actor=owner, selection="included")
        await svc.p.sync(con["connector_id"])
    await svc.p.process_available()


# ------------------------------------------------------------------------------------------------ registry
async def test_heartbeat_mirrors_the_shard_map_with_counts_only(db, org, auth):
    o = await _org(auth, org)
    hid, tid = o["ha"]["holder_id"], o["t"]
    report = {"items": [
        {"shard_id": "s0", "ordinal": 0, "file_name": "evidence.db", "partition": {}, "status": "active", "schema_version": 3,
         "stats": {"active_memories": 10, "matrix_bytes": 10240, "title": "Secret merger plans", "embedding_dims": [256]}, "health": "ok",
         "writer_id": "host:1", "last_backup_at": "2026-10-08T00:00:00+00:00"},
        {"shard_id": "shd_00112233445566aa", "ordinal": 1, "file_name": "shd_00112233445566aa.db", "status": "active", "schema_version": 3,
         "partition": {"domain_ids": ["engineering", f"personal.{hid}.reading", "made up!"]}, "stats": {"active_memories": 5}, "health": "hot",
         "writer_id": "host:1"},
        {"shard_id": "../../etc", "ordinal": 2, "status": "active"},
        {"shard_id": "shd_00112233445566bb", "ordinal": 3, "file_name": "../x.db", "status": "nonsense", "schema_version": 3}],
        "migrations": [{"migration_id": "smg_0011223344556677", "from_shard": "s0", "to_shard": "shd_00112233445566aa", "state": "done",
                        "partition": {"domain_ids": ["engineering"]}, "checkpoint": {"moved": 12, "note": "text never kept"}, "requested_by": "usr_x"},
                       {"migration_id": "not-a-migration", "state": "done"}],
        "recommendations": [{"shard_id": "s0", "action": "split", "domain_ids": ["sales", f"personal.{hid}.x"], "signals": {"active_memories": 10.0},
                             "moves": {"memories": 6, "share": 0.6}}]}
    await org.holder_heartbeat(hid, stats={"documents": 1, "shards": report})
    rows = {r["shard_id"]: dict(r) for r in db.all("SELECT * FROM shards WHERE holder_id=?", (hid,))}
    assert set(rows) == {"s0", "shd_00112233445566aa", "shd_00112233445566bb"}           # an invalid shard id is never stored
    s0 = rows["s0"]
    assert json.loads(s0["stats"]) == {"active_memories": 10, "matrix_bytes": 10240, "embedding_dims": [256]}   # numbers only
    assert s0["placement"] == "external" and s0["writer_id"] == "host:1" and s0["writer_lease_until"]     # an external holder: no path
    assert json.loads(s0["recommendation"])["domain_ids"] == ["sales", "personal"]
    d = rows["shd_00112233445566aa"]
    assert json.loads(d["partition"]) == {"domain_ids": ["engineering", "personal"]} and d["health"] == "hot" and d["tenant_id"] == tid
    assert rows["shd_00112233445566bb"]["status"] == "active" and rows["shd_00112233445566bb"]["placement"] == "external"
    mig = db.one("SELECT * FROM shard_migrations WHERE migration_id='smg_0011223344556677'")
    assert mig["state"] == "done" and json.loads(mig["checkpoint"]) == {"moved": 12} and mig["holder_id"] == hid
    assert db.one("SELECT COUNT(*) AS n FROM shard_migrations")["n"] == 1
    # the lease belongs to its writer until it expires; another process cannot take it meanwhile
    from mycelic.shard_registry import acquire_lease_sync
    async with db.tx() as c:
        assert not acquire_lease_sync(c, holder_id=hid, shard_id="s0", writer_id="host:2")
        assert acquire_lease_sync(c, holder_id=hid, shard_id="s0", writer_id="host:1")
    # another tenant's holder cannot overwrite these rows through its own heartbeat
    other = await auth.register_tenant(org_name="Other", slug="other", admin_email="admin@other.test", admin_name="Admin", password="password123")
    async with db.tx() as c:
        mirror_shards_sync(c, tenant_id=other["tenant"]["tenant_id"], holder_id=hid, mode="embedded", report=report)
    assert db.one("SELECT tenant_id FROM shards WHERE holder_id=? AND shard_id='s0'", (hid,))["tenant_id"] == tid


# ------------------------------------------------------------------------------------------------ admin API
async def test_admin_splits_an_embedded_holder_and_reads_the_registry(api: Api, tmp_path: Path) -> None:  # noqa: F811
    tok, reg = await api.register("acme")
    uid = reg["user"]["user_id"]
    status, h, _ = await api.call("POST", "/api/holders", token=tok, body={"name": "Ana's memory"})
    assert status == 201, h
    hid = h["holder"]["holder_id"]
    await api.rt.holders.ensure(hid)
    runtime = api.rt.holders.ingest(hid)
    await _feed(runtime.service, uid, tmp_path)
    status, before, _ = await api.call("GET", "/api/admin/shards", token=tok)
    assert status == 200 and [i["shard_id"] for i in before["items"]] == ["s0"] and before["items"][0]["stats"]["records"] == 18
    status, out, _ = await api.call("POST", f"/api/admin/shards/{hid}/split", token=tok, body={"domain_ids": ["engineering"], "wait": True})
    assert status == 200, out
    mig = out["migration"]
    assert mig["state"] == "done" and mig["partition"] == {"domain_ids": ["engineering"]} and mig["checkpoint"]["moved"] == 6
    status, lst, _ = await api.call("GET", "/api/admin/shards", token=tok)
    assert status == 200
    items = {i["shard_id"]: i for i in lst["items"]}
    assert set(items) == {"s0", mig["to_shard"]} and lst["recommendations"] == []
    assert items[mig["to_shard"]]["partition"] == {"domain_ids": ["engineering"]} and items[mig["to_shard"]]["status"] == "active"
    assert items[mig["to_shard"]]["stats"]["records"] == 6 and items["s0"]["stats"]["records"] == 12
    assert set(items["s0"]) >= {"shard_id", "holder_id", "ordinal", "partition", "status", "health", "stats", "last_backup_at"}
    assert "Item zq" not in json.dumps(lst)                                         # no content in the registry
    status, got, _ = await api.call("GET", f"/api/admin/shards/migrations/{mig['migration_id']}", token=tok)
    assert status == 200 and got["migration"]["state"] == "done" and got["migration"]["holder_id"] == hid
    audit = api.rt.db.all("SELECT * FROM audit_log WHERE action='shard.split'")
    assert len(audit) == 1 and json.loads(audit[0]["detail"])["migration_id"] == mig["migration_id"] and audit[0]["actor_id"] == uid
    # the holder's search fans out now; its owner's API view is unchanged
    status, srch, _ = await api.call("GET", f"/api/holders/{hid}/search?q=checkout%20deploy%20pipeline&k=3", token=tok)
    assert status == 200 and srch["results"]
    # refused splits: unknown domain (400), overlap (409, audited as denied)
    status, err, _ = await api.call("POST", f"/api/admin/shards/{hid}/split", token=tok, body={"domain_ids": ["nope"]})
    assert status == 400
    status, err, _ = await api.call("POST", f"/api/admin/shards/{hid}/split", token=tok, body={"domain_ids": ["engineering.dependencies"]})
    assert status == 409 and err["code"] == "overlap"
    assert api.rt.db.one("SELECT outcome FROM audit_log WHERE action='shard.split' ORDER BY id DESC LIMIT 1")["outcome"] == "deny"
    # a split in the background answers 202 and finishes on its own
    status, bg, _ = await api.call("POST", f"/api/admin/shards/{hid}/split", token=tok, body={"domain_ids": ["legal"]})
    assert status == 202 and bg["migration"]["state"] in ("planned", "provisioning", "copying", "catching_up", "cutover", "cleanup", "done")
    for _ in range(100):
        status, got, _ = await api.call("GET", f"/api/admin/shards/migrations/{bg['migration']['migration_id']}", token=tok)
        if got["migration"]["state"] == "done":
            break
        await asyncio.sleep(0.05)
    assert got["migration"]["state"] == "done"


async def test_shard_endpoints_are_admin_only_and_tenant_isolated(api: Api, tmp_path: Path) -> None:  # noqa: F811
    tok, reg = await api.register("acme")
    status, h, _ = await api.call("POST", "/api/holders", token=tok, body={"name": "Ana's memory"})
    hid = h["holder"]["holder_id"]
    await api.rt.holders.ensure(hid)
    await _feed(api.rt.holders.ingest(hid).service, reg["user"]["user_id"], tmp_path, n=3)
    status, out, _ = await api.call("POST", f"/api/admin/shards/{hid}/split", token=tok, body={"domain_ids": ["sales"], "wait": True})
    assert status == 200
    mid = out["migration"]["migration_id"]
    emp_tok, _ = await api.invite_and_accept(tok, email="emp@acme.example", role="employee", unit_id=reg["root_unit"]["unit_id"])
    for method, path, body in (("GET", "/api/admin/shards", None), ("POST", f"/api/admin/shards/{hid}/split", {"domain_ids": ["legal"]}),
                               ("GET", f"/api/admin/shards/migrations/{mid}", None)):
        status, _b, _ = await api.call(method, path, token=emp_tok, body=body)
        assert status == 403, (method, path)
    other_tok, _ = await api.register("globex")
    status, lst, _ = await api.call("GET", "/api/admin/shards", token=other_tok)
    assert status == 200 and lst["items"] == []
    status, _b, _ = await api.call("POST", f"/api/admin/shards/{hid}/split", token=other_tok, body={"domain_ids": ["legal"]})
    assert status == 404
    status, _b, _ = await api.call("GET", f"/api/admin/shards/migrations/{mid}", token=other_tok)
    assert status == 404
    status, _b, _ = await api.call("GET", "/api/admin/shards", token=None)
    assert status == 401


async def test_an_external_holder_splits_on_a_signed_control_envelope(api: Api, tmp_path: Path) -> None:  # noqa: F811
    tok, reg = await api.register("acme")
    uid = reg["user"]["user_id"]
    status, h, _ = await api.call("POST", "/api/holders", token=tok, body={"name": "Laptop memory", "mode": "external"})
    assert status == 201, h
    hid, tid = h["holder"]["holder_id"], reg["tenant"]["tenant_id"]
    # the holder process: its own store and pipeline, answering control envelopes signed with its route key
    from mycelic.evidence import EvidenceStore
    from mycelic.tests.ingest_support import make_pipeline
    store = EvidenceStore(tmp_path / "ext" / "evidence.db", holder_id=hid, tenant_id=tid, owner_ids=[uid])
    svc = IngestService(make_pipeline(store))
    await _feed(svc, uid, tmp_path, n=3)
    holder = HolderService(store, api.rt.transport, holder_id=hid, tenant_id=tid, route_key=api.rt.org.route_key(hid), transport_heartbeat=False)
    seen: list[Any] = []

    async def request(env: Any, *, timeout: float, sign_key: str) -> Any:
        env.sign(sign_key)
        assert env.verify(api.rt.org.route_key(hid)) and env.kind == "control" and env.payload["action"] == "shard_split"
        seen.append(env)
        outcome = await holder._dispatch(env)
        return type("Reply", (), {"payload": outcome["result"]})()
    api.rt.transport.request = request
    status, out, _ = await api.call("POST", f"/api/admin/shards/{hid}/split", token=tok, body={"domain_ids": ["engineering"]})
    assert status == 202, out
    mid = out["migration"]["migration_id"]
    assert len(seen) == 1 and api.rt.db.one("SELECT holder_id FROM shard_migrations WHERE migration_id=?", (mid,))["holder_id"] == hid
    for t in list(store.shards.tasks):
        await t
    assert store.shards.migration(mid)["state"] == "done"
    # its next heartbeat brings the registry up to date
    await api.rt.org.holder_heartbeat(hid, stats=await store.stats())
    status, got, _ = await api.call("GET", f"/api/admin/shards/migrations/{mid}", token=tok)
    assert status == 200 and got["migration"]["state"] == "done"
    status, lst, _ = await api.call("GET", "/api/admin/shards", token=tok)
    assert {i["placement"] for i in lst["items"] if i["holder_id"] == hid} == {"external"} and len([i for i in lst["items"] if i["holder_id"] == hid]) == 2
    await store.close()
