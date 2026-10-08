"""Downward verification from the agent side, against a live server: ``MycelicClient.verify`` and ``query(verify=True)``,
``python -m mycelic verify`` (its output and its exit status per verdict) and ``query --verify``, and the stdio MCP
proxy's ``mycelic_verify`` against this server and against one that predates the route.  And what an agent's local
store and the reference agent process send: only notes the agent asked to share, as it asked."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestServer

from NeuralGraph.chat_memory.mcp_server import ToolError
from mycelic import cli, mcp, verification
from mycelic.api import create_app
from mycelic.models import utcnow
from mycelic.sdk import LocalMemory, MycelicClient, MycelicError
from mycelic.sdk import agent as reference_agent

from .helpers import ADMIN_TOKEN, ServiceHarness
from .test_verification import demo

ROOT = Path(__file__).resolve().parents[2]
QUERY = "delivery risk RX-4 SD-9"


class SdkCliTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.h = await ServiceHarness().start()
        self.server = TestServer(create_app(self.h.service), host="127.0.0.1")
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")
        self.ids = await demo(self.h)

    async def asyncTearDown(self) -> None:
        await self.server.close()
        await self.h.close()

    def client(self, agent_id: str | None = None, url: str | None = None) -> MycelicClient:
        """An SDK client of ``agent_id`` (the administrator when None), without retries."""
        return MycelicClient(url or self.url, self.h.keys[agent_id] if agent_id else ADMIN_TOKEN, retries=0)

    async def cli(self, *argv: str) -> tuple[int, list[str], str]:
        """``cli.main(argv)`` in a thread (the server answers on this loop), with neither key in the environment: its exit
        status, its stdout lines and its stderr."""
        out, err = io.StringIO(), io.StringIO()

        def run() -> int:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                return cli.main(list(argv))

        with mock.patch.dict(os.environ):
            os.environ.pop("MYCELIC_API_KEY", None)
            os.environ.pop("MYCELIC_ADMIN_TOKEN", None)
            code = await asyncio.to_thread(run)
        return code, out.getvalue().splitlines(), err.getvalue()

    async def test_sdk_verify_matches_service(self) -> None:
        s, cid = self.h.service, self.ids["conclusion"]
        for who, principal in (("sales-2", self.h.principal("sales-2")), (None, self.h.admin)):
            with self.subTest(principal=who or "admin"):
                report = await asyncio.to_thread(self.client(who).verify, cid)
                self.assertEqual(report["verdict"], "verified")
                self.assertEqual(report["report_digest"], (await s.verify(principal, cid))["report_digest"])
        agent = self.client("sales-2")
        report = await asyncio.to_thread(agent.verify, cid, max_leaf_age=3600)
        self.assertEqual((report["verdict"], report["max_leaf_age"]), ("verified", 3600))
        with mock.patch.object(agent, "_request", wraps=agent._request) as spy:
            await asyncio.to_thread(agent.verify, cid)
            await asyncio.to_thread(agent.verify, cid, max_leaf_age=60)
        self.assertEqual([c.kwargs["params"] for c in spy.call_args_list], [None, {"max_leaf_age": 60}], "no bare '?'")
        # 'a/b' reaches the service only because the id is quoted with safe='' (with '/' kept it is the router's 404)
        for memory_id, kwargs, status, message in (("mem_nope", {}, 404, "no visible memory with id mem_nope"),
                                                   (self.ids["log-1"], {}, 404, f"no visible memory with id {self.ids['log-1']}"),
                                                   ("..", {}, 404, "no visible memory with id .."),
                                                   ("a/b", {}, 400, "'memory_id' must be an id"),
                                                   ("a b?c#d%", {}, 400, "'memory_id' must be an id"),
                                                   (cid, {"max_leaf_age": 0}, 400, "'max_leaf_age' must be an integer")):
            with self.subTest(memory_id=memory_id, **kwargs):
                with self.assertRaises(MycelicError) as ctx:
                    await asyncio.to_thread(agent.verify, memory_id, **kwargs)
                self.assertEqual(ctx.exception.status, status)
                self.assertTrue(ctx.exception.message.startswith(message), ctx.exception.message)
        # the flag is sent only when asked for
        with mock.patch.object(agent, "_request", wraps=agent._request) as spy:
            plain = await asyncio.to_thread(agent.query, QUERY, scope="northwind")
            verified = await asyncio.to_thread(agent.query, QUERY, scope="northwind", verify=True)
        (_, _, default_body), (_, _, verify_body) = [c.args for c in spy.call_args_list]
        self.assertNotIn("verify", default_body)
        self.assertEqual({**default_body, "verify": True}, verify_body)
        self.assertNotIn("verification", plain["answer"])
        self.assertEqual(verified["answer"]["memory_id"], cid)
        self.assertEqual(verified["answer"]["verification"],
                         {"verdict": "verified", "derived_correctly": True, "still_true": True, "reasons": []})

    async def test_cli_verdict_exit_codes(self) -> None:
        self.assertEqual([cli.verdict_exit_code(v) for v in ("verified", "stale", "failed", "unverifiable", "bogus", "")],
                         [0, 3, 4, 5, 1, 1])
        self.assertEqual(set(cli.VERDICT_EXIT_CODES), set(verification.VERDICTS), "a new verdict needs an exit status")
        s, cid, key = self.h.service, self.ids["conclusion"], self.h.keys["sales-2"]
        expected = await s.verify(self.h.principal("sales-2"), cid)
        base = ("verify", cid, "--url", self.url)

        code, out, _ = await self.cli(*base, "--api-key", key)
        self.assertEqual(code, 0)
        self.assertEqual(out[0], f"verified  derived_correctly=true  still_true=true  {cid}")
        self.assertRegex(out[1], r"^4 nodes \(1 derived, 3 leaves, 2 redacted\)  integrity unkeyed  verified at \S+$")
        self.assertEqual(out[-1], f"report_digest {expected['report_digest']}")
        self.assertEqual(len(out), 3, "no reasons and no warnings")
        code, out, _ = await self.cli(*base, "--api-key", key, "--json")
        self.assertEqual((code, json.loads("\n".join(out))["report_digest"]), (0, expected["report_digest"]))
        code, out, _ = await self.cli(*base, "--admin-token", ADMIN_TOKEN)
        self.assertEqual((code, out[-1]), (0, f"report_digest {(await s.verify(self.h.admin, cid))['report_digest']}"))
        # stale: two days on, every note sales-2 may read is older than a second
        with mock.patch("mycelic.service.utcnow", return_value=utcnow() + timedelta(days=2)):
            code, out, _ = await self.cli(*base, "--api-key", key, "--max-leaf-age", "1")
        self.assertEqual(code, 3)
        self.assertEqual(out[0], f"stale  derived_correctly=true  still_true=false  {cid}")
        self.assertIn("  S leaf_stale x1", out)
        budget = s.settings.verify_max_nodes
        s.settings.verify_max_nodes = 1
        try:
            code, out, _ = await self.cli(*base, "--api-key", key)
        finally:
            s.settings.verify_max_nodes = budget
        self.assertEqual(code, 5)
        self.assertEqual(out[0], f"unverifiable  derived_correctly=null  still_true=null  {cid}")
        self.assertIn("  U walk_truncated x1", out)
        # an invisible id, an older server, no key and a malformed argument
        code, out, err = await self.cli("verify", self.ids["log-1"], "--url", self.url, "--api-key", key)
        self.assertEqual((code, out), (1, []))
        self.assertIn("HTTP 404", err)
        old = TestServer(web.Application(), host="127.0.0.1")
        await old.start_server()
        try:
            code, _, err = await self.cli("verify", cid, "--url", str(old.make_url("")).rstrip("/"), "--api-key", key)
        finally:
            await old.close()
        self.assertEqual(code, 1)
        self.assertIn("HTTP 404", err)
        with self.assertRaises(SystemExit) as ctx:
            await self.cli(*base)
        self.assertEqual(ctx.exception.code, "MYCELIC_API_KEY or MYCELIC_ADMIN_TOKEN is required")
        with self.assertRaises(SystemExit) as ctx:
            await self.cli(*base, "--api-key", key, "--max-leaf-age", "1.5")
        self.assertEqual(ctx.exception.code, 2, "argparse rejects a non-integer")
        # query --verify
        code, out, _ = await self.cli("query", QUERY, "--url", self.url, "--api-key", key, "--scope", "northwind", "--verify")
        self.assertEqual(code, 0)
        self.assertIn("verification: verified (derived_correctly=true, still_true=true)", out)
        code, out, _ = await self.cli("query", QUERY, "--url", self.url, "--api-key", key, "--scope", "northwind")
        self.assertFalse([line for line in out if line.startswith("verification:")])
        # failed, last (the row stays tampered)
        s.store._conn.execute("UPDATE memories SET text=text||'!' WHERE memory_id=?", (self.ids["log-1"],))
        code, out, _ = await self.cli(*base, "--api-key", key)
        self.assertEqual(code, 4)
        self.assertEqual(out[0], f"failed  derived_correctly=false  still_true=null  {cid}")
        self.assertIn("  E hidden_error x1", out)

    def test_cli_verify_help(self) -> None:
        r = subprocess.run([sys.executable, "-m", "mycelic", "verify", "--help"], cwd=ROOT, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        for flag in ("--max-leaf-age", "--json", "--api-key"):
            self.assertIn(flag, r.stdout)

    async def test_stdio_proxy_verify_against_new_and_old_servers(self) -> None:
        s, cid, key = self.h.service, self.ids["conclusion"], self.h.keys["sales-2"]
        tools = mcp.ProxyTools(MycelicClient(self.url, key, retries=0))
        report: dict[str, Any] = await tools.call("mycelic_verify", {"memory_id": cid})
        self.assertEqual(report["report_digest"], (await s.verify(self.h.principal("sales-2"), cid))["report_digest"])
        report = await tools.call("mycelic_verify", {"memory_id": cid, "max_leaf_age_seconds": 3600})
        self.assertEqual((report["verdict"], report["max_leaf_age"]), ("verified", 3600))
        with self.assertRaises(ToolError) as ctx:
            await tools.call("mycelic_verify", {"memory_id": self.ids["log-1"]})
        self.assertIn("no visible memory", str(ctx.exception))
        # GET /verify/ matches no route: an empty or missing id is refused here, not taken for an older server
        for args in ({"memory_id": ""}, {}, {"memory_id": None}, {"memory_id": 7}):
            with self.subTest(args=args):
                with self.assertRaises(ToolError) as ctx:
                    await tools.call("mycelic_verify", args)
                self.assertEqual(str(ctx.exception), "'memory_id' must be a non-empty string")
        old = TestServer(web.Application(), host="127.0.0.1")
        await old.start_server()
        try:
            stale_server = mcp.ProxyTools(MycelicClient(str(old.make_url("")).rstrip("/"), key, retries=0))
            with self.assertRaises(ToolError) as ctx:
                await stale_server.call("mycelic_verify", {"memory_id": cid})
            self.assertIn("predates downward verification", str(ctx.exception))
            self.assertFalse(str(ctx.exception).startswith("ToolError:"), "not wrapped a second time")
            # the other tools keep their behaviour against it
            with self.assertRaises(ToolError) as ctx:
                await stale_server.call("mycelic_lineage", {"memory_id": cid})
            self.assertTrue(str(ctx.exception).startswith("MycelicError: HTTP 404"), str(ctx.exception))
        finally:
            await old.close()


class LocalMemoryTests(unittest.IsolatedAsyncioTestCase):
    """A note leaves the agent's disk only when the agent asked to share it, at the visibility it asked for."""

    async def asyncSetUp(self) -> None:
        self.h = await ServiceHarness().start()
        self.server = TestServer(create_app(self.h.service), host="127.0.0.1")
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")
        await self.h.register("bot-1", team="logistics")
        await self.h.register("bot-2", team="logistics")
        await self.h.settle()
        self.db = os.path.join(self.h.tmp.name, "bot-1-local.db")

    async def asyncTearDown(self) -> None:
        await self.server.close()
        await self.h.close()

    def local(self) -> LocalMemory:
        local = LocalMemory(self.db)
        self.addCleanup(local.close)
        return local

    async def share(self, local_id: str, **kwargs: Any) -> dict[str, Any]:
        """``LocalMemory.share`` as bot-1 against this server, from a thread with its own handle on the local store."""
        def run() -> dict[str, Any]:
            local = LocalMemory(self.db)
            try:
                return local.share(MycelicClient(self.url, self.h.keys["bot-1"], retries=0), local_id, **kwargs)
            finally:
                local.close()

        res = await asyncio.to_thread(run)
        await self.h.settle()
        return res

    async def run_agent(self, *observations: dict[str, Any]) -> list[str]:
        """One run of the reference agent process for bot-1 (with these observations, if any); its log lines."""
        argv = ["--url", self.url, "--api-key", self.h.keys["bot-1"], "--local-db", self.db]
        if observations:
            path = Path(self.h.tmp.name) / f"obs-{len(os.listdir(self.h.tmp.name))}.jsonl"
            path.write_text("".join(json.dumps(o) + "\n" for o in observations), encoding="utf-8")
            argv += ["--observations", str(path)]
        out = io.StringIO()

        def run() -> int:
            with contextlib.redirect_stdout(out):
                return reference_agent.main(argv)

        self.assertEqual(await asyncio.to_thread(run), 0)
        await self.h.settle()
        return out.getvalue().splitlines()

    def held(self) -> list[tuple[str, str]]:
        """(visibility, text) of every memory bot-1 produced, whatever its status."""
        return sorted((m.visibility, m.text) for m in self.h.service.store.list_memories("northwind", status=None, limit=100)
                      if m.producer_id == "bot-1")

    async def test_a_note_never_shared_stays_local_when_the_agent_starts(self) -> None:
        local = self.local()
        local.note("CFO said we will miss Q4 guidance; do not share", topic="finance:q4")
        lines = await self.run_agent()
        self.assertEqual(self.held(), [])
        self.assertFalse([line for line in lines if "re-sent" in line], lines)
        self.assertEqual(local.pending(), [])
        self.assertEqual(local.counts(), {"local": 1, "shared": 0, "local_only": 1})

    async def test_a_reused_local_id_never_publishes_the_earlier_note(self) -> None:
        await self.run_agent({"text": "PRIVATE salary data", "share": False, "local_id": "n1"})
        lines = await self.run_agent({"text": "public: dock 4 closed", "share": True, "local_id": "n1", "visibility": "org"})
        self.assertEqual(self.held(), [])
        self.assertTrue([line for line in lines if "observation skipped" in line and "'n1'" in line], lines)
        # the same note again under its id is the same note: a replayed observations file shares it once
        obs = {"text": "Dock 4 closed.", "share": True, "local_id": "n2", "visibility": "org"}
        await self.run_agent(obs)
        await self.run_agent(obs)
        self.assertEqual(self.held(), [("org", "Dock 4 closed.")])
        # and a share:false observation under an id that was shared is not turned private, nor shared again with new text
        await self.run_agent({"text": "Dock 4 closed.", "share": False, "local_id": "n2"})
        await self.run_agent({"text": "Dock 4 is open again.", "share": True, "local_id": "n2"})
        self.assertEqual(self.held(), [("org", "Dock 4 closed.")])

    async def test_note_and_share_refuse_what_would_leak(self) -> None:
        local = self.local()
        private = local.note("PRIVATE salary data", tags=["private"], local_id="p1")
        with mock.patch.object(MycelicClient, "_request") as spy:
            with self.assertRaises(ValueError):
                await self.share(private, visibility="org")
            spy.assert_not_called()
        self.assertEqual((local.pending(), self.held()), ([], []))
        with self.assertRaises(ValueError):
            local.note("public: dock 4 closed", local_id="p1")
        self.assertEqual(local.get("p1")["text"], "PRIVATE salary data")
        self.assertEqual(local.note("PRIVATE salary data", tags=["private"], local_id="p1"), "p1")

    async def test_an_unacknowledged_share_is_resent_as_it_was_asked_for(self) -> None:
        local = self.local()
        nid = local.note("Dock 4 closed.", topic="ops:docks")
        local.note("Never asked to share.", topic="ops:docks")
        down = MycelicClient("http://127.0.0.1:9", self.h.keys["bot-1"], retries=0)
        with self.assertRaises(MycelicError):
            local.share(down, nid, visibility="org", expires_at="2030-01-01T00:00:00+00:00")
        pending = local.pending()
        self.assertEqual([(n["local_id"], n["share_request"]) for n in pending],
                         [(nid, {"visibility": "org", "expires_at": "2030-01-01T00:00:00+00:00", "supersedes": None})])
        lines = await self.run_agent()
        self.assertTrue([line for line in lines if f"re-sent unacknowledged note {nid}" in line], lines)
        self.assertEqual(self.held(), [("org", "Dock 4 closed.")])
        m = next(m for m in self.h.service.store.list_memories("northwind", limit=100) if m.producer_id == "bot-1")
        self.assertEqual((m.local_ref, m.expires_at), (nid, "2030-01-01T00:00:00+00:00"))
        self.assertEqual(local.pending(), [])
        await self.run_agent()                       # acknowledged: nothing is sent again
        self.assertEqual(self.held(), [("org", "Dock 4 closed.")])

    async def test_a_store_from_an_earlier_sdk_keeps_its_unshared_notes_local(self) -> None:
        import sqlite3
        c = sqlite3.connect(self.db)
        c.executescript("""
            CREATE TABLE notes (
                local_id   TEXT PRIMARY KEY,
                text       TEXT NOT NULL,
                topic      TEXT, slot TEXT, entity TEXT,
                kind       TEXT NOT NULL DEFAULT 'observation',
                confidence REAL NOT NULL DEFAULT 0.8,
                tags       TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                shared_memory_id TEXT, shared_event_id TEXT, shared_at TEXT
            );
            INSERT INTO notes(local_id, text, topic, created_at) VALUES ('old-1', 'Kept locally before the upgrade.', 'ops:x',
                                                                         '2026-09-01T00:00:00+00:00');
        """)
        c.close()
        local = self.local()
        self.assertEqual((local.get("old-1")["share_request"], local.pending()), (None, []))
        await self.run_agent()
        self.assertEqual(self.held(), [])
        await self.share("old-1")
        self.assertEqual(self.held(), [("team", "Kept locally before the upgrade.")])


if __name__ == "__main__":
    unittest.main()
