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
