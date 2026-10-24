"""F-H4 shared code. Reproduces H4's definitions VERBATIM (test_h4_scores.py) so the re-run is the pre-registered
procedure and not a variant: b_features, cls_of, fit_tables (alpha = 2), strict salted ordering, Mann-Whitney AUC.
Reads swarm_out/H4/h4_cells.json (generator-rebuilt k-suppressed weekly cells + gate-model answers). Labels score only."""
import json, math, hashlib, os
from collections import Counter

SCRATCH = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad"
H4 = os.path.join(SCRATCH, "swarm_out", "H4")
R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
K, WIN, TIE_SALT = 3, 6, "e2e-ingest-v1"
SEEDS = [str(s) for s in range(6)]

def load_cells():
    return json.load(open(os.path.join(H4, "h4_cells.json")))

def lbub(v):
    return (0, 0) if v == 0 else ((1, K - 1) if v == -1 else (v, v))

def b_features(cells, w):
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
    roots_cert = sum(v[3] for v in cert if v[3] >= K)
    rep_lb = sum(v[4] for v in cert)
    fresh = any(w - v[2] <= 3 for v in cert)
    sumlb = sum(v[0] for v in per.values())
    return dict(nP=nP, nC=nC, nU=nU, nW=nW, roots=roots_cert, rep=rep_lb, fresh=int(fresh), sumlb=sumlb,
                count_cert=int(nC >= 2 or (nC >= 1 and nP >= 2)), count_poss=int(nC + nU >= 1 and nP >= 2))

def cls_of(f):
    return (min(f["nC"], 2), min(f["nU"], 2), min(f["nP"], 3), min(f["roots"], 3), min(f["rep"], 3), f["fresh"])

def build_rows(D):
    """one row per pool key per askable week (a week with any exported cell), exactly as H4"""
    rows = []
    for s in SEEDS:
        S = D[s]; widx = {wk: i for i, wk in enumerate(S["weeks"])}
        for key, v in S["keys"].items():
            for wk, a in v["answer"].items():
                f = b_features(v["cells"], widx[wk])
                rows.append(dict(seed=s, key=key, wk=wk, cls=cls_of(f), f=f, tb=int(a["tb"]),
                                 gold=int(v["class"] == "gold"), decoy=int(v["class"] == "decoy"),
                                 rule=int(wk in v["rule_weeks"])))
    return rows

def fit_tables(rows, train_seeds, alpha=2.0, gold_of=None, decoy_of=None):
    """H4's fit. gold_of/decoy_of: optional overrides (key -> 0/1) for label permutation / flip tests."""
    n = Counter(); c = Counter(); g = Counter(); cg = Counter(); ng = Counter(); cng = Counter(); dd = Counter(); cd = Counter()
    for r in rows:
        if r["seed"] not in train_seeds: continue
        gl = r["gold"] if gold_of is None else gold_of[(r["seed"], r["key"])]
        dl = r["decoy"] if decoy_of is None else decoy_of[(r["seed"], r["key"])]
        n[r["cls"]] += 1; c[r["cls"]] += r["tb"]; g[r["cls"]] += gl; dd[r["cls"]] += dl
        if dl: cd[r["cls"]] += r["tb"]
        if gl: cg[r["cls"]] += r["tb"]
        else: ng[r["cls"]] += 1; cng[r["cls"]] += r["tb"]
    N = sum(n.values()); p0 = sum(c.values()) / N; g0 = sum(g.values()) / N; d0 = sum(dd.values()) / N
    A = alpha
    T = dict(n=n, c=c, g=g, cg=cg, ng=ng, cng=cng, dd=dd, cd=cd, p0=p0, g0=g0, d0=d0, A=A)
    def pclear(k): return (c[k] + A * p0) / (n[k] + A)
    def pgold(k): return (g[k] + A * g0) / (n[k] + A)
    def pdecoy(k): return (dd[k] + A * d0) / (n[k] + A)
    def pclear_h(k, h):
        if h: return (cg[k] + A * p0) / (g[k] + A)
        return (cng[k] + A * p0) / (ng[k] + A)
    def pclear_d(k): return (cd[k] + A * p0) / (dd[k] + A)
    def net3(k): return pclear_h(k, 1) * pgold(k) - pclear_h(k, 0) * (1 - pgold(k))      # nu = (1, 1)
    def net(k): return pclear_h(k, 1) * pgold(k) - pclear_d(k) * pdecoy(k)                # nu = (1, 0)
    return dict(pclear=pclear, pgold=pgold, pdecoy=pdecoy, pclear_h=pclear_h, pclear_d=pclear_d, net3=net3, net=net, T=T)

