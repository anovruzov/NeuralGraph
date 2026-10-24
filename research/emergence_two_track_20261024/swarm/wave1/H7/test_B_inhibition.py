"""H7 question (B): contradiction as a NEGATIVE WEIGHT. Per site in a 6-week window, net support = pos - ALPHA*neg,
then bucketed as the gate buckets it. ALPHA sweeps the whole spectrum between the two extremes already measured:
  'blind' (model B: contradiction counted as support), 0 (model K: ignored), 0.5, 1, 2, 3 (inhibition), 'drop' (Ks).
Two statistics per key over the key's rule weeks W (the weeks a candidate existed; variant: all 26 weeks):
  S_a(key) = max_w  sum over sites of lb(net support bucket)        (graded, gate-shaped)
  P_a(key) = number of w in W at which TB_v4 passes under model a   (the gate itself, as a count)
Pool: wave-0 filtered rule-only pool (rule_weeks >= 7), 1,765 keys. Labels only score.
Reported: AUC gold vs decoy (overall, per decoy class 1-5, vs other), precision at top-20/50 per seed vs the
rule_weeks baseline, gate-level pass counts by class, contradiction prevalence by class, and leave-one-seed-out
choice of ALPHA (chosen on five seeds by pooled AUC gold-vs-decoy of S, scored on the sixth) with its spread.
"""
import json, sys
from collections import defaultdict, Counter
from h7lib import *

ALPHAS = ["blind", 0, 0.5, 1, 2, 3, "drop"]
# which weeks a key is scored at: rule (default) | all | last1 | last3 | first3   (--weeks=<mode>)
WEEKS_MODE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--weeks=")), "all" if "--all-weeks" in sys.argv else "rule")
ALL_WEEKS = WEEKS_MODE == "all"


def pick_weeks(rule_idx, n_weeks):
    if WEEKS_MODE == "all": return list(range(n_weeks))
    if WEEKS_MODE == "last1": return rule_idx[-1:]
    if WEEKS_MODE == "last3": return rule_idx[-3:]
    if WEEKS_MODE == "first3": return rule_idx[:3]
    return rule_idx
CLASSES = ["gold", "d1", "d2", "d3", "d4", "d5", "other"]


def classes_of(v):
    if v["class"] == "gold": return ["gold"]
    if v["class"] == "decoy": return [f"d{c}" for c in v["decoy_classes"]] or ["decoy?"]
    return ["other"]


def stats_for(v, W):
    out = {}
    for a in ALPHAS:
        S = 0; P = 0
        for w in W:
            st = site_stats(window_recs(v["recs"], w - WIN + 1, w), a)
            S = max(S, support_lb(st)); P += tb_v4(st, w)[0]
        out[a] = (S, P)
    return out


rows = []   # one per (seed, key): seed, classes, rule_weeks, has_neg, {alpha: (S, P)}
for seed in range(6):
    D = load(seed); widx = {w: i for i, w in enumerate(D["weeks"])}
    for key, v in D["keys"].items():
        if v["detector_state"] != "rule_only" or len(v["rule_weeks"]) < 7: continue
        W = pick_weeks([widx[w] for w in v["rule_weeks"]], len(D["weeks"]))
        neg_in_window = any(r[4] < 0 for w in W for r in window_recs(v["recs"], w - WIN + 1, w))
        rows.append({"seed": seed, "key": key, "cls": classes_of(v), "is_gold": v["class"] == "gold",
                     "is_decoy": v["class"] == "decoy", "rule_weeks": len(v["rule_weeks"]), "neg": neg_in_window,
                     "st": stats_for(v, W)})
print(f"filtered pool: {len(rows)} keys, gold {sum(r['is_gold'] for r in rows)}, decoy {sum(r['is_decoy'] for r in rows)}"
      f" (weeks: {WEEKS_MODE})\n")

