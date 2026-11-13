<h1 align="center">NeuralGraph</h1>

<p align="center"><strong>High-evidence recall. Millisecond retrieval. Your data stays local.</strong></p>
<p align="center">A persistent, source-traceable memory engine for AI agents — designed for the accuracy–latency frontier.</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-111111?style=flat-square"></a>
  <a href="https://www.rust-lang.org/"><img alt="Rust 1.89+" src="https://img.shields.io/badge/Rust-1.89%2B-B7410E?style=flat-square"></a>
  <a href="https://modelcontextprotocol.io"><img alt="Model Context Protocol" src="https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-555555?style=flat-square"></a>
  <a href="#privacy-by-construction"><img alt="Local-first" src="https://img.shields.io/badge/storage-local%20SQLite-333333?style=flat-square"></a>
</p>

<p align="center">
  <a href="#quick-start">Install</a> ·
  <a href="#connect-to-claude">Connect an agent</a> ·
  <a href="#what-you-get">Capabilities</a> ·
  <a href="#how-it-works">Architecture</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="#cli">CLI</a> ·
  <a href="#rest-api-and-dashboard">API & dashboard</a>
</p>

---

NeuralGraph is a persistent memory engine for Claude Code, Claude Desktop, Codex, Cursor, and other [MCP](https://modelcontextprotocol.io)-capable clients. It converts selected conversation content and imported documents into structured memories with **source provenance**, **entities**, **typed relationships**, and **temporal history**. On recall, it combines vector similarity and keyword search, with an optional graph channel. A local model can then answer using retrieved evidence and cite its sources.

The goal is not to store every token forever. It is to preserve useful information, know where it came from, distinguish old facts from current ones, and avoid supplying irrelevant memories to the next answer.

| Capability | What it means in practice |
|---|---|
| **Persistent** | Memory survives chat sessions and model restarts in a SQLite database you control |
| **Evidence-backed** | Memories retain references to originating messages or documents; the answer interface exposes citations and retrieval evidence |
| **Temporal** | Earlier observations remain in history when newer facts supersede them |
| **Hybrid retrieval** | Vector + BM25 rankings fuse via Reciprocal Rank Fusion; graph expansion is optional |
| **Local-first** | Works with locally running LM Studio or Ollama; no hosted account is required for normal operation |
| **Inspectable** | Use the CLI, local dashboard, API, or MCP tools to inspect, search, edit, and retract memories |

### Retrieval quality and speed

| **87.9%** | **17 ms** | **Local-first** |
|:---:|:---:|:---:|
| **LoCoMo evidence@15** | **Team-reported recall latency** | **SQLite + local models** |
| Gold-source evidence retrieved in a 66-question LoCoMo subset | Reported recall measurement; percentile and run artifact not yet published | No hosted NeuralGraph service required |

**What the numbers mean:** **87.9%** is evidence retrieval coverage, **not** 87.9% end-to-end LoCoMo answer accuracy. The **17 ms** figure is a separately reported recall latency, **not** a published same-run accuracy/latency benchmark. For a documented latency measurement, the repository records **10 ms p50 / 12 ms p95** retrieval on a separate LongMemEval Oracle sample. The 150-question LongMemEval Oracle *answer* evaluation averaged **69%** under a local judge. [Evaluation protocols and evidence](#benchmarks).

**The design goal:** source-grounded recall with strong evidence coverage at interactive latency, without treating slow reranking or large context dumps as a substitute for retrieval quality.

<p align="center">
  <img src="docs/images/dashboard.png" alt="NeuralGraph local dashboard showing memory activity and recent memories" width="820">
</p>

---

## Quick Start

### One command (macOS and Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/anovruzov/NeuralGraph/main/install.sh | sh
```

That is the whole install. The script:

1. Downloads the prebuilt binary for your machine from the latest release and verifies
   its SHA-256, so you need no compiler and no Rust. If there is no prebuilt binary for
   your machine, it installs what a source build needs (the Xcode command-line tools on
   macOS, Rust via rustup) and compiles instead.
2. Puts `neuralgraph-memory` in `~/.local/bin` and adds that folder to your `PATH`.
3. Starts the [setup wizard](#setup-wizard-recommended). If no model server is running, the
   wizard offers to install [Ollama](https://ollama.com) for you and download the models.

You need about 8 GB of RAM for a 7B-class model. The memory server has been observed to idle around 12 MB in the documented environment; memory and database size grow with workload. Read the script before piping it to `sh`:
[`install.sh`](install.sh).

Options are environment variables, for example
`curl -fsSL …/install.sh | NEURALGRAPH_NO_SETUP=1 sh` installs without starting the wizard.
The script's header lists all of them.

### Everyday commands

```bash
neuralgraph-memory open            # the web app in your browser (starts it if it is not running)
neuralgraph-memory open chat       # straight to a page: chat, memories, import, graph, models, settings
neuralgraph-memory chat            # ask your memory questions in the terminal
neuralgraph-memory chat "what cameras do I own?"   # one question, one answer
```

### Updating

You run the install command once. After that:

```bash
neuralgraph-memory update          # or: Settings → General → Update now in the web app
neuralgraph-memory update --check  # only say whether a newer version exists
```

`update` downloads the latest release for your machine, checks its SHA-256, replaces the
program, and restarts the dashboard in place with your settings. It also stops copies that
Claude Code or Claude Desktop started before the update, so they cannot keep learning with the
old version's rules; restart those apps to reconnect them. The web app shows *Update available*
when a newer release exists. That check asks GitHub for the latest version number and happens
only while the web app is open or when you run `update`.

### Prerequisites for building yourself

- **[Rust](https://rustup.rs) 1.89+** and a C compiler (SQLite is compiled into the binary) — or use the
  [container image](#deployment)
- **[Ollama](https://ollama.com)** or **[LM Studio](https://lmstudio.ai)** — optional for a first run (`--fake-llm`)

### 60 seconds, no model required

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph
cargo run --release --example chat_memory_live_demo -- --fake-llm
```

`--fake-llm` runs the entire extraction and retrieval pipeline with a deterministic
stand-in, so you can see the machinery work before downloading a single weight.

### Setup wizard (recommended)

The installer runs this for you. From a clone:

```bash
cargo install --locked --path crates/neuralgraph-memory
neuralgraph-memory setup
```

One command does the rest:

1. Finds LM Studio or Ollama and lists their models. If neither is running, it offers to
   install Ollama. If the recommended models are missing, it offers to download them.
2. Sends one test prompt and one test embedding, so you know both models work.
3. Asks your name and where to keep the memory file.
4. Connects **Claude Code**, **Codex**, **Claude Desktop** and **Cursor**. Pick any of the
   ones it finds. Existing configs are merged, never overwritten, and a `.bak` copy is kept.
5. Saves your answers to `~/.neuralgraph/config.json`, then offers to start the server and
   open the dashboard.

`setup` always shows a *Disk and memory* summary: every model it will use, its size, and
whether it is installed or still to download. Before anything is downloaded, it shows what it will fetch and how big it is. Ollama
sizes come straight from Ollama's registry. It also shows your free disk space and how much
RAM the models need, and only downloads after you confirm:

```text
  ⬇ Ready to download
    Chat model  qwen2.5:7b-instruct      4.7 GB
    Embeddings  nomic-embed-text         0.3 GB
    Images      qwen2.5vl:3b             3.2 GB
                total                    8.2 GB
    ✓ disk     101 GB free
    ✓ memory   about 6 GB of your 48 GB while learning
```

Re-run `setup` any time to change an answer. `setup --yes` accepts every default without
asking and connects no tools. Settings resolve as flags > environment variables >
`config.json` > built-in defaults.

### Manual setup with Ollama

```bash
cargo install --locked --path crates/neuralgraph-memory

ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text

export LLM_BASE_URL=http://localhost:11434
export LLM_MODEL=qwen2.5:7b-instruct
export EMBED_MODEL=nomic-embed-text

neuralgraph-memory serve --user-name "Your Name"
```

Open **http://127.0.0.1:8765/** — the dashboard shows the queue draining, memories
appearing, tokens saved and a five-axis health grade in real time.

The built-in defaults are for LM Studio. `setup` detects Ollama and saves its settings; the
block above sets them by hand.

| Default | LM Studio (built in) | Ollama (via `setup` or the variables above) |
|---|---|---|
| Chat model | `google/gemma-4-e4b` | `qwen2.5:7b-instruct` |
| Embedding model | `text-embedding-nomic-embed-text-v1.5` | `nomic-embed-text` |
| Model endpoint | `http://127.0.0.1:1234` | `http://localhost:11434` |

| | |
|---|---|
| Database | `~/.neuralgraph/chat_memory.db` |
| Dashboard / API / MCP | `http://127.0.0.1:8765/`, `/api/*`, `/mcp` |

---

## Connect to Claude

The MCP server reports the name `neuralgraph-chat-memory` (protocol/server version 1.0.0); the installed binary is `neuralgraph-memory`. It speaks **stdio** for desktop
clients and **Streamable HTTP** for everything else, negotiating protocol `2025-06-18`,
`2025-03-26` or `2024-11-05`.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "neuralgraph-memory": {
      "command": "/Users/you/.cargo/bin/neuralgraph-memory",
      "args": ["mcp", "--user-name", "Your Name"],
      "env": {
        "LLM_BASE_URL": "http://localhost:11434",
        "LLM_MODEL": "qwen2.5:7b-instruct",
        "EMBED_MODEL": "nomic-embed-text"
      }
    }
  }
}
```

Use the absolute path to the binary (`command -v neuralgraph-memory` prints it) — the client
does not inherit your `PATH`.

### Claude Code

```bash
claude mcp add neuralgraph-memory -- "$(command -v neuralgraph-memory)" mcp --user-name "Your Name"
```

Or, against an already-running HTTP server:

```bash
claude mcp add --transport http neuralgraph-memory http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $NEURALGRAPH_MCP_TOKEN"
```

Ask Claude *"what do you remember about me?"* — if `memory_status` comes back, you are wired up.

### Automatic memory in Claude Code

MCP alone only saves a memory when Claude decides to call a tool. `setup` offers two
Claude Code [hooks](https://docs.claude.com/en/docs/claude-code/hooks) that make memory
automatic. They are written to `~/.claude/settings.json`, any hooks you already have are kept,
and a `.bak` copy of the old file is saved.

| Hook | What it does |
|---|---|
| `Stop` | After every reply, queues your prompt and Claude's final answer from the session transcript. Any running `mcp` or `serve` process turns them into memories in the background. Tool calls, tool output, thinking, system reminders and subagents are skipped. Re-reading a transcript never stores anything twice. |
| `UserPromptSubmit` | Before Claude sees a prompt, adds the memories that are semantically close to it (at most 5, similarity 0.665 or higher with nomic-embed-text; `NEURALGRAPH_RECALL_MIN_SIM` changes the bar). Prompts with nothing relevant get nothing added. |

Both hooks run `neuralgraph-memory hook claude-code`, take milliseconds, and never block
Claude Code: if something fails, the hook logs it and steps aside. To turn them off, re-run
`neuralgraph-memory setup` and answer *no* to "Make memory automatic in Claude Code?".

> Full client configuration, transports, tokens and troubleshooting:
> **[Chat Memory User Guide](crates/neuralgraph-memory/README.md)**

---

## What you get

### Ten MCP tools

NeuralGraph offers **seven read-only tools** and **three write tools**. The CLI and HTTP API expose additional entry points to the same memory system.

| Tool | Purpose |
|---|---|
| `memory_search` | Search with subject, time, and kind filters |
| `memory_context` | Produce an evidence-filtered, prompt-sized context block |
| `memory_answer` | Answer from memory using the routed reader, with source-backed context |
| `memory_profile` | Retrieve known information about a subject |
| `memory_related` | Follow memory relationships |
| `memory_entities` | Inspect entities and typed relations |
| `memory_status` | Inspect ingestion, queue, and health |
| `memory_add_message` | Queue a conversation turn for asynchronous extraction |
| `memory_remember` | Write a specified fact immediately |
| `memory_forget` | Retract a memory while preserving the corresponding audit entry |

The MCP server also exposes resources and a recall prompt. See the [full MCP guide](crates/neuralgraph-memory/README.md) for arguments, outputs, and client-specific configuration.

### Why the memory behaves differently

- **Asynchronous, selective extraction.** Recording a turn need not block generation; a background worker decides which information is worth storing.
- **Source grounding.** Extracted memories are checked against their source text before the write is accepted.
- **Time-aware updates.** New observations can supersede old ones without erasing historical context.
- **Worker recovery.** Leases, retries, and commit fencing protect queue processing across worker restarts.
- **Incremental retrieval.** SQLite FTS5 and vector retrieval combine rankings, with optional entity-graph expansion and subject priors.
- **Useful answers, not just matches.** `memory_answer`, `neuralgraph-memory answer`, and `POST /api/answer` use a reader that routes some numerical and temporal questions to a computation-oriented prompt. This is a model-based answer path, not a guarantee of correctness.

**Limits:** retrieval may miss relevant evidence, extraction can omit or misread facts, and the reader can still answer incorrectly. The graph stores relationships and provenance; it does not independently prove that user-supplied statements are true.

---

## For organizations: Mycelic

NeuralGraph is the open-source local memory engine for an assistant. **[Mycelic Labs](https://myceliclabs.com)** is building a broader enterprise intelligence fabric for connecting evidence across teams and systems while preserving control of underlying data at its source. They address different deployment needs; the features documented here are those available in **this repository**.

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

**Ranks fuse, scores don't.** Channels vote by rank (vector and keyword by default; the graph hop is opt-in), so no single scorer's scale can
dominate. Priors then reweight by importance, mild recency, and whether the query names the
memory's subject or a mentioned entity. Finally a line must earn its place: a keyword or
graph hit, or cosine above the floor — otherwise it does not reach the model's context.

---


## Benchmarks

**Different metrics answer different questions.** NeuralGraph's research engine, its shipped assistant pipeline, and the local answer reader have separate evaluations. The following results are documented in [BENCHMARKS.md](docs/BENCHMARKS.md) and the [reproduction guide](research/overnight/README.md).

| Evaluation | Result | What was measured |
|---|---:|---|
| **LoCoMo — shipped extraction/retrieval pipeline** | **87.9% evidence@15** | Retrieval-only: in 66 questions from conversation 0, sessions 1–8, at least one retrieved memory cites a gold evidence message. Qwen3.8-9B extractor, nomic embeddings; one conversation and one run per configuration |
| **LongMemEval Oracle — retrieval** | **93.5% turn@15** | 50-question stratified sample, seed 1; whether retrieved context cites an answer-bearing source turn, not whether the assistant answers correctly |
| **LongMemEval Oracle — end-to-end answers** | **69% mean judged accuracy** | Three 50-question draws (150 total), local Qwen3.8-9B reader and Gemma-4-e4b using official judge prompts. The 100-question held-out mean was **67%**; the other 50 questions were used in protocol selection |
| **Research retrieval A/B** | **+8.9 points** | A controlled comparison on the same 282 LoCoMo questions, with the same judge; research pipeline, not the shipped assistant's end-to-end result |

**Interpretation matters.** `evidence@15` and `turn@15` are *retrieval* metrics, not LoCoMo answer accuracy. The LongMemEval run uses **Oracle**, where evidence sessions are supplied; it does not represent the harder LongMemEval S setting. These local-model, local-judge, partial-sample results must not be compared directly with vendor claims from different datasets, readers, judges, retrieval budgets, or evaluation procedures. The 69% is an observed score; projected frontier-model scores are **not** benchmark results.

### Accuracy at speed

**17 ms recall** is a team-reported measurement supplied after the published evaluation. It is shown here as a reported result, **not** a reproducible leaderboard entry: the test population, hardware, latency statistic (mean/p50/p95), context size, and raw run artifact have not yet been added to this repository. It must not be represented as 17 ms *end-to-end answering* or automatically paired with the 87.9% LoCoMo result.

**Reproduced evaluation context:** the benchmark document records p50 **10 ms** and p95 **12 ms** retrieval on its 50-question LongMemEval Oracle databases, including embedding. A separate 50-question product-path answer check measured a **3.45 s mean** including retrieval and generation. These are different workloads, not a universal service-level guarantee.

**Competitive benchmark standard:** for any claim of “#1 accuracy at speed,” systems must be evaluated on the *same* LoCoMo questions, extraction protocol, answer model, judge, retrieval depth, hardware, and latency statistic. The current repository does **not** establish that ranking or ≥85% **end-to-end answer accuracy**. It does establish the more specific **87.9% evidence@15** on the documented subset; that is the metric presented above.

For protocols, samples, prompts, limitations, negative results, and reproduction commands, start with [docs/BENCHMARKS.md](docs/BENCHMARKS.md). The [overnight reproduction guide](research/overnight/README.md) describes the shipped reader and its held-out evaluation.

---

## Repository layout

```text
crates/neuralgraph-memory/    the assistant: memory server, MCP, worker, store, dashboard
crates/neuralgraph-research/  research code, not used at runtime
demo/                         runnable demos (cargo examples; work with --fake-llm)
deploy/                       Docker, compose and systemd units
docs/                         design docs
research/                     research datasets and artifacts
```

| I want to… | Start here |
|---|---|
| Give Claude persistent memory | [`crates/neuralgraph-memory/README.md`](crates/neuralgraph-memory/README.md) |
| See it work in 60 seconds | [`demo/README.md`](demo/README.md) |
| Deploy it somewhere | [`deploy/`](deploy/) |

---

## CLI

After installation, run `neuralgraph-memory --help` for all options. Frequently used commands:

| Command | Purpose |
|---|---|
| `setup` | Select local models, test the endpoints, and connect MCP clients |
| `open [PAGE]` | Open the dashboard (home, chat, memories, import, graph, models, activity, settings) |
| `chat [QUESTION]` | Ask memory interactively or once; show citations, evidence, and stats |
| `answer "QUESTION"` | Use the routed answer reader, with optional `--today` and `--json` |
| `search "QUERY"` / `context "QUERY"` | Inspect retrieval or print context without asking the reader |
| `remember "FACT"` / `forget MEMORY_ID` | Explicitly save or retract a memory |
| `profile SUBJECT` / `graph [ENTITY]` | Inspect subject knowledge and relations |
| `ingest FILE` / `process` | Import JSONL or LoCoMo data and process the extraction queue |
| `stats` / `maintain` | Inspect health and perform maintenance |
| `pause` / `resume` | Pause or resume model-backed learning |
| `serve` / `mcp` | HTTP dashboard + API + MCP server, or MCP over stdio |
| `update` / `update --check` | Upgrade or check for a new GitHub release |

```bash
neuralgraph-memory remember "I prefer local-first tools" --subject "Your Name"
neuralgraph-memory search "local-first tools"
neuralgraph-memory answer "What tools do I prefer?"
neuralgraph-memory graph --out graph.html
```

Choose a separate database with `--db ~/work.db` or `--db ~/personal.db`. These are separate memory stores; the application does not automatically merge them.

---

## REST API and dashboard

`neuralgraph-memory serve` runs the background worker, the dashboard at **http://127.0.0.1:8765/**, REST endpoints under `/api/*`, and Streamable HTTP MCP at `/mcp`.

The local dashboard is intended for inspecting and controlling the actual stored data, not just looking at aggregate counts.

| Page | Primary use |
|---|---|
| **Home** | Status, memory and entity counts, recent activity, worker and queue health |
| **Chat** | Ask questions; inspect numbered source citations, search evidence, and timing |
| **Memories** | Search, add, edit, retract, archive, and restore memory entries |
| **Import** | Preview and import Claude/ChatGPT exports, Claude Code sessions, notes, PDFs, screenshots, or text |
| **Graph** | Explore entities, typed relations, co-mentions, neighborhoods, and source memories |
| **Models** | See local models, change assignments, load/unload, and download compatible models |
| **Activity** | Review imported chats, extraction queue, and recent work |
| **Settings** | Configure identity, memory location, clients, model endpoints, and updates |

<p align="center">
  <img src="docs/images/graph-explorer.png" alt="NeuralGraph graph explorer with entity relationships and local neighborhood views" width="820">
</p>

**Import behavior:** chats use the speaker's own turns as the source of personal memories and retain assistant responses as context; documents are read in smaller sections. Imports show a preview before processing. A vision-capable local model can process screenshots; this may require a separate model download. The resulting facts can still be incomplete or mistaken, so review imported memories before using them for high-stakes decisions.

**Answers with citations:** `POST /api/answer` and `memory_answer` use the routed reader. The ordinary Chat UI can also return a citation-backed answer from retrieved context. Citations show *where an answer came from*, not an external validation of that source.

**Automatic recall:** `setup` can install Claude Code hooks that save conversation content and supply relevant memories before a prompt. MCP alone does not automatically capture every conversation unless the client calls a write tool or a hook/integration is enabled. Existing client settings are backed up by the setup process.

More precise API payloads, supported imports, modes, and operating limits are documented in the [user guide](crates/neuralgraph-memory/README.md).

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEURALGRAPH_MEMORY_DB` | `~/.neuralgraph/chat_memory.db` | Database path |
| `NEURALGRAPH_USER_NAME` | unset | Display name for speaker `user` (same as `--user-name`) |
| `NEURALGRAPH_CONFIG` | `~/.neuralgraph/config.json` | Where `setup` saves its answers |
| `NEURALGRAPH_MCP_TOKEN` | unset | Bearer token required on `/mcp` |
| `NEURALGRAPH_API_TOKEN` | unset | Bearer token required on `/api/*` |
| `LLM_BASE_URL` | `http://127.0.0.1:1234` (LM Studio) | Model server URL; port 11434 selects the Ollama API |
| `LLM_MODEL` | `google/gemma-4-e4b` | Generation model |
| `EMBED_MODEL` | `text-embedding-nomic-embed-text-v1.5` | Embedding model |
| `OLLAMA_NUM_PARALLEL` | — | Match `--parallel` when using Ollama |
| `LLM_NO_THINK_PREFILL` | unset | `1` always switches reasoning off with an empty think block. Rarely needed: a model seen reasoning anyway gets this automatically |

Command-line flags override environment variables, which override the answers `setup`
saved in `~/.neuralgraph/config.json`.

---

## Privacy by construction

For normal use, memory lives in local SQLite and the chat and embedding requests go to the **model endpoint you configure**, typically Ollama or LM Studio on the same machine. The app does not require a hosted NeuralGraph account or send product telemetry. **Installation and version checks contact GitHub**; a remotely configured model endpoint may send prompts or memory content to that endpoint.

- **Local storage is not encryption.** SQLite files, journals, exports, and backups must be protected with appropriate file permissions and disk encryption.
- **Loopback is the safe default.** `serve` binds to `127.0.0.1` unless you change it.
- **Tokens are not on by default.** When exposing HTTP beyond localhost, configure `NEURALGRAPH_MCP_TOKEN` and `NEURALGRAPH_API_TOKEN`, restrict network access, and terminate TLS in a reverse proxy.
- **Retraction is not secure erasure.** `memory_forget` preserves an audit record; imported originals, backups, application transcripts, and any previously supplied context may still contain the information.
- **One store is not a multi-tenant service.** Run separate processes/databases for separate people. There is no built-in user-level isolation.

Review [SECURITY.md](SECURITY.md) before remote deployment or storing sensitive information.

---

## Deployment

Container with the memory server and Ollama, with volumes for both:

```bash
docker compose -f deploy/chat_memory/docker-compose.yml up
```

Or build just the server:

```bash
docker build -f deploy/chat_memory/Dockerfile -t neuralgraph-memory .
```

**Prebuilt binaries.** Pushing a tag such as `v1.1.0` runs `.github/workflows/release.yml`.
It builds `neuralgraph-memory` for macOS (Apple silicon and Intel) and Linux (x86_64 and
arm64), then attaches each `.tar.gz` and its `.sha256` to the GitHub release. `install.sh`
downloads those files.

For an always-on laptop or mini-PC, `deploy/chat_memory/neuralgraph-memory.service` runs
`serve` as a systemd user service beside a local Ollama. One binary, one SQLite file, no
other services.

---

## Tests

```bash
cargo test -p neuralgraph-memory
```

Every test uses a temporary database and a deterministic fake model, so the suite needs no
GPU and no network. Covered: idempotent ingestion, per-chat ordering with cross-chat
parallelism, backoff and dead-lettering, restart recovery of expired leases, lease-fenced
commits, reconciliation and the "older observation never wins" rule, grounding rejections,
embedding outages, retrieval channels and filters, superseded handling, the token ledger
and grade, and the MCP protocol over both stdio and HTTP.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Model server unreachable | Start LM Studio's server (Developer → Start Server) or `ollama serve`; the Models page shows which server NeuralGraph talks to |
| Model not found | Models page → Download, or `neuralgraph-memory setup` |
| Build errors | Rust 1.89+ (`rustup update`) and a C compiler (`xcode-select --install`, `build-essential`) |
| MCP client cannot connect | Run `neuralgraph-memory mcp` in a terminal first; then check the **absolute** binary path in your client config |
| Extraction seems stuck | Open the dashboard — the queue and worker mood tell you immediately |
| Concurrency does nothing | Raise `OLLAMA_NUM_PARALLEL` to match `--parallel`, or requests just queue inside Ollama |

More, client by client, in the
**[Chat Memory User Guide](crates/neuralgraph-memory/README.md#14-troubleshooting)**.

---

## Documentation

| Document | What is in it |
|---|---|
| **[Chat Memory User Guide](crates/neuralgraph-memory/README.md)** | Install, transports, every tool, every flag |
| **[Chat Memory Design](docs/CHAT_MEMORY.md)** | Why extraction is gated, how channels fuse, how commits are fenced |
| **[Security](SECURITY.md)** | What the server enforces, what it does not, and how to report a vulnerability |
| **[Contributing](CONTRIBUTING.md)** | Setup, tests and code style |

---

## Project status

Actively developed. Interfaces, configuration and storage formats may still change.

Current work: retrieval quality, temporal reasoning, query routing, cross-chat memory,
attribution, latency, and local-first agent integrations.

Contributions are welcome — issues and pull requests both.

---

## License

[MIT](LICENSE) © NeuralGraph contributors.

<div align="center">

**Your conversations. Your machine. Your memory.**

</div>
