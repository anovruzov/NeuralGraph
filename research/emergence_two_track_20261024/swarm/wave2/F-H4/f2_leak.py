"""KILL 2 -- LEAKAGE, H8 section 5 in full, applied to H4-3 (S_Net3; S_Net alongside because it is the number that was promoted).
 (1) field provenance: the class features vs the exported contract, AND H4's generator-rebuilt cells vs HQ's ACTUAL stored
     cells (pool_s*.json win_lb / win_sites from store.detection_inputs) at every asked (key, week);
 (3) fitted-table granularity: indexed by class only? how many classes; rows per key by label; where the AUC comes from;
 (4) label-flip: held-out key (must not move) and a training key (fragility);
 (5) seed-swap: refit on 4 of the 5 training seeds and on a 3/3 split;
 (8) too-good-to-be-honest: a PERMUTATION null for the WHOLE fitted LOSO procedure (labels permuted within seed, refit,
     rescore), which H4's random-order null did not include. Writes f2_leak.json."""
import sys, os, json, random, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f_common import *
from collections import Counter, defaultdict
OUT = os.path.dirname(os.path.abspath(__file__))
NPERM = int(sys.argv[1]) if len(sys.argv) > 1 else 200
D = load_cells(); rows = build_rows(D); per_key = score_keys(D, rows, loso); F = filtered(per_key); items = list(F.items())
res = {}

# ---------------------------------------------------------------- (1) provenance vs HQ's actual cells
print("== (1) field provenance")
print("features: nC, nU, nP, roots_lb, rep_lb, fresh from per-site weekly cells (n, n_roots, n_reporters; '<k' or count) over [w-5, w].")
print("contract: CellRow(site, entity_type, entity_id, predicate, iso_week, channel, n, n_roots, n_reporters, res_conf_min, bundle, as_of) -- all three counts ARE exported, k-suppressed.")
print("BUT H4 did not read HQ's cells: test_h4_extract.py rebuilds them from corpus.recs (every record on the key, polarity-blind). Check against HQ's actual stored cells:")
agree_lb = tot = agree_sites = exact_rows = 0; diffs = Counter(); per_seed = {}
for s in SEEDS:
    pool = json.load(open(os.path.join(R, "raw/rescue", f"pool_s{s}.json"))); S = D[s]; widx = {wk: i for i, wk in enumerate(S["weeks"])}
    a = t = ar = 0
    for r in pool["rows"]:
        v = S["keys"].get(r["key"]); 
        if v is None or r["week"] not in widx: continue
        i = widx[r["week"]]; win = list(range(max(0, i - 5), i + 1))
        h4_lb = [sum(lbub(c["n"][j])[0] for c in v["cells"].values()) for j in win]
        h4_sites = [sum(1 for c in v["cells"].values() if c["n"][j] != 0) for j in win]
        hq_lb = r["win_lb"][-len(win):]; hq_sites = r["win_sites"][-len(win):]
        t += 1; tot += 1
        if h4_lb == hq_lb: a += 1; agree_lb += 1
        else: diffs[("lb", "h4>hq" if sum(h4_lb) > sum(hq_lb) else "h4<hq")] += 1
        if h4_sites == hq_sites: ar += 1; agree_sites += 1
        if h4_lb == hq_lb and h4_sites == hq_sites: exact_rows += 1
    per_seed[s] = (a, ar, t)
print(f"  asked rows compared (all v4 pool rows found in h4_cells): {tot}; window lower-bound vector identical: {agree_lb} ({agree_lb/tot:.1%}); site-count vector identical: {agree_sites} ({agree_sites/tot:.1%}); both: {exact_rows}")
print(f"  per seed (lb agree, sites agree, n): {per_seed}; disagreement direction: {dict(diffs)}")
res["provenance"] = dict(rows=tot, lb_identical=agree_lb, sites_identical=agree_sites, both=exact_rows, per_seed=per_seed, direction={str(k): v for k, v in diffs.items()})
print("  (roots / reporters per cell cannot be checked against the pool dump -- it recorded only n. CellRow carries them; provenance of those two is by contract reading only.)")

