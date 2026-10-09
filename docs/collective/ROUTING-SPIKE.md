# Routing spike: Tesseract on the path to the fabric (plan item 3.2)

**Status: design and pre-registered criterion only.** No code that runs this experiment exists at this commit. The
design, the settings and the pass bar below are fixed before any such code is written. Base: commit `95b2a1e`, the
head of the base branch when this was written.

**This is a spike, not a decision.** It informs decision 1 (where Tesseract runs at a site) and decision 2 (what
Tesseract routes when nobody asks a question) in `docs/handoff/HANDOFF-2026-10-09.md`. Both stay with the chief
scientist. A pass or a fail here is one input to each.

**Synthetic only.** The world is generated from the illustrative `device_quality` pack. Its plants were written by the
same author as the detectors. Retrieval uses the repo's hash embedder (`NeuralGraph.chat_memory.llm.fake_embedding`).
No model server is called. Nothing here is a measurement of the real world.

Every number on this page is either a setting chosen here (marked *chosen*) or comes from a command listed in
Appendix A.

## 0. The question

A detector candidate arrives at HQ. It names an entity and a failure. It names no site. HQ may ask only a few sites.
Does choosing those sites with a router find more planted cross-site patterns than choosing them at random, when both
ask the same number of sites about the same candidates? And can the whole path run: a NeuralGraph of narratives at
each site, Tesseract over it through `NeuralGraphMemoryAdapter`, `TesseractCoordinator` with rank fusion and holder
selection at HQ, and conclusions published to the fabric with lineage?

## 1. What is built

### 1.1 Parts

New code lives in a new package, `research/routing_spike/`. Nothing under `mycelic/` changes. The guard tests stay as
they are. `NeuralGraph/research/coordination/` is used as it is; any change there must be additive and covered by
`NeuralGraph/tests`.

| Part | Module | Side | What it does |
|---|---|---|---|
| Wire | `research/routing_spike/wire.py` | both | The narrow interface: `request(kind, body_bytes) -> body_bytes`, kinds `describe` and `question`. The descriptor's closed schema. An in-process endpoint and a child-process endpoint. Standard library only |
| Site process | `research/routing_spike/site_process.py` | site | Builds the site's NeuralGraph from its own records, ranks them with Tesseract through `NeuralGraphMemoryAdapter`, reads the top records with the shipped lexical judge, applies the shipped verdict rules, sends the verdict through the site's own Boundary. Also `python -m research.routing_spike.site_process` for process mode |
| HQ | `research/routing_spike/hq.py` | HQ | The HQ view of the cells, the routers, one `SiteProxyAdapter` per site, `CapabilityRegistry`, `Router` and `TesseractCoordinator`, verdict intake, the shipped gate |
| Publisher | `research/routing_spike/publish.py` | HQ | Publishes verdicts and conclusions into an in-process fabric service with lineage |
| World | `research/routing_spike/world.py` | harness | Generates and plants worlds, runs the shipped pipeline and detection, picks the candidates, holds the labels |
| Scorer | `research/routing_spike/score.py` | harness | Reads the fabric, the HQ receive log and the site egress logs; scores against the plant spec; computes the statistics and the verdict |
| CLI | `research/routing_spike/run.py` | harness | `prereg`, `run`, `score` |
| Tests | `tests/routing_spike/` | | Section 9 |

The harness sees everything, as every harness in this repo does. It passes HQ code exactly two things: the shared
candidate list, and (for the oracle arm O only, section 4) the plant spec's sites. It never passes HQ code a record,
a narrative, a site graph, a Tesseract score or a label.

### 1.2 Data flow

```
 HARNESS  world.py
   generate(pack, seed, 6, 52) + plant(world, spec)          packs/generator.py, evaluate/plant.py
   run_pipeline(...): per site EdgeSite ingest, lexical extraction, weekly cells through each site's Boundary,
                      HQ CollectiveStore ingests the receive log                       evaluate/baselines.py
   detect(store, as_of, "X", tie_salt) -> candidates -> the first 60 (shared by every arm)   detect/detectors.py

 SITE <id>  site_process.py  (one per site: an object, or a child process)
   own records (RecordStore, read only) -> NeuralGraph: SQLite storage, one MESSAGE node per own record,
                                           hash embedding                       NeuralGraph/research/retrieval
   "describe" -> descriptor (closed, fixed per site)
   "question" -> Boundary.accept("in", "question")                                         edge/egress.py
              -> budget and master-data checks, as the shipped SiteVerifier does            edge/verify.py
              -> NeuralGraphMemoryAdapter.query: local_retrieve = Tesseract over the site's graph,
                 keep own records received in the question window, first L = 50
                                              coordination/adapters.py, retrieval/tesseract.py
              -> the exports stay in the site: record handles -> judge_payload + lexical_judge -> decide
              -> buckets and evidence_ref -> Boundary.send("out", "verdict")               edge/verify.py, egress.py
              -> verdict bytes

 WIRE  wire.py:  request("describe") -> descriptor bytes;  request("question", question bytes) -> verdict bytes

 HQ  hq.py
   descriptors -> one SiteProxyAdapter per site -> CapabilityRegistry           coordination/core.py
   per candidate: question_window + build_question at the snapshot's as_of      pushdown/questions.py
                  HqView over CollectiveStore cells -> a site ranking (router of the arm)   detect/store.py
                  TesseractCoordinator.execute(request, phase=arm, preferred_node_ids=ranking, max_nodes=m)
                     -> Router.select -> SiteProxyAdapter.query -> wire -> verdict bytes
                     -> intake (check_artifact, question id, pack hash, window) -> one ClaimEnvelope per site
                  gate.evaluate(pack, question, routes, verdict records, as_of) -> status   pushdown/gate.py
   publish.py: per arm, verdict events and one conclusion event with an embedded memory that cites them
                                                     mycelic/service.py (ingest_events, the API behind publish_events)
 SCORER  score.py: fabric notes + lineage + events, HQ receive log, site egress logs, plant labels -> found, statistics
```

### 1.3 Crossings

