"""KILL 1: does the shipped roots_bucket track distinct roots? Compare three root definitions per record on dev seeds
0-5 at 200 users, then recompute H7's A4 (whole-pool TB_v4 passes by class at rule weeks, raw vs veto) and A5 (gold
ceiling over label windows) under each. Output: run_1_roots.txt, results_1_roots.json."""
import json, sys
from collections import Counter, defaultdict
from f_common import *  # noqa

res = {}
print("## 1a. Root identity per record: shipped ingest (nd on / default) vs generator event id, 200 users\n")
print("| seed | records | own-root default | own-root nd | echo (site,event) groups | roots/group nd | roots/group default | "
      "gold facet recs | gold recs merged into another record's root (nd) | gold roots holding >1 event (nd) | "
      "non-echo recs merged (nd) | roots holding >1 event (nd) |")
print("|" + "---|" * 12)
tot = Counter()
for seed in range(6):
    D = build(200, seed)
    # flatten unique records across keys (a record can sit under two keys: anchor and aux)
    seen = {}
    for key, v in D["keys"].items():
        for row in v["recs"]:
            seen[row[6]] = row
    rows = list(seen.values())
    n = len(rows)
    own_df = sum(1 for r in rows if r[8] == 10_000_000 + 0 or True) and None  # placeholder, computed below
    # own-root: root id maps to itself -> we only have ids; count roots with one member instead
    def groups(field):
        g = defaultdict(list)
        for r in rows: g[r[field]].append(r)
        return g
    g_nd, g_df = groups(7), groups(8)
    own_nd = sum(1 for g in g_nd.values() if len(g) == 1)
    own_df = sum(1 for g in g_df.values() if len(g) == 1)
    # echo groups: KIND_DUP records grouped by (site, event)
    echo = defaultdict(list)
    for r in rows:
        if r[9]: echo[(r[1], r[3])].append(r)
    rpg_nd = [len({r[7] for r in g}) for g in echo.values()]
    rpg_df = [len({r[8] for r in g}) for g in echo.values()]
    # false merges under nd: roots holding more than one distinct event id
    multi_event_roots = {rid for rid, g in g_nd.items() if len({r[3] for r in g}) > 1}
    gold_recs = [r for r in rows if r[5]]
    gold_merged = sum(1 for r in gold_recs if len(g_nd[r[7]]) > 1)
    gold_multi = len({r[7] for r in gold_recs if r[7] in multi_event_roots})
    nonecho_merged = sum(1 for r in rows if not r[9] and len(g_nd[r[7]]) > 1)
    print(f"| {seed} | {n} | {own_df} | {own_nd} | {len(echo)} | {sum(rpg_nd)/max(1,len(rpg_nd)):.2f} | "
          f"{sum(rpg_df)/max(1,len(rpg_df)):.2f} | {len(gold_recs)} | {gold_merged} | {gold_multi} | {nonecho_merged} | "
          f"{len(multi_event_roots)} |")
    res[f"roots:{seed}"] = {"records_in_pool_keys": n, "own_root_default": own_df, "own_root_nd": own_nd,
                            "echo_groups": len(echo), "roots_per_group_nd": sum(rpg_nd)/max(1,len(rpg_nd)),
                            "roots_per_group_default": sum(rpg_df)/max(1,len(rpg_df)), "gold_recs": len(gold_recs),
                            "gold_merged_nd": gold_merged, "gold_multi_event_roots_nd": gold_multi,
                            "nonecho_merged_nd": nonecho_merged, "multi_event_roots_nd": len(multi_event_roots)}
    for k2, v2 in res[f"roots:{seed}"].items(): tot[k2] += v2
print(f"\npooled: {dict(tot)}")
print("\nNote: 'records' here are the records on the 3,178 candidate-pool keys (H7's universe), not the whole world.")

# which gold records were merged, and with what
print("\n### 1a'. Gold facet records merged by nd (record -> what shares its root)\n")
for seed in range(6):
    D = build(200, seed)
    seen = {}
    for key, v in D["keys"].items():
        for row in v["recs"]: seen[row[6]] = row
    g = defaultdict(list)
    for r in seen.values(): g[r[7]].append(r)
    for root, members in g.items():
        if len(members) > 1 and any(m[5] for m in members):
            kinds = Counter(("gold" if m[5] else ("echo" if m[9] else "other")) for m in members)
            evs = len({m[3] for m in members})
            print(f"  s{seed} root {root}: {len(members)} records, {evs} distinct events, members {dict(kinds)}, "
                  f"sites {sorted({m[1] for m in members})}, weeks {sorted({m[0] for m in members})}")

