"""End-to-end discovery loop with the deterministic fake model, an in-memory transport and scripted holders.

This exercises the coordinator side of the loop in isolation from the evidence-store implementation: holders
here are tiny scripted responders that follow the same answer_from_evidence rule. The full-stack scenario
(real EvidenceStore + SQLite transport + subprocess holders) lives in the `scenario` command.
"""
from __future__ import annotations

import json

import asyncio
from typing import Any

import pytest

from mycelic.config import Settings
from mycelic.discovery import DiscoveryWorker, LoopEngine
from mycelic.goals import GoalService
from mycelic.inquiry import QuestionService
from mycelic.jobs import JobQueue
from mycelic.knowledge import KnowledgeService
from mycelic.models.fake import FakeProvider, content_tokens
from mycelic.models.ledger import SqliteUsageLedger
from mycelic.models.router import DefaultModelRouter
from mycelic.transport import Envelope, Subjects
from mycelic.transport.base import Subscription
from mycelic.util import fingerprint, plus_seconds


class MemoryTransport:
    """In-memory Transport: publish delivers synchronously to matching subscribers (at-least-once, idempotent handlers)."""
    name = "memory"

    def __init__(self) -> None:
        self.subs: list[tuple[str, str, Any]] = []
        self.published: list[Envelope] = []
        self.seen: set[str] = set()

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    def _matches(self, pattern: str, subject: str) -> bool:
        return subject == pattern or (pattern.endswith(".>") and subject.startswith(pattern[:-1]))

    async def publish(self, env: Envelope) -> bool:
        if env.msg_id in self.seen:
            return False
        self.seen.add(env.msg_id)
        self.published.append(env)
        for pattern, _consumer, handler in list(self.subs):
            if self._matches(pattern, env.subject):
                await handler(env)
        return True

    async def subscribe(self, subject: str, *, consumer: str, handler: Any, ack_wait: float = 60.0) -> Subscription:
        self.subs.append((subject, consumer, handler))
        return Subscription(subject, consumer)

    async def request(self, env: Envelope, *, timeout: float = 10.0) -> Envelope:
        raise RuntimeError("not used")

    async def reply(self, request: Envelope, payload: dict, *, kind: str = "reply") -> None: ...
    async def stats(self) -> dict: return {"published": len(self.published)}
    async def prune(self) -> int: return 0


class ScriptedHolder:
    """Answers a question from its documents with the fake rule (>= 2 shared content tokens), signing its envelopes."""

    def __init__(self, transport: MemoryTransport, holder: dict, route_key: str, docs: list[dict]) -> None:
        self.t, self.holder, self.route_key, self.docs = transport, holder, route_key, docs
        self.answered: list[str] = []

    async def start(self) -> None:
        await self.t.subscribe(Subjects.holder_inbox(self.holder["tenant_id"], self.holder["holder_id"]), consumer=f"h-{self.holder['holder_id']}", handler=self.on_question)

    async def on_question(self, env: Envelope) -> None:
        assert env.verify(self.route_key), "unsigned question envelope"
        q = env.payload
        qt = set(content_tokens(q["text"]))
        kept = [d for d in self.docs if len(qt & set(content_tokens(d["text"]))) >= 2][:3]
        refs = [{"ref_id": f"ev_{self.holder['holder_id'][-6:]}_{i}", "source_root_id": fingerprint(d["text"]), "root_known": True, "kind": "note", "title": d["title"],
                 "disclosed_excerpt": d["text"][:480], "disclosure_level": "excerpt", "observed_at": d["observed_at"], "freshness_at": d["observed_at"]} for i, d in enumerate(kept)]
        payload = {"question_id": q["question_id"], "holder_id": self.holder["holder_id"], "route_id": q.get("route_id"),
                   "status": "answered" if kept else "no_evidence", "content": " ".join(d["text"] for d in kept), "confidence": min(0.9, 0.5 + 0.15 * len(kept)) if kept else None,
                   "evidence_refs": refs, "provenance": {"retrieval_operator": "scripted"}, "freshness_at": max((d["observed_at"] for d in kept), default=None)}
        self.answered.append(q["question_id"])
        resp = Envelope.new(Subjects.responses(env.tenant_id), "response", env.tenant_id, payload, msg_id=f"resp:{q['question_id']}:{self.holder['holder_id']}").sign(self.route_key)
        await self.t.publish(resp)


