"""Regression tests for the commit-gate and loop review (each test names the defect it pins down).

1. evidence the gate demotes is stored as context and never counted again by a later recomputation;
2. revised evidence keeps its original root, stops counting, and the claim stays stale until re-verification;
3. a three-way disagreement contests the majority finding instead of committing it as supported;
4. a conflict is not auto-resolved because a holder timed out;
5. scheduled checks do not re-ask handled targets or spend tokens once everything is handled (see also test_loop_engine);
6. freshness is measured from observation time, not upload time;
7. blind verification never reveals the claim and is tied to its target;
8. pausing or archiving a goal stops its question pipeline, resuming continues it;
9. an unroutable question does not block a loop that still has holders;
10. max_concurrent_questions holds for spawned verification and follow-up questions;
plus: an unresolved conflict leaves both claims as hypotheses, and a subgoal roll-up ignores unmeasured subgoals.
"""
from __future__ import annotations

import asyncio
import json
import re

from mycelic.inquiry import LIVE_STATUSES
from mycelic.knowledge import KnowledgeService
from mycelic.knowledge.support import evidence_time, freshness
from mycelic.models.fake import identify_gap
from mycelic.tests import test_loop_engine as T
from mycelic.tests.test_knowledge_goals import _org
from mycelic.util import parse_iso, plus_seconds, utcnow

DAY = 86400


def _cand(o, text, **kw):
    return {"tenant_id": o["t"], "scope_unit_id": o["dept"]["unit_id"], "visibility": "unit", "text": text, "kind": "finding",
            "confidence": 0.8, "created_by_type": "loop", "created_by_id": "loop", **kw}


async def _goal(s, p, o, **budget):
    return await s["goals"].create_goal(p, {"title": "[Demo] blockers", "objective": "Identify recurring operational blockers across teams",
                                            "owner_type": "unit", "owner_id": o["region"]["unit_id"],
                                            "budget": {"tokens": 5000000, "usd": 500, "questions": 400, "followup_depth": 2, **budget}}, activate=True)


# ------------------------------------------------------------------------------------------------ 1
async def test_gate_demotions_are_stored_and_never_recounted(db, org, auth, authz):
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    t, p, sysp = o["t"], authz.principal_for_user(o["lead"]["user_id"]), authz.principal_for_system(o["t"])
    q = {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "policy": {"visibility": "unit"}}
    await ks.upsert_refs(t, o["ha"]["holder_id"], [{"ref_id": "in_window", "source_root_id": "rootA", "observed_at": plus_seconds(-3 * DAY)}])
    await ks.upsert_refs(t, o["hb"]["holder_id"], [{"ref_id": "out_window", "source_root_id": "rootB", "observed_at": plus_seconds(-30 * DAY)}])
    claim, gate = await ks.commit_claim(p, _cand(o, "Deploy approvals delay releases by two days", valid_from=plus_seconds(-10 * DAY), valid_to=plus_seconds(10 * DAY)),
                                        evidence=[{"ref_id": "in_window"}, {"ref_id": "out_window"}], question=q, idempotency_key="k1")
    assert claim["status"] == "hypothesis" and gate.support["independent_roots"] == 1
    assert {r["ref_id"]: r["role"] for r in ks.refs_for_claim(claim["claim_id"])} == {"in_window": "supports", "out_window": "context"}
    await ks.sweep_stale(sysp, t)
    after = ks.get_claim(claim["claim_id"])
    assert after["status"] == "hypothesis" and after["support"]["independent_roots"] == 1
    # a revoked holder's evidence: not counted at commit, not counted by any later recomputation
    await ks.upsert_refs(t, o["ha"]["holder_id"], [{"ref_id": "a2", "source_root_id": "rootC", "observed_at": plus_seconds(-DAY)}])
    await ks.upsert_refs(t, o["hb"]["holder_id"], [{"ref_id": "b2", "source_root_id": "rootD", "observed_at": plus_seconds(-DAY)}])
    async with db.tx() as c:
        c.execute("UPDATE holders SET status='revoked' WHERE holder_id=?", (o["hb"]["holder_id"],))
    c3, g3 = await ks.commit_claim(p, _cand(o, "Approvals block hotfix deploys on weekdays"), evidence=[{"ref_id": "a2"}, {"ref_id": "b2"}], question=q, idempotency_key="k3")
    assert c3["status"] == "hypothesis" and g3.support["independent_roots"] == 1
    await ks.sweep_stale(sysp, t)
    await ks.recompute_status(sysp, c3["claim_id"], reason="test")
    assert ks.get_claim(c3["claim_id"])["status"] == "hypothesis"


