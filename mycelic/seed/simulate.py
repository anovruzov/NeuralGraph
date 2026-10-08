"""``demo.simulate``: bounded, clearly labelled activity for the demonstration tenant.

A demonstration that never changes is hard to tell from a screenshot. Every run of this job ingests one more
fictional document (title prefix ``[Simulated]``) into the next demo holder in rotation, emits the same
``document.ingested`` event a real upload produces and wakes the loops that can route to that holder, so the
discovery loop is seen observing new evidence. The cadence is bounded (one run every ten minutes, at most 24 runs
per UTC day) and the job only re-enqueues itself while the demo tenant exists and ``settings.demo_mode`` is on, so
a production deployment that seeds the demo once cannot be kept busy by it.

Embedded holders are written through their store; an external holder (a separate process) receives an ``ingest``
envelope over the transport and its own ``ingest_result`` produces the event and the wake-up, exactly as an upload
through the API would.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..transport import Envelope, Subjects
from ..util import now_iso, utcnow

logger = logging.getLogger(__name__)

JOB_KIND = "demo.simulate"
CADENCE_SECONDS = 600.0
MAX_RUNS_PER_DAY = 24
FIRST_DELAY_SECONDS = 60.0
TITLE_PREFIX = "[Simulated]"

_TEMPLATES: dict[str, str] = {
    "deployments": "the hotfix waited for a deploy approval before it could go live",
    "approvals": "the approval queue held the change until the next change board meeting",
    "dispatch": "a route update waited for approval while drivers held at the depot",
    "support": "tickets stayed open while support waited on an approval",
    "metrics": "the weekly resolution time report was delayed while approvals were pending",
    "on-call": "the on-call rotation was short one engineer and pages waited for acknowledgement",
    "escalation": "an escalation waited for the platform lead before anyone acknowledged it",
    "customs": "customs paperwork held a cross-border shipment at the border for a day",
    "maintenance": "the deck oven waited for its descaling slot before the morning bake",
}


def _day(at: datetime | None = None) -> str:
    return (at or utcnow()).strftime("%Y%m%d")


def simulated_document(run: int, holder: dict[str, Any]) -> dict[str, Any]:
    """One plausible recurring-blocker note in the holder's first domain; labelled as simulated in title and text."""
    domains = [d for d in (holder.get("domains") or []) if d != "*"]
    domain = domains[0] if domains else "operations"
    detail = _TEMPLATES.get(domain, "a routine hand-off waited for an approval that nobody owned")
    n = run + 1
    return {
        "title": f"{TITLE_PREFIX} Weekly incident note {n}",
        "text": (f"{TITLE_PREFIX} Weekly incident note {n} ({domain}). Recurring {domain} blocker this week: {detail}. "
                 "Simulated demonstration data, not a real record."),
        "kind": "note", "observed_at": now_iso(), "domains": [domain], "uploaded_by": "demo.simulate",
    }


def install(rt: Any) -> None:
    """Register the handler on the engine (the worker dispatches ``demo.simulate`` jobs to ``engine.hooks``)."""
    rt.engine.hooks[JOB_KIND] = make_handler(rt)


async def enqueue_first(rt: Any, tenant_id: str, *, delay_seconds: float = FIRST_DELAY_SECONDS) -> int | None:
    """The seed's first run (idempotency key ``demo.simulate:<tenant>:0``): a no-op when it already exists."""
    return await rt.jobs.enqueue(JOB_KIND, idempotency_key=f"{JOB_KIND}:{tenant_id}:0", tenant_id=tenant_id, ref_type="tenant", ref_id=tenant_id,
                                 payload={"run": 0, "day": _day(), "n": 0}, priority=7, delay_seconds=delay_seconds, max_attempts=3)


async def run_now(rt: Any, tenant_id: str) -> int | None:
    """Pull the tenant's next queued simulate job forward to now (the scenario uses it; the UI could too)."""
    async with rt.db.tx() as c:
        r = c.execute("SELECT job_id FROM jobs WHERE kind=? AND tenant_id=? AND status='queued' ORDER BY available_at LIMIT 1", (JOB_KIND, tenant_id)).fetchone()
        if r is None:
            return None
        c.execute("UPDATE jobs SET available_at=?, updated_at=? WHERE job_id=?", (now_iso(), now_iso(), r["job_id"]))
        return int(r["job_id"])


