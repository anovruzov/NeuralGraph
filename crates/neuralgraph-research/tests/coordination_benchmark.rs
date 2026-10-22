//! Tests for the capability-survival benchmark (port of `NeuralGraph/tests/test_benchmark.py` at `13a1729`, same
//! test names and assertions).
//!
//! These lock the claims the paper rests on. Several assert *failure* (a strategy losing the capability): the
//! claim is that replica count does not buy survival, so a replica-count strategy that suddenly survived would
//! invalidate the argument just as surely as a lineage-aware one that stopped.

use neuralgraph_research::coordination::benchmark::{
    build_runtime, generate_symbols, observed_lineage, order_repair_candidates, run_benchmark, run_cell, run_sweep,
    structural_diversity, ObservedLineage, DEFAULT_SEED, INTERVENTIONS, STRATEGIES,
};
use neuralgraph_research::coordination::contracts::strs;
use serde_json::Value;
use std::collections::BTreeSet;

fn cell(strategy: &str, intervention: &str) -> Value {
    cell_at(strategy, intervention, DEFAULT_SEED)
}

fn cell_at(strategy: &str, intervention: &str, seed: i64) -> Value {
    run_cell(seed, strategy, intervention, None, None).unwrap()
}

fn rate(result: &Value, strategy: &str, key: &str) -> f64 {
    result["per_strategy"][strategy][key].as_f64().unwrap()
}

fn i(v: &Value) -> i64 {
    v.as_i64().unwrap()
}

// MatrixShapeTests

#[test]
fn test_matrix_is_eight_by_nine() {
    assert_eq!(STRATEGIES.len(), 8);
    assert_eq!(INTERVENTIONS.len(), 9);
}

#[test]
fn test_every_cell_runs_and_reports_required_metrics() {
    let result = run_benchmark(DEFAULT_SEED).unwrap();
    let required = [
        "capability_survival",
        "silent_forgetting",
        "unrecovered_silent_forgetting",
        "recovery_steps",
        "bytes_moved",
        "independent_support_before",
        "structural_diversity",
        "privacy",
        "redundancy_efficiency",
    ];
    for strategy in STRATEGIES {
        for intervention in INTERVENTIONS {
            let cell = &result["cells"][format!("{strategy}::{intervention}")];
            for key in required {
                assert!(cell.get(key).is_some(), "{strategy}::{intervention} misses {key}");
            }
        }
    }
}

#[test]
fn test_every_coordinated_strategy_reconstructs_before_intervention() {
    // A strategy that cannot do the task at all cannot lose it informatively.
    for strategy in STRATEGIES {
        if strategy == "isolated_local" {
            continue;
        }
        let cell = cell(strategy, "node_failure");
        assert_eq!(cell["baseline_correct"], true, "{strategy} failed to reconstruct before any intervention");
    }
}

#[test]
fn test_isolated_local_never_reconstructs() {
    // The floor: one node holding one premise cannot produce the pair.
    let cell = cell("isolated_local", "node_failure");
    assert_eq!(cell["baseline_correct"], false);
    assert_eq!(cell["capability_survival"], false);
}

// DeterminismTests

#[test]
fn test_same_seed_produces_identical_json() {
    let first = run_benchmark(DEFAULT_SEED).unwrap();
    let second = run_benchmark(DEFAULT_SEED).unwrap();
    assert_eq!(
        neuralgraph_research::coordination::core::artifact_json(&first),
        neuralgraph_research::coordination::core::artifact_json(&second)
    );
}

#[test]
fn test_different_seeds_change_symbols_but_not_the_verdicts() {
    // Survival is structural: it must not depend on which opaque symbols were drawn.
    let base = cell_at("lineage_aware_repair", "lineage_root_failure", DEFAULT_SEED);
    let other = cell_at("lineage_aware_repair", "lineage_root_failure", DEFAULT_SEED + 977);
    assert_ne!(generate_symbols(DEFAULT_SEED).left, generate_symbols(DEFAULT_SEED + 977).left);
    assert_eq!(base["capability_survival"], other["capability_survival"]);
}

#[test]
fn test_sweep_flags_the_stochastic_strategy_and_only_that_one() {
    let result = run_sweep(DEFAULT_SEED, 12).unwrap();
    assert_eq!(result["per_strategy"]["random_path_diversification"]["fully_deterministic"], false);
    for strategy in STRATEGIES {
        if strategy == "random_path_diversification" {
            continue;
        }
        assert_eq!(
            result["per_strategy"][strategy]["fully_deterministic"], true,
            "{strategy} varied across seeds but should be deterministic"
        );
    }
}

// ReplicaCountIsNotSurvivalTests: the central negative claim, asserted from three directions.

#[test]
fn test_three_replicas_do_not_survive_their_shared_root_failing() {
    let cell = cell("fixed_distributed_replication", "lineage_root_failure");
    assert_eq!(cell["baseline_correct"], true);
    assert_eq!(cell["capability_survival"], false);
}

