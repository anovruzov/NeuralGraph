# Mycelic end-to-end performance probe (ENGINEER-B)

Run: 2026-10-09 05:35-06:08 UTC, this cloud container (4 vCPU Intel Xeon @ 2.10 GHz, 15.7 GB RAM, no swap, Python 3.13.16, SQLite 3.45.1,
`ulimit -n` 20000, 26 GB free disk). Code under test: `f96f263` (branch `claude/mycelic-implementation-vr034p`) without modification; every `file:line` below is a line of `git show f96f263:<file>`.
**Code pinning.** Other engineers began editing production files in `/home/user/ng-impl` at 05:51 UTC (new `0005_hypergraph.sql`, `inquiry/routing.py`, edits to `inquiry/service.py`, `discovery/engine.py`, `org.py`, `holder/embedded.py`, ...). The main-table runs (N=50/200/1000/2000, the three profiles) all started between 05:45 and 05:55 and had imported their modules before the first edit to any module they use, so they ran f96f263 logic (the N=1000 ingest profile started 05:55, before the 05:56-06:00 edits to `embedded.py`, `evidence/service.py`, `org.py`). The isolated N=1000 rerun and the `coord_pinned_*` reruns were made from a `git archive f96f263` copy, so they are unaffected by those edits. The first coordinator-only runs (`coord_n*.json`) started after `0005_hypergraph.sql` appeared, so their `coord.db` carries that extra migration (unused by the measured functions).
Every number below was produced by the scripts listed here; "measured" means it comes from a run, "extrapolated" means arithmetic on measured points
(the formula is given). Nothing was run on the MacBook bridge.

## What was measured, and how

The real stack, in one Python process per run (so RSS is clean): `build_runtime()` (CoordDB `coord.db`, `SqliteTransport`, `DefaultModelRouter` with the
deterministic `FakeProvider`, `EmbeddedHolders`), one **embedded holder per user** (`EvidenceStore` on a NeuralGraph store, `HolderService` transport consumer,
`IngestRuntime`), records fed through the `local_export` connector -> `IngestPipeline` -> `EvidenceStore`, and the real `LoopEngine` + `DiscoveryWorker`
(2 job loops + scheduler + core-inbound consumer) driving one goal. Only the model provider is the shipped fake.

