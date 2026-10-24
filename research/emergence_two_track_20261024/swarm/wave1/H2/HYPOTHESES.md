# H2 — POSITIVE-UNLABELED LEARNING — HYPOTHESES.md

## Summary (10 lines)

1. **The class prior does not calibrate under any exported feature set.** Elkan–Noto with P = snapshot gold (102), U = rule-only pool: rule-week features put the prior at 0.29–0.93 (truth 0.049 full / 0.078 filtered); per-site bucket features put it at 0.014–0.031. The binned mixture-proportion estimate on the bucket score is 0.000. Nothing lands near 0.049/0.078.
2. **Why: the known positives are gate-selected, not SCAR.** 94% of snapshot gold reach two k-sites in some window; 41% of rule-only gold do. The PU classifier learns "passed the detector gate", not "is gold" (cosine with the oracle gold-vs-decoy direction: 0.36).
3. **PU ranker vs rule_weeks on the filtered pool (1,765 keys, 138 gold, 317 decoy): DEAD for gold-vs-decoy.** Best PU variant (bucket features, LOSO): AUC(gold|decoy) 0.643, per-seed 0.54–0.76, below the n_rw baseline (0.49–0.67) on 3 of 6 seeds.
4. Against interest: the same ranker is a strong **plantedness** ranker — top-20/seed holds 41 gold + 40 decoys over six seeds vs 9.5 + 22.7 for n_rw (gold on every seed better) — but at wave 0's matched budget (736 keys) it returns 102 gold / 181 decoys vs 99 / 157: same gold, more decoys. The decoy column decides.
5. **ORACLE-DIAGNOSTIC (labels used to fit, not a hypothesis):** a supervised gold-vs-decoy logistic on the exported bucket features reaches AUC 0.83–0.86 leave-one-seed-out. The exported contracts *do* carry the gold-vs-decoy direction; no label-free construction tried here recovers it.
6. **PN transfer from the asked set** (P = asked-and-found, N = rest of the snapshot pool), bucket features, LOSO: AUC 0.708, per-seed 0.64–0.81, above n_rw on 4/6 seeds; top-20/seed = 43% gold / 28% decoy (n_rw 8% / 19%; wave-0 ≥11 cut 13.5% / 21.3%). This is the only lift in the family.
7. **It dies at inference time.** Rebuilt from the system's own verdicts (asked & supported vs asked & never supported, read from the official ce0e357 reports) it collapses to AUC 0.561 and 68% decoys at top-20: the judge supports 207 of 216 asked decoys, so "supported" agrees with "found" on only 39% of asked keys. The 0.71 needs the gold label on the asked set, which the system does not have.
8. So the family premise — "the snapshot pool's gold is available at inference time" — is false for this system as built; it is label knowledge, not system knowledge.
9. Variants tried in total: **41** (22 PU configurations × in-sample and LOSO; 5 PN-transfer feature sets; 12 single no-fit statistics; 2 verdict-based). One primary was pre-registered (P = snapshot gold, ALL features, logistic g). None of the 41 clears AUC 0.70 label-free on every held-out seed.
10. **Verdict: all three hypotheses DEAD.** The resurrection condition is precise: a positive set of asked keys that is ≥ ~70% gold instead of 31% (a judge or confirmation channel that rejects decoys), or an unsupervised statistic that aligns with the oracle direction listed in §Notes — which is the correct hand-off to the spectral / correlated-evidence families.

Data: dev seeds 0–5 only; `raw/plateau/candidate_pool.csv` + `scratchpad/dumps/seed-*.json` (free) + one world rebuild per seed for per-site buckets (`test_01_worlds.py`, ~3 s/seed here). Filtered pool = rule-only keys with rule_weeks > 6 (wave 0). Scores are expected counts under uniformly random tie-breaking. All scripts in this directory; outputs in `results_*.txt`.

---

