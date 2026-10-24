# H3 — Bayesian promotion / hierarchical priors by rule family

## Summary (10 lines)

1. Kill check run on the filtered pool (rule_only, rule_weeks>=7: 1,765 keys, 138 gold, 317 decoy, gold:decoy 0.44). Per-family gold:decoy spans 0.25 (pricing) to 0.59 (supply), but the 7x2 family x (gold,decoy) table gives chi2=6.23, dof 6, **p=0.40**; 3-class p=0.27; at rule_weeks>=11 p=0.50 / 0.81. The spread is sampling noise.
2. The hierarchical Beta-Binomial fit agrees: between-family variance tau2 is +0.00003..+0.00021 on every fold (below the binomial sampling variance ~0.0003), so the shrunken family priors collapse onto the pooled mean. The model itself says "no family effect".
3. Within a seed there IS family structure (seeds 0,1,2: p=0.001, 0.000, 0.039), but seed x family interaction is p<0.0001 (gold) and 0.0006 (decoy): the mix differs per seed. Leave-one-seed-out, the train-vs-test family log-odds correlation is **negative on all six folds** (mean −0.41, range −0.79..−0.07). The prior does not transfer; it anti-transfers.
4. Reason, from the generator (read-only): `research/mycelic/corpus.py` draws the chain `ci = rng.integers(0, len(CAUSAL_CHAINS))` uniformly per pattern for both gold and decoys; gold's `fam_open` mechanism re-uses an open chain with p=0.55, which clumps gold by chain *within* a seed and carries nothing *across* seeds. All 7 rules in `pack/emergence/rules.json` are identical (window 8, min_sites 2, min_count 1): the predicate is the only rule identity the detector exposes.
5. H3-1 (family prior alone, LOSO): AUC gold-vs-decoy 0.416 mean (0.23–0.52); top-50 precision 0.033 vs 0.113 for rule_weeks. **DEAD.**
6. H3-2 (family prior x rule-week likelihood, P(H|E,R), LOSO): AUC 0.670 vs 0.683 for the rule-week likelihood alone; top-50 43 gold vs 49. Adding R to E lowers every metric. **DEAD** as a family hypothesis.
7. Side finding while running (6): the rule-week count is informative *non-monotonically*. rule_weeks==8 (the rules' window length) is a single 8-consecutive-week run for 557/584 keys and holds 13 gold vs 130 decoys; rule_weeks>=16 is decoy-heavy (34 gold / 81 decoy); 9..15 holds 90 gold / 102 decoy. The LOSO categorical likelihood learns the same bucket order on all six folds (12 > 9 > 11,13 > 14-19 > 10 > 20+ > 8 > 7).
8. H3-3 (rule-week shape, E alone): LOSO AUC 0.683 (min 0.601), beats monotone rule_weeks (0.607) on 6/6 seeds; top-20 25 gold vs 10, top-50 49 vs 34 with fewer decoys (55 vs 74). Label-free band 9<=rw<=15: AUC 0.706 (min 0.663); filter keeps 619 keys with 90 gold (0.145) and 0.165 decoy, 1.13 decoys/gold, vs wave-0's 736 keys, 99 gold (0.135), 0.213 decoy, 1.59 decoys/gold. Recall per seed 0.45–0.81 (wave 0: 0.62–0.80).
9. H3-3 is not a family effect and is outside this agent's brief (it is H5's territory); its buckets and band were chosen after seeing the pooled rw x class table, so count it as best-of-~8 variants (effective sample ~240/8). **PROMOTED to falsification**, conditional on H6's break-even and H8's null. It is still decoy-heavy (>1 decoy per gold).
10. Label-free within-seed proxies for the family clump (family crowding, entity-suffix) are dead too: AUC 0.563 (0.36–0.75) and 0.428. Everything here reads `raw/plateau/candidate_pool.csv` only; the dumps carry no field that varies inside this pool (channels=['rule'], supporting_sites=None, candidate_weeks=[] for all 1,765 keys; dump rule_weeks match the CSV for all 3,178 rows).

Scripts (all run, outputs beside them): `test_family_table.py` → `family_table_rw7.txt`, `family_table_rw11.txt`; `test_bayes_posterior.py` → `bayes_posterior_rw7.txt`; `test_rw8_rule.py` → `rw8_rule.txt`; `test_rw_shape.py` → `rw_shape.txt`. Run with `nice -n 10 $PYTHON -I <script>`.

