"""KILL 3 -- THE ONLY BUDGET-NEUTRAL USE. Reorder the ALREADY-ASKED detector pool (v4 config, every candidate asked, real
verdicts in raw/rescue/pool_s{seed}.json) by S_Net3 and replay at B = 60 per seed with the Gate-0 replay tooling
(tools/rescue/common.score) -- the protocol that gave shipped 118 -> rule-hit-first 128 (e2e: 119 -> 127).
The asked WEEK is the detector's snapshot week and cannot change in a replay (the verdict exists only there), so the
pre-registered score is S_Net3 of the candidate's exported class AT ITS SNAPSHOT WEEK, LOSO-fitted on the other seeds.
Other orderings are diagnostics and are labelled as such (best-of-many applies to them)."""
import sys, os, json, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join("/home/user/ng-recovery/research/emergence_two_track_20261024", "tools", "rescue"))
from f_common import *
from common import load as load_pool, score as rescue_score
from collections import Counter
OUT = os.path.dirname(os.path.abspath(__file__)); POOLS = os.path.join(R, "raw", "rescue"); B = 60
D = load_cells(); rows = build_rows(D); per_key = score_keys(D, rows, loso)
Fits = {s: fit_tables(rows, loso(s)) for s in SEEDS}
def h(s): return hashlib.sha256(("rescue|" + s).encode()).hexdigest()      # det_rank.py's tie-break, reproduced

def score_full(pool, chosen):
    """common.score plus decoys-by-class and found-by-rarity."""
    r = rescue_score(pool, chosen); rws, meta = pool["rows"], pool["meta"]
    sup = [rws[i] for i in chosen if rws[i]["status"] == "supported" and rws[i]["lineage_ok"]]
    bycls = {}
    for x in sup:
        for d in meta["decoys"]:
            if x["key"] == d["key0"] and d["found_from"] <= x["week"] <= d["found_to"]: bycls[d["pattern"]] = d["class"]
    r["decoys_by_class"] = Counter(bycls.values()); return r

gen_cache = {}
def cells_of(s, key):
    v = D[s]["keys"].get(key)
    if v is not None: return v["cells"]
    if s not in gen_cache:
        from f_gen import World; gen_cache[s] = World(200, int(s))
    return gen_cache[s].cells_for(key)

pools = {}; feats = {}
for s in SEEDS:
    pool = load_pool(POOLS, int(s)); pools[s] = pool; widx = {wk: i for i, wk in enumerate(D[s]["weeks"])}
    for i, r in enumerate(pool["rows"]):
        if r["source"] == "rule": continue
        f = b_features(cells_of(s, r["key"]), widx[r["week"]]); c = cls_of(f)
        pk = per_key.get((s, r["key"]))
        feats[(s, i)] = dict(cls=c, net3=Fits[s]["net3"](c), net=Fits[s]["net"](c), pclear=Fits[s]["pclear"](c),
                             net3_h4=(pk["S_Net3"] if pk else Fits[s]["net3"](c)), keycls=(D[s]["keys"][r["key"]]["class"] if r["key"] in D[s]["keys"] else "?"))
print("detector pool per seed:", {s: sum(1 for r in pools[s]["rows"] if r["source"] != "rule") for s in SEEDS})

