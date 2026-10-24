"""Gate 1 at scale: the evidence ceiling (same gate model as tools/plateau/ceiling_b.py) as a function of world size.
Dev seeds only. Offline, generator + gate model; no e2e run, no labels used at inference (labels only score)."""
import sys, json
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import ceiling_b as cb
from research.e2e_ingest import emergence_world as ew
ew.check_world_counts = lambda *a: None
size = int(sys.argv[1]); seeds = [int(x) for x in sys.argv[2].split(",")]
assert all(s in range(6) for s in seeds)
orig = ew.generate
cb.ew.generate = lambda n, seed: orig(size, seed)        # run() hardcodes 200; size is the only change
out = {}
for s in seeds:
    res = cb.run(s); out[s] = res
    print(f"size {size} seed {s} done", flush=True)
for model in ("F", "K", "B"):
    for g in ("R0_full", "TB_v4"):
        tot = rare = n = nr = 0
        for res in out.values():
            tot += sum(1 for v in res.values() if v["feasible_weeks"][model][g]); n += len(res)
            rare += sum(1 for v in res.values() if v["rare"] and v["feasible_weeks"][model][g]); nr += sum(v["rare"] for v in res.values())
        print(f"SIZE {size} {model} {g:8s} all {tot}/{n}={tot/n:.3f} rare {rare}/{nr}" + (f"={rare/nr:.3f}" if nr else ""))
json.dump({str(k): v for k, v in out.items()}, open(__import__("os").path.join(__import__("os").environ.get("PLATEAU_SCRATCH", "."), f"ceiling_{size}.json"), "w"))
