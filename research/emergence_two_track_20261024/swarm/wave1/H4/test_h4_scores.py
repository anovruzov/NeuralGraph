"""H4 scoring (free: reads OUT/h4_cells.json only). Every score is computed from the exported pre-question view b
(per-site weekly cells, k-suppressed) -- never from a gold label, a node id, a file path or value equality.
Label-fitted variants (S_IG, S_Yield) are fitted LEAVE-ONE-SEED-OUT and the label enters the FIT on five seeds, never
the score of the sixth; they are reported separately from the label-free ones. Labels score; labels never select."""
import json, math, hashlib, random, os, sys
from collections import defaultdict, Counter

OUT = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(OUT, "h4_cells.json")))
K, WIN, TIE_SALT = 3, 6, "e2e-ingest-v1"
SEEDS = [str(s) for s in range(6)]

def lbub(v):  # a k-suppressed weekly cell: 0 absent, -1 = '<k', else the count
    return (0, 0) if v == 0 else ((1, K - 1) if v == -1 else (v, v))

def b_features(cells, w):
    """The exported pre-question view of one key at week w (window w-5..w), summarised. All from cells."""
    per = {}
    for s, c in cells.items():
        lb = ub = 0; present = False; newest = -1; roots = rep = 0
        for j in range(w - WIN + 1, w + 1):
            v = c["n"][j]
            if v == 0: continue
            a, b = lbub(v); lb += a; ub += b; present = True; newest = j
            roots = max(roots, lbub(c["roots"][j])[0]); rep = max(rep, lbub(c["rep"][j])[0])
        if present: per[s] = (lb, ub, newest, roots, rep)
    nP = len(per); cert = [v for v in per.values() if v[0] >= K]
    nC = len(cert); nU = sum(1 for v in per.values() if v[0] < K <= v[1]); nW = nP - nC - nU
    roots_cert = sum(v[3] for v in cert if v[3] >= K)        # N2: roots only from sites certainly at k roots
    rep_lb = sum(v[4] for v in cert)
    fresh = any(w - v[2] <= 3 for v in cert)                  # age = 7(w-j)+20 <= 42 days
    sumlb = sum(v[0] for v in per.values())
    return dict(nP=nP, nC=nC, nU=nU, nW=nW, roots=roots_cert, rep=rep_lb, fresh=int(fresh), sumlb=sumlb,
                count_cert=int(nC >= 2 or (nC >= 1 and nP >= 2)), count_poss=int(nC + nU >= 1 and nP >= 2))

def cls_of(f):   # the b-class the empirical P(clear|b) is tabulated on (coarse on purpose; ~1k cells)
    return (min(f["nC"], 2), min(f["nU"], 2), min(f["nP"], 3), min(f["roots"], 3), min(f["rep"], 3), f["fresh"])

def h2(p):
    return 0.0 if p <= 0 or p >= 1 else -(p * math.log2(p) + (1 - p) * math.log2(1 - p))

def mutual_info(p_h, p_a_given_h):
    """I(H;A) for binary H and binary A from P(H=1) and (P(A=1|H=0), P(A=1|H=1))."""
    pa = (1 - p_h) * p_a_given_h[0] + p_h * p_a_given_h[1]
    return h2(pa) - ((1 - p_h) * h2(p_a_given_h[0]) + p_h * h2(p_a_given_h[1]))

# ---------------------------------------------------------------- per (key, week) rows
rows = []   # one per pool key per askable week (a week with any exported cell)
for s in SEEDS:
    S = D[s]; widx = {wk: i for i, wk in enumerate(S["weeks"])}
    for key, v in S["keys"].items():
        for wk, a in v["answer"].items():
            f = b_features(v["cells"], widx[wk])
            rows.append(dict(seed=s, key=key, wk=wk, cls=cls_of(f), f=f, tb=int(a["tb"]), tbK=int(a["tbK"]),
                             gold=int(v["class"] == "gold"), decoy=int(v["class"] == "decoy"),
                             rule=int(wk in v["rule_weeks"])))
print("rows (key,week)", len(rows))

