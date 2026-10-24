"""H4 extraction (ONE pass, dev seeds 0-5, read-only): per pool key, the exported weekly cells HQ sees before a
question (per site, per week: count / roots / reporters, k-suppressed) and, per askable week, what the question's
answer would be (per-site window support bucket + the TB_v4 gate outcome from ceiling_b's gate model).
Model B (polarity-blind, what the shipped lexical judge counts) is primary; TB_v4 under model K is kept as a check.
Writes OUT/h4_cells.json. No label is used anywhere in this file except to copy the pool's class column through."""
import csv, json, os, sys, time
from collections import defaultdict
sys.path.insert(0, "/home/user/ng-deploy")
sys.path.insert(0, "/home/user/ng-recovery/research/emergence_two_track_20261024/tools/plateau")
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate
from mycelic.collective.edge.weeks import iso_week
from ceiling_b import site_stats, gates, K, WIN

ew.check_world_counts = lambda *a, **k: None
OUT = os.path.dirname(os.path.abspath(__file__))
POOL = "/home/user/ng-recovery/research/emergence_two_track_20261024/raw/plateau/candidate_pool.csv"
DUMPS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps"

def supp(n):            # k-suppression as a cell exports it: 0 absent, -1 for '<k' (1..k-1), else the count
    return 0 if n == 0 else (-1 if n < K else n)

def main():
    pool = defaultdict(list)
    for r in csv.DictReader(open(POOL)):
        pool[int(r["seed"])].append(r)
    out = {}
    for seed in range(6):
        t0 = time.time()
        org, corpus, gold = ew.generate(200, seed)
        sites = ew._user_sites(org)
        weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}
        recs = corpus.recs
        ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
        bykey = defaultdict(list)
        for r in range(len(recs)):
            pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
            if pp is None: continue
            for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
                if e >= 0: bykey[(e, pp)].append(r)
        dump = json.load(open(f"{DUMPS}/seed-{seed}.json"))
        res = {}
        for row in pool[seed]:
            key = row["key"]; _, eid, pred = key.split(":")
            rows = bykey.get((ent[eid], pred), [])
            # ---- exported weekly cells (pre-question view), model B: every record on the key counts
            cell_n = defaultdict(lambda: [0] * len(weeks)); cell_u = defaultdict(lambda: [set() for _ in weeks])
            cell_e = defaultdict(lambda: [set() for _ in weeks])
            for r in rows:
                rec = recs[r]; wi = widx[iso_week(received_date(int(rec["t"])))]; s = sites[int(rec["uid"])]
                cell_n[s][wi] += 1; cell_u[s][wi].add(int(rec["uid"])); cell_e[s][wi].add(int(rec["event"]))
            cells = {s: {"n": [supp(x) for x in cell_n[s]], "roots": [supp(len(x)) for x in cell_e[s]],
                         "rep": [supp(len(x)) for x in cell_u[s]]} for s in sorted(cell_n)}
            # ---- answer side: per askable week, the window-level exported buckets and the gate
            ans = {}
            for w in range(WIN - 1, len(weeks)):
                stB = site_stats(rows, recs, sites, widx, w - WIN + 1, w, blind=True)
                if not stB: continue
                gB = gates(stB, w, weeks, False)
                stK = site_stats(rows, recs, sites, widx, w - WIN + 1, w, blind=False)
                gK = gates(stK, w, weeks, False) if stK else {"TB_v4": False}
                ans[weeks[w]] = {"tb": gB["TB_v4"], "n1": gB["N1_count"], "r0": gB["R0_count"], "tbK": gK["TB_v4"],
                                 "bucket": {s: (v["records"] if v["records"] >= K else "<k") for s, v in stB.items()
                                            if v["records"] > 0},
                                 "roots": {s: supp(v["events"]) for s, v in stB.items() if v["records"] >= K},
                                 "rep": {s: supp(v["users"]) for s, v in stB.items() if v["records"] >= K}}
            d = dump["candidates"].get(key, {})
            res[key] = {"class": row["class"], "pattern": row["pattern"], "rare": row["rare"],
                        "state": row["detector_state"], "rule_weeks_n": int(row["rule_weeks"]),
                        "rule_weeks": d.get("rule_weeks") or [], "candidate_weeks": d.get("candidate_weeks") or [],
                        "snapshot_week": d.get("snapshot_week"), "channels": d.get("channels"),
                        "cells": cells, "answer": ans}
        # label windows (used only for scoring / break-even, never inside a score)
        win = {}
        for p in dump["labels"]["patterns"]: win.setdefault(p["key"], []).append([p["found_from"], p["found_to"], "gold", p["id"]])
        for dd in dump["labels"]["decoys"]:
            for k in dd["keys"]: win.setdefault(k, []).append([dd["found_from"], dd["found_to"], "decoy", dd["id"]])
        out[seed] = {"weeks": weeks, "keys": res, "windows": win}
        print("seed", seed, "keys", len(res), "secs", round(time.time() - t0, 1), flush=True)
    json.dump(out, open(os.path.join(OUT, "h4_cells.json"), "w"))
    print("wrote", os.path.join(OUT, "h4_cells.json"), os.path.getsize(os.path.join(OUT, "h4_cells.json")) // 1024, "KB")

if __name__ == "__main__":
    main()
