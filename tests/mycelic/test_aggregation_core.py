"""The aggregation engine as a function of the event log: derived events from the stream are never trusted, rules and
the registry are read as the log has applied them, a rebuild under consumer lag reproduces the full history, an
inconsistency or a bound is reported instead of raised or hidden, hot topics and wide units keep aggregating their
newest evidence, '*' conclusions follow their evidence, and every redelivery is a no-op."""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import random
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from mycelic import aggregation
from mycelic.auth import generate_api_key
from mycelic.metrics import Metrics
from mycelic.models import Agent, Memory, content_hash, derived_memory_id, new_id, now_iso
from mycelic.service import MycelicService
from mycelic.store import MycelicStore
from mycelic.transport import InProcessTransport, subject_for

from .helpers import DEMO_RULE, HeldTransport, ServiceHarness, full_reaggregation_pass, settings

ORG = "northwind"
OPS = "northwind/emea/nw-gmbh/ops"
TEAM = f"{OPS}/logistics"
TRANSPORT = "supply:sd-9/transport"


def counter(metric: Any, *labels: str) -> float:
    return metric.labels(*labels)._value.get()


def history(store: MycelicStore, org: str = ORG) -> dict[str, tuple]:
    return {m.memory_id: (m.layer, m.status, m.support, m.metadata.get("version_of"))
            for m in store.list_memories(org, status=None, limit=100_000)}


def applied_rules(store: MycelicStore) -> list[tuple]:
    return [tuple(r) for r in store._conn.execute("SELECT rule_id, applied_seq, snapshot FROM applied_rules ORDER BY rule_id")]


def audits(store: MycelicStore, action: str) -> list[dict[str, Any]]:
    return [a for a in store.recent_audit(10_000) if a["action"] == action]


def wire(kind: str, payload: Any, *, org_id: str = ORG, event_id: str | None = None) -> dict[str, Any]:
    return {"event_id": event_id or new_id("evt"), "kind": kind, "org_id": org_id, "agent_id": None,
            "created_at": now_iso(), "payload": payload, "schema": 1}


async def publish(transport: InProcessTransport, event: dict[str, Any]) -> int:
    return await transport.publish(subject_for(event["org_id"], event["kind"]), json.dumps(event).encode(), event["event_id"])


def raw_note(i: int, agent: str, team: str, slot: str, confidence: float, entity: str | None, value: str | None = None) -> Memory:
    """An applied org-visible agent note, built directly (no service), for the pure builders."""
    return Memory(memory_id=f"mem_{i:06d}", org_id=ORG, layer="agent", scope=f"{OPS}/{team}/{agent}", text=f"note {i}",
                  topic="t", slot=slot, entity=entity, kind="observation", confidence=confidence, support=1,
                  independent_teams=1, producer_id=agent, operator="agent_observation", rule_id=None, event_id=None,
                  visibility="org", created_at=now_iso(), applied_at=None, source_event_ids=[],
                  metadata={"value": value} if value is not None else {})


def note_id(agent_id: str, key: str) -> str:
    """The id ``ingest_memory`` gives a note sent with an idempotency key."""
    return f"mem_{content_hash(agent_id, key)[:22]}"


def broken_chains(store: MycelicStore, org: str = ORG) -> list[str]:
    """Active memories that rest on a memory that is not active, directly or anywhere below."""
    out = []
    for m in store.list_memories(org, status="active", limit=100_000):
        stack, seen = [m.memory_id], set()
        while stack:
            for e in store.parents_of(stack.pop()):
                if e.parent_id in seen:
                    continue
                seen.add(e.parent_id)
                parent = store.get_memory(e.parent_id)
                if parent is None or parent.status != "active":
                    out.append(f"{m.memory_id} rests on {e.parent_id} ({parent.status if parent else 'missing'})")
                stack.append(e.parent_id)
    return out


class Capture(logging.Handler):
    """Collects the messages one logger emits; unlike ``assertLogs`` it does not fail when there are none, so the
    subtests after it still run and report."""

    def __init__(self, name: str, level: int) -> None:
        super().__init__(level)
        self.lines: list[str] = []
        self.logger = logging.getLogger(name)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def __enter__(self) -> "Capture":
        self.logger.addHandler(self)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.logger.removeHandler(self)


