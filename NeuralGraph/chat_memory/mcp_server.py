"""MCP (Model Context Protocol) server exposing chat memory to Claude.

Two transports, one tool set:

* **stdio** (edge / local): ``python -m NeuralGraph.chat_memory mcp --db ~/.neuralgraph/chat_memory.db``.
  Register it with Claude Code (``claude mcp add neuralgraph-memory -- python -m NeuralGraph.chat_memory mcp``)
  or Claude Desktop (``claude_desktop_config.json`` -> ``mcpServers``). Runs next to a local Ollama/LM Studio.
* **Streamable HTTP** (cloud / remote): mounted at ``/mcp`` by the ``serve`` command next to the dashboard.
  Add it to claude.ai as a custom connector, to Claude Code with ``claude mcp add --transport http``, or to the
  Messages API via the MCP connector (``mcp_servers=[{"type": "url", "url": "https://host/mcp", ...}]``).
  Optional bearer-token auth (``--mcp-token`` / ``NEURALGRAPH_MCP_TOKEN``).

The protocol layer is a small, dependency-free JSON-RPC 2.0 implementation of the MCP server methods Claude
clients use (``initialize``, ``notifications/initialized``, ``ping``, ``tools/list``, ``tools/call``,
``resources/list``, ``resources/read``, ``prompts/list``). Protocol version negotiation follows the spec:
the server answers with the client's version when it is supported, otherwise with its latest.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any, Awaitable, Callable

from .service import ChatMemory

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_INFO = {"name": "neuralgraph-chat-memory", "version": "1.0.0"}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


class ToolError(Exception):
    """Raised by a tool to return an ``isError`` result (the model sees the message)."""


# ---------------------------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------------------------


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "memory_search",
        "title": "Search memories",
        "description": ("Search long-term memories collected across all past chats. Hybrid semantic + keyword + "
                        "entity-graph retrieval. Returns the current version of each fact with provenance. Use it "
                        "before answering anything about the user's life, preferences, history, people or projects."),
        "inputSchema": _schema({
            "query": {"type": "string", "description": "What to look for, in natural language."},
            "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "subject": {"type": "string", "description": "Only memories about this person/entity (name)."},
            "chat_id": {"type": "string", "description": "Only memories first observed in this chat."},
            "kinds": {"type": "array", "items": {"type": "string"}, "description": "fact, preference, event, plan, relationship, opinion, identity, task, other"},
            "since": {"type": "string", "description": "ISO date lower bound on the memory's date."},
            "until": {"type": "string", "description": "ISO date upper bound on the memory's date."},
            "include_superseded": {"type": "boolean", "default": False, "description": "Also return the history of facts that were later updated."},
        }, ["query"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_context",
        "title": "Memory context block",
        "description": ("Return a compact block of the memories most relevant to a query, ready to paste into a prompt "
                        "or read before replying. Empty string when nothing relevant is stored."),
        "inputSchema": _schema({
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
            "max_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 2400},
            "subject": {"type": "string"},
        }, ["query"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_add_message",
        "title": "Record a chat message",
        "description": ("Store one message of a conversation so the background worker (a local Qwen model) can turn it "
                        "into memories and relationships. Returns immediately; extraction happens asynchronously. "
                        "Use the same chat_id for all messages of one conversation."),
        "inputSchema": _schema({
            "chat_id": {"type": "string"},
            "speaker": {"type": "string", "description": "Who said it: the person's name, or 'user' / 'assistant'."},
            "text": {"type": "string"},
            "role": {"type": "string", "enum": ["user", "assistant", "system", ""], "default": ""},
            "sent_at": {"type": "string", "description": "ISO-8601 timestamp of the message (defaults to now)."},
            "message_id": {"type": "string", "description": "Optional stable id for idempotent re-sends."},
        }, ["chat_id", "speaker", "text"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_remember",
        "title": "Remember a fact now",
        "description": ("Store an explicit memory immediately (no extraction step), e.g. when the user says "
                        "'remember that ...'. The text should be one self-contained third-person sentence."),
        "inputSchema": _schema({
            "text": {"type": "string"},
            "subject": {"type": "string", "description": "Who/what the memory is about (defaults to 'user')."},
            "kind": {"type": "string", "default": "fact"},
            "importance": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
            "chat_id": {"type": "string", "default": "mcp"},
            "when": {"type": "string", "description": "Date the memory is about (YYYY-MM-DD, YYYY-MM or YYYY)."},
        }, ["text"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_profile",
        "title": "Profile of a person or entity",
        "description": "Everything currently known about a subject: memories grouped by kind and the entity's relations.",
        "inputSchema": _schema({"subject": {"type": "string"}, "limit": {"type": "integer", "default": 60, "minimum": 1, "maximum": 500}}, ["subject"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_related",
        "title": "Related memories",
        "description": "Memories linked to a given memory id (supersedes, contradicts, related, elaborates) and its history.",
        "inputSchema": _schema({"memory_id": {"type": "string"}}, ["memory_id"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_entities",
        "title": "Entity graph",
        "description": "Top entities by mentions, or the typed relations of one entity (1-hop neighbourhood).",
        "inputSchema": _schema({"entity": {"type": "string", "description": "Entity name; omit for the top list."},
                                "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 500}}),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_forget",
        "title": "Forget a memory",
        "description": "Retract a memory (and the relations grounded in it) at the user's request. Keeps an audit entry.",
        "inputSchema": _schema({"memory_id": {"type": "string"}, "reason": {"type": "string", "default": "user request"}}, ["memory_id"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "memory_status",
        "title": "Memory system status",
        "description": "Counts, worker/queue state, token savings and the five-axis grade of the memory system.",
        "inputSchema": _schema({}),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


def _mem_brief(m: dict[str, Any]) -> dict[str, Any]:
    keys = ("memory_id", "text", "kind", "subject_name", "speaker", "chat_id", "importance", "confidence",
            "event_time", "observed_at", "status", "version", "source_message_ids")
    return {k: m.get(k) for k in keys if k in m}


class MemoryTools:
    """Tool implementations over :class:`ChatMemory`."""

    def __init__(self, cm: ChatMemory) -> None:
        self.cm = cm

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            raise ToolError(f"unknown tool: {name}")
        return await fn(**args)

    async def tool_memory_search(self, query: str, k: int = 10, subject: str | None = None, chat_id: str | None = None,
                                 kinds: list[str] | None = None, since: str | None = None, until: str | None = None,
                                 include_superseded: bool = False) -> dict[str, Any]:
        hits = await self.cm.search(query, k=int(k), subject=subject, chat_id=chat_id, kinds=kinds, since=since,
                                    until=until, include_superseded=bool(include_superseded))
        return {"query": query, "count": len(hits), "results": [
            {**_mem_brief(h.memory.to_dict()), "score": h.score, "why": h.explanation, "entities": h.entities,
             "sources": [{"chat_id": s.chat_id, "speaker": s.speaker, "sent_at": s.sent_at, "text": s.text[:300]} for s in h.sources[:3]]}
            for h in hits]}

    async def tool_memory_context(self, query: str, k: int = 10, max_chars: int = 2400, subject: str | None = None) -> dict[str, Any]:
        block = await self.cm.context_for(query, k=int(k), max_chars=int(max_chars), subject=subject)
        return {"query": query, "context": block, "empty": not block}

    async def tool_memory_add_message(self, chat_id: str, speaker: str, text: str, role: str = "", sent_at: str | None = None,
                                      message_id: str | None = None) -> dict[str, Any]:
        msg = await self.cm.add_message(chat_id, speaker, text, role=role, sent_at=sent_at, message_id=message_id)
        return {"message_id": msg.message_id, "chat_id": msg.chat_id, "seq": msg.seq, "status": msg.status,
                "note": "queued for background extraction"}

    async def tool_memory_remember(self, text: str, subject: str | None = None, kind: str = "fact", importance: float = 0.8,
                                   chat_id: str = "mcp", when: str | None = None) -> dict[str, Any]:
        mem = await self.cm.remember(text, subject=subject or "user", kind=kind, importance=float(importance),
                                     chat_id=chat_id, when=when)
        return {"memory": _mem_brief(mem.to_dict())}

    async def tool_memory_profile(self, subject: str, limit: int = 60) -> dict[str, Any]:
        prof = await self.cm.profile(subject, limit=int(limit))
        prof["memories_by_kind"] = {k: [_mem_brief(m) for m in v] for k, v in prof["memories_by_kind"].items()}
        return prof

    async def tool_memory_related(self, memory_id: str) -> dict[str, Any]:
        mem = await self.cm.store.get_memory(memory_id, with_sources=True)
        if mem is None:
            raise ToolError(f"no memory with id {memory_id}")
        rel = await self.cm.related(memory_id)
        hist = await self.cm.store.history(memory_id)
        return {"memory": _mem_brief(mem.to_dict()),
                "links": [{"link_type": r["link_type"], "weight": r["weight"], "direction": r["direction"], "memory": _mem_brief(r["memory"])} for r in rel],
                "history": [_mem_brief(h.to_dict()) for h in hist]}

    async def tool_memory_entities(self, entity: str | None = None, limit: int = 30) -> dict[str, Any]:
        if entity:
            eid = await self.cm.store.resolve_alias(entity)
            if not eid:
                return {"entity": entity, "found": False, "relations": []}
            ent = await self.cm.store.get_entity(eid)
            rels = await self.cm.store.relations_for(eid, limit=int(limit))
            return {"entity": ent.to_dict() if ent else {"entity_id": eid}, "found": True,
                    "relations": [{"subject": r.subject_id, "predicate": r.predicate, "object": r.object_id,
                                   "confidence": r.confidence, "observations": r.observation_count, "chat_id": r.chat_id} for r in rels]}
        ents = await self.cm.store.top_entities(int(limit))
        return {"entities": [e.to_dict() for e in ents]}

    async def tool_memory_forget(self, memory_id: str, reason: str = "user request") -> dict[str, Any]:
        ok = await self.cm.retract(memory_id, reason)
        return {"memory_id": memory_id, "retracted": ok}

    async def tool_memory_status(self) -> dict[str, Any]:
        st = await self.cm.status()
        return {k: st[k] for k in ("chats", "messages", "memories", "entities", "relations", "links", "jobs", "tokens", "grade")} | {
            "worker": {k: st["worker"][k] for k in ("running", "busy_workers", "batches", "memories_added", "failures", "dead_jobs", "last_error")}}


# ---------------------------------------------------------------------------------------------
# JSON-RPC / MCP protocol core (transport-agnostic)
# ---------------------------------------------------------------------------------------------


class MCPProtocol:
    def __init__(self, cm: ChatMemory, *, instructions: str | None = None) -> None:
        self.tools = MemoryTools(cm)
        self.cm = cm
        self.instructions = instructions or (
            "Long-term memory across chats. Call memory_context or memory_search before answering questions about the "
            "user's life, preferences, people, projects or past conversations; call memory_add_message to record "
            "conversation turns so the background worker can learn from them; memory_remember stores an explicit fact.")

    # -- helpers
    @staticmethod
    def _result(id_: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    @staticmethod
    def _error(id_: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": id_, "error": err}

    async def handle(self, message: Any) -> dict[str, Any] | None:
        """Process one JSON-RPC message (request or notification). Returns a response dict or None."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return self._error(message.get("id") if isinstance(message, dict) else None, JSONRPC_INVALID_REQUEST, "invalid request")
        method = message["method"]
        params = message.get("params") or {}
        id_ = message.get("id")
        is_notification = "id" not in message
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "notifications/initialized" or method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = await self._tools_call(params)
            elif method == "resources/list":
                result = {"resources": [
                    {"uri": "memory://status", "name": "Memory system status", "mimeType": "application/json",
                     "description": "Counts, queue, tokens saved and grade."},
                    {"uri": "memory://entities", "name": "Top entities", "mimeType": "application/json"},
                ]}
            elif method == "resources/read":
                result = await self._resources_read(params)
            elif method == "resources/templates/list":
                result = {"resourceTemplates": [
                    {"uriTemplate": "memory://profile/{subject}", "name": "Subject profile", "mimeType": "application/json"}]}
            elif method == "prompts/list":
                result = {"prompts": [{"name": "recall", "description": "Recall what is known about a topic before answering.",
                                       "arguments": [{"name": "topic", "required": True}]}]}
            elif method == "prompts/get":
                topic = (params.get("arguments") or {}).get("topic", "")
                block = await self.cm.context_for(topic) if topic else ""
                result = {"messages": [{"role": "user", "content": {"type": "text", "text":
                          f"Before answering about '{topic}', here is what long-term memory holds:\n{block or '(nothing stored)'}"}}]}
            elif method == "logging/setLevel":
                result = {}
            else:
                if is_notification:
                    return None
                return self._error(id_, JSONRPC_METHOD_NOT_FOUND, f"method not found: {method}")
        except ToolError as exc:
            return self._error(id_, JSONRPC_INVALID_PARAMS, str(exc))
        except TypeError as exc:  # bad/missing tool arguments
            return self._error(id_, JSONRPC_INVALID_PARAMS, f"invalid params: {exc}")
        except Exception as exc:
            logger.exception("MCP %s failed", method)
            return self._error(id_, JSONRPC_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        if is_notification:
            return None
        return self._result(id_, result)

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False},
                             "prompts": {"listChanged": False}, "logging": {}},
            "serverInfo": SERVER_INFO,
            "instructions": self.instructions,
        }

    async def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not any(t["name"] == name for t in TOOLS):
            raise ToolError(f"unknown tool: {name}")
        if not isinstance(args, dict):
            raise ToolError("arguments must be an object")
        try:
            out = await self.tools.call(name, args)
        except ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except TypeError as exc:
            return {"content": [{"type": "text", "text": f"invalid arguments: {exc}"}], "isError": True}
        except Exception as exc:
            logger.exception("tool %s failed", name)
            return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
        text = out["context"] if name == "memory_context" else json.dumps(out, ensure_ascii=False, indent=1, default=str)
        return {"content": [{"type": "text", "text": text}], "structuredContent": out, "isError": False}

    async def _resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = str(params.get("uri") or "")
        if uri == "memory://status":
            data = await self.tools.tool_memory_status()
        elif uri == "memory://entities":
            data = await self.tools.tool_memory_entities()
        elif uri.startswith("memory://profile/"):
            data = await self.tools.tool_memory_profile(uri[len("memory://profile/"):])
        else:
            raise ToolError(f"unknown resource: {uri}")
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False, default=str)}]}

    async def handle_payload(self, payload: Any) -> Any:
        """Handle a decoded JSON payload that may be a single message or a batch. Returns the response payload or None."""
        if isinstance(payload, list):
            responses = [r for r in await asyncio.gather(*(self.handle(m) for m in payload)) if r is not None]
            return responses or None
        return await self.handle(payload)


