"""MCP (Model Context Protocol) surface for Mycelic: Claude Code or any MCP client can use the organization's
memory as a tool set.

The JSON-RPC/MCP core is ``NeuralGraph.chat_memory.mcp_server.MCPProtocol`` (already serving the assistant); Mycelic
supplies its own tool definitions and implementations.  Identity is per request: the aiohttp middleware
authenticates the ``Authorization: Bearer <agent key>`` header exactly as for the REST routes and the principal is
handed to the tools through a context variable, so ``mycelic_remember`` writes as the calling agent and
``mycelic_query`` sees only what that agent may see.  There is no shared MCP token.

Two transports:

* Streamable HTTP at ``/mcp`` on the service itself;
* stdio via ``python -m mycelic mcp --url https://mycelic.example --api-key mk_...`` which proxies to the HTTP API,
  for desktop clients that only speak stdio.
"""
from __future__ import annotations

import contextvars
import json
from typing import Any

from NeuralGraph.chat_memory.mcp_server import MCPProtocol, StreamableHTTPTransport, ToolError

from .auth import Principal
from .service import Forbidden, MycelicService, NotFound, ValidationError

SERVER_INFO = {"name": "mycelic", "version": "0.1.0"}
INSTRUCTIONS = ("Organizational memory shared across agents. Call mycelic_query before answering questions about the "
                "organization, its customers, suppliers, projects or risks; the answer carries lineage you can inspect "
                "with mycelic_lineage. Call mycelic_remember to share an observation worth propagating; give it a topic "
                "(and a slot/entity when it is evidence for a known pattern) so it can be aggregated with other agents' notes.")

