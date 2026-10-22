//! Gate 4 (scale) and Gate 5 (generalization) claims, pinned (port of `NeuralGraph/tests/test_scale.py` at
//! `13a1729`, same test names and assertions).
//!
//! The load-bearing assertions are the negative and the self-critical ones: if correlated replication ever
//! survives a lineage-root failure the central claim is wrong, and if the conservatism cost of lineage-aware
//! repair silently disappears the paper would be overclaiming.

use neuralgraph_research::coordination::benchmark::DEFAULT_SEED;
use neuralgraph_research::coordination::scale::{
    build_placement, default_specs, route_preference, run_point, run_scale_sweep, CapabilitySpec,
};
use serde_json::Value;
use std::collections::BTreeSet;

const ARITIES: [i64; 4] = [2, 3, 5, 8];
const HOLDERS: [i64; 2] = [2, 3];

fn point(k: i64, h: i64, i: i64, policy: &str, intervention: &str) -> Value {
    run_point(&CapabilitySpec::new(k, h, i).unwrap(), policy, intervention, DEFAULT_SEED).unwrap()
}

fn sweep() -> Value {
    run_scale_sweep(DEFAULT_SEED, &default_specs()).unwrap()
}

// GeneralizationTests: the mechanism is not specific to reconstructing a pair.

#[test]
fn test_reconstruction_succeeds_for_every_capability_arity() {
    for k in ARITIES {
        for h in HOLDERS {
            let point = point(k, h, 2, "lineage_aware", "node_failure");
            assert_eq!(point["baseline_correct"], true, "a {k}-premise capability failed to reconstruct at all");
        }
    }
}

#[test]
fn test_answer_requires_every_premise() {
    // An 8-premise answer must compose 8 distinct values, not repeat one.
    let placement = build_placement(&CapabilitySpec::new(8, 2, 2).unwrap(), DEFAULT_SEED, "lineage_aware");
    let values: BTreeSet<&String> = placement.records_by_node.values().flatten().map(|r| &r.value).collect();
    assert_eq!(values.len(), 8);
}

#[test]
fn test_bounded_route_covers_every_premise_at_any_arity() {
    // Interleaved preference is what makes the bounded route viable at scale.
    for k in ARITIES {
        let spec = CapabilitySpec::new(k, 3, 2).unwrap();
        let preference = route_preference(&spec);
        let placement = build_placement(&spec, DEFAULT_SEED, "lineage_aware");
        let reachable = &preference[..placement.max_nodes as usize];
        let covered: BTreeSet<String> =
            reachable.iter().flat_map(|node| placement.records_by_node[node].iter().map(|r| r.slot.clone())).collect();
        assert_eq!(covered, spec.slot_names().into_iter().collect::<BTreeSet<_>>(), "K={k}");
    }
}

// ReplicaCountBuysNothingAtAnyScaleTests: the central negative claim, swept across K and H.

#[test]
fn test_correlated_replicas_never_survive_a_lineage_root_failure() {
    for k in ARITIES {
        for h in HOLDERS {
            for policy in ["source_count", "lineage_aware"] {
                let point = point(k, h, 1, policy, "lineage_root_failure");
                assert_eq!(point["baseline_correct"], true);
                assert_eq!(
                    point["capability_survival"], false,
                    "correlated replication survived at K={k} H={h}; the paper's central claim would be wrong"
                );
            }
        }
    }
}

#[test]
fn test_adding_holders_does_not_help_when_they_share_a_root() {
    // H=3 is no better than H=2 while I stays 1.
    for k in ARITIES {
        let two = point(k, 2, 1, "lineage_aware", "lineage_root_failure");
        let three = point(k, 3, 1, "lineage_aware", "lineage_root_failure");
        assert_eq!(two["capability_survival"], false);
        assert_eq!(three["capability_survival"], false);
        assert!(three["record_count"].as_i64() > two["record_count"].as_i64());
    }
}

#[test]
fn test_independent_roots_restore_survival_at_every_scale() {
    for k in ARITIES {
        for h in HOLDERS {
            assert_eq!(
                point(k, h, 2, "lineage_aware", "lineage_root_failure")["capability_survival"],
                true,
                "K={k} H={h}"
            );
        }
    }
}

// RepairEfficiencyTests

#[test]
fn test_lineage_awareness_finds_independent_support_in_fewer_verifications() {
    // With survival equal, the policies differ in what the search costs.
    let result = sweep();
    let lineage = &result["survival_by_independence"]["lineage_aware::lineage_root_failure::I2"];
    let replicas = &result["survival_by_independence"]["source_count::lineage_root_failure::I2"];
    assert_eq!(lineage["survival_rate"], 1.0);
    assert_eq!(replicas["survival_rate"], 1.0);
    assert!(lineage["mean_recovery_steps"].as_f64() < replicas["mean_recovery_steps"].as_f64());
}

// ConservatismCostTests: the honest tradeoff — lineage-aware repair is not free, and not always best.

#[test]
fn test_replica_count_beats_lineage_awareness_under_plain_node_failure() {
    for k in ARITIES {
        for h in HOLDERS {
            let replicas = point(k, h, 1, "source_count", "node_failure");
            let lineage = point(k, h, 1, "lineage_aware", "node_failure");
            assert_eq!(
                replicas["capability_survival"], true,
                "a surviving correlated replica should serve a node failure"
            );
            assert_eq!(
                lineage["capability_survival"], false,
                "lineage-aware repair is expected to be over-conservative here; if it now survives, the documented \
                 tradeoff has changed"
            );
        }
    }
}

#[test]
fn test_the_cost_disappears_once_independent_support_exists() {
    // The conservatism only bites when there is nothing independent to find.
    for k in ARITIES {
        assert_eq!(point(k, 2, 2, "lineage_aware", "node_failure")["capability_survival"], true, "K={k}");
    }
}

// SweepDeterminismTests

#[test]
fn test_sweep_is_deterministic() {
    assert_eq!(sweep()["points"], sweep()["points"]);
}

#[test]
fn test_survival_is_invariant_across_scale_in_every_cell() {
    // Whatever the verdict is, it must not depend on K or H.
    let result = sweep();
    for (key, row) in result["survival_by_independence"].as_object().unwrap() {
        assert_eq!(row["invariant_across_scale"], true, "{key} changed verdict across capability shapes");
    }
}

#[test]
fn test_cost_grows_with_arity() {
    let result = sweep();
    let costs: Vec<f64> = ARITIES
        .iter()
        .map(|k| result["cost_by_arity"][format!("K{k}")]["mean_bytes_moved"].as_f64().unwrap())
        .collect();
    let mut sorted = costs.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    assert_eq!(costs, sorted, "bytes moved should rise with arity");
    for k in ARITIES {
        assert_eq!(result["cost_by_arity"][format!("K{k}")]["all_baselines_correct"], true, "K={k}");
    }
}

#[test]
fn capability_spec_rejects_what_python_rejected() {
    // `CapabilitySpec.__post_init__`
    for (k, h, i, message) in [
        (1, 2, 1, "a reconstruction capability needs at least two premises"),
        (2, 0, 1, "each premise needs at least one holder"),
        (2, 2, 3, "independent_roots must be between 1 and holders_per_slot"),
        (2, 2, 0, "independent_roots must be between 1 and holders_per_slot"),
    ] {
        let error = CapabilitySpec::new(k, h, i).unwrap_err();
        assert_eq!((error.kind.as_str(), error.message.as_str()), ("ValueError", message));
    }
}
