# Module specification for parallel implementation

Read `DESIGN.md` first. The trunk (already implemented, do not restructure) is:

| file | role |
|---|---|
| `src/mycelic_bench/vocab.py` | attribute vocabulary, `CellIndex` (encode/decode/sub_ids/super_ids/ids_for_rows) |
| `src/mycelic_bench/sketch.py` | `Sketch` sparse count tensor: `from_interactions`, `pool`, `lookup`, `restrict`, `wire_bytes`; columns `COL_N, COL_K0.., COL_DW, COL_DT, COL_DD, COL_DR` |
| `src/mycelic_bench/hypothesis.py` | `search(sketch, n_min, effect_min, fdr_q, max_order, restrict_ids)` -> `Candidates`; `rate_test`, `null_evidence`, `bh_qvalues` — **every system must use this** |
| `src/mycelic_bench/world.py` | `generate_world(cfg, seed) -> World`; `World.records() -> RecordView` (what systems may see); `Effect` (ground truth — forbidden outside `world.py`, `evaluate.py`, `attacks.py`, `failures.py`, `experiments/`) |
| `src/mycelic_bench/agents.py` | `SimulatedSLM(profile, seed)` perception channel; `ObservationBatch`; `make_batches(...)` |
| `src/mycelic_bench/hierarchy.py` | `Policy`, `UnitNode`, `Hierarchy(org, records, policy, profile, seed, layers, security, question_policy, attack_hook)` with `run(n_rounds, on_round)`; nodes expose `cumulative`, `received_from`, `claims`, `conflicts`, `obs_*` arrays (team local store), `available`, `inbox`, `delay` |
| `src/mycelic_bench/evaluate.py` | `ClaimRecord`, `match_effects`, `evidence_metrics`, `transition_fidelity`, `temporal_metrics`, `contradiction_metrics`, `privacy_metrics`, `raw_record_bytes`, `PopulationTruth` |
| `src/mycelic_bench/runner.py` | `run_system(world, cfg, seed, name, sys_cfg, **kw)` dispatches to `run_hierarchy` (kind=hierarchy) or `baselines.run_baseline` (kind=baseline/oracle); returns `{"metrics", "classified", "discovered", ...}` |
| `src/mycelic_bench/manifest.py` | `write_manifest(run_dir, ...)`, `append_processed_row(csv, row)` |
| `src/mycelic_bench/security.py` | interface `SecurityPipeline`, `Verdict`, `build_security` (delegates to `security_impl.py`) |
| `src/mycelic_bench/questioning.py` | interface `QuestionPolicy` + `fixed`; `build_question_policy` delegates other names to `questioning_impl.py` |
| `configs/*.yaml` | organization / models / attacks / experiments (systems table, policy defaults, tiers) |

Run anything with `cd mycelic-org-benchmark && PYTHONPATH=src python3 ...`. A Tier-1 world (1000 workers,
10k interactions, 30 rounds) generates in ~1 s; one hierarchy run takes ~25 s. Use
`cfg = load_config(overrides=["org.n_workers=300", "org.n_rounds=10", "world.n_local_findings=20", ...])`
for fast tests (< 5 s).

## Metric dictionary every system must return (`result["metrics"]`)
`n_accepted, categories, precision_strict, precision_lenient, false_discovery_rate, false_association_rate,
recall_local, recall_cross_team, recall_global, recall_temporal, recall_contradiction, ttd_global_median,
ttd_cross_team_median, evidence_coverage, lineage_correctness, source_diversity, ece, revision_accuracy,
stale_persistence, contradiction_f1, correct_resolution_rate, incorrect_resolution_rate,
raw_sensitive_leakage, n_canaries_exposed, bytes_off_device, fraction_raw_exposed, reconstructability,
bytes_transmitted, compression_ratio, tokens, tokens_to_cloud, model_calls, latency_ms_est, energy_j_est,
cost_usd_est, runtime_s, poison {attack_records, poison_claims_at_root, poison_promotion_rate, ...},
failure_reasons, fidelity (per-layer table when the system has layers)`.
`match_effects` produces most of them from a list of `ClaimRecord`s; reuse it — never re-implement matching.