# ---------------------------------------------------------------------------------------------
# stdio transport (newline-delimited JSON-RPC over stdin/stdout)
# ---------------------------------------------------------------------------------------------


async def serve_stdio(cm: ChatMemory, *, reader: asyncio.StreamReader | None = None,
                      write: Callable[[bytes], Awaitable[None] | None] | None = None) -> None:
    """Run the MCP server over stdio until EOF. The worker keeps running in the background meanwhile."""
    proto = MCPProtocol(cm)
    loop = asyncio.get_running_loop()
    if reader is None:
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    if write is None:
        out = sys.stdout.buffer

        def write(b: bytes) -> None:  # type: ignore[misc]
            out.write(b)
            out.flush()
    write_lock = asyncio.Lock()

    async def emit(obj: Any) -> None:
        data = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        async with write_lock:
            r = write(data)
            if asyncio.iscoroutine(r):
                await r

    pending: set[asyncio.Task] = set()

    async def process(line: bytes) -> None:
        try:
            payload = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            await emit(MCPProtocol._error(None, JSONRPC_PARSE_ERROR, "parse error"))
            return
        resp = await proto.handle_payload(payload)
        if resp is not None:
            await emit(resp)

    while True:
        try:
            line = await reader.readline()
        except (asyncio.IncompleteReadError, ConnectionError):
            break
        if not line:
            break
        if not line.strip():
            continue
        t = asyncio.create_task(process(line))
        pending.add(t)
        t.add_done_callback(pending.discard)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------------------------
