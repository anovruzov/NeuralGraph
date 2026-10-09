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
    if task.get("goal_only") and task.get("policy"):
        goal["policy"] = dict(task["policy"])            # goal-level question rules (min_independent_units): the loop's questions inherit them
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


def _empty_view(task: dict[str, Any], issued: Issued, tenant_id: str) -> dict[str, Any]:
    return {"task_id": task["task_id"], "tenant_id": tenant_id, "status": "ok", "error": issued.error, "latency_s": None, "question": None, "claims": [],
            "discoveries": [], "evidence": [], "raw_checks": {}, "goal": None, "ids": {"goal_id": issued.goal_id, "question_id": issued.question_id}}


async def collect_view(client: ApiClient, task: dict[str, Any], issued: Issued, token: str, *, tenant_id: str, timed_out: bool = False) -> dict[str, Any]:
    """The asker's own view of the outcome (GET results only), in the shape bench/score.py reads."""
    view = _empty_view(task, issued, tenant_id)
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
    # goal-level output (what the loop's own questions produced under the same goal) is visible to the asker too: the disclosure check covers it
    view["goal_claims"], view["goal_discoveries"], view["goal_evidence"] = [], [], []
    if issued.question_id and issued.goal_id and view["goal"] is not None:
        st, g = await client.call("GET", f"/api/goals/{issued.goal_id}", token)
        if st == 200:
            for c in (g.get("claims") or [])[:60]:
                if c.get("claim_id") in claim_ids:
                    continue
                st2, cd = await client.call("GET", f"/api/claims/{c['claim_id']}", token)
                if st2 == 200:
                    view["goal_claims"].append(cd)
                    for r in cd.get("evidence") or []:
                        if isinstance(r, dict) and r.get("ref_id"):
                            seen_refs.setdefault(r["ref_id"], r)
                            view["goal_evidence"].append(r)
            for d in (g.get("discoveries") or [])[:30]:
                if d.get("discovery_id") in disc_ids:
                    continue
                st2, dd = await client.call("GET", f"/api/discoveries/{d['discovery_id']}", token)
                if st2 == 200:
                    view["goal_discoveries"].append(dd)
                    for r in dd.get("evidence") or []:        # the scorer counts these references as visible too: they get their raw check as well (H2)
                        if isinstance(r, dict) and r.get("ref_id"):
                            seen_refs.setdefault(r["ref_id"], r)
    for rid in list(seen_refs):                                  # every visible reference, not a prefix
        st, _ = await client.call("GET", f"/api/evidence/{rid}/raw", token)
        view["raw_checks"][rid] = st
    if view["status"] == "ok" and timed_out:
        view["status"] = "timeout"
    return view


# ---------------------------------------------------------------------------------------------- per-task isolation (H1)
# BENCHMARK_CONTRACT section 5: an API error, an exception, a missing view or a timeout makes THAT task wrong; it never aborts the run
# (a starved API answering one request late must not cost the other tasks, or a one-shot holdout run, their results). The run driver
# calls these wrappers for every per-task API interaction of its wave loop; anything outside them (materialize, feed, worker start) still aborts.
KINDS = ("issue", "status", "loop", "view")


def describe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


class ApiErrors:
    """Tally of per-task API failures: ``counts`` per kind (``issue`` / ``status`` / ``loop`` / ``view``) and ``tasks`` (task id -> kind -> n).
    Each (kind, task) is logged on its first failure only, so a polling loop cannot flood the log; every failure is counted."""

    def __init__(self, log: Any = None) -> None:
        self.counts: dict[str, int] = {k: 0 for k in KINDS}
        self.tasks: dict[str, dict[str, int]] = {}
        self._log = log

    def record(self, kind: str, task_id: str, exc: BaseException) -> None:
        self.counts[kind] += 1
        per = self.tasks.setdefault(task_id, {})
        per[kind] = per.get(kind, 0) + 1
        if per[kind] == 1 and self._log is not None:
            self._log(f"API error ({kind}) for task {task_id}: {describe_error(exc)}; the task is affected, the run goes on")


async def safe_issue_task(client: ApiClient, task: dict[str, Any], *, errors: ApiErrors, **kw: Any) -> Issued:
    """``issue_task``; an exception marks this task as an error (``Issued.error``), so its view is an error view."""
    try:
        return await issue_task(client, task, **kw)
    except Exception as exc:  # noqa: BLE001 - per-task isolation (TimeoutError, aiohttp.ClientError, anything)
        errors.record("issue", task["task_id"], exc)
        return Issued(task["task_id"], issued_at=time.time(), error=f"issue: {describe_error(exc)}")


async def safe_question_status(client: ApiClient, qid: str, token: str, *, errors: ApiErrors, task_id: str) -> str:
    """``question_status``; a failed poll counts as unresolved (it is not in ``RESOLVED``), so the caller keeps polling until its deadline."""
    try:
        return await question_status(client, qid, token)
    except Exception as exc:  # noqa: BLE001
        errors.record("status", task_id, exc)
        return "error"


async def safe_run_loop_now(client: ApiClient, goal_id: str, token: str, *, errors: ApiErrors, task_id: str) -> int | None:
    """``run_loop_now``; a failure is logged and counted and the run continues (``None`` instead of the HTTP status)."""
    try:
        return await run_loop_now(client, goal_id, token)
    except Exception as exc:  # noqa: BLE001
        errors.record("loop", task_id, exc)
        return None


def error_view(task: dict[str, Any], issued: Issued, *, tenant_id: str, exc: BaseException) -> dict[str, Any]:
    """The view of a task whose reads failed: the shape ``collect_view`` returns for its own error views, with the exception as the error.
    ``tenant_id`` is the asker's (the scorer requires it for the cross-tenant check)."""
    view = _empty_view(task, issued, tenant_id)
    view.update(status="error", error=describe_error(exc))
    return view


async def safe_collect_view(client: ApiClient, task: dict[str, Any], issued: Issued, token: str, *, tenant_id: str, errors: ApiErrors,
                            timed_out: bool = False) -> dict[str, Any]:
    """``collect_view``; any exception while reading the outcome yields an error view for this task only."""
    try:
        return await collect_view(client, task, issued, token, tenant_id=tenant_id, timed_out=timed_out)
    except Exception as exc:  # noqa: BLE001
        errors.record("view", task["task_id"], exc)
        return error_view(task, issued, tenant_id=tenant_id, exc=exc)
