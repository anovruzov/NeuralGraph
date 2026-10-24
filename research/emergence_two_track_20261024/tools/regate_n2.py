"""Offline re-gate of a finished run's ACTUAL verdicts under a stricter roots rule (N2: a site's roots count toward
min_independent_roots only if its own roots bucket reaches k). Evaluation only; changes nothing in the run."""
import json, sqlite3, glob, collections, sys
from research.e2e_ingest import emergence_world as ew
from research.routing_spike.world import candidate_label
from mycelic.collective.detect.rules import series_key
R, seed = sys.argv[1], int(sys.argv[2])
org, corpus, gold = ew.generate(200, seed); labels = ew.gold_labels(corpus, gold)
d = json.load(open(f"{R}/e2e-ingest-report.json"))["discovery"]
cons = [sqlite3.connect(f) for f in glob.glob(f"{R}/main/edge/site-*.sqlite3") if not f.endswith(".graph.sqlite3")]
def verdicts(qid):
    return [json.loads(b) for con in cons for (b,) in con.execute("SELECT body FROM verdict_log WHERE question_id=?", (qid,))]
lo = lambda b: 1 if b == "<k" else int(str(b).rstrip("+").split("-")[0])
stat = collections.Counter(); kept, lost = set(), set(); dkept, dlost = set(), set()
dec_class = {k: str(x["class"]) for x in labels["decoys"] for k in x["keys"]}
for c in d["fabric_conclusions"]:
    if c["status"] != "supported" or not c.get("lineage_ok"): continue
    key = series_key(ew.ENTITY_TYPE, str(c["entity_id"]), str(c["predicate"]))
    lab = candidate_label(key, str(c["snapshot_week"]), labels)
    counted = [b for b in verdicts(c["question_id"]) if b["verdict"] == "confirm" and b["support_bucket"] != "<k"]
    ok = sum(lo(b["roots_bucket"]) for b in counted if b["roots_bucket"] != "<k") >= 3
    stat[(lab["label"], "kept" if ok else "dropped")] += 1
    if lab["label"] == "true": (kept if ok else lost).add(lab["pattern"])
    if lab["label"] == "decoy": stat[("decoy-class-" + dec_class.get(key, "?"), "kept" if ok else "dropped")] += 1
print(json.dumps({f"{a}:{b}": n for (a, b), n in sorted(stat.items())}))
print("true patterns found:", len(kept | lost), "lost by N2:", sorted(lost - kept))