# ------------------------------------------------------------------------------------------------ 2
async def test_revised_evidence_keeps_root_and_claim_waits_for_reverification(db, org, auth, authz):
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    t, p, sysp = o["t"], authz.principal_for_user(o["lead"]["user_id"]), authz.principal_for_system(o["t"])
    ha, hb = o["ha"]["holder_id"], o["hb"]["holder_id"]
    q = {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "policy": {"visibility": "unit"}}
    await ks.upsert_refs(t, ha, [{"ref_id": "r1", "source_root_id": "rootA", "observed_at": plus_seconds(-2 * DAY)}])
    await ks.upsert_refs(t, hb, [{"ref_id": "r2", "source_root_id": "rootB", "observed_at": plus_seconds(-2 * DAY)}])
    claim, _ = await ks.commit_claim(p, _cand(o, "Deploy approvals take two days"), evidence=[{"ref_id": "r1"}, {"ref_id": "r2"}], question=q, idempotency_key="k1")
    assert claim["status"] == "supported"
    await ks.on_evidence_event(sysp, t, ha, "revised", ["r1"], new_source_root_id="rootA2", reason="doc rewritten")
    r1 = ks.get_ref("r1")
    assert r1["status"] == "revised" and r1["source_root_id"] == "rootA" and r1["meta"]["revised_root_id"] == "rootA2"
    # a redelivered event changes nothing
    again = await ks.on_evidence_event(sysp, t, ha, "revised", ["r1"], new_source_root_id="rootA2")
    assert again["changed_ref_ids"] == []
    out = await ks.recompute_status(sysp, claim["claim_id"], reason="independent verification added support")
    assert out["status"] == "stale" and out["support"]["independent_roots"] == 1 and out["support"]["inactive_ref_ids"] == ["r1"]
    # a reference revised between the response and the commit: the claim is born stale, never supported
    await ks.upsert_refs(t, ha, [{"ref_id": "r3", "source_root_id": "rootC", "observed_at": plus_seconds(-DAY)}])
    await ks.upsert_refs(t, hb, [{"ref_id": "r4", "source_root_id": "rootD", "observed_at": plus_seconds(-DAY)}])
    await ks.on_evidence_event(sysp, t, ha, "revised", ["r3"], new_source_root_id="rootC2")
    c2, g2 = await ks.commit_claim(p, _cand(o, "Approvals block weekday deploys"), evidence=[{"ref_id": "r3"}, {"ref_id": "r4"}], question=q, idempotency_key="k2")
    assert c2["status"] == "stale" and g2.support["independent_roots"] == 1
    # re-verification brings current evidence: the changed support is superseded (kept for lineage) and the claim recovers
    await ks.upsert_refs(t, ha, [{"ref_id": "r1new", "source_root_id": "rootA2", "observed_at": plus_seconds(-1)}])
    async with db.tx() as c:
        c.execute("INSERT INTO claim_evidence(claim_id, ref_id, role, weight) VALUES (?, 'r1new', 'supports', 1.0)", (claim["claim_id"],))
    assert await ks.supersede_changed_support(sysp, claim["claim_id"], reason="re-verified") == ["r1"]
    rec = await ks.recompute_status(sysp, claim["claim_id"], reason="re-verified")
    assert rec["status"] == "supported" and rec["support"]["independent_roots"] == 2
    assert {r["ref_id"]: r["role"] for r in ks.refs_for_claim(claim["claim_id"])}["r1"] == "superseded"
    # a later change to the superseded reference no longer touches the claim
    moved = await ks.on_evidence_event(sysp, t, ha, "retracted", ["r1"])
    assert claim["claim_id"] not in moved["affected_claim_ids"]