| From | To | Interface | Artifact | Checked by | Logged in |
|---|---|---|---|---|---|
| Site | HQ | `Boundary.send` (existing) | `cells_bundle`, k-suppressed weekly cells | the Boundary, then `CollectiveStore.ingest_log` | `hq/receive.jsonl`, site egress log |
| HQ | site | `wire.request("question")`, then `Boundary.accept("in", "question")` | `question` (closed spec, `ARCHITECTURE.md` 15.2) | `check_artifact` on both sides | `hq/questions.jsonl`, site ingress log |
| Site | HQ | `Boundary.send("out", "verdict")`, returned by `wire` | `verdict` (closed spec, 15.2) | the Boundary, then HQ intake (`check_artifact`, question id, pack hash, window) | `hq/receive.jsonl`, site egress log |
| Site | HQ | `wire.request("describe")` | descriptor (section 2.3) | the spike's closed schema, on both sides | `hq/descriptors.jsonl`, `site-<id>.descriptors.jsonl` |
| HQ | fabric | `MycelicService.ingest_events`, in process | agent events with one embedded memory | the fabric's validators | the fabric event log |

Inside a site, Tesseract, the adapter and the reader talk by plain Python calls. Their objects are never serialized
out. The adapter's `ClaimEnvelope`s carry record handles and stay in the site process.

The descriptor is the one artifact the existing Boundary does not know. Adding it to the Boundary's specs would change
`edge/egress.py`, which is out of scope here. So the spike validates it on both sides, logs it beside the Boundary's
logs, and scans it with the rest (section 5.5).

### 1.4 Inside a site

The site process answers a question with the shipped `SiteVerifier.answer` steps (`ARCHITECTURE.md` 15.4), in the
same order, with one change: retrieval (step 7 there, step 5 below) is Tesseract through the adapter instead of
`edge/verify.retrieve`.

1. `Boundary.accept("in", "question")`. The site clock is set from the question's `as_of` (a simulation convenience).
   The shipped no-secret step never fires here: every site has a seeded-demo secret.
2. A question answered before re-sends its stored bytes and uses no budget.
3. The pack's daily budgets apply: 5 questions per entity per day and 50 entities per site per day (Appendix A).
4. An id outside the site's master data gets `unknown` without a record read.
5. **Retrieval.** The graph holds one MESSAGE node per own record. Forwarded-in copies are left out, as the shipped
   retrieval leaves them out. The node text is the narrative plus one line listing the record's structured entity
   values and codes, so a record that names its entity only in a structured field can still rank. Each node carries
   the record's received date. The query is the question rendered by `pushdown.questions.render_text` plus the
   entity's alias phrases. Tesseract ranks the site's session with the hash embedder (256 dimensions), with its
   `limit` set to the session's node count. Tesseract fuses its stores' top candidates, so it may return fewer nodes
   than the session holds; a node it does not return is not read. The adapter's `local_retrieve` keeps the returned
   nodes received in the question window, in Tesseract's order, and returns the first L = 50 (*chosen*, see below).
   `NeuralGraphMemoryAdapter` gets a `claim_projection` that exports only the record handle, and `max_claims` = 50.
6. **Reading.** Each exported record is read with `edge.verify.judge_payload` and `edge.verify.lexical_judge`, the
   shipped offline judge. `edge.verify.decide` applies the shipped verdict rules.
7. **The verdict.** Counts become buckets. `evidence_ref` is the HMAC of the verdict id with the site's seeded-demo
   secret. The verdict and its question-log row are stored in the site store, then sent through the Boundary.
   `truncated` is true when the window held more than L of the site's own records.

Why L = 50: a site cannot read every record per question once a model reads them. The fastest reader in the handoff
took 9.9 to 10.5 s per record. Fifty records at that speed is under the pushdown's default 600 s deadline. With the
lexical judge the cost is small; L keeps the shape a model site would have.

A test pins this re-implementation to the shipped one: with `edge/verify.retrieve` swapped back in for step 5 and the
cap set to `verify_max_records`, the site process returns the same verdict bytes as `SiteVerifier.answer` on the same
questions.

### 1.5 At HQ

`SiteProxyAdapter` implements the coordination package's `MemoryNodeAdapter` protocol for one site:

- `describe_capabilities` returns the descriptor the site sent.
- `query` sends the question bytes, receives the verdict bytes, runs HQ intake, and returns one `ClaimEnvelope`:
  content `{slot: "verdict", value: <confirm|refute|unknown>}` plus the bucket labels and newest week;
  `evidence_refs` = the verdict's `evidence_ref` (or none); `lineage_root_ids` = one opaque root
  `<site>:<verdict_id>`; `failure_domains` = the site id. A refused or missing verdict becomes HQ's own
  `unknown` record (`reason: error`), as the shipped orchestrator does.
- `verify` and `propose_learning` are not used.

`TesseractCoordinator.execute` runs with `preferred_node_ids` set to the arm's site ranking and
`budget.max_nodes` = m. `Router.select` keeps the eligible sites (descriptor capability matches, availability not
`unavailable`) in that order and takes the first m. The coordinator's trace (`route_selected` before any retrieval
event) is saved per candidate and arm.

The coordinator's rule-based synthesis is not the decision. The shipped gate is: `pushdown.gate.evaluate` over the
routed sites' verdicts, with each site's role set as the shipped pushdown sets it (D3): `contributing` when HQ holds a
cell of the key in the question window at `as_of`, else `sibling`. The role rule is the same for every arm.

**Rank fusion** here means fusing several HQ-side site rankings into one holder ranking (section 4). It never fuses
Tesseract scores across sites. Tesseract scores never leave a site. That also sidesteps the handoff's finding that
merging Tesseract's per-store, max-normalised scores across stores did worse than pooling.

### 1.6 Publishing to the fabric

One in-process fabric service (`MycelicService` with the in-process transport) per seed and world. One organisation
per arm, and one per random subset (section 4), so the fabric's consolidation never mixes arms. One agent, `hq`, per
organisation. Per candidate and arm:

1. one `collective.verdict` agent event per routed site, whose payload holds the site, question id, verdict id,
   verdict, buckets, newest week, window and the sha256 of the verdict bytes as HQ received them;
2. one `collective.conclusion` agent event with an embedded memory: `entity` = the entity id, `slot` = the predicate,
   `value` = the gate status, `topic` = `collective/device_quality`, `kind` = `risk`, `source_event_ids` = the verdict
   events of step 1, and metadata with the entity type, candidate key, question id, conclusion id, `as_of`, snapshot
   week, arm and routed sites. Its text is HQ's own display sentence built from the question's parameters.

Every conclusion is published, whatever its status. Only `supported` ones can count as found (section 3.5).

