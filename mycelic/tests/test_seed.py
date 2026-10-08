"""The demonstration seed: fictional organization, every level and role, holders, evidence, the labelled demo goal.

The seed is what the deployed demonstration and the verification scenario start from, so these tests pin its contract:
the exact demo goal, the configurable hierarchy with an omitted level and a multi-team member, a second tenant,
copied and stale evidence, external holders that are not ingested in-process, and idempotence.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mycelic.runtime import build_runtime
from mycelic.seed import HOLDER_A, HOLDER_B, MERIDIAN_SLUG, ORBITAL_SLUG, demo_documents, run_seed
from mycelic.seed.demo import DEMO_GOAL_OBJECTIVE, DEMO_GOAL_TITLE, PERSONAS
from mycelic.seed.scenario import scenario_settings

ROLES = {"employee", "team_lead", "department_lead", "subsidiary_lead", "regional_lead", "executive", "org_admin"}


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = build_runtime(scenario_settings(tmp_path))
    await runtime.start(run_worker=False, run_holders=True)
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_seed_creates_the_labelled_demo_goal_and_both_tenants(rt):
    out = await run_seed(rt, external_holder_keys={HOLDER_A: "key-a", HOLDER_B: "key-b"})
    assert out["created"] is True
    meridian, orbital = rt.org.get_tenant_by_slug(MERIDIAN_SLUG), rt.org.get_tenant_by_slug(ORBITAL_SLUG)
    assert meridian and orbital and meridian["tenant_id"] != orbital["tenant_id"]
    goal = rt.goals.get_goal(out["goal_id"])
    # the exact goal of the brief, clearly labelled as a demonstration
    assert goal["objective"] == ("Identify recurring operational blockers across teams, verify their causes, and propose measurable "
                                 "actions that reduce resolution time.") == DEMO_GOAL_OBJECTIVE
    assert goal["title"] == DEMO_GOAL_TITLE and goal["title"].startswith("[Demo]") and goal["is_demo"]
    assert goal["status"] == "active" and rt.goals.get_loop(goal["goal_id"])["desired"] == "active"       # the loop is on by default
    assert goal["success_criteria"] and goal["baseline"] and goal["measurement_source"] and goal["budget"]["period"] == "day"
    assert len(out["subgoal_ids"]) == 2 and all(rt.goals.get_goal(g)["parent_goal_id"] == goal["goal_id"] for g in out["subgoal_ids"])
    # progress is unknown until a measurement exists (never guessed)
    assert (goal.get("progress") or {}).get("known") in (None, False)


async def test_seed_hierarchy_roles_and_holders(rt):
    out = await run_seed(rt, external_holder_keys={HOLDER_A: "key-a", HOLDER_B: "key-b"})
    tid = out["tenant_id"]
    # the external holders' documents went out over the transport, signed for each holder, durably
    assert sorted(out["delivered_external_documents"]) == sorted(d["doc_id"] for d in out["pending_external_documents"])
    rows = rt.db.all("SELECT subject, payload FROM transport_messages WHERE msg_id LIKE 'ingest:%'")
    assert {r["subject"].split(".")[3] for r in rows} == {d["holder_id"] for d in out["pending_external_documents"]}
    types = {u["type"] for u in rt.org.list_units(tid)}
    assert {"executive", "region", "subsidiary", "department", "team", "project"} <= types
    roles = {m["role"] for m in rt.db.all("SELECT role FROM memberships WHERE tenant_id=?", (tid,))}
    assert ROLES <= roles
    # Diego leads a subsidiary and a team directly under it (a level omitted); Marcus is in a team and a cross-functional project
    marcus = out["users"]["marcus"]
    assert len(rt.db.all("SELECT unit_id FROM memberships WHERE user_id=?", (marcus,))) == 2
    holders = {h["holder_id"]: h for h in rt.org.list_holders(tid)}
    assert {HOLDER_A, HOLDER_B} <= set(holders) and set(out["external_holder_ids"]) == {HOLDER_A, HOLDER_B}
    assert all(holders[h]["mode"] != "embedded" for h in (HOLDER_A, HOLDER_B))
    with_holder = [p for p in PERSONAS if p["domains"]]
    assert len(holders) == len(with_holder)
    # external holders' documents are not ingested by the API process: they are handed to the holder processes
    assert {d["holder_id"] for d in out["pending_external_documents"]} <= {HOLDER_A, HOLDER_B}


async def test_seed_evidence_is_fictional_and_covers_copy_and_staleness(rt):
    await run_seed(rt, external_holder_keys={HOLDER_A: "key-a", HOLDER_B: "key-b"})
    docs = demo_documents()
    assert docs and all(d["text"] for d in docs)
    texts = [d["text"] for d in docs]
    assert len(texts) != len(set(texts)), "an exact copy exists (copied support must not count twice)"
    assert any(d["observed_days_ago"] > 90 for d in docs), "a stale fact exists (older than the freshness policy)"
    assert all("meridian.example" in u["email"] for u in rt.db.all("SELECT email FROM users WHERE tenant_id=(SELECT tenant_id FROM tenants WHERE slug=?)",
                                                                    (MERIDIAN_SLUG,)))


async def test_seed_is_idempotent(rt):
    first = await run_seed(rt, external_holder_keys={HOLDER_A: "key-a", HOLDER_B: "key-b"})
    users = int(rt.db.scalar("SELECT COUNT(*) FROM users", (), 0))
    goals = int(rt.db.scalar("SELECT COUNT(*) FROM goals", (), 0))
    second = await run_seed(rt, external_holder_keys={HOLDER_A: "key-a", HOLDER_B: "key-b"})
    assert second["created"] is False and second["tenant_id"] == first["tenant_id"] and second["goal_id"] == first["goal_id"]
    assert int(rt.db.scalar("SELECT COUNT(*) FROM users", (), 0)) == users and int(rt.db.scalar("SELECT COUNT(*) FROM goals", (), 0)) == goals
