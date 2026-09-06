"""Aggregations powering the dashboard and the CLI status command."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from ..database.db import Database
from ..database.models import JobStatus


def _iso_to_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def collect(db: Database, run_id: Optional[str] = None) -> dict[str, Any]:
    run = db.get_run(run_id) if run_id else db.latest_run()
    run_data = dict(run) if run else {}

    counts = {row["status"]: row["n"] for row in
              db.query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    app_counts = {row["status"]: row["n"] for row in
                  db.query("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")}

    qwen = db.query_one(
        "SELECT COUNT(*) AS requests, "
        "COALESCE(SUM(total_tokens),0) AS tokens, "
        "COALESCE(SUM(estimated_cost_usd),0) AS cost, "
        "COALESCE(SUM(cache_hit),0) AS cache_hits, "
        "COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) AS failures "
        "FROM qwen_calls"
    )
    qwen_data = dict(qwen) if qwen else {}

    avg_row = db.query_one("SELECT AVG(score) AS avg_score FROM jobs WHERE score IS NOT NULL")
    scored_avg = round(avg_row["avg_score"], 1) if avg_row and avg_row["avg_score"] else 0.0

    started = _iso_to_ts(run_data.get("started_at"))
    ended = _iso_to_ts(run_data.get("ended_at"))
    elapsed = max(0.0, (ended or time.time()) - started) if started else 0.0

    verified = int(counts.get(JobStatus.VERIFIED, 0))
    ready = int(app_counts.get(JobStatus.READY_TO_SUBMIT, 0))
    completed = verified or ready
    per_hour = round(completed / (elapsed / 3600), 2) if elapsed > 60 else 0.0

    cost = float(qwen_data.get("cost") or 0.0)
    cost_per_verified = round(cost / verified, 4) if verified else 0.0

    recent = [dict(r) for r in db.query(
        "SELECT j.company, j.title, j.score, a.status, a.updated_at, a.blocker_type, "
        "a.ats_application_id FROM applications a JOIN jobs j ON j.job_id = a.job_id "
        "ORDER BY a.updated_at DESC LIMIT 15")]

    blockers = [dict(r) for r in db.query(
        "SELECT blocker_type, COUNT(*) AS n FROM applications "
        "WHERE blocker_type IS NOT NULL GROUP BY blocker_type ORDER BY n DESC")]

    errors = [dict(r) for r in db.query(
        "SELECT stage, kind, message, created_at FROM errors "
        "ORDER BY id DESC LIMIT 10")]

    top = [dict(r) for r in db.query(
        "SELECT company, title, score, status FROM jobs WHERE score IS NOT NULL "
        "ORDER BY score DESC LIMIT 10")]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": {
            "run_id": run_data.get("run_id"),
            "mode": run_data.get("mode"),
            "state": db.get_control("harness_state", run_data.get("state") or "UNKNOWN"),
            "started_at": run_data.get("started_at"),
            "ended_at": run_data.get("ended_at"),
            "elapsed_s": round(elapsed),
            "current_company": run_data.get("current_company"),
            "current_role": run_data.get("current_role"),
            "current_state": run_data.get("current_state") or "IDLE",
        },
        "pipeline": {
            "discovered": int(sum(counts.values())),
            "scored": int(sum(v for k, v in counts.items() if k != JobStatus.DISCOVERED)),
            "queued": int(counts.get(JobStatus.QUEUED, 0)),
            "skipped": int(counts.get(JobStatus.SKIPPED, 0)),
            "opened": int(app_counts.get(JobStatus.OPENED, 0) + app_counts.get(JobStatus.FILLING, 0)),
            "ready_to_submit": ready,
            "submitted": int(counts.get(JobStatus.SUBMITTED, 0)) + verified,
            "verified": verified,
            "blocked": int(counts.get(JobStatus.BLOCKED, 0)),
            "failed": int(counts.get(JobStatus.FAILED, 0)),
            "duplicates": int(counts.get(JobStatus.DUPLICATE, 0)),
        },
        "rates": {
            "applications_per_hour": per_hour,
            "average_fit_score": scored_avg,
            "cost_per_verified_application": cost_per_verified,
        },
        "qwen": {
            "requests": int(qwen_data.get("requests") or 0),
            "cache_hits": int(qwen_data.get("cache_hits") or 0),
            "tokens": int(qwen_data.get("tokens") or 0),
            "estimated_cost_usd": round(cost, 5),
            "failures": int(qwen_data.get("failures") or 0),
        },
        "recent": recent,
        "blockers": blockers,
        "errors": errors,
        "top_scored": top,
    }
