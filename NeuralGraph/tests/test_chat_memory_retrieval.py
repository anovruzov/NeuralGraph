"""Retrieval + facade tests: channel fusion, subject routing from metadata, filters, superseded handling,
context blocks with a relative cutoff, explicit remember/forget, the token ledger and the grade.

Run: .venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_retrieval -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig, estimate_tokens
from NeuralGraph.chat_memory.llm import FakeLLMClient, fake_embedding
from NeuralGraph.chat_memory.retrieval import MemoryRetriever, RetrievalConfig, rrf
from NeuralGraph.chat_memory.store import ChatMemoryStore
from NeuralGraph.tests.test_chat_memory_store import make_memory


def emb_memory(text: str, **kw):
    return make_memory(text, embedding=fake_embedding(text), **kw)


class RetrievalTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ChatMemoryStore(Path(self._tmp.name) / "r.db")
        self.llm = FakeLLMClient()
        self.ret = MemoryRetriever(self.store, self.llm, RetrievalConfig())

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmp.cleanup()

    async def seed(self) -> dict[str, str]:
        ids = {}
        specs = [
            ("ali_city", "Ali lives in Berlin near Prenzlauer Berg.", "ali", "c1", "2026-03-01T00:00:00+00:00", "identity", 0.9),
            ("ali_cat", "Ali has a cat called Luna.", "ali", "c1", "2026-03-01T00:00:00+00:00", "fact", 0.7),
            ("mel_city", "Mel lives in Tokyo and works at a pottery studio.", "mel", "c2", "2026-04-01T00:00:00+00:00", "identity", 0.9),
            ("mel_trip", "Mel plans to visit Berlin in October 2026.", "mel", "c2", "2026-04-02T00:00:00+00:00", "plan", 0.7),
            ("ali_old", "Ali ran a half marathon in 2019.", "ali", "c3", "2019-06-01T00:00:00+00:00", "event", 0.5),
        ]
        for key, text, subject, chat, at, kind, imp in specs:
            m = emb_memory(text, subject=subject, chat_id=chat, observed_at=at, kind=kind, importance=imp)
            ents = [subject]
            if "Berlin" in text:
                ents.append("berlin")
            if "Tokyo" in text:
                ents.append("tokyo")
            if "Luna" in text:
                ents.append("luna")
            await self.store.insert_memory(m, entity_ids=ents)
            ids[key] = m.memory_id
        for eid, name, etype in (("ali", "Ali", "person"), ("mel", "Mel", "person"), ("berlin", "Berlin", "place"),
                                 ("tokyo", "Tokyo", "place"), ("luna", "Luna", "pet")):
            await self.store.upsert_entity(eid, name, etype, aliases=["melanie"] if eid == "mel" else [])
        await self.store.upsert_relation("ali", "has_pet", "luna", chat_id="c1", memory_id=ids["ali_cat"])
        await self.store.upsert_relation("ali", "lives_in", "berlin", chat_id="c1", memory_id=ids["ali_city"])
        return ids


class FusionTests(RetrievalTestCase):
    def test_rrf_shape(self) -> None:
        fused = rrf([["a", "b"], ["b", "c"]], [1.0, 1.0], k=60)
        self.assertGreater(fused["b"], fused["a"])
        self.assertGreater(fused["a"], fused["c"])

    async def test_subject_named_in_query_is_boosted(self) -> None:
        ids = await self.seed()
        hits = await self.ret.search("Where does Mel live?", k=3)
        self.assertEqual(hits[0].memory.memory_id, ids["mel_city"])
        self.assertIn("subject", hits[0].channels)
        # alias "Melanie" routes to the same subject (identity from metadata, not text)
        hits = await self.ret.search("Where does Melanie live?", k=3)
        self.assertEqual(hits[0].memory.memory_id, ids["mel_city"])

    async def test_channels_and_explanations(self) -> None:
        ids = await self.seed()
        hits = await self.ret.search("Luna cat", k=2)
        self.assertEqual(hits[0].memory.memory_id, ids["ali_cat"])
        self.assertIn("keyword", hits[0].channels)
        self.assertIn("graph", hits[0].channels, "the entity 'luna' is matched in the query")
        self.assertIn("keyword match", hits[0].explanation)
        self.assertTrue(hits[0].sources == [] and hits[0].entities)

    async def test_graph_hop_finds_related_memory(self) -> None:
        ids = await self.seed()
        hits = await self.ret.search("berlin", k=5, channels="G")
        got = [h.memory.memory_id for h in hits]
        self.assertIn(ids["ali_city"], got)
        self.assertIn(ids["mel_trip"], got)

    async def test_filters(self) -> None:
        ids = await self.seed()
        self.assertEqual([h.memory.memory_id for h in await self.ret.search("Berlin", subject="Mel")], [ids["mel_trip"]])
        self.assertEqual([h.memory.memory_id for h in await self.ret.search("Ali", chat_id="c3")], [ids["ali_old"]])
        self.assertEqual([h.memory.memory_id for h in await self.ret.search("Ali", kinds=["fact"])], [ids["ali_cat"]])
        self.assertEqual([h.memory.memory_id for h in await self.ret.search("Ali marathon", until="2020-01-01")], [ids["ali_old"]])
        self.assertEqual(await self.ret.search("Ali", since="2030-01-01"), [])

    async def test_superseded_memories_are_hidden_unless_asked(self) -> None:
        ids = await self.seed()
        new = emb_memory("Ali lives in Tokyo since June 2026.", subject="ali", chat_id="c4", observed_at="2026-07-01T00:00:00+00:00", kind="identity", importance=0.9)
        await self.store.insert_memory(new, entity_ids=["ali", "tokyo"])
        await self.store.supersede(ids["ali_city"], new.memory_id, "contradicts")
        hits = await self.ret.search("Where does Ali live?", k=5)
        got = [h.memory.memory_id for h in hits]
        self.assertIn(new.memory_id, got)
        self.assertNotIn(ids["ali_city"], got)
        hist = await self.ret.search("Where does Ali live?", k=5, include_superseded=True)
        old = [h for h in hist if h.memory.memory_id == ids["ali_city"]]
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0].memory.status, "superseded")
        self.assertIn("superseded by", old[0].explanation)

    async def test_index_cache_tracks_store_revision(self) -> None:
        await self.seed()
        before = {h.memory.memory_id for h in await self.ret.search("Ali", k=50)}
        extra = emb_memory("Ali bought a bike.", subject="ali")
        await self.store.insert_memory(extra, entity_ids=["ali"])
        after = {h.memory.memory_id for h in await self.ret.search("Ali", k=50)}
        self.assertEqual(after - before, {extra.memory_id})
        # retract goes through the store revision too
        await self.store.retract(extra.memory_id)
        self.assertNotIn(extra.memory_id, {h.memory.memory_id for h in await self.ret.search("Ali", k=50)})

    async def test_context_block_and_relative_cutoff(self) -> None:
        ids = await self.seed()
        block = await self.ret.context_for("Where does Mel live?", k=5)
        self.assertTrue(block.startswith("Relevant long-term memories"))
        self.assertIn("Mel lives in Tokyo", block)
        self.assertNotIn("half marathon", block, "weak, unrelated hits are cut by the relative-score threshold")
        self.assertEqual(await self.ret.context_for("zzzz qqqq"), "")
        short = await self.ret.context_for("Where does Mel live?", max_chars=120)
        self.assertLessEqual(len(short), 120)

    async def test_profile_related_timeline(self) -> None:
        ids = await self.seed()
        prof = await self.ret.profile("Ali")
        self.assertEqual(prof["memory_count"], 3)
        self.assertEqual({r["predicate"] for r in prof["relations"]}, {"has_pet", "lives_in"})
        await self.store.add_link(ids["ali_cat"], ids["ali_city"], "related", 0.5)
        rel = await self.ret.related(ids["ali_cat"])
        self.assertEqual([r["memory"]["memory_id"] for r in rel], [ids["ali_city"]])
        tl = await self.ret.timeline(subject="ali")
        self.assertEqual([m.memory_id for m in tl][0], ids["ali_old"])

    async def test_vector_channel_survives_dimension_mismatch(self) -> None:
        ids = await self.seed()
        weird = make_memory("Ali collects stamps.", subject="ali", embedding=[0.1, 0.2, 0.3])   # different embedding model
        await self.store.insert_memory(weird, entity_ids=["ali"])
        hits = await self.ret.search("stamps", k=3)
        self.assertEqual(hits[0].memory.memory_id, weird.memory_id, "keyword channel still finds it")


class FacadeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        cfg = ChatMemoryConfig(); cfg.debounce_seconds = 0
        self.cm = ChatMemory(Path(self._tmp.name) / "f.db", llm=FakeLLMClient(), config=cfg)

    async def asyncTearDown(self) -> None:
        await self.cm.close()
        self._tmp.cleanup()

    async def test_remember_forget_and_dedupe(self) -> None:
        m = await self.cm.remember("Ali prefers dark roast coffee.", subject="Ali", kind="preference", when="2026-01")
        self.assertEqual((m.subject, m.kind, m.event_time), ("ali", "preference", "2026-01"))
        again = await self.cm.remember("Ali prefers dark roast coffee.", subject="Ali")
        self.assertEqual(again.memory_id, m.memory_id)
        self.assertEqual(len(await self.cm.memories()), 1)
        hits = await self.cm.search("coffee")
        self.assertEqual(hits[0].memory.memory_id, m.memory_id)
        self.assertTrue(await self.cm.retract(m.memory_id))
        self.assertEqual(await self.cm.search("coffee"), [])
        with self.assertRaises(ValueError):
            await self.cm.remember("x")

    async def test_token_ledger_and_grade(self) -> None:
        self.assertEqual(estimate_tokens("a" * 40), 10)
        msg = await self.cm.add_message("c1", "Ali", "I moved to Berlin in March and I love it here, the food is great and my cat Luna enjoys the balcony." * 3)
        await self.cm.store.set_message_status([msg.message_id], "processed")
        await self.cm.remember("Ali moved to Berlin in March 2026.", subject="Ali", chat_id="c1")
        ledger = await self.cm.token_ledger()
        self.assertGreater(ledger["raw_tokens_digested"], ledger["memory_tokens"])
        self.assertEqual(ledger["tokens_saved"], ledger["raw_tokens_digested"] - ledger["memory_tokens"])
        self.assertGreater(ledger["compression_ratio"], 1.0)
        grade = await self.cm.grade()
        self.assertEqual(set(grade["axes"]), {"coverage", "compression", "connectivity", "freshness", "reliability"})
        self.assertTrue(all(0 <= v <= 100 for v in grade["axes"].values()))
        self.assertEqual(grade["axes"]["coverage"], 100.0)
        self.assertGreater(grade["axes"]["compression"], 50.0)
        self.assertIn(grade["letter"], "SABCD")
        block = await self.cm.context_for("Berlin")
        self.assertIn("Ali moved to Berlin", block)
        ledger = await self.cm.token_ledger()
        self.assertEqual(ledger["context_queries"], 1)
        self.assertGreater(ledger["context_raw_tokens_avoided"], 0)

    async def test_status_shape(self) -> None:
        st = await self.cm.status()
        for key in ("chats", "messages", "memories", "entities", "relations", "links", "jobs", "worker", "llm", "tokens",
                    "grade", "recent_memories", "top_entities", "recent_events"):
            self.assertIn(key, st)
        self.assertFalse(st["worker"]["running"])

    async def test_add_message_validates_and_infers_role(self) -> None:
        with self.assertRaises(ValueError):
            await self.cm.add_message("", "Ali", "x")
        m = await self.cm.add_message("c", "assistant", "hello")
        self.assertEqual(m.role, "assistant")
        m2 = await self.cm.add_message("c", "Ali", "hello there")
        self.assertEqual(m2.role, "")


if __name__ == "__main__":
    unittest.main()
