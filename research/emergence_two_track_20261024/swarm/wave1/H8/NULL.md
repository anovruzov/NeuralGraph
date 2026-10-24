# H8 — NULL / ADVERSARY: what noise, selection and leakage look like on six dev seeds

**Summary (10 lines)**
1. Pool: wave-0 filtered rule-only pool, `rule_weeks >= 7`: 1,765 keys = 138 gold, 317 decoy, 1,310 other. Target is gold vs decoy (n = 455; per seed 20–27 gold against 46–62 decoy). Dev seeds 0–5 only; dumps + `candidate_pool.csv`; nothing rebuilt, nothing run.
2. **N1 (luck).** A sha256 of the key is a ranker. Over 1,000 salts its pooled gold-vs-decoy AUC spans 0.415–0.595 (95th pct 0.550); the luckiest salt scored AUC 0.595 with per-seed AUCs 0.69 / 0.61 / 0.55 / 0.55 / 0.72 / 0.48. The max-over-seeds p@20 is ≥ 0.50 for 17% of salts and ≥ 0.55 for 5%.
3. **N2 (best-of-k).** Reporting the best of k random projections over exported fields, labels permuted so nothing is real, gives in-sample AUC medians 0.553 / 0.566 / 0.578 for k = 10 / 50 / 200 and 95th percentiles up to 0.60 / 0.62 / 0.63. Under leave-one-seed-out every one of these collapses to 0.50 ± 0.07.
4. **N3 (leakage).** `F_TB_v4_feasible`, `rare`, `found`, `pattern` in `candidate_pool.csv` are non-empty on 100% of gold and 0% of decoys: `fillna(0)` on any of them is the label (AUC 1.000; `feasible == 1` alone 0.793, `rare` 0.833). A per-entity "prior" fitted in-sample is the label (AUC 0.999). A per-entity-NAME table fitted on five seeds and applied to the sixth still gets p@20 = 0.70 (permutation p = 0.003), because all six dev seeds draw anchors from the same ~150 names: **this is the artefact that survives LOSO.**
5. **Thresholds (one-sided p < 0.05, one pre-registered statistic):** pooled AUC > **0.549**; pooled p@20 ≥ **0.50** (10/20 on the gold+decoy subset) or ≥ **0.25** (5/20 on the full filtered pool); p@50 ≥ **0.42** / ≥ **0.16**; max per-seed p@20 ≥ **0.55**; single-seed AUC > **0.61–0.63**.
6. **Thresholds after selection:** to beat best-of-10 / 50 / 200 noise at the 95th pct: pooled AUC > **0.60 / 0.62 / 0.63** (Bonferroni with sign-flip: 0.583 / 0.597 / 0.608). The §4 "effective sample 240/k" rule is right in direction: selection moves the noise ceiling by +0.05 to +0.08 AUC here.
7. **Leave-one-seed-out spread of pure noise:** per-seed AUC sd 0.07; the range (max − min) of six per-seed AUCs has median 0.175 and 95th pct 0.30; all six above 0.50 happens 1.5% of the time; the mean of six above 0.55 happens 4.5%. "Works on five seeds and fails on one" is the modal outcome for noise, and "all six > 0.5" is a 1-in-64 event, not proof.
8. **Reference, not a null:** the free persistence statistics already in the exported fields separate gold from decoy at AUC 0.611 (rule-week count), 0.593 (span) and **0.668 (longest contiguous run; per seed 0.63–0.72, every seed above its own 5% line)**. A new statistic must clear 0.668, not 0.549, to add anything; that is agent 5's family and is left to it.
9. **Leak detection from the READS line:** (a) every field must appear in `example_candidate_fields`; (b) any field that is empty on one class and full on another is a label; (c) any gold rate / prior / posterior / calibration indexed finer than ~10 groups and fitted on the scored keys is a target encoding; (d) label-flip test: flip one key's label, s(key) must not move; (e) seed-swap test: refit on other seeds, a per-entity table changes, an exported statistic does not; (f) anything computed on the gold+decoy subset (group sizes, ranks, normalisations) uses subset membership, which is a label.
10. **What this means for the swarm:** with 138 gold and 317 decoys the resolution is about 0.03 AUC pooled and 0.07 per seed. Anything reported between 0.55 and 0.63 pooled is inside the selection band unless the statistic was written down before the run. Anything above 0.79 with p@20 = 1.00 is a leak until the label-flip test passes.

