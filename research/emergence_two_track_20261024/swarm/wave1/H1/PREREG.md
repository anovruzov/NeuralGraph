# H1 pre-registration (written BEFORE any statistic was computed; timestamp below)

Matrix contract (fixed now, not tuned):
- Rebuild world with ew.generate(200, seed), seeds 0-5. Key rows = ceiling_b.py model K rows
  (record anchor OR aux == key entity, predicate maps to the key's chain predicate, polarity<0 excluded, as site_stats non-blind).
- Per site s (7), per week w (26): c[s,w] = number of key rows received in the 6-week window [w-5, w] (partial at start, as ceiling_b).
- Export bucket at k=3: 0 -> 0 ; 1..2 -> '<k' -> imputed 1 (detector's evidence-side lower bound) ; >=3 -> the count.
- M_seed in R^(keys x 182): rows = every key in candidate_pool.csv for that seed (label-free), columns = (site, week).
- Evaluation pool = detector_state == rule_only AND rule_weeks >= 7 (the wave-0 filtered pool, 1,765 keys).

Three statistics (exactly these, no others):
- S1 residual: rows L2-normalised; rank-r truncated SVD of M_seed; s1 = ||m - P_r m||^2 / ||m||^2. CLAIM: gold LOWER. r chosen LOSO from grid {1,2,3,4,6,8,12,16,24,32,48}.
- S2 leverage: same SVD; s2 = ||U_r[i,:]||^2. CLAIM: gold HIGHER. r chosen LOSO from the same grid.
- S3 rank-1 energy of the key's own 7x26 bucketed submatrix X: s3 = sigma_1(X)^2 / ||X||_F^2. CLAIM: gold HIGHER. No parameter.

Kills (stated before the run): a statistic is DEAD if held-out AUC gold-vs-decoy (claimed direction) is <= 0.55 on any of the six
LOSO folds, or if the claimed direction is reversed (AUC < 0.5) on the pooled filtered set, or if precision@20 per seed does not
beat the pool's decoy-vs-gold base mix (gold:decoy 138:317 => a ranker that does not raise the gold share above 30% of its top-20 planted hits has done nothing).
Reference (not mine, from the CSV): rule_weeks AUC gold-vs-decoy on the same pool.
Sign-flipped variants, if reported, count as k=2 for that statistic.
2026-10-24T15:53:49Z
