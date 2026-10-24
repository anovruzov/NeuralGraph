"""H2 / step 0: exported-feature table for every pool key, from raw/plateau/candidate_pool.csv + scratchpad/dumps/seed-*.json.
No world rebuild. Reads only: rule hit weeks, candidate weeks, channels, entity id, predicate. Labels (class, pattern, found)
are carried along for SCORING only and are never read by any feature. Output: OUT/features_base.json (one row per key)."""
import csv, json, collections, os, sys
R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
D = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps"
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H2"
N_WEEKS = 26
def widx(w):  # '2024-W05' -> 5 (1-based week index in the 26-week world)
    return int(w.split("-W")[1])

rows = list(csv.DictReader(open(f"{R}/raw/plateau/candidate_pool.csv")))
dumps = {s: json.load(open(f"{D}/seed-{s}.json")) for s in range(6)}
out = []; miss = 0; preds = collections.Counter()
# entity-level aggregates per seed (exported: every pool key is a rule hit on (entity, predicate))
ent_keys = collections.defaultdict(list)
for r in rows:
    _, ent, pred = r["key"].split(":", 2)
    ent_keys[(r["seed"], ent)].append(r["key"])
for r in rows:
    s = int(r["seed"]); c = dumps[s]["candidates"].get(r["key"])
    if c is None: miss += 1; continue
    rw = sorted(widx(w) for w in (c["rule_weeks"] or []))
    cw = sorted(widx(w) for w in (c["candidate_weeks"] or []))
    _, ent, pred = r["key"].split(":", 2); preds[pred] += 1
    n = len(rw); first = rw[0] if rw else 0; last = rw[-1] if rw else 0
    span = (last - first + 1) if rw else 0
    runs = 0; maxrun = 0; cur = 0; prev = None
    for w in rw:
        if prev is None or w != prev + 1: runs += 1; cur = 1
        else: cur += 1
        maxrun = max(maxrun, cur); prev = w
    assert int(r["rule_weeks"]) == n, (r, rw)
    sib = [k for k in ent_keys[(r["seed"], ent)] if k != r["key"]]
    sib_rw = sum(len(dumps[s]["candidates"][k]["rule_weeks"] or []) for k in sib if k in dumps[s]["candidates"])
    out.append({"seed": s, "key": r["key"], "entity": ent, "pred": pred,
                # --- labels: SCORE ONLY ---
                "cls": r["class"], "state": r["detector_state"], "found": r["found"] == "1", "rare": r["rare"],
                "feasible": r["F_TB_v4_feasible"] == "1",
                # --- exported features ---
                "n_rw": n, "first_rw": first, "last_rw": last, "span": span, "density": (n / span) if span else 0.0,
                "n_runs": runs, "max_run": maxrun, "n_gaps": span - n, "recency": (N_WEEKS - last) if rw else N_WEEKS,
                "rw_last6": sum(1 for w in rw if w > N_WEEKS - 6), "rw_first_half": sum(1 for w in rw if w <= 13),
                "n_sib": len(sib), "sib_rw": sib_rw,
                # --- membership-of-P indicators (NOT used as PU features; recorded for audit) ---
                "n_cw": len(cw), "has_detector": "detector" in (c["channels"] or [])})
json.dump(out, open(f"{OUT}/features_base.json", "w"))
print("rows", len(out), "missing in dumps", miss)
print("predicates", preds.most_common())
by = collections.Counter((o["state"], o["cls"]) for o in out); print(by)
# sanity: P vs U membership is exactly the channel/candidate-week indicator
print("P (snapshot gold):", sum(1 for o in out if o["state"] == "snapshot" and o["cls"] == "gold"),
      "all snapshot have detector channel:", all(o["has_detector"] for o in out if o["state"] == "snapshot"),
      "no rule_only has candidate weeks:", all(o["n_cw"] == 0 for o in out if o["state"] == "rule_only"))
# feature means by (state, class) for a first look
feats = ["n_rw", "first_rw", "last_rw", "span", "density", "n_runs", "max_run", "n_gaps", "recency", "rw_last6", "n_sib", "sib_rw"]
print(f"{'group':22s} {'n':>5s} " + " ".join(f"{f:>9s}" for f in feats))
for st, cl in [("snapshot", "gold"), ("snapshot", "decoy"), ("snapshot", "other"), ("rule_only", "gold"), ("rule_only", "decoy"), ("rule_only", "other")]:
    g = [o for o in out if o["state"] == st and o["cls"] == cl]
    print(f"{st+'/'+cl:22s} {len(g):5d} " + " ".join(f"{sum(o[f] for o in g)/len(g):9.3f}" for f in feats))
    gf = [o for o in g if o["n_rw"] > 6]
    if len(gf) != len(g):
        print(f"{'  filtered >6':22s} {len(gf):5d} " + " ".join(f"{sum(o[f] for o in gf)/len(gf):9.3f}" for f in feats))
