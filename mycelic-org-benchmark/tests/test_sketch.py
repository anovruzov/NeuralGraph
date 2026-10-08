"""Sketch invariants: counts vs brute force, pooling, lookup, restrict, wire size."""

from __future__ import annotations

import numpy as np
import pytest

from mycelic_bench.sketch import COL_DD, COL_DR, COL_DT, COL_DW, COL_K0, COL_N, N_COLS, Sketch
from mycelic_bench.vocab import N_ATTR, N_LABELS, N_VALUES, cell_index, mask_matrix


def make_rows(rng: np.random.Generator, n: int, p: float = 0.1):
    rows = np.stack([rng.integers(0, N_VALUES[a], size=n) for a in range(N_ATTR)], axis=1)
    lab = rng.random((n, N_LABELS)) < p
    masks = (lab.astype(np.int64) << np.arange(N_LABELS)).sum(axis=1)
    worker = rng.integers(0, 40, size=n)
    team = worker // 10
    dept = team // 2
    region = dept // 2
    return rows, masks, worker, team, dept, region


def brute_force(rows, masks, worker, team, dept, region, cell: int) -> np.ndarray:
    ci = cell_index()
    m = np.ones(len(rows), dtype=bool)
    for a, v in ci.decode(int(cell)):
        m &= rows[:, a] == v
    out = np.zeros(N_COLS, dtype=np.int64)
    out[COL_N] = m.sum()
    out[COL_K0:COL_K0 + N_LABELS] = mask_matrix(masks[m]).sum(axis=0)
    out[COL_DW] = len(np.unique(worker[m])) if m.any() else 0
    out[COL_DT] = len(np.unique(team[m])) if m.any() else 0
    out[COL_DD] = len(np.unique(dept[m])) if m.any() else 0
    out[COL_DR] = len(np.unique(region[m])) if m.any() else 0
    return out


def test_from_interactions_matches_brute_force() -> None:
    rng = np.random.default_rng(0)
    rows, masks, worker, team, dept, region = make_rows(rng, 300)
    sk = Sketch.from_interactions("T", "team", 0, rows, masks, worker, team, dept, region, max_order=3)
    ci = cell_index()
    assert np.all(np.diff(sk.ids) > 0)                       # sorted unique ids
    assert sk.total_n() == 300
    assert set(np.unique(ci.order_of(sk.ids)).tolist()) == {1, 2, 3}
    # every order-1..3 cell of every row is present; spot-check counts against brute force
    for r in range(0, 300, 37):
        for k in (1, 2, 3):
            assert np.isin(ci.ids_for_rows(rows[r:r + 1], k).ravel(), sk.ids).all()
    sample = rng.choice(sk.ids, size=60, replace=False)
    got = sk.lookup(sample)
    for i, cell in enumerate(sample):
        np.testing.assert_array_equal(got[i], brute_force(rows, masks, worker, team, dept, region, int(cell)))


def test_present_mask_skips_cells_needing_omitted_attributes() -> None:
    rng = np.random.default_rng(1)
    rows, masks, worker, team, dept, region = make_rows(rng, 100)
    present = np.ones((100, N_ATTR), dtype=bool)
    present[:, 4] = False     # attribute 4 never registered
    sk = Sketch.from_interactions("T", "team", 0, rows, masks, worker, team, dept, region, max_order=3, present=present)
    ci = cell_index()
    for cell in sk.ids:
        assert all(a != 4 for a, _ in ci.decode(int(cell)))
    # marginals of the other attributes are unaffected
    full = Sketch.from_interactions("T", "team", 0, rows, masks, worker, team, dept, region, max_order=1)
    keep = np.array([all(a != 4 for a, _ in ci.decode(int(c))) for c in full.ids])
    np.testing.assert_array_equal(sk.lookup(full.ids[keep]), full.counts[keep])


def test_weights_exclude_records_from_counts() -> None:
    rng = np.random.default_rng(2)
    rows, masks, worker, team, dept, region = make_rows(rng, 80)
    w = np.ones(80, dtype=np.int64); w[:20] = 0
    sk = Sketch.from_interactions("T", "team", 0, rows, masks, worker, team, dept, region, max_order=2, weights=w)
    ref = Sketch.from_interactions("T", "team", 0, rows[20:], masks[20:], worker[20:], team[20:], dept[20:], region[20:], max_order=2)
    np.testing.assert_array_equal(sk.ids, ref.ids)
    np.testing.assert_array_equal(sk.counts, ref.counts)


