"""H7 one-pass world extraction (dev seeds 0-5, read-only on every repo).

For every key in raw/plateau/candidate_pool.csv, regenerate the frozen world (ew.generate, ~1s) and store the
records on the key under the ceiling_b.py model-K/B reading (anchor OR aux is the key's entity, predicate maps to
the key's chain): [week_idx, site_idx, uid, event, polarity, facet_of_gold]. uid/event are WORLD-PRIVATE and are
kept only so the downstream tests can (a) compute what a site would export (bucketed counts) and (b) run the
ORACLE root-identity check for question (A). No statistic in test_A/test_B reads uid or event directly.

Output: OUT/cache/seed-<s>.json. Run once: nice -n 10 PYTHON test_extract.py 0 1 2 3 4 5
"""
import csv, json, os, sys
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, CHAIN_PREDICATES
from mycelic.collective.edge.weeks import iso_week

OUT = os.path.dirname(os.path.abspath(__file__))
R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
DUMPS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps"
pool = list(csv.DictReader(open(f"{R}/raw/plateau/candidate_pool.csv")))


def run(seed):
    org, corpus, gold = ew.generate(200, seed)
    sites_of_user = ew._user_sites(org)
    site_ids = ew.site_ids(org); sidx = {s: i for i, s in enumerate(site_ids)}
    labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}
    recs = corpus.recs
    ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    # gold facet records per key (model F), label metadata
    facet_of_key = defaultdict(set); gold_meta = {}
    for lab in labels["patterns"]:
        p = corpus.patterns[lab["pattern"]]
        facet_of_key[lab["key"]].update(int(r) for fr in p.facet_records for r in fr)
        gold_meta[lab["key"]] = {"pattern": lab["id"], "rare": lab["rare"], "contradicted": lab["contradicted"],
                                 "window": [lab["found_from"], lab["found_to"]], "label_sites": lab["sites"]}
    decoy_classes = defaultdict(set); decoy_windows = defaultdict(list)
    for d in labels["decoys"]:
        for k in d["keys"]:
            decoy_classes[k].add(int(d["class"])); decoy_windows[k].append([d["found_from"], d["found_to"]])
    dump = json.load(open(f"{DUMPS}/seed-{seed}.json"))
    out = {"seed": seed, "sites": site_ids, "weeks": weeks, "keys": {}}
    for row in pool:
        if int(row["seed"]) != seed: continue
        key = row["key"]; _, eid, pred = key.split(":")
        assert pred in CHAIN_PREDICATES or pred == ew.REVENUE_MISS, key
        rows = bykey[(ent[eid], pred)]
        fac = facet_of_key.get(key, set())
        tuples = [[widx[iso_week(received_date(int(recs[r]["t"])))], sidx[sites_of_user[int(recs[r]["uid"])]],
                   int(recs[r]["uid"]), int(recs[r]["event"]), int(recs[r]["polarity"]), int(r in fac)] for r in rows]
        c = dump["candidates"][key]
        out["keys"][key] = {"class": row["class"], "detector_state": row["detector_state"],
                            "rule_weeks": c["rule_weeks"] or [], "candidate_weeks": c["candidate_weeks"] or [],
                            "decoy_classes": sorted(decoy_classes.get(key, ())),
                            "decoy_windows": decoy_windows.get(key, []),
                            "gold": gold_meta.get(key), "recs": tuples}
    os.makedirs(f"{OUT}/cache", exist_ok=True)
    json.dump(out, open(f"{OUT}/cache/seed-{seed}.json", "w"))
    n = len(out["keys"]); nr = sum(len(v["recs"]) for v in out["keys"].values())
    print(f"seed {seed}: {n} pool keys, {nr} key-records, sites {len(site_ids)}", flush=True)


if __name__ == "__main__":
    seeds = [int(x) for x in sys.argv[1:]] or list(range(6))
    assert all(s in range(6) for s in seeds), "dev seeds 0-5 only"
    for s in seeds: run(s)
