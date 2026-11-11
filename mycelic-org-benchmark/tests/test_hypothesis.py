"""Shared hypothesis search: FDR control on resampled nulls, power on planted effects, two-sided detection."""

from __future__ import annotations

import numpy as np

from mycelic_bench.hypothesis import bh_qvalues, null_evidence, outside_counts, rate_test, search
from mycelic_bench.sketch import COL_K0, COL_N, Sketch
from mycelic_bench.vocab import N_ATTR, N_LABELS, N_VALUES, cell_index

P0 = 0.03
EFFECT_MIN = 0.08


def random_rows(rng: np.random.Generator, n: int) -> np.ndarray:
    return np.stack([rng.integers(0, N_VALUES[a], size=n) for a in range(N_ATTR)], axis=1)


def force_matches(rng, rows, pairs, n_match):
    """Rewrite random rows so that at least `n_match` rows satisfy the cell."""
    m = np.ones(len(rows), dtype=bool)
    for a, v in pairs:
        m &= rows[:, a] == v
    need = n_match - int(m.sum())
    if need > 0:
        idx = rng.choice(np.flatnonzero(~m), size=need, replace=False)
        for a, v in pairs:
            rows[idx, a] = v
    m = np.ones(len(rows), dtype=bool)
    for a, v in pairs:
        m &= rows[:, a] == v
    return m


def sketch_of(rows, p_matrix, rng) -> tuple[Sketch, np.ndarray]:
    lab = rng.random(p_matrix.shape) < p_matrix
    masks = (lab.astype(np.int64) << np.arange(N_LABELS)).sum(axis=1)
    z = np.zeros(len(rows), dtype=np.int64)
    return Sketch.from_interactions("x", "team", 0, rows, masks, z, z, z, z, max_order=3), masks


def population_true(rows, p_matrix, cell: int, label: int, sign: int) -> bool:
    """Noise-free truth of a claim, mirroring evaluate.PopulationTruth: the cell's
    mean probability against the most elevated sub-marginal outside the cell."""
    ci = cell_index()
    pairs = ci.decode(int(cell))
    cm = np.ones(len(rows), dtype=bool)
    for a, v in pairs:
        cm &= rows[:, a] == v
    p_in = float(p_matrix[cm, label].mean())
    if len(pairs) == 1:
        out = ~cm
        p_out = float(p_matrix[out, label].mean()) if out.any() else 0.0
    else:
        p_out = -1.0
        for drop in range(len(pairs)):
            sub = np.ones(len(rows), dtype=bool)
            for j, (a, v) in enumerate(pairs):
                if j != drop:
                    sub &= rows[:, a] == v
            out = sub & ~cm
            if out.any():
                p_out = max(p_out, float(p_matrix[out, label].mean()))
        if p_out < 0:
            p_out = float(p_matrix[~cm, label].mean())
    return (p_in - p_out) * sign >= EFFECT_MIN / 4


def test_pure_null_accepts_almost_nothing() -> None:
    """Flat null (p=0.03 for every label): under the global null BH's FDR equals the
    probability of any rejection, so accepted claims must be (near) absent across seeds."""
    total_accepted = 0
    seeds_with_rejections = 0
    for seed in range(5):
        rng = np.random.default_rng(seed)
        rows = random_rows(rng, 2000)
        p = np.full((2000, N_LABELS), P0)
        sk, _ = sketch_of(rows, p, rng)
        c = search(sk, n_min=8, effect_min=EFFECT_MIN, fdr_q=0.05, max_order=3)
        total_accepted += len(c)
        seeds_with_rejections += int(len(c) > 0)
    assert seeds_with_rejections <= 1
    assert total_accepted <= 3


