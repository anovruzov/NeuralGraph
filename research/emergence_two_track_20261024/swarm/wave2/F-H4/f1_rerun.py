"""KILL 1 -- ONE PROCEDURE. H4-3 re-run exactly once as pre-registered: class (nC,nU,nP,roots_lb,rep_lb,fresh) from the
k-suppressed weekly cells, nu = (1,1) [S_Net3], LOSO fit (alpha = 2), key score = max over its rule weeks, strict salted
ordering, filtered pool (rule_only, rule_weeks >= 7). Pooled AUC gold-vs-decoy and every held-out seed. S_Net (nu=(1,0),
the 0.794 that was promoted) is reported alongside for reference only -- it is NOT the pre-registered falsifier target."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f_common import *
from collections import Counter

D = load_cells(); rows = build_rows(D); print("rows (key, week):", len(rows))
per_key = score_keys(D, rows, loso)
F = filtered(per_key); items = list(F.items())
print("filtered pool:", len(F), dict(Counter(v["cls"] for v in F.values())))

out = {}
for name in ("S_Net3", "S_Net", "baseline"):
    pooled = strict_auc(items, name, "decoy")
    per = {s: strict_auc([(k, v) for k, v in items if v["seed"] == s], name, "decoy") for s in SEEDS}
    tie = auc([v[name] if name != "baseline" else v["rule_weeks"] for k, v in items if v["cls"] == "gold"],
              [v[name] if name != "baseline" else v["rule_weeks"] for k, v in items if v["cls"] == "decoy"])
    out[name] = dict(pooled_strict=round(pooled, 3), pooled_tieshalf=round(tie, 3), per_seed={s: round(x, 3) for s, x in per.items()},
                     min_seed=round(min(per.values()), 3), max_seed=round(max(per.values()), 3))
    print(f"{name:9s} pooled(strict) {pooled:.3f}  pooled(ties=.5) {tie:.3f}  per-seed " + " ".join(f"{per[s]:.3f}" for s in SEEDS)
          + f"  min {min(per.values()):.3f} max {max(per.values()):.3f}")

H4_REPORTED = {"S_Net3": (0.731, [0.797, 0.641, 0.777, 0.806, 0.676, 0.717]), "S_Net": (0.794, [0.826, 0.790, 0.787, 0.798, 0.800, 0.741])}
print("\nagainst H4's reported numbers:")
for name, (p, per) in H4_REPORTED.items():
    mine = out[name]
    print(f"  {name}: H4 pooled {p} vs re-run {mine['pooled_strict']}; H4 per-seed {per} vs re-run {[mine['per_seed'][s] for s in SEEDS]}")

print("\nverdict lines (pre-registered target = S_Net3, nu=(1,1)):")
per = out["S_Net3"]["per_seed"]
below70 = [s for s in SEEDS if per[s] < 0.70]
below_h8 = [s for s in SEEDS if per[s] < 0.63]; below_h8_low = [s for s in SEEDS if per[s] < 0.61]
print(f"  H4-3's own KILL ('any held-out seed below 0.70'): seeds below 0.70 = {below70} -> {'KILLED by its own criterion' if below70 else 'passes'}")
print(f"  H8 single-seed line 0.61-0.63: seeds below 0.63 = {below_h8}; below 0.61 = {below_h8_low}")
print(f"  H8 pooled line (one pre-registered statistic, p<0.05): 0.549 -> pooled {out['S_Net3']['pooled_strict']} {'above' if out['S_Net3']['pooled_strict'] > 0.549 else 'below'}")
print(f"  H8 free reference line (longest rule run, 0.668): pooled {'above' if out['S_Net3']['pooled_strict'] > 0.668 else 'below'}; seeds below 0.668 = {[s for s in SEEDS if per[s] < 0.668]}")
print(f"  H8 best-of-50 line 0.62: pooled {'above' if out['S_Net3']['pooled_strict'] > 0.62 else 'below'}")
print(f"  H8 leak alarm >= 0.79: S_Net3 {'TRIPS' if out['S_Net3']['pooled_strict'] >= 0.79 else 'does not trip'}; S_Net (nu=(1,0)) {'TRIPS' if out['S_Net']['pooled_strict'] >= 0.79 else 'does not trip'}")

# the fitted object: how many classes, how many carry the gold
all_fit = fit_tables(rows, SEEDS); T = all_fit["T"]
rule_cls = Counter(r["cls"] for r in rows if r["rule"])
print(f"\nfitted table granularity: {len(T['n'])} distinct classes over all rows; {len(rule_cls)} classes seen at rule weeks")
gold_rows_by_cls = Counter(r["cls"] for r in rows if r["gold"]); dec_rows_by_cls = Counter(r["cls"] for r in rows if r["decoy"])
gold_keys_by_cls = {}; 
for r in rows:
    if r["gold"]: gold_keys_by_cls.setdefault(r["cls"], set()).add((r["seed"], r["key"]))
print("top classes by net3 (pooled fit) seen at rule weeks, with rows / gold rows / gold KEYS / decoy rows:")
top = sorted(rule_cls, key=lambda k: -all_fit["net3"](k))[:12]
for k in top:
    print(f"  {str(k):22s} net3 {all_fit['net3'](k):+.3f} rows {T['n'][k]:5d} gold_rows {T['g'][k]:4d} gold_keys {len(gold_keys_by_cls.get(k, ())):3d} decoy_rows {T['dd'][k]:4d} P(clear) {all_fit['pclear'](k):.3f}")
# which class each filtered gold / decoy key is scored at (argmax week class), per seed
cls_at = Counter((v["cls"], v["S_Net3_cls"]) for v in F.values() if v["cls"] in ("gold", "decoy"))
print("\nfiltered gold/decoy keys by the class at their S_Net3 argmax week (top 10):")
byc = {}
for (cl, c), n in cls_at.items(): byc.setdefault(c, Counter())[cl] += n
for c, cnt in sorted(byc.items(), key=lambda kv: -sum(kv[1].values()))[:10]: print(f"  {str(c):22s} {dict(cnt)}")
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_rerun.json"), "w"), indent=1)