# Streamable HTTP transport (aiohttp handler; mounted by ui.py at /mcp)
# ---------------------------------------------------------------------------------------------


class StreamableHTTPTransport:
    """MCP Streamable HTTP (2025-03-26+): one endpoint, POST for client->server messages.

    * Responses are returned as ``application/json`` (single response) or, when the client only accepts
      ``text/event-stream``, as one SSE ``message`` event followed by stream end.
    * A session id is issued on ``initialize`` (``Mcp-Session-Id``); sessions are lightweight here (no
      server-initiated messages), so an unknown/missing session is tolerated for stateless clients.
    * ``GET`` opens an SSE stream that only carries keep-alives (the server never pushes requests);
      ``DELETE`` ends a session.
    * Optional bearer-token auth via ``token``; ``Origin`` is validated against ``allowed_origins`` when set
      (DNS-rebinding protection for local deployments).
    """

    def __init__(self, cm: ChatMemory, *, token: str | None = None, allowed_origins: list[str] | None = None) -> None:
        self.proto = MCPProtocol(cm)
        self.token = token or None
        self.allowed_origins = allowed_origins
        self.sessions: dict[str, dict[str, Any]] = {}

    def _authorized(self, request) -> bool:
        if not self.token:
            return True
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[7:].strip() == self.token

    def _origin_ok(self, request) -> bool:
        origin = request.headers.get("Origin")
        if not origin or self.allowed_origins is None:
            return True
        return origin in self.allowed_origins

    async def handle(self, request):
        from aiohttp import web

        if not self._origin_ok(request):
            return web.json_response({"error": "origin not allowed"}, status=403)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401,
                                     headers={"WWW-Authenticate": 'Bearer realm="neuralgraph-memory"'})
        if request.method == "GET":
            return await self._handle_get(request)
        if request.method == "DELETE":
            sid = request.headers.get("Mcp-Session-Id")
            self.sessions.pop(sid or "", None)
            return web.Response(status=204)
        if request.method != "POST":
            return web.json_response({"error": "method not allowed"}, status=405, headers={"Allow": "GET, POST, DELETE"})
        try:
            payload = await request.json()
        except Exception:
            return web.json_response(MCPProtocol._error(None, JSONRPC_PARSE_ERROR, "parse error"), status=400)
        first = payload[0] if isinstance(payload, list) and payload else payload
        is_init = isinstance(first, dict) and first.get("method") == "initialize"
        resp = await self.proto.handle_payload(payload)
        headers = {"MCP-Protocol-Version": LATEST_PROTOCOL_VERSION}
        if is_init:
            sid = uuid.uuid4().hex
            self.sessions[sid] = {"created": asyncio.get_running_loop().time()}
            headers["Mcp-Session-Id"] = sid
            if isinstance(resp, dict) and "result" in resp:
                headers["MCP-Protocol-Version"] = resp["result"]["protocolVersion"]
        elif request.headers.get("Mcp-Session-Id"):
            headers["Mcp-Session-Id"] = request.headers["Mcp-Session-Id"]
        if resp is None:  # notifications / responses only
            return web.Response(status=202, headers=headers)
        accept = request.headers.get("Accept", "")
        wants_sse = "text/event-stream" in accept and "application/json" not in accept
        if wants_sse:
            body = "event: message\ndata: " + json.dumps(resp, ensure_ascii=False) + "\n\n"
            return web.Response(text=body, content_type="text/event-stream", headers={**headers, "Cache-Control": "no-cache"})
        return web.json_response(resp, headers=headers)

    async def _handle_get(self, request):
        from aiohttp import web

        accept = request.headers.get("Accept", "")
        if "text/event-stream" not in accept:
            return web.json_response({"error": "GET requires Accept: text/event-stream"}, status=405, headers={"Allow": "POST"})
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                                       "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION})
        await resp.prepare(request)
        try:
            while True:
                await resp.write(b": keep-alive\n\n")
                await asyncio.sleep(15)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return resp
