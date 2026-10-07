"""The re-aggregation job: derived state from an older release (ids built without a derivation version, legacy label
spellings, conclusions of rules that are gone), from another MIN_SUPPORT or flagged by a migration converges in the
background, in small steps that keep the node serving, waits out a replay, survives interruption and failure, and is
exposed as POST /admin/reaggregate, ``MycelicClient.reaggregate`` and ``python -m mycelic reaggregate``."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import shutil
import sqlite3
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

from mycelic import aggregation, cli
from mycelic.api import create_app
from mycelic.metrics import Metrics
from mycelic.models import LineageEdge, Memory, derived_memory_id, now_iso
from mycelic.sdk import MycelicClient, MycelicError
from mycelic.service import MycelicService
from mycelic.store import MycelicStore

from .helpers import (
    ADMIN_TOKEN, DEMO_RULE, V2_SCHEMA, HeldTransport, ServiceHarness, full_reaggregation_pass, invariant_violations,
    settings, table_dump,
)

ORG = "northwind"
TEAM = "northwind/emea/nw-gmbh/ops/logistics"
TRANSPORT = "supply:sd-9/transport"
LATE_RULE = {"rule_id": "late", "target_layer": "team", "required_slots": ["bay_blocked"], "corroborate": True, "min_agents": 2,
             "conclusion": "Bay {entity} blocked: {slot:bay_blocked}"}


def g2_keys() -> contextlib.ExitStack:
    """Build ids as the release before derivation versions did: the bare topic, the bare rule and entity."""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(aggregation, "consolidation_id_key", lambda topic, min_support: topic))
    stack.enter_context(mock.patch.object(aggregation, "conclusion_id_key", lambda rule, entity: f"{rule.rule_id}:{entity or '*'}"))
    return stack


def derived(store: MycelicStore, org: str = ORG, status: str | None = "active") -> list[Memory]:
    return [m for m in store.list_memories(org, status=status, limit=100_000) if m.operator != "agent_observation"]


def active_ids(store: MycelicStore) -> set[str]:
    return {r["memory_id"] for r in store._conn.execute("SELECT memory_id FROM memories WHERE status='active'")}


def audits(store: MycelicStore, action: str) -> list[dict[str, Any]]:
    return [a for a in store.recent_audit(10_000) if a["action"] == action]


def slow_steps(seconds: float, calls: list[tuple[str, Any]] | None = None) -> Any:
    """``Aggregator.reaggregate_step`` that blocks the event loop for ``seconds`` first, like a large step would, and
    records each step's (organization, cursor) in ``calls``."""
    original = aggregation.Aggregator.reaggregate_step

    def step(self: aggregation.Aggregator, tx: Any, org_id: str, cursor: Any, **kwargs: Any) -> Any:
        time.sleep(seconds)
        if calls is not None:
            calls.append((org_id, cursor))
        return original(self, tx, org_id, cursor, **kwargs)

    return mock.patch.object(aggregation.Aggregator, "reaggregate_step", step)


def legacy_row(memory_id: str, *, operator: str, layer: str, scope: str, topic: str | None, agg_key: str | None,
               parents: list[Memory], rule_id: str | None = None, entity: str | None = None) -> Memory:
    meta: dict[str, Any] = {"roots": sorted(p.memory_id for p in parents),
                            "contributing_agents": sorted({p.producer_id for p in parents}),
                            "contributing_teams": sorted({p.scope.rsplit("/", 1)[0] for p in parents})}
    if agg_key is not None:
        meta["agg_key"] = agg_key
    return Memory(memory_id=memory_id, org_id=ORG, layer=layer, scope=scope, text=f"legacy {operator} on {topic}", topic=topic,
                  slot=None, entity=entity, kind="fact", confidence=0.7, support=len(meta["contributing_agents"]),
                  independent_teams=len(meta["contributing_teams"]), producer_id="mycelic", operator=operator, rule_id=rule_id,
                  event_id=None, visibility="org", created_at=now_iso(), applied_at=now_iso(), metadata=meta)


