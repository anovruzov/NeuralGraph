"""F-H5 KILL 4 (feasibility at the asked week). H5 estimated ~75% of its top-20 gold is F/TB_v4-feasible using the pool
csv's F_TB_v4_feasible flag, which (verified: 240/240) means 'feasible at SOME week inside the label window' — not at
the week the ranker would ask. Here c_g is measured from raw/plateau/ceiling_b.json feasible_weeks against the week
H5-3 would actually pick. H5-3 never named its asked week, so every defensible label-free pick is scored:
  A  w_maxk : the week inside the longest-run window whose 6-week gate window holds the most k-sites (ranker's own best guess)
  B  hi     : the last week of the longest rule run (the window the statistic is computed on ends here)
  C  run0   : the first week of the longest rule run
  D  first  : the key's first rule week overall (what the shipped rule part records as first_week; H6's c_g^ship basis)
  E  any    : feasible at any week of the window [lo, hi] (optimistic; needs hindsight)
  F  flag   : any week in the LABEL window (= H5's 75%; reads the label window, so it is a ceiling, not a pick)
For each: c_g over the top-20 gold (theta=1.25, real cells, deterministic + 20-draw average) and over all pool gold;
then H6's p* = 0.275 / c_g vs the measured P@20 gold. Labels and ceiling_b.json score only."""
import sys, json, statistics, random
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *

rows = load_pool()
real = {s: json.load(open(f"{H5}/cells-real-seed-{s}.json"))["cells"] for s in SEEDS}
ceil = json.load(open(CEIL))
k2id = {}; win = {}
for s in SEEDS:
    d = json.load(open(DUMPS % s))
    for p in d["labels"]["patterns"]:
        k2id[(s, p["key"])] = p["id"]; win[(s, p["key"])] = (widx(p["found_from"]), widx(p["found_to"]))
F = {(r["seed"], r["key"]): h5_feats(site_weeks(real[r["seed"]].get(r["key"], {}), True), r["rule_weeks"]) for r in rows}
score = lambda r: gated(F[(r["seed"], r["key"])])
G_S = 0.275

def feas_weeks(s, key, model="F", gate="TB_v4"):
    return {widx(w) for w in ceil[str(s)][k2id[(s, key)]]["feasible_weeks"][model][gate]}

def picks(r):
    f = F[(r["seed"], r["key"])]; rw = r["rule_weeks"]
    lo, hi = f["lo"], f["hi"]
    run0 = max(lo, hi - max(runs(rw)) + 1)  # first week of the longest run
    return {"A_w_maxk": f["w_maxk"], "B_hi": hi, "C_run0": run0, "D_first": rw[0], "E_anywin": (lo, hi)}

def feasible_at(r, pick, model):
    fw = feas_weeks(r["seed"], r["key"], model)
    if isinstance(pick, tuple): return any(w in fw for w in range(pick[0], pick[1] + 1))
    return pick in fw

out = {}
for model in ("F", "K"):
    print(f"\n==================== gate model {model} / TB_v4 ====================")
    for name in ("A_w_maxk", "B_hi", "C_run0", "D_first", "E_anywin", "F_flag"):
        top_c = []; top_n = []; pool_c = pool_n = 0; inwin = inwin_n = 0; per_seed_p20 = []; per_seed_cg = []
        for s in SEEDS:
            sub = [r for r in rows if r["seed"] == s]
            # 20-draw averaged top-20 gold feasibility (H5's tie convention) + deterministic
            cg_num = cg_den = 0.0
            for t in range(20):
                rnd = random.Random(7919 * t + s)
                order = sorted(sub, key=lambda r: (tuple(-x for x in score(r)), rnd.random()))[:20]
                for r in order:
                    if r["label"] != "gold": continue
                    cg_den += 1
                    ok = r["feasible"] if name == "F_flag" else feasible_at(r, picks(r)[name], model)
                    cg_num += ok
            per_seed_p20.append(cg_den / 20 / 20); per_seed_cg.append(cg_num / cg_den if cg_den else float("nan"))
            top_c.append(cg_num / 20); top_n.append(cg_den / 20)
        for r in rows:
            if r["label"] != "gold": continue
            pool_n += 1
            pool_c += r["feasible"] if name == "F_flag" else feasible_at(r, picks(r)[name], model)
            if name not in ("E_anywin", "F_flag"):
                inwin_n += 1; w = picks(r)[name]; a, b = win[(r["seed"], r["key"])]; inwin += (a <= w <= b)
        cg_top = sum(top_c) / sum(top_n); cg_pool = pool_c / pool_n
        p20 = statistics.mean(per_seed_p20); p_star = G_S / cg_top if cg_top else float("inf")
        print(f"  pick {name:9s}: top-20 gold per seed {' '.join(f'{x:.1f}' for x in top_n)} (P@20 {p20:.3f}); feasible among them {sum(top_c):.1f}/{sum(top_n):.1f} -> c_g(top-20) = {cg_top:.3f} "
              f"[per seed {' '.join(f'{x:.2f}' for x in per_seed_cg)}] | c_g(all 138 pool gold) = {cg_pool:.3f}"
              + (f" | pick inside label window {inwin}/{inwin_n} = {inwin/inwin_n:.2f}" if inwin_n else "")
              + f" | p* = 0.275/c_g = {p_star:.3f} -> measured P@20 {p20:.3f} {'CLEARS' if p20 > p_star else 'FAILS'} (margin {p20 - p_star:+.3f})")
        out[f"{model}:{name}"] = {"cg_top20": cg_top, "cg_pool": cg_pool, "p20": p20, "p_star": p_star, "per_seed_cg": per_seed_cg, "per_seed_p20": per_seed_p20}
print("\n== how far the ranker's window sits from the feasible weeks (model F): for top-20 gold, distance in weeks from A_w_maxk to the nearest feasible week ==")
dist = []
for s in SEEDS:
    sub = [r for r in rows if r["seed"] == s]
    for r in topn_det(sub, score, 20):
        if r["label"] != "gold": continue
        fw = feas_weeks(s, r["key"], "F"); w = picks(r)["A_w_maxk"]
        dist.append(min((abs(w - x) for x in fw), default=99))
from collections import Counter
print("  distance histogram (99 = pattern has no feasible week at all):", sorted(Counter(dist).items()))
print(f"  gold in det top-20 with NO F/TB_v4-feasible week anywhere: {sum(1 for x in dist if x == 99)}/{len(dist)}")
json.dump(out, open(f"{OUT}/kill4_summary.json", "w"), indent=1)
