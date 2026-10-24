"""KILL 4: does the d4-vs-gold separation (0.998 at 200 users) and the veto's effect hold at 2,000 users (seed 0)?
No detector dumps exist at 2,000, so weeks are the LABEL windows (gold: pattern window; decoys: their decoy windows;
background keys: every week). The same protocol is run at 200 users so the two sizes compare like for like.
Roots: generator event id (H7's model) and the shipped nd rule (real RecordStore.ingest, (0.65, 14))."""
import json, sys
from collections import Counter, defaultdict
from f_common import *  # noqa

import os
SEED = int(os.environ.get("F_SEED", "0"))
assert SEED in range(6)
sizes = [int(x) for x in sys.argv[1:]] or [200, 2000]
res = {}
for size in sizes:
    D = build(size, SEED, keys_from_h7=False)
    print(f"\n## size {size}, seed {SEED}: {D['n_records']} records, {len(D['sites'])} sites, {len(D['keys'])} keys with records "
          f"(gold {sum(v['class']=='gold' for v in D['keys'].values())}, decoy {sum(v['class']=='decoy' for v in D['keys'].values())})\n")
    for rf in ("event", "nd"):
        sc = defaultdict(list); cnt = Counter(); pairs = defaultdict(Counter)
        for key, v in D["keys"].items():
            if v["class"] == "gold": W = label_window(D, v)
            elif v["class"] == "decoy": W = decoy_window_weeks(D, v)
            else: W = list(range(len(D["weeks"])))
            s = echo_score(v["recs"], W, rf)
            for c in classes_of(v):
                sc[c].append(s)
                cnt[(c, "raw")] += passes(v["recs"], W, rf, None); cnt[(c, "veto")] += passes(v["recs"], W, rf, 1)
            if v["class"] == "decoy": sc["decoy"].append(s)
            # exported pairs at passing weeks
            for w in W:
                st = site_stats(window_recs(v["recs"], w - WIN + 1, w), rf)
                ok, counted, _ = tb_v4(st, w)
                if not ok: continue
                for site in counted:
                    if st[site]["records"] >= K:
                        for c in classes_of(v): pairs[c][(bidx(st[site]["records"]), bidx(st[site]["events"]))] += 1
        a4 = auc(sc["d4"], sc["gold"]); ad = auc(sc["decoy"], sc["gold"]); ao = auc(sc["other"], sc["gold"])
        print(f"root={rf}: AUC d4-vs-gold **{a4:.3f}**, all-decoy-vs-gold {ad:.3f}, other-vs-gold {ao:.3f}; "
              f"d4 s>=1: {sum(x>=1 for x in sc['d4'])}/{len(sc['d4'])}, gold s>=1: {sum(x>=1 for x in sc['gold'])}/{len(sc['gold'])}, "
              f"other s>=1: {sum(x>=1 for x in sc['other'])}/{len(sc['other'])}")
        print("  s_echo distribution: " + "; ".join(f"{c} {dict(sorted(Counter(sc[c]).items()))}" for c in CLASSES))
        print("  | variant | " + " | ".join(CLASSES) + " |"); print("  |" + "---|" * (len(CLASSES) + 1))
        for var in ("raw", "veto"):
            print(f"  | TB_v4 {var} | " + " | ".join(str(cnt[(c, var)]) for c in CLASSES) + " |")
        print("  (support, roots) pairs of counted k-sites at passing weeks: " +
              "; ".join(f"{c} {dict(sorted(pairs[c].items()))}" for c in ("gold", "d4", "other")))
        res[f"{size}:{rf}"] = {"auc_d4_gold": a4, "auc_decoy_gold": ad, "auc_other_gold": ao,
                               "dist": {c: dict(Counter(sc[c])) for c in CLASSES},
                               "passes": {f"{c}:{var}": cnt[(c, var)] for c in CLASSES for var in ("raw", "veto")},
                               "pairs": {c: {str(k): n for k, n in pairs[c].items()} for c in ("gold", "d4", "other")}}
    # per-site echo size for d4 at this scale (event roots): records per (key, site, week-window) at passing weeks
    sizes_d4 = Counter()
    for key, v in D["keys"].items():
        if "d4" not in classes_of(v): continue
        for w in decoy_window_weeks(D, v):
            st = site_stats(window_recs(v["recs"], w - WIN + 1, w), "event")
            for s, x in st.items():
                sizes_d4[(min(x["records"], 50) // 5 * 5, min(x["events"], 10))] += 1
    print("  d4 per-site (records//5*5, roots) histogram over decoy-window weeks: " + str(dict(sorted(sizes_d4.items()))))
    res[f"{size}:d4_site_hist"] = {str(k): n for k, n in sizes_d4.items()}
json.dump(res, open(f"{OUT}/results_4_scale_s{SEED}.json", "w"), indent=1, default=str)
