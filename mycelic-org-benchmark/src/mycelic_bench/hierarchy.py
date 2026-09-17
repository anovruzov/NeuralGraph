"""Hierarchical aggregation engine (Mycelic and its lineage-blind ablations).

Each `UnitNode` holds a pooled `Sketch` of everything below it, runs the
shared hypothesis search at its own scope, merges/forwards child claims with
lineage, opens conflicts from cross-source inconsistency, revises stale
claims from a recent-window test, answers questions from below/above, and
promotes a compressed delta to its parent.

Nothing in this module reads ground truth (honesty rule 1.1).  The only
truth-adjacent field is `origin_attack` on claims and `is_attack` on
observation batches, which are carried *for evaluation* and never used in a
decision unless the configured detector is the profile-based content
classifier (an explicit assumption, see DESIGN.md §7/§9).
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .agents import ObservationBatch, SimulatedSLM, make_batches
from .hypothesis import Candidates, null_evidence, rate_test, search
from .schemas import Claim, Conflict, LineageRecord, QuestionArtifact, SupportRecord, stable_hash
from .sketch import COL_DD, COL_DR, COL_DT, COL_DW, COL_K0, COL_N, N_COLS, Sketch
from .vocab import N_LABELS, cell_index, mask_matrix

LAYER_ORDER = ("worker", "squad", "team", "department", "division", "region", "executive")
_LAYER_RANK = {l: i for i, l in enumerate(LAYER_ORDER)}
# distinct-source columns of a sketch and the organisational layer each one counts (position in unit_membership)
_DISTINCT_COLS = ((COL_DT, "team", 0), (COL_DD, "department", 1), (COL_DR, "region", 2))


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------
@dataclass
class Policy:
    sketch_order: int = 3
    compression: str = "order_capped"   # full_sketch | order_capped | order2_plus_significant | significant_cells_only | claims_only
    top_k_claims: int = 200
    byte_budget: int = 0
    min_cell_n: int = 3
    k_anonymity: int = 3
    quote_policy: str = "none"
    fdr_q: float = 0.05
    n_min: int = 8
    effect_min: float = 0.08
    support_min: float = 2.0
    lineage: bool = True
    questioning: str = "none"
    question_budget: int = 20
    question_byte_budget: int = 4096
    recent_window: int = 6
    cadence: dict = field(default_factory=lambda: {"team": 1, "department": 1, "region": 1, "executive": 1})
    fan_in_budget_bytes: int = 0
    resolve_ratio: float = 2.0
    max_order_questions: int = 4
    retain_superseded: bool = True
    consistency_z: float = 3.0
    rho_team: float = 0.25
    rho_department: float = 0.7
    rho_region: float = 0.9
    detector: str = "none"
    local_slm_classifier: bool = False
    max_roots_tracked: int = 64
    forward_inherited: bool = True

    @staticmethod
    def from_cfg(cfg: dict[str, Any], **overrides: Any) -> "Policy":
        base = dict(cfg.get("policy", {}))
        sec = cfg.get("security", {})
        base.setdefault("consistency_z", sec.get("consistency_z", 3.0))
        base.setdefault("rho_team", sec.get("rho_team", 0.25))
        base.setdefault("rho_department", sec.get("rho_department", 0.7))
        base.setdefault("rho_region", sec.get("rho_region", 0.9))
        base.setdefault("max_roots_tracked", sec.get("max_roots_tracked", 64))
        base.setdefault("detector", sec.get("detector", "none"))
        base.setdefault("support_min", sec.get("min_independent_support", base.get("support_min", 2.0)))
        base.update({k: v for k, v in overrides.items() if v is not None})
        fields = set(Policy.__dataclass_fields__)
        return Policy(**{k: v for k, v in base.items() if k in fields})


@dataclass
class Artifact:
    sender_id: str
    layer: str
    round: int
    sketch: Sketch
    claims: list[Claim]
    answers: list[tuple[str, Sketch]] = field(default_factory=list)   # (question_id, one-cell sketch)
    signature_ok: bool = True

    def wire_bytes(self) -> int:
        b = 24 + self.sketch.wire_bytes() + sum(c.wire_bytes() for c in self.claims)
        b += sum(16 + s.wire_bytes() for _, s in self.answers)
        return b


# --------------------------------------------------------------------------
# Unit node
# --------------------------------------------------------------------------
class UnitNode:
    def __init__(self, unit_id: str, layer: str, parent_id: str | None, children: list[str], policy: Policy,
                 hier: "Hierarchy") -> None:
        self.unit_id = unit_id
        self.layer = layer
        self.parent_id = parent_id
        self.children = children
        self.policy = policy
        self.hier = hier
        self._cumulative = Sketch(unit_id, layer, 0, max_order=policy.sketch_order)
        self._cum_dirty = False          # leaf store changed since the last rebuild (rebuilt lazily on read)
        self._cum_round = 0
        self._distinct_dirty = False     # children's sketches pooled since the distinct-source columns were fixed
        ln = hier.layer_names
        self.child_layer = ln[ln.index(layer) - 1] if layer in ln and ln.index(layer) > 0 else None
        self.sent = Sketch(unit_id, layer, 0, max_order=policy.sketch_order)
        self.recent: OrderedDict[int, Sketch] = OrderedDict()
        self.received_from: dict[str, Sketch] = {}
        self.received_claims: dict[str, dict[tuple, Claim]] = {}
        self.claims: dict[tuple, Claim] = {}          # key: (cell, label, sign, scope_unit)
        self.conflicts: dict[tuple, Conflict] = {}
        self.quarantined: list[Claim] = []
        self.questions: dict[str, QuestionArtifact] = {}
        self.pending_answers: list[tuple[str, Sketch]] = []
        self.key = hashlib.sha256(f"key|{hier.seed}|{unit_id}".encode()).digest()
        self.available = True
        self.inbox: deque = deque()
        # team-level retained observations (local storage; never leave the node)
        self.obs_attrs: list[np.ndarray] = []
        self.obs_labels: list[np.ndarray] = []
        self.obs_present: list[np.ndarray] = []
        self.obs_worker: list[np.ndarray] = []
        self.obs_fp: list[np.ndarray] = []
        self.obs_idx: list[np.ndarray] = []
        self.obs_round: list[np.ndarray] = []
        self.seen_fingerprints: set[int] = set()
        self.sent_claim_version: dict[str, tuple[int, str]] = {}
        self._answer_cells: Sketch | None = None
        # accounting
        self.bytes_in = 0
        self.bytes_out = 0
        self.bytes_dropped_fan_in = 0
        self.tokens = 0
        self.model_calls = 0
        self.latency_ms = 0.0
        self.quotes_received: list[str] = []
        self.n_records_in = 0
        self.last_synth_round = -1

    # ---- pooled sketch (lazy rebuild for leaf nodes) ---------------------
    @property
    def cumulative(self) -> Sketch:
        """Pooled sketch of everything below this node.  A leaf node rebuilds it from its local store on the
        first read after new observations arrived (once per round instead of once per worker batch: the flat
        topology's single leaf receives every team's batch, which made the rebuild quadratic)."""
        if self._cum_dirty:
            self._rebuild_cumulative(self._cum_round)
        if self._distinct_dirty:
            self._cumulative = self._fix_distinct(self._cumulative)
            self._distinct_dirty = False
        return self._cumulative

    @cumulative.setter
    def cumulative(self, sk: Sketch) -> None:
        self._cumulative = sk
        self._cum_dirty = False

    # ---- distinct-source columns after pooling ---------------------------
    def _distinct_units(self, ids: np.ndarray, pos: int, children: list[str] | None = None) -> np.ndarray:
        """Number of distinct organisational units (team / department / region by `pos`) among the children
        (default: every child in `received_from`) whose sketch holds each cell of `ids`."""
        mem = self.hier.unit_membership
        parts_c, parts_u = [], []
        for cid in (children if children is not None else list(self.received_from)):
            sk = self.received_from.get(cid)
            if sk is None or len(sk.ids) == 0:
                continue
            u = mem.get(cid, (-1, -1, -1))[pos]
            held = sk.ids[sk.counts[:, COL_N] > 0]
            parts_c.append(held); parts_u.append(np.full(len(held), u, dtype=np.int64))
        out = np.zeros(len(ids), dtype=np.int64)
        if not parts_c or len(ids) == 0:
            return out
        cells = np.concatenate(parts_c); units = np.concatenate(parts_u)
        pairs = np.unique(cells.astype(np.int64) * (int(units.max()) + 2) + (units + 1))
        pc = pairs // (int(units.max()) + 2)
        uc, cnt = np.unique(pc, return_counts=True)
        pos_ = np.clip(np.searchsorted(uc, ids), 0, max(len(uc) - 1, 0))
        hit = uc[pos_] == ids
        out[hit] = cnt[pos_[hit]]
        return out

    def _fix_distinct(self, sk: Sketch, children: list[str] | None = None) -> Sketch:
        """Pooling children's sketches adds their distinct-source columns, which is exact only for the layers at
        or below the child layer (those units are disjoint across children).  A column for this node's layer or
        above is 1 for every cell with n > 0 (everything pooled here lies in one such unit); a column for a
        layer the topology skips (2-/3-layer ablations) counts distinct units among the children holding the
        cell.  Leaf sketches are rebuilt from the local store and never need this."""
        if self.child_layer is None or len(sk.ids) == 0:
            return sk
        my_rank, child_rank = _LAYER_RANK[self.layer], _LAYER_RANK[self.child_layer]
        counts = None
        for col, layer_x, pos in _DISTINCT_COLS:
            rx = _LAYER_RANK[layer_x]
            if rx <= child_rank:
                continue
            if counts is None:
                counts = sk.counts.copy()
            if rx >= my_rank:
                counts[:, col] = (counts[:, COL_N] > 0).astype(np.int64)
            else:
                counts[:, col] = np.minimum(self._distinct_units(sk.ids, pos, children), np.maximum(counts[:, COL_N], 1))
        if counts is None:
            return sk
        return Sketch(sk.producer_id, sk.layer, sk.round, sk.ids, counts, sk.max_order)

    def _pooled_distinct(self, rows: dict[str, np.ndarray], child_ids: list[str], cell: int) -> tuple[int, int, int, int]:
        """(dw, dt, dd, dr) of one cell pooled over the given children (same rules as `_fix_distinct`)."""
        n = sum(int(rows[i][COL_N]) for i in child_ids)
        out = [sum(int(rows[i][COL_DW]) for i in child_ids)]
        my_rank = _LAYER_RANK[self.layer]
        child_rank = _LAYER_RANK[self.child_layer] if self.child_layer is not None else my_rank - 1
        for col, layer_x, pos in _DISTINCT_COLS:
            rx = _LAYER_RANK[layer_x]
            if rx <= child_rank:
                out.append(sum(int(rows[i][COL_DT + pos]) for i in child_ids))
            elif rx >= my_rank:
                out.append(1 if n > 0 else 0)
            else:
                held = [i for i in child_ids if int(rows[i][COL_N]) > 0]
                out.append(int(self._distinct_units(np.array([cell], dtype=np.int64), pos, held)[0]) if held else 0)
        return out[0], out[1], out[2], out[3]

    # ---- helpers ---------------------------------------------------------
    def _independent_support(self, dw: int, dt: int, dd: int, dr: int, n: int) -> float:
        p = self.policy
        if not p.lineage:
            return float(n)   # replica count
        dr, dd, dt, dw = max(dr, 1 if n else 0), max(dd, 1 if n else 0), max(dt, 1 if n else 0), max(dw, 1 if n else 0)
        return float(dr + p.rho_region * (dd - dr) + p.rho_department * (dt - dd) + p.rho_team * (dw - dt))

    def _claim_id(self, cell: int, label: int, sign: int, round_: int) -> str:
        return f"{self.unit_id}:{cell}:{label}:{'+' if sign > 0 else '-'}:{round_}"

    def _worker_backing(self, worker: int, cell: int) -> int:
        if worker < 0 or not self.obs_attrs:
            return 0
        ci = cell_index()
        attrs = np.concatenate(self.obs_attrs); w = np.concatenate(self.obs_worker)
        m = w == worker
        for a, v in ci.decode(int(cell)):
            m &= attrs[:, a] == v
        return int(m.sum())

    def _team_roots(self, cell: int) -> list[str]:
        """Hashed worker ids whose retained observations match the cell (bounded)."""
        if not self.obs_attrs:
            return []
        ci = cell_index()
        attrs = np.concatenate(self.obs_attrs); w = np.concatenate(self.obs_worker)
        m = np.ones(len(attrs), dtype=bool)
        for a, v in ci.decode(int(cell)):
            m &= attrs[:, a] == v
        ws = np.unique(w[m])[: self.policy.max_roots_tracked]
        return [f"W{int(x):06d}" for x in ws]

    def child_cell_counts(self, cell: int) -> dict[str, np.ndarray]:
        out = {}
        for cid, sk in self.received_from.items():
            c = sk.lookup(np.array([cell]))[0]
            if c[COL_N] > 0:
                out[cid] = c
        return out

    # ---- ingestion -------------------------------------------------------
    def ingest_batch(self, batch: ObservationBatch, round_: int) -> None:
        """Team node receives structured observations from its workers."""
        p = self.policy
        self.bytes_in += batch.wire_bytes()
        self.n_records_in += len(batch)
        self.model_calls += batch.model_calls
        self.tokens += batch.tokens_in
        self.quotes_received.extend(batch.quotes)
        keep = np.ones(len(batch), dtype=bool)
        if p.lineage:
            keep &= batch.signature_ok
            # evidence-hash dedup (copies / replayed evidence count once)
            fps = batch.fingerprint
            uniq, first = np.unique(fps, return_index=True)
            dup = np.ones(len(batch), dtype=bool)
            dup[first] = False
            already = np.array([int(f) in self.seen_fingerprints for f in fps], dtype=bool)
            keep &= ~dup & ~already
            self.seen_fingerprints.update(int(f) for f in uniq)
        sec = self.hier.security
        if sec is not None:
            keep &= sec.filter_batch(batch, self)
        self.hier.trace_batch(self, batch, keep, round_)
        if keep.any():
            org = self.hier.org
            w = batch.worker[keep]
            sk = Sketch.from_interactions(
                self.unit_id, self.layer, round_, batch.attrs[keep], batch.labels[keep], w,
                org.worker_team[w], org.worker_department[w], org.worker_region[w],
                max_order=p.sketch_order, present=batch.present[keep])
            self.recent[round_] = Sketch.pool([self.recent.get(round_, Sketch(self.unit_id, self.layer, round_)), sk],
                                              self.unit_id, self.layer, round_, p.sketch_order)
            self.obs_attrs.append(batch.attrs[keep]); self.obs_labels.append(batch.labels[keep])
            self.obs_present.append(batch.present[keep]); self.obs_worker.append(w)
            self.obs_fp.append(batch.fingerprint[keep]); self.obs_idx.append(batch.idx[keep])
            self.obs_round.append(np.full(int(keep.sum()), round_))
            self._cum_dirty = True
            self._cum_round = round_
        # injected / fabricated claims emitted by compromised devices arrive as bare claims
        for d in batch.injected_claims:
            c = Claim(claim_id=f"W{int(d['worker']):06d}:{d['cell']}:{d['label']}:+:{round_}", producer_id=f"W{int(d['worker']):06d}",
                      layer="worker", cell=int(d["cell"]), label=int(d["label"]), sign=1, n=int(d["n"]), k=int(d["k"]),
                      rate=d["k"] / max(d["n"], 1), baseline_rate=0.0, effect=d["k"] / max(d["n"], 1), p_value=1e-6,
                      q_value=1e-4, confidence=float(d.get("confidence", 0.95)), round_created=round_, round_updated=round_,
                      origin_attack=d.get("attack", "prompt_injection"), status="proposed")
            c.support = SupportRecord(replica_count=1, independent_support=1.0, distinct_workers=1)
            c.lineage = LineageRecord(contributing_units=[c.producer_id], path=[c.producer_id], derivation_operator="observe")
            c.signature = d.get("signature", "")
            self._receive_claim(c.producer_id, c, round_)

    def _rebuild_cumulative(self, round_: int) -> None:
        """Team-level cumulative sketch from all retained observations.  Distinct-source
        columns are exact only when computed over the full local store (a worker
        contributes in many rounds), so the leaf sketch is rebuilt rather than pooled."""
        p = self.policy
        org = self.hier.org
        self._cum_dirty = False
        if not self.obs_attrs:
            self._cumulative = Sketch(self.unit_id, self.layer, round_, max_order=p.sketch_order)
            return
        attrs = np.concatenate(self.obs_attrs); labels = np.concatenate(self.obs_labels)
        present = np.concatenate(self.obs_present); w = np.concatenate(self.obs_worker)
        sk = Sketch.from_interactions(
            self.unit_id, self.layer, round_, attrs, labels, w, org.worker_team[w], org.worker_department[w],
            org.worker_region[w], max_order=p.sketch_order, present=present)
        # question answers of higher order previously absorbed must survive the rebuild
        if self._answer_cells is not None and len(self._answer_cells.ids):
            sk = Sketch.pool([sk, self._answer_cells], self.unit_id, self.layer, round_, p.sketch_order)
        self._cumulative = sk

    def _receive_claim(self, sender: str, c: Claim, round_: int) -> None:
        p = self.policy
        if p.lineage and c.layer == "worker":
            # a bare worker claim must be backed by that worker's own observations in this node's local store
            backed = self._worker_backing(int(c.producer_id[1:]) if c.producer_id[1:].isdigit() else -1, c.cell)
            if backed < 0.5 * max(c.n, 1):
                c.status = "quarantined"; c.quarantine_reason = "unbacked claim (provenance)"
                self.quarantined.append(c); self.hier.trace_quarantine(self, c, round_)
                return
        sec = self.hier.security
        if sec is not None:
            verdict = sec.inspect_claim(c, sender, self)
            if verdict is not None and verdict.flag:
                c.status = "quarantined"; c.quarantine_reason = verdict.reason
                self.quarantined.append(c)
                self.hier.trace_quarantine(self, c, round_)
                return
        key = (c.cell, c.label, c.sign, c.lineage.path[0] if c.lineage.path else sender)
        self.received_claims.setdefault(sender, {})[key] = c
        # update an inherited copy already in the knowledge base (status / counts changed upstream)
        cur = self.claims.get(key)
        if cur is not None and key[3] != self.unit_id and _newer_version(c, cur):
            self.claims[key] = c

    def ingest_artifact(self, art: Artifact, round_: int) -> None:
        p = self.policy
        self.bytes_in += art.wire_bytes()
        if p.lineage and not art.signature_ok:
            self.hier.stats["artifacts_rejected_signature"] += 1
            return
        self.tokens += art.wire_bytes() // 4
        self.model_calls += 1
        if len(art.sketch.ids):
            # pool into the raw sketch; the distinct-source columns are fixed once, on the next read (lazy flag)
            base = self.cumulative if self._cum_dirty else self._cumulative
            self.cumulative = Sketch.pool([base, art.sketch], self.unit_id, self.layer, round_, p.sketch_order)
            self.recent[round_] = Sketch.pool([self.recent.get(round_, Sketch(self.unit_id, self.layer, round_)), art.sketch],
                                              self.unit_id, self.layer, round_, p.sketch_order)
            prev = self.received_from.get(art.sender_id, Sketch(art.sender_id, art.layer, round_))
            self.received_from[art.sender_id] = Sketch.pool([prev, art.sketch], art.sender_id, art.layer, round_, p.sketch_order)
            self._distinct_dirty = True
        for c in art.claims:
            self._receive_claim(art.sender_id, c, round_)
        for qid, sk in art.answers:
            self._absorb_answer(qid, sk, round_)

    # ---- synthesis -------------------------------------------------------
    def synthesize(self, round_: int) -> None:
        p = self.policy
        self._trim_recent(round_)
        max_order = p.sketch_order + (1 if p.questioning != "none" else 0)
        max_order = min(max_order, p.max_order_questions)
        cands = search(self.cumulative, n_min=p.n_min, effect_min=p.effect_min, fdr_q=p.fdr_q, max_order=max_order)
        recent_sk = self._recent_pool(round_)
        recent_c = search(recent_sk, n_min=p.n_min, effect_min=p.effect_min, fdr_q=p.fdr_q, max_order=min(max_order, 2)) \
            if len(recent_sk.ids) else None
        self.tokens += (self.cumulative.wire_bytes() // 16)
        self.model_calls += 1
        # own-scope claims from cumulative evidence
        for i in range(len(cands)):
            self._upsert_own_claim(cands, i, round_, source="cumulative")
        # revival / fresh claims from the recent window
        if recent_c is not None:
            for i in range(len(recent_c)):
                self._upsert_own_claim(recent_c, i, round_, source="recent")
        # temporal revision: refute accepted own claims that the recent window contradicts with power
        self._revise(round_, recent_sk)
        # inherit child claims (scoped) that this node did not synthesize itself
        self._inherit(round_)
        # cross-source consistency -> conflicts (contradictions & poisoning containment)
        self._consistency(round_)
        self.last_synth_round = round_

    def _upsert_own_claim(self, cands: Candidates, i: int, round_: int, source: str) -> None:
        p = self.policy
        cell, label, sign = int(cands.cell[i]), int(cands.label[i]), int(cands.sign[i])
        key = (cell, label, sign, self.unit_id)
        is_ = self._independent_support(int(cands.dw[i]), int(cands.dt[i]), int(cands.dd[i]), int(cands.dr[i]), int(cands.n[i]))
        existing = self.claims.get(key)
        if existing is not None and existing.status == "superseded" and source == "cumulative":
            return   # stale cumulative signal; only a recent-window signal can revive it
        if existing is not None and existing.status != "superseded" and source == "recent":
            return   # the cumulative evidence already carries this claim; window stats must not replace it
        if existing is not None and existing.status in ("rejected", "contested") and source == "cumulative":
            # keep counts fresh but do not silently re-accept a contested claim; consistency decides,
            # except for a claim contested only because a child's claim was: it clears with the child's dispute
            existing.n, existing.k, existing.rate = int(cands.n[i]), int(cands.k[i]), float(cands.rate[i])
            existing.baseline_rate, existing.effect = float(cands.baseline[i]), float(cands.effect[i])
            existing.p_value, existing.q_value, existing.round_updated = float(cands.p[i]), float(cands.q[i]), round_
            if existing.status == "contested" and existing.quarantine_reason == "contested below":
                still = any(c2.status == "contested" and k2[0] == cell and k2[1] == label and k2[2] == sign and k2[3] == sender
                            for sender, ch in self.received_claims.items() for k2, c2 in ch.items())
                if not still:
                    existing.status = "accepted"; existing.quarantine_reason = None
                    if p.lineage:
                        existing.sign_with(self.key)
                    conf = self.conflicts.get(key)
                    if conf is not None and conf.reason == "a child's own-scope claim is contested":
                        conf.status = "resolved"; conf.winner = existing.claim_id
            return
        conf = float((1.0 - cands.q[i]) * (1.0 - math.exp(-is_ / max(p.support_min, 1e-6))))
        contributing = [cid for cid, cnt in self.child_cell_counts(cell).items()]
        if not contributing and self.obs_attrs:
            contributing = self._team_roots(cell)
        parents = [c.claim_id for ch in self.received_claims.values() for k2, c in ch.items()
                   if k2[0] == cell and k2[1] == label and k2[2] == sign and c.status != "quarantined"]
        if existing is None or existing.status == "superseded":
            c = Claim(
                claim_id=self._claim_id(cell, label, sign, round_), producer_id=self.unit_id, layer=self.layer,
                cell=cell, label=label, sign=sign, n=int(cands.n[i]), k=int(cands.k[i]), rate=float(cands.rate[i]),
                baseline_rate=float(cands.baseline[i]), effect=float(cands.effect[i]), p_value=float(cands.p[i]),
                q_value=float(cands.q[i]), confidence=conf, round_created=round_, round_updated=round_,
                valid_from=max(0, round_ - p.recent_window + 1) if source == "recent" else 0,   # window = [r-w+1, r]
                revision_of=existing.claim_id if existing is not None else None,
            )
            c.lineage = LineageRecord(parent_claim_ids=parents, contributing_units=contributing,
                                      path=[self.unit_id], derivation_operator="synthesize" if len(contributing) > 1 else "pool",
                                      evidence_rounds=(c.valid_from, round_))
            if existing is not None:
                c.lineage.derivation_operator = "revise"
            self.claims[key] = c
        else:
            c = existing
            material = int(cands.n[i]) >= 1.2 * max(c.n, 1) or abs(conf - c.confidence) >= 0.1
            c.n, c.k, c.rate = int(cands.n[i]), int(cands.k[i]), float(cands.rate[i])
            c.baseline_rate, c.effect, c.p_value, c.q_value = float(cands.baseline[i]), float(cands.effect[i]), float(cands.p[i]), float(cands.q[i])
            c.confidence = conf
            if material:
                c.round_updated = round_
            c.lineage.contributing_units = contributing
            c.lineage.parent_claim_ids = parents
            c.lineage.evidence_rounds = (c.lineage.evidence_rounds[0], round_)
        c.support = SupportRecord(replica_count=int(cands.n[i]), independent_support=is_, distinct_workers=int(cands.dw[i]),
                                  distinct_teams=int(cands.dt[i]), distinct_departments=int(cands.dd[i]),
                                  distinct_regions=int(cands.dr[i]))
        accept = is_ >= p.support_min if p.lineage else int(cands.n[i]) >= p.support_min
        contested_below = p.lineage and any(
            c2.status == "contested" and k2[0] == cell and k2[1] == label and k2[2] == sign and k2[3] == sender
            for sender, ch in self.received_claims.items() for k2, c2 in ch.items())
        if self.received_from and p.lineage:
            # a synthesis at this scope must rest on independent evidence from >= 2 children; a cell seen by
            # one child only stays that child's scoped claim (also removes single-source selection bias)
            n_children = sum(1 for cid in contributing if cid in self.received_from)
            if n_children < min(2, len(self.children)):
                accept = False
        if c.status in ("proposed", "accepted"):
            c.status = "accepted" if accept else "proposed"
        if contested_below and c.status == "accepted":
            c.status = "contested"; c.quarantine_reason = "contested below"
            self.conflicts.setdefault(key, Conflict(conflict_id=f"C:{self.unit_id}:{cell}:{label}", cell=cell, label=label,
                                                    claim_ids=[c.claim_id], signs=[1, 0], round_opened=round_, status="contested",
                                                    reason="a child's own-scope claim is contested"))
        if c.status == "accepted" and p.lineage:
            c.sign_with(self.key)
        self.hier.trace_claim(self, c, round_)

    def _revise(self, round_: int, recent_sk: Sketch) -> None:
        p = self.policy
        if len(recent_sk.ids) == 0:
            return
        own = [c for (cell, label, sign, scope), c in self.claims.items()
               if scope == self.unit_id and c.status == "accepted" and c.sign > 0]
        if not own:
            return
        cells = np.array([c.cell for c in own])
        cnt = recent_sk.lookup(cells)
        n = cnt[:, COL_N]
        k = np.array([cnt[i, COL_K0 + c.label] for i, c in enumerate(own)])
        base = np.array([c.baseline_rate for c in own])
        refuted = (n >= p.n_min) & null_evidence(n, k, base, p.effect_min)
        for i in np.flatnonzero(refuted):
            c = own[i]
            c.status = "superseded"; c.valid_to = round_; c.round_updated = round_
            self.hier.trace_claim(self, c, round_)
            if not p.retain_superseded:
                del self.claims[(c.cell, c.label, c.sign, self.unit_id)]

    def _inherit(self, round_: int) -> None:
        for sender, ch in self.received_claims.items():
            for (cell, label, sign, scope), c in ch.items():
                key = (cell, label, sign, scope)
                cur = self.claims.get(key)
                if cur is None:
                    if c.status in ("accepted", "contested"):
                        self.claims[key] = c
                elif _newer_version(c, cur):
                    # same version with changed status/counts, or a later claim of the same scope (a revival after
                    # supersession / a rebuilt node carries a new claim_id): the child's current view replaces the copy
                    self.claims[key] = c

    def _consistency(self, round_: int) -> None:
        """Cross-source check.  For each positive claim compare children whose
        evidence is elevated with children holding powerful null evidence.

        Outcomes (recorded as a Conflict):
        * resolved for the claim  - elevated side has >= resolve_ratio x the null side's independent support;
        * split_conditional       - at the executive, disjoint region sets with adequate support on both sides;
        * contested               - otherwise.  An own-scope claim is demoted to `contested` (it no longer
          counts as organisation-wide knowledge); an inherited scoped claim keeps its scope but is marked
          contested so that downstream consumers see the disagreement.  Nothing is deleted: uncertainty is
          preserved rather than forced to a winner.
        """
        p = self.policy
        if not p.lineage or not self.received_from:
            return
        for key, c in list(self.claims.items()):
            if c.sign <= 0 or c.status not in ("accepted", "contested", "proposed"):
                continue
            cell, label = c.cell, c.label
            rows = self.child_cell_counts(cell)
            if len(rows) < 2:
                continue
            ids = list(rows)
            n = np.array([rows[i][COL_N] for i in ids]); k = np.array([rows[i][COL_K0 + label] for i in ids])
            rate = k / np.maximum(n, 1)
            elevated = (rate >= c.baseline_rate + p.effect_min / 2) & (n >= 3)
            rest = ~elevated
            n_rest, k_rest = int(n[rest].sum()), int(k[rest].sum())
            n_el, k_el = int(n[elevated].sum()), int(k[elevated].sum())
            nulls = np.zeros(len(n), dtype=bool)
            if elevated.any() and n_rest >= p.n_min and n_el > 0:
                # the pooled rest must be (i) powerful null evidence and (ii) significantly below the elevated side
                ub_ok = bool(null_evidence(np.array([n_rest]), np.array([k_rest]), np.array([c.baseline_rate]), p.effect_min,
                                           margin=p.effect_min)[0])
                p_two = float(rate_test(np.array([n_el]), np.array([k_el]), np.array([n_rest]), np.array([k_rest]), np.array([1]))[0])
                if ub_ok and p_two < 0.01:
                    nulls = rest & (n > 0)
            conf = self.conflicts.get(key)
            if not (elevated.any() and nulls.any()):
                if conf is not None and conf.status in ("open", "contested"):
                    conf.status = "resolved"; conf.winner = c.claim_id; conf.reason = "disagreement vanished"
                    if c.status == "contested":
                        c.status = "accepted"; c.round_updated = round_
                        if key[3] == self.unit_id and p.lineage:
                            c.sign_with(self.key)
                continue
            def side_support(mask):
                dw, dt, dd, dr = self._pooled_distinct(rows, [i for i, m in zip(ids, mask) if m], cell)
                return self._independent_support(dw, dt, dd, dr, int(n[mask].sum()))
            is_pos, is_null = side_support(elevated), side_support(nulls)
            if conf is None:
                conf = Conflict(conflict_id=f"C:{self.unit_id}:{cell}:{label}", cell=cell, label=label,
                                claim_ids=[c.claim_id], signs=[1, 0], round_opened=round_)
                self.conflicts[key] = conf
                self.hier.stats["conflicts_opened"] += 1
            c.contradicts = [ids[i] for i in np.flatnonzero(nulls)]
            pos_units = {ids[i] for i in np.flatnonzero(elevated)}
            null_units = {ids[i] for i in np.flatnonzero(nulls)}
            own_scope = key[3] == self.unit_id
            if self.layer == "executive" and pos_units.isdisjoint(null_units) and is_pos >= p.support_min \
                    and is_null >= p.support_min and len(pos_units) >= 1 and len(null_units) >= 1 and own_scope:
                conf.status = "split_conditional"; conf.winner = c.claim_id; conf.reason = "disjoint regions"
                c.conditional_on = {"region": ",".join(sorted(pos_units))}
                if c.status == "contested":
                    c.status = "accepted"
                self.hier.trace_claim(self, c, round_)
                continue
            if is_pos >= p.resolve_ratio * is_null:
                conf.status = "resolved"; conf.winner = c.claim_id; conf.reason = f"IS {is_pos:.1f} vs null {is_null:.1f}"
                if c.status == "contested":
                    c.status = "accepted"; c.round_updated = round_
                    if own_scope and p.lineage:
                        c.sign_with(self.key)   # counts changed while contested; sign the current content
                    self.hier.trace_claim(self, c, round_)
                c.disputed = False
            else:
                conf.status = "contested"; conf.winner = None; conf.reason = f"IS {is_pos:.1f} vs null {is_null:.1f}"
                if own_scope:
                    if c.status in ("accepted", "proposed"):
                        c.status = "contested"; c.quarantine_reason = "contested by independent null evidence"
                        self.hier.trace_claim(self, c, round_)
                else:
                    # an inherited scoped claim keeps its scope and status; the dispute is recorded on it
                    if not c.disputed:
                        c.disputed = True; c.round_updated = round_

    # ---- temporal helpers ------------------------------------------------
    def _trim_recent(self, round_: int) -> None:
        w = self.policy.recent_window
        for r in list(self.recent):
            if r < round_ - w + 1:
                del self.recent[r]

    def _recent_pool(self, round_: int) -> Sketch:
        p = self.policy
        if self.obs_attrs:
            # leaf: rebuild the window from the local store so distinct-source columns are exact (pooling the
            # per-round sketches counts a worker once per round it contributed in)
            rounds = np.concatenate(self.obs_round)
            m = rounds >= round_ - p.recent_window + 1
            if not m.any():
                return Sketch(self.unit_id, self.layer, round_, max_order=p.sketch_order)
            org = self.hier.org
            w = np.concatenate(self.obs_worker)[m]
            sk = Sketch.from_interactions(
                self.unit_id, self.layer, round_, np.concatenate(self.obs_attrs)[m], np.concatenate(self.obs_labels)[m], w,
                org.worker_team[w], org.worker_department[w], org.worker_region[w], max_order=p.sketch_order,
                present=np.concatenate(self.obs_present)[m])
            return sk
        return self._fix_distinct(Sketch.pool(list(self.recent.values()), self.unit_id, self.layer, round_, p.sketch_order))

    # ---- questions -------------------------------------------------------
    def answer_question(self, q: QuestionArtifact, round_: int) -> Sketch | None:
        """Exact counts for one cell from local evidence (bounded artifact), subject to the same
        k-anonymity floor as promoted sketch cells (a cell held by fewer than k records is not disclosed)."""
        ans = self._answer_question_raw(q, round_)
        k = int(self.policy.k_anonymity)
        if ans is not None and k > 0 and len(ans.ids) and int(ans.counts[0, COL_N]) < k:
            self.hier.stats["answers_suppressed_k_anonymity"] = self.hier.stats.get("answers_suppressed_k_anonymity", 0) + 1
            return None
        return ans

    def _answer_question_raw(self, q: QuestionArtifact, round_: int) -> Sketch | None:
        ci = cell_index()
        order = int(ci.order_of(np.array([q.cell]))[0])
        if order <= self.policy.sketch_order and len(self.cumulative.ids):
            cnt = self.cumulative.lookup(np.array([q.cell]))
            if cnt[0, COL_N] > 0:
                return Sketch(self.unit_id, self.layer, round_, np.array([q.cell]), cnt, order)
            return None
        if self.obs_attrs:
            attrs = np.concatenate(self.obs_attrs); labels = np.concatenate(self.obs_labels)
            present = np.concatenate(self.obs_present); w = np.concatenate(self.obs_worker)
            pairs = ci.decode(int(q.cell))
            m = np.ones(len(attrs), dtype=bool)
            for a, v in pairs:
                m &= (attrs[:, a] == v) & present[:, a]
            if not m.any():
                return None
            org = self.hier.org
            ww = w[m]
            lab = mask_matrix(labels[m]).astype(np.int64)
            counts = np.zeros((1, N_COLS), dtype=np.int64)
            counts[0, COL_N] = int(m.sum()); counts[0, COL_K0:COL_K0 + N_LABELS] = lab.sum(axis=0)
            counts[0, COL_DW] = len(np.unique(ww)); counts[0, COL_DT] = len(np.unique(org.worker_team[ww]))
            counts[0, COL_DD] = len(np.unique(org.worker_department[ww])); counts[0, COL_DR] = len(np.unique(org.worker_region[ww]))
            return Sketch(self.unit_id, self.layer, round_, np.array([q.cell]), counts, order)
        # non-team node without the cell order: relay to children (cost charged per hop)
        parts = []
        for cid in self.children:
            child = self.hier.nodes.get(cid)
            if child is None or not child.available:
                continue
            ans = child.answer_question(q, round_)
            if ans is not None:
                self.hier.stats["question_bytes"] += ans.wire_bytes() + q.wire_bytes()
                self.hier.stats["question_hops"] += 1
                parts.append(ans)
        if not parts:
            return None
        pooled = Sketch.pool(parts, self.unit_id, self.layer, round_, order)
        # the relayed answer is pooled over this node's children: fix the columns for the layers above them
        held = {p_.producer_id for p_ in parts}
        return self._fix_distinct(pooled, [cid for cid in self.children if cid in held])

    def _absorb_answer(self, qid: str, sk: Sketch, round_: int) -> Sketch | None:
        """Pool an answer (the child's exact current count for one cell) into this node's evidence.

        Only what this node does not already hold from that child for the cell (promoted deltas, an earlier
        answer) is new evidence, so absorption never double-counts.  Returns the absorbed delta (the caller
        mirrors it into the child's `sent`, so the child's later promotions stay relative to what the parent holds)."""
        q = self.questions.get(qid)
        if q is None:
            return None
        q.status = "answered"; q.answer_bytes += sk.wire_bytes()
        sender = sk.producer_id
        prev = self.received_from.get(sender, Sketch(sender, "?", round_))
        delta = _delta(sk, prev)
        if len(delta.ids) == 0:
            return None
        self.cumulative = Sketch.pool([self.cumulative, delta], self.unit_id, self.layer, round_, self.policy.sketch_order)
        if self.obs_attrs:
            prev_a = self._answer_cells if self._answer_cells is not None else Sketch(self.unit_id, self.layer, round_)
            self._answer_cells = Sketch.pool([prev_a, delta], self.unit_id, self.layer, round_, self.policy.sketch_order)
        self.recent[round_] = Sketch.pool([self.recent.get(round_, Sketch(self.unit_id, self.layer, round_)), delta],
                                          self.unit_id, self.layer, round_, self.policy.sketch_order)
        self.received_from[sender] = Sketch.pool([prev, delta], sender, prev.layer, round_, self.policy.sketch_order)
        self._distinct_dirty = True
        return delta

    def ask(self, proposals: list[tuple[int, int, float, str]], round_: int) -> None:
        """Send questions (cell, label, gain, trigger) to children and absorb answers.

        One question per cell: an answer is a one-cell sketch carrying every label's count and is pooled into
        `cumulative`, so a cell whose answer this node already absorbed (or that appears twice in `proposals`)
        is skipped rather than double-counted."""
        answered = {q.cell for q in self.questions.values() if q.status == "answered"}
        for cell, label, gain, trigger in proposals:
            if int(cell) in answered:
                self.hier.stats["questions_skipped_duplicate"] = self.hier.stats.get("questions_skipped_duplicate", 0) + 1
                continue
            answered.add(int(cell))
            qid = f"Q:{self.unit_id}:{cell}:{label}:{round_}"
            q = QuestionArtifact(question_id=qid, asker_id=self.unit_id, cell=int(cell), label=int(label), trigger=trigger,
                                 budget_bytes=self.policy.question_byte_budget, target_unit_ids=list(self.children),
                                 round=round_, expected_gain=gain)
            self.questions[qid] = q
            self.hier.stats["questions_asked"] += 1
            for cid in self.children:
                child = self.hier.nodes.get(cid)
                if child is None or not child.available:
                    continue
                self.hier.stats["question_bytes"] += q.wire_bytes()
                ans = child.answer_question(q, round_)
                if ans is not None:
                    self.hier.stats["question_bytes"] += ans.wire_bytes()
                    d = self._absorb_answer(qid, ans, round_)
                    if d is not None:
                        # the child's `sent` mirrors what this node holds from it (as after a promotion / resync)
                        child.sent = Sketch.pool([child.sent, d], child.unit_id, child.layer, round_, child.policy.sketch_order)

    # ---- promotion -------------------------------------------------------
    def promote(self, round_: int) -> Artifact | None:
        p = self.policy
        ci = cell_index()
        cum = self.cumulative
        # select cells according to the compression policy
        if p.compression == "full_sketch":
            selected = cum
        elif p.compression == "order_capped":
            selected = cum.restrict(min_n=p.min_cell_n)
        elif p.compression == "order2_plus_significant":
            low = cum.restrict(max_order=2, min_n=p.min_cell_n)
            high = self._significant_cells(cum).filter_orders({3, 4})
            selected = Sketch.pool([low, high], cum.producer_id, cum.layer, cum.round, cum.max_order)
        elif p.compression == "significant_cells_only":
            selected = self._significant_cells(cum)
        elif p.compression == "claims_only":
            claim_cells = np.array(sorted({c.cell for c in self.claims.values() if c.status == "accepted"}), dtype=np.int64)
            selected = cum.restrict(keep_ids=claim_cells, min_n=p.min_cell_n)
        else:
            raise ValueError(p.compression)
        # k-anonymity suppression on higher-order cells (order-1 marginals are aggregates)
        if p.k_anonymity > 0:
            selected = selected.restrict(min_n=max(p.min_cell_n, p.k_anonymity))
        delta = _delta(selected, self.sent)
        # claims: own accepted first (by q then effect), then inherited
        own = [c for (cell, label, sign, scope), c in self.claims.items() if scope == self.unit_id and c.status == "accepted"]
        inh = [c for (cell, label, sign, scope), c in self.claims.items() if scope != self.unit_id and c.status in ("accepted", "contested")] \
            if p.forward_inherited else []
        own += [c for (cell, label, sign, scope), c in self.claims.items() if scope == self.unit_id and c.status == "contested"]
        own.sort(key=lambda c: (c.q_value, -abs(c.effect)))
        inh.sort(key=lambda c: (-c.confidence, c.q_value))
        # only claims that are new or changed since last promotion travel (bounded by top_k per round)
        never = [c for c in own + inh if c.claim_id not in self.sent_claim_version]
        updated = [c for c in own + inh if c.claim_id in self.sent_claim_version
                   and self.sent_claim_version[c.claim_id] != (c.round_updated, c.status)]
        changed = never + updated
        # superseded / rejected own claims must also be announced once so parents can retire their copies
        retired = [c for (cell, label, sign, scope), c in self.claims.items()
                   if c.status in ("superseded", "rejected") and self.sent_claim_version.get(c.claim_id) not in (None, (c.round_updated, c.status))]
        changed = retired + changed
        claims = changed[: p.top_k_claims] if p.top_k_claims > 0 else changed
        import copy as _copy
        claims = [_copy.deepcopy(c) for c in claims]
        for c in claims:
            self.sent_claim_version[c.claim_id] = (c.round_updated, c.status)
            # security hook (C-security): own claims leave with a signature over their *current* content, so a
            # parent's lineage-aware verifier can check the producer signature after count refreshes or a
            # contested -> accepted flip (which do not re-sign in place).  Inherited copies keep the producer's.
            if p.lineage and c.producer_id == self.unit_id and c.status in ("accepted", "contested"):
                c.sign_with(self.key)
        # byte budget: drop lowest-priority claims then largest cells
        art = Artifact(self.unit_id, self.layer, round_, delta, claims, answers=self.pending_answers)
        if p.byte_budget > 0:
            while art.wire_bytes() > p.byte_budget and art.claims:
                art.claims.pop()
            if art.wire_bytes() > p.byte_budget and len(delta.ids):
                orders = ci.order_of(delta.ids)
                keep_n = max(1, int(len(delta.ids) * p.byte_budget / max(art.wire_bytes(), 1)))
                pri = np.lexsort((-delta.counts[:, COL_N], orders))   # low order & high n first
                keep = np.zeros(len(delta.ids), dtype=bool); keep[pri[:keep_n]] = True
                delta = Sketch(delta.producer_id, delta.layer, delta.round, delta.ids[keep], delta.counts[keep], delta.max_order)
                art.sketch = delta
        self.pending_answers = []
        # update `sent` with what actually left
        self.sent = Sketch.pool([self.sent, art.sketch], self.unit_id, self.layer, round_, p.sketch_order)
        self.bytes_out += art.wire_bytes()
        art.signature_ok = True
        return art

    def _significant_cells(self, cum: Sketch) -> Sketch:
        """Order-1 marginals plus higher-order cells whose label rate deviates
        from the cell's own order-1 marginal by a cheap z >= 1 screen."""
        ci = cell_index()
        if len(cum.ids) == 0:
            return cum
        orders = ci.order_of(cum.ids)
        first_attr_ids = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
        tot = cum.lookup(first_attr_ids).sum(axis=0)
        tot_rate = tot[COL_K0:COL_K0 + N_LABELS] / max(int(tot[COL_N]), 1)
        n = cum.counts[:, COL_N][:, None]
        rate = cum.counts[:, COL_K0:COL_K0 + N_LABELS] / np.maximum(n, 1)
        z = (rate - tot_rate[None, :]) / np.sqrt(np.maximum(tot_rate * (1 - tot_rate), 1e-4) / np.maximum(n, 1))
        keep = (orders == 1) | ((np.abs(z) >= 1.0).any(axis=1) & (cum.counts[:, COL_N] >= self.policy.min_cell_n))
        keep |= np.isin(cum.ids, np.array([c.cell for c in self.claims.values() if c.status == "accepted"], dtype=np.int64))
        return Sketch(cum.producer_id, cum.layer, cum.round, cum.ids[keep], cum.counts[keep], cum.max_order)

    def accepted_claims(self) -> list[Claim]:
        return [c for c in self.claims.values() if c.status == "accepted"]

    def contested_claims(self) -> list[Claim]:
        return [c for c in self.claims.values() if c.status == "contested"]


def _delta(selected: Sketch, sent: Sketch) -> Sketch:
    if len(selected.ids) == 0:
        return Sketch(selected.producer_id, selected.layer, selected.round, max_order=selected.max_order)
    prev = sent.lookup(selected.ids)
    d = selected.counts - prev
    keep = d[:, COL_N] > 0
    return Sketch(selected.producer_id, selected.layer, selected.round, selected.ids[keep], d[keep], selected.max_order)


def _newer_version(c: Claim, cur: Claim) -> bool:
    """Is `c` a fresher view of the claim `cur` holds under the same (cell, label, sign, scope) key?  The same
    claim_id with a changed (round_updated, status), or a later claim of that scope (a revival after supersession
    carries `revision_of`; a rebuilt node re-creates its claims with new ids)."""
    if c.claim_id == cur.claim_id:
        return (c.round_updated, c.status) != (cur.round_updated, cur.status)
    return c.revision_of == cur.claim_id or c.round_created > cur.round_created


# --------------------------------------------------------------------------
# Hierarchy
# --------------------------------------------------------------------------
class Hierarchy:
    """Builds the aggregation tree for `layers` in {1,2,3,5,7} over the org."""

    def __init__(self, org, records, policy: Policy, profile, seed: int, layers: int = 5, security=None,
                 question_policy=None, attack_hook=None, name: str = "mycelic") -> None:
        self.org = org
        self.records = records
        self.policy = policy
        self.profile = profile
        self.seed = seed
        self.layers_n = layers
        self.security = security
        self.question_policy = question_policy
        self.attack_hook = attack_hook
        self.name = name
        self.slm = SimulatedSLM(profile, seed * 7919 + 17)
        self.nodes: dict[str, UnitNode] = {}
        self.team_node_of: dict[int, str] = {}   # team index -> node id that receives its batches
        self.unavailable: set[str] = set()
        self.delay: dict[str, int] = {}
        self.stats: dict[str, float] = {k: 0 for k in (
            "conflicts_opened", "questions_asked", "question_bytes", "question_hops", "artifacts_rejected_signature",
            "records_rejected", "records_deduped", "bytes_worker_to_team")}
        self.trace: list[dict[str, Any]] = []
        self.batch_trace: list[dict[str, Any]] = []
        self.quarantine_trace: list[dict[str, Any]] = []
        self.layer_names: list[str] = []
        self._build()
        self.root = self.layer_names[-1]

    # ---- topology --------------------------------------------------------
    def _build(self) -> None:
        org = self.org
        L = self.layers_n
        # membership per layer for each team (teams are the leaf aggregators; workers feed teams)
        team = np.arange(org.n_teams)
        chain: list[tuple[str, np.ndarray]] = []   # (layer name, unit index per team)
        if L >= 7:
            chain.append(("squad", None))       # squads sit between workers and teams
        chain.append(("team", team))
        if L >= 3:
            chain.append(("department", org.team_department))
        if L >= 7:
            chain.append(("division", org.team_department // 2))
        if L >= 5:
            chain.append(("region", org.department_region[org.team_department]))
        chain.append(("executive", np.zeros(org.n_teams, dtype=np.int64)))
        if L == 1:
            chain = [("executive", np.zeros(org.n_teams, dtype=np.int64))]
        # unit ids
        ids_for = {
            "team": org.team_ids, "department": org.department_ids, "region": org.region_ids, "executive": ["EXEC"],
            "division": [f"V{i:02d}" for i in range((org.n_departments + 1) // 2)],
        }
        squads_per_team = 4
        if L >= 7:
            ids_for["squad"] = [f"S{t:04d}.{s}" for t in range(org.n_teams) for s in range(squads_per_team)]
        self.layer_names = [name for name, _ in chain]
        # parent of each unit
        for li, (name, mem) in enumerate(chain):
            if name == "squad":
                continue
            unit_ids = ids_for[name]
            parent_layer = chain[li + 1][0] if li + 1 < len(chain) else None
            for u, uid in enumerate(unit_ids):
                if name == "executive":
                    parent = None
                else:
                    teams_in = np.flatnonzero(mem == u)
                    if len(teams_in) == 0:
                        continue
                    parent = ids_for[parent_layer][int(chain[li + 1][1][teams_in[0]])]
                self.nodes[uid] = UnitNode(uid, name, parent, [], self.policy, self)
        if L >= 7:
            for t in range(org.n_teams):
                for s in range(squads_per_team):
                    sid = f"S{t:04d}.{s}"
                    self.nodes[sid] = UnitNode(sid, "squad", org.team_ids[t], [], self.policy, self)
        for uid, node in self.nodes.items():
            if node.parent_id is not None and node.parent_id in self.nodes:
                self.nodes[node.parent_id].children.append(uid)
        # (team, department, region) index of every unit (-1 where the unit spans several); used to count distinct
        # units when a node pools children from a layer the topology skips (UnitNode._fix_distinct)
        td, dr = org.team_department, org.department_region
        self.unit_membership: dict[str, tuple[int, int, int]] = {"EXEC": (-1, -1, -1)}
        for t, tid in enumerate(org.team_ids):
            self.unit_membership[tid] = (t, int(td[t]), int(dr[td[t]]))
            for s in range(squads_per_team):
                self.unit_membership[f"S{t:04d}.{s}"] = self.unit_membership[tid]
        for d, did in enumerate(org.department_ids):
            self.unit_membership[did] = (-1, d, int(dr[d]))
        for v, vid in enumerate(ids_for["division"]):
            self.unit_membership[vid] = (-1, -1, int(dr[min(2 * v, org.n_departments - 1)]))
        for r, rid in enumerate(org.region_ids):
            self.unit_membership[rid] = (-1, -1, r)
        # where worker batches land
        for t in range(org.n_teams):
            if L == 1:
                self.team_node_of[t] = "EXEC"
            elif L >= 7:
                self.team_node_of[t] = f"S{t:04d}.0"   # split further inside ingest by worker index
            else:
                self.team_node_of[t] = org.team_ids[t]
        self.by_layer: dict[str, list[UnitNode]] = {}
        for n in self.nodes.values():
            self.by_layer.setdefault(n.layer, []).append(n)

    # ---- tracing ---------------------------------------------------------
    def trace_claim(self, node: UnitNode, c: Claim, round_: int) -> None:
        self.trace.append({"round": round_, "unit": node.unit_id, "layer": node.layer, "cell": c.cell, "label": c.label,
                           "sign": c.sign, "scope": c.lineage.path[0] if c.lineage.path else node.unit_id,
                           "status": c.status, "claim_id": c.claim_id, "is": c.support.independent_support,
                           "n": c.n, "conf": c.confidence, "attack": c.origin_attack})

    def trace_batch(self, node: UnitNode, batch: ObservationBatch, keep: np.ndarray, round_: int) -> None:
        self.stats["records_rejected"] += int((~keep).sum())
        self.stats["bytes_worker_to_team"] += batch.wire_bytes()
        if (batch.is_attack > 0).any() or (~keep).any():
            self.batch_trace.append({"round": round_, "unit": node.unit_id, "n": len(batch), "kept": int(keep.sum()),
                                     "attack_records": int((batch.is_attack > 0).sum()),
                                     "attack_kept": int(((batch.is_attack > 0) & keep).sum()),
                                     "benign_dropped": int(((batch.is_attack == 0) & ~keep).sum())})

    def trace_quarantine(self, node: UnitNode, c: Claim, round_: int) -> None:
        self.quarantine_trace.append({"round": round_, "unit": node.unit_id, "layer": node.layer, "claim_id": c.claim_id,
                                      "cell": c.cell, "label": c.label, "attack": c.origin_attack, "reason": c.quarantine_reason})

    # ---- simulation ------------------------------------------------------
    def run(self, n_rounds: int, on_round: Callable[["Hierarchy", int], None] | None = None) -> None:
        rec = self.records
        team_of_record = rec.team
        order = np.argsort(rec.round, kind="stable")
        bounds = np.searchsorted(rec.round[order], np.arange(n_rounds + 1))
        for r in range(n_rounds):
            idx = order[bounds[r]:bounds[r + 1]]
            batches = make_batches(rec, idx, team_of_record, self.org.n_teams, self.slm, self.policy.quote_policy, self.attack_hook)
            self._ingest_workers(batches, r)
            self._step_layers(r, final=(r == n_rounds - 1))
            if on_round is not None:
                on_round(self, r)

    def _ingest_workers(self, batches: dict[int, ObservationBatch], r: int) -> None:
        L = self.layers_n
        fan_in_used = 0
        for t, batch in batches.items():
            if L >= 7:
                # split the team batch into squads by worker index
                wpt = max(1, self.org.n_workers // max(self.org.n_teams, 1))
                squad = ((batch.worker - t * wpt) * 4 // max(wpt, 1)).clip(0, 3)
                # a bare claim goes to the squad of the worker it names (never to all four)
                claim_squad = [int(np.clip((int(d.get("worker", -1)) - t * wpt) * 4 // max(wpt, 1), 0, 3)) for d in batch.injected_claims]
                for s in range(4):
                    m = squad == s
                    inj = [d for d, cs in zip(batch.injected_claims, claim_squad) if cs == s]
                    if not m.any() and not inj:
                        continue
                    sub = _subset_batch(batch, m, inj)
                    node = self.nodes[f"S{t:04d}.{s}"]
                    if node.available:
                        node.ingest_batch(sub, r)
                continue
            node = self.nodes[self.team_node_of[t]]
            if not node.available:
                continue
            if self.policy.fan_in_budget_bytes > 0 and node.layer == "executive":
                b = batch.wire_bytes()
                if fan_in_used + b > self.policy.fan_in_budget_bytes:
                    node.bytes_dropped_fan_in += b
                    continue
                fan_in_used += b
            node.ingest_batch(batch, r)

    def _step_layers(self, r: int, final: bool) -> None:
        cadence = self.policy.cadence
        for layer in self.layer_names:
            nodes = self.by_layer.get(layer, [])
            due = final or (r % int(cadence.get(layer, 1)) == 0)
            for node in nodes:
                if not node.available:
                    continue
                # deliver delayed artifacts
                while node.inbox and node.inbox[0][0] <= r:
                    _, art = node.inbox.popleft()
                    node.ingest_artifact(art, r)
                if not due:
                    continue
                node.synthesize(r)
                if self.question_policy is not None and self.policy.questioning != "none":
                    props = self.question_policy.propose(node, r)
                    if props:
                        node.ask(props, r)
                        node.synthesize(r)
                if node.parent_id is not None:
                    art = node.promote(r)
                    parent = self.nodes[node.parent_id]
                    if art is None or not parent.available:
                        continue
                    d = self.delay.get(node.unit_id, 0)
                    if d > 0:
                        parent.inbox.append((r + d, art))
                    else:
                        parent.ingest_artifact(art, r)

    # ---- accessors -------------------------------------------------------
    def root_node(self) -> UnitNode:
        return self.nodes["EXEC"]

    def layer_nodes(self, layer: str) -> list[UnitNode]:
        return self.by_layer.get(layer, [])

    def totals(self) -> dict[str, float]:
        out: dict[str, float] = {"bytes_in": 0, "bytes_out": 0, "tokens": 0, "model_calls": 0, "quotes": 0,
                                 "bytes_dropped_fan_in": 0, "records_in": 0}
        per_layer: dict[str, dict[str, float]] = {}
        for n in self.nodes.values():
            out["bytes_in"] += n.bytes_in; out["bytes_out"] += n.bytes_out; out["tokens"] += n.tokens
            out["model_calls"] += n.model_calls; out["quotes"] += len(n.quotes_received)
            out["bytes_dropped_fan_in"] += n.bytes_dropped_fan_in; out["records_in"] += n.n_records_in
            pl = per_layer.setdefault(n.layer, {"bytes_in": 0, "bytes_out": 0, "claims_accepted": 0, "nodes": 0})
            pl["bytes_in"] += n.bytes_in; pl["bytes_out"] += n.bytes_out; pl["nodes"] += 1
            pl["claims_accepted"] += len(n.accepted_claims())
        out["per_layer"] = per_layer
        out.update(self.stats)
        return out


def _subset_batch(b: ObservationBatch, m: np.ndarray, injected: list[dict] | None = None) -> ObservationBatch:
    quotes = [q for q, keep in zip(b.quotes, m) if keep] if len(b.quotes) == len(m) else list(b.quotes[: int(m.sum())])
    return ObservationBatch(unit_id=b.unit_id, round=b.round, idx=b.idx[m], attrs=b.attrs[m], present=b.present[m],
                            labels=b.labels[m], worker=b.worker[m], fingerprint=b.fingerprint[m], confidence=b.confidence[m],
                            signature_ok=b.signature_ok[m], is_attack=b.is_attack[m], quotes=quotes,
                            injected_claims=list(b.injected_claims if injected is None else injected),
                            model_calls=int(m.sum()), tokens_in=int(m.sum()) * 180)
