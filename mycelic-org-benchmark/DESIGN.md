# Mycelic Organizational Aggregation Benchmark — Design Specification

Status: implementation spec (v1). Every module in `src/mycelic_bench/` is built
against the interfaces in this document. If code and this document disagree,
the code is wrong until the document is amended in the same commit.

## 0. What is being measured, in one paragraph

An organization of N workers evaluates AI models. Each worker interaction is a
record with structured task attributes (model family, version, task family,
context length, input format, domain, language, difficulty, tool) and an
observed outcome (worker score, error labels). The *world* contains hidden
**effects**: conjunctions of attributes under which a specific error label is
elevated (e.g. `{model=Atlas, task=math_reasoning, context=long,
format=table} -> arithmetic_error +0.30`). Some effects are visible to a single
worker or team ("local findings"); some need evidence pooled across teams
("cross-team findings"); some need evidence pooled across departments or
regions ("hidden global discoveries") because no single unit below that layer
has enough matching samples to detect the interaction. The benchmark asks
whether a five-layer hierarchy of local agents, promoting **bounded
abstractions with lineage**, reconstructs those hidden effects — with what
recall, precision, false-discovery rate, compression, bandwidth, privacy
exposure, poison resistance, and cost — compared with centralized systems that
receive every raw record.

## 1. Honesty rules (enforced by tests in `tests/test_no_cheating.py`)

1. No aggregator, baseline, or agent module may import `world.py`'s
   ground-truth structures (`Effect`, `GroundTruth`) or receive them as
   arguments. They receive only `Interaction` records (raw, for centralized
   systems) or `Sketch`/`Claim` artifacts (for hierarchical systems).
2. The hypothesis-search engine (`hypothesis.py`) is shared by *every* system.
   Differences between systems come only from what information reaches the
   engine (raw vs sketches, order of sketches, lineage, security filters,
   questioning), never from a different statistical method.
3. Centralized baselines always receive **strictly more** information than
   Mycelic: the complete raw interaction records with exact structured
   attributes and full rationale text. They are not fed a text-only view.
4. The simulated frontier model used by centralized baselines has extraction
   fidelity **>=** the simulated edge SLM on every parameter
   (`configs/models.yaml`). Any run where this is violated is rejected by
   `config.validate()`.
5. Failed runs are recorded in `results/raw/<run>/manifest.json` with
   `status: failed` and are never deleted by scripts.
6. Every reported number is produced by `experiments/*.py` from
   `results/raw/` and written to `results/processed/`; the report cites file
   paths. There is no hand-entered number in the report.
7. Model backend limitation is stated in every manifest: `backend:
   simulated-slm` when no real model was used. See §9.

## 2. Vocabulary (`vocab.py`)

Attributes (9), each with a small closed value set:

| attribute | values |
|---|---|
| model_family | Atlas, Borealis, Cirrus, Delta, Ember, Fjord, Granite, Helix |
| model_version | v1, v2, v3, v4 |
| task_family | math_reasoning, coding, tool_use, factuality, instruction_following, long_context, vision_language, data_analysis, agentic_workflow, safety, hallucination_detection, retrieval, formatting, latency_sensitive |
| context_len | short, medium, long, very_long |
| input_format | prose, table, code, json, image, multi_turn, spreadsheet |
| domain | general, finance, medical, legal, science, engineering, customer_support |
| language | en, es, zh, de, ja, fr |
| difficulty | easy, medium, hard |
| tool | none, calculator, search, code_interpreter, browser, database |

Error labels (18): arithmetic_error, unit_conversion_error, table_misread,
hallucinated_fact, hallucinated_citation, wrong_tool_call, tool_output_ignored,
format_violation, instruction_ignored, truncated_output, context_loss,
unsafe_compliance, over_refusal, stale_knowledge, retrieval_miss, off_by_one,
timeout, visual_misgrounding.

Every attribute value is encoded as a small integer; an interaction's
attributes are a `uint8[9]` row. A **cell** is a conjunction of
`(attribute, value)` pairs of order 1..4, canonically sorted by attribute
index. A **signature** is `(cell, label)`.

## 3. World model (`world.py`)

### 3.1 Organization
`OrgSpec(n_workers, workers_per_team, teams_per_department,
departments_per_region, n_regions, layers)` builds the tree
`worker -> team -> department -> region -> executive`. `layers` in
{1,2,3,5,7} builds ablation topologies (7 adds `squad` under team and
`division` above department). Each unit has an id like `T017`, `D03`, `R1`,
`EXEC`.

