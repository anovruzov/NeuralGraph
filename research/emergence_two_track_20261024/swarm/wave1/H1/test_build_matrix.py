"""H1 step 1: rebuild dev worlds 0-5 with the frozen generator and export, per pool key, the per-site per-week
6-week-window record count bucketed at k=3 (0 -> 0, 1..2 -> '<k' -> 1, >=3 -> count). Follows ceiling_b.py's
model K rows (anchor OR aux is the key entity, predicate maps to the key's chain, polarity<0 excluded).
Writes OUT/matrix_seed-{s}.npz with: keys (str), sites, weeks, W6 (keys x 7 x 26 bucketed window counts),
W1 (keys x 7 x 26 bucketed weekly cells, saved but NOT used by the pre-registered tests), rule_jaccard diagnostics.
Read-only on every repository. Labels are never read here."""
import csv, json, os, sys, time
import numpy as np
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate
from mycelic.collective.edge.weeks import iso_week

OUT = os.environ["H1_OUT"]
POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
DUMPS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps"
K = 3; WIN = 6

def bucket(c):
    c = np.asarray(c, dtype=np.int64)
    return np.where(c == 0, 0, np.where(c < K, 1, c))

def build(seed):
    t = time.time()
    rows = [r for r in csv.DictReader(open(POOL)) if int(r["seed"]) == seed]
    keys = [r["key"] for r in rows]
    org, corpus, gold = ew.generate(200, seed)
    usite = ew._user_sites(org); sites = sorted(set(usite.values())); sidx = {s: i for i, s in enumerate(sites)}
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}
    recs = corpus.recs
    canon = [ew.canonical_entity_id(e) for e in corpus.entities]
    # per (entity canonical id, chain predicate): weekly per-site counts, polarity<0 excluded (ceiling_b K, non-blind)
    cells = defaultdict(lambda: np.zeros((len(sites), len(weeks)), dtype=np.int64))
    wk = np.array([widx[iso_week(received_date(int(tt)))] for tt in recs["t"]])
    st = np.array([sidx[usite[int(u)]] for u in recs["uid"]])
    pp = [pack_predicate(PREDICATES[int(p)]) if int(p) >= 0 else None for p in recs["pred"]]
    for r in range(len(recs)):
        if pp[r] is None or int(recs[r]["polarity"]) < 0: continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0: cells[(canon[e], pp[r])][st[r], wk[r]] += 1
    W1 = np.zeros((len(keys), len(sites), len(weeks)), dtype=np.int64)
    missing = 0
    for i, k in enumerate(keys):
        _, ent, pred = k.split(":")
        c = cells.get((ent, pred))
        if c is None: missing += 1; continue
        W1[i] = c
    # 6-week window ending at w (partial at the start, exactly as ceiling_b's lo = w-WIN+1)
    cs = np.concatenate([np.zeros_like(W1[..., :1]), np.cumsum(W1, axis=2)], axis=2)
    W6 = np.stack([cs[..., w + 1] - cs[..., max(0, w - WIN + 1)] for w in range(len(weeks))], axis=2)
    B6 = bucket(W6); B1 = bucket(W1)
    # diagnostic only: the rule channel = >=2 sites with 8-week lower-bound count >= 1; compare to the dump's rule_weeks
    cs8 = np.stack([cs[..., w + 1] - cs[..., max(0, w - 8 + 1)] for w in range(len(weeks))], axis=2)
    dump = json.load(open(f"{DUMPS}/seed-{seed}.json"))["candidates"]
    jac = []
    for i, k in enumerate(keys):
        mine = {weeks[w] for w in range(len(weeks)) if (cs8[i, :, w] >= 1).sum() >= 2}
        theirs = set(dump.get(k, {}).get("rule_weeks") or [])
        jac.append(len(mine & theirs) / max(1, len(mine | theirs)))
    np.savez_compressed(f"{OUT}/matrix_seed-{seed}.npz", keys=np.array(keys), sites=np.array(sites), weeks=np.array(weeks),
                        W6=B6, W1=B1, raw6=W6, rule_jaccard=np.array(jac))
    print(f"seed {seed}: keys {len(keys)} missing {missing} sites {len(sites)} weeks {len(weeks)} "
          f"rule-week jaccard mean {np.mean(jac):.3f} median {np.median(jac):.3f} min {np.min(jac):.3f}  ({time.time()-t:.1f}s)", flush=True)

if __name__ == "__main__":
    for s in [int(x) for x in sys.argv[1:]] or range(6):
        assert s in range(6)
        build(s)
