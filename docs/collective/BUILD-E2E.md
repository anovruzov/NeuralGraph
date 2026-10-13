# E2E-INGEST build note: the EMERGENCE world through NeuralGraph, as built

What was built so the end-to-end run of `E2E-INGEST.md` (with its section "Amended before any build", M1 to M25) can
ingest the EMERGENCE benchmark's world. The brief is the rule; this note says how the build carries it out, where the
pinned code left the build no choice, and what the sandbox runs showed. The brief was committed alone (`e3052c0`),
reviewed twice and amended before any code (`315a087`); this build starts from `6f98472`, the head of the production
branch (`mycelic-production-deployment-r93cez`). Everything here is synthetic and stamped `measurement: false`; no
model ran; no record text or record ref appears in this note, in a log, in a test message or in a report.

Each part of the build has its own section below, written by the branch that built it. Sections are added, never
rewritten; a later fix adds a line under "Fixes" in its section.

## S2 and S2b: `emergence_world.py`, `emergence_pack.py`, `site_graph.py` (branch `wf/e2e-site`)

### The shape

| Piece | Where | What |
|---|---|---|
| The world | `research/e2e_ingest/emergence_world.py` | `build_org`, `build_corpus`, `make_gold` (imported from `research.mycelic`, never edited); section 3.1's counts asserted (`WORLD_TABLE`, `check_world_counts`); section 3.3's record mapping (`record_dict`, `build_records`); one site per org SITE node (`site_ids`, lower-cased node names); master data = every entity at every site; the shipped `run_pipeline` (M16: nothing ingests first) and `detect` at the pipeline's `as_of`, channel X, tie salt `e2e-ingest-v1`; the candidates by (score, `sha256(salt|key)`), the first 60, no window (M21); Gold's labels in the spike's shape (`gold_labels`, `world.candidate_label`); the 20 fixed questions (`fixed_questions`: the first 20 candidates in (`as_of`, key) order, built with the pinned `question_window` and `build_question`); `site_configs` naming the pack by its directory |
| The pack | `research/e2e_ingest/emergence_pack.py`, data at `research/e2e_ingest/pack/emergence/` | section 3.4's files from `corpus.py`'s constants; `write_pack`, `check_pack` (byte identity with a regeneration plus the pinned loader's full check); `python -m research.e2e_ingest.emergence_pack write\|check` |
| The site graph | `research/e2e_ingest/site_graph.py` | `PersistentTesseractRetrieval(TesseractRetrieval)`: the graph file persists; at start the site store's `records` (`forwarded_in = 0`, `mode=ro`) past a `seq` watermark kept in the graph file (table `e2e_sync`) are upserted as MESSAGE nodes keyed on `record_ref` in batches of 500, the watermark moved after each batch's commit, never a delete; `E2ESiteProcess(SiteProcess)` with that retrieval in step 5; `E2EChildProcessEndpoint(wire.ChildProcessEndpoint)` running `python -m research.e2e_ingest.site_graph`; harness readers `graph_node_ids` (M9's `SELECT node_id FROM nodes WHERE session_key = ? AND layer = ?`, `mode=ro`), `graph_watermark`, `node_ids_digest`; `rank(text, as_of)` for criterion 3's fixed queries |
| Tests | `tests/e2e_ingest/test_emergence_world.py`, `test_site_graph.py` | 34 tests (22 and 12); the 200-user world runs for real through the pipeline, detection and every site's graph, twice (once per file); nothing is skipped for the environment; about 4 minutes in the sandbox, the 280 fixed-query rankings of criterion 3's site half being most of it |

No pinned module was changed: nothing under `mycelic/collective/`, `research/routing_spike/`, `research/mycelic/`,
`NeuralGraph/`, `mycelic/*.py`, `lab/`; every one is imported. `tests/routing_spike/helpers.py` is imported by the
site-graph tests for the spike's seed-1 world.

### Where the pinned code fixed the data, not the brief's wording

Three points of section 3.4's table cannot load as written; each is pinned by a test and none lowers a floor.

1. **The canonical entity ids carry the prefix `E-`** (`E-NIMBUS-PAY`), not the bare upper-cased name. The brief's
   `aliases.json` is "every EMERGENCE entity name → its canonical id" and its ids "the upper-cased names". The pinned
   loader folds aliases and targets to lower case (`packs/canonical.py` `fold_phrase`) and refuses an alias whose
   target folds to an alias key (`packs/loader.py` `_aliases`, "alias chain"); the bare upper-cased name is exactly
   that. Run in the sandbox: a pack with bare upper-cased ids and the name aliases is refused with
   `aliases.json: $.entity.arbor: alias chain`; `test_every_entity_name_is_an_alias_of_its_id_and_the_literal_table_is_refused`
   repeats it. The alias table is exactly every entity name, as the brief says; the ids match the alias-only pattern
   (`ALIAS_ONLY_ID_RE`); a text mention of an entity resolves through its alias (scan finds alias-only types only through
   aliases, `canonical.py` `resolve_exact`). The ids appear in every key, question and conclusion of the run.
2. **`codes.json`: `EM-08` (`revenue_miss`) is the non-specific code.** The loader needs at least one specific and one
   non-specific code and the generator a non-specific `generic_code`. The brief's eight codes are kept, one per
   predicate; no record carries a code.
3. **`fixtures/records.jsonl` holds the first 40 records of the world, not 5.** The loader's `MIN_FIXTURES` is 40.
   The fixtures are the world's own synthetic records, which the brief requires as examples; they are the one place
   record text is committed, by the brief's own design.

### Settings the build chose where the brief left them open

- `generator.json` is required by the loader and never used to generate a record: its sites are the 200-user world's
  seven site ids, weekly volume `[1, 1]`, one reporter, every rate 0, one template pair per predicate from the
  predicate's own surface phrases, `master_data` `all` for every site.
- `vocabulary.json` negation: `post: ["not"]`, `pre` and `terminators` empty, window 8 (the corpus appends `not` to a
  polarity −1 record, `corpus.py:223-224`). The judge's reading of such a record is reported, not corrected
  (section 3.4).
- The pipeline's enterprise name is `emergence` (`org_for_sites`); the HQ store sits at `<workdir>/hq/collective.sqlite3`
  as `run_pipeline` puts it.
- The graph's watermark lives in the graph file (table `e2e_sync`, one row per session) so the nodes and the watermark
  share one file; a crash between a batch's commit and its watermark leaves records the next start upserts again (keyed
  on `record_ref`, no node added, no node lost); a crash before a commit leaves the watermark behind the nodes never.
  Node content, embedding, dates and metadata are the spike's, field for field, so a ranking over the persistent graph
  is the spike's ranking (Tesseract breaks ties by node id, `tesseract.py` `_charge_order`).
- The sync writes one row of counts per start to `site-<id>.graph.jsonl` in the site's directory (`nodes_before`,
  `nodes_added`, `nodes_upserted`, `nodes`, `seq_before`, `seq_after`, `seconds`); no ref, no text.

### What the sandbox runs showed (Intel Xeon 2.10 GHz, 4 vCPU; sandbox figures, not results)

- The generator at seed 0 gives exactly section 3.1's rows at 200, 600 and 2,000 (users, sites, teams, records, real
  patterns, Gold discoverable, rare, decoys, entities); per site at 200: 2,574 / 1,620 / 1,789 / 1,876 / 1,805 /
  1,842 / 1,458 records (min 1,458, median 1,805, max 2,574).
- The 200-user world through `run_pipeline` (ingest, lexical extraction, 26 weeks of cells at 7 sites, HQ's store) and
  `detect`: 4.5 s. `as_of` is `2024-07-14`. Detection gives 55 snapshot candidates on channel X (all 55 are the
  candidate list; the floor of criterion 1 is 20); by Gold's labels 15 are true, 35 decoy, 5 background. The 20 fixed
  questions are 20 distinct valid questions.
- Every site store holds exactly its records (`forwarded_in = 0` rows equal the expected count, `seq` dense to that
  count, no late record); `run_pipeline` returned, so no duplicate and no rejection (M16).
- Seven persistent graphs from the pipeline's stores: node ids equal the record refs at every site (digests equal),
  node count equals the expected count, the watermark equals the count; build 0.3 to 0.7 s per site (about 0.2 s per
  1,000 nodes); a re-open adds 0 nodes in about 0.1 s.
- One Tesseract ranking over the largest site (2,574 nodes) takes about 1.0 s with the hash embedder; the 20 fixed
  queries repeat identically. A ranking returns the nodes above Tesseract's charge threshold (for the first fixed
  query at the largest site, 87 of the 1,677 nodes received by its `as_of`), every one received on or before the
  question's `as_of` (`AsOfStorage`); criterion 3 compares the top 10 and the full returned order of that list.
- Byte identity with the spike: on the spike's seed-1 planted world (`tests/routing_spike/helpers.small_world`, four
  candidates, six sites), `E2ESiteProcess` and the spike's `SiteProcess` give identical verdict bytes on all 24
  (question, site) pairs and identical descriptors.
- A child process (`E2EChildProcessEndpoint`) killed with SIGKILL answers `wire:closed` and, reopened on the same
  graph and store, adds 0 nodes and answers the remaining questions with the bytes a never-killed process gives.

### Criteria this part carries and how the tests check them

| Criterion | Check here |
|---|---|
| 8 (EMERGENCE load, 0 loss), site half | `test_c8_every_site_graph_holds_one_node_per_own_record_with_the_record_refs_as_ids`: per site, `graph_node_ids` (M9, `mode=ro`, after the process exited) has the expected count and the record refs' digest; the sum is 12,964 = `world.records`; `test_every_site_store_holds_exactly_its_records_and_the_pipeline_raised_nothing` (M16) |
| 3 (idempotent re-ingest), site half | `test_c3_reingest_leaves_the_node_ids_the_count_and_the_20_fixed_rankings_unchanged_at_every_site`: at every site, `EdgeSite.ingest` of the site's records again reports every one a duplicate, the re-sync adds nothing, node ids and count are unchanged, and the top 10 and the full order of the 20 fixed queries are unchanged (digests) |
| 4 (recovery), site restart | `test_a_crash_during_the_build_resumes_from_the_watermark_to_the_same_graph`, `test_process_mode_answers_byte_identically_and_survives_a_kill`, the spike-world restart tests |
| `as_of` bound (section 4, item 2) | `test_the_ranking_and_the_answers_see_only_records_received_on_or_before_as_of` |
| M17 | `SourceHunkTests`: the unified diff of each re-implemented `__init__` against the spike's holds exactly the named hunks; the inherited methods are the spike's own function objects |
| The pack (section 3.4) | `PackTests`: byte identity with a regeneration, the loader's CLI check, the brief's values file by file |

### Not done here (other parts of the build)

`hq_tesseract.py`, `hq_bridge.py`, `publish_api.py`, `harness.py`, `drills.py`, `measure.py`, `report.py`,
`__main__.py`, the workflow and the settings test are other steps (S3 to S8) and other branches. The oracle of
section 3.5 (expected statuses from Gold and the pack thresholds) and the discovery score of section 10 read
`gold_labels` and `fixed_questions` from this module when they are built.

## S3 and S4: `publish_api.py`, `hq_bridge.py`, `drills.py` (branch `wf/e2e-bridge`)

What was built of `docs/collective/E2E-INGEST.md` (the brief, with its section "Amended before any build", M1 to
M25) in the first build step: `publish_api.py` (S3) and `hq_bridge.py` (S4), the part of the path a pilot would use,
plus the broker-side pieces M3, M8 and M25 need and S5 to S7 will import. The brief is the rule; this note says how
the build carries it out, where the rule left a choice to the build, what the build found in the brief that the
later steps must know, and what is not done. It starts from the loop branch at `6f98472` (which holds `315a087`, the
amended brief) on branch `wf/e2e-bridge`.

Declaration. Everything here is synthetic and stamped `measurement: false`. In the sandbox (Intel Xeon 2.10 GHz,
4 vCPU, nats-server v2.10.22 at `/root/.local/bin`) this step ran: one probe of the publisher, the stream reader and
the three forgeries against a real `ProcessDriver`; the new suite `tests/e2e_ingest` (28 tests; the record is under
"Tests"); `tests/routing_spike` (61 passed, 125.6 s); `tests/mycelic/test_collective_guards.py` (82 passed, 1 failed
on a file this build did not write, see "Found in the brief", item 5). No EMERGENCE world, no pipeline, no detector,
no Tesseract, no site process and no end-to-end step ran. The HQ store every test reads is hand-built in the shape
the pushdown orchestrator writes, over the `device_quality` pack; no record, narrative or graph node exists in it.
None of this is a run, a result or a measurement of anything.

### The shape

| Module | Side | What |
|---|---|---|
| `research/e2e_ingest/publish_api.py` | bridge | the two event types, `conclusion_text`, the payload and memory builders (key for key as `research/routing_spike/publish.py:80-102`), the idempotency keys of section 7, `Publisher` (one `POST /events` per event through `mycelic.sdk.MycelicClient.publish_events`, bounded retries, a ledger and a watermark), `event_ids` |
| `research/e2e_ingest/hq_bridge.py` | bridge | `HqStoreReader` (`mode=ro`, one connection per call, literal ordered SELECTs; `file_sha256`), `Position` and the watermark file, `HqBridge` (`pending`, `plan`, `events_for`, `publish_conclusion`, `run`), `BridgeReport` |
| `research/e2e_ingest/drills.py` | harness | `E2EProcessDriver` (section 3.6 and M5), `read_stream` with `Keyring(driver.signing_key)` (M3), `fetch_raw`, `publish_raw`, the three forgeries of M8 (M25), `signature_failures` from `/metrics`, `broker_version`, `wait_idle`, `fabric_counts` |
| `tests/e2e_ingest/helpers.py` | tests | `nats_server_bin` (fails, never skips: M11), `LiveFabric`, `build_hq_store`, `verdict_body` (checked with `check_artifact`), the id formulas of `service.py:1220` and `:1232` |
| `tests/e2e_ingest/test_publish_api.py` | tests | the schema against the spike's own fabric, the keys, the publisher, the live publish, the forgeries |
| `tests/e2e_ingest/test_hq_bridge.py` | tests | the reader, the plan, the events, the refusals, the live bridge with the broker kill, the rerun, the resume, the import guards of c7 |