# ------------------------------------------------------------------------------------------------ 3
async def test_three_way_disagreement_contests_the_majority_finding(db, org, auth, authz, monkeypatch):
    s = await T.build(db, org, auth, authz)
    monkeypatch.setitem(T.DOCS, "ana", [{"title": "SRE handbook", "text": "Recurring on-call blocker noted in the SRE handbook: the on-call rotation has 6 engineers and pages take 20 minutes to acknowledge.",
                                         "observed_at": plus_seconds(-2 * DAY)}])
    monkeypatch.setitem(T.DOMAINS, "ana", ["on-call"])
    o = await T.seed_org(org, auth, authz, s["transport"])
    goal = await _goal(s, authz.principal_for_user(o["petra"]["user_id"]), o)
    await s["worker"].drain(max_jobs=1, tick_due=False)
    await s["worker"].drain(max_jobs=3, tick_due=False)
    oc = next(q for q in db.all("SELECT question_id, candidate_domains FROM questions WHERE goal_id=?", (goal["goal_id"],)) if "on-call" in q["candidate_domains"])
    for kind in ("question.collect", "question.evaluate"):
        await asyncio.sleep(0.35)
        job = await s["jobs"].lease("w", 30, kinds=[kind])
        while job is not None and job.ref_id != oc["question_id"]:
            await s["jobs"].complete(job.job_id, "w")
            job = await s["jobs"].lease("w", 30, kinds=[kind])
        await s["engine"].handle(job)
        await s["jobs"].complete(job.job_id, "w")
    claims = db.all("SELECT claim_id, status, text FROM claims WHERE question_id=?", (oc["question_id"],))
    assert len(claims) == 2, [dict(c) for c in claims]                     # the majority finding + the minority side, no copies
    assert {c["status"] for c in claims} == {"contested"}
    assert int(db.scalar("SELECT COUNT(*) FROM conflicts", (), 0)) == 1
    majority = next(c for c in claims if "6 engineers" in c["text"])
    holders = {r["holder_id"] for r in s["knowledge"].refs_for_claim(majority["claim_id"])}
    assert holders == {o["holders"]["ana"]["holder_id"], o["holders"]["jonas"]["holder_id"]}


# ------------------------------------------------------------------------------------------------ 4
async def test_conflict_is_not_resolved_because_a_holder_timed_out(db, org, auth, authz):
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    noor = o["scripted"]["noor"]
    orig = noor.on_question

    async def offline_for_contradictions(env):
        if env.payload.get("kind") == "contradiction":
            return                      # noor's holder is offline for the investigation: the route times out
        await orig(env)
    noor.on_question = offline_for_contradictions
    s["transport"].subs = [(pat, cons, offline_for_contradictions if cons == f"h-{o['holders']['noor']['holder_id']}" else h) for pat, cons, h in s["transport"].subs]
    goal = await _goal(s, authz.principal_for_user(o["petra"]["user_id"]), o)
    await T.run_until_quiet(s)
    assert T.job_errors(db) == []
    conflicts = s["knowledge"].list_conflicts(authz.principal_for_system(o["t"]), goal_id=goal["goal_id"])
    assert len(conflicts) == 1
    k = conflicts[0]
    assert k["status"] in ("open", "investigating"), k
    a, b = s["knowledge"].get_claim(k["claim_a_id"]), s["knowledge"].get_claim(k["claim_b_id"])
    assert a["status"] == b["status"] == "contested"
    assert any(step["step"] == "needs_review" and "did not answer" in step["note"] for step in k["investigation"]), k["investigation"]


# ------------------------------------------------------------------------------------------------ 5
def test_fake_identify_gap_accepts_grouped_observations():
    out = identify_gap({"goal": {"title": "g"}, "observations": {"open_conflicts": [{"conflict_id": "k1", "summary": "Numbers differ"}],
                                                                 "stale_claims": [{"claim_id": "c1", "text": "old roster"}]},
                        "existing_claims": [], "candidate_domains": [], "max_gaps": 5})
    assert [g["kind"] for g in out["gaps"]] == ["contradiction", "verification"]


