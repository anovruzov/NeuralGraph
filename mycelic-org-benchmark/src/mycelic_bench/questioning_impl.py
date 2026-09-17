"""Question policies beyond `fixed`: confidence-triggered, expected-information-gain (EIG) per byte and
byte-budgeted EIG, plus `marginal_pair` candidate generation from screened cumulative counts (Task D).

Honesty.  A policy sees only what the asking node already holds -- its pooled `cumulative` sketch, the
per-child `received_from` sketches, its claims and its question log -- and the shape of the org tree.
It never touches records or ground truth.  Answers are produced by the trunk
(`UnitNode.answer_question`) from local evidence only.

Shared semantics (all three policies inherit `ScoredQuestionPolicy`):

* Candidates come from (a) `QuestionPolicy.candidate_cells` (unions of compatible accepted claims, the
  trunk generator) and (b) `marginal_pair_candidates`: pairs of *screened* order-k cells, k = the highest
  order children promote (`policy.sketch_order`), whose label rate passes a cheap one-sided z >= 1.5
  screen against the node-wide label rate (the node's order-1 marginal for that label), that agree on
  k-1 attribute values including a model_family or task_family value, merged into an order-(k+1) cell.
  With k = 2 this is literally "pairs of order-2 cells sharing a model or task value -> order 3"; with
  k = 3 it yields the order-4 targets.  (Two order-2 cells that share a *value* share an attribute and
  can therefore never merge into an order-4 cell; parents of order k are what make order 4 reachable.)
* Only cells of order sketch_order + 1 (<= max_order_questions) are asked.  Lower orders already travel
  in promoted sketches, and the trunk *pools* (never replaces) answers into `cumulative` and
  `received_from`, so asking for them would count records twice.  For the same reason a cell is asked
  only while the node holds no evidence for it (n == 0), at most once per node, and never when an
  ancestor or descendant has already asked for it (a relayed answer would overlap a promoted one).
* Expected answer size per child = min over the cell's order-k sub-cells of the child's promoted count,
  scaled by the child's order-1 fraction for the attribute value that sub-cell lacks (an
  attribute-independence estimate; unbiased inside a team, whose attributes are sampled independently).
* Expected gain = reduction of the posterior variance of the label rate under a Beta(1,1) prior updated
  with the node's pooled (n, k) for the cell and label, after m expected further observations:
  V0 * m / (n + 2 + m) with V0 = a b / ((a+b)^2 (a+b+1)), a = 1 + k, b = 1 + n - k (law of total
  variance for the beta-binomial predictive).  It is stored on `QuestionArtifact.expected_gain`.
* Expected bytes mirror the trunk's accounting in `UnitNode.ask` / `answer_question`: one question copy
  per child, one one-cell answer sketch per child expected to hold evidence, and question + answer
  copies for every relay hop below a child that cannot answer from its own store.
* Testability gate: a question is proposed only when n + m_hat >= n_min * ask_margin (default 1.0) so
  that the single answer a cell can receive is expected to make the cell testable.

Policies
    confidence  ask only when the candidate's parents are *uncertain*: every parent is at least
                suggestive (q <= 0.2) and some parent is either not significant (fdr_q < q <= 0.2) or
                thinly supported (independent support < 2 x support_min).  Ranked by expected gain,
                at most `question_budget` per node-round.
    eig         ranked by expected gain per expected byte, at most `question_budget` per node-round.
    budget      eig ranking, greedy under `question_byte_budget` expected bytes per node-round.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .hypothesis import search
from .questioning import QuestionPolicy
from .sketch import COL_DD, COL_DR, COL_DT, COL_DW, COL_K0, COL_N, Sketch
from .vocab import ATTR_INDEX, MAX_ORDER, N_ATTR, N_LABELS, cell_index

SHARED_ATTRS: tuple[int, ...] = (ATTR_INDEX["model_family"], ATTR_INDEX["task_family"])
Z_SCREEN = 1.5                  # cheap one-sided screen for marginal-pair parents
CONFIDENCE_Q_HI = 0.2           # upper end of the "suggestive but not significant" q band
QUESTION_HEADER_BYTES = 48      # QuestionArtifact.wire_bytes() = 48 + 6 * len(target_unit_ids)
QUESTION_BYTES_PER_TARGET = 6
ANSWER_CELL_BYTES = 16 + 8      # Sketch.wire_bytes() for one cell before label / big-count bytes
MAX_GROUP = 16                  # screened extensions kept per (label, shared sub-cell) group
MAX_MARGINAL_CANDIDATES = 20000
LOG_LIMIT = 50000

SOURCE_CLAIM = "claim_pair"
SOURCE_MARGINAL = "marginal_pair"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
_COMBO_OF_MASK: dict[int, np.ndarray] = {}


def _combo_of_mask(order: int) -> np.ndarray:
    """Attribute bitmask (9 bits) -> combo index of that attribute set at `order` (-1 if not that order)."""
    tab = _COMBO_OF_MASK.get(order)
    if tab is None:
        ci = cell_index()
        tab = np.full(1 << N_ATTR, -1, dtype=np.int64)
        for idx, combo in enumerate(ci.combos[order]):
            tab[sum(1 << a for a in combo)] = idx
        _COMBO_OF_MASK[order] = tab
    return tab


def beta_variance_gain(n: np.ndarray, k: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Expected posterior-variance reduction of a rate under Beta(1,1) updated with (n, k), from m more
    Bernoulli observations: Var(theta | n,k) * m / (n + 2 + m)."""
    a = 1.0 + np.asarray(k, dtype=float)
    b = 1.0 + np.asarray(n, dtype=float) - np.asarray(k, dtype=float)
    s = a + b
    v0 = a * b / (s * s * (s + 1.0))
    m = np.maximum(np.asarray(m, dtype=float), 0.0)
    return v0 * m / (s + m)


