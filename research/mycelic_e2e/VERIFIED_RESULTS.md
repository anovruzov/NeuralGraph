# Verified results: Mycelic-E2E v1 (2026-10-09)

**How these numbers were produced.**
- Every accuracy figure below comes from `python -m research.mycelic_e2e.tools.results_table <run dirs> --json`. The tool re-runs
  the isolated scorer (`bench/score.py`) on each run's asker views and reads that run's architecture-gate report
  (`arch_gate.json`, written by `bench/arch_gate.py`).
- `tools/verified/gen_verified.py` composed the tables in this file from that JSON. The narrative notes between the tables
  quote further figures from the same runs' manifests and gate reports.
- The dev runs' `score.json`, `arch_gate.json`, `run_manifest.json` and `report.md` are kept under `results/runs/<run>/`.
- Holdout tasks, gold and views are **not** committed, so no holdout content enters the repository. Only aggregate numbers and
  gate verdicts are reported for the holdout.

**Labels that apply to every row.**
- **Provider:** `deterministic-provider`, i.e. `mycelic/models/fake.py` (sha256 `0dad30d6…`), lexical rules. No language model.
  The record templates were written to satisfy those rules (BENCHMARK_CONTRACT §2). These numbers therefore measure:
  - routing, authorization and isolation;
  - independence and lineage;
  - fault handling;
  - the commit gate;
  
  not language understanding.
- **Execution:** single process, SQLite transport (no NATS), one in-process API server.
- **Host:** cloud container, 4 vCPU, 15 GB RAM. The MacBook bridge environment never came up.
- **Primary metric (contract §5):** correct / all 120 tasks of the world, with a 95 % Wilson interval. An API error, timeout or
  missing view counts as wrong.
  - A positive task is correct only if the single option extracted from the asker's own supported claims is the gold entity.
  - An expected-abstain task is correct only if nothing is extracted **and** the asker's output has no reference the asker
    may not reach, **and** raw access to every visible reference is refused where the asker lacks it.
- **"disclosures"** is the scorer's count of such references: foreign refs, forbidden markers, open raw access, and visible
  references whose raw access was never checked. The last kind is counted conservatively as a disclosure.
- **Gate (§8):**
  - The scored number of a system run is published as full-system accuracy only if G1–G10 pass.
  - Ablation rows are expected to fail the gate their ablation targets.
  - Central-baseline runs have no coordinator, so the gate does not apply.

## 1. Holdout: the frozen candidate, run once

| run | code | size/seed | mode | n | correct | accuracy | 95 % Wilson | disclosures | gate |
|---|---|---|---|---:|---:|---:|---|---:|---|
| H-M101 | `7f37551` | M/101 | system | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | fails G7 |
| H-M101-baseline-source | `7f37551` | M/101 | central baseline source | 120 | 114 | 0.950 | [0.895, 0.977] | 0 | n/a (no coordinator) |
| H-M101-baseline-single | `7f37551` | M/101 | central baseline single | 120 | 70 | 0.583 | [0.494, 0.668] | 0 | n/a (no coordinator) |

Per class (correct/n):

| run | x-dept | fault | contra | temporal | origin+ | copies | coinc | single | denied | x-tenant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H-M101 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| H-M101-baseline-source | 35/40 | 9/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| H-M101-baseline-single | 0/40 | 0/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |

**The holdout system run is not a valid full-system result.** Its architecture gate failed **G7**, so under contract §8 the
scorer's 120/120 is **not** published as full-system accuracy. The row above shows what the scorer computed, labelled with
the gate verdict.

**What G7 found.**
- Three claims in the asker views (tasks holdout-033, -036, -074) were `hypothesis` both in the view and in the final
  coordinator database, at version 1 in both. Their independent-root counts grew after the view was captured (5→12, 5→6,
  2→8), with no version bump or revision row to record the change.
- The writer is `KnowledgeService.sync_support_sync` (`mycelic/knowledge/service.py:537`), added tonight with the hypergraph
  support invariant. It updates `claims.support` in place.
- G7 did what it is for: it found an audit-trail defect in the product. Hypothesis claims are never extracted (only
  `supported` claims count), so no scored answer depended on these three. The verdict does not depend on that.
