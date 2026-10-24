"""H1 step 3 — POST HOC diagnostics on S3 (rank-1 energy), run AFTER the pre-registered tests failed. Everything here is
k>=2 by construction (the sign was chosen after seeing the labels). Labels enter only grouping/scoring lines.
Questions: (a) which decoy types are rank-1; (b) is reversed-S3 a proxy for burst count / active sites; (c) as a
NEGATIVE filter (drop high-S3 keys), what does it cost in gold; (d) does reversed S3 add anything inside rule_weeks>=11."""
import csv, json, os
import numpy as np
OUT = os.environ["H1_OUT"]
POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
DUMPS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps"
def auc(pos, neg):
    """Mann-Whitney AUC = P(score(pos) > score(neg)) with ties at 1/2."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if len(pos) == 0 or len(neg) == 0: return float("nan")
    allv = np.concatenate([pos, neg]); order = allv.argsort(kind="mergesort"); ranks = np.empty(len(allv)); ranks[order] = np.arange(1, len(allv) + 1)
    # average ranks for ties
    u, inv = np.unique(allv, return_inverse=True)
    sums = np.bincount(inv, weights=ranks); cnt = np.bincount(inv); ranks = (sums / cnt)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))

pool = {(int(r["seed"]), r["key"]): r for r in csv.DictReader(open(POOL))}
S3, CLS, RW, NB, NS, DT, SEED = [], [], [], [], [], [], []
for s in range(6):
    z = np.load(f"{OUT}/matrix_seed-{s}.npz"); keys = list(z["keys"]); W6 = z["W6"].astype(float); W1 = z["W1"].astype(float)
    sv = np.linalg.svd(W6, compute_uv=False); fro2 = (W6 ** 2).sum(axis=(1, 2)); s3 = sv[:, 0] ** 2 / np.where(fro2 == 0, 1, fro2)
    # burst count: number of maximal runs of consecutive non-empty weeks in the key's weekly (window=1) site-summed series
    act = (W1.sum(axis=1) > 0).astype(int); bursts = ((np.diff(np.pad(act, ((0, 0), (1, 0))), axis=1) == 1).sum(axis=1))
    nsites = (W6.sum(axis=2) > 0).sum(axis=1)
    lab = json.load(open(f"{DUMPS}/seed-{s}.json"))["labels"]; dtype = {}
    for d in lab["decoys"]:
        for k in d["keys"]: dtype[k] = d["class"]
    for i, k in enumerate(keys):
        r = pool[(s, k)]
        if not (r["detector_state"] == "rule_only" and int(r["rule_weeks"]) >= 7): continue
        S3.append(s3[i]); CLS.append(r["class"]); RW.append(int(r["rule_weeks"])); NB.append(bursts[i]); NS.append(nsites[i]); SEED.append(s)
        DT.append(dtype.get(k, 0) if r["class"] == "decoy" else -1)
S3, CLS, RW, NB, NS, DT, SEED = map(np.array, (S3, CLS, RW, NB, NS, DT, SEED))
g, d, o = CLS == "gold", CLS == "decoy", CLS == "other"
out = {}
out["a_median_s3_by_decoy_type"] = {int(t): {"n": int((DT == t).sum()), "median_s3": round(float(np.median(S3[DT == t])), 3),
                                              "auc_gold_vs_this_type_reversed": round(auc(-S3[g], -S3[DT == t]), 3)} for t in sorted(set(DT[d]))}
out["a_share_exactly_rank1"] = {k: round(float((S3[m] > 0.999).mean()), 3) for k, m in (("gold", g), ("decoy", d), ("other", o))}
out["b_median_bursts_by_class"] = {k: float(np.median(NB[m])) for k, m in (("gold", g), ("decoy", d), ("other", o))}
out["b_spearman_reversedS3_vs_bursts"] = round(float(np.corrcoef(np.argsort(np.argsort(-S3)), np.argsort(np.argsort(NB)))[0, 1]), 3)
out["b_auc_gold_vs_decoy_by_bursts_alone"] = round(auc(NB[g], NB[d]), 3)
out["b_auc_gold_vs_decoy_by_nsites_alone"] = round(auc(NS[g], NS[d]), 3)
out["c_negative_filter_drop_s3_ge_T"] = {}
for T in (0.999, 0.95, 0.9, 0.85, 0.8):
    drop = S3 >= T
    out["c_negative_filter_drop_s3_ge_T"][T] = {"dropped": int(drop.sum()), "gold_dropped": f"{int((drop & g).sum())}/{int(g.sum())}",
        "decoy_dropped": f"{int((drop & d).sum())}/{int(d.sum())}", "other_dropped": f"{int((drop & o).sum())}/{int(o.sum())}",
        "gold_dropped_per_seed": [int((drop & g & (SEED == s)).sum()) for s in range(6)],
        "gold:decoy_after": f"{int((~drop & g).sum())}:{int((~drop & d).sum())}"}
hi = RW >= 11
out["d_inside_rule_weeks_ge_11"] = {"n": int(hi.sum()), "gold": int((hi & g).sum()), "decoy": int((hi & d).sum()),
                                    "auc_gold_vs_decoy_reversedS3": round(auc(-S3[hi & g], -S3[hi & d]), 3),
                                    "per_seed": [round(auc(-S3[hi & g & (SEED == s)], -S3[hi & d & (SEED == s)]), 3) for s in range(6)]}
out["d_auc_gold_vs_decoy_rank_sum(rule_weeks, -S3)"] = round(auc((np.argsort(np.argsort(RW)) + np.argsort(np.argsort(-S3)))[g],
                                                                 (np.argsort(np.argsort(RW)) + np.argsort(np.argsort(-S3)))[d]), 3)
json.dump(out, open(f"{OUT}/diag_s3.json", "w"), indent=1, default=float); print(json.dumps(out, indent=1, default=float))
