"""Knowledge (claims, evidence refs, support, commit gate, conflicts, discoveries, revisions) and goals (lifecycle,
progress, loop record, budgets). Offline and deterministic."""
from __future__ import annotations

import pytest

from mycelic.goals import GoalService
from mycelic.jobs import JobQueue
from mycelic.knowledge import KnowledgeService, compute_support
from mycelic.util import plus_seconds


async def _org(auth, org):
    reg = await auth.register_tenant(org_name="Acme", slug="acme", admin_email="admin@acme.test", admin_name="Admin", password="password123")
    t, root = reg["tenant"]["tenant_id"], reg["root_unit"]["unit_id"]
    dept = await org.create_unit(t, "department", "Operations", parent_id=root)
    team_a = await org.create_unit(t, "team", "Support", parent_id=dept["unit_id"])
    team_b = await org.create_unit(t, "team", "Logistics", parent_id=dept["unit_id"])
    a = await org.create_user(t, "a@acme.test", "Ana")
    b = await org.create_user(t, "b@acme.test", "Bo")
    lead = await org.create_user(t, "lead@acme.test", "Lee")
    await org.add_membership(t, a["user_id"], team_a["unit_id"], "employee")
    await org.add_membership(t, b["user_id"], team_b["unit_id"], "employee")
    await org.add_membership(t, lead["user_id"], dept["unit_id"], "department_lead")
    ha, _ = await org.register_holder(t, owner_type="user", owner_id=a["user_id"], name="Ana's notes", domains=["support"])
    hb, _ = await org.register_holder(t, owner_type="user", owner_id=b["user_id"], name="Bo's notes", domains=["logistics"])
    return {"t": t, "root": root, "dept": dept, "team_a": team_a, "team_b": team_b, "a": a, "b": b, "lead": lead, "ha": ha, "hb": hb, "admin": reg["user"]}


def test_compute_support_counts_roots_not_copies():
    refs = [
        {"ref_id": "r1", "holder_id": "h1", "source_root_id": "root_x", "root_known": 1, "status": "active", "role": "supports"},
        {"ref_id": "r2", "holder_id": "h2", "source_root_id": "root_x", "root_known": 1, "status": "active", "role": "supports"},   # copy in another holder
        {"ref_id": "r3", "holder_id": "h2", "source_root_id": "root_y", "root_known": 1, "status": "active", "role": "supports"},
        {"ref_id": "r4", "holder_id": "h3", "source_root_id": None, "root_known": 0, "status": "active", "role": "supports"},       # unknown independence
        {"ref_id": "r5", "holder_id": "h3", "source_root_id": "root_z", "root_known": 1, "status": "active", "role": "contradicts"},
    ]
    s = compute_support(refs)
    assert s["independent_roots"] == 2 and s["copied_refs"] == 1 and s["unknown_independence"] == 1
    assert s["shared_dependencies"][0]["source_root_id"] == "root_x" and s["contradicting_refs"] == ["r5"]


