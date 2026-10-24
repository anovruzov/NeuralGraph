"""F-H5 shared code. Read-only on every repository; writes only under OUT.

Provenance: load_pool / auc / runs / precision_at are copied verbatim in logic from
  swarm_out/H5/h5_common.py  (H5's loader; labels are read ONLY to score)
longest_window / stats are copied from swarm_out/H5/test_h2_crosssite_sync.py (H5-2's exact formula).
build_cells is swarm_out/H5/test_rebuild_cells.py generalised to a world size (model K of tools/plateau/ceiling_b.py).
rule_weeks_from_cells is swarm_out/H5/test_h0_cells_validity.py's rule (>= 2 sites with lb >= 1 over [i-7, i]),
which is pack/emergence/rules.json (window_weeks 8, min_count_per_site 1, min_sites 2).
"""
import json, csv, random, bisect, statistics, hashlib
from collections import defaultdict

SCRATCH = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad"
DUMPS = SCRATCH + "/dumps/seed-%d.json"
R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
POOL = R + "/raw/plateau/candidate_pool.csv"
CEIL = R + "/raw/plateau/ceiling_b.json"
H5 = SCRATCH + "/swarm_out/H5"
OUT = SCRATCH + "/swarm_out/F-H5"
SEEDS = range(6)
K = 3; RULE_WIN = 8; GATE_WIN = 6; THETA = 1.25; TIE_SALT = "e2e-ingest-v1"


def widx(w):  # '2024-W05' -> 4
    return int(w[-2:]) - 1


def load_pool():
    """H5's filtered rule-only pool at 200 users (channels == ['rule'], rule_weeks >= 7) + scoring labels."""
    cls = {}; meta = {}
    for r in csv.DictReader(open(POOL)):
        cls[(int(r["seed"]), r["key"])] = r["class"]
        meta[(int(r["seed"]), r["key"])] = {"rare": r["rare"] == "1", "feasible": r["F_TB_v4_feasible"] == "1"}
    rows = []
    for s in SEEDS:
        d = json.load(open(DUMPS % s))
        dcls = defaultdict(set)
        for x in d["labels"]["decoys"]:
            for k in x["keys"]: dcls[k].add(int(x["class"]))
        for k, v in d["candidates"].items():
            if v["channels"] != ["rule"]: continue
            rw = sorted(widx(w) for w in (v["rule_weeks"] or []))
            if len(rw) < 7: continue
            lab = cls[(s, k)]
            rows.append({"seed": s, "key": k, "rule_weeks": rw, "label": lab,
                         "dclass": tuple(sorted(dcls[k])) if lab == "decoy" else (), **meta[(s, k)]})
    return rows


def auc(pos, neg):
    """Mann-Whitney AUC, ties 0.5 (H5's)."""
    if not pos or not neg: return float("nan")
    sn = sorted(neg); n_lt = n_eq = 0.0
    for v in pos:
        lo = bisect.bisect_left(sn, v); hi = bisect.bisect_right(sn, v)
        n_lt += lo; n_eq += hi - lo
    return (n_lt + 0.5 * n_eq) / (len(pos) * len(neg))


def runs(ws):
    if not ws: return []
    out = [1]
    for a, b in zip(ws, ws[1:]):
        if b == a + 1: out[-1] += 1
        else: out.append(1)
    return out


def longest_window(rw):
    """[lo, hi]: longest run of rule weeks widened back by the 8-week rule window (H5-2's scope)."""
    best = cur = [rw[0], rw[0]]
    for a, b in zip(rw, rw[1:]):
        if b == a + 1: cur[1] = b
        else:
            if cur[1] - cur[0] > best[1] - best[0]: best = cur
            cur = [b, b]
    if cur[1] - cur[0] > best[1] - best[0]: best = cur
    return max(0, best[0] - RULE_WIN + 1), best[1]


def lb(n):  # Boundary lower bound: absent 0, '<k' -> 1, else the count
    return 0 if n == 0 else (1 if n < K else n)


