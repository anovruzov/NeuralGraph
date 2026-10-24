"""F-H5 kill 1 diagnostic: WHY does H5-2 degrade at 2,000 users? Two candidate mechanisms:
 (i)  H5's own prediction: a region holds several sites, so a gold facet (one region) lights several sites in one week;
 (ii) density: 5x more background records per key, so a gold key's window picks up background cells at other sites.
For gold keys in the REAL rule-only pool at 2,000 (and at 200 from H5's real cells), inside the longest-run window:
 - weeks with >= 2 active sites: share where ALL co-active sites lie in ONE region (mechanism i) vs span regions (ii)
 - sync recomputed at REGION granularity (sites collapsed to their region): if (i) is the whole story this restores 200-level AUC
Labels score only."""
import sys, json, statistics
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *
from collections import defaultdict, Counter

def analyse(size, seeds, loader):
    allrows = []; same = cross = 0; gold_sync_site = []; gold_sync_region = []
    for seed in seeds:
        cells, cands, labels, reg_of_site = loader(seed)
        gold_keys = {p["key"] for p in labels["patterns"]}
        dcls = defaultdict(set)
        for x in labels["decoys"]:
            for k in x["keys"]: dcls[k].add(int(x["class"]))
        for key, v in cands.items():
            if v["channels"] != ["rule"]: continue
            rw = sorted(widx(w) for w in (v["rule_weeks"] or []))
            if len(rw) < 7: continue
            lab = "gold" if key in gold_keys else ("decoy" if key in dcls else "other")
            sw = site_weeks(cells.get(key, {}), True)
            lo, hi = longest_window(rw)
            act = defaultdict(set)
            for s, d in sw.items():
                for w in d:
                    if lo <= w <= hi: act[w].add(s)
            sizes = [len(v) for v in act.values()]
            sync_site = sum(sizes) / len(sizes) if sizes else 0.0
            rsizes = [len({reg_of_site.get(s, s) for s in v}) for v in act.values()]
            sync_reg = sum(rsizes) / len(rsizes) if rsizes else 0.0
            if lab == "gold":
                for w, ss in act.items():
                    if len(ss) >= 2:
                        if len({reg_of_site.get(s, s) for s in ss}) == 1: same += 1
                        else: cross += 1
            allrows.append({"seed": seed, "key": key, "label": lab, "dclass": tuple(sorted(dcls[key])) if lab == "decoy" else (),
                            "sync_site": sync_site, "sync_reg": sync_reg})
    print(f"\n== size {size}: gold co-active weeks (>=2 sites) inside the window: all-in-one-region {same} vs spanning regions {cross} "
          f"({same/(same+cross):.2f} same-region)" if same + cross else f"\n== size {size}: no co-active gold weeks")
    report_auc(allrows, {(r["seed"], r["key"]): -r["sync_site"] for r in allrows}, f"neg sync, SITE granularity ({size})")
    report_auc(allrows, {(r["seed"], r["key"]): -r["sync_reg"] for r in allrows}, f"neg sync, REGION granularity ({size})")
    for lab in ("gold", "decoy", "other"):
        xs = [r["sync_site"] for r in allrows if r["label"] == lab]; ys = [r["sync_reg"] for r in allrows if r["label"] == lab]
        if xs: print(f"   {lab:6s} n={len(xs):5d} median sync site {statistics.median(xs):.3f} region {statistics.median(ys):.3f} | pass gate (<=1.25) site {sum(x<=1.25 for x in xs)/len(xs):.3f} region {sum(x<=1.25 for x in ys)/len(ys):.3f}")

def load2000(seed):
    D = json.load(open(f"{OUT}/realcells-2000-seed-{seed}.json"))
    return D["cells"], D["candidates"], D["labels"], D["region_of_site"]
def load200(seed):
    cells = json.load(open(f"{H5}/cells-real-seed-{seed}.json"))["cells"]
    d = json.load(open(DUMPS % seed))
    reg = json.load(open(f"{H5}/cells-seed-{seed}.json"))["region_of_site"]
    return cells, d["candidates"], d["labels"], reg
analyse(200, SEEDS, load200)
analyse(2000, SEEDS, load2000)
