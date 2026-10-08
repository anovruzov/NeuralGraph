"""Coordinator side of evidence changes from connectors (docs/mycelic/INGESTION.md E11–E13, G22).

* ``deleted`` purges the content the coordinator holds (excerpt, title, quoting responses, unsupported claim text);
* ``unavailable`` / ``restored`` take a reference out of support and back;
* conjectures and causal links never become ``supported`` (E13);
* a discovery reports the health of the evidence under it (G22);
* the loop's observations and coordinator events carry no document titles (E12).
"""
from __future__ import annotations

import json

from mycelic.knowledge import KnowledgeService
from mycelic.knowledge.service import DELETED_TEXT, evidence_health
from mycelic.tests.test_knowledge_goals import _org
from mycelic.util import j, new_id, now_iso, plus_seconds

DAY = 86400


async def _setup(db, org, auth, authz):
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    t = o["t"]
    p = authz.principal_for_user(o["lead"]["user_id"])
    q = {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "policy": {"visibility": "unit"}}
    await ks.upsert_refs(t, o["ha"]["holder_id"], [{"ref_id": "ra", "source_root_id": "rootA", "observed_at": plus_seconds(-DAY), "title": "Deploy log",
                                                   "disclosed_excerpt": "deploys failed after the 4.2 upgrade", "disclosure_level": "excerpt"}])
    await ks.upsert_refs(t, o["hb"]["holder_id"], [{"ref_id": "rb", "source_root_id": "rootB", "observed_at": plus_seconds(-DAY), "title": "Incident",
                                                   "disclosed_excerpt": "timeouts after upgrading to 4.2", "disclosure_level": "excerpt"}])
    return o, ks, t, p, q


def _cand(o, text, **kw):
    return {"tenant_id": o["t"], "scope_unit_id": o["dept"]["unit_id"], "visibility": "unit", "text": text, "kind": "finding",
            "confidence": 0.8, "created_by_type": "loop", "created_by_id": "loop", **kw}


async def test_deleted_source_purges_coordinator_content(db, org, auth, authz):
    o, ks, t, p, q = await _setup(db, org, auth, authz)
    sysp = authz.principal_for_system(t)
    only_a, _ = await ks.commit_claim(p, _cand(o, "Deploys fail after the 4.2 upgrade"), evidence=[{"ref_id": "ra"}], question=q, idempotency_key="k1")
    both, _ = await ks.commit_claim(p, _cand(o, "The 4.2 upgrade causes timeouts"), evidence=[{"ref_id": "ra"}, {"ref_id": "rb"}], question=q, idempotency_key="k2")
    assert both["status"] == "supported"
    qid = new_id("q")
    async with db.tx() as c:
        c.execute("INSERT INTO questions(question_id, tenant_id, asker_type, asker_id, text, kind, status, created_at, updated_at) VALUES (?, ?, 'user', 'u', 'q?', 'gap', 'committed', ?, ?)",
                  (qid, t, now_iso(), now_iso()))
        c.execute("INSERT INTO responses(response_id, tenant_id, question_id, holder_id, msg_id, status, content, evidence_ref_ids, received_at) VALUES (?, ?, ?, ?, ?, 'answered', ?, ?, ?)",
                  (new_id("resp"), t, qid, o["ha"]["holder_id"], new_id("msg"), "deploys failed after the 4.2 upgrade", j(["ra"]), now_iso()))
    out = await ks.on_evidence_event(sysp, t, o["ha"]["holder_id"], "deleted", ["ra"], reason="message deleted in the source app")
    ref = ks.get_ref("ra")
    assert ref["status"] == "retracted" and ref["disclosed_excerpt"] == "" and ref["title"] == "[deleted]" and ref["meta"]["deleted_at"]
    assert db.one("SELECT content FROM responses WHERE question_id=?", (qid,))["content"] == DELETED_TEXT
    gone = ks.get_claim(only_a["claim_id"])
    assert gone["status"] == "retracted" and gone["text"] == DELETED_TEXT        # retracted and its text came from the deleted excerpt
    assert all("4.2" not in json.dumps(r["before"]) + json.dumps(r["after"]) for r in ks.revisions_for("claim", only_a["claim_id"]))
    kept = ks.get_claim(both["claim_id"])
    assert kept["status"] == "stale" and kept["text"] == "The 4.2 upgrade causes timeouts"   # still backed by B: the org's own synthesis stays
    assert set(out["affected_claim_ids"]) == {only_a["claim_id"], both["claim_id"]}
    again = await ks.on_evidence_event(sysp, t, o["ha"]["holder_id"], "deleted", ["ra"])
    assert again["changed_ref_ids"] == []                                           # redelivery changes nothing


