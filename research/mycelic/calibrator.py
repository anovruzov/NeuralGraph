"""Learned confidence calibration over kernel-side features.

The loss accounting shows the hierarchy's binding constraint is not
retrieval: with a full question budget it holds evidence for ~98% of hidden
patterns and forms a correct confident candidate for ~80%, and then buries
them under spurious candidates that the hand-set confidence logistic rates
just as highly.  The triage pre-selects anchors that already satisfy the
logistic's main terms (a causal span, several regions), so among the
candidates the kernel actually has to rank, those terms carry almost no
information.

This module asks the only question that matters before building anything:
**using features the kernel already holds, how separable are the genuine
candidates from the spurious ones?**  It dumps every candidate the synthesis
produced (pre-register-cut), labels it against gold, fits a logistic on the
CALIBRATION seeds (500-502), and re-ranks the EVALUATION seeds' candidates to
see how much discovery a better ranking alone recovers under the same
register cap and the same confidence threshold.

Nothing here reads raw text, and every feature is a statistic the kernel
computes from the objects it already receives, so the privacy accounting is
unchanged.  The calibrator is one shared model, fitted on the pooled
candidates of every architecture, so no system gets a private ranker.

    python3 -m research.mycelic.calibrator dump     # writes hyp_features_*.jsonl
    python3 -m research.mycelic.calibrator fit      # fits, evaluates, prints
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .evalm import _match_sets
from .models import allocation
from .ops import stem_rep_map
from .org import USER
from .runner import ARCHS, ART, build_world, hier_cfg, run_arch
from .systems import HierRunner

CAL_SEEDS = (500, 501, 502)
EVAL_SEEDS = (0, 1, 2, 3, 4)
TAU = 0.5

FEATURES = [
    "n_links", "min_sup", "mean_sup", "spread", "multi", "n_indep",
    "n_sites", "n_regions", "contra", "conflict", "dispersion", "synchrony",
    "verified", "penalty", "tspan", "max_lag", "min_lag", "neg_lag",
    "log_n_kos", "from_question", "attribution",
]


def _features(h) -> Dict[str, float]:
    lags = list(h.link_lag) if h.link_lag else [0]
    sup = list(h.link_sup) if h.link_sup else [h.n_indep]
    return {
        "n_links": float(len(h.preds)),
        "min_sup": float(min(sup)),
        "mean_sup": float(np.mean(sup)),
        "spread": float(h.spread),
        "multi": float(h.multi),
        "n_indep": float(min(50, h.n_indep)),
        "n_sites": float(h.n_branch_sites),
        "n_regions": float(h.n_branch_regions),
        "contra": float(h.contra),
        "conflict": float(h.conflict),
        "dispersion": float(h.dispersion),
        "synchrony": float(h.synchrony),
        "verified": 1.0 if h.verified else 0.0,
        "penalty": float(h.penalty),
        "tspan": float(h.tspan[1] - h.tspan[0]),
        "max_lag": float(max(lags)),
        "min_lag": float(min(lags)),
        "neg_lag": 1.0 if min(lags) < 0 else 0.0,
        "log_n_kos": float(math.log1p(len(h.kos))),
        "from_question": 1.0 if h.from_question >= 0 else 0.0,
        "attribution": float(h.attribution),
    }


def dump(scale: int = 10_000, seeds: Sequence[int] = CAL_SEEDS + EVAL_SEEDS,
         archs: Sequence[str] = ("H_mycelic_full", "A2_chunked_ctx",
                                 "B4_central_triage", "Y_oracle_retrieval"),
         cfg_over: Optional[Dict] = None, tag: str = "",
         alloc_name: str = "back-loaded") -> str:
    """One row per candidate hypothesis, labelled against gold."""
    path = os.path.join(ART, f"hyp_features{tag}.jsonl")
    alloc = allocation(alloc_name)
    with open(path, "w") as fh:
        for seed in seeds:
            t0 = time.time()
            w = build_world(scale, seed)
            stem = stem_rep_map(w.corpus)
            pats = {p.pid: p for p in w.corpus.patterns}
            real = [pats[i] for i in w.gold.discoverable]
            decoys = [p for p in w.corpus.patterns if not p.real]
            for arch in archs:
                if ARCHS[arch]["kind"] == "hier":
                    over = dict(ARCHS[arch].get("cfg", {}))
                    if cfg_over:
                        over.update(cfg_over)
                    cfg = hier_cfg(**over)
                    runner = HierRunner(w.corpus, alloc, cfg, seed=seed,
                                        ul=w.user_layer(alloc[USER], seed),
                                        near_miss=w.near_miss)
                    res = runner.run()
                    hyps = list(runner.h.full_hyps)      # pre-cut
                    cap = cfg.max_reports
                else:
                    res = run_arch(arch, w, alloc, seed)
                    hyps = list(res.hypotheses)
                    cap = int(min(6000, max(600, len(w.corpus.entities))))
                m_real = _match_sets(hyps, real, stem, lenient=False)
                m_dec = _match_sets(hyps, decoys, stem, lenient=False)
                gold_idx: Dict[int, int] = {}
                for pid, idxs in m_real.items():
                    for i in idxs:
                        gold_idx[i] = pid
                dec_idx = {i for v in m_dec.values() for i in v}
                for i, h in enumerate(hyps):
                    row = {"arch": arch, "scale": scale, "seed": seed, "i": i,
                           "cap": cap, "conf": float(h.conf),
                           "gold": i in gold_idx, "pid": gold_idx.get(i, -1),
                           "decoy": i in dec_idx, "anchor": int(h.anchor),
                           "chain": int(h.chain), "rare": bool(
                               pats[gold_idx[i]].rare) if i in gold_idx else False,
                           **_features(h)}
                    fh.write(json.dumps(row) + "\n")
                print(f"  seed {seed} {arch:20s} {len(hyps)} candidates, "
                      f"{len(gold_idx)} gold-matching, {len(m_real)} patterns "
                      f"matched ({time.time() - t0:.0f}s)", flush=True)
            w.clear_cache()
    return path


# --------------------------------------------------------------------------
# A small, dependency-free logistic regression (Newton's method, L2).
# --------------------------------------------------------------------------

def _standardise(X: np.ndarray, mu=None, sd=None):
    if mu is None:
        mu = X.mean(axis=0)
        sd = X.std(axis=0) + 1e-9
    return (X - mu) / sd, mu, sd


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 iters: int = 60) -> np.ndarray:
    n, d = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(d + 1)
    reg = np.full(d + 1, l2)
    reg[0] = 0.0
    # class weights so a 5% positive rate does not collapse to "always no"
    pos = max(1, int(y.sum()))
    wt = np.where(y > 0, (n - pos) / pos, 1.0)
    for _ in range(iters):
        z = Xb @ w
        p = 1.0 / (1.0 + np.exp(-z))
        g = Xb.T @ (wt * (p - y)) + reg * w
        s = wt * p * (1 - p)
        H = (Xb * s[:, None]).T @ Xb + np.diag(reg)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return w


def predict(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    Xb = np.hstack([np.ones((len(X), 1)), X])
    return 1.0 / (1.0 + np.exp(-(Xb @ w)))


def auc(score: np.ndarray, y: np.ndarray) -> float:
    order = np.argsort(score)
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    npos = y.sum()
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return float("nan")
    return float((ranks[y > 0].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def _found_under_cap(rows: List[Dict], score: np.ndarray, thresh: float,
                     n_gold_by_seed: Dict[int, int]) -> Tuple[float, float, int]:
    """Discovery if candidates were ranked by `score`, kept while score >=
    thresh, and cut at each run's own register cap.  Returns (found,
    rare found, n kept)."""
    by_run: Dict[Tuple[str, int], List[int]] = {}
    for i, r in enumerate(rows):
        by_run.setdefault((r["arch"], r["seed"]), []).append(i)
    found = rare_found = 0
    n_kept = 0
    rare_total = 0
    for (arch, seed), idx in by_run.items():
        idx = sorted(idx, key=lambda i: -score[i])
        cap = rows[idx[0]]["cap"]
        kept = [i for i in idx if score[i] >= thresh][:cap]
        n_kept += len(kept)
        seen = set()
        for i in kept:
            if rows[i]["gold"] and rows[i]["pid"] not in seen:
                seen.add(rows[i]["pid"])
                found += 1
                rare_found += int(rows[i]["rare"])
    return found, rare_found, n_kept


def evaluate_calibrator(tag: str = "", arch_filter: Optional[str] = None,
                        train_archs: Optional[Sequence[str]] = None) -> Dict:
    path = os.path.join(ART, f"hyp_features{tag}.jsonl")
    rows = [json.loads(l) for l in open(path)]
    if arch_filter:
        rows_eval = [r for r in rows if r["arch"] == arch_filter]
    else:
        rows_eval = rows
    tr = [r for r in rows if r["seed"] in CAL_SEEDS
          and (train_archs is None or r["arch"] in train_archs)]
    te = [r for r in rows_eval if r["seed"] in EVAL_SEEDS]
    Xtr = np.array([[r[f] for f in FEATURES] for r in tr])
    ytr = np.array([1.0 if r["gold"] else 0.0 for r in tr])
    Xte = np.array([[r[f] for f in FEATURES] for r in te])
    yte = np.array([1.0 if r["gold"] else 0.0 for r in te])
    Xtr_s, mu, sd = _standardise(Xtr)
    Xte_s, _, _ = _standardise(Xte, mu, sd)
    w = fit_logistic(Xtr_s, ytr)
    p_te = predict(Xte_s, w)
    conf_te = np.array([r["conf"] for r in te])
    # The denominator is EVERY discoverable gold pattern in the world, not the
    # ones that happened to have a matching candidate - "found" means found.
    # The funnel summary rows carry the count per (scale, seed).
    n_gold_world: Dict[int, int] = {}
    fp = os.path.join(ART, "loss_funnel.jsonl")
    if os.path.exists(fp):
        for line in open(fp):
            rr = json.loads(line)
            if rr.get("summary") and rr["scale"] == te[0]["scale"]:
                n_gold_world[rr["seed"]] = rr["n_gold"]
    n_gold = {}
    for r in te:
        if r["gold"]:
            n_gold.setdefault((r["arch"], r["seed"]), set()).add(r["pid"])
    out = {"tag": tag, "arch": arch_filter or "all",
           "n_train": len(tr), "n_test": len(te),
           "pos_rate_train": float(ytr.mean()),
           "auc_conf": auc(conf_te, yte), "auc_learned": auc(p_te, yte),
           "weights": {f: float(x) for f, x in zip(["bias"] + FEATURES, w)}}
    # discovery under the register cap, by architecture
    per_arch = {}
    for arch in sorted({r["arch"] for r in te}):
        idx = [i for i, r in enumerate(te) if r["arch"] == arch]
        sub = [te[i] for i in idx]
        seeds_here = sorted({r["seed"] for r in sub})
        gold_total = sum(n_gold_world.get(sd, 0) for sd in seeds_here) or \
            sum(len(v) for k, v in n_gold.items() if k[0] == arch)
        matchable = sum(len(v) for k, v in n_gold.items() if k[0] == arch)
        base = _found_under_cap(sub, conf_te[idx], TAU, {})
        # learned score: keep the same NUMBER of candidates the baseline kept
        # (so the comparison is at equal register pressure), ranked by p
        cal = _found_under_cap(sub, p_te[idx], 0.0, {})
        # and the honest version: a threshold on the learned probability
        cal_t = _found_under_cap(sub, p_te[idx], TAU, {})
        per_arch[arch] = {
            "gold_patterns": gold_total,
            "matchable_patterns": matchable,
            "found_baseline": base[0] / max(1, gold_total),
            "found_learned_rank_only": cal[0] / max(1, gold_total),
            "found_learned_thresholded": cal_t[0] / max(1, gold_total),
            "kept_baseline": base[2], "kept_learned_thresholded": cal_t[2],
            "auc_conf": auc(conf_te[idx], yte[idx]),
            "auc_learned": auc(p_te[idx], yte[idx]),
        }
    out["per_arch"] = per_arch
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fit"
    if cmd == "dump":
        # default budget for every system, and the full-budget hierarchy that
        # the loss accounting says is the regime where ranking binds
        dump(tag="")
        dump(archs=("H_mycelic_full",), cfg_over={"question_frac": 1.0},
             tag="_qf1")
    else:
        for tag in ("", "_qf1"):
            p = os.path.join(ART, f"hyp_features{tag}.jsonl")
            if not os.path.exists(p):
                continue
            r = evaluate_calibrator(tag)
            print(json.dumps({k: v for k, v in r.items() if k != "weights"},
                             indent=1))
            print("weights:", json.dumps(r["weights"], indent=1))