No pinned module was changed: nothing under `mycelic/collective/` at the top level, `detect/`, `edge/`, `packs/*.py`,
`evaluate/`, `followup/`, `inference/`, `pushdown/`, `experiments/`, `onboard/`, `connectors/openfda.py`,
`pilot/audit.py`, `pilot/power.py`; nothing under `research/routing_spike/`, `research/mycelic/`,
`NeuralGraph/research/coordination/`, `NeuralGraph/research/retrieval/`, `NeuralGraph/chat_memory/llm.py`,
`NeuralGraph/chat_memory/textutil.py` or `mycelic/*.py`; no `CHOICE-*`, `run-*.json` or `*-settings.json`; nothing
under `docs/collective/replay/vehicles/pack/`; not `lab/j1.py` or `lab/goldlabels.py`. Every one of them is imported.

### How the rules are carried out

- **Section 3.6, the events.** `verdict_payload` carries site, question id, source, verdict id, verdict, reason, the
  three buckets, newest week, window and sha256; `conclusion_memory` carries `entity`, `slot` (the predicate),
  `value` (the gate status), `topic` `collective/<pack id>`, `kind` `risk`, `visibility` `team`, the verdict event
  ids as `source_event_ids`, and the metadata (entity type, entity id, predicate, candidate key, question id,
  conclusion id, `as_of`, snapshot week, route label `R`, routed sites). `test_payloads_and_memory_equal_what_the_
  spikes_fabric_publishes` publishes the same rows through the spike's in-process `Fabric` and compares every key of
  the stored events and the stored note with ours. One organisation `emergence`, one agent `hq`; each event is its
  own POST.
- **Section 7, the keys.** `verdict_key` is `{question_id}:{site}:{seq}`; `conclusion_key` is
  `{conclusion_id}:{version}`. The live tests assert the API's ids equal `evt_<hash(hq, "event", key)>` and
  `mem_<hash(hq, "event-memory", key)>` and that a second POST of a key is `created: false` with no memory.
- **Retries.** The SDK retries connection errors and 429, 502, 503, 504 (`sdk/__init__.py`); `Publisher` adds a
  bounded loop over the same set (default 8, backoff 0.25 s doubling to 5 s) and raises `PublishError` on any other
  status at once. A `created: false` after a failed attempt is `ambiguous` and counted apart, so a report never
  claims a creation it cannot tell from a re-send.
- **The watermarks.** `Publisher` keeps `publish_watermark.json` (cumulative publishes, created, duplicate,
  ambiguous, the last key and event id) and `publishes.jsonl` (one result per line); `HqBridge` keeps
  `watermark.json` with the last `(created_at, conclusion_id, version)` published, moved after every conclusion.
  `pending()` is every conclusion past it; `run(limit=5)` then `run()` publishes the rest, in order
  (`test_the_watermark_resumes_where_a_run_stopped`). Correctness rests on the keys, as section 7 says: a bridge
  with an empty watermark re-POSTs everything and creates nothing.
- **Read only.** `HqStoreReader` opens `file:...?mode=ro` per call, like `HqReader` (`store.py:678-684`), and the
  bridge module holds no SQL write statement (a test greps its source). `BridgeReport.store_sha256_before` and
  `_after` are the sha256 of the store file; the live test asserts them equal across the run and the rerun.
- **Order.** Conclusions in `(created_at, conclusion_id, version)` order; per conclusion the `pd_verdicts` rows in
  the store's own `ORDER BY site, seq`, then the conclusion event citing the verdict ids the API returned. Every
  pending row is planned (read and checked) before the first POST, so a store the bridge cannot read publishes
  nothing (`test_nothing_is_published_when_a_later_row_cannot_be_planned`).
- **Drill 3 (fabric half) in the suite.** N = 80 (20 questions, 3 verdict rows each, 20 conclusions; the floor of
  section 2.3), the broker killed by the publisher's `on_publish` hook after publish k = N // 2 = 40, the rest
  taken by the outbox (`outbox_pending >= 1`, `connected` false, `status` `degraded`), the broker restarted, idle:
  every API id (80 `agent.event`, 20 `memory.observed`) `applied` with `applied_at` set, `outbox_pending` 0,
  `consumer_pending` 0, `reconnects >= 1`.
- **M3 in the suite.** `read_stream` fetches every sequence of stream `MYCELIC` from `first_seq` to `last_seq`
  through `JetStreamContext.get_msg` with the driver's credentials and checks each under
  `Keyring(driver.signing_key).verify_event(data, headers["Mycelic-Signature"])`; the tests assert `fetched ==
  stream_messages`, `verified == fetched`, `foreign == 0`, every API id present exactly once, `stream_messages ==
  sum(kinds) + foreign`, and that a wrong key verifies none.
- **M8 and M25 in the suite.** Three forgeries through a harness nats-py connection on
  `subject_for("emergence", "memory.observed")`, each with its own `Nats-Msg-Id`: no header; a header under a random
  key; one of our own `memory.observed` messages re-published with its `memory_id` replaced and its original header.
  After forgery i the counter `mycelic_events_failed_total{stage="signature"}` is exactly i (read with the exact
  label), the three memory ids are 404, `stream_messages_foreign == 3`, our ids are all still exactly once, and the
  audit holds three `event.rejected` rows.
- **Drill 7 (bridge half) in the suite.** A second bridge with an empty watermark: 80 publishes, 0 created, 0 new
  memory ids, 0 new event ids, `stream_messages` unchanged, the active memory ids unchanged, the event rows
  unchanged, the store file unchanged.
- **c7 in the suite.** `static_imports` and `imports_loaded` (`run.py:97-126`) over `publish_api` and `hq_bridge`
  against `FORBIDDEN_HQ` plus `research.e2e_ingest.site_graph` and `mycelic.service` return empty lists; `drills`
  loads neither numpy, the retrieval package, the backend module, the service nor the site process.
- **Section 3.6 and M5, the driver.** `E2EProcessDriver.env()` adds `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG=5000` and
  `MYCELIC_REPLAY_MAX_REJECT_RATIO=0.05` and changes nothing else of `harness.py:132-142`.

### The contract with S5 (`hq_tesseract.py`)

The bridge reads the store and guesses nothing. For every conclusion it needs:

1. `pd_questions.body` = the question bytes (`build_question`) made under the bridge's pack (`pack_hash` is
   checked), so `question_id`, `candidate_key`, `params`, `window` and `as_of` come from it; a body that disagrees
   with its row, or another pack's question, is a `BridgeError`;
2. `pd_questions.run_id` = the id of the detection run saved with `CollectiveStore.save_run(result)` (the `detect`
   result of the X run), whose `candidates` row for `candidate_key` carries `snapshot.week`: that is the memory's
   `snapshot_week`. The spike's `build_world` never calls `save_run`; S5 must. A question with no run or no
   candidate row is a `BridgeError` ("names no detection run", "no candidate snapshot week");
3. `pd_routes` in rank order = the routed sites (`routed_sites`), one row per routed site;
4. `pd_verdicts` = one row per routed site, the site's exact bytes (source `site`) or `gate.hq_record_body` (source
   `hq`); `sha256 == sha256_hex(body)` and a strict-JSON body whose `verdict` is the row's are checked before any
   POST, and a mismatch is a `BridgeError`; an `hq` row's event carries `sha256: null`, as the spike's does
   (`hq.py:273`);
5. `pd_conclusions.conclusion_id == "c-" + question_id[:32]`, `status` the gate's.

The fixture `tests/e2e_ingest/helpers.py:build_hq_store` is this contract written out; S5's test should feed a store
its own `hq_tesseract` wrote through the same bridge.

### Found in the brief while building (corrections the later steps must carry; nothing is lowered)

1. **M3 says "registration writes none". It does.** `register_agent` inserts an `agent.registered` event into the
   outbox (`service.py:2028-2033`) and the service publishes it; the rules file puts `rule.upserted` events on the
   stream at start (three in the probe). The stream of the probe held `rule.upserted` 3, `agent.registered` 1,
   `agent.event` 3, `memory.observed` 1. `stream_kinds` must count every kind, and the c1 equation
   `fabric.stream_messages == sum(stream_kinds.values()) + stream_messages_foreign` holds exactly as written once it
   does; the sentence "the service puts five kinds on the stream and nothing else" is false and the harness must not
   assert it. The equalities `stream_kinds["agent.event"] == publish.publishes` and
   `stream_kinds["memory.observed"] == publish.conclusion_events` hold (both asserted in the suite).
2. **A forgery needs its own `Nats-Msg-Id`.** The stream de-duplicates on that header inside its window; a forgery
   re-using an id of ours is dropped by the broker, never reaches the consumer and proves nothing. `drills` uses
   `evt_forged_<memory_id>`. M8 does not say so.
3. **The stored memory cites the conclusion event too.** The service adds the conclusion event's own id to the
   embedded memory's `source_event_ids` (`service.py:1229`), so a note's sources are the verdict events plus one.
   c2's "`source_event_ids` include one verdict event per routed site that answered" holds; an equality check must
   add the conclusion event id (`tests/routing_spike/test_publish_score.py:69-71` already reads it this way).
4. **Labels are stored canonical.** The service stores `entity` and `slot` through `canonical_label` (lower case):
   `L20046` is stored as `l20046`. The memory's `metadata.entity_id` keeps the canonical id as sent. The discovery
   scorer (c9) must match on the metadata, not on the `entity` column.
5. **The guards suite is red on this tree for a reason outside this build.**
   `tests/mycelic/test_collective_guards.py::NoModelNamesTests::test_repository_scan_has_no_hits` fails on
   `docs/collective/E2E-INGEST.md` (two hits: the loop branch's name, written in the brief's declaration), because
   the scan's exemption covers one earlier provenance ref only. This build did not write that file and does not edit
   the brief. Criterion 10 (`tests/mycelic` green on the exact commit) cannot be met until the brief's text is
   amended in its own commit (the name replaced by a reference), or the scan's exemption is widened in the guards
   test, which is a pinned-adjacent decision for the loop, not for a build step. This note names no branch for the
   same reason.
6. **The stream read tolerates a purge.** `get_msg` on a sequence the stream no longer holds is reported as a gap
   (the message is absent from `messages`), never invented; the harness's `fetched == stream_messages` catches it.

### Tests

| Test | What it proves |
|---|---|
| `SchemaTests` (4) | event types and `conclusion_text` equal the spike's; keys are the store primary keys; payloads and memory equal the spike's own fabric output, key for key; an HQ record has no sha256 |
| `PublisherTests` (6) | retries with backoff on 503 and connection failures; a 400 raises at once; exhaustion raises; `ambiguous`; the watermark and ledger follow every publish and continue across instances; only the two event types with a key are sent |
| `LivePublishTests` (1) | against a real nats-server and `ProcessDriver`: ids equal the service's formulas, re-POSTs `created: false`, every stream message verifies under the driver's key and under no other, every API id once, `/admin/events` all applied, broker version from the live broker equals the binary's |
| `ForgeryTests` (1) | the three forgeries of M8: counter exactly 1, 2, 3; three 404s; `foreign == 3`; ours untouched; three `event.rejected` audit rows |
| `StoreReaderTests` (6) | the fixture's shape (20 / 60 / 60 / 20; N = 80); conclusion order; the plan (route by rank, snapshot week, sources, status); the events of section 7; the bridge without a broker publishes 80 in order and moves the watermark; the resume |
| `BridgeRefusalTests` (6) | a missing store; no detection run; no candidate; a sha256 that does not hold; nothing published when a later row cannot be planned; another pack's question and a body that disagrees with its row |
| `LiveBridgeTests` (1) | drill 3's fabric half with N = 80 and k = 40; M3 over the whole stream; the notes equal the conclusions with the right metadata and sources; lineage reconstructable; drill 7's bridge half; the first bridge's watermark empty afterwards |
| `ImportGuardTests` (3) | c7 for `publish_api` and `hq_bridge`; `drills` loads no numpy, retrieval, backend, service or site module; the bridge opens the store `mode=ro` and holds no write statement |