def fit_tables(train_seeds):
    """Label-free: P(tb | cls). Label-fitted: P(gold | cls), P(tb | cls, gold), P(tb | cls, not gold). Laplace toward
    the global rate (alpha = 2 pseudo-counts)."""
    n = Counter(); c = Counter(); g = Counter(); cg = Counter(); ng = Counter(); cng = Counter(); dd = Counter(); cd = Counter()
    for r in rows:
        if r["seed"] not in train_seeds: continue
        n[r["cls"]] += 1; c[r["cls"]] += r["tb"]; g[r["cls"]] += r["gold"]; dd[r["cls"]] += r["decoy"]
        if r["decoy"]: cd[r["cls"]] += r["tb"]
        if r["gold"]: cg[r["cls"]] += r["tb"]
        else: ng[r["cls"]] += 1; cng[r["cls"]] += r["tb"]
    N = sum(n.values()); p0 = sum(c.values()) / N; g0 = sum(g.values()) / N; d0 = sum(dd.values()) / N
    A = 2.0
    def pclear(k): return (c[k] + A * p0) / (n[k] + A)
    def pgold(k): return (g[k] + A * g0) / (n[k] + A)
    def pdecoy(k): return (dd[k] + A * d0) / (n[k] + A)
    def pclear_h(k, h):
        if h: return (cg[k] + A * p0) / (g[k] + A)
        return (cng[k] + A * p0) / (ng[k] + A)
    def pclear_d(k): return (cd[k] + A * p0) / (dd[k] + A)
    pclear.extra = (pdecoy, pclear_d)
    return pclear, pgold, pclear_h

def salt(key): return hashlib.sha256(f"{TIE_SALT}|{key}".encode()).hexdigest()

# ---------------------------------------------------------------- per-key scores, LOSO
per_key = {}
for s in SEEDS:
    train = [t for t in SEEDS if t != s]
    pclear, pgold, pclear_h = fit_tables(train)
    S = D[s]
    for key, v in S["keys"].items():
        rr = [r for r in rows if r["seed"] == s and r["key"] == key]
        rule_rows = [r for r in rr if r["rule"]] or rr      # the system asks at a week its rule fired
        sc = dict(rule_weeks=v["rule_weeks_n"], salt=salt(key), cls=v["class"], state=v["state"], seed=s,
                  pattern=v["pattern"], rare=v["rare"])
        sc["S_cert"] = max((r["f"]["count_cert"] and r["f"]["roots"] >= 3 and r["f"]["rep"] >= 3 and r["f"]["fresh"])
                           for r in rule_rows)
        sc["S_poss"] = max(r["f"]["count_poss"] for r in rule_rows)
        sc["S_sumlb"] = max(r["f"]["sumlb"] for r in rule_rows)
        sc["S_Pclear"] = max(pclear(r["cls"]) for r in rule_rows)
        sc["S_ent"] = max(h2(pclear(r["cls"])) for r in rule_rows)
        # label-fitted (five other seeds), the two objectives of the brief
        sc["S_IG"] = max(mutual_info(pgold(r["cls"]), (pclear_h(r["cls"], 0), pclear_h(r["cls"], 1))) for r in rule_rows)
        sc["S_Yield"] = max(pclear_h(r["cls"], 1) * pgold(r["cls"]) for r in rule_rows)
        pdecoy, pclear_d = pclear.extra
        netf = lambda r: pclear_h(r["cls"], 1) * pgold(r["cls"]) - pclear_d(r["cls"]) * pdecoy(r["cls"])
        sc["S_Net"] = max(netf(r) for r in rule_rows)
        net3 = lambda r: pclear_h(r["cls"], 1) * pgold(r["cls"]) - pclear_h(r["cls"], 0) * (1 - pgold(r["cls"]))
        sc["S_Net3"] = max(net3(r) for r in rule_rows)
        best = max(rule_rows, key=lambda r: (net3(r), r["wk"])); sc["S_Net3_wk"] = best["wk"]; sc["S_Net3_tb"] = best["tb"]
        # label-free structural distillation of what the table learned (post hoc, no parameters): a THIN key --
        # exactly two sites present, exactly one of them certainly at k, roots and reporters certainly >= 3, fresh
        thin = lambda f: int(f["nP"] == 2 and f["nC"] == 1 and f["roots"] >= 3 and f["rep"] >= 3 and f["fresh"])
        sc["S_thin"] = max(thin(r["f"]) for r in rule_rows)
        best = max(rule_rows, key=lambda r: (thin(r["f"]), r["wk"])); sc["S_thin_wk"] = best["wk"]; sc["S_thin_tb"] = best["tb"]
        sc["cls_at_Pclear_wk"] = str(max(rule_rows, key=lambda r: (pclear(r["cls"]), r["wk"]))["cls"])
        best = max(rule_rows, key=lambda r: (netf(r), r["wk"])); sc["S_Net_wk"] = best["wk"]; sc["S_Net_tb"] = best["tb"]
        first = min(rule_rows, key=lambda r: r["wk"]); sc["first_wk"] = first["wk"]; sc["first_tb"] = first["tb"]
        sc["rule_rows"] = [(r["wk"], r["tb"]) for r in rule_rows]
        # the week each policy would ask at (argmax; ties -> latest), and what the answer would be there
        for name in ("S_Pclear", "S_IG", "S_Yield", "S_ent", "S_sumlb"):
            fn = {"S_Pclear": lambda r: pclear(r["cls"]), "S_ent": lambda r: h2(pclear(r["cls"])),
                  "S_IG": lambda r: mutual_info(pgold(r["cls"]), (pclear_h(r["cls"], 0), pclear_h(r["cls"], 1))),
                  "S_Yield": lambda r: pclear_h(r["cls"], 1) * pgold(r["cls"]), "S_sumlb": lambda r: r["f"]["sumlb"]}[name]
            best = max(rule_rows, key=lambda r: (fn(r), r["wk"]))
            sc[name + "_wk"] = best["wk"]; sc[name + "_tb"] = best["tb"]
        best = max(rule_rows, key=lambda r: r["wk"]); sc["latest_wk"] = best["wk"]; sc["latest_tb"] = best["tb"]
        sc["any_tb_rule"] = max(r["tb"] for r in rule_rows)
        sc["mean_tb_rule"] = sum(r["tb"] for r in rule_rows) / len(rule_rows)
        wins = [w for w in S["windows"].get(key, []) if w[2] == v["class"] and (v["class"] != "gold" or w[3] == v["pattern"])]
        sc["windows"] = [(w[0], w[1]) for w in wins]
        per_key[(s, key)] = sc

