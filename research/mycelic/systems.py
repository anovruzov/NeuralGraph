"""Architectures under test.

All of them consume the same corpus through the same operators in ops.py.

  A  flat_rag            lexical retrieval over raw text -> one kernel call
  B  long_context        largest possible raw-text context -> one kernel call
  B2 map_reduce          cheap extraction over EVERY record (same cost as the
                         hierarchy's user layer) -> global importance rank ->
                         one strong kernel call.  This is the strongest
                         centralised competitor and exists specifically to
                         stop the hierarchy winning by default.
  C  recursive_summary   text-level summarisation up the org tree
  D..H hierarchical      structured abstraction with feature flags:
                           lineage, dedup/independence, temporal, contradiction,
                           downward retrieval, questioning, cross-links
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .corpus import (CAUSAL_CHAINS, N_CAUSAL_PRED, PREDICATES, PRED_ID, Corpus)
from .models import Tier, USD_PER_MTOK
from .org import DEPT, ENT, Org, REGION, SITE, TEAM, USER
from .ops import (BaseRate, CAUSAL_MASK, ExtractResult, ForeignScore,
                  Hypothesis, KO,
                  stem_rep_map,
                  LocalBaseRate,
                  MAX_EVIDENCE,
                  MAX_SIGS, MAX_USERS, TOK_PER_HYP, TOK_PER_KO,
                  TOK_PER_KO_NOLIN, TOK_PER_QUESTION, TOK_PER_RECORD,
                  TOK_PROMPT_OVERHEAD, _chain_span, _merge_into, _valid_chain_path,
                  _near_miss_map, detect_contradictions, extract,
                  importance_score, synthesize)


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------

@dataclass
class LevelMeter:
    calls: int = 0
    tok_in: int = 0
    tok_out: int = 0
    cu: float = 0.0
    usd: float = 0.0
    max_ctx: int = 0
    lat: List[float] = field(default_factory=list)
    tier: str = ""
    concurrency: int = 1


class Meter:
    def __init__(self) -> None:
        self.levels: Dict[str, LevelMeter] = {}

    def add(self, stage: str, tier: Tier, tok_in: int, tok_out: int,
            calls: int = 1, sample_lat: bool = True) -> None:
        m = self.levels.setdefault(stage, LevelMeter(tier=tier.name,
                                                     concurrency=tier.concurrency))
        m.calls += calls
        m.tok_in += tok_in
        m.tok_out += tok_out
        m.cu += tier.cu(tok_in, tok_out)
        m.usd += USD_PER_MTOK.get(tier.name, 1.0) * (tok_in + tok_out) / 1e6
        per_in = tok_in // max(1, calls)
        per_out = tok_out // max(1, calls)
        m.max_ctx = max(m.max_ctx, per_in)
        if sample_lat and len(m.lat) < 4000:
            m.lat.append(tier.seconds(per_in, per_out))

    # ---- aggregates ----
    @property
    def tok_in(self) -> int:
        return sum(m.tok_in for m in self.levels.values())

    @property
    def tok_out(self) -> int:
        return sum(m.tok_out for m in self.levels.values())

    @property
    def calls(self) -> int:
        return sum(m.calls for m in self.levels.values())

    @property
    def cu(self) -> float:
        return sum(m.cu for m in self.levels.values())

    @property
    def usd(self) -> float:
        return sum(m.usd for m in self.levels.values())

    def wall_seconds(self) -> float:
        """Critical path: stages run in sequence, calls within a stage run in
        parallel up to the tier's fleet concurrency."""
        tot = 0.0
        for m in self.levels.values():
            if not m.calls:
                continue
            per = float(np.mean(m.lat)) if m.lat else 0.0
            tot += math.ceil(m.calls / max(1, m.concurrency)) * per
        return tot

    def call_latency_pct(self, p: float) -> float:
        allv = [x for m in self.levels.values() for x in m.lat]
        return float(np.percentile(allv, p)) if allv else 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "tok_in": self.tok_in, "tok_out": self.tok_out,
            "calls": self.calls, "cu": round(self.cu, 2),
            "usd": round(self.usd, 4),
            "wall_s": round(self.wall_seconds(), 2),
            "p50_call_s": round(self.call_latency_pct(50), 4),
            "p95_call_s": round(self.call_latency_pct(95), 4),
            "max_ctx": max((m.max_ctx for m in self.levels.values()), default=0),
            "by_stage": {k: {"calls": v.calls, "tok_in": v.tok_in,
                             "tok_out": v.tok_out, "cu": round(v.cu, 1),
                             "tier": v.tier, "max_ctx": v.max_ctx}
                         for k, v in self.levels.items()},
        }


@dataclass
class Question:
    qid: int
    anchor: int
    target_preds: List[int]
    why: str
    branch_hint: int
    expected_gain: float
    cost_est: float
    urgency: float
    well_targeted: bool = True
    answered: bool = False
    n_new_evidence: int = 0
    changed_conclusion: bool = False
    resolved_contradiction: bool = False


@dataclass
class RunResult:
    name: str
    hypotheses: List[Hypothesis]
    meter: Meter
    retained: Set[Tuple[int, int]]          # (pred, anchor) reaching the kernel
    kernel_kos: List[KO]
    questions: List[Question] = field(default_factory=list)
    families: List[List[int]] = field(default_factory=list)   # hyp index groups
    contradiction_calls: List[Tuple[int, int, bool]] = field(default_factory=list)
    notes: Dict[str, object] = field(default_factory=dict)
    # --- privacy accounting, three distinct things ---
    # raw record TEXT read by anything other than the owning user agent
    exposed_raw_records: int = 0
    # extracted CLAIMS (abstracted, no surface text) that left the user node
    claims_leaving_node: int = 0
    # index/sketch METADATA entries that left the node (entity + predicate
    # bitmask + counts; no claim content)
    sketch_entries_leaving_node: int = 0
    # underlying records represented by whatever left the node
    propagated_records: int = 0


# ---------------------------------------------------------------------------
# Shared: user-level extraction (the "map" everybody may pay for)
# ---------------------------------------------------------------------------

@dataclass
class UserLayer:
    ex: ExtractResult
    base: BaseRate
    imp: np.ndarray
    order: np.ndarray           # records sorted by uid
    uid_start: Dict[int, int]
    uid_count: Dict[int, int]
    tok_per_record: float


def user_extract(corpus: Corpus, tier: Tier, rng: np.random.Generator,
                 meter: Optional[Meter] = None, stage: str = "L0-extract",
                 near_miss: Optional[np.ndarray] = None,
                 local_context: bool = True) -> UserLayer:
    n_rec = len(corpus.recs)
    ex = extract(corpus, np.arange(n_rec), tier, rng, near_miss=near_miss)
    base = BaseRate(ex, len(PREDICATES), len(corpus.entities))
    nov = base.novelty(ex.pred, ex.anchor)
    org = corpus.org
    lnov = None
    sketch_tok = 0
    if local_context:
        site_of = np.array([org.ancestor_at(int(u), SITE) or -1
                            for u in ex.uid], dtype=np.int64)
        team_of = np.array([org.ancestor_at(int(u), TEAM) or -1
                            for u in ex.uid], dtype=np.int64)
        fs = ForeignScore(ex, site_of)
        lb_site = LocalBaseRate(ex, site_of, len(corpus.entities))
        lb_team = LocalBaseRate(ex, team_of, len(corpus.entities))
        # The SITE is the natural "is this entity normally seen here" context;
        # the team signal is much noisier (a 9-person team sees few entities at
        # all, so almost everything looks locally novel) and is only a small
        # correction.  Taking the max of the two was measured to destroy the
        # ranking - see logs/research_log.md, iteration 4.
        lnov = (0.70 * fs.score() + 0.22 * lb_site.novelty()
                + 0.08 * lb_team.novelty())
        sketch_tok = (lb_site.sketch_tokens() + lb_team.sketch_tokens()
                      + fs.sketch_tokens())
    imp = importance_score(ex.pred, nov, ex.t, ex.polarity, corpus.n_days,
                           local_novelty=lnov)
    order = np.argsort(ex.uid, kind="stable")
    ex = ex.take(order)
    imp = imp[order]
    nov = nov[order]
    uids = ex.uid
    uniq, starts, counts = np.unique(uids, return_index=True, return_counts=True)
    uid_start = {int(u): int(s) for u, s in zip(uniq, starts)}
    uid_count = {int(u): int(c) for u, c in zip(uniq, counts)}
    if meter is not None:
        # every user's local model reads that user's own records
        cnt = np.bincount(
            np.searchsorted(np.array(corpus.org.user_ids), corpus.recs["uid"]),
            minlength=len(corpus.org.user_ids))
        tin = int(corpus.recs.shape[0] * TOK_PER_RECORD
                  + len(corpus.org.user_ids) * TOK_PROMPT_OVERHEAD)
        tout = int(len(ex) * 10)
        meter.add(stage, tier, tin, tout, calls=len(corpus.org.user_ids))
    ul = UserLayer(ex=ex, base=base, imp=imp, order=order, uid_start=uid_start,
                   uid_count=uid_count, tok_per_record=TOK_PER_RECORD)
    ul.nov = np.maximum(nov, lnov) if lnov is not None else nov  # type: ignore
    ul.sketch_tokens = sketch_tok                                # type: ignore
    return ul


