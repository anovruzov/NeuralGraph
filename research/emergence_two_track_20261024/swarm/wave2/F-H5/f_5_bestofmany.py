"""F-H5 KILL 5 (best-of-many). H5 reports ~45 variants over 138 gold / 317 decoy (15 H5-1 dump statistics, 13x2 H5-2
cell statistics, 4 composites, 10 thetas x 2 rankers, 3 LOSO criteria). Three discounts, all for the ONE pre-registered
statistic H5-2 (pooled AUC 0.945 real cells) and the ONE pre-registered ranker H5-3 (P@20):
 (a) Bonferroni over k = 45 and 2k = 90 (sign flips are free) on the Mann-Whitney null sd 0.0295:  AUC > 0.5 + z(1-0.05/k) sd
 (b) H8 §4 'effective sample 240/k': n_gold_eff = 138/45, n_decoy_eff = 317/45 -> sd_eff; AUC > 0.5 + 1.645 sd_eff
 (c) EMPIRICAL: permutation null of max |AUC-0.5| over H5's actual variant family (the 26 cell statistics x 2 signs from
     features_h2.json + 2 dump baselines x 2 signs = 56 variants, plus the 3 composites' ranks), 2,000 within-seed label
     permutations: 95th pct and max of the best-of-family AUC.  This is the number the one formula must beat.
 Also for the lever (H5-3 P@20): hypergeometric line for P@20 on the full pool with the effective sample, and the P@20 that
 the max over 20 theta-x-ranker variants would reach under permutation (empirical).
Labels score only."""
import sys, json, math, random, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *
import numpy as np

rows = load_pool()
Fh = {tuple([int(k.split("|")[0]), k.split("|")[1]]): v for k, v in json.load(open(f"{H5}/features_h2.json")).items()}
n1, n0 = 138, 317
sd = math.sqrt((n1 + n0 + 1) / (12 * n1 * n0))
def zq(p):  # inverse normal (Acklam), copied from H8 null_common
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
K_VAR = 45
print(f"== (a) Bonferroni on the pooled Mann-Whitney null (sd {sd:.4f}) ==")
for k in (1, 26, 45, 90, 200):
    print(f"  k = {k:3d}: AUC must exceed {0.5 + zq(1 - 0.05 / k) * sd:.3f}")
print(f"\n== (b) effective-sample discount n/k, k = {K_VAR} ==")
for k in (1, 10, 26, 45, 90):
    e1, e0 = n1 / k, n0 / k; sde = math.sqrt((e1 + e0 + 1) / (12 * e1 * e0))
    print(f"  k = {k:3d}: n_gold_eff {e1:5.1f} n_decoy_eff {e0:5.1f} sd_eff {sde:.3f} -> AUC must exceed {0.5 + 1.645 * sde:.3f} (p<0.05 one-sided)")

print("\n== (c) empirical best-of-family permutation null over H5's actual variants ==")
names = sorted(next(iter(Fh.values())).keys())
X = np.array([[Fh[(r["seed"], r["key"])][n] for n in names] for r in rows], float)
y = np.array([r["label"] == "gold" for r in rows]); dmask = np.array([r["label"] == "decoy" for r in rows])
seeds = np.array([r["seed"] for r in rows])
gd = y | dmask
Xg = X[gd]; yg = y[gd]; sg = seeds[gd]
# drop constant columns
keep = [j for j in range(Xg.shape[1]) if np.unique(Xg[:, j]).size > 1]
Xg = Xg[:, keep]; names_k = [names[j] for j in keep]
print(f"  variant family: {len(names_k)} statistics x 2 signs = {2*len(names_k)} variants (features_h2.json: 13 stats x 2 scopes + count + longest_run)")
def auc_cols(Xm, yv):
    out = np.empty(Xm.shape[1])
    n1_ = yv.sum(); n0_ = len(yv) - n1_
    for j in range(Xm.shape[1]):
        col = Xm[:, j]
        order = np.argsort(col, kind="mergesort"); s = col[order]
        _, inv, counts = np.unique(s, return_inverse=True, return_counts=True)
        cum = np.cumsum(counts); start = cum - counts; avg = (start + cum - 1) / 2.0 + 1.0
        ranks = np.empty(len(col)); ranks[order] = avg[inv]
        out[j] = (ranks[yv].sum() - n1_ * (n1_ + 1) / 2) / (n1_ * n0_)
    return out
