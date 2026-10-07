"""Schema migrations: a version-1 database (rules without the composition columns), a version-2 database (labels as
agents wrote them, no log-applied state) and a version-3 database (no per-row digests) open as the current version with
the documented defaults, canonical labels and digest columns left NULL for the start-up backfill, re-open as a no-op,
roll back as a whole when interrupted, and a database from a newer version refuses to open."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mycelic.integrity import check_memory
from mycelic.models import Memory
from mycelic.store import SCHEMA_VERSION, MycelicStore, row_memory

from .helpers import V2_SCHEMA, V3_SCHEMA

V1_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE rules (
    rule_id TEXT PRIMARY KEY, org_id TEXT, target_layer TEXT NOT NULL, required_slots TEXT NOT NULL,
    conclusion TEXT NOT NULL, topic_prefix TEXT, min_agents INTEGER NOT NULL DEFAULT 2, min_teams INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL DEFAULT 'risk', enabled INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
);
INSERT INTO meta(key, value) VALUES ('schema_version', '1');
INSERT INTO rules(rule_id, org_id, target_layer, required_slots, conclusion, topic_prefix, min_agents, min_teams, kind, enabled, metadata, updated_at)
VALUES ('legacy', NULL, 'team', '["a","b"]', 'x', 'ops', 2, 1, 'risk', 1, '{}', '2026-01-01T00:00:00+00:00');
"""

T0 = "2026-09-01T00:00:00+00:00"
TEAM = "northwind/emea/nw-gmbh/ops/logistics"

# a schema-2 database as the released code left it: labels exactly as agents and operators wrote them
V2_ROWS = f"""
INSERT INTO orgs VALUES ('northwind', 'northwind', '{T0}');
INSERT INTO agents(agent_id, org_id, display_name, path, scopes, key_hash, key_prefix, status, created_at, metadata) VALUES
  ('log-1', 'northwind', 'log-1', '{TEAM}/log-1', '[]', 'h1', 'p1', 'active', '{T0}', '{{}}'),
  ('log-2', 'northwind', 'log-2', '{TEAM}/log-2', '[]', 'h2', 'p2', 'revoked', '{T0}', '{{}}');
INSERT INTO memories(memory_id, org_id, layer, scope, text, topic, slot, entity, kind, confidence, producer_id, operator,
                     agg_key, created_at, applied_at) VALUES
  ('mem_a', 'northwind', 'agent', '{TEAM}/log-1', 'strike', ' Supply:SD-9/Transport ', 'Transport_Disruption', 'SD-9',
   'observation', 0.9, 'log-1', 'agent_observation', NULL, '{T0}', '{T0}'),
  ('mem_b', 'northwind', 'agent', '{TEAM}/log-1', 'strike again', 'SUPPLY:SD-9/TRANSPORT', 'transport_disruption', 'sd-9',
   'observation', 0.7, 'log-1', 'agent_observation', NULL, '{T0}', '{T0}'),
  ('mem_d1', 'northwind', 'team', '{TEAM}', 'consolidated', 'Supply:SD-9/Transport', NULL, 'SD-9',
   'fact', 0.9, 'mycelic', 'topic_consolidation', 'Supply:SD-9/Transport', '{T0}', '{T0}'),
  ('mem_c', 'northwind', 'agent', '{TEAM}/log-2', 'pallets', 'Ops:Pallets', NULL, NULL,
   'observation', 0.5, 'log-2', 'agent_observation', NULL, '{T0}', NULL);
INSERT INTO rules(rule_id, org_id, target_layer, required_slots, conclusion, topic_prefix, min_agents, min_teams, kind,
                  enabled, metadata, updated_at, sources, emits_slot, emits_topic, min_units, corroborate) VALUES
  ('mixed', NULL, 'region', '["Transport_Disruption","transport_disruption","Risk:A"]',
   'T {{slot:Transport_Disruption}} R {{slot:Risk:A}} for {{entity}}', 'Supply:', 2, 1, 'risk', 1, '{{}}', '{T0}',
   '["agent_observation"]', 'Supply_Risk', 'Strategy:Supply-Risk',
   '{{"Transport_Disruption": {{"region": 2}}, "transport_disruption": {{"region": 3, "team": 1}}}}', 1);
"""


