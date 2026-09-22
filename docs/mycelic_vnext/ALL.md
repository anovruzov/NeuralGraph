# Mycelic vNext — research report

_Generated 2026-09-22 12:55 UTC from `research/mycelic/artifacts/` (new) and `artifacts/v1/` (old) by `vnext_docs.py`. Every table is computed from those files._

## 1. Result

The brief asked for the discovery accuracy of the Mycelic hierarchy to be raised as fast and as compute-efficiently as possible without touching the metrics, the gold labels, the evaluation worlds, the register cap or the calibration/evaluation seed split, and then for the whole benchmark to be rerun on the frozen configuration. This is the paired result on identical worlds (OLD = the archived v1 rows, NEW = the rerun).

### 1.1 OLD vs NEW at 10,000 people — development panel (seeds 0–4)

These five worlds were read after every accepted and rejected change during the work (about eleven decisions); their numbers are development-panel numbers and carry a selection optimism of roughly +0.02 to +0.04 found on the small deltas (section 6).

| system | metric | OLD | NEW | Δ | 95% CI | better on | verdict |
|---|---|---:|---:|---:|---|---:|---|
| Mycelic hierarchy | found | 0.375 | 0.625 | +0.250 | [+0.205, +0.295] | 5/5 | **better** |
| Mycelic hierarchy | evidence cov. | 0.690 | 0.980 | +0.290 | [+0.240, +0.335] | 5/5 | **better** |
| Mycelic hierarchy | rare recall | 0.215 | 0.375 | +0.160 | [+0.093, +0.220] | 5/5 | **better** |
| Mycelic hierarchy | AP | 0.023 | 0.185 | +0.162 | [+0.106, +0.215] | 5/5 | **better** |
| Mycelic hierarchy | FDR | 0.975 | 0.958 | -0.016 | [-0.019, -0.013] | 5/5 | **better** |
| Mycelic hierarchy | decoy acc. (all) | 0.180 | 0.360 | +0.180 | [+0.165, +0.195] | 0/5 | **worse** |
| Mycelic hierarchy | decoy D5 stale | 0.260 | 0.780 | +0.520 | [+0.500, +0.560] | 0/5 | **worse** |
| Mycelic hierarchy | decoy D2 scramble | 0.260 | 0.280 | +0.020 | [-0.200, +0.160] | 1/5 | inside noise |
| Mycelic hierarchy | compute | 1.09e+06 | 1.37e+06 | +26% | [+260160.412, +299765.284] | 0/5 | **worse** |
| Mycelic hierarchy | calls (metering) | 9.10e+04 | 2.67e+04 | -71% | [-70762.000, -57242.200] | 5/5 | **better** |
| Mycelic hierarchy | kernel prompt tokens (max) | 7.88e+05 | 1.40e+06 | +77% | [+572946.400, +644956.000] | 0/5 | **worse** |
| A2 chunked long context | found | 0.685 | 0.740 | +0.055 | [+0.005, +0.110] | 3/3 | **better** |
| A2 chunked long context | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | rare recall | 0.653 | 0.669 | +0.016 | [-0.100, +0.133] | 2/4 | inside noise |
| A2 chunked long context | AP | 0.067 | 0.202 | +0.135 | [+0.083, +0.208] | 5/5 | **better** |
| A2 chunked long context | FDR | 0.954 | 0.951 | -0.003 | [-0.007, +0.000] | 3/5 | inside noise |
| A2 chunked long context | decoy acc. (all) | 0.345 | 0.460 | +0.115 | [+0.080, +0.160] | 0/5 | **worse** |
| A2 chunked long context | decoy D5 stale | 0.520 | 0.700 | +0.180 | [+0.040, +0.320] | 0/3 | **worse** |
| A2 chunked long context | decoy D2 scramble | 0.400 | 0.540 | +0.140 | [+0.060, +0.240] | 0/4 | **worse** |
| A2 chunked long context | compute | 2.08e+06 | 2.08e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | calls (metering) | 3.00e+00 | 3.00e+00 | +0% | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | kernel prompt tokens (max) | 1.00e+06 | 1.00e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | found | 0.370 | 0.540 | +0.170 | [+0.095, +0.255] | 5/5 | **better** |
| B4 central triage | evidence cov. | 0.970 | 0.970 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | rare recall | 0.258 | 0.283 | +0.025 | [-0.056, +0.120] | 2/4 | inside noise |
| B4 central triage | AP | 0.022 | 0.130 | +0.108 | [+0.075, +0.143] | 5/5 | **better** |
| B4 central triage | FDR | 0.975 | 0.964 | -0.011 | [-0.017, -0.006] | 5/5 | **better** |
| B4 central triage | decoy acc. (all) | 0.175 | 0.350 | +0.175 | [+0.155, +0.195] | 0/5 | **worse** |
| B4 central triage | decoy D5 stale | 0.160 | 0.680 | +0.520 | [+0.440, +0.620] | 0/5 | **worse** |
| B4 central triage | decoy D2 scramble | 0.200 | 0.380 | +0.180 | [+0.080, +0.280] | 0/4 | **worse** |
| B4 central triage | compute | 3.98e+05 | 3.98e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | calls (metering) | 1.00e+04 | 1.00e+04 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | kernel prompt tokens (max) | 5.20e+05 | 5.20e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | found | 0.240 | 0.415 | +0.175 | [+0.135, +0.215] | 5/5 | **better** |
| Y oracle retrieval | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | rare recall | 0.143 | 0.120 | -0.023 | [-0.109, +0.040] | 1/2 | inside noise |
| Y oracle retrieval | AP | 0.006 | 0.023 | +0.017 | [+0.010, +0.024] | 5/5 | **better** |
| Y oracle retrieval | FDR | 0.984 | 0.972 | -0.012 | [-0.014, -0.009] | 5/5 | **better** |
| Y oracle retrieval | decoy acc. (all) | 0.125 | 0.275 | +0.150 | [+0.070, +0.215] | 0/4 | **worse** |
| Y oracle retrieval | decoy D5 stale | 0.080 | 0.520 | +0.440 | [+0.320, +0.560] | 0/5 | **worse** |
| Y oracle retrieval | decoy D2 scramble | 0.140 | 0.220 | +0.080 | [-0.020, +0.160] | 1/5 | inside noise |
| Y oracle retrieval | compute | 4.87e+05 | 4.87e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | calls (metering) | 1.00e+04 | 1.00e+04 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | kernel prompt tokens (max) | 7.60e+05 | 7.60e+05 | +0% | [+0.000, +0.000] | 0/0 | same |

paired seeds at 10,000: 5 (old rows 5, new rows 5); register entries kept: OLD [600], NEW [600] (the cap is min(6000, max(600, n_entities)) in both).

### 1.2 OLD vs NEW at 10,000 people — confirmation panel (seeds 5–9, never read during development)

No paired experiment, dump, funnel or decision touched these five worlds before the final rerun; this is the number to quote.

| system | metric | OLD | NEW | Δ | 95% CI | better on | verdict |
|---|---|---:|---:|---:|---|---:|---|
| Mycelic hierarchy | found | 0.395 | 0.570 | +0.175 | [+0.125, +0.210] | 5/5 | **better** |
| Mycelic hierarchy | evidence cov. | 0.665 | 0.930 | +0.265 | [+0.185, +0.340] | 5/5 | **better** |
| Mycelic hierarchy | rare recall | 0.220 | 0.348 | +0.128 | [+0.068, +0.199] | 5/5 | **better** |
| Mycelic hierarchy | AP | 0.036 | 0.176 | +0.140 | [+0.097, +0.173] | 5/5 | **better** |
| Mycelic hierarchy | FDR | 0.973 | 0.962 | -0.012 | [-0.014, -0.008] | 5/5 | **better** |
| Mycelic hierarchy | decoy acc. (all) | 0.230 | 0.430 | +0.200 | [+0.115, +0.290] | 0/5 | **worse** |
| Mycelic hierarchy | decoy D5 stale | 0.300 | 0.820 | +0.520 | [+0.380, +0.660] | 0/5 | **worse** |
| Mycelic hierarchy | decoy D2 scramble | 0.260 | 0.420 | +0.160 | [-0.020, +0.340] | 1/4 | inside noise |
| Mycelic hierarchy | compute | 1.14e+06 | 1.40e+06 | +23% | [+246201.718, +272994.984] | 0/5 | **worse** |
| Mycelic hierarchy | calls (metering) | 9.70e+04 | 2.70e+04 | -72% | [-73601.200, -66326.800] | 5/5 | **better** |
| Mycelic hierarchy | kernel prompt tokens (max) | 8.40e+05 | 1.43e+06 | +71% | [+571386.400, +616543.200] | 0/5 | **worse** |
| A2 chunked long context | found | 0.640 | 0.725 | +0.085 | [+0.045, +0.125] | 5/5 | **better** |
| A2 chunked long context | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | rare recall | 0.509 | 0.575 | +0.065 | [+0.007, +0.113] | 4/5 | **better** |
| A2 chunked long context | AP | 0.070 | 0.225 | +0.155 | [+0.102, +0.206] | 5/5 | **better** |
| A2 chunked long context | FDR | 0.957 | 0.951 | -0.005 | [-0.008, -0.003] | 5/5 | **better** |
| A2 chunked long context | decoy acc. (all) | 0.380 | 0.490 | +0.110 | [+0.070, +0.150] | 0/5 | **worse** |
| A2 chunked long context | decoy D5 stale | 0.540 | 0.700 | +0.160 | [+0.060, +0.280] | 0/4 | **worse** |
| A2 chunked long context | decoy D2 scramble | 0.460 | 0.620 | +0.160 | [+0.060, +0.280] | 0/4 | **worse** |
| A2 chunked long context | compute | 2.07e+06 | 2.07e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | calls (metering) | 3.00e+00 | 3.00e+00 | +0% | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | kernel prompt tokens (max) | 1.00e+06 | 1.00e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | found | 0.405 | 0.615 | +0.210 | [+0.180, +0.255] | 5/5 | **better** |
| B4 central triage | evidence cov. | 0.930 | 0.930 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | rare recall | 0.256 | 0.368 | +0.113 | [+0.044, +0.178] | 4/4 | **better** |
| B4 central triage | AP | 0.026 | 0.129 | +0.103 | [+0.041, +0.177] | 5/5 | **better** |
| B4 central triage | FDR | 0.973 | 0.959 | -0.014 | [-0.017, -0.012] | 5/5 | **better** |
| B4 central triage | decoy acc. (all) | 0.225 | 0.430 | +0.205 | [+0.120, +0.285] | 0/5 | **worse** |
| B4 central triage | decoy D5 stale | 0.360 | 0.800 | +0.440 | [+0.240, +0.640] | 0/5 | **worse** |
| B4 central triage | decoy D2 scramble | 0.260 | 0.460 | +0.200 | [+0.120, +0.300] | 0/5 | **worse** |
| B4 central triage | compute | 3.98e+05 | 3.98e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | calls (metering) | 1.00e+04 | 1.00e+04 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | kernel prompt tokens (max) | 5.20e+05 | 5.20e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | found | 0.225 | 0.420 | +0.195 | [+0.145, +0.275] | 5/5 | **better** |
| Y oracle retrieval | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | rare recall | 0.153 | 0.221 | +0.068 | [-0.011, +0.149] | 3/4 | inside noise |
| Y oracle retrieval | AP | 0.011 | 0.023 | +0.012 | [-0.001, +0.021] | 4/5 | inside noise |
| Y oracle retrieval | FDR | 0.985 | 0.972 | -0.013 | [-0.018, -0.010] | 5/5 | **better** |
| Y oracle retrieval | decoy acc. (all) | 0.140 | 0.315 | +0.175 | [+0.125, +0.230] | 0/5 | **worse** |
| Y oracle retrieval | decoy D5 stale | 0.180 | 0.520 | +0.340 | [+0.200, +0.480] | 0/5 | **worse** |
| Y oracle retrieval | decoy D2 scramble | 0.200 | 0.320 | +0.120 | [-0.040, +0.240] | 1/5 | inside noise |
| Y oracle retrieval | compute | 4.88e+05 | 4.88e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | calls (metering) | 1.00e+04 | 1.00e+04 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | kernel prompt tokens (max) | 7.61e+05 | 7.61e+05 | +0% | [+0.000, +0.000] | 0/0 | same |

paired seeds at 10,000: 5 (old rows 5, new rows 5); register entries kept: OLD [600], NEW [600] (the cap is min(6000, max(600, n_entities)) in both).

### 1.3 OLD vs NEW at 50,000 people (seeds 0–4; seeds 0–2 were the development panel at this scale)