def _kos_from_claims(corpus: Corpus, ul: UserLayer, idx: np.ndarray,
                     owner: int, level: int, keep_lineage: bool,
                     org: Org) -> List[KO]:
    """Build merged KOs for one user's selected claims."""
    out: Dict[Tuple[int, int], KO] = {}
    ex = ul.ex
    for i in idx.tolist():
        pred = int(ex.pred[i]); anchor = int(ex.anchor[i])
        k = out.get((pred, anchor))
        u = int(ex.uid[i])
        if k is None:
            br: Dict[int, Set[int]] = {}
            if keep_lineage:
                for lvl in (TEAM, DEPT, SITE, REGION):
                    a = org.ancestor_at(u, lvl)
                    if a is not None:
                        br[lvl] = {a}
            out[(pred, anchor)] = KO(
                pred=pred, anchor=anchor, tmin=int(ex.t[i]), tmax=int(ex.t[i]),
                polarity=int(ex.polarity[i]), n_raw=1, sigs={int(ex.sig[i])},
                evidence=[int(ex.rid[i])], branches=br,
                lineage=(u,) if keep_lineage else (), owner=owner, level=level,
                conf=0.55, importance=float(ul.imp[i]),
                novelty=float(ul.nov[i]), contra=0, revisions=0, q_tag=-1,
                origin_users={u} if keep_lineage else set(),
                pos_tmax=int(ex.t[i]) if ex.polarity[i] > 0 else -1,
                neg_tmax=int(ex.t[i]) if ex.polarity[i] < 0 else -1)
        else:
            k.tmin = min(k.tmin, int(ex.t[i]))
            k.tmax = max(k.tmax, int(ex.t[i]))
            k.n_raw += 1
            if len(k.sigs) < MAX_SIGS:
                k.sigs.add(int(ex.sig[i]))
            if len(k.evidence) < MAX_EVIDENCE:
                k.evidence.append(int(ex.rid[i]))
            if k.polarity != int(ex.polarity[i]):
                k.contra += 1
            k.importance = max(k.importance, float(ul.imp[i]))
            k.novelty = max(k.novelty, float(ul.nov[i]))
            if ex.polarity[i] > 0:
                k.pos_tmax = max(k.pos_tmax, int(ex.t[i]))
            else:
                k.neg_tmax = max(k.neg_tmax, int(ex.t[i]))
    return list(out.values())


# ---------------------------------------------------------------------------
# Hierarchical family
# ---------------------------------------------------------------------------

@dataclass
class HierConfig:
    budgets: Tuple[int, int, int, int, int] = (6, 18, 42, 90, 130)
    # feature flags (ablation switches)
    lineage: bool = True
    independence: bool = True
    contradiction: bool = True
    temporal: bool = True
    adaptive_routing: bool = True
    questions: bool = True
    question_targeting: bool = True
    downward_retrieval: bool = True
    cross_links: bool = False
    confidence: bool = True
    adaptive_abstraction: bool = True
    n_questions: int = 220
    question_frac: float = 0.55
    max_questions: int = 4000
    n_descents: int = 40
    descent_fanout: int = 3
    cross_link_degree: int = 4
    cross_link_budget: int = 6
    bloom_fp: float = 0.0        # >0 -> approximate anchor index
    retrieval_depth: int = USER  # how far down a descent goes
    sketch_channel: bool = True
    sketch_cap: int = 4000
    sketch_min_support: int = 2
    foreign_share: float = 0.35
    foreign_only_evidence: bool = True
    restrict_synthesis_to_triage: bool = True
    pin_importance: float = 3.0
    triage_prior_weight: float = 1.2
    chain_completion: bool = False
    completion_budget: int = 400
    # --- adversarial / robustness knobs ---
    malicious_frac: float = 0.0      # nodes that fabricate or corrupt KOs
    unavailable_frac: float = 0.0    # branches temporarily unreachable
    unavailable_level: int = SITE
    triage_budget: int = 6000
    descent_leaf_cap: int = 140
    descent_frontier_cap: int = 400
    max_kernel_kos: int = 900
    max_reports: int = 0   # 0 -> scale with the entity namespace