#[test]
fn test_full_replication_does_not_survive_a_root_failure_either() {
    // Eight records, twice the storage of any other strategy, and it still dies.
    let cell = cell("full_replication", "lineage_root_failure");
    assert_eq!(cell["record_count"], 8);
    assert_eq!(cell["baseline_correct"], true);
    assert_eq!(cell["capability_survival"], false);
}

#[test]
fn test_source_count_repair_spends_its_budget_on_correlated_nodes() {
    let cell = cell("source_count_repair", "lineage_root_failure");
    assert_eq!(cell["capability_survival"], false);
    assert!(i(&cell["recovery_steps"]) > 0, "it did attempt a repair");
    assert!(cell["repair_selected_node"].is_null(), "but found no valid support");
}

#[test]
fn test_lineage_aware_repair_survives_the_same_failure_at_lower_storage() {
    let lineage = cell("lineage_aware_repair", "lineage_root_failure");
    let replication = cell("full_replication", "lineage_root_failure");
    assert_eq!(lineage["capability_survival"], true);
    assert_eq!(replication["capability_survival"], false);
    assert!(i(&lineage["record_count"]) < i(&replication["record_count"]));
}

// MinCutPredictsSurvivalTests: min-cut is validated as predictive, not assumed.

fn structural_min_cut(strategy: &str) -> i64 {
    let runtime = build_runtime(DEFAULT_SEED, strategy, None).unwrap();
    i(&structural_diversity(&runtime.placement)["structural_min_cut"])
}

#[test]
fn test_only_the_oracle_placement_has_a_min_cut_above_one() {
    assert_eq!(structural_min_cut("oracle_min_cut"), 2);
    for strategy in STRATEGIES {
        if strategy == "oracle_min_cut" {
            continue;
        }
        assert!(structural_min_cut(strategy) <= 1, "{strategy}");
    }
}

#[test]
fn test_worst_single_domain_failure_is_survived_only_by_min_cut_two() {
    for strategy in STRATEGIES {
        let cut = structural_min_cut(strategy);
        let cell = cell(strategy, "worst_single_domain_failure");
        assert_eq!(
            cell["capability_survival"],
            cut >= 2,
            "{strategy} (min-cut {cut}) survival did not match its min-cut prediction"
        );
    }
}

#[test]
fn test_the_intervention_is_not_tautological() {
    // It fails one domain, never the whole cut -- otherwise nothing could survive.
    let cell = cell("oracle_min_cut", "worst_single_domain_failure");
    let applications = cell["intervention_plan"]["applications"].as_array().unwrap();
    assert!(!applications.is_empty(), "the intervention must actually do something");

    let struck_roots: BTreeSet<&str> = applications.iter().map(|a| a["target_id"].as_str().unwrap()).collect();
    let runtime = build_runtime(DEFAULT_SEED, "oracle_min_cut", None).unwrap();
    let struck_domains: BTreeSet<&String> = runtime
        .placement
        .records_by_node
        .values()
        .flatten()
        .filter(|r| r.lineage_root_ids.iter().any(|root| struck_roots.contains(root.as_str())))
        .flat_map(|r| r.failure_domains.iter())
        .collect();

    // Exactly one domain, and strictly fewer than the min-cut.
    assert_eq!(struck_domains.len(), 1);
    let cut_size = i(&structural_diversity(&runtime.placement)["structural_min_cut"]);
    assert!(
        (struck_domains.len() as i64) < cut_size,
        "the intervention struck a full cut, making the result tautological"
    );
}

// RepairPolicyBoundaryTests: repair policies may use exported contracts only, never node-private records.

#[test]
fn test_observed_lineage_comes_only_from_exported_claims() {
    let runtime = build_runtime(DEFAULT_SEED, "lineage_aware_repair", None).unwrap();
    let baseline = runtime
        .coordinator
        .execute(&runtime.query, "before_intervention", Some(&runtime.placement.node_ids()))
        .unwrap();
    let lineage = observed_lineage(&baseline);
    // node-d is never contacted under the bounded route, so a policy that reads only exported claims cannot
    // know it exists. If this ever includes node-d, the route bound has silently changed.
    assert!(!lineage.contains_key("node-d"));
    let contacted: BTreeSet<&String> = baseline.route.iter().collect();
    assert!(lineage.keys().all(|k| contacted.contains(k)));
}

fn lineage_of(entries: &[(&str, &str, &str)]) -> ObservedLineage {
    entries
        .iter()
        .map(|(node, root, domain)| {
            (node.to_string(), (BTreeSet::from([root.to_string()]), BTreeSet::from([domain.to_string()])))
        })
        .collect()
}

#[test]
fn test_lineage_aware_ordering_demotes_correlated_candidates() {
    let lineage = lineage_of(&[("node-b", "R2", "FD2"), ("node-c", "R2", "FD2"), ("node-d", "R3", "FD3")]);
    let ordered = order_repair_candidates(
        "lineage_aware",
        &strs(&["node-b", "node-c", "node-d"]),
        &lineage,
        &strs(&["R2"]),
        &strs(&["FD2"]),
        1,
        &[],
        None,
    );
    assert_eq!(ordered[0], "node-d");
}

