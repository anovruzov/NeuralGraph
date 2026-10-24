"""H2 / step 3: diagnostics around the PU result.
 (a) SCAR check: do the known positives (snapshot gold) look like the rule-only gold on the gate's exported buckets?
 (b) budget-matched comparison with wave 0's n_rw>=11 cut: same number of keys per seed, PU-buckets LOSO order.
 (c) per-seed top-20/50 composition (gold/decoy/other) for the buckets-only LOSO ranker.
 (d) ORACLE-DIAGNOSTIC, NOT A HYPOTHESIS: supervised gold-vs-decoy logistic fitted ON LABELS, leave-one-seed-out, to bound
     what the exported feature space could ever separate. Labels select here by design; this number is a ceiling for the
     feature space and is never a ranker."""
import json, sys
import numpy as np
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2")
from pu_lib import fit_logistic, predict, Scaler, auc, prec_at
import importlib.util
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2"
# reuse feature construction from test_02 without re-running its grid: exec the top part
src = open(f"{OUT}/test_02_pu.py").read().split("log = []")[0]
exec(compile(src, "test_02_head", "exec"))
other = cls == "other"
log = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)

say("== (a) SCAR check: gate-shaped exported buckets, known positives vs the rule-only pool (filtered n_rw>6) ==")
groups = [("P: snapshot gold", P_DEFS["snap_gold"]), ("snapshot decoy", (state == "snapshot") & decoy),
          ("U_f gold", isUf & gold), ("U_f decoy", isUf & decoy), ("U_f other", isUf & other)]
F = ["wk_ge2k", "wk_n1", "wk_ge1k", "max_ksites", "max_roots_lb", "max_users_lb", "max_site_bucket", "n_active_wk", "rw_first_half", "rw_last6"]
say(f"{'group':18s} {'n':>5s} {'P(wk_ge2k>=1)':>14s} {'P(wk_n1>=1)':>12s} " + " ".join(f"{f:>14s}" for f in F))
for name, m in groups:
    rs = [r for r, x in zip(rows, m) if x]
    say(f"{name:18s} {len(rs):5d} {np.mean([r['wk_ge2k'] >= 1 for r in rs]):14.3f} {np.mean([r['wk_n1'] >= 1 for r in rs]):12.3f} "
        + " ".join(f"{np.mean([r[f] for r in rs]):14.2f}" for f in F))

def loso_scores(names, Pmask):
    sc = np.zeros(isUf.sum())
    for s in range(6):
        Pm = Pmask & (seed != s); tr = Pm | (isUf & (seed != s)); te = isUf & (seed == s)
        trr = [r for r, m in zip(rows, tr) if m]; ter = [r for r, m in zip(rows, te) if m]
        sc_ = Scaler().fit(mat(trr, names)); beta = fit_logistic(sc_.t(mat(trr, names)), Pm[tr].astype(float), lam=1.0)
        sc[seed[isUf] == s] = predict(beta, sc_.t(mat(ter, names)))
    return sc
sc_b = loso_scores(FEATURE_SETS["buckets"], P_DEFS["snap_gold"]); sc_a = loso_scores(FEATURE_SETS["ALL"], P_DEFS["snap_gold"])
nrw_f = n_rw[isUf].astype(float)

say("\n== (b) budget-matched vs wave 0: per seed, K = keys kept at n_rw>=11; PU-buckets LOSO top-K vs the >=11 cut (gold/decoy/other) ==")
tot = np.zeros((3, 3))
for s in range(6):
    m = seed[isUf] == s; K = int((nrw_f[m] >= 11).sum())
    lab = [gold[isUf][m], decoy[isUf][m], other[isUf][m]]
    cut = [float(((nrw_f[m] >= 11) & l).sum()) for l in lab]
    pu = [prec_at(sc_b[m], l, K) for l in lab]; pa = [prec_at(sc_a[m], l, K) for l in lab]
    tot += np.array([cut, pu, pa])
    say(f"   seed {s}: K={K:3d}  >=11 cut {cut[0]:.0f}/{cut[1]:.0f}/{cut[2]:.0f}   PU-buckets {pu[0]:.0f}/{pu[1]:.0f}/{pu[2]:.0f}   PU-ALL {pa[0]:.0f}/{pa[1]:.0f}/{pa[2]:.0f}")
n = tot[0].sum()
for i, name in enumerate([">=11 cut", "PU-buckets", "PU-ALL"]):
    say(f"   {name:11s} total {int(n)} keys: gold {tot[i,0]:.0f} ({tot[i,0]/n:.3f}) decoy {tot[i,1]:.0f} ({tot[i,1]/n:.3f}) other {tot[i,2]:.0f} ({tot[i,2]/n:.3f})  gold:decoy {tot[i,0]/max(tot[i,1],1e-9):.2f}")

say("\n== (c) buckets-only LOSO ranker: top-20 / top-50 composition per seed (gold/decoy/other) vs n_rw baseline ==")
T = np.zeros((2, 2, 3))
for s in range(6):
    m = seed[isUf] == s; lab = [gold[isUf][m], decoy[isUf][m], other[isUf][m]]
    row = f"   seed {s}:"
    for j, N in enumerate((20, 50)):
        pu = [prec_at(sc_b[m], l, N) for l in lab]; ba = [prec_at(nrw_f[m], l, N) for l in lab]
        T[j, 0] += pu; T[j, 1] += ba
        row += f"  top{N} PU {pu[0]:.1f}/{pu[1]:.1f}/{pu[2]:.1f}  base {ba[0]:.1f}/{ba[1]:.1f}/{ba[2]:.1f}"
    say(row)
for j, N in enumerate((20, 50)):
    say(f"   six-seed total top{N} ({6*N} keys): PU gold {T[j,0,0]:.0f} ({T[j,0,0]/(6*N):.3f}) decoy {T[j,0,1]:.0f} ({T[j,0,1]/(6*N):.3f})"
        f" | base gold {T[j,1,0]:.1f} ({T[j,1,0]/(6*N):.3f}) decoy {T[j,1,1]:.1f} ({T[j,1,1]/(6*N):.3f})")

say("\n== (d) ORACLE-DIAGNOSTIC (labels used to FIT; a ceiling for the feature space, NOT a ranker, NOT a hypothesis) ==")
say("supervised gold-vs-decoy logistic on U_f gold+decoy rows, leave-one-seed-out, lam=1")
for fs in ("rw_only", "shape", "shape+ent", "buckets", "ALL"):
    names = FEATURE_SETS[fs]; gd = isUf & (gold | decoy); aucs = []; sc = np.full(isUf.sum(), np.nan)
    for s in range(6):
        tr = gd & (seed != s); te = gd & (seed == s)
        trr = [r for r, m in zip(rows, tr) if m]; ter = [r for r, m in zip(rows, te) if m]
        sc_ = Scaler().fit(mat(trr, names)); beta = fit_logistic(sc_.t(mat(trr, names)), gold[tr].astype(float), lam=1.0)
        p = predict(beta, sc_.t(mat(ter, names))); aucs.append(auc(p, gold[te]))
    say(f"   {fs:12s} AUC(gold|decoy) per seed [{' '.join(f'{x:.2f}' for x in aucs)}]  mean {np.mean(aucs):.3f} spread {min(aucs):.2f}-{max(aucs):.2f}")
say("if even the oracle sits near 0.6-0.7, the exported feature space, not the PU estimator, is what cannot tell gold from decoy")
open(f"{OUT}/results_diag.txt", "w").write("\n".join(log) + "\n")