class Hierarchy:
    def __init__(self, corpus: Corpus, alloc: List[Tier], cfg: HierConfig,
                 rng: np.random.Generator):
        self.c = corpus
        self.org = corpus.org
        self.alloc = alloc
        self.cfg = cfg
        self.rng = rng
        self.meter = Meter()
        self.kos_at: Dict[int, List[KO]] = {}
        self.anchor_index: Dict[int, Set[int]] = {}
        self.anchor_count: Dict[int, Dict[int, int]] = {}   # node -> anchor->n
        self.anchor_causal: Dict[int, Dict[int, int]] = {}  # node -> anchor->n_causal
        # Channel A: the complete, cheap, structured sketch.
        # node -> anchor -> [count, causal predicate bitmask, n_users, tmin, tmax]
        self.sketch: Dict[int, Dict[int, List[int]]] = {}
        self.site_sketch: Dict[int, Dict[int, Tuple[int, int, int, int]]] = {}
        self.sketch_entries = 0
        self.sketch_dropped = 0
        self.triage_entries = 0
        self.triage_sites: Dict[int, List[int]] = {}
        self.triage_home: Dict[int, int] = {}
        self.triage_gain: Dict[int, float] = {}
        self.completion_targets = 0
        self.index_entries = 0
        self.questions: List[Question] = []
        self.propagated = 0
        self.raw_reads = 0
        self._ul: Optional[UserLayer] = None
        self.watch: Set[int] = set()
        self.unavailable: Set[int] = set()
        self.malicious_nodes: Set[int] = set()

    # -- upward pass ------------------------------------------------------
    def build_user_layer(self, ul: UserLayer) -> None:
        self._ul = ul
        org = self.org
        cfg = self.cfg
        b0 = cfg.budgets[0]
        ex = ul.ex
        tier = self.alloc[USER]
        noise = self.rng.normal(0.0, tier.salience_noise, len(ex))
        score = ul.imp + noise
        for u in org.user_ids:
            s = ul.uid_start.get(u)
            if s is None:
                self.kos_at[u] = []
                continue
            n = ul.uid_count[u]
            sl = slice(s, s + n)
            sc = score[sl]
            keep = np.argsort(-sc)[:b0] + s
            kos = _kos_from_claims(self.c, ul, keep, u, USER, cfg.lineage, org)
            # retention loss during abstraction
            if kos and tier.abstract_retention < 1.0:
                m = self.rng.random(len(kos)) < tier.abstract_retention
                kos = [k for k, mm in zip(kos, m) if mm]
            self.kos_at[u] = kos
            self.propagated += sum(k.n_raw for k in kos)
        # anchor index (cheap metadata, no LLM call).  This is the structure
        # that makes targeted descent possible without broadcasting; its size
        # is metered as propagation volume.
        for u in org.user_ids:
            s = ul.uid_start.get(u)
            if s is None:
                self.anchor_index[u] = set()
                self.anchor_count[u] = {}
                continue
            n = ul.uid_count[u]
            a = ex.anchor[s:s + n]
            vals, cts = np.unique(a, return_counts=True)
            self.anchor_index[u] = set(int(x) for x in vals)
            self.anchor_count[u] = {int(x): int(c) for x, c in zip(vals, cts)}
            cm = CAUSAL_MASK[ex.pred[s:s + n]]
            if cm.any():
                cv, cc = np.unique(a[cm], return_counts=True)
                self.anchor_causal[u] = {int(x): int(c) for x, c in zip(cv, cc)}
            else:
                self.anchor_causal[u] = {}
            self.index_entries += len(vals)
            if self.cfg.sketch_channel:
                # per entity: total mentions + counts of each OPERATIONAL
                # predicate.  Background predicates carry no chain structure
                # and are not forwarded.
                sk: Dict[int, List] = {}
                pr = ex.pred[s:s + n]
                tt = ex.t[s:s + n]
                for ai, pi, ti in zip(a.tolist(), pr.tolist(), tt.tolist()):
                    e = sk.get(ai)
                    if e is None:
                        e = [0, {}, 1, ti, ti]
                        sk[ai] = e
                    e[0] += 1
                    e[3] = min(e[3], ti)
                    e[4] = max(e[4], ti)
                    if CAUSAL_MASK[pi]:
                        e[1][pi] = e[1].get(pi, 0) + 1
                self.sketch[u] = sk
                self.sketch_entries += len(sk)

    def aggregate_level(self, level: int, ul: UserLayer) -> None:
        org = self.org
        cfg = self.cfg
        tier = self.alloc[level]
        budget = cfg.budgets[level]
        nodes = org.levels[level]
        n_calls = 0
        tin = tout = 0
        for nid in nodes:
            if cfg.unavailable_frac > 0.0 and level == cfg.unavailable_level \
                    and self.rng.random() < cfg.unavailable_frac:
                # a site/region is temporarily unreachable: nothing propagates
                # from it and it cannot answer a descent either
                self.kos_at[nid] = []
                self.anchor_index[nid] = set()
                self.anchor_count[nid] = {}
                self.anchor_causal[nid] = {}
                self.sketch[nid] = {}
                self.unavailable.add(nid)
                continue
            kids = org.nodes[nid].children
            pool: List[KO] = []
            aset: Set[int] = set()
            acnt: Dict[int, int] = {}
            accz: Dict[int, int] = {}
            for c in kids:
                pool.extend(self.kos_at.get(c, []))
                aset |= self.anchor_index.get(c, set())
                for a2, c2 in self.anchor_count.get(c, {}).items():
                    acnt[a2] = acnt.get(a2, 0) + c2
                for a2, c2 in self.anchor_causal.get(c, {}).items():
                    accz[a2] = accz.get(a2, 0) + c2
            self.anchor_index[nid] = aset
            self.anchor_count[nid] = acnt
            self.anchor_causal[nid] = accz
            self.index_entries += len(aset)
            if cfg.sketch_channel:
                sk: Dict[int, List] = {}
                for ch in kids:
                    for a2, e2 in self.sketch.get(ch, {}).items():
                        e = sk.get(a2)
                        if e is None:
                            sk[a2] = [e2[0], dict(e2[1]), e2[2], e2[3], e2[4]]
                        else:
                            e[0] += e2[0]
                            for pp, cc in e2[1].items():
                                e[1][pp] = e[1].get(pp, 0) + cc
                            e[2] += e2[2]
                            e[3] = min(e[3], e2[3])
                            e[4] = max(e[4], e2[4])
                if len(sk) > cfg.sketch_cap:
                    ranked = sorted(sk.items(),
                                    key=lambda kv: -(len(kv[1][1]) * 4 + kv[1][2]))
                    self.sketch_dropped += len(sk) - cfg.sketch_cap
                    sk = dict(ranked[:cfg.sketch_cap])
                self.sketch[nid] = sk
                self.sketch_entries += len(sk)
                if level == SITE:
                    # At the site boundary the counts collapse to a support
                    # -thresholded bitmask: "which operational predicates were
                    # seen at least min_support times here, for this entity".
                    # Single stray mentions (benign cross-site traffic) are
                    # dropped; a real facet, which is several reports of the
                    # SAME predicate, survives.  One int per entity from here up.
                    ms = cfg.sketch_min_support
                    ent: Dict[int, Tuple[int, int, int, int]] = {}
                    for a2, e2 in sk.items():
                        mask = 0
                        for pp, cc in e2[1].items():
                            if cc >= ms and pp < 63:
                                mask |= (1 << pp)
                        ent[a2] = (e2[0], mask, e2[3], e2[4])
                    self.site_sketch[nid] = ent
            if not pool:
                self.kos_at[nid] = []
                continue
            n_calls += 1
            tin += len(pool) * (TOK_PER_KO if cfg.lineage else TOK_PER_KO_NOLIN) \
                + TOK_PROMPT_OVERHEAD
            merged: Dict[Tuple[int, int], KO] = {}
            for k in pool:
                key = k.key()
                m = merged.get(key)
                if m is None:
                    k.owner = nid
                    k.level = level
                    if cfg.lineage:
                        k.lineage = k.lineage + (nid,)
                    merged[key] = k
                else:
                    if self.rng.random() < tier.abstract_distortion:
                        continue            # lost in merge
                    _merge_into(m, k, keep_lineage=cfg.lineage)
            items = list(merged.values())
            # observable importance: novelty + breadth + watchlist
            for k in items:
                breadth = math.log1p(len(k.branches.get(TEAM, ())) if cfg.lineage
                                     else k.n_raw)
                k.importance = (k.importance + 0.25 * k.novelty
                                + 0.10 * breadth
                                + (0.7 if k.anchor in self.watch else 0.0))
                if cfg.adaptive_abstraction:
                    # a claim seen in several distinct sub-branches but rare
                    # overall is exactly the weak cross-cutting signal we want
                    if cfg.lineage and len(k.branches.get(TEAM, ())) >= 2 \
                            and k.novelty > 0.55:
                        k.importance += 0.45
            sc = np.array([k.importance for k in items]) \
                + self.rng.normal(0.0, tier.salience_noise, len(items))
            keep_idx = np.argsort(-sc)[:budget]
            kept = [items[i] for i in keep_idx.tolist()]
            if tier.abstract_retention < 1.0 and kept:
                m = self.rng.random(len(kept)) < tier.abstract_retention
                kept = [k for k, mm in zip(kept, m) if mm]
            if cfg.contradiction:
                for k in kept:
                    if k.contra > 0 and self.rng.random() > tier.contradiction_acc:
                        k.contra = 0
            if cfg.malicious_frac > 0.0 and self.rng.random() < cfg.malicious_frac:
                # A compromised or simply broken aggregator: it forwards
                # fabricated objects with inflated support and strips the
                # contradictions it saw.
                for k in kept:
                    k.contra = 0
                    k.sigs = set(range(-90_000_000 - 40 * nid,
                                       -90_000_000 - 40 * nid + 12))
                    k.n_raw = max(k.n_raw, 12)
                    k.importance += 1.5
                    if self.rng.random() < 0.5:
                        k.pred = int(self.rng.integers(0, 34))
                self.malicious_nodes.add(nid)
            self.kos_at[nid] = kept
            tout += len(kept) * (TOK_PER_KO if cfg.lineage else TOK_PER_KO_NOLIN)
        if n_calls:
            self.meter.add(f"L{level}-aggregate", tier, tin, tout, calls=n_calls)

    # -- cross links ------------------------------------------------------
    def cross_link(self, level: int = DEPT) -> None:
        """Semantic overlay: departments that share function or top anchors
        exchange their top KOs, so a two-facet partial pattern can form below
        the kernel and survive the upward budget."""
        org = self.org
        cfg = self.cfg
        tier = self.alloc[level]
        nodes = org.levels[level]
        if len(nodes) < 2:
            return
        # index nodes by their top anchors
        by_anchor: Dict[int, List[int]] = {}
        tops: Dict[int, List[int]] = {}
        for nid in nodes:
            ks = self.kos_at.get(nid, [])
            ts = sorted(ks, key=lambda k: -k.importance)[:cfg.cross_link_budget]
            tops[nid] = [k.anchor for k in ts]
            for a in set(tops[nid]):
                by_anchor.setdefault(a, []).append(nid)
        sent = 0
        calls = 0
        tin = tout = 0
        for a, group in by_anchor.items():
            if len(group) < 2 or len(group) > 60:
                continue
            for nid in group[:cfg.cross_link_degree * 8]:
                peers = [p for p in group if p != nid][:cfg.cross_link_degree]
                if not peers:
                    continue
                calls += 1
                incoming: List[KO] = []
                for p in peers:
                    for k in self.kos_at.get(p, []):
                        if k.anchor == a:
                            incoming.append(k)
                if not incoming:
                    continue
                tin += len(incoming) * TOK_PER_KO + TOK_PROMPT_OVERHEAD
                own = [k for k in self.kos_at.get(nid, []) if k.anchor == a]
                preds = {k.pred for k in own} | {k.pred for k in incoming}
                valid, _ = _valid_chain_path(sorted(preds))
                if valid and len(preds) >= 2:
                    # promote: mark local KOs on this anchor as high importance
                    for k in own:
                        k.importance += 0.9
                        k.q_tag = 1
                    sent += len(incoming)
                    tout += 8
        if calls:
            self.meter.add(f"L{level}-crosslink", tier, tin, tout, calls=calls)
        self.propagated += sent

    # -- downward retrieval ----------------------------------------------
    def descend(self, anchor: int, target_preds: Sequence[int],
                budget_nodes: int = 3,
                start_nodes: Optional[Sequence[int]] = None,
                avoid: Optional[Sequence[int]] = None) -> List[KO]:
        """Targeted descent guided by the propagated sketches.

        Two important properties, both learned the hard way:

        * The expansion is **breadth-balanced** (top-k per parent, not a global
          top-N).  A global sort is captured by the entity's home branch, which
          holds most of its mentions and none of the interesting foreign
          evidence.
        * When the triage already knows *which* sites showed repeated
          operational mentions of this entity, the descent starts there
          instead of searching from the root.  Routing cost is still metered.
        """
        org = self.org
        cfg = self.cfg
        found: List[KO] = []
        frontier = [org.root]
        levels = (REGION, SITE, DEPT, TEAM, USER)
        # sites the triage already flagged are injected into the frontier at
        # the site boundary.  They are a *seed*, not a replacement: restricting
        # the descent to them was measured to halve recovery, because the
        # sketch only lists sites whose evidence already passed the support
        # threshold and the thin facets are exactly the ones that did not.
        seeds = set(start_nodes or ())
        # The entity's HOME branch holds most of its mentions and none of the
        # interesting foreign evidence, so it is strongly down-ranked: the
        # retrieval budget should be spent where the entity does not belong.
        avoid_set = set(avoid or ())
        avoid_anc: Set[int] = set()
        for a_ in avoid_set:
            avoid_anc.add(a_)
            avoid_anc.update(org.ancestors(a_))
        per_parent = {REGION: 12, SITE: budget_nodes + 2, DEPT: budget_nodes + 2,
                      TEAM: budget_nodes + 2, USER: budget_nodes + 5}
        for level in levels:
            nxt: List[int] = []
            for nid in frontier:
                node = org.nodes[nid]
                if node.level == 0:
                    continue
                kids = node.children
                tier = self.alloc[max(1, node.level)]
                scored: List[Tuple[float, int]] = []
                for ch in kids:
                    nc = self.anchor_count.get(ch, {}).get(anchor, 0)
                    ncz = self.anchor_causal.get(ch, {}).get(anchor, 0)
                    if nc == 0:
                        if cfg.bloom_fp > 0.0 and self.rng.random() < cfg.bloom_fp:
                            scored.append((1e-6, ch))
                        continue
                    sc = 2.0 * ncz + nc
                    if ch in avoid_anc:
                        sc *= 0.12
                    scored.append((sc, ch))
                if not scored:
                    continue
                scored.sort(reverse=True)
                k = per_parent[level]
                if cfg.adaptive_routing:
                    if self.rng.random() > tier.route_quality and len(scored) > 1:
                        scored = scored[1:]
                    chosen = scored[:k]
                else:
                    self.rng.shuffle(scored)
                    chosen = scored[:k]
                nxt.extend(chosen)
                self.meter.add("descend-route", tier,
                               len(kids) * 6 + TOK_PROMPT_OVERHEAD, 20, calls=1)
            # per-parent quota has already guaranteed breadth; a global sort
            # on top of it keeps the best candidates when the frontier is cut
            nxt.sort(reverse=True)
            ordered = [n for _, n in nxt]
            if level == SITE and seeds:
                ordered = list(dict.fromkeys(list(seeds) + ordered))
            frontier = ordered[:cfg.descent_frontier_cap]
            if not frontier:
                return found
            if org.nodes[frontier[0]].level == cfg.retrieval_depth:
                break
        leaf_tier = self.alloc[USER]
        ul = self._ul
        for nid in frontier[:cfg.descent_leaf_cap]:
            if avoid_anc and cfg.foreign_only_evidence and \
                    org.ancestor_at(nid, SITE) in avoid_set:
                # Evidence from the entity's own home site is what the
                # organisation already knows; pulling it in dilutes the
                # cross-branch story the kernel is trying to verify.  Measured:
                # an unfiltered pool is *worse* than a filtered sample.
                continue
            if org.nodes[nid].level == USER and ul is not None:
                st = ul.uid_start.get(nid)
                if st is None:
                    continue
                n = ul.uid_count[nid]
                a = ul.ex.anchor[st:st + n]
                sel = np.nonzero(a == anchor)[0]
                if target_preds is not None and len(target_preds):
                    tp = np.array(list(target_preds))
                    pm = np.isin(ul.ex.pred[st:st + n][sel], tp)
                    if pm.any():
                        sel = sel[pm]
                # A queried user agent consults its OWN local index and reads
                # only the matching records - sovereign local memory is the
                # premise of the design, so charging it for a full re-read of
                # everything it has ever seen would be wrong.  The index lookup
                # itself is charged as a small fixed cost.
                self.meter.add("descend-read", leaf_tier,
                               len(sel) * TOK_PER_RECORD + TOK_PROMPT_OVERHEAD
                               + 12, len(sel) * TOK_PER_KO, calls=1)
                if len(sel) == 0:
                    continue
                kos = _kos_from_claims(self.c, ul, sel + st, nid, USER,
                                       cfg.lineage, org)
                for k in kos:
                    k.importance += 1.2
                    k.q_tag = 2
                found.extend(kos)
                self.raw_reads += int(n)
            else:
                kos = self.kos_at.get(nid, [])
                hit = [k for k in kos if k.anchor == anchor
                       and (not target_preds or k.pred in target_preds)]
                self.meter.add("descend-read", leaf_tier,
                               len(kos) * TOK_PER_KO + TOK_PROMPT_OVERHEAD,
                               len(hit) * TOK_PER_KO, calls=1)
                found.extend(hit)
        return found

    def deep_descend(self, anchor: int, target_preds: Sequence[int]) -> List[KO]:
        """Descend all the way to raw user records for the matching anchor.

        This is the "I need more evidence about X" path: it reaches records
        that never propagated at all.
        """
        org = self.org
        kos = self.descend(anchor, target_preds,
                           budget_nodes=self.cfg.descent_fanout)
        # also read raw records of the users under the reached teams
        return kos