```
H-H2-1   (class prior from the known positives — the family's calibration check)
  STATISTIC   s(key) = g(x)/c  with g = P(labeled | x) an L2-logistic fitted P-vs-U, c = E[g | P] out-of-fold (5-fold);
              prior-in-U  pi = |P|(1-c)/(c|U|)  (Elkan & Noto 2008), also  pi = (1/|U|) sum_U w(x), w = (1-c)/c * g/(1-g);
              cross-check pi_MPE = min over bins b of p_U(b)/p_P(b)  (mixture-proportion estimate)
  READS       rule hit weeks (count, first, last, span, density, runs, gaps, recency, weeks in first half / last 6);
              entity id + predicate only as "number of other pool keys on the same entity" and their rule-week total;
              per-site per-week buckets over the 6-week window (records '<k'|count, reporters, roots, contradiction flag,
              newest week), reduced per key to 20 summaries (test_02_pu.py BUCKET_NAMES). P = snapshot-pool gold keys.
  FORBIDDEN   confirmed: no rule-only label is read in fitting; no facet_records, Placement internals, node ids, file
              paths, raw records, or value equality across sites. Candidate weeks / detector channel are excluded as
              features because they are the P-membership indicator itself.
  CLAIM       pi lands within a factor 1.5 of 0.049 (full pool) and 0.078 (filtered) for at least one exported feature set.
  KILL        every feature set gives pi outside [0.033, 0.074] (full) and [0.052, 0.117] (filtered); or pi_MPE on the
              best feature set is ~0 (P separable from U), which means P is not a SCAR sample of the positives.
  COST        O(weeks x sites) per key for the buckets, needs the world rebuild (one pass, six seeds); rule-week features
              are free from the dumps.
  TEST        test_02_pu.py section (1); condensed:
                P = snapshot&gold; U = rule_only [& n_rw>6]
                for names in FEATURE_SETS: X = feats(P|U, names); y = isP
                    g = cv_scores(X, y, lam=1, k=5)                    # out-of-fold logistic
                    c = g[y==1].mean(); pi = len(P)*(1-c)/(c*len(U))
                pi_MPE = nanmin(hist_U/hist_P) over n_rw bins and over P-quintile bins of g
  RESULT      (pool full -> target 0.049 ; filtered -> target 0.078)
                rw_only          c=0.051  pi=0.677 | c=0.058  pi=0.934
                shape            c=0.109  pi=0.294 | c=0.113  pi=0.455
                shape+ent        c=0.156  pi=0.194 | c=0.155  pi=0.316
                shape+ent+pred   c=0.150  pi=0.205 | c=0.149  pi=0.331
                buckets          c=0.636  pi=0.021 | c=0.650  pi=0.031
                ALL              c=0.719  pi=0.014 | c=0.719  pi=0.023
                pi_MPE on n_rw: 0.257 / 0.413 (upper bound, far above);  pi_MPE on ALL-feature g: 0.000 / 0.000
                (P's g lies almost entirely in quintiles where U has 13/2816 and 1/2816 keys: P is separable from U)
              SCAR check (test_03_diag.py a): P(ever >=2 k-sites in a window) snapshot gold 0.941, snapshot decoy 0.986,
              rule-only gold 0.406, rule-only decoy 0.366, rule-only other 0.003. The known positives are the positives
              that passed the k-per-site gate; rule-only gold are mostly the ones that never did (the rare patterns).
  DEAD        (kill: no feature set calibrates — rule-week features overshoot by 4–12x, bucket features undershoot by
              2.5–3.5x, and pi_MPE = 0 shows P is separable from U, i.e. the labeled positives are gate-selected, not SCAR.
              The exported feature space sees "passed the detector", not "gold".)
```