- The holdout is not re-run (one shot per candidate), and no candidate change followed it.

**The rest of the evidence for the run.**
- **Every other gate passed:** G1–G6, G8–G10.
  - Connector provenance of all 6,325 ingested records.
  - One store per holder.
  - Support and roots consistent with the hypergraph.
  - All 19,921 routes authorized in the as-of replay.
  - Every claim with gate and revision rows; truthful provider label.
  - Hypergraph present; lineage resolvable.
  - Every injected fault disposed.
- **Disclosures:** 0. Every visible reference was raw-checked.
- **API errors:** 0.
- **Support quality:** independent-support counts correct on 72/78 positives; lineage exact on 72/78.
- **Run shape:** 1,120 holders created, 1,117 with records, 555 routed to, 488 activated; 2,030 questions; 2,824 s; peak RSS
  2.4 GB.
- **Comparison on the same holdout world:** the primary centralized baseline (`source`) scores 114/120 = 0.950
  [0.895, 0.977], missing 5 cross-department tasks and 1 fault task. The `single` variant scores 70/120.
- **The provider caveat applies throughout:** deterministic provider, single process.

## 2. Dev: the candidate code on the dev world S/1

| run | code | size/seed | mode | n | correct | accuracy | 95 % Wilson | disclosures | gate |
|---|---|---|---|---:|---:|---:|---|---:|---|
| C7-S1 | `7d2e63b` | S/1 | system | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | valid |
| C7-S1-baseline-source | `7d2e63b` | S/1 | central baseline source | 120 | 116 | 0.967 | [0.917, 0.987] | 0 | n/a (no coordinator) |
| C7-S1-baseline-single | `7d2e63b` | S/1 | central baseline single | 120 | 69 | 0.575 | [0.486, 0.660] | 0 | n/a (no coordinator) |

Per class (correct/n):

| run | x-dept | fault | contra | temporal | origin+ | copies | coinc | single | denied | x-tenant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C7-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7-S1-baseline-source | 37/40 | 10/10 | 10/10 | 9/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7-S1-baseline-single | 0/40 | 0/10 | 10/10 | 9/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |

**Reading.**
- C7-S1 is the frozen candidate's code (`7d2e63b`; the freeze commit `7f37551` adds documents only) on the dev world S/1:
  112 people, 2 tenants, 136 holders, 1,572 records, with popular rivals and a background corpus.
- The candidate was developed against this world: the C5 rules came from the C4-S1 failure analysis
  (`plan/DEV_ANALYSIS_C4.md`). Its dev score is therefore optimistic by construction. The holdout (§1) is the test.
- On this world the primary centralized baseline (`source` variant: one central store with every record's ACL, the same
  ingestion, provider, evidence budget, gate and scorer) scores 116/120. The `single`-response variant scores 69/120.
- **C6-S1 vs C7-S1** (same system code, harness differs by H1/H2):
  - C6-S1 (`4c27744`) scored 96/120 under the frozen scorer. All 24 misses are expected-abstain tasks. In each, the answer
    extraction was right (abstain), but the scorer counted references that appear only in goal-level discovery details as
    "raw unchecked" disclosures (1,039 references; foreign 0, open 0), because the harness never raw-checked them.
  - Harness fix H2 added those checks (REVIEW_H1.md, H2 section). C7-S1 checks every visible reference and records 0
    disclosures.
  - C6-S1 is reported as measured. It cannot be re-scored, because the checks were never made.

## 3. Ablations of the candidate code (dev S/1, paired with C7-S1)

- **A1**: routing ranker off (first N authorized holders)
- **A2**: root-aware support off (count references, not source roots)
- **A3**: verification questions off
- **A4**: holder entity/term index publication off
- **A5**: authorized routing off (any holder of the tenant)
- **A6**: ingestion dedupe off

