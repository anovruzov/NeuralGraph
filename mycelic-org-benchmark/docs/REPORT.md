# Mycelic Organizational Aggregation Benchmark — Technical Report

> Results sections (§5 onward) are generated from `results/processed/*.csv`
> by the analysis in `experiments/tables.py` and `experiments/figures.py`;
> every number cites the CSV it came from. Sections §1–§4 describe the
> method and hold regardless of the numbers.

## 0. Limitation statement (read first)

No large language model was reachable from the environment that produced
`results/` (no API key; model hubs blocked by the egress policy). Every
"edge SLM" and "frontier model" in these results is therefore the
**simulated perception profile** described in `configs/models.yaml`
(attribute omission and misreading rates, label drop/spurious rates,
prompt-injection susceptibility, poison-classifier operating point, latency,
memory, energy, price). These profiles are assumptions, not measurements.
Two consequences:

1. Everything that depends on *information flow* (what reaches which node,
   lineage, dedup, cross-source consistency, compression, bandwidth,
   privacy exposure, poisoning containment by structure, hierarchy depth,
   scale) is measured for real, because the simulator implements those
   mechanisms exactly.
2. Everything that depends on *model capability* (how well a 1B vs 14B model
   extracts structured observations, how often it is fooled by an injected
   instruction, how well a content classifier spots poison) is a sensitivity
   analysis over assumed profiles. The model-ablation figure is labelled
   "synthetic capability sweep". `scripts/validate_with_real_slm.py`
   measures the same profile parameters against any OpenAI-compatible local
   endpoint so the sweep can be replaced by measured profiles.

The benchmark is built so that Mycelic can lose. It does lose in several
conditions reported below.

## 1. Question and design

**Question.** Can a five-layer hierarchy of local agents (user → team →
department → region → executive) discover, verify, compress, revise and
safely propagate organizational knowledge better than centralized
alternatives, while exposing less raw private data?

**World.** A synthetic AI-evaluation workforce (no Mercor production data is
used) of N workers rating model outputs. Each interaction carries nine
structured attributes (model family/version, task family, context length,
input format, domain, language, difficulty, tool) and an outcome (score,
error labels). The generative truth is a per-label base rate plus a list of
**effects**: conjunctions of 1–4 attribute values under which one error
label is elevated by δ ∈ [0.2, 0.45]. Effects are typed by where their
evidence lives:

| kind | scope | minimum discovery layer (computed, not assumed) |
|---|---|---|
| base_rate | organisation | any |
| local | one team's interactions | team |
| cross_team | one department; no single team has enough evidence | department |
| global | organisation-wide; evidence spread over ≥3 teams and ≥2 departments (and ≥2 regions when they exist) | region or executive |
| temporal | organisation-wide, δ switches on/off at model-release rounds | per phase |
| contradiction | three shapes: biased_positive (false claim from 1–2 correlated teams), biased_null (true effect under-reported by correlated teams), conditional (true only in some regions) | — |
| decoy | zero-δ cells adjacent to real effects | never |

