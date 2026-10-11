# End-to-end ingestion E2E: the EMERGENCE world through NeuralGraph, Tesseract, the API, the outbox, JetStream, apply

The fabric's four parts exist and are tested apart; the only chain that joins them is the research routing spike,
which publishes into an in-process fabric (`research/routing_spike/publish.py:53-58`, `nats_url=None`,
`InProcessTransport()`) and runs in no CI workflow (`docs/strategy/LOOP-48H.md:156`). Nothing in `mycelic/collective`
publishes into the fabric's event log (`docs/handoff/HANDOFF-2026-10-09.md:170-171`). This brief fixes what the
end-to-end run is, what it ingests, what counts as passing, and what the report must carry. It is committed alone,
before any code (`LOOP-48H.md:158`, `:171`). It may raise the floors of `LOOP-48H.md` 3.1; it lowers none.

Two instructions from the founder redefine Goal B and are the base of this brief, quoted as received:

- 2026-10-11 02:08 UTC: "Run the entire embedded bench make sure that you're able to run the entire emergence
  benchmark end-to-end ingestion. Literally make sure that each neural graph takes in the messages, ingests it, goes
  into the NATs, and then goes into the actual fabric. Make sure tesseract routing is used. No mistakes allowed.
  End-to-end benchmarking is a requisite."
- 2026-10-11 02:20 UTC: "Run unit tests before running emergence to ensure full Mycelic system gets tested."

What follows from them: the load is the EMERGENCE benchmark's world (`research/mycelic/`, README.md:1-5), not the
device-quality plant of the spike; one NeuralGraph per agent node of that world; Tesseract routing is mandatory; the
fabric is a real keyed JetStream service; the end-to-end numbers and a discovery score against the benchmark's own
ground truth are deliverables; every criterion is a machine check in CI; the "bridge first, Tesseract later" order and
the hour-24 cut rule of `LOOP-48H.md:169` and `:181` are void; and the full unit suites must be green on the exact
commit before any end-to-end run counts. Everything here is synthetic and stamped `measurement: false`.

Plain style, as `docs/collective/L001/CHOICE-L001.md`. Lines under 120 characters except tables. Every claim names
its file and line.

## The declaration

- This brief was written by the AI system that wrote the repository's code, on 2026-10-11, on branch
  `wf/e2e-ingest-design` from `claude/mycelic-production-deployment-r93cez` at `5d646a9`.
- It has read: `LOOP-48H.md` section 3 (`:154-182`); the routing spike (`research/routing_spike/{world,publish,run,
  hq,site_process,wire,score}.py`; `docs/collective/ROUTING-SPIKE.md`); `NeuralGraph/research/retrieval/tesseract.py`
  and its tests; the EMERGENCE generator (`research/mycelic/{org,corpus,runner,experiments,evalm,test_mycelic}.py`,
  `README.md`, `docs/MYCELIC_ENTERPRISE.md`); the fabric (`mycelic/harness.py`, `service.py`, `transport.py`,
  `integrity.py`, `config.py`, `lineage.py`, `sdk/__init__.py`, `tests/mycelic/test_jetstream.py`,
  `tests/smoke/mycelic_smoke.py`); the HQ store (`mycelic/collective/detect/store.py`); `DEPLOYMENT.md:165-169,
  440-446`; `HANDOFF-2026-10-09.md:170-171` and its decisions table (`:361-372`); `.github/workflows/mycelic.yml`.
- It ran nothing end to end. It ran the EMERGENCE generator alone (`build_org`, `build_corpus`, `make_gold`) at seeds
  0 and 1 for five org sizes to size the load (section 3.1). Those are generator counts, not results. No pipeline,
  no detector, no Tesseract, no fabric ran for this brief.
- Nothing in this document is a measurement of any model. The site reader is the shipped lexical judge
  (`mycelic/collective/edge/verify.py:171`), the embedder is the deterministic hash embedder
  (`NeuralGraph/chat_memory/llm.py:201`), and the EMERGENCE simulator's model tiers are assumed capability vectors
  (`research/mycelic/models.py:5`; `docs/MYCELIC_ENTERPRISE.md:411-433`).

## 1. The path

```
 HARNESS  research/e2e_ingest/emergence_world.py
   build_org(target_users, seed) -> build_corpus(org, seed) -> make_gold(corpus)      research/mycelic/org.py:134,
                                                                                     corpus.py:316, :759
   one pack record per EMERGENCE record (section 3.3); one site per org SITE node (section 3.2)
   run_pipeline(pack, records, site_ids, master_data, weeks, workdir)   mycelic/collective/evaluate/baselines.py:116
     per site: EdgeSite.ingest (counts checked, 0 loss), lexical extraction, weekly k-suppressed cells through the
     Boundary; HQ CollectiveStore ingests the receive log
   detect(store, as_of, "X", tie_salt) -> candidates, the first 60                   detect/detectors.py:650

 SITE <id>  research/e2e_ingest/site_graph.py (one per site; a child process in process mode)
   own records (RecordStore, read only) -> persistent SQLite NeuralGraph, one MESSAGE node per own record,
   hash embedding, incremental by the records' seq watermark                 routing_spike/site_process.py:119, :153
   "describe" -> descriptor; "question" -> Boundary.accept -> Tesseract through NeuralGraphMemoryAdapter
   (AsOfStorage) -> lexical judge -> decide -> Boundary.send("out", "verdict")       site_process.py:175-341, :345-392

 WIRE  routing_spike/wire.py: request("describe"), request("question", bytes) -> verdict bytes      wire.py:32, :154

 HQ  research/e2e_ingest/hq_tesseract.py over routing_spike/hq.py
   per candidate: question_window + build_question; HqView signals; arm R's rank fusion;
   TesseractCoordinator.execute(request, phase="R", preferred_node_ids=order, max_nodes=3)        hq.py:180, :414
     -> Router.select -> SiteProxyAdapter.query -> wire -> verdict bytes -> intake -> ClaimEnvelope
   gate.evaluate -> status                                                                          hq.py:433-435
   rows into the HQ store: pd_questions, pd_routes, pd_verdicts, pd_conclusions               detect/store.py:127-136,
                                                                                               :615, :628, :657

 BRIDGE  research/e2e_ingest/hq_bridge.py -> publish_api.py
   read-only reader of pd_verdicts / pd_conclusions by watermark -> one POST /events per event through
   mycelic.sdk.MycelicClient.publish_events                                              sdk/__init__.py:120-121
   -> MycelicService.ingest_events -> outbox -> HMAC-signed publish -> JetStream -> consumer verifies -> apply
                                                     service.py:1203, :1041-1044, :929-931

 FABRIC  mycelic.harness.ProcessDriver: real nats-server, MYCELIC_EVENT_SIGNING_KEY set       harness.py:97-99, :138

 SCORER  research/e2e_ingest/report.py: the fabric (API and lineage), HQ's receive log, the sites' egress and
   verdict logs, the benchmark's Gold -> the JSON report, every check, exit code
```

What leaves a site is exactly what leaves it in the spike: k-suppressed weekly cells, bucketed verdicts and the fixed
descriptor (`ROUTING-SPIKE.md:247-260`). Raw records and graph nodes never cross the Boundary; the fabric receives
verdicts and conclusions only (`LOOP-48H.md:156`).

## 2. Pass conditions

### 2.1 Criteria 0 to 7, verbatim from `LOOP-48H.md:159-166`

0. **Real fabric, keyed.** The service runs under `mycelic.harness.ProcessDriver`, which refuses to start without
   nats-server and sets `MYCELIC_EVENT_SIGNING_KEY` (`mycelic/harness.py:97-99, 111, 138`); the JSON report records
   `keyring.keyed == true`, `transport == jetstream`, the broker version string read from the live nats-server, and
   `stream_messages > 0`. A run that publishes through `InProcessTransport` or an unkeyed service fails criterion 1
   outright.
1. **Signed.** Every `collective.verdict` and `collective.conclusion` event in JetStream verifies under the keyring;
   `events_failed{signature}` is 0 before the forgery drill and exactly 1 after one forged publish straight to NATS,
   whose memory id is absent from the DB. Minimum counts: verdicts ≥ 6 (one per routed site that answered) and
   conclusions ≥ 1, at least one conclusion per planted candidate, and the planted candidate's conclusion status
   equal to the spike key's expected status.
2. **Lineage to the source.** 100% of conclusions (count ≥ 1) have
   `lineage.reconstruct(...)["evidence"]["reconstructable"] == True`; their `source_event_ids` include one verdict
   event per routed site that answered; each verdict sha256 matches HQ's receive log and the site's egress log; a
   site-side audit joins sha256 → `verdict_log` → the `record_ref`s read, all on or before `as_of`; a scan of the
   fabric DB and stream finds 0 record text and 0 `record_ref`.
3. **Idempotent re-ingest.** Re-running site ingest gives the same node ids and count and the same top 10 on ≥ 20
   fixed queries per site; re-running the HQ→fabric bridge leaves `stream_messages` unchanged and creates 0 new
   memory ids.
4. **Recovery.** With N ≥ 50 publishes, nats-server killed after k of them (1 ≤ k ≤ N−10) and restarted: all N
   applied exactly once, `outbox_pending=0`, `consumer_pending=0`. The service SIGKILLed after at least one
   conclusion is applied and its DB deleted: the rebuild gives identical active memory ids and the same verify report
   digest for the conclusions.
5. **Measured numbers** (JSON report uploaded as a CI artifact, with the runner's CPU model): site graph build seconds
   per 1k records; Tesseract per-question p50/p95 at 600, 5,882 and 20,000 nodes; question→verdict p50/p95; event
   `created_at`→`applied_at` p50/p95; sustained events/s until apply p95 exceeds 1.0 s (`DEPLOYMENT.md:165-169,
   440-446`). Pass condition: apply p95 ≤ 1.0 s at a sustained ≥ 2 events/s for ≥ 60 s (≥ 120 events); the rest
   are reported and labelled runner-dependent (lab CPUs vary 1.5-3× between EPYC 7763/9V74/9V45 and Xeon
   8573C/6973P-C).
6. **CI.** A workflow on this branch (push plus `workflow_dispatch`) runs criteria 0-4 and 7 against a real
   nats-server v2.10.29 (`mycelic.yml:16`) in under 20 minutes and uploads the report; criterion 5 runs as a separate
   `workflow_dispatch` job (or a second job in the same workflow) with its own bound of 45 minutes; `mycelic.yml`
   runs on this branch again and includes `pytest tests/routing_spike`.
7. **Guards untouched.** `tests/mycelic/test_collective_guards.py` and every hash-pin test in rule 4 pass unchanged;
   HQ-side and bridge modules import none of numpy, `NeuralGraph.llm_backend`, `NeuralGraph.research.retrieval`,
   enforced like `research/routing_spike/run.py:80-84` (`FORBIDDEN_HQ`, `FORBIDDEN_PUBLISH`).

### 2.2 Criteria 8, 9 and 10, added here

8. **EMERGENCE load, 0 loss.** The demonstration world is the EMERGENCE world of section 3.1. Every agent node (one
   per org site, section 3.2) has its own NeuralGraph, and for every site: `EdgeSite.ingest(...).ingested` equals the
   number of EMERGENCE records whose user's site ancestor is that site (`research/mycelic/org.py:74-81`,
   `ancestor_at(uid, SITE)`), `duplicates == 0`, every `rejected` count is 0, and the site's graph holds exactly that
   many MESSAGE nodes with node ids equal to the records' `record_ref`s. The sums over sites equal `len(corpus.recs)`.
   The world's user, site and record counts equal the table in section 3.1 (the generator is deterministic,
   `research/mycelic/test_mycelic.py:31-34`).
9. **End-to-end benchmark.** The report carries every per-hop number of criterion 5 at the chosen sizes
   (section 10) and the discovery score against the benchmark's Gold (`research/mycelic/corpus.py:746-784`): which
   hidden patterns were recovered at HQ as `supported` conclusions in the fabric, the rare and common splits, decoy
   acceptance by decoy class, and, beside them, the simulator's own numbers for `H_mycelic_full` and `A2_chunked_ctx`
   on the same world (`research/mycelic/runner.py:153-190`, `:193-244`; `evalm.py:101`), labelled synthetic and
   simulated, `measurement: false`. No bar is set on the score. The check is that every field is present and was
   computed by the scorer from the fabric and Gold, never typed in.
10. **Suites green first.** The full unit suites, `python -m pytest NeuralGraph/tests`, `tests/mycelic`,
    `tests/onboard`, `tests/routing_spike`, `tests/lab`, and `python -m unittest research.mycelic.test_mycelic`
    (plus the new `tests/e2e_ingest`), pass on the exact commit of the tree that runs end to end, before it runs. In
    CI the end-to-end jobs `need` the suite jobs (section 11). Locally the loop runs the suites before merging the
    build and before any recorded run. The harness records `code_commit` (`experiments/common.py:107`) and refuses to
    count a run (`verdict.pass == false`, reason `suites_not_green`) unless `E2E_SUITES_SHA` equals that commit and
    `code_dirty` over the paths of section 9 is false. A run on a tree whose suites are not green is not a result.

### 2.3 Floors raised (never lowered)