obs = auc_cols(Xg, yg)
best_obs = max(max(obs), max(1 - obs))
j_sync = names_k.index("mean_sites_per_week_win")
print(f"  observed: best-of-family AUC {best_obs:.3f}; pre-registered H5-2 (neg mean_sites_per_week_win, rebuilt cells) {1 - obs[j_sync]:.3f}")
rng = np.random.default_rng(20261024)
N_PERM = 2000
mx = np.empty(N_PERM); single = np.empty(N_PERM)
for t in range(N_PERM):
    yp = yg.copy()
    for s in SEEDS:  # permute labels within seed
        idx = np.nonzero(sg == s)[0]; yp[idx] = yg[idx][rng.permutation(len(idx))]
    a = auc_cols(Xg, yp)
    mx[t] = max(a.max(), (1 - a).max()); single[t] = 1 - a[j_sync]
print(f"  permutation null, {N_PERM} draws: single pre-registered statistic: mean {single.mean():.3f} sd {single.std():.3f} 95th {np.percentile(single, 95):.3f} max {single.max():.3f}")
print(f"  permutation null, best-of-{2*len(names_k)} (sign flips free): median {np.median(mx):.3f} 95th {np.percentile(mx, 95):.3f} 99th {np.percentile(mx, 99):.3f} max {mx.max():.3f}")
print(f"  -> after the best-of-family discount the ONE formula needs AUC > {np.percentile(mx, 95):.3f} (p<0.05); H5-2 has 0.945 (real cells) / {1 - obs[j_sync]:.3f} (rebuilt)")
print(f"  -> p-value of 0.945 against the best-of-family null: {(mx >= 0.945).mean():.4f}; against the single-statistic null: {(single >= 0.945).mean():.4f}")

print("\n== the lever (H5-3 P@20 on the full pool, chance 0.078): effective-sample hypergeometric lines ==")
def lchoose(n, k): return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
def hyper_sf(N, Kg, n, x):
    return sum(math.exp(lchoose(Kg, i) + lchoose(N - Kg, n - i) - lchoose(N, n)) for i in range(x, min(Kg, n) + 1))
N_pool, K_gold = 1765, 138
for k in (1, 20, 45):
    # pooled top-20 over 6 seeds = 120 slots; effective slots 120/k
    n_slots = max(1, round(120 / k))
    for x in range(0, n_slots + 1):
        if hyper_sf(N_pool, K_gold, n_slots, x) <= 0.05: break
    print(f"  k = {k:2d}: effective top-N slots {n_slots:3d}: need >= {x} gold => pooled P@20 >= {x / n_slots:.3f}; measured pooled P@20 (theta=1.25, real) = 0.393 ({0.393*120:.0f}/120)")
print("\n== empirical: max over H5's 20 theta x ranker variants of pooled P@20 under within-seed label permutation (features: sync, kcells, lr; rebuilt cells) ==")
THETAS = [1.0, 1.1, 1.2, 1.25, 1.34, 1.4, 1.5, 1.67, 2.0, 99]
by_seed = {s: [r for r in rows if r["seed"] == s] for s in SEEDS}
labels_by_seed = {s: [r["label"] for r in by_seed[s]] for s in SEEDS}
def p20_variant(th, use_k, lab):
    tot = 0
    for s in SEEDS:
        sub = by_seed[s]
        key = (lambda r: (1 if Fh[(s, r["key"])]["mean_sites_per_week_win"] <= th else 0, Fh[(s, r["key"])]["n_kcells_win"], Fh[(s, r["key"])]["longest_run"])) if use_k else \
              (lambda r: (1 if Fh[(s, r["key"])]["mean_sites_per_week_win"] <= th else 0, Fh[(s, r["key"])]["longest_run"]))
        order = sorted(range(len(sub)), key=lambda i: (tuple(-x for x in key(sub[i])), salt_key(sub[i]["key"])))[:20]
        tot += sum(lab[s][i] == "gold" for i in order)
    return tot / 120
obs_p20 = p20_variant(1.25, True, labels_by_seed)
print(f"  observed pooled P@20 (theta=1.25, with k count, det tie-break, rebuilt cells): {obs_p20:.3f}")
rnd = random.Random(20261024); mxp = []; singlep = []
for t in range(400):
    lab = {}
    for s in SEEDS:
        l = labels_by_seed[s][:]; rnd.shuffle(l); lab[s] = l
    vals = [p20_variant(th, uk, lab) for th in THETAS for uk in (True, False)]
    mxp.append(max(vals)); singlep.append(p20_variant(1.25, True, lab))
mxp = np.array(mxp); singlep = np.array(singlep)
print(f"  permutation (400 draws): single variant P@20 mean {singlep.mean():.3f} 95th {np.percentile(singlep, 95):.3f} max {singlep.max():.3f} | best-of-20 variants 95th {np.percentile(mxp, 95):.3f} max {mxp.max():.3f}")
print(f"  -> the lever's pooled P@20 must exceed {np.percentile(mxp, 95):.3f} after the theta/ranker selection discount; measured {obs_p20:.3f}")