---

## Kill check — per-family gold / decoy on the filtered pool (rule_only, rule_weeks >= 7)

| family (chain predicate) | n | gold | decoy | other | gold rate | decoy rate | gold:decoy | lift vs pool 0.44 |
|---|---|---|---|---|---|---|---|---|
| chain_firmware | 305 | 29 | 54 | 222 | 0.095 | 0.177 | 0.54 | 1.23 |
| chain_policy | 274 | 23 | 53 | 198 | 0.084 | 0.193 | 0.43 | 1.00 |
| chain_attrition | 272 | 16 | 51 | 205 | 0.059 | 0.188 | 0.31 | 0.72 |
| chain_supply | 255 | 24 | 41 | 190 | 0.094 | 0.161 | 0.59 | 1.34 |
| chain_network | 248 | 17 | 34 | 197 | 0.069 | 0.137 | 0.50 | 1.15 |
| chain_pricing | 211 | 10 | 40 | 161 | 0.047 | 0.190 | 0.25 | 0.57 |
| chain_auth | 200 | 19 | 44 | 137 | 0.095 | 0.220 | 0.43 | 0.99 |
| **pool** | **1765** | **138** | **317** | **1310** | **0.078** | **0.180** | **0.44** | 1.00 |

- family x (gold, decoy): chi2 = 6.23, dof 6, p = 0.397. family x (gold, decoy, other): chi2 = 14.46, dof 12, p = 0.272.
- Same at rule_weeks >= 11 (the wave-0 cut, pool gold:decoy 0.63): chi2 = 5.32, p = 0.504; 3-class p = 0.812. Pricing is the only family that looks low on both cuts (g:d 0.25 / 0.24) and it is 10 and 4 gold.
- Per seed (gold/decoy/n per family) and within-seed chi-square: seed 0 p=0.001, seed 1 p<0.001, seed 2 p=0.039, seed 3 p=0.205, seed 4 p=0.418, seed 5 p=0.960. Seed x family interaction: gold p<0.0001, decoy p=0.0006. The family structure is real inside a seed and different in every seed.
- LOSO transfer, corr(train family log-odds, held-out family log-odds): −0.068, −0.623, −0.257, −0.626, −0.793, −0.111 (mean −0.413).
- Hierarchical Beta-Binomial fit per fold (gold): m=0.076–0.080, tau2 = +0.00003, +0.00014, −0.00004, +0.00010, +0.00015, +0.00021; concentration 337 to 1e6 (full shrinkage on one fold). Decoy: tau2 −0.00017..+0.00035.
- Rule identity beyond the predicate: none exists. `pack/emergence/rules.json` defines seven `<chain>_multi_site` rules with identical `window_weeks: 8, min_sites: 2, min_count_per_site: 1`; `mycelic/collective/detect/rules.py` exposes only `rule_id` (1:1 with predicate), key, week and satisfying sites.

**Verdict of the kill check: rule identity is uninformative across seeds.** Per the brief this is a full result. The hypotheses below record the runs that establish it and the one thing found on the way.

---

## H3-1 — hierarchical family prior

```
H3-1
  STATISTIC   s(key) = P(gold | family f)  = (g_f + a) / (n_f + a + b)
              g_f, n_f = gold count and key count of family f on the five TRAINING seeds;
              a, b = Beta hyper-parameters by method of moments across the 7 families (shrinkage to the pooled rate)
  READS       predicate (the last ':'-field of the key); nothing else from the key
  FORBIDDEN   gold labels enter only as training counts on the other five seeds (score, never select on the scored
              seed); no facet_records, Placement internals, node ids, file paths, raw records, value equality
  CLAIM       s separates gold from decoy in the filtered rule-only pool with AUC >= 0.60 on every held-out seed
  KILL        pooled family x (gold,decoy) chi-square p > 0.05, OR LOSO AUC < 0.55 on any seed, OR negative
              train/test correlation of the family log-odds (stated before the run)
  COST        O(1) per key, dumps/CSV only; no world rebuild
  TEST        (test_family_table.py, test_bayes_posterior.py variant B)
    rows=[r for r in csv.DictReader(open(POOL)) if r['detector_state']=='rule_only' and int(r['rule_weeks'])>=7]
    fam=lambda r:r['key'].split(':')[-1]
    for s in range(6):
        tr=[r for r in rows if int(r['seed'])!=s]; te=[r for r in rows if int(r['seed'])==s]
        pri,_=beta_binomial_rates(tr,fam,'gold')          # method-of-moments Beta prior, per family posterior mean
        score={i:pri[fam(r)] for i,r in enumerate(te)}
        print(s, auc(score,[i for i,r in enumerate(te) if r['class']=='gold'],[i for i,r in enumerate(te) if r['class']=='decoy']))
```