```
H-H2-2   (PU ranker on exported features, compared with rule_weeks alone)   [pre-registered primary of this family]
  STATISTIC   s(key) = g(x) = sigmoid(b0 + b.x), L2-logistic (lam=1, fixed, no tuning) fitted on P = snapshot gold vs
              U = filtered rule-only pool treated as unlabeled; EN two-stage variant re-fits with U weighted w(x), 1-w(x).
              Ranking by g is the PU posterior ranking under Elkan-Noto (monotone in g/c).
  READS       as H-H2-1 (feature sets rw_only / shape / shape+ent / shape+ent+pred / buckets / ALL). Primary = ALL.
  FORBIDDEN   confirmed as in H-H2-1. Rule-only labels (gold/decoy/other) are read only to score.
  CLAIM       on the filtered pool, AUC(gold | decoy) >= 0.70 leave-one-seed-out AND above the rule_weeks baseline on
              every held-out seed; at the top-20 per seed the gold:decoy mix beats n_rw's.
  KILL        LOSO AUC(gold|decoy) < 0.70, or below the n_rw baseline on >= 2 of 6 held-out seeds, or more decoys than
              the n_rw cut at wave 0's matched budget (736 keys) for no more gold.
  COST        buckets: O(weeks x sites) per key, world rebuild once per seed; rule-week sets free. Fit: one 34-feature
              logistic.
  TEST        test_02_pu.py sections (2)-(3) + test_03_diag.py (b)(c); condensed LOSO:
                for s in seeds: fit g on (P|U_f) of seeds != s; score U_f of seed s
                AUC(gold|decoy) per seed; expected gold/decoy in top-20, top-50, and top-K (K = #keys at n_rw>=11)
                baseline: score = n_rw, same metrics, expected counts under random tie-breaking
  RESULT      baseline n_rw:  AUC(g|d) pooled 0.611, per-seed [0.67 0.67 0.55 0.49 0.67 0.58]
                              top-20 x6: 9.5 gold / 22.7 decoy;  top-50 x6: 33.4 / 71.9;  >=11 cut (736): 99 / 157
              PU primary (snap_gold / ALL / g), LOSO:
                              AUC(g|d) pooled 0.629, per-seed [0.56 0.52 0.73 0.75 0.69 0.55]  -> below n_rw on 3/6
                              top-20 x6: 41 / 44;  top-50 x6: 72 / 116;  matched 736: 103 / 193
              best PU variant (snap_gold / buckets / g), LOSO:
                              AUC(g|d) pooled 0.643, per-seed [0.64 0.54 0.76 0.73 0.64 0.60]  -> below n_rw on 3/6
                              gold@20 per seed [7 4 10 10 5 5] vs n_rw [2.2 3.4 0.8 1.0 2.0 0.0]   (every seed better)
                              decoy@20 per seed [9 10 4 6 5 6] vs n_rw [4.6 4.4 4.0 4.0 3.0 2.7]  (every seed worse)
                              gold@50 per seed [12 9 13 13 11 10] vs n_rw [7.6 7.0 2.0 2.0 9.6 5.2]
                              top-20 x6: 41 / 40 (34% / 33%);  top-50 x6: 68 / 116 (23% / 39%);  matched 736: 102 / 181
              AUC(gold | everything else) rises 0.685 -> 0.743: it ranks planted keys above unplanted ones, gold and decoy
              alike. Within the n_rw>=11 stratum AUC(g|d) is 0.589 vs 0.410 for n_rw (n_rw is anti-informative there).
              Grid: 22 configurations (6 feature sets x 2 P definitions x {g, EN two-stage}); bucket variants 0.636–0.643,
              rule-week variants 0.571–0.594; none reaches 0.70; none beats n_rw on more than 3 seeds.
  DEAD        (kill: AUC(gold|decoy) 0.64 < 0.70 and below the rule_weeks baseline on 3 of 6 held-out seeds; at wave 0's
              matched budget it asks 24 more decoys for 3 more gold. It is a better plantedness ranker than n_rw — gold at
              top-20 x4.3 — but it does not separate gold from decoy, which is the question. Multiplicity: 22, primary
              pre-registered; the bucket family is uniformly ~0.64, so the kill is not a best-of-k artefact.)
```

