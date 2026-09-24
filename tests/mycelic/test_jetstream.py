"""Integration tests against a real nats-server with JetStream (file store).

They prove what the in-process transport cannot: the service publishes to and consumes from a real broker; state
survives a service restart (durable consumer resumes), a broker restart (file store keeps the stream), an outage in
between (the outbox flushes afterwards), and a lost database is rebuilt from the stream with identical derived ids.

The binary is found via ``MYCELIC_NATS_SERVER_BIN`` or ``nats-server`` on PATH; the whole module is skipped otherwise
(the smoke test and CI must run it).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from mycelic.metrics import Metrics
from mycelic.service import MycelicService

from .helpers import DEMO_RULE, settings

NATS_BIN = os.environ.get("MYCELIC_NATS_SERVER_BIN") or shutil.which("nats-server")
NATS_USER, NATS_PASSWORD = "mycelic", "nats-test-password-0123456789abcdef"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NatsServer:
    """A nats-server subprocess with JetStream on a temp dir and user/password auth, like the compose broker."""

    def __init__(self, store_dir: Path, port: int) -> None:
        self.store_dir = store_dir
        self.port = port
        self.conf = store_dir / "nats.conf"
        self.conf.write_text(f"""
port: {port}
http_port: -1
jetstream {{ store_dir: "{store_dir / 'js'}" }}
authorization {{ user: "{NATS_USER}", password: "{NATS_PASSWORD}" }}
""")
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen([NATS_BIN, "-c", str(self.conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("nats-server did not start")

    def stop(self, *, kill: bool = False) -> None:
        if self.proc is None:
            return
        self.proc.kill() if kill else self.proc.terminate()
        self.proc.wait(timeout=10)
        self.proc = None

    @property
    def url(self) -> str:
        return f"nats://127.0.0.1:{self.port}"


@unittest.skipUnless(NATS_BIN, "nats-server binary not available (set MYCELIC_NATS_SERVER_BIN)")
class JetStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.nats = NatsServer(self.root, free_port())
        self.nats.start()
        self.db = self.root / "mycelic.db"
        self.services: list[MycelicService] = []

    async def asyncTearDown(self) -> None:
        for s in self.services:
            try:
                await s.close()
            except Exception:
                pass
        self.nats.stop(kill=True)
        self.tmp.cleanup()

    def make_settings(self, **overrides):
        return settings(self.root, db_path=str(self.db), nats_url=self.nats.url, nats_user=NATS_USER, nats_password=NATS_PASSWORD,
                        nats_stream="MYCELIC_TEST", nats_consumer="mycelic-test", event_signing_key="test-signing-key-0123456789abcdef0123",
                        **overrides)

    async def new_service(self, **overrides) -> MycelicService:
        s = MycelicService(self.make_settings(**overrides), metrics=Metrics())
        await s.start()
        self.services.append(s)
        return s

    async def seed(self, s: MycelicService) -> dict[str, str]:
        await s.upsert_rule(DEMO_RULE)
        keys = {}
        for agent, units in (("log-1", {"team": "logistics"}), ("log-2", {"team": "logistics"}), ("proc-1", {"team": "procurement"}),
                             ("sales-1", {"department": "commercial", "team": "field-sales"})):
            _, key = await s.register_agent({"enterprise": "northwind", "region": "emea", "subsidiary": "nw-gmbh",
                                             "department": units.get("department", "ops"), "team": units["team"], "agent_id": agent})
            keys[agent] = key
        obs = [("log-1", "Port of Rotterdam terminal 3 strike announced for weeks 41-43.", "supply:sd-9/transport", "transport_disruption", 0.9),
               ("log-2", "Carrier ETA for the SD-9 container slipped by 12 days.", "supply:sd-9/transport", "transport_disruption", 0.7),
               ("proc-1", "Kessler Antriebe has two weeks of SD-9 inventory left.", "supply:sd-9/supplier", "supplier_buffer_low", 0.85),
               ("sales-1", "Helios Automation committed to 40 RX-4 arms for November.", "supply:sd-9/demand", "demand_commitment", 0.8)]
        for agent, text, topic, slot, conf in obs:
            p = s.authenticate(f"Bearer {keys[agent]}")
            await s.ingest_memory(p, {"text": text, "topic": topic, "slot": slot, "entity": "sd-9", "confidence": conf})
        self.assertTrue(await s.wait_idle(20))
        return keys

    def snapshot(self, s: MycelicService) -> dict[str, tuple]:
        return {m.memory_id: (m.layer, m.status, m.support, m.confidence)
                for m in s.store.list_memories("northwind", status=None, limit=1000)}

    async def test_publish_consume_and_survive_service_restart(self) -> None:
        s1 = await self.new_service()
        keys = await self.seed(s1)
        info = await s1.transport.info()
        self.assertTrue(info["connected"])
        self.assertGreaterEqual(info["stream_messages"], 9, "registrations, rule, observations and derived events are in the stream")
        self.assertEqual(info["consumer_pending"], 0)
        st = s1.store.stats()
        self.assertEqual(st["memories_by_layer"]["enterprise"], 1)
        self.assertEqual(st["outbox_pending"], 0)
        before = self.snapshot(s1)
        seq_before = st["last_applied_seq"]
        await s1.close()
        self.services.remove(s1)

        s2 = await self.new_service()
        self.assertTrue(await s2.wait_idle(10))
        self.assertEqual(self.snapshot(s2), before, "restart changes nothing")
        self.assertEqual(s2.store.stats()["last_applied_seq"], seq_before)
        self.assertIsNone(s2._replay_target, "an up-to-date database does not replay")
        p = s2.authenticate(f"Bearer {keys['sales-1']}")
        res = s2.query(p, {"query": "supply risk sd-9", "scope": "northwind"})
        self.assertEqual(res["answer"]["layer"], "enterprise")
        self.assertTrue(res["answer"]["lineage"]["reconstructable"])

    async def test_broker_outage_is_absorbed_by_the_outbox(self) -> None:
        s = await self.new_service()
        keys = await self.seed(s)
        _, keys["log-3"] = await s.register_agent({"enterprise": "northwind", "region": "emea", "subsidiary": "nw-gmbh",
                                                   "department": "ops", "team": "logistics", "agent_id": "log-3"})
        self.assertTrue(await s.wait_idle(10))
        self.nats.stop(kill=True)
        await asyncio.sleep(0.5)
        p = s.authenticate(f"Bearer {keys['log-3']}")
        m, _ = await s.ingest_memory(p, {"text": "Customs backlog of two weeks reported at Rotterdam.", "topic": "supply:sd-9/transport",
                                        "slot": "transport_disruption", "entity": "sd-9", "confidence": 0.6})
        await asyncio.sleep(1.0)
        self.assertGreaterEqual(s.store.stats()["outbox_pending"], 1, "accepted while the broker is down")
        h = await s.health()
        self.assertEqual(h["status"], "degraded")
        ok, _ = await s.ready()
        self.assertTrue(ok, "readiness does not depend on the broker by default")
        self.nats.start()
        self.assertTrue(await s.wait_idle(30), "outbox flushed and applied after the broker returned")
        self.assertEqual(s.store.stats()["outbox_pending"], 0)
        self.assertEqual(s.transport.reconnects, 1, "the existing client reconnected; no second client was opened")
        self.assertEqual(s.metrics.recoveries.labels("transport_reconnect")._value.get(), 0)
        self.assertEqual(s.store.get_memory(m.memory_id).applied_at is not None, True)
        team = s.store.list_memories("northwind", layers=["team"])[0]
        self.assertEqual(team.support, 3)

    async def test_lost_database_is_rebuilt_from_the_stream(self) -> None:
        s1 = await self.new_service()
        keys = await self.seed(s1)
        before = self.snapshot(s1)
        active_before = {k for k, v in before.items() if v[1] == "active"}
        stream_before = (await s1.transport.info())["stream_messages"]
        await s1.close()
        self.services.remove(s1)
        for f in self.root.glob("mycelic.db*"):
            f.unlink()

        s2 = await self.new_service()
        self.assertGreater(s2.metrics.recoveries.labels("replay_fresh_db")._value.get(), 0)
        self.assertTrue(await s2.wait_idle(30))
        after = self.snapshot(s2)
        active_after = {k for k, v in after.items() if v[1] == "active"}
        self.assertEqual(active_after, active_before, "same derived ids after the rebuild")
        self.assertEqual({k: after[k] for k in active_after}, {k: before[k] for k in active_before})
        self.assertEqual((await s2.transport.info())["stream_messages"], stream_before, "replay appended nothing")
        self.assertEqual(len(await s2.store.list_agents("northwind")), 4)
        p = s2.authenticate(f"Bearer {keys['sales-1']}")     # the old key still works: registrations replayed
        res = s2.query(p, {"query": "supply risk sd-9", "scope": "northwind"})
        self.assertEqual(res["answer"]["layer"], "enterprise")
        self.assertTrue(res["answer"]["lineage"]["reconstructable"])
        self.assertEqual(len(s2.store.list_rules("northwind")), 1)

    async def test_restored_backup_receives_the_events_it_missed(self) -> None:
        import sqlite3

        s1 = await self.new_service()
        await self.seed(s1)
        await s1.close()
        self.services.remove(s1)
        backup = self.root / "backup.db"          # a quiescent copy, like a volume snapshot
        src, dst = sqlite3.connect(self.db), sqlite3.connect(backup)
        src.backup(dst)
        src.close(); dst.close()

        s2 = await self.new_service()             # life goes on after the backup: a new agent and a new observation
        _, key3 = await s2.register_agent({"enterprise": "northwind", "region": "emea", "subsidiary": "nw-gmbh",
                                           "department": "ops", "team": "logistics", "agent_id": "log-3"})
        p = s2.authenticate(f"Bearer {key3}")
        m, _ = await s2.ingest_memory(p, {"text": "Customs backlog of two weeks reported at Rotterdam.", "topic": "supply:sd-9/transport",
                                         "slot": "transport_disruption", "entity": "sd-9", "confidence": 0.6})
        self.assertTrue(await s2.wait_idle(20))
        await s2.close()
        self.services.remove(s2)

        for f in self.root.glob("mycelic.db*"):   # disaster: restore the older database
            f.unlink()
        shutil.copy(backup, self.db)
        s3 = await self.new_service()
        self.assertTrue(await s3.wait_idle(20))
        self.assertGreater(s3.metrics.recoveries.labels("replay_restored_backup")._value.get(), 0)
        self.assertIsNotNone(s3.store.get_memory(m.memory_id), "the event after the backup was re-delivered")
        self.assertIsNotNone(s3.store.get_agent("log-3"), "so was the registration")
        self.assertEqual(s3.store.list_memories("northwind", layers=["team"])[0].support, 3)
        self.assertEqual(s3.authenticate(f"Bearer {key3}").id, "log-3")

    async def test_lost_consumer_is_recreated_after_the_last_applied_event(self) -> None:
        s1 = await self.new_service()
        await self.seed(s1)
        before = self.snapshot(s1)
        stream_before = (await s1.transport.info())["stream_messages"]
        await s1.transport._js.delete_consumer(s1.settings.nats_stream, s1.settings.nats_consumer)
        await s1.close()
        self.services.remove(s1)
        s2 = await self.new_service()
        self.assertTrue(await s2.wait_idle(20))
        self.assertGreater(s2.metrics.recoveries.labels("consumer_recreated")._value.get(), 0)
        self.assertEqual(self.snapshot(s2), before)
        self.assertEqual((await s2.transport.info())["stream_messages"], stream_before, "nothing was re-published")
        self.assertEqual(s2.store.stats()["outbox_pending"], 0)

    async def test_forced_replay_is_idempotent(self) -> None:
        s = await self.new_service()
        await self.seed(s)
        before = self.snapshot(s)
        stream_before = (await s.transport.info())["stream_messages"]
        res = await s.replay()
        self.assertTrue(res["replaying"])
        self.assertFalse((await s.ready())[0], "not ready while replaying")
        self.assertTrue(await s.wait_idle(30))
        self.assertTrue((await s.ready())[0])
        self.assertEqual(self.snapshot(s), before)
        self.assertEqual((await s.transport.info())["stream_messages"], stream_before)
        self.assertGreater(s.metrics.recoveries.labels("replay_requested")._value.get(), 0)
        self.assertIsNone(s.store.get_meta("replay_target_seq"))

    async def test_poison_event_is_terminated_and_replay_completes(self) -> None:
        import json
        s = await self.new_service(nats_max_deliver=2)
        await self.seed(s)
        bad = {"event_id": "evt_poison", "kind": "memory.observed", "org_id": "northwind", "agent_id": "log-1",
               "created_at": "2026-01-01T00:00:00+00:00", "payload": {"memory_id": "mem_poison"}, "schema": 1}
        wire = json.dumps(bad).encode()
        await s.transport.publish("mycelic.northwind.memory-observed", wire, "evt_poison", headers=s._sign(wire))
        self.assertTrue(await s.wait_idle(30))
        self.assertGreater(s.metrics.events_failed.labels("apply")._value.get(), 0)
        self.assertIsNone(s.store.get_memory("mem_poison"))
        self.assertIn("event.failed", {row["action"] for row in s.store.recent_audit(20)})
        # an unsigned event as the very last stream entry must not leave a rebuild stuck in 'replaying'
        await s.transport.publish("mycelic.northwind.memory-observed", b'{"event_id": "evt_unsigned", "kind": "agent.event", "payload": {}}', "evt_unsigned")
        self.assertTrue(await s.wait_idle(20))
        await s.close()
        self.services.remove(s)
        for f in self.root.glob("mycelic.db*"):
            f.unlink()
        s2 = await self.new_service(nats_max_deliver=2)
        self.assertTrue(await s2.wait_idle(30))
        self.assertTrue((await s2.ready())[0], "terminated events count as consumed for replay completion")
        self.assertEqual(s2.store.stats()["memories_by_layer"]["enterprise"], 1)

    async def test_unfinished_replay_resumes_after_a_crash(self) -> None:
        s1 = await self.new_service()
        await self.seed(s1)
        before = self.snapshot(s1)
        stream_before = (await s1.transport.info())["stream_messages"]
        await s1.close()
        self.services.remove(s1)
        for f in self.root.glob("mycelic.db*"):
            f.unlink()
        # a rebuild that dies after a few events: no background loops, three deliveries applied by hand
        s2 = MycelicService(self.make_settings(), metrics=Metrics())
        await s2.start(background=False)
        self.assertIsNotNone(s2._replay_target)
        for _ in range(3):
            for d in await s2.transport.fetch(1, 2.0):
                await s2._handle_delivery(d)
        self.assertEqual(s2.store.get_meta("last_applied_seq"), "3")
        self.assertIsNotNone(s2.store.get_meta("replay_target_seq"))
        await s2.transport.close()
        await s2.store.close()
        s3 = await self.new_service()
        self.assertFalse((await s3.ready())[0], "still replaying after the crash")
        self.assertGreater(s3.metrics.recoveries.labels("replay_resumed")._value.get(), 0)
        self.assertTrue(await s3.wait_idle(30))
        self.assertTrue((await s3.ready())[0])
        self.assertEqual(self.snapshot(s3), before)
        self.assertEqual((await s3.transport.info())["stream_messages"], stream_before, "no duplicates were appended")

    async def test_unsigned_events_are_rejected(self) -> None:
        s = await self.new_service()
        await self.seed(s)
        import json
        forged = {"event_id": "evt_forged", "kind": "memory.observed", "org_id": "northwind", "agent_id": "log-1", "created_at": "2026-01-01T00:00:00+00:00",
                  "payload": {"memory_id": "mem_forged", "org_id": "northwind", "layer": "agent", "scope": "northwind/emea/nw-gmbh/ops/logistics/log-1",
                              "text": "forged", "producer_id": "log-1", "operator": "agent_observation"}, "schema": 1}
        await s.transport.publish("mycelic.northwind.memory-observed", json.dumps(forged).encode(), "evt_forged")
        await asyncio.sleep(1.5)
        self.assertIsNone(s.store.get_memory("mem_forged"))
        self.assertGreater(s.metrics.events_failed.labels("signature")._value.get(), 0)


if __name__ == "__main__":
    unittest.main()
