"""Seed-level funnel for R0-feasible patterns: did the detector produce a candidate on the key; with a snapshot;
inside the label window; inside top_n; and what did the run conclude."""
import json, sys, tempfile
from research.e2e_ingest import emergence_world as ew
from mycelic.collective.detect.detectors import detect
seed, report = int(sys.argv[1]), sys.argv[2]
ew.check_world_counts = lambda *a: None
feas = json.load(open(sys.argv[3]))[str(seed)]
import os
kw = {"pack_dir": os.environ["PACK_DIR"]} if os.environ.get("PACK_DIR") else {}
w = ew.build_emergence_world(200, seed, tempfile.mkdtemp(), **kw)
res = detect(w.pipeline.store, as_of=w.pipeline.as_of, run_channel=ew.RUN_CHANNEL, tie_salt=ew.TIE_SALT)
bykey = {}
for c in res["candidates"]:
    bykey.setdefault(c["key"], []).append(c)
asked = {c.key if hasattr(c, "key") else c["key"] for c in w.candidates}
found = set(json.load(open(report))["discovery"]["found_patterns"])
labs = {p["id"]: p for p in w.labels["patterns"]}
for pid, v in sorted(feas.items()):
    if v["class"] != "R0":
        continue
    lab = labs[pid]; cs = bykey.get(lab["key"], [])
    snap = [c for c in cs if c.get("snapshot")]
    weeks = sorted({c["snapshot"]["week"] if isinstance(c["snapshot"], dict) and "week" in c["snapshot"] else str(c.get("first_candidate_week")) for c in snap})
    print(pid, "rare" if lab["rare"] else "common", "found" if pid in found else "MISSED",
          f"window {lab['found_from']}..{lab['found_to']}", "cands", len(cs), "snap", len(snap), "weeks", weeks[:4],
          "asked" if lab["key"] in asked else "not-asked")