class ReaggregationTests(unittest.IsolatedAsyncioTestCase):
    async def harness(self, **kw: Any) -> ServiceHarness:
        h = await ServiceHarness(**kw).start()
        self.addAsyncCleanup(h.close)
        return h

    async def service(self, h: ServiceHarness, **overrides: Any) -> MycelicService:
        """Another service on the harness's database and log (the previous one must be closed)."""
        s = MycelicService(settings(h.tmp.name, **overrides), transport=h.service.transport, metrics=Metrics())
        self.addAsyncCleanup(s.close)
        await s.start()
        return s

    async def world(self, h: ServiceHarness) -> dict[str, str]:
        s = h.service
        for a in ("log-1", "log-2"):
            await h.register(a, team="logistics")
        await h.register("proc-1", team="procurement")
        await h.register("sales-1", team="field-sales", department="commercial")
        for a in ("acme-1", "acme-2"):
            await h.register(a, team="t1", department="ops", subsidiary="acme-gmbh", region="emea", enterprise="acme")
        await s.upsert_rule(DEMO_RULE)
        ids = {
            "t1": await h.observe("log-1", "Port strike.", topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.9),
            "t2": await h.observe("log-2", "Carrier slipped.", topic=TRANSPORT, slot="transport_disruption", entity="sd-9", confidence=0.7),
            "s1": await h.observe("proc-1", "Two weeks of stock.", topic="supply:sd-9/supplier", slot="supplier_buffer_low",
                                  entity="sd-9", confidence=0.85),
            "d1": await h.observe("sales-1", "Committed demand.", topic="supply:sd-9/demand", slot="demand_commitment",
                                  entity="sd-9", confidence=0.8),
        }
        for a in ("acme-1", "acme-2"):
            await h.observe(a, f"acme pallets short ({a})", topic="ops:pallets")
        await h.observe("log-1", "Dock 4 closed.", topic="ops:docks")
        await h.observe("proc-1", "Dock 4 closed, procurement agrees.", topic="ops:docks")
        await h.settle()
        return ids

    async def seed_g2_era(self, h: ServiceHarness) -> dict[str, Any]:
        """A store as the release before derivation versions left it, plus the legacy rows such a store can hold."""
        s = h.service
        st = s.store
        with g2_keys():
            notes = await self.world(h)
            # a rule applied after its evidence: that release evaluated rules on new evidence only, so it never concluded
            # on notes already applied (without a topic, so nothing but the job's rules phase offers them again)
            for a in ("log-1", "log-2"):
                await h.observe(a, f"bay 4 blocked, says {a}", slot="bay_blocked", entity="bay-4")
            await h.settle()
            with mock.patch.object(aggregation.Aggregator, "evaluate_rule", lambda self, tx, rule, **kw: []):
                await s.upsert_rule(LATE_RULE)
                await h.settle()
        self.assertIsNotNone(st.get_applied_rule("late"))
        self.assertEqual([m for m in derived(st, status=None) if m.rule_id == "late"], [])
        [team] = [m for m in derived(st) if m.scope == TEAM and m.topic == TRANSPORT]
        n1, n2 = st.get_memory(notes["t1"]), st.get_memory(notes["t2"])
        self.assertEqual(team.memory_id, derived_memory_id(operator="topic_consolidation", scope=TEAM, key=TRANSPORT,
                                                           parent_ids=[n1.memory_id, n2.memory_id]), "a genuine G2 id")
        rows = {
            # a consolidation under the spelling a note had before labels were normalised
            "legacy_topic": legacy_row(derived_memory_id(operator="topic_consolidation", scope=TEAM, key="Supply:SD-9/Transport",
                                                         parent_ids=[n1.memory_id, n2.memory_id]),
                                       operator="topic_consolidation", layer="team", scope=TEAM, topic="Supply:SD-9/Transport",
                                       agg_key="Supply:SD-9/Transport", parents=[n1, n2]),
            # a conclusion of a rule that is no longer among the applied rules
            "gone_rule": legacy_row(derived_memory_id(operator="slot_composition", scope=TEAM, key="gone:sd-9", parent_ids=[n1.memory_id]),
                                    operator="slot_composition", layer="team", scope=TEAM, topic="gone", agg_key="gone:sd-9",
                                    parents=[n1], rule_id="gone", entity="sd-9"),
            # a derived row written without its key (outside the one-active-per-key index)
            "null_key": legacy_row(derived_memory_id(operator="topic_consolidation", scope=TEAM, key="",
                                                     parent_ids=[n1.memory_id, n2.memory_id]),
                                   operator="topic_consolidation", layer="team", scope=TEAM, topic=TRANSPORT, agg_key=None,
                                   parents=[n1, n2]),
        }
        async with st.transaction() as tx:
            for m in rows.values():
                tx.insert_memory(m)
                tx.add_lineage_edges(LineageEdge(child_id=m.memory_id, parent_id=p, contributed_by="mycelic", parent_layer="agent",
                                                 created_at=m.created_at) for p in m.metadata["roots"])
            tx.c.execute("UPDATE memories SET metadata=json_remove(metadata, '$.derivation') WHERE operator!='agent_observation'")
            tx.delete_meta("derivation_version")
            tx.delete_meta("min_support")
            tx.set_meta("reaggregate_pending", "1")
        self.assertIsNone(st._conn.execute("SELECT agg_key FROM memories WHERE memory_id=?", (rows["null_key"].memory_id,)).fetchone()[0])
        legacy = {m.memory_id: m for org in (ORG, "acme") for m in derived(st, org)}
        self.assertTrue(legacy and all("derivation" not in m.metadata for m in legacy.values()))
        await s.close()
        return {"legacy": legacy, "rows": rows, "notes": notes}

    # ------------------------------------------------------------------ convergence
    async def test_job_converges_legacy_rows_and_is_idempotent(self) -> None:
        h = await self.harness()
        seeded = await self.seed_g2_era(h)
        legacy, rows = seeded["legacy"], seeded["rows"]
        s = await self.service(h)
        self.assertEqual(s._reaggregation["state"], "running")
        self.assertEqual(s._reaggregation["reason"], "pending")
        self.assertTrue(await s.wait_idle(30))
        st = s.store
        self.assertEqual(s._reaggregation["state"], "done", s._reaggregation)
        for org in (ORG, "acme"):
            with self.subTest(org=org):
                active = derived(st, org)
                self.assertTrue(active)
                self.assertEqual([m.memory_id for m in active if (m.metadata.get("derivation") or {}).get("v") != 1], [],
                                 "every active derived memory is derived by this release")
                self.assertEqual(invariant_violations(s, org), [], "soundness and completeness hold")
                [row] = [a for a in audits(st, "aggregation.reaggregate") if a["detail"]["org_id"] == org]
                self.assertEqual(row["detail"]["reason"], "pending")
                self.assertGreater(row["detail"]["changed"], 0)
        # legacy rows are kept as history: superseded by their new version (version_of chains kept) or withdrawn
        for mid, old in legacy.items():
            now = st.get_memory(mid)
            self.assertIsNotNone(now, f"{mid} was deleted")
            self.assertIn(now.status, ("superseded", "retracted"), mid)
            if now.status == "superseded":
                self.assertEqual(st.get_memory(now.superseded_by).metadata["version_of"], mid)
        self.assertEqual(st.get_memory(rows["legacy_topic"].memory_id).status, "retracted")
        self.assertEqual(st.get_memory(rows["gone_rule"].memory_id).metadata["status_reason"], "rule deleted")
        self.assertEqual(st.get_memory(rows["null_key"].memory_id).status, "retracted")
        [canonical] = [m for m in derived(st) if m.scope == TEAM and m.topic == TRANSPORT]
        self.assertEqual(canonical.metadata["derivation"], {"v": 1, "min_support": 2})
        [late] = [m for m in derived(st) if m.rule_id == "late"]
        self.assertEqual((late.scope, late.entity, late.support, late.metadata["version_of"]), (TEAM, "bay-4", 2, None),
                         "the rule that never concluded on its earlier evidence does now")
        self.assertEqual((st.get_meta("reaggregate_pending"), st.get_meta("derivation_version"), st.get_meta("min_support")),
                         (None, "1", "2"))
        self.assertGreater(s.metrics.reaggregation_steps._value.get(), 0)
        self.assertIn("mycelic_reaggregation_steps_total", s.metrics.render()[0].decode())
        # a rebuild of the log derives the converged state directly
        live_active = active_ids(st)
        before = table_dump(st, "memories")
        await s.stop()
        rebuilt = MycelicService(settings(h.tmp.name), store=MycelicStore(":memory:"), transport=s.transport, metrics=Metrics())
        self.addAsyncCleanup(rebuilt.close)
        await rebuilt.start()
        self.assertTrue(await rebuilt.wait_idle(30))
        self.assertEqual(rebuilt._reaggregation["state"], "idle", "a rebuilt database needs no re-aggregation")
        self.assertEqual(active_ids(rebuilt.store), live_active)
        await rebuilt.close()
        # a second run changes nothing
        await s.close()
        again = await self.service(h)
        self.assertIsNone(again._reaggregate_reason())
        self.assertTrue(again._start_reaggregation(None, "requested"))
        self.assertTrue(await again.wait_idle(30))
        self.assertEqual((again._reaggregation["state"], again._reaggregation["changed"]), ("done", 0))
        self.assertEqual(table_dump(again.store, "memories"), before)

    async def test_job_yields_to_the_event_loop(self) -> None:
        h = await self.harness()
        s = h.service
        await self.world(h)
        client = TestClient(TestServer(create_app(s)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        s.reaggregate_batch = 1
        latencies: list[float] = []
        answered = 0
        with slow_steps(0.005):                       # every step blocks the loop, as a large one would
            self.assertTrue(s._start_reaggregation(None, "requested"))
            while s._reaggregating():
                t0 = time.perf_counter()
                await asyncio.sleep(0)                # a probe waits for at most the step in progress
                health = await s.health()
                ready, _ = await s.ready()
                latencies.append(time.perf_counter() - t0)
                self.assertTrue(ready)
                self.assertEqual(health["checks"]["reaggregation"]["state"], "running")
                for path in ("/health", "/ready"):
                    r = await client.get(path)
                    self.assertEqual(r.status, 200, await r.text())
                answered += 1
        self.assertGreaterEqual(s._reaggregation["steps"], 30, s._reaggregation)
        self.assertGreaterEqual(answered, 3, "the probes were answered while the job ran")
        self.assertLess(max(latencies), 0.1, latencies)
        # evidence applied by the consumer between the job's steps converges with it
        with slow_steps(0.005):
            self.assertTrue(s._start_reaggregation(None, "requested"))
            for i in range(6):
                await h.observe(("log-1", "log-2", "proc-1")[i % 3], f"concurrent note {i}", topic=TRANSPORT,
                                slot="transport_disruption", entity="sd-9", confidence=0.5 + i / 20)
                await asyncio.sleep(0.02)
            self.assertTrue(s._reaggregating(), "the notes arrived while the job ran")
            await h.settle()
        for org in (ORG, "acme"):
            self.assertEqual(invariant_violations(s, org), [])
        self.assertEqual(await full_reaggregation_pass(s), 0)

    async def test_wait_idle_waits_for_the_job(self) -> None:
        h = await self.harness()
        s = h.service
        await self.world(h)
        self.assertTrue(await s.wait_idle(10))
        s.reaggregate_batch = 1
        with slow_steps(0.05):                        # dozens of steps: the job runs for well over a second
            self.assertTrue(s._start_reaggregation(None, "requested"))
            self.assertFalse(await s.wait_idle(0.5), "not idle while the job runs, though nothing else is pending")
            self.assertTrue(s._reaggregating())
            self.assertTrue(await s.wait_idle(30))
            self.assertFalse(s._reaggregating(), "idle means the job is done")
        self.assertEqual(s._reaggregation["state"], "done")

    async def test_job_waits_for_replay_and_min_support_change_triggers_it(self) -> None:
        transport = HeldTransport(hold=False)
        h = await self.harness(transport=transport)
        s = h.service
        await self.world(h)
        transport.hold = True
        await s.replay()
        revision, events = s.store.revision, s.store.event_counts()
        self.assertTrue(s._start_reaggregation(None, "requested"))
        await asyncio.sleep(0.3)
        self.assertEqual((s._reaggregation["state"], s._reaggregation["steps"]), ("waiting_for_replay", 0))
        self.assertEqual((s.store.revision, s.store.event_counts()), (revision, events), "nothing changes during the replay")
        self.assertFalse(await s.wait_idle(0.5), "not idle while the job waits")
        transport.hold = False
        self.assertTrue(await s.wait_idle(15))
        self.assertEqual(s._reaggregation["state"], "done")
        self.assertGreater(s._reaggregation["steps"], 0)
        # a replay that starts mid-job: the job pauses, then starts again from the first organization
        calls: list[tuple[str, Any]] = []
        s.reaggregate_batch = 1
        with slow_steps(0.005, calls):
            self.assertTrue(s._start_reaggregation(None, "requested"))
            while len(calls) < 3:
                await asyncio.sleep(0.005)
            transport.hold = True
            await s.replay()
            done_before = len(calls)
            revision, events = s.store.revision, s.store.event_counts()
            await asyncio.sleep(0.3)
            self.assertEqual(s._reaggregation["state"], "waiting_for_replay")
            self.assertLessEqual(len(calls), done_before + 1, "at most the step that was already waiting for the lock")
            self.assertEqual((s.store.revision, s.store.event_counts()), (revision, events), "no step and no event while replaying")
            paused = len(calls)
            transport.hold = False
            self.assertTrue(await s.wait_idle(15))
        self.assertEqual(s._reaggregation["state"], "done")
        self.assertEqual(calls[paused], (s.store.memory_org_ids()[0], None), "restarted from the first organization")
        # MIN_SUPPORT changed between restarts: converged automatically at the next start
        [team] = [m for m in derived(s.store) if m.scope == TEAM and m.topic == TRANSPORT]
        consolidations = len([m for m in derived(s.store) if m.operator == "topic_consolidation"])
        await s.close()
        s2 = await self.service(h, min_support=1)
        self.assertEqual(s2._reaggregation["reason"], "min_support")
        self.assertTrue(await s2.wait_idle(30))
        self.assertEqual(s2._reaggregation["state"], "done")
        now = [m for m in derived(s2.store) if m.operator == "topic_consolidation"]
        self.assertGreater(len(now), consolidations, "single-child units consolidate under min_support 1")
        self.assertEqual({m.metadata["derivation"]["min_support"] for org in (ORG, "acme") for m in derived(s2.store, org)
                          if m.operator == "topic_consolidation"}, {1})
        self.assertEqual(s2.store.get_memory(team.memory_id).status, "superseded", "same coalition, new min_support: new id")
        self.assertEqual(s2.store.get_meta("min_support"), "1")
        for org in (ORG, "acme"):
            self.assertEqual(invariant_violations(s2, org), [])

    # ------------------------------------------------------------------ interruption, failure, fresh databases
    async def test_job_interrupted_by_shutdown_restarts_from_the_beginning(self) -> None:
        h = await self.harness()
        await self.seed_g2_era(h)
        db = Path(h.settings.db_path)
        copy = Path(h.tmp.name) / "copy.db"
        shutil.copyfile(db, copy)
        with slow_steps(0.01):
            s = MycelicService(settings(h.tmp.name), transport=h.service.transport, metrics=Metrics())
            s.reaggregate_batch = 1
            await s.start()
            while s._reaggregation["steps"] < 3:
                await asyncio.sleep(0.005)
            await s.close()
        self.assertEqual(s._reaggregation["state"], "interrupted")
        st = MycelicStore(db)
        try:
            self.assertEqual((st.get_meta("reaggregate_pending"), st.get_meta("derivation_version")), ("1", None),
                             "an interrupted run records nothing")
        finally:
            await st.close()
        resumed = await self.service(h)
        self.assertEqual(resumed._reaggregation["reason"], "pending")
        self.assertTrue(await resumed.wait_idle(30))
        self.assertEqual(resumed._reaggregation["state"], "done")
        self.assertEqual(resumed.store.get_meta("derivation_version"), "1")
        result = active_ids(resumed.store)
        await resumed.close()
        uninterrupted = await self.service(h, db_path=str(copy))
        self.assertTrue(await uninterrupted.wait_idle(30))
        self.assertEqual(active_ids(uninterrupted.store), result, "the same state as a run that was never interrupted")

    async def test_job_failure_is_reported_and_retried_next_start(self) -> None:
        h = await self.harness()
        await self.world(h)
        async with h.service.store.transaction() as tx:
            tx.delete_meta("derivation_version")
        await h.service.close()
        with mock.patch.object(aggregation.Aggregator, "reaggregate_step", side_effect=RuntimeError("injected fault")):
            s = await self.service(h)
            for _ in range(200):
                if s._reaggregation["state"] != "running":
                    break
                await asyncio.sleep(0.01)
        self.assertEqual((s._reaggregation["state"], s._reaggregation["error"]), ("failed", "RuntimeError: injected fault"))
        ready, _ = await s.ready()
        self.assertTrue(ready, "the node keeps serving")
        self.assertEqual((await s.health())["checks"]["reaggregation"]["state"], "failed")
        self.assertIsNone(s.store.get_meta("derivation_version"), "a failed run records nothing")
        self.assertTrue(await s.wait_idle(10))
        await s.close()
        retried = await self.service(h)
        self.assertEqual(retried._reaggregation["reason"], "derivation_version")
        self.assertTrue(await retried.wait_idle(30))
        self.assertEqual(retried._reaggregation["state"], "done")
        self.assertEqual(retried.store.get_meta("derivation_version"), "1")

    async def test_fresh_database_records_meta_without_a_job(self) -> None:
        h = await self.harness()
        s = h.service
        self.assertEqual((s.store.get_meta("derivation_version"), s.store.get_meta("min_support")), ("1", "2"))
        self.assertIsNone(s._reaggregate_task)
        self.assertEqual((await s.health())["checks"]["reaggregation"], {"state": "idle"})
        await self.world(h)
        # a rebuild into a fresh database: recorded at start, nothing to converge after the replay
        await s.stop()
        rebuilt = await self.service(h, db_path=str(Path(h.tmp.name) / "rebuilt.db"))
        self.assertTrue(await rebuilt.wait_idle(15))
        self.assertIsNone(rebuilt._reaggregate_task)
        self.assertEqual(rebuilt._reaggregation["state"], "idle")
        self.assertEqual(rebuilt.store.get_meta("derivation_version"), "1")
        self.assertEqual(active_ids(rebuilt.store), active_ids(s.store))
        await rebuilt.close()
        # an empty database migrated from schema 2: the migration's pending flag is cleared at start, without a job
        v2 = Path(h.tmp.name) / "v2.db"
        c = sqlite3.connect(v2)
        c.executescript(V2_SCHEMA)
        c.close()
        migrated = await self.service(h, db_path=str(v2))
        self.assertIsNone(migrated._reaggregate_task)
        self.assertEqual((migrated.store.get_meta("reaggregate_pending"), migrated.store.get_meta("derivation_version")), (None, "1"))
        self.assertEqual(migrated._reaggregation["state"], "idle")

    # ------------------------------------------------------------------ surfaces
    async def test_admin_route_sdk_and_cli(self) -> None:
        h = await self.harness()
        s = h.service
        await self.world(h)
        client = TestClient(TestServer(create_app(s)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        url = str(client.make_url("")).rstrip("/")
        admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        agent = {"Authorization": f"Bearer {h.keys['log-1']}"}
        r = await client.post("/admin/reaggregate", headers=agent)
        self.assertEqual(r.status, 403)
        # an empty org_id is refused rather than widened to every organization (omitted or null means all of them)
        for bad in ({"org_id": "Not/AnOrg"}, {"org_id": "UPPER"}, {"org_id": 7}, {"org_id": "x" * 65}, {"org_id": ""},
                    {"org_id": "  "}):
            with self.subTest(body=bad):
                r = await client.post("/admin/reaggregate", json=bad, headers=admin)
                self.assertEqual(r.status, 400, await r.text())
        # an organization-scoped run never records the derivation meta
        async with s.store.transaction() as tx:
            tx.delete_meta("derivation_version")
        s.reaggregate_batch = 1
        with slow_steps(0.005):
            r = await client.post("/admin/reaggregate", json={"org_id": ORG}, headers=admin)
            self.assertEqual((r.status, await r.json()), (202, {"started": True, "org_id": ORG}))
            r = await client.post("/admin/reaggregate", headers=admin)
            self.assertEqual((r.status, await r.json()), (202, {"started": False, "org_id": None}), "the running job is kept")
            r = await client.get("/admin/status", headers=admin)
            self.assertEqual((await r.json())["checks"]["reaggregation"]["state"], "running")
            self.assertTrue(await s.wait_idle(15))
        self.assertEqual(s._reaggregation["scope"], [ORG])
        self.assertIsNone(s.store.get_meta("derivation_version"))
        # an organization with no memories: a no-op, audited
        r = await client.post("/admin/reaggregate", json={"org_id": "nobody"}, headers=admin)
        self.assertEqual(r.status, 202)
        self.assertTrue(await s.wait_idle(15))
        [row] = [a for a in audits(s.store, "aggregation.reaggregate") if a["detail"]["org_id"] == "nobody"]
        self.assertEqual(row["detail"]["changed"], 0)
        self.assertEqual([a["detail"] for a in audits(s.store, "reaggregate")][0], {"org_id": "nobody", "started": True})
        # the SDK and the CLI, against the running server; a full run records the meta
        with self.assertRaises(MycelicError) as refused:
            await asyncio.to_thread(MycelicClient(url, ADMIN_TOKEN, retries=0).reaggregate, "")
        self.assertEqual(refused.exception.status, 400, "the SDK passes an empty org_id on instead of widening the run")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = await asyncio.to_thread(cli.main, ["reaggregate", "--url", url, "--admin-token", ADMIN_TOKEN, "--org", ""])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 400", err.getvalue())
        res = await asyncio.to_thread(MycelicClient(url, ADMIN_TOKEN, retries=0).reaggregate)
        self.assertEqual(res, {"started": True, "org_id": None})
        self.assertTrue(await s.wait_idle(15))
        self.assertEqual(s.store.get_meta("derivation_version"), "1")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = await asyncio.to_thread(cli.main, ["reaggregate", "--url", url, "--admin-token", ADMIN_TOKEN, "--org", "acme"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"started": True, "org_id": "acme"})
        self.assertTrue(await s.wait_idle(15))
        r = await client.get("/admin/status", headers=admin)
        status = (await r.json())["checks"]["reaggregation"]
        self.assertEqual((status["state"], status["scope"], status["changed"]), ("done", ["acme"], 0))
        jobs = [t for t in s._tasks if t.get_name() == "mycelic-reaggregate"]
        self.assertEqual(jobs, [s._reaggregate_task], "finished runs are not kept for stop() to cancel")
        r = await client.get("/health")
        self.assertEqual(set(await r.json()), {"status", "version", "transport_connected", "consumer_running"})


if __name__ == "__main__":
    unittest.main()