"Minimum discovery layer" is computed by the generator: for each effect the
number of matching records in every unit at every layer is counted, the
sample size needed to detect δ at α = 10⁻⁸ (a per-test level that survives
Benjamini–Hochberg over the ~3·10⁵ hypotheses of an executive node's search)
and power 0.8 after average-worker label noise is computed against the most
elevated sub-marginal under the final probability matrix (exactly the contrast
the shared test uses), and the lowest layer whose best unit holds ≥ 1.25×
that count is recorded. For cross-team and global effects no single unit
below the minimum layer may hold more than half of that count. Effects that do not satisfy their
kind's layer requirement are resampled. This makes "no individual worker can
infer it alone" a verified property of every cross-team and global finding.

Workers have quality types (expert, average, weak, over/under-confident,
noisy, duplicator, adversarial) that set label recall/false-positive rates,
confidence bias and copying behaviour; 15 % of teams share a correlated
label bias; 8 % of records contain a canary token standing in for sensitive
content of nine kinds.

**Systems.** All systems use the same hypothesis search (`hypothesis.py`):
for a cell of order k the label rate inside the cell is compared with the
most elevated order-(k−1) sub-cell outside it by an exact one-sided
hypergeometric test, with Benjamini–Hochberg control at q = 0.05, a minimum
of 8 matching records and a minimum effect of 0.08. Systems differ only in
what information reaches the test:

| id | information flow |
|---|---|
| B0_isolated | each worker tests its own perceived records |
| B1_central_keyword | all raw records centralized; an analyst with 3,000 exact facet queries per 10k records per analysis round explores conjunctions (up to 4 terms) by beam search, org-wide and per unit |
| B2_central_rag | same centre; evidence per hypothesis is the 200 records retrieved by hashed bag-of-words similarity (cell and its sub-cells); a frontier model reads them |
| B3_central_llm_summary | records chunked in arrival order into 200-record windows perceived by the frontier profile; chunk sketches pooled without lineage, organisation-wide scope |
| B3_central_llm_summary_scoped | as B3 but the frontier's per-record extractions are reduced per organisational unit as well (the strongest chunked-summarisation variant) |
| B4_majority_vote | keyword-analyst candidates (ranked by facet z); workers with ≥3 matching records vote against their own sub-marginal; simple majority of ≥3 voters |
| B5_flat_agents | one aggregator receives every device's structured observations (fan-in budget) |
| B5_flat_agents_lineage | flat topology with the lineage features (isolates topology from lineage) |
| B6_hier_no_lineage | Mycelic topology with replica counts as support: no signatures, no dedup, no cross-source consistency |
| B7_mycelic | lineage-aware hierarchy: signed artifacts, evidence-hash dedup, correlation-discounted independent support, two-child synthesis rule, cross-source consistency conflicts, recent-window revision, k-anonymous sketch promotion |
| B8_mycelic_security | B7 + lineage-aware verifier + local content classifier |
| B9_mycelic_questioning | B8 + bounded QuestionArtifacts (expected-information-gain policy) |
| ORACLE_central_stats | exhaustive search on exact, copy-deduplicated sketches at every scope, orders ≤4 (an exact-data *reference*, not an upper bound on recall: it tests the largest hypothesis family) |

Centralized systems analyse on the same cadence as the hierarchy's executive node (every round at Tier 1, every third round at Tier 2/3). Device-emitted bare claims (attacks) are ingested unverified by every lineage-blind system, centralized or flat; ORACLE ignores them. Centralized systems receive strictly more information than the hierarchy
(exact attributes, full text, all units), the frontier profile dominates the
edge profile on every fidelity parameter (enforced by `config.validate`),
and no system reads ground truth (`tests/test_no_cheating.py`).

**Artifacts.** A device emits *structured observations* (attribute row,
labels, evidence fingerprint, confidence) — no text unless the quote policy
is deliberately weakened for the privacy experiment. A team node keeps its
observations locally and promotes a **sketch** (per-cell counts with
distinct-worker/team/department/region columns) and **claims** (signature,
statistics, support record, lineage record, validity, status). Higher nodes
pool sketches, synthesize claims at their own scope, forward child claims as
scoped knowledge, open **conflicts** when children with elevated evidence
disagree with children holding powerful null evidence, resolve conflicts by
independent support ratio or region split, and supersede claims that a
recent window refutes. Every system, centralized or hierarchical, also
re-tests its accepted claims on cumulative evidence each analysis round and
withdraws those that are no longer nominally significant (p > 0.05), so an
early fluke does not persist by default. Bytes are metered on a compact wire
encoding of every artifact that crosses a node boundary.

## 2. Metrics

Discovery: a claim matches an effect when signature (cell, label, sign)
equals and the claim's scope is nested with the effect's scope. Matches are
`exact`, `under_specified` (a projection of a true effect),
`over_specified` (asserts an interaction that does not exist),
`confounded_true` (a true association in the generative population that is
not a listed effect, e.g. a specialization confound), `false`
(population-false), or `poison` (population-false with ≥30 % attack
records in its cell). Precision (strict) counts exact matches only; the
**false association rate** counts claims that are false in the noise-free
population truth — the number to watch. Recall is reported per effect kind,
strict (accepted claims) and lenient (accepted or contested).

Time-to-discovery is the first round the executive accepted the claim.
Evidence coverage, lineage correctness and source diversity come from the
claim's lineage pointers against the true distribution of matching
records. Calibration is the expected calibration error of claim confidence
against population truth. The layer-fidelity table tracks each true effect
through the transitions (survived, dropped, distorted, duplicated,
incorrectly promoted, independent support preserved, lineage retained).
Compression ratio is raw record bytes divided by promoted bytes. Privacy:
canary leakage, bytes off device, k-anonymity reconstructability. Costs:
tokens (edge and cloud), model calls, latency, energy, USD from the
profiles.

## 3. Statistical design

Every condition is run on the same worlds (paired by seed). Comparisons
against B7_mycelic report mean ± sd, paired-bootstrap 95 % intervals
(10,000 resamples), paired t and Wilcoxon signed-rank p-values with Holm
correction within each metric family, and Cohen's d_z. Tier 1 uses 30
seeds for the main comparison and 5–10 for sweeps; Tier 2 uses 5. Seeds,
configurations, git commit, hardware, dataset hashes and runtimes are in
every `results/raw/<family>/<run>/manifest.json`.

## 4. What the simulator does and does not model

Modelled: organisation structure and specialisation, worker quality mix,
correlated team bias, copies and paraphrases, sensitive content, temporal
phases and model releases, ten attack types with device-side behaviour,
node failures, partitions and delays, perception noise, bounded wire
encodings, questioning with local-only answers.

Not modelled: natural-language reasoning (claims are structured), real
inference latency, network topology, human review, model drift beyond the
release schedule, and adversaries that adapt to the detector.
