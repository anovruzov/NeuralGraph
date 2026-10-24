"""N3 — leakage artefacts: statistics that quietly use the label, and how each is detected.
 N3a 'the accidental column'   : candidate_pool.csv columns pattern / rare / found / F_TB_v4_feasible are populated only for
                                 gold; fillna(0) on any of them is the label.  Looks like 'feasibility under F/TB_v4'.
 N3b 'the fine-grained prior'  : hierarchical-Bayes-looking per-entity gold rate, fitted in-sample on the pool (target
                                 encoding).  Looks like agent 3's 'prior over rule families'.  Shown in-sample, leave-one-
                                 key-out, and leave-one-seed-out (entity vocabulary is shared across seeds, so LOSO does NOT
                                 kill it — it learns the generator's anchor list, not the world).
 N3c 'the label-chosen threshold': choose the rule_weeks cut on all six seeds (as wave 0 did), then 'validate' per seed.
                                 Quantifies how much a threshold picked with labels inflates a per-seed table vs honest LOSO.
 N3d 'the window alignment'    : rule weeks inside the label window [found_from, found_to] — reads labels.* outright."""
import sys, json, collections
import numpy as np
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H8")
import null_common as nc

rows, dumps = nc.load_pool()
gd, full = nc.subsets(rows)
GD = [rows[i] for i in gd]
y = np.array([r["gold"] for r in GD]); seed = np.array([r["seed"] for r in GD]); ent = np.array([r["entity"] for r in GD])
n1 = int(y.sum()); n0 = len(y) - n1
out = []; P = out.append


def report(name, s, note=""):
    s = np.asarray(s, float)
    per = [nc.auc(s[seed == sd], y[seed == sd]) for sd in range(6)]
    P(f"{name:58s} AUC {nc.auc(s, y):.3f} | per-seed " + " ".join(f"{a:.2f}" for a in per) + f" | p@20 {nc.prec_at_k(s, y, 20):.2f} p@50 {nc.prec_at_k(s, y, 50):.2f}" + (f"  {note}" if note else ""))


P("# N3 — leakage artefacts")
P(f"gold-vs-decoy subset n={len(y)} (gold {n1}, decoy {n0})\n")
P("## N3a — the accidental column (candidate_pool.csv)")
P("missingness by class in the filtered pool (non-empty cells / rows):")
for col in ("csv_pattern", "csv_rare", "csv_found", "csv_feasible"):
    cnt = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        cnt[r["cls"]][0] += 1 if r[col] != "" else 0; cnt[r["cls"]][1] += 1
    P(f"  {col[4:]:16s} " + "  ".join(f"{c}: {v[0]}/{v[1]}" for c, v in sorted(cnt.items())))
report("s = 1 if F_TB_v4_feasible is non-null else 0  (fillna(0) then notna)", [1.0 if r["csv_feasible"] != "" else 0.0 for r in GD], "<- the label")
report("s = F_TB_v4_feasible == 1  ('feasible under F/TB_v4')", [1.0 if r["csv_feasible"] == "1" else 0.0 for r in GD], "<- 81/138 gold flagged, 0 decoys")
report("s = rare (fillna(0))", [1.0 if r["csv_rare"] == "1" else 0.0 for r in GD], "<- rare is defined only for gold")
report("s = 1 if rare is non-null", [1.0 if r["csv_rare"] != "" else 0.0 for r in GD], "<- the label")
P("detection: READS names a column that is not in example_candidate_fields of the dump, or whose null pattern differs by class."
  " Table above: every one of these four columns is empty on 100% of decoys and non-empty on 100% of gold.\n")

P("## N3b — the fine-grained prior (per-entity gold rate, Beta-smoothed target encoding)")
A, B = 1.0, 10.0   # Beta(1,10) prior ~ the pool base rate of ~0.08


def target_encode(train_mask, apply_idx, key_fn):
    stats = collections.defaultdict(lambda: [0, 0])
    for i in np.where(train_mask)[0]:
        stats[key_fn(i)][0] += y[i]; stats[key_fn(i)][1] += 1
    return np.array([(stats[key_fn(i)][0] + A) / (stats[key_fn(i)][1] + A + B) for i in apply_idx])


