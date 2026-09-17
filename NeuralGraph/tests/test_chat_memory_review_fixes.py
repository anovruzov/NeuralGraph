"""Regression tests for the defects found by the adversarial review of NeuralGraph.chat_memory.

Each test names the defect it pins. All deterministic; no model server.

Run: .venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_review_fixes -v
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig
from NeuralGraph.chat_memory.extraction import EntityRegistry, ExtractionConfig, MemoryExtractor, parse_when
from NeuralGraph.chat_memory.llm import FakeLLMClient, fake_embedding
from NeuralGraph.chat_memory.mcp_server import LATEST_PROTOCOL_VERSION, MCPProtocol, serve_stdio
from NeuralGraph.chat_memory.retrieval import MemoryRetriever, RetrievalConfig
from NeuralGraph.chat_memory.store import ChatMemoryStore, ExtractionPlan
from NeuralGraph.chat_memory.testing import scripted_fake_llm
from NeuralGraph.chat_memory.textutil import fts_match_string, tokenize
from NeuralGraph.chat_memory.ui.server import create_app
from NeuralGraph.tests.test_chat_memory_store import make_memory


def llm_with(memories: dict, relations: dict | None = None, reconcile: dict | None = None) -> FakeLLMClient:
    return FakeLLMClient(script=[
        ("You maintain the long-term memory", json.dumps(memories)),
        ("You are building a knowledge graph", json.dumps(relations or {"entities": [], "triples": []})),
        ("You maintain a memory store", json.dumps(reconcile or {"decision": "ADD", "target": None})),
    ])


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "fix.db"
        self.store = ChatMemoryStore(self.db_path)

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmp.cleanup()


class ExtractionGroundingTests(Base):
    async def test_hallucinated_name_listed_in_entities_is_still_rejected(self) -> None:
        m, _ = await self.store.add_message("c", "Ali", "I met someone interesting at the market yesterday.", sent_at="2026-05-02T10:00:00+00:00")
        llm = llm_with({"memories": [{"text": "Ali met Zorblax Quintrell at the market.", "kind": "event", "subject": "Ali",
                                      "importance": 0.6, "confidence": 0.9, "sources": [1],
                                      "entities": [{"name": "Zorblax Quintrell", "type": "person"}]}]})
        plan = await MemoryExtractor(self.store, llm).build_plan([m])
        self.assertEqual(plan.new_memories, [])
        self.assertIn("unknown_name", [d.get("reason") for k, _, d in plan.audit if k == "reject"])
        self.assertEqual([e["entity_id"] for e in plan.entities if "zorblax" in e["entity_id"]], [])

    async def test_relation_endpoints_must_be_grounded(self) -> None:
        m, _ = await self.store.add_message("c", "Ali", "I work at Charite as a nurse.", sent_at="2026-05-02T10:00:00+00:00")
        rel = {"entities": [{"name": "Charite", "type": "org"}, {"name": "Globex Corp", "type": "org"}],
               "triples": [{"s": "I", "r": "works_at", "o": "Charite", "source": 1, "confidence": 0.9},
                           {"s": "I", "r": "friend_of", "o": "Bob Zimmer", "source": 1, "confidence": 0.9}]}
        plan = await MemoryExtractor(self.store, llm_with({"memories": []}, rel)).build_plan([m])
        rels = {(s, p, o) for s, p, o, *_ in plan.relations}
        self.assertEqual(rels, {("ali", "works_at", "charite")})
        self.assertNotIn("globex corp", {e["entity_id"] for e in plan.entities})

    async def test_short_identity_facts_pass_grounding(self) -> None:
        m, _ = await self.store.add_message("c", "Ali", "I'm 34 and I have 2 kids.", sent_at="2026-05-02T10:00:00+00:00")
        llm = llm_with({"memories": [
            {"text": "Ali is 34 years old.", "kind": "identity", "subject": "Ali", "importance": 0.9, "confidence": 1.0, "sources": [1]},
            {"text": "Ali has 2 kids.", "kind": "fact", "subject": "Ali", "importance": 0.9, "confidence": 1.0, "sources": [1]}]})
        plan = await MemoryExtractor(self.store, llm).build_plan([m])
        self.assertEqual(sorted(x.text for x, _, _ in plan.new_memories), ["Ali has 2 kids.", "Ali is 34 years old."])

    async def test_longer_name_never_collapses_onto_speaker(self) -> None:
        r = EntityRegistry()
        r.add("alex", "Alex", "person", 5)
        self.assertEqual(r.resolve("Alex Jones", etype="person"), "alex jones")
        r.add("melanie carter", "Melanie Carter", "person", 5)
        self.assertEqual(r.resolve("Melanie"), "melanie carter")

    async def test_assistant_subject_is_rejected_not_reattributed(self) -> None:
        m, _ = await self.store.add_message("c", "Ali", "You're really helpful and patient, thank you so much for the detailed answer.", sent_at="2026-05-02T10:00:00+00:00")
        llm = llm_with({"memories": [{"text": "The assistant is helpful and patient.", "kind": "opinion", "subject": "assistant",
                                      "importance": 0.6, "confidence": 0.9, "sources": [1]}]})
        plan = await MemoryExtractor(self.store, llm).build_plan([m])
        self.assertEqual(plan.new_memories, [])
        self.assertIn("assistant_subject", [d.get("reason") for k, _, d in plan.audit if k == "reject"])

    async def test_missing_sources_default_to_a_human_message(self) -> None:
        m1, _ = await self.store.add_message("c", "Ali", "I adopted a dog called Rex last week.", sent_at="2026-05-02T10:00:00+00:00")
        m2, _ = await self.store.add_message("c", "assistant", "Congratulations on adopting Rex!", role="assistant", sent_at="2026-05-02T10:00:10+00:00")
        llm = llm_with({"memories": [{"text": "Ali adopted a dog called Rex.", "kind": "event", "subject": "Ali", "importance": 0.7, "confidence": 0.9}]})
        plan = await MemoryExtractor(self.store, llm).build_plan([m1, m2])
        self.assertEqual(len(plan.new_memories), 1)
        self.assertEqual(plan.new_memories[0][1], [m1.message_id])

    async def test_backslash_in_subject_does_not_crash(self) -> None:
        m, _ = await self.store.add_message("c", "Ali", "I love hiking in the Alps every summer.", sent_at="2026-05-02T10:00:00+00:00")
        llm = llm_with({"memories": [{"text": "I love hiking in the Alps.", "kind": "preference", "subject": "Ali\\u00e9", "importance": 0.7, "confidence": 0.9, "sources": [1]}]})
        plan = await MemoryExtractor(self.store, llm).build_plan([m])   # must not raise re.error
        self.assertLessEqual(len(plan.new_memories), 1)

    def test_year_relative_phrases(self) -> None:
        self.assertEqual(parse_when("last year", "2026-09-17T10:00:00+00:00"), ("2025", "year"))
        self.assertEqual(parse_when("this year", "2026-09-17T10:00:00+00:00"), ("2026", "year"))


class ReconcileTests(Base):
    async def seed_cat(self) -> None:
        mem = make_memory("Ali has a cat called Luna.", observed_at="2026-03-01T00:00:00+00:00", embedding=fake_embedding("Ali has a cat called Luna."), kind="fact")
        mem.event_time, mem.event_time_precision = "2026-02", "month"
        m0, _ = await self.store.add_message("c0", "Ali", "I have a cat called Luna.", sent_at="2026-03-01T00:00:00+00:00")
        await self.store.insert_memory(mem, source_message_ids=[m0.message_id], entity_ids=["ali", "luna"])
        self.cat_id = mem.memory_id

    async def test_update_keeps_target_metadata_and_reembeds_merged_text(self) -> None:
        await self.seed_cat()
        m, _ = await self.store.add_message("c1", "Ali", "Luna is a grey British shorthair.", sent_at="2026-05-01T00:00:00+00:00")
        llm = llm_with({"memories": [{"text": "Ali's cat Luna is a grey British shorthair.", "kind": "other", "subject": "Ali", "importance": 0.5, "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]}]},
                       reconcile={"decision": "UPDATE", "target": "A", "text": "Ali has a grey British shorthair cat called Luna."})
        plan = await MemoryExtractor(self.store, llm, ExtractionConfig(reconcile_cosine=0.2)).build_plan([m])
        mem, sources, ents = plan.new_memories[0]
        self.assertEqual(mem.text, "Ali has a grey British shorthair cat called Luna.")
        self.assertEqual((mem.event_time, mem.kind, mem.importance), ("2026-02", "fact", 0.7))
        self.assertEqual(len(sources), 2, "sources of the old memory are carried over")
        self.assertIn("luna", ents)
        self.assertEqual(mem.embedding, fake_embedding(mem.text), "embedding is of the merged text")

    async def test_two_candidates_superseding_one_target_chain(self) -> None:
        await self.seed_cat()
        m, _ = await self.store.add_message("c1", "Ali", "Actually Luna is 4, and she is a British shorthair.", sent_at="2026-05-01T00:00:00+00:00")
        llm = llm_with({"memories": [
            {"text": "Ali's cat Luna is 4 years old.", "kind": "fact", "subject": "Ali", "importance": 0.7, "confidence": 0.9, "sources": [1]},
            {"text": "Ali's cat Luna is a British shorthair.", "kind": "fact", "subject": "Ali", "importance": 0.7, "confidence": 0.9, "sources": [1]}]},
            reconcile={"decision": "UPDATE", "target": "A", "text": None})
        plan = await MemoryExtractor(self.store, llm, ExtractionConfig(reconcile_cosine=0.2, dedupe_cosine=0.99)).build_plan([m])
        await self.store.apply_plan(plan)
        active = await self.store.list_memories(subject="ali")
        self.assertEqual(len(active), 1, "only the newest replacement stays active")
        self.assertEqual(len(await self.store.history(active[0].memory_id)), 2)

    async def test_mixed_timezone_offsets_compare_correctly(self) -> None:
        from NeuralGraph.chat_memory.extraction import _older
        self.assertFalse(_older("2026-05-01T12:00:00+02:00", "2026-05-01T09:00:00+00:00"))   # 10:00Z vs 09:00Z: newer
        self.assertTrue(_older("2026-05-01T08:00:00+02:00", "2026-05-01T09:00:00+00:00"))    # 06:00Z vs 09:00Z: older


class QueueTests(Base):
    async def test_fail_and_release_are_fenced(self) -> None:
        await self.store.add_message("A", "x", "hello world facts", max_attempts=2)
        zombie = await self.store.lease_job("zombie", lease_seconds=-1)
        await self.store.requeue_expired_leases()
        live = await self.store.lease_job("live", 60)
        self.assertEqual(await self.store.fail_jobs([zombie[0].job_id], "boom", 0, worker_id="zombie"), {zombie[0].job_id: "lost"})
        self.assertEqual((await self.store.get_job(live[0].job_id)).status, "leased", "the live worker's lease is untouched")
        await self.store.release_jobs([zombie[0].job_id], worker_id="zombie")
        self.assertEqual((await self.store.get_job(live[0].job_id)).status, "leased")

    async def test_pull_forward_debounce_batches_turn_and_reply(self) -> None:
        m1, _ = await self.store.add_message("t", "Ali", "I moved to Berlin.", debounce_seconds=5)
        m2, _ = await self.store.add_message("t", "assistant", "Nice!", role="assistant", debounce_seconds=5)
        j1, j2 = (await self.store.get_job(1)), (await self.store.get_job(2))
        self.assertEqual(j1.available_at, j2.available_at, "the earlier job waits for the reply")

    async def test_drain_recovers_a_lease_left_by_a_crashed_process(self) -> None:
        await self.store.add_message("r", "Ali", "I live in Berlin with my cat Luna.")
        await self.store.lease_job("crashed", lease_seconds=-1)
        await self.store.close()
        cfg = ChatMemoryConfig(); cfg.debounce_seconds = 0; cfg.worker.poll_interval = 0.01
        cm = ChatMemory(self.db_path, llm=scripted_fake_llm(), config=cfg)
        n = await asyncio.wait_for(cm.process_pending(), timeout=10)
        self.assertEqual(n, 1)
        self.assertEqual(await cm.store.job_counts(), {"done": 1})
        await cm.close()
        self.store = ChatMemoryStore(self.db_path)

    async def test_supersede_race_keeps_newest_head(self) -> None:
        t = make_memory("Ali lives in Berlin.", observed_at="2026-01-01T00:00:00+00:00")
        await self.store.insert_memory(t, entity_ids=["ali"])
        # plan A (computed first, committed first): newer fact
        a = make_memory("Ali moved to Paris.", observed_at="2026-06-01T00:00:00+00:00")
        pa = ExtractionPlan("A"); pa.new_memories.append((a, [], ["ali"])); pa.supersedes.append((t.memory_id, a.memory_id, "contradicts"))
        # plan B (computed concurrently against the same snapshot, committed second): older refinement
        b = make_memory("Ali lives in Berlin, Kreuzberg.", observed_at="2026-03-01T00:00:00+00:00")
        pb = ExtractionPlan("B"); pb.new_memories.append((b, [], ["ali"])); pb.supersedes.append((t.memory_id, b.memory_id, "supersedes"))
        await self.store.apply_plan(pa)
        await self.store.apply_plan(pb)
        active = [m.memory_id for m in await self.store.list_memories(subject="ali")]
        self.assertEqual(active, [a.memory_id], "the newest observation stays the single active head")
        self.assertEqual((await self.store.get_memory(b.memory_id)).superseded_by, a.memory_id)

    async def test_retracted_memories_never_resurface(self) -> None:
        old = make_memory("Ali lived in Berlin.", observed_at="2026-01-01T00:00:00+00:00")
        new = make_memory("Ali lives in Tokyo.", observed_at="2026-06-01T00:00:00+00:00")
        await self.store.insert_memory(old, entity_ids=["ali"]); await self.store.insert_memory(new, entity_ids=["ali"])
        await self.store.supersede(old.memory_id, new.memory_id)
        await self.store.retract(old.memory_id, "forget")
        self.assertEqual(await self.store.history(new.memory_id), [])
        ret = MemoryRetriever(self.store, FakeLLMClient())
        self.assertEqual(await ret.related(new.memory_id), [])


class RetrievalFixTests(Base):
    async def asyncSetUp(self) -> None:
        self.ret = MemoryRetriever(self.store, FakeLLMClient(), RetrievalConfig())
        self.mel = make_memory("Mel works at a pottery studio in Tokyo.", subject="mel", embedding=fake_embedding("Mel works at a pottery studio in Tokyo."))
        await self.store.insert_memory(self.mel, entity_ids=["mel", "tokyo"])
        await self.store.upsert_entity("mel", "Mel", "person", aliases=["melanie"])
        self.trip = make_memory("Mel plans to visit Berlin in October 2026.", subject="mel", embedding=fake_embedding("Mel plans to visit Berlin in October 2026."), kind="plan")
        self.trip.event_time, self.trip.event_time_precision = "2026-10", "month"
        await self.store.insert_memory(self.trip, entity_ids=["mel", "berlin"])
        self.joerg = make_memory("Ali's brother Jörg Müller lives in Zürich.", subject="ali", embedding=fake_embedding("Ali's brother Jörg Müller lives in Zürich."))
        await self.store.insert_memory(self.joerg, entity_ids=["ali", "jorg muller", "zurich"])

    async def test_possessive_query_matches_entity_and_keyword(self) -> None:
        self.assertEqual(await self.ret.query_entities("What is Mel's job?"), ["mel"])
        self.assertEqual(fts_match_string("What is Mel's job?"), '"mel" OR "job"')
        hits = await self.ret.search("What is Mel's job?", k=3)
        self.assertEqual(hits[0].memory.memory_id, self.mel.memory_id)
        self.assertIn("keyword", hits[0].channels)

    async def test_subject_filter_and_profile_resolve_aliases(self) -> None:
        hits = await self.ret.search("pottery", subject="Melanie")
        self.assertEqual([h.memory.memory_id for h in hits], [self.mel.memory_id])
        self.assertEqual((await self.ret.profile("Melanie"))["memory_count"], 2)
        self.assertEqual(len(await self.store.list_memories(subject="Melanie")), 2)

    async def test_month_precision_and_same_day_bounds(self) -> None:
        hits = await self.ret.search("visit Berlin", since="2026-10-01", until="2026-10-31")
        self.assertIn(self.trip.memory_id, [h.memory.memory_id for h in hits])
        self.assertEqual(await self.ret.search("visit Berlin", since="2026-11-01"), [])
        day = make_memory("Ali ran at dawn.", subject="ali", observed_at="2026-04-02T09:00:00+00:00", embedding=fake_embedding("Ali ran at dawn."))
        await self.store.insert_memory(day, entity_ids=["ali"])
        hits = await self.ret.search("dawn", until="2026-04-02")
        self.assertIn(day.memory_id, [h.memory.memory_id for h in hits])

    async def test_diacritics_fold_in_keyword_channel(self) -> None:
        self.assertEqual(tokenize("Zürich Müller"), ["zurich", "muller"])
        hits = await self.ret.search("Zürich", k=3)
        self.assertEqual(hits[0].memory.memory_id, self.joerg.memory_id)
        self.assertIn("keyword", hits[0].channels)

    async def test_filtered_keyword_channel_is_not_starved(self) -> None:
        for i in range(300):
            m = make_memory(f"User note {i} about the marathon training block.", subject="user", chat_id="big", embedding=fake_embedding(f"note {i} marathon"))
            await self.store.insert_memory(m, entity_ids=["user"])
        mel_run = make_memory("Mel finished her first marathon in Osaka.", subject="mel", embedding=fake_embedding("Mel finished her first marathon in Osaka."), kind="event")
        await self.store.insert_memory(mel_run, entity_ids=["mel"])
        hits = await self.ret.search("marathon", subject="Mel", k=5, channels="K")
        self.assertEqual([h.memory.memory_id for h in hits], [mel_run.memory_id])

    async def test_index_is_incremental_and_handles_mixed_dimensions(self) -> None:
        await self.ret.search("pottery")
        idx = self.ret._index
        before = idx.last_updated_at
        weird = make_memory("Ali collects stamps.", subject="ali", embedding=[0.1, 0.2, 0.3])
        await self.store.insert_memory(weird, entity_ids=["ali"])
        await self.ret.search("stamps")
        self.assertIs(self.ret._index, idx, "index object is refreshed in place, not rebuilt")
        self.assertGreaterEqual(idx.last_updated_at, before)
        self.assertIn(3, idx.mats)
        self.assertIn(len(fake_embedding("x")), idx.mats)
        hits = await self.ret.search("stamps", k=2)
        self.assertEqual(hits[0].memory.memory_id, weird.memory_id)
        await self.store.retract(weird.memory_id)
        self.assertNotIn(weird.memory_id, [h.memory.memory_id for h in await self.ret.search("stamps", k=5)])

    async def test_context_skips_lines_that_do_not_fit(self) -> None:
        long = make_memory("Mel " + "talked about pottery glazes at length " * 12 + "in Tokyo.", subject="mel", embedding=fake_embedding("Mel pottery glazes Tokyo"))
        await self.store.insert_memory(long, entity_ids=["mel"])
        block = await self.ret.context_for("Mel pottery", max_chars=260)
        self.assertIn("pottery studio", block, "a shorter relevant line still makes it into a tight budget")

    async def test_bm25_fallback_on_tiny_corpus(self) -> None:
        self.store.has_fts = False
        hits = await self.store.keyword_candidates("pottery")
        self.assertEqual([h[0] for h in hits], [self.mel.memory_id])


class HttpMcpFixTests(Base):
    async def asyncSetUp(self) -> None:
        cfg = ChatMemoryConfig(); cfg.debounce_seconds = 0
        await self.store.close()
        self.cm = ChatMemory(self.db_path, llm=scripted_fake_llm(), config=cfg)
        self.store = self.cm.store
        self.app = create_app(self.cm, allowed_hosts=["localhost", "127.0.0.1"])
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.cm.close()
        self._tmp.cleanup()

    async def test_cross_site_writes_are_refused(self) -> None:
        body = json.dumps({"chat_id": "x", "speaker": "Ali", "text": "I moved to Berlin."})
        r = await self.client.post("/api/messages", data=body, headers={"Origin": "https://evil.example", "Content-Type": "text/plain"})
        self.assertEqual(r.status, 403)
        r = await self.client.post("/mcp", data=body, headers={"Origin": "https://evil.example", "Content-Type": "text/plain"})
        self.assertEqual(r.status, 403)
        r = await self.client.post("/api/messages", data=body, headers={"Content-Type": "text/plain"})
        self.assertEqual(r.status, 415, "a JSON body must be declared as JSON")
        r = await self.client.post("/api/messages", data=body, headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:" + str(self.client.port)})
        self.assertEqual(r.status, 202, "same-origin browser requests work")
        self.assertEqual((await self.cm.store.message_counts()).get("pending"), 1)

    async def test_bad_body_types_are_400_not_500(self) -> None:
        r = await self.client.post("/api/remember", json={"text": "Ali likes tea.", "importance": "high"})
        self.assertEqual(r.status, 400)
        r = await self.client.post("/api/messages", json={"chat_id": "x", "speaker": "Ali", "text": "hi", "sent_at": 12345})
        self.assertEqual(r.status, 400)
        r = await self.client.post("/api/messages/batch", json={"chat_id": "x", "messages": ["not an object"]})
        self.assertEqual(r.status, 400)

    async def test_mcp_malformed_shapes_and_version_header(self) -> None:
        p = MCPProtocol(self.cm)
        self.assertEqual((await p.handle({"jsonrpc": "2.0", "id": 1, "method": 5}))["error"]["code"], -32600)
        self.assertEqual((await p.handle({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []}))["error"]["code"], -32602)
        self.assertEqual((await p.handle_payload([]))["error"]["code"], -32600)
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"MCP-Protocol-Version": "1999-01-01"})
        self.assertEqual(r.status, 400)

    async def test_stdio_survives_a_huge_message(self) -> None:
        reader = asyncio.StreamReader(limit=64 * 1024 * 1024)
        out: list[bytes] = []
        big = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory_add_message",
               "arguments": {"chat_id": "big", "speaker": "Ali", "text": "x" * 200_000}}}
        reader.feed_data((json.dumps(big) + "\n").encode())
        reader.feed_data((json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n").encode())
        reader.feed_eof()
        await serve_stdio(self.cm, reader=reader, write=out.append)
        ids = sorted(json.loads(b)["id"] for b in out)
        self.assertEqual(ids, [1, 2])


if __name__ == "__main__":
    unittest.main()


class ResidualTests(Base):
    async def test_debounce_coalesces_with_sub_second_timing(self) -> None:
        """A turn at x.60 s and its reply 0.5 s later must wait for each other and be leased as one batch."""
        from datetime import datetime, timedelta, timezone
        from unittest import mock
        import NeuralGraph.chat_memory.store as store_mod
        t0 = datetime(2026, 9, 17, 10, 0, 0, 600000, tzinfo=timezone.utc)
        clock = {"now": t0}
        with mock.patch.object(store_mod, "utcnow", lambda: clock["now"]):
            await self.store.add_message("t", "Ali", "I moved to Berlin.", debounce_seconds=1.0)
            clock["now"] = t0 + timedelta(seconds=0.5)
            await self.store.add_message("t", "assistant", "Nice! Berlin is lovely.", role="assistant", debounce_seconds=1.0)
            clock["now"] = t0 + timedelta(seconds=1.2)
            self.assertEqual(await self.store.lease_job("w", 60, batch_size=6), [], "nothing runnable inside the reply's window")
            clock["now"] = t0 + timedelta(seconds=1.6)
            jobs = await self.store.lease_job("w", 60, batch_size=6)
            self.assertEqual(len(jobs), 2, "turn and reply leased together")

    async def test_kind_and_time_filters_reach_keyword_and_graph_channels(self) -> None:
        for i in range(300):
            m = make_memory(f"User note {i} about the marathon training block.", subject="user", chat_id="big",
                            embedding=fake_embedding(f"note {i} marathon"), importance=0.9, observed_at="2026-02-01T00:00:00+00:00")
            await self.store.insert_memory(m, entity_ids=["user", "marathon"])
        old = make_memory("Mel ran the Osaka marathon.", subject="mel", embedding=fake_embedding("Mel ran the Osaka marathon."),
                          kind="event", importance=0.3, observed_at="2019-06-01T00:00:00+00:00")
        await self.store.insert_memory(old, entity_ids=["mel", "marathon"])
        await self.store.upsert_entity("marathon", "marathon", "event")
        ret = MemoryRetriever(self.store, FakeLLMClient())
        self.assertEqual([h.memory.memory_id for h in await ret.search("marathon", kinds=["event"], channels="K")], [old.memory_id])
        self.assertEqual([h.memory.memory_id for h in await ret.search("marathon", until="2020-01-01", channels="G")], [old.memory_id])
        # store-side list/timeline use the same interval semantics as the index
        month = make_memory("Mel plans a Kyoto trip.", subject="mel", embedding=fake_embedding("Kyoto trip"), kind="plan")
        month.event_time, month.event_time_precision = "2026-10", "month"
        await self.store.insert_memory(month, entity_ids=["mel"])
        self.assertEqual([m.memory_id for m in await self.store.list_memories(subject="mel", since="2026-10-01", until="2026-10-31")], [month.memory_id])
        self.assertEqual([m.memory_id for m in await self.store.list_memories(subject="mel", since="2019-01-01", until="2019-12-31")], [old.memory_id])

    async def test_non_object_json_bodies_are_400(self) -> None:
        cfg = ChatMemoryConfig(); cfg.debounce_seconds = 0
        await self.store.close()
        cm = ChatMemory(self.db_path, llm=scripted_fake_llm(), config=cfg)
        self.store = cm.store
        client = TestClient(TestServer(create_app(cm)))
        await client.start_server()
        try:
            for path in ("/api/messages", "/api/messages/batch", "/api/remember", "/api/forget"):
                r = await client.post(path, data=b"[1, 2]", headers={"Content-Type": "application/json"})
                self.assertEqual(r.status, 400, path)
            r = await client.post("/api/messages/batch", json={"chat_id": "b", "messages": [{"text": "ok one"}, {"text": "bad", "speaker": 5}]})
            self.assertEqual(r.status, 400)
            self.assertEqual(await cm.store.message_counts(), {}, "all-or-nothing: nothing inserted")
        finally:
            await client.close()
            await cm.close()
            self.store = ChatMemoryStore(self.db_path)

    async def test_worker_learns_embedding_dimension_from_its_own_plans(self) -> None:
        cfg = ChatMemoryConfig(); cfg.debounce_seconds = 0
        await self.store.close()
        cm = ChatMemory(self.db_path, llm=scripted_fake_llm(), config=cfg)
        self.store = cm.store
        await cm.add_message("d", "Ali", "I moved to Berlin in March and my cat Luna is 3 years old.", sent_at="2026-04-01T10:00:00+00:00")
        await cm.process_pending()
        self.assertEqual(await cm.store.get_meta("embed_dim"), str(len(fake_embedding("x"))))
        # switch to a model with another dimension: maintenance re-embeds the old rows
        cm.llm = cm.worker.llm = scripted_fake_llm(dim=128)
        report = await cm.maintain()
        self.assertEqual(report["reembedded"], 0, "dimension meta still says 256; the probe is only used when unknown")
        await cm.store.set_meta("embed_dim", "128")
        report = await cm.maintain()
        self.assertGreaterEqual(report["reembedded"], 1)
        self.assertTrue(all(len(m.embedding) == 128 for m in await cm.memories() if m.embedding))
        await cm.close()
        self.store = ChatMemoryStore(self.db_path)
