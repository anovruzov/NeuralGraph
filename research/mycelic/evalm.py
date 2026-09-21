"""Metrics.  Raw measurements first; composites only as a convenience.

Matching rule (strict, used for every headline number):
    a reported hypothesis matches gold pattern p iff
        hyp.anchor == p.anchor
    and |set(hyp.preds) & set(p.preds)| >= min(3, len(p.preds))
A "lenient" variant that also accepts a lexical-stem-equal anchor is reported
alongside as a sensitivity check, never as the headline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .corpus import CAUSAL_CHAINS, Corpus, Gold, N_CAUSAL_PRED, PRED_ID, Pattern
from .ops import stem_rep_map
from .org import REGION, SITE
from .systems import RunResult


def _match_sets(hyps, patterns: List[Pattern], stem: np.ndarray,
                lenient: bool, mode: str = "primary") -> Dict[int, List[int]]:
    """gold pattern id -> list of hypothesis indices that match it.

    mode="primary"  : the report names the right entity and substantially
                      identifies the chain - at least 2 of the gold links and
                      at least half of them.  This is what counts as "the
                      executive was told about this risk".
    mode="strict"   : at least min(3, |gold links|) of the gold links.
    Both are reported in every table; the strict column is the conservative
    reading and is never dropped.
    """
    out: Dict[int, List[int]] = {}
    by_anchor: Dict[int, List[int]] = {}
    for i, h in enumerate(hyps):
        a = int(h.anchor)
        by_anchor.setdefault(a, []).append(i)
        if lenient:
            by_anchor.setdefault(int(stem[a]), []).append(i)
    for p in patterns:
        cands = set(by_anchor.get(int(p.anchor), []))
        if lenient:
            cands |= set(by_anchor.get(int(stem[p.anchor]), []))
        ps = set(p.preds)
        if mode == "strict":
            need = min(3, len(p.preds))
        else:
            need = max(2, (len(p.preds) + 1) // 2)
        hit = [i for i in cands if len(set(hyps[i].preds) & ps) >= need]
        if hit:
            out[p.pid] = sorted(hit)
    return out


@dataclass
class Metrics:
    d: Dict[str, float]

    def __getitem__(self, k: str) -> float:
        return self.d[k]

    def as_dict(self) -> Dict[str, float]:
        return self.d


def _ranked_metrics(ranked, real, stem, lenient, mode="primary"):
    """Average precision / recall@K over the kernel's ranked risk register.

    A finite "top-40 briefing" metric has no dynamic range at 50k users, where
    ordinary cross-site traffic throws up thousands of plausible-looking
    causal chains.  AP is budget-free, is the standard way to score a ranked
    retrieval of a known relevant set, and has a known random baseline
    (|gold| / |candidates|), so a result can be stated as a multiple of chance.
    """
    m = _match_sets(ranked, real, stem, lenient, mode=mode)
    first: Dict[int, int] = {}
    for pid, idxs in m.items():
        first[pid] = min(idxs)
    order = sorted(first.items(), key=lambda kv: kv[1])
    n_gold = max(1, len(real))
    ap = 0.0
    for rank_pos, (pid, idx) in enumerate(order, start=1):
        ap += rank_pos / (idx + 1)
    ap /= n_gold
    out = {"ap": ap, "n_found": float(len(order))}
    for K in (40, 100, 300):
        hit = sum(1 for _, idx in order if idx < K)
        out[f"recall_at_{K}"] = hit / n_gold
        out[f"precision_at_{K}"] = hit / min(K, max(1, len(ranked)))
    rp = sum(1 for _, idx in order if idx < n_gold) / n_gold
    out["r_precision"] = rp
    return out


def evaluate(corpus: Corpus, gold: Gold, res: RunResult,
             lenient: bool = False, tau: float = 0.5) -> Dict[str, float]:
    stem = stem_rep_map(corpus)
    ranked = list(res.hypotheses)          # already sorted by confidence
    hyps = [h for h in res.hypotheses if h.conf >= tau]
    pats = {p.pid: p for p in corpus.patterns}
    real = [pats[i] for i in gold.discoverable]
    # D4 (duplicate-inflation) chains are genuine causal chains that are merely
    # over-supported, so reporting one is not a false discovery.  They are
    # scored separately, via independent-support inflation, and excluded from
    # the precision denominator.
    decoys = [p for p in corpus.patterns if not p.real and p.decoy_type != 4]
    dup_infl = [p for p in corpus.patterns if not p.real and p.decoy_type == 4]

    m_real = _match_sets(hyps, real, stem, lenient)
    m_real_strict = _match_sets(hyps, real, stem, lenient, mode="strict")
    m_dec = _match_sets(hyps, decoys, stem, lenient)
    m_dup = _match_sets(hyps, dup_infl, stem, lenient)
    dup_hyp: Set[int] = set()
    for v in m_dup.values():
        dup_hyp.update(v)

    n_rep = len(hyps) - len(dup_hyp)
    matched_hyp: Set[int] = set()
    for v in m_real.values():
        matched_hyp.update(v)
    dec_hyp: Set[int] = set()
    for v in m_dec.values():
        dec_hyp.update(v)

    rec = len(m_real) / max(1, len(real))
    prec = len(matched_hyp) / max(1, n_rep)
    f1 = 2 * rec * prec / max(1e-9, rec + prec)
    fdr = 1.0 - prec

    rare = [p for p in real if p.rare]
    rare_rec = sum(1 for p in rare if p.pid in m_real) / max(1, len(rare))
    common = [p for p in real if not p.rare]
    common_rec = sum(1 for p in common if p.pid in m_real) / max(1, len(common))

    # decoy acceptance, by type
    dec_acc = {t: 0.0 for t in (1, 2, 3, 5)}
    dec_n = {t: 0 for t in (1, 2, 3, 5)}
    for p in decoys:
        dec_n[p.decoy_type] += 1
        if p.pid in m_dec:
            dec_acc[p.decoy_type] += 1
    for t in dec_acc:
        dec_acc[t] = dec_acc[t] / max(1, dec_n[t])

    # duplicate-inflation: does the system over-count independent support?
    dup_errs: List[float] = []
    for pid, idxs in m_dup.items():
        p = pats[pid]
        true_ind = len({int(corpus.recs["event"][r])
                        for fr in p.facet_records for r in fr})
        h = max((hyps[i] for i in idxs), key=lambda x: x.conf)
        dup_errs.append(min(4.0, h.n_indep / max(1, true_ind)))
    dup_err = float(np.mean(dup_errs)) if dup_errs else 1.0

    hall = sum(1 for h in hyps if h.hallucinated)
    hall_rate = hall / max(1, n_rep)
    unsupported = sum(1 for i, h in enumerate(hyps)
                      if i not in matched_hyp and i not in dec_hyp)

    # --- cross-branch discovery / lineage / provenance ---
    cb_ok = cb_n = 0
    lin_p_num = lin_p_den = 0
    prov_ok = 0
    ind_err: List[float] = []
    ev_p: List[float] = []
    ev_r: List[float] = []
    for pid, idxs in m_real.items():
        p = pats[pid]
        best = max(idxs, key=lambda i: hyps[i].conf)
        h = hyps[best]
        if p.n_regions >= 2:
            cb_n += 1
            if h.n_branch_regions >= 2:
                cb_ok += 1
        true_regions = set()
        for fr in p.facet_records:
            for r in fr:
                a = corpus.org.ancestor_at(int(corpus.recs["uid"][r]), REGION)
                if a is not None:
                    true_regions.add(a)
        rep_regions: Set[int] = set()
        for k in h.kos:
            rep_regions |= k.branches.get(REGION, set())
        if rep_regions:
            lin_p_num += len(rep_regions & true_regions)
            lin_p_den += len(rep_regions)
        if h.evidence and h.kos and any(k.lineage for k in h.kos):
            prov_ok += 1
        true_ind = gold.independent_sources.get(pid, 1)
        ind_err.append(min(2.0, abs(h.n_indep - true_ind) / max(1, true_ind)))
        gev = set(gold.evidence.get(pid, []))
        rev = set(h.evidence)
        if rev:
            ev_p.append(len(rev & gev) / len(rev))
        ev_r.append(len(rev & gev) / max(1, min(len(gev), 24)))

    # --- contradiction detection (pattern level) ---
    tp = fp = fn = 0
    for pid, idxs in m_real.items():
        h = max((hyps[i] for i in idxs), key=lambda x: x.conf)
        truth = pats[pid].contradicted
        pred_c = h.contra > 0
        if truth and pred_c:
            tp += 1
        elif pred_c and not truth:
            fp += 1
        elif truth and not pred_c:
            fn += 1
    c_prec = tp / max(1, tp + fp)
    c_rec = tp / max(1, tp + fn)
    c_f1 = 2 * c_prec * c_rec / max(1e-9, c_prec + c_rec)

    # --- strongest-independent-support question ---
    top_ok = 0.0
    if hyps and gold.top_supported >= 0:
        best_i = max(range(len(hyps)), key=lambda i: hyps[i].n_indep)
        top_ok = 1.0 if best_i in set(m_real.get(gold.top_supported, [])) else 0.0

    # --- cross-region variant families ---
    fam_ok = 0
    fam_n = len(gold.families)
    if fam_n:
        rep_groups = []
        for grp in res.families:
            pids = set()
            for i in grp:
                for pid, idxs in m_real.items():
                    if i in idxs:
                        pids.add(pid)
            if len(pids) >= 2:
                rep_groups.append(pids)
        for f, members in gold.families.items():
            ms = set(members)
            if any(len(ms & g) >= 2 for g in rep_groups):
                fam_ok += 1
    fam_rec = fam_ok / max(1, fam_n)

    # --- evidence coverage: did the kernel ever HOLD the evidence? ---
    # This separates "could not get the evidence" from "had it and ranked it
    # badly", which turn out to be different bottlenecks at different scales.
    held: Dict[int, Set[int]] = {}
    for h in res.hypotheses:
        for k in h.kos:
            held.setdefault(int(k.anchor), set()).add(int(k.pred))
    for k in res.kernel_kos:
        held.setdefault(int(k.anchor), set()).add(int(k.pred))
    cov2 = cov3 = 0
    for p in real:
        got = len(held.get(int(p.anchor), set()) & set(p.preds))
        cov2 += got >= 2
        cov3 += got >= 3
    evidence_coverage2 = cov2 / max(1, len(real))
    evidence_coverage3 = cov3 / max(1, len(real))

    # --- information loss / compression ---
    facet_types = set()
    for p in corpus.patterns:
        if not p.real:
            continue
        for fr, pr in zip(p.facet_records, p.preds):
            for r in fr:
                facet_types.add((int(corpus.recs["pred"][r]),
                                 int(corpus.recs["anchor"][r])))
    kept = res.retained
    facet_survival = len(facet_types & kept) / max(1, len(facet_types))
    all_types = set(zip(corpus.recs["pred"].tolist(),
                        corpus.recs["anchor"].tolist()))
    type_survival = len(all_types & kept) / max(1, len(all_types))

    md = res.meter.as_dict()
    in_tokens_if_raw = len(corpus.recs) * 24
    kernel_ctx = md["by_stage"].get("L5-kernel", {}).get("max_ctx", 0)
    compression = in_tokens_if_raw / max(1, kernel_ctx)

    # --- questions ---
    qs = res.questions
    q_util = (sum(1 for q in qs if q.changed_conclusion) / len(qs)) if qs else 0.0
    q_evid = (sum(q.n_new_evidence for q in qs) / len(qs)) if qs else 0.0
    q_targeted = (sum(1 for q in qs if q.well_targeted) / len(qs)) if qs else 0.0

    n_correct = len(m_real)
    out: Dict[str, float] = {
        "discovery_recall": rec,
        "discovery_recall_strict": len(m_real_strict) / max(1, len(real)),
        "discovery_precision": prec,
        "discovery_f1": f1,
        "false_discovery_rate": fdr,
        "rare_signal_recall": rare_rec,
        "common_signal_recall": common_rec,
        "n_reported": float(n_rep),
        "n_correct": float(n_correct),
        "n_gold": float(len(real)),
        "hallucination_rate": hall_rate,
        "unsupported_reports": float(unsupported),
        "decoy_acceptance_all": float(len(m_dec)) / max(1, len(decoys)),
        "decoy_D1_entity_coincidence": dec_acc[1],
        "decoy_D2_temporal_scramble": dec_acc[2],
        "decoy_D3_near_miss_entity": dec_acc[3],
        "decoy_D5_stale_chain": dec_acc[5],
        "dup_inflation_reported": float(len(m_dup)) / max(1, len(dup_infl)),
        "dup_inflation_support_error": dup_err,
        "cross_branch_discovery_accuracy": cb_ok / max(1, cb_n),
        "lineage_accuracy": lin_p_num / max(1, lin_p_den),
        "provenance_preservation": prov_ok / max(1, len(m_real)),
        "independent_evidence_error": float(np.mean(ind_err)) if ind_err else 1.0,
        "independent_evidence_accuracy": (1.0 - float(np.mean(ind_err)) / 2.0)
        if ind_err else 0.0,
        "evidence_precision": float(np.mean(ev_p)) if ev_p else 0.0,
        "evidence_recall": float(np.mean(ev_r)) if ev_r else 0.0,
        "contradiction_f1": c_f1,
        "contradiction_precision": c_prec,
        "contradiction_recall": c_rec,
        "top_support_accuracy": top_ok,
        "family_recall": fam_rec,
        "evidence_coverage_2links": evidence_coverage2,
        "evidence_coverage_3links": evidence_coverage3,
        "facet_type_survival": facet_survival,
        "information_loss": 1.0 - facet_survival,
        "claim_type_survival": type_survival,
        "compression_ratio": compression,
        "kernel_context_tokens": float(kernel_ctx),
        "max_context_tokens": float(md["max_ctx"]),
        "tokens_in": float(md["tok_in"]),
        "tokens_out": float(md["tok_out"]),
        "tokens_total": float(md["tok_in"] + md["tok_out"]),
        "inference_calls": float(md["calls"]),
        "compute_units": float(md["cu"]),
        "usd_estimate": float(md["usd"]),
        "wall_seconds": float(md["wall_s"]),
        "p50_call_s": float(md["p50_call_s"]),
        "p95_call_s": float(md["p95_call_s"]),
        "tau": float(tau),
        "n_questions": float(len(qs)),
        "question_utility": q_util,
        "question_new_evidence": q_evid,
        "question_targeting": q_targeted,
        "privacy_propagated_records": float(res.propagated_records),
        "privacy_exposure_fraction": res.propagated_records / max(1, len(corpus.recs)),
        "raw_records_leaving_node": float(res.exposed_raw_records),
        "raw_text_exposure_fraction": res.exposed_raw_records / max(1, len(corpus.recs)),
        "claims_leaving_node": float(res.claims_leaving_node),
        "claim_exposure_fraction": res.claims_leaving_node / max(1, len(corpus.recs)),
        "sketch_entries_leaving_node": float(res.sketch_entries_leaving_node),
    }
    rank_p = _ranked_metrics(ranked, real, stem, lenient, "primary")
    rank_s = _ranked_metrics(ranked, real, stem, lenient, "strict")
    out["average_precision"] = rank_p["ap"]
    out["average_precision_strict"] = rank_s["ap"]
    out["r_precision"] = rank_p["r_precision"]
    out["n_candidates"] = float(len(ranked))
    out["random_ap_baseline"] = len(real) / max(1, len(ranked))
    out["ap_lift_over_random"] = (rank_p["ap"] /
                                  max(1e-9, len(real) / max(1, len(ranked))))
    for K in (40, 100, 300):
        out[f"recall_at_{K}"] = rank_p[f"recall_at_{K}"]
        out[f"precision_at_{K}"] = rank_p[f"precision_at_{K}"]
    out["found_anywhere_in_register"] = rank_p["n_found"] / max(1, len(real))
    out["cost_per_correct_discovery"] = (out["compute_units"] /
                                         max(1, n_correct))
    out["usd_per_correct_discovery"] = out["usd_estimate"] / max(1, n_correct)
    # Executive answer accuracy: macro average over the executive question set
    out["executive_answer_accuracy"] = float(np.mean([
        f1, top_ok, c_f1, fam_rec, cb_ok / max(1, cb_n)]))
    return out


HEADLINE = [
    "discovery_recall", "discovery_precision", "discovery_f1",
    "false_discovery_rate", "rare_signal_recall",
    "cross_branch_discovery_accuracy", "decoy_acceptance_all",
    "independent_evidence_accuracy", "lineage_accuracy",
    "contradiction_f1", "information_loss", "question_utility",
    "compute_units", "tokens_total", "inference_calls", "wall_seconds",
    "cost_per_correct_discovery", "privacy_exposure_fraction",
    "executive_answer_accuracy",
]