allm = np.ones(len(y), bool)
# in-sample, per entity (within the same seed): the key's own label is inside its group's count
s_in = target_encode(allm, np.arange(len(y)), lambda i: (seed[i], ent[i]))
report("in-sample per-(seed,entity) gold rate, key's own label included", s_in, "<- target encoding: looks like a prior, is the label")
# leave-one-key-out: remove the key's own label from its group before scoring it.
# (i) groups formed over the gold+decoy EVALUATION SUBSET: n_group then counts "other gold/decoy keys on this entity",
#     and membership of that subset is itself label information (decoys come in multi-key groups, gold does not).
s_loo = np.empty(len(y))
grp = collections.defaultdict(lambda: [0, 0])
for i in range(len(y)): grp[(seed[i], ent[i])][0] += y[i]; grp[(seed[i], ent[i])][1] += 1
for i in range(len(y)):
    g = grp[(seed[i], ent[i])]; s_loo[i] = (g[0] - y[i] + A) / (g[1] - 1 + A + B)
report("same, leave-one-KEY-out, groups over the gold+decoy SUBSET", s_loo, "<- subset-membership leak: n_group = #other gold/decoy keys on the entity")
# (ii) honest: groups over the FULL filtered pool (other keys count, their label is 0)
grp_full = collections.defaultdict(lambda: [0, 0])
for r in rows: grp_full[(r["seed"], r["entity"])][0] += r["gold"]; grp_full[(r["seed"], r["entity"])][1] += 1
s_loo_full = np.array([(grp_full[(r["seed"], r["entity"])][0] - r["gold"] + A) / (grp_full[(r["seed"], r["entity"])][1] - 1 + A + B) for r in GD])
report("same, leave-one-KEY-out, groups over the FULL filtered pool", s_loo_full, "<- honest LOO: collapses to the sibling-count feature")
# cross-seed entity prior: fit on 5 seeds by entity NAME, apply to the 6th
s_loso = np.empty(len(y))
for sd in range(6):
    te = np.where(seed == sd)[0]
    s_loso[te] = target_encode(seed != sd, te, lambda i: ent[i])
report("LOSO per-entity-NAME gold rate (fit 5 seeds, score 6th)", s_loso, "<- a fitted lookup table of entity names")
# is that cross-seed transfer more than chance?  permutation null: shuffle labels within seed, refit LOSO by name
rng = np.random.default_rng(11); null_auc, null_p20 = [], []
y_save = y.copy()
for rep in range(400):
    yp = y_save.copy()
    for sd in range(6):
        m = np.where(seed == sd)[0]; yp[m] = y_save[rng.permutation(m)]
    y[:] = yp
    sp = np.empty(len(y))
    for sd in range(6):
        te = np.where(seed == sd)[0]; sp[te] = target_encode(seed != sd, te, lambda i: ent[i])
    null_auc.append(nc.auc(sp, yp)); null_p20.append(nc.prec_at_k(sp, yp, 20))
y[:] = y_save
null_auc = np.array(null_auc); null_p20 = np.array(null_p20)
obs_auc = nc.auc(s_loso, y); obs_p20 = nc.prec_at_k(s_loso, y, 20)
P(f"  permutation null for that LOSO entity-name prior (400 within-seed label shuffles): pooled AUC 95th {np.percentile(null_auc,95):.3f}, p = {np.mean(null_auc >= obs_auc):.3f}; "
  f"p@20 95th {np.percentile(null_p20,95):.2f}, p = {np.mean(null_p20 >= obs_p20):.3f}")
gold_anchor = {sd: set(p["anchor"] for p in dumps[sd]["labels"]["patterns"]) for sd in range(6)}
reuse = collections.Counter(a for sd in range(6) for a in gold_anchor[sd])
n_ent = len(set(r["entity"] for r in rows))
exp_distinct = n_ent * (1 - (1 - 40 / n_ent) ** 6)
P(f"  gold anchors per seed: 40 of ~{n_ent} entity names; distinct across 6 seeds: {len(reuse)} (uniform-choice expectation {exp_distinct:.0f}); in >=2 seeds: {sum(1 for v in reuse.values() if v>=2)}; in all 6: {sum(1 for v in reuse.values() if v==6)}")
P("  => the vocabulary is shared, so a per-name table fitted on five dev seeds carries over to the sixth and to the sealed seeds alike. It is a fit to labels, not an exported statistic: void under §2 whatever its LOSO number.")
# in-sample per-chain (7 groups): coarse prior; the honest version is leave-one-seed-out by chain
s_chain_in = target_encode(allm, np.arange(len(y)), lambda i: GD[i]["chain"])
s_chain_loso = np.empty(len(y))
for sd in range(6):
    te = np.where(seed == sd)[0]; s_chain_loso[te] = target_encode(seed != sd, te, lambda i: GD[i]["chain"])
