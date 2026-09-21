"""Direct measurement of the operator that actually binds: CANDIDATE
DISCRIMINATION.

The five primitive-operator tasks (live_tasks.py) saturate at 1.00 for
frontier-class models.  That is a real result - those primitives are solved -
but it means they cannot explain why enterprise-scale discovery is hard, and
they cannot discriminate between model tiers where the architecture needs it.

The operator that does bind is the kernel's judgement over a *candidate list*:
given several hundred entity-chain hypotheses, each with its evidence
statistics, which ones are genuine emerging cross-organisational risks?  In the
simulator this is a calibrated logistic over (links, weakest-link independent
support, branch spread, links-in-distinct-branches, chain validity,
contradictions).  If a real frontier model ranks these materially better than
that logistic, the simulator understates the value of top-tier compute at the
kernel and every allocation conclusion has to be revisited.  If it does not,
the simulation is fair.

The candidates are taken from a real run of the benchmark, so the difficulty,
the class balance and the feature distributions are the ones the architecture
actually produces - not a hand-made puzzle.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

import numpy as np

from .corpus import CAUSAL_CHAINS, PREDICATES, PRED_ID
from .evalm import _match_sets
from .experiments import _hier
from .models import allocation
from .org import REGION, SITE, USER
from .ops import stem_rep_map
from .runner import build_world, ART
from .systems import HierRunner

KEYDIR = os.path.join(os.path.dirname(__file__), "keys")
N_ITEMS = 60


def _render(h, corpus, rank: int) -> Dict[str, object]:
    by_pred: Dict[int, Dict[str, object]] = {}
    for k in h.kos:
        d = by_pred.setdefault(int(k.pred), {"sigs": set(), "sites": set(),
                                             "regions": set(), "tmin": 10 ** 9,
                                             "tmax": -1, "raw": 0, "neg": 0})
        d["sigs"] |= k.sigs
        d["sites"] |= k.branches.get(SITE, set())
        d["regions"] |= k.branches.get(REGION, set())
        d["tmin"] = min(d["tmin"], k.tmin)
        d["tmax"] = max(d["tmax"], k.tmax)
        d["raw"] += k.n_raw
        d["neg"] += 1 if k.polarity < 0 else 0
    links = []
    order = sorted(by_pred.items(), key=lambda kv: kv[1]["tmin"])
    for p, d in order:
        links.append({
            "event": PREDICATES[p],
            "first_seen_day": int(d["tmin"]),
            "last_seen_day": int(d["tmax"]),
            "independent_sources": int(len(d["sigs"])),
            "total_reports": int(d["raw"]),
            "distinct_sites": int(len(d["sites"])),
            "distinct_regions": int(len(d["regions"])),
            "contradicting_reports": int(d["neg"]),
        })
    return {
        "id": f"C-{rank}",
        "entity": corpus.entities[int(h.anchor)],
        "links": links,
        "n_links": len(links),
        "distinct_regions_total": int(h.n_branch_regions),
        "distinct_sites_total": int(h.n_branch_sites),
        "contradiction_count": int(h.contra),
    }


def build(seed: int = 901, scale: int = 10_000) -> Dict[str, object]:
    w = build_world(scale, seed)
    alloc = allocation("back-loaded")
    cfg = _hier(downward_retrieval=True, questions=True, cross_links=True)
    res = HierRunner(w.corpus, alloc, cfg, seed=seed,
                     ul=w.user_layer(alloc[USER], seed),
                     near_miss=w.near_miss).run()
    hyps = res.hypotheses
    stem = stem_rep_map(w.corpus)
    real = [p for p in w.corpus.patterns if p.pid in set(w.gold.discoverable)]
    m = _match_sets(hyps, real, stem, False, "primary")
    gold_idx = set()
    for v in m.values():
        gold_idx.update(v)

    rng = np.random.default_rng(seed)
    n_gold = min(len(gold_idx), N_ITEMS // 4)
    g = sorted(gold_idx)[:n_gold]
    others = [i for i in range(len(hyps)) if i not in gold_idx
              and not hyps[i].hallucinated and len(hyps[i].kos) > 0]
    # sample distractors from across the simulator's own confidence range, so
    # the model is not handed an easy split
    others_sorted = sorted(others, key=lambda i: -hyps[i].conf)
    take = N_ITEMS - n_gold
    if len(others_sorted) > take:
        pick = np.linspace(0, len(others_sorted) - 1, take).astype(int)
        others_sorted = [others_sorted[i] for i in pick.tolist()]
    chosen = g + others_sorted
    order = rng.permutation(len(chosen))
    items, key = [], {}
    for rank, ci in enumerate([chosen[i] for i in order.tolist()]):
        it = _render(hyps[ci], w.corpus, rank)
        items.append(it)
        key[it["id"]] = {
            "is_real": bool(ci in gold_idx),
            "simulator_confidence": float(hyps[ci].conf),
        }
    spec = {
        "seed": seed, "scale": scale,
        "n_items": len(items),
        "causal_chains": CAUSAL_CHAINS,
        "instructions_summary":
            "Each candidate is one entity plus the operational events reported "
            "about it across the enterprise. Decide which candidates are "
            "genuine emerging cross-organisational risks.",
        "candidates": items,
    }
    with open(os.path.join(ART, "live_rank_tasks.json"), "w") as fh:
        json.dump(spec, fh, indent=1)
    os.makedirs(KEYDIR, exist_ok=True)
    with open(os.path.join(KEYDIR, "live_rank_key.json"), "w") as fh:
        json.dump(key, fh, indent=1)
    return spec


def _ap(ranked_ids: List[str], key: Dict) -> float:
    gold = [i for i, k in key.items() if k["is_real"]]
    if not gold:
        return 0.0
    pos = {rid: i for i, rid in enumerate(ranked_ids)}
    found = sorted((pos[g] for g in gold if g in pos))
    ap = 0.0
    for r, idx in enumerate(found, start=1):
        ap += r / (idx + 1)
    return ap / len(gold)


def score() -> Dict[str, object]:
    spec = json.load(open(os.path.join(ART, "live_rank_tasks.json")))
    key = json.load(open(os.path.join(KEYDIR, "live_rank_key.json")))
    ids = [c["id"] for c in spec["candidates"]]
    n_gold = sum(1 for k in key.values() if k["is_real"])
    out: Dict[str, object] = {"n_items": len(ids), "n_real": n_gold}

    # reference 1: the simulator's own logistic
    sim = sorted(ids, key=lambda i: -key[i]["simulator_confidence"])
    out["simulator_logistic_ap"] = _ap(sim, key)
    # reference 2: random
    rng = np.random.default_rng(5)
    aps = []
    for _ in range(2000):
        r = list(ids)
        rng.shuffle(r)
        aps.append(_ap(r, key))
    out["random_ap"] = float(np.mean(aps))

    models = {}
    for fn in sorted(os.listdir(ART)):
        if not fn.startswith("live_rank_answers_"):
            continue
        name = fn[len("live_rank_answers_"):-len(".json")]
        a = json.load(open(os.path.join(ART, fn)))
        ranked = [str(x) for x in a.get("ranking", []) if str(x) in key]
        missing = [i for i in ids if i not in set(ranked)]
        ranked = ranked + missing
        picks = set(str(x) for x in a.get("real", []))
        tp = sum(1 for p in picks if key.get(p, {}).get("is_real"))
        prec = tp / max(1, len(picks))
        rec = tp / max(1, n_gold)
        models[name] = {
            "ap": _ap(ranked, key),
            "selection_precision": prec,
            "selection_recall": rec,
            "selection_f1": 2 * prec * rec / max(1e-9, prec + rec),
            "n_selected": len(picks),
            "n_ranked_returned": len(a.get("ranking", [])),
        }
    out["models"] = models
    with open(os.path.join(ART, "live_rank_results.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "score":
        print(json.dumps(score(), indent=2))
    else:
        s = build()
        print("wrote live_rank_tasks.json with", s["n_items"], "candidates")
        print(json.dumps(s["candidates"][0], indent=1)[:900])
