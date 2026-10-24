"""Shared scoring for the rule-candidate rescue replay. Selection code never reads 'label' (scoring only)."""
import json
from pathlib import Path

def load(d, seed):
    return json.load(open(Path(d) / f"pool_s{seed}.json"))

def score(pool, chosen):
    """Score a chosen subset of rows (indices) exactly as the harness's discovery() does, from the v4 verdicts."""
    rows, meta = pool["rows"], pool["meta"]
    rare = {p["id"] for p in meta["patterns"] if p["rare"]}
    sup = [rows[i] for i in chosen if rows[i]["status"] == "supported" and rows[i]["lineage_ok"]]
    found = {r["label"]["pattern"] for r in sup if r["label"]["label"] == "true"}
    dec = set()
    for r in sup:
        for d in meta["decoys"]:
            if r["key"] == d["key0"] and d["found_from"] <= r["week"] <= d["found_to"]:
                dec.add(d["pattern"])
    bg = sum(r["label"]["label"] == "background" for r in sup)
    gold_asked = {rows[i]["label"]["pattern"] for i in chosen if rows[i]["label"]["label"] == "true"}
    return {"found": found, "rare": found & rare, "decoys": len(dec), "bg": bg, "q": len(chosen),
            "gold_asked": gold_asked, "supported": len(sup)}