---

## 0. Setup

| item | value |
|---|---|
| pool | `raw/plateau/candidate_pool.csv`, `detector_state == rule_only`, `rule_weeks >= 7` |
| size | 1,765 keys: gold 138, decoy 317 (of 338 rule-only decoys; 21 fell below the cut), other 1,310 |
| gold-vs-decoy subset | 455 keys; chance p@k = 138/455 = 0.303 |
| per seed (gold, gold+decoy) | s0 (25, 83) · s1 (27, 89) · s2 (21, 71) · s3 (21, 72) · s4 (20, 70) · s5 (24, 70) |
| full-pool chance | gold 138/1765 = 0.078; decoys per 20 at random = 3.6 |
| exported fields used | `rule_weeks[]` (count, first, last, span, gaps, longest run), `candidate_weeks[]` (always empty here), `channels[]` (always `['rule']` here), key string (length, entity id, predicate/chain), entity-id hash buckets, sibling count (keys sharing the entity in the same seed) |
| metrics | Mann-Whitney AUC with average ranks for ties; precision@k with seeded random tie-break; exact hypergeometric for p@k; 20,000-draw permutation null for AUC |

In the filtered pool `candidate_weeks` count and `channels` count are constants (0 and 1): two of the five fields the brief names carry no information at all, which is itself worth knowing before anyone projects on them.

Scripts (all read-only, numpy only, `nice -n 10`):
`null_common.py` (loader, metrics) · `null_n1_hash.py` · `null_n2_bestofk.py` · `null_n3_leak.py`.
Raw outputs: `n1_result.md/.json`, `n2_result.md/.json`, `n3_result.md`.

---

## 1. N1 — hashed-key pseudo-random score ("what does luck look like")

`s(key) = sha256(salt, seed, key) / 2^64`. No fitting, no features. 1,000 salts.

Distribution over 1,000 salts (min | p5 | p25 | p50 | p75 | p95 | p99 | max):

| statistic | distribution |
|---|---|
| pooled AUC gold-vs-decoy | 0.415 \| 0.451 \| 0.481 \| 0.500 \| 0.521 \| **0.550** \| 0.569 \| 0.595 |
| p@20 pooled, gold+decoy subset (chance 0.30) | 0.05 \| 0.15 \| 0.25 \| 0.30 \| 0.35 \| **0.45** \| 0.55 \| 0.60 |
| p@50 pooled, gold+decoy subset | 0.12 \| 0.20 \| 0.26 \| 0.30 \| 0.34 \| **0.40** \| 0.46 \| 0.48 |
| p@20 pooled, FULL filtered pool (chance 0.078) | 0.00 \| 0.00 \| 0.05 \| 0.05 \| 0.10 \| **0.20** \| 0.25 \| 0.35 |
| p@50 pooled, FULL filtered pool | 0.00 \| 0.02 \| 0.06 \| 0.08 \| 0.10 \| **0.14** \| 0.18 \| 0.22 |
| decoys in the top-20 of the FULL pool (chance 3.6) | 0 \| 1 \| 2 \| 3 \| 5 \| 6 \| 8 \| 9 |
| **max over 6 seeds of per-seed p@20**, gold+decoy subset | 0.25 \| 0.35 \| 0.40 \| 0.40 \| 0.45 \| **0.50** \| 0.55 \| 0.65 |
| max over 6 seeds of per-seed p@20, FULL pool | 0.05 \| 0.10 \| 0.10 \| 0.15 \| 0.20 \| 0.25 \| 0.30 \| 0.30 |
| per-seed AUC (all 6,000 salt×seed values) | 0.256 \| 0.381 \| 0.451 \| 0.501 \| 0.549 \| 0.617 \| 0.667 \| 0.758 |

Histogram of pooled AUC over 1,000 salts:
```
  0.41-0.43  ##                                       6
  0.43-0.45  ###########                              40
  0.45-0.47  #############################            107
  0.47-0.49  ######################################## 199
  0.49-0.51  ######################################## 291
  0.51-0.53  ###########################              189
  0.53-0.55  ################                         117
  0.55-0.57  ######                                   45
  0.57-0.59  #                                        5
  0.59-0.60                                           1
```
Histogram of max-over-seeds p@20 (gold+decoy subset): 0.25–0.30: 43 · 0.30–0.35: 163 · 0.40–0.45: 355 · 0.45–0.50: 261 · 0.50–0.55: 140 · 0.55–0.60: 36 · 0.65–0.70: 2.

