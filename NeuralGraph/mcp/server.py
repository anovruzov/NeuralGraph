"""NeuralGraph memory as an MCP server for Claude Code and Codex.

Run on stdio::

    python3.11 -m NeuralGraph.mcp.server

Environment:

- ``NEURALGRAPH_DB``               SQLite file (default ``~/.neuralgraph/memory.db``)
- ``NEURALGRAPH_SESSION``          memory namespace (default ``default``)
- ``NEURALGRAPH_EMBED``            ``auto`` | ``hashed`` | ``model`` (default ``auto``:
                                   probe the local model once, fall back to hashed)
- ``NEURALGRAPH_FAILURE_DOMAIN``   recorded on every memory when set; never inferred
- ``LLM_BASE_URL`` / ``EMBED_MODEL`` the local embedding endpoint (see llm_backend.py)

Print a ready-to-paste client config::

    python3.11 -m NeuralGraph.mcp.server --print-config claude
    python3.11 -m NeuralGraph.mcp.server --print-config codex
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .memory import DEFAULT_DB, MemoryEngine, choose_embedder

SERVER_NAME = "neuralgraph-memory"

INSTRUCTIONS = """NeuralGraph is the agent's long-term memory: a private graph of memories with
provenance, validity intervals and decay. Use it in this order:

1. At the start of a task, `recall` what is known about the project, the
   person and the task. Prefer 3-8 focused recalls over one vague one.
2. Whenever you learn something that will matter beyond this conversation,
   `remember` it immediately: a decision and why, a preference, a convention,
   a fact about the environment, a commitment, a failure and its cause. One
   memory per fact. Give it a `kind` (preference | decision | fact |
   convention | commitment | incident | note), `tags`, and a `source`
   (file path, URL, conversation). Mark secrets `private=true` (they are
   never exported to other agents).
3. For facts that can change (the editor someone uses, the current branch,
   a deadline), pass a stable `key` such as `user.editor` or
   `project.default_branch`. A new memory with the same key supersedes the
   old one and records the contradiction; history is kept.
4. If you are unsure which of two memories is current, `recall` with
   `explain=true` and `include_superseded=true`, and prefer the one without
   `superseded_by`.
5. Use `forget` only for memories that were wrong or must not be kept; it is
   a soft delete with a reason, and provenance survives.
6. Run `decay` occasionally (daily is plenty). Recalled memories warm up;
   cold, unimportant ones are archived, never deleted.

