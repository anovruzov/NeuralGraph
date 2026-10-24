"""H2 / step 4: two label-free resurrection tests, run because the oracle (test_03 d) showed the exported bucket space
separates gold from decoy at AUC ~0.83-0.86 held-out while the PU ranker reaches ~0.64.
 (A) PN transfer on the system's OWN outcomes: P = snapshot keys the system asked and FOUND; N = snapshot keys asked and
     NOT found (the system knows both at inference time; no benchmark label is read). Fit on five seeds' snapshot pool,
     score the sixth seed's filtered rule-only pool. Gold/decoy labels score only.
 (B) single exported statistics, NO fitting: s(key) over the per-week per-site buckets. Multiplicity disclosed (every one tried is listed).
 (C) oracle coefficients, diagnostic only: which exported fields carry the gold-vs-decoy direction."""
import json, sys
import numpy as np
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2")
from pu_lib import fit_logistic, predict, Scaler, auc, prec_at
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2"
src = open(f"{OUT}/test_02_pu.py").read().split("log = []")[0]
exec(compile(src, "test_02_head", "exec"))
other = cls == "other"; snap = state == "snapshot"
log = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)
nrw_f = n_rw[isUf].astype(float)

def report(sc, tag, with_budget=True):
    gd = (gold | decoy)[isUf]
    aucs = [auc(sc[(seed[isUf] == s) & gd], gold[isUf][(seed[isUf] == s) & gd]) for s in range(6)]
    T = np.zeros((2, 3)); K_tot = np.zeros(3); n_keys = 0
    for s in range(6):
        m = seed[isUf] == s; lab = [gold[isUf][m], decoy[isUf][m], other[isUf][m]]
        for j, N in enumerate((20, 50)): T[j] += [prec_at(sc[m], l, N) for l in lab]
        K = int((nrw_f[m] >= 11).sum()); n_keys += K; K_tot += [prec_at(sc[m], l, K) for l in lab]
    say(f"{tag:44s} AUC(g|d) pooled {auc(sc[gd], gold[isUf][gd]):.3f} per-seed [{' '.join(f'{x:.2f}' for x in aucs)}] spread {min(aucs):.2f}-{max(aucs):.2f}"
        f" | top20x6 gold {T[0,0]:.0f} decoy {T[0,1]:.0f} | top50x6 gold {T[1,0]:.0f} decoy {T[1,1]:.0f}"
        f" | wave-0 budget ({n_keys}) gold {K_tot[0]:.0f} decoy {K_tot[1]:.0f}")
    return aucs

say("baseline n_rw:"); report(nrw_f, "BASELINE n_rw")
say("reference: wave-0 >=11 cut on 736 keys = gold 99 / decoy 157; PU-buckets LOSO (test_02/03) AUC 0.643, top20x6 41/40, top50x6 68/116\n")

say("== (A) PN transfer from the system's own outcomes (found vs asked-not-found on the snapshot pool), LOSO by seed ==")
Pm_all = snap & found; Nm_all = snap & ~found
say(f"P = asked & found: {Pm_all.sum()}  N = asked & not found: {Nm_all.sum()} (contains {int((Nm_all & gold).sum())} gold the system missed, {int((Nm_all & decoy).sum())} decoys, {int((Nm_all & other).sum())} other)")
pn = {}
for fs in ("rw_only", "shape", "shape+ent", "buckets", "ALL"):
    names = FEATURE_SETS[fs]; sc = np.zeros(isUf.sum())
    for s in range(6):
        tr = snap & (seed != s); te = isUf & (seed == s)
        trr = [r for r, m in zip(rows, tr) if m]; ter = [r for r, m in zip(rows, te) if m]
        sc_ = Scaler().fit(mat(trr, names)); beta = fit_logistic(sc_.t(mat(trr, names)), found[tr].astype(float), lam=1.0)
        sc[seed[isUf] == s] = predict(beta, sc_.t(mat(ter, names)))
    pn[fs] = sc; report(sc, f"PN-transfer found-vs-notfound / {fs}")
