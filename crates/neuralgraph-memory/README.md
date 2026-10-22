# NeuralGraph Chat Memory — MCP server

Long-term memory across every chat, stored locally, served to any MCP client.

The server is `neuralgraph-chat-memory` (v1.0.0), shipped as one binary, `neuralgraph-memory`. It gives an
assistant nine tools for reading and writing durable memory, backed by a local SQLite database and a background
worker that turns ordinary conversation turns into structured memories using a local model. Nothing is sent to a
third-party service: the model runs on your machine, and the database is a file you own.

> This is the user guide. For the design — why extraction is gated, how the retrieval channels are fused, how
> commits are fenced on a worker lease — see [`docs/CHAT_MEMORY.md`](../../docs/CHAT_MEMORY.md).

---

## Contents

1. [What you get](#1-what-you-get)
2. [Requirements](#2-requirements)
3. [Install](#3-install)
4. [Pick a model backend](#4-pick-a-model-backend)
5. [Run the server](#5-run-the-server)
6. [Connect a client](#6-connect-a-client)
7. [Tool reference](#7-tool-reference)
8. [Resources and prompts](#8-resources-and-prompts)
9. [How to actually use it](#9-how-to-actually-use-it)
10. [CLI reference](#10-cli-reference)
11. [Configuration](#11-configuration)
12. [Security](#12-security)
13. [Your data](#13-your-data)
14. [Troubleshooting](#14-troubleshooting)
15. [Upgrading from the Python version](#15-upgrading-from-the-python-version)

---

## 1. What you get

Nine tools, in two groups.

**Reading** — `memory_search`, `memory_context`, `memory_profile`, `memory_related`, `memory_entities`,
`memory_status`. All are read-only and cannot modify anything.

**Writing** — `memory_add_message` (record a turn, extraction happens in the background), `memory_remember`
(store a fact immediately), `memory_forget` (retract one, keeping an audit entry).

The distinction that matters in practice: `memory_add_message` is cheap and asynchronous — it returns in about a
millisecond and queues the turn for the worker, so you can record every message without slowing a conversation
down. `memory_remember` writes straight through, for when someone says "remember that…" and expects it to stick
immediately.

Two transports are supported: **stdio** (for Claude Desktop and most local clients) and **Streamable HTTP** at
`/mcp` (for clients that speak HTTP, and for running the server on a different machine from the client).

Protocol versions `2025-06-18`, `2025-03-26` and `2024-11-05` are accepted; the server negotiates to whichever
the client asks for, defaulting to the newest.

---

## 2. Requirements

- A Rust toolchain, 1.89 or newer ([rustup](https://rustup.rs)), and a C compiler (SQLite is compiled into the
  binary). Or skip both and use the [container image](#container).
- A local model server for generation and embeddings — [Ollama](https://ollama.com) or
  [LM Studio](https://lmstudio.ai). You can skip this for a first run using `--fake-llm`, which substitutes a
  deterministic stand-in and needs no model at all.

Roughly 8 GB of RAM is comfortable for a 7B-class model; the memory server itself idles at about 12 MB and the
database is small.

---

## 3. Install

From the repository root:

```bash
cargo install --locked --path crates/neuralgraph-memory
```

That puts `neuralgraph-memory` in `~/.cargo/bin` (on your `PATH` after a rustup install). To build without
installing, `cargo build --release` leaves it at `target/release/neuralgraph-memory`.

Check it runs:

```bash
neuralgraph-memory stats --fake-llm
```

That prints counts, queue state and the system grade against an empty database, creating
`~/.neuralgraph/chat_memory.db` on first run.

### Verify the server end to end

No model server needed — `--fake-llm` covers the whole path:

```bash
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | neuralgraph-memory mcp --fake-llm 2>/dev/null
```

A healthy server answers `initialize` with

```json
{"name": "neuralgraph-chat-memory", "version": "1.0.0"}
```

at protocol `2025-06-18`, advertising the `tools`, `resources`, `prompts` and `logging` capabilities, and then
lists all nine tools. If you get nothing, run the `stats` command above to see the error, since stdio mode sends
logs to stderr and the redirect hides them here.

### Container

```bash
docker build -f deploy/chat_memory/Dockerfile -t neuralgraph-memory .
docker compose -f deploy/chat_memory/docker-compose.yml up        # memory server + Ollama
```

---

## 4. Pick a model backend

**Ollama**

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
export LLM_BASE_URL=http://localhost:11434
export LLM_MODEL=qwen2.5:7b-instruct
export EMBED_MODEL=nomic-embed-text
```

If you raise `--parallel` above 4, raise `OLLAMA_NUM_PARALLEL` to match or the extra concurrency just queues
inside Ollama.

**LM Studio** — defaults to `http://127.0.0.1:1234`; load a generation model and an embedding model, then set
`LLM_BASE_URL`, `LLM_MODEL` and `EMBED_MODEL` the same way.

**No model yet** — add `--fake-llm` to any command. Extraction still runs end to end and the results are
deterministic, which makes it useful for trying the tools and for tests, but the memories it produces are
stand-ins, not real extractions.

---

## 5. Run the server

### stdio

```bash
neuralgraph-memory mcp --user-name "Your Name"
```

This runs the background worker and speaks MCP over stdin/stdout. Logs go to stderr, so they never corrupt the
protocol stream. This is what you want for Claude Desktop.

### Streamable HTTP

```bash
neuralgraph-memory serve --user-name "Your Name" --parallel 4 --mcp-token "$(openssl rand -hex 32)"
```

`serve` runs four things at once: the worker, the MCP endpoint at `http://127.0.0.1:8765/mcp`, a REST API under
`/api/*`, and a live dashboard at `http://127.0.0.1:8765/`. The dashboard is the quickest way to see whether
extraction is actually working — it shows the queue draining and the memories appearing.

`--host` and `--port` change where it binds. Binding to anything other than `127.0.0.1` means other machines can
reach it, so read [Security](#12-security) before you do.

---

## 6. Connect a client

### Claude Desktop

Edit the MCP config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

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

Use the absolute path to the binary (`command -v neuralgraph-memory` prints it) — the client does not inherit
your shell's `PATH`. Restart Claude Desktop; the tools appear under the server name.

### Claude Code

```bash
claude mcp add neuralgraph-memory -- "$(command -v neuralgraph-memory)" mcp --user-name "Your Name"
```

Or, against a running HTTP server:

```bash
claude mcp add --transport http neuralgraph-memory http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $NEURALGRAPH_MCP_TOKEN"
```

### Any HTTP client

POST JSON-RPC to `/mcp`:

```bash
curl -s http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $NEURALGRAPH_MCP_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools[].name'
```

A session id is issued on `initialize` and returned in the `Mcp-Session-Id` header. The server never initiates
messages, so sessions are lightweight and a stateless client that omits the header still works. `GET` opens an
SSE stream carrying keep-alives only; `DELETE` ends a session.

---

## 7. Tool reference

### `memory_search` — search memories

Hybrid semantic + keyword retrieval across every past chat (the entity-graph channel is opt-in, see
[`docs/CHAT_MEMORY.md`](../../docs/CHAT_MEMORY.md)). Returns the current version of each fact with its provenance.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | **Required.** Natural language. |
| `k` | integer | 15 | 1–50. |
| `subject` | string | — | Restrict to memories about this person or entity. |
| `chat_id` | string | — | Restrict to memories first observed in this chat. |
| `kinds` | string[] | — | `fact`, `preference`, `event`, `plan`, `relationship`, `opinion`, `identity`, `task`, `other`. |
| `since` / `until` | string | — | ISO date bounds on the memory's own date. |
| `include_superseded` | boolean | false | Also return the history of facts that were later updated. |

### `memory_context` — prompt-ready context block

The same retrieval, rendered as a compact block to paste into a prompt. Returns an empty string when nothing
relevant is stored — which is a meaningful answer, not a failure.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | **Required.** |
| `k` | integer | 15 | 1–30. |
| `max_chars` | integer | 3600 | 200–20000. Hard budget on the block. |
| `subject` | string | — | Restrict to one subject. |

### `memory_add_message` — record a chat message

Queues one turn for background extraction. Returns immediately. Use the same `chat_id` for every message in a
conversation.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `chat_id` | string | — | **Required.** |
| `speaker` | string | — | **Required.** A name, or `user` / `assistant`. |
| `text` | string | — | **Required.** |
| `role` | string | `""` | `user`, `assistant`, `system` or empty. |
| `sent_at` | string | now | ISO-8601. |
| `message_id` | string | — | Stable id, so re-sending the same message is idempotent. |

### `memory_remember` — remember a fact now

Stores an explicit memory with no extraction step. The text should be one self-contained third-person sentence.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `text` | string | — | **Required.** |
| `subject` | string | `user` | Who or what it is about. |
| `kind` | string | `fact` | |
| `importance` | number | 0.8 | 0–1. |
| `chat_id` | string | `mcp` | |
| `when` | string | — | The date the memory is *about*: `YYYY-MM-DD`, `YYYY-MM` or `YYYY`. |

### `memory_profile` — profile of a person or entity

Everything currently known about a subject, grouped by kind, plus that entity's relations. Parameters: `subject`
(**required**), `limit` (default 60, 1–500).

### `memory_related` — related memories

Memories linked to a given `memory_id` (**required**) — supersedes, contradicts, related, elaborates — together
with its history.

### `memory_entities` — entity graph

With `entity`: the typed relations of that entity (its 1-hop neighbourhood). Without: the top entities by mention
count. `limit` defaults to 30 (1–500).

### `memory_forget` — forget a memory

Retracts a memory and the relations grounded in it. Parameters: `memory_id` (**required**), `reason` (default
`user request`). An audit entry is kept, so the retraction is recorded rather than the row simply vanishing.

### `memory_status` — system status

No parameters. Counts, worker and queue state, token savings, and the five-axis grade of the memory system.

---

## 8. Resources and prompts

**Resources**

| URI | Content |
|---|---|
| `memory://status` | Counts, queue, tokens saved, grade (JSON). |
| `memory://entities` | Top entities (JSON). |
| `memory://profile/{subject}` | Profile of one subject (JSON, templated). |

**Prompt** — `recall`, taking a required `topic` argument: recall what is known about a topic before answering.

---

## 9. How to actually use it

The pattern that makes this worth running is boring and consistent:

**Read before answering.** Call `memory_context` (or `memory_search` if you want the structured hits and their
scores) before answering anything about the user's life, preferences, people, projects or past conversations. An
empty result is informative — it tells you that you do not know, which is exactly when a model is most likely to
invent something.

**Record as you go.** Send each turn through `memory_add_message` with a stable `chat_id`. It is cheap, it does
not block, and the worker does the extraction afterwards. Do not try to decide in the moment what is worth
remembering — the extractor is deliberately selective and is better at that than an in-flight judgment.

**Write through when asked.** "Remember that I'm allergic to penicillin" should call `memory_remember`
immediately, not wait for a background pass.

**Retract on request.** "Forget that" should call `memory_forget` with the relevant `memory_id`, which you will
have from a prior search result.

A worked example, over the CLI, which uses the same service the tools do:

```bash
neuralgraph-memory remember "The user moved to Berlin in August 2026." --when 2026-08
neuralgraph-memory remember "The user's cat is named Luna and is 3 years old."
neuralgraph-memory search "where does the user live" -k 5
neuralgraph-memory context "tell me about the user's pets"
neuralgraph-memory profile user
```

To load real conversation history, `ingest` takes a JSONL chat log (one `{"chat_id", "speaker", "text",
"sent_at"?, "role"?}` per line):

```bash
neuralgraph-memory ingest chats.jsonl --chat-id imported --process
```

`--process` drains the extraction queue in the foreground so you can watch it finish instead of guessing when it
is done.

---

## 10. CLI reference

All commands are `neuralgraph-memory <command>`; `neuralgraph-memory <command> --help` lists the flags.

| Command | What it does |
|---|---|
| `serve` | Worker + dashboard + REST API + MCP over HTTP. |
| `mcp` | Worker + MCP over stdio. |
| `ingest FILE` | Import a JSONL chat log, or LoCoMo JSON with `--locomo` (`--only 0,1`). |
| `process` | Drain the extraction queue in the foreground (`--max-batches`). |
| `search QUERY` | Search memories. `-k`, `--subject`, `--history`, `--json`. |
| `context QUERY` | Print a prompt-ready context block. `-k`, `--max-chars`. |
| `profile SUBJECT` | What is known about a subject. `--json`. |
| `stats` | Counts, queue, tokens saved, grade. |
| `remember TEXT` | Store a memory now. `--subject`, `--kind`, `--when`. |
| `forget MEMORY_ID` | Retract a memory. `--reason`. |
| `maintain` | Run one maintenance pass. |

**Common flags** (accepted by every command): `--db`, `--model`, `--base-url`, `--embed-model`, `--parallel`,
`--workers`, `--batch-size`, `--debounce`, `--user-name`, `--extract-assistant` / `--no-extract-assistant`,
`--fake-llm`, `-v/--verbose`. Unambiguous prefixes work (`--user` for `--user-name`), as before.

**`serve` flags**: `--host` (default `127.0.0.1`), `--port` (default `8765`), `--mcp-token`, `--api-token`,
`--allowed-origin`, `--cors-origin`, `--allowed-host` (the last three repeatable).

Two worth understanding:

- `--debounce` (default 1.0s) waits so that a user turn and the assistant's reply are extracted together as one
  window. Lower it and you extract from half an exchange; raise it and memories appear later.
- `--no-extract-assistant` limits memory to what the human says. By default the assistant's turns are sources
  too: a recommendation, ratio, name or place the assistant gave you is stored from your side ("The user was told
  to ..."). Measured on LongMemEval this is +6.5 points of evidence recall at about 1.85x the memory count; turn
  it off if you want a smaller store or do not want the assistant's own mistakes to become memories.

---

## 11. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEURALGRAPH_MEMORY_DB` | `~/.neuralgraph/chat_memory.db` | Database path. |
| `NEURALGRAPH_MCP_TOKEN` | unset | Bearer token required on `/mcp`. |
| `NEURALGRAPH_API_TOKEN` | unset | Bearer token required on `/api/*`. |
| `LLM_BASE_URL` | `http://127.0.0.1:1234` (LM Studio) | Model server URL; port 11434 selects the Ollama API. |
| `LLM_MODEL` | `google/gemma-4-e4b` | Generation model. |
| `EMBED_MODEL` | `text-embedding-nomic-embed-text-v1.5` | Embedding model. |
| `LLM_BACKEND` | inferred | Force `ollama` or `openai` (LM Studio and other OpenAI-compatible servers). |
| `LLM_REASONING_EFFORT` | `none` | Sent to OpenAI-compatible servers; keeps thinking models from spending the budget. |
| `OLLAMA_NUM_PARALLEL` | — | Match `--parallel` when using Ollama. |
| `LLM_NO_THINK_PREFILL` | unset | Set to `1` for Qwen builds that ignore `reasoning_effort` and spend the whole output budget thinking (empty replies, retries, dead jobs). Prefills an empty think block. |

Command-line flags override environment variables.

Separate databases are just separate paths — `--db ~/work.db` and `--db ~/personal.db` share nothing.

---

## 12. Security

The server is built to run locally, and the defaults reflect that.

**Bind address.** Defaults to `127.0.0.1`. Binding to `0.0.0.0` exposes your memory database to anything that can
reach the port. If you do it, set `--mcp-token` and `--api-token` (without `--api-token`, a non-loopback `serve`
protects `/api/*` with the MCP token, and warns if neither is set).

**Tokens.** `--mcp-token` / `NEURALGRAPH_MCP_TOKEN` requires `Authorization: Bearer <token>` on `/mcp`;
comparison is constant-time. `--api-token` does the same for `/api/*`. Without a token set, no authentication is
performed — acceptable on loopback, not otherwise.

**DNS-rebinding protection.** `--allowed-origin` restricts the browser `Origin` header on `/mcp`;
`--allowed-host` restricts accepted `Host` headers, defaulting to loopback names when bound to `127.0.0.1`. These
stop a web page you visit from driving a server bound to your own machine.

**CSRF.** A state-changing request from a foreign `Origin` is refused, and JSON endpoints require
`Content-Type: application/json`, so a cross-site page cannot write or forget memories.

**CORS** on `/api/*` is off by default and must be opted into with `--cors-origin`.

**What leaves your machine:** requests to your configured model server, and nothing else. There is no telemetry
and no outbound call to any hosted service.

---

## 13. Your data

Everything lives in one SQLite file (WAL mode), by default `~/.neuralgraph/chat_memory.db`. Back it up by copying
the file. Delete it and the memory is gone.

Stored: messages, extracted memories, entities, typed relations, links between memories, provenance for each
memory (which messages it came from), and an audit log of retractions and extraction decisions.

Facts are versioned rather than overwritten. When something changes — a move, a new job — the old memory is
superseded, not deleted, and stays reachable with `include_superseded` or `memory_related`. `memory_forget`
retracts a memory and the relations grounded in it, and records that it happened.

---

## 14. Troubleshooting

**Tools do not appear in the client.** Check the absolute path to the `neuralgraph-memory` binary in your config —
the client does not inherit your shell environment. Run the exact command from the config by hand and confirm it
starts. Then check the client's own MCP logs.

**Server starts but nothing is ever extracted.** The worker needs a model. Run `stats` and look at the queue: if
jobs are accumulating, the model server is unreachable. Verify `LLM_BASE_URL` responds, and that both a
generation model and an embedding model are loaded. `--fake-llm` isolates the question — if extraction works with
it and not without, the problem is the model server.

**Searches return nothing.** Extraction is asynchronous, so there is a lag after `memory_add_message`. Run
`process` to drain the queue in the foreground, or watch the dashboard. If the queue is empty and searches are
still empty, the extractor's gate may be dropping your input as chit-chat — it is deliberately selective. Use
`remember` to confirm the read path works.

**Extraction is slow.** Raise `--parallel`, and raise `OLLAMA_NUM_PARALLEL` to match. `--batch-size` (default 2;
6 is ~2.5x faster on bulk ingest but was measured to lose ~15 pts of evidence recall) trades latency for
throughput.

**`401 unauthorized` on `/mcp`.** A token is set and the client is not sending it, or is sending a different one.
Check `Authorization: Bearer <token>`.

**`403 origin not allowed`.** `--allowed-origin` is set and the client's `Origin` header is not in the list.

**Protocol stream corrupted over stdio.** Something is writing to stdout. The server logs to stderr for exactly
this reason; check anything you have wrapped around the process.

---

## 15. Upgrading from the Python version

Earlier releases ran as `python -m NeuralGraph.chat_memory`. The Rust server is a drop-in replacement:

- **Your database works as is.** Same schema, same JSON and embedding encodings, same full-text index; point
  `--db` (or `NEURALGRAPH_MEMORY_DB`) at the existing file. The test suite opens, searches and writes a database
  created by the Python version (`tests/golden/ingest_user_named.sqlite`).
- **Same commands and flags.** Replace `python -m NeuralGraph.chat_memory` with `neuralgraph-memory`; subcommands,
  flags, environment variables, HTTP routes, JSON shapes and MCP tools are unchanged.
- **Update your MCP client config**: the command is now the binary path and `PYTHONPATH` is no longer needed.

The parity evidence is `tests/golden.rs`: the fixtures in `tests/golden/` were recorded from the Python
implementation at `f4311102` by `tests/golden/generate.py`. That script was removed together with the last of the
Python and is recoverable from git history (`git show eba2f370:crates/neuralgraph-memory/tests/golden/generate.py`).
The fixtures are the contract: change one only deliberately, in a commit that says why, never to make a test pass.

---

## Related

- [`docs/CHAT_MEMORY.md`](../../docs/CHAT_MEMORY.md) — architecture and design rationale
- [`../../README.md`](../../README.md) — NeuralGraph overview
- `cargo run --release --example chat_memory_live_demo -- --fake-llm` — end-to-end demo; `--fake-llm` needs no
  model server
