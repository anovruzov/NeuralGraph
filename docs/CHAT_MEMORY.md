# Cross-chat memory (`NeuralGraph.chat_memory`)

Durable memory across every chat, generated automatically in the background by a local Qwen model,
searchable from Python, the CLI, a REST API, a live dashboard, and from Claude through MCP.

```
chat app / Claude ──POST /api/messages, memory_add_message──▶ messages (SQLite, WAL)
                                                                  │  job queue (leased, retried, dead-lettered)
                                                                  ▼
                                   MemoryWorker ── N parallel passes ── Qwen via llm_backend
                                       gate → extract memories + relations → ground → reconcile → commit (1 txn)
                                                                  │
                          memories · entities · relations · links · provenance · audit
                                                                  │
   search / context_for / profile ◀── MemoryRetriever (vector + BM25 + entity graph, RRF, subject routing)
   dashboard  /            REST  /api/*            MCP  /mcp (Streamable HTTP)   ·   stdio
```

## Why it is built this way

The LoCoMo campaign (`docs/research/REPORT.md`) showed three things that carry over directly:

* **Identity must come from metadata, never from text.** Every memory carries a canonical `subject`; a query that
  names a person routes to that person's memories through the entity registry and its aliases, not through
  embedding similarity of the name.
* **Ranks fuse, scores don't.** The three retrieval channels are combined with Reciprocal Rank Fusion.
* **Candidate precision matters as much as recall.** The extractor is *selective*: a cheap gate drops
  chit-chat before any model call, the prompt asks for few durable memories, every candidate must be grounded
  in the window (no hallucinated names), and duplicates/updates/contradictions are reconciled against what
  is already stored instead of piling up.

Everything the worker decides for a batch is written in **one SQLite transaction** together with the job
acknowledgement, and the commit is **fenced** on the worker's lease. A crash before the commit leaves nothing
behind; a zombie worker whose lease expired cannot double-write. That is what makes retries safe.

## Quick start

```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python aiohttp numpy rank-bm25
# local model: Ollama ...
ollama pull qwen2.5:7b-instruct && ollama pull nomic-embed-text
export LLM_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text
# ... or LM Studio (default http://127.0.0.1:1234, google/gemma-4-e4b + text-embedding-nomic-embed-text-v1.5)

# run everything: worker + dashboard + REST + MCP
.venv/bin/python -m NeuralGraph.chat_memory serve --user-name "Your Name" --parallel 4
# open http://127.0.0.1:8765/
```

Python:

```python
from NeuralGraph.chat_memory import ChatMemory

async with ChatMemory("~/.neuralgraph/chat_memory.db") as cm:        # worker starts in the background
    await cm.add_message("chat-42", "user", "I moved to Berlin last month and my cat Luna is 3")  # ~1 ms, no LLM
    await cm.add_message("chat-42", "assistant", "Congrats on the move!", role="assistant")
    ...
    block = await cm.context_for("where does the user live?")      # prepend to the next prompt
    hits = await cm.search("Luna", k=5)                            # RetrievedMemory: memory, score, channels, sources
    prof = await cm.profile("user")
```

No model server? `--fake-llm` (CLI) or `llm=scripted_fake_llm()` (Python) runs a deterministic stand-in.

## What gets stored

| Table | Contents |
|---|---|
| `chats`, `messages` | raw transcript, ordered by `seq` per chat, `status` pending → processed / skipped / failed, idempotent by content hash |
| `memories` | one self-contained third-person sentence each: `kind` (fact, preference, event, plan, relationship, opinion, identity, task), `subject`, `speaker`, `importance`, `confidence`, `event_time` (+precision), `observed_at`, `status` (active / superseded / retracted), `version`, embedding |
| `memory_sources` | provenance: which messages support a memory |
| `entities`, `entity_aliases`, `memory_entities` | canonical entity registry shared across chats, aliases ("Mel" → melanie), mentions |
| `relations` | typed triples `subject —predicate→ object` with confidence, observation count, provenance; single-valued predicates (`lives_in`, `works_at`, …) supersede older objects |
| `memory_links` | `supersedes`, `contradicts`, `related`, `elaborates` between memories |
| `jobs` | durable queue: `queued → leased → done | dead`, attempts, backoff, lease expiry |
| `audit_log` | every add / update / contradict / duplicate / reject decision with its reason |

