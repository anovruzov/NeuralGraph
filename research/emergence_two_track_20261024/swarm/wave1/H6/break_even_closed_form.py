#!/usr/bin/env python
"""H6 — closed-form break-even for ranking the rule-only candidate pool.

Every input is read from the recorded artifacts or restated from them; nothing is tuned.
Outputs: p*(N) tables under budgets (a) displace and (b) add; expected net change at the
wave-0 operating point; BH-style rule (classical BH on rule_weeks with a label-free marginal
null, prec@k rule, discovery-level FDR). Read-only on the repo; writes nothing but stdout.
"""
import csv, collections, sys
R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
rows = list(csv.DictReader(open(R + "/raw/plateau/candidate_pool.csv")))

# ---------------------------------------------------------------- recorded inputs
# official ce0e357 ledger (raw/official_ce0e357/BASELINE_SEEDS_0-5.csv)
found_official = [13, 12, 17, 17, 19, 15]           # sum 93
decoys_acc     = [17, 18, 21, 22, 18, 22]           # sum 118
bg_supp        = [0, 1, 1, 0, 0, 1]                 # sum 3
asked_official = [55, 53, 60, 53, 60, 60]           # sum 341

snap  = [r for r in rows if r["detector_state"] == "snapshot"]
ro    = [r for r in rows if r["detector_state"] == "rule_only"]
snap_asked = [r for r in snap if r["asked"] == "1"]
n_asked        = len(snap_asked)
gold_asked     = sum(r["class"] == "gold"  for r in snap_asked)
gold_found     = sum(r["found"] == "1"     for r in snap_asked)
decoy_asked    = sum(r["class"] == "decoy" for r in snap_asked)
other_asked    = sum(r["class"] == "other" for r in snap_asked)
assert n_asked == sum(asked_official) == 341 and gold_found == sum(found_official) == 93

a_d  = sum(decoys_acc) / decoy_asked          # decoy acceptance among asked decoys
u_s  = sum(bg_supp) / other_asked             # false discovery among asked unplanted (snapshot pool)
g_s_task = 0.29 * (93 / 98)                   # task framing: 29% gold x judging 93/98
g_s_csv  = gold_found / n_asked               # 93/341
dacc_s   = sum(decoys_acc) / n_asked          # accepted decoys per snapshot question
bg_s     = sum(bg_supp) / n_asked

def rw(r): return int(r["rule_weeks"])
F7  = [r for r in ro if rw(r) >= 7]
F11 = [r for r in ro if rw(r) >= 11]
def comp(pool):
    g = sum(r["class"] == "gold" for r in pool); d = sum(r["class"] == "decoy" for r in pool)
    o = len(pool) - g - d
    gf = sum(r["class"] == "gold" and r["F_TB_v4_feasible"] == "1" for r in pool)
    return dict(n=len(pool), g=g, d=d, o=o, gf=gf, p=g/len(pool), dd=d/len(pool), delta=d/(len(pool)-g), cg=gf/g)
C_full, C7, C11 = comp(ro), comp(F7), comp(F11)
c_g_ceiling = C7["cg"]                        # 81/138 (task: 83/142)
# recorded rule-only random arm, replay (raw/rescue/arms_seeds0-5.txt, A3 rows); yields per question
A3 = {0.10: (36, 0.9, 0.2, 100.4, 11.1), 0.25: (90, 2.3, 0.4, 84.9, 12.5),
      0.50: (180, 4.5, 0.8, 61.8, 12.0), 1.00: (360, 8.9, 1.6, 3.7, 10.1)}
q_tot = sum(v[0] for v in A3.values()); gained_tot = sum(v[2] for v in A3.values())
c_g_shipped_week = gained_tot / (C_full["p"] * q_tot)   # found per gold KEY asked at the rule layer's own week
a_d_rule_random  = A3[1.00][3] / (C_full["dd"] * 360)   # accepted per decoy key, f=1.0 arm (no snapshot questions)
u_rule_random    = A3[1.00][4] / ((1 - C_full["p"] - C_full["dd"]) * 360)
in_window        = 65 / 138                             # CANDIDATE_RESCUE §1: 65 gold rows of 2,663
c_g_mid          = in_window * c_g_ceiling              # key picked by ranker, week by rule layer, gate at feasibility

