"""KILL 5 -- H4-2's week choice as a separate claim: argmax_w P^(clear | b_w), label-free (P^ tabulated from gate OUTCOMES
on the other seeds), lifts a filtered key from 1-in-30 (first rule week) to 1-in-18 corrected. Re-run once; seed-swap
the P^ table; compare with parameter-free structural week rules to see whether the table adds anything."""
import sys, os, json, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f_common import *
from collections import Counter

D = load_cells(); rows = build_rows(D)
by_sk = {}
for r in rows: by_sk.setdefault((r["seed"], r["key"]), []).append(r)
per_key = score_keys(D, rows, loso); F = filtered(per_key)
print("filtered pool", len(F))

def tally(pick):
    """pick(v) -> (wk, tb). corrected = gold & clears & in-window; decoy accepted = decoy & clears & in-window; unplanted = other & clears"""
    g = d = o = 0
    for v in F.values():
        wk, tb = pick(v)
        if v["cls"] == "gold" and tb and in_window(v, wk): g += 1
        elif v["cls"] == "decoy" and tb and in_window(v, wk): d += 1
        elif v["cls"] == "other" and tb: o += 1
    return g, d, o

def line(name, g, d, o):
    print(f"  {name:58s} corrected {g:3d} (1 in {len(F)/max(1,g):5.1f})  decoys_acc {d:3d}  unplanted_sup {o:3d}")

print("\n[re-run] week policies on the filtered pool, one question per key (gate model B, NOT deflated):")
line("latest rule week", *tally(lambda v: (v["latest_wk"], v["latest_tb"])))
line("first rule week (replay's rule_snapshots policy)", *tally(lambda v: (v["first_wk"], v["first_tb"])))
line("argmax_w P^(clear|b_w), LOSO on outcomes (H4-2's claim)", *tally(lambda v: (v["S_Pclear_wk"], v["S_Pclear_tb"])))
anyg = sum(1 for v in F.values() if v["cls"] == "gold" and any(tb and in_window(v, wk) for wk, tb, c, f in v["rule_rows"]))
print(f"  {'any rule week (ceiling)':58s} corrected {anyg:3d}")

# ---- structural, parameter-free week rules (no table at all), argmax with ties -> latest, same as H4's tie rule
def pick_struct(fn):
    def p(v):
        best = max(v["rule_rows"], key=lambda r: (fn(r[3]), r[0])); return best[0], best[1]
    return p
print("\n[stand-alone?] parameter-free week rules computed from the same exported view (no fitted table):")
line("argmax sumlb (total exported lower bound), ties latest", *tally(pick_struct(lambda f: f["sumlb"])))
line("argmax count_cert (gate count condition certain from b), ties latest", *tally(pick_struct(lambda f: f["count_cert"])))
line("argmax S_cert (count_cert & roots>=3 & rep>=3 & fresh), ties latest", *tally(pick_struct(lambda f: (f["count_cert"] and f["roots"] >= 3 and f["rep"] >= 3 and f["fresh"]))))
line("lexicographic (S_cert, count_cert, count_poss, sumlb), ties latest", *tally(pick_struct(lambda f: ((f["count_cert"] and f["roots"] >= 3 and f["rep"] >= 3 and f["fresh"]), f["count_cert"], f["count_poss"], f["sumlb"]))))
line("argmax nC (sites certainly at k), ties latest", *tally(pick_struct(lambda f: f["nC"])))

# ---- seed-swap of the P^ table: refit on every 4-of-5 training subset per held-out seed, and on 3 seeds
print("\n[seed-swap] P^(clear|b) refit on fewer / different training seeds; does the chosen week and the count move?")
base_wk = {k: v["S_Pclear_wk"] for k, v in F.items()}
res = []
for s in SEEDS:
    others = loso(s)
    for drop in others:
        train = [t for t in others if t != drop]
        Fit = fit_tables(rows, train)
        changed = 0; g = 0
        for (sd, key), v in F.items():
            if sd != s: continue
            rr = v["rule_rows"]
            best = max(rr, key=lambda r: (Fit["pclear"](r[2]), r[0]))
            if best[0] != base_wk[(sd, key)]: changed += 1
            if v["cls"] == "gold" and best[1] and in_window(v, best[0]): g += 1
        g_base = sum(1 for (sd, key), v in F.items() if sd == s and v["cls"] == "gold" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]))
        n_s = sum(1 for (sd, key) in F if sd == s)
        res.append((s, drop, changed, n_s, g, g_base))
print("  held-out  dropped-train  keys_with_week_changed/keys  corrected(4-seed fit) vs corrected(5-seed fit)")
for s, drop, ch, n, g, gb in res: print(f"  {s:8s} {drop:13s} {ch:4d}/{n:<4d} {g:3d} vs {gb}")
tot4 = {}
for s, drop, ch, n, g, gb in res: tot4.setdefault(drop, []).append(g)
print("  six-seed corrected totals when each training-seed-drop is applied uniformly:",
      {d: sum(sum(x for (s2, d2, c2, n2, x, gb2) in res if d2 == d and s2 == s) for s in SEEDS) for d in SEEDS})
# 3-seed fit: fit on {0,1,2} score {3,4,5} and vice versa
for train, test in (( ["0","1","2"], ["3","4","5"]), (["3","4","5"], ["0","1","2"])):
    Fit = fit_tables(rows, train); g = d = o = 0; changed = 0; n = 0
    for (sd, key), v in F.items():
        if sd not in test: continue
        n += 1; best = max(v["rule_rows"], key=lambda r: (Fit["pclear"](r[2]), r[0]))
        if best[0] != base_wk[(sd, key)]: changed += 1
        if v["cls"] == "gold" and best[1] and in_window(v, best[0]): g += 1
        elif v["cls"] == "decoy" and best[1] and in_window(v, best[0]): d += 1
        elif v["cls"] == "other" and best[1]: o += 1
    gb = sum(1 for (sd, key), v in F.items() if sd in test and v["cls"] == "gold" and v["S_Pclear_tb"] and in_window(v, v["S_Pclear_wk"]))
    print(f"  fit on {train} score {test}: corrected {g} (LOSO gave {gb}), decoys {d}, unplanted {o}, weeks changed {changed}/{n}")

# ---- what P^(clear|b) is: how deterministic is the gate given the class? (if b decides G, the table is a lookup of the gate)
Fit = fit_tables(rows, SEEDS); T = Fit["T"]
det = sum(1 for k in T["n"] if T["c"][k] in (0, T["n"][k])); mass = sum(T["n"][k] for k in T["n"] if T["c"][k] in (0, T["n"][k]))
print(f"\nP(clear|class) is exactly 0 or 1 on {det}/{len(T['n'])} classes carrying {mass}/{sum(T['n'].values())} rows ({mass/sum(T['n'].values()):.1%}):"
      " the 'label-free table' is mostly a lookup of the gate's own deterministic reading of b.")
# agreement between table-argmax week and S_cert-structural week
agree = sum(1 for v in F.values() if v["S_Pclear_wk"] == max(v["rule_rows"], key=lambda r: ((r[3]["count_cert"] and r[3]["roots"] >= 3 and r[3]["rep"] >= 3 and r[3]["fresh"]), r[3]["count_cert"], r[3]["count_poss"], r[3]["sumlb"], r[0]))[0])
print(f"table-argmax week == lexicographic structural week on {agree}/{len(F)} filtered keys")