**The luckiest salt (140 of 1,000):** pooled AUC 0.595; per-seed 0.694, 0.609, 0.553, 0.553, 0.724, 0.477; p@20 0.35; p@50 0.38; max per-seed p@20 0.55. Fifty-four salts cleared the 0.549 line, six cleared 0.57, none cleared 0.60. An agent that reports "AUC 0.59, works on five of six seeds" has described this salt.

### Leave-one-seed-out spread that pure noise produces
A hash needs no fitting, so LOSO equals per-seed evaluation; the spread below is the floor for any statistic's six held-out numbers (20,000 permutation draws agree with the 1,000 salts).

| quantity over the 6 per-seed AUCs | min \| p5 \| p25 \| p50 \| p75 \| p95 \| p99 \| max |
|---|---|
| range (max − min) | 0.038 \| 0.088 \| 0.135 \| **0.175** \| 0.217 \| **0.299** \| 0.337 \| 0.450 |
| min of six | 0.256 \| 0.328 \| 0.379 \| 0.412 \| 0.443 \| 0.481 \| 0.510 \| 0.522 |
| max of six | 0.452 \| 0.516 \| 0.558 \| 0.589 \| 0.619 \| 0.670 \| 0.719 \| 0.758 |
| mean of six | 0.420 \| 0.450 \| 0.481 \| 0.500 \| 0.521 \| 0.551 \| 0.567 \| 0.602 |
| sd of six | 0.015 \| 0.034 \| 0.051 \| 0.067 \| 0.082 \| 0.106 \| 0.127 \| 0.144 |

| LOSO event | probability under noise |
|---|---|
| all six per-seed AUCs > 0.50 | 0.015 (exactly 2^-6 = 0.0156) |
| all six > 0.52 | 0.003 |
| all six > 0.55 | 0.0001 |
| at least 5 of 6 > 0.50 | 0.11 |
| at least 4 of 6 > 0.50 | 0.35 |
| mean of six > 0.55 | 0.045 |
| mean of six > 0.56 | 0.023 |

So: "survives LOSO" is worth a p of 0.016 when it means all six held-out seeds above 0.5, and nothing at all when it means five of six. A statistic whose six numbers span less than 0.17 is no more consistent than noise. For a statistic that *is* fitted (N2), the held-out numbers are drawn from exactly this distribution (§2).

---

## 2. N2 — best-of-k random projections ("we tried a few things and reported the best")

`s = X·w`, `w ~ N(0, I)`, k draws, report the best in-sample pooled AUC; sign-flip allowed (max(AUC, 1−AUC)), because an agent flips a sign for free — it doubles k. Two feature sets (columns standardised, constants dropped):

