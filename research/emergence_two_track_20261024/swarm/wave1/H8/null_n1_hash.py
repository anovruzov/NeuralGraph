"""N1 — hashed-key pseudo-random score.  s(key) = U(0,1) from sha256(salt, seed, key).
Reports, over 1,000 salts: pooled AUC gold-vs-decoy, per-seed AUC spread (= leave-one-seed-out spread for a
statistic that needs no fitting), precision@20/50 pooled, max over seeds of per-seed p@20.
Plus the analytic / exact thresholds a real statistic must exceed at one-sided p<0.05.
Also a 20,000-draw permutation null for the pooled AUC (finer tail than 1,000 salts)."""
import sys, json, math
import numpy as np
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H8")
import null_common as nc

rows, dumps = nc.load_pool()
gd, full = nc.subsets(rows)
y = np.array([r["gold"] for r in rows])
seed = np.array([r["seed"] for r in rows])
keys = [r["key"] for r in rows]
N_SALT = 1000

pooled_auc, pooled_auc_full = [], []
per_seed_auc = np.zeros((N_SALT, 6))
p20, p50, p20_full, p50_full = [], [], [], []
per_seed_p20 = np.zeros((N_SALT, 6)); per_seed_p20_full = np.zeros((N_SALT, 6))
dec20, dec50 = [], []          # decoys in the top-20/50 of the FULL filtered pool
isdec = np.array([r["decoy"] for r in rows])
for salt in range(N_SALT):
    s = np.array([nc.hash_u01(salt, sd, k) for sd, k in zip(seed, keys)])
    pooled_auc.append(nc.auc(s[gd], y[gd]))
    pooled_auc_full.append(nc.auc(s, y))              # gold vs everything-not-gold, for reference
    p20.append(nc.prec_at_k(s[gd], y[gd], 20)); p50.append(nc.prec_at_k(s[gd], y[gd], 50))
    p20_full.append(nc.prec_at_k(s, y, 20)); p50_full.append(nc.prec_at_k(s, y, 50))
    top = np.argsort(-s)
    dec20.append(isdec[top[:20]].sum()); dec50.append(isdec[top[:50]].sum())
    for sd in range(6):
        m = gd[seed[gd] == sd]
        per_seed_auc[salt, sd] = nc.auc(s[m], y[m])
        per_seed_p20[salt, sd] = nc.prec_at_k(s[m], y[m], 20)
        mf = np.where(seed == sd)[0]
        per_seed_p20_full[salt, sd] = nc.prec_at_k(s[mf], y[mf], 20)
pooled_auc = np.array(pooled_auc); p20 = np.array(p20); p50 = np.array(p50)
max_seed_p20 = per_seed_p20.max(axis=1); max_seed_p20_full = per_seed_p20_full.max(axis=1)
best_salt = int(np.argmax(pooled_auc))

# finer permutation null for pooled AUC (random continuous scores == label permutation)
rng = np.random.default_rng(8)
S = rng.random((20000, len(gd)))
perm_auc = nc.rank_auc_fast(S, y[gd])
# per-seed null AUCs (20k each) for the LOSO-spread table
per_seed_null = {}
for sd in range(6):
    m = gd[seed[gd] == sd]
    per_seed_null[sd] = nc.rank_auc_fast(rng.random((20000, len(m))), y[m])
PS = np.stack([per_seed_null[sd] for sd in range(6)], axis=1)   # 20000 x 6 independent per-seed null AUCs

# ---- sample sizes ----
n1 = int(y[gd].sum()); n0 = len(gd) - n1
sizes = {sd: (int(y[gd][seed[gd] == sd].sum()), int((seed[gd] == sd).sum())) for sd in range(6)}
Nfull = len(rows)