### 3.2 Specialization (this is what makes evidence *distributed*)
Each team draws a specialization mixture: a Dirichlet-perturbed distribution
over task_family, input_format, model_family, context_len, and tool. Each
department is a cluster of teams with correlated mixtures; each region has
language/domain bias. Interactions of a worker sample attributes from the
team mixture. Consequently a 4-way conjunction's evidence is spread thinly
across many teams while its 1-way/2-way marginals are concentrated in
different specialist teams.

### 3.3 Workforce quality
Worker types with proportions from `configs/organization.yaml`:
`expert, average, weak, overconfident, underconfident, noisy, adversarial,
duplicator`. Each type has label recall, label precision (false-label rate),
confidence bias, attribute-mention probability (for rationale text), and
copy probability (duplicator copies a teammate's rationale and labels).
Correlated teams share a team-level label bias vector.

### 3.4 Outcome generation
For interaction i with attribute row a_i, model version release time, and
timestamp t_i, the probability of error label l is

    p_il = clip( base_l(model_family, task_family) + Σ_{e ∈ active(t_i, region_i)} δ_e · [a_i ⊨ cell_e ∧ label_e = l] , 0, 0.97 )

Labels are sampled independently per label. The worker's observed labels
apply the worker's noise channel (recall/precision) and team bias.
`worker_score` = 10 − 2.5·(#true labels) + noise, clipped to [0,10].

### 3.5 Effects (ground truth)
`Effect(effect_id, kind, cell, label, delta, valid_from, valid_to, regions,
sign, contradiction_group, revision_group, decoy_of)`.

Kinds: `local` (order 1–2, concentrated in one team), `cross_team` (order 2–3,
evidence in ≥2 teams of one department, none sufficient alone), `global`
(order 3–4, evidence in ≥2 departments and optionally ≥2 regions),
`temporal` (member of a revision group whose δ flips at version-release
rounds), `contradiction` (pairs with opposite sign that are either
region-conditional — both true — or one side false, generated only through
adversarial/correlated workers), and `decoy` (cells that look suspicious by
marginals but carry δ=0; they exist so precision is measurable).

**Minimum discovery layer** is computed, not assumed: for each effect, the
generator counts matching interactions per worker, team, department, region,
and executive, computes the sample size n_min needed to detect δ at α=10⁻⁸ with power 0.8
against the most elevated sub-marginal under the current probability matrix
(the contrast the shared interaction test uses; label noise attenuated), and
records the lowest layer at which some unit has n ≥ 1.25·n_min. A final
validation pass re-checks every effect under the final matrix and drops
those that no longer qualify (counts are over-generated by 1.4×, kinds that
fall short are topped up and re-validated up to five times, then trimmed to
the requested counts; the achieved counts per seed are recorded in the
ground-truth summary and reported). An effect labelled `global`
must have min layer ∈ {department, region, executive}, evidence in ≥3 teams,
≥2 departments and ≥2 regions when they exist, and no single unit below its
minimum layer may hold more than half of the required evidence. The
per-unit evidence distribution is saved to `data/ground_truth/` and used by
`evaluate.py` for evidence-coverage and layer-needed metrics.

### 3.6 Sensitive content
A configured fraction of interactions is marked sensitive with one of:
`pii, employee_name, customer_id, internal_secret, code_snippet, credential,
financial, hr, unreleased_product`. Each inserts a **canary token** of the
form `CANARY-<kind>-<hex8>` into `prompt` and/or `rationale`. Leakage is
measured by exact canary presence in any artifact that crosses a boundary
(promoted upward, sent to cloud, stored centrally). Reconstructability is
measured by k-anonymity of promoted sketch cells (cells with n < k are
re-identifiable).

### 3.7 Time
The simulation runs in rounds (days). Interactions are spread over
`n_rounds`. Version releases happen at fixed rounds; temporal revision
groups switch δ at those rounds. Gradual poisoning ramps with round.

## 4. Artifacts (`schemas.py`)

```
Interaction: interaction_id, worker_id, team_id, department_id, region_id,
             task_id, round, timestamp, attrs(uint8[9]), true_labels(bitmask),
             observed_labels(bitmask), worker_score, confidence, rationale,
             prompt, model_response, sensitive(bool), sensitive_kind,
             canaries(list[str]), attack(None|AttackTag)
Sketch:      producer_id, layer, order (max cell order), cells: dict[cell ->
             (n, label_counts[18])], evidence_roots: set of hashed
             interaction ids (or counts by root when compressed),
             bytes_estimate, round
Claim:       claim_id, producer_id, layer, cell, label, sign, n, k,
             rate, baseline_rate, effect, p_value, confidence,
             support: SupportRecord, lineage: LineageRecord, valid_from,
             valid_to, revision_of, contradicts: list[claim_id],
             status ∈ {proposed, accepted, quarantined, superseded,
             unresolved}, privacy_class, quotes: list[str] (empty under DLP),
             signature (HMAC of content by producer key), round_created,
             round_last_updated
SupportRecord: replica_count, independent_support (float, after
             correlation discount), distinct_workers, distinct_teams,
             distinct_departments, distinct_regions, evidence_hashes
LineageRecord: parent_claim_ids, root_worker_ids (hashed), path (unit ids),
             derivation_operator ∈ {observe, pool, synthesize, revise,
             answer_question}
QuestionArtifact: question_id, asker_id, cell, label, trigger, budget,
             target_unit_ids, round, status, answers: list[Sketch]
Conflict:    conflict_id, claim_ids, cell, label, signs, status ∈
             {open, resolved, unresolved_appropriate}, winner, reason
```

Every artifact has `to_bytes()` (canonical JSON) so bandwidth is measurable.

## 5. Edge agent (`agents.py`)

`EdgeAgent(worker, model_profile, policy)` consumes the worker's interactions
for the round and emits:

1. A `Sketch` up to `policy.sketch_order` (1–3; 4 only in answer to a
   question), with cells derived from the observed (noisy) labels. DLP is
   applied here: no text leaves unless `policy.quote_policy != none`; cells
   with `n < policy.k_anonymity` are suppressed at promotion time.
2. `Claim`s of kind `observe` for signatures that the local hypothesis test
   flags on the worker's own data (rare at 10 interactions/worker; common for
   the B0 baseline where a worker accumulates data across rounds).
3. Under attack tags, attack behaviors (§7).

The **simulated SLM** noise channel is the `ModelProfile`
(`configs/models.yaml`): attribute-omission probability, spurious-cell rate,
injection susceptibility, label-extraction accuracy, poison-classifier ROC
point, latency per 1k tokens, RAM, energy per 1k tokens. Backends
(`backends.py`): `SimulatedSLM` (default), `OpenAICompatSLM` (real model over
an OpenAI-compatible endpoint, used by `scripts/validate_with_real_slm.py`
to *measure* the profile parameters when a model is available), and
`LlamaCppSLM`. See §9.

## 6. Hierarchy engine (`hierarchy.py`)

Each `UnitNode` (team, department, region, executive; plus squad/division in
the 7-layer ablation) holds:

- `pooled: DenseSketch` — dense count tensors for orders 1..3 over the
  vocabulary (`sketch.py`), incrementally updated from children's promoted
  sketches. Order-4 is sparse and only populated from question answers.
- `claims: dict[signature -> Claim]` merged from children with lineage.
- `conflicts`, `questions`, `quarantine`.

Per round, each node runs `step()`:

1. **Ingest** children artifacts through the `SecurityPipeline`
   (§7): provenance signature check, evidence-hash dedup, confidence/support
   sanity rules, cross-source consistency test, content classifier
   (profile), quarantine.
2. **Pool** sketches (cell-wise sum) with lineage: each cell tracks the
   multiset of root hashes contributing (bounded; beyond `max_roots_tracked`
   only counts per child are kept — this bound is itself a compression
   knob).
3. **Merge** claims by signature: pooled n,k; `independent_support` =
   Σ_roots w(root) where w discounts roots by correlation: copies detected
   by evidence hash or near-duplicate fingerprint → 0; same worker → 0 beyond
   the first; same team → ρ_team (default 0.5); same department → ρ_dept
   (0.8); else 1.0.
4. **Synthesize**: run `hypothesis.search(pooled, order ≤ policy.sketch_order
   + 1 if questioning else policy.sketch_order)` → candidate signatures with
   p-values (interaction test vs best sub-marginal, §6.1), BH-corrected within
   the node at q=policy.fdr_q. Candidates that pass n_min, effect_min,
   support_min become `synthesize` claims with lineage = contributing child
   claims/sketch cells.
5. **Contradict**: claims with equal cell+label and opposite sign, or a
   sub/super cell with opposite sign, open a `Conflict`. Resolution policy:
   resolve for the side with independent_support ≥ 2× the other AND fresher
   evidence; else `unresolved` (kept, both lineages preserved). Region- or
   time-conditional splits are attempted: if each side's evidence is
   confined to disjoint regions/periods, both are accepted as conditional
   claims.
6. **Revise**: if a claim's evidence after the latest version release for its
   model_family contradicts pre-release evidence, the claim is superseded
   (`revision_of`), old evidence retained in lineage, `valid_to` set.
   **Cumulative re-verification** (every system, through its claim book):
   an accepted claim is re-tested each analysis round on the cumulative
   evidence at its scope; when it is no longer even nominally significant
   (one-sided p > `withdraw_p` = 0.05 against the current most-elevated
   sub-marginal) it is withdrawn (`superseded`, reason `withdrawn`) and
   revived if the signal returns. Without this, a round-0 fluke accepted at
   BH q = 0.05 on 300 records persists forever and the union over rounds
   and scopes has no false-discovery control (ORACLE reached 15 % false
   associations on a clean 6000-record world). The analyst baselines pay one
   facet query per re-tested claim.
7. **Question** (only when enabled): for signatures with 2+ elevated
   sub-marginals sharing a model or task, and insufficient joint samples,
   emit a `QuestionArtifact` to children (or, hierarchy-aware, to siblings
   via parent) asking for the order-(k+1) cell counts; answers are sketches
   of exactly one cell (bounded bytes). Policies: none, fixed (top-Q by
   marginal z), confidence-triggered, EIG (expected reduction in posterior
   variance of the cell rate per byte), budget-aware (EIG under a byte
   budget).
8. **Promote**: select what goes to the parent under `policy.compression`:
   `full_sketch` (every cell of every order ≤ sketch_order, no suppression:
   the privacy-unsafe reference), `order_capped` (default: cells with
   n ≥ min_cell_n, then k-anonymity suppression), `order2_plus_significant`
   (all order-≤2 cells plus the order-3/4 cells that pass the significance
   screen), `significant_cells_only` (order-1 marginals, cells whose rate
   for some label is elevated over the organisation-wide marginal by a
   one-sided z ≥ 2.5 *and* an exact binomial tail ≤ 0.005, the cells of
   accepted claims, and the order-(k−1) sub-cells of every kept cell so the
   parent can still form the test's contrast), `claims_only` (only the
   cells of accepted claims), plus `top_k_claims` and `byte_budget`.
   Significance-filtered rungs pre-select elevated cells, so the parent's
   test inherits a selection bias; the ladder is there to measure that
   trade-off. Privacy filter: canary scan (should find nothing under DLP),
   k-anonymity suppression. Bytes are metered on the wire representation.

### 6.1 The interaction test
For a candidate cell C of order k and label l, with counts (n_C, k_C), find
the sub-cell S ⊂ C of order k−1 with the highest rate outside C:
(n_S − n_C, k_S − k_C). The test is a one-sided two-proportion z-test (with
Fisher exact when any expected count < 5) of rate(C) > rate(S \ C). Order-1
cells are tested against the complement of the cell. The effect estimate is
rate(C) − rate(S \ C). This makes the search find *interaction* effects and
avoid over-specified claims. It is shared by every system (rule 1.2).

## 7. Attacks and security (`attacks.py`, `security.py`)

Attack tags on workers (fraction sweep 0–40%): `false_claim`,
`coordinated` (same fake signature across teams), `fake_consensus`,
`duplicate_evidence`, `hidden_in_rationale` (flip observed labels toward a
fake signature on real interactions), `provenance_spoof` (forged worker_id),
`confidence_inflation`, `prompt_injection` (payload text; a susceptible SLM
emits the injected claim), `adversarial_format` (malformed artifacts),
`gradual` (ramp over rounds), `cross_team_coordinated`.

Detectors (each a `Detector` with `inspect(artifact, context) ->
Verdict(flag, score, reason)`): `RegexRules`, `CentralClassifier(profile)`,
`CloudClassifier(profile; charges tokens to cloud and exposes text)`,
`NoFilter`, `LocalSLMClassifier(profile)`, `HybridLocalRules`,
`LineageAwareVerifier` (signature check, dedup, independent-support
requirement, consistency z-test of a claim against independent sketches for
the same cell). The **structural** detectors (`LineageAwareVerifier`,
rules, dedup) are fully measured; the **content classifiers** are profile
operating points (§9) and results are labelled accordingly.

## 8. Baselines (`baselines.py`)

All receive the full raw `Interaction` list (rule 1.3) and the same
`hypothesis.search`.

- `B0_isolated`: each worker searches its own cumulative data; a discovery is
  any worker's accepted claim. Privacy 0 exposure, no synthesis.
- `B1_central_keyword`: central inverted index over rendered records; analyst
  with query budget Q per round explores conjunctions by beam search over
  facet counts (exact hit counts for AND-queries); no lineage (copies and
  spoofs count), all raw text central.
- `B2_central_rag`: TF-IDF/hashing vectors; per hypothesis query, reads
  top-k records (context window) and estimates the rate from those k only.
- `B3_central_llm_summary`: records chunked into windows of W; simulated
  frontier model extracts an order-≤3 sketch per chunk (profile
  `frontier`); reduce pools without lineage. Tokens to cloud = all text.
- `B4_majority_vote`: candidate signatures from B1's beam; each worker votes
  from its own data; replica count decides.
- `B5_flat_agents`: all edge agents promote to one aggregator with fan-in
  intake budget (bytes per round); overflow dropped by confidence order.
- `B6_hier_no_lineage`: Mycelic topology, no signatures, no dedup, replica
  count as support, no consistency test.
- `B7_mycelic`: lineage-aware hierarchy (§6) with DLP.
- `B8_mycelic_security`: B7 + LocalSLMClassifier + LineageAwareVerifier.
- `B9_mycelic_questioning`: B8 + questioning policy.
- `ORACLE_central_stats`: exhaustive search on all raw labeled data with
  ground-truth-free lineage (dedup by exact record). Exact-data reference (it
  tests the largest hypothesis family, so it bounds false associations, not recall).

## 9. Model backends and the limitation statement

This environment has no reachable LLM (no API key, model hubs blocked). All
results in `results/` therefore use `backend: simulated-slm` with profiles in
`configs/models.yaml`. The profiles are **assumptions**, swept in the model
ablation (`experiments/models/`) and labelled "synthetic capability sweep",
never "measured model results". `scripts/validate_with_real_slm.py` measures
the same profile parameters against any OpenAI-compatible local endpoint
(LM Studio / Ollama, which the parent repository already uses) so a user with
a GPU can replace the assumed profiles with measured ones and re-run
`scripts/reproduce_all.sh`.

## 10. Metrics (`metrics.py`, `evaluate.py`)

Discovery matching: an accepted claim at a layer matches effect e if
signature equal (exact), or cell ⊃ cell_e with same label and sign
(`over_specified`), or cell ⊂ cell_e (`under_specified`, partial credit
0.5 only when reported as such). Recall/precision/FDR use exact matches by
default; partial columns are reported separately. Time-to-discovery = first
round accepted at the executive − round when the world first had n_min
matching interactions anywhere. Evidence coverage = fraction of the effect's
matching interactions whose root hash appears in the accepted claim's
lineage. Lineage correctness = fraction of root hashes in lineage that truly
match the cell. Calibration = ECE of claim confidence vs. truth. Layers
needed = the layer where first accepted vs. the ground-truth minimum layer.

Aggregation fidelity tracks each true effect through transitions with the
eleven states listed in the task (§4 of the task): survived, dropped,
distorted (effect estimate off by > 50%), duplicated, incorrectly promoted
(a decoy or false claim), incorrectly merged (two effects merged into one
signature), contradicted, resolved, lineage preserved, independent support
preserved (|IS_est − IS_true| ≤ 1), privacy boundary violated.

Compression ratio = bytes of raw records at the lower layer / bytes promoted
across the transition. Costs: simulated model calls, tokens (rendered text
length / 4), latency from profile, energy = tokens × J/token from profile,
USD from `configs/models.yaml` prices (cloud) or amortized hardware (edge).

## 11. Statistics (`stats.py`)
Paired by seed: paired bootstrap (10,000 resamples) CI, paired t-test,
Wilcoxon signed-rank, Cohen's d_z, Holm correction within a comparison family.
Never a p-value without an effect size. Seeds: Tier 1 uses 30, Tier 2 uses
≥10 (target 20 where runtime allows), Tier 3 uses 3–5 and says so.

## 12. Experiments (`experiments/`)
One runner per family; each writes `results/raw/<family>/<run_id>/` with
`manifest.json` (§13), `metrics.json`, `claims.jsonl` (executive layer), and
appends a row to `results/processed/<family>.csv`. Figures are produced only
by `experiments/figures.py` from processed CSVs.

## 13. Reproducibility manifest
`git_commit, config (full resolved), backend, model profiles + versions,
seed, timestamp, hardware (platform, cpu, ram), runtime_s, peak_rss_mb,
dataset_sha256, outputs (paths), metrics, log path, status`.
`scripts/reproduce_all.sh` re-runs every experiment at the requested tier.

## 14. Tiers
Tier 1 (laptop): 100–1,000 workers, 30 seeds, minutes.
Tier 2 (serious): 10,000 workers, 100,000 interactions, ≥10 seeds.
Tier 3 (scale): 50,000–100,000 workers, 3–5 seeds, single machine with
numpy; distributed execution hooks (`--shard`) documented but optional.