Memory text is indexed in SQLite FTS5 (Porter stemming) when available; otherwise an in-process BM25 is used.

## The worker

`MemoryWorker` runs `concurrency` asyncio loops. Each loop leases the oldest runnable job; the SQL lease
only hands out a chat's message when no earlier message of that chat is still queued or leased, so **chats
run in parallel and each chat stays in order**. Up to `batch_size` consecutive messages of a chat are
processed in one pass (a user turn and its reply usually land together thanks to `debounce_seconds`).

One pass:

1. **Gate** – greetings, acknowledgements, assistant boilerplate are marked `skipped` without a model call.
   Assistant turns are context only (never a memory source) unless `extract_assistant=True`.
2. **Extract** – two concurrent Qwen calls: memories (`MEMORY_PROMPT`) and entities + triples
   (`RELATION_PROMPT`), both with the known-entity registry, recent memories about the speakers, prior
   context, and relative dates pre-annotated (`yesterday [= 7 May 2023]`).
3. **Normalise & ground** – names resolve through the registry (exact, alias, first-name, fuzzy); `when`
   becomes an ISO date + precision; candidates with hallucinated names, no lexical overlap with the window,
   assistant-only sources, or low importance are rejected (each rejection is audited).
4. **Reconcile** – each candidate is embedded and compared with the subject's active memories:
   cosine ≥ `dedupe_cosine` → duplicate (no LLM); ≥ `reconcile_cosine` → one `RECONCILE_PROMPT` call
   decides **ADD / DUPLICATE / UPDATE / CONTRADICT**; otherwise ADD. UPDATE and CONTRADICT create a new
   version and supersede the old one — but an observation *older* than the stored fact never overrides it
   (bulk-importing old chats after new ones is safe).
5. **Commit** – `ChatMemoryStore.apply_plan` writes memories, entities, relations, links, provenance,
   message and job status atomically.

Failures back off exponentially (`backoff_base`, `backoff_max`) and dead-letter after `max_attempts`; the
raw message keeps its text and is marked `failed` (`retry_dead_jobs` re-queues). Expired leases are swept on
start and periodically. If only the embedding endpoint is down, memories are stored without vectors and
maintenance back-fills them. Maintenance (`maintenance_interval`) also merges near-duplicate memories that
two chats produced at the same moment and prunes old finished jobs.

## Retrieval

`MemoryRetriever.search(query, k, subject, speaker, chat_id, kinds, since, until, include_superseded)`

* **vector** – cosine over a numpy matrix of active memory embeddings (cached per store revision; filters
  are masks, so tens of thousands of memories stay in the low milliseconds); floor `vector_min_sim`.
* **keyword** – FTS5 bm25 (Porter) or in-process BM25.
* **graph** – entities named in the query (longest alias match) → memories that mention them, plus one typed
  hop over relations; hub entities are down-weighted by inverse frequency.
* fused with RRF, then priors: importance, mild recency, ×`subject_boost` when the query names the memory's
  subject, ×`entity_boost` when it names a mentioned entity.

Superseded memories are hidden unless `include_superseded=True` (then the history chain is appended).
`context_for()` renders the top hits as a compact block with an evidence floor (a line needs a keyword or
graph hit, or cosine ≥ `context_min_vector_sim`) and a relative-score cutoff, and updates the token ledger.

## Dashboard, REST API, tokens saved, pentagon grade

`serve` hosts a single self-contained page at `/` that polls `/api/status` every two seconds:

* **Avatar** – pulses while the worker is busy, shows the mood (idle / learning / waiting / error) and the
  hero figure **tokens saved** = tokens of raw chat text the worker has digested minus tokens of the active
  memories that replace it (plus how many raw tokens `context_for` calls avoided re-reading).
