# Morning handoff: overnight repair of 2026-10-09

_Final version, written 2026-10-09 11:22 UTC. Every number here is recomputed in `VERIFIED_RESULTS.md` from the run
directories. The ledger is `EXPERIMENTS.jsonl` (rows X000–X083)._

## 1. What you asked, and the short answer

You asked for:
- the benchmark rebuilt around the real system (ingestion → individual NeuralGraphs → hypergraph routing → discovery loop →
  commit gate);
- confirmed defects fixed;
- ≥ 80 % genuine end-to-end accuracy (stretch 90 %), with integrity first.

**Headline: there is no valid holdout accuracy number.** The frozen candidate ran once on the one pre-registered holdout world
(1,000 users, seed 101):
- The scorer marked 120/120 tasks correct, with 0 disclosures. The centralized baseline scored 114/120 on the same world.
- The frozen architecture gate rejected the run on **G7**: a claim-support update in tonight's product code leaves no audit
  record.
- Under the contract, a rejected run's score is not published as full-system accuracy, and the holdout cannot be repeated for
  this commit.
- On dev, the same code scores 120/120 with a valid gate. That is optimistic, because the candidate was developed on that world.

The ≥ 80 % target is therefore met on dev but **not demonstrated on a valid holdout run**.

- **Your suspicion was right.** Every published Mycelic accuracy number came from a numpy simulator that imports no
  implementation code (`custody/OLD_BENCHMARKS.md`). There was no hypergraph anywhere in the code.
- **What exists now:** Mycelic-E2E v1 (`BENCHMARK_CONTRACT.md`).
  - It feeds raw source records through the real connector path into one NeuralGraph store per person and per unit.
  - It routes questions through a real coordinator hypergraph.
  - It scores only what the asker can see, with an isolated scorer.
  - It compares against a fair centralized baseline.
  - An architecture gate (G1–G10) invalidates a run whose pipeline was bypassed or whose records do not add up.
- **Honest qualifiers:**
  - Every number uses the **deterministic provider**: lexical rules, no language model.
  - Everything ran **single-process in a cloud container**; the MacBook bridge never came up.
  - The holdout world is **1,000 users, not 10,000**. The 10,000-user hardened world was too slow for the time left; §6 has the
    measured reason.

## 2. Where everything is

- **Code:** the overnight commits sit on top of `claude/mycelic-implementation-vr034p@f96f263`.
  - They are in a clone at `/home/user/ng-impl`, branch `claude/friendly-mayer-y9f1vt`.
  - The frozen commit is `7f37551`; the candidate code is `7d2e63b`.
- **Designated remote branch:**
  - `claude/friendly-mayer-y9f1vt` is `main`-based, so the commits are mirrored there as a patch series in
    `research/mycelic_e2e/impl_patches/`, together with this directory.
  - The session's safety settings refused a force-push that would have rewritten the branch onto the implementation.
  - To get the tested tree: `git checkout -b e2e-overnight f96f263 && git am research/mycelic_e2e/impl_patches/*.patch`
    (README in `impl_patches/`).
- **Deliverables:**
  - `ARCHITECTURE_AUDIT.md`, `BENCHMARK_CONTRACT.md` (frozen, §9), `EXPERIMENTS.jsonl`, `VERIFIED_RESULTS.md`, this file;
  - reviews in `reviews/`, plans and analyses in `plan/`, the single-event trace in `audit/`, the custody record in `custody/`,
    scale probes in `perf/`, the live model check in `live/`, per-run score and gate files in `results/runs/`.
- **Sealed holdout bank:** `/root/sealed_holdout/templates_holdout.py`, outside the repository, sha256 `6a5eabd3…`.
  - It lives only in this container and is gone when the container is reclaimed. It is deliberately kept out of the
    repository, so that implementation agents never see it.
  - It was evaluated for exactly one candidate (`7f37551`).

## 3. What changed in the product

Every change was reviewed by an agent that did not write it (see `reviews/`).