def job_errors(db) -> list[str]:
    """Errors any job raised (a lost lease is the queue working, not an error)."""
    return [r["last_error"] for r in db.all("SELECT last_error FROM jobs WHERE last_error IS NOT NULL AND last_error NOT LIKE 'lease expired%'")]


async def run_until_quiet(s, *, rounds: int = 40) -> int:
    """Drain the durable queue, sleeping through short delays (question timeouts) until nothing is queued or leased."""
    from mycelic.util import parse_iso, utcnow
    total = 0
    for _ in range(rounds):
        total += await s["worker"].drain(max_jobs=500)
        counts = s["jobs"].counts()
        if not counts.get("queued") and not counts.get("leased"):
            break
        nxt = s["jobs"].next_available_at()
        if nxt:
            delay = (parse_iso(nxt) - utcnow()).total_seconds()
            if delay > 0:
                await asyncio.sleep(min(delay + 0.01, 1.0))
    return total


async def build(db, org, auth, authz, *, timeout: float = 0.3):
    jobs = JobQueue(db)
    knowledge = KnowledgeService(db, org, authz)
    settings = Settings()
    router = DefaultModelRouter({"fake": FakeProvider()}, settings.model_tiers, SqliteUsageLedger(db))
    loop_defaults = {"check_interval_seconds": 300, "max_concurrent_questions": 3, "cooldown_seconds": 900, "max_followup_depth": 2, "question_timeout_seconds": timeout}
    goals = GoalService(db, org, authz, jobs, loop_defaults=loop_defaults, heartbeat_seconds=10)
    transport = MemoryTransport()
    questions = QuestionService(db, org, authz, jobs, goals, knowledge, transport, question_timeout_seconds=timeout, cooldown_seconds=900, max_followup_depth=2)
    engine = LoopEngine(db, org, authz, jobs, goals, knowledge, questions, router, transport, loop_defaults=loop_defaults)
    worker = DiscoveryWorker(db, engine, jobs, goals, transport, concurrency=1, lease_seconds=30, heartbeat_seconds=10, poll_seconds=0.05, worker_id="w-test")
    await transport.subscribe(Subjects.all_core_inbound(), consumer="core", handler=engine.on_transport)
    return dict(jobs=jobs, knowledge=knowledge, router=router, goals=goals, transport=transport, questions=questions, engine=engine, worker=worker)


DAY = 86400
DOCS = {
    "elin": [{"title": "Support retrospective, week 36", "text": "Recurring blocker: deploy approvals for hotfixes take two days because the change board meets twice a week. Tickets stay open while we wait for the approval.", "observed_at": plus_seconds(-3 * DAY)}],
    "ana": [{"title": "Forwarded: Support retrospective, week 36", "text": "Recurring blocker: deploy approvals for hotfixes take two days because the change board meets twice a week. Tickets stay open while we wait for the approval.", "observed_at": plus_seconds(-1 * DAY)}],
    "priya": [{"title": "Change board notes", "text": "Recurring operational blocker for hotfix deployments: the change board approves deploys on Tuesdays and Thursdays; deploy approvals take two days on average.", "observed_at": plus_seconds(-5 * DAY)}],
    "noor": [{"title": "On-call roster (January)", "text": "Recurring on-call blocker: the on-call rotation has 4 engineers and pages take 45 minutes to acknowledge.", "observed_at": plus_seconds(-200 * DAY)}],
    "jonas": [{"title": "On-call roster (current)", "text": "Recurring on-call blocker: the on-call rotation has 6 engineers and pages take 20 minutes to acknowledge.", "observed_at": plus_seconds(-1 * DAY)}],
    "rafael": [{"title": "Customs clearance delays", "text": "Recurring customs blocker: customs paperwork delays cross-border shipments by a day; the approval from the customs broker is the blocker.", "observed_at": plus_seconds(-4 * DAY)}],
}
DOMAINS = {"elin": ["support", "deployments"], "ana": ["deployments"], "priya": ["deployments", "approvals"], "noor": ["on-call"], "jonas": ["dispatch", "approvals", "on-call"], "rafael": ["customs"]}


