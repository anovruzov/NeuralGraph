"""Q1 funnel: for every Gold pattern (rare and common), the stage at which a miss dies, from the recorded report plus
the offline detector replay (det-<cfg>-s<seed>.json from detect_dump.py, validated: asked-key count == report)."""
import json, sys, collections
S = __import__("os").environ.get("PLATEAU_SCRATCH", ".")  # dir holding em/ and plateau/
CFG = {"v2": "abl-v2-h1n_nd_n1", "v4": "abl-v4-rs_nd_n1_n2"}

def classify(cfg, seed):
    rep = json.load(open(f"{S}/em/{CFG[cfg]}-s{seed}/e2e-ingest-report.json"))
    det = json.load(open(f"{S}/plateau/det-{cfg}-s{seed}.json"))
    assert rep["phase"] == "finished" and det["seed"] == seed
    assert len(det["asked_keys"]) == rep["routing"]["candidates_asked"], "replay mismatch"
    D = rep["discovery"]; found = set(D["found_patterns"]); rare = set(D["gold_rare_patterns"])
    ing = rep["ingest"]; ingested_all = ing["loss"] == 0 and ing["total_ingested"] == ing["total_expected"]
    fab = collections.defaultdict(list)
    for c in D["fabric_conclusions"]:
        fab[f"entity:{c['entity_id']}:{c['predicate']}"].append(c)
    orows = collections.defaultdict(list)
    for o in D["oracle_rows"]:
        orows[o["pattern"]].append(o)
    keycount = collections.Counter(p["key"] for p in det["labels"]["patterns"])
    rows = []
    for p in det["labels"]["patterns"]:
        pid, key = p["id"], p["key"]
        cand = det["candidates"].get(key)
        concl = fab.get(key, [])
        inwin = [c for c in concl if p["found_from"] <= c["snapshot_week"] <= p["found_to"]]
        info = {"cfg": cfg, "seed": seed, "pattern": pid, "rare": p["rare"], "key": key, "window": [p["found_from"], p["found_to"]],
                "label_sites": p["sites"], "contradicted_label": p["contradicted"], "shared_key": keycount[key] > 1,
                "cand_channels": cand["channels"] if cand else None,
                "snapshot_week": cand["snapshot_week"] if cand else None,
                "snapshot_source": cand["snapshot_source"] if cand else None,
                "rule_weeks": cand["rule_weeks"] if cand else None,
                "candidate_weeks": cand["candidate_weeks"] if cand else None,
                "asked_weeks_status": [(c["snapshot_week"], c["status"], c["lineage_ok"]) for c in concl],
                "oracle": [{k: o[k] for k in ("expected", "ambiguous", "reason", "route", "per_site_facet_counts", "status")} for o in orows.get(pid, [])]}
        if pid in found:
            stage = "found"
        elif not ingested_all:
            stage = "S0_not_ingested"
        elif cand is None:
            stage = "S1_no_detector_candidate"
        elif not concl:
            stage = "S2_candidate_not_asked"          # no snapshot (rule-only, or no fitting week)
        elif not inwin:
            wk = concl[0]["snapshot_week"]
            stage = "S3_asked_before_window" if wk < p["found_from"] else "S3_asked_after_window"
        elif not any(c["status"] == "supported" for c in inwin):
            stage = "S4_verdict_" + "/".join(sorted({c["status"] for c in inwin}))
        else:
            stage = "S5_supported_not_scored"
        info["stage"] = stage
        rows.append(info)
    return rows, rep

if __name__ == "__main__":
    out = []
    seeds = {"v2": range(6), "v4": [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else range(4)}
    for cfg in ("v2", "v4"):
        for s in seeds[cfg]:
            rows, rep = classify(cfg, s)
            out += rows
    json.dump(out, open(f"{S}/plateau/funnel_rows.json", "w"), indent=1)
    for cfg in ("v2", "v4"):
        for rare in (True, False):
            R = [r for r in out if r["cfg"] == cfg and r["rare"] == rare]
            c = collections.Counter(r["stage"] for r in R)
            print(cfg, "rare" if rare else "common", len(R), dict(sorted(c.items())))
        for s in seeds[cfg]:
            R = [r for r in out if r["cfg"] == cfg and r["rare"] and r["seed"] == s]
            print("   seed", s, dict(sorted(collections.Counter(r["stage"] for r in R).items())))
    print("shared keys:", sum(r["shared_key"] for r in out))