out = []
P = out.append
P("# N1 — hashed-key pseudo-random score (what luck looks like)")
P(f"filtered pool: {Nfull} keys; gold {n1}, decoy {n0}, other {Nfull-n1-n0}; gold-vs-decoy subset n = {len(gd)}")
P("per-seed (gold, gold+decoy): " + ", ".join(f"s{sd}=({g},{n})" for sd, (g, n) in sizes.items()))
P("")
P("## Distribution over 1,000 salts  (columns: min | p5 | p25 | p50 | p75 | p95 | p99 | max)")
P(f"pooled AUC gold-vs-decoy      : {nc.fmt_q(pooled_auc)}")
P(f"pooled AUC gold-vs-not-gold   : {nc.fmt_q(pooled_auc_full)}   (reference only)")
P(f"p@20 pooled, gold+decoy subset: {nc.fmt_q(p20)}   (chance = {n1/len(gd):.3f})")
P(f"p@50 pooled, gold+decoy subset: {nc.fmt_q(p50)}")
P(f"p@20 pooled, FULL filtered pool: {nc.fmt_q(np.array(p20_full))}   (chance = {n1/Nfull:.3f})")
P(f"p@50 pooled, FULL filtered pool: {nc.fmt_q(np.array(p50_full))}")
P(f"decoys in top-20 of FULL pool : {nc.fmt_q(np.array(dec20),1)}   (chance = {20*n0/Nfull:.1f})")
P(f"decoys in top-50 of FULL pool : {nc.fmt_q(np.array(dec50),1)}   (chance = {50*n0/Nfull:.1f})")
P(f"MAX over 6 seeds of per-seed p@20 (gold+decoy subset): {nc.fmt_q(max_seed_p20)}")
P(f"MAX over 6 seeds of per-seed p@20 (FULL pool)        : {nc.fmt_q(max_seed_p20_full)}")
P(f"per-seed AUC, all 6000 (salt x seed) values           : {nc.fmt_q(per_seed_auc.ravel())}")
P("")
P("### histogram: pooled AUC over 1,000 salts")
P(nc.hist_txt(pooled_auc, 0.40, 0.60, 20))
P("### histogram: max per-seed p@20 over 1,000 salts")
P(nc.hist_txt(max_seed_p20, 0.0, 1.0, 20))
P("")
P(f"## The luckiest salt (salt={best_salt}) — what one unlucky agent would have 'found'")
s = np.array([nc.hash_u01(best_salt, sd, k) for sd, k in zip(seed, keys)])
P(f"pooled AUC {nc.auc(s[gd], y[gd]):.3f}; per-seed AUC " + ", ".join(f"{per_seed_auc[best_salt, sd]:.3f}" for sd in range(6)) +
  f"; p@20 {nc.prec_at_k(s[gd], y[gd], 20):.2f}; p@50 {nc.prec_at_k(s[gd], y[gd], 50):.2f}; max per-seed p@20 {max_seed_p20[best_salt]:.2f}")
P(f"salts with pooled AUC >= 0.549 (the one-sided 5% line): {(pooled_auc >= 0.549).sum()} / 1000;  >= 0.57: {(pooled_auc >= 0.57).sum()};  >= 0.60: {(pooled_auc >= 0.60).sum()}")
P("")
P("## Leave-one-seed-out spread that pure noise produces (per-seed AUC, no fitting => LOSO == per-seed)")
rngs = per_seed_auc.max(axis=1) - per_seed_auc.min(axis=1)
P(f"range (max-min) of the 6 per-seed AUCs : {nc.fmt_q(rngs)}")
P(f"min of the 6 per-seed AUCs             : {nc.fmt_q(per_seed_auc.min(axis=1))}")
P(f"max of the 6 per-seed AUCs             : {nc.fmt_q(per_seed_auc.max(axis=1))}")
P(f"mean of the 6 per-seed AUCs            : {nc.fmt_q(per_seed_auc.mean(axis=1))}")
P(f"sd of the 6 per-seed AUCs              : {nc.fmt_q(per_seed_auc.std(axis=1, ddof=1))}")
for thr in (0.50, 0.52, 0.55, 0.58, 0.60):
    P(f"P(all six per-seed AUCs > {thr:.2f}) under noise: 1000-salt {np.mean(per_seed_auc.min(axis=1) > thr):.4f}   20k-perm {np.mean(PS.min(axis=1) > thr):.4f}")
for kk in (4, 5):
    P(f"P(at least {kk} of 6 per-seed AUCs > 0.50) under noise: {np.mean((PS > 0.5).sum(axis=1) >= kk):.3f}")
