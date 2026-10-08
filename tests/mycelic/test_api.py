"""HTTP API and MCP endpoint over aiohttp's test client: authentication, authorization, limits, health, metrics,
memory/query/lineage routes, admin routes and the per-request MCP identity; downward verification on every surface
(GET /verify/{id} with its errors and four verdicts, the opt-in "verify" of POST /query, the mycelic_verify tool), its
cost-weighted rate limiting over REST and inside MCP batches, and its documentation; the per-organization note limit's
507 over REST, the SDK (which never retries it) and MCP."""
from __future__ import annotations

import asyncio
import itertools
import json
import re
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

from mycelic import mcp, verification
from mycelic.api import create_app
from mycelic.auth import RateLimiter
from mycelic.models import now_iso, utcnow
from mycelic.sdk import MycelicClient, MycelicError
from mycelic.service import RateLimited

from .helpers import ADMIN_TOKEN, DEMO_RULE, ServiceHarness, full_reaggregation_pass
from .test_integrity import boot
from .test_verification import DEMO_TEXTS, World, demo

ROOT = Path(__file__).resolve().parents[2]
#: sentences SECURITY.md (all four) and docs/MYCELIC_ARCHITECTURE.md (the first two) state verbatim
POLICY = {
    "P1": ("Consolidations above team level quote only notes marked `visibility: org` and rule conclusions; team-visibility "
           "notes are counted there, never quoted, and no consolidation adds an agent id to what it quotes."),
    "P2": ("Text of a memory that is not active (superseded or retracted) is returned only to its producer and to "
           "administrators; everyone else who may read the memory gets an empty `text` and `text_withheld` set to its status."),
    "P3": ("A rule whose conclusion template quotes `{slot:...}` publishes the quoted evidence, whatever its visibility, at "
           "the rule's target layer and, through consolidations of the conclusion's topic, at every layer above it."),
    "P4": "Retraction withdraws a note from answers but does not erase it.",
}


#: the four notes of MemoryRoutesTests' scenario, by producer
NOTES = {
    "log-1": "Port of Rotterdam terminal 3 strike announced for weeks 41-43.",
    "log-2": "Carrier ETA for the SD-9 container slipped by 12 days.",
    "proc-1": "Kessler Antriebe has two weeks of SD-9 inventory left.",
    "sales-1": "Helios Automation committed to 40 RX-4 arms for November.",
}
#: what every caller's verification report carries (an administrator's also has ``as_of``)
REPORT_KEYS = {"memory_id", "verdict", "derived_correctly", "still_true", "reasons", "warnings", "summary", "superseded_by",
               "current_version", "freshness_partial", "integrity_mode", "verified_at", "max_leaf_age", "nodes", "dag_digest",
               "report_digest", "valid_until", "valid_until_partial"}
TOOL_NAMES = ["mycelic_query", "mycelic_remember", "mycelic_lineage", "mycelic_verify", "mycelic_get_memory", "mycelic_status",
              "mycelic_attest"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def verify_audits(service) -> list[dict]:
    return [a for a in service.store.recent_audit(100_000) if a["action"] == "memory.verify"]


def codes(report: dict) -> list[str]:
    return [r["code"] for r in report["reasons"]]


def normalised(rel: str) -> str:
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())


class ApiTestCase(unittest.IsolatedAsyncioTestCase):
    harness_overrides: dict = {}

    async def asyncSetUp(self) -> None:
        self.h = await ServiceHarness(**self.harness_overrides).start()
        self.client = TestClient(TestServer(create_app(self.h.service)))
        await self.client.start_server()
        self.admin = bearer(ADMIN_TOKEN)

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.h.close()

    async def register(self, agent_id: str, **units: str) -> str:
        body = {"enterprise": "northwind", "region": "emea", "subsidiary": "nw-gmbh", "department": "ops", "team": "logistics",
                "agent_id": agent_id, **units}
        r = await self.client.post("/admin/agents", json=body, headers=self.admin)
        self.assertEqual(r.status, 201, await r.text())
        data = await r.json()
        self.assertTrue(data["api_key"].startswith(f"mk_{agent_id}."))
        return data["api_key"]


class AuthAndLimitsTests(ApiTestCase):
    async def test_unauthenticated_and_malformed_requests(self) -> None:
        r = await self.client.post("/memory", json={"text": "x"})
        self.assertEqual(r.status, 401)
        self.assertIn("Bearer", r.headers.get("WWW-Authenticate", ""))
        r = await self.client.post("/memory", data="text=1", headers={**bearer("mk_a.b"), "Content-Type": "text/plain"})
        self.assertEqual(r.status, 415)
        r = await self.client.get("/whoami", headers=bearer("mk_nobody.secret"))
        self.assertEqual(r.status, 401)
        r = await self.client.get("/whoami", headers=bearer("é"))
        self.assertEqual(r.status, 401, "non-ASCII tokens are rejected, not crashed on")
        r = await self.client.get("/admin/agents", headers=bearer(ADMIN_TOKEN))
        self.assertEqual(r.status, 200)
        big = "x" * (self.h.settings.max_body_bytes + 10)
        r = await self.client.post("/memory", data=big, headers={**self.admin, "Content-Type": "application/json"})
        self.assertEqual(r.status, 413)

    async def test_admin_cannot_write_and_agents_cannot_administer(self) -> None:
        key = await self.register("log-1")
        r = await self.client.post("/memory", json={"text": "admin note"}, headers=self.admin)
        self.assertEqual(r.status, 403)
        r = await self.client.post("/admin/agents", json={"enterprise": "northwind", "agent_id": "evil"}, headers=bearer(key))
        self.assertEqual(r.status, 403)
        r = await self.client.post("/admin/agents", json={"enterprise": "northwind", "agent_id": "x", "scopes": ["admin"]}, headers=self.admin)
        self.assertEqual(r.status, 400)
        r = await self.client.post("/admin/agents", json={"enterprise": "northwind", "agent_id": "log-1"}, headers=self.admin)
        self.assertEqual(r.status, 400)

    async def test_health_ready_metrics(self) -> None:
        r = await self.client.get("/health")
        self.assertEqual(r.status, 200)
        pub = await r.json()
        self.assertEqual(set(pub), {"status", "version", "transport_connected", "consumer_running"})
        r = await self.client.get("/health", headers=self.admin)
        full = await r.json()
        self.assertIn("checks", full)
        self.assertIn("stats", full)
        r = await self.client.get("/ready")
        self.assertEqual(r.status, 200)
        self.assertTrue((await r.json())["ready"])
        r = await self.client.get("/admin/status", headers=self.admin)
        self.assertEqual((await r.json())["settings"]["admin_token"], "set")
        r = await self.client.get("/metrics")
        self.assertEqual(r.status, 200, "loopback bind: metrics scrapable without a token")
        text = await r.text()
        self.assertIn("mycelic_memories{layer=\"agent\"}", text)
        self.assertIn("mycelic_http_requests_total", text)
        self.assertIn("mycelic_consumer_pending 0.0", text)
        # a wrong token on the public /health still yields the public view and is counted
        before = self.h.service.metrics.auth_failures.labels("invalid_token")._value.get()
        r = await self.client.get("/health", headers=bearer("mk_nobody.wrong"))
        self.assertEqual(set(await r.json()), {"status", "version", "transport_connected", "consumer_running"})
        self.assertEqual(self.h.service.metrics.auth_failures.labels("invalid_token")._value.get(), before + 1)

    async def test_rotate_and_revoke(self) -> None:
        key = await self.register("log-1")
        r = await self.client.post("/admin/agents/log-1/rotate", headers=self.admin)
        new_key = (await r.json())["api_key"]
        self.assertEqual((await self.client.get("/whoami", headers=bearer(key))).status, 401)
        self.assertEqual((await self.client.get("/whoami", headers=bearer(new_key))).status, 200)
        r = await self.client.delete("/admin/agents/log-1", headers=self.admin)
        self.assertEqual(r.status, 200)
        self.assertEqual((await self.client.get("/whoami", headers=bearer(new_key))).status, 403)


