"""Specificity of the v4 count gate by world size: decoy pass rate (decoy facet-0 key, decoy window) by type, and the
pass rate of keys that carry no planted pattern or decoy (ever passes in any week). Same gate model as ceiling_b.py.
Dev seeds only; offline; labels used only to score."""
import sys, random
from collections import defaultdict, Counter
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from ceiling_b import site_stats, gates, WIN
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES
ew.check_world_counts = lambda *a: None
size = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(",")]; assert all(s in range(6) for s in seeds)
dec = Counter(); decK = Counter(); bg_n = bg_K = bg_B = 0
for seed in seeds:
    org, corpus, gold = ew.generate(size, seed); sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}; recs = corpus.recs
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    planted = set()
    for p in corpus.patterns:
        planted.add((int(p.anchor), CHAIN_PREDICATES[int(p.chain)]))
        if p.facet_records and p.facet_records[0]:
            planted.add((int(recs[int(p.facet_records[0][0])]["anchor"]), CHAIN_PREDICATES[int(p.chain)]))
    def passes(rows, lo_hi, blind):
        lo, hi = lo_hi
        for w in range(len(weeks)):
            if not lo <= weeks[w] <= hi or w < WIN - 1: continue
            if gates(site_stats(rows, recs, sites, widx, w - WIN + 1, w, blind), w, weeks, False)["TB_v4"]: return True
        return False
    for d in labels["decoys"]:
        p = corpus.patterns[int(d["pattern"])]
        if not (p.facet_records and p.facet_records[0]): continue
        anc = int(recs[int(p.facet_records[0][0])]["anchor"]); rows = bykey[(anc, CHAIN_PREDICATES[int(p.chain)])]
        dec[d["class"]] += 1; decK[d["class"]] += passes(rows, (d["found_from"], d["found_to"]), False)
    chain = set(CHAIN_PREDICATES.values()) if isinstance(CHAIN_PREDICATES, dict) else set(CHAIN_PREDICATES)
    cand = sorted(k for k in bykey if k[1] in chain and k not in planted and len(bykey[k]) >= 3)
    random.Random(seed).shuffle(cand)
    for k in cand[:300]:
        bg_n += 1; bg_K += passes(bykey[k], (weeks[0], weeks[-1]), False); bg_B += passes(bykey[k], (weeks[0], weeks[-1]), True)
    print(f"size {size} seed {seed} done ({len(cand)} non-planted chain keys with >=3 records)", flush=True)
print(f"SPEC {size} decoys passing (K, in window) by type: " + ", ".join(f"{t}:{decK[t]}/{dec[t]}" for t in sorted(dec)) +
      f" total {sum(decK.values())}/{sum(dec.values())}")
print(f"SPEC {size} non-planted chain keys ever passing: K {bg_K}/{bg_n}={bg_K/max(bg_n,1):.3f}  B {bg_B}/{bg_n}={bg_B/max(bg_n,1):.3f}")
