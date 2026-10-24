import json, collections
S = __import__("os").environ.get("PLATEAU_SCRATCH", ".")  # dir holding em/ and plateau/
C = {int(k): v for k, v in json.load(open(f"{S}/plateau/ceiling_b.json")).items()}
rows = json.load(open(f"{S}/plateau/funnel_rows.json"))
by = {(r["cfg"], r["seed"], r["pattern"]): r for r in rows}
# v4 vs v2 gains / losses per seed
for s in range(6):
    v2 = {r["pattern"] for r in rows if r["cfg"] == "v2" and r["seed"] == s and r["stage"] == "found"}
    v4 = {r["pattern"] for r in rows if r["cfg"] == "v4" and r["seed"] == s and r["stage"] == "found"}
    gain = sorted(v4 - v2); loss = sorted(v2 - v4)
    print(f"s{s} v4-v2 gain {len(gain)} {[(p, by[('v4', s, p)]['snapshot_source'], by[('v4', s, p)]['rare']) for p in gain]}  loss {len(loss)} {[(p, by[('v4', s, p)]['stage']) for p in loss]}")
# timing direction for v4 misses that were feasible at some week
dirc = collections.Counter()
for r in rows:
    if r["cfg"] != "v4" or not r["rare"] or r["stage"] == "found": continue
    _f = C[r["seed"]][r["pattern"]]["feasible_weeks"]
    fw = sorted(set(_f["B"]["TB_v4"]) | set(_f["F"]["TB_v4"]))  # union: F (facet records) is not a subset of B (key records)
    if not fw: dirc["structural"] += 1; continue
    a = r["asked_weeks_status"][0][0]
    dirc["asked_before_all_feasible" if all(w > a for w in fw) else ("asked_after_all_feasible" if all(w < a for w in fw) else "between")] += 1
print("v4 rare miss timing direction:", dict(dirc))
# source of v4 snapshot for misses
print("v4 rare miss snapshot source:", collections.Counter((r["stage"][:2], r["snapshot_source"]) for r in rows if r["cfg"] == "v4" and r["rare"] and r["stage"] != "found"))
print("v4 rare found snapshot source:", collections.Counter(r["snapshot_source"] for r in rows if r["cfg"] == "v4" and r["rare"] and r["stage"] == "found"))
for key in [("v2", 0, "p002"), ("v4", 0, "p002"), ("v4", 0, "p005"), ("v4", 0, "p004"), ("v4", 1, "p013")]:
    r = by[key]; c = C[key[1]][key[2]]
    print("\n==", key, r["stage"], "window", r["window"], "label sites", r["label_sites"], "channels", r["cand_channels"], "snapshot", r["snapshot_week"], r["snapshot_source"])
    print("   rule_weeks", r["rule_weeks"], "detector weeks", r["candidate_weeks"])
    print("   asked", r["asked_weeks_status"])
    print("   feasible weeks TB_v4: F", c["feasible_weeks"]["F"]["TB_v4"], "B", c["feasible_weeks"]["B"]["TB_v4"], "| R0 B", c["feasible_weeks"]["B"]["R0_count"])
    for o in r["oracle"]: print("   oracle", o["reason"], json.dumps(o["per_site_facet_counts"]))
