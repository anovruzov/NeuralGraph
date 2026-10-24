#!/usr/bin/env python
"""H6 — 10,000-draw Monte Carlo over the filtered rule-only pool (rw>=7), dev seeds 0-5.

Checks the closed form in break_even_closed_form.py. Per draw and seed it samples REAL keys:
  - top-N of a synthetic ranker with gold precision p: each slot is gold w.p. p (gold drawn without
    replacement from the seed's filtered gold, so the pool cap binds), otherwise a non-gold key drawn
    without replacement from the seed's rw>=11 non-gold set (delta = decoy/non-gold = 0.246, the wave-0 mix);
  - a gold key is FOUND iff its recorded F_TB_v4_feasible flag is 1 (ceiling mode), or w.p. c_g (rate mode);
  - a decoy key is ACCEPTED w.p. a_d; an unplanted key yields a false discovery w.p. u;
  - budget (a): N asked snapshot keys of the same seed are displaced (uniform without replacement); their
    found flags are the recorded ones; accepted decoys / bg among them are hypergeometric against the seed's
    recorded totals (17,18,21,22,18,22 accepted of 35,31,36,33,40,39 decoys asked; 0,1,1,0,0,1 bg of 5,9,5,1,1,6).
  - the wave-0 operating point is a separate arm: N keys uniform from the seed's rw>=11 bucket.
Reads only raw/plateau/candidate_pool.csv and the ledger numbers. Writes stdout only.
"""
import csv, sys, numpy as np
R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
rows = list(csv.DictReader(open(R + "/raw/plateau/candidate_pool.csv")))
DRAWS = 10_000
rng = np.random.default_rng(20261024)
decoys_acc = [17, 18, 21, 22, 18, 22]; bg_supp = [0, 1, 1, 0, 0, 1]
A_D, U_S, G_S = 118/214, 3/27, 0.29*(93/98)
SEEDS = range(6)

def rw(r): return int(r["rule_weeks"])
per = []
for s in SEEDS:
    ss = str(s)
    F7  = [r for r in rows if r["seed"]==ss and r["detector_state"]=="rule_only" and rw(r)>=7]
    F11 = [r for r in F7 if rw(r)>=11]
    gold_feas = np.array([r["F_TB_v4_feasible"]=="1" for r in F7 if r["class"]=="gold"], dtype=np.int64)
    ng11 = np.array([1 if r["class"]=="decoy" else 0 for r in F11 if r["class"]!="gold"], dtype=np.int64)  # 1=decoy, 0=other
    b11_cls = np.array([{"gold":0,"decoy":1,"other":2}[r["class"]] for r in F11]); b11_feas = np.array([r["F_TB_v4_feasible"]=="1" for r in F11], dtype=np.int64)
    snap = [r for r in rows if r["seed"]==ss and r["detector_state"]=="snapshot" and r["asked"]=="1"]
    s_found = np.array([r["found"]=="1" for r in snap], dtype=np.int64)
    s_cls   = np.array([{"gold":0,"decoy":1,"other":2}[r["class"]] for r in snap])
    per.append(dict(gold_feas=gold_feas, ng11=ng11, b11_cls=b11_cls, b11_feas=b11_feas,
                    s_found=s_found, s_cls=s_cls, Ds=int((s_cls==1).sum()), Os=int((s_cls==2).sum()),
                    As=decoys_acc[s], Bs=bg_supp[s]))

def perm_prefix_counts(flags, k):
    """flags: (M,) 0/1; k: (DRAWS,) prefix lengths. Returns count of flag==1 among the first k of a fresh
    random permutation per draw (vectorised without replacement)."""
    M = len(flags)
    order = np.argsort(rng.random((DRAWS, M)), axis=1)
    cs = np.concatenate([np.zeros((DRAWS,1), dtype=np.int64), np.cumsum(flags[order], axis=1)], axis=1)
    return cs[np.arange(DRAWS), np.minimum(k, M)]

