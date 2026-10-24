"""H5-3: shape-gated count. The synchrony statistic (sites co-active per active week) kills decoys but cannot see
gold against background; the count inside the window (k-cells) sees gold against background but not against decoys.
Composite rankers, with leave-one-seed-out tuning of the gate threshold where a threshold exists."""
import sys, json, random, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5")
from h5_common import *
rows = load_pool()
F = {tuple([int(k.split("|")[0]), k.split("|")[1]]): v for k, v in json.load(open(f"{OUT}/features_h2.json")).items()}
sync = lambda r: F[(r["seed"], r["key"])]["mean_sites_per_week_win"]
kc = lambda r: F[(r["seed"], r["key"])]["n_kcells_win"]
lr = lambda r: F[(r["seed"], r["key"])]["longest_run"]
nsw = lambda r: F[(r["seed"], r["key"])]["n_sync_weeks_win"]

print("== per-seed AUC gold-vs-decoy of the untuned synchrony statistic (neg mean_sites_per_week_win) ==")
for s in SEEDS:
    sub = [r for r in rows if r["seed"] == s]
    g = [-sync(r) for r in sub if r["label"] == "gold"]; d = [-sync(r) for r in sub if r["label"] == "decoy"]; o = [-sync(r) for r in sub if r["label"] == "other"]
    print(f"  seed {s}: n_gold {len(g)} n_decoy {len(d)}  AUC g/decoy {auc(g, d):.3f}  g/other {auc(g, o):.3f}")

def topn_stats(sub, score, n, tie_seeds=20):
    g = d = fg = rg = 0.0
    for t in range(tie_seeds):
        rnd = random.Random(7919 * t + sub[0]["seed"])
        order = sorted(sub, key=lambda r: (tuple(-x for x in score(r)), rnd.random()))[:n]
        g += sum(r["label"] == "gold" for r in order); d += sum(r["label"] == "decoy" for r in order)
        fg += sum(r["label"] == "gold" and r["feasible"] for r in order); rg += sum(r["label"] == "gold" and r["rare"] for r in order)
    return g / tie_seeds, d / tie_seeds, fg / tie_seeds, rg / tie_seeds

def evaluate(name, score, rows_=rows):
    """score(r) -> tuple (lexicographic, higher = more gold-like)."""
    sc = {(r["seed"], r["key"]): score(r) for r in rows_}
    # AUC on the first component only when it is scalar-ish: use lexicographic rank instead
    order = sorted(rows_, key=lambda r: sc[(r["seed"], r["key"])])
    rank = {}; i = 0
    while i < len(order):
        j = i
        while j < len(order) and sc[(order[j]["seed"], order[j]["key"])] == sc[(order[i]["seed"], order[i]["key"])]: j += 1
        for r in order[i:j]: rank[(r["seed"], r["key"])] = (i + j - 1) / 2
        i = j
    res = report_auc(rows_, rank, name)
    line20 = []; line50 = []; tot20 = [0, 0, 0, 0]; tot50 = [0, 0, 0, 0]
    for s in SEEDS:
        sub = [r for r in rows_ if r["seed"] == s]
        if not sub: continue
        a = topn_stats(sub, score, 20); b = topn_stats(sub, score, 50)
        line20.append(f"{a[0]:.0f}/{a[1]:.0f}"); line50.append(f"{b[0]:.0f}/{b[1]:.0f}")
        for i in range(4): tot20[i] += a[i]; tot50[i] += b[i]
    ns = len(set(r["seed"] for r in rows_))
    print(f"{'':28s} top-20 gold/decoy per seed {' '.join(line20)} | mean gold {tot20[0]/ns:.1f} ({tot20[0]/ns/20:.3f}) decoy {tot20[1]/ns:.1f} ({tot20[1]/ns/20:.3f}) feasible-gold {tot20[2]/ns:.1f} rare-gold {tot20[3]/ns:.1f}")
    print(f"{'':28s} top-50 gold/decoy per seed {' '.join(line50)} | mean gold {tot50[0]/ns:.1f} ({tot50[0]/ns/50:.3f}) decoy {tot50[1]/ns:.1f} ({tot50[1]/ns/50:.3f}) feasible-gold {tot50[2]/ns:.1f} rare-gold {tot50[3]/ns:.1f}")
    return res