| change | files | review |
|---|---|---|
| Hypergraph: `hyperedges`, typed `hyperedge_members` and `entity_registry`, written in the commit, conflict, discovery and heartbeat transactions; `traverse`, `incidence`, `rebuild` | `knowledge/hypergraph.py`, migration 0005 | REVIEW_WP1 (accepted after fixes F1–F11 and S1) |
| Ranked routing (entity and term incidence, department round-robin, audited) instead of the first 10 holders in registry order | `inquiry/routing.py`, `inquiry/service.py` | REVIEW_WP1 (C3 round-robin accepted in the C5 review) |
| Content-free term index: keyed hashes of rare words, from public records only, after the effective ACL and deny patterns | `entities.py`, migration 0007, D18 | REVIEW_WP1 (B1, B1-d, HB-1, L1 fixed and re-reviewed) |
| Cross-department corroboration (`min_independent_units`) in the commit gate, with minimum-unit attribution | `knowledge/gate.py`, `knowledge/units.py` | REVIEW_WP1 |
| Competing findings: two statements about different single subjects are ranked, not treated as a contradiction. Contradiction follow-ups reach both sides | `discovery/engine.py`, `inquiry/routing.py`, D19 | REVIEW_WP1 C5. Accepted for the benchmark; product caveats in §6 |
| Disclosure fixes K1/K1b: a holder owner who was only routed a question no longer sees its run state or its result (both carry other responders' text) | `inquiry/service.py` | REVIEW_WP1 (K1, K1b) |
| Ingestion: traceable rejections, undated records never count as fresh, explicit forwards never add independent roots | `ingest/*`, `knowledge/support.py`, holder migration 0004 | REVIEW_D (accepted) |
| Scale: bounded open holders woken by their inbox, lifecycle lock, revision-counter-keyed authorization caches, subject-indexed transport wake-ups, stale-heartbeat rule | `holder/embedded.py`, `authz.py`, `org.py`, `transport/*`, migration 0006 | REVIEW_E (accepted) |
| Discovery `observe()` reads new evidence through the goal's own responses (json_each) instead of a quadratic `LIKE` join: 388 ms → 2.6 ms at 3k × 3k, and a dev run went from >1,700 s (aborted) to 563 s | `discovery/engine.py` | REVIEW_WP1 (observe fix) |
| **After the holdout:** claim-support changes bump the claim version and write a revision. This is the audit gap behind the holdout's G7 failure. Not part of the evaluated candidate. | `knowledge/service.py` | REVIEW_WP1 (support-revision fix) |

Benchmark harness changes made during the night, each reviewed in `reviews/REVIEW_H1.md`:
- **H1:** a per-task API error or timeout makes that task wrong instead of aborting the run.
- **H2:** every evidence reference visible through goal-level discoveries is raw-checked.
- **A5 fix (after the holdout):** the authorization ablation patches the routing choke point.
- **A4 fix (after the holdout):** the index ablation patches the index sinks, and the report stops expecting the ranker under
  A4.


## 4. What ran

All runs used the deterministic provider in one process with the SQLite transport, on the cloud container. Full tables are in
`VERIFIED_RESULTS.md`; every row there is recomputed from the run directory, and the ledger records each start, abort and
result.

| what | runs | classification |
|---|---|---|
| **Holdout (once)** | H-M101: system, central baseline `source`, central baseline `single`; frozen commit `7f37551`; world M seed 101 | holdout, valid if its gate passes (§5) |
| Dev, candidate code `7d2e63b` | C7-S1 (+ both baselines); ablations C7abl-A1…A6 on S/1 | dev |
| Dev, same system code before harness fixes H1/H2 (`4c27744`) | C6-S1 (+ both baselines); ablations C6abl-A1, A2, A3, A4, A6 | dev (expected-abstain classes affected by the H2 measurement gap) |
| Dev, earlier candidates | C4-S1 (`54fec3f`, first hardened world), C3-L3 (`36a9951`, 10,000 users, sparse world), C2a-M2 (`56f9832`, 1,000 users, sparse world), each with both baselines | dev |
| Not scored | S1 and C1-S1 (harness defects), C4-M2 / C5-M2 / C5-L3 (disk allowance), C5-L3 retry (stopped: scale finding), C5-S1 (unisolated API timeout → fix H1), C5abl-A1/A2 (stopped) | aborted / invalid, in the ledger with reasons |
| Component check | Qwen3-4B (llama.cpp, CPU) answer step on gold holders, n = 40 | live, component only |

Concurrency: up to four benchmark processes at once. During the holdout system run, the dev ablations of the candidate ran
next to it (ledger X067).

## 5. Results

All figures are from `VERIFIED_RESULTS.md` (deterministic provider; 120 tasks per world; 95 % Wilson intervals).

| world | system | gate | central baseline `source` | `single` |
|---|---|---|---|---|
| **Holdout M/101 (1,000 users), frozen `7f37551`, once** | 120/120 = 1.000 [0.969, 1.000], disclosures 0 | **INVALID (G7)**: not published as full-system accuracy | 114/120 = 0.950 [0.895, 0.977] | 70/120 = 0.583 |
| Dev S/1 (112 users), candidate code `7d2e63b` | 120/120 = 1.000 [0.969, 1.000], disclosures 0 | valid | 116/120 = 0.967 | 69/120 = 0.575 |
| Dev S/1, earlier candidate C4 (`54fec3f`) | 103/120 = 0.858 | valid | 116/120 | 69/120 |
| Dev L/3 (10,000 users, sparse world), `36a9951` | 120/120 | valid | 119/120 | 109/120 |

**Holdout, in plain terms.**
- The frozen candidate answered every holdout task correctly and disclosed nothing. On the same world, the centralized
  baseline missed 5 cross-department tasks and 1 fault task.
- The architecture gate still rejected the run, on G7. Three hypothesis claims had fewer independent roots in the asker's view
  than in the final database, and no revision row recorded the change.
- The cause is in tonight's own product code (`sync_support_sync` rewrites claim support without a revision). The gate was
  frozen with the contract, so the run stays invalid.
- No answer depended on those claims: hypothesis claims are never extracted.
- **So the ≥ 80 % target is met on dev with a valid gate, but it is not demonstrated by a valid holdout run.** The holdout
  cannot be repeated for this commit.

**Ablations** (dev S/1, candidate code; full system 120/120):

| ablation | result | what it shows |
|---|---|---|
| A1 ranker off | 63/120, gate valid | ranked hypergraph routing is the main driver: cross-department 16/40, temporal 0/10 |
| A2 roots off | 117/120, gate fails G3 | copies-only tasks fail (4/7); G3 catches it |
| A3 verification off | 120/120 | verification is not decisive on this world |
| A4 index off (partial) | 111/120, gate valid | temporal 2/10; the ablation misses two feeders, so G8 cannot see it |
| A4 index off, corrected after the holdout (`658a093`; pair C8-S1 = 120/120) | 110/120, **gate fails G8** | once the index is really gone, G8 catches it; temporal 1/10 |
| A5 authorization off, first version | 120/120, gate valid | ineffective: it patched a method routing does not call |
| A5 authorization off, corrected | 113/120, **gate fails G4** (13,554 unauthorized routes) | G4 catches unauthorized routing; disclosures stayed 0 |
| A6 dedupe off | 120/120, gate fails G10 | G10 catches it |

**10,000-user scenario: users represented vs. graphs created and activated.**
- **C3-L3 (dev, sparse world, earlier candidate):**
  - 10,000 users and 648 unit holders represented; 10,648 holder graphs created;
  - 417 graphs had records, 482 were routed to, 319 were activated (gave at least one answered response);
  - peak 518 open under the 1,500 bound;
  - 120/120, gate valid; 781 s; 1.17 GB RSS.
- **Holdout M/101:**
  - 1,000 users; 1,120 graphs created, 1,117 with records, 555 routed to, 488 activated;
  - 2,824 s; 2.4 GB RSS.
- **The hardened 10,000-user world** (every person has records) did not finish within the night; §6 has the measured cause.

## 6. Unresolved blockers and honest limits

- **MacBook:** the MacBook bridge environment stayed at "pending / disconnected" from 05:23 UTC to the end. Everything ran in
  the 4-vCPU / 15 GB cloud container.
- **Deterministic provider:**
  - Every end-to-end number uses `mycelic/models/fake.py`, which applies lexical rules.
  - The record templates are written to satisfy those rules (contract §2). The numbers measure routing, authorization,
    independence, lineage, the commit gate and fault handling, not language understanding.
  - A real model (Qwen3-4B, CPU) was only fast enough for a component check: 13/17 vs 17/17, never wrong.
- **10,000 users:**
  - The complete 10k run (C3-L3: 10,648 holders, 120/120) used the early sparse world, in which ~400 holders had records.
  - The hardened 10k world (every person has records) never finished. Its feed fell from ~8 to ~0.7 sources/s once ~1,300
    holders were open.
  - The profiler shows the coordinator rebuilding each open holder's term index and shard mirror on every heartbeat:
    `org.holder_heartbeat → upsert_term_index_sync / mirror_shards_sync`.
  - The fix to make next: skip unchanged snapshots (digest compare) or apply them off the event loop.
  - Until then, a single process will not carry 10,000 *active* people.
- **Discovery loop cost:** C5's rules (competing findings, contradiction follow-ups to both sides) roughly doubled the number
  of follow-up questions. A pre-existing quadratic query made that visible (`observe()`, fixed in `4c27744`; 388 ms → 2.6 ms
  on 3k × 3k).
- **Product caveats on D19 (REVIEWER-2):**
  - The "different subjects → competing" rule should apply only to same-kind subjects, keep the conflict for aliases or
    renames, and not depend on unrelated tenant state.
  - The competing relation should be persisted as a hypergraph relation so the asker sees it.
  - Accepted for the benchmark (canonical names); listed as follow-ups.
- **The holdout gate failure (G7):**
  - `KnowledgeService.sync_support_sync` rewrites a claim's support without a version bump or revision row.
  - Three hypothesis claims changed after the asker's view was taken, and the audit trail cannot explain it (ledger X072).
  - Fixed **after** the holdout in `195e9ad`, reviewed. On dev C8-S1: 120/120, gate valid with G7 passing, 568 s.
  - That is a new candidate. It has **not** been evaluated on a holdout: the holdout is used up for `7f37551`, and re-running it
    after reading its failure would be selection on the holdout.
- **Gate and ablation coverage:**
  - The first A5 ablation patched a method routing does not call; the corrected A5 makes G4 fail as it should
    (`plan/ANALYSIS_A5.md`).
  - The original A4 ablation (index publication off) did not trip G8. The term index was still populated through another path, so G8
    cannot detect A4 as written.
  - A4 still moved routing from hypergraph ranking to the domain fallback. Analysis: `plan/ANALYSIS_A4.md`.
  - A4 was corrected after the holdout (`3c18b1a`, `658a093`). In its dev run, G8 fails (terms 0/112), G4 passes, and the
    score is 110/120 (temporal 1/10).
- **Single process, SQLite transport:** no NATS or distributed-recovery claim is made.
- **No EMERGENCE contract** exists in this repository.
- **Holdout isolation is procedural:**
  - The bank was written by the harness engineer, sealed outside the repository, and its path given to no implementation or
    analysis agent.
  - It sits in the same container, so this is not a separate security domain.
  - One generation smoke test at non-holdout seeds printed counts only (X043).
- **Commit trailers:** most commits carry the session's attribution line. The three post-holdout engineer commits
  (`3c18b1a`, `195e9ad`, `658a093`) carry the engineer's own line. The model that wrote each commit is recorded in
  `EXPERIMENTS.jsonl` (implementation by `claude-sonnet-5-5` engineers; reviews and orchestration by `claude-opus-5-5`;
  planning and dev analyses by `claude-fable-5-1`).

## 7. Reproduce / resume

```
# 1. tested tree (implementation branch + overnight patches)
git fetch origin claude/mycelic-implementation-vr034p claude/friendly-mayer-y9f1vt
git worktree add ../e2e-patches origin/claude/friendly-mayer-y9f1vt      # the patch series lives on the designated branch
git checkout -b e2e-overnight f96f263
git am ../e2e-patches/research/mycelic_e2e/impl_patches/*.patch         # ends at the closing-docs commit after the frozen 7f37551

# 2. test suites
python -m pytest mycelic/tests -q -p no:warnings           # 527 passed, 7 skipped at the candidate
python -m pytest research/mycelic_e2e/bench/tests -q        # 183 passed, 2 skipped at the candidate

# 3. one dev evaluation (about 10 minutes at S on 4 vCPU)
python research/mycelic_e2e/bench/run.py --size S --seed 1 --split dev --mode system --out RUN --anchor 2026-10-09T00:00:00+00:00
python -m research.mycelic_e2e.bench.baseline_central RUN --authority-only
python -m research.mycelic_e2e.bench.report RUN --finalize
python -m research.mycelic_e2e.bench.baseline_central RUN --out RUN-baseline-source --variant source
python -m research.mycelic_e2e.bench.report RUN-baseline-source --finalize
python -m research.mycelic_e2e.tools.results_table RUN RUN-baseline-source

# 4. ablation: add --ablation A1 (A1..A6) to the run.py line
```

Next steps, in the order I would take them:
0. **Evaluate the post-holdout candidate on a fresh holdout.** The candidate is `195e9ad` plus the harness commits after it; it
   adds the support-revision fix. It needs a newly written, newly sealed holdout bank (§2).
1. **Fix the heartbeat cost** (§6), then rerun the hardened 10,000-user world on dev, and on a fresh holdout world if the
   candidate changes.
2. **Make A4 real** (patch the index sink) and harden G8 (index stability, share of hypergraph-ranked routes).
3. **D19 product follow-ups:** same-kind subjects, aliases, a persisted competing relation.
4. **Live model end to end:** needs a GPU host or the MacBook.
   - On this CPU, Qwen3-4B takes ~22 s per 60-token answer in a single stream (`live/LIVE_PROVIDER.md`).
   - One S run makes ~36,000 model calls (C7-S1).
5. **The sealed holdout bank** (`/root/sealed_holdout/templates_holdout.py`) exists only in this container and is gone once the
   container is reclaimed. It is deliberately not in the repository. A new candidate therefore needs a newly written,
   newly sealed holdout bank, written by someone who never sees the implementation agents' work.
