# Roll-up debate, hardening benchmark 001: the plan, committed before any run

Status: committed alone, before the build is run on any world and before any debate. Synthetic EMERGENCE worlds,
development seed 0 only; `measurement: false`. The founder's order of 2026-10-16 (~09:27 UTC) sets the design and
the four steps; the status log's decisions of 2026-10-15 ~05:37, ~05:44 and ~05:53 UTC, which the founder confirmed
in that order, bound it. The edge calibration is `docs/research/hierarchy-sim/edge-calibration-seed0.json` (2d2418c).

## 1. What is built

A new package, `rollup/`, outside every frozen and pinned path (`research/**`, `mycelic/collective/**`, `tools/**`,
the lab's pinned files). EMERGENCE's generator (`research/mycelic/corpus.py` through
`research/e2e_ingest/emergence_world.generate`) and its labels and scorer functions (`gold_labels`,
`research/routing_spike/world.candidate_label`) are imported unchanged.

- **Edge.** Each site's edge writes memories with lineage and sends one abstract summary per closed week: entities,
  event kinds, weeks, distinct-event counts, lineage handles; no record id, reporter, quote or narrative text. A
  privacy guard refuses any other field, and a test fails on each kind of violation. At 10,000 users the edge is a
  code emulator calibrated from the calibration file (step a); no model reads records.
- **Gossip.** Weekly summaries travel peer to peer with no central store: push-pull anti-entropy over per-origin
  signed, hash-chained logs. Every node keeps lineage and repairs itself after loss.
- **Candidates.** Code forms a candidate story when the merged summaries show one entity with 2 or more distinct
  event kinds across sites, inside the evidence window, counting distinct lineage handles.
- **Debate.** Advocates (the sites holding evidence, each from its own summary), one skeptic, one rebuttal round.
- **Judge.** Rules supported, hypothesis or rejected with a reason; writes the week's discoveries with lineage and
  next week's watch list back to the edges.
- **One model interface, three backends:** a deterministic fixture (tests, and a code-only debate reported as an
  ablation), the lab's local models through llama-server (written and tested against a fake server; not run
  tonight), and the hosted stand-in `h-sim` run as this session's sub-agents, for small pilots only.

## 2. Fixed settings (no setting is changed after a result is seen)