# per-fold fitted tables (what each LOSO fold learned), for inspection
folds = {}
for s in SEEDS:
    pclear, pgold, pclear_h = fit_tables([t for t in SEEDS if t != s]); pdecoy, pclear_d = pclear.extra
    seen = sorted({r["cls"] for r in rows if r["seed"] == s and r["rule"]})
    folds[s] = {str(k): dict(P_clear=round(pclear(k), 3), P_gold=round(pgold(k), 3), P_decoy=round(pdecoy(k), 3),
                             P_clear_gold=round(pclear_h(k, 1), 3), P_clear_notgold=round(pclear_h(k, 0), 3),
                             P_clear_decoy=round(pclear_d(k), 3),
                             net=round(pclear_h(k, 1) * pgold(k) - pclear_d(k) * pdecoy(k), 3),
                             net3=round(pclear_h(k, 1) * pgold(k) - pclear_h(k, 0) * (1 - pgold(k)), 3)) for k in seen}
json.dump(folds, open(os.path.join(OUT, "h4_fold_tables.json"), "w"), indent=1)

# ---------------------------------------------------------------- evaluation on the filtered pool
def auc(pos, neg):
    """Mann-Whitney AUC with ties = 1/2."""
    allv = sorted(set(pos) | set(neg)); rank = {}
    # average ranks
    vals = sorted([(x, 1) for x in pos] + [(x, 0) for x in neg])
    i = 0; rsum = 0.0
    while i < len(vals):
        j = i
        while j < len(vals) and vals[j][0] == vals[i][0]: j += 1
        avg = (i + 1 + j) / 2
        rsum += avg * sum(1 for t in vals[i:j] if t[1] == 1)
        i = j
    n1, n0 = len(pos), len(neg)
    return (rsum - n1 * (n1 + 1) / 2) / (n1 * n0)

def in_window(sc, wk):
    return any(lo <= wk <= hi for lo, hi in sc["windows"])

filt = {k: v for k, v in per_key.items() if v["state"] == "rule_only" and v["rule_weeks"] >= 7}
print("filtered pool", len(filt), Counter(v["cls"] for v in filt.values()))
SCORES = ["baseline", "S_cert", "S_poss", "S_sumlb", "S_Pclear", "S_ent", "S_IG", "S_Yield", "S_Net", "S_Net3", "S_thin"]