| Criterion | `LOOP-48H.md` floor | Here |
|---|---|---|
| 1 | verdicts ≥ 6, conclusions ≥ 1 | candidates asked ≥ 20, verdict events ≥ 60 (3 per candidate), conclusion events ≥ 20; ≥ 1 unambiguous planted candidate with expected status `supported` (section 3.5) |
| 3 | ≥ 20 fixed queries per site | exactly 20 per site, the same 20 for every site (section 3.5), compared on top 10 and on the full returned order |
| 4 | N ≥ 50 publishes | N ≥ 80 (one POST per event, section 3.6); k = N // 2 (section 8) |
| 5 | apply p95 ≤ 1.0 s at ≥ 2 events/s for ≥ 60 s | the same bar, and the rate sweep continues past it until p95 exceeds 1.0 s or 32 events/s is reached (section 10) |
| 6 | criteria 0-4 and 7 in under 20 minutes | criteria 0-4, 7, 8 and 9's discovery score in under 20 minutes; criterion 5 and the scaling table in 45; both jobs `need` the suite jobs (criterion 10) |

## 3. The demonstration load

### 3.1 The world: EMERGENCE at the generator's smallest size, with a scaling table

The EMERGENCE org generator grows regions to a floor of 40 users each (`research/mycelic/org.py:165`,
`np.maximum(40, ...)`) over 7 regions (`org.py:136`, `n_regions=7`), so no world it makes at its default shape has
fewer than about 300 users. The generator run in the sandbox on 2026-10-11 (seed 0 unless said; `build_org`,
`build_corpus`, `make_gold`, default `DEFAULT_CFG`, `corpus.py:262-286`) gave:

| `target_users` | users | sites | teams | records | records per site (min / median / max) | real patterns | Gold discoverable | rare | decoys | pattern sites (min / med / max) |
|---|---|---|---|---|---|---|---|---|---|---|
| 200 | 309 | 7 | 35 | 12,964 | 1,458 / 1,805 / 2,574 | 40 | 40 | 18 | 50 | 2 / 3 / 4 |
| 600 | 601 | 7 | 66 | 22,464 | 1,717 / 2,274 / 7,139 | 40 | 40 | 15 | 50 | 2 / 3 / 4 |
| 2,000 | 1,999 | 9 | 213 | 68,463 | 2,036 / 6,539 / 18,633 | 40 | 40 | 9 | 50 | 2 / 4 / 5 |

The 2,000 row is the benchmark's own smallest published scale (`research/mycelic/experiments.py:32`,
`SCALES = (2_000, 10_000, 50_000)`; `docs/MYCELIC_ENTERPRISE.md:577-581`: 1,999 users, 68,463 records, 213 teams,
31 departments, 9 sites). Seed 1 gives the same user counts and 12,911 / 22,283 / 68,514 records.

**Fixed.** The end-to-end job (criteria 0-4, 7, 8, 9) runs `target_users = 200, seed = 0`: 309 users, 7 sites,
12,964 records. The measure job (criterion 5 and the scaling table) runs `target_users` 200, 600 and 2,000 at seed 0,
smallest first, each under its own wall-clock budget (section 10). Why 200 for the end-to-end job: it is the smallest
world the generator makes; per site it holds 1,458 to 2,574 records, which puts every site graph between the 600- and
5,882-node points where Tesseract was measured at 272 ms and 2.6 s per query (`HANDOFF-2026-10-09.md:145`); 60
candidates routed to 3 sites each are 180 rankings, about 2 to 3 minutes at that cost; the whole job is estimated at
12 to 15 minutes (section 11), inside the 20-minute bound with margin for a slower runner. Why 2,000 as the largest:
its biggest site holds 18,633 records, close to the 20,000-node point measured at 8.7 s per query, so 60 candidates
cost up to about 10 minutes of ranking there, inside the 45-minute bound with the two smaller sizes; the 10,000- and
50,000-user worlds (329,730 and 1,639,365 records, `MYCELIC_ENTERPRISE.md:579-581`) would need site graphs of 40,000
to 100,000+ nodes at a flat scan and cannot complete inside any CI bound today.

**What fraction of the benchmark this is.** The end-to-end world is 3.1% of the 10,000-user world's users
(309 / 9,999) and 3.9% of its records (12,964 / 329,730); 0.6% and 0.8% of the 50,000-user world's. The largest
measured size is the published benchmark's smallest scale, in full (1,999 users, 68,463 records), and 20.0% / 20.8%
of the 10,000-user world. The report states these fractions from its own counts (`world.fraction_of_benchmark`).

### 3.2 Node granularity: one NeuralGraph per org site

"Each neural graph takes in the messages" is read as one NeuralGraph store per agent node of the world, and the agent
node is the org SITE (level 3 of `org.py:3-4`, `SITE = 3`, `org.py:21`). Reasons:

1. The site is the collective's Boundary unit: `EdgeSite` (`mycelic/collective/edge/site.py:230`), the record store
   (`edge/records.py:1-3`, one SQLite file per site), k-suppression, the verdict path and the spike's site process
   (`site_process.py:1-2`) are all per site. The shipped pipeline, the leakage scan and the gate run unchanged.
2. EMERGENCE partitions its entity namespace by site (`corpus.py:337-356`): "the same entity showing up in three
   regions" is an anomaly at the site level, which is the level at which cross-site routing is informative at all.
   Echoes also circulate within a site (`corpus.py:423-447`).
3. A graph per user (median 28 records, `corpus.py:263`) or per team (38 to 1,056 records at 309 users, measured
   above) would put almost every weekly cell under k = 3 and route among 35 to 309 near-empty graphs; it would test
   the fabric's agent count, not ingestion through Tesseract. A per-team variant is not in scope and is not claimed.

Site ids are the org node names lower-cased (`org.py:181`, `R{ri:02d}-SITE-{si:02d}` → `r00-site-00`), which match
the site id pattern `[a-z0-9][a-z0-9_.-]{0,63}` (`routing_spike/wire.py:37`). Each record's site is
`org.ancestor_at(uid, SITE)` (`org.py:74-81`). The count check of criterion 8 is per site and in total.

### 3.3 Mapping EMERGENCE records onto the pack record interface

The pipeline ingests record dicts with the keys of `mycelic/collective/packs/connector.py:28` (`RECORD_KEYS`), checked
by `record_problems` (`connector.py:84`) inside `EdgeSite.ingest` (`site.py:278-299`). No raw-row mapping file is
used; the adapter (`research/e2e_ingest/emergence_world.py`) builds the dicts from the corpus rows
(`corpus.py:151-157`, `REC_DTYPE`) and `run_pipeline` feeds them as `baselines.py:136` already does.

| Record key | Value | Source |
|---|---|---|
| `record_ref` | `em{seed}-{rid:07d}` (rid = row index in `corpus.recs`) | 1..128 printable characters, `connector.py:93-95` |
| `site` | lower-cased name of `org.ancestor_at(uid, SITE)` | `org.py:74-81`, `:181` |
| `received_date` | `2024-01-01 + t days` (ISO), t = the row's day index 0..179 | `corpus.py:267` (`n_days` 180), `:366` |
| `language` | `"en"` | the surface text is English, `corpus.py:91-134` |
| `codes` | `[]` | EMERGENCE's content is reachable only through surface text (`corpus.py:6-10`); the codes channel stays empty |
| `entities` | `{"entity": [canonical(anchor name)]}` | `corpus.py:212` (the anchor is a structured field of the tuple; the aux entity is not) |
| `narrative` | `corpus.text(rid)` exactly | `corpus.py:227-230`: `[d042] <user node name> :: <surface tokens>` |
| `persons` | `{}` | none in the benchmark |
| `reporter` | `u{uid}` | one reporter per user; `n_reporters` and the gate's reporter floor count users |
| `origin_ref`, `origin_site` | `None`, `None` | echoes are other users' records; independence is a text problem by design, `corpus.py:196-205`; the site store folds equal narratives to one root, `records.py:25-27` |
| `synthetic` | `True` | every record of this world is synthetic |

Weeks: 26 ISO weeks from 2024-01-01 (`world_weeks("2024-01-01", 26)`, `baselines.py:85-87`), covering days 0 to 179
(2024-01-01 to 2024-06-28); the pipeline's `as_of` is `closing_date(pack, "2024-W26")` (`baselines.py:90-92`),
14 days after that week's Sunday. The pack's `close_lag_days` is 14 (section 3.4).

Master data: every site's master data is the whole entity list, `{"entity": [all canonical ids]}` (150 ids at every
size measured, `corpus.py:327-329`). A question about any entity is answerable at any site, so no site's
`in_master_data` answer (`edge/site.py:138`; `site_process.py:370`) tells HQ which entities a site holds (the
membership oracle of audit r2 F4 is not re-opened).

### 3.4 The EMERGENCE pack

The pipeline, the detectors, the question templates and the gate need a pack (`mycelic/collective/packs/loader.py:
69-71`, `FILES`). The adapter writes one, `emergence`, into the run directory from the benchmark's own constants
(`research/e2e_ingest/emergence_pack.py`), loads it with `load_pack_dir` (`loader.py:1522`; `load_pack` accepts a
directory path, `loader.py:1569-1576`, which is what `SiteConfig.pack` carries into every site process,
`site_process.py:402`), and records `FrozenPack.hashes()` (`loader.py:404`) in the report. The pack for the
end-to-end world is also committed at `research/e2e_ingest/pack/emergence/` with a test that regenerating it is
byte-identical. Nothing under `mycelic/collective/packs/` changes.

**The predicate is the causal chain.** An EMERGENCE strategic pattern is a contiguous path of 3 to 5 distinct
predicates of one causal chain on one anchor entity, and each facet's users are drawn from one region
(`corpus.py:556-558`: `pick_users(k, [regs[j % len(regs)]])`), the regions rotating across facets so that a pattern
spans at least 2 regions (`corpus.py:549`). At every size measured, regions and sites coincide or nearly so (7 regions,
7 to 9 sites), so a single (anchor, predicate) facet sits at one site. The collective's detectors raise a key only when
it rises at `min_sites` ≥ 2 sites (`loader.py:557`; `device_quality/detectors.json` `min_sites` 2), and the gate
confirms only with `min_confirming_sites` sites (`questions.json` pushdown block). At predicate granularity, therefore,
no real EMERGENCE pattern can ever become a candidate or a supported conclusion, by construction, while decoys, whose
facet users rotate across regions per user (`corpus.py:651`, `pick_users(k, regs)`), can. The pack's predicates are
the 7 chains of `CAUSAL_CHAINS` (`corpus.py:51-66`), each with the lexicon of all its predicates' surface phrases
(`PRED_SURFACE`, `corpus.py:91-126`), plus `revenue_miss` on its own, since it ends two chains (`corpus.py:53`, `:55`)
and a phrase maps to one predicate. A key is then (anchor, chain), which a real pattern raises at 2 or more sites and
which the gate can confirm across sites. Two consequences are stated now and counted in the report: the gate checks no
temporal order, so a D2 temporal-scramble decoy (`corpus.py:624-628`) on an otherwise real-looking chain is accepted
by construction; and the rare patterns (1 to 2 users per facet, `corpus.py:281`) fall under k = 3 and are invisible
to the cells by construction. The background predicates (`corpus.py:69-74`) get no pack predicate: such records are
graph nodes for Tesseract and count in no cell.

| File | Content |
|---|---|
| `pack.json` | id `emergence`, version `0.1.0`, languages `["en"]`, `illustrative: true`, `same_author_as_code: true`, a disclaimer naming `research/mycelic/corpus.py` as the source of every term |
| `vocabulary.json` | entity type `entity`: alias-only (`ids` = the 150 canonical ids, upper-cased names, pattern `loader.py:61`; `id_format` null, `loader.py:762-763`), `egress: true`; predicates: 7 chain predicates `chain_supply`, `chain_auth`, `chain_policy`, `chain_attrition`, `chain_firmware`, `chain_pricing`, `chain_network` (lexicon `en` = the chain's `PRED_SURFACE` phrases, `revenue_miss`'s excluded) and `revenue_miss`; negation `en: ["not"]` (polarity −1 appends `not`, `corpus.py:223-224`), window 8; extraction block copied from `device_quality` |
| `aliases.json` | `entity`: every EMERGENCE entity name (`corpus.py:289-302`, `_mk_entities`) → its canonical id |
| `codes.json` | one placeholder code per predicate (`EM-01`..`EM-08`), attached to no record; required by the schema, never used |
| `mapping.json` | identity paths for the record keys; `primary_entity_type: entity`; unused (records arrive normalised) |
| `egress.json` | `k` 3, `suppress_below_days` 31, `count_granularity` week, `close_lag_days` 14, `min_window_weeks` 6, `verdict_count_buckets` [3, 10, 50], `egress_entity_types` ["entity"], `require_master_data` true, `never_fields` ["narrative", "reporter"], `central_allowed_fields` ["codes", "entities.entity", "received_date", "site"], `verify_max_records` 500, `question_budget_per_entity_per_day` 5, `question_entities_per_site_per_day` 200 (bounds `loader.py:542-543`; 200 so that 60 candidates sharing one `as_of` never hit the per-day entity budget, which would turn answers into `budget` unknowns) |
| `detectors.json` | `alert_budget_per_week` 20, `cooldown_weeks` 2, `baseline_weeks` 8, `window_weeks` 6, `min_history_weeks` 6 (bounds `loader.py:554-555`; a 26-week world cannot carry `device_quality`'s 26-week baseline and 12-week history), `burst`, `cooccurrence`, `resolution`, `independence`, `decoy` (`stale_days` 42) and `ranker` copied from `device_quality/detectors.json`. These are the adapter's settings for a synthetic 26-week world; they decide nothing about the pilot's per-site-count settings (`HANDOFF-2026-10-09.md:266-271`) |
| `rules.json` | one rule per chain predicate: `entity_type` entity, `min_sites` 2, `min_count_per_site` 1, `window_weeks` 8. Rule hits are not routed: the spike routes snapshot candidates only (`hq.py:79-84`) |
| `questions.json` | template `count_predicate_on_entity` for `["entity"]`, predicates null; pushdown `min_confirming_sites` 2, `min_independent_roots` 3, `min_independent_reporters` 3, `freshness_days` 42, `max_sibling_sites` 2 (copied from `device_quality`) |
| `followups.json` | copied from `mycelic/collective/onboard/data/template/followups.json`; the follow-up layer is not run |
| `generator.json` | start `2024-01-01`; the world's site ids as sites, language `en`; required by the schema, never used to generate a record (the records are EMERGENCE's) |
| `fixtures/records.jsonl` | the first 5 records of the world, as examples (required: `loader.py:678-681`) |

