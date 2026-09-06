# Continual Discovery — sensitivity sweep

> **Evidence class: synthetic simulation / mechanism probe. These are not
> production deployment results.**

One-factor-at-a-time sweep around the base configuration, 1500 claims x 8 seeds per cell at a synthetic population of 1000 agents with bounded per-claim routing. Every strategy in a cell sees the same generated worlds, so all comparisons below are seed-paired. A cell at the base parameter values reproduces the corresponding rows of the headline sweep exactly (asserted in `tests/test_sensitivity.py`).

Verdicts use the paired mean difference against its standard error over seeds: **win** if the mean is more than 2 SEM better, **loss** if more than 2 SEM worse, **tie** otherwise. Metrics are oriented so positive always means the focus policy is better. No significance correction is applied across the 736 comparisons; read individual cells as descriptive.

## What the sweep says

- `lineage` vs `none` on **accuracy**: 22 wins / 1 ties / 0 losses across 23 cells
- `lineage` vs `random` on **accuracy**: 3 wins / 8 ties / 12 losses across 23 cells
- `lineage` vs `uncertainty` on **accuracy**: 3 wins / 10 ties / 10 losses across 23 cells
- `lineage` vs `none` on **false_confident_consensus_rate**: 23 wins / 0 ties / 0 losses across 23 cells
- `lineage` vs `random` on **false_confident_consensus_rate**: 7 wins / 14 ties / 2 losses across 23 cells
- `lineage` vs `uncertainty` on **false_confident_consensus_rate**: 23 wins / 0 ties / 0 losses across 23 cells

- `continual` vs `none` on **accuracy**: 23 wins / 0 ties / 0 losses across 23 cells
- `continual` vs `random` on **accuracy**: 23 wins / 0 ties / 0 losses across 23 cells
- `continual` vs `uncertainty` on **accuracy**: 23 wins / 0 ties / 0 losses across 23 cells
- `continual` vs `none` on **false_confident_consensus_rate**: 20 wins / 3 ties / 0 losses across 23 cells
- `continual` vs `random` on **false_confident_consensus_rate**: 3 wins / 5 ties / 15 losses across 23 cells
- `continual` vs `uncertainty` on **false_confident_consensus_rate**: 21 wins / 2 ties / 0 losses across 23 cells

Read the per-axis tables before quoting any of this. The two lineage-aware policies buy different things: `lineage` spends almost no questions and is the cheapest way to suppress confidently-wrong consensus, while `continual` spends more and is the only policy that resolves initial epistemic gaps at a meaningful rate. Neither dominates the other on every metric, and both are compared against `random`, which always spends the full budget.

## corrupt_root_prob

| value | strategy | accuracy | false confident consensus | Q / claim | new roots / Q | resolved gaps |
|---|---|---:|---:|---:|---:|---:|
| 0.05 | none | 0.878 ± 0.008 | 0.017 ± 0.001 | 0.00 | 0.000 | 0.000 |
| 0.05 | random | 0.907 ± 0.006 | 0.009 ± 0.002 | 3.00 | 0.227 | 0.140 |
| 0.05 | uncertainty | 0.904 ± 0.004 | 0.017 ± 0.001 | 0.86 | 0.235 | 0.012 |
| 0.05 | lineage | 0.898 ± 0.004 | 0.007 ± 0.002 | 0.25 | 0.913 | 0.000 |
| 0.05 | continual | 0.940 ± 0.005 | 0.009 ± 0.001 | 1.59 | 0.838 | 0.368 |
| 0.1 | none | 0.846 ± 0.008 | 0.023 ± 0.003 | 0.00 | 0.000 | 0.000 |
| 0.1 | random | 0.871 ± 0.008 | 0.013 ± 0.002 | 3.00 | 0.228 | 0.113 |
| 0.1 | uncertainty | 0.869 ± 0.009 | 0.023 ± 0.002 | 1.01 | 0.229 | 0.009 |
| 0.1 | lineage | 0.867 ± 0.008 | 0.011 ± 0.002 | 0.24 | 0.927 | 0.000 |
| 0.1 | continual | 0.910 ± 0.006 | 0.014 ± 0.003 | 1.76 | 0.835 | 0.307 |
| 0.14 (base) | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 0.14 (base) | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 0.14 (base) | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 0.14 (base) | lineage | 0.820 ± 0.009 | 0.015 ± 0.002 | 0.25 | 0.925 | 0.000 |
| 0.14 (base) | continual | 0.871 ± 0.005 | 0.019 ± 0.003 | 1.94 | 0.843 | 0.263 |
| 0.2 | none | 0.740 ± 0.006 | 0.046 ± 0.003 | 0.00 | 0.000 | 0.000 |
| 0.2 | random | 0.763 ± 0.009 | 0.030 ± 0.003 | 3.00 | 0.227 | 0.075 |
| 0.2 | uncertainty | 0.760 ± 0.013 | 0.047 ± 0.003 | 1.34 | 0.232 | 0.005 |
| 0.2 | lineage | 0.758 ± 0.009 | 0.028 ± 0.003 | 0.24 | 0.932 | 0.000 |
| 0.2 | continual | 0.807 ± 0.010 | 0.037 ± 0.004 | 2.12 | 0.836 | 0.188 |
| 0.3 | none | 0.631 ± 0.016 | 0.080 ± 0.007 | 0.00 | 0.000 | 0.000 |
| 0.3 | random | 0.647 ± 0.014 | 0.059 ± 0.006 | 3.00 | 0.224 | 0.046 |
| 0.3 | uncertainty | 0.646 ± 0.014 | 0.081 ± 0.007 | 1.51 | 0.230 | 0.005 |
| 0.3 | lineage | 0.641 ± 0.016 | 0.055 ± 0.008 | 0.25 | 0.916 | 0.000 |
| 0.3 | continual | 0.678 ± 0.017 | 0.076 ± 0.007 | 2.32 | 0.830 | 0.115 |

## root_zipf_exponent