class RateLimitTests(ApiTestCase):
    harness_overrides = {"rate_limit_rps": 1.0, "rate_limit_burst": 3, "trust_proxy_headers": True}

    async def test_per_principal_and_per_peer_buckets(self) -> None:
        statuses = [(await self.client.get("/whoami", headers=self.admin)).status for _ in range(5)]
        self.assertIn(429, statuses)

    async def test_forwarded_for_uses_the_proxy_observed_entry(self) -> None:
        # the leftmost entry is client-chosen; rotating it must not buy a fresh bucket
        statuses = []
        for i in range(6):
            h = {**bearer("mk_nobody.x"), "X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"}
            statuses.append((await self.client.get("/whoami", headers=h)).status)
        self.assertIn(429, statuses)
        self.assertEqual(statuses[:3], [401, 401, 401])

    async def test_mcp_batch_is_capped_and_charged(self) -> None:
        key = await self.register("log-1")
        big = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(self.h.settings.max_batch + 1)]
        r = await self.client.post("/mcp", json=big, headers={**bearer(key), "Accept": "application/json"})
        self.assertEqual(r.status, 400)
        self.assertEqual((await r.json())["error"]["code"], -32600)
        # a fresh principal (each request from a different proxy-observed address so the address buckets do not
        # interfere): burst 3 covers a 2-message batch plus one request, the next request is refused
        key2 = await self.register("log-2")
        xff = lambda n: {"X-Forwarded-For": f"198.51.100.{n}"}  # noqa: E731
        r = await self.client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}],
                                   headers={**bearer(key2), "Accept": "application/json", **xff(1)})
        self.assertEqual(r.status, 200)
        self.assertEqual((await self.client.get("/whoami", headers={**bearer(key2), **xff(2)})).status, 200)
        self.assertEqual((await self.client.get("/whoami", headers={**bearer(key2), **xff(3)})).status, 429)


