"""F-H5 kill 1, real path: run the shipped pipeline + detector at SIZE users exactly as swarm_out/H5/test_h5_realcells.py
(itself tools/plateau/detect_dump.py without a pack override) and dump (a) the cells the detector sees
(store.detection_inputs(as_of, 'X')), (b) the detector's own candidates: rule_weeks and channels (so the rule-only pool
is the shipped one, not a reconstruction), (c) the labels doc (scoring only). Offline: no questions, no HQ round trip,
no e2e harness. Dev seeds only. Workdir under OUT only. Usage: f_1b_realcells.py SIZE SEED OUT_JSON"""
import json, sys, time, shutil, tempfile
from pathlib import Path
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
from mycelic.collective.detect.detectors import detect
ew.check_world_counts = lambda *a, **k: None
size, seed, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]; assert seed in range(6)
WORK = Path("/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5/world_tmp"); WORK.mkdir(exist_ok=True)
wd = Path(tempfile.mkdtemp(dir=WORK, prefix=f"w-{size}-{seed}-"))
t0 = time.time(); timings = {}
w = ew.build_emergence_world(size, seed, wd, top_n=60, timings=timings)
try:
    t1 = time.time()
    res = detect(w.pipeline.store, as_of=w.pipeline.as_of, run_channel=ew.RUN_CHANNEL, tie_salt=ew.TIE_SALT)
    cands = {}
    for c in res["candidates"]:
        snap = c.get("snapshot"); rule = c.get("rule")
        cands[c["key"]] = {"snapshot_week": snap["week"] if snap else None, "candidate_weeks": c.get("candidate_weeks"),
                           "rule_weeks": (rule or {}).get("weeks") if isinstance(rule, dict) else None, "channels": c.get("channels")}
    bundles, cells = w.pipeline.store.detection_inputs(w.pipeline.as_of, ew.RUN_CHANNEL)
    weeks = list(w.weeks); widx = {x: i for i, x in enumerate(weeks)}
    lbv = lambda v: 1 if v is None else int(v)
    acc = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    for c in cells:
        key = f"{c.entity_type}:{c.entity_id}:{c.predicate}"
        a = acc[key][(c.site, widx[c.iso_week])]
        a[0] += lbv(c.n); a[1] += lbv(c.n_roots); a[2] += lbv(c.n_reporters); a[3] += lbv(c.n)
    outc = {}
    for key, d in acc.items():
        per = defaultdict(dict)
        for (s, wk), a in d.items(): per[s][str(wk)] = a
        outc[key] = per
    from research.mycelic.org import REGION, SITE
    reg_of_site = {ew.site_id_of(w.org, nid): int(w.org.ancestor_at(nid, REGION)) for nid in w.org.levels[SITE]}
    json.dump({"size": size, "seed": seed, "weeks": weeks, "sites": list(w.site_ids), "region_of_site": reg_of_site,
               "cells": outc, "candidates": cands, "labels": w.labels, "detected_with_snapshot": w.detected,
               "n_cells_total": len(cells), "n_records": len(w.records), "timings": timings,
               "secs_total": round(time.time() - t0, 1)}, open(out, "w"))
    print(f"size {size} seed {seed} records {len(w.records)} cells {len(cells)} keys-with-cells {len(outc)} candidates {len(cands)} "
          f"rule-only {sum(1 for v in cands.values() if v['channels'] == ['rule'])} with-snapshot {w.detected} "
          f"sites {len(w.site_ids)} regions {len(set(reg_of_site.values()))} timings {{{', '.join(f'{k}={v:.1f}' for k, v in timings.items())}}} "
          f"detect2 {time.time() - t1:.1f}s total {time.time() - t0:.1f}s", flush=True)
finally:
    w.pipeline.close()
shutil.rmtree(wd, ignore_errors=True)
