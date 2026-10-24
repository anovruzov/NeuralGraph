"""Offline (no e2e run): regenerate a dev world with the frozen generator, run the shipped pipeline + detector with the
same pack override the recorded run used (read from its report's run.command), and dump per-key candidate facts plus
the labels doc. Code: $EMERGENCE_RULE @ 28275759 (read-only). Dev seeds only."""
import json, sys, shutil, tempfile
from pathlib import Path
from research.e2e_ingest import emergence_world as ew
from mycelic.collective.detect.detectors import detect

ew.check_world_counts = lambda *a, **k: None
report, out = sys.argv[1], sys.argv[2]
rep = json.load(open(report))
cmd = rep["run"]["command"]; seed = int(rep["run"]["seed"]); top_n = rep["run"]["top_n"]
assert seed in range(6)
over = json.loads(cmd[cmd.index("--pack-override") + 1]) if "--pack-override" in cmd else {}
WORK = Path(sys.argv[3])
pack = WORK / f"pack-{Path(report).parent.name}" / "emergence"
if pack.exists(): shutil.rmtree(pack)
shutil.copytree(ew.PACK_DIR, pack)
def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict): merge(dst[k], v)
        else: dst[k] = v
for fname, fields in over.items():
    f = pack / f"{fname}.json"; d = json.loads(f.read_text()); merge(d, fields)
    f.write_text(json.dumps(d, sort_keys=True, separators=(",", ":")))
wd = Path(tempfile.mkdtemp(dir=WORK, prefix=f"w-{seed}-"))
w = ew.build_emergence_world(200, seed, wd, pack_dir=pack, top_n=top_n)
try:
    res = detect(w.pipeline.store, as_of=w.pipeline.as_of, run_channel=ew.RUN_CHANNEL, tie_salt=ew.TIE_SALT)
finally:
    w.pipeline.close()
cands = {}
for c in res["candidates"]:
    snap = c.get("snapshot")
    rule = c.get("rule")
    cands[c["key"]] = {"snapshot_week": snap["week"] if snap else None,
                       "snapshot_source": (snap.get("source", "detector") if snap else None),
                       "supporting_sites": (snap or {}).get("supporting_sites"),
                       "candidate_weeks": c.get("candidate_weeks"),
                       "rule_weeks": (rule or {}).get("weeks") if isinstance(rule, dict) else None,
                       "channels": c.get("channels")}
asked = [c["key"] for c in w.candidates]
json.dump({"report": report, "seed": seed, "override": over, "top_n": top_n, "detected_with_snapshot": w.detected,
           "asked_keys": asked, "candidates": cands, "labels": w.labels,
           "example_candidate_fields": sorted(res["candidates"][0].keys())}, open(out, "w"))
shutil.rmtree(wd, ignore_errors=True)
print(seed, Path(report).parent.name, "candidates", len(cands), "with snapshot", w.detected, "asked", len(asked))