def site_weeks(cells_key, is_real):
    """site -> {week: lb}. Rebuilt cells store raw n (bucket here); real cells already store the lb."""
    out = {}
    for s, wk in cells_key.items():
        d = {}
        for w, v in wk.items():
            n = v[0] if is_real else lb(v[0])
            if n > 0: d[int(w)] = n
        if d: out[s] = d
    return out


def h5_feats(sw, rw):
    """H5-2 / H5-3 inputs inside the longest-run window: sync (mean sites per active week), kcells, lr, sync_max."""
    lo, hi = longest_window(rw)
    act = defaultdict(set); kact = defaultdict(set)
    for s, d in sw.items():
        for w, v in d.items():
            if lo <= w <= hi:
                act[w].add(s)
                if v >= K: kact[w].add(s)
    sizes = [len(v) for v in act.values()]
    # label-free week pick inside the window: the week whose 6-week gate window holds the most k-sites
    best_w, best_k, best_s = None, -1, -1
    for w in range(lo, hi + 1):
        ks = ss = 0
        for s, d in sw.items():
            tot = sum(v for ww, v in d.items() if w - GATE_WIN + 1 <= ww <= w)
            if tot >= K: ks += 1
            if tot > 0: ss += 1
        if (ks, ss) > (best_k, best_s): best_w, best_k, best_s = w, ks, ss
    return {"sync": (sum(sizes) / len(sizes)) if sizes else 0.0, "kcells": sum(len(v) for v in kact.values()),
            "lr": max(runs(rw)), "sync_max": max(sizes) if sizes else 0, "lo": lo, "hi": hi,
            "w_maxk": best_w, "w_maxk_ksites": best_k, "n_active_weeks": len(act)}


def gated(f, theta=THETA):
    """THE pre-registered ranker: (1[sync <= theta], kcells, longest_run), higher = better."""
    return (1 if f["sync"] <= theta else 0, f["kcells"], f["lr"])


def lex(f):
    """H5's untuned lexicographic ranker (-sync, kcells, longest_run)."""
    return (-f["sync"], f["kcells"], f["lr"])


def salt_key(key):
    return hashlib.sha256(f"{TIE_SALT}|{key}".encode()).hexdigest()


def topn_det(sub, score, n):
    """Deterministic top-n: score desc, then sha256(tie_salt|key) (emergence_world.ordered_candidates' tie rule)."""
    return sorted(sub, key=lambda r: (tuple(-x for x in score(r)), salt_key(r["key"])))[:n]


def topn_avg(sub, score, n, tie_seeds=20):
    """H5's 20-random-tie-break average: (gold, decoy, feasible gold) counts."""
    g = d = f = 0.0
    for t in range(tie_seeds):
        rnd = random.Random(7919 * t + sub[0]["seed"])
        order = sorted(sub, key=lambda r: (tuple(-x for x in score(r)), rnd.random()))[:n]
        g += sum(r["label"] == "gold" for r in order); d += sum(r["label"] == "decoy" for r in order)
        f += sum(r["label"] == "gold" and r.get("feasible", False) for r in order)
    return g / tie_seeds, d / tie_seeds, f / tie_seeds


def report_auc(rows, score, name, classes=(1, 2, 3, 4, 5)):
    sc = lambda r: score[(r["seed"], r["key"])]
    gold = [sc(r) for r in rows if r["label"] == "gold"]
    dec = [sc(r) for r in rows if r["label"] == "decoy"]
    oth = [sc(r) for r in rows if r["label"] == "other"]
    res = {"gold_vs_decoy": auc(gold, dec), "gold_vs_other": auc(gold, oth), "n_gold": len(gold), "n_decoy": len(dec), "n_other": len(oth)}
    for c in classes:
        dc = [sc(r) for r in rows if r["label"] == "decoy" and c in r["dclass"]]
        res[f"c{c}"] = auc(gold, dc); res[f"n_c{c}"] = len(dc)
    line = f"{name:34s} AUC g/decoy {res['gold_vs_decoy']:.3f} (n {len(gold)} vs {len(dec)}) | " + " ".join(
        f"c{c}:{res[f'c{c}']:.3f}(n={res[f'n_c{c}']})" for c in classes) + f" | g/other {res['gold_vs_other']:.3f} (n_other {len(oth)})"
    print(line)
    return res


