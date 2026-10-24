"""H5 one-pass world rebuild (read-only on code; dev seeds 0-5 only). Mirrors tools/plateau/ceiling_b.py's model K:
a key's rows are every record whose anchor OR aux is the key's entity and whose predicate maps to the key's chain
predicate. Per (key, site, ISO week) it stores what a weekly cell would carry before k-suppression:
  [n, n_roots, n_reporters]  over polarity >= 0 records (negated claims never enter a cell), and
  n_all                       over all polarities (model B sensitivity only).
Downstream scripts bucket these at k=3 exactly as the Boundary does ('<k' -> lb 1) and read nothing else.
No label is read here; the keys are the dump's candidate keys. Usage: test_rebuild_cells.py SEED OUT_JSON"""
import json, sys, time
from collections import defaultdict
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate
from mycelic.collective.edge.weeks import iso_week

seed, out = int(sys.argv[1]), sys.argv[2]
assert seed in range(6)
DUMP = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps/seed-%d.json" % seed
keys = list(json.load(open(DUMP))["candidates"].keys())
t0 = time.time()
org, corpus, gold = ew.generate(200, seed)
sites = ew._user_sites(org)
weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); widx = {x: i for i, x in enumerate(weeks)}
recs = corpus.recs
ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
wk = [widx[iso_week(received_date(int(r["t"])))] for r in recs]
bykey = defaultdict(list)
for r in range(len(recs)):
    pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
    if pp is None: continue
    for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
        if e >= 0: bykey[(e, pp)].append(r)
cells = {}
missing = 0
for key in keys:
    _, eid, pred = key.split(":")
    e = ent.get(eid)
    if e is None: missing += 1; continue
    acc = defaultdict(lambda: [0, set(), set(), 0])
    for r in bykey.get((e, pred), ()):
        rec = recs[r]; s = sites[int(rec["uid"])]; w = wk[r]
        a = acc[(s, w)]
        a[3] += 1
        if int(rec["polarity"]) < 0: continue
        a[0] += 1; a[1].add(int(rec["event"])); a[2].add(int(rec["uid"]))
    per = defaultdict(dict)
    for (s, w), a in acc.items():
        per[s][str(w)] = [a[0], len(a[1]), len(a[2]), a[3]]
    cells[key] = per
# world shape (no labels): sites per region
from research.mycelic.org import REGION, SITE
reg_of_site = {}
for nid in org.levels[SITE]:
    try:
        a = org.ancestor_at(nid, REGION); reg_of_site[ew.site_id_of(org, nid)] = -1 if a is None else int(a)
    except Exception:
        reg_of_site[ew.site_id_of(org, nid)] = -1
json.dump({"seed": seed, "weeks": weeks, "sites": sorted(set(sites.values())), "region_of_site": reg_of_site,
           "cells": cells, "missing_keys": missing}, open(out, "w"))
print("seed", seed, "keys", len(cells), "missing", missing, "regions", sorted(set(reg_of_site.values())),
      "sites/region", {r: sum(1 for v in reg_of_site.values() if v == r) for r in set(reg_of_site.values())},
      "secs", round(time.time() - t0, 1), flush=True)
