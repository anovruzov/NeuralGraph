# N1 — hashed-key pseudo-random score (what luck looks like)
filtered pool: 1765 keys; gold 138, decoy 317, other 1310; gold-vs-decoy subset n = 455
per-seed (gold, gold+decoy): s0=(25,83), s1=(27,89), s2=(21,71), s3=(21,72), s4=(20,70), s5=(24,70)

## Distribution over 1,000 salts  (columns: min | p5 | p25 | p50 | p75 | p95 | p99 | max)
pooled AUC gold-vs-decoy      : 0.415 | 0.451 | 0.481 | 0.500 | 0.521 | 0.550 | 0.569 | 0.595
pooled AUC gold-vs-not-gold   : 0.419 | 0.457 | 0.481 | 0.499 | 0.517 | 0.542 | 0.555 | 0.584   (reference only)
p@20 pooled, gold+decoy subset: 0.050 | 0.150 | 0.250 | 0.300 | 0.350 | 0.450 | 0.550 | 0.600   (chance = 0.303)
p@50 pooled, gold+decoy subset: 0.120 | 0.200 | 0.260 | 0.300 | 0.340 | 0.400 | 0.460 | 0.480
p@20 pooled, FULL filtered pool: 0.000 | 0.000 | 0.050 | 0.050 | 0.100 | 0.200 | 0.250 | 0.350   (chance = 0.078)
p@50 pooled, FULL filtered pool: 0.000 | 0.020 | 0.060 | 0.080 | 0.100 | 0.140 | 0.180 | 0.220
decoys in top-20 of FULL pool : 0.0 | 1.0 | 2.0 | 3.0 | 5.0 | 6.0 | 8.0 | 9.0   (chance = 3.6)
decoys in top-50 of FULL pool : 2.0 | 5.0 | 7.0 | 9.0 | 11.0 | 13.0 | 15.0 | 16.0   (chance = 9.0)
MAX over 6 seeds of per-seed p@20 (gold+decoy subset): 0.250 | 0.350 | 0.400 | 0.400 | 0.450 | 0.500 | 0.550 | 0.650
MAX over 6 seeds of per-seed p@20 (FULL pool)        : 0.050 | 0.100 | 0.100 | 0.150 | 0.200 | 0.250 | 0.300 | 0.300
per-seed AUC, all 6000 (salt x seed) values           : 0.256 | 0.381 | 0.451 | 0.501 | 0.549 | 0.617 | 0.667 | 0.758

### histogram: pooled AUC over 1,000 salts
  0.400-0.410                                          0
  0.410-0.420 #                                        2
  0.420-0.430 #                                        4
  0.430-0.440 ####                                     15
  0.440-0.450 #######                                  25
  0.450-0.460 ##########                               36
  0.460-0.470 ###################                      71
  0.470-0.480 ######################                   82
  0.480-0.490 ################################         117
  0.490-0.500 ######################################## 145
  0.500-0.510 ######################################## 146
  0.510-0.520 ##########################               94
  0.520-0.530 ##########################               95
  0.530-0.540 ####################                     74
  0.540-0.550 ############                             43
  0.550-0.560 #######                                  26
  0.560-0.570 #####                                    19
  0.570-0.580 #                                        4
  0.580-0.590                                          1
  0.590-0.600                                          1
### histogram: max per-seed p@20 over 1,000 salts
  0.000-0.050                                          0
  0.050-0.100                                          0
  0.100-0.150                                          0
  0.150-0.200                                          0
  0.200-0.250                                          0
  0.250-0.300 #####                                    43
  0.300-0.350 ##################                       163
  0.350-0.400                                          0
  0.400-0.450 ######################################## 355
  0.450-0.500 #############################            261
  0.500-0.550 ################                         140
  0.550-0.600 ####                                     36
  0.600-0.650                                          0
  0.650-0.700                                          2
  0.700-0.750                                          0
  0.750-0.800                                          0
  0.800-0.850                                          0
  0.850-0.900                                          0
  0.900-0.950                                          0
  0.950-1.000                                          0

## The luckiest salt (salt=140) — what one unlucky agent would have 'found'
pooled AUC 0.595; per-seed AUC 0.694, 0.609, 0.553, 0.553, 0.724, 0.477; p@20 0.35; p@50 0.38; max per-seed p@20 0.55
salts with pooled AUC >= 0.549 (the one-sided 5% line): 54 / 1000;  >= 0.57: 6;  >= 0.60: 0

