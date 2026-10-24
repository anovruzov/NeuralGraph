"""F-H5 KILL 2 (one formula). The pre-registered ranker (1[sync <= 1.25], n_kcells inside the window, longest_run),
theta fixed at 1.25, no re-tuning, scored on all six dev seeds at 200 users on H5's own pool (rule-only, rule_weeks >= 7)
and H5's own cells: REAL Boundary cells (swarm_out/H5/cells-real-seed-N.json) and rebuilt cells (cells-seed-N.json).
Line to beat (WAVE_1_RESULTS §3, H8 NULL.md §4 'max over 6 seeds of per-seed p@20', 95th pct of 1,000 hashed-key
rankers): EVERY seed's P@20 gold must be > 0.25 on the full filtered pool, and > 0.55 on the gold+decoy subset.
Two tie-break conventions: H5's 20-random-draw average, and the deterministic sha256(tie_salt|key) rule of
emergence_world.ordered_candidates. Also reported: H5's untuned lexicographic (-sync, kcells, lr), in case the brief's
wording meant that one. Labels score only."""
import sys, json, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *

rows = load_pool()
real = {s: json.load(open(f"{H5}/cells-real-seed-{s}.json"))["cells"] for s in SEEDS}
reb = {s: json.load(open(f"{H5}/cells-seed-{s}.json"))["cells"] for s in SEEDS}
FR = {}; FM = {}
for r in rows:
    FR[(r["seed"], r["key"])] = h5_feats(site_weeks(real[r["seed"]].get(r["key"], {}), True), r["rule_weeks"])
    FM[(r["seed"], r["key"])] = h5_feats(site_weeks(reb[r["seed"]].get(r["key"], {}), False), r["rule_weeks"])
LINE_FULL, LINE_GD = 0.25, 0.55
summary = {}
for cname, FF in (("REAL cells", FR), ("rebuilt cells", FM)):
    for rname, score in (("gated theta=1.25 (PRE-REGISTERED)", lambda r, FF=FF: gated(FF[(r["seed"], r["key"])])),
                         ("untuned lex (-sync,k,lr)", lambda r, FF=FF: lex(FF[(r["seed"], r["key"])]))):
        print(f"\n== {cname} | {rname} ==")
        res = {"full_avg": [], "full_det": [], "gd_avg": [], "gd_det": [], "decoy_full_avg": [], "decoy_gd_avg": []}
        for s in SEEDS:
            sub = [r for r in rows if r["seed"] == s]; gd = [r for r in sub if r["label"] != "other"]
            a = topn_avg(sub, score, 20); b = topn_avg(gd, score, 20)
            ad = topn_det(sub, score, 20); bd = topn_det(gd, score, 20)
            g_full_avg, g_full_det = a[0] / 20, sum(r["label"] == "gold" for r in ad) / 20
            g_gd_avg, g_gd_det = b[0] / 20, sum(r["label"] == "gold" for r in bd) / 20
            res["full_avg"].append(g_full_avg); res["full_det"].append(g_full_det); res["gd_avg"].append(g_gd_avg); res["gd_det"].append(g_gd_det)
            res["decoy_full_avg"].append(a[1] / 20); res["decoy_gd_avg"].append(b[1] / 20)
            # how many keys tie at the top boundary (sets how much the tie-break matters)
            sc = sorted((tuple(-x for x in score(r)) for r in sub)); cut = sc[19]; n_tied = sum(1 for x in sc if x == cut)
            print(f"  seed {s}: FULL pool P@20 gold avg {g_full_avg:.3f} det {g_full_det:.2f} decoy {a[1]/20:.3f} [{'>' if g_full_avg > LINE_FULL else '<='}{LINE_FULL}] | "
                  f"G+D subset P@20 gold avg {g_gd_avg:.3f} det {g_gd_det:.2f} decoy {b[1]/20:.3f} [{'>' if g_gd_avg > LINE_GD else '<='}{LINE_GD}] | pool {len(sub)} gd {len(gd)} | keys tied at rank-20 score {n_tied}")
        fa = res["full_avg"]; ga = res["gd_avg"]
        print(f"  FULL pool: min {min(fa):.3f} mean {statistics.mean(fa):.3f} max {max(fa):.3f} | seeds > {LINE_FULL}: {sum(1 for x in fa if x > LINE_FULL)}/6 (avg tie-break), {sum(1 for x in res['full_det'] if x > LINE_FULL)}/6 (det tie-break)")
        print(f"  G+D subset: min {min(ga):.3f} mean {statistics.mean(ga):.3f} max {max(ga):.3f} | seeds > {LINE_GD}: {sum(1 for x in ga if x > LINE_GD)}/6 (avg), {sum(1 for x in res['gd_det'] if x > LINE_GD)}/6 (det)")
        print(f"  decoy share at top-20: full {' '.join(f'{x:.2f}' for x in res['decoy_full_avg'])} | G+D {' '.join(f'{x:.2f}' for x in res['decoy_gd_avg'])}")
        summary[f"{cname}|{rname}"] = res
json.dump(summary, open(f"{OUT}/kill2_summary.json", "w"), indent=1)

# the precision the ranker reaches when it is NOT allowed to see the pool's rule-only restriction: not needed; the pool is exported.
# Honest extra: P@20 on the full pool when gold that has NO k-cell in the window is counted as unreachable by any count.
print("\n== reference: H8 N1 line = 95th pct of max-over-6-seeds per-seed p@20 of 1,000 hashed-key rankers: full 0.25, G+D 0.55; chance full 0.078, G+D 0.303 ==")
