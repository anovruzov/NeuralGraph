#!/usr/bin/env python3
"""Routing experiment runner (Task E).

Per seed: generate the world, run the hierarchy system (default `B7_mycelic`) to obtain a finished
`Hierarchy`, generate the retrieval workload (`routing.generate_workload`), evaluate every router, then

* write `results/raw/routing/<run_id>/manifest.json`, `metrics.json` and `routes.jsonl` (per query x router),
* append one flat row per router x seed to `results/processed/routing.csv`
  (columns: family, system, router, seed, tier, n_workers, run_id, metrics.* with dotted keys).

Usage:  PYTHONPATH=src python3 experiments/routing/run.py --tier tier1 [--seeds N] [--seed-offset K]
        [--systems B7_mycelic] [--routers a,b,...] [--set org.n_rounds=10] [--results DIR] [--quick]

The CLI/manifest logic is implemented locally (mirrors the `experiments/common.py` conventions of Task H) so
this runner does not depend on that module existing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
BENCH_ROOT = HERE.parents[2]
if str(BENCH_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT / "src"))

from mycelic_bench.config import DEFAULT_RESULTS, load_config  # noqa: E402
from mycelic_bench.manifest import append_processed_row, write_manifest  # noqa: E402

FAMILY = "routing"
QUICK_OVERRIDES = [
    "org.n_workers=600", "org.teams_per_department=3", "org.n_rounds=10",
    "world.n_local_findings=30", "world.n_cross_team_findings=8", "world.n_global_findings=4", "world.n_decoys=8",
    "world.n_contradictions=3", "world.n_temporal_revisions=3", "routing.n_random_queries=40",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tier", default="tier1", choices=["tier1", "tier2", "tier3"])
    p.add_argument("--seeds", type=int, default=None, help="number of seeds (default: the tier's seed count)")
    p.add_argument("--seed-offset", type=int, default=0)
    p.add_argument("--systems", default="B7_mycelic", help="comma-separated hierarchy systems to route over")
    p.add_argument("--routers", default="default", help="comma-separated router names, or 'default' / 'all'")
    p.add_argument("--set", action="append", default=[], metavar="k=v", help="dotted config override (repeatable)")
    p.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root directory")
    p.add_argument("--quick", action="store_true", help="small world (600 workers, 10 rounds) for smoke runs")
    p.add_argument("--budget", type=int, default=None, help="contact budget for recall_at_budget (default routing.contact_budget)")
    return p.parse_args(argv)


def tier_overrides(tier: str) -> list[str]:
    base = load_config()
    t = base.get("tiers", {}).get(tier)
    if t is None:
        raise SystemExit(f"unknown tier {tier!r}; available: {sorted(base.get('tiers', {}))}")
    out: list[str] = []
    for section, vals in t.items():
        if isinstance(vals, dict):
            out.extend(f"{section}.{k}={v}" for k, v in vals.items())
    return out


def make_cfg(tier: str, overrides: list[str], quick: bool) -> dict[str, Any]:
    ov = tier_overrides(tier) + (QUICK_OVERRIDES if quick else []) + list(overrides)
    return load_config(overrides=ov)


def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, (list, tuple)):
            out[key] = json.dumps(v)
        else:
            out[key] = v
    return out


def run_one(cfg: dict[str, Any], tier: str, system: str, seed: int, routers: list[str], results_root: Path,
            budget: int | None) -> dict[str, Any]:
    from mycelic_bench import routing as R
    from mycelic_bench.runner import run_system
    from mycelic_bench.world import generate_world, ground_truth_summary

    run_id = f"{system}_{tier}_seed{seed}"
    run_dir = results_root / "raw" / FAMILY / run_id
    csv_path = results_root / "processed" / f"{FAMILY}.csv"
    t0 = time.time()
    status, error, metrics, dataset_sha, extra = "ok", None, None, "", {}
    try:
        world = generate_world(cfg, seed)
        dataset_sha = world.dataset_sha256
        sys_cfg = dict(cfg["systems"][system])
        if sys_cfg.get("kind", "hierarchy") != "hierarchy":
            raise ValueError(f"{system}: routing needs a hierarchy system (kind=hierarchy)")
        res = run_system(world, cfg, seed, system, sys_cfg)
        hier = res["hier"]
        wl = R.generate_workload(world, hier, cfg, seed=seed)
        metrics, detail = R.evaluate_routers(hier, wl, routers, budget=budget, cfg=cfg, seed=seed)
        metrics["_overbroadening"] = R.overbroadening_summary(metrics)
        hm = res["metrics"]
        extra = {
            "tier": tier, "n_workers": int(cfg["org"]["n_workers"]), "routers": routers,
            "ground_truth": ground_truth_summary(world),
            "hierarchy_metrics": {k: hm.get(k) for k in ("n_accepted", "precision_strict", "recall_local", "recall_cross_team",
                                                          "recall_global", "bytes_transmitted", "compression_ratio", "runtime_s")},
            "workload": metrics["_workload"],
        }
        run_dir.mkdir(parents=True, exist_ok=True)
        with open(run_dir / "metrics.json", "w") as f:
            json.dump(_jsonable(metrics), f, indent=1)
        with open(run_dir / "routes.jsonl", "w") as f:
            for row in detail:
                f.write(json.dumps(_jsonable(row)) + "\n")
    except Exception as exc:  # noqa: BLE001 - failed runs are recorded, never hidden
        status, error = "failed", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    runtime = time.time() - t0
    outputs = {"metrics": str(run_dir / "metrics.json"), "routes": str(run_dir / "routes.jsonl"), "processed_csv": str(csv_path)}
    write_manifest(run_dir, family=FAMILY, run_id=run_id, system=system, seed=seed, cfg=cfg, metrics=metrics,
                   dataset_sha256=dataset_sha, runtime_s=runtime, status=status, error=error, outputs=outputs, extra=extra)
    if status == "ok":
        for r in routers:
            row = {"family": FAMILY, "system": system, "router": r, "seed": seed, "tier": tier,
                   "n_workers": int(cfg["org"]["n_workers"]), "run_id": run_id, "status": status}
            row.update(flatten({k: v for k, v in metrics[r].items()}, "metrics."))
            row.update(flatten({"workload": metrics["_workload"]}, "metrics."))
            append_processed_row(csv_path, row)
    else:
        for r in routers:
            append_processed_row(csv_path, {"family": FAMILY, "system": system, "router": r, "seed": seed, "tier": tier,
                                            "n_workers": int(cfg["org"]["n_workers"]), "run_id": run_id, "status": status})
        print(f"[routing] seed {seed} {system}: FAILED\n{error}", file=sys.stderr)
    return {"run_id": run_id, "status": status, "metrics": metrics, "runtime_s": runtime}


def _jsonable(x: Any) -> Any:
    import numpy as np
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating):
        return None if np.isnan(x) else float(x)
    if isinstance(x, float) and x != x:
        return None
    if isinstance(x, np.ndarray):
        return x.tolist()
    return x


def print_table(metrics: dict[str, Any], routers: list[str], budget: int) -> None:
    cols = [("recall@B", "recall_at_budget"), ("recall_full", "recall_full"), ("rec_strong", "recall_strong_full"),
            ("precision", "precision"), ("unnecessary", "unnecessary_escalation_rate"), ("correct_dst", "correct_destination"),
            ("xteam", "cross_team_discovery"), ("msgs", "messages"), ("bytes", "bytes"), ("hops", "latency_hops"),
            ("raw_B_eq", "raw_bytes_equivalent"), ("sketch_B", "sketch_bytes"), ("IS_true", "independent_support_true")]
    print(f"  contact budget B={budget}")
    print("  " + f"{'router':18s}" + "".join(f"{c[0]:>12s}" for c in cols))
    for r in routers:
        m = metrics[r]
        cells = []
        for _, k in cols:
            v = m.get(k)
            cells.append(f"{v:12.3f}" if isinstance(v, (int, float)) and v == v else f"{'nan':>12s}")
        print("  " + f"{r:18s}" + "".join(cells))


def main(argv: list[str] | None = None) -> int:
    from mycelic_bench import routing as R
    args = parse_args(argv)
    cfg = make_cfg(args.tier, args.set, args.quick)
    n_seeds = args.seeds if args.seeds is not None else int(cfg["tiers"][args.tier].get("seeds", 1))
    if args.routers == "default":
        routers = list(R.DEFAULT_ROUTERS)
    elif args.routers == "all":
        routers = list(R.ROUTERS)
    else:
        routers = [r.strip() for r in args.routers.split(",") if r.strip()]
    unknown = [r for r in routers if r not in R.ROUTERS]
    if unknown:
        raise SystemExit(f"unknown routers {unknown}; available: {sorted(R.ROUTERS)}")
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    results_root = Path(args.results)
    budget = args.budget if args.budget is not None else int(R.routing_config(cfg)["contact_budget"])
    print(f"[routing] tier={args.tier} quick={args.quick} seeds={n_seeds} offset={args.seed_offset} systems={systems} "
          f"routers={routers} n_workers={cfg['org']['n_workers']} n_rounds={cfg['org']['n_rounds']} results={results_root}")
    failures = 0
    for system in systems:
        for s in range(args.seed_offset, args.seed_offset + n_seeds):
            out = run_one(cfg, args.tier, system, s, routers, results_root, budget)
            print(f"[routing] {out['run_id']}: {out['status']} ({out['runtime_s']:.1f}s)")
            if out["status"] != "ok":
                failures += 1
                continue
            print_table(out["metrics"], routers, budget)
            ob = out["metrics"].get("_overbroadening", {})
            if ob:
                print("  over-broadening: precision global_only={:.3f} vs hierarchy_aware={:.3f} (ratio {:.3f}); "
                      "unnecessary escalation global={:.3f} vs hierarchy={:.3f}; bytes ratio global/hierarchy={:.2f}".format(
                          ob.get("precision_global", float("nan")), ob.get("precision_hierarchy_aware", float("nan")),
                          ob.get("precision_ratio_global_vs_hierarchy", float("nan")), ob.get("unnecessary_global", float("nan")),
                          ob.get("unnecessary_hierarchy_aware", float("nan")), ob.get("bytes_ratio_global_vs_hierarchy", float("nan"))))
    print(f"[routing] done; processed rows -> {results_root / 'processed' / (FAMILY + '.csv')}; failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