async def seed_org(org, auth, authz, transport):
    reg = await auth.register_tenant(org_name="Meridian", slug="meridian", admin_email="admin@m.test", admin_name="Tomas", password="password123")
    t, root = reg["tenant"]["tenant_id"], reg["root_unit"]["unit_id"]
    region = await org.create_unit(t, "region", "Europe", parent_id=root)
    nordics = await org.create_unit(t, "subsidiary", "Nordics", parent_id=region["unit_id"])
    ops = await org.create_unit(t, "department", "Operations", parent_id=nordics["unit_id"])
    eng = await org.create_unit(t, "department", "Engineering", parent_id=nordics["unit_id"])
    support = await org.create_unit(t, "team", "Customer Support", parent_id=ops["unit_id"])
    dispatch = await org.create_unit(t, "team", "Fleet Dispatch", parent_id=ops["unit_id"])
    platform = await org.create_unit(t, "team", "Platform", parent_id=eng["unit_id"])
    sre = await org.create_unit(t, "team", "Site Reliability", parent_id=eng["unit_id"])
    iberia = await org.create_unit(t, "subsidiary", "Iberia", parent_id=region["unit_id"])
    iberia_ops = await org.create_unit(t, "team", "Iberia Operations", parent_id=iberia["unit_id"])
    units = {"elin": support, "ana": platform, "priya": platform, "noor": sre, "jonas": dispatch, "rafael": iberia_ops}
    users, holders, scripted = {}, {}, {}
    for name, unit in units.items():
        u = await org.create_user(t, f"{name}@m.test", name.title())
        await org.add_membership(t, u["user_id"], unit["unit_id"], "team_lead" if name in ("priya", "jonas") else "employee")
        h, _key = await org.register_holder(t, owner_type="user", owner_id=u["user_id"], name=f"{name.title()}'s notes", domains=DOMAINS[name])
        row = org.holder_secret_row(h["holder_id"])
        sh = ScriptedHolder(transport, h, row["route_key"], DOCS[name])
        await sh.start()
        users[name], holders[name], scripted[name] = u, h, sh
    petra = await org.create_user(t, "petra@m.test", "Petra")
    await org.add_membership(t, petra["user_id"], region["unit_id"], "regional_lead")
    return {"t": t, "root": root, "region": region, "nordics": nordics, "ops": ops, "units": units, "users": users, "holders": holders, "scripted": scripted, "petra": petra, "admin": reg["user"]}


