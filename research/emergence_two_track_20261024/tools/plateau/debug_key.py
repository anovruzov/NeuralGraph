import sys
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, CHAIN_PREDICATES
from mycelic.collective.edge.weeks import iso_week
seed, pid, wk = int(sys.argv[1]), sys.argv[2], sys.argv[3]
org, corpus, gold = ew.generate(200, seed); sites = ew._user_sites(org); labels = ew.gold_labels(corpus, gold)
lab = [l for l in labels["patterns"] if l["id"] == pid][0]; p = corpus.patterns[lab["pattern"]]
weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); wi = {x: i for i, x in enumerate(weeks)}; w = wi[wk]
facet = {int(r) for fr in p.facet_records for r in fr}
chain = CHAIN_PREDICATES[p.chain]; anc = int(p.anchor)
print(lab)
st = defaultdict(lambda: defaultdict(list))
for r in range(len(corpus.recs)):
    rec = corpus.recs[r]; pr = int(rec["pred"])
    pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
    x = wi[iso_week(received_date(int(rec["t"])))]
    if not w - 5 <= x <= w: continue
    if anc in (int(rec["anchor"]), int(rec["aux"])) :
        tag = ("facet" if r in facet else "key" if pp == chain else f"other:{PREDICATES[pr] if pr>=0 else None}")
        st[sites[int(rec["uid"])]][tag].append((weeks[x], int(rec["uid"]), int(rec["event"]), int(rec["polarity"]), "A" if int(rec["anchor"])==anc else "aux", int(rec["kind"])))
for s in sorted(st):
    for tag, v in st[s].items():
        if tag.startswith("other"): print(" ", s, tag, len(v)); continue
        print(" ", s, tag, v)
