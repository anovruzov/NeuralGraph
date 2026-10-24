"""Build raw/e2e_ledger.jsonl from every e2e report under EM_DIR: one row per run with the discovery figures, the
report's sha256, the run id, the code revision the report recorded, the decoys by class and the per-run labels."""
import glob, hashlib, json, os, sys
em, out = sys.argv[1], sys.argv[2]
rows = []
for f in sorted(glob.glob(os.path.join(em, "*/e2e-ingest-report.json"))):
    tag = f.split("/")[-2]
    raw = open(f, "rb").read(); doc = json.loads(raw); d = doc.get("discovery")
    run = doc.get("run", {})
    kind = ("OFFICIAL-CLI" if tag.startswith("official") else "PLUMBING-fake-judge" if tag.startswith("plumb")
            else "DEV-DIRTY-TREE" if tag.startswith("base") else "DIAGNOSTIC-guard-bypass" if tag.startswith("clean")
            else "ABLATION")
    row = {"tag": tag, "kind": kind, "report_sha256": hashlib.sha256(raw).hexdigest(),
           "run_id": run.get("run_id"), "code_commit": run.get("code_commit"), "command": run.get("command"),
           "code_dirty_start": run.get("code_dirty_start"), "code_dirty_end": run.get("code_dirty_end"), "spike_code_hash": run.get("spike_code_hash"), "top_n": run.get("top_n"),
           "seconds": run.get("seconds"), "main_pass_seconds": (doc.get("timings") or {}).get("main_pass_seconds"), "complete": doc.get("completed")}
    if d:
        row.update({k: d[k] for k in ("found", "gold_discoverable", "recall_all", "recall_rare", "recall_common",
                                      "decoy_accepted_by_type", "candidates_true", "candidates_decoy",
                                      "candidates_background", "supported_conclusions", "found_patterns")})
        row["decoys_accepted_total"] = sum(d["decoy_accepted_by_type"].values())
    else:
        row["discovery"] = None
    rows.append(row)
with open(out, "w") as fh:
    for r in rows:
        fh.write(json.dumps(r, sort_keys=True) + "\n")
print(len(rows), "rows ->", out)
