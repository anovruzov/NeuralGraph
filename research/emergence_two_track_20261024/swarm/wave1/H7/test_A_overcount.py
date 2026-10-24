"""H7 question (A): does the TB_v4 gate's site count overcount correlated disclosure (two sites reporting the same
root or the same reporter)?

Per seed and per class, over every candidate-pool key, at the key's rule weeks (the weeks a candidate existed):
  raw      : TB_v4 passes at some week (model B, the shipped polarity-blind judge; and model K)
  dedupe   : TB_v4 passes at some week AFTER the ORACLE removes every record whose root already appeared at a
             stronger site in the same window (h7lib.dedupe_window) -- two sites echoing one root count once
  overcount: raw passes and dedupe never passes  == "passes only because sites share roots"
  shared   : raw passes at a week where the counted sites share at least one root (overlap present, outcome aside)
Reporter sharing across sites is counted directly (expected 0: a user has one site ancestor).
Ceiling impact: the 240 gold patterns over their label windows under F and B, raw vs dedupe (ceiling_b.py semantics).
Exported-contract correction tested: s_echo(key) = max over rule weeks, over k-sites, of
  bucket_index(support) - bucket_index(roots)   (a site reporting more records than roots: an echo signature),
AUC gold vs class-4 decoys and gold vs all decoys on the wave-0 filtered pool (rule_only, rule_weeks >= 7).
"""
import json, sys
from collections import defaultdict, Counter
from h7lib import *

CLASSES = ["gold", "d1", "d2", "d3", "d4", "d5", "other"]


def classes_of(v):
    if v["class"] == "gold": return ["gold"]
    if v["class"] == "decoy": return [f"d{c}" for c in v["decoy_classes"]] or ["decoy?"]
    return ["other"]


def key_eval(v, weeks_idx, alpha, recs=None):
    recs = v["recs"] if recs is None else recs
    raw_w, ded_w, shared_w = [], [], []
    for w in weeks_idx:
        wr = window_recs(recs, w - WIN + 1, w)
        ok, counted, _ = tb_v4(site_stats(wr, alpha), w)
        if ok:
            raw_w.append(w)
            if cross_site_shared_events(wr, set(counted)): shared_w.append(w)
        ok2, _, _ = tb_v4(site_stats(dedupe_window(wr, alpha), alpha), w)
        if ok2: ded_w.append(w)
    return raw_w, ded_w, shared_w


def echo_score(v, weeks_idx, alpha="blind"):
    best = -1
    for w in weeks_idx:
        st = site_stats(window_recs(v["recs"], w - WIN + 1, w), alpha)
        for s, x in st.items():
            if x["records"] >= K:
                best = max(best, bidx(x["records"]) - bidx(x["events"]))
    return best


results = {"per_seed": {}, "ceiling": {}, "echo": {}}
print("## A. Overcount table (TB_v4 at the key's rule weeks; model B unless stated)\n")
hdr = "| seed | model | class | keys | raw pass | dedupe pass | overcount (pass only via shared roots) | raw pass with shared root | shared reporters |"
print(hdr); print("|" + "---|" * 9)
tot = defaultdict(Counter)
for seed in range(6):
    D = load(seed); widx = {w: i for i, w in enumerate(D["weeks"])}
    shared_users = set()
    for model in ("blind", 0):
        cnt = defaultdict(Counter)
        for key, v in D["keys"].items():
            W = [widx[w] for w in v["rule_weeks"]]
            if not W: continue
            raw_w, ded_w, shared_w = key_eval(v, W, model)
            for c in classes_of(v):
                cnt[c]["keys"] += 1
                cnt[c]["raw"] += bool(raw_w); cnt[c]["ded"] += bool(ded_w)
                cnt[c]["over"] += bool(raw_w) and not ded_w
                cnt[c]["shared"] += bool(shared_w)
            if model == "blind":
                shared_users |= cross_site_shared_users(v["recs"])
        for c in CLASSES:
            x = cnt[c]
            print(f"| {seed} | {'B' if model == 'blind' else 'K'} | {c} | {x['keys']} | {x['raw']} | {x['ded']} | {x['over']} | {x['shared']} | {len(shared_users)} |")
            for k2, n in x.items(): tot[(model, c)][k2] += n
        results["per_seed"][f"{seed}:{model}"] = {c: dict(cnt[c]) for c in CLASSES}
print("\n**Pooled, six seeds**\n")
print("| model | class | keys | raw pass | dedupe pass | overcount | raw pass with shared root |"); print("|" + "---|" * 7)
for model in ("blind", 0):
    for c in CLASSES:
        x = tot[(model, c)]
        print(f"| {'B' if model == 'blind' else 'K'} | {c} | {x['keys']} | {x['raw']} | {x['ded']} | {x['over']} | {x['shared']} |")
results["pooled"] = {f"{m}:{c}": dict(tot[(m, c)]) for m in ("blind", 0) for c in CLASSES}