* **Pentagon grade** – five axes, 0–100, single series, hover for definitions, table beside it:
  coverage (share of messages digested), compression (log-scale token ratio; 4× = 100), connectivity
  (relations + links per memory), freshness (worker lag behind the newest message), reliability
  (failed batches and dead jobs). Overall → S / A / B / C / D.
* stat tiles, live memory stream, entity graph, recent audit events, an "ask memory" box and a "feed a chat
  turn" form.

REST: `GET /api/status`, `GET /api/search?q=&k=&subject=&chat_id=&kinds=&since=&until=&history=1`,
`GET /api/context?q=&k=&max_chars=`, `GET /api/memories`, `GET /api/entities`, `GET /api/profile/{subject}`,
`POST /api/messages` (`{"chat_id","speaker","text","role"?,"sent_at"?,"message_id"?}` → 202),
`POST /api/messages/batch`, `POST /api/remember`, `POST /api/forget`, `POST /api/maintain`, `GET /healthz`.
Optional `--api-token` requires `Authorization: Bearer …` on `/api/*`.

## Claude access through MCP (edge and cloud)

The same tool set is exposed over two transports (`NeuralGraph/chat_memory/mcp_server.py`):

| tool | what it does |
|---|---|
| `memory_search` | hybrid search across all chats, with provenance and history |
| `memory_context` | prompt-ready block for a query |
| `memory_add_message` | record a conversation turn (queued; extraction happens in the background) |
| `memory_remember` | store an explicit fact immediately ("remember that …") |
| `memory_profile` | everything known about a subject |
| `memory_related` | links and history of one memory |
| `memory_entities` | top entities or one entity's relations |
| `memory_forget` | retract a memory |
| `memory_status` | counts, queue, tokens saved, grade |

Resources `memory://status`, `memory://entities`, `memory://profile/{subject}`; prompt `recall`.

**Edge / local (stdio)** – runs next to your Ollama, no network:

```bash
# Claude Code
claude mcp add neuralgraph-memory -- /path/to/.venv/bin/python -m NeuralGraph.chat_memory mcp --db ~/.neuralgraph/chat_memory.db
```
```json
// Claude Desktop: claude_desktop_config.json
{"mcpServers": {"neuralgraph-memory": {"command": "/path/to/.venv/bin/python",
  "args": ["-m", "NeuralGraph.chat_memory", "mcp", "--db", "/Users/you/.neuralgraph/chat_memory.db"],
  "env": {"LLM_BASE_URL": "http://localhost:11434", "LLM_MODEL": "qwen2.5:7b-instruct", "EMBED_MODEL": "nomic-embed-text"}}}}
```

**Cloud / remote (Streamable HTTP)** – `serve` mounts the endpoint at `/mcp` next to the dashboard:

```bash
NEURALGRAPH_MCP_TOKEN=change-me .venv/bin/python -m NeuralGraph.chat_memory serve --host 0.0.0.0 --port 8765
claude mcp add --transport http neuralgraph-memory https://memory.example.com/mcp --header "Authorization: Bearer change-me"
```

Put it behind TLS (any reverse proxy), add it to claude.ai as a custom connector, or call it from the
Messages API with the MCP connector (`mcp_servers=[{"type": "url", "url": "https://memory.example.com/mcp",
"name": "memory", "authorization_token": "change-me"}]` plus `tools=[{"type": "mcp_toolset",
"mcp_server_name": "memory"}]`, beta `mcp-client-2025-11-20`). The transport implements MCP 2025-06-18
(with 2025-03-26 / 2024-11-05 negotiation): JSON or SSE responses, `Mcp-Session-Id`, `MCP-Protocol-Version`,
bearer auth, optional `--allowed-origin` (DNS-rebinding protection), CORS. Verified against the official
`mcp` Python SDK client over both transports.

## CLI

