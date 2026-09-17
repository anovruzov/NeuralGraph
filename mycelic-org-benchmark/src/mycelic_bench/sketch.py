"""Sparse count sketches over cells.

A `Sketch` is the bounded abstraction that travels upward.  It contains, for
each observed cell id: n (matching interactions), k[18] (label counts), and
distinct-source counts (workers, teams, departments, regions).  Pooling adds
them, which is exact only for the layers at or below the pooled producers'
layer (those units are disjoint); a node pooling its children restores the
columns for its own layer and above (`hierarchy.UnitNode._fix_distinct`).

Columns of `counts`:  0: n, 1..18: label counts, 19: distinct_workers,
20: distinct_teams, 21: distinct_departments, 22: distinct_regions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

from .vocab import N_LABELS, cell_index, mask_matrix

COL_N = 0
COL_K0 = 1
COL_DW = 1 + N_LABELS
COL_DT = COL_DW + 1
COL_DD = COL_DW + 2
COL_DR = COL_DW + 3
N_COLS = COL_DW + 4


@dataclass
class Sketch:
    producer_id: str
    layer: str
    round: int
    ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    counts: np.ndarray = field(default_factory=lambda: np.zeros((0, N_COLS), dtype=np.int64))
    max_order: int = 3

    # ---- construction ----------------------------------------------------
    @staticmethod
    def from_interactions(
        producer_id: str,
        layer: str,
        round: int,
        rows: np.ndarray,
        label_masks: np.ndarray,
        worker_idx: np.ndarray,
        team_idx: np.ndarray,
        dept_idx: np.ndarray,
        region_idx: np.ndarray,
        max_order: int = 3,
        present: np.ndarray | None = None,
        weights: np.ndarray | None = None,
    ) -> "Sketch":
        """Build a sketch from attribute rows (int [N, 9]) and label masks [N].

        `present` (bool [N, 9]) marks attributes the producer actually
        registered; cells needing an absent attribute are skipped (the
        simulated-SLM omission channel).  `weights` (0/1 per interaction)
        exclude deduplicated copies from counts but not from ids.
        """
        ci = cell_index()
        n = len(rows)
        if n == 0:
            return Sketch(producer_id, layer, round, max_order=max_order)
        lab = mask_matrix(label_masks).astype(np.int64)
        w = np.ones(n, dtype=np.int64) if weights is None else weights.astype(np.int64)
        all_ids = []
        all_rows = []
        for order in range(1, max_order + 1):
            if present is None:
                ids = ci.ids_for_rows(rows, order)
                valid = np.ones_like(ids, dtype=bool)
            else:
                ids, valid = ci.ids_for_rows_masked(rows, order, present)
            valid &= (w[:, None] > 0)
            rr, cc = np.nonzero(valid)
            all_ids.append(ids[rr, cc])
            all_rows.append(rr)
        flat_ids = np.concatenate(all_ids)
        flat_rows = np.concatenate(all_rows)
        uniq, inv = np.unique(flat_ids, return_inverse=True)
        m = len(uniq)
        counts = np.zeros((m, N_COLS), dtype=np.int64)
        counts[:, COL_N] = np.bincount(inv, minlength=m)
        for l in range(N_LABELS):
            counts[:, COL_K0 + l] = np.bincount(inv, weights=lab[flat_rows, l], minlength=m).astype(np.int64)
        for col, src in ((COL_DW, worker_idx), (COL_DT, team_idx), (COL_DD, dept_idx), (COL_DR, region_idx)):
            key = inv.astype(np.int64) * (int(src.max()) + 1) + src[flat_rows].astype(np.int64)
            uk = np.unique(key)
            counts[:, col] = np.bincount(uk // (int(src.max()) + 1), minlength=m)
        return Sketch(producer_id, layer, round, uniq, counts, max_order)

    @staticmethod
    def pool(sketches: list["Sketch"], producer_id: str, layer: str, round: int, max_order: int = 3) -> "Sketch":
        parts = [s for s in sketches if len(s.ids)]
        if not parts:
            return Sketch(producer_id, layer, round, max_order=max_order)
        if len(parts) == 1:
            s0 = parts[0]
            return Sketch(producer_id, layer, round, s0.ids.copy(), s0.counts.copy(), max_order)
        ids = np.concatenate([s.ids for s in parts])
        counts = np.concatenate([s.counts for s in parts])
        order = np.argsort(ids, kind="stable")
        ids_s = ids[order]
        starts = np.flatnonzero(np.concatenate([[True], ids_s[1:] != ids_s[:-1]]))
        pooled = np.add.reduceat(counts[order], starts, axis=0)
        return Sketch(producer_id, layer, round, ids_s[starts], pooled, max_order)

    # ---- queries ---------------------------------------------------------
    def lookup(self, ids: np.ndarray) -> np.ndarray:
        """Counts for the given ids (zeros for absent cells)."""
        ids = np.asarray(ids, dtype=np.int64)
        out = np.zeros((len(ids), N_COLS), dtype=np.int64)
        if len(self.ids) == 0 or len(ids) == 0:
            return out
        pos = np.searchsorted(self.ids, ids)
        pos = np.clip(pos, 0, len(self.ids) - 1)
        hit = self.ids[pos] == ids
        out[hit] = self.counts[pos[hit]]
        return out

    def total_n(self) -> int:
        """Number of interactions = n of any order-1 cell family summed over one attribute."""
        if len(self.ids) == 0:
            return 0
        ci = cell_index()
        first_attr_ids = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
        return int(self.lookup(first_attr_ids)[:, COL_N].sum())

    def restrict(self, max_order: int | None = None, keep_ids: np.ndarray | None = None, min_n: int = 0) -> "Sketch":
        ci = cell_index()
        mask = np.ones(len(self.ids), dtype=bool)
        if max_order is not None:
            mask &= ci.order_of(self.ids) <= max_order
        if min_n > 0:
            # keep order-1 always (needed as baselines), suppress small higher-order cells
            mask &= (self.counts[:, COL_N] >= min_n) | (ci.order_of(self.ids) == 1)
        if keep_ids is not None:
            mask &= np.isin(self.ids, keep_ids) | (ci.order_of(self.ids) == 1)
        return Sketch(self.producer_id, self.layer, self.round, self.ids[mask], self.counts[mask],
                      max_order if max_order is not None else self.max_order)

    def filter_orders(self, orders: set[int]) -> "Sketch":
        ci = cell_index()
        mask = np.isin(ci.order_of(self.ids), list(orders))
        return Sketch(self.producer_id, self.layer, self.round, self.ids[mask], self.counts[mask], self.max_order)

    # ---- wire size -------------------------------------------------------
    def wire_bytes(self) -> int:
        """Bytes of a compact delta encoding: per cell a 3-byte id (delta-coded
        against the sorted previous id), 1-2 bytes for n, 1 byte per non-zero
        label count (+1 index byte), and 4 x 1 byte source-count increments."""
        if len(self.ids) == 0:
            return 16
        nz = (self.counts[:, COL_K0:COL_K0 + N_LABELS] > 0).sum()
        big = (self.counts[:, COL_N] > 255).sum()
        return int(16 + len(self.ids) * (3 + 1 + 4) + big + nz * 2)

    def to_json(self) -> str:
        return json.dumps({
            "producer_id": self.producer_id, "layer": self.layer, "round": self.round,
            "cells": {int(i): c.tolist() for i, c in zip(self.ids, self.counts)},
        }, separators=(",", ":"))

    def __len__(self) -> int:
        return len(self.ids)
