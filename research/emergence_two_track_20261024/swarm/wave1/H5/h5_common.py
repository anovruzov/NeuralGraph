"""Shared loaders/metrics for H5. Labels are loaded ONLY for scoring (AUC / precision), never inside a statistic."""
import json, csv, random
from collections import defaultdict
DUMPS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps/seed-%d.json"
POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5"
SEEDS = range(6)

def widx(w):  # '2024-W05' -> 4 ; weeks 2024-W01..W26 are indices 0..25
    return int(w[-2:]) - 1

def load_pool():
    """filtered rule-only pool (channels == ['rule'], rule_weeks >= 7) with exported fields + a scoring label.
    label in {'gold','decoy','other'}; dclass = sorted tuple of decoy classes (scoring only)."""
    cls = {}; meta = {}
    for r in csv.DictReader(open(POOL)):
        cls[(int(r["seed"]), r["key"])] = r["class"]
        meta[(int(r["seed"]), r["key"])] = {"rare": r["rare"] == "1", "feasible": r["F_TB_v4_feasible"] == "1"}
    rows = []
    for s in SEEDS:
        d = json.load(open(DUMPS % s))
        dcls = defaultdict(set)
        for x in d["labels"]["decoys"]:
            for k in x["keys"]: dcls[k].add(int(x["class"]))
        for k, v in d["candidates"].items():
            if v["channels"] != ["rule"]: continue
            rw = sorted(widx(w) for w in (v["rule_weeks"] or []))
            if len(rw) < 7: continue
            lab = cls[(s, k)]
            rows.append({"seed": s, "key": k, "rule_weeks": rw, "label": lab,
                         "dclass": tuple(sorted(dcls[k])) if lab == "decoy" else (), **meta[(s, k)]})
    return rows

def auc(pos, neg):
    """Mann-Whitney AUC with ties = 0.5. pos/neg: lists of scores (higher = more gold-like)."""
    if not pos or not neg: return float("nan")
    allv = sorted(set(pos + neg)); rank = {}
    # average ranks
    import bisect
    sp = sorted(pos); sn = sorted(neg)
    n_lt = n_eq = 0.0
    for v in pos:
        lo = bisect.bisect_left(sn, v); hi = bisect.bisect_right(sn, v)
        n_lt += lo; n_eq += hi - lo
    return (n_lt + 0.5 * n_eq) / (len(pos) * len(neg))

def runs(ws):
    """maximal runs of consecutive week indices -> list of run lengths, in order."""
    if not ws: return []
    out = [1]
    for a, b in zip(ws, ws[1:]):
        if b == a + 1: out[-1] += 1
        else: out.append(1)
    return out

def precision_at(rows, score, n, tie_seeds=20):
    """per-seed precision@n (gold share, decoy share) with random tie-break averaged over tie_seeds draws."""
    out = {}
    for s in SEEDS:
        sub = [r for r in rows if r["seed"] == s]
        g = d = 0.0
        for t in range(tie_seeds):
            rnd = random.Random(1000 * s + t)
            order = sorted(sub, key=lambda r: (-score[(r["seed"], r["key"])], rnd.random()))[:n]
            g += sum(r["label"] == "gold" for r in order) / n
            d += sum(r["label"] == "decoy" for r in order) / n
        out[s] = (g / tie_seeds, d / tie_seeds)
    return out

def report_auc(rows, score, name):
    """print overall gold-vs-decoy AUC, per decoy class, gold-vs-other, gold-vs-nongold; return dict."""
    sc = lambda r: score[(r["seed"], r["key"])]
    gold = [sc(r) for r in rows if r["label"] == "gold"]
    dec = [sc(r) for r in rows if r["label"] == "decoy"]
    oth = [sc(r) for r in rows if r["label"] == "other"]
    res = {"gold_vs_decoy": auc(gold, dec), "gold_vs_other": auc(gold, oth), "gold_vs_nongold": auc(gold, dec + oth)}
    for c in (1, 2, 3, 4, 5):
        dc = [sc(r) for r in rows if r["label"] == "decoy" and c in r["dclass"]]
        res[f"gold_vs_decoy{c}"] = auc(gold, dc); res[f"n_decoy{c}"] = len(dc)
    line = f"{name:28s} AUC g/decoy {res['gold_vs_decoy']:.3f} | per class " + " ".join(
        f"c{c}:{res[f'gold_vs_decoy{c}']:.3f}(n={res[f'n_decoy{c}']})" for c in (1, 2, 3, 4, 5)) + \
        f" | g/other {res['gold_vs_other']:.3f} g/nongold {res['gold_vs_nongold']:.3f}"
    print(line)
    return res

def report_prec(rows, score, name):
    p20 = precision_at(rows, score, 20); p50 = precision_at(rows, score, 50)
    m20g = sum(v[0] for v in p20.values()) / 6; m20d = sum(v[1] for v in p20.values()) / 6
    m50g = sum(v[0] for v in p50.values()) / 6; m50d = sum(v[1] for v in p50.values()) / 6
    print(f"{name:28s} P@20 gold {m20g:.3f} decoy {m20d:.3f} per-seed gold " +
          " ".join(f"{p20[s][0]:.2f}" for s in SEEDS) + f" | P@50 gold {m50g:.3f} decoy {m50d:.3f} per-seed gold " +
          " ".join(f"{p50[s][0]:.2f}" for s in SEEDS))
    return p20, p50
