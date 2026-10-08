"""Regression tests for the independent review of ingestion Phase A, one per confirmed finding (numbers follow the review).

Each test reproduces the defect's scenario and asserts the fixed behaviour: no restricted content through holder reads,
use-time source ACLs, disclosure and external-model limits on answers, durable deletions at the coordinator, one root
per provider object, repeated redactions, payloads of deleted records, scrubbed discoveries, released webhook claims,
the full question audience, intersected memberships, deprecated domains, and connector retracts reported as deletions.
"""
from __future__ import annotations

import json
from pathlib import Path

from mycelic.ingest.acl import members_of, narrow
from mycelic.ingest.contract import WebhookNotice
from mycelic.ingest.domains import Domain, Taxonomy, domains_overlap
from mycelic.ingest.events import Permissions
from mycelic.ingest.service import IngestService
from mycelic.knowledge import KnowledgeService
from mycelic.knowledge.service import DELETED_DISCOVERY_TITLE, DELETED_TEXT
from mycelic.knowledge.support import compute_support
from mycelic.tests.ingest_support import OWNER, connect_export, make_pipeline, make_store, question, write_jsonl
from mycelic.tests.test_knowledge_goals import _org
from mycelic.util import plus_seconds

TEXT = "The quokka migration budget was cut by forty percent after the board meeting on Tuesday."
DAY = 86400
OUTSIDER = {"principal_ids": ["u_bob"], "complete": True, "owner": False}
MEMBER = {"principal_ids": ["u_alice"], "complete": True, "owner": False}


async def _holder(tmp_path: Path, name: str, header: dict, records: list[dict] | None = None, **source_settings):
    store = make_store(tmp_path, name)
    svc = IngestService(make_pipeline(store))
    f = write_jsonl(tmp_path / f"{name}.jsonl", records or [{"type": "message", "id": "m1", "created_at": "2026-09-01T10:00:00Z", "text": TEXT}],
                    header=header)
    con = await connect_export(svc, [f], source_app="teamchat")
    if source_settings:
        src = svc.sources(con["connector_id"])[0]
        await svc.set_source(src.source_id, actor=OWNER, **source_settings)
    await svc.p.sync(con["connector_id"])
    await svc.p.process_available()
    return store, svc, con, f


# 1 -------------------------------------------------------------------------------------------- holder reads take an audience
async def test_1_search_list_and_read_respect_the_source_acl(tmp_path):
    store, *_ = await _holder(tmp_path, "a", {"id": "ch-secret", "name": "#exec", "source_type": "channel", "visibility": "members", "member_ids": ["u_alice"]})
    assert await store.search("quokka migration budget", k=5) == []                          # no audience: fail closed
    assert await store.search("quokka migration budget", k=5, audience=OUTSIDER) == []
    assert await store.search("quokka migration budget", k=5, audience=MEMBER)
    assert await store.search("quokka migration budget", k=5, audience={"owner": True, "complete": True, "principal_ids": []})
    assert await store.list_documents(audience=OUTSIDER) == []
    doc_id = (await store.list_documents(audience=MEMBER))[0]["doc_id"]
    assert await store.document(doc_id, audience=OUTSIDER) is None and await store.document_text(doc_id, audience=OUTSIDER) is None
    await store.close()


# 2 -------------------------------------------------------------------------------------------- the source ACL applies at use time
async def test_2_a_channel_turned_members_only_withholds_existing_records(tmp_path):
    store, svc, con, f = await _holder(tmp_path, "b", {"id": "ch-x", "name": "#x", "source_type": "channel", "visibility": "public"})
    assert (await store.answer_question(question("quokka migration budget board meeting", audience=OUTSIDER)))["status"] == "answered"
    write_jsonl(f, [{"type": "message", "id": "m1", "created_at": "2026-09-01T10:00:00Z", "text": TEXT}],
                header={"id": "ch-x", "name": "#x", "source_type": "channel", "visibility": "members", "member_ids": ["u_alice"]})
    await svc.discover_sources(con["connector_id"])
    assert (await store.answer_question(question("quokka migration budget board meeting again", audience=OUTSIDER)))["status"] == "no_evidence"
    assert (await store.answer_question(question("quokka migration budget board meeting member", audience=MEMBER)))["status"] == "answered"
    await svc.set_source(svc.sources(con["connector_id"])[0].source_id, actor=OWNER, exportable=True)
    write_jsonl(f, [{"type": "message", "id": "m1", "created_at": "2026-09-01T10:00:00Z", "text": TEXT}],
                header={"id": "ch-x", "name": "#x", "source_type": "channel", "visibility": "private", "member_ids": ["u_alice"]})
    await svc.discover_sources(con["connector_id"])
    assert svc.sources(con["connector_id"])[0].exportable is False             # the opt-in does not carry over to a private source
    await store.close()


