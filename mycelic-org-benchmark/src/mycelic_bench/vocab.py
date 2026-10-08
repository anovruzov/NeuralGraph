"""Closed attribute vocabulary and cell (conjunction) indexing.

A *cell* is a conjunction of (attribute, value) pairs.  Cells of order k are
indexed by a flat integer id so that count sketches can be stored as sparse
numpy arrays and pooled with `np.unique` + `np.add.at`.

Flat id layout for order k:
    id = ORDER_BASE[k] + COMBO_OFFSET[k][c] + mixed_radix(values of combo c)
where combo c is the c-th k-subset of attributes (lexicographic), and the
mixed-radix strides use each attribute's number of values.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable

import numpy as np

ATTRIBUTES: tuple[str, ...] = (
    "model_family",
    "model_version",
    "task_family",
    "context_len",
    "input_format",
    "domain",
    "language",
    "difficulty",
    "tool",
)

VALUES: dict[str, tuple[str, ...]] = {
    "model_family": ("Atlas", "Borealis", "Cirrus", "Delta", "Ember", "Fjord", "Granite", "Helix"),
    "model_version": ("v1", "v2", "v3", "v4"),
    "task_family": (
        "math_reasoning", "coding", "tool_use", "factuality", "instruction_following",
        "long_context", "vision_language", "data_analysis", "agentic_workflow", "safety",
        "hallucination_detection", "retrieval", "formatting", "latency_sensitive",
    ),
    "context_len": ("short", "medium", "long", "very_long"),
    "input_format": ("prose", "table", "code", "json", "image", "multi_turn", "spreadsheet"),
    "domain": ("general", "finance", "medical", "legal", "science", "engineering", "customer_support"),
    "language": ("en", "es", "zh", "de", "ja", "fr"),
    "difficulty": ("easy", "medium", "hard"),
    "tool": ("none", "calculator", "search", "code_interpreter", "browser", "database"),
}

LABELS: tuple[str, ...] = (
    "arithmetic_error", "unit_conversion_error", "table_misread", "hallucinated_fact",
    "hallucinated_citation", "wrong_tool_call", "tool_output_ignored", "format_violation",
    "instruction_ignored", "truncated_output", "context_loss", "unsafe_compliance",
    "over_refusal", "stale_knowledge", "retrieval_miss", "off_by_one", "timeout",
    "visual_misgrounding",
)

N_ATTR = len(ATTRIBUTES)
N_LABELS = len(LABELS)
ATTR_INDEX = {a: i for i, a in enumerate(ATTRIBUTES)}
LABEL_INDEX = {l: i for i, l in enumerate(LABELS)}
N_VALUES = np.array([len(VALUES[a]) for a in ATTRIBUTES], dtype=np.int64)
VALUE_INDEX = {a: {v: i for i, v in enumerate(VALUES[a])} for a in ATTRIBUTES}
MAX_ORDER = 4

# Sensitive content kinds and canary format
SENSITIVE_KINDS: tuple[str, ...] = (
    "pii", "employee_name", "customer_id", "internal_secret", "code_snippet",
    "credential", "financial", "hr", "unreleased_product",
)
CANARY_PREFIX = "CANARY-"


class CellIndex:
    """Precomputed flat indexing for cells of order 1..MAX_ORDER."""

    def __init__(self, max_order: int = MAX_ORDER) -> None:
        self.max_order = max_order
        self.combos: dict[int, list[tuple[int, ...]]] = {}
        self.combo_offset: dict[int, np.ndarray] = {}
        self.combo_size: dict[int, np.ndarray] = {}
        self.strides: dict[int, np.ndarray] = {}   # [n_combos, N_ATTR] stride per attribute (0 if absent)
        self.order_base: dict[int, int] = {}
        self.order_size: dict[int, int] = {}
        base = 0
        for k in range(1, max_order + 1):
            combos = list(combinations(range(N_ATTR), k))
            sizes = np.array([int(np.prod(N_VALUES[list(c)])) for c in combos], dtype=np.int64)
            offsets = np.concatenate([[0], np.cumsum(sizes)[:-1]]).astype(np.int64)
            strides = np.zeros((len(combos), N_ATTR), dtype=np.int64)
            for ci, c in enumerate(combos):
                s = 1
                for a in reversed(c):
                    strides[ci, a] = s
                    s *= int(N_VALUES[a])
            self.combos[k] = combos
            self.combo_offset[k] = offsets
            self.combo_size[k] = sizes
            self.strides[k] = strides
            self.order_base[k] = base
            self.order_size[k] = int(sizes.sum())
            base += self.order_size[k]
        self.total = base
        # reverse lookup: flat id -> order
        self._order_bounds = np.array([self.order_base[k] for k in range(1, max_order + 1)] + [base])

    # ---- encoding --------------------------------------------------------
    def ids_for_rows(self, rows: np.ndarray, order: int) -> np.ndarray:
        """Flat ids of all order-`order` cells for each attribute row.

        rows: int array [N, N_ATTR]. Returns int64 [N, n_combos(order)].
        """
        rows = np.asarray(rows, dtype=np.int64)
        return (rows @ self.strides[order].T) + self.combo_offset[order] + self.order_base[order]

    def ids_for_rows_masked(self, rows: np.ndarray, order: int, present: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Like ids_for_rows but returns a validity mask: a cell is valid only
        if every attribute in its combo is present (present: bool [N, N_ATTR])."""
        ids = self.ids_for_rows(rows, order)
        combo_mask = (self.strides[order] > 0)  # [C, N_ATTR]
        valid = ~((~present.astype(bool))[:, None, :] & combo_mask[None, :, :]).any(axis=2)
        return ids, valid

    def encode(self, cell: Iterable[tuple[int, int]]) -> int:
        pairs = sorted(cell)
        k = len(pairs)
        attrs = tuple(a for a, _ in pairs)
        ci = self.combos[k].index(attrs)
        idx = 0
        for a, v in pairs:
            idx += v * int(self.strides[k][ci, a])
        return int(self.order_base[k] + self.combo_offset[k][ci] + idx)

    # ---- decoding --------------------------------------------------------
    def order_of(self, ids: np.ndarray) -> np.ndarray:
        ids = np.asarray(ids, dtype=np.int64)
        return np.searchsorted(self._order_bounds, ids, side="right").astype(np.int64)

    def decode(self, flat_id: int) -> tuple[tuple[int, int], ...]:
        k = int(self.order_of(np.array([flat_id]))[0])
        rel = flat_id - self.order_base[k]
        ci = int(np.searchsorted(self.combo_offset[k], rel, side="right") - 1)
        rel -= int(self.combo_offset[k][ci])
        combo = self.combos[k][ci]
        vals = []
        for a in combo:
            s = int(self.strides[k][ci, a])
            v = rel // s
            rel -= v * s
            vals.append((a, int(v)))
        return tuple(vals)

    def decode_many(self, ids: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
        """Vectorised decode for ids all of the same order.
        Returns (combo_idx [N], values [N, order]) with values in combo attribute order."""
        ids = np.asarray(ids, dtype=np.int64)
        rel = ids - self.order_base[order]
        ci = np.searchsorted(self.combo_offset[order], rel, side="right") - 1
        rel = rel - self.combo_offset[order][ci]
        combos = np.array(self.combos[order], dtype=np.int64)  # [C, order]
        attrs = combos[ci]  # [N, order]
        st = self.strides[order][ci]  # [N, N_ATTR]
        vals = np.zeros((len(ids), order), dtype=np.int64)
        for j in range(order):
            s = st[np.arange(len(ids)), attrs[:, j]]
            v = rel // s
            rel = rel - v * s
            vals[:, j] = v
        return ci, vals

    def sub_ids(self, ids: np.ndarray, order: int) -> np.ndarray:
        """For each order-k id, the k sub-cell ids of order k-1 (drop one attribute).
        Returns int64 [N, order]. For order 1 returns an empty [N, 0] array."""
        ids = np.asarray(ids, dtype=np.int64)
        if order == 1:
            return np.zeros((len(ids), 0), dtype=np.int64)
        ci, vals = self.decode_many(ids, order)
        combos = np.array(self.combos[order], dtype=np.int64)
        attrs = combos[ci]  # [N, order]
        rows = np.zeros((len(ids), N_ATTR), dtype=np.int64)
        rows[np.arange(len(ids))[:, None], attrs] = vals
        sub_table = self._sub_combo_table(order)          # [C_k, order] -> sub combo index
        sub_order = order - 1
        out = np.zeros((len(ids), order), dtype=np.int64)
        for j in range(order):
            sub_ci = sub_table[ci, j]
            st = self.strides[sub_order][sub_ci]
            out[:, j] = (rows * st).sum(axis=1) + self.combo_offset[sub_order][sub_ci] + self.order_base[sub_order]
        return out

    def _sub_combo_table(self, order: int) -> np.ndarray:
        cache = getattr(self, "_sub_tables", None)
        if cache is None:
            cache = self._sub_tables = {}
        if order not in cache:
            key_to_ci = {c: i for i, c in enumerate(self.combos[order - 1])}
            table = np.zeros((len(self.combos[order]), order), dtype=np.int64)
            for ci, combo in enumerate(self.combos[order]):
                for j in range(order):
                    table[ci, j] = key_to_ci[tuple(a for jj, a in enumerate(combo) if jj != j)]
            cache[order] = table
        return cache[order]

    def super_ids(self, cell: int, order_up: int = 1) -> np.ndarray:
        """All cells of order k+1 that contain this cell (one extra attribute, any value)."""
        pairs = dict(self.decode(int(cell)))
        out = []
        if len(pairs) >= self.max_order:
            return np.zeros(0, dtype=np.int64)
        for a in range(N_ATTR):
            if a in pairs:
                continue
            for v in range(int(N_VALUES[a])):
                out.append(self.encode(list(pairs.items()) + [(a, v)]))
        return np.array(sorted(out), dtype=np.int64)

    def cell_to_str(self, flat_id: int) -> str:
        return " & ".join(f"{ATTRIBUTES[a]}={VALUES[ATTRIBUTES[a]][v]}" for a, v in self.decode(flat_id))

    def cell_from_dict(self, d: dict[str, str]) -> int:
        return self.encode([(ATTR_INDEX[a], VALUE_INDEX[a][v]) for a, v in d.items()])

    def cell_to_dict(self, flat_id: int) -> dict[str, str]:
        return {ATTRIBUTES[a]: VALUES[ATTRIBUTES[a]][v] for a, v in self.decode(flat_id)}


_CELL_INDEX: CellIndex | None = None


def cell_index() -> CellIndex:
    global _CELL_INDEX
    if _CELL_INDEX is None:
        _CELL_INDEX = CellIndex()
    return _CELL_INDEX


def labels_to_mask(labels: Iterable[str]) -> int:
    m = 0
    for l in labels:
        m |= 1 << LABEL_INDEX[l]
    return m


def mask_to_labels(mask: int) -> list[str]:
    return [LABELS[i] for i in range(N_LABELS) if mask >> i & 1]


def mask_matrix(masks: np.ndarray) -> np.ndarray:
    """uint32 masks [N] -> bool [N, N_LABELS]."""
    masks = np.asarray(masks, dtype=np.int64)
    return ((masks[:, None] >> np.arange(N_LABELS)[None, :]) & 1).astype(bool)