report("per-chain (predicate) gold rate, in-sample (7 groups)", s_chain_in, "<- coarse target encoding: small leak")
report("per-chain gold rate, LOSO", s_chain_loso, "<- honest version of the same idea")
# the label-flip test on each
def flip_delta(fn):
    """max over keys of |s(key) with y(key) flipped - s(key)|: a legitimate statistic gives exactly 0."""
    return fn()
P("  label-flip test (does s(key) change if ONLY that key's label is flipped?):"
  f" in-sample entity prior: yes (delta = 1/(n_group+{A+B:.0f}) = {1/(1+A+B):.3f}..{1/(7+A+B):.3f}); LOO entity prior: no; LOSO entity prior: no (but a *seed*-flip changes it); per-chain in-sample: yes ({1/(len(y)/7+A+B):.4f}); hash score: no.")
P("")

P("## N3c — the label-chosen threshold (what wave 0's table is worth per seed)")
rw = np.array([r["rw_count"] for r in GD])
P("gold-vs-decoy AUC of rule_weeks count itself (monotone, no threshold): "
  f"pooled {nc.auc(rw, y):.3f}; per-seed " + " ".join(f"{nc.auc(rw[seed==sd], y[seed==sd]):.3f}" for sd in range(6)))
def best_cut(mask):
    best = None
    for t in range(7, 27):
        s = (rw[mask] >= t).astype(float); a = nc.auc(s, y[mask])
        if best is None or a > best[1]: best = (t, a)
    return best
t_all, a_all = best_cut(allm)
P(f"threshold chosen on all six seeds: rule_weeks >= {t_all} (pooled AUC {a_all:.3f}); per-seed AUC at that cut: " +
  " ".join(f"{nc.auc((rw[seed==sd]>=t_all).astype(float), y[seed==sd]):.3f}" for sd in range(6)))
loso = []
for sd in range(6):
    t, _ = best_cut(seed != sd); loso.append((t, nc.auc((rw[seed == sd] >= t).astype(float), y[seed == sd])))
P("honest LOSO (cut chosen on 5, applied to the 6th): " + " ".join(f"s{sd}:cut>={t} AUC {a:.3f}" for sd, (t, a) in enumerate(loso)) + f"; mean {np.mean([a for _, a in loso]):.3f}")
P("inflation from choosing one threshold among 20 with labels: " + f"{a_all - np.mean([a for _, a in loso]):+.3f} AUC on this sample (small here because the signal is real and monotone; it is the SAME move that makes a noise feature look good)")
# how often does a pure-noise feature with 20 candidate cuts beat the honest line?
rng = np.random.default_rng(3); fake = []
for rep in range(2000):
    z = rng.random(len(y)); best = max(nc.auc((z >= np.quantile(z, qq)).astype(float), y) for qq in np.linspace(0.05, 0.95, 19))
    fake.append(best)
fake = np.array(fake)
P(f"pure-noise feature, best of 19 quantile cuts, in-sample pooled AUC: median {np.median(fake):.3f}, 95th {np.percentile(fake,95):.3f} (vs 0.549 for one pre-registered statistic)")
P("")

P("## N3d — the window alignment (reads labels.* outright)")
win = {}
for sd in range(6):
    for p in dumps[sd]["labels"]["patterns"]: win[(sd, p["key"])] = (nc._wk(p["found_from"]), nc._wk(p["found_to"]))
    for d in dumps[sd]["labels"]["decoys"]:
        for k in d["keys"]: win.setdefault((sd, k), (nc._wk(d["found_from"]), nc._wk(d["found_to"])))
s_win = []
for r in GD:
    lo, hi = win[(r["seed"], r["key"])]
    rws = [nc._wk(w) for w in dumps[r["seed"]]["candidates"][r["key"]]["rule_weeks"]]
    s_win.append(sum(1 for w in rws if lo <= w <= hi) / len(rws))
report("fraction of rule weeks inside the key's label window", s_win, "<- uses found_from/found_to from labels: void")
P("detection: READS names found_from / found_to / facet_weeks / sites / anchor / 'window' / 'label' / grace: those live under labels.* in the dump and are not exported.")

P("")
P("## Reference (NOT a null, NOT a hypothesis): the free persistence baseline already in the exported fields")
for name, col in (("rule_weeks count", "rw_count"), ("longest contiguous rule-week run", "rw_longest"), ("rule-week span (last-first+1)", "rw_span")):
    v = np.array([r[col] for r in GD], float)
    report(f"s = {name}", v, "")
P("any new statistic must clear these, not 0.549, to be worth a question: they cost nothing and were known at wave 0.")
txt = "\n".join(out); print(txt)
open(f"{nc.OUT}/n3_result.md", "w").write(txt + "\n")
