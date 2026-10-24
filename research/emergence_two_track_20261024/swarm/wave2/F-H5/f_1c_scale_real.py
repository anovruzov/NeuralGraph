"""F-H5 KILL 1 (scale), REAL arm: score H5-2 / H5-3 on the cells the shipped detector itself sees at SIZE users
(realcells-<size>-seed-<s>.json from f_1b_realcells.py) with the detector's OWN rule_weeks and channels, so the pool is
the real rule-only pool (channels == ['rule'], rule_weeks >= 7) exactly as H5 defined it at 200 users.
Labels (the labels doc) score only. Usage: f_1c_scale_real.py SIZE seed,seed,..."""
import sys, json, os, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *
from collections import Counter, defaultdict

size = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(",")]
seeds = [s for s in seeds if os.path.exists(f"{OUT}/realcells-{size}-seed-{s}.json")]
print("seeds with real cells:", seeds)
allrows = []; allrows_any = []; per_seed = {}
for seed in seeds:
    D = json.load(open(f"{OUT}/realcells-{size}-seed-{seed}.json"))
    cells = D["cells"]; labels = D["labels"]; cands = D["candidates"]
    gold_keys = {p["key"] for p in labels["patterns"]}
    dcls = defaultdict(set)
    for x in labels["decoys"]:
        for k in x["keys"]: dcls[k].add(int(x["class"]))
    rows = []; rows_any = []
    for key, v in cands.items():
        rw = sorted(widx(w) for w in (v["rule_weeks"] or []))
        if len(rw) < 7: continue
        lab = "gold" if key in gold_keys else ("decoy" if key in dcls else "other")
        r = {"seed": seed, "key": key, "rule_weeks": rw, "label": lab, "dclass": tuple(sorted(dcls[key])) if lab == "decoy" else (),
             "channels": v["channels"]}
        r.update(h5_feats(site_weeks(cells.get(key, {}), True), rw))
        rows_any.append(r)
        if v["channels"] == ["rule"]: rows.append(r)
    allrows += rows; allrows_any += rows_any
    lab = Counter(r["label"] for r in rows)
    g = [-r["sync"] for r in rows if r["label"] == "gold"]; d = [-r["sync"] for r in rows if r["label"] == "decoy"]; o = [-r["sync"] for r in rows if r["label"] == "other"]
    t_avg = topn_avg(rows, gated, 20) if rows else (0, 0, 0); t_det = topn_det(rows, gated, 20)
    tl_avg = topn_avg(rows, lex, 20) if rows else (0, 0, 0)
    spr = Counter(D["region_of_site"].values())
    per_seed[seed] = {"n_pool": len(rows), "gold": lab["gold"], "decoy": lab["decoy"], "other": lab["other"], "auc_gd": auc(g, d), "auc_go": auc(g, o),
                      "sites": len(D["sites"]), "sites_per_region": sorted(spr.values()), "p20_gated_gold": t_avg[0] / 20, "p20_gated_decoy": t_avg[1] / 20,
                      "p20_gated_gold_det": sum(r["label"] == "gold" for r in t_det) / 20, "p20_lex_gold": tl_avg[0] / 20, "p20_lex_decoy": tl_avg[1] / 20,
                      "n_candidates": len(cands), "n_rule_only": sum(1 for v in cands.values() if v["channels"] == ["rule"]),
                      "with_snapshot": D["detected_with_snapshot"], "timings": D["timings"], "secs_total": D["secs_total"]}
    print(f"SIZE {size} seed {seed} REAL: candidates {len(cands)} rule-only {per_seed[seed]['n_rule_only']} with-snapshot {D['detected_with_snapshot']} | sites {len(D['sites'])} sites/region {sorted(spr.values())} | "
          f"rule-only pool(rw>=7) {len(rows)} = gold {lab['gold']} / decoy {lab['decoy']} / other {lab['other']} | H5-2 AUC g/decoy {auc(g, d):.3f} g/other {auc(g, o):.3f} | "
          f"gated1.25 P@20 gold {t_avg[0]/20:.2f} decoy {t_avg[1]/20:.2f} | lex P@20 gold {tl_avg[0]/20:.2f} decoy {tl_avg[1]/20:.2f} | secs {D['secs_total']}", flush=True)

for name, rows_ in (("RULE-ONLY pool (H5's definition)", allrows), ("ALL candidates rw>=7 (incl. snapshot keys)", allrows_any)):
    print(f"\n== SIZE {size} REAL cells, {name}, pooled over seeds {seeds} ==")
    report_auc(rows_, {(r["seed"], r["key"]): -r["sync"] for r in rows_}, f"neg sync (real, {size})")
    report_auc(rows_, {(r["seed"], r["key"]): -r["sync_max"] for r in rows_}, f"neg sync_max (real, {size})")
    report_auc(rows_, {(r["seed"], r["key"]): r["kcells"] for r in rows_}, f"n_kcells (real, {size})")
    report_auc(rows_, {(r["seed"], r["key"]): r["lr"] for r in rows_}, f"longest_run (real, {size})")
    order = sorted(rows_, key=lambda r: gated(r)); rank = {}; i = 0
    while i < len(order):
        j = i
        while j < len(order) and gated(order[j]) == gated(order[i]): j += 1
        for r in order[i:j]: rank[(r["seed"], r["key"])] = (i + j - 1) / 2
        i = j
    report_auc(rows_, rank, f"gated(1.25,k,lr) rank (real, {size})")
print("\n== gold sync distribution (rule-only pool): share of gold with sync <= 1.25 (passes the gate) by label ==")
for lab in ("gold", "decoy", "other"):
    xs = [r["sync"] for r in allrows if r["label"] == lab]
    if xs: print(f"  {lab:6s} n={len(xs):5d} pass-gate(sync<=1.25) {sum(1 for x in xs if x <= 1.25)/len(xs):.3f} median sync {statistics.median(xs):.3f}")
print("\n== per-seed ==")
for s, v in per_seed.items():
    print(f"  seed {s}: AUC g/decoy {v['auc_gd']:.3f} g/other {v['auc_go']:.3f} | pool {v['n_pool']} (gold {v['gold']} decoy {v['decoy']} other {v['other']}) | gated P@20 gold {v['p20_gated_gold']:.2f} decoy {v['p20_gated_decoy']:.2f} | timings {v['timings']}")
json.dump({"size": size, "per_seed": per_seed,
           "features": [{k: r[k] for k in ("seed", "key", "label", "dclass", "channels", "rule_weeks", "sync", "kcells", "lr", "sync_max", "lo", "hi", "w_maxk")} for r in allrows_any]},
          open(f"{OUT}/scale_{size}_real_features.json", "w"))
