# Mycelic — hierarchical enterprise intelligence benchmark

A mechanism study: how should thousands of private user-level agents abstract,
route, combine, question, verify and propagate knowledge upward so that an
enterprise kernel discovers things no single part of the organisation could?

The findings, with every table generated from the raw rows, are in
[`docs/MYCELIC_ENTERPRISE.md`](../../docs/MYCELIC_ENTERPRISE.md).
The chronological research log — including every design that was measured and
thrown away — is in [`logs/research_log.md`](logs/research_log.md).

## Layout

| file | what it is |
|---|---|
| `org.py` | heterogeneous six-level enterprise generator |
| `corpus.py` | records, hidden causal-chain patterns, five decoy classes, ground truth |
| `models.py` | model tiers as a sweepable capability vector; cost and latency model |
| `ops.py` | the shared cognitive operators every architecture calls |
| `systems.py` | all architectures and controls |
| `evalm.py` | metrics (raw measurements; composites are secondary) |
| `runner.py` | world construction, architecture registry, calibrated knobs |
| `calibrate.py` | knob fitting on held-out seeds 500-502 |
| `experiments.py` | E1-E12, each streaming rows to its own JSONL |
| `quick_paired.py` / `arm_paired.py` | paired A/B runs on identical worlds (the `artifacts/quick_*.jsonl` evidence) |
| `analysis.py` | bootstrap CIs, paired exact sign tests |
| `calibrator.py` | the learned candidate ranker: feature dumps, fitting, per-architecture adoption |
| `freeze_vnext.py` | writes the frozen vNext knobs into `calibration.json` with the file that justified each |
| `loss_accounting.py` / `loss_report.py` | per-pattern loss funnel: where each hidden pattern is lost |
| `findings.py` | headline claims, computed from the rows |
| `report.py` / `report_text.py` | generates `docs/MYCELIC_ENTERPRISE.md` |
| `loss_doc.py` | generates `docs/MYCELIC_LOSS_ACCOUNTING.md` |
| `vnext_data.py` / `vnext_docs.py` | generate `docs/mycelic_vnext/*` |
| `plots.py` | the figures in `artifacts/figures/` |
| `make_pdf.py` | renders a generated document to a paginated PDF |
| `live_tasks.py` / `score_live.py` | blind primitive-operator measurement on real models |
| `live_rank.py` | blind candidate-discrimination measurement on real models |
| `final_rerun.sh` | the full rerun on the frozen vNext configuration: E1-E12, the loss funnel and the ranker evaluation (the paired `quick_*.jsonl` evidence and the live `live_*.json` measurements are not rerun) |
| `run_suite.sh` | every experiment after E1, in sequence, on whatever `calibration.json` holds |
| `test_mycelic.py` | determinism, leakage, fairness and generator invariants |
| `artifacts/` | raw per-run metrics, one JSON object per run; `artifacts/v1/` is the archived pre-vNext benchmark |
| `keys/` | answer keys for the live measurements, kept OUT of the served directory |

## Running it

The simulator and every generator need only numpy. Figures and PDFs need
[`requirements.txt`](requirements.txt) in this directory as well.

**Regenerate every document from the committed artifacts** (minutes, changes
nothing but the "Generated" dates):

```sh
python3 -m unittest research.mycelic.test_mycelic     # 30 tests
python3 -m research.mycelic.report                    # docs/MYCELIC_ENTERPRISE.md
python3 -m research.mycelic.loss_doc                  # docs/MYCELIC_LOSS_ACCOUNTING.md
python3 -m research.mycelic.vnext_docs                # docs/mycelic_vnext/*
python3 -m research.mycelic.plots                     # artifacts/figures/*.png
python3 -m research.mycelic.make_pdf                  # docs/MYCELIC_ENTERPRISE.pdf
```

The last lines of `final_rerun.sh` render the other two PDFs.

**Rerun the published benchmark from scratch** (hours; the frozen vNext
configuration, with the arguments the final rerun used):

```sh
sh research/mycelic/final_rerun.sh question_frac=0.65:qf_v3 batched_descent=true:v3_H \
   link_time=hybrid:refit_hyb local_reextract=false:rx_reextract \
   strict_targeting=false:cal_screen triage_target_chains=none:cal_screen
```

Experiments append to their JSONL files. The headline E1/E1b/E10 tables
keep the first row per architecture, scale and seed, but every other table,
figure and finding (E2-E9, E12, the loss funnel) pools every row, so a rerun
has to start from empty files. `final_rerun.sh` does that: on a checkout where
`artifacts/v1/` already exists it moves the current rows to
`artifacts/previous/` (gitignored). Running experiments by hand on top of the
committed files changes published numbers; `git checkout
research/mycelic/artifacts` restores them.

The rerun installs the learned ranker from the committed
`artifacts/calibration.hyb.json`, fitted on the interim candidate dumps
`_hybH`, `_v3C` and `_J` (10-25 MB each, now gitignored). `_v3C` and `_J` were
committed during development and can be restored with
`git show 9713789^:research/mycelic/artifacts/hyp_features_v3C.jsonl` (and
`_J`); `_hybH` was only ever committed as an in-flight partial file, so the
weights can be reused but not refitted exactly. With complete dumps in place,
`python3 -m research.mycelic.calibrator select _hybH,_v3C,_J` refits the ranker
into `artifacts/calibration.json`; pass
`RANKER=research/mycelic/artifacts/calibration.json` to `final_rerun.sh` to
rerun with it, since by default the rerun reinstalls `calibration.hyb.json`.

**The archived v1 benchmark** (`artifacts/v1/`): `python3 -m
research.mycelic.calibrate` (with `ct` and `evidence`) fits the v1 knobs on
seeds 500-502. It rewrites `calibration.json` from scratch, dropping the
frozen vNext knobs and the learned ranker, so run it only to reproduce v1.

**Live measurements.** To re-score the committed model answers, run only the
scorers:

```sh
python3 -m research.mycelic.score_live
python3 -m research.mycelic.live_rank score           # add `rich` for the notes variant
```

`live_tasks` and `live_rank` build task files and answer keys for a *new*
measurement: they overwrite the committed ones, after which the committed
answers no longer match their keys. For a new measurement: build the task
file, hand it to the model, drop its answers in `artifacts/`, then score.

## Three things to know before reading the numbers

1. **Model tiers are a capability axis, not checkpoints.** No GPU or local
   inference was available, so named model classes are *labelled positions* on
   a swept scalar. What is established is a function from operator quality to
   architecture quality. See §5 and §23 (Direct model measurement) of the
   report for exactly what was measured on real models.
2. **Not every decision was made on held-out seeds.** The v1 knobs, the
   learned ranker's weights and its per-architecture adoption were fitted on
   calibration-seed rows (500-502). Several vNext decisions were made on
   evaluation seeds, the development panel (0-4 at 10,000 users, 0-2 at
   50,000): the question budget (`question_frac`), and the rejection of local
   re-extraction, of decoy-weighted ranker selection and of modal link timing.
   The 50,000-user headline (seeds 0-4) therefore includes three development
   seeds; seeds 5-9 at 10,000 users are the panel no development decision
   read. The report's frozen-configuration table names the file and seeds
   behind every value.
3. **The headline result is not favourable to the hierarchy on accuracy.**
   `A2_chunked_ctx`, a chunked long-context pass over every causal-predicate
   record (about 22% of all records), finds more of the hidden problems at
   every scale measured. §1 of the report lists which of the usual arguments
   for a hierarchy hold on this evidence (confidentiality, independent-support
   accuracy, resistance to planted decoys) and which do not (weak-signal
   sensitivity, lineage).
