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
