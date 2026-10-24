"""H3 kill check: per-rule-family gold / decoy rates on the filtered rule-only pool (rule_weeks >= 7).

Reads only raw/plateau/candidate_pool.csv. Labels are used to SCORE (rates, chi-square, LOSO transfer), never to
rank. Pure python (no scipy): chi-square p-values via the regularized gamma function.
"""
import csv, math, statistics, sys, collections

POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
SEEDS = range(6)


def pred(r):
    return r["key"].split(":")[-1]


def chi2(tab):
    R, C = len(tab), len(tab[0]); N = sum(map(sum, tab))
    rs = [sum(t) for t in tab]; cs = [sum(tab[i][j] for i in range(R)) for j in range(C)]
    x = sum((tab[i][j] - rs[i] * cs[j] / N) ** 2 / (rs[i] * cs[j] / N)
            for i in range(R) for j in range(C) if rs[i] * cs[j] > 0)
    return x, (R - 1) * (C - 1)


def chi2_sf(x, k):
    a, z = k / 2.0, x / 2.0
    if z <= 0: return 1.0
    if z < a + 1:
        s = term = 1.0 / a; n = 1
        while abs(term) > 1e-14 * abs(s) and n < 10000:
            term *= z / (a + n); s += term; n += 1
        return 1 - s * math.exp(-z + a * math.log(z) - math.lgamma(a))
    b = z + 1 - a; c = 1e300; d = 1 / b; h = d
    for i in range(1, 10000):
        an = -i * (i - a); b += 2; d = an * d + b; d = 1 / d if abs(d) > 1e-300 else 1e300
        c = b + an / c if abs(c) > 1e-300 else 1e300; de = d * c; h *= de
        if abs(de - 1) < 1e-14: break
    return math.exp(-z + a * math.log(z) - math.lgamma(a)) * h


def main(min_rw=7):
    rows = list(csv.DictReader(open(POOL)))
    f = [r for r in rows if r["detector_state"] == "rule_only" and int(r["rule_weeks"]) >= min_rw]
    preds = sorted({pred(r) for r in f})
    G = sum(r["class"] == "gold" for r in f); Dd = sum(r["class"] == "decoy" for r in f); N = len(f)
    print(f"filtered pool: rule_only & rule_weeks>={min_rw}: n={N} gold={G} decoy={Dd} other={N-G-Dd} "
          f"gold_rate={G/N:.3f} decoy_rate={Dd/N:.3f} gold:decoy={G/Dd:.2f}")
    print()
    print("%-16s %5s %5s %5s %5s %7s %7s %6s %9s" % ("family", "n", "gold", "decoy", "other", "g_rate", "d_rate", "g:d", "lift_g:d"))
    for p in sorted(preds, key=lambda p: -sum(1 for r in f if pred(r) == p)):
        c = collections.Counter(r["class"] for r in f if pred(r) == p); n = sum(c.values())
        gd = c["gold"] / c["decoy"] if c["decoy"] else float("inf")
        print("%-16s %5d %5d %5d %5d %7.3f %7.3f %6.2f %9.2f" % (p, n, c["gold"], c["decoy"], c["other"], c["gold"] / n, c["decoy"] / n, gd, gd / (G / Dd)))
    print()
    tab = [[sum(1 for r in f if pred(r) == p and r["class"] == c) for c in ("gold", "decoy")] for p in preds]
    x, k = chi2(tab); print(f"pooled 6 seeds, family x (gold,decoy): chi2={x:.2f} dof={k} p={chi2_sf(x,k):.3f}")
    tab = [[sum(1 for r in f if pred(r) == p and r["class"] == c) for c in ("gold", "decoy", "other")] for p in preds]
    x, k = chi2(tab); print(f"pooled 6 seeds, family x (gold,decoy,other): chi2={x:.2f} dof={k} p={chi2_sf(x,k):.3f}")
    print()
    print("per seed, family -> gold/decoy/n, and within-seed family x (gold,decoy) chi-square:")
    for s in SEEDS:
        fs = [r for r in f if int(r["seed"]) == s]
        cells = " ".join("%s:%d/%d/%d" % (p.replace("chain_", "")[:4],
                                           sum(1 for r in fs if pred(r) == p and r["class"] == "gold"),
                                           sum(1 for r in fs if pred(r) == p and r["class"] == "decoy"),
                                           sum(1 for r in fs if pred(r) == p)) for p in preds)
        t = [[sum(1 for r in fs if pred(r) == p and r["class"] == c) for c in ("gold", "decoy")] for p in preds]
        x, k = chi2(t)
        print(f"  seed {s}: {cells}   chi2={x:.1f} p={chi2_sf(x,k):.3f}")
    print()
    for cls in ("gold", "decoy"):
        t = [[sum(1 for r in f if int(r["seed"]) == s and pred(r) == p and r["class"] == cls) for p in preds] for s in SEEDS]
        x, k = chi2(t); print(f"seed x family interaction on {cls} counts (6x7): chi2={x:.2f} dof={k} p={chi2_sf(x,k):.4f}")
    print()
    print("leave-one-seed-out transfer of the family signal: corr(train log-odds gold:decoy per family, test log-odds)")
    cors = []
    for s in SEEDS:
        tr = [r for r in f if int(r["seed"]) != s]; te = [r for r in f if int(r["seed"]) == s]
        lo = lambda rs, p: math.log((sum(1 for r in rs if pred(r) == p and r["class"] == "gold") + 0.5) /
                                    (sum(1 for r in rs if pred(r) == p and r["class"] == "decoy") + 0.5))
        a = [lo(tr, p) for p in preds]; b = [lo(te, p) for p in preds]
        ma, mb = statistics.mean(a), statistics.mean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b)); den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
        cors.append(num / den); print(f"  hold out seed {s}: r = {cors[-1]:+.3f}")
    print(f"  mean {statistics.mean(cors):+.3f}  min {min(cors):+.3f}  max {max(cors):+.3f}  (positive = the prior transfers)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
