"""Schema migrations: a version-1 database (rules without the composition columns) opens as version 2 with the
documented defaults, re-opens as a no-op, and a database from a newer version refuses to open."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from mycelic.store import SCHEMA_VERSION, MycelicStore

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
            self.assertEqual(SCHEMA_VERSION, 2)
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
            self.assertEqual(again.get_meta("schema_version"), "2")
            self.assertEqual(again.get_rule("legacy").sources, ["agent_observation"])
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
