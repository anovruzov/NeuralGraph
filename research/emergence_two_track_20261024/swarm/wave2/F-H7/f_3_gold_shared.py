"""KILL 3: the veto's gold cost on a world where gold shares roots. The generator gives every gold facet record its own
event id (corpus.py: rows["event"] = arange(next_event, next_event + k)); only class-4 decoys write ONE event id for
all echoes. Counterfactuals built in the generator's own terms, by rewriting the event id of gold FACET records only
(background records on the key untouched), per (pattern, facet, site) group G of facet records:
  CF-A  one incident per facet-site: every record of G shares one root (k reporters, one incident)
  CF-B  pairs: G collapsed into ceil(|G|/2) roots (two reporters per incident)
  CF-C  one shared pair: exactly two records of G share a root, the rest keep their own
  CF-A@p  CF-A applied to a random fraction p of the facet-site groups (p = 0.25, 0.5), seeded
Because an echo in this generator is realised as near-identical text keyed on the event id, the shipped nd rule
collapses such groups to one root (Kill 1: 1.03-1.10 roots per echo group), so rewriting event ids is the faithful
stand-in for what a site would export. Scored: TB_v4 feasibility over the label window (ceiling_b semantics),
models B and K, raw (N1+N2, no veto) vs veto>=1; the veto's MARGINAL cost = patterns feasible raw-CF but not veto-CF."""
import json, math, random
from collections import Counter, defaultdict
from f_common import *  # noqa


def rewrite(recs, mode, p=1.0, seed=0):
    """Return a copy of the key's records with gold-facet event ids rewritten per (site, facet-week) group."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    for i, r in enumerate(recs):
        if r[5]:                         # facet_of_gold
            groups[(r[1], r[0])].append(i)   # facet records of one facet land on one day -> (site, week) is the group
    out = [list(r) for r in recs]
    for g, idxs in groups.items():
        if len(idxs) < 2 or rng.random() > p:
            continue
        idxs = sorted(idxs, key=lambda i: recs[i][6])
        if mode == "A":
            for i in idxs: out[i][3] = recs[idxs[0]][3]
        elif mode == "B":
            for j, i in enumerate(idxs): out[i][3] = recs[idxs[2 * (j // 2)]][3]
        elif mode == "C":
            out[idxs[1]][3] = recs[idxs[0]][3]
    return out


CF = [("original", None, 1.0), ("CF-C one shared pair", "C", 1.0), ("CF-B pairs", "B", 1.0),
      ("CF-A@0.25", "A", 0.25), ("CF-A@0.5", "A", 0.5), ("CF-A all share", "A", 1.0)]
res = {}
print("## 3a. Gold TB_v4 feasibility over label windows (240 patterns), counterfactual root sharing among gold facet records\n")
print("| counterfactual | model | raw (N1+N2) | raw lost vs original | veto>=1 | veto lost vs raw-CF (marginal cost) | "
      "rare lost (marginal) | common lost (marginal) | per seed raw | per seed veto |")
print("|" + "---|" * 10)
for name, mode, p in CF:
    for model, alpha in (("B", "blind"), ("K", 0)):
        raw_tot = veto_tot = 0; raw_ps = []; veto_ps = []; lost_raw = []; lost_veto = []; rare_l = common_l = 0
        for seed in range(6):
            D = build(200, seed)
            r_ = v_ = 0
            for key, v in D["keys"].items():
                if v["class"] != "gold": continue
                W = label_window(D, v)
                recs = v["recs"] if mode is None else rewrite(v["recs"], mode, p, seed * 1000 + hash(key) % 1000)
                raw = passes(recs, W, "event", None, alpha); vt = passes(recs, W, "event", 1, alpha)
                orig = passes(v["recs"], W, "event", None, alpha)
                r_ += raw; v_ += vt
                tag = f"s{seed}:{v['gold']['pattern']}{'(rare)' if v['gold']['rare'] else ''}"
                if orig and not raw: lost_raw.append(tag)
                if raw and not vt:
                    lost_veto.append(tag)
                    if v["gold"]["rare"]: rare_l += 1
                    else: common_l += 1
            raw_tot += r_; veto_tot += v_; raw_ps.append(r_); veto_ps.append(v_)
        print(f"| {name} | {model} | {raw_tot}/240 | {len(lost_raw)} | {veto_tot}/240 | **{len(lost_veto)}** | {rare_l} | {common_l} | {raw_ps} | {veto_ps} |")
        res[f"{name}:{model}"] = {"raw": raw_tot, "veto": veto_tot, "lost_raw_vs_original": lost_raw, "lost_veto_vs_raw": lost_veto,
                                  "per_seed_raw": raw_ps, "per_seed_veto": veto_ps}

print("\n## 3b. Why: exported (support, roots) bucket pairs of gold k-sites at feasible weeks, original vs CF-A\n")
for name, mode, p in (("original", None, 1.0), ("CF-A all share", "A", 1.0)):
    pairs = Counter()
    for seed in range(6):
        D = build(200, seed)
        for key, v in D["keys"].items():
            if v["class"] != "gold": continue
            recs = v["recs"] if mode is None else rewrite(v["recs"], mode, p, seed)
            for w in label_window(D, v):
                st = site_stats(window_recs(recs, w - WIN + 1, w), "event")
                ok, counted, _ = tb_v4(st, w)
                if not ok: continue
                for s in counted:
                    if st[s]["records"] >= K: pairs[(bidx(st[s]["records"]), bidx(st[s]["events"]))] += 1
    print(f"  {name}: {dict(sorted(pairs.items()))}")
    res[f"pairs:{name}"] = {str(k): n for k, n in pairs.items()}

print("\n## 3c. The decoy side is unchanged by the counterfactual (d4 keys carry no gold facet records): d4 TB_v4 passes at rule weeks, raw vs veto, event roots\n")
c = Counter()
for seed in range(6):
    D = build(200, seed); widx = {w: i for i, w in enumerate(D["weeks"])}
    for key, v in D["keys"].items():
        if "d4" not in classes_of(v): continue
        W = [widx[w] for w in v["rule_weeks"]]
        if not W: continue
        c["raw"] += passes(v["recs"], W, "event", None); c["veto"] += passes(v["recs"], W, "event", 1)
print(f"  d4: raw {c['raw']} -> veto {c['veto']}")
json.dump(res, open(f"{OUT}/results_3_gold_shared.json", "w"), indent=1, default=str)
