"""Strategic synthesis: conclusions built from other units' conclusions, across regions, with multi-hop lineage,
corroboration, retraction cascades with re-evaluation, and a rebuild from the log."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from mycelic.metrics import Metrics
from mycelic.service import MycelicService, ValidationError
from mycelic.store import MycelicStore

from .helpers import ServiceHarness, settings

RULES = json.loads((Path(__file__).resolve().parent.parent.parent / "deploy" / "mycelic" / "rules.json").read_text())["rules"]
REGIONAL = next(r for r in RULES if r["rule_id"] == "regional_supply_risk")
STRATEGIC = next(r for r in RULES if r["rule_id"] == "strategic_second_source")

def histogram_mean(hist) -> float:
    """Mean observed value of a prometheus Histogram, read through its public samples."""
    samples = {smp.name.rsplit("_", 1)[-1]: smp.value for smp in hist.collect()[0].samples
               if not smp.name.endswith("_bucket")}
    return samples["sum"] / max(1.0, samples["count"])

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
        self.assertNotIn("status_reason", two[0].metadata)
        self.assertEqual(s.store.get_memory(three.memory_id).status, "retracted")

    async def test_corroboration_needs_corroborate_unless_one_memory_spans_the_units(self) -> None:
        s = self.h.service
        await s.upsert_rule({**STRATEGIC, "corroborate": False})
        await self.observe_region("emea")
        await self.observe_region("apac")
        await self.observe_hq()
        await self.h.settle()
        self.assertEqual(self.strategic(), [], "without corroborate only the strongest regional conclusion is a parent")
        await s.upsert_rule(STRATEGIC)
        await self.h.observe("hq-analytics-1", "Second demand datapoint: backlog doubled.", topic="strategy:demand",
                             slot="demand_growth", entity=ENTITY, confidence=0.7)
        await self.h.settle()
        self.assertEqual(len(self.strategic()), 1)

    async def test_withdrawal_for_lost_support_cascades(self) -> None:
        """A note that agrees with the regional evidence never takes support away, also when it makes one agent the
        strongest in two slots: three agents of three teams still fill them, so nothing moves.  Support is lost when the
        rule needs more teams than any selection has: both regional conclusions are withdrawn, and so is everything
        built on them; the rule as it was brings both back."""
        s = self.h.service
        await self.observe_region("emea")
        await self.observe_region("apac")
        await self.observe_hq()
        await self.h.settle()
        [strategy] = self.strategic()
        before = {m.memory_id for m in s.store.list_memories("northwind", status="active", limit=10_000) if m.layer != "agent"}
        emea_before = self.regional()["northwind/emea"]
        # emea-logistics-1 already supplies the strongest transport note; its supplier note becomes the strongest supplier
        # claim, and emea-procurement-1's note, with the transport and demand notes, still makes three agents
        await self.h.observe("emea-logistics-1", "Kessler told us there are two weeks of SD-9 inventory left.",
                             topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity=ENTITY, confidence=0.95)
        await self.h.settle()
        self.assertEqual(self.regional()["northwind/emea"].memory_id, emea_before.memory_id)
        self.assertEqual([m.memory_id for m in self.strategic()], [strategy.memory_id])
        derived = {m.memory_id for m in s.store.list_memories("northwind", status="retracted", limit=10_000) if m.layer != "agent"}
        self.assertEqual(derived, set(), "nothing was withdrawn")
        self.assertLessEqual({m for m in before if s.store.get_memory(m).operator == "slot_composition"},
                             {m.memory_id for m in s.store.list_memories("northwind", status="active", limit=10_000)})
        # three slots never make four teams: every regional conclusion loses its support
        await s.upsert_rule({**REGIONAL, "min_teams": 4})
        await self.h.settle()
        self.assertEqual(self.regional(), {})
        self.assertEqual(s.store.get_memory(emea_before.memory_id).metadata["status_reason"], "support below threshold")
        self.assertEqual(self.strategic(), [], "the strategy rested on the withdrawn regional conclusions")
        self.assertEqual(s.store.get_memory(strategy.memory_id).status, "retracted")
        res = s.query(self.h.principal("hq-sourcing-1"), {"query": "second source sd-9", "scope": "northwind", "min_layer": "enterprise"})
        self.assertTrue(res["answer"] is None or res["answer"]["rule_id"] != "strategic_second_source")
        # the rule as it was: both come back with the same ids
        await s.upsert_rule(REGIONAL)
        await self.h.settle()
        self.assertEqual(self.regional()["northwind/emea"].memory_id, emea_before.memory_id)
        self.assertEqual([m.memory_id for m in self.strategic()], [strategy.memory_id])
        for m in s.store.list_memories("northwind", status="active", limit=1000):
            for e in s.store.parents_of(m.memory_id):
                parent = s.store.get_memory(e.parent_id)
                self.assertEqual(parent.status, "active", f"active {m.memory_id} rests on {parent.status} {e.parent_id}")

    async def test_a_strategy_is_never_stitched_from_risks_about_different_components(self) -> None:
        """EMEA concludes a supply risk for sd-9 only, APAC for sd-10 only, and headquarters' demand and concentration
        notes name no component.  No component is at risk in two regions, so there is no strategy, '*' or otherwise
        (its evidence would be about two components).  Once APAC also concludes a risk for sd-9, the '*' strategy rests
        on the two sd-9 conclusions and the entity-less notes, and verifies."""
        s = self.h.service
        await self.observe_region("emea")
        for i, (team, slot, text) in enumerate((("logistics", "transport_disruption", "Busan strike blocks SD-10 inbound."),
                                                ("logistics", "transport_disruption", "SD-10 carrier slipped."),
                                                ("procurement", "supplier_buffer_low", "Two weeks of SD-10 stock left."),
                                                ("field-sales", "demand_commitment", "40 RX-2 arms committed; each uses an SD-10."))):
            await self.h.observe(f"apac-{team}-{1 + i % 2 if team == 'logistics' else 1}", text, topic="supply:sd-10",
                                 slot=slot, entity="sd-10", confidence=0.9 - i / 20)
        await self.h.observe("hq-analytics-1", "RX order intake is up 40% year over year.", topic="strategy:demand",
                             slot="demand_growth", confidence=0.8)
        await self.h.observe("hq-sourcing-1", "Our drive components each have a single qualified source.",
                             topic="strategy:supply-base", slot="supplier_concentration", confidence=0.95)
        await self.h.settle()
        self.assertEqual({scope: m.entity for scope, m in self.regional().items()},
                         {"northwind/emea": ENTITY, "northwind/apac": "sd-10"})
        self.assertEqual(self.strategic(), [], "no component is at risk in two regions")
        self.assertIsNone(s.aggregator.plan_rule(s.store.get_applied_rule("strategic_second_source"), "northwind", "northwind",
                                                 None).memory)
        await self.observe_region("apac")
        await self.h.settle()
        [strategy] = self.strategic()
        evidence = [s.store.get_memory(e.parent_id) for e in s.store.parents_of(strategy.memory_id)]
        self.assertEqual(strategy.entity, None)
        self.assertEqual({m.entity for m in evidence}, {None, ENTITY})
        self.assertEqual(strategy.metadata["corroborated_units"], {"supply_risk": {"region": ["northwind/apac", "northwind/emea"]}})
        report = await s.verify(self.h.principal("hq-sourcing-1"), strategy.memory_id)
        self.assertEqual((report["verdict"], report["derived_correctly"], report["still_true"]), ("verified", True, True))

    async def test_a_wildcard_strategy_holds_whichever_supply_risk_is_strongest(self) -> None:
        """EMEA concludes a supply risk for sd-9; APAC's notes name no component, so APAC concludes a '*' risk; headquarters'
        notes name none either.  The '*' strategy rests on the entity-less evidence and sd-9's risk, two regions, and it
        stays when APAC's agents report more confidently than EMEA's, so that the entity-less risk is the strongest."""
        s = self.h.service
        slots = (("logistics", "transport_disruption", 0.9), ("procurement", "supplier_buffer_low", 0.85),
                 ("field-sales", "demand_commitment", 0.8))
        for team, slot, confidence in slots:
            await self.h.observe(f"emea-{team}-1", f"EMEA {slot} for SD-9.", topic="supply:sd-9", slot=slot, entity=ENTITY,
                                 confidence=confidence)
            await self.h.observe(f"apac-{team}-1", f"APAC {slot}, no component named.", topic="supply:apac", slot=slot,
                                 confidence=0.75)
        await self.h.observe("hq-analytics-1", "RX order intake is up 40% year over year.", topic="strategy:demand",
                             slot="demand_growth", confidence=0.8)
        await self.h.observe("hq-sourcing-1", "Our drive components each have a single qualified source.",
                             topic="strategy:supply-base", slot="supplier_concentration", confidence=0.95)
        await self.h.settle()

        def entities(m) -> set:
            return {s.store.get_memory(e.parent_id).entity for e in s.store.parents_of(m.memory_id)}

        self.assertEqual({scope: (m.entity, m.confidence) for scope, m in self.regional().items()},
                         {"northwind/emea": (ENTITY, 0.8), "northwind/apac": (None, 0.75)})
        [weaker] = self.strategic()
        self.assertEqual((weaker.entity, entities(weaker)), (None, {None, ENTITY}))
        self.assertEqual(weaker.metadata["corroborated_units"], {"supply_risk": {"region": ["northwind/apac", "northwind/emea"]}})
        # APAC's second agents agree, more confidently than anyone in EMEA: APAC's '*' risk becomes the strongest
        for team, slot, _ in slots:
            await self.h.observe(f"apac-{team}-2", f"APAC {slot} confirmed, no component named.", topic="supply:apac",
                                 slot=slot, confidence=0.95)
        await self.h.settle()
        apac = self.regional()["northwind/apac"]
        self.assertEqual((apac.entity, apac.confidence), (None, 0.95))
        [stronger] = self.strategic()
        history = {m.memory_id: m.status for m in s.store.list_memories("northwind", status=None, layers=["enterprise"])
                   if m.rule_id == "strategic_second_source"}
        self.assertEqual(history.pop(stronger.memory_id), "active")
        self.assertIn(weaker.memory_id, history)
        self.assertEqual(set(history.values()), {"superseded"}, "the strategy was never withdrawn on the way")
        self.assertEqual(stronger.metadata["slots"]["supply_risk"], apac.memory_id)
        self.assertEqual((stronger.entity, entities(stronger)), (None, {None, ENTITY}))
        self.assertEqual(stronger.metadata["corroborated_units"], {"supply_risk": {"region": ["northwind/apac", "northwind/emea"]}})
        report = await s.verify(self.h.principal("hq-sourcing-1"), stronger.memory_id)
        self.assertEqual((report["verdict"], report["derived_correctly"], report["still_true"]), ("verified", True, True))

    async def test_a_strategy_resting_on_a_disputed_regional_risk_is_flagged(self) -> None:
        """EMEA's logistics agents contradict each other on SD-9's transport (disrupted, or running normally again), so
        EMEA's regional supply risk is flagged.  The strategy rests on it and on APAC's risk; its own slots claim no values
        (conclusions and headquarters' notes), yet it is flagged too, and verifying it warns ``disputed`` for the strategy
        itself, not only for EMEA's risk.  Once the agent who was wrong retracts, both flags go."""
        s = self.h.service
        await self.observe_region("emea")
        await self.observe_region("apac")
        await self.observe_hq()
        await self.h.settle()
        [calm] = self.strategic()
        self.assertNotIn("conflict", calm.metadata)
        await self.h.observe("emea-logistics-1", "Rotterdam strike confirmed: SD-9 inbound is disrupted.",
                             topic="supply:sd-9/transport", slot="transport_disruption", entity=ENTITY, value="disrupted",
                             confidence=0.9)
        normal = await self.h.observe("emea-logistics-2", "SD-9 inbound via Rotterdam runs normally; the strike was called off.",
                                      topic="supply:sd-9/transport", slot="transport_disruption", entity=ENTITY,
                                      value="normal", confidence=0.75)
        await self.h.settle()
        regional = self.regional()
        self.assertTrue(regional["northwind/emea"].metadata["conflict"])
        self.assertNotIn("conflict", regional["northwind/apac"].metadata)
        [strategy] = self.strategic()
        self.assertIn(regional["northwind/emea"].memory_id, {e.parent_id for e in s.store.parents_of(strategy.memory_id)})
        self.assertTrue(strategy.metadata.get("conflict"), "a conclusion resting on a disputed conclusion is flagged too")
        report = await s.verify(self.h.principal("emea-field-sales-2"), strategy.memory_id)
        self.assertEqual((report["verdict"], report["derived_correctly"], report["still_true"]), ("verified", True, True))
        self.assertIn({"code": "disputed", "memory_id": strategy.memory_id}, report["warnings"])
        # the agent who was wrong retracts: EMEA's risk and the strategy are derived again, without the flag
        await s.retract(self.h.principal("emea-logistics-2"), normal, "the strike goes ahead")
        await self.h.settle()
        self.assertNotIn("conflict", self.regional()["northwind/emea"].metadata)
        [settled] = self.strategic()
        self.assertNotIn("conflict", settled.metadata)
        report = await s.verify(self.h.principal("emea-field-sales-2"), settled.memory_id)
        self.assertEqual((report["verdict"], [w["code"] for w in report["warnings"]]), ("verified", []))

    async def test_stale_dependents_of_a_superseded_consolidation_are_retired(self) -> None:
        s = self.h.service
        await s.upsert_rule({"rule_id": "r", "target_layer": "department", "required_slots": ["transport_disruption", "supplier_buffer_low"],
                             "sources": ["topic_consolidation"], "min_agents": 2, "min_teams": 2, "conclusion": "R for {entity}"})
        for a, slot, topic in (("emea-logistics-1", "transport_disruption", "supply:sd-9/transport"),
                               ("emea-logistics-2", "transport_disruption", "supply:sd-9/transport"),
                               ("emea-procurement-1", "supplier_buffer_low", "supply:sd-9/supplier"),
                               ("emea-procurement-2", "supplier_buffer_low", "supply:sd-9/supplier")):
            await self.h.observe(a, f"note by {a}", topic=topic, slot=slot, entity="sd-9", confidence=0.8)
        await self.h.settle()
        conclusions = [m for m in s.store.list_memories("northwind", layers=["department"]) if m.rule_id == "r"]
        self.assertEqual([m.entity for m in conclusions], ["sd-9"])
        # a transport note about another entity joins the logistics consolidation: its entity becomes None,
        # the old 'r:sd-9' conclusion rests on a superseded parent and must go
        await self.h.observe("emea-logistics-1", "Rail slot for RX-2 frames also affected by the strike.",
                             topic="supply:sd-9/transport", slot="transport_disruption", entity="rx-2", confidence=0.6)
        await self.h.settle()
        active = [m for m in s.store.list_memories("northwind", layers=["department"]) if m.rule_id == "r"]
        self.assertEqual([m.entity for m in active], [None])
        self.assertEqual(s.store.get_memory(conclusions[0].memory_id).status, "retracted")
        for m in s.store.list_memories("northwind", status="active", limit=1000):
            for e in s.store.parents_of(m.memory_id):
                self.assertEqual(s.store.get_memory(e.parent_id).status, "active")

    async def test_consolidation_requires_unanimous_slot_and_entity(self) -> None:
        s = self.h.service
        await s.upsert_rule({"rule_id": "r2", "target_layer": "region", "required_slots": ["transport_disruption", "supplier_buffer_low"],
                             "sources": ["agent_observation", "topic_consolidation"], "min_agents": 3, "conclusion": "R2 {entity}"})
        await self.h.observe("emea-logistics-1", "strike", topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.9)
        await self.h.observe("emea-procurement-1", "low stock", topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity="sd-9", confidence=0.8)
        await self.h.observe("emea-logistics-2", "Port congestion note without a slot.", topic="supply:sd-9/transport")
        await self.h.settle()
        team = [m for m in s.store.list_memories("northwind", layers=["team"]) if m.topic == "supply:sd-9/transport"]
        self.assertEqual(len(team), 1)
        self.assertIsNone(team[0].slot)
        self.assertIsNone(team[0].entity)
        self.assertEqual([m for m in s.store.list_memories("northwind", layers=["region"]) if m.rule_id == "r2"], [],
                         "a slotless note must not lend its agent to a rule's support")

    async def test_cyclic_rules_are_refused_but_cross_layer_chains_are_not(self) -> None:
        s = self.h.service
        await s.upsert_rule({"rule_id": "a", "target_layer": "team", "required_slots": ["x"], "emits_slot": "y", "conclusion": "A",
                             "sources": ["agent_observation", "slot_composition"]})
        with self.assertRaises(ValidationError):
            await s.upsert_rule({"rule_id": "b", "target_layer": "team", "required_slots": ["y"], "emits_slot": "x", "conclusion": "B",
                                 "sources": ["slot_composition", "agent_observation"]})
        await s.upsert_rule({"rule_id": "c", "target_layer": "enterprise", "required_slots": ["y"], "emits_slot": "z", "conclusion": "C",
                             "sources": ["slot_composition"]})
        with self.assertRaises(ValidationError):
            await s.upsert_rule({"rule_id": "too-many", "target_layer": "team", "required_slots": list("abcdefg"), "conclusion": "x"})
        self.assertEqual(len(s.store.list_rules("northwind")), 2 + 2)   # regional, strategic, a, c

    async def test_fragility_scoring_is_bounded(self) -> None:
        s = self.h.service
        # every note is strictly stronger than the one before, so the last one always changes the coalition and
        # the surviving conclusion was derived with all 72 candidates in view
        for i in range(12):
            for j in (1, 2):
                c = 0.5 + (2 * i + j) / 100
                await self.h.observe(f"emea-logistics-{j}", f"transport note {i}-{j}", topic="supply:sd-9/transport",
                                     slot="transport_disruption", entity=ENTITY, confidence=c)
                await self.h.observe(f"emea-procurement-{j}", f"supplier note {i}-{j}", topic="supply:sd-9/supplier",
                                     slot="supplier_buffer_low", entity=ENTITY, confidence=c)
                await self.h.observe(f"emea-field-sales-{j}", f"demand note {i}-{j}", topic="supply:sd-9/demand",
                                     slot="demand_commitment", entity=ENTITY, confidence=c)
        await self.h.settle(timeout=60)
        m = self.regional()["northwind/emea"]
        self.assertEqual(m.metadata["candidates"], 72)
        self.assertEqual(m.metadata["fragility_scored_candidates"], 18)
        self.assertLess(histogram_mean(s.metrics.aggregation_latency), 0.2)

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
