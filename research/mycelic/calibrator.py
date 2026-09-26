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
    "n_links", "min_sup", "mean_sup", "max_sup", "spread", "multi", "n_indep",
    "n_sites", "n_regions", "contra", "conflict", "dispersion", "synchrony",
    "verified", "penalty", "tspan", "max_lag", "min_lag", "mean_lag", "neg_lag",
    "log_n_kos", "from_question", "q_evidence_frac", "attribution", "hand_conf",
    "n_same_anchor", "rank_in_anchor",
    "n_origin_users", "min_link_users", "echo_ratio", "frac_single_witness",
    "lag_cv", "span_per_link", "chain_len_frac",
    "triage_gain", "sk_total", "n_foreign_sites", "users_reached",
    "n_questions_anchor",
]

# a small, fixed set of interactions - chosen a priori from the mechanism,
# not searched over, so there is nothing to overfit with
INTERACTIONS = [("verified", "min_sup"), ("dispersion", "n_sites"),
                ("n_links", "min_sup"), ("tspan", "n_links"),
                ("from_question", "n_sites"), ("rank_in_anchor", "n_same_anchor")]


def _features(h) -> Dict[str, float]:
    f = dict(h.feat) if getattr(h, "feat", None) else {}
    f.setdefault("hand_conf", float(h.conf))
    for k in FEATURES:
        f.setdefault(k, 0.0)
    return {k: float(f[k]) for k in FEATURES}


def _design(rows: List[Dict], interactions: bool):
    # a dump written before a feature existed carries it as 0, which is what
    # the runtime supplies when a system has no value for it either
    X = np.array([[r.get(f, 0.0) for f in FEATURES] for r in rows], dtype=float)
    if interactions:
        idx = {f: i for i, f in enumerate(FEATURES)}
        extra = np.stack([X[:, idx[a]] * X[:, idx[b]] for a, b in INTERACTIONS], axis=1)
        X = np.hstack([X, extra])
    return X


def dump(scale: int = 10_000, seeds: Sequence[int] = CAL_SEEDS + EVAL_SEEDS,
         archs: Sequence[str] = ("H_mycelic_full", "A2_chunked_ctx",
                                 "B4_central_triage", "Y_oracle_retrieval"),
         cfg_over: Optional[Dict] = None, tag: str = "",
         alloc_name: str = "back-loaded") -> str:
    """One row per candidate hypothesis, labelled against gold."""
    path = os.path.join(ART, f"hyp_features{tag}.jsonl")
    alloc = allocation(alloc_name)
    from . import ops as _ops
    _ops.set_ranker(None)          # features must be the un-ranked ones
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
        sd = X.std(axis=0)
        # A feature that is constant on the training dump (attribution is -1
        # everywhere unless verification ran) gets sd = 1, not 1e-9: with a
        # near-zero sd a different value at run time would be scaled by 1e9
        # and one stray weight would dominate the score.  Its weight is
        # exactly zero after fitting anyway; this keeps it harmless if a
        # later run supplies the feature.
        sd = np.where(sd < 1e-6, 1.0, sd)
    return (X - mu) / sd, mu, sd


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 iters: int = 60, row_weight: Optional[np.ndarray] = None) -> np.ndarray:
    n, d = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(d + 1)
    reg = np.full(d + 1, l2)
    reg[0] = 0.0
    # class weights so a 5% positive rate does not collapse to "always no"
    pos = max(1, int(y.sum()))
    wt = np.where(y > 0, (n - pos) / pos, 1.0)
    if row_weight is not None:
        wt = wt * row_weight
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


# --------------------------------------------------------------------------
# A small dependency-free gradient-boosted tree ensemble (depth-2 stumps on
# histogram splits, logistic loss).  Justified only because the linear
# ranker leaves a measured 26 points between "held evidence" and "reported";
# it is selected against the logistic on the calibration seeds, never adopted
# by default.
# --------------------------------------------------------------------------

def _best_split(X, g, h, lam):
    n, d = X.shape
    best = (0.0, -1, 0.0)
    G, H = g.sum(), h.sum()
    base = G * G / (H + lam)
    for j in range(d):
        col = X[:, j]
        qs = np.unique(np.quantile(col, np.linspace(0.05, 0.95, 19)))
        for t in qs:
            m = col <= t
            gl, hl = g[m].sum(), h[m].sum()
            if hl < 1e-9 or (H - hl) < 1e-9:
                continue
            gain = gl * gl / (hl + lam) + (G - gl) ** 2 / (H - hl + lam) - base
            if gain > best[0]:
                best = (gain, j, float(t))
    return best


