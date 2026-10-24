"""Q4: the same evidence gates applied to decoys (frozen decoy rule: facet-0 anchor + chain key, decoy window), so we
can see which decoys an evidence-perfect TB_v4 system would accept, by type; and cross with what v2/v4 accepted."""
import json, os, sys
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(__file__))
from ceiling_b import site_stats, gates, WIN
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES
S = __import__("os").environ.get("PLATEAU_SCRATCH", ".")  # dir holding em/ and plateau/
out = {}
for seed in range(6):
    org, corpus, gold = ew.generate(200, seed); sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}; recs = corpus.recs
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    for d in labels["decoys"]:
        p = corpus.patterns[int(d["pattern"])]
        first = int(p.facet_records[0][0]) if p.facet_records and p.facet_records[0] else None
        if first is None: continue
        anc = int(recs[first]["anchor"]); rows = bykey[(anc, CHAIN_PREDICATES[int(p.chain)])]
        facet = [int(r) for fr in p.facet_records for r in fr]
        res = {}
        for model, rr, blind in (("F", facet, False), ("B", rows, True)):
            ok = []
            for w in range(len(weeks)):
                if not d["found_from"] <= weeks[w] <= d["found_to"]: continue
                if gates(site_stats(rr, recs, sites, widx, w - WIN + 1, w, blind), w, weeks, False)["TB_v4"]: ok.append(weeks[w])
            res[model] = ok
        out[f"{seed}:{d['pattern']}"] = {"seed": seed, "class": d["class"], "key_anchor": ew.canonical_entity_id(corpus.entities[anc]),
                                          "chain": CHAIN_PREDICATES[int(p.chain)], "window": [d["found_from"], d["found_to"]], **res}
json.dump(out, open(f"{S}/plateau/decoy_ceiling.json", "w"))

def accepted(cfg, seed):
    f = f"{S}/em/{cfg}-s{seed}/e2e-ingest-report.json"
    r = json.load(open(f)) if os.path.exists(f) else None
    if not r or r.get("phase") != "finished": return None
    acc = set()
    sup = [c for c in r["discovery"]["fabric_conclusions"] if c["status"] == "supported" and c["lineage_ok"]]
    for k, v in out.items():
        if v["seed"] != seed: continue
        for c in sup:
            if c["entity_id"] == v["key_anchor"] and c["predicate"] == v["chain"] and v["window"][0] <= c["snapshot_week"] <= v["window"][1]:
                acc.add(k)
    return acc
for seeds, tag in ((range(6), "s0-5"), (range(5), "s0-4")):
    cnt = Counter(); feasF = Counter(); feasB = Counter(); acc = {c: Counter() for c in ("abl-v2-h1n_nd_n1", "abl-v3-h1n_nd_n1_n2", "abl-v4-rs_nd_n1_n2")}
    accB = {c: Counter() for c in acc}; ok_cfg = {}
    for c in acc:
        sets = [accepted(c, s) for s in seeds]; ok_cfg[c] = all(x is not None for x in sets)
        for s, A in zip(seeds, sets):
            for k in (A or ()):
                acc[c][out[k]["class"]] += 1; accB[c][out[k]["class"]] += bool(out[k]["B"])
    for k, v in out.items():
        if v["seed"] in seeds:
            cnt[v["class"]] += 1; feasF[v["class"]] += bool(v["F"]); feasB[v["class"]] += bool(v["B"])
    print(f"== {tag}  decoys by type {dict(sorted(cnt.items()))}")
    print(f"   TB_v4 evidence-feasible, model F {dict(sorted(feasF.items()))} total {sum(feasF.values())}")
    print(f"   TB_v4 evidence-feasible, model B {dict(sorted(feasB.items()))} total {sum(feasB.values())}")
    for c in acc:
        if ok_cfg[c]:
            print(f"   accepted {c:22s} {dict(sorted(acc[c].items()))} total {sum(acc[c].values())}  (of which B-feasible {sum(accB[c].values())})")
