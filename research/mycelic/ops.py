"""Shared cognitive operators.

Every architecture under test (flat RAG, long context, recursive summary, all
hierarchy variants) calls *these* operators.  Architectures differ only in
    - which records/objects reach which operator call, and
    - which tier runs that call,
never in the quality of the operator itself.  That is the fairness contract.

Leakage contract
----------------
Operators may read ground-truth record fields in order to *simulate* a noisy
model.  Their OUTPUT must never expose ``kind``, ``group``, ``facet``,
``veracity`` or ``event``.  Systems consume only operator output plus the org
chart plus the surface text.  ``assert_no_leak`` in eval.py re-checks this.

Token accounting is derived from the actual serialised size of what each call
sees, so "context explosion" is measured, not assumed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from .corpus import (CAUSAL_CHAINS, CHAIN_OF_PRED, N_CAUSAL_PRED, PREDICATES,
                     PRED_ID, Corpus)
from .models import Tier

# token cost constants (measured from the generator's own output, see
# calibrate_token_costs())
TOK_PER_RECORD = 24       # surface text of one record, incl. header
TOK_PER_KO = 26           # one serialised knowledge object with lineage fields
TOK_PER_KO_NOLIN = 15     # without lineage/provenance fields
TOK_PER_HYP = 40          # one emitted hypothesis with evidence pointers
TOK_PER_QUESTION = 30
TOK_PROMPT_OVERHEAD = 120


def calibrate_token_costs(corpus: Corpus, n: int = 2000) -> float:
    """Mean surface tokens per record, measured on the actual corpus."""
    rng = np.random.default_rng(7)
    ids = rng.integers(0, len(corpus.recs), min(n, len(corpus.recs)))
    return float(np.mean([len(corpus.tokens(int(i))) + 8 for i in ids]))


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

# predicate confusion targets: neighbours in the same causal chain
_NEIGHBOURS: Dict[int, List[int]] = {}
for _ci, _ch in enumerate(CAUSAL_CHAINS):
    for _pos, _p in enumerate(_ch):
        nb = _NEIGHBOURS.setdefault(PRED_ID[_p], [])
        if _pos > 0:
            nb.append(PRED_ID[_ch[_pos - 1]])
        if _pos + 1 < len(_ch):
            nb.append(PRED_ID[_ch[_pos + 1]])


def _neighbour_table(n_pred: int) -> np.ndarray:
    tab = np.full((n_pred, 4), -1, dtype=np.int16)
    for p, nb in _NEIGHBOURS.items():
        for i, x in enumerate(nb[:4]):
            tab[p, i] = x
    return tab


NEIGHBOUR_TAB = _neighbour_table(len(PREDICATES))


@dataclass
class ExtractResult:
    """Columnar extraction output for a set of records."""
    rid: np.ndarray          # source record id (retrieval pointer)
    uid: np.ndarray
    pred: np.ndarray
    anchor: np.ndarray
    t: np.ndarray
    polarity: np.ndarray
    sig: np.ndarray          # near-duplicate signature (text-derived)
    spurious: np.ndarray     # bool: operator invented this claim

    def __len__(self) -> int:
        return len(self.rid)

    def take(self, idx: np.ndarray) -> "ExtractResult":
        return ExtractResult(self.rid[idx], self.uid[idx], self.pred[idx],
                             self.anchor[idx], self.t[idx], self.polarity[idx],
                             self.sig[idx], self.spurious[idx])


def _near_miss_map(corpus: Corpus, rng: np.random.Generator) -> np.ndarray:
    """For each entity, a lexically-similar different entity id."""
    ents = corpus.entities
    n = len(ents)
    stems: Dict[str, List[int]] = {}
    for i, e in enumerate(ents):
        stems.setdefault(e[:5], []).append(i)
    out = np.arange(n, dtype=np.int32)
    for s, ids in stems.items():
        if len(ids) < 2:
            continue
        arr = np.array(ids, dtype=np.int32)
        perm = rng.permutation(len(arr))
        shifted = arr[(perm + 1) % len(arr)]
        out[arr[perm]] = shifted
    return out


def _hash01(a: np.ndarray, salt: int) -> np.ndarray:
    """Deterministic uniform(0,1) keyed on an integer array (splitmix64)."""
    x = (a.astype(np.uint64) + np.uint64(salt & 0xFFFFFFFFFFFFFFF)) \
        * np.uint64(0x9E3779B97F4A7C15)
    x ^= x >> np.uint64(30)
    x *= np.uint64(0xBF58476D1CE4E5B9)
    x ^= x >> np.uint64(27)
    x *= np.uint64(0x94D049BB133111EB)
    x ^= x >> np.uint64(31)
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def stem_rep_map(corpus: Corpus) -> np.ndarray:
    """Map every entity onto the representative of its lexical stem group."""
    ents = corpus.entities
    rep = np.arange(len(ents), dtype=np.int32)
    first: Dict[str, int] = {}
    for i, e in enumerate(ents):
        k = e[:5]
        if k not in first:
            first[k] = i
        rep[i] = first[k]
    return rep


def extract(corpus: Corpus, rids: np.ndarray, tier: Tier, rng: np.random.Generator,
            near_miss: Optional[np.ndarray] = None,
            sig_collision: float = 0.08,
            stem_rep: Optional[np.ndarray] = None,
            entity_salt: int = 0) -> ExtractResult:
    """Simulated text -> claim extraction at a given model tier.

    Error modes, all tier-governed:
      * miss            (1 - extract_recall)
      * predicate slip  pred_confusion  -> a neighbour in the same causal chain
      * entity slip     (1 - entity_fidelity) -> a lexically similar entity
      * spurious claim  (1 - extract_precision)
      * signature collision: two genuinely independent records with the same
        (pred, anchor) and near timestamps look like near-duplicates.
    """
    if near_miss is None:
        near_miss = _near_miss_map(corpus, np.random.default_rng(99))
    if stem_rep is None:
        stem_rep = stem_rep_map(corpus)
    rids = np.asarray(rids, dtype=np.int64)
    rec = corpus.recs[rids]
    n = len(rids)
    keep = rng.random(n) < tier.extract_recall
    idx = np.nonzero(keep)[0]
    pred = rec["pred"][idx].astype(np.int16).copy()
    anchor = rec["anchor"][idx].astype(np.int32).copy()
    t = rec["t"][idx].astype(np.int32).copy()
    pol = rec["polarity"][idx].astype(np.int8).copy()
    sig = rec["event"][idx].astype(np.int64).copy()

    # predicate slip
    slip = rng.random(len(idx)) < tier.pred_confusion
    nb = NEIGHBOUR_TAB[pred]
    pick = rng.integers(0, 4, len(idx))
    cand = nb[np.arange(len(idx)), pick]
    ok = slip & (cand >= 0)
    pred[ok] = cand[ok]

    # Entity resolution errors are per (AGENT, ENTITY), not per record: an
    # agent that mis-links "nimbus-pay" once mis-links it every time, and an
    # agent that resolves it correctly keeps doing so.  Modelling this per
    # record instead would make a 7-record facet survive with probability
    # p^7 and would turn the whole study into a referendum on extraction
    # noise rather than on architecture.
    ukey = (rec["uid"][idx].astype(np.int64) * 1_000_003
            + anchor.astype(np.int64))
    salt = int(tier.q * 1e6) + int(entity_salt)
    eslip = _hash01(ukey, salt + 11) < (1.0 - tier.entity_fidelity)
    anchor[eslip] = near_miss[anchor[eslip]]

    # NOTE: entity *over-merging* is deliberately NOT applied here.  Copying a
    # mention string is easy for any model; deciding that two similar strings
    # denote the same thing is the hard part, and it happens at synthesis.
    # See ``synthesize(..., stem_rep=...)``.  (Applying it at extraction was
    # measured to destroy entity locality in the index and with it the entire
    # routing signal - logs/research_log.md, iteration 5.)

    # signature collision between independent near-identical reports
    coll = rng.random(len(idx)) < sig_collision
    if coll.any():
        key = (pred.astype(np.int64) * 1_000_003 + anchor.astype(np.int64)) * 97 \
            + (t // 3)
        sig[coll] = key[coll] * -1 - 1     # negative space: collided pseudo-sigs

    spurious = np.zeros(len(idx), dtype=bool)
    res = ExtractResult(rid=rids[idx], uid=rec["uid"][idx].astype(np.int64),
                        pred=pred, anchor=anchor, t=t, polarity=pol, sig=sig,
                        spurious=spurious)

    # spurious claims
    n_spur = int(rng.binomial(n, max(0.0, 1.0 - tier.extract_precision)))
    if n_spur:
        src = rng.integers(0, max(1, len(idx)), n_spur)
        if len(idx) == 0:
            return res
        sp = ExtractResult(
            rid=res.rid[src].copy(),
            uid=res.uid[src].copy(),
            pred=rng.integers(0, len(PREDICATES), n_spur).astype(np.int16),
            anchor=rng.integers(0, len(corpus.entities), n_spur).astype(np.int32),
            t=res.t[src].copy(),
            polarity=np.ones(n_spur, dtype=np.int8),
            sig=(-2_000_000_000 - np.arange(n_spur)).astype(np.int64),
            spurious=np.ones(n_spur, dtype=bool))
        res = ExtractResult(
            np.concatenate([res.rid, sp.rid]), np.concatenate([res.uid, sp.uid]),
            np.concatenate([res.pred, sp.pred]),
            np.concatenate([res.anchor, sp.anchor]),
            np.concatenate([res.t, sp.t]),
            np.concatenate([res.polarity, sp.polarity]),
            np.concatenate([res.sig, sp.sig]),
            np.concatenate([res.spurious, sp.spurious]))
    return res


# ---------------------------------------------------------------------------
# Knowledge objects
# ---------------------------------------------------------------------------

@dataclass
class KO:
    """One propagated knowledge object.

    ``lineage`` is the node path from the originating node up to the current
    owner.  ``branches`` records, per org level, the distinct ancestor nodes
    that contributed - this is what independent-support counting uses.
    """
    __slots__ = ("pred", "anchor", "tmin", "tmax", "polarity", "n_raw",
                 "sigs", "evidence", "branches", "lineage", "owner", "level",
                 "conf", "importance", "novelty", "contra", "revisions",
                 "q_tag", "origin_users", "pos_tmax", "neg_tmax")
    pred: int
    anchor: int
    tmin: int
    tmax: int
    polarity: int
    n_raw: int
    sigs: Set[int]
    evidence: List[int]
    branches: Dict[int, Set[int]]
    lineage: Tuple[int, ...]
    owner: int
    level: int
    conf: float
    importance: float
    novelty: float
    contra: int
    revisions: int
    q_tag: int
    origin_users: Set[int]
    pos_tmax: int
    neg_tmax: int

    def key(self) -> Tuple[int, int]:
        return (self.pred, self.anchor)

    def n_indep(self) -> int:
        return len(self.sigs)


MAX_EVIDENCE = 6          # retrieval pointers retained per KO
MAX_SIGS = 64             # bounded sketch of distinct source signatures
MAX_USERS = 32


def _merge_into(a: KO, b: KO, keep_lineage: bool = True) -> None:
    a.tmin = min(a.tmin, b.tmin)
    a.tmax = max(a.tmax, b.tmax)
    a.pos_tmax = max(a.pos_tmax, b.pos_tmax)
    a.neg_tmax = max(a.neg_tmax, b.neg_tmax)
    a.n_raw += b.n_raw
    if len(a.sigs) < MAX_SIGS:
        a.sigs |= b.sigs
    if len(a.evidence) < MAX_EVIDENCE:
        a.evidence.extend(b.evidence[:MAX_EVIDENCE - len(a.evidence)])
    if keep_lineage:
        for lvl, s in b.branches.items():
            a.branches.setdefault(lvl, set()).update(s)
        if len(a.origin_users) < MAX_USERS:
            a.origin_users |= b.origin_users
    if a.polarity != b.polarity:
        a.contra += 1
    a.revisions += 1
    a.importance = max(a.importance, b.importance)
    a.novelty = max(a.novelty, b.novelty)
    a.conf = min(0.99, 1.0 - (1.0 - a.conf) * (1.0 - b.conf) ** 0.5)


# ---------------------------------------------------------------------------
# Novelty / importance (observable features only - NO ground truth)
# ---------------------------------------------------------------------------

CAUSAL_MASK = np.zeros(len(PREDICATES), dtype=bool)
CAUSAL_MASK[:N_CAUSAL_PRED] = True


class BaseRate:
    """Global observed frequency of (pred, anchor) pairs, built from what the
    *user layer* actually extracted.  Used for novelty scoring.  This is an
    observable statistic; it contains no ground-truth labels."""

    def __init__(self, ex: ExtractResult, n_pred: int, n_ent: int):
        self.n_pred = n_pred
        self.n_ent = n_ent
        self.pair = {}
        key = ex.pred.astype(np.int64) * n_ent + ex.anchor.astype(np.int64)
        u, cnt = np.unique(key, return_counts=True)
        self.keys = u
        self.cnts = cnt
        self.total = float(cnt.sum()) + 1.0
        self.pred_cnt = np.bincount(ex.pred.astype(np.int64), minlength=n_pred) + 1.0
        self.ent_cnt = np.bincount(ex.anchor.astype(np.int64), minlength=n_ent) + 1.0

    def novelty(self, pred: np.ndarray, anchor: np.ndarray) -> np.ndarray:
        key = pred.astype(np.int64) * self.n_ent + anchor.astype(np.int64)
        pos = np.searchsorted(self.keys, key)
        pos = np.clip(pos, 0, len(self.keys) - 1)
        hit = self.keys[pos] == key
        c = np.where(hit, self.cnts[pos], 1)
        return np.log(self.total / c) / np.log(self.total)


class LocalBaseRate:
    """Per-org-unit frequency of (pred, anchor).

    Realised by a cheap, LLM-free statistical pre-pass: every node publishes a
    compact frequency sketch of its own subtree upward, and the unit's
    "what is normal here" profile is broadcast back down before the
    propagation pass.  Novelty is then *local* surprise, which is the only
    feature that actually separates a weak cross-cutting signal from local
    routine traffic.  Cost is metered as sketch tokens, not model tokens.
    """

    def __init__(self, ex: "ExtractResult", unit_of_record: np.ndarray,
                 n_ent: int):
        # entity-level: "is this thing normally seen here at all?"
        acomb = unit_of_record.astype(np.int64) * (1 << 34) \
            + ex.anchor.astype(np.int64)
        au, ainv, acnt = np.unique(acomb, return_inverse=True,
                                   return_counts=True)
        self.anchor_count = acnt[ainv].astype(np.float64)
        # pair-level: "has this thing ever done this here?"
        key = ex.pred.astype(np.int64) * n_ent + ex.anchor.astype(np.int64)
        comb = unit_of_record.astype(np.int64) * (1 << 34) + key
        uc, inv, cnt = np.unique(comb, return_inverse=True, return_counts=True)
        self.count_per_record = cnt[inv].astype(np.float64)
        un, uinv, ucnt = np.unique(unit_of_record, return_inverse=True,
                                   return_counts=True)
        self.unit_total = ucnt[uinv].astype(np.float64)
        self.n_units = len(un)
        self.n_entries = len(uc) + len(au)

    def novelty(self) -> np.ndarray:
        lg = np.log(np.maximum(3.0, self.unit_total))
        ent = np.log(self.unit_total / self.anchor_count) / lg
        pair = np.log(self.unit_total / self.count_per_record) / lg
        return 0.75 * ent + 0.25 * pair

    def sketch_tokens(self, cap: int = 200) -> int:
        """Serialised size of the sketches that travel up/down (3 tok/entry)."""
        return int(3 * min(self.n_entries, cap * self.n_units))


class ForeignScore:
    """"This entity belongs somewhere else."

    For every entity we compute its spatial concentration across sites
    (max share of its mentions held by one site).  A record mentioning a
    strongly site-bound entity *away from that entity's home* is the
    cheapest possible weak-signal detector, and it is exactly what a
    cross-branch pattern looks like from the inside.

    It needs one global statistic - per (site, entity) counts - which in the
    hierarchy is produced by the same sketch that travels up with the
    knowledge objects and is broadcast back down once.  No model tokens are
    involved; the sketch size is metered separately.  Every architecture under
    test receives this feature, so it is not a hierarchy-only advantage.
    """

    def __init__(self, ex: "ExtractResult", site_of_record: np.ndarray):
        anc = ex.anchor.astype(np.int64)
        site = site_of_record.astype(np.int64)
        comb = site * (1 << 34) + anc
        uc, inv, cnt = np.unique(comb, return_inverse=True, return_counts=True)
        pair_cnt = cnt[inv].astype(np.float64)
        ua, ainv, acnt = np.unique(anc, return_inverse=True, return_counts=True)
        ent_tot = acnt[ainv].astype(np.float64)
        share = pair_cnt / np.maximum(1.0, ent_tot)
        # concentration = the largest share any single site holds
        ent_of_pair = uc & np.int64((1 << 34) - 1)
        pos = np.searchsorted(ua, ent_of_pair)
        best = np.zeros(len(ua), dtype=np.float64)
        pair_share = cnt.astype(np.float64) / np.maximum(
            1.0, acnt[np.clip(pos, 0, len(ua) - 1)])
        np.maximum.at(best, np.clip(pos, 0, len(ua) - 1), pair_share)
        self.conc = best[ainv]
        self.share = share
        self.n_entries = len(uc)

    def score(self) -> np.ndarray:
        return self.conc * (1.0 - self.share)

    def sketch_tokens(self) -> int:
        return int(3 * self.n_entries)


def importance_score(pred: np.ndarray, novelty: np.ndarray, t: np.ndarray,
                     polarity: np.ndarray, n_days: int,
                     watch: Optional[Set[int]] = None,
                     anchor: Optional[np.ndarray] = None,
                     w: Optional[Dict[str, float]] = None,
                     local_novelty: Optional[np.ndarray] = None) -> np.ndarray:
    """Observable importance heuristic shared by every architecture."""
    ww = {"causal": 0.25, "novelty": 0.15, "local": 0.55, "interaction": 1.10,
          "recency": 0.10, "negative": 0.10, "watch": 0.55}
    if w:
        ww.update(w)
    cz = CAUSAL_MASK[pred].astype(np.float64)
    s = (ww["causal"] * cz
         + ww["novelty"] * novelty
         + ww["recency"] * (t / max(1, n_days))
         + ww["negative"] * (polarity < 0).astype(np.float64))
    if local_novelty is not None:
        # The interaction is the load-bearing term: an *operational* event
        # about an entity that is not normally seen in this unit.  Either
        # feature alone is common (routine ops chatter; benign cross-site
        # traffic); together they are rare.
        s = s + ww["local"] * local_novelty \
            + ww["interaction"] * (cz * local_novelty)
    if watch and anchor is not None and len(watch):
        wa = np.fromiter((a in watch for a in anchor.tolist()), dtype=bool,
                         count=len(anchor))
        s = s + ww["watch"] * wa
    return s


# ---------------------------------------------------------------------------
# Synthesis: candidate hypotheses + the four structural checks
# ---------------------------------------------------------------------------

@dataclass
class Hypothesis:
    anchor: int
    preds: List[int]
    kos: List[KO]
    evidence: List[int]
    n_indep: int
    n_branch_regions: int
    n_branch_sites: int
    conf: float
    contra: int
    tspan: Tuple[int, int]
    chain: int = -1
    hallucinated: bool = False
    prior: float = 0.0
    dispersion: float = 0.0        # sources spread over sites, per link
    synchrony: float = 0.0         # links whose reports all land on one day
    attribution: float = -1.0      # fraction of evidence that names this entity
    from_question: int = -1
    label: str = "pattern"


def _chain_span(mask: int) -> Tuple[int, int]:
    """Longest run of predicates from ONE causal chain present in a bitmask.

    This is the entity-level relational test the kernel runs over the
    propagated sketches: it costs a handful of integer operations per entity
    and needs no model call at all.
    """
    best, bci = 0, -1
    for ci, ch in enumerate(CAUSAL_CHAINS):
        n = 0
        for p in ch:
            i = PRED_ID[p]
            if i < 63 and (mask >> i) & 1:
                n += 1
        if n > best:
            best, bci = n, ci
    return best, bci


def _valid_chain_path(preds: Sequence[int]) -> Tuple[bool, int]:
    """True iff preds form a set of >=2 distinct predicates lying on one chain."""
    best = (False, -1)
    for ci, ch in enumerate(CAUSAL_CHAINS):
        ids = [PRED_ID[p] for p in ch]
        pos = [ids.index(p) for p in preds if p in ids]
        if len(pos) >= 2 and len(set(pos)) == len(pos):
            if max(pos) - min(pos) <= len(preds) + 1:
                return True, ci
            best = (True, ci)
    return best


def synthesize(kos: List[KO], tier: Tier, rng: np.random.Generator,
               org, min_preds: int = 2,
               use_lineage: bool = True,
               use_dedup: bool = True,
               use_temporal: bool = True,
               use_entity_check: bool = True,
               n_entities: int = 1000,
               max_reports: int = 1200,
               min_link_support: int = 1,
               w_dispersion: float = 0.0,
               w_synchrony: float = 0.0,
               stem_rep: Optional[np.ndarray] = None,
               question_tag: int = -1) -> List[Hypothesis]:
    """Enumerate candidate strategic patterns and verify them.

    For every anchor entity the kernel considers each causal chain separately
    and proposes the sub-path of predicates it can actually see.  Verification
    is four independent checks, each of which the tier passes with its own
    probability and otherwise *fails open* (i.e. does not notice the problem):

      causal    - are these predicates really on one chain?
      temporal  - are they in causal order, and has the chain been retracted
                  later (stale)?
      dedup     - how many genuinely independent sources are behind it?
      entity    - handled at extraction time (near-miss over-merging)

    A tier that fails the causal check also emits an *unverified* anchor-level
    hypothesis lumping together whatever predicates share the anchor; that is
    how entity-coincidence decoys get accepted.
    """
    from .org import REGION, SITE
    # Entity linking.  A weak tier collapses lexically similar entity names
    # onto one identity; a strong tier keeps them apart.  This is the single
    # mechanism that lets near-miss (D3) decoys assemble into apparently valid
    # causal chains, and it costs real patterns their anchor identity when it
    # fires spuriously.  The decision is per entity, not per mention.
    amap: Dict[int, int] = {}
    if stem_rep is not None and use_entity_check:
        uniq = np.array(sorted({k.anchor for k in kos}), dtype=np.int64)
        if len(uniq):
            merge = _hash01(uniq, int(tier.q * 1e6) + 7717) < \
                (1.0 - tier.entity_check)
            for a_, m_ in zip(uniq.tolist(), merge.tolist()):
                if m_ and stem_rep[a_] != a_:
                    amap[int(a_)] = int(stem_rep[a_])
    by_anchor: Dict[int, List[KO]] = {}
    for k in kos:
        by_anchor.setdefault(amap.get(k.anchor, k.anchor), []).append(k)

    chain_pos: List[Dict[int, int]] = []
    for ch in CAUSAL_CHAINS:
        chain_pos.append({PRED_ID[p]: i for i, p in enumerate(ch)})

    out: List[Hypothesis] = []

    def _mk(anchor: int, plist: List[int], pred_kos: Dict[int, List[KO]],
            chain: int, penalty: float, verified: bool) -> None:
        # ---- per-link independent support ----
        # A chain is only as strong as its weakest link.  Counting support per
        # LINK (rather than over the whole hypothesis) is what stops a single
        # stray background mention from completing an otherwise plausible
        # causal story, and it is where echo-vs-independent-source matters.
        link_sup: List[int] = []
        link_regions: List[Set[int]] = []
        link_modal: List[int] = []
        disp: List[float] = []
        sync: List[float] = []
        for p in plist:
            ks = pred_kos[p]
            sg: Set[int] = set()
            rg: Set[int] = set()
            st: Set[int] = set()
            rc: Dict[int, int] = {}
            tlo, thi, nraw = 10 ** 9, -1, 0
            for k in ks:
                sg |= k.sigs
                nraw += k.n_raw
                tlo = min(tlo, k.tmin)
                thi = max(thi, k.tmax)
                if use_lineage:
                    kr = k.branches.get(REGION, set())
                    rg |= kr
                    st |= k.branches.get(SITE, set())
                    for rr in kr:
                        rc[rr] = rc.get(rr, 0) + k.n_raw
            link_modal.append(max(rc, key=rc.get) if rc else -1)
            # SOURCE DISPERSION: are this link's reports spread over sites, or
            # are they several departments of one loud site?  Measured to be a
            # decisive cue that aggregate counts hide.
            disp.append(len(st) / max(1.0, float(len(sg))) if use_lineage else 1.0)
            # SYNCHRONY: many reports that all land in the same day or two are
            # one announcement fanned out, not independent discovery.
            span = max(0, thi - tlo)
            sync.append(1.0 if (nraw >= 4 and span <= 1) else 0.0)
            if not use_dedup or rng.random() > tier.dedup_check:
                link_sup.append(sum(k.n_raw for k in ks))   # echoes counted
            else:
                link_sup.append(len(sg))
            link_regions.append(rg)
        if len(plist) < min_preds:
            return
        members = [k for p in plist for k in pred_kos[p]]

        sigs: Set[int] = set()
        for k in members:
            sigs |= k.sigs
        n_indep = len(sigs)
        if (not use_dedup) or rng.random() > tier.dedup_check:
            n_indep = sum(k.n_raw for k in members)
        regions: Set[int] = set()
        sites: Set[int] = set()
        if use_lineage:
            for k in members:
                regions |= k.branches.get(REGION, set())
                sites |= k.branches.get(SITE, set())
        ev: List[int] = []
        for k in members:
            ev.extend(k.evidence[:3])
        contra = sum(k.contra for k in members)
        if use_lineage:
            spread = len(regions)
            # The decisive structural feature: are the links of this chain
            # carried by DIFFERENT parts of the organisation?  A genuine
            # cross-org pattern has each link dominated by a different branch.
            # A story assembled entirely out of one site's own traffic has one.
            # Only lineage makes this computable - this is what the
            # lineage ablation actually removes.
            multi = len({r for r in link_modal if r >= 0})
        else:
            spread = min(4, max(1, sum(k.n_raw for k in members) // 4))
            multi = 1
        min_sup = min(link_sup) if link_sup else 0
        mean_disp = float(np.mean(disp)) if disp else 1.0
        frac_sync = float(np.mean(sync)) if sync else 0.0
        # Calibrated logistic rather than a clipped linear sum: with a linear
        # score most candidates pin at the ceiling and the ranking - which is
        # what an executive report budget actually consumes - becomes
        # arbitrary.
        z = (-3.25
             + 0.35 * min(4, len(plist))
             + 0.45 * min(4, min_sup)
             + 0.30 * min(3, max(0, spread - 1))
             + 0.95 * min(3, max(0, multi - 1))
             + (0.55 if verified else 0.0)
             - 0.35 * min(4, contra)
             - 3.0 * penalty
             + w_dispersion * (mean_disp - 0.5)
             - w_synchrony * frac_sync)
        conf = float(1.0 / (1.0 + math.exp(-z)))
        out.append(Hypothesis(
            anchor=anchor, preds=plist, kos=members, evidence=ev[:24],
            n_indep=n_indep, n_branch_regions=len(regions),
            n_branch_sites=len(sites), conf=conf, contra=contra,
            tspan=(min(k.tmin for k in members), max(k.tmax for k in members)),
            chain=chain, from_question=question_tag,
            dispersion=mean_disp, synchrony=frac_sync))

    def _support(ks: List[KO]) -> int:
        sg: Set[int] = set()
        for k in ks:
            sg |= k.sigs
        return len(sg)

    for anchor, group in by_anchor.items():
        preds: Dict[int, List[KO]] = {}
        for k in group:
            preds.setdefault(k.pred, []).append(k)
        if len(preds) < min_preds:
            continue
        causal_ok = rng.random() < tier.causal_check
        if causal_ok:
            for ci, pos in enumerate(chain_pos):
                on = [p for p in preds if p in pos]
                if len(on) < min_preds:
                    continue
                on.sort(key=lambda p: pos[p])
                # (1) thin links first: a single stray mention should not be
                #     allowed to complete an otherwise plausible causal story
                if use_dedup and rng.random() < tier.dedup_check:
                    strong = [p for p in on
                              if _support(preds[p]) >= min_link_support]
                    if len(strong) >= min_preds:
                        on = strong
                # (2) temporal: take the longest chain-ordered, time-ordered
                #     sub-path rather than rejecting the whole group because
                #     one unrelated mention is out of order.  A weak tier
                #     cannot do this and keeps the raw set.
                if use_temporal:
                    tmins = [min(k.tmin for k in preds[p]) for p in on]
                    if rng.random() < tier.temporal_check:
                        # Heaviest chain-ordered, time-ordered sub-path, where
                        # a link's weight is its independent support.  Pure
                        # longest-path picks up thin background links and
                        # drops well-evidenced ones, which loses real patterns.
                        wts = [math.log1p(_support(preds[p])) + 0.35 for p in on]
                        best = list(wts)
                        prev = [-1] * len(on)
                        for i2 in range(len(on)):
                            for j2 in range(i2):
                                if tmins[j2] <= tmins[i2] + 3 and \
                                        best[j2] + wts[i2] > best[i2]:
                                    best[i2] = best[j2] + wts[i2]
                                    prev[i2] = j2
                        end = int(np.argmax(best))
                        path = []
                        while end >= 0:
                            path.append(on[end])
                            end = prev[end]
                        path.reverse()
                        if len(path) < min_preds:
                            continue
                        on = path
                    # staleness: the chain was retracted later than asserted
                    mem0 = [k for p in on for k in preds[p]]
                    neg = max((k.neg_tmax for k in mem0), default=-1)
                    pos_t = max((k.pos_tmax for k in mem0), default=-1)
                    if neg > pos_t >= 0 and rng.random() < tier.temporal_check:
                        continue
                on = on[:max(2, tier.synth_depth)]
                _mk(anchor, on, preds, ci, 0.0, verified=True)
        else:
            ordered = sorted(preds.items(),
                             key=lambda kv: -max(x.importance for x in kv[1]))
            ordered = ordered[:max(2, tier.synth_depth)]
            plist = [p for p, _ in ordered]
            ci = -1
            for cj, pos in enumerate(chain_pos):
                if sum(1 for p in plist if p in pos) >= 2:
                    ci = cj
                    break
            _mk(anchor, plist, preds, ci, 0.06, verified=False)

    # --- hallucination: unsupported hypotheses ---
    n_hall = int(rng.binomial(max(1, len(out)), tier.hallucination))
    for _ in range(n_hall):
        ci = int(rng.integers(0, len(CAUSAL_CHAINS)))
        ch = CAUSAL_CHAINS[ci]
        L = int(rng.integers(2, min(4, len(ch)) + 1))
        sp = int(rng.integers(0, len(ch) - L + 1))
        out.append(Hypothesis(
            anchor=int(rng.integers(0, n_entities)),
            preds=[PRED_ID[ch[sp + j]] for j in range(L)], kos=[], evidence=[],
            n_indep=int(rng.integers(1, 5)), n_branch_regions=1,
            n_branch_sites=1, conf=float(0.3 + 0.4 * rng.random()), contra=0,
            tspan=(0, 0), chain=ci, hallucinated=True))
    out.sort(key=lambda h: -h.conf)
    return out[:max_reports]


def detect_contradictions(kos: List[KO], tier: Tier,
                          rng: np.random.Generator) -> List[Tuple[KO, int]]:
    """Return KOs judged internally contradictory, with tier-limited accuracy."""
    out = []
    for k in kos:
        true_c = k.contra > 0
        acc = rng.random() < tier.contradiction_acc
        judged = true_c if acc else (not true_c)
        if judged:
            out.append((k, k.contra))
    return out
