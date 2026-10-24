"""H2 / step 2: positive-unlabeled analysis.
  P (known positives) = snapshot-pool gold keys (the system's own asked-and-found set; permitted by the family definition).
  U (unlabeled)       = the rule-only pool. Its labels are read ONLY to score, never to fit.
(1) class prior in U by Elkan-Noto (out-of-fold g) and by binned mixture-proportion estimation, vs measured 0.049 / 0.078.
(2) PU ranker g(x)=P(s=1|x) (L2 logistic, lam=1, fixed) on exported features; EN two-stage variant.
(3) separation gold-vs-decoy on the FILTERED rule-only pool (n_rw>6), vs the rule_weeks-only baseline:
    pooled AUC, per-seed expected precision@20/@50 (gold and decoy counts, random tie-breaking), leave-one-seed-out spread.
Pre-registered primary: P=snapshot gold, features=ALL (rule-week shape + entity siblings + per-site bucket shape), model=g.
Everything else in the grid is reported as a variant and counted."""
import json, sys, itertools
import numpy as np
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2")
from pu_lib import fit_logistic, predict, Scaler, auc, prec_at, cv_scores, elkan_noto, mpe_binned
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2"
rows = json.load(open(f"{OUT}/features_base.json"))
buckets = {s: json.load(open(f"{OUT}/buckets_seed-{s}.json")) for s in range(6)}
PREDS = ["chain_supply", "chain_auth", "chain_policy", "chain_attrition", "chain_firmware", "chain_pricing", "chain_network", "revenue_miss"]

def bucket_feats(pw):
    pw = np.asarray(pw); ks, weak, ksb, sumlb, users, roots, contra, maxb, nsites, newest = pw.T
    active = nsites > 0
    n_act = int(active.sum())
    wk1 = int((ks >= 1).sum()); wk2 = int((ks >= 2).sum()); wkn1 = int(((ks >= 2) | ((ks >= 1) & (weak >= 1))).sum())
    wkb2 = int((ksb >= 2).sum())
    idx1 = np.where(ks >= 1)[0]
    first1 = int(idx1[0]) + 1 if len(idx1) else 0; last1 = int(idx1[-1]) + 1 if len(idx1) else 0
    s_act = sumlb[active] if n_act else np.zeros(1)
    return [wk1, wk2, wkn1, wkb2, int(ks.max()), int(sumlb.max()), float(s_act.mean()), float(s_act.std()),
            n_act, int(maxb.max()), int(users.max()), int(roots.max()), int((contra >= 1).sum()), int(nsites.max()),
            first1, last1, (last1 - first1 + 1) if len(idx1) else 0, (wk1 / n_act) if n_act else 0.0,
            int((weak >= 1).sum()), float((weak[active]).mean()) if n_act else 0.0]
BUCKET_NAMES = ["wk_ge1k", "wk_ge2k", "wk_n1", "wk_blind_ge2k", "max_ksites", "max_sumlb", "mean_sumlb", "std_sumlb", "n_active_wk",
                "max_site_bucket", "max_users_lb", "max_roots_lb", "contra_wks", "max_nsites", "first_k_wk", "last_k_wk", "k_span",
                "k_frac_active", "weak_wks", "mean_weak"]
SHAPE = ["n_rw", "first_rw", "last_rw", "span", "density", "n_runs", "max_run", "n_gaps", "recency", "rw_last6", "rw_first_half"]
ENT = ["n_sib", "sib_rw"]
for r in rows:
    bf = bucket_feats(buckets[r["seed"]][r["key"]])
    for n, v in zip(BUCKET_NAMES, bf): r[n] = v
    for p in PREDS: r["pred_" + p] = 1.0 if r["pred"] == p else 0.0
FEATURE_SETS = {
    "rw_only": ["n_rw"],
    "shape": SHAPE,
    "shape+ent": SHAPE + ENT,
    "shape+ent+pred": SHAPE + ENT + ["pred_" + p for p in PREDS],
    "buckets": BUCKET_NAMES,
    "ALL": SHAPE + ENT + BUCKET_NAMES,
}
def mat(rs, names): return np.array([[float(r[n]) for n in names] for r in rs])
seed = np.array([r["seed"] for r in rows]); state = np.array([r["state"] for r in rows]); cls = np.array([r["cls"] for r in rows])
found = np.array([r["found"] for r in rows]); n_rw = np.array([r["n_rw"] for r in rows])
isU = state == "rule_only"; isUf = isU & (n_rw > 6)
P_DEFS = {"snap_gold": (state == "snapshot") & (cls == "gold"), "snap_gold_found": (state == "snapshot") & (cls == "gold") & found}
gold = cls == "gold"; decoy = cls == "decoy"
log = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)