The lexical judge then reads a facet record as "yes" for its chain and entity when the surface phrase and an alias of
the anchor are present (`edge/verify.py:171`), which is how every facet record is written (`corpus.py:206-225`: the
phrase, the anchor, optionally the aux entity, filler). The aux entity in a narrative may be read as a second mention;
the simulator's own extraction pays entity errors too (`ops.py:138`; `test_mycelic.py:162`). It is reported, not
corrected.

### 3.5 HQ's questions, the candidates, N, k and the fixed queries

HQ's questions come from the shipped detectors on HQ's own cells, as the spike's do (`hq.py:4-7`): the X run at the
pipeline's `as_of` (`detect/detectors.py:650`), candidates with a snapshot, ordered by (score descending,
`sha256(tie_salt|key)`), the first `top_n = 60` (`routing_spike/world.py:49`, `:120-124`), tie salt `e2e-ingest-v1`.
One question per candidate at its snapshot's `as_of` (`hq.py:184-186`). This follows the spike's shape and decides
nothing about decision 2 (what Tesseract routes when nobody asks; `HANDOFF-2026-10-09.md:366`).

- **Floors.** Candidates asked ≥ 20. Fewer fails criterion 1 and the run is recorded as failed; the next recorded run
  uses the next size of the table in 3.1 (600), declared here, never a changed setting.
- **Planted candidates.** A routed candidate is *planted* when its key equals a Gold-discoverable pattern's
  (canonical anchor, chain predicate) and its snapshot week lies in that pattern's window
  `[iso_week(start + min(facet_times)), iso_week(start + max(facet_times)) + 4 weeks]` (`corpus.py:169`,
  `facet_times`; grace 4 weeks as the spike, `world.py:42`). Its *expected status* is computed by the harness from
  Gold and the pack thresholds alone, after the route is known: `supported` when at least 2 routed sites each hold
  at least 3 of the pattern's chain facet records in the question window, from at least 3 distinct users and 3
  distinct events (`corpus.py:566`, one event per record), none with polarity −1, and the newest within 42 days of
  `as_of`; `hypothesis` when no routed site holds 3 such records; otherwise the candidate is *ambiguous* (one strong
  site, or a contradicted pattern, `corpus.py:577-595`) and is reported with both values, not asserted. Criterion 1's
  check: every unambiguous planted candidate's fabric status equals its expected status, and at least one unambiguous
  planted candidate has expected status `supported`.
- **N and k.** One publish is one `POST /events` carrying one event (section 3.6). N is the number of publishes of
  the main pass: one per `pd_verdicts` row plus one per `pd_conclusions` row, so N ≥ 4 × candidates ≥ 80. The
  broker is killed after publish k = N // 2 (1 ≤ k ≤ N − 10 holds for N ≥ 20) and restarted after publish N.
- **The 20 fixed queries per site.** `query_text(pack, q)` (`site_process.py:125-128`) for the first 20 questions in
  (`as_of`, key) order of the candidate list; the same 20 at every site. Re-ingest (criterion 3) compares, per site,
  node ids and count, and the top 10 and the full returned order of each query before and after.
- **Budget m = 3** sites per question (`world.py:57`), the spike's. Arm R only (`hq.py:146-147`, rank fusion of
  `key_now`, `supporting`, `entity_span`, `type_span`); no U, A, O or P arm runs here.

### 3.6 The fabric layout

One organisation, `emergence`, one agent, `hq`, registered through the admin API
(`MycelicClient.register_agent`, `sdk/__init__.py:192-197`; `service.py:2006`). The service runs under a
`ProcessDriver` subclass whose `env()` adds `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG=5000` (the shipped cap,
`DEPLOYMENT.md:165-169`; default 0, `config.py:116`, `:182`) and leaves every other setting as `harness.py:132-142`
sets it (rules file `deploy/mycelic/rules.json`, `min_support` 2, rate limit 1000 rps).

Per conclusion the bridge publishes, in order, one `collective.verdict` event per `pd_verdicts` row of the question
(payload as `publish.py:80-87`: site, question id, source, verdict id, verdict, reason, the three buckets, newest
week, window, the sha256 of the verdict bytes as HQ received them; no memory) and then one `collective.conclusion`
event with its embedded memory (`publish.py:91-102`: `entity`, `slot` = the chain predicate, `value` = the gate
status, `topic` `collective/emergence`, `kind` `risk`, `visibility` `team`, `source_event_ids` = the verdict event
ids the API returned, metadata with entity type, candidate key, question id, conclusion id, `as_of`, snapshot week,
route label `R` and routed sites; text = `conclusion_text`). Each event is its own `POST /events` (`max_batch` is
100, `config.py:104`; one per request keeps N a count of API calls and the per-event `created_at`→`applied_at` clean).
Active notes in `emergence` = conclusions ≤ 60, far under the 5,000 cap; the main pass publishes at the bridge's pace
(no throttle) and the measure job's rate sweep uses one organisation per rate step (`emergence-rate-02`, `-04`, ...)
so no step's notes mix with another's. Only verdicts and conclusions are published; never a record, a node or a cell.

## 4. Tesseract routing, mandatory

"Tesseract routing" in this repository is two things and both are required and checked:

1. **At HQ**, `TesseractCoordinator.execute` with `Router.select` over the registry of `SiteProxyAdapter`s, the arm's
   order as `preferred_node_ids` and `budget.max_nodes = m` (`hq.py:379-380`, `:414-424`;
   `NeuralGraph/research/coordination`). Check: for every question the trace has `route_selected` before any
   `retrieval_selected` event (`hq.py:425-429`, `route_selected_first`), the route has exactly `min(3, eligible)`
   sites, and the routed sites are the first 3 of arm R's order (`hq.py:180-195`, `plan`).
2. **At every site**, retrieval through `NeuralGraphMemoryAdapter` over the site's NeuralGraph with
   `NeuralGraph.research.retrieval.tesseract.Tesseract` (`site_process.py:189-221`, `:257-296`), `AsOfStorage`
   bounding the nodes to those received on or before the question's `as_of` (`site_process.py:153-172`), the union
   with the shipped retrieval at cap L = 50 (`site_process.py:282-296`). Check: the site diagnostics
   (`site-<id>.tesseract.jsonl`, `site_process.py:330-335`) hold one row per (question, site) whose verdict came from
   a record read (verdict `confirm` or `refute`, or `unknown` with a record read), with `seconds`, `nodes`,
   `nodes_as_of`, `returned`, `read`; the count of rows equals the count of such verdicts.

HQ never sees a node, a record, a Tesseract score or a label: the import check of criterion 7 and the leakage scan of
the spike (`run.py:181-202`) run over every crossing byte of this world.

## 5. The JSON report

Written by `research/e2e_ingest/report.py` with `write_json_atomic` (`experiments/common.py:57`), canonical JSON,
uploaded as the CI artifact `e2e-ingest-report.json` (end-to-end job) and `e2e-ingest-measure.json` (measure job).
Every number is computed by the harness from its own logs, the fabric's API and DB, the sites' stores and Gold;
nothing is typed in. Durations are seconds (float), counts integers, percentiles by `mycelic.collective.stats.
percentile` (as `routing_spike/run.py:377`).

| Path | Type, unit | Source |
|---|---|---|
| `kind`, `schema_version` | `"e2e_ingest_report"`, 1 | constant |
| `synthetic`, `measurement` | `true`, `false` | constant |
| `run.run_id`, `run.job` | string; `"e2e"` or `"measure"` | CLI |
| `run.started_at`, `run.finished_at` | ISO 8601 UTC | `utc_clock`, `common.py:70` |
| `run.code_commit`, `run.code_dirty`, `run.spike_code_hash` | 40 hex; bool; 64 hex | `common.py:107`, `:113`; `run.py:91` |
| `run.suites_sha`, `run.suites_green` | 40 hex or null; bool | `E2E_SUITES_SHA` env; equals `code_commit` |
| `run.github_run_id`, `run.command` | string or null | `GITHUB_RUN_ID`; `sys.argv` |
| `runner.cpu_model`, `runner.cpu_count`, `runner.python`, `runner.os` | strings, int | `/proc/cpuinfo` `model name`, `os.cpu_count()`, `sys.version`, `platform.platform()` |
| `fabric.keyring.keyed`, `fabric.keyring.key_id` | bool; string | `Keyring(driver.signing_key)` (`integrity.py:132-139`; `harness.py:111`, `:138`) |
| `fabric.transport` | `"nats-jetstream"` | `/status` `checks.transport.transport` (`transport.py:220`, `:464-466`) |
| `fabric.broker_version` | string, e.g. `2.10.29` | a harness nats-py connection to the driver's broker, `Client.connected_server_version` (`nats/aio/client.py:1415-1422`) |
| `fabric.broker_binary_version` | string | `nats-server --version` of `driver.nats_bin` (`harness.py:97`) |
| `fabric.stream_messages`, `fabric.consumer_pending`, `fabric.outbox_pending` | int | `/status` `checks.transport.stream_messages`, `.consumer_pending`, `checks.publisher.outbox_pending` (`service.py:2496-2506`; `transport.py:473`, `:478`) |
| `fabric.service_version`, `fabric.org_id`, `fabric.agent_id`, `fabric.active_notes`, `fabric.max_active_memories_per_org` | string; `emergence`; `hq`; int; 5000 | `/status` `version`; settings; `list_memories` |
| `world.benchmark`, `world.target_users`, `world.seed`, `world.users`, `world.sites`, `world.teams`, `world.records`, `world.n_days`, `world.weeks`, `world.patterns_real`, `world.gold_discoverable`, `world.gold_rare`, `world.decoys`, `world.entities` | strings, ints | `org.py`, `corpus.py`, `make_gold` |
| `world.node_granularity`, `world.site_ids` | `"site"`; list | section 3.2 |
| `world.pack_id`, `world.pack_hashes` | `emergence`; `FrozenPack.hashes()` | `loader.py:404` |
| `world.fraction_of_benchmark.{users_vs_10k, records_vs_10k, users_vs_50k, records_vs_50k}` | float | `MYCELIC_ENTERPRISE.md:579-581` denominators, the world's own numerators |
| `ingest.per_site.<site>.{expected_records, ingested, duplicates, rejected, graph_nodes, graph_build_seconds, seconds_per_1k_records}` | ints; dict; floats | `EdgeSite.ingest` (`site.py:278-299`), `site_graph` |
| `ingest.total_expected`, `ingest.total_ingested`, `ingest.loss` | ints; 0 | sums |
| `routing.candidates_detected`, `routing.candidates_asked`, `routing.m`, `routing.route_label`, `routing.questions` | ints; 3; `"R"`; int | `detect`, the harness |
| `routing.route_selected_first_all`, `routing.routes_equal_arm_order` | bool | `hq.py:425-429`; `plan` |
| `routing.tesseract.{rankings, nodes_min, nodes_median, nodes_max, seconds_p50, seconds_p95}` | ints, floats | site diagnostics |
| `routing.question_to_verdict.{per_site_p50, per_site_p95, per_question_p50, per_question_p95, n}` | floats, int | a timing wrapper around each wire endpoint's `request` (harness side) and around `hq.ask` |
| `routing.verdicts.{confirm, refute, unknown, hq_error, budget_unknown}` | ints | HQ intake |
| `publish.{publishes, verdict_events, conclusion_events, events_created, events_duplicate}` | ints | the API's `results[].created` (`service.py:1244`) |
| `publish.created_to_applied.{p50, p95, n}` | floats, int | `GET /admin/events?org=emergence` (`sdk/__init__.py:226-228`) `created_at`, `applied_at`; where the view omits a field, the service DB opened `mode=ro` (`store.py:100`, `:505-507`) |
| `signatures.{failed_before_forgery, failed_after_forgery, forged_memory_absent, forged_event_terminated}` | 0; 1; bool; bool | `/metrics` `mycelic_events_failed_total{stage="signature"}` (`metrics.py:35`); `GET /memory/<forged>` 404 |
| `lineage.{conclusions, reconstructable, source_events_complete, sha256_in_receive_log, sha256_in_egress_log, site_audit_joined, records_on_or_before_as_of}` | ints; ints; bool | `GET /lineage/<id>` `evidence.reconstructable` (`service.py:1751`, `lineage.py:139`, `:177`); `score.log_index`, `logged_match` (`score.py:46`, `:60`); `RecordStore.verdict_for_ref` (`records.py:518`), `VerdictRow.confirming_refs`, `.entity_refs` (`records.py:268-287`) |
| `lineage.scan.{record_ref_hits, narrative_marker_hits, narrative_prefix_hits, db_bytes, stream_messages_scanned}` | ints | section 8 |
| `idempotency.site_reingest.{same_node_ids, same_count, queries_per_site, same_top10, same_order}` | bools; 20 | section 8 |
| `idempotency.bridge_rerun.{stream_messages_before, stream_messages_after, new_memory_ids, new_event_ids}` | ints; 0; 0 | `/status`; the API's `created` flags |
| `recovery.broker_kill.{N, k, applied_exactly_once, outbox_pending, consumer_pending, broker_down_seconds}` | ints; bool; 0; 0; float | section 8 |
| `recovery.service_rebuild.{conclusions_applied_before_kill, active_memory_ids_identical, verify_digest_identical, rebuild_seconds}` | int; bools; float | `GET /verify/<id>` `report_digest` (`verification.py:715`, `:735`) |
| `recovery.site_restart.{site, questions_before, questions_after, verdict_bytes_identical}` | string; ints; bool | section 8 |
| `discovery.*` | section 10 | the scorer and `evalm` |
| `throughput.*` | section 10, measure job only | the rate sweep |
| `checks.<name>.{ok, ...}` | bool plus the values compared | section 9 |
| `verdict.pass`, `verdict.failed` | bool; list of check names | all checks `ok` |