# ------------------------------------------------------------------------------------------------ 6
def test_freshness_is_observation_time_not_upload_time():
    old = {"observed_at": plus_seconds(-200 * DAY), "freshness_at": plus_seconds(-1)}     # a January roster uploaded today
    assert freshness([old], freshness_days=90)["stale"] is True
    reconfirmed = {**old, "meta": {"reconfirmed_at": plus_seconds(-DAY)}}                 # its holder explicitly re-confirmed it
    assert freshness([reconfirmed], freshness_days=90)["stale"] is False
    assert evidence_time({"freshness_at": plus_seconds(-5 * DAY)}) is not None            # no observation time: fallback


# ------------------------------------------------------------------------------------------------ 7
async def test_blind_question_that_reveals_its_claim_is_rejected(db, org, auth, authz):
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    goal = await _goal(s, authz.principal_for_user(o["petra"]["user_id"]), o)
    await T.run_until_quiet(s)
    claim = next(c for c in s["knowledge"].list_claims(authz.principal_for_system(o["t"]), goal_id=goal["goal_id"]) if re.search(r"\d", c["text"]))
    p = authz.principal_for_loop(o["t"], s["goals"].get_goal(goal["goal_id"]))
    base = {"goal_id": goal["goal_id"], "kind": "verification", "trigger": {"kind": "verification", "claim_id": claim["claim_id"]}, "policy": {"blind_verification": True}}
    number = re.search(r"\d+", claim["text"]).group(0)
    for text in (f"Do your records also show {number} for this?", f"Can you confirm: {claim['text']}"):
        try:
            await s["questions"].create(p, {**base, "text": text}, asker_type="loop", asker_id=p.id)
            raise AssertionError("a leaking blind question was accepted")
        except ValueError as exc:
            assert "must not reveal" in str(exc)


# ------------------------------------------------------------------------------------------------ 8
async def test_pause_and_archive_stop_the_question_pipeline(db, org, auth, authz):
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    p = authz.principal_for_user(o["petra"]["user_id"])
    for action in ("pause", "archive"):
        goal = await s["goals"].create_goal(p, {"title": f"[Demo] blockers {action}", "objective": "Identify recurring operational blockers across teams",
                                                "owner_type": "unit", "owner_id": o["region"]["unit_id"],
                                                "budget": {"tokens": 5000000, "usd": 500, "questions": 400, "followup_depth": 2}}, activate=True)
        gid = goal["goal_id"]
        await s["worker"].drain(max_jobs=1, tick_due=False)     # tick
        await s["worker"].drain(max_jobs=3, tick_due=False)     # routes (holders answer inline)
        fake = s["router"].providers["fake"]
        n0 = len(fake.calls)
        q_before = {r["question_id"] for r in db.all("SELECT question_id FROM questions WHERE goal_id=?", (gid,))}
        routes_before = int(db.scalar("SELECT COUNT(*) FROM question_routes", (), 0))
        await s["goals"].action(p, gid, action)
        await T.run_until_quiet(s)
        assert len(fake.calls) == n0, [c["task"] for c in fake.calls[n0:]]
        assert {r["question_id"] for r in db.all("SELECT question_id FROM questions WHERE goal_id=?", (gid,))} == q_before
        assert int(db.scalar("SELECT COUNT(*) FROM question_routes", (), 0)) == routes_before
        assert int(db.scalar("SELECT COUNT(*) FROM discoveries WHERE goal_id=?", (gid,), 0)) == 0
        live = int(db.scalar(f"SELECT COUNT(*) FROM questions WHERE goal_id=? AND status IN ({','.join('?' * len(LIVE_STATUSES))})", (gid, *LIVE_STATUSES), 0))
        if action == "pause":
            assert live == len(q_before)            # kept, with their responses, for resume
            await s["goals"].action(p, gid, "resume")
            await T.run_until_quiet(s)
            assert len(fake.calls) > n0 and int(db.scalar("SELECT COUNT(*) FROM discoveries WHERE goal_id=?", (gid,), 0)) > 0
        else:
            assert live == 0                         # archived: cancelled, routes revoked
            assert int(db.scalar("SELECT COUNT(*) FROM question_routes r JOIN questions q ON q.question_id=r.question_id WHERE q.goal_id=? AND r.status IN ('pending','delivered')", (gid,), 0)) == 0