say(f"pool rows {len(rows)}  U {isU.sum()}  U_filtered(n_rw>6) {isUf.sum()}  gold in U {int((isU & gold).sum())} ({(isU & gold).sum()/isU.sum():.3f})"
    f"  gold in U_f {int((isUf & gold).sum())} ({(isUf & gold).sum()/isUf.sum():.3f})  decoy in U_f {int((isUf & decoy).sum())}")
for k, m in P_DEFS.items(): say(f"P '{k}': {m.sum()} keys")

# ------------------------------------------------------------------ (1) class prior
say("\n== (1) CLASS PRIOR IN U — Elkan-Noto, out-of-fold g (5-fold), P = snapshot gold ==")
say(f"{'features':16s} {'pool':6s} {'c=E[g|P]':>9s} {'pi_global':>9s} {'pi_sum':>7s}  target")
prior_tab = {}
for fs, names in FEATURE_SETS.items():
    for pool_name, Um, target in (("full", isU, 0.049), ("filt", isUf, 0.078)):
        Pm = P_DEFS["snap_gold"]; sel = Pm | Um
        X = mat([r for r, m in zip(rows, sel) if m], names); y = Pm[sel].astype(float)
        g = cv_scores(X, y, lam=1.0, k=5, seed=0)
        c, pg, ps, _ = elkan_noto(g, y.astype(bool))
        prior_tab[(fs, pool_name)] = (c, pg, ps)
        say(f"{fs:16s} {pool_name:6s} {c:9.3f} {pg:9.3f} {ps:7.3f}  {target}")
say("\n-- mixture-proportion estimate (min over bins of p_U/p_P), P = snapshot gold --")
P1 = P_DEFS["snap_gold"]
for pool_name, Um in (("full", isU), ("filt", isUf)):
    pi, tab = mpe_binned(n_rw[P1], n_rw[Um], bins=np.array([0, 7, 9, 11, 13, 15, 17, 19, 27]), min_p=5)
    say(f"MPE on n_rw, pool {pool_name}: pi_hat = {pi:.3f}   bins(lo, nP, nU, pU/pP): " + "; ".join(f"{int(lo)}:{int(a)}/{int(b)}/{c:.2f}" for lo, a, b, c in tab))
    # on the ALL-feature out-of-fold score, decile bins of P's score
    sel = P1 | Um; X = mat([r for r, m in zip(rows, sel) if m], FEATURE_SETS["ALL"]); y = P1[sel].astype(float)
    g = cv_scores(X, y, lam=1.0, k=5, seed=0)
    qs = np.quantile(g[y == 1], [0, .2, .4, .6, .8, 1.0]); qs[-1] += 1e-9; qs[0] = -1e-9
    pi2, tab2 = mpe_binned(g[y == 1], g[y == 0], bins=qs, min_p=5)
    say(f"MPE on ALL-feature g (P-quintile bins), pool {pool_name}: pi_hat = {pi2:.3f}   bins(nP/nU/pU:pP): " + "; ".join(f"{int(a)}/{int(b)}/{c:.2f}" for lo, a, b, c in tab2))

# ------------------------------------------------------------------ (2)+(3) ranker vs rule_weeks baseline
def fit_score(train_rows, train_y, test_rows, names, model):
    sc = Scaler().fit(mat(train_rows, names)); Xtr = sc.t(mat(train_rows, names)); Xte = sc.t(mat(test_rows, names))
    beta = fit_logistic(Xtr, train_y, lam=1.0)
    if model == "g": return predict(beta, Xte)
    # Elkan-Noto two-stage: c from in-sample g on P; U rows duplicated with weights w(x) as positive and 1-w(x) as negative
    g = predict(beta, Xtr); c = g[train_y == 1].mean(); P = train_y == 1
    gu = np.clip(g[~P], 1e-9, 1 - 1e-9); w = np.clip((1 - c) / c * gu / (1 - gu), 0, 1)
    X2 = np.vstack([Xtr[P], Xtr[~P], Xtr[~P]]); y2 = np.concatenate([np.ones(P.sum()), np.ones((~P).sum()), np.zeros((~P).sum())])
    w2 = np.concatenate([np.ones(P.sum()), w, 1 - w])
    return predict(fit_logistic(X2, y2, lam=1.0, w=w2), Xte)

