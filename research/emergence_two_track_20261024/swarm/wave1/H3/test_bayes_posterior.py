"""H3: Bayesian promotion P(H=1 | E, R) on the filtered rule-only pool (rule_weeks >= 7), leave-one-seed-out.

R = rule family (chain predicate, from the key)      -> hierarchical Beta-Binomial prior per family, fit on 5 seeds
E = rule-week count                                   -> categorical likelihood P(rw | class), fit on 5 seeds
Posterior over class in {gold, decoy, other}: P(c | f, rw) propto P(c | f) * P(rw | c).  Scored on the 6th seed.

Variants (all scored identically; ties broken by sha256(key), label-free):
  A  rule_weeks (monotone)                       -- the wave-0 baseline
  B  family prior only, P(gold | f)              -- the hypothesis as stated
  C  rw categorical likelihood only (uniform prior over families)
  D  B x C combined posterior                    -- P(H=1 | E, R)
  E  within-seed family crowding (label-free): number of rw>=7 keys in the same family in the same seed
  F  entity-suffix prior (LOSO) -- a naming-scheme artefact, reported as a variant tried, not proposed
Labels score only. The fit for each held-out seed never sees that seed's labels.
Reads raw/plateau/candidate_pool.csv only (the dumps carry no field that varies inside this pool: channels=['rule'],
supporting_sites=None, candidate_weeks=[] for every one of the 1,765 keys).
"""
import csv, hashlib, math, statistics, collections, sys

POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
SEEDS = list(range(6)); CLASSES = ("gold", "decoy", "other")
RW_BUCKETS = [7, 8, 9, 10, 11, 12, 13, (14, 15), (16, 17), (18, 19), (20, 99)]


def pred(r): return r["key"].split(":")[-1]


def suffix(r):
    e = r["key"].split(":")[1]
    for s in ("-CORE", "-EDGE", "-PAY", "-II"):
        if e.endswith(s): return s
    return "X" if e.endswith("X") else "plain"


def rw_bucket(rw):
    for b in RW_BUCKETS:
        if isinstance(b, tuple):
            if b[0] <= rw <= b[1]: return b
        elif rw == b: return b
    return RW_BUCKETS[-1]


def tie(r): return hashlib.sha256(("e2e-ingest-v1|" + r["key"]).encode()).hexdigest()


def beta_binomial_rates(train, group, cls):
    """Per-group P(cls | group) with hierarchical Beta prior fit by method of moments across groups."""
    groups = sorted({group(r) for r in train})
    k = {g: sum(1 for r in train if group(r) == g and r["class"] == cls) for g in groups}
    n = {g: sum(1 for r in train if group(r) == g) for g in groups}
    m = sum(k.values()) / sum(n.values())
    p = {g: k[g] / n[g] for g in groups}
    var_obs = statistics.pvariance(list(p.values()))
    var_samp = statistics.mean(m * (1 - m) / n[g] for g in groups)   # binomial noise at each group's n
    tau2 = var_obs - var_samp
    if tau2 <= 1e-9:
        conc = 1e6                                                      # no between-family variance: full shrinkage
    else:
        conc = max(m * (1 - m) / tau2 - 1, 1e-3)
    a, b = m * conc, (1 - m) * conc
    return {g: (k[g] + a) / (n[g] + a + b) for g in groups}, {"mean": m, "tau2": tau2, "conc": conc}


def rw_likelihood(train):
    """P(rw bucket | class) with add-0.5 smoothing."""
    out = {}
    for c in CLASSES:
        rs = [r for r in train if r["class"] == c]
        cnt = collections.Counter(rw_bucket(int(r["rule_weeks"])) for r in rs)
        tot = len(rs) + 0.5 * len(RW_BUCKETS)
        out[c] = {b: (cnt[b] + 0.5) / tot for b in RW_BUCKETS}
    return out


def auc(scores, pos, neg):
    """AUC gold-vs-decoy by rank comparison; ties count 1/2."""
    P = [scores[i] for i in pos]; Nn = [scores[i] for i in neg]
    if not P or not Nn: return float("nan")
    wins = sum((1.0 if p > q else 0.5 if p == q else 0.0) for p in P for q in Nn)
    return wins / (len(P) * len(Nn))


def topk(order, k):
    top = order[:k]
    return sum(1 for r in top if r["class"] == "gold"), sum(1 for r in top if r["class"] == "decoy")


