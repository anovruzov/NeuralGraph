"""MCP server tests: JSON-RPC/MCP protocol core (initialize negotiation, tools/list, tools/call result and
error shapes, resources, prompts, batches, notifications), the stdio transport driven through in-memory
streams, and the Streamable HTTP transport + dashboard API through aiohttp's test client (auth, session id,
SSE responses, CORS, 405/202 semantics).

Run: .venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_mcp -v
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig
from NeuralGraph.chat_memory.llm import FakeLLMClient
from NeuralGraph.chat_memory.mcp_server import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    LATEST_PROTOCOL_VERSION,
    TOOLS,
    MCPProtocol,
    serve_stdio,
)
from NeuralGraph.chat_memory.testing import scripted_fake_llm
from NeuralGraph.chat_memory.ui.server import create_app


class MCPTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        cfg = ChatMemoryConfig(); cfg.debounce_seconds = 0; cfg.worker.poll_interval = 0.02
        self.cm = ChatMemory(Path(self._tmp.name) / "mcp.db", llm=scripted_fake_llm(), config=cfg)

    async def asyncTearDown(self) -> None:
        await self.cm.close()
        self._tmp.cleanup()

    async def seed(self) -> None:
        await self.cm.remember("Ali has a cat called Luna.", subject="Ali", chat_id="c1")
        await self.cm.remember("Ali lives in Berlin.", subject="Ali", kind="identity", chat_id="c1")


class ProtocolTests(MCPTestCase):
    async def test_initialize_negotiates_version(self) -> None:
        p = MCPProtocol(self.cm)
        r = await p.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(r["result"]["serverInfo"]["name"], "neuralgraph-chat-memory")
        r = await p.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)
        self.assertIsNone(await p.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual((await p.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"}))["result"], {})

    async def test_tools_list_schema_is_well_formed(self) -> None:
        r = await MCPProtocol(self.cm).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], [t["name"] for t in TOOLS])
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(set(t["inputSchema"]["required"]) <= set(t["inputSchema"]["properties"]))
            self.assertIn("readOnlyHint", t["annotations"])
        self.assertEqual(len({t["name"] for t in tools}), len(tools))

    async def test_tools_call_success_error_and_unknown(self) -> None:
        await self.seed()
        p = MCPProtocol(self.cm)
        r = await p.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "Luna"}}})
        res = r["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(res["content"][0]["type"], "text")
        self.assertEqual(res["structuredContent"]["count"], 1)
        self.assertEqual(json.loads(res["content"][0]["text"])["count"], 1)
        r = await p.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_related", "arguments": {"memory_id": "nope"}}})
        self.assertTrue(r["result"]["isError"])
        r = await p.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"nonsense": 1}}})
        self.assertTrue(r["result"]["isError"], "missing required argument surfaces as a tool error, not a crash")
        r = await p.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}})
        self.assertEqual(r["error"]["code"], JSONRPC_INVALID_PARAMS)
        r = await p.handle({"jsonrpc": "2.0", "id": 5, "method": "does/not/exist"})
        self.assertEqual(r["error"]["code"], JSONRPC_METHOD_NOT_FOUND)
        self.assertEqual((await p.handle({"jsonrpc": "1.0", "id": 6}))["error"]["code"], -32600)

    async def test_context_add_remember_forget_status_roundtrip(self) -> None:
        p = MCPProtocol(self.cm)
        add = await p.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory_add_message", "arguments": {"chat_id": "s1", "speaker": "Ali", "text": "I moved to Berlin in March and my cat Luna is 3 years old.", "sent_at": "2026-04-01T10:00:00+00:00"}}})
        self.assertFalse(add["result"]["isError"])
        self.assertEqual(add["result"]["structuredContent"]["status"], "pending")
        await self.cm.process_pending()
        ctx = await p.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_context", "arguments": {"query": "Ali cat"}}})
        self.assertIn("Luna", ctx["result"]["content"][0]["text"])
        rem = await p.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_remember", "arguments": {"text": "Ali prefers tea.", "subject": "Ali"}}})
        mid = rem["result"]["structuredContent"]["memory"]["memory_id"]
        forget = await p.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "memory_forget", "arguments": {"memory_id": mid}}})
        self.assertTrue(forget["result"]["structuredContent"]["retracted"])
        st = await p.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "memory_status", "arguments": {}}})
        self.assertIn("grade", st["result"]["structuredContent"])
        ents = await p.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "memory_entities", "arguments": {"entity": "Ali"}}})
        self.assertTrue(ents["result"]["structuredContent"]["found"])

    async def test_resources_prompts_and_batch(self) -> None:
        await self.seed()
        p = MCPProtocol(self.cm)
        r = await p.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        self.assertEqual([x["uri"] for x in r["result"]["resources"]], ["memory://status", "memory://entities"])
        r = await p.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "memory://profile/Ali"}})
        self.assertEqual(json.loads(r["result"]["contents"][0]["text"])["memory_count"], 2)
        r = await p.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "recall", "arguments": {"topic": "Luna"}}})
        self.assertIn("Luna", r["result"]["messages"][0]["content"]["text"])
        batch = await p.handle_payload([{"jsonrpc": "2.0", "id": 4, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}])
        self.assertEqual([b["id"] for b in batch], [4])
        self.assertIsNone(await p.handle_payload({"jsonrpc": "2.0", "method": "notifications/cancelled"}))


class StdioTransportTests(MCPTestCase):
    async def test_stdio_loop_end_to_end(self) -> None:
        await self.seed()
        reader = asyncio.StreamReader()
        out: list[bytes] = []
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "Berlin"}}},
        ]
        for m in msgs:
            reader.feed_data((json.dumps(m) + "\n").encode())
        reader.feed_data(b"not json\n")
        reader.feed_eof()
        await serve_stdio(self.cm, reader=reader, write=out.append)
        lines = [json.loads(b.decode()) for b in out]
        by_id = {l.get("id"): l for l in lines}
        self.assertEqual(by_id[1]["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)
        self.assertEqual(by_id[2]["result"]["structuredContent"]["count"], 1)
        self.assertEqual(by_id[None]["error"]["code"], -32700)
        self.assertEqual(len(lines), 3, "notifications produce no output line")


class HttpTransportTests(MCPTestCase):
    async def asyncSetUp(self) -> None:
        await self.seed()
        self.app = create_app(self.cm, mcp_token="secret")
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await super().asyncTearDown()

    async def test_auth_session_and_json_response(self) -> None:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        r = await self.client.post("/mcp", json=init)
        self.assertEqual(r.status, 401)
        self.assertIn("Bearer", r.headers.get("WWW-Authenticate", ""))
        h = {"Authorization": "Bearer secret", "Accept": "application/json, text/event-stream"}
        r = await self.client.post("/mcp", json=init, headers=h)
        self.assertEqual(r.status, 200)
        sid = r.headers.get("Mcp-Session-Id")
        self.assertTrue(sid)
        self.assertEqual(r.headers.get("MCP-Protocol-Version"), LATEST_PROTOCOL_VERSION)
        body = await r.json()
        self.assertEqual(body["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)
        h2 = {**h, "Mcp-Session-Id": sid}
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h2)
        self.assertEqual(r.status, 202)
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "Luna"}}}, headers=h2)
        self.assertEqual((await r.json())["result"]["structuredContent"]["count"], 1)
        self.assertEqual(r.headers.get("Mcp-Session-Id"), sid)
        r = await self.client.delete("/mcp", headers=h2)
        self.assertEqual(r.status, 204)

    async def test_sse_get_and_method_semantics(self) -> None:
        h = {"Authorization": "Bearer secret", "Accept": "text/event-stream"}
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 9, "method": "ping"}, headers=h)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.content_type, "text/event-stream")
        text = await r.text()
        self.assertTrue(text.startswith("event: message\ndata: "))
        self.assertEqual(json.loads(text.split("data: ", 1)[1].strip())["id"], 9)
        r = await self.client.get("/mcp", headers={"Authorization": "Bearer secret", "Accept": "application/json"})
        self.assertEqual(r.status, 405)
        r = await self.client.post("/mcp", data=b"{not json", headers={"Authorization": "Bearer secret", "Content-Type": "application/json"})
        self.assertEqual(r.status, 400)
        self.assertEqual((await r.json())["error"]["code"], -32700)
        r = await self.client.options("/mcp", headers={"Origin": "https://claude.ai"})
        self.assertEqual(r.status, 204)
        self.assertIn("Mcp-Session-Id", r.headers.get("Access-Control-Allow-Headers", ""))

    async def test_dashboard_and_api(self) -> None:
        r = await self.client.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("Pentagon grade", await r.text())
        r = await self.client.get("/api/status")
        st = await r.json()
        self.assertEqual(st["memories"]["by_status"]["active"], 2)
        self.assertIn("grade", st)
        r = await self.client.post("/api/messages", json={"chat_id": "api", "speaker": "Ali", "text": "I adopted a dog called Rex in Berlin."})
        self.assertEqual(r.status, 202)
        self.assertTrue((await r.json())["queued"])
        r = await self.client.post("/api/messages", json={"chat_id": "api", "speaker": "", "text": "x"})
        self.assertEqual(r.status, 400)
        r = await self.client.post("/api/messages/batch", json={"chat_id": "api", "messages": [{"speaker": "Ali", "text": "I also like tea."}]})
        self.assertEqual((await r.json())["queued"], 1)
        r = await self.client.get("/api/search", params={"q": "Luna"})
        self.assertEqual(len((await r.json())["results"]), 1)
        r = await self.client.get("/api/search")
        self.assertEqual(r.status, 400)
        r = await self.client.get("/api/context", params={"q": "Berlin"})
        self.assertIn("Berlin", (await r.json())["context"])
        r = await self.client.get("/api/profile/Ali")
        self.assertEqual((await r.json())["memory_count"], 2)
        r = await self.client.get("/api/entities")
        self.assertEqual([e["name"] for e in (await r.json())["entities"]], ["Ali"])
        r = await self.client.post("/api/remember", json={"text": "Ali likes jazz.", "subject": "Ali"})
        self.assertEqual(r.status, 201)
        mid = (await r.json())["memory"]["memory_id"]
        r = await self.client.post("/api/forget", json={"memory_id": mid})
        self.assertTrue((await r.json())["retracted"])
        r = await self.client.get("/api/memories", params={"subject": "Ali"})
        self.assertEqual(len((await r.json())["memories"]), 2)
        r = await self.client.get("/healthz")
        self.assertEqual((await r.json())["ok"], True)

    async def test_api_token_when_configured(self) -> None:
        app = create_app(self.cm, api_token="api-secret")
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            self.assertEqual((await client.get("/api/status")).status, 401)
            self.assertEqual((await client.get("/api/status", headers={"Authorization": "Bearer api-secret"})).status, 200)
            self.assertEqual((await client.get("/")).status, 200, "the dashboard page itself is not gated")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
