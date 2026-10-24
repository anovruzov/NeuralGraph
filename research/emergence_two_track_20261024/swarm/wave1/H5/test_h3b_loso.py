"""H5-3 follow-up: leave-one-seed-out with a decoy-aware criterion (max gold@50 - decoy@50 on the 5 training seeds),
and the per-seed spread of the UNTUNED lexicographic ranker (-sync, k-cells, longest_run), which has no parameter."""
import sys, json, random, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5")
from h5_common import *
rows = load_pool()
F = {tuple([int(k.split("|")[0]), k.split("|")[1]]): v for k, v in json.load(open(f"{OUT}/features_h2.json")).items()}
sync = lambda r: F[(r["seed"], r["key"])]["mean_sites_per_week_win"]
kc = lambda r: F[(r["seed"], r["key"])]["n_kcells_win"]
lr = lambda r: F[(r["seed"], r["key"])]["longest_run"]
THETAS = [1.0, 1.1, 1.2, 1.25, 1.34, 1.4, 1.5, 1.67, 2.0, 99]
gated = lambda th: (lambda r: (1 if sync(r) <= th else 0, kc(r), lr(r)))
lex = lambda r: (-sync(r), kc(r), lr(r))

def topn(sub, score, n, tie_seeds=20):
    g = d = f = 0.0
    for t in range(tie_seeds):
        rnd = random.Random(7919 * t + sub[0]["seed"])
        order = sorted(sub, key=lambda r: (tuple(-x for x in score(r)), rnd.random()))[:n]
        g += sum(r["label"] == "gold" for r in order); d += sum(r["label"] == "decoy" for r in order)
        f += sum(r["label"] == "gold" and r["feasible"] for r in order)
    return g / tie_seeds, d / tie_seeds, f / tie_seeds
by_seed = {s: [r for r in rows if r["seed"] == s] for s in SEEDS}

print("== UNTUNED lexicographic (-sync, kcells, longest_run): per-seed, no parameter to tune ==")
for n in (20, 50):
    vals = [topn(by_seed[s], lex, n) for s in SEEDS]
    print(f"  top-{n}: gold " + " ".join(f"{v[0]/n:.2f}" for v in vals) + f"  (range {min(v[0] for v in vals)/n:.2f}-{max(v[0] for v in vals)/n:.2f}, mean {statistics.mean(v[0] for v in vals)/n:.3f})"
          f" | decoy " + " ".join(f"{v[1]/n:.2f}" for v in vals) + f" (mean {statistics.mean(v[1] for v in vals)/n:.3f}) | feasible gold mean {statistics.mean(v[2] for v in vals):.1f}")
print("\n== LOSO, criterion = max (gold@50 - decoy@50) on the 5 training seeds ==")
held = []
for s in SEEDS:
    best = None
    for th in THETAS:
        m = statistics.mean((lambda v: v[0] - v[1])(topn(by_seed[t], gated(th), 50)) for t in SEEDS if t != s)
        if best is None or m > best[0]: best = (m, th)
    th = best[1]; a = topn(by_seed[s], gated(th), 20); b = topn(by_seed[s], gated(th), 50)
    held.append((s, th, a, b))
    print(f"  held-out seed {s}: theta={th}  P@20 gold {a[0]/20:.2f} decoy {a[1]/20:.2f} feasible-gold {a[2]:.1f} | P@50 gold {b[0]/50:.2f} decoy {b[1]/50:.2f} feasible-gold {b[2]:.1f}")
g20 = [a[0]/20 for *_, a, _ in held]; d20 = [a[1]/20 for *_, a, _ in held]; g50 = [b[0]/50 for *_, b in held]; d50 = [b[1]/50 for *_, b in held]
print(f"  SPREAD held-out: P@20 gold {min(g20):.2f}-{max(g20):.2f} (mean {statistics.mean(g20):.3f}) decoy {min(d20):.2f}-{max(d20):.2f} (mean {statistics.mean(d20):.3f})"
      f" | P@50 gold {min(g50):.2f}-{max(g50):.2f} (mean {statistics.mean(g50):.3f}) decoy {min(d50):.2f}-{max(d50):.2f} (mean {statistics.mean(d50):.3f})")
print("\n== LOSO, criterion = max gold@20 - decoy@20 ==")
held = []
for s in SEEDS:
    best = None
    for th in THETAS:
        m = statistics.mean((lambda v: v[0] - v[1])(topn(by_seed[t], gated(th), 20)) for t in SEEDS if t != s)
        if best is None or m > best[0]: best = (m, th)
    th = best[1]; a = topn(by_seed[s], gated(th), 20); b = topn(by_seed[s], gated(th), 50)
    held.append((s, th, a, b))
    print(f"  held-out seed {s}: theta={th}  P@20 gold {a[0]/20:.2f} decoy {a[1]/20:.2f} | P@50 gold {b[0]/50:.2f} decoy {b[1]/50:.2f}")
g20 = [a[0]/20 for *_, a, _ in held]; d20 = [a[1]/20 for *_, a, _ in held]; g50 = [b[0]/50 for *_, b in held]; d50 = [b[1]/50 for *_, b in held]
print(f"  SPREAD held-out: P@20 gold {min(g20):.2f}-{max(g20):.2f} (mean {statistics.mean(g20):.3f}) decoy mean {statistics.mean(d20):.3f} | P@50 gold {min(g50):.2f}-{max(g50):.2f} (mean {statistics.mean(g50):.3f}) decoy mean {statistics.mean(d50):.3f}")

print("\n== reference: baseline rule_weeks >= 11 per seed (precision / decoy share), and the same cut + sync<=1.34 ==")
for s in SEEDS:
    kept = [r for r in by_seed[s] if len(r["rule_weeks"]) >= 11]
    kept2 = [r for r in kept if sync(r) <= 1.34]
    g = sum(r["label"] == "gold" for r in kept); d = sum(r["label"] == "decoy" for r in kept)
    g2 = sum(r["label"] == "gold" for r in kept2); d2 = sum(r["label"] == "decoy" for r in kept2)
    print(f"  seed {s}: >=11 kept {len(kept):3d} gold {g:2d} ({g/len(kept):.3f}) decoy {d:2d} ({d/len(kept):.3f}) | + sync<=1.34 kept {len(kept2):3d} gold {g2:2d} ({g2/max(1,len(kept2)):.3f}) decoy {d2:2d} ({d2/max(1,len(kept2)):.3f})")