print("\n== baselines ==")
evaluate("count (rule_weeks)", lambda r: (len(r["rule_weeks"]),))
evaluate("longest_run", lambda r: (lr(r),))
evaluate("n_kcells_win (count in window)", lambda r: (kc(r), lr(r)))
print("\n== untuned composites ==")
evaluate("ratio kcells/sync, then lr/sync", lambda r: (kc(r) / sync(r), lr(r) / sync(r)))
evaluate("lex: -sync, kcells, lr", lambda r: (-sync(r), kc(r), lr(r)))
evaluate("lex: -n_sync_weeks, kcells, lr", lambda r: (-nsw(r), kc(r), lr(r)))
evaluate("product kcells*excl_frac", lambda r: (kc(r) * F[(r["seed"], r["key"])]["excl_frac_win"], lr(r) * F[(r["seed"], r["key"])]["excl_frac_win"]))

print("\n== gated composite: keep sync <= theta, rank by (kcells, longest_run); theta fixed, all seeds (for reference) ==")
THETAS = [1.0, 1.1, 1.2, 1.25, 1.34, 1.4, 1.5, 1.67, 2.0, 99]
def gated(theta, use_k=True):
    if use_k: return lambda r: (1 if sync(r) <= theta else 0, kc(r), lr(r))
    return lambda r: (1 if sync(r) <= theta else 0, lr(r))
for th in THETAS:
    evaluate(f"gate sync<={th} (k,lr)", gated(th))
print("\n== gated, longest_run only (no k count) ==")
for th in (1.2, 1.34, 1.5):
    evaluate(f"gate sync<={th} (lr only)", gated(th, use_k=False))

print("\n== leave-one-seed-out: choose theta on 5 seeds by mean top-50 gold, score the 6th ==")
def p_at(sub, score, n): return topn_stats(sub, score, n)
for use_k, label in ((True, "(k,lr)"), (False, "(lr only)")):
    held = []
    for s in SEEDS:
        train = [r for r in rows if r["seed"] != s]; test = [r for r in rows if r["seed"] == s]
        best = None
        for th in THETAS:
            m = statistics.mean(p_at([r for r in train if r["seed"] == t], gated(th, use_k), 50)[0] for t in SEEDS if t != s)
            if best is None or m > best[0]: best = (m, th)
        th = best[1]
        a = p_at(test, gated(th, use_k), 20); b = p_at(test, gated(th, use_k), 50)
        held.append((s, th, a, b))
    print(f"LOSO {label}: " + " | ".join(f"s{s} th={th} P20 {a[0]/20:.2f}g/{a[1]/20:.2f}d P50 {b[0]/50:.2f}g/{b[1]/50:.2f}d" for s, th, a, b in held))
    g20 = [a[0] / 20 for _, _, a, _ in held]; g50 = [b[0] / 50 for _, _, _, b in held]; d20 = [a[1] / 20 for _, _, a, _ in held]; d50 = [b[1] / 50 for _, _, _, b in held]
    print(f"   held-out P@20 gold {min(g20):.2f}-{max(g20):.2f} (mean {statistics.mean(g20):.3f}) decoy mean {statistics.mean(d20):.3f} | P@50 gold {min(g50):.2f}-{max(g50):.2f} (mean {statistics.mean(g50):.3f}) decoy mean {statistics.mean(d50):.3f}")

print("\n== how many gold are reachable this way: gold in filtered pool by k-cells and feasibility ==")
from collections import Counter
print("gold k-cells>=1:", sum(1 for r in rows if r["label"] == "gold" and kc(r) >= 1), "of", sum(1 for r in rows if r["label"] == "gold"))
print("gold feasible(F/TB_v4):", sum(1 for r in rows if r["label"] == "gold" and r["feasible"]), " rare:", sum(1 for r in rows if r["label"] == "gold" and r["rare"]))
print("gold feasible & k-cells>=1:", sum(1 for r in rows if r["label"] == "gold" and r["feasible"] and kc(r) >= 1))
print("gold sync<=1.34:", sum(1 for r in rows if r["label"] == "gold" and sync(r) <= 1.34), " decoy sync<=1.34:", sum(1 for r in rows if r["label"] == "decoy" and sync(r) <= 1.34), " other:", sum(1 for r in rows if r["label"] == "other" and sync(r) <= 1.34))