Run: `python -m pytest tests/e2e_ingest -q -p no:cacheprovider`. The live tests start a real nats-server
(`MYCELIC_NATS_SERVER_BIN`, then PATH, then `/root/.local/bin/nats-server`) and a real service through
`E2EProcessDriver`; a missing binary fails them (M11). Sandbox record for this step, the last run before the
commit: 28 passed, 176 subtests, 12.56 s (an earlier run of the same suite took 38.9 s; both figures are the
sandbox's and mean nothing beyond it); `flake8 --max-line-length 120` clean over the new files. The wider suites run
on the same tree for the record: `NeuralGraph/tests` 264 passed, 1 skipped, 76.3 s; `tests/routing_spike` 61 passed;
`tests/mycelic` see "Found in the brief", item 5, for its one failure.

### Not done (the brief's S2, S2b, S5 to S9; every criterion still open)

- `site_graph.py` (S2), `emergence_pack.py` and `emergence_world.py` (S2b), `hq_tesseract.py` and `harness.py`
  (S5), the drills' remaining parts (reference pass, main pass with the site restart, lineage witnesses of M1, the
  scan of drill 5, the service rebuild of drill 6, the site re-ingest of drill 7), `measure.py` (S7), `report.py`,
  `__main__.py`, `.github/workflows/e2e-ingest.yml` and `tests/e2e_ingest/test_workflow.py` (S8), the settings
  test of section 6 against the tables, and S9's recorded runs. None exists.
- No criterion 0 to 10 is met by this step: the suite exercises pieces of c0 (a keyed `ProcessDriver`, transport
  `nats-jetstream`, the broker version), c1 (signatures, the forgery counter), c3's bridge half, c4's fabric half
  and c7 for the two bridge modules, on a hand-built store, not on the EMERGENCE world, and none of it is a run.
- The local end-to-end run at the 200-user world (criteria 0 to 4, 7 and 8 before a commit) belongs to S5 and
  later; it has not run and could not: the world, the sites and HQ do not exist yet.
- Criterion 10 was blocked on this tree by item 5 of "Found in the brief" until the brief was amended; the
  deployment branch amended it at `65428d9` (merged into the build at `3c850d8`).
- Nothing is pushed.

## S5 and S6: `hq_tesseract.py`, `harness.py`, `report.py`, the drills (branch `wf/e2e-harness`)

The end-to-end run itself: the world of S2b through the seven site graphs of S2, HQ with Tesseract routing, the
bridge of S3 and S4 into a real keyed fabric, the seven drills of section 8, the checks of section 9 and the report
of section 5. This branch starts from the bridge branch with the site branch merged into it (the merge resolved two
add/add conflicts, the package docstring and this note, by keeping both sides whole).

    python -m research.e2e_ingest.harness run --world 200 --seed 0 --out DIR [--skip-drills]

### The shape

| Piece | Where | What |
|---|---|---|
| HQ | `research/e2e_ingest/hq_tesseract.py` | `E2EHq(Hq)`: the spike's HQ with the `FailureInjector` kept and the coordinator rebuilt with it (M4 check 3), the six constructor arguments in the spike's order; `replace_endpoint` (drill 2 reopens a killed child process); `probe_masked` (the eligibility probe); `record_question` (`pd_questions` + `pd_routes` + `pd_verdicts` + `pd_conclusions`, the orchestrator's shape); `save_detection_run` (the candidates' rows carry the snapshot week the bridge reads); `route_of` (the route the Router will choose, used to plan the run) |
| The run | `research/e2e_ingest/harness.py` | `E2ERun`: the world, the reference pass (drill 1, three threads per question, M15), the main pass in process mode with the site kill and the probe (drill 2), the publish with the broker kill (drill 3), the forgeries (drill 4), the lineage witnesses and the scan (drill 5), the service rebuild (drill 6), the re-ingest and the bridge rerun (drill 7); the oracle of section 3.5 and M20 (`pattern_facts`, `expected_status`); criterion 7's import guards (`static_imports`, `imports_loaded`, `guard_report`); the CLI |
| The report | `research/e2e_ingest/report.py` | the header of section 5 with the runner's CPU and the code stamps (M10, M13), `stat_block` (p50/p95 with the samples beside them, M12), the scans of drill 5 and M24, and one function per criterion (`c0_real_fabric_keyed` to `c8_emergence_load`) returning `{ok, assertions, failed, ...}`, plus `verdict` |
| The broker-side drills | `research/e2e_ingest/drills.py` (added to) | `metric_sum` and `event_counters` (the applied counter carries `kind` **and** `result`, so only `result="applied"` counts an event once), `audit_rows`, `ready`, `service_meta` / `known_key_ids` (M3's `key_id_in_service_db`), `fabric_db_text` and `stream_payloads` (drill 5's scan inputs) |
| Tests | `tests/e2e_ingest/test_harness.py` | the oracle, the scans, every assertion of every check with one mutation each (a check that stopped checking fails), criterion 7 for real (including a probe module that does import numpy and the service, so the guard can fail), and one real run of the harness on the 200-user world with every drill |

No pinned module was changed: nothing under `mycelic/`, `research/routing_spike/`, `research/mycelic/`,
`NeuralGraph/`, `lab/`; every one of them is imported. `research/e2e_ingest/drills.py` gained functions and lost
none.

### How the brief's rules are carried out

