"""Classify every Gold-discoverable pattern by the strongest protocol that could legally confirm it.
Evidence model (most permissive the reviewer found legal for the shipped verifier): records on (anchor OR aux,
chain predicate); windows of 6 weeks (shipped) and up to 26 weeks; window end >= found_from (late as_of allowed).
Classes, in order:
  R0     >= 2 sites each >= k in one window                           (feasible under today's gate)
  ONE    exactly one site >= k, no second                             (blocked only by the two-site rule)
  POOL2  no site >= k, but >= 2 sites hold records and their pooled count >= 2k in one window
         (fragmented: only a cross-site pooled count could see it)
  POOL1  pooled count >= k in one window but < 2k, or only one site holds any (weak / single-source)
  NONE   pooled count < k in every window (the evidence does not exist at any threshold)
"""
import json, sys
from collections import defaultdict, Counter
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, CHAIN_PREDICATES
from mycelic.collective.edge.weeks import iso_week

K = 3

def classify(seed, WIN):
    org, corpus, gold = ew.generate(200, seed)
    sites = ew._user_sites(org)
    labels = ew.gold_labels(corpus, gold)
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
    out = {}
    for lab in labels["patterns"]:
        p = corpus.patterns[lab["pattern"]]
        per_site = bykey[(int(p.anchor), CHAIN_PREDICATES[p.chain])]
        best = "NONE"
        rank = {"NONE": 0, "POOL1": 1, "POOL2": 2, "ONE": 3, "R0": 4}
        for w in range(len(weeks)):
            if weeks[w] < lab["found_from"]:
                continue
            counts = [sum(1 for x in ws if w - WIN < x <= w) for ws in per_site.values()]
            big = sum(1 for c in counts if c >= K); held = sum(1 for c in counts if c > 0); pooled = sum(counts)
            c = ("R0" if big >= 2 else "ONE" if big == 1 else "POOL2" if held >= 2 and pooled >= 2 * K
                 else "POOL1" if pooled >= K else "NONE")
            if rank[c] > rank[best]:
                best = c
        out[lab["id"]] = {"class": best, "rare": lab["rare"]}
    return out

if __name__ == "__main__":
    found = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    for WIN in (6, 26):
        tot = Counter(); rare = Counter(); rows = {}
        for seed in range(6):
            res = classify(seed, WIN); rows[seed] = res
            for pid, v in res.items():
                tot[v["class"]] += 1; rare[v["class"]] += v["rare"]
        n = sum(tot.values())
        print(f"window {WIN}w: " + "  ".join(f"{c} {tot[c]} ({tot[c]/n:.3f}; rare {rare[c]})" for c in ("R0", "ONE", "POOL2", "POOL1", "NONE")))
        cum = 0
        for c in ("R0", "ONE", "POOL2", "POOL1"):
            cum += tot[c]; print(f"   ceiling if protocol admits up to {c}: {cum}/{n} = {cum/n:.4f}")
        json.dump({str(s): r for s, r in rows.items()}, open(f"categories_{WIN}w.json", "w"))