| run | code | size/seed | mode | n | correct | accuracy | 95 % Wilson | disclosures | gate |
|---|---|---|---|---:|---:|---:|---|---:|---|
| C7abl-A1-S1 | `7d2e63b` | S/1 | system A1 | 120 | 63 | 0.525 | [0.436, 0.612] | 0 | valid |
| C7abl-A2-S1 | `7d2e63b` | S/1 | system A2 | 120 | 117 | 0.975 | [0.929, 0.991] | 0 | fails G3 |
| C7abl-A3-S1 | `7d2e63b` | S/1 | system A3 | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | valid |
| C7abl-A4-S1 | `7d2e63b` | S/1 | system A4 | 120 | 111 | 0.925 | [0.864, 0.960] | 0 | valid |
| C7abl-A5-S1 | `7d2e63b` | S/1 | system A5 | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | valid |
| C7abl-A5fix-S1 | `7d2e63b + A5 fix` | S/1 | system A5 | 120 | 113 | 0.942 | [0.884, 0.971] | 0 | fails G4 |
| C7abl-A6-S1 | `7d2e63b` | S/1 | system A6 | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | fails G10 |

Per class (correct/n):

| run | x-dept | fault | contra | temporal | origin+ | copies | coinc | single | denied | x-tenant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C7abl-A1-S1 | 16/40 | 2/10 | 2/10 | 0/10 | 1/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7abl-A2-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 4/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7abl-A3-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7abl-A4-S1 | 40/40 | 10/10 | 10/10 | 2/10 | 7/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7abl-A5-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7abl-A5fix-S1 | 33/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C7abl-A6-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |

**Reading the ablations** (each row is the candidate code with ONE mechanism replaced in-process by `bench/ablations.py`; same
world, harness and scorer as C7-S1 = 120/120):
- **A1, routing ranker off: the largest loss.** Questions go to the first N authorized holders in registry order.
  - The loss falls on cross-department positives (16/40), temporal (0/10), fault (2/10), contradiction (2/10) and
    common-origin positives (1/8).
  - The gate stays valid because G4 skips its ranker assertion under A1 by design.
  - Ranked routing over the hypergraph's entity and term incidence is what makes the cross-department answers reachable.
- **A2, root-aware support off** (count references, not independent source roots).
  - Copies-only tasks fail (4/7): forwarded copies now look like independent corroboration.
  - G3 (support/roots consistency) fails, so the gate catches the ablation.