def salt(key): return hashlib.sha256(f"{TIE_SALT}|{key}".encode()).hexdigest()

def auc(pos, neg):
    """Mann-Whitney AUC, ties = 1/2."""
    vals = sorted([(x, 1) for x in pos] + [(x, 0) for x in neg])
    i = 0; rsum = 0.0
    while i < len(vals):
        j = i
        while j < len(vals) and vals[j][0] == vals[i][0]: j += 1
        avg = (i + 1 + j) / 2
        rsum += avg * sum(1 for t in vals[i:j] if t[1] == 1)
        i = j
    n1, n0 = len(pos), len(neg)
    return (rsum - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else float("nan")

def order(items, name):
    """H4's strict salted ordering: score desc, rule_weeks desc, salted hash (baseline: rule_weeks desc, hash)."""
    if name == "baseline":
        return sorted(items, key=lambda kv: (-kv[1]["rule_weeks"], kv[1]["salt"]))
    return sorted(items, key=lambda kv: (-kv[1][name], -kv[1]["rule_weeks"], kv[1]["salt"]))

def strict_auc(items, name, neg_cls="decoy"):
    ordd = order(items, name); pos = []; neg = []
    for i, (k, v) in enumerate(ordd):
        (pos if v["cls"] == "gold" else neg if v["cls"] == neg_cls else []).append(-i)
    return auc(pos, neg)

def score_keys(D, rows, train_for, gold_of=None, decoy_of=None):
    """Per-key scores under a fit chosen per scored seed: train_for(seed) -> list of training seeds.
    Returns {(seed,key): dict(S_Net3, S_Net, S_Pclear, S_cert, S_sumlb, rule_weeks, salt, cls, state, *_wk, *_tb, windows)}"""
    by_sk = {}
    for r in rows: by_sk.setdefault((r["seed"], r["key"]), []).append(r)
    per_key = {}
    for s in SEEDS:
        F = fit_tables(rows, train_for(s), gold_of=gold_of, decoy_of=decoy_of)
        S = D[s]
        for key, v in S["keys"].items():
            rr = by_sk.get((s, key), [])
            if not rr: continue
            rule_rows = [r for r in rr if r["rule"]] or rr
            sc = dict(rule_weeks=v["rule_weeks_n"], salt=salt(key), cls=v["class"], state=v["state"], seed=s,
                      pattern=v["pattern"], rare=v["rare"], n_rule_rows=len(rule_rows))
            sc["S_Net3"] = max(F["net3"](r["cls"]) for r in rule_rows)
            sc["S_Net"] = max(F["net"](r["cls"]) for r in rule_rows)
            sc["S_Pclear"] = max(F["pclear"](r["cls"]) for r in rule_rows)
            sc["S_cert"] = max((r["f"]["count_cert"] and r["f"]["roots"] >= 3 and r["f"]["rep"] >= 3 and r["f"]["fresh"]) for r in rule_rows)
            sc["S_sumlb"] = max(r["f"]["sumlb"] for r in rule_rows)
            for name, fn in (("S_Net3", F["net3"]), ("S_Net", F["net"]), ("S_Pclear", F["pclear"])):
                best = max(rule_rows, key=lambda r: (fn(r["cls"]), r["wk"])); sc[name + "_wk"] = best["wk"]; sc[name + "_tb"] = best["tb"]; sc[name + "_cls"] = best["cls"]
            first = min(rule_rows, key=lambda r: r["wk"]); sc["first_wk"] = first["wk"]; sc["first_tb"] = first["tb"]
            latest = max(rule_rows, key=lambda r: r["wk"]); sc["latest_wk"] = latest["wk"]; sc["latest_tb"] = latest["tb"]
            sc["rule_rows"] = [(r["wk"], r["tb"], r["cls"], r["f"]) for r in rule_rows]
            wins = [w for w in S["windows"].get(key, []) if w[2] == v["class"] and (v["class"] != "gold" or w[3] == v["pattern"])]
            sc["windows"] = [(w[0], w[1]) for w in wins]
            per_key[(s, key)] = sc
    return per_key

def in_window(sc, wk):
    return any(lo <= wk <= hi for lo, hi in sc["windows"])

def filtered(per_key):
    return {k: v for k, v in per_key.items() if v["state"] == "rule_only" and v["rule_weeks"] >= 7}

def loso(s): return [t for t in SEEDS if t != s]
