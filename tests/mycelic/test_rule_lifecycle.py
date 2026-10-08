"""Rule and registry lifecycle at apply: a conclusion appears as soon as it holds and disappears as soon as it stops
holding, whether the change is new evidence, a rule added after its evidence, a rule changed, disabled, deleted,
re-created, narrowed or moved, or a unit gaining or losing a child.  Derived ids are versioned by the rule's digest
and the derivation settings, so equal derivations keep their ids and different ones never reuse them."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from mycelic.hierarchy import layer_of_path
from mycelic.metrics import Metrics
from mycelic.models import DERIVATION_VERSION, Rule, new_id, now_iso, rule_digest, rule_snapshot
from mycelic.service import MycelicService
from mycelic.store import MycelicStore

from .helpers import (
    DEMO_RULE, HeldTransport, ServiceHarness, applied_rule_rows, broken_chains, drain_outbox, invariant_violations,
    lineage_edge_set, memory_history, settings, step, table_dump,
)

ORG = "northwind"
RULES_FILE = Path(__file__).resolve().parents[2] / "deploy" / "mycelic" / "rules.json"
RULES = {r["rule_id"]: r for r in json.loads(RULES_FILE.read_text())["rules"]}
REGIONAL, STRATEGIC = RULES["regional_supply_risk"], RULES["strategic_second_source"]
DEMO = DEMO_RULE["rule_id"]
ENTITY = "sd-9"
DEPT = "northwind/emea/nw-gmbh/ops"
# p's conclusion about e blocks w's '*' conclusion: it is the strongest candidate for w's slot s, so w's best selection
# names e only and is e's conclusion, not a '*' one, although a weaker note that names no entity fills s as well.  v sits
# above w and concludes on whatever w concludes.
BLOCKING = {"rule_id": "p", "target_layer": "team", "required_slots": ["x"], "min_agents": 1, "emits_slot": "s",
            "emits_topic": "supply:p", "conclusion": "P {entity}: {slot:x}"}
BLOCKED = {"rule_id": "w", "target_layer": "department", "required_slots": ["s", "y"], "min_agents": 2, "emits_slot": "ws",
           "sources": ["slot_composition", "agent_observation"], "topic_prefix": "supply:", "conclusion": "W {entity}: {slot:s}"}
ABOVE = {"rule_id": "v", "target_layer": "enterprise", "required_slots": ["ws"], "min_agents": 1, "sources": ["slot_composition"],
         "conclusion": "V {entity}: {slot:ws}"}


def wire(kind: str, payload: Any, *, org_id: str = ORG) -> dict[str, Any]:
    return {"event_id": new_id("evt"), "kind": kind, "org_id": org_id, "agent_id": None, "created_at": now_iso(),
            "payload": payload, "schema": 1}


def of_rule(store: MycelicStore, rule_id: str, *, org_id: str = ORG, status: str | None = "active") -> list:
    return [m for m in store.list_memories(org_id, status=status, limit=10_000) if m.rule_id == rule_id]


def memories_and_edges(store: MycelicStore) -> tuple[list[tuple], list[tuple]]:
    return table_dump(store, "memories"), table_dump(store, "lineage_edges")


def kinds(store: MycelicStore) -> dict[str, int]:
    return {r["kind"]: r["n"] for r in store._conn.execute("SELECT kind, COUNT(*) AS n FROM events GROUP BY kind")}


class RuleLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def harness(self, **kw: Any) -> ServiceHarness:
        h = await ServiceHarness(**kw).start()
        self.addAsyncCleanup(h.close)
        return h

    async def demo_agents(self, h: ServiceHarness) -> None:
        for a in ("log-1", "log-2"):
            await h.register(a, team="logistics")
        await h.register("proc-1", team="procurement")
        await h.register("sales-1", team="field-sales", department="commercial")

    async def demo_evidence(self, h: ServiceHarness, *, settle: bool = True) -> dict[str, str]:
        """Notes covering the three slots of DEMO_RULE for sd-9: three agents in three teams once the strongest per slot
        is selected."""
        ids = {
            "t1": await h.observe("log-1", "Port strike; SD-9 shipments route through it.", topic="supply:sd-9/transport",
                                  slot="transport_disruption", entity=ENTITY, confidence=0.9),
            "t2": await h.observe("log-2", "Carrier ETA for SD-9 slipped by 12 days.", topic="supply:sd-9/transport",
                                  slot="transport_disruption", entity=ENTITY, confidence=0.7),
            "s1": await h.observe("proc-1", "Two weeks of SD-9 inventory left.", topic="supply:sd-9/supplier",
                                  slot="supplier_buffer_low", entity=ENTITY, confidence=0.85),
            "d1": await h.observe("sales-1", "40 RX-4 arms committed for November.", topic="supply:sd-9/demand",
                                  slot="demand_commitment", entity=ENTITY, confidence=0.8),
        }
        if settle:
            await h.settle()
        return ids

    # ------------------------------------------------------------------ rules added after their evidence
    async def test_rule_added_after_evidence_concludes(self) -> None:
        h = await self.harness()
        s = h.service
        await self.demo_agents(h)
        await self.demo_evidence(h)
        self.assertEqual(of_rule(s.store, DEMO, status=None), [])
        observed = kinds(s.store)["memory.observed"]
        await s.upsert_rule(DEMO_RULE)
        await h.settle()
        self.assertEqual(kinds(s.store)["memory.observed"], observed, "no observation event was needed")
        [c] = of_rule(s.store, DEMO)
        self.assertEqual((c.layer, c.scope, c.support, c.independent_teams), ("enterprise", ORG, 3, 3))
        rule = s.store.get_applied_rule(DEMO)
        self.assertEqual(DERIVATION_VERSION, 5)
        self.assertEqual(c.metadata["derivation"], {"v": DERIVATION_VERSION, "rule_digest": rule_digest(rule), "rule": rule_snapshot(rule)})
        self.assertIsNone(c.metadata["version_of"])
        self.assertEqual(s.aggregator.plan_for(c).memory.memory_id, c.memory_id, "settled: re-planning reproduces it")
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_rule_is_used_only_once_its_event_applies(self) -> None:
        transport = HeldTransport()
        h = await self.harness(transport=transport)
        s = h.service
        await self.demo_agents(h)
        await self.demo_evidence(h, settle=False)
        await s.upsert_rule(DEMO_RULE)
        await drain_outbox(s)
        log_kinds = [json.loads(payload)["kind"] for _, payload, _, _ in transport._log]
        self.assertEqual(log_kinds[-1], "rule.upserted", "the notes are in the log before the rule")
        for _ in log_kinds[:-1]:
            await step(s, transport)
        self.assertIsNotNone(s.store.get_rule(DEMO), "the admin table (GET /admin/rules) shows the rule at once")
        self.assertIsNone(s.store.get_applied_rule(DEMO))
        self.assertEqual(of_rule(s.store, DEMO, status=None), [], "no conclusion while only the notes have applied")
        self.assertEqual((await step(s, transport))["kind"], "rule.upserted")
        [c] = of_rule(s.store, DEMO, status=None)
        self.assertEqual((c.status, c.metadata["version_of"]), ("active", None), "it appears when the rule event applies")
        transport.hold = False
        await h.settle()
        self.assertEqual([m.memory_id for m in of_rule(s.store, DEMO, status=None)], [c.memory_id], "and only once")

    # ------------------------------------------------------------------ disable, delete, re-create
    async def test_rule_disabled_withdraws_and_cascades(self) -> None:
        h = await self.harness()
        s = h.service
        rule_a = {"rule_id": "a", "target_layer": "team", "required_slots": ["x"], "corroborate": True, "min_agents": 2,
                  "emits_slot": "s", "emits_topic": "risk:a", "conclusion": "A {entity}: {slot:x}"}
        rule_b = {"rule_id": "b", "target_layer": "enterprise", "required_slots": ["s", "y"], "min_agents": 3,
                  "sources": ["slot_composition", "agent_observation"], "conclusion": "B {entity}: {slot:s} / {slot:y}"}
        await self.demo_agents(h)
        await s.upsert_rule(rule_a)
        await s.upsert_rule(rule_b)
        await h.observe("log-1", "x by log-1", slot="x", entity="e", confidence=0.8)
        await h.observe("log-2", "x by log-2", slot="x", entity="e", confidence=0.7)
        await h.observe("proc-1", "y by proc-1", slot="y", entity="e", confidence=0.9)
        await h.settle()
        [a] = of_rule(s.store, "a")
        [b] = of_rule(s.store, "b")
        self.assertIn(a.memory_id, {e.parent_id for e in s.store.parents_of(b.memory_id)})
        await s.upsert_rule({**rule_a, "enabled": False})
        await h.settle()
        self.assertEqual(of_rule(s.store, "a"), [])
        self.assertEqual(of_rule(s.store, "b"), [], "the conclusion resting on it is withdrawn too")
        self.assertEqual(s.store.get_memory(a.memory_id).metadata["status_reason"], "rule disabled")
        self.assertEqual(s.store.get_memory(b.memory_id).status, "retracted")
        self.assertEqual(s.store.get_memory(b.memory_id).metadata["status_reason"], "evidence withdrawn")
        self.assertEqual(broken_chains(s.store, ORG), [])
        await s.upsert_rule(rule_a)
        await h.settle()
        self.assertEqual([m.memory_id for m in of_rule(s.store, "a")], [a.memory_id], "re-enabled: the same conclusion")
        self.assertEqual([m.memory_id for m in of_rule(s.store, "b")], [b.memory_id], "and the same one above it")
        self.assertNotIn("status_reason", s.store.get_memory(a.memory_id).metadata)
        self.assertEqual(invariant_violations(s, ORG), [])

    async def strategic_world(self, h: ServiceHarness) -> None:
        """Two regions with the three supply slots each and head office with growth and concentration (test_strategic)."""
        s = h.service
        await s.upsert_rule(REGIONAL)
        await s.upsert_rule(STRATEGIC)
        for region, sub in (("emea", "nw-gmbh"), ("apac", "nw-kk")):
            for team, dept in (("logistics", "ops"), ("procurement", "ops"), ("field-sales", "commercial")):
                for i in (1, 2):
                    await h.register(f"{region}-{team}-{i}", team=team, department=dept, subsidiary=sub, region=region)
        await h.register("hq-sourcing-1", team="sourcing", department="strategy", subsidiary="northwind-hq", region="hq")
        await h.register("hq-analytics-1", team="analytics", department="strategy", subsidiary="northwind-hq", region="hq")
        for region in ("emea", "apac"):
            await h.observe(f"{region}-logistics-1", f"{region} port strike", topic="supply:sd-9/transport",
                            slot="transport_disruption", entity=ENTITY, confidence=0.9)
            await h.observe(f"{region}-logistics-2", f"{region} carrier slipped", topic="supply:sd-9/transport",
                            slot="transport_disruption", entity=ENTITY, confidence=0.7)
            await h.observe(f"{region}-procurement-1", f"{region} two weeks of stock", topic="supply:sd-9/supplier",
                            slot="supplier_buffer_low", entity=ENTITY, confidence=0.85)
            await h.observe(f"{region}-field-sales-1", f"{region} committed demand", topic="supply:sd-9/demand",
                            slot="demand_commitment", entity=ENTITY, confidence=0.8)
        await h.observe("hq-analytics-1", "RX-4 order intake up 40%.", topic="strategy:demand", slot="demand_growth",
                        entity=ENTITY, confidence=0.8)
        await h.observe("hq-sourcing-1", "Kessler is the only qualified source.", topic="strategy:supply-base",
                        slot="supplier_concentration", entity=ENTITY, confidence=0.95)
        await h.settle()

    async def test_rule_deleted_withdraws_strategic_dependents(self) -> None:
        h = await self.harness()
        s = h.service
        await self.strategic_world(h)
        regional = {m.scope: m.memory_id for m in of_rule(s.store, "regional_supply_risk")}
        [strategic] = of_rule(s.store, "strategic_second_source")
        self.assertEqual(set(regional), {"northwind/emea", "northwind/apac"})
        await s.delete_rule("regional_supply_risk")
        await h.settle()
        self.assertIsNone(s.store.get_applied_rule("regional_supply_risk"))
        self.assertEqual(of_rule(s.store, "regional_supply_risk"), [])
        self.assertEqual({s.store.get_memory(mid).metadata["status_reason"] for mid in regional.values()}, {"rule deleted"})
        self.assertNotEqual(s.store.get_memory(strategic.memory_id).status, "active")
        self.assertEqual(of_rule(s.store, "strategic_second_source"), [], "the strategy rested on the regional conclusions")
        self.assertEqual([m.memory_id for m in s.store.list_memories(ORG, limit=10_000)
                          if "regional_supply_risk" in m.metadata.get("rule_chain", [])], [], "nothing active rests on them")
        self.assertEqual(broken_chains(s.store, ORG), [])
        await s.upsert_rule(REGIONAL)
        await h.settle()
        self.assertEqual({m.scope: m.memory_id for m in of_rule(s.store, "regional_supply_risk")}, regional,
                         "re-created identically: the same regional conclusions")
        self.assertEqual([m.memory_id for m in of_rule(s.store, "strategic_second_source")], [strategic.memory_id],
                         "and the same strategy")
        self.assertEqual(invariant_violations(s, ORG), [])

    # ------------------------------------------------------------------ changed rules
    async def test_tightened_thresholds_withdraw_and_loosened_reactivate(self) -> None:
        h = await self.harness()
        s = h.service
        await self.demo_agents(h)
        await s.upsert_rule(DEMO_RULE)
        await self.demo_evidence(h)
        [c] = of_rule(s.store, DEMO)
        for tighter in ({"min_agents": 4}, {"min_teams": 4}):
            with self.subTest(tighter=tighter):
                await s.upsert_rule({**DEMO_RULE, **tighter})
                await h.settle()
                self.assertEqual(of_rule(s.store, DEMO), [])
                self.assertEqual(s.store.get_memory(c.memory_id).metadata["status_reason"], "support below threshold")
                await s.upsert_rule(DEMO_RULE)
                await h.settle()
                self.assertEqual([m.memory_id for m in of_rule(s.store, DEMO)], [c.memory_id], "loosened: the same id is back")
        # a change that still holds derives under the new digest: a new version of the same conclusion
        await s.upsert_rule({**DEMO_RULE, "min_agents": 2})
        await h.settle()
        [n] = of_rule(s.store, DEMO)
        self.assertNotEqual(n.memory_id, c.memory_id)
        self.assertEqual((n.metadata["version_of"], s.store.get_memory(c.memory_id).superseded_by), (c.memory_id, n.memory_id))
        self.assertEqual(n.metadata["derivation"]["rule_digest"], rule_digest(s.store.get_applied_rule(DEMO)))
        self.assertNotEqual(n.metadata["derivation"]["rule_digest"], c.metadata["derivation"]["rule_digest"])
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_template_change_creates_new_version(self) -> None:
        h = await self.harness()
        s = h.service
        await self.demo_agents(h)
        await s.upsert_rule(DEMO_RULE)
        await self.demo_evidence(h)
        [c] = of_rule(s.store, DEMO)
        await s.upsert_rule({**DEMO_RULE, "conclusion": "Revised: supply risk for {entity} ({slot:transport_disruption})."})
        await h.settle()
        [n] = of_rule(s.store, DEMO)
        self.assertNotEqual(n.memory_id, c.memory_id)
        self.assertTrue(n.text.startswith("Revised: supply risk for sd-9 (Port strike"), n.text)
        old = s.store.get_memory(c.memory_id)
        self.assertEqual((old.status, old.superseded_by, old.text), ("superseded", n.memory_id, c.text))
        self.assertEqual(n.metadata["version_of"], c.memory_id)
        self.assertEqual(s.lineage(h.admin, n.memory_id)["previous_versions"], [c.memory_id])
        self.assertEqual({e.parent_id for e in s.store.parents_of(n.memory_id)}, {e.parent_id for e in s.store.parents_of(c.memory_id)},
                         "the same evidence")
        self.assertNotEqual(n.metadata["derivation"]["rule_digest"], c.metadata["derivation"]["rule_digest"])
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_emitted_topic_or_slot_change_supersedes_and_reevaluates_dependents(self) -> None:
        h = await self.harness()
        s = h.service
        await self.strategic_world(h)
        regional = {m.scope: m for m in of_rule(s.store, "regional_supply_risk")}
        [strategic] = of_rule(s.store, "strategic_second_source")

        def on_topic(topic: str) -> list:
            return [m for m in s.store.list_memories(ORG, limit=10_000) if m.topic == topic and m.operator == "topic_consolidation"]

        self.assertTrue(on_topic("strategy:supply-risk"), "the regional conclusions are consolidated at the enterprise")
        await s.upsert_rule({**REGIONAL, "emits_topic": "strategy:regional-risk"})
        await h.settle()
        moved = {m.scope: m for m in of_rule(s.store, "regional_supply_risk")}
        self.assertEqual(set(moved), set(regional))
        for scope, m in moved.items():
            self.assertEqual((m.topic, m.metadata["version_of"]), ("strategy:regional-risk", regional[scope].memory_id))
            self.assertEqual(s.store.get_memory(regional[scope].memory_id).superseded_by, m.memory_id)
        [again] = of_rule(s.store, "strategic_second_source")
        self.assertNotEqual(again.memory_id, strategic.memory_id, "the strategy rests on the new versions")
        self.assertTrue({m.memory_id for m in moved.values()} <= {e.parent_id for e in s.store.parents_of(again.memory_id)})
        self.assertEqual(on_topic("strategy:supply-risk"), [], "nothing is consolidated under the old topic any more")
        self.assertTrue(on_topic("strategy:regional-risk"))
        self.assertEqual(broken_chains(s.store, ORG), [])
        # a different emitted slot: the strategy loses its supply-risk evidence
        await s.upsert_rule({**REGIONAL, "emits_topic": "strategy:regional-risk", "emits_slot": "regional_risk"})
        await h.settle()
        self.assertEqual({m.slot for m in of_rule(s.store, "regional_supply_risk")}, {"regional_risk"})
        self.assertEqual(of_rule(s.store, "strategic_second_source"), [])
        self.assertEqual(broken_chains(s.store, ORG), [])
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_withdrawn_evidence_reevaluates_wildcard_conclusions(self) -> None:
        """A '*' conclusion that did not hold because the strongest evidence named an entity holds once that evidence
        is withdrawn, although it never rested on it."""
        h = await self.harness()
        s = h.service
        await s.upsert_rule({"rule_id": "p", "target_layer": "team", "required_slots": ["x"], "min_agents": 1, "emits_slot": "s",
                             "conclusion": "P {entity}: {slot:x}"})
        await s.upsert_rule({"rule_id": "w", "target_layer": "department", "required_slots": ["s"], "min_agents": 1,
                             "sources": ["slot_composition"], "conclusion": "W {entity}: {slot:s}"})
        await h.register("log-1", team="logistics")
        await h.register("proc-1", team="procurement")
        named = await h.observe("log-1", "x about e1", slot="x", entity="e1", confidence=0.9)
        await h.observe("proc-1", "x about nothing in particular", slot="x", confidence=0.4)
        await h.settle()
        self.assertEqual({m.entity for m in of_rule(s.store, "p")}, {"e1", None})
        self.assertEqual({m.entity for m in of_rule(s.store, "w")}, {"e1"}, "the strongest evidence names e1: no '*' yet")
        await s.retract(h.principal("log-1"), named, "wrong part")
        await h.settle()
        self.assertEqual(of_rule(s.store, "p", status="active")[0].entity, None)
        [star] = of_rule(s.store, "w")
        self.assertEqual((star.entity, star.scope), (None, "northwind/emea/nw-gmbh/ops"), "the '*' conclusion now holds")
        self.assertEqual(invariant_violations(s, ORG), [])

    # ------------------------------------------------------------------ a candidate that stops blocking a conclusion
    # A rule takes the best selection that meets its thresholds, so a candidate that is not selected never matters to an
    # entity's conclusion.  A '*' conclusion exists only when that selection names no entity somewhere: a strong candidate
    # about one entity keeps it away.  When that candidate stops filling the slot, the '*' conclusion holds, although it
    # never rested on the candidate (it is not among its dependents) and nothing new is offered under the old slot, entity
    # or topic.
    async def blocked_world(self, h: ServiceHarness) -> str:
        s = h.service
        for body in (BLOCKING, BLOCKED, ABOVE):
            await s.upsert_rule(body)
        await h.register("log-1", team="logistics")
        await h.register("proc-1", team="procurement")
        await h.register("proc-2", team="procurement")
        await h.observe("log-1", "x, strongly", topic="supply:x", slot="x", entity="e", confidence=0.95)
        await h.observe("proc-1", "s about nothing in particular, weakly", topic="supply:s", slot="s", confidence=0.5)
        await h.observe("proc-2", "y", topic="supply:y", slot="y", entity="e", confidence=0.9)
        await h.settle()
        [p] = of_rule(s.store, "p")
        self.assertEqual((p.slot, p.topic, p.confidence, p.entity), ("s", "supply:p", 0.95, "e"))
        [w] = of_rule(s.store, "w")
        self.assertEqual((w.entity, w.support), ("e", 2), "p's conclusion is selected for s: w's best selection names e only")
        self.assertEqual({e.parent_id for e in s.store.parents_of(w.memory_id)} & {p.memory_id}, {p.memory_id})
        self.assertEqual([m.entity for m in of_rule(s.store, "v")], ["e"])
        self.assertEqual(invariant_violations(s, ORG), [])
        return p.memory_id

    def assert_unblocked(self, s: MycelicService, support: int) -> None:
        [w] = of_rule(s.store, "w")
        self.assertEqual((w.scope, w.entity, w.support), (DEPT, None, support))
        [v] = of_rule(s.store, "v")
        self.assertEqual({e.parent_id for e in s.store.parents_of(v.memory_id)}, {w.memory_id}, "and is offered upward")
        self.assertEqual(invariant_violations(s, ORG), [])

    def assert_blocked_again(self, s: MycelicService, p: str) -> None:
        self.assertEqual([m.memory_id for m in of_rule(s.store, "p")], [p])
        self.assertEqual(([m.entity for m in of_rule(s.store, "w")], [m.entity for m in of_rule(s.store, "v")]), (["e"], ["e"]))
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_superseded_candidate_unblocks_conclusions(self) -> None:
        for change in ({"emits_slot": "z"}, {"emits_topic": "risk:p"}):
            with self.subTest(change=change):
                h = await self.harness()
                s = h.service
                p = await self.blocked_world(h)
                await s.upsert_rule({**BLOCKING, **change})
                await h.settle()
                [p2] = of_rule(s.store, "p")
                self.assertEqual((s.store.get_memory(p).superseded_by, p2.metadata["version_of"]), (p2.memory_id, p))
                self.assert_unblocked(s, support=2)
                # changed back: the original version is reactivated and blocks w's '*' conclusion again
                await s.upsert_rule(BLOCKING)
                await h.settle()
                self.assert_blocked_again(s, p)

    async def test_withdrawn_candidate_unblocks_conclusions(self) -> None:
        """The withdrawal itself is the only path here: the rule event reconciles p's conclusion and nothing else."""
        for change in ("disable", "delete"):
            with self.subTest(change=change):
                h = await self.harness()
                s = h.service
                p = await self.blocked_world(h)
                if change == "disable":
                    await s.upsert_rule({**BLOCKING, "enabled": False})
                else:
                    await s.delete_rule("p")
                await h.settle()
                self.assertEqual(s.store.get_memory(p).metadata["status_reason"],
                                 "rule disabled" if change == "disable" else "rule deleted")
                self.assert_unblocked(s, support=2)
                await s.upsert_rule(BLOCKING)
                await h.settle()
                self.assert_blocked_again(s, p)

    async def test_consolidation_losing_its_slot_or_entity_unblocks_conclusions(self) -> None:
        """No rule changes: logistics' consolidation about e is the strongest candidate for s, so w's best selection names
        e only and no '*' conclusion exists although procurement's consolidation names no entity.  A note with another slot,
        or about another entity, joins logistics' topic; its new version carries no common slot (or entity), so it is no
        longer that candidate, and w's '*' conclusion holds."""
        for joining in ({"slot": "q", "entity": "e"}, {"slot": "s", "entity": "f"}):
            with self.subTest(joining=joining):
                h = await self.harness()
                s = h.service
                await s.upsert_rule({**BLOCKED, "min_agents": 3, "sources": ["topic_consolidation"], "topic_prefix": None})
                await s.upsert_rule(ABOVE)
                for a in ("a1", "a2", "a3"):
                    await h.register(a, team="logistics")
                for b in ("b1", "b2"):
                    await h.register(b, team="procurement")
                for c in ("c1", "c2"):
                    await h.register(c, team="field-sales")
                await h.observe("a1", "s by a1", topic="risk:t", slot="s", entity="e", confidence=0.95)
                await h.observe("a2", "s by a2", topic="risk:t", slot="s", entity="e", confidence=0.9)
                await h.observe("b1", "s by b1", topic="risk:u", slot="s", confidence=0.5)
                await h.observe("b2", "s by b2", topic="risk:u", slot="s", confidence=0.4)
                await h.observe("a1", "y by a1", topic="risk:y", slot="y", entity="e", confidence=0.9)
                await h.observe("a2", "y by a2", topic="risk:y", slot="y", entity="e", confidence=0.9)
                await h.observe("c1", "y by c1", topic="risk:y", slot="y", entity="e", confidence=0.8)
                await h.observe("c2", "y by c2", topic="risk:y", slot="y", entity="e", confidence=0.8)
                await h.settle()
                [blocking] = [m for m in s.store.list_memories(ORG, layers=["team"]) if m.topic == "risk:t"]
                self.assertEqual((blocking.slot, blocking.entity), ("s", "e"))
                [w] = of_rule(s.store, "w")
                self.assertEqual((w.entity, w.support), ("e", 4), "logistics' s and field-sales' y: four agents, about e")
                other = await h.observe("a3", "joins by a3", topic="risk:t", confidence=0.6, **joining)
                await h.settle()
                [now] = [m for m in s.store.list_memories(ORG, layers=["team"]) if m.topic == "risk:t"]
                self.assertEqual((now.metadata["version_of"], s.store.get_memory(blocking.memory_id).status),
                                 (blocking.memory_id, "superseded"))
                if joining["slot"] != "s":
                    # procurement's consolidation (no entity) fills s next to logistics' y: a1, a2, b1, b2
                    self.assertEqual((now.slot, now.entity), (None, "e"))
                    self.assert_unblocked(s, support=4)
                else:
                    # the entity-less version itself fills s next to logistics' y: a1, a2 and a3
                    self.assertEqual((now.slot, now.entity), ("s", None))
                    self.assert_unblocked(s, support=3)
                    [star] = of_rule(s.store, "w")
                    self.assertIn(now.memory_id, {e.parent_id for e in s.store.parents_of(star.memory_id)})
                # the note retracted: the blocking version is back and blocks w's '*' conclusion again
                await s.retract(h.principal("a3"), other, "wrong note")
                await h.settle()
                self.assertEqual(s.store.get_memory(blocking.memory_id).status, "active")
                self.assertEqual(([m.entity for m in of_rule(s.store, "w")], [m.entity for m in of_rule(s.store, "v")]),
                                 (["e"], ["e"]))
                self.assertEqual(invariant_violations(s, ORG), [])

    # ------------------------------------------------------------------ one agent strongest in several slots
    async def test_an_agreeing_note_never_withdraws_a_conclusion_and_one_agent_never_blocks_a_coalition(self) -> None:
        """The shipped supply-risk rules need three agents in two teams.  x, y and z of three teams fill the three slots;
        x then confirms the thin buffer more confidently than y: the strongest note of two slots is x's, but x, y and z
        still qualify, so neither conclusion moves (same ids, nothing withdrawn).  In the other order (x's buffer note
        first) the conclusions appear as soon as z's note applies.  Of several qualifying selections the one whose
        weakest note is strongest counts."""
        component = RULES["component_supply_risk"]
        notes = {
            "x-tr": ("x", "Rotterdam strike blocks SD-9 inbound.", "transport_disruption", 0.9),
            "y-buf": ("y", "SD-9 buffer down to 2 days.", "supplier_buffer_low", 0.8),
            "z-dem": ("z", "Q4 SD-9 orders committed.", "demand_commitment", 0.8),
            "x-buf": ("x", "SD-9 buffer is thin, I confirm.", "supplier_buffer_low", 0.95),
        }
        for order in (("x-tr", "y-buf", "z-dem", "x-buf"), ("x-buf", "x-tr", "y-buf", "z-dem")):
            with self.subTest(order=order):
                h = await self.harness()
                s = h.service
                for body in (REGIONAL, component):
                    await s.upsert_rule(body)
                for agent, team in (("x", "logistics"), ("y", "procurement"), ("z", "planning")):
                    await h.register(agent, team=team)
                ids: dict[str, str] = {}
                for name in order:
                    agent, text, slot, confidence = notes[name]
                    ids[name] = await h.observe(agent, text, topic="supply:sd-9", slot=slot, entity=ENTITY,
                                                confidence=confidence)
                    if name == "z-dem":
                        await h.settle()
                        held = {m.rule_id: m.memory_id for m in s.store.list_memories(ORG, operator="slot_composition")}
                await h.settle()
                conclusions = {m.rule_id: m for m in s.store.list_memories(ORG, operator="slot_composition")}
                self.assertEqual(set(conclusions), {REGIONAL["rule_id"], component["rule_id"]})
                self.assertEqual({rule: m.memory_id for rule, m in conclusions.items()}, held, "same ids: nothing moved")
                self.assertEqual(of_rule(s.store, REGIONAL["rule_id"], status="retracted"), [])
                for m in conclusions.values():
                    self.assertEqual((m.support, m.independent_teams, m.confidence), (3, 3, 0.8))
                    self.assertEqual(m.metadata["slots"], {"transport_disruption": ids["x-tr"],
                                                           "supplier_buffer_low": ids["y-buf"],
                                                           "demand_commitment": ids["z-dem"]})
                    report = await s.verify(h.principal("z"), m.memory_id)
                    self.assertEqual((report["verdict"], report["derived_correctly"], report["still_true"]),
                                     ("verified", True, True))
                self.assertEqual(invariant_violations(s, ORG), [])
                # w, a fourth agent of a fourth team, is a stronger buffer note than y's: the selection whose weakest note
                # is strongest takes it (weakest 0.8 either way, then the earlier in the buffer ranking)
                await h.register("w", team="warehouse")
                ids["w-buf"] = await h.observe("w", "SD-9 buffer at 2 days in the warehouse too.", topic="supply:sd-9",
                                               slot="supplier_buffer_low", entity=ENTITY, confidence=0.85)
                await h.settle()
                [regional] = of_rule(s.store, REGIONAL["rule_id"])
                self.assertEqual(regional.metadata["slots"]["supplier_buffer_low"], ids["w-buf"])
                self.assertEqual(regional.metadata["version_of"], conclusions[REGIONAL["rule_id"]].memory_id)
                self.assertEqual(invariant_violations(s, ORG), [])

    async def test_same_candidate_successor_is_not_composed_twice(self) -> None:
        """The common supersession (a note joins a consolidation, a conclusion gains evidence) keeps the slot, entity and
        topic: offering the successor upward already composed the rule keys the old version fed, so the old version is
        not offered to the rules again (each note's apply would otherwise compose them twice)."""
        h = await self.harness()
        s = h.service
        await self.demo_agents(h)
        await self.demo_evidence(h, settle=False)
        await h.observe("proc-1", "SD-9 inbound lane congested.", topic="supply:sd-9/transport",
                        slot="transport_disruption", entity=ENTITY, confidence=0.6)
        await s.upsert_rule(REGIONAL)
        await h.settle()
        [c] = of_rule(s.store, REGIONAL["rule_id"])
        before = {m.memory_id for m in s.store.list_memories(ORG, status="active", limit=10_000) if m.layer != "agent"}
        offered: list[str] = []
        compose_rules = s.aggregator._compose_rules

        def spy(tx: Any, memory: Any) -> Any:
            offered.append(memory.memory_id)
            return compose_rules(tx, memory)

        s.aggregator._compose_rules = spy  # type: ignore[method-assign]
        await h.observe("log-1", "SD-9 port backlog now 9 days.", topic="supply:sd-9/transport",
                        slot="transport_disruption", entity=ENTITY, confidence=0.95)
        await h.settle()
        superseded = [m for m in map(s.store.get_memory, sorted(before)) if m.status == "superseded"]
        self.assertEqual(sorted((m.layer, m.operator) for m in superseded),
                         [("department", "topic_consolidation"), ("region", "slot_composition"), ("team", "topic_consolidation")],
                         "the note supersedes the consolidations of its topic and the regional conclusion it strengthens")
        for old in superseded:
            new = s.store.get_memory(old.superseded_by)
            self.assertIsNotNone(old.slot)
            self.assertEqual((new.slot, new.entity, new.topic), (old.slot, old.entity, old.topic))
            self.assertIn(new.memory_id, offered)
            self.assertNotIn(old.memory_id, offered, f"the old {old.layer} {old.operator} was composed again")
        [c2] = of_rule(s.store, REGIONAL["rule_id"])
        self.assertEqual(c2.metadata["version_of"], c.memory_id)
        self.assertEqual(s.aggregator.plan_for(c2).memory.memory_id, c2.memory_id)
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_identical_upsert_is_a_noop(self) -> None:
        h = await self.harness(rules_file=str(RULES_FILE))
        s1 = h.service
        await self.demo_agents(h)
        await self.demo_evidence(h)
        self.assertEqual(len(of_rule(s1.store, DEMO)), 1)
        self.assertEqual(len(of_rule(s1.store, "regional_supply_risk")), 1)
        before, events = memories_and_edges(s1.store), kinds(s1.store)
        await s1.upsert_rule(RULES[DEMO])
        await h.settle()
        self.assertEqual(memories_and_edges(s1.store), before, "an identical upsert leaves memories and lineage as they were")
        self.assertEqual(kinds(s1.store), {**events, "rule.upserted": events["rule.upserted"] + 1}, "only the rule event is added")
        # a restart with the same rules file appends nothing and changes nothing
        await s1.close()
        transport = s1.transport
        s2 = MycelicService(settings(h.tmp.name, rules_file=str(RULES_FILE)), transport=transport, metrics=Metrics())
        await s2.start()
        try:
            self.assertEqual(s2.load_rules_file(), 0)
            self.assertTrue(await s2.wait_idle(15))
            self.assertEqual(kinds(s2.store), {**events, "rule.upserted": events["rule.upserted"] + 1})
            self.assertEqual(memories_and_edges(s2.store), before)
            # a metadata-only edit does not derive differently
            await s2.upsert_rule({**RULES[DEMO], "metadata": {"owner": "someone else"}})
            self.assertTrue(await s2.wait_idle(15))
            self.assertEqual(memories_and_edges(s2.store), before)
            live = memory_history(s2.store)
        finally:
            await s2.close()
        # a fresh database rebuilt from the log appends the rules file again after the replay: no supersession
        s3 = MycelicService(settings(h.tmp.name, db_path=str(Path(h.tmp.name) / "fresh.db"), rules_file=str(RULES_FILE)),
                            transport=transport, metrics=Metrics())
        await s3.start()
        try:
            self.assertTrue(await s3.wait_idle(15))
            self.assertEqual(memory_history(s3.store), live)
            self.assertNotIn("superseded", {m[1] for m in memory_history(s3.store).values()})
            self.assertGreater(kinds(s3.store)["rule.upserted"], events["rule.upserted"] + 2, "the file's rules were appended")
        finally:
            await s3.close()

    async def test_rule_churn_rebuilds_to_identical_history(self) -> None:
        transport = HeldTransport(hold=False)
        h = await self.harness(transport=transport)
        s1 = h.service
        await self.demo_agents(h)
        await s1.upsert_rule(DEMO_RULE)
        await self.demo_evidence(h)
        [v1] = of_rule(s1.store, DEMO)
        # two upserts before the first applies: the first is evaluated, then superseded by the second
        transport.hold = True
        await s1.upsert_rule({**DEMO_RULE, "conclusion": "v2 {entity}: {slot:transport_disruption}"})
        await s1.upsert_rule({**DEMO_RULE, "conclusion": "v3 {entity}: {slot:transport_disruption}"})
        await drain_outbox(s1)
        transport.hold = False
        await h.settle()
        [v3] = of_rule(s1.store, DEMO)
        [v2] = [m for m in of_rule(s1.store, DEMO, status=None) if m.text.startswith("v2")]
        self.assertEqual((v2.metadata["version_of"], v2.superseded_by), (v1.memory_id, v3.memory_id), "one intermediate version")
        self.assertEqual(v3.metadata["version_of"], v2.memory_id)
        # tighten, loosen, disable, enable, delete, re-create: all behind consumer lag
        transport.hold = True
        for body in ({**DEMO_RULE, "min_agents": 4}, DEMO_RULE, {**DEMO_RULE, "enabled": False}, DEMO_RULE):
            await s1.upsert_rule(body)
        await s1.delete_rule(DEMO)
        await s1.upsert_rule(DEMO_RULE)
        await drain_outbox(s1)
        transport.hold = False
        await h.settle()
        self.assertEqual([m.memory_id for m in of_rule(s1.store, DEMO)], [v1.memory_id], "the original rule's conclusion is back")
        self.assertEqual(invariant_violations(s1, ORG), [])
        live = (memory_history(s1.store), lineage_edge_set(s1.store), applied_rule_rows(s1.store))
        await s1.stop()
        s2 = MycelicService(settings(h.tmp.name), store=MycelicStore(":memory:"), transport=transport, metrics=Metrics())
        await s2.start()
        try:
            self.assertTrue(await s2.wait_idle(15))
            self.assertEqual(memory_history(s2.store), live[0], "the rebuild reproduces every version and status")
            self.assertEqual(lineage_edge_set(s2.store), live[1])
            self.assertEqual(applied_rule_rows(s2.store), live[2])
            rederived = s2.store.list_events(ORG, kind="memory.derived", status="published", limit=1000)
            self.assertEqual({e.js_seq for e in rederived}, {None} if rederived else set(), "derived in the replay, never re-published")
            self.assertEqual(s2.store.stats()["outbox_pending"], 0)
        finally:
            await s2.close()

    # ------------------------------------------------------------------ organizations and layers
    async def test_global_rule_org_narrowing_and_ruleless_org(self) -> None:
        h = await self.harness()
        s = h.service
        rule = {"rule_id": "g", "target_layer": "team", "required_slots": ["x"], "corroborate": True, "min_agents": 2,
                "conclusion": "G {entity}: {slot:x}"}
        for a in ("log-1", "log-2"):
            await h.register(a, team="logistics")
        for a in ("a-1", "a-2"):
            await h.register(a, team="t1", department="ops", subsidiary="acme-gmbh", region="emea", enterprise="acme")
        await s.upsert_rule(rule)
        for a in ("log-1", "log-2", "a-1", "a-2"):
            await h.observe(a, f"x by {a}", slot="x", entity="e")
        await h.settle()
        [nw] = of_rule(s.store, "g")
        [acme] = of_rule(s.store, "g", org_id="acme")

        def org_rows(org: str) -> list[tuple]:
            return [tuple(r) for r in s.store._conn.execute("SELECT * FROM memories WHERE org_id=? ORDER BY rid", (org,))]

        northwind = org_rows(ORG)
        await s.upsert_rule({**rule, "org_id": ORG})
        await h.settle()
        self.assertEqual(of_rule(s.store, "g", org_id="acme"), [])
        self.assertEqual(s.store.get_memory(acme.memory_id).metadata["status_reason"], "rule no longer applies here")
        self.assertEqual(org_rows(ORG), northwind, "the organization it still covers keeps its conclusion: no churn")
        self.assertEqual(rule_digest(s.store.get_applied_rule("g")), nw.metadata["derivation"]["rule_digest"])
        # a rule for an organization with no agents and no memories applies as a no-op
        everything = table_dump(s.store, "memories")
        await s.upsert_rule({**rule, "rule_id": "nobodys", "org_id": "nobody"})
        await h.settle()
        [ev] = s.store.list_events("nobody", kind="rule.upserted", limit=10)
        self.assertEqual(ev.status, "applied")
        self.assertEqual(s.aggregator.rule_keys(s.store.get_applied_rule("nobodys")), [])
        self.assertEqual(table_dump(s.store, "memories"), everything)
        for org in (ORG, "acme"):
            self.assertEqual(invariant_violations(s, org), [])

    async def test_target_layer_change_moves_conclusions(self) -> None:
        h = await self.harness()
        s = h.service
        rule = {"rule_id": "mv", "target_layer": "team", "required_slots": ["x"], "corroborate": True, "min_agents": 1,
                "conclusion": "MV {entity}: {slot:x}"}
        await h.register("log-1", team="logistics")
        await h.register("proc-1", team="procurement")
        await s.upsert_rule(rule)
        n1 = await h.observe("log-1", "x by log-1", slot="x", entity="e")
        await h.observe("proc-1", "x by proc-1", slot="x", entity="e")
        await h.settle()
        teams = of_rule(s.store, "mv")
        self.assertEqual({m.layer for m in teams}, {"team"})
        self.assertEqual(len(teams), 2)
        await s.upsert_rule({**rule, "target_layer": "department"})
        await h.settle()
        self.assertEqual({s.store.get_memory(m.memory_id).metadata["status_reason"] for m in teams}, {"rule no longer applies here"})
        [dept] = of_rule(s.store, "mv")
        self.assertEqual((dept.layer, dept.scope, dept.support), ("department", "northwind/emea/nw-gmbh/ops", 2))
        # evidence above a rule's target layer gives it nothing to evaluate
        await s.upsert_rule({"rule_id": "p", "target_layer": "department", "required_slots": ["x"], "corroborate": True,
                             "min_agents": 1, "emits_slot": "q", "conclusion": "P {entity}"})
        await s.upsert_rule({"rule_id": "q", "target_layer": "team", "required_slots": ["q"], "sources": ["slot_composition"],
                             "min_agents": 1, "conclusion": "Q {entity}"})
        await h.settle()
        self.assertEqual(len(of_rule(s.store, "p")), 1)
        self.assertEqual(s.aggregator.rule_keys(s.store.get_applied_rule("q")), [])
        self.assertEqual(of_rule(s.store, "q", status=None), [])
        # a node that applied a move before rule events were evaluated left a conclusion at a layer its rule no longer
        # targets: when its evidence changes it is withdrawn, never rebuilt at its old unit under the moved rule
        async with s.store.transaction() as tx:
            tx.upsert_applied_rule(Rule(**{**s.store.get_applied_rule("mv").to_dict(), "target_layer": "subsidiary"}),
                                   s.store.max_apply_seq())
        await s.retract(h.principal("log-1"), n1, "withdrawn")
        await h.settle()
        self.assertEqual(s.store.get_memory(dept.memory_id).status, "retracted")
        active = s.store.list_memories(ORG, limit=10_000)
        self.assertEqual([m.memory_id for m in active if m.layer != layer_of_path(m.scope)], [],
                         "no memory's layer contradicts its unit")
        self.assertEqual([(m.layer, m.scope) for m in active if m.rule_id == "mv"], [("subsidiary", "northwind/emea/nw-gmbh")])
        self.assertEqual(invariant_violations(s, ORG), [])

    # ------------------------------------------------------------------ versioned ids and their metadata
    async def test_derivation_metadata_is_reserved_and_redacted(self) -> None:
        h = await self.harness()
        s = h.service
        digest = lambda body: rule_digest(s._rule_from_body(body))  # noqa: E731 (the real normalisation and validation)
        self.assertEqual(digest(DEMO_RULE), "d76ea7aa716bca15")
        self.assertEqual(digest(RULES[DEMO]), "d76ea7aa716bca15", "their metadata differs, which is not part of the digest")
        self.assertEqual(digest(REGIONAL), "431987b88754db0f")
        self.assertEqual(digest(STRATEGIC), "06f107b308df695d")
        for same in ({"enabled": False}, {"metadata": {"owner": "x"}}, {"org_id": ORG}, {"required_slots": [" Transport_Disruption",
                     "supplier_buffer_low", "demand_commitment"]}):
            with self.subTest(same=same):
                self.assertEqual(digest({**DEMO_RULE, **same}), digest(DEMO_RULE))
        for other in ({"min_agents": 4}, {"min_teams": 1}, {"conclusion": "x {entity}"}, {"target_layer": "region"},
                      {"topic_prefix": "ops:"}, {"emits_slot": "z"}, {"emits_topic": "z"}, {"corroborate": True}, {"kind": "fact"},
                      {"sources": ["agent_observation", "slot_composition"]}, {"min_units": {"demand_commitment": {"team": 2}}}):
            with self.subTest(other=other):
                self.assertNotEqual(digest({**DEMO_RULE, **other}), digest(DEMO_RULE))
        self.assertEqual(set(rule_snapshot(s._rule_from_body(DEMO_RULE))),
                         set(Rule.__dataclass_fields__) - {"enabled", "metadata", "org_id"})
        await self.demo_agents(h)
        await s.upsert_rule(DEMO_RULE)
        await h.observe("log-1", "x", topic="ops:x", metadata={"derivation": {"v": 99}, "note": "kept"})
        await h.observe("log-2", "y", topic="ops:x")
        await self.demo_evidence(h)
        [own] = [m for m in s.store.list_memories(ORG, layers=["agent"], producer_id="log-1") if m.topic == "ops:x"]
        self.assertEqual(own.metadata, {"note": "kept"}, "an agent cannot set the derivation")
        [team] = [m for m in s.store.list_memories(ORG, layers=["team"]) if m.topic == "ops:x"]
        self.assertEqual(team.metadata["derivation"], {"v": DERIVATION_VERSION, "min_support": 2})
        [c] = of_rule(s.store, DEMO)
        agent_view = s.public_view(c, h.principal("sales-1"))
        self.assertEqual(agent_view["metadata"]["derivation"], {"v": DERIVATION_VERSION, "rule_digest": "d76ea7aa716bca15"})
        self.assertEqual(s.public_view(team, h.principal("sales-1"))["metadata"]["derivation"], {"v": DERIVATION_VERSION, "min_support": 2})
        self.assertEqual(s.public_view(c, h.admin)["metadata"]["derivation"]["rule"], rule_snapshot(s.store.get_applied_rule(DEMO)))

    # ------------------------------------------------------------------ registry changes
    async def register_at(self, h: ServiceHarness, agent_id: str, **units: str) -> None:
        """Register with only the units given (missing ones default to the enterprise name: a sparse path)."""
        _, key = await h.service.register_agent({**units, "agent_id": agent_id})
        h.keys[agent_id] = key

    def promotions(self, s: MycelicService, org: str, topic: str) -> dict[str, Any]:
        return {m.layer: m for m in s.store.list_memories(org, limit=10_000) if m.topic == topic and m.layer != "agent"}

    async def test_new_child_unit_withdraws_promotion_and_revocation_restores_it(self) -> None:
        transport = HeldTransport(hold=False)
        h = await self.harness(transport=transport)
        s = h.service
        for a in ("log-1", "log-2"):
            await h.register(a, team="logistics")
        for a in ("log-1", "log-2"):
            await h.observe(a, f"pallets short, says {a}", topic="ops:x")
        await h.settle()
        chain = self.promotions(s, ORG, "ops:x")
        self.assertEqual(set(chain), {"team", "department", "subsidiary", "region", "enterprise"})
        self.assertTrue(all(chain[layer].metadata["promoted_from"] for layer in ("department", "subsidiary", "region", "enterprise")))
        promoted = {layer: chain[layer].memory_id for layer in ("department", "subsidiary", "region", "enterprise")}
        # a second team in the department: its promotion ends once the registration applies, and everything above it
        transport.hold = True
        await h.register("proc-1", team="procurement")
        await drain_outbox(s)
        self.assertEqual(set(self.promotions(s, ORG, "ops:x")), set(chain), "not before the registration applies")
        transport.hold = False
        await h.settle()
        self.assertEqual(set(self.promotions(s, ORG, "ops:x")), {"team"})
        self.assertEqual({layer: s.store.get_memory(mid).status for layer, mid in promoted.items()},
                         dict.fromkeys(promoted, "retracted"))
        self.assertEqual(s.store.get_memory(promoted["department"]).metadata["status_reason"], "support below threshold")
        self.assertEqual(invariant_violations(s, ORG), [])
        # a topic both teams contribute to is not a promotion: revoking the procurement agent keeps it, and its note
        await h.observe("log-1", "both teams: dock 4 closed", topic="ops:z")
        z_note = await h.observe("proc-1", "both teams: dock 4 closed", topic="ops:z")
        await h.settle()
        [z_dept] = [m for m in s.store.list_memories(ORG, layers=["department"]) if m.topic == "ops:z"]
        self.assertIsNone(z_dept.metadata["promoted_from"])
        # registration into an existing team, a duplicate registration, a revocation of an unknown agent: nothing to do
        unchanged = memories_and_edges(s.store)
        await h.register("log-3", team="logistics")
        await h.settle()
        self.assertEqual(await s.apply_event(wire("agent.registered", {**s.store.get_agent("log-3").to_dict(), "key_hash": "x"})),
                         "duplicate")
        self.assertEqual(await s.apply_event(wire("agent.revoked", {"agent_id": "ghost"})), "applied")
        self.assertEqual(memories_and_edges(s.store), unchanged)
        # the procurement team's only agent leaves: the department promotes again, with the same ids
        await s.revoke_agent("proc-1")
        await h.settle()
        self.assertEqual({layer: m.memory_id for layer, m in self.promotions(s, ORG, "ops:x").items() if layer != "team"}, promoted)
        self.assertEqual(s.store.get_memory(z_note).status, "active", "a revoked agent's notes stay evidence")
        self.assertEqual(s.store.get_memory(z_dept.memory_id).status, "active", "so the department's contributions are unchanged")
        replayed = memories_and_edges(s.store)
        self.assertEqual(await s.apply_event(wire("agent.revoked", {"agent_id": "proc-1"})), "applied")
        self.assertEqual(memories_and_edges(s.store), replayed, "a replayed revocation changes nothing")
        # an agent in a brand-new region: the enterprise no longer promotes its one region; revoked, it does again
        await h.register("apac-1", team="logistics", subsidiary="nw-kk", region="apac")
        await h.settle()
        self.assertEqual(s.store.get_memory(promoted["enterprise"]).status, "retracted")
        self.assertEqual(s.store.get_memory(promoted["region"]).status, "active")
        await s.revoke_agent("apac-1")
        await h.settle()
        self.assertEqual(s.store.get_memory(promoted["enterprise"]).status, "active")
        self.assertEqual(invariant_violations(s, ORG), [])
        # a sparse default-filled path (acme/acme/acme/acme/t5/...) next to a full one
        for a in ("s-1", "s-2"):
            await self.register_at(h, a, enterprise="acme", team="t5")
        self.assertEqual(s.store.get_agent("s-1").path, "acme/acme/acme/acme/t5/s-1")
        for a in ("s-1", "s-2"):
            await h.observe(a, f"sparse note by {a}", topic="ops:y")
        await h.settle()
        sparse = self.promotions(s, "acme", "ops:y")
        self.assertEqual([sparse[layer].scope for layer in ("department", "subsidiary", "region", "enterprise")],
                         ["acme/acme/acme/acme", "acme/acme/acme", "acme/acme", "acme"])
        await self.register_at(h, "f-1", enterprise="acme", region="emea", subsidiary="gmbh", department="ops", team="t1")
        await h.settle()
        self.assertEqual(s.store.get_memory(sparse["enterprise"].memory_id).status, "retracted", "a second region")
        self.assertEqual(s.store.get_memory(sparse["region"].memory_id).status, "active")
        await s.revoke_agent("f-1")
        await h.settle()
        self.assertEqual({layer: m.memory_id for layer, m in self.promotions(s, "acme", "ops:y").items()},
                         {layer: m.memory_id for layer, m in sparse.items()})
        self.assertEqual(invariant_violations(s, "acme"), [])