#[test]
fn test_source_count_ordering_promotes_correlated_candidates() {
    // The intuition under test: "more copies means safer" ranks replicas first.
    let lineage = lineage_of(&[("node-b", "R2", "FD2"), ("node-c", "R2", "FD2"), ("node-d", "R3", "FD3")]);
    let ordered = order_repair_candidates(
        "source_count",
        &strs(&["node-b", "node-c", "node-d"]),
        &lineage,
        &[],
        &[],
        1,
        &[],
        None,
    );
    assert_eq!(ordered.last().unwrap(), "node-d");
}

#[test]
fn test_ordering_is_stable_for_deterministic_policies() {
    let lineage = lineage_of(&[("n1", "R1", "FD1"), ("n2", "R1", "FD1"), ("n3", "R1", "FD1")]);
    let first = order_repair_candidates("lineage_aware", &strs(&["n3", "n1", "n2"]), &lineage, &[], &[], 5, &[], None);
    let second = order_repair_candidates("lineage_aware", &strs(&["n1", "n2", "n3"]), &lineage, &[], &[], 9, &[], None);
    assert_eq!(first, second);
}

// PrivacyTests

#[test]
fn test_centralized_holds_the_full_answer_and_is_flagged() {
    let cell = cell("centralized", "node_failure");
    assert_eq!(cell["privacy"]["single_node_holds_full_answer"], true);
}

#[test]
fn test_distributed_strategies_never_let_one_node_hold_the_answer() {
    for strategy in [
        "fixed_distributed_replication",
        "source_count_repair",
        "random_path_diversification",
        "lineage_aware_repair",
        "oracle_min_cut",
    ] {
        let cell = cell(strategy, "node_failure");
        assert_eq!(cell["privacy"]["single_node_holds_full_answer"], false, "{strategy}");
    }
}

#[test]
fn test_no_strategy_leaks_the_answer_into_the_query_or_a_single_memory() {
    for strategy in STRATEGIES {
        let cell = cell(strategy, "node_failure");
        assert_eq!(cell["privacy"]["answer_present_in_query"], false, "{strategy}");
        assert_eq!(cell["privacy"]["complete_answer_in_a_single_memory"], false, "{strategy}");
    }
}

#[test]
fn test_denied_responses_never_carry_payload() {
    // Interventions produce denials; none of them may carry structural identifiers.
    for intervention in INTERVENTIONS {
        let cell = cell("lineage_aware_repair", intervention);
        assert_eq!(cell["privacy"]["payload_bearing_denials"], 0, "{intervention}");
        assert_eq!(cell["privacy"]["policy_violations"], 0, "{intervention}");
    }
}

// SilentForgettingTests

#[test]
fn test_a_system_that_never_had_the_capability_has_not_forgotten_it() {
    let cell = cell("isolated_local", "node_failure");
    assert_eq!(cell["baseline_correct"], false);
    assert_eq!(cell["silent_forgetting"], false);
}

#[test]
fn test_centralized_failure_is_silent_and_unrecovered() {
    let cell = cell("centralized", "lineage_root_failure");
    assert_eq!(cell["baseline_correct"], true);
    assert_eq!(cell["capability_survival"], false);
    assert_eq!(cell["unrecovered_silent_forgetting"], true);
}

#[test]
fn test_confidence_exceeds_coverage_when_the_capability_is_lost() {
    // The signature of collective forgetting: high confidence, missing support.
    let cell = cell("fixed_distributed_replication", "lineage_root_failure");
    assert_eq!(cell["capability_survival"], false);
    assert!(
        cell["post_intervention_confidence"].as_f64().unwrap() > cell["post_intervention_coverage"].as_f64().unwrap()
    );
    assert!(cell["confidence_valid_support_gap"].as_f64().unwrap() > 0.0);
}

#[test]
fn test_transient_and_unrecovered_forgetting_are_distinguished() {
    let repaired = cell("lineage_aware_repair", "lineage_root_failure");
    assert_eq!(repaired["capability_survival"], true);
    assert_eq!(repaired["unrecovered_silent_forgetting"], false);
}

// OrderingOfResultsTests

#[test]
fn test_survival_is_monotone_in_lineage_sophistication() {
    let result = run_sweep(DEFAULT_SEED, 6).unwrap();
    let r = |s: &str| rate(&result, s, "capability_survival_rate");
    assert!(r("isolated_local") < r("centralized"));
    assert!(r("centralized") < r("fixed_distributed_replication"));
    assert!(r("fixed_distributed_replication") <= r("random_path_diversification"));
    assert!(r("random_path_diversification") < r("lineage_aware_repair"));
    assert!(r("lineage_aware_repair") < r("oracle_min_cut"));
}

#[test]
fn test_lineage_awareness_beats_full_replication_on_both_axes() {
    // Higher survival at lower storage is the Pareto claim, asserted directly.
    let result = run_sweep(DEFAULT_SEED, 6).unwrap();
    assert!(
        rate(&result, "lineage_aware_repair", "capability_survival_rate")
            > rate(&result, "full_replication", "capability_survival_rate")
    );
    assert!(
        rate(&result, "lineage_aware_repair", "mean_bytes_moved")
            < rate(&result, "full_replication", "mean_bytes_moved")
    );
}
