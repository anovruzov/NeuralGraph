# Mycelic-E2E v1 — benchmark contract

Status: **DRAFT — definitions fixed before any scored run; hashes filled at freeze.** Written by the orchestrator from
`plan/PLAN_v1.md` §B with the binding amendments of §F. A definition in this file changes only by a new version (v1.1, ...)
recorded in `EXPERIMENTS.jsonl` with the reason; scores under different versions are never compared as equals.

## 1. What is being measured

End-to-end task accuracy of the Mycelic implementation (`claude/mycelic-implementation-vr034p` plus the overnight patches)
when it receives **only raw source records** through its supported connector path and **only the task** (a goal and a
question, as a real user would issue them through the API), with everything in between done by production code:
connector → normalization → tenant/holder identity, ACL, domains, shard → durable idempotent ingestion → the holder's own
NeuralGraph store → entity/graph/index updates → hypergraph membership (coordinator) → authorized, ranked routing →
holder-side retrieval and answering → evaluation → verification → commit gate → claims and discoveries.

This is a **successor** to the published Mycelic enterprise benchmark (`research/mycelic`, simulation-only, see
`custody/OLD_BENCHMARKS.md`), not a rewrite of it. Its scores are **not comparable** to the simulator's "hidden problems
found" numbers: different system, different tasks, different metric.

## 2. Provider label

Every run carries a provider label. `deterministic-provider`: `mycelic/models/fake.py` (lexical rules, no model; the
sha256 of the file is recorded per run). `live:<model>`: a real model behind the product's provider interface (only if one
is available; reported as separate rows, never pooled). `mixed` when tiers differ. Deterministic runs verify the
architecture and plumbing and measure routing, authorization, independence, lineage and fault handling — not language
understanding; the record templates are written so the deterministic lexical rules can apply (≥2 shared content tokens
question↔record, ≥3 between agreeing records), and the holdout templates satisfy the same predicate with different wording.

## 3. Worlds

Deterministic from `(seed, size, bank)`. Two tenants. Six-level units (executive, region, subsidiary, department, team,
plus cross-functional projects). One `user` holder per user, one `unit` holder per department and team, all registered
through `OrgService` and materialized as embedded holders (each its own SQLite NeuralGraph store). Holder domains are never
set by the harness: routable domains come only from ingestion. Sizes: **S** 112 users (end-to-end checks; enlarged from ≈48 so goal-only tasks get disjoint scopes), **M** 1,000
users (development), **L** 10,000 users (priority scale; attempted when measurement shows it fits). Policies:
`min_independent_roots = 2`, `freshness_days = 365`, default export `{disclosure: excerpt, answer_scopes: [unit, org]}`.

## 4. Raw inputs and faults

Raw records only, as `local_export` JSONL sources (and Slack/GitHub offline mocks at S), dropped in each holder's import
root and attached through the connector API (`connectors` add + `sync`). Org-wide sources feed department holders,
personal sources feed user holders; every record carries its source ACL. Fixtures supply raw records only, never derived
answers, claims, domains or graph nodes.

Hidden cross-department pattern (positive): one entity, ≥3 observations by distinct people in ≥2 departments, distinct
source roots, consistent statement. Decoys: entity coincidence, common-origin copies (`forwarded_from`), contradiction /
retraction, temporal supersession, single-department, denied access (members-restricted source excluding the asker),
cross-tenant. Faults on positives: duplicate delivery, replay (re-sync after cursor reset), out-of-order updates, edits,
deletions, missing metadata, malformed lines, restart mid-ingest.

## 5. Tasks and the primary metric

Each split has **120 tasks** in this fixed mix: 40 cross-department positives (10 of them issued as goal-only), 10
contradiction/retraction (gold = surviving value's entity), 10 temporal supersession (gold = newest), 15 common-origin
(8 positives with copies present and ≥2 genuine roots; 7 copies-only → abstain), 10 entity coincidence → abstain, 10
single-department → abstain, 10 denied access → abstain, 5 cross-tenant → abstain, 10 fault-injected positives.

The system receives a goal (`POST /api/goals`, activated) and, for question tasks, one question under it (`POST
/api/questions`) as the task's asker. Questions name the context (departments, symptom), never the gold entity.
Cross-department questions carry `policy.min_independent_units = {"department": 2}`.

**Answer extraction** (identical for system, baseline and ablations; never reads system internals): from the asker's own
views only, take claims of the task's question (or the goal's discoveries for goal-only tasks) with status `supported`;
match their visible text against the K=4 option entities by canonical id or display name. Exactly one matched option →
that option; none → `abstain`; several → the option with the most independent roots, ties → wrong.

**Rubric.** Positive classes: correct iff the extracted option equals gold. Expected-abstain classes: correct iff the
extraction is `abstain` and the asker-visible output contains no evidence reference from a holder the asker may not reach
and raw access to every visible reference is refused where the asker lacks it. API error, exception, missing view or
timeout → wrong.

**PRIMARY METRIC: accuracy = correct tasks / ALL 120 tasks of the frozen split.** No exclusions after freeze. Reported with
a 95 % Wilson interval, per-class table, numerator and denominator; across seeds with the per-seed values and a bootstrap
interval over seeds. Targets: ≥ 0.80 (stretch 0.90) on the holdout split. Any unauthorized disclosure is reported and
fails the run's compliance regardless of accuracy.

