"""Budget-matched (B = 60/seed) re-ranking of the DETECTOR candidate pool, replayed on the v4 config's real verdicts.
Inference-time fields only; 'label' read only by common.score. 'oracle' is an upper bound that reads labels (never a
selector). Usage: det_rank.py POOL_DIR"""
import hashlib, math, random, statistics, sys
from collections import Counter
from common import load, score
B = 60
def h(s): return hashlib.sha256(("rescue|" + s).encode()).hexdigest()
def z(r):
    W = sum(r["win_lb"]); hist = r["hist_lb"]
    lam = max((sum(hist) / len(hist)) * len(r["win_lb"]) if hist else 0.0, 1.0)
    return (W - lam) / math.sqrt(lam)
RANKS = {
    "shipped (score desc)": lambda r: (-r["score"],),
    "score asc": lambda r: (r["score"],),
    "cells excess z desc": lambda r: (-z(r),),
    "alert weeks desc": lambda r: (-r["n_alert_weeks"], -r["score"]),
    "candidate weeks desc": lambda r: (-r["n_candidate_weeks"], -r["score"]),
    "supporting sites desc": lambda r: (-r["supporting_sites"], -r["score"]),
    "window sites max desc": lambda r: (-max(r["win_sites"] or [0]), -r["score"]),
    "as_of latest first": lambda r: (tuple(-ord(c) for c in r["as_of"]),),
    "as_of earliest first": lambda r: (r["as_of"],),
    "has rule hit first": lambda r: (-len(r["rule_ids"]), -r["score"]),
}
seeds = range(6); out = {}
def run(sel):
    t = Counter(); per = []
    for s in seeds:
        p = load(sys.argv[1], s); rows = p["rows"]
        det = [i for i, r in enumerate(rows) if r["source"] != "rule"]
        r = score(p, sel(rows, det)); per.append(len(r["found"]))
        t["found"] += len(r["found"]); t["rare"] += len(r["rare"]); t["decoys"] += r["decoys"]; t["bg"] += r["bg"]; t["q"] += r["q"]
    return t, per
print(f"{'ranking of detector pool, B=60/seed':28s} q   found/240 rare decoys bg   per-seed")
for name, key in RANKS.items():
    t, per = run(lambda rows, det: sorted(det, key=lambda i: (*key(rows[i]), h(rows[i]["key"])))[:B])
    print(f"{name:28s} {t['q']:4d} {t['found']:5d} {t['rare']:6d} {t['decoys']:6d} {t['bg']:4d}   {per}")
rs = []
for d in range(500):
    rng = random.Random(d); t, _ = run(lambda rows, det: rng.sample(det, min(B, len(det)))); rs.append(t)
print(f"{'random (500 draws, mean)':28s} {360:4d} {statistics.mean(x['found'] for x in rs):5.1f} {statistics.mean(x['rare'] for x in rs):6.1f} "
      f"{statistics.mean(x['decoys'] for x in rs):6.1f} {statistics.mean(x['bg'] for x in rs):4.1f}   sd {statistics.pstdev(x['found'] for x in rs):.1f}")
def oracle(rows, det):
    good = [i for i in det if rows[i]["status"] == "supported" and rows[i]["lineage_ok"] and rows[i]["label"]["label"] == "true"]
    seen, pick = set(), []
    for i in good:
        if rows[i]["label"]["pattern"] not in seen: seen.add(rows[i]["label"]["pattern"]); pick.append(i)
    return pick[:B]
t, per = run(oracle); print(f"{'ORACLE bound (reads labels)':28s} {t['q']:4d} {t['found']:5d} {t['rare']:6d} {t['decoys']:6d} {t['bg']:4d}   {per}")
t, per = run(lambda rows, det: det); print(f"{'all detector (not matched)':28s} {t['q']:4d} {t['found']:5d} {t['rare']:6d} {t['decoys']:6d} {t['bg']:4d}   {per}")