async def wide_world(test: unittest.IsolatedAsyncioTestCase, notes: int = 100, **overrides: Any) -> tuple[World, Any]:
    """test_wide_fan_in_verifies_within_budget's fast path, 10 agents x ``notes`` org-visible notes: the world and the
    enterprise consolidation, whose DAG has ``10 * notes + 5`` nodes."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    w = World(tmp.name, **overrides)
    test.addAsyncCleanup(w.close)
    await boot(w.service)
    s = w.service
    for j in range(10):
        await w.register(f"a{j}", team="t1", department="acme", subsidiary="acme", region="acme", enterprise="acme")
    await w.settle()
    for j in range(10):
        p = w.principal(f"a{j}")
        for n in range(notes):
            await s.ingest_memory(p, {"text": f"note {n} by a{j}", "topic": "supply:x", "visibility": "org", "confidence": 0.6})
    now = now_iso()
    async with s.store.transaction() as tx:
        for ev in s.store.pending_events(1_000_000):
            w.log.append({})
            tx.mark_applied(ev.event_id, len(w.log), now)
            tx.set_applied(ev.payload["memory_id"], now)
    await full_reaggregation_pass(s)
    await w.settle()
    [top] = s.store.list_memories("acme", layers=["enterprise"])
    return w, top


class VerifyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_never_holds_the_event_loop(self) -> None:
        asyncio.get_running_loop().set_debug(False)       # a timing test: without the test runner's debug bookkeeping
        w, top = await wide_world(self, 300, trust_proxy_headers=True)
        s = w.service
        s.limiter = RateLimiter(rps=50.0, burst=100)          # the shipped defaults
        for j in range(6):
            w.principal(f"a{j}")                               # each agent's last-seen touch is written now, not below
        await asyncio.sleep(0.05)
        s.store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")    # nor the checkpoint of the build's writes
        peers = itertools.count()

        def headers(agent_id: str) -> dict[str, str]:
            # a new proxy-observed address per request, so only the principal's bucket counts
            n = next(peers)
            return {**bearer(w.keys[agent_id]), "X-Forwarded-For": f"203.0.{n // 250}.{n % 250}"}

        t0 = time.perf_counter()
        walk = verification.verify(s.store, s.aggregator.planner(), s.keyring, top.memory_id, principal=w.admin,
                                   now=utcnow(), max_nodes=s.settings.verify_max_nodes)
        walk_seconds = time.perf_counter() - t0
        self.assertEqual(walk.report["summary"]["nodes"], 3005)
        client = TestClient(TestServer(create_app(s)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        audits = len(verify_audits(s))
        gaps: list[float] = []
        done = asyncio.Event()

        async def ticker() -> None:
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.002)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        tick = asyncio.ensure_future(ticker())
        health: list[float] = []

        async def probe() -> None:
            while not done.is_set():
                t = time.perf_counter()
                r = await client.get("/health")
                self.assertEqual(r.status, 200)
                health.append(time.perf_counter() - t)
                await asyncio.sleep(0.01)

        probing = asyncio.ensure_future(probe())
        # one MCP batch of 100 verifications of the 3005-node DAG by one agent, and five agents verifying it at once
        batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call",
                  "params": {"name": "mycelic_verify", "arguments": {"memory_id": top.memory_id}}} for i in range(100)]
        t0 = time.perf_counter()
        responses = await asyncio.gather(
            client.post("/mcp", json=batch, headers={**headers("a0"), "Accept": "application/json"}),
            *[client.get(f"/verify/{top.memory_id}", headers=headers(f"a{j}")) for j in range(1, 6)])
        elapsed = time.perf_counter() - t0
        done.set()
        await asyncio.gather(tick, probing)
        results = [m["result"] for m in await responses[0].json()]
        walked = sum(not x["isError"] for x in results)
        self.assertEqual([x["content"][0]["text"] for x in results if x["isError"]], ["rate limit exceeded"] * (100 - walked))
        # each walk is charged at least twice what the bucket refills while it runs, so the batch is refused after a few
        self.assertLessEqual(walked, 10, f"{walked} of 100 walked")
        self.assertEqual([r.status for r in responses[1:]], [200] * 5)
        self.assertEqual(len(verify_audits(s)) - audits, walked + 5)
        # the walks ran in worker threads: the loop kept turning and /health kept answering throughout
        self.assertGreater(len(health), 0)
        self.assertLess(max(gaps), walk_seconds / 2, f"the loop stalled {max(gaps):.3f}s (one walk takes {walk_seconds:.3f}s)")
        self.assertLess(max(health), walk_seconds / 2 + 0.05)
        self.assertLess(elapsed, 20 * walk_seconds + 5)

    async def test_mcp_verification_is_bounded_and_query_can_verify(self) -> None:
        w, top = await wide_world(self, 50)
        s = w.service
        client = TestClient(TestServer(create_app(s)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {**bearer(w.keys["a1"]), "Accept": "application/json"}

        async def call(name: str, **arguments) -> tuple[dict, int]:
            r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                "params": {"name": name, "arguments": arguments}}, headers=headers)
            self.assertEqual(r.status, 200)
            body = await r.read()
            return json.loads(body)["result"], len(body)

        full = await s.verify(w.principal("a1"), top.memory_id)
        self.assertEqual((full["verdict"], full["summary"]["nodes"]), ("verified", 505))
        # the default: every key of the report, the nodes that did not pass (none here), a few kilobytes
        out, size = await call("mycelic_verify", memory_id=top.memory_id)
        self.assertFalse(out["isError"], out)
        report = out["structuredContent"]
        self.assertLess(size, 10_000)
        self.assertEqual((report["detail"], report["nodes"], report["nodes_omitted"], report["warnings_omitted"]),
                         ("summary", [], 505, 0))
        # each call verifies anew, so verified_at (to the second) may differ; every other key is the same
        same = [k for k in full if k not in ("nodes", "verified_at")]
        self.assertEqual({k: report[k] for k in same}, {k: full[k] for k in same})
        self.assertGreaterEqual(report["verified_at"], full["verified_at"])
        out, size = await call("mycelic_verify", memory_id=top.memory_id, detail="full")
        self.assertEqual(len(out["structuredContent"]["nodes"]), 505)
        self.assertGreater(size, 100_000)
        out, _ = await call("mycelic_verify", memory_id=top.memory_id, detail="everything")
        self.assertTrue(out["isError"])
        self.assertEqual(out["content"][0]["text"], "'detail' must be 'summary' or 'full'")
        # a failing node is listed, at most SUMMARY_ITEMS of them, upper layers first
        leaves = [n["memory_id"] for n in full["nodes"] if n["layer"] == "agent"]
        s.store._conn.execute(f"UPDATE memories SET text=text||'!' WHERE memory_id IN ({','.join('?' * 60)})", leaves[:60])
        out, size = await call("mycelic_verify", memory_id=top.memory_id)
        report = out["structuredContent"]
        self.assertEqual(report["verdict"], "failed")
        self.assertEqual(len(report["nodes"]), mcp.SUMMARY_ITEMS)
        self.assertEqual(report["nodes_omitted"], 505 - mcp.SUMMARY_ITEMS)
        self.assertTrue(all(not n["ok"] for n in report["nodes"]))
        self.assertLess(size, 50_000)
        s.store._conn.execute(f"UPDATE memories SET text=substr(text, 1, length(text) - 1) WHERE memory_id IN "
                              f"({','.join('?' * 60)})", leaves[:60])
        # mycelic_query answers the verdict with the answer, as POST /query does
        out, _ = await call("mycelic_query", query="note", scope="acme", min_layer="enterprise", verify=True)
        self.assertFalse(out["isError"], out)
        answer = out["structuredContent"]["answer"]
        self.assertEqual(answer["memory_id"], top.memory_id)
        self.assertEqual(answer["verification"], {"verdict": "verified", "derived_correctly": True, "still_true": True,
                                                  "reasons": []})
        out, _ = await call("mycelic_query", query="note", scope="acme", min_layer="enterprise")
        self.assertNotIn("verification", out["structuredContent"]["answer"])
        out, _ = await call("mycelic_query", query="note", scope="acme", verify="yes")
        self.assertTrue(out["isError"])
        # the stdio proxy does the same over HTTP
        server = TestServer(create_app(s), host="127.0.0.1")
        await server.start_server()
        self.addAsyncCleanup(server.close)
        proxy = mcp.ProxyTools(MycelicClient(str(server.make_url("")).rstrip("/"), w.keys["a1"], retries=0))
        report = await proxy.call("mycelic_verify", {"memory_id": top.memory_id})
        self.assertEqual((report["detail"], report["nodes"], report["nodes_omitted"]), ("summary", [], 505))
        report = await proxy.call("mycelic_verify", {"memory_id": top.memory_id, "detail": "full"})
        self.assertEqual(len(report["nodes"]), 505)
        res = await proxy.call("mycelic_query", {"query": "note", "scope": "acme", "min_layer": "enterprise", "verify": True})
        self.assertEqual(res["answer"]["verification"]["verdict"], "verified")

    async def test_verify_is_cost_weighted_by_the_rate_limiter(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        w = World(tmp.name, trust_proxy_headers=True)
        self.addAsyncCleanup(w.close)
        await boot(w.service)
        s = w.service
        # test_wide_fan_in_verifies_within_budget's fast path, 10 agents x 100 org-visible notes: a 1005-node DAG
        for j in range(10):
            await w.register(f"a{j}", team="t1", department="acme", subsidiary="acme", region="acme", enterprise="acme")
        await w.settle()
        for j in range(10):
            p = w.principal(f"a{j}")
            for n in range(100):
                await s.ingest_memory(p, {"text": f"note {n} by a{j}", "topic": "supply:x", "visibility": "org", "confidence": 0.6})
        now = now_iso()
        async with s.store.transaction() as tx:
            for ev in s.store.pending_events(1_000_000):
                w.log.append({})
                tx.mark_applied(ev.event_id, len(w.log), now)
                tx.set_applied(ev.payload["memory_id"], now)
        await full_reaggregation_pass(s)
        await w.settle()
        [top] = s.store.list_memories("acme", layers=["enterprise"])
        own = next(m for m in s.store.list_memories("acme", layers=["agent"], limit=1000) if m.producer_id == "a1")
        client = TestClient(TestServer(create_app(s)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        # one token a second, ten at most, and a clock that never moves: no refill between requests
        s.limiter = RateLimiter(rps=1.0, burst=10, clock=lambda: 0.0)
        peers = itertools.count()

        def headers(agent_id: str) -> dict[str, str]:
            # a new proxy-observed address per request, so only the principal's bucket counts
            n = next(peers)
            return {**bearer(w.keys[agent_id]), "X-Forwarded-For": f"198.51.{n // 250}.{n % 250}"}

        refused = s.metrics.auth_failures.labels("rate_limited")._value.get()
        # 1005 nodes cost 1 + ceil(1005/250) = 6 tokens: 10 -> 4 -> -2, and the third request is refused at the door
        statuses, nodes = [], []
        for _ in range(3):
            r = await client.get(f"/verify/{top.memory_id}", headers=headers("a0"))
            statuses.append(r.status)
            if r.status == 200:
                nodes.append((await r.json())["summary"]["nodes"])
        self.assertEqual(statuses, [200, 200, 429])
        self.assertEqual(nodes, [1005, 1005])
        # the debt is checked before the id is looked up, so an indebted caller learns nothing about any id
        audits = len(verify_audits(s))
        for memory_id in ("mem_doesnotexist", top.memory_id):
            with self.assertRaises(RateLimited):
                await s.verify(s.authenticate(f"Bearer {w.keys['a0']}"), memory_id)
        self.assertEqual(len(verify_audits(s)), audits)
        # a 1-node walk costs 1 + 1 = 2 tokens: five of them
        statuses = [(await client.get(f"/verify/{own.memory_id}", headers=headers("a1"))).status for _ in range(6)]
        self.assertEqual(statuses, [200, 200, 200, 200, 200, 429])
        # a batch is charged one token per message up front (3), then each walk: 7 -> 2 -> -3, and the third verify
        # message finds the bucket in debt and is refused without walking
        batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call",
                  "params": {"name": "mycelic_verify", "arguments": {"memory_id": top.memory_id}}} for i in (1, 2, 3)]
        audits = len(verify_audits(s))
        r = await client.post("/mcp", json=batch, headers={**headers("a2"), "Accept": "application/json"})
        self.assertEqual(r.status, 200)
        results = [m["result"] for m in await r.json()]
        reports = [x["structuredContent"] for x in results if not x["isError"]]
        self.assertEqual([x["summary"]["nodes"] for x in reports], [1005, 1005])
        self.assertEqual([x["content"][0]["text"] for x in results if x["isError"]], ["rate limit exceeded"])
        self.assertEqual([a["principal"] for a in verify_audits(s)[: len(verify_audits(s)) - audits]], ["a2", "a2"])
        self.assertEqual((await client.get("/whoami", headers=headers("a2"))).status, 429)
        self.assertGreater(s.metrics.auth_failures.labels("rate_limited")._value.get(), refused)
        # two concurrent requests both pass the door (4 -> 3 -> 2) while a transaction holds the store lock; the first
        # walk takes 5 (-> -3) and the second request finds the debt under the lock: 429, neither walked nor audited
        key = s.authenticate(f"Bearer {w.keys['a3']}").limiter_key
        s.limiter.take(key, 6)
        audits = len(verify_audits(s))
        async with s.store._lock:
            racing = [asyncio.ensure_future(client.get(f"/verify/{top.memory_id}", headers=headers("a3"))) for _ in range(2)]
            for _ in range(500):
                if s.limiter._buckets[key].tokens == 2.0:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(s.limiter._buckets[key].tokens, 2.0, "both requests passed the middleware")
        responses = await asyncio.gather(*racing)
        self.assertEqual(sorted(r.status for r in responses), [200, 429])
        self.assertEqual([await r.json() for r in responses if r.status == 429], [{"error": "rate limit exceeded"}])
        self.assertEqual(len(verify_audits(s)), audits + 1)


class HostAllowListTests(ApiTestCase):
    harness_overrides = {"allowed_hosts": ["mycelic.example.com"]}

    async def test_probes_bypass_the_host_allow_list_but_api_routes_do_not(self) -> None:
        probe = {"Host": "127.0.0.1:8080"}
        self.assertEqual((await self.client.get("/health", headers=probe)).status, 200)
        self.assertEqual((await self.client.get("/ready", headers=probe)).status, 200)
        self.assertEqual((await self.client.get("/whoami", headers={**probe, **self.admin})).status, 421)
        self.assertEqual((await self.client.get("/whoami", headers={"Host": "mycelic.example.com", **self.admin})).status, 200)


class ScopeTests(ApiTestCase):
    async def test_scopes_are_enforced_per_route(self) -> None:
        writer = await self.register("w-1")
        r = await self.client.post("/memory", json={"text": "note", "topic": "t"}, headers=bearer(writer))
        mid = (await r.json())["memory_id"]
        r = await self.client.post("/admin/agents", json={"enterprise": "northwind", "region": "emea", "subsidiary": "nw-gmbh",
                                                         "department": "ops", "team": "logistics", "agent_id": "events-only",
                                                         "scopes": ["events:write"]}, headers=self.admin)
        eo = (await r.json())["api_key"]
        self.assertEqual((await self.client.get(f"/memory/{mid}", headers=bearer(eo))).status, 403)
        self.assertEqual((await self.client.get("/memories?scope=northwind", headers=bearer(eo))).status, 403)
        self.assertEqual((await self.client.post("/query", json={"query": "note"}, headers=bearer(eo))).status, 403)
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "mycelic_get_memory", "arguments": {"memory_id": mid}}},
                                   headers={**bearer(eo), "Accept": "application/json"})
        self.assertTrue((await r.json())["result"]["isError"])
        r = await self.client.post("/admin/agents", json={"enterprise": "northwind", "region": "emea", "subsidiary": "nw-gmbh",
                                                         "department": "ops", "team": "logistics", "agent_id": "read-only",
                                                         "scopes": ["memory:read"]}, headers=self.admin)
        ro = (await r.json())["api_key"]
        self.assertEqual((await self.client.get(f"/lineage/{mid}", headers=bearer(ro))).status, 403)
        r = await self.client.post("/query", json={"query": "note"}, headers=bearer(ro))
        self.assertEqual(r.status, 200)
        self.assertIsNone((await r.json())["lineage"], "no lineage graph without lineage:read")
        r = await self.client.post("/admin/agents", json={"enterprise": "northwind", "agent_id": "no-scopes", "scopes": []}, headers=self.admin)
        self.assertEqual(r.status, 201)
        self.assertEqual((await r.json())["agent"]["scopes"], [])
        self.assertEqual((await self.client.post("/memory", json={"text": "x"}, headers=bearer((await r.json())["api_key"]))).status, 403)


class MemoryRoutesTests(ApiTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        r = await self.client.post("/admin/rules", json=DEMO_RULE, headers=self.admin)
        self.assertEqual(r.status, 201, await r.text())
        self.log1 = await self.register("log-1")
        self.log2 = await self.register("log-2")
        self.proc1 = await self.register("proc-1", team="procurement")
        self.sales1 = await self.register("sales-1", department="commercial", team="field-sales")
        self.sales2 = await self.register("sales-2", department="commercial", team="field-sales")

    async def observe(self, key: str, text: str, **fields) -> dict:
        r = await self.client.post("/memory", json={"text": text, **fields}, headers=bearer(key))
        self.assertEqual(r.status, 202, await r.text())
        return await r.json()

    async def test_memory_query_lineage_and_visibility(self) -> None:
        a = await self.observe(self.log1, "Port of Rotterdam terminal 3 strike announced for weeks 41-43.",
                               topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.9)
        await self.observe(self.log2, "Carrier ETA for the SD-9 container slipped by 12 days.",
                           topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.7)
        await self.observe(self.proc1, "Kessler Antriebe has two weeks of SD-9 inventory left.",
                           topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity="sd-9")
        await self.observe(self.sales1, "Helios Automation committed to 40 RX-4 arms for November.",
                           topic="supply:sd-9/demand", slot="demand_commitment", entity="sd-9")
        await self.h.settle()

        # own memory is readable; another team's is not even acknowledged
        self.assertEqual((await self.client.get(f"/memory/{a['memory_id']}", headers=bearer(self.log1))).status, 200)
        self.assertEqual((await self.client.get(f"/memory/{a['memory_id']}", headers=bearer(self.sales2))).status, 404)
        self.assertEqual((await self.client.get(f"/memory/{a['memory_id']}", headers=self.admin)).status, 200)

        r = await self.client.post("/query", json={"query": "delivery risk RX-4 SD-9", "scope": "northwind"}, headers=bearer(self.sales2))
        self.assertEqual(r.status, 200, await r.text())
        res = await r.json()
        self.assertEqual(res["answer"]["layer"], "enterprise")
        self.assertEqual(res["lineage"]["redacted_contributions"], 2)
        top = res["results"][0]["memory"]
        self.assertNotIn("contributing_agents", top["metadata"], "other teams' agent ids never leave through /query")
        self.assertIn("contributing_teams", top["metadata"])
        r = await self.client.post("/query", json={"query": "delivery risk", "scope": "northwind/emea/nw-gmbh/ops"}, headers=bearer(self.sales2))
        self.assertEqual(r.status, 403)
        r = await self.client.post("/query", json={"query": "delivery risk", "scope": "northwind/emea/nw-gmbh/ops"}, headers=self.admin)
        self.assertEqual(r.status, 200)

        r = await self.client.get(f"/lineage/{res['answer']['memory_id']}", headers=bearer(self.sales2))
        g = await r.json()
        self.assertTrue(g["evidence"]["reconstructable"])
        self.assertEqual(g["layers"], ["agent", "enterprise"])
        r = await self.client.get("/lineage/mem_doesnotexist", headers=bearer(self.sales2))
        self.assertEqual(r.status, 404)
        r = await self.client.get("/memories?scope=northwind&layer=enterprise", headers=bearer(self.sales2))
        self.assertEqual(len((await r.json())["memories"]), 1)
        r = await self.client.get("/memories?scope=northwind&limit=500", headers=bearer(self.sales2))
        scopes = {mm["scope"] for mm in (await r.json())["memories"]}
        self.assertIn("northwind", scopes)
        self.assertIn("northwind/emea/nw-gmbh/commercial/field-sales/sales-1", scopes)
        self.assertFalse({sc for sc in scopes if "/ops/" in sc}, "the SQL visibility rule must hide other teams' notes and consolidations")
        r = await self.client.get("/memories?scope=northwind&limit=500", headers=self.admin)
        self.assertTrue({sc for sc in {mm["scope"] for mm in (await r.json())["memories"]} if "/ops/" in sc})

        # retract through the API: only the producer (or admin)
        r = await self.client.post(f"/memory/{a['memory_id']}/retract", json={"reason": "strike called off"}, headers=bearer(self.log2))
        self.assertEqual(r.status, 403)
        r = await self.client.post(f"/memory/{a['memory_id']}/retract", json={"reason": "strike called off"}, headers=bearer(self.log1))
        self.assertEqual(r.status, 202)
        await self.h.settle()
        r = await self.client.get(f"/memory/{a['memory_id']}", headers=bearer(self.log1))
        self.assertEqual((await r.json())["memory"]["status"], "retracted")

    async def test_retracted_text_not_listed_to_other_teams(self) -> None:
        note = "Port of Rotterdam terminal 3 strike announced for weeks 41-43."
        a = await self.observe(self.log1, note, topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9",
                               confidence=0.9)
        await self.observe(self.log2, "Carrier ETA for the SD-9 container slipped by 12 days.",
                           topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.7)
        await self.observe(self.proc1, "Kessler Antriebe has two weeks of SD-9 inventory left.",
                           topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity="sd-9")
        await self.observe(self.sales1, "Helios Automation committed to 40 RX-4 arms for November.",
                           topic="supply:sd-9/demand", slot="demand_commitment", entity="sd-9")
        await self.h.settle()
        r = await self.client.get("/memories?scope=northwind&layer=enterprise", headers=self.admin)
        [conclusion] = [m for m in (await r.json())["memories"] if m["rule_id"] == DEMO_RULE["rule_id"]]
        cid, aid = conclusion["memory_id"], a["memory_id"]
        r = await self.client.post(f"/memory/{aid}/retract", json={"reason": "strike called off"}, headers=bearer(self.log1))
        self.assertEqual(r.status, 202)
        await self.h.settle()

        async def get(key: str | dict, mid: str) -> dict:
            r = await self.client.get(f"/memory/{mid}", headers=key if isinstance(key, dict) else bearer(key))
            self.assertEqual(r.status, 200, await r.text())
            return (await r.json())["memory"]

        async def listed(key: str, scope: str) -> list[dict]:
            r = await self.client.get(f"/memories?scope={scope}&status=retracted&limit=500", headers=bearer(key))
            self.assertEqual(r.status, 200, await r.text())
            return (await r.json())["memories"]

        # another team's agent: every retracted memory it may read comes without its text (B3)
        others = await listed(self.sales2, "northwind")
        self.assertIn(cid, {m["memory_id"] for m in others})
        for m in others:
            self.assertEqual((m["status"], m["text"], m["text_withheld"]), ("retracted", "", "retracted"))
            self.assertNotIn("statements", m["metadata"])
            self.assertNotIn("statement_origins", m["metadata"])
        got = await get(self.sales2, cid)
        self.assertEqual((got["status"], got["text"], got["text_withheld"]), ("retracted", "", "retracted"))
        # the producer still reads its own note, but not the conclusion that quoted it
        own = await get(self.log1, aid)
        self.assertEqual((own["status"], own["text"]), ("retracted", note))
        self.assertNotIn("text_withheld", own)
        self.assertEqual((await get(self.log1, cid))["text"], "")
        # a teammate gets an empty text for the note, by GET and by list
        mate = await get(self.log2, aid)
        self.assertEqual((mate["text"], mate["text_withheld"]), ("", "retracted"))
        [mate_listed] = [m for m in await listed(self.log2, "northwind/emea/nw-gmbh/ops/logistics") if m["memory_id"] == aid]
        self.assertEqual((mate_listed["text"], mate_listed["text_withheld"]), ("", "retracted"))
        # the administrator reads all of it
        self.assertEqual((await get(self.admin, aid))["text"], note)
        admin_c = await get(self.admin, cid)
        self.assertIn("Supply risk for sd-9", admin_c["text"])
        self.assertNotIn("text_withheld", admin_c)
        # the lineage keeps its shape
        g = await (await self.client.get(f"/lineage/{cid}", headers=bearer(self.sales2))).json()
        ga = await (await self.client.get(f"/lineage/{cid}", headers=self.admin)).json()
        self.assertEqual((g["memory"]["text"], g["memory"]["text_withheld"]), ("", "retracted"))
        self.assertEqual(set(g["nodes"]), set(ga["nodes"]))
        self.assertEqual({(e["child"], e["parent"]) for e in g["edges"]}, {(e["child"], e["parent"]) for e in ga["edges"]})
        self.assertNotIn(note, await (await self.client.get(f"/lineage/{cid}", headers=bearer(self.sales2))).text())
        # and so does MCP
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                 "params": {"name": "mycelic_get_memory", "arguments": {"memory_id": cid}}},
                                   headers={**bearer(self.sales2), "Accept": "application/json"})
        out = (await r.json())["result"]
        self.assertFalse(out["isError"], out)
        self.assertEqual((out["structuredContent"]["text"], out["structuredContent"]["text_withheld"]), ("", "retracted"))

    async def demo_notes(self) -> dict[str, str]:
        """The four notes of test_memory_query_lineage_and_visibility, settled: their ids and the enterprise conclusion's."""
        ids = {}
        for agent_id, key, fields in (
                ("log-1", self.log1, {"topic": "supply:sd-9/transport", "slot": "transport_disruption", "confidence": 0.9}),
                ("log-2", self.log2, {"topic": "supply:sd-9/transport", "slot": "transport_disruption", "confidence": 0.7}),
                ("proc-1", self.proc1, {"topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low"}),
                ("sales-1", self.sales1, {"topic": "supply:sd-9/demand", "slot": "demand_commitment"})):
            ids[agent_id] = (await self.observe(key, NOTES[agent_id], entity="sd-9", **fields))["memory_id"]
        await self.h.settle()
        [conclusion] = self.h.service.store.list_memories("northwind", layers=["enterprise"])
        ids["conclusion"] = conclusion.memory_id
        return ids

    async def register_reader(self) -> str:
        """An agent of the field-sales team that may read memories but not lineage."""
        r = await self.client.post("/admin/agents", json={"enterprise": "northwind", "region": "emea", "subsidiary": "nw-gmbh",
                                                         "department": "commercial", "team": "field-sales",
                                                         "agent_id": "reader-1", "scopes": ["memory:read"]}, headers=self.admin)
        self.assertEqual(r.status, 201, await r.text())
        return (await r.json())["api_key"]

    async def test_verify_route_auth_errors_and_shape(self) -> None:
        # every principal before the notes, so no registry change re-plans anything under them
        reader = await self.register_reader()
        revoked = await self.register("sales-3", department="commercial", team="field-sales")
        self.assertEqual((await self.client.delete("/admin/agents/sales-3", headers=self.admin)).status, 200)
        r = await self.client.post("/admin/agents", json={"enterprise": "acme", "agent_id": "acme-1"}, headers=self.admin)
        acme = (await r.json())["api_key"]
        ids = await self.demo_notes()
        s, cid = self.h.service, ids["conclusion"]
        self.assertIn("/verify/{id}", (await (await self.client.get("/")).json())["endpoints"])

        async def get(key: str | dict | None, memory_id: str, **params: str):
            headers = key if isinstance(key, dict) else bearer(key) if key else {}
            return await self.client.get(f"/verify/{memory_id}", params=params, headers=headers)

        # refusals, none of which walks, counts or audits anything
        audits = len(verify_audits(s))
        r = await get(None, cid)
        self.assertEqual(r.status, 401)
        self.assertIn("Bearer", r.headers.get("WWW-Authenticate", ""))
        r = await get(reader, cid)
        self.assertEqual((r.status, await r.json()), (403, {"error": "missing scope lineage:read"}))
        r = await get(revoked, cid)
        self.assertEqual((r.status, await r.json()), (403, {"error": "agent revoked"}))
        # another team's raw note, an id that does not exist and another organization's conclusion: one answer
        for key, memory_id in ((self.sales2, ids["log-1"]), (self.sales2, "mem_doesnotexist"), (acme, cid), (self.sales2, "x" * 200)):
            with self.subTest(memory_id=memory_id[:40]):
                r = await get(key, memory_id)
                self.assertEqual((r.status, await r.json()), (404, {"error": f"no visible memory with id {memory_id}"}))
        for raw in ("abc", "0", "-1", "1.5", "1e3", "", "+5", " 5", "315360001", "9" * 12, "9" * 13):
            with self.subTest(max_leaf_age=raw):
                r = await get(self.sales2, cid, max_leaf_age=raw)
                self.assertEqual(r.status, 400, await r.text())
                self.assertEqual((await r.json())["error"],
                                 f"'max_leaf_age' must be an integer between 1 and {verification.MAX_LEAF_AGE_SECONDS} seconds")
        # aiohttp decodes %2F inside {id}: every id reaches the service's check, which answers 400, never 500
        for raw in ("a%20b", "a%2Fb", "%C3%A9", "a%3Fb", "a%23b", "a%25b", "x" * 201):
            with self.subTest(memory_id=raw[:40]):
                r = await get(self.sales2, raw)
                self.assertEqual(r.status, 400, await r.text())
                self.assertIn("'memory_id' must be an id", (await r.json())["error"])
        self.assertEqual(len(verify_audits(s)), audits, "refused calls are not audited")
        for raw in ("1", "315360000"):
            with self.subTest(max_leaf_age=raw):
                r = await get(self.sales2, cid, max_leaf_age=raw)
                self.assertEqual(r.status, 200, await r.text())
                self.assertEqual((await r.json())["max_leaf_age"], int(raw))
        self.assertEqual(len(verify_audits(s)), audits + 2)

        # verified, for an agent of another team: the shape, the redaction and the service's own answer
        r = await get(self.sales2, cid)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        body = await r.text()
        report = json.loads(body)
        self.assertEqual(set(report), REPORT_KEYS)
        self.assertEqual((report["verdict"], report["derived_correctly"], report["still_true"], report["summary"]["redacted"]),
                         ("verified", True, True, 2))
        self.assertEqual(report["report_digest"], (await s.verify(s.authenticate(f"Bearer {self.sales2}"), cid))["report_digest"])
        for hidden in (NOTES["log-1"], NOTES["log-2"], NOTES["proc-1"], "log-1", "log-2", "proc-1"):
            self.assertNotIn(hidden, body)
        r = await get(self.admin, cid)
        admin_report = await r.json()
        self.assertEqual(set(admin_report), REPORT_KEYS | {"as_of"})
        self.assertEqual((admin_report["verdict"], admin_report["summary"]["redacted"]), ("verified", 0))
        self.assertEqual(admin_report["report_digest"], (await s.verify(self.h.admin, cid))["report_digest"])
        self.assertEqual([a["principal"] for a in verify_audits(s)[:4]], ["admin", "admin", "sales-2", "sales-2"])

        # stale: two days on, every note sales-2 may read is older than a second; the hidden ones are not judged
        with mock.patch("mycelic.service.utcnow", return_value=utcnow() + timedelta(days=2)):
            r = await get(self.sales2, cid, max_leaf_age="1")
        self.assertEqual(r.status, 200)
        stale = await r.json()
        self.assertEqual((stale["verdict"], stale["derived_correctly"], stale["still_true"]), ("stale", True, False))
        self.assertIn("leaf_stale", codes(stale))
        self.assertTrue(stale["freshness_partial"])
        # unverifiable: the walk stops at MYCELIC_VERIFY_MAX_NODES
        budget = s.settings.verify_max_nodes
        s.settings.verify_max_nodes = 1
        try:
            r = await get(self.sales2, cid)
        finally:
            s.settings.verify_max_nodes = budget
        self.assertEqual(r.status, 200)
        unverifiable = await r.json()
        self.assertEqual((unverifiable["verdict"], unverifiable["derived_correctly"], unverifiable["still_true"]),
                         ("unverifiable", None, None))
        self.assertIn("walk_truncated", codes(unverifiable))
        self.assertLessEqual(unverifiable["summary"]["nodes"], 1)
        # failed, last (the row stays tampered): sales-2 learns that a node it may not read failed, never why
        s.store._conn.execute("UPDATE memories SET text=text||'!' WHERE memory_id=?", (ids["log-1"],))
        r = await get(self.sales2, cid)
        self.assertEqual(r.status, 200)
        failed = await r.json()
        self.assertEqual((failed["verdict"], failed["derived_correctly"], failed["still_true"]), ("failed", False, None))
        [node] = [n for n in failed["nodes"] if n["memory_id"] == ids["log-1"]]
        self.assertEqual((node["redacted"], node["ok"], node["reasons"]), (True, False, [{"code": "hidden_error", "severity": "E"}]))
        admin_failed = await (await get(self.admin, cid)).json()
        [node] = [n for n in admin_failed["nodes"] if n["memory_id"] == ids["log-1"]]
        self.assertIn("integrity_mismatch", codes(node))
        self.assertEqual(len(verify_audits(s)), audits + 10)

    async def test_metrics_count_verification_codes_as_the_caller_sees_them(self) -> None:
        # /metrics answers any agent key when MYCELIC_METRICS_TOKEN is unset: diffing it around its own verification must
        # not tell an agent why a node it may not read failed, while the audit row keeps every code for administrators
        ids = await self.demo_notes()
        self.assertEqual((await self.client.delete("/admin/agents/proc-1", headers=self.admin)).status, 200)
        await self.h.settle()                                                     # proc-1's note: producer_revoked
        s, cid = self.h.service, ids["conclusion"]
        s.store._conn.execute("UPDATE memories SET text=text||'!' WHERE memory_id=?", (ids["log-1"],))
        pattern = re.compile(r'^mycelic_verification_reasons_total\{reason="([^"]+)"\} (\S+)$', re.M)

        async def counted(headers: dict[str, str]) -> tuple[dict, dict[str, float]]:
            r = await self.client.get("/metrics", headers=bearer(self.sales2))
            self.assertEqual(r.status, 200)
            before = {code: float(n) for code, n in pattern.findall(await r.text())}
            report = await (await self.client.get(f"/verify/{cid}", headers=headers)).json()
            after = {code: float(n) for code, n in pattern.findall(await (await self.client.get("/metrics", headers=bearer(self.sales2))).text())}
            return report, {code: n - before.get(code, 0.0) for code, n in after.items() if n != before.get(code, 0.0)}

        report, moved = await counted(bearer(self.sales2))
        self.assertEqual(report["verdict"], "failed")
        self.assertEqual(report["summary"]["hidden_warnings"], 1)
        self.assertEqual(moved, {"hidden_error": 1.0})
        hidden = verify_audits(s)[0]["detail"]["reasons"]
        self.assertTrue({"integrity_mismatch", "producer_revoked"} <= set(hidden), hidden)
        report, moved = await counted(self.admin)
        self.assertEqual(set(moved), set(verify_audits(s)[0]["detail"]["reasons"]))
        self.assertEqual(set(moved), set(hidden), "an administrator's report, and its count, has every code")
        self.assertEqual(set(moved), set(codes(report)) | {w["code"] for w in report["warnings"]})

    async def test_query_verify_flag_is_opt_in(self) -> None:
        reader = await self.register_reader()
        ids = await self.demo_notes()
        s = self.h.service
        q = {"query": "delivery risk RX-4 SD-9", "scope": "northwind"}
        verified = s.metrics.verifications.labels("verified")

        async def query(key: str, body: dict, status: int = 200) -> dict:
            r = await self.client.post("/query", json=body, headers=bearer(key))
            self.assertEqual(r.status, status, await r.text())
            return await r.json()

        audits, count = len(verify_audits(s)), verified._value.get()
        plain = await query(self.sales2, q)
        self.assertEqual(plain["answer"]["memory_id"], ids["conclusion"])
        self.assertNotIn("verification", plain["answer"])
        for flag in (False, None):                 # JSON null is the flag left out
            with self.subTest(verify=flag):
                res = await query(self.sales2, {**q, "verify": flag})
                self.assertEqual((set(res), res["answer"]), (set(plain), plain["answer"]))
        self.assertEqual((len(verify_audits(s)), verified._value.get()), (audits, count), "nothing walked without the flag")
        res = await query(self.sales2, {**q, "verify": True})
        v = res["answer"].pop("verification")
        self.assertEqual(v, {"verdict": "verified", "derived_correctly": True, "still_true": True, "reasons": []})
        self.assertEqual((set(res), res["answer"]), (set(plain), plain["answer"]), "the rest of the response is unchanged")
        self.assertEqual((len(verify_audits(s)), verified._value.get()), (audits + 1, count + 1))
        res = await query(self.admin["Authorization"][7:], {**q, "verify": True})
        self.assertEqual(res["answer"]["verification"]["verdict"], "verified")
        self.assertEqual(verified._value.get(), count + 2)
        # without lineage:read the summary is left out, like the lineage graph: nothing walked, nothing audited
        audits = len(verify_audits(s))
        res = await query(reader, {**q, "verify": True})
        self.assertEqual(res["answer"]["memory_id"], ids["conclusion"])
        self.assertNotIn("verification", res["answer"])
        for bad in ("yes", 1, 0, "true", [], {}):
            with self.subTest(verify=bad):
                res = await query(self.sales2, {**q, "verify": bad}, 400)
                self.assertEqual(res, {"error": "'verify' must be true or false"})
        res = await query(self.sales2, {"query": "zebra quantum harmonica", "scope": "northwind", "verify": True})
        self.assertIsNone(res["answer"])
        self.assertEqual(len(verify_audits(s)), audits)
        # a DAG beyond MYCELIC_VERIFY_MAX_NODES: the walk stops at the budget
        budget = s.settings.verify_max_nodes
        s.settings.verify_max_nodes = 2
        try:
            v = (await query(self.sales2, {**q, "verify": True}))["answer"]["verification"]
        finally:
            s.settings.verify_max_nodes = budget
        self.assertEqual((v["verdict"], v["derived_correctly"], v["still_true"]), ("unverifiable", None, None))
        self.assertIn({"code": "walk_truncated", "severity": "U", "count": 1}, v["reasons"])

    async def test_events_validation_and_size(self) -> None:
        r = await self.client.post("/events", json={"events": [{"type": "call", "memory": {"text": "Kessler stock is low", "slot": "supplier_buffer_low"}}]},
                                   headers=bearer(self.proc1))
        self.assertEqual(r.status, 202, await r.text())
        self.assertIn("memory_id", (await r.json())["results"][0])
        r = await self.client.post("/events", json={"events": []}, headers=bearer(self.proc1))
        self.assertEqual(r.status, 400)
        r = await self.client.post("/memory", json={"text": "x", "metadata": {"blob": "y" * 5000}}, headers=bearer(self.proc1))
        self.assertEqual(r.status, 400)
        r = await self.client.post("/memory", json={"text": "x", "slot": "bad slot!"}, headers=bearer(self.proc1))
        self.assertEqual(r.status, 400)
        r = await self.client.post("/memory", json={"text": "x", "agent_id": "log-1"}, headers=bearer(self.proc1))
        self.assertEqual(r.status, 403)

    async def test_admin_audit_and_events(self) -> None:
        await self.observe(self.log1, "note", topic="t")
        r = await self.client.get("/admin/audit", headers=self.admin)
        actions = {row["action"] for row in (await r.json())["audit"]}
        self.assertIn("memory.ingest", actions)
        self.assertIn("agent.register", actions)
        r = await self.client.get("/admin/events?org=northwind&kind=memory.observed", headers=self.admin)
        self.assertEqual(len((await r.json())["events"]), 1)
        r = await self.client.get("/admin/audit", headers=bearer(self.log1))
        self.assertEqual(r.status, 403)


