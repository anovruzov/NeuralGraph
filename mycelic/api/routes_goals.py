"""Goals and their discovery loops (docs/mycelic/API.md "Goals and loops").

``POST /goals`` activates by default: the point of a goal is the loop that pursues it. When activation is refused
(the goal cannot start in its state) the goal is still returned, with ``loop`` null, so the UI can show why instead
of losing the person's input.
"""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from ..authz import Forbidden
from .middleware import ApiError, json_response, limit_of, listing, opt_dict, query_flag, read_json, require_user

logger = logging.getLogger(__name__)

GOAL_FIELDS = ("title", "objective", "owner_type", "owner_id", "scope_unit_id", "parent_goal_id", "success_criteria", "baseline", "measurement_source",
               "deadline", "priority", "permitted_actions", "budget", "dependencies", "assignees")
GOAL_ACTIONS = ("activate", "pause", "resume", "complete", "archive", "assign", "decompose", "prioritize", "delegate")
LOOP_ACTIONS = ("start", "pause", "resume", "run_now", "stop")


def goal_or_404(rt: Any, p: Any, goal_id: str) -> dict[str, Any]:
    g = rt.goals.get_goal(goal_id)
    if g is None or g["tenant_id"] != p.tenant_id:
        raise KeyError(goal_id)
    return g