async def test_loop_end_to_end(db, org, auth, authz):
    s = await build(db, org, auth, authz)
    o = await seed_org(org, auth, authz, s["transport"])
    p_petra = authz.principal_for_user(o["petra"]["user_id"])
    goal = await s["goals"].create_goal(p_petra, {"title": "[Demo] Identify recurring operational blockers", "objective": "Identify recurring operational blockers across teams, verify their causes, and propose measurable actions that reduce resolution time",
                                                  "owner_type": "unit", "owner_id": o["region"]["unit_id"], "success_criteria": [{"metric": "resolution_time_hours", "target": 24, "direction": "decrease"}],
                                                  "baseline": {"resolution_time_hours": 52}, "budget": {"tokens": 500000, "usd": 5, "questions": 40, "followup_depth": 2}}, activate=True)
    assert goal["status"] == "active" and goal["loop"]["state"] == "waiting"
    # drive the durable queue until nothing is runnable: tick -> questions -> route -> (holders answer inline) -> collect -> evaluate -> commit -> next tick ...
    await run_until_quiet(s)
    assert job_errors(db) == [] and s["jobs"].counts().get("dead", 0) == 0
    qs = s["questions"].list(authz.principal_for_system(o["t"]), goal_id=goal["goal_id"], limit=200)
    assert qs, "the loop generated questions automatically"
    assert all(q["asker"]["type"] == "loop" for q in qs) and all(q["priority_breakdown"]["method"] == "heuristic" for q in qs)
    by_domain = {tuple(q["candidate_domains"]): q for q in qs if q["depth"] == 0 and q["kind"] == "gap"}
    assert ("deployments",) in by_domain and ("on-call",) in by_domain and ("customs",) in by_domain
    # separate holders responded from their own stores
    dep_q = by_domain[("deployments",)]
    responders = {r["holder_id"] for r in s["questions"].responses(dep_q["question_id"]) if r["status"] == "answered"}
    assert responders == {o["holders"]["elin"]["holder_id"], o["holders"]["ana"]["holder_id"], o["holders"]["priya"]["holder_id"]}
    assert dep_q["status"] == "committed"
    claims = s["knowledge"].list_claims(authz.principal_for_system(o["t"]), question_id=dep_q["question_id"])
    supported = [c for c in claims if c["status"] == "supported"]
    assert supported, [ (c["status"], c["support"]) for c in claims ]
    sup = supported[0]["support"]
    # the forwarded copy shares Elin's root: three references, two independent roots, one copied
    assert sup["independent_roots"] == 2 and sup["copied_refs"] == 1 and sup["unknown_independence"] == 0
    # a supported discovery exists with a useful follow-up and a proposed action outcome
    discs = s["knowledge"].list_discoveries(p_petra, goal_id=goal["goal_id"])
    dep_disc = next(d for d in discs if d["question_id"] == dep_q["question_id"])
    assert dep_disc["kind"] == "finding" and dep_disc["support"]["independent_roots"] == 2
    detail = s["knowledge"].discovery_detail(p_petra, dep_disc["discovery_id"])
    assert detail["evidence"] and all("disclosed_excerpt" in e for e in detail["evidence"]) and detail["lineage"]["edges"]
    outs = s["goals"].outcomes(goal["goal_id"])
    assert any(x["kind"] == "action" and x["value"].get("proposed") for x in outs)
    # the on-call responses disagree on numbers: two contested claims and a conflict object
    oc_q = by_domain[("on-call",)]
    oc_claims = s["knowledge"].list_claims(authz.principal_for_system(o["t"]), question_id=oc_q["question_id"])
    assert {c["status"] for c in oc_claims} == {"contested"} and len(oc_claims) == 2
    conflicts = s["knowledge"].list_conflicts(p_petra, goal_id=goal["goal_id"])
    assert len(conflicts) == 1 and conflicts[0]["status"] in ("open", "investigating")
    # the contradiction follow-up investigated the existing conflict instead of opening a second one
    assert any(step["step"] == "responses" for step in conflicts[0]["investigation"]) or conflicts[0]["status"] == "open"
    oc_disc = next(d for d in discs if d["question_id"] == oc_q["question_id"])
    assert oc_disc["kind"] == "contradiction"
    # customs: a single source -> hypothesis, blind verification excludes the supporter -> no authorized holder -> failed, uncertainty retained
    cu_q = by_domain[("customs",)]
    cu_claims = s["knowledge"].list_claims(authz.principal_for_system(o["t"]), question_id=cu_q["question_id"])
    assert cu_claims and cu_claims[0]["status"] == "hypothesis"
    ver = [q for q in qs if q["kind"] == "verification" and q["parent_question_id"] == cu_q["question_id"]]
    assert ver and ver[0]["status"] == "failed" and ver[0]["result"]["outcome"] == "no_independent_holders"
    assert o["holders"]["rafael"]["holder_id"] in ver[0]["policy"]["exclude_holder_ids"]
    # every verification question is blind: tied to its target claim, worded without the claim's numbers or wording
    from mycelic.inquiry.service import _longest_run, _NUM
    all_q = db.all("SELECT question_id, kind, text, trigger, policy FROM questions WHERE goal_id=?", (goal["goal_id"],))
    for row in all_q:
        trig, pol = json.loads(row["trigger"]), json.loads(row["policy"])
        assert not row["text"].endswith("(follow-up)")
        if row["kind"] == "verification":
            target = s["knowledge"].get_claim(trig["claim_id"])
            assert pol["blind_verification"] is True and target is not None
            assert not (set(_NUM.findall(row["text"])) & set(_NUM.findall(target["text"]))) and _longest_run(row["text"], target["text"]) < 6
        if row["kind"] == "contradiction":
            assert trig.get("conflict_id") and pol["blind_verification"] is True
            for side in ("claim_a_id", "claim_b_id"):
                cl = s["knowledge"].get_claim(s["knowledge"].get_conflict(trig["conflict_id"])[side])
                assert not (set(_NUM.findall(row["text"])) & set(_NUM.findall(cl["text"])))
    # budget accounting: usage recorded and charged; questions counted
    g = s["goals"].get_goal(goal["goal_id"])
    assert g["budget_spent"]["questions"] >= 3 and g["budget_spent"]["tokens"] > 0
    # progress from the baseline measurement is unknown until a measurement is recorded
    assert g["progress"]["known"] is False
    # vertical abstraction: team member sees only their team's scope; regional lead sees everything under Europe
    p_elin = authz.principal_for_user(o["users"]["elin"]["user_id"])
    assert s["knowledge"].list_discoveries(p_elin, goal_id=goal["goal_id"]) == [] or all(d["scope_unit_id"] in authz.visible_unit_ids(p_elin) for d in s["knowledge"].list_discoveries(p_elin, goal_id=goal["goal_id"]))
    assert len(s["knowledge"].list_discoveries(p_petra, goal_id=goal["goal_id"])) >= 3
    # loop state reflects reality and waits without spending tokens when nothing is useful
    calls_before = len(s["router"].providers["fake"].calls)
    q_before = len(all_q)
    for _ in range(3):
        await s["goals"].enqueue_tick(goal["goal_id"], reason="test", force=True)
        await s["worker"].drain(max_jobs=5)
    lv = s["goals"].loop_view(goal["goal_id"])
    assert lv["state"] in ("waiting", "active"), lv
    # once every target has had its question, scheduled checks cost nothing and re-ask nothing
    assert len(s["router"].providers["fake"].calls) == calls_before, s["router"].providers["fake"].calls[calls_before:]
    assert int(db.scalar("SELECT COUNT(*) FROM questions WHERE goal_id=?", (goal["goal_id"],), 0)) == q_before
    # pause stops ticks; resume restarts; run_now queues an immediate tick
    await s["goals"].loop_control(p_petra, goal["goal_id"], "pause")
    assert s["goals"].get_loop(goal["goal_id"])["state"] == "paused" and s["jobs"].counts().get("queued", 0) == 0
    await s["goals"].loop_control(p_petra, goal["goal_id"], "run_now")
    assert s["jobs"].counts().get("queued", 0) == 1
    await s["worker"].drain(max_jobs=3)
    # budget exhaustion is a real state with an explanation
    await s["goals"].update_goal(p_petra, goal["goal_id"], {"budget": {"tokens": 1, "usd": 5, "questions": 40, "followup_depth": 2}})
    await s["goals"].enqueue_tick(goal["goal_id"], reason="test", force=True)
    await s["worker"].drain(max_jobs=3)
    lv = s["goals"].loop_view(goal["goal_id"])
    assert lv["state"] == "budget_exhausted" and "tokens" in lv["explanation"]
    # stop
    await s["goals"].loop_control(p_petra, goal["goal_id"], "stop")
    assert s["goals"].get_loop(goal["goal_id"])["state"] == "stopped"