P(f"P(mean of 6 per-seed AUCs > 0.55) under noise: {np.mean(PS.mean(axis=1) > 0.55):.4f};  > 0.56: {np.mean(PS.mean(axis=1) > 0.56):.4f}")
P("")
P("## Thresholds a real statistic must EXCEED at one-sided p < 0.05 (no selection, one pre-registered statistic)")
sd_pool = nc.auc_null_sd(n1, n0); z95 = nc.z_quantile(0.95); z99 = nc.z_quantile(0.99)
P(f"AUC, pooled (n1={n1}, n0={n0}): H0 sd = sqrt((n1+n0+1)/(12 n1 n0)) = sqrt({n1+n0+1}/{12*n1*n0}) = {sd_pool:.4f}")
P(f"  -> AUC > 0.5 + 1.645*{sd_pool:.4f} = {0.5+z95*sd_pool:.4f}   (p<0.01: > {0.5+z99*sd_pool:.4f})")
P(f"  -> empirical 95th pct of 20,000 permutation AUCs = {np.percentile(perm_auc,95):.4f}; 99th = {np.percentile(perm_auc,99):.4f}; 1,000-salt 95th = {np.percentile(pooled_auc,95):.4f}")
P("AUC, single seed:")
for sd, (g, n) in sizes.items():
    s_ = nc.auc_null_sd(g, n - g)
    P(f"  s{sd}: n1={g}, n0={n-g}: sd={s_:.4f} -> AUC > {0.5+z95*s_:.3f} (p<0.05), > {0.5+z99*s_:.3f} (p<0.01); empirical 95th {np.percentile(per_seed_null[sd],95):.3f}")
x20, pp20 = nc.hyper_crit(len(gd), n1, 20); x50, pp50 = nc.hyper_crit(len(gd), n1, 50)
P(f"p@20 pooled, gold+decoy subset: X ~ Hypergeom(N={len(gd)}, K={n1}, n=20), mean {20*n1/len(gd):.2f}; smallest x with P(X>=x)<=0.05 is {x20} (P={pp20:.4f}) -> p@20 >= {x20/20:.2f}, i.e. must EXCEED {(x20-1)/20:.2f}")
P(f"p@50 pooled, gold+decoy subset: Hypergeom(N={len(gd)}, K={n1}, n=50), mean {50*n1/len(gd):.2f}; crit x={x50} (P={pp50:.4f}) -> p@50 >= {x50/50:.2f}, must EXCEED {(x50-1)/50:.2f}")
x20f, pp20f = nc.hyper_crit(Nfull, n1, 20); x50f, pp50f = nc.hyper_crit(Nfull, n1, 50)
P(f"p@20 pooled, FULL filtered pool: Hypergeom(N={Nfull}, K={n1}, n=20), mean {20*n1/Nfull:.2f}; crit x={x20f} (P={pp20f:.4f}) -> p@20 >= {x20f/20:.2f}")
P(f"p@50 pooled, FULL filtered pool: Hypergeom(N={Nfull}, K={n1}, n=50), mean {50*n1/Nfull:.2f}; crit x={x50f} (P={pp50f:.4f}) -> p@50 >= {x50f/50:.2f}")
# max over six seeds of per-seed p@20: P(max >= x) = 1 - prod_s P(X_s < x)
P("MAX over 6 seeds of per-seed p@20 (gold+decoy subset): P(max >= x) = 1 - prod_s P(X_s <= x-1), X_s ~ Hypergeom(N_s, K_s, 20)")
for x in range(6, 21):
    pm = 1 - np.prod([nc.hyper_cdf(n, g, 20, x - 1) for sd, (g, n) in sizes.items()])
    if pm <= 0.25:
        P(f"  x={x:2d} ({x/20:.2f}): P(max>=x) = {pm:.4f}" + ("   <- first x with P<=0.05" if pm <= 0.05 and 1 - np.prod([nc.hyper_cdf(n, g, 20, x - 2) for sd, (g, n) in sizes.items()]) > 0.05 else ""))
P(f"  empirical (1,000 salts): 95th pct of max per-seed p@20 = {np.percentile(max_seed_p20,95):.2f}; share of salts with max >= 0.60: {np.mean(max_seed_p20>=0.60):.3f}; >= 0.65: {np.mean(max_seed_p20>=0.65):.3f}")
P("single-seed p@20 (gold+decoy subset), per seed:")
for sd, (g, n) in sizes.items():
    x, pp = nc.hyper_crit(n, g, 20)
    P(f"  s{sd}: Hypergeom(N={n}, K={g}, n=20) mean {20*g/n:.2f}; crit x={x} (P={pp:.4f}) -> p@20 >= {x/20:.2f}")
txt = "\n".join(out)
print(txt)
open(f"{nc.OUT}/n1_result.md", "w").write(txt + "\n")
json.dump(dict(pooled_auc=pooled_auc.tolist(), max_seed_p20=max_seed_p20.tolist(), p20=p20.tolist(), p50=p50.tolist(),
               per_seed_auc=per_seed_auc.tolist(), perm_auc_q=nc.q(perm_auc), per_seed_null_q={sd: nc.q(v) for sd, v in per_seed_null.items()}),
          open(f"{nc.OUT}/n1_result.json", "w"))
