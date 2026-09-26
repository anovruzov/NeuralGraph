"""The complete data path over the in-process transport: observe -> event -> apply -> aggregate -> query -> lineage,
plus visibility/redaction, idempotency, supersession, retraction and a rebuild from the event log."""
from __future__ import annotations

import unittest

from mycelic.metrics import Metrics
from mycelic.service import Forbidden, MycelicService, NotFound, ValidationError
from mycelic.store import MycelicStore

from .helpers import DEMO_RULE, ServiceHarness, settings

TRANSPORT = "supply:sd-9/transport"


class DataPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.h = await ServiceHarness().start()
        s = self.h.service
        await s.upsert_rule(DEMO_RULE)
        for a in ("log-1", "log-2"):
            await self.h.register(a, team="logistics")
        await self.h.register("proc-1", team="procurement")
        for a in ("sales-1", "sales-2"):
            await self.h.register(a, team="field-sales", department="commercial")

    async def asyncTearDown(self) -> None:
        await self.h.close()

    async def seed(self) -> dict[str, str]:
        ids = {}
        ids["log-1"] = await self.h.observe("log-1", "Port of Rotterdam terminal 3 strike announced for weeks 41-43; SD-9 servo drive shipments route through it.",
                                            topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.9)
        ids["log-2"] = await self.h.observe("log-2", "Carrier ETA for the SD-9 servo drive container slipped by 12 days.",
                                            topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.7)
        ids["proc-1"] = await self.h.observe("proc-1", "Kessler Antriebe confirms roughly two weeks of SD-9 inventory left.",
                                             topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity="sd-9", confidence=0.85)
        ids["sales-1"] = await self.h.observe("sales-1", "Helios Automation committed to 40 RX-4 arms for November; every RX-4 uses an SD-9 drive.",
                                              topic="supply:sd-9/demand", slot="demand_commitment", entity="sd-9", confidence=0.8)
        await self.h.settle()
        return ids

    async def test_observations_become_team_and_enterprise_memories_with_lineage(self) -> None:
        ids = await self.seed()
        s = self.h.service
        st = s.store.stats()
        self.assertEqual(st["memories_by_layer"]["agent"], 4)
        self.assertEqual(st["memories_by_layer"]["team"], 1, "logistics consolidates its two observations")
        self.assertEqual(st["memories_by_layer"]["enterprise"], 1, "the rule fires once all three slots are covered")
        self.assertEqual(st["outbox_pending"], 0)
        self.assertEqual(st["events"].get("pending", 0), 0)

        # query from a different team's agent, at enterprise scope
        res = s.query(self.h.principal("sales-2"), {"query": "delivery risk for RX-4 and the SD-9 servo drive", "scope": "northwind"})
        self.assertIsNotNone(res["answer"])
        top = res["answer"]
        self.assertEqual(top["layer"], "enterprise")
        self.assertIn("Supply risk for sd-9", top["text"])
        self.assertEqual(top["support"], 3)
        self.assertEqual(top["independent_teams"], 3)
        self.assertEqual(top["lineage"]["contributing_agents"], 3)
        self.assertTrue(top["lineage"]["reconstructable"])
        self.assertEqual(top["lineage"]["layers"], ["agent", "enterprise"])

        g = res["lineage"]
        self.assertEqual(set(g["roots"]), {ids["log-1"], ids["proc-1"], ids["sales-1"]}, "the strongest transport observation is selected")
        self.assertEqual(g["redacted_contributions"], 2, "sales-2 may not read logistics' or procurement's raw notes")
        self.assertEqual(g["contributing_agents"], ["sales-1"])
        self.assertEqual(len(g["contributing_teams"]), 3)
        self.assertIsNone(g["nodes"][ids["log-1"]]["text"])
        self.assertTrue(g["nodes"][ids["log-1"]]["redacted"])
        self.assertEqual(g["nodes"][ids["log-1"]]["scope"], "northwind/emea/nw-gmbh/ops/logistics")
        self.assertIsNotNone(g["nodes"][ids["sales-1"]]["text"])
        self.assertTrue(g["evidence"]["reconstructable"])
        self.assertEqual(g["support"]["fragility"]["unique_failure_domain_count"], 3)
        self.assertIsNotNone(g["timeline"]["first_observed_at"])

        # the same lineage seen by an administrator has nothing redacted
        ga = s.lineage(self.h.admin, top["memory_id"])
        self.assertEqual(ga["redacted_contributions"], 0)
        self.assertEqual(ga["contributing_agents"], ["log-1", "proc-1", "sales-1"])

        # team consolidation is visible to its own team only
        team = s.store.list_memories("northwind", layers=["team"])[0]
        self.assertEqual(team.support, 2)
        self.assertEqual(team.scope, "northwind/emea/nw-gmbh/ops/logistics")
        self.assertAlmostEqual(team.confidence, 0.97, places=2)
        self.assertEqual(s.get_memory(self.h.principal("log-2"), team.memory_id).memory_id, team.memory_id)
        with self.assertRaises(NotFound):
            s.get_memory(self.h.principal("sales-2"), team.memory_id)
        with self.assertRaises(NotFound):
            s.get_memory(self.h.principal("sales-2"), ids["log-1"])
        with self.assertRaises(Forbidden):
            s.query(self.h.principal("sales-2"), {"query": "x", "scope": "northwind/emea/nw-gmbh/ops"})
        tl = s.lineage(self.h.principal("log-1"), team.memory_id)
        self.assertEqual(tl["layers"], ["agent", "team"])
        self.assertEqual(tl["contributing_agents"], ["log-1", "log-2"])

    async def test_idempotent_resend_and_write_as_self(self) -> None:
        p = self.h.principal("log-1")
        s = self.h.service
        m1, created1 = await s.ingest_memory(p, {"text": "same note", "idempotency_key": "note-1"})
        m2, created2 = await s.ingest_memory(p, {"text": "same note", "idempotency_key": "note-1"})
        self.assertEqual(m1.memory_id, m2.memory_id)
        self.assertEqual((created1, created2), (True, False))
        with self.assertRaises(Forbidden):
            await s.ingest_memory(p, {"text": "spoof", "agent_id": "log-2"})
        with self.assertRaises(Forbidden):
            await s.ingest_memory(self.h.admin, {"text": "admins do not observe"})

    async def test_growing_coalition_supersedes_and_keeps_history(self) -> None:
        await self.seed()
        s = self.h.service
        old = s.store.list_memories("northwind", layers=["team"])[0]
        await self.h.register("log-3", team="logistics")
        await self.h.observe("log-3", "Rotterdam customs broker warns of a two-week backlog for SD-9 containers.",
                             topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.6)
        await self.h.settle()
        active = s.store.list_memories("northwind", layers=["team"])
        self.assertEqual(len(active), 1)
        new = active[0]
        self.assertNotEqual(new.memory_id, old.memory_id)
        self.assertEqual(new.support, 3)
        stale = s.store.get_memory(old.memory_id)
        self.assertEqual(stale.status, "superseded")
        self.assertEqual(stale.superseded_by, new.memory_id)
        g = s.lineage(self.h.principal("log-1"), new.memory_id)
        self.assertEqual(g["previous_versions"], [old.memory_id])
        self.assertEqual(len(g["roots"]), 3)

    async def test_retraction_withdraws_dependent_conclusions(self) -> None:
        ids = await self.seed()
        s = self.h.service
        conclusion = s.store.list_memories("northwind", layers=["enterprise"])[0]
        await s.retract(self.h.principal("sales-1"), ids["sales-1"], "customer cancelled the order")
        await self.h.settle()
        self.assertEqual(s.store.get_memory(ids["sales-1"]).status, "retracted")
        self.assertEqual(s.store.get_memory(conclusion.memory_id).status, "retracted")
        self.assertEqual(s.store.list_memories("northwind", layers=["enterprise"]), [])
        res = s.query(self.h.principal("sales-2"), {"query": "supply risk sd-9", "scope": "northwind", "min_layer": "enterprise"})
        self.assertIsNone(res["answer"])
        # derived memories cannot be retracted directly: they follow their evidence
        team = s.store.list_memories("northwind", layers=["team"])[0]
        with self.assertRaises(ValidationError):
            await s.retract(self.h.admin, team.memory_id)
        for agent in ("log-1", "log-2"):
            await s.retract(self.h.principal(agent), ids[agent], "withdrawn")
        await self.h.settle()
        self.assertEqual(s.store.list_memories("northwind", layers=["team"]), [])
        await self.h.observe("proc-1", "Unrelated note about pallets.", topic="ops:pallets")
        await self.h.settle()
        self.assertEqual(s.store.list_memories("northwind", layers=["team"]), [], "retracted evidence does not come back")
        with self.assertRaises(NotFound):           # another team's raw note: not even its existence is revealed
            await s.retract(self.h.principal("sales-2"), ids["log-1"])
        with self.assertRaises(Forbidden):          # visible (enterprise-level) but not the producer
            await s.retract(self.h.principal("sales-2"), conclusion.memory_id)

    async def test_coalition_that_shrinks_back_reactivates_the_earlier_version(self) -> None:
        await self.seed()
        s = self.h.service
        first = s.store.list_memories("northwind", layers=["team"])[0]
        await self.h.register("log-3", team="logistics")
        third = await self.h.observe("log-3", "Customs broker warns of a backlog for SD-9 containers.",
                                     topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.6)
        await self.h.settle()
        second = s.store.list_memories("northwind", layers=["team"])[0]
        self.assertNotEqual(first.memory_id, second.memory_id)
        await s.retract(self.h.principal("log-3"), third, "false alarm")
        await self.h.settle()
        active = s.store.list_memories("northwind", layers=["team"])
        self.assertEqual([m.memory_id for m in active], [first.memory_id], "the earlier coalition is current again")
        self.assertEqual(s.store.get_memory(second.memory_id).status, "retracted")
        g = s.lineage(self.h.principal("log-1"), first.memory_id)
        self.assertEqual(set(g["contributing_agents"]), {"log-1", "log-2"})
        self.assertTrue(g["evidence"]["reconstructable"])

    async def test_reactivated_version_keeps_its_history(self) -> None:
        await self.seed()
        s = self.h.service
        t1 = s.store.list_memories("northwind", layers=["team"])[0]
        await self.h.register("log-3", team="logistics")
        await self.h.observe("log-3", "Customs backlog for SD-9 containers.", topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.6)
        await self.h.settle()
        t2 = s.store.list_memories("northwind", layers=["team"])[0]
        await self.h.register("log-4", team="logistics")
        third = await self.h.observe("log-4", "Rail alternative for SD-9 is fully booked.", topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.5)
        await self.h.settle()
        t3 = s.store.list_memories("northwind", layers=["team"])[0]
        self.assertEqual(len({t1.memory_id, t2.memory_id, t3.memory_id}), 3)
        await s.retract(self.h.principal("log-4"), third, "mistaken")
        await self.h.settle()
        active = s.store.list_memories("northwind", layers=["team"])
        self.assertEqual([m.memory_id for m in active], [t2.memory_id])
        self.assertEqual(s.lineage(self.h.admin, t2.memory_id)["previous_versions"], [t1.memory_id])

    async def test_agent_input_cannot_impersonate_aggregator_metadata(self) -> None:
        await self.seed()
        s = self.h.service
        conclusion = s.store.list_memories("northwind", layers=["enterprise"])[0]
        await self.h.observe("sales-2", "Nothing to see here.", topic="misc", visibility="org",
                             metadata={"promoted_from": conclusion.memory_id, "agg_key": "k", "version_of": "x", "note": "kept"})
        await self.h.observe("sales-2", "Second note.", topic="misc", metadata={"agg_key": "k"})
        await self.h.settle()
        mine = s.store.list_memories("northwind", layers=["agent"], producer_id="sales-2")
        self.assertEqual(len(mine), 2)
        self.assertNotIn("promoted_from", mine[0].metadata)
        self.assertIn("note", {k for m in mine for k in m.metadata})
        res = s.query(self.h.principal("log-1"), {"query": "supply risk sd-9", "scope": "northwind", "min_layer": "enterprise"})
        self.assertEqual(res["answer"]["memory_id"], conclusion.memory_id, "an org-visible note cannot hide a conclusion")

    async def test_source_events_must_belong_to_the_organization(self) -> None:
        s = self.h.service
        with self.assertRaises(ValidationError):
            await s.ingest_memory(self.h.principal("log-1"), {"text": "x", "source_event_ids": ["evt_doesnotexist"]})
        await self.h.register("acme-1", team="acme", department="acme", subsidiary="acme", region="acme", enterprise="acme")
        out = await s.ingest_events(self.h.principal("acme-1"), [{"type": "x"}])
        with self.assertRaises(ValidationError):
            await s.ingest_memory(self.h.principal("log-1"), {"text": "x", "source_event_ids": [out[0]["event_id"]]})

    async def test_reserved_producer_id_and_metadata_visibility(self) -> None:
        s = self.h.service
        with self.assertRaises(ValidationError):
            await s.register_agent({"enterprise": "northwind", "agent_id": "mycelic"})
        await self.seed()
        conclusion = s.store.list_memories("northwind", layers=["enterprise"])[0]
        default = self.h.principal("sales-2")
        self.assertIn("roots", s.public_view(conclusion, default)["metadata"])
        self.assertNotIn("evidence", s.public_view(conclusion, default)["metadata"])
        await self.h.register("reader", team="field-sales", department="commercial", scopes=["memory:read"])
        reader = self.h.principal("reader")
        self.assertNotIn("roots", s.public_view(conclusion, reader)["metadata"])
        self.assertFalse(reader.owns(conclusion))
        self.assertFalse(self.h.principal("log-1").owns(conclusion), "derived memories have no owner but the organization")

    async def test_local_reference_is_private_to_the_producer(self) -> None:
        s = self.h.service
        mid = await self.h.observe("log-1", "Terminal 3 strike.", topic=TRANSPORT, local_ref="note-42")
        await self.h.settle()
        self.assertEqual(s.lineage(self.h.principal("log-1"), mid)["nodes"][mid]["local_ref"], "note-42")
        self.assertEqual(s.lineage(self.h.admin, mid)["nodes"][mid]["local_ref"], "note-42")
        self.assertIsNone(s.lineage(self.h.principal("log-2"), mid)["nodes"][mid]["local_ref"])
        self.assertIsNone(s.public_view(s.get_memory(self.h.principal("log-2"), mid), self.h.principal("log-2"))["local_ref"])

    async def test_events_with_embedded_memories_and_evidence(self) -> None:
        s = self.h.service
        out = await s.ingest_events(self.h.principal("proc-1"), [
            {"type": "supplier.call", "payload": {"supplier": "Kessler"}, "idempotency_key": "call-1",
             "memory": {"text": "Kessler has two weeks of SD-9 stock.", "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": "sd-9"}},
            {"type": "heartbeat"},
        ])
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0]["created"])
        mem = s.store.get_memory(out[0]["memory_id"])
        self.assertEqual(mem.source_event_ids, [out[0]["event_id"]])
        again = await s.ingest_events(self.h.principal("proc-1"), [{"type": "supplier.call", "idempotency_key": "call-1"}])
        self.assertFalse(again[0]["created"])
        # re-sending the full item (memory included) is idempotent for the embedded memory too
        for _ in range(2):
            rep = await s.ingest_events(self.h.principal("proc-1"), [
                {"type": "supplier.call", "payload": {"supplier": "Kessler"}, "idempotency_key": "call-1",
                 "memory": {"text": "Kessler has two weeks of SD-9 stock.", "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": "sd-9"}}])
            self.assertEqual(rep[0]["memory_id"], out[0]["memory_id"])
            self.assertFalse(rep[0].get("memory_created", True))
        self.assertEqual(len(s.store.list_memories("northwind", layers=["agent"], producer_id="proc-1")), 1)
        await self.h.settle()
        g = s.lineage(self.h.principal("proc-1"), mem.memory_id)
        self.assertIn(out[0]["event_id"], g["evidence"]["source_event_ids"])
        self.assertTrue(g["evidence"]["reconstructable"])

    async def test_rebuild_from_the_event_log_reproduces_state_agents_and_rules(self) -> None:
        await self.seed()
        s1 = self.h.service
        # a burst: the API writes land before the consumer applies them; aggregation must only see applied ones
        # so that a rebuild derives exactly the same history (every version, not only the active set)
        for i, agent in enumerate(("log-1", "log-2", "proc-1")):
            await self.h.observe(agent, f"Burst note {i} about the SD-9 servo drive delay.", topic="supply:sd-9/burst", confidence=0.5)
        await self.h.settle()
        full_before = {m.memory_id: (m.layer, m.status, m.support, m.metadata.get("version_of"))
                       for m in s1.store.list_memories("northwind", status=None, limit=1000)}
        events_before = s1.store.event_counts()
        before = {m.memory_id: (m.layer, m.status, m.support) for m in s1.store.list_memories("northwind", status=None, limit=1000)}
        active_before = {mid for mid, (_, st, _) in before.items() if st == "active"}
        transport = s1.transport
        await s1.stop()

        # a brand-new database over the same log: state, agents (their keys) and rules come back from the stream
        fresh = MycelicStore(":memory:")
        s2 = MycelicService(settings(self.h.tmp.name), store=fresh, transport=transport, metrics=Metrics())
        await s2.start()
        try:
            self.assertTrue(await s2.wait_idle(10))
            after = {m.memory_id: (m.layer, m.status, m.support) for m in s2.store.list_memories("northwind", status=None, limit=1000)}
            active_after = {mid for mid, (_, st, _) in after.items() if st == "active"}
            self.assertEqual(active_after, active_before)
            self.assertEqual({k: v for k, v in after.items() if k in active_after}, {k: v for k, v in before.items() if k in active_before})
            self.assertEqual(len(s2.store.list_rules("northwind")), 1)
            p = s2.authenticate(f"Bearer {self.h.keys['sales-2']}")
            res = s2.query(p, {"query": "supply risk sd-9", "scope": "northwind"})
            self.assertEqual(res["answer"]["layer"], "enterprise")
            self.assertTrue(res["answer"]["lineage"]["reconstructable"])
            self.assertEqual(s2.store.stats()["outbox_pending"], 0, "replay must not re-publish derived events")
            self.assertGreater(s2.metrics.replay_events._value.get(), 0)
            full_after = {m.memory_id: (m.layer, m.status, m.support, m.metadata.get("version_of"))
                          for m in s2.store.list_memories("northwind", status=None, limit=1000)}
            self.assertEqual(full_after, full_before, "the whole history, superseded versions included, is reproduced")
            self.assertEqual(s2.store.event_counts(), events_before)
            self.assertFalse([e for e in s2.store.list_events("northwind", kind="memory.derived", limit=1000) if e.js_seq is None])
        finally:
            await s2.close()


if __name__ == "__main__":
    unittest.main()
