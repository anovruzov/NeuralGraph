"""HTTP API and MCP endpoint over aiohttp's test client: authentication, authorization, limits, health, metrics,
memory/query/lineage routes, admin routes and the per-request MCP identity."""
from __future__ import annotations

import unittest

from aiohttp.test_utils import TestClient, TestServer

from mycelic.api import create_app

from .helpers import ADMIN_TOKEN, DEMO_RULE, ServiceHarness


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
        self.assertEqual(names, ["mycelic_query", "mycelic_remember", "mycelic_lineage", "mycelic_get_memory", "mycelic_status"])
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


if __name__ == "__main__":
    unittest.main()
