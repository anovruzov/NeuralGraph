"""Vocabulary / cell-index invariants (DESIGN.md §2)."""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pytest

from mycelic_bench.vocab import (
    ATTRIBUTES, LABELS, N_ATTR, N_LABELS, N_VALUES, VALUES, CellIndex, cell_index, labels_to_mask, mask_matrix,
    mask_to_labels,
)


def random_cell(rng: np.random.Generator, order: int) -> list[tuple[int, int]]:
    attrs = sorted(int(a) for a in rng.choice(N_ATTR, size=order, replace=False))
    return [(a, int(rng.integers(0, N_VALUES[a]))) for a in attrs]


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_encode_decode_roundtrip(order: int) -> None:
    ci = cell_index()
    rng = np.random.default_rng(order)
    for _ in range(200):
        cell = random_cell(rng, order)
        flat = ci.encode(cell)
        assert ci.order_base[order] <= flat < ci.order_base[order] + ci.order_size[order]
        assert ci.decode(flat) == tuple(cell)
        assert int(ci.order_of(np.array([flat]))[0]) == order
        # order of attributes given to encode must not matter
        assert ci.encode(list(reversed(cell))) == flat
        # dict / string helpers roundtrip
        d = ci.cell_to_dict(flat)
        assert ci.cell_from_dict(d) == flat
        assert all(f"{a}={v}" in ci.cell_to_str(flat) for a, v in d.items())


def test_ids_are_unique_and_cover_every_cell() -> None:
    ci = cell_index()
    total = 0
    for k in range(1, 5):
        n_cells = sum(int(np.prod(N_VALUES[list(c)])) for c in combinations(range(N_ATTR), k))
        assert ci.order_size[k] == n_cells
        total += n_cells
    assert ci.total == total
    # vectorised decode agrees with scalar decode
    rng = np.random.default_rng(7)
    ids = np.array([ci.encode(random_cell(rng, 3)) for _ in range(50)])
    combo_idx, vals = ci.decode_many(ids, 3)
    for i, flat in enumerate(ids):
        pairs = ci.decode(int(flat))
        assert tuple(a for a, _ in pairs) == ci.combos[3][int(combo_idx[i])]
        assert [v for _, v in pairs] == vals[i].tolist()


@pytest.mark.parametrize("order", [2, 3, 4])
def test_sub_ids_are_subsets_of_lower_order(order: int) -> None:
    ci = cell_index()
    rng = np.random.default_rng(100 + order)
    ids = np.array([ci.encode(random_cell(rng, order)) for _ in range(100)])
    subs = ci.sub_ids(ids, order)
    assert subs.shape == (100, order)
    for i, flat in enumerate(ids):
        parent = set(ci.decode(int(flat)))
        seen = set()
        for s in subs[i]:
            child = set(ci.decode(int(s)))
            assert len(child) == order - 1
            assert child < parent
            assert int(ci.order_of(np.array([s]))[0]) == order - 1
            seen.add(frozenset(child))
        assert len(seen) == order   # each attribute dropped exactly once


def test_sub_ids_match_ids_for_rows() -> None:
    """The order-(k-1) ids of a row must contain every sub-cell of any order-k cell of that row."""
    ci = cell_index()
    rng = np.random.default_rng(3)
    rows = np.stack([rng.integers(0, N_VALUES[a], size=20) for a in range(N_ATTR)], axis=1)
    for k in (2, 3, 4):
        ids_k = ci.ids_for_rows(rows, k)
        ids_km1 = ci.ids_for_rows(rows, k - 1)
        for r in range(len(rows)):
            subs = ci.sub_ids(ids_k[r], k)
            assert set(subs.ravel().tolist()) <= set(ids_km1[r].tolist())


def test_order_one_has_no_sub_ids() -> None:
    ci = cell_index()
    ids = np.array([ci.encode([(0, 1)]), ci.encode([(4, 2)])])
    assert ci.sub_ids(ids, 1).shape == (2, 0)


@pytest.mark.parametrize("order", [1, 2, 3])
def test_super_ids_contain_the_cell(order: int) -> None:
    ci = cell_index()
    rng = np.random.default_rng(200 + order)
    for _ in range(30):
        cell = random_cell(rng, order)
        flat = ci.encode(cell)
        sup = ci.super_ids(flat)
        used = {a for a, _ in cell}
        expected = sum(int(N_VALUES[a]) for a in range(N_ATTR) if a not in used)
        assert len(sup) == expected
        assert len(set(sup.tolist())) == expected
        for s in sup:
            pairs = set(ci.decode(int(s)))
            assert set(cell) < pairs and len(pairs) == order + 1
            assert int(ci.order_of(np.array([s]))[0]) == order + 1


def test_super_ids_of_max_order_is_empty() -> None:
    ci = cell_index()
    flat = ci.encode([(0, 0), (1, 0), (2, 0), (3, 0)])
    assert len(ci.super_ids(flat)) == 0


def test_ids_for_rows_shape_and_membership() -> None:
    ci = cell_index()
    rng = np.random.default_rng(11)
    rows = np.stack([rng.integers(0, N_VALUES[a], size=15) for a in range(N_ATTR)], axis=1)
    for k in (1, 2, 3, 4):
        ids = ci.ids_for_rows(rows, k)
        assert ids.shape == (15, len(list(combinations(range(N_ATTR), k))))
        for r in range(15):
            for flat in ids[r]:
                for a, v in ci.decode(int(flat)):
                    assert rows[r, a] == v


def test_ids_for_rows_masked_respects_omission() -> None:
    ci = cell_index()
    rng = np.random.default_rng(5)
    rows = np.stack([rng.integers(0, N_VALUES[a], size=12) for a in range(N_ATTR)], axis=1)
    present = rng.random((12, N_ATTR)) < 0.6
    present[0, :] = True          # fully registered row
    present[1, :] = False         # nothing registered
    for k in (1, 2, 3):
        ids, valid = ci.ids_for_rows_masked(rows, k, present)
        assert valid.shape == ids.shape
        assert valid[0].all()
        assert not valid[1].any()
        for r in range(12):
            for c, flat in enumerate(ids[r]):
                needs = {a for a, _ in ci.decode(int(flat))}
                assert valid[r, c] == all(present[r, a] for a in needs)


def test_label_mask_helpers() -> None:
    assert labels_to_mask([]) == 0
    m = labels_to_mask(["arithmetic_error", "timeout"])
    assert mask_to_labels(m) == ["arithmetic_error", "timeout"]
    assert mask_to_labels(labels_to_mask(LABELS)) == list(LABELS)
    mm = mask_matrix(np.array([0, m, (1 << N_LABELS) - 1]))
    assert mm.shape == (3, N_LABELS)
    assert mm[0].sum() == 0 and mm[2].all()
    assert mm[1].sum() == 2 and mm[1, 0] and mm[1, LABELS.index("timeout")]


def test_vocabulary_sizes_match_design() -> None:
    assert len(ATTRIBUTES) == 9 and N_ATTR == 9
    assert len(LABELS) == 18 and N_LABELS == 18
    assert len(VALUES["task_family"]) == 14
    assert all(len(set(VALUES[a])) == len(VALUES[a]) for a in ATTRIBUTES)
    assert CellIndex(max_order=2).total == cell_index().order_base[3]
