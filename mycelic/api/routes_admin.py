"""Administration and notifications (docs/mycelic/API.md "Workspaces" admin rows, "Live updates" notifications).

Administrators see operations (workers, jobs, holders, usage, budgets, loops, deployment facts) but no claims or
evidence (DECISIONS D9); everything is scoped to the caller's tenant except the ``workers`` table, which describes
the processes of this deployment. Provider keys are never returned: ``settings.public_summary()`` and
``router.describe()`` only carry names.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from .. import __version__
from ..db.migrate import migration_status
from ..models.base import TIERS
from ..util import now_iso, parse_iso, utcnow
from .middleware import ApiError, json_response, limit_of, listing, opt_dict, query_flag, read_json, require_admin, require_user
from .routes_org import holder_view

logger = logging.getLogger(__name__)


def workers_view(rt: Any) -> list[dict[str, Any]]:
    from ..db.coord import rows_to_dicts
    now = utcnow()
    out = []
    for w in rows_to_dicts(rt.db.all("SELECT * FROM workers ORDER BY last_heartbeat_at DESC"), json_fields=("stats",)):
        hb = parse_iso(w.get("last_heartbeat_at"))
        w["heartbeat_age_seconds"] = round((now - hb).total_seconds(), 1) if hb else None
        w["busy"] = bool(w.get("busy"))
        w["alive"] = w["heartbeat_age_seconds"] is not None and w["heartbeat_age_seconds"] <= 60
        out.append(w)
    return out


def newest_heartbeat_age(rt: Any) -> float | None:
    hb = rt.db.scalar("SELECT MAX(last_heartbeat_at) FROM workers")
    t = parse_iso(hb) if hb else None
    return round((utcnow() - t).total_seconds(), 1) if t else None


def usage_bucket(t: dict[str, Any]) -> dict[str, Any]:
    return {"calls": int(t.get("calls", 0)), "tokens": int(t.get("tokens", 0)), "usd": round(float(t.get("cost_usd", 0.0)), 6)}


def usage_summary(rt: Any, tenant_id: str) -> dict[str, Any]:
    zero = {"calls": 0, "tokens": 0, "usd": 0.0}
    if rt.ledger is None:
        return {"today": dict(zero), "month": dict(zero), "all": dict(zero), "by_tier": {}}
    today = now_iso()[:10] + "T00:00:00+00:00"
    month = now_iso()[:7] + "-01T00:00:00+00:00"
    try:
        all_t = rt.ledger.totals(tenant_id)
        return {"today": usage_bucket(rt.ledger.totals(tenant_id, since=today)), "month": usage_bucket(rt.ledger.totals(tenant_id, since=month)),
                "all": usage_bucket(all_t), "by_tier": {k: usage_bucket(v) for k, v in (all_t.get("by_tier") or {}).items()},
                "by_model": {k: usage_bucket(v) for k, v in (all_t.get("by_model") or {}).items()}}
    except Exception:
        logger.exception("usage totals failed")
        return {"today": dict(zero), "month": dict(zero), "all": dict(zero), "by_tier": {}}


def models_view(rt: Any, settings: Any, tenant_id: str) -> dict[str, Any]:
    tiers: dict[str, Any] = {}
    embedding: dict[str, Any] | None = None
    prices: dict[str, Any] = {}
    if rt.router is not None:
        try:
            desc = rt.router.describe() or {}
            tiers = desc.get("tiers") or {}
            embedding = desc.get("embedding")
            prices = desc.get("prices") or {}
        except Exception:
            logger.exception("router.describe failed")
    if not tiers:
        for tier, spec in (settings.model_tiers or {}).items():
            provider, _, model = (spec or "").partition(":")
            tiers[tier] = {"provider": provider or "fake", "model": model or ""}
    if embedding is None:
        embedding = {"provider": settings.embed_provider, "model": settings.embed_model}
    return {"tiers": tiers, "embedding": embedding, "prices": prices, "policy_tiers": rt.org.policy(tenant_id, "model_tiers", {}) or {},
            "providers": {"anthropic_configured": bool(settings.anthropic_api_key), "openai_configured": bool(settings.openai_api_key)},
            "configured": rt.router is not None}


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]
    settings = app["settings"]

    async def overview(request: web.Request) -> web.Response:
        p = require_admin(request)
        tid = p.tenant_id
        counts = rt.jobs.counts(tid)
        jobs = {k: int(counts.get(k, 0)) for k in ("queued", "leased", "done", "dead", "failed", "cancelled")}
        goals = rt.goals.list_goals(rt.authz.principal_for_system(tid), include_archived=False, limit=500)
        transport_name = getattr(rt.transport, "name", None) or (settings.transport if rt.transport is not None else "none")
        try:
            mig = migration_status(rt.db.conn)
            migrations = {"current": mig["current"], "latest": mig["latest"], "pending": len(mig["pending"]), "pending_names": [m["name"] for m in mig["pending"]]}
        except Exception:
            migrations = {"current": None, "latest": None, "pending": 0}
        freshness = rt.knowledge.evidence_freshness(tid)
        return json_response({
            "workers": workers_view(rt), "jobs": jobs, "failed_jobs": rt.jobs.list(status="dead", tenant_id=tid, limit=100),
            "holders": [holder_view(rt, h) for h in rt.org.list_holders(tid)], "usage": usage_summary(rt, tid),
            "budgets": [{"goal_id": g["goal_id"], "title": g["title"], "status": g["status"], "budget": g.get("budget") or {}, "spent": g.get("budget_spent") or {}} for g in goals],
            "loops": rt.goals.loops_for_tenant(tid),
            "deployment": {"version": __version__, "settings": settings.public_summary(), "migrations": migrations, "transport": transport_name,
                           "uptime_seconds": round(time.monotonic() - app["started_monotonic"], 1), "started_at": app.get("started_at"),
                           "worker_in_process": rt.worker is not None and getattr(rt.worker, "running", False),
                           "embedded_holders": [] if rt.holders is None else list(rt.holders.holder_ids()),
                           "errors_recent": app["reporter"].recent(limit=20) if app.get("reporter") is not None else []},
            "metrics": {"queue_backlog": rt.jobs.backlog(tid), "failed_routes": rt.questions.failed_routes(tid), "question_outcomes": rt.questions.outcome_counts(tid),
                        "evidence_freshness": {"stale": freshness.get("stale", 0), "fresh": freshness.get("fresh", 0), "unknown": freshness.get("unknown", 0),
                                               "total": freshness.get("total", 0), "median_age_days": freshness.get("median_age_days")},
                        "knowledge": rt.knowledge.counts(tid), "sse_subscribers": app["hub"].subscribers if app.get("hub") is not None else 0},
        })

    async def jobs_list(request: web.Request) -> web.Response:
        p = require_admin(request)
        items = rt.jobs.list(status=request.query.get("status") or None, tenant_id=p.tenant_id, kind=request.query.get("kind") or None, limit=limit_of(request, 100))
        return json_response(listing(items))

    def _job_or_404(p: Any, raw: str) -> Any:
        try:
            jid = int(raw)
        except ValueError:
            raise KeyError(raw) from None
        job = rt.jobs.get(jid)
        if job is None or job.tenant_id != p.tenant_id:
            raise KeyError(raw)
        return job

    async def job_retry(request: web.Request) -> web.Response:
        p = require_admin(request)
        job = _job_or_404(p, request.match_info["id"])
        if job.status != "dead":
            raise ApiError(409, f"job is {job.status}; only dead jobs can be retried")
        n = await rt.jobs.retry_dead([job.job_id])
        await rt.db.audit(p.tenant_id, "user", p.id, "job.retry", resource_type="job", resource_id=str(job.job_id), request_id=request.get("request_id"))
        if rt.worker is not None:
            rt.worker.wake()
        return json_response({"job": rt.jobs.get(job.job_id).to_dict(), "retried": n, "attempts": rt.jobs.attempts(job.job_id)})

    async def jobs_retry_dead(request: web.Request) -> web.Response:
        p = require_admin(request)
        n = await rt.jobs.retry_dead(tenant_id=p.tenant_id)
        await rt.db.audit(p.tenant_id, "user", p.id, "job.retry_dead", detail={"retried": n}, request_id=request.get("request_id"))
        if rt.worker is not None and n:
            rt.worker.wake()
        return json_response({"retried": n})

    async def audit(request: web.Request) -> web.Response:
        p = require_admin(request)
        items = rt.org.audit_events(p.tenant_id, limit=limit_of(request, 100, 1000), action_prefix=request.query.get("action") or None,
                                    resource_id=request.query.get("resource_id") or None)
        return json_response(listing(items))

    async def usage(request: web.Request) -> web.Response:
        p = require_admin(request)
        since = request.query.get("since") or None
        if rt.ledger is None:
            return json_response({"items": [], "total": 0, "totals": {"calls": 0, "tokens": 0, "cost_usd": 0.0, "by_tier": {}, "by_model": {}}, "summary": usage_summary(rt, p.tenant_id)})
        items = rt.ledger.recent(limit_of(request, 200, 2000), tenant_id=p.tenant_id, since=since)
        return json_response({"items": items, "total": len(items), "totals": rt.ledger.totals(p.tenant_id, since=since), "summary": usage_summary(rt, p.tenant_id)})

    async def models_get(request: web.Request) -> web.Response:
        p = require_admin(request)
        return json_response(models_view(rt, settings, p.tenant_id))

    async def models_put(request: web.Request) -> web.Response:
        p = require_admin(request)
        body = await read_json(request)
        policy_tiers = opt_dict(body, "policy_tiers")
        if policy_tiers is None:
            raise ApiError(400, "'policy_tiers' is required (task family -> tier)")
        for k, v in policy_tiers.items():
            if not isinstance(k, str) or v not in TIERS:
                raise ApiError(400, f"policy_tiers values must be one of {', '.join(TIERS)}")
        merged = {**(rt.org.policy(p.tenant_id, "model_tiers", {}) or {}), **policy_tiers}
        await rt.org.set_policy(p.tenant_id, "model_tiers", merged, actor_id=p.id)
        return json_response(models_view(rt, settings, p.tenant_id))

    async def workers(request: web.Request) -> web.Response:
        require_admin(request)
        ws = workers_view(rt)
        return json_response({"items": ws, "total": len(ws), "workers": ws, "newest_heartbeat_age_seconds": newest_heartbeat_age(rt)})

    # ------------------------------------------------------------------ notifications (any user)
    async def notifications(request: web.Request) -> web.Response:
        p = require_user(request)
        items = rt.org.notifications_for(p.id, unread_only=query_flag(request, "unread"), limit=limit_of(request, 50, 500))
        unread = int(rt.db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (p.id,), 0))
        return json_response(listing(items, unread=unread))

    async def notifications_read(request: web.Request) -> web.Response:
        p = require_user(request)
        body = await read_json(request, required=False)
        ids = body.get("ids")
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(i, int) for i in ids)):
            raise ApiError(400, "'ids' must be a list of integers")
        n = await rt.org.mark_notifications_read(p.id, ids)
        return json_response({"updated": n, "ok": True})

    app.router.add_get(f"{prefix}/admin/overview", overview)
    app.router.add_get(f"{prefix}/admin/jobs", jobs_list)
    app.router.add_post(f"{prefix}/admin/jobs/retry-dead", jobs_retry_dead)
    app.router.add_post(f"{prefix}/admin/jobs/{{id}}/retry", job_retry)
    app.router.add_get(f"{prefix}/admin/audit", audit)
    app.router.add_get(f"{prefix}/admin/usage", usage)
    app.router.add_get(f"{prefix}/admin/models", models_get)
    app.router.add_put(f"{prefix}/admin/models", models_put)
    app.router.add_get(f"{prefix}/admin/workers", workers)
    app.router.add_get(f"{prefix}/notifications", notifications)
    app.router.add_post(f"{prefix}/notifications/read", notifications_read)


__all__ = ["setup", "workers_view", "newest_heartbeat_age", "usage_summary", "models_view"]
