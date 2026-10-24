"""N2 — best-of-k artefact.  k random Gaussian projections over exported fields; report the BEST one's AUC.
Two feature sets:
  F_task = exactly the fields named in the brief: rule_weeks count, candidate_weeks count, channel count, key length,
           entity-string hash buckets (16 one-hot)          [cand count and channel count are constant in this pool]
  F_ext  = F_task + rule-week shape (first, last, span, gaps, longest run) + chain one-hot (predicate) + sibling count
Three label regimes:
  REAL      labels as recorded  -> what an agent 'trying a few things' would actually see (selection + any real signal)
  PERM      labels permuted within seed -> pure selection inflation, i.i.d. keys
  PERM-ENT  labels permuted at the entity level within seed (sibling keys keep a common label draw) -> selection
            inflation when the feature space encodes entity groups (hash buckets do)
Each best-of-k is repeated REPS times to give a distribution; LOSO = pick the best projection on 5 seeds, score the 6th.
Sign-flipping (reporting max(AUC, 1-AUC)) is allowed because that is what an agent does; the no-flip number is also given."""
import sys, json
import numpy as np
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H8")
import null_common as nc

rows, dumps = nc.load_pool()
gd, full = nc.subsets(rows)
y_all = np.array([r["gold"] for r in rows]); seed_all = np.array([r["seed"] for r in rows])
ent_all = np.array([r["entity"] for r in rows])
NB = 16
chains = sorted(set(r["chain"] for r in rows))


def feats(rows, ext):
    cols, names = [], []
    def add(n, v): names.append(n); cols.append(np.asarray(v, float))
    add("rw_count", [r["rw_count"] for r in rows]); add("cw_count", [r["cw_count"] for r in rows]); add("ch_count", [r["ch_count"] for r in rows])
    add("key_len", [r["key_len"] for r in rows])
    for b in range(NB):
        add(f"ent_hash{b}", [1.0 if nc.hash_bucket(r["entity"], NB) == b else 0.0 for r in rows])
    if ext:
        for f in ("rw_first", "rw_last", "rw_span", "rw_gaps", "rw_longest", "siblings"):
            add(f, [r[f] for r in rows])
        for c in chains:
            add(f"chain={c}", [1.0 if r["chain"] == c else 0.0 for r in rows])
    X = np.stack(cols, 1)
    keep = X.std(0) > 0
    dropped = [n for n, k in zip(names, keep) if not k]
    X = X[:, keep]; names = [n for n, k in zip(names, keep) if k]
    X = (X - X.mean(0)) / X.std(0)
    return X, names, dropped


GD_rows = [rows[i] for i in gd]
y = y_all[gd]; seed = seed_all[gd]; ent = ent_all[gd]
n1 = int(y.sum()); n0 = len(y) - n1


def permute_labels(y, seed, rng):
    yp = y.copy()
    for sd in range(6):
        m = np.where(seed == sd)[0]; yp[m] = y[rng.permutation(m)]
    return yp


def permute_labels_entity(y, seed, ent, rng):
    """Within seed, permute the label MULTISET across entity groups while keeping each group's labels together as a block
    only in the sense that the group->label-block assignment is shuffled among groups of equal size."""
    yp = y.copy()
    for sd in range(6):
        m = np.where(seed == sd)[0]
        groups = {}
        for i in m: groups.setdefault(ent[i], []).append(i)
        by_size = {}
        for e, idx in groups.items(): by_size.setdefault(len(idx), []).append(idx)
        for size, glist in by_size.items():
            blocks = [y[idx].copy() for idx in glist]
            order = rng.permutation(len(blocks))
            for idx, bi in zip(glist, order): yp[idx] = blocks[bi]
    return yp


def best_of_k(X, y, seed, k, rng, flip=True):
    W = rng.standard_normal((X.shape[1], k))
    S = X @ W
    a = nc.auc_matrix(S, y)
    a_eff = np.maximum(a, 1 - a) if flip else a
    j = int(np.argmax(a_eff))
    sgn = 1.0 if (not flip or a[j] >= 0.5) else -1.0
    s = sgn * S[:, j]
    per_seed = np.array([nc.auc(s[seed == sd], y[seed == sd]) for sd in range(6)])
    return dict(auc=a_eff[j], auc_noflip=a.max(), p20=nc.prec_at_k(s, y, 20), p50=nc.prec_at_k(s, y, 50),
                max_seed_p20=max(nc.prec_at_k(s[seed == sd], y[seed == sd], 20) for sd in range(6)),
                per_seed=per_seed, W=W, j=j, sgn=sgn)


def loso_best_of_k(X, y, seed, k, rng, flip=True):
    """For each held-out seed: choose the projection with the best pooled AUC on the other five, score the held-out seed."""
    W = rng.standard_normal((X.shape[1], k)); S = X @ W
    held = []
    for sd in range(6):
        tr = seed != sd; te = seed == sd
        a = nc.auc_matrix(S[tr], y[tr]); a_eff = np.maximum(a, 1 - a) if flip else a
        j = int(np.argmax(a_eff)); sgn = 1.0 if (not flip or a[j] >= 0.5) else -1.0
        held.append(nc.auc(sgn * S[te, j], y[te]))
    return np.array(held)


