"""NeuralGraph as an MCP memory server: engine behaviour and tool surface.

Everything runs offline with the hashed embedder; no model, no network.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from NeuralGraph.mcp import server as mcp_server
from NeuralGraph.mcp.memory import HashedEmbedder, MemoryEngine, extract_entities, normalize_text
from NeuralGraph.sqlite_storage import SQLiteNeuralGraphStorage


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "memory.db"
        self.clock = _Clock()
        self.engine = MemoryEngine(self.db, session_key="agent-a", clock=self.clock, failure_domain="workstation-1")

    async def asyncTearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    async def test_remember_creates_a_memory_with_entities_and_provenance_fields(self):
        item = await self.engine.remember(
            "Deploys run from GitHub Actions on push to main; secrets live in 1Password.",
            kind="fact", tags=["Deploy", "ci"], source="docs/deploy.md", importance=0.8,
        )
        self.assertFalse(item["deduplicated"])
        self.assertEqual(item["kind"], "fact")
        self.assertEqual(item["tags"], ["ci", "deploy"])
        self.assertIn("github", item["entities"])
        self.assertEqual(item["source"], "docs/deploy.md")
        self.assertEqual(item["state"], "active")
        self.assertEqual(item["embedding_source"], HashedEmbedder.name)
        node = await self.engine.storage.get_node(item["id"])
        self.assertEqual(node.metadata["failure_domain"], "workstation-1")
        self.assertEqual(node.metadata["created_by"], "neuralgraph-mcp")

    async def test_failure_domain_is_absent_unless_the_operator_names_it(self):
        engine = MemoryEngine(Path(self.tmp.name) / "other.db", session_key="b", clock=self.clock)
        try:
            item = await engine.remember("A memory with no declared domain.")
            node = await engine.storage.get_node(item["id"])
            self.assertNotIn("failure_domain", node.metadata)
        finally:
            engine.close()

    async def test_exact_and_near_duplicates_strengthen_instead_of_cloning(self):
        first = await self.engine.remember("The default branch is main.", kind="fact")
        again = await self.engine.remember("  The default   branch is MAIN. ", kind="fact")
        self.assertTrue(again["deduplicated"])
        self.assertEqual(again["id"], first["id"])
        self.assertGreater(again["heat"], first["heat"])
        near = await self.engine.remember("The default branch is main!", kind="fact")
        self.assertTrue(near["deduplicated"])
        self.assertEqual(near["id"], first["id"])
        self.assertEqual((await self.engine.stats())["memories"], 1)

    async def test_keyed_fact_supersedes_records_contradiction_and_keeps_history(self):
        old = await self.engine.remember("Ali uses neovim as the editor.", kind="preference", key="user.editor")
        new = await self.engine.remember("Ali uses Zed as the editor.", kind="preference", key="user.editor")
        self.assertEqual(new["supersedes"], old["id"])
        self.assertEqual(new["contradicts"]["memory_id"], old["id"])
        self.assertIn(old["id"], new["source_memory_ids"])
        old_now = await self.engine.get(old["id"])
        self.assertEqual(old_now["state"], "archived")
        self.assertEqual(old_now["superseded_by"], new["id"])
        self.assertIsNotNone(old_now["valid_to"])
        # derivation edge new -> old, the direction the lineage resolver reads
        self.assertEqual((await self.engine.get(new["id"]))["derived_from"], [old["id"]])
        current = await self.engine.recall("which editor does Ali use")
        self.assertEqual([m["id"] for m in current], [new["id"]])
        history = await self.engine.recall("editor", include_superseded=True)
        self.assertEqual({m["id"] for m in history}, {old["id"], new["id"]})

    async def test_same_content_under_a_key_does_not_create_a_contradiction(self):
        first = await self.engine.remember("Standup is at 09:30.", kind="fact", key="team.standup")
        second = await self.engine.remember("Standup is at 09:30.", kind="fact", key="team.standup")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["id"], first["id"])

    async def test_recall_fuses_keyword_entity_and_vector_signals_and_explains(self):
        await self.engine.remember("Ali prefers dark mode in every editor.", kind="preference")
        await self.engine.remember("The staging database is Postgres 16 on Railway.", kind="fact")
        await self.engine.remember("Lunch on Fridays is at the taco place.", kind="note")
        hits = await self.engine.recall("what database does staging use", explain=True)
        self.assertEqual(hits[0]["content"], "The staging database is Postgres 16 on Railway.")
        why = hits[0]["why"]
        self.assertEqual(why["keyword_rank"], 1)
        self.assertIn("staging", why["shared_entities"])
        # recency alone never surfaces a memory
        self.assertTrue(all("taco" not in h["content"] for h in hits))

    async def test_recall_strengthens_used_memories_and_decay_archives_cold_ones(self):
        used = await self.engine.remember("Ali prefers dark mode in every editor.", kind="preference", importance=0.2)
        cold = await self.engine.remember("Parking is on level two.", kind="note", importance=0.2)
        before = (await self.engine.get(used["id"]))["heat"]
        await self.engine.recall("dark mode preference")
        self.assertGreater((await self.engine.get(used["id"]))["heat"], before)
        result = await self.engine.decay(rate=0.35, archive_below=0.1)
        self.assertEqual(result["cooled"], 2)
        self.assertEqual((await self.engine.get(cold["id"]))["state"], "archived")
        self.assertEqual((await self.engine.get(used["id"]))["state"], "active")
        self.assertIsNotNone(await self.engine.storage.get_node(cold["id"]))  # never deleted

    async def test_keyed_and_important_memories_survive_decay(self):
        keyed = await self.engine.remember("Default branch is main.", kind="fact", key="repo.default_branch", importance=0.2)
        important = await self.engine.remember("Production DB must never be migrated on Fridays.", kind="convention", importance=0.9)
        await self.engine.decay(rate=5.0, archive_below=0.5)
        self.assertEqual((await self.engine.get(keyed["id"]))["state"], "active")
        self.assertEqual((await self.engine.get(important["id"]))["state"], "active")

    async def test_forget_is_soft_and_keeps_provenance(self):
        item = await self.engine.remember("Wrong: the API key is abc123.", kind="fact")
        self.assertTrue(await self.engine.forget(item["id"], reason="contained a secret"))
        self.assertEqual(await self.engine.recall("API key"), [])
        node = await self.engine.storage.get_node(item["id"])
        self.assertEqual(node.consolidation_state.value, "evicted")
        self.assertEqual(node.metadata["forgotten_reason"], "contained a secret")
        self.assertFalse(await self.engine.forget("does-not-exist"))

    async def test_validity_window_and_as_of(self):
        await self.engine.remember("Sprint goal: ship search.", kind="fact", valid_from="2026-09-01T00:00:00Z", valid_to="2026-09-14T00:00:00Z")
        self.assertEqual(len(await self.engine.recall("sprint goal", as_of="2026-09-10T00:00:00Z")), 1)
        self.assertEqual(len(await self.engine.recall("sprint goal", as_of="2026-10-01T00:00:00Z")), 0)
        with self.assertRaises(ValueError):
            await self.engine.remember("bad window", valid_from="2026-09-14T00:00:00Z", valid_to="2026-09-01T00:00:00Z")
        with self.assertRaises(ValueError):
            await self.engine.recall("x", as_of="not-a-time")

    async def test_derived_from_links_and_rejects_unknown_parents(self):
        a = await self.engine.remember("Meeting: we chose Postgres over Mongo for the ledger.", kind="decision")
        b = await self.engine.remember("Summary: ledger uses Postgres.", kind="fact", derived_from=[a["id"]])
        self.assertEqual((await self.engine.get(b["id"]))["derived_from"], [a["id"]])
        with self.assertRaises(ValueError):
            await self.engine.remember("orphan", derived_from=["nope"])

    async def test_input_validation(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                await self.engine.remember(bad)
        with self.assertRaises(ValueError):
            await self.engine.remember("x" * 9000)
        with self.assertRaises(ValueError):
            await self.engine.remember("ok", importance=1.5)
        with self.assertRaises(ValueError):
            await self.engine.recall("")

    async def test_sessions_are_isolated(self):
        await self.engine.remember("Only agent A knows this: the launch code word is heron.")
        other = MemoryEngine(self.db, session_key="agent-b", clock=self.clock)
        try:
            self.assertEqual(await other.recall("launch code word"), [])
            self.assertEqual((await other.stats())["memories"], 0)
            self.assertIsNone(await other.get((await self.engine.list_recent())[0]["id"]))
        finally:
            pass  # same file; closing one connection is enough at teardown

    async def test_persistence_survives_reopen(self):
        item = await self.engine.remember("Persisted fact: the office wifi is Heron-5G.", kind="fact")
        self.engine.close()
        reopened = MemoryEngine(self.db, session_key="agent-a", clock=self.clock)
        try:
            hits = await reopened.recall("office wifi")
            self.assertEqual(hits[0]["id"], item["id"])
        finally:
            reopened.close()
            self.engine = MemoryEngine(self.db, session_key="agent-a", clock=self.clock)

    async def test_export_claims_denies_private_memories_without_payload(self):
        await self.engine.remember("Staging token lives in the vault at secrets/staging.", kind="fact", private=True)
        public = await self.engine.remember("Staging runs on Railway.", kind="fact")
        out = await self.engine.export_claims("staging")
        self.assertEqual(out["allowed"], 1)
        self.assertEqual(out["denied"], 1)
        denied = [e for e in out["exports"] if not e["claims"]][0]
        self.assertEqual(denied["trace"]["policy_status"], "denied")
        self.assertNotIn("vault", json.dumps(denied))
        allowed = [e for e in out["exports"] if e["claims"]][0]
        self.assertEqual(allowed["claims"][0]["content"]["text"], "Staging runs on Railway.")
        self.assertEqual(allowed["claims"][0]["failure_domains"], ["workstation-1"])
        self.assertEqual(allowed["claims"][0]["lineage_root_ids"], [public["id"]])  # an origin is its own root
        self.assertEqual(allowed["trace"]["memory_ids"], [public["id"]])

    async def test_entity_extraction_covers_handles_and_paths(self):
        ents = extract_entities("Ping @nurman about NeuralGraph/coordination/core.py before Friday.", ("Explicit Thing",))
        self.assertIn("@nurman"[1:], ents)
        self.assertIn("NeuralGraph/coordination/core.py", ents)
        self.assertIn("explicit thing", ents)
        self.assertEqual(normalize_text("  a \n b  "), "a b")


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = MemoryEngine(Path(self.tmp.name) / "m.db", session_key="t", clock=_Clock())
        mcp_server.set_engine(self.engine)
        self.server = mcp_server.build_server()

    async def asyncTearDown(self):
        mcp_server.set_engine(None)
        self.engine.close()
        self.tmp.cleanup()

    async def test_tool_surface(self):
        names = [t.name for t in await self.server.list_tools()]
        self.assertEqual(names, ["remember", "recall", "get_memory", "forget", "list_recent", "decay", "memory_stats", "export_claims"])
        self.assertEqual([p.name for p in await self.server.list_prompts()], ["memory_policy"])
        remember = next(t for t in await self.server.list_tools() if t.name == "remember")
        self.assertEqual(remember.input_schema["required"], ["text"])
        for name in ("key", "tags", "private", "derived_from", "valid_from"):
            self.assertIn(name, remember.input_schema["properties"])

    async def test_remember_recall_round_trip_through_the_protocol(self):
        r = await self.server.call_tool("remember", {"text": "Ali prefers dark mode in every editor.", "kind": "preference", "key": "user.theme", "tags": ["ui"]})
        self.assertFalse(r.is_error)
        self.assertEqual(r.structured_content["key"], "user.theme")
        r2 = await self.server.call_tool("recall", {"query": "what theme does Ali like", "explain": True})
        self.assertEqual(r2.structured_content["count"], 1)
        self.assertEqual(r2.structured_content["memories"][0]["why"]["shared_entities"], ["ali"])
        r3 = await self.server.call_tool("memory_stats", {})
        self.assertEqual(r3.structured_content["by_kind"], {"preference": 1})

    async def test_validation_errors_reach_the_client_with_their_message(self):
        from mcp.server.mcpserver.exceptions import ToolError

        with self.assertRaises(ToolError) as ctx:
            await self.server.call_tool("remember", {"text": "   "})
        self.assertIn("text must not be empty", str(ctx.exception))

    def test_client_configs(self):
        claude = json.loads(mcp_server.client_config("claude", python="/usr/bin/python3.11", cwd="/repo"))
        self.assertEqual(claude["mcpServers"]["neuralgraph"]["args"], ["-m", "NeuralGraph.mcp.server"])
        self.assertEqual(claude["mcpServers"]["neuralgraph"]["cwd"], "/repo")
        codex = mcp_server.client_config("codex", python="/usr/bin/python3.11", cwd="/repo")
        self.assertIn("[mcp_servers.neuralgraph]", codex)
        self.assertIn('args = ["-m", "NeuralGraph.mcp.server"]', codex)
        with self.assertRaises(ValueError):
            mcp_server.client_config("cursor")
        project = json.loads(Path(__file__).resolve().parents[2].joinpath(".mcp.json").read_text())
        self.assertEqual(project["mcpServers"]["neuralgraph"]["args"], ["-m", "NeuralGraph.mcp.server"])


if __name__ == "__main__":
    unittest.main()