print("== recorded / derived inputs ==")
print(f"snapshot asked {n_asked}: gold {gold_asked} (found {gold_found}), decoy {decoy_asked}, other {other_asked}")
print(f"a_d  = {sum(decoys_acc)}/{decoy_asked} = {a_d:.3f}   (per-seed {[round(a/n,3) for a,n in zip(decoys_acc,[sum(r['class']=='decoy' and r['seed']==str(s) for r in snap_asked) for s in range(6)])]})")
print(f"u_s  = {sum(bg_supp)}/{other_asked} = {u_s:.3f}")
print(f"g_s  = 0.29*(93/98) = {g_s_task:.3f}  (csv 93/341 = {g_s_csv:.3f})")
print(f"accepted decoys per snapshot question = 118/341 = {dacc_s:.3f};  bg per snapshot question = {bg_s:.4f}")
for name, c in (("full", C_full), ("rw>=7", C7), ("rw>=11", C11)):
    print(f"pool {name:6s}: n={c['n']} gold={c['g']} ({c['p']:.3f}) decoy={c['d']} ({c['dd']:.3f}) other={c['o']}  delta=decoy/non-gold={c['delta']:.3f}  c_g=feasible/gold={c['gf']}/{c['g']}={c['cg']:.3f}")
print(f"c_g ceiling (ask at a feasible week)      = {c_g_ceiling:.3f}")
print(f"c_g mid (in-window 65/138 x feasibility)  = {c_g_mid:.3f}")
print(f"c_g shipped-week (A3 random, {gained_tot:.1f} gained / {C_full['p']*q_tot:.1f} gold keys asked) = {c_g_shipped_week:.3f}  (1 per {1/(C_full['p']*c_g_shipped_week):.0f} random questions)")
print(f"a_d rule-only random (A3 f=1): {A3[1.00][3]}/{C_full['dd']*360:.1f} = {a_d_rule_random:.3f};  u rule-only random = {u_rule_random:.3f}")

# per-seed caps
seeds = range(6)
Gs  = [sum(r['class']=='gold' and r['seed']==str(s) for r in F7) for s in seeds]
GFs = [sum(r['class']=='gold' and r['F_TB_v4_feasible']=='1' and r['seed']==str(s) for r in F7) for s in seeds]
N7  = [sum(r['seed']==str(s) for r in F7) for s in seeds]
N11 = [sum(r['seed']==str(s) for r in F11) for s in seeds]
print(f"per seed filtered pool {N7}, gold {Gs}, feasible gold {GFs}; rw>=11 pool {N11}")

# ---------------------------------------------------------------- closed forms
def p_star_a(g_s, c_g, lam=0.0, delta=C11["delta"], ad=a_d, dacc=dacc_s):
    num = g_s + lam * (delta * ad - dacc); den = c_g + lam * delta * ad
    return num / den
def p_star_b(c_g, lam, delta=C11["delta"], ad=a_d, kappa=0.0):
    return (kappa + lam * delta * ad) / (c_g + lam * delta * ad)

print("\n== (a) displacement budget: p*_a = (g_s + lam*(delta*a_d - 0.346)) / (c_g + lam*delta*a_d) ==")
for c_g, lab in ((c_g_ceiling, "ceiling 0.587"), (c_g_mid, "mid 0.28"), (c_g_shipped_week, "shipped-week 0.09")):
    for lam in (0.0, 0.5, 1.0):
        print(f"  c_g={lab:18s} lam={lam:3.1f}: p*_a = {p_star_a(g_s_task, c_g, lam):.3f}")
# recorded marginal displacement cost from v4 replay rank table (CANDIDATE_RESCUE §2): new patterns by rank bucket, 6 seeds, 15 ranks each
bucket = {(0,14):23, (15,29):24, (30,44):30, (45,59):41}
def g_s_marginal(N):
    lost = 0.0
    for (lo,hi),v in bucket.items():
        overlap = max(0, min(hi, 59) - max(lo, 60-N) + 1)
        lost += v * overlap / 15 / 6
    return lost / N
