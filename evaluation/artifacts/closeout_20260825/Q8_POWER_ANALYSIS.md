# Q8 — Can n=60 measure improvement?

**Answer: NO.** Seeded Monte Carlo, 4000 iterations, seed 20260825, exact
two-sided McNemar at alpha 0.05, baseline 24/60, measured judge flip 4.5%.

Artifact: `q8_power_analysis.json` · code: `evaluation/improvement_loop/power.py`
· tests: `NeuralGraph/tests/test_power_analysis.py`

## 1. Minimum net improvement distinguishable from noise

**12 questions = 20.0 percentage points** for 80% power.

That is larger than the entire addressable failure pool for any single mechanism
identified so far (incomplete_list is 4 questions; the largest bucket,
wrong_selection, is 10).

## 2. Power at realistic effect sizes

| true gain | points | power | observed delta 95% CI |
|---|---|---|---|
| 1q | 1.7 | 0.012 | [−0.050, +0.083] |
| 2q | 3.3 | 0.027 | [−0.050, +0.100] |
| **3q** | **5.0** | **0.059** | [−0.033, +0.117] |
| 4q | 6.7 | 0.103 | [−0.017, +0.133] |
| **5q** | **8.3** | **0.175** | [+0.000, +0.150] |
| 6q | 10.0 | 0.261 | [+0.017, +0.167] |
| 7q | 11.7 | 0.364 | [+0.033, +0.183] |
| **8q** | **13.3** | **0.470** | [+0.050, +0.200] |
| 9q | 15.0 | 0.585 | [+0.067, +0.217] |
| **10q** | **16.7** | **0.692** | [+0.083, +0.233] |

A genuine 5-point gain is detected **17.5%** of the time. Four times in five it
is missed and would be recorded as "no improvement".

## 3. Sample size for 80% power

| true effect | with measured judge noise | with a perfect judge |
|---|---|---|
| 3.0 pt | **1,232** | 242 |
| 5.0 pt | **547** | 161 |
| 8.0 pt | **242** | 71 |
| 10.0 pt | **161** | 71 |

## 4. What judge instability costs

At a fixed 8-point effect, judge noise raises the requirement from **71 to 242
questions — 3.4x**. The judge flip is applied independently to both arms, so it
adds variance to a paired design whose whole purpose is to cancel shared
variance.

There is also a floor independent of n: with a perfect judge, a pure-gain
candidate produces zero regressions, and an exact two-sided McNemar needs **six**
one-way discordant pairs before it can reach p < 0.05 at all (5 → p = 0.0625;
6 → p = 0.031). That is why the perfect-judge power curve is flat at zero for
gains below six and jumps to 1.0 by eight. It is pinned as a test.

## 5. Can the 60-question set responsibly accept or reject a single-mechanism fix?

**No.** A normal single-mechanism fix in this repository moves 2-5 questions.
Power there is **0.03 to 0.18**. Such a set cannot distinguish a real gain from
judge noise, and it cannot license a rejection either: failing to reach
significance at 17% power is uninformative.

**Consequence, stated plainly: further scored rounds on this 60-question set are
diagnostic, not confirmatory.** They can locate failure mechanisms. They cannot
accept or reject a fix. Every accept/reject gate in the earlier loop design was
therefore under-powered by roughly an order of magnitude, and the v2 round's
non-significant p = 0.18 was the expected outcome of the design rather than a
finding about the mechanism.

To make confirmatory rounds possible, either enlarge the scored set to several
hundred questions, or remove judge nondeterminism (cache one verdict per
distinct answer string and reuse it, which also cuts cost), or both. Removing
judge noise alone cuts the 8-point requirement from 242 to 71.