async def drain_outbox(service: MycelicService, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while service.store.stats()["outbox_pending"]:
        assert asyncio.get_running_loop().time() < deadline, "outbox did not drain"
        await asyncio.sleep(0.02)


async def step(service: MycelicService, transport: InProcessTransport) -> dict[str, Any]:
    """Deliver and apply exactly the next event of the log, whatever ``hold`` says."""
    [d] = await InProcessTransport.fetch(transport, 1, 1.0)
    await service._handle_delivery(d)
    return json.loads(d.data)


class AggregationCoreTests(unittest.IsolatedAsyncioTestCase):
    async def harness(self, *, agents: bool = True, **kw: Any) -> ServiceHarness:
        h = await ServiceHarness(**kw).start()
        self.addAsyncCleanup(h.close)
        if agents:
            for a in ("log-1", "log-2", "log-3", "log-4"):
                await h.register(a, team="logistics")
            await h.register("proc-1", team="procurement")
            await h.register("sales-1", team="field-sales", department="commercial")
        return h

    async def team_notes(self, h: ServiceHarness, *agents: str, topic: str = TRANSPORT) -> list[str]:
        ids = []
        for i, a in enumerate(agents):
            ids.append(await h.observe(a, f"note {i} by {a} about the SD-9 delay", topic=topic, idempotency_key=f"{a}-{i}",
                                       slot="transport_disruption", entity="sd-9", confidence=0.6 + i / 100))
        await h.settle()
        return ids

    # ------------------------------------------------------------------ derived events are informational
    async def test_injected_derived_event_is_ignored_and_counted(self) -> None:
        h = await self.harness()
        s = h.service
        exposed = s.metrics.render()[0].decode()
        for line in ('mycelic_events_ignored_total{reason="derived_not_reproduced"} 0.0',
                     'mycelic_events_ignored_total{reason="retraction_target"} 0.0',
                     'mycelic_events_ignored_total{reason="unknown_kind"} 0.0',
                     'mycelic_aggregation_inconsistency_total{kind="id_collision"} 0.0',
                     'mycelic_aggregation_inconsistency_total{kind="reactivation_mismatch"} 0.0',
                     'mycelic_aggregation_truncated_total{what="candidates"} 0.0',
                     'mycelic_aggregation_truncated_total{what="dependents"} 0.0',
                     'mycelic_aggregation_truncated_total{what="cascade"} 0.0'):
            self.assertIn(line, exposed, "every label is exported from the start")
        n1, n2 = await self.team_notes(h, "log-1", "log-2")
        [team] = s.store.list_memories(ORG, layers=["team"])
        derived_before = counter(s.metrics.derived, "team", "topic_consolidation")
        forged = {"memory_id": "mem_dforged0000000000000", "org_id": ORG, "layer": "team", "scope": TEAM,
                  "text": "Forged: the board approved a second source.", "topic": TRANSPORT, "slot": None, "entity": "sd-9",
                  "kind": "fact", "confidence": 0.99, "support": 9, "independent_teams": 9, "producer_id": "mycelic",
                  "operator": "topic_consolidation", "status": "active",
                  "parents": [{"parent_id": n1, "contributed_by": "log-1", "parent_layer": "agent"}],
                  "supersedes": team.memory_id}
        event = wire("memory.derived", forged)
        self.assertEqual(await s.apply_event(event, seq=None), "ignored")
        self.assertIsNone(s.store.get_memory("mem_dforged0000000000000"))
        self.assertEqual(s.store.parents_of("mem_dforged0000000000000"), [])
        self.assertEqual(s.store.get_memory(team.memory_id).status, "active", "the payload's supersession is not honoured")
        self.assertEqual(counter(s.metrics.events_ignored, "derived_not_reproduced"), 1)
        self.assertEqual(counter(s.metrics.derived, "team", "topic_consolidation"), derived_before)
        rows = [a for a in audits(s.store, "event.ignored") if a["target"] == event["event_id"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["detail"], {"reason": "derived_not_reproduced", "memory_id": "mem_dforged0000000000000", "org_id": ORG})
        self.assertEqual(s.store.recent_audit(10_000, org_id=ORG)[0]["target"], event["event_id"])
        self.assertEqual(await s.apply_event(event, seq=None), "duplicate")
        self.assertEqual(counter(s.metrics.events_ignored, "derived_not_reproduced"), 1)
        self.assertEqual(len(audits(s.store, "event.ignored")), 1)
        # an existing id with other content: the stored memory is what this node derived, whatever the stream says
        tampered = wire("memory.derived", {**team.to_dict(), "text": "Tampered text", "confidence": 0.01})
        self.assertEqual(await s.apply_event(tampered), "duplicate")
        self.assertEqual(s.store.get_memory(team.memory_id).text, team.text)
        self.assertEqual(s.store.get_memory(team.memory_id).confidence, team.confidence)
        # malformed payloads are ignored, never poison
        for payload in ({}, {"memory_id": 7}, {"memory_id": None, "parents": "x"}, "not an object", [1, 2], None):
            with self.subTest(payload=payload):
                self.assertEqual(await s.apply_event(wire("memory.derived", payload)), "ignored")
        # the same through the stream: consumed, counted, nothing planted
        await publish(s.transport, wire("memory.derived", {**forged, "memory_id": "mem_dforged0000000000001"}))
        await h.settle()
        self.assertIsNone(s.store.get_memory("mem_dforged0000000000001"))
        self.assertEqual(counter(s.metrics.events_applied, "memory.derived", "ignored"), 1)
        self.assertEqual(counter(s.metrics.events_ignored, "derived_not_reproduced"), 8)
        self.assertEqual([m.memory_id for m in s.store.list_memories(ORG, layers=["team"])], [team.memory_id])

    async def test_derived_event_ignores_are_summarised_once_per_replay(self) -> None:
        h = await self.harness()
        s1, transport = h.service, h.service.transport
        await self.team_notes(h, "log-1", "log-2")
        forged = [wire("memory.derived", {"memory_id": f"mem_dold{i:019d}", "org_id": ORG, "layer": "team", "scope": TEAM,
                                          "text": "old", "topic": "Supply:SD-9/Transport"}) for i in range(5)]
        seqs = [await publish(transport, ev) for ev in forged[:2]]
        await self.team_notes(h, "log-3", "proc-1")
        for ev in forged[2:]:
            await publish(transport, ev)
        await h.settle()
        self.assertEqual(len([a for a in audits(s1.store, "event.ignored") if a["target"]]), 5, "outside a replay: one row each")
        await s1.stop()
        # rebuild into a fresh database, crash after the second ignored event, resume
        cfg = settings(h.tmp.name, db_path=str(Path(h.tmp.name) / "rebuild.db"))
        s2 = MycelicService(cfg, transport=transport, metrics=Metrics())
        await s2.start(background=False)
        self.assertIsNotNone(s2._replay_target)
        seq = 0
        while seq < seqs[1]:
            [d] = await transport.fetch(1, 1.0)
            await s2._handle_delivery(d)
            seq = d.seq
        self.assertEqual(s2.store.get_meta("replay_ignored_derived"), "2", "the count is durable mid-replay")
        self.assertEqual(audits(s2.store, "event.ignored"), [])
        await s2.store.close()                       # crash: no stop, the replay is unfinished
        s3 = MycelicService(cfg, transport=transport, metrics=Metrics())
        await s3.start()
        try:
            self.assertTrue(await s3.wait_idle(15))
            rows = audits(s3.store, "event.ignored")
            self.assertEqual(len(rows), 1, rows)
            self.assertIsNone(rows[0]["target"])
            self.assertEqual(rows[0]["detail"], {"reason": "derived_not_reproduced", "count": 5, "replay": True})
            self.assertIsNone(s3.store.get_meta("replay_ignored_derived"))
            self.assertEqual(history(s3.store), history(s1.store))
        finally:
            await s3.close()

    # ------------------------------------------------------------------ no raise in apply
    async def test_apply_never_raises_on_inconsistency(self) -> None:
        h = await self.harness()
        s = h.service
        await self.team_notes(h, "log-1", "log-2")
        [first] = s.store.list_memories(ORG, layers=["team"])
        [third] = await self.team_notes(h, "log-3")
        self.assertEqual(s.store.get_memory(first.memory_id).status, "superseded")
        original = aggregation.render_consolidation
        aggregation.render_consolidation = lambda *a, **k: original(*a, **k) + " (rendered differently)"
        try:
            ev = await s.retract(h.principal("log-3"), third, "false alarm")
            await h.settle()
        finally:
            aggregation.render_consolidation = original
        self.assertEqual(s.store.get_event(ev.event_id).status, "applied")
        back = s.store.get_memory(first.memory_id)
        self.assertEqual(back.status, "active", "the earlier coalition is reactivated all the same")
        self.assertEqual(back.text, first.text, "keeping the text it was stored with")
        self.assertEqual(counter(s.metrics.aggregation_inconsistency, "reactivation_mismatch"), 1)
        self.assertEqual(counter(s.metrics.aggregation_inconsistency, "id_collision"), 0)
        [row] = audits(s.store, "aggregation.inconsistency")
        self.assertEqual(row["target"], first.memory_id)
        detail = row["detail"]
        self.assertEqual((detail["kind"], detail["org_id"], detail["operator"], detail["scope"]),
                         ("reactivation_mismatch", ORG, "topic_consolidation", TEAM))
        self.assertEqual(detail["stored_text_sha"], content_hash(first.text))
        self.assertNotEqual(detail["recomputed_text_sha"], detail["stored_text_sha"])
        self.assertNotIn(first.text, json.dumps(detail), "texts are reported as hashes only")

    async def test_id_collision_is_reported_and_keeps_the_stored_row(self) -> None:
        h = await self.harness()
        s = h.service
        n1, n2 = await self.team_notes(h, "log-1", "log-2")
        [team] = s.store.list_memories(ORG, layers=["team"])
        n3 = note_id("log-3", "log-3-0")
        taken = derived_memory_id(operator="topic_consolidation", scope=TEAM, key=aggregation.consolidation_id_key(TRANSPORT, 2), parent_ids=[n1, n2, n3])
        squatter = Memory(memory_id=taken, org_id=ORG, layer="agent", scope=f"{TEAM}/log-4", text="unrelated row", topic=None,
                          slot=None, entity=None, kind="observation", confidence=0.5, support=1, independent_teams=1,
                          producer_id="log-4", operator="agent_observation", rule_id=None, event_id=None, created_at=now_iso())
        async with s.store.transaction() as tx:
            tx.insert_memory(squatter)
        before = len(s.store.list_memories(ORG, status=None, limit=1000))
        ev_ids = {e.event_id for e in s.store.list_events(ORG, kind="memory.observed", limit=100)}
        await self.team_notes(h, "log-3")              # the coalition with the third note would take the squatter's id
        self.assertEqual(counter(s.metrics.aggregation_inconsistency, "id_collision"), 1)
        self.assertEqual([a["detail"]["kind"] for a in audits(s.store, "aggregation.inconsistency")], ["id_collision"])
        kept = s.store.get_memory(taken)
        self.assertEqual((kept.text, kept.operator, kept.layer), ("unrelated row", "agent_observation", "agent"))
        self.assertEqual(s.store.parents_of(taken), [], "no lineage is attached to a row the derivation did not produce")
        self.assertEqual(s.store.get_memory(team.memory_id).status, "active", "the current consolidation stays current")
        self.assertEqual(len(s.store.list_memories(ORG, status=None, limit=1000)), before + 1)
        new = [e for e in s.store.list_events(ORG, kind="memory.observed", limit=100) if e.event_id not in ev_ids]
        self.assertEqual({e.status for e in new}, {"applied"}, "every event applied: nothing raised")

    # ------------------------------------------------------------------ cascades
    async def test_dependents_traversal_is_active_only_and_counts_truncation(self) -> None:
        h = await self.harness(agents=False)
        s = h.service
        for a in ("log-1", "log-2", "log-3", "log-4"):          # one team: every unit above it promotes its memory
            await h.register(a, team="logistics")
        ids = await self.team_notes(h, "log-1", "log-2")
        for agent in ("log-3", "log-4"):
            ids += await self.team_notes(h, agent)
        historical = s.store.dependents_of(ids[0])
        active = s.store.dependents_of(ids[0], active_only=True)
        self.assertEqual([s.store.get_memory(m).layer for m in active], ["team", "department", "subsidiary", "region", "enterprise"],
                         "nearest first")
        self.assertEqual(len(historical), 15, "three versions of each")
        s.aggregator.max_dependents = 8                     # below the history, above what is active
        await s.retract(h.principal("log-1"), ids[0], "withdrawn")
        await h.settle()
        self.assertEqual(broken_chains(s.store), [])
        self.assertEqual(counter(s.metrics.aggregation_truncated, "dependents"), 0)
        self.assertEqual(len(s.store.list_memories(ORG, layers=["enterprise"])), 1, "re-derived on what remains")
        s.aggregator.max_dependents = 1
        self.assertGreaterEqual(len(s.store.dependents_of(ids[1], active_only=True)), 2)
        with self.assertLogs("mycelic.aggregation", "ERROR") as logs:
            ev = await s.retract(h.principal("log-2"), ids[1], "withdrawn")
            await h.settle()
        self.assertGreaterEqual(counter(s.metrics.aggregation_truncated, "dependents"), 1)
        self.assertTrue(any("cut at 1" in line for line in logs.output))
        self.assertEqual(s.store.get_event(ev.event_id).status, "applied")

    # ------------------------------------------------------------------ candidates
    async def test_candidate_cap_does_not_freeze_team_or_upper_layers(self) -> None:
        h = await self.harness(agents=False)
        s = h.service
        s.aggregator.max_candidates = 10
        for a in ("a-1", "a-2"):
            await h.register(a, team="logistics")
        with Capture("mycelic.aggregation", logging.WARNING) as logs:
            newest = None
            for i in range(25):
                newest = await h.observe(f"a-{i % 2 + 1}", f"hot note {i}", topic="ops:hot")
            await h.settle(timeout=30)
        [team] = s.store.list_memories(ORG, layers=["team"])
        with self.subTest("team"):
            self.assertIn(newest, team.metadata["roots"], "the team consolidation follows the newest notes")
            self.assertEqual(len(team.metadata["roots"]), 10)
        with self.subTest("truncation is counted every time and logged once per (unit, key)"):
            self.assertGreater(counter(s.metrics.aggregation_truncated, "candidates"), 1)
            self.assertEqual(len([line for line in logs.lines if f"ops:hot at {TEAM} reached the cap" in line]), 1, logs.lines)
        # a wide department: three consolidated teams and a solo team whose notes alone must reach the department
        plant = "acme/emea/acme-gmbh/plant"
        teams = {"t1": ["t1-a", "t1-b"], "t2": ["t2-a", "t2-b"], "t3": ["t3-a", "t3-b"], "solo": ["solo-a"]}
        for team_name, members in teams.items():
            for a in members:
                await h.register(a, team=team_name, department="plant", subsidiary="acme-gmbh", region="emea", enterprise="acme")
        last: dict[str, str] = {}
        # the solo team writes first: the newest leaves of the department are all the other teams' notes, which their
        # own consolidations already stand for
        for i in range(25):
            last["solo"] = await h.observe("solo-a", f"solo hot note {i}", topic="ops:hot")
        for i in range(25):
            for team_name in ("t1", "t2", "t3"):
                last[team_name] = await h.observe(teams[team_name][i % 2], f"{team_name} hot note {i}", topic="ops:hot")
        await h.settle(timeout=60)
        dept = [m for m in s.store.list_memories("acme", layers=["department"]) if m.topic == "ops:hot"]
        ent = [m for m in s.store.list_memories("acme", layers=["enterprise"]) if m.topic == "ops:hot"]
        with self.subTest("department"):
            self.assertEqual([m.scope for m in dept], [plant])
            self.assertEqual(set(dept[0].metadata["children"]), {f"{plant}/{t}" for t in teams})
            self.assertIn(last["solo"], dept[0].metadata["roots"], "the solo team's newest note reaches its department")
            self.assertLessEqual(set(last.values()), set(dept[0].metadata["roots"]), "every team's newest note does")
        with self.subTest("enterprise"):
            self.assertEqual(len(ent), 1)
            self.assertIn(last["t3"], ent[0].metadata["roots"], "the newest note overall reaches the enterprise")
            self.assertIn(last["solo"], ent[0].metadata["roots"])
            self.assertEqual(ent[0].metadata["roots"], dept[0].metadata["roots"])
        # a grandchild's consolidation inside a child without one is found even when the newest leaves are all
        # elsewhere: d1 (teams t1, t1b) has only t1's consolidation; d2's solo agent fills the leaf cap
        sub = "deep-co/eu/sub"
        for a, team_name, dept_name in (("d1-a", "t1", "d1"), ("d1-b", "t1", "d1"), ("d1-c", "t1b", "d1"), ("d2-a", "solo", "d2")):
            await h.register(a, team=team_name, department=dept_name, subsidiary="sub", region="eu", enterprise="deep-co")
        for a in ("d1-a", "d1-b"):
            await h.observe(a, f"deep note by {a}", topic="ops:deep")
        await h.settle()
        [t1] = [m for m in s.store.list_memories("deep-co", layers=["team"]) if m.topic == "ops:deep"]
        for i in range(12):
            await h.observe("d2-a", f"solo deep note {i}", topic="ops:deep")
        await h.settle(timeout=30)
        with self.subTest("grandchild"):
            [top] = [m for m in s.store.list_memories("deep-co", layers=["subsidiary"]) if m.topic == "ops:deep"]
            self.assertEqual(top.metadata["children"], [f"{sub}/d1", f"{sub}/d2"])
            self.assertIn(t1.memory_id, {e.parent_id for e in s.store.parents_of(top.memory_id)})

    async def test_wildcard_conclusion_is_refreshed_by_entity_evidence(self) -> None:
        h = await self.harness()
        s = h.service
        await s.upsert_rule({"rule_id": "w", "target_layer": "team", "required_slots": ["a", "b"], "min_agents": 2,
                             "conclusion": "W {entity}: {slot:a} / {slot:b}"})
        n2 = await h.observe("log-2", "b about x", slot="b", entity="x", confidence=0.6)
        n1 = await h.observe("log-1", "a about nothing in particular", slot="a", confidence=0.6)
        await h.settle()
        [first] = [m for m in s.store.list_memories(ORG, layers=["team"]) if m.rule_id == "w"]
        self.assertEqual((first.entity, first.metadata["slots"]), (None, {"a": n1, "b": n2}))
        n3 = await h.observe("log-3", "stronger b about x", slot="b", entity="x", confidence=0.9)
        await h.settle()
        active = [m for m in s.store.list_memories(ORG, layers=["team"]) if m.rule_id == "w"]
        self.assertEqual(len(active), 1)
        star = active[0]
        self.assertEqual(star.metadata["slots"], {"a": n1, "b": n3}, "the '*' conclusion follows the strongest evidence")
        self.assertEqual(star.metadata["version_of"], first.memory_id)
        self.assertEqual(s.store.get_memory(first.memory_id).status, "superseded")
        plan = s.aggregator.plan_rule(s.store.get_applied_rule("w"), ORG, TEAM, None)
        self.assertEqual(plan.memory.memory_id, star.memory_id, "settled: a fresh evaluation reproduces the stored '*'")
        # with no entity-less evidence selected there is no '*' conclusion, only per-entity ones
        await s.retract(h.principal("log-1"), n1, "wrong")
        await h.observe("log-4", "a about x", slot="a", entity="x", confidence=0.7)
        await h.settle()
        active = {m.entity for m in s.store.list_memories(ORG, layers=["team"]) if m.rule_id == "w"}
        self.assertEqual(active, {"x"})
        self.assertIsNone(s.aggregator.plan_rule(s.store.get_applied_rule("w"), ORG, TEAM, None).memory)

    def test_the_selection_is_the_best_one_that_meets_the_thresholds(self) -> None:
        """Against every selection of one memory per slot, on random small candidate sets: without ``corroborate`` the rule
        rests on the selection that meets min_agents and min_teams whose weakest memory is strongest, then the earliest in
        each slot's ranking (None when none does); a '*' evaluation only on selections about one entity at most.  With
        ``corroborate``, a '*' evaluation takes the pool "evidence that names no entity, and evidence about E" (one per E)
        whose evidence meets the thresholds and whose strongest selection is best, then the first E, whichever memories
        that selection names."""
        rnd = random.Random(20261009)
        note = raw_note

        def best(rule: aggregation.Rule, candidates: list[Memory], *, one_entity: bool) -> tuple | None:
            ranked = [sorted((m for m in candidates if m.slot == s), key=aggregation._rank) for s in rule.required_slots]
            entities = sorted({m.entity for m in candidates if m.entity is not None}) or [None]
            found: tuple | None = None
            for selection in itertools.product(*ranked):
                named = {m.entity for m in selection if m.entity is not None}
                if one_entity and len(named) > 1:
                    continue
                pools: list[tuple[str, list[Memory]]] = [("", [])]
                if rule.corroborate:
                    pools = [(e or "", pool) for e in entities
                             for pool in [[m for m in candidates if m.entity in (None, e)]]
                             if aggregation._strongest(rule, pool) == list(selection)
                             and aggregation._meets_thresholds(rule, pool)]
                elif not aggregation._meets_thresholds(rule, list(selection)):
                    continue
                for e, pool in pools:
                    key = (-min(m.confidence for m in selection), tuple(r.index(m) for r, m in zip(ranked, selection)), e)
                    if found is None or key < found[0]:
                        found = (key, [m.memory_id for m in selection], sorted(m.memory_id for m in pool))
            return found[1:] if found else None

        for trial in range(1500):
            slots = ["a", "b", "c"][: rnd.randint(1, 3)]
            star = trial % 2 == 1
            rule = aggregation.Rule(rule_id="r", target_layer="department", required_slots=slots, conclusion="r",
                                    min_agents=rnd.randint(1, 4), min_teams=rnd.randint(1, 3),
                                    corroborate=star and rnd.random() < 0.3)
            agents = [(f"ag-{i}", f"team-{rnd.randint(0, 2)}") for i in range(rnd.randint(1, 5))]
            candidates = [note(i, *rnd.choice(agents), rnd.choice(slots), rnd.choice((0.5, 0.6, 0.8, 0.9, 0.95)),
                               rnd.choice((None, None, "e1", "e2")) if star else "e1") for i in range(rnd.randint(1, 9))]
            expected = best(rule, candidates, one_entity=star)
            with self.subTest(trial=trial):
                if not star:
                    got = aggregation._select(rule, candidates)
                    self.assertEqual([m.memory_id for m in got] if got else None, expected and expected[0])
                    continue
                found = aggregation._wildcard_scopes(rule, candidates)
                if found is None or expected is None:
                    self.assertEqual(found, expected)
                elif rule.corroborate:
                    self.assertEqual(([m.memory_id for m in found[0]], sorted(m.memory_id for m in found[1])), expected)
                else:
                    self.assertEqual([m.memory_id for m in found[0]], expected[0])

    def test_a_qualifying_selection_is_kept_when_the_search_reaches_its_bound(self) -> None:
        """Shaped like regional_supply_risk (three slots, min_agents 3, min_teams 2): x is the strongest in transport, w's
        buffer note is weak, and 2,000 agents of seven teams commit demand.  x confirming the buffer more confidently leaves
        the conclusion as it is.  With the search bounded to fewer selections than it needs, a qualifying selection it
        found is kept, never dropped, and the evidence alone reproduces the conclusion: where the bound stopped the search
        short of the best selection, the best one among the disputed slots' sides replaces it."""
        slots = ["transport_disruption", "supplier_buffer_low", "demand_commitment"]
        rule = aggregation.Rule(rule_id="regional", target_layer="region", required_slots=slots, min_agents=3, min_teams=2,
                                conclusion="Risk for {entity}: {slot:transport_disruption}; {slot:supplier_buffer_low}; "
                                           "{slot:demand_commitment}")

        def conclude(r: aggregation.Rule, candidates: list[Memory], entity: str | None = "sd-9") -> tuple | None:
            return aggregation.build_conclusion(r, ORG, "northwind/emea", entity, candidates, version_of=None, now=now_iso())

        base = [raw_note(0, "x", "logistics", slots[0], 0.99, "sd-9"), raw_note(1, "w", "warehouse", slots[1], 0.5, "sd-9")]
        base += [raw_note(10 + i, f"d{i}", f"plan-{i % 7}", slots[2], round(0.6 + 0.38 * i / 2000, 6), "sd-9")
                 for i in range(2000)]
        aggregation._exhausted_warned.discard("regional")
        with Capture("mycelic.aggregation", logging.WARNING) as logs:
            before = conclude(rule, base)
            after = conclude(rule, base + [raw_note(2, "x", "logistics", slots[1], 0.99, "sd-9")])
        assert before is not None and after is not None
        self.assertEqual(logs.lines, [], "the search ends well within its bound")
        self.assertEqual(after[0].memory_id, before[0].memory_id, "the agreeing note changes nothing")
        self.assertEqual([m.producer_id for m in after[1]], ["x", "w", "d1999"])

        # x is the strongest in a and b, so the best selection is y, x, u (0.9); the first one found in ranking order is
        # x, z, u (0.3), after x's 60 notes on c (each from another team, none a third agent).  a and b are disputed, so
        # x's, y's and z's notes there are all evidence, and x's notes on c are not.
        small = aggregation.Rule(rule_id="small", target_layer="region", required_slots=["a", "b", "c"], min_agents=3,
                                 conclusion="{slot:a} / {slot:b} / {slot:c}")
        cands = [raw_note(20, "x", "logistics", "a", 0.99, "sd-9", "v1"), raw_note(21, "y", "warehouse", "a", 0.9, "sd-9", "v2"),
                 raw_note(22, "x", "logistics", "b", 0.98, "sd-9", "w1"), raw_note(23, "z", "planning", "b", 0.3, "sd-9", "w2"),
                 raw_note(24, "u", "sales", "c", 0.95, "sd-9")]
        cands += [raw_note(100 + i, "x", f"team-{i}", "c", 0.96 + i / 2000, "sd-9") for i in range(60)]
        best = conclude(small, cands)
        assert best is not None
        self.assertEqual((best[0].confidence, best[0].metadata["slots"]), (0.9, {"a": "mem_000021", "b": "mem_000022",
                                                                                "c": "mem_000024"}))
        held, settled = [], []
        for bound in range(1, 400, 3):
            with self.subTest(bound=bound), mock.patch.object(aggregation, "COALITION_SEARCH_NODES", bound):
                first = aggregation._select(small, cands)
                built = conclude(small, cands)
                if built is None:
                    continue
                held.append(bound)
                if first is not None and min(m.confidence for m in first) < 0.9:
                    settled.append(bound)
                self.assertEqual((built[0].memory_id, built[0].text, built[0].confidence),
                                 (best[0].memory_id, best[0].text, best[0].confidence))
                again = conclude(small, built[1])
                assert again is not None
                self.assertEqual((again[0].memory_id, again[0].text, again[0].confidence),
                                 (built[0].memory_id, built[0].text, built[0].confidence))
        self.assertTrue(held and held == list(range(held[0], 400, 3)), held)
        self.assertTrue(settled, "some bound stops the search at the first selection it finds")

        # whatever the bound, a conclusion is reproduced from its evidence alone (verification re-derives it so)
        rnd = random.Random(20261010)
        for trial in range(800):
            star = trial % 3 == 0
            r = aggregation.Rule(rule_id="r", target_layer="region", required_slots=["a", "b", "c"][: rnd.randint(2, 3)],
                                 conclusion="{entity}: {slot:a} / {slot:b}", min_agents=rnd.randint(2, 4),
                                 min_teams=rnd.randint(1, 3), corroborate=star and rnd.random() < 0.3)
            agents = [(f"ag-{i}", f"team-{rnd.randint(0, 3)}") for i in range(rnd.randint(2, 6))]
            cands = [raw_note(i, *rnd.choice(agents), rnd.choice(r.required_slots), rnd.choice((0.3, 0.5, 0.6, 0.8, 0.9, 0.95)),
                              rnd.choice((None, "e1", "e2")) if star else "e1", rnd.choice((None, None, "open", "closed")))
                     for i in range(rnd.randint(2, 14))]
            with self.subTest(trial=trial), mock.patch.object(aggregation, "COALITION_SEARCH_NODES", rnd.randint(1, 40)):
                built = conclude(r, cands, None if star else "e1")
                if built is None:
                    continue
                again = conclude(r, built[1], None if star else "e1")
                assert again is not None
                self.assertEqual((again[0].memory_id, again[0].text, again[0].confidence, again[0].metadata.get("conflict")),
                                 (built[0].memory_id, built[0].text, built[0].confidence, built[0].metadata.get("conflict")))

    def test_a_wildcard_evaluation_is_one_search_whatever_the_number_of_components(self) -> None:
        """Shaped like regional_supply_risk: one logistics bot notes transport and a thin buffer for 1,000 components, and
        planners in five teams note committed demand without naming a component.  Each of the 1,000 pools "the notes that
        name no component, and the notes about E" covers all three slots and none has three agents, so no '*' conclusion
        holds.  One '*' evaluation works out each author's agents and teams once, not once per pool, and runs one search
        for all the pools, within one search bound; with 3,000 planners it takes well under a second (searching each pool
        on its own took about 24 s).  A procurement note on one component's buffer then makes the '*' conclusion hold on
        that component's notes and the strongest demand note."""
        slots = ["transport_disruption", "supplier_buffer_low", "demand_commitment"]
        rule = aggregation.Rule(rule_id="regional", target_layer="region", required_slots=slots, min_agents=3, min_teams=2,
                                conclusion="Risk for {entity}: {slot:transport_disruption}; {slot:supplier_buffer_low}; "
                                           "{slot:demand_commitment}")

        def notes(planners: int) -> list[Memory]:
            out = []
            for e in range(1000):
                out.append(raw_note(2 * e, "x", "logistics", slots[0], 0.9, f"sd-{e}"))
                out.append(raw_note(2 * e + 1, "x", "logistics", slots[1], round(0.5 + 0.4 * e / 1000, 6), f"sd-{e}"))
            return out + [raw_note(10_000 + k, f"z{k}", f"planning-{k % 5}", slots[2], round(0.5 + 0.4 * k / planners, 6), None)
                          for k in range(planners)]

        def conclude(candidates: list[Memory]) -> tuple | None:
            return aggregation.build_conclusion(rule, ORG, "northwind/emea", None, candidates, version_of=None, now=now_iso())

        cands = notes(300)
        aggregation._exhausted_warned.discard("regional")
        with Capture("mycelic.aggregation", logging.WARNING) as logs, \
                mock.patch.object(aggregation, "contributing_teams", wraps=aggregation.contributing_teams) as teams, \
                mock.patch.object(aggregation, "_coalition", wraps=aggregation._coalition) as search:
            self.assertIsNone(conclude(cands))
        self.assertEqual(search.call_count, 1, "one search for every pool")
        self.assertLessEqual(teams.call_count, len(cands), "each candidate's teams are worked out once at most")
        self.assertEqual(logs.lines, [], "the search ends within its bound")

        cands = notes(3000)
        t0 = time.perf_counter()
        self.assertIsNone(conclude(cands))
        self.assertLess(time.perf_counter() - t0, 2.0)
        buffer = raw_note(9_999, "p", "procurement", slots[1], 0.95, "sd-500")
        built = conclude(cands + [buffer])
        assert built is not None
        self.assertEqual(built[0].metadata["slots"], {slots[0]: "mem_001000", slots[1]: buffer.memory_id, slots[2]: "mem_012999"})
        self.assertEqual({m.entity for m in built[1]}, {None, "sd-500"})

    async def test_a_wildcard_conclusion_never_stitches_two_entities(self) -> None:
        """The shipped supply-risk rules: transport is disrupted for sd-9, the buffer is thin for sd-10, and a note that
        names no component says demand is committed.  No component has all three conditions, so no rule concludes at any
        layer and a query for the risk is not answered by one; a '*' conclusion stitched that way (as an earlier release
        derived it) fails verification.  Once the buffer is thin for sd-9 too, the '*' conclusion rests on sd-9's notes
        and the note that names none, and verifies."""
        h = await self.harness(agents=False)
        s = h.service
        rules = {r["rule_id"]: r for r in json.loads((Path(__file__).resolve().parents[2] / "deploy" / "mycelic"
                                                      / "rules.json").read_text())["rules"]}
        for rule_id in ("component_supply_risk", "regional_supply_risk"):
            await s.upsert_rule(rules[rule_id])
        for a, team in (("a1", "logistics"), ("a2", "procurement"), ("a3", "planning"), ("a4", "warehouse")):
            await h.register(a, team=team)
        tr = await h.observe("a1", "Rotterdam strike blocks SD-9 inbound.", topic="supply:drives", slot="transport_disruption",
                             entity="sd-9", confidence=0.9, visibility="org")
        buf = await h.observe("a2", "SD-10 supplier buffer down to 2 days.", topic="supply:drives", slot="supplier_buffer_low",
                              entity="sd-10", confidence=0.9, visibility="org")
        dem = await h.observe("a3", "Q4 orders are committed.", topic="supply:drives", slot="demand_commitment", confidence=0.9,
                              visibility="org")
        await h.settle()
        conclusions = lambda: [m for m in s.store.list_memories(ORG, limit=1000) if m.operator == "slot_composition"]  # noqa: E731
        self.assertEqual(conclusions(), [], "no component has all three conditions")
        answer = s.query(h.principal("a3"), {"query": "supply risk", "scope": ORG, "k": 5})["answer"]
        self.assertTrue(answer is None or answer["operator"] != "slot_composition", answer)
        regional = s.store.get_applied_rule("regional_supply_risk")
        notes = [s.store.get_memory(x) for x in (tr, buf, dem)]
        self.assertIsNone(aggregation.build_conclusion(regional, ORG, "northwind/emea", None, notes, version_of=None, now=now_iso()))
        # as an earlier release stitched it (the strongest note of each slot, whatever it names, next to one that names
        # none): the evidence of a '*' conclusion about two components fails verification
        with mock.patch.object(aggregation, "_wildcard_scopes", lambda rule, cands: (aggregation._strongest(rule, cands), cands)), \
                mock.patch.object(aggregation, "wildcard_selection", lambda selected: any(m.entity is None for m in selected)):
            await full_reaggregation_pass(s)
        [stitched] = [m for m in conclusions() if m.rule_id == "regional_supply_risk"]
        report = await s.verify(h.admin, stitched.memory_id)
        self.assertEqual((report["verdict"], report["derived_correctly"]), ("failed", False))
        self.assertIn("parent_ineligible", [r["code"] for r in report["reasons"]])
        # one component, and a note that names none: the '*' conclusion holds and verifies
        await h.observe("a4", "SD-9 supplier buffer down to 3 days.", topic="supply:drives", slot="supplier_buffer_low",
                        entity="sd-9", confidence=0.8, visibility="org")
        await h.settle()
        [star] = [m for m in conclusions() if m.rule_id == "regional_supply_risk"]
        self.assertNotEqual(star.memory_id, stitched.memory_id)
        self.assertEqual(s.store.get_memory(stitched.memory_id).status, "superseded")
        self.assertEqual((star.entity, {s.store.get_memory(e.parent_id).entity for e in s.store.parents_of(star.memory_id)}),
                         (None, {None, "sd-9"}))
        report = await s.verify(h.principal("a3"), star.memory_id)
        self.assertEqual((report["verdict"], report["derived_correctly"], report["still_true"]), ("verified", True, True))

    # ------------------------------------------------------------------ log order
    async def test_rules_and_registry_are_read_in_log_order_and_rebuild_identically(self) -> None:
        transport = HeldTransport()
        h = await ServiceHarness(transport=transport).start()
        self.addAsyncCleanup(h.close)
        s1 = h.service
        for a in ("log-1", "log-2", "log-3"):
            await h.register(a, team="logistics")
        await h.observe("log-1", "Port strike.", topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.9)
        await h.observe("log-2", "Carrier slipped.", topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.7)
        # all of this is written by the API before the consumer has applied a single event: a second region (the
        # enterprise would no longer promote the region's consolidation), a rule the notes satisfy, a revocation
        await h.register("apac-1", team="logistics", subsidiary="nw-kk", region="apac")
        await s1.upsert_rule({"rule_id": "lag", "target_layer": "region", "required_slots": ["transport_disruption"],
                              "corroborate": True, "min_agents": 2,
                              "conclusion": "Transport risk for {entity}: {slot:transport_disruption}"})
        await s1.revoke_agent("log-3")
        self.assertEqual(s1.store.get_rule("lag").min_agents, 2, "the admin table shows the change at once")
        await drain_outbox(s1)
        transport.hold = False
        await h.settle()
        live = history(s1.store)
        await s1.stop()
        s2 = MycelicService(settings(h.tmp.name), store=MycelicStore(":memory:"), transport=transport, metrics=Metrics())
        await s2.start()
        try:
            self.assertTrue(await s2.wait_idle(15))
            self.assertEqual(history(s2.store), live, "the full history is the same on the live node and on a rebuild")
            lag = [m for m in s1.store.list_memories(ORG, status=None, limit=1000) if m.rule_id == "lag"]
            self.assertEqual([(m.status, m.support, m.metadata.get("version_of")) for m in lag], [("active", 2, None)],
                             "the rule, applied after the notes, concludes on them once, at its own apply")
            self.assertEqual([m.status for m in s1.store.list_memories(ORG, layers=["enterprise"], status=None, limit=1000)],
                             ["retracted"], "the notes were applied while the enterprise had one region, and apac-1's "
                                            "registration withdrew the promotion")
            self.assertEqual([r[0] for r in applied_rules(s1.store)], ["lag"])
            self.assertEqual(applied_rules(s2.store), applied_rules(s1.store))
            self.assertEqual(s2.store.child_units(ORG, "northwind"), s1.store.child_units(ORG, "northwind"))
        finally:
            await s2.close()

    async def test_capped_mixed_leaves_rebuild_identically_under_lag(self) -> None:
        """More raw notes and rule conclusions on one topic than the cap, all applied behind consumer lag: apply order,
        not ingest order, picks the newest candidates, so the rebuild derives the same history."""
        transport = HeldTransport()
        h = await ServiceHarness(transport=transport).start()
        self.addAsyncCleanup(h.close)
        s1 = h.service
        s1.aggregator.max_candidates = 5
        for a, team in (("a1", "t1"), ("a2", "t1"), ("b1", "t2"), ("b2", "t2")):
            await h.register(a, team=team)
        # each note with slot x also yields a team conclusion on the same topic: a leaf derived at apply time
        await s1.upsert_rule({"rule_id": "mk", "target_layer": "team", "required_slots": ["x"], "emits_topic": "ops:mix",
                              "min_agents": 1, "corroborate": True, "conclusion": "MK {entity}: {slot:x}"})
        for i in range(16):
            await h.observe(("a1", "b1", "a2", "b2")[i % 4], f"mixed note {i}", topic="ops:mix", slot="x" if i % 3 else None,
                            entity="e", confidence=0.5 + i / 100)
        await drain_outbox(s1)
        transport.hold = False
        await h.settle(timeout=30)
        live = {m.memory_id: (m.layer, m.status, m.support, m.metadata.get("version_of"), m.text)
                for m in s1.store.list_memories(ORG, status=None, limit=10_000)}
        await s1.stop()
        s2 = MycelicService(settings(h.tmp.name), store=MycelicStore(":memory:"), transport=transport, metrics=Metrics())
        s2.aggregator.max_candidates = 5
        await s2.start()
        try:
            self.assertTrue(await s2.wait_idle(30))
            rebuilt = {m.memory_id: (m.layer, m.status, m.support, m.metadata.get("version_of"), m.text)
                       for m in s2.store.list_memories(ORG, status=None, limit=10_000)}
            self.assertEqual(rebuilt, live)
            self.assertEqual(len(s1.store.list_memories(ORG, layers=["enterprise"])), 1, "the cap froze nothing")
            # one version per note from the second on (the first note is one team's: no coalition yet), never two: the
            # rule's conclusion on the note's topic is in place before the units above consolidate the topic
            self.assertEqual(len([m for m in live.values() if m[0] == "enterprise"]), 15, "one version per note")
            self.assertGreater(counter(s1.metrics.aggregation_truncated, "candidates"), 0)
        finally:
            await s2.close()

    async def test_registry_lag_is_not_counted_in_child_units(self) -> None:
        transport = HeldTransport(hold=False)
        h = await ServiceHarness(transport=transport).start()
        self.addAsyncCleanup(h.close)
        s = h.service
        for a, team in (("a-1", "t1"), ("a-2", "t2")):
            await h.register(a, team=team)
        await h.settle()
        self.assertEqual(s.store.child_units(ORG, OPS), [f"{OPS}/t1", f"{OPS}/t2"])
        transport.hold = True
        await h.register("a-3", team="t3")
        await s.revoke_agent("a-2")
        await drain_outbox(s)
        self.assertEqual(s.store.child_units(ORG, OPS), [f"{OPS}/t1", f"{OPS}/t2"],
                         "registered but not applied: not counted; revoked but not applied: still counted")
        self.assertEqual([a.agent_id for a in await s.store.list_agents(ORG)], ["a-1", "a-3"], "the admin view is immediate")
        transport.hold = False
        await h.settle()
        self.assertEqual(s.store.child_units(ORG, OPS), [f"{OPS}/t1", f"{OPS}/t3"])
        replay = wire("agent.registered", {**s.store.get_agent("a-1").to_dict(), "key_hash": "x"})
        self.assertEqual(await s.apply_event(replay), "duplicate", "a registration already applied changes nothing")

    async def test_rule_events_never_regress_the_admin_table(self) -> None:
        transport = HeldTransport()
        h = await ServiceHarness(transport=transport).start()
        self.addAsyncCleanup(h.close)
        s = h.service
        rule = {"rule_id": "r1", "target_layer": "team", "required_slots": ["a"], "conclusion": "R"}

        def admin_min() -> int | None:
            r = s.store.get_rule("r1")
            return r.min_agents if r else None

        def applied_min() -> int | None:
            r = s.store.get_applied_rule("r1")
            return r.min_agents if r else None

        # an older upsert applied after a newer one from the API
        await s.upsert_rule({**rule, "min_agents": 2})
        await s.upsert_rule({**rule, "min_agents": 3})
        await drain_outbox(s)
        await step(s, transport)
        self.assertEqual(admin_min(), 3, "the older event does not overwrite the newer API write")
        self.assertEqual(applied_min(), 2, "but it is what aggregation uses until the newer event applies")
        await step(s, transport)
        self.assertEqual((admin_min(), applied_min()), (3, 3))
        # upsert, then delete
        await s.upsert_rule({**rule, "min_agents": 4})
        await s.delete_rule("r1")
        await drain_outbox(s)
        await step(s, transport)
        self.assertIsNone(admin_min(), "the deleted rule is not resurrected")
        self.assertEqual(applied_min(), 4)
        await step(s, transport)
        self.assertEqual((admin_min(), applied_min()), (None, None))
        # create, delete, re-create
        await s.upsert_rule({**rule, "min_agents": 5})
        await s.delete_rule("r1")
        await s.upsert_rule({**rule, "min_agents": 6})
        await drain_outbox(s)
        for expected in ((6, 5), (6, None), (6, 6)):
            await step(s, transport)
            self.assertEqual(admin_min(), expected[0], "the re-created rule is not deleted again")
            self.assertEqual(applied_min(), expected[1])
        self.assertEqual(await s.apply_event(wire("rule.deleted", {"rule_id": "never-existed"}, org_id="_")), "applied")
        transport.hold = False
        await h.settle()
        self.assertEqual((admin_min(), applied_min()), (6, 6))
        # a rules file loaded at start, then a replay of an older stream version of the same rule
        await s.upsert_rule({**rule, "rule_id": "filed", "min_agents": 2})
        await h.settle()
        await s.stop()
        transport.hold = True
        path = Path(h.tmp.name) / "rules.json"
        path.write_text(json.dumps({"rules": [{**rule, "rule_id": "filed", "min_agents": 7}]}))
        s2 = MycelicService(settings(h.tmp.name, db_path=str(Path(h.tmp.name) / "fresh.db"), rules_file=str(path)),
                            transport=transport, metrics=Metrics())
        await s2.start()
        try:
            self.assertEqual(s2.store.get_rule("filed").min_agents, 7)
            await drain_outbox(s2)
            while True:
                ev = await step(s2, transport)
                if ev["kind"] == "rule.upserted" and ev["payload"]["rule_id"] == "filed":
                    break
            self.assertEqual(ev["payload"]["min_agents"], 2)
            self.assertEqual(s2.store.get_rule("filed").min_agents, 7, "the replayed older version does not overwrite the file's")
            self.assertEqual(s2.store.get_applied_rule("filed").min_agents, 2, "it is applied in log order all the same")
            transport.hold = False
            self.assertTrue(await s2.wait_idle(15))
            self.assertEqual((s2.store.get_rule("filed").min_agents, s2.store.get_applied_rule("filed").min_agents), (7, 7))
            self.assertEqual(s2.store.get_rule("r1").min_agents, 6, "stream-only events still rebuild the admin table")
        finally:
            await s2.close()

    async def test_pre_v3_stream_replays_into_canonical_state(self) -> None:
        transport = InProcessTransport()
        await transport.connect()
        agents = {}
        for a in ("log-1", "log-2"):
            _, key_hash, prefix = generate_api_key(a)
            agent = Agent(agent_id=a, org_id=ORG, display_name=a, path=f"{TEAM}/{a}", scopes=["memory:read", "memory:write"],
                          key_prefix=prefix, created_at=now_iso())
            agents[a] = agent
            await publish(transport, wire("agent.registered", {**agent.to_dict(), "key_hash": key_hash}))
        notes = []
        for a, topic, entity in (("log-1", " Supply:SD-9/Transport ", "SD-9"), ("log-2", "SUPPLY:SD-9/TRANSPORT", "sd-9 ")):
            ev = wire("memory.observed", {})
            m = Memory(memory_id=new_id("mem"), org_id=ORG, layer="agent", scope=agents[a].path, text=f"note by {a}", topic=topic,
                       slot="Transport_Disruption", entity=entity, kind="observation", confidence=0.8, support=1,
                       independent_teams=1, producer_id=a, operator="agent_observation", rule_id=None,
                       event_id=ev["event_id"], created_at=now_iso())
            ev["payload"] = m.to_dict()
            notes.append(m.memory_id)
            await publish(transport, ev)
        # what the old code derived from them, under the raw-case key: never reproduced, so ignored
        old_ids = []
        for key in ("Supply:SD-9/Transport", " Supply:SD-9/Transport "):
            mid = derived_memory_id(operator="topic_consolidation", scope=TEAM, key=key, parent_ids=notes)
            old_ids.append(mid)
            await publish(transport, wire("memory.derived", {"memory_id": mid, "org_id": ORG, "layer": "team", "scope": TEAM,
                                                             "text": "old", "topic": key, "operator": "topic_consolidation"}))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = MycelicService(settings(tmp.name), transport=transport, metrics=Metrics())
        await s.start()
        try:
            self.assertTrue(await s.wait_idle(15))
            raw = s.store.list_memories(ORG, layers=["agent"])
            self.assertEqual({(m.topic, m.slot, m.entity) for m in raw}, {("supply:sd-9/transport", "transport_disruption", "sd-9")})
            [team] = s.store.list_memories(ORG, layers=["team"])
            self.assertEqual((team.topic, team.support), ("supply:sd-9/transport", 2))
            self.assertEqual([s.store.get_memory(mid) for mid in old_ids], [None, None])
            rows = audits(s.store, "event.ignored")
            self.assertEqual([r["detail"] for r in rows], [{"reason": "derived_not_reproduced", "count": 2, "replay": True}])
            recreated = s.store.list_events(ORG, kind="memory.derived", status="published", limit=100)
            self.assertTrue(recreated)
            self.assertEqual({e.js_seq for e in recreated}, {None}, "re-derived, not on the stream, never published again")
            self.assertEqual(s.store.stats()["outbox_pending"], 0)
            ready, _ = await s.ready()
            self.assertTrue(ready)
            self.assertTrue(await s.wait_idle(5))
            self.assertEqual(counter(s.metrics.events_applied, "memory.derived", "ignored"), 2)
        finally:
            await s.close()

    # ------------------------------------------------------------------ store filters
    async def test_substring_filters_treat_wildcards_literally(self) -> None:
        store = MycelicStore(":memory:")
        self.addAsyncCleanup(store.close)
        rows = [("s_pply%:a", "acme/r_1/a_b/d/t/x"), ("supply%:x", "acme/r_1/aXb/d/t/x"), ("s_pplyx:", "acme/r_1/a_bc/d/t/x"),
                ("s_pply%:b", "acme/r_1/a_b"), (None, "acme/r_1/a_b/d/t/y")]
        async with store.transaction() as tx:
            for i, (topic, scope) in enumerate(rows):
                tx.insert_memory(Memory(memory_id=f"m{i}", org_id="acme", layer="agent", scope=scope, text=str(i), topic=topic,
                                        slot="s" if i % 2 else None, entity=None if i < 3 else "e", kind="observation",
                                        confidence=0.5, support=1, independent_teams=1, producer_id="p",
                                        operator="agent_observation", rule_id=None, event_id=None, created_at=now_iso(),
                                        applied_at=now_iso()))

        def ids(**kw: Any) -> list[str]:
            return [m.memory_id for m in store.list_memories("acme", status=None, latest=True, limit=100, **kw)]

        self.assertEqual(ids(topic_prefix="s_pply%:"), ["m0", "m3"])
        self.assertEqual(ids(topic_prefix="s_pply"), ["m0", "m2", "m3"])
        self.assertEqual(ids(exclude_subtrees=["acme/r_1/a_b"]), ["m1", "m2"])
        self.assertEqual(ids(scope="acme/r_1/a_b"), ["m0", "m3", "m4"])
        self.assertEqual(ids(slots=["s"]), ["m1", "m3"])
        self.assertEqual(ids(null_entity=True), ["m0", "m1", "m2"])
        self.assertEqual([m.memory_id for m in store.list_memories("acme", status=None, latest=True, limit=2)], ["m3", "m4"],
                         "the newest in apply order, returned oldest first")

    # ------------------------------------------------------------------ shapes of hierarchy
    async def test_single_agent_org_reaches_the_enterprise(self) -> None:
        h = await self.harness(agents=False, min_support=1)
        s = h.service
        await h.register("solo", team="core", department="all", subsidiary="solo-gmbh", region="eu", enterprise="solo-co")
        mid = await h.observe("solo", "The only agent's only note.", topic="ops:note")
        await h.settle()
        layers = {m.layer: m for m in s.store.list_memories("solo-co", status="active", limit=100) if m.layer != "agent"}
        self.assertEqual(set(layers), {"team", "department", "subsidiary", "region", "enterprise"})
        self.assertEqual(layers["enterprise"].metadata["roots"], [mid])
        g = s.lineage(h.admin, layers["enterprise"].memory_id)
        self.assertEqual(g["layers"], ["agent", "team", "department", "subsidiary", "region", "enterprise"])

    # ------------------------------------------------------------------ redelivery and concurrency
    async def test_duplicate_and_redelivered_events_are_noops(self) -> None:
        h = await self.harness()
        s = h.service
        await s.upsert_rule(DEMO_RULE)
        ids = await self.team_notes(h, "log-1", "log-2")
        await h.observe("proc-1", "Two weeks of stock.", topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity="sd-9")
        await h.observe("sales-1", "Committed demand.", topic="supply:sd-9/demand", slot="demand_commitment", entity="sd-9")
        await h.settle()
        await s.retract(h.principal("log-1"), ids[0], "withdrawn")
        await h.settle()

        def snapshot() -> tuple:
            edges = sorted(tuple(r) for r in s.store._conn.execute("SELECT child_id, parent_id FROM lineage_edges"))
            rows = sorted(tuple(r) for r in s.store._conn.execute(
                "SELECT memory_id, status, text, confidence, support, apply_seq, metadata FROM memories"))
            return rows, edges, applied_rules(s.store), s.store.event_counts()

        before = snapshot()
        self.assertTrue([m for m in s.store.list_memories(ORG, layers=["enterprise"]) if m.rule_id == DEMO_RULE["rule_id"]])
        # every event of the log applied again, in reverse order: all duplicates
        log = [(json.loads(payload), seq) for seq, (_, payload, _, _) in enumerate(s.transport._log, start=1)]
        results = {await s.apply_event(ev, seq=seq) for ev, seq in reversed(log)}
        self.assertEqual(results, {"duplicate"})
        # the whole log redelivered by the transport, and a publish retried under the same message id
        await s.transport.reset_consumer()
        await s.transport.publish("x", s.transport._log[0][1], s.transport._log[0][2])
        await h.settle()
        after = snapshot()
        self.assertEqual(after[:3], before[:3], "memories, apply order, lineage and applied rules are unchanged")
        self.assertEqual(after[3], before[3])
        self.assertEqual(s.store.get_memory(ids[0]).status, "retracted", "a redelivered observation does not undo its retraction")
        # events that name something they cannot act on are applied without effect, audited and counted
        [team] = s.store.list_memories(ORG, layers=["team"], status=None, limit=1)
        for event, reason in ((wire("memory.retracted", {"memory_id": team.memory_id}), "retraction_target"),
                              (wire("memory.retracted", {"memory_id": "mem_unknown"}), "retraction_target"),
                              (wire("memory.mystery", {"memory_id": ids[1]}), "unknown_kind")):
            with self.subTest(kind=event["kind"], payload=event["payload"]):
                self.assertEqual(await s.apply_event(event), "ignored")
                self.assertEqual(s.store.get_event(event["event_id"]).status, "applied")
        self.assertEqual(counter(s.metrics.events_ignored, "retraction_target"), 2)
        self.assertEqual(counter(s.metrics.events_ignored, "unknown_kind"), 1)
        self.assertEqual(len([a for a in s.store.recent_audit(1000, org_id=ORG) if a["action"] == "event.ignored"]), 2)
        self.assertEqual(snapshot()[0], before[0], "and change no memory")

    async def test_concurrent_identical_idempotent_posts_create_one(self) -> None:
        h = await self.harness()
        s = h.service
        p = h.principal("log-1")
        results = await asyncio.gather(*(s.ingest_memory(p, {"text": "same note", "topic": "ops:x", "idempotency_key": "k"})
                                         for _ in range(20)))
        self.assertEqual(len({m.memory_id for m, _ in results}), 1)
        self.assertEqual(sum(1 for _, created in results if created), 1)
        await h.settle()
        self.assertEqual(len(s.store.list_memories(ORG, layers=["agent"], producer_id="log-1")), 1)
        self.assertEqual(len(s.store.list_events(ORG, kind="memory.observed", limit=100)), 1)


if __name__ == "__main__":
    unittest.main()
