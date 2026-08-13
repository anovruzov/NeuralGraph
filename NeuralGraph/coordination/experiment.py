"""Executable deterministic collective-capability failure experiment."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .contracts import ExperimentManifest, InterventionTarget
from .core import (
    ClaimNormalizer,
    LineageAnalyzer,
    RepairDecision,
    RepairPlanner,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
    to_jsonable,
)
from .fixture import FIXTURE_VERSION, REQUIRED_SLOTS, assert_fixture_privacy, build_runtime


STRATEGIES = (
    "replica_count",
    "lineage_without_repair",
    "lineage_targeted_repair",
)


async def run_strategy(seed: int, strategy: str) -> dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    runtime = build_runtime(seed, strategy)
    fixture = runtime.fixture
    router = Router(runtime.registry, runtime.failure)
    coordinator = TesseractCoordinator(
        registry=runtime.registry,
        router=router,
        normalizer=ClaimNormalizer(),
        synthesizer=RuleBasedSynthesizer(REQUIRED_SLOTS),
        analyzer=LineageAnalyzer(),
        traces=runtime.traces,
    )
    manifest = ExperimentManifest(
        run_id=fixture.run_id,
        seed=seed,
        started_at=runtime.clock.now(),
        fixture_version=FIXTURE_VERSION,
        strategy=strategy,
        budget=fixture.query.budget,
    )

    before = await coordinator.execute(
        fixture.query,
        phase="before_failure",
        preferred_node_ids=("node-a", "node-b", "node-c"),
    )
    runtime.failure.apply(
        InterventionTarget.LINEAGE_ROOT,
        "R2",
        reason="controlled removal of shared critical support",
        expected_effect="invalidate node-b and correlated node-c evidence",
    )
    after_failure = await coordinator.execute(
        fixture.query,
        phase="after_failure",
        preferred_node_ids=("node-a", "node-b", "node-c"),
    )

    repair: RepairDecision | None = None
    after_repair = after_failure
    additional_query_cost = 0
    if strategy == "replica_count":
        # Replica-count logic sees C and B as two copies and retries both without
        # checking root independence. Both still depend on failed R2.
        planner = RepairPlanner(runtime.registry, runtime.traces)
        repair = await planner.plan(
            fixture.query,
            missing_slots=("right",),
            candidate_node_ids=("node-c", "node-b"),
            excluded_roots=(),
            excluded_failure_domains=(),
        )
        after_repair = await coordinator.execute(
            fixture.query,
            phase="after_replica_repair",
            preferred_node_ids=("node-a", "node-c", "node-b"),
        )
        additional_query_cost = repair.steps
    elif strategy == "lineage_targeted_repair":
        planner = RepairPlanner(runtime.registry, runtime.traces)
        repair = await planner.plan(
            fixture.query,
            missing_slots=("right",),
            candidate_node_ids=("node-c", "node-d"),
            excluded_roots=("R1", "R2"),
            excluded_failure_domains=("FD1", "FD2"),
        )
        preferred = (
            ("node-a", "node-c", repair.selected_node_id)
            if repair.selected_node_id else ("node-a", "node-b", "node-c")
        )
        after_repair = await coordinator.execute(
            fixture.query,
            phase="after_independent_repair",
            preferred_node_ids=tuple(node for node in preferred if node),
        )
        additional_query_cost = repair.steps

    manifest = replace(manifest, interventions=runtime.failure.records)
    phases = {
        "before_failure": before,
        "after_failure": after_failure,
        "after_repair": after_repair,
    }
    actual_executions = [before, after_failure]
    if strategy != "lineage_without_repair":
        actual_executions.append(after_repair)
    privacy = assert_fixture_privacy(fixture)
    privacy["no_policy_violations"] = all(phase.policy_violations == 0 for phase in phases.values())
    privacy["only_claim_contracts_exported"] = all(
        claim.__class__.__name__ == "ClaimEnvelope"
        for phase in phases.values()
        for claim in phase.claims
    )
    return {
        "manifest": to_jsonable(manifest),
        "fixture": {
            "query": to_jsonable(fixture.query),
            "expected_answer": fixture.expected_answer,
            "node_ids": list(fixture.records_by_node),
            "structural_expectations": {
                "node-a": {"slot": "left", "root": "R1", "failure_domain": "FD1"},
                "node-b": {"slot": "right", "root": "R2", "failure_domain": "FD2"},
                "node-c": {"slot": "right", "root": "R2", "failure_domain": "FD2"},
                "node-d": {"slot": "right", "root": "R3", "failure_domain": "FD3"},
            },
        },
        "phases": {
            name: {
                "route": list(phase.route),
                "synthesis": to_jsonable(phase.synthesis),
                "metrics": to_jsonable(phase.metrics),
                "exported_bytes": phase.exported_bytes,
                "exported_claim_count": len(phase.claims),
                "policy_violations": phase.policy_violations,
                "claim_roots": {
                    claim.claim_id: list(claim.lineage_root_ids) for claim in phase.claims
                },
            }
            for name, phase in phases.items()
        },
        "repair": to_jsonable(repair) if repair else None,
        "evaluation": {
            "predicted_failure_domain_threshold": before.metrics.minimal_failure_domain_cut,
            "observed_failure_threshold": (
                1 if before.synthesis.success and not after_failure.synthesis.success else None
            ),
            "repair_steps": repair.steps if repair else 0,
        },
        "cost": {
            "phase_query_count": len(actual_executions),
            "verification_steps": additional_query_cost,
            "total_exported_bytes": sum(phase.exported_bytes for phase in actual_executions),
            "total_exported_claims": sum(len(phase.claims) for phase in actual_executions),
        },
        "privacy_assertions": privacy,
        "trace_events": to_jsonable(runtime.traces.events),
    }


async def run_experiment(seed: int = 20260813) -> dict[str, Any]:
    results = {strategy: await run_strategy(seed, strategy) for strategy in STRATEGIES}
    targeted = results["lineage_targeted_repair"]
    summary = {
        strategy: {
            "before_failure": result["phases"]["before_failure"]["synthesis"]["success"],
            "after_failure": result["phases"]["after_failure"]["synthesis"]["success"],
            "after_repair": result["phases"]["after_repair"]["synthesis"]["success"],
            "verification_steps": result["cost"]["verification_steps"],
        }
        for strategy, result in results.items()
    }
    return {
        "schema_version": "1.0",
        "seed": seed,
        "milestone": {
            "before_failure": targeted["phases"]["before_failure"],
            "after_failure": targeted["phases"]["after_failure"],
            "after_repair": targeted["phases"]["after_repair"],
        },
        "strategy_summary": summary,
        "strategies": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = asyncio.run(run_experiment(args.seed))
    payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