- **Tesseract routing (section 4, M4).** The main pass asks through `TesseractCoordinator.execute` with arm R's
  order as `preferred_node_ids` and `budget.max_nodes = 3`; the report carries the live `routing.coordinator_class`
  and `routing.router_class` (the check asserts the coordination package's own classes), per question the eligible
  count, the route, the route size and the `TraceEventType` sequence (`route_selected` before every
  `retrieval_selected`), and `routes_equal_arm_order` (the route the Router chose equals the first three eligible
  sites of arm R's order). The eligibility filter is exercised once: with the restarted site's process down the
  harness applies `failure.apply(InterventionTarget.ROUTE, site)`, asks one probe question through `hq.ask` alone
  (nothing written to HQ's store, nothing published), then clears the mask. The probe candidate is chosen so that
  the masked site **is** in its unmasked arm-R route, and the report carries both routes: a mask that excluded a
  site the route would not have held anyway proves nothing. `router_excluded_masked` and `intervention_records`
  are both 1, and the probe's verdict sources hold no `hq` record (a Router that ignored the mask would have asked
  the dead process and got `wire:closed`).
- **The five lineage witnesses (M1).** For every site-sourced verdict event: `pd_verdicts.sha256` recomputed from
  the stored bytes, a response frame of that site in `hq/wire.jsonl` whose decoded body has that sha256, a row of
  `hq/intake.jsonl` with that sha256 and question id and `source == "site"`, a verdict row of that site's
  `site-<id>.egress.jsonl`, and `RecordStore.verdict_by_id(body["verdict_id"]).sha256`. Then the join as drilled:
  `verdict_for_ref(evidence_ref)` back to the same verdict id, its `confirming_refs` and `entity_refs`, and every
  such record's `received_date` on or before the question's `as_of` (`records_after_as_of` is reported and must be
  0). c2 asserts each witness count equals the site-sourced verdict events.
- **The stream binding (M3).** Every message of stream `MYCELIC` is read by sequence and checked under
  `Keyring(driver.signing_key)`; the key id the harness holds is looked up in the service's own
  `meta.known_key_ids`, read `mode=ro`. The report keeps two stream reads, `signatures.stream_before` (before any
  forgery: verified == fetched, foreign 0) and `signatures.stream_after` (foreign 3, verified == fetched − 3), and
  the flat fields of section 5 mirror the second. `stream_kinds` counts every kind the service puts on the stream,
  which is more than M3's five (the bridge branch's note, item 1); the totals equation holds as written.
- **The forgeries (M8, M25).** Three publishes straight to the broker over a harness nats-py connection, each with
  its own `Nats-Msg-Id`; after forgery *i* the counter `mycelic_events_failed_total{stage="signature"}` is exactly
  *i*, `GET /memory/<forged>` is 404 for all three, and the audit holds at least three `event.rejected` rows.
- **The broker kill (drill 3, M6).** One `POST /events` per event through `publish_api`; the publisher's
  `on_publish` hook kills nats-server after publish k = N // 2; the rest is taken by the outbox. The outage is
  sampled **after the last POST and before the broker comes back**, which is when the outbox holds what the broker
  could not take (`outbox_pending_during_outage >= 1`, `connected` false, `status` `degraded`); then the broker is
  restarted and the fabric brought to idle.
- **The applied deltas (M6).** `mycelic_events_applied_total` carries `kind` and `result`
  (`applied`/`duplicate`/`ignored`), and a broker restart re-delivers what was in flight, so the per-kind delta is
  read over `result="applied"` only; the deliveries of every result are reported beside it. See "Found in the
  brief", item 1.
- **The service rebuild (drill 6, M5).** The active memory ids and every conclusion's `report_digest` are recorded,
  the service is SIGKILLed, its database deleted and the service restarted; after idle the ids and digests must be
  identical, and the replay's own audit row `recovery.signature_rejections` must show the three forgeries rejected
  with `blocked` false, no `ready_block` and `/ready` 200.
- **Re-ingest and the bridge rerun (drill 7, criterion 3).** Every site ingests its records again (all duplicates),
  every graph re-syncs (0 nodes added), and the 20 fixed queries are compared per site before and after on the top
  10 and on the full returned order, as digests. Then a second bridge with an empty watermark re-POSTs every event:
  0 created, 0 new memory ids, 0 new event ids, the stream unchanged, HQ's store file unchanged.
- **Criterion 8 (M9, M16).** `ingested` is the count of the site store's own records (`forwarded_in = 0`) read
  `mode=ro`; `duplicates` and `rejected` are 0 because `run_pipeline` returned, which the report records as
  `ingest.pipeline_raised: false`; the graph's node ids are read from the graph file `mode=ro` after the site
  processes have exited and their digest is compared with the record refs computed from `corpus.recs` and
  `org.ancestor_at` alone.
- **Criterion 7.** `static_imports` is re-implemented here because the spike's own version resolves relative
  imports in `research.routing_spike`; `imports_loaded` runs a fresh interpreter per module. The three modules
  (`hq_tesseract`, `hq_bridge`, `publish_api`) are checked against `FORBIDDEN_HQ` plus `research.e2e_ingest.
  site_graph` (and `mycelic.service` for `publish_api`), the drills against the site and service modules, and
  `run.spike_code_hash` against M13's pinned value over 142 files.
- **The oracle (section 3.5, M20).** Per routed site, the pattern's facet records inside the question window: how
  many, from how many distinct users and events, whether one of them is a polarity −1 record, and the newest week —
  read from the generator's own rows, harness side. `supported` when at least two routed sites are strong and the
  newest strong week is within 42 days of `as_of`, `stale` when it is older, `hypothesis` when no routed site is
  strong; one strong site, or a contradiction at any routed site, is ambiguous and is reported with its reason,
  never asserted. c1 asserts every unambiguous planted candidate's fabric status equals the oracle's.
- **Privacy.** No record, narrative or node id is an argument to anything HQ, the bridge or the report touches; the
  report holds ids, counts, statuses, digests and seconds. Drill 5's scan reads every row of the fabric's database
  and every message of the stream (both decoded with replacement, so no byte can hide behind an encoding) and counts
  the record-ref pattern, the narrative marker and the first 24 characters of every narrative of the world; a test
  runs the same three scans over the written report.

### What the report does not claim

The end-to-end job's checks are c0 to c4, c7 and c8. `verdict.pass` is true only when every check that **ran** is
`ok`, and `verdict.not_run` names the ones this build cannot make, with the reason: `c5_measured` and the scaling
table are the measure job's (S7), `c6_ci` is the workflow's (S8), `c9_benchmark` needs the simulator's columns
(S7) although `discovery` already carries the path's own score, and `c10_suites_green` needs the junit evidence of
M2 from the suites CLI (S8). `verdict.complete` is false while any is missing, so no reader can mistake this report
for the brief's whole gate.

### What the recorded local run showed (Intel Xeon 2.10 GHz, 4 vCPU, nats-server v2.10.22; sandbox figures)

`python -m research.e2e_ingest.harness run --world 200 --seed 0 --out DIR`, 55 candidates, every drill, 701 s of
wall time. `verdict.pass` true over the seven checks the end-to-end job runs; `complete` false, since c5, c6, c9's
simulator columns and c10's junit evidence belong to S7 and S8.

| Check | Assertions | Result |
|---|---|---|
| `c0_real_fabric_keyed` | 9 | ok |
| `c1_signed` | 25 | ok |
| `c2_lineage` | 15 | ok |
| `c3_idempotent` | 12 | ok |
| `c4_recovery` | 25 | ok |
| `c7_guards` | 6 | ok |
| `c8_emergence_load` | 6 | ok |

- **The world and the load.** 309 users, 7 sites, 35 teams, 12,964 records, 150 entities, 40 real patterns, 40 Gold
  discoverable (18 rare), 50 decoys; `as_of` 2024-07-14; pipeline and detection 5.0 s; 55 snapshot candidates on
  channel X, all 55 asked (fewer than the brief's `top_n` 60, so the candidate list is the whole detected set).
  Per site 2,574 / 1,620 / 1,789 / 1,876 / 1,805 / 1,842 / 1,458 records ingested, each site's graph holding exactly
  that many MESSAGE nodes whose ids digest to the record refs; 0.26 to 0.39 s per 1,000 records to build, 0.33 s per
  1,000 over the world; loss 0.
- **Tesseract routing.** The live coordinator and router are the coordination package's own
  `TesseractCoordinator` and `Router`; every one of the 55 questions had 7 eligible sites, a route of 3, and
  `route_selected` before every `retrieval_selected`; every route equalled the first three of arm R's order. 166
  rankings (165 of the main pass and one of the probe) over graphs of 1,458 to 2,574 nodes, 0.76 s p50 and 1.31 s
  p95; question to verdict 0.79 s p50 and 1.34 s p95 per site, 2.40 s and 3.54 s per question.
- **The eligibility probe (M4).** The masked site was in the probe question's unmasked arm-R route and absent from
  the route the Router chose; 7 sites still eligible, route size 3, one `failure_injected`, one intervention record
  ended, no `hq` verdict record. The 200-user world detects 55 candidates and all 55 are asked, so the probe
  re-asked one of them (`candidate_source: "reused"`) rather than taking a spare; its result is a separate `Asked`
  and is neither written to HQ's store nor published, so the answer matrix is untouched.
- **The gate's statuses.** 47 supported, 7 hypothesis, 1 stale over 55 conclusions; 148 confirm, 15 refute, 2
  unknown, 0 HQ error records, 0 `budget` unknowns.
- **The oracle (c1's hardest assertion).** 8 of the 15 planted candidates were unambiguous: 7 expected `supported`
  and the fabric says `supported`, 1 expected `hypothesis` and the fabric says `hypothesis`. The other 7 are
  ambiguous (one strong site, or a contradiction at a routed site) and are reported with their reason, not asserted.
- **Publishing.** 220 publishes (165 verdict events, 55 conclusions), all created, 0 duplicates, 0 ambiguous; 275
  API ids (220 `agent.event` plus 55 `memory.observed`), every one applied exactly once.
- **The broker kill.** nats-server killed after publish 110 of 220; during the outage the outbox held 138 and the
  service read `connected: false`, `status: degraded`; after the restart one reconnect, `outbox_pending` 0,
  `consumer_pending` 0, applied deltas exactly 220 `agent.event` and 55 `memory.observed`, 0 ignored, 55 active
  notes, 0 stream duplicates. `created_to_applied` p50 28 s and p95 29 s are the outage's own waiting time, not a
  latency measurement; criterion 5's figures are the measure job's.
- **Signatures.** Before any forgery 279 stream messages fetched, 279 verified, 0 foreign, every API id exactly
  once, kinds `agent.event` 220, `memory.observed` 55, `rule.upserted` 3, `agent.registered` 1 (M3's "registration
  writes none" is false; the totals equation holds as written). After the three forgeries 282 fetched, 279
  verified, 3 foreign; the counter read exactly 1, 2, 3; all three forged memory ids 404; three `event.rejected`
  audit rows.
- **Lineage.** 55 conclusions, all reconstructable with their verdict events among the memory's sources; 165
  site-sourced verdict events, each with all five witnesses (165 in `pd_verdicts`, 165 in HQ's wire log, 165 in
  HQ's intake log, 165 in the sites' egress logs, 165 in the sites' own stores); 163 of them (the confirms and
  refutes) joined site-side through `verdict_for_ref` to their confirming and entity records, every one received on
  or before the question's `as_of`; 0 records after it. The scan read 396,084 bytes of the fabric's database and
  282 stream messages (264,647 bytes) and found 0 record refs, 0 narrative markers and 0 narrative prefixes. The
  narrative-prefix set of this world holds 1,312 distinct 24-character prefixes (the first 24 characters of an
  EMERGENCE narrative are its day stamp and user node name, so many records share one); the marker pattern covers
  the same ground more sharply and both are 0.
- **Recovery.** The restarted site: 16 questions before the SIGKILL, 15 after, 2,574 nodes before and after (0
  added), every post-restart answer byte-identical to a fresh `E2ESiteProcess` whose graph was built from nothing,
  and the whole answer matrix byte-identical to the reference pass (0 mismatches). The service rebuild: database
  deleted and rebuilt in 1.8 s, the same 55 active memory ids, the same 55 verification digests, the replay seeing
  282 deliveries and rejecting the 3 forgeries (ratio 0.011, under the 0.05 the driver sets), `replay_blocked`
  false, no `ready_block`, `/ready` 200.
- **Idempotency.** Re-ingest: 12,964 duplicates, 0 ingested, 0 rejected, 0 nodes added at any site, and all 140
  fixed-query rankings (20 per site) identical on the top 10 and on the full returned order. The bridge rerun from
  an empty watermark: 220 publishes, 0 created, 0 new memory ids, 0 new event ids, the stream unchanged at 285
  messages, the notes identical, HQ's store file unchanged. (285, not 282: the rebuilt service re-emitted its three
  `rule.upserted` events at start. The checks compare like with like — c1 against the sample taken with the stream
  read, c4's replay against the stream as it was when the replay ran, c3 before against after.)
- **Phases (seconds).** world 5.0; reference pass 177.8; main pass 146.5; fixed queries before 154.6; restart
  witness 19.3; fabric start 0.6; publish 30.9 (including the outage); forgeries 0.3; lineage and the scan 0.5;
  discovery 0.01; rebuild 2.2; re-ingest 160.7 (which contains the 160.6 s of the second ranking pass); bridge
  rerun 1.4. Total 701 s. A CI runner is not this sandbox, and the brief's 20-minute bound for the end-to-end job
  is neither met nor missed by a local figure; the first recorded CI run decides (section 11).

### Found in the brief while building (nothing lowered)

1. **M6's `applied_delta_*` needs the `result` label.** `mycelic_events_applied_total` carries `kind` **and**
   `result` (`applied`, `duplicate`, `ignored`; `service.py:1407`, `:1442`, `:1467`, `:1484`), and a broker restart
   re-delivers what was in flight, so the per-kind total over every result exceeds N (a probe run at 12 publishes
   read 18 `agent.event` deliveries). The deltas are therefore read over `result="applied"`, which is what "applied
   exactly once" means; the deliveries of every result are reported beside them.
2. **M5's ratio arithmetic needs the floors.** Three forgeries over the floors (80 publishes + 20 conclusions + 3)
   give 0.029, under the 0.05 the driver sets; the recorded run's 3 over 282 is 0.011. Below the floors the ratio
   passes 0.05 and the rebuilt service keeps `/ready` at 503 — which is M5's own arithmetic, not a defect: a run
   that does not reach the brief's N cannot drill the rebuild. The suite's live run therefore asks 20 candidates
   (N = 80, the floor), not a handful.
3. **M4's probe needs a candidate the masked site would have answered.** The brief says "the next candidate in
   order"; a mask on a site that the probe's route would not have held anyway excludes nothing and proves nothing.
   The harness picks the first candidate whose unmasked arm-R route holds the masked site, prefers one beyond the
   asked list, and reports both routes and which it used.
4. **The 200-user world detects fewer candidates than `top_n`.** 55 with a snapshot at seed 0, so "the first 60"
   is all of them and there is no spare for the probe (item 3). The floor of 20 asked candidates (section 2.3) is
   met with room; the brief's 60 is an upper bound, not a count the world owes.
5. **The stream grows at a service restart.** The rebuilt service re-emits its rules file's `rule.upserted` events,
   so the stream after drill 6 holds three more messages than before it. Every check compares samples taken at the
   same moment, and the report keeps each sample where it was taken.
6. **The narrative-prefix scan is weak at 24 characters on this world** (1,312 distinct prefixes for 12,964
   records, because an EMERGENCE narrative starts with a day stamp and the user's node name). It stays as the brief
   fixes it; the record-ref pattern and the marker pattern are the sharp ones, and all three are 0.

### Tests (`tests/e2e_ingest/test_harness.py`)

| Test class | What it proves |
|---|---|
| `OracleTests` | section 3.5 and M20: two strong sites inside the freshness window expect `supported`, older expect `stale`, no strong site expects `hypothesis`, one strong site or a contradiction at any routed site is ambiguous, and a site holding a polarity −1 record of the pattern is not strong |
| `ScanTests` | drill 5's three patterns find a planted ref, marker and narrative prefix and count 0 over a verdict-shaped payload; M24's scan names a tier label when one is present |
| `CheckTests` | a green report passes every check the end-to-end job runs; **one mutation per assertion** (98 of them) each makes its own check fail and names itself in `failed`; every assertion of every check has a mutation (the counts are compared); the verdict fails when any check that ran is not ok, and `complete` is false while a check did not run |
| `GuardTests` | criterion 7 for real: the three modules import nothing forbidden, statically and in fresh interpreters; the drills load no site or service module; the static scan resolves this package's relative imports; a probe module that **does** import numpy and the service is caught by both halves; the spike's code hash and file count are M13's pinned values and its paths are clean |
| `LiveRunTests` | one real run on the 200-user world with a real nats-server and service and every drill of section 8: only the floors the short scale cannot reach may fail, the fabric is real and keyed, every event is signed and in the stream exactly once and the three forgeries are terminated, Tesseract routed every question and the Router excluded the masked site, every conclusion has five witnesses per verdict and the scans are 0, the drills of c3 and c4, the world loaded whole at every site, the discovery score computed from the fabric and Gold, the report's schema and runner, and no record ref, narrative or tier label anywhere in the report |

The live run asks the first 20 candidates (N = 80 publishes, the floor of section 2.3, so the broker kill and the
service rebuild are drilled at the brief's own scale) and 2 fixed queries per site instead of 20, which is what
`SHORT_SCALE` allows to fail and the only thing it allows. Run: `python -m pytest tests/e2e_ingest -q
-p no:cacheprovider`. Nothing skips: a missing nats-server fails (M11).

Sandbox record for this step: the whole new suite (this file plus the world, pack, site-graph, publisher and bridge
files of S2 to S4) is **91 passed, 532 subtests, 494 s**, 0 skipped, on the commit this note belongs to; the
recorded end-to-end run above is `verdict.pass` true over c0 to c4, c7 and c8; `tests/mycelic/
test_collective_guards.py -k NoModelNames` reports the brief's own two hits and no others.

### Not done here

- `measure.py` and criterion 5 (the per-hop numbers at the three node counts, the rate sweep, the scaling table)
  and the simulator's columns of criterion 9: S7. The report carries `discovery` with the path's own score and
  `simulator: null` with its reason.
- `.github/workflows/e2e-ingest.yml`, `tests/e2e_ingest/test_workflow.py` and criterion 6: S8. The suites CLI of
  M2 (`python -m research.e2e_ingest suites --out DIR`) and with it criterion 10's junit evidence: S8.
- The section-6 settings test comparing the harness constants with the brief's tables: S8's settings test.
- `__main__.py` (`python -m research.e2e_ingest run|measure|check|suites`): the harness's own CLI is
  `python -m research.e2e_ingest.harness run ...` until then.
- S9's recorded CI runs and the committed reports under `docs/collective/e2e_ingest/`.
- The local report of the recorded run is not committed (it is a sandbox artifact, and S9 commits the CI ones);
  its figures are above and it holds no record text, which a test asserts.
- Criterion 10's guard hit was cleared upstream: the deployment branch amended the brief at `65428d9` (the
  declaration and section 11 name the deployment branch without its literal ref), and that commit is in this
  build since the merge `3c850d8`. The settings test (`tests/e2e_ingest/test_workflow.py`) reads the deployment
  branch's name from `mycelic.yml`, never from the brief.

### Fixes

- The section above was written by the stage the account's usage limit interrupted; its WIP commit (`0154242`)
  was neither reviewed nor run to the end before the interruption. The next section (`wf/e2e-harness2`) reviewed
  it against the brief, changed what the table there lists, and recorded a run of the finished harness; the
  figures above are superseded by that run's. Under M2 the `verdict.pass` of any run without the suites' junit
  evidence is false (`reason: suites_not_green`); what the section above calls "`verdict.pass` true" is the
  report's `checks_pass` now.

## S5 and S6, continued: the harness finished (branch `wf/e2e-harness2`)

The interrupted stage's WIP, reviewed against the brief and finished: `harness.py`, `hq_tesseract.py`,
`report.py`, the drills' additions and `test_harness.py` as `0154242` left them, plus `measure.py` (new, the
simulator's columns of section 10 only). This branch starts from `0154242`. The command is unchanged:

    python -m research.e2e_ingest.harness run --world 200 --seed 0 --out DIR [--skip-drills] [--queries N]
    python -m research.e2e_ingest.harness check REPORT [--recorded]

Declaration. Everything here is synthetic and stamped `measurement: false`. In the sandbox (Intel Xeon 2.10 GHz,
4 vCPU, nats-server v2.10.22 at `/root/.local/bin`) this stage ran: the whole `tests/e2e_ingest` suite (with
its one real run of the harness), two full runs of the harness on the 200-user world (one of the WIP as left, one
of the finished code; the second is the one recorded below), the simulator's two architectures alone at size 200
(under a second), and `flake8 --max-line-length 120` over `research/e2e_ingest` and `tests/e2e_ingest`. No model
ran. No record text or record ref appears in this note, in a log, in a test message or in a report.

### What changed against the WIP (the brief wins)

| Where | What | Why |
|---|---|---|
| `routing.questions` | the per-question list `{question_id, hq_eligible, route, route_size, status, trace}`; the count is `routing.questions_asked` | M4 says `routing.questions[]`; section 5's int is text the amendment overrides |
| `routing.per_question_route_ok`; c1 `every_question_eligible_and_routed` | every question of the main pass had every site eligible and a route of `min(3, eligible)` | M4 check 2 is per question, not only in the suite |
| `routing.tesseract.{read_verdicts, rows_for_read_verdicts, rows_outside_asked_pairs, rows_duplicated, asked_pairs, rows_equal_read_verdicts}`; c1 `tesseract_row_per_read_verdict` | the site diagnostics reconciled with the verdicts they produced: every confirm or refute of the main pass has exactly one row at its site, no row names a (question, site) pair HQ did not ask (the probe's pairs included), no pair has two | section 4, item 2 ("both are required and checked") |
| `publish.created_to_applied` | one sample per POST: a verdict's `agent.event`, a conclusion's `memory.observed` (the note's apply); the conclusions' own `agent.event`s beside it as `created_to_applied_conclusion_agent_events` | M18 |
| `discovery.*` | computed from the fabric's notes as drill 5 read them back (`metadata.entity_id`, `.predicate`, `.snapshot_week`, `.value`, the lineage check) and Gold's labels, never from HQ's store; `found_patterns` needs the lineage check; a decoy counts by its facet-0 (anchor, chain) key inside its window, with `decoy_acceptance_by_type` (accepted over the class's count) beside the counts; the oracle rows carry the fabric's status (what c1 compares) with `hq_status` beside it; `gold_discoverable_patterns` and `fabric_conclusions` so c9 and `check` work from the report alone | section 10; c1 ("fabric status"); c9 ("computed by the scorer from the fabric and Gold") |
| `discovery.simulator` | `measure.py` (new): `H_mycelic_full` and `A2_chunked_ctx` on the same world (`runner.build_world`, `run_arch`, `allocation("back-loaded")`, `evalm.evaluate`; never `run_matrix`), the five metric fields, `simulated: true`, under the 10-minute budget, else null with `reason: budget` | section 10, M14, M24; the WIP left the columns to S7 |
| `c9_benchmark` | run by the end-to-end job: every discovery field present, `found_patterns` ⊆ Gold's discoverable set, `recall_all` the ratio, criterion 5's per-hop numbers at this size numeric, the simulator columns present (null with `reason: budget` at the 2,000-user size only), the synthetic and measurement stamps | section 9; section 2.3, criterion 6 ("8 and 9's discovery score in under 20 minutes") |
| `verdict` | `pass` only when every check that ran is `ok` **and** the suites' junit evidence of M2 is in the report and green; otherwise `pass: false`, `reason: suites_not_green`, with `checks_pass` and `suites_green` beside it; the CLI exits 1 on `pass: false` | M2; criterion 10 ("a run on a tree whose suites are not green is not a result") |
| `check REPORT` | the report re-read: every percentile recomputed from its samples (`routing.tesseract`, `routing.question_to_verdict` per question and per site, `publish.created_to_applied`), the lineage counts from `lineage.rows`, the recall from `found_patterns`; the checks run again and compared with the stored ones; `--recorded` requires `run.github_run_id` and `run.command`; exit 0 only when everything agrees and the verdict passes | M12 |
| `routing.question_to_verdict.per_site_samples` | the per-(question, site) wire timings, so the per-site percentiles are recomputable | M12 |
| `ProbeResult.received` | the probe's per-site `{site, source, verdict}` (no body, no ref) | section 4, item 2 reconciles the probe's pairs too |
| M24 in the suite | the tier labels are read from the simulator's own `ALLOCATIONS` (none is written in the test) and must be absent from the report; the allocation name must be present | M24 records the allocation name; the WIP's test forbade it |
| Style | `flake8 --max-line-length 120` clean over both directories (two continuation lines in S2b's and S2's files, two in the WIP's test) | the stage's gate |

No pinned module was changed; `research/e2e_ingest/drills.py` is as the WIP left it. `hq_tesseract`'s store contract
with the bridge (the earlier section "The contract with S5") is unchanged.

### Found in the brief while finishing (nothing lowered)

1. **Section 5 and M4 disagree on `routing.questions`** (an int; a list of per-question records). The amendment
   wins: the list is `routing.questions`, the count `routing.questions_asked`.
2. **Section 4, item 2's "unknown with a record read".** A site writes its diagnostics row inside its retrieval
   (`site_process.py:330-335`), which runs for every fresh question on an entity in its master data — every entity
   is, here (section 3.3) — so an `unknown` after a retrieval that read nothing also has a row, and a question the
   site had already answered (its stored verdict is returned, `site_process.py:362-364`) has none. The check
   reconciles what the two sides can both see: every confirm or refute has one row, no row lies outside the pairs
   HQ asked (the probe's included), no pair has two; the unknowns' rows stay in the samples with their `read`.
3. **M4's probe with a re-asked candidate** (the 200-user world has no spare candidate, item 4 of the section
   above) runs a fresh retrieval at the replacement site only; the two sites that had answered the question return
   their stored verdict. The exclusion is still the Router's (the masked site is in the unmasked arm-R route and
   absent from the route chosen) and the probe's received pairs are in the report.
4. **M24 records the allocation name** (`back-loaded`) and forbids the tier labels; the WIP's test forbade both.
5. **M2 leaves no local run passing before S8.** `verdict.pass` is false without the suites' junit evidence, so
   the trial run below is `checks_pass` true, `pass` false, `reason: suites_not_green`, until the suites CLI of M2
   exists and `run --suites DIR` reads its files (S8). The checks themselves are the stage's result.
6. **Section 10's `found_patterns` and the oracle read the fabric, not HQ's store.** The WIP scored both from
   `pd_conclusions`; the brief says "recovered at HQ as `supported` conclusions in the fabric" and c1 says "fabric
   status". The service stores a note's `value` as `metadata.value` (canonical, lower case; the earlier section's
   item 4), which is what both read now; `hq_status` is reported beside it.
7. **The simulator's columns are nested.** Section 10 names them `simulator.H_mycelic_full` and
   `simulator.A2_chunked_ctx`; the report carries them as `discovery.simulator.architectures.<name>`, beside the
   allocation name, the world's counts, the metric list and the seconds (`measure.simulator_columns`), and c9 reads
   that path. Layout only: every field section 10 requires is present under the architecture's name.

### What the recorded local run showed (Intel Xeon 2.10 GHz, 4 vCPU, nats-server v2.10.22; sandbox figures, synthetic, `measurement: false`)

`python -m research.e2e_ingest.harness run --world 200 --seed 0 --out DIR` on the finished code, 55 candidates,
every drill of section 8, 1,181 s of wall time (its first nine minutes shared the four cores with the suite's own
live run, so the ranking phases are slower than the section above measured; nothing here is a timing result).
`verdict.checks_pass` true over the eight checks the end-to-end job runs; `verdict.pass` false with reason
`suites_not_green` (M2, item 5 above); `complete` false (c5, c6, c10). `check REPORT` re-read the written report:
every aggregate recomputed to itself (0 disagreements), every check reproduced, exit 1 as the stored verdict says.

| Check | Assertions | Result |
|---|---|---|
| `c0_real_fabric_keyed` | 9 | ok |
| `c1_signed` | 27 | ok |
| `c2_lineage` | 15 | ok |
| `c3_idempotent` | 12 | ok |
| `c4_recovery` | 25 | ok |
| `c7_guards` | 6 | ok |
| `c8_emergence_load` | 6 | ok |
| `c9_benchmark` | 6 | ok |
| `c5_measured`, `c6_ci`, `c10_suites_green` | | not run (S7, S8, S8) |

- **c0.** `E2EProcessDriver` (a `ProcessDriver`) with the binary set; the harness key set and its id in the
  service's `meta.known_key_ids`; transport `nats-jetstream`; broker `2.10.22` from the live broker, equal to the
  binary's; `integrity_mode` `keyed`; 282 stream messages at the sample.
- **c8.** 309 users, 7 sites, 35 teams, 12,964 records, 150 entities, 40 real patterns, 40 Gold discoverable
  (18 rare), 50 decoys (section 3.1's row, asserted); per site 2,574 / 1,620 / 1,789 / 1,876 / 1,805 / 1,842 /
  1,458 records ingested (`forwarded_in = 0`, `mode=ro`), each graph holding exactly that many MESSAGE nodes
  whose ids digest to the record refs; loss 0; `pipeline_raised` false; 0.73 to 0.94 s per 1,000 records to build,
  0.82 over the world (the pipeline and detection 8.2 s).
- **Tesseract routing (c1).** The live `TesseractCoordinator` and `Router` of the coordination package; 55
  questions, each with 7 eligible sites, a route of 3 equal to the first three of arm R's order, and
  `route_selected` before every `retrieval_selected`; 166 rankings over graphs of 1,458 to 2,574 nodes (median
  1,805), 1.37 s p50 and 2.34 s p95 under the shared load; question to verdict 1.40 / 2.43 s per site (168
  requests) and 4.39 / 6.44 s per question. The diagnostics reconciled (section 4, item 2): 163 verdicts from a
  record read (148 confirm, 15 refute), 163 rows for them, 0 rows outside the 166 (question, site) pairs asked, 0
  duplicated; the 166th row is the probe's fresh retrieval at its replacement site.
- **The eligibility probe (M4).** `r00-site-00` (31 of the 55 routes, the most) killed after its 16th question,
  2,574 nodes before and after; the probe re-asked a candidate whose unmasked arm-R route was `r03 / r04 / r00`
  and the Router chose `r03 / r04 / r02`, 7 still eligible, route size 3, one `failure_injected`, one intervention
  record ended, all three answers `site`-sourced (two stored verdicts replayed by the sites that had answered,
  one fresh `unknown` at `r02`).
- **The gate.** 47 supported, 7 hypothesis, 1 stale; 148 confirm, 15 refute, 2 unknown, 0 HQ error records,
  0 `budget` unknowns.
- **The oracle (c1).** 15 planted candidates; 8 unambiguous, 7 expected `supported` and 1 `hypothesis`, and the
  fabric's `metadata.value` equals each (and HQ's status); 7 ambiguous (one strong site, or a contradicted
  pattern), reported, not asserted.
- **Publishing (c1, c4).** 220 publishes (165 verdict events, 55 conclusions), 220 created, 0 duplicate, 0
  ambiguous, 275 API ids; nats-server killed after publish 110 and down 1.7 s; during the outage the outbox held
  138, `connected` false, `status` `degraded`; after the restart 1 reconnect, outbox 0, consumer 0, applied deltas
  220 `agent.event` and 55 `memory.observed` over `result="applied"`, 0 ignored, 55 active notes, 0 stream
  duplicates. `created_to_applied` (M18: 165 verdict `agent.event`s and 55 `memory.observed`s) p50 1.0 s and
  p95 2.0 s at the service's one-second stamps; the conclusions' own `agent.event`s the same.
- **Signatures (c1).** Before any forgery 279 fetched, 279 verified, 0 foreign, every API id exactly once, kinds
  `agent.event` 220, `memory.observed` 55, `rule.upserted` 3, `agent.registered` 1; after the three forgeries 282
  fetched, 279 verified, 3 foreign, the counter 1, 2, 3, the three memory ids 404, 3 `event.rejected` audit rows.
- **Lineage (c2).** 55 conclusions reconstructable with their verdict events among the sources; 165 site-sourced
  verdict events with all five witnesses (165 each in `pd_verdicts`, HQ's wire log, HQ's intake log, the sites'
  egress logs, the sites' stores); 163 joined site-side through `verdict_for_ref` to their records, 0 received
  after `as_of`. The scan read 396,084 bytes of the fabric's database (279 events, 55 memories, 228 audit rows,
  the rest) and 282 stream messages (264,647 bytes): 0 record refs, 0 narrative markers, 0 of the 1,312 narrative
  prefixes.
- **Recovery (c4).** 16 questions before the SIGKILL, 15 after, every post-restart answer byte-identical to a
  fresh `E2ESiteProcess` built from nothing, the whole answer matrix byte-identical to the reference pass (0
  mismatches). The service rebuilt in 2.6 s: the same 55 active memory ids, the same 55 verification digests, the
  replay seeing 282 deliveries and rejecting the 3 forgeries, not blocked, no `ready_block`, `/ready` 200.
- **Idempotency (c3).** Re-ingest: 12,964 duplicates, 0 ingested, 0 rejected, 0 nodes added at any site, the 140
  fixed rankings (20 per site) identical on the top 10 and on the full order. The bridge rerun: 220 publishes, 0
  created, 0 new memory ids, 0 new event ids, the stream unchanged at 285 (the rebuilt service's three
  `rule.upserted`, item 5 of the section above), the notes identical, HQ's store file unchanged.
- **Discovery (c9).** 15 true, 35 decoy, 5 background candidates; 47 supported notes in the fabric with their
  lineage check; 13 Gold-discoverable patterns found at (anchor, chain) granularity: `recall_all` 0.325 (13 / 40),
  `recall_common` 0.591 (13 / 22), `recall_rare` 0 (0 / 18, k-suppression by construction, section 3.4);
  decoys accepted by class 2: 5 of 10, class 4: 9 of 10, class 5: 3 of 10, classes 1 and 3: 0 (class 4 is
  duplicate inflation on a genuine chain and class 2 the temporal scramble the chain-level key cannot see, section
  3.4); 0 background keys supported. The simulator's columns on the same world (`simulated: true`, allocation
  `back-loaded`, 0.9 s): `H_mycelic_full` discovery recall 0.475, rare 0.056, decoy acceptance 0.175, recall at
  100 0.475, 156,338 compute units; `A2_chunked_ctx` 0.925, 0.944, 0.300, 0.925, 167,746. The two are not the
  same metric and the report says so (`granularity`, the simulator's `note`).
- **Guards (c7).** `hq_tesseract`, `hq_bridge`, `publish_api` import nothing forbidden, statically or in a fresh
  interpreter; the drills load no site or service module; the spike's code hash is M13's over 142 files and its
  paths are clean; `code_dirty` false at start and end.
- **Phases (seconds).** world 8.2; reference pass 324.4; main pass 261.7; fixed queries before 258.9; restart
  witness 33.8; fabric start 0.8; publish 4.9; forgeries 0.4; lineage and the scan 0.7; discovery 0.9; rebuild
  3.2; re-ingest 279.9 (the second ranking pass inside it); bridge rerun 2.0. The brief's 20-minute bound for
  the end-to-end job is neither met nor missed by a sandbox figure under a shared load; the first recorded CI run
  decides (section 11).

### Tests (`tests/e2e_ingest/test_harness.py`, as the stage left it)

| Test class | What it proves |
|---|---|
| `OracleTests` (6) | section 3.5 and M20, as the section above says |
| `ScanTests` (3) | drill 5's three patterns and M24's needle scan |
| `CheckTests` (7) | a green report passes every check the end-to-end job runs (c0 to c4, c7, c8 **and c9**); one mutation per assertion (106 of them, a removed field among them) each makes its own check fail and names itself; every assertion has a mutation; the verdict fails when a check that ran is not ok; a run without the suites' junit evidence does not count (M2); c9 accepts null simulator columns with `reason: budget` at the 2,000-user size only; M12's recomputation agrees with a report whose aggregates are its samples' and disagrees with a typed-in percentile, lineage count or recall |
| `GuardTests` (5) | criterion 7 for real, as the section above says |
| `LiveRunTests` (12) | one real run as the section above says, plus: `routing.questions[]` per M4 and `per_question_route_ok`; the diagnostics reconciled with the verdicts (section 4, item 2) and the probe's received pairs; the discovery score from the fabric's notes with the simulator's two columns present, labelled and numeric; the oracle's fabric status equal to HQ's; M2's `pass: false, reason: suites_not_green` with `checks_pass` beside it; the written report recomputes to itself and `check REPORT` reproduces its checks, exits as the stored verdict says, refuses `--recorded` without a CI run id, and catches a typed-in verdict; no tier label of the simulator in the report and the allocation name present. The run asks the first 20 candidates, so c1's `one_unambiguous_supported` may fail here (`SHORT_SCALE`): section 2.3's floor of one unambiguous planted candidate with expected `supported` holds at 55 candidates (the e2e job; the review's run had 7 expected supported and 0 mismatches), and the first 20 by score need not hold one, so that floor is the e2e job's |

Run: `python -m pytest tests/e2e_ingest -q -p no:cacheprovider` with `MYCELIC_NATS_SERVER_BIN` set (a missing
binary fails, M11). Sandbox record of the whole suite (the world, pack, site-graph, publisher, bridge and harness
files; 95 tests) on this stage's code: three full runs. The first two each had one failure in the M24 scan of
`LiveRunTests` and none elsewhere — the needle set first took every allocation's labels, one of which (`lexical`)
is a word of the report's own `granularity` text, and then looked for the allocation name with a space after the
colon that canonical JSON does not write; the scan now reads the labels of the allocation the report ran and the
allocation from the parsed report (94 passed, 1 failed, 545 subtests, 888 s, the harness run sharing the cores with
the trial run; 94 passed, 1 failed, 545 subtests, 868 s). The third run, on the final code: **95 passed, 545
subtests, 0 skipped, 878.6 s**. `flake8 --max-line-length 120` is clean over `research/e2e_ingest` and
`tests/e2e_ingest`;
`tests/mycelic/test_collective_guards.py` on this tree is 82 passed, 1 failed on the brief's own two hits (the
bridge section's item 5), none in a file of this stage.

### Not done here

- `measure.py` holds the simulator's columns only. Criterion 5 (the per-hop numbers at 600, 5,882 and 20,000
  nodes from the 2,000-user world, the rate sweep in its own organisations, the scaling table at 200, 600 and
  2,000 with per-phase reports) is the measure job's and is not built: `c5_measured` stays `not_run`.
- `.github/workflows/e2e-ingest.yml`, `tests/e2e_ingest/test_workflow.py` and criterion 6 (S8); the suites CLI
  of M2 (`python -m research.e2e_ingest suites --out DIR`), `run --suites DIR` and with them criterion 10's junit
  evidence (S8); `__main__.py` (`python -m research.e2e_ingest run|measure|check|suites`); the section-6 settings
  test. Until the suites CLI exists every local run reads `pass: false, reason: suites_not_green`, by M2.
- S9's recorded CI runs and the committed reports under `docs/collective/e2e_ingest/`.
- Criterion 10's guard hit (the bridge section's item 5) was cleared upstream at `65428d9`, merged into this
  build at `3c850d8`; nothing of this stage is hit. The settings test reads the deployment branch's name from
  `mycelic.yml`, never from the brief.
- The local reports of the two trial runs are sandbox artifacts and are not committed; their figures are above and
  they hold no record text, which the suite asserts of its own run's report.
## S7 and S8: `measure.py`, `suites.py`, `__main__.py`, the workflow (branch `wf/e2e-measure`)

The measure job of section 10 (criterion 5 and the scaling table), criterion 10's gate with evidence (M2), the
package's command line of section 6, and the CI workflow of section 11 with its test. This branch starts from the
harness stage's final commit (`99a9d0f`). The commands:

    python -m research.e2e_ingest run --size 200 --mode process --out DIR [--suites DIR]
    python -m research.e2e_ingest measure --sizes 200,600,2000 --out DIR [--suites DIR]
    python -m research.e2e_ingest check REPORT [--recorded]
    python -m research.e2e_ingest suites --out DIR [--group core|lab] [--suite NAME ...] [--verify]

Declaration. Everything here is synthetic and stamped `measurement: false`. In the sandbox (Intel Xeon 2.80 GHz,
4 vCPU, nats-server v2.10.22 at `/root/.local/bin`) this stage ran: one reduced measure job (20 candidates, two
8-second sweep steps, graphs of 600 and 1,200 nodes) to exercise every phase; one measure job at size 200
(`--sizes 200`: the size-200 phase at 60 candidates, the rate sweep and the three measurement graphs; recorded
below); the whole `tests/e2e_ingest` suite; `tests/mycelic/test_packaging.py` and `tests/onboard/test_onboard_demo.py`
(they read workflow files); `actionlint` 1.7.12 over the workflow; `flake8 --max-line-length 120`. Sizes 600 and
2,000 did not run locally. No model ran. No record text or record ref appears in this note, in a log, in a test
message or in a report.

### The shape

| Piece | Where | What |
|---|---|---|
| The measure job | `research/e2e_ingest/measure.py` | `MeasureRun`: M14's phases in order, each under its own budget, the report (`e2e-ingest-measure.json`, `write_json_atomic`) written when each phase ends with `completed` and `phase`; the size phases run the harness as a child process (`harness run --world N --skip-drills --queries 0 --top-n 60|20`), the scaling row taken from the size's own report (`report.scaling_row`); the rate sweep (M7, M19) in a fresh fabric, one organisation and one HQ agent per step, the size-200 events re-keyed `{step}:{cycle}:{original key}`, every event's stamps read from the service database `mode=ro`; the three measurement graphs at 600, 5,882 and 20,000 nodes from the first N records of the 2,000-user world, ranked with the 20 fixed queries; then the scaling table, criterion 5's per-hop block, `c5_measured`, `c10_suites_green` with `--suites`, the verdict. `simulator_columns` (the end-to-end job's, M24) is unchanged |
| The suites CLI | `research/e2e_ingest/suites.py` | M2: the seven suites, each `python -m pytest <suite> -q -p no:warnings [-p no:cacheprovider] --junitxml=DIR/<slug>.xml`; one row per suite (`DIR/<slug>.json`: `{suite, slug, group, sha, tests, failures, errors, skipped, skipped_in_scope, duration_s, junit_sha256, ...}`, `sha` = `GITHUB_SHA` or the tree's commit); every selected suite runs even after a red one; `read_evidence` re-hashes every junit file (`junit_bound`); `--verify [--group]` reads a directory; the floors of M2's table and `E2E_INGEST_FLOOR` (151 at this stage; 157 after the review's fixes, "Fixes after review") |
| Criterion 10 | `report.py` `c10_suites_green`, `suites_green` | per suite: present, `sha == run.code_commit`, no failure, no error, `tests >= floor`, junit-bound; no skip in `tests/e2e_ingest` nor under `tests.mycelic.test_jetstream` (M11); `code_dirty` false at start and end (M10); in CI both `needs` results `success` and the run id set. `verdict.pass` needs it (`suites_green`) |
| Criterion 5 | `report.py` `c5_measured`, the measure constants | every phase completed; every requested size's row numeric and its own checks ok; the three graphs at their node counts with 20 queries; the sweep's first step at 2 events/s, >= 60 s, >= 120 events, apply p95 <= 1.0 s, the rate held, every step's samples its own events; a sustained rate; the runner's CPU named |
| M12 for the measure report | `report.py` `recompute_measure` | each graph's and each step's percentiles from their samples; each size's row against the size report beside the measure report, itself recomputed and its checks reproduced; `check REPORT` dispatches on `run.job` |
| The harness | `harness.py` | `--suites DIR` (the evidence embedded as `run.suites[]`, `run.suites_evidence`, `run.needs_results`; c10 run), `--top-n`, `--queries 0` (no fixed queries), `--size` as the brief spells it; `fixed_questions.json` written beside the report; `run.paths` (the HQ store, the fixed questions); `timings.world_pipeline_seconds` and `world_detection_seconds` (the timing hook in `emergence_world.build_emergence_world`) |
| The CLI | `research/e2e_ingest/__main__.py` | `run`, `measure`, `check`, `suites`; `suites` loads none of the fabric, the sites or the simulator |
| The workflow | `.github/workflows/e2e-ingest.yml` | section 11: on push to the deployment branch with the brief's eight paths and on `workflow_dispatch`; `suites-core` and `suites-lab` (45 min, one step per suite through the CLI, the later steps `if: ${{ !cancelled() }}`, the rows uploaded `if: always()` as `e2e-suites-core` / `e2e-suites-lab`, the job's own `--verify --group` last), `e2e` (needs both, 20 min) and `measure` (needs both, 45 min) download both artifacts into `suites/` and export the `needs` results (`E2E_NEEDS_SUITES_CORE`, `E2E_NEEDS_SUITES_LAB`); the install step is `mycelic.yml`'s with `NATS_VERSION: v2.10.29`; `MYCELIC_NATS_SERVER_BIN` and `E2E_REQUIRE_NATS=1` for every job (M11); each report printed between `=== E2E-INGEST <path> BEGIN lines=N sha256=... ===` and `=== E2E-INGEST <path> END ===` with its sha256 in the step summary (M12); the artifacts `e2e-ingest-report.json` and `e2e-ingest-measure.json` `if: always()` |
| Tests | `tests/e2e_ingest/test_workflow.py`, `test_measure.py`, `test_suites.py`; `test_harness.py` and `helpers.py` updated | below |

No pinned module was changed: nothing under `mycelic/`, `research/routing_spike/`, `research/mycelic/`,
`NeuralGraph/`, `lab/`; every one is imported. `tests/mycelic/test_collective_guards.py` is untouched.

### How the brief's rules are carried out

- **M14's phases and budgets.** `size-200` (5 min, 60 candidates), `rate-sweep` (7), `tesseract-nodes` (5),
  `size-600` (8, 60 candidates), `size-2000` (15, 20 candidates): 40 of the job's 45 minutes with the install's 2.
  A size phase is the harness in a child process with `start_new_session`; past its budget the group gets SIGINT
  (the harness's `finally` blocks close its site processes and bring its fabric down), then SIGKILL after 60 s, and
  the phase is `completed: false, reason: budget`; the later phases still run and the job exits 1. The sweep and
  the graphs check their budget between steps and between queries. The report is written after every phase, and
  after every sweep step and every graph, so a job timeout leaves a partial report with `phase` named.
- **The sizes run the main pass, not the drills.** `--skip-drills --queries 0`: the reference pass, the restart
  witness, the broker kill, the forgeries, the rebuild, the re-ingest and the bridge rerun are the end-to-end
  job's; the 280 fixed queries of criterion 3 are not measurement. What runs per size: the world, the seven (or
  nine) site graphs, the main pass through Tesseract routing with the site kill and M4's probe (one code path), the
  publish into a real keyed fabric, the lineage read-back, the discovery score and the simulator's columns; its
  report carries c0, c7, c8 and c9 of its own, which c5 requires ok.
- **The rate sweep (M7, M19).** The size-200 run's HQ store, read through the bridge's own planner (`HqBridge.plan`,
  `events_for`; nothing is published through it); per step a new organisation `emergence-rate-NN` and its own HQ
  agent `hq-rate-NN`; one thread pacing one POST per event at `t0 + i / rate` through the SDK, no ledger; a
  conclusion cites the verdict event ids the API returned for its cycle; the step ends at the first conclusion
  boundary after 60 s; then idle (`wait_idle`), then `SELECT event_id, kind, status, created_at, applied_at FROM
  events WHERE org_id = ?` from the service database `mode=ro`; a verdict's latency is its `agent.event`'s, a
  conclusion's its `memory.observed` event's (M18), the conclusions' `agent.event`s beside; `n == events` and
  `duplicate == 0` per step. The sweep stops at the first step whose p95 exceeds 1.0 s, whose rate the publisher
  could not hold (below 90 % of the target), or after 32 events/s; `sustained_events_per_second` is the highest held
  rate whose p95 stayed at or under 1.0 s.
- **Tesseract at 600, 5,882 and 20,000 nodes.** `ew.generate(2000, 0)` and `build_records`; for each N a scratch
  site `measure-NNNNN` whose store takes the first N records in record order (each record's `site` set to the
  measurement site, nothing else changed; `EdgeSite.ingest` reports `ingested == N`), the graph synced from that
  store by `site_graph` (`E2ESiteProcess`, the persistent retrieval: `nodes == N`, `build_seconds`), and
  `PersistentTesseractRetrieval.rank` of the 20 fixed queries of the size-200 run (`fixed_questions.json` beside its
  report; a scratch 200-user world otherwise) at their `as_of`; p50 and p95 per N, the samples beside. The graphs
  are never asked a question and no HQ exists for them.
- **M2's evidence.** `suites --out DIR` runs the seven commands and writes the rows; `run --suites DIR` and `measure
  --suites DIR` embed them (`run.suites[]`, `run.suites_evidence = "junit"` only when all seven rows are present
  and bound to their junit files, `run.suites_problems` otherwise) and run c10; `verdict.pass` is `checks_pass and
  suites_green`. In CI the suite jobs run one step per suite through the same CLI, upload `suites/` whole, and the
  `e2e` and `measure` jobs download both artifacts merged into `suites/`. `E2E_SUITES_SHA` is gone; the rows carry
  `GITHUB_SHA`.
- **M11.** The workflow exports `MYCELIC_NATS_SERVER_BIN=/usr/local/bin/nats-server` and `E2E_REQUIRE_NATS=1` for
  every job; `tests/e2e_ingest` never skips for a missing broker (`helpers.nats_server_bin` fails); c10 bounds
  `skipped` at 0 for `tests/e2e_ingest` and for `tests/mycelic/test_jetstream.py` by junit `classname`.
- **M12.** The job log holds each report pretty-printed between the markers, `lines=N` the printed lines and
  `sha256` the artifact file's digest (canonical JSON, one line); the step summary carries the digests of the
  measure report and of each size report. `check REPORT` recomputes a measure report's percentiles from its
  samples and each scaling row from the size report beside it.
- **Section 6's settings test** (`test_workflow.SettingsTests`) parses the brief: the 3.1 table against
  `WORLD_TABLE`; `top_n`, the tie salt, `m`, the 20 fixed queries, the cap and the replay ratio; the floors of 2.3;
  the oracle's thresholds and the forged ids; M21's null window; M13's hash and file count; M10's fourteen paths;
  M2's floors and the `tests/e2e_ingest` count (151, collected in the test; may only rise); M14's phases, budgets,
  candidates and node counts; section 10's rate steps, step length, bound; the report names of section 5.

### Found in the brief while building (nothing lowered)

1. **Agent ids are one namespace across organisations.** Section 3.6 names one organisation per rate step with the
   agent `hq`; the service refuses a second `register_agent` of `hq` (`agent 'hq' already exists`, HTTP 400), so
   each step's organisation registers its own HQ agent, `hq-rate-NN`. The events' ids differ per step anyway (they
   hash the agent and the re-keyed key).
2. **The service's stamps have one-second resolution** (`mycelic/models.py` `now_iso`, `isoformat(timespec=
   "seconds")`), so every `created_at → applied_at` latency is a whole number of seconds and the sweep's p95 is
   read at that resolution; the report says so (`stamp_resolution_seconds: 1`). Criterion 5's bar, p95 <= 1.0 s,
   stands as written.
3. **A rate the publisher could not hold is not sustained.** Section 10's stop rule names the p95 bound and the last
   step; a step whose achieved rate fell under 90 % of its target also ends the sweep (`rate_held`, `stopped:
   rate_not_held`), and `sustained_events_per_second` counts held steps only. The publisher is one thread; the
   achieved rate and the largest pacing lag are in every step.
4. **A site store takes its own records only** (`EdgeSite.ingest` rejects `wrong_site`), so the measurement graph's
   records carry the measurement site's id. The record refs, dates, text and entities are the 2,000-user world's.
5. **The fixed queries of the measurement graphs are the size-200 run's.** Section 3.5's 20 are per world and the
   Tesseract phase runs before the 2,000 size (M14's order); the entities and predicates are the same at every size,
   so the queries are comparable across graphs. The report names the source (`queries_from`).
6. **M2's `suites/<suite>.json` needs a file name.** The suite paths hold slashes; the rows are named by a slug
   (`tests-mycelic.json`, `research-mycelic-test_mycelic.json`), the `suite` field inside is the brief's path.
7. **pytest's junit `tests` counts subtests as testcases** (a probe of 9 tests with 3 subtests reports 12), so a
   row's `tests` is at or above the collected count; the floors are minimums and `--collect-only` is what the
   settings test compares.
8. **Section 11's table lists pytest commands as steps; M2 asks the same commands locally.** The steps call the CLI
   (`suites --out suites --suite NAME`), one step per suite, so the commands exist once (`suites.py`) and the
   local run is the CI run; `test_workflow` asserts each step's command and each suite's pytest line.
9. **The measure job runs on push.** Section 11's table gives it no condition, and criterion 6 allows either; it
   runs with the other three on every push to the deployment branch that touches the brief's paths, and on
   dispatch.

### What the local measure run at size 200 showed (Intel Xeon 2.80 GHz, 4 vCPU, nats-server v2.10.22; sandbox figures, synthetic, `measurement: false`)

`python -m research.e2e_ingest measure --sizes 200 --out DIR --run-id local-200` on the code of WIP `f52bfe0` (the
final commit differs from it by a renamed local in `sweep_step`, see below, and by this note), 888 s of wall time,
nothing else running on the four cores. `measure exit 1`: `verdict.pass` false with reason `suites_not_green` (no
`--suites` directory, M2), `checks_pass` false because `c5_measured` failed two of its 16 assertions
(`every_phase_completed`, `tesseract_20000_nodes`): the third phase ran out of its budget, below. `check REPORT`
re-read the written report and the size report beside it: every aggregate recomputed to itself, the scaling row is
the size report's, the size report's checks reproduce (0 problems), exit 1 as the stored verdict says.

| Phase | Budget | Took | Completed |
|---|---|---|---|
| `size-200` (55 candidates) | 300 s | 270 s | yes |
| `rate-sweep` (5 steps) | 420 s | 313 s | yes |
| `tesseract-nodes` (600, 5,882, 20,000) | 300 s | 304 s | **no** (`budget`: the 20,000-node graph had ranked 16 of its 20 queries) |

- **The size-200 phase** (the harness in a child process, `--skip-drills --queries 0 --top-n 60`, exit 1 for
  `suites_not_green`, its own checks c0, c7, c8 and c9 all ok): 309 users, 7 sites, 12,964 records; generation
  0.4 s, pipeline 5.7 s, detection 0.7 s; 55 snapshot candidates, all 55 asked (the cap of 60 is a bound; c5 asks
  `min(cap, detected)`); seven graphs of 1,458 / 1,805 / 2,574 nodes (min / median / max), built in 7.3 s in all,
  0.56 s per 1,000 records; the main pass 254.8 s: 166 rankings (165 of the pass, one of M4's probe) at 1.32 s p50
  and 2.18 s p95; question to verdict 1.37 / 2.24 s per site and 4.35 / 5.85 s per question; 47 supported, 7
  hypothesis, 1 stale; 148 confirm, 15 refute, 2 unknown, 0 HQ records, 0 budget unknowns; 220 publishes (165
  verdict events, 55 conclusions), 220 created, 279 stream messages; created to applied 0.0 s p50 and p95 at the
  service's one-second stamps; wall 269 s. The size's discovery score and simulator columns are the harness
  stage's figures again (15 true / 35 decoy / 5 background candidates; 13 of 40 Gold-discoverable patterns found,
  `recall_all` 0.325, `recall_common` 0.591, `recall_rare` 0; decoys accepted by class 2: 5, 4: 9, 5: 3; 0
  background keys; the simulator's `H_mycelic_full` 0.475 / 0.056 / 0.175 / 0.475 / 156,338 and `A2_chunked_ctx`
  0.925 / 0.944 / 0.300 / 0.925 / 167,746 in 0.9 s, `simulated: true`). 270 of the 300 s budget: a runner 1.2× slower
  than this sandbox would miss it.
- **The rate sweep** (a fresh fabric, nats-server 2.10.22, `E2EProcessDriver`; the size run's 55 conclusions, 220
  events per cycle, re-keyed per step and cycle; one organisation and one HQ agent per step):

| Rate | Events | Created / duplicate | Cycles | Duration | Achieved | Held | Max pacing lag | Settle | Apply p50 / p95 (s) | n | Applied |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 /s | 124 | 124 / 0 | 1 | 61.5 s | 2.02 /s | yes | 0.05 s | 1.0 s | 0 / 0 | 124 | all |
| 4 /s | 244 | 244 / 0 | 2 | 60.8 s | 4.02 /s | yes | 0.03 s | 1.0 s | 0 / 0 | 244 | all |
| 8 /s | 484 | 484 / 0 | 3 | 60.4 s | 8.02 /s | yes | 0.00 s | 1.1 s | 0 / 0 | 484 | all |
| 16 /s | 964 | 964 / 0 | 5 | 60.2 s | 16.02 /s | yes | 0.03 s | 1.1 s | 0 / 0 | 964 | all |
| 32 /s | 1,864 | 1,864 / 0 | 9 | 60.1 s | 30.99 /s | yes (97 %) | 1.95 s | 4.8 s | 0 / 3 | 1,864 | all |

  Every POST a new event, every event applied, every step's samples its own events (`n == events`); the first
  step at 2 events/s for 61.5 s and 124 events with apply p95 0 s is criterion 5's pass condition on this sandbox;
  the sweep stopped at 32 events/s (`apply_p95_over_bound`, 3 s at the one-second stamps), so
  `throughput.sustained_events_per_second` is 16. The conclusions' own `agent.event`s read the same percentiles.
- **Tesseract at the three node counts** (the 2,000-user world: 1,999 users, 9 sites, 68,463 records, generated and
  mapped in 2.1 s; the size-200 run's 20 fixed questions; a scratch site store per graph, `ingested == N`):

| Nodes | Store ingest | Graph build | Per 1k records | Queries | p50 | p95 | Returned per query |
|---|---|---|---|---|---|---|---|
| 600 | 0.0 s | 0.32 s | 0.53 s | 20 | 0.35 s | 0.45 s | 64 to 66 |
| 5,882 | 0.5 s | 2.23 s | 0.38 s | 20 | 3.67 s | 3.91 s | 74 to 82 |
| 20,000 | 1.9 s | 8.96 s | 0.45 s | 16 of 20 | 12.91 s | 13.97 s | 86 to 94 |

  About 0.6 to 0.65 ms per node per query on this sandbox at every size, 1.5× the 0.42 to 0.44 ms the brief measured
  for M15 on its 2.10 GHz sandbox, inside the 1.5 to 3× the brief gives for runner CPUs. At that cost the phase
  (20 × 0.35 + 20 × 3.7 + 20 × 12.9 s of ranking plus the builds) needs about 350 s, and M14's budget of 5 minutes
  stopped it after the 16th query of the largest graph: `completed: false, reason: budget`, the 16 samples kept,
  `c5_measured` failed, the job's exit 1. The budget is the brief's (M14's table, pinned by the settings test) and
  is not changed here; the first recorded CI run says whether a GitHub runner holds it. The size-200 phase is in
  the same position at 270 of 300 s.
- **The report.** 678,785 bytes (the samples of every ranking, every step's events and every graph query), the
  size report 202,072 bytes beside it; `run.code_dirty_start` false and `code_dirty_end` true (a renamed local in
  `measure.py` made during the run, below); the runner named; `synthetic: true`, `measurement: false`. The sweep
  steps' `agent` field in this report holds a sample instead of the agent id: the loop that reads the conclusions'
  `agent.event` samples shadowed the id's variable, found in the self-review after the run and renamed in the
  final commit (`agent_event`); no figure above depends on it.

### Tests

| Test file | What it proves |
|---|---|
| `test_workflow.py` (`TriggerTests`, `JobTests`, `SuiteJobTests`, `EndToEndJobTests`: 14) | c6: the triggers and the eight paths from the brief; the four jobs in the brief's order with its `needs` and bounds; ubuntu, Python 3.11, the hashed requirements; the install step equal to `mycelic.yml`'s with its `NATS_VERSION`; M11's environment; each suite one step of its job through the CLI, the later steps kept when an earlier one is red, the rows uploaded always and verified last; the CLI's pytest line per suite; the evidence downloaded and the `needs` results exported; the brief's commands; the markers and the digest; the artifact names uploaded always; no expression in a run block but the run id |
| `test_workflow.py` (`SettingsTests`: 13) | section 6's settings test, as above |
| `test_measure.py` (24) | the plan, the budgets (42 of 45), `rekey`, the organisations and agents per step, `parse_sizes`; the phase bookkeeping (completed, overrun, exception); the scaling row; c5 with one mutation per assertion (22) and removed blocks; M12's measure recomputation with the size reports beside; the simulator's columns at 200 users without a tier label; the summary |
| `test_suites.py` (12) | the junit counts and the scoped skips; the CLI on a tiny suite (the row, the binding, a silent interpreter, a suite pytest cannot find); the reader's binding, a tampered junit file, a missing row, `--group`; `--verify`; the `needs` results from the environment; c10 with one mutation per assertion (47) and the three CI-only ones; `suites_green` |
| `test_harness.py` (updated) | the green report carries the suites' rows (M2) and the clean tree (M10); c10's 47 mutations join the per-assertion set; `put` takes list indices; the live run without `--suites` runs every end-to-end check but c10 and reports why |

Run: `python -m pytest tests/e2e_ingest -q -p no:cacheprovider` with `MYCELIC_NATS_SERVER_BIN` set. Sandbox record
of the whole suite on this stage's code, alone on the four cores: **151 passed, 767 subtests, 0 skipped, 888.1 s** (the live run of `test_harness.LiveRunTests` is most of it); `pytest --collect-only` gives 151,
the floor the suites CLI and the settings test carried at this stage (157 after the review's fixes). `tests/mycelic/test_packaging.py` and
`tests/onboard/test_onboard_demo.py` (they read workflow files): 99 passed, 76 subtests, 121.7 s. `actionlint`
1.7.12 (installed from PyPI's `actionlint-py` into the sandbox's scratch directory, not into the tree) passes the
workflow. `flake8 --max-line-length 120` is clean over `research/e2e_ingest` and `tests/e2e_ingest`.
`tests/mycelic/test_collective_guards.py -k NoModelNames` on this tree reports the brief's own two hits and no
others (the bridge section's item 5): nothing of this stage is hit, this section included.

### Not done here

- Sizes 600 and 2,000 did not run locally (the sandbox's four cores and the task's scope: the size-200 phase, the
  sweep and the graphs); their phases are built and run in CI's measure job. The size-2,000 world's generation and
  the 20,000-record store were exercised by the Tesseract phase.
- The seven suites were not all run through the CLI locally (`tests/lab` alone is about 21 minutes); the CLI was
  run on a tiny suite in its tests and on `research/mycelic/test_mycelic.py` through
  `suites --out DIR --suite research/mycelic/test_mycelic.py` (30 tests, 0 failures, 0 errors, 0 skipped, 35 s, the
  row bound to its junit file, exit 0). Until a run is given a `--suites` directory with all seven
  rows, `verdict.pass` reads false with reason `suites_not_green`, by M2; the local measure run below is such a run.
- S9's recorded CI runs (the first run of this workflow on the deployment branch), the committed reports under
  `docs/collective/e2e_ingest/` and the `## Runs` entries. The 20-minute bound of the e2e job and the 45 of the
  measure job are neither met nor missed by a sandbox figure.
- Criterion 10's guard hit (the bridge section's item 5) was cleared upstream at `65428d9`, merged into this
  build at `3c850d8`; nothing of this stage is hit. The settings test reads the deployment branch's name from
  `mycelic.yml`, never from the brief (see "Fixes after review").
- The local reports are sandbox artifacts and are not committed; their figures are above.

## Fixes after review (branch `wf/e2e-fix2`, on top of `wf/e2e-fix`)

The build review of `254dcf2` returned seven blocking and twenty minor findings. The earlier fixer (`wf/e2e-fix`)
merged the deployment branch into the build (`3c850d8`: the brief's no-model-names fix `65428d9` and the compose
job fix) and amended the brief twice in its own commits (`2ffd773`: section 11 has a fifth job, `suites-e2e`, and
`e2e` and `measure` need all three suite jobs; `5920294`: M14 gives the Tesseract phase 8 minutes and the
size-2,000 phase 12, the total still 42 of 45). This branch applies every finding to the code. No pinned module
changed; no test was weakened, skipped or quarantined; no floor was lowered (`tests/e2e_ingest`'s rose from 151 to
157 with the tests added here). Everything is synthetic and stamped `measurement: false`.

### Blocking findings, as applied

| Finding | What changed |
|---|---|
| The settings test read the deployment branch's name from the brief with a regex on a literal ref, which `65428d9` removed | `test_workflow.TriggerTests` reads the branch from `mycelic.yml` (`on.push.branches` less `main`, exactly one) and parses only the paths from section 11; the three build-note passages that called c10 blocked now say the guard was cleared upstream at `65428d9` (in this build since `3c850d8`) and that the settings test never reads the branch from the brief |
| M1's chain never read a verdict event's payload from the fabric: `shas` and the five witnesses used HQ's own `pd_verdicts.sha256` | `harness.lineage` reads each verdict event's stored payload (`fabric_event_payloads`: the `/admin/events` rows when they hold the organisation whole, else the service database `events.payload` `mode=ro`, M7; `lineage.fabric_events_source` says which) and takes `sha256` from the POSTed payload inside it; `lineage.sha256_in_fabric_event` counts `fabric sha == pd_verdicts.sha256` (witness 1 as M1's table defines it), witnesses 2 to 5 are compared with the fabric's value, `lineage.rows[].sha256s` carry the fabric's values (null when the fabric holds none), and c2 asserts `witness_fabric_event` (`== site_sourced_verdict_events`); a bridge publishing a null or wrong `sha256` now fails every witness |
| `--mode in_process` typed `killed = True` and nothing read `run.mode` | `restart_and_probe` records `killed` only from the child process's exit (`False` when no process exists); c4 asserts `main_pass_in_process_mode` (`run.mode == "process"`) and `site_process_sigkilled` (`recovery.site_restart.killed is True`); the green fixture carries both and each has a mutation |
| `E2E_INGEST_TESTS_AT_THIS_BUILD = su.E2E_INGEST_FLOOR` made the floor test a tautology | the literal is written in `test_workflow.py` (157 at this build); the test asserts `E2E_INGEST_FLOOR >= literal`, `collected >= E2E_INGEST_FLOOR` and `collected == literal`, so lowering the floor in `suites.py`, or adding tests without raising the literal, fails here |
| Section 4's leakage scan and closed-shape check of the spike never ran | `harness.crossing_checks` (after the restart witness, before the fabric starts) runs `research.routing_spike.run.leakage_check` and `crossing_check` over the main pass's directory (`dataclasses.replace(world, workdir=dirs["main"])`: the main pass has the spike's layout, HQ's `wire.jsonl`, `intake.jsonl`, `questions.jsonl`, `descriptors.jsonl`, `receive.jsonl` and the sites' ingress, egress and descriptor logs) and `leakage_check` over the pipeline's directory (the cells that crossed); the result is `lineage.crossing = {main: {leakage, crossing}, pipeline: {leakage}}` and c2 asserts `crossing_leakage_zero` (both halves present, 0 overlap bytes, 0 canary hits, items scanned) and `crossing_shapes_ok` (`ok` true, 0 intake refusals); `LiveRunTests` asserts both hold on its run. The measure job's sizes (`--skip-drills`) do not run it: their reports carry c0, c7, c8 and c9 only (c2 is the e2e job's) and the phase stays inside M14's budgets. The scan over the 200-user pipeline directory takes about 2.5 s at 217 MB in the sandbox |
| c10 compared M2's `--collect-only` floors with pytest's junit `tests`, which counts every subtest (`tests/mycelic` about 27 times its floor) | `suites.junit_summary` adds `tests_distinct`, the distinct `(classname, name)` pairs (pytest-subtests reports a subtest under its parent's name: 16 of 16 for `test_measure.py` here, 67 in the `tests` attribute); the row and `helpers.suite_rows` carry it; c10's `tests_at_or_above_floor` holds `tests_distinct` to the floor and reports `tests` beside it; the mutations in `test_suites` and `test_harness` mutate `tests_distinct`; `JunitTests` asserts 4 distinct in the fixture (5 testcases once one is doubled) and `C10Tests` that a suite with many subtests and half its tests fails the floor |
| The tree's guard hit at `E2E-INGEST.md:31` and `:583` | cleared by the earlier fixer's merge of the deployment branch (`3c850d8` brings `65428d9`); `tests/mycelic/test_collective_guards.py` is green on this tree (the run record below) and no file of this build is hit |

### Minor findings, as applied

| Finding | What changed |
|---|---|
| `queries_per_site` was the command-line option | `reingest` measures it from the rankings: `queries_per_site` is the common count across sites (null when they differ), `queries_per_site_by_site` the counts, `queries_expected` the fixed questions asked, and `same_count` also requires every site's count before and after to equal the fixed questions; c3's `twenty_queries_per_site` reads the measured value |
| c1 bound two of M4 check 3's witnesses | c1 adds `probe_masked_site_in_unmasked_route`, `probe_route_size_m` (`== routing.m`), `probe_hq_eligible_all_sites` (`== routing.hq_eligible_expected`), `probe_no_hq_record` (`verdict_sources == ["site"]`), `probe_one_failure_injected` and `probe_intervention_ended`, reading `routing.probe`; the green fixture carries the block and each has a mutation |
| c5 accepted a size report with any checks and never bound the sizes to the brief's | `size_{size}_checks_ok` requires `c0_real_fabric_keyed`, `c7_guards`, `c8_emergence_load` and `c9_benchmark` present and every check ok (`report.MEASURE_SIZE_CHECKS`); c5 adds `sizes_are_the_briefs` (`sizes_requested == [200, 600, 2000]`), so a `measure --sizes 200` report fails c5 and `check REPORT`; mutations in `test_measure.C5Tests` |
| The stamps are whole seconds, so a stamped p95 of 1 did not prove the brief's `<= 1.0 s` | c5 holds the first step's `apply_p95` to the bound the stamps can prove, `MEASURE_APPLY_P95_BOUND - throughput.stamp_resolution_seconds` (0 at the service's one-second stamps; the local run's p95 of 0 at 2 events/s meets it); `per_hop.apply_p95_bound_proven` and `throughput.bound_proven_seconds` record it; c5's values carry `apply_p95_bound`, `stamp_resolution_seconds` and `apply_p95_bound_proven`; the green fixture's step is all-zero samples and a stamped 1 is its mutation. The sweep's own stop rule stays the brief's (`> 1.0 s`); `sustained_events_per_second` is reported under it |
| `records_on_or_before_as_of` dropped refs the site store could not date | `lineage.unknown_refs` counts them and c2 asserts `every_audited_ref_dated` (`== 0`), with a mutation |
| `forged_event_terminated` counted any three `event.rejected` rows | the service's audit row of a terminated event names the stream sequence (`{"reason": "invalid signature", "seq", "subject"}`), never the memory id, so the three forgeries are counted by their own stream sequences (`forged_stream_seqs`, the `stream_seq` each forgery's publish returned): `forged_seq_rejected_audit_rows` and `forged_event_terminated = (== 3)`; `event_rejected_audit_rows` stays the whole count. The finding's match on `FORGED_IDS` inside the detail would match nothing, which is why the sequence is the key |
| `check REPORT` recomputed neither the oracle nor the routing flags | `report.recompute_oracle` rebuilds each oracle row's `SiteFacts` from `per_site_facet_counts` and compares `expected_status(..., as_of)[:2]` with `(expected, ambiguous)` (the oracle lives in `harness.py`, imported late since the harness imports `report`); `report.recompute_routing` recomputes `route_selected_first_all` from every `questions[].trace` (as `hq.py` defines it) and `per_question_route_ok` from `hq_eligible`, `route_size`, `m` and `hq_eligible_expected`; a report without `routing.questions` or without a row's facet counts is a problem; mutations in `CheckTests` |
| `read_evidence` re-hashed the junit file but never re-parsed it | after the hash check the file is re-parsed under the suite's zero-skip scope and the row is bound only when `tests`, `tests_distinct`, `failures`, `errors`, `skipped` and `skipped_in_scope` agree (`suites.JUNIT_COUNTS`); `test_the_reader_binds_every_row_to_its_junit_file` adds a row typed beside a real junit file (each count in turn) |
| `in_ci` came from the environment at check time and `cmd_check` ignored the assertion count | `report._in_ci`: as given, else `bool(run.github_run_id)` from the report itself, the environment only for a document with no `run` block; `cmd_check` also compares each check's `assertions`; `C10Tests` covers the three cases |
| The stream read's bounds and `/admin/status`'s were never shown to agree | `stream_block` records `status_first_seq` and `status_last_seq` beside the read's `first_seq` and `last_seq`; c1 asserts `stream_bounds_agree` |
| `0 or -1` turned a legitimate 0 into -1 | `kinds_and_foreign_are_the_stream` and the witnesses guard on `is not None` instead |
| The report was written once at the end | `E2ERun.checkpoint` writes the partial document after every phase (`phase` named, `completed` false, the timings so far): world, reference_pass, main_pass, fixed_queries, restart_witness, crossing, publish, forgeries, lineage, discovery, rebuild, reingest, bridge_rerun; the finished report carries `phase: "finished", completed: true`; `LiveRunTests` pins the list |
| `publish.created_to_applied` measured drill 3's outage | `publish.created_to_applied_before_kill` holds the samples of the first k publishes (taken by the live broker; `k` in the block), the full block carries a `note` saying it includes the outage, and both are in `report.RECOMPUTE` |
| `suites-core` at its own 45-minute bound | section 11 amended (`2ffd773`); the workflow has the fifth job `suites-e2e` (the brief's order: core, lab, e2e, then `e2e` and `measure` needing all three), `suites.GROUPS`, `NEEDS_JOBS` and `NEEDS_ENV` carry `e2e` / `suites-e2e` / `E2E_NEEDS_SUITES_E2E`, `tests/e2e_ingest` is the `e2e` group, c10 asserts the three `needs` results in CI, and `test_workflow` / `test_suites` follow (`test_the_amendments_of_the_build_review_are_in_the_brief_and_in_the_code` pins both amendments) |
| M14's budgets | amended (`5920294`); `MEASURE_PHASE_BUDGET_MINUTES` is tesseract-nodes 8, size-2000 12 (total 42); `test_measure` pins both |
| `E2E_REQUIRE_NATS=1` was read by no code | `helpers.nats_server_bin` reads it and names it in its failure message; nothing skips either way (M11) |
| The simulator's columns are nested under `architectures` | stated under "Found in the brief" of the S5-continued section (item 7); layout only |
| `SHORT_SCALE`'s exemption did not say whose floor it is | the comment, the `LiveRunTests` docstring and the Tests table say the floor holds at 55 candidates (the e2e job) and the first 20 by score need not hold one |

### What this branch ran
