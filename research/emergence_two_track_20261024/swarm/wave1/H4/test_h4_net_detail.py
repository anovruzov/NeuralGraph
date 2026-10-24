"""H4 detail (free): what S_Net ranks first per LOSO fold, top-5/10 precision per seed, and S_Net on the snapshot
(already-asked) pool as an out-of-pool check. Reads OUT/h4_per_key_scores.json written by test_h4_scores.py."""
import json, os, hashlib, math
from collections import Counter, defaultdict
OUT = os.path.dirname(os.path.abspath(__file__))
P = json.load(open(os.path.join(OUT, "h4_per_key_scores.json")))
SEEDS = [str(s) for s in range(6)]
def in_window(v, wk): return any(lo <= wk <= hi for lo, hi in v["windows"])
def order(items, name):
    if name == "baseline": return sorted(items, key=lambda kv: (-kv[1]["rule_weeks"], kv[1]["salt"]))
    return sorted(items, key=lambda kv: (-kv[1][name], -kv[1]["rule_weeks"], kv[1]["salt"]))
def auc_strict(items, name, neg):
    o = order(items, name); pos = []; ng = []
    for i, (k, v) in enumerate(o):
        if v["cls"] == "gold": pos.append(-i)
        elif v["cls"] == neg: ng.append(-i)
    if not pos or not ng: return float("nan")
    return sum((p > n) for p in pos for n in ng) / (len(pos) * len(ng))

filt = {k: v for k, v in P.items() if v["state"] == "rule_only" and v["rule_weeks"] >= 7}
snap = {k: v for k, v in P.items() if v["state"] == "snapshot"}
print("top-k precision per seed on the filtered pool, (gold, decoy, gold corrected at label-free week, decoy accepted):")
for name in ("baseline", "S_Net", "S_Yield", "S_IG"):
    for n in (5, 10, 20):
        cells = []; G = Dc = C = A = 0
        for s in SEEDS:
            it = order([(k, v) for k, v in filt.items() if v["seed"] == s], name)[:n]
            g = sum(v["cls"] == "gold" for k, v in it); d = sum(v["cls"] == "decoy" for k, v in it)
            c = sum(v["cls"] == "gold" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]) for k, v in it)
            a = sum(v["cls"] == "decoy" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]) for k, v in it)
            cells.append(f"({g},{d},{c},{a})"); G += g; Dc += d; C += c; A += a
        print(f"  {name:9s} @{n:2d} " + " ".join(cells) + f"   sum gold {G} decoy {Dc} corrected {C} decoy_acc {A}  yield/question {C/(6*n):.3f}")

print("\nS_Net score distribution of the filtered pool's top-20 per seed (what the fitted table puts first):")
for s in SEEDS:
    it = order([(k, v) for k, v in filt.items() if v["seed"] == s], "S_Net")[:20]
    print(f"  seed {s}: " + ", ".join(f"{v['S_Net']:+.2f}{v['cls'][0]}" for k, v in it))

print("\nout-of-pool check -- the snapshot (already-asked) pool, same LOSO scores, AUC gold vs decoy per seed:")
for name in ("baseline", "S_Net", "S_Yield", "S_IG", "S_Pclear"):
    per = [auc_strict([(k, v) for k, v in snap.items() if v["seed"] == s], name, "decoy") for s in SEEDS]
    print(f"  {name:9s} pooled {auc_strict(list(snap.items()), name, 'decoy'):.3f}  per-seed " + " ".join(f"{x:.3f}" for x in per))
print("  snapshot pool:", Counter(v["cls"] for v in snap.values()))
# S_Net > 0 as a hard filter: how much of the filtered pool, and what it holds
pos = {k: v for k, v in filt.items() if v["S_Net"] > 0}
print("\nS_Net > 0 (expected net utility positive at nu=1):", len(pos), Counter(v["cls"] for v in pos.values()),
      "gold corrected at label-free week:", sum(v["cls"] == "gold" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]) for v in pos.values()),
      "decoys accepted:", sum(v["cls"] == "decoy" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]) for v in pos.values()),
      "unplanted supported:", sum(v["cls"] == "other" and v["S_Pclear_tb"] for v in pos.values()))
for s in SEEDS:
    ps = {k: v for k, v in pos.items() if v["seed"] == s}
    print(f"   seed {s}: n={len(ps)} " + str(dict(Counter(v['cls'] for v in ps.values()))),
          "corrected", sum(v["cls"] == "gold" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]) for v in ps.values()),
          "decoy_acc", sum(v["cls"] == "decoy" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]) for v in ps.values()),
          "other_sup", sum(v["cls"] == "other" and v["S_Pclear_tb"] for v in ps.values()))
# rare vs common among the gold S_Net finds at top-20
rare = Counter()
for s in SEEDS:
    for k, v in order([(k, v) for k, v in filt.items() if v["seed"] == s], "S_Net")[:20]:
        if v["cls"] == "gold": rare[v["rare"]] += 1
print("\nrare flag of gold in S_Net top-20 (6 seeds):", dict(rare))