| value | strategy | accuracy | false confident consensus | Q / claim | new roots / Q | resolved gaps |
|---|---|---:|---:|---:|---:|---:|
| 0.0 | none | 0.832 ± 0.008 | 0.014 ± 0.003 | 0.00 | 0.000 | 0.000 |
| 0.0 | random | 0.855 ± 0.008 | 0.012 ± 0.004 | 3.00 | 0.326 | 0.168 |
| 0.0 | uncertainty | 0.858 ± 0.007 | 0.014 ± 0.003 | 1.05 | 0.332 | 0.009 |
| 0.0 | lineage | 0.834 ± 0.008 | 0.013 ± 0.003 | 0.05 | 0.591 | 0.000 |
| 0.0 | continual | 0.882 ± 0.008 | 0.013 ± 0.003 | 1.90 | 0.738 | 0.207 |
| 0.6 | none | 0.822 ± 0.007 | 0.016 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 0.6 | random | 0.847 ± 0.008 | 0.012 ± 0.001 | 3.00 | 0.302 | 0.163 |
| 0.6 | uncertainty | 0.851 ± 0.006 | 0.017 ± 0.004 | 1.06 | 0.305 | 0.009 |
| 0.6 | lineage | 0.827 ± 0.007 | 0.013 ± 0.004 | 0.07 | 0.719 | 0.000 |
| 0.6 | continual | 0.883 ± 0.009 | 0.016 ± 0.005 | 1.89 | 0.769 | 0.223 |
| 1.0 | none | 0.813 ± 0.008 | 0.021 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 1.0 | random | 0.839 ± 0.009 | 0.014 ± 0.002 | 3.00 | 0.263 | 0.130 |
| 1.0 | uncertainty | 0.837 ± 0.008 | 0.021 ± 0.004 | 1.10 | 0.272 | 0.007 |
| 1.0 | lineage | 0.822 ± 0.009 | 0.014 ± 0.004 | 0.13 | 0.853 | 0.000 |
| 1.0 | continual | 0.875 ± 0.007 | 0.017 ± 0.003 | 1.91 | 0.807 | 0.246 |
| 1.35 (base) | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 1.35 (base) | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 1.35 (base) | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 1.35 (base) | lineage | 0.820 ± 0.009 | 0.015 ± 0.002 | 0.25 | 0.925 | 0.000 |
| 1.35 (base) | continual | 0.871 ± 0.005 | 0.019 ± 0.003 | 1.94 | 0.843 | 0.263 |
| 1.8 | none | 0.784 ± 0.011 | 0.049 ± 0.006 | 0.00 | 0.000 | 0.000 |
| 1.8 | random | 0.808 ± 0.007 | 0.027 ± 0.004 | 3.00 | 0.186 | 0.044 |
| 1.8 | uncertainty | 0.805 ± 0.008 | 0.050 ± 0.006 | 1.20 | 0.180 | 0.005 |
| 1.8 | lineage | 0.820 ± 0.008 | 0.018 ± 0.005 | 0.48 | 0.962 | 0.000 |
| 1.8 | continual | 0.859 ± 0.009 | 0.024 ± 0.006 | 1.98 | 0.863 | 0.256 |

## question_budget_per_claim

| value | strategy | accuracy | false confident consensus | Q / claim | new roots / Q | resolved gaps |
|---|---|---:|---:|---:|---:|---:|
| 1 | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 1 | random | 0.812 ± 0.003 | 0.024 ± 0.005 | 1.00 | 0.254 | 0.037 |
| 1 | uncertainty | 0.812 ± 0.004 | 0.028 ± 0.004 | 0.43 | 0.244 | 0.002 |
| 1 | lineage | 0.819 ± 0.007 | 0.016 ± 0.002 | 0.22 | 0.974 | 0.000 |
| 1 | continual | 0.838 ± 0.007 | 0.019 ± 0.003 | 0.73 | 0.967 | 0.145 |
| 2 | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 2 | random | 0.821 ± 0.007 | 0.020 ± 0.003 | 2.00 | 0.249 | 0.071 |
| 2 | uncertainty | 0.822 ± 0.009 | 0.029 ± 0.004 | 0.81 | 0.253 | 0.005 |
| 2 | lineage | 0.821 ± 0.010 | 0.015 ± 0.002 | 0.24 | 0.950 | 0.000 |
| 2 | continual | 0.856 ± 0.004 | 0.020 ± 0.003 | 1.37 | 0.916 | 0.246 |
| 3 (base) | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 3 (base) | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 3 (base) | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 3 (base) | lineage | 0.820 ± 0.009 | 0.015 ± 0.002 | 0.25 | 0.925 | 0.000 |
| 3 (base) | continual | 0.871 ± 0.005 | 0.019 ± 0.003 | 1.94 | 0.843 | 0.263 |
| 5 | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 5 | random | 0.834 ± 0.007 | 0.015 ± 0.003 | 5.00 | 0.206 | 0.144 |
| 5 | uncertainty | 0.837 ± 0.003 | 0.029 ± 0.004 | 1.81 | 0.219 | 0.008 |
| 5 | lineage | 0.822 ± 0.009 | 0.015 ± 0.002 | 0.26 | 0.880 | 0.000 |
| 5 | continual | 0.883 ± 0.006 | 0.018 ± 0.003 | 2.96 | 0.669 | 0.261 |
| 8 | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 8 | random | 0.846 ± 0.008 | 0.014 ± 0.003 | 8.00 | 0.184 | 0.185 |
| 8 | uncertainty | 0.846 ± 0.005 | 0.029 ± 0.004 | 2.66 | 0.194 | 0.011 |
| 8 | lineage | 0.822 ± 0.008 | 0.015 ± 0.002 | 0.28 | 0.820 | 0.000 |
| 8 | continual | 0.885 ± 0.007 | 0.019 ± 0.003 | 4.44 | 0.466 | 0.272 |

## min_independent_roots

| value | strategy | accuracy | false confident consensus | Q / claim | new roots / Q | resolved gaps |
|---|---|---:|---:|---:|---:|---:|
| 2 | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 2 | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 2 | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 2 | lineage | 0.802 ± 0.007 | 0.025 ± 0.003 | 0.02 | 1.000 | 0.000 |
| 2 | continual | 0.868 ± 0.005 | 0.028 ± 0.003 | 1.72 | 0.835 | 0.260 |
| 3 (base) | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 3 (base) | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 3 (base) | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 3 (base) | lineage | 0.820 ± 0.009 | 0.015 ± 0.002 | 0.25 | 0.925 | 0.000 |
| 3 (base) | continual | 0.871 ± 0.005 | 0.019 ± 0.003 | 1.94 | 0.843 | 0.263 |
| 4 | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 4 | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 4 | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 4 | lineage | 0.821 ± 0.009 | 0.007 ± 0.001 | 0.91 | 0.906 | 0.000 |
| 4 | continual | 0.871 ± 0.007 | 0.013 ± 0.002 | 2.32 | 0.846 | 0.266 |
| 5 | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 5 | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 5 | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 5 | lineage | 0.862 ± 0.009 | 0.020 ± 0.004 | 1.85 | 0.869 | 0.320 |
| 5 | continual | 0.870 ± 0.007 | 0.010 ± 0.003 | 2.64 | 0.844 | 0.264 |

## candidate_pool_size

