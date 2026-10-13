"""Measurement (``E2E-INGEST.md`` section 10, S7): the part the end-to-end job needs now, the simulator's two columns
beside the path's discovery score (criterion 9). The per-hop numbers at the three node counts, the rate sweep and the
scaling table (criterion 5) belong to the measure job and are not built here yet.

From the pinned simulator this module imports ``runner.build_world`` (the same ``build_org``, ``build_corpus``,
``make_gold`` calls the world adapter makes, ``runner.py:54-62``), ``runner.run_arch`` (``runner.py:193-244``),
``models.allocation`` (``models.py:215-217``) and ``evalm.evaluate`` (``evalm.py:101``); never ``run_matrix``, which
appends to the committed artifacts (``runner.py:253-254``; ``research/mycelic/README.md:72-79``). Nothing here writes a
file.

The output is labelled ``simulated: true`` (the tiers are assumed capability vectors, ``models.py:5``), ``synthetic:
true`` and ``measurement: false``, and carries the allocation name and the five metric fields only, never a tier
label (M24).
"""
from __future__ import annotations

import time
from typing import Any

from research.mycelic import evalm, runner
from research.mycelic.models import allocation

__all__ = ["ALLOCATION", "ARCHITECTURES", "METRICS", "SIMULATOR_BUDGET_SECONDS", "simulator_columns"]

ALLOCATION = "back-loaded"
ARCHITECTURES = ("H_mycelic_full", "A2_chunked_ctx")
METRICS = ("discovery_recall", "rare_signal_recall", "decoy_acceptance_all", "recall_at_100", "compute_units")
#: Section 10: the simulator's columns run under a 10-minute budget; past it they are null with ``reason: budget``,
#: which c9 accepts for the 2,000-user world only.
SIMULATOR_BUDGET_SECONDS = 600.0


def simulator_columns(target_users: int, seed: int, *,
                      budget_seconds: float = SIMULATOR_BUDGET_SECONDS) -> tuple[dict[str, Any] | None, str | None]:
    """The simulator's ``H_mycelic_full`` and ``A2_chunked_ctx`` on the same world (``build_world(target_users,
    seed)``), under the committed calibration, as ``(columns, reason)``: the columns, or ``None`` with ``reason``
    ``"budget"`` when the budget ran out before both architectures were evaluated."""
    t0 = time.perf_counter()
    world = runner.build_world(target_users, seed)
    alloc = allocation(ALLOCATION)
    architectures: dict[str, dict[str, Any]] = {}
    for name in ARCHITECTURES:
        if time.perf_counter() - t0 > budget_seconds:
            return None, "budget"
        t1 = time.perf_counter()
        result = runner.run_arch(name, world, alloc, seed)
        metrics = evalm.evaluate(world.corpus, world.gold, result)
        architectures[name] = {**{k: float(metrics[k]) for k in METRICS}, "seconds": time.perf_counter() - t1}
    if time.perf_counter() - t0 > budget_seconds:
        return None, "budget"
    return {"simulated": True, "synthetic": True, "measurement": False, "allocation": ALLOCATION,
            "target_users": int(target_users), "seed": int(seed), "users": len(world.org.user_ids),
            "records": int(len(world.corpus.recs)), "metrics": list(METRICS), "architectures": architectures,
            "seconds": time.perf_counter() - t0, "budget_seconds": float(budget_seconds),
            "note": "the simulator's discovery_recall matches reported hypotheses through its own extraction and "
                    "chain checks; the path's score is at (anchor, chain) granularity with no temporal-order check "
                    "and the lexical judge; the two are not the same metric"}, None
