"""The fabric over in-process peers: composition, forecast, repair, privacy, bus, pinned demo."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from NeuralGraph.coordination.contracts import InterventionTarget
from NeuralGraph.coordination.core import to_jsonable
from NeuralGraph.mcp import fabric as fabric_mod
from NeuralGraph.mcp.fabric import Fabric, _Clock, _ids
from NeuralGraph.mcp.memory import HashedEmbedder, MemoryEngine

ARTIFACTS = Path(__file__).resolve().parents[1] / "mcp" / "artifacts"
Q = "what does the team know"


def engine(tmp: Path, name: str, domain: str, clock: _Clock) -> MemoryEngine:
    return MemoryEngine(tmp / f"{name}.db", session_key=name, clock=clock, embedder=HashedEmbedder(), failure_domain=domain, id_factory=_ids(name))


class FabricTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); tmp = Path(self.tmp.name); self.clock = _Clock()
        self.a = engine(tmp, "a", "laptop-a", self.clock); self.b = engine(tmp, "b", "office-b", self.clock)
        self.c = engine(tmp, "c", "office-b", self.clock); self.d = engine(tmp, "d", "home-d", self.clock)
        await self.a.remember("Staging db is Postgres 16.", kind="fact", key="env.db")
        await self.a.remember("Token is in the vault.", kind="fact", key="env.token", private=True)
        await self.b.remember("Standup is at 10:00.", kind="fact", key="team.standup")
        await self.c.remember("Standup is at 10:00.", kind="fact", key="team.standup")   # replica, same domain as b
        await self.d.remember("Standup is at 10:00.", kind="fact", key="team.standup")   # independent domain
        self.fabric = Fabric(run_id="t", seed=1)
        for e in (self.a, self.b, self.c, self.d):
            self.fabric.add_in_process(e)

    async def asyncTearDown(self):
        await self.fabric.close()
        for e in (self.a, self.b, self.c, self.d):
            e.close()
        self.tmp.cleanup()

    async def test_collective_composes_keys_no_single_peer_holds(self):
        r = await self.fabric.collective_recall(Q, ("env.db", "team.standup"))
        self.assertTrue(r["complete"])
        self.assertEqual(r["answer"]["env.db"]["producer"], "neuralgraph:a")
        self.assertEqual(r["answer"]["env.db"]["failure_domains"], ["laptop-a"])
        self.assertEqual(r["support_by_key"]["team.standup"], ["neuralgraph:b", "neuralgraph:c", "neuralgraph:d"])
        self.assertEqual(r["metrics"]["unique_failure_domain_count"], 3)
        self.assertEqual(r["metrics"]["minimal_failure_domain_cut"], 1)  # env.db lives in one domain

    async def test_forecast_names_the_single_points_of_failure(self):
        f = await self.fabric.forecast(Q, ("env.db", "team.standup"))
        self.assertEqual(f["single_node_failures_that_forget"], ["neuralgraph:a"])
        self.assertEqual(f["single_domain_failures_that_forget"], ["laptop-a"])
        self.assertEqual(f["by_domain"]["office-b"]["lost"], [])   # b and c share it; d survives
        self.assertEqual(f["by_node"]["neuralgraph:a"]["lost"], ["env.db"])
        # probes were cleared: nothing is masked afterwards
        self.assertTrue(all(r.ended_at for r in self.fabric.failure.records))
        self.assertTrue((await self.fabric.collective_recall(Q, ("env.db", "team.standup")))["complete"])

    async def test_same_domain_replica_is_not_protection_but_independent_domain_is(self):
        f = await self.fabric.forecast(Q, ("team.standup",))
        self.assertEqual(f["single_domain_failures_that_forget"], [])
        await self.d.forget((await self.d.list_recent())[0]["id"], "test")   # remove the independent holder
        f2 = await self.fabric.forecast(Q, ("team.standup",))
        self.assertEqual(f2["single_domain_failures_that_forget"], ["office-b"])
        self.assertEqual(f2["single_node_failures_that_forget"], [])          # b or c alone still fine

    async def test_repair_reaches_an_independent_holder_when_the_route_is_bounded(self):
        # route bound 2: a then b; b's root is masked, so standup is missing and repair must verify c/d
        self.fabric.failure.apply(InterventionTarget.LINEAGE_ROOT, "b-001", "test")
        r = await self.fabric.collective_recall(Q, ("env.db", "team.standup"), max_nodes=2)
        self.assertTrue(r["complete"])
        self.assertIsNotNone(r["repair"])
        self.assertEqual(r["repair"]["reason"], "independent support selected")
        self.assertIn(r["repair"]["selected_node_id"], ("neuralgraph:c", "neuralgraph:d"))
        self.assertEqual(r["answer"]["team.standup"]["producer"], r["repair"]["selected_node_id"])

    async def test_private_memory_crosses_as_denial_only(self):
        r = await self.fabric.collective_recall("token", ("env.token",))
        self.assertFalse(r["complete"]); self.assertEqual(r["missing"], ["env.token"]); self.assertEqual(r["denied_exports"], 1)
        blob = json.dumps([r, to_jsonable(self.fabric.traces.events), self.fabric.bus.to_jsonable()])
        self.assertNotIn("vault", blob)

    async def test_federated_recall_merges_replicas_across_peers(self):
        r = await self.fabric.collective_recall("standup time")
        hit = r["hits"][0]
        self.assertEqual(hit["value"], "Standup is at 10:00.")
        self.assertEqual(hit["replicas"], 3); self.assertEqual(hit["independent_domains"], 2)
        self.assertEqual(sorted(hit["producers"]), ["neuralgraph:b", "neuralgraph:c", "neuralgraph:d"])

    async def test_redelivered_exports_are_dropped_not_double_counted(self):
        peer = self.fabric.registry.adapter_for("neuralgraph:b"); original = peer.query

        async def twice(request):
            exports = await original(request); return exports + exports
        peer.query = twice
        r = await self.fabric.collective_recall(Q, ("team.standup",))
        self.assertEqual(r["duplicates_dropped"], 1)
        self.assertEqual(r["support_by_key"]["team.standup"], ["neuralgraph:b", "neuralgraph:c", "neuralgraph:d"])

    async def test_bus_records_and_hints(self):
        await self.fabric.collective_recall("standup time", ("team.standup",))
        r2 = await self.fabric.collective_recall("standup time again", ("team.standup",))
        self.assertEqual(self.fabric.bus.verify(), 0)
        self.assertEqual(len(self.fabric.bus.entries), 2)
        self.assertEqual(r2["bus_entry"]["peers"], [self.fabric.bus.entries[0].entry_id])

    async def test_bus_persists_across_fabric_restarts(self):
        path = Path(self.tmp.name) / "bus.jsonl"
        f1 = Fabric(run_id="p", seed=2, bus_path=path); f1.add_in_process(self.a)
        await f1.collective_recall("db", ("env.db",)); await f1.close()
        f2 = Fabric(run_id="p", seed=2, bus_path=path); f2.add_in_process(self.a)
        self.assertEqual(len(f2.bus.entries), 1); self.assertEqual(f2.bus.verify(), 0); await f2.close()


class FabricDemoArtifactTests(unittest.TestCase):
    def test_pinned_demo_regenerates_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(fabric_mod.run_demo(Path(tmp)))
        payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
        self.assertEqual((ARTIFACTS / "fabric_demo.json").read_text(encoding="utf-8"), payload, "fabric_demo.json moved; diff it, then re-pin deliberately")
        for line in (ARTIFACTS / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split()
            self.assertEqual(hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest(), digest, name)
        self.assertTrue(result["collective"]["complete"]); self.assertFalse(result["private_memory_leaked"]); self.assertTrue(result["bus_intact"])
        self.assertEqual(result["forecast"]["single_domain_failures_that_forget"], ["laptop-a"])
        self.assertEqual(result["private_probe"]["denied_exports"], 1)


if __name__ == "__main__":
    unittest.main()
