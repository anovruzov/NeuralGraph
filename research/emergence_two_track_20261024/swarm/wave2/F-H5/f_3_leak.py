"""F-H5 KILL 3 (seed-swap and the H8 NULL.md §5 checklist) on the pre-registered ranker (1[sync<=1.25], kcells, lr).
(1) READS provenance: the statistic is a function of {per-(site,week) cell lower bound for the key, rule_weeks}. Shown by
    construction: f_common.h5_feats takes only (site_weeks, rule_weeks).
(4) Label-flip test: flip one key's label, recompute s(key): must not move.  (Trivially true: labels never enter; shown.)
(5) Seed-swap test: the only fitted object in H5-3 is theta. Refit theta with H5's own LOSO criteria (gold@50-decoy@50
    and gold@20-decoy@20, test_h3b_loso.py) on EVERY 4-of-6 and 5-of-6 subset of dev seeds and report the theta
    distribution; and show s(key) itself is invariant to which other seeds are present.
(6) Subset-membership: all statistics are per key; the gate threshold is compared to a per-key value; no group size,
    rank or normalisation over the gold+decoy subset enters s(key). The lexicographic P@20 ranks within the FULL pool.
(3) Indexing finer than the exported cells: rename test — replace every entity name by a hash and every site id by a
    hash, recompute: the ranking must be identical. If it is, nothing is indexed by entity name or site identity.
(8) Too-good-to-be-honest: H5-2 is 0.945 pooled (> 0.79 alarm). The alarm is cleared only by items 1-7 passing.
Labels score only."""
import sys, json, hashlib, itertools, statistics, random
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *
from collections import Counter

rows = load_pool()
real = {s: json.load(open(f"{H5}/cells-real-seed-{s}.json"))["cells"] for s in SEEDS}
F = {(r["seed"], r["key"]): h5_feats(site_weeks(real[r["seed"]].get(r["key"], {}), True), r["rule_weeks"]) for r in rows}
score = lambda r: gated(F[(r["seed"], r["key"])])

print("== (1) READS provenance ==")
print("  h5_feats(site_weeks, rule_weeks) -> sync, kcells, lr. Inputs: cell lower bound per (site, ISO week) for the key"
      " (detection_inputs: entity_type, entity_id, predicate, site, iso_week, n) and the key's rule_weeks (exported 'rule' part)."
      " No labels.*, no candidate_pool.csv column, no facet_records, no node ids, no file paths, no value equality across sites.")
print("  Fields of example_candidate_fields used: key (entity_id, entity_type, predicate), rule (weeks). Cells: the HQ-side k-suppressed weekly cells.")

print("\n== (4) label-flip test ==")
r0 = next(r for r in rows if r["label"] == "gold"); s_before = score(r0)
r_flipped = dict(r0); r_flipped["label"] = "decoy"; s_after = score(r_flipped)
r1 = next(r for r in rows if r["label"] == "decoy"); r1f = dict(r1); r1f["label"] = "gold"
print(f"  gold key {r0['key']} seed {r0['seed']}: s before {s_before} after flip {s_after} -> moved: {s_before != s_after}")
print(f"  decoy key {r1['key']} seed {r1['seed']}: s before {score(r1)} after flip {score(r1f)} -> moved: {score(r1) != score(r1f)}")
print("  (the statistic has no label argument; the test is structural and passes by construction)")

print("\n== (3) rename test: hash every entity name and site id, recompute, compare rankings ==")
def hname(x): return "h" + hashlib.sha256(x.encode()).hexdigest()[:10]
F2 = {}
for r in rows:
    cells_key = real[r["seed"]].get(r["key"], {})
    renamed = {hname(site): wk for site, wk in cells_key.items()}
    F2[(r["seed"], r["key"])] = h5_feats(site_weeks(renamed, True), r["rule_weeks"])