- **F_task** (the brief's list): `rw_count`, `cw_count` (dropped: constant 0), `ch_count` (dropped: constant 1), `key_len`, 16 one-hot sha256 buckets of the entity id → 18 columns.
- **F_ext**: F_task + `rw_first`, `rw_last`, `rw_span`, `rw_gaps`, `rw_longest`, `siblings`, 7 chain (predicate) one-hots → 31 columns.

Three label regimes, 200 independent best-of-k draws per cell, each also run under LOSO (select the projection on five seeds, score the sixth):

- **REAL** — labels as recorded. This is what an agent sees. It contains real signal (rule-week count AUC 0.611, longest run 0.668) diluted by noise columns.
- **PERM** — labels permuted within seed. Pure selection inflation on i.i.d. keys.
- **PERM-ENT** — labels permuted within seed at the entity-group level (sibling keys move together). Pure selection inflation when the feature space encodes entity groups, as hash buckets do.

| cell | in-sample best AUC median / 95th / max | p@20 median / 95th | p@50 median / 95th | max per-seed p@20 median / 95th | LOSO held-out: mean-of-6 median (95th) · min-of-6 · P(all 6 > 0.5) |
|---|---|---|---|---|---|
| F_task PERM k=10 | 0.553 / 0.582 / 0.625 | 0.35 / 0.55 | 0.36 / 0.46 | 0.45 / 0.55 | 0.498 (0.575) · 0.417 · 0.065 |
| F_task PERM k=50 | 0.566 / 0.598 / 0.622 | 0.40 / 0.55 | 0.38 / 0.46 | 0.50 / 0.60 | 0.501 (0.586) · 0.418 · 0.070 |
| F_task PERM k=200 | 0.578 / 0.605 / 0.628 | 0.40 / 0.55 | 0.40 / 0.48 | 0.50 / 0.60 | 0.503 (0.564) · 0.422 · 0.050 |
| F_task PERM-ENT k=10 | 0.557 / 0.592 / 0.615 | 0.40 / 0.55 | 0.38 / 0.46 | 0.50 / 0.65 | 0.495 (0.586) · 0.411 · 0.100 |
| F_task PERM-ENT k=50 | 0.574 / 0.601 / 0.636 | 0.40 / 0.55 | 0.40 / 0.48 | 0.50 / 0.60 | 0.510 (0.590) · 0.425 · 0.130 |
| F_task PERM-ENT k=200 | 0.587 / 0.616 / 0.633 | 0.40 / 0.60 | 0.40 / 0.50 | 0.50 / 0.65 | 0.505 (0.588) · 0.414 · 0.035 |
| F_ext PERM k=10 | 0.552 / 0.582 / 0.612 | 0.35 / 0.55 | 0.36 / 0.46 | 0.45 / 0.60 | 0.499 (0.576) · 0.413 · 0.055 |
| F_ext PERM k=50 | 0.572 / 0.592 / 0.620 | 0.40 / 0.55 | 0.38 / 0.46 | 0.50 / 0.60 | 0.487 (0.567) · 0.413 · 0.050 |
| F_ext PERM k=200 | 0.580 / 0.608 / 0.627 | 0.40 / 0.55 | 0.40 / 0.48 | 0.50 / 0.60 | 0.495 (0.576) · 0.410 · 0.050 |
| F_ext PERM-ENT k=10 | 0.568 / 0.601 / 0.619 | 0.40 / 0.60 | 0.38 / 0.48 | 0.50 / 0.60 | 0.519 (0.599) · 0.429 · 0.135 |
| F_ext PERM-ENT k=50 | 0.590 / **0.617** / 0.645 | 0.40 / 0.60 | 0.42 / 0.52 | 0.50 / 0.60 | 0.530 (0.614) · 0.444 · 0.145 |
| F_ext PERM-ENT k=200 | 0.604 / **0.633** / 0.647 | 0.45 / 0.60 | 0.42 / 0.52 | 0.53 / 0.60 | 0.521 (0.623) · 0.439 · 0.175 |
| F_task REAL k=10 | 0.570 / 0.600 / 0.610 | 0.35 / 0.60 | 0.38 / 0.48 | 0.55 / 0.65 | 0.519 (0.596) · 0.430 · 0.090 |
| F_task REAL k=50 | 0.590 / 0.611 / 0.625 | 0.40 / 0.60 | 0.40 / 0.50 | 0.55 / 0.70 | 0.521 (0.601) · 0.436 · 0.085 |
| F_task REAL k=200 | 0.602 / 0.620 / 0.628 | 0.40 / 0.60 | 0.40 / 0.52 | 0.60 / 0.70 | 0.523 (0.592) · 0.437 · 0.075 |
| F_ext REAL k=10 | 0.570 / 0.596 / 0.625 | 0.40 / 0.55 | 0.38 / 0.46 | 0.50 / 0.65 | 0.520 (0.599) · 0.415 · 0.090 |
| F_ext REAL k=50 | 0.592 / 0.615 / 0.633 | 0.40 / 0.55 | 0.40 / 0.48 | 0.55 / 0.65 | 0.503 (0.603) · 0.405 · 0.085 |
| F_ext REAL k=200 | 0.606 / 0.628 / 0.637 | 0.40 / 0.55 | 0.42 / 0.50 | 0.55 / 0.65 | 0.512 (0.588) · 0.413 · 0.040 |

Reading it:
- **Selection alone buys +0.05 to +0.08 AUC in-sample** (PERM medians 0.553 → 0.578 as k goes 10 → 200; the analytic expectation 0.5 + 0.0295·E[max of k normals] = 0.545 / 0.566 / 0.581 matches). The 95th percentile of the best-of-k noise AUC — the number a real statistic must beat to be distinguishable from an agent who tried k things — is **0.60 (k=10), 0.62 (k=50), 0.63 (k=200)**, taking the worst regime (entity-grouped labels, 31 columns).
- **Group structure in the features inflates it further.** PERM-ENT runs about +0.01 above PERM at every k: hash buckets of the entity id are noise *per entity* but keys on the same entity share a bucket, and decoys come 1–5 keys per entity. A projection can memorise a few entity groups. Any feature that is constant within an entity (hash bucket, name length, suffix) has this property.
- **REAL ≈ PERM-ENT.** The random-projection route mixes the real persistence signal with 16–29 noise columns, so what an agent would see from this procedure (median 0.57–0.61) is indistinguishable from pure selection. The real signal is there (`rw_longest` alone is 0.668) but best-of-k over random projections cannot tell you that; only the pre-registered single statistic can.
- **LOSO kills all of it, REAL included.** Held-out mean-of-6 medians are 0.49–0.53 in every cell; the min-of-6 is 0.41–0.44; P(all six held-out > 0.5) is 4–18%, i.e. the §1 noise rate inflated slightly by the entity-group memorisation. The REAL rows do not survive LOSO either, because a random projection chosen on five seeds for in-sample AUC is mostly noise weights.
- **§4 item 3 ("effective sample 240/k") is directionally right but the inflation is bounded**, because the k projections are correlated through the shared 18–31 columns. Use the empirical 95th percentiles above rather than 240/k.

**Worked example** (F_ext, REAL labels, best-of-200, rng 7) — the projection an agent would have written up: in-sample pooled AUC **0.623**, per-seed 0.71, 0.69, 0.55, 0.55, 0.55, 0.64, p@20 0.45, max per-seed p@20 0.65. Largest weights: `ent_hash5 = −2.71`, `rw_longest = +1.81`, `chain=attrition = −1.57`, `ent_hash2 = +1.32`. Same procedure under LOSO: held-out per-seed AUC 0.47, 0.50, 0.55, 0.45, 0.55, 0.64, mean **0.527**. The in-sample number clears every N1 threshold and sits at the best-of-200 95th percentile; the held-out number is noise.

---

## 3. N3 — leakage artefacts (statistics that quietly use the label)

### N3a — the accidental column
`candidate_pool.csv` carries `pattern`, `rare`, `found`, `F_TB_v4_feasible`. Missingness by class in the filtered pool:

| column | gold | decoy | other |
|---|---|---|---|
| `pattern` | 138/138 | 0/317 | 0/1310 |
| `rare` | 138/138 | 0/317 | 0/1310 |
| `found` | 138/138 | 0/317 | 0/1310 |
| `F_TB_v4_feasible` | 138/138 | 0/317 | 0/1310 |

| statistic that looks like a feature | AUC | per-seed | p@20 / p@50 |
|---|---|---|---|
| `F_TB_v4_feasible.notna()` ("has a feasibility verdict") | **1.000** | 1.00 ×6 | 1.00 / 1.00 |
| `F_TB_v4_feasible == 1` ("feasible under F/TB_v4") | **0.793** | 0.82 0.80 0.79 0.83 0.75 0.77 | 1.00 / 1.00 |
| `rare.fillna(0)` | **0.833** | 0.84 0.85 0.76 0.74 0.90 0.90 | 1.00 / 1.00 |
| `rare.notna()` | **1.000** | 1.00 ×6 | 1.00 / 1.00 |

"Feasible under F/TB_v4" sounds like an exported-contract computation (it is a ceiling computed from bucket counts). In this CSV it was only ever computed for gold. An agent who joins the CSV to the dumps and lets pandas fill the NaNs has an AUC of 0.79 with a perfect top-50 and no idea why. **Detect:** the column is not in `example_candidate_fields`; its null pattern is the class; every per-seed number is far above 0.72, the highest honest per-seed number in this pool.

### N3b — the fine-grained prior (target encoding dressed as hierarchical Bayes)
Beta(1,10)-smoothed gold rate per group, agent 3's "prior over rule families" at the wrong granularity:

| version | AUC | per-seed | p@20 / p@50 | verdict |
|---|---|---|---|---|
| per-(seed, entity), in-sample, own label included | **0.999** | 1.00 ×6 | 1.00 / 1.00 | the label |
| same, leave-one-key-out, groups over the gold+decoy **subset** | 0.683 | 0.72 0.71 0.63 0.64 0.70 0.70 | 0.10 / 0.28 | subset-membership leak (below) |
| same, leave-one-key-out, groups over the **full** pool | 0.537 | 0.63 0.49 0.62 0.50 0.43 0.55 | 0.25 / 0.28 | honest; = sibling count |
| per-entity-**NAME**, fit on 5 seeds, score the 6th (LOSO) | 0.564 | 0.60 0.60 0.55 0.58 0.45 0.59 | **0.70** / 0.46 | survives LOSO — see below |
| per-chain (7 groups), in-sample | 0.570 | 0.81 0.71 0.61 0.39 0.38 0.49 | 0.25 / 0.34 | small leak, unstable |
| per-chain, LOSO | 0.389 | 0.46 0.23 0.44 0.36 0.38 0.49 | 0.20 / 0.18 | honest: chain identity is uninformative or inverted |

Two of these are instructive:

**The subset-membership leak (0.683).** Computing the group size over the gold+decoy evaluation subset makes `n_group` = "number of *other* gold or decoy keys on this entity". Decoys are planted 1–5 keys per entity, gold one key per anchor, so a small group means gold. Nothing in the statistic reads a label — but *which keys are in the subset* is the label. Recomputing the groups over the full 1,765-key pool drops it to 0.537. **Any statistic that is normalised, ranked, grouped or counted inside the gold+decoy subset has this leak.**

**The entity-name table that survives LOSO (0.564, p@20 0.70).** All six dev seeds draw their 40 gold anchors from the same ~150 entity names (125 distinct across six seeds; uniform choice predicts 127, so the draw is near-uniform, but one anchor is gold in all six seeds and 66 names recur). A table "gold rate by entity name" fitted on five seeds and applied to the sixth scores pooled AUC 0.564 (permutation p = 0.055) and **p@20 = 0.70 (permutation p = 0.003)**. It would pass §4 items 2 and 4 on dev seeds and would pass again on the sealed seeds, because they share the vocabulary. It is void under §2 anyway — a table fitted on labels is labels selecting — but the falsifier has to catch it by reading, not by LOSO. **Detect:** READS names `entity id` *and* the statistic contains a rate, prior, weight or lookup indexed by it; the seed-swap test (refit on a different five seeds) changes s(key); a genuine exported statistic is a function of the key's own exported fields and nothing else.

**Label-flip test results** (does s(key) move when only that key's label is flipped?): in-sample entity prior — yes, by 1/(n_group+11) ≈ 0.06–0.08; LOO entity prior — no; LOSO entity-name table — no, but a seed swap moves it; in-sample chain prior — yes, by 0.013; hash score — no.

### N3c — the label-chosen threshold (what a per-seed table is worth when the cut came from the labels)
Rule-week count, gold-vs-decoy: monotone AUC 0.611 pooled (per seed 0.67 0.67 0.55 0.49 0.67 0.58). The best single cut chosen on all six seeds is `>= 9` (pooled 0.661; per seed 0.70 0.66 0.65 0.62 0.69 0.64). Honest LOSO (choose the cut on five, apply to the sixth) picks `>= 9` every time: mean 0.660. Inflation here: +0.001, because the signal is real and monotone. The same move on a pure-noise feature with 19 candidate quantile cuts gives in-sample AUC median 0.524, 95th pct **0.555** — just above the pre-registered line of 0.549. Wave 0's ≥ 11 cut is of this type (chosen because the buckets below it were empty), but its zero-gold filter is far outside any null: P(0 gold in a random 1,066 of 2,831 | 138 gold) ≈ 0.623^138 ≈ 10^-28. The threshold choice is real; the per-seed "validation" table in WAVE_0_RESULT.md is not LOSO and should not be read as such.

### N3d — the window alignment
Fraction of a key's rule weeks inside its own label window `[found_from, found_to]`: AUC 0.377 (gold *below* decoy — decoy windows are tighter). Reads `labels.*` outright; void. Listed because "fraction of weeks in window" is how an agent would phrase it without saying where the window came from. **Detect:** READS mentions found_from / found_to / facet_weeks / sites / anchor / grace / "label window". None are exported.

---

## 4. The quotable table

All numbers are for gold vs decoy on the filtered pool (138 vs 317 pooled; 20–27 vs 46–62 per seed). "Exceed" is strict.

| to be distinguishable from … | pooled AUC | pooled p@20 (gold+decoy subset, chance 0.30) | pooled p@50 (subset) | pooled p@20 (FULL pool, chance 0.08) | pooled p@50 (FULL) | max over 6 seeds of per-seed p@20 | single-seed AUC |
|---|---|---|---|---|---|---|---|
| **N1 luck, one pre-registered statistic, p < 0.05** | **> 0.549** | **≥ 0.50** (10/20) | **≥ 0.42** (21/50) | **≥ 0.25** (5/20) | **≥ 0.16** (8/50) | **≥ 0.55** (11/20) | > 0.61–0.63 |
| N1 luck, p < 0.01 | > 0.569 | ≥ 0.60 (12/20) | ≥ 0.46 (23/50) | ≥ 0.30 (6/20) | ≥ 0.20 (10/50) | ≥ 0.60 (12/20) | > 0.66–0.68 |
| **best-of-10** (in-sample, sign flip allowed), 95th pct of noise | **> 0.60** | > 0.60 | > 0.48 | — | — | > 0.65 | — |
| **best-of-50** | **> 0.62** | > 0.60 | > 0.52 | — | — | > 0.65 | — |
| **best-of-200** | **> 0.63** | > 0.60 | > 0.52 | — | — | > 0.70 | — |
| free persistence baseline (reference, not a null): longest rule-week run | > 0.668 | > 0.35 | > 0.26 | — | — | — | > 0.63–0.72 |
| **leak alarm**: presume N3 until the label-flip test passes | ≥ 0.79 | = 1.00 | = 1.00 | | | | every seed ≥ 0.74 |

Compact form: **to beat N1 at p < 0.05: AUC > 0.549, p@20 > 0.45 (i.e. ≥ 10/20), p@50 > 0.40, max per-seed p@20 > 0.50; to beat best-of-10: AUC > 0.60; best-of-50: AUC > 0.62; best-of-200: AUC > 0.63; to add anything to what the exported fields already give for free: AUC > 0.668.**

### The calculations
- **AUC under H0** (Mann-Whitney, no ties): sd = √((n₁+n₀+1)/(12·n₁·n₀)). Pooled: √(456/524,952) = **0.0295** → one-sided 5%: 0.5 + 1.645·0.0295 = **0.5485**; 1%: 0.5 + 2.326·0.0295 = 0.5686. Empirical (20,000 permutations): 95th 0.5488, 99th 0.5684. Per seed, e.g. s0 (25 vs 58): sd = √(84/17,400) = 0.0695 → 0.614; s4 (20 vs 50): sd 0.0769 → 0.627.
- **p@k under H0**: hits ~ Hypergeom(N, K, k). Subset: N = 455, K = 138: P(X ≥ 10 | k=20) = 0.048, P(X ≥ 21 | k=50) = 0.043. Full pool: N = 1,765, K = 138: P(X ≥ 5 | k=20) = 0.016 (P(X ≥ 4) = 0.065), P(X ≥ 8 | k=50) = 0.037.
- **Max over six seeds of per-seed p@20**: P(max ≥ x) = 1 − Π_s P(X_s ≤ x−1), X_s ~ Hypergeom(N_s, K_s, 20) with the per-seed sizes above: P(max ≥ 10/20) = 0.17, **P(max ≥ 11/20) = 0.049**, P(max ≥ 12/20) = 0.010, P(max ≥ 13/20) = 0.002. The 1,000-salt empirical 95th percentile is 0.50 and 0.9% of salts reach 0.60.
- **Best-of-k**: Bonferroni, k tries: 0.5 + z(1 − 0.05/k)·0.0295 = 0.576 / 0.591 / 0.603 for k = 10 / 50 / 200; with sign flips (2k): 0.583 / 0.597 / 0.608. The empirical 95th percentiles (0.60 / 0.62 / 0.63) are higher because entity-grouped features let a projection memorise groups; use the empirical ones.
- **LOSO**: with per-seed sd ≈ 0.07, six independent held-out AUCs have range median 2.5·sd ≈ 0.175; P(all six > 0.5) = 2⁻⁶ = 0.0156; P(mean of six > 0.55) = P(Z > 0.05·√6/0.0715) ≈ 0.045.

---

## 5. Leakage-detection checklist (apply to the READS line of every §2 hypothesis)

1. **Field provenance.** Every token in READS must be one of the 17 `example_candidate_fields` in the dump (`alert_weeks, candidate_weeks, channels, config_hash, decision_unit, detection_week, detector_hash, detectors, entity_id, entity_type, first_candidate_week, key, org_hash, predicate, rule, run_channel, snapshot`) or a bucketed count derived from one. Anything under `labels.*`, `asked_keys`, `detected_with_snapshot`, `report`, or a `candidate_pool.csv` column other than `seed, key, detector_state, rule_weeks, candidate_weeks` is off-limits. Specifically void: `class, pattern, rare, found, F_TB_v4_feasible, asked, snapshot_week` (the last two are constant in the rule-only pool and so merely useless).
2. **Missingness-by-class.** For every column read, tabulate non-empty / total per class. A column that is populated on one class and empty on another *is* the label after `fillna`. In this CSV four columns are 138/138 gold, 0/317 decoy.
3. **Fitted tables.** Any "prior", "rate", "posterior", "calibration", "weight by family", "target encoding", "class prior estimate" that is indexed by a per-key field and fitted on the keys being scored is a leak at fine granularity (entity: AUC 0.999) and a small unstable one at coarse granularity (7 chains: 0.57 in-sample, 0.39 LOSO). Granularity finer than ~10 groups with ~455 keys is a fit to labels.
4. **Label-flip test.** Flip one key's label and recompute s(key). A legitimate statistic does not move. Costs one line.
5. **Seed-swap test.** Refit (or just recompute) on a different subset of seeds. An exported statistic of the key's own fields does not change; a per-entity-name table does. This is the only test that catches the N3b LOSO-surviving artefact, because dev and sealed seeds share the entity vocabulary.
6. **Subset-membership leak.** Any group size, rank, z-score, percentile or normalisation computed inside the gold+decoy evaluation subset uses the subset's membership, which is the label (0.683 vs 0.537 honest). All derived quantities must be computed over the full 1,765-key filtered pool (or the 2,831-key rule-only pool), then evaluated on gold vs decoy.
7. **Window words.** "inside the window", "aligned with onset", "within the grace period", "label span", `found_from/found_to/facet_weeks/sites/anchor/contradicted/facets` → reads labels. The only exported windows are the key's own `rule_weeks` and `candidate_weeks`.
8. **Too-good-to-be-honest.** The best honest exported statistic found here scores 0.668 pooled, 0.72 on its best seed. Anything ≥ 0.79 pooled, or with p@20 = p@50 = 1.00, or ≥ 0.74 on every seed, should be treated as N3 until items 1–7 clear it.
9. **Threshold provenance.** If a cut-off was chosen by looking at a gold-rate table (wave 0's ≥ 6 / ≥ 11), the per-seed table that follows is in-sample, not LOSO. Re-choose the cut on five seeds and apply to the sixth before quoting per-seed numbers. On a real monotone signal the cost is ~0; on noise it is the whole effect (0.555 → 0.50).
10. **Best-of-many disclosure.** Ask how many variants were computed before the one reported (features × transforms × sign × thresholds × k-values). Compare the reported AUC with the 95th percentile for that k in §2; if it sits inside the band, the effective evidence is nil without a held-out seed.

---

## 6. What the falsifiers should take from this

- The sample resolves ~0.03 AUC pooled and ~0.07 per seed. Claims in the band **0.55–0.63** are inside the selection envelope unless the statistic was pre-registered as a single formula.
- "Survives leave-one-seed-out" is worth p ≈ 0.016 at best (all six > 0.5), and nothing if it means five of six, or if the fitted object is a per-entity-name table.
- The honest free baseline from the exported fields is already **0.668** (longest contiguous run of rule weeks; per seed 0.63–0.72, every seed significant on its own). That number, not 0.549, is what a new statistic has to beat to be worth a question — and it is a persistence statistic, so it will also pull decoys (wave 0's point). Agent 5's family should own it; it is reported here only because N2's feature set exposed it.
- Nothing here says a real signal exists beyond persistence. It says what the six seeds can and cannot see.

Files: `NULL.md` (this), `null_common.py`, `null_n1_hash.py`, `null_n2_bestofk.py`, `null_n3_leak.py`, `n1_result.md/.json`, `n2_result.md/.json`, `n3_result.md`, `START`, `END`. Reproduce with `cd swarm_out/H8 && nice -n 10 $PYTHON -I null_n1_hash.py && nice -n 10 $PYTHON -I null_n2_bestofk.py && nice -n 10 $PYTHON -I null_n3_leak.py` (8 s, 190 s, 7 s).