def test_fdr_on_resampled_labels_with_planted_effects() -> None:
    """Labels are resampled from the (mostly flat) null; a few strong planted effects
    guarantee accepted claims exist.  The fraction of accepted claims that are
    population-false must be <= 0.1 pooled over 5 seeds (q = 0.05)."""
    ci = cell_index()
    n_false = n_acc = 0
    for seed in range(5):
        rng = np.random.default_rng(1000 + seed)
        rows = random_rows(rng, 2000)
        p = np.full((2000, N_LABELS), P0)
        planted = []
        for j in range(4):
            attrs = sorted(int(a) for a in rng.choice(N_ATTR, size=2, replace=False))
            pairs = [(a, int(rng.integers(0, N_VALUES[a]))) for a in attrs]
            m = force_matches(rng, rows, pairs, 120)
            label = 3 * j
            p[m, label] = P0 + 0.35
            planted.append((ci.encode(pairs), label))
        sk, _ = sketch_of(rows, p, rng)
        c = search(sk, n_min=8, effect_min=EFFECT_MIN, fdr_q=0.05, max_order=3)
        assert len(c) > 0
        for i in range(len(c)):
            n_acc += 1
            if not population_true(rows, p, int(c.cell[i]), int(c.label[i]), int(c.sign[i])):
                n_false += 1
        # each planted effect is recovered exactly (they are far above threshold)
        found = {(int(cc), int(l)) for cc, l in zip(c.cell, c.label)}
        assert all(pl in found for pl in planted), (seed, planted, found)
    assert n_acc >= 20
    assert n_false / n_acc <= 0.1, (n_false, n_acc)


def test_planted_order2_effect_is_found() -> None:
    """A +0.30 effect on an order-2 cell with >= 60 matching records is found in every seed."""
    ci = cell_index()
    for seed in range(5):
        rng = np.random.default_rng(100 + seed)
        rows = random_rows(rng, 3000)
        pairs = [(0, 1), (2, 3)]
        m = force_matches(rng, rows, pairs, 60)
        assert m.sum() >= 60
        p = np.full((3000, N_LABELS), P0)
        p[m, 5] = P0 + 0.30
        sk, _ = sketch_of(rows, p, rng)
        c = search(sk, n_min=8, effect_min=EFFECT_MIN, fdr_q=0.05, max_order=2)
        hit = [(int(s), float(e), int(n)) for cc, l, s, e, n in zip(c.cell, c.label, c.sign, c.effect, c.n)
               if int(cc) == ci.encode(pairs) and int(l) == 5]
        assert hit, f"seed {seed}: planted effect missed; accepted={len(c)}"
        sign, eff, n = hit[0]
        assert sign == 1 and eff >= 0.15 and n >= 60


def test_planted_effect_survives_full_order3_family() -> None:
    """With the whole order<=3 family tested (~1e5 hypotheses) a 100-record +0.30 effect
    is still found in most seeds; BH is applied to the full family so a few misses
    at this size are expected and tolerated."""
    ci = cell_index()
    found = 0
    for seed in range(5):
        rng = np.random.default_rng(200 + seed)
        rows = random_rows(rng, 2000)
        pairs = [(0, 1), (2, 3)]
        m = force_matches(rng, rows, pairs, 100)
        p = np.full((2000, N_LABELS), P0)
        p[m, 5] = P0 + 0.30
        sk, _ = sketch_of(rows, p, rng)
        c = search(sk, n_min=8, effect_min=EFFECT_MIN, fdr_q=0.05, max_order=3)
        found += int(any(int(cc) == ci.encode(pairs) and int(l) == 5 for cc, l in zip(c.cell, c.label)))
        assert c.n_tested > 10000
    assert found >= 4