def test_pool_equals_brute_force_sum() -> None:
    rng = np.random.default_rng(3)
    parts = []
    all_rows, all_masks, all_w, all_t, all_d, all_r = [], [], [], [], [], []
    for p in range(3):
        rows, masks, worker, team, dept, region = make_rows(rng, 120)
        worker = worker + 1000 * p; team = team + 100 * p; dept = dept + 10 * p; region = region + 4 * p   # disjoint units
        parts.append(Sketch.from_interactions(f"T{p}", "team", 0, rows, masks, worker, team, dept, region, max_order=3))
        all_rows.append(rows); all_masks.append(masks); all_w.append(worker); all_t.append(team); all_d.append(dept); all_r.append(region)
    pooled = Sketch.pool(parts, "D", "department", 0, max_order=3)
    whole = Sketch.from_interactions("D", "department", 0, np.concatenate(all_rows), np.concatenate(all_masks),
                                     np.concatenate(all_w), np.concatenate(all_t), np.concatenate(all_d), np.concatenate(all_r), max_order=3)
    np.testing.assert_array_equal(pooled.ids, whole.ids)
    np.testing.assert_array_equal(pooled.counts, whole.counts)     # exact because units are disjoint
    # pooling is associative and ignores empty sketches
    p2 = Sketch.pool([Sketch.pool(parts[:2], "x", "l", 0), parts[2], Sketch("e", "l", 0)], "D", "department", 0)
    np.testing.assert_array_equal(p2.ids, pooled.ids)
    np.testing.assert_array_equal(p2.counts, pooled.counts)
    assert pooled.total_n() == 360
    # single-part pool is a copy, not a view
    one = Sketch.pool([parts[0]], "c", "l", 0)
    one.counts[0, COL_N] += 5
    assert parts[0].counts[0, COL_N] != one.counts[0, COL_N]


def test_lookup_absent_ids_returns_zeros() -> None:
    rng = np.random.default_rng(4)
    rows, masks, worker, team, dept, region = make_rows(rng, 50)
    sk = Sketch.from_interactions("T", "team", 0, rows, masks, worker, team, dept, region, max_order=2)
    ci = cell_index()
    absent = np.array([i for i in range(ci.order_base[3], ci.order_base[3] + 500) if i not in set(sk.ids.tolist())][:10])
    got = sk.lookup(absent)
    assert got.shape == (10, N_COLS) and not got.any()
    # far beyond the largest id, and the empty sketch
    assert not sk.lookup(np.array([ci.total + 10])).any()
    assert not Sketch("e", "l", 0).lookup(np.array([1, 2, 3])).any()
    assert sk.lookup(np.zeros(0, dtype=np.int64)).shape == (0, N_COLS)
    # mixed present / absent
    mixed = np.array([int(sk.ids[0]), int(absent[0]), int(sk.ids[-1])])
    got = sk.lookup(mixed)
    np.testing.assert_array_equal(got[0], sk.counts[0]); assert not got[1].any(); np.testing.assert_array_equal(got[2], sk.counts[-1])


def test_restrict_keeps_order_one_marginals() -> None:
    rng = np.random.default_rng(5)
    rows, masks, worker, team, dept, region = make_rows(rng, 200)
    sk = Sketch.from_interactions("T", "team", 0, rows, masks, worker, team, dept, region, max_order=3)
    ci = cell_index()
    r = sk.restrict(min_n=5)
    orders = ci.order_of(r.ids)
    o1_full = sk.ids[ci.order_of(sk.ids) == 1]
    np.testing.assert_array_equal(r.ids[orders == 1], o1_full)               # all marginals kept
    assert (r.counts[orders > 1, COL_N] >= 5).all()                           # small higher-order cells suppressed
    assert len(r.ids) < len(sk.ids)
    r2 = sk.restrict(max_order=2)
    assert ci.order_of(r2.ids).max() == 2 and r2.max_order == 2
    keep = sk.ids[ci.order_of(sk.ids) == 3][:3]
    r3 = sk.restrict(keep_ids=keep)
    assert set(r3.ids.tolist()) == set(o1_full.tolist()) | set(keep.tolist())
    f = sk.filter_orders({2})
    assert set(np.unique(ci.order_of(f.ids)).tolist()) == {2}


def test_wire_bytes_monotone() -> None:
    rng = np.random.default_rng(6)
    rows, masks, worker, team, dept, region = make_rows(rng, 400)
    prev = Sketch("e", "l", 0).wire_bytes()
    assert prev == 16
    for n in (10, 50, 100, 200, 400):
        sk = Sketch.from_interactions("T", "team", 0, rows[:n], masks[:n], worker[:n], team[:n], dept[:n], region[:n], max_order=3)
        b = sk.wire_bytes()
        assert b >= prev
        prev = b
    full = Sketch.from_interactions("T", "team", 0, rows, masks, worker, team, dept, region, max_order=3)
    assert full.restrict(max_order=2).wire_bytes() <= full.wire_bytes()
    assert full.restrict(min_n=5).wire_bytes() <= full.wire_bytes()
    assert full.restrict(max_order=1).wire_bytes() <= full.restrict(max_order=2).wire_bytes()
    assert len(full.to_json()) > 0 and len(full) == len(full.ids)


def test_empty_inputs() -> None:
    sk = Sketch.from_interactions("T", "team", 0, np.zeros((0, N_ATTR), dtype=np.int64), np.zeros(0, dtype=np.int64),
                                  np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
    assert len(sk) == 0 and sk.total_n() == 0
    assert len(Sketch.pool([sk, sk], "p", "l", 0)) == 0
