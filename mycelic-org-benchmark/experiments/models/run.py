#!/usr/bin/env python3
"""Model-profile family (SYNTHETIC capability sweep, DESIGN.md §9).

    python experiments/models/run.py --tier tier1 [--quick]

models.edge_profile in {sim-1b, sim-3b, sim-7b, sim-8b, sim-14b, sim-perfect} for B7_mycelic and
B8_mycelic_security.  When the edge profile is sim-perfect the frontier profile is set to sim-perfect
as well so that config.validate's "frontier dominates edge" rule holds.  Every row is labelled
profile_kind=synthetic (or measured, if the profile carries `measured: true`); these are assumed
operating points, never measured model results.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, log, make_cfg, parse_args, resolve_systems, run_grid, runner_banner,  # noqa: E402
                                seeds_from_args)

PROFILES = ["sim-1b", "sim-3b", "sim-7b", "sim-8b", "sim-14b", "sim-perfect"]
QUICK_PROFILES = ["sim-1b", "sim-7b", "sim-perfect"]


def model_conditions(cfg: dict, quick: bool) -> list[Condition]:
    profiles = [p for p in (QUICK_PROFILES if quick else PROFILES) if p in cfg["models"]["profiles"]]
    out = []
    for p in profiles:
        ov = {"models.edge_profile": p}
        if p == "sim-perfect":
            ov["models.frontier_profile"] = "sim-perfect"
        measured = bool(cfg["models"]["profiles"][p].get("measured", False))
        out.append(Condition(name=f"profile-{p}", columns={"profile": p, "profile_kind": "measured" if measured else "synthetic",
                                                           "params_b": cfg["models"]["profiles"][p].get("params_b", "")},
                             cfg_overrides=ov))
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv)
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, ["B7_mycelic", "B8_mycelic_security"], cfg, "models")
    conds = model_conditions(cfg, args.quick)
    runner_banner("models", args, cfg, seeds, systems, conds)
    log("NOTE: this is a synthetic capability sweep over assumed profiles (configs/models.yaml), not measured models", family="models")
    run_grid("models", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
             tier=args.tier, quick=args.quick)
    log("done", family="models")
    return 0


if __name__ == "__main__":
    sys.exit(main())
