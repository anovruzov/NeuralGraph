"""KILL 6 -- BREAK-EVEN with c_g MEASURED for the keys H4-3 (S_Net3, LOSO) ranks top-5 and top-20 per seed.
c_g = P(the gold key clears the gate at the week it is asked). Measured three ways from raw/plateau/ceiling_b.json:
  F  : the pattern's own facet records clear TB_v4 at the asked week (planted-evidence feasibility, H6's definition)
  K  : every record on the key clears TB_v4 at the asked week (permissive)
  B  : the H4 gate model's own tb at the asked week AND inside the gold window (what H4 called 'corrected')
Asked week = S_Net3's argmax week (H4's week rule for that score); also at H4-2's label-free week argmax P^(clear|b).
H6: p*_a = g_s / c_g (displacing, g_s = 0.275); p*_b = lambda*delta*a_d / (c_g + lambda*delta*a_d) (additive, lambda = 1).
Deflation: H4 measured its gate model x4.5 optimistic against the recorded replay; H6's recorded c_g^ship = 0.092."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f_common import *
from collections import Counter

D = load_cells(); rows = build_rows(D); per_key = score_keys(D, rows, loso); F = filtered(per_key)
CB = json.load(open(os.path.join(R, "raw/plateau/ceiling_b.json")))
G_S = 0.275; DEFL = 4.5

def feas(v, wk, model):
    p = CB[v["seed"]].get(v["pattern"]); 
    if not p: return False
    return wk in p["feasible_weeks"][model]["TB_v4"]

print("c_g measured on H4-3's top-k (S_Net3, LOSO, strict salted order) per seed, six seeds summed")
print(f"{'k':>3s} {'week rule':14s} {'q':>4s} {'gold':>4s} {'dec':>4s} {'oth':>4s} {'p':>6s} | {'cg_F':>6s} {'cg_K':>6s} {'cg_B':>6s} | {'p*a_F':>6s} {'p*a_K':>6s} {'p*a_B':>6s} | {'a_d':>5s} {'delta':>5s} {'p*b_F':>6s} | clears(a)? clears(b)? | deflated p*a_B(x4.5)")
summary = {}
for n in (5, 10, 20, 30):
    for wkrule in ("S_Net3_wk", "S_Pclear_wk"):
        gold = dec = oth = 0; fF = fK = fB = 0; dacc = 0; per = []
        for s in SEEDS:
            it = order([(k, v) for k, v in F.items() if v["seed"] == s], "S_Net3")[:n]
            for k, v in it:
                wk = v[wkrule]; tb = v[wkrule.replace("_wk", "_tb")]
                if v["cls"] == "gold":
                    gold += 1; fF += feas(v, wk, "F"); fK += feas(v, wk, "K"); fB += bool(tb and in_window(v, wk))
                elif v["cls"] == "decoy":
                    dec += 1; dacc += bool(tb and in_window(v, wk))
                else: oth += 1
        q = 6 * n; p = gold / q
        cgF, cgK, cgB = (fF / gold if gold else 0, fK / gold if gold else 0, fB / gold if gold else 0)
        pa = lambda cg: G_S / cg if cg else float("inf")
        a_d = dacc / dec if dec else 0.0; delta = dec / (dec + oth) if (dec + oth) else 0.0
        pb = lambda cg: (delta * a_d) / (cg + delta * a_d) if (cg + delta * a_d) else float("inf")
        cgB_defl = cgB / DEFL
        clears_a = p >= pa(cgF); clears_b = p >= pb(cgF)
        print(f"{n:3d} {wkrule:14s} {q:4d} {gold:4d} {dec:4d} {oth:4d} {p:6.3f} | {cgF:6.3f} {cgK:6.3f} {cgB:6.3f} | {pa(cgF):6.3f} {pa(cgK):6.3f} {pa(cgB):6.3f} | {a_d:5.2f} {delta:5.2f} {pb(cgF):6.3f} | {str(clears_a):10s} {str(clears_b):10s} | {pa(cgB_defl):6.2f}")
        summary[f"top{n}_{wkrule}"] = dict(q=q, gold=gold, decoy=dec, other=oth, precision=round(p, 3), cg_F=round(cgF, 3), cg_K=round(cgK, 3), cg_B=round(cgB, 3),
                                           pstar_a_F=round(pa(cgF), 3), pstar_a_B=round(pa(cgB), 3), a_d=round(a_d, 3), delta=round(delta, 3), pstar_b_F=round(pb(cgF), 3),
                                           clears_a_F=bool(clears_a), clears_b_F=bool(clears_b), pstar_a_B_deflated=round(pa(cgB_defl), 3), decoys_accepted=dacc)
# reference: H6's pooled c_g for the whole filtered gold set = 81/138 feasible at SOME week in window
anyF = sum(1 for v in F.values() if v["cls"] == "gold" and CB[v["seed"]].get(v["pattern"]) and CB[v["seed"]][v["pattern"]]["feasible_weeks"]["F"]["TB_v4"])
print(f"\nreference: filtered gold feasible under F at some week = {anyF}/{sum(1 for v in F.values() if v['cls']=='gold')} (H6 used 81/138 = 0.587)")
print("H6's bars: p*_a = 0.47 at c_g = 0.587 (and N must be 10-30; at N = 60 nothing clears, even an oracle); p*_b = 0.19 at lambda = 1, a_d = 0.551, delta = 0.246.")
print("Decoy bar: 118 accepted decoys with zero headroom -- any accepted decoy under budget (b) breaches it.")
json.dump(summary, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "f6_breakeven.json"), "w"), indent=1)
