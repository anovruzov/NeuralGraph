# Cross-chat memory (`neuralgraph-memory`)

Durable memory across every chat, generated automatically in the background by a local Qwen model,
searchable from Rust, the CLI, a REST API, a live dashboard, and from Claude through MCP. The implementation is
the Rust crate [`crates/neuralgraph-memory`](../crates/neuralgraph-memory/) (one binary, one SQLite file); the
user guide is its [README](../crates/neuralgraph-memory/README.md).

```
chat app / Claude ──POST /api/messages, memory_add_message──▶ messages (SQLite, WAL)
                                                                  │  job queue (leased, retried, dead-lettered)
                                                                  ▼
                                   MemoryWorker ── N parallel passes ── Qwen via backend.rs
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
cargo install --locked --path crates/neuralgraph-memory
ollama pull qwen2.5:7b-instruct && ollama pull nomic-embed-text
export LLM_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text
neuralgraph-memory serve --user-name "Your Name" --parallel 4
```

Then open http://127.0.0.1:8765/. `serve` runs the worker, dashboard, REST API and MCP together.
To use LM Studio instead of Ollama, skip the `ollama` and `export` lines: the defaults point at
LM Studio (`http://127.0.0.1:1234`, `google/gemma-4-e4b`, `text-embedding-nomic-embed-text-v1.5`).

As a library (`neuralgraph_memory` crate):

```rust
use neuralgraph_memory::{ChatMemory, ChatMemoryConfig, MessageInput, SearchOptions};

let cm = ChatMemory::open("~/.neuralgraph/chat_memory.db", None, ChatMemoryConfig::default())?;
cm.start().await?;                                                    // worker starts in the background
cm.add_message("chat-42", "user", "I moved to Berlin last month and my cat Luna is 3", MessageInput::default())?;
let block = cm.context_for("where does the user live?", None, None, &SearchOptions::default()).await?;
let hits = cm.search("Luna", &SearchOptions { k: 5, ..Default::default() }).await?;   // memory, score, channels, sources
let prof = cm.profile("user", 60)?;
cm.close().await;
```

No model server? `--fake-llm` (CLI) or `Some(Arc::new(testing::scripted_fake_llm()))` (library) runs a
deterministic stand-in.

**Live demo:** `cargo run --release --example chat_memory_live_demo -- --fake-llm` starts everything, replays
`demo/data/sample_chats.jsonl` with human pacing, calls `memory_context`/`memory_search` over the
MCP endpoint the way Claude would, prints what the worker learned after each chat, and keeps serving at the
end (`--screenshots` saves a storyboard, `--locomo 0` adds a 400-message chat for volume, `--no-serve` exits
when the story ends; drop `--fake-llm` to use your local model).

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

`MemoryWorker` runs `concurrency` tokio tasks. Each loop leases the oldest runnable job; the SQL lease
only hands out a chat's message when no earlier message of that chat is still queued or leased, so **chats
run in parallel and each chat stays in order**. Up to `batch_size` consecutive messages of a chat are
processed in one pass (a user turn and its reply usually land together thanks to `debounce_seconds`).

One pass:

