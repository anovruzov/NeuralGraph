#!/usr/bin/env python
"""Probe for judge test J001: how well a judge that never reads the record can score, by the predicate it is asked.

    python tools/market/j001_prior_probe.py

A record-blind judge here confirms a question when its predicate is one of the K most-filed components, and refutes it
otherwise. The probe scores that judge, for every K, under two ways of drawing the negative question:

- ``uniform``: the first rule 2 of ``CHOICE-J001.md``. The negative is drawn uniformly from the pack's predicates,
  less the filed one, its twin and ``unknown_or_other``.
- ``frequency``: the amended rule 2. The negative is drawn with the frequency of filed predicates, less the filed one,
  its twin and ``unknown_or_other``.

The frequencies are the component row counts of the whole NHTSA complaint file (``nhtsa-probe.json``,
``top_components``: all makes, all years), mapped to the pack's predicates through ``mapping.json`` and
``codes.json``. They stand in for J001's 150 records and are not those records. Each record is taken to be filed under
one predicate. The figures are expectations under those counts, not a draw.

It prints, per rule and per K, the sensitivity, the specificity and the balanced accuracy, then the largest balanced
accuracy over K. No network, no model.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VEHICLES = ROOT / "docs" / "collective" / "replay" / "vehicles"
PACK = VEHICLES / "pack"
EXCLUDED = "unknown_or_other"
TWINS = (("engine_and_engine_cooling", "engine"), ("fuel_system_gasoline", "fuel_propulsion_system"),
         ("service_brakes_hydraulic", "service_brakes"))


def twin(predicate: str) -> str | None:
    for old, new in TWINS:
        if predicate == old:
            return new
        if predicate == new:
            return old
    return None


def weights() -> dict[str, int]:
    """Filed rows per specific predicate, from the whole-file component counts."""
    probe = json.loads((VEHICLES / "nhtsa-probe.json").read_text(encoding="utf-8"))
    value_map = json.loads((PACK / "mapping.json").read_text(encoding="utf-8"))["codes"][0]["value_map"]
    codes = json.loads((PACK / "codes.json").read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for component, rows in probe["complaints"]["top_components"]:
        code = value_map.get(component)
        if code and codes[code]["specific"]:
            predicate = codes[code]["predicate"]
            out[predicate] = out.get(predicate, 0) + rows
    return out


def predicates() -> list[str]:
    return sorted(json.loads((PACK / "vocabulary.json").read_text(encoding="utf-8"))["predicates"])


def negative_share(rule: str, filed: str, top: set[str], w: dict[str, int], pool: list[str]) -> float:
    """The chance that a record filed under ``filed`` gets a negative in ``top``."""
    left = [q for q in pool if q != filed and q != twin(filed)]
    if rule == "uniform":
        return sum(q in top for q in left) / len(left)
    total = sum(w.get(q, 0) for q in left)
    return sum(w.get(q, 0) for q in left if q in top) / total


def table(rule: str, w: dict[str, int], pool: list[str]) -> list[tuple[int, float, float, float]]:
    total = sum(w.values())
    order = sorted(w, key=lambda p: (-w[p], p))
    rows = []
    for k in range(1, len(order) + 1):
        top = set(order[:k])
        sensitivity = sum(w[p] for p in top) / total
        specificity = 1 - sum(w[p] / total * negative_share(rule, p, top, w, pool) for p in w)
        rows.append((k, sensitivity, specificity, (sensitivity + specificity) / 2))
    return rows


def main() -> int:
    w = weights()
    pool = [p for p in predicates() if p != EXCLUDED]
    print(f"whole-file component rows mapped to {len(w)} predicates: {sum(w.values())}")
    for rule in ("uniform", "frequency"):
        rows = table(rule, w, pool)
        print(f"\nrule {rule}: K, sensitivity, specificity, balanced accuracy")
        for k, sens, spec, ba in rows:
            print(f"{k:2d} {sens:.3f} {spec:.3f} {ba:.3f}")
        best = max(rows, key=lambda r: r[3])
        print(f"rule {rule}: largest balanced accuracy {best[3]:.3f} at K={best[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