def evaluate(score_f, Uf_mask):
    """score_f(rows_subset_mask)->scores for the filtered-U rows; returns pooled AUCs and per-seed P@20/50 gold & decoy."""
    res = {}
    sc = score_f
    gd = Uf_mask & (gold | decoy)
    res["auc_gd"] = auc(sc[gd[Uf_mask]], gold[gd]) if gd.sum() else float("nan")
    res["auc_g_all"] = auc(sc, gold[Uf_mask])
    per = []
    for s in range(6):
        m = (seed[Uf_mask] == s)
        per.append([prec_at(sc[m], gold[Uf_mask][m], 20), prec_at(sc[m], decoy[Uf_mask][m], 20),
                    prec_at(sc[m], gold[Uf_mask][m], 50), prec_at(sc[m], decoy[Uf_mask][m], 50)])
    res["per_seed"] = np.array(per)
    return res

def fmt(res, tag):
    p = res["per_seed"]
    return (f"{tag:34s} AUC(g|d)={res['auc_gd']:.3f} AUC(g|all)={res['auc_g_all']:.3f} | top20 gold {p[:,0].sum():5.1f} decoy {p[:,1].sum():5.1f}"
            f" | top50 gold {p[:,2].sum():5.1f} decoy {p[:,3].sum():5.1f} | per-seed gold@20 [{' '.join(f'{x:.1f}' for x in p[:,0])}] gold@50 [{' '.join(f'{x:.1f}' for x in p[:,2])}]")

say("\n== (3) FILTERED RULE-ONLY POOL (n_rw>6): gold vs decoy, ranker vs rule_weeks baseline ==")
say("counts are expected gold/decoy keys in the top-N per seed under random tie-breaking, summed over six seeds (6 x N keys asked)")
Uf_rows = [r for r, m in zip(rows, isUf) if m]
base = evaluate(n_rw[isUf].astype(float), isUf); say(fmt(base, "BASELINE rule_weeks (n_rw)"))
# a second baseline: n_rw with the dumps' own natural tie-break (max_run) - also label-free
base2 = evaluate(n_rw[isUf] + 0.01 * np.array([r["max_run"] for r in Uf_rows]), isUf); say(fmt(base2, "BASELINE n_rw, tie-break max_run"))
say("--- random-ordering reference (expected): gold@20 per seed = 20*rate, gold@50 = 50*rate")
for s in range(6):
    m = isUf & (seed == s); say(f"   seed {s}: U_f {m.sum()} gold {int((m & gold).sum())} decoy {int((m & decoy).sum())} -> random gold@20 {20*(m & gold).sum()/m.sum():.1f} decoy@20 {20*(m & decoy).sum()/m.sum():.1f} gold@50 {50*(m & gold).sum()/m.sum():.1f}")

variants = 0; results = {}
say("\n-- IN-SAMPLE (fit on P + U_f of all six seeds, score U_f) --")
for pdef, fs, model in itertools.product(P_DEFS, FEATURE_SETS, ("g", "en2")):
    if fs == "rw_only" and model == "en2": continue
    Pm = P_DEFS[pdef]; tr = Pm | isUf
    sc = fit_score([r for r, m in zip(rows, tr) if m], Pm[tr].astype(float), Uf_rows, FEATURE_SETS[fs], model)
    res = evaluate(sc, isUf); results[("in", pdef, fs, model)] = res; variants += 1
    say(fmt(res, f"{pdef}/{fs}/{model}"))

say("\n-- LEAVE-ONE-SEED-OUT (fit on P + U_f of five seeds, score the sixth; six times) --")
loso = {}
for pdef, fs, model in itertools.product(P_DEFS, FEATURE_SETS, ("g", "en2")):
    if fs == "rw_only" and model == "en2": continue
    sc = np.zeros(isUf.sum())
    for s in range(6):
        Pm = P_DEFS[pdef] & (seed != s); tr = Pm | (isUf & (seed != s))
        te = isUf & (seed == s)
        sc[seed[isUf] == s] = fit_score([r for r, m in zip(rows, tr) if m], Pm[tr].astype(float), [r for r, m in zip(rows, te) if m], FEATURE_SETS[fs], model)
    res = evaluate(sc, isUf); loso[(pdef, fs, model)] = (res, sc)
    # per-seed AUC gold-vs-decoy for the spread
    aucs = []
    for s in range(6):
        m = (seed[isUf] == s) & (gold | decoy)[isUf]
        aucs.append(auc(sc[m], gold[isUf][m]))
    res["auc_gd_per_seed"] = np.array(aucs)
    say(fmt(res, f"LOSO {pdef}/{fs}/{model}") + f" | AUC(g|d) per seed [{' '.join(f'{x:.2f}' for x in aucs)}] spread {min(aucs):.2f}-{max(aucs):.2f}")