def _grow(X, g, h, depth, lam):
    if depth == 0 or len(g) < 20:
        return {"leaf": -g.sum() / (h.sum() + lam)}
    gain, j, t = _best_split(X, g, h, lam)
    if j < 0 or gain <= 0:
        return {"leaf": -g.sum() / (h.sum() + lam)}
    m = X[:, j] <= t
    return {"f": int(j), "t": t, "l": _grow(X[m], g[m], h[m], depth - 1, lam),
            "r": _grow(X[~m], g[~m], h[~m], depth - 1, lam)}


def _tree_pred(node, X):
    if "leaf" in node:
        return np.full(len(X), node["leaf"])
    m = X[:, node["f"]] <= node["t"]
    out = np.empty(len(X))
    out[m] = _tree_pred(node["l"], X[m])
    out[~m] = _tree_pred(node["r"], X[~m])
    return out


def fit_gbdt(X: np.ndarray, y: np.ndarray, rounds: int = 150, lr: float = 0.1,
             depth: int = 2, lam: float = 1.0, pos_weight: Optional[float] = None,
             row_weight: Optional[np.ndarray] = None):
    n = len(y)
    pos = max(1, int(y.sum()))
    wpos = pos_weight if pos_weight is not None else (n - pos) / pos
    wt = np.where(y > 0, wpos, 1.0)
    if row_weight is not None:
        wt = wt * row_weight
    F = np.zeros(n)
    trees = []
    for _ in range(rounds):
        p = 1.0 / (1.0 + np.exp(-F))
        g = wt * (p - y)
        h = wt * p * (1 - p)
        t = _grow(X, g, h, depth, lam)
        trees.append(t)
        F += lr * _tree_pred(t, X)
    return {"kind": "gbdt", "trees": trees, "lr": lr}


def gbdt_predict(model, X: np.ndarray) -> np.ndarray:
    F = np.zeros(len(X))
    for t in model["trees"]:
        F += model["lr"] * _tree_pred(t, X)
    return 1.0 / (1.0 + np.exp(-F))


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


def _row_weight(rows: List[Dict], decoy_weight: float) -> np.ndarray:
    """Planted traps are a small share of the negatives, so a plain fit does
    not learn to reject them specifically; weighting them up in training is a
    hyper-parameter chosen on the calibration seeds like any other."""
    return np.array([decoy_weight if r.get("decoy") else 1.0 for r in rows])


def _loso_found(rows_by_tag: Dict[str, List[Dict]], l2: float,
                interactions: bool, kind: str = "logistic",
                decoy_weight: float = 1.0, decoy_penalty: float = 0.5) -> float:
    """Leave-one-seed-out over the CALIBRATION seeds only: fit on two, score
    found-under-cap (top-K by learned score, K = hand-gated count) on the
    third, for the hierarchy rows of every dump.  Sum of found over held-out
    cal seeds is the selection objective; evaluation seeds are never read."""
    total = 0
    for held in CAL_SEEDS:
        tr = [r for rows in rows_by_tag.values() for r in rows
              if r["seed"] in CAL_SEEDS and r["seed"] != held]
        X = _design(tr, interactions)
        y = np.array([1.0 if r["gold"] else 0.0 for r in tr])
        Xs, mu, sd = _standardise(X)
        rw = _row_weight(tr, decoy_weight)
        model = (fit_gbdt(Xs, y, row_weight=rw) if kind == "gbdt"
                 else fit_logistic(Xs, y, l2=l2, row_weight=rw))
        for rows in rows_by_tag.values():
            te = [r for r in rows if r["seed"] == held and r["arch"].startswith("H_")]
            if not te:
                continue
            Xt, _, _ = _standardise(_design(te, interactions), mu, sd)
            p = gbdt_predict(model, Xt) if kind == "gbdt" else predict(Xt, model)
            hand = np.array([r["hand_conf"] for r in te])
            k = int((hand >= 0.5).sum())
            order = np.argsort(-p)[:k]
            cap = te[0]["cap"]
            seen = set()
            dec = 0
            for i in order[:cap]:
                if te[i]["gold"]:
                    seen.add(te[i]["pid"])
                elif te[i].get("decoy"):
                    dec += 1
            # the objective is discovery under the real cap, less a charge for
            # every planted trap the ranking admits - decoy acceptance is a
            # headline metric, not a footnote
            total += len(seen) - decoy_penalty * dec / max(1, len(seen))
    return float(total)