def _goal_input(body: dict[str, Any]) -> dict[str, Any]:
    data = {k: body[k] for k in GOAL_FIELDS if k in body}
    for k in ("success_criteria", "permitted_actions", "dependencies", "assignees"):
        if k in data and data[k] is not None and not isinstance(data[k], list):
            raise ApiError(400, f"'{k}' must be a list")
    for k in ("budget", "measurement_source"):
        if k in data and data[k] is not None and not isinstance(data[k], dict):
            raise ApiError(400, f"'{k}' must be an object")
    if "priority" in data and data["priority"] is not None:
        try:
            data["priority"] = int(data["priority"])
        except (TypeError, ValueError):
            raise ApiError(400, "'priority' must be an integer 1..5") from None
    for a in data.get("assignees") or []:
        if not isinstance(a, dict) or a.get("type") not in ("user", "unit") or not isinstance(a.get("id"), str):
            raise ApiError(400, "assignees must be [{type: user|unit, id}]")
    return data


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]

    async def list_goals(request: web.Request) -> web.Response:
        p = require_user(request)
        items = rt.goals.list_goals(p, scope_unit_id=request.query.get("scope_unit_id") or None, status=request.query.get("status") or None,
                                    mine=query_flag(request, "mine"), parent_goal_id=request.query.get("parent_goal_id") or None,
                                    include_archived=query_flag(request, "include_archived"), limit=limit_of(request, 200))
        return json_response(listing(items))

    async def create_goal(request: web.Request) -> web.Response:
        p = require_user(request)
        body = await read_json(request)
        data = _goal_input(body)
        activate = body.get("activate", True)
        if not isinstance(activate, bool):
            raise ApiError(400, "'activate' must be a boolean")
        goal = await rt.goals.create_goal(p, data, activate=False)
        activation_error = None
        if activate:
            try:
                goal = await rt.goals.action(p, goal["goal_id"], "activate")
            except (ValueError, Forbidden) as exc:
                activation_error = str(exc)
                logger.info("goal %s created but not activated: %s", goal["goal_id"], exc)
                goal = rt.goals.goal_view(rt.goals.get_goal(goal["goal_id"]))
        out: dict[str, Any] = {"goal": goal}
        if activation_error:
            out["activation_error"] = activation_error
        return json_response(out, 201)

    async def get_goal(request: web.Request) -> web.Response:
        p = require_user(request)
        g = goal_or_404(rt, p, request.match_info["goal_id"])
        rt.authz.require(rt.goals.can_view(p, g), "goal.view", g["goal_id"])
        gid = g["goal_id"]
        parent = rt.goals.get_goal(g["parent_goal_id"]) if g.get("parent_goal_id") else None
        usage = {"tokens": 0, "usd": 0.0, "calls": 0}
        if rt.ledger is not None:
            try:
                t = rt.ledger.totals(p.tenant_id, goal_id=gid)
                usage = {"tokens": t.get("tokens", 0), "usd": t.get("cost_usd", 0.0), "calls": t.get("calls", 0), "by_tier": t.get("by_tier", {})}
            except Exception:
                logger.exception("ledger totals failed for goal %s", gid)
        return json_response({
            "goal": rt.goals.goal_view(g), "subgoals": rt.goals.list_goals(p, parent_goal_id=gid, include_archived=True),
            "parent": rt.goals.goal_view(parent) if parent and rt.goals.can_view(p, parent) else None, "loop": rt.goals.loop_view(gid),
            "questions": rt.questions.list(p, goal_id=gid, limit=200), "discoveries": rt.knowledge.list_discoveries(p, goal_id=gid, limit=100),
            "claims": rt.knowledge.list_claims(p, goal_id=gid, limit=100), "conflicts": rt.knowledge.list_conflicts(p, goal_id=gid, limit=50),
            "outcomes": rt.goals.outcomes(gid), "usage": usage, "revisions": rt.knowledge.revisions_for("goal", gid),
        })

    async def update_goal(request: web.Request) -> web.Response:
        p = require_user(request)
        g = goal_or_404(rt, p, request.match_info["goal_id"])
        body = await read_json(request)
        if "status" in body:
            raise ApiError(400, "status changes go through /actions")
        fields = _goal_input(body)
        for k in ("owner_type", "owner_id"):
            if k in fields:
                raise ApiError(400, f"'{k}' changes through the delegate action")
        if not fields:
            raise ApiError(400, "nothing to update")
        goal = await rt.goals.update_goal(p, g["goal_id"], fields, reason=body.get("reason") if isinstance(body.get("reason"), str) else "edit")
        return json_response({"goal": goal})

    async def goal_action(request: web.Request) -> web.Response:
        p = require_user(request)
        g = goal_or_404(rt, p, request.match_info["goal_id"])
        body = await read_json(request)
        action = body.get("action")
        if action not in GOAL_ACTIONS:
            raise ApiError(400, f"action must be one of {', '.join(GOAL_ACTIONS)}")
        params = {k: v for k, v in body.items() if k != "action"}
        if action == "decompose":
            subs = params.get("subgoals")
            if not isinstance(subs, list) or not subs:
                raise ApiError(400, "decompose needs 'subgoals'")
            params["subgoals"] = [_goal_input(s) if isinstance(s, dict) else {} for s in subs]
        out = await rt.goals.action(p, g["goal_id"], action, params)
        subgoals = out.pop("subgoals", None)
        resp: dict[str, Any] = {"goal": out}
        if subgoals is not None:
            resp["subgoals"] = subgoals
        return json_response(resp)

    async def add_outcome(request: web.Request) -> web.Response:
        p = require_user(request)
        g = goal_or_404(rt, p, request.match_info["goal_id"])
        body = await read_json(request)
        kind = body.get("kind")
        value = opt_dict(body, "value")
        if not isinstance(kind, str) or value is None:
            raise ApiError(400, "'kind' and 'value' are required")
        claim_ids = body.get("claim_ids") or []
        if not isinstance(claim_ids, list) or any(not isinstance(c, str) for c in claim_ids):
            raise ApiError(400, "'claim_ids' must be a list of strings")
        out = await rt.goals.add_outcome(p, g["goal_id"], kind=kind, value=value, claim_ids=claim_ids)
        return json_response(out, 201)

    async def get_loop(request: web.Request) -> web.Response:
        p = require_user(request)
        g = goal_or_404(rt, p, request.match_info["goal_id"])
        rt.authz.require(rt.goals.can_view(p, g), "loop.view", g["goal_id"])
        return json_response({"loop": rt.goals.loop_view(g["goal_id"])})

    async def loop_action(request: web.Request) -> web.Response:
        p = require_user(request)
        g = goal_or_404(rt, p, request.match_info["goal_id"])
        body = await read_json(request)
        action = body.get("action")
        if action not in LOOP_ACTIONS:
            raise ApiError(400, f"action must be one of {', '.join(LOOP_ACTIONS)}")
        loop = await rt.goals.loop_control(p, g["goal_id"], action)
        if action == "run_now" and rt.worker is not None:
            try:
                rt.worker.wake()
            except Exception:
                pass
        return json_response({"loop": loop})

    app.router.add_get(f"{prefix}/goals", list_goals)
    app.router.add_post(f"{prefix}/goals", create_goal)
    app.router.add_get(f"{prefix}/goals/{{goal_id}}", get_goal)
    app.router.add_patch(f"{prefix}/goals/{{goal_id}}", update_goal)
    app.router.add_post(f"{prefix}/goals/{{goal_id}}/actions", goal_action)
    app.router.add_post(f"{prefix}/goals/{{goal_id}}/outcomes", add_outcome)
    app.router.add_get(f"{prefix}/goals/{{goal_id}}/loop", get_loop)
    app.router.add_post(f"{prefix}/goals/{{goal_id}}/loop", loop_action)


__all__ = ["setup", "goal_or_404"]