| value | strategy | accuracy | false confident consensus | Q / claim | new roots / Q | resolved gaps |
|---|---|---:|---:|---:|---:|---:|
| 16 | none | 0.801 ± 0.009 | 0.030 ± 0.002 | 0.00 | 0.000 | 0.000 |
| 16 | random | 0.826 ± 0.006 | 0.018 ± 0.003 | 3.00 | 0.229 | 0.101 |
| 16 | uncertainty | 0.827 ± 0.007 | 0.031 ± 0.002 | 1.18 | 0.237 | 0.007 |
| 16 | lineage | 0.820 ± 0.008 | 0.017 ± 0.001 | 0.26 | 0.837 | 0.000 |
| 16 | continual | 0.853 ± 0.008 | 0.021 ± 0.003 | 1.99 | 0.558 | 0.207 |
| 32 | none | 0.809 ± 0.008 | 0.029 ± 0.003 | 0.00 | 0.000 | 0.000 |
| 32 | random | 0.833 ± 0.010 | 0.018 ± 0.002 | 3.00 | 0.228 | 0.100 |
| 32 | uncertainty | 0.829 ± 0.009 | 0.030 ± 0.003 | 1.15 | 0.231 | 0.007 |
| 32 | lineage | 0.827 ± 0.010 | 0.017 ± 0.003 | 0.24 | 0.925 | 0.000 |
| 32 | continual | 0.872 ± 0.006 | 0.021 ± 0.004 | 1.93 | 0.774 | 0.256 |
| 64 (base) | none | 0.802 ± 0.007 | 0.028 ± 0.004 | 0.00 | 0.000 | 0.000 |
| 64 (base) | random | 0.825 ± 0.006 | 0.017 ± 0.003 | 3.00 | 0.230 | 0.101 |
| 64 (base) | uncertainty | 0.824 ± 0.003 | 0.029 ± 0.004 | 1.17 | 0.232 | 0.006 |
| 64 (base) | lineage | 0.820 ± 0.009 | 0.015 ± 0.002 | 0.25 | 0.925 | 0.000 |
| 64 (base) | continual | 0.871 ± 0.005 | 0.019 ± 0.003 | 1.94 | 0.843 | 0.263 |
| 128 | none | 0.808 ± 0.007 | 0.027 ± 0.006 | 0.00 | 0.000 | 0.000 |
| 128 | random | 0.836 ± 0.009 | 0.015 ± 0.003 | 3.00 | 0.229 | 0.099 |
| 128 | uncertainty | 0.832 ± 0.007 | 0.027 ± 0.006 | 1.16 | 0.227 | 0.008 |
| 128 | lineage | 0.827 ± 0.008 | 0.015 ± 0.004 | 0.24 | 0.931 | 0.000 |
| 128 | continual | 0.882 ± 0.008 | 0.017 ± 0.003 | 1.93 | 0.846 | 0.259 |

## Accuracy — `continual` vs each baseline, every cell

