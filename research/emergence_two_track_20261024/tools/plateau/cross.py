"""Cross the Q1 funnel with the Q3 evidence ceilings: (1) falsification check - every found pattern of every recorded
run must lie inside the matching ceiling set; (2) sub-classify each v2/v4 rare miss by whether the evidence ever allowed
it (TB_v4 gate, models F and K), and whether the week it was actually asked was a feasible week."""
import json, collections, glob, os
S = __import__("os").environ.get("PLATEAU_SCRATCH", ".")  # dir holding em/ and plateau/
C = {int(k): v for k, v in json.load(open(f"{S}/plateau/ceiling_b.json")).items()}
# (1) falsification
for cfgdir, gate in (("clean", "R0_count"), ("abl-h1n", "R0_count"), ("abl-v2-h1n_nd_n1", "N1_count"),
                     ("abl-v3-h1n_nd_n1_n2", "TB_v4"), ("abl-v4-rs_nd_n1_n2", "TB_v4")):
    bad = {m: [] for m in ("F", "K", "B")}; n = 0
    for s in range(6):
        f = f"{S}/em/{cfgdir}-s{s}/e2e-ingest-report.json"
        if not os.path.exists(f): continue
        r = json.load(open(f))
        if r.get("phase") != "finished": continue
        for pid in r["discovery"]["found_patterns"]:
            n += 1
            for m in bad:
                if not C[s][pid]["feasible_weeks"][m][gate]: bad[m].append(f"s{s}:{pid}")
    print(f"falsification {cfgdir:22s} gate {gate:8s} found {n:3d}  outside F: {len(bad['F'])} {bad['F'][:8]}  outside K: {len(bad['K'])} {bad['K']}  outside B: {len(bad['B'])} {bad['B']}")
# (2) sub-classify misses
rows = json.load(open(f"{S}/plateau/funnel_rows.json"))
for cfg in ("v2", "v4"):
    agg = collections.Counter(); ex = collections.defaultdict(list)
    for r in rows:
        if r["cfg"] != cfg or not r["rare"]: continue
        fw = C[r["seed"]][r["pattern"]]["feasible_weeks"]
        Fw, Kw = fw["F"]["TB_v4"], fw["B"]["TB_v4"]
        ev = "evF" if Fw else ("evB_only" if Kw else "ev_none")
        asked = r["asked_weeks_status"][0][0] if r["asked_weeks_status"] else None
        if asked is None: wk = "-"
        else: wk = "asked@feasibleF" if asked in Fw else ("asked@feasibleB" if asked in Kw else "asked@infeasible")
        key = (r["stage"], ev, wk)
        agg[key] += 1; ex[key].append(f"s{r['seed']}:{r['pattern']}")
    print(f"== {cfg} rare, by (stage, evidence class under TB_v4 gate, asked-week feasibility)")
    for k, v in sorted(agg.items()):
        print(f"  {v:3d}  {k}  e.g. {ex[k][:6]}")
