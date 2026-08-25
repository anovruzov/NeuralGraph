# Post-Six-Round Completion Plan

**Purpose:** finish the NeuralGraph/Mycelic research package after the six-round LoCoMo improvement run, converge the fragmented repository, produce defensible benchmark artifacts, complete the paper, and ship one reproducible submission by September 5, 2026.

**Status:** authoritative execution plan, not a result artifact.

**Rule:** a box is checked only when the named evidence exists at the named path. Passing tests, an agent summary, or an uncommitted local result is not a substitute.

---

## 1. Definition of done

This project is done for the September 5 submission when all of the following are true:

- [ ] One integration branch contains the accepted six LoCoMo fixes, coordination benchmark, authoritative manuscript, generated figures, and reproducibility tooling.
- [ ] The branch is based on the actual final six-round commit, not an older GitHub branch.
- [ ] A clean Python 3.11 checkout passes the complete test suite.
- [ ] Every scored benchmark has a frozen dataset hash, question/fixture identifiers, configuration hash, model identifiers, error accounting, and immutable result artifact.
- [ ] The final LoCoMo result is a fresh end-to-end run. Historical replay results are labeled historical.
- [ ] The official full LoCoMo protocol and any reduced conversational subset are reported separately with explicit denominators.
- [ ] The capability-survival result is reported per intervention and under matched resource budgets; the nine-condition macro-average is secondary.
- [ ] The coordination result generalizes beyond one hand-authored A/B/C/D topology.
- [ ] Every number in the manuscript is generated from a committed artifact.
- [ ] Unsupported architecture claims are absent or explicitly marked as future work.
- [ ] Anonymous PDF, camera-ready PDF, source bundle, artifact manifest, and final verification report exist.
- [ ] The integration pull request is green, reviewed, merged to main, and tagged.
- [ ] Public links point only to the final verified paper and architecture artifact.

Anything else—NATS/JetStream transport, a production distributed deployment, packaging NeuralGraph for PyPI, the call-center benchmark, new architecture families, or another QA benchmark—is outside this submission and must not delay it.

---

## 2. Repository facts found on August 25

These are the facts that the cleanup must resolve.

### 2.1 Branch fragmentation

GitHub main is at **8785d69** and does not contain the current research system.

| Branch | Head | Relationship to main | Meaning |
|---|---:|---:|---|
| main | 8785d69 | baseline | July README commit |
| claude/mycelic-gate-0-recovery-tjl82x | 1c8245c | 18 ahead | coordination benchmarks, accuracy diagnosis, replay tooling |
| agent/locomo-integrity-gpt | e20215e | 3 ahead, diverged | alternative LoCoMo integrity and retrieval work |
| claude/new-session-e99pju | 727490f | 4 ahead, diverged | provider abstraction, error handling, recall harness/cache |
| tesseract-coordination-v1 | 8258d13 | 3 ahead | ancestor of the larger gate branch |
| fix-the-accuracies | 85eba75 | historical | December 2025 single-hop work |

Two pull requests remain open:

- PR 1: Harden LoCoMo retrieval and GPT evaluation
- PR 2: Tesseract coordination v1

Neither may be merged blindly. PR 1 overlaps the six-round retrieval/generation path. PR 2 is already an ancestor of the 18-commit coordination branch.

### 2.2 The most recent work is not on GitHub

The reported local verification commit **7dab416** and later audit/manuscript work are not reachable from any current remote branch inspected here. GitHub therefore cannot yet be treated as the source of truth for the active six-round run.

The first post-run action is to push the exact final branch without rebasing, squashing, force-pushing, applying the stash, or rewriting history.

### 2.3 LoCoMo protocol conflict

The committed historical diagnosis scores **1,540 questions** across four reported categories:

- multi-hop: 841
- temporal: 321
- single-hop: 282
- open-domain: 96

PR 1 describes a separate **1,986-question, five-category** protocol. Its complete result was stated as in progress and no complete result artifact is committed.

The paper must never compare a 1,540-question score against a 1,986-question score as though they were the same benchmark. The final runner must derive the denominator from the frozen dataset and emit the included and excluded question IDs.

### 2.4 Coordination evidence is real but narrower than the headline can imply

