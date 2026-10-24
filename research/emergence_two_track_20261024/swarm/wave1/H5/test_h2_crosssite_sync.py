"""H5-2/H5-3: cross-site timing statistics from exported weekly cells (per site, per key, per ISO week: a count that
is absent, '<k' or an int >= k). The cells come from test_rebuild_cells.py (model K, polarity >= 0). Everything here
reads only: cell presence / lower bound per (site, week), the rule hit weeks. Labels score afterwards."""
import sys, json, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5")
from h5_common import *
from collections import Counter, defaultdict
K = 3; RULE_WIN = 8; GATE_WIN = 6

rows = load_pool()
cells = {s: json.load(open(f"{OUT}/cells-seed-{s}.json"))["cells"] for s in SEEDS}

def lb(n):            # the Boundary's lower bound: absent 0, '<k' -> 1, else the count
    return 0 if n == 0 else (1 if n < K else n)

def site_weeks(seed, key):
    """site -> {week_idx: lb}; only positive-polarity n (negated claims never enter a cell)."""
    out = {}
    for s, wk in cells[seed].get(key, {}).items():
        d = {int(w): lb(v[0]) for w, v in wk.items() if v[0] > 0}
        if d: out[s] = d
    return out

def longest_window(rw):
    """[lo, hi] of the longest run of rule weeks, widened by the rule window (the activity that fired it)."""
    best = cur = [rw[0], rw[0]]
    for a, b in zip(rw, rw[1:]):
        if b == a + 1: cur[1] = b
        else:
            if cur[1] - cur[0] > best[1] - best[0]: best = cur
            cur = [b, b]
    if cur[1] - cur[0] > best[1] - best[0]: best = cur
    return max(0, best[0] - RULE_WIN + 1), best[1]

def stats(sw, lo, hi):
    """statistics over weeks lo..hi from per-site lb series."""
    act = defaultdict(set); kact = defaultdict(set)
    for s, d in sw.items():
        for w, v in d.items():
            if lo <= w <= hi:
                act[w].add(s)
                if v >= K: kact[w].add(s)
    if not act:
        return {"sync_max": 0, "n_sync_weeks": 0, "excl_frac": 1.0, "ksync_max": 0, "first_spread": 0, "first_std": 0.0,
                "n_kcells": 0, "n_active_cells": 0, "n_active_sites": 0, "n_active_weeks": 0, "max_ksites_6w": 0,
                "max_sites_6w": 0, "mean_sites_per_week": 0.0}
    sizes = [len(v) for v in act.values()]
    firsts = {}
    for w in sorted(act):
        for s in act[w]: firsts.setdefault(s, w)
    fv = list(firsts.values())
    # gate-like counts inside a 6-week window: sites whose window lb-sum >= K, and sites with any record
    mk = ms = 0
    for w in range(lo, hi + 1):
        ks = ss = 0
        for s, d in sw.items():
            tot = sum(v for ww, v in d.items() if w - GATE_WIN + 1 <= ww <= w)
            if tot >= K: ks += 1
            if tot > 0: ss += 1
        mk = max(mk, ks); ms = max(ms, ss)
    return {"sync_max": max(sizes), "n_sync_weeks": sum(1 for x in sizes if x >= 2),
            "excl_frac": sum(1 for x in sizes if x == 1) / len(sizes), "ksync_max": max((len(v) for v in kact.values()), default=0),
            "first_spread": max(fv) - min(fv), "first_std": statistics.pstdev(fv) if len(fv) > 1 else 0.0,
            "n_kcells": sum(len(v) for v in kact.values()), "n_active_cells": sum(sizes), "n_active_sites": len(firsts),
            "n_active_weeks": len(act), "max_ksites_6w": mk, "max_sites_6w": ms, "mean_sites_per_week": sum(sizes) / len(sizes)}

F = {}
for r in rows:
    sw = site_weeks(r["seed"], r["key"]); rw = r["rule_weeks"]
    lo, hi = longest_window(rw)
    a = stats(sw, 0, 25); b = stats(sw, lo, hi)
    f = {f"{k}_all": v for k, v in a.items()}; f.update({f"{k}_win": v for k, v in b.items()})
    f["longest_run"] = max(runs(rw)); f["count"] = len(rw)
    F[(r["seed"], r["key"])] = f

# sign convention: higher = more gold-like. a priori from the generator: gold bursts are single-site, decoys multi-site
GOLD_HIGH = ["excl_frac", "first_spread", "first_std", "n_kcells", "max_ksites_6w", "n_active_weeks"]
GOLD_LOW = ["sync_max", "n_sync_weeks", "ksync_max", "n_active_sites", "max_sites_6w", "mean_sites_per_week", "n_active_cells"]
print("filtered pool", len(rows))
print("\n== AUC, statistics over ALL 26 weeks ==")
R = {}
for scope in ("all", "win"):
    print(f"\n== scope: {'all 26 weeks' if scope == 'all' else 'longest rule run window (run widened by 8-week rule window)'} ==")
    for n in GOLD_HIGH:
        R[f"{n}_{scope}"] = report_auc(rows, {k: v[f"{n}_{scope}"] for k, v in F.items()}, f"{n}_{scope}")
    for n in GOLD_LOW:
        R[f"neg_{n}_{scope}"] = report_auc(rows, {k: -v[f"{n}_{scope}"] for k, v in F.items()}, f"neg_{n}_{scope}")
print("\n== baselines ==")
report_auc(rows, {k: v["count"] for k, v in F.items()}, "count (rule_weeks)")
report_auc(rows, {k: v["longest_run"] for k, v in F.items()}, "longest_run")

print("\n== distributions by class (scope win) ==")
def table(name, neg=False):
    print(f"-- {name}")
    for lab in ("gold", "decoy", "other"):
        c = Counter(F[(r["seed"], r["key"])][name] for r in rows if r["label"] == lab)
        print(f"  {lab:6s} n={sum(c.values()):4d}", sorted(c.items())[:14])
    for cl in (1, 2, 3, 4, 5):
        c = Counter(F[(r["seed"], r["key"])][name] for r in rows if r["label"] == "decoy" and cl in r["dclass"])
        print(f"  decoy{cl} n={sum(c.values()):4d}", sorted(c.items())[:14])
for n in ("sync_max_win", "n_sync_weeks_win", "ksync_max_win", "first_spread_win", "n_kcells_win", "max_ksites_6w_win", "n_active_sites_win"):
    table(n)
print("\n== precision at top-N (single statistics) ==")
for n in ("count", "longest_run", "n_kcells_win", "max_ksites_6w_win"):
    report_prec(rows, {k: v[n] for k, v in F.items()}, n)
for n in ("sync_max_win", "n_sync_weeks_win", "mean_sites_per_week_win"):
    report_prec(rows, {k: -v[n] for k, v in F.items()}, "neg_" + n)
json.dump({f"{k[0]}|{k[1]}": v for k, v in F.items()}, open(f"{OUT}/features_h2.json", "w"))
