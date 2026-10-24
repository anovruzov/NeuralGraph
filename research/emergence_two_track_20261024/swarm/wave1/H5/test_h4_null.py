"""Null check for H5-2: permute gold/decoy labels within each seed (keeping class sizes), recompute the AUC of the
synchrony statistic. Also the best-of-many null: the max AUC over 26 random label-independent statistics."""
import sys, json, random, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5")
from h5_common import *
rows = load_pool()
F = {tuple([int(k.split("|")[0]), k.split("|")[1]]): v for k, v in json.load(open(f"{OUT}/features_h2.json")).items()}
gd = [r for r in rows if r["label"] in ("gold", "decoy")]
score = {(r["seed"], r["key"]): -F[(r["seed"], r["key"])]["mean_sites_per_week_win"] for r in gd}
obs = auc([score[(r["seed"], r["key"])] for r in gd if r["label"] == "gold"], [score[(r["seed"], r["key"])] for r in gd if r["label"] == "decoy"])
rnd = random.Random(20261024); nulls = []
for it in range(2000):
    labs = {}
    for s in SEEDS:
        sub = [r for r in gd if r["seed"] == s]; l = [r["label"] for r in sub]; rnd.shuffle(l)
        for r, x in zip(sub, l): labs[(r["seed"], r["key"])] = x
    nulls.append(auc([score[k] for k in labs if labs[k] == "gold"], [score[k] for k in labs if labs[k] == "decoy"]))
nulls.sort()
print(f"observed AUC {obs:.3f}; permutation null (2000 draws, within-seed): mean {statistics.mean(nulls):.3f} sd {statistics.pstdev(nulls):.3f} max {nulls[-1]:.3f} 99.9th pct {nulls[int(0.999*len(nulls))]:.3f}")
# best-of-26 null: max over 26 permutation AUCs per draw
best26 = []
for it in range(500):
    m = 0
    for v in range(26):
        labs = {}
        for s in SEEDS:
            sub = [r for r in gd if r["seed"] == s]; l = [r["label"] for r in sub]; rnd.shuffle(l)
            for r, x in zip(sub, l): labs[(r["seed"], r["key"])] = x
        a = auc([score[k] for k in labs if labs[k] == "gold"], [score[k] for k in labs if labs[k] == "decoy"])
        m = max(m, a, 1 - a)
    best26.append(m)
best26.sort()
print(f"best-of-26 two-sided null (500 draws): mean {statistics.mean(best26):.3f} 99th pct {best26[int(0.99*len(best26))]:.3f} max {best26[-1]:.3f}")
