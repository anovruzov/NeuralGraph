"""H2 / step 5: the strictly inference-time version of the PN transfer. The system's own judge verdicts (fabric_conclusions
status in the official ce0e357 reports, read-only) define P'' = asked keys with a 'supported' verdict (gold AND accepted
decoys AND background: the system cannot tell them apart) and N'' = asked keys never supported. No benchmark label is
read for fitting. Fit on five seeds' asked keys, score the sixth seed's filtered rule-only pool. Also prints the per-seed
top-20/50 composition for both PN variants (found-based from test_04, verdict-based here) against the n_rw baseline."""
import json, sys, collections
import numpy as np
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2")
from pu_lib import fit_logistic, predict, Scaler, auc, prec_at
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2"
RUNS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/runs/official-ce0e357"
src = open(f"{OUT}/test_02_pu.py").read().split("log = []")[0]
exec(compile(src, "test_02_head", "exec"))
other = cls == "other"; snap = state == "snapshot"
log = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)
nrw_f = n_rw[isUf].astype(float)
# the system's own verdicts
supported = {}
for s in range(6):
    rep = json.load(open(f"{RUNS}/seed-{s}/e2e-ingest-report.json")); assert rep["phase"] == "finished" and int(rep["run"]["seed"]) == s
    for c in rep["discovery"]["fabric_conclusions"]:
        k = (s, f"entity:{c['entity_id']}:{c['predicate']}")
        supported[k] = supported.get(k, False) or (c["status"] == "supported" and bool(c.get("lineage_ok", True)))
sup = np.array([supported.get((r["seed"], r["key"]), False) for r in rows])
asked_in_report = np.array([(r["seed"], r["key"]) in supported for r in rows])
say(f"asked keys in reports {asked_in_report.sum()} (snapshot pool {snap.sum()}; snapshot keys missing from reports {int((snap & ~asked_in_report).sum())}, report keys outside snapshot pool {int((asked_in_report & ~snap).sum())})")
say(f"P'' = asked & supported: {int((snap & sup).sum())} = gold {int((snap & sup & gold).sum())} + decoy {int((snap & sup & decoy).sum())} + other {int((snap & sup & other).sum())}")
say(f"N'' = asked & never supported: {int((snap & ~sup).sum())} = gold {int((snap & ~sup & gold).sum())} + decoy {int((snap & ~sup & decoy).sum())} + other {int((snap & ~sup & other).sum())}")
say(f"agreement of verdict-supported with found on snapshot keys: {np.mean((sup == found)[snap]):.3f}")

def loso_pn(names, ymask):
    sc = np.zeros(isUf.sum())
    for s in range(6):
        tr = snap & (seed != s); te = isUf & (seed == s)
        trr = [r for r, m in zip(rows, tr) if m]; ter = [r for r, m in zip(rows, te) if m]
        sc_ = Scaler().fit(mat(trr, names)); beta = fit_logistic(sc_.t(mat(trr, names)), ymask[tr].astype(float), lam=1.0)
        sc[seed[isUf] == s] = predict(beta, sc_.t(mat(ter, names)))
    return sc
def table(sc, tag):
    gd = (gold | decoy)[isUf]
    aucs = [auc(sc[(seed[isUf] == s) & gd], gold[isUf][(seed[isUf] == s) & gd]) for s in range(6)]
    b_aucs = [auc(nrw_f[(seed[isUf] == s) & gd], gold[isUf][(seed[isUf] == s) & gd]) for s in range(6)]
    say(f"\n{tag}: pooled AUC(g|d) {auc(sc[gd], gold[isUf][gd]):.3f}; per-seed {[round(x,2) for x in aucs]} (n_rw {[round(x,2) for x in b_aucs]}); seeds above n_rw: {sum(a > b for a, b in zip(aucs, b_aucs))}/6")
    say(f"   {'seed':4s} {'U_f':>4s} | {'top20 gold/decoy/other':>24s} {'n_rw top20':>14s} | {'top50 gold/decoy/other':>24s} {'n_rw top50':>14s} | {'wave0 K':>7s} {'PN K gold/decoy':>15s} {'>=11 gold/decoy':>15s}")
    T = np.zeros((3, 2, 3))
    for s in range(6):
        m = seed[isUf] == s; lab = [gold[isUf][m], decoy[isUf][m], other[isUf][m]]; K = int((nrw_f[m] >= 11).sum())
        p20 = [prec_at(sc[m], l, 20) for l in lab]; b20 = [prec_at(nrw_f[m], l, 20) for l in lab]
        p50 = [prec_at(sc[m], l, 50) for l in lab]; b50 = [prec_at(nrw_f[m], l, 50) for l in lab]
        pK = [prec_at(sc[m], l, K) for l in lab]; cK = [float(((nrw_f[m] >= 11) & l).sum()) for l in lab]
        T[0, 0] += p20; T[0, 1] += b20; T[1, 0] += p50; T[1, 1] += b50; T[2, 0] += pK; T[2, 1] += cK
        say(f"   {s:4d} {int(m.sum()):4d} | {p20[0]:6.1f}/{p20[1]:5.1f}/{p20[2]:5.1f}        {b20[0]:4.1f}/{b20[1]:4.1f}/{b20[2]:4.1f} | {p50[0]:6.1f}/{p50[1]:5.1f}/{p50[2]:5.1f}        {b50[0]:4.1f}/{b50[1]:4.1f}/{b50[2]:4.1f} | {K:7d} {pK[0]:7.0f}/{pK[1]:<7.0f} {cK[0]:7.0f}/{cK[1]:<7.0f}")
    say(f"   six-seed totals: top20 PN {T[0,0,0]:.0f}/{T[0,0,1]:.0f}/{T[0,0,2]:.0f} vs n_rw {T[0,1,0]:.1f}/{T[0,1,1]:.1f}/{T[0,1,2]:.1f} | top50 PN {T[1,0,0]:.0f}/{T[1,0,1]:.0f}/{T[1,0,2]:.0f} vs n_rw {T[1,1,0]:.1f}/{T[1,1,1]:.1f}/{T[1,1,2]:.1f}"
        f" | wave-0 budget PN {T[2,0,0]:.0f}/{T[2,0,1]:.0f} vs >=11 cut {T[2,1,0]:.0f}/{T[2,1,1]:.0f}")
    say(f"   precision: top20 gold {T[0,0,0]/120:.3f} decoy {T[0,0,1]/120:.3f} (n_rw {T[0,1,0]/120:.3f}/{T[0,1,1]/120:.3f}) | top50 gold {T[1,0,0]/300:.3f} decoy {T[1,0,1]/300:.3f} (n_rw {T[1,1,0]/300:.3f}/{T[1,1,1]/300:.3f})")
    return aucs
res = {}
for fs in ("buckets", "ALL"):
    res[("verdict", fs)] = table(loso_pn(FEATURE_SETS[fs], sup), f"PN-transfer VERDICT-based (asked&supported vs asked&never-supported) / {fs}")
    res[("found", fs)] = table(loso_pn(FEATURE_SETS[fs], found), f"PN-transfer FOUND-based (snapshot gold found vs rest of snapshot pool) / {fs}")
json.dump({f"{k[0]}|{k[1]}": v for k, v in res.items()}, open(f"{OUT}/results_verdicts.json", "w"), indent=1)
open(f"{OUT}/results_verdicts.txt", "w").write("\n".join(log) + "\n")
