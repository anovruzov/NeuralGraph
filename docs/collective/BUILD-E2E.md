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
- Criterion 10 is blocked on this tree by item 5 of "Found in the brief" until the brief is amended.
- Nothing is pushed.