def _next_slot(payload: dict[str, Any]) -> tuple[str, int, float]:
    """(day bucket, run-in-day, delay) for the following run under the cadence and the per-day cap."""
    now = utcnow()
    today = _day(now)
    n = int(payload.get("n") or 0) if payload.get("day") == today else 0
    if n + 1 < MAX_RUNS_PER_DAY:
        return today, n + 1, CADENCE_SECONDS
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    return _day(tomorrow), 0, max(CADENCE_SECONDS, (tomorrow - now).total_seconds() + 60.0)


async def schedule_next(rt: Any, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    day, n, delay = _next_slot(payload)
    run = int(payload.get("run") or 0) + 1
    key = f"{JOB_KIND}:{tenant_id}:{day}:{n}"
    job_id = await rt.jobs.enqueue(JOB_KIND, idempotency_key=key, tenant_id=tenant_id, ref_type="tenant", ref_id=tenant_id,
                                   payload={"run": run, "day": day, "n": n}, priority=7, delay_seconds=delay, max_attempts=3)
    return {"idempotency_key": key, "job_id": job_id, "delay_seconds": delay, "run": run}


def make_handler(rt: Any):
    async def handler(job: Any) -> dict[str, Any]:
        tid = job.tenant_id or (job.payload or {}).get("tenant_id")
        tenant = rt.org.get_tenant(tid) if tid else None
        if tenant is None or not tenant.get("is_demo"):
            return {"skipped": "demo tenant missing", "tenant_id": tid}
        if not getattr(rt.settings, "demo_mode", False):
            return {"skipped": "demo mode is off; not re-enqueued", "tenant_id": tid}
        payload = dict(job.payload or {})
        run = int(payload.get("run") or 0)
        holders = sorted((h for h in rt.org.list_holders(tid) if h.get("status") != "revoked"), key=lambda h: h["holder_id"])
        if not holders:
            return {"skipped": "no holders", "tenant_id": tid}
        holder = holders[run % len(holders)]
        hid = holder["holder_id"]
        doc = simulated_document(run, holder)
        doc_id = f"doc_sim_{tid[-6:]}_{run}"
        store = rt.holders.get(hid) if rt.holders is not None and hasattr(rt.holders, "get") else None
        woken: list[str] = []
        if store is not None:
            result = await store.ingest_document(doc["title"], doc["text"], kind=doc["kind"], observed_at=doc["observed_at"], domains=doc["domains"],
                                                 uploaded_by=doc["uploaded_by"], doc_id=doc_id)
            owner_aud = {"user_ids": [holder["owner_id"]]} if holder["owner_type"] == "user" else {"unit_ids": [holder["owner_id"]]}
            await rt.db.emit(tid, "document.ingested", ref_type="holder", ref_id=hid,
                             payload={"holder_id": hid, "title": result["title"], "domains": result["domains"], "doc_id": result["doc_id"], "simulated": True},
                             audience=owner_aud)
            woken = await rt.engine.wake_goals_for_holder(holder)
            delivery = "store"
        else:
            # external holder: the request travels over the transport; its ingest_result produces the event and the wake
            env = Envelope.new(Subjects.holder_ingest(tid, hid), "ingest", tid, {**doc, "doc_id": doc_id}, msg_id=f"{JOB_KIND}:{tid}:{run}:ingest")
            env.sign(rt.org.route_key(hid))
            if rt.transport is None:
                return {"skipped": "no transport for an external holder", "holder_id": hid}
            await rt.transport.publish(env)
            delivery = "transport"
        nxt = await schedule_next(rt, tid, payload)
        logger.info("demo.simulate run %d: %r -> %s via %s; next %s", run, doc["title"], hid, delivery, nxt["idempotency_key"])
        return {"run": run, "holder_id": hid, "doc_id": doc_id, "title": doc["title"], "delivery": delivery, "woken_goals": woken, "next": nxt}

    return handler
