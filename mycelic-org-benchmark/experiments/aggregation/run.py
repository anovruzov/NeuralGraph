#!/usr/bin/env python3
"""Aggregation family: every system on the same worlds, plus the compression ladder.

    python experiments/aggregation/run.py --tier tier1 [--quick] [--only aggregation|compression|both]

* `aggregation.csv`  - all systems in configs/experiments.yaml (B0..B9 + ORACLE) at the tier defaults.
* `compression.csv`  - B7_mycelic under policy.compression x policy.sketch_order x cadence
  (full grid 5 x 3 x 3 = 45 points; `--quick` runs a 5-point ladder).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, cadence_dict, log, make_cfg, parse_args, resolve_systems, run_grid,  # noqa: E402
                                runner_banner, seeds_from_args)

COMPRESSIONS = ["full_sketch", "order_capped", "order2_plus_significant", "significant_cells_only", "claims_only"]
SKETCH_ORDERS = [1, 2, 3]
CADENCES = [1, 2, 4]
QUICK_LADDER = [("full_sketch", 3, 1), ("order_capped", 3, 1), ("order_capped", 2, 2), ("significant_cells_only", 3, 1),
                ("claims_only", 1, 4)]


def compression_conditions(quick: bool) -> list[Condition]:
    points = QUICK_LADDER if quick else [(c, o, k) for c in COMPRESSIONS for o in SKETCH_ORDERS for k in CADENCES]
    return [Condition(name=f"{c}-o{o}-c{k}", columns={"compression": c, "sketch_order": o, "cadence": k},
                      policy_overrides={"compression": c, "sketch_order": o, "cadence": cadence_dict(k)}) for c, o, k in points]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv, lambda ap: ap.add_argument("--only", choices=["aggregation", "compression", "both"],
                                                                 default="both", help="which sub-family to run"))
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    if args.only in ("aggregation", "both"):
        systems = resolve_systems(args, list(cfg["systems"]), cfg, "aggregation")
        conds = [Condition(name="default")]
        runner_banner("aggregation", args, cfg, seeds, systems, conds)
        run_grid("aggregation", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
                 tier=args.tier, quick=args.quick)
    if args.only in ("compression", "both"):
        systems = resolve_systems(args, ["B7_mycelic"], cfg, "compression")
        conds = compression_conditions(args.quick)
        runner_banner("compression", args, cfg, seeds, systems, conds)
        run_grid("compression", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
                 tier=args.tier, quick=args.quick)
    log("done", family="aggregation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
