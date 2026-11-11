#!/usr/bin/env python3
"""Contradictions family: detection and resolution quality with many planted contradiction groups.

    python experiments/contradictions/run.py --tier tier1 [--quick]

Runs B6_hier_no_lineage, B7_mycelic and B8_mycelic_security on worlds whose `world.n_contradictions`
is scaled up 3x over the tier default (tier1: 10 -> 30) unless `--set world.n_contradictions=...` is
given.  Reported metrics: contradiction_precision/recall/f1, correct/incorrect resolution rate,
unresolved_when_appropriate, time_to_detect_mean, per_shape.* (biased_positive / biased_null /
conditional).  The full grid also sweeps policy.resolve_ratio in {1.5, 2, 4} for B7.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, log, make_cfg, parse_args, resolve_systems, run_grid, runner_banner,  # noqa: E402
                                seeds_from_args)

SYSTEMS = ["B6_hier_no_lineage", "B7_mycelic", "B8_mycelic_security"]
RESOLVE_RATIOS = [1.5, 2.0, 4.0]
SCALE = 3


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv)
    cfg = make_cfg(args.tier, args.overrides)
    if not any(o.startswith("world.n_contradictions=") for o in args.overrides):
        cfg["world"]["n_contradictions"] = int(cfg["world"].get("n_contradictions", 10)) * SCALE
    n_con = int(cfg["world"]["n_contradictions"])
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, SYSTEMS, cfg, "contradictions")
    conds = [Condition(name=f"contradictions-{n_con}", columns={"n_contradictions": n_con, "resolve_ratio": cfg["policy"].get("resolve_ratio", 2.0)})]
    if not args.quick and "B7_mycelic" in systems:
        for r in RESOLVE_RATIOS:
            if float(r) == float(cfg["policy"].get("resolve_ratio", 2.0)):
                continue
            conds.append(Condition(name=f"contradictions-{n_con}-resolve{r}", columns={"n_contradictions": n_con, "resolve_ratio": r},
                                   policy_overrides={"resolve_ratio": r}, systems=["B7_mycelic"]))
    runner_banner("contradictions", args, cfg, seeds, systems, conds)
    run_grid("contradictions", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
             tier=args.tier, quick=args.quick)
    log("done", family="contradictions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