- **A3, verification questions off: no loss on this world** (120/120).
  - Verification is a safety mechanism (blind re-checks of a finding by other holders). The deterministic dev world never
    makes it decisive.
  - It does cost load: the full system asked 676 verification questions in C6-S1 (analyst's count, `plan/ANALYSIS_A4.md`).
- **A4, holder index publication off: partial.**
  - Temporal tasks fail (2/10) and one common-origin positive is lost (111/120).
  - The ablation removes only one of three index feeders, so G8 (which reads the final index) cannot detect it; see
    `plan/ANALYSIS_A4.md`.
  - At runtime most routes fall back to domain-only ranking.
- **A5, authorized routing off** (any holder of the tenant). Two rows:
  - **C7abl-A5-S1 (original A5): did not reach routing.** It replaced `Authorizer.can_route`, but routing calls
    `Authorizer._can_route` directly. It changed only redundant answer-time re-checks, so the run equals the full system
    (120/120, G4 valid with 0 replay denials). It is reported as an ineffective ablation, not as evidence; see
    `plan/ANALYSIS_A5.md`.
  - **C7abl-A5fix-S1 (corrected A5, reviewed).** It replaces `_can_route` and counts how often the replacement loosened a
    decision (155,645 of 243,748 calls).
    - **G4 fails** with 13,554 unauthorized routes in the as-of replay, so the gate is load-bearing for authorization.
    - Accuracy falls to 113/120 (cross-department 33/40).
    - Disclosures stay at 0: the holder-side answer policy and the claim audience filters still apply downstream.
    - The fix landed after the freeze and after the holdout baselines started. It is harness-only (ablations), and the
      candidate is unchanged.
- **A6, ingestion dedupe off.**
  - Accuracy is unaffected on this world (120/120). The injected duplicates are few, and why they do not change answers was not
    analysed.
  - G10 (fault dispositions) fails, so the gate catches the ablation, and the run is not a valid full-system result.
- **Paired C6 ablations.** The same ablations were also run on `4c27744`, before harness fix H2: A1 57, A2 96 (G3 fails),
  A3 120, A4 77, A6 96 (G10 fails).
  - Their expected-abstain classes carry the H2 measurement gap, like C6-S1 (96/120).
  - Against C6-S1, A1 and A4 lose accuracy as they do here, and A2 and A6 equal C6-S1.
  - A3 scores 120, above C6-S1, and records 0 unchecked references. The likely reason, inferred and not verified: with no
    verification questions, the goal-level discovery references that the pre-H2 harness never checked are not produced.
    Treat this as a measurement artefact, not a gain from turning verification off; C7abl-A3 = C7-S1 = 120 agrees.

## 4. Earlier candidates (history; every row a complete, scored run)

| run | code | size/seed | mode | n | correct | accuracy | 95 % Wilson | disclosures | gate |
|---|---|---|---|---:|---:|---:|---|---:|---|
| C6-S1 | `4c27744` | S/1 | system | 120 | 96 | 0.800 | [0.720, 0.862] | 1039 | valid |
| C6-S1-baseline-source | `4c27744` | S/1 | central baseline source | 120 | 116 | 0.967 | [0.917, 0.987] | 0 | n/a (no coordinator) |
| C6-S1-baseline-single | `4c27744` | S/1 | central baseline single | 120 | 69 | 0.575 | [0.486, 0.660] | 0 | n/a (no coordinator) |
| C6abl-A1-S1 | `4c27744` | S/1 | system A1 | 120 | 57 | 0.475 | [0.388, 0.564] | 344 | valid |
| C6abl-A2-S1 | `4c27744` | S/1 | system A2 | 120 | 96 | 0.800 | [0.720, 0.862] | 1094 | fails G3 |
| C6abl-A3-S1 | `4c27744` | S/1 | system A3 | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | valid |
| C6abl-A4-S1 | `4c27744` | S/1 | system A4 | 120 | 77 | 0.642 | [0.553, 0.722] | 2712 | valid |
| C6abl-A6-S1 | `4c27744` | S/1 | system A6 | 120 | 96 | 0.800 | [0.720, 0.862] | 1402 | fails G10 |
| C2a-M2 | `56f9832` | M/2 | system | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | valid |
| C2a-M2-baseline-source | `56f9832` | M/2 | central baseline source | 120 | 119 | 0.992 | [0.954, 0.999] | 0 | n/a (no coordinator) |
| C2a-M2-baseline-single | `56f9832` | M/2 | central baseline single | 120 | 110 | 0.917 | [0.853, 0.954] | 0 | n/a (no coordinator) |
| C3-L3 | `36a9951` | L/3 | system | 120 | 120 | 1.000 | [0.969, 1.000] | 0 | valid |
| C3-L3-baseline-source | `36a9951` | L/3 | central baseline source | 120 | 119 | 0.992 | [0.954, 0.999] | 0 | n/a (no coordinator) |
| C3-L3-baseline-single | `36a9951` | L/3 | central baseline single | 120 | 109 | 0.908 | [0.843, 0.948] | 0 | n/a (no coordinator) |
| C4-S1 | `54fec3f` | S/1 | system | 120 | 103 | 0.858 | [0.785, 0.910] | 0 | valid |
| C4-S1-baseline-source | `54fec3f` | S/1 | central baseline source | 120 | 116 | 0.967 | [0.917, 0.987] | 0 | n/a (no coordinator) |
| C4-S1-baseline-single | `54fec3f` | S/1 | central baseline single | 120 | 69 | 0.575 | [0.486, 0.660] | 0 | n/a (no coordinator) |

Per class (correct/n):

| run | x-dept | fault | contra | temporal | origin+ | copies | coinc | single | denied | x-tenant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C6-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 2/7 | 3/10 | 2/10 | 7/10 | 4/5 |
| C6-S1-baseline-source | 37/40 | 10/10 | 10/10 | 9/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C6-S1-baseline-single | 0/40 | 0/10 | 10/10 | 9/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C6abl-A1-S1 | 16/40 | 2/10 | 2/10 | 0/10 | 1/8 | 6/7 | 10/10 | 7/10 | 9/10 | 4/5 |
| C6abl-A2-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 2/7 | 3/10 | 2/10 | 7/10 | 4/5 |
| C6abl-A3-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C6abl-A4-S1 | 40/40 | 10/10 | 10/10 | 2/10 | 8/8 | 0/7 | 1/10 | 0/10 | 5/10 | 1/5 |
| C6abl-A6-S1 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 2/7 | 3/10 | 2/10 | 7/10 | 4/5 |
| C2a-M2 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C2a-M2-baseline-source | 39/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C2a-M2-baseline-single | 30/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C3-L3 | 40/40 | 10/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C3-L3-baseline-source | 40/40 | 10/10 | 9/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C3-L3-baseline-single | 30/40 | 10/10 | 9/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C4-S1 | 28/40 | 5/10 | 10/10 | 10/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C4-S1-baseline-source | 37/40 | 10/10 | 10/10 | 9/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |
| C4-S1-baseline-single | 0/40 | 0/10 | 10/10 | 9/10 | 8/8 | 7/7 | 10/10 | 10/10 | 10/10 | 5/5 |

**Reading the history.**
- **C2a-M2 and C3-L3 used the first world generator** (`56f9832` / `36a9951`). That generator had no popular rivals and no
  background corpus. Only ~400 of the 10,648 holders in the L world had any records. Both worlds turned out too easy:
  - system 120/120 vs. central baseline 119/120 on each;
  - so contract decisions C9 and C10 (§10) hardened the generator before the freeze.
- **From C4-S1 on, every dev world is hardened.** On the same S/1 world:
  - C4 (`54fec3f`) scored 103/120, against 116/120 for the central baseline. Its 17 misses were different-service claims
    treated as contradictions (DEV_ANALYSIS_C4).
  - C5's competing-findings rule (D19) fixed them.
- **C3-L3 is the complete 10,000-user end-to-end run.** It ran on an earlier candidate and the sparse world, and is labelled dev.
  - 10,648 holders created, 417 with records, 482 routed to, 319 activated;
  - peak 518 open under the 1,500 bound;
  - 120/120 correct, gate valid;
  - 781 s, peak RSS 1.17 GB.
- **Runs not tabulated as accuracy.** Each is in the ledger with its reason:
  - S1 (first harness, 78/120): invalidated by harness review REVIEW_HARNESS.
  - C1-S1: scorer/generator entity-format defect plus a heartbeat replay blackout; both were fixed before C2a.
  - C4-M2, C5-M2, C5-L3 (first attempt): aborted by the disk allowance (ENOSPC).
  - C5-L3 (second attempt): stopped by the orchestrator after its feed slowed tenfold (scale finding, ledger X046).
  - C5-S1: crashed on an unisolated API timeout after 60 of 120 tasks. That crash led to harness fix H1.
  - C5abl-A1/A2: stopped.
  - Baselines whose system run aborted: kept as `*.unpaired`.

## 5. Live language-model check (component level only)

- **What ran:**
  - Qwen3-4B-Instruct (llama.cpp, CPU) behind the product's model router;
  - n = 40 answer-step calls on the holders that hold the gold evidence;
  - details in `live/LIVE_ANSWER_CHECK.md`.
- **Result:** the live model gave the gold answer in 13 of 17 gold-holder cases, against 17/17 for the deterministic provider.
  It never gave a wrong option.
- **Not an end-to-end number:** the CPU model was far too slow for a 120-task run.

## 6. Reproduce

```
# from a checkout of the frozen commit (see impl_patches/README.md), anchored sources
python research/mycelic_e2e/bench/run.py --size S --seed 1 --split dev --mode system --out RUN --anchor 2026-10-09T00:00:00+00:00
python -m research.mycelic_e2e.bench.baseline_central RUN --authority-only
python -m research.mycelic_e2e.bench.report RUN --finalize                       # score + architecture gate
python -m research.mycelic_e2e.bench.baseline_central RUN --out RUN-baseline-source --variant source
python -m research.mycelic_e2e.bench.report RUN-baseline-source --finalize
python research/mycelic_e2e/bench/run.py ... --ablation A1                         # A1..A6
python -m research.mycelic_e2e.tools.results_table RUN RUN-baseline-source ... --json OUT.json
```

The holdout was run once:
- with `--split holdout`;
- with `MYCELIC_E2E_HOLDOUT_BANK` pointing at the sealed bank (sha256 `6a5eabd3…`);
- from a snapshot whose `.rev` equals the clean repository HEAD.

The ledger guard (`bench/ledger.py`) refuses a second holdout run of the same commit, ablation, mode and variant.