async def test_replay_and_restart_do_not_duplicate_effects(db, org, auth, authz):
    s = await build(db, org, auth, authz)
    o = await seed_org(org, auth, authz, s["transport"])
    p_petra = authz.principal_for_user(o["petra"]["user_id"])
    goal = await s["goals"].create_goal(p_petra, {"title": "[Demo] blockers", "objective": "find recurring blockers", "owner_type": "unit", "owner_id": o["region"]["unit_id"]}, activate=True)
    # run the tick and routing; holders answer inline; then process collect
    await s["worker"].drain(max_jobs=1)     # tick
    await s["worker"].drain(max_jobs=3)     # routes
    qs = s["questions"].list(authz.principal_for_system(o["t"]), goal_id=goal["goal_id"])
    dep = next(q for q in qs if q["candidate_domains"] == ["deployments"])
    assert dep["status"] == "collecting" and len([r for r in s["questions"].responses(dep["question_id"])]) == 3
    # duplicate delivery of a response is ignored
    dup = await s["questions"].handle_response({"question_id": dep["question_id"], "holder_id": o["holders"]["elin"]["holder_id"], "status": "answered", "content": "x", "evidence_refs": []},
                                               msg_id=f"resp:{dep['question_id']}:{o['holders']['elin']['holder_id']}")
    assert dup["duplicate"] is True and len(s["questions"].responses(dep["question_id"])) == 3
    # simulate a worker crash: lease the collect job and abandon it; a new worker sweeps the expired lease and resumes
    job = await s["jobs"].lease("w-crashed", 0.01, kinds=["question.collect"])
    assert job is not None
    await asyncio.sleep(0.05)
    assert await s["jobs"].requeue_expired() == 1
    await s["worker"].drain(max_jobs=1)     # collect -> evaluating
    # evaluate twice (replay after a crash between checkpoint and ack): claims are keyed by (question, finding index)
    job = await s["jobs"].lease("w-test", 30, kinds=["question.evaluate"])
    assert job is not None
    await s["engine"].evaluate(job)
    n_claims = len(s["knowledge"].list_claims(authz.principal_for_system(o["t"]), question_id=dep["question_id"], include_retracted=True))
    n_conf = len(s["knowledge"].list_conflicts(authz.principal_for_system(o["t"])))
    await s["engine"].evaluate(job)          # replay
    assert len(s["knowledge"].list_claims(authz.principal_for_system(o["t"]), question_id=dep["question_id"], include_retracted=True)) == n_claims
    assert len(s["knowledge"].list_conflicts(authz.principal_for_system(o["t"]))) == n_conf
    await s["jobs"].complete(job.job_id, "w-test")
    # commit twice -> one discovery
    job = await s["jobs"].lease("w-test", 30, kinds=["question.commit"])
    await s["engine"].commit(job)
    q2 = s["questions"].get(dep["question_id"])
    assert q2["status"] == "committed"
    dcount = lambda: len(s["knowledge"].list_discoveries(authz.principal_for_system(o["t"]), goal_id=goal["goal_id"]))  # noqa: E731
    before = dcount()
    # a replayed commit job for a resolved question is a no-op
    await s["engine"].commit(job)
    assert dcount() == before
    # source revision invalidates the derived claim and schedules re-verification
    claim = s["knowledge"].list_claims(authz.principal_for_system(o["t"]), question_id=dep["question_id"])[0]
    refs = s["knowledge"].refs_for_claim(claim["claim_id"])
    elin_ref = next(r for r in refs if r["holder_id"] == o["holders"]["elin"]["holder_id"])
    env = Envelope.new(Subjects.evidence_events(o["t"]), "evidence_event", o["t"], {"holder_id": o["holders"]["elin"]["holder_id"], "event": "revised", "affected_ref_ids": [elin_ref["ref_id"]], "new_source_root_id": "root_new"},
                       msg_id="evt:1").sign(org.holder_secret_row(o["holders"]["elin"]["holder_id"])["route_key"])
    await s["transport"].publish(env)
    assert s["knowledge"].get_claim(claim["claim_id"])["status"] == "stale"
    assert s["knowledge"].get_ref(elin_ref["ref_id"])["status"] == "revised"
    assert any(j["kind"] == "claim.reverify" for j in s["jobs"].list(status="queued"))
    # an unsigned evidence event from a holder is refused
    bad = Envelope.new(Subjects.evidence_events(o["t"]), "evidence_event", o["t"], {"holder_id": o["holders"]["ana"]["holder_id"], "event": "retracted", "affected_ref_ids": [r["ref_id"] for r in refs]}, msg_id="evt:2")
    bad.signature = "deadbeef"
    await s["transport"].publish(bad)
    assert all(s["knowledge"].get_ref(r["ref_id"])["status"] != "retracted" for r in refs)
    # the re-verification creates a blind verification question with the claim as motivating lineage
    await run_until_quiet(s)
    vqs = [q for q in s["questions"].list(authz.principal_for_system(o["t"]), goal_id=goal["goal_id"], limit=200) if (q.get("trigger") or {}).get("kind") == "evidence_revised"]
    assert vqs, [(j["kind"], j["status"], j["last_error"], j["result"]) for j in s["jobs"].list(limit=30)]
    assert any(q["motivating_lineage"][0]["id"] == claim["claim_id"] for q in vqs)
    # history available for audit: revisions of the claim record the stale transition
    revs = s["knowledge"].revisions_for("claim", claim["claim_id"])
    assert any(r["after"].get("status") == "stale" for r in revs)