# in-sample sanity on the snapshot pool itself: can found be told from not-found there at all?
names = FEATURE_SETS["buckets"]; trr = [r for r, m in zip(rows, snap) if m]; sc_ = Scaler().fit(mat(trr, names))
beta = fit_logistic(sc_.t(mat(trr, names)), found[snap].astype(float), lam=1.0); p = predict(beta, sc_.t(mat(trr, names)))
say(f"   (snapshot pool, in-sample, buckets: AUC found-vs-notfound {auc(p, found[snap]):.3f}; gold-vs-decoy among snapshot keys {auc(p[(gold|decoy)[snap]], gold[snap][(gold|decoy)[snap]]):.3f})")

say("\n== (B) single exported statistics, no fitting (all tried are listed; multiplicity = number of lines) ==")
def per_week(key_rows): return [np.asarray(buckets[r['seed']][r['key']]) for r in key_rows]
Uf_rows = [r for r, m in zip(rows, isUf) if m]; PW = per_week(Uf_rows)
stats = {
    "max_roots_lb":            lambda pw: pw[:, 5].max(),
    "max_users_lb":            lambda pw: pw[:, 4].max(),
    "max(roots-users)":        lambda pw: (pw[:, 5] - pw[:, 4]).max(),
    "max roots/(users+1)":     lambda pw: (pw[:, 5] / (pw[:, 4] + 1)).max(),
    "sum(roots-users) over wks": lambda pw: (pw[:, 5] - pw[:, 4]).sum(),
    "wk_ge1k":                 lambda pw: (pw[:, 0] >= 1).sum(),
    "wk_ge2k":                 lambda pw: (pw[:, 0] >= 2).sum(),
    "wk_n1":                   lambda pw: ((pw[:, 0] >= 2) | ((pw[:, 0] >= 1) & (pw[:, 1] >= 1))).sum(),
    "max_ksites":              lambda pw: pw[:, 0].max(),
    "max_site_bucket":         lambda pw: pw[:, 7].max(),
    "max_sumlb":               lambda pw: pw[:, 3].max(),
    "max_nsites":              lambda pw: pw[:, 8].max(),
}
for name, f in stats.items():
    sc = np.array([f(pw) for pw in PW], float); report(sc, f"s = {name}")

say("\n== (C) ORACLE coefficients (labels used to fit; diagnostic only): supervised gold-vs-decoy on U_f, all six seeds, buckets ==")
gd = isUf & (gold | decoy); names = FEATURE_SETS["buckets"]; trr = [r for r, m in zip(rows, gd) if m]
sc_ = Scaler().fit(mat(trr, names)); beta = fit_logistic(sc_.t(mat(trr, names)), gold[gd].astype(float), lam=1.0)
for n, b in sorted(zip(["intercept"] + names, beta), key=lambda t: -abs(t[1]))[:12]: say(f"   {n:18s} {b:+.3f}")
# PU direction vs oracle direction: cosine between standardized coefficient vectors (buckets)
Pm = P_DEFS["snap_gold"]; tr = Pm | isUf; trr2 = [r for r, m in zip(rows, tr) if m]; sc2 = Scaler().fit(mat(trr2, names))
beta_pu = fit_logistic(sc2.t(mat(trr2, names)), Pm[tr].astype(float), lam=1.0)
trr3 = [r for r, m in zip(rows, snap) if m]; sc3 = Scaler().fit(mat(trr3, names))
beta_pn = fit_logistic(sc3.t(mat(trr3, names)), found[snap].astype(float), lam=1.0)
cos = lambda a, b: float(a[1:] @ b[1:] / (np.linalg.norm(a[1:]) * np.linalg.norm(b[1:])))
say(f"   cosine(PU direction, oracle gold-vs-decoy direction) = {cos(beta_pu, beta):+.3f};  cosine(PN-transfer direction, oracle) = {cos(beta_pn, beta):+.3f}")
open(f"{OUT}/results_resurrect.txt", "w").write("\n".join(log) + "\n")