The committed coordination benchmark contains:

- 8 strategies
- 9 interventions
- 30 seeds
- real-SQLite validation
- scale sweep over capability arity and holder count
- byte-reproducible artifacts

Current macro survival scores are:

| Strategy | Mean survival |
|---|---:|
| isolated_local | 0.000 |
| centralized | 0.111 |
| fixed_distributed_replication | 0.556 |
| source_count_repair | 0.556 |
| full_replication | 0.667 |
| random_path_diversification | 0.704 |
| lineage_aware_repair | 0.778 |
| oracle_min_cut | 0.889 |

The 0.778 score is primarily seven survived intervention types out of nine. The nine interventions are heterogeneous fixed conditions, not nine independent samples from a natural distribution. Therefore:

- the per-intervention matrix is the primary result;
- the mean is a compact summary, not proof of statistical significance;
- 30 repeated seeds add uncertainty information only where the mechanism is stochastic;
- generalization needs multiple independently generated topology/lineage fixtures, not merely more seeds on the same fixture.

### 2.5 Missing release infrastructure

The inspected GitHub state contains no:

- authoritative manuscript source at docs/paper/capability_survival.md;
- rendered submission PDF;
- anonymous paper;
- generated paper figures;
- final LoCoMo run artifact;
- GitHub Actions workflow;
- release or paper tag.

Documentation also contains fixed historical test counts that have already become stale. Future documentation should reference the verification artifact or CI run rather than treating a manually typed count as permanent.

---

## 3. Non-negotiable integrity rules

1. Never alter gold answers, question labels, dataset membership, failure labels, or judge verdicts to obtain a higher score.
2. Never drop provider, parsing, ingestion, or timeout failures from the denominator.
3. Never tune against the locked test after its verdicts are exposed.
4. Never let the answer model serve as its own judge.
5. Never refresh a pinned artifact merely to make a reproducibility test pass.
6. Never infer failure domains from storage identity, filenames, node IDs, or value equality.
7. Never allow a repair policy to inspect node-private records or placement internals.
8. Never claim NATS, JetStream, cross-machine transport, or production wall-clock performance; none is implemented in the inspected branch.
9. Never call NeuralGraph/tesseract.py the distributed coordinator. It is intra-node retrieval fusion.
10. Never touch or apply the existing stash during convergence.
11. Never force-push main or delete historical branches during the submission window.
12. Preserve negative results: partition failure and lineage-aware regression under ordinary node failure stay in the paper.
13. Keep API keys out of logs, artifacts, commits, prompts, screenshots, and command transcripts.

---

## 4. Phase 0 — close the six-round experiment

**Start:** immediately when round six finishes.

**Maximum time:** four hours.

### 4.1 Freeze the exact state

- [ ] Record the final branch and full 40-character SHA.
- [ ] Confirm six accepted fix commits exist and each has a bounded mechanism in its message.
- [ ] Confirm rejected or reverted attempts remain documented but are absent from the final code.
- [ ] Record git status and do not proceed with uncommitted source changes.
- [ ] Push the branch under a stable name such as locomo-six-fix-final.
- [ ] Do not rebase or squash before artifact verification.

### 4.2 Required six-round artifacts

The following must exist:

~~~text
evaluation/artifacts/locomo_loop/
  split_manifest.json
  frozen_config.json
  baseline_validation.json
  round_1.json
  round_2.json
  round_3.json
  round_4.json
  round_5.json
  round_6.json
  locked_baseline.json
  locked_after_round_3.json
  locked_after_round_6.json
  history.json
  progress.md
  cost_latency.json
  integrity_report.md
  SHA256SUMS
~~~

Every scored row must include:

- question ID and conversation ID;
- category;
- gold answer hash;
- prediction;
- retrieved evidence IDs and text hashes;
- strict and lenient evidence fields from the authoritative implementation;
- route, top-k, model, prompt hash, and configuration hash;
- latency, token usage, retry count, and error state;
- deterministic metric result and independent judge result.

### 4.3 Acceptance gate

The six-round run is **valid** only if:

- [ ] every accepted round raised frozen validation accuracy;
- [ ] the final locked score is not below the locked baseline;
- [ ] scored membership is identical across paired runs;
- [ ] API, parsing, and ingestion errors are at most 1%;
- [ ] no category loses more than 3 percentage points without an explicit accepted tradeoff;
- [ ] the full Python 3.11 suite passes;
- [ ] the integrity reviewer finds no label leakage, hard-coded entities, grader weakening, or cache-key collision.

If the final locked test is worse, do not tune again on that locked set. Preserve the result as negative, leave the six-fix branch experimental, and use the last pre-registered system for the paper.

---

## 5. Phase 1 — converge the repository

**Target branch:** release/mycelic-paper-2026-09-05

**Maximum time:** one day.

### 5.1 Choose the base correctly

Create the release branch from the **final six-round SHA**. This plan branch is documentation-only and is based on remote commit 1c8245c; it must be cherry-picked into the release branch, not used as the release base.

### 5.2 Integrate the coordination track

- [ ] Prove whether 1c8245c is already an ancestor of the six-round branch.
- [ ] If it is not, merge the 18-commit coordination/diagnosis branch with preserved history.
- [ ] Re-run artifact reproducibility before resolving any output difference.
- [ ] Preserve the coordination contract boundary and real-SQLite tests.

### 5.3 Reconcile divergent LoCoMo branches by behavior

Do **not** merge PR 1, claude/new-session-e99pju, or fix-the-accuracies wholesale.

Create docs/BRANCH_CONVERGENCE.md with a row for each behavior:

| Behavior | Existing source | Present after six rounds? | Action |
|---|---|---:|---|
| official category mapping | PR 1 | verify | port test if missing |
| complete vs any evidence recall | PR 1 | verify | retain authoritative implementation |
| post-rerank context coverage | PR 1 | verify | port if not superseded |
| provider-neutral generation | new-session/gate | verify | keep one client layer |
| rate-limit and quota validity | new-session/gate | verify | keep bounded retry and fail-fast |
| disk-backed cache | new-session | verify | port only with collision tests |
| structured answer contract | gate/six-round | verify | six-round version wins |
| strict/lenient evidence classification | gate/7dab416 | verify | one source of truth |
| progress to stderr | 7dab416 | verify | preserve |
| benchmark integrity tests | PR 1/six-round | verify | deduplicate, do not weaken |

When two implementations overlap, select by tests and frozen benchmark behavior, not by recency or model authorship.

### 5.4 Clean branch topology

After the release branch passes all gates:

- [ ] open one release PR into main;
- [ ] mark PR 1 superseded with a link and a precise list of ported behaviors;
- [ ] mark PR 2 superseded because its commits are contained in the release branch;
- [ ] leave historical branches intact until after submission;
- [ ] update main only through the reviewed release PR.

---

## 6. Phase 2 — security, hygiene, and clean-clone reproducibility

**Maximum time:** half a day.

### 6.1 Secrets

- [x] Add .env and .env.* to .gitignore while retaining .env.example.
- [ ] Verify no .env file is tracked.
- [ ] Scan reachable history for API-key patterns without printing matched secret values.
- [ ] If any credential was committed, rotate it before further API calls and document only the rotation—not the secret.
- [ ] Ensure benchmark artifacts contain model metadata but no request headers or credentials.

### 6.2 Repository hygiene

- [ ] Remove tracked Python bytecode and generated caches.
- [ ] Archive superseded manuscripts and projections; delete only exact duplicates and generated junk.
- [ ] Move QUERY_ROUTING_IMPROVEMENTS.md to archive if the audited local move is not already present.
- [ ] Add a supersession header pointing to docs/ACCURACY.md.
- [ ] Ensure active README and paper paths do not cite archived projections as results.
- [ ] Add an explicit license decision or state all-rights-reserved consistently.

### 6.3 CI

Add .github/workflows/ci.yml with Python 3.11 and these offline jobs:

1. full unit/regression suite;
2. coordination artifact byte-reproduction;
3. benchmark schema and membership validation;
4. manuscript table/figure regeneration check;
5. secret-pattern and tracked-junk check.

Live model calls do not run in pull-request CI. CI validates the committed response artifacts and cache provenance offline.

### 6.4 Canonical verification output

