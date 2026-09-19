#!/usr/bin/env python3
"""Questioning family: active discovery policies on the B9 topology.

    python experiments/questioning/run.py --tier tier1 [--quick]

policy.questioning in {none, fixed, confidence, eig, budget} x policy.sketch_order in {2, 3} on
B9_mycelic_questioning (lineage + lineage-aware detector + local SLM classifier).  The `none` point of
each sketch order runs first and is the paired baseline of the same seed; the hook then reports
questions_asked, question_bytes, question_hops (from the hierarchy stats) and
marginal_value_per_question = (recall_global + recall_cross_team - baseline) / questions_asked.
`confidence`, `eig` and `budget` need questioning_impl.py (skipped with a message when missing).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, RunContext, log, make_cfg, parse_args, resolve_systems, run_grid,  # noqa: E402
                                runner_banner, seeds_from_args)

POLICIES = ["none", "fixed", "confidence", "eig", "budget"]
ORDERS = [2, 3]
QUICK_POLICIES = ["none", "fixed", "eig"]
QUICK_ORDERS = [3]


def questioning_conditions(quick: bool) -> list[Condition]:
    pols = QUICK_POLICIES if quick else POLICIES
    orders = QUICK_ORDERS if quick else ORDERS
    out = []
    for o in orders:
        for q in pols:   # `none` first so the paired baseline exists when the others run
            out.append(Condition(name=f"q-{q}-o{o}", columns={"questioning": q, "sketch_order": o},
                                 policy_overrides={"questioning": q, "sketch_order": o}))
    return out


def _f(v: Any) -> float:
    try:
        x = float(v)
        return x if x == x else 0.0
    except Exception:
        return 0.0


def questioning_columns(ctx: RunContext) -> dict[str, Any]:
    stats = ctx.metrics.get("stats", {}) or {}
    asked = int(stats.get("questions_asked", 0) or 0)
    qbytes = int(stats.get("question_bytes", 0) or 0)
    out: dict[str, Any] = {"questioning.questions_asked": asked, "questioning.question_bytes": qbytes,
                           "questioning.question_hops": int(stats.get("question_hops", 0) or 0)}
    order = ctx.condition.columns.get("sketch_order")
    base = ctx.seed_metrics.get((f"q-none-o{order}", ctx.system))
    value = _f(ctx.metrics.get("recall_global")) + _f(ctx.metrics.get("recall_cross_team"))
    out["questioning.discovery_value"] = value
    if base is not None:
        bvalue = _f(base.get("recall_global")) + _f(base.get("recall_cross_team"))
        out["questioning.baseline_value"] = bvalue
        out["questioning.delta_value"] = value - bvalue
        out["questioning.marginal_value_per_question"] = (value - bvalue) / asked if asked else float("nan")
        out["questioning.marginal_value_per_kb"] = (value - bvalue) / (qbytes / 1024) if qbytes else float("nan")
        out["questioning.delta_bytes"] = int(ctx.metrics.get("bytes_transmitted", 0) or 0) - int(base.get("bytes_transmitted", 0) or 0)
    else:
        out["questioning.baseline_value"] = value if ctx.condition.columns.get("questioning") == "none" else float("nan")
        out["questioning.marginal_value_per_question"] = float("nan")
    hier = ctx.result.get("hier")
    if hier is not None:
        answered = sum(1 for n in hier.nodes.values() for q in n.questions.values() if q.status == "answered")
        gains = [q.expected_gain for n in hier.nodes.values() for q in n.questions.values()]
        out["questioning.questions_answered"] = answered
        out["questioning.mean_expected_gain"] = float(sum(gains) / len(gains)) if gains else float("nan")
        triggers: dict[str, int] = {}
        for n in hier.nodes.values():
            for q in n.questions.values():
                triggers[q.trigger] = triggers.get(q.trigger, 0) + 1
        for t, c in triggers.items():
            out[f"questioning.trigger.{t}"] = c
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv)
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, ["B9_mycelic_questioning"], cfg, "questioning")
    conds = questioning_conditions(args.quick)
    runner_banner("questioning", args, cfg, seeds, systems, conds)
    run_grid("questioning", systems, seeds, cfg, per_run=questioning_columns, conditions=conds, results_root=args.results,
             jobs=args.jobs, tag=args.tag, tier=args.tier, quick=args.quick)
    log("done", family="questioning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