## Honesty rules (tests enforce)
* Only `world.py`, `evaluate.py`, `attacks.py`, `failures.py`, `routing.py` (query generation only) and
  `experiments/` may import `Effect`/`World.effects`/`World.p_true`/`World.true_labels`. Systems get
  `world.records()` (a `RecordView`) or artifacts. `tests/test_no_cheating.py` greps for this.
* Baselines receive **all raw records** (`RecordView`: exact attrs, observed labels, worker/team/dept/region,
  round, score, confidence, rationale text) — never a degraded view.
* Every system calls `hypothesis.search` for statistics. Different information flow, same statistics.
* Frontier profile must dominate the edge profile (`config.validate`).
* No hard-coded expected discoveries anywhere.

---

## Task A — `baselines.py`  (+ `tests/test_baselines.py`)
`run_baseline(world, cfg, seed, name, sys_cfg, attack_hook=None, failure_plan=None, snapshot_every=1) -> dict`
with the same result structure as `runner.run_hierarchy` (metrics dict per dictionary above, `classified`,
`discovered`, `snapshots` {round: list[ClaimRecord]}). Implement:

* `B0_isolated` — per worker cumulative sketch (order ≤3) from its own *perceived* records (use
  `SimulatedSLM.perceive` with the edge profile), `search` per worker each round (cadence ok); claim
  scope = ("worker", idx). Bytes off device = 0, leakage 0, compression = raw/0 -> report `inf` as `None`.
  A discovery is any worker's accepted claim. Vectorise: group records by worker; a worker with < n_min
  matching records cannot find anything, so skip workers with < n_min records.
* `B1_central_keyword` — all records shipped raw to a centre (bytes_off_device = raw bytes; leakage = all
  canaries present in shipped rationales = 1.0 of canaries; tokens_to_cloud = 0 unless frontier used).
  Analyst with `query_budget` AND-queries per round: facet counts are exact (`Sketch.from_interactions`
  over all records gives exact counts for any cell). The analyst explores by beam search: start from all
  order-1 cells, expand the top-B (by label-rate z) by one attribute, each expansion consumes one query,
  and hypothesis-tests only the queried cells with `search(..., restrict_ids=queried)`. Also runs per-unit
  facets (team/department/region as extra query dimensions) within the same budget so scoped effects are
  reachable. No dedup, no signatures (copies and attacks counted), no consistency check.
