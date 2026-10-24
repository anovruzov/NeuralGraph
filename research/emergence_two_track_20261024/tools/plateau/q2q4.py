"""Q2 per-seed v4 vs v2 table and Q4 decoy acceptance by type, from recorded reports only."""
import json, os, statistics as st
S = __import__("os").environ.get("PLATEAU_SCRATCH", ".")  # dir holding em/ and plateau/
def load(cfg, s):
    f = f"{S}/em/{cfg}-s{s}/e2e-ingest-report.json"
    if not os.path.exists(f): return None
    r = json.load(open(f)); return r if r.get("phase") == "finished" else None
for cfg in ("abl-v2-h1n_nd_n1", "abl-v3-h1n_nd_n1_n2", "abl-v4-rs_nd_n1_n2"):
    print("==", cfg)
    tot = {"found": 0, "n": 0, "rf": 0, "rn": 0, "cf": 0, "cn": 0, "dec": {}, "decn": {}, "q": 0, "bg": 0}
    recs = []
    for s in range(6):
        r = load(cfg, s)
        if r is None: print("  s%d not finished" % s); continue
        D = r["discovery"]; rare = set(D["gold_rare_patterns"]); fd = set(D["found_patterns"])
        rf = len(fd & rare); cf = len(fd - rare)
        dec = D["decoy_accepted_by_type"]; dsum = sum(dec.values())
        recs.append(D["recall_all"])
        print(f"  s{s} commit {r['run']['code_commit'][:8]} found {D['found']}/{D['gold_discoverable']}={D['recall_all']:.3f} rare {rf}/{D['gold_rare']} common {cf}/{D['gold_common']} "
              f"decoys {dsum} {dict(sorted(dec.items()))} questions {r['routing']['questions_asked']} cand true/decoy/bg {D['candidates_true']}/{D['candidates_decoy']}/{D['candidates_background']} bg_supported {D['background_supported']} statuses {r['routing']['statuses']}")
        tot["found"] += D["found"]; tot["n"] += D["gold_discoverable"]; tot["rf"] += rf; tot["rn"] += D["gold_rare"]; tot["cf"] += cf; tot["cn"] += D["gold_common"]
        tot["q"] += r["routing"]["questions_asked"]; tot["bg"] += D["background_supported"]
        for t, v in D["decoys_by_type"].items(): tot["decn"][t] = tot["decn"].get(t, 0) + v
        for t in D["decoys_by_type"]: tot["dec"][t] = tot["dec"].get(t, 0) + dec.get(t, 0)
    sd = st.stdev(recs) if len(recs) > 1 else 0
    print(f"  pooled {tot['found']}/{tot['n']}={tot['found']/tot['n']:.3f} rare {tot['rf']}/{tot['rn']}={tot['rf']/tot['rn']:.3f} common {tot['cf']}/{tot['cn']}={tot['cf']/tot['cn']:.3f} "
          f"per-seed min {min(recs):.3f} max {max(recs):.3f} sd {sd:.3f}  decoys {sum(tot['dec'].values())}/{sum(tot['decn'].values())} by type {tot['dec']} questions {tot['q']} bg_supported {tot['bg']}")