Create evaluation/artifacts/final_verification.json containing:

- commit SHA and dirty-tree state;
- Python and dependency versions;
- exact command list;
- test result;
- artifact hashes;
- dataset hashes;
- benchmark configuration hashes;
- manuscript source and PDF hashes.

Do not hard-code a permanent test count into multiple documents. Generate the count into this artifact and link to it.

---

## 7. Phase 3 — freeze and run the final LoCoMo protocol

**Maximum time:** one day plus API runtime.

### 7.1 Resolve the denominator before any final call

The runner must inspect the dataset and generate:

~~~text
evaluation/artifacts/locomo_final/
  dataset_manifest.json
  included_ids_full.json
  included_ids_conversational.json
  excluded_ids.json
  category_map.json
~~~

The manifest must answer:

- Does this exact dataset contain 1,986 questions?
- Which fifth category is absent from the historical 1,540 artifact?
- Are unanswerable/adversarial questions scored, scored as abstentions, or reported separately?
- Is the 1,540 set an official protocol or a project-defined subset?

Report the official full-set score as primary when compatible with the benchmark definition. Report the 1,540 historical-compatible subset only as a secondary apples-to-apples comparison.

### 7.2 Freeze configuration

Before the full run, freeze:

- dataset SHA-256;
- question IDs and order;
- ingestion code SHA;
- retrieval top-k and reranker;
- answer model and exact resolved model identifier;
- judge model and prompt;
- temperature and decoding;
- answer prompt hash;
- cache-key schema;
- timeout, retry, rate, and concurrency settings;
- scoring implementation SHA.

No prompt, top-k, model, or threshold changes are allowed after reading the final-test verdicts.

### 7.3 Required reports

Report for the full protocol and the conversational subset:

- judged accuracy;
- exact normalized set match;
- item precision, recall, and F1;
- abstention accuracy;
- Recall@1, @5, @10, and @20;
- complete-evidence recall and fractional evidence coverage;
- MRR;
- unsupported-item rate;
- false-abstention rate;
- answer-type mismatch rate;
- latency p50/p95/p99;
- token use and cost;
- API, parse, ingestion, and judge-error counts;
- results by category.

Include paired deltas from the frozen baseline with improved, regressed, and unchanged question IDs.

### 7.4 Judge calibration

Blindly hand-audit a stratified sample of at least 50 final predictions. Record human versus judge labels and disagreements. Do not silently override individual verdicts. Report agreement and preserve the adjudication sheet.

### 7.5 Interpretation gate

LoCoMo is supporting evidence, not the paper's central novelty.

- 75% or higher on a valid frozen protocol is a strong supporting result.
- 72–75% remains publishable if retrieval coverage and failure analysis are clear.
- Below 70% should be reported as a limitation, not described as strong memory QA.
- No score, however high, rescues an invalid protocol or denominator mismatch.

---

## 8. Phase 4 — strengthen the capability-survival benchmark

**Maximum time:** one and a half days.

The current result is compelling but reviewers can reasonably call it one engineered fixture repeated across seeds. Add one decisive generalization suite rather than more architecture.

### 8.1 Multi-topology fixture generator

Implement a deterministic generator that varies:

- number of agents;
- capability arity;
- holders per premise;
- independent lineage-root count;
- failure-domain count and imbalance;
- derivation depth;
- correlated-replica fraction;
- route/verification budget;
- node placement;
- intervention target.

Generate at least 100 pre-registered fixtures across topology families. Save every fixture specification before running strategies. No strategy may receive a fixture generated in its favor.

### 8.2 Matched-budget comparison

For each fixture, compare at matched record and byte budgets:

- isolated local;
- centralized;
- fixed distributed replication;
- full replication;
- source-count repair;
- random path diversification;
- lineage-aware repair;
- oracle min-cut.

The oracle is an upper bound, not a deployable competitor.

### 8.3 Primary outcomes

Report:

- capability survival per intervention;
- unrecovered silent forgetting;
- records and bytes;
- verification count and recovery steps;
- minimum failure-domain cut;
- survival at equal storage cost;
- area under the survival-versus-storage curve if multiple budgets are tested.