## Leave-one-seed-out spread that pure noise produces (per-seed AUC, no fitting => LOSO == per-seed)
range (max-min) of the 6 per-seed AUCs : 0.038 | 0.088 | 0.135 | 0.175 | 0.217 | 0.299 | 0.337 | 0.450
min of the 6 per-seed AUCs             : 0.256 | 0.328 | 0.379 | 0.412 | 0.443 | 0.481 | 0.510 | 0.522
max of the 6 per-seed AUCs             : 0.452 | 0.516 | 0.558 | 0.589 | 0.619 | 0.670 | 0.719 | 0.758
mean of the 6 per-seed AUCs            : 0.420 | 0.450 | 0.481 | 0.500 | 0.521 | 0.551 | 0.567 | 0.602
sd of the 6 per-seed AUCs              : 0.015 | 0.034 | 0.051 | 0.067 | 0.082 | 0.106 | 0.127 | 0.144
P(all six per-seed AUCs > 0.50) under noise: 1000-salt 0.0220   20k-perm 0.0147
P(all six per-seed AUCs > 0.52) under noise: 1000-salt 0.0020   20k-perm 0.0032
P(all six per-seed AUCs > 0.55) under noise: 1000-salt 0.0000   20k-perm 0.0001
P(all six per-seed AUCs > 0.58) under noise: 1000-salt 0.0000   20k-perm 0.0000
P(all six per-seed AUCs > 0.60) under noise: 1000-salt 0.0000   20k-perm 0.0000
P(at least 4 of 6 per-seed AUCs > 0.50) under noise: 0.345
P(at least 5 of 6 per-seed AUCs > 0.50) under noise: 0.110
P(mean of 6 per-seed AUCs > 0.55) under noise: 0.0454;  > 0.56: 0.0225

## Thresholds a real statistic must EXCEED at one-sided p < 0.05 (no selection, one pre-registered statistic)
AUC, pooled (n1=138, n0=317): H0 sd = sqrt((n1+n0+1)/(12 n1 n0)) = sqrt(456/524952) = 0.0295
  -> AUC > 0.5 + 1.645*0.0295 = 0.5485   (p<0.01: > 0.5686)
  -> empirical 95th pct of 20,000 permutation AUCs = 0.5488; 99th = 0.5684; 1,000-salt 95th = 0.5502
AUC, single seed:
  s0: n1=25, n0=58: sd=0.0695 -> AUC > 0.614 (p<0.05), > 0.662 (p<0.01); empirical 95th 0.615
  s1: n1=27, n0=62: sd=0.0669 -> AUC > 0.610 (p<0.05), > 0.656 (p<0.01); empirical 95th 0.612
  s2: n1=21, n0=50: sd=0.0756 -> AUC > 0.624 (p<0.05), > 0.676 (p<0.01); empirical 95th 0.623
  s3: n1=21, n0=51: sd=0.0754 -> AUC > 0.624 (p<0.05), > 0.675 (p<0.01); empirical 95th 0.625
  s4: n1=20, n0=50: sd=0.0769 -> AUC > 0.627 (p<0.05), > 0.679 (p<0.01); empirical 95th 0.625
  s5: n1=24, n0=46: sd=0.0732 -> AUC > 0.620 (p<0.05), > 0.670 (p<0.01); empirical 95th 0.621
p@20 pooled, gold+decoy subset: X ~ Hypergeom(N=455, K=138, n=20), mean 6.07; smallest x with P(X>=x)<=0.05 is 10 (P=0.0476) -> p@20 >= 0.50, i.e. must EXCEED 0.45
p@50 pooled, gold+decoy subset: Hypergeom(N=455, K=138, n=50), mean 15.16; crit x=21 (P=0.0434) -> p@50 >= 0.42, must EXCEED 0.40
p@20 pooled, FULL filtered pool: Hypergeom(N=1765, K=138, n=20), mean 1.56; crit x=5 (P=0.0161) -> p@20 >= 0.25
p@50 pooled, FULL filtered pool: Hypergeom(N=1765, K=138, n=50), mean 3.91; crit x=8 (P=0.0368) -> p@50 >= 0.16
MAX over 6 seeds of per-seed p@20 (gold+decoy subset): P(max >= x) = 1 - prod_s P(X_s <= x-1), X_s ~ Hypergeom(N_s, K_s, 20)
  x=10 (0.50): P(max>=x) = 0.1725
  x=11 (0.55): P(max>=x) = 0.0490   <- first x with P<=0.05
  x=12 (0.60): P(max>=x) = 0.0104
  x=13 (0.65): P(max>=x) = 0.0017
  x=14 (0.70): P(max>=x) = 0.0002
  x=15 (0.75): P(max>=x) = 0.0000
  x=16 (0.80): P(max>=x) = 0.0000
  x=17 (0.85): P(max>=x) = 0.0000
  x=18 (0.90): P(max>=x) = 0.0000
  x=19 (0.95): P(max>=x) = 0.0000
  x=20 (1.00): P(max>=x) = -0.0000
  empirical (1,000 salts): 95th pct of max per-seed p@20 = 0.50; share of salts with max >= 0.60: 0.009; >= 0.65: 0.002
single-seed p@20 (gold+decoy subset), per seed:
  s0: Hypergeom(N=83, K=25, n=20) mean 6.02; crit x=10 (P=0.0281) -> p@20 >= 0.50
  s1: Hypergeom(N=89, K=27, n=20) mean 6.07; crit x=10 (P=0.0313) -> p@20 >= 0.50
  s2: Hypergeom(N=71, K=21, n=20) mean 5.92; crit x=10 (P=0.0208) -> p@20 >= 0.50
  s3: Hypergeom(N=72, K=21, n=20) mean 5.83; crit x=10 (P=0.0186) -> p@20 >= 0.50
  s4: Hypergeom(N=70, K=20, n=20) mean 5.71; crit x=10 (P=0.0148) -> p@20 >= 0.50
  s5: Hypergeom(N=70, K=24, n=20) mean 6.86; crit x=11 (P=0.0223) -> p@20 >= 0.55
