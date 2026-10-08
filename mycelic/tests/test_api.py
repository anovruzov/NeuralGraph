"""HTTP API tests: sessions, tenant isolation, CSRF, goals and loops, questions, holders and documents, SSE, health.

Offline and deterministic: fake model provider, hash embeddings, SQLite transport, embedded holders, no worker in
the API process (``/readyz`` therefore does not require a heartbeat). The client keeps no cookie jar so every request
states its credential explicitly (a ``Cookie`` header or a bearer token) and two tenants can be driven side by side.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import DummyCookieJar
from aiohttp.test_utils import TestClient, TestServer

from mycelic.api.app import create_app
from mycelic.config import Settings
from mycelic.observability import Metrics
from mycelic.runtime import build_runtime
from mycelic.util import j, new_id, now_iso


def make_settings(tmp_path: Path, **override: Any) -> Settings:
    kw: dict[str, Any] = dict(data_dir=tmp_path, host="127.0.0.1", port=0, demo_mode=True, embedded_holders=True, run_worker_in_api=False,
                              transport="sqlite", model_light="fake:mycelic-fake-light", model_standard="fake:mycelic-fake-standard",
                              model_heavy="fake:mycelic-fake-heavy", embed_provider="hash", log_json=False, secure_cookies=False,
                              secret_key="test-secret-key", cors_origins=[], allowed_hosts=[], public_url="")
    kw.update(override)
    s = Settings(**kw)
    s.coord_db = str(Path(tmp_path) / "coord.db")
    s.holders_dir = str(Path(tmp_path) / "holders")
    return s


class Api:
    """Thin wrapper: ``(status, body)`` per call, explicit credentials, JSON bodies."""

    def __init__(self, client: TestClient, app: Any) -> None:
        self.client = client
        self.app = app
        self.rt = app["rt"]

    @property
    def origin(self) -> str:
        u = self.client.make_url("/")
        return f"{u.scheme}://{u.host}:{u.port}"

    async def call(self, method: str, path: str, *, token: str | None = None, cookie: str | None = None, body: Any = None,
                   headers: dict[str, str] | None = None, origin: str | None = None) -> tuple[int, Any, aiohttp.ClientResponse]:
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        if cookie:
            h["Cookie"] = f"mycelic_session={cookie}"
        if origin:
            h["Origin"] = origin
        kw: dict[str, Any] = {"headers": h}
        if body is not None:
            kw["data"] = j(body)
            h["Content-Type"] = "application/json"
        async with self.client.request(method, path, **kw) as resp:
            text = await resp.text()
            try:
                data = json.loads(text) if text else None
            except ValueError:
                data = text
            return resp.status, data, resp

    async def register(self, slug: str, *, email: str | None = None, name: str = "Admin", org_name: str | None = None, password: str = "correct horse") -> tuple[str, dict[str, Any]]:
        status, body, resp = await self.call("POST", "/api/auth/register", body={"org_name": org_name or slug.title(), "slug": slug, "email": email or f"admin@{slug}.example",
                                                                                "name": name, "password": password})
        assert status == 201, body
        return resp.cookies["mycelic_session"].value, body

    async def invite_and_accept(self, admin_token: str, *, email: str, role: str, unit_id: str | None = None, name: str = "Member") -> tuple[str, dict[str, Any]]:
        status, body, _ = await self.call("POST", "/api/org/invitations", token=admin_token, body={"email": email, "role": role, "unit_id": unit_id})
        assert status == 201, body
        token = body["accept_url"].rsplit("/", 1)[-1]
        status, body, resp = await self.call("POST", f"/api/auth/invitation/{token}/accept", body={"name": name, "password": "another horse"})
        assert status == 200, body
        return resp.cookies["mycelic_session"].value, body


@pytest.fixture
async def api(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    rt = build_runtime(settings)
    # dist_dir points at a directory that does not exist so the "not built" page is served whatever the repo checkout holds
    app = create_app(rt, settings, run_worker=False, run_holders=True, metrics=Metrics(), allowed_hosts=None, cors_origins=["http://allowed.example"],
                     dist_dir=tmp_path / "no-dist")
    app["hub"].ping_seconds = 0.5
    server = TestServer(app, shutdown_timeout=5.0)
    client = TestClient(server, cookie_jar=DummyCookieJar())
    await client.start_server()
    try:
        yield Api(client, app)
    finally:
        await client.close()


@pytest.fixture
async def api_nodemo(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path / "nodemo", demo_mode=False)
    rt = build_runtime(settings)
    app = create_app(rt, settings, run_worker=False, run_holders=False, metrics=Metrics(), allowed_hosts=None, dist_dir=tmp_path / "no-dist")
    server = TestServer(app, shutdown_timeout=5.0)
    client = TestClient(server, cookie_jar=DummyCookieJar())
    await client.start_server()
    try:
        yield Api(client, app)
    finally:
        await client.close()


async def read_sse(resp: aiohttp.ClientResponse, *, until_event: str | None, timeout: float) -> list[str]:
    """Read frames until ``until_event`` shows up (or the timeout elapses); returns the lines seen."""
    lines: list[str] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return lines
        try:
            raw = await asyncio.wait_for(resp.content.readline(), timeout=remaining)
        except asyncio.TimeoutError:
            return lines
        if not raw:
            return lines
        line = raw.decode("utf-8").rstrip("\n")
        lines.append(line)
        if until_event and line == f"event: {until_event}":
            # pull the data line of this frame as well
            data = await asyncio.wait_for(resp.content.readline(), timeout=max(0.1, deadline - loop.time()))
            lines.append(data.decode("utf-8").rstrip("\n"))
            return lines


# ---------------------------------------------------------------------------------------------
# auth and onboarding
# ---------------------------------------------------------------------------------------------


async def test_register_me_and_onboarding_steps(api: Api) -> None:
    status, body, resp = await api.call("POST", "/api/auth/register", body={"org_name": "Acme", "slug": "acme", "email": "ada@acme.example", "name": "Ada", "password": "correct horse"})
    assert status == 201, body
    assert body["tenant"]["slug"] == "acme" and body["user"]["email"] == "ada@acme.example"
    assert body["principal"]["is_admin"] is True and "executive" in body["principal"]["roles"]
    assert resp.headers["X-Request-Id"]
    cookie = resp.cookies["mycelic_session"]
    assert cookie["httponly"] and cookie["samesite"].lower() == "lax" and cookie["path"] == "/" and int(cookie["max-age"]) > 0 and not cookie["secure"]
    token = cookie.value

    status, me, _ = await api.call("GET", "/api/auth/me", cookie=token)
    assert status == 200, me
    assert me["user"]["user_id"] == body["user"]["user_id"] and me["tenant"]["tenant_id"] == body["tenant"]["tenant_id"]
    assert me["demo_mode"] is True and me["unread_notifications"] == 0 and me["holders"] == []
    assert set(me["levels"]) >= {"employee", "executive"}
    steps = me["onboarding"]["steps"]
    assert set(steps) == {"organization", "members", "holder", "model", "goal", "loop_active"}
    assert steps == {"organization": False, "members": False, "holder": False, "model": True, "goal": False, "loop_active": False}
    assert me["onboarding"]["complete"] is False

    # the same session works as a bearer token
    status, me2, _ = await api.call("GET", "/api/auth/me", token=token)
    assert status == 200 and me2["user"]["user_id"] == me["user"]["user_id"]

    # organization step flips once a unit exists under the root
    status, unit, _ = await api.call("POST", "/api/org/units", token=token, body={"type": "team", "name": "Platform"})
    assert status == 201, unit
    assert unit["unit"]["parent_id"] == body["root_unit"]["unit_id"] and unit["unit"]["level"] == "team"
    status, me3, _ = await api.call("GET", "/api/auth/me", token=token)
    assert me3["onboarding"]["steps"]["organization"] is True

    # duplicate slug is a conflict, bad input a validation error
    status, dup, _ = await api.call("POST", "/api/auth/register", body={"org_name": "Acme2", "slug": "acme", "email": "x@acme.example", "name": "X", "password": "correct horse"})
    assert status == 409 and dup["code"] == "slug_taken"
    status, bad, _ = await api.call("POST", "/api/auth/register", body={"org_name": "A", "slug": "bad slug!", "email": "x@x", "name": "X", "password": "correct horse"})
    assert status == 400 and bad["code"] == "validation"

    # logout clears the cookie and revokes the session
    status, out, resp = await api.call("POST", "/api/auth/logout", cookie=token)
    assert status == 200 and out == {"ok": True}
    assert "mycelic_session" in resp.headers.get("Set-Cookie", "")
    status, _, _ = await api.call("GET", "/api/auth/me", cookie=token)
    assert status == 401


async def test_login_wrong_password_401(api: Api) -> None:
    await api.register("acme")
    status, body, _ = await api.call("POST", "/api/auth/login", body={"email": "admin@acme.example", "password": "wrong password"})
    assert status == 401 and body["code"] == "unauthorized"
    status, body, _ = await api.call("POST", "/api/auth/login", body={"email": "nobody@acme.example", "password": "correct horse"})
    assert status == 401
    status, body, resp = await api.call("POST", "/api/auth/login", body={"email": "admin@acme.example", "password": "correct horse"})
    assert status == 200 and body["user"]["email"] == "admin@acme.example" and resp.cookies["mycelic_session"].value
    # the denial is audited
    rows = api.rt.db.all("SELECT * FROM audit_log WHERE action='auth.login' AND outcome='deny'")
    assert len(rows) >= 2


async def test_unauthenticated_and_bad_credentials(api: Api) -> None:
    status, body, _ = await api.call("GET", "/api/goals")
    assert status == 401 and body["code"] == "unauthorized"
    status, body, _ = await api.call("GET", "/api/goals", token="not-a-real-token")
    assert status == 401
    status, body, _ = await api.call("GET", "/api/goals", token="mk_not-a-real-key")
    assert status == 401
    # an expired cookie is treated as anonymous, not as an error
    status, body, _ = await api.call("GET", "/api/auth/me", cookie="stale")
    assert status == 401


# ---------------------------------------------------------------------------------------------
# tenant isolation
# ---------------------------------------------------------------------------------------------


async def test_second_tenant_cannot_read_first_tenants_goal(api: Api) -> None:
    tok_a, reg_a = await api.register("alpha")
    tok_b, reg_b = await api.register("beta")
    status, goal, _ = await api.call("POST", "/api/goals", token=tok_a, body={"title": "Cut incident time", "objective": "Halve MTTR", "activate": False})
    assert status == 201, goal
    gid = goal["goal"]["goal_id"]
    status, body, _ = await api.call("GET", f"/api/goals/{gid}", token=tok_b)
    assert status in (403, 404), body
    status, body, _ = await api.call("GET", "/api/goals", token=tok_b)
    assert status == 200 and body["items"] == []
    status, body, _ = await api.call("GET", f"/api/org/units/{reg_a['root_unit']['unit_id']}", token=tok_b)
    assert status == 404
    status, body, _ = await api.call("POST", f"/api/goals/{gid}/actions", token=tok_b, body={"action": "archive"})
    assert status in (403, 404)
    status, body, _ = await api.call("GET", f"/api/goals/{gid}", token=tok_a)
    assert status == 200 and body["goal"]["goal_id"] == gid


# ---------------------------------------------------------------------------------------------
# invitations and organization
# ---------------------------------------------------------------------------------------------


async def test_invitation_create_and_accept(api: Api) -> None:
    tok, reg = await api.register("acme")
    root = reg["root_unit"]["unit_id"]
    status, team, _ = await api.call("POST", "/api/org/units", token=tok, body={"type": "team", "name": "Support"})
    team_id = team["unit"]["unit_id"]
    status, inv, _ = await api.call("POST", "/api/org/invitations", token=tok, body={"email": "Bea@acme.example", "role": "employee", "unit_id": team_id})
    assert status == 201 and inv["invitation"]["status"] == "pending" and inv["invitation"]["unit_name"] == "Support"
    assert "/invite/" in inv["accept_url"]
    token = inv["accept_url"].rsplit("/", 1)[-1]
    status, look, _ = await api.call("GET", f"/api/auth/invitation/{token}")
    assert status == 200 and look["invitation"] == {**look["invitation"], "email": "bea@acme.example", "role": "employee", "unit_name": "Support", "org_name": "Acme", "status": "pending"}
    status, body, _ = await api.call("GET", "/api/auth/invitation/nope")
    assert status == 404
    status, acc, resp = await api.call("POST", f"/api/auth/invitation/{token}/accept", body={"name": "Bea", "password": "another horse"})
    assert status == 200 and acc["user"]["email"] == "bea@acme.example" and resp.cookies["mycelic_session"].value
    assert [m["unit_id"] for m in acc["principal"]["memberships"]] == [team_id]
    # single use
    status, again, _ = await api.call("POST", f"/api/auth/invitation/{token}/accept", body={"name": "Bea", "password": "another horse"})
    assert status == 400 and again["code"] == "invalid_invitation"
    status, lst, _ = await api.call("GET", "/api/org/invitations", token=tok)
    assert status == 200 and lst["items"][0]["status"] == "accepted"
    status, org, _ = await api.call("GET", "/api/org", token=resp.cookies["mycelic_session"].value)
    assert status == 200 and org["counts"]["users"] == 2 and org["tree"][0]["unit"]["unit_id"] == root and org["tree"][0]["children"][0]["unit"]["unit_id"] == team_id
    status, users, _ = await api.call("GET", "/api/org/users", token=tok)
    assert status == 200 and users["total"] == 2
    status, mem, _ = await api.call("GET", "/api/org/memberships", token=tok)
    assert status == 200 and any(m["user_name"] == "Bea" and m["unit_name"] == "Support" for m in mem["items"])


# ---------------------------------------------------------------------------------------------
# goals and loops
# ---------------------------------------------------------------------------------------------


async def test_goal_create_activates_and_loop_control(api: Api) -> None:
    tok, reg = await api.register("acme")
    status, body, _ = await api.call("POST", "/api/goals", token=tok, body={"title": "Reduce churn", "objective": "Find why customers leave", "priority": 2,
                                                                          "success_criteria": [{"metric": "churn", "target": 5, "direction": "decrease"}]})
    assert status == 201, body
    goal = body["goal"]
    assert goal["status"] == "active" and goal["loop"] is not None and goal["loop"]["desired"] == "active"
    assert goal["owner_type"] == "user" and goal["owner_name"] == "Admin" and goal["counts"]["questions"] == 0
    gid = goal["goal_id"]
    status, detail, _ = await api.call("GET", f"/api/goals/{gid}", token=tok)
    assert status == 200 and detail["loop"]["goal_id"] == gid and detail["questions"] == [] and detail["usage"]["calls"] == 0
    status, lp, _ = await api.call("GET", f"/api/goals/{gid}/loop", token=tok)
    assert status == 200 and lp["loop"]["state"] in ("waiting", "active") and "active_indicator" in lp["loop"] and "budget_remaining" in lp["loop"]
    status, lp, _ = await api.call("POST", f"/api/goals/{gid}/loop", token=tok, body={"action": "pause"})
    assert status == 200 and lp["loop"]["desired"] == "paused" and lp["loop"]["state"] == "paused"
    status, lp, _ = await api.call("POST", f"/api/goals/{gid}/loop", token=tok, body={"action": "resume"})
    assert status == 200 and lp["loop"]["desired"] == "active"
    status, lp, _ = await api.call("POST", f"/api/goals/{gid}/loop", token=tok, body={"action": "bogus"})
    assert status == 400
    # a tick job is queued for the worker
    assert api.rt.db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='loop.tick' AND ref_id=?", (gid,)) >= 1
    # patch a field, status through actions only
    status, upd, _ = await api.call("PATCH", f"/api/goals/{gid}", token=tok, body={"priority": 1})
    assert status == 200 and upd["goal"]["priority"] == 1 and upd["goal"]["version"] > goal["version"]
    status, upd, _ = await api.call("PATCH", f"/api/goals/{gid}", token=tok, body={"status": "archived"})
    assert status == 400
    status, act, _ = await api.call("POST", f"/api/goals/{gid}/actions", token=tok, body={"action": "pause"})
    assert status == 200 and act["goal"]["status"] == "paused"
    status, out, _ = await api.call("POST", f"/api/goals/{gid}/outcomes", token=tok, body={"kind": "measurement", "value": {"metric": "churn", "value": 7}})
    assert status == 201 and out["outcome"]["kind"] == "measurement" and out["progress"]["known"] is True
    # activate: false leaves the goal in draft with no loop
    status, draft, _ = await api.call("POST", "/api/goals", token=tok, body={"title": "Draft", "objective": "later", "activate": False})
    assert status == 201 and draft["goal"]["status"] == "draft" and draft["goal"]["loop"] is None
    status, lst, _ = await api.call("GET", "/api/goals?mine=1", token=tok)
    assert status == 200 and lst["total"] == 2


# ---------------------------------------------------------------------------------------------
# questions
# ---------------------------------------------------------------------------------------------


async def test_question_create_and_duplicate_409(api: Api) -> None:
    tok, reg = await api.register("acme")
    status, g, _ = await api.call("POST", "/api/goals", token=tok, body={"title": "Onboarding", "objective": "Why is onboarding slow?", "activate": False})
    gid = g["goal"]["goal_id"]
    status, q, _ = await api.call("POST", "/api/questions", token=tok, body={"text": "What blocks new hires in their first week?", "goal_id": gid})
    assert status == 201, q
    qid = q["question"]["question_id"]
    assert q["question"]["asker"]["id"] == reg["user"]["user_id"] and q["question"]["scope_unit_id"] == reg["root_unit"]["unit_id"]
    assert q["question"]["priority_breakdown"]["method"] == "heuristic"
    status, dup, _ = await api.call("POST", "/api/questions", token=tok, body={"text": "What blocks new hires in their first week?", "goal_id": gid})
    assert status == 409 and dup["code"] == "duplicate_question" and dup["existing_question_id"] == qid
    status, short, _ = await api.call("POST", "/api/questions", token=tok, body={"text": "why?", "goal_id": gid})
    assert status == 400
    status, lst, _ = await api.call("GET", f"/api/questions?goal_id={gid}", token=tok)
    assert status == 200 and [x["question_id"] for x in lst["items"]] == [qid]
    status, det, _ = await api.call("GET", f"/api/questions/{qid}", token=tok)
    assert status == 200 and det["question"]["question_id"] == qid and det["responses"] == [] and "lineage" in det
    status, can, _ = await api.call("POST", f"/api/questions/{qid}/cancel", token=tok, body={"reason": "asked twice"})
    assert status == 200 and can["question"]["status"] == "cancelled"


# ---------------------------------------------------------------------------------------------
# knowledge visibility and admin
# ---------------------------------------------------------------------------------------------


def _insert_claim(rt: Any, tenant_id: str, scope_unit_id: str, text: str, *, visibility: str = "unit") -> str:
    cid = new_id("claim")
    now = now_iso()
    rt.db.conn.execute("INSERT INTO claims(claim_id, tenant_id, scope_unit_id, visibility, text, kind, status, confidence, created_by_type, created_by_id, support, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, 'finding', 'hypothesis', 0.5, 'loop', 'test', '{}', ?, ?)", (cid, tenant_id, scope_unit_id, visibility, text, now, now))
    did = new_id("disc")
    rt.db.conn.execute("INSERT INTO discoveries(discovery_id, tenant_id, scope_unit_id, visibility, level, kind, title, summary, claim_ids, status, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, 'executive', 'finding', ?, 'summary', ?, 'new', ?, ?)", (did, tenant_id, scope_unit_id, visibility, text, j([cid]), now, now))
    rt.db.revision += 1
    return cid


async def test_outsider_sees_no_discoveries_or_claims(api: Api) -> None:
    tok, reg = await api.register("acme")
    root = reg["root_unit"]["unit_id"]
    status, team, _ = await api.call("POST", "/api/org/units", token=tok, body={"type": "team", "name": "Warehouse"})
    emp_tok, emp = await api.invite_and_accept(tok, email="emp@acme.example", role="employee", unit_id=team["unit"]["unit_id"])
    cid = _insert_claim(api.rt, reg["tenant"]["tenant_id"], root, "Approvals take two days on average")
    status, body, _ = await api.call("GET", "/api/claims", token=tok)
    assert status == 200 and [c["claim_id"] for c in body["items"]] == [cid]
    status, body, _ = await api.call("GET", "/api/discoveries", token=tok)
    assert status == 200 and body["total"] == 1
    status, body, _ = await api.call("GET", "/api/claims", token=emp_tok)
    assert status == 200 and body["items"] == []
    status, body, _ = await api.call("GET", "/api/discoveries", token=emp_tok)
    assert status == 200 and body["items"] == []
    status, body, _ = await api.call("GET", f"/api/claims/{cid}", token=emp_tok)
    assert status == 403 and body["code"] == "forbidden"
    assert api.rt.db.scalar("SELECT COUNT(*) FROM audit_log WHERE outcome='deny' AND action='claim.view'") == 1
    status, body, _ = await api.call("GET", f"/api/claims/{cid}", token=tok)
    assert status == 200 and body["claim"]["claim_id"] == cid and "freshness" in body and "dependencies" in body


async def test_admin_endpoints_forbidden_for_non_admin(api: Api) -> None:
    tok, reg = await api.register("acme")
    emp_tok, emp = await api.invite_and_accept(tok, email="emp@acme.example", role="employee", unit_id=reg["root_unit"]["unit_id"])
    for path in ("/api/admin/overview", "/api/admin/jobs", "/api/admin/audit", "/api/admin/usage", "/api/admin/models", "/api/admin/workers", "/api/org/users", "/api/org/policies"):
        status, body, _ = await api.call("GET", path, token=emp_tok)
        assert status == 403, (path, body)
    status, body, _ = await api.call("POST", "/api/org/units", token=emp_tok, body={"type": "team", "name": "Nope"})
    assert status == 403
    assert api.rt.db.scalar("SELECT COUNT(*) FROM audit_log WHERE outcome='deny' AND action='admin'") >= 8
    status, ov, _ = await api.call("GET", "/api/admin/overview", token=tok)
    assert status == 200, ov
    assert set(ov) >= {"workers", "jobs", "failed_jobs", "holders", "usage", "budgets", "loops", "deployment", "metrics"}
    assert ov["deployment"]["version"] and "model_tiers" in ov["deployment"]["settings"] and ov["deployment"]["migrations"]["pending"] == 0
    assert ov["deployment"]["transport"] == "sqlite" and ov["metrics"]["queue_backlog"] == 0
    assert "anthropic_api_key" not in json.dumps(ov)
    status, models, _ = await api.call("GET", "/api/admin/models", token=tok)
    assert status == 200 and models["tiers"]["light"]["provider"] == "fake" and models["policy_tiers"]["synthesize"] == "heavy"
    status, models, _ = await api.call("PUT", "/api/admin/models", token=tok, body={"policy_tiers": {"chat": "light"}})
    assert status == 200 and models["policy_tiers"]["chat"] == "light"
    status, pol, _ = await api.call("PUT", "/api/org/policies", token=tok, body={"key": "freshness_days", "value": 30})
    assert status == 200 and pol["policies"]["freshness_days"] == 30
    status, aud, _ = await api.call("GET", "/api/admin/audit?action=policy", token=tok)
    assert status == 200 and aud["items"] and aud["items"][0]["action"] == "policy.set"
    status, jobs, _ = await api.call("GET", "/api/admin/jobs", token=tok)
    assert status == 200 and "items" in jobs
    status, rd, _ = await api.call("POST", "/api/admin/jobs/retry-dead", token=tok, body={})
    assert status == 200 and rd["retried"] == 0


# ---------------------------------------------------------------------------------------------
# demo mode
# ---------------------------------------------------------------------------------------------


async def test_demo_switch_issues_real_session_only_in_demo_mode(api: Api, api_nodemo: Api) -> None:
    demo = await api.rt.auth.register_tenant(org_name="Demo Org", slug="demo", admin_email="ceo@demo.example", admin_name="Demo CEO", password="demo demo demo", is_demo=True)
    tok, reg = await api.register("acme")   # a real tenant in the same deployment
    status, personas, _ = await api.call("GET", "/api/demo/personas")
    assert status == 200 and [x["user_id"] for x in personas["items"]] == [demo["user"]["user_id"]]
    assert personas["items"][0]["level"] == "executive" and "org_admin" in personas["items"][0]["roles"] and personas["items"][0]["unit_names"] == ["Demo Org"]
    status, sw, resp = await api.call("POST", "/api/demo/switch", body={"user_id": demo["user"]["user_id"]})
    assert status == 200 and sw["user"]["user_id"] == demo["user"]["user_id"] and sw["principal"]["is_demo"] is True
    cookie = resp.cookies["mycelic_session"].value
    status, me, _ = await api.call("GET", "/api/auth/me", cookie=cookie)
    assert status == 200 and me["tenant"]["is_demo"] is True and me["session_kind"] == "session"
    sess = api.rt.auth.resolve_session(cookie)
    assert sess["kind"] == "demo" and sess["tenant_id"] == demo["tenant"]["tenant_id"]
    # the demo session is an ordinary one: it cannot read the other tenant
    status, goals, _ = await api.call("GET", "/api/goals", token=cookie)
    assert status == 200 and goals["items"] == []
    status, body, _ = await api.call("GET", f"/api/org/units/{reg['root_unit']['unit_id']}", token=cookie)
    assert status == 404
    # only demo accounts can be switched into
    status, body, _ = await api.call("POST", "/api/demo/switch", body={"user_id": reg["user"]["user_id"]})
    assert status == 404
    # demo mode off: the endpoints do not exist
    status, body, _ = await api_nodemo.call("GET", "/api/demo/personas")
    assert status == 404
    status, body, _ = await api_nodemo.call("POST", "/api/demo/switch", body={"user_id": demo["user"]["user_id"]})
    assert status == 404


# ---------------------------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------------------------


async def test_sse_delivers_goal_updated_to_owner_not_other_tenant(api: Api) -> None:
    tok_a, reg_a = await api.register("alpha")
    tok_b, reg_b = await api.register("beta")
    stream_a = await api.client.get("/api/events/stream", headers={"Authorization": f"Bearer {tok_a}"}, timeout=aiohttp.ClientTimeout(total=None))
    stream_b = await api.client.get("/api/events/stream", headers={"Authorization": f"Bearer {tok_b}"}, timeout=aiohttp.ClientTimeout(total=None))
    try:
        assert stream_a.status == 200 and stream_a.headers["Content-Type"].startswith("text/event-stream") and stream_a.headers.get("X-Request-Id")
        status, goal, _ = await api.call("POST", "/api/goals", token=tok_a, body={"title": "Alpha goal", "objective": "objective", "activate": False})
        gid = goal["goal"]["goal_id"]
        lines = await read_sse(stream_a, until_event="goal.updated", timeout=5.0)
        assert "event: goal.updated" in lines, lines
        data = json.loads([ln for ln in lines if ln.startswith("data: ")][-1][6:])
        assert data["ref_id"] == gid and data["kind"] == "goal.updated" and data["tenant_id"] == reg_a["tenant"]["tenant_id"]
        assert any(ln.startswith("id: ") for ln in lines)
        lines_b = await read_sse(stream_b, until_event="goal.updated", timeout=1.5)
        assert not any(ln == "event: goal.updated" for ln in lines_b), lines_b
        assert gid not in "\n".join(lines_b)
        assert any(ln.startswith(": ping") for ln in lines_b), lines_b   # the keep-alive comment arrives while nothing is delivered
    finally:
        stream_a.close()
        stream_b.close()
    # since= backfills from the outbox, still filtered per viewer
    stream_a2 = await api.client.get("/api/events/stream?since=0", headers={"Authorization": f"Bearer {tok_a}"}, timeout=aiohttp.ClientTimeout(total=None))
    try:
        lines = await read_sse(stream_a2, until_event="goal.updated", timeout=5.0)
        assert "event: goal.updated" in lines
    finally:
        stream_a2.close()
    stream_b2 = await api.client.get("/api/events/stream?since=0", headers={"Authorization": f"Bearer {tok_b}"}, timeout=aiohttp.ClientTimeout(total=None))
    try:
        lines = await read_sse(stream_b2, until_event="goal.updated", timeout=1.0)
        assert gid not in "\n".join(lines)
    finally:
        stream_b2.close()
    status, body, _ = await api.call("GET", "/api/events/stream")
    assert status == 401


async def test_sse_audience_filter_rules(api: Api) -> None:
    from mycelic.api.sse import EventHub
    tok, reg = await api.register("acme")
    tid = reg["tenant"]["tenant_id"]
    root = reg["root_unit"]["unit_id"]
    status, team, _ = await api.call("POST", "/api/org/units", token=tok, body={"type": "team", "name": "Ops"})
    emp_tok, emp = await api.invite_and_accept(tok, email="emp@acme.example", role="employee", unit_id=team["unit"]["unit_id"])
    hub: EventHub = api.app["hub"]
    admin = api.rt.authz.principal_for_user(reg["user"]["user_id"])
    employee = api.rt.authz.principal_for_user(emp["user"]["user_id"])

    def ev(audience: dict[str, Any] | None, tenant: str = tid) -> dict[str, Any]:
        return {"id": 1, "tenant_id": tenant, "kind": "x", "audience": audience, "payload": {}}

    assert hub.authorized(admin, ev({"roles": ["org_admin"]})) and not hub.authorized(employee, ev({"roles": ["org_admin"]}))
    assert hub.authorized(employee, ev({"user_ids": [employee.id]})) and not hub.authorized(admin, ev({"user_ids": [employee.id]}))
    assert hub.authorized(employee, ev({"unit_ids": [team["unit"]["unit_id"]], "visibility": "unit"}))
    assert hub.authorized(admin, ev({"unit_ids": [team["unit"]["unit_id"]], "visibility": "unit"}))      # executive leads the root closure
    assert not hub.authorized(employee, ev({"unit_ids": [root], "visibility": "unit"}))
    assert hub.authorized(employee, ev({"visibility": "org"})) and hub.authorized(employee, ev({})) and hub.authorized(employee, ev(None))
    assert not hub.authorized(employee, ev({"visibility": "private"}))
    assert not hub.authorized(admin, ev({"visibility": "org"}, tenant="ten_other")) and not hub.authorized(admin, ev({"roles": ["org_admin"]}, tenant="ten_other"))


# ---------------------------------------------------------------------------------------------
# health, metrics, CSRF, static
# ---------------------------------------------------------------------------------------------


async def test_healthz_readyz_metrics(api: Api) -> None:
    status, body, resp = await api.call("GET", "/healthz")
    assert status == 200 and body["ok"] is True and body["service"] == "api" and body["version"] and resp.headers["X-Request-Id"]
    status, body, resp = await api.call("GET", "/readyz")
    assert status == 200, body
    assert body["ok"] is True and body["db"] is True and body["transport"] == "sqlite" and body["migrations_pending"] == 0 and body["worker_heartbeat_age_seconds"] is None
    status, text, resp = await api.call("GET", "/metrics")
    assert status == 200 and resp.headers["Content-Type"].startswith("text/plain")
    for name in ("mycelic_model_calls_total", "mycelic_model_cost_usd_total", "mycelic_jobs_backlog", "mycelic_jobs_dead", "mycelic_questions_total", "mycelic_routes_failed_total",
                 "mycelic_claims", "mycelic_evidence_stale", "mycelic_worker_heartbeat_age_seconds", "mycelic_http_requests_total", "mycelic_http_request_seconds"):
        assert f"# TYPE {name}" in text, name
    assert 'mycelic_http_requests_total{method="GET",route="/healthz",status="200"} 1' in text
    assert 'mycelic_claims{status="supported"} 0' in text
    status, body, resp = await api.call("GET", "/healthz", headers={"X-Request-Id": "req_client-supplied-1"})
    assert resp.headers["X-Request-Id"] == "req_client-supplied-1"
    # readiness reflects a stale worker heartbeat only when a worker is expected in this process
    api.rt.db.conn.execute("INSERT INTO workers(worker_id, role, started_at, last_heartbeat_at) VALUES ('w-old', 'discovery', '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')")
    status, body, _ = await api.call("GET", "/readyz")
    assert status == 200 and body["worker_heartbeat_age_seconds"] > 60
    api.app["run_worker"] = True
    status, body, _ = await api.call("GET", "/readyz")
    assert status == 503 and body["ok"] is False
    api.app["run_worker"] = False


async def test_csrf_foreign_origin_403(api: Api) -> None:
    tok, reg = await api.register("acme")
    status, body, _ = await api.call("POST", "/api/goals", token=tok, body={"title": "x", "objective": "y"}, origin="http://evil.example")
    assert status == 403 and body["code"] == "csrf"
    status, body, _ = await api.call("POST", "/api/goals", token=tok, body={"title": "Same origin", "objective": "y", "activate": False}, origin=api.origin)
    assert status == 201
    status, body, resp = await api.call("POST", "/api/goals", token=tok, body={"title": "Allowed origin", "objective": "y", "activate": False}, origin="http://allowed.example")
    assert status == 201 and resp.headers.get("Access-Control-Allow-Origin") == "http://allowed.example"
    status, body, _ = await api.call("GET", "/api/goals", token=tok, origin="http://evil.example")
    assert status == 200   # reads are not state-changing
    async with api.client.options("/api/goals", headers={"Origin": "http://allowed.example", "Access-Control-Request-Method": "POST"}) as resp:
        assert resp.status == 204 and resp.headers["Access-Control-Allow-Origin"] == "http://allowed.example"
    async with api.client.options("/api/goals", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}) as resp:
        assert resp.status == 403


async def test_static_fallback_and_unknown_api_route(api: Api) -> None:
    status, text, resp = await api.call("GET", "/")
    assert status == 200 and "npm run build" in text and resp.headers["Content-Type"].startswith("text/html")
    status, text, _ = await api.call("GET", "/app/goals/abc")
    assert status == 200 and "not built" in text
    status, body, _ = await api.call("GET", "/api/does-not-exist")
    assert status == 404 and body["code"] == "not_found"
    status, body, _ = await api.call("GET", "/api")
    assert status == 200 and body["service"] == "mycelic"


async def test_static_serves_built_frontend(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Mycelic</title><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    settings = make_settings(tmp_path / "data")
    rt = build_runtime(settings)
    app = create_app(rt, settings, run_worker=False, run_holders=False, metrics=Metrics(), dist_dir=dist)
    client = TestClient(TestServer(app, shutdown_timeout=5.0), cookie_jar=DummyCookieJar())
    await client.start_server()
    try:
        async with client.get("/") as resp:
            assert resp.status == 200 and "id=root" in await resp.text() and resp.headers["Cache-Control"] == "no-store"
        async with client.get("/app/executive") as resp:
            assert resp.status == 200 and "id=root" in await resp.text()
        async with client.get("/assets/app.js") as resp:
            assert resp.status == 200 and "console.log" in await resp.text()
        async with client.get("/../etc/passwd") as resp:
            assert resp.status == 200 and "id=root" in await resp.text()   # normalised to the SPA index, never a file outside dist
        async with client.get("/api/nope") as resp:
            assert resp.status == 404
    finally:
        await client.close()


# ---------------------------------------------------------------------------------------------
# holders, documents, memory, responses
# ---------------------------------------------------------------------------------------------


async def test_holder_documents_search_memory_and_response(api: Api) -> None:
    tok, reg = await api.register("acme")
    tid = reg["tenant"]["tenant_id"]
    status, h, _ = await api.call("POST", "/api/holders", token=tok, body={"name": "Ada's notes", "domains": ["ops"]})
    assert status == 201, h
    assert h["key"] and h["holder"]["mode"] == "embedded" and h["holder"]["owner_id"] == reg["user"]["user_id"] and "route_key" not in h["holder"] and "key_hash" not in h["holder"]
    hid = h["holder"]["holder_id"]
    status, me, _ = await api.call("GET", "/api/auth/me", token=tok)
    assert [x["holder_id"] for x in me["holders"]] == [hid] and me["onboarding"]["steps"]["holder"] is True
    text = ("Deployment approvals take two days on average because the change advisory board meets twice a week. "
            "Teams batch their releases to cope with the delay.\n\nThe VPN outage on 4 March was caused by an expired certificate.")
    status, d, _ = await api.call("POST", f"/api/holders/{hid}/documents", token=tok, body={"title": "Ops incident log", "text": text, "kind": "note"})
    assert status == 201, d
    doc = d["document"]
    assert doc["doc_id"].startswith("doc_") and doc["chunks"] >= 1 and doc["status"] == "active" and doc["source_root_id"]
    ev = api.rt.db.all("SELECT * FROM events WHERE kind='document.ingested'")
    assert len(ev) == 1 and json.loads(ev[0]["payload"])["doc_id"] == doc["doc_id"] and json.loads(ev[0]["audience"])["user_ids"] == [reg["user"]["user_id"]]
    # multipart upload of a text file; pdf refused
    form = aiohttp.FormData()
    form.add_field("file", b"Warehouse robots stalled in June because floor markings confused the cameras.", filename="robots.md", content_type="text/markdown")
    form.add_field("domains", "ops,warehouse")
    async with api.client.post(f"/api/holders/{hid}/documents", data=form, headers={"Authorization": f"Bearer {tok}"}) as resp:
        up = await resp.json()
        assert resp.status == 201, up
        assert up["document"]["title"] == "robots" and up["document"]["domains"] == ["ops", "warehouse"]
    form = aiohttp.FormData()
    form.add_field("file", b"%PDF-1.4", filename="x.pdf", content_type="application/pdf")
    async with api.client.post(f"/api/holders/{hid}/documents", data=form, headers={"Authorization": f"Bearer {tok}"}) as resp:
        assert resp.status == 400 and "PDF" in (await resp.json())["error"]
    status, lst, _ = await api.call("GET", f"/api/holders/{hid}/documents", token=tok)
    assert status == 200 and lst["total"] == 2
    status, one, _ = await api.call("GET", f"/api/holders/{hid}/documents/{doc['doc_id']}", token=tok)
    assert status == 200 and one["text"].startswith("Deployment approvals") and one["document"]["doc_id"] == doc["doc_id"]
    status, srch, _ = await api.call("GET", f"/api/holders/{hid}/search?q=deployment%20approvals&k=3", token=tok)
    assert status == 200 and srch["results"] and srch["results"][0]["doc_id"] == doc["doc_id"] and srch["results"][0]["holder_id"] == hid
    status, mem, _ = await api.call("GET", "/api/me/memory/search?q=deployment%20approvals", token=tok)
    assert status == 200 and mem["results"] and mem["results"][0]["holder_name"] == "Ada's notes"
    status, rec, _ = await api.call("GET", "/api/me/memory/recent?limit=5", token=tok)
    assert status == 200 and rec["total"] >= 1 and rec["items"][0]["holder_id"] == hid and rec["items"][0]["text"]
    status, st, _ = await api.call("GET", f"/api/holders/{hid}/stats", token=tok)
    assert status == 200 and st["documents"] == 2 and st["memories"] >= 2 and st["status"] == "online"
    status, ws, _ = await api.call("GET", "/api/workspace/employee", token=tok)
    assert status == 200 and [x["holder_id"] for x in ws["holders"]] == [hid] and ws["recent_memory"]
    # another member cannot read the store; a grant lets them search
    emp_tok, emp = await api.invite_and_accept(tok, email="emp@acme.example", role="employee", unit_id=reg["root_unit"]["unit_id"])
    status, body, _ = await api.call("GET", f"/api/holders/{hid}/search?q=approvals", token=emp_tok)
    assert status == 403
    status, body, _ = await api.call("GET", f"/api/holders/{hid}/documents/{doc['doc_id']}", token=emp_tok)
    assert status == 403
    status, gr, _ = await api.call("POST", "/api/grants", token=tok, body={"grantee_type": "user", "grantee_id": emp["user"]["user_id"], "resource_type": "holder", "resource_id": hid, "level": "artifact"})
    assert status == 201 and gr["grant"]["level"] == "artifact"
    status, body, _ = await api.call("GET", f"/api/holders/{hid}/search?q=approvals", token=emp_tok)
    assert status == 200 and body["results"]
    status, body, _ = await api.call("GET", f"/api/holders/{hid}/documents/{doc['doc_id']}", token=emp_tok)
    assert status == 403   # artifact is not raw
    status, grants, _ = await api.call("GET", "/api/grants", token=emp_tok)
    assert status == 200 and [g["grant_id"] for g in grants["received"]] == [gr["grant"]["grant_id"]] and grants["given"] == []
    # a person answers a question from their own documents: the reference is opaque, the route is created on the fly
    status, q, _ = await api.call("POST", "/api/questions", token=tok, body={"text": "How long do deployment approvals take?", "candidate_domains": ["ops"]})
    assert status == 201, q
    qid = q["question"]["question_id"]
    status, r, _ = await api.call("POST", f"/api/questions/{qid}/respond", token=tok, body={"content": "About two days because the board meets twice a week.", "doc_ids": [doc["doc_id"]]})
    assert status == 201, r
    assert r["response"]["status"] == "answered" and r["response"]["holder_id"] == hid and len(r["response"]["evidence"]) == 1
    ref = r["response"]["evidence"][0]
    assert ref["ref_id"].startswith("ev_") and ref["source_root_id"] == doc["source_root_id"] and "doc_id" not in ref and ref["holder_name"] == "Ada's notes"
    status, det, _ = await api.call("GET", f"/api/questions/{qid}", token=tok)
    assert status == 200 and det["responses"][0]["response_id"] == r["response"]["response_id"] and det["question"]["routes"][0]["status"] == "answered"
    status, evd, _ = await api.call("GET", f"/api/evidence/{ref['ref_id']}", token=tok)
    assert status == 200 and evd["evidence_ref"]["ref_id"] == ref["ref_id"]
    status, raw, _ = await api.call("GET", f"/api/evidence/{ref['ref_id']}/raw", token=tok)
    assert status == 200 and raw["doc_id"] == doc["doc_id"] and raw["text"].startswith("Deployment approvals")
    status, body, _ = await api.call("GET", f"/api/evidence/{ref['ref_id']}/raw", token=emp_tok)
    assert status == 403
    # revise and retract mark the reference and report it; the outbox carries the document events
    status, rev, _ = await api.call("POST", f"/api/holders/{hid}/documents/{doc['doc_id']}/revise", token=tok, body={"text": "Deployment approvals now take one day.", "reason": "process changed"})
    assert status == 200, rev
    assert rev["document"]["version"] == 2 and ref["ref_id"] in rev["affected_ref_ids"]
    assert api.rt.knowledge.get_ref(ref["ref_id"])["status"] == "revised"
    assert api.rt.db.scalar("SELECT COUNT(*) FROM events WHERE kind='document.revised'") == 1
    status, ret, _ = await api.call("POST", f"/api/holders/{hid}/documents/{doc['doc_id']}/retract", token=tok, body={"reason": "obsolete"})
    assert status == 200 and ret["document"]["status"] == "retracted" and api.rt.knowledge.get_ref(ref["ref_id"])["status"] == "retracted"
    assert api.rt.db.scalar("SELECT COUNT(*) FROM events WHERE kind='document.retracted'") == 1
    # policy patch and key rotation are owner-only
    status, body, _ = await api.call("PATCH", f"/api/holders/{hid}", token=emp_tok, body={"domains": ["hr"]})
    assert status == 403
    status, body, _ = await api.call("PATCH", f"/api/holders/{hid}", token=tok, body={"domains": ["ops", "finance"], "export_policy": {"max_excerpt_chars": 120}})
    assert status == 200 and body["holder"]["domains"] == ["ops", "finance"] and body["holder"]["export_policy"]["max_excerpt_chars"] == 120
    status, body, _ = await api.call("POST", f"/api/holders/{hid}/rotate-key", token=tok, body={})
    assert status == 200 and body["key"] and body["key"] != h["key"]
    status, body, _ = await api.call("GET", "/api/holders", token=emp_tok)
    assert status == 200 and [x["holder_id"] for x in body["items"]] == [hid]   # visible through the grant


async def test_holder_process_endpoints_use_holder_key(api: Api) -> None:
    tok, reg = await api.register("acme")
    status, h, _ = await api.call("POST", "/api/holders", token=tok, body={"name": "laptop", "mode": "external", "domains": ["sales"]})
    assert status == 201 and h["holder"]["mode"] == "external" and h["holder"]["status"] == "offline"
    hid, key = h["holder"]["holder_id"], h["key"]
    status, boot, _ = await api.call("GET", f"/api/holders/{hid}/bootstrap", token=key)
    assert status == 200, boot
    assert boot["holder_id"] == hid and boot["tenant_id"] == reg["tenant"]["tenant_id"] and boot["route_key"] == api.rt.org.route_key(hid)
    assert boot["domains"] == ["sales"] and boot["transport"]["kind"] == "sqlite" and boot["export_policy"]["answer_scopes"]
    status, hb, _ = await api.call("POST", f"/api/holders/{hid}/heartbeat", token=key, body={"stats": {"documents": 3}})
    assert status == 200 and hb["ok"] is True and hb["domains"] == ["sales"]
    status, lst, _ = await api.call("GET", "/api/holders", token=tok)
    assert lst["items"][0]["status"] == "online" and lst["items"][0]["stats"]["documents"] == 3 and lst["items"][0]["last_heartbeat_at"]
    # a user session is not a holder key; another holder's key is not this holder
    status, body, _ = await api.call("GET", f"/api/holders/{hid}/bootstrap", token=tok)
    assert status == 403
    status, other, _ = await api.call("POST", "/api/holders", token=tok, body={"name": "other", "mode": "external"})
    status, body, _ = await api.call("GET", f"/api/holders/{hid}/bootstrap", token=other["key"])
    assert status == 403
    status, body, _ = await api.call("GET", "/api/goals", token=key)
    assert status == 403   # holder keys are not people
    # external ingest goes over the transport and answers 202
    status, d, _ = await api.call("POST", f"/api/holders/{hid}/documents", token=tok, body={"title": "Q3 pipeline", "text": "Pipeline grew 12 percent in Q3."})
    assert status == 202 and d["document"]["status"] == "indexing"
    env = api.rt.db.one("SELECT * FROM transport_messages WHERE subject=?", (f"mycelic.{reg['tenant']['tenant_id']}.holder.{hid}.ingest",))
    assert env is not None
    payload = json.loads(env["payload"])
    assert payload["kind"] == "ingest" and payload["payload"]["doc_id"] == d["document"]["doc_id"] and payload["signature"]
    # revoking stops routing and the key
    status, body, _ = await api.call("PATCH", f"/api/holders/{hid}", token=tok, body={"status": "revoked"})
    assert status == 200 and body["holder"]["status"] == "revoked"
    status, body, _ = await api.call("POST", f"/api/holders/{hid}/heartbeat", token=key, body={"stats": {}})
    assert status == 401


# ---------------------------------------------------------------------------------------------
# workspaces, network, chats, notifications
# ---------------------------------------------------------------------------------------------


async def test_workspaces_network_chat_notifications(api: Api) -> None:
    tok, reg = await api.register("acme")
    root = reg["root_unit"]["unit_id"]
    tid = reg["tenant"]["tenant_id"]
    status, dept, _ = await api.call("POST", "/api/org/units", token=tok, body={"type": "department", "name": "Engineering"})
    dept_id = dept["unit"]["unit_id"]
    status, team, _ = await api.call("POST", "/api/org/units", token=tok, body={"type": "team", "name": "Platform", "parent_id": dept_id})
    team_id = team["unit"]["unit_id"]
    lead_tok, lead = await api.invite_and_accept(tok, email="lead@acme.example", role="department_lead", unit_id=dept_id, name="Lea")
    emp_tok, emp = await api.invite_and_accept(tok, email="emp@acme.example", role="employee", unit_id=team_id, name="Eve")
    _insert_claim(api.rt, tid, team_id, "Platform team budget approval is the recurring blocker")
    status, g, _ = await api.call("POST", "/api/goals", token=lead_tok, body={"title": "Faster releases", "objective": "Cut lead time", "owner_type": "unit", "owner_id": dept_id, "activate": False})
    assert status == 201, g
    # unit workspace: the lead sees the department with synthesis (model) and comparisons only at subsidiary+; the employee cannot open it
    status, ws, _ = await api.call("GET", f"/api/workspace/unit/{dept_id}", token=lead_tok)
    assert status == 200, ws
    assert ws["level"] == "department" and ws["unit"]["unit_id"] == dept_id and [c["unit"]["unit_id"] for c in ws["children"]] == [team_id]
    assert ws["children"][0]["discoveries"] == 1 and ws["synthesis"]["method"] == "model" and ws["synthesis"]["computed_at"] and ws["comparisons"] == []
    assert ws["members"] is not None and [gl["goal_id"] for gl in ws["goals"]] == [g["goal"]["goal_id"]] and ws["discoveries"][0]["scope_unit_id"] == team_id
    assert ws["synthesis"]["constraints"] and "budget" in ws["synthesis"]["constraints"][0]["text"]
    status, body, _ = await api.call("GET", f"/api/workspace/unit/{dept_id}", token=emp_tok)
    assert status == 403
    status, ws_t, _ = await api.call("GET", f"/api/workspace/unit/{team_id}", token=emp_tok)
    assert status == 200 and ws_t["level"] == "team" and ws_t["synthesis"] is None and ws_t["members"] is None and ws_t["discoveries"]
    # executive workspace: only for the executive root's leads
    status, ex, _ = await api.call("GET", "/api/workspace/executive", token=tok)
    assert status == 200, ex
    assert ex["level"] == "executive" and "strategic" in ex and "material_uncertainties" in ex and "decisions" in ex and ex["comparisons"][0]["unit_id"] == dept_id
    assert ex["comparisons"][0]["discoveries"] == 1 and ex["synthesis"]["method"] == "model"
    status, body, _ = await api.call("GET", "/api/workspace/executive", token=emp_tok)
    assert status == 403
    # employee workspace
    status, ew, _ = await api.call("GET", "/api/workspace/employee", token=emp_tok)
    assert status == 200 and set(ew) >= {"goals", "questions_needing_input", "discoveries", "holders", "recent_memory", "shared", "loop_states"}
    # network views are visibility-filtered
    status, net, _ = await api.call("GET", "/api/org/network?view=hierarchy", token=emp_tok)
    assert status == 200 and {n["id"] for n in net["nodes"] if n["type"] == "unit"} == {team_id} and any(e["kind"] == "member" for e in net["edges"])
    status, net, _ = await api.call("GET", "/api/org/network?view=hierarchy", token=tok)
    assert {n["id"] for n in net["nodes"] if n["type"] == "unit"} == {root, dept_id, team_id} and sum(1 for e in net["edges"] if e["kind"] == "parent") == 2
    assert any(e["kind"] == "leads" for e in net["edges"])
    status, net, _ = await api.call("GET", "/api/org/network?view=collaboration", token=tok)
    assert status == 200 and net["nodes"]
    status, net, _ = await api.call("GET", "/api/org/network?view=lineage", token=lead_tok)
    assert status == 200 and any(n["type"] == "claim" for n in net["nodes"]) and any(n["type"] == "discovery" for n in net["nodes"])
    root_claim = _insert_claim(api.rt, tid, root, "Executive-only finding about headcount")
    status, net, _ = await api.call("GET", "/api/org/network?view=lineage", token=emp_tok)
    ids = {n["id"] for n in net["nodes"]}
    assert status == 200 and root_claim not in ids and any(n["type"] == "claim" for n in net["nodes"])   # the team claim is visible to its member, the root one is not
    status, net, _ = await api.call("GET", "/api/org/network?view=lineage", token=tok)
    assert root_claim in {n["id"] for n in net["nodes"]}
    status, body, _ = await api.call("GET", "/api/org/network?view=bogus", token=tok)
    assert status == 400
    # chat with the personal agent (fake model) cites authorized context only
    status, chat, _ = await api.call("POST", "/api/chats", token=lead_tok, body={"agent_type": "user", "agent_id": lead["user"]["user_id"]})
    assert status == 201 and chat["chat"]["agent_type"] == "user"
    status, msg, _ = await api.call("POST", f"/api/chats/{chat['chat']['chat_id']}/messages", token=lead_tok, body={"text": "What is the recurring blocker for the platform team budget?"})
    assert status == 200 and msg["message"]["role"] == "assistant" and msg["context_summary"]["claims"] >= 1
    status, body, _ = await api.call("POST", "/api/chats", token=emp_tok, body={"agent_type": "unit", "agent_id": dept_id})
    assert status == 403
    status, body, _ = await api.call("GET", f"/api/chats/{chat['chat']['chat_id']}", token=emp_tok)
    assert status == 403
    status, lst, _ = await api.call("GET", "/api/chats", token=lead_tok)
    assert status == 200 and lst["total"] == 1
    # notifications: assigning a goal notifies the assignee
    status, act, _ = await api.call("POST", f"/api/goals/{g['goal']['goal_id']}/actions", token=lead_tok, body={"action": "assign", "assignees": [{"type": "user", "id": emp["user"]["user_id"]}]})
    assert status == 200 and act["goal"]["assignees"][0]["name"] == "Eve"
    status, n, _ = await api.call("GET", "/api/notifications?unread=1", token=emp_tok)
    assert status == 200 and n["unread"] == 1 and n["items"][0]["ref_id"] == g["goal"]["goal_id"]
    status, rd, _ = await api.call("POST", "/api/notifications/read", token=emp_tok, body={"ids": [n["items"][0]["id"]]})
    assert status == 200 and rd["updated"] == 1
    status, me, _ = await api.call("GET", "/api/auth/me", token=emp_tok)
    assert me["unread_notifications"] == 0


async def test_membership_revocation_and_unit_visibility(api: Api) -> None:
    tok, reg = await api.register("acme")
    status, team, _ = await api.call("POST", "/api/org/units", token=tok, body={"type": "team", "name": "Ops"})
    team_id = team["unit"]["unit_id"]
    emp_tok, emp = await api.invite_and_accept(tok, email="emp@acme.example", role="employee", unit_id=team_id)
    status, body, _ = await api.call("GET", f"/api/org/units/{team_id}", token=emp_tok)
    assert status == 200 and body["unit"]["member_count"] == 1 and body["members"][0]["user_id"] == emp["user"]["user_id"]
    status, body, _ = await api.call("DELETE", "/api/org/memberships", token=tok, body={"user_id": emp["user"]["user_id"], "unit_id": team_id})
    assert status == 200 and body["revoked"] == 1
    status, body, _ = await api.call("GET", f"/api/org/units/{team_id}", token=emp_tok)
    assert status == 403
    status, body, _ = await api.call("PATCH", f"/api/org/users/{emp['user']['user_id']}", token=tok, body={"status": "disabled"})
    assert status == 200 and body["user"]["status"] == "disabled"
    status, body, _ = await api.call("GET", "/api/auth/me", token=emp_tok)
    assert status == 401
    status, body, _ = await api.call("PATCH", f"/api/org/users/{reg['user']['user_id']}", token=tok, body={"status": "disabled"})
    assert status == 400
