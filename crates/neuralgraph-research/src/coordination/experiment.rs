//! Executable deterministic collective-capability failure experiment (port of `coordination/experiment.py`).
//!
//! `run_experiment(20260813)` serialised with [`artifact_json`](crate::coordination::core::artifact_json) is the
//! pinned `experiment_seed20260813.json`; the CLI is `coordination experiment` (`src/bin/coordination.rs`).

use crate::coordination::contracts::strs;
use crate::coordination::contracts::{
    str_list, CoordinationError, CoordinationResult, ExperimentManifest, InterventionTarget, ToJsonable,
};
use crate::coordination::core::{
    py_truthy_str, ClaimNormalizer, LineageAnalyzer, QueryExecution, RepairDecision, RepairPlanner, Router,
    RuleBasedSynthesizer, TesseractCoordinator,
};
use crate::coordination::fixture::{assert_fixture_privacy, build_runtime, required_slots, FIXTURE_VERSION};
use serde_json::{json, Map, Value};

pub const STRATEGIES: [&str; 3] = ["replica_count", "lineage_without_repair", "lineage_targeted_repair"];

pub const DEFAULT_SEED: i64 = 20260813;

fn phase_summary(phase: &QueryExecution) -> Value {
    let mut claim_roots = Map::new();
    for claim in &phase.claims {
        claim_roots.insert(claim.claim_id.clone(), str_list(&claim.lineage_root_ids));
    }
    json!({
        "route": str_list(&phase.route),
        "synthesis": phase.synthesis.to_jsonable(),
        "metrics": phase.metrics.to_jsonable(),
        "exported_bytes": phase.exported_bytes,
        "exported_claim_count": phase.claims.len(),
        "policy_violations": phase.policy_violations,
        "claim_roots": claim_roots,
    })
}