| Setting | Value |
|---|---|
| Worlds | seed 0 at 200 users (step c); seed 0 at 10,000 users (steps b and d) |
| Weeks | 2024-W01 to 2024-W26, each processed when closed |
| Evidence window | the trailing 12 closed weeks |
| Event kind | the fine EMERGENCE predicate named by the record's lexicon phrase; its chain from the pack |
| Candidate rule | one entity, at least 2 distinct non-negated event kinds across all sites in the window |
| Story key | (entity, the chain holding most of the entity's distinct kinds; ties by distinct handles, then name) |
| Ranking | distinct lineage handles, then distinct sites, then sha256 of the story key |
| Weekly cap | 8 candidates per week, at both sizes |
| Re-debate | supported stories are not debated again; rejected ones return only with at least one new distinct handle; hypotheses return while new evidence arrives or while on the watch list |
| Skeptic attacks | copies counted as independent reports; reversed order; retraction or contradiction; staleness; a single source |
| Rounds | advocates, skeptic, one rebuttal, judge; each role batched once per week |
| Gossip | 15 nodes at 10,000 users; fan-out 2; 24 rounds per simulated week |

## 3. Scoring (unchanged functions)

- **Found:** a judge-`supported` discovery whose `(entity, chain)` key and week give `candidate_label(...) == "true"`.
  `recall_all` over Gold's discoverable patterns; `recall_rare` beside it.
- **Decoys accepted, two readings, both reported per class 1 to 5:** the shipped rule (a supported discovery on
  the decoy's facet-0 key inside its window) and every facet key (a supported discovery on any of the decoy's
  labelled keys inside its window). The stricter reading is the headline.
- **Background supported** is reported beside them.
- **Class against attack:** class 4 (duplicate inflation) against copies; class 2 (temporal scramble) against
  reversed order; class 5 (stale chain, later retracted) against retraction and staleness. Classes 1 (entity
  coincidence) and 3 (near-miss entities) have no attack named for them in the design, and are reported as such.
- **Comparators, as recorded, not re-run:** the geometry baseline, seed 0, 13 of 40 found with decoys accepted 5 of
  class 2, 9 of class 4 and 3 of class 5 by the shipped rule (`docs/research/EMERGENCE_ROOT_CAUSE_REPORT.md`, the
  seed-0 real run); and the judge without debate in the calibration, period 1 (W01 to W07): 12 supported, 2 on a
  Gold anchor inside its window, 9 on decoy anchors (anchor-level). The debate's period-1 figures are also given at
  that anchor level, so the two are read the same way.

## 4. The gate for step (d)

Step (d) runs only if step (c) shows the debate rejects decoys, read as both of these:

1. In weeks W01 to W07, fewer than 9 of the debate's supported stories sit on decoy anchors (the calibration
   judge's figure, read the same way).
2. Over all 26 weeks, of the debated candidates whose story key is a decoy's (any facet key), at least half end
   rejected or hypothesis rather than supported.

If either fails, step (d) does not run, and the report says which.

## 5. Gossip faults (step b)

All in one run at 10,000 users, seed 0, over 26 simulated weeks:

- **Node loss:** two nodes down for W05 to W08, one keeping its disk and one losing it.
- **Partition:** seven nodes against eight for W12 to W14.
- **Faults on every week:** duplicates (each delivery repeated with probability 0.2), reordering (random delay of
  0 to 3 rounds), message loss (probability 0.1), and one slow node whose deliveries take five times as long.

**Measured:**
- per week, the rounds until every live and connected node holds every summary;
- delivery, as the share of summaries held by every node at the end of each week and at the end of the run;
- lineage integrity: every per-origin chain verifies, with no gap, fork or bad signature, and every duplicate is
  applied once;
- whether every node computes the same candidate list from its merged state.

A tampered message is also injected, and must be refused.

## 6. Stops and labels

- If the stand-in's weekly limit is hit, the run stops and the report says where.
- Tokens are given two ways:
  - prompt and reply text, estimated at four characters per token;
  - the sub-agents' own reported usage, which includes their tool overhead.
- Every result is labelled synthetic and `measurement: false`. Development seed 0 only; no held-out seed is used.

## Amended 2026-10-16 ~10:20 UTC, after three plan reviews and before any run

Committed alone. No debate, pilot or gossip benchmark had run. The reviews raised blocking findings, and this
amendment answers them. The only code that had run was the build's calibration check of the edge emulator
(`docs/research/hierarchy-sim/runs/calibrate-check-seed0.json`, on the stage-B1 commit of the build branch). One
reviewer also ran a code-only probe of the candidate cap against Gold. Because that probe used Gold, no setting below
is chosen from it: the cap stays at 8, and the probe's finding goes to the founder as a decision.

### A1. The edge, its rates and the random seeds

- **Edge at both sizes.** Steps (c) and (d) both use the step-(a) code emulator, for all 26 weeks. Only the debate
  and the judge use h-sim. The calibration judge read h-sim edge memories over a 7-week block, while the debate reads
  emulated weekly summaries. Every comparison with it says so.
- **Keep rate.** Each lexicon event is kept with probability `recall_by_chain[chain]` from the calibration file.
  There are no false memories.
- **Copy marking.** A true copy is a kept memory whose event already appeared in an earlier kept memory at the same
  site. The calibration's edges marked 1,385 copies against 770 true ones (seed 0, 200 users, W01 to W13). So
  marking runs at two rates:
  - every true copy is marked, the rate being capped at 1;
  - each other kept memory gets a false copy mark at rate (1,385 − 770) / (3,099 − 770) = 615 / 2,329 = 0.2641. A
    false copy mark joins the memory to the newest earlier memory of the same (site, entity, kind), if one exists,
    so it no longer counts as a distinct event. With no earlier memory, the mark has no effect.
- **Negation marking.** It runs at 42 / 95 = 0.4421. An unmarked negated record counts as a positive memory.
- **Ablation.** A perfect-marking run (every true copy marked, no false marks, every negation marked) is reported
  beside the main one, from the fixture backend only.
- **Labels.** These rates are labelled "derived from the calibration plus world ground truth".
- **Random seeds.**
  - emulator salt `rollup-001` with seed 0;
  - gossip faults seed 0;
  - the slow node is the node with the lowest sha256 of its name.

### A2. Lineage handles

- **What a handle is.** A lineage handle is a keyed hash: HMAC-SHA256 under a company key that never leaves the
  company's nodes, truncated to 16 hex characters. It covers the memory's content fingerprint, which in the emulator
  stands for what an edge model computes from near-verbatim wording. It is never a record ref or a uid, nor an
  unkeyed hash of either. The guard refuses any handle equal to one of those, and a test pins that.
- **Why the key is company-wide.** Copies of one event at different sites share a handle, so the skeptic's copies
  attack can see echoes across sites. That reveals to peers in the same company only that two sites saw the same
  event, and the report says so.
- **What a summary carries.** Per (entity, kind, week), the distinct-event count and the copy-marked count, beside
  the handles. Each prompt carries counts and at most 8 handles per site entry.

### A3. h-sim, its scope and its limits

- **How it runs.** The founder's order of 2026-10-16 (~09:27 UTC) runs h-sim as this session's sub-agents for the
  advocate, skeptic, rebuttal and judge roles. That replaces the dedicated-secret workflow of the 05:53 decision,
  item 3: no key or secret is used.
- **What still binds from 05:53.**
  - Item 1: synthetic EMERGENCE worlds only; never a real record; never the accuracy loop (A004, J-cycles).
  - Item 2: the alias h-sim.
  - Item 4's prompt bound: code estimates each prompt at 4 characters per token plus 20% before it is sent, and
    refuses anything over 100,000 tokens. A batched prompt over the bound is split per candidate.
- **Spend.** The 20-dollar cap in item 4 governs paid API calls, and sub-agents make none. The binding limit is the
  stand-in's weekly limit, which the founder's order names. The run keeps a token ledger of prompt and reply text at
  4 characters per token, plus each sub-agent's reported usage when the harness exposes it (stated as unavailable
  otherwise). Weeks W01 and W02 of step (c) run first. Their tokens are measured, and the 26-week total and step
  (d)'s total are projected. No run starts whose projection would reach the weekly limit. If the limit is hit, the
  run stops and the report says where.