# ---- mechanism check: where does contradiction live?
print("## B0. Contradiction prevalence: keys with any polarity -1 record inside a rule-week window, by class\n")
print("| class | keys | with contradiction | share |"); print("|---|---|---|---|")
for c in CLASSES:
    sub = [r for r in rows if c in r["cls"]]
    n = sum(r["neg"] for r in sub)
    print(f"| {c} | {len(sub)} | {n} | {n/len(sub):.3f} |" if sub else f"| {c} | 0 | 0 | - |")

# ---- AUC tables
def auc_table(stat_idx, name):
    print(f"\n## B1-{name}. AUC on the filtered pool, statistic {name}\n")
    print("| alpha | gold vs decoy | vs d1 | vs d2 | vs d3 | vs d4 | vs d5 | gold vs other | gold vs non-gold |")
    print("|" + "---|" * 9)
    out = {}
    for a in ALPHAS:
        g = [r["st"][a][stat_idx] for r in rows if r["is_gold"]]
        d = [r["st"][a][stat_idx] for r in rows if r["is_decoy"]]
        o = [r["st"][a][stat_idx] for r in rows if not r["is_gold"] and not r["is_decoy"]]
        per = [auc(g, [r["st"][a][stat_idx] for r in rows if f"d{c}" in r["cls"]]) for c in range(1, 6)]
        out[str(a)] = {"decoy": auc(g, d), "per_class": per, "other": auc(g, o), "nongold": auc(g, d + o)}
        print(f"| {a} | {auc(g, d):.3f} | " + " | ".join(f"{x:.3f}" for x in per) + f" | {auc(g, o):.3f} | {auc(g, d + o):.3f} |")
    return out


res = {"S": auc_table(0, "S (max net support_lb)"), "P": auc_table(1, "P (TB_v4 pass-weeks)")}
# baseline AUCs
g = [r["rule_weeks"] for r in rows if r["is_gold"]]; d = [r["rule_weeks"] for r in rows if r["is_decoy"]]
o = [r["rule_weeks"] for r in rows if not r["is_gold"] and not r["is_decoy"]]
print(f"\nbaseline rule_weeks on the same pool: AUC gold vs decoy {auc(g, d):.3f}, per class "
      + " ".join(f"d{c}={auc(g, [r['rule_weeks'] for r in rows if f'd{c}' in r['cls']]):.3f}" for c in range(1, 6))
      + f", gold vs other {auc(g, o):.3f}, gold vs non-gold {auc(g, d + o):.3f}")
res["baseline"] = {"decoy": auc(g, d), "other": auc(g, o), "nongold": auc(g, d + o)}

# ---- gate-level pass counts by class (the cost of each alpha at the gate)
print("\n## B2. Keys with >= 1 TB_v4 pass-week at their rule weeks, by class and alpha (filtered pool)\n")
print("| alpha | " + " | ".join(CLASSES) + " |"); print("|" + "---|" * (len(CLASSES) + 1))
for a in ALPHAS:
    cnt = Counter()
    for r in rows:
        if r["st"][a][1] > 0:
            for c in r["cls"]: cnt[c] += 1
    print(f"| {a} | " + " | ".join(str(cnt[c]) for c in CLASSES) + " |")
    res.setdefault("gate_pass", {})[str(a)] = dict(cnt)
cnt = Counter()
for r in rows:
    for c in r["cls"]: cnt[c] += 1
print(f"| (pool) | " + " | ".join(str(cnt[c]) for c in CLASSES) + " |")