Supporting metrics, reported separately and never folded into the primary number: independent-support correctness,
lineage correctness, decoy acceptance, unauthorized disclosures, abstentions and errors, latency per task, model calls and
tokens, RSS/DB size, holders created / with records / routed to / activated.

## 6. Splits and isolation

Dev and holdout use **disjoint** record templates, entity names and word 4-grams (tested). The holdout bank and seeds live
only in `bench/holdout/`; its sha256 is written to `plan/HOLDOUT_SHA256` at freeze and checked by every run. The holdout is
run **once** per frozen candidate (ledger guard); its failures are not inspected for tuning. Gold lives only in
`tasks_<split>.gold.json`, opened only by `bench/gold.py` (scorer side); the runtime, the feeder and the issuer never read
it (tested by grep). Limitation: the isolation is by module boundary and tests, inside one process tree and one container;
it is not a separate security domain. Concretely: the holdout template bank was written by the WP2 engineer together
with the dev bank (someone has to write it), then moved out of the repository tree at ~06:40 UTC; no implementation or
analysis agent was given its path, and analysis agents were instructed never to open it. A process in the same container
could still find it. The holdout world seeds are chosen by the orchestrator at freeze time and recorded here.

## 7. Centralized baseline

Same raw files, same connector and ingestion pipeline, same provider and embedder, **one** central holder store with every
record's ACL; per task the asker's audience filter, one retrieval with the system's evidence budget (up to 30 items), the
same evaluation and the same commit gate and policy; answers in the same view format, scored by the same scorer. Declared
differences: no routing, no per-person stores, no verification questions, no loop follow-ups, no hypergraph.

## 8. Architecture gate

A scored run is valid only if G1–G10 (`bench/arch_gate.py`) pass: connector-path provenance of every record (G1), per-holder
isolation of stores (G2), support/roots consistency incl. hypergraph (G3), audited authorized routing with rank method
(G4), gate and revision rows for every claim (G5), provider label (G6), no harness writes to coordinator or holder data
(G7), hypergraph present and consistent (G8), lineage resolvable (G9), fault dispositions (G10). An invalid run's score is
not published as full-system accuracy. Ablations (routing ranker off, root-aware support off, verification off, entity
index off, dedupe off) are separate rows, each tied to the task classes it must break.

## 10. Decisions recorded before the freeze (development, 2026-10-09 05:30–08:00 UTC)

These were fixed while the harness was being repaired, before any candidate was frozen and before the holdout was
generated. Each came from a review finding or an analysis of DEV runs. None came from holdout material.

| # | decision | why |
|---|---|---|
| C1 | **Ambiguity rule**: a supported claim that names more than one distinct entity identifies nothing and contributes no option. Entities are canonicalized (`x-service` = `service:x`) and matched only at word boundaries. Same rule for system, baselines and ablations. | REVIEWER-4: claims naming 2–5 services were credited whenever the other services were not options |
| C2 | **Primary central baseline = `source` variant** (≤3-chunk pseudo-responses grouped by origin holder, ≤30 items). `single` (one response) is reported as secondary. | REVIEWER-4: `single` accretes unrelated chunks into one finding |
| C3 | **Product export-policy defaults are not overridden by the benchmark.** Entity ids are published only from public records with ≥5 occurrences per holder. Term ids are published from public records only. The benchmark sets neither `entity_include_members` nor `entity_min_records`. | REVIEWER-2's F5/F6 conditions: a lower threshold must be declared, and the benchmark tests the defaults |
| C4 | **Readiness**: tasks are issued only after two consecutive identical heartbeat snapshots of every holder with content. | The planner's D1 and the C1 diagnosis: questions routed during a heartbeat blackout |
| C5 | **Size S = 112 users** (2 tenants), so that goal-only tasks get disjoint scopes. | REVIEWER-4 1a |
| C6 | **Sources are anchored** (`--anchor 2026-10-09T00:00:00+00:00`), so a seed regenerates byte-identical sources. | reproducibility |
| C7 | **Answer extraction reads only asker-visible views.** `score.py` refuses views without `tenant_id`. Disclosure is judged by authorization reach (`reach_authority.json`, computed from the org with `can_route`, answer-free). | REVIEWER-4 2b/2c |
| C8 | **The gate is non-vacuous.** G7 cross-checks every scored view against `coord.db`. G4 replays routing as of each route from the audited domain history. G10 derives each injected fault from the sources and requires its evidence. G8 follows the publication policy (§10 C3). | REVIEWER-4 §5 |

## 9. Freeze record (filled at freeze)

| item | value |
|---|---|
| contract version | v1 |
| implementation commit | _at freeze_ |
| evaluator files sha256 (`score.py`, `gold.py`) | _at freeze_ |
| dev templates sha256 | _at freeze_ |
| holdout bank sha256 | `89f4afa815b0092320e160f077c4279a287c0f538db5ff17272af81c464791a3` (`templates_holdout.py`, re-sealed 2026-10-09 ~07:05 UTC after the D3 boilerplate fix (first seal `6613deee...5214` ~06:40 UTC); never evaluated before either seal at `/root/sealed_holdout/`, outside the repository; loaded only via `MYCELIC_E2E_HOLDOUT_BANK`; dev/holdout disjointness tests passed against it: 12 passed) |
| dev seeds / holdout seeds | _at freeze_ |
| sizes per split | _at freeze_ |
| per-task timeout, worker drain budget | _at freeze_ |
| host | cloud container, 4 vCPU, 15 GB (MacBook bridge unreachable, see ORCHESTRATION.md) |