async def test_commit_gate_statuses_and_conflicts(db, org, auth, authz):
    o = await _org(auth, org)
    ks = KnowledgeService(db, org, authz)
    p_lead = authz.principal_for_user(o["lead"]["user_id"])
    p_a = authz.principal_for_user(o["a"]["user_id"])
    p_b = authz.principal_for_user(o["b"]["user_id"])
    t = o["t"]
    old = plus_seconds(-200 * 86400)
    await ks.upsert_refs(t, o["ha"]["holder_id"], [
        {"ref_id": "ev_a1", "source_root_id": "root_deploy", "kind": "note", "title": "Support retro", "disclosed_excerpt": "Deploy approvals take two days", "observed_at": plus_seconds(-3 * 86400)},
        {"ref_id": "ev_a_old", "source_root_id": "root_old", "kind": "note", "title": "Old note", "disclosed_excerpt": "On-call has 4 engineers", "observed_at": old, "freshness_at": old},
    ])
    await ks.upsert_refs(t, o["hb"]["holder_id"], [
        {"ref_id": "ev_b1", "source_root_id": "root_deploy_b", "kind": "note", "title": "Logistics log", "disclosed_excerpt": "Approvals delay releases", "observed_at": plus_seconds(-2 * 86400)},
        {"ref_id": "ev_b_copy", "source_root_id": "root_deploy", "kind": "note", "title": "Forwarded retro", "disclosed_excerpt": "Deploy approvals take two days", "observed_at": plus_seconds(-1 * 86400)},
        {"ref_id": "ev_b_new", "source_root_id": "root_oncall_b", "kind": "note", "title": "Rota", "disclosed_excerpt": "On-call has 6 engineers", "observed_at": plus_seconds(-1 * 86400)},
    ])
    q = {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "policy": {"visibility": "unit"}}
    cand = {"tenant_id": t, "scope_unit_id": o["dept"]["unit_id"], "visibility": "unit", "text": "Deploy approvals delay releases by about two days", "kind": "finding",
            "confidence": 0.8, "created_by_type": "loop", "created_by_id": "loop"}
    # copy only: one root -> hypothesis
    claim, res = await ks.commit_claim(p_lead, cand, evidence=[{"ref_id": "ev_a1"}, {"ref_id": "ev_b_copy"}], question=q, idempotency_key="k1")
    assert res.ok and claim["status"] == "hypothesis" and res.support["independent_roots"] == 1 and res.support["copied_refs"] == 1
    # replay is idempotent
    again, res2 = await ks.commit_claim(p_lead, cand, evidence=[{"ref_id": "ev_a1"}], question=q, idempotency_key="k1")
    assert again["claim_id"] == claim["claim_id"] and "idempotent replay" in res2.reasons
    # two roots -> supported
    claim2, res = await ks.commit_claim(p_lead, cand, evidence=[{"ref_id": "ev_a1"}, {"ref_id": "ev_b1"}, {"ref_id": "ev_b_copy"}], question=q, idempotency_key="k2")
    assert claim2["status"] == "supported" and res.support["independent_roots"] == 2
    # stale evidence -> stale
    stale_cand = {**cand, "text": "On-call rotation has 4 engineers"}
    claim3, res = await ks.commit_claim(p_lead, stale_cand, evidence=[{"ref_id": "ev_a_old"}], question=q, idempotency_key="k3")
    assert claim3["status"] == "stale"
    # contradiction -> contested + conflict object
    claim4, res = await ks.commit_claim(p_lead, {**cand, "text": "On-call rotation has 6 engineers"}, evidence=[{"ref_id": "ev_b_new"}], question=q, idempotency_key="k4",
                                        conflict_with=[claim3["claim_id"]], conflict_summary="records disagree on rota size")
    assert claim4["status"] == "contested"
    confs = ks.conflicts_for_claim(claim3["claim_id"])
    assert len(confs) == 1 and confs[0]["status"] == "open"
    assert ks.get_claim(claim3["claim_id"])["status"] == "contested"
    # rejected: employee cannot write into another team's scope
    bad, res = await ks.commit_claim(p_a, {**cand, "scope_unit_id": o["team_b"]["unit_id"]}, evidence=[{"ref_id": "ev_a1"}], question=q, idempotency_key="k5")
    assert bad is None and not res.ok and res.checks["authorization"] is False
    # visibility: Ana (team member under dept) cannot see dept-scoped claim; lead can; Bo cannot
    assert authz.can_view_scoped(p_lead, claim2, resource_type="claim")
    assert not authz.can_view_scoped(p_a, claim2, resource_type="claim")
    assert len(ks.list_claims(p_lead)) == 4 and ks.list_claims(p_b) == []
    # resolve conflict -> b wins -> claim3 retracted, claim4 recomputed (one root -> hypothesis)
    winner = "a_wins" if confs[0]["claim_a_id"] == claim4["claim_id"] else "b_wins"   # the newer rota record wins
    k = await ks.resolve_conflict(p_lead, confs[0]["conflict_id"], winner, note="rota doc is newer")
    assert k["status"] == "resolved"
    assert ks.get_claim(claim3["claim_id"])["status"] == "retracted"
    assert ks.get_claim(claim4["claim_id"])["status"] == "hypothesis"
    # evidence revision marks derived claims stale
    out = await ks.on_evidence_event(authz.principal_for_system(t), t, o["hb"]["holder_id"], "revised", ["ev_b1"], new_source_root_id="root_deploy_b2")
    assert claim2["claim_id"] in out["affected_claim_ids"] and ks.get_claim(claim2["claim_id"])["status"] == "stale"
    revs = ks.revisions_for("claim", claim2["claim_id"])
    assert [r["version"] for r in revs] == [1, 2]
    # discovery + review + escalation to the department's parent (root)
    disc = await ks.create_discovery(p_lead, t, title="Approvals slow releases", summary="Supported: ...", kind="finding", claim_ids=[claim2["claim_id"]],
                                     scope_unit_id=o["dept"]["unit_id"], idempotency_key="d1")
    same = await ks.create_discovery(p_lead, t, title="x", summary="y", kind="finding", claim_ids=[], scope_unit_id=o["dept"]["unit_id"], idempotency_key="d1")
    assert same["discovery_id"] == disc["discovery_id"] and disc["level"] == "department"
    detail = ks.discovery_detail(p_lead, disc["discovery_id"])
    assert detail["evidence"] and all("disclosed_excerpt" in e for e in detail["evidence"]) and detail["lineage"]["nodes"]
    esc = await ks.review_discovery(p_lead, disc["discovery_id"], "escalated", note="needs exec attention")
    assert esc["status"] == "escalated" and esc["escalated_to"] == o["root"]
    with pytest.raises(Exception):
        ks.discovery_detail(p_b, disc["discovery_id"])
    assert ks.evidence_freshness(t)["total"] == 4   # ev_b1 was revised and is no longer counted as active


