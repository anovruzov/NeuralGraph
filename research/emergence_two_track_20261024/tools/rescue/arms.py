"""Rule-candidate rescue, step 2: budget-matched selection arms replayed on the v4 config's real verdicts.
Budget B = the shipped TOP_N (60 questions per seed). Selection reads only inference-time fields: detector score, rule
hit fields, HQ's own k-suppressed weekly cells. 'label' is read only by common.score. Pre-registered primary scores:
  s_rule  (A1): sites with a rule hit, then summed lower-bound count, then rule weeks
  s_cells (A2): Poisson excess of the candidate window's HQ cell total over the key's own earlier weeks (never later)
Random (A3): 500 draws, mean. Usage: arms.py POOL_DIR"""
import hashlib, math, random, sys, statistics
from common import load, score

B = 60; SEEDS = range(6); FRACS = (0.1, 0.25, 0.5, 1.0)
def h(s): return hashlib.sha256(("rescue|" + s).encode()).hexdigest()
def s_rule(r): return (r["rule_sites"], r["rule_count_lb"], r["rule_weeks"])
def s_cells(r):
    W = sum(r["win_lb"]); hist = r["hist_lb"]
    lam = max((sum(hist) / len(hist)) * len(r["win_lb"]) if hist else 0.0, 1.0)
    return ((W - lam) / math.sqrt(lam), max(r["win_sites"]) if r["win_sites"] else 0)

def pools(p):
    rows = p["rows"]
    det = sorted([i for i, r in enumerate(rows) if r["source"] != "rule"], key=lambda i: (-rows[i]["score"], h(rows[i]["key"])))
    rul = [i for i, r in enumerate(rows) if r["source"] == "rule"]
    return rows, det, rul

def arm(p, kind, f, rng=None):
    rows, det, rul = pools(p)
    nr = int(round(f * B)); nd = B - nr
    if kind == "A0": return det[:B]
    if kind == "A1": pick = sorted(rul, key=lambda i: (tuple(-x for x in s_rule(rows[i])), h(rows[i]["key"])))[:nr]
    elif kind == "A2": pick = sorted(rul, key=lambda i: (tuple(-x for x in s_cells(rows[i])), h(rows[i]["key"])))[:nr]
    elif kind == "A3": pick = rng.sample(rul, min(nr, len(rul)))
    elif kind == "Dall": return det
    elif kind == "ALL": return det + rul
    return det[:nd] + pick

def total(kind, f=0.0, draws=1):
    agg = []
    for d in range(draws):
        rng = random.Random(1000 + d); t = {"found": 0, "rare": 0, "decoys": 0, "bg": 0, "q": 0, "new_gold_asked": 0,
                                             "gained": 0, "lost": 0}
        for s in SEEDS:
            p = load(sys.argv[1], s); base = score(p, arm(p, "A0", 0)); r = score(p, arm(p, kind, f, rng))
            t["found"] += len(r["found"]); t["rare"] += len(r["rare"]); t["decoys"] += r["decoys"]; t["bg"] += r["bg"]
            t["q"] += r["q"]; t["new_gold_asked"] += len(r["gold_asked"] - base["gold_asked"])
            t["gained"] += len(r["found"] - base["found"]); t["lost"] += len(base["found"] - r["found"])
        agg.append(t)
    return {k: statistics.mean(a[k] for a in agg) for k in agg[0]}, (statistics.pstdev(a["found"] for a in agg) if draws > 1 else 0)

print("arm        f     questions found/240 rare  decoys bg   new-gold-asked gained lost  rule-q per gained pattern")
def line(name, f, t, sd=0):
    rq = round(f * B) * 6
    cost = (rq / t["gained"]) if t["gained"] else float("inf")
    print(f"{name:10s} {f:4.2f}  {t['q']:9.0f} {t['found']:6.1f}{'±%.1f' % sd if sd else '    '} {t['rare']:5.1f} {t['decoys']:6.1f} {t['bg']:5.1f} "
          f"{t['new_gold_asked']:8.1f} {t['gained']:10.1f} {t['lost']:5.1f}  {cost:8.1f}")
t, _ = total("A0"); line("A0 snap", 0, t)
for f in FRACS:
    for k, name in (("A1", "A1 rule"), ("A2", "A2 cells"), ("A3", "A3 random")):
        t, sd = total(k, f, 500 if k == "A3" else 1); line(name, f, t, sd)
t, _ = total("Dall"); print("reference (not budget-matched):"); line("all-det", 0, t)
t, _ = total("ALL"); line("v4 all", 0, t)