| axis | value | vs | Δ accuracy | SEM | seeds favoring | verdict | Q/claim (continual vs baseline) |
|---|---|---|---:|---:|---:|---|---|
| corrupt_root_prob | 0.05 | none | +0.0620 | 0.0027 | 8/8 | win | 1.59 vs 0.00 |
| corrupt_root_prob | 0.05 | random | +0.0331 | 0.0028 | 8/8 | win | 1.59 vs 3.00 |
| corrupt_root_prob | 0.05 | uncertainty | +0.0353 | 0.0016 | 8/8 | win | 1.59 vs 0.86 |
| corrupt_root_prob | 0.1 | none | +0.0639 | 0.0036 | 8/8 | win | 1.76 vs 0.00 |
| corrupt_root_prob | 0.1 | random | +0.0383 | 0.0040 | 8/8 | win | 1.76 vs 3.00 |
| corrupt_root_prob | 0.1 | uncertainty | +0.0405 | 0.0043 | 8/8 | win | 1.76 vs 1.01 |
| corrupt_root_prob | 0.14 | none | +0.0686 | 0.0023 | 8/8 | win | 1.94 vs 0.00 |
| corrupt_root_prob | 0.14 | random | +0.0463 | 0.0025 | 8/8 | win | 1.94 vs 3.00 |
| corrupt_root_prob | 0.14 | uncertainty | +0.0467 | 0.0022 | 8/8 | win | 1.94 vs 1.17 |
| corrupt_root_prob | 0.2 | none | +0.0670 | 0.0026 | 8/8 | win | 2.12 vs 0.00 |
| corrupt_root_prob | 0.2 | random | +0.0437 | 0.0037 | 8/8 | win | 2.12 vs 3.00 |
| corrupt_root_prob | 0.2 | uncertainty | +0.0472 | 0.0029 | 8/8 | win | 2.12 vs 1.34 |
| corrupt_root_prob | 0.3 | none | +0.0463 | 0.0036 | 8/8 | win | 2.32 vs 0.00 |
| corrupt_root_prob | 0.3 | random | +0.0311 | 0.0043 | 8/8 | win | 2.32 vs 3.00 |
| corrupt_root_prob | 0.3 | uncertainty | +0.0315 | 0.0039 | 8/8 | win | 2.32 vs 1.51 |
| root_zipf_exponent | 0.0 | none | +0.0504 | 0.0033 | 8/8 | win | 1.90 vs 0.00 |
| root_zipf_exponent | 0.0 | random | +0.0269 | 0.0040 | 8/8 | win | 1.90 vs 3.00 |
| root_zipf_exponent | 0.0 | uncertainty | +0.0244 | 0.0019 | 8/8 | win | 1.90 vs 1.05 |
| root_zipf_exponent | 0.6 | none | +0.0608 | 0.0031 | 8/8 | win | 1.89 vs 0.00 |
| root_zipf_exponent | 0.6 | random | +0.0360 | 0.0047 | 8/8 | win | 1.89 vs 3.00 |
| root_zipf_exponent | 0.6 | uncertainty | +0.0320 | 0.0030 | 8/8 | win | 1.89 vs 1.06 |
| root_zipf_exponent | 1.0 | none | +0.0617 | 0.0025 | 8/8 | win | 1.91 vs 0.00 |
| root_zipf_exponent | 1.0 | random | +0.0359 | 0.0025 | 8/8 | win | 1.91 vs 3.00 |
| root_zipf_exponent | 1.0 | uncertainty | +0.0381 | 0.0021 | 8/8 | win | 1.91 vs 1.10 |
| root_zipf_exponent | 1.35 | none | +0.0686 | 0.0023 | 8/8 | win | 1.94 vs 0.00 |
| root_zipf_exponent | 1.35 | random | +0.0463 | 0.0025 | 8/8 | win | 1.94 vs 3.00 |
| root_zipf_exponent | 1.35 | uncertainty | +0.0467 | 0.0022 | 8/8 | win | 1.94 vs 1.17 |
| root_zipf_exponent | 1.8 | none | +0.0745 | 0.0050 | 8/8 | win | 1.98 vs 0.00 |
| root_zipf_exponent | 1.8 | random | +0.0508 | 0.0048 | 8/8 | win | 1.98 vs 3.00 |
| root_zipf_exponent | 1.8 | uncertainty | +0.0538 | 0.0042 | 8/8 | win | 1.98 vs 1.20 |
| question_budget_per_claim | 1 | none | +0.0354 | 0.0022 | 8/8 | win | 0.73 vs 0.00 |
| question_budget_per_claim | 1 | random | +0.0257 | 0.0026 | 8/8 | win | 0.73 vs 1.00 |
| question_budget_per_claim | 1 | uncertainty | +0.0260 | 0.0026 | 8/8 | win | 0.73 vs 0.43 |
| question_budget_per_claim | 2 | none | +0.0533 | 0.0027 | 8/8 | win | 1.37 vs 0.00 |
| question_budget_per_claim | 2 | random | +0.0344 | 0.0022 | 8/8 | win | 1.37 vs 2.00 |
| question_budget_per_claim | 2 | uncertainty | +0.0333 | 0.0027 | 8/8 | win | 1.37 vs 0.81 |
| question_budget_per_claim | 3 | none | +0.0686 | 0.0023 | 8/8 | win | 1.94 vs 0.00 |
| question_budget_per_claim | 3 | random | +0.0463 | 0.0025 | 8/8 | win | 1.94 vs 3.00 |
| question_budget_per_claim | 3 | uncertainty | +0.0467 | 0.0022 | 8/8 | win | 1.94 vs 1.17 |
| question_budget_per_claim | 5 | none | +0.0807 | 0.0028 | 8/8 | win | 2.96 vs 0.00 |
| question_budget_per_claim | 5 | random | +0.0489 | 0.0024 | 8/8 | win | 2.96 vs 5.00 |
| question_budget_per_claim | 5 | uncertainty | +0.0460 | 0.0026 | 8/8 | win | 2.96 vs 1.81 |
| question_budget_per_claim | 8 | none | +0.0826 | 0.0032 | 8/8 | win | 4.44 vs 0.00 |
| question_budget_per_claim | 8 | random | +0.0390 | 0.0034 | 8/8 | win | 4.44 vs 8.00 |
| question_budget_per_claim | 8 | uncertainty | +0.0391 | 0.0034 | 8/8 | win | 4.44 vs 2.66 |
| min_independent_roots | 2 | none | +0.0652 | 0.0031 | 8/8 | win | 1.72 vs 0.00 |
| min_independent_roots | 2 | random | +0.0429 | 0.0029 | 8/8 | win | 1.72 vs 3.00 |
| min_independent_roots | 2 | uncertainty | +0.0433 | 0.0025 | 8/8 | win | 1.72 vs 1.17 |
| min_independent_roots | 3 | none | +0.0686 | 0.0023 | 8/8 | win | 1.94 vs 0.00 |
| min_independent_roots | 3 | random | +0.0463 | 0.0025 | 8/8 | win | 1.94 vs 3.00 |
| min_independent_roots | 3 | uncertainty | +0.0467 | 0.0022 | 8/8 | win | 1.94 vs 1.17 |
| min_independent_roots | 4 | none | +0.0682 | 0.0025 | 8/8 | win | 2.32 vs 0.00 |
| min_independent_roots | 4 | random | +0.0459 | 0.0025 | 8/8 | win | 2.32 vs 3.00 |
| min_independent_roots | 4 | uncertainty | +0.0463 | 0.0023 | 8/8 | win | 2.32 vs 1.17 |
| min_independent_roots | 5 | none | +0.0679 | 0.0030 | 8/8 | win | 2.64 vs 0.00 |
| min_independent_roots | 5 | random | +0.0457 | 0.0031 | 8/8 | win | 2.64 vs 3.00 |
| min_independent_roots | 5 | uncertainty | +0.0461 | 0.0030 | 8/8 | win | 2.64 vs 1.17 |
| candidate_pool_size | 16 | none | +0.0522 | 0.0026 | 8/8 | win | 1.99 vs 0.00 |
| candidate_pool_size | 16 | random | +0.0265 | 0.0021 | 8/8 | win | 1.99 vs 3.00 |
| candidate_pool_size | 16 | uncertainty | +0.0254 | 0.0027 | 8/8 | win | 1.99 vs 1.18 |
| candidate_pool_size | 32 | none | +0.0625 | 0.0027 | 8/8 | win | 1.93 vs 0.00 |
| candidate_pool_size | 32 | random | +0.0386 | 0.0028 | 8/8 | win | 1.93 vs 3.00 |
| candidate_pool_size | 32 | uncertainty | +0.0423 | 0.0027 | 8/8 | win | 1.93 vs 1.15 |
| candidate_pool_size | 64 | none | +0.0686 | 0.0023 | 8/8 | win | 1.94 vs 0.00 |
| candidate_pool_size | 64 | random | +0.0463 | 0.0025 | 8/8 | win | 1.94 vs 3.00 |
| candidate_pool_size | 64 | uncertainty | +0.0467 | 0.0022 | 8/8 | win | 1.94 vs 1.17 |
| candidate_pool_size | 128 | none | +0.0740 | 0.0012 | 8/8 | win | 1.93 vs 0.00 |
| candidate_pool_size | 128 | random | +0.0460 | 0.0023 | 8/8 | win | 1.93 vs 3.00 |
| candidate_pool_size | 128 | uncertainty | +0.0493 | 0.0019 | 8/8 | win | 1.93 vs 1.16 |

## Regimes where a lineage-aware policy loses to a baseline

