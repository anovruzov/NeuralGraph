"""Evaluation workload: labelled questions and the scoring of one condition.

Category labels are assigned once, stored in arrays, and never re-derived, so a
question's category cannot drift between systems or conditions.

Categories
  single_hop            one claim
  multi_hop             conjunction of 2-3 claims (correct iff every hop is)
  temporal              a claim whose value was revised: the post-revision value
                        is the only correct answer
  revision_sensitive    the hard subset of temporal: claims where the majority of
                        the world's evidence is still the pre-revision observation
  reconstruction        claims whose original evidence neighbourhood was destroyed
                        by the failure and must be rebuilt from elsewhere
  contradiction         binary: does genuine disagreement exist in this claim's
                        evidence? (precision / recall / F1)
  evidence_verification binary: does this claim have >= 2 *independent* sources?
                        NOTE: replica-counting systems cannot represent this
                        distinction at all, so this category is reported
                        separately and excluded from headline accuracy.
  strategic             ranking: "which claims have the weakest independent
                        support?" -- Spearman rho and precision@10%

Headline accuracy A is the macro-average over the five answer-producing
categories (single_hop, multi_hop, temporal, revision_sensitive, reconstruction).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .evaluate import ClaimResult, Condition
from .world import World

ANSWER_CATEGORIES = ["single_hop", "multi_hop", "temporal", "revision_sensitive",
                     "reconstruction"]
ALL_CATEGORIES = ANSWER_CATEGORIES + ["contradiction", "evidence_verification", "strategic"]


@dataclass
class Workload:
    q_cat: np.ndarray        # int8[Q] category id
    q_hops: np.ndarray       # int32[Q,3] claim ids, -1 padded
    q_nhops: np.ndarray      # int8[Q]
    categories: list[str]
    querier: np.ndarray      # int32[M] node that issues the query for each claim
    n_questions: int

    def category_id(self, name: str) -> int:
        return self.categories.index(name)


def build_workload(w: World, rng: np.random.Generator, cfg: dict) -> Workload:
    m = w.n_claims
    cats = ALL_CATEGORIES
    q_cat, q_hops, q_nhops = [], [], []

    def add(cat: str, hops: np.ndarray):
        n = hops.shape[0]
        if n == 0:
            return
        padded = np.full((n, 3), -1, np.int32)
        padded[:, :hops.shape[1]] = hops
        q_cat.append(np.full(n, cats.index(cat), np.int8))
        q_hops.append(padded)
        q_nhops.append(np.full(n, hops.shape[1], np.int8))

    single_mult = int(cfg.get("single_hop_multiplier", 1))
    base = np.tile(np.arange(m, dtype=np.int32), single_mult)
    add("single_hop", base.reshape(-1, 1))

    n2 = int(round(m * float(cfg.get("multi_hop2_fraction", 0.5))))
    n3 = int(round(m * float(cfg.get("multi_hop3_fraction", 0.5))))
    if n2:
        add("multi_hop", rng.integers(0, m, (n2, 2)).astype(np.int32))
    if n3:
        add("multi_hop", rng.integers(0, m, (n3, 3)).astype(np.int32))

    revised = np.flatnonzero(w.claim_revised).astype(np.int32)
    add("temporal", revised.reshape(-1, 1))

    # hard subset: the majority of the world's evidence for the claim is stale
    stale_frac = np.bincount(w.item_claim, weights=w.item_stale.astype(np.float64),
                             minlength=m) / np.maximum(w.claim_n_items, 1)
    hard = np.flatnonzero(w.claim_revised & (stale_frac > 0.5)).astype(np.int32)
    add("revision_sensitive", hard.reshape(-1, 1))

    # reconstruction targets are condition-dependent; the question set is every
    # claim and scoring restricts to those actually destroyed by the failure
    add("reconstruction", np.arange(m, dtype=np.int32).reshape(-1, 1))
    add("contradiction", np.arange(m, dtype=np.int32).reshape(-1, 1))
    add("evidence_verification", np.arange(m, dtype=np.int32).reshape(-1, 1))
    add("strategic", np.arange(m, dtype=np.int32).reshape(-1, 1))

    q_cat = np.concatenate(q_cat)
    q_hops = np.concatenate(q_hops)
    q_nhops = np.concatenate(q_nhops)

    # querier: with probability p the asking agent is one that participates in the
    # claim (holds one of its evidence items), otherwise an arbitrary agent
    p_local = float(cfg.get("querier_local_prob", 0.5))
    local = rng.random(m) < p_local
    off = (rng.random(m) * w.claim_n_items.astype(np.int64)).astype(np.int64)
    home_of = w.item_home[(w.claim_item_ptr[:-1] + off).astype(np.int64)]
    querier = np.where(local, home_of, rng.integers(0, w.n_nodes, m)).astype(np.int32)

    return Workload(q_cat=q_cat, q_hops=q_hops, q_nhops=q_nhops, categories=cats,
                    querier=querier, n_questions=int(q_cat.size))


# -------------------------------------------------------------------------
# ground truth that depends on the failure condition
# -------------------------------------------------------------------------

def condition_truth(w: World, cond: Condition) -> dict:
    """Per-claim ground truth that varies with the injected failure."""
    codes = w.item_stale.astype(np.int64)
    if cond.partition_updated is not None:
        codes = np.where(cond.partition_updated[w.item_claim] &
                         ~cond.node_saw_update[w.item_home], 1, codes)
    if cond.root_corrupt is not None:
        codes = np.where(cond.root_corrupt[w.item_root], 2, codes)
    if cond.node_corrupt is not None:
        nc = cond.node_corrupt[w.item_home]
        var = (cond.node_false_variant[w.item_home].astype(np.int64)
               if cond.node_false_variant is not None else 0)
        codes = np.where(nc, 2 + var, codes)
    v = 2 + max(1, cond.n_false_variants)
    present = np.zeros(w.n_claims * v, bool)
    present[w.item_claim.astype(np.int64) * v + codes] = True
    n_distinct = present.reshape(w.n_claims, v).sum(axis=1)

    alive_items = np.bincount(w.item_claim, weights=cond.alive[w.item_home].astype(np.float64),
                              minlength=w.n_claims)
    frac_alive = alive_items / np.maximum(w.claim_n_items, 1)
    return {
        "contradiction_label": n_distinct > 1,
        "multi_source_label": w.claim_n_roots >= 2,
        "reconstruction_mask": frac_alive < float(0.2),
        "frac_home_evidence_alive": frac_alive,
        "partition_updated": np.zeros(w.n_claims, bool),
    }


def _prf(pred: np.ndarray, label: np.ndarray) -> tuple[float, float, float]:
    tp = float(np.sum(pred & label))
    fp = float(np.sum(pred & ~label))
    fn = float(np.sum(~pred & label))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def ece(conf: np.ndarray, correct: np.ndarray, n_bins: int = 10) -> float:
    if conf.size == 0:
        return float("nan")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    b = np.clip(np.digitize(conf, edges[1:-1]), 0, n_bins - 1)
    tot = 0.0
    for i in range(n_bins):
        mask = b == i
        if not mask.any():
            continue
        tot += mask.mean() * abs(correct[mask].mean() - conf[mask].mean())
    return float(tot)


def auroc(conf: np.ndarray, correct: np.ndarray) -> float:
    """Discrimination of the confidence signal, independent of its scale."""
    pos = int(correct.sum()); neg = int((~correct).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    ranks = np.argsort(np.argsort(conf)).astype(np.float64) + 1.0
    return float((ranks[correct].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def ece_recalibrated(conf: np.ndarray, correct: np.ndarray, n_bins: int = 10) -> float:
    """ECE after histogram-binning recalibration fitted on a held-out half.

    Raw ECE partly reflects the arbitrary scale of a confidence formula.  This
    variant asks the fairer question: after each system is allowed a simple
    post-hoc recalibration, how well does its confidence still track correctness?
    """
    n = conf.size
    if n < 40:
        return float("nan")
    fit = np.zeros(n, bool); fit[::2] = True
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    b = np.clip(np.digitize(conf, edges[1:-1]), 0, n_bins - 1)
    mapped = np.empty(n)
    for i in range(n_bins):
        m_fit = fit & (b == i)
        m_ev = (~fit) & (b == i)
        if not m_ev.any():
            continue
        mapped[m_ev] = correct[m_fit].mean() if m_fit.any() else conf[m_ev].mean()
    ev = ~fit
    return ece(mapped[ev], correct[ev], n_bins)


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 3:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(np.float64)
    rb = np.argsort(np.argsort(b)).astype(np.float64)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra * ra).sum() * (rb * rb).sum())
    return float((ra * rb).sum() / d) if d > 0 else float("nan")


def score(w: World, wl: Workload, res: ClaimResult, cond: Condition,
          truth: dict, agg_is_lineage: bool) -> dict:
    """Score every category for one (system, condition) pair."""
    out: dict[str, float] = {}
    correct_c = res.correct
    cats = wl.categories

    for name in ANSWER_CATEGORIES:
        cid = cats.index(name)
        sel = wl.q_cat == cid
        hops = wl.q_hops[sel]
        if name == "reconstruction":
            keep = truth["reconstruction_mask"][hops[:, 0]]
            hops = hops[keep]
        if hops.shape[0] == 0:
            out[f"acc_{name}"] = float("nan")
            out[f"n_{name}"] = 0
            continue
        ok = np.ones(hops.shape[0], bool)
        for h in range(hops.shape[1]):
            col = hops[:, h]
            valid = col >= 0
            ok &= np.where(valid, correct_c[np.maximum(col, 0)], True)
        out[f"acc_{name}"] = float(ok.mean())
        out[f"n_{name}"] = int(hops.shape[0])

    # accuracy conditional on having retrieved anything at all: separates
    # availability from correctness
    out["acc_given_answered"] = (float(res.correct[res.answered].mean())
                                 if res.answered.any() else float("nan"))

    # contradiction detection: identical prediction rule for every system --
    # "did the evidence I retrieved disagree?"
    pred = res.n_distinct_values > 1
    p, r, f = _prf(pred, truth["contradiction_label"])
    out["contradiction_precision"], out["contradiction_recall"], out["contradiction_f1"] = p, r, f
    out["n_contradiction"] = int(w.n_claims)

    # evidence verification: lineage systems can count roots, count systems can
    # only count replicas -- that conflation is the phenomenon under test
    pred_ms = (res.probed_roots >= 2) if agg_is_lineage else (res.support_total >= 2)
    out["evidence_verification_acc"] = float((pred_ms == truth["multi_source_label"]).mean())
    p, r, f = _prf(pred_ms, truth["multi_source_label"])
    out["evidence_verification_f1"] = f
    out["n_evidence_verification"] = int(w.n_claims)

    # strategic ranking: "which claims have the weakest independent support?"
    est = res.probed_roots.astype(np.float64) if agg_is_lineage else res.support_total.astype(np.float64)
    truth_support = w.claim_n_roots.astype(np.float64)
    out["strategic_spearman"] = spearman(est, truth_support)
    kq = max(1, int(0.10 * w.n_claims))
    weakest_true = set(np.argsort(truth_support + np.arange(w.n_claims) * 1e-12)[:kq].tolist())
    weakest_pred = np.argsort(est + np.arange(w.n_claims) * 1e-12)[:kq]
    out["strategic_p_at_10pct"] = float(np.mean([int(i) in weakest_true for i in weakest_pred]))

    # contradiction *resolution*: when the world's evidence for a claim genuinely
    # disagrees, does the system still land on the truth?  A system that resolves
    # contradictions by re-verification lowers its own detection recall while
    # raising this, so both are reported.
    cmask = truth["contradiction_label"]
    out["contradiction_resolved_acc"] = (float(res.correct[cmask].mean())
                                         if cmask.any() else float("nan"))
    out["n_contradictory_claims"] = int(cmask.sum())

    # partition reconciliation: of the claims updated inside one component while
    # the network was split, how many carry the post-update value afterwards?
    pu = truth.get("partition_updated")
    if pu is not None and pu.any():
        out["acc_partition_updated"] = float(res.correct[pu].mean())
        out["conflict_visible_rate"] = float((res.n_distinct_values[pu] > 1).mean())
        out["n_partition_updated"] = int(pu.sum())
    else:
        out["acc_partition_updated"] = float("nan")
        out["conflict_visible_rate"] = float("nan")
        out["n_partition_updated"] = 0

    # subset breakdowns: the section-7 correlated-vs-independent contrast and
    # the "no independent evidence exists" regime
    for label, mask in (("class_correlated", w.claim_class == 1),
                        ("class_independent", w.claim_class == 2),
                        ("single_root", w.claim_n_roots == 1),
                        ("multi_root", w.claim_n_roots >= 3)):
        if mask.any():
            out[f"acc_{label}"] = float(res.correct[mask].mean())
            out[f"avail_{label}"] = float(res.answered[mask].mean())
            out[f"iss_{label}"] = float(res.iss[mask].mean())
            am = mask & res.answered
            out[f"ece_{label}"] = ece(res.confidence[am], res.correct[am])
            out[f"conf_{label}"] = float(res.confidence[mask].mean())
            out[f"n_{label}"] = int(mask.sum())

    # headline metrics
    accs = [out[f"acc_{c}"] for c in ANSWER_CATEGORIES if out[f"n_{c}"] > 0]
    out["accuracy_macro"] = float(np.nanmean(accs)) if accs else float("nan")
    out["knowledge_survival"] = float(res.correct.mean())
    out["availability"] = float(res.answered.mean())
    out["iss"] = float(res.iss.mean())
    out["ece"] = ece(res.confidence[res.answered], res.correct[res.answered])
    out["ece_recalibrated"] = ece_recalibrated(res.confidence[res.answered],
                                               res.correct[res.answered])
    out["conf_auroc"] = auroc(res.confidence[res.answered], res.correct[res.answered])
    out["mean_confidence"] = float(res.confidence.mean())
    out["query_messages"] = float(res.attempts.sum())
    out["mean_probe_rounds"] = float(res.rounds.mean())
    return out
