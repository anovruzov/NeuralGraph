"""Evaluator-side scoring of live (and simulated) extraction.

This is the ONLY module of research/mycelic/live that reads ground truth: the
true record tuple (pred, anchor, polarity, aux, event) and the gold patterns'
evidence records.  It runs after the agents have answered and nothing it
computes is fed back into a system decision (it is SCORING in
leakage_allowlist.json).

It scores any ``ExtractResult`` - a live model's canonicalised claims or the
simulated operator's output on the same notes - with the same definitions,
so the two can be put side by side with the simulator's nominal tier
parameters (the "sim-vs-live operator gap" table).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from ..corpus import CAUSAL_CHAINS, N_CAUSAL_PRED, PRED_ID, Corpus
from ..models import Tier
from ..ops import NEIGHBOUR_TAB, ExtractResult


def _chain_of(p: int) -> set:
    out = set()
    for ci, ch in enumerate(CAUSAL_CHAINS):
        if any(PRED_ID[x] == p for x in ch):
            out.add(ci)
    return out


_CHAINS = {p: _chain_of(p) for p in range(N_CAUSAL_PRED)}


def _facet_rids(corpus: Corpus, gold) -> Dict[str, np.ndarray]:
    ev = gold.evidence
    rare = set(gold.rare)
    allr = [r for pid in gold.discoverable for r in ev.get(pid, [])]
    rr = [r for pid in gold.discoverable if pid in rare for r in ev.get(pid, [])]
    return {"all": np.array(sorted(set(allr)), dtype=np.int64),
            "rare": np.array(sorted(set(rr)), dtype=np.int64)}


def extraction_quality(corpus: Corpus, gold, ex: ExtractResult,
                       notes: np.ndarray) -> Dict[str, float]:
    """Per-note scores of claims ``ex`` against the true tuple of each note
    the operator read (``notes``: record ids).  Definitions:

    claims_per_note   claims / notes
    emission_rate     notes with >= 1 claim / notes   (sim: extract_recall)
    precision         claims whose (pred, anchor, polarity) is the note's
                      true tuple / claims
    recall            notes with a correct claim / notes
    recall_causal     the same over notes whose true predicate is causal
    facet_recall      the same over the gold facet records of discoverable
                      patterns among ``notes``; *_rare over rare patterns;
                      facet_recall_pa ignores polarity
    pred_error        claims with a wrong predicate / claims, split into
                      chain neighbour / same chain / other causal /
                      causal<->routine; pred_error_causal_given_entity_ok:
                      the same over claims on causal notes that name the
                      right entity (sim: pred_confusion, which applies to
                      causal predicates only)
    entity_fidelity   claims naming the true subject / claims; slips split
                      into the note's second mention / same 5-letter stem /
                      other; entity_fidelity_given_pred_ok: over claims with
                      the right predicate (sim: entity_fidelity)
    polarity_acc      claims with the true polarity / claims;
                      neg_recall: negated notes whose claims say `not`;
                      neg_false: positive notes whose claims say `not`
    spurious_per_note claims with BOTH predicate and entity wrong / notes
                      (sim: 1 - extract_precision)
    """
    recs = corpus.recs
    notes = np.asarray(notes, dtype=np.int64)
    in_set = np.isin(ex.rid, notes)
    rid = ex.rid[in_set]
    pred = ex.pred[in_set].astype(np.int64)
    anc = ex.anchor[in_set].astype(np.int64)
    pol = ex.polarity[in_set].astype(np.int64)
    tp = recs["pred"][rid].astype(np.int64)
    ta = recs["anchor"][rid].astype(np.int64)
    tpol = recs["polarity"][rid].astype(np.int64)
    taux = recs["aux"][rid].astype(np.int64)
    n_notes = max(1, len(notes))
    n_cl = len(rid)
    ok_p = pred == tp
    ok_a = anc == ta
    ok_pol = pol == tpol
    ok = ok_p & ok_a & ok_pol
    correct_notes = set(rid[ok].tolist())
    emitted = set(rid.tolist())
    out: Dict[str, float] = {
        "notes": int(len(notes)), "claims": int(n_cl),
        "claims_per_note": n_cl / n_notes,
        "emission_rate": len(emitted) / n_notes,
        "precision": float(ok.mean()) if n_cl else 0.0,
        "recall": len(correct_notes) / n_notes,
        "pred_error": float((~ok_p).mean()) if n_cl else 0.0,
        "entity_fidelity": float(ok_a.mean()) if n_cl else 0.0,
        "polarity_acc": float(ok_pol.mean()) if n_cl else 0.0,
        "spurious_per_note": float((~ok_p & ~ok_a).sum()) / n_notes,
        # conditional rates: separate the three error modes the simulated
        # operator draws independently (spurious claims get BOTH wrong)
        "entity_fidelity_given_pred_ok": float(ok_a[ok_p].mean()) if ok_p.any() else 0.0,
        "pred_error_given_entity_ok": float((~ok_p[ok_a]).mean()) if ok_a.any() else 0.0,
    }
    causal_cl = (tp < N_CAUSAL_PRED) & ok_a
    out["pred_error_causal_given_entity_ok"] = (float((~ok_p[causal_cl]).mean())
                                                if causal_cl.any() else 0.0)
    causal_notes = notes[recs["pred"][notes] < N_CAUSAL_PRED]
    out["recall_causal"] = (len(correct_notes & set(causal_notes.tolist()))
                            / max(1, len(causal_notes)))
    # predicate error taxonomy
    wp = ~ok_p
    if wp.any():
        nb = NEIGHBOUR_TAB[tp[wp]]
        is_nb = (nb == pred[wp][:, None]).any(axis=1)
        both_causal = (tp[wp] < N_CAUSAL_PRED) & (pred[wp] < N_CAUSAL_PRED)
        same_chain = np.array([bool(_CHAINS.get(int(a), set()) & _CHAINS.get(int(b), set()))
                               for a, b in zip(tp[wp], pred[wp])], dtype=bool)
        k = int(wp.sum())
        out["pred_err_neighbour"] = float(is_nb.sum()) / k
        out["pred_err_same_chain_other"] = float((same_chain & ~is_nb).sum()) / k
        out["pred_err_other_causal"] = float((both_causal & ~same_chain).sum()) / k
        out["pred_err_causal_routine"] = float((~both_causal).sum()) / k
    # entity slip taxonomy
    wa = ~ok_a
    if wa.any():
        ents = corpus.entities
        to_aux = anc[wa] == taux[wa]
        stem = np.array([ents[int(a)][:5] == ents[int(b)][:5] and not x
                         for a, b, x in zip(anc[wa], ta[wa], to_aux)], dtype=bool)
        k = int(wa.sum())
        out["entity_slip_to_second_mention"] = float(to_aux.sum()) / k
        out["entity_slip_same_stem"] = float(stem.sum()) / k
        out["entity_slip_other"] = float((~to_aux & ~stem).sum()) / k
    neg_notes = notes[recs["polarity"][notes] < 0]
    if n_cl:
        on_neg = tpol < 0
        out["neg_recall"] = float((pol[on_neg] < 0).mean()) if on_neg.any() else float("nan")
        out["neg_false"] = float((pol[~on_neg] < 0).mean()) if (~on_neg).any() else 0.0
    out["negated_notes"] = int(len(neg_notes))
    # gold facet records
    fr = _facet_rids(corpus, gold)
    for name, f in fr.items():
        f = f[np.isin(f, notes)]
        sfx = "" if name == "all" else "_rare"
        out[f"facet_notes{sfx}"] = int(len(f))
        if len(f):
            fs = set(f.tolist())
            out[f"facet_recall{sfx}"] = len(correct_notes & fs) / len(fs)
            pa = set(rid[ok_p & ok_a].tolist())
            out[f"facet_recall_pa{sfx}"] = len(pa & fs) / len(fs)
            out[f"facet_emission{sfx}"] = len(emitted & fs) / len(fs)
    return out


def signature_accuracy(corpus: Corpus, ex: ExtractResult,
                       drop_invented: bool = False) -> Dict[str, float]:
    """Echo-signature quality against the true event ids, over one claim per
    note (the first).  Pairwise: precision = pairs sharing a signature that
    share an event / pairs sharing a signature; recall = pairs sharing an
    event that share a signature.  ``drop_invented``: ignore the simulated
    operator's invented claims (its ``spurious`` flag; their signatures are
    unique by construction and are not an echo-detection decision)."""
    keep = ~ex.spurious if drop_invented else np.ones(len(ex), dtype=bool)
    rid = ex.rid[keep]
    sig = ex.sig[keep]
    _, first = np.unique(rid, return_index=True)
    rid = rid[first]
    sig = sig[first]
    ev = corpus.recs["event"][rid].astype(np.int64)
    return _pairwise(ev, sig)


def _pairwise(ev: np.ndarray, sig: np.ndarray) -> Dict[str, float]:
    def c2(counts: np.ndarray) -> int:
        counts = counts.astype(np.int64)
        return int((counts * (counts - 1) // 2).sum())
    _, ce = np.unique(ev, return_counts=True)
    _, cs = np.unique(sig, return_counts=True)
    pair = np.stack([ev, sig], axis=1)
    _, cb = np.unique(pair, axis=0, return_counts=True)
    same_ev, same_sig, both = c2(ce), c2(cs), c2(cb)
    # event-level: echo groups (events with >= 2 notes) kept whole
    grp: Dict[int, set] = {}
    for e, s in zip(ev.tolist(), sig.tolist()):
        grp.setdefault(e, set()).add(s)
    multi = [e for e, n in zip(*np.unique(ev, return_counts=True)) if n >= 2]
    whole = sum(1 for e in multi if len(grp[int(e)]) == 1)
    return {"sig_pairs_same_event": same_ev, "sig_pairs_same_sig": same_sig,
            "sig_pair_precision": both / same_sig if same_sig else 1.0,
            "sig_pair_recall": both / same_ev if same_ev else 1.0,
            "echo_groups": len(multi),
            "echo_groups_unsplit": whole / len(multi) if multi else 1.0,
            "n": int(len(ev))}


def text_signature_intrinsic(corpus: Corpus, rids: np.ndarray,
                             sig_of) -> Dict[str, float]:
    """The text recipe's accuracy over every note in ``rids`` (independent of
    any model): ``sig_of(rid)`` is the code-side signature."""
    rids = np.asarray(rids, dtype=np.int64)
    sig = np.array([sig_of(int(r)) for r in rids], dtype=np.int64)
    ev = corpus.recs["event"][rids].astype(np.int64)
    return _pairwise(ev, sig)


GAP_ROWS = [
    # (row label, live/sim metric key, nominal tier field, how the nominal maps)
    ("claim emission rate", "emission_rate", "extract_recall", "P(a note yields a claim)"),
    ("predicate error, causal notes, entity right", "pred_error_causal_given_entity_ok",
     "pred_confusion", "chain-neighbour slips only"),
    ("entity fidelity, predicate right", "entity_fidelity_given_pred_ok", "entity_fidelity",
     "per (author, entity), same-stem partner"),
    ("predicate error, all claims", "pred_error", None, ""),
    ("entity fidelity, all claims", "entity_fidelity", None, ""),
    ("spurious claims / note", "spurious_per_note", "extract_precision", "1 - extract_precision"),
    ("polarity accuracy", "polarity_acc", None, "exact at every tier"),
    ("exact-tuple precision", "precision", None, ""),
    ("exact-tuple recall, all notes", "recall", None, ""),
    ("exact-tuple recall, causal notes", "recall_causal", None, ""),
    ("exact-tuple recall, gold facet notes", "facet_recall", None, ""),
    ("exact-tuple recall, rare-pattern facet notes", "facet_recall_rare", None, ""),
    ("echo signature pair precision", "sig_pair_precision", None, "event id for 92% of claims"),
    ("echo signature pair recall", "sig_pair_recall", None, "event id for 92% of claims"),
]


def nominal(tier: Tier, field: Optional[str]) -> Optional[float]:
    if field is None:
        return None
    v = float(getattr(tier, field))
    return 1.0 - v if field == "extract_precision" else v


def gap_table(tier: Tier, sim: Dict[str, float], live: Dict[str, float],
              live_label: str, sim_label: str) -> str:
    """Markdown: nominal tier parameter | simulated operator measured on the
    same notes | live model measured on the same notes."""
    hdr = (f"| quantity | {tier.name} nominal | {sim_label} (measured) | "
           f"{live_label} (measured) | live - sim |\n|---|---|---|---|---|\n")
    rows = []
    for label, key, fld, how in GAP_ROWS:
        nv = nominal(tier, fld)
        if label == "polarity accuracy":
            ns = "1.000 (exact)"
        elif nv is None:
            ns = "-" if not how else how
        else:
            ns = f"{nv:.3f}" + (f" ({how})" if how else "")
        s, l_ = sim.get(key), live.get(key)
        fs = "-" if s is None else f"{s:.3f}"
        fl = "-" if l_ is None else f"{l_:.3f}"
        d = "-" if s is None or l_ is None else f"{l_ - s:+.3f}"
        rows.append(f"| {label} | {ns} | {fs} | {fl} | {d} |")
    return hdr + "\n".join(rows) + "\n"
