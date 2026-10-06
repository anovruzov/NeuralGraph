"""Build a deterministic operator test-set from the real corpus, for DIRECT
measurement on real models.

The simulator parameterises five operator abilities.  This file turns each of
them into a concrete, gradeable task over text produced by the same generator
the benchmark uses, so that the mapping "model class -> capability vector" has
at least a few measured anchors instead of being asserted end to end.

  T1 extraction        text -> (predicate, entity)
  T2 causal check      claim set -> is this one causal chain?
  T3 temporal check    claim set -> is it in causal order / has it been retracted?
  T4 entity linking    two mention strings -> same entity or not?
  T5 independence      claim set with echoes -> how many independent sources?

What this measures is the *operator*, not the architecture.  It is a
calibration of the simulator's inputs; no end-to-end enterprise result is
claimed to be directly measured.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

import numpy as np

from .corpus import (CAUSAL_CHAINS, PREDICATES, PRED_ID, PRED_SURFACE,
                     build_corpus)
from .org import build_org
from .runner import ART

KEYDIR = os.path.join(os.path.dirname(__file__), "keys")

N_T1 = 48
N_T2 = 48
N_T3 = 36
N_T4 = 36
N_T5 = 30


def build(seed: int = 900, scale: int = 2000) -> Dict[str, object]:
    org = build_org(scale, seed=seed)
    cp = build_corpus(org, seed=seed)
    rng = np.random.default_rng(seed)
    recs = cp.recs

    tasks: Dict[str, List[Dict]] = {"T1": [], "T2": [], "T3": [], "T4": [],
                                    "T5": []}
    key: Dict[str, Dict[str, object]] = {}

    # ---- T1 extraction ----
    # Only records with a single entity mention: in a two-mention note nothing
    # grammatically attaches the predicate to either token, so half the items
    # would be decided by a positional convention rather than by reading.
    # (Flagged by the first blind measurement run.)
    single = np.nonzero(recs["aux"] < 0)[0]
    ids = rng.choice(single, N_T1, replace=False)
    for i in ids.tolist():
        r = recs[i]
        tasks["T1"].append({"id": f"T1-{i}", "text": cp.text(int(i))})
        key[f"T1-{i}"] = {"predicate": PREDICATES[int(r["pred"])],
                          "entity": cp.entities[int(r["anchor"])]}

    # ---- T2 causal chain membership ----
    # The yes/no label is drawn at random, NOT alternated by index: an
    # alternating label lets a model score well by noticing the pattern
    # instead of by reasoning.  (Found by the first measurement run.)
    t2_lab = rng.random(N_T2) < 0.5
    for k in range(N_T2):
        real = bool(t2_lab[k])
        ci = int(rng.integers(0, len(CAUSAL_CHAINS)))
        ch = CAUSAL_CHAINS[ci]
        L = int(rng.integers(3, min(5, len(ch)) + 1))
        sp = int(rng.integers(0, len(ch) - L + 1))
        ent = cp.entities[int(rng.integers(0, len(cp.entities)))]
        if real:
            preds = [ch[sp + j] for j in range(L)]
        else:
            preds = []
            for _ in range(L):
                cj = int(rng.integers(0, len(CAUSAL_CHAINS)))
                preds.append(CAUSAL_CHAINS[cj][
                    int(rng.integers(0, len(CAUSAL_CHAINS[cj])))])
            # a "no" item must genuinely span more than one chain
            chains_used = set()
            for pp in preds:
                for cj2, ch2 in enumerate(CAUSAL_CHAINS):
                    if pp in ch2:
                        chains_used.add(cj2)
            if len(chains_used) < 2 or len(set(preds)) < 2:
                preds = [ch[sp + j] for j in range(L)]
                real = True
        claims = []
        for j, p in enumerate(preds):
            surf = PRED_SURFACE[p][int(rng.integers(0, len(PRED_SURFACE[p])))]
            claims.append(f"day {10 + 12 * j:3d}: {surf} ({ent})")
        tasks["T2"].append({"id": f"T2-{k}", "claims": claims, "entity": ent})
        key[f"T2-{k}"] = {"answer": "yes" if real else "no"}

    # ---- T3 temporal order / retraction ----
    t3_mode = rng.integers(0, 3, N_T3)
    for k in range(N_T3):
        mode = int(t3_mode[k])   # 0 ordered, 1 scrambled, 2 retracted
        ci = int(rng.integers(0, len(CAUSAL_CHAINS)))
        ch = CAUSAL_CHAINS[ci]
        L = min(4, len(ch))
        sp = int(rng.integers(0, len(ch) - L + 1))
        ent = cp.entities[int(rng.integers(0, len(cp.entities)))]
        times = [10 + 12 * j for j in range(L)]
        if mode == 1:
            times = times[::-1]
        claims = []
        for j in range(L):
            p = ch[sp + j]
            surf = PRED_SURFACE[p][int(rng.integers(0, len(PRED_SURFACE[p])))]
            claims.append(f"day {times[j]:3d}: {surf} ({ent})")
        if mode == 2:
            for j in range(L):
                p = ch[sp + j]
                surf = PRED_SURFACE[p][0]
                claims.append(f"day {times[-1] + 40 + j:3d}: NOT {surf} - "
                              f"earlier report withdrawn ({ent})")
        gold = {0: "current", 1: "out_of_order", 2: "retracted"}[mode]
        tasks["T3"].append({"id": f"T3-{k}", "claims": claims, "entity": ent})
        key[f"T3-{k}"] = {"answer": gold}

    # ---- T4 entity linking ----
    stems: Dict[str, List[str]] = {}
    for e in cp.entities:
        stems.setdefault(e[:5], []).append(e)
    sibs = [v for v in stems.values() if len(v) >= 2]
    t4_lab = rng.random(N_T4) < 0.5
    used_pairs = set()
    for k in range(N_T4):
        same = bool(t4_lab[k])
        if same:
            e = cp.entities[int(rng.integers(0, len(cp.entities)))]
            a, b = e, e
            # same referent, different surface wrapping
            b = e.upper() if rng.random() < 0.5 else f"the {e} platform"
        else:
            grp = sibs[int(rng.integers(0, len(sibs)))] if sibs else \
                [cp.entities[0], cp.entities[1]]
            a, b = grp[0], grp[1]
        tries = 0
        while (a, b) in used_pairs and tries < 40:
            tries += 1
            if same:
                e = cp.entities[int(rng.integers(0, len(cp.entities)))]
                a = e
                b = e.upper() if rng.random() < 0.5 else f"the {e} platform"
            elif sibs:
                grp = sibs[int(rng.integers(0, len(sibs)))]
                i2 = int(rng.integers(0, len(grp)))
                j2 = (i2 + 1 + int(rng.integers(0, len(grp) - 1))) % len(grp)
                a, b = grp[i2], grp[j2]
        used_pairs.add((a, b))
        tasks["T4"].append({"id": f"T4-{k}", "a": a, "b": b})
        key[f"T4-{k}"] = {"answer": "same" if same else "different"}

    # ---- T5 independent support ----
    for k in range(N_T5):
        n_indep = int(rng.integers(1, 5))
        ent = cp.entities[int(rng.integers(0, len(cp.entities)))]
        p = PREDICATES[int(rng.integers(0, 34))]
        surfs = PRED_SURFACE[p]
        lines = []
        for j in range(n_indep):
            base = surfs[j % len(surfs)]
            lines.append(f"[team-{j}] day {20+j}: {base} affecting {ent}; "
                         f"logged by owner {j}")
            n_echo = int(rng.integers(0, 4))
            for e_ in range(n_echo):
                lines.append(f"[team-{j}-relay-{e_}] day {21+j}: {base} "
                             f"affecting {ent}; forwarded from owner {j}")
        idx = rng.permutation(len(lines))
        tasks["T5"].append({"id": f"T5-{k}",
                            "reports": [lines[i] for i in idx.tolist()],
                            "entity": ent})
        key[f"T5-{k}"] = {"answer": int(n_indep)}

    # shuffle item order within each group so no positional regularity remains
    for g in tasks:
        order = rng.permutation(len(tasks[g]))
        tasks[g] = [tasks[g][i] for i in order.tolist()]

    spec = {
        "seed": seed, "scale": scale,
        # the FULL predicate vocabulary, including the routine/background
        # predicates: the first version listed only the 34 operational ones,
        # so 30 of the 48 extraction items had an out-of-vocabulary gold label
        "predicate_vocabulary": PREDICATES,
        "operational_predicates": PREDICATES[:34],
        "causal_chains": CAUSAL_CHAINS,
        "tasks": tasks,
    }
    path = os.path.join(ART, "live_tasks.json")
    with open(path, "w") as fh:
        json.dump(spec, fh, indent=1)
    # The answer key lives OUTSIDE the directory the measured model is pointed
    # at.  The first version inlined gold labels next to every item; the
    # second put the key in the same folder, which a measurement run correctly
    # called out as a leakage hazard even though it did not open it.
    os.makedirs(KEYDIR, exist_ok=True)
    with open(os.path.join(KEYDIR, "live_key.json"), "w") as fh:
        json.dump(key, fh, indent=1)
    return spec


if __name__ == "__main__":
    s = build()
    print("wrote", os.path.join(ART, "live_tasks.json"))
    for k, v in s["tasks"].items():
        print(" ", k, len(v), "items; example:",
              json.dumps(v[0])[:180])
