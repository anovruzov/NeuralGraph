"""H4 budget accounting (free): every ranking's top-k per seed on the filtered pool, all asked at the SAME label-free
week (argmax P(clear|b), fitted on gate outcomes only): gold asked, decoys asked, corrected verdicts (clear AND in the
gold window), decoys accepted (clear AND in the decoy window), unplanted supported (clear). Plus the snapshot-pool
check for every score, and the gate-model-vs-replay calibration."""
import json, os
from collections import Counter
OUT = os.path.dirname(os.path.abspath(__file__))
P = json.load(open(os.path.join(OUT, "h4_per_key_scores.json")))
C = json.load(open(os.path.join(OUT, "h4_cells.json")))
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
    return sum((p > n) for p in pos for n in ng) / (len(pos) * len(ng))
def outcome(v):   # at the label-free week
    wk, tb = v["S_Pclear_wk"], v["S_Pclear_tb"]
    return (v["cls"] == "gold" and tb and in_window(v, wk), v["cls"] == "decoy" and tb and in_window(v, wk), v["cls"] == "other" and tb)
filt = {k: v for k, v in P.items() if v["state"] == "rule_only" and v["rule_weeks"] >= 7}
snap = {k: v for k, v in P.items() if v["state"] == "snapshot"}
NAMES = ("baseline", "S_Pclear", "S_IG", "S_Yield", "S_Net", "S_Net3", "S_thin")
print("filtered pool, top-k per seed summed over 6 seeds, asked at the label-free week:")
print(f"{'rank by':10s} {'k':>3s} {'q':>4s} {'gold':>5s} {'decoy':>5s} {'corrected':>9s} {'decoy_acc':>9s} {'unpl_sup':>8s} {'corr/q':>7s} {'FP/q':>6s} {'per-seed corrected':>24s}")
for name in NAMES:
    for n in (5, 10, 20, 60):
        G = Dq = Cc = Da = Us = 0; per = []
        for s in SEEDS:
            it = order([(k, v) for k, v in filt.items() if v["seed"] == s], name)[:n]
            o = [outcome(v) for k, v in it]
            G += sum(v["cls"] == "gold" for k, v in it); Dq += sum(v["cls"] == "decoy" for k, v in it)
            c = sum(x[0] for x in o); Cc += c; Da += sum(x[1] for x in o); Us += sum(x[2] for x in o); per.append(c)
        q = 6 * n
        print(f"{name:10s} {n:3d} {q:4d} {G:5d} {Dq:5d} {Cc:9d} {Da:9d} {Us:8d} {Cc/q:7.3f} {(Da+Us)/q:6.3f} {str(per):>24s}")
print("\nsnapshot (already-asked) pool, AUC gold vs decoy, pooled and per seed:")
for name in NAMES:
    per = [auc_strict([(k, v) for k, v in snap.items() if v["seed"] == s], name, "decoy") for s in SEEDS]
    print(f"  {name:10s} pooled {auc_strict(list(snap.items()), name, 'decoy'):.3f}  per-seed " + " ".join(f"{x:.3f}" for x in per) + f"  spread {min(per):.3f}-{max(per):.3f}")
# calibration: gate model vs the recorded replay (first fitting rule week on the whole rule-only pool)
ro = [v for v in P.values() if v["state"] == "rule_only"]
g = sum(1 for v in ro if v["cls"] == "gold" and v["first_tb"] and in_window(v, v["first_wk"]))
print(f"\ncalibration: whole rule-only pool at the first fitting rule week (the replay's rule_snapshots week): gate model {g}/{len(ro)} = 1 in {len(ro)/g:.0f}; recorded replay ~1 in 220 -> gate-model optimism x{220*g/len(ro):.1f}")
# which b-classes carry S_Net's and S_Net3's top-20 (at the label-free week), gold vs decoy vs other
for name in ("S_Net", "S_Net3", "S_Yield"):
    cc = Counter()
    for s in SEEDS:
        for k, v in order([(k, v) for k, v in filt.items() if v["seed"] == s], name)[:20]:
            cc[(v["cls_at_Pclear_wk"], v["cls"])] += 1
    print(f"\n{name} top-20 x 6 seeds by b-class at the asked week (nC,nU,nP,roots,rep,fresh) -> class:")
    byc = {}
    for (c, cl), n in cc.items(): byc.setdefault(c, Counter())[cl] += n
    for c, cnt in sorted(byc.items(), key=lambda kv: -sum(kv[1].values())): print(f"   {c:24s} {dict(cnt)}")