def displaced(P, N):
    """Budget (a) losses from displacing N uniform asked snapshot questions of one seed."""
    cls = P["s_cls"]; M = len(cls)
    order = np.argsort(rng.random((DRAWS, M)), axis=1)[:, :N]
    sel_cls = cls[order]; sel_found = P["s_found"][order]
    lost_found = sel_found.sum(axis=1)
    n_dec = (sel_cls==1).sum(axis=1); n_oth = (sel_cls==2).sum(axis=1)
    lost_acc = rng.hypergeometric(P["As"], P["Ds"]-P["As"], n_dec) if P["As"]>0 else np.zeros(DRAWS, dtype=np.int64)
    lost_bg  = rng.hypergeometric(P["Bs"], P["Os"]-P["Bs"], n_oth) if P["Bs"]>0 else np.zeros(DRAWS, dtype=np.int64)
    return lost_found, lost_acc, lost_bg

def arm_ranker(p, N, mode="ceiling", c_g=None, a_d=A_D, u=U_S):
    """Synthetic ranker with gold precision p. Returns six-seed totals per draw: (gold found, decoys accepted,
    bg, realised gold slots) for the rule-only questions, plus the budget-(a) losses."""
    G = np.zeros(DRAWS, dtype=np.int64); D = np.zeros(DRAWS, dtype=np.int64); B = np.zeros(DRAWS, dtype=np.int64); K = np.zeros(DRAWS, dtype=np.int64)
    LG = np.zeros(DRAWS, dtype=np.int64); LD = np.zeros(DRAWS, dtype=np.int64); LB = np.zeros(DRAWS, dtype=np.int64)
    for P in per:
        Gs = len(P["gold_feas"])
        k_gold = np.minimum(rng.binomial(N, p, DRAWS), Gs)          # gold slots, capped by the seed's gold
        k_non  = N - k_gold
        if mode == "ceiling":
            found = perm_prefix_counts(P["gold_feas"], k_gold)
        else:
            found = rng.binomial(k_gold, c_g)
        n_dec = perm_prefix_counts(P["ng11"], k_non)                 # decoys among non-gold slots (rw>=11 mix)
        n_oth = k_non - n_dec
        G += found; D += rng.binomial(n_dec, a_d); B += rng.binomial(n_oth, u); K += k_gold
        lg, ld, lb = displaced(P, N); LG += lg; LD += ld; LB += lb
    return G, D, B, K, LG, LD, LB

def arm_wave0(N, mode="ceiling", c_g=None, a_d=A_D, u=U_S):
    """Wave-0 operating point: N keys uniform from the seed's rw>=11 bucket."""
    G = np.zeros(DRAWS, dtype=np.int64); D = np.zeros(DRAWS, dtype=np.int64); B = np.zeros(DRAWS, dtype=np.int64); K = np.zeros(DRAWS, dtype=np.int64)
    LG = np.zeros(DRAWS, dtype=np.int64); LD = np.zeros(DRAWS, dtype=np.int64); LB = np.zeros(DRAWS, dtype=np.int64)
    perseed = []
    for P in per:
        M = len(P["b11_cls"]); order = np.argsort(rng.random((DRAWS, M)), axis=1)[:, :N]
        cls = P["b11_cls"][order]; feas = P["b11_feas"][order]
        gold = (cls==0); n_gold = gold.sum(axis=1)
        found = (gold & (feas==1)).sum(axis=1) if mode=="ceiling" else rng.binomial(n_gold, c_g)
        n_dec = (cls==1).sum(axis=1); n_oth = (cls==2).sum(axis=1)
        acc = rng.binomial(n_dec, a_d); bg = rng.binomial(n_oth, u)
        lg, ld, lb = displaced(P, N)
        G += found; D += acc; B += bg; K += n_gold; LG += lg; LD += ld; LB += lb
        perseed.append((found.mean(), lg.mean(), acc.mean(), ld.mean()))
    return G, D, B, K, LG, LD, LB, perseed

def report(G, D, B, K, LG, LD, LB, N, label, closed=None):
    dGa = G - LG; dDa = D - LD; dBa = B - LB
    prec = K / (6*N)
    line = (f"{label:>34s} N={N:2d} | realised p={prec.mean():.3f} | (b) dGold {G.mean():+6.2f}±{G.std():4.2f} dDec {D.mean():+6.2f} dBG {B.mean():+5.2f} "
            f"| (a) dGold {dGa.mean():+6.2f}±{dGa.std():4.2f} P(>0)={np.mean(dGa>0):.3f} P(>=0)={np.mean(dGa>=0):.3f} dDec {dDa.mean():+6.2f} dBG {dBa.mean():+5.2f}")
    if closed is not None: line += f" | closed (a) dGold {closed:+6.2f}"
    print(line)
    return dGa.mean(), prec.mean()