# 3 -------------------------------------------------------------------------------------------- disclosure and model limits
async def test_3_disclosure_none_and_no_external_models_hold_for_the_answer(tmp_path):
    store, *_ = await _holder(tmp_path, "c", {"id": "ch-n", "name": "#n", "source_type": "channel", "visibility": "public"}, disclosure="none")
    r = await store.answer_question(question("quokka migration budget board meeting", audience=OUTSIDER))
    assert r["status"] == "no_evidence" and "quokka" not in json.dumps(r)
    calls = []

    class Spy:
        async def run_task(self, task, inp, **kw):
            calls.append(task)
            ev = inp.get("evidence") or []
            return {"answer": " ".join(e["excerpt"] for e in ev), "confidence": 0.7, "used_ref_ids": [e["ref_id"] for e in ev], "no_evidence": False}
    store2, *_ = await _holder(tmp_path, "c2", {"id": "ch-m", "name": "#m", "source_type": "channel", "visibility": "public"}, allow_external_models=0)
    store2.router = Spy()
    r2 = await store2.answer_question(question("quokka migration budget board meeting", audience=OUTSIDER))
    assert r2["status"] == "answered" and "answer_from_evidence" not in calls          # the local rule answered
    await store.close()
    await store2.close()


# 4 -------------------------------------------------------------------------------------------- deletions are durable at the coordinator
async def test_4_a_deletion_that_overtakes_the_response_wins(db, org, auth, authz):
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    t, hid, sysp = o["t"], o["ha"]["holder_id"], authz.principal_for_system(o["t"])
    await ks.on_evidence_event(sysp, t, hid, "deleted", ["rx"], reason="deleted in the app")
    await ks.upsert_refs(t, hid, [{"ref_id": "rx", "source_root_id": "rootX", "observed_at": plus_seconds(-DAY), "title": "message",
                                   "disclosed_excerpt": "SECRET deleted excerpt", "disclosure_level": "excerpt"}])
    ref = ks.get_ref("rx")
    assert ref["status"] == "retracted" and ref["disclosed_excerpt"] == "" and "SECRET" not in ref["title"]
    # an active ref that was deleted is never refilled by a later response naming it
    await ks.upsert_refs(t, hid, [{"ref_id": "ry", "source_root_id": "rootY", "disclosed_excerpt": "SECRET y", "disclosure_level": "excerpt"}])
    await ks.on_evidence_event(sysp, t, hid, "deleted", ["ry"])
    await ks.upsert_refs(t, hid, [{"ref_id": "ry", "disclosed_excerpt": "SECRET y", "disclosure_level": "excerpt"}])
    assert ks.get_ref("ry")["disclosed_excerpt"] == ""


# 5 -------------------------------------------------------------------------------------------- one provider object, one root
def test_5_two_versions_of_one_message_are_one_root():
    refs = [{"ref_id": "a", "holder_id": "h1", "source_root_id": "fp_v1", "root_known": 1, "status": "active", "role": "supports", "meta": {"object_key": "ok1_m"}},
            {"ref_id": "b", "holder_id": "h2", "source_root_id": "fp_v2", "root_known": 1, "status": "active", "role": "supports", "meta": json.dumps({"object_key": "ok1_m"})},
            {"ref_id": "c", "holder_id": "h2", "source_root_id": "fp_other", "root_known": 1, "status": "active", "role": "supports"}]
    s = compute_support(refs)
    assert s["independent_roots"] == 2 and s["copied_refs"] == 1