### 1.7 Process mode and decision 1

Each site can run as a child process: `python -m research.routing_spike.site_process` reads one JSON request per line
on stdin and writes one response per line on stdout. It opens its own record store and graph. HQ holds no handle into
it. This is the shape of decision 1's option (a), a separate site-side process behind the existing Boundary. The
in-process endpoint passes bytes through the same framing. A test runs one seed both ways and requires byte-identical
verdicts. The primary run may use either mode; the run file records which.

What the spike can say about decision 1: whether the narrow interface was enough, what the adapter needed that it did
not have, the per-question site cost, and the import picture. Today `NeuralGraph.research.coordination.core` loads
neither numpy nor `NeuralGraph.llm_backend`, and `NeuralGraph.research.retrieval.tesseract` loads both (Appendix A).
`hq.py` and `wire.py` must import with none of numpy, `NeuralGraph.llm_backend` or `NeuralGraph.research.retrieval`
loaded. `publish.py` loads the fabric service, which already loads numpy through `NeuralGraph.chat_memory`, the chain
the fabric guard documents; it must still load neither `NeuralGraph.llm_backend` nor `NeuralGraph.research.retrieval`.

## 2. What HQ knows, what sites return, what leaves

### 2.1 What HQ knows when it routes

| HQ may use | Source |
|---|---|
| The org config: site ids and unit paths | `detect/org.py` |
| Each site's descriptor (2.3) | `wire.request("describe")` |
| The cells visible at the candidate's `as_of`: per site, entity type, entity id, predicate, ISO week and channel, `n`, `n_roots` and `n_reporters` as an int at least k or `'<k'`, and `res_conf_min` when `n` is an int | `CollectiveStore`, run channel X (codes and text_only) |
| The detector candidate (2.2) | the shared candidate list |
| The question body HQ itself built | `pushdown/questions.py` |

HQ's router never uses: any verdict (of this question or of an earlier one; the router is stateless across
candidates and picks all m sites before any is asked), the plant spec (except arm O, labelled an oracle), any record,
narrative, person or reporter, any site graph, node id or Tesseract score, any site-side diagnostic, or any label.

### 2.2 What the router receives (plan item 3.3)

A detector candidate from the X run: `key`, `entity_type`, `entity_id`, `predicate`, `run_channel`, and its
`snapshot`: `week`, `as_of`, `window`, `supporting_sites`, `contributing_sites` and `lineage` (cells HQ already
holds). The routers read the key, the snapshot's `as_of` and window start (to build the question) and
`supporting_sites`. The rest comes from the cell store. The snapshot score orders the candidate list, which every arm
shares. Rule-only candidates (no snapshot) are left out. An HQ analyst question, the other option in 3.3, is not tested
here.

### 2.3 What each site returns

**The descriptor**, once per site, exactly the fields of the coordination package's `CapabilityDescriptor`, as canonical
JSON in a closed schema:

| Field | Value |
|---|---|
| `capability_id` | `pushdown:<pack id>:<pack config_hash>` |
| `node_id` | the site id |
| `description` | the fixed sentence `Answers pushdown questions over this site's own records.` |
| `query_types` | the pack's question template ids |
| `policy_scope` | `["pushdown:question"]` |
| `availability` | `available` |

It holds nothing derived from records. A test requires it to be identical across seeds and worlds for a site.

**The verdict**, per question, the shipped closed spec (`ARCHITECTURE.md` 15.2): `verdict` (`confirm`, `refute`,
`unknown`), `reason` (null, `budget` or `no_secret`), `window`, `support_bucket`, `roots_bucket`,
`reporters_bucket`, `entity_records_bucket`, `newest_week`, `evidence_ref` (16 hex), `truncated`, `quality`,
`secret_mode`, `verdict_id`, and the pack, pack hash, site and question id.

### 2.4 What leaves a site, and k

| Leaves | Suppression |
|---|---|
| Weekly cells (existing) | k = 3: each of `n`, `n_roots`, `n_reporters` is an int at least 3, else `'<k'` |
| Verdicts | counts only as buckets `'<k'`, `'3-9'`, `'10-49'`, `'50+'`; one opaque `evidence_ref` per confirm or refute |
| The descriptor | fixed; no counts |

Nothing else leaves: no record, narrative, person, reporter, record ref, node id, Tesseract rank or score, adapter
claim, per-record judge reply or exact count. A confirm with support `'<k'` is a weak confirm, and the gate does not
count it (D1). The residual disclosures of verdicts that `LEAKAGE.md` sections 7 and 9 describe still apply.

**Beyond what the pipeline already lets leave**, the routing uses one new thing: the descriptor. It carries no
record-derived value. Arm O also reads the plant spec; it is an oracle and only bounds the others (section 4).

## 3. The world

### 3.1 Pack, sites, weeks, seeds

| Setting | Value | Source |
|---|---|---|
| Pack | `device_quality` (illustrative, same author as the code) | Appendix A |
| Sites | all 6: `plant-ashvale`, `plant-brindlemoor`, `plant-corrowfield`, `plant-fennick` (English), `werk-dornhagen`, `werk-erlenbruch` (German) | Appendix A |
| Weeks | 52 from 2024-01-01, the pack's start | Appendix A; *chosen* to match the plant spec |
| Evaluation weeks | indices 20 to 51 | *chosen* to match the plant spec |
| Grace | 4 weeks | *chosen*; the X1 smoke prereg uses 4 too (Appendix A) |
| Seeds | 1 to 20; the world seed is the seed; the plant uses `plant.py`'s own seeded RNG | *chosen* |
| Detection | run channel X, the pack's frozen detectors (window 8, baseline 26, minimum history 12, `min_sites` 2) | Appendix A |
| Gate | the pack's pushdown block: 2 confirming sites, 3 independent roots, 3 independent reporters, freshness 42 days | Appendix A |

On seed 1 the planted world held 3,661 records and the no-plant world 1,261. Over two runs each, the shipped pipeline
took 3.2 to 3.9 s on the planted world and 2.1 to 2.3 s on the no-plant world, and detection 0.6 to 0.9 s and 0.4 to
0.5 s (Appendix A). Twenty seeds of both worlds are feasible. The handoff measured Tesseract at 272 ms per query at 600
nodes with the hash embedder; the run will report its own figures.

### 3.2 The plants

