"""The fabric over REAL peer processes: MCP on stdio, a peer killed and regrown."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from NeuralGraph.mcp.fabric import Fabric

ROOT = Path(__file__).resolve().parents[2]


def _env(db: Path, session: str, domain: str) -> dict[str, str]:
    return {"NEURALGRAPH_DB": str(db), "NEURALGRAPH_SESSION": session, "NEURALGRAPH_EMBED": "hashed", "NEURALGRAPH_FAILURE_DOMAIN": domain, "PYTHONPATH": str(ROOT)}


def _pids(marker: str) -> list[int]:
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if p.isdigit() and int(p) != os.getpid()]


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); tmp = Path(self.tmp.name)
        self.fabric = Fabric(run_id="transport", seed=3)
        # no leading dashes: pgrep -f would read them as its own options
        self.marker_a = f"session transport-a-{os.getpid()}"
        self.a = self.fabric.add_mcp("neuralgraph:transport-a", sys.executable, ["-m", "NeuralGraph.mcp.server", "--session", f"transport-a-{os.getpid()}"], _env(tmp / "a.db", "unused", "laptop-a"))
        self.b = self.fabric.add_mcp("neuralgraph:transport-b", sys.executable, ["-m", "NeuralGraph.mcp.server", "--session", f"transport-b-{os.getpid()}"], _env(tmp / "b.db", "unused", "office-b"))
        for peer, text, key in ((self.a, "Staging db is Postgres 16.", "env.db"), (self.b, "Standup is at 10:00.", "team.standup")):
            res = await peer._call("remember", {"text": text, "kind": "fact", "key": key})
            self.assertIsNotNone(res)

    async def asyncTearDown(self):
        await self.fabric.close(); self.tmp.cleanup()

    async def test_collective_over_two_real_processes(self):
        r = await self.fabric.collective_recall("team environment", ("env.db", "team.standup"))
        self.assertTrue(r["complete"], r)
        self.assertEqual(r["answer"]["env.db"]["producer"], "neuralgraph:transport-a")
        self.assertEqual(r["answer"]["team.standup"]["failure_domains"], ["office-b"])
        self.assertEqual(r["transport_failures"], 0)

    async def test_killed_peer_is_a_transport_failure_then_regrows_from_its_store(self):
        await self.fabric.collective_recall("warm", ("env.db",))
        pids = _pids(self.marker_a); self.assertTrue(pids, "peer a process not found")
        for pid in pids:
            os.kill(pid, signal.SIGKILL)
        r = await self.fabric.collective_recall("team environment", ("env.db", "team.standup"))
        # the adapter reconnects (one retry) and the peer's store is intact on disk
        self.assertTrue(r["complete"], r)
        self.assertGreaterEqual(self.a.transport_failures, 1)
        f = await self.fabric.forecast("team environment", ("env.db", "team.standup"))
        self.assertEqual(f["single_node_failures_that_forget"], ["neuralgraph:transport-a", "neuralgraph:transport-b"])

    async def test_unreachable_peer_is_reported_not_raised(self):
        dead = self.fabric.add_mcp("neuralgraph:dead", "/nonexistent/python", ["-m", "x"], {})
        r = await self.fabric.collective_recall("team environment", ("env.db", "team.standup"))
        self.assertTrue(r["complete"])   # the live peers still answer
        self.assertGreaterEqual(dead.transport_failures, 1)


if __name__ == "__main__":
    unittest.main()