class HierRunner:
    """Executes a hierarchical configuration end to end."""

    def __init__(self, corpus: Corpus, alloc: List[Tier], cfg: HierConfig,
                 seed: int = 0, ul: Optional[UserLayer] = None,
                 near_miss: Optional[np.ndarray] = None):
        self.c = corpus
        self.alloc = alloc
        self.cfg = cfg
        self.rng = np.random.default_rng(90_000 + seed)
        self.h = Hierarchy(corpus, alloc, cfg, self.rng)
        self._ul = ul
        self._near_miss = near_miss
        self._stem = stem_rep_map(corpus)

    def run(self) -> RunResult:
        c = self.c
        cfg = self.cfg
        h = self.h
        ul = self._ul if self._ul is not None else user_extract(
            c, self.alloc[USER], self.rng, near_miss=self._near_miss)
        # meter the user layer against THIS run's tier
        h.meter.add("L0-extract", self.alloc[USER],
                    int(len(c.recs) * TOK_PER_RECORD
                        + len(c.org.user_ids) * TOK_PROMPT_OVERHEAD),
                    int(len(ul.ex) * 10), calls=len(c.org.user_ids))
        h.build_user_layer(ul)
        for level in (TEAM, DEPT):
            h.aggregate_level(level, ul)
        if cfg.cross_links:
            h.cross_link(DEPT)
            h.aggregate_level(DEPT, ul) if False else None
        for level in (SITE, REGION):
            h.aggregate_level(level, ul)

        kernel_tier = self.alloc[ENT]
        pool: List[KO] = []
        for r in c.org.levels[REGION]:
            pool.extend(h.kos_at.get(r, []))
        pool = sorted(pool, key=lambda k: -k.importance)[:cfg.max_kernel_kos]
        h.meter.add("L5-kernel", kernel_tier,
                    len(pool) * (TOK_PER_KO if cfg.lineage else TOK_PER_KO_NOLIN)
                    + TOK_PROMPT_OVERHEAD, 0, calls=1)

        if cfg.max_reports <= 0:
            # A risk register holds roughly one entry per tracked entity; a
            # constant 1000 was binding at 50k users and silently truncated
            # most of the gold out of every system's output.
            cfg.max_reports = int(min(6000, max(600, len(c.entities))))
        hyps = synthesize(pool, kernel_tier, self.rng, c.org,
                          use_lineage=cfg.lineage,
                          use_dedup=cfg.independence,
                          use_temporal=cfg.temporal,
                          n_entities=len(c.entities),
                          max_reports=cfg.max_reports,
                          stem_rep=self._stem if hasattr(self,'_stem') else None)
        h.meter.add("L5-kernel", kernel_tier, 0, len(hyps) * TOK_PER_HYP, calls=0)

        # ---- downward retrieval ----
        # Evidence recovered by a descent is ACCUMULATED into the working pool;
        # dropping it between rounds (the original bug) silently discarded most
        # of what the retrieval path had just paid to fetch.
        if cfg.downward_retrieval:
            hyps, pool = self._descend_round(hyps, pool, kernel_tier)

        # ---- continual questioning ----
        if cfg.questions:
            hyps, pool = self._question_round(hyps, pool, kernel_tier)

        # ---- hypothesis-driven chain completion ----
        if cfg.chain_completion:
            hyps, pool = self._completion_round(hyps, pool, kernel_tier)

        # ---- cross-region variant families ----
        fams = self._families(hyps, kernel_tier)

        retained = {(k.pred, k.anchor) for k in pool}
        claims_out = sum(len(h.kos_at.get(u, [])) for u in c.org.user_ids)
        return RunResult(name="hier", hypotheses=hyps, meter=h.meter,
                         retained=retained, kernel_kos=pool,
                         questions=h.questions, families=fams,
                         propagated_records=h.propagated,
                         exposed_raw_records=0,
                         claims_leaving_node=claims_out,
                         sketch_entries_leaving_node=h.sketch_entries,
                         notes={"kernel_kos": len(pool),
                                "sketch_dropped": h.sketch_dropped,
                                "raw_records_reread_locally": h.raw_reads})

    # ---- helpers --------------------------------------------------------
    def _weak_targets(self, hyps: List[Hypothesis]) -> List[Tuple[int, List[int], str]]:
        out = []
        for hy in hyps:
            if hy.hallucinated:
                continue
            missing: List[int] = []
            if hy.chain >= 0:
                ch = [PRED_ID[p] for p in CAUSAL_CHAINS[hy.chain]]
                pos = sorted(ch.index(p) for p in hy.preds if p in ch)
                if pos:
                    for i in range(max(0, pos[0] - 1), min(len(ch), pos[-1] + 2)):
                        if ch[i] not in hy.preds:
                            missing.append(ch[i])
            why = ("missing chain link" if missing else
                   ("weak independent support" if hy.n_indep < 3 else
                    "contradiction" if hy.contra else ""))
            if why:
                out.append((hy.anchor, missing, why))
        return out

    def _descend_round(self, hyps: List[Hypothesis], pool: List[KO],
                       kernel_tier: Tier) -> Tuple[List[Hypothesis], List[KO]]:
        targets = self._weak_targets(hyps)[: self.cfg.n_descents]
        new_kos: List[KO] = []
        for anchor, missing, _why in targets:
            got = self.h.descend(anchor, missing,
                                 budget_nodes=self.cfg.descent_fanout)
            new_kos.extend(got)
        if not new_kos:
            return hyps, pool
        merged = pool + new_kos
        self.h.meter.add("L5-kernel-redo", kernel_tier,
                         len(merged) * TOK_PER_KO + TOK_PROMPT_OVERHEAD, 0,
                         calls=1)
        hyps2 = synthesize(merged, kernel_tier, self.rng, self.c.org,
                           use_lineage=self.cfg.lineage,
                           use_dedup=self.cfg.independence,
                           use_temporal=self.cfg.temporal,
                           n_entities=len(self.c.entities),
                           max_reports=self.cfg.max_reports,
                           stem_rep=self._stem if hasattr(self,'_stem') else None)
        self.h.meter.add("L5-kernel-redo", kernel_tier, 0,
                         len(hyps2) * TOK_PER_HYP, calls=0)
        return hyps2, merged

    def _question_round(self, hyps: List[Hypothesis], pool: List[KO],
                        kernel_tier: Tier) -> Tuple[List[Hypothesis], List[KO]]:
        cfg = self.cfg
        h = self.h
        qs: List[Question] = []
        qid = 0
        before = {self._hkey(x): x for x in hyps}
        # 1. missing-link / weak-support / contradiction questions
        for hy in sorted(hyps, key=lambda x: -x.conf):
            if hy.hallucinated:
                continue
            missing: List[int] = []
            if hy.chain >= 0:
                ch = [PRED_ID[p] for p in CAUSAL_CHAINS[hy.chain]]
                pos = sorted(ch.index(p) for p in hy.preds if p in ch)
                if pos:
                    for i in range(max(0, pos[0] - 1), min(len(ch), pos[-1] + 2)):
                        if ch[i] not in hy.preds:
                            missing.append(ch[i])
            why = ("missing chain link" if missing
                   else "weak independent support" if hy.n_indep < 3
                   else "contradictory evidence" if hy.contra
                   else "")
            if not why:
                continue
            gain = (0.5 * (len(missing) > 0) + 0.3 * (hy.n_indep < 3)
                    + 0.2 * (hy.contra > 0)) * (1.0 - abs(hy.conf - 0.5) * 1.2)
            well = self.rng.random() < kernel_tier.question_quality
            anchor = hy.anchor
            if not (well and cfg.question_targeting):
                anchor = int(self.rng.integers(0, len(self.c.entities)))
                missing = []
            qs.append(Question(qid=qid, anchor=anchor, target_preds=missing,
                               why=why, branch_hint=-1, expected_gain=float(gain),
                               cost_est=float(cfg.descent_fanout * 4),
                               urgency=float(hy.conf), well_targeted=well))
            qid += 1
        # 2. Sketch-driven triage (channel A).
        #    Each SITE forwarded, per entity, (total mentions, bitmask of
        #    operational predicates seen >= min_support times, time span).
        #    The kernel's test is relational and entity-level:
        #      - find sites where this entity is FOREIGN (small share of its
        #        total mentions) yet still shows a repeated operational
        #        predicate;
        #      - require >= 2 such sites in >= 2 different regions;
        #      - require the predicates involved to lie on one causal chain,
        #        in temporal order.
        #    No individual record can pass this test, which is why record-level
        #    propagation cannot find these patterns at all.  The test costs a
        #    few integer ops per entity and no model tokens.
        by_anchor: Dict[int, Set[int]] = {}
        for k in pool:
            by_anchor.setdefault(k.anchor, set()).add(k.pred)
        cands: List[Tuple[float, int, int, int, int]] = []
        if cfg.sketch_channel:
            org = self.c.org
            per_ent: Dict[int, List[Tuple[int, int, int, int, int]]] = {}
            tot_ent: Dict[int, int] = {}
            n_entries = 0
            for st, ent in h.site_sketch.items():
                reg = org.ancestor_at(st, REGION)
                for a2, (tot, mask, t0, t1) in ent.items():
                    tot_ent[a2] = tot_ent.get(a2, 0) + tot
                    if mask:
                        per_ent.setdefault(a2, []).append((st, reg, mask, t0, t1))
                        n_entries += 1
            h.meter.add("L5-triage", kernel_tier,
                        n_entries * 5 + TOK_PROMPT_OVERHEAD, 0, calls=1)
            h.triage_entries = n_entries
            for a2, rows in per_ent.items():
                total = max(1, tot_ent.get(a2, 1))
                # a site is "foreign" for this entity if it holds only a small
                # share of the entity's enterprise-wide mentions
                site_tot = {}
                for st, reg, mask, t0, t1 in rows:
                    site_tot[st] = h.site_sketch[st][a2][0]
                foreign = [(st, reg, mask, t0, t1) for st, reg, mask, t0, t1 in rows
                           if site_tot[st] / total <= cfg.foreign_share]
                if len(foreign) < 2:
                    continue
                regs = {r for _, r, _, _, _ in foreign}
                if len(regs) < 2:
                    continue
                umask = 0
                for _, _, m, _, _ in foreign:
                    umask |= m
                span, chain = _chain_span(umask)
                if span < 2:
                    continue
                seen = len(by_anchor.get(a2, ()))
                gain = (0.6 * span + 0.35 * len(regs) + 0.3 * (seen < 2)) \
                    / (1.0 + 0.25 * math.log1p(total))
                cands.append((gain, int(a2), len(regs), int(total), seen))
                h.triage_sites[int(a2)] = [st for st, _, _, _, _ in foreign]
                h.triage_gain[int(a2)] = float(gain)
                h.triage_home[int(a2)] = max(site_tot, key=site_tot.get)
        cands.sort(reverse=True)
        for gain, a2, nreg, tot, seen in cands[:cfg.triage_budget]:
            qs.append(Question(
                qid=qid, anchor=int(a2), target_preds=[],
                why=f"sketch: causal span across {nreg} regions, {tot} mentions, "
                    f"{seen} facets already at kernel",
                branch_hint=-1, expected_gain=float(gain),
                cost_est=float(cfg.descent_fanout * 4), urgency=0.5,
                well_targeted=True))
            qid += 1
        qs.sort(key=lambda q: -(q.expected_gain / max(1.0, q.cost_est)))
        # The question budget scales with the number of entities the triage
        # actually flags, not with a constant: a 50k-person enterprise has ~30x
        # the candidate entities of a 2k one, and a fixed budget of 220 was
        # measured to push every gold anchor out of the queue at 50k.
        nq = min(cfg.max_questions,
                 max(cfg.n_questions, int(cfg.question_frac * len(qs))))
        qs = qs[:nq]
        h.meter.add("L5-questions", kernel_tier,
                    len(pool) * TOK_PER_KO + TOK_PROMPT_OVERHEAD,
                    len(qs) * TOK_PER_QUESTION, calls=1)
        new_kos: List[KO] = []
        for q in qs:
            got = h.descend(q.anchor, q.target_preds,
                            budget_nodes=cfg.descent_fanout,
                            start_nodes=h.triage_sites.get(q.anchor),
                            avoid=[h.triage_home[q.anchor]]
                            if q.anchor in h.triage_home else None)
            q.answered = True
            q.n_new_evidence = len(got)
            new_kos.extend(got)
        h.questions = qs
        if not new_kos:
            return hyps, pool
        merged = pool + new_kos
        if cfg.restrict_synthesis_to_triage:
            # The triage is the candidate generator.  Proposing a causal chain
            # for every entity the kernel happens to hold an object about
            # produces thousands of candidates built out of one site's routine
            # traffic, and buries the real ones in the ranking.
            keep_anchors = {q.anchor for q in qs}
            keep_anchors |= {k.anchor for k in pool if k.importance > cfg.pin_importance}
            merged = [k for k in merged if k.anchor in keep_anchors]
        h.meter.add("L5-kernel-q", kernel_tier,
                    len(merged) * TOK_PER_KO + TOK_PROMPT_OVERHEAD, 0, calls=1)
        hyps2 = synthesize(merged, kernel_tier, self.rng, self.c.org,
                           use_lineage=cfg.lineage, use_dedup=cfg.independence,
                           use_temporal=cfg.temporal,
                           n_entities=len(self.c.entities),
                           max_reports=cfg.max_reports,
                           stem_rep=self._stem if hasattr(self,'_stem') else None)
        h.meter.add("L5-kernel-q", kernel_tier, 0, len(hyps2) * TOK_PER_HYP,
                    calls=0)
        if cfg.triage_prior_weight > 0.0 and h.triage_gain:
            # Combine the cheap structural prior (what the sketches say about
            # this entity) with the expensive verification (what the kernel
            # concluded from the evidence).  Neither alone ranks well: the
            # prior cannot tell a real chain from a decoy, and the verifier
            # cannot tell an entity worth looking at from one that is just
            # noisy.
            gs = np.array([h.triage_gain.get(x.anchor, 0.0) for x in hyps2])
            if len(gs) > 2 and gs.std() > 1e-9:
                z = (gs - gs.mean()) / gs.std()
                for x, zz in zip(hyps2, z):
                    x.prior = float(zz)
                    lo = math.log(max(1e-6, x.conf) / max(1e-6, 1 - x.conf))
                    x.conf = float(1.0 / (1.0 + math.exp(
                        -(lo + cfg.triage_prior_weight * zz))))
                hyps2.sort(key=lambda x: -x.conf)
        after = {self._hkey(x): x for x in hyps2}
        for q in qs:
            for key, hy in after.items():
                if hy.anchor != q.anchor:
                    continue
                old = before.get(key)
                if old is None:
                    q.changed_conclusion = True
                elif abs(old.conf - hy.conf) > 0.05 or \
                        set(old.preds) != set(hy.preds):
                    q.changed_conclusion = True
                if old is not None and old.contra > 0 and hy.contra == 0:
                    q.resolved_contradiction = True
        return hyps2, merged

    def _completion_round(self, hyps: List[Hypothesis], pool: List[KO],
                          kernel_tier: Tier) -> Tuple[List[Hypothesis], List[KO]]:
        """Once two links of a chain are established for an entity, go looking
        for the MISSING links specifically.

        The triage has to keep a high evidence bar (a predicate seen at least
        `sketch_min_support` times at a foreign site) or the candidate list
        explodes.  That bar is exactly what hides a rare facet carried by one
        or two people.  Lowering it globally is unaffordable; lowering it for
        the handful of entities that already show a partial chain is cheap,
        and it is where the rare-signal recall lives.
        """
        cfg = self.cfg
        h = self.h
        targets: List[Tuple[int, List[int]]] = []
        seen: Set[int] = set()
        for hy in sorted(hyps, key=lambda x: -x.conf):
            if hy.hallucinated or hy.chain < 0 or len(hy.preds) < 2:
                continue
            if hy.anchor in seen:
                continue
            ch = [PRED_ID[p] for p in CAUSAL_CHAINS[hy.chain]]
            missing = [p for p in ch if p not in hy.preds]
            if not missing:
                continue
            seen.add(hy.anchor)
            targets.append((hy.anchor, missing))
            if len(targets) >= cfg.completion_budget:
                break
        if not targets:
            return hyps, pool
        new_kos: List[KO] = []
        for anchor, missing in targets:
            got = h.descend(anchor, missing, budget_nodes=cfg.descent_fanout + 2,
                            start_nodes=h.triage_sites.get(anchor),
                            avoid=[h.triage_home[anchor]]
                            if anchor in h.triage_home else None)
            new_kos.extend(got)
        if not new_kos:
            return hyps, pool
        merged = pool + new_kos
        keep = {a for a, _ in targets} | {k.anchor for k in pool
                                          if k.importance > cfg.pin_importance}
        if cfg.restrict_synthesis_to_triage:
            merged = [k for k in merged if k.anchor in keep or
                      k.anchor in {x.anchor for x in hyps}]
        h.meter.add("L5-kernel-complete", kernel_tier,
                    len(merged) * TOK_PER_KO + TOK_PROMPT_OVERHEAD, 0, calls=1)
        hyps2 = synthesize(merged, kernel_tier, self.rng, self.c.org,
                           use_lineage=cfg.lineage, use_dedup=cfg.independence,
                           use_temporal=cfg.temporal,
                           n_entities=len(self.c.entities),
                           max_reports=cfg.max_reports,
                           stem_rep=self._stem)
        h.meter.add("L5-kernel-complete", kernel_tier, 0,
                    len(hyps2) * TOK_PER_HYP, calls=0)
        h.completion_targets = len(targets)
        return hyps2, merged

    @staticmethod
    def _hkey(h: Hypothesis) -> Tuple[int, Tuple[int, ...]]:
        return (h.anchor, tuple(sorted(h.preds)))

    def _families(self, hyps: List[Hypothesis],
                  tier: Tier) -> List[List[int]]:
        by_chain: Dict[int, List[int]] = {}
        for i, hy in enumerate(hyps):
            if hy.chain >= 0 and not hy.hallucinated:
                by_chain.setdefault(hy.chain, []).append(i)
        fams = []
        for ch, ids in by_chain.items():
            anchors = {hyps[i].anchor for i in ids}
            if len(ids) >= 2 and len(anchors) >= 2:
                if self.rng.random() < tier.causal_check:
                    fams.append(ids)
        if fams:
            self.h.meter.add("L5-families", tier,
                             len(hyps) * TOK_PER_HYP + TOK_PROMPT_OVERHEAD,
                             len(fams) * 30, calls=1)
        return fams