## 6. Module layout

New code lives in `research/e2e_ingest/` and `tests/e2e_ingest/`. Every pinned module is imported, never edited
(`LOOP-48H.md:85`; `run.py:73-75`, `CODE_PATTERNS`). The pack data of section 3.4 is new data.

| Module | Side | Imports from pinned modules | Must not import |
|---|---|---|---|
| `emergence_world.py` | harness | `research.mycelic.org` (`build_org`, `SITE`), `research.mycelic.corpus` (`build_corpus`, `make_gold`, `CAUSAL_CHAINS`, `PRED_SURFACE`), `mycelic.collective.evaluate.baselines` (`run_pipeline`, `world_weeks`, `closing_date`), `mycelic.collective.detect.detectors` (`detect`), `mycelic.collective.packs.loader` (`load_pack_dir`), `research.routing_spike.world` (`World`, `backup_sqlite`, `restore_store`), `research.routing_spike.site_process` (`SiteConfig`), `research.routing_spike.hq` (`Candidate`), `mycelic.collective.jsonio` | — |
| `emergence_pack.py` | harness | `research.mycelic.corpus` constants; `mycelic.collective.packs.loader` (`load_pack_dir`, the self-check) | — |
| `site_graph.py` | site | `research.routing_spike.site_process` (`SiteProcess`, `TesseractRetrieval`, `AsOfStorage`, `node_text`, `query_text`, `SiteConfig`, `read_config`), `research.routing_spike.wire` (`serve_line`), `mycelic.collective.edge.records` (`RecordStore`), `NeuralGraph.research.retrieval.sqlite_storage`, `NeuralGraph.chat_memory.llm` (`fake_embedding`) | — (site side; loads numpy and the retrieval package by design) |
| `hq_tesseract.py` | HQ | `research.routing_spike.hq` (`Hq`, `Candidate`, `Asked`), `research.routing_spike.wire`, `mycelic.collective.detect.store` (`CollectiveStore`, `QuestionRow`, `RouteRow`, `ConclusionRow`), `mycelic.collective.pushdown.questions` (`render_text`), `mycelic.collective.jsonio` | `FORBIDDEN_HQ` (`run.py:80-83`) plus `research.e2e_ingest.site_graph` |
| `hq_bridge.py` | bridge | `mycelic.collective.detect.store` (the row types; its own `mode=ro` connection as `HqReader` opens one, `store.py:678-684`), `research.e2e_ingest.publish_api` | `FORBIDDEN_HQ` plus `site_graph` |
| `publish_api.py` | bridge | `mycelic.sdk` (`MycelicClient`, `MycelicError`; standard library only, `sdk/__init__.py:22-32`) | `FORBIDDEN_HQ` plus `site_graph` plus `mycelic.service` (which loads numpy through `NeuralGraph.chat_memory`, `publish.py:16-18`). It declares the event types and `conclusion_text` itself; a test asserts equality with `research.routing_spike.publish` (`VERDICT_EVENT`, `CONCLUSION_EVENT`, `conclusion_text`, `publish.py:30-31`, `:43-47`) |
| `harness.py` | harness | `mycelic.harness` (`ProcessDriver`), `research.routing_spike.run` (`imports_loaded`, `static_imports`, `leakage_check`, `crossing_check`, `open_endpoints`, `close_endpoints`), `research.routing_spike.score` (`log_index`, `logged_match`), the modules above | — |
| `drills.py` | harness | `mycelic.harness`, `mycelic.transport` (`subject_for`, `transport.py:41-43`), `nats` (the forged publish, the stream dump, the broker version) | — |
| `measure.py` | harness | `research.mycelic.runner` (`build_world`, `run_arch`), `research.mycelic.models` (`allocation`), `research.mycelic.evalm` (`evaluate`), `site_graph` | `research.mycelic.runner.run_matrix` (it appends to the committed artifacts, `runner.py:253-254`; README.md:72-79) |
| `report.py` | harness | `mycelic.collective.experiments.common` (`code_commit`, `code_dirty`, `write_json_atomic`, `utc_clock`), `mycelic.collective.stats` (`percentile`) | — |
| `__main__.py` | CLI | `python -m research.e2e_ingest run --out DIR [--size 200] [--mode process]`, `measure --out DIR`, `check REPORT` | — |
| `tests/e2e_ingest/` | tests | pack regeneration and loader check; record mapping and counts; `site_graph` re-ingest, crash and resume, `as_of` bound; `publish_api` idempotency against `ProcessDriver` (skipped only when no nats-server binary is found, as `test_jetstream.py:7`, `:32`); bridge watermark and rerun; the import guards of criterion 7; report schema; a settings test comparing the harness constants with this brief's tables | — |

What `site_graph.py` changes against the spike: `TesseractRetrieval._build` deletes and rebuilds the graph on every
start (`site_process.py:205-207`, `:244-255`). `site_graph.PersistentTesseractRetrieval` keeps the SQLite graph,
reads the site store's `records` table by `seq` past a stored watermark (`site_process.py:238-239` is the read;
`records.py:20-27` the ingest rules), upserts one MESSAGE node per own record keyed on `record_ref` with `node_text`
(`site_process.py:119-122`), never deletes a node, and records `build_seconds` and the node count. `E2ESiteProcess`
is `SiteProcess` with that retrieval in step 5 and nothing else changed (`site_process.py:397-424`);
`python -m research.e2e_ingest.site_graph --config FILE` serves the wire as `site_process.serve` does
(`site_process.py:472-480`), so `wire.ChildProcessEndpoint` (`wire.py:212-240`) needs only the module name changed
by a subclass that passes `-m research.e2e_ingest.site_graph`.

