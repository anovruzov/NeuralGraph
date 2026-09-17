#!/usr/bin/env python3
"""Failures family: knowledge survival, lineage survival and recovery under user / node / link failures.

    python experiments/failures/run.py --tier tier1 [--quick] [--kinds a,b] [--rates 0.1,0.5]
                                       [--at-round R] [--recover-round R]

For every seed the runner first executes the system with **no failure** (the paired reference run on
the same world, condition ``no-failure``), then every failure kind in ``failures.KINDS`` x rate in
{1, 5, 10, 20, 30, 50} % (``--quick``: ``random_team`` and ``partition`` x {10, 50} %).  The failure is
active during rounds ``[n_rounds // 3, 2 * n_rounds // 3)``: ``FailurePlan.apply`` arms it after round
``at_round - 1`` and recovers it after round ``recover_round - 1``.

Every row of ``results/processed/failures.csv`` carries, next to the usual ``metrics.*`` columns of the
failed run, the paired metrics of ``failures.compare_runs(world, base_run, failed_run, plan)``:
``knowledge_survival`` (fraction of the no-failure root discoveries still accepted at the end),
``useful_discovery_survival`` (cross-team + global only), ``lineage_survival`` (contributing units of the
surviving claims still reachable at the end of the outage), ``contradiction_detection_survival``,
``recovery_latency`` (rounds after recovery until the survival curve is back to >= 95 %; empty when it
never recovers, see ``recovery_latency_censored``), ``repair_quality`` (discoveries dropped during the
outage that are back by the end) and ``false_reconstruction_rate`` (root claims first accepted after
recovery that are population-false).  The ``no-failure`` row carries the same columns compared with
itself (survival 1.0) so that every seed has a rate-0 reference in the CSV.

The paired comparison needs the *full* reference result (per-round snapshots, discovered effects,
classified root claims, conflicts), not only its metrics, so the runner keeps a per-process cache of
``failures.summarize_run(result)`` keyed by (seed, system) and fills it from the ``per_run`` hook of the
``no-failure`` condition, which ``run_grid`` always executes first within a seed (conditions run in
order, one seed end-to-end per process, so ``--jobs`` is safe).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.common import (Condition, RunContext, log, make_cfg, parse_args, resolve_systems, run_grid,  # noqa: E402
                                runner_banner, seeds_from_args)

RATES = [0.01, 0.05, 0.10, 0.20, 0.30, 0.50]
QUICK_RATES = [0.10, 0.50]
QUICK_KINDS = ["random_team", "partition"]
DEFAULT_SYSTEMS = ["B7_mycelic"]
SURVIVAL_THRESHOLD = 0.95
# paired metrics promoted to top-level CSV columns (everything else from compare_runs is prefixed `failure.`)
TOP_LEVEL = ("knowledge_survival", "useful_discovery_survival", "lineage_survival", "conflict_survival",
             "contradiction_detection_survival", "recovery_latency", "recovery_latency_censored", "recovered",
             "repair_quality", "false_reconstruction_rate", "survival_pre_recovery", "survival_min",
             "n_base_discoveries", "n_base_useful", "n_surviving", "n_dropped", "n_recovered", "at_round", "recover_round",
             "n_units_failed", "worker_fraction_affected", "records_lost", "records_buffered", "records_replayed")


def all_kinds() -> list[str]:
    from mycelic_bench.failures import KINDS
    return list(KINDS)


def window_for(n_rounds: int, at_round: int | None = None, recover_round: int | None = None) -> tuple[int, int]:
    """Failure window [at, recover): the middle third of the run unless overridden."""
    n = int(n_rounds)
    at = int(at_round) if at_round is not None else max(1, n // 3)
    rec = int(recover_round) if recover_round is not None else max(at + 1, (2 * n) // 3)
    if not (1 <= at < rec <= n):
        raise ValueError(f"invalid failure window at_round={at} recover_round={rec} for n_rounds={n}")
    return at, rec


class FailureFactory:
    """``Condition.failure`` callable: builds the plan for one (kind, rate) once the world is known.

    The plan is a ``FailurePlan`` extended with ``wrap_hook(attack_hook)`` (what ``run_grid`` calls to chain
    the record filter after any attack hook) so user-loss and stale-replica buffering reach the batches.
    """

    def __init__(self, kind: str, rate: float, at_round: int | None = None, recover_round: int | None = None,
                 delay_rounds: int = 3) -> None:
        self.kind = kind
        self.rate = float(rate)
        self.at_round = at_round
        self.recover_round = recover_round
        self.delay_rounds = int(delay_rounds)

    def __call__(self, world: Any, cfg: dict[str, Any], seed: int, system: str):
        from mycelic_bench.failures import FailurePlan, chain_hooks

        class RunnerPlan(FailurePlan):
            def wrap_hook(self, attack_hook):
                return chain_hooks(attack_hook, self.record_filter)

            def __repr__(self) -> str:
                return (f"FailurePlan(kind={self.kind!r}, rate={self.rate}, seed={self.seed}, at_round={self.at_round}, "
                        f"recover_round={self.recover_round}, layer={self.layer!r})")

        at, rec = window_for(world.n_rounds, self.at_round, self.recover_round)
        return RunnerPlan(self.kind, self.rate, seed=int(seed), at_round=at, recover_round=rec,
                          delay_rounds=self.delay_rounds, org=world.org)

    def __repr__(self) -> str:
        return (f"FailureFactory(kind={self.kind!r}, rate={self.rate}, at_round={self.at_round}, "
                f"recover_round={self.recover_round}, delay_rounds={self.delay_rounds})")


def failure_conditions(kinds: list[str], rates: list[float], at_round: int | None, recover_round: int | None,
                       delay_rounds: int) -> list[Condition]:
    """The paired reference first, then kind x rate (rates ascending so nested target sets are visible in logs)."""
    conds = [Condition(name="no-failure", columns={"failure_kind": "none", "failure_rate": 0.0})]
    for kind in kinds:
        for rate in rates:
            conds.append(Condition(name=f"{kind}-{rate:.2f}", columns={"failure_kind": kind, "failure_rate": float(rate)},
                                   failure=FailureFactory(kind, rate, at_round, recover_round, delay_rounds)))
    return conds


# --------------------------------------------------------------------------
# per-run hook: paired comparison against the no-failure run of the same seed and system
# --------------------------------------------------------------------------
_BASELINES: dict[tuple[int, str], Any] = {}


def failure_columns(ctx: RunContext) -> dict[str, Any]:
    from mycelic_bench.failures import compare_runs, summarize_run

    key = (int(ctx.seed), str(ctx.system))
    plan = ctx.failure_plan
    if plan is None:
        view = summarize_run(ctx.result)
        _BASELINES[key] = view
        cmp = compare_runs(ctx.world, view, view, None, survival_threshold=SURVIVAL_THRESHOLD)
        cmp["paired_with"] = "self"
    else:
        base = _BASELINES.get(key)
        if base is None:
            log(f"seed {ctx.seed} {ctx.system}: no paired no-failure run in this process; paired metrics left empty",
                family="failures")
            return {"failure.paired_with": "", "failure.error": "no paired no-failure run for this seed/system"}
        cmp = compare_runs(ctx.world, base, ctx.result, plan, survival_threshold=SURVIVAL_THRESHOLD)
        cmp["paired_with"] = "no-failure"
    out: dict[str, Any] = {}
    for k, v in cmp.items():
        if k in ("failure_kind", "failure_rate"):
            continue                                    # already condition columns of the row
        if k == "survival_curve":
            out["failure.survival_curve"] = json.dumps([None if x is None else round(float(x), 4) for x in v])
            continue
        out[k if k in TOP_LEVEL else f"failure.{k}"] = v
    if plan is not None:
        s = plan.summary()
        out["failure.layer"] = s.get("layer", "")
        out["failure.delay_rounds"] = s.get("delay_rounds", "")
        out["failure.activated_round"] = s.get("activated_round")
        out["failure.recovered_round"] = s.get("recovered_round")
        out["failure.failed_units"] = json.dumps(list(s.get("failed_units", [])))
        out["failure.n_failed_workers"] = s.get("n_failed_workers", 0)
    else:
        out["failure.layer"] = ""
        out["failure.failed_units"] = "[]"
        out["failure.n_failed_workers"] = 0
    out["failure.survival_threshold"] = SURVIVAL_THRESHOLD
    ks = out.get("knowledge_survival")
    us = out.get("useful_discovery_survival")
    lat = out.get("recovery_latency")
    log(f"seed {ctx.seed} | {ctx.condition.name} | {ctx.system}: knowledge_survival={_f(ks)} useful={_f(us)} "
        f"lineage={_f(out.get('lineage_survival'))} repair={_f(out.get('repair_quality'))} "
        f"recovery_latency={'n/a' if lat is None else lat} false_reconstruction={_f(out.get('false_reconstruction_rate'))} "
        f"(base discoveries={out.get('n_base_discoveries')})", family="failures")
    return out


def _f(v: Any) -> str:
    return "n/a" if v is None else f"{float(v):.3f}"


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    def extra(ap):
        ap.add_argument("--kinds", default=None, help="comma-separated failure kinds (default: all of failures.KINDS; "
                                                      "--quick: random_team,partition)")
        ap.add_argument("--rates", default=None, help="comma-separated failure rates in (0,1] (default 0.01,0.05,0.1,0.2,0.3,0.5; "
                                                      "--quick: 0.1,0.5)")
        ap.add_argument("--at-round", dest="at_round", type=int, default=None, help="first failed round (default n_rounds//3)")
        ap.add_argument("--recover-round", dest="recover_round", type=int, default=None,
                        help="first recovered round (default 2*n_rounds//3)")
        ap.add_argument("--delay-rounds", dest="delay_rounds", type=int, default=3, help="latency of delayed_sync in rounds")

    args = parse_args(__doc__, argv, extra)
    cfg = make_cfg(args.tier, args.overrides)
    seeds = seeds_from_args(args, cfg)
    systems = resolve_systems(args, DEFAULT_SYSTEMS, cfg, "failures")
    known = all_kinds()
    if args.kinds:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        bad = [k for k in kinds if k not in known]
        if bad:
            raise SystemExit(f"unknown failure kinds {bad}; known: {known}")
    else:
        kinds = QUICK_KINDS if args.quick else known
    rates = [float(r) for r in args.rates.split(",")] if args.rates else (QUICK_RATES if args.quick else RATES)
    if any(not 0.0 < r <= 1.0 for r in rates):
        raise SystemExit(f"rates must be in (0, 1]: {rates}")
    at, rec = window_for(cfg["org"]["n_rounds"], args.at_round, args.recover_round)
    conds = failure_conditions(kinds, rates, args.at_round, args.recover_round, args.delay_rounds)
    runner_banner("failures", args, cfg, seeds, systems, conds)
    log(f"failure window: rounds [{at}, {rec}) of {cfg['org']['n_rounds']}; kinds={kinds}; rates={rates}", family="failures")
    run_grid("failures", systems, seeds, cfg, per_run=failure_columns, conditions=conds, results_root=args.results,
             jobs=args.jobs, tag=args.tag, tier=args.tier, quick=args.quick)
    log("done", family="failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
