"""H2 / step 1: ONE pass over dev seeds 0-5 rebuilding each world with the frozen generator (as tools/plateau/ceiling_b.py
does) to export, per pool key and per week w, the per-site buckets the gate reads over the 6-week window ending at w.
Model K (every record whose anchor OR aux is the key's entity and whose predicate maps to the key's chain) — the only model
a site can compute without knowing which records are "the pattern's". Per site the gate reads: records bucketed at k
('<k' -> 1, else the count), reporters and roots bucketed the same way, a contradiction flag, newest week. Nothing else is
read: no facet_records, no labels, no pattern membership. Output: OUT/buckets_seed-{s}.json, key -> 26 x 10 ints."""
import json, sys, os, time
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate
from mycelic.collective.edge.weeks import iso_week
ew.check_world_counts = lambda *a, **k: None
K = 3; WIN = 6
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2"
pool = json.load(open(f"{OUT}/features_base.json"))
def lb(x): return 0 if x == 0 else (1 if x < K else x)
seeds = [int(x) for x in sys.argv[1:]] or list(range(6))
assert all(s in range(6) for s in seeds)
for seed in seeds:
    t0 = time.time()
    org, corpus, gold = ew.generate(200, seed)
    sites = ew._user_sites(org)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}
    recs = corpus.recs
    ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
    wk = [widx.get(iso_week(received_date(int(r["t"]))), -1) for r in recs]
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    res = {}; n_missing_entity = 0; n_empty = 0
    for row in pool:
        if row["seed"] != seed: continue
        e = ent.get(row["entity"])
        if e is None: n_missing_entity += 1
        rows = bykey.get((e, row["pred"]), []) if e is not None else []
        if not rows: n_empty += 1
        cell = defaultdict(lambda: {"n": 0, "u": set(), "ev": set(), "c": 0, "nb": 0})
        for r in rows:
            w = wk[r]
            if w < 0: continue
            rec = recs[r]; s = sites[int(rec["uid"])]; c = cell[(s, w)]
            c["nb"] += 1
            if int(rec["polarity"]) < 0: c["c"] += 1; continue
            c["n"] += 1; c["u"].add(int(rec["uid"])); c["ev"].add(int(rec["event"]))
        per_week = []
        for w in range(len(weeks)):
            st = {}
            for (s, ww), c in cell.items():
                if w - WIN + 1 <= ww <= w:
                    a = st.setdefault(s, {"n": 0, "u": set(), "ev": set(), "c": 0, "nb": 0, "newest": -1})
                    a["n"] += c["n"]; a["u"] |= c["u"]; a["ev"] |= c["ev"]; a["c"] += c["c"]; a["nb"] += c["nb"]
                    if c["nb"]: a["newest"] = max(a["newest"], ww)
            ks = [a for a in st.values() if a["n"] >= K]
            per_week.append([
                len(ks),                                              # 0 sites at k (positive polarity)
                sum(1 for a in st.values() if 0 < a["n"] < K),        # 1 weak sites ('<k')
                sum(1 for a in st.values() if a["nb"] >= K),          # 2 sites at k, polarity-blind (model B)
                sum(lb(a["n"]) for a in st.values()),                 # 3 sum of record buckets over sites
                sum(lb(len(a["u"])) for a in ks),                     # 4 reporters lower bound over k-sites
                sum(lb(len(a["ev"])) for a in ks),                    # 5 roots lower bound over k-sites
                sum(1 for a in st.values() if a["c"] > 0),            # 6 sites holding a contradiction
                lb(max((a["n"] for a in st.values()), default=0)),    # 7 largest site bucket
                sum(1 for a in st.values() if a["nb"] > 0),           # 8 sites with any record in window
                max((a["newest"] for a in st.values()), default=-1),  # 9 newest week with a record (freshness)
            ])
        res[row["key"]] = per_week
    json.dump(res, open(f"{OUT}/buckets_seed-{seed}.json", "w"))
    print(f"seed {seed} keys {len(res)} missing_entity {n_missing_entity} empty {n_empty} {time.time()-t0:.0f}s", flush=True)