# ---------------------------------------------------------------- (3) fitted table granularity and where the AUC lives
print("\n== (3) fitted table")
Fit = fit_tables(rows, SEEDS); T = Fit["T"]
print(f"  index: the 6-tuple class only (code: Counter keyed by r['cls']); {len(T['n'])} classes populated; no entity / seed / key index.")
rk = defaultdict(int); 
for r in rows: rk[(r["seed"], r["key"])] += 1
by_lab = defaultdict(list)
for (s, k), n in rk.items(): by_lab[D[s]["keys"][k]["class"]].append(n)
print("  rows (askable weeks) per key by label -- the fit weights a key by its number of askable weeks:", {l: round(sum(x)/len(x), 1) for l, x in by_lab.items()})
# where does the AUC come from: keys whose argmax class never clears
def never_clears(v): return Fit["pclear"](v["S_Net3_cls"]) < 0.01
nz = [(k, v) for k, v in items if not never_clears(v)]; z = [(k, v) for k, v in items if never_clears(v)]
print(f"  filtered keys scored at a class with P(clear) < 0.01 (can never yield a verdict): {len(z)}/{len(items)}; gold {sum(v['cls']=='gold' for k,v in z)}/138, decoy {sum(v['cls']=='decoy' for k,v in z)}/317")
print(f"  AUC gold-vs-decoy among those never-clearing keys alone (ordering by ~1e-6 smoothing residues): {strict_auc(z, 'S_Net3'):.3f}; among the rest: {strict_auc(nz, 'S_Net3'):.3f}")
# pair decomposition
G = [(k, v) for k, v in items if v["cls"] == "gold"]; Dd = [(k, v) for k, v in items if v["cls"] == "decoy"]
rank = {k: i for i, (k, v) in enumerate(order(items, "S_Net3"))}
def pairs(gs, ds): return sum(1 for g in gs for d in ds if rank[g[0]] < rank[d[0]]), len(gs) * len(ds)
zg = [x for x in G if never_clears(x[1])]; zd = [x for x in Dd if never_clears(x[1])]; ng_ = [x for x in G if not never_clears(x[1])]; nd_ = [x for x in Dd if not never_clears(x[1])]
w, n = pairs(zg, zd); print(f"  pairs (gold,decoy) both never-clearing: {n} of {len(G)*len(Dd)} ({n/(len(G)*len(Dd)):.1%}); gold ranked above decoy in {w/n:.3f} of them")
w2, n2 = pairs(ng_, zd); print(f"  pairs clearing-gold vs never-clearing-decoy: {n2}; gold above in {w2/n2:.3f}")
w3, n3 = pairs(zg, nd_); print(f"  pairs never-clearing-gold vs clearing-decoy: {n3}; gold above in {w3/n3:.3f}")
w4, n4 = pairs(ng_, nd_); print(f"  pairs both clearing: {n4}; gold above in {w4/n4:.3f}")
res["auc_decomposition"] = dict(never_clearing_keys=len(z), gold_nc=len(zg), decoy_nc=len(zd), auc_nc_only=strict_auc(z, 'S_Net3'), auc_clearing_only=strict_auc(nz, 'S_Net3'),
                                pairs_nc_nc=(w, n), pairs_cg_ncd=(w2, n2), pairs_ncg_cd=(w3, n3), pairs_c_c=(w4, n4))
# the near-zero classes: what orders them
print("  near-zero classes at which filtered gold/decoy keys are scored, with their net3 (pooled fit) -- the sign/magnitude is smoothing arithmetic:")
cc = Counter(v["S_Net3_cls"] for k, v in z)
for c, n_ in cc.most_common(6):
    print(f"    {str(c):22s} keys {n_:4d}  net3 {Fit['net3'](c):+.2e}  P(gold|c) {Fit['pgold'](c):.3f}  rows {T['n'][c]}  clears {T['c'][c]}")