async def test_tenant_isolation_and_private_evidence(db, org, auth, authz):
    s = await build(db, org, auth, authz)
    o = await seed_org(org, auth, authz, s["transport"])
    other = await auth.register_tenant(org_name="Orbital", slug="orbital", admin_email="x@o.test", admin_name="X", password="password123")
    p_other = authz.principal_for_user(other["user"]["user_id"])
    p_petra = authz.principal_for_user(o["petra"]["user_id"])
    goal = await s["goals"].create_goal(p_petra, {"title": "[Demo] blockers", "objective": "find recurring blockers", "owner_type": "unit", "owner_id": o["region"]["unit_id"]}, activate=True)
    await run_until_quiet(s)
    sysp = authz.principal_for_system(o["t"])
    assert s["knowledge"].list_claims(sysp, goal_id=goal["goal_id"])
    # the other tenant sees nothing: lists are empty, details are forbidden, questions cannot be created against the goal
    assert s["knowledge"].list_claims(p_other) == [] and s["knowledge"].list_discoveries(p_other) == [] and s["goals"].list_goals(p_other) == []
    disc = s["knowledge"].list_discoveries(p_petra, goal_id=goal["goal_id"])[0]
    with pytest.raises(PermissionError):
        s["knowledge"].discovery_detail(p_other, disc["discovery_id"])
    with pytest.raises(PermissionError):
        await s["questions"].create(p_other, {"text": "What is Meridian doing about deploy approvals?", "goal_id": goal["goal_id"]})
    # a Meridian employee outside the scope of a discovery cannot open it; their own lead can
    p_rafael = authz.principal_for_user(o["users"]["rafael"]["user_id"])
    dep_disc = next(d for d in s["knowledge"].list_discoveries(p_petra, goal_id=goal["goal_id"]) if d["scope_unit_id"] == o["region"]["unit_id"])
    # discoveries are scoped at the goal's scope (Europe): a regional lead sees them, a team employee does not
    assert s["knowledge"].discovery_detail(p_petra, dep_disc["discovery_id"])
    with pytest.raises(PermissionError):
        s["knowledge"].discovery_detail(p_rafael, dep_disc["discovery_id"])
    # private evidence: nobody but the owner (or a raw grant) can read raw evidence; admins are not exempt
    h = o["holders"]["elin"]
    p_admin = authz.principal_for_user(o["admin"]["user_id"])
    p_elin = authz.principal_for_user(o["users"]["elin"]["user_id"])
    assert authz.can_view_raw_evidence(p_elin, h) and not authz.can_view_raw_evidence(p_admin, h) and not authz.can_view_raw_evidence(p_petra, h)
    # visibility SQL never leaks across tenants even with a forged grant row for the other tenant's user
    meridian_claim = s["knowledge"].list_claims(sysp)[0]["claim_id"]
    with pytest.raises(ValueError):    # a grant on another tenant's object is refused at write time
        await org.add_grant(other["tenant"]["tenant_id"], grantor_id=other["user"]["user_id"], grantee_type="user", grantee_id=other["user"]["user_id"],
                            resource_type="claim", resource_id=meridian_claim, level="read")
    # ... and a row forged straight into the database is ignored at read time
    async with db.tx() as c:
        c.execute("INSERT INTO grants(grant_id, tenant_id, grantor_id, grantee_type, grantee_id, resource_type, resource_id, level, reason, status, created_at) "
                  "VALUES ('grant_forged', ?, ?, 'user', ?, 'claim', ?, 'raw', 'forged', 'active', '2026-01-01T00:00:00+00:00')",
                  (o["t"], o["admin"]["user_id"], other["user"]["user_id"], meridian_claim))
    p_other2 = authz.principal_for_user(other["user"]["user_id"])
    assert p_other2.grants == [] and s["knowledge"].list_claims(p_other2) == []
