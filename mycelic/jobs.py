"""Durable job queue on the coordination database (docs/mycelic/DECISIONS.md D5).

Semantics
---------
* ``enqueue`` is idempotent on ``idempotency_key`` (a second enqueue is a no-op and returns ``None``).
* ``lease`` hands out the highest-priority runnable job and stamps ``leased_until``; ``heartbeat`` extends
  it; a lease that expires is swept back to ``queued`` (``requeue_expired``), so a crashed worker's job is
  retried by another worker.
* ``complete`` / ``fail`` are fenced on ``worker_id``: a zombie whose lease expired cannot finish or
  dead-letter a job another worker now holds (it gets ``"lost"``).
* ``fail`` requeues with exponential backoff until ``max_attempts``; then the job is ``dead`` (the failed-job
  queue in the admin UI) and can be retried by hand with ``retry_dead``.
* Every attempt is recorded in ``job_attempts``.

Callers commit their effects inside the same transaction as ``complete_sync`` whenever they can (pass the
connection), so a crash between "effect" and "ack" is impossible; where the effect is external (a model
call, a transport publish) the effect itself is keyed by an idempotency key so replaying is harmless.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from .db.coord import CoordDB, row_to_dict, rows_to_dicts
from .util import iso, j, now_iso, now_precise, plus_seconds, utcnow


@dataclass
class Job:
    job_id: int
    tenant_id: str | None
    kind: str
    ref_type: str | None
    ref_id: str | None
    idempotency_key: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    available_at: str
    leased_until: str | None
    worker_id: str | None
    last_error: str | None
    payload: dict[str, Any]
    result: dict[str, Any]
    created_at: str
    updated_at: str
    finished_at: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Job":
        d = row_to_dict(r, json_fields=("payload", "result"))
        assert d is not None
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


JOB_KINDS = (
    "loop.tick",            # observe + plan for one goal
    "question.route",       # deliver a question to its holders
    "question.collect",     # gather responses (or time out)
    "question.evaluate",    # evaluate support / disagreement / freshness / relevance
    "question.verify",      # independent verification round
    "question.commit",      # commit gate + knowledge update + goal update + follow-ups
    "claim.reverify",       # evidence changed: re-check affected claims
    "goal.progress",        # recompute a goal's progress from outcomes
    "holder.ingest",        # (embedded holders) ingest a document
    "demo.simulate",        # labelled simulated activity for the demonstration tenant
    "maintenance",          # sweep leases, expire questions, prune transport/audit
)


class JobQueue:
    def __init__(self, db: CoordDB) -> None:
        self.db = db

    # ------------------------------------------------------------------ enqueue
    @staticmethod
    def enqueue_sync(c: sqlite3.Connection, kind: str, *, idempotency_key: str, tenant_id: str | None = None,
                     ref_type: str | None = None, ref_id: str | None = None, payload: dict[str, Any] | None = None,
                     priority: int = 5, delay_seconds: float = 0.0, max_attempts: int = 5) -> int | None:
        now = now_iso()
        avail = plus_seconds(delay_seconds) if delay_seconds > 0 else now
        cur = c.execute(
            """INSERT INTO jobs(tenant_id, kind, ref_type, ref_id, idempotency_key, status, priority, max_attempts,
                                available_at, payload, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
               ON CONFLICT(idempotency_key) DO NOTHING""",
            (tenant_id, kind, ref_type, ref_id, idempotency_key, int(priority), int(max_attempts), avail, j(payload), now, now),
        )
        return int(cur.lastrowid) if cur.rowcount else None

    async def enqueue(self, kind: str, **kw: Any) -> int | None:
        async with self.db.tx() as c:
            return self.enqueue_sync(c, kind, **kw)

    # ------------------------------------------------------------------ lease / heartbeat
    async def lease(self, worker_id: str, lease_seconds: float, *, kinds: Iterable[str] | None = None,
                    tenant_id: str | None = None) -> Job | None:
        now = now_iso()
        until = plus_seconds(lease_seconds)
        kind_list = list(kinds) if kinds else None
        async with self.db.tx() as c:
            sql = "SELECT * FROM jobs WHERE status='queued' AND available_at <= ?"
            args: list[Any] = [now_precise()]
            if kind_list:
                sql += f" AND kind IN ({','.join('?' * len(kind_list))})"; args.extend(kind_list)
            if tenant_id:
                sql += " AND tenant_id = ?"; args.append(tenant_id)
            sql += " ORDER BY priority, available_at, job_id LIMIT 1"
            r = c.execute(sql, args).fetchone()
            if r is None:
                return None
            attempt = int(r["attempts"]) + 1
            c.execute("UPDATE jobs SET status='leased', leased_until=?, worker_id=?, attempts=?, updated_at=? WHERE job_id=?",
                      (until, worker_id, attempt, now, r["job_id"]))
            c.execute("INSERT INTO job_attempts(job_id, worker_id, attempt, started_at) VALUES (?, ?, ?, ?)",
                      (r["job_id"], worker_id, attempt, now))
            job = Job.from_row(c.execute("SELECT * FROM jobs WHERE job_id=?", (r["job_id"],)).fetchone())
        return job

    async def heartbeat(self, job_id: int, worker_id: str, lease_seconds: float) -> bool:
        async with self.db.tx() as c:
            cur = c.execute("UPDATE jobs SET leased_until=?, updated_at=? WHERE job_id=? AND status='leased' AND worker_id=?",
                            (plus_seconds(lease_seconds), now_iso(), job_id, worker_id))
            return cur.rowcount == 1

    def holds_lease_sync(self, c: sqlite3.Connection, job_id: int, worker_id: str) -> bool:
        r = c.execute("SELECT 1 FROM jobs WHERE job_id=? AND status='leased' AND worker_id=?", (job_id, worker_id)).fetchone()
        return r is not None

    # ------------------------------------------------------------------ finish
    @staticmethod
    def complete_sync(c: sqlite3.Connection, job_id: int, worker_id: str, result: dict[str, Any] | None = None) -> str:
        """Mark done inside the caller's transaction. Returns 'done' or 'lost' (fenced)."""
        now = now_iso()
        cur = c.execute("UPDATE jobs SET status='done', result=?, finished_at=?, updated_at=?, leased_until=NULL "
                        "WHERE job_id=? AND status='leased' AND worker_id=?", (j(result), now, now, job_id, worker_id))
        if cur.rowcount != 1:
            c.execute("UPDATE job_attempts SET finished_at=?, outcome='lost_lease' WHERE job_id=? AND worker_id=? AND finished_at IS NULL",
                      (now, job_id, worker_id))
            return "lost"
        c.execute("UPDATE job_attempts SET finished_at=?, outcome='ok' WHERE job_id=? AND worker_id=? AND finished_at IS NULL",
                  (now, job_id, worker_id))
        return "done"

    async def complete(self, job_id: int, worker_id: str, result: dict[str, Any] | None = None) -> str:
        async with self.db.tx() as c:
            return self.complete_sync(c, job_id, worker_id, result)

    async def fail(self, job_id: int, worker_id: str, error: str, *, backoff_base: float = 5.0,
                   backoff_max: float = 600.0, retry: bool = True) -> str:
        """Requeue with backoff or dead-letter. Returns 'queued' | 'dead' | 'lost'."""
        now = now_iso()
        err = (error or "")[:2000]
        async with self.db.tx() as c:
            r = c.execute("SELECT attempts, max_attempts, status, worker_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if r is None or r["status"] != "leased" or r["worker_id"] != worker_id:
                return "lost"
            c.execute("UPDATE job_attempts SET finished_at=?, outcome='error', error=? WHERE job_id=? AND worker_id=? AND finished_at IS NULL",
                      (now, err, job_id, worker_id))
            if not retry or r["attempts"] >= r["max_attempts"]:
                c.execute("UPDATE jobs SET status='dead', last_error=?, finished_at=?, updated_at=?, leased_until=NULL WHERE job_id=?",
                          (err, now, now, job_id))
                return "dead"
            delay = min(backoff_max, backoff_base * (2 ** max(0, r["attempts"] - 1))) * (0.8 + 0.4 * random.random())
            c.execute("UPDATE jobs SET status='queued', last_error=?, available_at=?, updated_at=?, leased_until=NULL, worker_id=NULL WHERE job_id=?",
                      (err, plus_seconds(delay), now, job_id))
            return "queued"

    async def cancel(self, *, ref_type: str, ref_id: str, kinds: Iterable[str] | None = None) -> int:
        async with self.db.tx() as c:
            sql = "UPDATE jobs SET status='cancelled', updated_at=?, finished_at=? WHERE ref_type=? AND ref_id=? AND status='queued'"
            args: list[Any] = [now_iso(), now_iso(), ref_type, ref_id]
            kl = list(kinds) if kinds else None
            if kl:
                sql += f" AND kind IN ({','.join('?' * len(kl))})"; args.extend(kl)
            return c.execute(sql, args).rowcount

    async def requeue_expired(self) -> int:
        now = now_iso()
        precise = now_precise()
        async with self.db.tx() as c:
            rows = c.execute("SELECT job_id, worker_id FROM jobs WHERE status='leased' AND leased_until < ?", (precise,)).fetchall()
            for r in rows:
                c.execute("UPDATE job_attempts SET finished_at=?, outcome='lost_lease', error='lease expired' WHERE job_id=? AND worker_id=? AND finished_at IS NULL",
                          (now, r["job_id"], r["worker_id"]))
            cur = c.execute("UPDATE jobs SET status='queued', leased_until=NULL, worker_id=NULL, updated_at=?, "
                            "last_error=COALESCE(last_error, 'lease expired') WHERE status='leased' AND leased_until < ?", (now, precise))
            return cur.rowcount

    async def retry_dead(self, job_ids: Iterable[int] | None = None, *, tenant_id: str | None = None) -> int:
        now = now_iso()
        async with self.db.tx() as c:
            if job_ids is not None:
                ids = [int(i) for i in job_ids]
                if not ids:
                    return 0
                cur = c.execute(f"UPDATE jobs SET status='queued', attempts=0, available_at=?, updated_at=?, finished_at=NULL "
                                f"WHERE status='dead' AND job_id IN ({','.join('?' * len(ids))})", (now, now, *ids))
            elif tenant_id:
                cur = c.execute("UPDATE jobs SET status='queued', attempts=0, available_at=?, updated_at=?, finished_at=NULL WHERE status='dead' AND tenant_id=?",
                                (now, now, tenant_id))
            else:
                cur = c.execute("UPDATE jobs SET status='queued', attempts=0, available_at=?, updated_at=?, finished_at=NULL WHERE status='dead'", (now, now))
            return cur.rowcount

    async def prune(self, *, older_than_days: float = 7.0) -> int:
        cutoff = plus_seconds(-older_than_days * 86400)
        async with self.db.tx() as c:
            c.execute("DELETE FROM job_attempts WHERE job_id IN (SELECT job_id FROM jobs WHERE status IN ('done','cancelled') AND finished_at < ?)", (cutoff,))
            return c.execute("DELETE FROM jobs WHERE status IN ('done','cancelled') AND finished_at < ?", (cutoff,)).rowcount

    # ------------------------------------------------------------------ introspection
    def counts(self, tenant_id: str | None = None) -> dict[str, int]:
        if tenant_id:
            rows = self.db.all("SELECT status, COUNT(*) AS n FROM jobs WHERE tenant_id=? GROUP BY status", (tenant_id,))
        else:
            rows = self.db.all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def backlog(self, tenant_id: str | None = None) -> int:
        c = self.counts(tenant_id)
        return c.get("queued", 0) + c.get("leased", 0)

    def next_available_at(self) -> str | None:
        return self.db.scalar("SELECT MIN(available_at) FROM jobs WHERE status='queued'")

    def list(self, *, status: str | None = None, tenant_id: str | None = None, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            sql += " AND status=?"; args.append(status)
        if tenant_id:
            sql += " AND tenant_id=?"; args.append(tenant_id)
        if kind:
            sql += " AND kind=?"; args.append(kind)
        sql += " ORDER BY job_id DESC LIMIT ?"; args.append(int(limit))
        return rows_to_dicts(self.db.all(sql, args), json_fields=("payload", "result"))

    def get(self, job_id: int) -> Job | None:
        r = self.db.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return Job.from_row(r) if r else None

    def attempts(self, job_id: int) -> list[dict[str, Any]]:
        return rows_to_dicts(self.db.all("SELECT * FROM job_attempts WHERE job_id=? ORDER BY id", (job_id,)))
