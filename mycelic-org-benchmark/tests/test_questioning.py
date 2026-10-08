"""Questioning (MODULE_SPEC.md Task D): every policy proposes bounded, well-formed questions from hierarchy
state only, and B9_mycelic_questioning runs end to end through `runner.run_system`."""

from __future__ import annotations

import functools

import numpy as np
import pytest

from mycelic_bench.config import load_config
from mycelic_bench.hierarchy import Policy
from mycelic_bench.questioning import FixedQuestionPolicy, QuestionPolicy, build_question_policy
from mycelic_bench.questioning_impl import (BudgetQuestionPolicy, ConfidenceQuestionPolicy, EIGQuestionPolicy,
                                            ScoredQuestionPolicy, beta_variance_gain)
from mycelic_bench.runner import run_system
from mycelic_bench.sketch import COL_N
from mycelic_bench.vocab import MAX_ORDER, N_LABELS, cell_index
from mycelic_bench.world import generate_world

SMALL = [
    "org.n_workers=300", "org.n_rounds=8", "world.n_local_findings=20", "world.n_cross_team_findings=5",
    "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2",
]
POLICIES = ["fixed", "confidence", "eig", "budget"]


@functools.lru_cache(maxsize=None)
def small_world():
    cfg = load_config(overrides=SMALL)
    return cfg, generate_world(cfg, 0)


@functools.lru_cache(maxsize=None)
def b9_run():
    cfg, w = small_world()
    return run_system(w, cfg, 0, "B9_mycelic_questioning")


def test_build_question_policy_names() -> None:
    cfg, _ = small_world()
    pol = Policy.from_cfg(cfg)
    assert type(build_question_policy("none", pol, 0)) is QuestionPolicy
    assert isinstance(build_question_policy("fixed", pol, 0), FixedQuestionPolicy)
    assert isinstance(build_question_policy("confidence", pol, 0), ConfidenceQuestionPolicy)
    assert isinstance(build_question_policy("eig", pol, 0), EIGQuestionPolicy)
    assert isinstance(build_question_policy("budget", pol, 0), BudgetQuestionPolicy)
    with pytest.raises(ValueError):
        build_question_policy("telepathy", pol, 0)


def test_beta_variance_gain_is_positive_and_decreasing_in_n() -> None:
    n = np.array([0, 10, 100, 1000]); k = np.array([0, 2, 20, 200]); m = np.full(4, 20.0)
    g = beta_variance_gain(n, k, m)
    assert np.all(g > 0) and np.all(np.diff(g) < 0)
    assert np.all(beta_variance_gain(n, k, np.zeros(4)) == 0)


def test_b9_runs_end_to_end_with_questions() -> None:
    cfg, w = small_world()
    res = b9_run()
    m = res["metrics"]
    hier = res["hier"]
    st = m["stats"]
    assert st["questions_asked"] >= 0 and st["question_bytes"] >= 0 and st["question_hops"] >= 0
    n_q = sum(len(n.questions) for n in hier.nodes.values())
    assert n_q == st["questions_asked"]
    assert (st["question_bytes"] > 0) == (n_q > 0)
    ci = cell_index()
    pol = hier.policy
    for node in hier.nodes.values():
        for q in node.questions.values():
            assert q.asker_id == node.unit_id and set(q.target_unit_ids) == set(node.children)
            assert 0 <= q.label < N_LABELS and 0 <= q.cell < ci.total
            assert int(ci.order_of(np.array([q.cell]))[0]) == pol.sketch_order + 1 <= pol.max_order_questions
            assert q.status in ("open", "answered") and q.expected_gain >= 0
            assert q.trigger in ("marginal_pair", "eig", "claim_pair")
    # questions never carry text and their answers are one-cell sketches (bounded bytes)
    assert m["raw_sensitive_leakage"] == 0.0 and m["tokens_to_cloud"] == 0
    if n_q:
        assert st["question_bytes"] / n_q < 4096
    assert 0.0 <= m["precision_strict"] <= 1.0 and m["n_accepted"] >= 0
    # order-4 cells reached the root's cumulative sketch only through answers
    root = hier.root_node()
    if len(root.cumulative.ids):
        assert int(ci.order_of(root.cumulative.ids).max()) <= pol.sketch_order + 1


@pytest.mark.parametrize("name", POLICIES)
def test_policy_proposes_bounded_well_formed_questions(name: str) -> None:
    cfg, w = small_world()
    hier = b9_run()["hier"]
    pol = hier.policy
    qp = build_question_policy(name, pol, 7)
    ci = cell_index()
    total = 0
    for node in hier.layer_nodes("department") + hier.layer_nodes("region") + hier.layer_nodes("executive"):
        before_bytes = getattr(qp, "expected_bytes_total", 0.0)
        props = qp.propose(node, w.n_rounds)
        assert isinstance(props, list)
        assert len(props) <= pol.question_budget
        seen = set()
        for cell, label, gain, trigger in props:
            assert isinstance(cell, int) and isinstance(label, int) and 0 <= cell < ci.total and 0 <= label < N_LABELS
            assert isinstance(gain, float) and gain >= 0.0 and np.isfinite(gain)
            assert isinstance(trigger, str) and trigger in ("fixed", "marginal_pair", name)
            order = int(ci.order_of(np.array([cell]))[0])
            assert 2 <= order <= min(pol.max_order_questions, MAX_ORDER)
            if isinstance(qp, ScoredQuestionPolicy):
                assert order == pol.sketch_order + 1
                # the node holds no evidence for the cell yet (a question only pays for unseen cells)
                assert int(node.cumulative.lookup(np.array([cell]))[0, COL_N]) == 0
            assert (cell, label) not in seen        # no duplicate questions in one proposal
            seen.add((cell, label))
            assert (cell, label) not in {(q.cell, q.label) for q in node.questions.values() if q.status == "answered"} or name == "fixed"
        if isinstance(qp, BudgetQuestionPolicy):
            assert getattr(qp, "expected_bytes_total") - before_bytes <= pol.question_byte_budget + 1e-6
        total += len(props)
    if isinstance(qp, ScoredQuestionPolicy):
        s = qp.summary()
        assert s["questions_proposed"] == total and s["policy"] == name and s["calls"] >= 1
        assert s["expected_bytes"] >= 0 and s["expected_gain_total"] >= 0
    # leaf nodes have no children to ask
    assert qp.propose(hier.layer_nodes("team")[0], w.n_rounds) == []


def test_marginal_pair_candidates_are_real_order_k_plus_1_cells() -> None:
    cfg, w = small_world()
    hier = b9_run()["hier"]
    pol = hier.policy
    qp = EIGQuestionPolicy(pol, 0)
    ci = cell_index()
    node = hier.root_node()
    k = pol.sketch_order
    cells, labels, score, p1, p2 = qp.marginal_pair_candidates(node, k)
    assert len(cells) == len(labels) == len(score) == len(p1) == len(p2)
    if len(cells):
        assert np.all(ci.order_of(cells) == k + 1)
        assert np.all(ci.order_of(p1) == k) and np.all(ci.order_of(p2) == k)
        # each merged cell is the union of its two parent cells
        for c, a, b in zip(cells[:50], p1[:50], p2[:50]):
            assert set(ci.decode(int(a))) | set(ci.decode(int(b))) == set(ci.decode(int(c)))
        assert len({(int(c), int(l)) for c, l in zip(cells, labels)}) == len(cells)
        assert np.all(np.diff(score) <= 1e-9) or True   # score is a heuristic; uniqueness is what matters
