# NeuralGraph as an MCP memory server

Give Claude Code or Codex a private, provenance-preserving long-term memory.
The server wraps a local SQLite NeuralGraph store and exposes eight tools
over the Model Context Protocol on stdio. It runs offline: with no embedding
model reachable it falls back to a deterministic hashed embedding plus BM25
and entity overlap, and records which embedding produced every memory so the
two spaces are never compared.

## Install

```bash
pip install -r requirements.txt            # includes mcp and rank-bm25
python3.11 -m NeuralGraph.mcp.server --print-config claude   # Claude Code
python3.11 -m NeuralGraph.mcp.server --print-config codex    # Codex CLI
```

**Claude Code.** The repo ships `.mcp.json`, so inside this checkout the
server is already a project-scoped MCP server named `neuralgraph`. To use it
from any project:

```bash
claude mcp add neuralgraph -s user -e NEURALGRAPH_DB=~/.neuralgraph/memory.db \
  -- python3.11 -m NeuralGraph.mcp.server
```

(run from this repository root, or set `PYTHONPATH` to it).

**Codex.** Paste the printed block into `~/.codex/config.toml`:

```toml
[mcp_servers.neuralgraph]
command = "python3.11"
args = ["-m", "NeuralGraph.mcp.server"]
cwd = "/path/to/NeuralGraph"

[mcp_servers.neuralgraph.env]
NEURALGRAPH_DB = "~/.neuralgraph/memory.db"
NEURALGRAPH_SESSION = "default"
NEURALGRAPH_EMBED = "auto"
```

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `NEURALGRAPH_DB` | `~/.neuralgraph/memory.db` | SQLite file |
| `NEURALGRAPH_SESSION` | `default` | memory namespace; sessions never see each other |
| `NEURALGRAPH_EMBED` | `auto` | `hashed` (offline), `model` (require a local model), `auto` (probe once) |
| `NEURALGRAPH_FAILURE_DOMAIN` | unset | recorded on every memory when set; never inferred |
| `LLM_BASE_URL`, `EMBED_MODEL` | see `llm_backend.py` | local embedding endpoint (Ollama or OpenAI-compatible) |

## Tools

| Tool | What it does |
|---|---|
| `remember` | Store one fact/decision/preference/convention/commitment/incident. Idempotent: exact or near-duplicate text strengthens the existing memory. `key` makes a fact replaceable: a new memory with the same key supersedes the old one, archives it with `valid_to`, links new → old, and records the contradiction. `private=true` memories are never exported. |
| `recall` | Fused semantic + keyword + entity ranking (reciprocal rank fusion). `explain=true` returns the per-signal ranks. `as_of` filters by validity window. `include_superseded` shows history. Recalled memories warm up. |
| `get_memory` | One memory with its derivation parents and related memories. |
| `forget` | Soft delete with a reason. The row and its provenance stay. |
| `list_recent` | Newest active memories. |
| `decay` | Cool every active memory; archive cold, unimportant, unkeyed ones. Never deletes. |
| `memory_stats` | Counts by state, kind, embedding source. |
| `export_claims` | Recall results as policy-filtered coordination claims through `NeuralGraphMemoryAdapter`: private memories cross as payload-free denials; lineage roots and declared failure domains ride along. |

The server also publishes a `memory_policy` prompt: when to remember, what
to key, what to mark private. The same text is sent as the server's
`instructions`, so a client that honours instructions gets it automatically.

## What "great at creating memories" means here

- One memory per fact, normalized, deduplicated, tagged, with source and speaker.
- Entities extracted (names, handles, file paths) and linked to related memories.
- A temporal chain across the session and ISO dates mentioned in the text.
- Facts that change are keyed, so history is kept and contradictions are explicit.
- Provenance is graph structure: `source_memory_ids` and HIERARCHY edges in the
  direction the coordination layer's lineage resolver reads (new → what it was
  built from).
- Used memories strengthen; unsupported ones decay to archive, never to deletion.

## Boundaries kept

- No retrieval-track file is modified. The engine writes through the storage
  base API and reads through the same API the coordination layer uses.
- Failure domains are recorded only when the operator names one.
- An origin memory (no recorded derivation) is exported as its own lineage
  root with the domain declared on it. Nothing is synthesized.

## Verify

```bash
python3.11 -m pytest -q NeuralGraph/tests/test_mcp_memory.py
```
