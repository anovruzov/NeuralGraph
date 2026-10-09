"""Task issuer: one goal per task, one question per question task, through the public API only (PLAN_v1 §A stage 10, §D WP2).

Endpoints used: ``POST /api/goals`` (activated), ``POST /api/questions``, ``POST /api/goals/{id}/loop`` (run_now, for goal-only
tasks), and the asker's own reads (``GET /api/questions/{id}``, ``/goals/{id}``, ``/claims/{id}``, ``/discoveries/{id}``,
``/evidence/{ref}`` and ``/evidence/{ref}/raw`` status). It never calls ``/respond`` and never touches a service object.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .feed import ApiClient

RESOLVED = ("committed", "retained_uncertain", "investigated", "failed", "expired", "cancelled")


@dataclass
class Issued:
    task_id: str
    goal_id: str | None = None
    question_id: str | None = None
    issued_at: float = 0.0
    error: str | None = None
    activation_error: str | None = None


async def issue_task(client: ApiClient, task: dict[str, Any], *, token: str, scope_unit_id: str, now: datetime | None = None) -> Issued:
    """``task`` is a TaskPublic dict. Wording is distinct per task, and each task has its own goal, so no question is a duplicate."""
    out = Issued(task["task_id"], issued_at=time.time())
    goal = {"title": task["goal_title"], "objective": task["goal_objective"], "scope_unit_id": scope_unit_id,
            "measurement_source": {"domains": list(task.get("goal_domains") or [])},
            "budget": {"questions": 60, "followup_depth": 2, "tokens": 5_000_000, "usd": 100}}
    st, body = await client.call("POST", "/api/goals", token, goal)
    if st != 201:
        out.error = f"goal: HTTP {st} {str(body)[:200]}"
        return out
    out.goal_id = body["goal"]["goal_id"]
    out.activation_error = body.get("activation_error")
    if task.get("question_text"):
        q: dict[str, Any] = {"text": task["question_text"], "goal_id": out.goal_id, "scope_unit_id": scope_unit_id, "kind": "gap",
                             "candidate_domains": list(task.get("candidate_domains") or []), "policy": dict(task.get("policy") or {})}
        if task.get("valid_from_days"):
            q["valid_from"] = ((now or datetime.now(timezone.utc)) - timedelta(days=int(task["valid_from_days"]))).replace(microsecond=0).isoformat()
        st, body = await client.call("POST", "/api/questions", token, q)
        if st != 201:
            out.error = f"question: HTTP {st} {str(body)[:200]}"
            return out
        out.question_id = body["question"]["question_id"]
    return out


async def run_loop_now(client: ApiClient, goal_id: str, token: str) -> int:
    st, _ = await client.call("POST", f"/api/goals/{goal_id}/loop", token, {"action": "run_now"})
    return st


async def question_status(client: ApiClient, qid: str, token: str) -> str:
    st, body = await client.call("GET", f"/api/questions/{qid}", token)
    return str(((body or {}).get("question") or {}).get("status") or f"http{st}") if st == 200 else f"http{st}"


def _secs(a: str | None, b: str | None) -> float | None:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() if a and b else None  # type: ignore[arg-type]
    except Exception:
        return None


async def collect_view(client: ApiClient, task: dict[str, Any], issued: Issued, token: str, *, timed_out: bool = False) -> dict[str, Any]:
    """The asker's own view of the outcome (GET results only), in the shape bench/score.py reads."""
    view: dict[str, Any] = {"task_id": task["task_id"], "status": "ok", "error": issued.error, "latency_s": None, "question": None, "claims": [],
                            "discoveries": [], "evidence": [], "raw_checks": {}, "goal": None, "ids": {"goal_id": issued.goal_id, "question_id": issued.question_id}}
    if issued.error:
        view["status"] = "error"
        return view
    claim_ids: list[str] = []
    disc_ids: list[str] = []
    if issued.question_id:
        st, det = await client.call("GET", f"/api/questions/{issued.question_id}", token)
        if st != 200:
            view.update(status="error", error=f"GET question: HTTP {st}")
            return view
        q = det["question"]
        view["question"] = {"question_id": q["question_id"], "status": q["status"], "result": q.get("result") or {}, "created_at": q.get("created_at"),
                            "resolved_at": q.get("resolved_at"), "routes": [r.get("holder_id") for r in q.get("routes") or []],
                            "followups": [{"question_id": f.get("question_id"), "kind": f.get("kind"), "status": f.get("status")} for f in det.get("followups") or []]}
        view["latency_s"] = _secs(q.get("created_at"), q.get("resolved_at"))
        claim_ids += [c["claim_id"] for c in det.get("claims") or []]
        disc_ids += [d["discovery_id"] for d in det.get("discoveries") or []]
        if q["status"] not in RESOLVED:
            view["status"] = "timeout"
            view["error"] = f"question still {q['status']}"
    if issued.goal_id:
        st, g = await client.call("GET", f"/api/goals/{issued.goal_id}", token)
        if st == 200:
            view["goal"] = {"goal": {k: (g.get("goal") or {}).get(k) for k in ("goal_id", "status", "title")}, "loop": (g.get("loop") or {}).get("state") if isinstance(g.get("loop"), dict) else None,
                            "n_questions": len(g.get("questions") or []), "question_statuses": [x.get("status") for x in g.get("questions") or []]}
            if not issued.question_id:
                claim_ids += [c["claim_id"] for c in g.get("claims") or []]
                disc_ids += [d["discovery_id"] for d in g.get("discoveries") or []]
    seen_refs: dict[str, dict[str, Any]] = {}
    for cid in dict.fromkeys(claim_ids):
        st, c = await client.call("GET", f"/api/claims/{cid}", token)
        if st == 200:
            view["claims"].append(c)
            for r in c.get("evidence") or []:
                if isinstance(r, dict) and r.get("ref_id"):
                    seen_refs.setdefault(r["ref_id"], r)
    for did in dict.fromkeys(disc_ids):
        st, d = await client.call("GET", f"/api/discoveries/{did}", token)
        if st == 200:
            view["discoveries"].append(d)
            for r in d.get("evidence") or []:
                if isinstance(r, dict) and r.get("ref_id"):
                    seen_refs.setdefault(r["ref_id"], r)
    view["evidence"] = list(seen_refs.values())
    for rid in list(seen_refs)[:40]:
        st, _ = await client.call("GET", f"/api/evidence/{rid}/raw", token)
        view["raw_checks"][rid] = st
    if view["status"] == "ok" and timed_out:
        view["status"] = "timeout"
    return view
