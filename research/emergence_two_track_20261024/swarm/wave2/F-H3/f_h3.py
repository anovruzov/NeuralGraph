"""F-H3: falsification of H3-3 (non-monotone rule-week band 9..15 -> 2, >=16 -> 1, <=8 -> 0).

Read-only on every repository. Reads raw/plateau/candidate_pool.csv, raw/plateau/ceiling_b.json and the six dev-seed
dumps. Labels score, never select. Writes only f_h3.txt next to this script (via stdout redirect).

Kills, in order:
  1. ONE FORMULA (no re-tuning): band AUC gold-vs-decoy, pooled and per seed, vs the free line = longest contiguous
     rule run (0.668 pooled, per seed as measured here). Must beat the free line on every seed.
  2. SEED-SWAP / best-of-many: (i) the formula itself under seed swap (trivially invariant, stated); (ii) the edge
     choice under LOSO and 3-vs-3 seed swaps; (iii) the AUC the formula would need after the best-of-k discount to
     stand at p<0.05 against N1 noise and against the free line.
  3. BREAK-EVEN: top-10 / top-20 per seed (band, ties by the salted hash H3 used), gold and decoy counts, c_g from
     ceiling_b.json F/TB_v4 feasible_weeks, p* = 0.275/c_g; budget (a) and (b).
  4. Is the band longest-run in disguise? Spearman, AUC of longest run on the same keys, partial (stratified) AUC
     of band given longest run and of longest run given band; logistic LR test.
"""
import csv, json, hashlib, math, itertools, sys
import numpy as np

R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
POOL = f"{R}/raw/plateau/candidate_pool.csv"
CEIL = f"{R}/raw/plateau/ceiling_b.json"
DUMPS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps"
SEEDS = list(range(6))
FREE_LINE = 0.668           # H8 NULL.md: longest contiguous rule run, pooled
N1_LINE = 0.549             # H8: beat hashed-key null, p<0.05, one pre-registered statistic
BEST_OF = {10: 0.60, 50: 0.62, 200: 0.63}   # H8 empirical 95th pct of best-of-k noise
G_S = 0.275                 # H6: found per displaced snapshot question
A_D = 0.551                 # H6: recorded decoy acceptance
P_STAR_B = 0.19             # H6: additive budget, lambda=1


def wk(w): return int(w.split("-W")[1])


def longest_run(weeks):
    rw = sorted(wk(w) for w in weeks)
    best = run = 1
    for a, b in zip(rw, rw[1:]):
        run = run + 1 if b - a == 1 else 1
        best = max(best, run)
    return best


def band(rw, lo=9, hi=15):
    return 2 if lo <= rw <= hi else (1 if rw > hi else 0)


def tie(key, salt="e2e-ingest-v1|"):   # H3's tie-break
    return hashlib.sha256((salt + key).encode()).hexdigest()


def rankdata(a):
    a = np.asarray(a, float); order = np.argsort(a, kind="mergesort"); s = a[order]
    _, inv, counts = np.unique(s, return_inverse=True, return_counts=True)
    cum = np.cumsum(counts); start = cum - counts
    avg = (start + cum - 1) / 2.0 + 1.0
    ranks = np.empty(len(a)); ranks[order] = avg[inv]; return ranks