# 6 -------------------------------------------------------------------------------------------- a second redaction is not a duplicate
async def test_6_redact_show_redact_again(tmp_path):
    recs = [{"type": "message", "id": "m1", "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-01T10:00:00Z", "text": TEXT}]
    store, svc, con, f = await _holder(tmp_path, "r", {"id": "ch", "name": "#c", "source_type": "channel", "visibility": "public"}, records=recs)
    from mycelic.tests.ingest_support import append_jsonl
    append_jsonl(f, [{"type": "redact", "id": "m1", "updated_at": "2026-09-02T10:00:00Z"}])
    await svc.p.sync(con["connector_id"]); await svc.p.process_available()
    append_jsonl(f, [{"type": "edit", "id": "m1", "updated_at": "2026-09-03T10:00:00Z", "text": TEXT + " Shown again."}])
    await svc.p.sync(con["connector_id"]); await svc.p.process_available()
    assert await store.search("quokka", k=3, audience={"owner": True, "complete": True, "principal_ids": []})
    append_jsonl(f, [{"type": "redact", "id": "m1", "updated_at": "2026-09-04T10:00:00Z"}])
    await svc.p.sync(con["connector_id"]); await svc.p.process_available()
    assert await store.search("quokka", k=3, audience={"owner": True, "complete": True, "principal_ids": []}) == []
    await store.close()


# 7 -------------------------------------------------------------------------------------------- no content waits for a deleted record
async def test_7_dead_letters_of_a_deleted_record_lose_their_payload(tmp_path):
    store, svc, con, f = await _holder(tmp_path, "d", {"id": "ch", "name": "#c", "source_type": "channel", "visibility": "public"})
    c = store.store._conn
    rec = c.execute("SELECT record_id, record_key FROM ingest_records").fetchone()
    # a content item for the record sits dead-lettered (an earlier failure)
    c.execute("INSERT INTO ingest_queue(event_key, record_key, connector_id, stream, kind, priority_class, payload, payload_bytes, status, attempts, max_attempts, "
              "available_at, created_at, updated_at, order_key) SELECT 'ek_dead', record_key, connector_id, 'incr:x', 'message', 'live', ?, 10, 'dead', 5, 5, "
              "created_at, created_at, created_at, 'zz' FROM ingest_queue LIMIT 1", (json.dumps({"body": "SECRET body"}),))
    await svc.delete_record(rec["record_id"], actor=OWNER)
    assert "SECRET" not in json.dumps([dict(r) for r in c.execute("SELECT payload FROM ingest_queue")])
    await store.close()


# 8 -------------------------------------------------------------------------------------------- discoveries lose the deleted text
async def test_8_discovery_title_and_summary_are_scrubbed(db, org, auth, authz):
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    t, hid, sysp = o["t"], o["ha"]["holder_id"], authz.principal_for_system(o["t"])
    p = authz.principal_for_user(o["lead"]["user_id"])
    q = {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "policy": {"visibility": "unit"}}
    await ks.upsert_refs(t, hid, [{"ref_id": "rz", "source_root_id": "rootZ", "observed_at": plus_seconds(-DAY), "disclosed_excerpt": "PINEAPPLE merger closes in May",
                                   "disclosure_level": "excerpt"}])
    claim, _ = await ks.commit_claim(p, {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "visibility": "unit", "text": "The PINEAPPLE merger closes in May",
                                         "kind": "finding", "confidence": 0.8, "created_by_type": "loop", "created_by_id": "loop"},
                                     evidence=[{"ref_id": "rz"}], question=q, idempotency_key="kz")
    d = await ks.create_discovery(sysp, t, title="PINEAPPLE merger timing", summary="The PINEAPPLE merger closes in May", kind="finding",
                                  claim_ids=[claim["claim_id"]], scope_unit_id=o["dept"]["unit_id"])
    await ks.on_evidence_event(sysp, t, hid, "deleted", ["rz"])
    dd = ks.get_discovery(d["discovery_id"])
    assert ks.get_claim(claim["claim_id"])["text"] == DELETED_TEXT and dd["title"] == DELETED_DISCOVERY_TITLE and "PINEAPPLE" not in dd["summary"]
    leftovers = [r[0] for r in db.all("SELECT payload FROM events WHERE ref_id=?", (d["discovery_id"],))] + \
                [r[0] for r in db.all("SELECT after FROM revisions WHERE object_id=?", (d["discovery_id"],))] + \
                [r[0] for r in db.all("SELECT detail FROM audit_log WHERE resource_id=?", (d["discovery_id"],))]
    assert not any("PINEAPPLE" in (x or "") for x in leftovers)