RESULT (LOSO, each seed scored by a prior fit on the other five):

| | s0 | s1 | s2 | s3 | s4 | s5 | mean |
|---|---|---|---|---|---|---|---|
| AUC gold-vs-decoy | 0.464 | 0.227 | 0.519 | 0.394 | 0.388 | 0.505 | **0.416** |
| gold in top-20 (rule_weeks baseline) | 0 (2) | 1 (4) | 0 (1) | 1 (1) | 1 (2) | 2 (0) | 5 vs 10 |
| gold in top-50 (baseline) | 2 (8) | 1 (7) | 3 (2) | 1 (2) | 1 (10) | 2 (5) | 10 vs 34 |
| gold at top-n11 (baseline 99) | 8 | 4 | 11 | 9 | 4 | 5 | 41 |

top-50 precision 0.033 vs 0.113 for rule_weeks; decoy rate 0.183 vs 0.247 (fewer decoys only because it finds less of everything). All three kill conditions fired: p = 0.40, AUC < 0.55 on 6/6 seeds, transfer correlation negative on 6/6 folds.

**DEAD** (kill: chi2 p=0.397; LOSO AUC 0.416, below chance on five of six seeds; family log-odds anti-correlate across seeds, mean r=−0.41; generator draws the chain uniformly per pattern, `corpus.py` lines 532 and 605).

---

## H3-2 — P(H=1 | E, R): family prior x rule-week likelihood

```
H3-2
  STATISTIC   s(key) = P(gold | f, rw) = P(gold|f) P(rw|gold) / sum_c P(c|f) P(rw|c),  c in {gold, decoy, other}
              P(c|f): H3-1's hierarchical prior for gold and decoy (other = remainder); P(rw|c): categorical
              likelihood over buckets {7,8,9,10,11,12,13,14-15,16-17,18-19,20+}, add-0.5 smoothing, fit on the
              five training seeds
  READS       predicate (from the key), rule hit weeks (count)
  FORBIDDEN   labels only as training counts on other seeds; no facet_records, Placement internals, node ids, file
              paths, raw records, value equality
  CLAIM       s beats the rule-week likelihood alone (same E, uniform R) by >= 0.02 AUC and >= 5 gold in the summed
              top-50 across seeds — i.e. the family prior adds information to E
  KILL        the E-only ablation matches or beats the combined posterior on AUC and top-k (stated before the run)
  COST        O(1) per key, CSV only; no rebuild
  TEST        (test_bayes_posterior.py variants C and D)
    for s in range(6):
        tr=[r for r in rows if int(r['seed'])!=s]; te=[r for r in rows if int(r['seed'])==s]
        pg,_=beta_binomial_rates(tr,fam,'gold'); pd,_=beta_binomial_rates(tr,fam,'decoy'); lik=rw_likelihood(tr)
        base={c:sum(r['class']==c for r in tr)/len(tr) for c in CLASSES}
        for i,r in enumerate(te):
            f=fam(r); b=rw_bucket(int(r['rule_weeks'])); pf={'gold':pg[f],'decoy':pd[f]}; pf['other']=1-pf['gold']-pf['decoy']
            D[i]=pf['gold']*lik['gold'][b]/sum(pf[c]*lik[c][b] for c in CLASSES)        # with family prior
            C[i]=base['gold']*lik['gold'][b]/sum(base[c]*lik[c][b] for c in CLASSES)    # E-only ablation
```

RESULT (LOSO):

| variant | AUC per seed | mean / min | gold top-20 (sum) | decoy top-20 | gold top-50 | decoy top-50 | gold / decoy at top-n11 |
|---|---|---|---|---|---|---|---|
| A rule_weeks (wave 0) | .672 .668 .550 .494 .671 .583 | 0.607 / 0.494 | 10 (0.083) | 22 | 34 (0.113) | 74 (0.247) | 99 / 157 |
| C rw likelihood only (E) | .739 .711 .752 .602 .696 .601 | 0.683 / 0.601 | 25 (0.208) | 20 | 49 (0.163) | 55 (0.183) | 102 / 148 |
| **D family x rw (E,R)** | .738 .667 .750 .591 .676 .597 | **0.670 / 0.591** | 26 (0.217) | 19 | 43 (0.143) | 60 (0.200) | 97 / 146 |

