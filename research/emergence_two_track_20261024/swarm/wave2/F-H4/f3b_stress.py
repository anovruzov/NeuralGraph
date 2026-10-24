"""KILL 3 stress. (a) seed-swap INSIDE the replay: refit the S_Net3 table on 4 of the 5 training seeds and re-replay --
does 138 hold? (b) label-free STRUCTURAL orderings at the snapshot week (no table): is the gain the generator's
'gold thin / decoys broad' signature rather than expected utility? (c) look-ahead check on H4's 0.842 snapshot-pool AUC:
how often does the per-key max-over-weeks score read a week AFTER the asked week? (d) swap accounting S_Net3 vs rule-first."""
import sys, os, json, hashlib, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join("/home/user/ng-recovery/research/emergence_two_track_20261024", "tools", "rescue"))
from f_common import *
from common import load as load_pool, score as rescue_score
from collections import Counter
OUT = os.path.dirname(os.path.abspath(__file__)); POOLS = os.path.join(R, "raw", "rescue"); B = 60
D = load_cells(); rows = build_rows(D); per_key = score_keys(D, rows, loso)
def h(s): return hashlib.sha256(("rescue|" + s).encode()).hexdigest()
gen_cache = {}
def cells_of(s, key):
    v = D[s]["keys"].get(key)
    if v is not None: return v["cells"]
    if s not in gen_cache:
        from f_gen import World; gen_cache[s] = World(200, int(s))
    return gen_cache[s].cells_for(key)
pools = {s: load_pool(POOLS, int(s)) for s in SEEDS}; feat = {}
for s in SEEDS:
    widx = {wk: i for i, wk in enumerate(D[s]["weeks"])}
    for i, r in enumerate(pools[s]["rows"]):
        if r["source"] == "rule": continue
        f = b_features(cells_of(s, r["key"]), widx[r["week"]]); feat[(s, i)] = (f, cls_of(f))
def replay(keyfn, seeds=SEEDS):
    t = Counter(); per = []
    for s in seeds:
        rws = pools[s]["rows"]; det = [i for i, r in enumerate(rws) if r["source"] != "rule"]
        chosen = sorted(det, key=lambda i: (*keyfn(s, i, rws[i]), h(rws[i]["key"])))[:B]
        r = rescue_score(pools[s], chosen); per.append(len(r["found"]))
        t["found"] += len(r["found"]); t["rare"] += len(r["rare"]); t["decoys"] += r["decoys"]; t["bg"] += r["bg"]
    return t, per
def show(name, t, per): print(f"  {name:72s} found {t['found']:3d} rare {t['rare']:2d} decoys {t['decoys']:3d} bg {t['bg']:2d} {per}")

print("(a) seed-swap inside the replay: S_Net3@snapshot with the table fitted on 4 of the 5 other seeds (drop one), six-seed totals")
ref = replay(lambda s, i, r: (-fit_tables(rows, loso(s))["net3"](feat[(s, i)][1]), -r["score"])); show("5-seed LOSO fit (reference, = f3)", *ref)
Fit4 = {}
for drop in SEEDS:
    def kf(s, i, r, drop=drop):
        tr = tuple(t for t in loso(s) if t != drop)
        if (s, tr) not in Fit4: Fit4[(s, tr)] = fit_tables(rows, list(tr))
        return (-Fit4[(s, tr)]["net3"](feat[(s, i)][1]), -r["score"])
    t, per = replay(kf); show(f"4-seed fit, training seed {drop} dropped where it was a training seed", t, per)
# 3/3 split
for train, test in ((["0", "1", "2"], ["3", "4", "5"]), (["3", "4", "5"], ["0", "1", "2"])):
    F3 = fit_tables(rows, train); t, per = replay(lambda s, i, r: (-F3["net3"](feat[(s, i)][1]), -r["score"]), seeds=test)
    tr, perr = replay(lambda s, i, r: (-len(r["rule_ids"]), -r["score"]), seeds=test)
    print(f"  fit on {train}, replay {test}: S_Net3 found {t['found']} decoys {t['decoys']} {per}  vs rule-first found {tr['found']} decoys {tr['decoys']} {perr}")