def select_and_store(tags: Sequence[str] = ("", "_qf1"),
                     decoy_weights: Sequence[float] = (1.0,),
                     decoy_penalty: float = 0.0) -> Dict[str, object]:
    """Choose l2 and whether to use interactions by leave-one-seed-out found
    on the calibration seeds, then fit on all three and store.

    Decoy up-weighting and a decoy penalty in the objective are available
    but OFF by default: the grid over (1, 3, 10) with penalty 0.5 chose
    weight 10 on the calibration seeds and lost 0.06 found and 0.12 rare
    recall on the held-out seeds (quick_seldw_H.jsonl), i.e. the objective
    was fitting three seeds' worth of decoys."""
    rows_by_tag = {}
    for tag in tags:
        path = os.path.join(ART, f"hyp_features{tag}.jsonl")
        if os.path.exists(path):
            rows_by_tag[tag] = [json.loads(l) for l in open(path)]
    grid = []
    best, best_v = None, -1e9
    for l2 in (0.3, 1.0, 3.0):
        for inter in (False, True):
            for dw in decoy_weights:
                v = _loso_found(rows_by_tag, l2, inter, "logistic", decoy_weight=dw,
                                decoy_penalty=decoy_penalty)
                grid.append({"kind": "logistic", "l2": l2, "interactions": inter,
                             "decoy_weight": dw, "loso_objective": v})
                if v > best_v:
                    best_v, best = v, ("logistic", l2, inter, dw)
    for dw in decoy_weights[:1]:
        v = _loso_found(rows_by_tag, 1.0, False, "gbdt", decoy_weight=dw,
                        decoy_penalty=decoy_penalty)
        grid.append({"kind": "gbdt", "decoy_weight": dw, "loso_objective": v})
        if v > best_v:
            best_v, best = v, ("gbdt", 1.0, False, dw)
    r = fit_and_store(tags=tags, l2=best[1], interactions=best[2], kind=best[0],
                      decoy_weight=best[3])
    r["selection_grid"] = grid
    cal_path = os.path.join(ART, "calibration.json")
    cal = json.load(open(cal_path))
    cal["ranker"] = r
    with open(cal_path, "w") as fh:
        json.dump(cal, fh, indent=1)
    return r


def select_archs(archs: Sequence[str] = ("H_mycelic_full", "A2_chunked_ctx",
                                         "B4_central_triage", "Y_oracle_retrieval",
                                         "A_flat_rag", "B2_map_reduce",
                                         "G_hier_questions", "J_mycelic_verified",
                                         "H_mycelic_lean"),
                 scale: int = 10_000, seeds: Sequence[int] = CAL_SEEDS,
                 alloc_name: str = "back-loaded") -> Dict[str, object]:
    """Per architecture, on the CALIBRATION seeds only: found with the
    fitted ranker vs found with the hand-set logistic, paired on the same
    worlds.  An architecture adopts the ranker only if it is not worse.
    Hierarchy variants share the hierarchy's decision."""
    from .evalm import evaluate
    from .runner import apply_ranker, build_world, run_arch
    alloc = allocation(alloc_name)
    res: Dict[str, Dict[str, List[float]]] = {a: {"on": [], "off": []} for a in archs}
    for seed in seeds:
        w = build_world(scale, seed)
        for a in archs:
            for mode in ("off", "on"):
                apply_ranker(mode == "on")
                r = run_arch(a, w, alloc, seed)
                m = evaluate(w.corpus, w.gold, r)
                res[a][mode].append(m["found_anywhere_in_register"])
        w.clear_cache()
    apply_ranker(None)
    chosen = []
    table = {}
    for a in archs:
        on, off = float(np.mean(res[a]["on"])), float(np.mean(res[a]["off"]))
        table[a] = {"found_ranker": on, "found_hand": off, "adopt": on >= off}
        if on >= off:
            chosen.append(a)
    # every hierarchy variant shares the hierarchy's decision
    # (H_mycelic_prev is the v1 reference and keeps the hand-set ranker)
    hier = [k for k, v in __import__("research.mycelic.runner", fromlist=["ARCHS"]).ARCHS.items()
            if v.get("kind", "hier") == "hier" and k != "H_mycelic_prev"]
    if "H_mycelic_full" in chosen:
        chosen = sorted(set(chosen) | set(hier))
    cal_path = os.path.join(ART, "calibration.json")
    cal = json.load(open(cal_path))
    cal["ranker_archs"] = chosen
    cal["ranker_arch_table"] = table
    with open(cal_path, "w") as fh:
        json.dump(cal, fh, indent=1)
    return {"chosen": chosen, "table": table}


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
    cal_path = os.path.join(ART, "calibration.json")
    stored = (json.load(open(cal_path)).get("ranker") if os.path.exists(cal_path)
              else None) or {}
    inter = bool(stored.get("interactions", False))
    l2 = float(stored.get("l2", 1.0))
    Xtr = _design(tr, inter)
    ytr = np.array([1.0 if r["gold"] else 0.0 for r in tr])
    Xte = _design(te, inter)
    yte = np.array([1.0 if r["gold"] else 0.0 for r in te])
    Xtr_s, mu, sd = _standardise(Xtr)
    Xte_s, _, _ = _standardise(Xte, mu, sd)
    w = fit_logistic(Xtr_s, ytr, l2=l2)
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
           "weights": {f: float(x) for f, x in zip(
               ["bias"] + FEATURES + ([f"{a}*{b}" for a, b in INTERACTIONS]
                                      if inter else []), w)}}
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


