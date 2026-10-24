"""Offline question-timing simulation (no e2e run, no model calls). Step 1 calibrates an evidence predictor against the
recorded v4 runs: predicted 'supported' at the asked week := model-B TB_v4 gate passes for the (anchor, chain) key in the
6-week question window ending at that week. Step 2 replays label-free asking policies over the detector's own weeks
(det-v4-s<seed>.json) and scores them with the frozen label rules (found = a predicted-supported note whose week is in the
pattern window; decoy accepted = predicted-supported note on its facet-0 key inside its window). Estimates, not results."""
import json, os, sys
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(__file__))
from ceiling_b import site_stats, gates, WIN
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES
S = __import__("os").environ.get("PLATEAU_SCRATCH", ".")  # dir holding em/ and plateau/
seeds = [int(x) for x in sys.argv[1].split(",")]
DEC = json.load(open(f"{S}/plateau/decoy_ceiling.json"))
calib = Counter(); pol = defaultdict(Counter)
for seed in seeds:
    det = json.load(open(f"{S}/plateau/det-v4-s{seed}.json"))
    rep = json.load(open(f"{S}/em/abl-v4-rs_nd_n1_n2-s{seed}/e2e-ingest-report.json"))
    org, corpus, gold = ew.generate(200, seed); sites = ew._user_sites(org)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}; recs = corpus.recs
    ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    cache = {}
    def ok(key, wk):
        if (key, wk) not in cache:
            _, eid, pred = key.split(":"); w = widx[wk]
            cache[(key, wk)] = gates(site_stats(bykey[(ent[eid], pred)], recs, sites, widx, w - WIN + 1, w, True), w, weeks, False)["TB_v4"]
        return cache[(key, wk)]
    labs = det["labels"]["patterns"]; pkey = {p["key"]: p for p in labs}
    dkeys = defaultdict(list)
    for k, v in DEC.items():
        if v["seed"] == seed: dkeys[f"entity:{v['key_anchor']}:{v['chain']}"].append(v)
    # step 1: calibration on the recorded questions
    for c in rep["discovery"]["fabric_conclusions"]:
        key = f"entity:{c['entity_id']}:{c['predicate']}"
        kind = "gold" if key in pkey else ("decoy" if key in dkeys else "background")
        calib[(kind, ok(key, c["snapshot_week"]), c["status"] == "supported")] += 1
    # step 2: policies over each candidate key's detector weeks
    def score(name, asks):
        found = set(); dec = set(); bg = 0; q = 0
        for key, wks in asks.items():
            for wk in wks:
                q += 1
                if not ok(key, wk): continue
                p = pkey.get(key)
                if p and p["found_from"] <= wk <= p["found_to"]: found.add(p["id"])
                hit = False
                for d in dkeys.get(key, []):
                    if d["window"][0] <= wk <= d["window"][1]: dec.add((d["class"], key, tuple(d["window"]))); hit = True
                if not p and not dkeys.get(key): bg += 1
        rare = {p["id"] for p in labs if p["rare"]}
        pol[name]["found"] += len(found); pol[name]["rare"] += len(found & rare); pol[name]["q"] += q; pol[name]["bg_sup"] += bg
        for cls, *_ in dec: pol[name][f"d{cls}"] += 1
        pol[name]["dec"] += len(dec)
    first, everyrule, persist, upto3 = {}, {}, {}, {}
    for key, c in det["candidates"].items():
        if c["snapshot_week"] is None: continue
        rw = [w for w in (c["rule_weeks"] or []) if widx.get(w, -1) >= WIN - 1]
        cw = [w for w in (c["candidate_weeks"] or []) if w in widx]
        first[key] = [c["snapshot_week"]]
        allw = sorted(set(rw) | set(cw) | {c["snapshot_week"]})
        later = [w for w in allw if w >= c["snapshot_week"]]
        persist[key] = later
        upto3[key] = later[:3]
        # re-ask every 2 weeks after the snapshot while the key stays a candidate/rule hit
        everyrule[key] = later[::2]
    score("P0 v4 as run (first snapshot week)", first)
    score("P1 re-ask every later detector/rule week", persist)
    score("P2 re-ask every 2nd later week", everyrule)
    score("P3 snapshot + next 2 weeks", upto3)
    pol["_n"]["gold"] += len(labs); pol["_n"]["rare"] += sum(p["rare"] for p in labs)
print("calibration (kind, predicted_supported, system_supported):")
for k, v in sorted(calib.items()): print("  ", k, v)
n = pol.pop("_n")
for name, c in pol.items():
    print(f"{name:44s} found {c['found']}/{n['gold']}={c['found']/n['gold']:.3f} rare {c['rare']}/{n['rare']}={c['rare']/n['rare']:.3f} "
          f"decoys {c['dec']} (1:{c['d1']} 2:{c['d2']} 3:{c['d3']} 4:{c['d4']} 5:{c['d5']}) background-supported {c['bg_sup']} questions {c['q']}")