RANKS = {
    "shipped (score desc)  [det_rank reproduction]": lambda s, i, r: (-r["score"],),
    "rule hit first, then score  [Gate-0, e2e-confirmed 127]": lambda s, i, r: (-len(r["rule_ids"]), -r["score"]),
    "** S_Net3 at snapshot week, then shipped  [THE PRE-REGISTERED USE]": lambda s, i, r: (-feats[(s, i)]["net3"], -r["score"]),
    "S_Net3 H4 per-key (max over rule weeks), then shipped  [diagnostic]": lambda s, i, r: (-feats[(s, i)]["net3_h4"], -r["score"]),
    "S_Net (nu=1,0) at snapshot week, then shipped  [diagnostic]": lambda s, i, r: (-feats[(s, i)]["net"], -r["score"]),
    "P^(clear|b) at snapshot week, then shipped  [diagnostic, label-free]": lambda s, i, r: (-feats[(s, i)]["pclear"], -r["score"]),
    "rule hit first, then S_Net3@snap, then score  [diagnostic combo]": lambda s, i, r: (-len(r["rule_ids"]), -feats[(s, i)]["net3"], -r["score"]),
    "S_Net3@snap > 0 first, then rule hit, then score  [diagnostic combo]": lambda s, i, r: (-(feats[(s, i)]["net3"] > 0), -len(r["rule_ids"]), -r["score"]),
}
print(f"\n{'ordering of the asked detector pool, B=60/seed':70s} {'found/240':>9s} {'rare':>4s} {'decoys':>6s} {'bg':>3s}  per-seed  decoys by class")
results = {}
for name, key in RANKS.items():
    t = Counter(); per = []; dcls = Counter()
    for s in SEEDS:
        pool = pools[s]; rws = pool["rows"]; det = [i for i, r in enumerate(rws) if r["source"] != "rule"]
        chosen = sorted(det, key=lambda i: (*key(s, i, rws[i]), h(rws[i]["key"])))[:B]
        r = score_full(pool, chosen); per.append(len(r["found"]))
        t["found"] += len(r["found"]); t["rare"] += len(r["rare"]); t["decoys"] += r["decoys"]; t["bg"] += r["bg"]; dcls += r["decoys_by_class"]
    results[name] = dict(found=t["found"], rare=t["rare"], decoys=t["decoys"], bg=t["bg"], per_seed=per, decoys_by_class=dict(sorted(dcls.items())))
    print(f"{name:70s} {t['found']:9d} {t['rare']:4d} {t['decoys']:6d} {t['bg']:3d}  {per}  {dict(sorted(dcls.items()))}")
print("\nrecorded references: replay shipped 118 [18,15,22,24,18,21] / rule-first 128 [18,18,23,26,21,22], decoys 113, bg 13; e2e shipped 119 / rule-first 127, decoys 112 / 113.")

# AUC of the score on the asked detector pool, two label conventions
print("\nAUC gold-vs-decoy of the snapshot-week scores over the asked detector pool (all ~86/seed, no budget):")
for nm in ("net3", "net", "pclear", "net3_h4"):
    for conv in ("row label (true = gold & in-window at snapshot week)", "key class (H4's convention)"):
        pos = []; neg = []
        for s in SEEDS:
            for i, r in enumerate(pools[s]["rows"]):
                if r["source"] == "rule": continue
                lab = ("gold" if r["label"]["label"] == "true" else "decoy" if r["label"]["label"] == "decoy" else "other") if conv.startswith("row") else feats[(s, i)]["keycls"]
                (pos if lab == "gold" else neg if lab == "decoy" else []).append(feats[(s, i)][nm])
        print(f"  {nm:8s} {conv:55s} AUC {auc(pos, neg):.3f}  (n gold {len(pos)}, decoy {len(neg)})")
# what the S_Net3 ordering does to the found set: which found patterns it drops vs rule-first, and why
print("\nwhy: among the 60 chosen per seed, how many carry a positive S_Net3 at the snapshot week, and the verdict composition of positives vs non-positives over the WHOLE detector pool")
comp = Counter()
for s in SEEDS:
    for i, r in enumerate(pools[s]["rows"]):
        if r["source"] == "rule": continue
        pos = feats[(s, i)]["net3"] > 0; sup = r["status"] == "supported" and r["lineage_ok"]
        comp[(pos, r["label"]["label"], "supported" if sup else "not")] += 1
for k_, n_ in sorted(comp.items(), key=lambda kv: (-kv[0][0], kv[0][1], kv[0][2])): print(f"  net3>0={k_[0]!s:5s} label={k_[1]:10s} {k_[2]:9s} {n_}")
json.dump(results, open(os.path.join(OUT, "f3_replay.json"), "w"), indent=1, default=str)