1. **Gate** – greetings, acknowledgements, assistant boilerplate are marked `skipped` without a model call.
   Assistant turns are sources too by default (facts the assistant gave the user, phrased from the user's side);
   `extract_assistant=False` makes them context only.
2. **Extract** – two concurrent model calls: memories (`MEMORY_PROMPT`) and entities + triples
   (`RELATION_PROMPT`), both with the known-entity registry, recent memories about the speakers, prior
   context, and relative dates pre-annotated (`yesterday [= 7 May 2023]`).
3. **Normalise & ground** – names resolve through the registry (exact, alias, first name → known full name,
   fuzzy; a longer name never collapses onto a shorter known one); `when` becomes an ISO date + precision;
   candidates with names that neither the window nor the pre-existing registry contains (the model's own new
   entity list cannot vouch for a name it invented), no lexical overlap with the window (skipped for very
   short identity facts), assistant subjects or assistant-only sources, or low importance are rejected, and
   every relation endpoint must occur in the window or be already known (each rejection is audited).
4. **Reconcile** – each candidate is embedded and compared with the subject's active memories:
   cosine ≥ `dedupe_cosine` → duplicate (no LLM); ≥ `reconcile_cosine` → one `RECONCILE_PROMPT` call
   decides **ADD / DUPLICATE / UPDATE / CONTRADICT**; otherwise ADD. UPDATE and CONTRADICT create a new
   version and supersede the old one (an UPDATE keeps the old memory's date, importance, entities and
   sources and re-embeds the merged sentence; two corrections of the same fact in one batch chain instead of
   both staying active) — but an observation *older* than the stored fact never overrides it
   (bulk-importing old chats after new ones is safe), and a concurrent plan from another chat that already
   replaced the same target is reconciled at commit time by observation time.
5. **Commit** – `ChatMemoryStore.apply_plan` writes memories, entities, relations, links, provenance,
   message and job status atomically.

A model answer with no JSON at all is never silently dropped: the batch fails and is retried with variation
(a repair instruction is appended, the output budget grows, and the batch is halved on every attempt so one
bad message cannot poison its neighbours). Failures back off exponentially (`backoff_base`, `backoff_max`)
and dead-letter after `max_attempts`; the raw message keeps its text and is marked `failed`
(`retry_dead_jobs` re-queues). Expired leases are swept on
start and periodically. If only the embedding endpoint is down, memories are stored without vectors and
maintenance back-fills them. Maintenance (`maintenance_interval`) also merges near-duplicate memories that
two chats produced at the same moment and prunes old finished jobs.

## Retrieval

`MemoryRetriever.search(query, k, subject, speaker, chat_id, kinds, since, until, include_superseded)`

* **vector** – cosine over float32 matrices of memory embeddings (one per embedding dimension, refreshed
  incrementally with the rows written since the last query, so an ingest-heavy server never rebuilds the
  whole index; filters are masks, large indexes are scanned on every core while the keyword query runs);
  floor `vector_min_sim`. Changing the embedding model is safe: old rows keep matching queries of their own
  dimension until maintenance re-embeds them (`reembed_limit` per run).
* **keyword** – FTS5 bm25 (Porter, accent-folded, possessives stripped) or in-process BM25; subject/chat/
  speaker filters are pushed into the query so a filtered search is never starved by the global limit.
* **graph** (opt-in, `channels="VKG"`; off by default because it cost up to 9 pts of evidence recall on LoCoMo by pulling related non-answers into the context) – entities named in the query (longest alias match) → memories that mention them, plus one typed
  hop over relations; hub entities are down-weighted by inverse frequency.
* fused with RRF, then priors: importance, mild recency, ×`subject_boost` when the query names the memory's
  subject, ×`entity_boost` when it names a mentioned entity.

Superseded memories are hidden unless `include_superseded=True` (then the history chain is appended) or the
query carries a time window (`since`/`until`): a fact that was later replaced was still the truth back then,
so "where did Ali live in March?" returns the Berlin memory marked `superseded by …`. Single-valued relations
(`lives_in`, `works_at`, …) follow the same rule: the observation with the latest *source* time is current,
whatever order the chats were ingested in.
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
`POST /api/messages/batch`, `POST /api/remember`, `POST /api/forget`, `POST /api/maintain`,
`POST /api/answer` (`{"question","today"?}` → `{"text","routed","cascaded","context","calls","seconds"}`), `GET /healthz`.
Optional `--api-token` requires `Authorization: Bearer …` on `/api/*`.

Security defaults: **no CORS** (a page on another origin cannot read the API; add `--cors-origin https://…`
or `*` deliberately); **CSRF protection** (a state-changing request from a foreign `Origin` is refused, and
JSON endpoints require `Content-Type: application/json`, so a cross-site page cannot write or forget memories
even with a body that skips the browser preflight); a **Host allow-list** when bound to loopback
(`--allowed-host` to override) so DNS rebinding cannot reach a local server; bearer tokens for `/api/*` and
`/mcp` compared in constant time; when `serve` binds a non-loopback address the MCP token also protects
`/api/*` unless `--api-token` is given (and a warning is printed if neither is set); `--allowed-origin` for
browser clients of `/mcp`; an 8 MB request-body cap; bounded MCP session table. The dashboard sends the API
token itself: open `http://host:8765/?token=…` once (it is remembered per browser) or enter it when prompted.

## Claude access through MCP (edge and cloud)

The same tool set is exposed over two transports (`crates/neuralgraph-memory/src/mcp.rs`):

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
| `memory_answer` | answer a question with the routed reader protocol (see [Recommended reader protocol](#recommended-reader-protocol)) |

Resources `memory://status`, `memory://entities`, `memory://profile/{subject}`; prompt `recall`.

**Edge / local (stdio)** – runs next to your Ollama, no network:

Claude Code:

```bash
claude mcp add neuralgraph-memory -- "$(command -v neuralgraph-memory)" mcp --db ~/.neuralgraph/chat_memory.db
```
```json
// Claude Desktop: claude_desktop_config.json
{"mcpServers": {"neuralgraph-memory": {"command": "/Users/you/.cargo/bin/neuralgraph-memory",
  "args": ["mcp", "--db", "/Users/you/.neuralgraph/chat_memory.db"],
  "env": {"LLM_BASE_URL": "http://localhost:11434", "LLM_MODEL": "qwen2.5:7b-instruct", "EMBED_MODEL": "nomic-embed-text"}}}}
```

**Cloud / remote (Streamable HTTP)** – `serve` mounts the endpoint at `/mcp` next to the dashboard:

```bash
NEURALGRAPH_MCP_TOKEN=change-me neuralgraph-memory serve --host 0.0.0.0 --port 8765
claude mcp add --transport http neuralgraph-memory https://memory.example.com/mcp --header "Authorization: Bearer change-me"
```

Put it behind TLS (any reverse proxy), add it to claude.ai as a custom connector, or call it from the
Messages API with the MCP connector (`mcp_servers=[{"type": "url", "url": "https://memory.example.com/mcp",
"name": "memory", "authorization_token": "change-me"}]` plus `tools=[{"type": "mcp_toolset",
"mcp_server_name": "memory"}]`, beta `mcp-client-2025-11-20`). The transport implements MCP 2025-06-18
(with 2025-03-26 / 2024-11-05 negotiation): JSON or SSE responses, `Mcp-Session-Id`, `MCP-Protocol-Version`,
bearer auth, optional `--allowed-origin` (DNS-rebinding protection), CORS. The Python server it replaces was
verified against the official `mcp` Python SDK client over both transports; the Rust server answers the same
requests with byte-identical JSON-RPC payloads (`tests/golden/mcp.json`, `tests/golden/rest.json`).

## CLI

```
neuralgraph-memory serve|mcp|ingest|process|search|context|profile|stats|forget|remember|maintain
  --db PATH  --model M  --base-url URL  --embed-model E  --parallel N  --workers N  --batch-size N
  --debounce SECONDS  --user-name NAME  --no-extract-assistant  --fake-llm  -v
serve --host --port --mcp-token --api-token --allowed-origin --cors-origin --allowed-host
ingest FILE.jsonl [--chat-id ID] [--process]        # {"chat_id","speaker","text","sent_at"?,"role"?} per line
ingest research/datasets/locomo10.json --locomo --only 0,1 --process
```

`--parallel` bounds concurrent model calls (match `OLLAMA_NUM_PARALLEL`); `--workers` bounds chats in flight
(each pass makes 2+ concurrent calls).

## Configuration knobs (`ChatMemoryConfig`)

`extraction`: `context_messages` 6, `max_memories_per_batch` 12, `min_importance` 0.3, `min_confidence` 0.45,
`dedupe_cosine` 0.94, `reconcile_cosine` 0.62, `related_cosine` 0.55, `extract_assistant` True (LongMemEval: 87.0 -> 93.5 turn@15),
`annotate_relative_dates` True, `min_grounding_overlap` 0.2, `require_name_grounding` True,
`speaker_names` ({"user": "Your Name"} so first person resolves to a real subject).
`worker`: `concurrency` 2, `batch_size` 2 (measured: 6 loses ~15 pts of evidence recall on LoCoMo), `lease_seconds` 300, `backoff_base` 5, `backoff_max` 600,
`maintenance_interval` 900, `merge_cosine` 0.96. `retrieval`: `channels` "VK" (graph channel opt-in: it never helped on LoCoMo), `context_k` 15, `context_max_chars` 6000 (3600 before 2026-10-28), `context_raw_messages` 3 (0 before; see Episodic recall), `depth` 100, `rrf_k` 60, channel weights,
`subject_boost` 1.6, `entity_boost` 1.25, `recency_half_life_days` 365, `vector_min_sim` 0.05,
`context_min_vector_sim` 0.3, `context_min_relative_score` 0.25, `importance_weight` 0.35, `recency_weight` 0.2, channel weights 1.0 / 0.8 / 0.7 (vector / keyword / graph), `min_channel_hits` 1, `context_multi_query` false (opt-in), `temporal_prior` 0.0 (off). The cosine thresholds were set for nomic-embed-text-class models; re-tune them
for another embedding model.

### Source quotes (`retrieval.context_quotes*`)

| field | default | meaning |
|---|---|---|
| `context_quotes` | 1 | quotes rendered under each memory line of the context block (default since commit c3899d07; `0` restores the plain block) |
| `context_quote_chars` | 300 | characters per quote, cut at a sentence end past half the limit, else hard-cut with "…" |
| `context_quotes_human_only` | true | skip assistant turns as quotes; quotes are also deduplicated across memories |

Quotes only use the budget left after the memory lines, so they never displace a memory. The default is `1`; set `0` for the pre-2026-10-28 block.
Measured on fresh ingests with the shipped 3600-character budget and the IDK cascade below (Qwen3.8-9B answerer,
Gemma-4-e4b and Qwen3.6-35B-A3B judges, details in [BENCHMARKS.md](BENCHMARKS.md#track-d-2026-10-28-overnight-campaign-source-quotes--idk-cascade)):

| set | strict (Gemma) | strict (Qwen-35B) | "I don't know" |
|---|---:|---:|---:|
| LongMemEval Oracle, 50 questions: off -> `context_quotes = 1` | 42.0 -> 50.0 | 44.0 -> 54.0 | 24 -> 14 |
| LoCoMo conversation 0, 66 questions: off -> `context_quotes = 1` | 50.0 -> 50.0 | 56.1 -> 62.1 | 15 -> 9 |

These were measured at the 3600-character budget that was the default then; the default budget is now 6000 (next section).
`context_quotes = 2` was not better (LongMemEval 48.0 / 54.0, LoCoMo 51.5 / 59.1). The context block grows from about 560 to
about 780 tokens on LongMemEval. One question is 2 points on LongMemEval and 1.5 on LoCoMo, so these are single-run results,
and one judge prompt (Gemma `strict_v2`) scored LoCoMo 6 points lower with quotes (4 wins, 8 losses); the Qwen-35B judge did not.

### Episodic recall and the context budget (`retrieval.context_raw_messages*`, `context_max_chars`)

| field | default | meaning |
|---|---|---|
| `context_max_chars` | 6000 | character budget of the whole context block (3600 until 2026-10-28) |
| `context_raw_messages` | 3 | up to this many raw chat messages matching the question (BM25 over the message store) are appended after the memory lines under a separate header; `0` turns episodic recall off |
| `context_raw_message_chars` | 400 | characters per raw message, cut at a sentence end |
| `context_raw_include_assistant` | false | allow assistant turns in the raw block; messages already quoted under a memory are skipped |

The raw block spends only the budget left after memories and quotes. Extracted memories lose detail the user later asks about (a
price, a date, a second figure that was said in passing); keeping a few verbatim turns beside the facts is the technique several
published systems credit. Both defaults were flipped in one commit; the reader below is client-side and is not part of it. `context_max_chars = 3600` and
`context_raw_messages = 0` restore the previous block (about 770 tokens on LongMemEval instead of about 1020).

Measured against the previous defaults (quotes + concise prompt + IDK cascade, 3600 characters, no raw turns), paired on identical
databases, reader = the thinking compute prompt on every question, the variant that preceded the routed protocol below (Qwen3.8-9B; details, columns and the judge audit in
[BENCHMARKS.md](BENCHMARKS.md#round-2-3-episodic-recall-context-budget-reasoning-reader)):

| set | strict (Gemma) | strict_v2 | official LongMemEval judge | "I don't know" |
|---|---:|---:|---:|---:|
| LongMemEval Oracle 50, clean ingest | 62 -> 66 | 56 -> 76 (11:1, p .006) | 58 -> 70 (7:1, p .07) | 15 -> 11 |
| LongMemEval Oracle 50, first ingest (replication) | 50 -> 62 | 52 -> 70 (11:2, p .022) | 48 -> 68 (11:1, p .006) | 14 -> 6 |
| LoCoMo conversation 0, 66 questions | 50.0 -> 56.1 (12:8, p .50) | 56.1 -> 66.7 (12:5) | n/a | 9 -> 10 |

How much of that is retrieval, how much is the reader (clean ingest, strict_v2 / official): the 6000-character budget alone with the
old reader 56 -> 64 / n/a; the thinking reader alone on the old 3600 block 56 -> 68 / 58 -> 64; raw turns plus the old reader 56 -> 68 /
58 -> 62; all three 56 -> 76 / 58 -> 70. A hand audit of the 17 flipped questions put the judge-corrected gain at about +12
`strict_v2` and +10 official points: most of it is the thinking reader, the raw turns add about +2 to +4 on top of it, and assistant
turns in the raw block did not help (`strict_v2` +2, 4:3, lenient non-IDK -2). Under thinking on every question LoCoMo was neutral on conversation 0 and regressed on conversation 1 (strict 57.9 -> 50.0); the routed reader below is neutral to positive on it. One question is 2 points on
LongMemEval and 1.5 on LoCoMo, and every row is a single run; one ingest was used to choose the configuration and the other to
check it.

### `NEURALGRAPH_EXP` (experiment hook)

`ChatMemory::open` reads `NEURALGRAPH_EXP`, a comma-separated list of tokens that override the config for benchmark runs
(`ChatMemoryConfig::apply_exp`). Unknown or malformed tokens warn on stderr and are ignored. It is an experiment hook, not a
supported interface: tokens may change or move into the config between releases. Example:
`NEURALGRAPH_EXP=hyphen_ground,num_dedupe,context_quotes=1` (a bare token means `=1`; `=0` turns a switch off).

| token | effect |
|---|---|
| `hyphen_ground[=0\|1]` | name grounding accepts the parts of hyphenated names ("Red-headed"); default off |
| `num_dedupe[=0\|1]` | a candidate with different numbers than the existing memory is never a cosine duplicate; default off |
| `months_ago[=0\|1]` | annotate "N weeks/months/years ago" with the resolved date (default off) |
| `retry_zero[=0\|1]` | re-ask once when a batch with 2 or more substantive messages yields no memory (default off) |
| `verbatim_fallback[=0\|1]` | sentences with a number that no memory mentions become memories (default off) |
| `verbatim_max=N` | cap of those per batch (default 3) |
| `prompt_extra_file=PATH` | insert the file's text into the memory prompt's rules block (number its rules from 10) |
| `batch_size=N` | messages per extraction pass |
| `context_messages=N` | earlier messages shown as context |
| `context_quotes=N` | same as `retrieval.context_quotes` |
| `context_quote_chars=N` | same as `retrieval.context_quote_chars` |
| `context_quotes_human_only=0\|1` | same as `retrieval.context_quotes_human_only` |
| `context_k=N` | memories in the context block (default 15) |
| `context_max_chars=N` | character budget of the context block (default 6000) |
| `context_raw_messages=N` | episodic recall: raw matching messages appended to the block (default 3; `0` = off) |
| `context_raw_message_chars=N` | characters per raw message (default 400) |
| `context_raw_include_assistant=0\|1` | allow assistant-role raw messages (default 0) |
| `context_multi_query[=0\|1]` | rule-based sub-queries (up to 3) merged into the context search; opt-in, default off (+1 question on each LongMemEval ingest, noise) |
| `temporal_prior=W` | rule-based temporal prior from the question's time cues, W >= 0; default 0 = off (touches 3 of 50 LongMemEval questions, noise) |

The two extraction fixes `hyphen_ground` and `num_dedupe` were measured together on an 18-question LongMemEval subset only:
evidence@15 83.3 -> 94.4 against a same-config re-ingest. Extraction is not run-to-run stable (one question had 7 memories in one
run and 18 in another), so a gain of under about three questions is not evidence. `hyphen_ground` and `num_dedupe` stay default off. They were measured on a full LongMemEval-50 re-ingest (evidence@15 95.7 -> 97.8, plain-context strict 42 -> 60 under the Gemma judge), but an unchanged-config re-ingest of the same 50 histories alone moved strict 42 -> 54, so the gain is within the ±12-point run-to-run variance of extraction (see the noise floor in `docs/BENCHMARKS.md`). Enable them with `NEURALGRAPH_EXP=hyphen_ground,num_dedupe`; mechanism-level evidence is in `research/overnight/judgments/lme50_E56.md`.

### Recommended reader protocol

The model that reads the context block decides how much of the stored evidence reaches the answer. The memory delivers the evidence
on about 94-98% of the LongMemEval Oracle questions (turn@15), but a concise 9B reader answers "I don't know" on questions whose
answer is derivable from the memories and does not total or subtract across them. The protocol lives in the client, not in the
crate. Measured on the same databases (Qwen3.8-9B reader, Gemma-4-e4b judges; details in
[BENCHMARKS.md](BENCHMARKS.md#round-2-3-episodic-recall-context-budget-reasoning-reader)).

**Recommended: routed.** Thinking helps arithmetic and date questions and costs accuracy on short-phrase answers, so only the
first kind gets it.

1. Build the context block with the crate defaults (source quotes, 6000 characters, 3 episodic user turns).
2. Classify the question with a regex. The harness uses `COMPUTE_RE` in `research/overnight/scripts/answer_eval.py`: how long /
   how many / how much / how old / total / in total / altogether / combined / sum of / number of / difference / between / days /
   weeks / months / years, case-insensitive, whole words.
3. No match: answer with the concise prompt, no thinking. Match: answer with the compute prompt
   `research/overnight/prompts/auto_v1n.txt` as the system prompt, with the model's thinking enabled and capped at about 2500
   tokens; if the reply is empty (the reasoning hit the cap and no answer line was produced), make one more call with the same prompt
   and thinking off. The prompt's key rules: compute durations, date differences, totals and counts from the memories, listing the
   numbers used; words like "a month ago" inside a memory are relative to the date the memory was said, not to today; if memories
   disagree, use the later one; say "I don't know" only when no memory addresses the topic (a related but different fact does not
   count). It ends with an `Answer:` line that the client parses.
4. IDK cascade: if the answer is "I don't know", ask once more with the compute prompt and use that answer.

**As a command.** The protocol above ships in the crate as `ChatMemory::answer(question, &AnswerOptions)` (`src/answer.rs`),
`neuralgraph-memory answer "<question>" [--today YYYY-MM-DD] [--route auto|concise|compute] [--json]`, the MCP tool
`memory_answer` and `POST /api/answer`. It uses the configured context defaults, the harness `COMPUTE_RE`, the concise prompt
(160 tokens, no thinking) and the compute prompt (thinking on, 2500 tokens, non-thinking re-ask when the reply is empty), and
cascades a concise "I don't know" to the compute prompt. A question that already went to the compute prompt is not cascaded.
Run the model without an empty-`<think>` prefill for the thinking call: the backend omits the prefill and `reasoning_effort`
for that call only (`LlmClient::generate_with` with `GenerateOpts { think: true, .. }`); every other call keeps
`LLM_NO_THINK_PREFILL` / `LLM_REASONING_EFFORT`. The result reports `routed`, `cascaded`, the model `calls` and `seconds`.

To reproduce these numbers, and to compare them with published figures on the same footing, follow [`research/overnight/README.md`](../research/overnight/README.md).

Measured against the previous shipped protocol (concise + IDK cascade on the 3600-character quotes block), paired on identical
databases, each cell `previous -> routed` (w:l = questions that flipped to correct : to wrong, McNemar exact p):

| set | strict (Gemma) | strict_v2 | lenient non-IDK | official LME judge | IDK | questions routed | mean s/answer (p50) |
|---|---|---|---|---|---:|---:|---|
| LongMemEval Oracle 50, clean ingest | 62 -> 68 (6:3) | 56 -> 78 (11:0, p .001) | 72 -> 80 (5:1) | 58 -> 74 (8:0, p .008) | 15 -> 10 | 28/50 | 3.8 -> 4.3 (3.8) |
| LongMemEval Oracle 50, first ingest | 50 -> 68 (9:0, p .004) | 52 -> 72 (10:0, p .002) | 64 -> 74 (6:1) | 48 -> 64 (8:0, p .008) | 14 -> 9 | 28/50 | 5.0 -> 5.0 (3.4) |
| LoCoMo conversation 0, 66 questions | 50.0 -> 57.6 (9:4) | 56.1 -> 68.2 (13:5, p .10) | 72.7 -> 81.8 (8:2) | n/a | 9 -> 7 | 4/66 | 3.8 -> 2.1 (1.7) |
| LoCoMo conversation 1, 38 questions | 57.9 -> 57.9 (4:4) | 63.2 -> 68.4 (4:2) | 76.3 -> 78.9 (3:2) | n/a | 5 -> 3 | 0/38 | 2.9 -> 1.9 (1.8) |

The clean LongMemEval ingest was the selection set for the earlier configuration, so its numbers are optimistic, and every row is a
single run. The LoCoMo rows are not significant individually (one question is 1.5 points on conversation 0 and 2.6 on conversation 1). Mean
latency stays near the concise reader's because only the routed questions (more than half of LongMemEval, a handful on LoCoMo)
think; the p50 is the concise path. The official judge is the LongMemEval prompts run with Gemma-4-e4b, not GPT-4o, and it rejects a
bare "I don't know" on abstention questions, so about 8 points of its range are largely uncredited. Held-out check: on a second 50-question sample never used for choosing prompts or routing (seed 2, deterministic ingest, harder draw: evidence@15 82.6) the routed protocol against the original plain-concise pipeline scored strict 50 -> 60 (10:5), strict_v2 50 -> 72 (12:1, p .003), lenient non-IDK 48 -> 66 (12:3, p .035), official 44 -> 62 (13:4, p .049).

**Note: thinking on every question.** The compute prompt with thinking on every question gained on LongMemEval (clean ingest
`strict_v2` 76 and official 70 against the previous protocol's 56 and 58, at 7-8 seconds per answer, twice the concise path's
latency), but it regressed LoCoMo conversation 1 (strict 57.9 -> 50.0, 2:5; `strict_v2` 63.2 -> 57.9). A hand
audit of that conversation's 13 flipped questions found 4 real fixes and 3 real regressions (a dropped detail, a wrong date, a
false "I don't know" after a truncated reasoning pass): thinking costs on short-phrase golds, while the richer context itself was
neutral to positive. Routing keeps the LongMemEval gain on the questions that need computation and leaves the rest on the concise
path. Without routing, the measured gain is on LongMemEval only, at double the latency, with a LoCoMo regression on conversation 1. The per-question audit is
`research/overnight/judgments/locomo_c1.md`.

**Cheaper alternative: the IDK cascade alone, no thinking, about 4 seconds per answer.** (1) answer concisely from the context block;
(2) if the answer is "I don't know", ask once more with the same compute prompt and use that answer. Measured on LongMemEval Oracle
(50 questions, `context_quotes = 1`, 3600 characters): "I don't know" answers 24 -> 14; strict 42 -> 50 (Gemma-4-e4b) and
44 -> 54 (Qwen3.6-35B-A3B). Used alone as the first-pass prompt without thinking, the compute style lost 8-12 strict points on
LoCoMo (more refusals), which is why the cascade runs it only after an "I don't know". Roughly half of the unlocked refusals become
correct answers and half become confident wrong ones, so the cascade is not a free gain on abstention-heavy workloads. With the 6000-character
context block the cascade alone scored `strict_v2` 68 and official 62 on the clean ingest (against 78 and 74 routed and 76 and 70 with thinking on every
question; these are separate runs), and letting only the cascade call think scored 70 and 64 at 5.4 seconds per answer.

**Reader size.** A bigger dense local model did not help without thinking: Qwen3.8-27B with the concise prompt scored `strict_v2`
58 against 58 for the 9B, at about 32 seconds per answer (10x). With thinking, 27B scored `strict_v2` 70 and lenient non-IDK 86 against 68
and 74 for the 9B, at 57 seconds per answer. On a 20-question probe with identical memories, Claude Sonnet as the reader (concise prompt)
scored `strict_v2` 75 against 55 for the 9B (4:0), and lenient non-IDK 95 against 75: the remaining errors in this pipeline are
mostly the reader's (arithmetic and update reasoning), not the memory's. Twenty questions is a probe, not a result.

### Latency: what was measured and what to do

**Where the time goes.** Retrieval and context building take milliseconds. The concise call takes about 2 seconds. A routed
question runs a thinking call of about 6-8 seconds, because the dense 9B decodes 400-600 reasoning tokens at about 60-70 tokens per
second. Routing sends 28 of 50 LongMemEval questions to that path and 0-4 of 66 LoCoMo questions (4 on conversation 0, 0 on
conversations 1 and 2), so the mean stays near 4-5 seconds on LongMemEval and near 2 seconds on LoCoMo. Cutting latency means
cutting reasoning tokens or decode time, not retrieval.

Knobs tried (rows `R4-*`, `R4b-*`, `R4c-*`, `R4d-*`, `R4e-*`, `R4f-*`, `LAT-*` in
[`research/overnight/LEDGER.md`](../research/overnight/LEDGER.md); LongMemEval-50 unless noted, one run each, official = the
LongMemEval prompts run with Gemma-4-e4b):

| knob | effect on latency | effect on accuracy | verdict |
|---|---|---|---|
| Tight routing regex (13 questions routed instead of 28) | 4.33 -> 3.75 s (-13%) | official 74 -> 68 (0:3); strict_v2 -2 | rejected |
| Two-stage thinking budget, 900 then 2500 | 4.33 -> 4.95 and 4.95 -> 4.61 s (two runs): 20-26% of routed calls hit 900 and rerun (1.26 calls per question) | flat (+4, +2) | no saving |
| Concise `max_tokens` 160 -> 80 | none | identical answers (concise replies are already short) | no effect |
| Reasoning instruction capped at 120 words | 4.33 -> 4.22 s (-3%) | official 74 -> 68 (0:3); strict_v2 -2 | rejected |
| Gemma-4-e4b as the reader for all calls | slower on LongMemEval: 6.9 s mean, p95 24 s (it thinks long) | -4 (LongMemEval), -4.5 (LoCoMo conversation 0) strict; lenient -6 and -7.6 (Qwen-35B judge; the official judge was not run) | rejected |
| Speculative decoding (MTP draft) on the MLX 9B in LM Studio | none: no drafting on this build, 70.9 -> 69.0 tokens/s | n/a | no effect |
| Qwen3.6-35B-A3B as the thinking model | 1.8x tokens/s (106 vs 59) but 2.1x the reasoning tokens: wall time about equal (33.9 vs 29.2 s on 6 prompts) | strict +12 on the clean ingest (6:0), +4 on the first (2:0); mean 4.3 -> 12.9 s | accuracy mode, not interactive (next section) |

**Recommendation.** Do not trade accuracy for latency inside a single call: every knob above that shortened the thinking path lost
official-judge points, and the ones that kept accuracy saved nothing. Instead, serve the concise answer immediately (about
2 seconds) and, for questions the router flags, replace it with the routed thinking answer when that completes (about 6-8 seconds):
answer, then upgrade. The client already has both calls; the only change is to show the first result before the second returns. For
batch or offline use, where nobody waits on the first answer, run the routed protocol directly.

**Do not add a verifier pass.** A second call in which the 9B checks the answer against the memories and converts "unsupported"
answers to "I don't know" destroyed accuracy (strict -18 and -32 on two LongMemEval runs, -4.5 on LoCoMo conversation 0; rows
`R4-verify` and `R4-vidk`), because a small model flags many correct answers as unsupported.

**Caveat on the cheaper no-thinking option.** The concise + IDK-cascade protocol on the 3600-character quotes context (the
morning configuration) was +8 strict on the tuning sample but neutral to negative on two held-out LongMemEval draws (strict −4 on
both, official judge −8 on one). It does not hold on held-out draws as a standalone change; use the routed protocol with the
current defaults (6000-character budget, episodic turns) if accuracy matters.

**Use it from the product.** `memory_answer` implements this protocol end to end (CLI `neuralgraph-memory answer`, MCP tool
`memory_answer`, `POST /api/answer`); on the 50 clean-ingest LongMemEval questions the product path matches the research harness
(strict 74 vs 68, official judge 74 vs 74, 3.5 s mean per answer including retrieval). Clients that render memories themselves can keep
using `memory_context` and apply the steps above.

### Accuracy mode (batch or offline)

For offline evaluation or batch answering where latency does not matter, use a larger reasoning model for the routed (count /
duration / date-difference) questions only and keep the 9B for everything else. Measured with `qwen/qwen3.6-35b-a3b` (a mixture
model, 3B active parameters) as the thinking model on LongMemEval-50, against the routed protocol with the 9B thinking:
strict (Gemma) 68 → 80 on the clean ingest (6:0, p .031) and 68 → 72 on the first ingest (2:0); strict_v2 78 → 82 and 72 → 76;
official LongMemEval judge 74 → 76 and 64 → 68; pooled strict 8 wins : 0 losses on those two. On the held-out seed-2 sample it scored strict 66, strict_v2 70, lenient non-IDK 72, official 66: +16 / +20 / +24 / +22 over the original pipeline (p .004 to .077) and +6 / -2 / +6 / +4 over the routed 9B (5:2, 3:4, 5:2, 4:2; within noise), so expect a few points, not a guaranteed gain. Cost: a routed answer takes about 22 s (mean
over the set 13 s vs 4.3 s), because the larger model reasons about twice as long even though it decodes 1.8× faster. A
1200-token thinking cap truncates most of its reasoning and gives mixed results, so do not cap it. On two held-out draws (seeds 2
and 3) its gain over the routed 9B protocol shrank to strict +6 / 0, official +4 / −2 — within noise — so treat it as an option to
try on your own data, not a default recommendation.

## Tests

```bash
cargo test
```

All tests use temporary SQLite files and a deterministic fake model (`FakeLlmClient`, hashed bag-of-words
embeddings); they cover idempotent ingestion, per-chat ordering with cross-chat parallelism, backoff and
dead-lettering, restart recovery of expired leases, lease-fenced commits, reconciliation decisions and the
"older observation never wins" rule, grounding rejections, embedding outages, retrieval channels and filters,
superseded handling, the token ledger and grade, and the MCP protocol over stdio and HTTP. Each of the 106 tests
of the former Python suite has a Rust counterpart of the same name (`tests/store.rs`, `extraction.rs`,
`retrieval.rs`, `worker.rs`, `mcp.rs`, `review_fixes.rs`).

`tests/golden.rs` holds the port's parity evidence: `tests/golden/generate.py` (removed with the rest of the
Python; recoverable with `git show eba2f370:crates/neuralgraph-memory/tests/golden/generate.py`) ran the Python implementation
with a pinned clock and counter-based ids and recorded its outputs, and the Rust crate must reproduce them
exactly — text and date helpers, every extraction prompt byte for byte, a full fake-model ingest of the sample
chats down to every database row, extraction plans for 17 scenarios, retrieval scores and channels, an MCP
session and the REST API. `tests/golden/ingest_user_named.sqlite` is a database written by the Python version;
the retrieval, MCP and REST checks open, search and write it.

## Deployment

* **Container (cloud):** `docker build -f deploy/chat_memory/Dockerfile -t neuralgraph-memory .` or
  `docker compose -f deploy/chat_memory/docker-compose.yml up` (memory server + Ollama, volumes for both).
  Set `NEURALGRAPH_MCP_TOKEN` / `NEURALGRAPH_API_TOKEN`, terminate TLS in front, and point Claude at
  `https://your-host/mcp`.
* **Edge (laptop / mini-PC):** `deploy/chat_memory/neuralgraph-memory.service` runs `serve` as a systemd
  user service next to a local Ollama; Claude Desktop/Code talk to it over stdio (`mcp`) or to the local
  HTTP endpoint. Everything is one binary plus one SQLite file; no other services.
* Read caches are keyed on SQLite's `data_version` as well as the in-process write counter, so a reader in a
  second process (for example `search` on the CLI while `serve` is running) never serves stale indexes.

## Known limits

* Speaker identity is by display name; two different people called "Alex" in different chats would share
  memories unless the caller passes a stable id as `speaker` and maps it via `speaker_names`.
* Extraction quality is prompt-bound to a 7B model; the reconcile decision (UPDATE vs CONTRADICT vs ADD) is
  the weakest step, and it is biased towards ADD, which keeps the store safe but leaves near-duplicates for
  maintenance to merge. Thresholds are exposed for tuning against a labelled sample of your own chats.
* Run one *worker* per database (`serve` or `mcp`); read-only CLI commands can run alongside it.

## Port from Python

The memory server was rewritten from Python (`NeuralGraph.chat_memory`) to Rust. What was kept identical, on
purpose:

* **Storage.** Schema, SQL statements, JSON columns (sorted keys, non-ASCII kept), float32 little-endian
  embedding blobs and the FTS5 tables are unchanged, so an existing database opens and keeps working, and a
  database written by the Rust server can be read by the Python one.
* **Behaviour.** Prompts (byte for byte), extraction, reconciliation, retrieval scoring and fusion, worker
  queue semantics, defaults (`batch_size` 2, `concurrency` 2, graph channel off, `context_k` 15, assistant
  turns extracted, the recall-first prompt), the CLI, environment variables, HTTP routes, JSON shapes and the MCP
  tool schemas.
* **Python semantics the outputs depend on** are reproduced explicitly (`src/pycompat.rs`): code-point string
  lengths, `round()`, float repr, `json.dumps` formats, `difflib` ratios, BM25 from `rank_bm25`.

Differences that remain:

* Vector similarities are float32 dot products; the order of exactly tied candidates is fixed (index order)
  where numpy left it unspecified.
* The worker's tasks run in parallel on several threads, so with `concurrency > 1` the interleaving of
  different chats (and therefore tie order among equally ranked relations) can vary between runs, as it could
  under a different asyncio schedule.
* Two latent Python issues are not reproduced: concurrent extraction passes shared one "superseded in this
  plan" table (now per pass), and `wait_idle` re-checked an already-set idle event in a loop (now it polls).
* Error messages for malformed MCP tool arguments are phrased like the Python exceptions for the common cases
  (unexpected/missing arguments, bad integers and floats); exotic type errors may be worded differently.

The Python package is gone from the repository (last present at `eba2f370`). The research harnesses that
measure this pipeline (`chat-memory-locomo`, `chat-memory-longmemeval` in `crates/neuralgraph-research`) drive
this crate as a library.