# ---- 1b: H7's A4 under each root definition
print("\n## 1b. H7 A4 recomputed: TB_v4 passes over the WHOLE pool (3,178 keys, rule weeks, model B), raw vs veto, by root definition\n")
print("| root definition | variant | " + " | ".join(CLASSES) + " |")
print("|" + "---|" * (len(CLASSES) + 2))
for rf in ("event", "nd", "default"):
    for T, name in ((None, "raw TB_v4"), (1, "veto support-roots>=1")):
        cnt = Counter()
        for seed in range(6):
            D = build(200, seed); widx = {w: i for i, w in enumerate(D["weeks"])}
            for key, v in D["keys"].items():
                W = [widx[w] for w in v["rule_weeks"]]
                if not W: continue
                if passes(v["recs"], W, rf, T):
                    for c in classes_of(v): cnt[c] += 1
        print(f"| {rf} | {name} | " + " | ".join(str(cnt[c]) for c in CLASSES) + " |")
        res[f"A4:{rf}:{name}"] = dict(cnt)

print("\n## 1b'. Same, per seed, d4 and gold only (raw -> veto), and asked (snapshot-state) d4 keys\n")
print("| root | seed | d4 raw | d4 veto | gold raw | gold veto | other raw | other veto | asked d4 raw -> veto |")
print("|" + "---|" * 9)
for rf in ("event", "nd", "default"):
    for seed in range(6):
        D = build(200, seed); widx = {w: i for i, w in enumerate(D["weeks"])}
        c = Counter()
        for key, v in D["keys"].items():
            W = [widx[w] for w in v["rule_weeks"]]
            if not W: continue
            raw = passes(v["recs"], W, rf, None); vt = passes(v["recs"], W, rf, 1)
            for cl in classes_of(v):
                c[(cl, "raw")] += raw; c[(cl, "veto")] += vt
                if cl == "d4" and v["detector_state"] == "snapshot":
                    c[("asked_d4", "raw")] += raw; c[("asked_d4", "veto")] += vt
        print(f"| {rf} | {seed} | {c[('d4','raw')]} | {c[('d4','veto')]} | {c[('gold','raw')]} | {c[('gold','veto')]} | "
              f"{c[('other','raw')]} | {c[('other','veto')]} | {c[('asked_d4','raw')]} -> {c[('asked_d4','veto')]} |")
        res[f"A4seed:{rf}:{seed}"] = {f"{a}:{b}": n for (a, b), n in c.items()}

# ---- 1c: H7's A5 (gold ceiling, label windows) under each root definition
print("\n## 1c. H7 A5 recomputed: 240 gold patterns, label windows, TB_v4, raw vs veto, by root definition\n")
print("| root | model | variant | feasible | per seed | lost to veto |")
print("|---|---|---|---|---|---|")
for rf in ("event", "nd", "default"):
    for model, alpha in (("B", "blind"), ("K", 0)):
        for T, name in ((None, "raw"), (1, "veto>=1")):
            tot_ = 0; ps = []; lost = []
            for seed in range(6):
                D = build(200, seed)
                n = 0
                for key, v in D["keys"].items():
                    if v["class"] != "gold": continue
                    W = label_window(D, v)
                    ok = passes(v["recs"], W, rf, T, alpha)
                    n += ok
                    if not ok and T is not None and passes(v["recs"], W, rf, None, alpha):
                        lost.append(f"s{seed}:{v['gold']['pattern']}{'(rare)' if v['gold']['rare'] else ''}")
                tot_ += n; ps.append(n)
            print(f"| {rf} | {model} | {name} | {tot_}/240 = {tot_/240:.4f} | {ps} | {len(lost)} {lost} |")
            res[f"A5:{rf}:{model}:{name}"] = {"feasible": tot_, "per_seed": ps, "lost": lost}

# ---- 1d: exported bucket pairs at counted k-sites, passing weeks, by class and root definition
print("\n## 1d. (support bucket, roots bucket) of counted k-sites at TB_v4-passing rule weeks, by class and root definition\n")
for rf in ("event", "nd", "default"):
    pairs = defaultdict(Counter)
    for seed in range(6):
        D = build(200, seed); widx = {w: i for i, w in enumerate(D["weeks"])}
        for key, v in D["keys"].items():
            W = [widx[w] for w in v["rule_weeks"]]
            for w in W:
                wr = window_recs(v["recs"], w - WIN + 1, w)
                st = site_stats(wr, rf)
                ok, counted, _ = tb_v4(st, w)
                if not ok: continue
                for s in counted:
                    x = st[s]
                    if x["records"] >= K:
                        for c in classes_of(v): pairs[c][(bidx(x["records"]), bidx(x["events"]))] += 1
    print(f"root={rf}:")
    for c in CLASSES:
        print(f"  {c:5s} {dict(sorted(pairs[c].items()))}")
    res[f"pairs:{rf}"] = {c: {str(k): n for k, n in pairs[c].items()} for c in CLASSES}
json.dump(res, open(f"{OUT}/results_1_roots.json", "w"), indent=1, default=str)