def order(items, name):
    # baseline: rule_weeks desc, then the salted hash (ordered_candidates' tie rule); others: score desc, then baseline
    if name == "baseline":
        return sorted(items, key=lambda kv: (-kv[1]["rule_weeks"], kv[1]["salt"]))
    return sorted(items, key=lambda kv: (-kv[1][name], -kv[1]["rule_weeks"], kv[1]["salt"]))

def strict_auc(items, name, neg_cls):
    """AUC of the full strict ordering (the salted tie-break makes every ranking strict, as the system's does)."""
    ordd = order(items, name); pos = []; neg = []
    for i, (k, v) in enumerate(ordd):
        (pos if v["cls"] == "gold" else neg if v["cls"] == neg_cls else []).append(-i)
    return auc(pos, neg)

report = {"auc": {}, "auc_per_seed": {}, "prec": {}, "null": {}}
items = list(filt.items())
for name in SCORES:
    report["auc"][name] = {"gold_vs_decoy": strict_auc(items, name, "decoy"), "gold_vs_other": strict_auc(items, name, "other"),
                           "gold_vs_nongold_tieshalf": auc([v[name] if name != "baseline" else v["rule_weeks"] for k, v in items if v["cls"] == "gold"],
                                                           [v[name] if name != "baseline" else v["rule_weeks"] for k, v in items if v["cls"] != "gold"]),
                           "gold_vs_decoy_tieshalf": auc([v[name] if name != "baseline" else v["rule_weeks"] for k, v in items if v["cls"] == "gold"],
                                                         [v[name] if name != "baseline" else v["rule_weeks"] for k, v in items if v["cls"] == "decoy"])}
    report["auc_per_seed"][name] = {}
    report["prec"][name] = {}
    for s in SEEDS:
        it = [(k, v) for k, v in items if v["seed"] == s]
        report["auc_per_seed"][name][s] = strict_auc(it, name, "decoy")
        ordd = order(it, name)
        for n in (20, 50):
            top = ordd[:n]
            report["prec"][name][f"{s}@{n}"] = (sum(v["cls"] == "gold" for k, v in top), sum(v["cls"] == "decoy" for k, v in top),
                                                sum(v["cls"] == "gold" and in_window(v, v["S_Pclear_wk"]) and v["S_Pclear_tb"] for k, v in top),
                                                sum(v["cls"] == "decoy" and in_window(v, v["S_Pclear_wk"]) and v["S_Pclear_tb"] for k, v in top))

# null: random strict orderings, same pool, gold-vs-decoy
rng = random.Random(4)
nulls = []
for _ in range(2000):
    for k, v in items: v["S_null"] = rng.random()
    nulls.append(strict_auc(items, "S_null", "decoy"))
nulls.sort(); report["null"] = {"p2.5": nulls[50], "p97.5": nulls[1950], "max": nulls[-1], "n": 2000}

# ---------------------------------------------------------------- the objective tables and the break-even
pclear_all, pgold_all, pclear_h_all = fit_tables(SEEDS)
tab = Counter(); tabc = Counter(); tabg = Counter(); tabgc = Counter(); tabd = Counter(); tabdc = Counter()
for r in rows:
    if not r["rule"]: continue
    tab[r["cls"]] += 1; tabc[r["cls"]] += r["tb"]; tabg[r["cls"]] += r["gold"]; tabgc[r["cls"]] += r["gold"] * r["tb"]
    tabd[r["cls"]] += r["decoy"]; tabdc[r["cls"]] += r["decoy"] * r["tb"]
objective_rows = []
for k, n in tab.most_common():
    pc = tabc[k] / n; pg = tabg[k] / n; pgc = tabgc[k] / max(1, tabc[k])
    pc_g = tabgc[k] / max(1, tabg[k]); pc_ng = (tabc[k] - tabgc[k]) / max(1, n - tabg[k])
    objective_rows.append(dict(cls=k, n=n, P_clear=round(pc, 3), P_gold=round(pg, 3), P_gold_given_clear=round(pgc, 3),
                               P_clear_given_gold=round(pc_g, 3), P_clear_given_notgold=round(pc_ng, 3),
                               IG_bits=round(mutual_info(pg, (pc_ng, pc_g)), 4), yield_=round(pg * pc_g, 4),
                               ans_entropy=round(h2(pc), 3), decoys=tabd[k], decoys_clear=tabdc[k]))

