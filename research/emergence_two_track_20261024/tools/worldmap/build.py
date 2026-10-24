"""Build the Mycelic World Map page from e2e run reports (no record refs or text: reports never hold them).
Usage: build.py OUT.html REPORT.json [REPORT.json ...]
Optional answer-key overlay: pass --pools DIR holding pool_s{seed}.json from tools/rescue/dump_pools.py; without it
every finding is shown as 'Unlabelled' (the system itself never sees the answer key)."""
import json, sys
from pathlib import Path

args = sys.argv[1:]; pools = None
if "--pools" in args:
    i = args.index("--pools"); pools = Path(args[i + 1]); del args[i:i + 2]
out, reports = Path(args[0]), [Path(a) for a in args[1:]]
runs = []
for rp in reports:
    d = json.loads(rp.read_text()); w, x, rt = d["world"], d["discovery"], d["routing"]
    lab = {}
    if pools and (pools / f"pool_s{w['seed']}.json").exists():
        lab = {(r["key"], r["week"]): r["label"] for r in json.loads((pools / f"pool_s{w['seed']}.json").read_text())["rows"]}
    rq = {q["question_id"]: q for q in rt["questions"]}
    lin = {r["question_id"]: r for r in d["lineage"]["rows"]}
    q2v = {s["question_id"]: s["seconds"] for s in rt["question_to_verdict"]["samples"]}
    concl = []
    for c in x["fabric_conclusions"]:
        L = lab.get((f"entity:{c['entity_id']}:{c['predicate']}", c["snapshot_week"]), {"label": "?"})
        l = lin.get(c["question_id"], {})
        concl.append({"entity": c["entity_id"], "predicate": c["predicate"], "week": c["snapshot_week"], "status": c["status"],
                      "lineage_ok": c["lineage_ok"], "route": rq[c["question_id"]]["route"],
                      "confirming": l.get("confirming_refs_count", 0), "reconstructable": l.get("reconstructable"),
                      "hashes": [h[:12] for h in (l.get("sha256s") or [])[:3]], "seconds": round(q2v.get(c["question_id"], 0), 2),
                      "label": L["label"]})
    runs.append({"seed": w["seed"], "users": w["users"], "teams": w["teams"], "records": w["records"], "sites": w["records_per_site"],
                 "found": x["found"], "gold": x["gold_discoverable"], "rare_found": round((x["recall_rare"] or 0) * len(x["gold_rare_patterns"])),
                 "rare": len(x["gold_rare_patterns"]), "decoys": sum(x["decoy_accepted_by_type"].values()),
                 "background": x["background_supported"], "q2v_p50": round(rt["question_to_verdict"]["per_question_p50"], 2),
                 "q2v_p95": round(rt["question_to_verdict"]["per_question_p95"], 2), "conclusions": concl})
tpl = (Path(__file__).with_name("template.html")).read_text()
out.write_text(tpl.replace("__DATA__", json.dumps({"runs": runs}, separators=(",", ":")).replace("</", "<\\/")))
print(f"wrote {out} with {len(runs)} run(s)")
