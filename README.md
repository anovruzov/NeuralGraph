<div align="center">

# NeuralGraph

### Persistent graph memory for Claude and AI agents — local-first, benchmarked, yours.

[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg?style=flat-square)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-2025--06--18-7c3aed.svg?style=flat-square)](https://modelcontextprotocol.io)
[![Local-first](https://img.shields.io/badge/Cloud_accounts-0-0ea5e9.svg?style=flat-square)](#privacy-by-construction)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-%2B8.9_pts_over_flat_retrieval-f59e0b.svg?style=flat-square)](#benchmarks)

**[Quick Start](#quick-start)** · **[Connect to Claude](#connect-to-claude)** · **[Features](#what-you-get)** · **[Benchmarks](#benchmarks)** · **[Architecture](#how-it-works)** · **[CLI](#cli)** · **[REST API](#rest-api-and-dashboard)**

</div>

---

NeuralGraph gives Claude and any MCP-capable agent memory that survives the end of a
conversation. Every turn becomes structured memories, entities and typed relations in a
local knowledge graph; retrieval fuses three independent channels; the whole thing runs on
your machine against a local model.

**Free and open source** — no cloud accounts, no API keys, no subscriptions, no telemetry.

What makes it different from a vector store with a nice wrapper:

| | NeuralGraph |
|---|---|
| **Retrieval** | Vector + BM25 + entity-graph hop, fused by Reciprocal Rank Fusion — not cosine similarity alone |
| **Time** | Facts supersede facts. "Where did Ali live in March?" returns the *March* answer, not today's |
| **Speakers** | Per-agent routing: a question about Nurman searches Nurman's memories. Worth **+8.9 points** end to end |
| **Evidence** | Every retrieval decision is measured on LoCoMo (1,540 questions) with per-question artifacts committed |
| **Honesty** | We publish the experiments that **failed**, and the judge every number was scored by |
| **Ownership** | One Python process, one SQLite file. Delete the file and the memory is gone — really gone |

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **[Ollama](https://ollama.com)** or **[LM Studio](https://lmstudio.ai)** — optional for a first run (`--fake-llm`)
- ~8 GB RAM for a 7B-class model. The database itself is tiny.

### 60 seconds, no model required

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph

python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Watch memory get built, live, with a deterministic stand-in model
python demo/chat_memory_live_demo.py --fake-llm
```

`--fake-llm` runs the entire extraction and retrieval pipeline with a deterministic
stand-in, so you can see the machinery work before downloading a single weight.

### With a real local model

```bash
ollama pull qwen2.5:7b-instruct        # generation
ollama pull nomic-embed-text           # embeddings

export LLM_BASE_URL=http://localhost:11434
export LLM_MODEL=qwen2.5:7b-instruct
export EMBED_MODEL=nomic-embed-text

python -m NeuralGraph.chat_memory serve --user-name "Your Name"
```

Open **http://127.0.0.1:8765/** — the dashboard shows the queue draining, memories
appearing, tokens saved and a five-axis health grade in real time.

| Default | Value |
|---|---|
| Generation model | `qwen2.5:7b-instruct` (Ollama) |
| Embedding model | `nomic-embed-text` |
| Model endpoint | `http://localhost:11434` |
| Database | `~/.neuralgraph/chat_memory.db` |
| Dashboard / API / MCP | `http://127.0.0.1:8765/`, `/api/*`, `/mcp` |

---

## Connect to Claude

The MCP server is `neuralgraph-chat-memory` (v1.0.0). It speaks **stdio** for desktop
clients and **Streamable HTTP** for everything else, negotiating protocol `2025-06-18`,
`2025-03-26` or `2024-11-05`.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "neuralgraph-memory": {
      "command": "/absolute/path/to/NeuralGraph/.venv/bin/python",
      "args": ["-m", "NeuralGraph.chat_memory", "mcp", "--user-name", "Your Name"],
      "env": {
        "LLM_BASE_URL": "http://localhost:11434",
        "LLM_MODEL": "qwen2.5:7b-instruct",
        "EMBED_MODEL": "nomic-embed-text"
      }
    }
  }
}
```

Use absolute paths — the client inherits neither your `PATH` nor your working directory.

### Claude Code

```bash
claude mcp add neuralgraph-memory \
  -- /absolute/path/to/NeuralGraph/.venv/bin/python -m NeuralGraph.chat_memory mcp --user-name "Your Name"

# ...or against an already-running HTTP server
claude mcp add --transport http neuralgraph-memory http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $NEURALGRAPH_MCP_TOKEN"
```

Ask Claude *"what do you remember about me?"* — if `memory_status` comes back, you are wired up.

> Full client configuration, transports, tokens and troubleshooting:
> **[Chat Memory User Guide](NeuralGraph/chat_memory/README.md)**

---

## What you get

### Nine MCP tools

**Read** — always safe, never mutate:

| Tool | What it does |
|---|---|
| `memory_search` | Hybrid search across every memory, with subject/time/kind filters |
| `memory_context` | A prompt-ready context block, evidence-floored and size-capped |
| `memory_profile` | Everything known about a person or entity |
| `memory_related` | Memories connected to a given one through the graph |
| `memory_entities` | The entity graph and its typed relations |
| `memory_status` | Queue depth, worker health, tokens saved, grade |

**Write** — the only three calls that change anything:

| Tool | What it does |
|---|---|
| `memory_add_message` | Record a turn. Returns in ~1 ms; extraction happens in the background |
| `memory_remember` | Store a fact immediately, write-through |
| `memory_forget` | Retract a memory, keeping an audit entry |

Plus three MCP resources and a `recall` prompt.

### The properties behind them

- **Asynchronous extraction.** Recording a turn never blocks the conversation. A debounce
  window pairs a user turn with its reply so they are extracted as one unit.
- **Gated extraction.** Not every message contains a fact worth keeping. The worker decides
  before it spends a token.
- **Grounded writes.** A memory that is not supported by the source text is rejected rather
  than stored.
- **Time-aware supersession.** The observation with the latest *source* time wins, whatever
  order chats were ingested in. History is kept and shown as `superseded by …`.
- **Fenced commits.** Work is leased; a worker that dies mid-job has its lease expire and its
  job retried, never double-committed. Restart recovery is tested.
- **Incremental indexes.** The vector index is refreshed with rows written since the last
  query, and filters are masks — tens of thousands of memories stay in the low milliseconds.
- **Safe model swaps.** Change your embedding model and old rows keep answering queries of
  their own dimension until maintenance re-embeds them.

---

## Benchmarks

Numbers, not adjectives. Full detail, sources and evidence classes in
**[docs/BENCHMARKS.md](docs/BENCHMARKS.md)**.

### Retrieval — the clean A/B

Same 282 LoCoMo questions, same judge, only the retrieval stack changed:

| Retrieval | Accuracy | recall@10 | recall@50 |
|---|---:|---:|---:|
| flat (original Tesseract) | 64.9% | 39.4 | 61.7 |
| hybrid (speaker boost + embedding back-fill) | 67.0% | 44.7 | 61.7 |
| graph (time chain + speaker + dialogue links) | 66.7% | 39.4 | 61.7 |
| **local_pairs — per-agent routing + pair back-fill (shipped)** | **73.8%** | **46.8** | **62.8** |

The **+8.9 point** gain survives all four graders (+11.3 Qwen lenient, +9.6 Qwen strict,
+4.6 substring; *p* < .001) — the most defensible result in the campaign.

### End to end, shipped configuration

| Category | n | Accuracy |
|---|---:|---:|
| single_hop | 142 | 73.9% |
| multi_hop | 400 | 72.8% |
| temporal | 156 | 73.7% |
| open_domain | 46 | 56.5% |
| **overall** | **744** | **72.2%** |

Gemma lenient judge, conversations 1–5 of LoCoMo. The prior leaky baseline scored 66.9% on
the same questions under a different judge, so treat that delta as indicative.

### The result everyone else omits

The *same answers* score **13.5% / 21.6% / 51.4% / 64.9%** depending only on who grades
them. Judge model alone moves a score ~12 points; prompt leniency ~30. **Every number above
names its judge, because a memory benchmark without one is a press release.**

We also publish what did not work — graph-neighbour expansion, listwise reranking, wider
context, an LLM router, mega search, an LLM-built entity graph: 18 experiments, verdicts and
all, in [docs/BENCHMARKS.md](docs/BENCHMARKS.md) and [docs/research/](docs/research/).

### Coordination track

A deterministic multi-agent simulator measuring which repair strategy keeps capabilities
alive under node failure, memory deletion, auth revocation and partition. Lineage-aware
repair reaches **0.778** mean survival against an **0.889** oracle, at 45% less transfer
volume than full replication — **bitwise reproducible**, SHA256-pinned artifacts, 277 tests.

---

## How it works

```text
    conversation turn
           │
           ▼
  ┌──────────────────┐   cheap, async, debounced — never blocks the chat
  │  message queue   │
  └────────┬─────────┘
           ▼
  ┌──────────────────┐   gate → extract → ground → reconcile
  │ background       │   local model decides what is worth keeping,
  │ worker (leased)  │   and refuses what the source text does not support
  └────────┬─────────┘
           ▼
  ╔══════════════════════════════════════════════════╗
  ║              neural memory graph                 ║
  ║   memories · entities · typed relations          ║
  ║   speakers · provenance · time & supersession    ║
  ║                  (SQLite)                        ║
  ╚════════════════════┬═════════════════════════════╝
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
    ┌────────┐    ┌─────────┐    ┌─────────┐
    │ vector │    │ keyword │    │  graph  │
    │ cosine │    │ FTS5    │    │ entity  │
    │        │    │ bm25    │    │ + 1 hop │
    └────┬───┘    └────┬────┘    └────┬────┘
         └─────────────┼──────────────┘
                       ▼
        Reciprocal Rank Fusion  ─ ranks fuse, scores don't
                       ▼
        priors: importance · recency · subject · entity
                       ▼
        evidence floor + relative cutoff
                       ▼
              context block → Claude
```

**Ranks fuse, scores don't.** Three channels vote by rank, so no single scorer's scale can
dominate. Priors then reweight by importance, mild recency, and whether the query names the
memory's subject or a mentioned entity. Finally a line must earn its place: a keyword or
graph hit, or cosine above the floor — otherwise it does not reach the model's context.

Measured along the way: retrieval decides, the reranker only reorders; 15 of 30 context
memories is the optimum; speaker names are embedding-level pointers to the *wrong* speaker
(0.1% own-name vs 33.4% other-name incidence) — which is exactly why routing by speaker wins.

---

## CLI

Everything the server does, available from a terminal. All commands are
`python -m NeuralGraph.chat_memory <command>`.

| Command | What it does |
|---|---|
| `serve` | Worker + dashboard + REST API + MCP over HTTP |
| `mcp` | Worker + MCP over stdio |
| `ingest FILE` | Import a JSONL chat log, or LoCoMo JSON with `--locomo` |
| `process` | Drain the extraction queue in the foreground |
| `search QUERY` | Search memories (`-k`, `--subject`, `--history`, `--json`) |
| `context QUERY` | Print a prompt-ready context block (`-k`, `--max-chars`) |
| `profile SUBJECT` | What is known about a subject (`--json`) |
| `stats` | Counts, queue, tokens saved, grade |
| `remember TEXT` | Store a memory now (`--subject`, `--kind`, `--when`) |
| `forget MEMORY_ID` | Retract a memory (`--reason`) |
| `maintain` | Run one maintenance pass |

```bash
python -m NeuralGraph.chat_memory remember "Ali moved to Berlin" --subject Ali
python -m NeuralGraph.chat_memory search "where does Ali live" --history
python -m NeuralGraph.chat_memory stats
```

Common flags on every command: `--db`, `--model`, `--base-url`, `--embed-model`,
`--parallel`, `--workers`, `--batch-size`, `--debounce`, `--user-name`,
`--extract-assistant`, `--fake-llm`, `-v`.

Separate databases are just separate paths — `--db ~/work.db` and `--db ~/personal.db`
share nothing.

---

## REST API and dashboard

`serve` runs four things in one process: the worker, MCP at `/mcp`, a REST API under
`/api/*`, and a live dashboard at `/`.

The dashboard polls `/api/status` every two seconds and shows an avatar that pulses while
the worker is busy, the **tokens saved** figure (raw chat tokens digested minus the tokens
of the memories that replace them), and a **pentagon grade** across five axes — coverage,
compression, connectivity, freshness and reliability.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEURALGRAPH_MEMORY_DB` | `~/.neuralgraph/chat_memory.db` | Database path |
| `NEURALGRAPH_MCP_TOKEN` | unset | Bearer token required on `/mcp` |
| `NEURALGRAPH_API_TOKEN` | unset | Bearer token required on `/api/*` |
| `LLM_BASE_URL` | LM Studio default | Model server URL |
| `LLM_MODEL` | backend default | Generation model |
| `EMBED_MODEL` | backend default | Embedding model |
| `OLLAMA_NUM_PARALLEL` | — | Match `--parallel` when using Ollama |

Command-line flags override environment variables.

---

## Privacy by construction

NeuralGraph is local-first by design, not by policy:

- **Local SQLite** — one file, on your disk, that you can inspect, back up or delete.
- **Local models** — generation and embeddings run on your machine via Ollama or LM Studio.
- **Local transport** — stdio, or HTTP bound to `127.0.0.1` by default.
- **No telemetry.** Nothing phones home. There is nothing to phone home to.

Hardening if you expose it beyond localhost: bearer tokens on `/mcp` and `/api/*`
(`--mcp-token`, `--api-token`), a CORS allow-list, `Host` validation and CSRF protection
(a state-changing request from a foreign `Origin` is refused). Binding to anything other
than `127.0.0.1` makes the server reachable by other machines — read
[Security](NeuralGraph/chat_memory/README.md#12-security) before you do.

You remain responsible for the machine and the applications that can reach the database
and the MCP server.

---

## Deployment

```bash
# Container — memory server + Ollama, volumes for both
docker compose -f deploy/chat_memory/docker-compose.yml up

# Or build just the server
docker build -f deploy/chat_memory/Dockerfile -t neuralgraph-memory .
```

For an always-on laptop or mini-PC, `deploy/chat_memory/neuralgraph-memory.service` runs
`serve` as a systemd user service beside a local Ollama. One Python process, one SQLite
file, no other services.

---

## Tests

```bash
python -m unittest NeuralGraph.tests.test_chat_memory_store \
  NeuralGraph.tests.test_chat_memory_extraction NeuralGraph.tests.test_chat_memory_worker \
  NeuralGraph.tests.test_chat_memory_retrieval NeuralGraph.tests.test_chat_memory_mcp
```

Every test uses a temporary database and a deterministic fake model, so the suite needs no
GPU and no network. Covered: idempotent ingestion, per-chat ordering with cross-chat
parallelism, backoff and dead-lettering, restart recovery of expired leases, lease-fenced
commits, reconciliation and the "older observation never wins" rule, grounding rejections,
embedding outages, retrieval channels and filters, superseded handling, the token ledger
and grade, and the MCP protocol over both stdio and HTTP.

> `pytest` cannot collect from the repository root because of a stale root `__init__.py`;
> use `unittest` as above.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Model server unreachable | `ollama serve`; confirm `LLM_BASE_URL` (default `http://localhost:11434`) |
| Model not found | `ollama list`, then `ollama pull qwen2.5:7b-instruct` / `nomic-embed-text` |
| Import errors | Activate the venv, re-run `pip install -r requirements.txt` |
| MCP client cannot connect | Run `python -m NeuralGraph.chat_memory mcp` in a terminal first; then check the **absolute** interpreter path in your client config |
| Extraction seems stuck | Open the dashboard — the queue and worker mood tell you immediately |
| Concurrency does nothing | Raise `OLLAMA_NUM_PARALLEL` to match `--parallel`, or requests just queue inside Ollama |

More, client by client, in the
**[Chat Memory User Guide](NeuralGraph/chat_memory/README.md#14-troubleshooting)**.

---

## Documentation

| Document | What is in it |
|---|---|
| **[Chat Memory User Guide](NeuralGraph/chat_memory/README.md)** | Install, transports, every tool, every flag |
| **[Chat Memory Design](docs/CHAT_MEMORY.md)** | Why extraction is gated, how channels fuse, how commits are fenced |
| **[Benchmarks](docs/BENCHMARKS.md)** | Every number across all three research tracks, with evidence classes |
| **[Research Reports](docs/research/)** | 18 experiments, including the ones that failed |
| **[System Architecture](NeuralGraph_System_Architecture.md)** | The full system design |
| **[Query Routing](QUERY_ROUTING_IMPROVEMENTS.md)** | How questions are routed to the right memories |

---

## Project status

Actively developed. Interfaces, configuration and storage formats may still change.

Current work: retrieval quality, temporal reasoning, query routing, cross-chat memory,
attribution, latency, and local-first agent integrations.

Contributions are welcome — issues and pull requests both. If you change retrieval, bring a
benchmark run with it, and name the judge.

---

## License

[MIT](LICENSE) © NeuralGraph contributors.

<div align="center">

**Your conversations. Your machine. Your memory.**

</div>
