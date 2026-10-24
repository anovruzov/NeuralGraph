"""Validity check: do the rebuilt cells (model K, polarity >= 0) reproduce the REAL pipeline's rule_weeks in the dumps?
Rule: at week i, >= 2 sites whose lower-bound count over weeks [i-7, i] is >= 1. Reported sites: all (none late here)."""
import sys, json
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5")
from h5_common import *
cells = {s: json.load(open(f"{OUT}/cells-seed-{s}.json"))["cells"] for s in SEEDS}
exact = sub = sup = other = 0; jac = []; n = 0
examples = []
for s in SEEDS:
    d = json.load(open(DUMPS % s))["candidates"]
    for key, v in d.items():
        rw = set(widx(w) for w in (v["rule_weeks"] or []))
        sw = cells[s].get(key, {})
        mine = set()
        for i in range(26):
            k = sum(1 for site, wk in sw.items() if any(i - 7 <= int(w) <= i and val[0] > 0 for w, val in wk.items()))
            if k >= 2: mine.add(i)
        n += 1
        if mine == rw: exact += 1
        elif mine < rw: sub += 1
        elif mine > rw: sup += 1
        else: other += 1
        jac.append(len(mine & rw) / max(1, len(mine | rw)))
        if mine != rw and len(examples) < 6: examples.append((s, key, sorted(rw), sorted(mine)))
print(f"keys {n}  exact {exact} ({exact/n:.3f})  rebuilt-subset {sub}  rebuilt-superset {sup}  other {other}  mean Jaccard {sum(jac)/len(jac):.3f}")
for e in examples: print("  mismatch", e)