D vs C: AUC −0.013, lower on 6/6 seeds; top-50 −6 gold, +5 decoys; top-n11 −5 gold. The family prior subtracts. The whole lift over wave 0 is E's (see H3-3). The kill condition fired.

**DEAD** (kill: the E-only ablation beats the combined posterior on every seed's AUC and on summed top-50; R carries no transferable information, so P(H|E,R) = P(H|E) up to noise).

---

## H3-3 — the shape of E: rule-week count as a non-monotone likelihood (side finding, not a family effect)

```
H3-3
  STATISTIC   LOSO form:  s(key) = P(gold | rw bucket), buckets {7,8,9,10,11,12,13,14-15,16-17,18-19,20+}, pooled
              class prior, add-0.5 smoothing, fit on five seeds (variant C above).
              Label-free form: s(key) = 2 if 9 <= rw <= 15, 1 if rw >= 16, 0 if rw <= 8   (8 = the rules' window)
  READS       rule hit weeks (count); the pack's rule window length (pack/emergence/rules.json window_weeks=8)
  FORBIDDEN   the label-free form reads no label anywhere; the LOSO form reads labels only on the other five seeds.
              No facet_records, Placement internals, node ids, file paths, raw records, value equality
  CLAIM       gold-vs-decoy AUC >= 0.65 on every held-out seed and higher than monotone rule_weeks on every seed;
              at a matched key count the filter has fewer decoys per gold than rule_weeks >= 11 (1.59)
  KILL        any held-out seed with AUC below monotone rule_weeks, OR the band keeping more decoys per gold than
              the >=11 cut, OR the rw==8 population not being single window-length runs (mechanism check)
  COST        O(1) per key, CSV/dumps only; no rebuild
  TEST        (test_rw_shape.py; mechanism check in the dumps)
    band=lambda r: 2 if 9<=int(r['rule_weeks'])<=15 else (1 if int(r['rule_weeks'])>=16 else 0)
    for s in range(6):
        te=[r for r in rows if int(r['seed'])==s]; order=sorted(te,key=lambda r:(-band(r),sha256(key)))
        print(s, auc(te,band), sum(r['class']=='gold' for r in order[:50]), sum(r['class']=='decoy' for r in order[:50]))
    kept=[r for r in rows if 9<=int(r['rule_weeks'])<=15]; print(len(kept), Counter(r['class'] for r in kept))
    # mechanism: for every rule_only key with len(rule_weeks)==8 in dumps/seed-*.json, are the 8 weeks consecutive?
```

RESULT:

Pooled rw x class, filtered pool:

| rule weeks | n | gold | decoy | other | gold rate | decoy rate | gold:decoy |
|---|---|---|---|---|---|---|---|
| 7 | 244 | 1 | 4 | 239 | 0.004 | 0.016 | 0.25 |
| **8** | **584** | **13** | **130** | 441 | 0.022 | **0.223** | **0.10** |
| 9–10 | 201 | 25 | 26 | 150 | 0.124 | 0.129 | 0.96 |
| 11–15 | 418 | 65 | 76 | 277 | 0.156 | 0.182 | 0.86 |
| 16–19 | 189 | 23 | 56 | 110 | 0.122 | 0.296 | 0.41 |
| 20+ | 129 | 11 | 25 | 93 | 0.085 | 0.194 | 0.44 |

Mechanism: 557 of 584 rw==8 keys are one run of 8 consecutive rule weeks (all 130 decoys and all 13 gold among them); 8 is `window_weeks` for all seven rules, so rw==8 is "one co-occurrence week carried by the window". The decoys in the filtered pool are D3 near-miss-entity (132) and D1 entity-coincidence (122) first, then D5 (28), D2 (21), D4 (19). The LOSO bucket order is the same on all six folds: 12 > 9 > {11, 13} > {14-15, 16-17} > 18-19 > 10 > 20+ > 8 > 7.

| form | AUC per seed | mean / min | vs monotone rw | gold top-20 / decoy | gold top-50 / decoy |
|---|---|---|---|---|---|
| monotone rule_weeks (wave 0) | .672 .668 .550 .494 .671 .583 | 0.607 / 0.494 | — | 10 / 22 | 34 / 74 |
| LOSO categorical (C) | .739 .711 .752 .602 .696 .601 | 0.683 / 0.601 | higher 6/6 | 25 / 20 | 49 / 55 |
| label-free band 9..15 > 16+ > <=8 | .709 .685 .723 .753 .663 .700 | 0.706 / 0.663 | higher 5/6 (s4: .663 vs .671) | 17 / 22 | 41 / 52 |
| rw==8 demoted only (W) | .671 .664 .550 .492 .671 .601 | 0.608 / 0.492 | tie | 10 / 22 | 34 / 74 |

Filter comparison per seed (kept, gold/total, precision, recall, decoy rate):

| filter | s0 | s1 | s2 | s3 | s4 | s5 | all |
|---|---|---|---|---|---|---|---|
| rw >= 11 (wave 0) | 139, 20/25, .144, .80, .20 | 115, 20/27, .174, .74, .24 | 110, 14/21, .127, .67, .21 | 130, 13/21, .100, .62, .22 | 129, 16/20, .124, .80, .21 | 113, 16/24, .142, .67, .20 | **736, 99/138, .135, .717, .213, 1.59 decoys/gold** |
| 9 <= rw <= 15 | 110, 13/25, .118, .52, .15 | 114, 18/27, .158, .67, .20 | 95, 17/21, .179, .81, .20 | 97, 16/21, .165, .76, .12 | 103, 9/20, .087, .45, .16 | 100, 17/24, .170, .71, .16 | **619, 90/138, .145, .652, .165, 1.13 decoys/gold** |

What it is and is not: the top-20/top-50 gains do not come from demoting rw==8 (W changes nothing at the top — top-k is already rw>=15 territory); they come from the categorical likelihood ranking rw 9–13 above rw 16+. The band trades 9 gold (recall .72 → .65) for 55 fewer decoys and 117 fewer keys; per-seed recall is wider (.45–.81 vs .62–.80; seed 4 has its gold at rw>=16). It remains decoy-heavy: 1.13 decoys per gold at 0.145 precision. Whether that pays is H6's break-even, not this agent's call. Bucket edges and the band were chosen after seeing the pooled table; the LOSO protects the per-bucket rates, not the bucketing. This agent tried ~8 variants; the falsifier should use 240/8.

**PROMOTED to falsification** (LOSO AUC 0.683, min 0.601, beats monotone rule_weeks on 6/6 seeds; band filter 0.145 gold / 0.165 decoy at 619 keys vs wave-0's 0.135 / 0.213 at 736; top-50 precision 0.163 vs 0.113) — conditional on H6's break-even and H8's null, and noted as E-shape (H5 territory), not a rule-family effect.

---

## Variants tried (all in the scripts; none beyond these)

| variant | what | LOSO AUC mean (min) | top-50 gold / decoy (sum over 6 seeds) | status |
|---|---|---|---|---|
| A | monotone rule_weeks (wave-0 baseline) | 0.607 (0.494) | 34 / 74 | reference |
| B | hierarchical family prior (H3-1) | 0.416 (0.227) | 10 / 55 | dead |
| C | rule-week categorical likelihood, LOSO | 0.683 (0.601) | 49 / 55 | H3-3 |
| D | B x C posterior P(H\|E,R) (H3-2) | 0.670 (0.591) | 43 / 60 | dead |
| E | within-seed family crowding (label-free: # rw>=7 keys of the same family in the seed) | 0.563 (0.361) | 25 / 47 | dead |
| F | entity-suffix prior (-CORE/-EDGE/-PAY/-II/X/plain), LOSO — a pack naming artefact, tried for completeness | 0.428 (0.327) | 16 / 53 | dead |
| W | rule_weeks with rw==8 demoted to 0 | 0.608 (0.492) | 34 / 74 | no change at the top |
| band | 9..15 > 16+ > <=8, label-free | 0.706 (0.663) | 41 / 52 | H3-3 label-free form |

Why the other two are worse than H3-3: H3-1 and H3-2 are dead on their own kill conditions; the only thing in this family that moved anything was the evidence term E, and it moved it by reshaping, not by a prior.

## What would resurrect the family hypothesis

A world in which the chain of a planted pattern is not drawn uniformly per pattern — i.e. a disclosure or generator change. On this benchmark, as generated, the per-family prior is fixed at the pooled rate by construction and no amount of data on dev seeds will change what it predicts on sealed seeds.