# a schema-3 database as the released code left it: raw rows (one retracted), a team consolidation and its edges
V3_ROWS = f"""
INSERT INTO orgs VALUES ('northwind', 'northwind', '{T0}');
INSERT INTO agents(agent_id, org_id, display_name, path, scopes, key_hash, key_prefix, status, created_at, metadata, log_status) VALUES
  ('log-1', 'northwind', 'log-1', '{TEAM}/log-1', '[]', 'h1', 'p1', 'active', '{T0}', '{{}}', 'active'),
  ('log-2', 'northwind', 'log-2', '{TEAM}/log-2', '[]', 'h2', 'p2', 'active', '{T0}', '{{}}', 'active');
INSERT INTO memories(memory_id, org_id, layer, scope, text, topic, slot, entity, kind, confidence, producer_id, operator,
                     agg_key, event_id, status, created_at, applied_at, source_event_ids, metadata, apply_seq) VALUES
  ('mem_a', 'northwind', 'agent', '{TEAM}/log-1', 'Streik in Rotterdam ✓', 'supply:sd-9/transport', 'transport_disruption', 'sd-9',
   'observation', 0.9, 'log-1', 'agent_observation', NULL, 'evt_a', 'active', '{T0}', '{T0}', '["evt_x"]', '{{"source": "erp"}}', 1),
  ('mem_r', 'northwind', 'agent', '{TEAM}/log-1', 'false alarm', 'supply:sd-9/transport', NULL, 'sd-9',
   'observation', 0.4, 'log-1', 'agent_observation', NULL, 'evt_r', 'retracted', '{T0}', '{T0}', '[]',
   '{{"status_reason": "withdrawn"}}', 2),
  ('mem_b', 'northwind', 'agent', '{TEAM}/log-2', 'carrier delay', 'supply:sd-9/transport', 'transport_disruption', 'sd-9',
   'observation', 0.7, 'log-2', 'agent_observation', NULL, 'evt_b', 'active', '{T0}', '{T0}', '[]', '{{}}', 3),
  ('mem_d1', 'northwind', 'team', '{TEAM}', 'consolidated', 'supply:sd-9/transport', 'transport_disruption', 'sd-9',
   'observation', 0.97, 'mycelic', 'topic_consolidation', 'supply:sd-9/transport', 'evt_d1', 'active', '{T0}', '{T0}', '[]',
   '{{"agg_key": "supply:sd-9/transport", "roots": ["mem_a", "mem_b"], "children": ["{TEAM}/log-1", "{TEAM}/log-2"],
      "version_of": null}}', 4);
INSERT INTO lineage_edges VALUES ('mem_d1', 'mem_a', 'log-1', 'agent', '{T0}'), ('mem_d1', 'mem_b', 'log-2', 'agent', '{T0}');
INSERT INTO meta(key, value) VALUES ('last_applied_seq', '7');
"""
DIGEST_COLUMNS = {"digest", "digest_key_id", "digest_origin"}


