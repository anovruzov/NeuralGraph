"""Q3 (model B = K counted polarity-blind, as the shipped lexical judge confirms; added after the falsification
check found supported patterns outside K). Evidence-level ceilings from the frozen generator (dev seeds 0-5), mirroring tools/ceiling.py + categories2.py
of the R0 feasibility check, extended with the gate's other conditions (roots, reporters, freshness) under R0 and under
the Track B v4 gate (N1 weak corroborator + N2 roots only from k-root sites). Perfect detection, perfect judging,
perfect routing, best single week inside the label window. Upper bounds by construction.

Evidence models:  F = the pattern's own facet records (the harness oracle, pattern_facts);
                  K = every record whose anchor OR aux is the pattern's anchor and whose predicate maps to its chain
                      (the reviewer's permissive model; background records on the key count too).
Per site, in the 6-week question window ending at w: records (positive polarity), users (reporters), events (roots),
contradicted (a polarity -1 record), newest week. as_of(w) = sunday(w) + close_lag 14 + 6 (detector's as_of_W)."""
import json, sys
from collections import defaultdict, Counter
from datetime import date, timedelta
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, CHAIN_PREDICATES
from mycelic.collective.edge.weeks import iso_week, week_sunday
K = 3; WIN = 6; FRESH = 42; MIN_ROOTS = 3; MIN_REP = 3

def lb(x):           # bucket lower bound as the gate reads it: '<k' -> 1, else the count's bucket floor (>= k)
    return 0 if x == 0 else (1 if x < K else x)

def site_stats(rows, recs, sites, weeks_idx, lo, hi, blind=False):
    st = defaultdict(lambda: {"records": 0, "users": set(), "events": set(), "contra": False, "newest": -1})
    for r in rows:
        rec = recs[r]; wi = weeks_idx[iso_week(received_date(int(rec["t"])))]
        if not lo <= wi <= hi: continue
        s = st[sites[int(rec["uid"])]]
        if int(rec["polarity"]) < 0:
            s["contra"] = True
            if not blind: continue
        s["records"] += 1; s["users"].add(int(rec["uid"])); s["events"].add(int(rec["event"])); s["newest"] = max(s["newest"], wi)
    return {k: {"records": v["records"], "users": len(v["users"]), "events": len(v["events"]), "contra": v["contra"],
                "newest": v["newest"]} for k, v in st.items()}

def gates(st, w, weeks, strict_contra):
    asof = week_sunday(weeks[w]) + timedelta(days=20) if not isinstance(week_sunday(weeks[w]), str) else \
        date.fromisoformat(week_sunday(weeks[w])) + timedelta(days=20)
    def fresh(newest):
        ws = week_sunday(weeks[newest]); ws = date.fromisoformat(ws) if isinstance(ws, str) else ws
        return (asof - ws).days <= FRESH
    use = {s: v for s, v in st.items() if not (strict_contra and v["contra"])}
    ks = [v for v in use.values() if v["records"] >= K]
    weak = [v for v in use.values() if 0 < v["records"] < K]
    out = {}
    out["R0_count"] = len(ks) >= 2
    # R0 full gate: >=2 k-sites (best 2-3 of them; M=3), roots over k-sites incl '<k' as 1, reporters likewise, fresh
    best = sorted(ks, key=lambda v: (-v["events"], -v["users"]))[:3]
    def full(sel, n2):
        if not sel: return False
        roots = sum(lb(v["events"]) for v in sel if not (n2 and v["events"] < K))
        rep = sum(lb(v["users"]) for v in sel)
        return roots >= MIN_ROOTS and rep >= MIN_REP and fresh(max(v["newest"] for v in sel))
    out["R0_full"] = len(best) >= 2 and full(best, False)
    out["N1_count"] = len(ks) >= 2 or (len(ks) >= 1 and len(weak) >= 1)
    out["TB_v4"] = (len(ks) >= 2 and full(best, True)) or (len(ks) >= 1 and len(weak) >= 1 and full(best[:2], True))
    return out

def run(seed):
    org, corpus, gold = ew.generate(200, seed)
    sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}
    recs = corpus.recs
    ent = {canonical: i for i, canonical in enumerate(ew.canonical_entity_id(e) for e in corpus.entities)}
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"])
        pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    res = {}
    for lab in labels["patterns"]:
        p = corpus.patterns[lab["pattern"]]
        facet = [int(r) for fr in p.facet_records for r in fr]
        keyrows = bykey[(int(p.anchor), CHAIN_PREDICATES[p.chain])]
        per = {}
        for model, rows, blind in (("F", facet, False), ("K", keyrows, False), ("B", keyrows, True)):
            for strict in ((False, True) if not blind else (False,)):
                hit = Counter(); weeks_ok = defaultdict(list)
                for w in range(len(weeks)):
                    if not lab["found_from"] <= weeks[w] <= lab["found_to"]: continue
                    st = site_stats(rows, recs, sites, widx, w - WIN + 1, w, blind)
                    for g, ok in gates(st, w, weeks, strict).items():
                        if ok: hit[g] += 1; weeks_ok[g].append(weeks[w])
                per[f"{model}{'s' if strict else ''}"] = {g: weeks_ok[g] for g in ("R0_count", "R0_full", "N1_count", "TB_v4")}
        res[lab["id"]] = {"rare": lab["rare"], "window": [lab["found_from"], lab["found_to"]], "feasible_weeks": per,
                          "n_sites_label": len(lab["sites"])}
    return res

if __name__ == "__main__":
    seeds = [int(x) for x in sys.argv[1:]] or list(range(6))
    assert all(s in range(6) for s in seeds)
    allres = {}
    for s in seeds:
        allres[s] = run(s); print("seed", s, "done", flush=True)
    json.dump(allres, open(__import__("os").path.join(__import__("os").environ.get("PLATEAU_SCRATCH", "."), "plateau", "ceiling_b.json"), "w"))
    for model in ("F", "Fs", "K", "Ks", "B"):
        for g in ("R0_count", "R0_full", "N1_count", "TB_v4"):
            tot = rare = n = nr = 0; per_seed = []
            for s, res in allres.items():
                a = sum(1 for v in res.values() if v["feasible_weeks"][model][g]); ar = sum(1 for v in res.values() if v["rare"] and v["feasible_weeks"][model][g])
                tot += a; rare += ar; n += len(res); nr += sum(v["rare"] for v in res.values()); per_seed.append(f"{a}/{len(res)}(r{ar})")
            print(f"{model:3s} {g:9s} all {tot}/{n}={tot/n:.3f}  rare {rare}/{nr}={rare/nr:.3f}  common {tot-rare}/{n-nr}  per-seed {' '.join(per_seed)}")
