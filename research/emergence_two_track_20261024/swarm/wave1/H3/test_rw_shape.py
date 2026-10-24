"""H3-3 support: what the LOSO rule-week likelihood (variant C in test_bayes_posterior.py) actually learned, and its
label-free reading as a fixed 3-level band score.

  (a) per held-out fold, the LOSO posterior P(gold | rw bucket) ranking of buckets (fit on the other five seeds);
  (b) the fixed band statistic s = 2 if 9 <= rw <= 15, 1 if rw >= 16, 0 if rw <= 8  (no labels anywhere in s),
      AUC gold-vs-decoy, top-20/50, and the band filter 9..15 vs the wave-0 filter >= 11, per seed.
Chosen AFTER viewing the pooled rw x class table (bayes_posterior_rw7.txt / family_table run), so it is post hoc:
the falsifier should count it as one of ~8 variants tried by this agent.
"""
import csv, hashlib, statistics, collections

POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
RW_BUCKETS = [7, 8, 9, 10, 11, 12, 13, (14, 15), (16, 17), (18, 19), (20, 99)]
CLASSES = ("gold", "decoy", "other")


def rw_bucket(rw):
    for b in RW_BUCKETS:
        if isinstance(b, tuple):
            if b[0] <= rw <= b[1]: return b
        elif rw == b: return b
    return RW_BUCKETS[-1]


def tie(r): return hashlib.sha256(("e2e-ingest-v1|" + r["key"]).encode()).hexdigest()


def auc(test, score):
    P = [score(r) for r in test if r["class"] == "gold"]; N = [score(r) for r in test if r["class"] == "decoy"]
    return sum((1.0 if p > q else 0.5 if p == q else 0.0) for p in P for q in N) / (len(P) * len(N))


def main():
    rows = list(csv.DictReader(open(POOL)))
    pool = [r for r in rows if r["detector_state"] == "rule_only" and int(r["rule_weeks"]) >= 7]
    print("(a) LOSO posterior P(gold | rw bucket) with the pooled class prior, per held-out seed; buckets in learned order:")
    for s in range(6):
        train = [r for r in pool if int(r["seed"]) != s]
        base = {c: sum(1 for r in train if r["class"] == c) / len(train) for c in CLASSES}
        lik = {}
        for c in CLASSES:
            rs = [r for r in train if r["class"] == c]; cnt = collections.Counter(rw_bucket(int(r["rule_weeks"])) for r in rs)
            lik[c] = {b: (cnt[b] + 0.5) / (len(rs) + 0.5 * len(RW_BUCKETS)) for b in RW_BUCKETS}
        post = {b: base["gold"] * lik["gold"][b] / sum(base[c] * lik[c][b] for c in CLASSES) for b in RW_BUCKETS}
        order = sorted(RW_BUCKETS, key=lambda b: -post[b])
        print(f"  hold out {s}: " + " > ".join(f"{b if not isinstance(b, tuple) else f'{b[0]}-{b[1]}'}({post[b]:.2f})" for b in order))
    print("\n(b) fixed band score s = 2 (9<=rw<=15) / 1 (rw>=16) / 0 (rw<=8):")
    band = lambda r: 2 if 9 <= int(r["rule_weeks"]) <= 15 else (1 if int(r["rule_weeks"]) >= 16 else 0)
    aucs, g20, d20, g50, d50 = [], [], [], [], []
    for s in range(6):
        test = [r for r in pool if int(r["seed"]) == s]; order = sorted(test, key=lambda r: (-band(r), tie(r)))
        aucs.append(auc(test, band))
        g20.append(sum(r["class"] == "gold" for r in order[:20])); d20.append(sum(r["class"] == "decoy" for r in order[:20]))
        g50.append(sum(r["class"] == "gold" for r in order[:50])); d50.append(sum(r["class"] == "decoy" for r in order[:50]))
    print(f"  AUC gold-vs-decoy per seed {' '.join(f'{a:.3f}' for a in aucs)}  mean {statistics.mean(aucs):.3f} min {min(aucs):.3f} max {max(aucs):.3f}")
    print(f"  top-20 gold {g20} sum {sum(g20)} prec {sum(g20)/120:.3f} | decoy {d20} sum {sum(d20)} rate {sum(d20)/120:.3f}   (random inside the band)")
    print(f"  top-50 gold {g50} sum {sum(g50)} prec {sum(g50)/300:.3f} | decoy {d50} sum {sum(d50)} rate {sum(d50)/300:.3f}")
    print("\n  band filter 9<=rw<=15 vs wave-0 filter rw>=11, per seed (kept, gold/total, precision, recall, decoy rate, decoys per gold):")
    for name, keep in (("rw>=11 ", lambda r: int(r["rule_weeks"]) >= 11), ("9..15  ", lambda r: 9 <= int(r["rule_weeks"]) <= 15)):
        tot = collections.Counter(); line = []
        for s in range(6):
            test = [r for r in pool if int(r["seed"]) == s]; kept = [r for r in test if keep(r)]
            g = sum(r["class"] == "gold" for r in kept); d = sum(r["class"] == "decoy" for r in kept); G = sum(r["class"] == "gold" for r in test)
            tot.update({"k": len(kept), "g": g, "d": d, "G": G}); line.append(f"s{s}: {len(kept)} {g}/{G} p={g/len(kept):.3f} r={g/G:.3f} d={d/len(kept):.3f}")
        print(f"  {name}" + " | ".join(line))
        print(f"          all: kept {tot['k']} gold {tot['g']}/{tot['G']} prec {tot['g']/tot['k']:.3f} recall {tot['g']/tot['G']:.3f} decoy {tot['d']/tot['k']:.3f} decoys/gold {tot['d']/tot['g']:.2f}")
    print("\n  pooled rw x class by coarse band:")
    for lo, hi in ((7, 7), (8, 8), (9, 10), (11, 15), (16, 19), (20, 99)):
        c = collections.Counter(r["class"] for r in pool if lo <= int(r["rule_weeks"]) <= hi); n = sum(c.values())
        print(f"    rw {lo:2d}-{hi:2d}: n={n:4d} gold={c['gold']:3d} decoy={c['decoy']:3d} other={c['other']:4d}  g_rate={c['gold']/n:.3f} d_rate={c['decoy']/n:.3f} gold:decoy={c['gold']/max(c['decoy'],1):.2f}")


if __name__ == "__main__":
    main()