# ---------------------------------------------------------------------------
# Centralised baselines
# ---------------------------------------------------------------------------

def _lexical_index(corpus: Corpus) -> Dict[int, np.ndarray]:
    """Inverted index restricted to the predicate surface forms - the strongest
    cheap retrieval signal available without an LLM."""
    idx: Dict[int, np.ndarray] = {}
    order = np.argsort(corpus.recs["pred"], kind="stable")
    preds = corpus.recs["pred"][order]
    uniq, starts, counts = np.unique(preds, return_index=True, return_counts=True)
    for u, s, ct in zip(uniq, starts, counts):
        idx[int(u)] = order[s:s + ct]
    return idx


def flat_rag(corpus: Corpus, kernel_tier: Tier, seed: int,
             token_budget: int, near_miss=None,
             expand_schema: bool = True) -> RunResult:
    """BASELINE A. Lexical retrieval over raw text, one kernel call.

    An open-ended discovery question has no useful query terms, so the
    strongest fair formulation is schema-aware expansion: retrieve records
    whose surface form matches ANY causal-chain predicate, then take a
    random/recency sample up to the kernel's token budget.  (The global
    novelty ranking used by map_reduce is deliberately NOT given here - that
    requires the extraction pass that map_reduce pays for.)
    """
    rng = np.random.default_rng(31_000 + seed)
    meter = Meter()
    idx = _lexical_index(corpus)
    cand = np.concatenate([idx[p] for p in range(N_CAUSAL_PRED) if p in idx]) \
        if expand_schema else np.arange(len(corpus.recs))
    n_read = min(len(cand), max(1, token_budget // TOK_PER_RECORD))
    sel = rng.choice(cand, size=n_read, replace=False)
    ex = extract(corpus, sel, kernel_tier, rng, near_miss=near_miss)
    meter.add("retrieve", kernel_tier, 0, 0, calls=0)
    meter.add("L5-kernel", kernel_tier,
              int(n_read * TOK_PER_RECORD + TOK_PROMPT_OVERHEAD),
              int(len(ex) * 10), calls=1)
    kos = _flat_kos(corpus, ex, keep_lineage=True)
    hyps = synthesize(kos, kernel_tier, rng, corpus.org,
                      n_entities=len(corpus.entities),
                      stem_rep=stem_rep_map(corpus),
                      max_reports=int(min(6000, max(600, len(corpus.entities)))))
    meter.add("L5-kernel", kernel_tier, 0, len(hyps) * TOK_PER_HYP, calls=0)
    return RunResult(name="flat_rag", hypotheses=hyps, meter=meter,
                     retained={(int(k.pred), int(k.anchor)) for k in kos},
                     kernel_kos=kos, propagated_records=n_read,
                     exposed_raw_records=n_read,
                     claims_leaving_node=0,
                     notes={"records_read": int(n_read)})


def long_context(corpus: Corpus, kernel_tier: Tier, seed: int,
                 near_miss=None) -> RunResult:
    """BASELINE B. Fill the kernel's entire context window with raw records,
    with NO retrieval step at all - an unfiltered sample of the enterprise.
    The difference from baseline A is exactly the value of the cheap lexical
    prefilter, which is what a RAG stage contributes."""
    budget = kernel_tier.ctx - TOK_PROMPT_OVERHEAD
    return _relabel(flat_rag(corpus, kernel_tier, seed, budget,
                             near_miss=near_miss, expand_schema=False),
                    "long_context")


def _relabel(r: RunResult, name: str) -> RunResult:
    r.name = name
    return r


def _flat_kos(corpus: Corpus, ex: ExtractResult, keep_lineage: bool) -> List[KO]:
    org = corpus.org
    out: Dict[Tuple[int, int], KO] = {}
    for i in range(len(ex)):
        pred = int(ex.pred[i]); anchor = int(ex.anchor[i]); u = int(ex.uid[i])
        k = out.get((pred, anchor))
        if k is None:
            br: Dict[int, Set[int]] = {}
            if keep_lineage:
                for lvl in (TEAM, DEPT, SITE, REGION):
                    a = org.ancestor_at(u, lvl)
                    if a is not None:
                        br[lvl] = {a}
            out[(pred, anchor)] = KO(
                pred=pred, anchor=anchor, tmin=int(ex.t[i]), tmax=int(ex.t[i]),
                polarity=int(ex.polarity[i]), n_raw=1, sigs={int(ex.sig[i])},
                evidence=[int(ex.rid[i])], branches=br, lineage=(u,),
                owner=org.root, level=ENT, conf=0.55, importance=0.5,
                novelty=0.5, contra=0, revisions=0, q_tag=-1,
                origin_users={u} if keep_lineage else set(),
                pos_tmax=int(ex.t[i]) if ex.polarity[i] > 0 else -1,
                neg_tmax=int(ex.t[i]) if ex.polarity[i] < 0 else -1)
        else:
            k.tmin = min(k.tmin, int(ex.t[i])); k.tmax = max(k.tmax, int(ex.t[i]))
            k.n_raw += 1
            if len(k.sigs) < MAX_SIGS:
                k.sigs.add(int(ex.sig[i]))
            if len(k.evidence) < MAX_EVIDENCE:
                k.evidence.append(int(ex.rid[i]))
            if keep_lineage:
                for lvl in (TEAM, DEPT, SITE, REGION):
                    a = org.ancestor_at(u, lvl)
                    if a is not None:
                        k.branches.setdefault(lvl, set()).add(a)
                if len(k.origin_users) < MAX_USERS:
                    k.origin_users.add(u)
            if k.polarity != int(ex.polarity[i]):
                k.contra += 1
            if ex.polarity[i] > 0:
                k.pos_tmax = max(k.pos_tmax, int(ex.t[i]))
            else:
                k.neg_tmax = max(k.neg_tmax, int(ex.t[i]))
    return list(out.values())


def map_reduce(corpus: Corpus, alloc: List[Tier], seed: int,
               kernel_ko_budget: int, ul: Optional[UserLayer] = None,
               near_miss=None, keep_lineage: bool = True) -> RunResult:
    """BASELINE B2 - the strongest centralised competitor.

    Pays exactly the same bottom-layer extraction cost as the hierarchy, then
    pools every claim centrally, ranks by GLOBAL importance (which the
    hierarchy cannot compute, because its nodes only see their own subtree),
    and hands the top of that ranking to one strong kernel call.
    No intermediate aggregation, no lineage compression, no questioning.
    """
    rng = np.random.default_rng(41_000 + seed)
    meter = Meter()
    ul = ul if ul is not None else user_extract(corpus, alloc[USER], rng,
                                                near_miss=near_miss)
    meter.add("L0-extract", alloc[USER],
              int(len(corpus.recs) * TOK_PER_RECORD
                  + len(corpus.org.user_ids) * TOK_PROMPT_OVERHEAD),
              int(len(ul.ex) * 10), calls=len(corpus.org.user_ids))
    kernel_tier = alloc[ENT]
    ex = ul.ex
    score = ul.imp + rng.normal(0.0, kernel_tier.salience_noise, len(ul.imp))
    # global rank, then merge the winners into KOs
    take = min(len(ex), kernel_ko_budget * 12)
    sel = np.argsort(-score)[:take]
    kos = _flat_kos(corpus, ex.take(sel), keep_lineage=keep_lineage)
    kos = sorted(kos, key=lambda k: -(k.novelty + 0.1 * k.n_raw))[:kernel_ko_budget]
    meter.add("L5-kernel", kernel_tier,
              len(kos) * TOK_PER_KO + TOK_PROMPT_OVERHEAD, 0, calls=1)
    hyps = synthesize(kos, kernel_tier, rng, corpus.org,
                      use_lineage=keep_lineage, n_entities=len(corpus.entities),
                      stem_rep=stem_rep_map(corpus),
                      max_reports=int(min(6000, max(600, len(corpus.entities)))))
    meter.add("L5-kernel", kernel_tier, 0, len(hyps) * TOK_PER_HYP, calls=0)
    return RunResult(name="map_reduce", hypotheses=hyps, meter=meter,
                     retained={(k.pred, k.anchor) for k in kos},
                     kernel_kos=kos,
                     propagated_records=int(len(ex)),
                     exposed_raw_records=0,
                     claims_leaving_node=int(len(ex)),
                     notes={"pooled_claims": int(len(ex))})


def recursive_summary(corpus: Corpus, alloc: List[Tier], seed: int,
                      compression: float = 0.25, ul=None,
                      near_miss=None) -> RunResult:
    """BASELINE C. Text-level recursive summarisation: each level keeps a
    fixed fraction of what it received, with no structure, no lineage, no
    independence tracking.  Implemented on the same claim substrate so the
    only difference from D..H is the aggregation policy."""
    cfg = HierConfig(budgets=(4, 10, 20, 35, 60), lineage=False, independence=False,
                     contradiction=False, temporal=False, adaptive_routing=False,
                     questions=False, downward_retrieval=False,
                     cross_links=False, adaptive_abstraction=False,
                     sketch_channel=False, max_kernel_kos=200)
    r = HierRunner(corpus, alloc, cfg, seed=seed, ul=ul,
                   near_miss=near_miss).run()
    r.name = "recursive_summary"
    return r


# ---------------------------------------------------------------------------
# Reference controls (not deployable systems - they bound the problem)
# ---------------------------------------------------------------------------

def oracle_retrieval(corpus: Corpus, alloc: List[Tier], seed: int,
                     ul: Optional[UserLayer] = None, near_miss=None,
                     extraction_tier: Optional[Tier] = None) -> RunResult:
    """UPPER BOUND. Every extracted claim in the enterprise, no propagation
    budget, no retrieval budget, same reasoning operators, strongest kernel.
    Not a deployable architecture: it assumes the kernel can hold the entire
    claim pool.  It exists to tell us whether a given result is limited by
    retrieval or by reasoning."""
    rng = np.random.default_rng(77_000 + seed)
    meter = Meter()
    tier = extraction_tier or alloc[USER]
    ul = ul if ul is not None else user_extract(corpus, tier, rng,
                                                near_miss=near_miss)
    meter.add("L0-extract", tier,
              int(len(corpus.recs) * TOK_PER_RECORD
                  + len(corpus.org.user_ids) * TOK_PROMPT_OVERHEAD),
              int(len(ul.ex) * 10), calls=len(corpus.org.user_ids))
    kos = _flat_kos(corpus, ul.ex, keep_lineage=True)
    kt = alloc[ENT]
    meter.add("L5-kernel", kt, len(kos) * TOK_PER_KO + TOK_PROMPT_OVERHEAD, 0,
              calls=1)
    hyps = synthesize(kos, kt, rng, corpus.org, n_entities=len(corpus.entities),
                      stem_rep=stem_rep_map(corpus),
                      max_reports=int(min(6000, max(600, len(corpus.entities)))))
    meter.add("L5-kernel", kt, 0, len(hyps) * TOK_PER_HYP, calls=0)
    return RunResult(name="oracle_retrieval", hypotheses=hyps, meter=meter,
                     retained={(k.pred, k.anchor) for k in kos}, kernel_kos=kos,
                     propagated_records=int(len(ul.ex)),
                     exposed_raw_records=0,
                     claims_leaving_node=int(len(ul.ex)))


def random_rank(res: RunResult, seed: int) -> RunResult:
    """CONTROL. The same candidate hypotheses, ranked at random.  The gap
    between a system and its own random-rank control is the part of its score
    that comes from judging evidence rather than from generating candidates."""
    rng = np.random.default_rng(88_000 + seed)
    hy = list(res.hypotheses)
    rng.shuffle(hy)
    return RunResult(name=res.name + "_randrank", hypotheses=hy,
                     meter=res.meter, retained=res.retained,
                     kernel_kos=res.kernel_kos, questions=res.questions,
                     families=res.families, notes=res.notes,
                     propagated_records=res.propagated_records,
                     exposed_raw_records=res.exposed_raw_records,
                     claims_leaving_node=res.claims_leaving_node,
                     sketch_entries_leaving_node=res.sketch_entries_leaving_node)


def central_triage(corpus: Corpus, alloc: List[Tier], seed: int,
                   ul: Optional[UserLayer] = None, near_miss=None,
                   sketch_min_support: int = 2, foreign_share: float = 0.35,
                   max_anchors: int = 6000,
                   kernel_ko_cap: int = 0) -> RunResult:
    """CONTROL B4 - the centralised twin of the hierarchy's own algorithm.

    It runs EXACTLY the same entity-level relational triage the hierarchy runs,
    but centrally: one claim pool, no propagation budget, no sketch cap, no
    routing, no descent (it does not need one - every claim is already local
    to it), no lineage compression.  It is therefore strictly better informed
    than the hierarchy at every step.

    The point of this control is to separate two things that are easy to
    conflate: the value of the *triage algorithm* and the value of the
    *hierarchy*.  Whatever the hierarchy scores above a pure-propagation
    baseline but below this one is attributable to the algorithm, not to the
    org structure.  What the hierarchy buys over this control has to be found
    in cost, privacy, latency and robustness - not in accuracy.
    """
    rng = np.random.default_rng(55_000 + seed)
    meter = Meter()
    org = corpus.org
    tier = alloc[USER]
    ul = ul if ul is not None else user_extract(corpus, tier, rng,
                                                near_miss=near_miss)
    meter.add("L0-extract", tier,
              int(len(corpus.recs) * TOK_PER_RECORD
                  + len(org.user_ids) * TOK_PROMPT_OVERHEAD),
              int(len(ul.ex) * 10), calls=len(org.user_ids))
    ex = ul.ex
    site_of = np.array([org.ancestor_at(int(u), SITE) or -1 for u in ex.uid],
                       dtype=np.int64)
    reg_of_site = {st: org.ancestor_at(st, REGION) for st in org.levels[SITE]}
    n_ent = len(corpus.entities)

    # per (site, entity): total mentions, and per-predicate counts for the
    # operational predicates -> support-thresholded bitmask
    key = site_of * (n_ent * 64) + ex.anchor.astype(np.int64) * 64 \
        + np.minimum(ex.pred.astype(np.int64), 63)
    uk, cnt = np.unique(key, return_counts=True)
    sk_site = uk // (n_ent * 64)
    sk_ent = (uk % (n_ent * 64)) // 64
    sk_pred = uk % 64
    mask: Dict[Tuple[int, int], int] = {}
    tot: Dict[Tuple[int, int], int] = {}
    ent_tot: Dict[int, int] = {}
    for s_, e_, p_, c_ in zip(sk_site.tolist(), sk_ent.tolist(),
                              sk_pred.tolist(), cnt.tolist()):
        tot[(s_, e_)] = tot.get((s_, e_), 0) + c_
        ent_tot[e_] = ent_tot.get(e_, 0) + c_
        if p_ < 34 and c_ >= sketch_min_support:
            mask[(s_, e_)] = mask.get((s_, e_), 0) | (1 << p_)
    per_ent: Dict[int, List[Tuple[int, int, int]]] = {}
    for (s_, e_), m_ in mask.items():
        per_ent.setdefault(e_, []).append((s_, reg_of_site.get(s_, -1), m_))
    kt = alloc[ENT]
    meter.add("L5-triage", kt, len(mask) * 5 + TOK_PROMPT_OVERHEAD, 0, calls=1)

    cands: List[Tuple[float, int, List[int]]] = []
    for e_, rows in per_ent.items():
        total = max(1, ent_tot.get(e_, 1))
        foreign = [(s_, r_, m_) for s_, r_, m_ in rows
                   if tot[(s_, e_)] / total <= foreign_share]
        if len(foreign) < 2:
            continue
        regs = {r_ for _, r_, _ in foreign}
        if len(regs) < 2:
            continue
        um = 0
        for _, _, m_ in foreign:
            um |= m_
        span, _ = _chain_span(um)
        if span < 2:
            continue
        gain = (0.6 * span + 0.35 * len(regs)) / (1.0 + 0.25 * math.log1p(total))
        cands.append((gain, int(e_), [s_ for s_, _, _ in foreign]))
    cands.sort(reverse=True)
    cands = cands[:max_anchors]

    keep_ent = {e for _, e, _ in cands}
    home_of = {}
    for _, e_, _ in cands:
        rows = [(tot[(s_, e_)], s_) for s_, _, _ in per_ent[e_]]
        home_of[e_] = max(rows)[1] if rows else -1
    sel = np.nonzero(np.isin(ex.anchor, np.array(sorted(keep_ent) or [-1])))[0]
    if len(sel):
        home_arr = np.array([home_of.get(int(a), -1) for a in ex.anchor[sel]])
        sel = sel[site_of[sel] != home_arr]
    kos = _flat_kos(corpus, ex.take(sel), keep_lineage=True) if len(sel) else []
    if kernel_ko_cap and len(kos) > kernel_ko_cap:
        # Give this control the same evidence-selection discipline the
        # hierarchy is forced into by its propagation budget.  Without it the
        # comparison would credit the hierarchy for a filtering effect the
        # centralised version was simply never allowed to apply.
        kos.sort(key=lambda k: -(k.novelty + 0.35 * math.log1p(k.n_raw)))
        kos = kos[:kernel_ko_cap]
    meter.add("L5-kernel", kt, len(kos) * TOK_PER_KO + TOK_PROMPT_OVERHEAD, 0,
              calls=1)
    mr = int(min(6000, max(600, len(corpus.entities))))
    hyps = synthesize(kos, kt, rng, org, n_entities=len(corpus.entities),
                      max_reports=mr, stem_rep=stem_rep_map(corpus))
    meter.add("L5-kernel", kt, 0, len(hyps) * TOK_PER_HYP, calls=0)
    return RunResult(name="central_triage", hypotheses=hyps, meter=meter,
                     retained={(k.pred, k.anchor) for k in kos}, kernel_kos=kos,
                     propagated_records=int(len(ex)),
                     exposed_raw_records=0,
                     claims_leaving_node=int(len(ex)),
                     notes={"triage_candidates": len(cands),
                            "sketch_entries": len(mask)})
