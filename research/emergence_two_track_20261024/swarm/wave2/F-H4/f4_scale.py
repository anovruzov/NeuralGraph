"""KILL 4 -- SCALE. The thin-state mechanism (one certain k-site + exactly one weak site: class (1,0,2,>=3,>=3,fresh)) and the
whole S_Net3 class table at 2,000 users, dev seeds. Generator + gate model only (no detector dump exists at 2,000, so the
pool is every key with a record, all askable weeks; the same definition is applied at 200 users so the comparison is
like with like). Tests the CLASS, not co-activation."""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f_common import b_features, cls_of, fit_tables, auc, WIN, SEEDS
from f_gen import World
from collections import Counter, defaultdict
OUT = os.path.dirname(os.path.abspath(__file__))
SIZES = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["200", "2000"])]
SEEDL = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["0", "1", "2", "3", "4", "5"])]
THIN = lambda c: c[0] == 1 and c[1] == 0 and c[2] == 2 and c[3] >= 3 and c[4] >= 3 and c[5] == 1
def in_win(wins, wk): return any(lo <= wk <= hi for lo, hi in wins)

data = {}
for size in SIZES:
    rows = []; keyinfo = {}
    for seed in SEEDL:
        t0 = time.time(); W = World(size, seed); nk = 0
        for key in W.all_keys():
            cells = W.cells_for(key); lab = W.label(key); wins = W.windows(key)
            active = sorted({j for c in cells.values() for j, v in enumerate(c["n"]) if v != 0})
            if not active: continue
            nk += 1; rare = any(p["rare"] for p in W.gold_key.get(key, []))
            keyinfo[(size, str(seed), key)] = dict(label=lab, rare=rare, n_active=len(active), windows=wins,
                                                   n_sites_total=len(cells), weeks=[])
            for w in range(WIN - 1, len(W.weeks)):
                if not any(c["n"][j] != 0 for c in cells.values() for j in range(w - WIN + 1, w + 1)): continue
                f = b_features(cells, w); c = cls_of(f); tb = W.tb_at(key, w); wk = W.weeks[w]
                rows.append(dict(seed=str(seed), key=key, wk=wk, cls=c, f=f, tb=int(tb), gold=int(lab == "gold"), decoy=int(lab == "decoy"), rule=1))
                keyinfo[(size, str(seed), key)]["weeks"].append((wk, c, int(tb), in_win(wins, wk), f["nP"]))
        print(f"size {size} seed {seed}: sites {W.n_sites}, keys with records {nk}, rows so far {len(rows)}, gold keys {len(W.gold_key)}, decoy keys {len(W.decoy_key)}, {time.time()-t0:.1f} s", flush=True)
    data[size] = (rows, keyinfo)

