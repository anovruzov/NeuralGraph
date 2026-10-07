"""Lifecycle extensions that propagate upward through the log and show in downward verification: an agent removed
(``DELETE /admin/agents/{id}?retract=1``: one event retracts every note it has in one apply, its key refused at once,
notes logged after the removal applied retracted) and moved (re-shared under a new id, then the old id removed); a note
that expires (``expires_at``: hidden from answers at once, reported ``leaf_expired`` by verification, retracted through
the log by the expiry sweep, exactly once per note, page by page during a broker outage)."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from datetime import timedelta
from typing import Any
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

from NeuralGraph.chat_memory.textutil import tokenize
from mycelic import cli, mcp
from mycelic.api import create_app
from mycelic.auth import AuthError
from mycelic.mcp import MycelicTools, _principal
from mycelic.models import Memory, content_hash, new_id, now_iso, utcnow
from mycelic.retrieval import BM25
from mycelic.sdk import LocalMemory, MycelicClient, MycelicError
from mycelic.service import MAX_EXPIRY_SECONDS, Conflict, Forbidden, NotFound, ValidationError
from mycelic.store import MycelicStore

from .helpers import (
    ADMIN_TOKEN, HeldTransport, ServiceHarness, broken_chains, drain_outbox, invariant_violations, rebuild, rebuild_differences,
    table_dump,
)
from .test_verification import TRANSPORT, World, codes, demo, detail, view

ORG = "northwind"
K = "lifecycle-signing-key-0123456789abcdef0123"


def kinds(store: MycelicStore) -> dict[str, int]:
    return {r["kind"]: r["n"] for r in store._conn.execute("SELECT kind, COUNT(*) AS n FROM events GROUP BY kind")}


def audits(store: MycelicStore, action: str) -> list[dict[str, Any]]:
    return [a for a in store.recent_audit(100_000) if a["action"] == action]


def wire(kind: str, payload: Any, *, org_id: str = ORG) -> dict[str, Any]:
    return {"event_id": new_id("evt"), "kind": kind, "org_id": org_id, "agent_id": None, "created_at": now_iso(),
            "payload": payload, "schema": 1}


def notes_of(store: MycelicStore, agent_id: str) -> list[Memory]:
    return store.list_memories(ORG, status=None, producer_id=agent_id, operator="agent_observation", limit=10_000)


def observed(store: MycelicStore, agent_id: str, memory_id: str, text: str, **fields: Any) -> dict[str, Any]:
    """A ``memory.observed`` event of a registered agent as the stream could carry it (a write that raced a check on the
    API side, or one from another writer)."""
    agent = store.get_agent(agent_id)
    m = Memory(memory_id=memory_id, org_id=agent.org_id, layer="agent", scope=agent.path, text=text, topic=None, slot=None,
               entity=None, kind="observation", confidence=0.8, support=1, independent_teams=1, producer_id=agent_id,
               operator="agent_observation", rule_id=None, event_id=None, created_at=now_iso())
    for k, v in fields.items():
        setattr(m, k, v)
    event = wire("memory.observed", {})
    m.event_id = event["event_id"]
    event["payload"] = m.to_dict()
    return event


def iso(dt: Any) -> str:
    return dt.isoformat(timespec="seconds")


async def mcp_call(service: Any, principal: Any, name: str, **arguments: Any) -> Any:
    """A tool of the MCP surface as ``principal`` (the API middleware sets the same context variable per request)."""
    token = _principal.set(principal)
    try:
        return await MycelicTools(service).call(name, arguments)
    finally:
        _principal.reset(token)


async def apply_next(service: Any, log: list[dict[str, Any]]) -> dict[str, Any]:
    """Publish and apply the oldest pending event (``helpers.pump``, one event at a time)."""
    [ev] = service.store.pending_events(1)
    seq = len(log) + 1
    async with service.store.transaction() as tx:
        tx.mark_published(ev.event_id, seq)
    log.append(json.loads(json.dumps(service._event_wire(ev))))
    await service.apply_event(log[-1], seq=seq)
    return log[-1]


async def apply_through(service: Any, log: list[dict[str, Any]], kind: str) -> set[str]:
    """Apply pending events in outbox order up to and including the first of ``kind``; the memory ids that one created."""
    while service.store.pending_events(1)[0].kind != kind:
        await apply_next(service, log)
    before = {r["memory_id"] for r in service.store._conn.execute("SELECT memory_id FROM memories")}
    await apply_next(service, log)
    return {r["memory_id"] for r in service.store._conn.execute("SELECT memory_id FROM memories")} - before


class AgentRemovalTests(unittest.IsolatedAsyncioTestCase):
    def tmpdir(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    async def world(self, **kwargs: Any) -> World:
        w = World(self.tmpdir(), **kwargs)
        self.addAsyncCleanup(w.close)
        await w.service.transport.connect()
        return w

    def assertNoFlapping(self, store: MycelicStore, created: set[str]) -> None:
        """At most one new row per (unit, operator, key), and every row the apply created is still active after it."""
        rows = [store.get_memory(mid) for mid in created]
        keys = [(m.scope, m.operator, m.metadata.get("agg_key")) for m in rows]
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertEqual({m.memory_id: m.status for m in rows}, dict.fromkeys(created, "active"))

    async def test_remove_agent_retracts_in_one_apply_and_cascades(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st, old = w.service, w.store, ids["conclusion"]
        # two more notes of log-1, still in the outbox when the removal is asked for: they apply first, then go with it
        extra = [await w.observe("log-1", "Picket lines at terminal 3 confirmed.", topic=TRANSPORT, slot="transport_disruption",
                                 entity="sd-9", confidence=0.5),
                 await w.observe("log-1", "Rotterdam yard is full.", topic="ops:yard")]
        events = kinds(st)
        res = await s.revoke_agent("log-1", retract=True, remote="10.0.0.1")
        self.assertEqual(res, {"agent_id": "log-1", "revoked": True, "retracted": 3}, "counted when asked, applied or not")
        after = kinds(st)
        self.assertEqual(after.pop("agent.removed"), 1)
        self.assertEqual(after, events, "one event, and no memory.retracted")
        with self.assertRaises(AuthError) as ctx:
            s.authenticate(f"Bearer {w.keys['log-1']}")
        self.assertEqual(ctx.exception.status, 403, "the key is refused at once")
        self.assertEqual([(a["target"], a["detail"], a["remote"]) for a in audits(st, "agent.remove")],
                         [("log-1", {"org_id": ORG, "active_notes": 3}, "10.0.0.1")])

        created = await apply_through(s, w.log, "agent.removed")
        self.assertNoFlapping(st, created)
        self.assertEqual({m.memory_id: (m.status, m.metadata.get("status_reason")) for m in notes_of(st, "log-1")},
                         dict.fromkeys([ids["log-1"], *extra], ("retracted", "agent removed")))
        [applied] = audits(st, "agent.removed")
        self.assertEqual(applied["detail"], {"org_id": ORG, "retracted": 3, "derivations": len(created)})
        self.assertEqual(st.agent_log_status("log-1"), "removed")
        await w.settle()
        gone = {m.memory_id for m in notes_of(st, "log-1")}
        for m in st.list_memories(ORG, status="active", limit=10_000):
            self.assertFalse(gone & set(m.metadata.get("roots") or []), m.memory_id)
        self.assertEqual(broken_chains(st, ORG), [])
        self.assertEqual(invariant_violations(s, ORG), [])
        self.assertEqual(st.get_memory(old).status, "retracted")
        replacement = st.current_derived(ORG, "slot_composition", ORG, st.get_memory(old).metadata["agg_key"])
        self.assertIn(ids["log-2"], replacement.metadata["roots"], "the conclusion rests on log-2's note now")
        r = await s.verify(w.admin, old)
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        self.assertEqual(codes(r, old), ["node_retracted"])
        self.assertEqual(codes(r, ids["log-1"]), ["node_retracted"])
        self.assertNotIn("status_inconsistent", json.dumps(r))
        self.assertEqual(r["current_version"], replacement.memory_id)
        r = await s.verify(w.admin, replacement.memory_id)
        self.assertEqual((r["verdict"], r["reasons"]), ("verified", []))
        for mid in gone:
            self.assertEqual((await s.verify(w.admin, mid))["verdict"], "stale")
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
            self.assertEqual(rebuilt.store.agent_log_status("log-1"), "removed")
            self.assertEqual((await rebuilt.verify(w.admin, old))["report_digest"], (await s.verify(w.admin, old))["report_digest"])
        finally:
            await rebuilt.store.close()

    async def test_remove_agent_twice_and_unknown(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, st = w.service, w.store
        self.assertIsNone(await s.revoke_agent("ghost", retract=True))
        self.assertIsNone(await s.revoke_agent("ghost"))
        self.assertEqual(await s.apply_event(wire("agent.removed", {"agent_id": "ghost", "org_id": ORG})), "applied")
        # repeated: one event each; the first apply retracts, the next ones change nothing
        first = await s.revoke_agent("sales-1", retract=True)
        self.assertEqual(first["retracted"], 1)
        await w.settle()
        settled = (table_dump(st, "memories"), table_dump(st, "lineage_edges"))
        self.assertEqual(await s.revoke_agent("sales-1", retract=True), {"agent_id": "sales-1", "revoked": True, "retracted": 0})
        both = await asyncio.gather(s.revoke_agent("sales-1", retract=True), s.revoke_agent("sales-1", retract=True))
        self.assertEqual([b["retracted"] for b in both], [0, 0])
        await w.settle()
        self.assertEqual(len(st.removal_events(ORG, ["sales-1"])["sales-1"]), 4)
        self.assertEqual((table_dump(st, "memories"), table_dump(st, "lineage_edges")), settled)
        # a plain revocation after a removal: 'removed' is terminal
        self.assertEqual(await s.revoke_agent("sales-1"), {"agent_id": "sales-1", "revoked": True})
        await w.settle()
        self.assertEqual(st.agent_log_status("sales-1"), "removed")
        self.assertEqual(st.get_agent("sales-1").status, "revoked")
        # a registration replayed after the removal does not revive it either
        replayed = wire("agent.registered", {**st.get_agent("sales-1").to_dict(), "status": "active", "key_hash": "x"})
        self.assertEqual(await s.apply_event(replayed), "duplicate")
        self.assertEqual(st.agent_log_status("sales-1"), "removed")
        # a revoked agent removed later: its notes are retracted, and the registry it already left does not change
        await s.revoke_agent("proc-1")
        await w.settle()
        self.assertEqual(st.get_memory(ids["proc-1"]).status, "active", "a revoked agent's notes stay evidence")
        before = s.aggregator.registry_counts(ORG, st.get_agent("proc-1").path)
        await s.revoke_agent("proc-1", retract=True)
        await w.settle()
        self.assertEqual(st.agent_log_status("proc-1"), "removed")
        self.assertEqual(s.aggregator.registry_counts(ORG, st.get_agent("proc-1").path), before)
        self.assertEqual((st.get_memory(ids["proc-1"]).status, st.get_memory(ids["proc-1"]).metadata["status_reason"]),
                         ("retracted", "agent removed"))
        self.assertEqual(st.get_memory(ids["conclusion"]).status, "retracted")
        self.assertEqual(invariant_violations(s, ORG), [])
        # a removal whose payload is not JSON, or names no agent, is skipped by verification's lookup
        st._conn.execute("INSERT INTO events(event_id, kind, org_id, subject, payload, status, created_at) VALUES "
                         "('evt_bad_r1', 'agent.removed', ?, 'x', '{not json', 'applied', ?), "
                         "('evt_bad_r2', 'agent.removed', ?, 'x', '{\"agent_id\": 5}', 'pending', ?)", (ORG, now_iso()) * 2)
        self.assertEqual(set(st.removal_events(ORG, ["sales-1", "proc-1", "log-1"])), {"sales-1", "proc-1"})
        self.assertEqual([status for _, status in st.removal_events(ORG, ["sales-1"])["sales-1"]], ["applied"] * 4)
        for mid in (ids["log-1"], ids["team"]):
            self.assertEqual((await s.verify(w.admin, mid))["verdict"], "verified", mid)

    async def test_removed_agent_cannot_write_and_late_notes_apply_retracted(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        stale = w.principal("log-2")                    # authenticated before the revocation commits
        await s.revoke_agent("log-2")
        events, memories = kinds(st), len(table_dump(st, "memories"))
        with self.assertRaises(Forbidden) as ctx:
            await s.ingest_memory(stale, {"text": "written after the revocation", "topic": TRANSPORT})
        self.assertEqual(str(ctx.exception), "agent revoked")
        with self.assertRaises(Forbidden):
            await s.ingest_events(stale, [{"type": "x", "memory": {"text": "embedded after the revocation"}}])
        with self.assertRaises(Forbidden):
            await s.ingest_memory(stale, {"text": "a resend", "idempotency_key": "k1"})
        self.assertEqual((kinds(st), len(table_dump(st, "memories"))), (events, memories), "nothing was written")
        await s.revoke_agent("log-1", retract=True)
        await w.settle()
        # a note on the stream after the removal (a write that raced it, or an injected event): applied retracted
        event = observed(st, "log-1", "mem_late_1", "Strike extended to week 44.", topic=TRANSPORT, slot="transport_disruption",
                         entity="sd-9", confidence=0.99)
        derived = {m.memory_id for m in st.list_memories(ORG, status=None, limit=10_000) if m.operator != "agent_observation"}
        w.log.append(event)
        self.assertEqual(await s.apply_event(event, seq=len(w.log)), "applied")
        m = st.get_memory("mem_late_1")
        self.assertEqual((m.status, m.metadata.get("status_reason"), m.applied_at is not None), ("retracted", "agent removed", True))
        self.assertEqual({m.memory_id for m in st.list_memories(ORG, status=None, limit=10_000) if m.operator != "agent_observation"},
                         derived, "never offered upward")
        self.assertEqual([(a["target"], a["detail"]) for a in audits(st, "memory.observed_after_removal")],
                         [("mem_late_1", {"org_id": ORG, "agent_id": "log-1"})])
        r = await s.verify(w.admin, "mem_late_1")
        self.assertEqual((r["verdict"], codes(r, "mem_late_1")), ("stale", ["node_retracted"]))
        # a status flip is still caught: an active note of a removed producer, a retracted one with nothing applied
        r = w.tampered(ids["log-1"], [("UPDATE memories SET status='active' WHERE memory_id=?", (ids["log-1"],))])
        self.assertEqual((r["verdict"], codes(r, ids["log-1"])), ("failed", ["status_inconsistent"]))
        r = w.tampered(ids["log-1"], [("UPDATE events SET status='failed' WHERE kind='agent.removed'", ())])
        self.assertEqual((r["verdict"], codes(r, ids["log-1"])), ("failed", ["status_inconsistent", "node_retracted"]))
        r = w.tampered(ids["log-1"], [("UPDATE events SET status='pending' WHERE kind='agent.removed'", ())])
        self.assertEqual((r["verdict"], codes(r, ids["log-1"])),
                         ("failed", ["status_inconsistent", "node_retracted", "retraction_pending"]),
                         "retracted while the only event that retracts it is still pending")
        r = w.tampered(ids["sales-1"], [("INSERT INTO events(event_id, kind, org_id, subject, payload, status, created_at) "
                                         "VALUES ('evt_rm', 'agent.removed', ?, 'x', json_object('agent_id', 'sales-1'), "
                                         "'published', ?)", (ORG, now_iso()))])
        self.assertEqual((r["verdict"], codes(r, ids["sales-1"])), ("stale", ["retraction_pending"]), "a removal in flight")
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
            self.assertEqual(rebuilt.store.get_memory("mem_late_1").status, "retracted")
        finally:
            await rebuilt.store.close()

    async def test_removing_the_only_team_member_replans_promotions(self) -> None:
        w = await self.world(key=K)
        s, st = w.service, w.store
        for agent_id, team in (("log-1", "logistics"), ("log-2", "logistics"), ("proc-1", "procurement")):
            await w.register(agent_id, team=team)
        await w.settle()
        for agent_id in ("log-1", "log-2", "proc-1"):
            await w.observe(agent_id, f"Dock 4 is closed ({agent_id}).", topic="ops:dock", visibility="org")
        await w.settle()
        by_layer = {m.layer: m for m in st.list_memories(ORG, topic="ops:dock", limit=100) if m.operator == "topic_consolidation"}
        dept = by_layer["department"]
        self.assertIsNone(dept.metadata["promoted_from"], "two teams contribute")
        self.assertEqual(st.get_memory(by_layer["subsidiary"].memory_id).metadata["promoted_from"], dept.memory_id)
        await s.revoke_agent("proc-1", retract=True)
        created = await apply_through(s, w.log, "agent.removed")
        self.assertNoFlapping(st, created)
        now = {m.layer: m for m in st.list_memories(ORG, topic="ops:dock", limit=100) if m.operator == "topic_consolidation"}
        self.assertEqual(now["team"].memory_id, by_layer["team"].memory_id)
        self.assertEqual(now["department"].metadata["promoted_from"], by_layer["team"].memory_id,
                         "the department promotes the remaining team in the same apply")
        for upper, lower in (("subsidiary", "department"), ("region", "subsidiary"), ("enterprise", "region")):
            self.assertEqual(now[upper].metadata["promoted_from"], now[lower].memory_id)
        self.assertEqual({now[layer].memory_id for layer in ("department", "subsidiary", "region", "enterprise")}, created)
        self.assertEqual(st.get_memory(dept.memory_id).status, "retracted")
        await w.settle()
        self.assertEqual(invariant_violations(s, ORG), [])
        for m in st.list_memories(ORG, status="active", limit=1000):
            self.assertEqual((await s.verify(w.admin, m.memory_id))["verdict"], "verified", m.memory_id)
        r = await s.verify(w.admin, dept.memory_id)
        self.assertEqual((r["verdict"], r["current_version"]), ("stale", now["department"].memory_id))
        self.assertNotIn("status_inconsistent", json.dumps(r))
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()

    async def test_removal_unblocks_a_conclusion_its_notes_did_not_support(self) -> None:
        """A removed agent's note can keep a conclusion from holding without being part of it: the strongest candidate
        for a slot, from the team that already fills the other one.  The removal composes the rules on each retracted
        note's slot, as a single retraction does, so the next candidate fills the slot in the removal's own apply."""
        w = await self.world(key=K)
        s, st = w.service, w.store
        await s.upsert_rule({"rule_id": "two-teams", "target_layer": "department", "required_slots": ["s", "y"],
                             "min_agents": 2, "min_teams": 2, "conclusion": "Two teams on {entity}: {slot:s}"})
        for agent_id, team in (("log-1", "logistics"), ("log-2", "logistics"), ("proc-1", "procurement")):
            await w.register(agent_id, team=team)
        await w.settle()
        await w.observe("log-1", "s at e, strongly", topic="supply:a", slot="s", entity="e", confidence=0.95)
        y = await w.observe("log-2", "y at e", topic="supply:b", slot="y", entity="e", confidence=0.9)
        weak = await w.observe("proc-1", "s at e, weakly", topic="supply:c", slot="s", entity="e", confidence=0.5)
        await w.settle()

        def conclusions() -> list[Memory]:
            return [m for m in st.list_memories(ORG, status=None, limit=1000) if m.rule_id == "two-teams"]

        self.assertEqual(conclusions(), [], "log-1's note is selected for s: one team where the rule needs two")
        await s.revoke_agent("log-1", retract=True)
        created = await apply_through(s, w.log, "agent.removed")
        [c] = conclusions()
        self.assertIn(c.memory_id, created, "it holds in the removal's own apply")
        self.assertEqual({e.parent_id for e in st.parents_of(c.memory_id)}, {y, weak})
        self.assertEqual((c.scope, c.entity, c.support, c.independent_teams), ("northwind/emea/nw-gmbh/ops", "e", 2, 2))
        await w.settle()
        self.assertEqual(invariant_violations(s, ORG), [])
        self.assertEqual((await s.verify(w.admin, c.memory_id))["verdict"], "verified")
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()

    async def test_removal_during_a_replay_is_applied_after_it(self) -> None:
        transport = HeldTransport(hold=False)
        h = await ServiceHarness(transport=transport, event_signing_key=K).start()
        self.addAsyncCleanup(h.close)
        ids = await demo(h)
        s, st = h.service, h.service.store
        transport.hold = True
        target = (await s.replay())["target_seq"]
        res = await s.revoke_agent("log-1", retract=True)
        self.assertEqual(res["retracted"], 1, "the API answers at once")
        await drain_outbox(s)
        [removal] = st.list_events(ORG, kind="agent.removed")
        self.assertGreater(removal.js_seq, target, "after the replay's target: applied once the replay is done")
        self.assertEqual(st.get_memory(ids["log-1"]).status, "active")
        transport.hold = False
        await h.settle()
        self.assertEqual(st.get_event(removal.event_id).status, "applied")
        self.assertEqual(st.get_memory(ids["log-1"]).status, "retracted")
        derived = [m for m in st.list_memories(ORG, status="active", limit=1000) if m.operator != "agent_observation"]
        self.assertTrue(derived)
        for m in derived:
            ev = st.get_event(m.event_id)
            self.assertEqual(ev.status, "applied", "its derived events were queued, published and applied")
            self.assertGreater(ev.js_seq, removal.js_seq)
        self.assertEqual(st._conn.execute("SELECT COUNT(*) FROM events WHERE js_seq IS NULL").fetchone()[0], 0)
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_moving_an_agent_reflows_contributions(self) -> None:
        h = await ServiceHarness(event_signing_key=K).start()
        self.addAsyncCleanup(h.close)
        server = TestServer(create_app(h.service), host="127.0.0.1")
        await server.start_server()
        self.addAsyncCleanup(server.close)
        url = str(server.make_url("")).rstrip("/")
        s, st = h.service, h.service.store
        await h.register("mover", team="team-a")
        await h.register("a-1", team="team-a")
        await h.register("b-1", team="team-b")
        await h.settle()
        local_db = os.path.join(h.tmp.name, "mover-local.db")

        def share_all(key: str) -> list[dict[str, Any]]:
            """The agent's local notes (written once), each shared with ``key``: what the agent's own process does."""
            local = LocalMemory(local_db)
            try:
                for n in (1, 2):
                    local.note(f"Dock {n} is closed.", topic="ops:docks", local_id=f"note-{n}")
                client = MycelicClient(url, key, retries=0)
                return [local.share(client, f"note-{n}", visibility="org") for n in (1, 2)]
            finally:
                local.close()

        shared = await asyncio.to_thread(share_all, h.keys["mover"])
        await h.observe("a-1", "Dock 1 is closed, seen from team a.", topic="ops:docks", visibility="org")
        await h.observe("b-1", "Dock 2 is closed, seen from team b.", topic="ops:docks", visibility="org")
        await h.settle()

        def dept() -> Any:
            return st.current_derived(ORG, "topic_consolidation", "northwind/emea/nw-gmbh/ops", "ops:docks")

        self.assertEqual(dept().metadata["contributing_agents"], ["a-1", "b-1", "mover"])
        # the move: a new id at the new path, the same local notes re-shared under its key (new memory ids)
        await h.register("mover-2", team="team-b")
        await h.settle()
        reshared = await asyncio.to_thread(share_all, h.keys["mover-2"])
        self.assertFalse({r["memory_id"] for r in reshared} & {r["memory_id"] for r in shared})
        await h.settle()
        self.assertEqual(dept().metadata["contributing_agents"], ["a-1", "b-1", "mover", "mover-2"],
                         "while both ids are active, support counts both")
        res = await asyncio.to_thread(MycelicClient(url, ADMIN_TOKEN, retries=0).revoke_agent, "mover", retract=True)
        self.assertEqual(res, {"agent_id": "mover", "revoked": True, "retracted": 2})
        await h.settle()
        self.assertEqual(dept().metadata["contributing_agents"], ["a-1", "b-1", "mover-2"])
        self.assertEqual(dept().support, 3)
        self.assertIsNone(st.current_derived(ORG, "topic_consolidation", "northwind/emea/nw-gmbh/ops/team-a", "ops:docks"),
                          "team a is down to one agent")
        self.assertEqual(sorted(st.current_derived(ORG, "topic_consolidation", "northwind/emea/nw-gmbh/ops/team-b",
                                                   "ops:docks").metadata["contributing_agents"]), ["b-1", "mover-2"])
        self.assertEqual(invariant_violations(s, ORG), [])
        for m in st.list_memories(ORG, status="active", limit=1000):
            self.assertEqual((await s.verify(h.admin, m.memory_id))["verdict"], "verified", m.memory_id)


class SurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.transport = HeldTransport(hold=False)          # hold: events are published but not applied
        self.h = await ServiceHarness(transport=self.transport).start()
        self.client = TestClient(TestServer(create_app(self.h.service)))
        await self.client.start_server()
        self.url = str(self.client.make_url("")).rstrip("/")
        self.admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        for agent_id in ("a-1", "a-2", "a-3", "a-4", "a-5"):
            await self.h.register(agent_id, team="logistics")
        await self.h.settle()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.h.close()

    async def cli(self, *argv: str) -> tuple[int, list[str], str]:
        out, err = io.StringIO(), io.StringIO()

        def run() -> int:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                return cli.main(list(argv))

        with mock.patch.dict(os.environ):
            os.environ.pop("MYCELIC_ADMIN_TOKEN", None)
            code = await asyncio.to_thread(run)
        return code, out.getvalue().splitlines(), err.getvalue()

    async def test_remove_agent_surfaces_http_sdk_cli(self) -> None:
        st = self.h.service.store
        for agent_id in ("a-1", "a-2", "a-3"):
            await self.h.observe(agent_id, f"Note by {agent_id}.", topic="ops:x")
        await self.h.settle()
        # without retract: exactly the response of before
        r = await self.client.delete("/admin/agents/a-4", headers=self.admin)
        self.assertEqual((r.status, await r.text()), (200, '{"agent_id": "a-4", "revoked": true}'))
        for flag in ("0", "false", ""):
            r = await self.client.delete(f"/admin/agents/a-4?retract={flag}", headers=self.admin)
            self.assertEqual((r.status, await r.json()), (200, {"agent_id": "a-4", "revoked": True}))
        self.assertEqual(kinds(st).get("agent.removed"), None)
        for flag in ("2", "yes", "TRUE", "1.0"):
            with self.subTest(retract=flag):
                r = await self.client.delete(f"/admin/agents/a-1?retract={flag}", headers=self.admin)
                self.assertEqual((r.status, await r.json()), (400, {"error": "'retract' must be 1 or 0"}))
        r = await self.client.delete("/admin/agents/ghost?retract=1", headers=self.admin)
        self.assertEqual((r.status, await r.json()), (404, {"agent_id": "ghost", "revoked": False}))
        r = await self.client.delete("/admin/agents/a-1?retract=1", headers={"Authorization": f"Bearer {self.h.keys['a-2']}"})
        self.assertEqual(r.status, 403)
        r = await self.client.delete("/admin/agents/a-1?retract=1", headers=self.admin)
        self.assertEqual((r.status, await r.json()), (200, {"agent_id": "a-1", "revoked": True, "retracted": 1}))
        r = await self.client.get("/whoami", headers={"Authorization": f"Bearer {self.h.keys['a-1']}"})
        self.assertEqual(r.status, 403)
        r = await self.client.delete("/admin/agents/a-5?retract=true", headers=self.admin)
        self.assertEqual((r.status, await r.json()), (200, {"agent_id": "a-5", "revoked": True, "retracted": 0}))
        await self.h.settle()
        # the SDK sends the flag only when asked
        client = MycelicClient(self.url, ADMIN_TOKEN, retries=0)
        with mock.patch.object(client, "_request", wraps=client._request) as spy:
            self.assertEqual(await asyncio.to_thread(client.revoke_agent, "a-2", retract=True),
                             {"agent_id": "a-2", "revoked": True, "retracted": 1})
            self.assertEqual(await asyncio.to_thread(client.revoke_agent, "a-4"), {"agent_id": "a-4", "revoked": True})
        self.assertEqual([c.kwargs["params"] for c in spy.call_args_list], [{"retract": 1}, None])
        with self.assertRaises(MycelicError) as ctx:
            await asyncio.to_thread(client.revoke_agent, "ghost", retract=True)
        self.assertEqual(ctx.exception.status, 404)
        # the CLI
        base = ("--url", self.url, "--admin-token", ADMIN_TOKEN)
        code, out, _ = await self.cli("revoke-agent", "--agent-id", "a-3", "--retract", *base)
        self.assertEqual((code, out), (0, ["revoked a-3; 1 note will be retracted"]))
        code, out, _ = await self.cli("revoke-agent", "--agent-id", "a-4", *base)
        self.assertEqual((code, out), (0, ["revoked a-4"]))
        code, out, _ = await self.cli("revoke-agent", "--agent-id", "a-4", "--retract", "--json", *base)
        self.assertEqual((code, json.loads("\n".join(out))), (0, {"agent_id": "a-4", "revoked": True, "retracted": 0}))
        code, out, err = await self.cli("revoke-agent", "--agent-id", "ghost", "--retract", *base)
        self.assertEqual((code, out), (1, []))
        self.assertIn("HTTP 404", err)
        with self.assertRaises(SystemExit):
            await self.cli("revoke-agent", "--agent-id", "a-4", "--url", self.url)
        await self.h.settle()
        self.assertEqual({a: st.agent_log_status(a) for a in ("a-1", "a-2", "a-3", "a-4", "a-5")},
                         {"a-1": "removed", "a-2": "removed", "a-3": "removed", "a-4": "removed", "a-5": "removed"})
        self.assertEqual([m.status for m in st.list_memories(ORG, status=None, operator="agent_observation", limit=100)],
                         ["retracted"] * 3)
        self.assertEqual(st.list_memories(ORG, status="active", limit=100), [])

    async def rpc(self, key: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                   headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        self.assertEqual(r.status, 200)
        return (await r.json())["result"]

    async def test_update_and_expiry_surfaces_http_mcp_sdk(self) -> None:
        st = self.h.service.store
        headers = {"Authorization": f"Bearer {self.h.keys['a-1']}"}
        at = iso(utcnow() + timedelta(hours=1))
        year = utcnow().year + 2
        r = await self.client.post("/memory", json={"text": "HTTP note on dock 4.", "topic": "ops:x",
                                                    "expires_at": f"{year}-06-01T12:00:00+02:00"}, headers=headers)
        self.assertEqual(r.status, 202)
        mid = (await r.json())["memory_id"]
        memory = (await (await self.client.get(f"/memory/{mid}", headers=headers)).json())["memory"]
        self.assertEqual((memory["expires_at"], memory["attested_at"]), (f"{year}-06-01T10:00:00+00:00", None))
        for value, message in (("yesterday", "'expires_at' must be an ISO-8601 timestamp"),
                               ("2020-01-01", "'expires_at' must be in the future"), (5, "'expires_at' must be a string")):
            r = await self.client.post("/memory", json={"text": "x", "expires_at": value}, headers=headers)
            self.assertEqual((r.status, await r.json()), (400, {"error": message}))
        r = await self.client.post("/events", json={"events": [{"type": "t", "memory": {"text": "embedded", "expires_at": at}}]},
                                   headers=headers)
        self.assertEqual(r.status, 202)
        self.assertEqual(st.get_memory((await r.json())["results"][0]["memory_id"]).expires_at, at)
        # MCP over HTTP
        key = self.h.keys["a-2"]
        await self.rpc(key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        tools = {t["name"]: t for t in (await self.rpc(key, "tools/list", {}))["tools"]}
        prop = tools["mycelic_remember"]["inputSchema"]["properties"]["expires_at"]
        self.assertEqual(prop["type"], "string")
        for phrase in ("answers leave it out", "MYCELIC_EXPIRY_SWEEP_SECONDS", "still counts in consolidations", "leaf_expired"):
            self.assertIn(phrase, prop["description"])
        out = await self.rpc(key, "tools/call", {"name": "mycelic_remember", "arguments": {"text": "MCP note on dock 4.",
                                                                                           "topic": "ops:x", "expires_at": at}})
        self.assertFalse(out["isError"], out)
        self.assertEqual(st.get_memory(out["structuredContent"]["memory_id"]).expires_at, at)
        out = await self.rpc(key, "tools/call", {"name": "mycelic_remember", "arguments": {"text": "x", "expires_at": "soon"}})
        self.assertTrue(out["isError"])
        self.assertIn("'expires_at' must be an ISO-8601 timestamp", out["content"][0]["text"])
        # the SDK and the agent's local store
        client = MycelicClient(self.url, self.h.keys["a-3"], retries=0)
        res = await asyncio.to_thread(client.remember, "SDK note on dock 4.", topic="ops:x", expires_at=at)
        self.assertEqual(st.get_memory(res["memory_id"]).expires_at, at)
        local_db = os.path.join(self.h.tmp.name, "a-3-local.db")

        def share() -> dict[str, Any]:
            local = LocalMemory(local_db)
            try:
                return local.share(client, local.note("Local note on dock 4.", topic="ops:x"), expires_at=f"{year + 1}-01-01")
            finally:
                local.close()

        shared = await asyncio.to_thread(share)
        self.assertEqual(st.get_memory(shared["memory_id"]).expires_at, f"{year + 1}-01-01T00:00:00+00:00")
        # the CLI's verification says until when its answer holds
        await self.h.settle()
        team = st.current_derived(ORG, "topic_consolidation", "northwind/emea/nw-gmbh/ops/logistics", "ops:x")
        code, out, _ = await self.cli("verify", team.memory_id, "--url", self.url, "--admin-token", ADMIN_TOKEN)
        self.assertEqual(code, 0)
        self.assertIn(f"valid until {at}", out)
        code, out, _ = await self.cli("verify", shared["memory_id"], "--url", self.url, "--admin-token", ADMIN_TOKEN)
        self.assertEqual((code, [line for line in out if line.startswith("valid until")]), (0, [f"valid until {year + 1}-01-01T00:00:00+00:00"]))

        # updates over HTTP: the response names what the update replaces; 409 while one is on its way, 404, 403, 400
        r = await self.client.post("/memory", json={"text": "HTTP note on dock 4, corrected.", "topic": "ops:x",
                                                    "supersedes": mid}, headers=headers)
        self.assertEqual(r.status, 202)
        update = await r.json()
        self.assertEqual((update["supersedes"], update["created"]), (mid, True))
        self.assertNotIn("supersedes", (await (await self.client.post("/memory", json={"text": "plain"}, headers=headers)).json()))
        self.transport.hold = True
        r = await self.client.post("/memory", json={"text": "A second correction.", "supersedes": update["memory_id"]},
                                   headers=headers)
        self.assertEqual(r.status, 202)
        held = await r.json()
        await drain_outbox(self.h.service)
        r = await self.client.post("/memory", json={"text": "A third correction.", "supersedes": update["memory_id"]},
                                   headers=headers)
        self.assertEqual((r.status, await r.json()), (409, {"error": "a retraction or another update of this memory is waiting "
                                                                    "to be applied; retry once it has been applied"}))
        # the same over MCP, as an error result
        out = await self.rpc(self.h.keys["a-1"], "tools/call", {"name": "mycelic_remember", "arguments": {
            "text": "An MCP correction.", "supersedes": update["memory_id"]}})
        self.assertTrue(out["isError"])
        self.assertIn("waiting to be applied", out["content"][0]["text"])
        self.transport.hold = False
        await self.h.settle()
        self.assertEqual(st.get_memory(update["memory_id"]).superseded_by, held["memory_id"])
        for body, status, message in (({"supersedes": "mem_nope"}, 404, "no visible memory with id mem_nope"),
                                      ({"supersedes": shared["memory_id"]}, 403, "only the producing agent can update a memory"),
                                      ({"supersedes": update["memory_id"]}, 400, "'supersedes' names a memory that is not active")):
            r = await self.client.post("/memory", json={"text": "x", **body}, headers=headers)
            self.assertEqual(r.status, status)
            self.assertTrue((await r.json())["error"].startswith(message))
        r = await self.client.post("/events", json={"events": [{"type": "t", "memory": {"text": "x", "supersedes": mid}}]},
                                   headers=headers)
        self.assertEqual((r.status, await r.json()), (400, {"error": "'supersedes' is accepted by POST /memory only"}))
        # MCP, the SDK, the agent's local store and the stdio proxy
        prop = tools["mycelic_remember"]["inputSchema"]["properties"]["supersedes"]
        for phrase in ("your own active memories", "nothing is inherited", "new idempotency_key"):
            self.assertIn(phrase, prop["description"])
        out = await self.rpc(key, "tools/call", {"name": "mycelic_remember", "arguments": {"text": "MCP note on dock 4, corrected.",
                                                                                           "topic": "ops:x", "supersedes": next(
            m.memory_id for m in notes_of(st, "a-2") if m.status == "active")}})
        self.assertFalse(out["isError"], out)
        self.assertIn("supersedes", out["structuredContent"])
        res = await asyncio.to_thread(client.remember, "SDK note on dock 4, corrected.", topic="ops:x",
                                      supersedes=next(m.memory_id for m in notes_of(st, "a-3") if m.status == "active"
                                                      and m.local_ref is None))
        self.assertEqual(st.get_memory(res["memory_id"]).metadata["version_of"], res["supersedes"])
        await self.h.settle()

        def correct() -> dict[str, Any]:
            local = LocalMemory(local_db)
            try:
                return local.share(client, local.note("Local note on dock 4, corrected.", topic="ops:x"),
                                   supersedes=shared["memory_id"])
            finally:
                local.close()

        corrected = await asyncio.to_thread(correct)
        self.assertEqual(corrected["supersedes"], shared["memory_id"])
        proxy = mcp.ProxyTools(MycelicClient(self.url, self.h.keys["a-4"], retries=0))
        first = await proxy.call("mycelic_remember", {"text": "Proxy note.", "topic": "ops:x"})
        await self.h.settle()
        second = await proxy.call("mycelic_remember", {"text": "Proxy note, corrected.", "topic": "ops:x",
                                                       "supersedes": first["memory_id"]})
        self.assertEqual(second["supersedes"], first["memory_id"])
        await self.h.settle()
        for m in (shared, first):
            self.assertEqual(st.get_memory(m["memory_id"]).status, "superseded")
        self.assertEqual(invariant_violations(self.h.service, ORG), [])


class ExpiryTests(unittest.IsolatedAsyncioTestCase):
    def tmpdir(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    async def world(self, **kwargs: Any) -> World:
        w = World(self.tmpdir(), **kwargs)
        self.addAsyncCleanup(w.close)
        await w.service.transport.connect()
        return w

    async def test_expiry_sweeper_retracts_through_the_log_idempotently(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        t0 = utcnow()
        at = iso(t0 + timedelta(seconds=2))
        mid = await w.observe("log-2", "Terminal 3 works weekend shifts only.", topic=TRANSPORT, slot="transport_disruption",
                              entity="sd-9", confidence=0.95, expires_at=at)
        await w.settle()
        team_scope, key = st.get_memory(ids["team"]).scope, st.get_memory(ids["conclusion"]).metadata["agg_key"]
        self.assertIn(mid, st.current_derived(ORG, "topic_consolidation", team_scope, TRANSPORT).metadata["roots"])
        self.assertIn(mid, st.current_derived(ORG, "slot_composition", ORG, key).metadata["roots"])
        self.assertEqual(await s.sweep_expired(now=t0), 0, "not expired yet")
        counted, audited = s.metrics.memories_expired._value.get(), len(audits(st, "memory.expired"))
        later = t0 + timedelta(seconds=3)
        # a replay re-delivers what earlier sweeps queued: no sweep runs during one
        s._replay_target = 10**9
        self.assertEqual(await s.sweep_expired(now=later), 0)
        s._replay_target = None
        self.assertEqual(st.list_events(ORG, kind="memory.retracted"), [])
        self.assertEqual(await s.sweep_expired(now=later), 1)
        self.assertEqual(await s.sweep_expired(now=later), 0, "selected again while its retraction waits: no second event")
        [ev] = st.list_events(ORG, kind="memory.retracted")
        self.assertEqual(ev.event_id, f"evt_x{content_hash(mid, 'expired')[:22]}")
        self.assertEqual((ev.payload, ev.agent_id), ({"memory_id": mid, "reason": "expired", "by": "mycelic", "expires_at": at}, None))
        self.assertEqual(s.metrics.memories_expired._value.get(), counted + 1)
        self.assertEqual([(a["target"], a["detail"]) for a in audits(st, "memory.expired")][: len(audits(st, "memory.expired")) - audited],
                         [(mid, {"org_id": ORG, "expires_at": at, "event_id": ev.event_id})])
        await w.settle()
        m = st.get_memory(mid)
        self.assertEqual((m.status, m.metadata["status_reason"]), ("retracted", "expired"))
        self.assertEqual(await s.sweep_expired(now=later), 0, "retracted: never selected again")
        # withdrawn from the consolidation and the conclusion: the coalitions without it are current again
        self.assertEqual(st.current_derived(ORG, "topic_consolidation", team_scope, TRANSPORT).memory_id, ids["team"])
        self.assertEqual(st.current_derived(ORG, "slot_composition", ORG, key).memory_id, ids["conclusion"])
        self.assertEqual(invariant_violations(s, ORG), [])
        self.assertEqual(broken_chains(st, ORG), [])
        self.assertEqual((await s.verify(w.admin, ids["conclusion"]))["verdict"], "verified")
        r = await s.verify(w.admin, mid)
        self.assertEqual((r["verdict"], codes(r, mid)), ("stale", ["node_retracted"]))
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)          # never started: no sweeper
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
            self.assertEqual(rebuilt.store.get_memory(mid).expires_at, at)
            self.assertEqual(await rebuilt.sweep_expired(now=later), 0, "the rebuilt note is retracted already")
        finally:
            await rebuilt.store.close()

        # the background loop does the same in real time; with an interval of 0 there is none
        h = await ServiceHarness(expiry_sweep_seconds=0.2).start()
        self.addAsyncCleanup(h.close)
        self.assertIn("mycelic-expiry", [t.get_name() for t in h.service._tasks])
        await h.register("a-1", team="t1")
        await h.settle()
        live = await h.observe("a-1", "A short-lived note.", topic="ops:x", expires_at=iso(utcnow() + timedelta(seconds=2)))
        deadline = time.monotonic() + 15
        while h.service.store.get_memory(live).status == "active":
            self.assertLess(time.monotonic(), deadline, "the loop did not retract the expired note")
            await asyncio.sleep(0.1)
        self.assertEqual(h.service.store.get_memory(live).metadata["status_reason"], "expired")
        expiry = (await h.service.health())["checks"]["expiry"]
        self.assertEqual((expiry["enabled"], expiry["interval_seconds"]), (True, 0.2))
        self.assertIsNotNone(expiry["last_sweep_at"])
        off = await ServiceHarness(expiry_sweep_seconds=0).start()
        self.addAsyncCleanup(off.close)
        self.assertNotIn("mycelic-expiry", [t.get_name() for t in off.service._tasks])
        self.assertEqual((await off.service.health())["checks"]["expiry"]["enabled"], False)

    async def test_expiry_during_outage_dedups_and_cursor_progresses(self) -> None:
        w = await self.world()
        s, st = w.service, w.store
        for agent_id in ("a-1", "a-2"):
            await w.register(agent_id, team="t1")
        await w.settle()
        t0 = utcnow()
        mids = [await w.observe(f"a-{1 + n % 2}", f"note {n}", topic="ops:x", expires_at=iso(t0 + timedelta(seconds=10 + n)))
                for n in range(5)]
        await w.settle()
        # the broker is down (nothing is published or applied): every expired note stays active meanwhile
        s.expiry_batch = 2
        later = t0 + timedelta(minutes=1)
        counts, cursors = [], []
        for _ in range(7):
            counts.append(await s.sweep_expired(now=later))
            cursors.append(s._expiry_cursor)
        self.assertEqual(counts, [2, 2, 1, 0, 0, 0, 0])
        self.assertEqual([c is None for c in cursors], [False, False, True, False, False, True, False],
                         "the cursor moves past a full page whatever it queued, and wraps after a short one")
        events = st.list_events(ORG, kind="memory.retracted")
        self.assertEqual(sorted(e.payload["memory_id"] for e in events), sorted(mids), "every note exactly once")
        self.assertEqual({e.status for e in events}, {"pending"})
        self.assertEqual(len(audits(st, "memory.expired")), 5)
        await w.settle()
        self.assertEqual({st.get_memory(mid).status for mid in mids}, {"retracted"})
        self.assertEqual([await s.sweep_expired(now=later) for _ in range(2)], [0, 0])
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_expired_unswept_memory_is_hidden_and_stale(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        t0 = utcnow()
        t1, t2 = iso(t0 + timedelta(hours=1)), iso(t0 + timedelta(hours=2))
        x = await w.observe("log-2", "Terminal 3 works weekend shifts only.", topic=TRANSPORT, slot="transport_disruption",
                            entity="sd-9", confidence=0.95, expires_at=t1)
        y = await w.observe("proc-1", "Kessler's second plant has one week of SD-9 stock.", topic="supply:sd-9/supplier",
                            slot="supplier_buffer_low", entity="sd-9", confidence=0.99, visibility="org", expires_at=t2)
        await w.settle()
        key = st.get_memory(ids["conclusion"]).metadata["agg_key"]
        c = st.current_derived(ORG, "slot_composition", ORG, key).memory_id
        self.assertTrue({x, y} <= set(st.get_memory(c).metadata["roots"]))
        admin, sales, log1 = w.admin, w.principal("sales-2"), w.principal("log-1")
        r = await s.verify(admin, c)
        self.assertEqual((r["verdict"], r["valid_until"], r["valid_until_partial"]), ("verified", t1, False))
        r = await s.verify(sales, c)
        self.assertEqual((r["verdict"], r["valid_until"], r["valid_until_partial"]), ("verified", t2, True),
                         "only what the caller can read, and it says so")
        after1, after2 = t0 + timedelta(hours=1, seconds=1), t0 + timedelta(hours=2, seconds=1)
        r = await s.verify(admin, c, now=after1)
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        self.assertEqual((codes(r, x), detail(r, x, "leaf_expired")), (["leaf_expired"], {"expires_at": t1}))
        self.assertEqual(codes(r, y), [])
        r = await s.verify(sales, c, now=after1)
        self.assertEqual((r["verdict"], codes(r, x), view(r, x)["redacted"]), ("stale", ["hidden_stale"], True))
        self.assertNotIn(t1, json.dumps(r), "a hidden leaf's expiry is not shown")
        r = await s.verify(sales, c, now=after2)
        self.assertEqual((codes(r, x), codes(r, y), detail(r, y, "leaf_expired")), (["hidden_stale"], ["leaf_expired"], {"expires_at": t2}))
        r = await s.verify(log1, st.current_derived(ORG, "topic_consolidation", st.get_memory(ids["team"]).scope, TRANSPORT).memory_id,
                           now=after1)
        self.assertEqual((r["verdict"], codes(r, x)), ("stale", ["leaf_expired"]))
        self.assertEqual((await s.verify(admin, x, now=after1))["reasons"], [{"code": "leaf_expired", "severity": "S", "count": 1}])

        # answers leave it out at once, and their BM25 statistics ignore it
        query = {"query": "terminal weekend shifts strike", "scope": ORG}
        self.assertIn(x, [h.memory.memory_id for h in s.retriever.search(ORG, query["query"], visible=log1.can_read)])
        self.assertIn(x, [h["memory"]["memory_id"] for h in s.query(log1, query)["results"]])
        s.retriever.clock = lambda: iso(after1)
        res = s.query(log1, query)
        self.assertNotIn(x, [h["memory"]["memory_id"] for h in res["results"]])
        self.assertNotEqual((res["answer"] or {}).get("memory_id"), x)
        mcp = await mcp_call(s, log1, "mycelic_query", query=query["query"])
        self.assertNotIn(x, [h["memory_id"] for h in mcp["results"]])
        index = s.retriever._index(ORG)
        visible = [i for i, m in enumerate(index.rows) if log1.can_read(m) and m.memory_id != x]
        expected = dict(zip([index.rows[i].memory_id for i in visible],
                            BM25([index.docs[i] for i in visible]).get_scores(tokenize(query["query"]))))
        hits = s.retriever.search(ORG, query["query"], visible=log1.can_read, k=100)
        self.assertTrue(hits)
        for h in hits:
            self.assertAlmostEqual(h.bm25, expected[h.memory.memory_id], places=9)
        self.assertIn(y, [h["memory"]["memory_id"] for h in s.query(sales, {"query": "kessler plant stock", "scope": ORG})["results"]])
        s.retriever.clock = lambda: iso(after2)
        self.assertNotIn(y, [h["memory"]["memory_id"] for h in s.query(sales, {"query": "kessler plant stock", "scope": ORG})["results"]])
        s.retriever.clock = now_iso
        self.assertIn(y, [h["memory"]["memory_id"] for h in s.query(sales, {"query": "kessler plant stock", "scope": ORG})["results"]])

    async def test_expiry_validation_errors(self) -> None:
        w = await self.world()
        s, st = w.service, w.store
        await w.register("a-1", team="t1")
        await w.settle()
        p = w.principal("a-1")
        now = utcnow()
        year = now.year + 2
        refused = [("not a time", "must be an ISO-8601 timestamp"), (12345, "'expires_at' must be a string"),
                   ("2030-13-01", "must be an ISO-8601 timestamp"), ("99999-01-01T00:00:00", "must be an ISO-8601 timestamp"),
                   ("0001-01-01T00:00:00+01:00", "must be an ISO-8601 timestamp"), ("x" * 41, "longer than 40"),
                   ("2020-01-01T00:00:00Z", "must be in the future"), (iso(now), "must be in the future"),
                   (iso(now + timedelta(seconds=MAX_EXPIRY_SECONDS + 60)),
                    "'expires_at' must be at most 315360000 seconds (ten years) ahead")]
        for value, message in refused:
            with self.subTest(expires_at=value):
                with self.assertRaises(ValidationError) as ctx:
                    await s.ingest_memory(p, {"text": "t", "expires_at": value})
                self.assertIn(message, str(ctx.exception))
        self.assertEqual(st.list_memories(ORG, status=None, operator="agent_observation", limit=100), [])
        accepted = [(f"{year}-06-01T12:00:00+02:00", f"{year}-06-01T10:00:00+00:00"),
                    (f"{year}-06-01T12:00:00", f"{year}-06-01T12:00:00+00:00"), (f"{year}-06-01", f"{year}-06-01T00:00:00+00:00"),
                    (f"{year}-06-01T12:00:00.999Z", f"{year}-06-01T12:00:00+00:00"),
                    (f" {year}-06-01T12:00:00Z ", f"{year}-06-01T12:00:00+00:00"), ("", None), (None, None)]
        for n, (value, stored) in enumerate(accepted):
            with self.subTest(expires_at=value):
                m, created = await s.ingest_memory(p, {"text": f"t{n}", "expires_at": value})
                self.assertTrue(created)
                self.assertEqual((m.expires_at, st.get_memory(m.memory_id).expires_at), (stored, stored))
                self.assertEqual(st.get_event(m.event_id).payload["expires_at"], stored, "the log carries the stored value")
        m, _ = await s.ingest_memory(p, {"text": "in ten years", "expires_at": iso(now + timedelta(seconds=MAX_EXPIRY_SECONDS - 60))})
        self.assertIsNotNone(m.expires_at)
        # a resend after the expiry returns the stored note
        first, created = await s.ingest_memory(p, {"text": "soon", "idempotency_key": "k1", "expires_at": iso(now + timedelta(seconds=5))})
        self.assertTrue(created)
        with mock.patch("mycelic.service.utcnow", return_value=now + timedelta(minutes=5)):
            again, created = await s.ingest_memory(p, {"text": "soon", "idempotency_key": "k1",
                                                       "expires_at": iso(now + timedelta(seconds=5))})
        self.assertEqual((again.memory_id, created), (first.memory_id, False))
        # embedded memories of POST /events: one refused expiry refuses the whole batch
        events = len(st.list_events(ORG, limit=10_000))
        with self.assertRaises(ValidationError):
            await s.ingest_events(p, [{"type": "a", "memory": {"text": "fine", "expires_at": iso(now + timedelta(hours=1))}},
                                      {"type": "b", "memory": {"text": "stale", "expires_at": "2020-01-01"}}])
        self.assertEqual(len(st.list_events(ORG, limit=10_000)), events, "rolled back")
        [entry] = await s.ingest_events(p, [{"type": "a", "memory": {"text": "embedded", "expires_at": f"{year + 1}-01-01T05:00:00+05:00"}}])
        self.assertEqual(st.get_memory(entry["memory_id"]).expires_at, f"{year + 1}-01-01T00:00:00+00:00")
        # the log is authoritative: an offset is normalised when applied, an invalid value refuses the event
        agent = st.get_agent("a-1")
        base = {"org_id": ORG, "layer": "agent", "scope": agent.path, "text": "from the stream", "producer_id": "a-1"}
        await s.apply_event(wire("memory.observed", {**base, "memory_id": "mem_stream_1", "expires_at": f"{year + 1}-01-01T05:00:00+05:00"}))
        self.assertEqual(st.get_memory("mem_stream_1").expires_at, f"{year + 1}-01-01T00:00:00+00:00")
        for bad in ("soon", 5, "0001-01-01T00:00:00+01:00"):
            with self.subTest(payload=bad):
                with self.assertRaises(ValidationError) as ctx:
                    await s.apply_event(wire("memory.observed", {**base, "memory_id": "mem_stream_2", "expires_at": bad}))
                self.assertEqual(str(ctx.exception), "memory payload has an invalid 'expires_at'")
        self.assertIsNone(st.get_memory("mem_stream_2"))
        # verification compares the logged expiry as the apply stored it, so an offset in the log is no mismatch
        await s.apply_event(observed(st, "a-1", "mem_stream_3", "from the stream", expires_at=f"{year + 1}-01-01T05:00:00+05:00"))
        self.assertEqual(st.get_memory("mem_stream_3").expires_at, f"{year + 1}-01-01T00:00:00+00:00")
        report = w.engine("mem_stream_3")
        self.assertEqual((report["verdict"], codes(report, "mem_stream_3")), ("verified", []))

    async def test_expiry_status_metrics_and_audit(self) -> None:
        h = await ServiceHarness(expiry_sweep_seconds=0).start()
        self.addAsyncCleanup(h.close)
        client = TestClient(TestServer(create_app(h.service)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        s, st = h.service, h.service.store
        await h.register("a-1", team="t1")
        await h.settle()
        t0 = utcnow()
        mid = await h.observe("a-1", "Soon stale.", topic="ops:x", expires_at=iso(t0 + timedelta(seconds=30)))
        await h.settle()
        r = await (await client.get("/admin/status", headers=admin)).json()
        self.assertEqual(r["checks"]["expiry"], {"enabled": False, "interval_seconds": 0, "overdue": 0, "last_sweep_at": None,
                                                 "last_queued": None})
        later = t0 + timedelta(minutes=1)
        with mock.patch("mycelic.store.now_iso", return_value=iso(later)):
            r = await (await client.get("/admin/status", headers=admin)).json()
            self.assertEqual((r["checks"]["expiry"]["overdue"], r["stats"]["expiry_overdue"]), (1, 1))
            text = await (await client.get("/metrics", headers=admin)).text()
            self.assertIn("mycelic_memories_expiry_overdue 1.0", text)
        self.assertIn("mycelic_memories_expired_total 0.0", text)
        self.assertEqual(await s.sweep_expired(now=later), 1)
        self.assertEqual(await s.sweep_expired(now=later), 0)
        [row] = audits(st, "memory.expired")
        self.assertEqual((row["principal"], row["target"], row["detail"]["event_id"]),
                         ("mycelic", mid, f"evt_x{content_hash(mid, 'expired')[:22]}"))
        r = await (await client.get("/admin/status", headers=admin)).json()
        self.assertEqual(r["checks"]["expiry"]["last_queued"], 0)
        self.assertIsNotNone(r["checks"]["expiry"]["last_sweep_at"])
        self.assertIn("mycelic_memories_expired_total 1.0", await (await client.get("/metrics", headers=admin)).text())
        await h.settle()
        with mock.patch("mycelic.store.now_iso", return_value=iso(later)):
            r = await (await client.get("/admin/status", headers=admin)).json()
            self.assertEqual((r["checks"]["expiry"]["overdue"], r["stats"]["expiry_overdue"]), (0, 0))
            self.assertIn("mycelic_memories_expiry_overdue 0.0", await (await client.get("/metrics", headers=admin)).text())
        self.assertEqual(st.get_memory(mid).status, "retracted")



class UpdateTests(unittest.IsolatedAsyncioTestCase):
    def tmpdir(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    async def world(self, **kwargs: Any) -> World:
        w = World(self.tmpdir(), **kwargs)
        self.addAsyncCleanup(w.close)
        await w.service.transport.connect()
        return w

    async def update(self, w: World, agent_id: str, supersedes: str, text: str, **fields: Any) -> str:
        m, created = await w.service.ingest_memory(w.principal(agent_id), {"text": text, "supersedes": supersedes, **fields})
        self.assertTrue(created)
        return m.memory_id

    async def test_update_supersedes_and_rederives(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        key = st.get_memory(ids["conclusion"]).metadata["agg_key"]
        team_scope = st.get_memory(ids["team"]).scope
        old = ids["log-1"]
        new = await self.update(w, "log-1", old, "Terminal 3 strike called for weeks 41-44.", topic=TRANSPORT,
                                slot="transport_disruption", entity="sd-9", confidence=0.9, idempotency_key="u1")
        self.assertEqual(st.get_memory(new).metadata["version_of"], old)
        self.assertEqual(st.get_memory(old).status, "active", "until the update applies")
        await w.settle()
        m = st.get_memory(old)
        self.assertEqual((m.status, m.superseded_by, m.metadata["status_reason"]), ("superseded", new, "updated by producer"))
        for current in (st.current_derived(ORG, "slot_composition", ORG, key),
                        st.current_derived(ORG, "topic_consolidation", team_scope, TRANSPORT)):
            self.assertIn(new, current.metadata["roots"])
            self.assertNotIn(old, current.metadata["roots"])
        self.assertEqual(st.get_memory(ids["conclusion"]).status, "retracted", "rested on the old note: re-derived")
        self.assertEqual(s.lineage(w.admin, new)["previous_versions"], [old])
        self.assertEqual([(a["principal"], a["target"], a["detail"]["supersedes"]) for a in audits(st, "memory.update")],
                         [("log-1", new, old)])
        self.assertEqual([(a["target"], a["detail"]) for a in audits(st, "memory.updated")], [(old, {"org_id": ORG, "by": new})])
        r = await s.verify(w.admin, old)
        self.assertEqual((r["verdict"], codes(r, old), r["superseded_by"]), ("stale", ["node_superseded"], new))
        r = await s.verify(w.admin, ids["conclusion"])
        self.assertEqual(r["verdict"], "stale")
        self.assertNotIn("status_inconsistent", json.dumps(r))
        self.assertEqual((await s.verify(w.admin, st.current_derived(ORG, "slot_composition", ORG, key).memory_id))["verdict"],
                         "verified")
        # a slot taken away: the rule's only demand evidence is gone, so its conclusion is withdrawn
        await self.update(w, "sales-1", ids["sales-1"], "Helios committed to 40 RX-4 arms; the SD-9 is not in it.",
                          topic="supply:sd-9/demand", confidence=0.8)
        await w.settle()
        self.assertEqual([m for m in st.list_memories(ORG, layers=["enterprise"]) if m.operator == "slot_composition"], [])
        # a topic changed: the note's contribution moves to the new topic's consolidations
        await w.observe("log-1", "The yard of terminal 3 is full.", topic="ops:yard")
        await w.settle()
        moved = await self.update(w, "log-2", ids["log-2"], "Containers wait in the yard, not at sea.", topic="ops:yard")
        await w.settle()
        self.assertIsNone(st.current_derived(ORG, "topic_consolidation", team_scope, TRANSPORT), "log-1 alone on transport")
        self.assertIn(moved, st.current_derived(ORG, "topic_consolidation", team_scope, "ops:yard").metadata["roots"])
        self.assertEqual(invariant_violations(s, ORG), [])
        self.assertEqual(broken_chains(st, ORG), [])
        for m in st.list_memories(ORG, status=None, limit=1000):
            r = await s.verify(w.admin, m.memory_id)
            self.assertEqual(r["verdict"], "verified" if m.status == "active" else "stale", (m.memory_id, r["reasons"]))
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()

    async def test_update_validation_and_conflicts(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, st = w.service, w.store
        log1 = w.principal("log-1")

        async def update(principal: Any, supersedes: str, **fields: Any) -> Any:
            return await s.ingest_memory(principal, {"text": "a correction", "supersedes": supersedes, **fields})

        mine, _ = await s.ingest_memory(log1, {"text": "a note", "topic": "ops:x", "idempotency_key": "k1"})
        await w.settle()
        with self.assertRaises(ValidationError) as ctx:
            await update(log1, mine.memory_id, idempotency_key="k1")
        self.assertEqual(str(ctx.exception), "'supersedes' names the memory this idempotency_key already identifies; use a "
                                             "new idempotency_key for the update")
        for target in ("mem_nope", ids["sales-1"]):         # unknown, and another team's note: the same 404
            with self.subTest(target=target):
                with self.assertRaises(NotFound):
                    await update(log1, target)
        with self.assertRaises(ValidationError) as ctx:
            await update(log1, ids["team"])
        self.assertEqual(str(ctx.exception), "'supersedes' must name a raw observation: derived memories are recomputed from "
                                             "their evidence")
        with self.assertRaises(Forbidden) as ctx:
            await update(w.principal("log-2"), ids["log-1"])
        self.assertEqual(str(ctx.exception), "only the producing agent can update a memory")
        for bad in ("a/b", 5, "x" * 201):
            with self.assertRaises(ValidationError):
                await update(log1, bad)
        with self.assertRaises(ValidationError) as ctx:
            await s.ingest_events(log1, [{"type": "t", "memory": {"text": "embedded", "supersedes": ids["log-1"]}}])
        self.assertEqual(str(ctx.exception), "'supersedes' is accepted by POST /memory only")
        # 409 while a retraction, an expiry's retraction or another update of the note waits in the log
        await s.retract(log1, mine.memory_id, "withdrawn")
        with self.assertRaises(Conflict):
            await update(log1, mine.memory_id)
        await w.settle()
        with self.assertRaises(ValidationError) as ctx:
            await update(log1, mine.memory_id)
        self.assertEqual(str(ctx.exception), "'supersedes' names a memory that is not active (it was superseded or retracted)")
        t0 = utcnow()
        soon, _ = await s.ingest_memory(log1, {"text": "soon stale", "expires_at": iso(t0 + timedelta(seconds=5))})
        await w.settle()
        self.assertEqual(await s.sweep_expired(now=t0 + timedelta(minutes=1)), 1)
        with self.assertRaises(Conflict):
            await update(log1, soon.memory_id)
        first, _ = await update(log1, ids["log-1"], idempotency_key="u1")
        with self.assertRaises(Conflict) as ctx:
            await update(log1, ids["log-1"], idempotency_key="u2")
        self.assertEqual(str(ctx.exception), "a retraction or another update of this memory is waiting to be applied; retry "
                                             "once it has been applied")
        self.assertEqual(st.unapplied_updates(ORG, [ids["log-1"]]), {ids["log-1"]: [first.memory_id]})
        # a resend of the update returns it, applied or not, and whatever its target's state by now
        again, created = await update(log1, ids["log-1"], idempotency_key="u1")
        self.assertEqual((again.memory_id, created), (first.memory_id, False))
        # an update whose event was terminated as failed blocks nothing
        async with st.transaction() as tx:
            tx.mark_failed(first.event_id, "terminated by a test")
        self.assertEqual(st.unapplied_updates(ORG, [ids["log-1"]]), {})
        second, _ = await update(log1, ids["log-1"], idempotency_key="u3")
        await w.settle()
        self.assertEqual((st.get_memory(ids["log-1"]).status, st.get_memory(ids["log-1"]).superseded_by),
                         ("superseded", second.memory_id))
        again, created = await update(log1, ids["log-1"], idempotency_key="u3")
        self.assertEqual((again.memory_id, created), (second.memory_id, False), "even after the old note was superseded")
        with self.assertRaises(ValidationError):
            await update(log1, ids["log-1"], idempotency_key="u4")
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_update_conflict_at_apply_is_plain_and_audited(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        gone, _ = await s.ingest_memory(w.principal("log-1"), {"text": "to be withdrawn", "topic": "ops:x"})
        await w.settle()
        await s.retract(w.principal("log-1"), gone.memory_id, "withdrawn")
        await w.settle()
        cases = {"target not found": ("log-1", "mem_nope"), "target not active": ("log-1", gone.memory_id),
                 "other producer": ("log-2", ids["log-1"]), "not raw": ("log-1", ids["team"]), "self": ("log-1", "mem_self")}
        for n, (reason, (agent_id, target)) in enumerate(cases.items()):
            with self.subTest(reason=reason):
                mid = "mem_self" if reason == "self" else f"mem_conflict_{n}"
                event = observed(st, agent_id, mid, f"A correction ({reason}).", topic=TRANSPORT,
                                 metadata={"version_of": target})
                before = st.get_memory(target)
                w.log.append(event)
                self.assertEqual(await s.apply_event(event, seq=len(w.log)), "applied")
                m = st.get_memory(mid)
                self.assertEqual((m.status, m.superseded_by), ("active", None))
                self.assertEqual([a["detail"] for a in audits(st, "memory.update_conflict") if a["target"] == mid],
                                 [{"org_id": ORG, "supersedes": target, "reason": reason}])
                after = st.get_memory(target)
                if before is not None and before.operator == "agent_observation" and reason != "self":
                    self.assertEqual((after.status, after.superseded_by), (before.status, before.superseded_by), "untouched")
                self.assertNotEqual(after.superseded_by if after else None, mid)
                self.assertIn(mid, st.current_derived(ORG, "topic_consolidation", st.get_memory(ids["team"]).scope,
                                                      TRANSPORT).metadata["roots"], "aggregated as a plain note")
        await w.settle()
        self.assertEqual(audits(st, "memory.updated"), [])
        self.assertEqual(invariant_violations(s, ORG), [])
        for m in st.list_memories(ORG, status=None, limit=1000):
            r = await s.verify(w.admin, m.memory_id)
            self.assertEqual(r["verdict"], "verified" if m.status == "active" else "stale", (m.memory_id, r["reasons"]))
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()

    async def test_update_chain_retracting_the_head_reactivates_nothing(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        a = ids["log-2"]
        b = await self.update(w, "log-2", a, "ETA slipped by 14 days.", topic=TRANSPORT, slot="transport_disruption",
                              entity="sd-9", confidence=0.7)
        await w.settle()
        c = await self.update(w, "log-2", b, "ETA slipped by 16 days.", topic=TRANSPORT, slot="transport_disruption",
                              entity="sd-9", confidence=0.7)
        await w.settle()
        self.assertEqual(s.lineage(w.admin, c)["previous_versions"], [b, a])
        await s.retract(w.principal("log-2"), c, "the carrier was wrong")
        await w.settle()
        self.assertEqual({mid: st.get_memory(mid).status for mid in (a, b, c)},
                         {a: "superseded", b: "superseded", c: "retracted"})
        team = st.current_derived(ORG, "topic_consolidation", st.get_memory(ids["team"]).scope, TRANSPORT)
        self.assertIsNone(team, "log-1 alone: nothing of log-2's chain came back")
        self.assertEqual(invariant_violations(s, ORG), [])
        for mid, code in ((a, "node_superseded"), (b, "node_superseded"), (c, "node_retracted")):
            r = await s.verify(w.admin, mid)
            self.assertEqual((r["verdict"], codes(r, mid)), ("stale", [code]))
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()

    async def test_verification_of_removed_expired_and_updated_leaves(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        t0 = utcnow()
        old = ids["log-1"]
        new = await self.update(w, "log-1", old, "Terminal 3 strike called for weeks 41-44.", topic=TRANSPORT,
                                slot="transport_disruption", entity="sd-9", confidence=0.9)
        short = await w.observe("log-2", "Gate 2 closed today.", topic=TRANSPORT, expires_at=iso(t0 + timedelta(seconds=5)))
        await w.register("proc-2", team="procurement")
        await w.settle()
        await w.observe("proc-2", "Kessler asks for prepayment.", topic="supply:sd-9/supplier", slot="supplier_buffer_low",
                        entity="sd-9", confidence=0.6)
        await w.settle()
        await s.revoke_agent("proc-1", retract=True)
        self.assertEqual(await s.sweep_expired(now=t0 + timedelta(minutes=1)), 1)
        await w.settle()
        self.assertEqual({st.get_memory(mid).status for mid in (old, short, ids["proc-1"])}, {"superseded", "retracted"})
        for m in st.list_memories(ORG, status=None, limit=1000):
            r = await s.verify(w.admin, m.memory_id)
            self.assertEqual(r["verdict"], "verified" if m.status == "active" else "stale", (m.memory_id, r["reasons"]))
            self.assertNotIn("status_inconsistent", json.dumps(r))
        # forgeries: the successor's link, the old note's status, an update the log never applied
        other = ids["log-2"]
        forged = {
            "superseded_by names another note": [("UPDATE memories SET superseded_by=? WHERE memory_id=?", (other, old))],
            "the successor's version_of": [("UPDATE memories SET metadata=json_set(metadata, '$.version_of', ?) WHERE memory_id=?",
                                            (other, new))],
            "the logged version_of": [("UPDATE events SET payload=json_set(payload, '$.metadata.version_of', ?) WHERE event_id=?",
                                       (other, st.get_memory(new).event_id))],
            "an update not applied": [("UPDATE events SET status='published' WHERE event_id=?", (st.get_memory(new).event_id,))],
            "superseded flipped to active": [("UPDATE memories SET status='active' WHERE memory_id=?", (old,))],
            "active flipped to superseded": [("UPDATE memories SET status='superseded', superseded_by=? WHERE memory_id=?",
                                              (new, other))],
        }
        for name, edits in forged.items():
            with self.subTest(forgery=name):
                target = other if name == "active flipped to superseded" else old
                r = w.tampered(target, edits)
                self.assertEqual(r["verdict"], "failed")
                self.assertIn("status_inconsistent", codes(r, target))
        # pending: an update, a removal, an expiry
        pending = await self.update(w, "log-2", other, "ETA slipped by 13 days.", topic=TRANSPORT, slot="transport_disruption",
                                    entity="sd-9", confidence=0.7)
        r = await s.verify(w.admin, other)
        self.assertEqual((r["verdict"], codes(r, other)), ("stale", ["update_pending"]))
        await w.settle()
        self.assertEqual(st.get_memory(other).superseded_by, pending)
        soon = await w.observe("proc-2", "Prepayment is due Friday.", topic="ops:x", expires_at=iso(utcnow() + timedelta(seconds=5)))
        await w.settle()
        await s.revoke_agent("sales-1", retract=True)
        await s.sweep_expired(now=utcnow() + timedelta(minutes=1))
        for mid in (ids["sales-1"], soon):
            r = await s.verify(w.admin, mid)
            self.assertEqual((r["verdict"], codes(r, mid)), ("stale", ["retraction_pending"]), mid)
        await w.settle()
        for mid in (ids["sales-1"], soon):
            r = await s.verify(w.admin, mid)
            self.assertEqual((r["verdict"], codes(r, mid)), ("stale", ["node_retracted"]), mid)



class LifecycleRebuildTests(unittest.IsolatedAsyncioTestCase):
    """Removal, expiry, update and attestation together, with a note logged after a removal and an update in conflict:
    a rebuild of the log reproduces all of it, and every verification report with it."""
    async def test_lifecycle_rebuilds_identically(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        w = World(tmp.name, key=K)
        self.addAsyncCleanup(w.close)
        await w.service.transport.connect()
        ids = await demo(w)
        s, st = w.service, w.store
        t0 = utcnow()
        await w.register("proc-2", team="procurement")
        await w.settle()
        await w.observe("proc-2", "Kessler asks for prepayment.", topic="supply:sd-9/supplier", slot="supplier_buffer_low",
                        entity="sd-9", confidence=0.6, visibility="org", expires_at=iso(t0 + timedelta(hours=1)))
        short = await w.observe("log-2", "Gate 2 closed today.", topic=TRANSPORT, expires_at=iso(t0 + timedelta(seconds=5)))
        new, _ = await s.ingest_memory(w.principal("log-1"), {"text": "Terminal 3 strike called for weeks 41-44.",
                                                              "topic": TRANSPORT, "slot": "transport_disruption",
                                                              "entity": "sd-9", "confidence": 0.9, "supersedes": ids["log-1"]})
        await w.settle()
        self.assertEqual(await s.sweep_expired(now=t0 + timedelta(minutes=1)), 1)
        await s.revoke_agent("proc-1", retract=True)
        await s.attest(w.principal("log-1"), new.memory_id, {"still_true": True}, now=t0 + timedelta(minutes=2))
        await s.attest(w.principal("log-2"), ids["log-2"], {"still_true": True}, now=t0 + timedelta(minutes=3))
        await w.settle()
        self.assertEqual(st.get_memory(new.memory_id).attested_at, iso(t0 + timedelta(minutes=2)))
        late = observed(st, "proc-1", "mem_late_r", "Logged after the removal.", topic="supply:sd-9/supplier",
                        slot="supplier_buffer_low", entity="sd-9")
        w.log.append(late)
        await s.apply_event(late, seq=len(w.log))
        conflict = observed(st, "log-2", "mem_conflict_r", "Names a note that is gone.", topic=TRANSPORT,
                            metadata={"version_of": short})
        w.log.append(conflict)
        await s.apply_event(conflict, seq=len(w.log))
        await w.settle()
        self.assertEqual({mid: st.get_memory(mid).status for mid in (ids["log-1"], new.memory_id, short, ids["proc-1"],
                                                                      "mem_late_r", "mem_conflict_r")},
                         {ids["log-1"]: "superseded", new.memory_id: "active", short: "retracted", ids["proc-1"]: "retracted",
                          "mem_late_r": "retracted", "mem_conflict_r": "active"})
        self.assertEqual(invariant_violations(s, ORG), [])
        rebuilt = await rebuild(w.log, tmp.name + "/rebuilt", event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
            self.assertEqual(invariant_violations(rebuilt, ORG), [])
            for m in st.list_memories(ORG, status=None, limit=10_000):
                live, again = await s.verify(w.admin, m.memory_id), await rebuilt.verify(w.admin, m.memory_id)
                with self.subTest(memory=m.memory_id, status=m.status):
                    self.assertEqual(live["verdict"], "verified" if m.status == "active" else "stale")
                    self.assertEqual((again["report_digest"], again["dag_digest"]), (live["report_digest"], live["dag_digest"]))
        finally:
            await rebuilt.store.close()


if __name__ == "__main__":
    unittest.main()