The spike reuses `packs/data/device_quality/fixtures/plant_e2_smoke.json` unchanged (sha256 `42f868eb…501c85c4`, full
value in Appendix A). It was written for E2 before this spike, so its placement cannot have been tuned to any router
here. Its own note says it was built so that this world gives at least 60 detector candidates per seed for seeds 1 to
5. Its `planter_saw_detector_code` is true.

| What | Value |
|---|---|
| Patterns | 85, each at 2 sites (62) or 3 sites (23) of the 6 |
| Visibility, language | all `narrative_only`, all English |
| Rate | 2 records per site per week |
| Length | 4 weeks (27), 5 weeks (25), 6 weeks (33); starts at weeks 20 to 48, ends by week 51 |
| Entity types | component 42, product 31, lot 12 |
| Decoys | 10: 4 `single_reporter`, 3 `cross_site_unmarked_copies`, 3 `high_base_rate_everywhere` |

`plant.py` places each pattern's records at exactly its listed sites, each week of its span, each with the planted
mention only (one affirmed template naming the exact id, plus filler sentences). Planted ids are in each counted
site's master data (`check_plant`).

### 3.3 The no-plant control

Each seed also runs the same generated world without the plant, through the same pipeline, detection, arms, gate and
fabric. It serves twice: for chance finds (3.5) and for the false-conclusion bar (5.2).

### 3.4 The candidates

