# Mycelic organizational aggregation benchmark

An adversarial, reproducible benchmark for **hierarchical local-agent knowledge
aggregation** ("Mycelic": edge agents on workers' devices promote bounded,
lineage-carrying abstractions up a worker -> team -> department -> region ->
executive tree) against **centralized alternatives** that receive every raw
record. A synthetic organization of N workers evaluates AI models; the world
hides *effects* (conjunctions of task attributes under which an error label is
elevated) whose evidence is spread across teams, departments or regions so that
no single unit below some layer can detect them. Every system is scored on
whether it reconstructs those effects, and at what cost:

* discovery recall / precision / false-discovery rate by effect kind (local,
  cross-team, global, temporal, contradiction) and time-to-discovery,
* aggregation fidelity across layers (survived / dropped / distorted /
  duplicated / incorrectly promoted / merged / contradicted / resolved /
  lineage preserved / independent support preserved / privacy violated),
* poison propagation under eleven attack types and seven detectors,
* privacy exposure (canary leakage, bytes off device, re-identifiability),
* knowledge survival under user / node / link failures,
* compression, bandwidth, latency, energy and cost,
* retrieval routing over the hierarchy and active questioning.

Read [DESIGN.md](DESIGN.md) for the full specification (world model, artifacts,
hierarchy engine, attacks, baselines, metrics, statistics) and
[MODULE_SPEC.md](MODULE_SPEC.md) for the per-module contracts. The written
report lives in [docs/REPORT.md](docs/REPORT.md) (by the main author); every
number in it is produced by `experiments/*` from `results/raw/` and cites a
file under `results/processed/`.

## Limitation statement (read this first)

**No real language model was reachable in the environment that produced
`results/`.** There was no API key and no network access to model hubs.
Every edge-model and frontier-model behaviour in the results (attribute
omission and misreads, label drops and spurious labels, prompt-injection
susceptibility, the operating point of content classifiers, latency, energy,
cost) is therefore a **simulated profile** from
[`configs/models.yaml`](configs/models.yaml) (`sim-1b` .. `sim-14b`,
`sim-frontier`, `sim-perfect`, all `measured: false`). Every manifest under
`results/raw/` records `backend: simulated-slm` and repeats this statement.
Concretely:

* the *structural* mechanisms (sketch pooling, lineage, independent-support
  discounting, dedup, consistency tests, contradiction handling, revision,
  compression, routing, failure recovery, the hypothesis test itself) are
  fully implemented and measured;
* the *model-dependent* parts are profile operating points, swept in
  `experiments/models/` and labelled "synthetic capability sweep", never
  "measured model results";
* the profiles are ordered so that the simulated frontier model dominates the
  simulated edge model on every fidelity parameter (honesty rule 1.4,
  enforced by `config.validate`), i.e. the centralized baselines never get a
  worse extractor than Mycelic.

