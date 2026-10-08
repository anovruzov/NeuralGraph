"""Upward aggregation that reaches every layer: a rule conclusion and its unit's consolidation of the same topic both
travel up (neither shadows the other), a sparse unit under MIN_SUPPORT 3 keeps what it promotes when more evidence
arrives (monotonic), and notes that claim different values for one slot and entity are flagged as a dispute instead of
being counted as corroboration; a database the release before derived is derived again at the upgrade.  Every derived
memory here verifies, and the invariants and a rebuild hold."""
from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

from mycelic import aggregation
from mycelic.metrics import Metrics
from mycelic.models import DERIVATION_VERSION, Memory
from mycelic.service import MycelicService
from mycelic.transport import subject_for

from .helpers import ServiceHarness, invariant_violations

ORG = "northwind"
SUB = "northwind/emea/nw-gmbh"
OPS = f"{SUB}/ops"
TEAM_A, TEAM_B = f"{OPS}/team-a", f"{OPS}/team-b"
ABOVE = (OPS, SUB, "northwind/emea", "northwind")


class UpwardTests(unittest.IsolatedAsyncioTestCase):
    async def harness(self, **overrides: Any) -> ServiceHarness:
        h = await ServiceHarness(**overrides).start()
        self.addAsyncCleanup(h.close)
        return h

    def consolidation(self, h: ServiceHarness, unit: str, topic: str) -> Memory | None:
        return h.service.store.current_derived(ORG, "topic_consolidation", unit, topic)

    def parents(self, h: ServiceHarness, m: Memory) -> list[str]:
        return [e.parent_id for e in h.service.store.parents_of(m.memory_id)]

    async def assert_sound(self, h: ServiceHarness) -> None:
        """Every active derived memory verifies for the administrator, and the invariants hold."""
        self.assertEqual(invariant_violations(h.service, ORG), [])
        for m in h.service.store.list_memories(ORG, limit=10_000):
            if m.operator != "agent_observation":
                r = await h.service.verify(h.admin, m.memory_id)
                self.assertEqual((m.scope, m.operator, r["verdict"], r["reasons"]), (m.scope, m.operator, "verified", []))

    # ------------------------------------------------------------------------------------------------ shadowing
    async def test_a_conclusion_on_its_evidence_topic_reaches_every_layer_above(self) -> None:
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        await h.register("b1", team="team-b")
        # no emits_topic: the conclusion's topic is the rule's topic_prefix, the topic its evidence is written on
        await h.service.upsert_rule({"rule_id": "dock_risk", "target_layer": "team", "topic_prefix": "ops:docks",
                                     "required_slots": ["dock_closed", "backlog_high"], "min_agents": 2,
                                     "conclusion": "DOCK-RISK for {entity}: {slot:dock_closed}"})
        await h.settle()
        await h.observe("a1", "Dock 4 closed.", topic="ops:docks", slot="dock_closed", entity="d4", visibility="org")
        await h.observe("a2", "40 trucks waiting.", topic="ops:docks", slot="backlog_high", entity="d4", visibility="org")
        await h.observe("b1", "Team b: docks congested.", topic="ops:docks", visibility="org")
        await h.settle()
        conclusion = h.service.store.current_derived(ORG, "slot_composition", TEAM_A, "dock_risk:d4")
        self.assertEqual((conclusion.topic, conclusion.status), ("ops:docks", "active"))
        team = self.consolidation(h, TEAM_A, "ops:docks")
        self.assertIsNotNone(team, "team-a consolidates the topic its conclusion is on")
        dept = self.consolidation(h, OPS, "ops:docks")
        self.assertIn(conclusion.memory_id, self.parents(h, dept), "next to team-a's consolidation, not instead of it")
        self.assertIn(team.memory_id, self.parents(h, dept))
        for unit in ABOVE:
            with self.subTest(unit=unit):
                c = self.consolidation(h, unit, "ops:docks")
                self.assertEqual(c.metadata["rule_chain"], ["dock_risk"])
                self.assertIn("DOCK-RISK for d4", " ".join(c.metadata["statements"]))
                self.assertEqual(c.metadata["contributing_agents"], ["a1", "a2", "b1"])
        await self.assert_sound(h)

    async def test_a_conclusion_stays_in_the_consolidation_above_when_its_unit_talks_on_its_topic(self) -> None:
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        await h.register("b1", team="team-b")
        await h.service.upsert_rule({"rule_id": "dock_risk", "target_layer": "team",
                                     "required_slots": ["dock_closed", "backlog_high"], "min_agents": 2,
                                     "conclusion": "Dock risk for {entity}.", "emits_topic": "risk:docks", "kind": "risk"})
        await h.settle()
        await h.observe("a1", "Dock 4 closed.", topic="ops:docks", slot="dock_closed", entity="d4", visibility="org")
        await h.observe("a2", "Backlog of 40 trucks.", topic="ops:docks", slot="backlog_high", entity="d4", visibility="org")
        await h.observe("b1", "Team b sees dock risk too.", topic="risk:docks", visibility="org")
        await h.settle()
        conclusion = h.service.store.current_derived(ORG, "slot_composition", TEAM_A, "dock_risk:d4")
        self.assertIn(conclusion.memory_id, self.parents(h, self.consolidation(h, OPS, "risk:docks")))
        # team-a now also talks on the conclusion's topic, so it gets a consolidation of risk:docks of its own
        await h.observe("a1", "Risk at the docks, a1.", topic="risk:docks", visibility="org")
        await h.observe("a2", "Risk at the docks, a2.", topic="risk:docks", visibility="org")
        await h.settle()
        self.assertIsNotNone(self.consolidation(h, TEAM_A, "risk:docks"))
        self.assertEqual(h.service.store.get_memory(conclusion.memory_id).status, "active")
        for unit in ABOVE:
            with self.subTest(unit=unit):
                self.assertEqual(self.consolidation(h, unit, "risk:docks").metadata["rule_chain"], ["dock_risk"])
        self.assertIn(conclusion.memory_id, self.parents(h, self.consolidation(h, OPS, "risk:docks")))
        await self.assert_sound(h)

    async def test_a_conclusion_at_a_unit_never_hides_the_rest_of_its_subtree(self) -> None:
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        for b in ("b1", "b2"):
            await h.register(b, team="team-b")
        for c in ("c1", "c2"):
            await h.register(c, team="team-c", department="sales")
        await h.service.upsert_rule({"rule_id": "r_dept", "target_layer": "department", "required_slots": ["s1", "s2"],
                                     "min_agents": 2, "conclusion": "CONCLUSION {entity}", "emits_topic": "ops:x",
                                     "topic_prefix": "supply:"})
        await h.settle()
        for agent in ("b1", "b2", "c1", "c2"):
            await h.observe(agent, f"{agent} sees x.", topic="ops:x", visibility="org")
        await h.settle()
        before = self.consolidation(h, SUB, "ops:x")
        self.assertEqual(before.metadata["contributing_agents"], ["b1", "b2", "c1", "c2"])
        await h.observe("a1", "s1 evidence", topic="supply:z", slot="s1", entity="z")
        await h.observe("a2", "s2 evidence", topic="supply:z", slot="s2", entity="z")
        await h.settle()
        conclusion = h.service.store.current_derived(ORG, "slot_composition", OPS, "r_dept:z")
        self.assertEqual(conclusion.topic, "ops:x")
        after = self.consolidation(h, SUB, "ops:x")
        self.assertEqual(after.metadata["contributing_agents"], ["a1", "a2", "b1", "b2", "c1", "c2"])
        self.assertIn("[ops] b1 sees x.", after.text, "team-b's statements still reach the subsidiary")
        self.assertIn(conclusion.memory_id, self.parents(h, after))
        self.assertIn(self.consolidation(h, TEAM_B, "ops:x").memory_id, self.parents(h, after))
        self.assertEqual(after.support, 6)
        await self.assert_sound(h)

    # ------------------------------------------------------------------------------------------------ promotion
    async def test_a_sparse_unit_keeps_its_promotion_when_a_sibling_corroborates(self) -> None:
        h = await self.harness(min_support=3)
        for a in ("a1", "a2", "a3"):
            await h.register(a, team="team-a")
        await h.register("b1", team="team-b")              # ops has two registered teams: fewer than min_support
        await h.settle()
        for a in ("a1", "a2", "a3"):
            await h.observe(a, f"Dock 4 closed (seen by {a}).", topic="ops:docks", visibility="org", confidence=0.8)
        await h.settle()
        for unit in ABOVE:
            c = self.consolidation(h, unit, "ops:docks")
            self.assertEqual((unit, c.support), (unit, 3))
        self.assertEqual(self.consolidation(h, OPS, "ops:docks").metadata["promoted_from"],
                         self.consolidation(h, TEAM_A, "ops:docks").memory_id)
        b1 = await h.observe("b1", "Dock 4 closed, also seen from team b.", topic="ops:docks", visibility="org", confidence=0.9)
        await h.settle()
        for unit in ABOVE:
            with self.subTest(unit=unit):
                c = self.consolidation(h, unit, "ops:docks")
                self.assertIsNotNone(c, "one more corroborating note never takes a consolidation away")
                self.assertEqual((c.support, c.independent_teams), (4, 2))
        dept = self.consolidation(h, OPS, "ops:docks")
        self.assertEqual(set(self.parents(h, dept)), {self.consolidation(h, TEAM_A, "ops:docks").memory_id, b1})
        self.assertEqual(dept.metadata["promoted_from"], self.consolidation(h, TEAM_A, "ops:docks").memory_id)
        await self.assert_sound(h)
        # and taking the note back returns to the promotion of team-a's consolidation alone
        await h.service.retract(h.principal("b1"), b1, "mistaken")
        await h.settle()
        for unit in ABOVE:
            self.assertEqual(self.consolidation(h, unit, "ops:docks").support, 3)
        await self.assert_sound(h)

    async def test_raw_notes_alone_are_never_promoted(self) -> None:
        h = await self.harness(min_support=3)
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        await h.register("b1", team="team-b")
        await h.settle()
        for a in ("a1", "a2", "b1"):
            await h.observe(a, f"Dock 4 closed ({a}).", topic="ops:docks", visibility="org")
        await h.settle()
        # three agents in two teams, but no team consolidation (each team has fewer than three agents agreeing) and two
        # children of three needed: nothing above team, as before
        for unit in (TEAM_A, TEAM_B, *ABOVE):
            self.assertIsNone(self.consolidation(h, unit, "ops:docks"), unit)
        await self.assert_sound(h)

    # ------------------------------------------------------------------------------------------------ disputes
    async def test_disputed_values_are_flagged_and_never_corroborate(self) -> None:
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        for b in ("b1", "b2"):
            await h.register(b, team="team-b")
        for c in ("c1", "c2"):
            await h.register(c, team="team-c")
        await h.settle()
        fields = {"topic": "port:rotterdam", "slot": "port_status", "entity": "rtm-3", "visibility": "org"}
        await h.observe("a1", "Rotterdam terminal 3 is CLOSED by a strike.", value="closed", confidence=0.9, **fields)
        await h.observe("a2", "Rotterdam terminal 3 is OPEN; the strike was called off.", value="Open", confidence=0.9,
                        **fields)
        await h.settle()
        team_a = self.consolidation(h, TEAM_A, "port:rotterdam")
        self.assertTrue(team_a.metadata["conflict"])
        self.assertEqual((team_a.confidence, team_a.support, team_a.slot, team_a.entity), (0.9, 2, None, "rtm-3"))
        self.assertNotIn("value", team_a.metadata)
        await h.observe("b1", "Terminal 3 open.", value="open", confidence=0.6, **fields)
        await h.observe("b2", "Terminal 3 open, trucks moving.", value="OPEN ", confidence=0.6, **fields)
        await h.settle()
        team_b = self.consolidation(h, TEAM_B, "port:rotterdam")
        self.assertNotIn("conflict", team_b.metadata, "agreeing values corroborate")
        self.assertEqual((team_b.metadata["value"], team_b.slot, team_b.confidence), ("open", "port_status", 0.84))
        for unit in ABOVE:
            with self.subTest(unit=unit):
                c = self.consolidation(h, unit, "port:rotterdam")
                self.assertTrue(c.metadata["conflict"], "the dispute travels up")
                self.assertEqual((c.confidence, c.support, c.slot), (0.9, 4, None), "a dispute is no corroboration")
        await self.assert_sound(h)
        # the agents who said closed take it back: the dispute is gone, and the agreement corroborates again
        for m in h.service.store.list_memories(ORG, layers=["agent"], limit=100):
            if m.metadata.get("value") == "closed":
                await h.service.retract(h.principal(m.producer_id), m.memory_id, "the strike was called off")
        await h.settle()
        dept = self.consolidation(h, OPS, "port:rotterdam")
        self.assertNotIn("conflict", dept.metadata)
        self.assertEqual((dept.metadata["value"], dept.slot, dept.support), ("open", "port_status", 3))
        self.assertGreater(dept.confidence, 0.9)
        await self.assert_sound(h)

    async def test_a_team_private_value_never_leaves_its_team(self) -> None:
        """A team-visibility note's value is never published or stored above its team, but it is still compared: that it
        disagrees with what another team says flags the dispute above them (one bit), and that it agrees corroborates."""
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        for b in ("b1", "b2"):
            await h.register(b, team="team-b")
        await h.settle()
        fields = {"slot": "port_status", "entity": "rtm-3"}
        for a in ("a1", "a2"):
            await h.observe(a, f"Closed, says {a}.", topic="port:rotterdam", value="closed-by-strike-of-local-17",
                            **fields)                                                                  # team visibility
            await h.observe(a, f"Open, says {a}.", topic="port:antwerp", value="open-for-local-17", **fields)
        for b in ("b1", "b2"):
            await h.observe(b, f"Open, says {b}.", topic="port:rotterdam", value="open", visibility="org", **fields)
            await h.observe(b, f"Open, says {b}.", topic="port:antwerp", value="Open-for-local-17", visibility="org", **fields)
        await h.settle()
        self.assertNotIn("value", self.consolidation(h, TEAM_A, "port:rotterdam").metadata)
        for unit in ABOVE:
            with self.subTest(unit=unit):
                c = self.consolidation(h, unit, "port:rotterdam")
                self.assertNotIn("closed-by-strike-of-local-17", repr(c.metadata))
                self.assertNotIn("closed-by-strike-of-local-17", repr(h.service.public_view(c, h.principal("b1"))))
                self.assertEqual((c.metadata.get("conflict"), c.metadata.get("value"), c.slot), (True, None, None),
                                 "team-a says otherwise: 'open' is not what the unit knows")
                self.assertEqual((c.confidence, c.support), (0.96, 4), "a dispute is no corroboration")
                agreed = self.consolidation(h, unit, "port:antwerp")
                self.assertNotIn("conflict", agreed.metadata)
                self.assertEqual((agreed.metadata["value"], agreed.slot, agreed.confidence),
                                 ("open-for-local-17", "port_status", 0.99))
        await self.assert_sound(h)

    async def test_a_dispute_between_team_visibility_notes_of_two_teams_is_flagged_above_them(self) -> None:
        """Every note at the default visibility (team): team-a says closed, team-b says open.  Each team agrees with
        itself; above them the values differ, so nothing above the teams corroborates and no value is published, and a
        corroborating rule over the teams' consolidations does not raise its confidence either."""
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        for b in ("b1", "b2"):
            await h.register(b, team="team-b")
        await h.service.upsert_rule({"rule_id": "port_watch", "target_layer": "enterprise", "required_slots": ["port_status"],
                                     "sources": ["topic_consolidation"], "corroborate": True, "min_agents": 2,
                                     "min_teams": 2, "conclusion": "Port watch at {entity}."})
        await h.settle()
        fields = {"topic": "port:rotterdam", "slot": "port_status", "entity": "rtm-3", "confidence": 0.6}
        for a in ("a1", "a2"):
            await h.observe(a, f"Terminal 3 closed, says {a}.", value="closed", **fields)
        for b in ("b1", "b2"):
            await h.observe(b, f"Terminal 3 open, says {b}.", value="open", **fields)
        await h.settle()
        for team in (TEAM_A, TEAM_B):
            with self.subTest(unit=team):
                t = self.consolidation(h, team, "port:rotterdam")
                self.assertNotIn("conflict", t.metadata, "each team agrees with itself")
                self.assertNotIn("value", t.metadata, "a team-visibility value is not published")
                self.assertEqual((t.slot, t.confidence, t.support), ("port_status", 0.84, 2))
        for unit in ABOVE:
            with self.subTest(unit=unit):
                c = self.consolidation(h, unit, "port:rotterdam")
                self.assertTrue(c.metadata.get("conflict"), "the teams dispute each other")
                self.assertEqual((c.confidence, c.support, c.independent_teams, c.slot), (0.84, 4, 2, None))
                self.assertNotIn("value", c.metadata)
                for reader in ("a1", "b1"):
                    view = repr(h.service.public_view(c, h.principal(reader)))
                    self.assertNotIn("'closed'", view)
                    self.assertNotIn("'open'", view)
        conclusion = h.service.store.current_derived(ORG, "slot_composition", "northwind", "port_watch:rtm-3")
        self.assertEqual((conclusion.confidence, conclusion.metadata.get("conflict")), (0.84, True))
        await self.assert_sound(h)
        # team-a takes it back: the teams agree, and the agreement corroborates up to the enterprise
        for a in ("a1", "a2"):
            for m in h.service.store.list_memories(ORG, layers=["agent"], limit=100):
                if m.producer_id == a and m.status == "active":
                    await h.service.retract(h.principal(a), m.memory_id, "reopened")
            await h.observe(a, f"Terminal 3 open again, says {a}.", value="open", **fields)
        await h.settle()
        for unit in ABOVE:
            with self.subTest(unit=unit, after="agreement"):
                c = self.consolidation(h, unit, "port:rotterdam")
                self.assertNotIn("conflict", c.metadata)
                self.assertNotIn("value", c.metadata, "every value is team-visibility: none is published")
                self.assertEqual((c.confidence, c.slot), (0.9744, "port_status"))
        conclusion = h.service.store.current_derived(ORG, "slot_composition", "northwind", "port_watch:rtm-3")
        self.assertEqual((conclusion.confidence, conclusion.metadata.get("conflict")), (0.9744, None))
        await self.assert_sound(h)

    async def test_a_minority_value_is_not_published_over_a_team_visibility_majority(self) -> None:
        """Two agents of team-a say closed (team visibility), one of team-b says open (org): the department does not say
        'open', does not count the dissenters as support for it, and takes the strongest contribution's confidence."""
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        await h.register("b1", team="team-b")
        await h.settle()
        fields = {"topic": "port:rotterdam", "slot": "port_status", "entity": "rtm-3", "confidence": 0.6}
        for a in ("a1", "a2"):
            await h.observe(a, f"Closed, says {a}.", value="closed", **fields)
        await h.observe("b1", "Open.", value="open", visibility="org", **fields)
        await h.settle()
        dept = self.consolidation(h, OPS, "port:rotterdam")
        self.assertEqual((dept.metadata.get("conflict"), dept.metadata.get("value"), dept.slot, dept.confidence),
                         (True, None, None, 0.84))
        await self.assert_sound(h)

    async def test_a_corroborating_rule_does_not_raise_confidence_over_a_dispute(self) -> None:
        h = await self.harness()
        for a, team in (("a1", "team-a"), ("b1", "team-b"), ("c1", "team-c")):
            await h.register(a, team=team)
        await h.service.upsert_rule({"rule_id": "port_risk", "target_layer": "department", "required_slots": ["port_status"],
                                     "min_agents": 2, "corroborate": True, "conclusion": "Port risk at {entity}."})
        await h.settle()
        await h.observe("a1", "Terminal 3 closed.", slot="port_status", entity="rtm-3", value="closed", confidence=0.8)
        await h.observe("b1", "Terminal 3 closed too.", slot="port_status", entity="rtm-3", value="closed", confidence=0.8)
        await h.settle()
        agreed = h.service.store.current_derived(ORG, "slot_composition", OPS, "port_risk:rtm-3")
        self.assertEqual(agreed.confidence, 0.96)
        self.assertNotIn("conflict", agreed.metadata)
        await h.observe("c1", "Terminal 3 is open.", slot="port_status", entity="rtm-3", value="open", confidence=0.8)
        await h.settle()
        disputed = h.service.store.current_derived(ORG, "slot_composition", OPS, "port_risk:rtm-3")
        self.assertTrue(disputed.metadata["conflict"])
        self.assertEqual(disputed.confidence, 0.8)
        await self.assert_sound(h)

    async def test_value_is_validated_and_stored_as_metadata(self) -> None:
        h = await self.harness()
        await h.register("a1", team="team-a")
        p = h.principal("a1")
        m, _ = await h.service.ingest_memory(p, {"text": "x", "slot": "s", "entity": "e", "value": "  CLOSED  "})
        self.assertEqual(m.metadata["value"], "closed")
        m, _ = await h.service.ingest_memory(p, {"text": "y", "value": "Open", "metadata": {"value": "OPEN"}})
        self.assertEqual(m.metadata["value"], "open", "they agree in canonical form")
        from mycelic.service import ValidationError
        for body, message in (({"text": "z", "value": "open", "metadata": {"value": "closed"}}, "'value' and 'metadata.value' differ"),
                              ({"text": "z", "value": "open", "metadata": {"value": 42}}, "'value' and 'metadata.value' differ"),
                              ({"text": "z", "value": 3}, "'value'"), ({"text": "z", "value": "v" * 201}, "'value'")):
            with self.subTest(body=body):
                with self.assertRaises(ValidationError) as ctx:
                    await h.service.ingest_memory(p, body)
                self.assertIn(message, str(ctx.exception))

    async def test_an_earlier_clients_metadata_is_kept_as_sent_and_never_refused(self) -> None:
        """``metadata`` was free-form before ``value`` existed: whatever ``metadata.value`` (or ``metadata.conflict``) a
        client sends is stored as sent, never refused, also on a resend, and only a string of at most 200 characters
        claims anything.  A raw note's metadata stays its producer's and the administrators', as before."""
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        await h.settle()
        p = h.principal("a1")
        sent = ({"value": 42}, {"value": {"amount": 3}}, {"value": True}, {"value": "x" * 300}, {"value": "Mixed Case"},
                {"value": ""}, {"value": None}, {"conflict": True, "source": "erp"})
        for i, meta in enumerate(sent):
            with self.subTest(metadata=meta):
                body = {"text": f"legacy {i}", "idempotency_key": f"legacy-{i}", "metadata": meta}
                m, created = await h.service.ingest_memory(p, body)
                self.assertTrue(created)
                self.assertEqual(m.metadata, meta)
                again, created = await h.service.ingest_memory(p, body)
                self.assertEqual((again.memory_id, created), (m.memory_id, False), "a resend is never refused")
                self.assertEqual(h.service.public_view(m, h.principal("a2"))["metadata"], {}, "as in earlier releases")
                self.assertEqual(h.service.public_view(m, p)["metadata"], meta)
        # values that claim nothing never dispute each other, and a note's own 'conflict' flags nothing
        fields = {"topic": "ops:legacy", "slot": "s", "entity": "e", "visibility": "org", "confidence": 0.8}
        await h.observe("a1", "First.", metadata={"value": 42, "conflict": True}, **fields)
        await h.observe("a2", "Second.", metadata={"value": "y" * 300}, **fields)
        await h.settle()
        team = self.consolidation(h, TEAM_A, "ops:legacy")
        self.assertNotIn("conflict", team.metadata)
        self.assertNotIn("value", team.metadata)
        self.assertEqual((team.slot, team.confidence), ("s", 0.96))
        await self.assert_sound(h)

    async def test_a_value_an_earlier_release_stored_agrees_in_canonical_form(self) -> None:
        """A note an earlier release stored with ``metadata.value`` 'Open' (as sent: that release did not normalise it)
        and a new note with ``value`` 'open' say the same thing: they corroborate, they are no dispute."""
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        await h.settle()
        s = h.service
        fields = {"topic": "port:rtm", "slot": "port_status", "entity": "rtm-3", "visibility": "org", "confidence": 0.8}
        # the memory.observed event that release logged (a rebuild or replay applies it exactly like this)
        payload = {"memory_id": "mem_from_0_2_0", "org_id": ORG, "layer": "agent", "scope": f"{TEAM_A}/a1",
                   "text": "Terminal 3 is open.", "producer_id": "a1", "operator": "agent_observation", "kind": "observation",
                   "metadata": {"value": "Open"}, **fields}
        event = {"event_id": "evt_from_0_2_0", "kind": "memory.observed", "org_id": ORG, "agent_id": "a1",
                 "created_at": "2026-10-01T08:00:00+00:00", "payload": payload, "schema": 1}
        data = json.dumps(event).encode()
        await s.transport.publish(subject_for(ORG, "memory.observed"), data, event["event_id"], headers=s._sign(data))
        await h.settle()
        self.assertEqual(s.store.get_memory("mem_from_0_2_0").metadata, {"value": "Open"}, "stored as that release sent it")
        await h.observe("a2", "Terminal 3 open, trucks moving.", value="open", **fields)
        await h.settle()
        team = self.consolidation(h, TEAM_A, "port:rtm")
        self.assertNotIn("conflict", team.metadata)
        self.assertEqual((team.metadata["value"], team.slot, team.confidence, team.support), ("open", "port_status", 0.96, 2))
        await self.assert_sound(h)

    # ------------------------------------------------------------------------------------------------ upgrade
    async def test_what_the_release_before_disputes_derived_is_derived_again_at_the_upgrade(self) -> None:
        """A schema-5 database (the release before disputes) holds consolidations and a corroborated conclusion over notes
        whose string ``metadata.value`` that release never read.  Derived ids embed DERIVATION_VERSION, so the first start
        derives every one of them again instead of keeping rows the builders now derive differently: they claim the
        agreed value or carry the dispute, and every one verifies (none fails on a recomputation that differs)."""
        h = await self.harness()
        for a in ("a1", "a2"):
            await h.register(a, team="team-a")
        await h.service.upsert_rule({"rule_id": "port_risk", "target_layer": "team", "required_slots": ["port_status"],
                                     "min_agents": 2, "corroborate": True, "conclusion": "Port risk at {entity}."})
        await h.settle()
        fields = {"slot": "port_status", "visibility": "org", "confidence": 0.8}
        # the builders of that release: derivation version 2, and no value claimed, so no dispute and no value
        with mock.patch.object(aggregation, "DERIVATION_VERSION", 2), \
                mock.patch.object(aggregation, "consolidation_claims", lambda parents: (False, None)), \
                mock.patch.object(aggregation, "_claimed_value", lambda m: None):
            for agent, value in (("a1", "Open"), ("a2", "closed")):
                await h.observe(agent, f"Terminal 3 is {value}.", topic="port:rtm-3", entity="rtm-3", metadata={"value": value},
                                **fields)
            for agent, value in (("a1", "Open"), ("a2", "open")):
                await h.observe(agent, f"Terminal 4 is {value}.", topic="port:rtm-4", entity="rtm-4", metadata={"value": value},
                                **fields)
            await h.settle()
        s = h.service
        old = [m for m in s.store.list_memories(ORG, status=None, limit=1000) if m.operator != "agent_observation"]
        self.assertEqual({(m.status, m.metadata["derivation"]["v"]) for m in old}, {("active", 2)})
        disputed = self.consolidation(h, TEAM_A, "port:rtm-3")
        self.assertEqual((disputed.confidence, disputed.slot, disputed.metadata.get("conflict")), (0.96, "port_status", None))
        self.assertNotIn("value", self.consolidation(h, TEAM_A, "port:rtm-4").metadata)
        # the database as that release left it
        s.store._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('derivation_version', '2')")
        s.store._conn.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
        await s.close()

        s = h.service = MycelicService(h.settings, transport=s.transport, metrics=Metrics())
        self.assertEqual((s.store.get_meta("schema_version"), s.store.get_meta("reaggregate_pending")), ("6", "1"))
        await s.start()
        await h.settle(30)
        self.assertEqual(s._reaggregation["state"], "done", s._reaggregation)
        self.assertEqual({s.store.get_memory(m.memory_id).status for m in old}, {"superseded"})
        for unit in (TEAM_A, *ABOVE):
            with self.subTest(unit=unit):
                c = self.consolidation(h, unit, "port:rtm-3")
                self.assertEqual((c.confidence, c.slot, c.metadata.get("conflict")), (0.8, None, True))
                c = self.consolidation(h, unit, "port:rtm-4")
                self.assertEqual((c.confidence, c.slot, c.metadata.get("value")), (0.96, "port_status", "open"))
        conclusion = s.store.current_derived(ORG, "slot_composition", TEAM_A, "port_risk:rtm-3")
        self.assertEqual((conclusion.confidence, conclusion.metadata.get("conflict")), (0.8, True))
        self.assertEqual({m.metadata["derivation"]["v"] for m in s.store.list_memories(ORG, limit=1000)
                          if m.operator != "agent_observation"}, {DERIVATION_VERSION})
        await self.assert_sound(h)

if __name__ == "__main__":
    unittest.main()