# ---------------------------------------------------------------- (4) label-flip
print("\n== (4) label-flip")
# held-out key: its own label is not in its fit -> cannot move (LOSO by construction)
k0 = next(k for k, v in items if v["cls"] == "gold")
gold_of = {(r["seed"], r["key"]): r["gold"] for r in rows}; decoy_of = {(r["seed"], r["key"]): r["decoy"] for r in rows}
g2 = dict(gold_of); g2[k0] = 0; d2 = dict(decoy_of); d2[k0] = 1
pk2 = score_keys(D, rows, loso, gold_of=g2, decoy_of=d2)
print(f"  flip held-out key {k0}: s moves by {pk2[k0]['S_Net3'] - F[k0]['S_Net3']:+.3e} (LOSO: cannot move; passes trivially)")
# training key: flip one gold key in a training seed, measure movement of held-out scores
moved = []
for s in SEEDS:
    tr = loso(s)[0]; kt = next(k for k, v in items if v["seed"] == tr and v["cls"] == "gold")
    g3 = dict(gold_of); g3[kt] = 0; d3 = dict(decoy_of); d3[kt] = 1
    Fit3 = fit_tables(rows, loso(s), gold_of=g3, decoy_of=d3); Fit0 = fit_tables(rows, loso(s))
    deltas = []; flips = 0
    for k, v in items:
        if v["seed"] != s: continue
        s_new = max(Fit3["net3"](r[2]) for r in v["rule_rows"]); deltas.append(abs(s_new - v["S_Net3"]))
    it = [(k, v) for k, v in items if v["seed"] == s]
    for k, v in it: v["S_tmp"] = max(Fit3["net3"](r[2]) for r in v["rule_rows"])
    a0 = strict_auc(it, "S_Net3"); a1 = strict_auc(it, "S_tmp")
    moved.append((s, kt, max(deltas), a0, a1))
print("  flip ONE training gold key -> held-out seed: max |delta s|, AUC before -> after")
for s, kt, m, a0, a1 in moved: print(f"    held-out {s}: flipped {kt[1]} (seed {kt[0]}): max|ds| {m:.3e}; AUC {a0:.3f} -> {a1:.3f}")
res["label_flip"] = dict(heldout_moves=pk2[k0]['S_Net3'] - F[k0]['S_Net3'], training_flip=[(s, kt[1], m, a0, a1) for s, kt, m, a0, a1 in moved])

# ---------------------------------------------------------------- (5) seed-swap
print("\n== (5) seed-swap: refit on 4 of the 5 training seeds (drop one), per held-out seed; and a 3/3 split")
swap = {}
for s in SEEDS:
    it = [(k, v) for k, v in items if v["seed"] == s]; row = []
    for drop in loso(s):
        Fit4 = fit_tables(rows, [t for t in loso(s) if t != drop])
        for k, v in it: v["S_swap"] = max(Fit4["net3"](r[2]) for r in v["rule_rows"])
        a = strict_auc(it, "S_swap")
        # top-5 overlap with the 5-seed fit
        top5 = {k for k, v in order(it, "S_Net3")[:5]}; top5b = {k for k, v in order(it, "S_swap")[:5]}
        row.append((drop, round(a, 3), len(top5 & top5b)))
    swap[s] = row
    print(f"  held-out {s}: 5-seed AUC {strict_auc(it, 'S_Net3'):.3f}; 4-seed fits (dropped, AUC, top5 overlap/5): {row}")
for train, test in ((["0", "1", "2"], ["3", "4", "5"]), (["3", "4", "5"], ["0", "1", "2"])):
    Fit3 = fit_tables(rows, train); it = [(k, v) for k, v in items if v["seed"] in test]
    for k, v in it: v["S_swap"] = max(Fit3["net3"](r[2]) for r in v["rule_rows"])
    per = {s: round(strict_auc([(k, v) for k, v in it if v["seed"] == s], "S_swap"), 3) for s in test}
    print(f"  fit on {train} -> score {test}: pooled AUC {strict_auc(it, 'S_swap'):.3f}; per seed {per}")
    swap[f"fit{''.join(train)}"] = per
