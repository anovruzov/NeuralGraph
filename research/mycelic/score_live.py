"""Score the direct model measurements and place them on the capability axis.

Output: for each measured model, the five operator accuracies, plus the value
of `q` whose simulated tier vector best matches those accuracies (least squares
over the measured components).  That `q` is the *measured anchor*; every other
position on the axis in this study remains an assumption.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np

from .models import tier_from_q
from .runner import ART

KEYDIR = os.path.join(os.path.dirname(__file__), "keys")

FILES = {
    "claude-haiku-4.5": "live_answers_haiku.json",
    "claude-sonnet-5": "live_answers_sonnet.json",
    "claude-opus-5": "live_answers_opus.json",
}


def _index(items: List[Dict]) -> Dict[str, Dict]:
    return {str(x.get("id")): x for x in items if isinstance(x, dict)}


def score_one(tasks: Dict, ans: Dict, key: Dict) -> Dict[str, float]:
    out: Dict[str, float] = {}
    T = tasks["tasks"]

    # T1 extraction: predicate recall and entity fidelity
    a1 = _index(ans.get("T1", []))
    pr_ok = ent_ok = n1 = 0
    for it in T["T1"]:
        g = a1.get(it["id"])
        n1 += 1
        if not g:
            continue
        kk = key[it["id"]]
        if str(g.get("predicate", "")).strip().lower() == kk["predicate"]:
            pr_ok += 1
        e = str(g.get("entity", "")).strip().lower()
        if e == str(kk["entity"]).lower():
            ent_ok += 1
    out["extract_recall"] = pr_ok / max(1, n1)
    out["entity_fidelity"] = ent_ok / max(1, n1)
    out["n_T1"] = n1

    # T2 causal check (balanced accuracy)
    a2 = _index(ans.get("T2", []))
    tp = tn = pos = neg = 0
    for it in T["T2"]:
        g = a2.get(it["id"])
        want = key[it["id"]]["answer"]
        got = str(g.get("answer", "")).strip().lower() if g else ""
        if want == "yes":
            pos += 1
            tp += got == "yes"
        else:
            neg += 1
            tn += got == "no"
    out["causal_check"] = 0.5 * (tp / max(1, pos) + tn / max(1, neg))
    out["n_T2"] = pos + neg

    # T3 temporal check (3-way, macro accuracy)
    a3 = _index(ans.get("T3", []))
    per: Dict[str, List[int]] = {}
    for it in T["T3"]:
        g = a3.get(it["id"])
        got = str(g.get("answer", "")).strip().lower() if g else ""
        gold = key[it["id"]]["answer"]
        per.setdefault(gold, [0, 0])
        per[gold][1] += 1
        per[gold][0] += got == gold
    out["temporal_check"] = float(np.mean([v[0] / max(1, v[1])
                                           for v in per.values()])) if per else 0.0
    out["n_T3"] = sum(v[1] for v in per.values())

    # T4 entity linking (balanced accuracy)
    a4 = _index(ans.get("T4", []))
    tp = tn = pos = neg = 0
    for it in T["T4"]:
        g = a4.get(it["id"])
        got = str(g.get("answer", "")).strip().lower() if g else ""
        if key[it["id"]]["answer"] == "same":
            pos += 1
            tp += got == "same"
        else:
            neg += 1
            tn += got == "different"
    out["entity_check"] = 0.5 * (tp / max(1, pos) + tn / max(1, neg))
    out["n_T4"] = pos + neg

    # T5 independence: exact-count accuracy
    a5 = _index(ans.get("T5", []))
    ok = n5 = 0
    errs = []
    for it in T["T5"]:
        g = a5.get(it["id"])
        n5 += 1
        gold = int(key[it["id"]]["answer"])
        try:
            v = int(g.get("answer"))
        except Exception:
            errs.append(float(gold))
            continue
        ok += v == gold
        errs.append(abs(v - gold))
    out["dedup_check"] = ok / max(1, n5)
    out["dedup_mae"] = float(np.mean(errs)) if errs else 0.0
    out["n_T5"] = n5
    return out


MEASURED_FIELDS = ["extract_recall", "entity_fidelity", "causal_check",
                   "temporal_check", "entity_check", "dedup_check"]


def fit_q(meas: Dict[str, float], shape: str = "convex") -> float:
    qs = np.linspace(0.01, 1.0, 199)
    best, bq = 1e9, 0.5
    for q in qs:
        t = tier_from_q(float(q), shape=shape)
        err = 0.0
        for f in MEASURED_FIELDS:
            if f in meas:
                err += (getattr(t, f) - meas[f]) ** 2
        if err < best:
            best, bq = err, float(q)
    return bq


def main() -> Dict[str, object]:
    tasks = json.load(open(os.path.join(ART, "live_tasks.json")))
    key = json.load(open(os.path.join(KEYDIR, "live_key.json")))
    res: Dict[str, object] = {"task_counts": {k: len(v)
                                              for k, v in tasks["tasks"].items()}}
    per = {}
    for model, fn in FILES.items():
        p = os.path.join(ART, fn)
        if not os.path.exists(p):
            continue
        ans = json.load(open(p))
        s = score_one(tasks, ans, key)
        s["fitted_q"] = fit_q(s)
        t = tier_from_q(s["fitted_q"])
        s["simulated_at_fitted_q"] = {f: round(getattr(t, f), 4)
                                      for f in MEASURED_FIELDS}
        per[model] = s
    res["measurements"] = per
    with open(os.path.join(ART, "live_calibration.json"), "w") as fh:
        json.dump(res, fh, indent=2)
    return res


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, indent=2))
