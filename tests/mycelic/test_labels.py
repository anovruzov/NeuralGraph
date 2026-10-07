"""Label normalisation: topics, slots and entities that mean the same thing aggregate together whatever case, width or
spacing an agent wrote them in; rule labels and template placeholders are held to the same spelling; the result is
a fixed point, and scripts other than Latin are kept, not stripped."""
from __future__ import annotations

import itertools
import json
import sqlite3
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from mycelic.api import create_app
from mycelic.metrics import Metrics
from mycelic.models import canonical_label
from mycelic.service import MycelicService, ValidationError

from .helpers import DEMO_RULE, V2_SCHEMA, ServiceHarness, settings

#: at least 35 code points that stress NFKC and case folding: combining marks, dotted/dotless i (U+0130, U+0131),
#: U+01F0 (folds to two code points), U+0345 (iota subscript), sharp s (U+00DF, U+1E9E), full-width letters, U+00A8
#: (compatibility-decomposes to a space and a mark), NBSP, U+3000, Cyrillic, CJK, half-width katakana and more
POOL = ["a", "A", "i", "I", "j", "J", "s", "-", ":", " ", "\t",
        "\u0301", "\u0307", "\u0308", "\u030c", "\u0345",
        "\u0130", "\u0131", "\u01f0", "ß", "\u1e9e", "\uff21", "\uff4a", "\ufb01", "\u00a8", "\u00a0", "\u3000",
        "\u03a3", "\u03c2", "\u2126", "\u212a", "\u0390", "\u1f80", "\u1fb3", "\u200b",
        "й", "Й", "中", "\uff76", "\uff9e", "\u3131"]

TRANSPORT = " SUPPLY:SD-9/Transport "


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class CanonicalLabelTests(unittest.TestCase):
    def test_canonical_label_is_idempotent_over_sequences(self) -> None:
        self.assertGreaterEqual(len(set(POOL)), 35)
        broken = []
        for n in (1, 2, 3):
            for chars in itertools.product(POOL, repeat=n):
                once = canonical_label("".join(chars))
                if once is not None and canonical_label(once) != once:
                    broken.append("".join(chars))
        self.assertEqual(broken, [], "canonical_label must be a fixed point")
        self.assertEqual(canonical_label(" Supply:SD-9/Transport "), "supply:sd-9/transport")
        self.assertEqual(canonical_label("Straße"), "strasse")
        self.assertEqual(canonical_label("\uff33\uff24\uff0d\uff19"), "sd-9", "full-width '\uff33\uff24\uff0d\uff19'")
        self.assertEqual(canonical_label("a \t\u00a0\u3000 b"), "a b")
        for blank in ("", " ", "\t\n", "\u3000\u00a0"):
            self.assertIsNone(canonical_label(blank))
        self.assertIsNone(canonical_label(None))
        self.assertEqual(canonical_label("Поставки:Транспорт"), "поставки:транспорт")
        self.assertEqual(canonical_label("供应链:运输"), "供应链:运输")
        with self.assertRaises(TypeError):
            canonical_label(9)  # type: ignore[arg-type]


class LabelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.h = await ServiceHarness().start()
        for a in ("log-1", "log-2"):
            await self.h.register(a, team="logistics")
        await self.h.register("proc-1", team="procurement")
        await self.h.register("sales-1", team="field-sales", department="commercial")

    async def asyncTearDown(self) -> None:
        await self.h.close()

    async def test_mixed_case_and_whitespace_labels_compose(self) -> None:
        s = self.h.service
        await s.upsert_rule(DEMO_RULE)
        await self.h.observe("log-1", "Port strike; SD-9 shipments route through it.", topic=TRANSPORT,
                             slot="Transport_Disruption", entity="SD-9", confidence=0.9)
        await self.h.observe("proc-1", "Two weeks of SD-9 inventory left.", topic="supply:sd-9/supplier",
                             slot="supplier_buffer_low", entity="sd-9 ", confidence=0.85)
        await self.h.observe("sales-1", "40 RX-4 arms committed.", topic="Supply:sd-9/demand",
                             slot="DEMAND_COMMITMENT", entity="Sd-9", confidence=0.8)
        await self.h.settle()
        conclusions = [m for m in s.store.list_memories("northwind", layers=["enterprise"])
                       if m.rule_id == DEMO_RULE["rule_id"]]
        self.assertEqual(len(conclusions), 1)
        self.assertEqual(conclusions[0].entity, "sd-9")
        self.assertEqual(conclusions[0].support, 3)
        self.assertEqual(conclusions[0].topic, "supply:")
        raw = s.store.list_memories("northwind", layers=["agent"])
        self.assertEqual({m.entity for m in raw}, {"sd-9"})
        self.assertEqual({m.topic for m in raw}, {"supply:sd-9/transport", "supply:sd-9/supplier", "supply:sd-9/demand"})

    async def test_query_and_list_filters_are_canonicalised(self) -> None:
        client = TestClient(TestServer(create_app(self.h.service)))
        await client.start_server()
        try:
            key = self.h.keys["log-1"]
            r = await client.post("/memory", json={"text": "Terminal 3 strike announced for weeks 41-43.",
                                                   "topic": " Supply:SD-9/Transport ", "entity": "SD-9"}, headers=bearer(key))
            self.assertEqual(r.status, 202, await r.text())
            mid = (await r.json())["memory_id"]
            r = await client.get(f"/memory/{mid}", headers=bearer(key))
            self.assertEqual(r.status, 200)
            got = (await r.json())["memory"]
            self.assertEqual((got["topic"], got["entity"]), ("supply:sd-9/transport", "sd-9"))
            await self.h.settle()
            r = await client.post("/query", json={"query": "terminal strike", "scope": "northwind",
                                                  "topic": "SUPPLY:SD-9/TRANSPORT ", "entity": " Sd-9"}, headers=bearer(key))
            self.assertEqual(r.status, 200, await r.text())
            self.assertEqual((await r.json())["answer"]["memory_id"], mid)
            r = await client.post("/query", json={"query": "terminal strike", "scope": "northwind",
                                                  "topic": "supply:sd-9/supplier"}, headers=bearer(key))
            self.assertIsNone((await r.json())["answer"], "the filter is still exact after normalisation")
            r = await client.get("/memories", params={"scope": "northwind/emea/nw-gmbh/ops/logistics"}, headers=bearer(key))
            self.assertEqual([m["topic"] for m in (await r.json())["memories"]], ["supply:sd-9/transport"])
            r = await client.post("/query", json={"query": "x", "scope": "northwind", "topic": "\ufdfa" * 12},
                                  headers=bearer(key))
            self.assertEqual(r.status, 400, "a filter longer than 200 characters once normalised is refused")
            self.assertIn("'topic'", (await r.json())["error"])
        finally:
            await client.close()

    async def test_slot_invalid_after_normalisation_is_rejected(self) -> None:
        s = self.h.service
        p = self.h.principal("log-1")
        for body, field in (({"slot": "Transport Disruption"}, "'slot'"), ({"slot": "транспорт"}, "'slot'"),
                            ({"topic": "\ufdfa" * 12}, "'topic'"), ({"entity": "\ufdfa" * 12}, "'entity'"),
                            ({"slot": "\u33a1" * 60}, "'slot'")):
            with self.subTest(body=body):
                with self.assertRaises(ValidationError) as ctx:
                    await s.ingest_memory(p, {"text": "x", **body})
                self.assertIn(field, str(ctx.exception))
        m, _ = await s.ingest_memory(p, {"text": "blank labels", "topic": "   ", "entity": " \u3000", "slot": "\t"})
        self.assertEqual((m.topic, m.slot, m.entity), (None, None, None))
        base = {"rule_id": "r", "target_layer": "team", "required_slots": ["a"], "conclusion": "x"}
        for body, field in (({"required_slots": ["Transport Disruption"]}, "'required_slots'"),
                            ({"required_slots": ["a", " "]}, "'required_slots'"),
                            ({"emits_slot": "Supply Risk"}, "'emits_slot'"),
                            ({"emits_slot": "A"}, "'emits_slot'")):
            with self.subTest(rule=body):
                with self.assertRaises(ValidationError) as ctx:
                    await s.upsert_rule({**base, **body})
                self.assertIn(field, str(ctx.exception))
        rule = await s.upsert_rule({**base, "required_slots": ["Risk:A", "risk:a ", "B"],
                                    "min_units": {"Risk:A": {"team": 1, "region": 2}, "risk:a": {"team": 2}}})
        self.assertEqual(rule.required_slots, ["risk:a", "b"], "duplicates after normalisation collapse in order")
        self.assertEqual(rule.min_units, {"risk:a": {"team": 2, "region": 2}}, "colliding slots keep the larger count")

    async def test_template_placeholders_and_fallback_topic_are_canonical(self) -> None:
        s = self.h.service
        await s.upsert_rule({"rule_id": "Tpl_Rule", "target_layer": "team", "required_slots": ["Transport_Disruption", "risk:transport"],
                             "conclusion": "Strike: {slot:Transport_Disruption} / risk: {slot:Risk:Transport} ({entity})",
                             "min_agents": 2})
        self.assertEqual(s.store.get_rule("Tpl_Rule").conclusion,
                         "Strike: {slot:transport_disruption} / risk: {slot:risk:transport} ({entity})")
        await self.h.observe("log-1", "terminal 3 is closed", slot="transport_disruption", entity="sd-9", confidence=0.9)
        await self.h.observe("log-2", "carrier risk is high", slot="RISK:TRANSPORT", entity="SD-9", confidence=0.8)
        await self.h.settle()
        [m] = [m for m in s.store.list_memories("northwind", layers=["team"]) if m.rule_id == "Tpl_Rule"]
        self.assertEqual(m.text, "Strike: terminal 3 is closed / risk: carrier risk is high (sd-9)")
        self.assertNotIn("missing", m.text)
        self.assertEqual(m.topic, "tpl_rule", "the fallback topic is the rule id as a label")
        # cycle detection compares the same canonical topic: Feeder's conclusions (topic 'feeder') feed c
        await s.upsert_rule({"rule_id": "Feeder", "target_layer": "team", "required_slots": ["y"], "emits_slot": "x",
                             "sources": ["agent_observation", "slot_composition"], "conclusion": "F"})
        with self.assertRaises(ValidationError):
            await s.upsert_rule({"rule_id": "c", "target_layer": "team", "required_slots": ["x"], "topic_prefix": "Feeder",
                                 "emits_slot": "y", "sources": ["slot_composition", "agent_observation"], "conclusion": "C"})

    async def test_cyrillic_and_cjk_labels_are_preserved(self) -> None:
        s = self.h.service
        await self.h.observe("log-1", "Забастовка в порту.", topic="Поставки:Транспорт", entity="Деталь-9")
        await self.h.observe("log-2", "Задержка контейнеров.", topic=" поставки:транспорт", entity="ДЕТАЛЬ-9")
        await self.h.observe("log-1", "港口罢工\u3002", topic="供应链:运输", entity="零件九")
        await self.h.observe("log-2", "集装箱延误\u3002", topic="供应链:运输 ", entity="零件九")
        await self.h.settle()
        team = {m.topic: m for m in s.store.list_memories("northwind", layers=["team"])}
        self.assertEqual(set(team), {"поставки:транспорт", "供应链:运输"})
        self.assertEqual(team["поставки:транспорт"].entity, "деталь-9")
        self.assertEqual(team["供应链:运输"].entity, "零件九")
        self.assertEqual({m.support for m in team.values()}, {2})
        with self.assertRaises(ValidationError):
            await s.ingest_memory(self.h.principal("log-1"), {"text": "x", "slot": "поставки"})

    async def test_case_variants_share_a_topic_and_count_the_agent_once(self) -> None:
        s = self.h.service
        await self.h.observe("log-1", "Pallet shortage at dock 4.", topic="Ops:Pallets")
        await self.h.observe("log-1", "Pallet shortage confirmed.", topic="OPS:PALLETS")
        await self.h.observe("log-2", "No pallets left for outbound.", topic="ops:pallets ")
        await self.h.settle()
        team = [m for m in s.store.list_memories("northwind", layers=["team"]) if m.topic == "ops:pallets"]
        self.assertEqual(len(team), 1)
        self.assertEqual(team[0].support, 2, "two notes from log-1 count one agent")
        self.assertEqual(len(team[0].metadata["roots"]), 3)
        self.assertEqual(team[0].metadata["contributing_agents"], ["log-1", "log-2"])

    async def test_rules_file_restart_does_not_churn(self) -> None:
        rule = {"rule_id": "file_rule", "target_layer": "department", "topic_prefix": "Supply:",
                "required_slots": ["Transport_Disruption", "supplier_buffer_low"], "min_agents": 2,
                "min_units": {"Transport_Disruption": {"department": 1}},
                "conclusion": "Risk for {entity}: {slot:Transport_Disruption}; {slot:Supplier_Buffer_Low}."}
        path = Path(self.h.tmp.name) / "rules.json"
        path.write_text(json.dumps({"rules": [rule]}))

        def rule_events(svc: MycelicService) -> int:
            return len(svc.store.list_events("_", kind="rule.upserted", limit=100))

        cfg = settings(self.h.tmp.name, db_path=str(Path(self.h.tmp.name) / "a.db"), rules_file=str(path))
        first = MycelicService(cfg, metrics=Metrics(), transport=self.h.service.transport)
        try:
            self.assertEqual(first.load_rules_file(), 1)
            self.assertEqual(rule_events(first), 1)
            stored = first.store.get_rule("file_rule")
            self.assertEqual(stored.topic_prefix, "supply:")
            self.assertEqual(stored.required_slots, ["transport_disruption", "supplier_buffer_low"])
            self.assertEqual(stored.min_units, {"transport_disruption": {"department": 1}})
        finally:
            await first.store.close()
        restarted = MycelicService(cfg, metrics=Metrics(), transport=self.h.service.transport)
        try:
            self.assertEqual(restarted.load_rules_file(), 0, "the same file at the next start changes nothing")
            self.assertEqual(rule_events(restarted), 1)
        finally:
            await restarted.store.close()
        # a schema-2 database whose rules table holds the file's rule as the old code stored it (labels verbatim)
        db = Path(self.h.tmp.name) / "v2.db"
        c = sqlite3.connect(db)
        c.executescript(V2_SCHEMA)
        c.execute("""INSERT INTO rules(rule_id, org_id, target_layer, required_slots, conclusion, topic_prefix, min_agents,
                     min_teams, kind, enabled, metadata, updated_at, sources, emits_slot, emits_topic, min_units, corroborate)
                     VALUES ('file_rule', NULL, 'department', ?, ?, 'Supply:', 2, 1, 'risk', 1, '{}', '2026-09-01T00:00:00+00:00',
                     '["agent_observation"]', NULL, NULL, ?, 0)""",
                  (json.dumps(rule["required_slots"]), rule["conclusion"], json.dumps(rule["min_units"])))
        c.commit()
        c.close()
        upgraded = MycelicService(settings(self.h.tmp.name, db_path=str(db), rules_file=str(path)), metrics=Metrics(),
                                  transport=self.h.service.transport)
        try:
            self.assertEqual(upgraded.load_rules_file(), 0, "the first start after the migration does not re-publish the rule")
            self.assertEqual(rule_events(upgraded), 0)
            self.assertEqual(upgraded.store.get_rule("file_rule").to_dict(), stored.to_dict())
            self.assertEqual(upgraded.store.get_applied_rule("file_rule").to_dict(), stored.to_dict())
        finally:
            await upgraded.store.close()


if __name__ == "__main__":
    unittest.main()
