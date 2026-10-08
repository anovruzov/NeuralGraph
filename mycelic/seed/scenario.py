"""The verification scenario: ``python -m mycelic scenario`` (docs/mycelic/DEMO_SCENARIO.md, docs/mycelic/VERIFICATION.md).

It builds a complete system in a temporary data directory (fake model, hash embeddings, SQLite transport), seeds
the demonstration organization, starts the API in-process on a random port, launches Elin's and Marcus's holders
as **separate processes** with their own SQLite files and their own keys, runs the discovery worker in-process, and
then performs every check of the task list in order, recording ``{name, passed, evidence}`` for each. The result is
printed as a PASS/FAIL table and returned as a dict; the CLI exits 1 when any check failed.

Why every check is exercised through the HTTP API with a real login: the demonstration must show the production
path (session, authorization, visibility filtering), not a privileged test hook. When ``mycelic.api.create_app`` is
not importable the scenario still runs: a small stand-in serves only the two holder endpoints the holder
processes need (bootstrap and heartbeat) and the session checks run against the service layer with the same real
session token; the report says so on every line that was affected ("API not available").

Timing: the fake model makes the loop itself take a few seconds; the holder processes' start-up and the transport
polling dominate. The whole scenario stays well under the ~90 s target.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import shutil
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..jobs import JOB_KINDS
from ..runtime import Runtime, build_runtime, build_worker
from ..transport import Envelope, Subjects, TransportError
from ..util import new_id, now_iso, plus_seconds, token
from . import simulate
from .demo import DEMO_PASSWORD, HOLDER_A, HOLDER_B, MERIDIAN_SLUG, ORBITAL_SLUG, run_seed

logger = logging.getLogger(__name__)

LIVE = ("draft", "routed", "collecting", "evaluating", "verifying")
EMAIL = {k: f"{k}@meridian.example" for k in ("petra", "elin", "sofia", "maya", "anders", "ingrid", "tomas", "marcus")}
ORBITAL_EMAIL = "mira@orbital.example"
REVISED_ELIN_TEXT = ("Recurring support blocker for deployments: deploy approvals for hotfixes now take one day because the change board "
                     "meets daily. Tickets still stay open while we wait for the approval.")


# ------------------------------------------------------------------------------------------------ small helpers
class ScenarioError(RuntimeError):
    pass


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self.end - time.monotonic())


async def wait_until(pred: Callable[[], Any], *, timeout: float, deadline: Deadline | None = None, interval: float = 0.2, what: str = "") -> Any:
    """Poll ``pred`` (sync or async) until it returns a truthy value; raise ``ScenarioError`` at the timeout."""
    limit = min(timeout, deadline.remaining()) if deadline else timeout
    end = time.monotonic() + limit
    while True:
        v = pred()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        if time.monotonic() >= end:
            raise ScenarioError(f"timed out after {limit:.1f}s waiting for {what or 'condition'}")
        await asyncio.sleep(interval)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def scenario_settings(base: Path) -> Settings:
    """Settings for a self-contained run. ``Settings`` binds most defaults at import time, so the attributes are set on
    the instance rather than through the environment."""
    s = Settings()
    s.data_dir = base
    s.coord_db = str(base / "coord.db")
    s.holders_dir = str(base / "holders")
    s.host, s.port, s.public_url = "127.0.0.1", 0, ""
    s.cors_origins, s.allowed_hosts, s.secure_cookies = [], [], False
    s.secret_key = token(32)
    s.demo_mode, s.embedded_holders, s.run_worker_in_api = True, True, False
    s.transport = "sqlite"
    s.model_light, s.model_standard, s.model_heavy = "fake:mycelic-fake-light", "fake:mycelic-fake-standard", "fake:mycelic-fake-heavy"
    s.embed_provider = "hash"
    s.loop_question_timeout_seconds = 3
    s.loop_max_concurrent_questions = 2
    s.loop_cooldown_seconds = 900
    s.loop_max_followup_depth = 3
    s.loop_check_interval_seconds = 300
    s.worker_concurrency = 2
    s.worker_lease_seconds = 30.0
    s.worker_heartbeat_seconds = 2.0
    s.worker_poll_seconds = 0.2
    s.log_json = False
    s.service_name = "scenario"
    return s


def tail(path: Path, n: int = 12) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


# ------------------------------------------------------------------------------------------------ holder processes
class HolderProcess:
    """One ``python -m mycelic holder ...`` process with its own data directory, key and local owner API."""

    def __init__(self, holder_id: str, key: str, data_dir: Path, core_url: str, *, env: dict[str, str]) -> None:
        self.holder_id, self.key, self.data_dir, self.core_url = holder_id, key, data_dir, core_url
        self.local_port = free_port()
        self.log_path = data_dir / f"{holder_id}.log"
        self.db_path = data_dir / holder_id / "evidence.db"
        self.env = env
        self.proc: subprocess.Popen | None = None
        self._log = None
        self.command: list[str] = []
        self._http = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        module = ["-m", "mycelic", "holder"] if (repo_root() / "mycelic" / "__main__.py").exists() else ["-m", "mycelic.holder.process"]
        # the key goes through the environment, never argv: argv is visible to every local user (ps, /proc), and a
        # random url-safe key may start with "-", which argparse would read as an option
        self.command = [sys.executable, *module, "--holder-id", self.holder_id, "--core-url", self.core_url,
                        "--data-dir", str(self.data_dir), "--local-port", str(self.local_port), "--heartbeat-seconds", "2"]
        self._log = open(self.log_path, "w")
        env = {**(self.env or os.environ), "MYCELIC_HOLDER_KEY": self.key}
        self.proc = subprocess.Popen(self.command, cwd=str(repo_root()), env=env, stdout=self._log, stderr=subprocess.STDOUT)

    def redacted_command(self) -> str:
        return "MYCELIC_HOLDER_KEY=<key> " + " ".join(self.command)

    async def _client(self):
        import aiohttp
        if self._http is None:
            self._http = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.key}"}, timeout=aiohttp.ClientTimeout(total=15))
        return self._http

    async def request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        c = await self._client()
        async with c.request(method, self.local_url + path, **kw) as resp:
            try:
                return resp.status, await resp.json()
            except Exception:
                return resp.status, {"raw": await resp.text()}

    async def wait_ready(self, *, timeout: float, deadline: Deadline | None = None) -> dict[str, Any]:
        async def probe():
            if self.proc is not None and self.proc.poll() is not None:
                raise ScenarioError(f"holder {self.holder_id} exited with code {self.proc.returncode}; log tail:\n{tail(self.log_path)}")
            try:
                status, body = await self.request("GET", "/stats")
            except Exception:
                return None
            return body if status == 200 else None
        return await wait_until(probe, timeout=timeout, deadline=deadline, interval=0.25, what=f"holder {self.holder_id} local API")

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.proc.wait, 8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                await asyncio.get_running_loop().run_in_executor(None, self.proc.wait, 5)
        if self._log is not None:
            self._log.close()
            self._log = None


# ------------------------------------------------------------------------------------------------ the API (real or stand-in)
def build_standin_app(rt: Runtime):
    """Only what a holder process needs from the core when ``mycelic.api`` is absent: bootstrap and heartbeat."""
    from aiohttp import web

    def bearer(request: web.Request) -> str:
        h = request.headers.get("Authorization", "")
        return h[7:] if h.startswith("Bearer ") else ""

    def holder_for(request: web.Request) -> dict[str, Any] | None:
        holder = rt.auth.resolve_holder_key(bearer(request))
        if holder is None or holder["holder_id"] != request.match_info["holder_id"]:
            return None
        return holder

    async def bootstrap(request: web.Request) -> web.Response:
        holder = holder_for(request)
        if holder is None:
            return web.json_response({"error": "unauthorized", "code": "unauthorized"}, status=401)
        return web.json_response({"holder_id": holder["holder_id"], "tenant_id": holder["tenant_id"], "name": holder["name"], "mode": holder["mode"],
                                  "route_key": rt.org.route_key(holder["holder_id"]), "export_policy": holder.get("export_policy") or {}, "domains": holder.get("domains") or [],
                                  "heartbeat_seconds": 2, "transport": {"kind": "sqlite", "coord_db": rt.settings.coord_db}})

    async def heartbeat(request: web.Request) -> web.Response:
        holder = holder_for(request)
        if holder is None:
            return web.json_response({"error": "unauthorized", "code": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            body = {}
        await rt.org.holder_heartbeat(holder["holder_id"], stats=body.get("stats"), status=body.get("status") or "online")
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/holders/{holder_id}/bootstrap", bootstrap)
    app.router.add_post("/api/holders/{holder_id}/heartbeat", heartbeat)
    return app


async def build_api(rt: Runtime) -> tuple[Any, str, str]:
    """(aiohttp app, mode, note): the real API when ``mycelic.api.create_app`` exists, else the stand-in."""
    try:
        from .. import api as api_pkg
        create_app = getattr(api_pkg, "create_app", None)
        if create_app is None:
            from ..api.app import create_app  # type: ignore[no-redef]
    except Exception as exc:
        return build_standin_app(rt), "stand-in", f"API not available ({type(exc).__name__}: {str(exc)[:80]}); holder bootstrap served by the scenario stand-in"
    app = create_app(rt, rt.settings)
    if asyncio.iscoroutine(app):
        app = await app
    return app, "http", "mycelic.api.create_app"


# ------------------------------------------------------------------------------------------------ clients
class HttpSession:
    def __init__(self, base_url: str, label: str) -> None:
        import aiohttp
        self.base_url, self.label = base_url, label
        self.principal: dict[str, Any] = {}
        self._s = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                        headers={"Origin": base_url, "Accept": "application/json"}, timeout=aiohttp.ClientTimeout(total=20))

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> tuple[int, Any]:
        async with self._s.request(method, self.base_url + path, params=params, json=json) as resp:
            try:
                return resp.status, await resp.json()
            except Exception:
                return resp.status, {"raw": (await resp.text())[:300]}

    async def get(self, path: str, **params: Any) -> tuple[int, Any]:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None} or None)

    async def post(self, path: str, body: Any = None) -> tuple[int, Any]:
        return await self.request("POST", path, json=body if body is not None else {})

    async def patch(self, path: str, body: Any) -> tuple[int, Any]:
        return await self.request("PATCH", path, json=body)

    async def close(self) -> None:
        await self._s.close()


class HttpClient:
    mode = "http"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.sessions: list[HttpSession] = []

    async def login(self, email: str, *, tenant_slug: str = MERIDIAN_SLUG, password: str = DEMO_PASSWORD) -> HttpSession:
        s = HttpSession(self.base_url, email)
        status, body = await s.post("/api/auth/login", {"email": email, "password": password, "tenant_slug": tenant_slug})
        if status != 200:
            await s.close()
            raise ScenarioError(f"login as {email} failed: HTTP {status} {body}")
        s.principal = (body or {}).get("principal") or {}
        self.sessions.append(s)
        return s

    async def close_all(self) -> int:
        n = len(self.sessions)
        for s in self.sessions:
            await s.close()
        self.sessions = []
        return n


class ServiceSession:
    """The API contract of docs/mycelic/API.md answered by the service layer, for runs without ``mycelic.api``. The
    principal comes from a real session token issued by ``AuthService.login``; every call goes through the same
    authorization the API handlers use."""

    def __init__(self, rt: Runtime, principal: Any, session_token: str, label: str) -> None:
        self.rt, self.p, self.token, self.label = rt, principal, session_token, label
        self.principal = principal.to_dict()

    async def get(self, path: str, **params: Any) -> tuple[int, Any]:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, body: Any = None) -> tuple[int, Any]:
        return await self.request("POST", path, json=body or {})

    async def patch(self, path: str, body: Any) -> tuple[int, Any]:
        return await self.request("PATCH", path, json=body)

    async def close(self) -> None:
        await self.rt.auth.logout(self.token)

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> tuple[int, Any]:
        from ..inquiry import CooldownActive, DuplicateQuestion
        if self.rt.auth.resolve_session(self.token) is None:
            return 401, {"error": "no session", "code": "unauthorized"}
        for m, pattern, handler in self._routes():
            mt = re.fullmatch(pattern, path)
            if m == method and mt:
                try:
                    out = handler(*mt.groups(), params=params or {}, body=json or {})
                    if asyncio.iscoroutine(out):
                        out = await out
                    return 200, out
                except DuplicateQuestion as exc:
                    return 409, {"error": str(exc), "code": "duplicate", "existing_question_id": exc.existing_id}
                except CooldownActive as exc:
                    return 409, {"error": str(exc), "code": "cooldown", "existing_question_id": exc.existing_id}
                except PermissionError as exc:
                    return 403, {"error": str(exc), "code": "forbidden"}
                except KeyError as exc:
                    return 404, {"error": f"not found: {exc}", "code": "not_found"}
                except ValueError as exc:
                    return 400, {"error": str(exc), "code": "validation"}
                except TransportError as exc:
                    return 502, {"error": str(exc), "code": "transport"}
        return 404, {"error": f"no service route for {method} {path}", "code": "not_found"}

    # ---- handlers (the shapes of API.md, reduced to what the checks read)
    def _routes(self):
        return [
            ("GET", r"/api/auth/me", self.me),
            ("GET", r"/api/goals", self.list_goals), ("GET", r"/api/goals/([^/]+)", self.goal_detail),
            ("GET", r"/api/goals/([^/]+)/loop", self.loop_get), ("POST", r"/api/goals/([^/]+)/loop", self.loop_post),
            ("POST", r"/api/goals/([^/]+)/actions", self.goal_action), ("PATCH", r"/api/goals/([^/]+)", self.goal_patch),
            ("GET", r"/api/questions", self.list_questions), ("POST", r"/api/questions", self.create_question),
            ("GET", r"/api/questions/([^/]+)", self.question_detail),
            ("GET", r"/api/discoveries", self.list_discoveries), ("GET", r"/api/discoveries/([^/]+)", self.discovery_detail),
            ("GET", r"/api/claims", self.list_claims), ("GET", r"/api/claims/([^/]+)", self.claim_detail),
            ("GET", r"/api/evidence/([^/]+)", self.evidence_get), ("GET", r"/api/evidence/([^/]+)/raw", self.evidence_raw),
            ("GET", r"/api/holders", self.list_holders), ("GET", r"/api/holders/([^/]+)/documents", self.holder_documents),
            ("POST", r"/api/holders/([^/]+)/documents/([^/]+)/revise", self.holder_revise),
            ("GET", r"/api/workspace/employee", self.ws_employee), ("GET", r"/api/workspace/unit/([^/]+)", self.ws_unit),
            ("GET", r"/api/workspace/executive", self.ws_executive), ("GET", r"/api/admin/overview", self.admin_overview),
        ]

    def _goal(self, gid: str) -> dict[str, Any]:
        g = self.rt.goals.get_goal(gid)
        if g is None or g["tenant_id"] != self.p.tenant_id:
            raise KeyError(gid)
        self.rt.authz.require(self.rt.goals.can_view(self.p, g), "goal.view", gid)
        return g

    def me(self, *, params, body):
        return {"user": self.rt.org.get_user(self.p.id), "tenant": self.rt.org.get_tenant(self.p.tenant_id), "principal": self.principal,
                "levels": self.rt.authz.levels_for(self.p), "holders": self.rt.org.holders_for_user(self.p.id)}

    def list_goals(self, *, params, body):
        return {"items": self.rt.goals.list_goals(self.p, scope_unit_id=params.get("scope_unit_id"))}

    def goal_detail(self, gid, *, params, body):
        g = self._goal(gid)
        gs, kn = self.rt.goals, self.rt.knowledge
        return {"goal": gs.goal_view(g), "subgoals": gs.list_goals(self.p, parent_goal_id=gid), "loop": gs.loop_view(gid),
                "questions": self.rt.questions.list(self.p, goal_id=gid, limit=200), "discoveries": kn.list_discoveries(self.p, goal_id=gid),
                "outcomes": gs.outcomes(gid)}

    def loop_get(self, gid, *, params, body):
        self._goal(gid)
        return {"loop": self.rt.goals.loop_view(gid)}

    async def loop_post(self, gid, *, params, body):
        return {"loop": await self.rt.goals.loop_control(self.p, gid, body.get("action", ""))}

    async def goal_action(self, gid, *, params, body):
        out = await self.rt.goals.action(self.p, gid, body.get("action", ""), {k: v for k, v in body.items() if k != "action"})
        return {"goal": out, "subgoals": out.get("subgoals")}

    async def goal_patch(self, gid, *, params, body):
        return {"goal": await self.rt.goals.update_goal(self.p, gid, body)}

    def list_questions(self, *, params, body):
        return {"items": self.rt.questions.list(self.p, goal_id=params.get("goal_id"), status=params.get("status"), limit=200)}

    async def create_question(self, *, params, body):
        q = await self.rt.questions.create(self.p, body)
        return {"question": self.rt.questions.view(q, principal=self.p)}

    def question_detail(self, qid, *, params, body):
        return self.rt.questions.detail(self.p, qid)

    def list_discoveries(self, *, params, body):
        return {"items": self.rt.knowledge.list_discoveries(self.p, goal_id=params.get("goal_id"), level=params.get("level"), limit=200)}

    def discovery_detail(self, did, *, params, body):
        return self.rt.knowledge.discovery_detail(self.p, did)

    def list_claims(self, *, params, body):
        return {"items": self.rt.knowledge.list_claims(self.p, goal_id=params.get("goal_id"), limit=200)}

    def claim_detail(self, cid, *, params, body):
        return self.rt.knowledge.claim_detail(self.p, cid)

    def evidence_get(self, ref_id, *, params, body):
        kn = self.rt.knowledge
        ref = kn.get_ref(ref_id)
        if ref is None or ref["tenant_id"] != self.p.tenant_id:
            raise KeyError(ref_id)
        claims = [c for c in kn.claims_by_ids([r["claim_id"] for r in self.rt.db.all("SELECT claim_id FROM claim_evidence WHERE ref_id=?", (ref_id,))])
                  if self.rt.authz.can_view_scoped(self.p, c, resource_type="claim")]
        self.rt.authz.require(self.rt.authz.can_view_evidence_ref(self.p, ref, via_claim_visible=bool(claims)), "evidence.view", ref_id)
        return {"evidence_ref": kn.ref_view(ref), "claims": [kn.claim_summary(c) for c in claims]}

    async def evidence_raw(self, ref_id, *, params, body):
        ref = self.rt.knowledge.get_ref(ref_id)
        if ref is None or ref["tenant_id"] != self.p.tenant_id:
            raise KeyError(ref_id)
        holder = self.rt.org.get_holder(ref["holder_id"]) or {}
        self.rt.authz.require(self.rt.authz.can_view_raw_evidence(self.p, holder), "evidence.raw", ref_id, "raw evidence needs the owner or a raw grant")
        env = Envelope.new(Subjects.holder_raw(self.p.tenant_id, ref["holder_id"]), "raw_request", self.p.tenant_id,
                           {"ref_id": ref_id, "grant_token": f"raw:{self.p.id}:{ref_id}"}, msg_id=f"raw:{ref_id}:{new_id('r')}")
        reply = await self.rt.transport.request(env, timeout=8.0, sign_key=self.rt.org.route_key(ref["holder_id"]))
        if not reply.payload or reply.payload.get("error"):
            raise KeyError(ref_id)
        p = reply.payload
        return {"ref_id": ref_id, "holder_id": ref["holder_id"], "doc_id": p.get("doc_id"), "title": p.get("title"), "text": p.get("text"),
                "observed_at": p.get("observed_at"), "version": p.get("version")}

    def _holder(self, hid: str) -> dict[str, Any]:
        h = self.rt.org.get_holder(hid)
        if h is None or h["tenant_id"] != self.p.tenant_id:
            raise KeyError(hid)
        return h

    def list_holders(self, *, params, body):
        hs = self.rt.org.list_holders(self.p.tenant_id)
        return {"items": [h for h in hs if self.p.is_admin or self.rt.authz.can_search_holder(self.p, h)]}

    async def holder_documents(self, hid, *, params, body):
        h = self._holder(hid)
        self.rt.authz.require(self.rt.authz.can_search_holder(self.p, h), "holder.documents", hid)
        store = self.rt.holders.get(hid) if self.rt.holders is not None else None
        if store is None:
            return {"items": [], "note": "external holder: its documents live with the holder process"}
        return {"items": await store.list_documents()}

    async def holder_revise(self, hid, doc_id, *, params, body):
        h = self._holder(hid)
        self.rt.authz.require(h["owner_type"] == "user" and h["owner_id"] == self.p.id, "holder.revise", hid, "only the owner revises")
        env = Envelope.new(Subjects.holder_ingest(self.p.tenant_id, hid), "revise", self.p.tenant_id,
                           {"doc_id": doc_id, "text": body.get("text", ""), "title": body.get("title"), "observed_at": body.get("observed_at"),
                            "reason": body.get("reason", "")}, msg_id=f"revise:{doc_id}:{new_id('rv')}").sign(self.rt.org.route_key(hid))
        await self.rt.transport.publish(env)
        return {"accepted": True, "doc_id": doc_id, "status": "revising"}

    def _unit_workspace(self, unit_id: str) -> dict[str, Any]:
        az, org = self.rt.authz, self.rt.org
        unit = org.get_unit(unit_id)
        if unit is None or unit["tenant_id"] != self.p.tenant_id:
            raise KeyError(unit_id)
        az.require(unit_id in (az.visible_unit_ids(self.p) | az.led_unit_ids(self.p)), "workspace.unit", unit_id)
        led = unit_id in az.led_unit_ids(self.p)
        return {"unit": unit, "level": unit["level"], "goals": self.rt.goals.list_goals(self.p, scope_unit_id=unit_id),
                "discoveries": self.rt.knowledge.list_discoveries(self.p, scope_unit_id=unit_id),
                "open_questions": self.rt.questions.list(self.p, scope_unit_id=unit_id, live_only=True),
                "conflicts": self.rt.knowledge.list_conflicts(self.p, scope_unit_id=unit_id),
                "children": [{"unit": u} for u in org.list_units(self.p.tenant_id) if u.get("parent_id") == unit_id],
                "members": org.members_of(unit_id) if led else []}

    def ws_employee(self, *, params, body):
        goals = self.rt.goals.list_goals(self.p)
        return {"goals": goals, "questions_needing_input": self.rt.questions.list(self.p, needs_input=True),
                "discoveries": self.rt.knowledge.list_discoveries(self.p), "holders": self.rt.org.holders_for_user(self.p.id),
                "loop_states": [g["loop"] for g in goals if g.get("loop")]}

    def ws_unit(self, unit_id, *, params, body):
        return self._unit_workspace(unit_id)

    def ws_executive(self, *, params, body):
        self.rt.authz.require("executive" in self.p.roles, "workspace.executive", reason="executive role required")
        root = next(u for u in self.rt.org.list_units(self.p.tenant_id) if u["type"] == "executive")
        out = self._unit_workspace(root["unit_id"])
        out["strategic"] = [d for d in out["discoveries"] if d.get("level") == "executive"]
        return out

    def admin_overview(self, *, params, body):
        self.rt.authz.require_admin(self.p)
        workers = [dict(r) for r in self.rt.db.all("SELECT worker_id, role, hostname, last_heartbeat_at, busy FROM workers")]
        return {"workers": workers, "jobs": self.rt.jobs.counts(), "holders": self.rt.org.list_holders(self.p.tenant_id),
                "loops": self.rt.goals.loops_for_tenant(self.p.tenant_id)}


class ServiceClient:
    mode = "service"

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.sessions: list[ServiceSession] = []

    async def login(self, email: str, *, tenant_slug: str = MERIDIAN_SLUG, password: str = DEMO_PASSWORD) -> ServiceSession:
        tok, user = await self.rt.auth.login(email=email, password=password, tenant_slug=tenant_slug)
        p = self.rt.authz.principal_for_user(user["user_id"])
        s = ServiceSession(self.rt, p, tok, email)
        self.sessions.append(s)
        return s

    async def close_all(self) -> int:
        n = len(self.sessions)
        for s in self.sessions:
            await s.close()
        self.sessions = []
        return n


# ------------------------------------------------------------------------------------------------ the checks
class Ctx:
    def __init__(self, rt: Runtime, seed: dict[str, Any], client: Any, procs: dict[str, HolderProcess], deadline: Deadline, report: dict[str, Any]) -> None:
        self.rt, self.seed, self.client, self.procs, self.deadline, self.report = rt, seed, client, procs, deadline, report
        self.tid = seed["tenant_id"]
        self.gid = seed["goal_id"]
        self.sysp = rt.authz.principal_for_system(self.tid)
        self.notes: list[str] = []
        self.api_note = "" if client.mode == "http" else " [API not available: service layer with a real session token]"

    def record(self, name: str, passed: bool, evidence: str) -> None:
        self.report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence + self.api_note})

    def questions(self, **kw: Any) -> list[dict[str, Any]]:
        return self.rt.questions.list(self.sysp, goal_id=self.gid, limit=300, **kw)

    def gap_question(self, domain: str) -> dict[str, Any] | None:
        return next((q for q in self.questions() if q["depth"] == 0 and q["kind"] == "gap" and q["candidate_domains"] == [domain]), None)

    def loop_quiet(self) -> bool:
        live = self.rt.db.scalar(f"SELECT COUNT(*) FROM questions WHERE goal_id=? AND status IN ({','.join('?' * len(LIVE))})", (self.gid, *LIVE), 0)
        if live:
            return False
        runnable = self.rt.db.scalar("SELECT COUNT(*) FROM jobs WHERE status='leased' OR (status='queued' AND kind<>'demo.simulate' AND available_at <= ?)",
                                     (plus_seconds(4.0),), 0)
        return runnable == 0


async def run_check(ctx: Ctx, name: str, fn: Callable[[Ctx], Any]) -> None:
    """Every check reports; an exception is a failure with the exception as evidence, and the scenario carries on."""
    t0 = time.monotonic()
    try:
        await fn(ctx)
    except Exception as exc:
        ctx.record(name, False, f"{type(exc).__name__}: {str(exc)[:400]}")
    ctx.report["timings"][name] = round(time.monotonic() - t0, 2)


async def check_a_goal_activation(ctx: Ctx) -> None:
    petra = await ctx.client.login(EMAIL["petra"])
    status, before = await petra.get(f"/api/goals/{ctx.gid}")
    if status != 200:
        raise ScenarioError(f"GET /api/goals/{{id}} as Petra -> {status} {before}")
    status, acted = await petra.post(f"/api/goals/{ctx.gid}/actions", {"action": "activate"})
    if status != 200:
        raise ScenarioError(f"POST actions activate -> {status} {acted}")
    status, after = await petra.get(f"/api/goals/{ctx.gid}")
    goal, loop = after.get("goal") or {}, after.get("loop") or {}
    created_by = goal.get("created_by")
    ok = (goal.get("status") == "active" and loop.get("desired") == "active" and loop.get("state") in ("waiting", "active")
          and created_by == ctx.seed["users"]["petra"] and bool(goal.get("is_demo")) and str(goal.get("title", "")).startswith("[Demo]"))
    ctx.record("a. authorized user creates/activates a goal; loop on", ok,
               f"Petra (regional_lead, session via POST /api/auth/login) activated {ctx.gid} which her principal created "
               f"(created_by={created_by}); status={goal.get('status')} loop.desired={loop.get('desired')} loop.state={loop.get('state')} "
               f"explanation={str(loop.get('explanation'))[:60]!r} budget={goal.get('budget')}")


async def check_b_automatic_question(ctx: Ctx) -> None:
    dep = ctx.gap_question("deployments")
    petra = await ctx.client.login(EMAIL["petra"])
    status, body = await petra.get("/api/questions", goal_id=ctx.gid, limit=200)
    items = (body or {}).get("items") or []
    loop_qs = [q for q in items if (q.get("asker") or {}).get("type") == "loop"]
    api_dep = next((q for q in items if q.get("question_id") == (dep or {}).get("question_id")), None)
    ok = bool(dep) and status == 200 and api_dep is not None and (api_dep.get("asker") or {}).get("type") == "loop" \
        and len(api_dep.get("routes") or []) >= 2 and "deployments" in api_dep.get("text", "") \
        and (api_dep.get("priority_breakdown") or {}).get("method") == "heuristic"
    ctx.record("b. loop generates and routes a relevant question", ok,
               f"{len(loop_qs)} loop-asked questions visible to Petra; deployments question {(dep or {}).get('question_id')} "
               f"text={(api_dep or {}).get('text', '')[:70]!r} routes={[r.get('holder_id') for r in (api_dep or {}).get('routes') or []]} "
               f"trigger={((dep or {}).get('trigger') or {}).get('kind')} priority.method={((api_dep or {}).get('priority_breakdown') or {}).get('method')}")


async def check_c_separate_holders(ctx: Ctx) -> None:
    rows = [r for r in ctx.rt.db.all("SELECT * FROM responses WHERE tenant_id=? AND holder_id IN (?, ?) AND status='answered' ORDER BY received_at",
                                     (ctx.tid, HOLDER_A, HOLDER_B))]
    by_holder: dict[str, list[dict[str, Any]]] = {HOLDER_A: [], HOLDER_B: []}
    leaks: list[str] = []
    known_doc_ids = [d["doc_id"] for d in ctx.seed["pending_external_documents"]]
    for r in rows:
        d = dict(r)
        by_holder[d["holder_id"]].append(d)
        blob = json.dumps(d)
        refs = json.loads(d["evidence_ref_ids"] or "[]")
        if not refs or not all(str(x).startswith("ev_") for x in refs):
            leaks.append(f"{d['response_id']}: refs {refs}")
        if "doc_" in blob or "mem_" in blob or any(did in blob for did in known_doc_ids):
            leaks.append(f"{d['response_id']}: identifier leaked")
    procs = ctx.procs
    a, b = procs[HOLDER_A], procs[HOLDER_B]
    separate = a.pid != b.pid and a.db_path != b.db_path and a.db_path.exists() and b.db_path.exists() \
        and ctx.rt.holders.get(HOLDER_A) is None and ctx.rt.holders.get(HOLDER_B) is None \
        and ctx.rt.org.holder_secret_row(HOLDER_A)["key_hash"] != ctx.rt.org.holder_secret_row(HOLDER_B)["key_hash"]
    online = {h: (ctx.rt.org.get_holder(h) or {}).get("status") for h in (HOLDER_A, HOLDER_B)}
    ok = bool(by_holder[HOLDER_A]) and bool(by_holder[HOLDER_B]) and not leaks and separate
    ctx.record("c. separate holders respond from their own stores", ok,
               f"answered responses: {HOLDER_A}={len(by_holder[HOLDER_A])} (pid {a.pid}, {a.db_path}), {HOLDER_B}={len(by_holder[HOLDER_B])} "
               f"(pid {b.pid}, {b.db_path}); core pid {os.getpid()} holds no store for them; distinct key hashes; registry status {online}; "
               f"refs opaque (ev_*), no doc_/mem_ ids in payloads; leaks={leaks or 'none'}")


async def check_d_supported_discovery_and_followup(ctx: Ctx) -> None:
    petra = await ctx.client.login(EMAIL["petra"])
    status, body = await petra.get("/api/discoveries", goal_id=ctx.gid, limit=200)
    items = (body or {}).get("items") or []
    supported = [d for d in items if d.get("kind") == "finding" and int((d.get("support") or {}).get("independent_roots") or 0) >= 2]
    followups = [q for q in ctx.questions() if int(q.get("depth") or 0) >= 1 and q.get("kind") in ("verification", "contradiction")]
    with_fu = [d for d in items if d.get("followup_question_ids")]
    fu = next((q for q in followups if q.get("motivating_lineage")), followups[0] if followups else None)
    ok = status == 200 and bool(supported) and fu is not None and bool(with_fu)
    ctx.record("d. supported discovery and a useful follow-up question", ok,
               f"{len(items)} discoveries visible to Petra; supported findings with >=2 independent roots: "
               f"{[(d['discovery_id'], d['support']['independent_roots']) for d in supported][:3]}; discoveries with follow-ups: {len(with_fu)}; "
               f"follow-up {(fu or {}).get('question_id')} kind={(fu or {}).get('kind')} depth={(fu or {}).get('depth')} "
               f"text={(fu or {}).get('text', '')[:80]!r} lineage={[(x.get('type'), x.get('id')) for x in ((fu or {}).get('motivating_lineage') or [])][:2]}")


def _visible_units(ctx: Ctx, user_id: str) -> set[str]:
    p = ctx.rt.authz.principal_for_user(user_id)
    return ctx.rt.authz.visible_unit_ids(p) | ctx.rt.authz.led_unit_ids(p)


def _goal_scope(g: dict[str, Any]) -> str | None:
    return g.get("scope_unit_id") or (g.get("owner_id") if g.get("owner_type") == "unit" else None)


async def check_e_level_abstraction(ctx: Ctx) -> None:
    units, users = ctx.seed["units"], ctx.seed["users"]
    personas = [("elin", "employee", "/api/workspace/employee"), ("sofia", "team", f"/api/workspace/unit/{units['support']}"),
                ("maya", "department", f"/api/workspace/unit/{units['operations']}"), ("anders", "subsidiary", f"/api/workspace/unit/{units['nordics']}"),
                ("petra", "region", f"/api/workspace/unit/{units['europe']}"), ("ingrid", "executive", "/api/workspace/executive")]
    problems: list[str] = []
    seen: dict[str, dict[str, Any]] = {}
    for key, level, path in personas:
        s = await ctx.client.login(EMAIL[key])
        status, body = await s.get(path)
        if status != 200:
            problems.append(f"{key}: {path} -> {status}")
            continue
        vis = _visible_units(ctx, users[key])
        goals = (body or {}).get("goals") or []
        discs = (body or {}).get("discoveries") or []
        bad_goals = [g["goal_id"] for g in goals if _goal_scope(g) not in vis and users[key] not in (g.get("created_by"), g.get("owner_id"))]
        bad_discs = [d["discovery_id"] for d in discs if d.get("scope_unit_id") not in vis]
        if bad_goals or bad_discs:
            problems.append(f"{key}: out-of-scope goals {bad_goals} discoveries {bad_discs}")
        if level != "employee" and (body or {}).get("level") != level:
            problems.append(f"{key}: level {(body or {}).get('level')!r} != {level!r}")
        seen[key] = {"goals": len(goals), "discoveries": len(discs), "levels": sorted({str(d.get("level")) for d in discs}), "level": (body or {}).get("level"),
                     "goal_ids": [g.get("goal_id") for g in goals]}
    above_team = {"department", "subsidiary", "region", "executive"}
    if set(seen.get("elin", {}).get("levels", [])) & above_team or ctx.gid in seen.get("elin", {}).get("goal_ids", []):
        problems.append("employee sees discoveries above her team or the region goal")
    if seen.get("petra", {}).get("discoveries", 0) < 1 or seen.get("ingrid", {}).get("discoveries", 0) < 1:
        problems.append("regional lead / executive see no discoveries")
    if "executive" not in seen.get("ingrid", {}).get("levels", []) and "region" not in seen.get("ingrid", {}).get("levels", []):
        problems.append("executive workspace lacks org-level discoveries")
    order = [seen.get(k, {}).get("goals", 0) for k in ("elin", "sofia", "maya", "anders", "petra", "ingrid")]
    if order != sorted(order):
        problems.append(f"goal visibility is not monotonic up the hierarchy: {order}")
    # the administrator manages, but does not read knowledge
    tomas = await ctx.client.login(EMAIL["tomas"])
    disc = ctx.rt.knowledge.list_discoveries(ctx.sysp, goal_id=ctx.gid)[0]
    claim = ctx.rt.knowledge.list_claims(ctx.sysp, goal_id=ctx.gid)[0]
    s1, _ = await tomas.get(f"/api/discoveries/{disc['discovery_id']}")
    s2, _ = await tomas.get(f"/api/claims/{claim['claim_id']}")
    s3, overview = await tomas.get("/api/admin/overview")
    s4, lst = await tomas.get("/api/discoveries", goal_id=ctx.gid)
    admin_ok = s1 == 403 and s2 == 403 and s3 == 200 and "workers" in (overview or {}) and (s4 != 200 or not ((lst or {}).get("items") or []))
    if not admin_ok:
        problems.append(f"admin: discovery {s1}, claim {s2}, overview {s3}, list {s4}")
    ctx.record("e. each level sees the appropriate abstraction", not problems,
               f"employee Elin {seen.get('elin')}; team lead Sofia {seen.get('sofia')}; department lead Maya {seen.get('maya')}; "
               f"subsidiary lead Anders {seen.get('anders')}; regional lead Petra {seen.get('petra')}; executive Ingrid {seen.get('ingrid')}; "
               f"admin Tomas: discovery {s1}, claim {s2}, /admin/overview {s3} ({len((overview or {}).get('workers') or [])} workers); problems={problems or 'none'}")


def _elin_ref(ctx: Ctx) -> dict[str, Any] | None:
    dep = ctx.gap_question("deployments")
    if dep is None:
        return None
    for c in ctx.rt.knowledge.list_claims(ctx.sysp, question_id=dep["question_id"]):
        for r in ctx.rt.knowledge.refs_for_claim(c["claim_id"]):
            if r["holder_id"] == HOLDER_A:
                return r
    return None


async def check_f_private_evidence(ctx: Ctx) -> None:
    ref = _elin_ref(ctx)
    if ref is None:
        raise ScenarioError("no evidence reference from Elin's holder is cited by the deployments claim")
    rid = ref["ref_id"]
    elin, sofia, tomas = [await ctx.client.login(EMAIL[k]) for k in ("elin", "sofia", "tomas")]
    s_elin, raw = await elin.get(f"/api/evidence/{rid}/raw")
    s_sofia, _ = await sofia.get(f"/api/evidence/{rid}/raw")
    s_tomas, _ = await tomas.get(f"/api/evidence/{rid}/raw")
    elin_text = next(d["text"] for d in ctx.seed["pending_external_documents"] if d["holder_id"] == HOLDER_A)
    raw_ok = s_elin == 200 and (raw or {}).get("text", "").strip() == elin_text and (raw or {}).get("doc_id") == "doc_demo_elin_1"
    # the other tenant: every Meridian object is invisible and every list is empty
    mira = await ctx.client.login(ORBITAL_EMAIL, tenant_slug=ORBITAL_SLUG)
    disc = ctx.rt.knowledge.list_discoveries(ctx.sysp, goal_id=ctx.gid)[0]
    claim = ctx.rt.knowledge.list_claims(ctx.sysp, goal_id=ctx.gid)[0]
    q = ctx.gap_question("deployments") or {}
    detail_paths = [f"/api/goals/{ctx.gid}", f"/api/discoveries/{disc['discovery_id']}", f"/api/claims/{claim['claim_id']}",
                    f"/api/questions/{q.get('question_id')}", f"/api/evidence/{rid}", f"/api/evidence/{rid}/raw", f"/api/holders/{HOLDER_A}/documents",
                    f"/api/workspace/unit/{ctx.seed['units']['europe']}"]
    detail_status = {}
    for p in detail_paths:
        st, _ = await mira.get(p)
        detail_status[p.split("/api/")[1].split("/")[0] + ("/raw" if p.endswith("/raw") else "")] = st
    lists = {}
    meridian_ids = {ctx.gid, disc["discovery_id"], claim["claim_id"], q.get("question_id"), HOLDER_A, HOLDER_B} | set(ctx.seed["holders"].values())
    for p in ("/api/goals", "/api/discoveries", "/api/claims", "/api/questions", "/api/holders"):
        st, body = await mira.get(p)
        items = (body or {}).get("items") or []
        ids = {i.get("goal_id") or i.get("discovery_id") or i.get("claim_id") or i.get("question_id") or i.get("holder_id") for i in items}
        lists[p.rsplit("/", 1)[1]] = (st, len(items), bool(ids & meridian_ids))
    isolated = all(st in (403, 404) for st in detail_status.values()) and all(st == 200 and not leak for st, _n, leak in lists.values()) \
        and all(n == 0 for _s, n, _l in [lists[k] for k in ("goals", "discoveries", "claims", "questions")])
    ok = raw_ok and s_sofia == 403 and s_tomas == 403 and isolated
    ctx.record("f. private evidence is inaccessible", ok,
               f"GET /api/evidence/{rid}/raw: Elin (owner) {s_elin} text matches her document (fetched from the holder process over the transport), "
               f"Sofia (artifact grant) {s_sofia}, Tomas (admin) {s_tomas}; Orbital executive on Meridian objects: {detail_status}; "
               f"Orbital lists (status, items, meridian ids present): {lists}")


async def check_g_copied_evidence(ctx: Ctx) -> None:
    dep = ctx.gap_question("deployments")
    if dep is None:
        raise ScenarioError("no deployments question")
    claims = ctx.rt.knowledge.list_claims(ctx.sysp, question_id=dep["question_id"])
    sup = next((c for c in claims if c["status"] == "supported"), None)
    if sup is None:
        raise ScenarioError(f"no supported claim for the deployments question: {[(c['status'], c['support']) for c in claims]}")
    petra = await ctx.client.login(EMAIL["petra"])
    status, detail = await petra.get(f"/api/claims/{sup['claim_id']}")
    refs = (detail or {}).get("evidence") or []
    support = (detail or {}).get("support") or sup["support"]
    roots = ((detail or {}).get("dependencies") or {}).get("roots") or []
    shared = [r for r in roots if int(r.get("ref_count") or 0) > 1]
    holders = sorted({r.get("holder_id") for r in refs})
    ok = status == 200 and int(support.get("independent_roots") or 0) == 2 and int(support.get("copied_refs") or 0) >= 1 and len(refs) == 3 \
        and any(HOLDER_A in (r.get("holders") or []) for r in shared)
    ctx.record("g. copied evidence does not inflate support", ok,
               f"deployments claim {sup['claim_id']}: {len(refs)} references from {holders}, independent_roots={support.get('independent_roots')}, "
               f"copied_refs={support.get('copied_refs')}, unknown={support.get('unknown_independence')}; shared root(s): "
               f"{[(r['source_root_id'][:14], r['ref_count'], r['holders']) for r in shared]} (Elin's retrospective and Ana's forwarded copy)")


async def check_h_source_revision(ctx: Ctx) -> None:
    ref = _elin_ref(ctx)
    if ref is None:
        raise ScenarioError("no evidence reference from Elin's holder")
    rid = ref["ref_id"]
    affected = [r["claim_id"] for r in ctx.rt.db.all("SELECT claim_id FROM claim_evidence WHERE ref_id=?", (rid,))]
    before = {cid: (ctx.rt.knowledge.get_claim(cid) or {}).get("status") for cid in affected}
    elin = await ctx.client.login(EMAIL["elin"])
    status, body = await elin.post(f"/api/holders/{HOLDER_A}/documents/doc_demo_elin_1/revise",
                                   {"text": REVISED_ELIN_TEXT, "reason": "the change board now meets daily"})
    path = f"POST /api/holders/{HOLDER_A}/documents/{{doc}}/revise -> {status}"
    if status not in (200, 201, 202):
        # the documented path is the API; when it refuses, send the same signed revise envelope the API sends
        env = Envelope.new(Subjects.holder_ingest(ctx.tid, HOLDER_A), "revise", ctx.tid,
                           {"doc_id": "doc_demo_elin_1", "text": REVISED_ELIN_TEXT, "reason": "the change board now meets daily"},
                           msg_id=f"revise:doc_demo_elin_1:{new_id('rv')}").sign(ctx.rt.org.route_key(HOLDER_A))
        await ctx.rt.transport.publish(env)
        path += f" ({body}); fell back to the signed revise envelope over the transport"

    def revised():
        r = ctx.rt.knowledge.get_ref(rid)
        return r is not None and r["status"] == "revised"
    await wait_until(revised, timeout=20, deadline=ctx.deadline, what="evidence reference marked revised")

    def verification():
        return [q for q in ctx.questions() if (q.get("trigger") or {}).get("kind") == "evidence_revised" and (q.get("trigger") or {}).get("claim_id") in affected]
    vqs = await wait_until(verification, timeout=20, deadline=ctx.deadline, what="re-verification question")
    stale_revs = {cid: any((r.get("after") or {}).get("status") == "stale" for r in ctx.rt.knowledge.revisions_for("claim", cid)) for cid in affected}
    ref_after = ctx.rt.knowledge.get_ref(rid) or {}
    # the holder process itself reports the new version through its local owner API
    st, doc = await ctx.procs[HOLDER_A].request("GET", "/documents/doc_demo_elin_1")
    local_version = ((doc or {}).get("document") or {}).get("version")
    local_ok = st == 200 and local_version == 2 and (doc or {}).get("text", "").strip() == REVISED_ELIN_TEXT
    ok = bool(affected) and all(stale_revs.values()) and ref_after.get("status") == "revised" and int(ref_after.get("version") or 0) >= 2 and bool(vqs) and local_ok
    ctx.record("h. a source revision updates and invalidates derived claims", ok,
               f"{path}; ref {rid} status={ref_after.get('status')} version={ref_after.get('version')}; affected claims {affected} "
               f"before={list(before.values())} stale revision recorded={stale_revs}; re-verification question(s) "
               f"{[q['question_id'] for q in vqs]} (trigger evidence_revised, kind {vqs[0]['kind']}); holder process local API reports version {local_version}")


async def check_i_worker_restart(ctx: Ctx) -> None:
    rt = ctx.rt
    await wait_until(ctx.loop_quiet, timeout=30, deadline=ctx.deadline, what="a quiet loop before the restart test")
    old = rt.worker
    if old is not None:
        await old.stop()
    # a worker that handles everything except evaluation, so the evaluate job is ours to lease and abandon
    w2 = build_worker(rt, worker_id="w-scenario-2")
    w2.kinds = [k for k in JOB_KINDS if k != "question.evaluate"]
    await w2.start()
    rt.worker = w2
    petra = await ctx.client.login(EMAIL["petra"])
    status, body = await petra.post("/api/questions", {"text": "Which recurring blockers related to support delayed ticket resolution, and what caused them?",
                                                       "goal_id": ctx.gid, "candidate_domains": ["support"]})
    if status not in (200, 201):
        raise ScenarioError(f"POST /api/questions -> {status} {body}")
    qid = ((body or {}).get("question") or body or {}).get("question_id")

    def evaluate_queued():
        return rt.db.one("SELECT job_id FROM jobs WHERE idempotency_key=? AND status='queued'", (f"question.evaluate:{qid}",))
    await wait_until(evaluate_queued, timeout=25, deadline=ctx.deadline, what="the evaluate job of the restart question")
    job = None
    for _ in range(10):
        cand = await rt.jobs.lease("w-crashed", 0.5, kinds=["question.evaluate"])
        if cand is None:
            break
        if cand.ref_id == qid:
            job = cand
            break
        await rt.engine.handle(cand)             # somebody else's evaluate: finish it properly
        await rt.jobs.complete(cand.job_id, "w-crashed")
    if job is None:
        raise ScenarioError("could not lease the evaluate job")
    first = await rt.engine.evaluate(job)        # effects written (claims, checkpoint, commit job) ...
    claims_after_first = len(rt.knowledge.list_claims(ctx.sysp, question_id=qid, include_retracted=True))
    await asyncio.sleep(0.8)                     # ... but the worker "dies" before acknowledging: the lease expires
    await w2.stop()
    w3 = build_worker(rt, worker_id="w-scenario-3")
    await w3.start()                             # requeues the expired lease and re-runs evaluate, then commits
    rt.worker = w3

    def committed():
        # resolved, and the abandoned evaluate job replayed to completion by the new worker
        q = rt.questions.get(qid)
        j = rt.jobs.get(job.job_id)
        return q is not None and q["status"] in ("committed", "retained_uncertain") and j is not None and j.status == "done"
    await wait_until(committed, timeout=25, deadline=ctx.deadline, what="the restart question to be committed and the abandoned job replayed")
    claims_final = rt.knowledge.list_claims(ctx.sysp, question_id=qid, include_retracted=True)
    discs = rt.knowledge.list_discoveries(ctx.sysp, goal_id=ctx.gid)
    discs_q = [d for d in discs if d.get("question_id") == qid]
    attempts = rt.jobs.attempts(job.job_id)
    outcomes = [a.get("outcome") for a in attempts]
    evaluation = (rt.engine._checkpoint(qid).get("state") or {}).get("evaluation") or {}
    expected_claims = len([f for f in evaluation.get("findings") or [] if f.get("text")]) + 2 * len(evaluation.get("disagreements") or [])
    replay = await rt.engine.evaluate(job)       # a third evaluate of the same job: a no-op
    claims_replay = len(rt.knowledge.list_claims(ctx.sysp, question_id=qid, include_retracted=True))
    texts = [c["text"] for c in claims_final]
    ok = len(claims_final) == claims_after_first == claims_replay == expected_claims and len(discs_q) == 1 and len(set(texts)) == len(texts) \
        and outcomes[:1] == ["lost_lease"] and "ok" in outcomes and replay.get("skipped") is not None and bool(first.get("claims"))
    ctx.record("i. worker restart preserves progress without duplicating effects", ok,
               f"question {qid}: evaluate job {job.job_id} attempts={outcomes} (w-crashed abandoned the lease after writing, w-scenario-3 requeued and "
               f"replayed it); claims after crash={claims_after_first}, final={len(claims_final)}, expected from the evaluation={expected_claims}, "
               f"after a third evaluate={claims_replay} (replay result {replay}); discoveries for the question={len(discs_q)}; status={rt.questions.get(qid)['status']}")


async def check_k_progress_without_client(ctx: Ctx) -> None:
    rt = ctx.rt
    closed = await ctx.client.close_all()
    for p in ctx.procs.values():
        if p._http is not None:
            await p._http.close()
            p._http = None
    goal = rt.goals.get_goal(ctx.gid)
    base_tokens = int((goal.get("budget_spent") or {}).get("tokens") or 0)
    base_done = int(rt.jobs.counts().get("done", 0))
    base_runs = int((rt.goals.get_loop(ctx.gid) or {}).get("run_count") or 0)
    job_id = await simulate.run_now(rt, ctx.tid)

    def progressed():
        g = rt.goals.get_goal(ctx.gid)
        tokens = int((g.get("budget_spent") or {}).get("tokens") or 0)
        done = int(rt.jobs.counts().get("done", 0))
        return tokens > base_tokens and done > base_done
    t0 = time.monotonic()
    await wait_until(progressed, timeout=25, deadline=ctx.deadline, interval=0.5, what="worker progress with no HTTP client connected")
    g = rt.goals.get_goal(ctx.gid)
    loop = rt.goals.get_loop(ctx.gid) or {}
    sim = rt.jobs.get(job_id) if job_id else None
    bs = rt.goals.budget_status(ctx.gid)
    tokens = int((g.get("budget_spent") or {}).get("tokens") or 0)
    done = int(rt.jobs.counts().get("done", 0))
    dead = int(rt.jobs.counts().get("dead", 0))
    errors = [r["last_error"] for r in rt.db.all("SELECT last_error FROM jobs WHERE last_error IS NOT NULL AND last_error NOT LIKE 'lease expired%' "
                                                 "AND last_error NOT LIKE 'crash injected%' ORDER BY job_id DESC LIMIT 3")]
    # progress alone is not enough: the loop must be healthy (not failed) and no job may have died or raised along the way
    ok = (closed >= 1 and tokens > base_tokens and done > base_done and not bs["exhausted"] and (sim is None or sim.status == "done")
          and loop.get("state") not in ("failed", "blocked") and dead == 0 and not errors)
    ctx.record("k. the worker progresses within budget with no HTTP client", ok,
               f"closed {closed} client session(s); demo.simulate job {job_id} -> {getattr(sim, 'status', None)} ({(getattr(sim, 'result', None) or {}).get('title')} "
               f"into {(getattr(sim, 'result', None) or {}).get('holder_id')} via {(getattr(sim, 'result', None) or {}).get('delivery')}); after {time.monotonic() - t0:.1f}s: "
               f"jobs done {base_done}->{done}, loop runs {base_runs}->{loop.get('run_count')}, budget_spent.tokens {base_tokens}->{tokens} "
               f"(budget {g['budget'].get('tokens')}, exhausted={bs['exhausted']}), loop state={loop.get('state')}; dead jobs={dead}; job errors={errors or 'none'}")


async def check_j_loop_controls(ctx: Ctx) -> None:
    petra = await ctx.client.login(EMAIL["petra"])
    steps: list[str] = []

    async def control(action: str) -> dict[str, Any]:
        status, body = await petra.post(f"/api/goals/{ctx.gid}/loop", {"action": action})
        if status != 200:
            raise ScenarioError(f"loop {action} -> {status} {body}")
        return (body or {}).get("loop") or {}

    async def loop_state(*wanted: str, timeout: float = 12) -> dict[str, Any]:
        async def cur():
            st, body = await petra.get(f"/api/goals/{ctx.gid}/loop")
            lp = (body or {}).get("loop") or {}
            return lp if lp.get("state") in wanted else None
        return await wait_until(cur, timeout=timeout, deadline=ctx.deadline, interval=0.3, what=f"loop state in {wanted}")

    lp = await control("pause")
    paused = lp.get("state") == "paused" and lp.get("desired") == "paused"
    steps.append(f"pause -> state={lp.get('state')} desired={lp.get('desired')} ({str(lp.get('explanation'))[:40]!r})")
    lp = await control("resume")
    lp = await loop_state("waiting", "active")
    resumed = lp.get("desired") == "active"
    steps.append(f"resume -> state={lp.get('state')} desired={lp.get('desired')} ({str(lp.get('explanation'))[:40]!r})")
    goal = ctx.rt.goals.get_goal(ctx.gid)
    budget = dict(goal.get("budget") or {})
    st, body = await petra.patch(f"/api/goals/{ctx.gid}", {"budget": {**budget, "tokens": 1}})
    if st != 200:
        raise ScenarioError(f"PATCH budget -> {st} {body}")
    await control("run_now")
    lp = await loop_state("budget_exhausted")
    exhausted = "tokens" in str(lp.get("explanation", ""))
    steps.append(f"budget tokens=1 + run_now -> state={lp.get('state')} ({str(lp.get('explanation'))[:60]!r}) remaining={lp.get('budget_remaining')}")
    await petra.patch(f"/api/goals/{ctx.gid}", {"budget": budget})
    await control("run_now")
    lp = await loop_state("waiting", "active")
    restored = lp.get("state") in ("waiting", "active")
    steps.append(f"budget restored + run_now -> state={lp.get('state')} ({str(lp.get('explanation'))[:40]!r})")
    lp = await control("stop")
    stopped = lp.get("state") == "stopped" and lp.get("desired") == "stopped"
    steps.append(f"stop -> state={lp.get('state')} desired={lp.get('desired')}")
    ctx.record("j. pause / resume / budget / stop via the loop endpoints", paused and resumed and exhausted and restored and stopped, "; ".join(steps))


async def check_l_return_and_inspect(ctx: Ctx) -> None:
    dep = ctx.gap_question("deployments")
    disc = next((d for d in ctx.rt.knowledge.list_discoveries(ctx.sysp, goal_id=ctx.gid) if d.get("question_id") == (dep or {}).get("question_id")), None)
    if disc is None:
        raise ScenarioError("no discovery for the deployments question")
    petra = await ctx.client.login(EMAIL["petra"])          # a fresh session: "returning" after the run
    status, detail = await petra.get(f"/api/discoveries/{disc['discovery_id']}")
    claims = (detail or {}).get("claims") or []
    evidence = (detail or {}).get("evidence") or []
    lineage = (detail or {}).get("lineage") or {}
    nodes, edges = lineage.get("nodes") or [], lineage.get("edges") or []
    kinds = sorted({n.get("type") for n in nodes})
    excerpts = all(e.get("disclosed_excerpt") for e in evidence)
    elin = await ctx.client.login(EMAIL["elin"])
    s_elin, _ = await elin.get(f"/api/discoveries/{disc['discovery_id']}")
    ok = status == 200 and claims and evidence and nodes and edges and excerpts and s_elin == 403
    ctx.record("l. a fresh login inspects the discovery and its permitted lineage", bool(ok),
               f"GET /api/discoveries/{disc['discovery_id']} as Petra (new session) -> {status}: {len(claims)} claims, {len(evidence)} evidence refs "
               f"(disclosed excerpts only), lineage {len(nodes)} nodes {kinds} / {len(edges)} edges; the same read as Elin (team employee) -> {s_elin}")


# ------------------------------------------------------------------------------------------------ orchestration
async def _push_pending_documents(procs: dict[str, HolderProcess], pending: list[dict[str, Any]]) -> list[str]:
    pushed = []
    for doc in pending:
        p = procs[doc["holder_id"]]
        status, body = await p.request("POST", "/documents", json={k: doc[k] for k in ("doc_id", "title", "text", "kind", "observed_at", "domains", "uploaded_by")})
        if status not in (200, 201):
            raise ScenarioError(f"POST {p.local_url}/documents ({doc['doc_id']}) -> {status} {body}")
        pushed.append(f"{doc['doc_id']}->{doc['holder_id']} v{(body.get('document') or {}).get('version')}")
    return pushed


def print_table(report: dict[str, Any]) -> str:
    lines = [f"Mycelic verification scenario  ({report.get('started_at')}; api={report.get('api', {}).get('mode')}; data_dir={report.get('data_dir')})", ""]
    width = max((len(c["name"]) for c in report["checks"]), default=20)
    lines.append(f"{'RESULT':6}  {'CHECK':<{width}}  EVIDENCE")
    lines.append("-" * (width + 90))
    for c in report["checks"]:
        lines.append(f"{'PASS' if c['passed'] else 'FAIL':6}  {c['name']:<{width}}  {c['evidence']}")
    lines.append("-" * (width + 90))
    n_ok = sum(1 for c in report["checks"] if c["passed"])
    lines.append(f"{n_ok}/{len(report['checks'])} checks passed in {report.get('duration_seconds')}s; holders: "
                 + ", ".join(f"{h} pid {v.get('pid')} db {v.get('db_path')}" for h, v in report.get("holders", {}).items()))
    text = "\n".join(lines)
    print(text)
    return text


async def run_scenario(rt: Runtime | None = None, *, data_dir: str | None = None, timeout: float = 120.0, keep: bool = False) -> dict[str, Any]:
    """Run every check; see the module docstring. Returns the report dict (``checks``, ``api``, ``holders``, ``timings`` ...)."""
    from aiohttp import web

    deadline = Deadline(timeout)
    t_start = time.monotonic()
    base = Path(data_dir).expanduser() if data_dir else Path(tempfile.mkdtemp(prefix="mycelic-scenario-"))
    base.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started_at": now_iso(), "data_dir": str(base), "checks": [], "api": {}, "holders": {}, "timings": {}, "notes": []}
    own_rt = rt is None
    if own_rt:
        rt = build_runtime(scenario_settings(base))
    procs: dict[str, HolderProcess] = {}
    runner = None
    client: Any = None
    try:
        await rt.start(run_worker=False, run_holders=True)
        key_a, key_b = token(24), token(24)
        seed = await run_seed(rt, external_holder_keys={HOLDER_A: key_a, HOLDER_B: key_b})
        report["tenant_id"], report["goal_id"] = seed["tenant_id"], seed["goal_id"]
        # the API (real when present, otherwise the holder-bootstrap stand-in) on a random free port
        app, mode, note = await build_api(rt)
        port = free_port()
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        base_url = f"http://127.0.0.1:{port}"
        report["api"] = {"mode": mode, "base_url": base_url, "note": note}
        if mode != "http":
            report["notes"].append(note)
        # two holders as separate processes, each with its own directory, database file and key
        env = {**os.environ, "MYCELIC_DATA_DIR": str(base), "MYCELIC_COORD_DB": rt.settings.coord_db, "MYCELIC_TRANSPORT": "sqlite",
               "MYCELIC_LOG_JSON": "0", "MYCELIC_LOG_LEVEL": "INFO", "MYCELIC_EMBED_PROVIDER": "hash", "MYCELIC_MODEL_LIGHT": "fake:mycelic-fake-light",
               "MYCELIC_MODEL_STANDARD": "fake:mycelic-fake-standard", "MYCELIC_MODEL_HEAVY": "fake:mycelic-fake-heavy", "PYTHONUNBUFFERED": "1",
               "PYTHONPATH": str(repo_root()) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
        for hid, key, sub in ((HOLDER_A, key_a, "holder-a"), (HOLDER_B, key_b, "holder-b")):
            procs[hid] = HolderProcess(hid, key, base / sub, base_url, env=env)
            procs[hid].start()
        for hid, p in procs.items():
            stats = await p.wait_ready(timeout=40, deadline=deadline)
            report["holders"][hid] = {"pid": p.pid, "db_path": str(p.db_path), "local_port": p.local_port, "command": p.redacted_command(),
                                      "log": str(p.log_path), "documents_at_start": stats.get("documents")}
        pushed = await _push_pending_documents(procs, seed["pending_external_documents"])
        report["notes"].append(f"documents pushed through the holder processes' local API: {pushed}")
        await wait_until(lambda: all((rt.org.get_holder(h) or {}).get("status") == "online" for h in procs), timeout=20, deadline=deadline,
                         what="holder processes to heartbeat as online")
        client = HttpClient(base_url) if mode == "http" else ServiceClient(rt)
        ctx = Ctx(rt, seed, client, procs, deadline, report)
        await run_check(ctx, "a", check_a_goal_activation)
        # the discovery worker, in-process, as `serve` with MYCELIC_RUN_WORKER_IN_API=1 would run it
        rt.worker = build_worker(rt, worker_id="w-scenario-1")
        await rt.worker.start()
        t_loop = time.monotonic()
        try:
            await wait_until(lambda: all((ctx.gap_question(d) or {}).get("status") == "committed" for d in ("deployments", "approvals")),
                             timeout=60, deadline=deadline, what="the deployments and approvals questions to be committed")
            await wait_until(ctx.loop_quiet, timeout=40, deadline=deadline, what="the loop to become quiet")
        except ScenarioError as exc:
            report["notes"].append(str(exc))
        report["timings"]["loop"] = round(time.monotonic() - t_loop, 2)
        for name, fn in (("b", check_b_automatic_question), ("c", check_c_separate_holders), ("d", check_d_supported_discovery_and_followup),
                         ("g", check_g_copied_evidence), ("e", check_e_level_abstraction), ("f", check_f_private_evidence),
                         ("h", check_h_source_revision), ("i", check_i_worker_restart), ("k", check_k_progress_without_client),
                         ("j", check_j_loop_controls), ("l", check_l_return_and_inspect)):
            await run_check(ctx, name, fn)
        report["checks"].sort(key=lambda c: c["name"])
        report["questions"] = len(ctx.questions())
        report["claims"] = rt.knowledge.counts(ctx.tid)
        report["budget_spent"] = (rt.goals.get_goal(ctx.gid) or {}).get("budget_spent")
        report["jobs"] = rt.jobs.counts()
    except Exception as exc:
        logger.exception("scenario aborted")
        report["checks"].append({"name": "scenario setup", "passed": False, "evidence": f"{type(exc).__name__}: {str(exc)[:400]}"})
        for hid, p in procs.items():
            report["notes"].append(f"{hid} log tail:\n{tail(p.log_path)}")
    finally:
        if rt is not None and rt.worker is not None:
            try:
                await rt.worker.stop(timeout=10)
            except Exception:
                logger.exception("worker stop failed")
        if client is not None:
            try:
                await client.close_all()
            except Exception:
                logger.debug("client close failed", exc_info=True)
        for p in procs.values():
            try:
                await p.stop()
            except Exception:
                logger.exception("holder process stop failed")
        if runner is not None:
            await runner.cleanup()
        if own_rt and rt is not None:
            try:
                await rt.stop()
            except Exception:
                logger.exception("runtime stop failed")
        if own_rt and not keep and not data_dir:
            shutil.rmtree(base, ignore_errors=True)
    report["duration_seconds"] = round(time.monotonic() - t_start, 1)
    report["passed"] = bool(report["checks"]) and all(c["passed"] for c in report["checks"])
    report["table"] = print_table(report)
    if report["notes"]:
        print("\nnotes:\n- " + "\n- ".join(report["notes"]))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mycelic scenario", description="Run the Mycelic verification scenario and print a PASS/FAIL table.")
    parser.add_argument("--data-dir", default=None, help="run inside this directory instead of a temporary one (kept afterwards)")
    parser.add_argument("--timeout", type=float, default=120.0, help="overall time budget in seconds")
    parser.add_argument("--keep", action="store_true", help="keep the temporary data directory for inspection")
    parser.add_argument("--json", default=None, help="also write the report as JSON to this path")
    parser.add_argument("--verbose", action="store_true", help="INFO logs on stderr (default: warnings only)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    report = asyncio.run(run_scenario(data_dir=args.data_dir, timeout=args.timeout, keep=args.keep))
    if args.json:
        Path(args.json).write_text(json.dumps({k: v for k, v in report.items() if k != "table"}, indent=2, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