To replace the assumption with a measurement, run
[`scripts/validate_with_real_slm.py`](scripts/validate_with_real_slm.py)
against any OpenAI-compatible or Ollama endpoint (see "Plugging in a real
model" below), paste the `measured: true` profile it writes into
`configs/models.yaml`, and re-run `scripts/reproduce_all.sh`. The figures and
tables label rows by `profile_kind` (`synthetic` / `measured`).

## Quick start

```bash
cd mycelic-org-benchmark
python3 -m pip install -e ".[dev]"          # numpy, scipy, pandas, pyyaml, matplotlib, pytest, scikit-learn
export PYTHONPATH=src

python3 -m pytest -q                        # unit tests incl. the honesty rules (tests/test_no_cheating.py)

# one family, reduced grid, one seed, small world (a minute)
python3 experiments/failures/run.py --tier tier1 --quick --seeds 1 \
    --set org.n_workers=300 --set org.n_rounds=9 --set world.n_local_findings=20 \
    --set world.n_cross_team_findings=5 --set world.n_global_findings=3 --set world.n_decoys=5 \
    --set world.n_contradictions=2 --set world.n_temporal_revisions=2

# the tier-1 headline comparison (all systems on the same worlds, 5 seeds, 3 processes)
python3 experiments/aggregation/run.py --tier tier1 --seeds 5 --jobs 3 --only aggregation

python3 experiments/figures.py --results results     # results/figures/*.png|pdf from results/processed/*.csv
python3 experiments/tables.py  --results results     # results/tables/*.md
```

Every runner shares the CLI of `experiments/common.py`: `--tier tier1|tier2|tier3`,
`--seeds N`, `--seed-offset K`, `--systems a,b`, `--set key=value` (repeatable dotted
config override), `--results DIR`, `--quick`, `--jobs N` (one seed per forked process),
`--tag`. Family-specific flags are documented in each runner's docstring
(`python3 experiments/<family>/run.py --help`).

## Repository layout

```
DESIGN.md, MODULE_SPEC.md        specification; docs/REPORT.md is the written report
configs/                         organization.yaml (workforce, world), models.yaml (profiles; ASSUMPTIONS unless
                                 measured: true), attacks.yaml, experiments.yaml (policy defaults, tiers, systems)
src/mycelic_bench/
  vocab.py, sketch.py            attribute vocabulary, cell index, sparse count sketches (wire-metered)
  world.py                       synthetic organization + hidden effects + workforce noise + canaries (ground truth)
  hypothesis.py                  the ONE interaction test / BH-FDR search every system uses
  agents.py, backends.py         edge perception: SimulatedSLM (profile noise) and OpenAICompatSLM (real model)
  hierarchy.py                   UnitNode / Hierarchy engine: ingest, pool, merge with lineage, synthesize,
                                 contradict, revise, question, promote under a compression policy
  security.py, security_impl.py  detectors: rules, classifiers (profile operating points), lineage-aware verifier
  questioning.py, questioning_impl.py   active questioning policies (fixed / confidence / EIG / budget)
  baselines.py                   B0-B4 centralized baselines and the ORACLE, over the full raw records
  attacks.py                     eleven attack types + independent-support scenarios
  failures.py                    failure injection (users, nodes, links) and paired survival metrics
  routing.py                     retrieval routers over a finished hierarchy
  evaluate.py, metrics.py        discovery matching, fidelity, temporal, contradiction, privacy metrics
  stats.py                       paired bootstrap / t / Wilcoxon / Cohen d_z / Holm
  runner.py, manifest.py, config.py     run one system; provenance manifests; configuration
experiments/
  common.py                      shared harness: CLI, tier config, grid executor, manifests, processed CSV rows
  <family>/run.py                aggregation (+compression), poisoning (+edge_security), privacy, contradictions,
                                 temporal, failures, hierarchy, scaling, models, questioning, independent_support,
                                 routing, headline
  figures.py, tables.py          F1-F10 + investor chart, markdown tables, ONLY from results/processed/*.csv
scripts/
  run_small.sh / run_medium.sh / run_large.sh / reproduce_all.sh   tier-1 / tier-2 / tier-3 sweeps, full reproduction
  validate_with_real_slm.py      measure a real model's fidelity profile
data/
  generators/generate.py         write synthetic_workforce/<tier>_seed<k>.jsonl.gz + ground_truth/<tier>_seed<k>.json
  README.md                      record and ground-truth schema; the data is fully synthetic
results/
  raw/<family>/<run_id>/         manifest.json, metrics.json, claims.jsonl, log.txt (failed runs kept)
  processed/<family>.csv         one flat row per run (append-only)
  figures/, tables/              produced only by experiments/figures.py and experiments/tables.py
tests/                           unit tests, including the static honesty checks
```

## Systems

All systems share `hypothesis.search` (the same interaction test, BH-corrected
at the same q); they differ only in what information reaches it. Centralized
baselines receive **strictly more** than Mycelic: every raw record with exact
attributes and full text.

| system | what it is | receives | lineage |
|---|---|---|---|
| `B0_isolated` | each worker searches only its own cumulative data | own records | n/a |
| `B1_central_keyword` | central inverted index; analyst explores conjunctions by beam search under a query budget | all raw records | none (copies and spoofs count) |
| `B2_central_rag` | hashed bag-of-words retrieval; rate estimated from the top-k retrieved records per hypothesis | all raw records | none |
| `B3_central_llm_summary` | records chunked; a frontier-profile extractor builds an order-<=3 sketch per chunk; pooled without lineage | all raw records (all text to cloud) | none |
| `B4_majority_vote` | B1's candidate cells; each worker votes from its own data; replica count decides | all raw records | replica count |
| `B5_flat_agents` | every edge agent promotes to one aggregator with a fan-in byte budget | sketches | none |
| `B6_hier_no_lineage` | the Mycelic topology without signatures, dedup, correlation discount or consistency test | sketches + claims | replica count |
| `B7_mycelic` | lineage-aware five-layer hierarchy with DLP (no text leaves a device) | sketches + claims with lineage | yes |
| `B8_mycelic_security` | B7 + local SLM content classifier + lineage-aware verifier | same | yes |
| `B9_mycelic_questioning` | B8 + expected-information-gain questioning for order-(k+1) cells | same + question answers | yes |
| `ORACLE_central_stats` | exhaustive search on the exact sketch of all observed records at every scope, exact-copy dedup | everything | exact |

## Metrics

Per run (`results/raw/<family>/<run_id>/metrics.json`, flattened as
`metrics.*` columns in the processed CSVs):

* **discovery**: `n_accepted`, `precision_strict`, `precision_lenient`,
  `false_discovery_rate`, `false_association_rate`, `recall_local`,
  `recall_cross_team`, `recall_global`, `recall_temporal`,
  `recall_contradiction`, `ttd_global_median`, `ttd_cross_team_median`,
  `categories` (exact / over- / under-specified / decoy / false / poison);
* **evidence & lineage**: `evidence_coverage`, `lineage_correctness`,
  `source_diversity`, `ece` (calibration);
* **temporal**: `revision_accuracy`, `stale_persistence`;
* **contradictions**: `contradiction_f1`, `correct_resolution_rate`,
  `incorrect_resolution_rate`;
* **privacy**: `raw_sensitive_leakage`, `n_canaries_exposed`,
  `bytes_off_device`, `fraction_raw_exposed`, `reconstructability`;
* **communication & cost**: `bytes_transmitted`, `compression_ratio`,
  `tokens`, `tokens_to_cloud`, `model_calls`, `latency_ms_est`,
  `energy_j_est`, `cost_usd_est`, `runtime_s`, `peak_rss_mb`;
* **poisoning**: `poison.attack_records`, `poison.poison_claims_at_root`,
  `poison.poison_promotion_rate`, `poison.poison_by_layer`, detector
  recall / precision / F1 / false suppression (`security.*`);
* **fidelity**: the per-transition table of the eleven states (`fidelity.*`)
  and `failure_reasons.*` (why each hidden effect was missed);
* **failures** (`failures.csv`): `knowledge_survival`,
  `useful_discovery_survival`, `lineage_survival`,
  `contradiction_detection_survival`, `recovery_latency`, `repair_quality`,
  `false_reconstruction_rate`, all paired against the no-failure run of the
  same seed;
* **routing** (`routing.csv`): recall at contact budget, correct destination,
  unnecessary escalation, messages, bytes, hops, cross-team discovery,
  private-data movement;
* **questioning**: `questions_asked`, `question_bytes`,
  `marginal_value_per_question`.

Statistics (`stats.py`, `experiments/tables.py`) are paired by seed: paired
bootstrap 95 % CI (10,000 resamples), paired t-test, Wilcoxon signed-rank,
Cohen's d_z, Holm correction within a comparison family. Never a p-value
without an effect size. Tier 1 uses 30 seeds, tier 2 >= 10, tier 3 3-5 (and
the figures print the seed count).

## Experiment families

| runner | processed CSV | sweep |
|---|---|---|
| `aggregation` | `aggregation.csv`, `compression.csv` | all systems on the same worlds; B7 under compression x sketch order x cadence |
| `poisoning` | `poisoning.csv`, `edge_security.csv` | malicious fraction 0-40 % x systems; detector sweep at 10 / 20 % |
| `privacy` | `privacy.csv` | quote policy x k-anonymity vs centralized |
| `contradictions` | `contradictions.csv` | 3x planted contradiction groups; resolve-ratio sweep |
| `temporal` | `temporal.csv` | 3x planted revision groups; recent-window sweep |
| `failures` | `failures.csv` | 9 failure kinds x {1, 5, 10, 20, 30, 50} % paired with the no-failure run |
| `hierarchy` | `hierarchy.csv` | 1 / 2 / 3 / 5 / 7 layers with equal per-node budgets, plus flat agents |
| `scaling` | `scaling.csv` | 100 .. 100,000 workers: runtime, RSS, tokens, bytes |
| `models` | `models.csv` | synthetic capability sweep sim-1b .. sim-14b, sim-perfect (labelled synthetic) |
| `questioning` | `questioning.csv` | none / fixed / confidence / eig / budget |
| `independent_support` | `independent_support.csv`, `..._clusters.csv` | copies, paraphrases, copying workers, independent workers, cross-department, cross-region |
| `routing` | `routing.csv` | local / global / hybrid / hierarchy-aware / provenance-aware / lineage-aware / oracle routers |
| `headline` | `headline.csv`, `results/tables/executive_comparison.md` | the executive table at tier 2 with 10 % malicious workers |

## Reproduction

```bash
scripts/reproduce_all.sh                 # run_small (tier 1, every family) -> run_medium (tier 2) -> figures -> tables
SEEDS=30 scripts/reproduce_all.sh        # the full tier-1 seed count
LARGE=1 scripts/reproduce_all.sh         # also run_large (tier 3: 50k / 100k workers, 2 seeds)
QUICK=1 SEEDS=1 scripts/reproduce_all.sh # end-to-end smoke test in minutes
```

`scripts/run_small.sh` (tier 1, `SEEDS=5 JOBS=3`), `scripts/run_medium.sh`
(tier 2: headline, aggregation, poisoning, scaling; `SEEDS=5 JOBS=2`) and
`scripts/run_large.sh` (tier 3: scaling at 50k / 100k workers and headline;
`SEEDS=2`) can be run on their own; `FAMILIES="failures headline"` restricts a
script to some families, `EXTRA_ARGS` is appended to every runner call,
`RESULTS` relocates the results tree. Each script is `set -euo pipefail`,
continues past a family whose runner exits non-zero (reported at the end,
`STOP_ON_FAIL=1` to abort), and logs to `results/logs/<family>.log`.

**Processed CSVs are append-only.** Every invocation appends rows with a fresh
`batch_id`; failed runs are kept with `status: failed` (DESIGN.md §1.5). To
reproduce from scratch: `rm -rf results/raw results/processed results/figures
results/tables` first. Each run directory holds a `manifest.json` with the git
commit, the fully resolved configuration, the backend and model profiles, the
seed, hardware, runtime, peak RSS, the dataset hash, output paths and status.

Runtime guide (single machine, numpy): a tier-1 world generates in ~1 s and
one hierarchy run takes ~25 s; `run_small.sh` with 5 seeds is on the order of
hours, tier 2 needs a few GB per process, tier 3 tens of GB and hours per run.

The synthetic data itself can be materialised for inspection with
`python3 data/generators/generate.py --tier tier1 --seed 0 --out data/`
(`data/README.md` documents the schema; the committed `tier1_seed0` files are
about 1 MB).

## Plugging in a real model

1. Serve a model behind an OpenAI-compatible endpoint (LM Studio, llama.cpp
   server, vLLM) or Ollama.
2. Measure its fidelity profile on synthetic records (no ground truth about
   effects is used, only the records' exact attributes and observed labels):

   ```bash
   PYTHONPATH=src python3 scripts/validate_with_real_slm.py \
       --base-url http://127.0.0.1:1234 --model google/gemma-4-e4b --n 300
   # Ollama:  --base-url http://127.0.0.1:11434 --backend ollama --model qwen3:4b
   # no endpoint at all (exercises the pipeline on the simulator): --dry-run --profile sim-3b
   ```

   It prints attr_omission, attr_misread, label_drop, label_spurious and
   injection_susceptibility with 95 % CIs and writes
   `configs/measured/<name>.yaml`, a `measured: true` profile snippet whose
   `notes` say which parameters were measured and which were copied from the
   base profile (content-classifier ROC point, RAM, energy, price).
3. Paste the snippet into `configs/models.yaml` under `models.profiles` and set
   `models.edge_profile` to it. Honesty rule 1.4 still applies: the frontier
   profile must dominate the edge profile on every fidelity parameter (measure
   or raise the frontier profile too); `config.validate` refuses otherwise.
4. Optionally run the edge agents on the real model instead of the profile:
   `models.backend: openai-compat` (or `ollama`) plus `models.base_url` /
   `models.model` (or `LLM_BASE_URL` / `LLM_MODEL`), then `scripts/reproduce_all.sh`.
   Manifests then record the backend and the rows carry `profile_kind: measured`.

`src/mycelic_bench/backends.py` renders each record as text, asks the model
for strict JSON (attributes + error labels), parses replies robustly (fences,
`<think>` blocks, aliases; unknown values become omissions), batches calls
over a thread pool and meters tokens and latency.

## Honesty rules

Enforced by `tests/test_no_cheating.py`, `tests/test_config.py` and the
harness: no aggregator, baseline or agent imports ground truth; every system
uses the same hypothesis engine; centralized baselines always see the full raw
records; the simulated frontier dominates the simulated edge; failed runs are
recorded, never deleted; every reported number comes from
`results/processed/` via `experiments/`; every manifest states the model
backend limitation. See DESIGN.md §1.

## Further reading

* [DESIGN.md](DESIGN.md) - the design specification (interfaces, metrics, statistics, tiers)
* [MODULE_SPEC.md](MODULE_SPEC.md) - per-module contracts and the experiment / script / data tasks
* [docs/REPORT.md](docs/REPORT.md) - the written report (results, figures, tables; by the main author)
* [data/README.md](data/README.md) - the synthetic data schema and the no-production-data statement