_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("mycelic_principal", default=None)


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "mycelic_query",
        "title": "Query organizational memory",
        "description": ("Retrieve what the organization knows about a question, ranked with a preference for higher "
                        "organizational layers (team, department, ... enterprise). Returns the best answer with a lineage "
                        "summary (contributing agents/teams, layers, whether the evidence is still reconstructable) and the "
                        "supporting memories. Only memories visible to the calling agent are considered."),
        "inputSchema": _schema({
            "query": {"type": "string"},
            "scope": {"type": "string", "description": "Unit path to search under (default: the whole enterprise)."},
            "min_layer": {"type": "string", "enum": ["agent", "team", "department", "subsidiary", "region", "enterprise"], "default": "agent"},
            "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
            "topic": {"type": "string"},
            "entity": {"type": "string"},
        }, ["query"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mycelic_remember",
        "title": "Share an observation",
        "description": ("Publish one memory to the organization as the calling agent. It stays attributed to you, is visible "
                        "to your team (or the whole organization with visibility='org'), and is aggregated with other agents' "
                        "memories on the same topic. Set slot/entity when the observation is evidence for a known pattern."),
        "inputSchema": _schema({
            "text": {"type": "string", "description": "One self-contained statement."},
            "topic": {"type": "string", "description": "Aggregation key, e.g. 'supply:sd-9/transport'."},
            "slot": {"type": "string", "description": "Which piece of evidence this is, e.g. 'transport_disruption'."},
            "entity": {"type": "string", "description": "What it is about, e.g. 'sd-9'."},
            "kind": {"type": "string", "default": "observation"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
            "visibility": {"type": "string", "enum": ["team", "org"], "default": "team"},
            "idempotency_key": {"type": "string", "description": "Stable id for safe re-sends."},
            "observed_at": {"type": "string", "description": "ISO-8601 time of the observation."},
        }, ["text"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "mycelic_lineage",
        "title": "Lineage of a memory",
        "description": ("Where a memory came from: the graph of contributing memories down to the raw observations, the "
                        "agents and teams behind them, the organizational layers it passed through, timestamps, confidence "
                        "and support, and whether the underlying evidence can still be reconstructed. Contributions outside "
                        "your visibility are redacted, not hidden."),
        "inputSchema": _schema({"memory_id": {"type": "string"}}, ["memory_id"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mycelic_get_memory",
        "title": "Read one memory",
        "description": "Fetch a memory by id (if you are allowed to see it).",
        "inputSchema": _schema({"memory_id": {"type": "string"}}, ["memory_id"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mycelic_status",
        "title": "Service status",
        "description": "Health of the memory service and counts of memories by organizational layer.",
        "inputSchema": _schema({}),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


class MycelicTools:
    def __init__(self, service: MycelicService) -> None:
        self.service = service

    @staticmethod
    def _principal() -> Principal:
        p = _principal.get()
        if p is None:
            raise ToolError("unauthenticated: send an agent API key as the Bearer token")
        return p

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            raise ToolError(f"unknown tool: {name}")
        try:
            return await fn(**args)
        except (ValidationError, Forbidden) as exc:
            raise ToolError(str(exc)) from exc
        except NotFound as exc:
            raise ToolError(f"no visible memory with id {exc}") from exc

    async def tool_mycelic_query(self, query: str, scope: str | None = None, min_layer: str = "agent", k: int = 5,
                                 topic: str | None = None, entity: str | None = None) -> dict[str, Any]:
        p = self._principal()
        body: dict[str, Any] = {"query": query, "min_layer": min_layer, "k": int(k), "include_lineage": False}
        if scope:
            body["scope"] = scope
        if topic:
            body["topic"] = topic
        if entity:
            body["entity"] = entity
        res = self.service.query(p, body)
        res["results"] = [{**self.service.public_view(h["memory"], p), "score": h["score"], "explanation": h["explanation"]}
                          for h in res["results"]]
        return res

    async def tool_mycelic_remember(self, text: str, topic: str | None = None, slot: str | None = None, entity: str | None = None,
                                    kind: str = "observation", confidence: float = 0.8, visibility: str = "team",
                                    idempotency_key: str | None = None, observed_at: str | None = None) -> dict[str, Any]:
        p = self._principal()
        body = {"text": text, "topic": topic, "slot": slot, "entity": entity, "kind": kind, "confidence": confidence,
                "visibility": visibility, "idempotency_key": idempotency_key, "observed_at": observed_at}
        m, created = await self.service.ingest_memory(p, {k: v for k, v in body.items() if v is not None})
        return {"memory_id": m.memory_id, "event_id": m.event_id, "created": created, "scope": m.scope,
                "note": "accepted; aggregation happens asynchronously through the event log"}

    async def tool_mycelic_lineage(self, memory_id: str) -> dict[str, Any]:
        return self.service.lineage(self._principal(), memory_id)

    async def tool_mycelic_get_memory(self, memory_id: str) -> dict[str, Any]:
        p = self._principal()
        return self.service.public_view(self.service.get_memory(p, memory_id), p)

    async def tool_mycelic_status(self) -> dict[str, Any]:
        h = await self.service.health()
        return {"status": h["status"], "version": h["version"], "memories_by_layer": h["stats"].get("memories_by_layer"),
                "transport_connected": bool(h["checks"]["transport"].get("connected"))}


def build_protocol(service: MycelicService) -> MCPProtocol:
    return MCPProtocol(None, instructions=INSTRUCTIONS, tool_defs=TOOLS, tools=MycelicTools(service), server_info=SERVER_INFO)


class MycelicMCPTransport(StreamableHTTPTransport):
    """Streamable HTTP transport whose identity comes from the request (set by the API middleware)."""

    def __init__(self, service: MycelicService, *, allowed_origins: list[str] | None = None) -> None:
        super().__init__(None, token=None, allowed_origins=allowed_origins, protocol=build_protocol(service))

    async def handle(self, request):  # type: ignore[override]
        token = _principal.set(request.get("principal"))
        try:
            return await super().handle(request)
        finally:
            _principal.reset(token)


async def serve_stdio_proxy(base_url: str, api_key: str, *, ca_file: str | None = None) -> None:
    """stdio MCP server that forwards every tool call to a running Mycelic over HTTP (for desktop clients)."""
    import asyncio
    import sys

    from .sdk import MycelicClient

    client = MycelicClient(base_url, api_key, ca_file=ca_file)

    class ProxyTools:
        async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            try:
                if name == "mycelic_query":
                    return await loop.run_in_executor(None, lambda: client.query(args["query"], scope=args.get("scope"),
                                                                                  min_layer=args.get("min_layer", "agent"),
                                                                                  k=int(args.get("k", 5)), include_lineage=False,
                                                                                  topic=args.get("topic"), entity=args.get("entity")))
                if name == "mycelic_remember":
                    return await loop.run_in_executor(None, lambda: client.remember(**args))
                if name == "mycelic_lineage":
                    return await loop.run_in_executor(None, lambda: client.lineage(args["memory_id"]))
                if name == "mycelic_get_memory":
                    return await loop.run_in_executor(None, lambda: client.get_memory(args["memory_id"]))
                if name == "mycelic_status":
                    return await loop.run_in_executor(None, client.health)
            except Exception as exc:
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc
            raise ToolError(f"unknown tool: {name}")

    proto = MCPProtocol(None, instructions=INSTRUCTIONS, tool_defs=TOOLS, tools=ProxyTools(), server_info=SERVER_INFO)
    reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
    await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    out = sys.stdout.buffer
    while True:
        line = await reader.readline()
        if not line:
            break
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            resp: Any = MCPProtocol._error(None, -32700, "parse error")
        else:
            resp = await proto.handle_payload(payload)
        if resp is not None:
            out.write((json.dumps(resp, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            out.flush()