| axis | value | focus | vs | metric | Δ (oriented) | SEM | seeds favoring focus | Q/claim (focus vs baseline) |
|---|---|---|---|---|---:|---:|---:|---|
| corrupt_root_prob | 0.05 | lineage | random | accuracy | -0.0084 | 0.0024 | 1/8 | 0.25 vs 3.00 |
| corrupt_root_prob | 0.05 | lineage | random | resolved_gap_rate | -0.1397 | 0.0040 | 0/8 | 0.25 vs 3.00 |
| corrupt_root_prob | 0.05 | lineage | uncertainty | accuracy | -0.0062 | 0.0009 | 0/8 | 0.25 vs 0.86 |
| corrupt_root_prob | 0.05 | lineage | uncertainty | resolved_gap_rate | -0.0122 | 0.0013 | 0/8 | 0.25 vs 0.86 |
| corrupt_root_prob | 0.1 | lineage | random | resolved_gap_rate | -0.1129 | 0.0035 | 0/8 | 0.24 vs 3.00 |
| corrupt_root_prob | 0.1 | lineage | uncertainty | resolved_gap_rate | -0.0090 | 0.0011 | 0/8 | 0.24 vs 1.01 |
| corrupt_root_prob | 0.14 | lineage | random | resolved_gap_rate | -0.1009 | 0.0032 | 0/8 | 0.25 vs 3.00 |
| corrupt_root_prob | 0.14 | lineage | uncertainty | resolved_gap_rate | -0.0062 | 0.0004 | 0/8 | 0.25 vs 1.17 |
| corrupt_root_prob | 0.14 | continual | random | false_confident_consensus_rate | -0.0021 | 0.0010 | 1/8 | 1.94 vs 3.00 |
| corrupt_root_prob | 0.2 | lineage | random | accuracy | -0.0055 | 0.0018 | 1/8 | 0.24 vs 3.00 |
| corrupt_root_prob | 0.2 | lineage | random | resolved_gap_rate | -0.0746 | 0.0026 | 0/8 | 0.24 vs 3.00 |
| corrupt_root_prob | 0.2 | lineage | uncertainty | resolved_gap_rate | -0.0051 | 0.0007 | 0/8 | 0.24 vs 1.34 |
| corrupt_root_prob | 0.2 | continual | random | false_confident_consensus_rate | -0.0066 | 0.0018 | 1/8 | 2.12 vs 3.00 |
| corrupt_root_prob | 0.3 | lineage | random | accuracy | -0.0061 | 0.0019 | 1/8 | 0.25 vs 3.00 |
| corrupt_root_prob | 0.3 | lineage | random | resolved_gap_rate | -0.0461 | 0.0023 | 0/8 | 0.25 vs 3.00 |
| corrupt_root_prob | 0.3 | lineage | uncertainty | accuracy | -0.0057 | 0.0017 | 0/8 | 0.25 vs 1.51 |
| corrupt_root_prob | 0.3 | lineage | uncertainty | resolved_gap_rate | -0.0048 | 0.0006 | 0/8 | 0.25 vs 1.51 |
| corrupt_root_prob | 0.3 | continual | random | false_confident_consensus_rate | -0.0177 | 0.0011 | 0/8 | 2.32 vs 3.00 |
| root_zipf_exponent | 0.0 | lineage | random | accuracy | -0.0212 | 0.0023 | 0/8 | 0.05 vs 3.00 |
| root_zipf_exponent | 0.0 | lineage | random | resolved_gap_rate | -0.1675 | 0.0042 | 0/8 | 0.05 vs 3.00 |
| root_zipf_exponent | 0.0 | lineage | uncertainty | accuracy | -0.0238 | 0.0021 | 0/8 | 0.05 vs 1.05 |
| root_zipf_exponent | 0.0 | lineage | uncertainty | resolved_gap_rate | -0.0089 | 0.0007 | 0/8 | 0.05 vs 1.05 |
| root_zipf_exponent | 0.6 | lineage | random | accuracy | -0.0201 | 0.0018 | 0/8 | 0.07 vs 3.00 |
| root_zipf_exponent | 0.6 | lineage | random | resolved_gap_rate | -0.1629 | 0.0037 | 0/8 | 0.07 vs 3.00 |
| root_zipf_exponent | 0.6 | lineage | uncertainty | accuracy | -0.0241 | 0.0022 | 0/8 | 0.07 vs 1.06 |
| root_zipf_exponent | 0.6 | lineage | uncertainty | resolved_gap_rate | -0.0090 | 0.0007 | 0/8 | 0.07 vs 1.06 |
| root_zipf_exponent | 0.6 | continual | random | false_confident_consensus_rate | -0.0036 | 0.0013 | 1/8 | 1.89 vs 3.00 |
| root_zipf_exponent | 1.0 | lineage | random | accuracy | -0.0165 | 0.0019 | 0/8 | 0.13 vs 3.00 |
| root_zipf_exponent | 1.0 | lineage | random | resolved_gap_rate | -0.1301 | 0.0043 | 0/8 | 0.13 vs 3.00 |
| root_zipf_exponent | 1.0 | lineage | uncertainty | accuracy | -0.0143 | 0.0013 | 0/8 | 0.13 vs 1.10 |
| root_zipf_exponent | 1.0 | lineage | uncertainty | resolved_gap_rate | -0.0072 | 0.0008 | 0/8 | 0.13 vs 1.10 |
| root_zipf_exponent | 1.0 | continual | random | false_confident_consensus_rate | -0.0033 | 0.0008 | 0/8 | 1.91 vs 3.00 |
| root_zipf_exponent | 1.35 | lineage | random | resolved_gap_rate | -0.1009 | 0.0032 | 0/8 | 0.25 vs 3.00 |
| root_zipf_exponent | 1.35 | lineage | uncertainty | resolved_gap_rate | -0.0062 | 0.0004 | 0/8 | 0.25 vs 1.17 |
| root_zipf_exponent | 1.35 | continual | random | false_confident_consensus_rate | -0.0021 | 0.0010 | 1/8 | 1.94 vs 3.00 |
| root_zipf_exponent | 1.8 | lineage | random | resolved_gap_rate | -0.0443 | 0.0021 | 0/8 | 0.48 vs 3.00 |
| root_zipf_exponent | 1.8 | lineage | uncertainty | resolved_gap_rate | -0.0049 | 0.0010 | 0/8 | 0.48 vs 1.20 |
| question_budget_per_claim | 1 | lineage | random | resolved_gap_rate | -0.0372 | 0.0012 | 0/8 | 0.22 vs 1.00 |
| question_budget_per_claim | 1 | lineage | uncertainty | resolved_gap_rate | -0.0024 | 0.0005 | 0/8 | 0.22 vs 0.43 |
| question_budget_per_claim | 2 | lineage | random | resolved_gap_rate | -0.0707 | 0.0028 | 0/8 | 0.24 vs 2.00 |
| question_budget_per_claim | 2 | lineage | uncertainty | resolved_gap_rate | -0.0045 | 0.0011 | 0/8 | 0.24 vs 0.81 |
| question_budget_per_claim | 3 | lineage | random | resolved_gap_rate | -0.1009 | 0.0032 | 0/8 | 0.25 vs 3.00 |
| question_budget_per_claim | 3 | lineage | uncertainty | resolved_gap_rate | -0.0062 | 0.0004 | 0/8 | 0.25 vs 1.17 |
| question_budget_per_claim | 3 | continual | random | false_confident_consensus_rate | -0.0021 | 0.0010 | 1/8 | 1.94 vs 3.00 |
| question_budget_per_claim | 5 | lineage | random | accuracy | -0.0120 | 0.0030 | 1/8 | 0.26 vs 5.00 |
| question_budget_per_claim | 5 | lineage | random | resolved_gap_rate | -0.1434 | 0.0037 | 0/8 | 0.26 vs 5.00 |
| question_budget_per_claim | 5 | lineage | uncertainty | accuracy | -0.0149 | 0.0031 | 1/8 | 0.26 vs 1.81 |
| question_budget_per_claim | 5 | lineage | uncertainty | resolved_gap_rate | -0.0074 | 0.0008 | 0/8 | 0.26 vs 1.81 |
| question_budget_per_claim | 5 | continual | random | false_confident_consensus_rate | -0.0027 | 0.0007 | 0/8 | 2.96 vs 5.00 |
| question_budget_per_claim | 8 | lineage | random | accuracy | -0.0239 | 0.0022 | 0/8 | 0.28 vs 8.00 |
| question_budget_per_claim | 8 | lineage | random | resolved_gap_rate | -0.1851 | 0.0024 | 0/8 | 0.28 vs 8.00 |
| question_budget_per_claim | 8 | lineage | uncertainty | accuracy | -0.0238 | 0.0030 | 0/8 | 0.28 vs 2.66 |
| question_budget_per_claim | 8 | lineage | uncertainty | resolved_gap_rate | -0.0107 | 0.0008 | 0/8 | 0.28 vs 2.66 |
| question_budget_per_claim | 8 | continual | random | false_confident_consensus_rate | -0.0052 | 0.0010 | 0/8 | 4.44 vs 8.00 |
| min_independent_roots | 2 | lineage | random | accuracy | -0.0223 | 0.0026 | 0/8 | 0.02 vs 3.00 |
| min_independent_roots | 2 | lineage | random | false_confident_consensus_rate | -0.0081 | 0.0010 | 0/8 | 0.02 vs 3.00 |
| min_independent_roots | 2 | lineage | random | resolved_gap_rate | -0.1010 | 0.0033 | 0/8 | 0.02 vs 3.00 |
| min_independent_roots | 2 | lineage | uncertainty | accuracy | -0.0219 | 0.0018 | 0/8 | 0.02 vs 1.17 |
| min_independent_roots | 2 | lineage | uncertainty | resolved_gap_rate | -0.0063 | 0.0004 | 0/8 | 0.02 vs 1.17 |
| min_independent_roots | 2 | continual | random | false_confident_consensus_rate | -0.0114 | 0.0005 | 0/8 | 1.72 vs 3.00 |
| min_independent_roots | 3 | lineage | random | resolved_gap_rate | -0.1009 | 0.0032 | 0/8 | 0.25 vs 3.00 |
| min_independent_roots | 3 | lineage | uncertainty | resolved_gap_rate | -0.0062 | 0.0004 | 0/8 | 0.25 vs 1.17 |
| min_independent_roots | 3 | continual | random | false_confident_consensus_rate | -0.0021 | 0.0010 | 1/8 | 1.94 vs 3.00 |
| min_independent_roots | 4 | lineage | random | resolved_gap_rate | -0.1006 | 0.0033 | 0/8 | 0.91 vs 3.00 |
| min_independent_roots | 4 | lineage | uncertainty | resolved_gap_rate | -0.0059 | 0.0004 | 0/8 | 0.91 vs 1.17 |
| min_independent_roots | 5 | lineage | random | false_confident_consensus_rate | -0.0031 | 0.0005 | 0/8 | 1.85 vs 3.00 |
| candidate_pool_size | 16 | lineage | random | accuracy | -0.0068 | 0.0018 | 1/8 | 0.26 vs 3.00 |
| candidate_pool_size | 16 | lineage | random | resolved_gap_rate | -0.1014 | 0.0038 | 0/8 | 0.26 vs 3.00 |
| candidate_pool_size | 16 | lineage | uncertainty | accuracy | -0.0079 | 0.0020 | 1/8 | 0.26 vs 1.18 |
| candidate_pool_size | 16 | lineage | uncertainty | resolved_gap_rate | -0.0074 | 0.0013 | 0/8 | 0.26 vs 1.18 |
| candidate_pool_size | 16 | continual | random | false_confident_consensus_rate | -0.0027 | 0.0011 | 2/8 | 1.99 vs 3.00 |
| candidate_pool_size | 32 | lineage | random | accuracy | -0.0059 | 0.0018 | 1/8 | 0.24 vs 3.00 |
| candidate_pool_size | 32 | lineage | random | resolved_gap_rate | -0.1003 | 0.0023 | 0/8 | 0.24 vs 3.00 |
| candidate_pool_size | 32 | lineage | uncertainty | resolved_gap_rate | -0.0066 | 0.0010 | 0/8 | 0.24 vs 1.15 |
| candidate_pool_size | 32 | continual | random | false_confident_consensus_rate | -0.0025 | 0.0010 | 1/8 | 1.93 vs 3.00 |
| candidate_pool_size | 64 | lineage | random | resolved_gap_rate | -0.1009 | 0.0032 | 0/8 | 0.25 vs 3.00 |
| candidate_pool_size | 64 | lineage | uncertainty | resolved_gap_rate | -0.0062 | 0.0004 | 0/8 | 0.25 vs 1.17 |
| candidate_pool_size | 64 | continual | random | false_confident_consensus_rate | -0.0021 | 0.0010 | 1/8 | 1.94 vs 3.00 |
| candidate_pool_size | 128 | lineage | random | accuracy | -0.0087 | 0.0025 | 1/8 | 0.24 vs 3.00 |
| candidate_pool_size | 128 | lineage | random | resolved_gap_rate | -0.0989 | 0.0048 | 0/8 | 0.24 vs 3.00 |
| candidate_pool_size | 128 | lineage | uncertainty | accuracy | -0.0053 | 0.0026 | 2/8 | 0.24 vs 1.16 |
| candidate_pool_size | 128 | lineage | uncertainty | resolved_gap_rate | -0.0083 | 0.0008 | 0/8 | 0.24 vs 1.16 |
| candidate_pool_size | 128 | continual | random | false_confident_consensus_rate | -0.0028 | 0.0006 | 0/8 | 1.93 vs 3.00 |