Do not store transcripts. Store the fact, the decision, the reason.
"""

_engine: MemoryEngine | None = None


async def _guarded(coro):
    """Surface engine validation errors to the client as tool errors with their message."""
    try:
        return await coro
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


async def engine() -> MemoryEngine:
    global _engine
    if _engine is None:
        embedder = await choose_embedder(os.environ.get("NEURALGRAPH_EMBED", "auto"))
        _engine = MemoryEngine(
            db_path=os.environ.get("NEURALGRAPH_DB", DEFAULT_DB),
            session_key=os.environ.get("NEURALGRAPH_SESSION", "default"),
            embedder=embedder,
        )
    return _engine


def set_engine(instance: MemoryEngine | None) -> None:
    """Inject an engine (tests) or reset to lazy construction."""
    global _engine
    _engine = instance


def build_server() -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version="1.0")

    @server.tool(description="Store one fact, decision, preference, convention, commitment or incident as a memory. Idempotent: an identical or near-identical memory strengthens the existing one. Pass `key` for facts that can change; a new memory with the same key supersedes the old one.")
    async def remember(
        text: str,
        kind: str = "note",
        key: str | None = None,
        tags: list[str] | None = None,
        speaker: str | None = None,
        source: str | None = None,
        when: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        importance: float = 0.5,
        entities: list[str] | None = None,
        derived_from: list[str] | None = None,
        private: bool = False,
    ) -> dict[str, Any]:
        return await _guarded((await engine()).remember(
            text, kind=kind, key=key, tags=tags or (), speaker=speaker, source=source, when=when,
            valid_from=valid_from, valid_to=valid_to, importance=importance, entities=entities or (),
            derived_from=derived_from or (), private=private,
        ))

    @server.tool(description="Retrieve the memories most relevant to a question. Fuses semantic, keyword and entity signals; `explain=true` returns why each hit ranked. `as_of` (ISO time) restricts to memories valid at that time; `include_superseded` includes replaced facts.")
    async def recall(
        query: str,
        limit: int = 8,
        kind: str | None = None,
        tags: list[str] | None = None,
        as_of: str | None = None,
        include_superseded: bool = False,
        explain: bool = False,
    ) -> dict[str, Any]:
        hits = await _guarded((await engine()).recall(
            query, limit=limit, kind=kind, tags=tags or (), as_of=as_of,
            include_superseded=include_superseded, explain=explain,
        ))
        return {"count": len(hits), "memories": hits}

    @server.tool(description="Fetch one memory by id with its derivation parents and related memories.")
    async def get_memory(memory_id: str) -> dict[str, Any]:
        item = await _guarded((await engine()).get(memory_id))
        return item or {"error": "not found", "memory_id": memory_id}

    @server.tool(description="Soft-delete a memory that was wrong or must not be kept. Provenance is preserved; give a reason.")
    async def forget(memory_id: str, reason: str = "forgotten by agent") -> dict[str, Any]:
        ok = await _guarded((await engine()).forget(memory_id, reason))
        return {"forgotten": ok, "memory_id": memory_id}

    @server.tool(description="List the most recent active memories, optionally filtered by kind.")
    async def list_recent(limit: int = 20, kind: str | None = None) -> dict[str, Any]:
        items = await _guarded((await engine()).list_recent(limit=limit, kind=kind))
        return {"count": len(items), "memories": items}

    @server.tool(description="Cool every active memory and archive cold, unimportant ones (never deletes). Run occasionally.")
    async def decay(rate: float = 0.05) -> dict[str, Any]:
        return await _guarded((await engine()).decay(rate=rate))

    @server.tool(description="Counts by state, kind and embedding source for the current memory session.")
    async def memory_stats() -> dict[str, Any]:
        return await _guarded((await engine()).stats())

    @server.tool(description="Export recall results as policy-filtered coordination claims (private memories are denied without payload). For handing evidence to other agents through the Tesseract boundary.")
    async def export_claims(query: str, limit: int = 8) -> dict[str, Any]:
        return await _guarded((await engine()).export_claims(query, limit=limit))

    @server.prompt(name="memory_policy", description="When and how to create memories with NeuralGraph.")
    def memory_policy() -> str:
        return INSTRUCTIONS

    return server


def client_config(client: str, python: str = sys.executable, cwd: str | None = None) -> str:
    cwd = cwd or str(Path(__file__).resolve().parents[2])
    env = {"NEURALGRAPH_DB": os.environ.get("NEURALGRAPH_DB", DEFAULT_DB), "NEURALGRAPH_SESSION": os.environ.get("NEURALGRAPH_SESSION", "default"), "NEURALGRAPH_EMBED": os.environ.get("NEURALGRAPH_EMBED", "auto")}
    if client == "claude":
        cfg = {"mcpServers": {"neuralgraph": {"type": "stdio", "command": python, "args": ["-m", "NeuralGraph.mcp.server"], "cwd": cwd, "env": env}}}
        return json.dumps(cfg, indent=2) + "\n"
    if client == "codex":
        env_toml = "\n".join(f'{k} = "{v}"' for k, v in env.items())
        return (
            "# ~/.codex/config.toml\n[mcp_servers.neuralgraph]\n"
            f'command = "{python}"\nargs = ["-m", "NeuralGraph.mcp.server"]\ncwd = "{cwd}"\n\n'
            f"[mcp_servers.neuralgraph.env]\n{env_toml}\n"
        )
    raise ValueError("client must be 'claude' or 'codex'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--print-config", choices=("claude", "codex"), help="print a client config and exit")
    parser.add_argument("--db", help="override NEURALGRAPH_DB")
    parser.add_argument("--session", help="override NEURALGRAPH_SESSION")
    parser.add_argument("--embed", choices=("auto", "hashed", "model"), help="override NEURALGRAPH_EMBED")
    args = parser.parse_args(argv)
    if args.print_config:
        sys.stdout.write(client_config(args.print_config))
        return 0
    if args.db:
        os.environ["NEURALGRAPH_DB"] = args.db
    if args.session:
        os.environ["NEURALGRAPH_SESSION"] = args.session
    if args.embed:
        os.environ["NEURALGRAPH_EMBED"] = args.embed
    build_server().run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