# break-even: ask each filtered key once, at the week the policy picks; a corrected verdict needs clear AND in-window
be = {}
def expected_random_week(v):
    """asking at a rule week drawn uniformly: P(clear and in-window)"""
    return sum(tb and in_window(v, wk) for wk, tb in v["rule_rows"]) / len(v["rule_rows"])
for name in ("latest", "first", "random_week", "any_week", "S_Pclear", "S_Yield", "S_Net", "S_Net3", "S_thin", "S_IG", "S_ent"):
    if name in ("random_week", "any_week"):
        f_ = expected_random_week if name == "random_week" else (lambda v: float(any(tb and in_window(v, wk) for wk, tb in v["rule_rows"])))
        g = sum(f_(v) for k, v in items if v["cls"] == "gold"); d = sum(f_(v) for k, v in items if v["cls"] == "decoy")
        o = sum((sum(tb for wk, tb in v["rule_rows"]) / len(v["rule_rows"])) if name == "random_week" else float(any(tb for wk, tb in v["rule_rows"])) for k, v in items if v["cls"] == "other")
        be[name] = dict(questions=len(items), gold_corrected=round(g, 1), decoys_accepted=round(d, 1), unplanted_supported=round(o, 1),
                        yield_per_question=round(g / len(items), 4), one_in=round(len(items) / max(1e-9, g), 1))
        ro_ = [(k, v) for k, v in per_key.items() if v["state"] == "rule_only"]
        g2 = sum(f_(v) for k, v in ro_ if v["cls"] == "gold")
        be[name]["whole_rule_only"] = dict(questions=len(ro_), gold_corrected=round(g2, 1), one_in=round(len(ro_) / max(1e-9, g2), 1))
        continue
    wkf = (lambda v: v[name + "_wk"]) if name.startswith("S_") else (lambda v: v[name + "_wk"])
    tbf = (lambda v: v[name + "_tb"]) if name.startswith("S_") else (lambda v: v[name + "_tb"])
    g = sum(1 for k, v in items if v["cls"] == "gold" and tbf(v) and in_window(v, wkf(v)))
    d = sum(1 for k, v in items if v["cls"] == "decoy" and tbf(v) and in_window(v, wkf(v)))
    o = sum(1 for k, v in items if v["cls"] == "other" and tbf(v))
    be[name] = dict(questions=len(items), gold_corrected=g, decoys_accepted=d, unplanted_supported=o,
                    yield_per_question=round(g / len(items), 4), one_in=round(len(items) / max(1, g), 1))
    # top-k of this ranking, per seed, budget 60 / 20
    for n in (20, 60):
        gg = dd = oo = 0
        for s in SEEDS:
            it = order([(k, v) for k, v in items if v["seed"] == s], name if name.startswith("S_") else "baseline")[:n]
            gg += sum(1 for k, v in it if v["cls"] == "gold" and tbf(v) and in_window(v, wkf(v)))
            dd += sum(1 for k, v in it if v["cls"] == "decoy" and tbf(v) and in_window(v, wkf(v)))
            oo += sum(1 for k, v in it if v["cls"] == "other" and tbf(v))
        be[name][f"top{n}_6seeds"] = dict(questions=6 * n, gold_corrected=gg, decoys_accepted=dd, unplanted_supported=oo,
                                          yield_per_question=round(gg / (6 * n), 4))
# whole rule-only pool at fixed week policies (the "1 pattern per 220 questions" calibration)
ro = [(k, v) for k, v in per_key.items() if v["state"] == "rule_only"]
for pol in ("latest", "first", "S_Pclear"):
    g = sum(1 for k, v in ro if v["cls"] == "gold" and v[pol + "_tb"] and in_window(v, v[pol + "_wk"]))
    be["whole_rule_only_" + pol] = dict(questions=len(ro), gold_corrected=g, one_in=round(len(ro) / max(1, g), 1),
                                        decoys_accepted=sum(1 for k, v in ro if v["cls"] == "decoy" and v[pol + "_tb"] and in_window(v, v[pol + "_wk"])),
                                        unplanted_supported=sum(1 for k, v in ro if v["cls"] == "other" and v[pol + "_tb"]))