# ---------------------------------------------------------------- world rebuild at any size (model K, H5's test_rebuild_cells.py)
def build_cells(size, seed):
    """Returns (cells, labels, meta). cells: key -> site -> {week: [n_pos, n_roots, n_rep, n_all]} over polarity >= 0
    for n_pos. Keys: every (entity, chain predicate) with >= 1 record (label-free). labels: ew.gold_labels (scoring)."""
    import time
    from research.e2e_ingest import emergence_world as ew
    from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, canonical_entity_id
    from mycelic.collective.edge.weeks import iso_week
    from research.mycelic.org import REGION, SITE
    t0 = time.time()
    org, corpus, gold = ew.generate(size, seed)
    sites = ew._user_sites(org)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); wmap = {x: i for i, x in enumerate(weeks)}
    recs = corpus.recs
    wk = [wmap[iso_week(received_date(int(r["t"])))] for r in recs]
    ents = [canonical_entity_id(e) for e in corpus.entities]
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: bykey[(e, pp)].append(r)
    cells = {}
    for (e, pp), rr in bykey.items():
        key = f"entity:{ents[e]}:{pp}"
        acc = defaultdict(lambda: [0, set(), set(), 0])
        for r in rr:
            rec = recs[r]; s = sites[int(rec["uid"])]; w = wk[r]
            a = acc[(s, w)]; a[3] += 1
            if int(rec["polarity"]) < 0: continue
            a[0] += 1; a[1].add(int(rec["event"])); a[2].add(int(rec["uid"]))
        per = defaultdict(dict)
        for (s, w), a in acc.items(): per[s][str(w)] = [a[0], len(a[1]), len(a[2]), a[3]]
        cells[key] = per
    reg_of_site = {}
    for nid in org.levels[SITE]:
        a = org.ancestor_at(nid, REGION); reg_of_site[ew.site_id_of(org, nid)] = -1 if a is None else int(a)
    labels = ew.gold_labels(corpus, gold)
    meta = {"size": size, "seed": seed, "weeks": weeks, "sites": sorted(set(sites.values())), "region_of_site": reg_of_site,
            "n_records": len(recs), "n_keys": len(cells), "secs": round(time.time() - t0, 1)}
    return cells, labels, meta


def rule_weeks_from_cells(cells_key, is_real):
    """The pack rule: week i fires when >= 2 sites have lb >= 1 over weeks [i-7, i] (26 weeks)."""
    sw = site_weeks(cells_key, is_real)
    out = []
    for i in range(26):
        k = sum(1 for s, d in sw.items() if any(i - 7 <= w <= i for w in d))
        if k >= 2: out.append(i)
    return out


def label_rows(cells, labels, rule_weeks, min_rw=7, keys=None):
    """Rows for a world: keys with >= min_rw rule weeks (optionally restricted to `keys`, e.g. the shipped detector's
    rule-only candidates); label from labels doc (scoring only)."""
    gold_keys = {p["key"]: p for p in labels["patterns"]}
    dcls = defaultdict(set)
    for x in labels["decoys"]:
        for k in x["keys"]: dcls[k].add(int(x["class"]))
    rows = []
    for key, rw in rule_weeks.items():
        if keys is not None and key not in keys: continue
        if len(rw) < min_rw: continue
        lab = "gold" if key in gold_keys else ("decoy" if key in dcls else "other")
        rows.append({"key": key, "rule_weeks": rw, "label": lab, "dclass": tuple(sorted(dcls[key])) if lab == "decoy" else ()})
    return rows