# ---- precision at top-20 / top-50 per seed vs rule_weeks baseline
print("\n## B3. Expected gold (and decoy) in the top-20 / top-50 per seed, statistic S, vs rule_weeks baseline\n")
print("| seed | pool | gold | baseline P@20 (decoy) | baseline P@50 (decoy) | " + " | ".join(f"a={a} P@20 / P@50" for a in ALPHAS) + " |")
print("|" + "---|" * (5 + len(ALPHAS)))
prec = defaultdict(lambda: defaultdict(list))
for seed in range(6):
    sub = [r for r in rows if r["seed"] == seed]
    gl = [r["is_gold"] for r in sub]; dl = [r["is_decoy"] for r in sub]
    base = [r["rule_weeks"] for r in sub]
    cells = []
    for a in ALPHAS:
        sc = [r["st"][a][0] for r in sub]
        p20, p50 = precision_at(sc, gl, 20), precision_at(sc, gl, 50)
        d20, d50 = precision_at(sc, dl, 20), precision_at(sc, dl, 50)
        prec[a]["p20"].append(p20); prec[a]["p50"].append(p50); prec[a]["d50"].append(d50)
        cells.append(f"{p20:.1f} ({d20:.1f}) / {p50:.1f} ({d50:.1f})")
    b20, b50 = precision_at(base, gl, 20), precision_at(base, gl, 50)
    prec["base"]["p20"].append(b20); prec["base"]["p50"].append(b50); prec["base"]["d50"].append(precision_at(base, dl, 50))
    print(f"| {seed} | {len(sub)} | {sum(gl)} | {b20:.1f} ({precision_at(base, dl, 20):.1f}) | {b50:.1f} ({precision_at(base, dl, 50):.1f}) | " + " | ".join(cells) + " |")
print("| sum | | | " + f"{sum(prec['base']['p20']):.1f} | {sum(prec['base']['p50']):.1f} ({sum(prec['base']['d50']):.1f}) | "
      + " | ".join(f"{sum(prec[a]['p20']):.1f} / {sum(prec[a]['p50']):.1f} ({sum(prec[a]['d50']):.1f})" for a in ALPHAS) + " |")
res["precision"] = {str(a): {k: v for k, v in prec[a].items()} for a in list(ALPHAS) + ["base"]}

# ---- leave-one-seed-out over alpha (statistic S; selection criterion: pooled AUC gold vs decoy on the five)
print("\n## B4. Leave-one-seed-out over alpha (statistic S): alpha chosen on five seeds by AUC gold-vs-decoy, scored on the sixth\n")
print("| held-out seed | alpha chosen | train AUC | held-out AUC gold vs decoy | held-out AUC gold vs non-gold | held-out P@50 gold (decoy) | baseline P@50 gold (decoy) |")
print("|" + "---|" * 7)
loso = []
for seed in range(6):
    train = [r for r in rows if r["seed"] != seed]; test = [r for r in rows if r["seed"] == seed]
    best = None
    for a in ALPHAS:
        v = auc([r["st"][a][0] for r in train if r["is_gold"]], [r["st"][a][0] for r in train if r["is_decoy"]])
        if best is None or v > best[1] + 1e-12: best = (a, v)
    a = best[0]
    ta = auc([r["st"][a][0] for r in test if r["is_gold"]], [r["st"][a][0] for r in test if r["is_decoy"]])
    tn = auc([r["st"][a][0] for r in test if r["is_gold"]], [r["st"][a][0] for r in test if not r["is_gold"]])
    sc = [r["st"][a][0] for r in test]; gl = [r["is_gold"] for r in test]; dl = [r["is_decoy"] for r in test]
    base = [r["rule_weeks"] for r in test]
    loso.append({"seed": seed, "alpha": a, "train_auc": best[1], "test_auc_decoy": ta, "test_auc_nongold": tn,
                 "p50": precision_at(sc, gl, 50), "d50": precision_at(sc, dl, 50),
                 "base_p50": precision_at(base, gl, 50), "base_d50": precision_at(base, dl, 50)})
    print(f"| {seed} | {a} | {best[1]:.3f} | {ta:.3f} | {tn:.3f} | {precision_at(sc, gl, 50):.1f} ({precision_at(sc, dl, 50):.1f}) | {precision_at(base, gl, 50):.1f} ({precision_at(base, dl, 50):.1f}) |")
sp = [x["test_auc_decoy"] for x in loso]
print(f"\nSPREAD held-out AUC gold vs decoy: min {min(sp):.3f} max {max(sp):.3f}; alphas chosen {[x['alpha'] for x in loso]}")
res["loso"] = loso
json.dump(res, open(f"{OUT}/results_B{'' if WEEKS_MODE == 'rule' else '_' + WEEKS_MODE}.json", "w"), indent=1, default=str)
