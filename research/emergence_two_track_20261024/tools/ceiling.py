"""Independent R0 ceiling: perfect detection, every week, own facet records only.
found(p) iff exists week w in p's scoring window with >= SITES sites each holding >= K of p's facet records received
in the window of WIN weeks ending at w. Also reports variants (longer windows, any week) to locate the binding limit."""
import sys, json
from collections import defaultdict
from datetime import date
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date
from mycelic.collective.edge.weeks import iso_week  # noqa

def weeks_index(weeks):
    return {w: i for i, w in enumerate(weeks)}

def run(seed, K=3, SITES=2, WIN=6):
    org, corpus, gold = ew.generate(200, seed)
    sites = ew._user_sites(org)
    labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS))
    wi = weeks_index(weeks)
    out = {}
    for lab in labels["patterns"]:
        p = corpus.patterns[lab["pattern"]]
        per_site = defaultdict(list)
        for fr in p.facet_records:
            for r in fr:
                rec = corpus.recs[int(r)]
                wk = iso_week(received_date(int(rec["t"])))
                if wk in wi:
                    per_site[sites[int(rec["uid"])]].append(wi[wk])
        ok = {}
        for win in (WIN, 12, 26):
            hit = False
            for w in range(len(weeks)):
                if not (lab["found_from"] <= weeks[w] <= lab["found_to"]):
                    continue
                n = sum(1 for s, ws in per_site.items() if sum(1 for x in ws if w - win < x <= w) >= K)
                if n >= SITES:
                    hit = True; break
            ok[win] = hit
        maxsite = sorted((len(v) for v in per_site.values()), reverse=True)
        out[lab["id"]] = {"rare": lab["rare"], "ok": ok, "site_counts": maxsite[:3]}
    return out

tot = defaultdict(int); n = 0; rare_tot = defaultdict(int); nr = 0
classes = defaultdict(int)
for seed in map(int, sys.argv[1:] or range(6)):
    res = run(seed)
    found = {w: sum(1 for v in res.values() if v["ok"][w]) for w in (6, 12, 26)}
    print(f"seed {seed}: discoverable {len(res)}  R0@6w {found[6]}  @12w {found[12]}  @26w {found[26]}", flush=True)
    for v in res.values():
        n += 1; nr += v["rare"]
        for w in (6, 12, 26):
            tot[w] += v["ok"][w]; rare_tot[w] += v["ok"][w] and v["rare"]
        if not v["ok"][6]:
            sc = v["site_counts"]
            c = "no site >=3" if not sc or sc[0] < 3 else ("one site >=3" if len(sc) < 2 or sc[1] < 3 else "two sites, never same window")
            classes[(v["rare"], c)] += 1
print(json.dumps({"patterns": n, "rare": nr, "ceiling_R0": {w: round(tot[w] / n, 4) for w in tot},
                  "rare_found": dict(rare_tot), "miss_classes": {f"{'rare' if k[0] else 'common'}: {k[1]}": v for k, v in classes.items()}}, indent=1))


def every_claim(seed, K=3, SITES=2, WIN=6):
    """Upper bound counting EVERY record on the key (anchor, chain predicate), not only the pattern's own; negation
    ignored (it can only remove support), so this over-counts what any verifier could confirm."""
    from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES
    org, corpus, gold = ew.generate(200, seed)
    sites = ew._user_sites(org)
    labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); wi = weeks_index(weeks)
    bykey = defaultdict(lambda: defaultdict(list))
    for r in range(len(corpus.recs)):
        rec = corpus.recs[r]
        pp = pack_predicate(PREDICATES[int(rec["pred"])]) if int(rec["pred"]) >= 0 else None
        if pp is None or int(rec["anchor"]) < 0:
            continue
        wk = iso_week(received_date(int(rec["t"])))
        if wk in wi:
            bykey[(int(rec["anchor"]), pp)][sites[int(rec["uid"])]].append(wi[wk])
    hits = 0
    for lab in labels["patterns"]:
        p = corpus.patterns[lab["pattern"]]
        per_site = bykey[(int(p.anchor), CHAIN_PREDICATES[p.chain])]
        for w in range(len(weeks)):
            if lab["found_from"] <= weeks[w] <= lab["found_to"] and sum(
                    1 for ws in per_site.values() if sum(1 for x in ws if w - WIN < x <= w) >= K) >= SITES:
                hits += 1; break
    return hits, len(labels["patterns"])