print("\n== p*_a(N) table: lam=0, task g_s=0.275 vs recorded marginal g_s(N) (v4 replay) ; pool cap = mean gold/N ==")
print(f"{'N':>3} {'g_s task':>9} {'p*_a':>6} {'p*_a(c_g mid)':>13} {'g_s marg':>9} {'p*_a marg':>9} {'cap G/N':>8} {'cap minseed':>11} {'feasible?':>9}")
for N in (10, 20, 30, 60):
    gm = g_s_marginal(N); cap = sum(Gs)/6/N; capmin = min(Gs)/N
    ps = p_star_a(g_s_task, c_g_ceiling); pm = p_star_a(gm, c_g_ceiling)
    print(f"{N:>3} {g_s_task:>9.3f} {ps:>6.3f} {p_star_a(g_s_task,c_g_mid):>13.3f} {gm:>9.3f} {pm:>9.3f} {min(1,cap):>8.3f} {min(1,capmin):>11.3f} {'yes' if min(1,cap)>=ps else 'NO':>9}")
print("  minimum-one-find requirement p >= 1/(N c_g):", {N: round(1/(N*c_g_ceiling),3) for N in (10,20,30,60)})

print("\n== (b) incremental budget: p*_b = (kappa + lam*delta*a_d) / (c_g + lam*delta*a_d) ==")
for c_g, lab in ((c_g_ceiling, "ceiling 0.587"), (c_g_mid, "mid 0.28"), (c_g_shipped_week, "shipped-week 0.09")):
    for lam in (0.0, 0.5, 1.0, 2.0):
        print(f"  c_g={lab:18s} lam={lam:3.1f} a_d=0.551: p*_b = {p_star_b(c_g, lam):.3f}   | a_d=0.086 (recorded rule-only): {p_star_b(c_g, lam, ad=a_d_rule_random):.3f}")
print(f"  kappa variants (lam=0, c_g=0.587): beat random asking kappa=1/220 -> {p_star_b(c_g_ceiling,0,kappa=1/220):.4f}; beat shipped avg question kappa=0.275 -> {p_star_b(c_g_ceiling,0,kappa=g_s_task):.3f}")
print("  decoy bar as hard constraint (headroom H=0 at 118/118): P(no accepted decoy in top-N) = (1 - d a_d)^N:")
for N in (10,20,30,60):
    print(f"    N={N}: d=0.213,a_d=0.551 -> {(1-0.213*a_d)**N:.3f};  d=0.213,a_d=0.086 -> {(1-0.213*a_d_rule_random)**N:.3f}")
print("  max decoy fraction d_max(N) to keep expected added accepted decoys <= H: d_max = H/(N a_d); H=0 -> 0; if H were 5 (one seed's slack):", {N: round(5/(N*a_d),3) for N in (10,20,30,60)})

# ---------------------------------------------------------------- operating point
print("\n== expected net change at the wave-0 operating point (rw>=11: p=0.135, d=0.213, o=0.652), six-seed totals (per seed x6) ==")
p0, d0 = C11["p"], C11["dd"]; o0 = 1 - p0 - d0
for lab, c_g, ad, uu in (("ceiling", c_g_ceiling, a_d, u_s), ("ceiling, c_g of >=11 gold 63/99", C11["cg"], a_d, u_s),
                         ("shipped-week recorded", c_g_shipped_week, a_d_rule_random, u_rule_random)):
    print(f"  rates: {lab}: c_g={c_g:.3f} a_d={ad:.3f} u={uu:.3f}")
    for N in (10,20,30,60):
        dGa = 6*N*(p0*c_g - g_s_task); dDa = 6*N*(d0*ad - dacc_s); dUa = 6*N*(o0*uu - bg_s)
        dGb = 6*N*(p0*c_g);            dDb = 6*N*(d0*ad);          dUb = 6*N*(o0*uu)
        print(f"    N={N:2d}  (a) dGold={dGa:+6.1f} dDecoys={dDa:+6.1f} dBG={dUa:+5.1f}   (b) dGold={dGb:+6.1f} ({dGb/240*100:+.1f} pts) dDecoys={dDb:+6.1f} dBG={dUb:+5.1f}")

# ---------------------------------------------------------------- BH-style rules
print("\n== BH-style rule 1: classical BH on rule_weeks with the LABEL-FREE marginal null (p_i = P(RW >= rw_i) over the seed's filtered pool) ==")
for q in (0.1, 0.2, 0.3):
    tot_k = tot_g = 0
    per = []
    for s in seeds:
        pool = [r for r in F7 if r['seed']==str(s)]
        m = len(pool); ws = sorted((rw(r) for r in pool), reverse=True)
        # p-value for key i: fraction of pool with rule_weeks >= rw_i (marginal null, no labels)
        pv = {}
        for w in set(ws): pv[w] = sum(1 for x in ws if x >= w) / m
        ps = sorted(((pv[rw(r)], r) for r in pool), key=lambda t: t[0])
        k = 0
        for i,(pval,r) in enumerate(ps, start=1):
            if pval <= i*q/m: k = i
        sel = ps[:k]; g = sum(r['class']=='gold' for _,r in sel)
        tot_k += k; tot_g += g; per.append((k,g))
    print(f"  q={q}: selected {tot_k} keys across 6 seeds, {tot_g} gold; per seed (k,gold) {per}")
