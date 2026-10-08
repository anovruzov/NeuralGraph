"""Coordinator side of ingestion: who a question's answers will reach, routing by nested domains, domains published from
a holder's ingested records, and evaluation that never lets a finding stand on responses that contradict each other.
"""
from __future__ import annotations

import asyncio

from mycelic.models import fake
from mycelic.models.fake import evaluate_responses
from mycelic.tests import test_loop_engine as T
from mycelic.tests.test_knowledge_goals import _org
from mycelic.tests.test_review_regressions import _goal
from mycelic.util import plus_seconds

DAY = 86400


def _question(o, **kw):
    return {"question_id": "q_test", "tenant_id": o["t"], "scope_unit_id": o["team_a"]["unit_id"], "policy": {"visibility": "unit"},
            "asker_type": "loop", "asker_id": "loop", "candidate_domains": [], **kw}


# ------------------------------------------------------------------------------------------------ audience
async def test_question_audience_is_exactly_who_can_read_its_answers(db, org, auth, authz):
    from mycelic.tests.test_loop_engine import build
    o = await _org(auth, org)
    s = await build(db, org, auth, authz)
    audience = s["questions"].question_audience(_question(o))
    # team members and the leads above the team (and whoever else the authorization rules let read the team's claims,
    # such as the org admin); never a member of another team
    assert audience["complete"] is True and audience["owner"] is False
    assert o["a"]["user_id"] in audience["principal_ids"] and o["lead"]["user_id"] in audience["principal_ids"]
    assert o["b"]["user_id"] not in audience["principal_ids"]
    row = {"tenant_id": o["t"], "visibility": "unit", "scope_unit_id": o["team_a"]["unit_id"]}
    assert set(audience["principal_ids"]) == {u["user_id"] for u in db.all("SELECT user_id FROM users WHERE tenant_id=?", (o["t"],))
                                              if authz.can_view_scoped(authz.principal_for_user(u["user_id"]), row, resource_type="claim")}
    # a new member of the team is in the audience of the next route (the cache follows database writes)
    c = await org.create_user(o["t"], "c@acme.test", "Cy")
    await org.add_membership(o["t"], c["user_id"], o["team_a"]["unit_id"], "employee")
    assert c["user_id"] in s["questions"].question_audience(_question(o))["principal_ids"]
    # a private question asked by a user reaches only that user
    private = s["questions"].question_audience(_question(o, policy={"visibility": "private"}, asker_type="user", asker_id=o["a"]["user_id"]))
    assert private["principal_ids"] == [o["a"]["user_id"]]


async def test_routed_question_carries_its_audience(db, org, auth, authz):
    s = await T.build(db, org, auth, authz)
    o = await T.seed_org(org, auth, authz, s["transport"])
    seen: list[dict] = []
    for sh in o["scripted"].values():
        orig = sh.on_question

        async def spy(env, _orig=orig):
            seen.append(env.payload)
            await _orig(env)
        sh.on_question = spy
    s["transport"].subs = [(pat, cons, next((x.on_question for x in o["scripted"].values() if cons == f"h-{x.holder['holder_id']}"), h))
                           for pat, cons, h in s["transport"].subs]
    await _goal(s, authz.principal_for_user(o["petra"]["user_id"]), o)
    await T.run_until_quiet(s)
    assert seen and all(isinstance(p.get("audience"), dict) and p["audience"]["complete"] is True for p in seen)
    assert all(o["petra"]["user_id"] in p["audience"]["principal_ids"] for p in seen)       # the regional lead reads what her goal produces


# ------------------------------------------------------------------------------------------------ nested domains (E8)
async def test_routing_matches_nested_and_aliased_domains(db, org, auth, authz):
    o = await _org(auth, org)
    holder = {**o["ha"], "domains": ["engineering.dependencies"], "export_policy": {}}
    q = _question(o, asker_type="system")
    assert authz.can_route({**q, "candidate_domains": ["engineering"]}, holder)[0]                     # parent reaches the subdomain
    assert authz.can_route({**q, "candidate_domains": ["engineering.dependencies"]}, holder)[0]
    assert not authz.can_route({**q, "candidate_domains": ["finance"]}, holder)[0]
    legacy = {**o["ha"], "domains": ["deployments"], "export_policy": {}}                              # a flat legacy domain (alias)
    assert authz.can_route({**q, "candidate_domains": ["infrastructure"]}, legacy)[0]
    assert authz.can_route({**q, "candidate_domains": ["deployments"]}, legacy)[0]
    assert not authz.can_route({**q, "candidate_domains": ["customer-support"]}, legacy)[0]