| system | metric | OLD | NEW | Δ | 95% CI | better on | verdict |
|---|---|---:|---:|---:|---|---:|---|
| Mycelic hierarchy | found | 0.336 | 0.566 | +0.230 | [+0.202, +0.266] | 5/5 | **better** |
| Mycelic hierarchy | evidence cov. | 0.420 | 0.708 | +0.288 | [+0.264, +0.312] | 5/5 | **better** |
| Mycelic hierarchy | rare recall | 0.189 | 0.315 | +0.126 | [+0.104, +0.148] | 5/5 | **better** |
| Mycelic hierarchy | AP | 0.009 | 0.108 | +0.099 | [+0.072, +0.130] | 5/5 | **better** |
| Mycelic hierarchy | FDR | 0.988 | 0.981 | -0.007 | [-0.008, -0.006] | 5/5 | **better** |
| Mycelic hierarchy | decoy acc. (all) | 0.204 | 0.378 | +0.174 | [+0.150, +0.196] | 0/5 | **worse** |
| Mycelic hierarchy | decoy D5 stale | 0.360 | 0.744 | +0.384 | [+0.304, +0.464] | 0/5 | **worse** |
| Mycelic hierarchy | decoy D2 scramble | 0.224 | 0.384 | +0.160 | [+0.088, +0.248] | 0/5 | **worse** |
| Mycelic hierarchy | compute | 2.93e+06 | 3.86e+06 | +32% | [+907151.612, +948522.898] | 0/5 | **worse** |
| Mycelic hierarchy | calls (metering) | 2.84e+05 | 1.14e+05 | -60% | [-175966.200, -163911.000] | 5/5 | **better** |
| Mycelic hierarchy | kernel prompt tokens (max) | 1.54e+06 | 3.23e+06 | +109% | [+1623429.600, +1730196.000] | 0/5 | **worse** |
| A2 chunked long context | found | 0.686 | 0.784 | +0.098 | [+0.092, +0.104] | 5/5 | **better** |
| A2 chunked long context | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | rare recall | 0.608 | 0.692 | +0.085 | [+0.056, +0.113] | 5/5 | **better** |
| A2 chunked long context | AP | 0.051 | 0.098 | +0.047 | [+0.037, +0.061] | 5/5 | **better** |
| A2 chunked long context | FDR | 0.977 | 0.974 | -0.003 | [-0.003, -0.003] | 5/5 | **better** |
| A2 chunked long context | decoy acc. (all) | 0.368 | 0.466 | +0.098 | [+0.078, +0.118] | 0/5 | **worse** |
| A2 chunked long context | decoy D5 stale | 0.560 | 0.688 | +0.128 | [+0.088, +0.168] | 0/5 | **worse** |
| A2 chunked long context | decoy D2 scramble | 0.440 | 0.576 | +0.136 | [+0.064, +0.208] | 0/5 | **worse** |
| A2 chunked long context | compute | 1.02e+07 | 1.02e+07 | +0% | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | calls (metering) | 1.00e+01 | 1.00e+01 | +0% | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | kernel prompt tokens (max) | 1.98e+06 | 1.98e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | found | 0.314 | 0.314 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | evidence cov. | 0.462 | 0.462 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | rare recall | 0.112 | 0.116 | +0.004 | [+0.000, +0.012] | 1/1 | inside noise |
| B4 central triage | AP | 0.008 | 0.034 | +0.025 | [+0.018, +0.033] | 5/5 | **better** |
| B4 central triage | FDR | 0.981 | 0.981 | -0.000 | [-0.000, +0.000] | 1/5 | inside noise |
| B4 central triage | decoy acc. (all) | 0.282 | 0.306 | +0.024 | [+0.008, +0.042] | 0/4 | **worse** |
| B4 central triage | decoy D5 stale | 0.576 | 0.672 | +0.096 | [+0.032, +0.168] | 0/4 | **worse** |
| B4 central triage | decoy D2 scramble | 0.360 | 0.360 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | compute | 1.07e+06 | 1.07e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | calls (metering) | 5.00e+04 | 5.00e+04 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | kernel prompt tokens (max) | 5.20e+05 | 5.20e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | found | 0.284 | 0.424 | +0.140 | [+0.084, +0.196] | 5/5 | **better** |
| Y oracle retrieval | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | rare recall | 0.220 | 0.210 | -0.010 | [-0.060, +0.054] | 1/4 | inside noise |
| Y oracle retrieval | AP | 0.005 | 0.012 | +0.007 | [+0.004, +0.010] | 5/5 | **better** |
| Y oracle retrieval | FDR | 0.991 | 0.986 | -0.005 | [-0.007, -0.003] | 5/5 | **better** |
| Y oracle retrieval | decoy acc. (all) | 0.148 | 0.284 | +0.136 | [+0.124, +0.148] | 0/5 | **worse** |
| Y oracle retrieval | decoy D5 stale | 0.176 | 0.576 | +0.400 | [+0.352, +0.448] | 0/5 | **worse** |
| Y oracle retrieval | decoy D2 scramble | 0.192 | 0.280 | +0.088 | [+0.032, +0.136] | 0/4 | **worse** |
| Y oracle retrieval | compute | 2.43e+06 | 2.43e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | calls (metering) | 5.00e+04 | 5.00e+04 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | kernel prompt tokens (max) | 3.79e+06 | 3.79e+06 | +0% | [+0.000, +0.000] | 0/0 | same |

paired seeds at 50,000: 5 (old rows 5, new rows 5); register entries kept: OLD [2776, 2999], NEW [2999] (the cap is min(6000, max(600, n_entities)) in both).

### 1.4 In one paragraph

On the confirmation panel (10k, seeds 5–9) the hierarchy's found rate goes from 0.395 to 0.570 (rare recall 0.220 → 0.348, A2 0.640 → 0.725). On the development panel (seeds 0–4) it goes from 0.375 to 0.625 (evidence coverage 0.690 → 0.980, rare recall 0.215 → 0.375). Compute: +26% against the v1 base as it was benchmarked (unbatched metering) and +44% against the same v1 decisions re-metered with batched descent, which is the like-for-like figure; the model-call reduction (-71%) is a metering convention, not fewer decisions. At 50k it goes from 0.336 to 0.566 (coverage 0.420 → 0.708) for +32% compute as benchmarked and +48% like-for-like. The centralised chunked-context control A2 also improved, because it adopts the same learned ranker (its calibration said the ranker was not worse for it): 0.685 → 0.740 at 10k. The remaining gap to A2 is +0.115 found at 10k and +0.218 at 50k, at 0.66× and 0.38× of A2's compute respectively. Decoy acceptance rose with the ranker at 10k (0.180 → 0.360), and the stale-chain family D5 in particular (0.260 → 0.780); both are reported as regressions, not hidden. The one attempt to train the ranker away from decoys lost found and rare recall on the held-out seeds and was rejected; the D5 mechanism (a staleness gate one late routine mention defeats) is understood and its fix is queued. The frozen configuration also requires a kernel prompt of 1.4M tokens at 10k and 3.2M at 50k, above the modelled tier's 1M context (section 5b).

Targets from the brief: 10k found ≥ 0.70 (stretch 0.75), 50k ≥ 0.60 (stretch 0.70 while materially below A2 compute). Reached: 10k 0.625 (not met), 50k 0.566 (not met), the 50k figure at 0.38× of A2's compute. Section 5 says which stage holds the rest.

## 2. Against the centralised controls on the same NEW worlds

### 2.1 10,000

| system | found | evidence cov. | rare recall | AP | decoy acc. | D5 stale | compute | calls | kernel prompt (max tokens) | found gap to H | compute ratio to H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full | 0.597 | 0.955 | 0.361 | 0.180 | 0.395 | 0.800 | 1.38e+06 | 2.69e+04 | 1.41e+06 | +0.000 | 1.00x |
| A2_chunked_ctx | 0.733 | 1.000 | 0.622 | 0.214 | 0.475 | 0.700 | 2.07e+06 | 3.00e+00 | 1.00e+06 | +0.135 | 1.50x |
| B4_central_triage | 0.577 | 0.950 | 0.326 | 0.129 | 0.390 | 0.740 | 3.98e+05 | 1.00e+04 | 5.20e+05 | -0.020 | 0.29x |
| Y_oracle_retrieval | 0.417 | 1.000 | 0.171 | 0.023 | 0.295 | 0.520 | 4.87e+05 | 1.00e+04 | 7.61e+05 | -0.180 | 0.35x |
| H_mycelic_lean | 0.600 | 0.780 | 0.306 | 0.207 | 0.417 | 0.780 | 7.27e+05 | 2.65e+04 | 3.85e+05 | +0.003 | 0.52x |
| H_mycelic_prev | 0.385 | 0.677 | 0.218 | 0.029 | 0.205 | 0.280 | 1.11e+06 | 9.40e+04 | 8.14e+05 | -0.212 | 0.81x |

### 2.2 50,000

| system | found | evidence cov. | rare recall | AP | decoy acc. | D5 stale | compute | calls | kernel prompt (max tokens) | found gap to H | compute ratio to H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full | 0.566 | 0.708 | 0.315 | 0.108 | 0.378 | 0.744 | 3.86e+06 | 1.14e+05 | 3.23e+06 | +0.000 | 1.00x |
| A2_chunked_ctx | 0.784 | 1.000 | 0.692 | 0.098 | 0.466 | 0.688 | 1.02e+07 | 1.00e+01 | 1.98e+06 | +0.218 | 2.64x |
| B4_central_triage | 0.314 | 0.462 | 0.116 | 0.034 | 0.306 | 0.672 | 1.07e+06 | 5.00e+04 | 5.20e+05 | -0.252 | 0.28x |
| Y_oracle_retrieval | 0.424 | 1.000 | 0.210 | 0.012 | 0.284 | 0.576 | 2.43e+06 | 5.00e+04 | 3.79e+06 | -0.142 | 0.63x |
| H_mycelic_lean | 0.502 | 0.556 | 0.245 | 0.155 | 0.356 | 0.688 | 2.68e+06 | 1.12e+05 | 1.06e+06 | -0.064 | 0.69x |
| H_mycelic_prev | 0.336 | 0.420 | 0.189 | 0.009 | 0.204 | 0.360 | 2.93e+06 | 2.84e+05 | 1.54e+06 | -0.230 | 0.76x |

`H_mycelic_prev` is the v1 hierarchy (question budget 0.25, unbatched metering, earliest-mention link timing, hand-set ranker) run inside the new suite; it is there to show the old configuration reproduces on the new worlds. `H_mycelic_lean` is the compute-lean Pareto point (chain-scoped triage questions; about half the compute, less evidence coverage).

### 2.3 Does the v1 configuration reproduce inside the new suite?

| metric | OLD H_mycelic_full | NEW H_mycelic_prev | max abs. Δ over seeds |
|---|---:|---:|---:|
| found | 0.385 | 0.385 | 0.000 |
| evidence cov. | 0.677 | 0.677 | 0.000 |
| rare recall | 0.218 | 0.218 | 0.000 |
| AP | 0.029 | 0.029 | 0.000 |
| FDR | 0.974 | 0.974 | 0.000 |
| decoy acc. (all) | 0.205 | 0.205 | 0.000 |
| decoy D5 stale | 0.280 | 0.280 | 0.000 |
| decoy D2 scramble | 0.260 | 0.260 | 0.000 |
| compute | 1.11e+06 | 1.11e+06 | 0.00e+00 |
| calls (metering) | 9.40e+04 | 9.40e+04 | 0.00e+00 |
| kernel prompt tokens (max) | 8.14e+05 | 8.14e+05 | 0.00e+00 |

A non-zero difference here would mean a code change altered v1 behaviour; the intended reading is "the same to the third decimal" for discovery and "identical" for compute.

## 3. What changed, and the mechanism behind each change

The loss accounting (`docs/MYCELIC_LOSS_ACCOUNTING.md`) showed the 35-point gap was not where the handoff pack expected. The sketch saw essentially every gold pattern; retrieval reached most of them once asked; the two stages that ate the gold were the question budget (calibrated to 0.25 of the triage queue because the hand-set confidence let extra candidates flood the register) and the register cut itself (candidates matched at ≥ 0.5 but out-ranked by same-entity siblings and background chains). Three changes address exactly those stages; nothing else was changed in the frozen configuration.

1. **Learned ranker over kernel-side features** (`calibrator.py`). The hand-set logistic still decides how many candidates pass the 0.5 gate; a class-weighted logistic fitted on the calibration seeds (500–502) over 45 features the kernel already holds — per-link support and independence, lineage dispersion, lag statistics, the anchor's triage context, evidence shape (origin users, single-witness fraction, echo ratio) and the kernel's own attribution verdict — decides which candidates fill that count. No raw text, no per-user record, no evaluation seed is involved. Adoption is decided per architecture on the calibration seeds so no control is compared at a ranker chosen for somebody else.

2. **Question budget 0.65 of the triage queue, with batched metering** (`systems.py`). With ordering fixed, the budget could be widened; the paired sweep 0.50/0.65/0.80/1.00 saturates at 0.65 at both scales (coverage reaches 0.97 at 10k; 0.80 and 1.00 buy nothing and lose AP). Batching meters one routing call per node and one read per queried user per round, with identical decisions and per-record tokens, which is why calls fall by about two thirds while compute rises.

3. **Hybrid link timing in the temporal DP** (`ops.py`). A link was dated at the earliest mention of its (predicate, entity) pair. Background mentions are not earlier than gold on average (the independent review measured mean day 86 against 65); there are simply several of them per pair, and the earliest of three to nine draws precedes the previous link about half the time for private entities and three quarters of the time for global ones, so the chain-order test fails. Under hybrid timing a link is dated at its heaviest witness cluster and a single-witness link becomes one DP state per cluster: tighter for strong links, looser only for single-witness links. It is free (no calls, no new candidates) and held on the held-out seeds (+0.04 at 10k with no seed worse, +0.06 at 50k on 3/3) while its cousin modal timing did not (+0.04 on calibration, −0.01 held-out — rejected). Two things the review established are withdrawn or flagged: the calibration-seed 'improved decoy resistance' did not replicate — the stale-chain family D5 rises under the adopted arm (+0.12 at 10k, +0.13 at 50k, seven of eight held-out seeds), because a staleness gate that one routine positive mention after a retraction defeats was being masked by min timing by accident; and the lag features the ranker reads are still computed from min timing, which the refit learned around and which is to be fixed before the next refit. The in-pool magnitude quoted by the panel (20–22 of 25 patterns) comes from its own replay, whose code is not in the repository.

### 3.1 The funnel, old and new (Mycelic hierarchy)

#### 10,000

| stage | OLD all | NEW all | OLD rare | NEW rare | OLD common | NEW common |
|---|---:|---:|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0.960 | 0.960 | 0.897 | 0.897 | 1.000 | 1.000 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 0.985 | 0.985 | 0.974 | 0.974 | 0.992 | 0.992 |
| on the triage candidate list | 0.985 | 0.985 | 0.974 | 0.974 | 0.992 | 0.992 |
| inside the question budget | 0.635 | 0.975 | 0.436 | 0.962 | 0.762 | 0.984 |
| descent reached a facet holder | 0.665 | 0.940 | 0.462 | 0.885 | 0.795 | 0.975 |
| >=2 gold links in kernel pool (= evidence coverage) | 0.690 | 0.980 | 0.487 | 0.962 | 0.820 | 0.992 |
| candidate formed on right entity + chain | 0.595 | 0.900 | 0.397 | 0.872 | 0.721 | 0.918 |
| matched primary rule at ANY confidence | 0.540 | 0.890 | 0.346 | 0.872 | 0.664 | 0.902 |
| matched with confidence >= 0.5 | 0.515 | 0.885 | 0.333 | 0.859 | 0.631 | 0.902 |
| survived the register cut (= reported) | 0.375 | 0.625 | 0.205 | 0.372 | 0.484 | 0.787 |
| patterns | 200 | 200 | 78 | 78 | 122 | 122 |


