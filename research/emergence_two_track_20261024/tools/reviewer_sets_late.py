import sys
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, CHAIN_PREDICATES
from mycelic.collective.edge.weeks import iso_week

def feasible(seed, mode, WIN, K=3, SITES=2):
    org, corpus, gold = ew.generate(200, seed)
    sites = ew._user_sites(org)
    labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); wi = {w:i for i,w in enumerate(weeks)}
    bykey = defaultdict(lambda: defaultdict(list))
    for r in range(len(corpus.recs)):
        rec = corpus.recs[r]
        pp = pack_predicate(PREDICATES[int(rec["pred"])]) if int(rec["pred"]) >= 0 else None
        if pp is None: continue
        wk = iso_week(received_date(int(rec["t"]))); s = sites[int(rec["uid"])]
        ents = {int(rec["anchor"])}
        if mode == "aux" and int(rec["aux"]) >= 0: ents.add(int(rec["aux"]))
        for e in ents:
            if e >= 0: bykey[(e, pp)][s].append(wi[wk])
    out = set()
    for lab in labels["patterns"]:
        p = corpus.patterns[lab["pattern"]]
        if mode == "own":
            per_site = defaultdict(list)
            for fr in p.facet_records:
                for r in fr:
                    rec = corpus.recs[int(r)]
                    per_site[sites[int(rec["uid"])]].append(wi[iso_week(received_date(int(rec["t"])))])
        else:
            per_site = bykey[(int(p.anchor), CHAIN_PREDICATES[p.chain])]
        for w in range(len(weeks)):
            if lab["found_from"] <= weeks[w] and sum(
                    1 for ws in per_site.values() if sum(1 for x in ws if w - WIN < x <= w) >= K) >= SITES:
                out.add(lab["id"]); break
    return out, len(labels["patterns"])

for seed in map(int, sys.argv[1:]):
    for mode in ("own", "every", "aux"):
        for win in (26,):
            s, n = feasible(seed, mode, win)
            print(seed, mode, win, len(s), n, sorted(s), flush=True)