- **Isolation.**
  - A role sub-agent reads exactly its prompt file and writes exactly its reply file. Its only command is the run's
    `advance` command.
  - Every sub-agent transcript is audited afterwards for any other tool use. A role call with other tool use is
    void and is reported.
  - Before sending, every prompt is scanned for the words decoy, gold and label, and for record text, refs, uids
    and reporters.
  - Every reply passes the same privacy scan as the summaries before it is stored.
- **Simulation limit.** With roles batched, one sub-agent sees every evidence site's summary at once. Site-locality
  of the advocates is therefore simulated, not enforced, and the report says so.

### A4. Replies

- **Schema.** Each role replies in strict JSON keyed by story key.
- **Attack tags.**
  - The skeptic returns, per candidate, a subset of the tags `copies`, `reversed_order`,
    `retraction_contradiction`, `staleness` and `single_source`.
  - The judge returns one ruling (`supported`, `hypothesis` or `rejected`), one reason tag (from the same set, or
    `none`) and the story chain.
- **Bad replies.** A malformed or missing item gets one retry with the parse error. After that it is recorded as
  `no_ruling` for the judge, or an empty argument for the other roles. It is counted, and never treated as
  supported.

### A5. State, aggregator and watch list

- **Merged state.** In steps (c) and (d), the aggregator reads the merged state of every emitted summary (the oracle
  merge). Step (b) separately measures how far a faulted gossip node's state differs from it.
- **Watch list.** It is applied to the edges directly and does not travel through faulted gossip in (c) and (d).
- **Week records.** Each closed week's state, replies, rulings, watch list and ledger are written under
  `docs/research/hierarchy-sim/runs/<run id>/`. They are committed at least every 4 simulated weeks. The report is
  regenerated from those records, so a stop loses no recorded week.

### A6. Scoring, replacing the comparators of section 3

- **Found.** A judge-supported discovery whose key `series_key(ENTITY_TYPE, canonical_entity_id(anchor), chain)` and
  ruling week give `candidate_label(...)["label"] == "true"`, and whose lineage handles verify against the merged
  logs.
- **Every-facet reading (the headline).** A decoy d of class c is accepted when some supported discovery (key, week)
  has key in `d["keys"]` and `d["found_from"] <= week <= d["found_to"]`. A key that `candidate_label` labels
  `"true"` is never a decoy acceptance. The unwindowed count is given beside it, and a test pins that a supported
  decoy key outside its window is not counted.
- **Facet-0 reading.** `harness.py:1296-1316` is reproduced exactly, with a test.
- **Class 4.** It is counted, as in the e2e shipped rule. Totals are given with and without class 4.
- **Geometry baseline, seed 0 (as recorded).** 13 of 40 found. Each reading of the debate is compared only with the
  same reading of the baseline:

  | Reading | Class 1 | Class 2 | Class 3 | Class 4 | Class 5 | Total |
  |---|---|---|---|---|---|---|
  | Facet-0 rule | 0 | 5 | 0 | 9 | 3 | 17 of 50 |
  | Every facet key in the window | 5 | 5 | 6 | 9 | 3 | 28 of 50 |

  Sources: `docs/research/EMERGENCE_ROOT_CAUSE_REPORT.md:83`, `:223`, and its `decoy_window_check` output.
