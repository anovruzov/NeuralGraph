"""Aggregated, visibility-filtered reads for the three workspaces (docs/mycelic/API.md "Workspaces").

Everything is assembled from the services with the caller's principal, so a workspace never shows more than the
individual endpoints would. The cross-unit ``synthesis`` (department and above) is one ``aggregate_level`` model
call over what the caller may see; it is cached per (tenant, unit, DB revision) in process memory because the
inputs only change when the coordination DB does, and a heavy-tier call per page view would be wasteful.
"""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from ..authz import Principal
from ..models.base import ModelError
from ..util import now_iso
from .middleware import json_response, require_user
from .routes_org import holder_view, recent_memories, root_unit, scopes_of, unit_or_404, unit_view, embedded_store

logger = logging.getLogger(__name__)

SYNTHESIS_LEVELS = ("department", "subsidiary", "region", "executive")
COMPARISON_LEVELS = ("subsidiary", "region", "executive")
EMPTY_SYNTHESIS = {"summary": "", "recurring_problems": [], "constraints": [], "conflicting_findings": [], "opportunities": [], "escalations": [],
                   "material_uncertainties": [], "decisions_needed": [], "computed_at": None, "method": "none"}


async def synthesize(app: web.Application, p: Principal, unit: dict[str, Any], *, discoveries: list[dict[str, Any]], claims: list[dict[str, Any]],
                     conflicts: list[dict[str, Any]], goals: list[dict[str, Any]]) -> dict[str, Any]:
    rt = app["rt"]
    if rt.router is None:
        return dict(EMPTY_SYNTHESIS)
    cache: dict[str, dict[str, tuple[int, str, dict[str, Any]]]] = app.setdefault("synthesis_cache", {})
    per_tenant = cache.setdefault(p.tenant_id, {})
    # the cache key includes the principal's scope signature: two viewers with different visibility get different inputs
    scope_sig = ",".join(sorted(scopes_of(rt, p))) if not p.is_system else "*"
    hit = per_tenant.get(unit["unit_id"])
    if hit is not None and hit[0] == rt.db.revision and hit[1] == scope_sig:
        return hit[2]
    payload = {
        "level": unit["level"], "unit_name": unit["name"],
        "discoveries": [{"discovery_id": d["discovery_id"], "title": d["title"], "summary": (d.get("summary") or "")[:600], "kind": d.get("kind"), "status": d.get("status"),
                         "level": d.get("level"), "unit_id": d.get("scope_unit_id"), "unit_name": d.get("scope_unit_name")} for d in discoveries[:60]],
        "claims": [{"claim_id": c["claim_id"], "text": c["text"][:400], "status": c.get("status"), "confidence": c.get("confidence"), "unit_id": c.get("scope_unit_id")}
                   for c in claims[:120]],
        "conflicts": [{"conflict_id": k["conflict_id"], "summary": (k.get("summary") or "")[:300], "status": k.get("status")} for k in conflicts[:40]],
        "goals": [{"goal_id": g["goal_id"], "title": g["title"], "status": g.get("status"), "progress": g.get("progress"), "unit_id": g.get("scope_unit_id")} for g in goals[:60]],
    }
    try:
        out = await rt.router.run_task("aggregate_level", payload, tenant_id=p.tenant_id, policy_tiers=rt.org.policy(p.tenant_id, "model_tiers", {}) or {})
        result = {**EMPTY_SYNTHESIS, **{k: v for k, v in out.items() if k in EMPTY_SYNTHESIS}, "computed_at": now_iso(), "method": "model"}
    except (ModelError, ValueError) as exc:
        logger.warning("aggregate_level failed for %s: %s", unit["unit_id"], exc)
        result = {**EMPTY_SYNTHESIS, "computed_at": now_iso(), "method": "none", "error": str(exc)[:300]}
    if len(per_tenant) > 200:
        per_tenant.clear()
    per_tenant[unit["unit_id"]] = (rt.db.revision, scope_sig, result)
    return result