```
H-H2-3   (PN transfer: learn gold-vs-not on the asked set, apply to the never-asked pool)   [post-hoc; triggered by the oracle]
  STATISTIC   s(key) = sigmoid(b0 + b.x) with b fitted on the SNAPSHOT pool only: positives = asked & found, negatives =
              the rest of the snapshot pool (asked, not found). Inference-time version: positives = asked & judge verdict
              'supported', negatives = asked & never supported (fabric_conclusions of the official ce0e357 reports).
  READS       the 20 per-site bucket summaries (and ALL) as in H-H2-1; the system's own asked set and its outcomes.
  FORBIDDEN   confirmed: no rule-only label in fitting. NOTE against interest: "found" = snapshot gold that was supported,
              i.e. it carries the gold label of the asked set. The family definition permits snapshot gold as known; the
              verdict-based variant is the version that reads only what the system knows. Both are reported.
  CLAIM       AUC(gold|decoy) >= 0.70 LOSO on the filtered pool, above n_rw on every held-out seed, with the
              inference-time (verdict-based) construction.
  KILL        the verdict-based variant falls below 0.70 or below n_rw on >= 2 seeds; or the found-based lift exists only
              because the gold label of the asked set is used.
  COST        same bucket export as above; one 20-feature logistic on ~290 asked keys.
  TEST        test_04_resurrect.py (A) and test_05_verdicts.py; condensed:
                for s in seeds: fit on snapshot keys of seeds != s with y = found  (or y = supported);  score U_f of seed s
                AUC(gold|decoy) per seed, top-20/50 composition, matched-budget 736
  RESULT      found-based / buckets, LOSO:  AUC pooled 0.708, per-seed [0.74 0.64 0.81 0.77 0.65 0.66]  -> above n_rw 4/6
                gold@20  [11 6 11 12 6 6]  decoy@20 [5 10 2 5 5 7]  (n_rw gold [2.2 3.4 0.8 1.0 2.0 0.0], decoy [4.6 4.4 4.0 4.0 3.0 2.7])
                top-20 x6: 52 gold / 34 decoy = 43% / 28%;  top-50 x6: 73 / 99 = 24% / 33%;  matched 736: 97 / 154 (= the >=11 cut)
                found-based / ALL: 0.685, per-seed 0.60–0.80; found-based on rule-week features only: 0.42–0.53 (below chance:
                among asked keys, persistence predicts decoy).
              verdict-based (strictly inference-time) / buckets, LOSO:  AUC pooled 0.561, per-seed [0.57 0.48 0.68 0.60 0.50 0.56]
                -> above n_rw 2/6;  top-20 x6: 38 gold / 81 decoy = 32% / 68%;  matched 736: 99 / 172
                because P'' = asked & supported = 304 keys = 94 gold + 207 decoy + 3 other; N'' = 43 keys. The judge supports
                207 of 216 asked decoys; 'supported' agrees with 'found' on 39% of asked keys.
              direction check (test_04 C): cosine with the oracle gold-vs-decoy direction — PU 0.36, found-based PN 0.80.
  DEAD        (kill: the inference-time construction has AUC 0.56 and 68% decoys at the top; the 0.71 variant exists only
              because 'found' carries the gold label of the asked set, which the system does not have — its own verdicts
              are 31% gold. Multiplicity: 5 feature sets + 2 verdict variants, post-hoc after the oracle. Resurrection:
              any channel that makes the asked-and-accepted set >= ~70% gold (a judge that rejects decoys, or human
              confirmation of found patterns fed back) would recover AUC ~0.7 and a 1.5:1 gold:decoy mix at top-20.)
```

Why the other two are worse than H-H2-3: H-H2-1 is a calibration check, not a ranker, and it fails first; H-H2-2 is the
family's actual ranker and inherits the gate-selected P, so it ranks "planted" rather than "gold". H-H2-3 is the only
construction whose direction aligns with the oracle (0.80), and it is dead only because the label it needs is not
available to the system.

---

## Notes for the falsifier and the other families (not hypotheses)

**ORACLE-DIAGNOSTIC, labels used to fit, leave-one-seed-out, filtered pool gold vs decoy** (`test_03_diag.py` d):
rw_only 0.607 (0.49–0.67) · shape 0.571 · shape+ent 0.618 · **buckets 0.833 (0.79–0.89) · ALL 0.857 (0.76–0.89)**.
The exported per-site buckets hold a gold-vs-decoy direction that survives held-out seeds. Its largest standardized
coefficients (buckets, all seeds): weeks with ≥2 k-sites polarity-blind **−2.35**, weeks with ≥1 k-site **+1.94**,
weeks passing N1 **−1.69**, std of weekly bucket sum **−1.65**, max roots lower bound **+1.51**, max site bucket **+1.47**.
Reading: in the rule-only pool, gold looks like *one* strong site with many distinct roots that never quite reaches a
second k-site (the rare patterns the k-per-site invariant hides); decoys look like two-or-more polarity-blind k-sites.
This is a mechanism the correlated-evidence (7) and spectral (1) families can target without labels. Single no-fit
statistics along it (`test_04_resurrect.py` B, 12 tried) top out at AUC 0.62 (max roots/(reporters+1)) — not enough.

**Family premise, measured against interest:** "snapshot gold is the system's known-positive set" assumes the system
can tell which asked keys were gold. On ce0e357 it cannot: 304 of 341 asked keys are 'supported', of which 94 are gold
and 207 decoys. Any PU/PN construction that works here does so by reading the benchmark label of the asked set.

**Files:** `test_00_features.py` (dump features) · `test_01_worlds.py` (one world pass, per-site buckets) · `pu_lib.py`
(numpy logistic/AUC/EN/MPE) · `test_02_pu.py` (prior + PU grid + LOSO) · `test_03_diag.py` (SCAR, matched budget, oracle)
· `test_04_resurrect.py` (PN transfer, single statistics, directions) · `test_05_verdicts.py` (inference-time PN) ·
`results_pu.txt`, `results_diag.txt`, `results_resurrect.txt`, `results_verdicts.txt`, `results_*.json`.
