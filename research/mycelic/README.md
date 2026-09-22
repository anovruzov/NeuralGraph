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
| `experiments.py` | E1-E11, each streaming rows to its own JSONL |
| `analysis.py` | bootstrap CIs, paired exact sign tests |
| `findings.py` | headline claims, computed from the rows |
| `report.py` / `report_text.py` | the report generator |
| `make_pdf.py` | renders the generated report to a paginated PDF |
| `live_tasks.py` / `score_live.py` | blind primitive-operator measurement on real models |
| `live_rank.py` | blind candidate-discrimination measurement on real models |
| `test_mycelic.py` | determinism, leakage, fairness and generator invariants |
| `artifacts/` | raw per-run metrics, one JSON object per run |
| `keys/` | answer keys for the live measurements, kept OUT of the served directory |

## Running it

```sh
python3 -m unittest research.mycelic.test_mycelic     # 27 tests
python3 -m research.mycelic.calibrate                 # fit knobs, held-out seeds
python3 -m research.mycelic.calibrate ct
python3 -m research.mycelic.calibrate evidence
python3 -m research.mycelic.experiments e1            # baselines x scales x seeds
sh research/mycelic/run_suite.sh                      # everything else
python3 -m research.mycelic.report                    # regenerate the document
```

Live measurements need a real model to answer them; build the task file, hand
it to the model, drop its answers in `artifacts/`, then score:

```sh
python3 -m research.mycelic.live_tasks
python3 -m research.mycelic.live_rank                 # add `rich` for the notes variant
python3 -m research.mycelic.score_live
python3 -m research.mycelic.live_rank score
```

## Three things to know before reading the numbers

1. **Model tiers are a capability axis, not checkpoints.** No GPU or local
   inference was available, so named model classes are *labelled positions* on
   a swept scalar. What is established is a function from operator quality to
   architecture quality. See §21 of the report for exactly what was measured
   on real models.
2. **Every knob is fitted on seeds 500-502 and frozen.** Two features that
   looked like large wins on a single evaluation seed were rejected by that
   protocol and are reported as non-results.
3. **The headline result is not favourable to the hierarchy on accuracy.**
   A centralised schema-aware retrieval pass into one large-context call beats
   it at every scale measured. The hierarchy's case rests on confidentiality,
   rare-signal recall and provenance, which the report quantifies separately.
