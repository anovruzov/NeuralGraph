#!/usr/bin/env python3
"""Privacy family: quote policy x k-anonymity for Mycelic versus centralized baselines.

    python experiments/privacy/run.py --tier tier1 [--quick]

B7_mycelic under policy.quote_policy in {none, redacted, raw} x policy.k_anonymity in {0, 3, 10}
(min_cell_n is set to the same k so that k=0 really means "no suppression"), plus B1_central_keyword
and B3_central_llm_summary which receive every raw record.  Besides the evaluator's privacy metrics
(raw_sensitive_leakage, n_canaries_exposed, bytes_off_device, fraction_raw_exposed, reconstructability
at the policy's own k) the hook records re-identification risk at fixed thresholds
(`privacy.small_cell_fraction_k3` / `_k10`: promoted higher-order cells with n < 3 / 10).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, RunContext, log, make_cfg, parse_args, resolve_systems, run_grid,  # noqa: E402
                                runner_banner, seeds_from_args)

QUOTE_POLICIES = ["none", "redacted", "raw"]
K_VALUES = [0, 3, 10]
QUICK_POINTS = [("none", 3), ("none", 0), ("redacted", 3), ("raw", 0)]
CENTRAL = ["B1_central_keyword", "B3_central_llm_summary"]


def privacy_conditions(quick: bool, hierarchy_systems: list[str], central: list[str]) -> list[Condition]:
    points = QUICK_POINTS if quick else [(q, k) for q in QUOTE_POLICIES for k in K_VALUES]
    out = [Condition(name=f"quote-{q}-k{k}", columns={"quote_policy": q, "k_anonymity": k},
                     policy_overrides={"quote_policy": q, "k_anonymity": k, "min_cell_n": k}, systems=hierarchy_systems)
           for q, k in points]
    if central:
        out.append(Condition(name="central-raw", columns={"quote_policy": "raw", "k_anonymity": 0}, systems=central))
    return out


def privacy_columns(ctx: RunContext) -> dict[str, Any]:
    hier = ctx.result.get("hier")
    out: dict[str, Any] = {}
    if hier is None:
        return out
    import numpy as np
    from mycelic_bench.vocab import cell_index
    from mycelic_bench.sketch import COL_N
    ci = cell_index()
    total = 0
    small = {3: 0, 10: 0}
    for node in hier.nodes.values():
        if node.parent_id is None or len(node.sent.ids) == 0:
            continue
        hi = ci.order_of(node.sent.ids) >= 2
        total += int(hi.sum())
        n = node.sent.counts[hi, COL_N]
        for k in small:
            small[k] += int((n < k).sum())
    out["privacy.promoted_higher_order_cells"] = total
    for k in small:
        out[f"privacy.small_cell_fraction_k{k}"] = small[k] / max(total, 1)
    quotes = sum(len(n.quotes_received) for n in hier.nodes.values())
    out["privacy.quotes_received"] = int(quotes)
    out["privacy.quote_bytes"] = int(sum(len(q) for n in hier.nodes.values() for q in n.quotes_received))
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv)
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, ["B7_mycelic"] + CENTRAL, cfg, "privacy")
    hier_systems = [s for s in systems if cfg["systems"].get(s, {}).get("kind", "hierarchy") == "hierarchy"]
    central = [s for s in systems if s not in hier_systems]
    conds = privacy_conditions(args.quick, hier_systems, central)
    runner_banner("privacy", args, cfg, seeds, systems, conds)
    run_grid("privacy", systems, seeds, cfg, per_run=privacy_columns, conditions=conds, results_root=args.results, jobs=args.jobs,
             tag=args.tag, tier=args.tier, quick=args.quick)
    log("done", family="privacy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
