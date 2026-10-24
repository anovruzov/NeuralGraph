# LAYER B AUDIT — Mycelic product workers (spec §02/§04/§05/§09)

Auditor D10 · read-only · 2026-10-24
Repo: /home/user/pnurmanm/myceliclabs · HEAD ed65f5a (branch `research/production-gaps-20261024`) · origin/main 64027cb (read-only remote) · 8 local-only commits (`git log origin/main..HEAD`).
Spec: /home/user/ng-recovery/research/emergence_two_track_20261024/swarm/OMEGA_HIVEGENESIS.spec §02, §04, §05, §09.
Method: grep + read only. No cargo invoked. Every claim carries file:line against HEAD unless a commit is named. Anything not read is marked **unverified**.

## 15-line summary

1. **There is no worker/scheduler/actor abstraction in any crate.** The only long-lived tasks are three `tokio::spawn`s in `MycelicService::start` (service.rs:439–443): transport keeper, outbox publisher, one consumer loop. No worker pool, no `JoinSet`, no actor mailbox, no supervisor.
2. The product's "workers" per the architecture doc are **customer-owned `mycelic-agent` OS processes** (docs/MYCELIC_ARCHITECTURE.md:8–9, 12–17) that read a JSONL file, POST over HTTP synchronously and poll `/query` (mycelic-agent.rs:111–198). They never touch the broker, do no inference, and have no inbox.
3. **Of the 15 W_i fields, 0 are PRESENT as a worker type; 7 have a partial analog (id, scope, roles, local_graph, outbox, rate_budget, checkpoint) and 8 are MISSING** (parent-as-spawn, gen, inbox, capabilities, belief_version, time_budget, memory_budget, lease). Table in §1.
4. **Transport (NATS JetStream) is genuinely implemented and tested against a real broker** (transport.rs:296–690; tests/jetstream.rs 9 tests; CI installs nats-server). ack/retry/idempotency, dedup (`Nats-Msg-Id`, 2 h window), replay, crash recovery: PRESENT. Dead-letter: PARTIAL (term + DB `mark_failed` + audit row, no DLQ subject). Ordering key: PARTIAL (global order via `max_ack_pending=1`, no per-key parallel ordering). Bounded fanout: PARTIAL (`max_ack_pending`, `publish_batch`, `max_batch`). Per-owner broker access control and opt-in federation: MISSING (SECURITY.md:124–125; one user/password for the service, transport.rs:534–540; `num_replicas: 1`, transport.rs:390).
5. **Concurrency on the consumer side is 1 by construction**: `consume_batch` default 1 ⇒ `max_ack_pending` 1 (config.rs:106; transport.rs:431–432, 456) and one durable consumer on `mycelic.>` (transport.rs:367). The spec's ω(E) ≥ 10 cannot be met inside the service without changing this.
6. **Writes are serialized by a single SQLite writer connection behind a mutex** (store.rs:7–8, 1116, 1221–1223). Aggregation runs inside that transaction (service.rs:1324–1493). Any N-worker design must keep inference outside this lock.
7. **No NeuralGraph integration.** No crate depends on it (all five Cargo.toml). The word appears only in provenance comments (coordination.rs:3, textutil.rs:2, docs). "tesseract" appears only in research-note paths. Confirmed by the repo's own trace (research/mycelic/ingestion_tesseract_20261024/TESSERACT_TRACE.md:11–13).
8. **Production retrieval is BM25 only** (retrieval.rs:1–15, 57–62; service.rs:1555–1565). Embeddings are "deliberately not part of this slice" (retrieval.rs:15). There are **zero model-inference call sites in the whole workspace**; the research crate has no HTTP client (mycelic-research/Cargo.toml) and scores pre-recorded model answers from JSON (score_live.rs:20–21).
9. **Message algebra: 2 of 12 spec types have a real analog** (EVIDENCE≈`memory.observed`, RETRACT=`memory.retracted`), 2 are weak analogs (HYPOTHESIS/REVISE≈`memory.derived`+`supersedes`), 8 are MISSING (QUERY-as-message, COUNTEREVIDENCE, ROUTE, REQUEST_MORE, VERIFY, SPAWN_TASK, HEARTBEAT, STOP). Event kinds are string literals matched in `apply_event` (service.rs:1352–1466), not an enum.
10. **ancestor_roots are carried only on derived memories** as `metadata.roots` + a `parents` edge list (aggregation.rs:68, 120–129, 367–381); the envelope `EventRecord` (models.rs:222–237) has no such field. Causal cycles are rejected **for rules** (`check_rule_cycles` service.rs:1925, `rule_chain` aggregation.rs:132–141), **not for evidence messages**. Consolidation confidence is plain noisy-OR (aggregation.rs:27, 74, 363), which §05 names as unacceptable.
11. **Of the 8 local-only commits, none builds Layer B.** 2b423cc (process fixture), 3daed66/8e17b46 (copy collapse), 7aee160/9294581 (Kuhn matching for slot independence, **opt-in per rule and not enabled by any shipped rules.json**), a10e2cb (fmt), a09283a (research notes), ed65f5a (latency harness). They improve evidence-independence accounting, which matters once many workers echo each other, but add no scheduling, no inbox, no inference.
12. The `derived_freshness` work (f50867c) is **not among the 8**: it sits alone on the other local branch `research/architecture-recovery-20261024`. It is relevant only if Layer B runs more than one service writer.
13. **§09: no interval instrumentation exists anywhere.** The service has three Prometheus duration histograms (metrics.rs:29–31) with no worker label; the latency harness (ed65f5a) records per-sample durations and explicitly says its agents "are not separate processes" (mycelic-latency.rs:44). No (worker, start, end) record, no ω(E), and no model-call record since there are no model calls.
14. **Smallest insertion points**: `MycelicService::handle_delivery` (service.rs:776–827, `t0` already at 777) for consumer-stage intervals; `api::blocking` (api.rs:322–330) for every HTTP request stage; `mycelic-latency.rs::ms` (82–84) + the `wall` origin (177) for the harness. None of them will show overlap > 1 on the consumer until item 5 is changed.
15. **Verdict**: instantiating 10 genuinely independent product workers with overlapping inference requires, in order, (a) a model-call site that does not exist, (b) N consumers or N processes, (c) interval tracing, (d) a typed message enum with `ancestor_roots`. All are **unbuilt**; **publishing anything** (including the 8 existing patches) is **blocked by read-only access** to `PNurmanM/myceliclabs` (7aee160's own message records this).

---

## 0. Repository state and scope

| Item | Evidence |
|---|---|
| HEAD | `ed65f5a9f2703a214acc152aedb215a952b63314`, branch `research/production-gaps-20261024` |
| origin/main | `64027cb` = local `main`; merge-base of HEAD and origin/main is 64027cb, so all 8 commits are unpublished |
| Other local branch | `research/architecture-recovery-20261024` = origin/main + `f50867c` ("Judge an arriving derivation against what this database already knows"; adds tests/derived_freshness.rs, 199 lines; service.rs +60) |
| Workspace crates | `crates/mycelic` (service), `crates/mycelic-sdk` (sync client + LocalMemory + `mycelic-agent` bin), `crates/mycelic-harness` (process/compose drivers, smoke, demos, `mycelic-latency`), `crates/mycelic-research` (simulator E1–E12, docs) — Cargo.toml:3 |
| Async runtime | Only `mycelic` depends on tokio and async-nats (crates/mycelic/Cargo.toml:20, 41). `mycelic-sdk` and `mycelic-harness` have **no tokio** (their Cargo.toml dependency lists); the SDK is synchronous by design (mycelic-sdk/src/lib.rs:19). `mycelic-research` uses rayon for data-parallel simulation only (grep `par_iter`, e.g. experiments.rs:146, 159). |
| Deployment shape | one `mycelic serve` + one `nats-server` (harness lib.rs:343–344; k8s `replicas: 1  # SQLite: exactly one writer. Never scale this` mycelic-statefulset.yaml:7; DEPLOYMENT.md:131–137) |

## 1. Worker / scheduler / actor abstraction — W_i field map

**What exists.** `MycelicService` (service.rs:318–335) holds `tasks: Mutex<Vec<JoinHandle<()>>>` and spawns exactly three background tasks when `start(background=true)` runs (service.rs:436–444):

```
service.rs:439  tasks.push(tokio::spawn(async move { s.transport_keeper().await }));
service.rs:441  tasks.push(tokio::spawn(async move { s.publisher_loop().await }));
service.rs:443  tasks.push(tokio::spawn(async move { s.consumer_loop().await }));
```

`consumer_loop` (service.rs:744–774) fetches `consume_batch` (default 1, config.rs:106) deliveries and processes them **sequentially** (`for d in deliveries { self.handle_delivery(d).await; }`, 768–770). `handle_delivery` (776–867) runs `apply_event` on `spawn_blocking` (815), acks only after the transaction committed (824), and on failure sleeps *before* nak so "nothing overtakes it" (861–863). HTTP handlers run on axum's per-connection tasks and hop to `spawn_blocking` via `api::blocking` (api.rs:322–330); that is the only request-side concurrency, and every write then funnels into the single SQLite writer (store.rs:1116 `writer: Mutex<Connection>`; 1221–1223 `BEGIN IMMEDIATE`).

Outside the service, the "agents" are `mycelic-agent` processes (mycelic-sdk/src/bin/mycelic-agent.rs): resume unacknowledged notes (96–109), iterate a JSONL file with `thread::sleep` delays (111–157), then poll `/query` every `poll_seconds` (159–198). The architecture doc states the intent: "Agents are separate processes owned by the customer; they never talk to the broker" (docs/MYCELIC_ARCHITECTURE.md:8–9).

The coordination package that *had* a router, capability registry and coordinator was **deliberately not ported**: "The rest of that package (router, capability registry, failure injector, mock adapters, trace logger) was research scaffolding with no caller in the service and was not ported" (coordination.rs:3–6).

**Grep results for an actor/worker type**: `tokio::spawn|JoinSet|thread::spawn|spawn_blocking` across crates/ returns only the three service tasks, `spawn_blocking` bridges (api.rs, mcp.rs, service.rs, main.rs:341), the harness's child-wait thread (live/mod.rs:380), the http_load example's load threads (examples/http_load.rs:110, 224) and rayon in research. No type named Worker/Actor/Scheduler/Mailbox exists (grep `struct .*Worker|struct .*Actor|struct .*Scheduler` → none; **unverified** only in the sense that I grepped names, not semantics).

### W_i := ⟨…⟩ mapping

| W_i field | Status | Closest existing thing (file:line) | Gap |
|---|---|---|---|
| `id` | PARTIAL | `Agent.agent_id` (models.rs:66–80) for SDK agents; `Settings.instance_id` default `"mycelic-main"` (config.rs:30, 90) for the service; NATS client name (transport.rs:489) | Identity exists for principals and for the one service instance; no scheduled-worker identity |
| `parent` | MISSING (as spawn parent) / PARTIAL (as org parent) | `hierarchy::parent_path` (hierarchy.rs:104), `AgentPath` (155–229) | Organizational ancestry only; nothing spawns workers |
| `gen` | MISSING | Memory `version_of` / `previous_versions` (lineage.rs:190–199) are *memory* versions | No generation counter on any executor |
| `scope` | PARTIAL | `Memory.scope`, `Agent.path`; `Principal::can_query_scope` (auth.rs:151), `can_read` (136) | Authorization scope, not a task scope |
| `roles` | PARTIAL | permission scopes `DEFAULT_AGENT_SCOPES`/`ALL_SCOPES` (models.rs:15–16); `Principal::has` (auth.rs:132) | Permissions, not functional roles (b00…b11 style) |
| `local_graph` | PARTIAL | agent side: `LocalMemory` SQLite note table, `search` is `LIKE` (mycelic-sdk/src/lib.rs:484–600, 557); server side: `lineage_edges` table walked by `lineage::reconstruct` (lineage.rs:73–129) | Local store is a flat table, not a graph; the graph is server-side and global |
| `inbox` | MISSING | the single durable pull consumer, filter `mycelic.>` (transport.rs:367, 449–459) is the *deployment's* inbox | Agents have no subject/consumer; they poll `/query` (mycelic-agent.rs:163–197) |
| `outbox` | PARTIAL | service outbox: `events.status='pending'` drained by `publisher_loop` (service.rs:654–742); agent side: unshared `LocalMemory` notes re-sent on start (mycelic-agent.rs:96–109) | Present for the service; agents' is a resend loop, not a durable queue to a peer |
| `capabilities` | MISSING | none; registry explicitly not ported (coordination.rs:5) | — |
| `belief_version` | MISSING | `store.revision()` (service.rs:1554) is a DB revision used to cache the BM25 index; `Memory.superseded_by`/`version_of` are per-memory | No per-worker belief version |
| `rate_budget` | PARTIAL | inbound token-bucket `RateLimiter` per API key (auth.rs:213–240; config.rs:62–63; api.rs:169) | A brake on callers, not a budget a worker spends |
| `time_budget` | MISSING | HTTP client timeouts (sdk lib.rs:221), `ack_wait` 30 s (config.rs:102) | No per-task deadline |
| `memory_budget` | MISSING | `max_body_bytes`, `max_text_chars`, `max_event_bytes` (config.rs:64–68) are message size caps | Not a worker memory budget |
| `lease` | MISSING | JetStream `ack_wait` (transport.rs:453) is a per-message lease on the one consumer | No worker lease / fencing |
| `checkpoint` | PARTIAL | `last_applied_seq` + `replay_target_seq` meta written in the apply txn (service.rs:870–877, 487–505); durable consumer ack floor (transport.rs:680–685) | Service-level, single consumer; not per worker |

**Answer to (1):** there is no "independently scheduled persistent worker" in the product. The nearest things are (a) one tokio consumer task with in-flight 1, and (b) N synchronous `mycelic-agent` OS processes that do HTTP only.

## 2. Transport — NATS JetStream matrix

Implementation: `crates/mycelic/src/transport.rs` (698 lines). `Transport` trait (77–102), `Delivery` trait with `ack/nak/term` (48–58), in-process log for tests (108–294), `JetStreamTransport` (296–690), selection by `MYCELIC_NATS_URL` (692–698). Config defaults: stream `MYCELIC`, consumer `mycelic-main`, dup window 7200 s, ack_wait 30 s, max_deliver 8, publish_batch 100, consume_batch 1 (config.rs:97–106).

| Requirement (§04) | Status | Evidence | Note |
|---|---|---|---|
| ack / retry | PRESENT | explicit ack policy transport.rs:452; ack after commit service.rs:824; nak with backoff 861–863; publisher retry with backoff on `Transient` 721–739 | at-least-once |
| idempotency | PRESENT | `apply_event` checks `event_status == applied` → `"duplicate"` (service.rs:1324–1333); derived ids deterministic (models.rs:50; arch doc 140–144); in-process transport dedups by msg_id (transport.rs:233–234) | tested: `test_forced_replay_is_idempotent` (tests/jetstream.rs:411) |
| ordering key | PARTIAL | subject `mycelic.<org>.<kind>` (transport.rs:40–45); **global** order by `max_ack_pending = consume_batch` (= 1) (431–432, 456) | Order is achieved by having one in-flight message; there is no per-key ordering that would survive N parallel consumers |
| duplicate suppression | PRESENT | `Nats-Msg-Id` header on every publish (transport.rs:17–18, 581); `duplicate_window` from settings (392); outbox `event_id` is the key (service.rs:691) | beyond the window the idempotent apply covers it (transport.rs:18) |
| dead-letter | PARTIAL | after `max_deliver` attempts: `term()` + `mark_failed` + audit `event.failed` + progress (service.rs:833–858); unpublishable events `mark_failed` + audit `event.unpublishable` (704–719) | Failures are recorded in SQLite, **no DLQ stream/subject**; tested: `test_poison_event_is_terminated_and_replay_completes` (jetstream.rs:431) |
| replay | PRESENT | `reset_consumer(start_seq)` deletes and recreates the durable consumer with `DeliverPolicy::All|ByStartSequence` (transport.rs:429–460, 637–645); `POST /admin/replay` (api.rs:771; service.rs:2002); replay target persisted (service.rs:487–505, 870–877); during replay derived events are not re-appended (1478–1483) | stream is file-backed, unbounded by default; a bounded stream logs an error (transport.rs:380–412) |
| crash recovery | PRESENT | ack after commit (service.rs:824); `recover_if_needed` (505); tests `..._survive_service_restart` (jetstream.rs:240), `..._rebuilt_from_the_stream` (311), `..._restored_backup_...` (340), `..._lost_consumer_...` (385), `..._unfinished_replay_resumes_after_a_crash` (469) | lost NATS volume with intact DB is **not covered** (arch doc:179) |
| bounded outstanding fanout | PARTIAL | `max_ack_pending` (transport.rs:456), `publish_batch` 100 (config.rs:104), `max_batch` events per request (service.rs:1159–1160), rate limiter (api.rs:169) | Bounds exist for the single pipeline; nothing bounds a *fanout to N workers* because there is no fanout |
| per-owner access control | MISSING at the broker / PRESENT at HTTP | one NATS user/password or token for the whole service (transport.rs:534–540); "No per-agent broker credentials, no multi-tenant broker isolation: all organizations share one stream" (SECURITY.md:124–125). HTTP layer: per-agent key, org confinement, visibility rule (auth.rs:1–19, 76–86, 136–158); producer must be registered for its scope at apply (service.rs:1356–1372); HMAC on the wire (937, 779–801) | Owner isolation is enforced by the service, not by NATS accounts |
| explicit opt-in federation | MISSING | `num_replicas: 1` (transport.rs:390); no mirror/source/cross-cluster config (grep `federat|mirror` in src → none); cross-org reads 404 by design (PIPELINE.md:16) | No federation concept at all |

## 3. Storage — NeuralGraph integration and the production retrieval path

**NeuralGraph integration: none.**
- No `neuralgraph*` dependency in any of the five Cargo.toml files (workspace deps Cargo.toml:11–44; crate deps mycelic/Cargo.toml:17–51, sdk:14–24, harness:30–45, research:17–37).
- `grep -rni 'neuralgraph|neural_graph|tesseract'` in crates/ hits only provenance comments: coordination.rs:3 ("copied from NeuralGraph's `research.coordination` at e7ef73ea"), textutil.rs:2, and two test headers naming the research folder (copied_roots.rs:1, ingestion_fixture_process.rs:1). Docs: README.md:21, 83; CONTRIBUTING.md:40; docs/MYCELIC_ARCHITECTURE.md:68–72.
- The repo's own trace agrees: "MycelicLabs main 64027cb: 'tesseract' appears nowhere … Neither Tesseract executes on any production request path in either repo" (research/mycelic/ingestion_tesseract_20261024/TESSERACT_TRACE.md:11–13).

**Graph primitives that do exist (spec: typed nodes/edges/hyperedges, lineage, dedup, retractions):**

| Primitive | Status | Evidence |
|---|---|---|
| typed nodes | PARTIAL | `Memory` with `layer`, `operator`, `kind`, `slot`, `entity` (models.rs:92–126); SQLite `memories` |
| typed edges | PARTIAL | `LineageEdge {child_id, parent_id, contributed_by, parent_layer, created_at}` (models.rs:205–213); one edge type (derivation parent) |
| hyperedges | MISSING | none; `ReconstructionCoalition` (coordination.rs:243–248) is computed, not stored as an edge |
| lineage | PRESENT | `lineage::reconstruct` walks `parents_of` to roots, redacts, checks `reconstructable` (lineage.rs:73–234) |
| dedup | PRESENT | idempotency key on ingest; deterministic derived ids; exact-copy collapse (aggregation.rs:557–590, from 3daed66/8e17b46) |
| retractions | PRESENT | `memory.retracted` → `retire_dependents` + `reevaluate` (service.rs:1419–1439; aggregation.rs:250–300) |
| "text similarity ≠ provenance identity" | VIOLATED by design, acknowledged | copy collapse keys on `(slot, normalised text)` (aggregation.rs:564–567) — hash-equality-as-identity, exactly what §04 warns about; paraphrases undetected (561) |

**Production retrieval = BM25, confirmed.** `POST /query` → `MycelicService::query` (service.rs:1511–1582) → `self.retriever.index(&org, revision, …visible_rows…)` (1555–1557) → `search_index(&index, &text, &|m| principal.can_read(m), &params)` (1565) → `answer = hits.first()` (1568). `retrieval.rs` header: "Retrieval over organizational memory: BM25 keyword ranking scoped to what the caller may see" (1); "Embeddings are deliberately not part of this slice: the deployment must run without any model server" (15); `struct Bm25` with Lucene-style positive IDF (57–105); layer boost (259). Prior audit's claim stands.

**Model inference anywhere?** None. `grep -rni 'ollama|/v1/chat|completions|anthropic|openai|llama|embed'` over crates/*.rs returns only metric-name strings in the research crate (`inference_calls` as a *simulated* count, e.g. evalm.rs:363) and two filenames of pre-recorded answers (`live_answers_haiku.json`, `live_answers_sonnet.json`, score_live.rs:20–21). The research crate has no HTTP client dependency (mycelic-research/Cargo.toml:17–31; `tungstenite` is used only by make_pdf.rs:36–37 to drive a local browser). The spec's "real overlapping model inference requests" therefore has **no call site to overlap**.

## 4. Message types (§05)

Event kinds are **string literals** matched in `apply_event` (service.rs:1352–1466) and documented in docs/MYCELIC_ARCHITECTURE.md:100–107: `memory.observed`, `memory.derived`, `memory.retracted`, `agent.event`, `agent.registered`, `agent.revoked`, `agent.key_rotated`, `rule.upserted`, `rule.deleted`. There is no `enum` for them (models.rs index: no `pub enum` at all).

| Spec M | Status | Evidence |
|---|---|---|
| QUERY | MISSING as message | `/query` is a synchronous HTTP call (api.rs:764; service.rs:1511); never enters the log |
| EVIDENCE | PRESENT-analog | `memory.observed` (service.rs:1353–1385) |
| COUNTEREVIDENCE | MISSING | no polarity/contradiction channel; `MEMORY_KINDS` (models.rs:12) has no negation |
| HYPOTHESIS | weak analog | `memory.derived` is a deterministic rule conclusion with point `confidence` (aggregation.rs:14–18) — no posterior, no calibration |
| ROUTE | MISSING | router not ported (coordination.rs:5); "no routing between holders" (PIPELINE.md:13) |
| REQUEST_MORE | MISSING | "Active inquiry / coordinator / registry / router — Absent in Rust" (PIPELINE.md:18) |
| REVISE | PARTIAL | supersession via `memory.derived` payload `supersedes` (service.rs:1408–1413; aggregation.rs:69) |
| RETRACT | PRESENT | `memory.retracted` (service.rs:1419–1439); `POST /memory/{id}/retract` (api.rs:761) |
| VERIFY | MISSING | `verify` (service.rs:937) is HMAC transport auth, not evidential verification |
| SPAWN_TASK | MISSING | — |
| HEARTBEAT | MISSING as message | passive proxy: `last_seen_at` touched ≤ every 30 s on authenticated requests (service.rs:980–996); gauge `mycelic_active_agents` "seen in the last 15 minutes" (metrics.rs:127) |
| STOP | MISSING as message | internal `CancellationToken` only (service.rs:454–456) |

**Envelope ⟨msg_id, source, destination, claim_id, typed_payload, ancestor_roots, timestamp, expiry, confidence_interval, privacy_scope, cost_estimate⟩ vs `EventRecord` (models.rs:222–237):**
`event_id`=msg_id ✓ · `agent_id`=source (optional) ✓ · `subject`≈destination (org+kind, never a worker) PARTIAL · `kind`+`payload: Value`=typed_payload (untyped JSON) PARTIAL · claim_id → `payload.memory_id` PARTIAL · **ancestor_roots → not on the envelope**; only derived memories carry `metadata.roots` (aggregation.rs:120–129, 367–381, 726–740), `metadata.rule_chain` (132–141) and a `parents` edge list in the `memory.derived` payload (aggregation.rs:68; consumed at service.rs:1393–1407). A raw `memory.observed` carries nothing (its root is itself, aggregation.rs:121–122) · `created_at`=timestamp ✓ · expiry MISSING · confidence_interval → point `confidence` only (models.rs:104) · privacy_scope → `visibility ∈ {team, org}` (models.rs:13, 115) PARTIAL · cost_estimate MISSING.

**Causal-cycle rejection:** present for **rules** (`check_rule_cycles` service.rs:1925; a rule never consumes its own output via `rule_chain`, aggregation.rs:132–141; arch doc:133–134). Absent for **evidence**: there is no ancestry-DAG check on incoming messages. What exists instead is (a) exact-copy collapse (aggregation.rs:557–590) and (b) `LineageAnalyzer` computing `unique_lineage_root_count` / `minimal_failure_domain_cut` from `ClaimEnvelope.lineage_root_ids` (coordination.rs:24, 351–375) — **stored as fragility metadata, not used to gate the conclusion**; gating uses distinct `producer_node_id` (Kuhn matching, coordination.rs:130–203) and `min_agents`/`min_teams` (aggregation.rs:601, 762). Consolidation confidence is **noisy-OR without dependence adjustment** (aggregation.rs:27, 74–80, 363, 698), which §05 explicitly rejects.

## 5. The 8 local-only commits (origin/main..HEAD)

| Commit | Files | What it adds | Needed for Layer B? |
|---|---|---|---|
| 2b423cc | mycelic-harness/tests/ingestion_fixture_process.rs (+178) | Process-level 3-owner fixture: real nats-server + real `mycelic` + SDK; asserts roots, cross-org 404, SIGKILL restart, retraction, rebuild. Several assertions acknowledged "wrong-reason" (TEST_MATRIX.md:10–11) | **No** for scheduling; **useful template** for a multi-process worker test (it is the only test that fails rather than skips without a broker, header:5–6) |
| 3daed66 | aggregation.rs (+27), tests/copied_roots.rs | Byte-identical copies of a note collapse to one source (aggregation.rs:557–590) | Not Layer B. Relevant to §05 integrity once N workers echo each other; paraphrases still counted (561) |
| 8e17b46 | aggregation.rs (+46/−19), copied_roots.rs | Copy attribution ignores client `observed_at`; credits the member whose producer already contributes elsewhere, ties to arrival (aggregation.rs:576–583) | Same as above; closes a client-time bypass |
| a10e2cb | two test files | rustfmt | No |
| a09283a | research/mycelic/ingestion_tesseract_20261024/* (5 files) | PIPELINE.md, TEST_MATRIX.md, RESULTS.md, CHECKPOINT.json, TESSERACT_TRACE.md — the evidence this audit cross-checked | No (documentation); it is the spec's `MycelicLabs: a09283a` reference |
| 7aee160 | coordination.rs (+60), tests/independence_selection.rs | Greedy swap so one agent strongest in two slots does not block `min_agents` | **Superseded by 9294581** (its greedy `independent_picks` was replaced, see diff); not Layer B |
| 9294581 | coordination.rs (+130/−?), aggregation.rs (+11), tests (+149), **and** mycelic-harness/src/bin/mycelic-latency.rs (+323, no Cargo bin entry yet) | Kuhn maximum bipartite matching slots×holders (coordination.rs:130–203); **opt-in per rule via `metadata.independent_slots`** (aggregation.rs:595–602); kept off to preserve golden parity (`withdrawal_cascade` step 38, commit message). No shipped rules.json sets it (grep → only the test, independence_selection.rs:26) | Not Layer B. Default behaviour unchanged. Note the latency bin was committed here by accident and only wired in ed65f5a |
| ed65f5a | mycelic-harness/Cargo.toml (+4: the `[[bin]] mycelic-latency`), mycelic-latency.rs (+29/−24) | Stage-latency harness over a real deployment (see §6) | Closest to §09, but measures durations not intervals; see §6 |

**derived_freshness**: commit **f50867c** is **not** among the 8; it is the single commit on `research/architecture-recovery-20261024` (origin/main + 1). It makes `apply_event` judge an arriving `memory.derived` against local knowledge (parent inactive → admit as lineage but not support; supersedes a newer version → current stands; `at_least_as_new` helper) — service.rs hunks at 1405–1455 and 2112–2134 of that commit; tests/derived_freshness.rs:1–19 states scope. Relevant to Layer B **only** if more than one service writer publishes `memory.derived` (multi-instance); irrelevant to single-writer deployments. Not on HEAD; merging it is a separate decision.

## 6. §09 parallelism — instrumentation audit

**What records time today**

| Place | What | Shape | Worker identity? | Overlap computable? |
|---|---|---|---|---|
| service.rs:777/825 | `aggregation_latency` histogram per applied event | duration | no | no (and in-flight is 1, so trivially ω=1) |
| service.rs:1516/1577–1578 | `retrieval_latency` per `/query` | duration + `latency_ms` in the response body | no | no |
| service.rs:1607/1622 | `lineage_latency` | duration | no | no |
| metrics.rs:17, 29–31, 62–67, 112–118 | three Prometheus histograms, 13 fixed buckets | bucket counts | no | no |
| mycelic-latency.rs (ed65f5a) 165–167, 192–194, 206–208, 218–227, 236–241 | per-sample `ms(t)` for register/ingest/settle/query/lineage/get | durations; `--concurrency` writer threads via `std::thread::scope` (178–201) | no; "they are not separate processes" (44) | no — start instants are discarded in `ms()` (82–84) |
| smoke.rs:106–109, 341 | step timestamps relative to start | one sequence | no | no |
| live/trace.rs:38–74, 134 | demo acts with `t0`/`t1` | sequential acts | no | no |
| examples/http_load.rs | thread load generator, counters | throughput | no | no |

**Model-call tracing:** nothing, because there are no model calls (§3).

**Answer to (6):** there is **no** instrumentation that records worker start/end intervals or overlapping model calls, so ω(E), S_N, occupancy per stage, stragglers and rate-limit stalls (§09) cannot be computed from anything in the tree.

**Smallest insertion points (ranked by leverage):**
1. `MycelicService::handle_delivery` — service.rs:776–827. `t0` exists (777); emit `{instance_id, consumer, seq, kind, t_start, t_end, result}` at 824–827 (and at the term/nak branches 833–863). Covers every consumer-stage interval. **Caveat:** with `max_ack_pending=1` these intervals never overlap within one process; the record will prove serialization, which is itself the honest §09 result for the current build.
2. `api::blocking` — api.rs:322–330. Every HTTP handler's service call passes through it; wrapping it with `Instant::now()` before/after `spawn_blocking` and the matched route (available in the middleware at api.rs:189) gives request-stage intervals that **can** overlap (axum is concurrent) — the one place today where ω(E) > 1 is reachable.
3. `mycelic-latency.rs::ms` (82–84) + `wall` (177): return `(start−wall, end−wall)` instead of a duration and tag with the scoped thread index (178–201); then ω(E) over client-side writers is a 20-line post-processing step. Does not prove anything about the server.
4. For agent-level workers: `mycelic-agent.rs` share loop (139) and watch loop (171) — record intervals per process to a `--state_file` (206–208 already writes a summary).

## 7. Verdict — minimal ranked change set for 10 independent workers with overlapping inference

Ranked by "nothing downstream is meaningful without it". Status: **BLOCKED** = cannot be done from this session because the repo/remote is read-only (and this audit is read-only by rule); **UNBUILT** = no code exists; both apply to every code item, so the column states which one dominates.

| # | Change | Where | Why first | Status |
|---|---|---|---|---|
| 1 | **Add a model-call site.** Spec §02 demands "real overlapping model inference requests"; the workspace has none. Minimal honest placement is agent-side (local-first per spec §00 priors): a blind extraction step over `text` before `share` in `mycelic-agent.rs` (111–157), via a small HTTP client to a local endpoint (the SDK already has `ureq`, mycelic-sdk/Cargo.toml:20). Server-side alternative: an extraction stage in `handle_delivery` **before** `apply_event` (service.rs:803–815), outside the SQLite lock. | mycelic-sdk/src/bin/mycelic-agent.rs; or crates/mycelic/src/service.rs | Without this, "overlapping model calls" is undefined. PIPELINE.md:7–9 already names the missing extractor. | UNBUILT (+ BLOCKED to commit). Also **environment-blocked**: "no local model endpoint in this container" (RESULTS.md:52–53) — **unverified** for the machine that will run Layer B |
| 2 | **N scheduling contexts.** Cheapest real path: run N `mycelic-agent` OS processes (independent schedulers for free; the harness already starts the stack and knows the binaries, live/mod.rs:18, 321). Service-side path: N durable consumers with disjoint `filter_subject` (today `mycelic.>`, transport.rs:367) e.g. per org or per subject hash, each in its own `consumer_loop`; or raise `MYCELIC_CONSUME_BATCH` and process the batch with `JoinSet` — but ordering (transport.rs:431–432) and the single writer (store.rs:1116) mean only the pre-apply stage may run in parallel. | harness (`ProcessDriver::up` lib.rs:277–360) for N agents; transport.rs:367/449–459 + service.rs:744–774 for N consumers | ω(E) ≥ 10 is impossible with in-flight 1 | UNBUILT; N agents is config/driver work only, N consumers is a design change (ordering + idempotency per key) |
| 3 | **Interval tracing** per §6 items 1–3, written as JSONL with `{worker_id, stage, t_start, t_end, model_call: bool}` and an offline ω(E)/S_N reducer. | service.rs:776–867; api.rs:322–330; mycelic-latency.rs:82–84, 177 | Without it, nothing in §09 is measurable; with it, the current build will *correctly* report ω=1 on the consumer | UNBUILT |
| 4 | **Typed message enum + envelope fields**: an `enum EventKind` replacing the string match (service.rs:1352–1466); `ancestor_roots: Vec<String>` and `expiry` on `EventRecord` (models.rs:222–237) populated from `lineage_roots` (aggregation.rs:120–129) for derived and `[memory_id]` for raw; reject a message whose roots include its own claim or whose roots were already counted for the same slot (extend the gate at aggregation.rs:595–602 to use `unique_lineage_root_count` from coordination.rs:351–365 instead of only `producer_node_id`). | models.rs, service.rs, aggregation.rs, coordination.rs | §05 cycle rejection; turns root accounting from metadata into a gate | UNBUILT |
| 5 | **Replace noisy-OR for correlated roots** (aggregation.rs:74–80, 363, 698) with a dependence-adjusted combiner keyed on shared `roots`. | aggregation.rs | §05 names noisy-OR unacceptable | UNBUILT |
| 6 | **Per-owner broker isolation + opt-in federation**: NATS accounts per org in `deploy/mycelic/nats/nats.conf`, per-org credentials in `Settings` (config.rs:33–39), stream-per-org or subject-scoped permissions; federation as explicit `sources`/mirrors. | config.rs, transport.rs:534–545, deploy/ | §04 transport requirements currently MISSING (SECURITY.md:124–125) | UNBUILT |
| 7 | **Publish the 8 existing patches** (and decide on f50867c). | git push to PNurmanM/myceliclabs | They are prerequisites for honest independence counting once workers echo each other | **BLOCKED by read-only access** (7aee160 message: "this session has read-only access to this repository") |
| 8 | Decide `independent_slots` per deployed rule (currently off everywhere; deploy/mycelic/rules.json unset). | deploy/mycelic/rules.json | Owners' decision per 9294581 | config only; BLOCKED to commit |

**Maximum observed product workers (spec §02 asks this to be reported explicitly):** in the tree as it stands, **1** consumer worker in flight (config.rs:106 / transport.rs:456), **1** service instance (k8s `replicas: 1`), and N agent processes only when a demo/harness launches them (the latency harness uses threads, not processes: mycelic-latency.rs:44). No run was executed by this audit, so **observed = unverified**; the structural maximum for overlapping *apply* is 1.

## Unverified items

- Exact current test counts (commit messages claim 93 in `mycelic`, 184 workspace at 64027cb; not run here).
- Whether `nats-server` or a local model endpoint exists on the target machine (RESULTS.md:52–53 says no model endpoint in that container).
- Semantics of the `wait_idle ignores JetStream ack-pending` suspicion (CHECKPOINT.json:25) — code read only.
- Whether `max_ack_pending > 1` with multiple consumers preserves the idempotency guarantees under `BEGIN IMMEDIATE` contention (store.rs:1130 `busy_timeout` 5 s) — not exercised.
- Any behaviour of `research/architecture-recovery-20261024` beyond the single commit f50867c listed by `git log`.