def main(min_rw=7):
    rows = list(csv.DictReader(open(POOL)))
    pool = [r for r in rows if r["detector_state"] == "rule_only" and int(r["rule_weeks"]) >= min_rw]
    print(f"filtered pool rule_only & rule_weeks>={min_rw}: n={len(pool)} gold={sum(r['class']=='gold' for r in pool)} "
          f"decoy={sum(r['class']=='decoy' for r in pool)}")
    variants = ["A_rw", "B_family", "C_rw_lik", "D_family_x_rw", "E_crowding", "F_suffix"]
    res = {v: collections.defaultdict(list) for v in variants}
    fits = []
    for s in SEEDS:
        train = [r for r in pool if int(r["seed"]) != s]; test = [r for r in pool if int(r["seed"]) == s]
        pri_g, fg = beta_binomial_rates(train, pred, "gold"); pri_d, fd = beta_binomial_rates(train, pred, "decoy")
        suf_g, sg = beta_binomial_rates(train, suffix, "gold"); suf_d, _ = beta_binomial_rates(train, suffix, "decoy")
        lik = rw_likelihood(train)
        base = {c: sum(1 for r in train if r["class"] == c) / len(train) for c in CLASSES}
        fits.append((s, fg, fd, sg))
        crowd = collections.Counter(pred(r) for r in test)
        scores = {v: {} for v in variants}
        for i, r in enumerate(test):
            f = pred(r); b = rw_bucket(int(r["rule_weeks"]))
            pf = {"gold": pri_g[f], "decoy": pri_d[f]}; pf["other"] = max(1 - pf["gold"] - pf["decoy"], 1e-6)
            ps = {"gold": suf_g[suffix(r)], "decoy": suf_d[suffix(r)]}; ps["other"] = max(1 - ps["gold"] - ps["decoy"], 1e-6)
            post_d = {c: pf[c] * lik[c][b] for c in CLASSES}; zd = sum(post_d.values())
            post_c = {c: base[c] * lik[c][b] for c in CLASSES}; zc = sum(post_c.values())
            scores["A_rw"][i] = int(r["rule_weeks"])
            scores["B_family"][i] = pf["gold"]
            scores["C_rw_lik"][i] = post_c["gold"] / zc
            scores["D_family_x_rw"][i] = post_d["gold"] / zd
            scores["E_crowding"][i] = crowd[f]
            scores["F_suffix"][i] = ps["gold"]
        pos = [i for i, r in enumerate(test) if r["class"] == "gold"]; neg = [i for i, r in enumerate(test) if r["class"] == "decoy"]
        n11 = sum(1 for r in test if int(r["rule_weeks"]) >= 11)
        for v in variants:
            order = sorted(range(len(test)), key=lambda i: (-scores[v][i], tie(test[i])))
            ordered = [test[i] for i in order]
            res[v]["auc"].append(auc(scores[v], pos, neg))
            for k in (20, 50, n11):
                g, d = topk(ordered, k); res[v][f"g@{k if k in (20,50) else 'n11'}"].append(g); res[v][f"d@{k if k in (20,50) else 'n11'}"].append(d)
            res[v]["n11"].append(n11); res[v]["gold_total"].append(len(pos))
    print("\nhierarchical fit per fold (held-out seed, gold prior: pooled mean, between-family variance tau2, concentration):")
    for s, fg, fd, sg in fits:
        print(f"  hold out {s}: gold  m={fg['mean']:.3f} tau2={fg['tau2']:+.5f} conc={fg['conc']:.1f} | "
              f"decoy m={fd['mean']:.3f} tau2={fd['tau2']:+.5f} conc={fd['conc']:.1f} | suffix-gold tau2={sg['tau2']:+.5f}")
    print("\nper-variant LOSO results (each seed scored by a fit on the other five):")
    for v in variants:
        a = res[v]["auc"]
        print(f"\n{v}")
        print(f"  AUC gold-vs-decoy per seed: {' '.join(f'{x:.3f}' for x in a)}   mean {statistics.mean(a):.3f}  min {min(a):.3f}  max {max(a):.3f}")
        for k in ("20", "50", "n11"):
            g = res[v][f"g@{k}"]; d = res[v][f"d@{k}"]; kk = [int(k)] * 6 if k != "n11" else res[v]["n11"]
            pg = [gi / ki for gi, ki in zip(g, kk)]; pd_ = [di / ki for di, ki in zip(d, kk)]
            print(f"  top-{k:>3}: gold {g} sum {sum(g):3d}  prec {statistics.mean(pg):.3f} [{min(pg):.3f},{max(pg):.3f}] | "
                  f"decoy {d} sum {sum(d):3d}  rate {statistics.mean(pd_):.3f}")
    print(f"\n  (n11 = keys with rule_weeks>=11 per seed = {res['A_rw']['n11']}; gold per seed = {res['A_rw']['gold_total']})")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