print("\n(b) label-free STRUCTURAL orderings at the snapshot week (no fitted table; ties -> shipped score, then hash)")
S = {
    "nP ascending (fewer sites present first)": lambda f: (f["nP"],),
    "nP descending": lambda f: (-f["nP"],),
    "thin indicator first (nP==2 & nC==1 & roots>=3 & rep>=3 & fresh)": lambda f: (-(f["nP"] == 2 and f["nC"] == 1 and f["roots"] >= 3 and f["rep"] >= 3 and f["fresh"]),),
    "nC==1 first (exactly one certain k-site)": lambda f: (-(f["nC"] == 1),),
    "nC ascending": lambda f: (f["nC"],),
    "sumlb ascending (less exported evidence first)": lambda f: (f["sumlb"],),
    "sumlb descending": lambda f: (-f["sumlb"],),
    "(nC==1, then nP asc)": lambda f: (-(f["nC"] == 1), f["nP"]),
    "S_cert (gate certainly clears from b) first": lambda f: (-(f["count_cert"] and f["roots"] >= 3 and f["rep"] >= 3 and f["fresh"]),),
    "nU ascending (fewer uncertain sites)": lambda f: (f["nU"],),
}
res_b = {}
for name, fn in S.items():
    t, per = replay(lambda s, i, r: (*fn(feat[(s, i)][0]), -r["score"])); show(name, t, per); res_b[name] = (t["found"], t["decoys"], per)
t, per = replay(lambda s, i, r: (-len(r["rule_ids"]), feat[(s, i)][0]["nP"], -r["score"])); show("rule hit first, then nP ascending, then score", t, per)
print("  (these are 11 variants tried AFTER seeing f3; best-of-11 applies -- they are here to see whether S_Net3's gain is a structural fact)")
# random re-orderings null for the replay, for scale (det_rank reported 104.9 +- 4.6)
rng = random.Random(7); rs = []
for d in range(300):
    t, _ = replay(lambda s, i, r: (rng.random(),)); rs.append(t["found"])
rs.sort(); print(f"  random order (300 draws): mean {sum(rs)/len(rs):.1f}, 95th {rs[int(0.95*300)]}, max {rs[-1]}  (shipped 118, rule-first 128, S_Net3 138)")

print("\n(c) look-ahead in H4's per-key score on the asked (snapshot) pool: is the argmax week after the asked week?")
late = tot = 0; late_cls = Counter()
for s in SEEDS:
    for r in pools[s]["rows"]:
        if r["source"] == "rule": continue
        pk = per_key.get((s, r["key"]))
        if not pk: continue
        tot += 1
        if pk["S_Net3_wk"] > r["week"]: late += 1; late_cls[D[s]["keys"][r["key"]]["class"]] += 1
print(f"  asked detector rows: {tot}; per-key S_Net3 argmax week AFTER the snapshot week: {late} ({late/tot:.1%}), by key class {dict(late_cls)}")
print("  -> H4's snapshot-pool AUC 0.842 / 0.814 (and my 0.791 diagnostic) read cells HQ did not have when the question was asked; the at-snapshot AUC is 0.66.")

print("\n(d) swap accounting: S_Net3@snapshot vs rule-first, per seed")
for s in SEEDS:
    rws = pools[s]["rows"]; det = [i for i, r in enumerate(rws) if r["source"] != "rule"]; Fs = fit_tables(rows, loso(s))
    a = set(sorted(det, key=lambda i: (-len(rws[i]["rule_ids"]), -rws[i]["score"], h(rws[i]["key"])))[:B])
    b = set(sorted(det, key=lambda i: (-Fs["net3"](feat[(s, i)][1]), -rws[i]["score"], h(rws[i]["key"])))[:B])
    ra = rescue_score(pools[s], sorted(a)); rb = rescue_score(pools[s], sorted(b))
    out_ = a - b; in_ = b - a
    lab = lambda ids: Counter((rws[i]["label"]["label"], "sup" if rws[i]["status"] == "supported" and rws[i]["lineage_ok"] else "not") for i in ids)
    print(f"  seed {s}: questions swapped {len(in_)}/60; dropped {dict(lab(out_))}; added {dict(lab(in_))}; found {len(ra['found'])} -> {len(rb['found'])} (gained {sorted(rb['found']-ra['found'])}, lost {sorted(ra['found']-rb['found'])}); decoys {ra['decoys']} -> {rb['decoys']}")
json.dump({"structural": res_b, "random_null": {"mean": sum(rs)/len(rs), "p95": rs[int(0.95*300)], "max": rs[-1]}, "lookahead": {"rows": tot, "late": late}}, open(os.path.join(OUT, "f3b_stress.json"), "w"), indent=1, default=str)