print(f"DRAWS={DRAWS}; a_d={A_D:.3f} u={U_S:.3f} g_s(task)={G_S:.3f}; recorded displaced-question loss in MC = 93/341 found, 118/341 accepted")
C_G_CEIL = 81/138
print("\n=== sweep: synthetic ranker, ceiling conversion (gold found iff recorded F_TB_v4_feasible), rw>=11 non-gold mix ===")
grid = [0.0, 0.05, 0.10, 0.135, 0.20, 0.30, 0.40, 0.469, 0.50, 0.60, 0.70, 0.80, 1.0]
cross = {}
for N in (10, 20, 30, 60):
    pts = []
    for p in grid:
        res = arm_ranker(p, N)
        closed = 6*N*(min(p, 1.0)*C_G_CEIL) - 6*N*(93/341)
        m, pr = report(*res, N, f"ranker p={p:.3f}", closed=closed)
        pts.append((pr, m))
    # empirical zero crossing in realised precision
    zc = None
    for (p0,m0),(p1,m1) in zip(pts, pts[1:]):
        if m0 < 0 <= m1: zc = p0 + (0-m0)*(p1-p0)/(m1-m0); break
    cross[N] = zc
    print(f"  --> N={N}: empirical break-even (realised precision where mean dGold_a crosses 0) = {zc if zc is None else round(zc,3)}   [closed form g_s/c_g: 0.275/0.587={G_S/C_G_CEIL:.3f}; with csv g_s 93/341: {(93/341)/C_G_CEIL:.3f}]")
print()

print("=== wave-0 operating point: N uniform from rw>=11 (p=0.135, d=0.213 in expectation) ===")
for N in (10, 20, 30, 60):
    *res, perseed = arm_wave0(N)
    report(*res, N, "wave-0 rw>=11, ceiling rates")
    if N in (10, 60):
        print("      per-seed mean (found_rule, lost_snapshot, acc_rule, lost_acc):", [tuple(round(x,2) for x in t) for t in perseed])
print("--- same arm, recorded shipped-week rates: c_g=0.092, a_d=0.086, u=0.034 (A3 random replay arm) ---")
for N in (10, 20, 30, 60):
    *res, perseed = arm_wave0(N, mode="rate", c_g=0.092, a_d=0.086, u=0.034)
    report(*res, N, "wave-0 rw>=11, shipped-week rates")
print("--- same arm, mid rates: c_g=0.276 (in-window x feasibility), a_d=0.551, u=0.111 ---")
for N in (10, 20, 30, 60):
    *res, perseed = arm_wave0(N, mode="rate", c_g=0.276)
    report(*res, N, "wave-0 rw>=11, mid rates")

print("\n=== sweep under shipped-week rates (c_g=0.092 Bernoulli): is any precision enough under (a)? ===")
for N in (10, 60):
    for p in (0.5, 1.0):
        res = arm_ranker(p, N, mode="rate", c_g=0.092, a_d=0.086, u=0.034)
        report(*res, N, f"ranker p={p:.1f} shipped-week")
print("\n=== sweep under mid rates (c_g=0.276 Bernoulli) ===")
for N in (10, 20, 30):
    for p in (0.8, 0.9, 1.0):
        res = arm_ranker(p, N, mode="rate", c_g=0.276)
        report(*res, N, f"ranker p={p:.1f} mid")

print("\n=== decoy-bar check under (b) at the wave-0 point: P(added accepted decoys == 0) ===")
for N in (10, 20, 30, 60):
    G, D, B, K, LG, LD, LB, _ = arm_wave0(N)
    G2, D2, B2, K2, LG2, LD2, LB2, _ = arm_wave0(N, mode="rate", c_g=0.092, a_d=0.086, u=0.034)
    print(f"  N={N:2d}: a_d=0.551 -> P(dDec_b=0)={np.mean(D==0):.3f} (closed (1-0.213*0.551)^(6N) = {(1-0.213*A_D)**(6*N):.3f});  a_d=0.086 -> {np.mean(D2==0):.3f}")
