"""Learned ranking of the detector pool at B = 60/seed, replayed on the v4 config's real verdicts.
Target = the SYSTEM's own verdict (supported + lineage ok), never the gold label. Features = inference-time fields
only. Leave-one-seed-out: the model scoring seed s is fitted on the other five. 'label' is read only by common.score.
Usage: learned_rank.py POOL_DIR"""
import math, sys
import numpy as np
from common import load, score

B = 60
def z(r):
    W = sum(r["win_lb"]); h = r["hist_lb"]; lam = max((sum(h) / len(h)) * len(r["win_lb"]) if h else 0.0, 1.0)
    return (W - lam) / math.sqrt(lam)
def feats(r):
    rb = 1.0 if r["rule_week_list"] and r["rule_week_list"][0] <= r["week"] else 0.0
    return [r["score"], math.log1p(r["n_alert_weeks"]), math.log1p(r["n_candidate_weeks"]), r["supporting_sites"], rb,
            r["rule_sites"], math.log1p(r["rule_count_lb"]), math.log1p(sum(r["win_lb"])), max(r["win_sites"] or [0]), z(r),
            len(r["detectors"])]
pools = [load(sys.argv[1], s) for s in range(6)]
def det(p): return [i for i, r in enumerate(p["rows"]) if r["source"] != "rule"]
def xy(p):
    ix = det(p); X = np.array([feats(p["rows"][i]) for i in ix], float)
    y = np.array([1.0 if p["rows"][i]["status"] == "supported" and p["rows"][i]["lineage_ok"] else 0.0 for i in ix])
    return ix, X, y
def fit(X, y, l2=1.0, it=3000, lr=0.1):
    mu, sd = X.mean(0), X.std(0) + 1e-9; Xs = (X - mu) / sd; w = np.zeros(X.shape[1]); b = 0.0
    for _ in range(it):
        p = 1 / (1 + np.exp(-(Xs @ w + b))); g = Xs.T @ (p - y) / len(y) + l2 * w / len(y)
        w -= lr * g; b -= lr * (p - y).mean()
    return lambda Z: ((Z - mu) / sd) @ w + b
tot = {"found": 0, "rare": 0, "decoys": 0, "bg": 0}; per = []
for s in range(6):
    Xtr = np.vstack([xy(pools[t])[1] for t in range(6) if t != s]); ytr = np.concatenate([xy(pools[t])[2] for t in range(6) if t != s])
    f = fit(Xtr, ytr); ix, X, _ = xy(pools[s]); sc = f(X)
    order = [ix[j] for j in np.argsort(-sc, kind="stable")][:B]
    r = score(pools[s], order); per.append(len(r["found"]))
    tot["found"] += len(r["found"]); tot["rare"] += len(r["rare"]); tot["decoys"] += r["decoys"]; tot["bg"] += r["bg"]
print(f"learned (target = system verdict, LOSO)  found {tot['found']}/240 rare {tot['rare']} decoys {tot['decoys']} bg {tot['bg']}  per-seed {per}")