def test_two_sided_detects_reduced_cell_when_baseline_high() -> None:
    ci = cell_index()
    for seed in range(3):
        rng = np.random.default_rng(300 + seed)
        rows = random_rows(rng, 3000)
        pairs = [(0, 2), (4, 1)]
        m = force_matches(rng, rows, pairs, 80)
        p = np.full((3000, N_LABELS), P0)
        p[:, 7] = 0.5                    # high baseline for label 7
        p[m, 7] = 0.1                    # strongly reduced inside the cell
        sk, _ = sketch_of(rows, p, rng)
        two = search(sk, n_min=8, effect_min=EFFECT_MIN, fdr_q=0.05, max_order=3, two_sided=True)
        one = search(sk, n_min=8, effect_min=EFFECT_MIN, fdr_q=0.05, max_order=3, two_sided=False)
        hit = [(int(s), float(e)) for cc, l, s, e in zip(two.cell, two.label, two.sign, two.effect)
               if int(cc) == ci.encode(pairs) and int(l) == 7]
        assert hit and hit[0][0] == -1 and hit[0][1] < -0.2
        assert not any(int(cc) == ci.encode(pairs) and int(l) == 7 for cc, l in zip(one.cell, one.label))
        assert (one.sign > 0).all()


def test_search_respects_thresholds_and_restrict() -> None:
    ci = cell_index()
    rng = np.random.default_rng(9)
    rows = random_rows(rng, 2000)
    pairs = [(0, 1), (2, 3)]
    m = force_matches(rng, rows, pairs, 120)
    p = np.full((2000, N_LABELS), P0)
    p[m, 5] = P0 + 0.35
    sk, _ = sketch_of(rows, p, rng)
    cell = ci.encode(pairs)
    c = search(sk, n_min=8, effect_min=EFFECT_MIN, fdr_q=0.05, max_order=3, restrict_ids=np.array([cell]))
    assert set(c.cell.tolist()) <= {cell}
    assert len(search(sk, n_min=10**6, effect_min=EFFECT_MIN, fdr_q=0.05)) == 0
    assert len(search(sk, n_min=8, effect_min=0.99, fdr_q=0.05)) == 0
    assert len(search(Sketch("e", "l", 0))) == 0
    assert (c.n >= 8).all() and (np.abs(c.effect) >= EFFECT_MIN).all() and (c.q < 0.05).all()
    sub = c.subset(np.zeros(len(c), dtype=bool))
    assert len(sub) == 0 and sub.n_tested == c.n_tested


def test_outside_counts_for_order1_is_complement() -> None:
    rng = np.random.default_rng(10)
    rows = random_rows(rng, 500)
    p = np.full((500, N_LABELS), 0.2)
    sk, masks = sketch_of(rows, p, rng)
    ci = cell_index()
    cell = ci.encode([(0, int(rows[0, 0]))])
    n_out, k_out, ok = outside_counts(sk, np.array([cell]))
    inside = rows[:, 0] == rows[0, 0]
    assert ok[0] and n_out[0, 0] == (~inside).sum()
    assert k_out[0, 0] == ((masks[~inside] >> 0) & 1).sum()


def test_bh_and_rate_helpers() -> None:
    p = np.array([0.001, 0.01, 0.2, 0.5, 0.04])
    q = bh_qvalues(p)
    assert q.shape == p.shape and (q >= p).all() and (q <= 1).all()
    order = np.argsort(p)
    assert (np.diff(q[order]) >= -1e-12).all()      # monotone in p
    assert np.isclose(q[0], 0.005)                  # 0.001 * 5 / 1
    assert len(bh_qvalues(np.zeros(0))) == 0
    strong = rate_test(np.array([100]), np.array([40]), np.array([1000]), np.array([30]), np.array([1]))
    weak = rate_test(np.array([100]), np.array([4]), np.array([1000]), np.array([30]), np.array([1]))
    assert strong[0] < 1e-8 < weak[0]
    assert null_evidence(np.array([400]), np.array([8]), np.array([0.03]), 0.08)[0]
    assert not null_evidence(np.array([10]), np.array([0]), np.array([0.03]), 0.08)[0]
    assert not null_evidence(np.array([0]), np.array([0]), np.array([0.03]), 0.08)[0]