class MCPTests(ApiTestCase):
    async def rpc(self, key: str | None, method: str, params: dict | None = None, id_: int = 1):
        headers = {"Accept": "application/json"}
        if key:
            headers.update(bearer(key))
        payload = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            payload["params"] = params
        return await self.client.post("/mcp", json=payload, headers=headers)

    async def test_mcp_identity_is_the_bearer_agent(self) -> None:
        r = await self.rpc(None, "tools/list")
        self.assertEqual(r.status, 401)
        key = await self.register("log-1")
        other = await self.register("sales-9", department="commercial", team="field-sales")
        r = await self.rpc(key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(r.status, 200)
        init = await r.json()
        self.assertEqual(init["result"]["serverInfo"]["name"], "mycelic")
        self.assertIn("Mcp-Session-Id", r.headers)
        r = await self.rpc(key, "tools/list")
        names = [t["name"] for t in (await r.json())["result"]["tools"]]
        self.assertEqual(names, ["mycelic_query", "mycelic_remember", "mycelic_lineage", "mycelic_verify", "mycelic_get_memory",
                                 "mycelic_status", "mycelic_attest"])
        r = await self.rpc(key, "tools/call", {"name": "mycelic_remember", "arguments": {"text": "Terminal 3 strike announced.", "topic": "supply:sd-9/transport"}})
        out = (await r.json())["result"]
        self.assertFalse(out["isError"], out)
        mid = out["structuredContent"]["memory_id"]
        self.assertEqual(out["structuredContent"]["scope"], "northwind/emea/nw-gmbh/ops/logistics/log-1")
        await self.h.settle()
        r = await self.rpc(key, "tools/call", {"name": "mycelic_query", "arguments": {"query": "terminal strike"}})
        self.assertEqual((await r.json())["result"]["structuredContent"]["answer"]["memory_id"], mid)
        r = await self.rpc(other, "tools/call", {"name": "mycelic_get_memory", "arguments": {"memory_id": mid}})
        self.assertTrue((await r.json())["result"]["isError"], "another team's agent cannot read it through MCP either")
        r = await self.rpc(key, "tools/call", {"name": "mycelic_lineage", "arguments": {"memory_id": mid}})
        self.assertEqual((await r.json())["result"]["structuredContent"]["roots"], [mid])
        r = await self.rpc(key, "tools/call", {"name": "mycelic_status", "arguments": {}})
        self.assertEqual((await r.json())["result"]["structuredContent"]["memories_by_layer"]["agent"], 1)
        # another organization sees its own counts only
        r = await self.client.post("/admin/agents", json={"enterprise": "acme", "agent_id": "acme-1"}, headers=self.admin)
        acme = (await r.json())["api_key"]
        r = await self.rpc(acme, "tools/call", {"name": "mycelic_status", "arguments": {}})
        self.assertEqual((await r.json())["result"]["structuredContent"]["memories_by_layer"]["agent"], 0)

    async def test_mcp_remember_description_is_truthful(self) -> None:
        key = await self.register("log-1")
        r = await self.rpc(key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertIn("untrusted data", (await r.json())["result"]["instructions"])
        r = await self.rpc(key, "tools/list")
        tools = {t["name"]: t for t in (await r.json())["result"]["tools"]}
        remember = tools["mycelic_remember"]["description"]
        for phrase in ("quoted only in your team's consolidation", "visibility=org", "without your agent id", "does not erase it"):
            self.assertIn(phrase, remember)
        for name in ("mycelic_get_memory", "mycelic_lineage"):
            self.assertIn("empty text and text_withheld set to its status", tools[name]["description"])
        security, architecture = normalised("SECURITY.md"), normalised("docs/MYCELIC_ARCHITECTURE.md")
        for name, sentence in POLICY.items():
            with self.subTest(sentence=name):
                self.assertIn(sentence, security)
        self.assertIn(POLICY["P1"], architecture)
        self.assertIn(POLICY["P2"], architecture)
        deployment = normalised("DEPLOYMENT.md")
        self.assertIn("**Responses changed in this release**", deployment)
        self.assertIn("`DERIVATION_VERSION` is 3", deployment)
        self.assertIn(POLICY["P3"], deployment)

    async def test_mcp_verify_tool_identity_and_redaction(self) -> None:
        await self.h.register("reader-1", team="field-sales", department="commercial", scopes=["memory:read"])
        ids = await demo(self.h)
        s, keys, cid = self.h.service, self.h.keys, ids["conclusion"]
        r = await self.rpc(keys["sales-2"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                          "clientInfo": {"name": "t", "version": "1"}})
        instructions = (await r.json())["result"]["instructions"]
        self.assertIn("Before acting on a conclusion, call mycelic_verify", instructions)
        self.assertIn("untrusted data", instructions)
        tools = (await (await self.rpc(keys["sales-2"], "tools/list")).json())["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], TOOL_NAMES)
        [tool] = [t for t in tools if t["name"] == "mycelic_verify"]
        self.assertEqual(tool["annotations"], {"readOnlyHint": True, "openWorldHint": False})
        self.assertEqual(tool["inputSchema"]["required"], ["memory_id"])
        self.assertEqual(tool["inputSchema"]["properties"]["max_leaf_age_seconds"],
                         {"type": "integer", "minimum": 1, "maximum": verification.MAX_LEAF_AGE_SECONDS})
        for phrase in ("derived correctly", "still true", "verified, stale, failed or unverifiable",
                       "never their text, agent ids or row digests"):
            self.assertIn(phrase, tool["description"])

        async def call(key: str, **arguments) -> tuple[dict, str]:
            r = await self.rpc(key, "tools/call", {"name": "mycelic_verify", "arguments": arguments})
            self.assertEqual(r.status, 200)
            text = await r.text()
            return json.loads(text)["result"], text

        audits = len(verify_audits(s))
        out, text = await call(keys["sales-2"], memory_id=cid)
        self.assertFalse(out["isError"], out)
        self.assertEqual([(a["principal"], a["target"]) for a in verify_audits(s)[:1]], [("sales-2", cid)])
        self.assertEqual(len(verify_audits(s)), audits + 1)
        report = out["structuredContent"]
        self.assertEqual(report["report_digest"], (await s.verify(self.h.principal("sales-2"), cid))["report_digest"])
        self.assertEqual(report["verdict"], "verified")
        self.assertGreaterEqual(report["summary"]["redacted"], 2)
        for hidden in (DEMO_TEXTS["log-1"], DEMO_TEXTS["log-2"], DEMO_TEXTS["proc-1"], "log-1", "log-2", "proc-1"):
            self.assertNotIn(hidden, text)
        out, _ = await call(ADMIN_TOKEN, memory_id=cid, max_leaf_age_seconds=3600)
        self.assertFalse(out["isError"], out)
        self.assertEqual((out["structuredContent"]["summary"]["redacted"], out["structuredContent"]["max_leaf_age"]), (0, 3600))
        audits = len(verify_audits(s))
        for key, arguments, message in ((keys["sales-2"], {"memory_id": ids["log-1"]}, "no visible memory with id"),
                                        (keys["sales-2"], {"memory_id": cid, "max_leaf_age_seconds": 0}, "'max_leaf_age'"),
                                        (keys["sales-2"], {"memory_id": cid, "max_leaf_age_seconds": True}, "'max_leaf_age'"),
                                        (keys["sales-2"], {"memory_id": cid, "max_leaf_age_seconds": 1.5}, "'max_leaf_age'"),
                                        (keys["sales-2"], {"memory_id": cid, "max_leaf_age_seconds": "3"}, "'max_leaf_age'"),
                                        (keys["sales-2"], {"memory_id": "a/b"}, "'memory_id'"),
                                        (keys["reader-1"], {"memory_id": cid}, "missing scope lineage:read")):
            with self.subTest(arguments=arguments):
                out, _ = await call(key, **arguments)
                self.assertTrue(out["isError"], out)
                self.assertIn(message, out["content"][0]["text"])
        self.assertEqual(len(verify_audits(s)), audits, "refused calls are not audited")


class QuotaApiTests(ApiTestCase):
    harness_overrides = {"max_active_memories_per_org": 2}

    @staticmethod
    def remember_rpc(text: str, id_: int = 1) -> dict:
        return {"jsonrpc": "2.0", "id": id_, "method": "tools/call",
                "params": {"name": "mycelic_remember", "arguments": {"text": text, "topic": "supply:sd-9/transport"}}}

    async def test_quota_status_code_and_sdk_does_not_retry_507(self) -> None:
        key = await self.register("log-1")
        ids = []
        for i in range(2):
            r = await self.client.post("/memory", json={"text": f"note {i}"}, headers=bearer(key))
            self.assertEqual(r.status, 202)
            ids.append((await r.json())["memory_id"])
        r = await self.client.post("/memory", json={"text": "one too many"}, headers=bearer(key))
        self.assertEqual(r.status, 507)
        self.assertEqual(r.content_type, "application/json")
        message = (await r.json())["error"]
        self.assertTrue(message.startswith("organization 'northwind' is at its limit of 2 active notes "
                                           "(MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG; 2 active now)"), message)
        # the SDK raises at once: a 507 is not retried, whatever ``retries`` says
        server = TestServer(create_app(self.h.service), host="127.0.0.1")
        await server.start_server()
        try:
            sdk = MycelicClient(str(server.make_url("")).rstrip("/"), key, retries=4, backoff=0)
            requests = self.h.service.metrics.http_requests.labels("/memory", "507")
            before = requests._value.get()
            with self.assertRaises(MycelicError) as cm:
                await asyncio.to_thread(sdk.remember, "from the SDK")
            self.assertEqual(cm.exception.status, 507)
            self.assertIn("MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG", cm.exception.message)
            self.assertEqual(requests._value.get(), before + 1, "exactly one request")
        finally:
            await server.close()
        # MCP: an error result naming the limit
        headers = {**bearer(key), "Accept": "application/json"}
        r = await self.client.post("/mcp", json=self.remember_rpc("over MCP"), headers=headers)
        result = (await r.json())["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], message, "the refusal itself, not an unexpected tool failure")
        # in a JSON-RPC batch each mycelic_remember is its own write: those past the limit get error results
        r = await self.client.post(f"/memory/{ids[0]}/retract", json={}, headers=bearer(key))
        self.assertEqual(r.status, 202)
        await self.h.settle()
        batch = [self.remember_rpc(f"batched {i}", id_=i) for i in range(3)]
        r = await self.client.post("/mcp", json=batch, headers=headers)
        results = {m["id"]: m["result"]["isError"] for m in await r.json()}
        self.assertEqual(results, {0: False, 1: True, 2: True})
        self.assertEqual(self.h.service.store.active_note_count("northwind"), 2)
        self.assertEqual(self.h.service.metrics.quota_rejections._value.get(), 5)


class VerificationDocsTests(unittest.TestCase):
    def test_verification_is_documented(self) -> None:
        architecture = (ROOT / "docs/MYCELIC_ARCHITECTURE.md").read_text(encoding="utf-8")
        for code, severity in verification.REASONS.items():
            with self.subTest(code=code):
                self.assertRegex(architecture, rf"\|\s*`{re.escape(code)}`\s*\|\s*{severity}\s*\|")
        for rel in ("DEPLOYMENT.md", "SECURITY.md", "docs/MYCELIC_ARCHITECTURE.md", "README.md", "mycelic/api.py"):
            with self.subTest(doc=rel):
                self.assertIn("/verify", (ROOT / rel).read_text(encoding="utf-8"))
        deployment = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertIn("`mycelic_verify`", deployment)
        self.assertTrue("MYCELIC_VERIFY_MAX_NODES" in deployment or "`VERIFY_MAX_NODES`" in deployment)


if __name__ == "__main__":
    unittest.main()
