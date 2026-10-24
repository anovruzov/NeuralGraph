"""H5-1: shape of the rule-fire week set (dumps only, free). Statistics over rule_weeks (exported: the rules
channel's hit weeks per key). Scored afterwards against gold / decoy(class) / other on the filtered pool."""
import sys, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5")
from h5_common import *

rows = load_pool()
print("filtered pool", len(rows), {l: sum(r["label"] == l for r in rows) for l in ("gold", "decoy", "other")})

def feats(rw):
    rs = runs(rw); gaps = [b - a for a, b in zip(rw, rw[1:])]
    big = [g for g in gaps if g > 1]
    span = rw[-1] - rw[0] + 1
    return {"count": len(rw), "longest_run": max(rs), "n_runs": len(rs), "count_minus_longest": len(rw) - max(rs),
            "neg_n_runs": -len(rs), "neg_count_minus_longest": -(len(rw) - max(rs)),
            "longest_frac": max(rs) / len(rw), "span": span, "density": len(rw) / span,
            "gap_var": statistics.pvariance(gaps) if len(gaps) > 1 else 0.0, "neg_gap_var": -(statistics.pvariance(gaps) if len(gaps) > 1 else 0.0),
            "n_big_gaps": len(big), "first": rw[0], "neg_first": -rw[0], "last": rw[-1],
            "longest_minus_8": max(rs) - 8}
F = {(r["seed"], r["key"]): feats(r["rule_weeks"]) for r in rows}
names = ["count", "longest_run", "n_runs", "neg_n_runs", "count_minus_longest", "neg_count_minus_longest", "longest_frac",
         "span", "density", "gap_var", "neg_gap_var", "n_big_gaps", "first", "neg_first", "last"]
print("\n== AUC (higher statistic = ranked more gold-like) ==")
R = {}
for n in names:
    R[n] = report_auc(rows, {k: v[n] for k, v in F.items()}, n)
print("\n== precision at top-N per seed (random tie-break averaged over 20 draws) ==")
for n in ("count", "longest_run", "span", "neg_n_runs", "neg_count_minus_longest", "longest_frac"):
    report_prec(rows, {k: v[n] for k, v in F.items()}, n)
# distribution tables: longest_run by class
print("\n== longest_run distribution by class ==")
from collections import Counter
for lab in ("gold", "decoy", "other"):
    c = Counter(F[(r["seed"], r["key"])]["longest_run"] for r in rows if r["label"] == lab)
    print(lab, sorted(c.items()))
print("\n== n_runs distribution by class ==")
for lab in ("gold", "decoy", "other"):
    c = Counter(F[(r["seed"], r["key"])]["n_runs"] for r in rows if r["label"] == lab)
    print(lab, sorted(c.items()))
print("\n== per decoy class: median count / longest_run / n_runs ==")
for cl in (1, 2, 3, 4, 5):
    sub = [F[(r["seed"], r["key"])] for r in rows if r["label"] == "decoy" and cl in r["dclass"]]
    if sub: print(cl, len(sub), "count", statistics.median(x["count"] for x in sub), "longest", statistics.median(x["longest_run"] for x in sub), "n_runs", statistics.median(x["n_runs"] for x in sub), "span", statistics.median(x["span"] for x in sub))
sub = [F[(r["seed"], r["key"])] for r in rows if r["label"] == "gold"]
print("gold", len(sub), "count", statistics.median(x["count"] for x in sub), "longest", statistics.median(x["longest_run"] for x in sub), "n_runs", statistics.median(x["n_runs"] for x in sub), "span", statistics.median(x["span"] for x in sub))
sub = [F[(r["seed"], r["key"])] for r in rows if r["label"] == "other"]
print("other", len(sub), "count", statistics.median(x["count"] for x in sub), "longest", statistics.median(x["longest_run"] for x in sub), "n_runs", statistics.median(x["n_runs"] for x in sub), "span", statistics.median(x["span"] for x in sub))
