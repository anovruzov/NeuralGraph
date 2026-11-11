#!/usr/bin/env python3
"""Temporal family: revision handling when effects flip at model-version releases.

    python experiments/temporal/run.py --tier tier1 [--quick]

Runs B6_hier_no_lineage and B7_mycelic on worlds whose `world.n_temporal_revisions` is scaled up 3x
over the tier default (tier1: 10 -> 30) unless overridden.  Reported metrics: recall_temporal,
revision_accuracy, stale_persistence, revision_latency, old_evidence_retention, catastrophic_overwrite.
The full grid also sweeps policy.recent_window in {3, 6, 12} for B7.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, log, make_cfg, parse_args, resolve_systems, run_grid, runner_banner,  # noqa: E402
                                seeds_from_args)

SYSTEMS = ["B6_hier_no_lineage", "B7_mycelic"]
RECENT_WINDOWS = [3, 6, 12]
SCALE = 3


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv)
    cfg = make_cfg(args.tier, args.overrides)
    if not any(o.startswith("world.n_temporal_revisions=") for o in args.overrides):
        cfg["world"]["n_temporal_revisions"] = int(cfg["world"].get("n_temporal_revisions", 10)) * SCALE
    n_rev = int(cfg["world"]["n_temporal_revisions"])
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, SYSTEMS, cfg, "temporal")
    base_window = int(cfg["policy"].get("recent_window", 6))
    conds = [Condition(name=f"revisions-{n_rev}", columns={"n_temporal_revisions": n_rev, "recent_window": base_window})]
    if not args.quick and "B7_mycelic" in systems:
        for w in RECENT_WINDOWS:
            if w == base_window:
                continue
            conds.append(Condition(name=f"revisions-{n_rev}-window{w}", columns={"n_temporal_revisions": n_rev, "recent_window": w},
                                   policy_overrides={"recent_window": w}, systems=["B7_mycelic"]))
    runner_banner("temporal", args, cfg, seeds, systems, conds)
    run_grid("temporal", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
             tier=args.tier, quick=args.quick)
    log("done", family="temporal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
