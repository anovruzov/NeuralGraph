"""F-H5 KILL 1 (scale), rebuilt-cells arm. For each SIZE and dev seed: rebuild the world (f_common.build_cells = H5's
test_rebuild_cells.py at any size), recompute the pack rule's rule_weeks from the cells (H5's test_h0_cells_validity.py
rule), take the pool = keys with >= 7 rule weeks, compute H5-2's statistic EXACTLY as H5 (-mean sites per active week
inside the longest rule run widened by 8), and score AUC gold-vs-decoy overall and per decoy class; also the
pre-registered H5-3 ranker (1[sync<=1.25], kcells, longest_run) P@20 gold/decoy per seed.
At 200 users the pool here is 'all keys with >= 7 rule weeks' (no detector run), so it is a superset of H5's rule-only
pool: the 200 row is a validation of the pipeline, H5's own numbers are the reference. Labels score only.
Usage: f_1_scale.py SIZE seed,seed,...   (writes OUT/cells-<size>-seed-<s>.json and prints the table)"""
import sys, json, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *
from collections import Counter

size = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(",")]; assert all(s in range(6) for s in seeds)
allrows = []; per_seed = {}
for seed in seeds:
    cells, labels, meta = build_cells(size, seed)
    json.dump({"meta": meta, "cells": cells, "labels": labels}, open(f"{OUT}/cells-{size}-seed-{seed}.json", "w"))
    rw = {k: rule_weeks_from_cells(v, False) for k, v in cells.items()}
    rows = label_rows(cells, labels, rw, min_rw=7)
    for r in rows:
        r["seed"] = seed
        r.update(h5_feats(site_weeks(cells[r["key"]], False), r["rule_weeks"]))
    allrows += rows
    spr = Counter(meta["region_of_site"].values())
    lab = Counter(r["label"] for r in rows)
    g = [-r["sync"] for r in rows if r["label"] == "gold"]; d = [-r["sync"] for r in rows if r["label"] == "decoy"]; o = [-r["sync"] for r in rows if r["label"] == "other"]
    sub = rows
    t_det = topn_det(sub, gated, 20); t_avg = topn_avg(sub, gated, 20)
    tl_avg = topn_avg(sub, lex, 20)
    per_seed[seed] = {"n_pool": len(rows), "gold": lab["gold"], "decoy": lab["decoy"], "other": lab["other"],
                      "auc_gd": auc(g, d), "auc_go": auc(g, o), "sites": len(meta["sites"]), "sites_per_region": sorted(spr.values()),
                      "p20_gated_gold_det": sum(r["label"] == "gold" for r in t_det) / 20, "p20_gated_decoy_det": sum(r["label"] == "decoy" for r in t_det) / 20,
                      "p20_gated_gold_avg": t_avg[0] / 20, "p20_gated_decoy_avg": t_avg[1] / 20,
                      "p20_lex_gold_avg": tl_avg[0] / 20, "p20_lex_decoy_avg": tl_avg[1] / 20,
                      "gold_sync_median": statistics.median([r["sync"] for r in rows if r["label"] == "gold"]) if lab["gold"] else None,
                      "decoy_sync_median": statistics.median([r["sync"] for r in rows if r["label"] == "decoy"]) if lab["decoy"] else None,
                      "n_records": meta["n_records"], "secs": meta["secs"]}
    print(f"SIZE {size} seed {seed}: records {meta['n_records']} sites {len(meta['sites'])} sites/region {sorted(spr.values())} pool(rw>=7) {len(rows)} "
          f"= gold {lab['gold']} / decoy {lab['decoy']} / other {lab['other']} | H5-2 AUC g/decoy {auc(g, d):.3f} g/other {auc(g, o):.3f} | "
          f"sync median gold {per_seed[seed]['gold_sync_median']} decoy {per_seed[seed]['decoy_sync_median']} | "
          f"gated1.25 P@20 gold {t_avg[0]/20:.2f} decoy {t_avg[1]/20:.2f} (det {per_seed[seed]['p20_gated_gold_det']:.2f}/{per_seed[seed]['p20_gated_decoy_det']:.2f}) | "
          f"lex P@20 gold {tl_avg[0]/20:.2f} decoy {tl_avg[1]/20:.2f} | rebuild {meta['secs']}s", flush=True)

print(f"\n== SIZE {size} pooled over seeds {seeds}: H5-2 statistic = -mean sites per active week, longest-run window ==")
score = {(r["seed"], r["key"]): -r["sync"] for r in allrows}
res = report_auc(allrows, score, f"neg sync (size {size})")
score2 = {(r["seed"], r["key"]): -r["sync_max"] for r in allrows}
report_auc(allrows, score2, f"neg sync_max (size {size})")
score3 = {(r["seed"], r["key"]): r["kcells"] for r in allrows}
report_auc(allrows, score3, f"n_kcells (size {size})")
score4 = {(r["seed"], r["key"]): r["lr"] for r in allrows}
report_auc(allrows, score4, f"longest_run (size {size})")
# lexicographic rank of the gated ranker as a scalar score for AUC
order = sorted(allrows, key=lambda r: gated(r)); rank = {}; i = 0
while i < len(order):
    j = i
    while j < len(order) and gated(order[j]) == gated(order[i]): j += 1
    for r in order[i:j]: rank[(r["seed"], r["key"])] = (i + j - 1) / 2
    i = j
report_auc(allrows, rank, f"gated(1.25,k,lr) rank (size {size})")
print("\n== distribution of max co-active sites in the window (sync_max) by label ==")
for lab in ("gold", "decoy", "other"):
    c = Counter(r["sync_max"] for r in allrows if r["label"] == lab); print(f"  {lab:6s} n={sum(c.values()):5d}", sorted(c.items())[:10])
for cl in (1, 2, 3, 4, 5):
    c = Counter(r["sync_max"] for r in allrows if r["label"] == "decoy" and cl in r["dclass"]); print(f"  decoy{cl} n={sum(c.values()):5d}", sorted(c.items())[:10])
print("\n== per-seed summary ==")
for s, v in per_seed.items():
    print(f"  seed {s}: AUC g/decoy {v['auc_gd']:.3f} g/other {v['auc_go']:.3f} sites {v['sites']} sites/region {v['sites_per_region']} pool {v['n_pool']} gold {v['gold']} decoy {v['decoy']} other {v['other']} "
          f"gated P@20 gold {v['p20_gated_gold_avg']:.2f} decoy {v['p20_gated_decoy_avg']:.2f}")
json.dump({"size": size, "pooled": res, "per_seed": per_seed,
           "features": [{k: r[k] for k in ("seed", "key", "label", "dclass", "rule_weeks", "sync", "kcells", "lr", "sync_max", "lo", "hi", "w_maxk")} for r in allrows]},
          open(f"{OUT}/scale_{size}_features.json", "w"))
