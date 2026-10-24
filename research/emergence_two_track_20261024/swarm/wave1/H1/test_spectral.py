"""H1 step 2: the three pre-registered spectral statistics over the exported-bucket matrices (OUT/matrix_seed-*.npz).
S1 residual (rank-r SVD, rows L2-normalised; gold claimed LOWER), S2 leverage (same SVD; gold claimed HIGHER),
S3 rank-1 energy sigma1^2/||X||_F^2 of the key's own site x week submatrix (gold claimed HIGHER).
Labels (class column of candidate_pool.csv) enter ONLY the scoring lines. Evaluation pool: rule_only & rule_weeks >= 7."""
import csv, json, os, sys
import numpy as np
OUT = os.environ["H1_OUT"]
POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
GRID = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48]
SEEDS = range(6)

def auc(pos, neg):
    """Mann-Whitney AUC = P(score(pos) > score(neg)) with ties at 1/2."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if len(pos) == 0 or len(neg) == 0: return float("nan")
    allv = np.concatenate([pos, neg]); order = allv.argsort(kind="mergesort"); ranks = np.empty(len(allv)); ranks[order] = np.arange(1, len(allv) + 1)
    # average ranks for ties
    u, inv = np.unique(allv, return_inverse=True)
    sums = np.bincount(inv, weights=ranks); cnt = np.bincount(inv); ranks = (sums / cnt)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))

# ---- load -------------------------------------------------------------------------------------------------------
pool = {}
for r in csv.DictReader(open(POOL)):
    pool[(int(r["seed"]), r["key"])] = r
per_seed = {}
for s in SEEDS:
    z = np.load(f"{OUT}/matrix_seed-{s}.npz")
    keys = list(z["keys"]); W6 = z["W6"].astype(float)                     # keys x sites x weeks, bucketed window counts
    M = W6.reshape(len(keys), -1)
    norms = np.linalg.norm(M, axis=1); Mn = M / np.where(norms == 0, 1, norms)[:, None]
    U, S, Vt = np.linalg.svd(Mn, full_matrices=False)
    # S1(r): residual energy of the unit-norm row outside the top-r right singular subspace = 1 - sum_{j<r} (sigma_j U_ij)^2
    proj = np.cumsum((U * S[None, :]) ** 2, axis=1)                        # keys x rank
    s1 = {r: np.clip(1.0 - proj[:, r - 1], 0, 1) for r in GRID if r <= len(S)}
    # S2(r): leverage = row norm of U_r
    lev = np.cumsum(U ** 2, axis=1)
    s2 = {r: lev[:, r - 1] for r in GRID if r <= len(S)}
    # S3: rank-1 energy of the key's own 7 x 26 submatrix
    sv = np.linalg.svd(W6, compute_uv=False)                               # keys x min(7,26)
    fro2 = (W6 ** 2).sum(axis=(1, 2)); s3 = np.where(fro2 > 0, sv[:, 0] ** 2 / np.where(fro2 == 0, 1, fro2), 0.0)
    rows = [pool[(s, k)] for k in keys]
    cls = np.array([r["class"] for r in rows]); rw = np.array([int(r["rule_weeks"]) for r in rows])
    state = np.array([r["detector_state"] for r in rows])
    filt = (state == "rule_only") & (rw >= 7)
    per_seed[s] = dict(keys=keys, s1=s1, s2=s2, s3=s3, cls=cls, rw=rw, filt=filt, jac=z["rule_jaccard"], S=S,
                       mass=M.sum(axis=1), nsites=(W6.sum(axis=2) > 0).sum(axis=1))

# ---- helpers ------------------------------------------------------------------------------------------------------
def scores(s, name, r=None):
    d = per_seed[s]
    return d["s1"][r] if name == "S1" else d["s2"][r] if name == "S2" else d["s3"] if name == "S3" else d["rw"].astype(float)
SIGN = {"S1": -1.0, "S2": 1.0, "S3": 1.0, "RW": 1.0}                        # claimed direction: higher signed score => gold

def seed_auc(s, name, r=None, versus="decoy", sign=None):
    d = per_seed[s]; f = d["filt"]; sc = SIGN[name] if sign is None else sign
    v = sc * scores(s, name, r)
    neg = (d["cls"] == "decoy") if versus == "decoy" else (d["cls"] != "gold")
    return auc(v[f & (d["cls"] == "gold")], v[f & neg])

def pooled_auc(name, r=None, versus="decoy", sign=None):
    sc = SIGN[name] if sign is None else sign
    P, N = [], []
    for s in SEEDS:
        d = per_seed[s]; f = d["filt"]; v = sc * scores(s, name, r)
        neg = (d["cls"] == "decoy") if versus == "decoy" else (d["cls"] != "gold")
        P.append(v[f & (d["cls"] == "gold")]); N.append(v[f & neg])
    return auc(np.concatenate(P), np.concatenate(N))

def prec_at(s, name, r=None, n=20, sign=None):
    d = per_seed[s]; f = d["filt"]; sc = SIGN[name] if sign is None else sign
    v = sc * scores(s, name, r); idx = np.where(f)[0]
    top = idx[np.argsort(-v[idx], kind="mergesort")[:n]]
    c = d["cls"][top]
    return int((c == "gold").sum()), int((c == "decoy").sum())

res = {"pool": {}, "stats": {}}
# ---- pool description ----------------------------------------------------------------------------------------------
for s in SEEDS:
    d = per_seed[s]; f = d["filt"]
    res["pool"][s] = {"n": int(f.sum()), "gold": int((d["cls"][f] == "gold").sum()), "decoy": int((d["cls"][f] == "decoy").sum()),
                      "other": int((d["cls"][f] == "other").sum()), "rule_jaccard_lt_0.5": int((d["jac"] < 0.5).sum()),
                      "top_sv_energy_share": [round(float(x), 3) for x in (d["S"][:6] ** 2 / (d["S"] ** 2).sum())]}
print("POOL (rule_only & rule_weeks>=7):", json.dumps(res["pool"]))

# ---- fixed-r / parameter-free pooled numbers (claimed direction) ------------------------------------------------------
for name in ("S1", "S2"):
    res["stats"][name] = {"by_r": {}}
    for r in GRID:
        if r > min(len(per_seed[s]["S"]) for s in SEEDS): continue
        res["stats"][name]["by_r"][r] = {"pooled_auc_gold_vs_decoy": pooled_auc(name, r), "pooled_auc_gold_vs_all": pooled_auc(name, r, "all"),
                                          "per_seed_auc_gold_vs_decoy": [round(seed_auc(s, name, r), 3) for s in SEEDS]}
res["stats"]["S3"] = {"pooled_auc_gold_vs_decoy": pooled_auc("S3"), "pooled_auc_gold_vs_all": pooled_auc("S3", None, "all"),
                      "per_seed_auc_gold_vs_decoy": [round(seed_auc(s, "S3"), 3) for s in SEEDS],
                      "per_seed_auc_gold_vs_all": [round(seed_auc(s, "S3", None, "all"), 3) for s in SEEDS],
                      "prec_top20_(gold,decoy)_per_seed": [prec_at(s, "S3", None, 20) for s in SEEDS],
                      "prec_top50_(gold,decoy)_per_seed": [prec_at(s, "S3", None, 50) for s in SEEDS]}
res["stats"]["RW_reference"] = {"pooled_auc_gold_vs_decoy": pooled_auc("RW"), "pooled_auc_gold_vs_all": pooled_auc("RW", None, "all"),
                                "per_seed_auc_gold_vs_decoy": [round(seed_auc(s, "RW"), 3) for s in SEEDS],
                                "prec_top20_(gold,decoy)_per_seed": [prec_at(s, "RW", None, 20) for s in SEEDS],
                                "prec_top50_(gold,decoy)_per_seed": [prec_at(s, "RW", None, 50) for s in SEEDS]}

# ---- leave-one-seed-out: choose r (and, in the k=2 variant, the sign) on five seeds, score the sixth -------------------
def loso(name, fit_sign=False):
    out = []
    for h in SEEDS:
        train = [s for s in SEEDS if s != h]; best = None
        for r in GRID:
            if r > min(len(per_seed[s]["S"]) for s in SEEDS): continue
            for sg in ([SIGN[name]] if not fit_sign else [1.0, -1.0]):
                m = float(np.mean([seed_auc(s, name, r, sign=sg) for s in train]))
                if best is None or m > best[0]: best = (m, r, sg)
        m, r, sg = best
        out.append({"held_out": h, "r": r, "sign": sg, "train_auc": round(m, 3), "heldout_auc_gold_vs_decoy": round(seed_auc(h, name, r, sign=sg), 3),
                    "heldout_auc_gold_vs_all": round(seed_auc(h, name, r, "all", sign=sg), 3),
                    "prec_top20_(gold,decoy)": prec_at(h, name, r, 20, sign=sg), "prec_top50_(gold,decoy)": prec_at(h, name, r, 50, sign=sg)})
    return out
for name in ("S1", "S2"):
    res["stats"][name]["loso_claimed_direction"] = loso(name)
    res["stats"][name]["loso_sign_fitted_k2"] = loso(name, fit_sign=True)
# S3 has no parameter: LOSO reduces to per-seed scores (already above); the k=2 sign-fitted version:
res["stats"]["S3"]["loso_sign_fitted_k2"] = [{"held_out": h, "sign": sg, "heldout_auc_gold_vs_decoy": round(seed_auc(h, "S3", sign=sg), 3)}
                                              for h in SEEDS for sg in [max([1.0, -1.0], key=lambda g: np.mean([seed_auc(s, "S3", sign=g) for s in SEEDS if s != h]))]]

# ---- diagnostics: what the statistics actually track (label used only to group means) --------------------------------
diag = {}
for name, r in (("S1", 8), ("S2", 8), ("S3", None)):
    v = np.concatenate([scores(s, name, r)[per_seed[s]["filt"]] for s in SEEDS]); c = np.concatenate([per_seed[s]["cls"][per_seed[s]["filt"]] for s in SEEDS])
    mass = np.concatenate([per_seed[s]["mass"][per_seed[s]["filt"]] for s in SEEDS]); ns = np.concatenate([per_seed[s]["nsites"][per_seed[s]["filt"]] for s in SEEDS])
    diag[name] = {"median_by_class": {k: round(float(np.median(v[c == k])), 4) for k in ("gold", "decoy", "other")},
                  "spearman_with_total_mass": round(float(np.corrcoef(np.argsort(np.argsort(v)), np.argsort(np.argsort(mass)))[0, 1]), 3),
                  "spearman_with_n_active_sites": round(float(np.corrcoef(np.argsort(np.argsort(v)), np.argsort(np.argsort(ns)))[0, 1]), 3)}
res["diagnostics"] = diag
json.dump(res, open(f"{OUT}/results.json", "w"), indent=1, default=float)

# ---- print ----------------------------------------------------------------------------------------------------------
for name in ("S1", "S2"):
    print(f"\n{name} (claimed direction {'gold LOWER' if SIGN[name] < 0 else 'gold HIGHER'}) pooled AUC by r:")
    for r, v in res["stats"][name]["by_r"].items():
        print(f"  r={r:>2}  gold-vs-decoy {v['pooled_auc_gold_vs_decoy']:.3f}  gold-vs-all {v['pooled_auc_gold_vs_all']:.3f}  per-seed {v['per_seed_auc_gold_vs_decoy']}")
    for tag in ("loso_claimed_direction", "loso_sign_fitted_k2"):
        L = res["stats"][name][tag]; h = [x["heldout_auc_gold_vs_decoy"] for x in L]
        print(f"  LOSO {tag}: held-out AUC gold-vs-decoy {h}  spread [{min(h):.3f}, {max(h):.3f}]  r chosen {[x['r'] for x in L]} sign {[x['sign'] for x in L]}")
        print(f"      held-out gold-vs-all {[x['heldout_auc_gold_vs_all'] for x in L]}  p@20 (gold,decoy) {[x['prec_top20_(gold,decoy)'] for x in L]}  p@50 {[x['prec_top50_(gold,decoy)'] for x in L]}")
print("\nS3 (claimed gold HIGHER):", json.dumps(res["stats"]["S3"], default=float))
print("\nRW reference (rule_weeks, not mine):", json.dumps(res["stats"]["RW_reference"], default=float))
print("\nDIAG:", json.dumps(diag))
