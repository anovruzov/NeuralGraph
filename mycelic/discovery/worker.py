"""The durable discovery worker process.

* ``concurrency`` asyncio loops lease jobs from :class:`mycelic.jobs.JobQueue`, run the matching
  :class:`LoopEngine` handler under a heartbeat that renews the lease, and complete or fail the job (fenced on
  the worker id). A worker that dies leaves an expired lease that the next sweep returns to the queue.
* A scheduler task enqueues ticks for loops whose scheduled check is due, sweeps expired leases, records the
  worker's own heartbeat (what the UI's Active indicator reads) and enqueues periodic maintenance.
* The transport subscription hands every inbound holder message to ``LoopEngine.on_transport``.

Wake-ups are event driven: a commit on the coordination DB (in-process) or a transport delivery sets the
wake event, so a worker with nothing to do sleeps until something happens or the poll interval elapses.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..db.coord import CoordDB
from ..goals import GoalService
from ..jobs import JobQueue
from ..transport import Subjects, Transport
from ..util import j, now_iso
from .engine import LoopEngine

logger = logging.getLogger(__name__)


@dataclass
class WorkerMetrics:
    started_at: str | None = None
    jobs_done: int = 0
    jobs_failed: int = 0
    jobs_lost: int = 0
    last_job_at: str | None = None
    last_error: str | None = None
    busy: int = 0
    recent: list[dict[str, Any]] = field(default_factory=list)

    def note(self, kind: str, detail: dict[str, Any]) -> None:
        self.recent.append({"at": now_iso(), "kind": kind, **detail})
        if len(self.recent) > 100:
            del self.recent[: len(self.recent) - 100]

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["recent"] = list(self.recent[-25:])
        return d


class DiscoveryWorker:
    def __init__(self, db: CoordDB, engine: LoopEngine, jobs: JobQueue, goals: GoalService, transport: Transport | None, *,
                 concurrency: int = 2, lease_seconds: float = 120.0, heartbeat_seconds: float = 10.0, poll_seconds: float = 1.0,
                 maintenance_interval: float = 900.0, worker_id: str | None = None, role: str = "discovery", version: str = "0.1.0",
                 kinds: list[str] | None = None) -> None:
        self.db, self.engine, self.jobs, self.goals, self.transport = db, engine, jobs, goals, transport
        self.concurrency = max(1, int(concurrency))
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_seconds = poll_seconds
        self.maintenance_interval = maintenance_interval
        self.worker_id = worker_id or f"w-{socket.gethostname()[:12]}-{uuid.uuid4().hex[:6]}"
        self.engine.worker_id = self.worker_id
        self.role = role
        self.version = version
        self.kinds = kinds
        self.metrics = WorkerMetrics()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._running = False
        self._subs: list[Any] = []
        self._last_maint = time.monotonic()

    @property
    def running(self) -> bool:
        return self._running

    def wake(self) -> None:
        self._wake.set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self.metrics.started_at = now_iso()
        await self._register()
        n = await self.jobs.requeue_expired()
        if n:
            logger.info("requeued %d expired leases from a previous run", n)
        self.db.add_waker(self._wake)
        if self.transport is not None:
            sub = await self.transport.subscribe(Subjects.all_core_inbound(), consumer="core", handler=self._on_transport, ack_wait=60.0)
            self._subs.append(sub)
        self._tasks = [asyncio.create_task(self._loop(i), name=f"mycelic-worker-{i}") for i in range(self.concurrency)]
        self._tasks.append(asyncio.create_task(self._scheduler(), name="mycelic-scheduler"))
        logger.info("discovery worker %s started (concurrency %d)", self.worker_id, self.concurrency)

    async def stop(self, timeout: float = 30.0) -> None:
        if not self._running:
            return
        self._stop.set()
        self._wake.set()
        done, pending = await asyncio.wait(self._tasks, timeout=timeout) if self._tasks else (set(), set())
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks = []
        for s in self._subs:
            try:
                await s.close()
            except Exception:  # pragma: no cover
                pass
        self._subs = []
        self.db.remove_waker(self._wake)
        self._running = False
        async with self.db.tx() as c:
            c.execute("UPDATE workers SET busy=0, stats=? , last_heartbeat_at=? WHERE worker_id=?", (j({**self.metrics.as_dict(), "stopped": True}), now_iso(), self.worker_id))

    async def _register(self) -> None:
        async with self.db.tx() as c:
            c.execute("INSERT INTO workers(worker_id, role, hostname, pid, version, started_at, last_heartbeat_at, busy, stats) VALUES (?, ?, ?, ?, ?, ?, ?, 0, '{}') "
                      "ON CONFLICT(worker_id) DO UPDATE SET started_at=excluded.started_at, last_heartbeat_at=excluded.last_heartbeat_at",
                      (self.worker_id, self.role, socket.gethostname(), os.getpid(), self.version, now_iso(), now_iso()))

    async def heartbeat(self) -> None:
        async with self.db.tx() as c:
            c.execute("UPDATE workers SET last_heartbeat_at=?, busy=?, stats=? WHERE worker_id=?", (now_iso(), self.metrics.busy, j(self.metrics.as_dict()), self.worker_id))

    # ------------------------------------------------------------------ transport intake
    async def _on_transport(self, env: Any) -> None:
        try:
            await self.engine.on_transport(env)
        finally:
            self._wake.set()

    # ------------------------------------------------------------------ job processing
    async def run_once(self) -> bool:
        """Lease and process one job in the current task (tests, CLI). Returns False when nothing was runnable."""
        job = await self.jobs.lease(self.worker_id, self.lease_seconds, kinds=self.kinds)
        if job is None:
            return False
        await self._process(job)
        return True

    async def drain(self, *, max_jobs: int | None = None, tick_due: bool = True) -> int:
        """Process until the queue has nothing runnable (tests and the `scenario` command)."""
        n = 0
        await self.jobs.requeue_expired()
        while max_jobs is None or n < max_jobs:
            if tick_due:
                for gid in self.goals.due_ticks():
                    await self.goals.enqueue_tick(gid, reason="scheduled check", priority=5)
            if not await self.run_once():
                nxt = self.jobs.next_available_at()
                if nxt is None or nxt > now_iso():
                    break
                await asyncio.sleep(0.05)
                continue
            n += 1
        return n

    async def _process(self, job: Any) -> None:
        self.metrics.busy += 1
        hb = asyncio.create_task(self._lease_heartbeat(job.job_id))
        t0 = time.perf_counter()
        try:
            result = await self.engine.handle(job)
            status = await self.jobs.complete(job.job_id, self.worker_id, result if isinstance(result, dict) else {"result": result})
            if status == "lost":
                self.metrics.jobs_lost += 1
                self.metrics.note("lost_lease", {"job_id": job.job_id, "kind": job.kind})
            else:
                self.metrics.jobs_done += 1
                self.metrics.note("done", {"job_id": job.job_id, "kind": job.kind, "ref_id": job.ref_id, "seconds": round(time.perf_counter() - t0, 2)})
            self.metrics.last_job_at = now_iso()
        except asyncio.CancelledError:
            await self.jobs.fail(job.job_id, self.worker_id, "cancelled during shutdown", backoff_base=1.0)
            raise
        except Exception as exc:
            status = await self.jobs.fail(job.job_id, self.worker_id, f"{type(exc).__name__}: {exc}")
            self.metrics.jobs_failed += 1
            self.metrics.last_error = f"{job.kind} #{job.job_id}: {exc}"[:500]
            self.metrics.note("failed", {"job_id": job.job_id, "kind": job.kind, "error": str(exc)[:200], "status": status})
            logger.exception("job %s (%s) failed -> %s", job.job_id, job.kind, status)
            if job.kind == "loop.tick" and job.ref_id and status == "dead":
                await self.goals.set_loop_state(job.ref_id, "failed", f"tick failed repeatedly: {exc}"[:400], worker_id=self.worker_id)
            try:
                await self.db.emit(job.tenant_id or "", "job.progress", ref_type="job", ref_id=str(job.job_id), payload={"kind": job.kind, "status": status, "error": str(exc)[:200]}, audience={"roles": ["org_admin"]})
            except Exception:  # pragma: no cover
                pass
        finally:
            hb.cancel()
            self.metrics.busy -= 1

    async def _lease_heartbeat(self, job_id: int) -> None:
        while True:
            try:
                await asyncio.sleep(max(1.0, self.lease_seconds / 3))
                await self.jobs.heartbeat(job_id, self.worker_id, self.lease_seconds)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # pragma: no cover
                logger.warning("lease heartbeat failed: %s", exc)

    async def _loop(self, idx: int) -> None:
        while not self._stop.is_set():
            try:
                if not await self.run_once():
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let a loop die
                logger.exception("worker loop %d error: %s", idx, exc)
                await asyncio.sleep(1.0)

    async def _scheduler(self) -> None:
        last_sweep = 0.0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                for gid in self.goals.due_ticks():
                    await self.goals.enqueue_tick(gid, reason="scheduled check", priority=5)
                if now - last_sweep >= 60.0:
                    last_sweep = now
                    n = await self.jobs.requeue_expired()
                    if n:
                        logger.warning("requeued %d expired leases", n)
                if self.maintenance_interval > 0 and now - self._last_maint >= self.maintenance_interval:
                    self._last_maint = now
                    await self.jobs.enqueue("maintenance", idempotency_key=f"maintenance:{int(time.time() // self.maintenance_interval)}", priority=8, max_attempts=2)
                await self.heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.warning("scheduler error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.heartbeat_seconds)
            except asyncio.TimeoutError:
                pass
