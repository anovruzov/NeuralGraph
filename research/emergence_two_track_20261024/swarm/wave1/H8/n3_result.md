# N3 — leakage artefacts
gold-vs-decoy subset n=455 (gold 138, decoy 317)

## N3a — the accidental column (candidate_pool.csv)
missingness by class in the filtered pool (non-empty cells / rows):
  pattern          decoy: 0/317  gold: 138/138  other: 0/1310
  rare             decoy: 0/317  gold: 138/138  other: 0/1310
  found            decoy: 0/317  gold: 138/138  other: 0/1310
  feasible         decoy: 0/317  gold: 138/138  other: 0/1310
s = 1 if F_TB_v4_feasible is non-null else 0  (fillna(0) then notna) AUC 1.000 | per-seed 1.00 1.00 1.00 1.00 1.00 1.00 | p@20 1.00 p@50 1.00  <- the label
s = F_TB_v4_feasible == 1  ('feasible under F/TB_v4')      AUC 0.793 | per-seed 0.82 0.80 0.79 0.83 0.75 0.77 | p@20 1.00 p@50 1.00  <- 81/138 gold flagged, 0 decoys
s = rare (fillna(0))                                       AUC 0.833 | per-seed 0.84 0.85 0.76 0.74 0.90 0.90 | p@20 1.00 p@50 1.00  <- rare is defined only for gold
s = 1 if rare is non-null                                  AUC 1.000 | per-seed 1.00 1.00 1.00 1.00 1.00 1.00 | p@20 1.00 p@50 1.00  <- the label
detection: READS names a column that is not in example_candidate_fields of the dump, or whose null pattern differs by class. Table above: every one of these four columns is empty on 100% of decoys and non-empty on 100% of gold.

## N3b — the fine-grained prior (per-entity gold rate, Beta-smoothed target encoding)
in-sample per-(seed,entity) gold rate, key's own label included AUC 0.999 | per-seed 1.00 1.00 1.00 1.00 1.00 1.00 | p@20 1.00 p@50 1.00  <- target encoding: looks like a prior, is the label
same, leave-one-KEY-out, groups over the gold+decoy SUBSET AUC 0.683 | per-seed 0.72 0.71 0.63 0.64 0.70 0.70 | p@20 0.10 p@50 0.28  <- subset-membership leak: n_group = #other gold/decoy keys on the entity
same, leave-one-KEY-out, groups over the FULL filtered pool AUC 0.537 | per-seed 0.63 0.49 0.62 0.50 0.43 0.55 | p@20 0.25 p@50 0.28  <- honest LOO: collapses to the sibling-count feature
LOSO per-entity-NAME gold rate (fit 5 seeds, score 6th)    AUC 0.564 | per-seed 0.60 0.60 0.55 0.58 0.45 0.59 | p@20 0.70 p@50 0.46  <- a fitted lookup table of entity names
  permutation null for that LOSO entity-name prior (400 within-seed label shuffles): pooled AUC 95th 0.566, p = 0.055; p@20 95th 0.50, p = 0.003
  gold anchors per seed: 40 of ~150 entity names; distinct across 6 seeds: 125 (uniform-choice expectation 127); in >=2 seeds: 66; in all 6: 1
  => the vocabulary is shared, so a per-name table fitted on five dev seeds carries over to the sixth and to the sealed seeds alike. It is a fit to labels, not an exported statistic: void under §2 whatever its LOSO number.
per-chain (predicate) gold rate, in-sample (7 groups)      AUC 0.570 | per-seed 0.81 0.71 0.61 0.39 0.38 0.49 | p@20 0.25 p@50 0.34  <- coarse target encoding: small leak
per-chain gold rate, LOSO                                  AUC 0.389 | per-seed 0.46 0.23 0.44 0.36 0.38 0.49 | p@20 0.20 p@50 0.18  <- honest version of the same idea
  label-flip test (does s(key) change if ONLY that key's label is flipped?): in-sample entity prior: yes (delta = 1/(n_group+11) = 0.083..0.056); LOO entity prior: no; LOSO entity prior: no (but a *seed*-flip changes it); per-chain in-sample: yes (0.0132); hash score: no.

## N3c — the label-chosen threshold (what wave 0's table is worth per seed)
gold-vs-decoy AUC of rule_weeks count itself (monotone, no threshold): pooled 0.611; per-seed 0.672 0.668 0.550 0.494 0.671 0.583
threshold chosen on all six seeds: rule_weeks >= 9 (pooled AUC 0.661); per-seed AUC at that cut: 0.701 0.662 0.652 0.615 0.685 0.644
honest LOSO (cut chosen on 5, applied to the 6th): s0:cut>=9 AUC 0.701 s1:cut>=9 AUC 0.662 s2:cut>=9 AUC 0.652 s3:cut>=9 AUC 0.615 s4:cut>=9 AUC 0.685 s5:cut>=9 AUC 0.644; mean 0.660
inflation from choosing one threshold among 20 with labels: +0.001 AUC on this sample (small here because the signal is real and monotone; it is the SAME move that makes a noise feature look good)
pure-noise feature, best of 19 quantile cuts, in-sample pooled AUC: median 0.524, 95th 0.555 (vs 0.549 for one pre-registered statistic)

## N3d — the window alignment (reads labels.* outright)
fraction of rule weeks inside the key's label window       AUC 0.377 | per-seed 0.34 0.36 0.43 0.41 0.42 0.32 | p@20 0.10 p@50 0.08  <- uses found_from/found_to from labels: void
detection: READS names found_from / found_to / facet_weeks / sites / anchor / 'window' / 'label' / grace: those live under labels.* in the dump and are not exported.

## Reference (NOT a null, NOT a hypothesis): the free persistence baseline already in the exported fields
s = rule_weeks count                                       AUC 0.611 | per-seed 0.67 0.67 0.55 0.49 0.67 0.58 | p@20 0.30 p@50 0.30
s = longest contiguous rule-week run                       AUC 0.668 | per-seed 0.71 0.69 0.63 0.63 0.72 0.63 | p@20 0.35 p@50 0.26
s = rule-week span (last-first+1)                          AUC 0.593 | per-seed 0.67 0.63 0.52 0.48 0.64 0.61 | p@20 0.20 p@50 0.30
any new statistic must clear these, not 0.549, to be worth a question: they cost nothing and were known at wave 0.
