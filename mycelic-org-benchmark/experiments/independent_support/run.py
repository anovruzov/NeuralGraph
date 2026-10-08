#!/usr/bin/env python3
"""Independent-support family: does the lineage-aware aggregator count sources, not copies?

    python experiments/independent_support/run.py --tier tier1 [--quick]

`attacks.independent_support_scenarios(world, cfg, seed)` plants evidence clusters (50 copies of one
record, 10 paraphrases with the same fingerprint, 5 workers copying each other, 3 independent workers,
cross-department, cross-region, ...) each with a ground-truth `true_independent_support`.  B6 (replica
count) and B7 (lineage) then run on the mutated world and every cluster's estimated support is read
from the root's claim for its (cell, label) - or, when no claim exists, from the root's pooled
distinct-source counts through the same discount formula.  Per run: is_mae, is_within_1 (accuracy),
correlated_discount_ok (copy/paraphrase/copying clusters not over-counted), fake_consensus_resisted
(echoed bare claims not accepted).  Per cluster rows go to `independent_support_clusters.csv`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, RunContext, append_processed_row, log, make_cfg, parse_args,  # noqa: E402
                                processed_csv, resolve_systems, run_grid, runner_banner, seeds_from_args)

SYSTEMS = ["B6_hier_no_lineage", "B7_mycelic"]
CORRELATED_HINTS = ("cop", "paraphrase", "duplicate", "replay", "same_worker", "echo", "consensus")   # exact_copies / paraphrases / copy_chain


def _cell_truth(world, cfg: dict[str, Any], cell: int) -> float:
    """Honest cell-level independent support: the lineage formula over every non-copied, benign worker whose
    records match the cell (evaluation only; experiments may read the world)."""
    import numpy as np
    from mycelic_bench.attacks import _rho, independent_support_formula
    from mycelic_bench.world import cell_match_mask
    m = cell_match_mask(world.attrs, cell) & ~world.is_copy & (world.attack_tag == 0)
    return independent_support_formula(np.unique(world.worker[m]), world.org, _rho(cfg)) if m.any() else 0.0
FAKE_HINTS = ("fake", "consensus", "echo", "attack", "fabricat")


def _get(c: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(c, dict) and n in c:
            return c[n]
        if not isinstance(c, dict) and hasattr(c, n):
            return getattr(c, n)
    return default


def _clusters(table: Any) -> list[Any]:
    if table is None:
        return []
    if isinstance(table, dict):
        if "clusters" in table:
            return list(table["clusters"])
        return [dict(v, cluster_id=k) if isinstance(v, dict) else v for k, v in table.items()]
    if hasattr(table, "clusters"):
        return list(table.clusters)
    if hasattr(table, "to_dict") and hasattr(table, "columns"):   # pandas
        return table.to_dict("records")
    return list(table)


def world_with_scenarios(cfg: dict[str, Any], seed: int):
    from mycelic_bench.world import generate_world
    world = generate_world(cfg, seed)
    from mycelic_bench import attacks
    table = attacks.independent_support_scenarios(world, cfg, seed)
    world.is_clusters = _clusters(table)   # type: ignore[attr-defined]
    world.dataset_sha256 = __import__("mycelic_bench.world", fromlist=["dataset_hash"]).dataset_hash(world)
    log(f"seed {seed}: {len(world.is_clusters)} independent-support clusters planted", family="independent_support")
    return world


def support_columns(ctx: RunContext) -> dict[str, Any]:
    import numpy as np
    from mycelic_bench.sketch import COL_DD, COL_DR, COL_DT, COL_DW, COL_N
    hier = ctx.result.get("hier")
    clusters = getattr(ctx.world, "is_clusters", [])
    out: dict[str, Any] = {"support.n_clusters": len(clusters)}
    if hier is None or not clusters:
        return out
    root = hier.root_node()
    by_sig: dict[tuple[int, int], list] = {}
    for node in hier.nodes.values():
        for (cell, label, sign, scope), c in node.claims.items():
            by_sig.setdefault((int(cell), int(label)), []).append((node, c))
    errs, within, corr_ok, corr_n, fake_ok, fake_n, acc_true = [], [], [], 0, [], 0, []
    csv_rows = []
    for c in clusters:
        cell = _get(c, "cell"); label = _get(c, "label")
        truth = _get(c, "true_independent_support", "true_is", "independent_support_true")
        kind = str(_get(c, "cluster", "kind", "scenario", "name", "type", default="unknown"))
        cid = _get(c, "cluster_id", "id", default=kind)
        if cell is None or label is None or truth is None:
            continue
        cell, label, truth = int(cell), int(label), float(truth)
        # the system estimates the support of the whole cell (planted roots + benign records that happen to match),
        # so the fair reference is the cell-level honest support; the cluster's planted-root support is kept as well
        truth_cell = _cell_truth(ctx.world, ctx.cfg, cell)
        claims = by_sig.get((cell, label), [])
        root_claims = [cl for node, cl in claims if node.unit_id == root.unit_id and cl.sign > 0]
        accepted = any(cl.status == "accepted" for cl in root_claims)
        if root_claims:
            best = max(root_claims, key=lambda cl: (cl.status == "accepted", cl.support.independent_support))
            est = float(best.support.independent_support)
            source = "root_claim"
        elif claims:
            best = max((cl for _, cl in claims if cl.sign > 0), key=lambda cl: cl.support.independent_support, default=None)
            est = float(best.support.independent_support) if best is not None else float("nan")
            source = "lower_claim" if best is not None else "none"
        else:
            est, source = float("nan"), "none"
        if est != est:
            cnt = root.cumulative.lookup(np.array([cell]))[0]
            if cnt[COL_N] > 0:
                est = float(root._independent_support(int(cnt[COL_DW]), int(cnt[COL_DT]), int(cnt[COL_DD]), int(cnt[COL_DR]), int(cnt[COL_N])))
                source = "root_counts"
        err = abs(est - truth_cell) if est == est else float("nan")
        err_cluster = abs(est - truth) if est == est else float("nan")
        klow = kind.lower()
        is_corr = any(h in klow for h in CORRELATED_HINTS) or bool(_get(c, "correlated", default=False))
        is_fake = bool(_get(c, "fake", "is_fake", "attack", default=False)) or any(h in klow for h in FAKE_HINTS)
        if err == err:
            errs.append(err); within.append(err <= max(1.0, 0.25 * truth_cell))
            if is_corr:
                corr_n += 1; corr_ok.append(est <= truth_cell + 1.0)
        if is_fake:
            fake_n += 1; fake_ok.append(not accepted)
        elif truth >= float(ctx.cfg["policy"].get("support_min", 2.0)):
            acc_true.append(accepted)
        csv_rows.append({"family": "independent_support", "run_id": ctx.run_dir.name, "system": ctx.system, "seed": ctx.seed,
                         "tier": ctx.tier, "cluster_id": cid, "kind": kind, "cell": cell, "label": label, "true_is": truth,
                         "true_is_cell": truth_cell, "estimated_is": est, "abs_error": err, "abs_error_vs_cluster": err_cluster,
                         "estimate_source": source, "accepted_at_root": accepted,
                         "correlated": is_corr, "fake": is_fake, "n_records": _get(c, "n_records", "n", "size", default="")})
    csv_path = processed_csv(ctx.run_dir.parents[2], "independent_support_clusters")
    for r in csv_rows:
        append_processed_row(csv_path, r)
    out["support.n_evaluated"] = len(errs)
    out["support.is_mae"] = float(np.mean(errs)) if errs else float("nan")
    out["support.is_within_1"] = float(np.mean(within)) if within else float("nan")
    out["support.correlated_clusters"] = corr_n
    out["support.correlated_discount_ok"] = float(np.mean(corr_ok)) if corr_ok else float("nan")
    out["support.fake_clusters"] = fake_n
    out["support.fake_consensus_resisted"] = float(np.mean(fake_ok)) if fake_ok else float("nan")
    out["support.true_clusters_accepted"] = float(np.mean(acc_true)) if acc_true else float("nan")
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(__doc__, argv)
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, SYSTEMS, cfg, "independent_support")
    conds = [Condition(name="scenarios")]
    runner_banner("independent_support", args, cfg, seeds, systems, conds)
    try:
        from mycelic_bench import attacks  # noqa: F401
    except ImportError as e:
        log(f"SKIP: attacks module unavailable ({e}); the independent-support scenarios cannot be planted", family="independent_support")
        return 0
    if not hasattr(attacks, "independent_support_scenarios"):
        log("SKIP: attacks.independent_support_scenarios is not implemented", family="independent_support")
        return 0
    run_grid("independent_support", systems, seeds, cfg, world_factory=world_with_scenarios, per_run=support_columns, conditions=conds,
             results_root=args.results, jobs=args.jobs, tag=args.tag, tier=args.tier, quick=args.quick)
    log("done", family="independent_support")
    return 0


if __name__ == "__main__":
    sys.exit(main())