# ---- ceiling impact: gold patterns over their label windows (ceiling_b semantics), raw vs dedupe
print("\n## A2. Ceiling impact: 240 gold patterns, label window, TB_v4, raw vs root-deduped\n")
print("| model | feasible raw | feasible dedupe | lost to dedupe | per-seed raw | per-seed dedupe |"); print("|" + "---|" * 6)
for model_name, alpha, facet_only in (("F", 0, True), ("B", "blind", False), ("K", 0, False)):
    raw_tot = ded_tot = 0; ps_raw = []; ps_ded = []; lost = []
    for seed in range(6):
        D = load(seed); widx = {w: i for i, w in enumerate(D["weeks"])}
        r_ = d_ = 0
        for key, v in D["keys"].items():
            if v["class"] != "gold": continue
            g = v["gold"]; lo, hi = widx[g["window"][0]], min(widx[g["window"][1]], len(D["weeks"]) - 1) if g["window"][1] in widx else len(D["weeks"]) - 1
            W = list(range(lo, hi + 1))
            recs = [r for r in v["recs"] if r[5]] if facet_only else v["recs"]
            raw_w, ded_w, _ = key_eval(v, W, alpha, recs)
            r_ += bool(raw_w); d_ += bool(ded_w)
            if raw_w and not ded_w: lost.append(f"s{seed}:{g['pattern']}")
        raw_tot += r_; ded_tot += d_; ps_raw.append(r_); ps_ded.append(d_)
    print(f"| {model_name} | {raw_tot}/240 = {raw_tot/240:.4f} | {ded_tot}/240 = {ded_tot/240:.4f} | {len(lost)} {lost} | {ps_raw} | {ps_ded} |")
    results["ceiling"][model_name] = {"raw": raw_tot, "dedupe": ded_tot, "lost": lost, "per_seed_raw": ps_raw, "per_seed_dedupe": ps_ded}

# ---- exported-contract echo signature on the filtered pool
print("\n## A3. Exported-contract echo signature s_echo = max over rule weeks, k-sites of bidx(support) - bidx(roots)\n")
print("| seed | n gold | n d4 | n decoy | AUC gold vs d4 | AUC gold vs all decoy | AUC gold vs other | gold s_echo>0 | d4 s_echo>0 | overcounted keys with s_echo>0 |")
print("|" + "---|" * 10)
pool_scores = defaultdict(list); over_hit = Counter()
for seed in range(6):
    D = load(seed); widx = {w: i for i, w in enumerate(D["weeks"])}
    sc = defaultdict(list)
    for key, v in D["keys"].items():
        if v["detector_state"] != "rule_only" or len(v["rule_weeks"]) < 7: continue
        W = [widx[w] for w in v["rule_weeks"]]
        s = echo_score(v, W)
        for c in classes_of(v):
            sc[c].append(s); pool_scores[c].append(s)
        if v["class"] == "decoy": sc["decoy"].append(s); pool_scores["decoy"].append(s)
        raw_w, ded_w, _ = key_eval(v, W, "blind")
        if raw_w and not ded_w:
            over_hit["n"] += 1; over_hit["hit"] += s > 0
    a4 = auc(sc["gold"], sc["d4"]); ad = auc(sc["gold"], sc["decoy"]); ao = auc(sc["gold"], sc["other"])
    print(f"| {seed} | {len(sc['gold'])} | {len(sc['d4'])} | {len(sc['decoy'])} | {a4:.3f} | {ad:.3f} | {ao:.3f} | "
          f"{sum(1 for x in sc['gold'] if x > 0)} | {sum(1 for x in sc['d4'] if x > 0)} | {over_hit['hit']}/{over_hit['n']} |")
    results["echo"][seed] = {"auc_gold_vs_d4": a4, "auc_gold_vs_decoy": ad, "auc_gold_vs_other": ao}
a4 = auc(pool_scores["gold"], pool_scores["d4"]); ad = auc(pool_scores["gold"], pool_scores["decoy"])
print(f"| all | {len(pool_scores['gold'])} | {len(pool_scores['d4'])} | {len(pool_scores['decoy'])} | {a4:.3f} | {ad:.3f} | "
      f"{auc(pool_scores['gold'], pool_scores['other']):.3f} | {sum(1 for x in pool_scores['gold'] if x > 0)} | "
      f"{sum(1 for x in pool_scores['d4'] if x > 0)} | {over_hit['hit']}/{over_hit['n']} |")
print("\ns_echo distribution on the filtered pool (value: count) by class:")
for c in ["gold", "d1", "d2", "d3", "d4", "d5", "other"]:
    print(f"  {c:5s} {dict(sorted(Counter(pool_scores[c]).items()))}")
results["echo"]["pooled"] = {"auc_gold_vs_d4": a4, "auc_gold_vs_decoy": ad,
                             "dist": {c: dict(Counter(pool_scores[c])) for c in CLASSES}}
json.dump(results, open(f"{OUT}/results_A.json", "w"), indent=1, default=str)