# clearable ceilings inside the filtered pool
be["filtered_ceiling"] = dict(gold_clearable_at_some_rule_week_in_window=sum(1 for k, v in items if v["cls"] == "gold" and any(tb and in_window(v, wk) for wk, tb in v["rule_rows"])),
                              gold=sum(1 for k, v in items if v["cls"] == "gold"),
                              decoy_clearable=sum(1 for k, v in items if v["cls"] == "decoy" and any(tb and in_window(v, wk) for wk, tb in v["rule_rows"])),
                              decoy=sum(1 for k, v in items if v["cls"] == "decoy"),
                              other_clearable=sum(1 for k, v in items if v["cls"] == "other" and any(tb for wk, tb in v["rule_rows"])),
                              other=sum(1 for k, v in items if v["cls"] == "other"))
report["objective_table"] = objective_rows
report["break_even"] = be
json.dump(report, open(os.path.join(OUT, "h4_report.json"), "w"), indent=1, default=str)
json.dump({f"{s}|{k}": v for (s, k), v in per_key.items()}, open(os.path.join(OUT, "h4_per_key_scores.json"), "w"), default=str)

# ---------------------------------------------------------------- print
print("\nAUC on the filtered pool (rule_only, rule_weeks>=7; 138 gold / 317 decoy / 1310 other); strict salted orderings")
print(f"{'score':10s} {'g-vs-decoy':>10s} {'g-vs-other':>10s} {'g-vs-decoy(ties=.5)':>20s} {'g-vs-nongold(ties=.5)':>22s}   per-seed g-vs-decoy")
for name in SCORES:
    a = report["auc"][name]; ps = report["auc_per_seed"][name]
    print(f"{name:10s} {a['gold_vs_decoy']:10.3f} {a['gold_vs_other']:10.3f} {a['gold_vs_decoy_tieshalf']:20.3f} {a['gold_vs_nongold_tieshalf']:22.3f}   "
          + " ".join(f"{ps[s]:.3f}" for s in SEEDS) + f"  spread {min(ps.values()):.3f}-{max(ps.values()):.3f}")
print("null 95% band (random strict order, 2000 draws):", round(report['null']['p2.5'], 3), round(report['null']['p97.5'], 3), "max", round(report['null']['max'], 3))
print("\nprecision@20 / @50 per seed: (gold, decoy, gold corrected, decoy accepted) -- every ranking asks at the label-free week argmax P(clear|b)")
for name in SCORES:
    p = report["prec"][name]
    for n in (20, 50):
        print(f"{name if n == 20 else '':10s} @{n} " + " ".join(f"{p[f'{s}@{n}']}" for s in SEEDS) + f"  sum gold {sum(p[f'{s}@{n}'][0] for s in SEEDS)} decoy {sum(p[f'{s}@{n}'][1] for s in SEEDS)} corrected {sum(p[f'{s}@{n}'][2] for s in SEEDS)} decoy_acc {sum(p[f'{s}@{n}'][3] for s in SEEDS)}")
print("\nobjective table over (key, rule-week) rows, b-class = (nC, nU, nP, roots_lb, rep_lb, fresh):")
print(f"{'cls':24s} {'n':>6s} {'P(clear)':>8s} {'P(gold)':>8s} {'P(g|clr)':>8s} {'P(c|g)':>7s} {'P(c|~g)':>7s} {'IG':>7s} {'yield':>7s} {'H(A)':>6s} {'decoy':>6s} {'d_clr':>6s}")
for o in objective_rows[:34]:
    print(f"{str(o['cls']):24s} {o['n']:6d} {o['P_clear']:8.3f} {o['P_gold']:8.3f} {o['P_gold_given_clear']:8.3f} {o['P_clear_given_gold']:7.3f} {o['P_clear_given_notgold']:7.3f} {o['IG_bits']:7.4f} {o['yield_']:7.4f} {o['ans_entropy']:6.3f} {o['decoys']:6d} {o['decoys_clear']:6d}")
print("\nbreak-even (ask once at the policy's week; corrected = clear AND inside the label window):")
for k, v in be.items(): print(" ", k, v)
