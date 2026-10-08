#!/usr/bin/env python3
"""Scaling family: runtime, memory, tokens and bytes versus organisation size.

    python experiments/scaling/run.py --tier tier1 [--quick] [--sizes 100,1000,10000]

N in {100, 1000, 10000, 50000, 100000} workers for B7_mycelic and B1_central_keyword (`--quick`: 100 and
1000).  The world's effect counts scale with N (tier1 counts x N/1000 up to 1000 workers, the tier2
block at 10k, the tier3 block x N/50000 above), organisational shape follows the tier's
workers_per_team / teams_per_department / departments_per_region.  Above 10,000 workers only one seed
is run unless `--seeds` is given explicitly.  Recorded per run: runtime_s (wall, incl. evaluation),
metrics.runtime_s (system only), world_gen_s, peak_rss_mb, metrics.tokens, metrics.bytes_transmitted,
metrics.bytes_off_device, metrics.model_calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, log, make_cfg, parse_args, resolve_systems, run_grid, runner_banner,  # noqa: E402
                                seeds_from_args)

SIZES = [100, 1000, 10000, 50000, 100000]
QUICK_SIZES = [100, 1000]
WORLD_COUNT_KEYS = ("n_local_findings", "n_cross_team_findings", "n_global_findings", "n_decoys", "n_contradictions",
                    "n_temporal_revisions")


def world_counts_for(cfg: dict[str, Any], n_workers: int) -> dict[str, int]:
    tiers = cfg.get("tiers", {})
    t1 = tiers.get("tier1", {}).get("world", cfg["world"])
    t2 = tiers.get("tier2", {}).get("world", t1)
    t3 = tiers.get("tier3", {}).get("world", t2)
    if n_workers <= 1000:
        base, ref = t1, 1000
    elif n_workers <= 10000:
        base, ref = t2, 10000
    else:
        base, ref = t3, 50000
    scale = n_workers / ref
    return {k: max(1, int(round(int(base.get(k, cfg["world"].get(k, 0))) * scale))) for k in WORLD_COUNT_KEYS}


def scaling_conditions(cfg: dict[str, Any], sizes: list[int], seeds: list[int], explicit_seeds: bool) -> list[Condition]:
    out = []
    for n in sizes:
        counts = world_counts_for(cfg, n)
        overrides = {"org.n_workers": int(n)}
        overrides.update({f"world.{k}": v for k, v in counts.items()})
        cond_seeds = None if (explicit_seeds or n <= 10000) else seeds[:1]
        out.append(Condition(name=f"N{n}", columns={"n_workers": int(n), **{f"world_{k}": v for k, v in counts.items()}},
                             cfg_overrides=overrides, seeds=cond_seeds))
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv, lambda ap: ap.add_argument("--sizes", default=None, help="comma-separated worker counts"))
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    sizes = [int(s) for s in args.sizes.split(",")] if args.sizes else (QUICK_SIZES if args.quick else SIZES)
    systems = resolve_systems(args, ["B7_mycelic", "B1_central_keyword"], cfg, "scaling")
    conds = scaling_conditions(cfg, sizes, seeds, explicit_seeds=args.seeds is not None)
    runner_banner("scaling", args, cfg, seeds, systems, conds)
    run_grid("scaling", systems, seeds, cfg, conditions=conds, results_root=args.results, jobs=args.jobs, tag=args.tag,
             tier=args.tier, quick=args.quick)
    log("done", family="scaling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