def table(store: MycelicStore, sql: str) -> list[dict]:
    return [dict(r) for r in store._conn.execute(sql).fetchall()]


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "v1.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_v1_database_migrates_to_current_schema(self) -> None:
        c = sqlite3.connect(self.db)
        c.executescript(V1_DDL)
        c.close()
        store = MycelicStore(self.db)
        try:
            cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(rules)").fetchall()}
            self.assertTrue({"sources", "emits_slot", "emits_topic", "min_units", "corroborate"} <= cols)
            self.assertEqual(store.get_meta("schema_version"), str(SCHEMA_VERSION))
            self.assertEqual(SCHEMA_VERSION, 4)
            rule = store.get_rule("legacy")
            self.assertEqual(rule.required_slots, ["a", "b"])
            self.assertEqual(rule.sources, ["agent_observation"])
            self.assertIsNone(rule.emits_slot)
            self.assertIsNone(rule.emits_topic)
            self.assertEqual(rule.min_units, {})
            self.assertFalse(rule.corroborate)
        finally:
            store._conn.close()
        again = MycelicStore(self.db)
        try:
            self.assertEqual(again.get_meta("schema_version"), "4")
            self.assertEqual(again.get_rule("legacy").sources, ["agent_observation"])
        finally:
            again._conn.close()

    def write_v2(self, rows: str = "") -> None:
        c = sqlite3.connect(self.db)
        c.executescript(V2_SCHEMA + rows)
        c.close()

    def raw(self, sql: str) -> list[tuple]:
        c = sqlite3.connect(self.db)
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def test_v2_database_canonicalises_and_records_applied_state(self) -> None:
        self.write_v2(V2_ROWS)
        store = MycelicStore(self.db)
        try:
            self.assertEqual(store.get_meta("schema_version"), "4")
            self.assertEqual(store.get_meta("reaggregate_pending"), "1")
            rows = {m.memory_id: m for m in store.list_memories("northwind", status=None, limit=100)}
            labels = {mid: (m.topic, m.slot, m.entity) for mid, m in rows.items()}
            self.assertEqual(labels["mem_a"], ("supply:sd-9/transport", "transport_disruption", "sd-9"))
            self.assertEqual(labels["mem_b"], labels["mem_a"], "one agent's case variants share a topic")
            self.assertEqual(labels["mem_c"], ("ops:pallets", None, None))
            self.assertEqual(labels["mem_d1"], ("Supply:SD-9/Transport", None, "SD-9"), "derived rows keep the key their id embeds")
            self.assertEqual(table(store, "SELECT agg_key FROM memories WHERE memory_id='mem_d1'"), [{"agg_key": "Supply:SD-9/Transport"}])
            seqs = {r["memory_id"]: (r["rid"], r["apply_seq"]) for r in table(store, "SELECT memory_id, rid, apply_seq FROM memories")}
            for mid in ("mem_a", "mem_b", "mem_d1"):
                self.assertEqual(seqs[mid][1], seqs[mid][0], "applied rows keep their order")
            self.assertIsNone(seqs["mem_c"][1], "a row the consumer has not applied has no position yet")
            rule = store.get_rule("mixed")
            self.assertEqual(rule.required_slots, ["transport_disruption", "risk:a"])
            self.assertEqual(rule.min_units, {"transport_disruption": {"region": 3, "team": 1}})
            self.assertEqual(rule.conclusion, "T {slot:transport_disruption} R {slot:risk:a} for {entity}")
            self.assertEqual((rule.topic_prefix, rule.emits_slot, rule.emits_topic), ("supply:", "supply_risk", "strategy:supply-risk"))
            self.assertTrue(rule.corroborate)
            self.assertEqual(store.get_applied_rule("mixed").to_dict(), rule.to_dict())
            self.assertEqual(table(store, "SELECT rule_id, applied_seq FROM applied_rules"), [{"rule_id": "mixed", "applied_seq": 0}])
            self.assertEqual({r["agent_id"]: (r["status"], r["log_status"]) for r in table(store, "SELECT * FROM agents")},
                             {"log-1": ("active", "active"), "log-2": ("revoked", "revoked")})
            self.assertEqual(store.child_units("northwind", TEAM), [f"{TEAM}/log-1"])
            indexes = {r["name"] for r in table(store, "PRAGMA index_list(memories)")}
            self.assertTrue({"idx_memories_apply_seq", "idx_memories_org_op_status"} <= indexes)
            self.assertEqual({(r["digest"], r["digest_key_id"], r["digest_origin"]) for r in table(store, "SELECT * FROM memories")},
                             {(None, None, None)}, "migrated rows wait for the start-up backfill")

            async def later() -> None:
                async with store.transaction() as tx:
                    tx.set_applied("mem_c", T0)
                    tx.set_applied("mem_c", T0)
                    tx.insert_memory(Memory(memory_id="mem_e", org_id="northwind", layer="agent", scope=f"{TEAM}/log-1",
                                            text="new", topic="ops:pallets", slot=None, entity=None, kind="observation",
                                            confidence=0.5, support=1, independent_teams=1, producer_id="log-1",
                                            operator="agent_observation", rule_id=None, event_id=None, created_at=T0,
                                            applied_at=T0))
                    tx.set_memory_status("mem_d1", "retracted")
                    tx.reactivate_memory("mem_d1", applied_at=T0, metadata={})

            asyncio.run(later())
            seqs = {r["memory_id"]: r["apply_seq"] for r in table(store, "SELECT memory_id, apply_seq FROM memories")}
            self.assertEqual((seqs["mem_c"], seqs["mem_e"]), (4, 5), "set_applied assigns once, after every migrated row")
            [new] = table(store, "SELECT digest_key_id, digest_origin FROM memories WHERE digest IS NOT NULL")
            self.assertEqual(new, {"digest_key_id": "none", "digest_origin": "write"}, "a row inserted after the upgrade is signed")
            self.assertEqual(seqs["mem_d1"], 3, "reactivation keeps the original position")
            before = (table(store, "SELECT * FROM memories ORDER BY rid"), table(store, "SELECT * FROM rules"),
                      table(store, "SELECT * FROM applied_rules"), table(store, "SELECT * FROM agents"))
        finally:
            store._conn.close()
        again = MycelicStore(self.db)
        try:
            self.assertEqual((table(again, "SELECT * FROM memories ORDER BY rid"), table(again, "SELECT * FROM rules"),
                              table(again, "SELECT * FROM applied_rules"), table(again, "SELECT * FROM agents")), before,
                             "opening a migrated database again changes nothing")
        finally:
            again._conn.close()

    def test_v1_database_migrates_through_every_version(self) -> None:
        c = sqlite3.connect(self.db)
        c.executescript(V1_DDL)
        c.execute("""INSERT INTO rules(rule_id, org_id, target_layer, required_slots, conclusion, topic_prefix, min_agents, min_teams,
                     kind, enabled, metadata, updated_at) VALUES ('Upper', 'northwind', 'department', '["A","b"]',
                     '{slot:A} and {slot:B}', 'Ops:', 2, 1, 'risk', 1, '{}', ?)""", (T0,))
        c.commit()
        c.close()
        store = MycelicStore(self.db)
        try:
            self.assertEqual(store.get_meta("schema_version"), "4")
            self.assertEqual(store.get_meta("reaggregate_pending"), "1")
            upper = store.get_rule("Upper")
            self.assertEqual((upper.required_slots, upper.conclusion, upper.topic_prefix), (["a", "b"], "{slot:a} and {slot:b}", "ops:"))
            self.assertEqual(upper.sources, ["agent_observation"])
            self.assertEqual([r.rule_id for r in store.list_applied_rules("northwind")], ["Upper", "legacy"])
            self.assertEqual(store.get_applied_rule("legacy").to_dict(), store.get_rule("legacy").to_dict())
            self.assertIn("log_status", {r["name"] for r in table(store, "PRAGMA table_info(agents)")})
            self.assertIn("apply_seq", {r["name"] for r in table(store, "PRAGMA table_info(memories)")})
            self.assertTrue(DIGEST_COLUMNS <= {r["name"] for r in table(store, "PRAGMA table_info(memories)")})
        finally:
            store._conn.close()
        again = MycelicStore(self.db)
        try:
            self.assertEqual(again.get_meta("schema_version"), "4")
            self.assertEqual(again.get_rule("Upper").required_slots, ["a", "b"])
        finally:
            again._conn.close()

    def test_interrupted_migration_rolls_back_and_reruns(self) -> None:
        self.write_v2(V2_ROWS)
        before = self.raw("SELECT memory_id, topic, slot, entity FROM memories ORDER BY rid")
        with mock.patch("mycelic.store.canonical_label", side_effect=RuntimeError("injected fault")):
            with self.assertRaises(RuntimeError):
                MycelicStore(self.db)
        self.assertEqual(self.raw("SELECT value FROM meta WHERE key='schema_version'"), [("2",)])
        self.assertEqual(self.raw("SELECT memory_id, topic, slot, entity FROM memories ORDER BY rid"), before)
        self.assertNotIn("log_status", {r[1] for r in self.raw("PRAGMA table_info(agents)")})
        self.assertNotIn("apply_seq", {r[1] for r in self.raw("PRAGMA table_info(memories)")})
        self.assertFalse(DIGEST_COLUMNS & {r[1] for r in self.raw("PRAGMA table_info(memories)")})
        self.assertEqual(self.raw("SELECT * FROM meta WHERE key='reaggregate_pending'"), [])
        store = MycelicStore(self.db)
        try:
            self.assertEqual(store.get_meta("schema_version"), "4")
            self.assertEqual(store.get_memory("mem_a").topic, "supply:sd-9/transport")
            self.assertEqual(store.get_applied_rule("mixed").required_slots, ["transport_disruption", "risk:a"])
        finally:
            store._conn.close()

    def test_empty_v2_database_migrates(self) -> None:
        self.write_v2()
        store = MycelicStore(self.db)
        try:
            self.assertEqual(store.get_meta("schema_version"), "4")
            self.assertEqual(store.get_meta("reaggregate_pending"), "1")
            self.assertEqual(store.list_applied_rules("northwind"), [])
            self.assertEqual(store.max_apply_seq(), 0)
            self.assertIn("idx_memories_apply_seq", {r["name"] for r in table(store, "PRAGMA index_list(memories)")})
        finally:
            store._conn.close()

    def test_v3_to_v4_adds_integrity_columns(self) -> None:
        c = sqlite3.connect(self.db)
        c.executescript(V3_SCHEMA + V3_ROWS)
        # a fault after the columns were added (the version update aborts): the whole step rolls back
        c.executescript("CREATE TRIGGER fault BEFORE UPDATE ON meta WHEN NEW.key='schema_version' "
                        "BEGIN SELECT RAISE(ABORT, 'injected fault'); END;")
        c.close()
        before = self.raw("SELECT * FROM memories ORDER BY rid")
        with self.assertRaises(sqlite3.DatabaseError):
            MycelicStore(self.db)
        self.assertEqual(self.raw("SELECT value FROM meta WHERE key='schema_version'"), [("3",)])
        self.assertFalse(DIGEST_COLUMNS & {r[1] for r in self.raw("PRAGMA table_info(memories)")})
        self.assertEqual(self.raw("SELECT * FROM memories ORDER BY rid"), before)
        c = sqlite3.connect(self.db)
        c.executescript("DROP TRIGGER fault;")
        c.close()

        store = MycelicStore(self.db)
        try:
            self.assertEqual(store.get_meta("schema_version"), "4")
            self.assertTrue(DIGEST_COLUMNS <= {r["name"] for r in table(store, "PRAGMA table_info(memories)")})
            rows = table(store, "SELECT memory_id, digest, digest_key_id, digest_origin FROM memories ORDER BY rid")
            self.assertEqual([r["memory_id"] for r in rows], ["mem_a", "mem_r", "mem_b", "mem_d1"])
            self.assertEqual({(r["digest"], r["digest_key_id"], r["digest_origin"]) for r in rows}, {(None, None, None)})
            self.assertIsNone(store.get_meta("integrity_backfill_complete"), "the backfill decides at start, with the key")
            self.assertIsNone(store.get_meta("reaggregate_pending"), "digests change nothing that was derived")
            self.assertEqual(store.get_meta("last_applied_seq"), "7")
            self.assertEqual(store.backfill_bound(), None, "nothing was signed at insert yet")
            before = (table(store, "SELECT * FROM memories ORDER BY rid"), table(store, "SELECT * FROM meta ORDER BY key"),
                      table(store, "SELECT * FROM lineage_edges ORDER BY child_id, parent_id"))
        finally:
            store._conn.close()
        again = MycelicStore(self.db)
        try:
            self.assertEqual((table(again, "SELECT * FROM memories ORDER BY rid"), table(again, "SELECT * FROM meta ORDER BY key"),
                              table(again, "SELECT * FROM lineage_edges ORDER BY child_id, parent_id")), before,
                             "opening a migrated database again changes nothing")

            async def backfill() -> tuple[int, int]:
                async with again.transaction() as tx:
                    return tx.backfill_digests(after_rid=0, below_rid=None, limit=10)

            # the legacy derived row lacks most derivation keys: they read as null, so it is signed and checks
            self.assertEqual(asyncio.run(backfill()), (4, 4))
            for r in again._conn.execute("SELECT * FROM memories ORDER BY rid"):
                parents = [e.parent_id for e in again.parents_of(r["memory_id"])]
                with self.subTest(memory=r["memory_id"]):
                    self.assertEqual((r["digest_key_id"], r["digest_origin"]), ("none", "backfill"))
                    self.assertEqual(check_memory(again.keyring, row_memory(r), parents, r["digest"], r["digest_key_id"],
                                                  r["digest_origin"]), "ok")
            self.assertEqual(asyncio.run(backfill()), (0, 0), "a signed row is never signed again")
        finally:
            again._conn.close()

    def test_newer_schema_refuses_to_open(self) -> None:
        c = sqlite3.connect(self.db)
        c.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL); INSERT INTO meta VALUES ('schema_version', '99');")
        c.close()
        with self.assertRaises(RuntimeError):
            MycelicStore(self.db)


if __name__ == "__main__":
    unittest.main()
