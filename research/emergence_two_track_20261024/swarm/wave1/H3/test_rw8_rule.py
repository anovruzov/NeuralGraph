"""H3 side check: the rule-week likelihood's signal isolated to ONE pre-statable rule, no label fitting.

The pack's rules fire over an 8-week window (pack/emergence/rules.json: window_weeks=8 for all 7 rules). A key whose
rule fired for exactly 8 weeks is consistent with a single co-occurrence week carried by the window. Statistic:
    s(key) = rule_weeks, except rule_weeks == WINDOW (8) is demoted to 0.
Compared against the monotone rule_weeks baseline (wave 0) with the same sha256 tie-break. Labels score only.
Also prints the threshold table rule_weeks >= 9 vs >= 11 per seed (precision / recall / decoy rate).
"""
import csv, hashlib, statistics, collections

POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
WINDOW = 8


def tie(r): return hashlib.sha256(("e2e-ingest-v1|" + r["key"]).encode()).hexdigest()


def auc(test, score):
    P = [score(r) for r in test if r["class"] == "gold"]; N = [score(r) for r in test if r["class"] == "decoy"]
    return sum((1.0 if p > q else 0.5 if p == q else 0.0) for p in P for q in N) / (len(P) * len(N))


def main():
    rows = list(csv.DictReader(open(POOL)))
    pool = [r for r in rows if r["detector_state"] == "rule_only" and int(r["rule_weeks"]) >= 7]
    sA = lambda r: int(r["rule_weeks"])
    sW = lambda r: 0 if int(r["rule_weeks"]) == WINDOW else int(r["rule_weeks"])
    for name, sc in (("A  rule_weeks (wave-0 baseline)", sA), ("W  rule_weeks with rw==8 demoted", sW)):
        aucs, g20, d20, g50, d50 = [], [], [], [], []
        for s in range(6):
            test = [r for r in pool if int(r["seed"]) == s]
            order = sorted(test, key=lambda r: (-sc(r), tie(r)))
            aucs.append(auc(test, sc))
            g20.append(sum(r["class"] == "gold" for r in order[:20])); d20.append(sum(r["class"] == "decoy" for r in order[:20]))
            g50.append(sum(r["class"] == "gold" for r in order[:50])); d50.append(sum(r["class"] == "decoy" for r in order[:50]))
        print(name)
        print(f"  AUC gold-vs-decoy per seed {' '.join(f'{a:.3f}' for a in aucs)}  mean {statistics.mean(aucs):.3f} min {min(aucs):.3f}")
        print(f"  top-20 gold {g20} sum {sum(g20)} prec {sum(g20)/120:.3f} | decoy {d20} sum {sum(d20)} rate {sum(d20)/120:.3f}")
        print(f"  top-50 gold {g50} sum {sum(g50)} prec {sum(g50)/300:.3f} | decoy {d50} sum {sum(d50)} rate {sum(d50)/300:.3f}")
    print("\nthreshold filters per seed (kept / gold kept / gold total / precision / recall / decoy rate):")
    for thr in (11, 9):
        print(f"  rule_weeks >= {thr}")
        tot = collections.Counter()
        for s in range(6):
            test = [r for r in pool if int(r["seed"]) == s]; kept = [r for r in test if int(r["rule_weeks"]) >= thr]
            g = sum(r["class"] == "gold" for r in kept); d = sum(r["class"] == "decoy" for r in kept); G = sum(r["class"] == "gold" for r in test)
            tot.update({"kept": len(kept), "g": g, "d": d, "G": G})
            print(f"    seed {s}: kept {len(kept):3d} gold {g:2d}/{G}  prec {g/len(kept):.3f}  recall {g/G:.3f}  decoy {d/len(kept):.3f}")
        print(f"    all   : kept {tot['kept']} gold {tot['g']}/{tot['G']}  prec {tot['g']/tot['kept']:.3f}  recall {tot['g']/tot['G']:.3f}  decoy {tot['d']/tot['kept']:.3f}  gold:decoy {tot['g']/tot['d']:.2f}")
    print("  rule_weeks >= 9 excluding == 8 is the same set as >= 9; the two filters differ only in whether 9 and 10 are kept.")


if __name__ == "__main__":
    main()