def dependencies_of(rt: Any, goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    titles = {g["goal_id"]: g["title"] for g in goals}
    out = []
    for g in goals:
        for dep in g.get("dependencies") or []:
            target = rt.goals.get_goal(dep)
            out.append({"goal_id": g["goal_id"], "goal_title": g["title"], "depends_on": dep, "depends_on_title": titles.get(dep) or (target["title"] if target else ""),
                        "status": target["status"] if target else "unknown"})
    return out


async def unit_workspace(app: web.Application, p: Principal, unit: dict[str, Any]) -> dict[str, Any]:
    rt = app["rt"]
    uid = unit["unit_id"]
    level = unit["level"]
    goals = rt.goals.list_goals(p, scope_unit_id=uid, limit=200)
    discoveries = rt.knowledge.list_discoveries(p, scope_unit_id=uid, limit=200)
    open_questions = rt.questions.list(p, scope_unit_id=uid, live_only=True, limit=100)
    conflicts = rt.knowledge.list_conflicts(p, scope_unit_id=uid, limit=100)
    claims = rt.knowledge.list_claims(p, scope_unit_id=uid, limit=300)
    children_units = [u for u in rt.org.list_units(p.tenant_id) if u.get("parent_id") == uid]
    children = []
    comparisons = []
    for child in children_units:
        cid = child["unit_id"]
        c_disc = rt.knowledge.list_discoveries(p, scope_unit_id=cid, limit=500)
        c_open_q = rt.questions.list(p, scope_unit_id=cid, live_only=True, limit=500)
        c_goals = rt.goals.list_goals(p, scope_unit_id=cid, limit=500)
        children.append({"unit": unit_view(rt, child), "discoveries": len(c_disc), "open_questions": len(c_open_q), "goals": len(c_goals)})
        if level in COMPARISON_LEVELS:
            comparisons.append({"unit_id": cid, "name": child["name"], "level": child["level"], "discoveries": len(c_disc),
                                "supported_claims": len(rt.knowledge.list_claims(p, scope_unit_id=cid, status="supported", limit=500)),
                                "open_conflicts": len(rt.knowledge.list_conflicts(p, scope_unit_id=cid, status="open", limit=500)),
                                "stale_claims": len(rt.knowledge.list_claims(p, scope_unit_id=cid, status="stale", limit=500))})
    members = None
    if p.is_admin or uid in rt.authz.led_unit_ids(p):
        members = [{"user_id": m["user_id"], "user_name": m.get("user_name"), "email": m.get("email"), "role": m["role"]} for m in rt.org.members_of(uid)]
    synthesis = None
    if level in SYNTHESIS_LEVELS:
        synthesis = await synthesize(app, p, unit, discoveries=discoveries, claims=claims, conflicts=conflicts, goals=goals)
    return {"unit": unit_view(rt, unit), "level": level, "goals": goals, "discoveries": discoveries, "open_questions": open_questions, "conflicts": conflicts,
            "dependencies": dependencies_of(rt, goals), "progress": [{"goal_id": g["goal_id"], "title": g["title"], "progress": g.get("progress")} for g in goals],
            "synthesis": synthesis, "children": children, "members": members, "comparisons": comparisons,
            "counts": {"goals": len(goals), "discoveries": len(discoveries), "open_questions": len(open_questions), "conflicts": len(conflicts), "claims": len(claims)}}


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]

    async def employee(request: web.Request) -> web.Response:
        p = require_user(request)
        goals = rt.goals.list_goals(p, mine=True, limit=100)
        holders = rt.org.holders_for_user(p.id)
        recent: list[dict[str, Any]] = []
        for h in holders:
            store = await embedded_store(rt, h)
            if store is not None:
                recent.extend(await recent_memories(store, h, 10))
        recent.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return json_response({
            "goals": goals, "questions_needing_input": rt.questions.list(p, needs_input=True, limit=50),
            "discoveries": rt.knowledge.list_discoveries(p, limit=50), "holders": [holder_view(rt, h) for h in holders], "recent_memory": recent[:10],
            "shared": [g for g in p.grants if g.get("tenant_id") == p.tenant_id], "loop_states": [g["loop"] for g in goals if g.get("loop")],
            "levels": rt.authz.levels_for(p),
        })

    async def unit(request: web.Request) -> web.Response:
        p = require_user(request)
        u = unit_or_404(rt, p, request.match_info["unit_id"])
        rt.authz.require(u["unit_id"] in scopes_of(rt, p) or p.is_system, "workspace.unit", u["unit_id"], "the unit is not visible to you")
        return json_response(await unit_workspace(app, p, u))

    async def executive(request: web.Request) -> web.Response:
        p = require_user(request)
        root = root_unit(rt, p.tenant_id)
        if root is None:
            raise KeyError("executive root")
        rt.authz.require(root["unit_id"] in scopes_of(rt, p) or p.is_system, "workspace.executive", root["unit_id"], "the executive workspace needs an executive role")
        ws = await unit_workspace(app, p, root)
        strategic = [d for d in rt.knowledge.list_discoveries(p, limit=300) if d.get("level") in ("executive", "region") or d.get("status") == "escalated"]
        uncertain = rt.knowledge.list_claims(p, status="hypothesis", limit=50) + rt.knowledge.list_claims(p, status="stale", limit=50)
        decisions: list[dict[str, Any]] = []
        if ws.get("synthesis"):
            decisions.extend({**d, "kind": d.get("kind") or "synthesis"} for d in ws["synthesis"].get("decisions_needed") or [] if isinstance(d, dict))
        for k in rt.knowledge.list_conflicts(p, status="open", limit=50):
            decisions.append({"text": k.get("summary") or "open disagreement", "conflict_ids": [k["conflict_id"]], "kind": "conflict"})
        ws.update({"strategic": strategic[:100], "material_uncertainties": uncertain, "decisions": decisions})
        return json_response(ws)

    app.router.add_get(f"{prefix}/workspace/employee", employee)
    app.router.add_get(f"{prefix}/workspace/unit/{{unit_id}}", unit)
    app.router.add_get(f"{prefix}/workspace/executive", executive)


__all__ = ["setup", "unit_workspace", "synthesize"]