- **Paired comparison.** Against the baseline's found set: found by both, by the debate only, by the baseline only.
  Recall is also split into contradicted (13 at seed 0) and not contradicted (27) patterns, with the baseline's split
  beside it. A 2×2 table shows the retraction attack raised or not, against label true or class-5 decoy.
- **Anchor-level reading** (for the calibration judge only). A supported story entity e at ruling week w is "Gold in
  window" if e is a Gold pattern's anchor and `found_from <= w <= found_to`. Otherwise it is "decoy anchor" if e is
  the anchor of any facet record of any decoy. Entities are counted once. The calibration's 12 / 2 / 9 cannot be
  recomputed under this rule from committed files, so that comparison is approximate. Its differences in cadence,
  cap and edge input are stated.
- **Per decoy class and for true keys.** Debated n; matching attack raised n; ended rejected or hypothesis with the
  matching reason n; supported n. The true-key table gives each attack's false-alarm rate. Classes 1 and 3 are shown
  as "no targeted attack".
- **Miss decomposition.** Every Gold pattern ends in exactly one of: never qualified; qualified but outside the cap
  in every week of its window; debated and rejected (with reason); hypothesis at the end; supported outside its
  window; or found. Each decoy class is traced the same way, from entering the cap to its outcome. Both sizes are
  covered, with the qualifying and capped counts per week. Gold keys supported before `found_from` or after
  `found_to` are counted.
- **Statistics.** Every figure is a count over its denominator: one seed, one h-sim sample, no inferential claim.
  The h-sim sampling settings are stated.
- **The 10,000-user world.** It is generated with `generate()`, without `check_world_counts`, and its counts are
  recorded from `make_gold`. Step (d) has no recorded comparator. Before (d), the fixture backend runs all 26 weeks
  at 10,000 users with the cap and the re-debate rule, and reports:
  - the ceiling: the Gold keys ever debated inside their window;
  - the exact prompt characters per role per week.

  (d)'s recall is read against that ceiling. If (d) does not run, its tokens per week come from that dry run,
  labelled "prompt side only".

### A7. The gate for step (d), replacing section 4

Step (d) runs only if all three hold in step (c):

1. In W01 to W07, under the anchor-level reading:
   - the share of supported story entities on decoy anchors is below 9 / 12;
   - at least 2 supported entities sit on a Gold anchor inside its window.
2. Over W01 to W26, at least 5 debated story keys carry `candidate_label` "decoy". At least half of them have a
   final status after the W26 ruling of rejected or hypothesis.
3. That share is greater than the same share for debated keys labelled "true".

If the minimum in (2) is not met, the gate is "not determinable" and (d) does not run. The report gives every count
with its denominator, and states that the gate rests on one sample.

### A8. Gossip measures, sharpening section 5

- **Universe.** Every summary emitted so far. A down site's edge emits its missed weeks on recovery, from its own
  records.
- **Convergence.**
  - It is measured per connected component, as the rounds until every live node in the component holds every
    summary from the component. When it is not reached within 24 rounds, it is reported as more than 24 (censored).
  - Also reported: the rounds to heal after W14 ends, and after each lost node returns.
- **The disk-losing node.**
  - It crashes at round 1 of W05, before its W04 summary has fully spread.
  - On rejoin it pulls its own origin's chain head from peers before emitting, and any fork it causes is counted.
- **Candidate determinism.** For each node and week: whether its candidate list equals the oracle list, computed
  for scoring from every emitted summary. Mismatches are reported with their cause (missing summaries or other).
- **Slow node.** Its deliveries take 5 × (1 + uniform 0 to 3) rounds.
- **Authentication.**
  - Authentication is per-node HMAC under keys derived from one run secret. It is reported as "authenticated
    (shared-secret HMAC)", not "signed". Any node that can verify another origin could also forge it, so the tamper
    test covers an outsider only.
  - The injected set adds a replayed old message and a message claiming another origin.

### A9. Recorded elsewhere

- **The founder's order of 2026-10-16.** It is recorded in the status log's "Decisions received", with its scope:
  - the design;
  - h-sim as session sub-agents for the debate roles, on synthetic worlds only;
  - the four steps;
  - the stop on the weekly limit.
- **The shipped system is unchanged.** `rollup/` is not copied into any deploy image: the deploy Dockerfiles copy
  only `NeuralGraph` and `mycelic`.
- **Frozen paths, now named in full:** `research/**`, `mycelic/collective/**`, `tools/**`, `lab/**`,
  `docs/collective/replay/vehicles/pack/**`, `CHOICE-*.md`, `run-*.json` and `*-settings.json`.