async def test_goals_lifecycle_progress_and_loop(db, org, auth, authz):
    o = await _org(auth, org)
    jobs = JobQueue(db)
    gs = GoalService(db, org, authz, jobs, heartbeat_seconds=10)
    p_lead = authz.principal_for_user(o["lead"]["user_id"])
    p_a = authz.principal_for_user(o["a"]["user_id"])
    g = await gs.create_goal(p_lead, {"title": "Cut resolution time", "objective": "Reduce ticket resolution time", "owner_type": "unit", "owner_id": o["dept"]["unit_id"],
                                       "success_criteria": [{"metric": "resolution_time_hours", "target": 24, "direction": "decrease"}], "baseline": {"resolution_time_hours": 72},
                                       "assignees": [{"type": "user", "id": o["a"]["user_id"]}]})
    assert g["status"] == "draft" and g["progress"]["known"] is False and g["budget"]["questions"] == 40 and g["loop"] is None
    with pytest.raises(Exception):
        await gs.create_goal(p_a, {"title": "x", "objective": "y", "owner_type": "unit", "owner_id": o["team_b"]["unit_id"]})
    g = await gs.action(p_lead, g["goal_id"], "activate")
    assert g["status"] == "active" and g["loop"]["desired"] == "active" and g["loop"]["state"] == "waiting" and g["loop"]["active_indicator"]["active"] is False
    assert jobs.counts()["queued"] == 1
    # a second activate/run_now does not duplicate the runnable tick
    await gs.loop_control(p_lead, g["goal_id"], "resume")
    assert jobs.counts()["queued"] == 1
    # progress from a measurement against baseline
    r = await gs.add_outcome(p_a, g["goal_id"], kind="measurement", value={"metric": "resolution_time_hours", "value": 48})
    assert r["progress"]["known"] and r["progress"]["value"] == 0.5
    # decompose: subgoal inherits scope; roll-up when parent has no measurement
    g2 = await gs.create_goal(p_lead, {"title": "Parent", "objective": "o", "owner_type": "unit", "owner_id": o["dept"]["unit_id"]})
    out = await gs.action(p_lead, g2["goal_id"], "decompose", {"subgoals": [{"title": "S1", "objective": "o", "success_criteria": [{"metric": "m", "target": 1, "direction": "reach"}]}]})
    sub = out["subgoals"][0]
    assert sub["parent_goal_id"] == g2["goal_id"] and sub["scope_unit_id"] == o["dept"]["unit_id"]
    await gs.add_outcome(p_lead, sub["goal_id"], kind="milestone", value={"metric": "m"})
    assert gs.get_goal(g2["goal_id"])["progress"] == pytest.approx({"known": True, "value": 1.0}, abs=0) or gs.get_goal(g2["goal_id"])["progress"]["method"] == "subgoals"
    # delegate keeps the original owner's rights via a grant
    g3 = await gs.create_goal(p_a, {"title": "Mine", "objective": "o"})
    await gs.action(p_a, g3["goal_id"], "delegate", {"owner_type": "user", "owner_id": o["b"]["user_id"]})
    p_a2 = authz.principal_for_user(o["a"]["user_id"])
    assert authz.can_manage_goal(p_a2, gs.get_goal(g3["goal_id"]))
    # budget accounting and loop state
    st = await gs.charge_budget(g["goal_id"], tokens=10, usd=0.01, questions=40)
    assert st["exhausted"] and "questions" in st["exhausted_on"]
    await gs.set_loop_state(g["goal_id"], "budget_exhausted", "question budget used", worker_id="w1", ran=True)
    lv = gs.loop_view(g["goal_id"])
    assert lv["state"] == "budget_exhausted" and lv["run_count"] == 1 and lv["budget_remaining"]["questions"] == 0
    # pause cancels queued ticks; visibility of goals for Bo (other team) is denied, for assignee Ana allowed
    await gs.loop_control(p_lead, g["goal_id"], "pause")
    assert jobs.counts().get("queued", 0) == 0 and gs.get_loop(g["goal_id"])["state"] == "paused"
    assert [x["goal_id"] for x in gs.list_goals(p_a, mine=True)] == [g["goal_id"], g3["goal_id"]] or g["goal_id"] in [x["goal_id"] for x in gs.list_goals(p_a, mine=True)]
    p_b = authz.principal_for_user(o["b"]["user_id"])
    assert g["goal_id"] not in [x["goal_id"] for x in gs.list_goals(p_b)]
