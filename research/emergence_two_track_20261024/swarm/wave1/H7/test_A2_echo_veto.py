"""H7 (A), follow-up: the echo signature as a GATE-SIDE inhibitor, from exported contracts only.
A counted confirm is vetoed when bidx(support_bucket) - bidx(roots_bucket) >= T at that site (T = 1, 2); variant
uses reporters_bucket in place of support_bucket. Reported: TB_v4 pass counts by class over the WHOLE pool (3,178
keys) at rule weeks, raw vs veto; the 240-gold ceiling (label windows, models B and K) raw vs veto; AUC on the
filtered pool for both signatures. Oracle check: how many of the 26 root-overcounted d4 keys the veto removes.
"""
import json
from collections import defaultdict, Counter
from h7lib import *

CLASSES = ["gold", "d1", "d2", "d3", "d4", "d5", "other"]


def classes_of(v):
    if v["class"] == "gold": return ["gold"]
    if v["class"] == "decoy": return [f"d{c}" for c in v["decoy_classes"]] or ["decoy?"]
    return ["other"]


def veto(st, T, field):
    """Drop k-sites whose exported support (or reporters) bucket index exceeds their roots bucket index by >= T."""
    if T is None: return st
    return {s: v for s, v in st.items() if not (v["records"] >= K and bidx(v[field]) - bidx(v["events"]) >= T)}


def passes(v, W, alpha, T, field, recs=None):
    recs = v["recs"] if recs is None else recs
    for w in W:
        wr = window_recs(recs, w - WIN + 1, w)
        if tb_v4(veto(site_stats(wr, alpha), T, field), w)[0]: return True
    return False


VARIANTS = [(None, "records", "raw"), (2, "records", "support-roots>=2"), (1, "records", "support-roots>=1"),
            (2, "users", "reporters-roots>=2"), (1, "users", "reporters-roots>=1")]
res = {}
print("## A4. TB_v4 pass counts over the WHOLE pool (3,178 keys, rule weeks, model B), raw vs echo veto\n")
print("| variant | " + " | ".join(CLASSES) + " | overcounted d4 removed |"); print("|" + "---|" * (len(CLASSES) + 2))
for T, field, name in VARIANTS:
    cnt = Counter(); over_removed = 0; over_n = 0
    for seed in range(6):
        D = load(seed); widx = {w: i for i, w in enumerate(D["weeks"])}
        for key, v in D["keys"].items():
            W = [widx[w] for w in v["rule_weeks"]]
            if not W: continue
            ok = passes(v, W, "blind", T, field)
            if ok:
                for c in classes_of(v): cnt[c] += 1
            if "d4" in classes_of(v):
                # oracle: raw pass but dedupe never passes
                raw = any(tb_v4(site_stats(window_recs(v["recs"], w - WIN + 1, w), "blind"), w)[0] for w in W)
                ded = any(tb_v4(site_stats(dedupe_window(window_recs(v["recs"], w - WIN + 1, w), "blind"), "blind"), w)[0] for w in W)
                if raw and not ded:
                    over_n += 1; over_removed += not ok
    print(f"| {name} | " + " | ".join(str(cnt[c]) for c in CLASSES) + f" | {over_removed}/{over_n} |")
    res[name] = {"pass_by_class": dict(cnt), "overcounted_d4_removed": [over_removed, over_n]}

print("\n## A5. Ceiling cost of the veto: 240 gold patterns, label windows, TB_v4\n")
print("| model | variant | feasible | per seed | lost |"); print("|---|---|---|---|---|")
for model, alpha in (("B", "blind"), ("K", 0)):
    for T, field, name in VARIANTS:
        tot = 0; ps = []; lost = []
        for seed in range(6):
            D = load(seed); widx = {w: i for i, w in enumerate(D["weeks"])}
            n = 0
            for key, v in D["keys"].items():
                if v["class"] != "gold": continue
                g = v["gold"]; lo = widx[g["window"][0]]
                hi = widx[g["window"][1]] if g["window"][1] in widx else len(D["weeks"]) - 1
                ok = passes(v, list(range(lo, hi + 1)), alpha, T, field)
                n += ok
                if not ok and T is not None and passes(v, list(range(lo, hi + 1)), alpha, None, field):
                    lost.append(f"s{seed}:{g['pattern']}{'(rare)' if g['rare'] else ''}")
            tot += n; ps.append(n)
        print(f"| {model} | {name} | {tot}/240 = {tot/240:.4f} | {ps} | {len(lost)} {lost} |")
        res[f"ceiling:{model}:{name}"] = {"feasible": tot, "per_seed": ps, "lost": lost}

print("\n## A6. AUC on the filtered pool (rule_only, rule_weeks >= 7) for both signatures (score high = echo-like)\n")
print("| signature | AUC d4 vs gold | AUC all decoy vs gold | AUC other vs gold | gold>=2 | d4>=2 | d4>=1 | other>=2 |")
print("|" + "---|" * 8)
for field, name in (("records", "support-roots"), ("users", "reporters-roots")):
    sc = defaultdict(list)
    for seed in range(6):
        D = load(seed); widx = {w: i for i, w in enumerate(D["weeks"])}
        for key, v in D["keys"].items():
            if v["detector_state"] != "rule_only" or len(v["rule_weeks"]) < 7: continue
            best = -1
            for w in [widx[x] for x in v["rule_weeks"]]:
                for s, x in site_stats(window_recs(v["recs"], w - WIN + 1, w), "blind").items():
                    if x["records"] >= K: best = max(best, bidx(x[field]) - bidx(x["events"]))
            for c in classes_of(v): sc[c].append(best)
            if v["class"] == "decoy": sc["decoy"].append(best)
    print(f"| {name} | {auc(sc['d4'], sc['gold']):.3f} | {auc(sc['decoy'], sc['gold']):.3f} | {auc(sc['other'], sc['gold']):.3f} | "
          f"{sum(x >= 2 for x in sc['gold'])}/{len(sc['gold'])} | {sum(x >= 2 for x in sc['d4'])}/{len(sc['d4'])} | "
          f"{sum(x >= 1 for x in sc['d4'])}/{len(sc['d4'])} | {sum(x >= 2 for x in sc['other'])}/{len(sc['other'])} |")
    res[f"auc:{name}"] = {"d4_vs_gold": auc(sc["d4"], sc["gold"]), "decoy_vs_gold": auc(sc["decoy"], sc["gold"]),
                          "dist": {c: dict(Counter(sc[c])) for c in CLASSES}}
json.dump(res, open(f"{OUT}/results_A2.json", "w"), indent=1, default=str)