# ------------------------------------------------------------------------------------------------ 9
async def test_unroutable_question_does_not_block_a_loop_with_holders(db, org, auth, authz):
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    p = authz.principal_for_user(o["petra"]["user_id"])
    goal = await _goal(s, p, o)
    gid = goal["goal_id"]
    await T.run_until_quiet(s)
    await s["goals"].update_goal(p, gid, {"measurement_source": {"domains": ["finance"]}})     # a domain no holder serves
    await s["goals"].enqueue_tick(gid, reason="goal changed", force=True)
    await T.run_until_quiet(s)
    lp = s["goals"].get_loop(gid)
    assert lp["state"] not in ("blocked", "failed") and lp["next_check_at"], lp
    assert T.job_errors(db) == []
    fin = [q for q in db.all("SELECT status, result, candidate_domains FROM questions WHERE goal_id=?", (gid,)) if "finance" in json.loads(q["candidate_domains"])]
    assert fin and fin[0]["status"] == "failed" and json.loads(fin[0]["result"])["outcome"] == "no_authorized_holders"
    assert gid in s["goals"].due_ticks(now=plus_seconds(DAY))


# ------------------------------------------------------------------------------------------------ 10
async def test_max_concurrent_questions_holds_for_spawned_questions(db, org, auth, authz):
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    goal = await _goal(s, authz.principal_for_user(o["petra"]["user_id"]), o)
    gid = goal["goal_id"]
    async with db.tx() as c:
        c.execute("UPDATE goal_loops SET config=? WHERE goal_id=?", (json.dumps({"max_concurrent_questions": 1, "check_interval_seconds": 300, "cooldown_seconds": 900}), gid))
    live = lambda: int(db.scalar(f"SELECT COUNT(*) FROM questions WHERE goal_id=? AND status IN ({','.join('?' * len(LIVE_STATUSES))})", (gid, *LIVE_STATUSES), 0))  # noqa: E731
    peak = 0
    for _ in range(600):
        ran = await s["worker"].run_once()
        peak = max(peak, live())
        if not ran:
            nxt = s["jobs"].next_available_at()
            if nxt is None:
                break
            await asyncio.sleep(max(0.0, min(0.5, (parse_iso(nxt) - utcnow()).total_seconds() + 0.01)))
    total = int(db.scalar("SELECT COUNT(*) FROM questions WHERE goal_id=?", (gid,), 0))
    assert peak == 1 and total > 3, (peak, total)
    assert T.job_errors(db) == []


# ------------------------------------------------------------------------------------------------ extras
async def test_unresolved_conflict_leaves_hypotheses(db, org, auth, authz):
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    t, p, sysp = o["t"], authz.principal_for_user(o["lead"]["user_id"]), authz.principal_for_system(o["t"])
    q = {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "policy": {"visibility": "unit"}}
    await ks.upsert_refs(t, o["ha"]["holder_id"], [{"ref_id": "a", "source_root_id": "rA", "observed_at": plus_seconds(-DAY)}])
    await ks.upsert_refs(t, o["hb"]["holder_id"], [{"ref_id": "b", "source_root_id": "rB", "observed_at": plus_seconds(-DAY)}])
    ca, _ = await ks.commit_claim(p, _cand(o, "The rotation has 4 engineers"), evidence=[{"ref_id": "a"}], question=q, idempotency_key="ka")
    cb, _ = await ks.commit_claim(p, _cand(o, "The rotation has 6 engineers"), evidence=[{"ref_id": "b"}], question=q, idempotency_key="kb", conflict_with=[ca["claim_id"]])
    k = ks.conflicts_for_claim(ca["claim_id"])[0]
    await ks.resolve_conflict(sysp, k["conflict_id"], "unresolved", note="cannot tell")
    assert ks.get_claim(ca["claim_id"])["status"] == ks.get_claim(cb["claim_id"])["status"] == "hypothesis"