# ------------------------------------------------------------------------------------------------ heartbeat domains
async def test_heartbeat_publishes_only_tenant_taxonomy_domains(db, org, auth, authz):
    o = await _org(auth, org)
    hid = o["ha"]["holder_id"]
    stats = {"ingest": {"domains": {"engineering.dependencies": 12, f"personal.{hid}.reading": 4, "made-up": 9, "unclassified": 3}}}
    await org.holder_heartbeat(hid, stats=stats)
    assert org.get_holder(hid)["domains"] == ["support", "engineering.dependencies"]
    assert db.one("SELECT detail FROM audit_log WHERE action='holder.domains_published' AND resource_id=?", (hid,)) is not None
    await org.holder_heartbeat(hid, stats=stats)                                   # idempotent
    assert org.get_holder(hid)["domains"] == ["support", "engineering.dependencies"]
    # an owner who curates the list by hand turns publication off
    hb = o["hb"]["holder_id"]
    await org.update_holder(hb, export_policy={**(org.get_holder(hb).get("export_policy") or {}), "auto_domains": False})
    await org.holder_heartbeat(hb, stats=stats)
    assert org.get_holder(hb)["domains"] == ["logistics"]


# ------------------------------------------------------------------------------------------------ evaluation
def test_fake_evaluation_never_clusters_responses_that_contradict_each_other():
    texts = ["Recurring on-call blocker: the rotation has 4 engineers and pages wait for acknowledgement.",
             "Recurring on-call blocker: the rotation has 6 engineers and pages wait for acknowledgement.",
             "Recurring on-call blocker: the rotation is short and pages wait for acknowledgement."]
    out = evaluate_responses({"responses": [{"response_id": f"r{i}", "content": t, "refs": [{"ref_id": f"e{i}"}]} for i, t in enumerate(texts)]})
    assert any(set(d["a_response_ids"] + d["b_response_ids"]) == {"r0", "r1"} for d in out["disagreements"])
    # r2 agrees with both, but r0 and r1 never end up in one finding through it
    assert all(not {"r0", "r1"} <= set(f["supporting_response_ids"]) for f in out["findings"])


async def test_disagreement_inside_one_finding_contests_it(db, org, auth, authz, monkeypatch):
    """A model that puts contradicting responses into one finding: the dissenting side becomes its own claim and the
    finding is contested, not supported."""
    s = await T.build(db, org, auth, authz)
    monkeypatch.setitem(T.DOCS, "ana", [{"title": "SRE handbook", "text": "Recurring on-call blocker noted in the SRE handbook: the on-call rotation has 6 engineers and pages take 20 minutes to acknowledge.",
                                         "observed_at": plus_seconds(-2 * DAY)}])
    monkeypatch.setitem(T.DOMAINS, "ana", ["on-call"])

    def lumping(inp):
        out = evaluate_responses(inp)
        if out["disagreements"]:
            everyone = [r for f in out["findings"] for r in f["supporting_response_ids"]] + \
                       [r for d in out["disagreements"] for r in d["a_response_ids"] + d["b_response_ids"]]
            refs = [r for f in out["findings"] for r in f["supporting_ref_ids"]]
            longest = max(out["findings"], key=lambda f: len(f["text"]))
            out["findings"] = [{**longest, "supporting_response_ids": list(dict.fromkeys(everyone)), "supporting_ref_ids": list(dict.fromkeys(refs))}]
        return out
    monkeypatch.setitem(fake.HANDLERS, "evaluate_responses", lumping)
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
    assert len(claims) == 2, [dict(c) for c in claims]                     # the lumped finding + the dissenting side, no copies
    assert {c["status"] for c in claims} == {"contested"}
    dissent = next(c for c in claims if "4 engineers" in c["text"])
    finding = next(c for c in claims if c["claim_id"] != dissent["claim_id"])
    assert "6 engineers" in finding["text"]
    k = db.one("SELECT claim_a_id, claim_b_id FROM conflicts")
    assert {k["claim_a_id"], k["claim_b_id"]} == {dissent["claim_id"], finding["claim_id"]}