## Trade-offs between the two lineage-aware policies

These are not baseline comparisons. `lineage` asks far fewer questions, so it wins on roots acquired per question and on false confident consensus while never resolving gaps; `continual` spends more and wins on accuracy and resolved gaps. Both directions are listed.

| axis | value | focus | vs | metric | Δ (oriented) | SEM | Q/claim (focus vs peer) |
|---|---|---|---|---|---:|---:|---|
| corrupt_root_prob | 0.05 | lineage | continual | accuracy | -0.0415 | 0.0017 | 0.25 vs 1.59 |
| corrupt_root_prob | 0.05 | lineage | continual | resolved_gap_rate | -0.3676 | 0.0050 | 0.25 vs 1.59 |
| corrupt_root_prob | 0.05 | continual | lineage | false_confident_consensus_rate | -0.0015 | 0.0006 | 1.59 vs 0.25 |
| corrupt_root_prob | 0.05 | continual | lineage | independent_roots_per_question | -0.0756 | 0.0089 | 1.59 vs 0.25 |
| corrupt_root_prob | 0.1 | lineage | continual | accuracy | -0.0423 | 0.0033 | 0.24 vs 1.76 |
| corrupt_root_prob | 0.1 | lineage | continual | resolved_gap_rate | -0.3075 | 0.0042 | 0.24 vs 1.76 |
| corrupt_root_prob | 0.1 | continual | lineage | false_confident_consensus_rate | -0.0025 | 0.0008 | 1.76 vs 0.24 |
| corrupt_root_prob | 0.1 | continual | lineage | independent_roots_per_question | -0.0926 | 0.0071 | 1.76 vs 0.24 |
| corrupt_root_prob | 0.14 | lineage | continual | accuracy | -0.0507 | 0.0032 | 0.25 vs 1.94 |
| corrupt_root_prob | 0.14 | lineage | continual | resolved_gap_rate | -0.2626 | 0.0057 | 0.25 vs 1.94 |
| corrupt_root_prob | 0.14 | continual | lineage | false_confident_consensus_rate | -0.0038 | 0.0008 | 1.94 vs 0.25 |
| corrupt_root_prob | 0.14 | continual | lineage | independent_roots_per_question | -0.0827 | 0.0082 | 1.94 vs 0.25 |
| corrupt_root_prob | 0.2 | lineage | continual | accuracy | -0.0492 | 0.0035 | 0.24 vs 2.12 |
| corrupt_root_prob | 0.2 | lineage | continual | resolved_gap_rate | -0.1884 | 0.0040 | 0.24 vs 2.12 |
| corrupt_root_prob | 0.2 | continual | lineage | false_confident_consensus_rate | -0.0091 | 0.0009 | 2.12 vs 0.24 |
| corrupt_root_prob | 0.2 | continual | lineage | independent_roots_per_question | -0.0958 | 0.0086 | 2.12 vs 0.24 |
| corrupt_root_prob | 0.3 | lineage | continual | accuracy | -0.0372 | 0.0030 | 0.25 vs 2.32 |
| corrupt_root_prob | 0.3 | lineage | continual | resolved_gap_rate | -0.1148 | 0.0026 | 0.25 vs 2.32 |
| corrupt_root_prob | 0.3 | continual | lineage | false_confident_consensus_rate | -0.0214 | 0.0008 | 2.32 vs 0.25 |
| corrupt_root_prob | 0.3 | continual | lineage | independent_roots_per_question | -0.0855 | 0.0092 | 2.32 vs 0.25 |
| root_zipf_exponent | 0.0 | lineage | continual | accuracy | -0.0482 | 0.0033 | 0.05 vs 1.90 |
| root_zipf_exponent | 0.0 | lineage | continual | independent_roots_per_question | -0.1464 | 0.0283 | 0.05 vs 1.90 |
| root_zipf_exponent | 0.0 | lineage | continual | resolved_gap_rate | -0.2070 | 0.0037 | 0.05 vs 1.90 |
| root_zipf_exponent | 0.6 | lineage | continual | accuracy | -0.0561 | 0.0033 | 0.07 vs 1.89 |
| root_zipf_exponent | 0.6 | lineage | continual | independent_roots_per_question | -0.0497 | 0.0169 | 0.07 vs 1.89 |
| root_zipf_exponent | 0.6 | lineage | continual | resolved_gap_rate | -0.2233 | 0.0069 | 0.07 vs 1.89 |
| root_zipf_exponent | 0.6 | continual | lineage | false_confident_consensus_rate | -0.0025 | 0.0009 | 1.89 vs 0.07 |
| root_zipf_exponent | 1.0 | lineage | continual | accuracy | -0.0524 | 0.0024 | 0.13 vs 1.91 |
| root_zipf_exponent | 1.0 | lineage | continual | resolved_gap_rate | -0.2455 | 0.0046 | 0.13 vs 1.91 |
| root_zipf_exponent | 1.0 | continual | lineage | false_confident_consensus_rate | -0.0032 | 0.0013 | 1.91 vs 0.13 |
| root_zipf_exponent | 1.0 | continual | lineage | independent_roots_per_question | -0.0461 | 0.0127 | 1.91 vs 0.13 |
| root_zipf_exponent | 1.35 | lineage | continual | accuracy | -0.0507 | 0.0032 | 0.25 vs 1.94 |
| root_zipf_exponent | 1.35 | lineage | continual | resolved_gap_rate | -0.2626 | 0.0057 | 0.25 vs 1.94 |
| root_zipf_exponent | 1.35 | continual | lineage | false_confident_consensus_rate | -0.0038 | 0.0008 | 1.94 vs 0.25 |
| root_zipf_exponent | 1.35 | continual | lineage | independent_roots_per_question | -0.0827 | 0.0082 | 1.94 vs 0.25 |
| root_zipf_exponent | 1.8 | lineage | continual | accuracy | -0.0386 | 0.0039 | 0.48 vs 1.98 |
| root_zipf_exponent | 1.8 | lineage | continual | resolved_gap_rate | -0.2563 | 0.0057 | 0.48 vs 1.98 |
| root_zipf_exponent | 1.8 | continual | lineage | false_confident_consensus_rate | -0.0062 | 0.0012 | 1.98 vs 0.48 |
| root_zipf_exponent | 1.8 | continual | lineage | independent_roots_per_question | -0.0992 | 0.0053 | 1.98 vs 0.48 |
| question_budget_per_claim | 1 | lineage | continual | accuracy | -0.0186 | 0.0029 | 0.22 vs 0.73 |
| question_budget_per_claim | 1 | lineage | continual | resolved_gap_rate | -0.1455 | 0.0029 | 0.22 vs 0.73 |
| question_budget_per_claim | 1 | continual | lineage | false_confident_consensus_rate | -0.0033 | 0.0010 | 0.73 vs 0.22 |
| question_budget_per_claim | 1 | continual | lineage | independent_roots_per_question | -0.0077 | 0.0031 | 0.73 vs 0.22 |
| question_budget_per_claim | 2 | lineage | continual | accuracy | -0.0352 | 0.0035 | 0.24 vs 1.37 |
| question_budget_per_claim | 2 | lineage | continual | resolved_gap_rate | -0.2459 | 0.0050 | 0.24 vs 1.37 |
| question_budget_per_claim | 2 | continual | lineage | false_confident_consensus_rate | -0.0051 | 0.0011 | 1.37 vs 0.24 |
| question_budget_per_claim | 2 | continual | lineage | independent_roots_per_question | -0.0336 | 0.0058 | 1.37 vs 0.24 |
| question_budget_per_claim | 3 | lineage | continual | accuracy | -0.0507 | 0.0032 | 0.25 vs 1.94 |
| question_budget_per_claim | 3 | lineage | continual | resolved_gap_rate | -0.2626 | 0.0057 | 0.25 vs 1.94 |
| question_budget_per_claim | 3 | continual | lineage | false_confident_consensus_rate | -0.0038 | 0.0008 | 1.94 vs 0.25 |
| question_budget_per_claim | 3 | continual | lineage | independent_roots_per_question | -0.0827 | 0.0082 | 1.94 vs 0.25 |
| question_budget_per_claim | 5 | lineage | continual | accuracy | -0.0609 | 0.0032 | 0.26 vs 2.96 |
| question_budget_per_claim | 5 | lineage | continual | resolved_gap_rate | -0.2606 | 0.0054 | 0.26 vs 2.96 |
| question_budget_per_claim | 5 | continual | lineage | false_confident_consensus_rate | -0.0029 | 0.0004 | 2.96 vs 0.26 |
| question_budget_per_claim | 5 | continual | lineage | independent_roots_per_question | -0.2111 | 0.0118 | 2.96 vs 0.26 |
| question_budget_per_claim | 8 | lineage | continual | accuracy | -0.0629 | 0.0036 | 0.28 vs 4.44 |
| question_budget_per_claim | 8 | lineage | continual | resolved_gap_rate | -0.2717 | 0.0055 | 0.28 vs 4.44 |
| question_budget_per_claim | 8 | continual | lineage | false_confident_consensus_rate | -0.0046 | 0.0006 | 4.44 vs 0.28 |
| question_budget_per_claim | 8 | continual | lineage | independent_roots_per_question | -0.3539 | 0.0149 | 4.44 vs 0.28 |
| min_independent_roots | 2 | lineage | continual | accuracy | -0.0653 | 0.0031 | 0.02 vs 1.72 |
| min_independent_roots | 2 | lineage | continual | resolved_gap_rate | -0.2602 | 0.0060 | 0.02 vs 1.72 |
| min_independent_roots | 2 | continual | lineage | false_confident_consensus_rate | -0.0033 | 0.0007 | 1.72 vs 0.02 |
| min_independent_roots | 2 | continual | lineage | independent_roots_per_question | -0.1648 | 0.0034 | 1.72 vs 0.02 |
| min_independent_roots | 3 | lineage | continual | accuracy | -0.0507 | 0.0032 | 0.25 vs 1.94 |
| min_independent_roots | 3 | lineage | continual | resolved_gap_rate | -0.2626 | 0.0057 | 0.25 vs 1.94 |
| min_independent_roots | 3 | continual | lineage | false_confident_consensus_rate | -0.0038 | 0.0008 | 1.94 vs 0.25 |
| min_independent_roots | 3 | continual | lineage | independent_roots_per_question | -0.0827 | 0.0082 | 1.94 vs 0.25 |
| min_independent_roots | 4 | lineage | continual | accuracy | -0.0500 | 0.0024 | 0.91 vs 2.32 |
| min_independent_roots | 4 | lineage | continual | resolved_gap_rate | -0.2651 | 0.0048 | 0.91 vs 2.32 |
| min_independent_roots | 4 | continual | lineage | false_confident_consensus_rate | -0.0061 | 0.0008 | 2.32 vs 0.91 |
| min_independent_roots | 4 | continual | lineage | independent_roots_per_question | -0.0596 | 0.0025 | 2.32 vs 0.91 |
| min_independent_roots | 5 | lineage | continual | accuracy | -0.0086 | 0.0039 | 1.85 vs 2.64 |
| min_independent_roots | 5 | lineage | continual | false_confident_consensus_rate | -0.0101 | 0.0011 | 1.85 vs 2.64 |
| min_independent_roots | 5 | continual | lineage | independent_roots_per_question | -0.0251 | 0.0021 | 2.64 vs 1.85 |
| min_independent_roots | 5 | continual | lineage | resolved_gap_rate | -0.0551 | 0.0057 | 2.64 vs 1.85 |
| candidate_pool_size | 16 | lineage | continual | accuracy | -0.0333 | 0.0018 | 0.26 vs 1.99 |
| candidate_pool_size | 16 | lineage | continual | resolved_gap_rate | -0.2071 | 0.0050 | 0.26 vs 1.99 |
| candidate_pool_size | 16 | continual | lineage | false_confident_consensus_rate | -0.0042 | 0.0010 | 1.99 vs 0.26 |
| candidate_pool_size | 16 | continual | lineage | independent_roots_per_question | -0.2793 | 0.0095 | 1.99 vs 0.26 |
| candidate_pool_size | 32 | lineage | continual | accuracy | -0.0445 | 0.0034 | 0.24 vs 1.93 |
| candidate_pool_size | 32 | lineage | continual | resolved_gap_rate | -0.2559 | 0.0045 | 0.24 vs 1.93 |
| candidate_pool_size | 32 | continual | lineage | false_confident_consensus_rate | -0.0042 | 0.0006 | 1.93 vs 0.24 |
| candidate_pool_size | 32 | continual | lineage | independent_roots_per_question | -0.1508 | 0.0061 | 1.93 vs 0.24 |
| candidate_pool_size | 64 | lineage | continual | accuracy | -0.0507 | 0.0032 | 0.25 vs 1.94 |
| candidate_pool_size | 64 | lineage | continual | resolved_gap_rate | -0.2626 | 0.0057 | 0.25 vs 1.94 |
| candidate_pool_size | 64 | continual | lineage | false_confident_consensus_rate | -0.0038 | 0.0008 | 1.94 vs 0.25 |
| candidate_pool_size | 64 | continual | lineage | independent_roots_per_question | -0.0827 | 0.0082 | 1.94 vs 0.25 |
| candidate_pool_size | 128 | lineage | continual | accuracy | -0.0547 | 0.0020 | 0.24 vs 1.93 |
| candidate_pool_size | 128 | lineage | continual | resolved_gap_rate | -0.2591 | 0.0041 | 0.24 vs 1.93 |
| candidate_pool_size | 128 | continual | lineage | false_confident_consensus_rate | -0.0030 | 0.0005 | 1.93 vs 0.24 |
| candidate_pool_size | 128 | continual | lineage | independent_roots_per_question | -0.0850 | 0.0076 | 1.93 vs 0.24 |

## Sweep totals

- cells: 23 (5 axes, one factor varied at a time)
- comparisons: 736 (2 focus policies x 4 baselines-or-peers x 4 metrics x 23 cells)
- vs baselines: 405 wins / 64 ties / 83 losses
- vs the other lineage-aware policy: 91 wins / 2 ties / 91 losses

Do not remove the synthetic-evidence label when copying these tables.
