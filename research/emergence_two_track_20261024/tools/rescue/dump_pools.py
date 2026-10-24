"""Rule-candidate rescue, step 1: per dev seed, the v4 config's full candidate pool (every candidate the v4 run asked),
each with (a) inference-time fields only, from the detection result and HQ's own k-suppressed cells, (b) the verdict
the v4 e2e run actually got for it (fabric note status + lineage), and (c) a gold label used ONLY for scoring.
DIAGNOSTIC: check_world_counts bypassed for seeds 1-5 (as the ablation wrapper does). Usage: dump_pools.py REPORTS_DIR OUT_DIR SEEDS"""
import json, shutil, sys, tempfile
from collections import defaultdict
from pathlib import Path
from research.e2e_ingest import emergence_world as ew
from research.routing_spike.world import candidate_label
from mycelic.collective.detect.detectors import detect

reports, out = Path(sys.argv[1]), Path(sys.argv[2]); seeds = [int(s) for s in sys.argv[3].split(",")]
assert all(s in range(6) for s in seeds), "dev seeds only"
out.mkdir(parents=True, exist_ok=True)
ew.check_world_counts = lambda counts, target_users: None
OVER = {"detectors": {"min_history_weeks": 1, "rule_snapshots": True},
        "egress": {"near_duplicate_roots": {"jaccard": 0.65, "days": 14}},
        "questions": {"pushdown": {"weak_corroborating_sites": 1, "roots_from_k_sites_only": True}}}
pack = Path(tempfile.mkdtemp(prefix="pack-rescue-")) / "emergence"; shutil.copytree(ew.PACK_DIR, pack)
def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict): merge(dst[k], v)
        else: dst[k] = v
for fname, fields in OVER.items():
    f = pack / f"{fname}.json"; d = json.loads(f.read_text()); merge(d, fields)
    f.write_text(json.dumps(d, sort_keys=True, separators=(",", ":")))
def key0(w, d):
    """The decoy's facet-0 (anchor, chain) key, exactly as the harness's discovery() derives it."""
    from mycelic.collective.detect.rules import series_key
    p = w.corpus.patterns[int(d["pattern"])]
    if not (p.facet_records and p.facet_records[0]): return None
    anchor = ew.canonical_entity_id(w.corpus.entities[int(w.corpus.recs[int(p.facet_records[0][0])]["anchor"])])
    return series_key(ew.ENTITY_TYPE, anchor, ew.CHAIN_PREDICATES[int(p.chain)])
for seed in seeds:
    rep = json.load(open(reports / f"abl-v4-rs_nd_n1_n2-s{seed}" / "e2e-ingest-report.json"))
    fabric = {(c["entity_id"], c["predicate"], c["snapshot_week"]): c for c in rep["discovery"]["fabric_conclusions"]}
    w = ew.build_emergence_world(200, seed, Path(tempfile.mkdtemp()) / "w", pack_dir=pack, top_n=None)
    store = w.pipeline.store
    result = detect(store, as_of=w.as_of, run_channel=ew.RUN_CHANNEL, tie_salt=w.tie_salt)
    weeks = [x["week"] for x in result["weeks"]]
    _, cells = store.detection_inputs(w.as_of, ew.RUN_CHANNEL)
    lb = defaultdict(lambda: defaultdict(int))            # key -> week -> sum over sites of the lower bound (k-suppressed: <k counts 1)
    sites_at = defaultdict(lambda: defaultdict(set))       # key -> week -> sites with a cell
    for c in cells:
        key = f"{c.entity_type}|{c.entity_id}|{c.predicate}"
        lb[key][c.iso_week] += c.n if c.n is not None else 1
        sites_at[key][c.iso_week].add(c.site)
    rows = []
    for c in result["candidates"]:
        snap = c["snapshot"]
        if snap is None: continue
        key = c["key"]; f = fabric.get((c["entity_id"], c["predicate"], snap["week"]))
        ckey = f"{c['entity_type']}|{c['entity_id']}|{c['predicate']}"
        i = weeks.index(snap["week"]); win = weeks[max(0, i - 5):i + 1]; hist = weeks[:max(0, i - 5)]
        rule = c["rule"] or {}
        rows.append({
            "key": key, "week": snap["week"], "as_of": snap["as_of"], "source": snap.get("source", "detector"),
            "score": snap["score"], "channels": c["channels"], "detectors": c["detectors"],
            "n_candidate_weeks": len(c["candidate_weeks"]), "n_alert_weeks": len(c["alert_weeks"]),
            "rule_ids": rule.get("rule_ids", []), "rule_weeks": len(rule.get("weeks", [])),
            "rule_week_list": list(rule.get("weeks", [])), "candidate_week_list": list(c["candidate_weeks"]),
            "rule_sites": len({s["site"] for s in rule.get("sites", [])}),
            "rule_count_lb": sum(s["count_lb"] for s in rule.get("sites", [])),
            "supporting_sites": len(snap.get("supporting_sites", [])),
            "win_lb": [lb[ckey].get(x, 0) for x in win], "win_sites": [len(sites_at[ckey].get(x, ())) for x in win],
            "hist_lb": [lb[ckey].get(x, 0) for x in hist],
            # outcome of the v4 run (system output, not gold)
            "asked_in_v4": f is not None, "status": f["status"] if f else None, "lineage_ok": f["lineage_ok"] if f else None,
            # gold label: scoring only
            "label": candidate_label(key, snap["week"], w.labels)})
    meta = {"seed": seed, "detected": len(rows), "v4_fabric": len(fabric), "matched": sum(r["asked_in_v4"] for r in rows),
            "patterns": [{"id": p["id"], "rare": p["rare"]} for p in w.labels["patterns"]],
            "decoys": [{"pattern": d["pattern"], "class": d["class"], "keys": d["keys"], "key0": key0(w, d),
                        "found_from": d["found_from"], "found_to": d["found_to"]} for d in w.labels["decoys"]],
            "v4_found": rep["discovery"]["found"]}
    json.dump({"meta": meta, "rows": rows}, open(out / f"pool_s{seed}.json", "w"))
    print(json.dumps({k: v for k, v in meta.items() if k not in ("patterns", "decoys")}), flush=True)
    w.pipeline.close()
