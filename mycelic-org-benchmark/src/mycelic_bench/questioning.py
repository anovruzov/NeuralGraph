"""Question policies (active discovery) interface.

A `QuestionPolicy.propose(node, round_) -> list[(cell, label, expected_gain, trigger)]`
returns bounded questions the node sends to its children via `UnitNode.ask`.
Children answer with a one-cell sketch (exact counts from local evidence);
the asker pools the answer into its cumulative sketch so the next search
can test the higher-order cell.  Policies: none | fixed | confidence | eig | budget.

The trunk ships `fixed` (top-Q candidate conjunctions from pairs of
compatible accepted claims); the questioning task adds the rest in
`questioning_impl.py`.
"""

from __future__ import annotations

import numpy as np

from .sketch import COL_K0, COL_N
from .vocab import cell_index


class QuestionPolicy:
    name = "none"

    def __init__(self, policy, seed: int) -> None:
        self.policy = policy
        self.rng = np.random.default_rng(seed)

    def candidate_cells(self, node) -> list[tuple[int, int, float]]:
        """Union cells (order+1) from pairs of accepted claims sharing a label with compatible attributes,
        plus super-cells of accepted order-k claims whose pooled counts are already present."""
        ci = cell_index()
        p = self.policy
        acc = [c for c in node.claims.values() if c.status == "accepted" and c.sign > 0]
        by_label: dict[int, list] = {}
        for c in acc:
            by_label.setdefault(c.label, []).append(c)
        out: dict[tuple[int, int], float] = {}
        for label, cs in by_label.items():
            for i in range(len(cs)):
                for j in range(i + 1, len(cs)):
                    a, b = dict(ci.decode(cs[i].cell)), dict(ci.decode(cs[j].cell))
                    if any(k in b and b[k] != v for k, v in a.items()):
                        continue
                    merged = {**a, **b}
                    if len(merged) > p.max_order_questions or len(merged) == len(a) or len(merged) == len(b):
                        continue
                    cell = ci.encode(list(merged.items()))
                    cnt = node.cumulative.lookup(np.array([cell]))[0]
                    if cnt[COL_N] >= p.n_min:
                        continue   # already testable
                    gain = float(abs(cs[i].effect) + abs(cs[j].effect)) * (1 - cs[i].q_value) * (1 - cs[j].q_value)
                    key = (cell, label)
                    out[key] = max(out.get(key, 0.0), gain)
        return [(cell, label, gain) for (cell, label), gain in out.items()]

    def propose(self, node, round_: int) -> list[tuple[int, int, float, str]]:
        return []


class FixedQuestionPolicy(QuestionPolicy):
    name = "fixed"

    def propose(self, node, round_: int) -> list[tuple[int, int, float, str]]:
        cands = self.candidate_cells(node)
        asked = {(q.cell, q.label) for q in node.questions.values()}
        cands = [c for c in cands if (c[0], c[1]) not in asked]
        cands.sort(key=lambda t: -t[2])
        return [(cell, label, gain, "fixed") for cell, label, gain in cands[: self.policy.question_budget]]


def build_question_policy(name: str, policy, seed: int) -> QuestionPolicy:
    if name in ("none", None):
        return QuestionPolicy(policy, seed)
    if name == "fixed":
        return FixedQuestionPolicy(policy, seed)
    from .questioning_impl import build_question_policy as _impl
    return _impl(name, policy, seed)