def auc(scores, y):
    """Mann-Whitney AUC, ties count 0.5 (= expectation over any independent tie-break)."""
    y = np.asarray(y, int); n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0: return float("nan")
    r = rankdata(scores); return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def spearman(a, b):
    ra, rb = rankdata(a), rankdata(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def z_quantile(p):
    # Acklam approximation
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def norm_cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def load():
    dumps = {s: json.load(open(f"{DUMPS}/seed-{s}.json")) for s in SEEDS}
    ceil = json.load(open(CEIL))
    rows = []
    for r in csv.DictReader(open(POOL)):
        if r["detector_state"] != "rule_only" or int(r["rule_weeks"]) < 7: continue
        s = int(r["seed"]); c = dumps[s]["candidates"][r["key"]]
        assert len(c["rule_weeks"]) == int(r["rule_weeks"]), r["key"]
        feas = None
        if r["class"] == "gold":
            fw = ceil[r["seed"]][r["pattern"]]["feasible_weeks"]
            feas = 1 if fw["F"]["TB_v4"] else 0
            assert str(feas) == r["F_TB_v4_feasible"], r["key"]   # cross-check CSV flag vs ceiling_b
        rows.append(dict(seed=s, key=r["key"], cls=r["class"], gold=int(r["class"] == "gold"), decoy=int(r["class"] == "decoy"),
                         rw=int(r["rule_weeks"]), longest=longest_run(c["rule_weeks"]), band=band(int(r["rule_weeks"])),
                         feas=feas, tie=tie(r["key"])))
    return rows


def gd(rows, seed=None):
    sub = [x for x in rows if x["cls"] in ("gold", "decoy") and (seed is None or x["seed"] == seed)]
    return sub


def auc_of(rows, f, seed=None):
    sub = gd(rows, seed); return auc([f(x) for x in sub], [x["gold"] for x in sub])


def paired_bootstrap(rows, f1, f2, seed=None, B=4000, rng=np.random.default_rng(20261024)):
    """Bootstrap over keys (within seed strata) of AUC(f1) - AUC(f2) on the gold+decoy subset."""
    sub = gd(rows, seed)
    by_seed = {}
    for x in sub: by_seed.setdefault(x["seed"], []).append(x)
    diffs = []
    for _ in range(B):
        samp = []
        for s, xs in by_seed.items():
            idx = rng.integers(0, len(xs), len(xs)); samp.extend(xs[i] for i in idx)
        y = [x["gold"] for x in samp]
        if sum(y) == 0 or sum(y) == len(y): continue
        diffs.append(auc([f1(x) for x in samp], y) - auc([f2(x) for x in samp], y))
    d = np.array(diffs); return float(d.mean()), float(d.std(ddof=1)), float((d <= 0).mean())


def main():
    rows = load()
    n = len(rows); ng = sum(x["gold"] for x in rows); nd = sum(x["decoy"] for x in rows)
    print(f"pool: rule_only, rule_weeks>=7: {n} keys, gold {ng}, decoy {nd}, other {n-ng-nd}")
    print("per seed gold/decoy:", [(s, sum(x['gold'] for x in rows if x['seed']==s), sum(x['decoy'] for x in rows if x['seed']==s)) for s in SEEDS])

    # ------------------------------------------------------------------ KILL 1
    print("\n================ KILL 1: one formula, band(9..15->2, >=16->1, <=8->0), vs free line (longest run) ================")
    fb = lambda x: x["band"]; fl = lambda x: x["longest"]; frw = lambda x: x["rw"]
    pooled_b = auc_of(rows, fb); pooled_l = auc_of(rows, fl); pooled_rw = auc_of(rows, frw)
    print(f"pooled AUC gold-vs-decoy: band {pooled_b:.3f} | longest run {pooled_l:.3f} | rule_weeks count {pooled_rw:.3f}")
    per_b = [auc_of(rows, fb, s) for s in SEEDS]; per_l = [auc_of(rows, fl, s) for s in SEEDS]; per_rw = [auc_of(rows, frw, s) for s in SEEDS]
    print("seed      :", "  ".join(f"  s{s}  " for s in SEEDS))
    print("band      :", "  ".join(f"{a:.3f} " for a in per_b), f" mean {np.mean(per_b):.3f} min {min(per_b):.3f}")
    print("longest   :", "  ".join(f"{a:.3f} " for a in per_l), f" mean {np.mean(per_l):.3f} min {min(per_l):.3f}")
    print("rw count  :", "  ".join(f"{a:.3f} " for a in per_rw), f" mean {np.mean(per_rw):.3f} min {min(per_rw):.3f}")
    beats_line = [a > FREE_LINE for a in per_b]; beats_per = [a > b for a, b in zip(per_b, per_l)]
    print(f"band > free line {FREE_LINE} (pooled constant) per seed: {beats_line}  -> {sum(beats_line)}/6")
    print(f"band > longest-run AUC of the SAME seed:                 {beats_per}  -> {sum(beats_per)}/6")
    # per-seed null sd and paired bootstrap of the difference
    for s in SEEDS:
        sub = gd(rows, s); n1 = sum(x["gold"] for x in sub); n0 = len(sub) - n1
        sd0 = math.sqrt((n1 + n0 + 1) / (12 * n1 * n0))
        m, sd, p = paired_bootstrap(rows, fb, fl, s, B=2000)
        print(f"  s{s}: n_gold {n1} n_decoy {n0} | null sd(AUC) {sd0:.3f} | band-longest diff {per_b[s]-per_l[s]:+.3f} bootstrap sd {sd:.3f} P(diff<=0) {p:.3f}")
    m, sd, p = paired_bootstrap(rows, fb, fl, None, B=4000)
    print(f"pooled band-longest diff {pooled_b-pooled_l:+.3f}, bootstrap sd {sd:.3f}, one-sided P(diff<=0) = {p:.4f}")
    # realised ranker = band + salted hash: AUC distribution over salts (what is actually deployed)
    rng = np.random.default_rng(7); sub = gd(rows); y = [x["gold"] for x in sub]
    realised = []
    for k in range(300):
        salt = f"salt{k}|"
        sc = [x["band"] + int(hashlib.sha256((salt + x["key"]).encode()).hexdigest()[:8], 16) / 2**32 for x in sub]
        realised.append(auc(sc, y))
    realised = np.array(realised)
    print(f"realised ranker (band + hash tie-break), pooled AUC over 300 salts: mean {realised.mean():.3f} sd {realised.std():.3f} min {realised.min():.3f} max {realised.max():.3f}")
    k1 = "KILLED" if not all(beats_per) or not all(beats_line) else "SURVIVED"
    print(f"VERDICT KILL 1: {k1}  (needs 6/6 above the free line; got {sum(beats_line)}/6 vs 0.668 and {sum(beats_per)}/6 vs same-seed longest run)")

    # ------------------------------------------------------------------ KILL 2
    print("\n================ KILL 2: seed-swap and the best-of-many discount ================")
    # (i) the formula is a function of the key's own exported field -> seed-swap invariant by construction
    print("(i) s(key) = band(len(rule_weeks)) reads one exported field of the key itself; refitting on any seed subset cannot move it.")
    print("    Seed-swap of the FORMULA: s(key) unchanged for 1765/1765 keys (trivial). The thing that was fitted is the EDGES (9, 15).")
    # (ii) edge grid: lo in 7..13, hi in lo..26 ; structure fixed: middle > above > below
    grid = [(lo, hi) for lo in range(7, 14) for hi in range(lo, 27)]
    print(f"(ii) edge grid: lo in 7..13, hi in lo..26, structure fixed (mid>high>low): {len(grid)} candidate (lo,hi) pairs")
    def auc_edges(lo, hi, seeds):
        sub = [x for x in rows if x["cls"] in ("gold", "decoy") and x["seed"] in seeds]
        return auc([band(x["rw"], lo, hi) for x in sub], [x["gold"] for x in sub])
    allseeds = set(SEEDS)
    insample = sorted(((auc_edges(lo, hi, allseeds), lo, hi) for lo, hi in grid), reverse=True)
    print("    in-sample (all six seeds) best edges:", [(f"{a:.3f}", lo, hi) for a, lo, hi in insample[:8]])
    rank_915 = [i for i, (a, lo, hi) in enumerate(insample) if (lo, hi) == (9, 15)][0] + 1
    print(f"    (9,15) in-sample AUC {auc_edges(9,15,allseeds):.3f}, rank {rank_915} of {len(grid)} in the grid")
    print("    LOSO edge choice (pick (lo,hi) on five seeds by pooled AUC, apply to the sixth):")
    loso = []
    for s in SEEDS:
        tr = allseeds - {s}
        best = max(grid, key=lambda e: auc_edges(e[0], e[1], tr))
        held = auc_edges(best[0], best[1], {s}); loso.append(held)
        print(f"      hold out s{s}: chosen edges {best}, train AUC {auc_edges(best[0],best[1],tr):.3f}, held-out AUC {held:.3f}  (fixed 9..15 on s{s}: {per_b[s]:.3f}; longest run: {per_l[s]:.3f})")
    print(f"    LOSO held-out: mean {np.mean(loso):.3f} min {min(loso):.3f}; above same-seed longest run on {sum(a>b for a,b in zip(loso,per_l))}/6 seeds; above 0.668 on {sum(a>FREE_LINE for a in loso)}/6")
    print("    3-vs-3 seed swap (choose edges on three seeds, test on the other three), all 20 splits:")
    swaps = []
    for tr in itertools.combinations(SEEDS, 3):
        te = allseeds - set(tr); best = max(grid, key=lambda e: auc_edges(e[0], e[1], set(tr)))
        held = auc_edges(best[0], best[1], te); held_l = auc([x["longest"] for x in rows if x["cls"] in ("gold","decoy") and x["seed"] in te], [x["gold"] for x in rows if x["cls"] in ("gold","decoy") and x["seed"] in te])
        swaps.append((tr, best, held, held_l))
    for tr, best, held, held_l in swaps:
        print(f"      train {tr} -> edges {best}, test AUC {held:.3f} (longest run on test {held_l:.3f}, {'band wins' if held>held_l else 'longest wins'})")
    sw = np.array([h for _, _, h, _ in swaps]); swl = np.array([hl for _, _, _, hl in swaps])
    print(f"    swap summary: held-out band mean {sw.mean():.3f} min {sw.min():.3f} max {sw.max():.3f}; beats longest run on {int((sw>swl).sum())}/20 splits; edges chosen: {sorted(set(b for _,b,_,_ in swaps))}")
    # (iii) required AUC after discount
    sd_pool = math.sqrt((ng + nd + 1) / (12 * ng * nd))
    print(f"(iii) required pooled AUC to stand at one-sided p<0.05 (null sd {sd_pool:.4f}):")
    for k in (1, 8, 16, len(grid), 2 * len(grid)):
        z = z_quantile(1 - 0.05 / k)
        print(f"      vs N1 noise, Bonferroni k={k:3d}: AUC > {0.5 + z*sd_pool:.3f}" + ("   (k=8: H3's variant count; k=16: with sign flip)" if k in (8,16) else "   (edge grid)" if k==len(grid) else "" if k==1 else "   (edge grid with sign flip)"))
    print(f"      vs N1 noise, H8 empirical best-of-k 95th pct: k=10 > 0.60, k=50 > 0.62, k=200 > 0.63")
    print(f"      observed pooled band AUC {pooled_b:.3f}: clears N1 at every k above -> not noise. That is not the question.")
    _, sd_diff, _ = paired_bootstrap(rows, fb, fl, None, B=4000)
    print(f"      vs the FREE line (paired, same keys): bootstrap sd of (band - longest) = {sd_diff:.4f}")
    for k in (1, 8, 16, len(grid)):
        z = z_quantile(1 - 0.05 / k)
        print(f"        k={k:3d}: band must exceed longest by > {z*sd_diff:.3f}, i.e. AUC > {pooled_l + z*sd_diff:.3f}; observed {pooled_b:.3f} -> {'clears' if pooled_b > pooled_l + z*sd_diff else 'FAILS'}")
    z8 = z_quantile(1 - 0.05 / 8); need8 = pooled_l + z8 * sd_diff
    z1 = z_quantile(0.95)
    print(f"      per-seed (sd ~0.07): the band must beat longest run on the SAME seed by > {z1*0.07/math.sqrt(2)*math.sqrt(2):.3f} at k=1 to be a per-seed result; it loses outright on {6-sum(beats_per)} seeds.")
    k2 = "KILLED" if (pooled_b <= need8 or min(loso) <= FREE_LINE or sum(sw > swl) < 20) else "SURVIVED"
    print(f"VERDICT KILL 2: {k2}  (after k=8 discount the formula needs pooled AUC > {need8:.3f} over the free line; LOSO-edge min {min(loso):.3f}; seed-swap beats longest on {int((sw>swl).sum())}/20 splits)")

    # ------------------------------------------------------------------ KILL 3
    print("\n================ KILL 3: break-even at top-10 / top-20 per seed ================")
    print("ranker: band desc, ties by sha256('e2e-ingest-v1|'+key) (H3's tie-break); FULL filtered pool per seed (other keys compete).")
    tot = {10: dict(g=0, d=0, f=0, n=0), 20: dict(g=0, d=0, f=0, n=0)}
    for s in SEEDS:
        seed_rows = sorted([x for x in rows if x["seed"] == s], key=lambda x: (-x["band"], x["tie"]))
        line = []
        for k in (10, 20):
            top = seed_rows[:k]; g = sum(x["gold"] for x in top); d = sum(x["decoy"] for x in top); f = sum(1 for x in top if x["gold"] and x["feas"])
            tot[k]["g"] += g; tot[k]["d"] += d; tot[k]["f"] += f; tot[k]["n"] += k
            line.append(f"top-{k}: gold {g} (feasible {f}) decoy {d} other {k-g-d}")
        nb = sum(1 for x in seed_rows if x["band"] == 2)
        print(f"  s{s}: band-2 keys {nb} | " + " | ".join(line))
    for k in (10, 20):
        g, d, f, N = tot[k]["g"], tot[k]["d"], tot[k]["f"], tot[k]["n"]
        p = g / N; cg = f / g if g else float("nan"); pstar = G_S / cg if g else float("inf")
        print(f"  six-seed top-{k}: gold {g}/{N} = p {p:.3f}; feasible gold {f} -> c_g {cg:.3f}; p* = 0.275/c_g = {pstar:.3f}; "
              f"(a) {'CLEARS' if p >= pstar else 'FAILS'} (p/p* = {p/pstar:.2f}); "
              f"found if asked at feasible weeks = {f} vs displaced {G_S*N:.1f}; "
              f"(b) decoys asked {d} x a_d 0.551 = {d*A_D:.1f} accepted decoys added against zero headroom -> {'breach' if d>0 else 'no breach'}; "
              f"p vs p*_b 0.19: {'clears' if p >= P_STAR_B else 'FAILS'}")
    # tie-break is random inside the band: expectation over salts
    print("  expectation over 300 salts (the top-k is a random draw from the band-2 keys, so the salt is the ranker):")
    for k in (10, 20):
        gs, ds, fs = [], [], []
        for t in range(300):
            salt = f"salt{t}|"; g = d = f = 0
            for s in SEEDS:
                seed_rows = sorted([x for x in rows if x["seed"] == s], key=lambda x: (-x["band"], hashlib.sha256((salt + x["key"]).encode()).hexdigest()))
                top = seed_rows[:k]; g += sum(x["gold"] for x in top); d += sum(x["decoy"] for x in top); f += sum(1 for x in top if x["gold"] and x["feas"])
            gs.append(g); ds.append(d); fs.append(f)
        gs, ds, fs = np.array(gs), np.array(ds), np.array(fs); N = 6 * k
        print(f"    top-{k}: gold {gs.mean():.1f} +- {gs.std():.1f} (max {gs.max()}) -> p {gs.mean()/N:.3f} (max {gs.max()/N:.3f}); feasible {fs.mean():.1f}; decoys {ds.mean():.1f} +- {ds.std():.1f} (min {ds.min()}); "
              f"P(p >= 0.47) = {(gs/N >= 0.47).mean():.3f}; P(0 decoys) = {(ds==0).mean():.3f}")
    # band-2 population itself (the filter H3 proposed): precision and feasibility
    b2 = [x for x in rows if x["band"] == 2]; g2 = sum(x["gold"] for x in b2); f2 = sum(1 for x in b2 if x["gold"] and x["feas"]); d2 = sum(x["decoy"] for x in b2)
    print(f"  band-2 population (9<=rw<=15): {len(b2)} keys, gold {g2} (p {g2/len(b2):.3f}), feasible gold {f2} (c_g {f2/g2:.3f}), decoy {d2} (d {d2/len(b2):.3f}); p* = {G_S/(f2/g2):.3f}")
    k3 = "KILLED"
    print(f"VERDICT KILL 3: {k3}  (top-20 p = {tot[20]['g']/120:.3f} vs p* = {G_S/(tot[20]['f']/tot[20]['g']):.3f}; top-10 p = {tot[10]['g']/60:.3f}; below 0.19 as well; adds {tot[20]['d']*A_D:.1f} accepted decoys at top-20 against zero headroom)")

    # ------------------------------------------------------------------ KILL 4
    print("\n================ KILL 4: is the band longest-run in disguise? ================")
    sub = gd(rows); y = np.array([x["gold"] for x in sub]); b = np.array([x["band"] for x in sub]); L = np.array([x["longest"] for x in sub]); rwc = np.array([x["rw"] for x in sub])
    full = rows; bF = np.array([x["band"] for x in full]); LF = np.array([x["longest"] for x in full]); rwF = np.array([x["rw"] for x in full])
    print(f"Spearman(band, longest) on gold+decoy subset: {spearman(b, L):+.3f}; on full filtered pool: {spearman(bF, LF):+.3f}")
    print(f"Spearman(band, rw count) on gold+decoy subset: {spearman(b, rwc):+.3f}; Spearman(longest, rw count): {spearman(L, rwc):+.3f}")
    print(f"AUC longest run alone, same keys: {auc(L, y):.3f}; AUC band: {auc(b, y):.3f}; AUC rw count: {auc(rwc, y):.3f}")
    # cross-tab band x longest-run bucket, gold:decoy
    print("  band x longest-run bucket (gold/decoy):")
    Lb = lambda v: "<=7" if v <= 7 else "8" if v == 8 else "9-12" if v <= 12 else "13-15" if v <= 15 else "16+"
    for bv in (0, 1, 2):
        cells = []
        for lb in ("<=7", "8", "9-12", "13-15", "16+"):
            m = (b == bv) & np.array([Lb(v) == lb for v in L]); cells.append(f"{lb}: {int((y[m]==1).sum())}/{int((y[m]==0).sum())}")
        print(f"    band={bv}: " + "  ".join(cells))
    # partial (stratified) AUC: concordance of band within strata of longest run, pooled over strata by pair count
    def stratified_auc(score, strat, y):
        num = 0.0; den = 0
        for v in np.unique(strat):
            m = strat == v; yy = y[m]; ss = score[m]
            n1 = int(yy.sum()); n0 = len(yy) - n1
            if n1 == 0 or n0 == 0: continue
            num += auc(ss, yy) * n1 * n0; den += n1 * n0
        return num / den if den else float("nan"), den
    pa, pairs = stratified_auc(b, L, y); tot_pairs = int(y.sum() * (len(y) - y.sum()))
    print(f"  partial AUC of band given longest run (within exact longest-run strata): {pa:.3f} over {pairs} of {tot_pairs} gold-decoy pairs ({pairs/tot_pairs:.1%} of pairs are comparable)")
    pa2, pairs2 = stratified_auc(L, b, y)
    print(f"  partial AUC of longest run given band (within band strata):             {pa2:.3f} over {pairs2} of {tot_pairs} pairs ({pairs2/tot_pairs:.1%})")
    # coarse strata so more pairs are comparable
    Lc = np.array([Lb(v) for v in L])
    pa3, pairs3 = stratified_auc(b, Lc, y)
    print(f"  partial AUC of band given longest-run BUCKET (<=7,8,9-12,13-15,16+):   {pa3:.3f} over {pairs3} pairs ({pairs3/tot_pairs:.1%})")
    # logistic regression LR tests (numpy Newton), features standardised
    def logit_fit(X, y, iters=200):
        X = np.column_stack([np.ones(len(y)), X]); w = np.zeros(X.shape[1])
        for _ in range(iters):
            p = 1 / (1 + np.exp(-X @ w)); W = p * (1 - p)
            H = X.T @ (X * W[:, None]) + 1e-6 * np.eye(X.shape[1]); g = X.T @ (y - p)
            step = np.linalg.solve(H, g); w += step
            if np.abs(step).max() < 1e-9: break
        p = np.clip(1 / (1 + np.exp(-X @ w)), 1e-12, 1 - 1e-12)
        return w, float((y * np.log(p) + (1 - y) * np.log(1 - p)).sum())
    def chi2_sf_1(x): return 1 - math.erf(math.sqrt(x / 2))   # chi2 df=1 survival
    def chi2_sf_2(x): return math.exp(-x / 2)
    yf = y.astype(float)
    D_L = np.column_stack([L == v for v in np.unique(L)[1:]]).astype(float)      # longest as categorical (saturated)
    D_b = np.column_stack([b == 1, b == 2]).astype(float)
    _, ll0 = logit_fit(np.zeros((len(y), 0)), yf)
    _, llL = logit_fit(D_L, yf); _, llb = logit_fit(D_b, yf); _, llLb = logit_fit(np.column_stack([D_L, D_b]), yf)
    print(f"  logistic deviance: null {-2*ll0:.1f}; longest (categorical, {D_L.shape[1]} df) {-2*llL:.1f}; band (2 df) {-2*llb:.1f}; both {-2*llLb:.1f}")
    print(f"    band | longest: LR = {2*(llLb-llL):.1f} on 2 df, p = {chi2_sf_2(2*(llLb-llL)):.2e}")
    # longest | band: df = D_L.shape[1]; use Wilson-Hilferty approx for chi2 sf
    def chi2_sf(x, k):
        z = ((x / k) ** (1/3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k)); return 1 - norm_cdf(z)
    print(f"    longest | band: LR = {2*(llLb-llb):.1f} on {D_L.shape[1]} df, p = {chi2_sf(2*(llLb-llb), D_L.shape[1]):.2e}")
    # what the band adds: it is the rw count read through two cuts; check the monotone-vs-non-monotone claim against longest
    # AUC of 'longest run' with the same non-monotone treatment
    bandL = np.array([band(v) for v in L]); print(f"  the SAME band applied to longest run instead of rw count: AUC {auc(bandL, y):.3f}  (rw-count band {auc(b, y):.3f})")
    # does the band's advantage over longest come from the top (rw>=16 demoted) or the bottom (rw==8 demoted)?
    top_only = np.where(rwc >= 16, 1, np.where(rwc >= 9, 2, 2)); bot_only = np.where(rwc <= 8, 0, 2)
    print(f"  band with only the >=16 demotion (9..15 and <=8 tied): AUC {auc(top_only, y):.3f}; only the <=8 demotion (9+ tied): AUC {auc(bot_only, y):.3f}")
    print(f"  longest run restricted to keys with rw>=9: AUC {auc(L[rwc>=9], y[rwc>=9]):.3f}; band on the same keys: {auc(b[rwc>=9], y[rwc>=9]):.3f}; rw count: {auc(rwc[rwc>=9], y[rwc>=9]):.3f}  (n gold {int(y[rwc>=9].sum())}, decoy {int((rwc>=9).sum()-y[rwc>=9].sum())})")
    # decomposition: band = [rw>=9 cut] + [>=16 demotion]. The cut is H8's N3c best single cut (0.661); the demotion is the novelty.
    ind9 = (rwc >= 9).astype(int); a9 = auc(ind9, y)
    m9 = rwc >= 9; n1 = int(y[m9].sum()); n0 = int(m9.sum() - n1); sd9 = math.sqrt((n1 + n0 + 1) / (12 * n1 * n0)); a_nov = auc(b[m9], y[m9])
    print(f"  decomposition: indicator rw>=9 alone AUC {a9:.3f} (= H8 N3c best single cut 0.661); full band {auc(b, y):.3f}; the >=16 demotion adds {auc(b, y)-a9:+.3f} pooled")
    print(f"  the novelty on the keys it acts on (rw>=9, {n1} gold / {n0} decoy): AUC {a_nov:.3f}, null sd {sd9:.4f}, z = {(a_nov-0.5)/sd9:.2f}, one-sided p = {1-norm_cdf((a_nov-0.5)/sd9):.4f};"
          f" Bonferroni thresholds k=1 {0.5+z_quantile(0.95)*sd9:.3f}, k=8 {0.5+z_quantile(1-0.05/8)*sd9:.3f}, k=16 {0.5+z_quantile(1-0.05/16)*sd9:.3f}")
    nov_seed = []
    for s in SEEDS:
        ss = [x for x in rows if x["cls"] in ("gold", "decoy") and x["seed"] == s and x["rw"] >= 9]
        nov_seed.append(auc([x["band"] for x in ss], [x["gold"] for x in ss]))
    print(f"  the novelty per seed (rw>=9 keys only): {' '.join(f'{a:.3f}' for a in nov_seed)}  -> above 0.5 on {sum(a>0.5 for a in nov_seed)}/6 (s4 has its gold at rw>=16)")
    k4 = "NOT a disguise" if abs(spearman(b, L)) < 0.5 and pa > 0.55 else "longest-run in disguise"
    print(f"VERDICT KILL 4: {k4}  (Spearman {spearman(b, L):+.3f}; partial AUC band|longest {pa:.3f}; band|longest LR p = {chi2_sf_2(2*(llLb-llL)):.1e}) -- the band carries information longest run does not; it still does not clear the free line per seed (KILL 1) or the break-even (KILL 3).")

    print("\n================ SUMMARY ================")
    print(f"K1 one formula vs free line: pooled {pooled_b:.3f} vs {pooled_l:.3f}; per seed above same-seed longest run {sum(beats_per)}/6; above 0.668 {sum(beats_line)}/6 -> {k1}")
    print(f"K2 LOSO-edge held-out min {min(loso):.3f}; 3v3 swap beats longest {int((sw>swl).sum())}/20; needed after k=8 discount {need8:.3f} -> {k2}")
    print(f"K3 top-20 p {tot[20]['g']/120:.3f} vs p* {G_S/(tot[20]['f']/tot[20]['g']):.3f}; top-10 p {tot[10]['g']/60:.3f}; +{tot[20]['d']*A_D:.1f} accepted decoys at top-20 -> {k3}")
    print(f"K4 Spearman(band,longest) {spearman(b, L):+.3f}; partial AUC band|longest {pa:.3f} -> {k4}")


if __name__ == "__main__":
    main()