def independent_support_vec(policy, cnt: np.ndarray) -> np.ndarray:
    """Vectorised twin of `UnitNode._independent_support` over sketch count rows."""
    n = cnt[:, COL_N].astype(float)
    if not policy.lineage:
        return n
    one = (n > 0).astype(np.int64)
    dr = np.maximum(cnt[:, COL_DR], one); dd = np.maximum(cnt[:, COL_DD], one)
    dt = np.maximum(cnt[:, COL_DT], one); dw = np.maximum(cnt[:, COL_DW], one)
    return dr + policy.rho_region * (dd - dr) + policy.rho_department * (dt - dd) + policy.rho_team * (dw - dt)


def _pairs_within_groups(sizes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """All (i < j) index pairs inside consecutive groups of the given sizes (vectorised).
    Returns (group id, i, j) with i, j relative to the group start."""
    pairs = sizes * (sizes - 1) // 2
    total = int(pairs.sum())
    if total == 0:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z
    grp = np.repeat(np.arange(len(sizes)), pairs)
    t = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(pairs) - pairs, pairs)
    j = np.floor((1.0 + np.sqrt(1.0 + 8.0 * t)) / 2.0).astype(np.int64)
    j = np.where(j * (j - 1) // 2 > t, j - 1, j)
    j = np.where((j + 1) * j // 2 <= t, j + 1, j)
    i = t - j * (j - 1) // 2
    return grp, i, j


@dataclass
class CandidateTable:
    """Scored candidate questions of one node at one round (all arrays aligned, length M)."""
    cell: np.ndarray
    label: np.ndarray
    source: np.ndarray          # SOURCE_CLAIM | SOURCE_MARGINAL
    heuristic: np.ndarray       # trunk gain (claim pairs) or z1 + z2 (marginal pairs); tie-breaker only
    n: np.ndarray               # node's pooled count for the cell (0 for askable cells)
    k: np.ndarray               # node's pooled label count
    m_child: np.ndarray         # [M, n_children] expected answer size per child
    m_hat: np.ndarray           # expected answer size (sum over children)
    bytes_hat: np.ndarray       # expected question + answer bytes
    gain: np.ndarray            # expected posterior-variance reduction
    parent_q: np.ndarray        # worst (max) parent q; 1.0 when unknown
    parent_is: np.ndarray       # weakest (min) parent independent support; inf when unknown
    eligible: np.ndarray        # testability gate

    def __len__(self) -> int:
        return len(self.cell)


# --------------------------------------------------------------------------
# base: candidate generation + scoring
# --------------------------------------------------------------------------
class ScoredQuestionPolicy(QuestionPolicy):
    name = "scored"
    ask_margin: float = 1.0     # ask when n + m_hat >= n_min * ask_margin

    def __init__(self, policy, seed: int) -> None:
        super().__init__(policy, seed)
        # per-policy counters (questions proposed, not answered; answers are counted by the trunk stats)
        self.n_calls = 0
        self.n_proposed = 0
        self.n_by_trigger: dict[str, int] = {}
        self.n_by_layer: dict[str, int] = {}
        self.n_candidates = 0
        self.n_eligible = 0
        self.expected_bytes_total = 0.0    # counter (the method `expected_bytes` must not be shadowed)
        self.expected_gain_total = 0.0
        self.log: list[dict[str, Any]] = []
        self._asked_by_node: dict[str, set[int]] = {}
        self._subtree_cache: dict[str, list[str]] = {}

    # ---- public summary --------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            "policy": self.name, "calls": self.n_calls, "questions_proposed": self.n_proposed,
            "by_trigger": dict(self.n_by_trigger), "by_layer": dict(self.n_by_layer),
            "candidates_seen": self.n_candidates, "eligible_seen": self.n_eligible,
            "expected_bytes": float(self.expected_bytes_total), "expected_gain_total": float(self.expected_gain_total),
        }

    # ---- orders ----------------------------------------------------------
    def askable_order(self) -> int | None:
        p = self.policy
        order = int(p.sketch_order) + 1
        if order > min(int(p.max_order_questions), MAX_ORDER) or order < 2:
            return None
        return order

    # ---- bookkeeping of what was asked where -----------------------------
    def _subtree(self, node) -> list[str]:
        out = self._subtree_cache.get(node.unit_id)
        if out is None:
            out = []
            stack = list(node.children)
            while stack:
                cid = stack.pop()
                ch = node.hier.nodes.get(cid)
                if ch is None:
                    continue
                out.append(cid)
                stack.extend(ch.children)
            self._subtree_cache[node.unit_id] = out
        return out

    def asked_cells(self, node, round_: int) -> set[int]:
        """Cells this node must not ask now: answered here (ever), open here within `recent_window`
        rounds, or asked by any ancestor / descendant (their answers overlap promoted evidence)."""
        p = self.policy
        skip: set[int] = set()
        for q in node.questions.values():
            if q.status == "answered" or q.round >= round_ - int(p.recent_window):
                skip.add(int(q.cell))
        pid = node.parent_id
        while pid is not None:
            skip |= self._asked_by_node.get(pid, set())
            par = node.hier.nodes.get(pid)
            pid = par.parent_id if par is not None else None
        for d in self._subtree(node):
            skip |= self._asked_by_node.get(d, set())
        return skip

    # ---- marginal-pair candidates ---------------------------------------
    def marginal_pair_candidates(self, node, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Order-(k+1) cells from pairs of screened order-k cells of `node.cumulative` that agree on k-1
        attribute values including a model_family or task_family value (see module docstring).

        Returns (cells, labels, score = z1 + z2, parent1, parent2), unique by (cell, label)."""
        p = self.policy
        ci = cell_index()
        cum: Sketch = node.cumulative
        z0 = np.zeros(0, dtype=np.int64)
        empty = (z0, z0, np.zeros(0, dtype=float), z0, z0)
        if len(cum.ids) == 0 or k < 2 or k + 1 > MAX_ORDER:
            return empty
        orders = ci.order_of(cum.ids)
        sel = (orders == k) & (cum.counts[:, COL_N] >= int(p.n_min))
        if not sel.any():
            return empty
        ids = cum.ids[sel]
        cnt = cum.counts[sel]
        n = cnt[:, COL_N].astype(float)
        kk = cnt[:, COL_K0:COL_K0 + N_LABELS].astype(float)
        first = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
        tot = cum.lookup(first).sum(axis=0)
        r0 = tot[COL_K0:COL_K0 + N_LABELS] / max(int(tot[COL_N]), 1)
        z = (kk / n[:, None] - r0[None, :]) / np.sqrt(np.maximum(r0 * (1 - r0), 1e-4)[None, :] / n[:, None])
        ei, lab = np.nonzero(z >= Z_SCREEN)
        if len(ei) == 0:
            return empty
        zval = z[ei, lab]
        combo_idx, vals_all = ci.decode_many(ids, k)
        combos = np.array(ci.combos[k], dtype=np.int64)
        attrs_all = combos[combo_idx]                       # [S, k]
        subs_all = ci.sub_ids(ids, k)                       # [S, k]  (position j drops attribute j)
        attrs, vals, subs = attrs_all[ei], vals_all[ei], subs_all[ei]
        E = len(ei)
        rows_all = np.zeros((E, N_ATTR), dtype=np.int64)
        rows_all[np.arange(E)[:, None], attrs] = vals
        mask_all = (1 << attrs).sum(axis=1)
        # the shared part (cell minus position j) must keep a model_family / task_family value
        has_mt = np.isin(attrs, SHARED_ATTRS)
        ok = (has_mt.sum(axis=1)[:, None] - has_mt) > 0
        r, j = np.nonzero(ok)
        if len(r) == 0:
            return empty
        g_label, g_sub = lab[r], subs[r, j]
        a_j, v_j, z_r = attrs[r, j], vals[r, j], zval[r]
        # group by (label, shared sub-cell); keep the top MAX_GROUP extensions by z inside each group
        order_ = np.lexsort((-z_r, g_sub, g_label))
        r, g_label, g_sub, a_j, v_j, z_r = (x[order_] for x in (r, g_label, g_sub, a_j, v_j, z_r))
        gkey = g_label.astype(np.int64) * (int(ci.total) + 1) + g_sub
        new = np.concatenate([[True], gkey[1:] != gkey[:-1]])
        gid = np.cumsum(new) - 1
        starts = np.flatnonzero(new)
        rank = np.arange(len(gkey)) - starts[gid]
        keep = rank < MAX_GROUP
        r, g_label, g_sub, a_j, v_j, z_r, gkey = (x[keep] for x in (r, g_label, g_sub, a_j, v_j, z_r, gkey))
        new = np.concatenate([[True], gkey[1:] != gkey[:-1]])
        starts = np.flatnonzero(new)
        sizes = np.diff(np.concatenate([starts, [len(gkey)]]))
        grp, ii, jj = _pairs_within_groups(sizes)
        if len(grp) == 0:
            return empty
        ri, rj = starts[grp] + ii, starts[grp] + jj
        diff_attr = a_j[ri] != a_j[rj]
        ri, rj = ri[diff_attr], rj[diff_attr]
        if len(ri) == 0:
            return empty
        # merged cell = full row of cell ri plus (a_j, v_j) of cell rj -> order k + 1
        rows = rows_all[r[ri]].copy()
        rows[np.arange(len(ri)), a_j[rj]] = v_j[rj]
        mask = mask_all[r[ri]] | (1 << a_j[rj])
        cidx = _combo_of_mask(k + 1)[mask]
        merged = ci.order_base[k + 1] + ci.combo_offset[k + 1][cidx] + (rows * ci.strides[k + 1][cidx]).sum(axis=1)
        label_m = g_label[ri]
        score = z_r[ri] + z_r[rj]
        p1, p2 = ids[ei[r[ri]]], ids[ei[r[rj]]]
        ukey = merged * N_LABELS + label_m
        order2 = np.lexsort((-score, ukey))
        ukey_s = ukey[order2]
        first_u = np.concatenate([[True], ukey_s[1:] != ukey_s[:-1]])
        pick = order2[first_u]
        if len(pick) > MAX_MARGINAL_CANDIDATES:
            pick = pick[np.argsort(-score[pick], kind="stable")[:MAX_MARGINAL_CANDIDATES]]
        return merged[pick], label_m[pick], score[pick], p1[pick], p2[pick]

    # ---- expected answer size / bytes ------------------------------------
    @staticmethod
    def expected_answer_size(children: list[str], received_from: dict[str, Sketch], cells: np.ndarray, order: int) -> np.ndarray:
        """[M, len(children)] expected matching records per child for order-`order` cells: for each child
        the smallest order-(order-1) sub-cell count in its promoted sketch, scaled by the child's order-1
        fraction of the attribute value that sub-cell lacks."""
        ci = cell_index()
        M = len(cells)
        out = np.zeros((M, len(children)), dtype=float)
        if M == 0 or not children:
            return out
        cells = np.asarray(cells, dtype=np.int64)
        subs = ci.sub_ids(cells, order)                              # [M, order]
        combo_idx, vals = ci.decode_many(cells, order)               # vals [M, order] in combo attribute order
        attrs = np.array(ci.combos[order], dtype=np.int64)[combo_idx]
        extra1 = ci.order_base[1] + ci.combo_offset[1][attrs] + vals  # order-1 id of (a_j, v_j)
        ar = np.arange(M)
        for c_i, cid in enumerate(children):
            sk = received_from.get(cid)
            if sk is None or len(sk.ids) == 0:
                continue
            total = sk.total_n()
            if total <= 0:
                continue
            n_sub = sk.lookup(subs.ravel())[:, COL_N].reshape(M, order)
            j = n_sub.argmin(axis=1)
            n_min_sub = n_sub[ar, j]
            frac = sk.lookup(extra1[ar, j])[:, COL_N] / float(total)
            out[:, c_i] = n_min_sub * frac
        return out

    def _label_rates(self, node) -> np.ndarray:
        ci = cell_index()
        cum = node.cumulative
        if len(cum.ids) == 0:
            return np.full(N_LABELS, 0.05)
        first = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
        tot = cum.lookup(first).sum(axis=0)
        return tot[COL_K0:COL_K0 + N_LABELS] / max(int(tot[COL_N]), 1)

    @staticmethod
    def _answer_bytes(m: np.ndarray, rates: np.ndarray) -> np.ndarray:
        """Expected wire bytes of a one-cell answer holding m records (2 bytes per non-zero label count)."""
        r = np.minimum(rates, 0.999)
        nz = (1.0 - np.exp(np.outer(m, np.log1p(-r)))).sum(axis=1)
        return ANSWER_CELL_BYTES + (m > 255).astype(float) + 2.0 * nz

    def _answer_cost(self, unit, m: np.ndarray, order: int, q_bytes: float, rates: np.ndarray, depth: int = 0) -> np.ndarray:
        """Expected bytes charged to obtain `unit`'s answer for m expected records (answer sketch plus the
        relay copies the trunk charges below a unit that cannot answer from its own store)."""
        ans = np.where(m > 0, self._answer_bytes(m, rates), 0.0)
        direct = order <= int(self.policy.sketch_order) or bool(unit.obs_attrs) or not unit.children or depth > 6
        if direct:
            return ans
        kids = [unit.hier.nodes[c] for c in unit.children if c in unit.hier.nodes and unit.hier.nodes[c].available]
        if not kids:
            return np.zeros_like(m)
        n_ans = np.minimum(float(len(kids)), np.ceil(m))
        share = m / np.maximum(n_ans, 1.0)
        per_kid = q_bytes + self._answer_cost(kids[0], share, order, q_bytes, rates, depth + 1)
        return np.where(m > 0, ans + n_ans * per_kid, 0.0)

    def expected_bytes(self, node, m_child: np.ndarray, order: int) -> np.ndarray:
        q_bytes = float(QUESTION_HEADER_BYTES + QUESTION_BYTES_PER_TARGET * len(node.children))
        rates = self._label_rates(node)
        total = np.zeros(m_child.shape[0], dtype=float)
        for c_i, cid in enumerate(node.children):
            ch = node.hier.nodes.get(cid)
            if ch is None or not ch.available:
                continue
            total += q_bytes + self._answer_cost(ch, m_child[:, c_i], order, q_bytes, rates)
        return total

    # ---- parent statistics (confidence trigger) --------------------------
    def _parent_q_map(self, node, k: int) -> tuple[np.ndarray, np.ndarray]:
        """q-values of positive order-k (cell, label) signals in the node's cumulative sketch from the shared
        hypothesis engine with the FDR threshold lifted (fdr_q = 1) -- the same statistic claims carry."""
        p = self.policy
        c = search(node.cumulative, n_min=int(p.n_min), effect_min=float(p.effect_min), fdr_q=1.0, max_order=k, min_order=k)
        if len(c) == 0:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=float)
        pos = c.sign > 0
        keys = c.cell[pos].astype(np.int64) * N_LABELS + c.label[pos].astype(np.int64)
        order_ = np.argsort(keys)
        return keys[order_], c.q[order_]

    def parent_stats(self, node, cells: np.ndarray, labels: np.ndarray, source: np.ndarray, p1: np.ndarray, p2: np.ndarray,
                     need_q: bool, k: int) -> tuple[np.ndarray, np.ndarray]:
        M = len(cells)
        q_max = np.ones(M, dtype=float)
        is_min = np.full(M, np.inf, dtype=float)
        if M == 0:
            return q_max, is_min
        p = self.policy
        ci = cell_index()
        mm = source == SOURCE_MARGINAL
        if mm.any():
            c1 = node.cumulative.lookup(p1[mm]); c2 = node.cumulative.lookup(p2[mm])
            is_min[mm] = np.minimum(independent_support_vec(p, c1), independent_support_vec(p, c2))
            if need_q:
                keys, qs = self._parent_q_map(node, k)

                def q_of(cells_: np.ndarray) -> np.ndarray:
                    out = np.ones(len(cells_), dtype=float)
                    if len(keys) == 0:
                        return out
                    key = cells_.astype(np.int64) * N_LABELS + labels[mm].astype(np.int64)
                    pos = np.clip(np.searchsorted(keys, key), 0, len(keys) - 1)
                    hit = keys[pos] == key
                    out[hit] = qs[pos[hit]]
                    return out
                q_max[mm] = np.maximum(q_of(p1[mm]), q_of(p2[mm]))
        cm = ~mm
        if cm.any():
            by_label: dict[int, list[tuple[set, float, float]]] = {}
            for c in node.claims.values():
                if c.status == "accepted" and c.sign > 0:
                    by_label.setdefault(int(c.label), []).append((set(ci.decode(int(c.cell))), float(c.q_value),
                                                                 float(c.support.independent_support)))
            for i in np.flatnonzero(cm):
                pairs = set(ci.decode(int(cells[i])))
                parents = [(q, s) for (ps, q, s) in by_label.get(int(labels[i]), []) if ps <= pairs]
                if parents:
                    q_max[i] = max(q for q, _ in parents)
                    is_min[i] = min(s for _, s in parents)
        return q_max, is_min

    # ---- the scored table ------------------------------------------------
    def build_table(self, node, round_: int, need_parent_q: bool = False) -> CandidateTable | None:
        p = self.policy
        ci = cell_index()
        order = self.askable_order()
        if order is None or not node.children:
            return None
        k = order - 1
        # (a) trunk candidates from pairs of accepted claims
        cp = self.candidate_cells(node)
        c_cells = np.array([c for c, _, _ in cp], dtype=np.int64)
        c_labels = np.array([l for _, l, _ in cp], dtype=np.int64)
        c_gain = np.array([g for _, _, g in cp], dtype=float)
        if len(c_cells):
            keep = ci.order_of(c_cells) == order
            c_cells, c_labels, c_gain = c_cells[keep], c_labels[keep], c_gain[keep]
        # (b) marginal pairs from screened cumulative counts
        m_cells, m_labels, m_score, m_p1, m_p2 = self.marginal_pair_candidates(node, k)
        cells = np.concatenate([c_cells, m_cells]).astype(np.int64)
        labels = np.concatenate([c_labels, m_labels]).astype(np.int64)
        heuristic = np.concatenate([c_gain, m_score]).astype(float)
        source = np.array([SOURCE_CLAIM] * len(c_cells) + [SOURCE_MARGINAL] * len(m_cells), dtype=object)
        p1 = np.concatenate([np.full(len(c_cells), -1, dtype=np.int64), m_p1])
        p2 = np.concatenate([np.full(len(c_cells), -1, dtype=np.int64), m_p2])
        if len(cells) == 0:
            return None
        # unique by (cell, label); claim-pair entries win ties (they carry accepted-claim parents)
        ukey = cells * N_LABELS + labels
        order_ = np.lexsort((-heuristic, (source == SOURCE_MARGINAL).astype(np.int64), ukey))
        ukey_s = ukey[order_]
        first_u = np.concatenate([[True], ukey_s[1:] != ukey_s[:-1]])
        pick = order_[first_u]
        cells, labels, heuristic, source, p1, p2 = (x[pick] for x in (cells, labels, heuristic, source, p1, p2))
        self.n_candidates += len(cells)
        # only cells the node holds no evidence for, never asked along its lineage
        cnt = node.cumulative.lookup(cells)
        n = cnt[:, COL_N]
        kk = cnt[np.arange(len(cells)), COL_K0 + labels]
        skip = self.asked_cells(node, round_)
        keep = (n == 0) & (n < int(p.n_min))
        if skip:
            keep &= ~np.isin(cells, np.fromiter(skip, dtype=np.int64, count=len(skip)))
        if not keep.any():
            return None
        cells, labels, heuristic, source, p1, p2, n, kk = (x[keep] for x in (cells, labels, heuristic, source, p1, p2, n, kk))
        m_child = self.expected_answer_size(node.children, node.received_from, cells, order)
        m_hat = m_child.sum(axis=1)
        bytes_hat = self.expected_bytes(node, m_child, order)
        gain = beta_variance_gain(n, kk, m_hat)
        parent_q, parent_is = self.parent_stats(node, cells, labels, source, p1, p2, need_parent_q, k)
        eligible = (n + m_hat) >= float(p.n_min) * float(self.ask_margin)
        self.n_eligible += int(eligible.sum())
        return CandidateTable(cell=cells, label=labels, source=source, heuristic=heuristic, n=n, k=kk, m_child=m_child,
                              m_hat=m_hat, bytes_hat=bytes_hat, gain=gain, parent_q=parent_q, parent_is=parent_is,
                              eligible=eligible)

    # ---- selection (policy specific) -------------------------------------
    def select(self, table: CandidateTable) -> np.ndarray:
        raise NotImplementedError

    def needs_parent_q(self) -> bool:
        return False

    # ---- interface -------------------------------------------------------
    def propose(self, node, round_: int) -> list[tuple[int, int, float, str]]:
        self.n_calls += 1
        if not node.children:
            return []
        table = self.build_table(node, round_, need_parent_q=self.needs_parent_q())
        if table is None or len(table) == 0:
            return []
        idx = self.select(table)
        if len(idx) == 0:
            return []
        out: list[tuple[int, int, float, str]] = []
        asked = self._asked_by_node.setdefault(node.unit_id, set())
        for i in idx:
            i = int(i)
            trigger = SOURCE_MARGINAL if table.source[i] == SOURCE_MARGINAL else self.name
            cell, label, gain = int(table.cell[i]), int(table.label[i]), float(table.gain[i])
            out.append((cell, label, gain, trigger))
            asked.add(cell)
            self.n_proposed += 1
            self.n_by_trigger[trigger] = self.n_by_trigger.get(trigger, 0) + 1
            self.n_by_layer[node.layer] = self.n_by_layer.get(node.layer, 0) + 1
            self.expected_bytes_total += float(table.bytes_hat[i])
            self.expected_gain_total += gain
            if len(self.log) < LOG_LIMIT:
                self.log.append({
                    "round": int(round_), "unit": node.unit_id, "layer": node.layer, "cell": cell, "label": label,
                    "trigger": trigger, "policy": self.name, "expected_gain": gain,
                    "gain_per_byte": gain / max(float(table.bytes_hat[i]), 1.0),
                    "expected_bytes": float(table.bytes_hat[i]), "expected_answer_size": float(table.m_hat[i]),
                    "n": int(table.n[i]), "k": int(table.k[i]), "parent_q": float(table.parent_q[i]),
                    "parent_is": float(table.parent_is[i]) if np.isfinite(table.parent_is[i]) else None,
                })
        return out


# --------------------------------------------------------------------------
# policies
# --------------------------------------------------------------------------
class ConfidenceQuestionPolicy(ScoredQuestionPolicy):
    """Ask only when the candidate's parents are uncertain: all parents suggestive (q <= 0.2) and some
    parent either not significant (fdr_q < q <= 0.2) or thinly supported (IS < 2 x support_min)."""
    name = "confidence"

    def needs_parent_q(self) -> bool:
        return True

    def uncertain(self, table: CandidateTable) -> np.ndarray:
        p = self.policy
        q = table.parent_q
        return (q <= CONFIDENCE_Q_HI) & ((q > float(p.fdr_q)) | (table.parent_is < 2.0 * float(p.support_min)))

    def select(self, table: CandidateTable) -> np.ndarray:
        ok = table.eligible & self.uncertain(table)
        idx = np.flatnonzero(ok)
        if len(idx) == 0:
            return idx
        order_ = np.lexsort((-table.heuristic[idx], -table.gain[idx]))
        return idx[order_][: max(int(self.policy.question_budget), 0)]


class EIGQuestionPolicy(ScoredQuestionPolicy):
    """Expected information gain per expected byte, top `question_budget` per node-round."""
    name = "eig"

    @staticmethod
    def density(table: CandidateTable) -> np.ndarray:
        return table.gain / np.maximum(table.bytes_hat, 1.0)

    def select(self, table: CandidateTable) -> np.ndarray:
        idx = np.flatnonzero(table.eligible)
        if len(idx) == 0:
            return idx
        d = self.density(table)
        order_ = np.lexsort((-table.heuristic[idx], -d[idx]))
        return idx[order_][: max(int(self.policy.question_budget), 0)]


class BudgetQuestionPolicy(EIGQuestionPolicy):
    """EIG ranking, greedy under `question_byte_budget` expected bytes per node-round; stops when nothing
    else fits."""
    name = "budget"

    def select(self, table: CandidateTable) -> np.ndarray:
        idx = np.flatnonzero(table.eligible)
        if len(idx) == 0:
            return idx
        d = self.density(table)
        order_ = np.lexsort((-table.heuristic[idx], -d[idx]))
        idx = idx[order_]
        budget = float(self.policy.question_byte_budget)
        if budget <= 0:
            return idx[:0]
        cost = table.bytes_hat[idx]
        chosen: list[int] = []
        remaining = budget
        min_cost = float(cost.min())
        for i, c in zip(idx, cost):
            if remaining < min_cost:
                break
            if c <= remaining:
                chosen.append(int(i))
                remaining -= float(c)
        return np.array(chosen, dtype=np.int64)


POLICIES: dict[str, type[ScoredQuestionPolicy]] = {
    "confidence": ConfidenceQuestionPolicy,
    "eig": EIGQuestionPolicy,
    "budget": BudgetQuestionPolicy,
}


def build_question_policy(name: str, policy, seed: int) -> QuestionPolicy:
    cls = POLICIES.get(name)
    if cls is None:
        raise ValueError(f"unknown questioning policy {name!r}; available: none, fixed, {', '.join(sorted(POLICIES))}")
    return cls(policy, seed)