def report(size, rows, keyinfo, fit_rows_200=None):
    print(f"\n===== size {size} =====")
    labs = Counter(v["label"] for v in keyinfo.values() if v["weeks"])
    pers = Counter(v["label"] for v in keyinfo.values() if v["weeks"] and v["n_active"] >= 7)
    print(f"keys with an askable week: {dict(labs)}; 'persistent' keys (>= 7 active weeks, proxy for rule_weeks >= 7): {dict(pers)}")
    # the thin class: rows and keys
    T = fit_tables(rows, [str(s) for s in SEEDL]); Tt = T["T"]
    thin_cls = [c for c in Tt["n"] if THIN(c)]
    n = sum(Tt["n"][c] for c in thin_cls); g = sum(Tt["g"][c] for c in thin_cls); d = sum(Tt["dd"][c] for c in thin_cls); cl = sum(Tt["c"][c] for c in thin_cls)
    print(f"THIN class rows (1,0,2,>=3,>=3,1): {n} rows; gold {g} ({g/max(1,n):.1%}), decoy {d} ({d/max(1,n):.1%}), other {n-g-d} ({(n-g-d)/max(1,n):.1%}); P(clear) {cl/max(1,n):.3f}; net3 {T['net3'](thin_cls[0]) if thin_cls else float('nan'):+.3f}")
    # key level: passes through thin at an in-window week
    kk = Counter(); kk_any = Counter()
    for (sz, s, key), v in keyinfo.items():
        if not v["weeks"]: continue
        kk_any[v["label"]] += 1
        if any(THIN(c) and (iw or v["label"] == "other") for wk, c, tb, iw, nP in v["weeks"]): kk[v["label"]] += 1
    print("keys with >= 1 THIN week (inside the label window for gold/decoy):", {l: f"{kk[l]}/{kk_any[l]} = {kk[l]/max(1,kk_any[l]):.1%}" for l in ("gold", "decoy", "other")})
    rare_thin = sum(1 for v in keyinfo.values() if v["label"] == "gold" and v["rare"] and any(THIN(c) and iw for wk, c, tb, iw, nP in v["weeks"]))
    rare_all = sum(1 for v in keyinfo.values() if v["label"] == "gold" and v["rare"] and v["weeks"])
    comm_thin = kk["gold"] - rare_thin; comm_all = kk_any["gold"] - rare_all
    print(f"  gold by rarity: rare {rare_thin}/{rare_all}, common {comm_thin}/{comm_all}  (H4: 'only RARE gold passes through the thin state')")
    # breadth at in-window weeks: nP distribution for gold vs decoy
    npg = Counter(); npd = Counter()
    for v in keyinfo.values():
        for wk, c, tb, iw, nP in v["weeks"]:
            if not iw: continue
            (npg if v["label"] == "gold" else npd if v["label"] == "decoy" else Counter())[min(nP, 4)] += 1
    print(f"  sites present (nP, capped 4) at in-window weeks: gold {dict(sorted(npg.items()))}; decoy {dict(sorted(npd.items()))}")
    nsg = Counter(min(len(p['sites']), 5) for (sz, s, key), v in keyinfo.items() if v["label"] == "gold" for p in [None] ) if False else None
    # class table top by net3, with composition
    seen = [c for c in Tt["n"] if Tt["n"][c] >= 5]
    print("  top-6 classes by net3 (pooled fit at this size), rows / gold / decoy / P(clear):")
    for c in sorted(seen, key=lambda c: -T["net3"](c))[:6]:
        print(f"    {str(c):22s} net3 {T['net3'](c):+.3f} rows {Tt['n'][c]:5d} gold {Tt['g'][c]:4d} decoy {Tt['dd'][c]:4d} P(clear) {T['pclear'](c):.3f}")
    # AUCs: key score = max over askable weeks of net3
    by_sk = defaultdict(list)
    for r in rows: by_sk[(r["seed"], r["key"])].append(r)
    def aucs(fit_for, label):
        for poolname, cond in (("all keys", lambda v: True), ("persistent (>=7 active weeks)", lambda v: v["n_active"] >= 7)):
            pos = []; neg = []; per = {}
            for s in [str(x) for x in SEEDL]:
                Fs = fit_for(s); ps = []; ns = []
                for (sz, sd, key), v in keyinfo.items():
                    if sd != s or not v["weeks"] or not cond(v): continue
                    sc = max(Fs["net3"](c) for wk, c, tb, iw, nP in v["weeks"])
                    if v["label"] == "gold": ps.append(sc)
                    elif v["label"] == "decoy": ns.append(sc)
                per[s] = round(auc(ps, ns), 3); pos += ps; neg += ns
            print(f"  AUC gold-vs-decoy, {label}, {poolname:30s}: pooled {auc(pos, neg):.3f}  per seed {per}  (n gold {len(pos)}, decoy {len(neg)})")
    aucs(lambda s: fit_tables(rows, [t for t in [str(x) for x in SEEDL] if t != s]), "LOSO fit at this size")
    if fit_rows_200 is not None:
        F200 = fit_tables(fit_rows_200, [str(x) for x in SEEDL]); aucs(lambda s: F200, "table fitted at 200 users, transferred")
    # thin-only indicator AUC (parameter-free)
    for poolname, cond in (("all keys", lambda v: True), ("persistent", lambda v: v["n_active"] >= 7)):
        pos = []; neg = []
        for v in keyinfo.values():
            if not v["weeks"] or not cond(v): continue
            sc = int(any(THIN(c) for wk, c, tb, iw, nP in v["weeks"]))
            (pos if v["label"] == "gold" else neg if v["label"] == "decoy" else []).append(sc)
        print(f"  AUC of the bare THIN indicator (ever thin), {poolname}: {auc(pos, neg):.3f}")

for size in SIZES:
    rows, keyinfo = data[size]
    report(size, rows, keyinfo, fit_rows_200=(data[200][0] if (200 in data and size != 200) else None))
