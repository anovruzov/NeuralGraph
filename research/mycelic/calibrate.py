"""Hyper-parameter calibration.

Every system under test gets its main operating knob tuned, on CALIBRATION
seeds (500-502) that are disjoint from the evaluation seeds (0-9), by the same
procedure and against the same objective.  The chosen values are then frozen
and applied unchanged to every evaluation run, at every scale.

This exists so that no architecture is compared at a hand-picked operating
point against another at a default one, and so that no number in the results
was chosen after looking at the evaluation seeds.
"""
from __future__ import annotations

import itertools
import json
import os
from typing import Dict, List, Tuple

import numpy as np

from .evalm import evaluate
from .models import allocation
from .org import ENT, USER
from .runner import ART, build_world, hier_cfg
from .systems import (HierRunner, central_triage, flat_rag, long_context,
                      map_reduce, recursive_summary)

CAL_SEEDS = (500, 501, 502)
CAL_SCALE = 10_000
OBJECTIVE = "average_precision"


def _mean(rows: List[Dict], key: str) -> float:
    return float(np.mean([r[key] for r in rows]))


def calibrate(scale: int = CAL_SCALE, seeds=CAL_SEEDS,
              alloc_name: str = "back-loaded") -> Dict[str, object]:
    worlds = {s: build_world(scale, s) for s in seeds}
    alloc = allocation(alloc_name)
    out: Dict[str, object] = {"scale": scale, "seeds": list(seeds),
                              "objective": OBJECTIVE, "alloc": alloc_name}

    # ---- hierarchy: triage prior weight x question budget fraction ----
    best, best_v = None, -1.0
    grid = []
    for w in (0.0, 1.2, 2.0, 3.0, 4.0):
        for qf in (0.25, 0.55, 1.0):
            vals = []
            for s in seeds:
                wd = worlds[s]
                cfg = hier_cfg(downward_retrieval=True, questions=True,
                               cross_links=False, triage_prior_weight=w,
                               question_frac=qf)
                r = HierRunner(wd.corpus, alloc, cfg, seed=s,
                               ul=wd.user_layer(alloc[USER], s),
                               near_miss=wd.near_miss).run()
                m = evaluate(wd.corpus, wd.gold, r)
                vals.append(m)
            v = _mean(vals, OBJECTIVE)
            grid.append({"triage_prior_weight": w, "question_frac": qf,
                         OBJECTIVE: round(v, 5),
                         "recall_at_100": round(_mean(vals, "recall_at_100"), 4),
                         "found": round(_mean(vals, "found_anywhere_in_register"), 4),
                         "cu": float(np.mean([x["compute_units"] for x in vals]))})
            if v > best_v:
                best_v, best = v, (w, qf)
    out["hier_grid"] = grid
    out["triage_prior_weight"] = best[0]
    out["question_frac"] = best[1]

    # ---- map-reduce: kernel object budget ----
    mr = []
    bb, bv = 900, -1.0
    for b in (300, 900, 2500, 8000):
        vals = []
        for s in seeds:
            wd = worlds[s]
            r = map_reduce(wd.corpus, alloc, s, b,
                           ul=wd.user_layer(alloc[USER], s),
                           near_miss=wd.near_miss)
            vals.append(evaluate(wd.corpus, wd.gold, r))
        v = _mean(vals, OBJECTIVE)
        mr.append({"kernel_ko_budget": b, OBJECTIVE: round(v, 5),
                   "found": round(_mean(vals, "found_anywhere_in_register"), 4),
                   "cu": float(np.mean([x["compute_units"] for x in vals]))})
        if v > bv:
            bv, bb = v, b
    out["mr_grid"] = mr
    out["mr_budget"] = bb

    # ---- centralised triage control: kernel evidence budget ----
    ct = []
    cb, cv = 0, -1.0
    for b in (0, 2000, 6000, 20000, 60000):
        vals = []
        for s in seeds:
            wd = worlds[s]
            r = central_triage(wd.corpus, alloc, s,
                               ul=wd.user_layer(alloc[USER], s),
                               near_miss=wd.near_miss, kernel_ko_cap=b)
            vals.append(evaluate(wd.corpus, wd.gold, r))
        v = _mean(vals, OBJECTIVE)
        ct.append({"kernel_ko_cap": b, OBJECTIVE: round(v, 5),
                   "found": round(_mean(vals, "found_anywhere_in_register"), 4),
                   "cu": float(np.mean([x["compute_units"] for x in vals]))})
        if v > cv:
            cv, cb = v, b
    out["ct_grid"] = ct
    out["ct_kernel_ko_cap"] = cb

    # ---- flat RAG: retrieval token budget ----
    fr = []
    fb, fv = 120_000, -1.0
    for b in (60_000, 120_000, 400_000, 1_000_000):
        vals = []
        for s in seeds:
            wd = worlds[s]
            r = flat_rag(wd.corpus, alloc[ENT], s, b, near_miss=wd.near_miss)
            vals.append(evaluate(wd.corpus, wd.gold, r))
        v = _mean(vals, OBJECTIVE)
        fr.append({"token_budget": b, OBJECTIVE: round(v, 5),
                   "found": round(_mean(vals, "found_anywhere_in_register"), 4),
                   "cu": float(np.mean([x["compute_units"] for x in vals]))})
        if v > fv:
            fv, fb = v, b
    out["rag_grid"] = fr
    out["flat_budget"] = fb

    path = os.path.join(ART, "calibration.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    return out


if __name__ == "__main__":
    import time
    t0 = time.time()
    c = calibrate()
    print(json.dumps({k: v for k, v in c.items()
                      if not k.endswith("grid")}, indent=2))
    print("\nhier grid (top 6 by objective):")
    for row in sorted(c["hier_grid"], key=lambda r: -r[OBJECTIVE])[:6]:
        print("  ", row)
    print("\ncentral-triage grid:")
    for row in c["ct_grid"]:
        print("  ", row)
    print("\nmap-reduce grid:")
    for row in c["mr_grid"]:
        print("  ", row)
    print("\nflat-rag grid:")
    for row in c["rag_grid"]:
        print("  ", row)
    print(f"\ncalibration took {time.time()-t0:.0f}s")