# does the top class survive every fold? (H4's own kill: 'the top classes differing across folds')
tops = {}
for s in SEEDS:
    Fs = fit_tables(rows, loso(s)); seen = {r["cls"] for r in rows if r["seed"] == s and r["rule"]}
    tops[s] = sorted(seen, key=lambda c: -Fs["net3"](c))[:3]
print("  top-3 classes by net3 per fold:", {s: [str(c) for c in t] for s, t in tops.items()})
res["seed_swap"] = swap

# ---------------------------------------------------------------- (8) permutation null for the whole fitted procedure
print(f"\n== (8) too-good-to-be-honest: permutation null of the WHOLE LOSO fit+score ({NPERM} permutations; labels permuted among keys within seed)")
rng = random.Random(20261024); t0 = time.time()
keys_by_seed = {s: [k for k in D[s]["keys"]] for s in SEEDS}
labels_by_seed = {s: [D[s]["keys"][k]["class"] for k in keys_by_seed[s]] for s in SEEDS}
by_sk = defaultdict(list)
for r in rows: by_sk[(r["seed"], r["key"])].append(r)
nulls = []; nulls_per = []
for p in range(NPERM):
    gold_p = {}; decoy_p = {}; lab_p = {}
    for s in SEEDS:
        perm = labels_by_seed[s][:]; rng.shuffle(perm)
        for k, l in zip(keys_by_seed[s], perm):
            gold_p[(s, k)] = int(l == "gold"); decoy_p[(s, k)] = int(l == "decoy"); lab_p[(s, k)] = l
    pooled_items = []; per = {}
    for s in SEEDS:
        Fp = fit_tables(rows, loso(s), gold_of=gold_p, decoy_of=decoy_p)
        it = []
        for k, v in items:
            if v["seed"] != s: continue
            sc = max(Fp["net3"](r[2]) for r in v["rule_rows"])
            it.append((k, dict(S_perm=sc, rule_weeks=v["rule_weeks"], salt=v["salt"], cls=lab_p[k])))
        per[s] = strict_auc(it, "S_perm"); pooled_items += it
    nulls.append(strict_auc(pooled_items, "S_perm")); nulls_per.append(per)
    if p == 0: print(f"  first permutation took {time.time()-t0:.1f} s")
nulls_s = sorted(nulls)
print(f"  pooled AUC under the permutation null: mean {sum(nulls)/len(nulls):.3f}, 95th pct {nulls_s[int(0.95*len(nulls_s))]:.3f}, 99th {nulls_s[int(0.99*len(nulls_s))-1]:.3f}, max {nulls_s[-1]:.3f}")
print(f"  observed S_Net3 pooled 0.731: exceeded by {sum(1 for x in nulls if x >= 0.731)}/{NPERM} permutations; S_Net 0.794: by {sum(1 for x in nulls if x >= 0.794)}/{NPERM}")
mins = sorted(min(p.values()) for p in nulls_per)
print(f"  min-over-seeds AUC under the null: 95th pct {mins[int(0.95*len(mins))]:.3f}, max {mins[-1]:.3f} (observed S_Net3 min 0.641, S_Net min 0.741)")
res["perm_null"] = dict(n=NPERM, mean=sum(nulls)/len(nulls), p95=nulls_s[int(0.95*len(nulls_s))], max=nulls_s[-1], ge_0731=sum(1 for x in nulls if x >= 0.731), ge_0794=sum(1 for x in nulls if x >= 0.794), min_seed_p95=mins[int(0.95*len(mins))], min_seed_max=mins[-1])
json.dump(res, open(os.path.join(OUT, "f2_leak.json"), "w"), indent=1, default=str)
print("done", round(time.time() - t0, 1), "s for the null")
