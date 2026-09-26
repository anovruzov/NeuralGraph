# Mycelic vNext — research report

_Generated 2026-09-26 03:55 UTC from `research/mycelic/artifacts/` (new) and `artifacts/v1/` (old) by `vnext_docs.py`. Every table is computed from those files._

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

On the confirmation panel (10k, seeds 5–9) the hierarchy's found rate goes from 0.395 to 0.570 (rare recall 0.220 → 0.348, A2 0.640 → 0.725). On the development panel (seeds 0–4) it goes from 0.375 to 0.625 (evidence coverage 0.690 → 0.980, rare recall 0.215 → 0.375). Compute: +26% against the v1 base as it was benchmarked (unbatched metering) and +44% against the same v1 decisions re-metered with batched descent, which is the like-for-like figure; the model-call reduction (-71%) is a metering convention, not fewer decisions. At 50k it goes from 0.336 to 0.566 (coverage 0.420 → 0.708) for +32% compute as benchmarked and +48% like-for-like. The centralised chunked-context control A2 also improved, because it adopts the same learned ranker (its calibration said the ranker was not worse for it): 0.685 → 0.740 at 10k. The remaining gap to A2 is +0.115 found at 10k on the development panel (+0.155 on the confirmation panel) and +0.218 at 50k, at 0.66× (development panel) and 0.38× of A2's compute respectively. Decoy acceptance rose with the ranker at 10k (0.180 → 0.360), and the stale-chain family D5 in particular (0.260 → 0.780); both are reported as regressions, not hidden. The one attempt to train the ranker away from decoys lost found and rare recall on the held-out seeds and was rejected; the D5 mechanism (a staleness gate one late routine mention defeats) is understood and its fix is queued. The frozen configuration also requires a kernel prompt of 1.4M tokens at 10k and 3.2M at 50k, above the modelled tier's 1M context (section 5b).

Targets from the brief: 10k found ≥ 0.70 (stretch 0.75), 50k ≥ 0.60 (stretch 0.70 while materially below A2 compute). Reached: 10k 0.570 on the confirmation panel (0.625 on the development panel; not met), 50k 0.566 (not met), the 50k figure at 0.38× of A2's compute. Section 5 says which stage holds the rest.

## 2. Against the centralised controls on the same NEW worlds

### 2.1 10,000 (seeds 0–9)

| system | found | evidence cov. | rare recall | AP | decoy acc. | D5 stale | compute | calls | kernel prompt (max tokens) | found gap to H | compute ratio to H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full | 0.597 | 0.955 | 0.361 | 0.180 | 0.395 | 0.800 | 1.38e+06 | 2.69e+04 | 1.41e+06 | +0.000 | 1.00x |
| A2_chunked_ctx | 0.733 | 1.000 | 0.622 | 0.214 | 0.475 | 0.700 | 2.07e+06 | 3.00e+00 | 1.00e+06 | +0.135 | 1.50x |
| B4_central_triage | 0.577 | 0.950 | 0.326 | 0.129 | 0.390 | 0.740 | 3.98e+05 | 1.00e+04 | 5.20e+05 | -0.020 | 0.29x |
| Y_oracle_retrieval | 0.417 | 1.000 | 0.171 | 0.023 | 0.295 | 0.520 | 4.87e+05 | 1.00e+04 | 7.61e+05 | -0.180 | 0.35x |
| H_mycelic_lean | 0.600 | 0.780 | 0.306 | 0.207 | 0.417 | 0.780 | 7.27e+05 | 2.65e+04 | 3.85e+05 | +0.003 | 0.52x |
| H_mycelic_prev | 0.385 | 0.677 | 0.218 | 0.029 | 0.205 | 0.280 | 1.11e+06 | 9.40e+04 | 8.14e+05 | -0.212 | 0.81x |

### 2.2 50,000 (seeds 0–4)

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

2. **Question budget 0.65 of the triage queue, with batched metering** (`systems.py`). With ordering fixed, the budget could be widened; the paired sweep on the development panel (0.50/0.65/0.80/1.00 at 10k on seeds 0–4; 0.65/0.80/1.00 at 50k on seeds 0–2) saturates at 0.65 at both scales (coverage reaches 0.97 at 10k; 0.80 and 1.00 buy nothing and lose AP); on the calibration seeds found is flat from 0.50 to 0.80 (section 6). Batching meters one routing call per node and one read per queried user per round, with identical decisions and per-record tokens, which is why calls fall by about two thirds while compute rises.

3. **Hybrid link timing in the temporal DP** (`ops.py`). A link was dated at the earliest mention of its (predicate, entity) pair. Background mentions are not earlier than gold on average (the independent review measured mean day 86 against 65); there are simply several of them per pair, and the earliest of three to nine draws precedes the previous link about half the time for private entities and three quarters of the time for global ones, so the chain-order test fails. Under hybrid timing a link is dated at its heaviest witness cluster and a single-witness link becomes one DP state per cluster: tighter for strong links, looser only for single-witness links. It is free (no calls, no new candidates) and held on the held-out seeds (+0.04 at 10k with no seed worse, +0.06 at 50k on 3/3) while its cousin modal timing did not (+0.04 on calibration, −0.01 held-out — rejected). Two things the review established are withdrawn or flagged: the calibration-seed 'improved decoy resistance' did not replicate — the stale-chain family D5 rises under the adopted arm (+0.12 at 10k, +0.13 at 50k, seven of eight held-out seeds), because a staleness gate that one routine positive mention after a retraction defeats was being masked by min timing by accident; and the lag features the ranker reads are still computed from min timing, which the refit learned around and which is to be fixed before the next refit. The in-pool magnitude quoted by the panel (20–22 of 25 patterns) comes from its own replay, whose code is not in the repository.