async def test_subgoal_rollup_ignores_unmeasured_subgoals(db, org, auth, authz):
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    p = authz.principal_for_user(o["petra"]["user_id"])
    parent = await s["goals"].create_goal(p, {"title": "parent", "objective": "o", "owner_type": "unit", "owner_id": o["region"]["unit_id"]})
    subs = []
    for i in range(2):
        subs.append(await s["goals"].create_goal(p, {"title": f"sub {i}", "objective": "o", "owner_type": "unit", "owner_id": o["region"]["unit_id"],
                                                     "parent_goal_id": parent["goal_id"], "success_criteria": [{"metric": "m", "target": 10, "direction": "increase"}],
                                                     "baseline": {"m": 0}}))
    await s["goals"].add_outcome(p, subs[0]["goal_id"], kind="measurement", value={"metric": "m", "value": 10})
    await s["goals"].recompute_progress(subs[0]["goal_id"])
    prog = s["goals"].get_goal(parent["goal_id"])["progress"]
    assert prog["known"] is True and prog["value"] == 1.0 and prog["partial"] is True and prog["unmeasured_subgoal_ids"] == [subs[1]["goal_id"]]


# ------------------------------------------------------------------------------------------------ holder / model review
async def test_metered_embeddings_reach_the_usage_ledger():
    from mycelic.models.base import ModelError
    from mycelic.models.ledger import MemoryUsageLedger
    from mycelic.models.router import MeteredEmbeddings

    class Paid:
        name, model, dim = "openai", "text-embedding-3-small", 3
        fail = False

        async def embed_with_usage(self, texts):
            if self.fail:
                raise ModelError("endpoint down")
            return [[0.1, 0.2, 0.3] for _ in texts], 40

    class Hash:
        name, model, dim = "hash", "hash-bow-256", 256

        async def embed(self, texts):
            return [[0.0] * 256 for _ in texts]

    ledger = MemoryUsageLedger()
    paid = Paid()
    m = MeteredEmbeddings(paid, ledger, tenant_id="t1")
    assert len(await m.embed(["a", "b"])) == 2
    t = ledger.totals("t1")
    assert t["calls"] == 1 and t["input_tokens"] == 40 and t["cost_usd"] > 0 and "embed" in t["by_tier"]
    paid.fail = True
    try:
        await m.embed(["c"])
        raise AssertionError("expected a ModelError")
    except ModelError:
        pass
    assert ledger.totals("t1")["failures"] == 1
    await MeteredEmbeddings(Hash(), ledger, tenant_id="t1").embed(["free"])
    assert ledger.totals("t1")["calls"] == 2                 # the deterministic hash embedder costs nothing and is not recorded


async def test_agent_ignores_malformed_model_citations(db, org, auth, authz):
    from mycelic.agents.service import AgentService
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    p = authz.principal_for_user(o["lead"]["user_id"])
    q = {"tenant_id": o["t"], "scope_unit_id": o["dept"]["unit_id"], "policy": {"visibility": "unit"}}
    await ks.upsert_refs(o["t"], o["ha"]["holder_id"], [{"ref_id": "r1", "source_root_id": "rA", "observed_at": plus_seconds(-DAY)}])
    claim, _ = await ks.commit_claim(p, _cand(o, "Deploy approvals take two days"), evidence=[{"ref_id": "r1"}], question=q, idempotency_key="k1")

    class Router:
        async def run_task(self, task, payload, **kw):
            return {"answer": "Approvals take two days.", "citations": [{"type": "claim", "id": [claim["claim_id"]]}, {"type": "claim", "id": {"x": 1}},
                                                                      "junk", {"type": "claim", "id": "claim_not_in_context"}, {"type": "claim", "id": claim["claim_id"]}]}

    agents = AgentService(db, org, authz, ks, Router())
    chat = await agents.create_chat(p, "unit", o["dept"]["unit_id"])
    out = await agents.send(p, chat["chat_id"], "How long do deploy approvals take?")
    assert [(c["type"], c["id"]) for c in out["citations"]] == [("claim", claim["claim_id"])]