REPS = 200
out = []; P = out.append
P("# N2 — best-of-k random projections over exported fields")
P(f"gold-vs-decoy subset n={len(y)} (gold {n1}, decoy {n0}); REPS={REPS} independent best-of-k draws per cell; sign-flip allowed unless stated")
res = {}
for ext, label in ((False, "F_task"), (True, "F_ext")):
    X, names, dropped = feats(GD_rows, ext)
    P(f"\n## feature set {label}: {X.shape[1]} columns = {names}" + (f"; dropped constant: {dropped}" if dropped else ""))
    # reference: single raw features, real labels (NOT a hypothesis; shows what the projections can mix)
    P("single-feature AUC on REAL labels (reference only, oriented so AUC>=0.5): " + ", ".join(f"{n}={max(a,1-a):.3f}" for n, a in zip(names, nc.auc_matrix(X, y)) if not n.startswith("ent_hash")))
    hb = nc.auc_matrix(X[:, [i for i, n in enumerate(names) if n.startswith("ent_hash")]], y)
    P(f"entity hash buckets ({NB}) single-feature AUC on REAL labels: min {hb.min():.3f} max {hb.max():.3f} (each is a random partition of entities: pure noise by construction)")
    for regime in ("REAL", "PERM", "PERM-ENT"):
        for k in (10, 50, 200):
            rng = np.random.default_rng(1000 + k)
            aucs, aucs_nf, p20s, p50s, mx, loso_min, loso_mean, loso_max, loso_all = [], [], [], [], [], [], [], [], []
            for rep in range(REPS):
                yy = y if regime == "REAL" else (permute_labels(y, seed, rng) if regime == "PERM" else permute_labels_entity(y, seed, ent, rng))
                r = best_of_k(X, yy, seed, k, rng)
                aucs.append(r["auc"]); aucs_nf.append(r["auc_noflip"]); p20s.append(r["p20"]); p50s.append(r["p50"]); mx.append(r["max_seed_p20"])
                h = loso_best_of_k(X, yy, seed, k, rng)
                loso_min.append(h.min()); loso_mean.append(h.mean()); loso_max.append(h.max()); loso_all.append(h)
            aucs = np.array(aucs); loso_all = np.array(loso_all)
            res[(label, regime, k)] = dict(auc_q=nc.q(aucs), p20_q=nc.q(p20s), p50_q=nc.q(p50s), mx_q=nc.q(mx), loso_min_q=nc.q(loso_min), loso_mean_q=nc.q(loso_mean))
            P(f"[{label} | {regime:8s} | best-of-{k:3d}] in-sample best AUC: median {np.median(aucs):.3f}, 95th {np.percentile(aucs,95):.3f}, max {aucs.max():.3f}"
              f" (no-flip median {np.median(aucs_nf):.3f}) | p@20 median {np.median(p20s):.2f} 95th {np.percentile(p20s,95):.2f} | p@50 median {np.median(p50s):.2f} 95th {np.percentile(p50s,95):.2f}"
              f" | max per-seed p@20 median {np.median(mx):.2f} 95th {np.percentile(mx,95):.2f}"
              f" || LOSO held-out AUC: mean-of-6 median {np.median(loso_mean):.3f} (95th {np.percentile(loso_mean,95):.3f}); min-of-6 median {np.median(loso_min):.3f}; max-of-6 median {np.median(loso_max):.3f}; P(all 6 held-out > 0.5) {np.mean(np.array(loso_min)>0.5):.3f}")
# one concrete 'what the agent saw' example: best-of-200 on REAL labels, F_ext, first rep
X, names, _ = feats(GD_rows, True)
rng = np.random.default_rng(7)
r = best_of_k(X, y, seed, 200, rng)
w = r["sgn"] * r["W"][:, r["j"]]
top = np.argsort(-np.abs(w))[:6]
P("\n## Worked example (F_ext, REAL labels, best-of-200, rng=7): the projection an agent would have written up")
P(f"in-sample pooled AUC {r['auc']:.3f}; per-seed AUC " + ", ".join(f"{a:.3f}" for a in r["per_seed"]) + f"; p@20 {r['p20']:.2f}; p@50 {r['p50']:.2f}; max per-seed p@20 {r['max_seed_p20']:.2f}")
P("largest |weights|: " + ", ".join(f"{names[i]}={w[i]:+.2f}" for i in top))
h = loso_best_of_k(X, y, seed, 200, np.random.default_rng(7))
P(f"same procedure under LOSO (select on 5, score the 6th): held-out per-seed AUC " + ", ".join(f"{a:.3f}" for a in h) + f"; mean {h.mean():.3f}, min {h.min():.3f}, max {h.max():.3f}")
txt = "\n".join(out); print(txt)
open(f"{nc.OUT}/n2_result.md", "w").write(txt + "\n")
json.dump({f"{a}|{b}|{c}": v for (a, b, c), v in res.items()}, open(f"{nc.OUT}/n2_result.json", "w"))