What `hq_tesseract.py` adds to the spike's `Hq`: after `hq.ask(asked, "R")` it writes the question
(`QuestionRow`, `store.py:268-279`, body = the question bytes, `display_text` = `render_text`), the route
(`RouteRow` per routed site in route order with the gate's roles, `store.py:282-286`), every `Received` as a verdict
row (`append_verdict`, `store.py:628-639`, body = `canonical_bytes(r.body)`, `received_as_of` = the candidate's
`as_of`) and the conclusion (`ConclusionRow`, `store.py:315-323`, `conclusion_id` = `"c-" + question_id[:32]` as
`publish.py:76`, version 1, status = the gate's, body = the gate's `to_dict`) into the pipeline's `CollectiveStore`.
The bridge reads these tables only.

## 7. Idempotency keys

| Event | `idempotency_key` | Effect |
|---|---|---|
| `collective.verdict` | `{question_id}:{site}:{seq}` (`pd_verdicts` primary key, `store.py:127-130`) | `event_id = evt_<hash(agent, "event", key)>` (`service.py:1220`); a second POST returns `created: false` |
| `collective.conclusion` | `{conclusion_id}:{version}` (`pd_conclusions` primary key, `store.py:134-136`) | the same event id, and the embedded memory's id `mem_<hash(agent, "event-memory", key)>` (`service.py:1232`); a second POST creates no memory (`service.py:1259-1261`) |

Both keys are stable across bridge runs and across a rebuilt HQ store, since the tables are immutable (`store.py:
137-138`). The bridge's watermark (its own file, `bridge/watermark.json`: the last `(created_at, conclusion_id,
version)` published) is a convenience; correctness rests on the keys, which is what criterion 3's bridge half proves.

## 8. The drills

Every drill runs inside the end-to-end job, in this order, on the one world.

1. **Reference pass (in process).** Every question asked of its routed sites with `wire.InProcessEndpoint`
   (`wire.py:189`); the answer matrix (per question and site: verdict, reason, source, sha256) is kept. Nothing is
   published from this pass; its stores are discarded and the pristine site stores restored (`world.py:148-165`).
2. **Main pass (process mode) with a site restart.** Every site a child process. After the first ⌈Q_s / 2⌉ questions
   routed to the site with the most routed questions (ties by site id), its process is killed (SIGKILL) and reopened;
   it resumes from its persistent graph and store (0 new nodes) and answers the rest. Checks: every (question, site)
   answer equals the reference pass's byte for byte (the spike's process check, `run.py:672-706`); a fresh
   `E2ESiteProcess` of the restarted site in a scratch directory, graph rebuilt from nothing, re-answers the
   post-restart questions with identical verdict bytes (`recovery.site_restart.verdict_bytes_identical`).
3. **Main publish with the broker kill.** The bridge publishes the N events in order (section 3.6). After publish k =
   N // 2 the harness kills nats-server (`driver.kill("nats")`, `harness.py:167-175`), publishes k+1..N against the
   API while the broker is down (the outbox takes them, as smoke step 7 shows, `mycelic_smoke.py:193-204`), restarts
   it (`driver.start("nats")`) and waits idle (`outbox_pending == 0`, `consumer_pending == 0`, `replaying_to_seq`
   null; the smoke test's `wait_idle`, `mycelic_smoke.py:59-80`). Checks: every one of the N event ids is `applied`
   exactly once (`GET /admin/events`, one row per id, status `applied`), `outbox_pending == 0`,
   `consumer_pending == 0`, and `stream_messages > 0`.
4. **Signatures.** Before any forgery, `mycelic_events_failed_total{stage="signature"}` is 0. Then one forged event is
   published straight to NATS on `subject_for("emergence", "memory.observed")` (`transport.py:41-43`) with
   `memory_id` `mem_forged_e2e` and no `Mycelic-Signature` header, as `test_jetstream.py:541-551` does. Checks: after
   2 s the counter is exactly 1; `GET /memory/mem_forged_e2e` is 404; every one of our N events verifies under the
   keyring (the harness fetches each stream message of ours by sequence through nats-py and checks
   `Keyring(driver.signing_key).verify_event(bytes, headers["Mycelic-Signature"])`, `integrity.py:177-180`).
5. **Lineage.** For every conclusion memory: `GET /lineage/<id>` as the administrator has
   `evidence.reconstructable == true`; `GET /memory/<id>` `source_event_ids` contains one verdict event id per routed
   site whose answer came from the site (`source == "site"`); each verdict event's payload sha256 names a verdict row
   of its site in HQ's receive log and in that site's egress log with equal verdict, buckets and newest week
   (`score.logged_match`, `score.py:46-60`); the site-side audit opens the site store read only, joins
   `verdict_for_ref(evidence_ref)` (`records.py:518`) to `confirming_refs` and `entity_refs` and checks that every
   such record's `received_date` is on or before the question's `as_of`. The scan dumps every row of the fabric DB
   (`mode=ro`, all tables as JSON text) and every message of the stream and counts hits of: the record-ref pattern
   `em[0-9]+-[0-9]{7}`; the EMERGENCE narrative marker `\[d[0-9]{3}\] R[0-9]{2}S[0-9]{2}-DEPT-` (`corpus.py:230`,
   `org.py:188-200`); and the first 24 characters of every narrative of the world (`tests/routing_spike/
   test_end_to_end.py:69-77`). All three counts must be 0.
6. **Service rebuild.** After drill 3, record the sorted active memory ids of `emergence` and `report_digest` of
   `GET /verify/<id>` for every conclusion memory (`verification.py:715`). `driver.kill("mycelic")`,
   `driver.wipe_database()`, `driver.start("mycelic")` (`harness.py:167-191`; smoke step 8,
   `mycelic_smoke.py:217-236`); wait ready and idle. Checks: identical id set, identical digest per conclusion.
7. **Re-ingest.** Each site ingests its records again (`EdgeSite.ingest` reports them all as duplicates,
   `records.py:20`) and `site_graph` syncs: node ids and count unchanged; the 20 fixed queries return the same top
   10 and the same full order as before. Then the bridge runs again from an empty watermark: `stream_messages` is
   unchanged, every POST returns `created: false`, 0 new memory ids, 0 new event ids.

## 9. The checks

One check per criterion; a check is `ok` only when every assertion listed holds; any check not `ok` makes
`verdict.pass` false and the harness exits 1, which fails the CI job. Names are the report's `checks` keys.

| Check | Assertions (exact) |
|---|---|
| `c0_real_fabric_keyed` | the driver is `ProcessDriver` (or its subclass) and `driver.nats_bin` is set; `fabric.keyring.keyed is True`; `fabric.transport == "nats-jetstream"`; `fabric.broker_version` matches `^\d+\.\d+\.\d+` and equals the numeric part of `fabric.broker_binary_version`; in CI `fabric.broker_version == "2.10.29"`; `fabric.stream_messages > 0` |
| `c1_signed` | `signatures.failed_before_forgery == 0`; `signatures.failed_after_forgery == 1`; `signatures.forged_memory_absent is True`; every one of the N events verifies; `routing.candidates_asked >= 20`; `publish.verdict_events >= 60`; `publish.conclusion_events >= 20`; `routing.verdicts.budget_unknown == 0`; every unambiguous planted candidate's fabric status equals its expected status; at least one unambiguous planted candidate with expected `supported` |
| `c2_lineage` | `lineage.conclusions >= 20` and `== publish.conclusion_events`; `lineage.reconstructable == lineage.conclusions`; `source_events_complete == conclusions`; `sha256_in_receive_log == sha256_in_egress_log == publish.verdict_events (site-sourced)`; `site_audit_joined == publish.verdict_events (confirm or refute)`; `records_on_or_before_as_of is True`; the three scan counts are 0 |
| `c3_idempotent` | `idempotency.site_reingest.same_node_ids`, `.same_count`, `.same_top10`, `.same_order` all true with `queries_per_site == 20`; `idempotency.bridge_rerun.stream_messages_after == stream_messages_before`; `new_memory_ids == 0`; `new_event_ids == 0` |
| `c4_recovery` | `recovery.broker_kill.N >= 80`; `1 <= k <= N - 10`; `applied_exactly_once is True`; `outbox_pending == 0`; `consumer_pending == 0`; `recovery.service_rebuild.conclusions_applied_before_kill >= 1`; `active_memory_ids_identical is True`; `verify_digest_identical is True`; `recovery.site_restart.verdict_bytes_identical is True` and the main pass's answer matrix equals the reference pass's |
| `c5_measured` (measure job) | every field of `throughput` and of criterion 5's list is present and numeric; `throughput.steps[0].rate == 2`, `.events >= 120`, `.duration_seconds >= 60`, `.apply_p95 <= 1.0` |
| `c6_ci` | asserted by the workflow itself: the end-to-end job's `timeout-minutes` is 20, the measure job's 45, both on `push` and `workflow_dispatch`, both upload their report; `tests/e2e_ingest/test_workflow.py` reads the YAML and pins the install step to `mycelic.yml:24-28` and `NATS_VERSION: v2.10.29` (`mycelic.yml:16`) |
| `c7_guards` | `imports_loaded` and `static_imports` (`run.py:97-126`) over `research.e2e_ingest.hq_tesseract`, `.hq_bridge`, `.publish_api` against `FORBIDDEN_HQ` plus `research.e2e_ingest.site_graph` and (for `publish_api`) `mycelic.service` return empty lists; the hash-pin tests and `test_collective_guards.py` are in the suite jobs; `run.spike_code_hash()` equals the value `docs/collective/routing_spike/prereg-run-2.json` carries (no edit under the spike's code paths) |
| `c8_emergence_load` | per site `ingested == expected_records`, `duplicates == 0`, every `rejected` value 0, `graph_nodes == expected_records`, node ids == record refs; `ingest.total_ingested == ingest.total_expected == world.records`; `world.users`, `world.sites`, `world.records` equal the row of section 3.1 for the run's size |
| `c9_benchmark` | every `discovery.*` field present; `discovery.found_patterns ⊆ Gold.discoverable`; `recall_all == found / gold_discoverable`; the simulator fields present (or null with `reason == "budget"` for the 2,000 size only, section 10); every discovery field carries `synthetic: true` and `measurement: false` in the report's header |
| `c10_suites_green` | `run.suites_sha == run.code_commit`; `run.code_dirty is False` over `research/e2e_ingest`, `research/routing_spike`, `research/mycelic`, `NeuralGraph/research/coordination`, `NeuralGraph/research/retrieval`, `NeuralGraph/chat_memory`, `mycelic/collective`, `mycelic/*.py` and `.github/workflows/e2e-ingest.yml` (`common.py:113`); in CI `E2E_SUITES_SHA` is set by the workflow from `github.sha` only in jobs that `need` the suite jobs |

## 10. Measurement (S7) and the discovery score

**Per-hop numbers, both jobs, at every size run:**

- `ingest.per_site.*.seconds_per_1k_records` = `graph_build_seconds / graph_nodes × 1000`, and the whole-world
  figure; the pipeline's own ingest and extraction seconds beside it.
- `routing.tesseract.seconds_p50`, `_p95` over every ranking of the main pass (site diagnostics `seconds`,
  `site_process.py:330`), with the node counts the rankings ran over.
- `routing.question_to_verdict`: per (question, site) wall seconds around the wire `request`, and per question around
  `hq.ask`; p50 and p95.
- `publish.created_to_applied`: `applied_at − created_at` per event of ours; p50 and p95.

**Tesseract at 600, 5,882 and 20,000 nodes (measure job).** A harness-side measurement graph, not a site and never
asked a question, built with `site_graph` from the first N records of the 2,000 world in record order (68,463
records; the largest site alone has 18,633), for N in (600, 5,882, 20,000); 20 queries each (the 20 fixed queries of
section 3.5); p50 and p95 per N. These are the three points of `HANDOFF-2026-10-09.md:145` re-measured on EMERGENCE
text with the hash embedder.

**Sustained events/s (measure job).** Rate steps 2, 4, 8, 16, 32 events/s, each for 60 s (≥ 120 events), each in its
own organisation (section 3.6), publishing the end-to-end world's verdict and conclusion events re-keyed with the
step's suffix; per step `apply_p95` from `created_at`→`applied_at`; the sweep stops at the first step whose p95
exceeds 1.0 s or after 32 events/s; `throughput.sustained_events_per_second` is the highest rate whose p95 stayed at
or under 1.0 s. The pass condition is the 2 events/s step (criterion 5). The ~2 notes/s guidance of
`DEPLOYMENT.md:165-169` concerns notes; each step's note rate is a quarter of its event rate.

**Scaling table (measure job).** Sizes 200, 600, 2,000 at seed 0 under budgets of 5, 10 and 25 minutes of wall time
each, smallest first; a size that exceeds its budget is stopped and reported `completed: false` with the phase it
reached, and the measure job fails. The table rows: users, sites, records, nodes per site (min, median, max), graph
build seconds and per 1k, pipeline seconds, detection seconds, candidates, rankings, Tesseract p50/p95,
question→verdict p50/p95, publishes, created→applied p50/p95, wall total. The recorded run says which is the largest
size that completed; this brief fixes nothing larger.

**Discovery score (both jobs), `discovery`:**

| Field | Definition |
|---|---|
| `gold_discoverable`, `gold_rare`, `gold_common`, `decoys_by_type` | `make_gold(corpus)` (`corpus.py:759-784`); decoy types 1-5 (`corpus.py:603`) |
| `candidates_true`, `candidates_decoy`, `candidates_background` | routed candidates whose key is a Gold pattern's (anchor, chain) in its window; a decoy's; neither |
| `found_patterns` | Gold-discoverable patterns p with an active `supported` conclusion note in `emergence` by `hq` whose metadata `entity_id`, `predicate` equal p's canonical anchor and chain predicate and whose `snapshot_week` lies in p's window (section 3.5), and which passes the lineage check of drill 5 |
| `recall_all`, `recall_common`, `recall_rare` | found / the respective Gold count (rare is expected 0 by k-suppression, section 3.4) |
| `decoy_accepted_by_type` | decoys of type t with a `supported` conclusion on their (facet-0 anchor, chain) key in their window, over the count of type t |
| `background_supported` | `supported` conclusions on keys of no pattern |
| `simulator.H_mycelic_full`, `simulator.A2_chunked_ctx` | `runner.build_world(target_users, seed)` (the same `build_org`/`build_corpus`/`make_gold` calls, `runner.py:54-62`), `run_arch(name, world, allocation("back-loaded"), seed)` (`runner.py:193-244`; `models.py:215-217`), `evalm.evaluate` → `discovery_recall`, `rare_signal_recall`, `decoy_acceptance_all`, `recall_at_100`, `compute_units` (`runner.py:271-276`, `:303-305`), under the committed `calibration.json` (`runner.py:72-90`); labelled `simulated: true` ("assumed placements", `models.py:5`); for the 2,000 size under a 10-minute budget, else null with `reason: budget` |

The two columns are not the same metric and the report says so: the path's score is at (anchor, chain) granularity
with no temporal-order check and the lexical judge; the simulator's `discovery_recall` matches reported hypotheses to
patterns through its own extraction and chain checks (`evalm.py:27`, `:101-141`). Both are synthetic.

## 11. The CI workflow

`.github/workflows/e2e-ingest.yml`, on `push` to `claude/mycelic-production-deployment-r93cez` with paths
`research/e2e_ingest/**`, `research/routing_spike/**`, `research/mycelic/**`, `mycelic/**`, `NeuralGraph/**`,
`tests/**`, `requirements*.txt`, `.github/workflows/e2e-ingest.yml`, and on `workflow_dispatch`. Four jobs, all on
`ubuntu-latest`, Python 3.11, `pip install --require-hashes -r requirements.txt`, nats-server installed by the step
copied from `mycelic.yml:24-28` with `NATS_VERSION: v2.10.29` (`mycelic.yml:16`):

| Job | `needs` | `timeout-minutes` | Steps |
|---|---|---|---|
| `suites-core` | — | 45 | `python -m pytest NeuralGraph/tests -q -p no:warnings`; `tests/mycelic`; `tests/onboard`; `tests/routing_spike`; `tests/e2e_ingest` |
| `suites-lab` | — | 45 | `python -m pytest tests/lab -q -p no:cacheprovider` (about 21 min, `LOOP-48H.md:110`); `python -m unittest research.mycelic.test_mycelic` (30 tests, `research/mycelic/README.md:53`) |
| `e2e` | `suites-core`, `suites-lab` | 20 | `E2E_SUITES_SHA=${{ github.sha }} python -m research.e2e_ingest run --size 200 --mode process --out e2e-out`; upload `e2e-out/e2e-ingest-report.json` with `if: always()` |
| `measure` | `suites-core`, `suites-lab` | 45 | `E2E_SUITES_SHA=${{ github.sha }} python -m research.e2e_ingest measure --sizes 200,600,2000 --out measure-out`; upload `measure-out/e2e-ingest-measure.json` with `if: always()` |

The suite jobs are criterion 10's gate: a red suite stops both end-to-end jobs (`needs`), so no end-to-end run on a
non-green tree can exist in CI. Criterion 6's last clause (`mycelic.yml` on this branch with `tests/routing_spike`)
is step S0 of `LOOP-48H.md:170`, outside this brief's files and a precondition of the recorded runs.

Time estimate for the `e2e` job at size 200 (to be replaced by the recorded run): install 2 min; world, pipeline and
detection under 1 min (the spike's planted world of 3,661 records took 3.2 to 3.9 s, `ROUTING-SPIKE.md:277-279`);
7 site graphs of 12,964 nodes about 1 to 2 min (5,882 nodes took 31 s, `HANDOFF-2026-10-09.md:134`); reference pass
and main pass about 2 to 3 min each (180 rankings each at 0.3 to 1.3 s, `HANDOFF:145`; `ROUTING-SPIKE.md:911`);
publishing and the broker drill about 2 min; rebuild, forgery, lineage, scan, re-ingest and the 140 fixed queries
about 3 min; the discovery score and the simulator at 309 users under 1 min. About 12 to 15 minutes. If the first
recorded run exceeds 20 minutes the brief is amended before the next run, in its own commit, and the amendment is
the only change.

## 12. Build order (the Tesseract leg is mandatory; the cut rule of `LOOP-48H.md:181` is void)

| Step | Branch | What | Done when |
|---|---|---|---|
| S1 | `wf/e2e-ingest-design` | this brief, alone; adversarial review; amendments in their own commits | reviewed and merged |
| S3 | `wf/e2e-ingest` | `publish_api.py` and its tests against `ProcessDriver` and a real nats-server | idempotent re-POSTs return `created: false`; the event schema test against `publish.py` passes |
| S4 | `wf/e2e-ingest` | `hq_bridge.py`: read-only reader of `pd_verdicts` / `pd_conclusions`, watermark, publish through S3 | the bridge rerun leaves `stream_messages` unchanged on a hand-built HQ store (the `test_collective_pushdown` fixtures' shape) |
| S2 | `wf/e2e-ingest` | `site_graph.py`: persistent incremental graph, `E2ESiteProcess`, process-mode entry point | tests: re-ingest, crash and resume, `as_of` bound; byte-identical verdicts with the spike's site process on the spike's seed-1 planted world |
| S2b | `wf/e2e-ingest` | `emergence_pack.py` and `emergence_world.py`: the pack, the record mapping, the world, the counts | the loader check passes; counts equal section 3.1; `run_pipeline` and `detect` run on size 200 |
| S5 | `wf/e2e-ingest` | `hq_tesseract.py` and `harness.py`: HQ with Tesseract routing, HQ-store rows, the bridge, checks c0-c3, c7-c9 | one local run of size 200 against `/root/.local/bin/nats-server` v2.10.22 (`LOOP-48H.md:182`) passes every check it can locally; the broker version check accepts the local version outside CI |
| S6a | `wf/e2e-ingest` | drills 3, 4, 6, 7 (`drills.py`) | c1, c3, c4 (fabric half) pass locally |
| S6b | `wf/e2e-ingest` | drill 2's site restart | c4 (site half) passes locally |
| S7 | `wf/e2e-ingest` | `measure.py`: per-hop numbers, the three node counts, the rate sweep, the scaling table, the discovery score and the simulator columns | the measure report validates against section 5 |
| S8 | `wf/e2e-ingest` | `.github/workflows/e2e-ingest.yml` as section 11; `tests/e2e_ingest/test_workflow.py` | the workflow passes `actionlint` and the YAML test |
| S9 | `wf/e2e-ingest-fix` | build review, fixes; the six suites locally on the merged tree; merge; push; one recorded run of each job; `## Runs` below; the CI record in `docs/lab/INTEGRATION.md`; handoff status lines only | both jobs green on the same sha, reports committed under `docs/collective/e2e_ingest/` |

S3 and S4 come first because they need no Tesseract and are the part a pilot would use (`LOOP-48H.md:169`); S2, S5,
S6b and S7 follow in the same build and are not cut for any reason. Suites run locally before every merge
(criterion 10). No run counts until S9's recorded CI runs.

## 13. What it does not show

- Anything about real data. Every record, pattern, decoy and entity is generated (`corpus.py:1-34`); no partner
  export, no public file. `measurement: false` on every report.
- Anything about a site's hardware. The runners are shared GitHub runners whose CPU model varies 1.5 to 3× between
  hosts (`LOOP-48H.md:164`); the report names the CPU, and every latency is runner-dependent.
- Anything about a model. The reader is the lexical judge; the embedder is the hash embedder (decision 7's "hash
  embedder only" option, `HANDOFF-2026-10-09.md:371`, is what runs here, and this brief does not decide decision 7).
  The simulator's columns are simulated capability vectors (`models.py:5`; `MYCELIC_ENTERPRISE.md:411-433`), not
  measurements of any model.
- The decisions of the chief scientist. Running each site as a separate process behind the Boundary is the shape of
  decision 1's option (a) (`ROUTING-SPIKE.md:186-199`; `HANDOFF:365`) and decides nothing; routing detector
  candidates to holder sites is the spike's shape and decides nothing about decision 2 (`HANDOFF:366`); decision 7
  as above. Calls reach the loop only as messages (`LOOP-48H.md:92`).
- The benchmark's result. The world is EMERGENCE's; the architectures of `systems.py` do not run on this path. The
  path's discovery score is at (anchor, chain) granularity with no temporal-order check; D2 decoys are accepted and
  rare patterns missed by construction (section 3.4); the simulator's `discovery_recall` is a different metric and
  stands beside it, not against it.
- The full benchmark. 309 users and 12,964 records are 3.1% and 3.9% of the 10,000-user world; the largest measured
  size is the benchmark's smallest published scale (section 3.1).
- Goal A. Nothing here bears on accuracy against the centralized key (`LOOP-48H.md` section 2).
- A pilot's load or a partner's egress policy. One organisation, one HQ agent, 7 to 9 sites, at most 60 conclusions.
- The follow-up layer, the pack drafter, model extraction, the compose deployment: none runs here.

## 14. Open questions for the review

1. Chain-level predicates (section 3.4): the only granularity at which the collective's cross-site key definition can
   see an EMERGENCE pattern. Is the loss of temporal order (D2 accepted by construction) acceptable, stated as it is?
2. The expected-status oracle for planted candidates (section 3.5) is written from Gold and the pack thresholds; a
   mismatch between the lexical judge's reading and the oracle fails c1. Is "unambiguous" drawn in the right place?
3. The candidate floor of 20 at 309 users is not yet measured (no detector ran for this brief). The fallback is the
   next declared size, not a changed setting. Agreed?
4. The 20-minute estimate for the `e2e` job rests on the handoff's Tesseract timings at other node counts; the first
   recorded run decides. Is a one-amendment rule (section 11, last paragraph) acceptable?
5. `question_entities_per_site_per_day` 200 avoids `budget` unknowns when 60 candidates share one `as_of`; is a
   pack-level budget of that size acceptable for a synthetic world?
6. The report derives `keyring.keyed` from the driver's signing key (`harness.py:111`, `:138`) because `/status`
   exposes no key field (`service.py:2488-2519`); the forgery drill is the live proof. Sufficient?

## Amended before any build, 2026-10-11

No code of S2 to S9 exists. Two adversarial reviews of `e3052c0` (the commit of the sections above) returned 25
findings, 8 blocking and 17 minor. Each is answered below as M1 to M25, in the order the reviews gave them; each item
says what was, what is now and why, names its check, and cites the code it rests on. Every floor of sections 2.3 and 9
stands or rises; none falls. Where the text above disagrees with this section, this section wins. The settings test of
section 6 (`tests/e2e_ingest`) compares the harness constants with the tables above as amended here.

The declaration still holds, with this addendum. Since `e3052c0` its author ran in the sandbox (Intel Xeon 2.10 GHz,
4 vCPU) and nothing else: the routing spike's `spike_code_hash()` (M13); a timing of `Tesseract.retrieve` over graphs
built from the 200 world's smallest, median and largest sites with the hash embedder (M15); the simulator's two
architectures at sizes 200 and 600 (M14); the generator at 200 and 1,000 (M20); and `pytest --collect-only` over the
six suites (M2). None of these is a result; no pipeline, detector, wire, fabric or end-to-end step ran. The numbers are
the sandbox's and are labelled so where they appear.

### Blocking findings

**M1. Two lineage witnesses that were one writer** (criterion 2, drill 5, section 5, c2). Was: "each verdict sha256
matches HQ's receive log and the site's egress log", joined with `score.log_index` and `logged_match`. Why wrong:
`EdgeSite` builds its Boundary with `receive_log=Path(hq_dir) / "receive.jsonl"` (`edge/site.py:252-253`),
`Boundary.send` appends the identical line to both files (`edge/egress.py:852-853`), and in process mode the site's
own process constructs the `EdgeSite` (`site_process.py:402-404`). A site that sends one body over the wire and logs
another passes; nothing HQ wrote is consulted. Now, for every site-sourced verdict event, five witnesses, three of
them HQ's own records and two the site's:

| Witness | Side | Source | Assertion |
|---|---|---|---|
| `pd_verdicts.sha256` | HQ | `append_verdict` recomputes `sha256_hex(body)` from the bytes HQ received (`detect/store.py:628-633`) | `event.sha256 == row.sha256` for (question_id, site) |
| `hq/wire.jsonl` | HQ | every frame HQ read, with its own sha256 (`wire.py:181-186`); the harness decodes each response frame with `wire.decode_response` (`wire.py:140-151`) and hashes the body | one response frame of that site whose body sha256 `== event.sha256` |
| `hq/intake.jsonl` | HQ | the sha256 of the bytes HQ accepted, `source == "site"` (`hq.py:271`, `:276-279`) | one row with that sha256, question id and site |
| `site-<id>.egress.jsonl` | site | the Boundary's egress row (`egress.py:852-853`) | one verdict row with that sha256 |
| `RecordStore.verdict_by_id` | site | `verdict_by_id(body["verdict_id"]).sha256` (`edge/records.py:521-522`; `VerdictRow`, `:268-273`) | `== event.sha256` |

Then the join as drilled: `verdict_for_ref(evidence_ref)` (`records.py:518-519`) to `confirming_refs` and
`entity_refs`, every record's `received_date` on or before the question's `as_of`. Section 5's
`lineage.sha256_in_receive_log` and `sha256_in_egress_log` are replaced by `lineage.{sha256_in_pd_verdicts,
sha256_in_wire_log, sha256_in_intake_log, sha256_in_site_egress, sha256_in_site_store}`; c2 asserts each
`== publish.verdict_events` (site-sourced). `hq/receive.jsonl` is written by the site's Boundary; it stays what it is,
the carrier of the cells HQ ingests (`baselines.py:144`, `store.ingest_log`) and the leakage scan's input
(`run.py:188-190`), and is no witness. Section 6: `harness.py` imports `score.read_log` only; `log_index` and
`logged_match` are not used.

**M2. Criterion 10's gate left no evidence** (criterion 10, section 5 `run.suites_sha`, c10, section 11). Was:
`run.suites_sha == run.code_commit` where `suites_sha` was the `E2E_SUITES_SHA` environment string; `run.suites_green`
was a misnomer for that comparison; in CI the `needs` edge was the gate but the artifact carried nothing from the
suite jobs; a skip-when-missing module reads green with its tests unrun (`tests/mycelic/test_jetstream.py:7-8`,
`:32`). Now the gate leaves evidence in the artifact:

- Every suite step runs `python -m pytest <suite> -q -p no:warnings --junitxml=suites/<suite>.xml`;
  `python -m unittest research.mycelic.test_mycelic` becomes `python -m pytest research/mycelic/test_mycelic.py`
  with the same flags (pytest collects the same 30 tests; `research/mycelic/README.md:53`). The suite jobs write
  `suites/<suite>.json` with `{suite, sha, tests, failures, errors, skipped, duration_s, junit_sha256}` (sha =
  `github.sha`) and upload them as `e2e-suites-core` and `e2e-suites-lab`; the `e2e` and `measure` jobs download both
  (`actions/download-artifact@v4`) and the harness embeds `run.suites[]`, `run.suites_evidence = "junit"` and
  `run.needs_results = {"suites-core": "${{ needs.suites-core.result }}", "suites-lab": ...}`. `E2E_SUITES_SHA` is
  dropped. Locally `python -m research.e2e_ingest suites --out DIR` runs the same seven commands and writes the same
  files, and `run --suites DIR` reads them, so the loop's pre-merge run produces the same evidence.
- Floors, the `pytest --collect-only` counts at `e3052c0` (sandbox, 2026-10-11):

| Suite | Tests at `e3052c0` (floor) |
|---|---|
| `NeuralGraph/tests` | 265 |
| `tests/mycelic` | 1,711 |
| `tests/onboard` | 287 |
| `tests/routing_spike` | 61 |
| `tests/lab` | 725 |
| `research/mycelic/test_mycelic.py` | 30 |
| `tests/e2e_ingest` | ≥ 1; its count at the build's merge is written into the settings test and may only rise |

- c10 asserts, for each of the seven suites: `sha == run.code_commit`, `failures == 0`, `errors == 0`,
  `tests >= floor`; `run.suites_evidence == "junit"`; in CI both `needs_results` are `"success"`; `skipped == 0` for
  `tests/e2e_ingest` and for `tests/mycelic/test_jetstream.py` (per-testcase `classname` in the junit file, M11);
  a report with `suites_evidence != "junit"` or any suite missing has `verdict.pass == false`, reason
  `suites_not_green`. The other suites' `skipped` counts are reported, not bounded.

**M3. Nothing bound the stream to the key the harness holds** (section 5 `fabric.keyring.keyed`, `signatures.*`,
drill 4, c0, c1, section 14 question 6). Was: `fabric.keyring.keyed` from `Keyring(driver.signing_key).keyed`, which is
True by construction (`harness.py:111` generates a key; `integrity.py:139`, `self.keyed = bool(keys)`); c1's "every
one of the N events verifies" had no report field; a driver subclass whose `env()` dropped the key (`harness.py:131-142`
is overridable), a bridge writing rows into the service DB, or a second broker would pass. Now:

- The harness reads every message of stream `MYCELIC` (`config.py:82`) by sequence from `first_seq` to `last_seq`
  (`/admin/status` `checks.transport.first_seq`, `.last_seq`, `transport.py:474-475`) through nats-py
  (`JetStreamContext.get_msg`) with the driver's credentials (M25); for each it checks
  `Keyring(driver.signing_key).verify_event(data, headers["Mycelic-Signature"])` (`integrity.py:182-190`; the service
  signs the exact wire bytes, `service.py:1041-1044`) and reads the payload's `event_id` and `kind`. The service puts
  five kinds on the stream and nothing else: `agent.event` (`service.py:1221`), `memory.observed` (`:1157`),
  `memory.attested` (`:1351`), `memory.derived` (`:1588`), `memory.retracted` (`:1309`); registration writes none.
- Section 5 adds `signatures.{stream_messages_fetched, stream_messages_verified, stream_messages_foreign,
  stream_kinds, api_event_ids_in_stream, api_event_ids_duplicated}` and `fabric.keyring.{harness_key_set, key_id,
  key_id_in_service_db}` (`harness_key_set` replaces `keyed`); `key_id_in_service_db` is whether
  `Keyring(driver.signing_key).key_id` (`integrity.py:140`) is listed in `meta.known_key_ids`, which the service
  writes at every start (`service.py:467-478`), read `mode=ro` from the service DB (`store.py:49-52`, `meta`).
- c0 asserts `harness_key_set is True`, `key_id_in_service_db is True`, `fabric.integrity_mode == "keyed"` (M22).
  c1 asserts `stream_messages_fetched == fabric.stream_messages`; before drill 4 `stream_messages_verified ==
  stream_messages_fetched` and `stream_messages_foreign == 0`; after drill 4 `stream_messages_foreign == 3` (M8) and
  `stream_messages_verified == stream_messages_fetched - 3`; every API-returned id (`event_id` and
  `memory_event_id`, `service.py:1240-1256`) appears exactly once among the verified payloads
  (`api_event_ids_in_stream == publish.publishes + publish.conclusion_events`, `api_event_ids_duplicated == 0`);
  `stream_kinds["agent.event"] == publish.publishes`, `stream_kinds["memory.observed"] == publish.conclusion_events`;
  `fabric.stream_messages == sum(stream_kinds.values()) + stream_messages_foreign`. `memory.derived` and the other
  kinds are counted and reported. Section 14's question 6 is closed by this item and M22.

**M4. The HQ-side Tesseract check was a tautology** (section 4 item 1, section 5 `routing.*`, c1, c7). Was: "the
route has exactly `min(3, eligible)` sites and the routed sites are the first 3 of arm R's order", which holds for the
real Router (`coordination/core.py:230-241`: eligible preferred nodes first, the rest after, cut at
`budget.max_nodes`) and for any stub returning `order[:3]`; `route_selected` is one emit (`core.py:405-406`). Now
section 4 states the division: at HQ the Tesseract Router decides eligibility (capability id and availability,
`core.py:206-222`; the failure injector's masks, `:209`, `:156-157`) and the budget cut (`:241`); HQ's rank fusion
decides the order (`hq.py:144-146`, passed as `preferred_node_ids`, `:423-424`). Three checks, each of which a stub
fails:

1. Identity. `tests/e2e_ingest` asserts `type(hq.coordinator) is core.TesseractCoordinator` and
   `type(hq.coordinator.router) is core.Router` (`hq.py:379-380` builds them); the report carries
   `routing.coordinator_class` and `routing.router_class`, the fully qualified names of the live objects; the two
   classes' source is pinned by `run.spike_code_hash` (`CODE_PATTERNS` include
   `NeuralGraph/research/coordination/*.py`, `run.py:73-75`; M13).
2. Per question. `routing.questions[]` carries `{question_id, hq_eligible, route, trace}` with
   `hq_eligible == len(world.site_ids)` (7 at size 200; `Asked.eligible`, `hq.py:398-404`),
   `len(route) == min(3, hq_eligible)`, and `trace` the `TraceEventType` sequence HQ keeps (`hq.py:426-432`):
   `query_received`, then `route_selected` before every `retrieval_selected` (`contracts.py:61-63`).
3. The eligibility filter, exercised once. `hq_tesseract.E2EHq(Hq)` keeps the injector `Hq` builds and drops
   (`hq.py:371`): after `Hq.__init__` it sets `self.failure = FailureInjector(...)` and rebuilds
   `self.coordinator = TesseractCoordinator(self.registry, Router(self.registry, self.failure), ClaimNormalizer(),
   RuleBasedSynthesizer((SLOT,)), LineageAnalyzer(), self.traces)`, the six arguments of `hq.py:379-380` with the
   injector kept. In drill 2, while the restarted site's process is down, the harness applies
   `failure.apply(InterventionTarget.ROUTE, site, reason="e2e drill 2: process down")` (`core.py:109-138`;
   `contracts.py:49-56`), asks one probe question (the next candidate in order) through `hq.ask` only, not written
   to the HQ store, not published, outside the answer matrix, then `failure.clear(...)` (`core.py:140-150`) and
   reopens the process. Checks: the probe's `route_selected` excludes the site; `len(route) == 3`;
   `hq_eligible == 7` (the descriptor is cached, `hq.py:391-396`, so HQ's own eligibility did not change and the
   exclusion is the Router's, `core.py:206-209`); no received record of the probe has `source == "hq"` (a Router that
   ignored the mask would have asked the dead process and got `wire:closed`, `hq.py:290-292`); the trace holds one
   `failure_injected` (`core.py:130-131`; `contracts.py:70`) and `failure.records` one record with `ended_at` set.
   Report: `routing.router_excluded_masked == 1`, `routing.intervention_records == 1`.

A stub that copies `order[:3]` fails 2 and 3; a stub that reimplements the Router fails 1. The probe consumes one
question's budget at three sites and leaves rows in their verdict logs and in `hq/wire.jsonl`, `intake.jsonl` and
`receive.jsonl`; the lineage checks of M1 run from events to witnesses, so extra witness rows change nothing. Its
cost is one question.

**M13. c7 asserted a hash that is false on the tree it was written on** (c7). Was: `run.spike_code_hash()` equals
the value `docs/collective/routing_spike/prereg-run-2.json` carries. Computed at `e3052c0`: `spike_code_hash()` is
`22dc1abaaf3a30ba346118f180f255939cd04c69732d21a03eaacd0f8dfb55cd` over 142 files (`spike_code_files()`,
`run.py:87`); `prereg-run-2.json` carries `ad2ce42517ac3293984faff39770535926be37cdb26eecb7e810afd8b26fdb77` over 133
files, committed at `062c5e7` on 2026-10-09. The difference is nine modules added under `mycelic/collective/**`
after run 2 (`onboard/{__init__,__main__,check,draft,exports,report,roles,score}.py`, `pilot/start.py`; the glob is
`CODE_PATTERNS`, `run.py:73-75`) and `research/routing_spike/site_process.py` itself, changed at `9aa8230` after
`062c5e7`. As written, c7 fails every run and would press toward editing pinned data or the run-2 record. Now the
settings table records the hash at `e3052c0`, `22dc1aba...fb55cd` in full above, and `142` files; c7 asserts
`run.spike_code_hash == that value`, `run.spike_code_files == 142`, and `code_dirty` over the spike's `DIRTY_PATHS`
(`run.py:76-78`) is False; run 2's hash is historical. If the value ever differs, something under the pins changed
(rule 4, `LOOP-48H.md:85`): the run fails, and this value is not amended without naming the commit that changed a
pinned file and why.

**M14. The measure job's budgets summed past its timeout** (section 10 scaling table, section 11 `measure`). Was: 5 +
10 + 25 minutes for the three sizes, a sweep of five steps of at least 60 s, Tesseract at three node counts (20,000
nodes at 8.7 s per query, `HANDOFF-2026-10-09.md:145`, is about 3 minutes alone), install, and a 10-minute simulator
budget not placed inside any row: over 45 with every budget respected, and the report written once at the end
would never be produced. Measured for this amendment (sandbox): the simulator's `run_arch("H_mycelic_full", ...)`
takes 0.3 s at 200 and 0.6 s at 600, `A2_chunked_ctx` 0.1 s, `build_world` under 0.1 s (`runner.py:54-62`,
`:193-244`), so the 10-minute budget was never the constraint; the ranking cost is about 0.43 ms per node per query
(M15). Now the measure job runs these phases in this order, each under its own budget, and `measure.py` writes the
report (`write_json_atomic`) when each phase ends, with `completed` and `phase`, so a timeout leaves a partial report:

| Phase | Budget (min) | Work it must hold |
|---|---|---|
| install (pip, nats-server) | 2 | `mycelic.yml:23-28` |
| size 200 (60 candidates) | 5 | 180 rankings at 0.6-1.1 s; 7 graphs, 12,964 nodes in all, at 0.13-0.18 s per 1k; pipeline and detection under 1 min; about 240 publishes |
| rate sweep | 7 | 5 steps × 60 s, at most 15 s settle each; it needs only the size-200 events and is criterion 5's pass condition, so it runs before the larger sizes |
| Tesseract at 600, 5,882, 20,000 nodes | 5 | 20 queries each: 20 × 0.26 s, 20 × 2.5 s, 20 × 8.6 s (about 3 min); the three builds under 10 s |
| size 600 (60 candidates) | 8 | 180 rankings at a mean site of 3,209 nodes (about 1.4 s); 22,464 nodes built; about 240 publishes |
| size 2,000 (20 candidates) | 15 | 60 rankings at 3.3 s (mean site 7,607 nodes) to 8 s (18,633); 68,463 nodes built (under 15 s); pipeline and detection up to 3 min; about 80 publishes; the simulator columns under 1 min |
| total | 42 of 45 | |

Size 2,000 routes the first 20 candidates of the same ordering, not 60: at 60 its rankings alone would be 10 to 24
minutes. The table's `candidates` column says 20 there; Tesseract p50/p95 is per ranking and comparable across
sizes. The simulator columns run at every size inside the size's budget; c9's null-with-`reason: budget` stays as the
fallback at 2,000 only (unchanged). A phase over its budget is stopped, reported `completed: false` with the phase
named, and fails the job (unchanged). The timeout stays 45. The e2e job's candidate count (60) is unchanged.

**M15. The e2e time plan undercounted the ranking work** (section 3.1, 3.5, 8, 11). Was: 12 to 15 minutes, counting
the 140 fixed queries once (criterion 3 compares them before and after re-ingest, so 280), taking the low end of the
handoff's per-query range, and asking every routed site serially. Measured for this amendment (sandbox, Intel Xeon
2.10 GHz, 4 vCPU; `fake_embedding` at `EMBED_DIM` 256, `site_process.py:70`; `Tesseract.retrieve(limit=nodes)` as
`_local_retrieve` calls it, `site_process.py:264`; graphs built from the 200 world's three site sizes): per query
p50 0.617 s at 1,458 nodes, 0.773 s at 1,805, 1.141 s at 2,574, which is 0.42 to 0.44 ms per node; graph build 0.13 to
0.18 s per 1k nodes (the handoff's 31 s for 5,882 nodes, `HANDOFF:134`, was the conversation ingestion path, not this
one). The coordinator asks a route one site after another (`core.py:409-410`) and `SiteProxyAdapter.query` blocks on
the wire (`hq.py:290`). The e2e job's rankings: reference 180, main 180, the restarted site's re-answers at most 30
(⌈Q_s / 2⌉ of the most-routed site), fixed queries 280; about 670. Now:

1. The 280 fixed queries run concurrently across the seven site processes (each endpoint has its own lock,
   `wire.py:224`; the processes are independent); each site's 40 queries stay serial.
2. The reference pass asks a question's three routed sites in three threads over `InProcessEndpoint`s (each site runs
   on its own worker thread, `wire.py:189-191`); the route is `orders["R"][:3]` from `Hq.prepare` (`hq.py:398-404`,
   every site eligible in that pass). The reference pass is the byte-identity reference of drill 2, not the routing
   proof.
3. The main pass keeps the serial coordinator path: it is the Tesseract routing under test (section 4, M4).
4. The estimate, on a sandbox-class runner: main pass 180 × 0.8 s, about 2.4 min (3.4 if every ranking hit the
   largest site); reference pass 60 × 1.2 s, about 1.2 min if the three threads overlap, 2.4 to 3.4 if the interpreter
   serialises them; fixed queries 280 × 0.8 s over 4 vCPU, about 1 min; re-answers under 0.6 min; seven graph builds
   about 3 s; world, pipeline and detection under 1 min (the spike's 3,661 records took 3.2 to 3.9 s,
   `ROUTING-SPIKE.md:277-279`); publishing and the broker drill about 1.5 min; forgeries, lineage and scans about 1
   min; rebuild about 1 min; re-ingest and bridge rerun about 0.5 min; discovery score and simulator under 0.5 min;
   install 2 min. About 12 to 16 minutes; a runner 1.5× slower lands at 17 to 23. The 20-minute bound stays (criterion
   6; it is neither lowered nor raised here) and the one-amendment rule of section 11 stays: the first recorded run
   decides. Section 11's estimate paragraph and section 14's question 4 are read with this item.

### Minor findings, accepted

**M5. The rebuild replays the forged messages** (drills 4 and 6, section 3.6, c4). The replay judges
`rejected / seen` against `replay_max_reject_ratio`, default 0.01 (`config.py:114`, `:181`); `seen` counts every
delivery (`service.py:984-991`), the rejected ones included (`:929-937`); over the ratio it writes `ready_block` and
`/ready` stays 503 (`:596-612`). With three forgeries (M8) against the floors, 3 / (80 + 20 + 3) is 0.029: blocked
by default, for a reason unrelated to recovery. Now the driver subclass's `env()` also sets
`MYCELIC_REPLAY_MAX_REJECT_RATIO=0.05`, because three known forgeries against at least 103 deliveries give a ratio of
at most 0.0292, and the exact count is asserted so the raised ratio hides nothing. Drill 6 reads the audit row
`recovery.signature_rejections` (`service.py:604-608`) through `GET /admin/audit` (`api.py:459`) or the DB `mode=ro`,
`checks.consumer.ready_block` from `/admin/status` (`service.py:2507`) and `GET /ready`. Fields:
`recovery.service_rebuild.{replay_seen, replay_rejected, replay_blocked, ready_block, ready_after_rebuild}`. c4
asserts `replay_seen == fabric.stream_messages` at the rebuild, `replay_rejected == 3`, `replay_blocked is False`,
`ready_block is None`, `ready_after_rebuild is True`. This also shows the rebuilt service rejects the forgeries under
the same key.

**M6. The broker-kill fields could not show the broker was down** (drill 3, section 5, c4). "One row per id" is by
construction on a table keyed by `event_id` (`store.py:495-499`) and is not the claim. Now, after publish N and before
`driver.start("nats")`, the harness records from `/admin/status`: `checks.publisher.outbox_pending`
(`service.py:2498`), `checks.transport.connected` (`transport.py:465`) and `status` (`degraded` when not
connected, `service.py:2481-2486`; the smoke asserts the same, `mycelic_smoke.py:199-200`); after the restart
`checks.transport.reconnects` (`transport.py:467`); from `/metrics` `mycelic_events_applied_total` by `kind`
(`metrics.py:34`) and `mycelic_events_ignored_total` (`:39`) before and after the drill. Fields:
`recovery.broker_kill.{outbox_pending_during_outage, transport_connected_during_outage, status_during_outage,
transport_reconnects_after, applied_delta_agent_event, applied_delta_memory_observed, ignored_delta,
active_notes_after, stream_duplicates}`. c4 asserts `outbox_pending_during_outage >= 1`,
`transport_connected_during_outage is False`, `status_during_outage == "degraded"`, `transport_reconnects_after >= 1`;
`applied_exactly_once` is defined as: every API-returned id (N `agent.event` ids plus the `memory.observed` ids, M18)
has `status == "applied"` and `applied_at` not null (`store.py:503-507`), `applied_delta_agent_event == N`,
`applied_delta_memory_observed == publish.conclusion_events`, `active_notes_after == publish.conclusion_events`
(active conclusion notes in `emergence` by `hq`), and `stream_duplicates == 0` (M3's `api_event_ids_duplicated`).

**M7 and M19. `GET /admin/events` caps at 1,000 rows and the sweep's events were unspecified** (section 5
`publish.created_to_applied`, section 10 sweep). `limit` is capped at 1,000, newest first, no cursor (`api.py:425`;
`store.py:1192-1202`), so a step at 16 or 32 events/s would be read truncated; and a re-POST of an existing key is
`created: false` and never re-applied (`service.py:1240-1241`, `:1250-1259`), so its latency is meaningless. Now the
measure job reads `created_at` and `applied_at` for every event of a step from the service DB `events` table
(`store.py:495-499`) opened `mode=ro` (as `store.py:690` opens one) for the step's organisation, and asserts per step
`publish.created_to_applied.n == events published in the step`; the e2e job may use `/admin/events` only while
`publish.publishes + publish.conclusion_events <= 1000` (at most about 300 at size 200), else the same DB read. The
step's events are the e2e world's N events cycled with idempotency keys `{step}:{cycle}:{original key}` until 60 s
have elapsed, so every POST is a new event (`created: true`); per step `events_duplicate == 0`; at 32 events/s a step
holds at most 1,920 events and 480 notes, under the 5,000 cap. For a conclusion POST the latency is the
`memory.observed` event's (M18).

**M8. One header-less forgery proved little** (drill 4, section 5 `signatures.*`, c1). Now the forged payload is a
well-formed `memory.observed` event in org `emergence` in the shape of `test_jetstream.py:544-548` (`memory_id`,
`org_id`, `layer`, `scope`, `text`, `producer_id`, `operator`), which an unkeyed service would apply, so the 404 is
evidence. Three forgeries, in order: (1) no `Mycelic-Signature` header, `memory_id` `mem_forged_e2e_1`; (2) header
`Keyring(secrets.token_hex(32)).event_signature(wire)` (`integrity.py:177-181`), `mem_forged_e2e_2`; (3) one of our
own stream messages re-published with its `memory_id` replaced by `mem_forged_e2e_3` and its original header (altered
bytes fail `verify_event`, `integrity.py:182-190`). After forgery i the counter
`mycelic_events_failed_total{stage="signature"}` (`metrics.py:35`; `service.py:929-931`) is exactly i, so criterion
1's "exactly 1 after one forged publish" holds after the first; c1 asserts `signatures.failed_after_forgery == 3`,
`GET /memory/mem_forged_e2e_{1,2,3}` all 404, `stream_messages_foreign == 3` (M3), and the counter is read with the
exact label `stage="signature"`.

**M9. The graph node count was the builder checking itself** (section 5 `ingest.per_site`, c8). Now the harness opens
each site's graph file `mode=ro` after the site process has exited (the end of drill 7) and reads
`SELECT node_id FROM nodes WHERE session_key = ? AND layer = ?` with `"site:" + site_id` (`SESSION_PREFIX`,
`site_process.py:69`, `:204`) and `NodeLayer.MESSAGE.value` (`retrieval/data_types.py:37`; the storage's own read,
`sqlite_storage.py:317-318`; schema `:178-186`; insert `:258-263`). The expected refs come from `corpus.recs` and
`org.ancestor_at(uid, SITE)` (`org.py:74-81`), computed without the site modules. Fields
`ingest.per_site.<site>.{graph_nodes_db, node_ids_sha256, record_refs_sha256}`, both digests sha256 over the sorted
ids joined by newlines. c8 asserts `graph_nodes_db == expected_records`, `node_ids_sha256 == record_refs_sha256`,
and `graph_nodes` (what `site_graph` reported) `== graph_nodes_db`. No ref enters the report.

**M10. `code_dirty` missed paths that change the run** (c10). Accepted with one correction: git's pathspec
`mycelic/*.py` does match `mycelic/sdk/__init__.py` (`git ls-files -- 'mycelic/*.py'` at `e3052c0` lists 96 files,
two of them under `sdk/`: a pathspec wildcard matches `/`), unlike `Path.glob` in `code_files` (`common.py:80-86`).
The list was still too narrow. Now c10's paths are `mycelic`, `research/e2e_ingest`, `research/routing_spike`,
`research/mycelic`, `NeuralGraph/research/coordination`, `NeuralGraph/research/retrieval`, `NeuralGraph/chat_memory`,
`tests`, `deploy/mycelic` (the service's rules file, `harness.py:35`, `:139`), `requirements.txt`,
`requirements-lock.txt`, `requirements-runtime.txt`, `.github/workflows/e2e-ingest.yml` and
`.github/workflows/mycelic.yml`; `code_dirty` is evaluated at the start and at the end of the run
(`run.code_dirty_start`, `run.code_dirty_end`, both False).

**M11. A skip-when-missing test is a green suite** (section 6 `tests/e2e_ingest`, section 11). The workflow exports
`MYCELIC_NATS_SERVER_BIN=/usr/local/bin/nats-server` (where `mycelic.yml:27` installs it; read by `harness.py:97-99`
and `test_jetstream.py:32`) and `E2E_REQUIRE_NATS=1` for every job; under `E2E_REQUIRE_NATS` the tests in
`tests/e2e_ingest` fail instead of skipping; c10's `skipped == 0` for `tests/e2e_ingest` and
`tests/mycelic/test_jetstream.py` (M2) catches the rest.

**M12. A JSON file cannot show it was not typed; the aggregates could not be recomputed** (section 5, c9, S9). The
workflow prints `sha256sum` of each report into the job log and `$GITHUB_STEP_SUMMARY`; `## Runs` cites the run id,
the job and the artifact's sha256; `check REPORT` for a recorded run requires `run.github_run_id` and `run.command`
and recomputes every aggregate (`percentile`, `stats.py:112`) from per-item arrays the report must carry:
`routing.tesseract.samples[]` `{question_id, site, seconds, nodes, nodes_as_of, returned, read}`;
`routing.question_to_verdict.samples[]`; `publish.created_to_applied.samples[]` `{event_id, kind, created_at,
applied_at}`; `lineage.rows[]` `{memory_id, verdict_event_ids, sha256s, confirming_refs_count, entity_refs_count,
max_received_date, as_of}`; `discovery.oracle_rows[]` `{anchor, chain, as_of, route, expected, ambiguous, reason,
per_site_facet_counts}` (synthetic Gold counts, harness side, so c1's expected statuses are recomputable). No
`record_ref`, narrative or node id appears in any array; the report-schema test scans for the patterns of drill 5.
Sizes: at most 60 conclusions, about 300 events, about 700 rankings.

**M16. c8's `ingested` had no source** (criterion 8, section 3.3, c8). `run_pipeline` keeps the `IngestResult` local
and raises on any duplicate or rejection (`baselines.py:136-138`); `Pipeline` exposes `sites` and `store` only
(`:101-108`). Now `ingested` is the count of the site store's `records` rows with `forwarded_in = 0`, read `mode=ro`
(the read `site_process.py:236-239` uses; the ingest rules, `records.py:20-27`); `duplicates == 0` and `rejected == 0`
are proven by `run_pipeline` returning, recorded as `ingest.pipeline_raised: false`; the per-reason `rejected` dict
leaves the report. `emergence_world.py` does not ingest on its own first: a prior `EdgeSite.ingest` (`site.py:278`)
would make the pipeline's own ingest see duplicates and raise.

**M17. The three subclasses cannot call the pinned constructors** (section 6, "What `site_graph.py` changes").
`SiteProcess.__init__` builds `TesseractRetrieval` and `SpikeVerifier` inline (`site_process.py:412-421`);
`TesseractRetrieval.__init__` (`:186`) unlinks the graph files (`:205-207`) and builds (`:224`);
`ChildProcessEndpoint.__init__` hard-codes the module in `Popen` (`wire.py:221-223`). Now the mechanism is named:
`PersistentTesseractRetrieval(TesseractRetrieval)` re-implements `__init__` (no unlink; sync by the `seq` watermark;
`nodes` and `build_seconds` set) and inherits `_own_records`, `_local_retrieve`, `union`, `entity_matched`,
`retrieve` and `close` (`:227`, `:257`, `:282`, `:298`, `:310`, `:338`); `E2ESiteProcess(SiteProcess)`
re-implements `__init__` with step 5
(`:412-416`) replaced by the persistent retrieval and nothing else changed; `E2EChildProcessEndpoint
(wire.ChildProcessEndpoint)` re-implements `__init__`: `wire._Logged.__init__(self, site_id, log_path)`
(`wire.py:175-178`), the same env and `Popen` with `-m research.e2e_ingest.site_graph` (`:219-223`), `self._io =
threading.Lock()` (`:224`); it inherits `request` (`:226`) and `close` (`:240`). `tests/e2e_ingest` takes
`inspect.getsource` of each re-implemented `__init__` and the spike's and asserts the unified diff holds exactly the
named hunks.

**M18. A conclusion POST yields two events** (section 3.6, drills 3 and 4, c1, c4). `service.py:1246-1258`: the
`agent.event` and a `memory.observed` event with its own `new_id("evt")`, returned as `memory_event_id`. Now: N
counts POSTs (the `agent.event` ids); the signature and exactly-once sets are N + `conclusion_events` ids, both from
the API results; `fabric.stream_messages` expected is N + `conclusion_events` + the service's other kinds (M3) + 3
after drill 4; `created_to_applied` for a conclusion is the `memory.observed` event's `applied_at - created_at` (the
note's apply), with the `agent.event`'s reported beside it.

**M20. The oracle named three statuses; the gate has five** (section 3.5, c1). `STATUSES` are supported, hypothesis,
stale, contested, rejected (`pushdown/gate.py:64`); `confirming` is a site-sourced `confirm` whose support bucket is
not suppressed (`:231-236`), `weak` a confirm that is not counted (`:237`), `refuting_contributing` (`:238`),
`is_stale` by the Sunday of the newest confirming week (`:245-247`), assignment `:251-275`. A contradicted pattern
carries one polarity −1 record three days after a facet (`corpus.py:577-595`): 13 of 40 real patterns at 200/seed 0
(generator run for this amendment), and the judge's reading of such a record decides the site's verdict
(`edge/verify.py:47`, `:243-258`). Now the oracle is over the five: `supported` as written; `hypothesis` when no routed
site is strong; `stale` when at least two routed sites are strong, none holds a −1 record of the pattern, and the
Sunday of the newest facet week among the strong sites is more than 42 days before `as_of`; a candidate with exactly
one strong site, or whose pattern is contradicted at any routed site, is ambiguous and is reported with both values,
not asserted; `contested` is never expected; `rejected` on a planted candidate is a harness fault (`gate.py:202-213`:
pack hash, schema, look-ahead) and fails c1. The floor stands: at least one unambiguous planted candidate with
expected `supported`.

**M21. The candidate window was unstated** (section 3.5). The spike filters by `first_candidate_week` in
`[eval_from, eval_to]` (`world.py:121-123`). Now: no window. The candidate set is every candidate with
`snapshot is not None`, ordered as section 3.5 says, the first 60; the settings test records `eval_window: null` and
the report carries `routing.eval_window: null`.

**M22. There is no `/status`; a service-side keyedness field exists** (section 5, section 14 question 6). The routes
are `/health`, `/ready`, `/metrics` (`api.py:434-436`) and `/admin/status` (`:458`; `MycelicClient.status()`,
`sdk/__init__.py:223-224`). Every `/status` in section 5 reads `/admin/status`. Section 5 adds `fabric.integrity_mode`
from `GET /verify/<id>` (`api.py:448`; `verification.py:733`) of any conclusion; c0 asserts `== "keyed"` beside M3's
witnesses.

**M23. Wrong line range** (drill 2). The byte-identity check of the spike is `run.py:472-493` (`identity_bad`,
`answer_identity`) and `process_check` at `:769`; `:672-706` is the result dict.

**M24. Tier labels are model-class names** (section 10 discovery table, S9). The report records the allocation name
`"back-loaded"` and the five metric fields only, never the tier labels of `models.py:216-217` nor the checkpoint
named at `models.py:8`; the report-schema test scans the report for those strings and fails on a hit.

**M25. The forged publish needs its own connection** (drill 4, section 6 `drills.py`). `test_jetstream.py:548`
publishes through the service's own transport object, which the harness does not have. Now `drills.py` connects with
nats-py to `nats://127.0.0.1:{driver.nats_port}` with `driver.nats_user` and `driver.nats_password`
(`harness.py:107`, `:112`, `:117-119`), publishes the forgeries on `subject_for("emergence", "memory.observed")`
(`transport.py:41-43`) into stream `MYCELIC` (`config.py:82`), reads the stream for M3 and the broker version from
`Client.connected_server_version`, and never touches the service's transport.

### Declined

None. Every finding was either right as stated or right with the correction noted (M10).

### Floors after this amendment

| Where | Before | Now |
|---|---|---|
| c1 forgeries | 1 forged publish, counter exactly 1 | 3 forged publishes (M8), counter exactly i after the i-th; three 404s; `stream_messages_foreign == 3` |
| c1 stream binding | none | every API-returned id verified in the stream exactly once (M3) |
| c2 witnesses | 2 Boundary logs (one writer) | 5 witnesses, 3 of them HQ's own (M1) |
| c4 broker kill | harness-side fields | outage witnessed by the service and the metrics (M6); replay judged (M5) |
| c7 hash | run 2's value (false) | the value at `e3052c0`, 142 files (M13) |
| c8 graph | builder-reported count | DB-side count and digests (M9); `ingested` from the site store (M16) |
| c10 evidence | an environment string | junit evidence per suite with the floors of M2; `skipped == 0` where M11 says |
| c10 paths | 9 paths | 14 paths, at start and at end (M10) |
| measure job | budgets over 45 | 42 of 45 with per-phase reports (M14); size 2,000 at 20 candidates is a scope statement of the measure job, not a floor; the e2e job's 60 stands |

Nothing above this section was lowered.

### Section 14 after this amendment

Questions 4 and 6 are answered by M15 and by M3 with M22. One question is added:

7. M4's probe question costs one question's budget at three sites and leaves rows in their verdict logs and in HQ's
   wire, intake and receive logs, outside the answer matrix and the publish set. Is that acceptable, or should the
   eligibility exercise be a separate world of two sites in `tests/e2e_ingest` only?

## Runs

None yet.
