"""Compare rebuilt (model K) cells with the real Boundary cells for the seeds that have cells-real-seed-N.json, and
recompute the H5-2 synchrony statistic and the H5-3 composite on the REAL cells."""
import sys, json, random, statistics, os
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5")
from h5_common import *
from collections import defaultdict
K = 3; RULE_WIN = 8
rows = load_pool()
have = [s for s in SEEDS if os.path.exists(f"{OUT}/cells-real-seed-{s}.json")]
real = {s: json.load(open(f"{OUT}/cells-real-seed-{s}.json"))["cells"] for s in have}
mine = {s: json.load(open(f"{OUT}/cells-seed-{s}.json"))["cells"] for s in have}
rows = [r for r in rows if r["seed"] in have]
print("seeds with real cells:", have, " pool rows:", len(rows))
lbm = lambda n: 0 if n == 0 else (1 if n < K else n)

def presence(cellsd, key):
    return {(s, int(w)) for s, wk in cellsd.get(key, {}).items() for w, v in wk.items() if v[0] > 0}
agree = both = onlyr = onlym = 0; kagree = 0; kboth = 0
for r in rows:
    pr = presence(real[r["seed"]], r["key"]); pm = presence(mine[r["seed"]], r["key"])
    both += len(pr & pm); onlyr += len(pr - pm); onlym += len(pm - pr)
print(f"cell presence (site,week) over pool keys: in both {both}, real-only {onlyr}, rebuilt-only {onlym}  (Jaccard {both/(both+onlyr+onlym):.3f})")

def longest_window(rw):
    best = cur = [rw[0], rw[0]]
    for a, b in zip(rw, rw[1:]):
        if b == a + 1: cur[1] = b
        else:
            if cur[1] - cur[0] > best[1] - best[0]: best = cur
            cur = [b, b]
    if cur[1] - cur[0] > best[1] - best[0]: best = cur
    return max(0, best[0] - RULE_WIN + 1), best[1]

def feats(cellsd, r, is_real):
    lo, hi = longest_window(r["rule_weeks"])
    act = defaultdict(set); kact = defaultdict(set)
    for s, wk in cellsd.get(r["key"], {}).items():
        for w, v in wk.items():
            w = int(w); n = v[0] if is_real else lbm(v[0])
            if n > 0 and lo <= w <= hi:
                act[w].add(s)
                if n >= K: kact[w].add(s)
    sizes = [len(v) for v in act.values()]
    return {"sync": (sum(sizes) / len(sizes)) if sizes else 0.0, "kcells": sum(len(v) for v in kact.values()),
            "sync_max": max(sizes) if sizes else 0, "lr": max(runs(r["rule_weeks"]))}
FR = {(r["seed"], r["key"]): feats(real[r["seed"]], r, True) for r in rows}
FM = {(r["seed"], r["key"]): feats(mine[r["seed"]], r, False) for r in rows}
for name, FF in (("REAL cells", FR), ("rebuilt cells", FM)):
    print(f"\n== {name} ==")
    report_auc(rows, {k: -v["sync"] for k, v in FF.items()}, "neg mean_sites_per_week_win")
    report_auc(rows, {k: -v["sync_max"] for k, v in FF.items()}, "neg sync_max_win")
    report_auc(rows, {k: v["kcells"] for k, v in FF.items()}, "n_kcells_win")
    def topn(sub, score, n, tie_seeds=20):
        g = d = f = 0.0
        for t in range(tie_seeds):
            rnd = random.Random(7919 * t + sub[0]["seed"])
            order = sorted(sub, key=lambda r: (tuple(-x for x in score(r)), rnd.random()))[:n]
            g += sum(r["label"] == "gold" for r in order); d += sum(r["label"] == "decoy" for r in order); f += sum(r["label"] == "gold" and r["feasible"] for r in order)
        return g / tie_seeds, d / tie_seeds, f / tie_seeds
    lex = lambda r: (-FF[(r["seed"], r["key"])]["sync"], FF[(r["seed"], r["key"])]["kcells"], FF[(r["seed"], r["key"])]["lr"])
    gated = lambda th: (lambda r: (1 if FF[(r["seed"], r["key"])]["sync"] <= th else 0, FF[(r["seed"], r["key"])]["kcells"], FF[(r["seed"], r["key"])]["lr"]))
    for s in have:
        sub = [r for r in rows if r["seed"] == s]
        a = topn(sub, lex, 20); b = topn(sub, lex, 50); c = topn(sub, gated(1.25), 20); d = topn(sub, gated(1.25), 50)
        print(f"  seed {s}: lex(-sync,k,lr) P@20 gold {a[0]/20:.2f} decoy {a[1]/20:.2f} feas {a[2]:.1f} | P@50 gold {b[0]/50:.2f} decoy {b[1]/50:.2f} || gate1.25 P@20 gold {c[0]/20:.2f} decoy {c[1]/20:.2f} feas {c[2]:.1f} | P@50 gold {d[0]/50:.2f} decoy {d[1]/50:.2f}")