Use paired bootstrap confidence intervals over independently generated fixtures. Do not compute significance by treating the nine intervention labels as iid samples. Deterministic repeated seeds are reproducibility checks, not additional independent evidence.

### 8.4 Ablations

At minimum:

- lineage-aware ordering without targeted repair;
- targeted repair without lineage diversity;
- source count at the same verification budget;
- lineage-aware repair with failure-domain metadata removed;
- lineage-aware repair with a deliberately wrong lineage direction test that must fail.

The purpose is to show which mechanism causes the gain.

### 8.5 Real-storage extension

Run a representative stratified subset of generated fixtures through independent SQLite stores. The paper may claim real-storage validation, but not cross-machine deployment or transport latency.

### 8.6 Stop rule

Freeze the coordination benchmark after the pre-registered suite runs. A bug that changes membership or semantics invalidates the suite and requires a full rerun. A disappointing result is reported, not tuned away.

---

## 9. Phase 5 — generate the paper from evidence

**Maximum time:** two days.

### 9.1 Authoritative manuscript

Use exactly one source:

~~~text
docs/paper/capability_survival.md
~~~

Working title:

**Capability Survival and Collective Forgetting in Distributed Agent Memory**

Archive all conflicting drafts. The public README, portfolio, and submission package must point only to the rendered artifact from this source.

### 9.2 Claim hierarchy

The paper's primary claim is:

> Lineage independence, measured through failure-domain structure, preserves reconstructable multi-agent capabilities under correlated failures more efficiently than replica count alone.

Supporting claims:

1. A collective can lose a reconstructable capability while local health/confidence remains high.
2. Correlated replicas can all fail through a shared derivation root.
3. Lineage-aware repair beats full replication under lineage-root and authorization failures at lower storage cost.
4. Minimum failure-domain cut predicts survival under single-domain failure.
5. The mechanism generalizes across capability sizes and independently generated topologies.
6. NeuralGraph can support conversational memory QA, measured separately by LoCoMo.

Do not claim that lineage-aware repair dominates universally. It loses to source-count repair under ordinary node failure in the committed result.

### 9.3 Required figures

All quantitative figures must be generated by scripts from pinned JSON:

1. **Mechanism schematic:** why replicas B and C share one root while D is independent.
2. **Survival matrix:** strategy × intervention.
3. **Pareto plot:** survival against records/bytes at matched budgets.
4. **Topology generalization:** paired survival delta across generated fixtures.
5. **Silent forgetting:** reported confidence versus genuine capability coverage.
6. **LoCoMo trajectory:** baseline, rounds 1–6, and locked checkpoints, clearly separated from the full final result.

Use vector output for the paper. Captions state the artifact and protocol.

### 9.4 Required tables

Generate, never hand-copy:

- primary per-intervention survival table;
- resource-normalized comparison;
- ablation table;
- scale/topology generalization;
- LoCoMo full and subset results;
- negative results and limitations.

### 9.5 Limitations that must appear

- in-process coordinator; no NATS/JetStream transport;
- mock benchmark plus SQLite validation, not a production deployment;
- no meaningful wall-clock distributed latency claim;
- failure-domain quality depends on recorded provenance;
- partition isolates premise holders and defeats distributed strategies;
- lineage-aware repair can be over-conservative under simple node failure;
- LoCoMo judge and unanswerable/adversarial protocol limitations;
- synthetic topology distribution may not represent deployed agent networks.

### 9.6 Claim ledger

Update AUDIT.md so every manuscript claim is one of:

- VERIFIED;
- PARTIAL;
- PROPOSED;
- UNSUPPORTED;
- CONTRADICTED.

Only VERIFIED claims enter the abstract and contributions. PARTIAL claims require limiting language. PROPOSED material belongs in future work. UNSUPPORTED and CONTRADICTED claims are removed.

---

## 10. Phase 6 — hostile verification

**Maximum time:** one day.

A clean clone, not the development directory, must pass:

1. dependency installation under Python 3.11;
2. complete test suite;
3. coordination artifact reproduction;
4. final LoCoMo artifact schema/membership validation;
5. table and figure regeneration;
6. citation/reference check;
7. manuscript render;
8. anonymous metadata scan;
9. source-bundle extraction and rerun instructions.

