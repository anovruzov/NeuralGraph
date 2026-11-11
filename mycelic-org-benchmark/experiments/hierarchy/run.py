#!/usr/bin/env python3
"""Hierarchy-depth ablation: the Mycelic policy on 1/2/3/5/7-layer topologies, plus B5_flat_agents.

    python experiments/hierarchy/run.py --tier tier1 [--quick] [--budget BYTES]

Every layer count uses the same per-node policy (same top_k_claims and byte_budget for every node, so
deeper trees are not given more bandwidth per node).  `--budget 0` (default) keeps the configured
budget (unlimited unless configs/experiments.yaml says otherwise); the full grid adds one finite
equal per-node budget (`--budget`, default 131072 bytes per node per round) as a second regime.
The row's `system` column stays "B7_mycelic"; `layers` and `byte_budget` identify the point.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, log, make_cfg, parse_args, resolve_systems, run_grid, runner_banner,  # noqa: E402
                                seeds_from_args)

LAYERS = [1, 2, 3, 5, 7]
QUICK_LAYERS = [1, 3, 5]


def hierarchy_conditions(cfg: dict, quick: bool, budget: int, systems: list[str]) -> list[Condition]:
    layers = QUICK_LAYERS if quick else LAYERS
    base_budget = int(cfg["policy"].get("byte_budget", 0))
    regimes = [base_budget] if (quick or budget <= 0 or budget == base_budget) else [base_budget, int(budget)]
    out = []
    hier_systems = [s for s in systems if s != "B5_flat_agents"]
    for b in regimes:
        for L in layers:
            out.append(Condition(name=f"layers{L}-budget{b}", columns={"layers": L, "byte_budget": b},
                                 sys_cfg={"kind": "hierarchy", "layers": L, "lineage": True, "detector": "none"},
                                 policy_overrides={"byte_budget": b}, systems=hier_systems))
        if "B5_flat_agents" in systems:
            out.append(Condition(name=f"flat-budget{b}", columns={"layers": 1, "byte_budget": b}, policy_overrides={"byte_budget": b},
                                 systems=["B5_flat_agents"]))
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv, lambda ap: ap.add_argument("--budget", type=int, default=131072,
                                                                 help="finite equal per-node byte budget for the second regime (0 = skip)"))
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, ["B7_mycelic", "B5_flat_agents"], cfg, "hierarchy")
    conds = hierarchy_conditions(cfg, args.quick, args.budget, systems)
    runner_banner("hierarchy", args, cfg, seeds, systems, conds)
    run_grid("hierarchy", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
             tier=args.tier, quick=args.quick)
    log("done", family="hierarchy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