class RuleOrganizationTests(unittest.IsolatedAsyncioTestCase):
    """A rule's ``org_id`` names an organization: anything else is refused where rules come in (admin API, rules file),
    and an event whose subject the stream can never take (an earlier release accepted such rules) is marked failed
    instead of holding up the outbox of every organization behind it."""

    TYPO = {"rule_id": "typo_rule", "target_layer": "team", "required_slots": ["x"], "conclusion": "c {entity}"}

    async def harness(self, **kw: Any) -> ServiceHarness:
        h = await ServiceHarness(**kw).start()
        self.addAsyncCleanup(h.close)
        return h

    async def test_org_id_that_is_no_organization_name_is_refused(self) -> None:
        from mycelic.service import ValidationError

        h = await self.harness()
        s = h.service
        before = kinds(s.store).get("rule.upserted", 0)
        for org_id in ("north wind", "Northwind", "../../etc", "acme.eu", "_", "-acme", "a" * 65, "acme*", "acme>"):
            with self.assertRaises(ValidationError, msg=org_id):
                await s.upsert_rule({**self.TYPO, "org_id": org_id})
        self.assertIsNone(s.store.get_rule("typo_rule"))
        self.assertEqual(kinds(s.store).get("rule.upserted", 0), before, "nothing was appended to the log")
        rule = await s.upsert_rule({**self.TYPO, "org_id": "north-wind_2"})
        self.assertEqual(rule.org_id, "north-wind_2")
        self.assertIsNone((await s.upsert_rule({**self.TYPO, "org_id": None})).org_id)
        self.assertIsNone((await s.upsert_rule({**self.TYPO, "org_id": ""})).org_id)

    async def test_rules_file_with_a_bad_org_id_fails_the_start(self) -> None:
        import tempfile

        from mycelic.service import ValidationError

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "rules.json"
        path.write_text(json.dumps({"rules": [{**self.TYPO, "org_id": "North Wind"}]}))
        s = MycelicService(settings(tmp.name, rules_file=str(path)), metrics=Metrics())
        self.addAsyncCleanup(s.close)
        with self.assertRaises(ValidationError) as ctx:
            s.load_rules_file()
        self.assertIn("org_id", str(ctx.exception))
        self.assertIsNone(s.store.get_rule("typo_rule"))

    async def test_a_logged_rule_is_applied_as_written(self) -> None:
        """The log is authoritative: a rule event an earlier release published with an org_id that is no organization
        name is applied (it matches no organization), never refused at every delivery."""
        h = await self.harness()
        s = h.service
        rule = {**self.TYPO, "org_id": "Northwind", "enabled": True}
        self.assertEqual(await s.apply_event(wire("rule.upserted", rule, org_id="Northwind")), "applied")
        self.assertEqual(s.store.get_applied_rule("typo_rule").org_id, "Northwind")

    async def test_unpublishable_subject_never_blocks_the_outbox(self) -> None:
        h = await self.harness()
        s = h.service
        await h.register("a-1", team="t1")
        await h.register("a-2", team="t1")
        await h.register("b-1", team="t1", enterprise="acme")
        await h.register("b-2", team="t1", enterprise="acme")
        await h.settle()
        # what an earlier release wrote for {"org_id": "north wind"}: an event on a subject no stream takes, first in line
        bad = [("evt_badsubject0000000000001", "mycelic.north wind.rule-upserted"),
               ("evt_badsubject0000000000002", "mycelic.../../etc.rule-upserted"),
               ("evt_badsubject0000000000003", "mycelic.acme*.rule-upserted")]
        async with s.store.transaction() as tx:
            for event_id, subject in bad:
                tx.c.execute("INSERT INTO events(event_id, kind, org_id, subject, payload, status, created_at) "
                             "VALUES (?, 'rule.upserted', 'north wind', ?, ?, 'pending', ?)",
                             (event_id, subject, json.dumps({**self.TYPO, "org_id": "north wind"}), now_iso()))
        with self.assertLogs("mycelic.service", "ERROR") as logs:
            for agent in ("a-1", "a-2"):
                await h.observe(agent, f"Dock 4 closed ({agent}).", topic="ops:docks")
            for agent in ("b-1", "b-2"):
                await h.observe(agent, f"Dock 9 closed ({agent}).", topic="ops:docks")
            await h.settle()
        self.assertTrue(any("cannot be published" in line for line in logs.output))
        for event_id, _ in bad:
            ev = s.store.get_event(event_id)
            self.assertEqual(ev.status, "failed")
            self.assertIn("not one the stream takes", ev.last_error)
        self.assertEqual(s.store.stats()["outbox_pending"], 0)
        for org in (ORG, "acme"):
            self.assertEqual(len(s.store.list_memories(org, layers=["team"], status="active")), 1, org)
        self.assertEqual(s.metrics.events_failed.labels("publish_permanent")._value.get(), len(bad))
        self.assertEqual({r["target"] for r in s.store.recent_audit(100) if r["action"] == "event.unpublishable"},
                         {event_id for event_id, _ in bad})
        self.assertEqual((await s.health())["status"], "ok")


if __name__ == "__main__":
    unittest.main()
