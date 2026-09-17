"""Worker tests: the chat path never blocks on the LLM, chats are processed in parallel while each chat
stays ordered, failures back off and dead-letter, expired leases are recovered on restart, a zombie
worker cannot double-commit, maintenance back-fills embeddings and merges duplicates, and shutdown is
graceful. Uses FakeLLMClient with a scripted responder; no server.

Run: .venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_worker -v
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig
from NeuralGraph.chat_memory.extraction import ExtractionConfig, MemoryExtractor
from NeuralGraph.chat_memory.llm import FakeLLMClient, LLMError
from NeuralGraph.chat_memory.store import ChatMemoryStore
from NeuralGraph.chat_memory.testing import scripted_fake_llm
from NeuralGraph.chat_memory.worker import MemoryWorker, WorkerConfig


def fast_config(**worker_overrides) -> ChatMemoryConfig:
    cfg = ChatMemoryConfig()
    cfg.debounce_seconds = 0.0
    cfg.worker.poll_interval = 0.02
    cfg.worker.idle_poll_interval = 0.05
    cfg.worker.backoff_base = 0.05
    cfg.worker.backoff_max = 0.2
    cfg.worker.sweep_interval = 0.1
    cfg.worker.maintenance_interval = 0
    for k, v in worker_overrides.items():
        setattr(cfg.worker, k, v)
    return cfg


class WorkerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "worker.db"

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def make(self, llm=None, **overrides) -> ChatMemory:
        return ChatMemory(self.db_path, llm=llm or scripted_fake_llm(), config=fast_config(**overrides))


class ChatPathTests(WorkerTestCase):
    async def test_add_message_never_waits_for_the_llm(self) -> None:
        gate = asyncio.Event()

        async def blocked(prompt, **kw):
            await gate.wait()
            return "{}"
        llm = FakeLLMClient()
        llm.generate = blocked  # type: ignore[assignment]
        cm = self.make(llm=llm, concurrency=2)
        await cm.start()
        try:
            t0 = time.perf_counter()
            for i in range(20):
                await cm.add_message("chat", "Ali", f"I bought item number {i} in Berlin today.")
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 1.0, f"20 add_message calls took {elapsed:.2f}s while the LLM was blocked")
            self.assertEqual((await cm.store.message_counts()).get("pending", 0) + (await cm.store.message_counts()).get("processing", 0), 20)
            gate.set()
        finally:
            await cm.close()

    async def test_parallel_across_chats_ordered_within_chat(self) -> None:
        llm = scripted_fake_llm(latency=0.05)
        cm = self.make(llm=llm, concurrency=3, batch_size=1)
        await cm.start()
        try:
            for c in ("A", "B", "C"):
                for i in range(3):
                    await cm.add_message(c, "Ali", f"Fact {i} about chat {c}: I visited place number {i} in Berlin.", sent_at=f"2026-01-0{i + 1}T10:00:00+00:00")
            self.assertTrue(await cm.wait_until_idle(timeout=20))
            self.assertGreaterEqual(llm.max_in_flight, 3, "three chats should extract concurrently")
            # per-chat order: memories from chat A were created in message order
            for c in ("A", "B", "C"):
                mems = await cm.memories(chat_id=c, order="created_at ASC")
                idx = [int(m.text.split("Fact ")[1][0]) for m in mems if "Fact " in m.text]
                self.assertEqual(idx, sorted(idx))
            counts = await cm.store.message_counts()
            self.assertEqual(counts.get("processed"), 9)
            self.assertEqual(await cm.store.job_counts(), {"done": 9})
        finally:
            await cm.close()

    async def test_batches_coalesce_a_turn(self) -> None:
        llm = scripted_fake_llm()
        cm = self.make(llm=llm, concurrency=1, batch_size=6)
        await cm.add_message("t", "Ali", "I moved to Berlin in March.", sent_at="2026-04-01T10:00:00+00:00")
        await cm.add_message("t", "assistant", "Nice! Berlin is lovely.", role="assistant")
        await cm.add_message("t", "Ali", "And my cat Luna is 3 years old.", sent_at="2026-04-01T10:01:00+00:00")
        n = await cm.process_pending()
        self.assertEqual(n, 1, "three messages of one chat -> one extraction pass")
        self.assertEqual(llm.stats.generate_calls, 2, "memory + relation prompt, once")
        await cm.close()


class FailureTests(WorkerTestCase):
    async def test_retry_with_backoff_then_success(self) -> None:
        llm = scripted_fake_llm(fail_times=3)   # each pass issues 2 concurrent calls: 2 failed passes, then success
        cm = self.make(llm=llm, concurrency=1)
        await cm.add_message("f", "Ali", "I started a new job at Charite in Berlin.")
        await cm.start()
        try:
            self.assertTrue(await cm.wait_until_idle(timeout=20))
            self.assertEqual(cm.worker.metrics.failures, 2)
            self.assertEqual(cm.worker.metrics.batches, 1)
            self.assertEqual((await cm.store.message_counts()).get("processed"), 1)
            job = (await cm.store.list_jobs(status="done"))[0]
            self.assertEqual(job.attempts, 3)
            self.assertIn("scripted failure", job.last_error)
        finally:
            await cm.close()

    async def test_dead_letter_after_max_attempts(self) -> None:
        llm = FakeLLMClient(fail_times=100)
        cfg = fast_config(concurrency=1)
        cfg.max_attempts = 2
        cm = ChatMemory(self.db_path, llm=llm, config=cfg)
        msg = await cm.add_message("d", "Ali", "Something the model cannot process about Berlin.")
        await cm.start()
        try:
            for _ in range(200):
                if (await cm.store.job_counts()).get("dead"):
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(await cm.store.job_counts(), {"dead": 1})
            self.assertEqual((await cm.store.get_message(msg.message_id)).status, "failed")
            self.assertEqual(cm.worker.metrics.dead_jobs, 1)
            # the raw message survives and later messages of the chat still flow
            llm.fail_times = 0
            await cm.add_message("d", "Ali", "I adopted a dog called Rex in Berlin.")
            self.assertTrue(await cm.wait_until_idle(timeout=10))
            self.assertEqual((await cm.store.message_counts()).get("processed"), 1)
        finally:
            await cm.close()

    async def test_restart_recovers_expired_leases(self) -> None:
        store = ChatMemoryStore(self.db_path)
        m, _ = await store.add_message("r", "Ali", "I live in Berlin with my cat Luna.")
        jobs = await store.lease_job("crashed-worker", lease_seconds=-1)   # crashed mid-flight, lease already expired
        await store.set_message_status([m.message_id], "processing")
        await store.close()
        cm = self.make(concurrency=1)
        await cm.start()
        try:
            self.assertTrue(await cm.wait_until_idle(timeout=10))
            self.assertEqual(await cm.store.job_counts(), {"done": 1})
            self.assertEqual((await cm.store.get_message(m.message_id)).status, "processed")
            self.assertEqual((await cm.store.get_job(jobs[0].job_id)).attempts, 2)
        finally:
            await cm.close()

    async def test_zombie_worker_result_is_discarded(self) -> None:
        cm = self.make(concurrency=1)
        m = await cm.add_message("z", "Ali", "I moved to Berlin in March.")
        jobs = await cm.store.lease_job(cm.worker.worker_id, lease_seconds=-1)   # our lease has already expired
        await cm.store.requeue_expired_leases()
        other = await cm.store.lease_job("other-worker", 60)               # someone else took it
        # our (zombie) worker finishes its computation and tries to commit
        await cm.worker._process(jobs)
        self.assertEqual(cm.worker.metrics.batches, 0)
        self.assertEqual(cm.worker.metrics.recent[-1]["kind"], "lost_lease")
        self.assertEqual(await cm.memories(), [], "nothing committed by the zombie")
        self.assertEqual((await cm.store.get_job(other[0].job_id)).status, "leased")
        await cm.close()

    async def test_graceful_stop_returns_in_flight_work_to_queue(self) -> None:
        started = asyncio.Event()

        async def slow(prompt, **kw):
            started.set()
            await asyncio.sleep(30)
            return "{}"
        llm = FakeLLMClient()
        llm.generate = slow  # type: ignore[assignment]
        cm = self.make(llm=llm, concurrency=1)
        await cm.add_message("g", "Ali", "I moved to Berlin in March.")
        await cm.start()
        await asyncio.wait_for(started.wait(), 5)
        await cm.stop(timeout=0.2)   # cancels the in-flight batch
        self.assertFalse(cm.worker.running)
        self.assertEqual((await cm.store.message_counts()).get("pending"), 1)
        self.assertEqual(await cm.store.requeue_expired_leases(), 0, "lease still valid; a restart sweeps it later")
        await cm.close()


class MaintenanceTests(WorkerTestCase):
    async def test_maintenance_backfills_embeddings_and_merges_duplicates(self) -> None:
        cm = self.make(concurrency=1)
        # two near-identical active memories of the same subject (as if produced by two chats at once)
        from NeuralGraph.tests.test_chat_memory_store import make_memory
        from NeuralGraph.chat_memory.llm import fake_embedding
        a = make_memory("Ali has a cat called Luna.", observed_at="2026-01-01T00:00:00+00:00", embedding=fake_embedding("Ali has a cat called Luna."))
        b = make_memory("Ali has a cat called Luna!", observed_at="2026-02-01T00:00:00+00:00", embedding=fake_embedding("Ali has a cat called Luna!"))
        c = make_memory("Ali likes tea.", observed_at="2026-02-01T00:00:00+00:00")
        c.embedding = None   # as if the embedding server was down when it was stored
        for m in (a, b, c):
            await cm.store.insert_memory(m, entity_ids=["ali"])
        report = await cm.maintain()
        self.assertEqual(report["embedded"], 1)
        self.assertEqual(report["merged"], 1)
        active = {m.memory_id for m in await cm.memories()}
        self.assertEqual(active, {b.memory_id, c.memory_id}, "the older duplicate is superseded by the newer one")
        self.assertIsNotNone((await cm.store.get_memory(c.memory_id)).embedding)
        await cm.close()

    async def test_periodic_maintenance_job_is_enqueued_once_per_bucket(self) -> None:
        cm = self.make(concurrency=1, maintenance_interval=0.05)
        await cm.start()
        try:
            await asyncio.sleep(0.5)
            self.assertTrue(await cm.wait_until_idle(timeout=5))
            self.assertGreaterEqual(cm.worker.metrics.maintenance_runs, 1)
            self.assertEqual((await cm.store.job_counts()).get("dead", 0), 0)
        finally:
            await cm.close()


if __name__ == "__main__":
    unittest.main()
