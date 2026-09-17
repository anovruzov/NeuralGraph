"""Background worker: runs Qwen continuously and in parallel over the durable job queue.

* ``concurrency`` asyncio tasks each lease work from :class:`ChatMemoryStore` (``lease_job``); the SQL
  lease guarantees per-chat message order while different chats are processed in parallel. Each
  extraction pass issues two LLM calls concurrently (memories + relations) plus one reconcile call per
  ambiguous candidate, so effective LLM parallelism is higher than ``concurrency``.
* A lease is renewed by a heartbeat while a batch is in flight; a worker that dies leaves an expired
  lease that :meth:`ChatMemoryStore.requeue_expired_leases` returns to the queue on the next start or
  sweep.
* Failures back off exponentially and dead-letter after ``max_attempts``; the message keeps its raw
  text and status ``failed`` so nothing is lost.
* Periodic maintenance (``maintain`` jobs): embed memories whose embedding is missing, sweep expired
  leases, prune old done jobs, and merge near-duplicate active memories that slipped through when two
  chats produced the same fact at the same time.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .extraction import MemoryExtractor, _cos
from .llm import LLMClient
from .models import Job, iso, utcnow
from .store import ChatMemoryStore, ExtractionPlan, LostLease

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    concurrency: int = 2               # chats processed in parallel (each pass = 2+ concurrent LLM calls)
    batch_size: int = 6                # consecutive messages of one chat per extraction pass
    poll_interval: float = 0.25        # seconds between queue checks while work may be pending
    idle_poll_interval: float = 2.0    # seconds between checks when the queue was empty
    lease_seconds: float = 300.0
    heartbeat_seconds: float = 30.0
    backoff_base: float = 5.0
    backoff_max: float = 600.0
    maintenance_interval: float = 900.0   # seconds; 0 disables periodic maintenance
    sweep_interval: float = 60.0          # expired-lease sweep
    embed_missing_limit: int = 50
    merge_duplicates_limit: int = 200
    merge_cosine: float = 0.96
    prune_done_after_days: float = 7.0


@dataclass
class WorkerMetrics:
    started_at: str | None = None
    batches: int = 0
    messages_processed: int = 0
    messages_skipped: int = 0
    memories_added: int = 0
    memories_superseded: int = 0
    duplicates: int = 0
    relations: int = 0
    links: int = 0
    failures: int = 0
    dead_jobs: int = 0
    maintenance_runs: int = 0
    last_error: str | None = None
    last_batch_at: str | None = None
    last_batch_seconds: float = 0.0
    busy_workers: int = 0
    recent: list[dict[str, Any]] = field(default_factory=list)   # ring buffer of recent decisions

    def note(self, kind: str, detail: dict[str, Any]) -> None:
        self.recent.append({"at": iso(utcnow()), "kind": kind, **detail})
        if len(self.recent) > 200:
            del self.recent[: len(self.recent) - 200]

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["recent"] = list(self.recent[-50:])
        return d


class MemoryWorker:
    def __init__(self, store: ChatMemoryStore, llm: LLMClient, extractor: MemoryExtractor,
                 config: WorkerConfig | None = None) -> None:
        self.store = store
        self.llm = llm
        self.extractor = extractor
        self.config = config or WorkerConfig()
        self.metrics = WorkerMetrics()
        self.worker_id = f"w-{uuid.uuid4().hex[:8]}"
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._busy = 0
        self._last_sweep = 0.0
        self._last_maint = time.monotonic()
        self._running = False

    # ------------------------------------------------------------------ lifecycle
    @property
    def running(self) -> bool:
        return self._running

    def wake(self) -> None:
        """Signal that new work was enqueued (called by the service after ``add_message``)."""
        self._wake.set()

    async def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self.metrics.started_at = iso(utcnow())
        n = await self.store.requeue_expired_leases()
        if n:
            logger.info("requeued %d expired leases from a previous run", n)
        self._tasks = [asyncio.create_task(self._loop(i), name=f"chat-memory-worker-{i}")
                       for i in range(max(1, self.config.concurrency))]

    async def stop(self, timeout: float = 30.0) -> None:
        """Stop after in-flight batches finish (or cancel them after ``timeout``)."""
        if not self._running:
            return
        self._stop.set()
        self._wake.set()
        done, pending = await asyncio.wait(self._tasks, timeout=timeout)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks = []
        self._running = False

    async def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until the queue is empty and no batch is in flight. Returns False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            pending = await self.store.pending_jobs()
            if pending == 0 and self._busy == 0:
                return True
            if not self._running:
                return pending == 0
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            self._wake.set()
            try:
                await asyncio.wait_for(self._idle.wait(), timeout=min(0.2, remaining) if remaining is not None else 0.2)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> bool:
        """Lease and process one batch synchronously (no background tasks). Returns False if the queue was empty."""
        jobs = await self.store.lease_job(self.worker_id, self.config.lease_seconds, batch_size=self.config.batch_size)
        if not jobs:
            return False
        await self._process(jobs)
        return True

    async def drain(self, max_batches: int | None = None) -> int:
        """Process until the queue is empty (used by CLI batch ingestion and tests). Returns batches processed."""
        n = 0
        while max_batches is None or n < max_batches:
            if not await self.run_once():
                # nothing runnable now: maybe backing off; check whether anything is still queued
                if await self.store.pending_jobs() == 0:
                    break
                nxt = await self.store.next_available_at()
                await asyncio.sleep(min(1.0, self.config.poll_interval) if nxt else self.config.poll_interval)
                continue
            n += 1
        return n

    # ------------------------------------------------------------------ main loop
    async def _loop(self, idx: int) -> None:
        cfg = self.config
        while not self._stop.is_set():
            try:
                await self._periodic()
                jobs = await self.store.lease_job(self.worker_id, cfg.lease_seconds, batch_size=cfg.batch_size)
                if not jobs:
                    self._wake.clear()
                    pending = await self.store.pending_jobs()
                    wait = cfg.poll_interval if pending else cfg.idle_poll_interval
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=wait)
                    except asyncio.TimeoutError:
                        pass
                    continue
                self._busy += 1
                self._idle.clear()
                self.metrics.busy_workers = self._busy
                try:
                    await self._process(jobs)
                finally:
                    self._busy -= 1
                    self.metrics.busy_workers = self._busy
                    if self._busy == 0:
                        self._idle.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let a worker die
                logger.exception("worker %d loop error: %s", idx, exc)
                self.metrics.last_error = str(exc)[:500]
                await asyncio.sleep(1.0)

    async def _periodic(self) -> None:
        cfg = self.config
        now = time.monotonic()
        if now - self._last_sweep >= cfg.sweep_interval:
            self._last_sweep = now
            n = await self.store.requeue_expired_leases()
            if n:
                logger.warning("requeued %d expired leases", n)
        if cfg.maintenance_interval > 0 and now - self._last_maint >= cfg.maintenance_interval:
            self._last_maint = now
            bucket = int(time.time() // cfg.maintenance_interval)
            await self.store.enqueue_job("maintain", dedupe_key=f"maintain:{bucket}", max_attempts=2)

    # ------------------------------------------------------------------ processing
    async def _process(self, jobs: list[Job]) -> None:
        if jobs[0].kind == "maintain":
            await self._run_maintenance(jobs)
            return
        cfg = self.config
        t0 = time.perf_counter()
        ids = [j.job_id for j in jobs]
        message_ids = [j.ref_id for j in jobs if j.ref_id]
        messages = await self.store.get_messages_by_ids(message_ids)
        messages = [m for m in messages if m.status in ("pending", "processing", "failed")]
        if not messages:
            await self.store.complete_jobs(ids)
            return
        await self.store.set_message_status([m.message_id for m in messages], "processing")
        hb = asyncio.create_task(self._heartbeat(ids))
        try:
            plan = await self.extractor.build_plan(messages, ids, attempt=max(1, max(j.attempts for j in jobs)))
            counts = await self.store.apply_plan(plan, worker_id=self.worker_id)
        except asyncio.CancelledError:
            await self.store.set_message_status([m.message_id for m in messages], "pending")
            await self.store.release_jobs(ids)
            raise
        except LostLease as exc:
            hb.cancel()
            # our lease expired and another worker took the batch: their commit wins, ours is discarded
            self.metrics.note("lost_lease", {"chat_id": jobs[0].chat_id, "detail": str(exc)})
            logger.warning("discarding result for chat %s: %s", jobs[0].chat_id, exc)
            return
        except Exception as exc:
            hb.cancel()
            attempts = max(j.attempts for j in jobs)
            delay = min(cfg.backoff_max, cfg.backoff_base * (2 ** max(0, attempts - 1))) * (0.8 + 0.4 * random.random())
            result = await self.store.fail_jobs(ids, f"{type(exc).__name__}: {exc}", delay)
            dead = [j for j, s in result.items() if s == "dead"]
            requeued = [m.message_id for m, j in zip(messages, jobs) if result.get(j.job_id) == "queued"]
            if requeued:
                await self.store.set_message_status(requeued, "pending")
            self.metrics.failures += 1
            self.metrics.dead_jobs += len(dead)
            self.metrics.last_error = f"{type(exc).__name__}: {exc}"[:500]
            self.metrics.note("failure", {"chat_id": jobs[0].chat_id, "error": str(exc)[:200], "dead": len(dead), "retry_in": round(delay, 1)})
            logger.warning("batch failed (chat %s, %d msgs): %s; %s", jobs[0].chat_id, len(messages), exc,
                           "dead-lettered" if dead else f"retry in {delay:.0f}s")
            return
        finally:
            hb.cancel()
        dt = time.perf_counter() - t0
        self.metrics.batches += 1
        self.metrics.messages_processed += len(plan.processed_message_ids)
        self.metrics.messages_skipped += len(plan.skipped_message_ids)
        self.metrics.memories_added += counts["memories_added"]
        self.metrics.memories_superseded += counts["memories_superseded"]
        self.metrics.duplicates += counts["duplicates"]
        self.metrics.relations += counts["relations"]
        self.metrics.links += counts["links"]
        self.metrics.last_batch_at = iso(utcnow())
        self.metrics.last_batch_seconds = dt
        for mem, _, _ in plan.new_memories:
            self.metrics.note("memory", {"chat_id": plan.chat_id, "memory_id": mem.memory_id, "text": mem.text[:160],
                                         "kind": mem.kind, "subject": mem.subject_name, "decision": mem.metadata.get("decision", "ADD")})
        if plan.skipped_message_ids and not plan.new_memories:
            self.metrics.note("skip", {"chat_id": plan.chat_id, "messages": len(plan.skipped_message_ids)})
        logger.info("chat %s: %d msgs -> %s in %.1fs", plan.chat_id, len(messages), plan.summary(), dt)

    async def _heartbeat(self, job_ids: list[int]) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.heartbeat_seconds)
                await self.store.heartbeat(job_ids, self.config.lease_seconds)
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------ maintenance
    async def _run_maintenance(self, jobs: list[Job]) -> None:
        cfg = self.config
        ids = [j.job_id for j in jobs]
        try:
            report = await self.maintain()
            await self.store.complete_jobs(ids)
            self.metrics.maintenance_runs += 1
            self.metrics.note("maintenance", report)
        except Exception as exc:
            await self.store.fail_jobs(ids, f"maintenance: {exc}", cfg.backoff_base)
            self.metrics.last_error = f"maintenance: {exc}"[:500]
            logger.warning("maintenance failed: %s", exc)

    async def maintain(self) -> dict[str, Any]:
        """One maintenance pass (also callable directly). Returns a small report."""
        cfg = self.config
        report: dict[str, Any] = {"embedded": 0, "merged": 0, "requeued": 0, "pruned": 0}
        report["requeued"] = await self.store.requeue_expired_leases()
        missing = await self.store.memories_missing_embedding(cfg.embed_missing_limit)
        if missing:
            vecs = await self.llm.embed_many([m.text for m in missing])
            for m, v in zip(missing, vecs):
                if v:
                    await self.store.update_memory(m.memory_id, embedding=v)
                    report["embedded"] += 1
        # merge exact/near duplicates among recent active memories of the same subject
        rows = await self.store.active_embedding_rows()
        by_subject: dict[str, list[tuple[str, list[float]]]] = {}
        for mid, subject, _, _, _, _, emb in rows[-cfg.merge_duplicates_limit * 4:]:
            by_subject.setdefault(subject, []).append((mid, emb))
        merged = 0
        for subject, items in by_subject.items():
            items = items[-cfg.merge_duplicates_limit:]
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    if merged >= cfg.merge_duplicates_limit:
                        break
                    a, b = items[i], items[j]
                    if _cos(a[1], b[1]) >= cfg.merge_cosine:
                        older, newer = await self.store.get_memory(a[0]), await self.store.get_memory(b[0])
                        if not older or not newer or older.status != "active" or newer.status != "active":
                            continue
                        if (older.observed_at or "") > (newer.observed_at or ""):
                            older, newer = newer, older
                        await self.store.supersede(older.memory_id, newer.memory_id, "supersedes")
                        await self.store.add_memory_sources(newer.memory_id, (await self.store.get_memory(older.memory_id, with_sources=True)).source_message_ids)
                        merged += 1
        report["merged"] = merged
        report["pruned"] = await self.store.prune_jobs(cfg.prune_done_after_days)
        return report