| the pattern died at | OLD | NEW | OLD rare | NEW rare |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0 (0%) | 0 (0%) | 0 | 0 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 3 (2%) | 3 (2%) | 2 | 2 |
| on the triage candidate list | 0 (0%) | 0 (0%) | 0 | 0 |
| inside the question budget | 58 (29%) | 0 (0%) | 37 | 0 |
| descent reached a facet holder | 0 (0%) | 1 (0%) | 0 | 1 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 17 (8%) | 15 (8%) | 7 | 6 |
| matched primary rule at ANY confidence | 14 (7%) | 3 (2%) | 5 | 1 |
| matched, but confidence < 0.5 | 5 (2%) | 1 (0%) | 1 | 1 |
| cut from the register (matched at >= 0.5, out-ranked) | 28 (14%) | 52 (26%) | 10 | 38 |
| **reported** | 75 (38%) | 125 (62%) | 16 | 29 |

#### 50,000

| stage | OLD all | NEW all | OLD rare | NEW rare | OLD common | NEW common |
|---|---:|---:|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0.960 | 0.960 | 0.893 | 0.893 | 1.000 | 1.000 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 0.780 | 0.780 | 0.562 | 0.562 | 0.910 | 0.910 |
| on the triage candidate list | 0.780 | 0.780 | 0.562 | 0.562 | 0.910 | 0.910 |
| inside the question budget | 0.427 | 0.710 | 0.304 | 0.500 | 0.500 | 0.835 |
| descent reached a facet holder | 0.427 | 0.703 | 0.268 | 0.482 | 0.521 | 0.835 |
| >=2 gold links in kernel pool (= evidence coverage) | 0.450 | 0.727 | 0.312 | 0.527 | 0.532 | 0.846 |
| candidate formed on right entity + chain | 0.397 | 0.677 | 0.259 | 0.509 | 0.479 | 0.777 |
| matched primary rule at ANY confidence | 0.363 | 0.657 | 0.232 | 0.491 | 0.441 | 0.755 |
| matched with confidence >= 0.5 | 0.363 | 0.657 | 0.232 | 0.491 | 0.441 | 0.755 |
| survived the register cut (= reported) | 0.363 | 0.587 | 0.232 | 0.339 | 0.441 | 0.734 |
| patterns | 300 | 300 | 112 | 112 | 188 | 188 |


| the pattern died at | OLD | NEW | OLD rare | NEW rare |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 5 (2%) | 5 (2%) | 5 | 5 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 60 (20%) | 58 (19%) | 43 | 41 |
| on the triage candidate list | 0 (0%) | 0 (0%) | 0 | 0 |
| inside the question budget | 100 (33%) | 17 (6%) | 29 | 6 |
| descent reached a facet holder | 0 (0%) | 0 (0%) | 0 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 15 (5%) | 17 (6%) | 6 | 3 |
| matched primary rule at ANY confidence | 11 (4%) | 6 (2%) | 3 | 2 |
| matched, but confidence < 0.5 | 0 (0%) | 0 (0%) | 0 | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 0 (0%) | 21 (7%) | 0 | 17 |
| **reported** | 109 (36%) | 176 (59%) | 26 | 38 |

## 4. The change ledger: everything tried, with its cost

Every row is a paired run on identical worlds; Δ columns are variant minus base, compute and calls are ratios. Status ACCEPTED means the change is in the frozen configuration; rejected means it did not improve found under the real register cap on the held-out seeds (or improved it only at a cost the brief rules out); diagnostic means the run measures a ceiling and is not a fix.

| change | scale | seeds | Δ found | Δ rare | Δ cov. | Δ decoy all | Δ D1 | Δ D2 | Δ D3 | Δ D5 stale | compute | calls (metering) | status |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ranker v1: learned order inside the hand gate | 10,000 | eval 0-4 | +0.065 (5/5 better) | -0.048 | -0.005 | +0.065 | +0.000 | +0.060 | +0.120 | +0.080 | -1% | -0% | superseded |
| ranker v2: learned top-K, anchor-context features | 10,000 | eval 0-4 | +0.105 (5/5 better) | +0.038 | +0.000 | +0.095 | +0.000 | +0.040 | +0.100 | +0.240 | -1% | -0% | superseded |
| ranker v3 + question_frac 0.65 + batched descent | 10,000 | eval 0-4 | +0.210 (5/5 better) | +0.175 | +0.280 | +0.150 | +0.000 | +0.000 | +0.200 | +0.400 | +25% | -71% | ACCEPTED |
| decoy-weighted ranker selection | 10,000 | eval 0-4 | -0.060 (1/5 better) | -0.118 | +0.000 | -0.050 | +0.020 | -0.040 | +0.020 | -0.200 | -0% | -0% | rejected |
| question_frac 0.80 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/4 better) | -0.022 | +0.015 | +0.015 | +0.000 | +0.020 | +0.000 | +0.040 | +13% | +1% | rejected |
| question_frac 1.00 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/3 better) | -0.005 | +0.015 | -0.015 | +0.000 | +0.020 | -0.020 | -0.060 | +31% | +1% | rejected |
| local re-extraction at the user (vs qf 0.65 batched) | 10,000 | eval 0-4 | -0.005 (3/5 better) | -0.042 | +0.000 | -0.020 | +0.020 | +0.040 | -0.020 | -0.120 | +1% | +0% | rejected |
| batched descent metering alone (ranker v2, qf 0.65; same decisions) | 10,000 | eval 0-4 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -17% | -84% | ACCEPTED |
| modal link timing (+ refitted ranker) | 10,000 | eval 0-4 | -0.010 (2/5 better) | -0.058 | -0.005 | +0.010 | +0.020 | -0.040 | -0.000 | +0.060 | +0% | -0% | rejected |
| hybrid link timing (+ refitted ranker) | 10,000 | eval 0-4 | +0.040 (2/2 better) | -0.015 | +0.010 | +0.030 | -0.020 | +0.020 | +0.000 | +0.120 | +1% | +1% | ACCEPTED |
| hybrid + chain-scoped questions (H_mycelic_lean) | 10,000 | eval 0-4 | +0.025 (3/4 better) | -0.071 | -0.170 | +0.045 | +0.020 | +0.200 | -0.100 | +0.060 | -47% | -1% | Pareto arm |
| ranker v3 + qf 0.65 + batched, 50k | 50,000 | eval 0-2 | +0.163 (3/3 better) | +0.100 | +0.273 | +0.137 | +0.053 | +0.107 | +0.093 | +0.293 | +32% | -61% | ACCEPTED |
| question_frac 0.80 at 50k | 50,000 | eval 0-2 | +0.007 (2/3 better) | -0.032 | +0.047 | +0.033 | +0.013 | +0.093 | +0.027 | -0.000 | +12% | +2% | rejected |
| local re-extraction at 50k | 50,000 | eval 0-1 | +0.007 (1/3 better) | -0.025 | -0.003 | +0.040 | +0.013 | +0.067 | +0.027 | +0.053 | +1% | -0% | rejected |
| hybrid link timing at 50k (+ refitted ranker) | 50,000 | eval 0-2 | +0.060 (3/3 better) | +0.009 | +0.003 | +0.040 | +0.013 | +0.013 | +0.000 | +0.133 | -0% | -0% | validation |
| hybrid + chain-scoped questions at 50k | 50,000 | eval 0-2 | -0.003 (1/3 better) | -0.072 | -0.143 | +0.007 | +0.000 | +0.013 | -0.013 | +0.027 | -31% | -1% | Pareto arm |
| v1 hierarchy re-metered with batched descent (identical decisions) | 10,000 | eval 0-4 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -12% | -73% | metering control |
| v1 hierarchy re-metered with batched descent at 50k | 50,000 | eval 0-2 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -12% | -65% | metering control |
| unbounded register (DIAGNOSTIC ONLY, not a fix) | 10,000 | eval 0-4 | +0.165 (5/5 better) | +0.127 | +0.000 | +0.150 | +0.040 | +0.180 | +0.100 | +0.280 | +7% | +0% | diagnostic |
| unbounded register + full question budget (DIAGNOSTIC) | 10,000 | eval 0-4 | +0.420 (5/5 better) | +0.482 | +0.295 | +0.295 | +0.120 | +0.200 | +0.440 | +0.420 | +117% | +143% | diagnostic |
| chain-scoped questions alone (calibration seeds) | 10,000 | cal 500-502 | +0.008 (2/3 better) | -0.066 | -0.175 | -0.008 | +0.067 | +0.033 | -0.100 | -0.033 | -48% | -1% | screen |
| cluster-aware staleness gate (strong positives only), calibration seeds | 10,000 | cal 500-502 | +0.058 (2/2 better) | +0.019 | -0.008 | -0.042 | +0.000 | -0.033 | -0.067 | -0.067 | -0% | +0% | screen |
| strong staleness gate, frozen ranker, FRESH panel | 10,000 | eval 10-14 | -0.035 (0/3 better) | -0.039 | +0.000 | -0.040 | -0.040 | -0.020 | +0.020 | -0.120 | +0% | +0% | fresh-panel read |
| strong staleness gate + refitted ranker, FRESH panel | 10,000 | eval 10-14 | +0.020 (2/4 better) | +0.017 | +0.010 | -0.020 | -0.060 | -0.080 | +0.040 | +0.020 | +0% | +0% | fresh-panel read |

Not in the table because they were single calibration-seed screens (logs/research_log.md, iteration 21): merging descent returns per (predicate, entity) before the kernel reads them (compute −54%, found 0.400 → 0.275; per polarity 0.325) and support-1 sketch bits admitted with cross-region corroboration (found 0.400 → 0.225: 547 weak candidates flood the triage list). Both stay off; the weak-bit mechanism needs budget-aware admission before it is worth a paired run, and merging needs the ranker refitted on merged candidates.

### 4.1 Compute cost per accepted change (10k, evaluation seeds)

| configuration | found | rare recall | compute | calls (metering) | kernel prompt tokens | Δ found per +10% compute (vs previous row) |
|---|---:|---:|---:|---:|---:|---:|
| v1 hierarchy (hand ranker, qf 0.25), metered unbatched as benchmarked | 0.375 | 0.215 | 1.09e+06 | 9.10e+04 | 7.88e+05 | — |
| v1 hierarchy re-metered with batched descent (identical decisions) — the like-for-like base | 0.375 | 0.215 | 9.53e+05 | 2.46e+04 | 7.88e+05 | +0.000 at -12.4% compute |
| + ranker v3, qf 0.65, batched descent | 0.585 | 0.390 | 1.36e+06 | 2.66e+04 | 1.39e+06 | +0.049 |
| + hybrid link timing (refitted ranker) | 0.625 | 0.375 | 1.37e+06 | 2.67e+04 | 1.40e+06 | +0.040 at +0.5% compute |

The first row is the v1 base as it was benchmarked (unbatched metering); the second is the same decisions re-metered with batched descent, which is the base every later row should be read against. Batching is a metering convention: it changes no decision and no per-record token.

The biggest single gain is the ranker (with the budget it unlocked): +0.21 found for +25% compute. Hybrid timing is the cheapest: +0.04 for +1%.

## 5. Largest remaining loss stage

In the new funnel the most common terminal loss for the hierarchy is **cut from the register (matched at >= 0.5, out-ranked)** at 10k (52 of 200 gold patterns) and **visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2)** at 50k (58 of 300).

At 10k the register is still the binding stage: coverage is near 0.97, so almost every gold pattern is in the kernel pool, and the loss is ordering among the ~3,000 candidates that pass the gate for 600 slots. At 50k the register (2,999 slots) is not binding; coverage (~0.72) is, i.e. the question budget and the descent's reach. Those are different problems and the queue in section 8 treats them separately.

## 5b. Limitations the independent review established

- **Kernel context.** The kernel reads its whole pool in one call: 1.40M tokens at 10k and 3.23M at 50k in the frozen configuration (v1: 0.79M and 1.54M), against the modelled frontier tier's 1M-token context, which the simulator enforces only for the flat controls (A2 is chunked at 1M). No degradation is modelled for the hierarchy's kernel. v1 already exceeded the limit at 50k; vNext pushes 10k over it. Any deployment claim needs the kernel read chunked at the context limit (queue).
- **Compute conventions.** Batched metering changes no decision and no per-record token, but it was applied to the new arm only; the like-for-like compute increase is the second convention in section 4.1 and the call reduction is entirely metering.
- **Claims at the kernel.** The exposure counters see only the upward pass; descent returns per-user claim objects to the kernel and the budget change roughly doubles them (about 30k → 53k objects at 10k, 61k → 127k at 50k). Raw text stays at 0. The counter is to be extended; `J_mycelic_verified`'s raw-record count is hard-coded to 0 although its verification round reads raw records at the kernel.
- **Ranker hygiene.** The register cut happens before the five triage-context features are filled (they are constant at cut time, so membership is decided by the other 40 features; the ranker was fitted on post-enrichment dumps); the controls' dumps contain only their post-cut candidates; under hybrid timing the lag features still use min timing; the J dump duplicates the min-timing hierarchy candidates. None changes found, all are fixes for the next refit.
- **Fairness of link timing.** The flat controls collapse each (predicate, entity) to one object, on which hybrid timing is a no-op by construction; a fair test gives them per-user objects and charges the context (queue).
- **Panel reuse.** Section 6.

## 6. Did the gains survive held-out seeds? (the honest version)

The intended protocol was: choose on seeds 500–502, read once on seeds 0–4 (10k) and 0–2 (50k). That is not what happened, and the first independent refuter (REVIEWER_ATTACK.md) counted it: seeds 0–4 were read after roughly eleven accept/reject decisions over some forty configurations, and two frozen knobs were chosen on those seeds rather than on the calibration seeds — the question budget (both sweeps under a learned ranker ran on 0–4; the only calibration-seed sweep was the v1 one with the hand ranker) and the rejection of local re-extraction. The ranker fit and the per-architecture adoption are clean (only calibration-seed rows enter them). Two candidates that won on the calibration seeds lost on 0–4 and were dropped — modal link timing (+0.04 → −0.01) and decoy-weighted selection (found −0.06, rare −0.12) — which is the panel doing its job, and also evidence that decisions were being made on it.