* **Organization.** root (executive) -> departments of 100 users -> teams of 10 users (N=1000: 10 departments, 100 teams). One user, one membership
  (`team_lead` for each team's first, else `employee`), one embedded holder per user. Each team's holders register 2 of the 12 top-level taxonomy domains
  (rotating), so every domain is held by about 1/6 of the holders.
* **Records.** 5 per user (3 in the user's first domain, 2 in the second), ~290 characters each, one chunk each, dated yesterday (fresh). All holders of a
  domain say the same thing (the fake evaluator treats differing figures as contradictions; `--disagree` produces that variant, not used in the tables).
* **Embeddings.** Default everywhere is the deterministic hash embedder, no model, no network: the runtime embedder is `HashEmbeddings(dim=256)`
  (`hash-bow-256`, `mycelic/models/openai_provider.py:236`, from `NeuralGraph.chat_memory.llm.fake_embedding`); a bare `EvidenceStore` uses the equivalent
  `HashEmbedder` (`mycelic/evidence/service.py:261`). `MYCELIC_EMBED_PROVIDER=hash` is the default (`mycelic/config.py`).
* **Discovery "full pass".** One goal owned by the root unit (scope = whole org), `max_concurrent_questions=3` (bench setting), budget large enough not to bind.
  Round 0 = the loop as activated and left to run until the job queue is empty; each later round = the scheduled check coming due (`enqueue_tick`), until a
  tick asks nothing new. All runs ended after round 1 (0 new questions) with `jobs: done` only and no job errors.
* **Isolation.** Each run is pinned to one core (`taskset -c`); up to three runs (cores 0-2) were in flight at once, so the main table is not from a quiet
  machine. `results/n1000_isolated.json` is an N=1000 rerun from a clean f96f263 export with only one other pinned process (a coordinator-only probe) running; the comparison and the run-to-run spread are in the last section.

Scripts (all under `research/mycelic_e2e/perf/`):

| file | what |
|---|---|
| `perf_bench.py` | the full per-N run: tenant -> open N holders -> ingest -> discovery; `--profile ingest|discovery` adds cProfile to that phase |
| `bench_coordinator_scale.py` | coordinator-only routing costs (tenant + N holders registered, none opened), so N=10,000 fits in memory |
| `summarize.py` | prints the tables and the cProfile top-15 below from `results/*.json` |
| `results/n{50,200,1000,2000}.json`, `results/n1000_isolated.json` | raw output of the full runs (`n1000_isolated` from a clean f96f263 export, one other pinned probe running) |
| `results/coord_n{500,1000,2000,5000,10000}.json`, `results/coord_pinned_n{2000,5000,10000}.json` | raw output of the coordinator-only runs (the `pinned` ones from a clean f96f263 export) |
| `results/prof_ingest_n300.json`, `prof_ingest_n1000.json`, `prof_discovery_n1000.json` | raw cProfile runs (cProfile slows the run, use for shares not for seconds) |

Reproduce: `python research/mycelic_e2e/perf/perf_bench.py --n 1000 --out research/mycelic_e2e/perf/results/n1000.json`.

## Findings (answers to the four questions)

### 1. Tenant, users, memberships, holders; open/close; disk; RSS; open-store limit

* **Creating the tenant is cheap and linear.** 0.7-0.9 ms per user for user + membership + holder rows (one transaction each): 0.14 s at N=50, 0.77 s at N=1000,
  1.9 s at N=2000, and, coordinator-only, **4.0-4.2 s at N=5000 and 8.4-14.4 s at N=10,000 (measured twice; the 14.4 s run shared the machine with an N=1000 run)**. `coord.db` is 26.5 MB at 10,000 users.
* **Opening a holder** (create `evidence.db`, apply migrations, start the transport consumer, ingest runtime, first heartbeat): **~28-32 ms each, flat in N**
  (N=1000: 29.4 s for all; N=2000: 55.9 s). Closing one: 6-8 ms. Reopening an existing file: 5-7 ms (one N=1000 sample shows 16.8 ms; noisy).
* **No cache or limit on open stores.** `EmbeddedHolders` keeps every embedded holder's `EvidenceStore`, `HolderService` and `IngestRuntime` in plain dicts
  (`mycelic/holder/embedded.py:55-57`), `start()` -> `reconcile()` (`:74`) -> `ensure()` (`:148`) opens all of them, and nothing evicts (no LRU, no max-open: grep finds none). So memory,
  file descriptors and background tasks all grow linearly with N.
* **RSS: 1.156-1.216 MB per open holder** (N=1000: 57 -> 1215 MB; N=2000: 58 -> 2370 MB; the per-holder figure is constant to three digits). Ingesting
  5 records per holder adds about 0.8 MB each: whole-run peak RSS **2.0 GB at N=1000 and 3.98 GB at N=2000** (~2.0 MB per holder).
* **File descriptors: exactly 3 per open holder** (`evidence.db`, `-wal`, `-shm`): 3010 at N=1000. The limit here is 20,000.
* **Disk per holder:** an empty holder is **1.2 MB on disk** (`evidence.db` 4 KB + `-wal` 1.17 MB + `-shm` 33 KB; the WAL is not checkpointed). After 5 records:
  3.1 MB with WAL, **0.756 MB main file after a WAL checkpoint** (sample of 20 holders, constant across N).
* **Idle cost of N open holders** (nothing happening, 8 s sample): 0.008 / 0.042 / 0.144 / 0.283 of one core at N=50 / 200 / 1000 / 2000. Each holder owns a
  transport consumer that polls every 0.25 s, a heartbeat loop (20 s) and an ingest tick (5 s); every coordinator commit also wakes all N consumers.
* **Shutdown is quadratic:** `Runtime.stop()` took 0.5 / 2.0 / 22 / 92 s at N=50 / 200 / 1000 / 2000. Not profiled (suspects: `CoordDB.remove_waker` is
  a `list.remove` per consumer, `mycelic/db/coord.py:83`, and each holder's final `store.stats()` + heartbeat in `EmbeddedHolders.stop`, `holder/embedded.py:117-130`).

### 2. Ingestion throughput (connector -> pipeline -> EvidenceStore, hash embeddings)

Per run: connect `local_export`, discover + include its source, `IngestRuntime.drain()` (sync, process queue, flush batch, publish), for each holder in turn; the
other N-1 holders' consumers and loops run in the same event loop.

| N | records | wall s | aggregate rec/s | ms per record | per-holder (5 rec) mean / p95 ms |
|---|---|---|---|---|---|
| 50 | 250 | 2.3 | 107.9 | 9.3 | 46 / 66 |
| 200 | 1,000 | 10.4 | 96.3 | 10.4 | 52 / 69 |
| 1000 | 5,000 | 78.0 | 64.1 | 15.6 | 78 / 109 |
| 2000 | 10,000 | 274.1 | 36.5 | 27.4 | 137 / 273 |

(The standalone single-holder `research/ingest_bench` figure on this class of machine is 136-169 rec/s for the same pipeline, so the N=50 value is in line.)
Per-record cost **rises with the number of open holders** even though each holder's own work is identical: 9 ms -> 27 ms. Aggregate throughput is bounded by
one core, because holders share one Python process and one event loop; each holder in isolation would run at ~100-110 rec/s. Model calls during ingest:
`classify_domains` only, 0 / 40 / 160 / 340 (about 0.17 per holder, ambiguous records going to the fake model); there are no embedding model calls.
Messages published to the transport: one `ingest_result` batch per holder plus a heartbeat per holder per minute.

### 3. One discovery goal over N holders

Measured over the whole pass (round 0 + the closing empty round); 12 domains exist from N=200 up, 6 at N=50:

| N | wall s | questions | routes | model calls | mean tick ms | mean `question.route` ms | `domains_for_goal` ms/tick |
|---|---|---|---|---|---|---|---|
| 50 | 2.7 | 6 | 60 | 89 | 13 | 24 | 4.2 |
| 200 | 5.2 | 12 | 120 | 178 | 42 | 69 | 31 |
| 1000 | 22.0 | 12 | 120 | 178 | 197 | 1107 | 181 |
| 2000 | 63.2 | 12 | 120 | 178 | 640 | 3330 | 620 |

* **Model calls (fake) do not grow with N** once the domains are saturated: 178 = `answer_from_evidence` 120 (12 questions x 10 holders), `draft_question` 12,
  `evaluate_responses` 12, `synthesize_discovery` 12, `record_outcome` 12, `identify_gap` 10. With a real model the discovery bill is therefore flat in N.
* **Questions routed:** one gap question per uncovered domain (12), three per tick (`max_concurrent_questions`), 14 ticks in total.
* **Routing does NOT fan out to every holder, but it scans all of them.** Delivery is capped at 10 holders per question (`budget.holders`, default 10,
  `mycelic/inquiry/service.py:369-370`: `holders = holders[:max_holders]`); every question got exactly 10 routes, all answered. But `candidate_holders`
  (`inquiry/service.py:340`, called from `route` at `:351`) runs `Authorizer.can_route` for **all** N holders first, `domains_for_goal` (`discovery/engine.py:252`) does the same for all holders **on every tick**,
  and `question_audience` (`inquiry/service.py:408`) builds a `Principal` (4 SQL reads) for **every active user on every route**. The list is `ORDER BY name`
  (`org.py:418`) and cut at 10, so from reading the code (not measured) the same lexicographically-first holders answer every question: at N=1000 about 160 holders
  match a domain and 10 are ever asked. For the end-to-end benchmark this bounds how much of the ingested evidence the loop can reach.
* **The routing cost is superlinear in N.** Per route: 24 / 69 / 1107 / 3330 ms (x16 for x5 N, then x3 for x2 N). Coordinator-only, measured at
  larger N (see table): `question_audience` 0.25 s at N=1000, 0.58-0.86 s at 2000, 5.6-6.8 s at 5000, **21.3-24.5 s at 10,000** (per route); `candidate_holders` 0.20 / 0.39-0.44 / 2.1 / **8.5-9.3 s**;
  `domains_for_goal` 0.22 / 0.39 / 2.1-2.3 / **7.6-9.1 s per tick** (ranges are the two independent runs). The full system is slower than the coordinator-only probe for `question_audience` (929 ms vs 253 ms at N=1000, 2813 vs 582 ms at N=2000, i.e. 3.7x and 4.8x) and
  1.2-1.6x for `candidate_holders` and `domains_for_goal`; the likely cause is the heartbeat `stats` JSON that holder rows carry in the full system and that is re-decoded for every user and holder (295k `json.loads` in the profile), not verified separately.
* **Transport catch-up** before the first tick: the core-inbound consumer works through the holders' heartbeats and `ingest_result`s at ~1.1 ms per message:
  0.1 / 0.27 / 3.1 / 15.6 s at N=50 / 200 / 1000 / 2000 (14,000 messages at N=2000).

### 4. Hotspots (cProfile top-15 tables are at the end; `file:line` as of `f96f263`)

Ingestion (profile N=300, per-record work; N=1000 adds the per-holder background load):

1. `mycelic/ingest/domains.py:714 classify` is 64% of `process_item` (16.4 s of 25.5 s): `rule_candidates` (`:495`) re-tokenizes and re-stems the whole taxonomy's keyword lists for
   every record (`_tokens` `:375`, `_kw` `:387`: 303 tokenizations per record, `NeuralGraph/chat_memory/textutil.py:43 stem` and `:60 fold` 813k and 456k calls), and the embedding stage
   (`:566`, `score` `:573`) computes pure-Python 256-d cosines against each centroid (`_cos` `:397`, 6.8M generator iterations; `CentroidIndex.get` `:428`).
   Both are cacheable per taxonomy version / vectorizable with numpy.
2. SQLite statements: 236 `execute` calls per record (354,727 / 1,500), 19% of profiled time (`mycelic/evidence/store.py:179 run_in_tx` and the pipeline's per-record transactions).
3. At N=1000 (profile 196 s): `SqliteTransport._consume` (`mycelic/transport/sqlite_transport.py:198`) is **28%** (54.6 s; 1.3M loop passes, i.e. ~13 per holder per second, with a
   `call_later` timer each via `_wait_for_wake` `:89`) and the holder heartbeat loop (`holder/service.py:137`, `store.stats()` + two coordinator commits per beat) is **15%** (29 s);
   the actual per-record work (`process_item`) is 40%. `CoordDB.tx` (`mycelic/db/coord.py:57`) sets every consumer's event after every commit (`:76`).

Discovery (profile N=1000, 30 s profiled):

1. `question_audience` (`inquiry/service.py:408`) = 11.9 s of the 15.3 s spent in `QuestionService.route`: it calls `Authorizer.principal_for_user` (`authz.py:120`, 12,012 calls,
   8.8 s cum) for every user; each builds memberships, grants and holders from SQL (`org.py:302`, `:360`, `:433`).
   The cache key includes `db.revision` (`:416`), so any commit by anyone invalidates it.
2. `Authorizer.can_route` (`authz.py:364`, 26,580 calls, 4.8 s cum): `self.org.descendants(scope)` (`authz.py:384`, `org.py:197`) is a `LIKE path%` query returning the whole
   scope's units, **once per holder**, which makes the scan O(N x units) = quadratic (measured: 8.0-8.2 s for 10,000 holders, 60 ms for 500). `tenant_taxonomy` (`authz.py:206`) is rebuilt
   on every `db.revision` change (`default_taxonomy` `ingest/domains.py:237`, 4,172 calls, 1.3 s cum).
3. `OrgService.list_holders` (`org.py:418`) JSON-decodes four columns of every holder row on each call (295k `json.loads` in the profile).
4. `LoopEngine.domains_for_goal` (`discovery/engine.py:252`) re-runs 1 and 2 over all holders on every tick.

## Extrapolation to 10,000 users (labelled)

| quantity at N=10,000 | value | basis |
|---|---|---|
| create tenant + 10,000 users + memberships + holders | **8.4-14.4 s** | **measured** twice (coordinator-only) |
| coord.db size | 26.5 MB | measured |
| `can_route` over all holders / `candidate_holders` / `domains_for_goal` / `question_audience` | 8.0-8.2 s / 8.5-9.3 s / 7.6-9.1 s per tick / 21.3-24.5 s per route | **measured** (coordinator-only; a lower bound for the full system, which was 1.2-1.6x slower for the first three and 3.7-4.8x slower for `question_audience` at N=1000-2000) |
| open 10,000 holders | ~290 s | extrapolated, 29 ms x N (flat per-holder cost, measured to N=2000) |
| RSS after opening / peak with 5 records each | 11.6 GB / ~20 GB | extrapolated, 1.16 MB and 2.0 MB per holder (constant, measured to 2000); machine has 15.7 GB and no swap: **does not fit** |
| open file descriptors | 30,000 | extrapolated, 3 per holder; `ulimit -n` is 20,000: **fails near N = 6,600** |
| disk | ~31 GB with WAL (7.6 GB main files after checkpoint) | extrapolated, 3.13 MB and 0.756 MB per holder; 26 GB free here |
| ingest 50,000 records | ~62-101 min | extrapolated, ms/record = 27.4 + s x (N - 2000) with s = 0.0065 (200 -> 1000 slope) to 0.0118 (1000 -> 2000 slope), i.e. 74-122 ms per record; not reachable anyway |
| one discovery pass | **>= 8.1 min** (12 x (9.3 + 21.3) s routes + 14 x 7.6 s ticks, coordinator-only numbers); ~22 min if the full-system factors seen at N=1000-2000 hold (audience x4, others x1.3) | lower bound is arithmetic on measured values; the upper figure is extrapolated; plus ~1 min of transport catch-up for ~50,000 messages |
| idle CPU of the open holders | ~1.4 cores | extrapolated, 0.14 core per 1000 holders |
| `Runtime.stop()` | ~38 min if the quadratic trend holds | extrapolated (0.5 / 2 / 22 / 92 s measured) |

**10,000 users in one process is not possible on this machine** (RAM, file descriptors, disk), and would not be fast enough if it were.

## Recommendation: the largest scenario for ingest + one full discovery pass in <= 25 minutes here

| N (5 records/user) | open + ingest + catch-up + discovery | status |
|---|---|---|
| 1,000 | 29 + 78 + 3 + 22 = 132 s (2.2 min) | measured |
| 2,000 | 56 + 274 + 16 + 63 = 409 s (6.8 min) | **measured**, largest completed full run (plus 92 s shutdown) |
| 3,000 | ~12-14 min | extrapolated: open 87 s; ingest 15,000 rec at 34-39 ms = 8.5-10 min; discovery 116-142 s; catch-up ~23 s |
| 4,000 | ~19-24 min | extrapolated, on the edge; ingest 20,000 rec at 40-51 ms = 13.5-17 min; RSS ~8 GB |
| 5,000 | ~27-36 min | extrapolated, over budget; ingest 25,000 rec at 47-63 ms = 20-26 min; RSS ~10 GB |

**Recommended: N = 2,000 users as the verified size (6.8 min); N = 3,000 as the ceiling to plan for (~12-14 min, extrapolated, RSS ~6 GB, ~9,000 fds, leaves headroom for the model-free scenario overhead).** N = 4,000 is possible only if the ingest slope stays at its lower estimate. Do not plan on more in one process. Extrapolation formulas: open = 29 ms x N; ingest = N x 5 x (27.4 + s x (N - 2000)) ms with s = 0.0065 (200->1000 slope) to 0.0118 (1000->2000 slope); discovery = 63 s x (N/2000)^k with k = 1.5 to 2 (the 1000->2000 points give k = 1.5); catch-up = 15.6 s x N/2000.
To go beyond: run holders in separate processes (`mycelic/holder/process.py` exists), which also removes the per-commit wake-up of N consumers, or feed only a subset of
users with records (an idle holder costs ~29 ms to open and 1.2 MB RAM, an ingested one 2 MB).

Fixes that would help most, in order of effect on this benchmark:

1. **`question_audience` / `principal_for_user`** (`inquiry/service.py:408`, `authz.py:120`): compute the audience once per (scope, visibility) with set queries over `memberships`
   instead of one `Principal` (4 queries) per user, and key the cache on the membership/hierarchy tables, not `db.revision`. It is 78% of `QuestionService.route` time in the N=1000 profile.
2. **`can_route` scan** (`authz.py:364`, `org.py:197`, `org.py:418`, `engine.py:252`): hoist `org.descendants(scope)` out of the per-holder loop (compute the closure once per call), cache `list_holders`
   decoded rows, do the domain match in SQL, and stop re-scanning all holders on each tick. Removes the quadratic term (8 s -> well under 1 s at 10,000).
3. **Transport consumers** (`transport/sqlite_transport.py:198`, `db/coord.py:76`): one shared poller/dispatcher for all embedded holders (or per-subject wake-ups) instead of N tasks each woken by
   every commit, and spread holder heartbeats (`holder/service.py:137`) rather than N independent 20 s loops. Reclaims ~40% of ingest CPU at N=1000 and the idle load.
4. **Domain classification** (`ingest/domains.py:375-428, 495, 566-573`): precompute stemmed keyword tuples per taxonomy version and use numpy for centroid cosines; ~60% of per-record time at small N.
5. **Holder lifecycle** (`holder/embedded.py`): open stores lazily with an LRU (RSS 1.2 MB, 3 fds, a polling consumer per holder), and fix the quadratic `stop()`.

## Caveats

* Synthetic content (5 near-identical short notes per user, 12 domains), fake model, hash embeddings: the retrieval and discovery quality numbers of a real run will differ; only cost and
  scaling are claimed. With `--disagree` (differing figures) the loop adds contradiction follow-ups and ~2x the model calls; that variant was run only at small N during development and is not reported.
* cProfile inflates wall time 2-3x; use the profile tables for shares. The profiled N=300 ingestion run covered only the ingest phase.
* `max_concurrent_questions=3` and the 12-domain taxonomy set the number of questions (12) and ticks (14); a different taxonomy or goal changes those, not the per-question costs above.
* Timing runs shared the machine with two other pinned runs of mine; the isolated N=1000 rerun is compared below.
* Not measured: holders in separate processes, NATS transport, a real model or embedding endpoint, the HTTP API, multiple tenants, multi-process ingestion scaling.


## Run-to-run spread (same code, same N)

Full N=1000 run, first (`n1000.json`, three runs sharing the machine) vs the pinned-code rerun (`n1000_isolated.json`, one other probe running):

| measure | n1000 | n1000_isolated |
|---|---|---|
| open all holders, s | 29.4 | 24.0 |
| idle CPU of 1000 open holders | 0.144 | 0.172 |
| ingest wall s / aggregate rec/s | 78.0 / 64.1 | 87.6 / 57.1 |
| core-inbound catch-up s | 3.06 | 4.66 |
| discovery wall s (questions, model calls) | 22.0 (12, 178) | 26.8 (12, 178) |
| mean tick ms / mean `question.route` ms | 197 / 1107 | 275 / 1269 |
| `question_audience` uncached ms | 929 | 950 |
| peak RSS MB / RSS after open MB | 2026 / 1215 | 2025 / 1216 |
| shutdown s | 22.0 | 27.1 |

Counts (questions, routes, model calls) and memory are identical between the runs; times differ by up to 25-40% (the rerun was slower on ingest and discovery, faster on opening), so treat any single
timing here as +-25%, and the 2x-per-doubling trends (which are larger than that) as real. Coordinator-only pairs (`coord_nN.json` vs `coord_pinned_nN.json`) agree within 15% on `can_route`, `candidate_holders`,
`domains_for_goal` and differ by 10-40% on `question_audience` (21.3 vs 24.5 s at 10,000).

## Raw tables (generated by `summarize.py` from `results/*.json`)

| measure | N=50 | N=200 | N=1000 | N=2000 |
|---|---|---|---|---|
| tenant+units+users+memberships+holders, total s | 0.139 | 0.225 | 0.772 | 1.896 |
| per user (user+membership+holder rows), ms | 1.306 | 0.805 | 0.69 | 0.862 |
| coord.db bytes after tenant | 790528 | 1232896 | 3284992 | 5881856 |
| open all N holders, s | 1.579 | 6.44 | 29.414 | 55.936 |
| open one holder mean / p95 ms | 31.518 / 52.0 | 32.171 / 41.0 | 29.383 / 44.0 | 27.944 / 41.0 |
| first10 / last10 open mean ms | 54.533 / 25.492 | 32.95 / 28.13 | 29.426 / 19.169 | 31.883 / 23.682 |
| close one holder mean ms (n=20) | 7.526 | 5.677 | 7.052 | 8.154 |
| reopen one holder mean ms (n=20) | 5.83 | 5.482 | 16.768 | 6.965 |
| RSS before -> after open, MB | 55.3 -> 116.1 | 55.8 -> 290.3 | 57.4 -> 1215.3 | 58.3 -> 2370.4 |
| RSS per open holder, MB | 1.216 | 1.173 | 1.158 | 1.156 |
| fds per open holder | 3.0 | 3.0 | 3.0 | 3.0 |
| disk per empty holder, bytes (db / -wal / -shm) | 1202856 (4096 / 1165992 / 32768) | 1202856 (4096 / 1165992 / 32768) | 1202856 (4096 / 1165992 / 32768) | 1202856 (4096 / 1165992 / 32768) |
| idle CPU of N open holders (fraction of 1 core) | 0.008 | 0.042 | 0.144 | 0.283 |
| no-op reconcile() pass, s | 0.0025 | 0.0127 | 0.1072 | 0.1532 |
| ingest: records | 250 | 1000 | 5000 | 10000 |
| ingest: wall s | 2.317 | 10.382 | 77.957 | 274.123 |
| ingest: aggregate records/s | 107.88 | 96.32 | 64.14 | 36.48 |
| ingest: per holder (5 rec) mean ms | 46.025 | 51.649 | 77.668 | 136.74 |
| ingest: per holder p95 ms | 66.0 | 69.0 | 109.0 | 273.0 |
| ingest: per-holder records/s (mean) | 108.64 | 96.81 | 64.38 | 36.57 |
| ingest: model calls | 0 | 40 | 160 | 340 |
| ingest: disk/holder incl WAL, bytes | 2955501 | 3093976 | 3130859 | 3135418 |
| ingest: db-only/holder after checkpoint (sample), bytes | 755712 | 755302 | 755507 | 756122 |
| transport msgs after ingest (heartbeat+ingest_result) | 116 | 400 | 3419 | 14000 |
| core-inbound catch-up s (cpu s) | 0.103 (0.094) | 0.268 (0.232) | 3.059 (2.523) | 15.586 (13.378) |
| discovery: rounds (wall s each) | 1.686, 1.014 | 4.095, 1.057 | 20.216, 1.722 | 61.124, 2.058 |
| discovery: total wall s / cpu s | 2.702 / 2.613 | 5.157 / 4.565 | 21.97 / 20.833 | 63.241 / 60.933 |
| discovery: finished | True | True | True | True |
| discovery: questions (by kind/depth) | 6 {'gap/d0': 6} | 12 {'gap/d0': 12} | 12 {'gap/d0': 12} | 12 {'gap/d0': 12} |
| discovery: ticks, mean tick ms | 8, 13.016 | 14, 41.784 | 14, 196.965 | 14, 639.861 |
| discovery: routes total (per question) | 60 (10.0) | 120 (10.0) | 120 (10.0) | 120 (10.0) |
| discovery: model calls total | 89 | 178 | 178 | 178 |
| discovery: model calls by task | {'identify_gap': 5, 'draft_question': 6, 'answer_from_evidence': 60, 'evaluate_responses': 6, 'synthesize_discovery': 6, 'record_outcome': 6} | {'identify_gap': 10, 'draft_question': 12, 'answer_from_evidence': 120, 'evaluate_responses': 12, 'synthesize_discovery': 12, 'record_outcome': 12} | {'identify_gap': 10, 'draft_question': 12, 'answer_from_evidence': 120, 'evaluate_responses': 12, 'synthesize_discovery': 12, 'record_outcome': 12} | {'identify_gap': 10, 'draft_question': 12, 'answer_from_evidence': 120, 'evaluate_responses': 12, 'synthesize_discovery': 12, 'record_outcome': 12} |
| question.route job mean ms | 23.559 | 68.569 | 1107.469 | 3329.639 |
|   of which candidate_holders mean ms | 7.123 | 24.096 | 238.602 | 514.95 |
| domains_for_goal (per tick) mean ms | 4.177 | 31.319 | 181.245 | 619.806 |
| loop.tick mean ms | 13.016 | 41.784 | 196.965 | 639.861 |
| question.evaluate / commit mean ms | 18.002 / 4.249 | 20.997 / 15.217 | 21.832 / 5.068 | 23.685 / 8.222 |
| micro: list_holders ms | 1.253 | 5.303 | 63.253 | 970.868 |
| micro: can_route over all holders ms (us/holder) | 3.656 (73.2) | 16.81 (84.1) | 186.449 (186.5) | 423.988 (212.0) |
| micro: question_audience uncached ms | 8.898 | 41.487 | 928.575 | 2813.31 |
| peak RSS MB (whole run) | 164.6 | 462.3 | 2025.6 | 3975.7 |
| shutdown s | 0.469 | 1.98 | 22.014 | 91.683 |
| job errors | [] | [] | [] | [] |

### cProfile top 15, phase `discovery`, N=1000 (file prof_discovery_n1000.json; profiled total 29.809 s)


by own time

| where | function | calls | own s | cum s |
|---|---|---|---|---|
| ~:0 | <method 'execute' of 'sqlite3.Connection' objects> | 514861 | 7.939 | 7.939 |
| ~:0 | <method 'fetchall' of 'sqlite3.Cursor' objects> | 221104 | 5.654 | 5.654 |
| /usr/lib/python3.13/json/decoder.py:351 | raw_decode | 295655 | 1.475 | 1.475 |
| mycelic/db/coord.py:57 | tx | 11362 | 1.166 | 3.037 |
| mycelic/db/coord.py:144 | row_to_dict | 93411 | 0.721 | 3.673 |
| ~:0 | <method 'fetchone' of 'sqlite3.Cursor' objects> | 256159 | 0.654 | 0.654 |
| /usr/lib/python3.13/asyncio/locks.py:182 | set | 5693602 | 0.587 | 0.789 |
| /usr/lib/python3.13/json/decoder.py:340 | decode | 295655 | 0.584 | 2.455 |
| mycelic/authz.py:364 | can_route | 26580 | 0.494 | 4.802 |
| /usr/lib/python3.13/asyncio/events.py:113 | __init__ | 60306 | 0.404 | 0.486 |
| /usr/lib/python3.13/json/__init__.py:304 | loads | 295655 | 0.332 | 2.878 |
| ~:0 | <method 'get' of 'dict' objects> | 1208019 | 0.329 | 0.329 |
| mycelic/ingest/domains.py:237 | default_taxonomy | 4172 | 0.326 | 1.267 |
| mycelic/authz.py:153 | _scopes | 72060 | 0.298 | 1.262 |
| /usr/lib/python3.13/json/encoder.py:207 | iterencode | 14400 | 0.284 | 0.284 |

by cumulative time

| where | function | calls | own s | cum s |
|---|---|---|---|---|
| /usr/lib/python3.13/asyncio/base_events.py:1981 | _run_once | 57 | 0.093 | 30.315 |
| /usr/lib/python3.13/asyncio/events.py:87 | _run | 64056 | 0.053 | 30.008 |
| ~:0 | <method 'run' of '_contextvars.Context' objects> | 64056 | 0.068 | 29.955 |
| mycelic/discovery/worker.py:228 | _loop | 65 | 0.001 | 20.415 |
| mycelic/discovery/worker.py:146 | run_once | 127 | 0.002 | 20.41 |
| mycelic/discovery/worker.py:171 | _process | 62 | 0.002 | 20.13 |
| research/mycelic_e2e/perf/perf_bench.py:328 | handle | 62 | 0.001 | 20.082 |
| mycelic/discovery/engine.py:1249 | handle | 62 | 0.056 | 20.081 |
| mycelic/discovery/engine.py:536 | route_question | 12 | 0.0 | 15.351 |
| research/mycelic_e2e/perf/perf_bench.py:348 | route | 12 | 0.001 | 15.347 |
| mycelic/inquiry/service.py:351 | route | 12 | 0.01 | 15.346 |
| mycelic/inquiry/service.py:433 | publish_pending_routes | 12 | 0.002 | 12.066 |
| mycelic/inquiry/service.py:408 | question_audience | 12 | 0.111 | 11.941 |
| mycelic/db/coord.py:97 | all | 212624 | 0.265 | 10.875 |
| mycelic/authz.py:120 | principal_for_user | 12012 | 0.158 | 8.829 |

### cProfile top 15, phase `ingest`, N=1000 (file prof_ingest_n1000.json; profiled total 196.164 s)


by own time

| where | function | calls | own s | cum s |
|---|---|---|---|---|
| ~:0 | <method 'execute' of 'sqlite3.Connection' objects> | 3573441 | 31.957 | 31.957 |
| ~:0 | <built-in method builtins.sum> | 1630020 | 12.872 | 22.657 |
| NeuralGraph/chat_memory/textutil.py:43 | stem | 2715610 | 5.627 | 9.669 |
| /usr/lib/python3.13/asyncio/events.py:129 | __lt__ | 16278592 | 4.939 | 6.716 |
| mycelic/db/coord.py:57 | tx | 42320 | 4.423 | 15.752 |
| NeuralGraph/chat_memory/textutil.py:63 | <genexpr> | 15579688 | 4.162 | 5.802 |
| mycelic/transport/sqlite_transport.py:198 | _consume | 1306429 | 3.909 | 54.57 |
| ~:0 | <method 'join' of 'str' objects> | 1846066 | 3.38 | 9.308 |
| /usr/lib/python3.13/asyncio/events.py:113 | __init__ | 1355837 | 3.308 | 5.431 |
| /usr/lib/python3.13/asyncio/locks.py:200 | wait | 2612838 | 3.306 | 7.126 |
| /usr/lib/python3.13/asyncio/locks.py:182 | set | 21163429 | 3.305 | 8.826 |
| ~:0 | <method 'fetchone' of 'sqlite3.Cursor' objects> | 1708331 | 3.237 | 3.237 |
| mycelic/transport/sqlite_transport.py:89 | _wait_for_wake | 2612838 | 2.951 | 25.933 |
| ~:0 | <built-in method _heapq.heappop> | 1129499 | 2.947 | 8.216 |
| /usr/lib/python3.13/asyncio/base_events.py:806 | call_at | 1355837 | 2.59 | 10.41 |

by cumulative time

| where | function | calls | own s | cum s |
|---|---|---|---|---|
| /usr/lib/python3.13/asyncio/base_events.py:1981 | _run_once | 6129 | 2.508 | 204.837 |
| /usr/lib/python3.13/asyncio/events.py:87 | _run | 1420704 | 1.53 | 192.414 |
| ~:0 | <method 'run' of '_contextvars.Context' objects> | 1420704 | 1.735 | 190.883 |
| mycelic/ingest/runtime.py:163 | tick | 43461 | 0.258 | 101.496 |
| research/mycelic_e2e/perf/perf_bench.py:478 | run | 3035 | 0.005 | 101.152 |
| research/mycelic_e2e/perf/perf_bench.py:255 | phase_ingest | 3035 | 0.103 | 101.146 |
| mycelic/ingest/runtime.py:199 | drain | 3036 | 0.03 | 98.583 |
| mycelic/ingest/pipeline.py:510 | process_available | 1003 | 0.141 | 87.172 |
| mycelic/ingest/pipeline.py:563 | process_item | 5000 | 0.322 | 79.517 |
| mycelic/transport/sqlite_transport.py:198 | _consume | 1306429 | 3.909 | 54.57 |
| mycelic/ingest/domains.py:714 | classify | 5000 | 0.129 | 54.142 |
| ~:0 | <method 'execute' of 'sqlite3.Connection' objects> | 3573441 | 31.957 | 31.957 |
| mycelic/holder/service.py:137 | _heartbeat_loop | 10020 | 0.046 | 29.214 |
| mycelic/ingest/domains.py:566 | _embedding_stage | 5000 | 0.258 | 28.853 |
| mycelic/holder/service.py:142 | _safe_heartbeat | 10000 | 0.042 | 28.839 |

### cProfile top 15, phase `ingest`, N=300 (file prof_ingest_n300.json; profiled total 35.583 s)


by own time

| where | function | calls | own s | cum s |
|---|---|---|---|---|
| ~:0 | <method 'execute' of 'sqlite3.Connection' objects> | 354727 | 6.871 | 6.871 |
| ~:0 | <built-in method builtins.sum> | 484140 | 3.833 | 6.764 |
| NeuralGraph/chat_memory/textutil.py:43 | stem | 813670 | 1.677 | 2.864 |
| NeuralGraph/chat_memory/textutil.py:63 | <genexpr> | 4670558 | 1.224 | 1.713 |
| ~:0 | <method 'join' of 'str' objects> | 539108 | 0.974 | 2.724 |
| mycelic/ingest/domains.py:400 | <genexpr> | 6823350 | 0.706 | 0.706 |
| ~:0 | <built-in method builtins.len> | 4679345 | 0.645 | 0.645 |
| mycelic/ingest/domains.py:428 | get | 26550 | 0.553 | 3.218 |
| mycelic/ingest/domains.py:495 | rule_candidates | 1500 | 0.552 | 7.05 |
| mycelic/ingest/domains.py:375 | _tokens | 454500 | 0.535 | 5.92 |
| ~:0 | <built-in method unicodedata.combining> | 4295072 | 0.5 | 0.5 |
| NeuralGraph/chat_memory/textutil.py:60 | fold | 456000 | 0.499 | 3.342 |
| ~:0 | <method 'endswith' of 'str' objects> | 2930266 | 0.487 | 0.487 |
| ~:0 | <built-in method zlib.compress> | 3000 | 0.433 | 0.433 |
| NeuralGraph/chat_memory/llm.py:201 | fake_embedding | 6930 | 0.428 | 1.663 |

by cumulative time

| where | function | calls | own s | cum s |
|---|---|---|---|---|
| /usr/lib/python3.13/asyncio/base_events.py:1981 | _run_once | 1835 | 0.177 | 37.469 |
| /usr/lib/python3.13/asyncio/events.py:87 | _run | 101712 | 0.099 | 36.491 |
| ~:0 | <method 'run' of '_contextvars.Context' objects> | 101712 | 0.123 | 36.391 |
| research/mycelic_e2e/perf/perf_bench.py:478 | run | 906 | 0.001 | 31.305 |
| research/mycelic_e2e/perf/perf_bench.py:255 | phase_ingest | 906 | 0.026 | 31.303 |
| mycelic/ingest/runtime.py:163 | tick | 3332 | 0.027 | 30.687 |
| mycelic/ingest/runtime.py:199 | drain | 906 | 0.008 | 30.562 |
| mycelic/ingest/pipeline.py:510 | process_available | 301 | 0.047 | 26.943 |
| mycelic/ingest/pipeline.py:563 | process_item | 1500 | 0.09 | 25.482 |
| mycelic/ingest/domains.py:714 | classify | 1500 | 0.037 | 16.419 |
| mycelic/ingest/domains.py:566 | _embedding_stage | 1500 | 0.073 | 9.021 |
| mycelic/ingest/domains.py:573 | score | 26550 | 0.113 | 8.756 |
| mycelic/evidence/store.py:179 | run_in_tx | 7204 | 0.07 | 7.581 |
| mycelic/ingest/domains.py:495 | rule_candidates | 1500 | 0.552 | 7.05 |
| ~:0 | <method 'execute' of 'sqlite3.Connection' objects> | 354727 | 6.871 | 6.871 |