# 9 -------------------------------------------------------------------------------------------- a failed notice is not "seen"
async def test_9_webhook_claim_is_released_when_the_page_fails(tmp_path):
    store, svc, con, f = await _holder(tmp_path, "w", {"id": "ch", "name": "#c", "source_type": "channel", "visibility": "public"})
    calls = {"n": 0}
    instance = svc.p.connector(con["connector_id"])

    async def boom(ctx, notice):
        calls["n"] += 1
        raise RuntimeError("provider down")
        yield  # pragma: no cover
    instance.handle_webhook = boom
    src = svc.sources(con["connector_id"])[0]
    notice = WebhookNotice(connector_type="local_export", delivery_id="d-1", external_account_id="acme", source_external_id=src.external_id,
                           action="edited", object_refs=({"id": "m1"},))
    for _ in range(2):
        try:
            await svc.handle_notice(con["connector_id"], notice)
        except RuntimeError:
            pass
    assert calls["n"] == 2                                                           # the redelivery was processed again
    await store.close()


# 10 ------------------------------------------------------------------------------------------- the audience covers goal readers
async def test_10_question_audience_includes_the_goals_assigned_unit(db, org, auth, authz):
    from mycelic.tests.test_loop_engine import build
    o = await _org(auth, org)
    s = await build(db, org, auth, authz)
    p_lead = authz.principal_for_user(o["lead"]["user_id"])
    goal = await s["goals"].create_goal(p_lead, {"title": "g", "objective": "o", "owner_type": "unit", "owner_id": o["team_a"]["unit_id"]})
    q = {"question_id": "q_x", "tenant_id": o["t"], "scope_unit_id": o["team_a"]["unit_id"], "policy": {"visibility": "unit"}, "asker_type": "loop",
         "asker_id": "loop", "goal_id": goal["goal_id"]}
    audience = s["questions"].question_audience(q)
    readers = {u["user_id"] for u in db.all("SELECT user_id FROM users WHERE tenant_id=?", (o["t"],))
               if s["questions"]._full_view(authz.principal_for_user(u["user_id"]), q)}
    assert readers <= set(audience["principal_ids"])


# 12 ------------------------------------------------------------------------------------------- both memberships must hold
def test_12_narrow_intersects_membership_refs():
    n = narrow(Permissions("members", (), "chan-A"), Permissions("members", (), "thread-B"))
    refs = {"chan-A": ["u1", "u2"], "thread-B": ["u2", "u3"]}
    assert members_of(n, lambda r: refs[r]) == {"u2"}


# 13 ------------------------------------------------------------------------------------------- deprecated domains, case
def test_13_deprecated_domains_route_nothing_and_case_does_not_matter():
    tax = Taxonomy([Domain("legal", "Legal", None), Domain("legal.contracts", "Contracts", "legal"), Domain("old", "Old", None, status="deprecated")], {"lawyers": "legal"})
    assert domains_overlap(["Legal"], ["legal.contracts"], tax)
    assert domains_overlap(["LAWYERS"], ["legal"], tax)
    assert not domains_overlap(["old"], ["old"], tax)


# 14 ------------------------------------------------------------------------------------------- a connector retract is a deletion
async def test_14_retracting_a_connector_record_reports_deleted(tmp_path):
    from mycelic.holder.service import HolderService
    from mycelic.tests.test_holder import MemoryTransport
    from mycelic.transport import Envelope, Subjects
    store, svc, con, f = await _holder(tmp_path, "x", {"id": "ch", "name": "#c", "source_type": "channel", "visibility": "public"})
    transport = MemoryTransport()
    hs = HolderService(store, transport, holder_id=store.holder_id, tenant_id=store.tenant_id, route_key="rk")
    doc_id = store.store._conn.execute("SELECT record_id FROM ingest_records").fetchone()["record_id"]
    env = Envelope.new(Subjects.holder_ingest(store.tenant_id, store.holder_id), "retract", store.tenant_id, {"doc_id": doc_id, "reason": "owner"}, msg_id="rt1")
    await hs.handle(env.sign("rk"))
    events = [e.payload for e in transport.log if e.kind == "evidence_event"]
    assert events and events[-1]["event"] == "deleted"
    await store.close()
