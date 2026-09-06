# Experiment Protocol

## Research question

When a distributed collective contains correlated evidence descendants, can lineage-aware strategic questioning acquire independent support more efficiently than random or confidence-only questioning?

## Evidence class

**Synthetic simulation / mechanism probe.**

This experiment is intentionally not described as a deployment-scale benchmark.

## Generative model

For each synthetic proposition:

1. A latent binary truth is sampled.
2. Several upstream evidence roots are created.
3. A root is either truth-aligned or corrupted.
4. Candidate agents inherit one root, producing correlated descendants.
5. A small amount of local observation noise may flip an individual descendant.
6. The initial network sees only a small subset of candidate agents.

This creates the central failure mode the paper studies: many apparent copies may still depend on a small number of upstream roots.

## Shared answer rule

Every policy uses the same root-balanced aggregator:

- descendants are grouped by lineage root;
- each observed root receives one effective vote;
- a root's vote is the majority response among its observed descendants;
- final confidence is the fraction of root votes supporting the predicted answer.

This prevents the experiment from conflating question selection with a different final inference rule.

## Policies

### none

No additional evidence is requested.

### random

For each claim, ask up to the fixed question budget, selecting unseen candidate agents uniformly at random.

### uncertainty

Ask only while root-balanced answer confidence is below the uncertainty threshold. Selection among unseen candidates is random.

This baseline exposes a key failure mode: a collective can be confidently wrong when many descendants share the same corrupted root.

### lineage

Ask while fewer than `min_independent_roots` are observed. Prefer a candidate whose lineage root is not yet represented.

This isolates lineage-diversifying acquisition.

### continual

At each step compute three trigger components:

- uncertainty: `1 - confidence`
- lineage weakness: lack of independent roots
- conflict: fraction of observed roots opposing the current root-majority answer

If the combined trigger is nonzero and budget remains, ask a candidate from an unseen root when possible; otherwise ask a random unseen candidate. Stop early when confidence is high, independent-root support is sufficient, and conflict is low.

## Primary metrics

### Accuracy

Final root-balanced prediction accuracy.

### False confident consensus

Fraction of claims that are wrong while final confidence is at least the configured high-confidence threshold.

This is the synthetic analogue of a dangerous epistemic failure: apparent certainty without truth.

### Independent roots acquired per question

`new_lineage_roots / questions_asked`

This is the cleanest mechanism-efficiency metric.

### Conflict detection rate

Among claims where at least two distinct root-level answers exist in the candidate pool, the fraction where the questioning policy actually observes both answer values.

### Resolved epistemic gap rate

Among claims initially wrong or below the high-confidence threshold, the fraction ending correct and high-confidence.

## Secondary metrics

- questions per claim
- final independent roots observed
- runtime

## Scale interpretation

`agent_scales` controls the global population label and the number of unique agent IDs from which each per-claim candidate coalition is sampled. The simulator deliberately limits the per-claim candidate coalition (`candidate_pool_size`) to model sparse routing rather than all-to-all communication.

Therefore a 10,000-agent result means **a synthetic 10,000-agent population with bounded per-claim routing**, not a real 10,000-process deployment.

## Sensitivity

Before publication, sweep at least:

- `corrupt_root_prob`
- `root_zipf_exponent`
- `question_budget_per_claim`
- `min_independent_roots`
- `candidate_pool_size`

A useful mechanism should not depend on one hand-picked configuration.

`src/sensitivity_sweep.py` (`bash run_sensitivity.sh`) implements exactly this
sweep, one factor at a time around the base configuration, and
`tests/test_sensitivity.py` asserts that the five axes above are all covered and
that each axis includes its base value.

Every strategy inside a cell sees the same generated worlds, and the sweep reuses
the simulator's seeding, so a cell at the base parameter values reproduces the
corresponding headline rows exactly — also asserted by a test. Comparisons are
therefore seed-paired: the reported quantity is the mean per-seed difference
between two policies, with its standard error over seeds. A cell is called a win
for the focus policy when that mean is more than 2 SEM better, a loss when more
than 2 SEM worse, and a tie otherwise. No multiplicity correction is applied
across the sweep's comparisons, so individual cells are descriptive rather than
confirmatory.

Two classes of comparison are reported separately, because they answer different
questions:

- focus policy (`lineage`, `continual`) against the baselines (`none`, `random`,
  `uncertainty`) — the mechanism claim;
- `lineage` against `continual` — a cost/benefit trade-off between two
  lineage-aware policies, not evidence for or against the mechanism.

Losses in either class are listed in full in `results/sensitivity_summary.md`.
