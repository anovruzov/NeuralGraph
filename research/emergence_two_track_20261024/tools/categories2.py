"""Same evidence model as categories.py, with ONE split by corroboration, applied to true patterns AND decoys.
  R0         >= 2 sites with >= k
  ONE_CORR   one site >= k and >= 1 other site holds >= 1 record in the same window
  ONE_ALONE  one site >= k, no other site holds any record in any such window
  POOL2      no site >= k, >= 2 sites, pooled >= 2k
  WEAK       the rest (pooled >= k at best)
A decoy uses its facet-0 key (the frozen decoy rule) and its own window."""
import json, sys
from collections import defaultdict, Counter
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, CHAIN_PREDICATES
from mycelic.collective.edge.weeks import iso_week
K = 3; ORDER = ("WEAK", "POOL2", "ONE_ALONE", "ONE_CORR", "R0")

def best_class(per_site, weeks, found_from, WIN):
    best = 0
    for w in range(len(weeks)):
        if weeks[w] < found_from:
            continue
        counts = [sum(1 for x in ws if w - WIN < x <= w) for ws in per_site.values()]
        big = sum(1 for c in counts if c >= K); held = sum(1 for c in counts if c > 0); pooled = sum(counts)
        c = ("R0" if big >= 2 else ("ONE_CORR" if held >= 2 else "ONE_ALONE") if big == 1
             else "POOL2" if held >= 2 and pooled >= 2 * K else "WEAK")
        best = max(best, ORDER.index(c))
    return ORDER[best]

def run(seed, WIN):
    org, corpus, gold = ew.generate(200, seed)
    sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); wi = {w: i for i, w in enumerate(weeks)}
    bykey = defaultdict(lambda: defaultdict(list))
    for r in range(len(corpus.recs)):
        rec = corpus.recs[r]
        pp = pack_predicate(PREDICATES[int(rec["pred"])]) if int(rec["pred"]) >= 0 else None
        if pp is None:
            continue
        wk = wi[iso_week(received_date(int(rec["t"])))]; s = sites[int(rec["uid"])]
        for e in {int(rec["anchor"]), int(rec["aux"])}:
            if e >= 0:
                bykey[(e, pp)][s].append(wk)
    pats = {lab["id"]: best_class(bykey[(int(corpus.patterns[lab["pattern"]].anchor),
                                         CHAIN_PREDICATES[corpus.patterns[lab["pattern"]].chain])],
                                  weeks, lab["found_from"], WIN) for lab in labels["patterns"]}
    decs = {}
    for d in labels["decoys"]:
        p = corpus.patterns[int(d["pattern"])]
        first = int(p.facet_records[0][0]) if p.facet_records and p.facet_records[0] else None
        if first is None:
            continue
        key = (int(corpus.recs[first]["anchor"]), CHAIN_PREDICATES[int(p.chain)])
        decs[(int(d["pattern"]), str(d["class"]))] = best_class(bykey[key], weeks, d["found_from"], WIN)
    return pats, decs

for WIN in (6, 26):
    P = Counter(); D = Counter(); DC = defaultdict(Counter); npat = ndec = 0
    for seed in range(6):
        pats, decs = run(seed, WIN)
        npat += len(pats); ndec += len(decs)
        for c in pats.values(): P[c] += 1
        for (pid, cls), c in decs.items(): D[c] += 1; DC[c][cls] += 1
    print(f"== window {WIN}w  patterns {npat}  decoys {ndec}")
    cp = cd = 0
    for c in reversed(ORDER):
        cp += P[c]; cd += D[c]
        print(f"  {c:9s} patterns {P[c]:3d}  decoys {D[c]:3d} {dict(sorted(DC[c].items()))}   "
              f"admit-through: recall {cp/npat:.3f}, decoy acceptance {cd/ndec:.3f}")
