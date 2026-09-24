"""Strategic synthesis: conclusions built from other units' conclusions, across regions, with multi-hop lineage,
corroboration, retraction cascades with re-evaluation, and a rebuild from the log."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from mycelic.metrics import Metrics
from mycelic.service import MycelicService
from mycelic.store import MycelicStore

from .helpers import ServiceHarness, settings

RULES = json.loads((Path(__file__).resolve().parent.parent.parent / "deploy" / "mycelic" / "rules.json").read_text())["rules"]
REGIONAL = next(r for r in RULES if r["rule_id"] == "regional_supply_risk")
STRATEGIC = next(r for r in RULES if r["rule_id"] == "strategic_second_source")
ENTITY = "sd-9"


class StrategicSynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.h = await ServiceHarness().start()
        s = self.h.service
        await s.upsert_rule(REGIONAL)
        await s.upsert_rule(STRATEGIC)
        for region, sub in (("emea", "nw-gmbh"), ("apac", "nw-kk")):
            for team, dept in (("logistics", "ops"), ("procurement", "ops"), ("field-sales", "commercial")):
                for i in (1, 2):
                    await self.h.register(f"{region}-{team}-{i}", team=team, department=dept, subsidiary=sub, region=region)
        await self.h.register("hq-sourcing-1", team="sourcing", department="strategy", subsidiary="northwind-hq", region="hq")
        await self.h.register("hq-analytics-1", team="analytics", department="strategy", subsidiary="northwind-hq", region="hq")

    async def asyncTearDown(self) -> None:
        await self.h.close()

    async def observe_region(self, region: str, *, skip_supplier: bool = False) -> dict[str, str]:
        port = "Rotterdam" if region == "emea" else "Busan"
        ids = {}
        ids["t1"] = await self.h.observe(f"{region}-logistics-1", f"Port of {port} strike announced; SD-9 shipments route through it.",
                                         topic="supply:sd-9/transport", slot="transport_disruption", entity=ENTITY, confidence=0.9)
        ids["t2"] = await self.h.observe(f"{region}-logistics-2", f"Carrier ETA for SD-9 containers via {port} slipped by 12 days.",
                                         topic="supply:sd-9/transport", slot="transport_disruption", entity=ENTITY, confidence=0.7)
        if not skip_supplier:
            ids["s1"] = await self.h.observe(f"{region}-procurement-1", "Kessler Antriebe confirms two weeks of SD-9 inventory left.",
                                             topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity=ENTITY, confidence=0.85)
        ids["d1"] = await self.h.observe(f"{region}-field-sales-1", "A customer committed to 40 RX-4 arms for November; every RX-4 uses an SD-9.",
                                         topic="supply:sd-9/demand", slot="demand_commitment", entity=ENTITY, confidence=0.8)
        return ids

    async def observe_hq(self) -> dict[str, str]:
        ids = {}
        ids["growth"] = await self.h.observe("hq-analytics-1", "RX-4 order intake is up 40% year over year across all regions.",
                                             topic="strategy:demand", slot="demand_growth", entity=ENTITY, confidence=0.8)
        ids["conc"] = await self.h.observe("hq-sourcing-1", "Kessler Antriebe is the only qualified SD-9 source for every subsidiary.",
                                           topic="strategy:supply-base", slot="supplier_concentration", entity=ENTITY, confidence=0.95)
        return ids

    def strategic(self):
        return [m for m in self.h.service.store.list_memories("northwind", layers=["enterprise"]) if m.rule_id == "strategic_second_source"]

    def regional(self):
        return {m.scope: m for m in self.h.service.store.list_memories("northwind", layers=["region"]) if m.rule_id == "regional_supply_risk"}

    async def test_strategic_conclusion_rests_on_regional_conclusions(self) -> None:
        s = self.h.service
        await self.observe_region("emea")
        await self.observe_hq()
        await self.h.settle()
        self.assertEqual(set(self.regional()), {"northwind/emea"})
        self.assertEqual(self.strategic(), [], "one region is not corroboration")
        await self.observe_region("apac")
        await self.h.settle()
        self.assertEqual(set(self.regional()), {"northwind/emea", "northwind/apac"})
        strat = self.strategic()
        self.assertEqual(len(strat), 1)
        m = strat[0]
        self.assertEqual(m.slot, "sourcing_decision")
        self.assertEqual(m.topic, "strategy:sourcing")
        self.assertIn("qualify a second source", m.text)
        self.assertEqual(m.metadata["corroborated_units"]["supply_risk"]["region"], ["northwind/apac", "northwind/emea"])
        self.assertEqual(sorted(m.metadata["contributing_teams"])[:1], ["northwind/apac/nw-kk/commercial/field-sales"])
        self.assertGreaterEqual(m.support, 8)
        self.assertGreaterEqual(m.independent_teams, 7)
        # lineage: enterprise <- two regional conclusions <- raw observations
        g = s.lineage(self.h.admin, m.memory_id)
        self.assertEqual(g["layers"], ["agent", "region", "enterprise"])
        parents = {e["parent"] for e in g["edges"] if e["child"] == m.memory_id}
        self.assertTrue({r.memory_id for r in self.regional().values()} <= parents)
        self.assertEqual(len(g["roots"]), 8, "3 selected observations per region (strongest per slot) + 2 hq observations")
        self.assertTrue(g["evidence"]["reconstructable"])
        hops = sorted((t["to_layer"], t["rule_id"] or "") for t in g["transformations"])
        parent_ops = {g["nodes"][p]["operator"] for p in parents}
        self.assertEqual(parent_ops, {"agent_observation", "slot_composition"}, "no consolidation restating its own parents")
        self.assertIn(("region", "regional_supply_risk"), hops)
        self.assertIn(("enterprise", "strategic_second_source"), hops)
        # an EMEA agent sees the strategy and its own region's conclusion, not APAC's
        emea = self.h.principal("emea-field-sales-2")
        res = s.query(emea, {"query": "second source sourcing decision sd-9", "scope": "northwind", "min_layer": "region"})
        self.assertEqual(res["answer"]["memory_id"], m.memory_id)
        visible_regions = {h["memory"]["scope"] for h in res["results"] if h["memory"]["layer"] == "region"}
        self.assertEqual(visible_regions, {"northwind/emea"})
        gl = s.lineage(emea, m.memory_id)
        self.assertGreater(gl["redacted_contributions"], 0)
        self.assertIsNone(gl["nodes"][self.regional()["northwind/apac"].memory_id]["text"])

    async def test_strategy_follows_evidence(self) -> None:
        s = self.h.service
        await self.observe_region("emea")
        apac = await self.observe_region("apac")
        await self.observe_hq()
        await self.h.settle()
        strat = self.strategic()[0]
        await s.retract(self.h.principal("apac-procurement-1"), apac["s1"], "counted wrong")
        await self.h.settle()
        self.assertEqual(set(self.regional()), {"northwind/emea"}, "APAC lost its supplier evidence")
        self.assertEqual(self.strategic(), [], "a single region no longer corroborates the strategy")
        self.assertEqual(s.store.get_memory(strat.memory_id).status, "retracted")
        # the evidence returns as a new observation: the regional conclusion and the strategy come back
        await self.h.observe("apac-procurement-2", "Kessler Antriebe confirms two weeks of SD-9 inventory left.",
                             topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity=ENTITY, confidence=0.85)
        await self.h.settle()
        self.assertEqual(set(self.regional()), {"northwind/emea", "northwind/apac"})
        again = self.strategic()
        self.assertEqual(len(again), 1)
        self.assertNotEqual(again[0].memory_id, strat.memory_id, "different evidence, different version")
        self.assertEqual(s.lineage(self.h.admin, again[0].memory_id)["layers"], ["agent", "region", "enterprise"])
        # losing one of three corroborating pieces keeps a conclusion that still has enough support
        await self.h.register("amer-logistics-1", team="logistics", department="ops", subsidiary="nw-inc", region="amer")
        await self.h.register("amer-logistics-2", team="logistics", department="ops", subsidiary="nw-inc", region="amer")
        await self.h.register("amer-procurement-1", team="procurement", department="ops", subsidiary="nw-inc", region="amer")
        await self.h.register("amer-field-sales-1", team="field-sales", department="commercial", subsidiary="nw-inc", region="amer")
        amer = await self.observe_region("amer")
        await self.h.settle()
        three = self.strategic()[0]
        self.assertEqual(three.metadata["corroborated_units"]["supply_risk"]["region"], ["northwind/amer", "northwind/apac", "northwind/emea"])
        await s.retract(self.h.principal("amer-procurement-1"), amer["s1"], "duplicate entry")
        await self.h.settle()
        two = self.strategic()
        self.assertEqual(len(two), 1, "still corroborated by two regions after re-evaluation")
        self.assertEqual(two[0].memory_id, again[0].memory_id, "the exact earlier coalition is reactivated")
        self.assertEqual(s.store.get_memory(three.memory_id).status, "retracted")

    async def test_rebuild_reproduces_the_strategic_lineage(self) -> None:
        s1 = self.h.service
        await self.observe_region("emea")
        await self.observe_region("apac")
        await self.observe_hq()
        await self.h.settle()
        before = {m.memory_id: (m.layer, m.status, m.support, m.slot) for m in s1.store.list_memories("northwind", status=None, limit=1000)}
        strat = self.strategic()[0]
        lineage_before = s1.lineage(self.h.admin, strat.memory_id)
        transport = s1.transport
        await s1.stop()
        s2 = MycelicService(settings(self.h.tmp.name), store=MycelicStore(":memory:"), transport=transport, metrics=Metrics())
        await s2.start()
        try:
            self.assertTrue(await s2.wait_idle(15))
            after = {m.memory_id: (m.layer, m.status, m.support, m.slot) for m in s2.store.list_memories("northwind", status=None, limit=1000)}
            self.assertEqual(after, before)
            g = s2.lineage(s2.authenticate(f"Bearer {self.h.keys['hq-sourcing-1']}"), strat.memory_id)
            self.assertEqual(sorted(g["roots"]), sorted(lineage_before["roots"]))
            self.assertEqual(g["layers"], lineage_before["layers"])
        finally:
            await s2.close()


if __name__ == "__main__":
    unittest.main()