What this costs: with a paired standard error of about 0.02 found on five seeds, picking the best of four to eight arms inflates a null by +0.02 to +0.03. The small accepted deltas (budget 0.65 vs 0.50 +0.03; hybrid timing +0.04) are therefore not established by the development panel alone; the large one (ranker + budget + batching, +0.21 on every seed, more than ten standard errors) is. The cure is the confirmation panel in section 1.2: seeds 5–9 at 10k were never read by any experiment, dump or funnel before the final rerun, and the OLD rows for them exist in the archive, so that comparison is a clean single read. Where two legitimate arms differed only inside noise on the development panel (the ranker refitted under hybrid timing vs the previous one), the pre-registered rule — fit the ranker on the calibration seeds under the pipeline it will run in — decided, not the numbers.

One clean read of the frozen configuration on a panel nothing else ever touched exists as a by-product of the staleness-gate test: seeds 10–14 at 10k, found 0.650, rare recall 0.364, evidence coverage 0.930, decoy acceptance 0.365 (D5 0.740), compute 1.39e+06 (`quick_strong_fresh.jsonl`, arm `hyb`). It has no OLD counterpart, so it is a level, not a delta; it sits between the development-panel and confirmation-panel levels in section 1.

A budget sweep on the calibration seeds under the frozen ranker and timing was run after the refuter's report. The two sweeps are printed together so the reader can see whether the calibration seeds agree with the value the development panel chose:

**Calibration seeds (quick_qf_cal.jsonl, frozen ranker and hybrid timing)**

seeds 500–502 at 10,000 (calibration panel), paired Δ against qf0.65:

| arm | seeds | found | Δ found | rare recall | evidence cov. | AP | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qf0.50 | 3 | 0.617 | +0.017 | 0.343 | 0.850 | 0.219 | 0.375 | 1.22e+06 | 2.64e+04 |
| qf0.65 | 3 | 0.600 | +0.000 | 0.364 | 0.925 | 0.225 | 0.400 | 1.38e+06 | 2.68e+04 |
| qf0.80 | 3 | 0.625 | +0.025 | 0.364 | 0.958 | 0.207 | 0.392 | 1.58e+06 | 2.70e+04 |
| qf1.00 | 3 | 0.592 | -0.008 | 0.282 | 0.958 | 0.186 | 0.375 | 1.85e+06 | 2.70e+04 |

**Development panel (quick_qf_v3.jsonl, ranker v3, min timing)**

seeds 0–4 at 10,000 (evaluation panel), paired Δ against qf0.65:

| arm | seeds | found | Δ found | rare recall | evidence cov. | AP | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qf0.50 | 5 | 0.555 | -0.030 | 0.356 | 0.875 | 0.157 | 0.320 | 1.20e+06 | 2.62e+04 |
| qf0.65 | 5 | 0.585 | +0.000 | 0.390 | 0.970 | 0.157 | 0.330 | 1.36e+06 | 2.66e+04 |
| qf0.80 | 5 | 0.565 | -0.020 | 0.368 | 0.985 | 0.054 | 0.345 | 1.54e+06 | 2.67e+04 |
| qf1.00 | 5 | 0.565 | -0.020 | 0.384 | 0.985 | 0.046 | 0.315 | 1.79e+06 | 2.68e+04 |

Reading: on the calibration seeds the budget is flat between 0.50 and 0.80 (the per-seed spread is larger than any difference between arms), rare recall and coverage rise from 0.50 to 0.65 and 1.00 loses both found and rare recall. The calibration seeds neither single out 0.65 nor contradict it; the honest description of the frozen value is "inside the calibration-seed plateau, chosen on the development panel", and the budget's contribution is bounded by the confirmation panel, not by either sweep.

## 7. Ranker evaluation on the final configuration

| dump | system | gold patterns | matchable | found, hand ranker | found, learned (same count) | AUC hand | AUC learned |
|---|---|---:|---:|---:|---:|---:|---:|
| _finalH | H_mycelic_full | 200 | 172 | 0.365 | 0.620 | 0.690 | 0.848 |
| _finalC | A2_chunked_ctx | 200 | 148 | 0.740 | 0.740 | 0.779 | 0.813 |
| _finalC | B4_central_triage | 200 | 108 | 0.540 | 0.540 | 0.727 | 0.724 |
| _finalC | Y_oracle_retrieval | 200 | 83 | 0.415 | 0.415 | 0.609 | 0.648 |

Evaluation rows are the held-out seeds 0–4 only; the logistic is refitted on the calibration seeds of the same dump with the stored l2 and interaction choice, so the AUC and found columns are out-of-sample.

## 8. Frozen configuration

| knob | frozen value | seeds the cited evidence was read on | evidence |
|---|---|---|---|
| `question_frac` | `0.65` | quick_qf_v3.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_qf_v3.jsonl` |
| `batched_descent` | `True` | quick_v3_H.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_v3_H.jsonl` |
| `link_time` | `hybrid` | quick_refit_hyb.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_refit_hyb.jsonl` |
| `local_reextract` | `False` | quick_rx_reextract.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_rx_reextract.jsonl` |
| `strict_targeting` | `False` | quick_cal_screen.jsonl: seeds 500–502 at 10,000 — calibration | `quick_cal_screen.jsonl` |
| `triage_target_chains` | `none` | quick_cal_screen.jsonl: seeds 500–502 at 10,000 — calibration | `quick_cal_screen.jsonl` |
| ranker | logistic, l2 0.3, interactions True, 23614 candidates | seeds [500, 501, 502] | `research/mycelic/artifacts/calibration.hyb.json` |
| ranker adopted by | A2_chunked_ctx, A_flat_rag, B2_map_reduce, B4_central_triage, D_hier_nolineage, E_hier_lineage, F_hier_retrieval, G_hier_questions, H_mycelic_full, H_mycelic_lean, I_mycelic_completion, J_mycelic_verified, Y_oracle_retrieval | calibration seeds | ranker_arch_table |

## 9. Ranked experiment queue (by information value, not size)

