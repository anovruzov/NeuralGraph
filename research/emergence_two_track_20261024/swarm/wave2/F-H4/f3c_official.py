"""KILL 3 addendum -- the OFFICIAL run (official-ce0e357, seeds 0-5): its asked set and real verdicts. The official run
asked every snapshot candidate it had (341 of 347; 55/53/60/53/60/60 <= TOP_N 60), so there is NO budget slack to
reorder -- a reorder of the official asked set cannot change its score and is not a test. What the official verdicts
CAN check: does S_Net3 at the asked week separate the official run's found gold from its accepted decoys?"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f_common import *
from collections import Counter
RUNS = os.path.join(SCRATCH, "runs", "official-ce0e357")
D = load_cells(); rows = build_rows(D); Fits = {s: fit_tables(rows, loso(s)) for s in SEEDS}
pos = []; neg = []; per = {}
print("seed  asked  report.found  recomputed_found  accepted_decoys(recomputed)  AUC S_Net3@asked-week gold-vs-decoy (key class)  n_gold n_decoy")
for s in SEEDS:
    rep = json.load(open(os.path.join(RUNS, f"seed-{s}", "e2e-ingest-report.json"))); disc = rep["discovery"]
    S = D[s]["keys"]; widx = {wk: i for i, wk in enumerate(D[s]["weeks"])}
    found = set(); dec = 0; ps = []; ns = []; n_asked = 0
    for c in disc["fabric_conclusions"]:
        key = f"entity:{c['entity_id']}:{c['predicate']}"; v = S.get(key)
        if v is None: continue
        n_asked += 1; wk = c["snapshot_week"]; f = b_features(v["cells"], widx[wk]); sc = Fits[s]["net3"](cls_of(f))
        wins = [w for w in D[s]["windows"].get(key, []) if w[2] == v["class"] and (v["class"] != "gold" or w[3] == v["pattern"])]
        inw = any(w[0] <= wk <= w[1] for w in wins)
        sup = c["status"] == "supported" and c["lineage_ok"]
        if v["class"] == "gold":
            ps.append(sc)
            if sup and inw: found.add(v["pattern"])
        elif v["class"] == "decoy":
            ns.append(sc)
            if sup and inw: dec += 1
    a = auc(ps, ns); per[s] = round(a, 3); pos += ps; neg += ns
    print(f"{s:4s}  {n_asked:5d}  {disc['found']:12d}  {len(found):16d}  {dec:27d}  {a:46.3f}  {len(ps):6d} {len(ns):7d}")
print(f"pooled AUC S_Net3@asked-week on the official asked set: {auc(pos, neg):.3f}; per seed {per}  (H4's claim for this pool: 0.842 S_Net / 0.814 S_Net3, with look-ahead)")
print("note: recomputed found uses the key-class/window convention of h4_cells; the report's own 'found' is the official number.")