async def test_unavailable_then_restored(db, org, auth, authz):
    o, ks, t, p, q = await _setup(db, org, auth, authz)
    sysp = authz.principal_for_system(t)
    claim, _ = await ks.commit_claim(p, _cand(o, "The 4.2 upgrade causes timeouts"), evidence=[{"ref_id": "ra"}, {"ref_id": "rb"}], question=q, idempotency_key="k")
    assert claim["status"] == "supported"
    await ks.on_evidence_event(sysp, t, o["hb"]["holder_id"], "unavailable", ["rb"], reason="token expired")
    assert ks.get_ref("rb")["status"] == "unavailable" and ks.get_claim(claim["claim_id"])["status"] != "supported"
    await ks.on_evidence_event(sysp, t, o["hb"]["holder_id"], "restored", ["rb"], reason="reconnected")
    assert ks.get_ref("rb")["status"] == "active" and ks.get_claim(claim["claim_id"])["status"] == "supported"
    # restoring a reference that was never unavailable does nothing
    assert (await ks.on_evidence_event(sysp, t, o["ha"]["holder_id"], "restored", ["ra"]))["changed_ref_ids"] == []


async def test_conjectures_and_causal_links_stay_hypotheses(db, org, auth, authz):
    o, ks, t, p, q = await _setup(db, org, auth, authz)
    conj, g1 = await ks.commit_claim(p, _cand(o, "Could dependency changes be contributing to renewal risk?", kind="hypothesis"),
                                     evidence=[{"ref_id": "ra"}, {"ref_id": "rb"}], question=q, idempotency_key="c1")
    assert conj["status"] == "hypothesis" and g1.support["independent_roots"] == 2 and any("stays a hypothesis" in r for r in g1.reasons)
    causal, _ = await ks.commit_claim(p, _cand(o, "The 4.2 upgrade caused the renewal risk", kind="relationship", causal=True),
                                      evidence=[{"ref_id": "ra"}, {"ref_id": "rb"}], question=q, idempotency_key="c2")
    assert causal["status"] == "hypothesis" and causal["support"]["causal"] is True
    # no later recomputation lifts the cap
    for cid in (conj["claim_id"], causal["claim_id"]):
        assert (await ks.recompute_status(authz.principal_for_system(t), cid, reason="sweep"))["status"] == "hypothesis"
    plain, _ = await ks.commit_claim(p, _cand(o, "Timeouts follow the 4.2 upgrade"), evidence=[{"ref_id": "ra"}, {"ref_id": "rb"}], question=q, idempotency_key="c3")
    assert plain["status"] == "supported"


async def test_discovery_evidence_health(db, org, auth, authz):
    o, ks, t, p, q = await _setup(db, org, auth, authz)
    sysp = authz.principal_for_system(t)
    claim, _ = await ks.commit_claim(p, _cand(o, "Timeouts follow the 4.2 upgrade"), evidence=[{"ref_id": "ra"}, {"ref_id": "rb"}], question=q, idempotency_key="h")
    d = await ks.create_discovery(sysp, t, title="Upgrade timeouts", summary="", kind="finding", claim_ids=[claim["claim_id"]], scope_unit_id=o["dept"]["unit_id"])
    assert ks.discovery_summary(ks.get_discovery(d["discovery_id"]))["evidence_health"]["status"] == "healthy"
    await ks.on_evidence_event(sysp, t, o["ha"]["holder_id"], "revised", ["ra"], new_source_root_id="rootA2")
    h = ks.discovery_summary(ks.get_discovery(d["discovery_id"]))["evidence_health"]
    assert h["status"] == "degraded" and h["inactive_refs"] == 1
    assert db.one("SELECT 1 FROM events WHERE kind='discovery.updated' AND ref_id=?", (d["discovery_id"],)) is not None
    await ks.on_evidence_event(sysp, t, o["hb"]["holder_id"], "deleted", ["rb"])
    await ks.on_evidence_event(sysp, t, o["ha"]["holder_id"], "deleted", ["ra"])
    assert ks.discovery_summary(ks.get_discovery(d["discovery_id"]))["evidence_health"]["status"] == "unsupported"
    assert evidence_health([], [])["status"] == "unsupported"


async def test_unknown_evidence_event_is_rejected(db, org, auth, authz):
    o, ks, t, p, q = await _setup(db, org, auth, authz)
    try:
        await ks.on_evidence_event(authz.principal_for_system(t), t, o["ha"]["holder_id"], "exploded", ["ra"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


async def test_ingestion_events_and_loop_observations_carry_no_titles(db, org, auth, authz):
    from types import SimpleNamespace

    from mycelic.api.routes_org import emit_document_event
    from mycelic.tests import test_loop_engine as T
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    h = o["holders"]["elin"]
    rt = SimpleNamespace(db=db, org=org, authz=authz)
    await emit_document_event(rt, h, "document.ingested", {"doc_id": "d1", "title": "Private: salary review for Ana", "domains": ["support"]})
    ev = db.one("SELECT payload FROM events WHERE kind='document.ingested' ORDER BY id DESC LIMIT 1")
    assert "salary" not in ev["payload"] and json.loads(ev["payload"])["domains"] == ["support"]
    goal = await s["goals"].create_goal(authz.principal_for_user(o["petra"]["user_id"]), {"title": "g", "objective": "o", "owner_type": "unit",
                                                                                          "owner_id": o["region"]["unit_id"]})
    obs = s["engine"].observe(goal, None)
    assert obs["new_documents"] and all("title" not in d for d in obs["new_documents"]) and obs["new_documents"][0]["records"] == 1
