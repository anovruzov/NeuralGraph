"""Offline: what does the PER-SITE k-floor cost? Compare the shipped per-site gate (TB_v4, ceiling_b.gates) with a
hypothetical "aggregate-then-threshold" disclosure: sites contribute counts to a secure sum, and k applies to the
cross-site total (records >= k, reporters >= 3, roots >= 3, >= 2 contributing sites, freshness as shipped). Same
6-week windows, best week inside each pattern's label window, perfect detection/judging (upper bound by construction).
Specificity on 300 seeded non-planted chain keys (>= 3 records) per seed, any week. No generator label is read by the
gates; labels only score. Dev seeds 0-5. Usage: agg_ceiling.py SIZE SEEDS
agg_spec.py variant: aggregate gate plus a specificity statistic computed on the cross-site totals only."""
import os, random, sys
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ceiling_b import site_stats, gates, WIN, K, MIN_ROOTS, MIN_REP, FRESH
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES

ew.check_world_counts = lambda *a: None
size = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(",")]; assert all(s in range(6) for s in seeds)

HIST = 6
THRESH_HIST = os.environ.get('AGG_RAW_HISTORY') != '1'   # default: history thresholded as released
def zburst(rows_w, rows_h, nh):
    lam = max(rows_h * WIN / max(nh, 1), 1.0)
    return (rows_w - lam) / (lam ** 0.5)

def agg(st):
    pos = {s: v for s, v in st.items() if v["records"] > 0}
    if len(pos) < 2: return False
    return (sum(v["records"] for v in pos.values()) >= K and sum(v["users"] for v in pos.values()) >= MIN_REP
            and sum(v["events"] for v in pos.values()) >= MIN_ROOTS)

tot = Counter(); n = Counter()
for seed in seeds:
    org, corpus, gold = ew.generate(size, seed); sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}; recs = corpus.recs
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    def ever(rows, lo, hi):
        V = ("agg", "agg_z3", "agg_z4", "agg_s3", "agg_z3_s3")
        hit = dict.fromkeys(V, False)
        for w in range(WIN - 1, len(weeks)):
            if not lo <= weeks[w] <= hi: continue
            st = site_stats(rows, recs, sites, widx, w - WIN + 1, w, False)
            if not agg(st): continue
            tw = sum(v["records"] for v in st.values())
            th = 0                                   # prior weekly TOTALS as the contract would release them:
            for hw in range(max(0, w - WIN + 1 - HIST), max(0, w - WIN + 1)):   # 0 stays 0, 1..k-1 -> k-1 ('<k'
                t = sum(v["records"] for v in site_stats(rows, recs, sites, widx, hw, hw, False).values())  # read high)
                th += t if (t == 0 or t >= K or not THRESH_HIST) else K - 1
            nh = min(HIST, max(0, w - WIN + 1)); zz = zburst(tw, th, nh) if nh else 0.0
            ns = sum(1 for v in st.values() if v["records"] > 0)
            for k, ok in (("agg", True), ("agg_z3", zz >= 3), ("agg_z4", zz >= 4), ("agg_s3", ns >= 3), ("agg_z3_s3", zz >= 3 and ns >= 3)):
                hit[k] = hit[k] or ok
            if all(hit.values()): break
        return hit
    planted = set()
    for lab in labels["patterns"]:
        p = corpus.patterns[int(lab["pattern"])]; planted.add((int(p.anchor), CHAIN_PREDICATES[int(p.chain)]))
        facet = [int(r) for fr in p.facet_records for r in fr]
        for model, rows in (("F", facet),):
            h = ever(rows, lab["found_from"], lab["found_to"])
            for g, v in h.items():
                tot[f"{model}_{g}"] += v; tot[f"{model}_{g}_rare"] += v and bool(lab["rare"])
        n["pat"] += 1; n["rare"] += bool(lab["rare"])
    cand = sorted(k for k in bykey if k[1] in CHAIN_PREDICATES and k not in planted and len(bykey[k]) >= 3)
    random.Random(seed).shuffle(cand)
    for k in cand[:300]:
        h = ever(bykey[k], weeks[0], weeks[-1]); n["bg"] += 1
        for g, v in h.items(): tot[f"bg_{g}"] += v
    print(f"size {size} seed {seed} done", flush=True)
for g in ("agg", "agg_z3", "agg_z4", "agg_s3", "agg_z3_s3"):
    print(f"SPEC{'' if THRESH_HIST else '-rawhist'} {size} {g:10s} F patterns {tot['F_'+g]}/{n['pat']}={tot['F_'+g]/n['pat']:.3f} rare {tot['F_'+g+'_rare']}/{n['rare']}"
          f"  non-planted keys passing {tot['bg_'+g]}/{n['bg']}={tot['bg_'+g]/max(n['bg'],1):.3f}")
