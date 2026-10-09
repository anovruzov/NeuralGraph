# Morning handoff — overnight repair of 2026-10-09

_Status: DRAFT, updated during the night; the final version is written in the closing window (§7 lists when)._

## 1. What you asked, and the short answer

You asked for the benchmark to be rebuilt around the real system (ingestion → individual NeuralGraphs → hypergraph
routing → discovery loop → commit gate), for confirmed defects to be fixed, and for ≥80% genuine end-to-end accuracy
(stretch 90%), with integrity first.

- **Your suspicion was right.** Every published Mycelic accuracy number came from a numpy simulator that imports no
  implementation code (`custody/OLD_BENCHMARKS.md`). There was no hypergraph anywhere in the code.
- **What exists now:**
  - A versioned end-to-end successor, Mycelic-E2E v1 (`BENCHMARK_CONTRACT.md`), that feeds raw source records through
    the real connector path into one NeuralGraph store per person and per unit.
  - Real coordinator hypergraph tables written inside the existing transactions.
  - Routing ranked by hypergraph incidence.
  - A cross-department corroboration rule in the commit gate.
  - An isolated scorer, a fair centralized baseline, and an architecture gate whose checks were shown to fail when
    stages are broken.
- _(final numbers go here: dev, holdout, baseline, with the provider label)_

## 2. Where everything is

- **Code:** the overnight commits sit on top of `claude/mycelic-implementation-vr034p@f96f263` in a clone at
  `/home/user/ng-impl`, branch `claude/friendly-mayer-y9f1vt`.
- **Designated remote branch:** it is `main`-based, so the commits are mirrored there as a patch series in
  `research/mycelic_e2e/impl_patches/`, together with this directory. This was necessary because rewriting the branch
  onto the implementation (force-push) was refused by the session's safety settings. To publish the implementation-based
  history on the designated branch yourself:
  `git push --force-with-lease origin <impl-based-branch>:claude/friendly-mayer-y9f1vt`, or apply the patches with
  `git am` onto `f96f263` (the README in `impl_patches/` gives the commands).
- **Deliverables:** `ARCHITECTURE_AUDIT.md`, `BENCHMARK_CONTRACT.md`, `EXPERIMENTS.jsonl`, `VERIFIED_RESULTS.md`, this
  file, `reviews/`, `plan/`, `audit/`, `custody/`, `perf/`, `live/`.

## 3. What changed in the product (all reviewed by an independent reviewer; see `reviews/`)

| change | files | review |
|---|---|---|
| Hypergraph: `hyperedges`, typed `hyperedge_members`, `entity_registry`, written in the commit/conflict/discovery/heartbeat transactions; `traverse`, `incidence`, `rebuild` | `knowledge/hypergraph.py`, migration 0005 | REVIEW_WP1 (accept after fixes F1–F11, S1) |
| Ranked routing (entity and term incidence, department diversity, audited) instead of the first 10 holders in registry order | `inquiry/routing.py`, `inquiry/service.py` | REVIEW_WP1 |
| Content-free term index: keyed hashes of rare words from public records only | `entities.py`, migration 0007, D18 | REVIEW_WP1 (B1 fix pending at the time of writing) |
| Cross-department corroboration (`min_independent_units`) in the commit gate, with minimum-unit attribution | `knowledge/gate.py`, `knowledge/units.py` | REVIEW_WP1 |
| Ingestion: traceable rejections, undated records never fresh, explicit forwards never add independent roots | `ingest/*`, `knowledge/support.py`, holder migration 0004 | REVIEW_D (accepted) |
| Scale: bounded open holders woken by their inbox, lifecycle lock (remove→ensure dead-consumer race), revision-counter-keyed authorization caches, subject-indexed transport wake-ups | `holder/embedded.py`, `authz.py`, `org.py`, `transport/*`, migration 0006 | REVIEW_E (accepted) |

## 4. What actually ran

_(final table: every scored run, its classification, revision, host, provider; from VERIFIED_RESULTS.md)_

## 5. Results

_(final: primary metric with n, CI, per class; centralized baseline; ablations; scale; live component check)_

## 6. Unresolved blockers and honest limits

- **MacBook:** the MacBook bridge environment was unreachable all night (the probe session stayed at "Allocating
  sandbox"). Everything ran in the 4-vCPU / 15 GB cloud container.
- **Deterministic provider:** every end-to-end number uses the deterministic provider (`mycelic/models/fake.py`,
  lexical rules). The record templates are written to satisfy those rules (the contract declares this). The numbers
  therefore measure routing, authorization, independence, lineage and fault handling, not language understanding.
  A real model (Qwen3-4B, CPU) was only fast enough for a component-level check.
- **Single process:** the runs are single-process with the SQLite transport. No NATS or distributed-recovery claim is
  made.
- **No EMERGENCE contract** exists in this repository.

## 7. Reproduce / resume

_(final commands)_