### 3.1 The funnel, old and new (Mycelic hierarchy)

#### 10,000 (seeds 0–4)

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

#### 50,000 (seeds 0–2)

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
| local re-extraction at 50k | 50,000 | eval 0-2 | +0.007 (1/3 better) | -0.025 | -0.003 | +0.040 | +0.013 | +0.067 | +0.027 | +0.053 | +1% | -0% | rejected |
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

The biggest single gain is the ranker (with the budget it unlocked): +0.21 found for +43% compute like-for-like (+25% as benchmarked). Hybrid timing is the cheapest: +0.04 for +1%.

## 5. Largest remaining loss stage

In the new funnel the most common terminal loss for the hierarchy is **cut from the register (matched at >= 0.5, out-ranked)** at 10k (52 of 200 gold patterns) and **visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2)** at 50k (58 of 300).

At 10k the register is still the binding stage: coverage is near 0.97, so almost every gold pattern is in the kernel pool, and the loss is ordering among the ~3,000 candidates that pass the gate for 600 slots. At 50k the register (2,999 slots) is not binding; coverage (~0.72) is, and it is lost mainly at sketch visibility (58 of 300 gold patterns, 41 rare; 43 of the 66 sketch-invisible patterns fail causal span < 2), then at the question budget (17) and extraction (5); descent reach loses 0. Those are different problems and the queue in section 9 treats them separately.

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

One clean read of the frozen configuration on a panel nothing else ever touched exists as a by-product of the staleness-gate test: seeds 10–14 at 10k, found 0.650, rare recall 0.364, evidence coverage 0.930, decoy acceptance 0.365 (D5 0.740), compute 1.39e+06 (`quick_strong_fresh.jsonl`, arm `hyb`). It has no OLD counterpart, so it is a level, not a delta; it sits above both the development-panel (0.625) and confirmation-panel (0.570) levels in section 1.

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
| 1 | Register-forecast question budget at 50k (ask in gain order by distinct anchor until the forecast candidate count reaches α × cap) with descent fan-out 1 | whether 50k coverage is limited by the number of anchors asked (breadth) or by reach per anchor (depth) | high: coverage is the binding 50k stage and the two explanations imply opposite spending; but the funnel (50k, seeds 0–2) puts most of its loss at sketch visibility (58 of 300 patterns), with 17 at the question budget and 0 at descent reach | ~10 paired 50k runs | not run |
| 2 | D5 stale-chain mechanism: a staleness gate that compares the retraction against the link's assertion cluster (not just "strong positives only", which was tried) | whether the D5 rise under hybrid timing is the gate or the timing itself | high: D5 rose 0.26 → 0.78 across the accepted changes | cal screen + refit + fresh-panel read | the "strong positives only" variant was screened (+0.058 found on calibration seeds) and REJECTED on the fresh panel (found −0.035 with the frozen ranker, D5 −0.12; refit +0.02 / +0.02): it trades found for D5. The cluster-aware variant is still open |
| 3 | Kernel read chunked at the tier's context limit (as A2 already is), or an explicit degradation model | whether the vNext gain survives a kernel that cannot read a 1.4M/3.2M-token pool in one call | high: precondition of any deployment claim | code + paired runs at both scales | not run |
| 4 | Fresh evaluation panel (seeds 10–14 at 10k, 5–7 at 50k) read once | whether the development-panel deltas hold; this rerun's seeds 5–9 at 10k are the first such read | high for the small deltas | one suite run | seeds 5–9 done in this rerun |
| 5 | Sketch-corroboration feature for the ranker (foreign-site bit for each link's (entity, predicate)) | whether the ranker's remaining 10k ordering loss is missing information or missing capacity | high: offline AUC 0.74–0.77 for the feature; a null result says capacity | dump + refit + 5 paired runs | not run |
| 6 | Per-user object pools for A2/B4/Y with hybrid timing, context charged | whether the timing gain is the topology's or the controls' object builder's | medium: the panel's item was a null by construction | code + paired runs | not run |
| 7 | Lag features from the DP's chosen times; ranker applied once after enrichment; full-list dumps for the controls; then refit | whether the ranker hygiene defects cost anything | medium | refit + 5 paired runs | not run |
| 8 | Pairwise / top-K ranking objective on the same features | same question as 5 from the objective side | medium | refit only | not run |
| 9 | Budget-aware support-1 sketch bits (admit weak bits only into unused question budget) | how many of the rare patterns lost at the sketch (at 50k, seeds 0–2: 41 of the 58 sketch losses are rare, against 6 rare at the budget) weak bits recover without flooding the budget | medium: rare recall is 0.31–0.38 (10k and 50k, current rerun) and the weak-bit screen flooded triage | 5 paired runs | screen failed as implemented |
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