pub fn run_strategy(seed: i64, strategy: &str) -> CoordinationResult<Value> {
    if !STRATEGIES.contains(&strategy) {
        return Err(CoordinationError::value_error(format!("unknown strategy: {strategy}")));
    }
    let runtime = build_runtime(seed, strategy)?;
    let fixture = &runtime.fixture;
    let router = Router::new(runtime.registry.clone(), runtime.failure.clone());
    let coordinator = TesseractCoordinator::new(
        runtime.registry.clone(),
        router,
        ClaimNormalizer,
        RuleBasedSynthesizer::new(required_slots()),
        LineageAnalyzer,
        runtime.traces.clone(),
    );
    let mut manifest = ExperimentManifest {
        run_id: fixture.run_id.clone(),
        seed,
        started_at: runtime.clock.now(),
        fixture_version: FIXTURE_VERSION.to_string(),
        strategy: strategy.to_string(),
        budget: fixture.query.budget,
        interventions: Vec::new(),
        code_version: None,
    };

    let usual_route = strs(&["node-a", "node-b", "node-c"]);
    let before = coordinator.execute(&fixture.query, "before_failure", Some(&usual_route))?;
    runtime.failure.apply(
        InterventionTarget::LineageRoot,
        "R2",
        "controlled removal of shared critical support",
        Some("invalidate node-b and correlated node-c evidence"),
    )?;
    let after_failure = coordinator.execute(&fixture.query, "after_failure", Some(&usual_route))?;

    let mut repair: Option<RepairDecision> = None;
    let mut after_repair = after_failure.clone();
    let mut additional_query_cost = 0;
    if strategy == "replica_count" {
        // Replica-count logic sees C and B as two copies and retries both without checking root independence.
        // Both still depend on failed R2.
        let planner = RepairPlanner::new(runtime.registry.clone(), runtime.traces.clone());
        let decision = planner.plan(&fixture.query, &strs(&["right"]), &strs(&["node-c", "node-b"]), &[], &[])?;
        after_repair = coordinator.execute(
            &fixture.query,
            "after_replica_repair",
            Some(&strs(&["node-a", "node-c", "node-b"])),
        )?;
        additional_query_cost = decision.steps;
        repair = Some(decision);
    } else if strategy == "lineage_targeted_repair" {
        let planner = RepairPlanner::new(runtime.registry.clone(), runtime.traces.clone());
        let decision = planner.plan(
            &fixture.query,
            &strs(&["right"]),
            &strs(&["node-c", "node-d"]),
            &strs(&["R1", "R2"]),
            &strs(&["FD1", "FD2"]),
        )?;
        let preferred: Vec<Option<String>> = if py_truthy_str(&decision.selected_node_id) {
            vec![Some("node-a".into()), Some("node-c".into()), decision.selected_node_id.clone()]
        } else {
            vec![Some("node-a".into()), Some("node-b".into()), Some("node-c".into())]
        };
        // `tuple(node for node in preferred if node)`
        let preferred: Vec<String> = preferred.into_iter().flatten().filter(|n| !n.is_empty()).collect();
        after_repair = coordinator.execute(&fixture.query, "after_independent_repair", Some(&preferred))?;
        additional_query_cost = decision.steps;
        repair = Some(decision);
    }

    manifest.interventions = runtime.failure.records();
    let phases: [(&str, &QueryExecution); 3] =
        [("before_failure", &before), ("after_failure", &after_failure), ("after_repair", &after_repair)];
    let mut actual_executions: Vec<&QueryExecution> = vec![&before, &after_failure];
    if strategy != "lineage_without_repair" {
        actual_executions.push(&after_repair);
    }
    let mut privacy = Map::new();
    for (k, v) in assert_fixture_privacy(fixture) {
        privacy.insert(k, json!(v));
    }
    privacy.insert("no_policy_violations".into(), json!(phases.iter().all(|(_, p)| p.policy_violations == 0)));
    // Python checked `claim.__class__.__name__ == "ClaimEnvelope"`; the type system guarantees it here.
    privacy.insert("only_claim_contracts_exported".into(), json!(true));

    let mut phases_json = Map::new();
    for (name, phase) in phases {
        phases_json.insert(name.to_string(), phase_summary(phase));
    }
    let observed_failure_threshold =
        if before.synthesis.success && !after_failure.synthesis.success { json!(1) } else { Value::Null };
    Ok(json!({
        "manifest": manifest.to_jsonable(),
        "fixture": {
            "query": fixture.query.to_jsonable(),
            "expected_answer": fixture.expected_answer,
            "node_ids": fixture.records_by_node.keys().cloned().collect::<Vec<_>>(),
            "structural_expectations": {
                "node-a": {"slot": "left", "root": "R1", "failure_domain": "FD1"},
                "node-b": {"slot": "right", "root": "R2", "failure_domain": "FD2"},
                "node-c": {"slot": "right", "root": "R2", "failure_domain": "FD2"},
                "node-d": {"slot": "right", "root": "R3", "failure_domain": "FD3"},
            },
        },
        "phases": phases_json,
        "repair": repair.to_jsonable(),
        "evaluation": {
            "predicted_failure_domain_threshold": before.metrics.minimal_failure_domain_cut,
            "observed_failure_threshold": observed_failure_threshold,
            "repair_steps": repair.as_ref().map_or(0, |r| r.steps),
        },
        "cost": {
            "phase_query_count": actual_executions.len(),
            "verification_steps": additional_query_cost,
            "total_exported_bytes": actual_executions.iter().map(|p| p.exported_bytes).sum::<usize>(),
            "total_exported_claims": actual_executions.iter().map(|p| p.claims.len()).sum::<usize>(),
        },
        "privacy_assertions": privacy,
        "trace_events": runtime.traces.events().to_jsonable(),
    }))
}

pub fn run_experiment(seed: i64) -> CoordinationResult<Value> {
    let mut results = Map::new();
    for strategy in STRATEGIES {
        results.insert(strategy.to_string(), run_strategy(seed, strategy)?);
    }
    let targeted = &results["lineage_targeted_repair"];
    let mut summary = Map::new();
    for (strategy, result) in &results {
        summary.insert(
            strategy.clone(),
            json!({
                "before_failure": result["phases"]["before_failure"]["synthesis"]["success"],
                "after_failure": result["phases"]["after_failure"]["synthesis"]["success"],
                "after_repair": result["phases"]["after_repair"]["synthesis"]["success"],
                "verification_steps": result["cost"]["verification_steps"],
            }),
        );
    }
    Ok(json!({
        "schema_version": "1.0",
        "seed": seed,
        "milestone": {
            "before_failure": targeted["phases"]["before_failure"],
            "after_failure": targeted["phases"]["after_failure"],
            "after_repair": targeted["phases"]["after_repair"],
        },
        "strategy_summary": summary,
        "strategies": results,
    }))
}