* `B2_central_rag` — same centre; for each hypothesis (from B1's beam candidates) retrieve `top_k`
  records by cosine over hashed bag-of-tokens (`sklearn.feature_extraction.text.HashingVectorizer` on
  `records.rationale(i)`; the query is the cell's attribute tokens + label token) and estimate the rate
  from the retrieved records only (exact cell match among the k). Test with `rate_test` against the
  global marginal. Context limit = top_k. Reads cost `top_k * 180` tokens to the frontier model
  (tokens_to_cloud), cost from the frontier profile.
* `B3_central_llm_summary` — records chunked in arrival order into windows of `window_records`; per chunk
  a *frontier-profile* `SimulatedSLM` perceives records and builds an order-≤3 sketch; chunks are pooled
  (no lineage: `dw` etc. summed but copies not deduped) and `search` runs on the pooled sketch each round.
  tokens_to_cloud = all record text. Scope = ("executive", 0).
* `B4_majority_vote` — candidates = B1's beam cells; each worker with ≥3 matching records votes
  "elevated" if its own rate ≥ its own baseline + effect_min; central accepts if votes_for ≥ `min_votes`
  and votes_for > votes_against. Replica count = votes.
* `ORACLE_central_stats` — exhaustive `search` on the exact sketch of all *observed* records at every scope
  (executive, each region, each department, each team; order ≤3) plus order-4 cells present with n ≥ n_min
  (build order-4 sparse via `ids_for_rows(rows, 4)` restricted to cells with n ≥ n_min). Dedup exact
  copies by `records.fingerprint`. Reported as an upper-bound reference, not a system.

Time-to-discovery: run cumulative every round (or every `cadence` rounds) and record the first round each
signature is accepted. Poison accounting: `evaluate.match_effects` labels a claim "poison" when it is
population-false and ≥30% of its matching records are attack-tagged — nothing to do in the baseline.
Cost: `tokens_to_cloud * frontier.usd_per_1k_tokens/1000` + edge tokens for B0.

## Task B — `attacks.py` (+ `tests/test_attacks.py`)
`apply_attacks(world, cfg, seed, fraction=None, type_mix=None) -> AttackPlan` mutates the world *before* a
run: chooses malicious workers (fraction of workers; `world.org.worker_attack[w] = code`), sets
`world.attack_tag[i]` for every record of a malicious worker (code of its attack type), and implements:
* `false_claim` — bare claims with inflated n/k for random non-true signatures (`n_fake_signatures` shared
  targets), emitted in the hook as `batch.injected_claims` dicts `{worker, cell, label, n, k, confidence, attack}`.
* `coordinated` / `cross_team_coordinated` — the same fake signatures from attackers spread across teams;
  also fabricate records: attacker records' attrs are rewritten to match a target cell and the target label
  set (observed_labels), so sketches carry the poison (`hidden_in_rationale` does the same on *real*
  matching records with prob `hidden_flip_prob`, without changing attrs).
* `fake_consensus` — many attackers echo the same bare claim.
* `duplicate_evidence` — the attacker re-sends copies of one record (same fingerprint) many times in a batch.
* `provenance_spoof` — records/claims carry another worker's id; set `batch.signature_ok=False` for them.
* `confidence_inflation` — claims/records with confidence 0.99 regardless of support.
* `prompt_injection` — set `world.injection_payload[i]` from `attacks.injection_payloads` on the attacker's
  records; in the hook, for records where `slm.susceptible(n)` is True, emit the injected claim (the target
  cell/label written in the payload). This is the only attack whose success depends on the model profile.
* `adversarial_format` — malformed records: attrs out of range / NaN confidence; must be caught by rules.
* `gradual` — attack strength ramps with `round / (gradual_ramp_rounds * n_rounds)`.
`AttackPlan.hook(batch, slm) -> ObservationBatch` is passed as `attack_hook` to `Hierarchy`/baselines.
Expose `plan.summary()` (counts per type) and `plan.malicious_workers`.
Also provide `independent_support_scenarios(world, cfg, seed)` for the independent-support test (§10):
create claim clusters — 50 copies of one record, 10 paraphrases (same fingerprint, different text), 5
workers copying each other, 3 independent workers, cross-department, cross-region — as record mutations
with a ground-truth `true_independent_support` per cluster; return the cluster table for evaluation.

## Task C — `security_impl.py` (+ `tests/test_security.py`)
`build_security(detector, cfg, profile, seed, local_slm) -> SecurityPipeline` for detectors:
`none, rules, central_classifier, cloud_classifier, local_slm, hybrid_local_rules, lineage_aware`.
* `filter_batch(batch, node) -> keep mask`: rules = regex on `injection_payload` text (the batch has
  `idx` into `node.hier.records`; use `records.rationale(i)` only for records the policy allows the detector
  to read: local detectors read locally at zero exposure; `central_classifier` ships the text to the centre
  (bytes_exposed += len, text_exposed collects canaries); `cloud_classifier` additionally charges
  tokens_to_cloud), sanity rules (confidence vs n, malformed attrs -> drop), duplicate-fingerprint rule.
  Classifier detectors flag with `slm.classify_poison(batch.is_attack)` at the profile's (tpr, fpr) —
  **document in the docstring that this is a profile assumption**. `local_slm` uses the edge profile,
  `central_classifier` a mid profile (`sim-14b`), `cloud_classifier` the frontier profile.
* `inspect_claim(claim, sender, node) -> Verdict|None`: signature verification for node-level claims
  (`claim.verify(node.hier.nodes[sender].key)` when sender is a node), confidence-inflation rule
  (confidence > 0.9 with n < n_min), consistency z-test of the claim's rate against the node's own pooled
  counts for the cell (`node.cumulative.lookup`) when independent evidence exists (`rate_test`), and
  `lineage_aware` additionally requires `claim.support.independent_support >= support_min` for bare claims.
Keep counters (`flagged_records`, `flagged_claims`, per-attack-type flags using `batch.is_attack` for
evaluation only) so the runner can compute detection recall/precision/F1/false suppression.

## Task D — `questioning_impl.py` (+ `tests/test_questioning.py`)
Policies `confidence` (ask only when the best candidate's parents have q in (0.05, 0.2] or IS < 2×support_min),
`eig` (expected information gain per byte: for candidate cell with current pooled (n,k), gain =
posterior-variance reduction of the rate under a Beta(1,1) prior given the expected answer size
Σ_children n_child(cell) estimated from order-(k-1) marginals; divide by expected answer bytes), and
`budget` (eig under `question_byte_budget` per node-round; stop when exhausted). All reuse
`QuestionPolicy.candidate_cells`. Add `marginal_pair` triggers: candidates from pairs of *screened* order-2
cells sharing a model or task value (use `node.cumulative` counts, not only accepted claims).
Record per-question expected gain; the runner reports `questions_asked`, `question_bytes`,
`marginal_value_per_question` (Δ recall / questions) in `experiments/questioning/run.py` (Task H).

## Task E — `routing.py` + `experiments/routing/run.py` (+ tests)
Retrieval workload: for a finished hierarchy run, generate queries "which units hold evidence for cell C
(label l)?" from (a) ground-truth effects (holders = units whose retained observations match C with n≥1,
computed from `world` — allowed in this module only for *query generation and scoring*), (b) random cells.
Routers (each returns an ordered list of unit ids to contact, and bytes/hops):
`local_only` (asker's own team), `global_only` (broadcast to all teams), `hybrid` (own team then broadcast
if insufficient), `hierarchy_aware` (walk up; each ancestor forwards to children whose `received_from`
sketch has the cell — this is what the Tesseract multi-plane router does with its relational plane),
`provenance_aware` (contact lineage `contributing_units` of accepted claims first), `lineage_aware`
(prefer units with independent roots — distinct departments/regions — until support_min reached),
`oracle` (true holders). Metrics: retrieval accuracy (recall of holders @ contacted budget), correct
destination selection, unnecessary escalation rate (contacted non-holders / contacted), messages,
bytes (question + answer sizes), latency (hops), cross-team discovery (fraction of holders outside the
asker's team reached), private-data movement (bytes of records vs sketches moved). Show whether
`global_only` reduces precision by over-broadening.

## Task F — `failures.py` + `experiments/failures/run.py` (+ tests)
`FailurePlan(kind, rate, seed, at_round, recover_round=None)` with `apply(hier, round_)`: kinds
`random_user, random_team, department, region, targeted_high_support` (remove the nodes with most
accepted claims), `targeted_lineage` (remove the units named most often in root claims' contributing_units),
`stale_replica` (a unit stops updating: `available=False` but its old artifacts remain), `partition`
(set `hier.delay[unit] = 10**6` for a segment until recover), `delayed_sync` (delay = k rounds).
User loss = drop that worker's records from batches (wrap `attack_hook` chain; provide `record_filter`).
Metrics vs the paired no-failure run (same seed): knowledge survival (fraction of the no-failure root
accepted exact discoveries still accepted), useful-discovery survival (cross_team+global), lineage
survival (contributing units still available), contradiction-detection survival, recovery latency
(rounds after recover_round until survival returns to ≥95%), repair quality, false reconstruction rate
(claims accepted after recovery that are population-false). Sweep rates 1,5,10,20,30,50 %.

## Task G — `stats.py` + `experiments/figures.py` + `experiments/tables.py`
`paired_summary(a, b) -> dict(mean_a, mean_b, diff, ci_low, ci_high (paired bootstrap 10k), t_p, wilcoxon_p,
cohen_dz, n)`, `holm(pvals) -> adjusted`, `summarize(values) -> mean, sd, ci95`. Figures (matplotlib,
publication quality, PNG+PDF, from `results/processed/*.csv`): F1 discovery vs org size; F2 useful
information retained vs compression ratio; F3 poison propagation across layers (stacked by layer, per
malicious fraction); F4 privacy leakage vs centralized; F5 cross-team emergent discovery rate; F6 knowledge
survival under failures; F7 accuracy vs communication cost; F8 edge-model Pareto frontier (synthetic
capability sweep — label it so); F9 hierarchy-depth ablation; F10 time-to-discovery distribution;
plus `investor_comparison.png` (one bar/radar chart of the executive table) and `fig_headline_table.png`.
Every figure must degrade gracefully when a CSV is missing (skip with a warning) and read only CSVs.
Tables: markdown tables into `results/tables/` (executive comparison, layer fidelity, failure reasons).

## Task H — `experiments/common.py` + all runners + `scripts/*.sh`
`experiments/common.py`: `parse_args()` (`--tier tier1|tier2|tier3`, `--seeds N`, `--seed-offset`,
`--systems a,b`, `--set k=v`, `--results DIR`, `--quick`), `make_cfg(tier, overrides)`, `run_grid(family,
systems, seeds, cfg, world_factory, per_run_hook)` which: generates the world per seed (cached across
systems), applies attacks/failures if requested, calls `run_system`, writes `results/raw/<family>/<run_id>/
{manifest.json, metrics.json, claims.jsonl}` via `manifest.write_manifest`, appends flat rows to
`results/processed/<family>.csv` (flatten nested metrics with dotted keys), records failed runs with
status failed. Runners (each `python experiments/<family>/run.py --tier tier1`):
aggregation (all systems; also the compression ladder sweep over `policy.compression` ∈ full_sketch,
order_capped, order2_plus_significant, significant_cells_only, claims_only and `policy.sketch_order` ∈ 1,2,3
and cadence 1/2/4 → `compression.csv`), poisoning (fraction sweep × systems B6,B7,B8,B9,B1,B3 and detector
sweep for §7 → `poisoning.csv`, `edge_security.csv`), privacy (quote_policy ∈ none/redacted/raw and
k_anonymity ∈ 0/3/10 vs centralized → `privacy.csv`), contradictions, temporal, failures, hierarchy
(layers 1,2,3,5,7 with equal per-node budgets), scaling (N ∈ 100,1k,10k,50k,100k with runtime/RSS/tokens/
bytes; large N with 1–3 seeds and `--quick`), models (profile sweep sim-1b..sim-14b + sim-perfect →
`models.csv`, labelled synthetic), questioning (policies none/fixed/confidence/eig/budget), independent
support (§10 via `attacks.independent_support_scenarios`), headline (`experiments/headline/run.py`: tier2
config with 5–20% malicious, all seven systems from the task's executive table, writes
`results/tables/executive_comparison.md`). `scripts/run_small.sh` (tier1, few seeds, all families),
`run_medium.sh` (tier2), `run_large.sh` (tier3), `reproduce_all.sh` (small → medium → figures → tables),
each `set -euo pipefail` and idempotent.

## Task I — tests, data generator CLI, docs, real-model backend
* `tests/test_vocab.py, test_sketch.py, test_hypothesis.py` (FDR ≤ q on pure-null resampled data; power on
  a planted effect), `test_world.py` (min-layer property: no unit below min layer has ≥ n_min·margin matching
  records; scoped effects only affect their scope; canaries unique), `test_hierarchy.py` (smoke at
  300 workers/10 rounds; lineage dedup removes copies; superseded claims retained; contested logic),
  `test_no_cheating.py` (AST/grep: forbidden imports), `test_manifest.py`, `test_config.py`
  (frontier-dominates rule).
* `data/generators/generate.py`: CLI writing `data/synthetic_workforce/<tier>_seed<k>.jsonl.gz` (records
  in the task's JSON schema) and `data/ground_truth/<tier>_seed<k>.json` (effects via `Effect.to_dict`),
  plus `README.md` in `data/` describing the schema and that no Mercor production data is used.
* `src/mycelic_bench/backends.py`: `OpenAICompatSLM(base_url, model)` implementing `perceive` for real
  models: prompt the model with the rendered record and parse structured JSON (attributes + labels); and
  `scripts/validate_with_real_slm.py` that measures attr_omission / attr_misread / label_drop /
  label_spurious / injection_susceptibility on N records against `World` truth and writes a
  `measured: true` profile YAML snippet. Must run without network when `--dry-run` (uses SimulatedSLM).
* `README.md` at the benchmark root: what it is, the limitation statement (no real model in this
  environment), quick start, repo layout, how to reproduce, how to plug a real model.

Deliver code that runs. Prefer numpy vectorisation. Keep each module self-contained; do not edit trunk
files except to add a clearly-marked hook if strictly required (say so in your report).
