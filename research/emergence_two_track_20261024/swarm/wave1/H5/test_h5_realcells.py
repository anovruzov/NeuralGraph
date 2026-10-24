"""Real Boundary cells for one dev seed: run the shipped pipeline exactly as tools/plateau/detect_dump.py does (no
pack override was used for the recorded dumps), then read the cells the detector itself sees
(store.detection_inputs(as_of, 'X')), and write them in the same shape as test_rebuild_cells.py's output, with the
Boundary's lower bounds ('<k' -> 1). Offline; no questions, no HQ round trip. Usage: test_h5_realcells.py SEED OUT_JSON"""
import json, sys, time, shutil, tempfile
from pathlib import Path
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
ew.check_world_counts = lambda *a, **k: None
seed, out = int(sys.argv[1]), sys.argv[2]; assert seed in range(6)
DUMP = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps/seed-%d.json" % seed
keys = set(json.load(open(DUMP))["candidates"].keys())
WORK = Path("/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/h5_world"); WORK.mkdir(exist_ok=True)
wd = Path(tempfile.mkdtemp(dir=WORK, prefix=f"w-{seed}-"))
t0 = time.time()
w = ew.build_emergence_world(200, seed, wd, top_n=60)
try:
    bundles, cells = w.pipeline.store.detection_inputs(w.pipeline.as_of, ew.RUN_CHANNEL)
    weeks = list(w.weeks); widx = {x: i for i, x in enumerate(weeks)}
    lb = lambda v: 1 if v is None else int(v)
    acc = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    chans = defaultdict(int)
    for c in cells:
        key = f"{c.entity_type}:{c.entity_id}:{c.predicate}"
        if key not in keys: continue
        chans[c.channel] += 1
        a = acc[key][(c.site, widx[c.iso_week])]
        a[0] += lb(c.n); a[1] += lb(c.n_roots); a[2] += lb(c.n_reporters); a[3] += lb(c.n)   # X combines its channels' cells
    outc = {}
    for key, d in acc.items():
        per = defaultdict(dict)
        for (s, wk), a in d.items(): per[s][str(wk)] = a
        outc[key] = per
    json.dump({"seed": seed, "weeks": weeks, "sites": list(w.site_ids), "cells": outc, "channels": dict(chans),
               "n_cells_total": len(cells)}, open(out, "w"))
    print("seed", seed, "cells", len(cells), "keys with cells", len(outc), "of", len(keys), "channels", dict(chans), "secs", round(time.time() - t0, 1), flush=True)
finally:
    w.pipeline.close()
shutil.rmtree(wd, ignore_errors=True)
