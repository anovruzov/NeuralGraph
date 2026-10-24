"""Generator-side world for F-H4 (read-only on ng-deploy). Rebuilds the k-suppressed weekly cells for ANY key at ANY
world size exactly as H4's test_h4_extract.py did (model B: every record on the key, polarity-blind), plus the
ceiling_b gate model's TB_v4 answer at a week. Labels (gold key / decoy keys / windows) are carried for SCORING only."""
import sys, os
from collections import defaultdict
sys.path.insert(0, "/home/user/ng-deploy")
sys.path.insert(0, "/home/user/ng-recovery/research/emergence_two_track_20261024/tools/plateau")
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate
from mycelic.collective.edge.weeks import iso_week
from ceiling_b import site_stats, gates, K, WIN
ew.check_world_counts = lambda *a, **k: None

def supp(n): return 0 if n == 0 else (-1 if n < K else n)

class World:
    def __init__(self, size, seed):
        self.size, self.seed = size, seed
        org, corpus, gold = ew.generate(size, seed)
        self.corpus = corpus; self.sites = ew._user_sites(org); self.n_sites = len(set(self.sites.values()))
        self.weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); self.widx = {x: i for i, x in enumerate(self.weeks)}
        self.recs = corpus.recs
        self.ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
        self.ent_name = {i: c for c, i in self.ent.items()}
        self.bykey = defaultdict(list)
        for r in range(len(self.recs)):
            pr = int(self.recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
            if pp is None: continue
            for e in {int(self.recs[r]["anchor"]), int(self.recs[r]["aux"])}:
                if e >= 0: self.bykey[(e, pp)].append(r)
        self.labels = ew.gold_labels(corpus, gold)
        self.gold_key = {}; self.decoy_key = {}
        for p in self.labels["patterns"]: self.gold_key.setdefault(p["key"], []).append(p)
        for d in self.labels["decoys"]:
            for k in d["keys"]: self.decoy_key.setdefault(k, []).append(d)
    def all_keys(self):
        return [f"entity:{self.ent_name[e]}:{pp}" for (e, pp) in self.bykey]
    def rows_for(self, key):
        _, eid, pred = key.split(":"); return self.bykey.get((self.ent[eid], pred), [])
    def cells_for(self, key):
        rows = self.rows_for(key); W = len(self.weeks)
        cell_n = defaultdict(lambda: [0] * W); cell_u = defaultdict(lambda: [set() for _ in range(W)]); cell_e = defaultdict(lambda: [set() for _ in range(W)])
        for r in rows:
            rec = self.recs[r]; wi = self.widx[iso_week(received_date(int(rec["t"])))]; s = self.sites[int(rec["uid"])]
            cell_n[s][wi] += 1; cell_u[s][wi].add(int(rec["uid"])); cell_e[s][wi].add(int(rec["event"]))
        return {s: {"n": [supp(x) for x in cell_n[s]], "roots": [supp(len(x)) for x in cell_e[s]],
                    "rep": [supp(len(x)) for x in cell_u[s]]} for s in sorted(cell_n)}
    def tb_at(self, key, w):
        rows = self.rows_for(key)
        st = site_stats(rows, self.recs, self.sites, self.widx, w - WIN + 1, w, blind=True)
        return bool(gates(st, w, self.weeks, False)["TB_v4"]) if st else False
    def label(self, key):
        if key in self.gold_key: return "gold"
        if key in self.decoy_key: return "decoy"
        return "other"
    def windows(self, key):
        if key in self.gold_key: return [(p["found_from"], p["found_to"]) for p in self.gold_key[key]]
        if key in self.decoy_key: return [(d["found_from"], d["found_to"]) for d in self.decoy_key[key]]
        return []