| rank | experiment | what it distinguishes | expected information | cost | status |
|---:|---|---|---|---|---|
| 1 | Register-forecast question budget at 50k (ask in gain order by distinct anchor until the forecast candidate count reaches α × cap) with descent fan-out 1 | whether 50k coverage is limited by the number of anchors asked (breadth) or by reach per anchor (depth) | high: coverage is the binding 50k stage and the two explanations imply opposite spending | ~10 paired 50k runs | not run |
| 2 | D5 stale-chain mechanism: a staleness gate that compares the retraction against the link's assertion cluster (not just "strong positives only", which was tried) | whether the D5 rise under hybrid timing is the gate or the timing itself | high: D5 rose 0.26 → 0.78 across the accepted changes | cal screen + refit + fresh-panel read | the "strong positives only" variant was screened (+0.058 found on calibration seeds) and REJECTED on the fresh panel (found −0.035 with the frozen ranker, D5 −0.12; refit +0.02 / +0.02): it trades found for D5. The cluster-aware variant is still open |
| 3 | Kernel read chunked at the tier's context limit (as A2 already is), or an explicit degradation model | whether the vNext gain survives a kernel that cannot read a 1.4M/3.3M-token pool in one call | high: precondition of any deployment claim | code + paired runs at both scales | not run |
| 4 | Fresh evaluation panel (seeds 10–14 at 10k, 5–7 at 50k) read once | whether the development-panel deltas hold; this rerun's seeds 5–9 at 10k are the first such read | high for the small deltas | one suite run | seeds 5–9 done in this rerun |
| 5 | Sketch-corroboration feature for the ranker (foreign-site bit for each link's (entity, predicate)) | whether the ranker's remaining 10k ordering loss is missing information or missing capacity | high: offline AUC 0.74–0.77 for the feature; a null result says capacity | dump + refit + 5 paired runs | not run |
| 6 | Per-user object pools for A2/B4/Y with hybrid timing, context charged | whether the timing gain is the topology's or the controls' object builder's | medium: the panel's item was a null by construction | code + paired runs | not run |
| 7 | Lag features from the DP's chosen times; ranker applied once after enrichment; full-list dumps for the controls; then refit | whether the ranker hygiene defects cost anything | medium | refit + 5 paired runs | not run |
| 8 | Pairwise / top-K ranking objective on the same features | same question as 5 from the objective side | medium | refit only | not run |
| 9 | Budget-aware support-1 sketch bits (admit weak bits only into unused question budget) | whether rare patterns are lost at the sketch or at the budget | medium: rare recall is 0.33–0.42 and the weak-bit screen flooded triage | 5 paired runs | screen failed as implemented |
| 10 | Chain-scoped questions with a second untargeted round for thin answers | whether the lean arm's coverage loss can be bought back for less than the 47% compute it saves | medium | 5 paired runs | not run |
| 11 | Descent-evidence merge per polarity with a refitted ranker | compute −54% claim vs the found loss seen on one seed | medium | dump + refit + paired | one-seed screen only |
| 12 | Live model discrimination on kernel-side candidates (frontier vs small model) | whether the simulated kernel tier understates or overstates what a real model does with the same evidence | high for external validity, no effect on the simulator numbers | API budget | live harness exists |


## 10. Where the reports are

- `docs/MYCELIC_ENTERPRISE.md` / `.pdf` — the full benchmark document, regenerated on the new artifacts.
- `docs/MYCELIC_LOSS_ACCOUNTING.md` / `.pdf` — the per-pattern loss accounting, regenerated.
- `docs/mycelic_vnext/` — this report, `VNEXT_ARCHITECTURE.md`, `EXPERIMENT_MATRIX.md`, `LOSS_ACCOUNTING.md`, `REVIEWER_ATTACK.md`, the diagrams `fig_topology.svg` and `fig_loop.svg`.
- `research/mycelic/artifacts/v1/` — the archived pre-vNext artifacts the OLD columns come from.
- `research/mycelic/logs/research_log.md` — the iteration-by-iteration record, including the negative results.


## What would change my mind

- If `H_mycelic_prev` inside the new suite does not reproduce the archived v1 rows to the third decimal, the old-vs-new comparison is contaminated by a code change and every Δ in section 1 is suspect.
- If the learned ranker's out-of-sample AUC in section 7 is not above the hand-set score's, the found gain is a gate artefact and should not survive a different register cap.
- If a fresh set of evaluation seeds (5–9) gives hybrid timing a negative paired delta, it joins modal timing in the rejected column.
- If A2 with the ranker beats the hierarchy at 50k at equal compute (it does not today: 10.2e6 vs 3.9e6 units), the compute argument for the hierarchy is gone and only the privacy argument remains.
- If queue item 1 shows 50k coverage is depth-limited, the lean arm's mechanism is the wrong direction and breadth spending should be reverted.
- If the live discrimination harness shows a frontier model extracting a signal from raw notes that no kernel-side feature carries, the ranker's ceiling is a property of the simulator, not of the design.
- If the stale-chain decoy family D5 keeps rising on a fresh panel after the staleness gate is made cluster-aware, hybrid timing is a relaxation of the temporal check and is withdrawn.
- If chunking the kernel read at the tier's 1M-token context removes the vNext gain, the gain was bought with an unmodelled context and the frozen configuration is not deployable as described.
- If counting descent-returned objects in the exposure metric moves the hierarchy's claim exposure to the level of the centralised triage control, the privacy argument for the hierarchy is the raw-text line alone.


<div style="page-break-after: always"></div>

# Mycelic vNext — architecture

_The design as frozen for the final rerun. Every mechanism named here is implemented in `research/mycelic/{systems,ops,calibrator,runner}.py` and exercised by the benchmark; nothing described is a proposal unless marked as such._

![topology](fig_topology.svg)

## 1. Topology and node responsibilities

The org chart is the topology: USER → TEAM → DEPT → SITE → REGION → ENTERPRISE kernel. vNext keeps it, not for branding but because the loss accounting found no stage where the levels themselves lose gold: the losses were in the kernel's policies. The level-marginal experiment (E3b) in the main report is the evidence that a level can be removed only at a cost in routing precision.

| node | holds | computes | sends up | answers down |
|---|---|---|---|---|
| USER | its own raw records (never leave) and its local claim index | local extraction to claims; targeted reads from its own index (metered as index lookup + matched records) | claims (knowledge objects) with lineage | the records that match (entity, target predicates); nothing when a strict read matches nothing |
| TEAM / DEPT | knowledge objects of its subtree; anchor index (optionally a Bloom filter) | merge, dedupe by signature, adaptive abstraction | merged objects, entity index | routes a question to the children whose index holds the entity |
| SITE | the site sketch: per (entity, predicate) a support-thresholded bit (support ≥ 2 witnesses), total mentions, first/last time | sketch construction (no model call) | sketch entries (≤ 4,000 per site) + counts | routes down; is the seed of a descent when the kernel's triage marks it foreign for the entity |
| REGION | merged sketch of its sites | bit union with per-site provenance | merged sketch | routing |
| KERNEL | the pool of claims it has been sent, the merged enterprise sketch, the question ledger, the candidate list, the register | triage, question policy, synthesis, verification, ranking | — | the questions |

## 2. Data structures

```
SketchEntry(site, entity) = (total_mentions, bitmask over predicates with support>=2, t0, t1)
                           # + weak mask (support 1) when sketch_weak_bits is on (OFF in vNext)
KO (knowledge object)     = (pred, anchor, polarity, tmin, tmax, sigs: set[witness signature],
                             branches: {level: set[node]}, n_raw, importance, q_tag, neg_tmax, pos_tmax)
Hypothesis                = (anchor, chain, preds[], links[] (per-link support, regions, lag),
                             conf, verified, contra, n_indep, feat: {45 kernel-side features})
Question                  = (anchor, target_preds[], expected_gain, cost_est, branch_hint,
                             well_targeted, answered, n_new_evidence)
Register                  = top-K hypotheses by conf, K = min(6000, max(600, n_entities))
Ledgers (batched metering)= route_ledger[node] -> #questions routed this round;
                             read_ledger[user] -> [(index_size, matched, anchor)]
```

## 3. Messages

**Upward.** Claims (KOs) carry a predicate, an entity, a polarity, a time interval, the set of witness signatures and the branch lineage — never the record text. Sketch entries carry counts and bits. The benchmark's exposure metrics count the upward pass: raw text leaving a node is 0.0 and the fraction of claims leaving their node on that pass is unchanged by vNext (0.138 at 10k), because no change touched what moves up. Descent returns are a second channel the counters do not observe: a queried user answers with per-user claim objects, and the wider budget roughly doubles the objects the kernel holds (about 30k → 53k at 10k, 61k → 127k at 50k). Extending the counter to that channel is queued.

**Downward.** A question names an entity and, optionally, target predicates; it is routed by the kernel's triage (foreign sites for the entity, home site avoided), then by each node's anchor index, with per-parent quotas (descent fan-out 3; 5/5/5/8 children per level). A strict read (the lean arm) returns only the target predicates; the default soft read falls back to every record on the entity when nothing matches.

## 4. Routing

```
descend(anchor, target_preds, budget_nodes, start_nodes, avoid):
    frontier = start_nodes or [root]              # triage seeds: the entity's foreign sites
    for level in SITE, DEPT, TEAM, USER:
        children = [c for n in frontier for c in index_children(n, anchor)] - avoid
        rank children by (index hit count, importance prior); keep per-parent quota
        route_ledger[n] += 1 for each parent n          # one routing call per node per round
        frontier = kept children
    for u in frontier at USER:
        sel = u.index[anchor]; if target_preds: sel = sel[pred in target_preds] (strict) or fallback
        read_ledger[u].append(len(sel)); return claims(sel) with lineage
flush_descent_ledger(): meter one call per routed node and one read per queried user
```

## 5. Question policy

```
question_round(hyps, pool):
    Q = []
    for h in hyps by conf desc:                          # 1. hypothesis-driven questions
        missing = chain predicates adjacent to h.preds not in h.preds
        Q += Question(h.anchor, missing, gain=f(conf, n_indep, contra), well_targeted=True)
    for entity in merged sketch:                         # 2. sketch triage (no model call)
        foreign = sites holding <= 35% of the entity's mentions
        if |foreign| < 2 or |regions(foreign)| < 2: continue
        span, chain = longest single-chain run in OR(foreign bits)
        if span < 2: continue
        gain = (0.6*span + 0.35*|regions| + 0.3*[unseen]) / (1 + 0.25*log1p(total))
        Q += Question(entity, target_preds=[] | chain preds (lean), gain, seeds=foreign, avoid=home)
    Q.sort(by gain / cost); nq = min(4000, max(220, 0.65 * |Q|))        # frozen budget
    for q in Q[:nq]: pool += descend(q.anchor, q.target_preds, fanout=3, q.seeds, q.avoid)
    flush_descent_ledger()
    hyps = synthesize(pool, link_time='hybrid'); annotate_anchor_context(hyps); apply_ranker(hyps)
```
The budget is a fraction of the triage queue so it scales with the number of entities the sketch flags (about 30× more at 50k than at 2k). 0.65 was chosen because the paired sweep saturates there.

## 6. Candidate generation (kernel)

```
synthesize(pool):
    group claims by (entity after entity-check, predicate)
    for entity, for each causal chain with >= 2 of its predicates present:
        links = chain-ordered predicates; drop links below min support (dedup check)
        # hybrid timing (vNext): cluster each link's claims into <= 3-day windows;
        # a link with a cluster of >= 2 independent witnesses gets ONE state at that
        # cluster's time; a link whose witnesses disagree gets one state per cluster
        states = [(link, t_cluster, w = log1p(support) + 0.35)]
        path = heaviest chain-ordered, time-ordered path over states (t_j <= t_i + 3)
        if staleness (retracted after asserted): drop
        emit Hypothesis(entity, chain, path[:synth_depth], features)
    hallucinations at the tier's rate; anchor-level unverified lump when the causal check fails
```

## 7. Verification and ranking

Four checks with tier-dependent pass probabilities (causal, temporal, dedup, entity), the optional evidence verification round of `J_mycelic_verified` (which writes the attribution feature), then the ranker: `k = #candidates with hand_conf ≥ 0.5`; candidates are sorted by the learned probability `p`; the top k get `conf = 0.5 + 0.5p`, the rest `0.5p`; the register keeps the top K by conf. The ranker is a logistic (l2 and interaction set chosen leave-one-seed-out on the calibration seeds) over 45 features the kernel already holds; the depth-2 boosted trees lost to it every time and were not adopted.

## 8. Privacy boundaries

- Raw records: user node only. Re-extraction (rejected) would have re-read them locally; nothing proposed moves them.
- Claims: leave the user node as (predicate, entity, polarity, interval, witness signatures, lineage). This is the benchmark's `claim_exposure_fraction`.
- Sketch entries: counts and bits; no entity text beyond the entity id the enterprise already shares.
- Ranker features: every feature is a function of the claims and sketch the kernel already holds; the anchor-context features are kernel aggregates (how many candidates share the entity, its triage gain), not per-user data.
- No centralisation is hidden in vNext: the controls that centralise (A2, B4, Y) are run as such and labelled. What the kernel does hold is per-user claim objects returned by descents, and vNext doubles them; the exposure counters do not yet count that channel (section 3).

## 9. Model allocation, caching, complexity, failure handling

- **Allocation**: back-loaded tiers (small models at USER/TEAM extraction, frontier at the kernel) as in v1; E3 in the main report is the ablation.
- **Caching / batching**: routing and user reads are metered once per node per round (the ledgers); the kernel re-reads its pool once per synthesis. Decisions and per-record tokens are identical to unbatched metering — that is why calls fall ~65–70% while compute rises with the budget.
- **Complexity**: triage is O(entities in sketch) integer work; questions O(nq × fan-out × levels) routing calls; synthesis O(pool + Σ states²) with ≤ ~15 states per (entity, chain); ranking O(candidates × 45). The kernel reads its whole pool in one call — 1.3–1.5M tokens at 10k and 3.2–3.4M at 50k in the frozen configuration, above the modelled tier's 1M context, which the simulator enforces only for the flat controls; a chunked kernel read is queued before any deployment claim.
- **Failure handling**: unavailable branches and malicious nodes are E5 in the main report (unchanged by vNext); the lean arm's failure mode — the sketch names the wrong chain for ~16% of gold entities and a strict read then returns nothing — is why it is a separate arm and not the default.

![loop](fig_loop.svg)

## 10. What is proposed but not implemented

Register-forecast budgeting with fan-out 1 at 50k, the sketch-corroboration ranker feature, budget-aware weak sketch bits, and a pairwise ranking objective — see the ranked queue in `NEXT_RESEARCH_REPORT.md`.


<div style="page-break-after: always"></div>

# Mycelic vNext — experiment matrix

_Twenty hypotheses across the ten areas the pack asked for, each with mechanism, expected gain, privacy risk, compute cost, failure mode and minimal ablation; the status column is the measured result where one exists (paired on identical worlds, held-out seeds unless marked cal). The five highest-information experiments and the ranked queue follow._

## 1. Hypotheses

| id | area | hypothesis | mechanism | expected gain | privacy risk | compute | failure mode | minimal ablation | status |
|---|---|---|---|---|---|---|---|---|---|
| H01 | ranking/calibration | Learned ranker over kernel-side features, hand gate count preserved | logistic on 45 kernel-held features decides WHICH candidates fill the hand-gated count | +0.10–0.20 found at 10k | none (no raw text, kernel aggregates only) | zero calls; one fit on calibration seeds | over-fits 3 seeds; promotes decoys | quick_v3_H: ranker off vs on, 5 paired seeds | ACCEPTED (+0.21 with budget; decoy acc. +0.15) |
| H02 | active question selection | Question budget as 0.65 of the triage queue | widen the budget once ordering holds | +0.03 at 10k over 0.50; saturates | none | +13% compute per +0.15 of budget | floods register when ranking is weak | quick_qf_v3 sweep 0.50/0.65/0.80/1.00 | ACCEPTED at 0.65; 0.80/1.00 rejected |
| H03 | caching/batching | Batched metering per routed node and queried user | ledgers; identical decisions | calls −65–70%, compute −17% at equal budget | none | negative | none observed | quick_rk2_qf0.65 vs quick_rx_base065 | ACCEPTED |
| H04 | candidate generation | Hybrid link timing in the temporal DP | date links at heaviest witness cluster; float singletons | +0.04–0.08 found, rare + | none | zero | long-spread real chains could pick a minority time | quick_refit_hyb (5 held-out seeds) | ACCEPTED (+0.04, CI [0, +0.09]) |
| H05 | candidate generation | Modal link timing | date links at heaviest cluster only | +0.025–0.05 | none | zero | ties pick earliest = old behaviour | quick_refit_mod | REJECTED (−0.01 held-out; +0.04 on cal seeds) |
| H06 | ranking/calibration | Decoy-weighted ranker selection | up-weight decoy rows, penalise decoys in LOSO objective | decoy acc. −0.05 | none | zero | fits 3 seeds' decoys | quick_seldw_H | REJECTED (found −0.06, rare −0.12) |
| H07 | evidence reconstruction | Targeted local re-extraction at the user | re-extract records naming the anchor not yet claimed | +0.04 at 50k? | none (stays local) | +1% compute, 6× wall time | adds echo evidence | quick_rx_reextract; quick_v50_v3 rx arm | REJECTED (no gain at 10k; +0.04/−0.01 at 50k) |
| H08 | descent/routing | Chain-scoped triage questions (strict read of flagged chains) | target_preds = chains with span ≥ 2 | compute −47%, found ±0 | none | negative | sketch names wrong chain for ~16% of gold entities; coverage −0.17 | quick_cal_screen span2; quick_refit_hyb lean arm | KEPT AS H_mycelic_lean (Pareto arm), not default |
| H09 | richer sketches | Support-1 sketch bits with cross-region corroboration | weak site bits admitted only with a strong foreign bit elsewhere | rare recall + | none | +11% compute | floods triage (547 weak candidates on one seed) | logs iteration 21, one cal seed | REJECTED as implemented (found 0.40 → 0.225) |
| H10 | caching/batching | Merge descent returns per (predicate, entity[, polarity]) before the kernel reads | one KO per pair | compute −54% | none | negative | loses per-user time structure the DP needs | logs iteration 21, one cal seed | REJECTED (found 0.40 → 0.275 / 0.325) |
| H11 | ranking/calibration | Per-entity diversity in the register | cap same-anchor candidates | +0.02 | none | zero | freed slots go to junk | offline rescoring (scratch) | REJECTED (no gain) |
| H12 | ranking/calibration | Depth-2 gradient-boosted trees instead of logistic | same features | AUC + | none | zero | 3 seeds too few | select_and_store grid (both selections) | REJECTED (lost LOSO found both times) |
| H13 | active question selection | Register-forecast budget by distinct anchor with fan-out 1 (breadth over depth) | ask until forecast candidates reach α × cap | 50k +0.10 at ≤ +20% compute (panel estimate) | none | +19% at 50k (est.) | rare patterns in unreached teams | paired 50k runs | NOT RUN (queue #1) |
| H14 | ranking/calibration | Sketch-corroboration feature per link | foreign-site bit count for (entity, pred) | +0.025 alone (offline) | none | zero | rare patterns never set a bit | dump + refit + paired | NOT RUN (queue #2) |
| H15 | ranking/calibration | Pairwise / top-K ranking objective | optimise the cap directly | unknown | none | zero | noisier fit | refit only | NOT RUN (queue #3) |
| H16 | active question selection | Second untargeted round for thin strict answers | re-ask when < 2 on-chain preds returned | lean coverage + | claims leave node (as today) | +7% | re-floods register | paired | NOT RUN (queue #5) |
| H17 | temporal/causal reasoning | Interval order test (tmin_j ≤ tmax_i + slack) | loosen the DP edge test | +0.05–0.08 but decoy +0.10–0.15 | none | zero | D2 scrambled decoys pass | quick_paired link_time=interval | NOT RUN (panel predicts decoy leak; low priority) |
| H18 | contradiction handling | Per-chain enumeration when the causal check fails | emit one unverified hypothesis per chain | ≤ +0.01 | none | negligible | more junk | paired | NOT RUN (low information) |
| H19 | topology changes | Remove a hierarchy level | route from SITE directly to USER | compute −; precision − | none | negative | routing precision | E3b in the main report | MEASURED in v1: each level carries routing precision |
| H20 | evidence reconstruction | Hybrid timing for the flat controls with per-user object pools | keep per-user objects in A2/B4/Y, charge the context, pass hybrid timing | unknown (on the current one-object-per-pair pools hybrid is a no-op by construction) | none | +context | — | paired on A2/B4/Y | NOT RUN (queue #6, rewritten after review) |
| H21 | temporal/causal reasoning | Cluster-aware staleness gate: only a positive with ≥ 2 independent witnesses counts as the chain's assertion time | ops.synthesize stale_rule='strong'; one routine positive after a retraction no longer revives a stale chain | D5 down, found ±0 | none | zero | a genuinely re-asserted chain with single-witness positives is dropped | quick_stale_cal (cal), quick_strong_fresh (seeds 10-14) | REJECTED: cal seeds +0.058 found / D5 −0.07 did not replicate on the fresh panel (seeds 10–14): frozen ranker found −0.035 (0/3 better) with D5 −0.12; refitted ranker found +0.02 (noise) with D5 +0.02 — it trades found for D5 rather than fixing the mechanism |

## 2. The five highest-information experiments (chosen, and what they said)

1. **Ranker on vs off, hand gate count preserved, four architectures** — distinguishes "the register cut is ordering" from "the register cut is the gate". Result: ordering; every architecture gained.
2. **Question budget sweep with the fixed ranker** — distinguishes "the budget was starved" from "the budget was right and widening only floods". Result: starved up to 0.65, flooding beyond.
3. **Unbounded register + full budget (diagnostic only)** — the ceiling the pool allows (0.785 at 10k with budget 0.65); says how much of the gap is ordering versus evidence. Result: at 10k almost all ordering.
4. **Link timing: modal vs hybrid, calibration then held-out** — distinguishes "gold is lost to timing contamination" from "gold is lost to the order test being too tight". Result: contamination (hybrid helps; modal, which also fixes contamination but keeps one time per link, does not replicate).
5. **Old vs new at 50k with re-extraction and budget arms** — distinguishes "50k is a register problem" from "50k is a coverage problem". Result: coverage.

## 3. Ranked queue

| rank | experiment | what it distinguishes | expected information | cost | status |
|---:|---|---|---|---|---|
| 1 | Register-forecast question budget at 50k (ask in gain order by distinct anchor until the forecast candidate count reaches α × cap) with descent fan-out 1 | whether 50k coverage is limited by the number of anchors asked (breadth) or by reach per anchor (depth) | high: coverage is the binding 50k stage and the two explanations imply opposite spending | ~10 paired 50k runs | not run |
| 2 | D5 stale-chain mechanism: a staleness gate that compares the retraction against the link's assertion cluster (not just "strong positives only", which was tried) | whether the D5 rise under hybrid timing is the gate or the timing itself | high: D5 rose 0.26 → 0.78 across the accepted changes | cal screen + refit + fresh-panel read | the "strong positives only" variant was screened (+0.058 found on calibration seeds) and REJECTED on the fresh panel (found −0.035 with the frozen ranker, D5 −0.12; refit +0.02 / +0.02): it trades found for D5. The cluster-aware variant is still open |
| 3 | Kernel read chunked at the tier's context limit (as A2 already is), or an explicit degradation model | whether the vNext gain survives a kernel that cannot read a 1.4M/3.3M-token pool in one call | high: precondition of any deployment claim | code + paired runs at both scales | not run |
| 4 | Fresh evaluation panel (seeds 10–14 at 10k, 5–7 at 50k) read once | whether the development-panel deltas hold; this rerun's seeds 5–9 at 10k are the first such read | high for the small deltas | one suite run | seeds 5–9 done in this rerun |
| 5 | Sketch-corroboration feature for the ranker (foreign-site bit for each link's (entity, predicate)) | whether the ranker's remaining 10k ordering loss is missing information or missing capacity | high: offline AUC 0.74–0.77 for the feature; a null result says capacity | dump + refit + 5 paired runs | not run |
| 6 | Per-user object pools for A2/B4/Y with hybrid timing, context charged | whether the timing gain is the topology's or the controls' object builder's | medium: the panel's item was a null by construction | code + paired runs | not run |
| 7 | Lag features from the DP's chosen times; ranker applied once after enrichment; full-list dumps for the controls; then refit | whether the ranker hygiene defects cost anything | medium | refit + 5 paired runs | not run |
| 8 | Pairwise / top-K ranking objective on the same features | same question as 5 from the objective side | medium | refit only | not run |
| 9 | Budget-aware support-1 sketch bits (admit weak bits only into unused question budget) | whether rare patterns are lost at the sketch or at the budget | medium: rare recall is 0.33–0.42 and the weak-bit screen flooded triage | 5 paired runs | screen failed as implemented |
| 10 | Chain-scoped questions with a second untargeted round for thin answers | whether the lean arm's coverage loss can be bought back for less than the 47% compute it saves | medium | 5 paired runs | not run |
| 11 | Descent-evidence merge per polarity with a refitted ranker | compute −54% claim vs the found loss seen on one seed | medium | dump + refit + paired | one-seed screen only |
| 12 | Live model discrimination on kernel-side candidates (frontier vs small model) | whether the simulated kernel tier understates or overstates what a real model does with the same evidence | high for external validity, no effect on the simulator numbers | API budget | live harness exists |


## 4. Benchmark plan (as executed)

- Worlds: the benchmark's evaluation seeds 0–4 at 2k/10k and 0–4 at 50k for the headline suite; 0–2 for the sweeps; calibration seeds 500–502 for every choice.
- Controls on identical worlds: A_flat_rag, A2_chunked_ctx, B_long_context, B2_map_reduce, B4_central_triage, C_recursive_sum, the hierarchy family D–J, H_mycelic_prev (v1), H_mycelic_lean, the oracles Y/Z/Z2.
- Every change is a paired run first (`quick_paired` / `arm_paired`), then the suite.
- Ranker adoption per architecture on the calibration seeds; the hand-set score is a ranker too.
- Register cap, metrics, gold and worlds untouched; the unbounded register is used only as a diagnostic.

| change | scale | seeds | Δ found | Δ rare | Δ cov. | Δ decoy all | Δ D1 | Δ D2 | Δ D3 | Δ D5 stale | compute | calls (metering) | status |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ranker v1: learned order inside the hand gate | 10,000 | eval 0-4 | +0.065 (5/5 better) | -0.048 | -0.005 | +0.065 | +0.000 | +0.060 | +0.120 | +0.080 | -1% | -0% | superseded |
| ranker v2: learned top-K, anchor-context features | 10,000 | eval 0-4 | +0.105 (5/5 better) | +0.038 | +0.000 | +0.095 | +0.000 | +0.040 | +0.100 | +0.240 | -1% | -0% | superseded |
| ranker v3 + question_frac 0.65 + batched descent | 10,000 | eval 0-4 | +0.210 (5/5 better) | +0.175 | +0.280 | +0.150 | +0.000 | +0.000 | +0.200 | +0.400 | +25% | -71% | ACCEPTED |
| decoy-weighted ranker selection | 10,000 | eval 0-4 | -0.060 (1/5 better) | -0.118 | +0.000 | -0.050 | +0.020 | -0.040 | +0.020 | -0.200 | -0% | -0% | rejected |
| question_frac 0.80 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/4 better) | -0.022 | +0.015 | +0.015 | +0.000 | +0.020 | +0.000 | +0.040 | +13% | +1% | rejected |
| question_frac 1.00 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/3 better) | -0.005 | +0.015 | -0.015 | +0.000 | +0.020 | -0.020 | -0.060 | +31% | +1% | rejected |
| local re-extraction at the user (vs qf 0.65 batched) | 10,000 | eval 0-4 | -0.005 (3/5 better) | -0.042 | +0.000 | -0.020 | +0.020 | +0.040 | -0.020 | -0.120 | +1% | +0% | rejected |
| batched descent metering alone (ranker v2, qf 0.65; same decisions) | 10,000 | eval 0-4 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -17% | -84% | ACCEPTED |
| modal link timing (+ refitted ranker) | 10,000 | eval 0-4 | -0.010 (2/5 better) | -0.058 | -0.005 | +0.010 | +0.020 | -0.040 | -0.000 | +0.060 | +0% | -0% | rejected |
| hybrid link timing (+ refitted ranker) | 10,000 | eval 0-4 | +0.040 (2/2 better) | -0.015 | +0.010 | +0.030 | -0.020 | +0.020 | +0.000 | +0.120 | +1% | +1% | ACCEPTED |
| hybrid + chain-scoped questions (H_mycelic_lean) | 10,000 | eval 0-4 | +0.025 (3/4 better) | -0.071 | -0.170 | +0.045 | +0.020 | +0.200 | -0.100 | +0.060 | -47% | -1% | Pareto arm |
| ranker v3 + qf 0.65 + batched, 50k | 50,000 | eval 0-2 | +0.163 (3/3 better) | +0.100 | +0.273 | +0.137 | +0.053 | +0.107 | +0.093 | +0.293 | +32% | -61% | ACCEPTED |
| question_frac 0.80 at 50k | 50,000 | eval 0-2 | +0.007 (2/3 better) | -0.032 | +0.047 | +0.033 | +0.013 | +0.093 | +0.027 | -0.000 | +12% | +2% | rejected |
| local re-extraction at 50k | 50,000 | eval 0-1 | +0.007 (1/3 better) | -0.025 | -0.003 | +0.040 | +0.013 | +0.067 | +0.027 | +0.053 | +1% | -0% | rejected |
| hybrid link timing at 50k (+ refitted ranker) | 50,000 | eval 0-2 | +0.060 (3/3 better) | +0.009 | +0.003 | +0.040 | +0.013 | +0.013 | +0.000 | +0.133 | -0% | -0% | validation |
| hybrid + chain-scoped questions at 50k | 50,000 | eval 0-2 | -0.003 (1/3 better) | -0.072 | -0.143 | +0.007 | +0.000 | +0.013 | -0.013 | +0.027 | -31% | -1% | Pareto arm |
| v1 hierarchy re-metered with batched descent (identical decisions) | 10,000 | eval 0-4 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -12% | -73% | metering control |
| v1 hierarchy re-metered with batched descent at 50k | 50,000 | eval 0-2 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -12% | -65% | metering control |
| unbounded register (DIAGNOSTIC ONLY, not a fix) | 10,000 | eval 0-4 | +0.165 (5/5 better) | +0.127 | +0.000 | +0.150 | +0.040 | +0.180 | +0.100 | +0.280 | +7% | +0% | diagnostic |
| unbounded register + full question budget (DIAGNOSTIC) | 10,000 | eval 0-4 | +0.420 (5/5 better) | +0.482 | +0.295 | +0.295 | +0.120 | +0.200 | +0.440 | +0.420 | +117% | +143% | diagnostic |
| chain-scoped questions alone (calibration seeds) | 10,000 | cal 500-502 | +0.008 (2/3 better) | -0.066 | -0.175 | -0.008 | +0.067 | +0.033 | -0.100 | -0.033 | -48% | -1% | screen |
| cluster-aware staleness gate (strong positives only), calibration seeds | 10,000 | cal 500-502 | +0.058 (2/2 better) | +0.019 | -0.008 | -0.042 | +0.000 | -0.033 | -0.067 | -0.067 | -0% | +0% | screen |
| strong staleness gate, frozen ranker, FRESH panel | 10,000 | eval 10-14 | -0.035 (0/3 better) | -0.039 | +0.000 | -0.040 | -0.040 | -0.020 | +0.020 | -0.120 | +0% | +0% | fresh-panel read |
| strong staleness gate + refitted ranker, FRESH panel | 10,000 | eval 10-14 | +0.020 (2/4 better) | +0.017 | +0.010 | -0.020 | -0.060 | -0.080 | +0.040 | +0.020 | +0% | +0% | fresh-panel read |

<div style="page-break-after: always"></div>

# Mycelic vNext — loss accounting (old vs new)

_The full per-pattern accounting, with the stage definitions, the sketch-failure taxonomy, the witness curve and the rank tables, is `docs/MYCELIC_LOSS_ACCOUNTING.md` (regenerated on the new artifacts). This file puts the old and new funnels side by side for the hierarchy._

Stages (each row is a gold pattern; a stage is passed only if every earlier stage was): extracted → sketch_visible → in_triage → questioned → descent_reached → in_pool → candidate → matched_any → matched_tau → in_register. `loss_stage` is the terminal loss (first failing stage after the last passing one).

## 10,000

| stage | OLD all | NEW all | OLD rare | NEW rare | OLD common | NEW common |
|---|---:|---:|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0.960 | 0.960 | 0.897 | 0.897 | 1.000 | 1.000 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 0.985 | 0.985 | 0.974 | 0.974 | 0.992 | 0.992 |
| on the triage candidate list | 0.985 | 0.985 | 0.974 | 0.974 | 0.992 | 0.992 |
| inside the question budget | 0.635 | 0.975 | 0.436 | 0.962 | 0.762 | 0.984 |
| descent reached a facet holder | 0.665 | 0.940 | 0.462 | 0.885 | 0.795 | 0.975 |
| >=2 gold links in kernel pool (= evidence coverage) | 0.690 | 0.980 | 0.487 | 0.962 | 0.820 | 0.992 |
| candidate formed on right entity + chain | 0.595 | 0.900 | 0.397 | 0.872 | 0.721 | 0.918 |
| matched primary rule at ANY confidence | 0.540 | 0.890 | 0.346 | 0.872 | 0.664 | 0.902 |
| matched with confidence >= 0.5 | 0.515 | 0.885 | 0.333 | 0.859 | 0.631 | 0.902 |
| survived the register cut (= reported) | 0.375 | 0.625 | 0.205 | 0.372 | 0.484 | 0.787 |
| patterns | 200 | 200 | 78 | 78 | 122 | 122 |

### Where they died

| the pattern died at | OLD | NEW | OLD rare | NEW rare |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0 (0%) | 0 (0%) | 0 | 0 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 3 (2%) | 3 (2%) | 2 | 2 |
| on the triage candidate list | 0 (0%) | 0 (0%) | 0 | 0 |
| inside the question budget | 58 (29%) | 0 (0%) | 37 | 0 |
| descent reached a facet holder | 0 (0%) | 1 (0%) | 0 | 1 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 17 (8%) | 15 (8%) | 7 | 6 |
| matched primary rule at ANY confidence | 14 (7%) | 3 (2%) | 5 | 1 |
| matched, but confidence < 0.5 | 5 (2%) | 1 (0%) | 1 | 1 |
| cut from the register (matched at >= 0.5, out-ranked) | 28 (14%) | 52 (26%) | 10 | 38 |
| **reported** | 75 (38%) | 125 (62%) | 16 | 29 |

## 50,000

| stage | OLD all | NEW all | OLD rare | NEW rare | OLD common | NEW common |
|---|---:|---:|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0.960 | 0.960 | 0.893 | 0.893 | 1.000 | 1.000 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 0.780 | 0.780 | 0.562 | 0.562 | 0.910 | 0.910 |
| on the triage candidate list | 0.780 | 0.780 | 0.562 | 0.562 | 0.910 | 0.910 |
| inside the question budget | 0.427 | 0.710 | 0.304 | 0.500 | 0.500 | 0.835 |
| descent reached a facet holder | 0.427 | 0.703 | 0.268 | 0.482 | 0.521 | 0.835 |
| >=2 gold links in kernel pool (= evidence coverage) | 0.450 | 0.727 | 0.312 | 0.527 | 0.532 | 0.846 |
| candidate formed on right entity + chain | 0.397 | 0.677 | 0.259 | 0.509 | 0.479 | 0.777 |
| matched primary rule at ANY confidence | 0.363 | 0.657 | 0.232 | 0.491 | 0.441 | 0.755 |
| matched with confidence >= 0.5 | 0.363 | 0.657 | 0.232 | 0.491 | 0.441 | 0.755 |
| survived the register cut (= reported) | 0.363 | 0.587 | 0.232 | 0.339 | 0.441 | 0.734 |
| patterns | 300 | 300 | 112 | 112 | 188 | 188 |

### Where they died

| the pattern died at | OLD | NEW | OLD rare | NEW rare |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 5 (2%) | 5 (2%) | 5 | 5 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 60 (20%) | 58 (19%) | 43 | 41 |
| on the triage candidate list | 0 (0%) | 0 (0%) | 0 | 0 |
| inside the question budget | 100 (33%) | 17 (6%) | 29 | 6 |
| descent reached a facet holder | 0 (0%) | 0 (0%) | 0 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 15 (5%) | 17 (6%) | 6 | 3 |
| matched primary rule at ANY confidence | 11 (4%) | 6 (2%) | 3 | 2 |
| matched, but confidence < 0.5 | 0 (0%) | 0 (0%) | 0 | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 0 (0%) | 21 (7%) | 0 | 17 |
| **reported** | 109 (36%) | 176 (59%) | 26 | 38 |

## Reading

The pack's eight loss categories map onto the columns as: never represented in sketches = ¬sketch_visible; filtered from knowledge objects = extracted but ¬in_pool after reach; not selected for questioning = ¬questioned; descent routing miss = ¬descent_reached; local extraction miss = ¬extracted; evidence at kernel but no candidate = in_pool ∧ ¬candidate; candidate rejected by verification = candidate ∧ ¬matched_tau; ranking buried it = matched_tau ∧ ¬in_register.


<div style="page-break-after: always"></div>

# Mycelic vNext — adversarial review

_Written to falsify the claims in `NEXT_RESEARCH_REPORT.md`. Each attack is followed by what the artifacts say and what was done about it. Where an attack lands, it says so._

## Attack 1 — the gains are tuned on the evaluation seeds

The calibration seeds are 500–502; the evaluation seeds are 0–4 (10k) and 0–2 (50k). Every knob in the frozen configuration carries the tag of the paired file that confirmed it (`frozen_config_table` in the report). Two candidates that won on the calibration seeds lost on the evaluation seeds and were dropped, which is the protocol working, not a coincidence. **Where it lands**: the evaluation seeds were read more than once during this work (each accepted change was read on them). That is sequential testing on a fixed panel; the report's numbers are therefore optimistic by an amount five paired seeds cannot bound. Mitigation offered: the first item under *What would change my mind* is a fresh seed panel.

## Attack 2 — the ranker is a simulator artefact

Its features are exact quantities the simulator hands the kernel (witness signatures, lag, lineage). A real kernel would estimate them from claims with noise. The live-model harness in the main report measures the discrimination a frontier model achieves from raw notes; it does not yet measure discrimination from kernel-side claims. **Lands partly**: the *size* of the ranker gain is a simulator number; the *direction* (ordering was the binding stage) rests on the funnel, which counts gold patterns, not on the ranker.

## Attack 3 — the baselines were handicapped

The centralised controls adopt the ranker only where their own calibration says it is not worse; A2 adopted it and went from 0.685 to 0.740 at 10k. A_flat_rag kept the hand score by the same rule. The old A2 rows are in the OLD column of the same tables. **Does not land** on the comparison; it does mean the "gap to A2" moved less than the hierarchy's own gain.

## Attack 4 — metric gaming

Register cap: unchanged (`min(6000, max(600, n_entities))`); the unbounded register appears only in rows labelled diagnostic. Gold, worlds, seeds, metrics: unchanged (the funnel code was verified by three independent refuters before the work started, and the corrections it needed are in the research log). Question budget: a fraction of the triage queue, not of the number of gold patterns. **Does not land.**

## Attack 5 — hidden centralisation

Nothing new moves up and raw text leaving a node is 0.0. But the claim-exposure fraction that reads *same* in the paired tables counts only the upward pass: descent returns per-user claim objects to the kernel and the wider budget roughly doubles them (about 30k → 53k objects at 10k, 61k → 127k at 50k), which is also where most of the extra compute goes. The ranker's anchor-context features are counts over candidates the kernel already holds; re-extraction, the one change that touched raw records, stayed local and was rejected. **Lands partly**: the counter is blind to the channel the accepted change widened, and the privacy sections now say so.

## Attack 6 — the decoy regression is being minimised

It is not: decoy acceptance at 10k went from 0.180 to 0.360 and the one fix tried (decoy-weighted selection) was rejected because it cost found and rare recall. FDR fell slightly. **Lands**: a register with more true patterns and more decoys is a better register only if the reader's cost of a decoy is below the value of a pattern; the report does not claim otherwise.

## Attack 7 — the hierarchy still loses to centralised discovery

Yes: 0.566 vs 0.784 at 50k, at 0.38× of A2's compute. The brief's targets (0.70 / 0.60) were not met. The report says which stage holds the rest (ordering at 10k, coverage at 50k) and what would test it. **Lands.**

## Attack 8 — single-seed screens are being cited

Two negative results (weak sketch bits, descent-evidence merge) rest on one calibration seed. They are cited as screens that stopped further spending, not as findings, and they are not in the ledger table. **Lands on wording, addressed.**

## Attack 9 — the hybrid DP leaks decoys

Letting a link float across its clusters relaxes the order test for single-witness links. The scrambled-time family D2 is flat (+0.02 at 10k, +0.01 at 50k), but the stale-chain family D5 rises under the adopted arm on seven of eight held-out seeds (+0.12 at 10k, +0.13 at 50k; 0.26 → 0.78 across all the accepted changes at 10k). The independent review traced it to the staleness gate, which one routine positive mention after a retraction defeats and which min timing was masking by accident. **Lands**: the 'improved decoy resistance' claim is withdrawn, all four families are in every table, and a cluster-aware gate is queue item 2.

## Attack 10 — the compute comparison mixes metering conventions

It did: the v1 base was metered unbatched and the new arm batched; batching alone is −17% compute and −84% calls at identical decisions. **Lands**: the v1 base was re-metered batched on the same worlds and the report gives both conventions; the call reduction is labelled as metering.

## Attack 11 — the kernel prompt exceeds the modelled context

1.3–1.5M tokens at 10k and 3.2–3.4M at 50k in one call against a 1M context that the simulator enforces only for the flat controls; the report's kernel-context metric showed the first stage only. **Lands**: stated in section 5b of the report, printed in the tables, chunking queued as a precondition of any deployment claim.

## Revision after the review

- Kept: the three accepted changes and the lean arm as a labelled Pareto point.
- Reworded: the single-seed screens; the decoy regression is stated in the first paragraph of the report.
- Added to the queue: a fresh evaluation panel (seeds 5–9) before any of the reported deltas is quoted outside this repository.

## Independent review

_Three refuters were run as separate agents with read-only access to the repository, each told to falsify one group of claims (protocol and leakage; the hybrid DP and decoys; metrics, cap and funnel integrity). Their reports follow verbatim, each with the author's response._

### Refuter 1 — protocol and leakage (verbatim)

**Claim 1 — the stored ranker was fitted only on seeds 500–502.** Two of the three source dumps contain evaluation-seed rows (`hyp_features_v3C.jsonl`: 5,400 cal + 9,000 eval; `hyp_features_J.jsonl`: 8,810 cal + 15,335 eval; `hyp_features_hybH.jsonl`: 9,404 rows, all cal). `fit_and_store` keeps only `r["seed"] in CAL_SEEDS`; `_loso_found` trains and tests inside the calibration seeds. 9,404 + 5,400 + 8,810 = 23,614 = the stored `n_train`. The stored weights equal `calibration.hyb.json`'s. The fit (10:55) preceded the first evaluation read of hybrid (10:57). Caveats, not leakage: `hyp_features_J.jsonl` is the same candidate set as `hyp_features_v3H.jsonl` (identical features; only `conf` differs on 881 of 24,145 rows), so 8,810 of the training rows are min-timing hierarchy candidates relabelled "J", and "refitted under the pipeline it will run in" holds for the 9,404 hybH rows (40%). **Verdict: SURVIVES.** Severity low. Fix: write the dump's `cfg_over`/`link_time` into every row and into the ranker provenance; drop or relabel the J duplicate.

**Claim 2 — every frozen knob was chosen on calibration seeds and only confirmed on evaluation seeds.** `question_frac = 0.65` was chosen on the evaluation seeds, twice: the only calibration-seed budget sweep is the v1 grid over (0.25, 0.55, 1.0) with the hand ranker, which chose 0.25; under a learned ranker the budget was swept only on seeds 0–4 (`quick_rk2_qf*.jsonl`: 0.510 / 0.520 / 0.510 / 0.515; `quick_qf_v3.jsonl`: 0.555 / 0.585 / 0.565 / 0.565), and the 50k sweep is on evaluation seeds 0–2. `local_reextract = False` was decided on the evaluation seeds against the single calibration-seed screen (+0.125). `link_time = hybrid` is a calibration-consistent choice with an evaluation-seed accept gate (modal rejected there, hybrid kept; hybrid's evaluation delta `[0, +0.125, 0, +0.075, 0]`). `batched_descent` is pure metering (identical per-seed found). The ranker design was iterated v1 → v2 → v3 with an evaluation-seed A/B after each; only its hyperparameters were leave-one-seed-out on calibration seeds. The provenance column in `vnext_data.py` emits a literal "calibration seeds 500–502, confirmed on held-out 0–4" for every knob, and `report_text.py` says every knob is fitted on calibration seeds — false for `question_frac` and `local_reextract`. **Verdict: REFUTED** (for those two knobs; partially for `link_time`). Severity high for the written claim, medium for the numbers (the 0.65 margin is 0.02–0.03 found, about one winner's curse). Fix: derive the "chosen on" column from the seeds in the cited evidence file; relabel or re-run the budget sweep on seeds 500–502 under the frozen ranker.

**Claim 3 — sequential testing on a fixed panel.** 86 arm-reads on the evaluation seeds across 33 `quick_*.jsonl` files: about 40 configurations at 10k on seeds 0–4 and 17 arms at 50k on seeds 0–2; about 11 accept/reject decisions at 10k and 6 at 50k. Report §6's "read once on seeds 0–4" is not what the files show. Median sd of a paired found delta over 5 seeds = 0.045, so the standard error of a paired mean is 0.020; max-of-k under a null: k=4 → +0.021, k=8 → +0.029, k=12 → +0.033, k=20 → +0.038 found. The small accepted deltas (budget +0.030, hybrid +0.040, lean +0.025) are 1–2 standard errors and at or below the winner's curse; the large one (+0.210, per seed `[0.175, 0.200, 0.225, 0.175, 0.275]`, se 0.019) is more than 10 standard errors and survives any correction. Expect 0.625 to read about 0.58–0.60 on a fresh panel and 0.587 about 0.55. No quick file, dump or funnel row ever touched seeds 5–9 at 10k, and `final_rerun.sh` runs `e1c` (seeds 5–9) — but it appends into `e1_baselines.jsonl`, so the report would pool 0–9 into one mean. **Verdict: REFUTED** as a "read once" claim. Severity medium. Fix: report seeds 5–9 at 10k as a separate confirmation panel and label 0–4 as the development panel.

**Claim 4 — per-architecture adoption is decided on calibration seeds.** `select_archs` defaults to `CAL_SEEDS`; the adoption log's values are multiples of 1/120 (3 seeds × 40 gold). No code path passes evaluation seeds; `H_mycelic_prev` is excluded; B2 adopts on an exact tie; A_flat_rag flipped to adopt under the refitted ranker. **Verdict: SURVIVES.** Severity low. Fix: write the seeds and per-seed found into `ranker_arch_table`.

**Claim 5 — the A/B base rows had the ranker OFF.** `quick_v3_H.jsonl` base rows equal the archived v1 `e1_baselines.jsonl` rows for H at 10k seeds 0–4 on all 12 metrics compared (found 0.300/0.300/0.425/0.475/0.375, compute 1042841.82, calls 86616, 220 questions); the v1 rows predate the ranker, so the base is provably hand-ranked and the vNext code changes did not alter v1 behaviour. The variant rows match the `qf0.65` arm that forces the ranker on. `quick_paired` rows carry no `ranker` key, so ON/OFF was inferred, not logged. **Verdict: SURVIVES.** Severity low. Fix: record the ranker state in every quick row.

**What a careful reader should believe (refuter's summary).** The ranker fit and the adoption are clean. The frozen-config "chosen on calibration seeds" column was hardcoded text; `question_frac` and `local_reextract` were decided on the evaluation seeds, and the hybrid/modal gate was evaluation-seed too. Seeds 0–4 were read about 86 times over about 40 configurations with about 11 decisions; the winner's curse is about +0.02–0.04 found, so the +0.03 (budget) and +0.04 (hybrid) gains are not established. The +0.21 at 10k and +0.16 at 50k survive any selection correction on these worlds, in this simulator. Treat 0.625 / 0.587 as development-panel numbers; seeds 5–9 at 10k should be reported separately as the confirmation.

**Author's response.** Accepted in full. Changes made: (1) the provenance column is now derived from the seeds in each cited evidence file, so a knob whose only evidence is on the evaluation panel says so; (2) the research report reports seeds 5–9 at 10k as a separate confirmation panel (OLD rows for them exist in the archive; no development decision read them) and labels 0–4 as the development panel; (3) §6 of the report is rewritten around the refuter's counts and the winner's-curse estimate; (4) a budget sweep on the calibration seeds under the frozen ranker and timing was run (`quick_qf_cal.jsonl`) and cited next to the evaluation-seed sweeps; (5) `quick_paired` rows now record the ranker state; (6) the `report_text.py` sentence is corrected. The J-dump duplication and the missing per-row `cfg_over` in dumps are noted as fixes for the next refit, not applied to the frozen ranker (which would change the numbers under review).

**Outcome of the calibration-seed budget sweep the refuter asked for** (`quick_qf_cal.jsonl`, seeds 500–502, frozen ranker and hybrid timing): found 0.617 / 0.600 / 0.625 / 0.592 at question_frac 0.50 / 0.65 / 0.80 / 1.00, rare recall 0.343 / 0.364 / 0.364 / 0.282, evidence coverage 0.850 / 0.925 / 0.958 / 0.958, compute 1.22e6 / 1.38e6 / 1.58e6 / 1.85e6. The calibration seeds do not single out 0.65: the budget is flat between 0.50 and 0.80 within the per-seed spread, and 1.00 loses both found and rare recall. The frozen value is therefore described as "inside the calibration-seed plateau, chosen on the development panel", and the budget's contribution is left to the confirmation panel.

### Refuter 2 — the hybrid DP and the decoys (verbatim, lightly condensed)

**Attack 1 — correctness of the hybrid state DP.** States are appended in link order and a transition requires a strictly earlier link, so two states of one link never chain and chain order is strictly increasing; the time slack is the same as the min-timing DP; `clusters` is built from `on` after the chain sort and the strong-link filter and indexed by the same enumeration, so it is aligned. Fuzz: 400 random instances against brute-force enumeration, 0 violations, 0 weight mismatches. **Defect found: the DP's chosen times are discarded.** `_mk` computes every lag feature (`link_lag`, `min_lag`, `max_lag`, `mean_lag`, `neg_lag`, `lag_cv`, `tspan`) from min timing regardless of `link_time`, so a chain admitted under hybrid carries lag features describing a timing under which it would not have been admitted. In the calibration-seed dumps the share of verified candidates with `min_lag < -3` is 1.1–1.3% under v3 and 50–58% under hybrid; `min_lag`'s ranker weight went from +0.299 to −0.023. Secondary: `_time_clusters` treats a knowledge object's `tmin` as the time of all its signatures, so a site-merged upward object forms a "≥2 agreeing witnesses" cluster at the routine time (hybrid ≡ min for that link); the "witnesses agree" rationale holds only for per-user descent objects. **Verdict:** DP SURVIVES; feature/timing consistency REFUTED; severity medium. Fix: store the DP's per-link time on the hypothesis and compute the lag features from it; restrict clustering to user-level objects.

**Attack 2 — decoy leak, per family (paired, same seeds).** Adopted arm vs v3 base: D1 −0.02 (10k) / +0.013 (50k); D2 +0.02 / +0.013; D3 0.00 / 0.00; **D5 stale chain +0.12 (0.66→0.78, per seed +.1 +.2 +.1 +.2 0) at 10k and +0.133 (0.627→0.760, +.12 +.16 +.12) at 50k** — up on 7 of 8 held-out seeds, flat on 1, down on 0 (one-sided sign p ≈ 0.008). In counts: 10k +1.6 gold/seed vs +1.2 decoys/seed; 50k +6 gold vs +4 decoys — about 75% of the found gain. With the v3 ranker (`hyb_v3rk`) decoy_all is −0.01 and D5 mixed; on the calibration seeds D5 fell −0.167 on 3/3, so "lifts decoy resistance" was a calibration-seed artefact for D5 and the leak is concentrated in the adopted refitted-ranker arm. Mechanism: D5 links have 3–9 witnesses; the staleness gate is `max(neg_tmax) > max(pos_tmax)` over all member objects, so one routine positive mention after the retraction defeats it (50% of D5 links have one); min timing often knocked that link out of the path by accident, hybrid restores it. The docs point readers to D2, which is flat; D5 is not reported anywhere. Lean arm: D2 +0.20 against found +0.025, confounded with `span2`. **Verdict:** "decoy-safe" REFUTED for D5; the found gain survives; severity medium. Fix: a cluster-aware staleness gate (compare the retraction cluster against the link's assertion cluster, or require the late positive to have ≥2 independent signatures); report per-family decoys; pre-register D5 on the fresh panel.

**Attack 3 — is min() biased early?** Background record times are uniform on [0,180) (mean 86) except the 6% stale slice on [0,60); gold facets average 65 — background is later than gold on average, not earlier. The early pull is an order statistic: a gold link's (predicate, entity) shares on average 3.1 routine mentions (private entity) / 9.2 (global); P(at least one earlier than the previous link − 3) = 0.47 / 0.77; rare gold 0.63; D2 0.52; D5 0.38. For private entities 369 of 394 routine co-mentions sit at the home site the descent avoids and reach the kernel only inside the site-merged upward object, where hybrid dates the link at the routine time anyway; hybrid differs from min mainly on descent-returned per-user objects. The "20–22 of 25 in-pool gold patterns" figure comes from the panel's replay whose code is not in the repository. Hybrid is not simply a looser order test: it is tighter for strong links (constructed cases where min accepts and hybrid rejects) and looser only for single-witness links. **Verdict:** mechanism SURVIVES in kind, UNRESOLVED in magnitude; the causal story ("routine mentions are stale or early") is inaccurate. Fix: state the order-statistic cause; dump per-link chosen times in the funnel; note the merged-object limitation.

**Attack 4 — fairness to the flat controls.** No flat call site passes `link_time`, so A2, Y, B4, flat RAG and map-reduce use min timing; but `_flat_kos` collapses every (predicate, entity) to one object with tmin = min and signatures = union, on which hybrid is exactly a no-op. Queue item 6 as written would return a null guaranteed by the object builder, not by the topology; the controls hold per-record times and could keep per-user objects. Bounded: the controls already out-score the hierarchy on found (A2 0.765 vs H 0.625 at 10k), so the headline is not overturned. **Verdict:** "genuine property of the topology" REFUTED; severity medium (narrative). Fix: give A2/B4/Y per-user object pools, charge the extra context, pass hybrid timing, and rewrite item 6.

**Attack 5 — privacy.** Hybrid reads only `tmin` and `sigs`, fields of the claim message; no field was added; every exposure counter's delta is exactly 0 on all seeds. **Verdict: SURVIVES.**

**Author's response.** Accepted. (1) D5 is now in every ledger and old-vs-new table and the report states "found +0.04/+0.06, D5 +0.12/+0.13, other families flat" instead of "decoy resistance"; (2) the mechanism paragraph is rewritten as an order-statistic effect, two-sided, with the merged-object limitation and the unverifiable replay figure flagged; (3) the lag features computed from min timing under hybrid are recorded as a defect to fix before the next refit (not changed now, since it would alter the frozen ranker under review); (4) queue item 6 is rewritten (per-user object pools for the controls, context charged) and a cluster-aware staleness gate is added to the queue as the D5 fix, with a calibration-seed screen if time allows; (5) attack 9 in the review points to D5, not D2.

### Refuter 3 — metrics, cap, batching, centralisation, compute, funnel (verbatim, lightly condensed)

**Attack 1 — register cap.** `n_candidates` is 600 (10k) / 2999 (50k) in every row of every accepted-change and control file; only the diagnostic files with `max_reports: 100000` differ. `n_reported` is not identical (10k 592 → 598; 50k 2881 → 2998) because under the ranker every register entry is ≥ τ while the hand score leaves 1–5% below; `found` ignores τ, so no effect on discovery, a ≤4% shift in the FDR denominator at 50k. **Verdict: REFUTED** (no cap change). Fix: print `n_candidates` next to `n_reported`.

**Attack 2 — gate artefact.** The hand gate k (candidates with hand_conf ≥ 0.5) is 1312–2946 in every arm, 2.2–4.9× the cap, so the register is the top-K by learned score regardless of k and `found` cannot rise through k; the budget change doubles register pressure (≈1550 → ≈3050 pre-cut candidates), making ordering harder. Two hygiene findings: the register cut happens inside `synthesize` before `_enrich` fills the five triage-context features (they are 0 at cut time, filled after; a constant feature shifts every candidate equally, so membership is decided by the other 40 features and those five affect only order within the register) — train/serve skew since the ranker was fitted on post-enrich dumps; and the dump records the pre-cut list for the hierarchy but only the post-cut 600 for the controls, so the shared fit never sees the controls' below-cut negatives. **Verdict: REFUTED**; severity low. Fix: apply the ranker once after enrichment and cut there; dump full lists for the flat systems.

**Attack 3 — batching changes only metering.** Four code sites, each `ledger.append` vs `meter.add`; routing choice, selection and pool extension outside the branch; `Tier.seconds` deterministic. Rows: unbatched vs batched at the same ranker and budget: found/AP/coverage/rare/decoy/R@100/n_reported/n_questions identical on 5/5 seeds; compute −17.2%, calls −83.5%, tokens saved per call saved = exactly 120 (the prompt overhead). **Verdict: REFUTED** for accuracy — but see attack 5.

**Attack 4 — hidden centralisation.** The three privacy counters are identical base vs variant on every seed — but `claims_leaving_node` counts only the upward pass and `propagated` only the upward pass and cross-links; the descent reads a user's full claim set on the entity, builds objects with lineage and record ids and appends them to the kernel pool, none of which is counted. The rows show it: `privacy_propagated_records` is 48,128 at budget 0.25, 0.65 and 1.0 alike while questions × new evidence goes 22,525 → 45,628 (10k) and 53,416 → 119,645 (50k); the kernel pool goes 30,304 → 53,410 objects at 10k (×1.76) and 60,808 → 127,277 at 50k (×2.09). Raw text stays local (nothing reads record text in the descent). Ranker features are functions of object fields and kernel aggregates; `attribution` is written by `_verify_evidence`, which re-reads raw records at the kernel, constant −1 in H but a real feature in the `_J` dump the shared ranker was trained on. Related bug: `exposed_raw_records = 0` is hard-coded for every hierarchy run including `J_mycelic_verified`, whose kernel reads up to 720 raw records. **Verdict: SURVIVES in part** — no new upward mechanism and no raw text, but the claim-exposure evidence is an artefact of the counter and the accepted change doubles claim objects at the kernel; severity medium (privacy framing). Fix: count descent-returned objects, report kernel-pool objects per run, fix J's raw-record count, reword the privacy sections.

**Attack 5 — compute accounting.** 10k: v1 base (hand, budget 0.25, unbatched) 1.0881e6 units / 91,042 calls → accepted 1.3608e6 (+25.1%) / 26,579 calls (−70.8%); the same accepted pipeline metered unbatched is 1.6315e6 (+49.9%) / 161,230 calls. Batching alone is −17.2% / −83.5%, so the headline +25% nets a real ≈+50% work increase against a metering convention applied only to the new arm; like-for-like batched-vs-batched is ≈+45%, "+0.084 found per +10% compute" is ≈+0.04, and "calls fall by about two thirds" is entirely metering. 50k has the same structure. Where the extra compute lands: the frontier-plus kernel re-reading a pool twice as large (its prompt 788k → 1,389k tokens at 10k, ≈+240k of the net +273k units). The kernel's single call is 1.31–1.54M tokens at 10k and 3.2–3.4M at 50k, above the tier's 1M context, which is enforced only for the flat baselines (A2 is chunked at 999,984); the hierarchy's kernel is never chunked and no degradation is modelled; v1 already exceeded it at 50k, the accepted change pushes 10k over for the first time; `kernel_context_tokens` reads only the first kernel stage (≈20k) so the report never shows the 1.4M/3.3M prompt. Unmetered work: ranker scoring, anchor-context annotation, clustering and the DP are CPU-only in both arms; kernel output tokens are charged for the 600 register entries, not the ≈3,050 candidates scored (≈+96k units if charged, same convention in both arms). **Verdict: SURVIVES**; severity medium-high for the efficiency framing. Fix: report both conventions or re-meter the v1 base batched; make `kernel_context_tokens` the max over stages; chunk the kernel read at the context limit or state the requirement.

**Attack 6 — funnel and matching under hybrid timing.** `_match_sets` matches on entity equality and predicate overlap; time is never consulted; the loss accounting uses the same function; hybrid only changes which sub-path the DP keeps, one candidate per (entity, chain) as before. Hybrid admits more paths (calibration dumps: +4–9% candidates, gold-matching rows 33/36/33 vs 26/31/29). D2 is flat; **D5 stale-chain acceptance rises with every accepted step: 0.26 → 0.66 (v3 bundle, 10k, 5/5 seeds), 0.66 → 0.78 (hybrid refit, 4/5 worse), 0.63 → 0.76 (hybrid at 50k, 3/3 worse)**; the review watches D2 and does not mention D5. **Verdict: REFUTED** on leniency; **UNRESOLVED** on D5. Fix: add the four decoy families to the ledger; trace D5 through the funnel under min vs hybrid before quoting hybrid as decoy-neutral.

**Author's response.** Accepted. (1) The v1 hierarchy was re-metered with batched descent on the same worlds (`quick_prev_batched.jsonl`, `quick_prev_batched50.jsonl`; identical decisions) and the compute comparison is now given on both conventions, with the like-for-like figure quoted in the report; the "calls" reduction is labelled as a metering convention. (2) The kernel prompt size (`max_context_tokens`) is printed in the old-vs-new and gap tables and the report states that the frozen configuration requires a 1.4M-token (10k) / 3.3M-token (50k) kernel context that the modelled tier does not have, enforced only for the flat controls; chunking the kernel read is added to the queue as a required experiment before any deployment claim. (3) The privacy sections now say: upward messages unchanged, raw text 0, but descent-returned claim objects at the kernel roughly double and the exposure counters do not observe them; the counter fix and J's raw-record count are listed as defects. (4) D5 and the other families are in every table. (5) The enrich-before-cut skew and the controls' post-cut dumps are listed as fixes for the next refit.

**Follow-up to refuter 2's D5 finding.** The "strong positives only" staleness gate was implemented (`stale_rule="strong"`), screened on the calibration seeds (found +0.058, D5 −0.07) and read once on a fresh panel (seeds 10–14): found −0.035 with the frozen ranker (D5 −0.12) and +0.02 inside noise with a refitted ranker (D5 +0.02). Rejected; the cluster-aware variant the refuter proposed (compare the retraction against the link's assertion cluster) remains open in the queue.


## What would change my mind

- If `H_mycelic_prev` inside the new suite does not reproduce the archived v1 rows to the third decimal, the old-vs-new comparison is contaminated by a code change and every Δ in section 1 is suspect.
- If the learned ranker's out-of-sample AUC in section 7 is not above the hand-set score's, the found gain is a gate artefact and should not survive a different register cap.
- If a fresh set of evaluation seeds (5–9) gives hybrid timing a negative paired delta, it joins modal timing in the rejected column.
- If A2 with the ranker beats the hierarchy at 50k at equal compute (it does not today: 10.2e6 vs 3.9e6 units), the compute argument for the hierarchy is gone and only the privacy argument remains.
- If queue item 1 shows 50k coverage is depth-limited, the lean arm's mechanism is the wrong direction and breadth spending should be reverted.
- If the live discrimination harness shows a frontier model extracting a signal from raw notes that no kernel-side feature carries, the ranker's ceiling is a property of the simulator, not of the design.
- If the stale-chain decoy family D5 keeps rising on a fresh panel after the staleness gate is made cluster-aware, hybrid timing is a relaxation of the temporal check and is withdrawn.
- If chunking the kernel read at the tier's 1M-token context removes the vNext gain, the gain was bought with an unmodelled context and the frozen configuration is not deployable as described.
- If counting descent-returned objects in the exposure metric moves the hierarchy's claim exposure to the level of the centralised triage control, the privacy argument for the hierarchy is the raw-text line alone.
