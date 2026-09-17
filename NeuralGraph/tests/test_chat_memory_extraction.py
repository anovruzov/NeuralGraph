"""Extraction-layer tests: JSON parsing of small-model output, the chit-chat gate, entity alias resolution,
temporal normalisation, grounding validation, and the reconcile decisions (ADD / DUPLICATE / UPDATE /
CONTRADICT) driven by a scripted fake LLM. No server, fully deterministic.

Run: .venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_extraction -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from NeuralGraph.chat_memory.extraction import (
    EntityRegistry,
    ExtractionConfig,
    MemoryExtractor,
    UnparseableOutput,
    parse_when,
)
from NeuralGraph.chat_memory.jsonutil import parse_json_array, parse_json_object
from NeuralGraph.chat_memory.llm import FakeLLMClient, fake_embedding
from NeuralGraph.chat_memory.store import ChatMemoryStore
from NeuralGraph.chat_memory.textutil import is_probable_chitchat, norm_entity, norm_relation, stem, tokenize


class JsonUtilTests(unittest.TestCase):
    def test_fenced_prefixed_trailing_commas(self) -> None:
        raw = 'Sure! Here is the JSON:\n```json\n{"memories": [{"text": "a",}, ],}\n```\nHope this helps.'
        self.assertEqual(parse_json_object(raw), {"memories": [{"text": "a"}]})

    def test_think_blocks_and_truncation(self) -> None:
        raw = '<think>reasoning</think>{"memories": [{"text": "one"}, {"text": "tw'
        self.assertEqual(parse_json_object(raw), {"memories": [{"text": "one"}, {"text": "tw"}]})

    def test_array_and_garbage(self) -> None:
        self.assertEqual(parse_json_array('[["a","b","c"]] trailing'), [["a", "b", "c"]])
        self.assertIsNone(parse_json_object("no json here"))
        self.assertIsNone(parse_json_array("{}"))

    def test_nested_braces_in_strings(self) -> None:
        raw = '{"text": "he said {hi} and \\"bye\\"", "n": 1}'
        self.assertEqual(parse_json_object(raw), {"text": 'he said {hi} and "bye"', "n": 1})


class TextUtilTests(unittest.TestCase):
    def test_chitchat_gate(self) -> None:
        for s in ("ok", "thanks!", "lol", "Hi!", "Sure! Let me know if you need anything else.", "good morning", ""):
            self.assertTrue(is_probable_chitchat(s), s)
        for s in ("I moved to Berlin", "My cat is called Luna", "42", "Can you remind me what Mel said about Tokyo?"):
            self.assertFalse(is_probable_chitchat(s), s)

    def test_normalisation(self) -> None:
        self.assertEqual(norm_entity("  Melanie's "), "melanie")
        self.assertEqual(norm_entity("São Paulo"), "sao paulo")
        self.assertEqual(norm_entity("The Beatles'"), "beatles")
        self.assertEqual(norm_relation("Lives In"), "lives_in")
        self.assertEqual(tokenize("I moved to Berlin in March 2024, it's great"), ["move", "berlin", "march", "2024"])
        for w, expect in (("lives", "live"), ("running", "run"), ("cities", "city"), ("visited", "visit"), ("planned", "plan"),
                          ("buses", "bus"), ("glass", "glass"), ("hated", "hate"), ("cooking", "cook")):
            self.assertEqual(stem(w), expect, w)

    def test_fake_embedding_is_deterministic_and_lexical(self) -> None:
        a, b, c = fake_embedding("Ali moved to Berlin"), fake_embedding("Ali moved to Berlin"), fake_embedding("weather nice")
        self.assertEqual(a, b)
        dot = lambda x, y: sum(i * j for i, j in zip(x, y))
        self.assertAlmostEqual(dot(a, a), 1.0, places=6)
        self.assertLess(dot(a, c), 0.5)


class WhenParsingTests(unittest.TestCase):
    def test_absolute_forms(self) -> None:
        self.assertEqual(parse_when("2024-03-15", None), ("2024-03-15", "day"))
        self.assertEqual(parse_when("2024-03", None), ("2024-03", "month"))
        self.assertEqual(parse_when("2024", None), ("2024", "year"))
        self.assertEqual(parse_when("March 2024", None), ("2024-03", "month"))
        self.assertEqual(parse_when("8 May 2023", None), ("2023-05-08", "day"))
        self.assertEqual(parse_when("May 8, 2023", None), ("2023-05-08", "day"))
        self.assertEqual(parse_when("null", None), (None, "none"))
        self.assertEqual(parse_when("2024-13-01", None), (None, "none"))

    def test_relative_forms_resolve_against_message_date(self) -> None:
        self.assertEqual(parse_when("yesterday", "2026-09-17T10:00:00+00:00"), ("2026-09-16", "day"))
        self.assertEqual(parse_when("next summer", "2026-09-17T10:00:00+00:00"), (None, "none"))


class RegistryTests(unittest.TestCase):
    def test_alias_prefix_fuzzy_and_first_person(self) -> None:
        r = EntityRegistry()
        r.add("melanie carter", "Melanie Carter", "person", 10, aliases=["mel"])
        r.add("berlin", "Berlin", "place", 5)
        self.assertEqual(r.resolve("Mel"), "melanie carter")
        self.assertEqual(r.resolve("Melanie"), "melanie carter")
        self.assertEqual(r.resolve("melanie carter's"), "melanie carter")
        self.assertEqual(r.resolve("I", speaker_id="ali"), "ali")
        self.assertEqual(r.resolve("Berlín"), "berlin")
        self.assertEqual(r.resolve("Tokyo", etype="city"), "tokyo")
        self.assertEqual(r.new_entities["tokyo"]["type"], "place")
        self.assertIsNone(r.resolve("May"), "month names never become entities")
        self.assertIsNone(r.resolve("2024"))
        self.assertIsNone(r.resolve(""))

    def test_ambiguous_first_name_is_not_forced(self) -> None:
        r = EntityRegistry()
        r.add("alex smith", "Alex Smith", "person", 3)
        r.add("alex jones", "Alex Jones", "person", 3)
        self.assertEqual(r.resolve("Alex"), "alex", "two candidates -> new entity rather than a guess")


MEMORY_JSON = {
    "memories": [
        {"text": "Ali moved to Berlin in March 2026.", "kind": "event", "subject": "Ali", "when": "2026-03", "importance": 0.9,
         "confidence": 1.0, "sources": [1], "entities": [{"name": "Berlin", "type": "place"}]},
        {"text": "Ali has a cat called Luna.", "kind": "fact", "subject": "Ali", "when": None, "importance": 0.7,
         "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]},
        {"text": "Ali is tired today.", "kind": "other", "subject": "Ali", "when": None, "importance": 0.1, "confidence": 0.9, "sources": [1]},
        {"text": "Ali met Zorblax Quintrell at the market.", "kind": "event", "subject": "Ali", "when": None, "importance": 0.6, "confidence": 0.9, "sources": [1]},
        {"text": "The assistant recommends visiting museums.", "kind": "other", "subject": "assistant", "importance": 0.5, "confidence": 0.9, "sources": [2]},
    ]
}
RELATION_JSON = {"entities": [{"name": "Berlin", "type": "place"}, {"name": "Luna", "type": "pet"}],
                 "triples": [{"s": "I", "r": "lives in", "o": "Berlin", "source": 1, "confidence": 0.9},
                             {"s": "Ali", "r": "has_pet", "o": "Luna", "source": 1, "confidence": 0.9},
                             {"s": "Ali", "r": "has_pet", "o": "Luna", "source": 1, "confidence": 0.5}]}


class ExtractorTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ChatMemoryStore(Path(self._tmp.name) / "t.db")

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmp.cleanup()

    def make_llm(self, memories=MEMORY_JSON, relations=RELATION_JSON, reconcile=None) -> FakeLLMClient:
        script = [("You maintain the long-term memory", json.dumps(memories)),
                  ("You are building a knowledge graph", json.dumps(relations)),
                  ("You maintain a memory store", json.dumps(reconcile or {"decision": "ADD", "target": None}))]
        return FakeLLMClient(script=script)

    async def seed(self):
        m1, _ = await self.store.add_message("c1", "Ali", "I moved to Berlin in March 2026 and yesterday my cat Luna turned 3.", sent_at="2026-04-01T10:00:00+00:00")
        m2, _ = await self.store.add_message("c1", "assistant", "Berlin has great museums, you should visit them.", role="assistant", sent_at="2026-04-01T10:00:10+00:00")
        return m1, m2


class ExtractorPlanTests(ExtractorTestCase):
    async def test_plan_from_scripted_llm(self) -> None:
        m1, m2 = await self.seed()
        llm = self.make_llm()
        ex = MemoryExtractor(self.store, llm, ExtractionConfig())
        plan = await ex.build_plan([m1, m2], job_ids=[])
        texts = [m.text for m, _, _ in plan.new_memories]
        self.assertEqual(texts, ["Ali moved to Berlin in March 2026.", "Ali has a cat called Luna."])
        reasons = [d.get("reason") for k, _, d in plan.audit if k == "reject"]
        self.assertIn("unknown_name", reasons, "hallucinated 'Zorblax Quintrell' must be rejected")
        self.assertTrue({"assistant_source", "assistant_subject"} & set(reasons), f"assistant memory must be rejected: {reasons}")
        self.assertIn("low_importance", reasons)
        mem = plan.new_memories[0][0]
        self.assertEqual((mem.subject, mem.event_time, mem.event_time_precision, mem.kind), ("ali", "2026-03", "month", "event"))
        self.assertEqual(plan.new_memories[0][2], ["ali", "berlin"])
        self.assertEqual(plan.new_memories[0][1], [m1.message_id])
        rels = {(s, p, o) for s, p, o, *_ in plan.relations}
        self.assertEqual(rels, {("ali", "lives_in", "berlin"), ("ali", "has_pet", "luna")})
        ent_ids = {e["entity_id"] for e in plan.entities}
        self.assertEqual(ent_ids, {"ali", "berlin", "luna"})
        self.assertEqual(sorted(plan.processed_message_ids), sorted([m1.message_id, m2.message_id]))
        self.assertEqual(llm.stats.generate_calls, 2, "memory + relation prompts, no reconcile needed on an empty store")
        prompt = llm.prompts[0]
        self.assertIn("yesterday [= 31 March 2026]", prompt, "relative dates are annotated for the model")
        self.assertIn("(assistant)", prompt)

    async def test_gate_skips_chitchat_and_assistant_only_without_llm(self) -> None:
        m1, _ = await self.store.add_message("c1", "Ali", "thanks!!")
        m2, _ = await self.store.add_message("c1", "assistant", "You're welcome, anything else?", role="assistant")
        llm = self.make_llm()
        plan = await MemoryExtractor(self.store, llm).build_plan([m1, m2])
        self.assertEqual(llm.stats.generate_calls, 0)
        self.assertIn(m1.message_id, plan.skipped_message_ids)
        self.assertEqual(set(plan.skipped_message_ids) | set(plan.processed_message_ids), {m1.message_id, m2.message_id})
        self.assertEqual(plan.new_memories, [])
        # an assistant turn with real content is context only: still no LLM call, marked processed
        m3, _ = await self.store.add_message("c1", "assistant", "Berlin has three opera houses and a famous zoo.", role="assistant")
        plan = await MemoryExtractor(self.store, llm).build_plan([m3])
        self.assertEqual(llm.stats.generate_calls, 0)
        self.assertEqual(plan.processed_message_ids, [m3.message_id])

    async def test_duplicate_exact_and_embedding(self) -> None:
        m1, m2 = await self.seed()
        ex = MemoryExtractor(self.store, self.make_llm())
        plan = await ex.build_plan([m1, m2])
        await self.store.apply_plan(plan)
        # same messages again in another chat -> exact duplicates, no new memories, sources attached
        m3, _ = await self.store.add_message("c2", "Ali", "I moved to Berlin in March 2026 and yesterday my cat Luna turned 3.", sent_at="2026-05-01T10:00:00+00:00")
        plan2 = await ex.build_plan([m3])
        self.assertEqual(plan2.new_memories, [])
        self.assertEqual(len(plan2.duplicate_sources), 2)
        await self.store.apply_plan(plan2)
        mem = (await self.store.list_memories(subject="ali", order="observed_at ASC"))[0]
        full = await self.store.get_memory(mem.memory_id, with_sources=True)
        self.assertIn(m3.message_id, full.source_message_ids)

    async def test_contradict_supersedes_when_newer(self) -> None:
        m1, m2 = await self.seed()
        ex = MemoryExtractor(self.store, self.make_llm())
        await self.store.apply_plan(await ex.build_plan([m1, m2]))
        m3, _ = await self.store.add_message("c2", "Ali", "I moved to Tokyo in June 2026.", sent_at="2026-07-01T10:00:00+00:00")
        newer = {"memories": [{"text": "Ali moved to Tokyo in June 2026.", "kind": "event", "subject": "Ali", "when": "2026-06",
                               "importance": 0.9, "confidence": 1.0, "sources": [1], "entities": [{"name": "Tokyo", "type": "place"}]}]}
        cfg = ExtractionConfig(reconcile_cosine=0.3)   # fake embeddings: lower the band so the LLM is consulted
        llm = self.make_llm(memories=newer, relations={"entities": [], "triples": []},
                            reconcile={"decision": "CONTRADICT", "target": "A", "text": None, "reason": "moved again"})
        plan = await MemoryExtractor(self.store, llm, cfg).build_plan([m3])
        self.assertEqual(len(plan.new_memories), 1)
        self.assertEqual(len(plan.supersedes), 1)
        self.assertEqual(plan.supersedes[0][2], "contradicts")
        self.assertIn("You maintain a memory store", llm.prompts[-1])
        await self.store.apply_plan(plan)
        active = [m.text for m in await self.store.list_memories(subject="ali")]
        self.assertIn("Ali moved to Tokyo in June 2026.", active)
        self.assertNotIn("Ali moved to Berlin in March 2026.", active)
        self.assertEqual(plan.new_memories[0][0].version, 2)

    async def test_older_observation_cannot_supersede_newer_fact(self) -> None:
        m1, m2 = await self.seed()
        ex = MemoryExtractor(self.store, self.make_llm())
        await self.store.apply_plan(await ex.build_plan([m1, m2]))
        # an OLD chat imported later says something else about the same fact
        m3, _ = await self.store.add_message("old-chat", "Ali", "I moved to Paris in 2020.", sent_at="2020-05-01T10:00:00+00:00")
        older = {"memories": [{"text": "Ali moved to Paris in 2020.", "kind": "event", "subject": "Ali", "when": "2020",
                               "importance": 0.9, "confidence": 1.0, "sources": [1], "entities": [{"name": "Paris", "type": "place"}]}]}
        llm = self.make_llm(memories=older, relations={"entities": [], "triples": []},
                            reconcile={"decision": "CONTRADICT", "target": "A"})
        plan = await MemoryExtractor(self.store, llm, ExtractionConfig(reconcile_cosine=0.3)).build_plan([m3])
        self.assertEqual(plan.supersedes, [], "an earlier observation must not replace the current fact")
        self.assertEqual(plan.new_memories[0][0].status, "superseded")
        await self.store.apply_plan(plan)
        self.assertEqual([m.text for m in await self.store.list_memories(subject="ali", kinds=["event"])], ["Ali moved to Berlin in March 2026."])

    async def test_update_merges_text(self) -> None:
        m1, m2 = await self.seed()
        ex = MemoryExtractor(self.store, self.make_llm())
        await self.store.apply_plan(await ex.build_plan([m1, m2]))
        m3, _ = await self.store.add_message("c2", "Ali", "My cat Luna is a grey British shorthair.", sent_at="2026-05-01T10:00:00+00:00")
        upd = {"memories": [{"text": "Ali has a cat called Luna, a grey British shorthair.", "kind": "fact", "subject": "Ali",
                             "importance": 0.7, "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]}]}
        llm = self.make_llm(memories=upd, relations={"entities": [], "triples": []},
                            reconcile={"decision": "UPDATE", "target": "B", "text": "Ali has a grey British shorthair cat called Luna."})
        plan = await MemoryExtractor(self.store, llm, ExtractionConfig(reconcile_cosine=0.3)).build_plan([m3])
        await self.store.apply_plan(plan)
        texts = [m.text for m in await self.store.list_memories(subject="ali")]
        self.assertIn("Ali has a grey British shorthair cat called Luna.", texts)
        self.assertNotIn("Ali has a cat called Luna.", texts)
        new = [m for m in await self.store.list_memories(subject="ali") if "shorthair" in m.text][0]
        self.assertEqual(new.version, 2)
        self.assertEqual(len(await self.store.history(new.memory_id)), 1)

    async def test_embedding_outage_does_not_lose_memories(self) -> None:
        m1, m2 = await self.seed()
        llm = self.make_llm()

        async def broken_embed(text):
            raise RuntimeError("embedding server down")
        llm.embed = broken_embed  # type: ignore[assignment]

        async def broken_many(texts):
            raise RuntimeError("embedding server down")
        llm.embed_many = broken_many  # type: ignore[assignment]
        plan = await MemoryExtractor(self.store, llm).build_plan([m1, m2])
        self.assertEqual(len(plan.new_memories), 2)
        self.assertTrue(all(m.embedding is None for m, _, _ in plan.new_memories))
        await self.store.apply_plan(plan)
        self.assertEqual(len(await self.store.memories_missing_embedding()), 2)

    async def test_unparseable_output_raises_for_retry_and_retries_vary(self) -> None:
        m1, m2 = await self.seed()
        llm = FakeLLMClient(default_response="I cannot help with that.")
        ex = MemoryExtractor(self.store, llm)
        with self.assertRaises(UnparseableOutput):
            await ex.build_plan([m1, m2])
        with self.assertRaises(UnparseableOutput):
            await ex.build_plan([m1, m2], attempt=2)
        first, second = llm.prompts[0], llm.prompts[2]
        self.assertNotEqual(first, second, "a retry must not resend the identical prompt")
        self.assertIn("was not valid JSON", second)
        # an explicit empty answer is fine: nothing worth remembering
        llm = FakeLLMClient(default_response='{"memories": []}')
        plan = await MemoryExtractor(self.store, llm).build_plan([m1, m2])
        self.assertEqual(plan.new_memories, [])
        self.assertEqual(sorted(plan.processed_message_ids), sorted([m1.message_id, m2.message_id]))

    async def test_relations_carry_source_time(self) -> None:
        m1, m2 = await self.seed()
        plan = await MemoryExtractor(self.store, self.make_llm()).build_plan([m1, m2])
        self.assertTrue(all(len(r) == 7 and r[6] == m1.sent_at for r in plan.relations))


if __name__ == "__main__":
    unittest.main()