def fit_and_store(tags: Sequence[str] = ("", "_qf1"),
                  train_archs: Optional[Sequence[str]] = None,
                  l2: float = 1.0, interactions: bool = True,
                  kind: str = "logistic", decoy_weight: float = 1.0) -> Dict[str, object]:
    """Fit the shared ranker on the CALIBRATION seeds only and write it into
    calibration.json, where runner.py picks it up for every architecture.

    The dump it trains on must have been produced with the ranker OFF (the
    hand_conf feature is the hand-set logistic), which `dump` guarantees by
    disabling it for the duration of the run.
    """
    tr = []
    for tag in tags:
        path = os.path.join(ART, f"hyp_features{tag}.jsonl")
        if not os.path.exists(path):
            continue
        rows = [json.loads(l) for l in open(path)]
        tr += [r for r in rows if r["seed"] in CAL_SEEDS
               and (train_archs is None or r["arch"] in train_archs)]
    X = _design(tr, interactions)
    y = np.array([1.0 if r["gold"] else 0.0 for r in tr])
    Xs, mu, sd = _standardise(X)
    rw = _row_weight(tr, decoy_weight)
    names = list(FEATURES) + ([f"{a}*{b}" for a, b in INTERACTIONS] if interactions else [])
    if kind == "gbdt":
        model = fit_gbdt(Xs, y, row_weight=rw)
        ranker = {"kind": "gbdt", "features": names, "trees": model["trees"],
                  "lr": model["lr"], "bias": 0.0, "weights": [0.0] * len(names),
                  "mu": [float(x) for x in mu], "sd": [float(x) for x in sd],
                  "l2": l2, "interactions": interactions,
              "n_train": len(tr), "seeds": list(CAL_SEEDS),
              "train_archs": list(train_archs) if train_archs else "all",
              "source_dumps": list(tags)}
    else:
        w = fit_logistic(Xs, y, l2=l2, row_weight=rw)
        ranker = {"kind": "logistic", "features": names, "bias": float(w[0]),
                  "weights": [float(x) for x in w[1:]],
                  "mu": [float(x) for x in mu], "sd": [float(x) for x in sd],
                  "l2": l2, "interactions": interactions, "decoy_weight": decoy_weight,
                  "n_train": len(tr), "seeds": list(CAL_SEEDS),
                  "train_archs": list(train_archs) if train_archs else "all",
                  "source_dumps": list(tags)}
    cal_path = os.path.join(ART, "calibration.json")
    cal = json.load(open(cal_path)) if os.path.exists(cal_path) else {}
    cal["ranker"] = ranker
    with open(cal_path, "w") as fh:
        json.dump(cal, fh, indent=1)
    return ranker


def _tags_arg(argv: Sequence[str], default: Sequence[str]) -> Tuple[str, ...]:
    """Dump tags from argv[2] as a comma-separated list ("_hybH,_v3C,_J"); "" names the untagged dump."""
    return tuple(argv[2].split(",")) if len(argv) > 2 else tuple(default)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    cmd = argv[1] if len(argv) > 1 else "fit"
    if cmd == "archs":
        r = select_archs()
        for a, v in r["table"].items():
            print(f"  {a:22s} hand={v['found_hand']:.3f} ranker={v['found_ranker']:.3f} adopt={v['adopt']}")
        print("ranker_archs =", r["chosen"])
        return 0
    if cmd == "select":
        r = select_and_store(tags=_tags_arg(argv, ("", "_qf1")))
        print("selection grid:", r["selection_grid"])
        print("stored ranker: l2", r["l2"], "interactions", r["interactions"],
              "n_train", r["n_train"])
        return 0
    if cmd == "store":
        r = fit_and_store(tags=_tags_arg(argv, ("",)))
        print("stored ranker fitted on", r["n_train"], "candidates; largest weights:",
              sorted(zip(r["features"], r["weights"]), key=lambda t: -abs(t[1]))[:6])
        return 0
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