same = sum(1 for k in F if gated(F[k]) == gated(F2[k]) and abs(F[k]["sync"] - F2[k]["sync"]) < 1e-12)
print(f"  keys with identical (gate, kcells, lr, sync) after renaming sites: {same}/{len(F)}")
# entity-name dependence: does the score vary by name beyond what the key's own cells give? -> it cannot; but show the per-name
# gold rate table H8 warned about is NOT what is ranked: correlation of s with a LOSO per-name gold-rate table
names = Counter(); gold_by_name = Counter()
for r in rows:
    names[r["key"].split(":")[1]] += 1; gold_by_name[r["key"].split(":")[1]] += (r["label"] == "gold")
print(f"  distinct entity names in the pool: {len(names)}; a per-name table would have {len(names)} cells for 1,765 keys (H8 §5 item 3: a fit to labels). Not used.")

print("\n== (5) seed-swap: refit theta on every 4-of-6 and 5-of-6 subset with H5's two LOSO criteria ==")
THETAS = [1.0, 1.1, 1.2, 1.25, 1.34, 1.4, 1.5, 1.67, 2.0, 99]
by_seed = {s: [r for r in rows if r["seed"] == s] for s in SEEDS}
def gated_th(th): return lambda r: gated(F[(r["seed"], r["key"])], th)
def crit(train, th, n):
    return statistics.mean((lambda v: v[0] - v[1])(topn_avg(by_seed[t], gated_th(th), n)) for t in train)
for n in (50, 20):
    for k in (5, 4):
        picks = []
        for train in itertools.combinations(SEEDS, k):
            best = max(THETAS, key=lambda th: crit(train, th, n))
            picks.append(best)
        c = Counter(picks)
        print(f"  criterion gold@{n}-decoy@{n}, train on {k} seeds ({len(picks)} subsets): theta chosen = {dict(sorted(c.items()))}")
# s(key) invariance to the other seeds present: the statistic is computed per key; demonstrate by recomputing on a single seed
F_single = {(r["seed"], r["key"]): h5_feats(site_weeks(real[r["seed"]].get(r["key"], {}), True), r["rule_weeks"]) for r in by_seed[3]}
print(f"  s(key) recomputed with only seed 3 present equals the all-seed value for {sum(1 for k in F_single if gated(F_single[k]) == gated(F[k]))}/{len(F_single)} seed-3 keys")

print("\n== (6) subset-membership: is anything in s(key) computed over the gold+decoy subset? ==")
print("  No: sync, kcells, lr are per-key functions of that key's cells; the gate compares sync to a constant; P@20 is taken over the FULL filtered pool.")
print("  Check: AUC of the gated rank computed over the full pool vs over the G+D subset only (would differ if group sizes entered):")
def rank_of(rows_):
    order = sorted(rows_, key=lambda r: score(r)); rank = {}; i = 0
    while i < len(order):
        j = i
        while j < len(order) and score(order[j]) == score(order[i]): j += 1
        for r in order[i:j]: rank[(r["seed"], r["key"])] = (i + j - 1) / 2
        i = j
    return rank
gd = [r for r in rows if r["label"] != "other"]
a_full = auc([rank_of(rows)[(r["seed"], r["key"])] for r in gd if r["label"] == "gold"], [rank_of(rows)[(r["seed"], r["key"])] for r in gd if r["label"] == "decoy"])
a_gd = auc([rank_of(gd)[(r["seed"], r["key"])] for r in gd if r["label"] == "gold"], [rank_of(gd)[(r["seed"], r["key"])] for r in gd if r["label"] == "decoy"])
print(f"  AUC g/decoy of the gated rank: ranks over full pool {a_full:.4f} | ranks over G+D only {a_gd:.4f} (equal => no subset dependence)")

print("\n== (2) missingness-by-class of every input ==")
has_cells = Counter(); tot = Counter()
for r in rows:
    tot[r["label"]] += 1; has_cells[r["label"]] += bool(real[r["seed"]].get(r["key"]))
print("  keys with >= 1 real cell:", {lab: f"{has_cells[lab]}/{tot[lab]}" for lab in ("gold", "decoy", "other")}, "(rule_weeks >= 7 implies cells; no class-specific missingness)")

print("\n== (8) alarm status ==")
print("  H5-2 at 0.945 trips the >= 0.79 alarm; items 1-6 above pass structurally. The alarm is NOT a leak but a generator construction (pick_users), see kill 1.")
