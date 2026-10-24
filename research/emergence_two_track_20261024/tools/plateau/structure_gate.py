"""Offline: does chain STRUCTURE (distinct stages in causal order) restore the specificity a count gate loses at scale?

Gates over a key's records in the 6-week window ending at week w (polarity >= 0; site-visible fields only: user, site,
week, extracted fine predicate -> its stage index in the pack's public chain definition; no generator label is read):
  count : the v4 count gate (tools/plateau/ceiling_b.py, TB_v4, key model K)
  local2: count AND some site's own records hold >=2 distinct stages whose first weeks are in causal order
          (site-local check: changes only that site's verdict, exports nothing new)
  glob2 / glob3: count AND >=2 / >=3 distinct stages across sites whose first weeks are in causal order
          (needs per-stage presence to cross the Boundary: a NEW exported field, founder decision; upper bound only)
Scored with labels only afterwards: pattern found = passes at some week in its label window; decoy = passes in its
window; background = a chain key carrying no planted pattern/decoy ever passes. Dev seeds 0-5 only."""
import sys, random
from collections import defaultdict, Counter
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from ceiling_b import site_stats, gates, WIN
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES
from mycelic.collective.edge.weeks import iso_week
from research.e2e_ingest.emergence_world import received_date

ew.check_world_counts = lambda *a: None
size = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(",")]; assert all(s in range(6) for s in seeds)
STAGE = {}                                    # fine predicate index -> stage position within its chain (pack order)
for c in CHAIN_PREDICATES:
    for pos, i in enumerate(i for i in range(len(PREDICATES)) if pack_predicate(PREDICATES[i]) == c):
        STAGE[i] = pos
VARIANTS = ("count", "local2", "glob2", "glob3")

def ordered(first):                           # first: {stage: first week index}; causal order = non-decreasing
    st = sorted(first)
    return all(first[a] <= first[b] for a, b in zip(st, st[1:]))

tot = {k: Counter() for k in ("pat", "rare", "dec", "bg")}; n = Counter()
for seed in seeds:
    org, corpus, gold = ew.generate(size, seed); sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}; recs = corpus.recs
    wk = [widx.get(iso_week(received_date(int(r["t"]))), -1) for r in recs]
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    def passes_at(rows, w):
        out = dict.fromkeys(VARIANTS, False)
        if not gates(site_stats(rows, recs, sites, widx, w - WIN + 1, w, False), w, weeks, False)["TB_v4"]:
            return out
        out["count"] = True
        glob, loc = {}, defaultdict(dict)
        for r in rows:
            if not w - WIN + 1 <= wk[r] <= w or int(recs[r]["polarity"]) < 0: continue
            s, site = STAGE.get(int(recs[r]["pred"])), sites[int(recs[r]["uid"])]
            if s is None: continue
            glob[s] = min(glob.get(s, 99), wk[r]); loc[site][s] = min(loc[site].get(s, 99), wk[r])
        out["glob2"] = len(glob) >= 2 and ordered(glob); out["glob3"] = len(glob) >= 3 and ordered(glob)
        out["local2"] = any(len(f) >= 2 and ordered(f) for f in loc.values())
        return out
    def ever(rows, lo, hi):
        hit = dict.fromkeys(VARIANTS, False)
        for w in range(len(weeks)):
            if w < WIN - 1 or not lo <= weeks[w] <= hi: continue
            for k, v in passes_at(rows, w).items(): hit[k] = hit[k] or v
            if all(hit.values()): break
        return hit
    ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
    planted = set()
    for p in corpus.patterns:
        planted.add((int(p.anchor), CHAIN_PREDICATES[int(p.chain)]))
        if p.facet_records and p.facet_records[0]:
            planted.add((int(recs[int(p.facet_records[0][0])]["anchor"]), CHAIN_PREDICATES[int(p.chain)]))
    for lab in labels["patterns"]:
        p = corpus.patterns[int(lab["pattern"])]
        h = ever(bykey[(int(p.anchor), CHAIN_PREDICATES[int(p.chain)])], lab["found_from"], lab["found_to"])
        n["pat"] += 1; n["rare"] += bool(lab["rare"])
        for k, v in h.items(): tot["pat"][k] += v; tot["rare"][k] += v and bool(lab["rare"])
    for d in labels["decoys"]:
        p = corpus.patterns[int(d["pattern"])]
        if not (p.facet_records and p.facet_records[0]): continue
        anc = int(recs[int(p.facet_records[0][0])]["anchor"])
        h = ever(bykey[(anc, CHAIN_PREDICATES[int(p.chain)])], d["found_from"], d["found_to"]); n["dec"] += 1
        for k, v in h.items(): tot["dec"][k] += v
        for k, v in h.items(): tot[f"dec{d['class']}"] = tot.get(f"dec{d['class']}", Counter()); tot[f"dec{d['class']}"][k] += v
        n[f"dec{d['class']}"] += 1
    cand = sorted(k for k in bykey if k[1] in CHAIN_PREDICATES and k not in planted and len(bykey[k]) >= 3)
    random.Random(seed).shuffle(cand)
    for k in cand[:300]:
        h = ever(bykey[k], weeks[0], weeks[-1]); n["bg"] += 1
        for kk, v in h.items(): tot["bg"][kk] += v
    print(f"size {size} seed {seed} done", flush=True)
for v in VARIANTS:
    dec_t = " ".join(f"{c[3:]}:{tot[c][v]}/{n[c]}" for c in sorted(k for k in tot if k.startswith("dec") and k != "dec"))
    print(f"STRUCT {size} {v:7s} patterns {tot['pat'][v]}/{n['pat']}={tot['pat'][v]/n['pat']:.3f} rare {tot['rare'][v]}/{n['rare']}"
          f"  decoys {tot['dec'][v]}/{n['dec']} ({dec_t})  background {tot['bg'][v]}/{n['bg']}={tot['bg'][v]/max(n['bg'],1):.3f}")
