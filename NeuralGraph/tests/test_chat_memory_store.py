"""Store-level tests for NeuralGraph.chat_memory: schema, idempotent ingestion, the durable job queue
(per-chat ordering, cross-chat parallelism, leases, backoff, dead-letter, restart recovery), atomic plan
application with lease fencing, memories/entities/relations/links, and keyword search.

Every test writes to a temporary SQLite file (never the default path). No LLM is involved here.

Run: .venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_store -v
"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from NeuralGraph.chat_memory.models import Memory, iso, new_id, now_iso, utcnow
from NeuralGraph.chat_memory.store import ChatMemoryStore, ExtractionPlan, LostLease
from NeuralGraph.chat_memory.textutil import content_hash


def make_memory(text: str, subject: str = "ali", chat_id: str = "c1", observed_at: str = "2026-01-01T00:00:00+00:00",
                embedding: list[float] | None = None, kind: str = "fact", importance: float = 0.7) -> Memory:
    now = now_iso()
    return Memory(memory_id=new_id("mem"), text=text, kind=kind, subject=subject, subject_name=subject.title(), speaker=subject.title(),
                  chat_id=chat_id, importance=importance, confidence=0.9, event_time=None, event_time_precision="none",
                  observed_at=observed_at, created_at=now, updated_at=now, text_hash=content_hash(text.lower()),
                  embedding=embedding or [1.0, 0.0, 0.0])


class StoreTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "chat_memory_test.db"
        self.store = ChatMemoryStore(self.db_path)

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmp.cleanup()


class SchemaAndMessagesTests(StoreTestCase):
    async def test_schema_created_with_fts_and_wal(self) -> None:
        self.assertTrue(self.store.has_fts)
        conn = sqlite3.connect(self.db_path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        for t in ("chats", "messages", "memories", "memory_sources", "entities", "entity_aliases", "memory_entities",
                  "relations", "memory_links", "jobs", "audit_log", "meta"):
            self.assertIn(t, tables)
        self.assertEqual(await self.store.get_meta("schema_version"), "1")

    async def test_add_message_assigns_sequence_and_enqueues(self) -> None:
        m1, c1 = await self.store.add_message("chat", "Ali", "first", sent_at="2026-01-01T10:00:00+00:00")
        m2, c2 = await self.store.add_message("chat", "Mel", "second")
        self.assertTrue(c1 and c2)
        self.assertEqual((m1.seq, m2.seq), (0, 1))
        self.assertEqual(m1.status, "pending")
        self.assertEqual(await self.store.job_counts(), {"queued": 2})
        chats = await self.store.list_chats()
        self.assertEqual(chats[0]["message_count"], 2)
        self.assertEqual(sorted(chats[0]["participants"]), ["Ali", "Mel"])

    async def test_add_message_is_idempotent(self) -> None:
        m1, c1 = await self.store.add_message("chat", "Ali", "same text", sent_at="2026-01-01T10:00:00+00:00")
        m2, c2 = await self.store.add_message("chat", "Ali", "same text", sent_at="2026-01-01T10:00:00+00:00")
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(m1.message_id, m2.message_id)
        self.assertEqual(await self.store.job_counts(), {"queued": 1})
        # explicit id is honoured and also idempotent
        m3, c3 = await self.store.add_message("chat", "Ali", "with id", message_id="m-42")
        m4, c4 = await self.store.add_message("chat", "Ali", "with id", message_id="m-42")
        self.assertEqual(m3.message_id, "m-42")
        self.assertTrue(c3); self.assertFalse(c4)

    async def test_context_before_and_search_messages(self) -> None:
        for i in range(5):
            await self.store.add_message("chat", "Ali", f"message number {i} about Berlin" if i == 3 else f"message number {i}")
        ctx = await self.store.context_before("chat", 4, 2)
        self.assertEqual([m.seq for m in ctx], [2, 3])
        hits = await self.store.search_messages("berlin")
        self.assertEqual([m.seq for m, _ in hits], [3])

    async def test_persistence_across_reopen(self) -> None:
        await self.store.add_message("chat", "Ali", "durable")
        await self.store.close()
        reopened = ChatMemoryStore(self.db_path)
        try:
            msgs = await reopened.get_messages("chat")
            self.assertEqual([m.text for m in msgs], ["durable"])
            self.assertEqual(await reopened.job_counts(), {"queued": 1})
        finally:
            await reopened.close()
        self.store = ChatMemoryStore(self.db_path)  # for tearDown


class JobQueueTests(StoreTestCase):
    async def test_per_chat_ordering_and_cross_chat_parallelism(self) -> None:
        a0, _ = await self.store.add_message("A", "x", "a0")
        a1, _ = await self.store.add_message("A", "x", "a1")
        b0, _ = await self.store.add_message("B", "x", "b0")
        first = await self.store.lease_job("w1", 60, batch_size=1)
        second = await self.store.lease_job("w2", 60, batch_size=1)
        third = await self.store.lease_job("w3", 60, batch_size=1)
        self.assertEqual([j.ref_id for j in first], [a0.message_id])
        self.assertEqual([j.ref_id for j in second], [b0.message_id])   # a1 must wait for a0; B runs in parallel
        self.assertEqual(third, [])
        await self.store.complete_jobs([first[0].job_id])
        fourth = await self.store.lease_job("w1", 60, batch_size=1)
        self.assertEqual([j.ref_id for j in fourth], [a1.message_id])

    async def test_batch_lease_coalesces_contiguous_messages_only(self) -> None:
        ids = [(await self.store.add_message("A", "x", f"m{i}"))[0].message_id for i in range(4)]
        # make m2 unavailable (future) so the batch must stop at the gap
        future = iso(utcnow() + timedelta(seconds=60))
        self.store._conn.execute("UPDATE jobs SET available_at=? WHERE ref_id=?", (future, ids[2]))
        jobs = await self.store.lease_job("w", 60, batch_size=10)
        self.assertEqual([j.ref_id for j in jobs], ids[:2])
        self.assertTrue(all(j.status == "leased" and j.attempts == 1 for j in jobs))

    async def test_failure_backoff_then_dead_letter(self) -> None:
        m, _ = await self.store.add_message("A", "x", "m0", max_attempts=2)
        jobs = await self.store.lease_job("w", 60)
        result = await self.store.fail_jobs([jobs[0].job_id], "boom", backoff_seconds=30)
        self.assertEqual(result, {jobs[0].job_id: "queued"})
        self.assertEqual(await self.store.lease_job("w", 60), [], "backing-off job must not be leasable yet")
        self.store._conn.execute("UPDATE jobs SET available_at=? WHERE job_id=?", (now_iso(), jobs[0].job_id))
        jobs = await self.store.lease_job("w", 60)
        self.assertEqual(jobs[0].attempts, 2)
        result = await self.store.fail_jobs([jobs[0].job_id], "boom again", backoff_seconds=30)
        self.assertEqual(result, {jobs[0].job_id: "dead"})
        self.assertEqual((await self.store.get_message(m.message_id)).status, "failed")
        self.assertEqual(await self.store.job_counts(), {"dead": 1})
        # a dead job does not block later messages of the chat
        m1, _ = await self.store.add_message("A", "x", "m1")
        self.assertEqual([j.ref_id for j in await self.store.lease_job("w", 60)], [m1.message_id])
        # and can be retried by an operator
        self.assertEqual(await self.store.retry_dead_jobs(), 1)
        self.assertEqual((await self.store.get_message(m.message_id)).status, "pending")

    async def test_expired_lease_is_requeued(self) -> None:
        m, _ = await self.store.add_message("A", "x", "m0")
        jobs = await self.store.lease_job("w", lease_seconds=-1)   # already expired
        await self.store.set_message_status([m.message_id], "processing")
        self.assertEqual(await self.store.requeue_expired_leases(), 1)
        self.assertEqual((await self.store.get_message(m.message_id)).status, "pending")
        again = await self.store.lease_job("w2", 60)
        self.assertEqual(again[0].job_id, jobs[0].job_id)
        self.assertEqual(again[0].attempts, 2, "an expired lease counts as an attempt")

    async def test_enqueue_dedupe_key(self) -> None:
        j1 = await self.store.enqueue_job("maintain", dedupe_key="maintain:1")
        j2 = await self.store.enqueue_job("maintain", dedupe_key="maintain:1")
        self.assertIsNotNone(j1)
        self.assertIsNone(j2)
        self.assertEqual(await self.store.pending_jobs(), 1)


class PlanApplicationTests(StoreTestCase):
    async def test_apply_plan_is_atomic_and_marks_messages_and_jobs(self) -> None:
        m, _ = await self.store.add_message("c1", "Ali", "I have a cat called Luna", sent_at="2026-01-01T10:00:00+00:00")
        jobs = await self.store.lease_job("w", 60)
        plan = ExtractionPlan("c1", [j.job_id for j in jobs])
        plan.entities.append({"entity_id": "ali", "name": "Ali", "type": "person", "seen_at": m.sent_at, "aliases": ["ali n"]})
        plan.entities.append({"entity_id": "luna", "name": "Luna", "type": "pet"})
        mem = make_memory("Ali has a cat called Luna.")
        plan.new_memories.append((mem, [m.message_id], ["ali", "luna"]))
        plan.relations.append(("ali", "has_pet", "luna", 0.9, mem.memory_id, m.message_id))
        plan.processed_message_ids.append(m.message_id)
        counts = await self.store.apply_plan(plan, worker_id="w")
        self.assertEqual(counts["memories_added"], 1)
        self.assertEqual(counts["relations"], 1)
        self.assertEqual((await self.store.get_message(m.message_id)).status, "processed")
        self.assertEqual(await self.store.job_counts(), {"done": 1})
        stored = await self.store.get_memory(mem.memory_id, with_sources=True)
        self.assertEqual(stored.source_message_ids, [m.message_id])
        self.assertEqual(stored.entity_ids, ["ali", "luna"])
        self.assertEqual(await self.store.resolve_alias("Ali N"), "ali")
        self.assertEqual(await self.store.neighbors(["ali"]), {"ali": [("luna", "has_pet", 0.9)]})

    async def test_apply_plan_is_fenced_by_lease(self) -> None:
        m, _ = await self.store.add_message("c1", "Ali", "hello world facts")
        jobs = await self.store.lease_job("zombie", lease_seconds=-1)
        await self.store.requeue_expired_leases()
        await self.store.lease_job("fresh", 60)          # someone else owns it now
        plan = ExtractionPlan("c1", [jobs[0].job_id])
        plan.new_memories.append((make_memory("Ali says hello world."), [m.message_id], ["ali"]))
        with self.assertRaises(LostLease):
            await self.store.apply_plan(plan, worker_id="zombie")
        self.assertEqual(await self.store.list_memories(), [], "fenced commit must write nothing")

    async def test_apply_plan_rolls_back_on_error(self) -> None:
        m, _ = await self.store.add_message("c1", "Ali", "x")
        plan = ExtractionPlan("c1")
        good = make_memory("Ali likes tea.")
        plan.new_memories.append((good, [m.message_id], ["ali"]))
        plan.new_memories.append((good, [m.message_id], ["ali"]))   # duplicate primary key -> IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            await self.store.apply_plan(plan)
        self.assertEqual(await self.store.list_memories(), [])

    async def test_supersede_and_history(self) -> None:
        old = make_memory("Ali lives in Berlin.", observed_at="2026-01-01T00:00:00+00:00")
        new = make_memory("Ali lives in Tokyo.", observed_at="2026-06-01T00:00:00+00:00")
        await self.store.insert_memory(old, entity_ids=["ali"])
        await self.store.upsert_relation("ali", "lives_in", "berlin", chat_id="c1", memory_id=old.memory_id)
        await self.store.insert_memory(new, entity_ids=["ali"])
        await self.store.supersede(old.memory_id, new.memory_id, "contradicts")
        self.assertEqual([m.memory_id for m in await self.store.list_memories()], [new.memory_id])
        stale = await self.store.get_memory(old.memory_id)
        self.assertEqual((stale.status, stale.superseded_by), ("superseded", new.memory_id))
        self.assertEqual([m.memory_id for m in await self.store.history(new.memory_id)], [old.memory_id])
        self.assertEqual(await self.store.relation_counts(), {"superseded": 1}, "relations grounded only in the old memory follow it")

    async def test_functional_predicate_supersession(self) -> None:
        await self.store.upsert_relation("ali", "lives_in", "berlin", chat_id="c1")
        await self.store.upsert_relation("ali", "likes", "tea", chat_id="c1")
        await self.store.upsert_relation("ali", "likes", "coffee", chat_id="c1")
        await self.store.upsert_relation("ali", "lives_in", "tokyo", chat_id="c2")
        active = {(r.predicate, r.object_id) for r in await self.store.relations_for("ali")}
        self.assertEqual(active, {("lives_in", "tokyo"), ("likes", "tea"), ("likes", "coffee")})
        again = await self.store.upsert_relation("ali", "lives_in", "tokyo", chat_id="c3")
        self.assertEqual(again.observation_count, 2)

    async def test_keyword_candidates_and_retract(self) -> None:
        a = make_memory("Ali adopted a golden retriever named Bailey.")
        b = make_memory("Mel visited the Louvre in Paris.", subject="mel")
        await self.store.insert_memory(a); await self.store.insert_memory(b)
        hits = await self.store.keyword_candidates("Bailey retriever")
        self.assertEqual([h[0] for h in hits], [a.memory_id])
        self.assertTrue(await self.store.retract(a.memory_id, "test"))
        self.assertEqual(await self.store.keyword_candidates("Bailey retriever"), [])
        self.assertEqual((await self.store.recent_events(1))[0]["kind"], "retract")

    async def test_list_memories_filters(self) -> None:
        await self.store.insert_memory(make_memory("Ali ran a marathon.", observed_at="2026-03-01T00:00:00+00:00", kind="event"))
        await self.store.insert_memory(make_memory("Ali likes tea.", observed_at="2025-01-01T00:00:00+00:00", kind="preference"))
        await self.store.insert_memory(make_memory("Mel likes pottery.", subject="mel", chat_id="c2", kind="preference"))
        self.assertEqual(len(await self.store.list_memories(subject="Ali")), 2)
        self.assertEqual(len(await self.store.list_memories(kinds=["preference"])), 2)
        self.assertEqual(len(await self.store.list_memories(since="2026-01-01")), 2)
        self.assertEqual(len(await self.store.list_memories(chat_id="c2")), 1)
        with self.assertRaises(ValueError):
            await self.store.list_memories(order="text; DROP TABLE memories")


if __name__ == "__main__":
    unittest.main()