Create FINAL_VERIFICATION.md with:

- exact clean-clone SHA;
- every command and exit status;
- test and benchmark artifact hashes;
- manuscript page count;
- broken-reference/overflow check;
- anonymity check;
- all known deviations;
- final PASS or FAIL.

Visually inspect every PDF page for clipping, empty tables, unreadable plots, broken glyphs, incorrect titles, and accidental identity disclosure.

A failed integrity gate blocks the claim or artifact that failed. It does not authorize hiding the failure.

---

## 11. Phase 7 — merge, release, and public consistency

**Maximum time:** half a day.

- [ ] Open one release PR from release/mycelic-paper-2026-09-05 to main.
- [ ] Require green CI and inspect the complete diff.
- [ ] Confirm no secret or local artifact is present.
- [ ] Merge without rewriting the verified commit history.
- [ ] Tag the verified commit paper-2026-09-05.
- [ ] Attach anonymous PDF, camera-ready PDF, source bundle, manifest, and checksums to the release or durable submission archive.
- [ ] Close superseded PRs with exact explanations.
- [ ] Update README numbers from generated tables.
- [ ] Replace portfolio links only after the final PDF hash matches the release.
- [ ] Keep archived drafts visibly non-authoritative.
- [ ] Do not delete remote branches until at least 30 days after submission.

---

## 12. Calendar to September 5

Assuming the six-round run finishes by August 28:

| Date | Deliverable |
|---|---|
| Aug 28 | six-round freeze, pushed final branch, complete loop manifest |
| Aug 29 | branch convergence, security cleanup, clean Python 3.11 suite |
| Aug 30 | LoCoMo protocol freeze and full run |
| Aug 31 | multi-topology coordination suite and ablations |
| Sep 1 | final artifacts, tables, and vector figures |
| Sep 2 | complete authoritative manuscript |
| Sep 3 | hostile claim audit and clean-clone reproduction |
| Sep 4 | final PDFs, source bundle, release PR, portfolio artifact |
| Sep 5 | submission buffer only |

If the six-round run finishes after August 29, cut work in this order:

1. extra LoCoMo iterations;
2. call-center or any additional QA benchmark;
3. deployment/transport experiments;
4. packaging and website polish;
5. optional figures.

Never cut:

- branch convergence;
- benchmark denominator integrity;
- clean-clone reproduction;
- the multi-topology generalization check;
- negative results;
- the claim ledger;
- PDF inspection;
- submission buffer.

---

## 13. Final go/no-go rubric

### Submit as a strong paper

All are true:

- benchmark integrity PASS;
- coordination results reproduce;
- generalization suite supports the mechanism;
- manuscript claims match artifacts;
- no unresolved protocol mismatch;
- final PDF and source bundle pass verification.

### Submit as a narrower paper

The generalization or LoCoMo target is weaker than hoped, but:

- the original coordination result reproduces;
- negative results are explicit;
- claims are narrowed to the demonstrated fixture family;
- LoCoMo is supporting evidence or omitted.

### Do not submit the current claim

Any is true:

- final benchmark membership changed silently;
- provider errors were excluded;
- locked-test tuning occurred;
- lineage/domain information leaked through an oracle path;
- a headline table cannot be regenerated;
- the manuscript claims transport or deployment that does not exist;
- the only generalization evidence is repeated seeds on one topology.

---

## 14. Immediate next command after round six

The executor should begin with a read-only snapshot and produce no source edit until the final SHA and artifacts are recorded:

~~~bash
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline --decorate -20
python3.11 -m pytest
~~~

Then create release/mycelic-paper-2026-09-05 from that exact SHA and execute this document phase by phase.

---

## 15. Final handoff format

The last report must end with:

~~~text
Final commit:
Release tag:
Python 3.11 tests:
LoCoMo full protocol:
LoCoMo conversational subset:
Capability-survival primary result:
Matched-budget delta:
Topology-generalization result:
Negative results retained:
Anonymous PDF:
Camera-ready PDF:
Source bundle:
Artifact manifest:
Benchmark integrity: PASS/FAIL
Paper defensible: YES/NO
Submission status:
~~~

No completion claim is valid while any field is blank.