print("  (the marginal null is conservative: it mixes 7.8% gold into the null, so real p-values are smaller; BH selects nothing because rule_weeks is a persistence statistic and its tail is 24% non-gold-decoy, 65% unplanted)")

print("\n== BH-style rule 2: prec@k >= 1-q  (ask the top-k only while the running precision of the ranking stays above 1-q) ==")
print("  gold rate by exact rule_weeks (six seeds pooled, filtered pool) — the finest calibrated statistic on record:")
byw = collections.defaultdict(lambda: [0,0,0])
for r in F7:
    b = byw[rw(r)]; b[0]+=1; b[1]+= r['class']=='gold'; b[2]+= r['class']=='decoy'
cum_n=cum_g=cum_d=0
print(f"  {'rw':>3} {'n':>4} {'gold':>4} {'decoy':>5} {'gold rate':>9} | {'cum n':>5} {'cum gold':>8} {'prec@k':>7} {'cum decoy':>9}")
for w in sorted(byw, reverse=True):
    n,g,d = byw[w]; cum_n+=n; cum_g+=g; cum_d+=d
    print(f"  {w:>3} {n:>4} {g:>4} {d:>5} {g/n:>9.3f} | {cum_n:>5} {cum_g:>8} {cum_g/cum_n:>7.3f} {cum_d:>9}")
print("  => max prec@k of the rule_weeks ranking at any k:", max((byw[w][1]/byw[w][0]) for w in byw), "-> k_q = 0 for q in {0.1,0.2,0.3} (needs prec >= 0.9/0.8/0.7)")

print("\n== BH-style rule 3: discovery-level FDR among what enters the score ==")
print("  FDR_disc(p) = [d a_d + o u] / [p c_g + d a_d + o u], with d = delta(1-p), o = (1-delta)(1-p)")
def fdr_disc(p, c_g=c_g_ceiling, delta=C11['delta'], ad=a_d, uu=u_s):
    d = delta*(1-p); o = (1-delta)*(1-p); fd = d*ad + o*uu
    return fd/(p*c_g + fd)
print(f"  baseline system itself: (118+3)/(93+118+3) = {(118+3)/(93+118+3):.3f}")
print(f"  wave-0 operating point p=0.135: FDR_disc = {fdr_disc(p0):.3f}  (with recorded rule-only a_d=0.086,u=0.034: {fdr_disc(p0, ad=a_d_rule_random, uu=u_rule_random):.3f})")
import math
for q in (0.1,0.2,0.3):
    # solve fdr_disc(p) = q by bisection
    lo,hi=0.0,1.0
    for _ in range(60):
        mid=(lo+hi)/2
        if fdr_disc(mid) > q: lo=mid
        else: hi=mid
    lo2,hi2=0.0,1.0
    for _ in range(60):
        mid=(lo2+hi2)/2
        if fdr_disc(mid, ad=a_d_rule_random, uu=u_rule_random) > q: lo2=mid
        else: hi2=mid
    print(f"  q={q}: need top-N gold precision p >= {hi:.3f} (a_d=0.551,u=0.111)   | p >= {hi2:.3f} (recorded rule-only a_d=0.086,u=0.034)")

# ---------------------------------------------------------------- oracle ceilings under (a)
print("\n== oracle ceilings under (a) (labels used only to bound, never to select) ==")
for N in (10,20,30,60):
    gold_only = sum(min(N, g)*c_g_ceiling - N*g_s_task for g in Gs)       # oracle sees gold, not feasibility
    feas_or   = sum(min(N, gf) - N*g_s_task for gf in GFs)                # oracle sees feasible gold
    print(f"  N={N:2d}: gold-oracle net {gold_only:+6.1f} ({gold_only/240*100:+.1f} pts); feasibility-oracle net {feas_or:+6.1f} ({feas_or/240*100:+.1f} pts)")
