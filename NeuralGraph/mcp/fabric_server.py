"""The fabric as an MCP server: ask the collective, not one agent.

Configure peers in a JSON file named by ``NEURALGRAPH_FABRIC``::

    {"bus": "~/.neuralgraph/fabric_bus.jsonl",
     "peers": [
       {"node_id": "neuralgraph:ali", "command": "python3.11", "args": ["-m", "NeuralGraph.mcp.server"],
        "env": {"NEURALGRAPH_DB": "~/.neuralgraph/ali.db", "NEURALGRAPH_SESSION": "ali", "NEURALGRAPH_FAILURE_DOMAIN": "laptop-ali"}},
       {"node_id": "neuralgraph:nurman", "command": "ssh", "args": ["studio-1", "python3.11", "-m", "NeuralGraph.mcp.server"]}
     ]}

Each peer is another agent's memory server, spoken to over stdio.  Run::

    python3.11 -m NeuralGraph.mcp.fabric_server
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .fabric import Fabric

SERVER_NAME = "neuralgraph-fabric"
INSTRUCTIONS = """The fabric federates other agents' NeuralGraph memory servers. Use it when your
own memory has nothing, or when a fact spans people. `collective_recall` with
`keys` composes one value per key across peers and reports which peers
support each key and how many failure domains protect the answer.
`forecast_forgetting` says which single peer or domain failure would lose a
key, before it happens. Private memories never cross; you see denials, not
payloads. Prefer keys for facts that change; use a plain query for search.
"""

_fabric: Fabric | None = None


def _load_config() -> dict[str, Any]:
    path = os.environ.get("NEURALGRAPH_FABRIC")
    if not path:
        raise ToolError("NEURALGRAPH_FABRIC is not set; point it at a peers JSON file")
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"fabric config not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


async def fabric() -> Fabric:
    global _fabric
    if _fabric is None:
        cfg = _load_config()
        bus = cfg.get("bus")
        f = Fabric(run_id="fabric", bus_path=Path(bus).expanduser() if bus else None)
        for peer in cfg.get("peers", []):
            env = {k: os.path.expanduser(v) if isinstance(v, str) else v for k, v in (peer.get("env") or {}).items()}
            f.add_mcp(peer["node_id"], peer["command"], list(peer.get("args", [])), env, float(peer.get("timeout_seconds", 20)))
        _fabric = f
    return _fabric


def set_fabric(instance: Fabric | None) -> None:
    global _fabric
    _fabric = instance


def build_server() -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version="1.0")

    @server.tool(description="List the fabric's peers.")
    async def peers() -> dict[str, Any]:
        return {"peers": list((await fabric()).node_ids)}

    @server.tool(description="Ask the collective. With `keys`, composes one value per key across peers (support, lineage roots, failure domains, min failure-domain cut, repair if a key is missing). Without keys, federated recall merged across peers.")
    async def collective_recall(query: str, keys: list[str] | None = None, limit: int = 8, max_nodes: int | None = None) -> dict[str, Any]:
        try:
            return await (await fabric()).collective_recall(query, keys or (), limit=limit, max_nodes=max_nodes)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(description="Pre-mortem: for every peer and every declared failure domain, which of `keys` would the collective lose if it failed next. Reversible; no peer store is touched.")
    async def forecast_forgetting(query: str, keys: list[str], limit: int = 8, max_nodes: int | None = None) -> dict[str, Any]:
        if not keys:
            raise ToolError("forecast needs at least one key")
        return await (await fabric()).forecast(query, keys, limit=limit, max_nodes=max_nodes)

    return server


def main() -> int:
    build_server().run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