```
python -m NeuralGraph.chat_memory serve|mcp|ingest|process|search|context|profile|stats|forget|remember|maintain
  --db PATH  --model M  --base-url URL  --embed-model E  --parallel N  --workers N  --batch-size N
  --debounce SECONDS  --user-name NAME  --extract-assistant  --fake-llm  -v
ingest FILE.jsonl [--chat-id ID] [--process]        # {"chat_id","speaker","text","sent_at"?,"role"?} per line
ingest evaluation/locomo/locomo10.json --locomo --only 0,1 --process
```

`--parallel` bounds concurrent model calls (match `OLLAMA_NUM_PARALLEL`); `--workers` bounds chats in flight
(each pass makes 2+ concurrent calls).

## Configuration knobs (`ChatMemoryConfig`)

`extraction`: `context_messages` 6, `max_memories_per_batch` 12, `min_importance` 0.3, `min_confidence` 0.45,
`dedupe_cosine` 0.94, `reconcile_cosine` 0.62, `related_cosine` 0.55, `extract_assistant` False,
`annotate_relative_dates` True, `min_grounding_overlap` 0.2, `require_name_grounding` True,
`speaker_names` ({"user": "Your Name"} so first person resolves to a real subject).
`worker`: `concurrency` 2, `batch_size` 6, `lease_seconds` 300, `backoff_base` 5, `backoff_max` 600,
`maintenance_interval` 900, `merge_cosine` 0.96. `retrieval`: `depth` 100, `rrf_k` 60, channel weights,
`subject_boost` 1.6, `entity_boost` 1.25, `recency_half_life_days` 365, `vector_min_sim` 0.05,
`context_min_vector_sim` 0.3. The cosine thresholds were set for nomic-embed-text-class models; re-tune them
for another embedding model.

## Tests

```bash
.venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_store NeuralGraph.tests.test_chat_memory_extraction \
  NeuralGraph.tests.test_chat_memory_worker NeuralGraph.tests.test_chat_memory_retrieval NeuralGraph.tests.test_chat_memory_mcp
```

All tests use temporary SQLite files and a deterministic fake model (`FakeLLMClient`, hashed bag-of-words
embeddings); they cover idempotent ingestion, per-chat ordering with cross-chat parallelism, backoff and
dead-lettering, restart recovery of expired leases, lease-fenced commits, reconciliation decisions and the
"older observation never wins" rule, grounding rejections, embedding outages, retrieval channels and filters,
superseded handling, the token ledger and grade, and the MCP protocol over stdio and HTTP.
(`pytest` cannot collect from the repo root because of the stale root `__init__.py`; use `unittest`.)

## Deployment

* **Container (cloud):** `docker build -f deploy/chat_memory/Dockerfile -t neuralgraph-memory .` or
  `docker compose -f deploy/chat_memory/docker-compose.yml up` (memory server + Ollama, volumes for both).
  Set `NEURALGRAPH_MCP_TOKEN` / `NEURALGRAPH_API_TOKEN`, terminate TLS in front, and point Claude at
  `https://your-host/mcp`.
* **Edge (laptop / mini-PC):** `deploy/chat_memory/neuralgraph-memory.service` runs `serve` as a systemd
  user service next to a local Ollama; Claude Desktop/Code talk to it over stdio (`mcp`) or to the local
  HTTP endpoint. Everything is one Python process plus one SQLite file; no other services.
* Read caches are keyed on SQLite's `data_version` as well as the in-process write counter, so a reader in a
  second process (for example `search` on the CLI while `serve` is running) never serves stale indexes.

## Known limits

* Speaker identity is by display name; two different people called "Alex" in different chats would share
  memories unless the caller passes a stable id as `speaker` and maps it via `speaker_names`.
* Extraction quality is prompt-bound to a 7B model; the reconcile decision (UPDATE vs CONTRADICT vs ADD) is
  the weakest step, and it is biased towards ADD, which keeps the store safe but leaves near-duplicates for
  maintenance to merge. Thresholds are exposed for tuning against a labelled sample of your own chats.
* Run one *worker* per database (`serve` or `mcp`); read-only CLI commands can run alongside it.
