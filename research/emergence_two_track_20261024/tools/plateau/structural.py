"""Why the structurally undiscoverable rare patterns (no TB_v4-feasible week under model B) fail: best week's binding
condition, and their whole-world evidence (all 26 weeks, every site, model B)."""
import json, os, sys
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(__file__))
from ceiling_b import site_stats, WIN, K
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES
C = {int(k): v for k, v in json.load(open(os.path.join(os.path.dirname(__file__), "ceiling_b.json"))).items()}
why = Counter(); ex = defaultdict(list); pooled = Counter()
for seed in range(6):
    org, corpus, gold = ew.generate(200, seed); sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}; recs = corpus.recs
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    for lab in labels["patterns"]:
        v = C[seed][lab["id"]]
        if not lab["rare"] or v["feasible_weeks"]["B"]["TB_v4"]: continue
        p = corpus.patterns[lab["pattern"]]; rows = bykey[(int(p.anchor), CHAIN_PREDICATES[p.chain])]
        best = None
        for w in range(len(weeks)):
            if not lab["found_from"] <= weeks[w] <= lab["found_to"]: continue
            st = site_stats(rows, recs, sites, widx, w - WIN + 1, w, True)
            mx = max((x["records"] for x in st.values()), default=0)
            held = sum(1 for x in st.values() if x["records"] > 0)
            kroot = any(x["records"] >= K and x["events"] >= K and x["users"] >= K for x in st.values())
            score = (mx, held, kroot)
            if best is None or score > best[0]: best = (score, weeks[w], {s: (x["records"], x["users"], x["events"]) for s, x in st.items()})
        (mx, held, kroot), wk, detail = best
        c = ("no site reaches k=3 records in any 6-week window" if mx < K else
             "k-site lacks 3 independent roots/reporters" if not kroot else
             "k-site with roots, but no second site holds any record (ONE_ALONE)" if held < 2 else "freshness/other")
        why[c] += 1; ex[c].append(f"s{seed}:{lab['id']} best {wk} {detail}")
        tot = sum(1 for r in rows if lab["found_from"] <= ew.iso_week(ew.received_date(int(recs[r]["t"]))) <= lab["found_to"]) if hasattr(ew, "iso_week") else None
        pooled[c] += 0
for c, n in why.most_common():
    print(n, c); [print("    ", e) for e in ex[c][:4]]
