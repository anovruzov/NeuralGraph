"""Goals as first-class objects, and the per-goal discovery loop record.

Progress is calculated from recorded outcomes against the goal's success criteria; when no reliable
measurement exists the progress is reported as *unknown* rather than guessed (``progress.known = false``).
Delegation preserves the original owner's rights through a grant, so ownership can move without anybody
losing access they had.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Iterable, Mapping

from ..authz import Authorizer, Forbidden, Principal
from ..db.coord import CoordDB, row_to_dict, rows_to_dicts
from ..jobs import JobQueue
from ..org import OrgService
from ..util import j, new_id, now_iso, parse_iso, plus_seconds, utcnow

logger = logging.getLogger(__name__)

GOAL_STATUSES = ("draft", "active", "paused", "completed", "archived", "blocked")
LOOP_STATES = ("active", "waiting", "paused", "budget_exhausted", "blocked", "failed", "completed", "stopped")
GOAL_JSON = ("success_criteria", "baseline", "measurement_source", "permitted_actions", "budget", "budget_spent", "dependencies", "progress", "assignees")
LOOP_JSON = ("config", "stats")
DEFAULT_PERMITTED_ACTIONS = ["ask_questions", "route_to_holders", "verify", "synthesize", "propose_actions"]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class GoalService:
    def __init__(self, db: CoordDB, org: OrgService, authz: Authorizer, jobs: JobQueue, *, loop_defaults: Mapping[str, Any] | None = None,
                 heartbeat_seconds: float = 10.0) -> None:
        self.db = db
        self.org = org
        self.authz = authz
        self.jobs = jobs
        self.loop_defaults = dict(loop_defaults or {"check_interval_seconds": 300, "max_concurrent_questions": 2, "cooldown_seconds": 900,
                                                    "max_followup_depth": 3, "question_timeout_seconds": 120})
        self.heartbeat_seconds = heartbeat_seconds

    # ------------------------------------------------------------------ reads
    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM goals WHERE goal_id=?", (goal_id,)), json_fields=GOAL_JSON)

    def goal_view(self, goal: Mapping[str, Any], *, with_loop: bool = True) -> dict[str, Any]:
        d = dict(goal)
        if d.get("owner_type") == "user":
            u = self.org.get_user(d["owner_id"]); d["owner_name"] = u["name"] if u else ""
        else:
            un = self.org.get_unit(d["owner_id"]); d["owner_name"] = un["name"] if un else ""
        su = self.org.get_unit(d["scope_unit_id"]) if d.get("scope_unit_id") else None
        d["scope_unit_name"] = su["name"] if su else ""
        d["scope_level"] = su["level"] if su else "employee"
        names = []
        for a in d.get("assignees") or []:
            if a.get("type") == "user":
                u = self.org.get_user(a["id"]); names.append({**a, "name": u["name"] if u else ""})
            else:
                un = self.org.get_unit(a["id"]); names.append({**a, "name": un["name"] if un else ""})
        d["assignees"] = names
        gid = d["goal_id"]
        d["counts"] = {
            "questions": int(self.db.scalar("SELECT COUNT(*) FROM questions WHERE goal_id=?", (gid,), 0)),
            "open_questions": int(self.db.scalar("SELECT COUNT(*) FROM questions WHERE goal_id=? AND status IN ('draft','routed','collecting','evaluating','verifying')", (gid,), 0)),
            "discoveries": int(self.db.scalar("SELECT COUNT(*) FROM discoveries WHERE goal_id=?", (gid,), 0)),
            "claims": int(self.db.scalar("SELECT COUNT(*) FROM claims WHERE goal_id=? AND status<>'retracted'", (gid,), 0)),
            "subgoals": int(self.db.scalar("SELECT COUNT(*) FROM goals WHERE parent_goal_id=? AND status<>'archived'", (gid,), 0)),
        }
        if with_loop:
            d["loop"] = self.loop_view(gid)
        return d

    def list_goals(self, principal: Principal, *, scope_unit_id: str | None = None, status: str | None = None, mine: bool = False,
                   parent_goal_id: str | None = None, include_archived: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM goals WHERE tenant_id=?", [principal.tenant_id]
        if status:
            sql += " AND status=?"; args.append(status)
        elif not include_archived:
            sql += " AND status <> 'archived'"
        if parent_goal_id:
            sql += " AND parent_goal_id=?"; args.append(parent_goal_id)
        if scope_unit_id:
            ids = self.org.descendants(scope_unit_id)
            sql += f" AND (scope_unit_id IN ({','.join('?' * len(ids))}) OR (owner_type='unit' AND owner_id IN ({','.join('?' * len(ids))})))"; args.extend(ids); args.extend(ids)
        sql += " ORDER BY priority, deadline IS NULL, deadline, created_at DESC LIMIT ?"; args.append(int(limit))
        rows = rows_to_dicts(self.db.all(sql, args), json_fields=GOAL_JSON)
        out = []
        for g in rows:
            if mine and not self._is_mine(principal, g):
                continue
            if self.authz.can_view_scoped(principal, g, resource_type="goal") or self._is_mine(principal, g):
                out.append(self.goal_view(g))
        return out

    def _is_mine(self, p: Principal, g: Mapping[str, Any]) -> bool:
        if g.get("owner_type") == "user" and g.get("owner_id") == p.id:
            return True
        if g.get("created_by") == p.id:
            return True
        for a in g.get("assignees") or []:
            if a.get("type") == "user" and a.get("id") == p.id:
                return True
            if a.get("type") == "unit" and a.get("id") in p.member_unit_ids:
                return True
        return False

    def can_view(self, principal: Principal, goal: Mapping[str, Any]) -> bool:
        return self.authz.can_view_scoped(principal, goal, resource_type="goal") or self._is_mine(principal, goal)

    # ------------------------------------------------------------------ create / update
    async def create_goal(self, principal: Principal, data: Mapping[str, Any], *, activate: bool = False, is_demo: bool | None = None) -> dict[str, Any]:
        tenant_id = principal.tenant_id
        owner_type = data.get("owner_type") or "user"
        owner_id = data.get("owner_id") or principal.id
        scope = data.get("scope_unit_id")
        if owner_type == "unit" and not scope:
            scope = owner_id
        if not principal.is_system:
            self.authz.require(self.authz.can_create_goal(principal, owner_type=owner_type, owner_id=owner_id, scope_unit_id=scope), "goal.create", "", "cannot create a goal for that owner or scope")
        parent = data.get("parent_goal_id")
        if parent:
            pg = self.get_goal(parent)
            if pg is None or pg["tenant_id"] != tenant_id:
                raise ValueError("parent goal not found")
        title = (data.get("title") or "").strip()
        objective = (data.get("objective") or "").strip()
        if not title or not objective:
            raise ValueError("title and objective are required")
        budget = dict(self.org.policy(tenant_id, "default_goal_budget", {}) or {})
        budget.update({k: v for k, v in (data.get("budget") or {}).items() if v is not None})
        gid = new_id("goal")
        now = now_iso()
        prio = int(data.get("priority") or 3)
        async with self.db.tx() as c:
            c.execute(
                """INSERT INTO goals(goal_id, tenant_id, owner_type, owner_id, scope_unit_id, parent_goal_id, title, objective, success_criteria, baseline, measurement_source,
                                     deadline, priority, status, permitted_actions, budget, budget_spent, dependencies, progress, assignees, is_demo, version, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (gid, tenant_id, owner_type, owner_id, scope, parent, title, objective, j(list(data.get("success_criteria") or [])),
                 j(data["baseline"]) if data.get("baseline") is not None else None, j(data.get("measurement_source") or {}), data.get("deadline"),
                 max(1, min(5, prio)), j(list(data.get("permitted_actions") or DEFAULT_PERMITTED_ACTIONS)), j(budget), j({"tokens": 0, "usd": 0.0, "questions": 0}),
                 j(list(data.get("dependencies") or [])), j({"known": False, "note": "no measurement recorded yet"}), j(list(data.get("assignees") or [])),
                 int(principal.is_demo if is_demo is None else is_demo), principal.id, now, now),
            )
            c.execute("INSERT INTO revisions(revision_id, tenant_id, object_type, object_id, version, actor_type, actor_id, reason, before, after, at) VALUES (?, ?, 'goal', ?, 1, ?, ?, 'created', '{}', ?, ?)",
                      (new_id("rev"), tenant_id, gid, principal.kind, principal.id, j({"title": title}), now))
            self.db.audit_sync(c, tenant_id, principal.kind, principal.id, "goal.create", resource_type="goal", resource_id=gid, detail={"title": title, "scope_unit_id": scope})
            self.db.emit_sync(c, tenant_id, "goal.updated", ref_type="goal", ref_id=gid, payload={"action": "create", "title": title, "scope_unit_id": scope},
                              audience=self._audience(owner_type, owner_id, scope, data.get("assignees") or []))
            for a in data.get("assignees") or []:
                if a.get("type") == "user" and a.get("id") != principal.id:
                    self.org.notify_sync(c, tenant_id, a["id"], "goal", f"You were assigned the goal “{title}”", ref_type="goal", ref_id=gid)
        if activate:
            await self.action(principal, gid, "activate")
        return self.goal_view(self.get_goal(gid))  # type: ignore[arg-type]

    def _audience(self, owner_type: str, owner_id: str, scope: str | None, assignees: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        users = [owner_id] if owner_type == "user" else []
        units = [scope] if scope else []
        if owner_type == "unit":
            units.append(owner_id)
        for a in assignees:
            (users if a.get("type") == "user" else units).append(a["id"])
        return {"user_ids": list(dict.fromkeys(users)), "unit_ids": list(dict.fromkeys(units)), "visibility": "unit"}

    async def update_goal(self, principal: Principal, goal_id: str, fields: Mapping[str, Any], *, reason: str = "edit") -> dict[str, Any]:
        g = self.get_goal(goal_id)
        if g is None:
            raise KeyError(goal_id)
        self.authz.require(self.authz.can_manage_goal(principal, g), "goal.update", goal_id)
        allowed = {"title", "objective", "success_criteria", "baseline", "measurement_source", "deadline", "priority", "permitted_actions", "budget", "dependencies", "assignees", "scope_unit_id", "parent_goal_id"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        sets, args, before, after = ["version=version+1", "updated_at=?"], [now_iso()], {}, {}
        for k, v in fields.items():
            before[k] = g.get(k); after[k] = v
            if k in ("success_criteria", "measurement_source", "permitted_actions", "budget", "dependencies", "assignees"):
                sets.append(f"{k}=?"); args.append(j(v))
            elif k == "baseline":
                sets.append("baseline=?"); args.append(j(v) if v is not None else None)
            elif k == "priority":
                sets.append("priority=?"); args.append(max(1, min(5, int(v))))
            else:
                sets.append(f"{k}=?"); args.append(v)
        args.append(goal_id)
        async with self.db.tx() as c:
            c.execute(f"UPDATE goals SET {', '.join(sets)} WHERE goal_id=?", args)
            c.execute("INSERT INTO revisions(revision_id, tenant_id, object_type, object_id, version, actor_type, actor_id, reason, before, after, at) VALUES (?, ?, 'goal', ?, ?, ?, ?, ?, ?, ?, ?)",
                      (new_id("rev"), g["tenant_id"], goal_id, int(g["version"]) + 1, principal.kind, principal.id, reason, j(before), j(after), now_iso()))
            self.db.audit_sync(c, g["tenant_id"], principal.kind, principal.id, "goal.update", resource_type="goal", resource_id=goal_id, detail={"fields": sorted(fields)})
            self.db.emit_sync(c, g["tenant_id"], "goal.updated", ref_type="goal", ref_id=goal_id, payload={"action": "update"}, audience=self._audience(g["owner_type"], g["owner_id"], g.get("scope_unit_id"), g.get("assignees") or []))
        if "success_criteria" in fields or "baseline" in fields:
            await self.recompute_progress(goal_id)
        return self.goal_view(self.get_goal(goal_id))  # type: ignore[arg-type]

    async def action(self, principal: Principal, goal_id: str, action: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        g = self.get_goal(goal_id)
        if g is None:
            raise KeyError(goal_id)
        self.authz.require(self.authz.can_manage_goal(principal, g), f"goal.{action}", goal_id)
        params = dict(params or {})
        now = now_iso()
        transitions = {"activate": ("draft", "paused", "blocked", "active"), "pause": ("active",), "resume": ("paused", "blocked"), "complete": ("active", "paused", "blocked"),
                       "archive": ("draft", "active", "paused", "completed", "blocked")}
        new_status = {"activate": "active", "pause": "paused", "resume": "active", "complete": "completed", "archive": "archived"}.get(action)
        extra: dict[str, Any] = {}
        async with self.db.tx() as c:
            if new_status:
                if g["status"] not in transitions[action]:
                    raise ValueError(f"cannot {action} a goal in status {g['status']}")
                c.execute("UPDATE goals SET status=?, version=version+1, updated_at=?, completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END, archived_at=CASE WHEN ?='archived' THEN ? ELSE archived_at END WHERE goal_id=?",
                          (new_status, now, new_status, now, new_status, now, goal_id))
                self._loop_desired_sync(c, g, {"activate": "active", "resume": "active", "pause": "paused", "complete": "stopped", "archive": "stopped"}[action], principal)
            elif action == "assign":
                assignees = list(params.get("assignees") or [])
                c.execute("UPDATE goals SET assignees=?, version=version+1, updated_at=? WHERE goal_id=?", (j(assignees), now, goal_id))
                for a in assignees:
                    if a.get("type") == "user" and a.get("id") != principal.id:
                        self.org.notify_sync(c, g["tenant_id"], a["id"], "goal", f"You were assigned the goal “{g['title']}”", ref_type="goal", ref_id=goal_id)
            elif action == "prioritize":
                c.execute("UPDATE goals SET priority=?, version=version+1, updated_at=? WHERE goal_id=?", (max(1, min(5, int(params.get("priority", g["priority"])))), now, goal_id))
            elif action == "delegate":
                nt, ni = params.get("owner_type"), params.get("owner_id")
                if nt not in ("user", "unit") or not ni:
                    raise ValueError("delegate needs owner_type and owner_id")
                c.execute("UPDATE goals SET owner_type=?, owner_id=?, version=version+1, updated_at=? WHERE goal_id=?", (nt, ni, now, goal_id))
                # the delegating owner keeps management rights through an explicit grant (permissions preserved)
                if g["owner_type"] == "user":
                    c.execute("INSERT INTO grants(grant_id, tenant_id, grantor_id, grantee_type, grantee_id, resource_type, resource_id, level, reason, status, created_at) VALUES (?, ?, ?, 'user', ?, 'goal', ?, 'artifact', 'delegated goal: original owner keeps access', 'active', ?)",
                              (new_id("grant"), g["tenant_id"], principal.id, g["owner_id"], goal_id, now))
                if nt == "user" and ni != principal.id:
                    self.org.notify_sync(c, g["tenant_id"], ni, "goal", f"The goal “{g['title']}” was delegated to you", ref_type="goal", ref_id=goal_id)
            elif action == "decompose":
                extra["subgoals"] = []
            else:
                raise ValueError(f"unknown action {action}")
            c.execute("INSERT INTO revisions(revision_id, tenant_id, object_type, object_id, version, actor_type, actor_id, reason, before, after, at) VALUES (?, ?, 'goal', ?, ?, ?, ?, ?, ?, ?, ?)",
                      (new_id("rev"), g["tenant_id"], goal_id, int(g["version"]) + 1, principal.kind, principal.id, action, j({"status": g["status"]}), j({"status": new_status or g["status"], **params}), now))
            self.db.audit_sync(c, g["tenant_id"], principal.kind, principal.id, f"goal.{action}", resource_type="goal", resource_id=goal_id, detail=params)
            self.db.emit_sync(c, g["tenant_id"], "goal.updated", ref_type="goal", ref_id=goal_id, payload={"action": action, "status": new_status or g["status"]},
                              audience=self._audience(g["owner_type"], g["owner_id"], g.get("scope_unit_id"), g.get("assignees") or []))
        if action == "decompose":
            for sg in params.get("subgoals") or []:
                sub = dict(sg)
                sub["parent_goal_id"] = goal_id
                sub.setdefault("scope_unit_id", g.get("scope_unit_id"))
                sub.setdefault("owner_type", g["owner_type"]); sub.setdefault("owner_id", g["owner_id"])
                sub.setdefault("priority", g["priority"])
                extra["subgoals"].append(await self.create_goal(principal, sub, activate=bool(sg.get("activate", False)), is_demo=bool(g.get("is_demo"))))
        if action in ("activate", "resume"):
            await self.enqueue_tick(goal_id, reason=action, priority=2)
        if action in ("complete", "archive", "pause"):
            await self.jobs.cancel(ref_type="goal", ref_id=goal_id)
        out = self.goal_view(self.get_goal(goal_id))  # type: ignore[arg-type]
        out.update(extra)
        return out

    # ------------------------------------------------------------------ outcomes & progress
    async def add_outcome(self, principal: Principal, goal_id: str, *, kind: str, value: Mapping[str, Any], claim_ids: Iterable[str] = ()) -> dict[str, Any]:
        g = self.get_goal(goal_id)
        if g is None:
            raise KeyError(goal_id)
        if not principal.is_system and principal.kind != "loop":
            self.authz.require(self.authz.can_manage_goal(principal, g) or self._is_mine(principal, g), "goal.outcome", goal_id)
        if kind not in ("measurement", "milestone", "action", "note"):
            raise ValueError("unknown outcome kind")
        oid = new_id("out")
        async with self.db.tx() as c:
            c.execute("INSERT INTO goal_outcomes(outcome_id, tenant_id, goal_id, kind, value, claim_ids, recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (oid, g["tenant_id"], goal_id, kind, j(dict(value)), j(list(claim_ids)), principal.id, now_iso()))
            self.db.audit_sync(c, g["tenant_id"], principal.kind, principal.id, "goal.outcome", resource_type="goal", resource_id=goal_id, detail={"kind": kind, "value": dict(value)})
        progress = await self.recompute_progress(goal_id)
        return {"outcome": row_to_dict(self.db.one("SELECT * FROM goal_outcomes WHERE outcome_id=?", (oid,)), json_fields=("value", "claim_ids")), "progress": progress}

    def outcomes(self, goal_id: str) -> list[dict[str, Any]]:
        return rows_to_dicts(self.db.all("SELECT * FROM goal_outcomes WHERE goal_id=? ORDER BY recorded_at", (goal_id,)), json_fields=("value", "claim_ids"))

    def compute_progress(self, goal: Mapping[str, Any]) -> dict[str, Any]:
        """Progress from measurements against success criteria; unknown when no reliable measurement exists."""
        criteria = list(goal.get("success_criteria") or [])
        outs = self.outcomes(goal["goal_id"])
        measurements: dict[str, list[dict[str, Any]]] = {}
        milestones: set[str] = set()
        for o in outs:
            v = o.get("value") or {}
            if o["kind"] == "measurement" and v.get("metric") is not None and isinstance(v.get("value"), (int, float)):
                measurements.setdefault(str(v["metric"]), []).append(o)
            elif o["kind"] == "milestone" and v.get("metric"):
                milestones.add(str(v["metric"]))
        baseline = goal.get("baseline") if isinstance(goal.get("baseline"), dict) else {}
        per: list[dict[str, Any]] = []
        for i, crit in enumerate(criteria):
            metric = str(crit.get("metric") or f"criterion_{i}")
            target = crit.get("target")
            direction = crit.get("direction") or ("decrease" if isinstance(target, (int, float)) else "reach")
            entry: dict[str, Any] = {"metric": metric, "target": target, "direction": direction, "known": False}
            if metric in milestones:
                entry.update(known=True, value=1.0, method="milestone")
            elif metric in measurements and isinstance(target, (int, float)):
                latest = sorted(measurements[metric], key=lambda o: (o["value"].get("at") or o["recorded_at"]))[-1]
                cur = float(latest["value"]["value"])
                base = crit.get("baseline", baseline.get(metric) if baseline else None)
                entry["current"] = cur
                if isinstance(base, (int, float)) and float(base) != float(target):
                    if direction == "decrease":
                        p = (float(base) - cur) / (float(base) - float(target))
                    else:
                        p = (cur - float(base)) / (float(target) - float(base))
                    entry.update(known=True, value=round(_clamp(p), 3), method="measurement_vs_baseline", baseline=base)
                else:
                    met = cur <= float(target) if direction == "decrease" else cur >= float(target)
                    entry.update(known=True, value=1.0 if met else 0.0, method="measurement_vs_target_no_baseline", note="no baseline: binary")
            per.append(entry)
        known = [e for e in per if e["known"]]
        # subgoal roll-up when the goal itself has no measured criteria
        subs = rows_to_dicts(self.db.all("SELECT goal_id, progress, status FROM goals WHERE parent_goal_id=? AND status<>'archived'", (goal["goal_id"],)), json_fields=("progress",))
        sub_known = [s for s in subs if (s.get("progress") or {}).get("known")]
        now = now_iso()
        if known:
            value = sum(float(e["value"]) for e in known) / len(known)
            return {"known": True, "value": round(value, 3), "method": "success_criteria", "criteria": per, "measured": len(known), "total": len(per),
                    "note": f"{len(known)} of {len(per)} criteria measured", "computed_at": now}
        if sub_known:
            value = sum(float(s["progress"]["value"]) for s in sub_known) / len(subs)
            return {"known": True, "value": round(value, 3), "method": "subgoals", "criteria": per, "measured": len(sub_known), "total": len(subs),
                    "note": f"{len(sub_known)} of {len(subs)} subgoals report progress", "computed_at": now}
        return {"known": False, "method": "none", "criteria": per, "measured": 0, "total": len(per), "note": "no reliable measurement recorded", "computed_at": now}

    async def recompute_progress(self, goal_id: str) -> dict[str, Any]:
        g = self.get_goal(goal_id)
        if g is None:
            raise KeyError(goal_id)
        progress = self.compute_progress(g)
        async with self.db.tx() as c:
            c.execute("UPDATE goals SET progress=?, updated_at=? WHERE goal_id=?", (j(progress), now_iso(), goal_id))
            self.db.emit_sync(c, g["tenant_id"], "goal.updated", ref_type="goal", ref_id=goal_id, payload={"action": "progress", "progress": progress},
                              audience=self._audience(g["owner_type"], g["owner_id"], g.get("scope_unit_id"), g.get("assignees") or []))
        if g.get("parent_goal_id"):
            await self.recompute_progress(g["parent_goal_id"])
        return progress

    # ------------------------------------------------------------------ loop record
    def get_loop(self, goal_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM goal_loops WHERE goal_id=?", (goal_id,)), json_fields=LOOP_JSON)

    def _loop_desired_sync(self, c: sqlite3.Connection, goal: Mapping[str, Any], desired: str, principal: Principal, *, explanation: str | None = None) -> None:
        now = now_iso()
        state = {"active": "waiting", "paused": "paused", "stopped": "stopped"}[desired]
        expl = explanation or {"active": "activated; waiting for the worker to pick up the first tick", "paused": "paused by a person", "stopped": "stopped"}[desired]
        cfg = j({**self.loop_defaults})
        c.execute("""INSERT INTO goal_loops(goal_id, tenant_id, desired, state, explanation, config, next_check_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(goal_id) DO UPDATE SET desired=excluded.desired, state=excluded.state, explanation=excluded.explanation, next_check_at=excluded.next_check_at, updated_at=excluded.updated_at""",
                  (goal["goal_id"], goal["tenant_id"], desired, state, expl, cfg, now if desired == "active" else None, now))
        self.db.emit_sync(c, goal["tenant_id"], "loop.state", ref_type="goal", ref_id=goal["goal_id"], payload={"desired": desired, "state": state, "explanation": expl},
                          audience=self._audience(goal["owner_type"], goal["owner_id"], goal.get("scope_unit_id"), goal.get("assignees") or []))
        self.db.audit_sync(c, goal["tenant_id"], principal.kind, principal.id, f"loop.{desired}", resource_type="goal", resource_id=goal["goal_id"])

    async def loop_control(self, principal: Principal, goal_id: str, action: str) -> dict[str, Any]:
        g = self.get_goal(goal_id)
        if g is None:
            raise KeyError(goal_id)
        self.authz.require(self.authz.can_manage_goal(principal, g), f"loop.{action}", goal_id)
        if action not in ("start", "pause", "resume", "run_now", "stop"):
            raise ValueError("unknown loop action")
        if action in ("start", "resume", "run_now") and g["status"] not in ("active",):
            if g["status"] in ("draft", "paused", "blocked"):
                await self.action(principal, goal_id, "activate" if g["status"] == "draft" else "resume")
            else:
                raise ValueError(f"goal is {g['status']}; activate it first")
            g = self.get_goal(goal_id)  # type: ignore[assignment]
        async with self.db.tx() as c:
            if action in ("start", "resume"):
                self._loop_desired_sync(c, g, "active", principal)
            elif action == "pause":
                self._loop_desired_sync(c, g, "paused", principal)
            elif action == "stop":
                self._loop_desired_sync(c, g, "stopped", principal)
            elif action == "run_now":
                self._loop_desired_sync(c, g, "active", principal, explanation="run requested by a person; tick queued")
        if action in ("start", "resume", "run_now"):
            await self.enqueue_tick(goal_id, reason=action, priority=1 if action == "run_now" else 2, force=action == "run_now")
        else:
            await self.jobs.cancel(ref_type="goal", ref_id=goal_id, kinds=["loop.tick"])
        return self.loop_view(goal_id)

    async def enqueue_tick(self, goal_id: str, *, reason: str, priority: int = 5, delay_seconds: float = 0.0, force: bool = False) -> int | None:
        """One runnable tick per goal at a time (idempotency key on goal + reason bucket). ``force`` uses a unique key."""
        g = self.get_goal(goal_id)
        if g is None:
            return None
        key = f"loop.tick:{goal_id}:{new_id('t') if force else 'next'}"
        async with self.db.tx() as c:
            if not force:
                r = c.execute("SELECT job_id FROM jobs WHERE ref_type='goal' AND ref_id=? AND kind='loop.tick' AND status IN ('queued','leased')", (goal_id,)).fetchone()
                if r is not None:
                    if delay_seconds <= 0:
                        c.execute("UPDATE jobs SET available_at=?, priority=MIN(priority, ?) WHERE job_id=? AND status='queued'", (now_iso(), int(priority), r["job_id"]))
                    return int(r["job_id"])
                key = f"loop.tick:{goal_id}:{new_id('t')}"
            return self.jobs.enqueue_sync(c, "loop.tick", idempotency_key=key, tenant_id=g["tenant_id"], ref_type="goal", ref_id=goal_id,
                                          payload={"reason": reason}, priority=priority, delay_seconds=delay_seconds, max_attempts=3)

    async def set_loop_state(self, goal_id: str, state: str, explanation: str, *, worker_id: str | None = None, next_check_at: str | None = None,
                             stats_delta: Mapping[str, float] | None = None, ran: bool = False) -> None:
        if state not in LOOP_STATES:
            raise ValueError(state)
        now = now_iso()
        loop = self.get_loop(goal_id)
        if loop is None:
            return
        stats = dict(loop.get("stats") or {})
        for k, v in (stats_delta or {}).items():
            stats[k] = stats.get(k, 0) + v
        g = self.get_goal(goal_id)
        async with self.db.tx() as c:
            c.execute("UPDATE goal_loops SET state=?, explanation=?, last_worker_id=COALESCE(?, last_worker_id), last_heartbeat_at=CASE WHEN ? IS NULL THEN last_heartbeat_at ELSE ? END, "
                      "next_check_at=?, last_run_at=CASE WHEN ? THEN ? ELSE last_run_at END, run_count=run_count + ?, stats=?, updated_at=? WHERE goal_id=?",
                      (state, explanation[:500], worker_id, worker_id, now, next_check_at, int(ran), now, int(ran), j(stats), now, goal_id))
            if g:
                self.db.emit_sync(c, g["tenant_id"], "loop.state", ref_type="goal", ref_id=goal_id, payload={"state": state, "explanation": explanation, "next_check_at": next_check_at},
                                  audience=self._audience(g["owner_type"], g["owner_id"], g.get("scope_unit_id"), g.get("assignees") or []))

    async def loop_heartbeat(self, goal_id: str, worker_id: str) -> None:
        async with self.db.tx() as c:
            c.execute("UPDATE goal_loops SET last_heartbeat_at=?, last_worker_id=? WHERE goal_id=?", (now_iso(), worker_id, goal_id))

    def loop_view(self, goal_id: str) -> dict[str, Any] | None:
        loop = self.get_loop(goal_id)
        if loop is None:
            return None
        g = self.get_goal(goal_id) or {}
        hb = parse_iso(loop.get("last_heartbeat_at"))
        worker_hb = None
        if loop.get("last_worker_id"):
            w = self.db.one("SELECT last_heartbeat_at FROM workers WHERE worker_id=?", (loop["last_worker_id"],))
            worker_hb = parse_iso(w["last_heartbeat_at"]) if w else None
        newest = max([t for t in (hb, worker_hb) if t is not None], default=None)
        age = (utcnow() - newest).total_seconds() if newest else None
        active = loop["state"] == "active" and age is not None and age <= 3 * self.heartbeat_seconds
        budget, spent = g.get("budget") or {}, g.get("budget_spent") or {}
        remaining = {k: (budget.get(k) - spent.get(k, 0)) if isinstance(budget.get(k), (int, float)) else None for k in ("tokens", "usd", "questions")}
        d = dict(loop)
        d["active_indicator"] = {"active": active, "heartbeat_age_seconds": round(age, 1) if age is not None else None, "worker_id": loop.get("last_worker_id")}
        d["budget_remaining"] = remaining
        return d

    async def charge_budget(self, goal_id: str, *, tokens: int = 0, usd: float = 0.0, questions: int = 0) -> dict[str, Any]:
        """Add spend to the goal; returns {exhausted: bool, budget, spent, remaining}."""
        g = self.get_goal(goal_id)
        if g is None:
            raise KeyError(goal_id)
        spent = dict(g.get("budget_spent") or {})
        spent["tokens"] = int(spent.get("tokens", 0)) + int(tokens)
        spent["usd"] = round(float(spent.get("usd", 0.0)) + float(usd), 6)
        spent["questions"] = int(spent.get("questions", 0)) + int(questions)
        async with self.db.tx() as c:
            c.execute("UPDATE goals SET budget_spent=?, updated_at=? WHERE goal_id=?", (j(spent), now_iso(), goal_id))
        return self.budget_status(goal_id)

    async def charge_usage(self, goal_id: str) -> dict[str, Any]:
        """Charge model usage recorded for this goal since the last charge (cursor on ``model_usage.id``: exact and idempotent,
        whatever the clock resolution or how many jobs ran in the same second)."""
        g = self.get_goal(goal_id)
        if g is None:
            raise KeyError(goal_id)
        spent = dict(g.get("budget_spent") or {})
        cursor = int(spent.get("usage_cursor", 0))
        r = self.db.one("SELECT COALESCE(MAX(id), 0) AS m, COALESCE(SUM(input_tokens + output_tokens), 0) AS t, COALESCE(SUM(cost_usd), 0) AS c "
                        "FROM model_usage WHERE goal_id=? AND id > ?", (goal_id, cursor))
        if r is not None and int(r["m"]) > cursor:
            spent["tokens"] = int(spent.get("tokens", 0)) + int(r["t"])
            spent["usd"] = round(float(spent.get("usd", 0.0)) + float(r["c"]), 6)
            spent["usage_cursor"] = int(r["m"])
            async with self.db.tx() as c:
                c.execute("UPDATE goals SET budget_spent=?, updated_at=? WHERE goal_id=?", (j(spent), now_iso(), goal_id))
        return self.budget_status(goal_id)

    def budget_status(self, goal_id: str) -> dict[str, Any]:
        g = self.get_goal(goal_id) or {}
        budget, spent = g.get("budget") or {}, g.get("budget_spent") or {}
        exhausted = []
        remaining = {}
        for k in ("tokens", "usd", "questions"):
            lim = budget.get(k)
            if isinstance(lim, (int, float)):
                rem = lim - spent.get(k, 0)
                remaining[k] = rem
                if rem <= 0:
                    exhausted.append(k)
        return {"exhausted": bool(exhausted), "exhausted_on": exhausted, "budget": budget, "spent": spent, "remaining": remaining}

    def loops_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        return [v for v in (self.loop_view(r["goal_id"]) for r in self.db.all("SELECT goal_id FROM goal_loops WHERE tenant_id=?", (tenant_id,))) if v]

    def due_ticks(self, *, now: str | None = None) -> list[str]:
        """Goals whose loop is active and whose scheduled check is due (the worker enqueues ticks for them)."""
        now = now or now_iso()
        return [r["goal_id"] for r in self.db.all("SELECT l.goal_id FROM goal_loops l JOIN goals g ON g.goal_id=l.goal_id WHERE l.desired='active' AND g.status='active' "
                                                  "AND l.state IN ('active','waiting') AND l.next_check_at IS NOT NULL AND l.next_check_at <= ?", (now,))]