# baseline per-seed AUC
aucs = []
for s in range(6):
    m = (seed[isUf] == s) & (gold | decoy)[isUf]; aucs.append(auc(n_rw[isUf][m].astype(float), gold[isUf][m]))
say(f"BASELINE n_rw AUC(g|d) per seed [{' '.join(f'{x:.2f}' for x in aucs)}] spread {min(aucs):.2f}-{max(aucs):.2f}")
say(f"\nvariants fitted (feature set x P definition x model, lam fixed at 1.0, no tuning): {variants} in-sample + {variants} LOSO")

# ------------------------------------------------------------------ primary, with coefficients
say("\n== PRIMARY (pre-registered): P=snap_gold, features=ALL, model=g; LOSO coefficients averaged over the six fits ==")
names = FEATURE_SETS["ALL"]; betas = []
for s in range(6):
    Pm = P_DEFS["snap_gold"] & (seed != s); tr = Pm | (isUf & (seed != s))
    trr = [r for r, m in zip(rows, tr) if m]; sc_ = Scaler().fit(mat(trr, names))
    betas.append(fit_logistic(sc_.t(mat(trr, names)), Pm[tr].astype(float), lam=1.0))
B = np.array(betas)
for n, mu, sd in sorted(zip(["intercept"] + names, B.mean(0), B.std(0)), key=lambda t: -abs(t[1])):
    say(f"   {n:18s} {mu:+.3f} (sd {sd:.3f})")
# what the primary's top-20 per seed actually contains vs baseline (gold / decoy / other)
res, sc = loso[("snap_gold", "ALL", "g")]
say("\nPRIMARY LOSO top-20 and top-50 composition per seed (gold/decoy/other, expected under tie-break) vs baseline n_rw:")
other = cls == "other"
for s in range(6):
    m = seed[isUf] == s; lab = {"g": gold[isUf][m], "d": decoy[isUf][m], "o": other[isUf][m]}
    row = f"   seed {s}:"
    for N in (20, 50):
        row += f"  top{N} PU {prec_at(sc[m], lab['g'], N):.1f}/{prec_at(sc[m], lab['d'], N):.1f}/{prec_at(sc[m], lab['o'], N):.1f}" \
               f"  base {prec_at(n_rw[isUf][m].astype(float), lab['g'], N):.1f}/{prec_at(n_rw[isUf][m].astype(float), lab['d'], N):.1f}/{prec_at(n_rw[isUf][m].astype(float), lab['o'], N):.1f}"
    say(row)
# also: does the PU score beat n_rw AFTER controlling for n_rw? AUC gold-vs-decoy within n_rw strata (>=11 only, the wave-0 cut)
m11 = isUf & (n_rw >= 11) & (gold | decoy)
say(f"\nwithin n_rw>=11 (wave-0 cut) gold-vs-decoy AUC: PU-ALL LOSO {auc(sc[(n_rw[isUf] >= 11) & (gold | decoy)[isUf]], gold[m11]):.3f} vs n_rw {auc(n_rw[m11].astype(float), gold[m11]):.3f}  (n gold {int((m11 & gold).sum())}, decoy {int((m11 & decoy).sum())})")
json.dump({"prior": {f"{k[0]}|{k[1]}": v for k, v in prior_tab.items()},
           "loso": {"|".join(k): {"auc_gd": v[0]["auc_gd"], "auc_g_all": v[0]["auc_g_all"], "per_seed_g20_d20_g50_d50": v[0]["per_seed"].tolist(),
                                   "auc_gd_per_seed": v[0]["auc_gd_per_seed"].tolist()} for k, v in loso.items()},
           "baseline": {"auc_gd": base["auc_gd"], "auc_g_all": base["auc_g_all"], "per_seed_g20_d20_g50_d50": base["per_seed"].tolist(), "auc_gd_per_seed": aucs},
           "variants": variants}, open(f"{OUT}/results_pu.json", "w"), indent=1)
open(f"{OUT}/results_pu.txt", "w").write("\n".join(log) + "\n")