Per world and seed, as E2 does: the detector candidates of the X run whose first candidate week is an evaluation week,
ordered by (snapshot score descending, then sha256 of `tie_salt|key`), the first 60 (*chosen*, E2's default). A world
with fewer uses all it has; the count is reported. Each candidate is asked at its snapshot's `as_of`. Every arm gets
this one list. Its sha256 is recorded.

A candidate is **true for pattern p** when its key is p's key and its snapshot week lies in p's found window,
`[start, min(end + grace, week 51)]`. The plant spec refuses a key planted twice, so a candidate is true for at most
one pattern.

The planted world's pool will be mostly true candidates: the E2 rehearsal on this spec found 247 of 300 true
(`ARCHITECTURE.md` 15.7, Appendix A). That is why the false-conclusion bar sits on the no-plant world.

### 3.5 What "found" means

Pattern p, seed s, arm a. p is **found** when the fabric of seed s's planted world holds, in arm a's organisation, an
active raw note by agent `hq` that:

1. is the embedded memory of a `collective.conclusion` event for a candidate true for p (its metadata names p's entity
   type, entity id and predicate, and a snapshot week inside p's found window);
2. has `value` `supported` (the gate's status);
3. has lineage that the fabric reconstructs (`evidence.reconstructable` true), whose `source_event_ids` include
   `collective.verdict` events from at least 2 distinct routed sites (the pack's `min_confirming_sites`), each with
   verdict `confirm` and a support bucket other than `'<k'`;
4. and each of those verdict events carries the sha256 of a verdict row from that site in HQ's receive log and in
   that site's egress log.

A find is a **chance find** when, in the same seed's no-plant world, the same arm (for U, the same subset) has a
`supported` conclusion on p's key whose snapshot week is in p's found window and no later than the planted world's.
**Found net** is found and not a chance find, as X1 counts it. The primary statistic uses found net.

The scorer reads only the fabric, the two logs and the plant labels. It never reads HQ's internal tables.

## 4. The arms, at equal budget

**The budget is m = 3 sites per candidate** (*chosen*, fixed now). The gate needs 2 confirming sites, so m is at least
2. It must be below 6, or every arm asks everyone. At m = 3, a uniform draw covers both holders of a 2-site pattern in
4 of the 20 possible site triples, and at least 2 holders of a 3-site pattern in 10 of 20 (Appendix A). Random is not
starved, and m = 3 halves the questions.

Every arm gets the same candidates, the same questions at the same `as_of`, the same site answers, the same gate and
the same gate roles. Only the choice of sites differs. Every route of R, R1, R0, O and P, and every U subset, has
exactly min(m, |E|) sites, where E is the eligible set from the descriptors (all 6 when every site is available).

Ties in any ranking break by `h(site) = sha256(tie_salt | question_id | site)`.

| Arm | Role | How it picks sites |
|---|---|---|
| **R, routed** | primary | Reciprocal rank fusion of four HQ-side rankings, constant 60, equal weights. (1) `key_now`: the lower-bound record count of the key's cells in the question window, `'<k'` counting 1. (2) `supporting`: 1 for a site in the snapshot's `supporting_sites`. (3) `entity_span`: the entity's lower-bound cell volume, any predicate, over `[window start − 26 weeks, window end]` (the shipped sibling signal). (4) `type_span`: the same for the entity type. Per ranking, only sites with a value above 0 get a rank, by (value descending, h). Fused score = Σ 1 / (60 + rank). Order by (fused descending, h); take the first m eligible |
| **U, random** | primary comparator | Every m-site subset of the same eligible set (20 subsets when all 6 sites are eligible), each run as its own route. U's result is the exact mean over the subsets: the expectation of a uniform random choice, with no luck of one draw |
| A, every site | context | All eligible sites. Not equal budget; shows what verification can confirm at all |
| R1, key only | secondary | Ranking (1) alone |
| R0, cell-blind | secondary | Rankings (3) and (4) with the key's own cells in the question window subtracted; no (1), no (2). Measures routing without the cells that raised the candidate |
| O, oracle | context | **Reads the plant spec.** For a true candidate, its pattern's planted sites first (all of them, since no pattern has more than 3), filled by R's order; else R's order. Bounds what any 3-site router could find. Never part of a criterion |
| P, placebo | check | R's algorithm on a permuted view: per candidate a permutation of E seeded by the sha256 of `placebo`, the seed and the question id, and each site is scored with another site's signals. Expected to match U. Shows whether the router's code path, ties included, favours some sites by itself |

**The answer matrix.** A site's answer to a question depends only on the question, its records, its secret and, when a
budget binds, the day's earlier questions. The run asks every site every question first (the A pass, in `as_of`, key
and site order). If that pass has no `budget` unknown in a world, every arm reads those same answers: a site re-sends
its stored verdict for a question it answered before (step 2 of 1.4), and nothing differs by arm. If the A pass has any
`budget` unknown in a world, that world re-runs with fresh site state per arm and per U subset, so each meets the
budget as it would alone. The run file records which mode ran. A check requires each (question, site) verdict to be
byte-identical across the arms that asked it.

## 5. The pre-registered criterion

### 5.1 Primary

For each seed s, `F_a(s)` is the number of planted patterns found net by arm a. For U it is the mean over the 20
subsets.

- **Statistic:** the mean over the 20 seeds of `D(s) = F_R(s) − F_U(s)`.
- **Interval:** `mycelic.collective.stats.paired_bootstrap(F_R, F_U, B=10000, seed="routing-spike:primary",
  alpha=0.05)`: the 20 seeds are resampled with replacement 10,000 times, the mean paired difference is taken each
  time, and the interval is the 2.5th and 97.5th percentiles (linear interpolation, `stats.percentile`).
- **Pass bar:** the interval's lower end is above 0.

### 5.2 No-plant control

For each seed, `n(s)` is the number of candidates in the no-plant world and `FC_a(s)` the number of them that arm a
concluded `supported` in the fabric (U: mean over subsets). Each one is a false conclusion: nothing was planted.
`r_a(s) = FC_a(s) / n(s)`, or 0 when `n(s)` is 0.

- **Reported:** for every arm, the total of `FC`, the total of `n`, the pooled rate, and the per-seed rates.
- **Interval:** `stats.paired_bootstrap(r_R, r_U, B=10000, seed="routing-spike:noplant", alpha=0.05)`.
- **Bar:** the interval's upper end is at most 0.05 (*chosen*). In words: routing may raise the share of background
  candidates that end `supported` by at most 5 points over random choice.

### 5.3 The verdict

| Verdict | When |
|---|---|
| **Pass** | every integrity check in 5.5 holds, the primary lower end is above 0, and the no-plant upper end is at most 0.05 |
| **Fail** | every integrity check holds, and the primary lower end is at or below 0, or the no-plant upper end is above 0.05 |
| **Withheld** | any integrity check fails. No pass or fail is claimed. The defect is fixed and the run repeats under a new run id with every setting here unchanged. Both runs are reported |

A primary fail says routed holder selection did not find more planted patterns than random choice at m = 3. A
no-plant fail says routing bought its finds by confirming background. Either one means plan item 3.2's done-criterion
is not met. A fail is recorded as it comes out.

To read a fail: if A and O also find few patterns, the loss is on the shared path (site retrieval, reading, the gate
or the fabric), not in the router. If O finds many and R few, the router is at fault.

### 5.4 Secondary results (reported, no bar, not used for the verdict)

Each arm comparison a − b uses its own `paired_bootstrap` over seeds, B = 10,000, seed string
`routing-spike:<a>-<b>`, with `:2-site` or `:3-site` appended for a stratum:

- R1 − U, R0 − U and P − U;
- R − U by stratum: patterns at 2 sites, patterns at 3 sites;
- A and O found counts, R − A and R − O;
- false conclusions in the planted world (`supported` on candidates true for no pattern), per arm and per decoy class;
- site side: verdict agreement with the shipped `SiteVerifier` on the same questions; the in-window records the shipped
  retrieval finds that fall outside Tesseract's top 50 (harness-side counts; HQ never sees them); Tesseract ranking
  time per question (median and 95th percentile) and nodes per site;
- the bytes of each crossing artifact type, and HQ's exported bytes per coordinator query.

### 5.5 Integrity checks (any failure withholds the verdict)

1. **Leakage.** `mycelic.collective.leakage.scan` over every byte that crossed a site boundary in each world
   (questions, verdicts, descriptors), against that world's narratives with an empty canary manifest, as E2 does.
   The overlap must be 0.
2. **Crossing types.** Only `question` in, and `verdict` and the descriptor out (cells as before). Every question and
   verdict passes `check_artifact` on both sides. Every descriptor passes the closed schema and is constant per site.
3. **HQ imports.** `hq.py` and `wire.py` import none of: `research.routing_spike.site_process`,
   `NeuralGraph.research.retrieval`, `NeuralGraph.llm_backend`, numpy, `mycelic.collective.edge.records`,
   `edge.site`, `edge.verify`, `edge.extract`, `mycelic.collective.evaluate`, `packs.generator`. Checked statically and
   in a fresh interpreter. `publish.py` follows 1.7.
4. **Router blindness.** Every router's ranking is byte-identical when the plant spec is absent, when the site stores'
   narratives are rewritten after the cells were sent, and when the site graphs are deleted.
5. **Shared candidates.** One candidate list per seed and world, its sha256 recorded, read by every arm.
6. **Equal budget.** Every route has the size section 4 gives.
7. **Answer identity.** In the shared-answer mode, each (question, site) verdict is byte-identical across the arms
   that asked it. In the per-arm mode (section 4), the same holds for every pair in which neither verdict is a
   `budget` unknown.
8. **Fabric.** Every conclusion counted as found passes 3.5 items 3 and 4.
9. **Equivalence.** The site process with the shipped retrieval swapped in returns `SiteVerifier.answer`'s bytes on
   seed 1's planted-world questions.
10. **Code and settings.** The run's code hash and settings match `docs/collective/routing_spike/prereg.json`, which
    is committed before the run. No uncommitted change under `research/routing_spike/`,
    `NeuralGraph/research/coordination/`, `NeuralGraph/research/retrieval/` or the pinned collective paths.

### 5.6 What is recorded

`docs/collective/routing_spike/prereg.json` (written by `run.py prereg`, committed before `run.py run`) holds every
setting of section 8, the code hash, the pack hashes and the plant spec's sha256. `run.py run` refuses a missing,
uncommitted or mismatched prereg. The result file holds per seed and arm the found, found-net and chance counts, the
no-plant counts, every route, gate status and verdict sha256, the statistics, the integrity checks and the verdict,
stamped `synthetic: true` and `measurement: false`, with no record text and no record ref. A results section is added
to this page from that file, quoting the command that made it.

## 6. How the test could pass trivially or be unable to fail

**The honest expectation first.** On this world a pass is close to guaranteed by construction. The planted narratives
are the pack's own templates, which the lexical extractor reads. So each holder site emits cells of the key, and R's
`key_now` ranking points at the holders. The verdict reader is the lexical judge, which shares its machinery with the
extractor that made the cells (`ARCHITECTURE.md` 15.4: every claim a site stored makes the judge answer yes on that
record). So "the site's cells show the key in the window" and "the site confirms" are nearly the same event. Routing
on `key_now` is routing on a k-suppressed copy of the reader's own evidence. That is the trap of routing
on the same signal that defines found. It is not an oracle: HQ legitimately holds the cells. But a pass on this world
shows only that cells mark holders when holders leave cells. It does not show that a router can find holders the
cells do not mark, which is the case the theory needs.

What the design does about it:

- It says so here, before the run, and section 7 reads a pass accordingly: weak evidence for decision 2.
- R0 routes without the key's own window cells. Planted records carry only the planted mention, so R0 should sit near
  U on this world. Its interval shows how much of R's gain is the key's own cells.
- Found is scored against the plant spec's truth, net of the no-plant world, and must come through the fabric. The gate
  reads verdicts only, never cells.
- A fail stays informative. Because a router pass is expected, a fail points at a defect on the path, and A and O say
  where.

| Risk | What it would do | How the design blocks or exposes it |
|---|---|---|
| Oracle routing: the router reads the plant spec, records, narratives, site graphs, Tesseract scores, site-side diagnostics or labels | Passes by construction; the test means nothing | HQ code receives only cells, candidates and descriptors (2.1); import check (5.5.3); blindness test (5.5.4). O is the only arm that reads the plant spec, it is labelled, and it is in no criterion |
| Leakage: adapter claims, record refs, node ids, text or exact counts cross | HQ could route on them; privacy breaks | Only question, verdict and descriptor cross (5.5.2); the adapter's claims stay in the site process; scan overlap must be 0 (5.5.1); the process-mode test shows HQ gets only bytes |
| Routing on verdicts: ask, see a confirm, ask more; or reuse earlier questions' verdicts | Selects on the outcome | One-shot selection before any site is asked; the router keeps no state across candidates; the saved trace shows `route_selected` first |
| Routing on the same signal as found | Pass by construction | The paragraphs above: stated up front, R0, scoring against truth through the fabric |
| Unequal budget | R wins by asking more | Every route's size asserted (5.5.6) |
| Different candidates per arm | R gets easier candidates | One hashed list per world (5.5.5) |
| A starved random arm: one unlucky draw, a smaller pool, worse ties | U loses for reasons other than routing | U is the exact mean over every subset of the same eligible set; same gate roles; P checks tie and code-path bias |
| Credit for background or decoys | R confirms everything and "finds" more | Scored per pattern at its key and found window; chance finds removed with the no-plant world; the no-plant bar (5.2) |
| Answers that differ by arm (budget, caching) | One arm gets better answers | The answer-matrix rule (section 4) and the identity check (5.5.7) |
| Scoring from HQ's own tables | The fabric path is never tested | The scorer reads only the fabric, the logs and the labels; the lineage cross-check (3.5, 5.5.8) |
| Settings tuned after seeing results | A pass found by search | This page and `prereg.json` are committed before code that runs the experiment; `run` refuses dirty code or a mismatched prereg; the plant spec predates the spike |
| A statistic that cannot fail: m at least 6, a difference that cannot go negative, a degenerate interval | Pass guaranteed | m = 3 of 6; `D(s)` is signed; a unit test feeds the statistic an inverted router (interval below 0) and a no-signal router (interval containing 0); per-seed values are reported |
| A test that cannot pass: U drawn only among contributing sites, or a shared path that loses every pattern | Fail for reasons other than routing | U draws from every eligible site; A and O expose a broken shared path |

## 7. What a result would tell, and what it would not

**Decision 1 (where Tesseract runs).** The spike shows whether a site-side process with a two-kind byte interface is
enough to put Tesseract on the path, with the standard-library guards untouched. It reports what the adapter lacked
(for example, its default claim content is the node's text, which must never leave, so a projection is required), the
per-question cost at the site, and the import picture of each side. It does not choose between options (a) and (b).

**Decision 2 (what Tesseract routes when nobody asks).** The spike measures detector candidates routed to holder sites
against random holders at equal budget, with R1 and R0 separating the key's own cells from context, strata for 2- and
3-site patterns (handoff Q5, "more than 2 holders"), and the cost in false conclusions. It does not test HQ analyst
questions.

**What it does not show:**

- No model reads anything. The reader is the lexical judge, coupled to the cell extractor (section 6).
- Holders are visible in cells by construction. Routing when some holders' evidence escapes the cells (a model reads
  what the counter missed) needs a model reader (R002 first) and a world built for it. That is the next test, not this
  one.
- The hash embedder ranks by shared words. Tesseract's ranking with real embeddings may differ (decision 7).
- One illustrative pack, 6 sites, m = 3, a same-author plant spec whose planter saw the detector code. Not the 60-site
  state split.
- The fabric runs in process with the in-process transport, not the production broker.
- The descriptor is outside the Boundary's specs (1.3).
- `measurement: false` throughout.

## 8. Frozen settings

`run.py prereg` writes these values; a test compares its constants with this table.

| Setting | Value |
|---|---|
| Pack | `device_quality` |
| Sites | all 6 of the pack's generator, in its order |
| Weeks | 52, from 2024-01-01 |
| Evaluation weeks | indices 20 to 51 |
| Grace weeks | 4 |
| Seeds | 1 to 20 |
| Plant spec | `mycelic/collective/packs/data/device_quality/fixtures/plant_e2_smoke.json`, sha256 in Appendix A |
| Run channel | X |
| Tie salt | `routing-spike-v1` |
| Candidates | detector candidates with an evaluation first candidate week, top 60 by (score desc, sha256(salt\|key)) |
| Question | at the snapshot's `as_of`; `question_window` and `build_question` |
| Site graph | SQLite storage, one session per site, one MESSAGE node per own record, `fake_embedding` with 256 dimensions |
| Site retrieval | Tesseract over the whole session; keep own records received in the window; first L = 50 |
| Site reader | `edge.verify.lexical_judge`, rules `edge.verify.decide`, seeded-demo secret with the world seed |
| Budget m | 3 |
| Arms | R, U (primary); A, R1, R0, O, P (context, secondary, check) |
| Rank fusion | reciprocal rank, constant 60, equal weights over `key_now`, `supporting`, `entity_span`, `type_span` |
| Ties | sha256(`tie_salt` \| `question_id` \| site) |
| Gate | `pushdown.gate.evaluate` with the pack's pushdown block; roles by D3 |
| Fabric | in-process service and transport; one organisation per arm and per U subset; agent `hq` |
| Primary | mean over seeds of found-net R − U; `paired_bootstrap`, B = 10,000, seed `routing-spike:primary`; pass when the lower end > 0 |
| No-plant | false-conclusion rate R − U; `paired_bootstrap`, B = 10,000, seed `routing-spike:noplant`; bar: upper end ≤ 0.05 |
| Secondary | `paired_bootstrap`, B = 10,000, seed `routing-spike:<a>-<b>`, plus `:2-site` or `:3-site` for a stratum |

## 9. Build list

1. `wire.py` with its tests: descriptor schema, constant descriptors, both endpoints passing bytes only.
2. `site_process.py` with the equivalence test (5.5.9) and the process-mode test (1.7).
3. `hq.py` with tests: the fusion arithmetic on a hand-built HQ view, ties, R1, R0, P, U's 20 subsets, equal budget,
   the import check (5.5.3) and the blindness test (5.5.4).
4. `publish.py` and `score.py` with tests: a conclusion's lineage cites its verdict events; the scorer rejects a
   conclusion whose cited sha256 is not in the receive log; the statistic can fail (section 6, last rows).
5. `world.py` and `run.py`; `run.py prereg`; commit `prereg.json`; then `run.py run` and `run.py score`.
6. Suites run on every change: `NeuralGraph/tests`, `tests/mycelic` (the guards, pushdown, gate, edge, evaluate and
   the x3 pins in particular), `tests/market`, and `tests/routing_spike`. No test is skipped or loosened.

## Appendix A. Commands behind the numbers

Run in this worktree on commit `95b2a1e`, Python 3, offline.

**Pack settings and the plant spec** (sites, languages, start date, k, buckets, budgets, `verify_max_records`,
detector settings, pushdown block, plant composition, sha256). The script, run with
`PYTHONPATH=. python3 facts.py`:

```python
import collections, hashlib, json
from pathlib import Path
from mycelic.collective.edge.egress import verdict_buckets
from mycelic.collective.packs.loader import load_pack

base = Path("mycelic/collective/packs/data/device_quality")
pack = load_pack("device_quality")
gen, egr = (json.loads((base / f).read_text()) for f in ("generator.json", "egress.json"))
det, que = (json.loads((base / f).read_text()) for f in ("detectors.json", "questions.json"))
print("sites", [(s["id"], s["language"]) for s in gen["sites"]], "start", gen["start"])
print("k", egr["k"], "buckets", verdict_buckets(pack), "close_lag_days", egr["close_lag_days"],
      "min_window_weeks", egr["min_window_weeks"], "verify_max_records", egr["verify_max_records"],
      "budget/entity/day", egr["question_budget_per_entity_per_day"],
      "entities/site/day", egr["question_entities_per_site_per_day"], "require_master_data", egr["require_master_data"])
print("detectors window", det["window_weeks"], "baseline", det["baseline_weeks"], "min_history", det["min_history_weeks"],
      "burst.min_sites", det["burst"]["min_sites"], "cooccurrence.min_sites", det["cooccurrence"]["min_sites"],
      "alert_budget", det["alert_budget_per_week"], "cooldown", det["cooldown_weeks"])
print("pushdown", que["pushdown"])
print("config_hash", pack.config_hash)
path = base / "fixtures" / "plant_e2_smoke.json"
spec = json.loads(path.read_text()); pats = spec["patterns"]
print("plant sha256", hashlib.sha256(path.read_bytes()).hexdigest())
print("patterns", len(pats), "sites per pattern", dict(collections.Counter(len(p["sites"]) for p in pats)))
print("visibility", dict(collections.Counter(p["visibility"] for p in pats)),
      "language", dict(collections.Counter(p["language"] for p in pats)),
      "rate", dict(collections.Counter(p["rate_per_week"] for p in pats)),
      "weeks", dict(sorted(collections.Counter(p["weeks"] for p in pats).items())))
print("types", dict(collections.Counter(p["entity_type"] for p in pats)))
print("start_week min/max", min(p["start_week"] for p in pats), max(p["start_week"] for p in pats),
      "end max", max(p["start_week"] + p["weeks"] - 1 for p in pats))
print("decoys", len(spec["decoys"]), dict(collections.Counter(d["class"] for d in spec["decoys"])))
print("planted_by", spec["planted_by"], "| saw detector code", spec["planter_saw_detector_code"])
```

Output:

```
sites [('plant-ashvale', 'en'), ('plant-brindlemoor', 'en'), ('plant-corrowfield', 'en'), ('werk-dornhagen', 'de'), ('werk-erlenbruch', 'de'), ('plant-fennick', 'en')] start 2024-01-01
k 3 buckets ('<k', '3-9', '10-49', '50+') close_lag_days 14 min_window_weeks 6 verify_max_records 500 budget/entity/day 5 entities/site/day 50 require_master_data True
detectors window 8 baseline 26 min_history 12 burst.min_sites 2 cooccurrence.min_sites 2 alert_budget 5 cooldown 4
pushdown {'min_confirming_sites': 2, 'min_independent_roots': 3, 'min_independent_reporters': 3, 'freshness_days': 42, 'max_sibling_sites': 2}
config_hash 9de50cd4f690ba8876c7e1abb62f6fca886d1c5b96c00b048fa54d0c0c8a9d2d
plant sha256 42f868eb2e3127a024b0c66457ae17edeba751f6fd1a57500a726e34501c85c4
patterns 85 sites per pattern {2: 62, 3: 23}
visibility {'narrative_only': 85} language {'en': 85} rate {2: 85} weeks {4: 27, 5: 25, 6: 33}
types {'lot': 12, 'product': 31, 'component': 42}
start_week min/max 20 48 end max 51
decoys 10 {'single_reporter': 4, 'cross_site_unmarked_copies': 3, 'high_base_rate_everywhere': 3}
planted_by mycelic engineering (same author as the detector code) | saw detector code True
```

**The plant spec's own note:**

```
$ python3 -c "import json; print(json.load(open('mycelic/collective/packs/data/device_quality/fixtures/plant_e2_smoke.json'))['notes'])"
E2 smoke for the G6 tests: synthetic, same-author, not blind, never a result. Built so that an X1 prereg with 6 sites, 52 weeks, evaluation weeks 20 to 51 and seeds 1 to 5 yields at least 60 detector candidates per seed.
```

**World size and pipeline time on seed 1** (feasibility only; no routing code). The script, run with
`PYTHONPATH=. python3 time_seed.py 1 planted` and `... 1 control`:

```python
import sys, tempfile, time
from pathlib import Path
from mycelic.collective.detect.detectors import detect
from mycelic.collective.evaluate.baselines import run_pipeline, world_weeks
from mycelic.collective.evaluate.plant import load_plant, plant
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import load_pack

pack = load_pack("device_quality")
spec = load_plant("mycelic/collective/packs/data/device_quality/fixtures/plant_e2_smoke.json", pack)
seed, planted = int(sys.argv[1]), sys.argv[2] == "planted"
t0 = time.time()
world = generate(pack, seed, 6, 52)
recs = list(world.records) + (list(plant(world, spec, pack).records) if planted else [])
site_ids = [s["id"] for s in pack.generator["sites"][:6]]
t1 = time.time()
with tempfile.TemporaryDirectory() as d:
    p = run_pipeline(pack, recs, site_ids=site_ids, master_data=world.master_data,
                     weeks=world_weeks(pack.generator["start"], 52), workdir=Path(d))
    t2 = time.time()
    res = detect(p.store, as_of=p.as_of, run_channel="X", tie_salt="probe")
    t3 = time.time()
    p.close()
print(f"seed={seed} planted={planted} records={len(recs)} generate+plant={t1 - t0:.1f}s "
      f"pipeline={t2 - t1:.1f}s detect={t3 - t2:.1f}s")
```

Output of two runs of each (the record counts are identical; the times vary):

```
seed=1 planted=True records=3661 generate+plant=0.3s pipeline=3.2s detect=0.6s
seed=1 planted=True records=3661 generate+plant=0.4s pipeline=3.9s detect=0.9s
seed=1 planted=False records=1261 generate+plant=0.2s pipeline=2.1s detect=0.5s
seed=1 planted=False records=1261 generate+plant=0.2s pipeline=2.3s detect=0.4s
```

**What each side loads:**

```
$ python3 -c "
import sys
import NeuralGraph.research.coordination.core as c
print('coordination.core loads numpy:', 'numpy' in sys.modules, 'llm_backend:', 'NeuralGraph.llm_backend' in sys.modules, 'tesseract:', 'NeuralGraph.research.retrieval.tesseract' in sys.modules)
import NeuralGraph.research.retrieval.tesseract as t2
print('after tesseract: numpy', 'numpy' in sys.modules, 'llm_backend', 'NeuralGraph.llm_backend' in sys.modules)
"
coordination.core loads numpy: False llm_backend: False tesseract: False
after tesseract: numpy True llm_backend True
```

Each module imported alone in a fresh interpreter, printing whether numpy was loaded:

```
$ python3 -c "
import importlib, subprocess, sys
mods = ['mycelic.collective.pushdown.gate', 'mycelic.collective.detect.store', 'NeuralGraph.research.coordination.core', 'NeuralGraph.chat_memory', 'mycelic.retrieval', 'mycelic.service']
for m in mods:
    code = 'import sys, importlib; importlib.import_module(%r); print(%r, \"numpy\" in sys.modules)' % (m, m)
    print(subprocess.run([sys.executable, '-c', code], capture_output=True, text=True).stdout.strip())
"
mycelic.collective.pushdown.gate False
mycelic.collective.detect.store False
NeuralGraph.research.coordination.core False
NeuralGraph.chat_memory True
mycelic.retrieval True
mycelic.service True
```

The HQ-side modules together:

```
$ python3 -c "
import sys
import mycelic.service, mycelic.collective.pushdown.gate, mycelic.collective.detect.store, NeuralGraph.research.coordination.core
print('numpy', 'numpy' in sys.modules, '| llm_backend', 'NeuralGraph.llm_backend' in sys.modules, '| retrieval pkg', any(m.startswith('NeuralGraph.research.retrieval') for m in sys.modules), '| chat_memory.llm', 'NeuralGraph.chat_memory.llm' in sys.modules)
"
numpy True | llm_backend False | retrieval pkg False | chat_memory.llm True
```

**The random-draw arithmetic:**

```
$ python3 -c "
from itertools import combinations
sites=range(6)
for h in (2,3):
    holders=set(range(h)); subs=list(combinations(sites,3))
    hit=sum(1 for s in subs if len(holders & set(s))>=2)
    print(f'h={h}: {hit} of {len(subs)} three-site subsets hold at least 2 holders = {hit/len(subs):.2f}')
"
h=2: 4 of 20 three-site subsets hold at least 2 holders = 0.20
h=3: 10 of 20 three-site subsets hold at least 2 holders = 0.50
```

**A rendered question and the bucket labels:**

```
$ python3 -c "
from mycelic.collective.packs.loader import load_pack
from mycelic.collective.edge.egress import verdict_buckets
from mycelic.collective.pushdown.questions import build_question, question_window, render_text
p=load_pack('device_quality')
print(verdict_buckets(p))
w=question_window(p, as_of='2024-09-01')
b=build_question(p, entity_type='lot', entity_id='L20046', predicate='crack', window=w, as_of='2024-09-01')
print(sorted(b)); print(render_text(p,b))
"
('<k', '3-9', '10-49', '50+')
['as_of', 'candidate_key', 'pack', 'pack_hash', 'params', 'question_id', 'schema_version', 'template_id', 'window']
In the last 6 weeks, how many of your records describe Crack or fracture for Lot L20046?
```

**Figures quoted from other files** (long lines cut with `...`):

```
$ grep -o '"grace_weeks":[0-9]*' docs/collective/x3/x1_smoke/prereg.json
"grace_weeks":4
$ grep -n "272 ms\|9.9–10.5 s\|+7.4 recall@10" docs/handoff/HANDOFF-2026-10-09.md
105:| Latency per query | 272 ms at 600 nodes; 2.6 s at 5,882; 8.7 s at 20,000 (flat scan) | ...
163:**Speed on real text** (unit wall time per record, server start included): a-0p5b 9.9–10.5 s, ...
267:| 2 | What Tesseract routes when nobody asks a question | ... (+7.4 recall@10). Test it against random holder choice |
$ grep -n "247 of" docs/collective/ARCHITECTURE.md
1571:... What the rehearsal does say is about the **pool**: 247 of
$ grep -n "def fake_embedding" NeuralGraph/chat_memory/llm.py
201:def fake_embedding(text: str, dim: int = 256) -> list[float]:
```
